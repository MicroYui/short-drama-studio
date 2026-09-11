"""Draft captions from authored dialogue; native audio alignment remains separate."""

from __future__ import annotations

from collections.abc import Sequence

from short_drama.models import ScriptLine, ShotPlan, SubtitleCue, TimelineVideoClip
from short_drama.production_contracts import ClipGroup
from short_drama.providers.h3_dialogue_spans import request_dialogue_spans


def planned_dialogue_cues(
    shots: Sequence[ShotPlan],
    clips: Sequence[TimelineVideoClip],
    script_lines: Sequence[ScriptLine],
    *,
    clip_groups: Sequence[ClipGroup] = (),
) -> list[SubtitleCue]:
    """Keep one caption over a declared speech bridge, using adopted clip time.

    Ordinary one-shot lines retain the existing provisional caption placement.
    Cross-shot speech uses its explicit local beat windows, clipped to the actual
    adopted media. These authored timings do not claim to be heard ASR timings.
    """

    clip_by_shot = {clip.shot_id: clip for clip in clips}
    shot_by_id = {shot.id: shot for shot in shots}
    line_by_id = {line.id: line for line in script_lines}
    cues: list[SubtitleCue] = []
    groups = ([list(group.shot_ids) for group in clip_groups] if clip_groups
              else [[shot.id] for shot in shots])
    spans = [span for ids in groups for span in request_dialogue_spans([shot_by_id[sid] for sid in ids])]
    for span in spans:
        line = line_by_id.get(span.line_id)
        if line is None:
            continue
        first = span.parts[0]
        if len(span.parts) == 1:
            shot = shot_by_id[first.shot_id]
            clip = clip_by_shot[first.shot_id]
            ids = [line_id for line_id in shot.dialogue_line_ids if line_id in line_by_id]
            cue_duration = max(0.5, clip.duration_seconds - 1.0) / len(ids)
            start = clip.start_seconds + 0.5 + cue_duration * ids.index(line.id)
            end = min(clip.start_seconds + clip.duration_seconds - 0.25, start + cue_duration)
        else:
            visible_windows: list[tuple[float, float]] = []
            for part in span.parts:
                clip = clip_by_shot[part.shot_id]
                start = clip.start_seconds + min(part.start_seconds, clip.duration_seconds)
                end = clip.start_seconds + min(part.end_seconds, clip.duration_seconds)
                if end > start:
                    visible_windows.append((start, end))
            if not visible_windows:
                continue
            start, end = visible_windows[0][0], visible_windows[-1][1]
        if end <= start:
            continue
        cues.append(SubtitleCue(
            id=f"subtitle-{first.shot_id}-{line.id}", script_line_id=line.id,
            start_seconds=start, end_seconds=end, text=line.subtitle_text,
            speaker=line.speaker,
        ))
    return cues
