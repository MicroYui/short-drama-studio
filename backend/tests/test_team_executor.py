"""Single-process TeamExecutor concurrency and commit-lane regressions."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from short_drama.agent_runtime import (
    AgentResultStatus,
    AgentRuntime,
    AgentRuntimeStore,
    ArtifactRef,
    ArtifactSummary,
    ContextCompiler,
    DelegationStatus,
    SpecialistTurnRecord,
    WorkOrderNextOwner,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.production_manager import (
    AgentWorkOrderResultRequest,
    AgentWorkOrderView,
    ProductionManager,
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


def test_executor_allows_read_only_tool_turns_to_overlap(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_order_ids = [
        _work_order_with_tool(
            runtime,
            run_id,
            "read-a",
            "project.read-for-team",
        ),
        _work_order_with_tool(
            runtime,
            run_id,
            "read-b",
            "project.read-for-team",
        ),
    ]
    barrier = threading.Barrier(2)

    executor = LocalTeamExecutor(
        RecordingManager(
            runtime,
            _registry(),
            lambda _work_order_id: barrier.wait(timeout=2.0),
        ),
        max_workers=2,
    )

    batch = executor.run_ready_work(run_id)

    assert batch.selected_work_order_ids == work_order_ids
    assert all(item.status == TeamWorkAdvanceStatus.ADVANCED for item in batch.advances)
    assert all(item.serialized_project_write is False for item in batch.advances)


def test_executor_serializes_non_read_tool_turns_per_project(tmp_path: Path) -> None:
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


def test_production_manager_serializes_project_writes_and_external_effects_but_not_reads(
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
    read_barrier = threading.Barrier(2)

    def hold_write(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        write_entered.set()
        if not release_write.wait(timeout=2.0):
            raise AssertionError("Test did not release the project write lane")
        return ToolExecution(output=EmptyOutput())

    def enter_external(_request: EmptyInput) -> ToolExecution[EmptyOutput]:
        external_entered.set()
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

    def invoke_external() -> None:
        external_started.set()
        invoke("project.central-external")

    external_thread = threading.Thread(target=invoke_external, daemon=True)
    external_thread.start()
    assert external_started.wait(timeout=2.0)
    assert not external_entered.wait(timeout=0.05)
    release_write.set()
    write_thread.join(timeout=2.0)
    external_thread.join(timeout=2.0)

    assert not write_thread.is_alive()
    assert not external_thread.is_alive()
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
