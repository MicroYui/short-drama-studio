"""A pending text choice suspends its objects and dependent work only."""
from dataclasses import dataclass, field

from pydantic import JsonValue

from short_drama.agent_runtime.models import ActionRecord, PlanStep, WorkOrder
from short_drama.models import ProjectDocument
from short_drama.studio_changes import list_changes


@dataclass
class ReviewScope:
    pending_count: int = 0
    work_order_ids: set[str] = field(default_factory=set[str])
    step_ids: set[str] = field(default_factory=set[str])


def pending_review_scope(document: ProjectDocument, run_id: str, work_orders: list[WorkOrder],
                         steps: list[PlanStep], actions: list[ActionRecord]) -> ReviewScope:
    scope = ReviewScope()
    target_ids: set[str] = set()
    for change in list_changes(document):
        if change.run_id != run_id:
            continue
        pending = [item for item in change.items if item.status == "pending"]
        if not pending:
            continue
        scope.pending_count += len(pending)
        if change.work_order_id:
            scope.work_order_ids.add(change.work_order_id)
        for item in pending:
            target_ids.add(item.target_id)
            if item.target == "scene":
                target_ids.update(item.affected_shot_ids if item.affected_shot_ids is not None
                                  else [shot.id for shot in document.shots if shot.scene_id == item.target_id])
    if not scope.pending_count:
        return scope
    # Resolve stable group/unit identities from current content, not names in prose.
    for source in document.production_documents:
        if source.kind not in {"storyboard", "video.generation-plan"}:
            continue
        current = next((item for item in source.versions if item.id == source.current_version_id), None)
        if current is None:
            continue
        for key in ("clip_groups", "units"):
            groups = current.payload.get(key)
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, dict):
                        members, group_id = group.get("shot_ids"), group.get("id")
                        if isinstance(members, list) and isinstance(group_id, str):
                            if target_ids.intersection(value for value in members if isinstance(value, str)):
                                target_ids.add(group_id)

    def references_target(value: JsonValue) -> bool:
        if isinstance(value, str):
            return value in target_ids
        if isinstance(value, dict):
            return any(references_target(item) for item in value.values())
        if isinstance(value, list):
            return any(references_target(item) for item in value)
        return False

    for work in work_orders:
        if any(ref.artifact_id in target_ids for ref in work.scope) or references_target(work.task_inputs):
            scope.work_order_ids.add(work.id)
    for action in actions:
        if action.work_order_id in scope.work_order_ids and action.plan_step_id:
            scope.step_ids.add(action.plan_step_id)
    for step in steps:
        if any(ref.artifact_id in target_ids for ref in step.artifact_refs) or references_target(step.arguments):
            scope.step_ids.add(step.id)
    previous_count = -1
    while previous_count != len(scope.step_ids):
        previous_count = len(scope.step_ids)
        scope.step_ids.update(step.id for step in steps if scope.step_ids.intersection(step.depends_on))
    scope.work_order_ids.update(action.work_order_id for action in actions
        if action.plan_step_id in scope.step_ids and action.work_order_id is not None)
    return scope
