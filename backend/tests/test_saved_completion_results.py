"""Saved Fresh3 results reach actual Worker/Leader inputs without manual metadata copying."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from PIL import Image
from pydantic import JsonValue, ValidationError

from short_drama.agent_runtime import ActionKind, AgentRuntime, AgentRuntimeStore, ArtifactRef, ContextPack, WorkOrder
from short_drama.agent_runtime.completion_saved_results import prepare_saved_completion_results, saved_tool_result_index
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.leader_context import completed_work_handoffs
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import AgentResultStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.models import Asset, ProjectDocument, ReferencePack
from short_drama.image_models import ImageGenerationTask
from short_drama.production_manager import AgentWorkOrderResultRequest, AgentWorkOrderView, LanguageProductionPlanner, LanguageSpecialistAgent, ProductionManagerError
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioScope
from short_drama.tools import ToolCallStatus
from tests.test_agent_runtime import FakeArtifactResolver
from tests.test_completed_worker_handoff import worker_input
from tests.test_harness_tool_context import ScriptedClient, registry


FIXTURE = Path(__file__).parent / "fixtures" / "fresh3_completed_character_tools.json"


def replay(tmp_path: Path, role_index: int = 0) -> tuple[ShortDramaService, WorkOrder, ProjectDocument, dict[str, JsonValue]]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    service.initialize_fixture()
    document = service.get_project(service.list_projects().projects[0].id).document
    raw = cast(dict[str, JsonValue], json.loads(FIXTURE.read_text()))
    role = cast(dict[str, JsonValue], cast(list[JsonValue], raw["roles"])[role_index])
    # These bytes exercise file availability only. The real saved QA conclusions
    # and exact metadata remain fixture evidence, not a new inspection of pixels.
    fixture_dir = tmp_path / "availability"
    fixture_dir.mkdir()
    def remap(value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, str):
            if value == raw["source_project_id"]:
                return document.project.id
            if value.startswith("/fixtures/fresh3/"):
                path = fixture_dir / Path(value).name
                if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    Image.new("RGB", (24, 32), "white").save(path)
                return str(path)
        return value
    role = cast(dict[str, JsonValue], remap(role))
    asset, pack = Asset.model_validate(role["asset"]), ReferencePack.model_validate(role["reference_pack"])
    document.assets.append(asset)
    document.reference_packs.append(pack)
    service.repository.save_project(document)
    service.repository.save_image_task(ImageGenerationTask.model_validate(role["image_task"]))
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=document.project.id, objective="只完成视频前准备，不生成视频。", root_agent="leader")
    original = cast(dict[str, JsonValue], role["work_order"])
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="asset_builder",
        objective=cast(str, original["objective"]), why_now="Replay saved Fresh3 character results",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER,
        task_inputs=cast(dict[str, JsonValue], original["task_inputs"]),
        expected_output_schema=cast(dict[str, JsonValue], original["expected_output_schema"]),
        acceptance_criteria=cast(list[str], original["acceptance_criteria"]),
        scope=[ArtifactRef.model_validate(ref) for ref in cast(list[JsonValue], original["scope"])])
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in cast(list[JsonValue], role["specialist_turns"])]
    runtime.store.save_work_order(work)
    return service, work, document, role


def compact_response() -> AgentWorkOrderResultRequest:
    return AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="人物身份与服装母版、脸部参考已保存并登记用途。",
        saved_tool_result_sequences=[9, 10],
        findings=["选用的全身母版和脸部裁片可锚定同一人物；本次未生成额外身份视图。"],
        deliverable={"limitations": [{"message": "母版不是剧情首帧，手物接触与动态表演仍需在具体镜头验证。"}]},
        recommended_next_actions=["按实际镜头用途复用人物参考，保持用户视频前停止边界。"])


@pytest.mark.parametrize("role_index", [0, 1])
def test_real_successful_sources_reach_worker_submission_and_exact_completed_receipts(tmp_path: Path, role_index: int) -> None:
    service, work, document, role = replay(tmp_path, role_index)
    before = service.repository.get_project(document.project.id)
    client = ScriptedClient([compact_response().model_dump_json(exclude_defaults=True)])
    agent = LanguageSpecialistAgent(client, service.tool_registry)
    agent.context_compiler = ModelInputCompiler(service.agent_runtime.store)
    request = agent.execute(service.get_agent_work_order(work.id))
    assert isinstance(request, AgentWorkOrderResultRequest)
    payload = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    index = cast(list[dict[str, JsonValue]], payload["saved_tool_result_index"])
    assert {9, 10} <= {row["source_sequence"] for row in index}
    result = service.submit_agent_work_order_result(work.id, request).result
    assert result.created_artifacts == result.updated_artifacts == []
    assert result.status == AgentResultStatus.COMPLETED
    assert service.repository.get_project(document.project.id) == before
    refs = [ref for ref in result.evidence_refs if ref.artifact_type == "asset_version"]
    selected = document.assets[-1].versions[-2:]
    assert {(version.id, version.version) for version in selected} <= {(ref.artifact_id, ref.version) for ref in refs}
    assert selected[-1].approval_status.value == "DRAFT"
    facts = result.completion_receipts
    assert len([row for row in facts if row.get("kind") == "saved_tool_result"]) == 2
    observations = [row for row in facts if row.get("kind") == "saved_media_observation"]
    assert len(observations) == 4  # Two existing QA purposes and two saved reference uses.
    assert len([row for row in observations if row["observation_kind"] == "saved_reference_use"]) == 2
    for row in observations:
        marker = cast(dict[str, JsonValue], row["saved_result"])
        source = service.agent_runtime.store.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id)
        assert source is not None
        if row["observation_kind"] == "image_inspection":
            output = cast(dict[str, JsonValue], cast(dict[str, JsonValue], source["result"])["output"])
            assert row["result"] == {key: output.get(key) for key in ("recommended", "score", "issues", "summary")}
    assert count_text_tokens(compact_response().model_dump_json(exclude_defaults=True)) < cast(int, role["original_visible_tokens"])
    assert len(client.payloads) == 1  # Fake model only; submission performs no Tool/model call.
    now = work.updated_at.isoformat()
    service.repository.save_studio_job(StudioJob(id="saved-result-job", project_id=document.project.id,
        run_id=work.parent_run_id, objective=work.objective, status="running", scope=StudioScope(), created_at=now, updated_at=now))
    worker = service.studio_runtime.activity(document.project.id).workers[0]
    selected_refs = [row for row in facts if row.get("kind") == "saved_tool_result"]
    exact = {(ref["artifact_type"], ref["artifact_id"]) for row in selected_refs
        for ref in cast(list[dict[str, JsonValue]], row["artifact_refs"])}
    assert worker.completed_artifact_count == len(exact) > 0


def test_actual_leader_after_twelve_actions_and_restart_retains_selected_saved_media(tmp_path: Path) -> None:
    service, work, document, _role = replay(tmp_path)
    runtime = service.agent_runtime
    delegation = runtime.begin_action(work.parent_run_id, kind=ActionKind.DELEGATION, agent_id="leader",
        input_summary="Prepare identity references", work_order_id=work.id)
    result = service.submit_agent_work_order_result(work.id, compact_response()).result
    action = runtime.store.get_action(delegation.id)
    assert action is not None and {ref.artifact_id for ref in action.artifact_refs} >= {v.id for v in document.assets[-1].versions[-2:]}
    for i in range(12):
        action = runtime.begin_action(work.parent_run_id, kind=ActionKind.CHECKPOINT, agent_id="leader", input_summary=f"Later {i}")
        runtime.complete_action(action.id, output_summary="Another action")
    reopened = AgentRuntimeStore(tmp_path / "studio.sqlite3")
    client = ScriptedClient([json.dumps({"summary": "Use existing identities for the remaining work.", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(reopened)
    context = ContextCompiler(reopened, FakeArtifactResolver([])).compile(work.parent_run_id)
    planner.propose_plan(context=context, skill_prompt="", method_skills=[], tools=registry().list(), current_plan=None, reason="Continue after restart")
    working = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    assert "saved_media_observation" in json.dumps(working["completion_handoffs"])
    assert document.assets[-1].versions[-1].id in json.dumps(working["artifact_refs"])
    rows, _refs = completed_work_handoffs(reopened, run_id=work.parent_run_id, project_id=document.project.id, token_budget=0)
    marker = cast(dict[str, JsonValue], cast(dict[str, JsonValue], rows[0])["result"])
    snapshot = reopened.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id)
    assert snapshot == {"result": result.model_dump(mode="json")}
    selected_receipt = next(row for row in result.completion_receipts if row.get("kind") == "saved_tool_result")
    tool_marker = cast(dict[str, JsonValue], selected_receipt["saved_result"])
    assert reopened.get_context_snapshot(cast(str, tool_marker["context_ref"]), work.parent_run_id) == {
        "arguments": work.specialist_turns[-2].arguments, "result": work.specialist_turns[-2].result}
    reader = next(row for row in result.completion_receipts if row.get("kind") == "saved_result_read")
    read_contract = cast(dict[str, JsonValue], reader["read"])
    chunks: list[str] = []
    offset = 0
    while True:
        page = service.tool_registry.invoke("agent.read_context", {
            "project_id": read_contract["project_id"], "run_id": read_contract["run_id"],
            "snapshot_id": tool_marker["context_ref"], "pointer": "/result/output/entry", "offset": offset, "limit": 300})
        assert page.status == ToolCallStatus.SUCCEEDED, page.error
        content = cast(dict[str, JsonValue], page.output)
        chunks.append(cast(str, content["content"]))
        next_offset = content["next_offset"]
        if next_offset is None:
            break
        offset = cast(int, next_offset)
    entry = cast(dict[str, JsonValue], cast(dict[str, JsonValue], work.specialist_turns[-2].result)["output"])["entry"]
    assert len(chunks) > 1 and json.loads("".join(chunks)) == entry
    related = AgentRuntime(reopened).create_work_order(work.parent_run_id, sender="leader", recipient_role="director",
        objective="Use the saved character", why_now="Next composition", task_inputs={"asset_version_id": document.assets[-1].versions[-1].id})
    assert "completion_handoffs" in worker_input(reopened, related, context)
    unrelated = AgentRuntime(reopened).create_work_order(work.parent_run_id, sender="leader", recipient_role="director",
        objective="Work on room", why_now="Independent", task_inputs={"asset_id": "unrelated-room"})
    assert "completion_handoffs" not in worker_input(reopened, unrelated, context)


@pytest.mark.parametrize("case", ["missing", "failed", "running", "foreign"])
def test_invalid_saved_source_rejects_before_recording_result(tmp_path: Path, case: str) -> None:
    service, work, _document, _role = replay(tmp_path)
    sequence = 10
    last = work.specialist_turns[-1]
    if case == "missing":
        sequence = 900
    elif case == "running":
        last.status = SpecialistTurnStatus.IN_PROGRESS
    elif case == "failed":
        assert last.result is not None
        last.result["status"] = "INVALID_ARGUMENTS"
    else:
        assert last.result is not None
        refs = cast(list[dict[str, JsonValue]], last.result["artifact_refs"])
        refs[0]["project_id"] = "another-project"
    service.agent_runtime.store.save_work_order(work)
    with pytest.raises(ProductionManagerError, match="saved_tool_result_sequences"):
        service.submit_agent_work_order_result(work.id, compact_response().model_copy(update={"saved_tool_result_sequences": [sequence]}))
    assert service.agent_runtime.store.get_agent_result(work.id) is None


def test_partial_and_custom_deliverable_contract_are_not_overridden_by_saved_success(tmp_path: Path) -> None:
    service, work, _document, _role = replay(tmp_path)
    schema: dict[str, JsonValue] = {"type": "object", "required": ["director_decision"], "properties": {"director_decision": {"type": "string"}}}
    assert work.context_manifest is not None
    work = work.model_copy(update={"expected_output_schema": schema,
        "context_manifest": work.context_manifest.model_copy(update={"expected_output_schema": schema})})
    service.agent_runtime.store.save_work_order(work)
    with pytest.raises(ProductionManagerError, match="director_decision"):
        service.submit_agent_work_order_result(work.id, compact_response())
    assert service.agent_runtime.store.get_agent_result(work.id) is None
    request = AgentWorkOrderResultRequest(status=AgentResultStatus.PARTIAL, concise_summary="Images saved; explicit director decision remains.",
        saved_tool_result_sequences=[9, 10], unresolved_questions=["Which story framing is required?"])
    result = service.submit_agent_work_order_result(work.id, request).result
    assert result.status == AgentResultStatus.PARTIAL and result.unresolved_questions == request.unresolved_questions
    assert completed_work_handoffs(service.agent_runtime.store, run_id=work.parent_run_id, project_id=service.list_projects().projects[0].id)[0] == []


def test_optional_source_sequence_is_strict_and_legacy_results_remain_unchanged(tmp_path: Path) -> None:
    service, work, _document, role = replay(tmp_path)
    legacy = AgentWorkOrderResultRequest.model_validate(role["original_final"])
    assert legacy.saved_tool_result_sequences == []
    # The original response retains its report exactly; no migration/rewriting.
    assert legacy.deliverable == cast(dict[str, JsonValue], role["original_final"])["deliverable"]
    for invalid in [[True], ["9"], [0]]:
        with pytest.raises(ValidationError):
            AgentWorkOrderResultRequest.model_validate({"status": "COMPLETED", "concise_summary": "Saved", "saved_tool_result_sequences": invalid})
    result = service.submit_agent_work_order_result(work.id, AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="Analysis complete.", findings=["The identity references are suitable for the intended scope."]))
    assert result.result.completion_receipts == []


@pytest.mark.parametrize("role_index", [0, 1])
def test_original_real_final_report_passes_the_same_submit_and_leader_projection(tmp_path: Path, role_index: int) -> None:
    service, work, document, role = replay(tmp_path, role_index)
    original = AgentWorkOrderResultRequest.model_validate(role["original_final"])
    result = service.submit_agent_work_order_result(work.id, original).result
    assert result.deliverable == original.deliverable
    assert result.created_artifacts == original.created_artifacts
    assert not any(row.get("kind") == "saved_tool_result" for row in result.completion_receipts)
    rows, refs = completed_work_handoffs(service.agent_runtime.store, run_id=work.parent_run_id, project_id=document.project.id)
    assert rows and {version.id for version in document.assets[-1].versions[-2:]} <= {ref.artifact_id for ref in refs}


def test_index_reuses_only_same_exact_outputs_and_remains_pageable_for_large_history(tmp_path: Path) -> None:
    service, work, document, _role = replay(tmp_path)
    source = work.specialist_turns[-1]
    assert source.result is not None
    history: list[SpecialistTurnRecord] = []
    for i in range(600):
        ref = ArtifactRef(project_id=document.project.id, artifact_type="shot", artifact_id=f"shot-{i}", version=1)
        history.append(source.model_copy(update={"id": f"saved-{i}", "sequence": i + 1,
            "result": {"status": "SUCCEEDED", "artifact_refs": [ref.model_dump(mode="json")]}}))
    history.append(history[0].model_copy(update={"id": "same-output-later", "sequence": 601}))
    history.append(history[0].model_copy(update={"id": "wide-project", "sequence": 602,
        "result": {"status": "SUCCEEDED", "artifact_refs": [ArtifactRef(project_id=document.project.id, artifact_type="project", artifact_id=document.project.id).model_dump(mode="json")]}}))
    work.specialist_turns = history
    index = saved_tool_result_index(work, document.project.id)
    assert len(index) == 600
    assert 601 in {cast(dict[str, JsonValue], row)["source_sequence"] for row in index}
    assert 1 not in {cast(dict[str, JsonValue], row)["source_sequence"] for row in index}
    payload: dict[str, JsonValue] = {"goal": "Keep current goal unchanged.", "working_set": {"saved_tool_result_index": index}}
    compiler = ModelInputCompiler(service.agent_runtime.store, budget=ContextBudget(window_tokens=7000, compact_at_tokens=4500, target_tokens=2400, envelope_tokens=100))
    prepared = compiler.prepare(run_id=work.parent_run_id, project_id=document.project.id, instructions="Return a current decision.", payload=payload, max_output_tokens=1000)
    assert prepared.input_tokens < 2400 and prepared.offloaded_blocks > 0
    packed = cast(dict[str, JsonValue], json.loads(prepared.input_text))
    marker = cast(dict[str, JsonValue], cast(dict[str, JsonValue], packed["working_set"])["saved_tool_result_index"])
    reopened = AgentRuntimeStore(tmp_path / "studio.sqlite3")
    stored = reopened.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id)
    assert stored is not None and pointer_value(stored, cast(str, marker["pointer"])) == index


def test_same_version_different_qa_purposes_and_exact_construction_are_kept(tmp_path: Path) -> None:
    _service, work, document, _role = replay(tmp_path)
    first = next(turn for turn in work.specialist_turns if turn.tool_name == "qa.inspect_asset_version")
    assert first.result is not None
    different = first.model_copy(deep=True)
    different.id, different.sequence = "same-version-new-use", 11
    request = cast(dict[str, JsonValue], different.arguments["request"])
    request["review_focus"] = "Can this identity photograph serve as a specific hand-object contact first frame?"
    output = cast(dict[str, JsonValue], cast(dict[str, JsonValue], different.result)["output"])
    output.update({"recommended": False, "summary": "Identity remains useful, but this image does not show the required contact."})
    work.specialist_turns.append(different)
    # Existing cropped recipe is retained verbatim, including parent and source.
    saved = prepare_saved_completion_results(document, work, [9, 10, 10])
    inspections = [row for row in saved.receipts if row.get("observation_kind") == "image_inspection"]
    assert len(inspections) == 3
    assert any(cast(dict[str, JsonValue], row["result"])["recommended"] is False for row in inspections)
    metadata = next(row for row in saved.receipts if row.get("kind") == "saved_media_metadata" and row["asset_version_id"] == document.assets[-1].versions[-1].id)
    assert cast(dict[str, JsonValue], metadata["metadata"])["image_crop"] == document.assets[-1].versions[-1].model_dump(mode="json")["image_crop"]
    assert len([row for row in saved.receipts if row.get("kind") == "saved_tool_result"]) == 2


def test_new_batch_receipt_retains_each_actual_use_and_selected_precise_outputs(tmp_path: Path) -> None:
    _service, work, document, _role = replay(tmp_path)
    pack = document.reference_packs[-1]
    old = work.specialist_turns[-2:]
    refs = [ref for turn in old for ref in cast(list[JsonValue], cast(dict[str, JsonValue], turn.result)["artifact_refs"])]
    batch = old[-1].model_copy(update={"id": "saved-batch", "sequence": 11, "tool_name": "artifact.add_reference_entries",
        "result": {"status": "SUCCEEDED", "artifact_refs": refs, "output": {
            "pack": pack.model_dump(mode="json"), "entry_ids": [entry.id for entry in pack.entries],
            "created_entry_ids": [entry.id for entry in pack.entries], "reused_entry_ids": []}}})
    work.specialist_turns = [*work.specialist_turns[:-2], batch]
    saved = prepare_saved_completion_results(document, work, [11])
    uses = [row for row in saved.receipts if row.get("observation_kind") == "saved_reference_use"]
    assert len(uses) == 2
    assert {cast(dict[str, JsonValue], cast(dict[str, JsonValue], row["result"])["entry"])["id"] for row in uses} == {entry.id for entry in pack.entries}
    assert {ref.artifact_id for ref in saved.artifact_refs if ref.artifact_type == "asset_version"} == {v.id for v in document.assets[-1].versions[-2:]}
    assert len([row for row in saved.receipts if row.get("kind") == "saved_tool_result"]) == 1


def test_selected_parent_does_not_implicitly_select_its_crop_child(tmp_path: Path) -> None:
    service, work, document, _role = replay(tmp_path)
    service.submit_agent_work_order_result(work.id, compact_response())
    parent, child = document.assets[-1].versions[-2:]
    rows, refs = completed_work_handoffs(service.agent_runtime.store, run_id=work.parent_run_id,
        project_id=document.project.id, selected_ids={parent.id})
    assert len(rows) == 1
    assert child.id not in json.dumps(rows, ensure_ascii=False)
    assert child.id not in {ref.artifact_id for ref in refs}
    child_rows, child_refs = completed_work_handoffs(service.agent_runtime.store, run_id=work.parent_run_id,
        project_id=document.project.id, selected_ids={child.id})
    assert child.id in {ref.artifact_id for ref in child_refs}
    assert parent.id in json.dumps(child_rows)  # Actual input lineage remains available.
