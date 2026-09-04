"""Ordinary route-quality aggregation over persisted short-drama records."""

from __future__ import annotations

from short_drama.h3_models import H3PricingSnapshot, H3ProviderRun
from short_drama.models import (
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    GenerationTask,
    MockBehavior,
    OperationKind,
    QAReport,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    RouteEvidence,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.route_evaluation import (
    RouteEvaluationBucket,
    RouteEvaluationReport,
    build_route_evaluation_report,
)


_BASE_TIME = "2026-09-02T00:00:00+00:00"


def _task(
    task_id: str,
    *,
    route: VideoGenerationRoute,
    status: TaskStatus,
    shot_id: str = "shot-1",
    candidate_index: int = 1,
    retry_of_task_id: str | None = None,
    submitted_at: str | None = "2026-09-02T00:00:02+00:00",
    finished_at: str | None = "2026-09-02T00:00:12+00:00",
) -> GenerationTask:
    return GenerationTask(
        id=task_id,
        project_id="project-1",
        shot_id=shot_id,
        operation=OperationKind.GENERATE,
        candidate_index=candidate_index,
        status=status,
        provider="minimax",
        model="MiniMax-H3",
        route=route,
        resolution="768P",
        provider_run_id=f"run-{task_id}",
        provider_task_id=f"remote-{task_id}",
        request_fingerprint=f"fingerprint-{task_id}",
        input_script_version_id="script-v1",
        input_bible_version_id="bible-v1",
        input_shot_revision=1,
        input_voice_reference_revision=0,
        requested_duration_seconds=5.0,
        attempt=1,
        retry_of_task_id=retry_of_task_id,
        mock_behavior=MockBehavior.SUCCESS,
        submitted_at=submitted_at,
        started_at=submitted_at,
        finished_at=finished_at,
        created_at=_BASE_TIME,
        updated_at=finished_at or _BASE_TIME,
    )


def _run(
    task_id: str,
    *,
    route: VideoGenerationRoute,
    purpose: EvidencePurpose,
    shot_id: str = "shot-1",
    regression_case_id: str | None = "case-1",
    estimated_cost_usd: float | None = 0.4,
    estimated_cost_credits: float | None = 3.0,
) -> H3ProviderRun:
    return H3ProviderRun(
        id=f"run-{task_id}",
        task_id=task_id,
        project_id="project-1",
        shot_id=shot_id,
        route=route,
        purpose=purpose,
        regression_case_id=regression_case_id,
        prompt="A bounded test prompt.",
        pricing=H3PricingSnapshot(captured_at=_BASE_TIME),
        estimated_cost_usd=estimated_cost_usd,
        estimated_cost_credits=estimated_cost_credits,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
    )


def _take(
    task_id: str,
    *,
    route: VideoGenerationRoute,
    passed: bool,
    score: float,
    shot_id: str = "shot-1",
    issues: list[QaIssue] | None = None,
) -> Take:
    take_id = f"take-{task_id}"
    status = QaStatus.PASSED if passed else QaStatus.REJECTED
    return Take(
        id=take_id,
        project_id="project-1",
        shot_id=shot_id,
        task_id=task_id,
        candidate_index=1,
        operation=OperationKind.GENERATE,
        provider="minimax",
        model="MiniMax-H3",
        route=route,
        media_path=f"/tmp/{take_id}.mp4",
        thumbnail_path=f"/tmp/{take_id}.jpg",
        duration_seconds=5.0,
        width=768,
        height=1366,
        availability=TakeAvailability.AVAILABLE,
        qa_status=status,
        is_selected=False,
        freshness=Freshness.CURRENT,
        qa_report=QAReport(
            id=f"qa-{task_id}",
            take_id=take_id,
            status=status,
            hard_gate_passed=passed,
            score=score,
            issues=list(issues or []),
            checks=["full-playback"],
            reviewed_at="2026-09-02T00:01:00+00:00",
            created_at=_BASE_TIME,
        ),
        created_at=_BASE_TIME,
    )


def _evidence(
    task_id: str,
    *,
    route: VideoGenerationRoute,
    purpose: EvidencePurpose,
    shot_id: str = "shot-1",
    status: EvidenceStatus = EvidenceStatus.REVIEWED,
    technical_passed: bool | None = True,
    hard_gate_passed: bool | None = True,
    score: float | None = 4.5,
    take_id: str | None = None,
) -> RouteEvidence:
    return RouteEvidence(
        id=f"evidence-{task_id}",
        project_id="project-1",
        shot_id=shot_id,
        task_id=task_id,
        take_id=take_id if take_id is not None else f"take-{task_id}",
        provider="minimax",
        model="MiniMax-H3",
        route=route,
        resolution="768P",
        purpose=purpose,
        status=status,
        technical_passed=technical_passed,
        hard_gate_passed=hard_gate_passed,
        score=score,
        notes="Full-playback review.",
        created_at=_BASE_TIME,
        reviewed_at=(
            "2026-09-02T00:01:00+00:00"
            if status == EvidenceStatus.REVIEWED
            else None
        ),
    )


def _bucket(
    report: RouteEvaluationReport,
    purpose: EvidencePurpose,
    route: VideoGenerationRoute,
) -> RouteEvaluationBucket:
    purpose_report = next(item for item in report.purposes if item.purpose == purpose)
    return next(item for item in purpose_report.routes if item.route == route)


def test_route_metrics_keep_evidence_purposes_strictly_separate() -> None:
    contact_issue = QaIssue(
        code="CONTACT_DRIFT",
        severity="ERROR",
        message="The hand loses contact with the prop.",
        repair_layer="DIRECTOR_AGENT",
        category=QaFailureCategory.CONTACT_CONTINUITY,
    )
    tasks = [
        _task(
            "quality-pass",
            route=VideoGenerationRoute.DIRECT,
            status=TaskStatus.SUCCEEDED,
        ),
        _task(
            "quality-fail",
            route=VideoGenerationRoute.DIRECT,
            status=TaskStatus.SUCCEEDED,
            candidate_index=2,
            submitted_at="2026-09-02T00:00:01+00:00",
            finished_at="2026-09-02T00:00:21+00:00",
        ),
        _task(
            "quality-provider-fail",
            route=VideoGenerationRoute.FRAME_FIRST,
            status=TaskStatus.FAILED,
            finished_at="2026-09-02T00:00:07+00:00",
        ),
        _task(
            "smoke-pass",
            route=VideoGenerationRoute.DIRECT,
            status=TaskStatus.SUCCEEDED,
        ),
        _task(
            "production-fail",
            route=VideoGenerationRoute.DIRECT,
            status=TaskStatus.SUCCEEDED,
        ),
    ]
    runs = [
        _run(
            "quality-pass",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.QUALITY_PROBE,
        ),
        _run(
            "quality-fail",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.QUALITY_PROBE,
        ),
        _run(
            "quality-provider-fail",
            route=VideoGenerationRoute.FRAME_FIRST,
            purpose=EvidencePurpose.QUALITY_PROBE,
            regression_case_id=None,
        ),
        _run(
            "smoke-pass",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.CONTRACT_SMOKE,
        ),
        _run(
            "production-fail",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.PRODUCTION,
        ),
    ]
    takes = [
        _take(
            "quality-pass",
            route=VideoGenerationRoute.DIRECT,
            passed=True,
            score=4.5,
        ),
        _take(
            "quality-fail",
            route=VideoGenerationRoute.DIRECT,
            passed=False,
            score=2.5,
            issues=[contact_issue],
        ),
        _take(
            "smoke-pass",
            route=VideoGenerationRoute.DIRECT,
            passed=True,
            score=5.0,
        ),
        _take(
            "production-fail",
            route=VideoGenerationRoute.DIRECT,
            passed=False,
            score=1.0,
            issues=[contact_issue],
        ),
    ]
    evidence = [
        _evidence(
            "quality-pass",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.QUALITY_PROBE,
            score=4.5,
        ),
        _evidence(
            "quality-fail",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.QUALITY_PROBE,
            hard_gate_passed=False,
            score=2.5,
        ),
        _evidence(
            "quality-provider-fail",
            route=VideoGenerationRoute.FRAME_FIRST,
            purpose=EvidencePurpose.QUALITY_PROBE,
            status=EvidenceStatus.FAILED,
            technical_passed=None,
            hard_gate_passed=None,
            score=None,
            take_id="missing-take",
        ),
        _evidence(
            "smoke-pass",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.CONTRACT_SMOKE,
            score=5.0,
        ),
        _evidence(
            "production-fail",
            route=VideoGenerationRoute.DIRECT,
            purpose=EvidencePurpose.PRODUCTION,
            hard_gate_passed=False,
            score=1.0,
        ),
    ]

    report = build_route_evaluation_report(evidence, tasks, runs, takes)

    assert [item.purpose for item in report.purposes] == [
        EvidencePurpose.QUALITY_PROBE,
        EvidencePurpose.CONTRACT_SMOKE,
        EvidencePurpose.PRODUCTION,
    ]
    assert all(len(item.routes) == len(VideoGenerationRoute) for item in report.purposes)

    quality_direct = _bucket(
        report,
        EvidencePurpose.QUALITY_PROBE,
        VideoGenerationRoute.DIRECT,
    )
    assert quality_direct.evidence_count == 2
    assert quality_direct.task_count == 2
    assert quality_direct.task_success_rate == 1.0
    assert quality_direct.output_count == 2
    assert quality_direct.technical_pass_rate == 1.0
    assert quality_direct.human_reviewed_count == 2
    assert quality_direct.hard_pass_rate == 0.5
    assert quality_direct.first_candidate_reviewed_count == 1
    assert quality_direct.first_candidate_hard_pass_rate == 1.0
    assert quality_direct.median_score == 3.5
    assert quality_direct.median_provider_seconds == 15.0
    assert quality_direct.median_total_seconds == 16.5
    assert quality_direct.cost_usd_sample_count == 2
    assert quality_direct.total_estimated_cost_usd == 0.8
    assert quality_direct.median_estimated_cost_usd == 0.4
    assert quality_direct.cost_credit_sample_count == 2
    assert quality_direct.total_estimated_cost_credits == 6.0
    assert quality_direct.median_estimated_cost_credits == 3.0
    assert quality_direct.issue_category_counts == {"CONTACT_CONTINUITY": 1}
    assert quality_direct.regression_case_counts == {"case-1": 2}

    quality_frame = _bucket(
        report,
        EvidencePurpose.QUALITY_PROBE,
        VideoGenerationRoute.FRAME_FIRST,
    )
    assert quality_frame.evidence_count == 1
    assert quality_frame.task_success_rate == 0.0
    assert quality_frame.hard_pass_rate is None
    assert quality_frame.unclassified_regression_count == 1

    smoke_direct = _bucket(
        report,
        EvidencePurpose.CONTRACT_SMOKE,
        VideoGenerationRoute.DIRECT,
    )
    production_direct = _bucket(
        report,
        EvidencePurpose.PRODUCTION,
        VideoGenerationRoute.DIRECT,
    )
    assert smoke_direct.evidence_count == 1
    assert smoke_direct.hard_pass_rate == 1.0
    assert production_direct.evidence_count == 1
    assert production_direct.hard_pass_rate == 0.0
    assert quality_direct.evidence_count == 2


def test_route_evaluation_reports_incomplete_joins_without_reclassifying_sample() -> None:
    evidence = _evidence(
        "missing",
        route=VideoGenerationRoute.REFERENCE,
        purpose=EvidencePurpose.QUALITY_PROBE,
        score=4.0,
        take_id="missing-take",
    )

    report = build_route_evaluation_report([evidence], [], [], [])
    bucket = _bucket(
        report,
        EvidencePurpose.QUALITY_PROBE,
        VideoGenerationRoute.REFERENCE,
    )

    assert report.project_ids == ["project-1"]
    assert bucket.evidence_count == 1
    assert bucket.hard_pass_rate == 1.0
    assert bucket.task_count == 0
    assert bucket.output_count == 0
    assert bucket.data_issue_counts == {
        "missing_provider_run": 1,
        "missing_referenced_take": 1,
        "missing_task": 1,
        "reviewed_without_take": 1,
    }


def test_invalid_or_reversed_timestamps_are_excluded_from_time_metrics() -> None:
    invalid = _task(
        "invalid-time",
        route=VideoGenerationRoute.FRAME_FIRST_LAST,
        status=TaskStatus.FAILED,
        submitted_at="not-a-timestamp",
        finished_at="2026-09-01T23:59:59+00:00",
    )
    report = build_route_evaluation_report(
        [
            _evidence(
                "invalid-time",
                route=VideoGenerationRoute.FRAME_FIRST_LAST,
                purpose=EvidencePurpose.QUALITY_PROBE,
                status=EvidenceStatus.FAILED,
                technical_passed=None,
                hard_gate_passed=None,
                score=None,
                take_id="missing-take",
            )
        ],
        [invalid],
        [
            _run(
                "invalid-time",
                route=VideoGenerationRoute.FRAME_FIRST_LAST,
                purpose=EvidencePurpose.QUALITY_PROBE,
            )
        ],
        [],
    )
    bucket = _bucket(
        report,
        EvidencePurpose.QUALITY_PROBE,
        VideoGenerationRoute.FRAME_FIRST_LAST,
    )

    assert bucket.provider_time_sample_count == 0
    assert bucket.total_time_sample_count == 0
    assert bucket.median_provider_seconds is None
    assert bucket.median_total_seconds is None
    assert bucket.data_issue_counts["invalid_provider_timestamps"] == 1
    assert bucket.data_issue_counts["invalid_total_timestamps"] == 1


def test_first_candidate_is_the_first_non_retry_task_in_each_shot_slice() -> None:
    route = VideoGenerationRoute.REFERENCE
    purpose = EvidencePurpose.QUALITY_PROBE
    tasks = [
        _task(
            "shot-a-first",
            route=route,
            status=TaskStatus.SUCCEEDED,
            shot_id="shot-a",
            candidate_index=7,
        ),
        _task(
            "shot-a-retry",
            route=route,
            status=TaskStatus.SUCCEEDED,
            shot_id="shot-a",
            candidate_index=7,
            retry_of_task_id="shot-a-first",
        ),
        _task(
            "shot-a-second",
            route=route,
            status=TaskStatus.SUCCEEDED,
            shot_id="shot-a",
            candidate_index=8,
        ),
        _task(
            "shot-b-first",
            route=route,
            status=TaskStatus.SUCCEEDED,
            shot_id="shot-b",
            candidate_index=3,
        ),
    ]
    passed_by_task = {
        "shot-a-first": False,
        "shot-a-retry": True,
        "shot-a-second": True,
        "shot-b-first": True,
    }
    shot_by_task = {
        "shot-a-first": "shot-a",
        "shot-a-retry": "shot-a",
        "shot-a-second": "shot-a",
        "shot-b-first": "shot-b",
    }
    evidence = [
        _evidence(
            task.id,
            route=route,
            purpose=purpose,
            shot_id=shot_by_task[task.id],
            hard_gate_passed=passed_by_task[task.id],
        )
        for task in tasks
    ]
    runs = [
        _run(
            task.id,
            route=route,
            purpose=purpose,
            shot_id=shot_by_task[task.id],
        )
        for task in tasks
    ]
    takes = [
        _take(
            task.id,
            route=route,
            passed=passed_by_task[task.id],
            score=4.0,
            shot_id=shot_by_task[task.id],
        )
        for task in tasks
    ]

    report = build_route_evaluation_report(evidence, tasks, runs, takes)
    bucket = _bucket(report, purpose, route)

    assert bucket.task_count == 4
    assert bucket.cost_usd_sample_count == 4
    assert bucket.first_candidate_reviewed_count == 2
    assert bucket.first_candidate_hard_pass_count == 1
    assert bucket.first_candidate_hard_pass_rate == 0.5


def test_shot_mismatches_are_reported_and_excluded_from_joined_metrics() -> None:
    task_id = "wrong-shot"
    route = VideoGenerationRoute.FRAME_FIRST
    purpose = EvidencePurpose.QUALITY_PROBE
    report = build_route_evaluation_report(
        [_evidence(task_id, route=route, purpose=purpose)],
        [
            _task(
                task_id,
                route=route,
                status=TaskStatus.SUCCEEDED,
                shot_id="shot-other",
            )
        ],
        [
            _run(
                task_id,
                route=route,
                purpose=purpose,
                shot_id="shot-other",
            )
        ],
        [
            _take(
                task_id,
                route=route,
                passed=True,
                score=4.5,
                shot_id="shot-other",
            )
        ],
    )
    bucket = _bucket(report, purpose, route)

    assert bucket.task_count == 0
    assert bucket.provider_time_sample_count == 0
    assert bucket.cost_usd_sample_count == 0
    assert bucket.total_estimated_cost_usd is None
    assert bucket.output_count == 0
    assert bucket.issue_category_counts == {}
    assert bucket.first_candidate_reviewed_count == 0
    assert bucket.data_issue_counts == {
        "provider_run_shot_mismatch": 1,
        "take_shot_mismatch": 1,
        "task_shot_mismatch": 1,
    }
