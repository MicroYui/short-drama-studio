"""Provider-neutral normalization for human-readable media reference aliases."""

from __future__ import annotations

import re
import unicodedata


def normalize_reference_alias(
    value: str,
    *,
    max_length: int = 80,
    truncate: bool = False,
) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lstrip("@").strip()
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError("Reference alias cannot contain control characters")
    normalized = re.sub(r"[^\w\-]+", "_", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[_-]{2,}", "_", normalized).strip("_-")
    if not normalized:
        raise ValueError("Reference alias must contain a readable name")
    if len(normalized) > max_length:
        if not truncate:
            raise ValueError(
                f"Reference alias must not exceed {max_length} normalized characters"
            )
        normalized = normalized[:max_length].rstrip("_-")
    return normalized


__all__ = ["normalize_reference_alias"]
