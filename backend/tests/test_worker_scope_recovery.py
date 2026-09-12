"""Actual Fresh4 request replay through preflight, persistence and Worker input."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from short_drama.agent_runtime import DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrderNextOwner
from short_drama.agent_runtime.execution_control import ExecutionYielded
from short_drama.agent_runtime.models import AgentRunStatus
from short_drama.models import Asset
from short_drama.production_manager import AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioScope, StudioProjectCreate
from short_drama.studio_trace import production_trace, trace_context
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service


def _fixture() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures/fresh4_tool_scope.json").read_text())


def _pending(service: ShortDramaService, project_id: str, request: dict[str, Any]) -> tuple[str, str]:
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Use the selected saved image for this pre-video task.",
        initial_plan=AgentPlanProposal.model_validate(_plan("prop_asset_builder")),
    ))
    response = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False)
    work_id = response.created_work_order_ids[0]
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.specialist_turns = [SpecialistTurnRecord.model_validate({
        **request, "status": "REQUESTED", "result": None,
    })]
    work.status = DelegationStatus.NEEDS_ATTENTION
    work.next_owner = WorkOrderNextOwner.WORKER_TOOL
    service.agent_runtime.store.save_work_order(work)
    return run.snapshot.run.id, work_id


def test_actual_bad_project_request_is_returned_to_same_worker_and_corrected_after_restart(tmp_path: Path) -> None:
    fixture = _fixture()
    bad, corrected = fixture["turns"]
    calls = 0

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        assert "work_order" in payload, "A recoverable Tool request must not add a Leader call"
        calls += 1
        if calls == 1:
            encoded = json.dumps(payload)
            assert bad["id"] in encoded and "TOOL_SCOPE_MISMATCH" in encoded
            assert bad["arguments"]["project_id"] in encoded
            assert corrected["arguments"]["project_id"] in encoded
            assert "The Tool was not executed" in encoded
            return {key: corrected[key] for key in ("tool_name", "arguments", "reason")}
        return _done("The exact selected pixels were cropped once; no generation was needed.")

    client = HarnessLanguageClient(respond)
    service, _ = _service(tmp_path, client)
    document = service.studio.create_project(StudioProjectCreate(name="Offline scope replay")).document
    project_id = fixture["provenance"]["project_id"]
    document.project.id = project_id
    asset = Asset.model_validate(fixture["source_asset"])
    source = asset.versions[0]
    assert source.width and source.height
    source_path = tmp_path / "source.png"
    Image.new("RGB", (source.width, source.height), (170, 190, 210)).save(source_path)
    source.local_path = str(source_path)
    document.assets = [asset]
    service.repository.save_project(document)
    run_id, work_id = _pending(service, project_id, bad)
    job = StudioJob(id="offline-scope-replay", project_id=project_id, run_id=run_id, status="paused",
        objective="Offline scope replay", scope=StudioScope(), created_at="2026-09-12T00:00:00Z", updated_at="2026-09-12T00:00:00Z")
    service.repository.save_studio_job(job)
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, data: events.append((kind, data))), trace_context(
        run_id=run_id, work_order_id=work_id, agent_role="prop_asset_builder",
    ):
        response = service.production_manager.execute_work_order(work_id)
    assert response.result.next_owner == WorkOrderNextOwner.WORKER_DECISION
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    turn = work.specialist_turns[0]
    assert turn.id == bad["id"] and turn.sequence == 23 and turn.arguments == bad["arguments"]
    assert turn.status == SpecialistTurnStatus.COMPLETED
    assert turn.result is not None and turn.result["status"] == "INVALID_ARGUMENTS"
    error = turn.result["error"]
    assert isinstance(error, dict) and error["phase"] == "VALIDATION"
    assert error["details"] == [{
        "path": "/project_id", "code": "TOOL_SCOPE_MISMATCH",
        "message": "Agent Tool calls must target the current run project",
        "identifiers": {}, "constraints": {
            "expected": project_id, "received": bad["arguments"]["project_id"],
        },
    }]
    assert not turn.result.get("receipt")
    assert len(service.get_project(project_id).document.assets[0].versions) == 1
    run = service.agent_runtime.store.get_run(run_id)
    assert run is not None and run.status != AgentRunStatus.FAILED
    assert client.inputs == []
    starts = [value for kind, value in events if kind == "tool.started"]
    finishes = [value for kind, value in events if kind == "tool.finished"]
    assert len(starts) == len(finishes) == 1
    assert starts[0]["tool_call_id"] == finishes[0]["tool_call_id"]
    assert starts[0]["scope"]["project_id"] == bad["arguments"]["project_id"]
    assert starts[0]["attempt_id"] == finishes[0]["attempt_id"] == bad["id"]
    assert finishes[0]["status"] == "INVALID_ARGUMENTS" and finishes[0]["execution_started"] is False
    assert finishes[0]["error_details"] == error and finishes[0]["receipt"] is None
    activity = service.studio_runtime.activity(project_id)
    assert activity.workers[0].latest_error == error["message"]
    assert "请求已处理" in (activity.workers[0].summary or "")

    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
                                 language_provider_factory=lambda _key, _url: client)
    reopened.production_manager.execute_work_order(work_id)  # same Worker decides the correction
    work = reopened.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    request = work.specialist_turns[-1]
    assert request.sequence == 24 and request.status == SpecialistTurnStatus.REQUESTED
    assert request.arguments == corrected["arguments"] and request.reason == corrected["reason"]
    reopened.production_manager.execute_work_order(work_id)  # actual Registry + local crop
    reopened.production_manager.execute_work_order(work_id)  # completion
    work = reopened.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.status == DelegationStatus.COMPLETED
    assert len(work.specialist_turns) == 2
    assert work.specialist_turns[0].arguments == bad["arguments"]
    assert work.specialist_turns[1].id == request.id
    assert work.specialist_turns[1].result is not None and work.specialist_turns[1].result["status"] == "SUCCEEDED"
    versions = reopened.get_project(project_id).document.assets[0].versions
    assert len(versions) == 2 and versions[-1].image_crop is not None
    assert versions[-1].image_crop.source_asset_version_id == source.id
    assert calls == 2 and all("work_order" in value for value in client.inputs)


@pytest.mark.parametrize("tool_name,arguments", [
    ("project.get", {"project_id": "different-project"}),
    ("agent.read_context", {"project_id": "project-dont-trust-me", "run_id": "different-run", "snapshot_id": "unknown"}),
    ("agent.get_tool_attempt", {"attempt_ref": {"work_order_id": "not-handed"}}),
    ("image.crop", {"project_id": "project-dont-trust-me", "asset_id": "missing", "request": {}}),
])
def test_scope_and_schema_refusals_remain_failed_observations_without_executing(tmp_path: Path, tool_name: str,
                                                                             arguments: dict[str, Any]) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _value: _done()))
    before = service.get_project(project_id).document.model_dump(mode="json")
    run_id, work_id = _pending(service, project_id, {"id": "bad-request", "sequence": 1,
        "tool_name": tool_name, "arguments": arguments, "reason": "Offline invalid request."})
    response = service.production_manager.execute_work_order(work_id)
    assert response.result.next_owner == WorkOrderNextOwner.WORKER_DECISION
    current = service.agent_runtime.store.get_work_order(work_id)
    assert current is not None and current.specialist_turns[0].result is not None
    assert current.specialist_turns[0].result["status"] == "INVALID_ARGUMENTS"
    assert current.specialist_turns[0].arguments == arguments
    assert service.get_project(project_id).document.model_dump(mode="json") == before
    run = service.agent_runtime.store.get_run(run_id)
    assert run is not None and run.status != AgentRunStatus.FAILED


@pytest.mark.parametrize("status", [AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED])
@pytest.mark.parametrize("pending_tool", [True, False])
def test_terminal_run_cannot_start_worker_decision_or_pending_tool(tmp_path: Path, status: AgentRunStatus,
                                                                 pending_tool: bool) -> None:
    client = HarnessLanguageClient(lambda _value: _done())
    service, project_id = _service(tmp_path, client)
    run_id, work_id = _pending(service, project_id, {"id": "still-pending", "sequence": 1,
        "tool_name": "project.get", "arguments": {"project_id": project_id}, "reason": "Read only if running."})
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    if not pending_tool:
        work.specialist_turns = []
        work.next_owner = WorkOrderNextOwner.WORKER_DECISION
        service.agent_runtime.store.save_work_order(work)
    before = deepcopy(work.model_dump(mode="json"))
    service.agent_runtime.set_run_status(run_id, status)
    with pytest.raises(ExecutionYielded, match=status.value):
        service.production_manager.execute_work_order(work_id)
    current = service.agent_runtime.store.get_work_order(work_id)
    assert current is not None and current.model_dump(mode="json") == before
    assert client.inputs == []
