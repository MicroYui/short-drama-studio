"""Bounded context assembly for agent calls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from short_drama.agent_runtime.models import (
    ActionDigest,
    ArtifactRef,
    ArtifactSummary,
    ContextPack,
    DecisionMemory,
    ExperienceMemory,
)
from short_drama.agent_runtime.store import AgentRuntimeStore


DEFAULT_CONTEXT_CHARACTER_BUDGET = 24_000


class ArtifactResolver(Protocol):
    """Adapter implemented by the canonical project/artifact repository."""

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        """Resolve only the concise information needed by an agent call."""


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class _CharacterBudget:
    """Small approximate prompt budget for human-readable context content."""

    def __init__(self, maximum: int) -> None:
        if maximum < 256:
            raise ValueError("max_characters must be at least 256")
        self.maximum = maximum
        self.remaining = maximum

    def take(
        self,
        value: str | None,
        *,
        limit: int,
        reserve: int = 0,
    ) -> str | None:
        if value is None:
            return None
        available = min(limit, max(0, self.remaining - reserve))
        if available == 0:
            return None
        if len(value) <= available:
            selected = value
        elif available == 1:
            selected = "…"
        else:
            selected = f"{value[: available - 1].rstrip()}…"
        self.remaining -= len(selected)
        return selected


def _bounded_strings(
    budget: _CharacterBudget,
    values: Sequence[str],
    *,
    item_limit: int = 1_000,
) -> list[str]:
    bounded: list[str] = []
    for value in _unique_strings(values):
        selected = budget.take(value, limit=item_limit)
        if selected is None:
            break
        bounded.append(selected)
    return bounded


def _bounded_artifacts(
    budget: _CharacterBudget,
    values: Sequence[ArtifactSummary],
) -> list[ArtifactSummary]:
    bounded: list[ArtifactSummary] = []
    for item in values:
        required_fields = 1 if item.title is None else 2
        if budget.remaining < required_fields:
            break
        title = budget.take(item.title, limit=240, reserve=1)
        summary = budget.take(item.summary, limit=3_000)
        if summary is None or (item.title is not None and title is None):
            break
        bounded.append(item.model_copy(update={"title": title, "summary": summary}))
    return bounded


def _bounded_decisions(
    budget: _CharacterBudget,
    values: Sequence[DecisionMemory],
) -> list[DecisionMemory]:
    bounded: list[DecisionMemory] = []
    for item in values:
        if budget.remaining < 2:
            break
        statement = budget.take(item.statement, limit=1_200, reserve=1)
        rationale = budget.take(item.rationale, limit=1_200)
        if statement is None or rationale is None:
            break
        bounded.append(
            item.model_copy(
                update={"statement": statement, "rationale": rationale}
            )
        )
    return bounded


def _bounded_experiences(
    budget: _CharacterBudget,
    values: Sequence[ExperienceMemory],
) -> list[ExperienceMemory]:
    bounded: list[ExperienceMemory] = []
    for item in values:
        if budget.remaining < 3:
            break
        summary = budget.take(item.summary, limit=1_500, reserve=2)
        applicability = budget.take(item.applicability, limit=1_000, reserve=1)
        evidence = budget.take(item.evidence, limit=2_000)
        if summary is None or applicability is None or evidence is None:
            break
        bounded.append(
            item.model_copy(
                update={
                    "summary": summary,
                    "applicability": applicability,
                    "evidence": evidence,
                }
            )
        )
    return bounded


def _bounded_actions(
    budget: _CharacterBudget,
    values: Sequence[ActionDigest],
) -> list[ActionDigest]:
    bounded: list[ActionDigest] = []
    for item in values:
        summary = budget.take(item.summary, limit=1_500)
        if summary is None:
            break
        bounded.append(item.model_copy(update={"summary": summary}))
    return bounded


class ContextCompiler:
    """Build focused context from memories and canonical artifact references."""

    def __init__(self, store: AgentRuntimeStore, artifact_resolver: ArtifactResolver) -> None:
        self._store = store
        self._artifact_resolver = artifact_resolver

    def compile(
        self,
        run_id: str,
        *,
        requested_artifact_refs: Sequence[ArtifactRef] = (),
        additional_constraints: Sequence[str] = (),
        experience_tags: Sequence[str] = (),
        objective_override: str | None = None,
        max_artifacts: int = 16,
        decision_limit: int = 12,
        experience_limit: int = 8,
        recent_action_limit: int = 8,
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ) -> ContextPack:
        if max_artifacts < 1:
            raise ValueError("max_artifacts must be at least 1")
        budget = _CharacterBudget(max_characters)
        run = self._store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        for ref in requested_artifact_refs:
            if ref.project_id != run.project_id:
                raise ValueError("Requested artifacts must belong to the run's project")

        project_memory = self._store.get_project_memory(run.project_id)
        run_memory = self._store.get_run_memory(run.id)
        decisions = self._store.list_decisions(
            run.project_id,
            run_id=run.id,
            limit=decision_limit,
        )
        experiences = self._store.list_experiences(
            run.project_id,
            skill_ids=run.active_skill_ids,
            tags=experience_tags,
            limit=experience_limit,
        )

        candidates: list[ArtifactRef] = list(requested_artifact_refs)
        if run_memory is not None:
            candidates.extend(run_memory.artifact_refs)
        if project_memory is not None:
            candidates.extend(project_memory.artifact_refs)
        for decision in decisions:
            candidates.extend(decision.artifact_refs)

        selected_refs: list[ArtifactRef] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        for ref in candidates:
            identity = ref.identity()
            if ref.project_id != run.project_id or identity in seen:
                continue
            seen.add(identity)
            selected_refs.append(ref)
            if len(selected_refs) >= max_artifacts:
                break

        artifacts: list[ArtifactSummary] = []
        unresolved_refs: list[ArtifactRef] = []
        for ref in selected_refs:
            summary = self._artifact_resolver.summarize_artifact(ref)
            if summary is None:
                unresolved_refs.append(ref)
            else:
                artifacts.append(summary)

        actions = self._store.list_actions(run.id)
        selected_actions = (
            actions[-recent_action_limit:] if recent_action_limit > 0 else []
        )
        recent_actions = [
            ActionDigest(
                id=action.id,
                kind=action.kind,
                status=action.status,
                summary=action.output_summary or action.input_summary,
                artifact_refs=action.artifact_refs,
            )
            for action in selected_actions
        ]
        constraints = list(additional_constraints)
        if project_memory is not None:
            constraints = [*project_memory.constraints, *constraints]

        headline_limit = max(64, min(4_000, max_characters // 4))
        objective = budget.take(
            objective_override or run.objective,
            limit=headline_limit,
        )
        if objective is None:
            raise ValueError("max_characters is too small for the run objective")
        project_summary = budget.take(
            None if project_memory is None else project_memory.summary,
            limit=headline_limit,
        )
        run_focus = budget.take(
            None if run_memory is None else run_memory.current_focus,
            limit=2_000,
        )
        bounded_constraints = _bounded_strings(budget, constraints)
        bounded_blockers = _bounded_strings(
            budget,
            [] if run_memory is None else run_memory.blockers,
        )
        bounded_questions = _bounded_strings(
            budget,
            [] if run_memory is None else run_memory.open_questions,
        )
        bounded_next_actions = _bounded_strings(
            budget,
            [] if run_memory is None else run_memory.next_actions,
        )
        bounded_decisions = _bounded_decisions(budget, decisions)
        bounded_recent_actions = _bounded_actions(budget, recent_actions)
        bounded_artifacts = _bounded_artifacts(budget, artifacts)
        bounded_experiences = _bounded_experiences(budget, experiences)

        return ContextPack(
            run_id=run.id,
            project_id=run.project_id,
            objective=objective,
            active_skill_ids=run.active_skill_ids,
            execution_target=run.execution_target,
            render_export=run.render_export,
            project_summary=project_summary,
            run_focus=run_focus,
            constraints=bounded_constraints,
            decisions=bounded_decisions,
            experiences=bounded_experiences,
            artifacts=bounded_artifacts,
            unresolved_artifact_refs=unresolved_refs,
            recent_actions=bounded_recent_actions,
            blockers=bounded_blockers,
            open_questions=bounded_questions,
            next_actions=bounded_next_actions,
        )

    def compile_for_work_order(
        self,
        work_order_id: str,
        *,
        experience_tags: Sequence[str] = (),
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ) -> ContextPack:
        work_order = self._store.get_work_order(work_order_id)
        if work_order is None:
            raise KeyError(f"Unknown work order: {work_order_id}")
        return self.compile(
            work_order.parent_run_id,
            requested_artifact_refs=work_order.scope,
            additional_constraints=work_order.constraints,
            experience_tags=experience_tags,
            objective_override=work_order.objective,
            max_characters=max_characters,
        )
