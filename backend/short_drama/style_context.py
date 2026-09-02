"""Small helpers for keeping unresolved Style fields out of provider prompts."""

from __future__ import annotations


UNSPECIFIED_STYLE_VALUE = "未指定"


def declared_style_value(value: str) -> str | None:
    """Return a concrete project choice, or None for the scaffold placeholder."""

    normalized = value.strip()
    if not normalized or normalized == UNSPECIFIED_STYLE_VALUE:
        return None
    return normalized
