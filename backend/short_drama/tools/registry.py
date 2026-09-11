"""In-process discovery and invocation registry for typed tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, Protocol, cast
from uuid import uuid4

from pydantic import ValidationError
from short_drama.studio_trace import record_trace

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

    def descriptor(self) -> ToolDescriptor:
        return self._spec.descriptor()

    def invoke(self, arguments: Mapping[str, object]) -> ToolResult:
        try:
            typed_input = self._spec.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            return ToolResult(
                tool_id=self._spec.id,
                status=ToolCallStatus.INVALID_ARGUMENTS,
                error=ToolError(kind="ValidationError", message=str(exc)),
            )

        tool_call_id = f"tool-{uuid4().hex[:12]}"
        try:
            raw = typed_input.model_dump(mode="json")
            scope = {key: value for key, value in raw.items() if key in {"scene_id", "shot_id", "asset_id", "work_order_id", "project_id"}}
            record_trace("tool.started", {"tool_call_id": tool_call_id, "tool_name": self._spec.id, "description": self._spec.description, "scope": scope})
            execution = self._spec.handler(typed_input)
            output = self._spec.output_model.model_validate(execution.output)
        except Exception as exc:
            record_trace("tool.finished", {"tool_call_id": tool_call_id, "tool_name": self._spec.id, "status": "FAILED", "error": str(exc)})
            return ToolResult(
                tool_id=self._spec.id,
                status=ToolCallStatus.FAILED,
                error=ToolError(kind=type(exc).__name__, message=str(exc)),
            )

        serialized = cast(dict[str, object], output.model_dump(mode="json"))
        record_trace("tool.finished", {"tool_call_id": tool_call_id, "tool_name": self._spec.id, "status": "SUCCEEDED"})
        return ToolResult(
            tool_id=self._spec.id,
            status=ToolCallStatus.SUCCEEDED,
            output=serialized,
            artifact_refs=list(execution.artifact_refs),
            warnings=list(execution.warnings),
        )


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
            return ToolResult(
                tool_id=tool_id,
                status=ToolCallStatus.NOT_FOUND,
                error=ToolError(kind="ToolNotFoundError", message=tool_id),
            )
        return registered.invoke(arguments)
