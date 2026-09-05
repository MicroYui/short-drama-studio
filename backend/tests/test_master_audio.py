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
    EnqueueRequest,
    Freshness,
    MasterAudioBinding,
    MasterAudioBindingResponse,
    MasterAudioBindRequest,
    MusicAnalysisRequest,
    MusicAnalysisResponse,
    OutputSpec,
    ProductionDocumentOrigin,
    TimedTextLock,
    TimedTextLockResponse,
    TimedTextLockUpdateRequest,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
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
