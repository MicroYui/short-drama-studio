"""One visual call, two exact observations; saved completion is not image adoption."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.agent_runtime.models import AgentResultStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.asset_inspection import AssetVersionInspectionRequest, inspect_asset_image
from short_drama.h3_models import H3MediaRole, H3RouteAssetPreparation
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, Freshness
from short_drama.production_manager import AgentWorkOrderResultRequest, ProductionManagerError
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_image_edit_results import _completed


FIXTURE = Path(__file__).parent / "fixtures/fresh5_s11_raw_final_reviews.json"


class PairClient(CapturingLanguageClient):
    def __init__(self) -> None:
        super().__init__()
        actual: dict[str, Any] = json.loads(FIXTURE.read_text())
        turns = {turn["sequence"]: turn for turn in actual["turns"]}
        self.response: dict[str, Any] = deepcopy(turns[42]["result"]["output"])
        self.response["provider_output_review"] = deepcopy(turns[43]["result"]["output"])
        self.calls = 0

    def generate_text(self, *, instructions: str, input_text: str,
        image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        del max_output_tokens
        self.calls += 1
        self.instructions, self.input_text = instructions, input_text
        self.image_urls = list(image_urls or [])
        return json.dumps(self.response, ensure_ascii=False)


def setup_pair(tmp_path: Path):
    fixture, provider, asset, base, mask, task = _completed(tmp_path)
    client = PairClient()
    service = ShortDramaService(database_path=fixture.repository.database_path,
        workspace_root=fixture.workspace_root, language_provider_factory=lambda _key, _url: client)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    return service, provider, client, asset, base, mask, task


def test_actual_two_failed_reports_are_independent_in_one_real_tool_call(tmp_path: Path) -> None:
    service, provider, client, asset, base, mask, task = setup_pair(tmp_path)
    before = service.get_project(asset.project_id).document
    shot = before.shots[0]
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": asset.project_id, "asset_id": asset.id, "version_id": task.output_version_id,
        "request": {"compare_provider_output": True, "shot_id": shot.id,
                    "intended_reference_role": "COMPOSITION", "review_focus": "停笔图：目标位置与可见连接。"},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    final, raw = result.output, result.output["provider_output_review"]
    assert final["recommended"] is raw["recommended"] is False
    assert (final["score"], raw["score"]) == (1.8, 3.4)
    assert raw["issues"] == client.response["provider_output_review"]["issues"]
    assert final["issues"] == client.response["issues"]
    assert raw["asset_version_id"] == task.provider_output_version_id
    assert raw["image_task_id"] == task.id and raw["asset_id"] == asset.id
    assert final["shot_revision"] == raw["shot_revision"] == shot.revision
    assert raw["review_focus"] == final["review_focus"]
    assert raw["edit_guide_suggestion"] is final["edit_guide_suggestion"] is None
    payload = json.loads(client.input_text)
    assert len(client.image_urls) == 4 and client.calls == 1
    assert [(item["image"], item["role"]) for item in payload["image_map"]] == [
        (1, "candidate_under_review"), (2, "derivation_reference"),
        (3, "provider_output_under_review"), (4, "EDIT_MASK")]
    actual_mask = payload["candidate_creation"]["masked_edit"]["actual_mask"]
    assert actual_mask["asset_version_id"] == mask.id
    assert actual_mask["editable_bounds_pixels"] == [200, 200, 569, 1201]
    assert (actual_mask["width"], actual_mask["height"]) == (768, 1344)
    assert "审核镜头关键帧时分两步" not in client.instructions
    assert "provider_output_review对应" in client.instructions
    assert "整图和provider_output_creation" in client.instructions
    assert "编辑源由运行时绑定Image 1" not in client.instructions
    assert "region使用Image 1" not in client.instructions
    assert "provider_output_review" in payload["output_schema"]["required"]
    refs = {ref.artifact_id: ref for ref in result.artifact_refs}
    versions = {v.id: v.version for a in before.assets for v in a.versions}
    assert refs[task.provider_output_version_id or ""].version == versions[task.provider_output_version_id or ""]
    assert refs[shot.id].artifact_type == "shot" and refs[shot.id].version == shot.revision
    assert service.get_project(asset.project_id).document == before
    assert len(provider.calls) == 1 and Path(base.local_path or "").is_file()


@pytest.mark.parametrize("problem", ["missing", "rejected", "stale", "wrong_task", "wrong_locator", "legacy", "not_final"])
def test_pair_rejects_unavailable_or_unrelated_raw_before_language(tmp_path: Path, problem: str) -> None:
    service, provider, client, asset, base, _mask, task = setup_pair(tmp_path)
    document = service.get_project(asset.project_id).document
    raw = next(v for a in document.assets for v in a.versions if v.id == task.provider_output_version_id)
    assert raw.source is not None
    if problem == "missing":
        Path(raw.local_path or "").unlink()
    elif problem == "rejected":
        raw.approval_status = ApprovalStatus.REJECTED
    elif problem == "stale":
        raw.freshness = Freshness.STALE
    elif problem == "wrong_task":
        provenance = json.loads(raw.source.prompt or "{}")
        provenance["image_task_id"] = "another-task"
        raw.source.prompt = json.dumps(provenance)
    elif problem == "wrong_locator":
        raw.source.source_uri = base.local_path
    elif problem == "legacy":
        task.provider_output_version_id = None
        service.repository.save_image_task(task)
    service.repository.save_project(document)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": asset.project_id, "asset_id": asset.id,
        "version_id": raw.id if problem == "not_final" else task.output_version_id,
        "request": {"compare_provider_output": True},
    })
    assert result.status == ToolCallStatus.FAILED
    assert client.calls == 0 and len(provider.calls) == 1
    assert service.get_project(asset.project_id).document == document


@pytest.mark.parametrize("role", [None, "COMPOSITION", "FIRST_FRAME", "LAST_FRAME"])
def test_actual_typed_role_controls_boundary_instructions(tmp_path: Path, role: str | None) -> None:
    service, _provider, client, asset, base, _mask, _task = setup_pair(tmp_path)
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    client.response.pop("provider_output_review")
    inspect_asset_image(document=document, asset=target, version=base, client=client,
        request=AssetVersionInspectionRequest.model_validate({"intended_reference_role": role}))
    assert ("审核镜头关键帧时分两步" in client.instructions) is (role in {"FIRST_FRAME", "LAST_FRAME"})
    assert json.loads(client.input_text)["intended_reference_role"] == role


@pytest.mark.parametrize("role", [None, "COMPOSITION"])
def test_explicit_current_use_precedes_older_preparation_role(tmp_path: Path, role: str | None) -> None:
    service, _provider, client, asset, base, _mask, _task = setup_pair(tmp_path)
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    client.response.pop("provider_output_review")
    preparation = H3RouteAssetPreparation(id="prior-first-frame", shot_id=document.shots[0].id,
        role=H3MediaRole.FIRST_FRAME, name="Prior first frame", descriptor="Prior preparation",
        prompt="Original first frame design", rationale="Saved earlier intended use")
    inspect_asset_image(document=document, asset=target, version=base, client=client,
        preparation=preparation,
        request=AssetVersionInspectionRequest.model_validate({"intended_reference_role": role}))
    assert ("审核镜头关键帧时分两步" in client.instructions) is (role is None)
    assert json.loads(client.input_text)["keyframe_preparation"]["id"] == preparation.id


@pytest.mark.parametrize("bad_report", [None, {"recommended": True, "score": 9, "summary": "invalid"}])
def test_requested_comparison_never_invents_missing_or_invalid_raw_verdict(tmp_path: Path, bad_report: object) -> None:
    service, _provider, client, asset, _base, _mask, task = setup_pair(tmp_path)
    client.response["provider_output_review"] = bad_report
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": asset.project_id, "asset_id": asset.id, "version_id": task.output_version_id,
        "request": {"compare_provider_output": True}})
    assert result.status == ToolCallStatus.FAILED and client.calls == 1


@pytest.mark.parametrize("raw_source", ["seen", "unseen"])
def test_each_observed_image_keeps_its_own_geometry_and_verdict(tmp_path: Path, raw_source: str) -> None:
    service, _provider, client, asset, base, _mask, task = setup_pair(tmp_path)
    quad = [{"x": .1, "y": .1}, {"x": .9, "y": .1}, {"x": .9, "y": .9}, {"x": .1, "y": .9}]
    for report in (client.response, client.response["provider_output_review"]):
        report["crop_suggestion"] = {"region": {"x": .1, "y": .1, "width": .8, "height": .8}, "reason": "Observed framing."}
        report["edit_guide_suggestion"] = {"regions": [{"polygon": quad, "label": "Observed connected area"}], "descriptor": "Observed edit"}
        report["plane_composite_suggestion"] = {"base_asset_version_id": "model-must-not-choose-base",
            "layers": [{"source_asset_version_id": base.id, "source_quad": quad, "target_quad": quad}],
            "descriptor": "Observed plane"}
    child = client.response["provider_output_review"]
    child.update(recommended=True, score=4.8, issues=[], summary="Synthetic independent positive report for wiring, not a new S11 visual verdict.")
    if raw_source == "unseen":
        child["plane_composite_suggestion"]["layers"][0]["source_asset_version_id"] = "unobserved-source"
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": asset.project_id, "asset_id": asset.id, "version_id": task.output_version_id,
        "request": {"compare_provider_output": True, "regions": [
            {"asset_version_id": task.provider_output_version_id, "label": "Raw detail", "region": {"x": .1, "y": .1, "width": .3, "height": .3}}]}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    final, raw = result.output, result.output["provider_output_review"]
    assert final["recommended"] is False and raw["recommended"] is True
    assert len(client.image_urls) == 5 and client.calls == 1
    for report, version_id in ((final, task.output_version_id), (raw, task.provider_output_version_id)):
        assert report["crop_suggestion"]["source"]["source_asset_version_id"] == version_id
        assert report["edit_guide_suggestion"]["source_asset_version_id"] == version_id
    assert final["plane_composite_suggestion"]["base_asset_version_id"] == task.output_version_id
    if raw_source == "seen":
        assert raw["plane_composite_suggestion"]["base_asset_version_id"] == task.provider_output_version_id
    else:
        assert raw["plane_composite_suggestion"] is None
    assert raw["region_evidence"][0]["asset_version_id"] == task.provider_output_version_id


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("invalid", [None, "version", "missing_shot"])
def test_inspection_refs_saved_sequences_survive_reopen_and_submit(
    tmp_path: Path, legacy: bool, invalid: str | None,
) -> None:
    service, _provider, client, asset, _base, _mask, task = setup_pair(tmp_path)
    document = service.get_project(asset.project_id).document
    shot = document.shots[0]
    args = {"project_id": asset.project_id, "asset_id": asset.id, "version_id": task.output_version_id,
            "request": {"shot_id": shot.id, "compare_provider_output": True}}
    call = service.tool_registry.invoke("qa.inspect_asset_version", args)
    assert call.status == ToolCallStatus.SUCCEEDED
    refs = list(call.artifact_refs)
    index = next(i for i, ref in enumerate(refs) if ref.artifact_type == "shot")
    refs[index] = refs[index].model_copy(update={
        "artifact_type": "shot_plan" if legacy else "shot",
        "artifact_id": "missing-shot" if invalid == "missing_shot" else shot.id,
        "version": shot.revision + 100 if invalid == "version" else shot.revision})
    call = call.model_copy(update={"artifact_refs": refs})
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=asset.project_id, objective="Report actual inspection only.", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="asset_builder",
        objective="Report inspected candidates without adopting either.", why_now="Review complete")
    work.specialist_turns = [SpecialistTurnRecord(id="actual-paired-qa", sequence=1,
        tool_name="qa.inspect_asset_version", arguments=args, reason="Inspect both saved candidates",
        status=SpecialistTurnStatus.COMPLETED, result=call.model_dump(mode="json"))]
    runtime.store.save_work_order(work)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    resolver = ProjectArtifactResolver(reopened.repository)
    assert (resolver.summarize_artifact(refs[index]) is None) is (invalid is not None)
    response = AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="Inspection finished; neither image fulfills the requested pen position.",
        saved_tool_result_sequences=[1], findings=["Two independent negative observations; no image was adopted."])
    if invalid:
        with pytest.raises(ProductionManagerError, match="not found|unresolved|artifact"):
            reopened.submit_agent_work_order_result(work.id, response)
        assert reopened.agent_runtime.store.get_agent_result(work.id) is None
    else:
        result = reopened.submit_agent_work_order_result(work.id, response).result
        assert result.status == AgentResultStatus.COMPLETED
        assert refs[index] in result.evidence_refs
        assert ArtifactRef(project_id=asset.project_id, artifact_type="asset_version",
            artifact_id=task.provider_output_version_id or "", version=task.provider_output_version_number) in result.evidence_refs
        assert reopened.agent_runtime.store.get_context_snapshot(
            f"context-completion-tool-{work.id}-actual-paired-qa", run.id)["result"] == call.model_dump(mode="json")
    assert reopened.get_project(asset.project_id).document == document
    assert client.calls == 1
