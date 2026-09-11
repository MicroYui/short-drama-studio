"""Ready Leader reads must yield execution to already-retained Worker tools."""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import (
    ActionKind, AgentResultStatus, DelegationStatus, PlanStep, SpecialistTurnRecord,
    SpecialistTurnStatus, WorkOrderNextOwner,
)
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest,
    AgentRunReplanRequest,
    AgentWorkOrderResultRequest,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.studio_models import StudioJob, StudioJobControl, StudioScope
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job
from short_drama.service import ShortDramaService


def test_repeated_ready_reads_cannot_starve_a_carried_worker_tool(tmp_path: Path) -> None:
    leader_observed_completed: list[bool] = []
    worker_decisions: list[str] = []
    work_ids: list[str] = []
    leader_started = threading.Event()

    def read_plan(sequence: int) -> AgentPlanProposal:
        return AgentPlanProposal(
            summary="Read the current capability while the retained Worker continues.",
            carried_work_order_ids=[work_id for work_id in work_ids
                                    if (saved := service.agent_runtime.store.get_work_order(work_id)) is not None
                                    and saved.status != DelegationStatus.COMPLETED],
            steps=[PlanStep(id=f"read-{sequence}", title="Read capability", kind=ActionKind.TOOL_CALL,
                            assigned_agent="Leader", instructions="Read the current H3 capabilities.",
                            tool_name="route.h3_capability_profile", arguments={"project_id": project_id})],
        )

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" in payload:
            worker_decisions.append(str(payload["work_order"]["id"]))
            assert leader_started.wait(8), "A retained Worker prevented the next independent Leader decision"
            return _done("The persisted Worker Tool completed; no media submission is needed.")
        leader_started.set()
        work = service.agent_runtime.store.get_work_order(work_ids[0])
        assert work is not None
        deadline = time.monotonic() + 5
        while work.status != DelegationStatus.COMPLETED and time.monotonic() < deadline:
            threading.Event().wait(0.01)
            work = service.agent_runtime.store.get_work_order(work_ids[0])
            assert work is not None
        assert work.status == DelegationStatus.COMPLETED, "Leader planning blocked the retained Worker's Tool or result save"
        leader_observed_completed.append(True)
        if len(leader_observed_completed) < 3:
            return read_plan(len(leader_observed_completed)).model_dump(mode="json")
        return _plan()

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Continue the existing pre-video work without repeating media.",
        initial_plan=AgentPlanProposal(summary="One bounded Worker.", steps=[PlanStep(
            id="existing-worker", title="Existing Worker", kind=ActionKind.DELEGATION,
            assigned_agent="director", production_skill_role=ProductionSkillRole.DIRECTOR,
            instructions="Continue the pending local read and finish the current work.",
        )]),
    ))
    run_id = run.snapshot.run.id
    created = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False)
    work_ids.extend(created.created_work_order_ids)
    assert len(work_ids) == 1
    work = service.agent_runtime.store.get_work_order(work_ids[0])
    assert work is not None
    source_action_id = work.source_action_id
    work.status = DelegationStatus.NEEDS_ATTENTION
    work.next_owner = WorkOrderNextOwner.WORKER_TOOL
    work.specialist_turns.append(SpecialistTurnRecord(
        id="saved-worker-tool", sequence=1, tool_name="route.h3_capability_profile",
        arguments={"project_id": project_id}, reason="Resume the already decided local Tool.",
    ))
    service.agent_runtime.store.save_work_order(work)
    service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason="Retain the current Worker and read the capability.", proposed_plan=read_plan(0),
    ))
    job = StudioJob(id="resume-carried-worker", project_id=project_id, run_id=run_id,
                    objective=run.snapshot.run.objective, scope=StudioScope(), status="paused",
                    created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
    _wait_job(service, job.id, "completed")

    assert leader_observed_completed and leader_observed_completed[0], (
        "The Runner requested another Leader plan before executing the retained Worker Tool"
    )
    assert worker_decisions == work_ids
    saved = service.agent_runtime.store.get_work_order(work_ids[0])
    assert saved is not None and saved.status == DelegationStatus.COMPLETED
    assert saved.source_action_id == source_action_id
    assert len(saved.specialist_turns) == 1
    assert saved.specialist_turns[0].id == "saved-worker-tool"
    assert saved.specialist_turns[0].status == SpecialistTurnStatus.COMPLETED
    assert saved.specialist_turns[0].result is not None
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 1
    assert not service.get_project(project_id).tasks
    worker_tools = [event for event in service.repository.list_studio_events(project_id)
                    if event.kind == "tool.finished" and event.data.get("work_order_id") == work_ids[0]]
    assert len(worker_tools) == 1
    assert worker_tools[0].data["tool_name"] == "route.h3_capability_profile"


@pytest.mark.parametrize("wait", [True, False])
def test_local_plan_continuation_preserves_handoff_and_consumes_wait(tmp_path: Path, wait: bool) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Continue independent work while keeping the existing media handoff.",
        initial_plan=AgentPlanProposal.model_validate(_plan("held", complete=False)),
    ))
    run_id = run.snapshot.run.id
    created = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False)
    work_id = created.created_work_order_ids[0]
    saved_work = service.agent_runtime.store.get_work_order(work_id)
    assert saved_work is not None
    service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason="Read current capability with the old handoff retained.",
        proposed_plan=AgentPlanProposal(summary="Current bounded read.", carried_work_order_ids=[work_id], steps=[
            PlanStep(id="saved-read", title="Read", kind=ActionKind.TOOL_CALL,
                     assigned_agent="Leader", instructions="Read the current capability.",
                     tool_name="route.h3_capability_profile", arguments={"project_id": project_id}),
        ]),
    ))
    service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1),
                              advance_workers=False, allow_replanning=False)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True)
    assert prepared is not None and prepared.local_plan_completed
    assert prepared.completed_work_order_ids == []
    new_steps = [] if wait else [PlanStep(
        id="successor", title="Use the completed read", kind=ActionKind.DELEGATION,
        assigned_agent="successor", instructions="Use the completed read for independent work.",
        production_skill_role=ProductionSkillRole.DIRECTOR, depends_on=["saved-read"],
    )]
    assert service.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary="Wait for a real result." if wait else "Start independent work.",
        steps=new_steps, carried_work_order_ids=[work_id],
    ))
    # A late application of this same response cannot append or replay it twice.
    assert not service.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary="Old response.", steps=new_steps, carried_work_order_ids=[work_id],
    ))
    retained = service.agent_runtime.store.get_work_order(work_id)
    assert retained is not None and retained.status == saved_work.status
    assert retained.source_action_id == saved_work.source_action_id
    assert retained.specialist_turns == saved_work.specialist_turns
    assert retained.superseded_by_plan_revision_id is None
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 1
    restarted = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    view = restarted.get_agent_run(run_id)
    assert view.snapshot.current_plan is not None and view.snapshot.current_plan.carried_work_order_ids == [work_id]
    if wait:
        assert view.snapshot.current_plan.steps == []
    else:
        assert [step.id for step in view.ready_steps] == ["successor"]
    assert restarted.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True) is None


def test_late_carried_completion_survives_empty_wait_and_opens_successor(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Use each real result when it arrives.",
        initial_plan=AgentPlanProposal.model_validate(_plan("late", "slow", complete=False)),
    ))
    run_id = run.snapshot.run.id
    ids = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=2),
                                    advance_workers=False).created_work_order_ids
    assert len(ids) == 2
    service.replan_agent_run(run_id, AgentRunReplanRequest(reason="Read while both existing Workers continue.",
        proposed_plan=AgentPlanProposal(summary="Current read.", carried_work_order_ids=ids, steps=[PlanStep(
            id="read", title="Read", kind=ActionKind.TOOL_CALL, assigned_agent="Leader",
            instructions="Read capability.", tool_name="route.h3_capability_profile", arguments={"project_id": project_id},
        )]),
    ))
    service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False, allow_replanning=False)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True)
    assert prepared is not None and prepared.local_plan_completed and prepared.completed_work_order_ids == []
    # This result lands after the slow Leader took its input snapshot, before
    # it proposes an empty wait. The proposal has not seen or consumed it.
    service.submit_agent_work_order_result(ids[0], AgentWorkOrderResultRequest(
        status=AgentResultStatus.COMPLETED, concise_summary="A usable result was saved during the Leader request.",
    ))
    completed_before = service.agent_runtime.store.get_work_order(ids[0])
    result_before = service.agent_runtime.store.get_agent_result(ids[0])
    assert completed_before is not None and result_before is not None
    assert service.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary="Wait for the existing work.", carried_work_order_ids=ids,
    ))
    view = service.get_agent_run(run_id)
    assert view.snapshot.current_plan is not None and view.snapshot.current_plan.steps == []
    assert view.snapshot.current_plan.carried_work_order_ids == [ids[1]]
    assert ids[0] not in view.snapshot.run.completion_planned_work_order_ids
    completed_after = service.agent_runtime.store.get_work_order(ids[0])
    assert completed_after is not None and completed_after.active_plan_revision_id == view.snapshot.current_plan.id
    assert completed_after.status == DelegationStatus.COMPLETED
    assert completed_after.source_action_id == completed_before.source_action_id
    assert service.agent_runtime.store.get_agent_result(ids[0]) == result_before
    restarted = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    followup = restarted.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True)
    assert followup is not None and not followup.local_plan_completed
    assert followup.completed_work_order_ids == [ids[0]]
    assert restarted.production_manager.apply_completion_plan(followup, AgentPlanProposal(
        summary="Use the newly saved result without waiting for the slow Worker.",
        steps=[PlanStep(id="use-late", title="Use ready result", kind=ActionKind.DELEGATION,
                        assigned_agent="successor", production_skill_role=ProductionSkillRole.DIRECTOR,
                        instructions="Use the completed bounded result.")],
    ))
    assert [step.id for step in restarted.get_agent_run(run_id).ready_steps] == ["use-late"]
    slow = restarted.agent_runtime.store.get_work_order(ids[1])
    assert slow is not None and slow.status == DelegationStatus.PENDING
    assert restarted.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True) is None
