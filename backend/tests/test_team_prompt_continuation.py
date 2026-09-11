"""Saved H3 prompt operations wait without repeated language decisions."""

from datetime import timedelta
from pathlib import Path

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import AgentRuntime, ContextCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus, WorkOrderNextOwner, utc_now
from short_drama.h3_group_operation import H3GroupOperationResponse
from short_drama.production_manager import ProductionManager
from short_drama.production_profiles import ProductionSkillRole
from short_drama.team_executor import LocalTeamExecutor, TeamWorkAdvanceStatus
from short_drama.tools import ToolCategory, ToolEffect, ToolExecution, ToolRegistry, ToolSpec
from short_drama.tools.route import H3GroupOperationInput
from tests.test_team_executor import (
    EmptyArtifactResolver, FixtureProjectStore, RecordingManager, VideoPollingSpecialist, _registry, _runtime, _work_order,
)


def _waiting(project_id: str, *, enqueue: bool = False, future: bool = True) -> dict[str, JsonValue]:
    return H3GroupOperationResponse(
        operation_id="operation-a", project_id=project_id, status="WAITING_PROMPT", phase="PROMPT_COMPILATION",
        compile_mode="H3_CONTEXT_IR", enqueue_requested=enqueue, provider_prompt_cache_id="cache-a",
        provider_prompt_task_id="provider-prompt-a", next_poll_at=(utc_now() + timedelta(seconds=60 if future else -1)).isoformat(),
        next_action="RESUME_SAVED_OPERATION",
    ).model_dump(mode="json")


def _land_wait(runtime: AgentRuntime, run_id: str, *, enqueue: bool, output: dict[str, JsonValue]) -> str:
    work_id = _work_order(runtime, run_id, "prompt")
    work = runtime.store.get_work_order(work_id)
    assert work is not None
    work.production_skill_role = ProductionSkillRole.ROUTE_PLANNER
    work.next_owner = WorkOrderNextOwner.WORKER_DECISION
    work.specialist_turns = [SpecialistTurnRecord(
        id="original-operation", sequence=1, tool_name="route.prepare_h3_group",
        arguments={"project_id": "project-team", "request": {"shot_id": "shot-a", "enqueue": enqueue, "compile_mode": "H3_CONTEXT_IR"}},
        reason="Prepare this current group.", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "route.prepare_h3_group", "status": "SUCCEEDED", "output": output},
    )]
    runtime.store.save_work_order(work)
    return work_id


@pytest.mark.parametrize("enqueue", [False, True])
def test_prompt_wait_persists_exact_resume_and_releases_slot_across_restart(tmp_path: Path, enqueue: bool) -> None:
    runtime, run_id = _runtime(tmp_path)
    output = _waiting("project-team", enqueue=enqueue)
    work_id = _land_wait(runtime, run_id, enqueue=enqueue, output=output)
    calls: list[str] = []
    manager = RecordingManager(runtime, _registry(), calls.append)
    first = LocalTeamExecutor(manager, max_workers=1)
    batch = first.drain_ready_work_until_handoff(run_id)
    assert batch.advances[0].deterministic_continuation_count == 1
    assert batch.advances[0].advance_count == 0 and not calls
    saved = runtime.store.get_work_order(work_id)
    assert saved is not None and saved.continuation_not_before is not None
    assert saved.continuation_not_before.isoformat() == output["next_poll_at"]
    assert saved.specialist_turns[-1].arguments == {
        "project_id": "project-team", "request": {"operation_id": "operation-a", "enqueue": enqueue},
    }
    resumed = LocalTeamExecutor(manager, max_workers=1)
    independent = _work_order(runtime, run_id, "independent")
    assert resumed.run_ready_work(run_id).selected_work_order_ids == [independent]
    still_waiting = runtime.store.get_work_order(work_id)
    assert still_waiting is not None and len(still_waiting.specialist_turns) == 2
    saved.continuation_not_before = utc_now() - timedelta(seconds=1)
    runtime.store.save_work_order(saved)
    assert resumed.run_ready_work(run_id).selected_work_order_ids == [work_id]
    assert calls == [independent, work_id]


@pytest.mark.parametrize("changes", [
    {"enqueue_requested": True}, {"status": "FAILED"}, {"phase": "GENERATION"},
    {"provider_prompt_task_id": None}, {"provider_prompt_cache_id": ""},
    {"next_poll_at": "2026-09-11T12:00:00"}, {"next_poll_at": "invalid"},
    {"project_id": "another-project"}, {"next_action": "RETRY_FAILED_STEP"},
    {"error": "Provider prompt failed"},
])
def test_ambiguous_prompt_continuation_returns_to_worker(tmp_path: Path, changes: dict[str, JsonValue]) -> None:
    runtime, run_id = _runtime(tmp_path)
    work_id = _land_wait(runtime, run_id, enqueue=False, output={**_waiting("project-team"), **changes})
    executor = LocalTeamExecutor(RecordingManager(runtime, _registry(), lambda _id: None))
    assert not executor._queue_deterministic_continuation(work_id)  # pyright: ignore[reportPrivateUsage]
    saved = runtime.store.get_work_order(work_id)
    assert saved is not None and len(saved.specialist_turns) == 1 and saved.continuation_not_before is None


def test_repeated_prompt_polls_use_saved_operation_without_language_roundtrips(tmp_path: Path) -> None:
    runtime, run_id = _runtime(tmp_path)
    runtime = AgentRuntime(runtime.store, ContextCompiler(runtime.store, EmptyArtifactResolver()))
    work_id = _land_wait(runtime, run_id, enqueue=True, output=_waiting("project-team", enqueue=True, future=False))
    registry = ToolRegistry()
    arguments: list[H3GroupOperationInput] = []

    def poll(request: H3GroupOperationInput) -> ToolExecution[H3GroupOperationResponse]:
        arguments.append(request)
        output = _waiting("project-team", enqueue=True, future=False)
        if len(arguments) == 2:
            output.update(status="READY", phase="PREPARED", next_action="REVIEW_PREPARED_GROUP", next_poll_at=None)
        return ToolExecution(output=H3GroupOperationResponse.model_validate(output))

    registry.register(ToolSpec(id="route.prepare_h3_group", version=1, category=ToolCategory.ROUTE,
        description="Resume a saved test group operation.", effect=ToolEffect.EXTERNAL, idempotent=True,
        input_model=H3GroupOperationInput, output_model=H3GroupOperationResponse, handler=poll))
    specialist = VideoPollingSpecialist("READY")
    projects = FixtureProjectStore()
    projects.document.project.id = "project-team"
    manager = ProductionManager(runtime=runtime, tools=registry, projects=projects,
        language_client_resolver=lambda: object(), specialist_agent_factory=lambda _client: specialist)
    batch = LocalTeamExecutor(manager, max_workers=1).drain_ready_work_until_handoff(run_id)
    assert batch.advances[0].status == TeamWorkAdvanceStatus.ADVANCED, batch.model_dump_json()
    assert batch.advances[0].deterministic_continuation_count == 2
    assert len(arguments) == 2 and specialist.calls == 1
    assert all(item.project_id == "project-team" and item.request.operation_id == "operation-a" and item.request.enqueue for item in arguments)
    saved = runtime.store.get_work_order(work_id)
    assert saved is not None and all(turn.status == SpecialistTurnStatus.COMPLETED for turn in saved.specialist_turns)
