"""Dependency helpers for agent-authored, revisable plan graphs."""

from __future__ import annotations

from short_drama.agent_runtime.models import (
    ActionRecord,
    ActionStatus,
    PlanRevision,
    PlanStep,
)


class PlanGraph:
    """Read-only view over one validated :class:`PlanRevision`."""

    def __init__(self, revision: PlanRevision) -> None:
        self.revision = revision

    def ready_steps(self, actions: list[ActionRecord]) -> list[PlanStep]:
        """Return unfinished steps whose dependencies have succeeded."""

        relevant_actions = [
            action
            for action in actions
            if action.plan_revision_id == self.revision.id and action.plan_step_id is not None
        ]
        completed = {
            action.plan_step_id
            for action in relevant_actions
            if action.status == ActionStatus.SUCCEEDED
        }
        attempted = {
            action.plan_step_id
            for action in relevant_actions
        }
        return [
            step
            for step in self.revision.steps
            if step.id not in completed
            and step.id not in attempted
            and all(dependency in completed for dependency in step.depends_on)
        ]

    def is_complete(self, actions: list[ActionRecord]) -> bool:
        completed = {
            action.plan_step_id
            for action in actions
            if action.plan_revision_id == self.revision.id
            and action.plan_step_id is not None
            and action.status == ActionStatus.SUCCEEDED
        }
        return all(step.id in completed for step in self.revision.steps)
