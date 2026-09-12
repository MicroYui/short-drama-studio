"""In-process discovery and invocation registry for typed tools."""

from __future__ import annotations

from collections.abc import Mapping
import threading
import time
from typing import Generic, Protocol, cast
from uuid import uuid4

from pydantic import ValidationError
from short_drama.studio_trace import record_trace
from short_drama.tools.feedback import argument_scope, validation_feedback, write_receipt

from short_drama.tools.contracts import (
    InputT,
    OutputT,
    ToolCallStatus,
    ToolCategory,
    ToolDescriptor,
    ToolEffect,
    ToolError,
    ToolResult,
    ToolSpec,
)


class ToolNotFoundError(KeyError):
    """Raised by discovery calls that require a registered tool."""


class _RegisteredToolLike(Protocol):
    def descriptor(self) -> ToolDescriptor: ...

    def invoke(self, arguments: Mapping[str, object]) -> ToolResult: ...


class _RegisteredTool(Generic[InputT, OutputT]):
    def __init__(self, spec: ToolSpec[InputT, OutputT]) -> None:
        self._spec = spec
        self._descriptor: ToolDescriptor | None = None
        self._descriptor_lock = threading.Lock()

    def descriptor(self) -> ToolDescriptor:
        # Schemas belong to this registered ToolSpec, not to every Agent turn.
        with self._descriptor_lock:
            if self._descriptor is None:
                self._descriptor = self._spec.descriptor()
            return self._descriptor.model_copy(deep=True)

    def invoke(self, arguments: Mapping[str, object]) -> ToolResult:
        tool_call_id = f"tool-{uuid4().hex[:12]}"
        started = time.monotonic()
        scope = argument_scope(arguments, nested=True)
        record_trace("tool.started", {"tool_call_id": tool_call_id, "tool_name": self._spec.id,
            "tool_version": self._spec.version, "description": self._spec.description, "scope": scope})
        try:
            typed_input = self._spec.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            result = ToolResult(
                tool_id=self._spec.id,
                status=ToolCallStatus.INVALID_ARGUMENTS,
                error=validation_feedback(exc, arguments),
            )
            self._trace_finished(tool_call_id, result, started)
            return result

        try:
            execution = self._spec.handler(typed_input)
        except Exception as exc:
            result = ToolResult(tool_id=self._spec.id, status=ToolCallStatus.FAILED,
                error=ToolError(kind=type(exc).__name__, message=str(exc), phase="EXECUTION", scope=scope,
                    retryable=False if isinstance(exc, (ValueError, KeyError)) else None,
                    suggested_actions=["Use the specific error and current object scope to repair this operation. Check its persisted task state before retrying external work."]))
            self._trace_finished(tool_call_id, result, started)
            return result
        try:
            output = self._spec.output_model.model_validate(execution.output)
        except Exception as exc:
            error = ToolError(kind=type(exc).__name__, message=str(exc), phase="OUTPUT_VALIDATION", scope=scope,
                retryable=False, suggested_actions=["The handler already ran. Inspect its returned artifacts before repairing the output adapter; do not repeat the write."])
            if isinstance(exc, ValidationError):
                feedback = validation_feedback(exc, {})
                error.message = feedback.message
                error.details = [detail.model_copy(update={"path": "/output" + detail.path}) for detail in feedback.details]
            result = ToolResult(
                tool_id=self._spec.id,
                status=ToolCallStatus.FAILED,
                artifact_refs=list(execution.artifact_refs),
                receipt=execution.receipt,
                error=error,
            )
            self._trace_finished(tool_call_id, result, started)
            return result

        serialized = cast(dict[str, object], output.model_dump(mode="json"))
        receipt = execution.receipt
        if receipt is None and self._spec.effect != ToolEffect.READ:
            receipt = write_receipt(serialized, execution.artifact_refs)
        result = ToolResult(
            tool_id=self._spec.id,
            status=ToolCallStatus.SUCCEEDED,
            output=serialized,
            artifact_refs=list(execution.artifact_refs),
            warnings=list(execution.warnings),
            receipt=receipt,
            visual_attachments=list(execution.visual_attachments),
        )
        self._trace_finished(tool_call_id, result, started)
        return result

    def _trace_finished(self, tool_call_id: str, result: ToolResult, started: float) -> None:
        record_trace("tool.finished", {"tool_call_id": tool_call_id, "tool_name": self._spec.id,
            "status": result.status.value, "elapsed_seconds": round(time.monotonic() - started, 6),
            "error": result.error.message if result.error is not None else None,
            "error_details": result.error.model_dump(mode="json") if result.error is not None else None,
            "receipt": result.receipt.model_dump(mode="json") if result.receipt is not None else None})


class ToolRegistry:
    """A small registry shared by HTTP adapters and agent runtimes."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredToolLike] = {}

    def register(self, spec: ToolSpec[InputT, OutputT]) -> None:
        if spec.id in self._tools:
            raise ValueError(f"Tool {spec.id!r} is already registered")
        self._tools[spec.id] = _RegisteredTool(spec)

    def contains(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def describe(self, tool_id: str) -> ToolDescriptor:
        registered = self._tools.get(tool_id)
        if registered is None:
            raise ToolNotFoundError(tool_id)
        return registered.descriptor()

    def list(
        self,
        *,
        category: ToolCategory | None = None,
        effect: ToolEffect | None = None,
        tags: frozenset[str] = frozenset(),
    ) -> list[ToolDescriptor]:
        descriptors = [tool.descriptor() for tool in self._tools.values()]
        return sorted(
            (
                descriptor
                for descriptor in descriptors
                if (category is None or descriptor.category == category)
                and (effect is None or descriptor.effect == effect)
                and tags.issubset(descriptor.tags)
            ),
            key=lambda descriptor: descriptor.id,
        )

    def invoke(
        self,
        tool_id: str,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        registered = self._tools.get(tool_id)
        if registered is None:
            tool_call_id = f"tool-{uuid4().hex[:12]}"
            record_trace("tool.started", {"tool_call_id": tool_call_id, "tool_name": tool_id,
                                          "scope": argument_scope(arguments, nested=True)})
            result = ToolResult(
                tool_id=tool_id,
                status=ToolCallStatus.NOT_FOUND,
                error=ToolError(kind="ToolNotFoundError", message=tool_id, phase="LOOKUP", retryable=False, scope=argument_scope(arguments, nested=True),
                    suggested_actions=["Discover an existing Tool ID and retry only with that registered identifier."]),
            )
            record_trace("tool.finished", {"tool_call_id": tool_call_id, "tool_name": tool_id,
                "status": result.status.value, "error": result.error.message if result.error else None,
                "error_details": result.error.model_dump(mode="json") if result.error else None})
            return result
        return registered.invoke(arguments)
