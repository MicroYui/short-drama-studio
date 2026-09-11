"""Current timeline alignment protects manual content and unrelated live edits."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import subprocess
from typing import cast
import wave

import imageio_ffmpeg
import numpy as np
import pytest
from pydantic import JsonValue

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.audio_inspection import AudioSemanticResult, TimedAudioCharacter, TimedAudioTranscript
from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave
from short_drama.subtitle_alignment import NativeSubtitleAlignmentResponse
from short_drama.tools import ToolCallStatus


class EditorialTimingService:
    provider = "local-test"
    transcription_model = "fixture-timing"
    speaker_model = None

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.on_transcribe: Callable[[], None] | None = None
        self.anchors = True

    def transcribe_timed(self, *, audio_path: Path, language: str) -> TimedAudioTranscript:
        with wave.open(str(audio_path), "rb") as wav:
            self.calls.append((wav.getnframes(), wav.getframerate()))
        if self.on_transcribe is not None:
            self.on_transcribe()
        text = "保留人工台词"
        return TimedAudioTranscript(transcript_text=text if self.anchors else "", characters=tuple(
            TimedAudioCharacter(text=char, start_seconds=.4 + index * .08, end_seconds=.48 + index * .08)
            for index, char in enumerate(text)
        ) if self.anchors else ())

    def analyze(self, *, audio_path: Path, language: str, include_speaker_embedding: bool) -> AudioSemanticResult:
        return AudioSemanticResult(transcript_text="保留人工台词", detected_language=language)


def _current_fixture(tmp_path: Path) -> tuple[ShortDramaService, EditorialTimingService, str, str]:
    timing = EditorialTimingService()
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media", audio_semantic_service=timing)
    document = service.initialize_fixture().document
    pid, shot_id = document.project.id, document.shots[0].id
    sound = tmp_path / "speech.wav"
    x = np.arange(64000, dtype=np.float64) / 16000
    signal = (np.sin(2 * np.pi * 440 * x) * 4096).astype("<i2")
    with wave.open(str(sound), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(signal.tobytes())
    video = tmp_path / "take.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=320x180:r=24:d=4", "-i", str(sound), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True)
    imported = import_studio_media(service, pid, video, name="现有镜头", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, pid, shot_id, StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    music = import_studio_media(service, pid, sound, name="独立音乐", kind=AssetKind.REFERENCE, asset_id=None)
    music_version = music.document.assets[-1].versions[-1]
    saved = read_editorial(service, pid)
    project = EditorProject.model_validate(saved.project)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    clip = next(item for item in timeline.clips if item.type == "video")
    clip.trimStart, clip.duration, clip.speed, clip.reversed, clip.volume = 1, 1.5, 2, True, .5
    clip.muted = False
    music_asset = EditorAsset(id="music-for-timeline", path=music_version.local_path or "", type="audio", duration=4)
    project.assets.append(music_asset)
    timeline.clips.append(EditorClip(id="music-clip", assetId=music_asset.id, type="audio", startTime=0, duration=4, trimStart=0, volume=.3, trackIndex=1))
    timeline.subtitles = [
        EditorSubtitle(id="manual", text="保留人工台词", startTime=.1, endTime=1.25, trackIndex=3, style={"fontSize": 42, "color": "#ffcc00"}),
        EditorSubtitle(id="other", text="不相关的后续字幕", startTime=2, endTime=3, trackIndex=3, style={"fontSize": 38}),
    ]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    return service, timing, pid, shot_id


def _edit_current(service: ShortDramaService, pid: str, change: Callable[[EditorProject], None]) -> None:
    saved = read_editorial(service, pid)
    project = EditorProject.model_validate(saved.project)
    change(project)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))


def test_current_alignment_uses_edited_native_audio_and_merges_times_across_unrelated_changes(tmp_path: Path) -> None:
    service, timing, pid, _shot_id = _current_fixture(tmp_path)
    before = read_editorial(service, pid)
    original = EditorProject.model_validate(before.project)
    def concurrent_edit(project: EditorProject) -> None:
        timeline = project.timelines[0]
        timeline.subtitles[0].style["fontSize"] = 51
        timeline.subtitles[1].text = "用户并行改了另一个字幕"
        next(clip for clip in timeline.clips if clip.id == "music-clip").volume = .2
    timing.on_transcribe = lambda: _edit_current(service, pid, concurrent_edit)
    result = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"expected_editorial_revision": before.revision, "cue_ids": ["manual"], "manifest_id": "old-legacy-id"}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = NativeSubtitleAlignmentResponse.model_validate(result.output)
    assert response.manifest is None
    assert response.source_editorial_revision == before.revision
    assert response.editorial_revision == before.revision + 2
    assert response.changed_cue_ids == ["manual"]
    assert timing.calls == [(24000, 16000)]  # Actual trim/speed duration, no music transcription.
    after = read_editorial(service, pid)
    current = EditorProject.model_validate(after.project)
    first, other = current.timelines[0].subtitles
    assert first.text == "保留人工台词" and first.style["fontSize"] == 51
    assert first.style["color"] == "#ffcc00"
    assert .19 <= first.startTime <= .33 and .96 <= first.endTime <= 1.05
    assert other.text == "用户并行改了另一个字幕" and (other.startTime, other.endTime) == (2, 3)
    assert next(clip for clip in current.timelines[0].clips if clip.type == "video") == next(clip for clip in original.timelines[0].clips if clip.type == "video")
    assert next(clip for clip in current.timelines[0].clips if clip.id == "music-clip").volume == .2
    assert service.get_project(pid).document.manifests == []
    assert result.artifact_refs[0].artifact_type == "editorial_timeline"
    assert result.artifact_refs[0].version == after.revision
    summary = ProjectArtifactResolver(service.repository).summarize_artifact(result.artifact_refs[0])
    assert summary is not None and "timeline.read_editorial" in summary.summary
    restarted = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    assert read_editorial(restarted, pid).project == after.project
    report = json.loads(Path(response.report_path).read_text())
    assert report["changed_cue_ids"] == ["manual"]
    assert report["editorial_revision"] == after.revision
    assert len(report["source_audio"]) == 1
    assert "legacy manifest_id" in response.warnings[0]


@pytest.mark.parametrize("change_kind", ["caption", "audio", "subtitle_track"])
def test_relevant_concurrent_changes_preserve_current_timeline(tmp_path: Path, change_kind: str) -> None:
    service, timing, pid, _ = _current_fixture(tmp_path)
    before = read_editorial(service, pid)
    def change(project: EditorProject) -> None:
        if change_kind == "caption":
            project.timelines[0].subtitles[0].text = "用户新台词"
        elif change_kind == "audio":
            next(clip for clip in project.timelines[0].clips if clip.type == "video").volume = .7
        else:
            project.timelines[0].tracks[3].enabled = False
    timing.on_transcribe = lambda: _edit_current(service, pid, change)
    result = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"expected_editorial_revision": before.revision, "cue_ids": ["manual"]}})
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert "changed during alignment" in result.error.message
    saved = read_editorial(service, pid)
    assert saved.revision == before.revision + 1
    project = EditorProject.model_validate(saved.project)
    assert project.timelines[0].subtitles[0].startTime == .1
    assert project.timelines[0].subtitles[0].endTime == 1.25
    assert len(timing.calls) == 1


def test_current_alignment_rejects_stale_scope_before_asr_and_supports_shot_selection(tmp_path: Path) -> None:
    service, timing, pid, shot_id = _current_fixture(tmp_path)
    before = read_editorial(service, pid)
    stale = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"expected_editorial_revision": before.revision - 1, "cue_ids": ["manual"]}})
    assert stale.status == ToolCallStatus.FAILED
    missing = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"cue_ids": ["absent-cue"]}})
    assert missing.status == ToolCallStatus.FAILED and timing.calls == []
    scoped = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"expected_editorial_revision": before.revision, "shot_ids": [shot_id]}})
    assert scoped.status == ToolCallStatus.SUCCEEDED, scoped.error
    response = NativeSubtitleAlignmentResponse.model_validate(scoped.output)
    assert response.changed_cue_ids == ["manual"] and len(timing.calls) == 1


def test_no_anchors_do_not_increment_current_editorial_revision(tmp_path: Path) -> None:
    service, timing, pid, _ = _current_fixture(tmp_path)
    before = read_editorial(service, pid)
    timing.anchors = False
    result = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid, "request": {"cue_ids": ["manual"]}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = NativeSubtitleAlignmentResponse.model_validate(result.output)
    assert response.editorial_revision == before.revision
    assert response.changed_cue_ids == [] and response.warnings
    assert read_editorial(service, pid).project == before.project


def test_parallel_caption_tracks_share_asr_without_changing_an_unselected_track(tmp_path: Path) -> None:
    service, timing, pid, _ = _current_fixture(tmp_path)
    def add_parallel(project: EditorProject) -> None:
        project.timelines[0].subtitles.append(EditorSubtitle(id="parallel", text="另一轨的标签",
            startTime=0, endTime=1.5, trackIndex=2, style={"fontSize": 18}))
    _edit_current(service, pid, add_parallel)
    result = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid, "request": {"cue_ids": ["manual"]}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert NativeSubtitleAlignmentResponse.model_validate(result.output).changed_cue_ids == ["manual"]
    assert len(timing.calls) == 1
    project = EditorProject.model_validate(read_editorial(service, pid).project)
    parallel = next(cue for cue in project.timelines[0].subtitles if cue.id == "parallel")
    assert (parallel.startTime, parallel.endTime, parallel.text, parallel.style) == (0, 1.5, "另一轨的标签", {"fontSize": 18})
