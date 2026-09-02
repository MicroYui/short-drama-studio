"""Script editing tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import ProjectAggregate, ReviseScriptLinesRequest
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class ScriptToolService(Protocol):
    def revise_script_lines(
        self,
        project_id: str,
        request: ReviseScriptLinesRequest,
    ) -> ProjectAggregate: ...


class ScriptReviseLinesInput(ToolModel):
    project_id: str
    request: ReviseScriptLinesRequest


def register_tools(registry: ToolRegistry, service: ScriptToolService) -> None:
    def revise_lines(request: ScriptReviseLinesInput) -> ToolExecution[ProjectAggregate]:
        result = service.revise_script_lines(request.project_id, request.request)
        current_script = max(
            result.document.script_versions,
            key=lambda version: version.version,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="script_version",
                    artifact_id=current_script.id,
                    version=current_script.version,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="script.revise_lines",
            version=1,
            category=ToolCategory.SCRIPT,
            description="Create a new script version by revising selected dialogue lines.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ScriptReviseLinesInput,
            output_model=ProjectAggregate,
            handler=revise_lines,
            tags=frozenset({"script", "partial-edit"}),
        )
    )


__all__ = ["ScriptReviseLinesInput", "register_tools"]
