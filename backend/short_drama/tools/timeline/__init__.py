"""Timeline assembly tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import ExportRecord, ExportRequest, TimelineManifest
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class TimelineToolService(Protocol):
    def rebuild_manifest(self, project_id: str) -> TimelineManifest: ...

    def export_project(
        self,
        project_id: str,
        rebuild_manifest: bool = True,
    ) -> ExportRecord: ...


class TimelineRebuildInput(ToolModel):
    project_id: str


class TimelineExportInput(ToolModel):
    project_id: str
    request: ExportRequest


def register_tools(registry: ToolRegistry, service: TimelineToolService) -> None:
    def rebuild(request: TimelineRebuildInput) -> ToolExecution[TimelineManifest]:
        manifest = service.rebuild_manifest(request.project_id)
        return ToolExecution(
            output=manifest,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="timeline_manifest",
                    artifact_id=manifest.id,
                    version=manifest.version,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="timeline.rebuild",
            version=1,
            category=ToolCategory.TIMELINE,
            description="Build a new timeline manifest from current selected Takes.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=TimelineRebuildInput,
            output_model=TimelineManifest,
            handler=rebuild,
            tags=frozenset({"assembly", "timeline"}),
        )
    )

    def export(request: TimelineExportInput) -> ToolExecution[ExportRecord]:
        result = service.export_project(
            request.project_id,
            request.request.rebuild_manifest,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="export",
                    artifact_id=result.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="timeline.export",
            version=1,
            category=ToolCategory.TIMELINE,
            description="Render the current project timeline to an export artifact.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=TimelineExportInput,
            output_model=ExportRecord,
            handler=export,
            tags=frozenset({"export", "render", "timeline"}),
        )
    )


__all__ = ["TimelineExportInput", "TimelineRebuildInput", "register_tools"]
