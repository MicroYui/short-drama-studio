"""Offline coverage for evidence-first H3 production route planning."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from short_drama.h3_models import (
    H3CameraMotionPreset,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
    H3ProviderConfigUpdate,
    H3RoutePlanStatus,
    H3ShotRouteAction,
    H3ShotRouteExecutionStatus,
    H3ShotRouteReadiness,
    H3VideoGuideBaseSource,
    H3VideoGuideKind,
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
    ProductionProfile,
    QAReport,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.media import probe_media_with_ffmpeg
from short_drama.providers.minimax_h3_compiler import build_h3_prompt
from short_drama.h3_route_planner import _route_agent_batches
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.service import (
    ShortDramaService,
    WorkflowConflictError,
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


def test_video_guides_are_prepared_as_visual_only_optional_route_inputs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    source_shot, target_shot = project.document.shots[:2]
    video = tmp_path / "preceding-take.mp4"
    _render_video(video, duration=5.0)
    _persist_passed_h3_take(
        service,
        shot_id=source_shot.id,
        media_path=video,
        selected=True,
    )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[target_shot.id],
            use_language_agent=False,
            consider_video_guides=True,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert {item.kind for item in decision.video_guides} == {
        H3VideoGuideKind.PREVIOUS_TAKE,
        H3VideoGuideKind.TAIL_WINDOW,
        H3VideoGuideKind.CAMERA_PREVIS,
    }
    assert decision.selected_video_guide_id is None
    assert decision.override_allowed is True
    previs = next(
        item
        for item in decision.video_guides
        if item.kind == H3VideoGuideKind.CAMERA_PREVIS
    )
    assert previs.camera_motion == H3CameraMotionPreset.DOLLY_IN
    assert previs.previs_base_source == H3VideoGuideBaseSource.PREVIOUS_BOUNDARY_FRAME
    assert previs.base_asset_version_id is None
    for guide in decision.video_guides:
        path = Path(guide.media_path)
        assert path.is_file() and path.stat().st_size > 0
        metadata = probe_media_with_ffmpeg(path)
        assert metadata["size"] == "768x1344"
        assert metadata["audio"] == "missing"
        assert float(metadata["duration"]) == pytest.approx(
            guide.duration_seconds,
            abs=0.08,
        )


def test_camera_previs_prefers_the_target_approved_start_frame(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    source_shot, target_shot = project.document.shots[:2]
    video = tmp_path / "preceding-take.mp4"
    _render_video(video, duration=5.0)
    source_take = _persist_passed_h3_take(
        service,
        shot_id=source_shot.id,
        media_path=video,
        selected=True,
    )
    target_frame = tmp_path / "target-approved-start.png"
    Image.new("RGB", (768, 1344), "#bf4f3f").save(target_frame)

    guides_by_shot, warnings = service.h3_video_guide_builder.prepare(
        document=project.document,
        takes=[source_take],
        shot_ids=[target_shot.id],
        created_at=NOW,
        prepared_media_inputs={
            target_shot.id: [
                H3MediaInput(
                    kind=H3MediaKind.IMAGE,
                    role=H3MediaRole.FIRST_FRAME,
                    source=H3MediaSource(local_path=str(target_frame)),
                    asset_version_id="asset-version-target-start",
                )
            ]
        },
    )

    assert warnings == []
    previs = next(
        item
        for item in guides_by_shot[target_shot.id]
        if item.kind == H3VideoGuideKind.CAMERA_PREVIS
    )
    assert previs.previs_base_source == H3VideoGuideBaseSource.TARGET_START_FRAME
    assert previs.base_asset_version_id == "asset-version-target-start"
    assert "asset-version-target-start" in previs.id
    assert "previs-target-asset-version-target-start" in previs.media_path
    metadata = probe_media_with_ffmpeg(Path(previs.media_path))
    assert metadata["size"] == "768x1344"
    assert metadata["audio"] == "missing"


def test_route_agent_can_choose_tail_window_without_making_it_a_gate(
    tmp_path: Path,
) -> None:
    class FakeRouteAgent:
        def __init__(self, output: str) -> None:
            self.output = output
            self.calls: list[tuple[str, str]] = []

        def validate_credentials(self) -> None:
            return None

        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            del image_urls, max_output_tokens
            self.calls.append((instructions, input_text))
            return self.output

    base_service = _service(tmp_path)
    project = base_service.initialize_fixture()
    source_shot, target_shot = project.document.shots[:2]
    source_video = tmp_path / "route-agent-source.mp4"
    _render_video(source_video, duration=5.0)
    source_take = _persist_passed_h3_take(
        base_service,
        shot_id=source_shot.id,
        media_path=source_video,
        selected=True,
    )
    tail_id = (
        f"h3-video-guide-tail_window-{target_shot.id}-{source_take.id}-"
        f"r{target_shot.revision}"
    )
    fake = FakeRouteAgent(
        "{\"choices\":[{"
        f"\"shot_id\":\"{target_shot.id}\","
        f"\"candidate_id\":\"video-guide:{tail_id}\","
        "\"rationale\":\"The boundary state matters more than the wider prior edit.\","
        "\"qa_focus_categories\":[\"SPATIAL_CONTINUITY\",\"CHARACTER_ORIENTATION\"],"
        "\"warnings\":[\"The recommendation is optional and should be boundary-reviewed.\"]"
        "}]}"
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    service.language_config.update(
        LanguageProviderConfigUpdate(
            api_key="test-key",
            base_url="https://example.invalid/v1",
        )
    )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[target_shot.id],
            use_language_agent=True,
            consider_video_guides=True,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.REFERENCE
    assert decision.selected_video_guide_id == tail_id
    assert decision.media_inputs[0].video_guide_id == tail_id
    assert decision.override_allowed is True
    assert fake.calls
    assert "never mandatory" in fake.calls[0][0]
    assert (
        "当前制片技能：AI 原生短剧。"
        in fake.calls[0][0]
    )
    assert "生成路线规划职责准则：" in fake.calls[0][0]
    assert "类型创作原则：" not in fake.calls[0][0]

    general_document = service.repository.get_project(project.document.project.id)
    assert general_document is not None
    general_document.project.production_profile = ProductionProfile.GENERAL_VIDEO
    service.repository.save_project(general_document)
    service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[target_shot.id],
            use_language_agent=True,
            consider_video_guides=True,
        ),
    )
    general_instructions = fake.calls[-1][0]
    assert "当前制片技能：通用视频。" in general_instructions
    assert "生成路线规划职责准则：" in general_instructions
    assert "AI 原生短剧" not in general_instructions

    updated = service.repository.get_project(project.document.project.id)
    assert updated is not None
    updated_source = next(item for item in updated.shots if item.id == source_shot.id)
    updated_source.revision += 1
    service.repository.save_project(updated)
    with pytest.raises(WorkflowConflictError, match="video-guide source shots"):
        service.execute_h3_production_route_plan(
            project.document.project.id,
            compiled.plan.id,
            H3ProductionRouteExecuteRequest(
                enqueue_new_generations=False,
                inspect_reused_takes=False,
                rebuild_manifest=False,
            ),
        )

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


def test_route_plan_adaptive_execution_queues_only_the_first_candidate(
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
            candidate_count=3,
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            enqueue_new_generations=True,
            adaptive_sequential=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert len(executed.queued_tasks) == 1
    assert executed.queued_tasks[0].candidate_index == 1
    assert executed.plan.decisions[0].candidate_count == 3
    assert executed.plan.decisions[0].task_ids == [executed.queued_tasks[0].id]


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
