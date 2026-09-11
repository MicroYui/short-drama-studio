"""Crossing subtitle edits preserve nullable intent and unique current cue IDs."""
from pathlib import Path

import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
from tests.test_shot_editorial_crossing_subtitles import cut_episode as cut_episode
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.mark.parametrize("part", [0, 1])
def test_nullable_time_fields_do_not_cut_unseen_subtitle_edges(
    cut_episode: tuple[ShortDramaService, str, list[str]], part: int,
) -> None:
    service, pid, ids = cut_episode
    before = read_editorial(service, pid)
    baseline = EditorProject.model_validate(before.project).timelines[0].subtitles
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "shot_id": ids[part], "subtitles": [{
            "id": "cue-0", "text": "只修改字幕文字", "start_seconds": None, "end_seconds": None}]}})
    assert result.error is None, result.model_dump_json()
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].subtitles
    expected = [cue.model_copy(deep=True, update={"text": "只修改字幕文字"}) if cue.id == "cue-0" else cue
                for cue in baseline]
    assert sorted(after, key=lambda cue: cue.id) == sorted(expected, key=lambda cue: cue.id)


def test_second_disconnected_edit_preserves_existing_split_cue_and_exports_both(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, ids = episode
    local = read_shot_editorial(service, pid, ids[0])
    editor = EditorProject.model_validate(local.project)
    timeline = editor.timelines[0]
    originals = list(timeline.clips)
    tail_ids = {clip.id: f"{clip.id}-tail" for clip in originals}
    for clip in originals:
        tail = clip.model_copy(deep=True, update={"id": tail_ids[clip.id], "startTime": 2, "trimStart": 2,
            "duration": 2, "linkedClipIds": [tail_ids.get(other, other)
                for other in (clip.model_extra or {}).get("linkedClipIds", [])]})
        clip.duration = 1
        timeline.clips.append(tail)
    save_shot_editorial(service, pid, ids[0], StudioTimelineSave(expected_revision=local.revision,
        timeline={"project": as_json(editor)}))
    first = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    old_split = next(cue for cue in first.subtitles if cue.id == "cue-0-part-1")
    assert (old_split.startTime, old_split.endTime) == (2, 3.5)

    local = read_shot_editorial(service, pid, ids[0], originals[0].id)
    editor = EditorProject.model_validate(local.project)
    timeline = editor.timelines[0]
    originals = list(timeline.clips)
    repeat_ids = {clip.id: f"{clip.id}-repeat" for clip in originals}
    for clip in originals:
        timeline.clips.append(clip.model_copy(deep=True, update={"id": repeat_ids[clip.id], "startTime": 2,
            "linkedClipIds": [repeat_ids.get(other, other) for other in (clip.model_extra or {}).get("linkedClipIds", [])]}))
    save_shot_editorial(service, pid, ids[0], StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=originals[0].id, timeline={"project": as_json(editor)}))
    current = read_editorial(service, pid)
    saved = EditorProject.model_validate(current.project).timelines[0]
    assert len({cue.id for cue in saved.subtitles}) == len(saved.subtitles) == 5
    preserved = next(cue for cue in saved.subtitles if cue.id == old_split.id)
    assert (preserved.startTime, preserved.endTime) == (4, 5.5)
    new = next(cue for cue in saved.subtitles if cue.startTime == 2.75)
    assert new.id != old_split.id and new.endTime == 3

    # Reopening just the repeated position must not pull in the unrelated old
    # split cue or produce a clipped end time before its start.
    repeated = read_shot_editorial(service, pid, ids[0], repeat_ids[originals[0].id])
    reopened = EditorProject.model_validate(repeated.project)
    assert [(cue.id, cue.startTime, cue.endTime) for cue in reopened.timelines[0].subtitles] == [(new.id, .75, 1)]
    save_shot_editorial(service, pid, ids[0], StudioTimelineSave(expected_revision=current.revision,
        anchor_clip_id=repeat_ids[originals[0].id], timeline={"project": as_json(reopened)}))
    current = read_editorial(service, pid)
    assert EditorProject.model_validate(current.project).timelines[0].subtitles == saved.subtitles
    exported = start_export(service, pid, StudioExportRequest(expected_revision=current.revision, width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.srt_path is not None
    subtitles = Path(exported.srt_path).read_text(encoding="utf-8")
    assert "00:00:02,750 --> 00:00:03,000" in subtitles
    assert "00:00:04,000 --> 00:00:05,500" in subtitles


def test_editorial_rejects_duplicate_subtitle_ids_before_saving(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _ids = episode
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    timeline = editor.timelines[0]
    timeline.subtitles.append(timeline.subtitles[0].model_copy(deep=True, update={"startTime": 4, "endTime": 5}))
    with pytest.raises(ValueError, match="字幕标识重复"):
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=before.revision, timeline={"project": as_json(editor)}))
    assert read_editorial(service, pid) == before
