"""Exact picture replacement retains existing PCM through the shared exporter."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.h3_picture_source import picture_only_binding, verify_retained_audio_files
from short_drama.models import H3PictureOnlyTarget, QaStatus, Take
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from tests.test_studio_shot_editorial import episode as episode
from tests.test_studio_take_source_selection import candidate_use as candidate_use
from tests.test_whole_group_shot_editorial import whole_group as whole_group


@pytest.mark.parametrize("split", [True, False])
def test_exact_picture_replacement_keeps_sound_and_all_other_uses(candidate_use: tuple[ShortDramaService, str, Take, str], split: bool) -> None:
    service, pid, take, clip_id = candidate_use
    if not split:
        current = read_editorial(service, pid)
        editor = EditorProject.model_validate(current.project)
        video = next(c for c in editor.timelines[0].clips if c.id == clip_id)
        assert video.model_extra is not None
        linked = cast(list[str], video.model_extra["linkedClipIds"])
        editor.timelines[0].clips = [c for c in editor.timelines[0].clips if c.id not in linked]
        video.model_extra["linkedClipIds"] = []
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
            timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    before = read_editorial(service, pid)
    old = EditorProject.model_validate(before.project)
    baseline = start_export(service, pid, StudioExportRequest(expected_revision=before.revision, width=480, height=270, fps=24), background=False)
    assert baseline.status == "completed", baseline.error
    result = service.tool_registry.invoke("video.select_take", {"take_id": take.id, "clip_ids": [clip_id],
        "expected_editorial_revision": before.revision, "source_in_seconds": 4.5, "source_out_seconds": 10.045,
        "audio_policy": "KEEP_CURRENT_AUDIO"})
    assert result.status.value == "SUCCEEDED", result.error
    after = read_editorial(service, pid)
    editor = EditorProject.model_validate(after.project)
    clips = {c.id: c for c in editor.timelines[0].clips}
    video = clips[clip_id]
    assert video.muted and video.asset is not None and video.asset.path == take.media_path
    assert (video.startTime, video.duration, video.trimStart) == (3.875, 5.545, 4.5)
    for clip in old.timelines[0].clips:
        if clip.id != clip_id:
            assert clips[clip.id] == clip
    assert editor.timelines[0].subtitles == old.timelines[0].subtitles
    if split:
        assert editor.timelines[0].tracks == old.timelines[0].tracks
    else:
        extra = [c for c in editor.timelines[0].clips if c.id not in {o.id for o in old.timelines[0].clips}]
        assert len(extra) == 1 and extra[0].type == "audio"
        source = next(c for c in old.timelines[0].clips if c.id == clip_id)
        assert (extra[0].assetId, extra[0].trimStart, extra[0].duration, extra[0].volume,
                extra[0].audioFadeInSeconds, extra[0].audioFadeOutSeconds) == (source.assetId, source.trimStart,
                source.duration, source.volume, source.audioFadeInSeconds, source.audioFadeOutSeconds)
    assert result.receipt is not None
    assert result.receipt.current_state["audio_policy"] == "KEEP_CURRENT_AUDIO"
    assert result.receipt.current_state["current_audio_clips_in_target"]
    rendered = start_export(service, pid, StudioExportRequest(expected_revision=after.revision, width=480, height=270, fps=24), background=False)
    assert rendered.status == "completed", rendered.error
    def pcm(path: str | None) -> bytes:
        assert path is not None
        return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path, "-map", "0:a:0",
            "-f", "s16le", "-acodec", "pcm_s16le", "-"], capture_output=True, check=True).stdout
    assert pcm(baseline.video_path) == pcm(rendered.video_path)
    service.select_take(take.id, clip_ids=[clip_id], expected_editorial_revision=after.revision,
        source_in_seconds=4.5, source_out_seconds=10.045, audio_policy="KEEP_CURRENT_AUDIO")
    assert read_editorial(service, pid) == after
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert read_editorial(restarted, pid) == after


def test_keep_audio_final_write_failure_rolls_back_everything(candidate_use: tuple[ShortDramaService, str, Take, str]) -> None:
    service, pid, take, clip_id = candidate_use
    before = service.get_project(pid)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER fail_picture_write BEFORE UPDATE ON projects BEGIN SELECT RAISE(ABORT, 'picture save failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="picture save failed"):
        service.select_take(take.id, clip_ids=[clip_id], expected_editorial_revision=read_editorial(service, pid).revision,
            source_in_seconds=4.5, source_out_seconds=10.045, audio_policy="KEEP_CURRENT_AUDIO")
    after = service.get_project(pid)
    assert after.document == before.document and after.takes == before.takes


def test_shared_group_picture_target_keeps_prefix_original_sound_and_other_shots(
    whole_group: tuple[ShortDramaService, str], tmp_path: Path,
) -> None:
    service, pid = whole_group
    before = read_editorial(service, pid)
    old = EditorProject.model_validate(before.project)
    original = old.timelines[0].clips[0]
    sid = "shot-yqzyd-02"
    target = H3PictureOnlyTarget(mode="PICTURE_ONLY", audio_policy="KEEP_CURRENT_AUDIO",
        timeline_id=old.activeTimelineId, clip_id=original.id, expected_editorial_revision=before.revision)
    binding = picture_only_binding(service.get_project(pid).document, sid, target, store=service.repository)
    clip = binding.picture["clip"]
    assert isinstance(clip, dict)
    assert (clip["startTime"], clip["duration"], clip["trimStart"]) == (6.5, 5.75, 6.5)
    verify_retained_audio_files(binding)
    source = next(t for t in service.repository.list_takes(pid) if t.id == (original.model_extra or {})["sourceTakeId"])
    media = tmp_path / "single-shot-picture.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=160x90:r=24:d=6", "-f", "lavfi", "-i", "sine=frequency=880:duration=6",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(media)], check=True, capture_output=True)
    candidate = source.model_copy(deep=True, update={"id": "one-picture", "task_id": "one-picture-task",
        "shot_id": sid, "shot_ids": [sid], "shot_windows": [], "media_path": str(media), "duration_seconds": 6.0,
        "is_selected": False, "selected_shot_ids": [], "qa_status": QaStatus.PENDING})
    source_task = service.repository.get_task(source.task_id)
    assert source_task is not None
    service.repository.save_task(source_task.model_copy(deep=True, update={"id": candidate.task_id,
        "shot_id": sid, "shot_ids": [sid], "requested_duration_seconds": 6.0}))
    service.repository.save_take(candidate)
    service.select_take(candidate.id, shot_ids=[sid], clip_ids=[original.id], expected_editorial_revision=before.revision,
        source_in_seconds=.25, source_out_seconds=6.0, audio_policy="KEEP_CURRENT_AUDIO")
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    timeline = after.timelines[0]
    target_clip = next(c for c in timeline.clips if c.id == original.id)
    assert (target_clip.startTime, target_clip.duration, target_clip.trimStart, target_clip.muted) == (6.5, 5.75, .25, True)
    prefix = next(c for c in timeline.clips if c.type == "video" and c.startTime == 0)
    assert (prefix.assetId, prefix.trimStart, prefix.duration) == (original.assetId, 0, 6.5)
    retained = next(c for c in timeline.clips if c.type == "audio")
    assert (retained.assetId, retained.trimStart, retained.startTime, retained.duration) == (original.assetId, 6.5, 6.5, 5.75)
    for other in old.timelines[0].clips[1:]:
        assert next(c for c in timeline.clips if c.id == other.id) == other
    assert timeline.subtitles == old.timelines[0].subtitles
    assert max(c.startTime + c.duration for c in timeline.clips) == max(c.startTime + c.duration for c in old.timelines[0].clips)
