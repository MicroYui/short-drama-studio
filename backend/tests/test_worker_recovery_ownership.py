"""Restart reconciles current WorkOrders, without replaying older handoffs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from short_drama.agent_runtime import (
    ActionKind, ActionStatus, AgentResultStatus, AgentRunStatus, DelegationStatus,
    PlanStep, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrderNextOwner,
)
from short_drama.agent_runtime.models import utc_now
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest, ProductionManagerError,
)
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole
from short_drama.providers.openai_compatible_language_client import LanguageProviderIncompleteError
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioScope
from tests.test_studio_harness import HarnessLanguageClient, _done, _service


def _delegate(service: ShortDramaService, project_id: str) -> tuple[str, str, str]:
    run = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Finish the current pre-video review; do not submit video.",
        initial_plan=AgentPlanProposal(summary="One bounded current task.", goal_complete=True, steps=[
            PlanStep(id="director", title="Director", kind=ActionKind.DELEGATION,
                     assigned_agent="director", production_skill_role=ProductionSkillRole.DIRECTOR,
                     production_prompt_stage=ProductionPromptStage.STORYBOARD,
                     instructions="Read current content and finish this bounded review."),
        ]),
    ))
    run_id = run.snapshot.run.id
    advanced = service.advance_agent_run(
        run_id, AgentRunAdvanceRequest(max_actions=1), advance_workers=False,
    )
    work_id = advanced.created_work_order_ids[0]
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.source_action_id is not None
    return run_id, work_id, work.source_action_id


def _reopen(service: ShortDramaService, client: HarnessLanguageClient) -> ShortDramaService:
    return ShortDramaService(
        database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: client,
    )


def test_restart_preserves_real_incomplete_handoff_after_successful_tool_and_pause(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if len(client.inputs) == 1:
            return {"tool_name": "artifact.read_current", "reason": "Read the current objects.",
                    "arguments": {"project_id": payload["context"]["project_id"]}}
        raise LanguageProviderIncompleteError(reason="max_output_tokens", partial_output_characters=9000)

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    run_id, work_id, action_id = _delegate(service, project_id)
    service.execute_agent_work_order(work_id)  # Persist the actual Tool decision.
    service.execute_agent_work_order(work_id)  # Execute once, save Tool success and PARTIAL.
    old_result = service.agent_runtime.store.get_agent_result(work_id)
    assert old_result is not None and old_result.next_owner == WorkOrderNextOwner.WORKER_DECISION
    with pytest.raises(ProductionManagerError, match="incomplete: max_output_tokens"):
        service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert work.updated_at > old_result.created_at
    saved_turn = work.specialist_turns[0]
    assert saved_turn.result is not None and saved_turn.result["status"] == "SUCCEEDED"
    service.repository.save_studio_job(StudioJob(
        id="paused-after-incomplete", project_id=project_id, run_id=run_id,
        objective="Keep the pre-video scope.", scope=StudioScope(), status="paused",
        created_at=utc_now().isoformat(), updated_at=utc_now().isoformat(),
    ))

    reopened = _reopen(service, client)
    reopened.studio_runtime.resume_after_restart()
    recovered = reopened.agent_runtime.store.get_work_order(work_id)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert recovered is not None and action is not None
    assert recovered.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert recovered.status == DelegationStatus.NEEDS_ATTENTION
    assert recovered.specialist_turns == work.specialist_turns
    assert reopened.agent_runtime.store.get_agent_result(work_id) == old_result
    assert action.status == ActionStatus.FAILED
    assert action.error_kind == "RecoveredSpecialistNeedsLeaderReplan"
    assert "incomplete: max_output_tokens" in (action.error_message or "")
    assert not reopened.advance_agent_team(run_id).selected_work_order_ids
    assert len(client.inputs) == 2
    assert reopened.repository.list_studio_jobs(project_id)[0].status == "paused"
    assert not reopened.get_project(project_id).tasks


@pytest.mark.parametrize("owner", [WorkOrderNextOwner.LEADER_REPLAN, WorkOrderNextOwner.USER_INPUT])
@pytest.mark.parametrize("missing_link", [False, True])
def test_restart_preserves_new_explicit_owner_without_overwriting_its_progress(
    tmp_path: Path, owner: WorkOrderNextOwner, missing_link: bool,
) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, action_id = _delegate(service, project_id)
    service.agent_runtime.record_agent_result(work_id, status=AgentResultStatus.PARTIAL,
        concise_summary="The earlier local Tool succeeded.", next_owner=WorkOrderNextOwner.WORKER_DECISION)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.next_owner = owner
    work.working_notes = "Preserve the latest local repair and exact selected objects."
    work.updated_at = utc_now()
    service.agent_runtime.store.save_work_order(work)
    service.agent_runtime.append_work_order_progress(work_id, author="current-owner",
        summary="Current handoff requires a smaller scope or the requested user answer.")
    if missing_link:
        action = service.agent_runtime.store.get_action(action_id)
        assert action is not None
        action.work_order_id = None
        service.agent_runtime.store.save_action(action)

    reopened = _reopen(service, client)
    saved = reopened.agent_runtime.store.get_work_order(work_id)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert saved == work
    assert action is not None and action.status == ActionStatus.FAILED
    assert action.error_kind == ("RecoveredSpecialistNeedsUserInput" if owner == WorkOrderNextOwner.USER_INPUT
                                 else "RecoveredSpecialistNeedsLeaderReplan")
    assert "Current handoff" in (action.error_message or "")
    assert not client.inputs


@pytest.mark.parametrize("in_progress", [False, True])
def test_restart_keeps_new_requested_tool_id_over_old_decision_receipt(
    tmp_path: Path, in_progress: bool,
) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, action_id = _delegate(service, project_id)
    service.agent_runtime.record_agent_result(work_id, status=AgentResultStatus.PARTIAL,
        concise_summary="The earlier read is complete.", next_owner=WorkOrderNextOwner.WORKER_DECISION)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.specialist_turns.append(SpecialistTurnRecord(
        id="already-requested-read", sequence=1, tool_name="artifact.read_current",
        arguments={"project_id": project_id}, reason="Execute the saved local read once.",
    ))
    work.next_owner = WorkOrderNextOwner.WORKER_TOOL
    work.status = DelegationStatus.IN_PROGRESS if in_progress else DelegationStatus.NEEDS_ATTENTION
    work.updated_at = utc_now()
    service.agent_runtime.store.save_work_order(work)

    reopened = _reopen(service, client)
    saved = reopened.agent_runtime.store.get_work_order(work_id)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert saved is not None and saved.next_owner == WorkOrderNextOwner.WORKER_TOOL
    assert action is not None and action.status == ActionStatus.RUNNING
    assert not client.inputs
    reopened.execute_agent_work_order(work_id)
    reopened.execute_agent_work_order(work_id)
    saved = reopened.agent_runtime.store.get_work_order(work_id)
    assert saved is not None and saved.status == DelegationStatus.COMPLETED
    assert len(saved.specialist_turns) == 1
    assert saved.specialist_turns[0].id == "already-requested-read"
    assert saved.specialist_turns[0].status == SpecialistTurnStatus.COMPLETED
    assert len(client.inputs) == 1


def test_restart_does_not_restore_tool_ownership_from_its_old_requested_receipt(tmp_path: Path) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, action_id = _delegate(service, project_id)
    service.agent_runtime.record_agent_result(work_id, status=AgentResultStatus.PARTIAL,
        concise_summary="The Tool was requested before it started.", next_owner=WorkOrderNextOwner.WORKER_TOOL)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.specialist_turns.append(SpecialistTurnRecord(
        id="interrupted-operation", sequence=1, tool_name="artifact.read_current",
        arguments={"project_id": project_id}, reason="Do not automatically replay after interruption.",
        status=SpecialistTurnStatus.IN_PROGRESS,
    ))
    work.status = DelegationStatus.IN_PROGRESS
    work.updated_at = utc_now()
    service.agent_runtime.store.save_work_order(work)

    reopened = _reopen(service, client)
    saved = reopened.agent_runtime.store.get_work_order(work_id)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert saved is not None and saved.next_owner == WorkOrderNextOwner.WORKER_DECISION
    assert action is not None and action.status == ActionStatus.RUNNING
    assert saved.specialist_turns[0].status == SpecialistTurnStatus.INTERRUPTED
    reopened.execute_agent_work_order(work_id)
    saved = reopened.agent_runtime.store.get_work_order(work_id)
    assert saved is not None and saved.specialist_turns[0].status == SpecialistTurnStatus.INTERRUPTED
    assert len(client.inputs) == 1


@pytest.mark.parametrize(("status", "owner", "action_status"), [
    (AgentResultStatus.COMPLETED, None, ActionStatus.SUCCEEDED),
    (AgentResultStatus.FAILED, None, ActionStatus.FAILED),
    (AgentResultStatus.PARTIAL, WorkOrderNextOwner.LEADER_REPLAN, ActionStatus.FAILED),
    (AgentResultStatus.PARTIAL, WorkOrderNextOwner.USER_INPUT, ActionStatus.FAILED),
    (AgentResultStatus.PARTIAL, WorkOrderNextOwner.WORKER_DECISION, ActionStatus.RUNNING),
])
def test_restart_still_reconciles_valid_saved_result_before_action_completion(
    tmp_path: Path, status: AgentResultStatus, owner: WorkOrderNextOwner | None,
    action_status: ActionStatus,
) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    run_id, work_id, action_id = _delegate(service, project_id)
    result = service.agent_runtime.record_agent_result(work_id, status=status,
        concise_summary="The saved handoff remains valid.", next_owner=owner)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None

    reopened = _reopen(service, client)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert action is not None and action.status == action_status
    assert reopened.agent_runtime.store.get_work_order(work_id) == work
    assert reopened.agent_runtime.store.get_agent_result(work_id) == result
    expected_run = AgentRunStatus.COMPLETED if status == AgentResultStatus.COMPLETED else AgentRunStatus.WAITING
    assert reopened.get_agent_run(run_id).snapshot.run.status == expected_run
    assert not client.inputs


@pytest.mark.parametrize("status", list(AgentResultStatus))
def test_restart_does_not_revive_cancelled_work_from_an_older_receipt(
    tmp_path: Path, status: AgentResultStatus,
) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, action_id = _delegate(service, project_id)
    old_result = service.agent_runtime.record_agent_result(work_id, status=status,
        concise_summary="A handoff saved before the later cancellation.")
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.status = DelegationStatus.CANCELLED
    work.updated_at = utc_now()
    service.agent_runtime.store.save_work_order(work)

    reopened = _reopen(service, client)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert action is not None and action.status == ActionStatus.CANCELLED
    assert reopened.agent_runtime.store.get_work_order(work_id) == work
    assert reopened.agent_runtime.store.get_agent_result(work_id) == old_result
    assert not client.inputs


@pytest.mark.parametrize("superseded", [False, True])
def test_stale_handoff_does_not_decide_the_current_work_orders_owner(
    tmp_path: Path, superseded: bool,
) -> None:
    client = HarnessLanguageClient(lambda _payload: _done())
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, action_id = _delegate(service, project_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    if superseded:
        work.active_plan_revision_id = "older-plan"
        work.updated_at = utc_now()
        service.agent_runtime.store.save_work_order(work)
    service.agent_runtime.record_agent_result(work_id, status=AgentResultStatus.COMPLETED,
        concise_summary="Historical handoff only.", stale_handoff=True,
        stale_reason="The original decision no longer owns the current scope.")

    reopened = _reopen(service, client)
    action = reopened.agent_runtime.store.get_action(action_id)
    assert action is not None
    assert action.status == (ActionStatus.CANCELLED if superseded else ActionStatus.RUNNING)
    assert reopened.agent_runtime.store.get_work_order(work_id) == work
    assert not client.inputs
