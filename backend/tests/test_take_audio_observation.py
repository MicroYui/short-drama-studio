"""Unadopted real media can be heard without creating editorial content."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast
import wave

import imageio_ffmpeg
import numpy as np
from pydantic import JsonValue, ValidationError
import pytest

from short_drama.models import AudioInspectionRequest, QaStatus, SemanticReviewStatus, Take
from short_drama.native_transcription import NativeTranscriptionRequest, NativeTranscriptionResponse
from short_drama.observation_sources import audio_report_evidence
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave
from short_drama.take_audio import take_audio_source_is_current
from short_drama.tools import ToolCallStatus
from tests.test_studio_subtitle_alignment import EditorialTimingService, _current_fixture
from tests.test_video_detail import render_source


def _candidate(tmp_path: Path) -> tuple[ShortDramaService, EditorialTimingService, str, Take]:
    service, timing, pid, _ = _current_fixture(tmp_path)
    take = service.repository.list_takes(pid)[0]
    take.is_selected, take.selected_shot_ids, take.qa_status = False, [], QaStatus.REJECTED
    service.repository.save_take(take)
    document = service.get_project(pid).document
    document.editorial_timeline = None
    service.repository.save_project(document)
    return service, timing, pid, take


def _transcribe(service: ShortDramaService, pid: str, take: Take, start: float = 1.0, end: float = 2.5) -> NativeTranscriptionResponse:
    result = service.tool_registry.invoke("audio.transcribe_native", {"project_id": pid, "request": {
        "scope": "TAKE", "take_id": take.id, "source_start_seconds": start, "source_end_seconds": end}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    return NativeTranscriptionResponse.model_validate(result.output)


def test_candidate_asr_and_signal_preserve_project_takes_qa_and_source_coordinates(tmp_path: Path) -> None:
    service, timing, pid, take = _candidate(tmp_path)
    before = service.get_project(pid)
    result = _transcribe(service, pid, take)
    assert result.observation_scope == "TAKE" and result.time_space == "TAKE_SOURCE"
    assert result.source_editorial_revision is None and result.editorial_timeline_id is None
    assert result.source_status == "CURRENT"
    assert timing.calls == [(24000, 16000)]
    window = result.windows[0]
    assert (window.start_seconds, window.end_seconds) == (1.0, 2.5)
    assert window.clip_ids == [] and window.shot_ids == [take.shot_id]
    assert window.characters[0].start_seconds == 1.4
    assert json.loads(Path(result.report_path).read_text())["time_space"] == "TAKE_SOURCE"
    report = service.inspect_current_manifest_audio(pid, AudioInspectionRequest(scope="TAKE", take_id=take.id,
        source_start_seconds=1, source_end_seconds=2.5)).report
    assert report.time_space == "TAKE_SOURCE" and report.source_take_id == take.id
    assert report.source_editorial_revision is None and report.source_editorial_timeline_id is None
    assert report.clip_inspections[0].timeline_start_seconds == 1
    metrics = report.clip_inspections[0].metrics
    assert metrics is not None and metrics.duration_seconds == pytest.approx(1.5, abs=.001)
    assert report.semantic_status == SemanticReviewStatus.UNAVAILABLE
    assert report.dialogue_checks == report.speaker_checks == []
    assert not any(item.name.startswith("subtitle-") for item in report.technical_checks)
    after = service.get_project(pid)
    assert before.document == after.document and before.takes == after.takes and before.tasks == after.tasks
    assert after.document.editorial_timeline is None and not after.document.manifests
    assert service.repository.get_audio_inspection(report.id) == report
    assert service.repository.get_take(take.id) == take


@pytest.mark.parametrize("source_request", [
    {"scope": "TAKE"}, {"take_id": "some-take"}, {"source_start_seconds": 1},
    {"scope": "TAKE", "take_id": "x", "source_start_seconds": 2, "source_end_seconds": 1},
    {"scope": "TAKE", "take_id": "x", "source_start_seconds": float("nan")},
    {"scope": "TAKE", "take_id": "x", "source_end_seconds": float("inf")},
])
def test_source_contract_rejects_ambiguous_or_invalid_ranges(source_request: dict[str, object]) -> None:
    for model in (AudioInspectionRequest, NativeTranscriptionRequest):
        with pytest.raises(ValidationError):
            model.model_validate(source_request)


@pytest.mark.parametrize("editorial_scope", [{"timeline_id": "film"}, {"expected_editorial_revision": 0},
                                          {"clip_ids": ["clip"]}, {"shot_ids": ["shot"]}])
def test_take_transcription_rejects_editorial_selectors(editorial_scope: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NativeTranscriptionRequest.model_validate({"scope": "TAKE", "take_id": "take", **editorial_scope})


def test_actual_media_bounds_missing_stream_and_cross_project_fail_before_asr(tmp_path: Path) -> None:
    service, timing, pid, take = _candidate(tmp_path)
    before = service.get_project(pid).document
    take.duration_seconds = 50  # Stored metadata cannot authorize reading beyond the actual 4s file.
    service.repository.save_take(take)
    for tool in ("audio.transcribe_native", "audio.inspect_manifest"):
        for data in ({"source_start_seconds": 3, "source_end_seconds": 5}, {"take_id": "absent"}):
            result = service.tool_registry.invoke(tool, {"project_id": pid,
                "request": {"scope": "TAKE", "take_id": take.id, **data}})
            assert result.status == ToolCallStatus.FAILED
    foreign = take.model_copy(deep=True, update={"id": "foreign-take", "project_id": "another-project"})
    other = before.model_copy(deep=True)
    other.project.id = foreign.project_id
    service.repository.save_project(other)
    service.repository.save_take(foreign)
    for tool in ("audio.transcribe_native", "audio.inspect_manifest"):
        result = service.tool_registry.invoke(tool, {"project_id": pid,
            "request": {"scope": "TAKE", "take_id": foreign.id}})
        assert result.status == ToolCallStatus.FAILED
    no_audio = tmp_path / "silent-video.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x36:r=24:d=1", "-an", "-c:v", "libx264", str(no_audio)], check=True)
    take.media_path = str(no_audio)
    service.repository.save_take(take)
    result = service.tool_registry.invoke("audio.transcribe_native", {"project_id": pid,
        "request": {"scope": "TAKE", "take_id": take.id}})
    assert result.status == ToolCallStatus.FAILED and "no native audio stream" in str(result.error)
    report = service.inspect_current_manifest_audio(pid, AudioInspectionRequest(scope="TAKE", take_id=take.id)).report
    assert report.clip_inspections[0].metrics is None
    assert any(item.code == "AUDIO_STREAM_UNAVAILABLE" for item in report.issues)
    assert timing.calls == [] and service.get_project(pid).document == before
    no_audio.unlink()
    result = service.tool_registry.invoke("audio.transcribe_native", {"project_id": pid,
        "request": {"scope": "TAKE", "take_id": take.id}})
    assert result.status == ToolCallStatus.FAILED and timing.calls == []


def test_original_source_currentness_never_implies_current_editorial_coverage(tmp_path: Path) -> None:
    service, _, pid, take = _candidate(tmp_path)
    report = service.inspect_current_manifest_audio(pid, AudioInspectionRequest(scope="TAKE", take_id=take.id,
        source_start_seconds=1, source_end_seconds=2)).report
    document = service.get_project(pid).document
    assert audio_report_evidence(document, report, source_take=take)["source_status"] == "CURRENT"
    edited = EditorProject.model_validate(read_editorial(service, pid).project)
    edited.name = "用户并行标题"
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=0,
        timeline={"project": cast(dict[str, JsonValue], edited.model_dump(mode="json"))}))
    document = service.get_project(pid).document
    assert audio_report_evidence(document, report, source_take=take)["source_status"] == "CURRENT"
    evidence = audio_report_evidence(document, report, take_id=take.id, source_take=take,
        timeline_start=1, timeline_end=2)
    assert evidence["source_status"] == "OUTSIDE_REQUESTED_USE" and evidence["clip_inspections"] == []
    changed = take.model_copy(deep=True, update={"media_path": str(tmp_path / "changed.mp4")})
    assert audio_report_evidence(document, report, source_take=changed)["source_status"] == "HISTORICAL_OR_UNVERIFIED"
    with Path(take.media_path).open("ab") as file:
        file.write(b"changed")
    assert audio_report_evidence(document, report, source_take=take)["source_status"] == "HISTORICAL_OR_UNVERIFIED"


def test_source_changes_during_asr_are_reported_without_overwriting_parallel_content(tmp_path: Path) -> None:
    service, timing, pid, take = _candidate(tmp_path)
    def change_source() -> None:
        current = service.get_project(pid).document
        current.project.name = "用户保留的改动"
        service.repository.save_project(current)
        changed = take.model_copy(deep=True, update={"media_path": str(tmp_path / "replacement.mp4")})
        service.repository.save_take(changed)
    timing.on_transcribe = change_source
    result = _transcribe(service, pid, take)
    assert result.source_status == "HISTORICAL_OR_UNVERIFIED"
    assert not take_audio_source_is_current(service.repository.get_take(take.id), result.source_context)
    assert service.get_project(pid).document.project.name == "用户保留的改动"
    assert service.get_project(pid).document.editorial_timeline is None


def test_candidate_pcm_preserves_late_audio_stream_in_original_take_time(tmp_path: Path) -> None:
    service, timing, pid, take = _candidate(tmp_path)
    source = tmp_path / "offset.mov"
    render_source(source, audio_offset=.4)
    take.media_path = str(source)
    service.repository.save_take(take)
    result = _transcribe(service, pid, take, start=1.5, end=2.5)
    with wave.open(str(Path(result.report_path).parent / "source-001.wav"), "rb") as wav:
        values = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        assert wav.getframerate() == 16000
    # Source pulses are at 1.37 + .4 and 1.87 + .4. Both channels downmix to mono.
    first = np.flatnonzero(np.abs(values) > 1000)[0] / 16000
    assert first == pytest.approx(.27, abs=.003)
    assert np.max(np.abs(values[round(.72 * 16000):round(.80 * 16000)])) > 1000
    assert timing.calls == [(16000, 16000)]


def test_default_end_uses_actual_media_and_long_take_reuses_30_second_asr_windows(tmp_path: Path) -> None:
    service, timing, pid, take = _candidate(tmp_path)
    source = tmp_path / "long-take.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x36:r=10:d=32", "-f", "lavfi", "-i", "sine=frequency=440:duration=32",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    take.media_path, take.duration_seconds = str(source), 500
    service.repository.save_take(take)
    result = service.tool_registry.invoke("audio.transcribe_native", {"project_id": pid,
        "request": {"scope": "TAKE", "take_id": take.id, "source_start_seconds": 1.5}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = NativeTranscriptionResponse.model_validate(result.output)
    assert [(item.start_seconds, item.end_seconds) for item in response.windows] == [(1.5, 31.5), (31.5, 32)]
    assert timing.calls == [(480000, 16000), (8000, 16000)]
    assert response.windows[-1].characters[-1].end_seconds <= 32
