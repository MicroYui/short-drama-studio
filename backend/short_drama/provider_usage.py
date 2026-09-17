"""Retain provider-reported token counters without response or request contents."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue


_COUNTERS = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
    "cached_tokens", "cache_read_tokens", "cache_write_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "reasoning_tokens", "text_tokens", "image_tokens",
    "audio_tokens", "video_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
    "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens",
})
_DETAILS = frozenset({
    "input_tokens_details", "output_tokens_details", "prompt_tokens_details",
    "completion_tokens_details", "cached_tokens_details", "cache_creation",
})


def token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def reported_token_usage(value: object) -> dict[str, JsonValue] | None:
    """Copy only known counters and their modality/cache breakdowns; never infer zeros."""
    if not isinstance(value, dict):
        return None
    result: dict[str, JsonValue] = {}
    for key, item in cast(dict[str, object], value).items():
        if key in _COUNTERS:
            result[key] = token_count(item)
        elif key in _DETAILS:
            # Details contain counters and, for caches, one modality breakdown.
            result[key] = _reported_details(item)
    return result or None


def _reported_details(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    details = cast(dict[str, object], value)
    result: dict[str, JsonValue] = {
        key: token_count(item) for key, item in details.items() if key in _COUNTERS
    }
    cached = details.get("cached_tokens_details")
    if "cached_tokens_details" in details:
        result["cached_tokens_details"] = (
            {key: token_count(item) for key, item in cast(dict[str, object], cached).items()
             if key in _COUNTERS}
            if isinstance(cached, dict) else None
        )
    return result or None


def detail_count(usage: dict[str, JsonValue], group: str, counter: str) -> int | None:
    details = usage.get(group)
    return token_count(details.get(counter)) if isinstance(details, dict) else None


def first_reported_count(*values: object) -> int | None:
    return next((count for value in values if (count := token_count(value)) is not None), None)
