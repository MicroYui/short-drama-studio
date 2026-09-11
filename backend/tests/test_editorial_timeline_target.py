"""Explicit editor targets survive active-timeline changes and duplicate IDs."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_models import StudioTimelineSave
from short_drama.tools.contracts import ToolCallStatus
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def two_timelines(episode: tuple[ShortDramaService, str, list[str]]) -> tuple[ShortDramaService, str, list[str], str, str]:
    service, pid, shots = episode
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    second = editor.timelines[0].model_copy(deep=True, update={"id": "film-b", "name": "Second cut"})
    # Each timeline legitimately retains its own copy of the same object IDs.
    for clip in second.clips:
        clip.startTime += 30
    for cue in second.subtitles:
        cue.startTime += 30
        cue.endTime += 30
    editor.timelines.append(second)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    return service, pid, shots, editor.activeTimelineId, second.id


def test_explicit_read_to_edit_targets_inactive_timeline_and_receipt(two_timelines: tuple[ShortDramaService, str, list[str], str, str]) -> None:
    service, pid, _shots, first_id, target_id = two_timelines
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    video = next(clip for clip in editor.timelines[1].clips if clip.type == "video" and clip.startTime == 34)
    read = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, "timeline_id": target_id,
        "subtitle_ids": ["cue-1"], "clip_ids": [video.id]})
    assert read.output is not None and read.error is None
    content = cast(dict[str, JsonValue], read.output["content"])
    target = cast(dict[str, JsonValue], content["edit_arguments"])
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {**target,
        "subtitles": [{"id": "cue-1", "start_seconds": 34.65, "end_seconds": 37.4}],
        "clips": [{"id": video.id, "volume": .6}]}})
    assert result.error is None, result.model_dump_json()
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    assert after.activeTimelineId == first_id and after.timelines[0] == editor.timelines[0]
    expected = editor.timelines[1].model_copy(deep=True)
    next(cue for cue in expected.subtitles if cue.id == "cue-1").startTime = 34.65
    next(cue for cue in expected.subtitles if cue.id == "cue-1").endTime = 37.4
    next(clip for clip in expected.clips if clip.id == "linked-1").volume = .6
    assert after.timelines[1] == expected
    assert result.artifact_refs[0].artifact_id == target_id
    assert result.receipt and result.receipt.current_state["content.timeline_id"] == target_id
    assert result.output is not None
    saved = cast(dict[str, JsonValue], result.output["content"])
    changed = cast(list[dict[str, JsonValue]], saved["changed_subtitles"])
    assert changed[0]["startTime"] == 34.65


def test_active_switch_requires_fresh_revision_then_preserves_original_target(two_timelines: tuple[ShortDramaService, str, list[str], str, str]) -> None:
    service, pid, _shots, target_id, now_active = two_timelines
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    editor.activeTimelineId = now_active
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=before.revision, timeline={"project": as_json(editor)}))
    switched = read_editorial(service, pid)
    stale = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": target_id, "expected_revision": before.revision,
        "subtitles": [{"id": "cue-1", "start_seconds": 4.65}]}})
    assert stale.error and stale.error.kind == "StudioContentConflict"
    assert read_editorial(service, pid) == switched
    fresh = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": target_id, "expected_revision": switched.revision,
        "subtitles": [{"id": "cue-1", "start_seconds": 4.65}]}})
    assert fresh.error is None, fresh.model_dump_json()
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    assert after.activeTimelineId == now_active and after.timelines[1] == editor.timelines[1]
    assert next(cue for cue in after.timelines[0].subtitles if cue.id == "cue-1").startTime == 4.65


@pytest.mark.parametrize("field", ["clips", "subtitles"])
def test_missing_timeline_id_rejects_duplicate_object_identity(two_timelines: tuple[ShortDramaService, str, list[str], str, str], field: str) -> None:
    service, pid, _shots, _first, _second = two_timelines
    before = read_editorial(service, pid)
    identity = "cue-1" if field == "subtitles" else EditorProject.model_validate(before.project).timelines[0].clips[0].id
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, field: [{"id": identity, "start_seconds": .25}]}})
    assert result.error and "/timeline_id is required" in result.error.message and identity in result.error.message
    assert read_editorial(service, pid) == before
    selector = "subtitle_ids" if field == "subtitles" else "clip_ids"
    read = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, selector: [identity]})
    assert read.error and "/timeline_id is required" in read.error.message and identity in read.error.message


def test_unknown_timeline_and_foreign_cue_never_fall_back_to_active(two_timelines: tuple[ShortDramaService, str, list[str], str, str]) -> None:
    service, pid, _shots, _first, second = two_timelines
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    editor.timelines[0].subtitles.append(EditorSubtitle(id="only-in-a", text="A only", startTime=1, endTime=2, trackIndex=3))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=before.revision, timeline={"project": as_json(editor)}))
    current = read_editorial(service, pid)
    for target, cue in (("deleted-timeline", "cue-1"), (second, "only-in-a")):
        result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
            "timeline_id": target, "expected_revision": current.revision,
            "subtitles": [{"id": cue, "start_seconds": .5}]}})
        assert result.status == ToolCallStatus.FAILED and result.error is not None
        assert target in result.error.message
        assert read_editorial(service, pid) == current


def test_local_shot_edit_uses_explicit_timeline_and_keeps_other_copy(two_timelines: tuple[ShortDramaService, str, list[str], str, str]) -> None:
    service, pid, shots, active, target_id = two_timelines
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    anchor = next(clip.id for clip in editor.timelines[1].clips if clip.type == "video" and clip.startTime == 34)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": target_id, "expected_revision": before.revision, "shot_id": shots[1], "anchor_clip_id": anchor,
        "subtitles": [{"id": "cue-1", "start_seconds": .5, "text": "B局部修改"}]}})
    assert result.error is None, result.model_dump_json()
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    assert after.activeTimelineId == active and after.timelines[0] == editor.timelines[0]
    cue = next(cue for cue in after.timelines[1].subtitles if cue.id == "cue-1")
    assert (cue.startTime, cue.endTime, cue.text) == (34.5, 37.5, "B局部修改")
    assert result.artifact_refs[0].artifact_id == target_id
    saved = read_editorial(service, pid)
    mismatch = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": target_id, "expected_revision": saved.revision, "shot_id": shots[0], "anchor_clip_id": anchor,
        "subtitles": [{"id": "cue-1", "text": "不能写入"}]}})
    assert mismatch.error and "使用位置已变化" in mismatch.error.message
    assert read_editorial(service, pid) == saved


def test_deleted_selected_timeline_is_not_replaced_by_current_active(two_timelines: tuple[ShortDramaService, str, list[str], str, str]) -> None:
    service, pid, _shots, active, deleted = two_timelines
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    editor.timelines = [timeline for timeline in editor.timelines if timeline.id != deleted]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=before.revision, timeline={"project": as_json(editor)}))
    current = read_editorial(service, pid)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": deleted, "expected_revision": current.revision,
        "subtitles": [{"id": "cue-1", "text": "不能改到A"}]}})
    assert result.error and deleted in result.error.message
    assert read_editorial(service, pid) == current
    assert EditorProject.model_validate(current.project).activeTimelineId == active


def test_unambiguous_legacy_edit_keeps_active_default(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, _shots = episode
    current = read_editorial(service, pid)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": current.revision, "subtitles": [{"id": "cue-1", "text": "旧调用仍可用"}]}})
    assert result.error is None, result.model_dump_json()
    assert next(cue for cue in EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].subtitles if cue.id == "cue-1").text == "旧调用仍可用"
