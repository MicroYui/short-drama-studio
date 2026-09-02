"""Typed contracts shared by every agent-callable short-drama tool."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from short_drama.agent_runtime.models import ArtifactRef


class ToolModel(BaseModel):
    """Strict base model for registry inputs, outputs, and discovery payloads."""

    model_config = ConfigDict(extra="forbid")


class ToolCategory(StrEnum):
    PROJECT = "project"
    PLANNING = "planning"
    ARTIFACT = "artifact"
    SCRIPT = "script"
    SHOT = "shot"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TIMELINE = "timeline"
    QA = "qa"
    TASK = "task"
    ROUTE = "route"
    AGENT = "agent"
    CHANGE = "change"


class ToolEffect(StrEnum):
    """The broadest side effect an invocation may perform."""

    READ = "READ"
    WRITE = "WRITE"
    EXTERNAL = "EXTERNAL"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    FAILED = "FAILED"
    NOT_FOUND = "NOT_FOUND"


class ToolDescriptor(ToolModel):
    """Serializable discovery view of a registered ToolSpec."""

    id: str
    version: int
    category: ToolCategory
    description: str
    effect: ToolEffect
    idempotent: bool
    tags: list[str]
    input_schema: dict[str, object]
    output_schema: dict[str, object]


class ToolError(ToolModel):
    kind: str
    message: str


class ToolResult(ToolModel):
    """JSON-ready result returned at the heterogeneous registry boundary."""

    tool_id: str
    status: ToolCallStatus
    output: dict[str, object] | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    warnings: list[str] = Field(default_factory=list[str])
    error: ToolError | None = None


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class ToolExecution(Generic[OutputT]):
    """Typed handler result before it is converted to the registry envelope."""

    output: OutputT
    artifact_refs: tuple[ArtifactRef, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec(Generic[InputT, OutputT]):
    """Code-first executable specification for one agent-callable capability."""

    id: str
    version: int
    category: ToolCategory
    description: str
    effect: ToolEffect
    idempotent: bool
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: Callable[[InputT], ToolExecution[OutputT]]
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id.startswith(f"{self.category.value}."):
            raise ValueError(
                f"Tool id {self.id!r} must start with {self.category.value!r}"
            )
        if self.version < 1:
            raise ValueError("Tool version must be at least 1")
        if not self.description.strip():
            raise ValueError("Tool description must not be empty")

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id=self.id,
            version=self.version,
            category=self.category,
            description=self.description,
            effect=self.effect,
            idempotent=self.idempotent,
            tags=sorted(self.tags),
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )
