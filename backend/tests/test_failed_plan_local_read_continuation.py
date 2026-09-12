"""Fresh2: a repaired local read must finish before an inherited Worker does."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

import pytest

from short_drama.agent_runtime import ActionStatus, DelegationStatus, PlanGraph, SpecialistTurnRecord, WorkOrderNextOwner
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary
from short_drama.production_manager import AgentPlanProposal, AgentRunAdvanceRequest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioJobControl, StudioMessage, StudioScope
from tests.test_failed_plan_continuation import _failed_plan, _read
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job


def _local_plan(service: ShortDramaService, project_id: str) -> tuple[str, str]:
    run_id, work_id, _ = _failed_plan(service, project_id)
    failed = service.production_manager.prepare_completion_planning(run_id, include_failed_plan=True)
    assert failed is not None
    assert service.production_manager.apply_completion_plan(failed, AgentPlanProposal(
        summary='Read the local design needed to repair the other slice.',
        steps=[_read(project_id, 'read_shot08_for_local_coverage_repair')], carried_work_order_ids=[work_id]))
    return run_id, work_id


def test_local_read_completion_survives_restart_and_empty_wait_is_consumed(tmp_path: Path) -> None:
    service, pid = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run_id, work_id = _local_plan(service, pid)
    assert service.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True) is None
    service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False, allow_replanning=False)
    restarted = ShortDramaService(database_path=tmp_path / 'studio.sqlite3', workspace_root=tmp_path / 'media')
    snapshot = restarted.agent_runtime.snapshot(run_id)
    assert snapshot.current_plan is not None
    graph = PlanGraph(snapshot.current_plan)
    assert not graph.is_complete(snapshot.actions) and not graph.ready_steps(snapshot.actions)
    assert graph.has_completed_local_work(snapshot.actions)
    active = restarted.agent_runtime.store.get_work_order(work_id)
    assert active is not None and active.status != DelegationStatus.COMPLETED
    prepared = restarted.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True)
    assert prepared is not None and prepared.local_plan_completed and not prepared.failed_plan_state
    assert prepared.completed_work_order_ids == []
    assert restarted.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary='No further independent work until the original Worker finishes.', steps=[], carried_work_order_ids=[work_id]))
    twice = ShortDramaService(database_path=tmp_path / 'studio.sqlite3', workspace_root=tmp_path / 'media')
    assert twice.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True) is None
    assert len(twice.agent_runtime.store.list_work_orders(run_id)) == 1
    assert not twice.get_project(pid).tasks


@pytest.mark.parametrize('control', ['finish', 'pause', 'message'])
def test_runner_continues_local_read_while_original_request_runs(tmp_path: Path, control: str) -> None:
    worker_started, second_leader, release, first_leader = (threading.Event() for _ in range(4))
    calls: list[str] = []
    yielded: list[str] = []
    old_id: list[str] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if 'work_order' in payload:
            assert payload['work_order']['id'] == old_id[0]
            worker_started.set()
            assert release.wait(12)
            return _done('Original saved request finished once; no media resubmitted.')
        if payload.get('goal', {}).get('latest_user_instructions'):
            calls.append('user')
            return _plan()
        if not first_leader.is_set():
            first_leader.set()
            calls.append('failed')
            assert 'InvalidContextManifest' in payload.get('reason', '')
            return AgentPlanProposal(summary='Read the local coverage before deciding its repair.',
                steps=[_read(pid, 'read_shot08_for_local_coverage_repair')], carried_work_order_ids=old_id).model_dump(mode='json')
        if not second_leader.is_set():
            assert worker_started.wait(8)
            snapshot = service.agent_runtime.snapshot(run_id)
            assert snapshot.current_plan is not None
            graph = PlanGraph(snapshot.current_plan)
            assert not graph.is_complete(snapshot.actions)
            assert any(a.plan_step_id == 'read_shot08_for_local_coverage_repair' and a.status == ActionStatus.SUCCEEDED for a in snapshot.actions)
            old = service.agent_runtime.store.get_work_order(old_id[0])
            assert old is not None and old.status != DelegationStatus.COMPLETED
            calls.append('local')
            second_leader.set()
            assert release.wait(12)
            if control != 'finish':
                try:
                    execution_boundary()
                except ExecutionYielded:
                    yielded.append(control)
                    raise
                if control == 'pause':
                    pytest.fail('Old local continuation crossed the pause boundary')
                # A message can already have been applied and cleared from
                # pending before this callback returns. The saved plan/user
                # comparison must then discard this stale response.
            return AgentPlanProposal(summary='Local evidence is saved; wait for the original Worker.',
                steps=[], carried_work_order_ids=old_id).model_dump(mode='json')
        calls.append('complete')
        return _plan()

    service, pid = _service(tmp_path, HarnessLanguageClient(respond))
    run_id, work_id, _ = _failed_plan(service, pid)
    old_id.append(work_id)
    old = service.agent_runtime.store.get_work_order(work_id)
    assert old is not None
    old.status = DelegationStatus.NEEDS_ATTENTION
    old.next_owner = WorkOrderNextOwner.WORKER_TOOL
    old.specialist_turns = [SpecialistTurnRecord(id='original-pending-request', sequence=1,
        tool_name='route.h3_capability_profile', arguments={'project_id': pid}, reason='Already saved before restart.')]
    original_action = old.source_action_id
    service.agent_runtime.store.save_work_order(old)
    job = StudioJob(id='fresh2-local-continuation', project_id=pid, run_id=run_id, objective='Only pre-video preparation.',
        scope=StudioScope(), status='paused', created_at='2026-09-12T00:00:00Z', updated_at='2026-09-12T00:00:00Z')
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action='resume'))
    try:
        assert second_leader.wait(10), 'Inherited RUNNING action blocked the local read continuation'
        if control == 'pause':
            service.studio_runtime.control(job.id, StudioJobControl(action='pause'))
        elif control == 'message':
            service.studio_runtime.send(pid, StudioMessage(text='Stop here and deliver saved evidence.'))
    finally:
        release.set()
    _wait_job(service, job.id, 'paused' if control == 'pause' else 'completed', timeout=15)
    saved = service.agent_runtime.store.get_work_order(work_id)
    assert saved is not None and saved.source_action_id == original_action
    assert len(saved.specialist_turns) == 1 and saved.specialist_turns[0].id == 'original-pending-request'
    assert saved.specialist_turns[0].result is not None and saved.specialist_turns[0].result['status'] == 'SUCCEEDED'
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 1
    assert calls.count('failed') == calls.count('local') == 1
    if control == 'message':
        assert yielded in ([], ['message'])
        assert not any(plan.summary == 'Local evidence is saved; wait for the original Worker.'
            for plan in service.agent_runtime.store.list_plan_revisions(run_id))
    else:
        assert yielded == ([] if control == 'finish' else ['pause'])
    assert not service.get_project(pid).tasks


def test_last_worker_finishes_during_local_decision_empty_snapshot_is_not_new_proposal(tmp_path: Path) -> None:
    from pydantic import ValidationError
    from short_drama.agent_runtime import AgentResultStatus
    from short_drama.production_manager import AgentRunReplanRequest, AgentWorkOrderResultRequest

    service, pid = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run_id, work_id = _local_plan(service, pid)
    service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False, allow_replanning=False)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_local_plan_completion=True)
    assert prepared is not None and prepared.local_plan_completed
    service.submit_agent_work_order_result(work_id, AgentWorkOrderResultRequest(
        status=AgentResultStatus.COMPLETED, concise_summary='The last carried Worker completed after the Leader snapshot.'))
    assert service.production_manager.apply_completion_plan(prepared, AgentPlanProposal(
        summary='Wait for the carried result visible in my input.', steps=[], carried_work_order_ids=[work_id]))
    snapshot = service.agent_runtime.snapshot(run_id)
    assert snapshot.current_plan is not None and snapshot.current_plan.steps == []
    assert snapshot.current_plan.carried_work_order_ids == [] and not snapshot.current_plan.goal_complete
    calls: list[dict[str, Any]] = []

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        assert payload['current_plan']['steps'] == [] and not payload['current_plan']['goal_complete']
        return _plan()

    client = HarnessLanguageClient(finish)
    restarted = ShortDramaService(database_path=tmp_path / 'studio.sqlite3', workspace_root=tmp_path / 'media',
        language_provider_factory=lambda _key, _url: client)
    restarted.replan_agent_run(run_id, AgentRunReplanRequest(reason='The last real result is now complete.'))
    assert len(calls) == 1
    assert restarted.agent_runtime.snapshot(run_id).current_plan.goal_complete
    assert len(restarted.agent_runtime.store.list_work_orders(run_id)) == 1
    # Only the persisted current-state view bypasses authored proposal validation.
    with pytest.raises(ValidationError, match='at least one next step'):
        AgentPlanProposal(summary='Invalid new incomplete proposal.', steps=[], carried_work_order_ids=[], goal_complete=False)


def test_real_r5_graph_has_finished_local_read_but_inherited_worker_is_running() -> None:
    import json
    from short_drama.agent_runtime import ActionRecord, PlanRevision

    data = json.loads((Path(__file__).parent / 'fixtures/fresh2_failed_plan_local_read.json').read_text())
    plan = PlanRevision.model_validate(data['plan'])
    actions = [ActionRecord.model_validate(item) for item in data['actions']]
    graph = PlanGraph(plan)
    assert graph.ready_steps(actions) == [] and not graph.is_complete(actions)
    assert graph.has_completed_local_work(actions)
    running = [a for a in actions if graph.owns_action(a) and a.status == ActionStatus.RUNNING]
    assert [a.work_order_id for a in running] == ['work-order-3b8fa566b63c48058ab82bf41539988e']
    assert running[0].id in plan.inherited_action_ids
    local = next(a for a in actions if a.id == 'action-97ad3ec442484cfbaee920048833445e')
    assert local.status == ActionStatus.SUCCEEDED and local.id not in plan.inherited_action_ids
    # A still-unfinished local READ/dependency remains a genuine wait.
    unfinished = [a.model_copy(update={'status': ActionStatus.RUNNING}) if a.id == local.id else a for a in actions]
    assert not graph.has_completed_local_work(unfinished)
