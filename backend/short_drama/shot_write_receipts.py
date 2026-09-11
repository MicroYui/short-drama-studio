"""Small saved-content facts, separate from generation reference diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field, JsonValue

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.models import DomainModel, ShotPlan, ShotReferenceCue, ShotTimelineBeat

if TYPE_CHECKING:
    from short_drama.tools.contracts import ToolWriteReceipt


class SavedShotTimeRange(DomainModel):
    start_seconds: float
    end_seconds: float


class SavedShotCue(DomainModel):
    scope: Literal["SHOT", "BEAT"]
    beat_index: int | None = None
    time_range: SavedShotTimeRange = Field(description=(
        "Design-time applicability within this Shot; a SHOT cue covers its whole duration. "
        "This does not assert that the image has been placed in the editorial timeline."
    ))
    cue: ShotReferenceCue
    referenced_text: str | None = Field(default=None, description=(
        "Exact saved Unicode substring identified by the cue; absent for unanchored references."
    ))


class SavedShotBeat(DomainModel):
    index: int
    start_seconds: float
    end_seconds: float
    dialogue_line_ids: list[str]
    sound: str | None = None


class SavedShotSummary(DomainModel):
    shot_id: str
    revision: int
    duration_seconds: float
    audio_intent: str
    dialogue_line_ids: list[str]
    beats: list[SavedShotBeat]
    shot_cues: list[SavedShotCue] = Field(description=(
        "Actual saved occurrences, including GENERATION and EDITORIAL usage. "
        "Saving a binding does not certify media quality or completed editing."
    ))


def summarize_saved_shot(shot: ShotPlan) -> SavedShotSummary:
    cues: list[SavedShotCue] = []
    owners: list[tuple[Literal["SHOT", "BEAT"], int | None, float, float, ShotPlan | ShotTimelineBeat]] = [
        ("SHOT", None, 0.0, shot.duration_seconds, shot), *(
            ("BEAT", index, beat.start_seconds, beat.end_seconds, beat)
            for index, beat in enumerate(shot.timeline_beats)
        )]
    for scope, beat_index, start, end, owner in owners:
        for cue in owner.references:
            text: str | None = None
            if cue.field is not None and cue.text_start is not None and cue.text_end is not None:
                field_text: object = getattr(owner, cue.field, None)
                if isinstance(field_text, str):
                    text = field_text[cue.text_start:cue.text_end]
            cues.append(SavedShotCue(
                scope=scope, beat_index=beat_index,
                time_range=SavedShotTimeRange(start_seconds=start, end_seconds=end),
                cue=cue.model_copy(deep=True), referenced_text=text,
            ))
    return SavedShotSummary(
        shot_id=shot.id, revision=shot.revision, duration_seconds=shot.duration_seconds,
        audio_intent=shot.audio_intent, dialogue_line_ids=list(shot.dialogue_line_ids),
        beats=[SavedShotBeat(index=index, start_seconds=beat.start_seconds,
            end_seconds=beat.end_seconds, dialogue_line_ids=list(beat.dialogue_line_ids), sound=beat.sound)
            for index, beat in enumerate(shot.timeline_beats)],
        shot_cues=cues,
    )


def shot_write_receipt(output: Mapping[str, object], refs: Sequence[ArtifactRef]) -> ToolWriteReceipt:
    # Local imports keep this typed domain projection independent of registry setup.
    from short_drama.tools.feedback import write_receipt

    receipt = write_receipt(output, refs)
    for key in ("saved_shot", "reference_plan_preview_scope", "reference_plan_preview_error"):
        if key in output:
            receipt.current_state[key] = cast(JsonValue, output[key])
    return receipt
