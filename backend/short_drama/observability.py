"""Small structured-log helpers for local short-drama runtime events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import cast
from pydantic import JsonValue
from short_drama.studio_trace import record_trace


LOGGER_NAME = "short_drama.observability"

_logger = logging.getLogger(LOGGER_NAME)

type EventValue = str | int | float | bool | None


def log_event(event_name: str, **fields: EventValue) -> None:
    """Emit one compact JSON event through the application's logging setup."""

    payload: dict[str, EventValue] = {
        "event": event_name,
        "timestamp": datetime.now(UTC).isoformat(),
        **fields,
    }
    _logger.info(
        "%s",
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    record_trace(event_name, cast(dict[str, JsonValue], payload))


def log_provider_task_transition(
    *,
    task_kind: str,
    action: str,
    task_id: str,
    project_id: str,
    provider: str,
    previous_status: str | None,
    status: str,
    provider_task_id: str | None = None,
    parent_task_id: str | None = None,
    error_kind: str | None = None,
) -> None:
    """Log a provider-facing task transition without request or media contents."""

    if previous_status == status and error_kind is None:
        return
    log_event(
        "provider.task_transition",
        task_kind=task_kind,
        action=action,
        task_id=task_id,
        parent_task_id=parent_task_id,
        project_id=project_id,
        provider=provider,
        provider_task_id=provider_task_id,
        previous_status=previous_status,
        status=status,
        error_kind=error_kind,
    )


def log_language_generation(
    *,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    image_count: int,
    response_metadata: dict[str, object],
    success: bool,
    completion_type: str,
    elapsed_seconds: float,
    time_to_first_text_delta_seconds: float | None,
    instruction_characters: int,
    input_characters: int,
    output_characters: int,
    error_kind: str | None = None,
    call_id: str | None = None,
) -> None:
    """Log one content-free language generation measurement."""

    raw_usage = response_metadata.get("usage")
    usage = cast(dict[str, object], raw_usage) if isinstance(raw_usage, dict) else {}

    def token_count(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    def detail_count(key: str, detail: str) -> int | None:
        value = usage.get(key)
        return token_count(cast(dict[str, object], value).get(detail)) if isinstance(value, dict) else None

    response_id = response_metadata.get("id")
    response_model = response_metadata.get("model")
    log_event(
        "provider.language_generation",
        call_id=call_id,
        provider="openai_compatible",
        model=model,
        reasoning_effort=reasoning_effort,
        response_id=response_id if isinstance(response_id, str) else None,
        response_model=response_model if isinstance(response_model, str) else None,
        max_output_tokens=max_output_tokens,
        image_count=image_count,
        usage_available=bool(usage),
        input_tokens=token_count(usage.get("input_tokens")),
        output_tokens=token_count(usage.get("output_tokens")),
        total_tokens=token_count(usage.get("total_tokens")),
        cached_input_tokens=detail_count("input_tokens_details", "cached_tokens"),
        reasoning_tokens=detail_count("output_tokens_details", "reasoning_tokens"),
        success=success,
        completion_type=completion_type,
        elapsed_seconds=round(max(0.0, elapsed_seconds), 6),
        time_to_first_text_delta_seconds=(
            round(max(0.0, time_to_first_text_delta_seconds), 6)
            if time_to_first_text_delta_seconds is not None
            else None
        ),
        instruction_characters=instruction_characters,
        input_characters=input_characters,
        output_characters=output_characters,
        error_kind=error_kind,
    )


__all__ = [
    "LOGGER_NAME",
    "log_event",
    "log_language_generation",
    "log_provider_task_transition",
]
