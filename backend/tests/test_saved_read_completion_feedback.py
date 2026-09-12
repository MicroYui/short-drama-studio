"""Fresh6 READ/PARTIAL -> real Registry, restarted Worker and lossless feedback."""
import json
from pathlib import Path

import pytest

from short_drama.agent_runtime import AgentRuntimeStore, AgentResultStatus, ArtifactRef, WorkOrderNextOwner
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ShotPlan
from short_drama.production_manager import AgentWorkOrderResultRequest, LanguageProductionPlanner, SpecialistResultContractError
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient
from tests.test_production_manager import _start_pending_delegation

REAL = json.loads((Path(__file__).parent / "fixtures/fresh6_saved_read_partial.json").read_text())


def setup(tmp_path, outputs, *, strict=False):
    client = ScriptedClient(outputs)
    service = reopen(tmp_path, client)
    pid = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    run_id, wid, _ = _start_pending_delegation(service, pid, strict_completion_contract=strict)
    return service, client, pid, run_id, wid


def reopen(tmp_path, client):
    return ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: client)


def test_real_read_partial_survives_registry_restart_and_leader_handoff(tmp_path):
    service, client, pid, run_id, wid = setup(tmp_path, [])
    # Only the project identifier is adapted to the isolated test repository.
    actual = json.loads(json.dumps(REAL, ensure_ascii=False).replace(REAL["source"]["project_id"], pid))
    expected = actual["submitted_result"]
    client.outputs = iter([json.dumps(expected)])
    document = service.get_project(pid).document
    document.shots = [ShotPlan.model_validate(actual["successful_read_turn"]["result"]["output"]["content"]["shot"])]
    service.repository.save_project(document)
    arguments = actual["successful_read_turn"]["arguments"]
    read = service.tool_registry.invoke("artifact.read_current", arguments)
    assert read.status == ToolCallStatus.SUCCEEDED and read.artifact_refs == []
    assert read.output["content"]["shot"] == document.shots[0].model_dump(mode="json")
    work = service.agent_runtime_store.get_work_order(wid)
    objective = actual["work_order_contract"]["objective"]
    work = work.model_copy(update={"objective": objective, "context_manifest": work.context_manifest.model_copy(update={"objective": objective})})
    work.specialist_turns = [SpecialistTurnRecord(id=actual["successful_read_turn"]["id"], sequence=1,
        tool_name="artifact.read_current", arguments=arguments, reason=actual["successful_read_turn"]["reason"],
        status=SpecialistTurnStatus.COMPLETED, result=read.model_dump(mode="json"))]
    service.agent_runtime_store.save_work_order(work)
    service = reopen(tmp_path, client)
    result = service.execute_agent_work_order(wid).result
    assert result.status == AgentResultStatus.PARTIAL and result.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    for key in ("concise_summary", "deliverable", "findings", "decisions", "unresolved_questions", "recommended_next_actions"):
        assert result.model_dump(mode="json")[key] == expected[key]
    assert result.created_artifacts == result.updated_artifacts == []
    assert service.agent_runtime_store.get_work_order(wid).pending_protocol_feedback is None
    receipt = next(row for row in result.completion_receipts if row["kind"] == "saved_tool_result")
    assert receipt["observation_only"] is True and receipt["tool_effect"] == "READ"
    assert receipt["artifact_refs"] == [] and receipt["saved_result"]["pointer"] == "/result"
    assert receipt["saved_arguments"]["pointer"] == "/arguments"
    store = AgentRuntimeStore(tmp_path / "studio.sqlite3")
    snapshot = store.get_context_snapshot(receipt["saved_result"]["context_ref"], run_id)
    assert snapshot == {"arguments": arguments, "result": read.model_dump(mode="json")}
    # New pointers target ToolResult; old root pointers and original args still work.
    for marker, target in ((receipt["saved_result"], read.model_dump(mode="json")),
                           (receipt["saved_arguments"], arguments),
                           ({**receipt["saved_result"], "pointer": ""}, snapshot)):
        chunks, offset = [], 0
        while True:
            page = service.tool_registry.invoke("agent.read_context", {"project_id": pid, "run_id": run_id,
                "snapshot_id": marker["context_ref"], "pointer": marker["pointer"], "offset": offset, "limit": 12000})
            assert page.status == ToolCallStatus.SUCCEEDED, page.error
            chunks.append(page.output["content"])
            if page.output["next_offset"] is None:
                break
            offset = page.output["next_offset"]
        assert json.loads("".join(chunks)) == target
    leader = ScriptedClient([json.dumps({"summary": "Align the saved no-face scope", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(leader)
    planner.context_compiler, planner.projects = ModelInputCompiler(store), service.repository
    planner.propose_plan(context=ContextCompiler(store, ProjectArtifactResolver(service.repository)).compile(run_id), skill_prompt="", method_skills=[],
        tools=service.tool_registry.list(), current_plan=None, reason="Review preserved partial handoff")
    assert expected["concise_summary"] in json.dumps(leader.payloads[0], ensure_ascii=False)
    assert len(client.payloads) == len(leader.payloads) == 1


def test_contract_feedback_survives_another_rejection_then_clears_on_acceptance(tmp_path):
    first = {"status": "COMPLETED", "concise_summary": "Keep the no-face design; do not regenerate.",
             "deliverable": {"reason": "Assignment requested a face but saved shot excludes faces."}}
    second = {**first, "deliverable": {"reason": "Preserve this semantic finding with its source."}}
    correct = {**first, "deliverable": {"delivery": "Return the scope conflict without recreating media."}}
    service, client, _pid, _run, wid = setup(tmp_path, [json.dumps(x) for x in (first, second, correct)], strict=True)
    for expected in (first, second):
        result = service.execute_agent_work_order(wid).result
        work = service.agent_runtime_store.get_work_order(wid)
        assert result.status == AgentResultStatus.PARTIAL and work.next_owner == WorkOrderNextOwner.WORKER_DECISION
        assert work.pending_protocol_feedback is not None
        assert json.loads(work.pending_protocol_feedback.rejected_response)["deliverable"] == expected["deliverable"]
        rejected = result.deliverable["rejected_handoff"]
        assert rejected["accepted"] is False and rejected["response"]["deliverable"] == expected["deliverable"]
        assert "delivery" in json.dumps(rejected["validation_issues"])
        assert result.created_artifacts == result.updated_artifacts == result.evidence_refs == []
        service = reopen(tmp_path, client)
    accepted = service.execute_agent_work_order(wid).result
    assert accepted.status == AgentResultStatus.COMPLETED and accepted.deliverable == correct["deliverable"]
    assert service.agent_runtime_store.get_work_order(wid).pending_protocol_feedback is None
    for index, rejected in ((1, first), (2, second)):
        assert json.loads(client.payloads[index]["response_protocol_feedback"]["rejected_response"])["deliverable"] == rejected["deliverable"]
    assert service.agent_runtime_store.get_work_order(wid).specialist_turns == []


def test_equivalent_contract_rejection_keeps_original_judgment_for_leader(tmp_path):
    bad = {"status": "COMPLETED", "concise_summary": "Staging conflicts with assignment.",
           "deliverable": {"scope_conflict": "No faces in the saved shot."}}
    service, client, _pid, _run, wid = setup(tmp_path, [json.dumps(bad), json.dumps(bad)], strict=True)
    service.execute_agent_work_order(wid)
    result = service.execute_agent_work_order(wid).result
    assert result.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert result.deliverable["rejected_handoff"]["response"]["deliverable"] == bad["deliverable"]
    assert service.agent_runtime_store.get_work_order(wid).pending_protocol_feedback is not None
    assert len(client.payloads) == 2


@pytest.mark.parametrize("case", ["failed_read", "unfinished_read", "unknown_tool", "write_without_refs", "missing_sequence", "empty_completed", "foreign_ref"])
def test_saved_reads_do_not_weaken_existing_result_checks(tmp_path, case):
    service, _client, pid, _run, wid = setup(tmp_path, [], strict=True)
    work = service.agent_runtime_store.get_work_order(wid)
    turn = SpecialistTurnRecord(id="actual-result", sequence=1, tool_name="artifact.read_current",
        arguments={"project_id": pid}, reason="Read current facts", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {}, "artifact_refs": []})
    if case == "failed_read":
        turn.result["status"] = "FAILED"
    elif case == "unfinished_read":
        turn.status = SpecialistTurnStatus.REQUESTED
    elif case == "unknown_tool":
        turn.tool_name = "unknown.read"
    elif case == "write_without_refs":
        turn.tool_name = "shot.save_current"
    work.specialist_turns = [turn]
    service.agent_runtime_store.save_work_order(work)
    request = AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED if case == "empty_completed" else AgentResultStatus.PARTIAL,
        concise_summary="Read is not creative completion", saved_tool_result_sequences=[2 if case == "missing_sequence" else 1])
    if case == "foreign_ref":
        request.evidence_refs = [ArtifactRef(project_id="other", artifact_type="shot", artifact_id="foreign")]
    with pytest.raises(SpecialistResultContractError):
        service.submit_agent_work_order_result(wid, request)
    assert service.agent_runtime_store.get_agent_result(wid) is None
