"""Ordered utterance coverage derived from current Shot and ClipGroup fields."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from short_drama.models import ShotPlan
    from short_drama.production_contracts import ClipGroup


def ordered_dialogue_line_ids(
    shots: Sequence[ShotPlan], *, clip_groups: Sequence[ClipGroup]
) -> list[str]:
    """Fold only one matching ID at an adjacent Shot boundary in one request.

    ShotPlan already derives its own ordered IDs from timeline beats. At a cut,
    the previous Shot's final line may continue as the next Shot's first line.
    An empty Shot or a ClipGroup boundary breaks that coverage. Non-contiguous
    A-B-A occurrences and different IDs remain intact for the source contract
    to validate; this helper never globally deduplicates authored dialogue.
    """

    by_id = {shot.id: shot for shot in shots}
    result: list[str] = []
    for group in clip_groups:
        previous: ShotPlan | None = None
        for shot_id in group.shot_ids:
            current = by_id.get(shot_id)
            if current is None:
                raise ValueError(f"ClipGroup {group.id} references unknown Shot {shot_id}")
            lines = current.dialogue_line_ids
            continuing = bool(
                previous is not None
                and previous.dialogue_line_ids
                and lines
                and previous.dialogue_line_ids[-1] == lines[0]
            )
            result.extend(lines[1:] if continuing else lines)
            previous = current
    return result
