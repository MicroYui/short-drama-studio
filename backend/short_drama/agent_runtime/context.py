"""Bounded context assembly for agent calls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from typing import Protocol, cast

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
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    ToolAttemptRef,
    WorkOrder,
    WorkOrderNextOwner,
)
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.leader_context import saved_tool_result_ref


DEFAULT_CONTEXT_CHARACTER_BUDGET = 24_000


class ContextManifestResolutionError(ValueError):
    """Raised when an explicit WorkOrder context pointer cannot be resolved."""


class ArtifactResolver(Protocol):
    """Adapter implemented by the canonical project/artifact repository."""

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        """Resolve only the concise information needed by an agent call."""


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _context_read_ref(ref: ArtifactRef) -> ArtifactRef:
    # Execution records are updated in place. Their saved state numbers remain
    # in Trace, while both Leader and Worker read the current task lifecycle.
    if ref.artifact_type in {"h3_route_plan", "h3_route_asset_preparation", "editorial_timeline"}:
        return ref.model_copy(update={"version": None})
    return ref


def _unique_artifact_refs(values: Sequence[ArtifactRef]) -> list[ArtifactRef]:
    selected: list[ArtifactRef] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for ref in values:
        ref = _context_read_ref(ref)
        identity = ref.identity()
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(ref)
    return selected


def _prioritize_current_asset_plan_indexes(
    values: Sequence[ArtifactRef],
) -> list[ArtifactRef]:
    """Put only the newest lightweight index for each AssetPlan first.

    Leader context may still contain an index selected by the just-completed
    plan plus the newer index published by an AssetPlan write.  Both identities
    remain resolvable, but replaying the stale scheduling view ahead of the new
    handoff invites the Leader to plan against superseded work status.  WorkOrder
    manifests are unaffected because they use ``compile_for_work_order`` and
    preserve every explicitly selected immutable reference.
    """

    latest_indexes: dict[tuple[str, str], ArtifactRef] = {}
    for ref in values:
        if ref.artifact_type != "asset_plan_index":
            continue
        key = (ref.project_id, ref.artifact_id)
        current = latest_indexes.get(key)
        if current is None or (ref.version or -1) > (current.version or -1):
            latest_indexes[key] = ref
    indexes = list(latest_indexes.values())
    return [
        *indexes,
        *[ref for ref in values if ref.artifact_type != "asset_plan_index"],
    ]


def _tool_attempt_ref(
    project_id: str,
    work_order: WorkOrder,
    turn: SpecialistTurnRecord,
) -> ToolAttemptRef:
    result_status = None if turn.result is None else turn.result.get("status")
    return ToolAttemptRef(
        project_id=project_id,
        agent_run_id=work_order.parent_run_id,
        work_order_id=work_order.id,
        attempt_id=turn.id,
        tool_name=turn.tool_name,
        turn_status=turn.status,
        outcome_status=(result_status if isinstance(result_status, str) else None),
        has_arguments=bool(turn.arguments),
        has_result=turn.result is not None,
    )


def _recoverable_tool_attempt_refs(
    project_id: str,
    work_order: WorkOrder,
) -> list[ToolAttemptRef]:
    """Expose current repair bases and unsaved successful prompt compilations.

    A completed compiler result is an addressable output even when its Worker
    has returned or been superseded. RoutePlan persistence retires that inline
    result. Exact arguments and prompt bodies remain outside normal context.
    """

    include_repairs = work_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    latest_by_tool: dict[str, ToolAttemptRef] = {}
    compiled_by_target: dict[tuple[str | None, str | None], ToolAttemptRef] = {}
    for turn in sorted(work_order.specialist_turns, key=lambda item: item.sequence):
        result_status = None if turn.result is None else turn.result.get("status")
        selected_attempt = turn.arguments.get("attempt_ref")
        if include_repairs and result_status == "SUCCEEDED" and isinstance(selected_attempt, dict):
            try:
                selected_ref = ToolAttemptRef.model_validate(selected_attempt)
            except ValueError:
                selected_ref = None
            if selected_ref is not None:
                # A repair Worker may discover that the candidate needs another
                # bounded planning decision before it can issue a new write. Keep
                # the exact source attempt addressable across that Leader replan
                # instead of falling back to user input or copying its payload.
                latest_by_tool[selected_ref.tool_name] = selected_ref
        request_value = turn.arguments.get("request")
        request = request_value if isinstance(request_value, dict) else turn.arguments
        if turn.tool_name == "route.compile_h3_prompt":
            version = request.get("generation_plan_version_id")
            target = request.get("generation_unit_id") or request.get("shot_id")
            key = (version if isinstance(version, str) else None, target if isinstance(target, str) else None)
            compiled_by_target.pop(key, None)
            if result_status == "SUCCEEDED":
                compiled_by_target[key] = _tool_attempt_ref(project_id, work_order, turn)
        if result_status == "SUCCEEDED":
            # A newer successful invocation supersedes its old failure pointer.
            latest_by_tool.pop(turn.tool_name, None)
            if turn.tool_name == "route.plan_h3" and turn.result is not None:
                output = turn.result.get("output")
                plan = output.get("plan") if isinstance(output, dict) else None
                if isinstance(plan, dict):
                    version = plan.get("input_generation_plan_version_id")
                    decisions = plan.get("decisions")
                    for decision in decisions if isinstance(decisions, list) else []:
                        if isinstance(decision, dict):
                            target = decision.get("generation_unit_id") or decision.get("shot_id")
                            compiled_by_target.pop((version if isinstance(version, str) else None,
                                                    target if isinstance(target, str) else None), None)
        failed = (
            turn.status == SpecialistTurnStatus.INTERRUPTED
            or (
                isinstance(result_status, str)
                and result_status != "SUCCEEDED"
            )
        )
        if include_repairs and failed and turn.arguments:
            latest_by_tool[turn.tool_name] = _tool_attempt_ref(
                project_id,
                work_order,
                turn,
            )
    return [*latest_by_tool.values(), *compiled_by_target.values()]


def _bounded_tool_attempt_refs(
    budget: _CharacterBudget,
    values: Sequence[ToolAttemptRef],
) -> list[ToolAttemptRef]:
    selected: list[ToolAttemptRef] = []
    for ref in values:
        if not budget.take_exact(ref.model_dump_json(), limit=1_200):
            break
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
    prioritized = [
        *[item for item in values if item.ref.artifact_type == "asset_plan_index"],
        *[item for item in values if item.ref.artifact_type != "asset_plan_index"],
    ]
    for item in prioritized:
        required_fields = 1 if item.title is None else 2
        if budget.remaining < required_fields:
            break
        if item.ref.artifact_type == "asset_plan_index":
            title = budget.take(
                item.title,
                limit=min(240, max(1, budget.remaining - len(item.summary))),
                reserve=len(item.summary),
            )
            if not budget.take_exact(item.summary):
                break
            summary = item.summary
        else:
            title = budget.take(item.title, limit=240, reserve=1)
            summary = budget.take(item.summary, limit=3_000)
        if summary is None or (item.title is not None and title is None):
            break
        bounded.append(item.model_copy(update={"title": title, "summary": summary}))
    return bounded


def _leader_artifact_summary(item: ArtifactSummary) -> ArtifactSummary:
    """Keep a completed asset stage from replaying its whole work inventory.

    The canonical index and explicit Worker manifests retain every work row.
    A Leader needs the completed item identities and status to hand off to the
    next stage; those rows remain available through the exact artifact pointer.
    """

    if item.ref.artifact_type != "asset_plan_index":
        return item
    try:
        payload: JsonValue = json.loads(item.summary)
    except ValueError:
        return item
    if not isinstance(payload, dict):
        return item
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return item
    if any(
        not isinstance(entry, dict)
        or entry.get("status") not in {"COMPLETED", "NOT_REQUIRED"}
        for entry in items
    ):
        return item
    completed_items: list[dict[str, JsonValue]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        compact = {key: value for key, value in entry.items() if key != "work"}
        work = entry.get("work")
        compact["completed_work_count"] = len(work) if isinstance(work, list) else 0
        completed_items.append(compact)
    return item.model_copy(update={"summary": json.dumps(
        {
            "execution_contract": payload.get("execution_contract"),
            "items": completed_items,
            "work_details": "Read this exact asset_plan_index artifact when needed.",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )})


def _bounded_leader_artifacts(
    budget: _CharacterBudget,
    values: Sequence[ArtifactSummary],
) -> list[ArtifactSummary]:
    """Bound optional prose without discarding a selected artifact's identity.

    Placeholder records are measured by the serialized ContextPack fitter. They
    must survive when a large earlier summary uses the prose budget, otherwise
    successful Tool outputs disappear from the very next planning call.
    """

    bounded = _bounded_artifacts(budget, values)
    visible = {item.ref.identity() for item in bounded}
    return [
        *bounded,
        *[
            item.model_copy(update={
                "title": None,
                "summary": "Summary not loaded. Read the exact artifact when needed.",
            })
            for item in values
            if item.ref.identity() not in visible
        ],
    ]


def _bounded_required_artifacts(
    budget: _CharacterBudget,
    values: Sequence[ArtifactSummary],
) -> list[ArtifactSummary]:
    """Keep every exact manifest pointer while sharing its summary budget fairly.

    A WorkOrder manifest is an explicit artifact handoff.  Dropping a later
    entry because an earlier parent document consumed the prompt budget breaks
    that contract and also makes the result depend on list order.  Required
    artifacts therefore receive a proportional slice of the remaining budget;
    the canonical refs stay exact while their read-only summaries may be
    shortened.  A specialist can still load an exact artifact through the
    artifact Tools when the bounded projection is not sufficient.
    """

    if not values:
        return []
    minimum_costs = [
        (
            len(item.summary) + (1 if item.title is not None else 0)
            if item.ref.artifact_type == "asset_plan_index"
            else 1 + (1 if item.title is not None else 0)
        )
        for item in values
    ]
    if sum(minimum_costs) > budget.remaining:
        return []

    bounded: list[ArtifactSummary] = []
    for index, item in enumerate(values):
        items_left = len(values) - index
        required_for_rest = sum(minimum_costs[index + 1 :])
        available_for_item = budget.remaining - required_for_rest
        if item.ref.artifact_type == "asset_plan_index":
            title: str | None = None
            if item.title is not None:
                title = budget.take(
                    item.title,
                    limit=min(240, max(1, available_for_item - len(item.summary))),
                    reserve=required_for_rest + len(item.summary),
                )
                if title is None:
                    return bounded
            if not budget.take_exact(item.summary):
                return bounded
            bounded.append(
                item.model_copy(update={"title": title, "summary": item.summary})
            )
            continue
        fair_share = max(minimum_costs[index], budget.remaining // items_left)
        item_limit = min(3_240, fair_share, available_for_item)

        title: str | None = None
        if item.title is not None:
            # Leave the summary at least one character.  Short titles return
            # their unused share to the later summaries automatically.
            title_limit = min(240, max(1, item_limit - 1))
            title = budget.take(
                item.title,
                limit=title_limit,
                reserve=required_for_rest + 1,
            )
            if title is None:
                return bounded

        summary_limit = min(
            3_000,
            max(1, item_limit - (0 if title is None else len(title))),
        )
        summary = budget.take(
            item.summary,
            limit=summary_limit,
            reserve=required_for_rest,
        )
        if summary is None:
            return bounded
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
    *, exact_read_action_ids: frozenset[str] = frozenset(),
) -> list[ActionDigest]:
    newest_first: list[ActionDigest] = []
    for item in reversed(values):
        summary = budget.take(
            item.summary,
            limit=len(item.summary) if item.id in exact_read_action_ids else 1_000,
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
        completion_receipts=list(result.completion_receipts),
        # The complete evidence set remains on the durable AgentResult.  It is
        # intentionally not promoted into every later Leader prompt: landed
        # created/updated artifacts are carried by ActionDigest, while a later
        # focused task can explicitly inspect the source result when needed.
        evidence_refs=[],
    )


def _model_context_characters(context: ContextPack) -> int:
    """Measure the complete JSON object sent to a model, not only its prose."""

    return len(
        json.dumps(
            context.model_context_dump(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _fit_model_context(
    build: Callable[[int], ContextPack],
    *,
    max_characters: int,
    error_type: type[ValueError],
    error_message: str,
) -> ContextPack:
    """Fit field-aware bounded content against its actual serialized payload."""

    candidate = build(max_characters)
    if _model_context_characters(candidate) <= max_characters:
        return candidate

    # A manifest can contain mandatory semantic records such as explicitly
    # inherited decisions and exact artifact pointers.  Such a builder may be
    # invalid at the public 256-character floor even though a slightly larger
    # content budget produces a compact projection that fits the complete JSON
    # envelope.  Find that real lower bound before maximizing useful content.
    minimum_budget = 256
    minimum: ContextPack | None = None
    minimum_buildable_budget = max_characters
    lower = minimum_budget
    upper = max_characters - 1
    while lower <= upper:
        content_budget = (lower + upper) // 2
        try:
            probe = build(content_budget)
        except error_type:
            lower = content_budget + 1
            continue
        minimum = probe
        minimum_buildable_budget = content_budget
        upper = content_budget - 1

    if minimum is None:
        minimum = candidate
    if _model_context_characters(minimum) > max_characters:
        raise error_type(error_message)

    # The existing bounders shorten individual semantic fields or omit an
    # optional record as a unit.  Binary search chooses the richest projection
    # that fits; it never slices the final JSON or corrupts a reference object.
    best = minimum
    lower = minimum_buildable_budget + 1
    upper = max_characters - 1
    while lower <= upper:
        content_budget = (lower + upper) // 2
        try:
            candidate = build(content_budget)
        except error_type:
            lower = content_budget + 1
            continue
        if _model_context_characters(candidate) <= max_characters:
            best = candidate
            lower = content_budget + 1
        else:
            upper = content_budget - 1
    return best


class ContextCompiler:
    """Build focused context from memories and canonical artifact references."""

    def __init__(self, store: AgentRuntimeStore, artifact_resolver: ArtifactResolver) -> None:
        self._store = store
        self._artifact_resolver = artifact_resolver

    def _summarize_artifacts(self, refs: Sequence[ArtifactRef]) -> list[ArtifactSummary | None]:
        batch = getattr(self._artifact_resolver, "summarize_artifacts", None)
        if callable(batch):
            resolve = cast(Callable[[Sequence[ArtifactRef]], list[ArtifactSummary | None]], batch)
            return resolve(refs)
        return [self._artifact_resolver.summarize_artifact(ref) for ref in refs]

    def unresolved_artifact_refs(
        self,
        run_id: str,
        refs: Sequence[ArtifactRef],
    ) -> list[ArtifactRef]:
        """Resolve content sources and live execution records before delegation."""

        run = self._store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")

        unresolved: list[ArtifactRef] = []
        refs = _unique_artifact_refs(refs)
        for ref in refs:
            if ref.project_id != run.project_id:
                raise ValueError("Requested artifacts must belong to the run's project")
        for ref, summary in zip(refs, self._summarize_artifacts(refs), strict=True):
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
        # Preserve the public lower bound even though fitting below is based on
        # the complete serialized projection rather than prose alone.
        _CharacterBudget(max_characters)
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

        actions = self._store.list_actions(run.id)
        # Newly read/written objects must not be crowded out by the first 16
        # older memory refs. Keep the newest observed version per object here;
        # explicitly requested immutable refs still take precedence.
        recent_refs: dict[tuple[str, str, str], ArtifactRef] = {}
        for action in reversed(actions):
            for ref in action.artifact_refs:
                recent_refs.setdefault((ref.project_id, ref.artifact_type, ref.artifact_id), ref)
        candidates: list[ArtifactRef] = [*requested_artifact_refs, *recent_refs.values()]
        if run_memory is not None:
            candidates.extend(run_memory.artifact_refs)
        if project_memory is not None:
            candidates.extend(project_memory.artifact_refs)
        for decision in decisions:
            candidates.extend(decision.artifact_refs)

        # A new GenerationPlan republishes the same stable unit ids. Leader
        # memory may still hold their earlier versions; use the current project
        # handoff at those positions. Explicit Worker manifests stay immutable.
        current_generation = {
            (ref.artifact_type, ref.artifact_id): ref
            for ref in ([] if project_memory is None else project_memory.artifact_refs)
            if ref.artifact_type in {"generation_plan", "generation_unit"}
        }
        candidates = [
            _context_read_ref(current_generation.get((ref.artifact_type, ref.artifact_id), ref))
            for ref in candidates
        ]
        candidates = _prioritize_current_asset_plan_indexes(candidates)

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
        for ref, summary in zip(selected_refs, self._summarize_artifacts(selected_refs), strict=True):
            if summary is None:
                unresolved_refs.append(ref)
            else:
                artifacts.append(_leader_artifact_summary(summary))

        selected_actions = (
            actions[-recent_action_limit:] if recent_action_limit > 0 else []
        )
        recent_actions = [
            ActionDigest(
                id=action.id,
                work_order_id=action.work_order_id,
                kind=action.kind,
                status=action.status,
                summary=action.output_summary or action.input_summary,
                tool_result_ref=(None if action.tool_result_context_ref is None else
                    saved_tool_result_ref(run.project_id, run.id, action.tool_result_context_ref)),
                artifact_refs=action.artifact_refs,
                agent_result=_agent_result_digest(
                    None
                    if action.work_order_id is None
                    else self._store.get_agent_result(action.work_order_id)
                ),
            )
            for action in selected_actions
        ]
        exact_read_action_ids: set[str] = set()
        for action in selected_actions:
            if action.tool_name != "timeline.read_editorial" or not action.output_summary:
                continue
            try:
                output: JsonValue = json.loads(action.output_summary)
            except ValueError:
                continue
            content = output.get("content") if isinstance(output, dict) else None
            if isinstance(content, dict) and content.get("selection_only") is True:
                exact_read_action_ids.add(action.id)
        tool_attempt_refs: list[ToolAttemptRef] = []
        seen_attempts: set[tuple[str, str, str, str]] = set()
        for action in selected_actions:
            if action.work_order_id is None:
                continue
            work_order = self._store.get_work_order(action.work_order_id)
            if work_order is None:
                continue
            for ref in _recoverable_tool_attempt_refs(run.project_id, work_order):
                if ref.identity() in seen_attempts:
                    continue
                seen_attempts.add(ref.identity())
                tool_attempt_refs.append(ref)
        constraints = list(additional_constraints)
        if project_memory is not None:
            constraints = [*project_memory.constraints, *constraints]

        def build(content_budget: int) -> ContextPack:
            budget = _CharacterBudget(content_budget)
            headline_limit = max(64, min(4_000, content_budget // 4))
            objective = budget.take(
                objective_override or run.objective,
                limit=headline_limit,
            )
            if objective is None:
                raise ValueError("max_characters is too small for the run objective")
            index_artifacts = [
                item
                for item in artifacts
                if item.ref.artifact_type == "asset_plan_index"
            ]
            other_artifacts = [
                item
                for item in artifacts
                if item.ref.artifact_type != "asset_plan_index"
            ]
            bounded_index_artifacts = _bounded_artifacts(budget, index_artifacts)
            if len(bounded_index_artifacts) != len(index_artifacts):
                raise ValueError(
                    "asset_plan_index summaries must fit the AgentRun context exactly"
                )
            # Current outcomes must precede ambient project prose. Otherwise a
            # completed Tool can lose both its result and exact handoff refs.
            bounded_tool_attempt_refs = _bounded_tool_attempt_refs(budget, tool_attempt_refs)
            bounded_recent_actions = _bounded_actions(budget, recent_actions,
                exact_read_action_ids=frozenset(exact_read_action_ids))
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
            project_summary = budget.take(
                None if project_memory is None else project_memory.summary,
                limit=headline_limit,
            )
            bounded_decisions = _bounded_decisions(budget, decisions)
            bounded_artifacts = [
                *bounded_index_artifacts,
                *_bounded_leader_artifacts(budget, other_artifacts),
            ]
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
                tool_attempt_refs=bounded_tool_attempt_refs,
                unresolved_artifact_refs=unresolved_refs,
                recent_actions=bounded_recent_actions,
                blockers=bounded_blockers,
                open_questions=bounded_questions,
                next_actions=bounded_next_actions,
            )

        return _fit_model_context(
            build,
            max_characters=max_characters,
            error_type=ValueError,
            error_message="max_characters is too small for the model context structure",
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
        for ref, summary in zip(refs, self._summarize_artifacts(refs), strict=True):
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

        for ref in manifest.tool_attempt_refs:
            if (
                ref.project_id != run.project_id
                or ref.agent_run_id != run.id
            ):
                raise ContextManifestResolutionError(
                    "ContextManifest Tool attempt references must belong to the AgentRun"
                )
            source_work_order = self._store.get_work_order(ref.work_order_id)
            if (
                source_work_order is None
                or source_work_order.parent_run_id != run.id
            ):
                raise ContextManifestResolutionError(
                    f"ContextManifest contains an unresolved Tool attempt: {ref.attempt_id}"
                )
            source_turn = next(
                (
                    turn
                    for turn in source_work_order.specialist_turns
                    if turn.id == ref.attempt_id
                ),
                None,
            )
            if (
                source_turn is None
                or _tool_attempt_ref(run.project_id, source_work_order, source_turn)
                != ref
            ):
                raise ContextManifestResolutionError(
                    f"ContextManifest contains a stale Tool attempt reference: {ref.attempt_id}"
                )

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

        _CharacterBudget(max_characters)

        def build(content_budget: int) -> ContextPack:
            budget = _CharacterBudget(content_budget)
            headline_limit = max(64, min(4_000, content_budget // 4))
            objective = budget.take(manifest.objective, limit=headline_limit)
            if objective is None:
                raise ContextManifestResolutionError(
                    "ContextManifest objective does not fit the context budget"
                )
            bounded_decisions = _bounded_decisions(budget, decisions)
            bounded_tool_attempt_refs = _bounded_tool_attempt_refs(
                budget,
                manifest.tool_attempt_refs,
            )
            # Exact artifact pointers are the WorkOrder's primary handoff.
            # Optional prose must not consume the space needed to represent all
            # explicitly selected canonical artifacts.
            bounded_artifacts = _bounded_required_artifacts(
                budget,
                resolved_artifacts,
            )
            bounded_constraints = _bounded_strings(budget, manifest.constraints)
            bounded_questions = _bounded_strings(budget, manifest.open_questions)
            bounded_experiences = _bounded_experiences(budget, experiences)
            if len(bounded_decisions) != len(decisions):
                raise ContextManifestResolutionError(
                    "ContextManifest decisions do not fit the context budget"
                )
            if len(bounded_artifacts) != len(resolved_artifacts):
                raise ContextManifestResolutionError(
                    "ContextManifest artifacts do not fit the context budget"
                )
            if len(bounded_tool_attempt_refs) != len(manifest.tool_attempt_refs):
                raise ContextManifestResolutionError(
                    "ContextManifest Tool attempt references do not fit the context budget"
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
                tool_attempt_refs=bounded_tool_attempt_refs,
                open_questions=bounded_questions,
            )

        return _fit_model_context(
            build,
            max_characters=max_characters,
            error_type=ContextManifestResolutionError,
            error_message=(
                "ContextManifest model projection does not fit the context budget"
            ),
        )
