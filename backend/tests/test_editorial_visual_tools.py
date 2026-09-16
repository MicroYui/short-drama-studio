"""Agent title/transition edits reach the same local rendering contract as the editor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import imageio_ffmpeg
import numpy as np
from pydantic import JsonValue
import pytest

from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave
from tests.test_studio_shot_editorial import as_json, episode as episode
from tests.test_editorial_timeline_target import two_timelines as two_timelines


def _title(identity: str = "new-title", **changes: JsonValue) -> dict[str, JsonValue]:
    return {"id": identity, "text": "借路 · 第二集", "start_seconds": .5, "duration_seconds": 1.5,
            "style": {"font_size": 108, "color": "#FFFF00", "position_x": 60}, **changes}


def test_title_create_edit_read_remove_preserves_other_timeline_and_existing_rows(
    two_timelines: tuple[ShortDramaService, str, list[str], str, str],
) -> None:
    service, pid, _shots, active_id, target_id = two_timelines
    before = read_editorial(service, pid)
    baseline = EditorProject.model_validate(before.project)
    created = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "timeline_id": target_id, "add_titles": [_title()]}})
    assert created.error is None, created.model_dump_json()
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    assert editor.activeTimelineId == active_id and editor.timelines[0] == baseline.timelines[0]
    assert editor.assets == baseline.assets
    assert editor.timelines[1].clips[:-1] == baseline.timelines[1].clips
    assert editor.timelines[1].subtitles == baseline.timelines[1].subtitles
    title = editor.timelines[1].clips[-1]
    assert title.type == "text" and title.assetId is None and title.duration == 1.5
    old_style = cast(dict[str, JsonValue], (title.model_extra or {})["textStyle"])
    changed = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": current.revision, "timeline_id": target_id,
        "clips": [{"id": title.id, "text": "借路\n片尾", "title_style": {"color": "#FFAA00"}}]}})
    assert changed.error is None, changed.model_dump_json()
    saved = read_editorial(service, pid)
    new_title = EditorProject.model_validate(saved.project).timelines[1].clips[-1]
    assert (new_title.model_extra or {})["textStyle"] == {**old_style, "text": "借路\n片尾", "color": "#FFAA00"}
    for selection in ({"clip_ids": [title.id]}, {}):
        read = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, "timeline_id": target_id, **selection})
        assert read.output is not None, read.model_dump_json()
        row = next(item for item in cast(list[dict[str, JsonValue]], cast(dict[str, JsonValue], read.output["content"])["clips"]) if item["id"] == title.id)
        assert row["text"] == "借路\n片尾" and cast(dict[str, JsonValue], row["title_style"])["font_size"] == 108
    removed = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": saved.revision, "timeline_id": target_id, "clips": [{"id": title.id, "remove": True}]}})
    assert removed.error is None, removed.model_dump_json()
    final = EditorProject.model_validate(read_editorial(service, pid).project)
    assert final.timelines[1].clips == baseline.timelines[1].clips and final.timelines[0] == baseline.timelines[0]
    assert removed.output and cast(dict[str, JsonValue], removed.output["content"])["removed_clip_ids"] == [title.id]


@pytest.mark.parametrize("operation", ["clear", "replace", "remove"])
def test_dissolve_updates_both_ends_and_cleans_old_partner_without_touching_audio(
    episode: tuple[ShortDramaService, str, list[str]], operation: str,
) -> None:
    service, pid, _shots = episode
    baseline = EditorProject.model_validate(read_editorial(service, pid).project)
    videos = [clip for clip in baseline.timelines[0].clips if clip.type == "video"]
    a, b = videos[:2]
    created = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": 1, "clips": [{"id": a.id, "transition_out": {"type": "dissolve", "duration_seconds": .4}}]}})
    assert created.error is None, created.model_dump_json()
    assert created.output
    rows = cast(list[dict[str, JsonValue]], cast(dict[str, JsonValue], created.output["content"])["changed_clips"])
    assert {row["id"] for row in rows} == {a.id, b.id}
    assert rows[0]["transition_out"] == rows[1]["transition_in"] == {"type": "dissolve", "duration_seconds": .4}
    created_editor = EditorProject.model_validate(read_editorial(service, pid).project)
    for clip in baseline.timelines[0].clips:
        if clip.id not in {a.id, b.id}:
            assert next(item for item in created_editor.timelines[0].clips if item.id == clip.id) == clip
    change: dict[str, JsonValue] = {"id": a.id, "remove": True} if operation == "remove" else {"id": b.id,
        "transition_in": {"type": "none", "duration_seconds": 0} if operation == "clear" else {"type": "fade-to-black", "duration_seconds": .25}}
    changed = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": 2, "clips": [change]}})
    assert changed.error is None, changed.model_dump_json()
    clips = {clip.id: clip for clip in EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].clips}
    if operation == "remove":
        assert a.id not in clips and (clips[b.id].model_extra or {})["transitionIn"] == {"type": "none", "duration": 0}
    else:
        assert (clips[a.id].model_extra or {})["transitionOut"] == {"type": "none", "duration": 0}
    for clip in baseline.timelines[0].clips:
        if clip.type == "audio" and clip.id in clips:
            assert clips[clip.id] == clip


def test_local_title_uses_actual_selected_shot_offset_and_cannot_extend_it(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, shots = episode
    before = read_editorial(service, pid)
    baseline = EditorProject.model_validate(before.project)
    anchor = next(clip.id for clip in baseline.timelines[0].clips if clip.type == "video" and clip.startTime == 4)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "shot_id": shots[1], "anchor_clip_id": anchor, "add_titles": [_title()]}})
    assert result.error is None, result.model_dump_json()
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    title = next(clip for clip in editor.timelines[0].clips if clip.type == "text")
    assert (title.startTime, title.duration) == (4.5, 1.5) and (title.model_extra or {})["sourceShotId"] == shots[1]
    for old in baseline.timelines[0].clips:
        if old.startTime != 4:
            assert next(clip for clip in editor.timelines[0].clips if clip.id == old.id) == old
    for patch in ({"add_titles": [_title("crossing", start_seconds=3, duration_seconds=2)]},
                  {"clips": [{"id": title.id, "duration_seconds": 5}]}):
        failed = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
            "expected_revision": current.revision, "shot_id": shots[1], "anchor_clip_id": anchor, **patch}})
        assert failed.error and "SHOT_LOCAL" in failed.error.message
        assert read_editorial(service, pid) == current


def test_invalid_visual_requests_and_stale_revision_do_not_partially_save(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _shots = episode
    before = read_editorial(service, pid)
    clips = [clip for clip in EditorProject.model_validate(before.project).timelines[0].clips if clip.type == "video"]
    bad: list[dict[str, JsonValue]] = [
        {"add_titles": [_title(clips[0].id)]}, {"add_titles": [_title(track_index=0)]},
        {"add_titles": [_title(text="unsupported\\tag")]}, {"add_titles": [_title(style={"font_size": 501})]},
        {"clips": [{"id": clips[0].id, "text": "不是标题"}]},
        {"clips": [{"id": clips[-1].id, "transition_out": {"type": "dissolve", "duration_seconds": .4}}]},
        {"clips": [{"id": clips[0].id, "transition_out": {"type": "fade-to-black", "duration_seconds": 5}}]},
        {"clips": [{"id": clips[0].id, "transition_out": {"type": "dissolve", "duration_seconds": .4}},
                   {"id": clips[1].id, "transition_in": {"type": "fade-to-black", "duration_seconds": .4}}]},
    ]
    for patch in bad:
        result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
            "expected_revision": before.revision, **patch}})
        assert result.error, result.model_dump_json()
        assert read_editorial(service, pid) == before
    human = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "subtitles": [{"id": "cue-1", "text": "用户刚保存的字幕"}]}})
    assert human.error is None
    saved = read_editorial(service, pid)
    stale = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "add_titles": [_title()]}})
    assert stale.error and stale.error.kind == "StudioContentConflict"
    assert read_editorial(service, pid) == saved


def test_tool_created_chinese_title_black_fades_and_dissolve_render_at_25fps(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    for color, shot in zip(("red", "blue"), document.shots[:2], strict=True):
        path = tmp_path / f"{color}.mp4"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=480x270:r=25:d=3", "-c:v", "libx264", str(path)], check=True, capture_output=True)
        imported = import_studio_media(service, pid, path, name=color, kind=AssetKind.REFERENCE, asset_id=None)
        version = imported.document.assets[-1].versions[-1]
        select_imported_video(service, pid, shot.id, StudioShotMediaSelect(asset_version_id=version.id, expected_revision=shot.revision))
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    timeline.subtitles = []
    assert len(timeline.clips) == 2
    for index, clip in enumerate(timeline.clips):
        clip.startTime, clip.duration = index * 2, 2
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    baseline = read_editorial(service, pid)
    a, b = timeline.clips
    edited = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": baseline.revision, "timeline_id": timeline.id,
        "add_titles": [_title(start_seconds=13 / 25, duration_seconds=37 / 25, style={"font_size": 144, "color": "#FFFF00"})],
        "clips": [{"id": a.id, "transition_in": {"type": "fade-to-black", "duration_seconds": .4},
                   "transition_out": {"type": "dissolve", "duration_seconds": .4}},
                  {"id": b.id, "transition_out": {"type": "fade-to-black", "duration_seconds": .4}}]}})
    assert edited.error is None, edited.model_dump_json()
    saved = read_editorial(service, pid)
    output = service.tool_registry.invoke("timeline.export_editorial", {"project_id": pid,
        "request": {"expected_revision": saved.revision, "width": 480, "height": 270, "fps": 25, "subtitles": False}})
    assert output.output and output.error is None, output.model_dump_json()
    path = Path(str(output.output["video_path"]))
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], check=True, capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape((-1, 270, 480, 3))
    assert len(frames) == 100
    assert max(frames[0, 5, 5]) < 10
    assert frames[25, 5, 5, 0] > 240 and frames[25, 5, 5, 2] < 10
    assert 105 < frames[45, 5, 5, 0] < 150 and 105 < frames[45, 5, 5, 2] < 150
    assert frames[50, 5, 5, 2] > 240 and 10 < frames[99, 5, 5, 2] < 45
    yellow = [int(np.count_nonzero((frame[:, :, 0] > 150) & (frame[:, :, 1] > 150) & (frame[:, :, 2] < 80))) for frame in frames]
    assert yellow[12] == yellow[50] == 0 and yellow[13] > 150 and yellow[49] > 150
    probe = json.loads(path.with_name("technical-check.json").read_text())
    assert probe["media"]["fps"] == "25" and all(probe["checks"].values())
    assert read_editorial(service, pid) == saved
    if destination := os.environ.get("STUDIO_VISUAL_TOOL_TEST_OUTPUT_DIR"):
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, folder / "agent-title-transitions-25fps.mp4")
        (folder / "acceptance.json").write_text(json.dumps({"frames": 100, "fps": 25, "title_start_frame": 13,
            "title_end_exclusive": 50, "yellow_pixels": {str(i): yellow[i] for i in (12, 13, 49, 50)},
            "dissolve_midpoint_rgb": frames[45, 5, 5].tolist(), "technical": probe,
            "edit_receipt": edited.model_dump(mode="json")}, ensure_ascii=False, indent=2))
