"""Pure aggregation for comparing persisted video-generation routes.

The evaluator deliberately consumes existing production records instead of
introducing a second experiment store.  Evidence purposes remain separate so a
contract smoke test or an ordinary production Take cannot inflate a quality
probe result.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from pydantic import Field

from short_drama.h3_models import H3ProviderRun
from short_drama.models import (
    DomainModel,
    EvidencePurpose,
    EvidenceStatus,
    GenerationTask,
    RouteEvidence,
    Take,
    TaskStatus,
    VideoGenerationRoute,
)


_PURPOSES: tuple[EvidencePurpose, ...] = (
    EvidencePurpose.QUALITY_PROBE,
    EvidencePurpose.CONTRACT_SMOKE,
    EvidencePurpose.PRODUCTION,
)
_ROUTES: tuple[VideoGenerationRoute, ...] = tuple(VideoGenerationRoute)
_TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.OUTPUT_MISSING,
}


class RouteEvaluationBucket(DomainModel):
    """One purpose-and-route slice with explicit metric denominators."""

    purpose: EvidencePurpose
    route: VideoGenerationRoute
    evidence_count: int = Field(ge=0)
    evidence_status_counts: dict[str, int] = Field(default_factory=dict[str, int])
    task_count: int = Field(ge=0)
    task_status_counts: dict[str, int] = Field(default_factory=dict[str, int])
    terminal_task_count: int = Field(ge=0)
    succeeded_task_count: int = Field(ge=0)
    task_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    output_count: int = Field(ge=0)
    technical_assessed_count: int = Field(ge=0)
    technical_pass_count: int = Field(ge=0)
    technical_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    human_reviewed_count: int = Field(ge=0)
    hard_pass_count: int = Field(ge=0)
    hard_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    first_candidate_reviewed_count: int = Field(ge=0)
    first_candidate_hard_pass_count: int = Field(ge=0)
    first_candidate_hard_pass_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    score_sample_count: int = Field(ge=0)
    median_score: float | None = Field(default=None, ge=0.0, le=5.0)
    provider_time_sample_count: int = Field(ge=0)
    median_provider_seconds: float | None = Field(default=None, ge=0.0)
    total_time_sample_count: int = Field(ge=0)
    median_total_seconds: float | None = Field(default=None, ge=0.0)
    cost_usd_sample_count: int = Field(ge=0)
    total_estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    median_estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    cost_credit_sample_count: int = Field(ge=0)
    total_estimated_cost_credits: float | None = Field(default=None, ge=0.0)
    median_estimated_cost_credits: float | None = Field(default=None, ge=0.0)
    issue_category_counts: dict[str, int] = Field(default_factory=dict[str, int])
    regression_case_counts: dict[str, int] = Field(default_factory=dict[str, int])
    unclassified_regression_count: int = Field(ge=0)
    data_issue_counts: dict[str, int] = Field(default_factory=dict[str, int])


class PurposeRouteEvaluation(DomainModel):
    """All route buckets for exactly one evidence purpose."""

    purpose: EvidencePurpose
    evidence_count: int = Field(ge=0)
    routes: list[RouteEvaluationBucket]


class RouteEvaluationReport(DomainModel):
    """Deterministic report suitable for an API, Tool, test, or JSON export."""

    project_ids: list[str]
    purposes: list[PurposeRouteEvaluation]


@dataclass
class _Accumulator:
    evidence_count: int = 0
    evidence_status_counts: Counter[str] = field(default_factory=Counter[str])
    task_count: int = 0
    task_status_counts: Counter[str] = field(default_factory=Counter[str])
    terminal_task_count: int = 0
    succeeded_task_count: int = 0
    output_count: int = 0
    technical_assessed_count: int = 0
    technical_pass_count: int = 0
    human_reviewed_count: int = 0
    hard_pass_count: int = 0
    first_candidate_reviewed_count: int = 0
    first_candidate_hard_pass_count: int = 0
    scores: list[float] = field(default_factory=list[float])
    provider_seconds: list[float] = field(default_factory=list[float])
    total_seconds: list[float] = field(default_factory=list[float])
    estimated_cost_usd: list[float] = field(default_factory=list[float])
    estimated_cost_credits: list[float] = field(default_factory=list[float])
    issue_category_counts: Counter[str] = field(default_factory=Counter[str])
    regression_case_counts: Counter[str] = field(default_factory=Counter[str])
    unclassified_regression_count: int = 0
    data_issue_counts: Counter[str] = field(default_factory=Counter[str])


def build_route_evaluation_report(
    evidence: Sequence[RouteEvidence],
    tasks: Sequence[GenerationTask],
    provider_runs: Sequence[H3ProviderRun],
    takes: Sequence[Take],
) -> RouteEvaluationReport:
    """Aggregate persisted route evidence without making provider calls.

    ``RouteEvidence.purpose`` is the sole grouping authority.  Conflicting
    task/provider-run metadata is reported as a data issue and is never used to
    move a sample into a different purpose or route bucket.
    """

    task_by_id = {item.id: item for item in tasks}
    run_by_task_id = {item.task_id: item for item in provider_runs}
    take_by_id = {item.id: item for item in takes}
    take_by_task_id = {item.task_id: item for item in takes}
    first_task_by_group = _first_non_retry_task_by_group(evidence, task_by_id)
    counted_first_task_ids: set[str] = set()
    accumulators = {
        (purpose, route): _Accumulator()
        for purpose in _PURPOSES
        for route in _ROUTES
    }

    for item in evidence:
        accumulator = accumulators[(item.purpose, item.route)]
        accumulator.evidence_count += 1
        accumulator.evidence_status_counts[item.status.value] += 1

        is_human_reviewed = (
            item.status == EvidenceStatus.REVIEWED
            and item.hard_gate_passed is not None
        )
        if item.technical_passed is not None:
            accumulator.technical_assessed_count += 1
            if item.technical_passed:
                accumulator.technical_pass_count += 1
        if item.status == EvidenceStatus.REVIEWED and item.hard_gate_passed is None:
            accumulator.data_issue_counts["reviewed_without_hard_gate"] += 1
        if item.status != EvidenceStatus.REVIEWED and item.hard_gate_passed is not None:
            accumulator.data_issue_counts["hard_gate_without_reviewed_status"] += 1
        if is_human_reviewed:
            accumulator.human_reviewed_count += 1
            if item.hard_gate_passed:
                accumulator.hard_pass_count += 1
            if item.score is None:
                accumulator.data_issue_counts["reviewed_without_score"] += 1
            else:
                accumulator.scores.append(item.score)

        task = task_by_id.get(item.task_id)
        valid_task = task
        if task is None:
            accumulator.data_issue_counts["missing_task"] += 1
        elif task.project_id != item.project_id:
            accumulator.data_issue_counts["task_project_mismatch"] += 1
            valid_task = None
        elif task.shot_id != item.shot_id:
            accumulator.data_issue_counts["task_shot_mismatch"] += 1
            valid_task = None
        elif task.route != item.route:
            accumulator.data_issue_counts["task_route_mismatch"] += 1
            valid_task = None

        if valid_task is not None:
            accumulator.task_count += 1
            accumulator.task_status_counts[valid_task.status.value] += 1
            if valid_task.status in _TERMINAL_TASK_STATUSES:
                accumulator.terminal_task_count += 1
            if valid_task.status == TaskStatus.SUCCEEDED:
                accumulator.succeeded_task_count += 1
            first_task_id = first_task_by_group.get(
                (item.purpose, item.route, item.project_id, item.shot_id)
            )
            if (
                is_human_reviewed
                and valid_task.id == first_task_id
                and valid_task.id not in counted_first_task_ids
            ):
                counted_first_task_ids.add(valid_task.id)
                accumulator.first_candidate_reviewed_count += 1
                if item.hard_gate_passed:
                    accumulator.first_candidate_hard_pass_count += 1

            provider_seconds, provider_invalid = _elapsed_seconds(
                valid_task.submitted_at,
                valid_task.finished_at,
            )
            if provider_invalid:
                accumulator.data_issue_counts["invalid_provider_timestamps"] += 1
            elif provider_seconds is not None:
                accumulator.provider_seconds.append(provider_seconds)

            total_seconds, total_invalid = _elapsed_seconds(
                valid_task.created_at,
                valid_task.finished_at,
            )
            if total_invalid:
                accumulator.data_issue_counts["invalid_total_timestamps"] += 1
            elif total_seconds is not None:
                accumulator.total_seconds.append(total_seconds)

        run = run_by_task_id.get(item.task_id)
        if run is None:
            accumulator.data_issue_counts["missing_provider_run"] += 1
        elif run.project_id != item.project_id:
            accumulator.data_issue_counts["provider_run_project_mismatch"] += 1
        elif run.shot_id != item.shot_id:
            accumulator.data_issue_counts["provider_run_shot_mismatch"] += 1
        elif run.route != item.route:
            accumulator.data_issue_counts["provider_run_route_mismatch"] += 1
        elif run.purpose != item.purpose:
            accumulator.data_issue_counts["provider_run_purpose_mismatch"] += 1
        else:
            if run.regression_case_id is None:
                accumulator.unclassified_regression_count += 1
            else:
                accumulator.regression_case_counts[run.regression_case_id] += 1
            if run.estimated_cost_usd is not None:
                accumulator.estimated_cost_usd.append(run.estimated_cost_usd)
            if run.estimated_cost_credits is not None:
                accumulator.estimated_cost_credits.append(run.estimated_cost_credits)

        take = _resolve_take(item, take_by_id, take_by_task_id, accumulator)
        if take is not None:
            if take.project_id != item.project_id:
                accumulator.data_issue_counts["take_project_mismatch"] += 1
            elif take.shot_id != item.shot_id:
                accumulator.data_issue_counts["take_shot_mismatch"] += 1
            elif take.route != item.route:
                accumulator.data_issue_counts["take_route_mismatch"] += 1
            else:
                accumulator.output_count += 1
                if is_human_reviewed:
                    accumulator.issue_category_counts.update(
                        issue.category.value for issue in take.qa_report.issues
                    )
        elif is_human_reviewed:
            accumulator.data_issue_counts["reviewed_without_take"] += 1

    purpose_reports: list[PurposeRouteEvaluation] = []
    for purpose in _PURPOSES:
        route_reports = [
            _build_bucket(purpose, route, accumulators[(purpose, route)])
            for route in _ROUTES
        ]
        purpose_reports.append(
            PurposeRouteEvaluation(
                purpose=purpose,
                evidence_count=sum(item.evidence_count for item in route_reports),
                routes=route_reports,
            )
        )

    return RouteEvaluationReport(
        project_ids=sorted({item.project_id for item in evidence}),
        purposes=purpose_reports,
    )


def _first_non_retry_task_by_group(
    evidence: Sequence[RouteEvidence],
    task_by_id: dict[str, GenerationTask],
) -> dict[tuple[EvidencePurpose, VideoGenerationRoute, str, str], str]:
    """Resolve the first submitted candidate independently for each shot slice.

    Candidate indexes are allocated across broader project history, so index 1 is
    not necessarily present in every purpose/route slice.  Retries retain their
    parent's candidate index and therefore cannot define an independent first
    candidate.
    """

    first_by_group: dict[
        tuple[EvidencePurpose, VideoGenerationRoute, str, str], GenerationTask
    ] = {}
    for item in evidence:
        task = task_by_id.get(item.task_id)
        if (
            task is None
            or task.retry_of_task_id is not None
            or task.project_id != item.project_id
            or task.shot_id != item.shot_id
            or task.route != item.route
        ):
            continue
        group = (item.purpose, item.route, item.project_id, item.shot_id)
        current = first_by_group.get(group)
        if current is None or _candidate_order(task) < _candidate_order(current):
            first_by_group[group] = task
    return {group: task.id for group, task in first_by_group.items()}


def _candidate_order(task: GenerationTask) -> tuple[int, str, str]:
    return (task.candidate_index, task.created_at, task.id)


def _resolve_take(
    evidence: RouteEvidence,
    take_by_id: dict[str, Take],
    take_by_task_id: dict[str, Take],
    accumulator: _Accumulator,
) -> Take | None:
    if evidence.take_id is not None:
        take = take_by_id.get(evidence.take_id)
        if take is not None:
            if take.task_id != evidence.task_id:
                accumulator.data_issue_counts["take_task_mismatch"] += 1
                return None
            return take
        accumulator.data_issue_counts["missing_referenced_take"] += 1
    fallback = take_by_task_id.get(evidence.task_id)
    if fallback is not None and evidence.take_id is None:
        accumulator.data_issue_counts["missing_take_link"] += 1
    return fallback


def _build_bucket(
    purpose: EvidencePurpose,
    route: VideoGenerationRoute,
    accumulator: _Accumulator,
) -> RouteEvaluationBucket:
    return RouteEvaluationBucket(
        purpose=purpose,
        route=route,
        evidence_count=accumulator.evidence_count,
        evidence_status_counts=_sorted_counts(accumulator.evidence_status_counts),
        task_count=accumulator.task_count,
        task_status_counts=_sorted_counts(accumulator.task_status_counts),
        terminal_task_count=accumulator.terminal_task_count,
        succeeded_task_count=accumulator.succeeded_task_count,
        task_success_rate=_rate(
            accumulator.succeeded_task_count,
            accumulator.terminal_task_count,
        ),
        output_count=accumulator.output_count,
        technical_assessed_count=accumulator.technical_assessed_count,
        technical_pass_count=accumulator.technical_pass_count,
        technical_pass_rate=_rate(
            accumulator.technical_pass_count,
            accumulator.technical_assessed_count,
        ),
        human_reviewed_count=accumulator.human_reviewed_count,
        hard_pass_count=accumulator.hard_pass_count,
        hard_pass_rate=_rate(
            accumulator.hard_pass_count,
            accumulator.human_reviewed_count,
        ),
        first_candidate_reviewed_count=accumulator.first_candidate_reviewed_count,
        first_candidate_hard_pass_count=accumulator.first_candidate_hard_pass_count,
        first_candidate_hard_pass_rate=_rate(
            accumulator.first_candidate_hard_pass_count,
            accumulator.first_candidate_reviewed_count,
        ),
        score_sample_count=len(accumulator.scores),
        median_score=_median(accumulator.scores),
        provider_time_sample_count=len(accumulator.provider_seconds),
        median_provider_seconds=_median(accumulator.provider_seconds),
        total_time_sample_count=len(accumulator.total_seconds),
        median_total_seconds=_median(accumulator.total_seconds),
        cost_usd_sample_count=len(accumulator.estimated_cost_usd),
        total_estimated_cost_usd=_sum(accumulator.estimated_cost_usd),
        median_estimated_cost_usd=_median(accumulator.estimated_cost_usd),
        cost_credit_sample_count=len(accumulator.estimated_cost_credits),
        total_estimated_cost_credits=_sum(accumulator.estimated_cost_credits),
        median_estimated_cost_credits=_median(accumulator.estimated_cost_credits),
        issue_category_counts=_sorted_counts(accumulator.issue_category_counts),
        regression_case_counts=_sorted_counts(accumulator.regression_case_counts),
        unclassified_regression_count=accumulator.unclassified_regression_count,
        data_issue_counts=_sorted_counts(accumulator.data_issue_counts),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), 3)


def _sum(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(float(sum(values)), 6)


def _sorted_counts(values: Counter[str]) -> dict[str, int]:
    return {key: values[key] for key in sorted(values)}


def _elapsed_seconds(start: str | None, end: str | None) -> tuple[float | None, bool]:
    if start is None or end is None:
        return None, False
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(end.replace("Z", "+00:00"))
        elapsed = (finished - started).total_seconds()
    except (TypeError, ValueError):
        return None, True
    if elapsed < 0:
        return None, True
    return elapsed, False


__all__ = [
    "PurposeRouteEvaluation",
    "RouteEvaluationBucket",
    "RouteEvaluationReport",
    "build_route_evaluation_report",
]
