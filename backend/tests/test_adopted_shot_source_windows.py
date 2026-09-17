"""Local edits retain observed source intervals already used in this occurrence."""
from __future__ import annotations

from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest

from short_drama.models import GenerationUnitShotWindow
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def adopted_uses(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> tuple[ShortDramaService, str, str, str]:
    service, pid, shots = episode
    target = shots[1]
    media = tmp_path / "observed-shared-source.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=24:d=10.13", "-f", "lavfi", "-i", "sine=frequency=880:duration=10.13",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(media)], check=True, capture_output=True)
    take = next(item for item in service.get_project(pid).takes if item.shot_id == target)
    take.media_path, take.duration_seconds = str(media), 10.13
    take.shot_id = shots[0]
    take.generation_unit_id = "observed-shared-h3-unit"
    take.shot_windows = [GenerationUnitShotWindow(
        shot_id=shot_id, shot_revision=1, source_in_seconds=start,
        source_out_seconds=end, duration_seconds=end - start,
    ) for shot_id, start, end in [(shots[0], 0, 5), (target, 5, 10.13)]]
    service.repository.save_take(take)
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    target_clips = [item for item in timeline.clips if (item.model_extra or {}).get("sourceShotId") == target]
    video = next(item for item in target_clips if item.type == "video")
    audio = next(item for item in target_clips if item.type == "audio")
    for clip in target_clips:
        clip.startTime, clip.trimStart, clip.duration = 4, 2, 25 / 24
        assert clip.asset is not None and clip.model_extra is not None
        clip.asset.path, clip.asset.duration = str(media), 10.13
        clip.model_extra.update(sourceWindowStart=5.0, sourceWindowEnd=10.13)
        editor.assets = [clip.asset if item.id == clip.assetId else item for item in editor.assets]
    for original, clip_id, linked_id in ((video, "handover-video", "handover-audio"), (audio, "handover-audio", "handover-video")):
        timeline.clips.append(original.model_copy(deep=True, update={
            "id": clip_id, "startTime": 4 + 25 / 24, "trimStart": 153 / 24,
            "duration": 55 / 24, "linkedClipIds": [linked_id],
        }))
    # The same shot and source appear again elsewhere. Its observed interval
    # must not become available to the first occurrence just because IDs match.
    for original, clip_id, linked_id in ((video, "later-video", "later-audio"), (audio, "later-audio", "later-video")):
        timeline.clips.append(original.model_copy(deep=True, update={
            "id": clip_id, "startTime": 20.0, "trimStart": 0.5,
            "duration": 0.5, "linkedClipIds": [linked_id],
        }))
    cue = next(item for item in timeline.subtitles if item.id == "cue-1")
    cue.startTime, cue.endTime = 4.2, 7.0
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    return service, pid, target, video.id


@pytest.mark.parametrize("audio_has_take_id", [True, False])
def test_sound_only_save_preserves_disjoint_adopted_video_ranges_and_reopens(
    adopted_uses: tuple[ShortDramaService, str, str, str], audio_has_take_id: bool,
) -> None:
    service, pid, shot_id, anchor = adopted_uses
    if not audio_has_take_id:
        current = read_editorial(service, pid)
        project = EditorProject.model_validate(current.project)
        for clip in project.timelines[0].clips:
            if clip.type == "audio" and (clip.model_extra or {}).get("sourceShotId") == shot_id:
                assert clip.model_extra is not None
                clip.model_extra.pop("sourceTakeId", None)
                clip.model_extra["linkedClipIds"] = []
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(project)}))
    before = service.get_project(pid)
    global_before = EditorProject.model_validate(read_editorial(service, pid).project)
    local = read_shot_editorial(service, pid, shot_id, anchor)
    editor = EditorProject.model_validate(local.project)
    for clip in editor.timelines[0].clips:
        clip.muted = clip.trimStart < 5
        clip.audioFadeInSeconds = clip.audioFadeOutSeconds = 0.02
    # Add existing independent room tone without changing either adopted video.
    roomtone = next(item for item in global_before.timelines[0].clips if item.type == "audio" and item.startTime == 0)
    assert roomtone.asset is not None
    editor.assets.append(roomtone.asset.model_copy(deep=True))
    editor.timelines[0].clips.append(roomtone.model_copy(deep=True, update={
        "id": "local-roomtone", "startTime": 0.0, "trimStart": 3.5,
        "duration": 25 / 24, "linkedClipIds": [],
    }))
    result = save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
        expected_revision=local.revision, anchor_clip_id=anchor, timeline={"project": as_json(editor)}))
    assert result.project.document.shots == before.document.shots
    after = read_editorial(service, pid)
    saved = EditorProject.model_validate(after.project)
    old_clips = {item.id: item for item in global_before.timelines[0].clips}
    local_ids = {item.id for item in editor.timelines[0].clips}
    for clip in saved.timelines[0].clips:
        if clip.id not in local_ids:
            assert clip == old_clips[clip.id]
        elif clip.id != "local-roomtone":
            original = old_clips[clip.id]
            assert (clip.startTime, clip.trimStart, clip.duration, clip.speed, clip.reversed) == (
                original.startTime, original.trimStart, original.duration, original.speed, original.reversed)
            assert clip.muted == (clip.trimStart < 5)
    assert {item.id: item for item in saved.timelines[0].subtitles} == {
        item.id: item for item in global_before.timelines[0].subtitles}
    assert service.get_project(pid).takes == before.takes
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert read_editorial(reopened, pid) == after
    projected = EditorProject.model_validate(read_shot_editorial(reopened, pid, shot_id, anchor).project)
    for clip in projected.timelines[0].clips:
        if clip.id == "local-roomtone":
            continue
        extra = clip.model_extra or {}
        assert (extra["sourceWindowStart"], extra["sourceWindowEnd"]) == pytest.approx(
            (2, 73 / 24) if clip.trimStart < 5 else (5, 10.13))


@pytest.mark.parametrize(("kind", "start", "duration", "message"), [
    ("video", 2.5, 3.0, "不能跨越区间间隙"),
    ("video", 3.5, 0.5, "本镜头的素材范围"),
    ("video", 0.5, 0.5, "本镜头的素材范围"),
    ("audio", 3.5, 0.5, "本镜头的素材范围"),
    ("video", 10.0, 0.5, "原素材时长"),
])
def test_server_rejects_gap_other_occurrence_and_source_end_with_edited_window_metadata(
    adopted_uses: tuple[ShortDramaService, str, str, str], kind: str, start: float, duration: float, message: str,
) -> None:
    service, pid, shot_id, anchor = adopted_uses
    before = service.get_project(pid)
    local = read_shot_editorial(service, pid, shot_id, anchor)
    editor = EditorProject.model_validate(local.project)
    clip = next(item for item in editor.timelines[0].clips if item.type == kind)
    clip.trimStart, clip.duration = start, duration
    assert clip.model_extra is not None
    clip.model_extra.update(sourceWindowStart=0, sourceWindowEnd=1000)
    with pytest.raises(ValueError, match=message):
        save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
            expected_revision=local.revision, anchor_clip_id=anchor, timeline={"project": as_json(editor)}))
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    after = reopened.get_project(pid)
    assert after.document == before.document and after.takes == before.takes


@pytest.mark.parametrize("operation", ["shorten", "split", "speed_reverse"])
def test_trim_split_and_speed_reverse_keep_each_native_audio_interval_inside_current_use(
    adopted_uses: tuple[ShortDramaService, str, str, str], operation: str,
) -> None:
    service, pid, shot_id, anchor = adopted_uses
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    local = read_shot_editorial(service, pid, shot_id, anchor)
    editor = EditorProject.model_validate(local.project)
    local_clips = editor.timelines[0].clips
    first = [item for item in local_clips if item.startTime == 0]
    assert {item.type for item in first} == {"video", "audio"}
    for clip in first:
        if operation == "shorten":
            clip.trimStart += 0.125
            clip.duration -= 0.25
        elif operation == "speed_reverse":
            clip.speed, clip.reversed = 2.0, True
            clip.duration /= 2
        else:
            part = clip.model_copy(deep=True, update={"id": "split-" + clip.id,
                "startTime": 0.5, "trimStart": 2.5, "duration": clip.duration - 0.5})
            assert part.model_extra is not None
            part.model_extra["linkedClipIds"] = ["split-" + item.id for item in first if item.id != clip.id]
            local_clips.append(part)
            clip.duration = 0.5
    save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
        expected_revision=local.revision, anchor_clip_id=anchor, timeline={"project": as_json(editor)}))
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    local_ids = {item.id for item in local_clips}
    for clip in before.timelines[0].clips:
        if clip.id not in local_ids:
            assert next(item for item in after.timelines[0].clips if item.id == clip.id) == clip
    for cue in before.timelines[0].subtitles:
        saved = next(item for item in after.timelines[0].subtitles if item.id == cue.id)
        if cue.id != "cue-1":
            assert saved == cue
        else:
            assert saved.text == cue.text and saved.style == cue.style
    projected = EditorProject.model_validate(read_shot_editorial(service, pid, shot_id, anchor).project)
    for clip in projected.timelines[0].clips:
        assert clip.trimStart >= float((clip.model_extra or {})["sourceWindowStart"])
        assert clip.trimStart + clip.duration * clip.speed <= float((clip.model_extra or {})["sourceWindowEnd"]) + 0.001


@pytest.mark.parametrize("overlap", [0.0, 0.125], ids=["adjacent", "overlapping"])
def test_adopted_and_nominal_intervals_merge_only_when_they_touch(
    adopted_uses: tuple[ShortDramaService, str, str, str], overlap: float,
) -> None:
    service, pid, shot_id, anchor = adopted_uses
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    delta = 3 + overlap - 25 / 24
    for clip in editor.timelines[0].clips:
        if (clip.model_extra or {}).get("sourceShotId") == shot_id and clip.startTime == 4:
            clip.duration = 3 + overlap
        elif clip.startTime > 4:
            clip.startTime += delta
    for cue in editor.timelines[0].subtitles:
        if cue.startTime > 4:
            cue.startTime += delta
            cue.endTime += delta
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    local = read_shot_editorial(service, pid, shot_id, anchor)
    projected = EditorProject.model_validate(local.project)
    assert {(item.model_extra or {}).get("sourceWindowStart") for item in projected.timelines[0].clips} == {2}
    assert {(item.model_extra or {}).get("sourceWindowEnd") for item in projected.timelines[0].clips} == {10.13}
    save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
        expected_revision=local.revision, anchor_clip_id=anchor, timeline={"project": as_json(projected)}))


def test_nominal_window_remains_available_and_other_occurrence_has_its_own_range(
    adopted_uses: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, shot_id, anchor = adopted_uses
    later = read_shot_editorial(service, pid, shot_id, "later-video")
    assert later.scope and len(later.scope.occurrences) == 2
    for clip in EditorProject.model_validate(later.project).timelines[0].clips:
        assert ((clip.model_extra or {})["sourceWindowStart"], (clip.model_extra or {})["sourceWindowEnd"]) == (0.5, 1.0)
    local = read_shot_editorial(service, pid, shot_id, anchor)
    editor = EditorProject.model_validate(local.project)
    first = next(item for item in editor.timelines[0].clips if item.type == "video")
    assert ((first.model_extra or {})["sourceWindowStart"], (first.model_extra or {})["sourceWindowEnd"]) == pytest.approx((2, 73 / 24))
    first.trimStart, first.duration = 5.25, 0.5
    save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
        expected_revision=local.revision, anchor_clip_id=anchor, timeline={"project": as_json(editor)}))
    assert read_shot_editorial(service, pid, shot_id, "later-video").project == later.project
