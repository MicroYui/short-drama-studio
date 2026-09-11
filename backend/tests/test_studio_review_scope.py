"""Review pauses the changed objects and their dependent work, not a project."""

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime.models import ActionKind, ActionRecord, ActionStatus, ArtifactRef, PlanStep, WorkOrder
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import ProductionDocument, ProductionDocumentOrigin, ProductionDocumentVersion, ProjectDocument
from short_drama.studio_models import StudioChangeSet, StudioTextChange
from short_drama.studio_review_scope import pending_review_scope


def _document() -> ProjectDocument:
    document = build_dont_trust_me_fixture("2026-09-11T00:00:00+00:00")
    shot = document.shots[0]
    document.shots = [shot.model_copy(update={"id": name, "scene_id": "scene-a" if name != "shot-c" else "scene-b"})
                      for name in ("shot-a", "shot-b", "shot-c")]
    document.production_documents = []
    for kind, key, groups in (
        ("storyboard", "clip_groups", [{"id": "group-ab", "shot_ids": ["shot-a", "shot-b"]}]),
        ("video.generation-plan", "units", [{"id": "unit-ab", "shot_ids": ["shot-a", "shot-b"]}, {"id": "unit-c", "shot_ids": ["shot-c"]}]),
    ):
        payload: dict[str, JsonValue] = {key: groups}
        old_payload: dict[str, JsonValue] = {key: [{"id": "old-group", "shot_ids": ["shot-a"]}]}
        document.production_documents.append(ProductionDocument(
            id=kind, project_id=document.project.id, kind=kind, title=kind, current_version_id="current",
            versions=[ProductionDocumentVersion(id="old", version=1, origin=ProductionDocumentOrigin.AGENT,
                payload=old_payload, created_at="earlier"), ProductionDocumentVersion(id="current", version=2,
                origin=ProductionDocumentOrigin.AGENT, payload=payload, created_at="now")],
        ))
    return document


def _change(document: ProjectDocument, *, scene: bool = False, affected: list[str] | None = None,
            run_id: str = "run", status: str = "pending", producer: str | None = None) -> None:
    item = StudioTextChange.model_validate({"id": f"choice-{len(document.studio_changes)}", "target": "scene" if scene else "shot",
        "target_id": "scene-a" if scene else "shot-a", "field": "screenplay_text" if scene else "visual",
        "title": "调整当前文字", "before": "原文", "after": "修改", "affected_shot_ids": affected, "status": status})
    change = StudioChangeSet(id=f"change-{len(document.studio_changes)}", project_id=document.project.id, run_id=run_id,
        work_order_id=producer, title="定向修改", items=[item], created_at="now")
    document.studio_changes.append(change.model_dump(mode="json"))


def _work(document: ProjectDocument, name: str, *, target: str | None = None, inputs: dict[str, JsonValue] | None = None) -> WorkOrder:
    return WorkOrder(id=name, parent_run_id="run", sender="leader", recipient_role="same-video-role",
        objective=f"完成 {name}", why_now="当前任务", task_inputs=inputs or {},
        scope=[] if target is None else [ArtifactRef(project_id=document.project.id, artifact_type="shot", artifact_id=target)])


def _step(name: str, *, dependencies: list[str] | None = None, target: str | None = None) -> PlanStep:
    return PlanStep(id=name, title=name, kind=ActionKind.DELEGATION, assigned_agent="worker", instructions=name,
        depends_on=dependencies or [], arguments={} if target is None else {"request": {"unit_id": target}})


def _action(work: str, step: str) -> ActionRecord:
    return ActionRecord(id=f"action-{work}", run_id="run", kind=ActionKind.DELEGATION, status=ActionStatus.RUNNING,
        agent_id="leader", plan_step_id=step, work_order_id=work, input_summary="Current delegation")


def test_pending_shot_expands_current_group_and_transitive_dependencies() -> None:
    document = _document()
    _change(document, producer="writer")
    works = [_work(document, "writer"), _work(document, "group", target="group-ab"),
             _work(document, "unit", inputs={"request": {"targets": ["unit-ab"]}}),
             _work(document, "conform"), _work(document, "export"),
             _work(document, "independent", target="unit-c"), _work(document, "old", target="old-group")]
    # Reverse order verifies closure is not tied to the plan's list order.
    steps = [_step("export-step", dependencies=["conform-step"]), _step("conform-step", dependencies=["unit-step"]),
             _step("unit-step", target="unit-ab"), _step("group-step"), _step("writer-step"), _step("independent-step")]
    actions = [_action(work, f"{work}-step") for work in ("writer", "group", "unit", "conform", "export", "independent")]
    scope = pending_review_scope(document, "run", works, steps, actions)
    assert scope.pending_count == 1
    assert scope.work_order_ids == {"writer", "group", "unit", "conform", "export"}
    assert scope.step_ids == {"writer-step", "group-step", "unit-step", "conform-step", "export-step"}


@pytest.mark.parametrize(("affected", "blocked"), [
    (None, {"scene", "a", "b", "group"}), ([], {"scene"}), (["shot-a"], {"scene", "a", "group"}),
])
def test_scene_review_respects_explicit_no_visual_change(affected: list[str] | None, blocked: set[str]) -> None:
    document = _document()
    _change(document, scene=True, affected=affected)
    works = [_work(document, name, target=target) for name, target in
             (("scene", "scene-a"), ("a", "shot-a"), ("b", "shot-b"), ("c", "shot-c"), ("group", "group-ab"))]
    scope = pending_review_scope(document, "run", works, [], [])
    assert scope.work_order_ids == blocked
    assert scope.pending_count == 1


def test_decided_changes_and_another_run_do_not_block_current_work() -> None:
    document = _document()
    _change(document, run_id="previous-run", producer="writer")
    _change(document, status="accepted")
    _change(document, status="rejected")
    scope = pending_review_scope(document, "run", [_work(document, "writer", target="shot-a")], [_step("current", target="unit-ab")], [])
    assert scope.pending_count == 0 and not scope.work_order_ids and not scope.step_ids
