"""Derive H3 utterances from the current ShotPlan dialogue ordering contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from short_drama.models import ProjectDocument, ShotPlan

if TYPE_CHECKING:
    from short_drama.production_contracts import GenerationUnit


@dataclass(frozen=True)
class H3DialogueSpan:
    line_id: str
    start_seconds: float
    end_seconds: float
    beat_indices: tuple[int, ...]


@dataclass(frozen=True)
class H3DialoguePart:
    """A spoken line's coverage in one editorial shot's local coordinates."""

    shot_id: str
    start_seconds: float
    end_seconds: float
    beat_indices: tuple[int, ...]


@dataclass(frozen=True)
class H3RequestDialogueSpan:
    line_id: str
    start_seconds: float
    end_seconds: float
    parts: tuple[H3DialoguePart, ...]


def shot_dialogue_spans(shot: ShotPlan) -> list[H3DialogueSpan]:
    """Collapse adjacent IDs, exactly as ShotPlan.validate_timeline_beats does.

    An intervening different ID starts a new utterance, even if its text matches.
    Empty beats do not reset the model's flattened dialogue sequence. No merge is
    inferred across editorial shots, and the source ShotPlan is never changed.
    """

    if not shot.timeline_beats:
        return [
            H3DialogueSpan(line_id, 0.0, shot.duration_seconds, ())
            for line_id in shot.dialogue_line_ids
        ]
    spans: list[H3DialogueSpan] = []
    for index, beat in enumerate(shot.timeline_beats):
        for line_id in beat.dialogue_line_ids:
            if spans and spans[-1].line_id == line_id:
                spans[-1] = replace(
                    spans[-1], end_seconds=beat.end_seconds,
                    beat_indices=(*spans[-1].beat_indices, index),
                )
            else:
                spans.append(H3DialogueSpan(
                    line_id, beat.start_seconds, beat.end_seconds, (index,),
                ))
    return spans


def dialogue_span_payload(shot: ShotPlan, *, offset_seconds: float = 0.0) -> list[dict[str, object]]:
    """A compact occurrence map; locked_dialogue remains the text source."""

    return [
        {
            "utterance_id": f"utterance-{index + 1}",
            "line_id": span.line_id,
            "start_seconds": offset_seconds + span.start_seconds,
            "end_seconds": offset_seconds + span.end_seconds,
            "start_beat_index": span.beat_indices[0] if span.beat_indices else None,
            "continuation_beat_indices": list(span.beat_indices[1:]),
        }
        for index, span in enumerate(shot_dialogue_spans(shot))
    ]


def request_dialogue_spans(
    shots: Sequence[ShotPlan], *, source_offsets: Mapping[str, float] | None = None,
) -> list[H3RequestDialogueSpan]:
    """One request's utterances, deriving continuations from adjacent shot IDs.

    Only the preceding shot's last occurrence and this shot's first occurrence
    can join. An empty shot breaks coverage; empty beats retain ShotPlan's
    existing meaning. A-B-A and distinct IDs with identical words stay separate.
    Call once per generation group, never on an entire film's concatenated shots.
    """

    spans: list[H3RequestDialogueSpan] = []
    previous_shot_id: str | None = None
    cursor = 0.0
    for shot in shots:
        offset = cursor if source_offsets is None else source_offsets[shot.id]
        local_spans = shot_dialogue_spans(shot)
        for index, span in enumerate(local_spans):
            part = H3DialoguePart(shot.id, span.start_seconds, span.end_seconds, span.beat_indices)
            if (index == 0 and spans and previous_shot_id is not None
                    and spans[-1].parts[-1].shot_id == previous_shot_id
                    and spans[-1].line_id == span.line_id):
                spans[-1] = replace(spans[-1], end_seconds=offset + span.end_seconds,
                                    parts=(*spans[-1].parts, part))
            else:
                spans.append(H3RequestDialogueSpan(span.line_id, offset + span.start_seconds,
                                                   offset + span.end_seconds, (part,)))
        previous_shot_id = shot.id if local_spans else None
        cursor = offset + shot.duration_seconds
    return spans


def generation_unit_dialogue_spans(unit: GenerationUnit, shots: Sequence[ShotPlan]) -> list[H3RequestDialogueSpan]:
    by_id = {shot.id: shot for shot in shots}
    if len(by_id) != len(shots) or set(by_id) != set(unit.shot_ids):
        raise ValueError("H3 dialogue request must contain exactly the generation unit's shots")
    ordered = [by_id[window.shot_id] for window in unit.shot_windows]
    for window, shot in zip(unit.shot_windows, ordered, strict=True):
        for span in shot_dialogue_spans(shot):
            if span.end_seconds > window.duration_seconds + 0.001:
                raise ValueError(f"H3 dialogue {span.line_id} exceeds shot {shot.id}'s actual source window")
    return request_dialogue_spans(ordered, source_offsets={
        window.shot_id: window.source_in_seconds for window in unit.shot_windows
    })


def request_dialogue_span_payload(spans: Sequence[H3RequestDialogueSpan]) -> list[dict[str, object]]:
    return [{
        "utterance_id": f"utterance-{index + 1}", "line_id": span.line_id,
        "start_seconds": span.start_seconds, "end_seconds": span.end_seconds,
        "parts": [{"shot_id": part.shot_id, "start_seconds": part.start_seconds,
                   "end_seconds": part.end_seconds, "beat_indices": list(part.beat_indices)}
                  for part in span.parts],
    } for index, span in enumerate(spans)]


def assert_complete_dialogue_request(document: ProjectDocument, shots: Sequence[ShotPlan]) -> None:
    """Do not independently generate one side of a declared continuous line.

    Current managed storyboard neighbours establish the boundary. Old ungrouped
    projections may reuse IDs for separate utterances; do not reinterpret them.
    """

    if document.storyboard_document_id is None:
        return
    selected = {shot.id: shot for shot in shots}
    canonical = sorted(document.shots, key=lambda item: item.sequence)
    for left, right in zip(canonical, canonical[1:], strict=False):
        if (left.id in selected) == (right.id in selected):
            continue
        left = selected.get(left.id, left)
        right = selected.get(right.id, right)
        if (left.dialogue_line_ids and right.dialogue_line_ids
                and left.dialogue_line_ids[-1] == right.dialogue_line_ids[0]):
            line_id = left.dialogue_line_ids[-1]
            raise ValueError(
                f"H3_DIALOGUE_REQUEST_INCOMPLETE: {line_id} continues across shots {left.id} and {right.id}; "
                "compile their complete current generation group with route.prepare_h3_group. "
                "If they belong to different groups, move the group boundary after the utterance; "
                "separate video requests cannot generate one continuous utterance."
            )


def request_speaker_ids(document: ProjectDocument, shots: Sequence[ShotPlan]) -> dict[str, str]:
    """Assign request-local voice labels, without creating character bindings.

    Bound entities remain distinct even when display names match. An unbound
    display name only groups unbound lines within this request; it never aliases
    a bound entity or persists a project-wide identity assertion.
    """

    script = next(version for version in document.script_versions
                  if version.id == document.project.current_script_version_id)
    lines = {line.id: line for line in script.lines}
    voices: dict[tuple[str, str], str] = {}
    assignments: dict[str, str] = {}
    for shot in shots:
        for line_id in shot.dialogue_line_ids:
            if (line := lines.get(line_id)) is None:
                continue
            key = (("entity", line.speaker_character_id) if line.speaker_character_id is not None
                   else ("unbound-speaker", line.speaker))
            assignments[line.id] = voices.setdefault(key, f"S{len(voices) + 1}")
    return assignments
