"""Single-process TeamExecutor concurrency and commit-lane regressions."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from short_drama.agent_runtime import (
    AgentResultStatus,
    AgentRuntime,
    AgentRuntimeStore,
    ArtifactRef,
    ArtifactSummary,
    ContextCompiler,
    DelegationStatus,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrderNextOwner,
)
from short_drama.agent_runtime.execution_control import execution_control
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import (
    AgentWorkOrderResultRequest,
    AgentWorkOrderView,
    ProductionManager,
    SpecialistToolRequest,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.team_executor import (
    LocalTeamExecutor,
    TeamExecutionBatch,
    TeamWorkAdvanceStatus,
)
from short_drama.tools import (
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolRegistry,
    ToolSpec,
)


class EmptyInput(ToolModel):
    pass


class EmptyOutput(ToolModel):
    ok: bool = True


class LifecycleEnqueueInput(ToolModel):
    project_id: str


class LifecycleAdvanceInput(ToolModel):
    task_id: str


class LifecycleTask(ToolModel):
    id: str
    project_id: str
    asset_id: str
    status: str


class LifecycleOutput(ToolModel):
    task: LifecycleTask


class VideoPollTask(ToolModel):
    id: str
    project_id: str
    status: str
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    provider_task_id: str | None = "remote-h3"
    error_code: str | None = None


def _empty_tool(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
    return ToolExecution(output=EmptyOutput())


def _register_empty_tool(
    registry: ToolRegistry,
    *,
    tool_id: str,
    effect: ToolEffect,
    handler: Callable[[EmptyInput], ToolExecution[EmptyOutput]],
) -> None:
    registry.register(
        ToolSpec(
            id=tool_id,
            version=1,
            category=ToolCategory.PROJECT,
            description=f"Test {effect.value.lower()} Tool.",
            effect=effect,
            idempotent=True,
            input_model=EmptyInput,
            output_model=EmptyOutput,
            handler=handler,
        )
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool_id, effect in (
        ("project.read-for-team", ToolEffect.READ),
        ("project.write-for-team", ToolEffect.WRITE),
    ):
        _register_empty_tool(
            registry,
            tool_id=tool_id,
            effect=effect,
            handler=_empty_tool,
        )
    return registry


class RecordingManager:
    def __init__(
        self,
        runtime: AgentRuntime,
        tools: ToolRegistry,
        on_execute: Callable[[str], None],
    ) -> None:
        self.runtime = runtime
        self.tools = tools
        self._on_execute = on_execute

    def execute_work_order(self, work_order_id: str) -> object:
        self.runtime.begin_work_order(work_order_id)
        self._on_execute(work_order_id)
        self.runtime.record_agent_result(
            work_order_id,
            status=AgentResultStatus.COMPLETED,
            concise_summary=f"Advanced {work_order_id}",
        )
        return object()


class DelayedClaimManager:
    def __init__(
        self,
        runtime: AgentRuntime,
        tools: ToolRegistry,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        self.runtime = runtime
        self.tools = tools
        self._entered = entered
        self._release = release

    def execute_work_order(self, work_order_id: str) -> object:
        self._entered.set()
        if not self._release.wait(timeout=2.0):
            raise AssertionError("Test did not release the delayed WorkOrder claim")
        self.runtime.begin_work_order(work_order_id)
        self.runtime.record_agent_result(
            work_order_id,
            status=AgentResultStatus.COMPLETED,
            concise_summary=f"Advanced {work_order_id}",
        )
        return object()


class DrainingManager:
    """Small persisted decision -> Tool -> decision state machine."""

    def __init__(
        self,
        runtime: AgentRuntime,
        tools: ToolRegistry,
        *,
        final_owner: WorkOrderNextOwner | None = None,
    ) -> None:
        self.runtime = runtime
        self.tools = tools
        self.final_owner = final_owner
        self.calls: list[str] = []

    def execute_work_order(self, work_order_id: str) -> object:
        self.calls.append(work_order_id)
        current = self.runtime.store.get_work_order(work_order_id)
        assert current is not None
        if len(self.calls) == 1:
            current.specialist_turns.append(
                SpecialistTurnRecord(
                    id="turn-drain-write",
                    sequence=1,
                    tool_name="project.write-for-team",
                    reason="Persist the Worker-owned change.",
                )
            )
            current.status = DelegationStatus.NEEDS_ATTENTION
            current.next_owner = WorkOrderNextOwner.WORKER_TOOL
            self.runtime.store.save_work_order(current)
            return object()
        if len(self.calls) == 2:
            turn = current.specialist_turns[-1]
            turn.status = SpecialistTurnStatus.COMPLETED
            turn.result = {
                "tool_id": turn.tool_name,
                "status": "SUCCEEDED",
                "output": {"ok": True},
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            }
            current.status = DelegationStatus.NEEDS_ATTENTION
            current.next_owner = WorkOrderNextOwner.WORKER_DECISION
            self.runtime.store.save_work_order(current)
            return object()
        if self.final_owner is not None:
            current.status = DelegationStatus.NEEDS_ATTENTION
            current.next_owner = self.final_owner
            self.runtime.store.save_work_order(current)
            return object()
        self.runtime.begin_work_order(work_order_id)
        self.runtime.record_agent_result(
            work_order_id,
            status=AgentResultStatus.COMPLETED,
            concise_summary="Worker completed after its persisted Tool result.",
        )
        return object()


class EmptyArtifactResolver:
    def summarize_artifact(self, _ref: ArtifactRef) -> ArtifactSummary | None:
        return None


class FixtureProjectStore:
    def __init__(self) -> None:
        self.document = build_dont_trust_me_fixture("2026-09-04T00:00:00Z")

    def get_project(self, project_id: str):
        return self.document if project_id == self.document.project.id else None

    def get_planning_run(self, _run_id: str):
        return None

    def get_task(self, _task_id: str):
        return None

    def list_tasks(self, _project_id: str):
        return []

    def get_image_task(self, _task_id: str):
        return None

    def get_take(self, _take_id: str):
        return None


class BarrierSpecialist:
    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def execute(self, work_order: AgentWorkOrderView) -> AgentWorkOrderResultRequest:
        self._barrier.wait(timeout=2.0)
        return AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary=f"Completed {work_order.work_order.id}",
        )


class ImageLifecycleSpecialist:
    def __init__(self, project_id: str, terminal_status: str) -> None:
        self._project_id = project_id
        self._terminal_status = terminal_status
        self.calls = 0

    def execute(
        self,
        work_order: AgentWorkOrderView,
    ) -> AgentWorkOrderResultRequest | SpecialistToolRequest:
        self.calls += 1
        if self.calls == 1:
            return SpecialistToolRequest(
                tool_name="image.enqueue",
                arguments={"project_id": self._project_id},
                reason="Generate the already-planned character master.",
            )
        latest_turn = work_order.work_order.specialist_turns[-1]
        assert latest_turn.result is not None
        output = latest_turn.result["output"]
        assert isinstance(output, dict)
        task = output["task"]
        assert isinstance(task, dict)
        assert task["status"] == self._terminal_status
        return AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="Worker reviewed the terminal image task result.",
        )


class VideoPollingSpecialist:
    def __init__(self, terminal_status: str) -> None:
        self.terminal_status = terminal_status
        self.calls = 0

    def execute(self, work_order: AgentWorkOrderView) -> AgentWorkOrderResultRequest | SpecialistToolRequest:
        self.calls += 1
        if not work_order.work_order.specialist_turns:
            return SpecialistToolRequest(tool_name="task.advance", arguments={"task_id": "task-h3"},
                                         reason="Advance the already submitted video task.")
        latest = work_order.work_order.specialist_turns[-1]
        assert latest.result is not None
        output = latest.result["output"]
        assert isinstance(output, dict) and output["status"] == self.terminal_status
        return AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
                                          concise_summary="Worker received the video result or recovery decision.")


def _runtime(tmp_path: Path) -> tuple[AgentRuntime, str]:
    runtime = AgentRuntime(AgentRuntimeStore(tmp_path / "runtime.sqlite3"))
    run = runtime.create_run(
        project_id="project-team",
        objective="Advance independent delegated work.",
        root_agent="production-manager",
    )
    return runtime, run.id


def _work_order(runtime: AgentRuntime, run_id: str, suffix: str) -> str:
    return runtime.create_work_order(
        run_id,
        sender="production-manager",
        recipient_role=f"specialist-{suffix}",
        objective=f"Complete independent work {suffix}.",
        why_now="Its dependencies are already satisfied.",
    ).id


def _work_order_with_tool(
    runtime: AgentRuntime,
    run_id: str,
    suffix: str,
    tool_name: str,
) -> str:
    work_order = runtime.create_work_order(
        run_id,
        sender="production-manager",
        recipient_role=f"specialist-{suffix}",
        objective=f"Execute Tool work {suffix}.",
        why_now="The specialist requested more evidence.",
    )
    work_order.specialist_turns.append(
        SpecialistTurnRecord(
            id=f"turn-{suffix}",
            sequence=1,
            tool_name=tool_name,
            reason="Needed for the bounded WorkOrder.",
        )
    )
    work_order.status = DelegationStatus.NEEDS_ATTENTION
    work_order.next_owner = WorkOrderNextOwner.WORKER_TOOL
    runtime.store.save_work_order(work_order)
    return work_order.id


def test_executor_runs_independent_specialist_decisions_in_parallel(
    tmp_path: Path,
) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_ids = [
        _work_order(runtime, run_id, "a"),
        _work_order(runtime, run_id, "b"),
    ]
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    def overlap(_work_order_id: str) -> None:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2.0)
        with counter_lock:
            active -= 1

    executor = LocalTeamExecutor(
        RecordingManager(runtime, _registry(), overlap),
        max_workers=2,
    )

    batch = executor.run_ready_work(run_id)

    assert batch.selected_work_order_ids == work_order_ids
    assert maximum_active == 2
    assert all(item.status == TeamWorkAdvanceStatus.ADVANCED for item in batch.advances)
    assert all(item.serialized_project_write is False for item in batch.advances)
    assert all(
        runtime.store.get_work_order(work_order_id).status == DelegationStatus.COMPLETED
        for work_order_id in work_order_ids
    )


def test_drain_continues_model_tool_model_until_worker_handoff(
    tmp_path: Path,
) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_id = _work_order(runtime, run_id, "drain")
    manager = DrainingManager(runtime, _registry())

    batch = LocalTeamExecutor(manager, max_workers=1).drain_ready_work_until_handoff(
        run_id
    )

    assert batch.selected_work_order_ids == [work_order_id]
    assert manager.calls == [work_order_id, work_order_id, work_order_id]
    assert batch.advances[0].advance_count == 3
    assert batch.advances[0].serialized_project_write is True
    assert batch.advances[0].work_order_status == DelegationStatus.COMPLETED
    assert batch.advances[0].result_status == AgentResultStatus.COMPLETED


@pytest.mark.parametrize(
    ("lifecycle_sequence", "expected_continuations", "expected_tool_names"),
    [
        (
            ("QUEUED", "SUBMITTING", "SUCCEEDED"),
            2,
            ("image.enqueue", "image.advance", "image.advance"),
        ),
        (("FAILED",), 0, ("image.enqueue",)),
    ],
)
def test_production_manager_image_lifecycle_continuation_preserves_full_trace(
    tmp_path: Path,
    lifecycle_sequence: tuple[str, ...],
    expected_continuations: int,
    expected_tool_names: tuple[str, ...],
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store, ContextCompiler(store, EmptyArtifactResolver()))
    projects = FixtureProjectStore()
    project_id = projects.document.project.id
    registry = _registry()
    lifecycle_statuses = iter(lifecycle_sequence)

    def lifecycle_output() -> ToolExecution[LifecycleOutput]:
        return ToolExecution(
            output=LifecycleOutput(
                task=LifecycleTask(
                    id="image-task-character",
                    project_id=project_id,
                    asset_id="asset-character",
                    status=next(lifecycle_statuses),
                )
            )
        )

    registry.register(
        ToolSpec(
            id="image.enqueue",
            version=1,
            category=ToolCategory.IMAGE,
            description="Create one persisted test image task.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=LifecycleEnqueueInput,
            output_model=LifecycleOutput,
            handler=lambda _request: lifecycle_output(),
        )
    )
    registry.register(
        ToolSpec(
            id="image.advance",
            version=1,
            category=ToolCategory.IMAGE,
            description="Advance one persisted test image task.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=LifecycleAdvanceInput,
            output_model=LifecycleOutput,
            handler=lambda _request: lifecycle_output(),
        )
    )
    specialist = ImageLifecycleSpecialist(project_id, lifecycle_sequence[-1])
    manager = ProductionManager(
        runtime=runtime,
        tools=registry,
        projects=projects,
        language_client_resolver=lambda: object(),
        specialist_agent_factory=lambda _client: specialist,
    )
    run = runtime.create_run(
        project_id=project_id,
        objective="Generate one planned character image and review the result.",
        root_agent="production-manager",
    )
    work_order_id = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-worker",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        objective="Generate the planned character master.",
        why_now="Its asset brief and prompt are already final.",
    ).id

    batch = LocalTeamExecutor(manager, max_workers=1).drain_ready_work_until_handoff(
        run.id
    )

    assert specialist.calls == 2
    assert (
        batch.advances[0].deterministic_continuation_count
        == expected_continuations
    )
    final_work_order = runtime.store.get_work_order(work_order_id)
    assert final_work_order is not None
    assert final_work_order.status == DelegationStatus.COMPLETED
    assert [
        turn.tool_name for turn in final_work_order.specialist_turns
    ] == list(expected_tool_names)
    assert [
        turn.result["output"]["task"]["status"]
        for turn in final_work_order.specialist_turns
        if turn.result is not None
    ] == list(lifecycle_sequence)
    progress = runtime.store.list_work_order_progress(work_order_id)
    assert sum(
        entry.author == "local-team-executor" for entry in progress
    ) == expected_continuations
    assert runtime.store.get_agent_result(work_order_id).status == AgentResultStatus.COMPLETED


@pytest.mark.parametrize(("sequence", "error_code", "remote_id", "resume"), [
    (("ACKNOWLEDGED", "RUNNING", "RUNNING", "SUCCEEDED"), None, "remote-h3", False),
    (("RUNNING", "SUCCEEDED"), None, "remote-h3", True),
    (("FAILED",), None, "remote-h3", False),
    (("SUBMIT_UNKNOWN",), None, None, False),
    (("OUTPUT_MISSING",), None, "remote-h3", False),
    (("SUBMITTING",), None, None, False),
    (("RUNNING",), "H3_POLL_TRANSIENT", "remote-h3", False),
    (("RUNNING",), None, None, False),
])
def test_h3_polling_returns_to_worker_only_for_result_or_recovery_and_resumes_trace(
    tmp_path: Path, sequence: tuple[str, ...], error_code: str | None,
    remote_id: str | None, resume: bool,
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store, ContextCompiler(store, EmptyArtifactResolver()))
    projects = FixtureProjectStore()
    project_id = projects.document.project.id
    statuses = iter(sequence[1:] if resume else sequence)
    registry = _registry()

    def poll(_request: LifecycleAdvanceInput) -> ToolExecution[VideoPollTask]:
        return ToolExecution(output=VideoPollTask(id="task-h3", project_id=project_id,
                                                 status=next(statuses), error_code=error_code,
                                                 provider_task_id=remote_id))

    registry.register(ToolSpec(
        id="task.advance", version=1, category=ToolCategory.TASK,
        description="Advance one test H3 lifecycle transition.", effect=ToolEffect.EXTERNAL,
        idempotent=False, input_model=LifecycleAdvanceInput, output_model=VideoPollTask, handler=poll,
    ))
    specialist = VideoPollingSpecialist(sequence[-1])
    manager = ProductionManager(runtime=runtime, tools=registry, projects=projects,
                                language_client_resolver=lambda: object(),
                                specialist_agent_factory=lambda _client: specialist)
    run = runtime.create_run(project_id=project_id, objective="Recover the existing H3 video.",
                             root_agent="production-manager", execution_target=ProductionExecutionTarget.REAL_H3)
    order = runtime.create_work_order(run.id, sender="production-manager", recipient_role="video-worker",
                                     production_skill_role=ProductionSkillRole.ROUTE_PLANNER,
                                     objective="Recover one video task.", why_now="It has been submitted.")
    if resume:
        order.specialist_turns.append(SpecialistTurnRecord(
            id="poll-before-restart", sequence=1, tool_name="task.advance", arguments={"task_id": "task-h3"},
            reason="The original Worker chose to poll this task.", status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "task.advance", "status": "SUCCEEDED", "output": {
                "id": "task-h3", "project_id": project_id, "status": sequence[0],
                "provider": "minimax", "model": "MiniMax-H3", "provider_task_id": remote_id,
            }},
        ))
        order.next_owner = WorkOrderNextOwner.WORKER_DECISION
        runtime.store.save_work_order(order)
    batch = LocalTeamExecutor(manager, max_workers=1, video_poll_interval_seconds=0.001).drain_ready_work_until_handoff(run.id)
    assert batch.advances[0].status == TeamWorkAdvanceStatus.ADVANCED, batch.model_dump_json()
    assert specialist.calls == (1 if resume else 2)
    assert batch.advances[0].deterministic_continuation_count == len(sequence) - 1
    persisted = runtime.store.get_work_order(order.id)
    assert persisted is not None
    assert [turn.result["output"]["status"] for turn in persisted.specialist_turns if turn.result] == list(sequence)
    assert all(turn.tool_name == "task.advance" and turn.arguments == {"task_id": "task-h3"}
               for turn in persisted.specialist_turns)


@pytest.mark.parametrize(
    "next_owner",
    [WorkOrderNextOwner.LEADER_REPLAN, WorkOrderNextOwner.USER_INPUT],
)
def test_drain_stops_at_non_worker_owner(
    tmp_path: Path,
    next_owner: WorkOrderNextOwner,
) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_id = _work_order(runtime, run_id, next_owner.value.lower())

    class HandoffManager(DrainingManager):
        def execute_work_order(self, work_order_id: str) -> object:
            self.calls.append(work_order_id)
            current = self.runtime.store.get_work_order(work_order_id)
            assert current is not None
            current.status = DelegationStatus.NEEDS_ATTENTION
            current.next_owner = next_owner
            self.runtime.store.save_work_order(current)
            return object()

    manager = HandoffManager(runtime, _registry())
    batch = LocalTeamExecutor(manager, max_workers=1).drain_ready_work_until_handoff(
        run_id
    )

    assert manager.calls == [work_order_id]
    assert batch.advances[0].advance_count == 1
    assert batch.advances[0].work_order_status == DelegationStatus.NEEDS_ATTENTION


@pytest.mark.parametrize("effect", [ToolEffect.READ, ToolEffect.EXTERNAL])
def test_executor_allows_reads_and_external_tool_turns_to_overlap(tmp_path: Path, effect: ToolEffect) -> None:
    runtime, run_id = _runtime(tmp_path)
    registry = _registry()
    _register_empty_tool(registry, tool_id="project.concurrent-for-team", effect=effect,
                         handler=lambda _request: ToolExecution(output=EmptyOutput()))
    work_order_ids = [
        _work_order_with_tool(
            runtime,
            run_id,
            "read-a",
            "project.concurrent-for-team",
        ),
        _work_order_with_tool(
            runtime,
            run_id,
            "read-b",
            "project.concurrent-for-team",
        ),
    ]
    barrier = threading.Barrier(2)

    executor = LocalTeamExecutor(
        RecordingManager(
            runtime,
            registry,
            lambda _work_order_id: barrier.wait(timeout=2.0),
        ),
        max_workers=2,
    )

    batch = executor.run_ready_work(run_id)

    assert batch.selected_work_order_ids == work_order_ids
    assert all(item.status == TeamWorkAdvanceStatus.ADVANCED for item in batch.advances)
    assert all(item.serialized_project_write is False for item in batch.advances)


def test_executor_serializes_direct_write_tool_turns_per_project(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_ids = [
        _work_order_with_tool(
            runtime,
            run_id,
            "write-a",
            "project.write-for-team",
        ),
        _work_order_with_tool(
            runtime,
            run_id,
            "write-b",
            "project.write-for-team",
        ),
    ]
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def hold_first(_work_order_id: str) -> None:
        nonlocal call_count
        with call_lock:
            call_count += 1
            sequence = call_count
        if sequence == 1:
            first_entered.set()
            if not release_first.wait(timeout=2.0):
                raise AssertionError("Test did not release the first project write")
        else:
            second_entered.set()

    executor = LocalTeamExecutor(
        RecordingManager(runtime, _registry(), hold_first),
        max_workers=2,
    )
    batches: list[TeamExecutionBatch] = []
    runner = threading.Thread(
        target=lambda: batches.append(executor.run_ready_work(run_id)),
        daemon=True,
    )
    runner.start()

    assert first_entered.wait(timeout=2.0)
    assert not second_entered.is_set()
    release_first.set()
    runner.join(timeout=2.0)

    assert not runner.is_alive()
    assert second_entered.is_set()
    assert batches[0].selected_work_order_ids == work_order_ids
    assert all(
        item.serialized_project_write is True for item in batches[0].advances
    )


def test_executor_does_not_select_in_progress_or_terminal_work(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    in_progress_id = _work_order(runtime, run_id, "in-progress")
    completed_id = _work_order(runtime, run_id, "completed")
    runtime.begin_work_order(in_progress_id)
    runtime.record_agent_result(
        completed_id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="Already complete.",
    )
    calls: list[str] = []
    executor = LocalTeamExecutor(
        RecordingManager(runtime, _registry(), calls.append),
        max_workers=2,
    )

    batch = executor.run_ready_work(run_id)

    assert batch.selected_work_order_ids == []
    assert calls == []


def test_executor_reserves_pending_work_before_worker_claims_it(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_id = _work_order(runtime, run_id, "reserved")
    entered = threading.Event()
    release = threading.Event()
    executor = LocalTeamExecutor(
        DelayedClaimManager(runtime, _registry(), entered, release),
        max_workers=1,
    )
    first_batches: list[TeamExecutionBatch] = []
    first_runner = threading.Thread(
        target=lambda: first_batches.append(executor.run_ready_work(run_id)),
        daemon=True,
    )
    first_runner.start()

    assert entered.wait(timeout=2.0)
    second_batch = executor.run_ready_work(run_id)
    assert second_batch.selected_work_order_ids == []

    release.set()
    first_runner.join(timeout=2.0)
    assert not first_runner.is_alive()
    assert first_batches[0].selected_work_order_ids == [work_order_id]


def test_executor_leaves_backlog_unclaimed_until_slots_are_free(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_ids = [_work_order(runtime, run_id, f'bounded-{index}') for index in range(5)]
    executor = LocalTeamExecutor(RecordingManager(runtime, _registry(), lambda _id: None), max_workers=2)
    batches = [executor.run_ready_work(run_id) for _ in range(3)]
    assert [len(batch.selected_work_order_ids) for batch in batches] == [2, 2, 1]
    assert {item for batch in batches for item in batch.selected_work_order_ids} == set(work_ids)


def test_pause_reaches_worker_waiting_for_project_write_lane(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_ids = [_work_order_with_tool(runtime, run_id, f'write-{index}', 'project.write-for-team') for index in range(2)]
    first_entered, release_first, paused = threading.Event(), threading.Event(), threading.Event()
    executed: list[str] = []
    def write(work_id: str) -> None:
        executed.append(work_id)
        first_entered.set()
        assert release_first.wait(2)
    executor = LocalTeamExecutor(RecordingManager(runtime, _registry(), write), max_workers=2)
    batches: list[TeamExecutionBatch] = []
    def advance() -> None:
        with execution_control(lambda: 'paused' if paused.is_set() else None):
            batches.append(executor.run_ready_work(run_id))
    thread = threading.Thread(target=advance, daemon=True)
    thread.start()
    try:
        assert first_entered.wait(2)
        paused.set()
    finally:
        release_first.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(executed) == 1 and executed[0] in work_ids
    assert sum(item.status == TeamWorkAdvanceStatus.YIELDED for item in batches[0].advances) == 1
    # A yielded slot is released; the remaining write can continue normally.
    executor.run_ready_work(run_id)
    assert set(executed) == set(work_ids)


def test_executor_uses_production_manager_lock_free_specialist_boundary(
    tmp_path: Path,
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store, ContextCompiler(store, EmptyArtifactResolver()))
    projects = FixtureProjectStore()
    barrier = threading.Barrier(2)
    specialist = BarrierSpecialist(barrier)
    manager = ProductionManager(
        runtime=runtime,
        tools=_registry(),
        projects=projects,
        language_client_resolver=lambda: object(),
        specialist_agent_factory=lambda _client: specialist,
    )
    run = runtime.create_run(
        project_id=projects.document.project.id,
        objective="Run independent specialist reviews.",
        root_agent="production-manager",
    )
    work_order_ids = [
        runtime.create_work_order(
            run.id,
            sender="production-manager",
            recipient_role=f"writer-{suffix}",
            production_skill_role=ProductionSkillRole.WRITER,
            objective=f"Review independent scene {suffix}.",
            why_now="Both scene drafts are available.",
        ).id
        for suffix in ("a", "b")
    ]

    batch = LocalTeamExecutor(manager, max_workers=2).run_ready_work(run.id)

    assert batch.selected_work_order_ids == work_order_ids
    assert all(item.status == TeamWorkAdvanceStatus.ADVANCED for item in batch.advances)
    assert all(
        runtime.store.get_work_order(work_order_id).status == DelegationStatus.COMPLETED
        for work_order_id in work_order_ids
    )


def test_production_manager_serializes_writes_without_blocking_external_io_or_reads(
    tmp_path: Path,
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store, ContextCompiler(store, EmptyArtifactResolver()))
    projects = FixtureProjectStore()
    registry = ToolRegistry()
    write_entered = threading.Event()
    release_write = threading.Event()
    external_started = threading.Event()
    external_entered = threading.Event()
    second_write_entered = threading.Event()
    read_barrier = threading.Barrier(2)

    def hold_write(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        write_entered.set()
        if not release_write.wait(timeout=2.0):
            raise AssertionError("Test did not release the project write lane")
        return ToolExecution(output=EmptyOutput())

    def enter_external(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        external_entered.set()
        return ToolExecution(output=EmptyOutput())

    def second_write(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        second_write_entered.set()
        return ToolExecution(output=EmptyOutput())

    def overlap_read(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        read_barrier.wait(timeout=2.0)
        return ToolExecution(output=EmptyOutput())

    _register_empty_tool(
        registry,
        tool_id="project.central-write",
        effect=ToolEffect.WRITE,
        handler=hold_write,
    )
    _register_empty_tool(
        registry,
        tool_id="project.central-external",
        effect=ToolEffect.EXTERNAL,
        handler=enter_external,
    )
    _register_empty_tool(registry, tool_id="project.second-write",
                         effect=ToolEffect.WRITE, handler=second_write)
    for tool_id in ("project.parallel-read-a", "project.parallel-read-b"):
        _register_empty_tool(
            registry,
            tool_id=tool_id,
            effect=ToolEffect.READ,
            handler=overlap_read,
        )
    manager = ProductionManager(
        runtime=runtime,
        tools=registry,
        projects=projects,
        language_client_resolver=lambda: None,
    )
    project_id = projects.document.project.id
    errors: list[Exception] = []

    def invoke(tool_id: str) -> None:
        try:
            manager._invoke_scoped_tool(project_id, tool_id, {})
        except Exception as exc:
            errors.append(exc)

    write_thread = threading.Thread(
        target=invoke,
        args=("project.central-write",),
        daemon=True,
    )
    write_thread.start()
    assert write_entered.wait(timeout=2.0)
    second_write_thread = threading.Thread(target=invoke, args=("project.second-write",), daemon=True)
    second_write_thread.start()

    def invoke_external() -> None:
        external_started.set()
        invoke("project.central-external")

    external_thread = threading.Thread(target=invoke_external, daemon=True)
    external_thread.start()
    assert external_started.wait(timeout=2.0)
    assert external_entered.wait(timeout=1.0)
    assert not second_write_entered.is_set()
    release_write.set()
    write_thread.join(timeout=2.0)
    second_write_thread.join(timeout=2.0)
    external_thread.join(timeout=2.0)

    assert not write_thread.is_alive()
    assert not external_thread.is_alive()
    assert second_write_entered.is_set()
    assert external_entered.is_set()
    assert errors == []

    read_threads = [
        threading.Thread(target=invoke, args=(tool_id,), daemon=True)
        for tool_id in ("project.parallel-read-a", "project.parallel-read-b")
    ]
    for thread in read_threads:
        thread.start()
    for thread in read_threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in read_threads)
    assert errors == []
