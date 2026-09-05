"""Master Audio, music structure, timed text, and timeline integration."""

from __future__ import annotations

import math
import subprocess
import wave
from array import array
from pathlib import Path

import pytest
import imageio_ffmpeg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes._errors import HTTPError
from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReviewRequest,
)
from short_drama.media import TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AudioInspectionRequest,
    AudioStructureMap,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationTargetOrigin,
    EnqueueRequest,
    Freshness,
    MasterAudioBinding,
    MasterAudioBindingResponse,
    MasterAudioBindRequest,
    MusicAnalysisRequest,
    MusicAnalysisResponse,
    OutputSpec,
    ProductionDocumentOrigin,
    StoryIntent,
    TimedTextLock,
    TimedTextLockResponse,
    TimedTextLockUpdateRequest,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
)
from short_drama.production_contracts import (
    ClipGroup,
    GenerationPlanCompileRequest,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionSpecReplaceRequest,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardReplaceRequest,
)
from short_drama.music_analysis import LocalMusicAnalysisService
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from tests.fakes.music_analysis import FakeMusicAnalysisService


def _write_wav(
    path: Path,
    *,
    duration_seconds: float = 6.0,
    frequency_hz: float = 330.0,
) -> None:
    sample_rate = 16_000
    samples = array(
        "h",
        (
            round(8_000 * math.sin(2 * math.pi * frequency_hz * index / sample_rate))
            for index in range(round(sample_rate * duration_seconds))
        ),
    )
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(samples.tobytes())


def _write_video(path: Path, *, duration_seconds: float = 1.0) -> None:
    duration = f"{duration_seconds:.3f}"
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
            f"color=c=0x273447:s=108x192:r=12:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=24000:duration={duration}",
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


def _service(
    tmp_path: Path,
    *,
    music_analysis_service: FakeMusicAnalysisService | None = None,
) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        music_analysis_service=music_analysis_service,
    )


def _approved_audio_version(
    service: ShortDramaService,
    project_id: str,
    source_path: Path,
) -> tuple[str, str]:
    created = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Master Audio",
            descriptor="用户提供的完整主音频",
        ),
    )
    imported = service.import_local_asset_version(
        project_id,
        created.asset.id,
        AssetImportLocalRequest(
            local_path=str(source_path),
            descriptor="主音频候选",
        ),
    )
    approved = service.review_asset_version(
        project_id,
        created.asset.id,
        imported.version.id,
        AssetReviewRequest(decision="APPROVED", notes="音频内容与项目一致"),
    )
    return approved.asset.id, approved.version.id


def test_master_audio_tools_version_documents_and_survive_restart(
    tmp_path: Path,
) -> None:
    analyzer = FakeMusicAnalysisService(bpm=126.0)
    service = _service(tmp_path, music_analysis_service=analyzer)
    project_id = service.initialize_fixture().document.project.id
    audio_path = tmp_path / "song.wav"
    _write_wav(audio_path)
    asset_id, asset_version_id = _approved_audio_version(
        service,
        project_id,
        audio_path,
    )

    bound_result = service.tool_registry.invoke(
        "audio.bind_master",
        {
            "project_id": project_id,
            "request": {
                "asset_version_id": asset_version_id,
                "source_in_seconds": 0.0,
                "source_out_seconds": 4.0,
                "time_origin_seconds": 1.0,
                "origin": "USER",
            },
        },
    )
    assert bound_result.status == ToolCallStatus.SUCCEEDED
    assert bound_result.output is not None
    first = MasterAudioBindingResponse.model_validate(bound_result.output)
    assert first.binding.asset_id == asset_id
    assert first.version.version == 1
    assert bound_result.artifact_refs[-1].artifact_type == (
        "production_document_version"
    )
    assert bound_result.artifact_refs[-1].artifact_id == first.version.id
    asset_ref = bound_result.artifact_refs[0]
    source_asset_ref = first.version.source_artifact_refs[0]
    assert asset_ref.artifact_type == "asset_version"
    assert asset_ref.artifact_id == asset_version_id
    assert asset_ref.version == source_asset_ref.version
    assert asset_ref.version is not None

    second = service.bind_master_audio(
        project_id,
        MasterAudioBindRequest(
            asset_version_id=asset_version_id,
            source_in_seconds=0.25,
            source_out_seconds=4.25,
            time_origin_seconds=1.0,
            base_version_id=first.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert second.version.version == 2
    assert second.version.parent_version_id == first.version.id
    with pytest.raises(WorkflowConflictError, match="stale version"):
        service.bind_master_audio(
            project_id,
            MasterAudioBindRequest(
                asset_version_id=asset_version_id,
                source_out_seconds=4.0,
                base_version_id=first.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    analysis_result = service.tool_registry.invoke(
        "audio.analyze_music",
        {
            "project_id": project_id,
            "request": {"master_audio_version_id": second.version.id},
        },
    )
    assert analysis_result.status == ToolCallStatus.SUCCEEDED
    assert analysis_result.output is not None
    analysis = MusicAnalysisResponse.model_validate(analysis_result.output)
    assert analysis.structure.bpm == 126.0
    assert analysis.version.payload == analysis.structure.model_dump(mode="json")
    assert analysis_result.artifact_refs[-1].artifact_id == analysis.version.id

    timed_result = service.tool_registry.invoke(
        "audio.update_timed_text_lock",
        {
            "project_id": project_id,
            "request": {
                "master_audio_version_id": second.version.id,
                "entries": [
                    {
                        "id": "lyric-1",
                        "text": "  保留精确空格  ",
                        "start_seconds": 1.2,
                        "end_seconds": 2.0,
                        "language": "zh-CN",
                    }
                ],
                "origin": "USER",
            },
        },
    )
    assert timed_result.status == ToolCallStatus.SUCCEEDED
    assert timed_result.output is not None
    timed = TimedTextLockResponse.model_validate(timed_result.output)
    assert timed.lock.entries[0].text == "  保留精确空格  "
    assert len(analyzer.calls) == 1

    restarted = _service(tmp_path)
    document = restarted.get_project(project_id).document
    assert document.master_audio_document_id == second.document.id
    assert document.audio_structure_document_id == analysis.document.id
    assert document.timed_text_document_id == timed.document.id
    persisted = restarted.get_production_document(
        project_id,
        second.document.id,
    )
    assert MasterAudioBinding.model_validate(persisted.version.payload) == second.binding


def test_audio_led_production_spec_pins_master_audio_and_rebind_invalidates_it(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    audio_path = tmp_path / "audio-led.wav"
    _write_wav(audio_path)
    _asset_id, asset_version_id = _approved_audio_version(
        service,
        project_id,
        audio_path,
    )
    first_binding = service.bind_master_audio(
        project_id,
        MasterAudioBindRequest(
            asset_version_id=asset_version_id,
            source_in_seconds=0.5,
            source_out_seconds=4.5,
            time_origin_seconds=1.0,
            origin=ProductionDocumentOrigin.USER,
        ),
    )
    target_seconds = 5.0
    duration_contract = DurationContract(
        target_seconds=target_seconds,
        target_origin=DurationTargetOrigin.MASTER_AUDIO,
        authority=DurationAuthority.AUDIO_LED,
        acceptable_range_seconds=None,
        source_fidelity_policy=DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS,
        mismatch_resolution=DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
        decision_basis="已批准的主音频窗口和时间原点共同定义完整时间线。",
    )
    document = service.get_project(project_id).document
    current_script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    current_script.story_intent = StoryIntent(
        audience_experience="画面动作紧贴已经批准的主音频推进。",
        dramatic_engine="声音结构驱动画面变化。",
        progression_strategy="依据主音频段落组织可见行动。",
        tone_and_plausibility="允许类型化表达，但不改动主音频时间权威。",
        duration_contract=duration_contract,
    )
    service.repository.save_project(document)
    spec = ProductionSpecContent(
        project_id=project_id,
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        title="主音频驱动制片规格",
        production_scope="依据主音频生成分镜、画面和时间线。",
        production_mode="AUDIO_LED",
        genre_and_setting="测试场景",
        language="zh-CN",
        aspect_ratio="9:16",
        target_duration_seconds=target_seconds,
        duration_intent="主音频定义完整时间线长度。",
        duration_contract=duration_contract,
        master_audio_binding_version_id=first_binding.version.id,
    )
    created = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=spec,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert any(
        ref.artifact_type == "production_document_version"
        and ref.artifact_id == first_binding.version.id
        for ref in created.version.source_artifact_refs
    )
    document = service.get_project(project_id).document
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    storyboard = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=StoryboardContent(
                project_id=project_id,
                title="主音频驱动分镜",
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                production_spec_document_id=created.document.id,
                production_spec_version_id=created.version.id,
                script_line_ids=[line.id for line in script.lines],
                shots=[shot.model_copy(deep=True) for shot in document.shots],
                clip_groups=[
                    ClipGroup(
                        id=f"audio-led-group-{shot.sequence}",
                        title=f"主音频生成段 {shot.sequence}",
                        shot_ids=[shot.id],
                        duration_seconds=shot.duration_seconds,
                        generation_intent=shot.narrative_purpose,
                    )
                    for shot in document.shots
                ],
                computed_duration_seconds=sum(
                    shot.duration_seconds for shot in document.shots
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    second_binding = service.bind_master_audio(
        project_id,
        MasterAudioBindRequest(
            asset_version_id=asset_version_id,
            source_in_seconds=0.75,
            source_out_seconds=4.75,
            time_origin_seconds=1.0,
            base_version_id=first_binding.version.id,
            origin=ProductionDocumentOrigin.USER,
        ),
    )
    invalidated = service.get_project(project_id).document
    spec_slot = next(
        item
        for item in invalidated.output_slots
        if item.id == PRODUCTION_SPEC_OUTPUT_SLOT_ID
    )
    assert spec_slot.current_artifact_ref is None
    assert spec_slot.invalidation_reason is not None
    assert "Master Audio changed" in spec_slot.invalidation_reason
    with pytest.raises(WorkflowConflictError, match="current exact Master Audio"):
        service.replace_production_spec(
            project_id,
            ProductionSpecReplaceRequest(
                spec=spec,
                base_version_id=created.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    with pytest.raises(WorkflowConflictError, match="current exact Master Audio"):
        service.replace_storyboard(
            project_id,
            StoryboardReplaceRequest(
                storyboard=storyboard.storyboard,
                base_version_id=storyboard.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    with pytest.raises(WorkflowConflictError, match="current exact Master Audio"):
        service.compile_generation_plan(
            project_id,
            GenerationPlanCompileRequest(
                storyboard_document_id=storyboard.document.id,
                storyboard_version_id=storyboard.version.id,
                production_spec_document_id=created.document.id,
                production_spec_version_id=created.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    rebound = service.replace_production_spec(
        project_id,
        ProductionSpecReplaceRequest(
            spec=spec.model_copy(
                update={
                    "master_audio_binding_version_id": second_binding.version.id,
                },
                deep=True,
            ),
            base_version_id=created.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert rebound.output_slot.current_artifact_ref is not None
    assert rebound.spec.master_audio_binding_version_id == second_binding.version.id
    assert any(
        ref.artifact_type == "production_document_version"
        and ref.artifact_id == second_binding.version.id
        for ref in rebound.version.source_artifact_refs
    )
    rebuilt_storyboard = service.replace_storyboard(
        project_id,
        StoryboardReplaceRequest(
            storyboard=storyboard.storyboard.model_copy(
                deep=True,
                update={
                    "production_spec_version_id": rebound.version.id,
                },
            ),
            base_version_id=storyboard.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    compiled = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=rebuilt_storyboard.document.id,
            storyboard_version_id=rebuilt_storyboard.version.id,
            production_spec_document_id=rebound.document.id,
            production_spec_version_id=rebound.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert compiled.generation_plan.production_spec_version_id == rebound.version.id


def test_master_audio_api_projects_timed_text_and_replaces_native_timeline_audio(
    tmp_path: Path,
) -> None:
    analyzer = FakeMusicAnalysisService()
    service = _service(tmp_path, music_analysis_service=analyzer)
    project_id = service.initialize_fixture().document.project.id
    service.enqueue(project_id, EnqueueRequest(candidate_count=1))
    drained = service.drain(project_id, 500)
    assert drained.remaining_runnable == 0
    ordinary = service.rebuild_manifest(project_id)
    assert len(ordinary.audio_clips) == len(ordinary.video_clips)
    assert all(clip.role != "MASTER_AUDIO" for clip in ordinary.audio_clips)

    audio_path = tmp_path / "master.wav"
    _write_wav(audio_path, duration_seconds=5.0)
    _, asset_version_id = _approved_audio_version(service, project_id, audio_path)
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)

    bound_response = client.post(
        f"/api/short-drama/projects/{project_id}/audio/master-binding",
        json={
            "asset_version_id": asset_version_id,
            "source_in_seconds": 0.5,
            "source_out_seconds": 4.5,
            "time_origin_seconds": 1.0,
            "origin": "USER",
        },
    )
    assert bound_response.status_code == 200, bound_response.text
    bound = MasterAudioBindingResponse.model_validate(bound_response.json())

    duration_contract = DurationContract(
        target_seconds=5.0,
        target_origin=DurationTargetOrigin.MASTER_AUDIO,
        authority=DurationAuthority.AUDIO_LED,
        acceptable_range_seconds=None,
        source_fidelity_policy=(
            DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
        ),
        mismatch_resolution=DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
        decision_basis="已批准的主音频窗口与时间原点定义完整时间线。",
    )
    document = service.get_project(project_id).document
    current_script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    current_script.story_intent = StoryIntent(
        audience_experience="视听节奏紧贴已批准主音频。",
        dramatic_engine="声音结构驱动画面变化。",
        progression_strategy="依据主音频段落组织时间线。",
        tone_and_plausibility="主音频是该版本的时间权威。",
        duration_contract=duration_contract,
    )
    service.repository.save_project(document)
    service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="主音频驱动时间线",
                production_scope="使用已批准主音频组织视频和字幕。",
                production_mode="AUDIO_LED",
                genre_and_setting="主音频集成测试",
                language="zh-CN",
                aspect_ratio="9:16",
                target_duration_seconds=5.0,
                duration_intent="主音频定义五秒成片时钟。",
                duration_contract=duration_contract,
                master_audio_binding_version_id=bound.version.id,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    timed_response = client.post(
        f"/api/short-drama/projects/{project_id}/audio/timed-text-lock",
        json={
            "master_audio_version_id": bound.version.id,
            "entries": [
                {
                    "id": "title-1",
                    "text": "精确标题",
                    "start_seconds": 1.25,
                    "end_seconds": 2.25,
                    "language": "zh-CN",
                    "speaker": "歌手",
                }
            ],
            "origin": "AGENT",
        },
    )
    assert timed_response.status_code == 200, timed_response.text

    analyzed_response = client.post(
        f"/api/short-drama/projects/{project_id}/audio/analyze-music",
        json={"master_audio_version_id": bound.version.id},
    )
    assert analyzed_response.status_code == 200, analyzed_response.text
    assert analyzed_response.json()["structure"]["analyzer"] == (
        "fake-music-analysis"
    )

    manifest = service.rebuild_manifest(project_id)
    assert len(manifest.audio_clips) == 1
    master_clip = manifest.audio_clips[0]
    assert master_clip.role == "MASTER_AUDIO"
    assert master_clip.shot_id is None
    assert master_clip.take_id is None
    assert master_clip.asset_version_id == asset_version_id
    assert master_clip.source_in_seconds == 0.5
    assert master_clip.source_out_seconds == 4.5
    assert master_clip.start_seconds == 1.0
    assert manifest.duration_seconds == 5.0
    assert sum(clip.duration_seconds for clip in manifest.video_clips) == 5.0
    assert [cue.text for cue in manifest.subtitle_cues] == ["精确标题"]
    assert manifest.subtitle_cues[0].timed_text_id == "title-1"
    assert manifest.subtitle_cues[0].script_line_id is None

    inspection = service.inspect_current_manifest_audio(
        project_id,
        AudioInspectionRequest(),
    )
    assert inspection.report.clip_inspections[0].shot_id == "MASTER_AUDIO"
    assert inspection.report.dialogue_checks == []
    assert not any(
        issue.code == "AUDIO_UNKNOWN_SCRIPT_LINE"
        for issue in inspection.report.issues
    )

    with pytest.raises(HTTPError) as failure:
        client.post(
            f"/api/short-drama/projects/{project_id}/audio/timed-text-lock",
            json={
                "master_audio_version_id": bound.version.id,
                "entries": [
                    {
                        "id": "outside",
                        "text": "越界",
                        "start_seconds": 3.9,
                        "end_seconds": 4.2,
                    }
                ],
                "origin": "AGENT",
            },
        )
    assert failure.value.status_code == 409


@pytest.mark.parametrize(
    ("authority", "target_origin", "mismatch_resolution"),
    [
        (
            DurationAuthority.TARGET_LED,
            DurationTargetOrigin.USER_DECLARED,
            DurationMismatchResolution.REVISE_SCRIPT,
        ),
        (
            DurationAuthority.SOURCE_LED,
            DurationTargetOrigin.SOURCE_DECLARED,
            DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
        ),
    ],
)
def test_bound_master_audio_does_not_take_over_non_audio_led_timeline(
    tmp_path: Path,
    authority: DurationAuthority,
    target_origin: DurationTargetOrigin,
    mismatch_resolution: DurationMismatchResolution,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    service.enqueue(project_id, EnqueueRequest(candidate_count=1))
    assert service.drain(project_id, 500).remaining_runnable == 0
    ordinary = service.rebuild_manifest(project_id)
    duration_contract = DurationContract(
        target_seconds=ordinary.duration_seconds,
        target_origin=target_origin,
        authority=authority,
        acceptable_range_seconds=None,
        source_fidelity_policy=(
            DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
        ),
        mismatch_resolution=mismatch_resolution,
        decision_basis="此制片版本的时长权威不是主音频。",
    )
    current_script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    current_script.story_intent = StoryIntent(
        audience_experience="保持剧本的自然演出时长。",
        dramatic_engine="画面叙事驱动时间线。",
        progression_strategy="主音频作为素材使用而非总时钟。",
        tone_and_plausibility="不改变已确定的剧情节奏。",
        duration_contract=duration_contract,
    )
    service.repository.save_project(document)
    service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="非音频时长权威测试",
                production_scope="将主音频作为已批准的时间线音频素材。",
                production_mode=authority.value,
                genre_and_setting="时长权威回归测试",
                language="zh-CN",
                aspect_ratio="9:16",
                target_duration_seconds=ordinary.duration_seconds,
                duration_intent="成片不应被后续绑定的音频自动改长。",
                duration_contract=duration_contract,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    audio_path = tmp_path / f"{authority.value.lower()}.wav"
    _write_wav(audio_path, duration_seconds=5.0)
    _, asset_version_id = _approved_audio_version(
        service,
        project_id,
        audio_path,
    )
    service.bind_master_audio(
        project_id,
        MasterAudioBindRequest(
            asset_version_id=asset_version_id,
            source_in_seconds=0.5,
            source_out_seconds=4.5,
            time_origin_seconds=1.0,
            origin=ProductionDocumentOrigin.USER,
        ),
    )

    manifest = service.rebuild_manifest(project_id)

    assert manifest.duration_seconds == ordinary.duration_seconds
    assert sum(clip.duration_seconds for clip in manifest.video_clips) == (
        ordinary.duration_seconds
    )
    assert [clip.role for clip in manifest.audio_clips] == ["MASTER_AUDIO"]


def test_local_music_analysis_reports_measured_metadata_without_inventing_fields(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "measured.wav"
    _write_wav(audio_path, duration_seconds=2.0)
    binding = MasterAudioBinding(
        asset_id="asset-audio",
        asset_version_id="asset-version-audio",
        source_in_seconds=0.25,
        source_out_seconds=1.75,
        time_origin_seconds=0.0,
        duration_seconds=1.5,
    )

    result = LocalMusicAnalysisService().analyze(
        audio_path=audio_path,
        binding=binding,
        created_at="2026-09-04T00:00:00+00:00",
    )

    assert isinstance(result, AudioStructureMap)
    assert result.file_size_bytes == audio_path.stat().st_size
    assert result.sample_rate_hz == 16_000
    assert result.channels == 1
    if result.analysis_level == "BASIC_METADATA":
        assert result.bpm is None
        assert result.beats == []
        assert result.onsets == []
        assert result.energy == []
        assert result.sections == []
        assert result.key_moments == []


def test_timeline_export_replaces_take_audio_with_the_master_audio_clip(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "take.mp4"
    audio_path = tmp_path / "master.wav"
    _write_video(video_path)
    _write_wav(audio_path, duration_seconds=1.0, frequency_hz=880.0)
    manifest = TimelineManifest(
        id="master-audio-export-v1",
        project_id="master-audio-export",
        version=1,
        script_version_id="script-v1",
        bible_version_id="bible-v1",
        video_clips=[
            TimelineVideoClip(
                id="video-1",
                shot_id="shot-1",
                take_id="take-1",
                media_path=str(video_path),
                start_seconds=0.0,
                source_in_seconds=0.0,
                duration_seconds=1.3,
                transition_in="none",
                transition_out="none",
            )
        ],
        audio_clips=[
            TimelineAudioClip(
                id="master-audio-1",
                asset_id="asset-audio",
                asset_version_id="asset-version-audio",
                media_path=str(audio_path),
                start_seconds=0.5,
                source_in_seconds=0.1,
                source_out_seconds=0.9,
                duration_seconds=0.8,
                gain_db=0.0,
                role="MASTER_AUDIO",
            )
        ],
        subtitle_cues=[],
        output_spec=OutputSpec(
            width=108,
            height=192,
            fps=12,
            crf=28,
            audio_sample_rate=24_000,
            audio_bitrate_kbps=64,
        ),
        duration_seconds=1.3,
        approval_status=ApprovalStatus.DRAFT,
        freshness=Freshness.CURRENT,
        created_at="2026-09-04T00:00:00+00:00",
    )

    export = TimelineExporter(tmp_path / "workspace").render(
        manifest,
        "2026-09-04T00:00:01+00:00",
    )

    assert all(check.passed for check in export.checks), export.checks
    assert Path(export.clean_video_path).is_file()
    assert (
        tmp_path
        / "workspace"
        / manifest.project_id
        / "exports"
        / manifest.id
        / "final-native-audio.mp4"
    ).is_file()
