"""One Worker decision, independent durable reads, and one informed continuation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from short_drama.agent_runtime import (
    AgentRunStatus, ArtifactRef, DelegationStatus, SpecialistTurnStatus, WorkOrderNextOwner,
)
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.agent_runtime.media_context import provider_media_model_view
from short_drama.models import DomainModel, EnqueueRequest, Take, VideoInspectionReport
from short_drama.production_manager import ProductionManagerError
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace, trace_context
from short_drama.team_executor import LocalTeamExecutor
from short_drama.tools import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_studio_harness import HarnessLanguageClient, _done, _service
from tests.test_worker_recovery_ownership import _delegate, _reopen


class ReadInput(DomainModel):
    project_id: str
    key: str


class ReadOutput(DomainModel):
    key: str
    fact: str


def register_read(
    service: ShortDramaService, calls: list[str], *,
    effect: ToolEffect = ToolEffect.READ, idempotent: bool = True,
    before: Callable[[str], None] | None = None,
) -> None:
    def read(request: ReadInput) -> ToolExecution[ReadOutput]:
        calls.append(request.key)
        if before is not None:
            before(request.key)
        if request.key == "failed":
            raise ValueError("The selected saved evidence is unavailable")
        return ToolExecution(
            output=ReadOutput(key=request.key, fact=f"Exact independent fact: {request.key}"),
            artifact_refs=(ArtifactRef(project_id=request.project_id,
                                       artifact_type="project", artifact_id=request.project_id),),
        )

    service.production_manager.tools.register(ToolSpec(
        id="project.batch_read_probe", version=1, category=ToolCategory.PROJECT,
        description="Read one selected independent saved fact.", effect=effect,
        idempotent=idempotent, input_model=ReadInput, output_model=ReadOutput, handler=read,
    ))


def batch(project_id: str, keys: tuple[str, ...] = ("first", "second", "third")) -> dict[str, Any]:
    return {
        "read_requests": [{"tool_name": "project.batch_read_probe", "reason": f"Read {key} independently.",
                           "arguments": {"project_id": project_id, "key": key}} for key in keys],
        "independence_reason": "All selectors are already known; none uses another result.",
        "working_notes": "Combine the actual selected facts, retaining any unknown result.",
    }


def start(
    tmp_path: Path, *, keys: tuple[str, ...] = ("first", "second", "third"),
    before: Callable[[str], None] | None = None,
) -> tuple[ShortDramaService, HarnessLanguageClient, list[str], str, str, str]:
    client = HarnessLanguageClient(lambda payload: batch(payload["context"]["project_id"], keys)
                                  if len(client.inputs) == 1 else _done("Combined the independent read results."))
    service, project_id = _service(tmp_path, client)
    calls: list[str] = []
    register_read(service, calls, before=before)
    run_id, work_id, _ = _delegate(service, project_id)
    return service, client, calls, project_id, run_id, work_id


def test_three_reads_use_one_decision_keep_scopes_refs_and_actual_trace(tmp_path: Path) -> None:
    service, client, calls, project_id, run_id, work_id = start(tmp_path)
    before = service.get_project(project_id).document.model_dump(mode="json")
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, value: events.append((kind, value))), trace_context(
        run_id=run_id, work_order_id=work_id,
    ):
        advance = LocalTeamExecutor(service.production_manager).drain_ready_work_until_handoff(run_id)
    assert advance.advances[0].advance_count == 5  # Decide, three reads, then decide from all results.
    assert calls == ["first", "second", "third"] and len(client.inputs) == 2
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.status == DelegationStatus.COMPLETED
    assert [turn.sequence for turn in work.specialist_turns] == [1, 2, 3]
    assert len({turn.batch_id for turn in work.specialist_turns}) == 1
    assert work.specialist_turns[0].batch_id is not None
    assert all(turn.status == SpecialistTurnStatus.COMPLETED for turn in work.specialist_turns)
    view = client.inputs[-1]["working_set"]
    assert [item["request"]["arguments"]["key"] for item in view["latest_read_batch"]["items"]] == calls
    assert all(item["artifact_refs"] for item in view["latest_read_batch"]["items"])
    bodies = [item["result"]["output"] for item in view["read_observations"]
              if item["tool_name"] == "project.batch_read_probe"]
    bodies.append(view["latest_handoff"]["result"]["output"])
    assert {item["fact"] for item in bodies} == {f"Exact independent fact: {key}" for key in calls}
    starts = [value for kind, value in events if kind == "tool.started"]
    finishes = [value for kind, value in events if kind == "tool.finished"]
    assert len(starts) == len(finishes) == 3
    assert {item["tool_call_id"] for item in starts} == {item["tool_call_id"] for item in finishes}
    assert [item["attempt_id"] for item in starts] == [turn.id for turn in work.specialist_turns]
    assert all(item["batch_id"] == work.specialist_turns[0].batch_id for item in finishes)
    assert all(item["tool_version"] == 1 for item in starts)
    assert service.get_project(project_id).document.model_dump(mode="json") == before
    assert "read_batch_request" in client.inputs[0]["response_schemas"]
    assert next(item for item in client.inputs[0]["available_tools"]
                if item["id"] == "project.batch_read_probe")["idempotent"] is True


def test_early_failed_read_remains_visible_after_later_success(tmp_path: Path) -> None:
    service, client, calls, _project_id, run_id, _work_id = start(tmp_path, keys=("failed", "second", "third"))
    LocalTeamExecutor(service.production_manager).drain_ready_work_until_handoff(run_id)
    assert calls == ["failed", "second", "third"] and len(client.inputs) == 2
    items = client.inputs[-1]["working_set"]["latest_read_batch"]["items"]
    assert [item["outcome_status"] for item in items] == ["FAILED", "SUCCEEDED", "SUCCEEDED"]
    assert items[0]["error"]["message"] == "The selected saved evidence is unavailable"
    assert items[0]["attempt_ref"]["has_result"] is True


def test_same_take_distinct_large_inspection_reads_survive_batch_and_reopen(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if len(client.inputs) == 1:
            return {"read_requests": [
                {"tool_name": "video.get_take_inspection", "reason": f"Read exact {report_id} scope.",
                 "arguments": {"take_id": "batch-take", "report_id": report_id}}
                for report_id in ("wide-report", "tail-report")],
                "independence_reason": "Both saved report IDs are known; their source windows differ."}
        if len(client.inputs) == 2:
            return {"tool_name": "project.get", "reason": "Continue after comparing both saved observations.",
                    "arguments": {"project_id": payload["context"]["project_id"]}}
        return _done("The exact observations were available together; audio remains unknown.")

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    shot_id = service.get_project(project_id).document.shots[0].id
    queued = service.enqueue(project_id, EnqueueRequest(shot_ids=[shot_id], candidate_count=1))
    take = Take.model_validate({
        "id": "batch-take", "project_id": project_id, "shot_id": shot_id, "task_id": queued.tasks[0].id,
        "candidate_index": 0, "operation": "GENERATE", "media_path": str(tmp_path / "saved.mp4"),
        "thumbnail_path": str(tmp_path / "saved.png"), "duration_seconds": 10, "width": 640, "height": 360,
        "availability": "AVAILABLE", "qa_status": "PENDING", "is_selected": False, "freshness": "CURRENT",
        "qa_report": {"id": "saved-qa", "take_id": "batch-take", "status": "PENDING",
                      "hard_gate_passed": False, "score": 0, "issues": [], "checks": [],
                      "created_at": "2026-09-17T00:00:00Z"},
        "created_at": "2026-09-17T00:00:00Z",
    })
    service.repository.save_take(take)
    reports = [VideoInspectionReport.model_validate({
        "id": report_id, "project_id": project_id, "shot_id": shot_id, "take_id": take.id,
        "review_focus": focus, "observed_source_in_seconds": start, "observed_source_out_seconds": end,
        "source_context": {"selected_observation_facts": facts},
        "scopes": ["INTRA_SHOT"], "duration_seconds": 10, "width": 640, "height": 360,
        "fps": 24, "audio_present": True,
        "samples": [{"source_take_id": take.id, "timestamp_seconds": start,
                     "image_path": str(tmp_path / "saved.png"), "reasons": ["SAVED_OBSERVATION"]}],
        "contact_sheet_path": str(tmp_path / "saved-sheet.jpg"), "technical_checks": [],
        "semantic_status": "COMPLETE", "summary": focus, "created_at": "2026-09-17T00:00:00Z",
    }) for report_id, focus, start, end, facts in (
        ("wide-report", "Earlier whole action", 0, 10,
         "\n".join(f"Whole-action selected fact {index}: original distinct observation." for index in range(1800))),
        ("tail-report", "Later tail detail", 8, 10, "The tail observation is a separate report, not a replacement."),
    )]
    for report in reports:
        service.repository.save_video_inspection(report)
    expected_reports = [provider_media_model_view(report.model_dump(mode="json")) for report in reports]
    _run_id, work_id, _ = _delegate(service, project_id)
    for _ in range(3):  # One batch decision and both real Registry reads; no sampling/Provider.
        service.execute_agent_work_order(work_id)
    before = service.agent_runtime.store.get_work_order(work_id)
    assert before is not None
    service = _reopen(service, client)
    service.execute_agent_work_order(work_id)
    view = client.inputs[-1]["working_set"]
    wide = next(item for item in view["read_observations"]
                if item["request_identifiers"].get("report_id") == reports[0].id)
    assert wide["result"]["output"]["report"] == expected_reports[0]
    assert view["latest_handoff"]["result"]["output"]["report"] == expected_reports[1]
    assert [item["request"]["arguments"]["report_id"] for item in view["latest_read_batch"]["items"]] == [
        report.id for report in reports]
    assert [{ref["artifact_id"] for ref in item["artifact_refs"]} for item in view["latest_read_batch"]["items"]] == [
        {take.id, report.id} for report in reports]
    assert service.agent_runtime.store.get_context_snapshot(f"context-read-{work_id}-1", before.parent_run_id) is None
    # Once another Tool has answered, older large reads use the existing exact
    # context snapshot policy again; batch does not make all history permanent inline input.
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    assert client.inputs[-1]["working_set"]["latest_read_batch"] is None
    snapshot = service.agent_runtime.store.get_context_snapshot(f"context-read-{work_id}-1", before.parent_run_id)
    assert snapshot is not None and snapshot["output"]["report"] == expected_reports[0]
    current = service.agent_runtime.store.get_work_order(work_id)
    assert current is not None and current.specialist_turns[:2] == before.specialist_turns
    assert service.repository.get_video_inspection(reports[0].id) == reports[0]


@pytest.mark.parametrize("effect,idempotent", [
    (ToolEffect.WRITE, True), (ToolEffect.EXTERNAL, True), (ToolEffect.READ, False),
])
def test_entire_declaration_rejects_ineligible_child_before_any_execution(
    tmp_path: Path, effect: ToolEffect, idempotent: bool,
) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        value = batch(payload["context"]["project_id"], ("first", "second"))
        value["read_requests"][0] = {"tool_name": "project.get", "reason": "Known read",
                                     "arguments": {"project_id": payload["context"]["project_id"]}}
        return value

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    calls: list[str] = []
    register_read(service, calls, effect=effect, idempotent=idempotent)
    _run_id, work_id, _ = _delegate(service, project_id)
    result = service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert result.result.next_owner == WorkOrderNextOwner.WORKER_DECISION
    assert work is not None and work.specialist_turns == [] and calls == []
    assert work.pending_protocol_feedback is not None
    error = str(work.pending_protocol_feedback.validation_issues["read_batch_request"])
    assert "/read_requests/1/tool_name" in error and "not queued or executed" in error


@pytest.mark.parametrize("keys", [("one",), ("one", "two", "three", "four", "five")])
def test_batch_cardinality_is_validated_before_queueing(tmp_path: Path, keys: tuple[str, ...]) -> None:
    service, _client, calls, _project_id, _run_id, work_id = start(tmp_path, keys=keys)
    service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.specialist_turns == [] and calls == []
    assert work.pending_protocol_feedback is not None
    assert "read_batch_request" in work.pending_protocol_feedback.validation_issues


def test_empty_child_reason_is_rejected_before_any_child_is_queued(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        value = batch(payload["context"]["project_id"], ("valid", "invalid"))
        value["read_requests"][1]["reason"] = ""
        return value

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    calls: list[str] = []
    register_read(service, calls)
    _run_id, work_id, _ = _delegate(service, project_id)
    service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.specialist_turns == [] and calls == []
    assert work.pending_protocol_feedback is not None
    assert "/read_requests/1/reason" in str(work.pending_protocol_feedback.validation_issues)


def test_scope_failure_is_individual_and_never_invokes_wrong_project(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if len(client.inputs) > 1:
            return _done("The unavailable scope and valid read are both visible.")
        value = batch(payload["context"]["project_id"], ("wrong", "valid"))
        value["read_requests"][0]["arguments"]["project_id"] = "different-project"
        return value

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    calls: list[str] = []
    register_read(service, calls)
    run_id, _work_id, _ = _delegate(service, project_id)
    LocalTeamExecutor(service.production_manager).drain_ready_work_until_handoff(run_id)
    assert calls == ["valid"]
    items = client.inputs[-1]["working_set"]["latest_read_batch"]["items"]
    assert items[0]["outcome_status"] == "INVALID_ARGUMENTS"
    assert items[0]["request"]["arguments"]["project_id"] == "different-project"
    assert items[1]["outcome_status"] == "SUCCEEDED"


def test_pause_between_children_and_reopen_resume_pending_without_model_or_repeat(tmp_path: Path) -> None:
    service, client, calls, _project_id, _run_id, work_id = start(tmp_path)
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    saved = service.agent_runtime.store.get_work_order(work_id)
    assert saved is not None and saved.next_owner == WorkOrderNextOwner.WORKER_TOOL
    with execution_control(lambda: "User pause"):
        with pytest.raises(ExecutionYielded, match="User pause"):
            service.execute_agent_work_order(work_id)
    assert calls == ["first"] and len(client.inputs) == 1
    reopened = _reopen(service, client)
    register_read(reopened, calls)
    for _ in range(3):
        reopened.execute_agent_work_order(work_id)
    assert calls == ["first", "second", "third"] and len(client.inputs) == 2
    current = reopened.agent_runtime.store.get_work_order(work_id)
    assert current is not None and current.specialist_turns[0] == saved.specialist_turns[0]


def test_interrupted_child_is_not_replayed_remaining_reads_and_unknown_survive_restart(tmp_path: Path) -> None:
    class ProcessStopped(BaseException):
        pass

    def stop_second(key: str) -> None:
        if key == "second":
            raise ProcessStopped()

    service, client, calls, _project_id, _run_id, work_id = start(tmp_path, before=stop_second)
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    with pytest.raises(ProcessStopped):
        service.execute_agent_work_order(work_id)
    saved = service.agent_runtime.store.get_work_order(work_id)
    assert saved is not None and saved.specialist_turns[1].status == SpecialistTurnStatus.IN_PROGRESS
    reopened = _reopen(service, client)
    register_read(reopened, calls)
    reopened.execute_agent_work_order(work_id)
    reopened.execute_agent_work_order(work_id)
    assert calls == ["first", "second", "third"] and len(client.inputs) == 2
    items = client.inputs[-1]["working_set"]["latest_read_batch"]["items"]
    assert [item["turn_status"] for item in items] == ["COMPLETED", "INTERRUPTED", "COMPLETED"]
    assert items[1]["error"]["kind"] == "ToolExecutionInterrupted"


@pytest.mark.parametrize("change", ["cancel", "supersede"])
def test_cancel_or_superseded_scope_stops_remaining_children(tmp_path: Path, change: str) -> None:
    service, client, calls, _project_id, run_id, work_id = start(tmp_path)
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    if change == "cancel":
        service.agent_runtime.set_run_status(run_id, AgentRunStatus.CANCELLED)
    else:
        work = service.agent_runtime.store.get_work_order(work_id)
        assert work is not None
        work.active_plan_revision_id = "replaced-plan"
        service.agent_runtime.store.save_work_order(work)
    with pytest.raises((ExecutionYielded, ProductionManagerError)):
        service.execute_agent_work_order(work_id)
    assert calls == ["first"] and len(client.inputs) == 1
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and all(turn.status == SpecialistTurnStatus.REQUESTED for turn in work.specialist_turns[1:])
