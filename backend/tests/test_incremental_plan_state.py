"""Incremental plan ownership preserves completed and in-flight work."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.agent_runtime import (
    ActionKind, AgentRuntime, AgentRuntimeStore, DelegationStatus,
    PlanRevision, PlanStep, WorkOrderNextOwner,
)
from short_drama.agent_runtime.plan_graph import PlanGraph


def _step(step_id: str, *dependencies: str) -> PlanStep:
    return PlanStep(id=step_id, title=step_id, kind=ActionKind.DELEGATION,
        assigned_agent="worker", instructions=f"Complete {step_id}", depends_on=list(dependencies))


def _runtime(tmp_path: Path) -> tuple[AgentRuntimeStore, AgentRuntime, str]:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project", objective="Produce the current film", root_agent="leader")
    return store, runtime, run.id


def _action(runtime: AgentRuntime, plan: PlanRevision, step_id: str):
    return runtime.begin_action(plan.run_id, kind=ActionKind.DELEGATION, agent_id="worker",
        input_summary=step_id, plan_revision_id=plan.id, plan_step_id=step_id)


def test_incremental_plan_reuses_completed_and_running_actions_after_restart(tmp_path: Path) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    first = runtime.add_plan(run_id, summary="Prepare independent images", steps=[_step("image-a"), _step("image-b")])
    done = _action(runtime, first, "image-a")
    runtime.complete_action(done.id, output_summary="Image A is ready")
    running = _action(runtime, first, "image-b")
    order = runtime.create_work_order(run_id, sender="leader", recipient_role="builder",
        objective="Generate image B", why_now="Its inputs are ready", source_plan_revision_id=first.id,
        source_action_id=running.id)
    order.status = DelegationStatus.IN_PROGRESS
    order.next_owner = WorkOrderNextOwner.WORKER_TOOL
    order.working_notes = "Submitted the image task; preserve its identity"
    store.save_work_order(order)
    second = runtime.replace_plan(run_id, summary="Use A while B continues",
        steps=[*first.steps, _step("video-a", "image-a"), _step("video-b", "image-b")],
        inherited_action_ids=[done.id, running.id], carried_work_order_ids=[order.id], updated_work_orders=[order])
    assert order.active_plan_revision_id == first.id  # Caller snapshots are not rewritten.

    reopened = AgentRuntimeStore(store.database_path)
    runtime = AgentRuntime(reopened)
    current = reopened.get_run(run_id)
    carried = reopened.get_work_order(order.id)
    assert current is not None and current.current_plan_revision_id == second.id
    assert carried is not None and carried.active_plan_revision_id == second.id
    assert carried.source_plan_revision_id == first.id and carried.source_action_id == running.id
    assert carried.status == DelegationStatus.IN_PROGRESS and carried.next_owner == WorkOrderNextOwner.WORKER_TOOL
    assert carried.working_notes == order.working_notes
    assert [step.id for step in runtime.ready_steps(run_id)] == ["video-a"]
    assert all(action.plan_revision_id == first.id for action in reopened.list_actions(run_id))
    assert not PlanGraph(second).is_complete(reopened.list_actions(run_id))

    video_a = _action(runtime, second, "video-a")
    runtime.complete_action(video_a.id, output_summary="Video A is ready")
    assert runtime.ready_steps(run_id) == []  # In-flight image B is not dispatched again.
    runtime.complete_action(running.id, output_summary="Image B is now ready")
    assert [step.id for step in runtime.ready_steps(run_id)] == ["video-b"]
    video_b = _action(runtime, second, "video-b")
    runtime.complete_action(video_b.id, output_summary="Video B is ready")
    assert PlanGraph(second).is_complete(reopened.list_actions(run_id))


def test_plan_only_inherits_explicit_actions_on_retained_steps(tmp_path: Path) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    original = runtime.add_plan(run_id, summary="First version", steps=[_step("asset")])
    action = _action(runtime, original, "asset")
    action = runtime.complete_action(action.id, output_summary="Old asset complete")
    replacement = runtime.replace_plan(run_id, summary="Deliberately redo asset", steps=[_step("asset")])
    graph = PlanGraph(replacement)
    assert not graph.owns_action(action)
    assert [step.id for step in graph.ready_steps(store.list_actions(run_id))] == ["asset"]
    assert not graph.is_complete([action])
    carried = replacement.model_copy(update={"inherited_action_ids": [action.id]})
    graph = PlanGraph(carried)
    assert graph.owns_action(action) and graph.is_complete([action])
    assert not graph.owns_action(action.model_copy(update={"run_id": "another-run"}))
    assert not graph.owns_action(action.model_copy(update={"plan_step_id": "removed-step"}))
    assert not graph.owns_action(action.model_copy(update={"id": "another-historical-action"}))


def test_failed_inherited_action_does_not_implicitly_retry(tmp_path: Path) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    first = runtime.add_plan(run_id, summary="One attempt", steps=[_step("image")])
    action = _action(runtime, first, "image")
    runtime.fail_action(action.id, error_message="Provider rejected request")
    second = runtime.revise_plan(run_id, summary="Preserve failure while planning another task",
        steps=[*first.steps, _step("independent"), _step("dependent", "image")], inherited_action_ids=[action.id])
    assert [step.id for step in runtime.ready_steps(run_id)] == ["independent"]
    assert not PlanGraph(second).is_complete(store.list_actions(run_id))


def test_plan_and_all_carried_orders_roll_back_when_one_order_is_missing(tmp_path: Path) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    first = runtime.add_plan(run_id, summary="Original", steps=[_step("image")])
    order = runtime.create_work_order(run_id, sender="leader", recipient_role="builder",
        objective="Make image", why_now="Ready", source_plan_revision_id=first.id)
    missing = order.model_copy(update={"id": "missing-order"})
    with pytest.raises(KeyError, match="missing-order"):
        runtime.replace_plan(run_id, summary="Must roll back", steps=first.steps,
            carried_work_order_ids=[order.id, missing.id], updated_work_orders=[order, missing])
    reopened = AgentRuntimeStore(store.database_path)
    current = reopened.get_run(run_id)
    retained = reopened.get_work_order(order.id)
    assert current is not None and current.current_plan_revision_id == first.id
    assert retained == order and reopened.get_work_order(missing.id) is None
    assert len(reopened.list_plan_revisions(run_id)) == 1


def test_append_preserves_discovery_and_consumed_completions_from_another_store(tmp_path: Path) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    first = runtime.add_plan(run_id, summary="Original", steps=[_step("image")])
    order = runtime.create_work_order(run_id, sender="leader", recipient_role="builder",
        objective="Make image", why_now="Ready", source_plan_revision_id=first.id)
    stale_run = store.get_run(run_id)
    assert stale_run is not None
    other = AgentRuntimeStore(store.database_path)
    other.remember_tool_schema_ids(run_id, ["image.enqueue"])
    other.remember_tool_schema_ids(run_id, ["image.advance"], work_order_id=order.id)
    consumed = other.get_run(run_id)
    assert consumed is not None
    consumed.completion_planned_work_order_ids = ["completed-a"]
    other.save_run(consumed)

    second = PlanRevision(id="next-plan", run_id=run_id, revision=2, summary="New work", steps=first.steps)
    stale_run.current_plan_revision_id = second.id
    stale_run.completion_planned_work_order_ids = ["completed-b"]
    carried = order.model_copy(update={"active_plan_revision_id": second.id})
    store.append_plan_revision(second, stale_run, updated_work_orders=[carried])
    saved = other.get_run(run_id)
    saved_order = other.get_work_order(order.id)
    assert saved is not None and saved.loaded_tool_schema_ids == ["image.enqueue"]
    assert saved.completion_planned_work_order_ids == ["completed-a", "completed-b"]
    assert saved_order is not None and saved_order.loaded_tool_schema_ids == ["image.advance"]
    assert saved_order.active_plan_revision_id == second.id
    stale_run.completion_planned_work_order_ids = []
    store.save_run(stale_run)
    saved_again = other.get_run(run_id)
    assert saved_again is not None and saved_again.completion_planned_work_order_ids == ["completed-a", "completed-b"]


@pytest.mark.parametrize("scope", ["missing", "another-run", "removed-step"])
def test_invalid_inherited_actions_do_not_append_a_plan(tmp_path: Path, scope: str) -> None:
    store, runtime, run_id = _runtime(tmp_path)
    first = runtime.add_plan(run_id, summary="Original", steps=[_step("image")])
    if scope == "another-run":
        foreign = runtime.create_run(project_id="other", objective="Another film", root_agent="leader")
        action_plan = runtime.add_plan(foreign.id, summary="Other plan", steps=[_step("image")])
    else:
        action_plan = first
    action = _action(runtime, action_plan, "image")
    with pytest.raises(ValueError, match="Inherited actions must exist"):
        runtime.replace_plan(run_id, summary="Invalid inheritance",
            steps=[_step("replacement")] if scope == "removed-step" else first.steps,
            inherited_action_ids=["missing-action" if scope == "missing" else action.id])
    assert len(store.list_plan_revisions(run_id)) == 1


def test_plan_inheritance_defaults_and_ids_remain_compatible() -> None:
    plan = PlanRevision(id="plan", run_id="run", revision=1, summary="Legacy plan")
    assert plan.inherited_action_ids == []
    for ids in ([""], [" action"], ["action", "action"]):
        with pytest.raises(ValidationError, match="trimmed and unique"):
            PlanRevision(id="plan", run_id="run", revision=1, summary="Plan", inherited_action_ids=ids)
