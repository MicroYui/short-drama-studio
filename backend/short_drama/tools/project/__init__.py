"""Project-level tool adapters."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from short_drama.models import CreateProjectRequest, ProjectAggregate, ProjectListResponse
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


class ProjectGetInput(ToolModel):
    project_id: str


class ProjectCreateInput(ToolModel):
    request: CreateProjectRequest
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    owner_agent_run_id: str | None = Field(default=None, min_length=1, max_length=200)


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
            description="Load the complete current aggregate for one project.",
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


__all__ = ["ProjectCreateInput", "ProjectGetInput", "register_tools"]
