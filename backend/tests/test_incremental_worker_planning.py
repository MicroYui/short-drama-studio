"""Partial completions open useful work without restarting sibling operations."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import (
    ActionKind, AgentResultStatus, DelegationStatus, PlanStep, SpecialistTurnRecord,
    SpecialistTurnStatus, WorkOrderNextOwner,
)
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest,
    AgentRunReplanRequest, AgentWorkOrderResultRequest, ProductionManagerError,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJobControl, StudioMessage
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job


def _delegation(name: str, *, after: str | None = None) -> PlanStep:
    return PlanStep(id=name, title=name, kind=ActionKind.DELEGATION, assigned_agent=name,
                    production_skill_role=ProductionSkillRole.DIRECTOR,
                    instructions=f"Complete only {name}.", depends_on=[] if after is None else [after])

def _running_plan(root: Path) -> tuple[ShortDramaService, str, list[str]]:
    service = ShortDramaService(database_path=root / "app.sqlite3", workspace_root=root / "media")
    project_id = service.initialize_fixture().document.project.id
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Prepare references and continue ready shots.",
        initial_plan=AgentPlanProposal(summary="Three independent bounded tasks.",
                                      steps=[_delegation(name) for name in ("fast", "slow", "other")]),
    ))
    run_id = run.snapshot.run.id
    advanced = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=3))
    return service, run_id, advanced.created_work_order_ids


def _complete(service: ShortDramaService, work_id: str) -> None:
    service.submit_agent_work_order_result(work_id, AgentWorkOrderResultRequest(
        status=AgentResultStatus.COMPLETED, concise_summary="The actual bounded result is saved.",
    ))


def test_partial_completion_appends_ready_work_and_keeps_inflight_media(tmp_path: Path) -> None:
    service, run_id, ids = _running_plan(tmp_path)
    _complete(service, ids[0])
    slow = service.agent_runtime.store.get_work_order(ids[1])
    assert slow is not None
    slow.status = DelegationStatus.IN_PROGRESS
    slow.next_owner = WorkOrderNextOwner.WORKER_TOOL
    slow.specialist_turns.append(SpecialistTurnRecord(
        id="existing-media-poll", sequence=1, tool_name="task.advance", arguments={"task_id": "existing-h3-task"},
        reason="Continue the already acknowledged remote task.", status=SpecialistTurnStatus.IN_PROGRESS,
    ))
    service.agent_runtime.store.save_work_order(slow)
    pm = service.production_manager
    prepared = pm.prepare_completion_planning(run_id)
    assert prepared is not None and prepared.completed_work_order_ids == [ids[0]]
    assert pm.prepare_completion_planning(run_id) is None
    previous_plan = service.get_agent_run(run_id).snapshot.current_plan
    assert previous_plan is not None
    assert pm.apply_completion_plan(prepared, AgentPlanProposal(
        summary="Use the finished reference to prepare its shot.", steps=[_delegation("successor", after="fast")],
    ))
    view = service.get_agent_run(run_id)
    assert view.snapshot.current_plan is not None
    assert [step.id for step in view.ready_steps] == ["successor"]
    retained = service.agent_runtime.store.get_work_order(ids[1])
    assert retained is not None and retained.status == DelegationStatus.IN_PROGRESS
    assert retained.active_plan_revision_id == view.snapshot.current_plan.id
    assert retained.source_plan_revision_id == previous_plan.id
    assert retained.specialist_turns == slow.specialist_turns
    assert retained.superseded_by_plan_revision_id is None
    successor = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
    assert len(successor.created_work_order_ids) == 1
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 4
    assert [action.plan_revision_id for action in view.snapshot.actions] == [previous_plan.id] * 3
    # The original in-flight handoff completes normally against its new active plan.
    _complete(service, ids[1])
    next_event = pm.prepare_completion_planning(run_id)
    assert next_event is not None and next_event.completed_work_order_ids == [ids[1]]
    assert pm.apply_completion_plan(next_event, AgentPlanProposal(
        summary="Continue from the second completed result.", steps=[_delegation("second-successor", after="slow")],
    ))
    twice_appended = service.get_agent_run(run_id)
    assert [step.id for step in twice_appended.ready_steps] == ["second-successor"]
    completed_slow = service.agent_runtime.store.get_work_order(ids[1])
    assert completed_slow is not None and completed_slow.status == DelegationStatus.COMPLETED
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 4
    assert len(service.get_project(view.snapshot.run.project_id).tasks) == 0


def test_explicit_wait_is_consumed_across_restart_and_cannot_rewrite_old_steps(tmp_path: Path) -> None:
    service, run_id, ids = _running_plan(tmp_path)
    _complete(service, ids[0])
    pm = service.production_manager
    prepared = pm.prepare_completion_planning(run_id)
    assert prepared is not None
    with pytest.raises(ProductionManagerError, match="cannot rewrite"):
        pm.apply_completion_plan(prepared, AgentPlanProposal(
            summary="Invalid attempt to replace old work.", steps=[_delegation("slow").model_copy(update={"instructions": "Repeat generation"})],
        ))
    assert not pm.apply_completion_plan(prepared, AgentPlanProposal(
        summary="The remaining paper states are needed; continue the existing work.",
        carried_work_order_ids=ids[1:],
    ))
    restarted = ShortDramaService(database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "media")
    assert restarted.production_manager.prepare_completion_planning(run_id) is None
    assert len(restarted.agent_runtime.store.list_plan_revisions(run_id)) == 1
    assert all(work.status != DelegationStatus.CANCELLED for work in restarted.agent_runtime.store.list_work_orders(run_id))


def test_user_plan_revision_discards_late_supplement(tmp_path: Path) -> None:
    service, run_id, ids = _running_plan(tmp_path)
    _complete(service, ids[0])
    prepared = service.production_manager.prepare_completion_planning(run_id)
    assert prepared is not None
    service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason="User narrowed the deliverable.", proposed_plan=AgentPlanProposal(
            summary="Keep the ongoing work under the new request.", carried_work_order_ids=ids[1:],
        ),
    ))
    assert not service.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary="Late old answer.", steps=[_delegation("stale-successor")],
    ))
    assert len(service.agent_runtime.store.list_plan_revisions(run_id)) == 2
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 3


def test_mechanical_media_continuation_reads_inherited_ownership_under_same_lock(tmp_path: Path) -> None:
    service, run_id, ids = _running_plan(tmp_path)
    slow = service.agent_runtime.store.get_work_order(ids[1])
    plan = service.get_agent_run(run_id).snapshot.current_plan
    assert slow is not None and plan is not None
    slow.status = DelegationStatus.NEEDS_ATTENTION
    slow.next_owner = WorkOrderNextOwner.WORKER_DECISION
    slow.specialist_turns.append(SpecialistTurnRecord(
        id="observed-poll", sequence=1, tool_name="task.advance", arguments={"task_id": "same-task"},
        reason="The existing remote task remains in progress.", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "task.advance", "status": "SUCCEEDED", "output": {
            "id": "same-task", "project_id": service.get_agent_run(run_id).snapshot.run.project_id,
            "status": "RUNNING", "provider": "minimax", "model": "MiniMax-H3", "provider_task_id": "same-remote-task",
        }},
    ))
    service.agent_runtime.store.save_work_order(slow)
    entered = threading.Event()

    def continue_media() -> bool:
        entered.set()
        return service.team_executor._queue_deterministic_continuation(ids[1])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.agent_runtime.run_lock(run_id):
            pending = pool.submit(continue_media)
            assert entered.wait(1)
            assert not pending.done()
            inherited = service.agent_runtime.replace_plan(
                run_id, summary="Retain the same running media.", steps=plan.steps,
                carried_work_order_ids=[slow.id], updated_work_orders=[slow],
                inherited_action_ids=[action.id for action in service.agent_runtime.snapshot(run_id).actions],
            )
        assert pending.result(timeout=2)
    retained = service.agent_runtime.store.get_work_order(slow.id)
    assert retained is not None and retained.active_plan_revision_id == inherited.id
    assert retained.specialist_turns[-1].arguments == {"task_id": "same-task"}
    assert retained.specialist_turns[-1].status == SpecialistTurnStatus.REQUESTED
    assert len(retained.specialist_turns) == 2


def test_runner_slow_leader_allows_worker_completion_and_starts_new_successor(tmp_path: Path) -> None:
    leader_started, release_leader = threading.Event(), threading.Event()
    slow_started, release_slow = threading.Event(), threading.Event()
    medium_started, release_medium = threading.Event(), threading.Event()
    successor_finished = threading.Event()
    known_successor_finished = threading.Event()
    supplement_events: list[str] = []
    planned_completion_sets: list[frozenset[str]] = []
    worker_calls: list[str] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" not in payload:
            if payload.get("current_plan") is None:
                initial = _plan("fast", "slow", "medium", "known-successor", complete=False)
                initial["steps"][-1]["depends_on"] = ["medium"]
                return initial
            if str(payload.get("reason", "")).startswith("这些 Worker"):
                supplement_events.append(str(payload["reason"]))
                run = service.agent_runtime.store.get_run(str(payload["context"]["run_id"]))
                assert run is not None
                planned_completion_sets.append(frozenset(run.completion_planned_work_order_ids))
                if len(supplement_events) == 1:
                    leader_started.set()
                    assert release_leader.wait(15)
                    return {"summary": "Prepare the next ready shot.", "goal_complete": False,
                            "steps": [_delegation("successor", after="fast").model_dump(mode="json")]}
                return {"summary": "No additional ready work yet.", "steps": [], "goal_complete": False,
                        "carried_work_order_ids": payload["current_plan"]["carried_work_order_ids"]}
            return _plan()
        name = str(payload["work_order"]["recipient_role"])
        worker_calls.append(name)
        if name == "slow":
            slow_started.set()
            assert release_slow.wait(20)
        elif name == "medium":
            medium_started.set()
            assert release_medium.wait(15)
        elif name == "successor":
            successor_finished.set()
        elif name == "known-successor":
            known_successor_finished.set()
        return _done(name + " completed")

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text="Use each completed result when it is ready."))
    try:
        assert slow_started.wait(10) and medium_started.wait(10) and leader_started.wait(10)
        release_medium.set()
        deadline = time.monotonic() + 3
        medium_completed = False
        while time.monotonic() < deadline:
            jobs = service.repository.list_studio_jobs(project_id)
            run_id = jobs[0].run_id
            if run_id is not None:
                medium_completed = any(work.recipient_role == "medium" and work.status == DelegationStatus.COMPLETED
                                       for work in service.agent_runtime.store.list_work_orders(run_id))
            if medium_completed:
                break
            threading.Event().wait(0.01)
        assert medium_completed, "A slow Leader held the run lock and prevented the independent Worker from saving"
        assert known_successor_finished.wait(3), "The slow Leader blocked an existing newly-ready dependent step"
        assert not release_leader.is_set() and not release_slow.is_set()
        release_leader.set()
        assert successor_finished.wait(10), "The successor waited for the unrelated slow Worker"
        assert not release_slow.is_set()
        # The Fake callback starts before its COMPLETED result is persisted. Wait
        # for every fast result and its actual Leader request, so a later valid
        # completion cannot race the no-new-result assertion below.
        deadline = time.monotonic() + 5
        expected_completed_roles = {"fast", "medium", "known-successor", "successor"}
        while time.monotonic() < deadline:
            current_job = next(item for item in service.repository.list_studio_jobs(project_id) if item.id == job.id)
            assert current_job.run_id is not None
            completed = [work for work in service.agent_runtime.store.list_work_orders(current_job.run_id)
                         if work.status == DelegationStatus.COMPLETED]
            if ({work.recipient_role for work in completed} == expected_completed_roles
                    and planned_completion_sets
                    and planned_completion_sets[-1] == {work.id for work in completed}):
                break
            threading.Event().wait(0.01)
        else:
            pytest.fail("The completed fast results were not consumed by Leader planning")
        assert not release_slow.is_set()
        # Each optional request must consume new persisted results. Calls may
        # batch those results differently as independent Workers finish.
        assert all(previous < current for previous, current in
                   zip(planned_completion_sets, planned_completion_sets[1:], strict=False))
        # Several scheduler ticks without a new result do not create another call.
        before = len(supplement_events)
        threading.Event().wait(0.4)
        assert len(supplement_events) == before
    finally:
        release_leader.set()
        release_medium.set()
        release_slow.set()
    finished = _wait_job(service, job.id, "completed", timeout=15)
    assert finished.run_id is not None
    assert sorted(worker_calls) == ["fast", "known-successor", "medium", "slow", "successor"]
    assert all(work.status == DelegationStatus.COMPLETED
               for work in service.agent_runtime.store.list_work_orders(finished.run_id))


@pytest.mark.parametrize("control", ["pause", "message"])
@pytest.mark.parametrize("yield_at_boundary", [False, True])
def test_runner_discards_started_supplement_at_user_boundary(
    tmp_path: Path, control: str, yield_at_boundary: bool,
) -> None:
    leader_started, release_leader = threading.Event(), threading.Event()
    slow_started, release_slow = threading.Event(), threading.Event()
    worker_calls: list[str] = []
    yielded_controls: list[str] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" not in payload:
            if payload.get("goal", {}).get("latest_user_instructions"):
                return _plan()
            if payload.get("current_plan") is None:
                return _plan("fast", "slow", complete=False)
            if str(payload.get("reason", "")).startswith("这些 Worker"):
                leader_started.set()
                assert release_leader.wait(15)
                if yield_at_boundary:
                    try:
                        execution_boundary()
                    except ExecutionYielded:
                        yielded_controls.append(control)
                        raise
                return {"summary": "Old proposed successor.", "steps": [_delegation("must-not-start").model_dump(mode="json")]}
            return _plan()
        name = str(payload["work_order"]["recipient_role"])
        worker_calls.append(name)
        if name == "slow":
            slow_started.set()
            assert release_slow.wait(15)
        return _done(name)

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text="Continue from ready results."))
    try:
        assert slow_started.wait(10) and leader_started.wait(10)
        if control == "pause":
            service.studio_runtime.control(job.id, StudioJobControl(action="pause"))
        else:
            service.studio_runtime.send(project_id, StudioMessage(text="Stop adding work; deliver the saved result."))
        release_slow.set()
        threading.Event().wait(0.1)
        current = service.repository.list_studio_jobs(project_id)[0]
        assert current.status != "paused", "Pause was acknowledged before the started Leader call ended"
        release_leader.set()
    finally:
        release_slow.set()
        release_leader.set()
    _wait_job(service, job.id, "paused" if control == "pause" else "completed", timeout=15)
    assert sorted(worker_calls) == ["fast", "slow"]
    assert yielded_controls == ([control] if yield_at_boundary else [])
