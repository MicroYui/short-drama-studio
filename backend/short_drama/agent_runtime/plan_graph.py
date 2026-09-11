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
        self._step_ids = frozenset(step.id for step in revision.steps)
        self._inherited_action_ids = frozenset(revision.inherited_action_ids)

    def owns_action(self, action: ActionRecord) -> bool:
        """Match exact carried actions without rewriting their original revision."""
        return (
            action.run_id == self.revision.run_id
            and action.plan_step_id in self._step_ids
            and (action.plan_revision_id == self.revision.id
                 or action.id in self._inherited_action_ids)
        )

    def ready_steps(self, actions: list[ActionRecord]) -> list[PlanStep]:
        """Return unfinished steps whose dependencies have succeeded."""

        relevant_actions = [
            action
            for action in actions
            if self.owns_action(action)
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
            if self.owns_action(action)
            and action.status == ActionStatus.SUCCEEDED
        }
        return all(step.id in completed for step in self.revision.steps)
