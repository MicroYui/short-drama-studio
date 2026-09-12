"""QA requests carry executable advice, while the runtime retains exact evidence."""

import json
from pathlib import Path

import pytest

from short_drama.asset_inspection import AssetVersionInspectionRequest, inspect_asset_image
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, Freshness
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_edit_guide_suggestion import _setup
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_asset_plane_composite_suggestion import _setup as plane_setup
from tests.test_image_edit_results import _completed


def test_single_image_qa_omits_unavailable_plane_schema_but_keeps_observed_guide(tmp_path: Path) -> None:
    service, language, pid, aid, version = _setup(tmp_path)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": aid, "version_id": version.id,
        "request": {"review_focus": "本次只检查可见纸张方向和局部修复范围。",
                    "regions": [{"label": "纸张局部", "region": {"x": .2, "y": .3, "width": .6, "height": .6}}]},
    })
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    payload = json.loads(language.input_text)
    schema = payload["output_schema"]
    assert "\n" not in language.input_text  # JSON escaping preserves newlines inside content.
    assert schema["properties"]["plane_composite_suggestion"]["type"] == "null"
    assert "AssetImagePlaneCompositeRequest" not in schema["$defs"]
    assert "ImagePlanePaperInk" not in schema["$defs"]
    assert "source_asset_version_id" not in schema["$defs"]["AssetEditGuideSuggestion"]["properties"]
    assert "source" not in schema["$defs"]["AssetCropSuggestion"]["properties"]
    for field in ("review_focus", "candidate_output_layout", "region_evidence", "evidence_references"):
        assert field not in schema["properties"]
        assert field in result.output
    assert result.output["review_focus"] == payload["review_focus"]
    assert result.output["region_evidence"][0]["asset_version_id"] == version.id
    assert result.output["edit_guide_suggestion"]["source_asset_version_id"] == version.id
    assert result.output["edit_guide_suggestion"]["arrows"][0]["end"] == {"x": .5, "y": .8}
    assert result.output["recommended"] is False
    assert len(language.image_urls) == 2 and language.calls == 1


def test_remote_candidate_does_not_ask_for_local_repair_geometry(tmp_path: Path) -> None:
    service, language, pid, aid, version = _setup(tmp_path)
    document = service.get_project(pid).document
    asset = next(item for item in document.assets if item.id == aid)
    version.external_url = "https://example.invalid/exact-remote-candidate.png"
    result = inspect_asset_image(document=document, asset=asset, version=version,
                                 client=language, request=AssetVersionInspectionRequest())
    schema = json.loads(language.input_text)["output_schema"]
    for field in ("crop_suggestion", "edit_guide_suggestion", "plane_composite_suggestion"):
        assert schema["properties"][field]["type"] == "null"
    assert set(schema["$defs"]) == {"QaIssue", "QaFailureCategory"}
    assert result.recommended is False and result.edit_guide_suggestion is None
    assert language.image_urls == [version.external_url]


def test_observed_local_plane_still_exposes_complete_repair_constraints(tmp_path: Path) -> None:
    service, language, pid, aid, base, source = plane_setup(tmp_path)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": aid, "version_id": base.id,
        "request": {"canonical_reference_version_ids": [source.id]},
    })
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    schema = json.loads(language.input_text)["output_schema"]
    definitions = schema["$defs"]
    assert "AssetImagePlaneCompositeRequest" in definitions
    layer = definitions["ImagePlaneLayer"]["properties"]
    assert layer["target_quad"]["minItems"] == layer["target_quad"]["maxItems"] == 4
    assert layer["blend_mode"]["default"] == "replace"
    assert "paper_ink" in layer and "source_white_point" in layer
    assert "target_paper_cleanup" in definitions["AssetImagePlaneCompositeRequest"]["properties"]
    assert result.output["plane_composite_suggestion"]["base_asset_version_id"] == base.id
    assert result.output["plane_composite_suggestion"]["layers"][0]["source_asset_version_id"] == source.id
    assert language.calls == 1 and len(language.image_urls) == 2


@pytest.mark.parametrize("raw_available", [True, False])
def test_masked_review_identifies_registered_raw_without_claiming_to_observe_or_select_it(
    tmp_path: Path, raw_available: bool,
) -> None:
    fixture, provider, asset, base, _mask, task = _completed(tmp_path)
    document = fixture.get_project(asset.project_id).document
    target = next(item for item in document.assets if item.id == asset.id)
    raw = next(item for item in target.versions if item.id == task.provider_output_version_id)
    if not raw_available:
        raw.approval_status = ApprovalStatus.REJECTED
        raw.freshness = Freshness.STALE
        Path(raw.local_path or "").unlink()
        fixture.repository.save_project(document)
    language = CapturingLanguageClient()
    service = ShortDramaService(database_path=fixture.repository.database_path,
        workspace_root=fixture.workspace_root, language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    before = service.get_project(asset.project_id).document
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": asset.project_id, "asset_id": asset.id,
        "version_id": task.output_version_id, "request": {},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    payload = json.loads(language.input_text)
    candidate = payload["candidate_creation"]["masked_edit"]["provider_output_candidate"]
    assert candidate["asset_id"] == asset.id
    assert candidate["asset_version_id"] == raw.id
    assert candidate["version"] == raw.version
    assert candidate["approval_status"] == raw.approval_status.value
    assert candidate["freshness"] == raw.freshness.value
    assert candidate["local_file_available"] is raw_available
    assert len(language.image_urls) == 3  # Candidate, existing base, actual old mask; no raw image.
    assert {item["version_id"] for item in payload["image_map"]} == {task.output_version_id, base.id, _mask.id}
    assert payload["image_map"][-1]["role"] == "EDIT_MASK"
    assert result.output is not None and result.output["provider_output_review"] is None
    assert service.get_project(asset.project_id).document == before
    assert len(provider.calls) == 1
