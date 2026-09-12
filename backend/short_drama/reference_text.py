"""Exact prose locations; no asset selection, normalization, or project mutation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from short_drama.models import ShotReferenceCue


class ShotTextAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact: str = Field(min_length=1, strict=True, description=(
        "逐字复制所属 field 中本次引用的文字，例如 @林乔；不按 alias 猜显示文本。"
    ))
    prefix: str | None = Field(default=None, strict=True, description=(
        "同一文字出现多次时，复制紧邻所选出现之前的原文以消歧；不包含 exact。"
    ))
    suffix: str | None = Field(default=None, strict=True, description=(
        "必要时复制紧邻所选出现之后的原文以消歧；不包含 exact。"
    ))


def locate_text_anchor(text: str, anchor: ShotTextAnchor) -> tuple[int, int]:
    """Return one Unicode code-point interval, preserving exact input bytes/text."""
    matches: list[int] = []
    position = text.find(anchor.exact)
    while position >= 0:
        end = position + len(anchor.exact)
        if ((anchor.prefix is None or text[:position].endswith(anchor.prefix))
                and (anchor.suffix is None or text[end:].startswith(anchor.suffix))):
            matches.append(position)
        position = text.find(anchor.exact, position + 1)
    if not matches:
        raise ValueError(
            "REFERENCE_TEXT_ANCHOR_NOT_FOUND: exact/prefix/suffix must match the current field verbatim; "
            "copy its actual text, or preserve an existing explicit text range."
        )
    if len(matches) != 1:
        raise ValueError(
            f"REFERENCE_TEXT_ANCHOR_AMBIGUOUS: {len(matches)} occurrences match; "
            "supply adjacent prefix/suffix from the intended occurrence. No reference was chosen."
        )
    return matches[0], matches[0] + len(anchor.exact)


def remap_field_references(
    references: list[ShotReferenceCue], *, field: str, before: str, after: str,
) -> list[ShotReferenceCue]:
    """Retain exact existing selections through a whole-field prose edit.

    Unchanged prefix/suffix selections retain their particular occurrence. An
    edited mention must survive uniquely inside the replacement; removing the
    complete mention removes that occurrence, never its asset or other uses.
    """
    result = [cue.model_copy(deep=True) for cue in references]
    if before == after:
        return result
    left = 0
    while left < min(len(before), len(after)) and before[left] == after[left]:
        left += 1
    tail = 0
    while (tail < min(len(before), len(after)) - left
           and before[len(before) - tail - 1] == after[len(after) - tail - 1]):
        tail += 1
    right = len(before) - tail
    delta = len(after) - len(before)
    # Expand a partial mention edit to that complete occurrence. Searching the
    # whole field instead could wrongly move a renamed first @mention onto an
    # unchanged second occurrence with the same visible text.
    overlaps = [cue for cue in result if cue.field == field
        and cue.text_start is not None and cue.text_end is not None
        and cue.text_start < right and cue.text_end > left]
    if overlaps:
        left = min(left, *(cue.text_start for cue in overlaps if cue.text_start is not None))
        right = max(right, *(cue.text_end for cue in overlaps if cue.text_end is not None))
    replacement = after[left:right + delta]
    for cue in list(result):
        if cue.field != field or cue.text_start is None or cue.text_end is None:
            continue
        if cue.text_end <= left:
            continue
        if cue.text_start >= right:
            cue.text_start += delta
            cue.text_end += delta
            continue
        label = before[cue.text_start:cue.text_end]
        if (not label or cue.text_end > len(before)
                or cue.text_start < left or cue.text_end > right):
            raise ValueError(f"REFERENCE_TEXT_RANGE_INVALID: {cue.id or cue.alias} in {field}; use shot.save_current with the current prose and explicit references.")
        if label not in replacement:
            result.remove(cue)
            continue
        original_region = before[left:right]
        if original_region.find(label) != original_region.rfind(label):
            raise ValueError(
                f"REFERENCE_TEXT_REMAP_AMBIGUOUS: {cue.id or cue.alias} in {field}; "
                "the changed region originally contained multiple identical mentions. "
                "Use shot.save_current with explicit references/text_anchor; no occurrence was chosen."
            )
        try:
            start, end = locate_text_anchor(replacement, ShotTextAnchor(exact=label))
        except ValueError as exc:
            raise ValueError(f"Reference {cue.id or cue.alias} in {field}: {exc}; use shot.save_current to select the intended occurrence.") from exc
        cue.text_start, cue.text_end = start + left, end + left
    return result
