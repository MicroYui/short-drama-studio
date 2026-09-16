"""Persisted failed delegation + independent ready work must return to the Leader."""

from __future__ import annotations

from pathlib import Path
import json
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import ActionKind, ActionStatus, ArtifactRef, DelegationStatus, PlanGraph, PlanStep, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrderNextOwner
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary
from short_drama.production_manager import AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioJobControl, StudioMessage, StudioScope
from tests.test_studio_harness import HarnessLanguageClient, _done, _plan, _service, _wait_job


def _read(project_id: str, step_id: str) -> PlanStep:
    return PlanStep(id=step_id, title=step_id, kind=ActionKind.TOOL_CALL,
        assigned_agent="Leader", instructions="Read current capability without submitting media.",
        tool_name="route.h3_capability_profile", arguments={"project_id": project_id})


def _delegate(step_id: str, refs: list[ArtifactRef] | None = None) -> PlanStep:
    return PlanStep(id=step_id, title=step_id, kind=ActionKind.DELEGATION,
        assigned_agent=step_id, production_skill_role=ProductionSkillRole.ASSET_BUILDER,
        instructions="Use exactly the saved reference for this bounded pre-video task.",
        artifact_refs=refs or [])


def _failed_plan(service: ShortDramaService, project_id: str) -> tuple[str, str, str]:
    version = service.get_project(project_id).document.assets[0].versions[0]
    ref = ArtifactRef(project_id=project_id, artifact_type="asset_version", artifact_id=version.id)
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective="Complete only pre-video preparation.",
        initial_plan=AgentPlanProposal(summary="Original r11-shaped plan.", steps=[
            _delegate("existing-worker"), _read(project_id, "already-read"),
            _delegate("complete_remaining_asset_preparation_slices", [ref]),
            _read(project_id, "read_h3_preparation_capability")])) )
    run_id = view.snapshot.run.id
    advanced = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=2),
        advance_workers=False, allow_replanning=False)
    work_id = advanced.created_work_order_ids[0]
    plan = service.agent_runtime.snapshot(run_id).current_plan
    assert plan is not None
    # Replay the saved pre-fix terminal Action, not a new Provider call or live DB edit.
    action = service.agent_runtime.start_action(run_id, kind=ActionKind.DELEGATION,
        agent_id="Leader", input_summary="Use the precise saved asset UUIDs.",
        plan_revision_id=plan.id, plan_step_id="complete_remaining_asset_preparation_slices")
    service.agent_runtime.fail_action(action.id, error_kind="InvalidContextManifest",
        error_message="Context manifest requires an explicit version for asset_version:" + version.id)
    return run_id, work_id, action.id


def test_restart_failed_delegation_and_ready_read_replans_without_starving_worker(tmp_path: Path) -> None:
    leader_started = threading.Event()
    worker_id: list[str] = []
    repaired = threading.Event()

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" in payload:
            if payload["work_order"]["id"] == worker_id[0]:
                assert leader_started.wait(8), "Failed plan with a ready read never reached the Leader"
            return _done("Saved pre-video work completed; no video was submitted.")
        if not repaired.is_set():
            leader_started.set()
            reason = payload.get("reason", "")
            assert "InvalidContextManifest" in reason
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                work = service.agent_runtime.store.get_work_order(worker_id[0])
                assert work is not None
                if work.status == DelegationStatus.COMPLETED:
                    break
                threading.Event().wait(0.01)
            assert work.status == DelegationStatus.COMPLETED, "Slow Leader planning held the Worker save lock"
            current = service.agent_runtime.snapshot(run_id).current_plan
            assert current is not None
            fixed = next(step for step in current.steps if step.id == "complete_remaining_asset_preparation_slices")
            fixed = fixed.model_copy(update={"depends_on": ["already-read"]})
            repaired.set()
            return AgentPlanProposal(summary="Repair the failed context and retain the independent read.",
                steps=[fixed, _read(project_id, "read_h3_preparation_capability")]).model_dump(mode="json")
        return _plan()

    client = HarnessLanguageClient(respond)
    initial, project_id = _service(tmp_path, client)
    run_id, old_work_id, failed_id = _failed_plan(initial, project_id)
    work = initial.agent_runtime.store.get_work_order(old_work_id)
    assert work is not None
    original_action_id = work.source_action_id
    work.status = DelegationStatus.NEEDS_ATTENTION
    work.next_owner = WorkOrderNextOwner.WORKER_TOOL
    work.specialist_turns.append(SpecialistTurnRecord(id="original-requested-tool", sequence=1,
        tool_name="route.h3_capability_profile", arguments={"project_id": project_id}, reason="Saved pending read."))
    initial.agent_runtime.store.save_work_order(work)
    worker_id.append(old_work_id)
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: client)
    job = StudioJob(id="failed-plan-restart", project_id=project_id, run_id=run_id,
        objective="Complete only pre-video preparation.", scope=StudioScope(), status="paused",
        created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
    _wait_job(service, job.id, "completed", timeout=16)
    assert repaired.is_set()
    saved = service.agent_runtime.store.get_work_order(old_work_id)
    assert saved is not None and saved.status == DelegationStatus.COMPLETED
    assert saved.source_action_id == original_action_id
    assert len(saved.specialist_turns) == 1 and saved.specialist_turns[0].id == "original-requested-tool"
    assert saved.specialist_turns[0].status == SpecialistTurnStatus.COMPLETED
    actions = service.agent_runtime.store.list_actions(run_id)
    assert next(action for action in actions if action.id == failed_id).status == ActionStatus.FAILED
    assert len([action for action in actions if action.plan_step_id == "already-read"]) == 1
    assert len([action for action in actions if action.plan_step_id == "read_h3_preparation_capability"]) == 1
    assert len([action for action in actions if action.plan_step_id == "complete_remaining_asset_preparation_slices"]) == 2
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 2
    assert not service.get_project(project_id).tasks


@pytest.mark.parametrize("change", ["user", "plan", "failure", "worker_user_input"])
def test_failed_plan_response_is_discarded_after_current_state_changes(tmp_path: Path, change: str) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run_id, work_id, _failed_id = _failed_plan(service, project_id)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_failed_plan=True)
    assert prepared is not None and prepared.failed_plan_state
    if change == "user":
        run = service.agent_runtime.store.get_run(run_id)
        assert run is not None
        run.latest_user_instructions = ["Keep the current result and wait."]
        service.agent_runtime.store.save_run(run)
    elif change == "plan":
        service.replan_agent_run(run_id, AgentRunReplanRequest(reason="A newer explicit plan.",
            proposed_plan=AgentPlanProposal(summary="Wait for the original Worker.", steps=[], carried_work_order_ids=[work_id])))
    elif change == "worker_user_input":
        work = service.agent_runtime.store.get_work_order(work_id)
        assert work is not None
        work.next_owner = WorkOrderNextOwner.USER_INPUT
        service.agent_runtime.store.save_work_order(work)
    else:
        action = service.agent_runtime.start_action(run_id, kind=ActionKind.TOOL_CALL, agent_id="Leader",
            input_summary="Second actual failure.", plan_revision_id=prepared.plan_revision_id,
            plan_step_id="read_h3_preparation_capability")
        service.agent_runtime.fail_action(action.id, error_message="The current source changed.")
    before = service.agent_runtime.snapshot(run_id).current_plan
    assert not service.production_manager.apply_completion_plan(prepared,
        AgentPlanProposal(summary="Old response must not apply.", steps=[], carried_work_order_ids=[work_id]))
    assert service.agent_runtime.snapshot(run_id).current_plan == before


def test_failed_plan_empty_wait_is_consumed_across_restart(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    run_id, work_id, failed_id = _failed_plan(service, project_id)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_failed_plan=True)
    assert prepared is not None
    assert service.production_manager.apply_completion_plan(prepared,
        AgentPlanProposal(summary="Wait for the existing Worker result.", steps=[], carried_work_order_ids=[work_id]))
    restarted = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    snapshot = restarted.agent_runtime.snapshot(run_id)
    assert snapshot.current_plan is not None and snapshot.current_plan.steps == []
    assert not PlanGraph(snapshot.current_plan).owns_action(next(action for action in snapshot.actions if action.id == failed_id))
    assert restarted.production_manager.prepare_completion_planning(run_id,
        include_local_plan_completion=True, include_failed_plan=True) is None


def test_last_carried_worker_returning_to_leader_can_replan_an_empty_wait(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        requests.append(payload)
        current = payload["current_plan"]
        assert current["steps"] == [] and current["carried_work_order_ids"] == []
        assert "LEADER_REPLAN" in str(payload["reason"])
        return AgentPlanProposal(summary="Inspect the reported failure before continuing.",
            steps=[_read(project_id, "inspect-failure")]).model_dump(mode="json")

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective="Finish the existing video work.",
        initial_plan=AgentPlanProposal(summary="A single worker.", steps=[_delegate("video-worker")])) )
    run_id = view.snapshot.run.id
    advanced = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1),
        advance_workers=False, allow_replanning=False)
    work_id = advanced.created_work_order_ids[0]
    service.replan_agent_run(run_id, AgentRunReplanRequest(reason="Wait for the same worker.",
        proposed_plan=AgentPlanProposal(summary="Second shot is being prepared.", carried_work_order_ids=[work_id])))
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.status, work.next_owner = DelegationStatus.NEEDS_ATTENTION, WorkOrderNextOwner.LEADER_REPLAN
    service.agent_runtime.store.save_work_order(work)
    prepared = service.production_manager.prepare_completion_planning(run_id, include_failed_plan=True)
    assert prepared is not None and prepared.failed_plan_state
    proposal = service.production_manager.plan_after_completions(prepared)
    assert service.production_manager.apply_completion_plan(prepared, proposal)
    assert len(requests) == 1
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 1
    assert not service.get_project(project_id).tasks
    # Authored empty/incomplete plans must still be rejected, even though the
    # truthful historical state above can be supplied to the next decision.
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="incomplete Agent plan"):
        AgentPlanProposal.model_validate_json(json.dumps({"summary": "No executable work.", "goal_complete": False}))


@pytest.mark.parametrize("control", ["pause", "message"])
def test_failed_plan_background_decision_obeys_real_user_boundary(tmp_path: Path, control: str) -> None:
    started, release = threading.Event(), threading.Event()
    yielded: list[str] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if "work_order" in payload:
            return _done("Existing saved work is complete.")
        if not payload.get("goal", {}).get("latest_user_instructions"):
            started.set()
            assert release.wait(8)
            try:
                execution_boundary()
            except ExecutionYielded:
                yielded.append(control)
                raise
            pytest.fail("The old failed-plan response crossed the current user boundary")
        return _plan()

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    run_id, _work_id, _failed_id = _failed_plan(service, project_id)
    job = StudioJob(id="failed-plan-control", project_id=project_id, run_id=run_id,
        objective="Complete only pre-video preparation.", scope=StudioScope(), status="paused",
        created_at="2026-09-12T00:00:00+00:00", updated_at="2026-09-12T00:00:00+00:00")
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action="resume"))
    try:
        assert started.wait(8)
        if control == "pause":
            service.studio_runtime.control(job.id, StudioJobControl(action="pause"))
        else:
            service.studio_runtime.send(project_id, StudioMessage(text="Stop adding work and deliver current results."))
        assert service.repository.list_studio_jobs(project_id)[0].status != "paused"
    finally:
        release.set()
    _wait_job(service, job.id, "paused" if control == "pause" else "completed")
    assert yielded == [control]
    assert len(service.agent_runtime.store.list_work_orders(run_id)) == 1


@pytest.mark.parametrize("where", ["scope", "adjacent"])
def test_immutable_version_uuid_is_canonicalized_before_manifest_on_restart(tmp_path: Path, where: str) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    document = service.get_project(project_id).document
    asset = document.assets[0]
    version = asset.versions[0]
    newer = version.model_copy(update={"id": "newer-current-uuid", "version": version.version + 10})
    asset.versions.append(newer)
    asset.current_version_id = newer.id
    service.repository.save_project(document)
    ref = ArtifactRef(project_id=project_id, artifact_type="asset_version", artifact_id=version.id)
    step = _delegate("use-saved-version", [ref] if where == "scope" else [])
    if where == "adjacent":
        step.arguments = {"adjacent_boundaries": [{"relationship": "preceding", "artifact_refs": [ref.model_dump(mode="json")]}]}
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective="Use the selected saved media.",
        initial_plan=AgentPlanProposal(summary="Saved old receipt with UUID and no number.", steps=[step])))
    original_plan = view.snapshot.current_plan
    restarted = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    result = restarted.advance_agent_run(view.snapshot.run.id, AgentRunAdvanceRequest(max_actions=1),
        advance_workers=False, allow_replanning=False)
    assert len(result.created_work_order_ids) == 1
    work = restarted.agent_runtime.store.get_work_order(result.created_work_order_ids[0])
    assert work is not None
    manifest = work.context_manifest
    assert manifest is not None
    refs = work.scope if where == "scope" else manifest.adjacent_boundaries[0].artifact_refs
    assert refs == [ref.model_copy(update={"version": version.version})]
    assert restarted.agent_runtime.snapshot(view.snapshot.run.id).current_plan == original_plan


@pytest.mark.parametrize("kind", ["wrong_number", "missing_uuid", "mutable_asset", "mutable_shot"])
def test_canonicalization_does_not_guess_or_relax_other_references(tmp_path: Path, kind: str) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    asset = service.get_project(project_id).document.assets[0]
    version = asset.versions[0]
    ref = ArtifactRef(project_id=project_id, artifact_type="shot" if kind == "mutable_shot" else "asset" if kind == "mutable_asset" else "asset_version",
        artifact_id=service.get_project(project_id).document.shots[0].id if kind == "mutable_shot" else asset.id if kind == "mutable_asset" else "missing-uuid" if kind == "missing_uuid" else version.id,
        version=999 if kind == "wrong_number" else None)
    # Old persisted plans can predate validation at plan-save time; the actual
    # WorkOrder boundary must still reject a wrong identity or a mutable null.
    run = service.agent_runtime.create_run(project_id=project_id, objective="Use the specified context.", root_agent="Leader")
    service.agent_runtime.replace_plan(run.id, summary="Legacy plan.", steps=[_delegate("invalid-ref", [ref])])
    result = service.advance_agent_run(run.id, AgentRunAdvanceRequest(max_actions=1),
        advance_workers=False, allow_replanning=False)
    if kind == "mutable_asset":
        # An object reference is allowed by the existing contract, but is never
        # silently converted into its current AssetVersion.
        assert len(result.created_work_order_ids) == 1
        saved = service.agent_runtime.store.get_work_order(result.created_work_order_ids[0])
        assert saved is not None and saved.scope == [ref]
        return
    assert not result.created_work_order_ids
    failed = [action for action in result.run.snapshot.actions if action.status == ActionStatus.FAILED]
    assert len(failed) == 1 and failed[0].error_kind == "InvalidContextManifest"
