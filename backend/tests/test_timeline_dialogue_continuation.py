"""A cut in the picture must not restart the same authored caption."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.models import ScriptLine, ShotPlan, ShotTimelineBeat, TimelineVideoClip
from short_drama.production_contracts import ClipGroup
from short_drama.service import ShortDramaService
from short_drama.timeline_dialogue import planned_dialogue_cues


def _fixture(tmp_path: Path) -> tuple[list[ShotPlan], list[ScriptLine]]:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "work")
    document = service.initialize_fixture().document
    script = next(s for s in document.script_versions if s.id == document.project.current_script_version_id)
    line = script.lines[0]
    shots: list[ShotPlan] = []
    for index, duration in enumerate((2.5, 1.5)):
        shots.append(ShotPlan.model_validate({
            **document.shots[index].model_dump(), "duration_seconds": duration,
            "dialogue_line_ids": [line.id],
            "timeline_beats": [ShotTimelineBeat(
                start_seconds=0, end_seconds=duration, visual="A paper is passed, then its text is shown.",
                framing="detail", camera="locked", action="Continue the same utterance over the cut.",
                dialogue_line_ids=[line.id],
            )],
        }))
    return shots, script.lines


def _groups(shots: list[ShotPlan]) -> list[ClipGroup]:
    return [ClipGroup(id="group-bridge", title="Speech over the cut", shot_ids=[s.id for s in shots],
                      duration_seconds=4, generation_intent="One continuous utterance.")]


def _clips(shots: list[ShotPlan], durations: tuple[float, float] = (2.5, 1.5)) -> list[TimelineVideoClip]:
    cursor = 8.5
    clips: list[TimelineVideoClip] = []
    for shot, duration in zip(shots, durations, strict=True):
        clips.append(TimelineVideoClip(
            id=f"clip-{shot.id}", shot_id=shot.id, take_id="take-shared-group", media_path="/test/group.mp4",
            start_seconds=cursor, source_in_seconds=cursor, duration_seconds=duration,
            transition_in="none", transition_out="cut",
        ))
        cursor += duration
    return clips


def test_continuous_caption_spans_two_adopted_shots_once(tmp_path: Path) -> None:
    shots, lines = _fixture(tmp_path)
    before = [s.model_dump() for s in shots]
    cues = planned_dialogue_cues(shots, _clips(shots), lines, clip_groups=_groups(shots))
    assert len(cues) == 1
    assert cues[0].script_line_id == lines[0].id
    assert cues[0].text == lines[0].subtitle_text
    assert (cues[0].start_seconds, cues[0].end_seconds) == (8.5, 12.5)
    assert [s.model_dump() for s in shots] == before


def test_continuous_caption_uses_actual_shortened_clip_bounds(tmp_path: Path) -> None:
    shots, lines = _fixture(tmp_path)
    cues = planned_dialogue_cues(shots, _clips(shots, (2.0, 1.0)), lines, clip_groups=_groups(shots))
    assert len(cues) == 1
    assert (cues[0].start_seconds, cues[0].end_seconds) == (8.5, 11.5)


def test_identical_text_from_two_line_ids_stays_two_captions(tmp_path: Path) -> None:
    shots, lines = _fixture(tmp_path)
    lines[1].subtitle_text = lines[0].subtitle_text
    shots[1].dialogue_line_ids = [lines[1].id]
    shots[1].timeline_beats[0].dialogue_line_ids = [lines[1].id]
    cues = planned_dialogue_cues(shots, _clips(shots), lines, clip_groups=_groups(shots))
    assert [cue.script_line_id for cue in cues] == [lines[0].id, lines[1].id]
    assert [(cue.start_seconds, cue.end_seconds) for cue in cues] == [(9.0, 10.5), (11.5, 12.0)]


def test_silent_shot_does_not_extend_the_caption_by_prose_alone(tmp_path: Path) -> None:
    shots, lines = _fixture(tmp_path)
    shots[1].dialogue_line_ids = []
    shots[1].timeline_beats[0].dialogue_line_ids = []
    cues = planned_dialogue_cues(shots, _clips(shots), lines, clip_groups=_groups(shots))
    assert len(cues) == 1
    assert cues[0].end_seconds == pytest.approx(10.5)
