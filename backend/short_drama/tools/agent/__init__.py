"""Agent delegation ToolSpec and its runtime adapter contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

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


__all__ = [
    "AgentDelegateInput",
    "AgentDelegateOutput",
    "AgentDelegator",
    "register_tools",
]
