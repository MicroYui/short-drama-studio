"""Keep editable script projections synchronized with canonical line records."""

from __future__ import annotations

from collections.abc import Sequence

from short_drama.models import ScriptLine, StructuredScene


class ScriptTextSynchronizationError(ValueError):
    """Raised when a line edit cannot be located in its readable scene text."""


def synchronize_script_line_text(
    *,
    previous_lines: Sequence[ScriptLine],
    revised_lines: Sequence[ScriptLine],
    scenes: Sequence[StructuredScene],
) -> tuple[list[StructuredScene], str]:
    """Apply exact line edits to scene text and rebuild the top-level screenplay.

    Line identity and scene membership provide the edit target.  Exact string
    replacement is deliberately used only inside that already-selected scene;
    it is not a routing heuristic.  If an established screenplay cannot be
    reconciled, fail instead of persisting two contradictory script truths.
    """

    previous_by_id = {line.id: line for line in previous_lines}
    revised_by_id = {line.id: line for line in revised_lines}
    changed_ids = {
        line_id
        for line_id, revised in revised_by_id.items()
        if (previous := previous_by_id.get(line_id)) is not None
        and revised.spoken_text != previous.spoken_text
    }
    assigned_changed_ids: set[str] = set()
    synchronized: list[StructuredScene] = []
    for source_scene in scenes:
        scene = source_scene.model_copy(deep=True)
        scene_changed_ids = [
            line_id for line_id in scene.line_ids if line_id in changed_ids
        ]
        if not scene_changed_ids:
            synchronized.append(scene)
            continue
        assigned_changed_ids.update(scene_changed_ids)
        if not scene.screenplay_text.strip():
            readable_lines = [
                revised_by_id[line_id]
                for line_id in scene.line_ids
                if line_id in revised_by_id
            ]
            scene.screenplay_text = "\n".join(
                [
                    f"【{scene.heading}】",
                    *(f"{line.speaker}：{line.spoken_text}" for line in readable_lines),
                ]
            )
            synchronized.append(scene)
            continue

        text = scene.screenplay_text
        cursor = 0
        for line_id in scene.line_ids:
            previous = previous_by_id.get(line_id)
            revised = revised_by_id.get(line_id)
            if previous is None or revised is None:
                continue
            position = text.find(previous.spoken_text, cursor)
            if position < 0:
                if line_id in changed_ids:
                    raise ScriptTextSynchronizationError(
                        "Current scene screenplay does not contain the selected line "
                        f"{line_id}; use the Agent screenplay revision flow for a "
                        "creative rewrite."
                    )
                continue
            if line_id in changed_ids:
                text = (
                    text[:position]
                    + revised.spoken_text
                    + text[position + len(previous.spoken_text) :]
                )
                cursor = position + len(revised.spoken_text)
            else:
                # Advance across unchanged dialogue as well.  Otherwise two
                # consecutive lines with identical text would make a later edit
                # replace the first occurrence instead of the selected line.
                cursor = position + len(previous.spoken_text)
        scene.screenplay_text = text
        synchronized.append(scene)

    unassigned = sorted(changed_ids - assigned_changed_ids)
    if unassigned:
        raise ScriptTextSynchronizationError(
            "Selected script lines are not assigned to a structured scene: "
            + ", ".join(unassigned)
        )
    return synchronized, "\n\n".join(
        scene.screenplay_text for scene in synchronized if scene.screenplay_text.strip()
    )


__all__ = [
    "ScriptTextSynchronizationError",
    "synchronize_script_line_text",
]
