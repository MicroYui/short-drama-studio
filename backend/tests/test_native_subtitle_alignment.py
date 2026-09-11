"""Native caption timing uses source audio and persists only a new manifest."""

from __future__ import annotations

import json
from pathlib import Path
import wave

import numpy as np
import pytest

from short_drama.audio_inspection import (
    AudioSemanticResult,
    TimedAudioCharacter,
    TimedAudioTranscript,
    continuous_audio_groups,
    normalize_dialogue_text,
)
from short_drama.models import (
    ApprovalStatus, AudioInspectionRequest, Freshness, OutputSpec, ProjectStage,
    SubtitleCue, TimelineAudioClip, TimelineManifest, TimelineVideoClip,
)
from short_drama.service import ShortDramaService
from short_drama.subtitle_alignment import NativeSubtitleAlignmentResponse
from short_drama.tools import ToolCallStatus


class FakeTimedAudioService:
    provider = "fake-local-timing"
    transcription_model = "fake-timed-transcriber"
    speaker_model = None

    def __init__(self) -> None:
        self.lines: dict[str, str] = {}
        self.timing_calls: list[Path] = []
        self.has_anchors = True

    def transcribe_timed(self, *, audio_path: Path, language: str) -> TimedAudioTranscript:
        assert language == "zh-CN"
        self.timing_calls.append(audio_path)
        with wave.open(str(audio_path), "rb") as source:
            assert source.getframerate() == 16000
            assert source.getnframes() == 160000
        if not self.has_anchors:
            return TimedAudioTranscript(transcript_text="", characters=())
        characters = []
        observed = []
        for text, (start, end) in zip(self.lines.values(), [(2.0, 5.6), (7.4, 8.7)], strict=True):
            text = normalize_dialogue_text(text.replace("十点零三分", "10点03分"))
            observed.append(text)
            for index, character in enumerate(text):
                characters.append(TimedAudioCharacter(
                    text=character,
                    start_seconds=start + (end - start) * index / len(text),
                    end_seconds=start + (end - start) * (index + 1) / len(text),
                ))
        return TimedAudioTranscript(transcript_text=" ".join(observed), characters=tuple(characters))

    def analyze(self, *, audio_path: Path, language: str, include_speaker_embedding: bool) -> AudioSemanticResult:
        assert language == "zh-CN"
        assert not include_speaker_embedding
        text = next(text for line_id, text in self.lines.items() if line_id in audio_path.name)
        return AudioSemanticResult(transcript_text=text, detected_language=language)


def _fixture(tmp_path: Path) -> tuple[ShortDramaService, FakeTimedAudioService, TimelineManifest]:
    audio = FakeTimedAudioService()
    service = ShortDramaService(
        database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "media",
        audio_semantic_service=audio,
    )
    document = service.initialize_fixture().document
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    audio.lines = {line.id: line.spoken_text for line in script.lines[:2]}
    # Local PCM keeps the test focused on extraction, timing and persistence.
    source_path = tmp_path / "native.wav"
    samples = np.zeros(160000, dtype="<i2")
    for start, end in [(2.0, 5.6), (7.4, 8.7)]:
        indices = np.arange(round(start * 16000), round(end * 16000))
        samples[indices] = (8000 * np.sin(2 * np.pi * 330 * indices / 16000)).astype("<i2")
    with wave.open(str(source_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16000)
        destination.writeframes(samples.tobytes())
    audio_clips = [TimelineAudioClip(
        id=f"audio-{index}", shot_id=document.shots[index].id, take_id="take-native",
        media_path=str(source_path), start_seconds=index * 5.0, source_in_seconds=index * 5.0,
        source_out_seconds=(index + 1) * 5.0, duration_seconds=5.0, gain_db=0, role="native",
    ) for index in range(2)]
    manifest = TimelineManifest(
        id=f"{document.project.id}-manifest-v1", project_id=document.project.id, version=1,
        script_version_id=script.id, bible_version_id=document.project.current_bible_version_id,
        video_clips=[TimelineVideoClip(
            id=f"video-{index}", shot_id=clip.shot_id, take_id=clip.take_id, media_path=clip.media_path,
            start_seconds=clip.start_seconds, source_in_seconds=clip.source_in_seconds,
            duration_seconds=clip.duration_seconds, transition_in="cut", transition_out="cut",
        ) for index, clip in enumerate(audio_clips)],
        audio_clips=audio_clips,
        subtitle_cues=[SubtitleCue(
            id=f"cue-{index}", script_line_id=line.id, text=line.subtitle_text,
            start_seconds=index * 5.0 + 0.5, end_seconds=index * 5.0 + 4.5,
        ) for index, line in enumerate(script.lines[:2])],
        output_spec=OutputSpec(), duration_seconds=10.0, approval_status=ApprovalStatus.DRAFT,
        freshness=Freshness.CURRENT, created_at=document.project.created_at,
    )
    document.manifests.append(manifest)
    document.project.current_manifest_id = manifest.id
    document.project.stage = ProjectStage.TIMELINE_READY
    service.repository.save_project(document)
    return service, audio, manifest


def test_alignment_tool_preserves_media_and_text_and_survives_restart(tmp_path: Path) -> None:
    service, audio, before = _fixture(tmp_path)
    original = before.model_dump(mode="json")
    result = service.tool_registry.invoke("audio.align_subtitles", {
        "project_id": before.project_id, "request": {"manifest_id": before.id},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    aligned = NativeSubtitleAlignmentResponse.model_validate(result.output)
    assert aligned.manifest is not None
    assert aligned.manifest.version == 2
    assert result.artifact_refs[0].artifact_id == aligned.manifest.id
    assert aligned.manifest.video_clips == before.video_clips
    assert aligned.manifest.audio_clips == before.audio_clips
    assert aligned.manifest.duration_seconds == before.duration_seconds
    assert [cue.text for cue in aligned.manifest.subtitle_cues] == [cue.text for cue in before.subtitle_cues]
    first, second = aligned.manifest.subtitle_cues
    assert first.start_seconds == pytest.approx(1.92, abs=0.03)
    assert first.end_seconds == pytest.approx(5.72, abs=0.03)
    assert second.start_seconds == pytest.approx(7.32, abs=0.03)
    assert second.end_seconds == pytest.approx(8.82, abs=0.03)
    assert aligned.warnings == []
    assert len(aligned.observations) == 1
    assert "10点03分" in aligned.observations[0].transcript_text
    assert len(audio.timing_calls) == 1  # Both shot windows share continuous source audio.
    report = json.loads(Path(aligned.report_path).read_text())
    assert report["source_manifest_id"] == before.id
    assert len(report["groups"]) == 1
    assert "10点03分" in report["groups"][0]["transcript"]["transcript_text"]
    assert Path(report["groups"][0]["audio_path"]).is_file()
    assert before.model_dump(mode="json") == original

    # Inspection must extract a whole caption even when it crosses a shot cut.
    inspection = service.inspect_current_manifest_audio(before.project_id, AudioInspectionRequest(
        semantic_review=True, speaker_continuity=False, dialogue_padding_seconds=0.0,
    ))
    assert inspection.report.semantic_status.value == "COMPLETE"
    assert len(inspection.report.dialogue_checks) == 2
    assert all(check.exact_match is True for check in inspection.report.dialogue_checks)
    assert all(check.passed for check in inspection.report.technical_checks)

    stale = service.tool_registry.invoke("audio.align_subtitles", {
        "project_id": before.project_id, "request": {"manifest_id": before.id},
    })
    assert stale.status == ToolCallStatus.FAILED
    assert len(audio.timing_calls) == 1
    restarted = ShortDramaService(database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "media")
    saved = restarted.get_project(before.project_id).document
    assert saved.project.current_manifest_id == aligned.manifest.id
    assert saved.manifests[-1] == aligned.manifest
    reread = restarted.get_production_document(
        before.project_id, aligned.alignment_document_ref.artifact_id,
        version_id=aligned.alignment_version_ref.artifact_id,
    )
    assert reread.document.kind == "NATIVE_SUBTITLE_ALIGNMENT"
    assert reread.version.source_artifact_refs[0].artifact_id == before.id
    assert reread.version.payload["aligned_manifest_id"] == aligned.manifest.id
    assert reread.version.payload["observations"] == [item.model_dump(mode="json") for item in aligned.observations]
    assert reread.version.payload["video_clips_unchanged"] is True
    assert reread.version.payload["audio_clips_unchanged"] is True
    assert len(result.artifact_refs) == 3
    tool_read = restarted.tool_registry.invoke("artifact.get_production_document", {
        "project_id": before.project_id,
        "production_document_id": aligned.alignment_document_ref.artifact_id,
        "version_id": aligned.alignment_version_ref.artifact_id,
    })
    assert tool_read.status == ToolCallStatus.SUCCEEDED, tool_read.error
    assert tool_read.output["version"]["payload"] == reread.version.payload

    # Export is a render operation. A stale pin or accidental rebuild must not
    # replace the manifest or discard the newly authored subtitle times.
    for arguments, expected_status in [
        ({"rebuild_manifest": True}, ToolCallStatus.INVALID_ARGUMENTS),
        ({"manifest_id": before.id}, ToolCallStatus.FAILED),
    ]:
        rejected = restarted.tool_registry.invoke("timeline.export", {
            "project_id": before.project_id, "request": arguments,
        })
        assert rejected.status == expected_status, rejected.error
        current = restarted.get_project(before.project_id).document
        assert current.project.current_manifest_id == aligned.manifest.id
        assert current.manifests[-1] == aligned.manifest
    assert restarted.repository.list_exports(before.project_id) == []


def test_missing_lexical_anchors_preserves_windows_and_reports_uncertainty(tmp_path: Path) -> None:
    service, audio, before = _fixture(tmp_path)
    audio.has_anchors = False
    result = service.tool_registry.invoke("audio.align_subtitles", {
        "project_id": before.project_id, "request": {"manifest_id": before.id},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    aligned = NativeSubtitleAlignmentResponse.model_validate(result.output)
    assert aligned.manifest is not None
    assert aligned.manifest.subtitle_cues == before.subtitle_cues
    assert aligned.adjustments == []
    assert len(aligned.warnings) == 2


@pytest.mark.parametrize("change", [
    {"take_id": "another-take"}, {"source_in_seconds": 6.0, "source_out_seconds": 11.0},
    {"start_seconds": 5.1}, {"gain_db": -3.0},
])
def test_audio_groups_do_not_bridge_source_or_timeline_discontinuity(change: dict[str, object]) -> None:
    first = TimelineAudioClip(
        id="a", shot_id="shot-a", take_id="take-a", media_path="native.wav", start_seconds=0,
        source_in_seconds=0, source_out_seconds=5, duration_seconds=5, gain_db=0, role="native",
    )
    second = first.model_copy(update={"id": "b", "shot_id": "shot-b", "start_seconds": 5,
                                      "source_in_seconds": 5, "source_out_seconds": 10, **change})
    assert len(continuous_audio_groups([first, second])) == 2
