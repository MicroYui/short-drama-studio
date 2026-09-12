"""Agent delegation ToolSpec and its runtime adapter contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field, JsonValue

from short_drama.agent_runtime import ToolAttemptRef

from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class AgentDelegateInput(ToolModel):
    recipient_role: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=8000)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    constraints: list[str] = Field(default_factory=list[str], max_length=64)
    expected_output: str = Field(min_length=1, max_length=4000)


class AgentDelegateOutput(ToolModel):
    delegation_id: str
    accepted: bool
    status: str
    summary: str | None = None


class AgentDelegator(Protocol):
    def delegate(self, request: AgentDelegateInput) -> AgentDelegateOutput: ...


class ToolAttemptPayload(StrEnum):
    ARGUMENTS = "ARGUMENTS"
    RESULT = "RESULT"
    BOTH = "BOTH"


class AgentGetToolAttemptInput(ToolModel):
    attempt_ref: ToolAttemptRef
    payload: ToolAttemptPayload


class AgentGetToolAttemptOutput(ToolModel):
    attempt_ref: ToolAttemptRef
    reason: str
    arguments: dict[str, JsonValue] | None = None
    result: dict[str, JsonValue] | None = None


class AgentToolAttemptReader(Protocol):
    def get_tool_attempt(
        self,
        request: AgentGetToolAttemptInput,
    ) -> AgentGetToolAttemptOutput: ...


class AgentReadContextInput(ToolModel):
    project_id: str
    run_id: str
    snapshot_id: str
    pointer: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=6000, ge=1, le=12000)


class AgentReadContextOutput(ToolModel):
    snapshot_id: str
    pointer: str
    content: str
    offset: int
    total_characters: int
    next_offset: int | None
    keys: list[str] = Field(default_factory=list[str])
    item_count: int | None = None
    note: str = "保存时的精确快照；修改前仍需读取当前项目内容。"


class AgentContextReader(Protocol):
    def read_context(self, request: AgentReadContextInput) -> AgentReadContextOutput: ...


def register_context_tools(registry: ToolRegistry, reader: AgentContextReader) -> None:
    def read(request: AgentReadContextInput) -> ToolExecution[AgentReadContextOutput]:
        return ToolExecution(output=reader.read_context(request))

    registry.register(ToolSpec(
        id="agent.read_context", version=1, category=ToolCategory.AGENT,
        description=("Read an exact saved context block by snapshot_id and JSON pointer. "
                     "Use deeper pointers to select only needed fields or list items; "
                     "offset/limit pages long text and next_offset indicates remaining content."),
        effect=ToolEffect.READ, idempotent=True, input_model=AgentReadContextInput,
        output_model=AgentReadContextOutput, handler=read,
        tags=frozenset({"agent", "context", "read", "on-demand-payload"}),
    ))


def register_tools(registry: ToolRegistry, delegator: AgentDelegator) -> None:
    def delegate(request: AgentDelegateInput) -> ToolExecution[AgentDelegateOutput]:
        result = delegator.delegate(request)
        return ToolExecution(output=result, artifact_refs=tuple(request.artifact_refs))

    registry.register(
        ToolSpec(
            id="agent.delegate",
            version=1,
            category=ToolCategory.AGENT,
            description="Delegate a bounded objective to a registered specialist Agent.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AgentDelegateInput,
            output_model=AgentDelegateOutput,
            handler=delegate,
            tags=frozenset({"agent", "delegation"}),
        )
    )


def register_tool_attempt_tools(
    registry: ToolRegistry,
    reader: AgentToolAttemptReader,
) -> None:
    def get_tool_attempt(
        request: AgentGetToolAttemptInput,
    ) -> ToolExecution[AgentGetToolAttemptOutput]:
        return ToolExecution(output=reader.get_tool_attempt(request))

    registry.register(
        ToolSpec(
            id="agent.get_tool_attempt",
            version=1,
            category=ToolCategory.AGENT,
            description=(
                "Read the exact arguments, result, or both from a persisted Tool "
                "attempt produced by this run. The Leader may read its Workers' "
                "attempts; a Worker uses its own or explicitly handed references. Use it to continue "
                "from a failed draft without reconstructing the draft from summaries, or retrieve exact "
                "saved local image/layout parameters that are not visible in the current working set. "
                "Use the actual attempt_ref from the handoff; do not inspect the image again just to recover saved parameters."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AgentGetToolAttemptInput,
            output_model=AgentGetToolAttemptOutput,
            handler=get_tool_attempt,
            tags=frozenset(
                {
                    "agent",
                    "handoff",
                    "tool-attempt",
                    "read",
                    "on-demand-payload",
                }
            ),
        )
    )


__all__ = [
    "AgentDelegateInput",
    "AgentDelegateOutput",
    "AgentDelegator",
    "AgentGetToolAttemptInput",
    "AgentGetToolAttemptOutput",
    "AgentToolAttemptReader",
    "ToolAttemptPayload",
    "register_tool_attempt_tools",
    "register_tools",
]
