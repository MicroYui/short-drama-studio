"""Local production trace, separate from the model's current working context."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from pydantic import JsonValue

type TraceWriter = Callable[[str, dict[str, JsonValue]], None]
_writer: ContextVar[TraceWriter | None] = ContextVar("studio_trace_writer", default=None)
_context: ContextVar[dict[str, JsonValue] | None] = ContextVar("studio_trace_context", default=None)


def current_trace_context() -> dict[str, JsonValue]:
    return dict(_context.get() or {})


def record_trace(kind: str, payload: dict[str, JsonValue]) -> None:
    writer = _writer.get()
    if writer is not None:
        writer(kind, {**(_context.get() or {}), **payload})


@contextmanager
def trace_context(**fields: JsonValue) -> Iterator[None]:
    """Correlate child calls without sharing mutable context across threads."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def clean_trace_text(value: str) -> str:
    # Keep prompts readable and original files referenced; never duplicate base64 media.
    return re.sub(r"data:[^;\s]+;base64,[A-Za-z0-9+/=]+", "[inline media retained in project assets]", value)


@contextmanager
def production_trace(directory: Path, emit: TraceWriter) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)

    def write(kind: str, payload: dict[str, JsonValue]) -> None:
        now = datetime.now(UTC)
        entry = {"kind": kind, "created_at": now.isoformat(), "data": payload}
        name = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{uuid4().hex[:8]}-{kind}.json"
        (directory / name).write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        if kind not in {"model.input", "model.output", "context.offloaded"}:
            emit(kind, payload)

    token = _writer.set(write)
    try:
        yield
    finally:
        _writer.reset(token)
