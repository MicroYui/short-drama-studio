"""Actual QA returns usable observed plane inputs without losing its conclusion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import ValidationError
import pytest

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.asset_inspection import (
    AssetInspectionReference, AssetInspectionReferenceSource,
    AssetVersionInspectionRequest, SemanticAssetReview, inspect_asset_image,
)
from short_drama.image_models import AssetImportLocalRequest
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ApprovalStatus, AssetKind, AssetTextPlate, Freshness,
    ImageReferenceAuthority, ImageReferenceRole,
)
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work


def _quad(*points: tuple[float, float]) -> list[dict[str, float]]:
    return [{"x": x, "y": y} for x, y in points]


class PlaneLanguageClient(CapturingLanguageClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.source_id = ""
        self.fault: str | None = None
        self.suggest = True

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        super().generate_text(instructions=instructions, input_text=input_text,
                              image_urls=image_urls, max_output_tokens=max_output_tokens)
        self.calls += 1
        result: dict[str, Any] = {"recommended": False, "score": 2.0,
            "issues": [{"code": "PROP_LAYOUT_ORIENTATION_MISMATCH", "severity": "major",
                        "message": "标题被竖排重写，完整纸面未同转。", "repair_layer": "ASSET_EDIT"}],
            "summary": "已有文字不可重绘；可见平面可用准确源像素贴合。建议未执行。"}
        if self.suggest:
            layer: dict[str, Any] = {"source_asset_version_id": self.source_id,
                "source_quad": _quad((.1, .1), (.9, .1), (.9, .9), (.1, .9)),
                # Same winding, but the original top-left becomes target top-right.
                "target_quad": _quad((.8, .2), (.8, .8), (.2, .8), (.2, .2)),
                "blend_mode": "replace"}
            if self.fault == "UNSEEN":
                layer["source_asset_version_id"] = "unseen-version"
            elif self.fault == "WHOLE":
                layer["source_quad"] = None
            elif self.fault == "NORMALIZED":
                layer["target_quad"] = _quad((.2, .2), (.4, .2), (.6, .2), (.8, .2))
            elif self.fault == "RASTER":
                layer["target_quad"] = _quad((.2, .2), (.6, .2), (.6, .20001), (.2, .20001))
            elif self.fault == "SOURCE_RASTER":
                layer["source_quad"] = _quad((.2, .2), (.6, .2), (.6, .20001), (.2, .20001))
            result["plane_composite_suggestion"] = {
                "base_asset_version_id": "wrong-base-must-not-survive", "layers": [layer],
                "protected_regions": [{"polygon": _quad((.25, .25), (.35, .25), (.3, .35)),
                    "label": "已看见的前景手指"}],
                "descriptor": "整页转90度，保留真实前景；当前建议没有执行。"}
            if self.fault == "PROTECTION_RASTER":
                result["plane_composite_suggestion"]["protected_regions"][0]["polygon"] = _quad(
                    (.2, .2), (.6, .20001), (.8, .2))
        return json.dumps(result, ensure_ascii=False)


def _setup(tmp_path: Path, *, rotated: bool = False):
    language = PlaneLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    asset = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    path = tmp_path / ("candidate.jpg" if rotated else "candidate.png")
    with Image.new("RGB", (900, 1600), "#998877") as image:
        if rotated:
            exif = image.getexif(); exif[274] = 6
            image.save(path, exif=exif)
        else:
            image.save(path)
    version = service.import_local_asset_version(document.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="已保存的摄影基图")).version
    source_path = tmp_path / "source.png"
    with Image.new("RGB", (360, 240), "#e0e0e0") as source:
        ImageDraw.Draw(source).rectangle((36, 24, 324, 216), fill="white")
        ImageDraw.Draw(source).text((50, 40), "EXACT TEXT", fill="black")
        source.save(source_path)
    source = service.import_local_asset_version(document.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(source_path), descriptor="可见完整平面，外围灰背景")).version
    language.source_id = source.id
    return service, language, document.project.id, asset.id, version, source


@pytest.mark.parametrize("suggest", [True, False])
def test_actual_plane_qa_survives_worker_read_and_restart(tmp_path: Path, suggest: bool) -> None:
    service, language, pid, aid, version, source = _setup(tmp_path)
    language.suggest = suggest
    before = service.get_project(pid).document.model_dump(mode="json")
    arguments = {"project_id": pid, "asset_id": aid, "version_id": version.id,
                 "request": {"canonical_reference_version_ids": [source.id]}}
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    suggestion = result.output["plane_composite_suggestion"]
    if suggest:
        request = AssetImagePlaneCompositeRequest.model_validate(suggestion)
        assert request.base_asset_version_id == version.id
        assert request.layers[0].source_asset_version_id == source.id
        assert request.layers[0].target_quad[0].x == .8  # Keep 90-degree corner identity.
        assert request.protected_regions[0].label == "已看见的前景手指"
    else:
        assert suggestion is None
    assert result.output["recommended"] is False and result.output["score"] == 2.0
    assert language.calls == 1 and len(language.image_urls) == 2
    payload = json.loads(language.input_text)
    assert payload["candidate_creation"]["plane_composite_tool"] == "image.composite_plane"
    assert payload["candidate_creation"]["local_plane_composite_available"] is True
    assert payload["candidate_creation"]["local_edit_guide_available"] is False  # Local plane has no paid-image size restriction.
    source_info = payload["image_map"][1]["local_plane_composite_source"]
    assert source_info == {"available": True, "actual_size": {"width": 360, "height": 240}, "full_image_is_content_plate": False}
    assert "不能两边各按屏幕左上排序" in language.instructions
    assert "`multiply` 只用于已确认的空白底面" in language.instructions
    assert "物理纸角被手挡住不单独构成不可合成理由" in language.instructions
    assert "只改所选quad，不猜遮挡物理角" in language.instructions
    assert service.get_project(pid).document.model_dump(mode="json") == before
    store, work, context = runtime_work(tmp_path / "worker.sqlite3")
    context.project_id = pid
    work.specialist_turns = [SpecialistTurnRecord(id="actual-plane-qa", sequence=1,
        tool_name="qa.inspect_asset_version", arguments=arguments, reason="Read actual plane evidence.",
        status=SpecialistTurnStatus.COMPLETED, result=result.model_dump(mode="json")),
        SpecialistTurnRecord(id="later-read", sequence=2, tool_name="artifact.read_current",
            arguments={"project_id": pid}, reason="Read current text.", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"status": "available"}})]
    store.save_work_order(work)
    reopened = AgentRuntimeStore(store.database_path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue from saved evidence.",
                                       "arguments": {"project_id": pid}})])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Continue the selected task."))
    working: Any = client.payloads[0]["working_set"]
    state = next(entity["state"] for entity in working["entities"] if entity["kind"] == "asset_review")
    assert state.get("plane_composite_suggestion") == suggestion
    assert state["recommended"] is False


@pytest.mark.parametrize("fault", ["UNSEEN", "WHOLE", "NORMALIZED", "RASTER", "SOURCE_RASTER", "PROTECTION_RASTER"])
def test_bad_plane_advice_preserves_actual_qa(tmp_path: Path, fault: str) -> None:
    service, language, pid, aid, version, source = _setup(tmp_path)
    language.fault = fault
    result = service.tool_registry.invoke("qa.inspect_asset_version", {"project_id": pid,
        "asset_id": aid, "version_id": version.id, "request": {"canonical_reference_version_ids": [source.id]}})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert result.output["plane_composite_suggestion"] is None
    assert result.output["recommended"] is False and result.output["score"] == 2.0
    assert result.output["issues"][0]["code"] == "PROP_LAYOUT_ORIENTATION_MISMATCH"
    assert language.calls == 1


def test_observed_plane_request_executes_via_actual_local_tool(tmp_path: Path) -> None:
    service, language, pid, aid, version, source = _setup(tmp_path)
    before = service.get_project(pid).document
    owner_before = next(item for item in before.assets if item.id == aid)
    qa = service.tool_registry.invoke("qa.inspect_asset_version", {"project_id": pid,
        "asset_id": aid, "version_id": version.id, "request": {"canonical_reference_version_ids": [source.id]}})
    assert qa.output is not None
    result = service.tool_registry.invoke("image.composite_plane", {"project_id": pid,
        "request": qa.output["plane_composite_suggestion"]})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    composed = result.output["version"]
    assert composed["parent_version_id"] == version.id
    assert composed["approval_status"] == "DRAFT"
    assert {ref["asset_version_id"] for ref in composed["derivation_references"]} == {version.id, source.id}
    with Image.open(composed["local_path"]) as image:
        assert image.size == (900, 1600)
    owner_after = next(item for item in service.get_project(pid).document.assets if item.id == aid)
    assert owner_after.current_version_id == owner_before.current_version_id
    assert owner_after.approved_version_id == owner_before.approved_version_id
    assert language.calls == 1  # The local composite adds no second image or language call.


@pytest.mark.parametrize("unavailable", ["REMOTE_BASE", "REMOTE_SOURCE", "REJECTED_BASE", "STALE_BASE", "REJECTED_SOURCE", "STALE_SOURCE", "CROSS_PROJECT", "NO_REFERENCE"])
def test_only_actually_seen_and_locally_usable_sources_can_be_suggested(tmp_path: Path, unavailable: str) -> None:
    service, language, pid, aid, version, source = _setup(tmp_path)
    document = service.get_project(pid).document
    asset = next(item for item in document.assets if item.id == aid)
    if unavailable == "REMOTE_BASE":
        version.external_url = "https://example.invalid/possibly-different-base.png"
    elif unavailable == "REMOTE_SOURCE":
        source.external_url = "https://example.invalid/possibly-different-source.png"
    elif unavailable == "REJECTED_BASE":
        version.approval_status = ApprovalStatus.REJECTED
    elif unavailable == "STALE_BASE":
        version.freshness = Freshness.STALE
    elif unavailable == "REJECTED_SOURCE":
        source.approval_status = ApprovalStatus.REJECTED
    elif unavailable == "STALE_SOURCE":
        source.freshness = Freshness.STALE
    refs = [] if unavailable == "NO_REFERENCE" else [AssetInspectionReference(
        asset=asset, version=source, project_id="another-project" if unavailable == "CROSS_PROJECT" else pid,
        reference_role=ImageReferenceRole.PROP_IDENTITY, reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
        purpose="本次实际看见的准确内容来源", subject_alias=None,
        source=AssetInspectionReferenceSource.DERIVATION_SOURCE, normative_for_declared_role=True)]
    result = inspect_asset_image(document=document, asset=asset, version=version, client=language,
        inspection_references=refs, request=AssetVersionInspectionRequest())
    assert result.plane_composite_suggestion is None
    assert result.recommended is False and result.score == 2.0
    assert language.calls == 1
    payload = json.loads(language.input_text)
    assert payload["candidate_creation"]["local_plane_composite_available"] is False
    assert len(language.image_urls) == (1 if unavailable == "NO_REFERENCE" else 2)


def test_exif_visible_dimensions_and_whole_declared_plate(tmp_path: Path) -> None:
    service, language, pid, aid, version, source = _setup(tmp_path, rotated=True)
    language.fault = "WHOLE"
    # Cached metadata cannot override the actual local source pixels used for QA.
    source.width = 999
    source.height = 999
    source.text_plate = AssetTextPlate(size="360x240", background_color="#fff", foreground_color="#000",
        exact_lines=["EXACT TEXT"], layout_description="整幅内容板，无摄影背景", font_family="local")
    document = service.get_project(pid).document
    asset = next(item for item in document.assets if item.id == aid)
    result = inspect_asset_image(document=document, asset=asset, version=version, client=language,
        canonical_references=[(asset, source)])
    assert result.plane_composite_suggestion is not None
    assert result.plane_composite_suggestion.layers[0].source_quad is None
    payload = json.loads(language.input_text)
    assert payload["candidate_creation"]["actual_size"] == {"width": 1600, "height": 900}
    assert payload["image_map"][1]["local_plane_composite_source"]["full_image_is_content_plate"] is True
    assert payload["image_map"][1]["local_plane_composite_source"]["actual_size"] == {"width": 360, "height": 240}
    assert language.calls == 1


def test_optional_plane_validation_does_not_relax_core_review() -> None:
    with pytest.raises(ValidationError, match="score"):
        SemanticAssetReview.model_validate({"recommended": False, "score": 6, "summary": "Still strict.",
            "plane_composite_suggestion": {"layers": []}})
    with pytest.raises(ValidationError, match="Invalid JSON"):
        SemanticAssetReview.model_validate_json('{"recommended":false,"plane_composite_suggestion":')
