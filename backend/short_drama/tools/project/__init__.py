"""Project-level tool adapters."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from short_drama.models import CreateProjectRequest, ProjectAggregate, ProjectListResponse
from short_drama.production_contracts import (
    OutputSlotBindRequest,
    OutputSlotListResponse,
    OutputSlotMutationResponse,
    OutputSlotReadResponse,
)
from short_drama.tools.common import EmptyToolInput
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class ProjectToolService(Protocol):
    def list_projects(self) -> ProjectListResponse: ...

    def get_project(self, project_id: str) -> ProjectAggregate: ...

    def create_project(
        self,
        request: CreateProjectRequest,
        *,
        project_id: str | None = None,
        owner_agent_run_id: str | None = None,
    ) -> ProjectAggregate: ...

    def list_output_slots(self, project_id: str) -> OutputSlotListResponse: ...

    def get_output_slot(
        self,
        project_id: str,
        slot_id: str,
    ) -> OutputSlotReadResponse: ...

    def bind_output_slot(
        self,
        project_id: str,
        request: OutputSlotBindRequest,
    ) -> OutputSlotMutationResponse: ...


class ProjectGetInput(ToolModel):
    project_id: str


class ProjectCreateInput(ToolModel):
    request: CreateProjectRequest
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    owner_agent_run_id: str | None = Field(default=None, min_length=1, max_length=200)


class OutputSlotListInput(ToolModel):
    project_id: str


class OutputSlotGetInput(ToolModel):
    project_id: str
    slot_id: str = Field(min_length=1, max_length=200)


class OutputSlotBindInput(ToolModel):
    project_id: str
    request: OutputSlotBindRequest


def _output_slot_refs(
    project_id: str,
    result: OutputSlotReadResponse,
) -> tuple[ArtifactRef, ...]:
    slot = result.output_slot
    refs = [
        ArtifactRef(
            project_id=project_id,
            artifact_type="output_slot",
            artifact_id=slot.id,
            version=slot.revision,
        )
    ]
    if slot.current_artifact_ref is not None:
        refs.append(
            ArtifactRef(
                project_id=slot.current_artifact_ref.project_id,
                artifact_type=slot.current_artifact_ref.artifact_type,
                artifact_id=slot.current_artifact_ref.artifact_id,
                version=slot.current_artifact_ref.version,
            )
        )
    return tuple(refs)


def register_tools(registry: ToolRegistry, service: ProjectToolService) -> None:
    registry.register(
        ToolSpec(
            id="project.list",
            version=1,
            category=ToolCategory.PROJECT,
            description="List the projects available in the canonical project store.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=EmptyToolInput,
            output_model=ProjectListResponse,
            handler=lambda _request: ToolExecution(output=service.list_projects()),
            tags=frozenset({"discovery", "project"}),
        )
    )

    def list_output_slots(
        request: OutputSlotListInput,
    ) -> ToolExecution[OutputSlotListResponse]:
        result = service.list_output_slots(request.project_id)
        refs = tuple(
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="output_slot",
                artifact_id=slot.id,
                version=slot.revision,
            )
            for slot in result.output_slots
        )
        return ToolExecution(output=result, artifact_refs=refs)

    registry.register(
        ToolSpec(
            id="project.list_output_slots",
            version=1,
            category=ToolCategory.PROJECT,
            description=(
                "列出项目内由语义用途命名的稳定输出槽。输出槽只指向当前产物，"
                "不会复制 ProductionSpec、Storyboard 或 GenerationPlan 内容。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=OutputSlotListInput,
            output_model=OutputSlotListResponse,
            handler=list_output_slots,
            tags=frozenset({"output-slot", "handoff", "discovery"}),
        )
    )

    def get_output_slot(
        request: OutputSlotGetInput,
    ) -> ToolExecution[OutputSlotReadResponse]:
        result = service.get_output_slot(request.project_id, request.slot_id)
        return ToolExecution(
            output=result,
            artifact_refs=_output_slot_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="project.get_output_slot",
            version=1,
            category=ToolCategory.PROJECT,
            description=(
                "读取一个稳定输出槽的当前精确 ArtifactRef、历史绑定和失效原因，"
                "供不同 Agent 按用途交接而不猜测文件名。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=OutputSlotGetInput,
            output_model=OutputSlotReadResponse,
            handler=get_output_slot,
            tags=frozenset({"output-slot", "handoff", "read"}),
        )
    )

    def bind_output_slot(
        request: OutputSlotBindInput,
    ) -> ToolExecution[OutputSlotMutationResponse]:
        result = service.bind_output_slot(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_output_slot_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="project.bind_output_slot",
            version=1,
            category=ToolCategory.PROJECT,
            description=(
                "把 Agent 已确认的精确 ArtifactRef 绑定到语义输出槽；再次绑定会保留"
                "旧引用。expected_revision 用于避免覆盖同一槽的新交接结果。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=OutputSlotBindInput,
            output_model=OutputSlotMutationResponse,
            handler=bind_output_slot,
            tags=frozenset({"output-slot", "handoff", "versioned"}),
        )
    )

    def get_project(request: ProjectGetInput) -> ToolExecution[ProjectAggregate]:
        project = service.get_project(request.project_id)
        return ToolExecution(
            output=project,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="project.get",
            version=1,
            category=ToolCategory.PROJECT,
            description=(
                "Load the project's current aggregate. The specialist model view retains current/approved media and "
                "exact versions selected or produced by this WorkOrder, without unrelated version history. "
                "For exact parameters or a large result from a known Tool attempt, prefer agent.get_tool_attempt "
                "with its actual attempt_ref; artifact.read_current is a content index, not a media recipe reader."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProjectGetInput,
            output_model=ProjectAggregate,
            handler=get_project,
            tags=frozenset({"context", "project"}),
        )
    )

    def create_project(request: ProjectCreateInput) -> ToolExecution[ProjectAggregate]:
        project = service.create_project(
            request.request,
            project_id=request.project_id,
            owner_agent_run_id=request.owner_agent_run_id,
        )
        project_id = project.document.project.id
        planning = project.document.planning
        artifact_refs = [
            ArtifactRef(
                project_id=project_id,
                artifact_type="project",
                artifact_id=project_id,
            )
        ]
        if planning is not None and planning.run_id is not None:
            artifact_refs.append(
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="content_planning_job",
                    artifact_id=planning.run_id,
                )
            )
        return ToolExecution(
            output=project,
            artifact_refs=tuple(artifact_refs),
            warnings=()
            if planning is None
            else tuple(dict.fromkeys(planning.warnings)),
        )

    registry.register(
        ToolSpec(
            id="project.create",
            version=1,
            category=ToolCategory.PROJECT,
            description="Create a project using the configured planning capabilities.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=ProjectCreateInput,
            output_model=ProjectAggregate,
            handler=create_project,
            tags=frozenset({"project", "planning"}),
        )
    )


__all__ = [
    "OutputSlotBindInput",
    "OutputSlotGetInput",
    "OutputSlotListInput",
    "ProjectCreateInput",
    "ProjectGetInput",
    "register_tools",
]
