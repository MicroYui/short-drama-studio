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


class PlanningAdvanceInput(ToolModel):
    project_id: str
    run_id: str
    owner_agent_run_id: str


def register_tools(registry: ToolRegistry, service: PlanningToolService) -> None:
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


__all__ = ["PlanningAdvanceInput", "register_tools"]
