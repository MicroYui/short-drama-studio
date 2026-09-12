"""Stop new work, while allowing real synchronous in-flight operations to land."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import DelegationStatus, SpecialistTurnRecord, WorkOrderNextOwner
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary, execution_control
from short_drama.agent_runtime.models import AgentRunStatus
from short_drama.agent_runtime.model_input import json_text
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioJobControl, StudioMessage, StudioScope
from short_drama.studio_runtime import StudioAgentRunner
from short_drama.team_executor import LocalTeamExecutor, TeamWorkAdvanceStatus
from short_drama.tools import ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolRegistry, ToolResult, ToolSpec
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job
from tests.test_team_executor import DrainingManager, _registry, _runtime, _work_order
from tests.test_failed_plan_continuation import _failed_plan


def _until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline, "Expected persisted boundary did not arrive"
        threading.Event().wait(0.005)


class BrokenManager(DrainingManager):
    def execute_work_order(self, work_order_id: str) -> object:
        self.calls.append(work_order_id)
        raise RuntimeError("Storage failed before execution")


def test_fatal_collection_does_not_reclaim_failed_worker_or_start_ready_peer(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    first = _work_order(runtime, run_id, "first")
    second = _work_order(runtime, run_id, "second")
    manager = BrokenManager(runtime, _registry())
    executor = LocalTeamExecutor(manager, max_workers=1)
    batch = executor.collect_ready_work(run_id)
    assert batch.selected_work_order_ids == [first]
    _until(lambda: bool(executor._background_futures[first][1].done()))
    batch = executor.collect_ready_work(run_id)
    assert batch.selected_work_order_ids == [] and batch.active_work_order_ids == []
    assert [item.status for item in batch.advances] == [TeamWorkAdvanceStatus.FAILED]
    assert manager.calls == [first] and second not in manager.calls
    executor.shutdown()


class BoundaryInput(ToolModel):
    project_id: str
    name: str


class BoundaryOutput(ToolModel):
    saved: bool = True


class FaultRegistry(ToolRegistry):
    """An infrastructure exception outside the normal Tool handler contract."""

    def __init__(self, slow_started: threading.Event, release_fault: threading.Event) -> None:
        super().__init__()
        self.slow_started = slow_started
        self.release_fault = release_fault
        self.calls: list[str] = []

    def invoke(self, tool_id: str, arguments: Mapping[str, object]) -> ToolResult:
        if tool_id == "image.boundary":
            self.calls.append(str(arguments["name"]))
            if arguments["name"] == "fatal":
                assert self.slow_started.wait(10)
                assert self.release_fault.wait(10)
                raise RuntimeError("Registry storage unexpectedly unavailable")
        return super().invoke(tool_id, arguments)


def _jobs(service: ShortDramaService, project_id: str, names: list[str]) -> tuple[StudioJob, dict[str, str]]:
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Finish these persisted operations, with no video submission.",
        initial_plan=AgentPlanProposal.model_validate(_plan(*names)),
    ))
    created = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest(max_actions=len(names)), advance_workers=False)
    ids: dict[str, str] = {}
    for work_id in created.created_work_order_ids:
        work = service.agent_runtime.store.get_work_order(work_id)
        assert work is not None
        ids[work.recipient_role] = work.id
        work.specialist_turns = [SpecialistTurnRecord(
            id=f"pending-{work.recipient_role}", sequence=1, tool_name="image.boundary",
            reason="Finish exactly this saved operation.", arguments={"project_id": project_id, "name": work.recipient_role},
        )]
        work.status = DelegationStatus.NEEDS_ATTENTION
        work.next_owner = WorkOrderNextOwner.WORKER_TOOL
        service.agent_runtime.store.save_work_order(work)
    job = StudioJob(id="boundary-job", project_id=project_id, run_id=run.snapshot.run.id,
        objective=run.snapshot.run.objective, scope=StudioScope(), status="paused",
        created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    return job, ids


def _register(registry: ToolRegistry, handler: Callable[[BoundaryInput], ToolExecution[BoundaryOutput]]) -> None:
    registry.register(ToolSpec(id="image.boundary", version=1, category=ToolCategory.IMAGE,
        description="Perform one synchronous offline media write.", effect=ToolEffect.EXTERNAL,
        idempotent=False, input_model=BoundaryInput, output_model=BoundaryOutput, handler=handler))


@pytest.mark.parametrize("explicit_action", [None, "resume", "message"])
def test_real_fatal_stops_job_and_run_but_late_receipt_lands_without_next_call(tmp_path: Path,
                                                                          explicit_action: str | None) -> None:
    slow_started, release_slow, release_fault = threading.Event(), threading.Event(), threading.Event()
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        assert "work_order" not in payload, "Stopped Worker must not continue after an explicit restart was queued"
        assert explicit_action is not None and landed.exists()
        return _plan()

    client = HarnessLanguageClient(respond)
    registry = FaultRegistry(slow_started, release_fault)
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        tool_registry=registry, language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    service.team_executor = LocalTeamExecutor(service.production_manager, max_workers=2)
    landed = tmp_path / "landed-media.txt"

    def media(request: BoundaryInput) -> ToolExecution[BoundaryOutput]:
        assert request.name == "slow", "Waiting peer must not start after fatal collection"
        slow_started.set()
        assert release_slow.wait(10)
        landed.write_text("one completed operation")
        return ToolExecution(output=BoundaryOutput())

    _register(registry, media)
    job, ids = _jobs(service, project_id, ["fatal", "slow", "queued-peer"])
    assert job.run_id is not None
    service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
    try:
        assert slow_started.wait(10)
        release_fault.set()
        _until(lambda: service.studio_runtime._job(job.id).status == "failed")
        _until(lambda: service.agent_runtime.snapshot(job.run_id or "").run.status == AgentRunStatus.FAILED)
        assert service.studio_runtime.threads[job.id].is_alive(), "Runner must drain its started operation"
        assert not landed.exists()
        if explicit_action == "resume":
            service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
        elif explicit_action == "message":
            service.studio_runtime.send(project_id, StudioMessage(text="只保留已落盘内容，现在结束此范围。"))
        if explicit_action is not None:
            assert service.studio_runtime._job(job.id).status == "queued"
        assert client.inputs == [], "Explicit continuation waits until prior in-flight work lands"
    finally:
        release_fault.set()
        release_slow.set()
    final = _wait_job(service, job.id, "failed" if explicit_action is None else "completed")
    if explicit_action is None:
        assert "Registry storage unexpectedly unavailable" in (final.error or "")
    assert landed.read_text() == "one completed operation"
    assert service.agent_runtime.snapshot(job.run_id).run.status == (
        AgentRunStatus.FAILED if explicit_action is None else AgentRunStatus.COMPLETED
    )
    assert registry.calls == ["fatal", "slow"] or registry.calls == ["slow", "fatal"]
    assert len(client.inputs) == (0 if explicit_action is None else 1)
    if explicit_action == "message":
        assert "只保留已落盘内容" in json_text(client.inputs[0]["goal"]["latest_user_instructions"])
        assert not final.pending_messages
    for name, work_id in ids.items():
        work = service.agent_runtime.store.get_work_order(work_id)
        assert work is not None and len(work.specialist_turns) == 1
        turn = work.specialist_turns[0]
        assert turn.id == f"pending-{name}"
        if name == "queued-peer":
            assert turn.status.value == "REQUESTED" and turn.result is None
        else:
            assert turn.result is not None
            assert turn.result["status"] == ("FAILED" if name == "fatal" else "SUCCEEDED")
    assert not service.team_executor.has_active_work(job.run_id)
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
                                  language_provider_factory=lambda _key, _url: client)
    restarted.studio_runtime.resume_after_restart()
    assert job.id not in restarted.studio_runtime.threads
    assert len(client.inputs) == (0 if explicit_action is None else 1)
    assert restarted.agent_runtime.snapshot(job.run_id).run.status == (
        AgentRunStatus.FAILED if explicit_action is None else AgentRunStatus.COMPLETED
    )


@pytest.mark.parametrize("status", ["paused", "failed", "completed", "waiting_user"])
def test_job_boundary_blocks_next_operation_and_inactive_execute_never_reopens(tmp_path: Path, status: str) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _register(service.tool_registry, lambda _request: ToolExecution(output=BoundaryOutput()))
    job, ids = _jobs(service, project_id, ["pending"])
    stopped = job.model_copy(update={"status": status})
    service.repository.save_studio_job(stopped)
    with execution_control(lambda: service.studio_runtime._yield_reason(job.id)):
        with pytest.raises(ExecutionYielded, match=status):
            service.production_manager.execute_work_order(ids["pending"])
        service.studio_runtime._execute(job.id)
    assert service.studio_runtime._job(job.id).status == status
    work = service.agent_runtime.store.get_work_order(ids["pending"])
    assert work is not None and work.specialist_turns[0].status.value == "REQUESTED"
    assert client.inputs == []


@pytest.mark.parametrize("terminal", [AgentRunStatus.FAILED, AgentRunStatus.COMPLETED, AgentRunStatus.CANCELLED])
def test_model_already_in_flight_may_complete_without_reopening_terminal_run(tmp_path: Path, terminal: AgentRunStatus) -> None:
    started, release = threading.Event(), threading.Event()

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        assert "work_order" in payload
        started.set()
        assert release.wait(10)
        return _done("Already-started decision returned normally.")

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job, ids = _jobs(service, project_id, ["inference"])
    assert job.run_id is not None
    work = service.agent_runtime.store.get_work_order(ids["inference"])
    assert work is not None
    work.specialist_turns = []
    work.next_owner = WorkOrderNextOwner.WORKER_DECISION
    service.agent_runtime.store.save_work_order(work)
    future = service.team_executor.collect_ready_work(job.run_id)
    assert future.selected_work_order_ids == [work.id]
    try:
        assert started.wait(10)
        with service.agent_runtime.run_lock(job.run_id):
            service.agent_runtime.set_run_status(job.run_id, terminal)
    finally:
        release.set()
    _until(lambda: not service.team_executor.collect_finished_work(job.run_id or "").active_work_order_ids)
    result = service.agent_runtime.store.get_agent_result(work.id)
    assert result is not None and result.status.value == "COMPLETED"
    assert service.agent_runtime.snapshot(job.run_id).run.status == terminal


def test_message_arriving_after_loop_check_drains_old_leader_before_claiming_input(tmp_path: Path) -> None:
    started, release, old_yielded, new_started = (threading.Event() for _ in range(4))

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" in payload:
            return _done("Existing saved work is complete.")
        if payload.get("goal", {}).get("latest_user_instructions"):
            assert old_yielded.is_set(), "New message planning must follow collection of the old decision"
            assert "Late user message" in json_text(payload["goal"]["latest_user_instructions"])
            new_started.set()
            return _plan()
        started.set()
        assert release.wait(10)
        try:
            execution_boundary()
        except ExecutionYielded:
            old_yielded.set()
            raise
        pytest.fail("Old Leader was incorrectly exempted by the new message's processing scope")

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    run_id, _work_id, _failed_id = _failed_plan(service, project_id)

    class LateMessageRunner(StudioAgentRunner):
        injected = False

        def _apply_messages(self, job_id: str, run_id: str) -> None:
            if started.is_set() and not self.injected:
                # The loop's preceding yield check saw no message. Deliver it
                # exactly before message consumption, with the old call blocked.
                self.injected = True
                self.send(project_id, StudioMessage(text="Late user message: stop adding work and deliver saved results."))
            try:
                super()._apply_messages(job_id, run_id)
            finally:
                if self.injected:
                    release.set()

    runner = LateMessageRunner(service)
    service.studio_runtime = runner
    job = StudioJob(id="late-message-boundary", project_id=project_id, run_id=run_id, status="paused",
        objective="Finish pre-video work only.", scope=StudioScope(),
        created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    runner.control(job.id, StudioJobControl(action="resume"))
    try:
        final = _wait_job(service, job.id, "completed")
    finally:
        release.set()
    assert runner.injected and old_yielded.is_set() and new_started.is_set()
    assert not final.pending_messages and job.id not in runner._completion_planning
