"""Actual captions can be created through a scoped partial tool operation."""

from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from tests.test_studio_shot_editorial import as_json, episode as episode
from tests.test_editorial_timeline_target import two_timelines as two_timelines


def _caption(identity: str = "heard-line", **changes: JsonValue) -> dict[str, JsonValue]:
    return {"id": identity, "text": "实际听见的这一句。", "start_seconds": .25, "end_seconds": 1.75, **changes}


def test_first_captions_create_missing_track_and_export_without_project_payload(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _shots = episode
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    timeline.subtitles = []
    timeline.tracks = timeline.tracks[:3]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    before = read_editorial(service, pid)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "timeline_id": timeline.id,
        "add_subtitles": [_caption(style={"fontSize": 40, "color": "#ffcc00"})]}})
    assert result.error is None, result.model_dump_json()
    after = read_editorial(service, pid)
    saved = EditorProject.model_validate(after.project)
    assert saved.assets == editor.assets
    assert saved.timelines[0].clips == timeline.clips
    assert saved.timelines[0].tracks[:3] == timeline.tracks
    cue = saved.timelines[0].subtitles[0]
    assert (cue.startTime, cue.endTime, cue.text, cue.trackIndex) == (.25, 1.75, "实际听见的这一句。", 3)
    assert saved.timelines[0].tracks[cue.trackIndex].type == "subtitle"
    assert cue.style == {"fontSize": 40, "color": "#ffcc00"}
    assert result.output is not None
    content = cast(dict[str, JsonValue], result.output["content"])
    assert content["created_subtitle_ids"] == [cue.id]
    assert content["changed_subtitles"] == [cue.model_dump(mode="json")]
    assert "project" not in content and result.artifact_refs[0].version == after.revision
    export = start_export(service, pid, StudioExportRequest(expected_revision=after.revision, width=480, height=270), background=False)
    assert export.status == "completed", export.error
    assert export.video_path and Path(export.video_path).is_file()
    assert export.input_path
    srt = Path(export.input_path).with_name("subtitles.srt").read_text()
    assert "00:00:00,250 --> 00:00:01,750" in srt and cue.text in srt


def test_add_edit_and_remove_share_one_revision_and_preserve_inactive_content(
    two_timelines: tuple[ShortDramaService, str, list[str], str, str],
) -> None:
    service, pid, _shots, active, target_id = two_timelines
    current = read_editorial(service, pid)
    before = EditorProject.model_validate(current.project)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": current.revision, "timeline_id": target_id,
        "add_subtitles": [_caption(start_seconds=34.5, end_seconds=36, track_index=3)],
        "subtitles": [{"id": "cue-0", "text": "校正已有识别"}, {"id": "cue-2", "remove": True}]}})
    assert result.error is None, result.model_dump_json()
    after = read_editorial(service, pid)
    editor = EditorProject.model_validate(after.project)
    assert after.revision == current.revision + 1
    assert editor.activeTimelineId == active and editor.timelines[0] == before.timelines[0]
    target = editor.timelines[1]
    assert target.clips == before.timelines[1].clips and target.tracks == before.timelines[1].tracks
    assert [cue.id for cue in target.subtitles] == ["cue-0", "cue-1", "heard-line"]
    assert target.subtitles[0].text == "校正已有识别"
    assert target.subtitles[1] == before.timelines[1].subtitles[1]
    assert result.artifact_refs[0].artifact_id == target_id


def test_new_local_cue_uses_selected_occurrence_and_keeps_other_adoptions(
    two_timelines: tuple[ShortDramaService, str, list[str], str, str],
) -> None:
    service, pid, shots, active, target_id = two_timelines
    current = read_editorial(service, pid)
    before = EditorProject.model_validate(current.project)
    anchor = next(clip.id for clip in before.timelines[1].clips if clip.type == "video" and clip.startTime == 34)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": current.revision, "timeline_id": target_id,
        "shot_id": shots[1], "anchor_clip_id": anchor, "add_subtitles": [_caption()]}})
    assert result.error is None, result.model_dump_json()
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    assert editor.activeTimelineId == active and editor.timelines[0] == before.timelines[0]
    # Local projection records source-window editing bounds; media geometry and
    # all previously saved fields remain identical despite the merge's order.
    after_clips = {clip.id: clip for clip in editor.timelines[1].clips}
    for clip in before.timelines[1].clips:
        expected = clip.model_dump(mode="json")
        actual = after_clips[clip.id].model_dump(mode="json")
        assert {key: actual[key] for key in expected} == expected
    assert editor.timelines[1].tracks == before.timelines[1].tracks
    existing = {cue.id: cue for cue in editor.timelines[1].subtitles if cue.id != "heard-line"}
    assert existing == {cue.id: cue for cue in before.timelines[1].subtitles}
    cue = next(cue for cue in editor.timelines[1].subtitles if cue.id == "heard-line")
    assert (cue.startTime, cue.endTime) == (34.25, 35.75)
    assert (cue.model_extra or {}).get("sourceShotId") == shots[1]
    assert result.output is not None
    changed = cast(list[dict[str, JsonValue]], cast(dict[str, JsonValue], result.output["content"])["changed_subtitles"])
    assert changed[0]["startTime"] == 34.25


@pytest.mark.parametrize("changes,fragment", [
    ({"add_subtitles": [_caption("cue-0")]}, "already exists"),
    ({"add_subtitles": [_caption(), _caption()]}, "IDs must be unique"),
    ({"add_subtitles": [_caption()], "subtitles": [{"id": "heard-line", "remove": True}]}, "both added and edited"),
    ({"add_subtitles": [_caption(end_seconds=.2)]}, "end_seconds must be after"),
    ({"add_subtitles": [_caption(track_index=1)]}, "not a current subtitle track"),
    ({"add_subtitles": [_caption()], "expected_revision": 0}, "剪辑已更新"),
])
def test_invalid_add_is_atomic_and_leaves_current_content(
    episode: tuple[ShortDramaService, str, list[str]], changes: dict[str, JsonValue], fragment: str,
) -> None:
    service, pid, _shots = episode
    before = read_editorial(service, pid)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": before.revision, "subtitles": [{"id": "cue-1", "text": "不能部分写入"}], **changes}})
    assert result.error and fragment in result.error.message
    assert read_editorial(service, pid) == before


def test_local_new_cue_cannot_be_silently_clipped_or_steal_another_shots_id(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, shots = episode
    before = read_editorial(service, pid)
    for cue, fragment in ((_caption(end_seconds=4.25), "exceeds the edited SHOT_LOCAL duration"),
                          (_caption("cue-0"), "其他镜头字幕")):
        result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
            "expected_revision": before.revision, "shot_id": shots[1], "add_subtitles": [cue]}})
        assert result.error and fragment in result.error.message
        assert read_editorial(service, pid) == before


def test_post_actual_prompt_and_tool_expose_transcript_creation_and_export_limits(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, _pid, _shots = episode
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(ProductionSkillRole.DIRECTOR,),
        stage=ProductionPromptStage.POST, method_skill_ids=("method.drama_series",))
    for phrase in ("audio.transcribe_native", "add_subtitles", "audio.align_subtitles", "不补成", "add_titles", "title_style", "transition_in/transition_out", "EDITORIAL"):
        assert phrase in prompt.text
    create = service.tool_registry.describe("timeline.edit_clips")
    assert create.version == 5 and "add_subtitles" in create.description and "add_titles" in create.description
    export = service.tool_registry.describe("timeline.export_editorial")
    assert "PNG" in export.description and "标题" in export.description and "溶解" in export.description
    for text in (prompt.text, export.description):
        assert "text/adjustment" not in text
        assert "非 none 的 transitionIn/transitionOut 尚未接通" not in text
