"""Offline coverage for evidence-first H3 production route planning."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver

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
    H3ShotRouteDirective,
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
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReferenceAngle,
    ReferenceFraming,
    ReferencePack,
    ReferencePackEntry,
    ReferenceRole,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.media import probe_media_with_ffmpeg
from short_drama.providers.h3_media_bindings import resolve_h3_media_bindings
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


class _FixedRouteAgent:
    def __init__(self) -> None:
        self.output = ""
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


def _service_with_route_agent(
    tmp_path: Path,
) -> tuple[ShortDramaService, _FixedRouteAgent]:
    agent = _FixedRouteAgent()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: agent,
    )
    service.language_config.update(
        LanguageProviderConfigUpdate(
            api_key="test-key",
            base_url="https://example.invalid/v1",
        )
    )
    return service, agent


def _direct_route_directive(shot_id: str) -> H3ShotRouteDirective:
    return H3ShotRouteDirective(
        shot_id=shot_id,
        candidate_id=f"direct:{shot_id}",
    )


def _set_h3_task_outcome(
    service: ShortDramaService,
    task_id: str,
    *,
    task_status: TaskStatus,
    evidence_status: EvidenceStatus,
    hard_gate_passed: bool | None = None,
    error_code: str | None = None,
) -> None:
    task = service.repository.get_task(task_id)
    assert task is not None
    task.status = task_status
    task.error_code = error_code
    task.updated_at = NOW
    if task_status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.OUTPUT_MISSING,
    }:
        task.finished_at = NOW
    service.repository.save_task(task)

    evidence = service.repository.get_route_evidence_for_task(task_id)
    assert evidence is not None
    evidence.status = evidence_status
    evidence.hard_gate_passed = hard_gate_passed
    evidence.reviewed_at = NOW if evidence_status == EvidenceStatus.REVIEWED else None
    service.repository.save_route_evidence(evidence)


def _review_h3_task_as_quality_failure(
    service: ShortDramaService,
    task_id: str,
    *,
    category: QaFailureCategory = QaFailureCategory.COMPOSITION_QUALITY,
    repair_layer: str = "PROVIDER_RETRY",
    technical_passed: bool = True,
    freshness: Freshness = Freshness.CURRENT,
) -> None:
    task = service.repository.get_task(task_id)
    assert task is not None
    issue = QaIssue(
        code="HUMAN_GENERATION_QUALITY",
        severity="ERROR",
        message="The generated candidate missed the requested visual quality.",
        repair_layer=repair_layer,
        category=category,
    )
    take = Take(
        id=f"take-{task.id}",
        project_id=task.project_id,
        shot_id=task.shot_id,
        task_id=task.id,
        candidate_index=task.candidate_index,
        operation=task.operation,
        provider=task.provider,
        model=task.model,
        route=task.route,
        media_path=f"/tmp/{task.id}.mp4",
        thumbnail_path=f"/tmp/{task.id}.jpg",
        duration_seconds=task.requested_duration_seconds,
        width=768,
        height=1344,
        availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.REJECTED,
        is_selected=False,
        freshness=freshness,
        qa_report=QAReport(
            id=f"qa-{task.id}",
            take_id=f"take-{task.id}",
            status=QaStatus.REJECTED,
            hard_gate_passed=False,
            score=2.0,
            issues=[issue],
            checks=["technical-media", "human-quality-review"],
            reviewer_notes="A fresh random sample may resolve this generation defect.",
            reviewed_at=NOW,
            created_at=NOW,
        ),
        created_at=NOW,
    )
    service.repository.save_take(take)
    _set_h3_task_outcome(
        service,
        task_id,
        task_status=TaskStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.REVIEWED,
        hard_gate_passed=False,
    )
    evidence = service.repository.get_route_evidence_for_task(task_id)
    assert evidence is not None
    evidence.take_id = take.id
    evidence.technical_passed = technical_passed
    evidence.score = 2.0
    service.repository.save_route_evidence(evidence)


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


def test_route_agent_batches_follow_story_scenes_without_fixed_size_limit(
    tmp_path: Path,
) -> None:
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

    assert [len(batch) for batch in batches] == [25, 20]
    assert [{shot.scene_id for shot in batch} for batch in batches] == [
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
    source_take = _persist_passed_h3_take(
        service,
        shot_id=source_shot.id,
        media_path=video,
        selected=True,
    )
    source_take.qa_status = QaStatus.PENDING
    source_take.qa_report.status = QaStatus.PENDING
    service.repository.save_take(source_take)
    _persist_passed_h3_take(
        service,
        shot_id=target_shot.id,
        media_path=video,
        selected=True,
    )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[target_shot.id],
            use_language_agent=False,
            consider_video_guides=True,
            shot_directives=[
                H3ShotRouteDirective(
                    shot_id=target_shot.id,
                    camera_previs_motion=H3CameraMotionPreset.DOLLY_IN,
                )
            ],
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
    assert {item.source_qa_status for item in decision.video_guides} == {
        QaStatus.PENDING
    }
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


def test_free_form_camera_does_not_create_previs_without_typed_intent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    source_shot, target_shot = project.document.shots[:2]
    target_shot.camera = "推近 push in dolly in 跟拍"
    service.repository.save_project(project.document)
    video = tmp_path / "preceding-with-camera-words.mp4"
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

    assert {item.kind for item in compiled.plan.decisions[0].video_guides} == {
        H3VideoGuideKind.PREVIOUS_TAKE,
        H3VideoGuideKind.TAIL_WINDOW,
    }


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
        camera_motion_by_shot={
            target_shot.id: H3CameraMotionPreset.TRACK_RIGHT,
        },
    )

    assert warnings == []
    previs = next(
        item
        for item in guides_by_shot[target_shot.id]
        if item.kind == H3VideoGuideKind.CAMERA_PREVIS
    )
    assert previs.previs_base_source == H3VideoGuideBaseSource.TARGET_START_FRAME
    assert previs.camera_motion == H3CameraMotionPreset.TRACK_RIGHT
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
    assert "所有 guide 都不是必选项" in fake.calls[0][0]
    assert "当前制片技能：AI 短剧（本地格式适配）。" in fake.calls[0][0]
    assert "生成路线规划的通用职责：" in fake.calls[0][0]
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
    assert "生成路线规划的通用职责：" in general_instructions
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
                expected_state_revision=compiled.plan.state_revision,
                enqueue_new_generations=False,
                inspect_reused_takes=False,
                rebuild_manifest=False,
            ),
        )

def test_route_plan_explicitly_reuses_reviewed_h3_takes_and_builds_real_manifest(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    video = tmp_path / "shared-h3.mp4"
    _render_video(video)
    directives: list[H3ShotRouteDirective] = []
    for shot in project.document.shots:
        take = _persist_passed_h3_take(
            service,
            shot_id=shot.id,
            media_path=video,
        )
        directives.append(
            H3ShotRouteDirective(shot_id=shot.id, reuse_take_id=take.id)
        )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            use_language_agent=False,
            shot_directives=directives,
        ),
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
            expected_state_revision=compiled.plan.state_revision,
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


def test_reusable_take_does_not_override_unjudged_direct_technical_baseline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    video = tmp_path / "reusable-but-not-selected.mp4"
    _render_video(video)
    _persist_passed_h3_take(service, shot_id=shot.id, media_path=video)

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    )

    assert compiled.plan.decisions[0].action == H3ShotRouteAction.GENERATE
    assert compiled.plan.decisions[0].readiness == (
        H3ShotRouteReadiness.NEEDS_AGENT_DECISION
    )
    assert compiled.plan.status == H3RoutePlanStatus.PARTIAL
    assert compiled.plan.estimated_new_h3_seconds == 0
    assert compiled.plan.planner.endswith("+awaiting-agent-decision")
    assert any(
        "cannot be executed" in warning
        for warning in compiled.plan.decisions[0].warnings
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert executed.queued_tasks == []
    assert executed.plan.status == H3RoutePlanStatus.PARTIAL
    assert executed.plan.decisions[0].execution_status == (
        H3ShotRouteExecutionStatus.SKIPPED
    )


def test_explicit_candidate_directive_selects_an_executable_route(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )

    assert compiled.plan.status == H3RoutePlanStatus.READY
    assert compiled.plan.decisions[0].readiness == H3ShotRouteReadiness.READY
    assert compiled.plan.estimated_new_h3_seconds > 0
    assert "+awaiting-agent-decision" not in compiled.plan.planner

    listed = service.tool_registry.invoke("route.list_h3_plans", {"project_id": project.document.project.id})
    assert listed.status.value == "SUCCEEDED"
    assert any(ref.artifact_id == compiled.plan.id and ref.version == compiled.plan.state_revision
               for ref in listed.artifact_refs)
    index_ref = next(ref for ref in listed.artifact_refs if ref.artifact_type == "h3_route_plan_index")
    index = ProjectArtifactResolver(service.repository).summarize_artifact(index_ref)
    assert index is not None
    row = json.loads(index.summary)["plans"][0]
    assert row["plan_id"] == compiled.plan.id and row["shot_ids"] == [shot.id]
    assert row["state_revision"] == compiled.plan.state_revision
    assert compiled.plan.decisions[0].prompt not in index.summary


def test_unknown_explicit_candidate_is_rejected_without_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]

    with pytest.raises(WorkflowConflictError, match="candidate .* is unavailable"):
        service.compile_h3_production_route_plan(
            project.document.project.id,
            H3ProductionRoutePlanRequest(
                shot_ids=[shot.id],
                use_language_agent=False,
                shot_directives=[
                    H3ShotRouteDirective(
                        shot_id=shot.id,
                        candidate_id="candidate-that-does-not-exist",
                    )
                ],
            ),
        )


def test_route_agent_receives_rejected_take_as_structured_decision_evidence(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    video = tmp_path / "agent-visible-rejected-take.mp4"
    _render_video(video)
    take = _persist_passed_h3_take(service, shot_id=shot.id, media_path=video)
    issue = QaIssue(
        code="PERFORMANCE_MISMATCH",
        severity="ERROR",
        message="The performance does not match the intended beat.",
        repair_layer="DIRECTOR_AGENT",
        category=QaFailureCategory.COMPOSITION_QUALITY,
    )
    take.qa_status = QaStatus.REJECTED
    take.qa_report.status = QaStatus.REJECTED
    take.qa_report.hard_gate_passed = False
    take.qa_report.score = 2.0
    take.qa_report.issues = [issue]
    service.repository.save_take(take)
    evidence = service.repository.get_route_evidence_for_task(take.task_id)
    assert evidence is not None
    evidence.hard_gate_passed = False
    evidence.score = 2.0
    service.repository.save_route_evidence(evidence)
    agent.output = (
        '{"choices":[{'
        f'"shot_id":"{shot.id}","candidate_id":"reuse:{take.id}",'
        '"rationale":"我已阅读该 Take 的 QA 证据，并判断当前镜头仍应复用。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )

    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    )

    assert compiled.plan.decisions[0].action == H3ShotRouteAction.REUSE_TAKE
    assert compiled.plan.decisions[0].reuse_take_id == take.id
    assert agent.calls
    agent_input = agent.calls[-1][1]
    agent_payload = json.loads(agent_input)
    reuse_candidate = next(
        candidate
        for candidate in agent_payload["shots"][0]["candidates"]
        if candidate["id"] == f"reuse:{take.id}"
    )
    assert reuse_candidate["decision_evidence"]["take_qa_status"] == "REJECTED"
    assert (
        reuse_candidate["decision_evidence"]["take_quality"]["hard_gate_passed"]
        is False
    )
    assert "quality_score_hint" not in agent_input


def test_route_agent_can_choose_generation_when_a_reusable_take_exists(
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
    shot = project.document.shots[0]
    video = tmp_path / "agent-reviewed-reusable.mp4"
    _render_video(video)
    _persist_passed_h3_take(base_service, shot_id=shot.id, media_path=video)
    fake = FakeRouteAgent(
        "{\"choices\":[{"
        f"\"shot_id\":\"{shot.id}\","
        f"\"candidate_id\":\"direct:{shot.id}\","
        "\"rationale\":\"The current shot needs a different performance.\","
        "\"qa_focus_categories\":[],\"warnings\":[]"
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
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    )

    assert fake.calls
    assert compiled.plan.decisions[0].action == H3ShotRouteAction.GENERATE
    assert compiled.plan.decisions[0].rationale == (
        "The current shot needs a different performance."
    )


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


def test_free_text_risk_does_not_auto_choose_a_route(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    shot = next(item for item in document.shots if item.sequence == 3)
    shot.duration_seconds = 15.0
    shot.local_rework_risk = "高；长镜头动作与构图需要导演权衡"
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
    assert decision.route == VideoGenerationRoute.DIRECT
    assert decision.inspection.max_frames == 23
    assert decision.inspection.scopes == [
        "INTRA_SHOT",
        "SHOT_BOUNDARY",
    ]
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_AGENT_DECISION
    assert compiled.plan.status == H3RoutePlanStatus.PARTIAL
    assert decision.asset_preparations == []
    assert decision.override_allowed is True


@pytest.mark.parametrize(
    ("candidate_prefix", "expected_route", "expected_missing_roles"),
    [
        (
            "frame-last-pending",
            VideoGenerationRoute.FRAME_LAST,
            [H3MediaRole.LAST_FRAME.value],
        ),
        (
            "frame-first-last-pending",
            VideoGenerationRoute.FRAME_FIRST_LAST,
            [
                H3MediaRole.FIRST_FRAME.value,
                H3MediaRole.LAST_FRAME.value,
            ],
        ),
    ],
)
def test_route_agent_can_choose_last_frame_control_without_fixed_routing(
    tmp_path: Path,
    candidate_prefix: str,
    expected_route: VideoGenerationRoute,
    expected_missing_roles: list[str],
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    shot = next(item for item in document.shots if item.sequence == 2)
    candidate_id = f"{candidate_prefix}:{shot.id}"
    agent.output = (
        '{"choices":[{'
        f'"shot_id":"{shot.id}",'
        f'"candidate_id":"{candidate_id}",'
        '"rationale":"终态画面控制比固定路由更符合当前镜头意图。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
            consider_video_guides=False,
        ),
    )

    decision = compiled.plan.decisions[0]
    assert decision.route == expected_route
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
    assert decision.missing_inputs == expected_missing_roles
    assert [item.role.value for item in decision.asset_preparations] == (
        [H3MediaRole.LAST_FRAME.value]
        if expected_route == VideoGenerationRoute.FRAME_LAST
        else [
            H3MediaRole.FIRST_FRAME.value,
            H3MediaRole.LAST_FRAME.value,
        ]
    )
    route_agent_payload = json.loads(agent.calls[0][1])
    candidate_ids = {
        item["id"]
        for item in route_agent_payload["shots"][0]["candidates"]
    }
    assert f"frame-first-pending:{shot.id}" in candidate_ids
    assert f"frame-last-pending:{shot.id}" in candidate_ids
    assert f"frame-first-last-pending:{shot.id}" in candidate_ids
    direct_candidate = next(
        item
        for item in route_agent_payload["shots"][0]["candidates"]
        if item["id"] == f"direct:{shot.id}"
    )
    assert direct_candidate["readiness"] == H3ShotRouteReadiness.READY.value
    assert direct_candidate["warnings"] == []


def test_lightweight_bindings_become_executable_reference_candidate(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
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
    subject_asset_id = document.assets[0].id
    document.reference_packs.append(
        ReferencePack(
            id="route-agent-reference-options",
            project_id=document.project.id,
            subject_asset_id=subject_asset_id,
            name="Route Agent reference options",
            entries=[
                ReferencePackEntry(
                    id="high-score-reference",
                    asset_version_id=document.assets[0].versions[0].id,
                    role=ReferenceRole.CHARACTER_FACE,
                    angle=ReferenceAngle.FRONT,
                    framing=ReferenceFraming.CLOSE_UP,
                    tags=["identity"],
                    description="Higher retrieval score but less useful camera side.",
                    priority=50,
                ),
                ReferencePackEntry(
                    id="lower-score-reference",
                    asset_version_id=document.assets[1].versions[0].id,
                    role=ReferenceRole.CHARACTER_PROFILE,
                    angle=ReferenceAngle.RIGHT_PROFILE,
                    framing=ReferenceFraming.MEDIUM,
                    tags=["camera-matched"],
                    description="Lower retrieval score with the camera side needed by this shot.",
                    priority=-20,
                ),
            ],
            created_at=NOW,
            updated_at=NOW,
        )
    )
    shot.local_rework_risk = "低；稳定对话近景"
    service.repository.save_project(document)
    agent.output = (
        '{"choices":[{'
        f'"shot_id":"{shot.id}","candidate_id":"reference:{shot.id}",'
        '"rationale":"批准的参考资产能够服务当前镜头。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )

    unresolved = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    )
    assert unresolved.plan.status == H3RoutePlanStatus.PARTIAL
    assert (
        unresolved.plan.decisions[0].readiness
        == H3ShotRouteReadiness.NEEDS_AGENT_DECISION
    )
    route_payload = json.loads(agent.calls[-1][1])
    reference_route = next(
        item
        for item in route_payload["shots"][0]["candidates"]
        if item["id"] == f"reference:{shot.id}"
    )
    selection_candidates = reference_route["reference_selection_candidates"]
    assert len(selection_candidates) > 1
    assert all(
        item["selection_candidate_id"] == item["candidate_id"]
        for item in selection_candidates
    )
    assert all(
        item["decision_evidence"]["deterministic_reasons"]
        for item in selection_candidates
    )
    selected = min(
        selection_candidates,
        key=lambda item: item["decision_evidence"]["deterministic_score"],
    )
    agent.output = json.dumps(
        {
            "choices": [
                {
                    "shot_id": shot.id,
                    "candidate_id": f"reference:{shot.id}",
                    "reference_selection_candidate_ids": [selected["candidate_id"]],
                    "rationale": "该候选的镜头职责更符合当前构图，分数仅作为证据。",
                    "qa_focus_categories": [],
                    "warnings": [],
                }
            ]
        },
        ensure_ascii=False,
    )
    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.REFERENCE
    assert decision.reference_selection_candidate_ids == [selected["candidate_id"]]
    assert len(decision.media_inputs) == 1
    assert decision.media_inputs[0].asset_version_id == selected["asset_version_id"]
    assert all(item.asset_version_id is not None for item in decision.media_inputs)
    assert all(item.subject_asset_id is not None for item in decision.media_inputs)
    assert all(item.reference_alias is not None for item in decision.media_inputs)
    assert all(item.binding_purpose is not None for item in decision.media_inputs)
    bindings = resolve_h3_media_bindings(document, decision.media_inputs, [])
    assert all(
        f"@{item.alias} = {item.prompt_label}" in decision.prompt
        for item in bindings
    )
    assert all(
        f"{item.provider_label} provides" in decision.prompt
        for item in bindings
    )


def test_applied_change_keyframe_repair_is_executed_by_next_route_plan(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
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
    agent.output = (
        '{"choices":[{'
        f'"shot_id":"{shot.id}",'
        f'"candidate_id":"frame-first-pending:{shot.id}",'
        '"rationale":"已应用的换关键帧策略需要新起始帧。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    )
    decision = compiled.plan.decisions[0]

    assert decision.route == VideoGenerationRoute.FRAME_FIRST
    assert decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
    assert decision.asset_preparations[0].role.value == "first_frame"


def test_applied_change_camera_repair_prepares_matching_frame_control(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
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
    agent.output = (
        '{"choices":[{'
        f'"shot_id":"{shot.id}",'
        f'"candidate_id":"frame-first-pending:{shot.id}",'
        '"rationale":"已应用的换机位策略需要重建起始构图。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )

    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
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
    directives: list[H3ShotRouteDirective] = []
    for shot in shots:
        take = _persist_passed_h3_take(service, shot_id=shot.id, media_path=video)
        directives.append(
            H3ShotRouteDirective(shot_id=shot.id, reuse_take_id=take.id)
        )
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[item.id for item in shots],
            use_language_agent=False,
            shot_directives=directives,
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
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
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
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


def test_route_plan_reexecution_is_idempotent_after_all_candidates_are_established(
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
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )

    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    first_task_ids = [item.id for item in first.queued_tasks]

    second = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    persisted = service.repository.list_tasks_for_shot(
        project.document.project.id,
        shot.id,
    )
    assert second.queued_tasks == []
    assert second.plan.decisions[0].task_ids == first_task_ids
    assert [item.id for item in persisted] == first_task_ids


def test_route_plan_reconciles_and_completes_a_partially_persisted_candidate_batch(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-key"))
    compiled = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            candidate_count=3,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    document = service.repository.get_project(project_id)
    plan = service.repository.get_h3_route_plan(compiled.plan.id)
    assert document is not None
    assert plan is not None
    decision = plan.decisions[0]
    media_inputs = service._resolve_approved_asset_inputs(
        document,
        decision.media_inputs,
    )
    task, run, evidence = service._new_h3_task_and_run(
        document=document,
        shot_id=shot.id,
        route=decision.route,
        duration_seconds=decision.duration_seconds,
        prompt=decision.prompt,
        media_inputs=media_inputs,
        purpose=EvidencePurpose.PRODUCTION,
        learning_objective=None,
        regression_case_id=None,
        candidate_index=1,
        attempt=1,
        retry_of_task_id=None,
    )
    decision.task_ids.append(task.id)
    decision.execution_status = H3ShotRouteExecutionStatus.QUEUED
    plan.status = H3RoutePlanStatus.EXECUTING
    service._touch_h3_route_plan(plan)
    service.repository.save_h3_route_task_bundle(
        task=task,
        run=run,
        evidence=evidence,
        plan=plan,
    )

    resumed = service.execute_h3_production_route_plan(
        project_id,
        plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert [item.candidate_index for item in resumed.queued_tasks] == [2, 3]
    all_tasks = service.repository.list_tasks_for_shot(project_id, shot.id)
    assert [item.candidate_index for item in all_tasks] == [1, 2, 3]
    assert resumed.plan.decisions[0].task_ids == [item.id for item in all_tasks]
    assert len({item.request_fingerprint for item in all_tasks}) == 3

    repeated = service.execute_h3_production_route_plan(
        project_id,
        plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=resumed.plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )
    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [item.id for item in all_tasks]


def test_route_plan_candidate_bundle_rolls_back_when_plan_update_fails(
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
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )

    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_h3_route_plan_update
            BEFORE UPDATE ON h3_route_plans
            BEGIN
                SELECT RAISE(ABORT, 'route plan update interrupted');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="route plan update interrupted"):
        service.execute_h3_production_route_plan(
            project.document.project.id,
            compiled.plan.id,
            request,
        )

    persisted_after_failure = service.repository.get_h3_route_plan(compiled.plan.id)
    assert persisted_after_failure is not None
    assert persisted_after_failure.decisions[0].task_ids == []
    assert service.repository.list_tasks(project.document.project.id) == []
    assert service.repository.list_provider_runs(project.document.project.id) == []
    assert service.repository.list_route_evidence(project.document.project.id) == []

    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("DROP TRIGGER fail_h3_route_plan_update")

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )

    task_ids = [task.id for task in executed.queued_tasks]
    assert len(task_ids) == 2
    assert executed.plan.decisions[0].task_ids == task_ids
    for task_id in task_ids:
        assert service.repository.get_provider_run(task_id) is not None
        evidence = service.repository.get_route_evidence_for_task(task_id)
        assert evidence is not None
        assert evidence.purpose == EvidencePurpose.PRODUCTION


def test_route_plan_executes_the_agent_requested_candidate_batch_without_local_cap(
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
            candidate_count=11,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )
    task_ids = [item.id for item in executed.queued_tasks]
    assert [item.candidate_index for item in executed.queued_tasks] == list(range(1, 12))
    assert executed.plan.decisions[0].candidate_count == 11
    assert executed.plan.decisions[0].task_ids == task_ids

    persisted = service.repository.get_h3_route_plan(compiled.plan.id)
    assert persisted is not None
    assert persisted.decisions[0].task_ids == task_ids


@pytest.mark.parametrize(
    ("task_status", "evidence_status"),
    [
        (TaskStatus.QUEUED, EvidenceStatus.AWAITING_OUTPUT),
        (TaskStatus.SUBMITTING, EvidenceStatus.AWAITING_OUTPUT),
        (TaskStatus.ACKNOWLEDGED, EvidenceStatus.AWAITING_OUTPUT),
        (TaskStatus.RUNNING, EvidenceStatus.AWAITING_OUTPUT),
        (TaskStatus.SUBMIT_UNKNOWN, EvidenceStatus.SUBMIT_UNKNOWN),
        (TaskStatus.OUTPUT_MISSING, EvidenceStatus.OUTPUT_MISSING),
        (TaskStatus.SUCCEEDED, EvidenceStatus.REVIEWED),
        (TaskStatus.FAILED, EvidenceStatus.FAILED),
        (TaskStatus.CANCELLED, EvidenceStatus.CANCELLED),
    ],
)
def test_route_plan_reexecution_never_derives_candidates_from_task_status(
    tmp_path: Path,
    task_status: TaskStatus,
    evidence_status: EvidenceStatus,
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
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    first_task_id = first.queued_tasks[0].id
    _set_h3_task_outcome(
        service,
        first_task_id,
        task_status=task_status,
        evidence_status=evidence_status,
    )

    repeated = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [first_task_id]


def test_route_plan_reexecution_ignores_quality_evidence_without_new_agent_plan(
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
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    _review_h3_task_as_quality_failure(service, first.queued_tasks[0].id)

    repeated = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [first.queued_tasks[0].id]


@pytest.mark.parametrize(
    ("category", "repair_layer", "technical_passed"),
    [
        (QaFailureCategory.TECHNICAL_MEDIA, "PROVIDER_RETRY", False),
        (QaFailureCategory.OTHER, "PROVIDER_RETRY", True),
        (QaFailureCategory.COMPOSITION_QUALITY, "DIRECTOR_AGENT", True),
    ],
)
def test_route_plan_reexecution_does_not_use_quality_classification(
    tmp_path: Path,
    category: QaFailureCategory,
    repair_layer: str,
    technical_passed: bool,
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
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    _review_h3_task_as_quality_failure(
        service,
        first.queued_tasks[0].id,
        category=category,
        repair_layer=repair_layer,
        technical_passed=technical_passed,
    )

    repeated = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [first.queued_tasks[0].id]


def test_route_plan_reexecution_does_not_use_review_pass_status(
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
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    _set_h3_task_outcome(
        service,
        first.queued_tasks[0].id,
        task_status=TaskStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.REVIEWED,
        hard_gate_passed=True,
    )

    repeated = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [first.queued_tasks[0].id]


def test_agent_can_request_another_candidate_batch_with_a_new_route_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-key"))
    first_plan = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=first_plan.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        first_plan.plan.id,
        request,
    )
    _review_h3_task_as_quality_failure(service, first.queued_tasks[0].id)

    next_plan = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            candidate_count=2,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    next_batch = service.execute_h3_production_route_plan(
        project.document.project.id,
        next_plan.plan.id,
        request.model_copy(
            update={"expected_state_revision": next_plan.plan.state_revision}
        ),
    )

    assert [item.candidate_index for item in next_batch.queued_tasks] == [2, 3]
    assert first.plan.decisions[0].task_ids == [first.queued_tasks[0].id]
    assert next_batch.plan.decisions[0].task_ids == [
        item.id for item in next_batch.queued_tasks
    ]


def test_route_plan_reexecution_does_not_use_retry_lineage(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-key"))
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            candidate_count=1,
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    first_task_id = first.queued_tasks[0].id
    _set_h3_task_outcome(
        service,
        first_task_id,
        task_status=TaskStatus.FAILED,
        evidence_status=EvidenceStatus.FAILED,
    )
    retry = service.retry_task(first_task_id)
    assert retry.status == TaskStatus.QUEUED

    repeated = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )

    assert repeated.queued_tasks == []
    assert repeated.plan.decisions[0].task_ids == [first_task_id]


def test_route_plan_executes_one_explicit_batch_per_shot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    shots = project.document.shots[:2]
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-key"))
    compiled = service.compile_h3_production_route_plan(
        project.document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id for shot in shots],
            use_language_agent=False,
            candidate_count=2,
            shot_directives=[
                _direct_route_directive(shot.id) for shot in shots
            ],
        ),
    )
    second_decision = next(
        item for item in compiled.plan.decisions if item.shot_id == shots[1].id
    )
    second_decision.route = VideoGenerationRoute.DIRECT
    second_decision.media_inputs = []
    second_decision.missing_inputs = []
    second_decision.readiness = H3ShotRouteReadiness.READY
    service.repository.save_h3_route_plan(compiled.plan)
    request = H3ProductionRouteExecuteRequest(
        expected_state_revision=compiled.plan.state_revision,
        enqueue_new_generations=True,
        inspect_reused_takes=False,
        rebuild_manifest=False,
    )
    first = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request,
    )
    assert len(first.queued_tasks) == 4
    first_by_shot = {task.shot_id: task for task in first.queued_tasks}
    _review_h3_task_as_quality_failure(
        service,
        first_by_shot[shots[0].id].id,
    )

    second = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        request.model_copy(
            update={"expected_state_revision": first.plan.state_revision}
        ),
    )
    assert second.queued_tasks == []

    resumed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=second.plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert resumed.queued_tasks == []
    task_counts = {
        decision.shot_id: len(decision.task_ids)
        for decision in resumed.plan.decisions
        if decision.shot_id in {shot.id for shot in shots}
    }
    assert task_counts == {shots[0].id: 2, shots[1].id: 2}


def test_route_plan_execution_queues_the_full_explicit_candidate_batch(
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
            shot_directives=[_direct_route_directive(shot.id)],
        ),
    )

    executed = service.execute_h3_production_route_plan(
        project.document.project.id,
        compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
            enqueue_new_generations=True,
            inspect_reused_takes=False,
            rebuild_manifest=False,
        ),
    )

    assert [item.candidate_index for item in executed.queued_tasks] == [1, 2, 3]
    assert executed.plan.decisions[0].candidate_count == 3
    assert executed.plan.decisions[0].task_ids == [
        item.id for item in executed.queued_tasks
    ]


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
            H3ProductionRouteExecuteRequest(
                expected_state_revision=compiled.plan.state_revision,
                rebuild_manifest=False,
            ),
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
            "shot_directives": [
                {
                    "shot_id": shot_id,
                    "candidate_id": f"direct:{shot_id}",
                }
            ],
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
            "expected_state_revision": plan["state_revision"],
            "enqueue_new_generations": False,
            "inspect_reused_takes": False,
            "rebuild_manifest": False,
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["plan"]["status"] == "PARTIAL"
    assert executed.json()["queued_tasks"] == []
