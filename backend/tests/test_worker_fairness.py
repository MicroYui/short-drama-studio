"""Fair background turns over real persistence, synchronous Tools and Runner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import (
    AgentResultStatus, AgentRuntime, AgentRuntimeStore, ContextCompiler, DelegationStatus,
    SpecialistTurnRecord, SpecialistTurnStatus, WorkOrderNextOwner,
)
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.agent_runtime.models import utc_now
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest,
    AgentWorkOrderResultRequest, AgentWorkOrderView, ProductionManager,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.studio_models import StudioJob, StudioJobControl, StudioScope
from short_drama.team_executor import LocalTeamExecutor, TeamExecutionBatch, TeamWorkAdvanceStatus
from short_drama.tools import ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolSpec
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job
from tests.test_team_executor import (
    DrainingManager, EmptyArtifactResolver, FixtureProjectStore, LifecycleAdvanceInput,
    LifecycleEnqueueInput, LifecycleOutput, LifecycleTask, _registry, _runtime, _work_order,
)


def _await_batch(collect: Callable[[], TeamExecutionBatch], *, timeout: float = 5) -> TeamExecutionBatch:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        batch = collect()
        if batch.advances:
            assert all(advance.status != TeamWorkAdvanceStatus.FAILED for advance in batch.advances), batch
            return batch
        threading.Event().wait(0.005)
    pytest.fail("No persisted Worker boundary was collected")


class FairMediaInput(ToolModel):
    project_id: str
    worker: str
    round: int


class FairMediaOutput(ToolModel):
    saved: bool = True


def test_four_long_workers_release_persisted_slots_for_fifth_without_leader_call(tmp_path: Path) -> None:
    first_four_started, release_media, short_started = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak_active = 0
    calls: list[tuple[str, int]] = []
    decisions: dict[str, int] = {}

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        assert "work_order" in payload, "Fair yielding must not request a Leader plan"
        role = str(payload["work_order"]["recipient_role"])
        if role == "short-evidence":
            short_started.set()
            return _done("The waiting short work received a slot.")
        # Before the fix all four long Workers retain their slots here, so the
        # fifth cannot enter. This is an actual synchronous execution boundary.
        assert short_started.wait(8), "Long Worker drains starved ready short work"
        with lock:
            decisions[role] = decisions.get(role, 0) + 1
            decision = decisions[role]
        if decision <= 2:
            return {"tool_name": "image.fairness_media", "reason": "Continue the current saved task.",
                    "arguments": {"project_id": project_id, "worker": role, "round": decision + 1}}
        return _done("All local fake media rounds finished.")

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)

    def media(request: FairMediaInput) -> ToolExecution[FairMediaOutput]:
        nonlocal active, peak_active
        with lock:
            calls.append((request.worker, request.round))
            active += 1
            peak_active = max(peak_active, active)
            if len(calls) == 4:
                first_four_started.set()
        try:
            if request.round == 1:
                assert release_media.wait(8)
            return ToolExecution(output=FairMediaOutput())
        finally:
            with lock:
                active -= 1

    service.tool_registry.register(ToolSpec(
        id="image.fairness_media", version=1, category=ToolCategory.IMAGE,
        description="Perform one synchronous fake media operation.", effect=ToolEffect.EXTERNAL,
        idempotent=False, input_model=FairMediaInput, output_model=FairMediaOutput, handler=media,
    ))
    names = [*(f"long-{index}" for index in range(4)), "short-evidence"]
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Finish these five already planned pre-video jobs.",
        initial_plan=AgentPlanProposal.model_validate(_plan(*names)),
    ))
    run_id = run.snapshot.run.id
    created = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=5), advance_workers=False)
    assert len(created.created_work_order_ids) == 5
    pending_ids: dict[str, str] = {}
    for work_id in created.created_work_order_ids:
        work = service.agent_runtime.store.get_work_order(work_id)
        assert work is not None
        if work.recipient_role == "short-evidence":
            continue
        turn_id = f"saved-media-{work.recipient_role}"
        pending_ids[work_id] = turn_id
        work.specialist_turns.append(SpecialistTurnRecord(
            id=turn_id, sequence=1, tool_name="image.fairness_media", reason="Resume the saved media operation.",
            arguments={"project_id": project_id, "worker": work.recipient_role, "round": 1},
        ))
        work.status = DelegationStatus.NEEDS_ATTENTION
        work.next_owner = WorkOrderNextOwner.WORKER_TOOL
        service.agent_runtime.store.save_work_order(work)
    job = StudioJob(id="fair-five-workers", project_id=project_id, run_id=run_id,
                    objective=run.snapshot.run.objective, scope=StudioScope(), status="paused",
                    created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
    try:
        assert first_four_started.wait(8)
        assert not short_started.is_set(), "A fifth operation exceeded the four occupied slots"
        assert active == peak_active == 4
    finally:
        release_media.set()
    _wait_job(service, job.id, "completed", timeout=20)
    assert short_started.is_set() and peak_active == 4
    assert sorted(calls) == sorted((name, round_) for name in names[:4] for round_ in (1, 2, 3))
    assert len(client.inputs) == 13 and all("work_order" in payload for payload in client.inputs)
    for work in service.agent_runtime.store.list_work_orders(run_id):
        assert work.status == DelegationStatus.COMPLETED
        if work.id in pending_ids:
            assert work.specialist_turns[0].id == pending_ids[work.id]
            assert len(work.specialist_turns) == 3
            assert all(turn.status == SpecialistTurnStatus.COMPLETED for turn in work.specialist_turns)
    assert not service.get_project(project_id).tasks


class TerminalSpecialist:
    def execute(self, view: AgentWorkOrderView) -> AgentWorkOrderResultRequest:
        if view.work_order.specialist_turns:
            latest = view.work_order.specialist_turns[-1]
            assert latest.result is not None
            output = latest.result["output"]
            assert isinstance(output, dict)
            task = output["task"]
            assert isinstance(task, dict) and task["status"] == "SUCCEEDED"
        return AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
                                           concise_summary="The saved media lifecycle completed.")


def test_yielded_media_continuation_keeps_same_request_across_restart(tmp_path: Path) -> None:
    projects = FixtureProjectStore()
    project_id = projects.document.project.id
    registry = _registry()
    enqueue_calls: list[str] = []
    advance_calls: list[str] = []

    def output(status: str) -> ToolExecution[LifecycleOutput]:
        return ToolExecution(output=LifecycleOutput(task=LifecycleTask(
            id="same-image-task", project_id=project_id, asset_id="same-asset", status=status)))

    def enqueue(request: LifecycleEnqueueInput) -> ToolExecution[LifecycleOutput]:
        enqueue_calls.append(request.project_id)
        return output("QUEUED")

    def advance(request: LifecycleAdvanceInput) -> ToolExecution[LifecycleOutput]:
        advance_calls.append(request.task_id)
        return output("SUBMITTING" if len(advance_calls) == 1 else "SUCCEEDED")

    registry.register(ToolSpec(id="image.enqueue", version=1, category=ToolCategory.IMAGE,
        description="Persist one task.", effect=ToolEffect.WRITE, idempotent=False,
        input_model=LifecycleEnqueueInput, output_model=LifecycleOutput, handler=enqueue))
    registry.register(ToolSpec(id="image.advance", version=1, category=ToolCategory.IMAGE,
        description="Advance the same task.", effect=ToolEffect.EXTERNAL, idempotent=False,
        input_model=LifecycleAdvanceInput, output_model=LifecycleOutput, handler=advance))

    def manager() -> ProductionManager:
        store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
        runtime = AgentRuntime(store, ContextCompiler(store, EmptyArtifactResolver()))
        return ProductionManager(runtime=runtime, tools=registry, projects=projects,
                                 language_client_resolver=lambda: object(),
                                 specialist_agent_factory=lambda _client: TerminalSpecialist())

    first_manager = manager()
    runtime = first_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Resume a saved image and short peer.", root_agent="leader")
    image = runtime.create_work_order(run.id, sender="leader", recipient_role="image-worker",
        objective="Use the same task.", why_now="Already ready.", production_skill_role=ProductionSkillRole.ASSET_BUILDER)
    image.specialist_turns.append(SpecialistTurnRecord(id="original-enqueue", sequence=1,
        tool_name="image.enqueue", arguments={"project_id": project_id}, reason="Already requested."))
    image.status = DelegationStatus.NEEDS_ATTENTION
    image.next_owner = WorkOrderNextOwner.WORKER_TOOL
    runtime.store.save_work_order(image)
    peer = runtime.create_work_order(run.id, sender="leader", recipient_role="short",
        objective="Finish the short current check.", why_now="Already ready.",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER).id
    executor = LocalTeamExecutor(first_manager, max_workers=1)
    try:
        assert executor.collect_ready_work(run.id).selected_work_order_ids == [image.id]
        first = _await_batch(lambda: executor.collect_finished_work(run.id))
        assert first.advances[0].advance_count == first.advances[0].deterministic_continuation_count == 1
        pending = runtime.store.get_work_order(image.id)
        assert pending is not None and pending.next_owner == WorkOrderNextOwner.WORKER_TOOL
        assert pending.status == DelegationStatus.NEEDS_ATTENTION
        assert [turn.id for turn in pending.specialist_turns] == ["original-enqueue", pending.specialist_turns[-1].id]
        saved_request = pending.specialist_turns[-1].model_dump()
        assert saved_request["arguments"] == {"task_id": "same-image-task"}
        assert not advance_calls and enqueue_calls == [project_id]
    finally:
        executor.shutdown()
    restarted = manager()
    restored = restarted.runtime.store.get_work_order(image.id)
    assert restored is not None and restored.specialist_turns[-1].model_dump() == saved_request
    executor = LocalTeamExecutor(restarted, max_workers=1)
    try:
        assert executor.collect_ready_work(run.id).selected_work_order_ids == [peer]
        _await_batch(lambda: executor.collect_ready_work(run.id))
        _await_batch(lambda: executor.collect_finished_work(run.id))
        final = restarted.runtime.store.get_work_order(image.id)
        assert final is not None and final.status == DelegationStatus.COMPLETED
        assert final.specialist_turns[1].id == saved_request["id"]
        assert [turn.tool_name for turn in final.specialist_turns] == ["image.enqueue", "image.advance", "image.advance"]
        assert enqueue_calls == [project_id] and advance_calls == ["same-image-task", "same-image-task"]
    finally:
        executor.shutdown()


@pytest.mark.parametrize("peer_kind", ["none", "review", "future", "leader", "user", "old-plan", "terminal", "other-run"])
def test_non_ready_peers_do_not_split_useful_background_drain(tmp_path: Path, peer_kind: str) -> None:
    runtime, run_id = _runtime(tmp_path)
    current = _work_order(runtime, run_id, "current")
    blocked: set[str] = set()
    if peer_kind != "none":
        peer_run_id = (runtime.create_run(project_id="project-other", objective="Other run", root_agent="leader").id
                       if peer_kind == "other-run" else run_id)
        peer_id = _work_order(runtime, peer_run_id, "peer")
        peer = runtime.store.get_work_order(peer_id)
        assert peer is not None
        if peer_kind == "review":
            blocked.add(peer_id)
        elif peer_kind == "future":
            peer.continuation_not_before = utc_now() + timedelta(hours=1)
        elif peer_kind in {"leader", "user"}:
            peer.next_owner = WorkOrderNextOwner.LEADER_REPLAN if peer_kind == "leader" else WorkOrderNextOwner.USER_INPUT
        elif peer_kind == "old-plan":
            peer.source_plan_revision_id = peer.active_plan_revision_id = "old-plan"
        elif peer_kind == "terminal":
            peer.status = DelegationStatus.COMPLETED
        runtime.store.save_work_order(peer)
    manager = DrainingManager(runtime, _registry())
    executor = LocalTeamExecutor(manager, max_workers=1)
    try:
        assert executor.collect_ready_work(run_id, blocked_work_order_ids=blocked).selected_work_order_ids == [current]
        batch = _await_batch(lambda: executor.collect_finished_work(run_id))
        assert len(batch.advances) == 1 and batch.advances[0].advance_count == 3
        assert batch.advances[0].work_order_status == DelegationStatus.COMPLETED
        assert manager.calls == [current] * 3
    finally:
        executor.shutdown()


@pytest.mark.parametrize("newly_blocked", [True, False])
def test_peer_check_uses_latest_review_scope_while_tool_is_running(tmp_path: Path, newly_blocked: bool) -> None:
    runtime, run_id = _runtime(tmp_path)
    first, peer = _work_order(runtime, run_id, "first"), _work_order(runtime, run_id, "peer")
    entered, release = threading.Event(), threading.Event()

    class HeldManager(DrainingManager):
        def execute_work_order(self, work_order_id: str) -> object:
            entered.set()
            assert release.wait(5)
            return super().execute_work_order(work_order_id)

    manager = HeldManager(runtime, _registry())
    executor = LocalTeamExecutor(manager, max_workers=1)
    try:
        executor.collect_ready_work(run_id, blocked_work_order_ids=set() if newly_blocked else {peer})
        assert entered.wait(2)
        batch = executor.collect_ready_work(run_id, blocked_work_order_ids={peer} if newly_blocked else set())
        assert not batch.selected_work_order_ids and batch.active_work_order_ids == [first]
        release.set()
        completed = _await_batch(lambda: executor.collect_finished_work(run_id))
        assert completed.advances[0].advance_count == (3 if newly_blocked else 1)
        saved_peer = runtime.store.get_work_order(peer)
        assert saved_peer is not None and saved_peer.status == DelegationStatus.PENDING
    finally:
        release.set()
        executor.shutdown()


@pytest.mark.parametrize("reason", ["pause", "new-message"])
def test_user_control_precedes_redispatch_and_preserves_pending_turn(tmp_path: Path, reason: str) -> None:
    runtime, run_id = _runtime(tmp_path)
    first, peer = _work_order(runtime, run_id, "first"), _work_order(runtime, run_id, "peer")
    entered, release, user_control = threading.Event(), threading.Event(), threading.Event()

    class ControlledManager(DrainingManager):
        def execute_work_order(self, work_order_id: str) -> object:
            entered.set()
            assert release.wait(5)
            result = super().execute_work_order(work_order_id)
            saved = self.runtime.store.get_work_order(work_order_id)
            assert saved is not None
            saved.updated_at = utc_now()
            self.runtime.store.save_work_order(saved)
            return result

    manager = ControlledManager(runtime, _registry())
    executor = LocalTeamExecutor(manager, max_workers=1)
    try:
        with execution_control(lambda: reason if user_control.is_set() else None):
            assert executor.collect_ready_work(run_id).selected_work_order_ids == [first]
            assert entered.wait(2)
            user_control.set()
            release.set()
            _await_batch(lambda: executor.collect_finished_work(run_id))
            with pytest.raises(ExecutionYielded):
                executor.collect_ready_work(run_id)
        saved = runtime.store.get_work_order(first)
        untouched = runtime.store.get_work_order(peer)
        assert saved is not None and untouched is not None
        assert saved.status == DelegationStatus.NEEDS_ATTENTION
        assert saved.next_owner == WorkOrderNextOwner.WORKER_TOOL
        assert saved.specialist_turns[0].id == "turn-drain-write"
        assert saved.specialist_turns[0].status == SpecialistTurnStatus.REQUESTED
        assert untouched.status == DelegationStatus.PENDING and not untouched.specialist_turns
        assert manager.calls == [first]
    finally:
        release.set()
        executor.shutdown()
