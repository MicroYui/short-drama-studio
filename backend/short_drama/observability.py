"""Small structured-log helpers for local short-drama runtime events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


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
    success: bool,
    completion_type: str,
    elapsed_seconds: float,
    time_to_first_text_delta_seconds: float | None,
    instruction_characters: int,
    input_characters: int,
    output_characters: int,
    error_kind: str | None = None,
) -> None:
    """Log one content-free language generation measurement."""

    log_event(
        "provider.language_generation",
        provider="openai_compatible",
        model=model,
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
