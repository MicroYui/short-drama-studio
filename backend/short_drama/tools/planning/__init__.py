"""Persisted content-planning tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import PlanningRunResponse
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class PlanningToolService(Protocol):
    def get_planning_run(self, run_id: str) -> PlanningRunResponse: ...

    def advance_content_planning_job(
        self,
        job_id: str,
        *,
        owner_agent_run_id: str,
    ) -> PlanningRunResponse: ...

    def start_script_revision(
        self,
        project_id: str,
        *,
        owner_agent_run_id: str,
        instruction: str,
        base_script_version_id: str,
    ) -> PlanningRunResponse: ...


class PlanningAdvanceInput(ToolModel):
    project_id: str
    run_id: str
    owner_agent_run_id: str


class PlanningStartScriptRevisionInput(ToolModel):
    project_id: str
    owner_agent_run_id: str
    instruction: str
    base_script_version_id: str


def register_tools(registry: ToolRegistry, service: PlanningToolService) -> None:
    def start_script_revision(
        request: PlanningStartScriptRevisionInput,
    ) -> ToolExecution[PlanningRunResponse]:
        response = service.start_script_revision(
            request.project_id,
            owner_agent_run_id=request.owner_agent_run_id,
            instruction=request.instruction,
            base_script_version_id=request.base_script_version_id,
        )
        return ToolExecution(
            output=response,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="content_planning_job",
                    artifact_id=response.run.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                ),
            ),
            warnings=tuple(dict.fromkeys(response.run.warnings)),
        )

    registry.register(
        ToolSpec(
            id="planning.start_script_revision",
            version=1,
            category=ToolCategory.PLANNING,
            description=(
                "Start a resumable screenplay revision for the current project. "
                "It preserves the original brief and current Script/Bible, then lets "
                "the Leader decide scene patches, all-scene rewriting, or structural "
                "re-architecture from the full user instruction."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=PlanningStartScriptRevisionInput,
            output_model=PlanningRunResponse,
            handler=start_script_revision,
            tags=frozenset(
                {
                    "planning",
                    "script-revision",
                    "agent-runtime",
                    "resumable",
                }
            ),
        )
    )

    def advance_planning(
        request: PlanningAdvanceInput,
    ) -> ToolExecution[PlanningRunResponse]:
        current = service.get_planning_run(request.run_id)
        if current.run.project_id != request.project_id:
            raise ValueError(
                "Content planning job does not belong to the requested project"
            )
        if current.run.owner_agent_run_id != request.owner_agent_run_id:
            raise ValueError(
                "Content planning job does not belong to the requesting AgentRun"
            )

        response = service.advance_content_planning_job(
            request.run_id,
            owner_agent_run_id=request.owner_agent_run_id,
        )
        if response.run.project_id != request.project_id:
            raise ValueError(
                "Advanced content planning job no longer belongs to the requested project"
            )

        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="content_planning_job",
                artifact_id=request.run_id,
            )
        ]
        if response.project is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                )
            )
        return ToolExecution(
            output=response,
            artifact_refs=tuple(refs),
            warnings=tuple(dict.fromkeys(response.run.warnings)),
        )

    registry.register(
        ToolSpec(
            id="planning.advance",
            version=1,
            category=ToolCategory.PLANNING,
            description=(
                "Advance one bounded unit of an AgentRun-owned Content Planning Job "
                "and return its persisted cursor."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=PlanningAdvanceInput,
            output_model=PlanningRunResponse,
            handler=advance_planning,
            tags=frozenset(
                {"planning", "content-planning-job", "agent-runtime", "resumable"}
            ),
        )
    )


__all__ = [
    "PlanningAdvanceInput",
    "PlanningStartScriptRevisionInput",
    "register_tools",
]
