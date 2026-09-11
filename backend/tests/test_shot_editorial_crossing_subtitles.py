"""One subtitle remains current and editable on both sides of a shot cut."""

from pydantic import JsonValue
import pytest

from short_drama.models import DirectorRepairDecision, DirectorRepairStrategy
from short_drama.reference_planner import DirectorRepairDecisionRequest, ReplacementShotDraft
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_models import StudioTimelineSave
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def cut_episode(episode: tuple[ShortDramaService, str, list[str]]) -> tuple[ShortDramaService, str, list[str]]:
    service, pid, ids = episode
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    timeline.tracks.append(timeline.tracks[3].model_copy(deep=True, update={"id": "parallel-subtitles"}))
    timeline.subtitles[0].style = {"fontSize": 35, "color": "#ffeeaa"}
    timeline.subtitles.append(EditorSubtitle(id="parallel-cue", text="另一字幕轨", startTime=0.8, endTime=3.2,
                                           trackIndex=len(timeline.tracks)-1, style={"position": "top"}))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    shot = next(item for item in service.get_project(pid).document.shots if item.id == ids[0])
    parts = [ReplacementShotDraft(id=f"{shot.id}-{index}", duration_seconds=shot.duration_seconds/2,
        narrative_purpose=shot.narrative_purpose, visual=shot.visual, framing=shot.framing, camera=shot.camera,
        audio_intent=shot.audio_intent, dialogue_line_ids=shot.dialogue_line_ids if index == 1 else [],
        continuity=shot.continuity, transition_out=shot.transition_out, local_rework_risk=shot.local_rework_risk) for index in (1, 2)]
    service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(decision=DirectorRepairDecision.APPLIED,
        strategy=DirectorRepairStrategy.SPLIT_SHOT, reason="分别呈现动作和反应", split_shots=parts))
    return service, pid, [part.id for part in parts]


def _cues(service: ShortDramaService, pid: str) -> dict[str, EditorSubtitle]:
    return {cue.id: cue for cue in EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].subtitles}


def test_crossing_subtitle_is_visible_in_both_shots_and_roundtrip_preserves_global_cues(cut_episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = cut_episode
    baseline = _cues(service, pid)
    assert (baseline["cue-0"].startTime, baseline["cue-0"].endTime) == (0.75, 3.5)
    assert (baseline["cue-0"].model_extra or {}).get("sourceShotId") in ids
    for shot_id, bounds in zip(ids, [(0.75, 2), (0, 1.5)], strict=True):
        local = read_shot_editorial(service, pid, shot_id)
        editor = EditorProject.model_validate(local.project)
        cues = {cue.id: cue for cue in editor.timelines[0].subtitles}
        assert set(cues) == {"cue-0", "parallel-cue"}
        assert (cues["cue-0"].startTime, cues["cue-0"].endTime) == bounds
        save_shot_editorial(service, pid, shot_id, StudioTimelineSave(expected_revision=local.revision, timeline={"project": as_json(editor)}))
        assert _cues(service, pid) == baseline


@pytest.mark.parametrize(("part", "change", "expected"), [
    (0, {"text": "修改这一句"}, (0.75, 3.5)), (1, {"text": "修改这一句"}, (0.75, 3.5)),
    (0, {"end_seconds": 1.8}, (0.75, 1.8)), (1, {"start_seconds": 0.2}, (2.2, 3.5)),
    # Exact patch intent still applies when a value equals the clipped display.
    (0, {"end_seconds": 2.0}, (0.75, 2.0)), (1, {"start_seconds": 0.0}, (2.0, 3.5)),
    (1, {"style": {"fontSize": 42}}, (0.75, 3.5)),
])
def test_exact_local_patch_changes_only_explicit_subtitle_fields(cut_episode: tuple[ShortDramaService, str, list[str]],
    part: int, change: dict[str, JsonValue], expected: tuple[float, float]) -> None:
    service, pid, ids = cut_episode
    baseline = _cues(service, pid)
    current = read_editorial(service, pid)
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": current.revision, "shot_id": ids[part], "clips": [],
        "subtitles": [{"id": "cue-0", **change}]}})
    assert result.error is None, result.model_dump_json()
    after = _cues(service, pid)
    assert set(after) == set(baseline)
    expected_cue = baseline["cue-0"].model_copy(deep=True, update={"startTime": expected[0], "endTime": expected[1]})
    if "text" in change:
        expected_cue.text = str(change["text"])
    style = change.get("style")
    if isinstance(style, dict):
        expected_cue.style.update(style)
    assert after["cue-0"] == expected_cue
    assert {key: value for key, value in after.items() if key != "cue-0"} == {key: value for key, value in baseline.items() if key != "cue-0"}
    assert result.output and result.output["content"]["changed_subtitles"][0]["id"] == "cue-0"


@pytest.mark.parametrize(("part", "change", "expected"), [
    (0, {"duration_seconds": 1.5}, (0.75, 3.0)),
    (0, {"duration_seconds": 1.5, "source_in_seconds": 0.5}, (0.25, 3.0)),
    (1, {"speed": 2.0}, (0.75, 2.75)),
    (0, {"remove": True}, (0.0, 1.5)), (1, {"remove": True}, (0.75, 2.0)),
])
def test_window_duration_edits_preserve_subtitle_portions_outside_window(cut_episode: tuple[ShortDramaService, str, list[str]],
    part: int, change: dict[str, JsonValue], expected: tuple[float, float]) -> None:
    service, pid, ids = cut_episode
    local = read_shot_editorial(service, pid, ids[part])
    video = next(clip for clip in EditorProject.model_validate(local.project).timelines[0].clips if clip.type == "video")
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": local.revision, "shot_id": ids[part], "clips": [{"id": video.id, **change}]}})
    assert result.error is None, result.model_dump_json()
    cue = _cues(service, pid)["cue-0"]
    assert (cue.startTime, cue.endTime) == expected
    assert cue.text == "镜头 1 的字幕" and cue.style == {"fontSize": 35, "color": "#ffeeaa"}


def test_local_clip_split_keeps_one_continuous_crossing_subtitle(cut_episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = cut_episode
    baseline = _cues(service, pid)
    local = read_shot_editorial(service, pid, ids[0])
    editor = EditorProject.model_validate(local.project)
    timeline = editor.timelines[0]
    halves = []
    for clip in timeline.clips:
        if clip.type != "video":
            continue
        halves.append(clip.model_copy(deep=True, update={"id": f"{clip.id}-second", "startTime": 1, "duration": 1, "trimStart": clip.trimStart + 1}))
        clip.duration = 1
    timeline.clips.extend(halves)
    save_shot_editorial(service, pid, ids[0], StudioTimelineSave(expected_revision=local.revision, timeline={"project": as_json(editor)}))
    assert _cues(service, pid) == baseline
