"""Bounded context assembly for agent calls."""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Protocol

from pydantic import JsonValue

from short_drama.agent_runtime.models import (
    ActionDigest,
    AgentResult,
    AgentResultDigest,
    ArtifactRef,
    ArtifactSummary,
    ContextPack,
    DecisionMemory,
    ExperienceMemory,
    WorkOrder,
)
from short_drama.agent_runtime.store import AgentRuntimeStore


DEFAULT_CONTEXT_CHARACTER_BUDGET = 24_000


class ContextManifestResolutionError(ValueError):
    """Raised when an explicit WorkOrder context pointer cannot be resolved."""


class ArtifactResolver(Protocol):
    """Adapter implemented by the canonical project/artifact repository."""

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        """Resolve only the concise information needed by an agent call."""


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_artifact_refs(values: Sequence[ArtifactRef]) -> list[ArtifactRef]:
    selected: list[ArtifactRef] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for ref in values:
        identity = ref.identity()
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(ref)
    return selected


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

    def take_exact(self, value: str, *, limit: int | None = None) -> bool:
        """Consume a complete structured value or leave the budget untouched."""

        available = self.remaining if limit is None else min(limit, self.remaining)
        if len(value) > available:
            return False
        self.remaining -= len(value)
        return True


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
    newest_first: list[ActionDigest] = []
    for item in reversed(values):
        summary = budget.take(
            item.summary,
            limit=1_000,
            reserve=256 if item.agent_result is not None else 0,
        )
        if summary is None:
            break
        agent_result = _bounded_agent_result(budget, item.agent_result)
        newest_first.append(
            item.model_copy(
                update={"summary": summary, "agent_result": agent_result}
            )
        )
    return list(reversed(newest_first))


def _bounded_agent_result(
    budget: _CharacterBudget,
    result: AgentResultDigest | None,
) -> AgentResultDigest | None:
    if result is None:
        return None
    concise_summary = budget.take(result.concise_summary, limit=1_000)
    if concise_summary is None:
        return None
    deliverable = _bounded_deliverable(budget, result.deliverable)
    findings = _bounded_strings(budget, result.findings, item_limit=700)
    evidence_refs: list[ArtifactRef] = []
    for ref in result.evidence_refs[:8]:
        serialized = ref.model_dump_json()
        if not budget.take_exact(serialized, limit=600):
            break
        evidence_refs.append(ref)
    return result.model_copy(
        update={
            "concise_summary": concise_summary,
            "deliverable": deliverable,
            "findings": findings,
            "evidence_refs": evidence_refs,
        }
    )


def _bounded_deliverable(
    budget: _CharacterBudget,
    deliverable: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Keep ordinary structured results intact and summarize oversized values."""

    bounded: dict[str, JsonValue] = {}
    remaining_limit = 2_000
    for key, value in deliverable.items():
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        field_cost = len(key) + len(rendered)
        if field_cost <= remaining_limit and budget.take_exact(
            f"{key}:{rendered}",
            limit=remaining_limit,
        ):
            bounded[key] = value
            remaining_limit -= field_cost
            continue
        available = min(700, remaining_limit - len(key))
        if available <= 0:
            break
        summarized = budget.take(rendered, limit=available)
        if summarized is None:
            break
        bounded[key] = summarized
        break
    return bounded


def _agent_result_digest(result: AgentResult | None) -> AgentResultDigest | None:
    if result is None:
        return None
    return AgentResultDigest(
        status=result.status,
        concise_summary=result.concise_summary,
        deliverable=dict(result.deliverable),
        findings=list(result.findings),
        evidence_refs=list(result.evidence_refs),
    )


class ContextCompiler:
    """Build focused context from memories and canonical artifact references."""

    def __init__(self, store: AgentRuntimeStore, artifact_resolver: ArtifactResolver) -> None:
        self._store = store
        self._artifact_resolver = artifact_resolver

    def unresolved_artifact_refs(
        self,
        run_id: str,
        refs: Sequence[ArtifactRef],
    ) -> list[ArtifactRef]:
        """Resolve exact artifact identities without applying a prompt budget."""

        run = self._store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")

        unresolved: list[ArtifactRef] = []
        for ref in _unique_artifact_refs(refs):
            if ref.project_id != run.project_id:
                raise ValueError("Requested artifacts must belong to the run's project")
            summary = self._artifact_resolver.summarize_artifact(ref)
            if summary is None or summary.ref.identity() != ref.identity():
                unresolved.append(ref)
        return unresolved

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
                agent_result=_agent_result_digest(
                    None
                    if action.work_order_id is None
                    else self._store.get_agent_result(action.work_order_id)
                ),
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
        bounded_recent_actions = _bounded_actions(budget, recent_actions)
        bounded_decisions = _bounded_decisions(budget, decisions)
        bounded_artifacts = _bounded_artifacts(budget, artifacts)
        bounded_experiences = _bounded_experiences(budget, experiences)

        return ContextPack(
            run_id=run.id,
            project_id=run.project_id,
            objective=objective,
            active_skill_ids=run.active_skill_ids,
            unattended=run.unattended,
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
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ) -> ContextPack:
        work_order = self._store.get_work_order(work_order_id)
        if work_order is None:
            raise KeyError(f"Unknown work order: {work_order_id}")
        return self._compile_manifest(
            work_order,
            max_characters=max_characters,
        )

    def _compile_manifest(
        self,
        work_order: WorkOrder,
        *,
        max_characters: int,
    ) -> ContextPack:
        """Resolve only IDs explicitly selected in the persisted manifest."""

        manifest = work_order.context_manifest
        if manifest is None:
            raise ContextManifestResolutionError(
                f"WorkOrder {work_order.id} has no ContextManifest"
            )
        run = self._store.get_run(work_order.parent_run_id)
        if run is None:
            raise ContextManifestResolutionError(
                f"Unknown AgentRun referenced by WorkOrder: {work_order.parent_run_id}"
            )

        refs = _unique_artifact_refs(
            [
                *manifest.required_artifacts,
                *[
                    ref
                    for boundary in manifest.adjacent_boundaries
                    for ref in boundary.artifact_refs
                ],
            ]
        )
        resolved_artifacts: list[ArtifactSummary] = []
        for ref in refs:
            if ref.project_id != run.project_id:
                raise ContextManifestResolutionError(
                    "ContextManifest artifact references must belong to the AgentRun project"
                )
            summary = self._artifact_resolver.summarize_artifact(ref)
            if summary is None or summary.ref.identity() != ref.identity():
                raise ContextManifestResolutionError(
                    "ContextManifest contains an unresolved artifact reference: "
                    f"{ref.artifact_type}:{ref.artifact_id}@{ref.version}"
                )
            resolved_artifacts.append(summary)

        decisions: list[DecisionMemory] = []
        for decision_id in manifest.inherited_decision_ids:
            decision = self._store.get_decision(decision_id)
            if decision is None or decision.project_id != run.project_id:
                raise ContextManifestResolutionError(
                    f"ContextManifest contains an unresolved decision id: {decision_id}"
                )
            decisions.append(decision)

        selected_tags = manifest.experience_tags
        experiences = (
            []
            if not selected_tags
            else self._store.list_experiences(
                run.project_id,
                skill_ids=manifest.selected_skill_ids,
                tags=selected_tags,
                limit=32,
            )
        )
        if not manifest.selected_skill_ids:
            experiences = [item for item in experiences if item.skill_id is None]

        budget = _CharacterBudget(max_characters)
        headline_limit = max(64, min(4_000, max_characters // 4))
        objective = budget.take(manifest.objective, limit=headline_limit)
        if objective is None:
            raise ContextManifestResolutionError(
                "ContextManifest objective does not fit the context budget"
            )
        bounded_constraints = _bounded_strings(budget, manifest.constraints)
        bounded_questions = _bounded_strings(budget, manifest.open_questions)
        bounded_decisions = _bounded_decisions(budget, decisions)
        bounded_artifacts = _bounded_artifacts(budget, resolved_artifacts)
        bounded_experiences = _bounded_experiences(budget, experiences)
        if len(bounded_decisions) != len(decisions):
            raise ContextManifestResolutionError(
                "ContextManifest decisions do not fit the context budget"
            )
        if len(bounded_artifacts) != len(resolved_artifacts):
            raise ContextManifestResolutionError(
                "ContextManifest artifacts do not fit the context budget"
            )

        return ContextPack(
            run_id=run.id,
            project_id=run.project_id,
            work_order_id=work_order.id,
            context_manifest_id=manifest.id,
            objective=objective,
            active_skill_ids=manifest.selected_skill_ids,
            unattended=run.unattended,
            execution_target=run.execution_target,
            render_export=run.render_export,
            constraints=bounded_constraints,
            decisions=bounded_decisions,
            experiences=bounded_experiences,
            artifacts=bounded_artifacts,
            open_questions=bounded_questions,
        )
