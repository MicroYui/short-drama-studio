"""Observed edit geometry reaches the Tool result and resumed Worker unchanged."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError
import pytest

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.asset_inspection import AssetVersionInspectionRequest, SemanticAssetReview, inspect_asset_image
from short_drama.image_edit_guides import AssetImageEditGuideRequest
from short_drama.image_models import AssetImportLocalRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, AssetKind, Freshness
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work


class GuideLanguageClient(CapturingLanguageClient):
    def __init__(self, *, suggest: bool = True, geometry_fault: str | None = None) -> None:
        super().__init__()
        self.calls = 0
        self.suggest = suggest
        self.geometry_fault = geometry_fault

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        super().generate_text(instructions=instructions, input_text=input_text,
            image_urls=image_urls, max_output_tokens=max_output_tokens)
        self.calls += 1
        response: dict[str, Any] = {"recommended": False, "score": 3.0,
            "issues": [{"code": "PROP_ORIENTATION_MISMATCH", "severity": "major",
                        "message": "可见纸张方向与本次要求不同。", "repair_layer": "KEYFRAME_IMAGE"}],
            "summary": "当前候选有需要修复的局部纸张方向；建议尚未执行。"}
        if self.suggest:
            response["edit_guide_suggestion"] = {
                "source_asset_version_id": "wrong-reference-image",
                "regions": [{"polygon": [{"x": .2, "y": .3}, {"x": .8, "y": .3},
                    {"x": .8, "y": .9}, {"x": .2, "y": .9}], "label": "在此可见桌面范围调整纸张方向，保留外部人脸。"}],
                "arrows": [{"start": {"x": .5, "y": .4}, "end": {"x": .5, "y": .8}, "label": "标题端移至此边"}],
                "descriptor": "根据本次实际可见纸张与明确目标圈出的编辑指示。"}
            if self.geometry_fault == "REGION":
                # Nonzero normalized area, but three collinear rasterized pixels.
                response["edit_guide_suggestion"]["regions"][0]["polygon"] = [
                    {"x": .2, "y": .3}, {"x": .4, "y": .30001}, {"x": .6, "y": .3}]
            elif self.geometry_fault == "ARROW":
                response["edit_guide_suggestion"]["arrows"][0]["end"] = {"x": .5001, "y": .4001}
            elif self.geometry_fault == "NORMALIZED_REGION":
                response["edit_guide_suggestion"]["regions"][0]["polygon"] = [
                    {"x": .2, "y": .3}, {"x": .4, "y": .3}, {"x": .6, "y": .3}]
            elif self.geometry_fault == "NORMALIZED_ARROW":
                response["edit_guide_suggestion"]["arrows"][0]["end"] = {"x": .5, "y": .4}
        return json.dumps(response, ensure_ascii=False)


def _setup(tmp_path: Path, *, suggest: bool = True, rotated: bool = False,
           size: tuple[int, int] = (1024, 1024), geometry_fault: str | None = None):
    language = GuideLanguageClient(suggest=suggest, geometry_fault=geometry_fault)
    service = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    asset = next(a for a in document.assets if a.kind == AssetKind.CHARACTER)
    path = tmp_path / ("candidate.jpg" if rotated else "candidate.png")
    with Image.new("RGB", size, "#998877") as image:
        if rotated:
            exif = image.getexif()
            exif[274] = 6
            image.save(path, exif=exif)
        else:
            image.save(path)
    version = service.import_local_asset_version(document.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="已保存的候选构图")).version
    return service, language, document.project.id, asset.id, version


@pytest.mark.parametrize("suggest", [True, False])
def test_actual_qa_suggestion_survives_other_read_and_worker_restart(tmp_path: Path, suggest: bool) -> None:
    service, language, pid, aid, version = _setup(tmp_path, suggest=suggest)
    before = service.get_project(pid).document.model_dump(mode="json")
    arguments = {"project_id": pid, "asset_id": aid, "version_id": version.id, "request": {}}
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    suggestion = result.output["edit_guide_suggestion"]
    if suggest:
        request = AssetImageEditGuideRequest.model_validate(suggestion)
        assert request.source_asset_version_id == version.id
        assert request.regions[0].polygon[0].x == .2
        assert request.arrows[0].label == "标题端移至此边"
    else:
        assert suggestion is None
    assert result.output["recommended"] is False and result.output["score"] == 3.0
    assert language.calls == 1
    assert json.loads(language.input_text)["candidate_creation"]["local_edit_guide_available"] is True
    assert "范围无法确定则留空" in language.instructions
    assert "不改变recommended或已有问题" in language.instructions
    assert service.get_project(pid).document.model_dump(mode="json") == before
    store, work, context = runtime_work(tmp_path / "worker.sqlite3")
    context.project_id = pid
    work.specialist_turns = [SpecialistTurnRecord(id="real-qa", sequence=1, tool_name="qa.inspect_asset_version",
        arguments=arguments, reason="Read actual candidate geometry.", status=SpecialistTurnStatus.COMPLETED,
        result=result.model_dump(mode="json")), SpecialistTurnRecord(id="later-read", sequence=2,
        tool_name="artifact.read_current", arguments={"project_id": pid}, reason="Read current instructions.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"status": "available"}})]
    store.save_work_order(work)
    restarted = AgentRuntimeStore(store.database_path)
    saved = restarted.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue with saved evidence.", "arguments": {"project_id": pid}})])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Prepare the selected edit."))
    working = client.payloads[0]["working_set"]
    assert isinstance(working, dict)
    entities = working["entities"]
    assert isinstance(entities, list)
    review = next(item for item in entities if isinstance(item, dict) and item["kind"] == "asset_review")
    state = review["state"]
    assert isinstance(state, dict)
    assert state["version_id"] == version.id
    assert state.get("edit_guide_suggestion") == suggestion
    assert state["recommended"] is False


@pytest.mark.parametrize("ineligible", ["REMOTE", "REJECTED", "STALE", "ROTATED"])
def test_unsupported_source_drops_only_optional_guide(tmp_path: Path, ineligible: str) -> None:
    service, language, pid, aid, version = _setup(tmp_path, rotated=ineligible == "ROTATED")
    document = service.get_project(pid).document
    asset = next(a for a in document.assets if a.id == aid)
    if ineligible == "REMOTE":
        version.external_url = "https://example.invalid/different-candidate.png"
    elif ineligible == "REJECTED":
        version.approval_status = ApprovalStatus.REJECTED
    elif ineligible == "STALE":
        version.freshness = Freshness.STALE
    result = inspect_asset_image(document=document, asset=asset, version=version,
        client=language, request=AssetVersionInspectionRequest())
    assert result.edit_guide_suggestion is None
    assert result.recommended is False and result.issues[0].code == "PROP_ORIENTATION_MISMATCH"
    assert language.calls == 1
    assert json.loads(language.input_text)["candidate_creation"]["local_edit_guide_available"] is False


@pytest.mark.parametrize("size", [(900, 1600), (955, 1696), (320, 208)])
def test_masked_edit_incompatible_size_drops_only_optional_guide(tmp_path: Path, size: tuple[int, int]) -> None:
    service, language, pid, aid, version = _setup(tmp_path, size=size)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": aid, "version_id": version.id, "request": {}})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert result.output["edit_guide_suggestion"] is None
    assert result.output["recommended"] is False and result.output["score"] == 3.0
    creation = json.loads(language.input_text)["candidate_creation"]
    assert creation["local_crop_available"] is True
    assert creation["local_edit_guide_available"] is False
    assert language.calls == 1


@pytest.mark.parametrize("geometry_fault", ["REGION", "ARROW", "NORMALIZED_REGION", "NORMALIZED_ARROW"])
def test_raster_degenerate_geometry_preserves_review(tmp_path: Path, geometry_fault: str) -> None:
    service, language, pid, aid, version = _setup(tmp_path, geometry_fault=geometry_fault)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": aid, "version_id": version.id, "request": {}})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert result.output["edit_guide_suggestion"] is None
    assert result.output["recommended"] is False and result.output["score"] == 3.0
    assert json.loads(language.input_text)["candidate_creation"]["local_edit_guide_available"] is True
    assert language.calls == 1


def test_invalid_optional_guide_does_not_relax_required_review() -> None:
    with pytest.raises(ValidationError, match="score"):
        SemanticAssetReview.model_validate({"recommended": False, "score": 6, "summary": "Original conclusion",
            "edit_guide_suggestion": {"regions": [], "descriptor": "Invalid optional geometry"}})
    with pytest.raises(ValidationError, match="Invalid JSON"):
        SemanticAssetReview.model_validate_json('{"recommended": false, "edit_guide_suggestion":')
