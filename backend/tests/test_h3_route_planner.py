"""Offline coverage for evidence-first H3 production route planning."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from short_drama.h3_models import (
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
    H3ProviderConfigUpdate,
    H3RoutePlanStatus,
    H3ShotRouteAction,
    H3ShotRouteExecutionStatus,
    H3ShotRouteReadiness,
)
from short_drama.models import (
    AssetMediaType,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    OperationKind,
    QAReport,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.providers.minimax_h3_compiler import build_h3_prompt
from short_drama.h3_route_planner import _route_agent_batches
from short_drama.service import (
    ShortDramaService,
    WorkflowConflictError,
    _BOUNDED_BEST_EFFORT_CHECK,
    _can_receive_bounded_best_effort_waiver,
    _prioritize_keyframe_canvas,
    _scope_keyframe_reference_versions,
    _timeline_clip_window,
)


NOW = "2026-08-17T12:00:00+00:00"


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _render_video(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=768x1344:r=12:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _persist_passed_h3_take(
    service: ShortDramaService,
    *,
    shot_id: str,
    media_path: Path,
    selected: bool = False,
    score: float = 4.5,
) -> Take:
    document = service.repository.get_project("project-dont-trust-me")
    assert document is not None
    shot = next(item for item in document.shots if item.id == shot_id)
    task, run, evidence = service._new_h3_task_and_run(
        document=document,
        shot_id=shot.id,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=max(4, min(15, round(shot.duration_seconds))),
        prompt=build_h3_prompt(document, shot),
        media_inputs=[],
        purpose=EvidencePurpose.QUALITY_PROBE,
        learning_objective="Verify a reusable current-version production route.",
        regression_case_id=None,
        candidate_index=1,
        attempt=1,
        retry_of_task_id=None,
    )
    task.status = TaskStatus.SUCCEEDED
    task.finished_at = NOW
    task.updated_at = NOW
    service.repository.save_task(task)
    service.repository.save_provider_run(run)
    take = Take(
        id=f"take-{shot.id}",
        project_id=document.project.id,
        shot_id=shot.id,
        task_id=task.id,
        candidate_index=1,
        operation=task.operation,
        provider="minimax",
        model="MiniMax-H3",
        route=VideoGenerationRoute.DIRECT,
        media_path=str(media_path),
        thumbnail_path=str(media_path),
        duration_seconds=1.0,
        width=768,
        height=1344,
        availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.PASSED,
        is_selected=selected,
        freshness=Freshness.CURRENT,
        qa_report=QAReport(
            id=f"qa-{shot.id}",
            take_id=f"take-{shot.id}",
            status=QaStatus.PASSED,
            hard_gate_passed=True,
            score=score,
            issues=[],
            checks=["technical-media", "human-quality-review"],
            reviewer_notes="Current-version H3 Take accepted for production reuse.",
            reviewed_at=NOW,
            created_at=NOW,
        ),
        created_at=NOW,
    )
    service.repository.save_take(take)
    evidence.take_id = take.id
    evidence.status = EvidenceStatus.REVIEWED
    evidence.technical_passed = True
    evidence.hard_gate_passed = True
    evidence.score = score
    evidence.notes = "Accepted production-quality evidence."
    evidence.reviewed_at = NOW
    service.repository.save_route_evidence(evidence)
    return take


def test_route_agent_batches_by_scene_and_bounded_size(tmp_path: Path) -> None:
    project = _service(tmp_path).initialize_fixture()
    source = project.document.shots[0]
    shots = [
        source.model_copy(
            update={
                "id": f"shot-{index:03d}",
                "sequence": index,
                "scene_id": "scene-1" if index <= 25 else "scene-2",
            }
        )
        for index in range(1, 46)
    ]

    batches = _route_agent_batches(shots)

    assert [len(batch) for batch in batches] == [20, 5, 20]
    assert [{shot.scene_id for shot in batch} for batch in batches] == [
        {"scene-1"},
        {"scene-1"},
        {"scene-2"},
    ]


def test_route_plan_reuses_reviewed_h3_takes_and_builds_real_manifest(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    video = tmp_path / "shared-h3.mp4"
    _render_video(video)
    for shot in project.document.shots:
        _persist_passed_h3_take(
            service,
            shot_id=shot.id,
            media_path=video,
        )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(use_language_agent=False),
    )

    assert compiled.plan.status == H3RoutePlanStatus.READY
    assert compiled.plan.estimated_new_h3_seconds == 0
    assert all(
        item.action == H3ShotRouteAction.REUSE_TAKE
        for item in compiled.plan.decisions
    )
    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            semantic_review=False,
            rebuild_manifest=True,
        ),
    )

    assert executed.plan.status == H3RoutePlanStatus.COMPLETE
    assert executed.queued_tasks == []
    assert executed.manifest is not None
    assert len(executed.manifest.video_clips) == len(project.document.shots)
    assert {item.role for item in executed.manifest.audio_clips} == {"MODEL_NATIVE_H3"}
    selected = [item for item in executed.project.takes if item.is_selected]
    assert len(selected) == len(project.document.shots)
    assert {item.provider for item in selected} == {"minimax"}


def test_timeline_clip_window_respects_director_duration_and_preserves_action_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    action_shot = project.document.shots[0].model_copy(deep=True)
    action_shot.duration_seconds = 2.5
    action_shot.dialogue_line_ids = []
    action_shot.continuity.action_in = "The prop is still worn."
    action_shot.continuity.action_out = "The prop is held by the other character."
    action_take = _persist_passed_h3_take(
        service,
        shot_id=action_shot.id,
        media_path=tmp_path / "unused-action.mp4",
    )
    action_take.duration_seconds = 4.46

    source_in, duration = _timeline_clip_window(action_shot, action_take)

    assert source_in == pytest.approx(1.96)
    assert duration == 2.5

    dialogue_shot = project.document.shots[1].model_copy(deep=True)
    dialogue_shot.duration_seconds = 2.5
    dialogue_take = action_take.model_copy(
        update={"shot_id": dialogue_shot.id},
        deep=True,
    )

    source_in, duration = _timeline_clip_window(dialogue_shot, dialogue_take)

    assert source_in == 0.0
    assert duration == 2.5

    extended_take = dialogue_take.model_copy(
        update={"operation": OperationKind.EXTEND},
        deep=True,
    )
    source_in, duration = _timeline_clip_window(dialogue_shot, extended_take)
    assert source_in == 0.0
    assert duration == 4.46


def test_route_plan_compiles_long_take_observation_and_advisory_asset_briefs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    shot = next(item for item in document.shots if item.sequence == 3)
    shot.duration_seconds = 15.0
    shot.local_rework_risk = "中；长镜头动作与构图需要导演权衡"
    service.repository.save_project(document)

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.action == H3ShotRouteAction.GENERATE
    assert decision.route == VideoGenerationRoute.FRAME_FIRST
    assert decision.inspection.max_frames == 23
    assert decision.inspection.scopes == [
        "INTRA_SHOT",
        "SHOT_BOUNDARY",
    ]
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
    assert decision.asset_preparations
    assert all(item.advisory_only for item in decision.asset_preparations)
    assert decision.override_allowed is True


def test_lightweight_bindings_become_executable_reference_candidate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    for index, asset in enumerate(document.assets, start=1):
        image_path = tmp_path / f"approved-reference-{index}.png"
        Image.new("RGB", (768, 1344), f"#{index:02x}3344").save(image_path)
        version = asset.versions[0]
        version.media_type = AssetMediaType.IMAGE
        version.local_path = str(image_path)
        asset.approved_version_id = version.id
    shot = next(item for item in document.shots if item.sequence == 2)
    shot.local_rework_risk = "低；稳定对话近景"
    service.repository.save_project(document)

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.REFERENCE
    assert decision.media_inputs
    assert all(item.asset_version_id is not None for item in decision.media_inputs)


def test_failed_advisory_keyframe_can_fall_back_to_approved_references(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    for index, asset in enumerate(document.assets, start=1):
        image_path = tmp_path / f"fallback-reference-{index}.png"
        Image.new("RGB", (768, 1344), f"#{index:02x}5566").save(image_path)
        version = asset.versions[0]
        version.media_type = AssetMediaType.IMAGE
        version.local_path = str(image_path)
        asset.approved_version_id = version.id
    shot = next(item for item in document.shots if item.sequence == 3)
    shot.local_rework_risk = "中；关键帧建议可被替代"
    service.repository.save_project(document)
    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    decision = compiled.plan.decisions[0]
    preparation = decision.asset_preparations[0]
    persisted_document = service.repository.get_project(document.project.id)
    assert persisted_document is not None

    changed = service._fallback_failed_keyframe_preparation(
        document=persisted_document,
        plan=compiled.plan,
        decision=decision,
        failed_preparation=preparation,
        reason="Three keyframe candidates failed semantic review.",
    )

    assert changed is True
    assert decision.route == VideoGenerationRoute.REFERENCE
    assert decision.readiness == H3ShotRouteReadiness.READY_WITH_WARNINGS
    assert decision.media_inputs
    assert decision.override_allowed is True
    assert "user may override" in decision.warnings[-1]
    assert all("起始帧" not in warning for warning in decision.warnings)
    expected_reference_ids = [
        item.asset_version_id
        for item in decision.media_inputs
        if item.asset_version_id is not None
    ]
    assert preparation.input_version_ids == expected_reference_ids

    decision.media_inputs = []
    assert service._refresh_rejected_keyframe_fallback(
        persisted_document,
        decision,
    ) is True
    assert [
        item.asset_version_id
        for item in decision.media_inputs
        if item.asset_version_id is not None
    ] == expected_reference_ids
    assert service._refresh_rejected_keyframe_fallback(
        persisted_document,
        decision,
    ) is False


def test_applied_change_keyframe_repair_is_executed_by_next_route_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    for index, asset in enumerate(document.assets, start=1):
        image_path = tmp_path / f"repair-reference-{index}.png"
        Image.new("RGB", (768, 1344), f"#{index:02x}6677").save(image_path)
        version = asset.versions[0]
        version.media_type = AssetMediaType.IMAGE
        version.local_path = str(image_path)
        asset.approved_version_id = version.id
    shot = next(item for item in document.shots if item.sequence == 2)
    shot.local_rework_risk = "低；稳定对话近景"
    shot.revision += 1
    document.repair_history.append(
        DirectorRepairRecord(
            id="director-repair-change-keyframe",
            source_shot_id=shot.id,
            strategy=DirectorRepairStrategy.CHANGE_KEYFRAME,
            decision=DirectorRepairDecision.APPLIED,
            reason="Replace the failed visual anchor before regenerating.",
            affected_shot_ids=[shot.id],
            created_at=NOW,
        )
    )
    service.repository.save_project(document)

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.FRAME_FIRST
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
    assert decision.asset_preparations[0].role.value == "first_frame"


def test_applied_change_camera_repair_prepares_matching_frame_control(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    shot = next(item for item in document.shots if item.sequence == 2)
    shot.local_rework_risk = "低；稳定对话近景"
    shot.camera = "fixed tighter single coverage"
    shot.revision += 1
    document.repair_history.append(
        DirectorRepairRecord(
            id="director-repair-change-camera",
            source_shot_id=shot.id,
            strategy=DirectorRepairStrategy.CHANGE_CAMERA,
            decision=DirectorRepairDecision.APPLIED,
            reason="Use a different coverage design after the previous route failed.",
            affected_shot_ids=[shot.id],
            created_at=NOW,
        )
    )
    service.repository.save_project(document)

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.FRAME_FIRST
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
    assert decision.asset_preparations[0].role.value == "first_frame"
    assert decision.override_allowed is True


def test_keyframe_reference_scope_and_primary_canvas_follow_route_and_framing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    lin = next(item for item in document.assets if item.name == "林夏定妆")
    zhou = next(item for item in document.assets if item.name == "周衡定妆")
    location = next(item for item in document.assets if item.kind.value == "LOCATION")
    prop = next(item for item in document.assets if item.kind.value == "PROP")
    bound = [
        (lin, lin.versions[0]),
        (zhou, zhou.versions[0]),
        (location, location.versions[0]),
        (prop, prop.versions[0]),
    ]
    shot_one = next(item for item in document.shots if item.sequence == 1)
    shot_four = next(item for item in document.shots if item.sequence == 4)
    shot_five = next(item for item in document.shots if item.sequence == 5)
    plan = next(item for item in document.reference_plan if item.shot_id == shot_one.id)

    scoped = _scope_keyframe_reference_versions(
        document,
        bound,
        plan,
        [lin.versions[0].id, location.versions[0].id],
    )

    assert [version.id for _asset, version in scoped] == [
        lin.versions[0].id,
        location.versions[0].id,
    ]
    assert _prioritize_keyframe_canvas(bound, shot_one)[0][0].kind.value == "LOCATION"
    assert _prioritize_keyframe_canvas(bound, shot_four)[0][0].kind.value == "CHARACTER"
    assert _prioritize_keyframe_canvas(bound, shot_five)[0][0].kind.value == "PROP"


def test_bounded_best_effort_keeps_rejected_qa_visible_but_allows_timeline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    video = tmp_path / "best-effort.mp4"
    _render_video(video)
    takes = [
        _persist_passed_h3_take(
            service,
            shot_id=shot.id,
            media_path=video,
            selected=True,
        )
        for shot in project.document.shots
    ]
    best_effort = takes[0]
    best_effort.qa_status = QaStatus.REJECTED
    best_effort.qa_report.status = QaStatus.REJECTED
    best_effort.qa_report.hard_gate_passed = False
    best_effort.qa_report.score = 2.95
    best_effort.qa_report.issues = [
        QaIssue(
            code="PROP_APPEARANCE_REFERENCE_DRIFT",
            severity="HIGH",
            message="The prop appearance drifted but remained singular and correctly held.",
            repair_layer="PROP_ASSET",
            category=QaFailureCategory.PROP_CONTINUITY,
        )
    ]
    evidence = service.repository.get_route_evidence_for_take(best_effort.id)
    assert evidence is not None
    evidence.hard_gate_passed = False
    service.repository.save_route_evidence(evidence)

    assert _can_receive_bounded_best_effort_waiver(best_effort, evidence) is True
    best_effort.qa_report.checks.append(_BOUNDED_BEST_EFFORT_CHECK)
    service.repository.save_take(best_effort)

    manifest = service.rebuild_manifest(project.document.project.id)

    assert len(manifest.video_clips) == len(project.document.shots)
    aggregate = service.get_project(project.document.project.id)
    persisted = next(item for item in aggregate.takes if item.id == best_effort.id)
    assert persisted.qa_status == QaStatus.REJECTED
    assert persisted.qa_report.hard_gate_passed is False
    assert aggregate.progress.shots_with_selected_take == len(project.document.shots)

    best_effort.qa_report.checks.remove(_BOUNDED_BEST_EFFORT_CHECK)
    best_effort.qa_report.issues = [
        QaIssue(
            code="CHARACTER_REFERENCE_DRIFT",
            severity="HIGH",
            message="The lead identity drifted from the approved character asset.",
            repair_layer="CHARACTER_GENERATION",
            category=QaFailureCategory.IDENTITY_CONTINUITY,
        )
    ]
    assert _can_receive_bounded_best_effort_waiver(best_effort, evidence) is False


def test_route_execution_invokes_intra_shot_and_boundary_observation_tools(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    video = tmp_path / "observed-h3.mp4"
    _render_video(video)
    shots = project.document.shots[:2]
    for shot in shots:
        _persist_passed_h3_take(service, shot_id=shot.id, media_path=video)
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[item.id for item in shots],
            use_language_agent=False,
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            inspect_reused_takes=True,
            semantic_review=False,
            rebuild_manifest=False,
        ),
    )

    assert len(executed.inspections) == 2
    first, second = executed.inspections
    assert first.scopes == ["INTRA_SHOT"]
    assert second.scopes == ["INTRA_SHOT", "SHOT_BOUNDARY"]
    assert second.boundary_contact_sheet_path is not None
    assert all(report.samples for report in executed.inspections)


def test_route_plan_can_enqueue_production_tasks_without_calling_provider(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-key"))
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            candidate_count=2,
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert executed.plan.status == H3RoutePlanStatus.EXECUTING
    assert executed.plan.decisions[0].execution_status == H3ShotRouteExecutionStatus.QUEUED
    assert len(executed.queued_tasks) == 2
    assert all(item.status == TaskStatus.QUEUED for item in executed.queued_tasks)
    saved_evidence = service.repository.list_route_evidence(project.document.project.id)
    assert {item.purpose for item in saved_evidence} == {EvidencePurpose.PRODUCTION}


def test_route_plan_becomes_stale_after_shot_revision_changes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )
    document = service.repository.get_project(project.document.project.id)
    assert document is not None
    document.shots[0].revision += 1
    service.repository.save_project(document)

    with pytest.raises(WorkflowConflictError, match="outdated shot revisions"):
        service.execute_h3_production_route_plan(
            project.document.project.id,
            compiled.plan.id,
            H3ProductionRouteExecuteRequest(rebuild_manifest=False),
        )

    persisted = service.repository.get_h3_route_plan(compiled.plan.id)
    assert persisted is not None
    assert persisted.status == H3RoutePlanStatus.STALE


def test_production_route_plan_api_supports_free_dry_run(client) -> None:
    project = client.post("/api/short-drama/fixtures/dont-trust-me").json()
    project_id = project["document"]["project"]["id"]
    shot_id = project["document"]["shots"][0]["id"]

    compiled = client.post(
        f"/api/short-drama/projects/{project_id}/h3/production-route-plans",
        json={
            "shot_ids": [shot_id],
            "use_language_agent": False,
            "candidate_count": 1,
        },
    )
    assert compiled.status_code == 200, compiled.text
    plan = compiled.json()["plan"]
    assert plan["estimated_new_h3_seconds"] == 5
    assert plan["estimated_cost_credits"] == 3.003541
    assert plan["decisions"][0]["action"] == "GENERATE"

    listed = client.get(
        f"/api/short-drama/projects/{project_id}/h3/production-route-plans"
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["plans"][0]["id"] == plan["id"]

    executed = client.post(
        f"/api/short-drama/projects/{project_id}/h3/production-route-plans/{plan['id']}/execute",
        json={
            "enqueue_new_generations": False,
            "inspect_reused_takes": False,
            "rebuild_manifest": False,
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["plan"]["status"] == "PARTIAL"
    assert executed.json()["queued_tasks"] == []
