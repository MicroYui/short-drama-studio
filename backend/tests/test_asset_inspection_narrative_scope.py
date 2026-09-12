"""Saved narrative selectors reach QA without promoting image-making preferences."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from short_drama.asset_inspection import AssetVersionInspectionRequest, SemanticAssetReview
from short_drama.image_models import ImageGenerationTask, ImageOperation, ImageProviderRequest, ImageTaskStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ApprovalStatus, Asset, AssetKind, AssetMediaType, AssetVersion, CoreAssetBrief,
    Freshness, OriginType, ProductionProfile, ScriptLine, ScriptVersion, ShotPlan, StructuredScene,
)
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient


FIXTURE: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/asset_inspection_narrative_scope_fresh4.json").read_text())


class ScopeLanguageClient(CapturingLanguageClient):
    """Replay the observed response, not an invented improved QA judgment."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        super().generate_text(instructions=instructions, input_text=input_text,
                              image_urls=image_urls, max_output_tokens=max_output_tokens)
        self.calls += 1
        return json.dumps(FIXTURE["original_review"], ensure_ascii=False)


def _fixture(tmp_path: Path) -> tuple[ShortDramaService, ScopeLanguageClient, dict[str, Any]]:
    language = ScopeLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    pid = document.project.id
    original = FIXTURE["original_qa_payload"]
    mapped = original["asset"]
    versions: list[AssetVersion] = []
    for index, reference in enumerate(original["image_map"]):
        path = tmp_path / f"synthetic-pixels-{index}.png"
        with Image.new("RGB", (320, 180), (120, 130 + index * 20, 145)) as image:
            image.save(path)
        versions.append(AssetVersion(id=reference["version_id"], version=4 if index == 0 else 3,
            origin_type=OriginType.MANUAL, descriptor=mapped["descriptor"] if index == 0 else reference["descriptor"],
            media_type=AssetMediaType.IMAGE, local_path=str(path), width=320, height=180,
            approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT,
            created_at=FIXTURE["source"]["created_at"]))
    asset = Asset(id=mapped["id"], project_id=pid, kind=AssetKind(mapped["kind"]), name=mapped["name"],
        brief=CoreAssetBrief.model_validate(mapped["brief"]), versions=versions, current_version_id=versions[0].id)
    asset.versions[0].image_task_id = "replay-exact-open-folder-task"
    document.assets.append(asset)
    script = document.script_versions[0].model_copy(deep=True, update={
        "id": FIXTURE["script"]["id"], "version": FIXTURE["script"]["version"],
        "lines": [ScriptLine.model_validate(line) for line in FIXTURE["lines"]],
        "structured_scenes": [StructuredScene.model_validate({**FIXTURE["scene"],
            "screenplay_text": "UNSELECTED_FULL_SCENE_BODY"}),
            StructuredScene(id="unrelated-scene", heading="UNRELATED_HEADING", summary="UNRELATED_SUMMARY", line_ids=[])],
        "screenplay_text": "UNSELECTED_WHOLE_SCREENPLAY", "structure_stale_scene_ids": [],
    })
    document.script_versions.append(script)
    document.project.current_script_version_id = script.id
    document.shots = [ShotPlan.model_validate({**FIXTURE["shot"], "project_id": pid})]
    service.repository.save_project(document)
    service.repository.save_image_task(ImageGenerationTask(id=asset.versions[0].image_task_id,
        project_id=pid, asset_id=asset.id, output_version_id=versions[0].id, output_version_number=4,
        descriptor=versions[0].descriptor, operation=ImageOperation.EDIT, status=ImageTaskStatus.SUCCEEDED,
        request=ImageProviderRequest(operation=ImageOperation.EDIT,
            prompt=original["candidate_creation"]["generation_prompt"], input_version_ids=[versions[1].id]),
        created_at=versions[0].created_at, updated_at=versions[0].created_at))
    return service, language, {"project_id": pid, "asset_id": asset.id, "version_id": versions[0].id,
        "request": {"review_focus": original["review_focus"], "canonical_reference_version_ids": [versions[1].id]}}


def _selection() -> dict[str, Any]:
    return {"scene_id": FIXTURE["scene"]["id"], "dialogue_line_ids": list(FIXTURE["selected_line_ids"])}


@pytest.mark.parametrize("include_shot", [False, True])
def test_actual_folder_focus_gets_exact_saved_causal_lines_without_whole_script(
    tmp_path: Path, include_shot: bool,
) -> None:
    service, language, arguments = _fixture(tmp_path)
    arguments["request"].update({"narrative_context": _selection(),
        "implementation_preferences": ["Keep the architecture and entrance width identical between both drawings."]})
    if include_shot:
        arguments["request"]["shot_id"] = FIXTURE["shot"]["id"]
    before_arguments = deepcopy(arguments)
    before_document = service.get_project(arguments["project_id"]).document
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    payload = json.loads(language.input_text)
    evidence = payload["narrative_context"]
    assert evidence["script_version_id"] == FIXTURE["script"]["id"]
    assert evidence["script_version"] == 3
    assert evidence["scene"] == {key: FIXTURE["scene"][key] for key in ("id", "heading", "summary")}
    expected_lines = [{key: line[key] for key in ("id", "speaker", "spoken_text")}
                      for line in FIXTURE["lines"] if line["id"] in FIXTURE["selected_line_ids"]]
    assert evidence["lines"] == expected_lines
    assert [line["spoken_text"] for line in evidence["lines"]] == [
        "入口要改，预算一分不加。怎么办？", "旧隔断别扔，挪过去用。省下的材料钱，正好改入口。"]
    assert payload["review_focus"] == FIXTURE["original_qa_payload"]["review_focus"]
    assert payload["candidate_creation"]["generation_prompt"] == FIXTURE["original_qa_payload"]["candidate_creation"]["generation_prompt"]
    assert payload["implementation_preferences"] == arguments["request"]["implementation_preferences"]
    assert payload["shot"] == (before_document.shots[0].model_dump(mode="json") if include_shot else None)
    for omitted in ("UNRELATED_HEADING", "UNRELATED_SUMMARY", "UNSELECTED_FULL_SCENE_BODY", "UNSELECTED_WHOLE_SCREENPLAY", "line-qiantai-12"):
        assert omitted not in language.input_text
    assert result.output["narrative_context"] == evidence
    assert result.output["implementation_preferences"] == payload["implementation_preferences"]
    # This is input transport evidence, not a live claim that the review now passes.
    assert result.output["recommended"] is False and result.output["score"] == 3.5
    assert result.output["issues"] == [issue.model_dump(mode="json") for issue in SemanticAssetReview.model_validate(FIXTURE["original_review"]).issues]
    assert language.calls == 1 and len(language.image_urls) == 2
    assert any(ref.artifact_type == "script_version" and ref.artifact_id == evidence["script_version_id"] and ref.version == 3 for ref in result.artifact_refs)
    assert arguments == before_arguments
    assert service.get_project(arguments["project_id"]).document == before_document
    assert "narrative_context" not in payload["output_schema"]["properties"]
    assert "implementation_preferences" not in payload["output_schema"]["properties"]
    assert "AssetInspectionNarrativeEvidence" not in payload["output_schema"]["$defs"]


def test_legacy_request_does_not_pull_story_or_require_preferences(tmp_path: Path) -> None:
    service, language, arguments = _fixture(tmp_path)
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    payload = json.loads(language.input_text)
    assert "narrative_context" not in payload and "implementation_preferences" not in payload
    assert result.output["narrative_context"] is None and result.output["implementation_preferences"] == []
    assert not any(ref.artifact_type == "script_version" for ref in result.artifact_refs)
    assert language.calls == 1 and len(language.image_urls) == 2


@pytest.mark.parametrize("fault", ["missing_scene", "foreign_line", "missing_line", "stale_scene", "missing_script", "duplicate_line"])
def test_unresolvable_scope_returns_local_feedback_without_calling_qa(tmp_path: Path, fault: str) -> None:
    service, language, arguments = _fixture(tmp_path)
    selection = _selection()
    document = service.get_project(arguments["project_id"]).document
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    if fault == "missing_scene":
        selection["scene_id"] = "does-not-exist"
    elif fault == "foreign_line":
        selection["scene_id"] = "unrelated-scene"
    elif fault == "missing_line":
        selection["dialogue_line_ids"] = ["not-a-line"]
    elif fault == "duplicate_line":
        selection["dialogue_line_ids"] *= 2
    elif fault == "stale_scene":
        script.structure_stale_scene_ids = [selection["scene_id"]]
    else:
        document.script_versions = []
    service.repository.save_project(document)
    arguments["request"]["narrative_context"] = selection
    before = service.get_project(arguments["project_id"]).document
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status != ToolCallStatus.SUCCEEDED and result.error is not None
    assert "narrative_context" in result.error.message
    assert result.error.retryable is False
    assert language.calls == 0
    assert service.get_project(arguments["project_id"]).document == before


def test_reopened_service_resolves_latest_saved_lines_and_retains_old_receipt(tmp_path: Path) -> None:
    service, language, arguments = _fixture(tmp_path)
    arguments["request"]["narrative_context"] = _selection()
    first = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert first.output is not None
    frozen = first.model_dump(mode="json")
    document = service.get_project(arguments["project_id"]).document
    prior = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    updated = ScriptVersion.model_validate(prior.model_dump(mode="json"))
    updated.id, updated.version = "current-script-v4", 4
    updated.lines[5].spoken_text = "本次明确改过的入口方案。"
    document.script_versions.append(updated)
    document.project.current_script_version_id = updated.id
    service.repository.save_project(document)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language)
    second = reopened.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert second.status == ToolCallStatus.SUCCEEDED and second.output is not None
    evidence = second.output["narrative_context"]
    assert evidence["script_version_id"] == updated.id and evidence["script_version"] == 4
    assert evidence["lines"][1]["spoken_text"] == updated.lines[5].spoken_text
    assert first.model_dump(mode="json") == frozen
    assert language.calls == 2


def test_scene_only_selector_and_saved_line_order_are_supported(tmp_path: Path) -> None:
    service, language, arguments = _fixture(tmp_path)
    arguments["request"]["narrative_context"] = {"scene_id": FIXTURE["scene"]["id"]}
    first = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert first.status == ToolCallStatus.SUCCEEDED and first.output is not None
    assert first.output["narrative_context"]["lines"] == []
    arguments["request"]["narrative_context"] = {**_selection(), "dialogue_line_ids": list(reversed(FIXTURE["selected_line_ids"]))}
    second = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert second.status == ToolCallStatus.SUCCEEDED and second.output is not None
    assert [line["id"] for line in second.output["narrative_context"]["lines"]] == FIXTURE["selected_line_ids"]
    assert language.calls == 2
    assert AssetVersionInspectionRequest().implementation_preferences == []


@pytest.mark.parametrize("role", [ProductionSkillRole.ASSET_BUILDER, ProductionSkillRole.ASSET_VERIFIER])
def test_loaded_role_uses_actual_optional_qa_schema(tmp_path: Path, role: ProductionSkillRole) -> None:
    service, _language, _arguments = _fixture(tmp_path)
    schema = service.tool_registry.describe("qa.inspect_asset_version").input_schema
    request = schema["$defs"]["AssetVersionInspectionRequest"]
    compiled = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(role,),
        stage=ProductionPromptStage.ASSETS, include_constitution=False)
    for field in ("narrative_context", "implementation_preferences"):
        assert field in request["properties"] and field not in request.get("required", [])
        assert field in compiled.text
    assert "dialogue_line_ids" in schema["$defs"]["AssetInspectionNarrativeContext"]["properties"]
