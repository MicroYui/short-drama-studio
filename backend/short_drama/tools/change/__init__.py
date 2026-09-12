"""Dependency-aware semantic change Tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.change_executor import (
    SemanticChangeApplyRequest,
    SemanticChangeApplyResponse,
)
from short_drama.change_planner import SemanticChangePlan, SemanticChangePlanRequest
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class SemanticChangeToolService(Protocol):
    def preview_semantic_change(
        self,
        project_id: str,
        request: SemanticChangePlanRequest,
    ) -> SemanticChangePlan: ...

    def apply_semantic_change(
        self,
        project_id: str,
        request: SemanticChangeApplyRequest,
    ) -> SemanticChangeApplyResponse: ...


class ChangePreviewInput(ToolModel):
    project_id: str
    request: SemanticChangePlanRequest


class ChangeApplyInput(ToolModel):
    project_id: str
    request: SemanticChangeApplyRequest


def register_tools(registry: ToolRegistry, service: SemanticChangeToolService) -> None:
    def preview(request: ChangePreviewInput) -> ToolExecution[SemanticChangePlan]:
        plan = service.preview_semantic_change(request.project_id, request.request)
        return ToolExecution(
            output=plan,
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
            id="change.preview",
            version=1,
            category=ToolCategory.CHANGE,
            description=(
                "Plan the smallest dependency-aware artifact branch for a semantic edit."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=True,
            input_model=ChangePreviewInput,
            output_model=SemanticChangePlan,
            handler=preview,
            tags=frozenset({"change", "planning", "partial-edit"}),
        )
    )

    def apply(
        request: ChangeApplyInput,
    ) -> ToolExecution[SemanticChangeApplyResponse]:
        response = service.apply_semantic_change(request.project_id, request.request)
        refs: list[ArtifactRef] = []
        if response.result.script_version_id is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="script_version",
                    artifact_id=response.result.script_version_id,
                )
            )
        if response.result.bible_version_id is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="bible_version",
                    artifact_id=response.result.bible_version_id,
                )
            )
        refs.extend(
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="asset_version",
                artifact_id=version_id,
            )
            for version_id in response.result.asset_version_ids
        )
        refs.extend(
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="shot",
                artifact_id=shot_id,
            )
            for shot_id in response.result.invalidated_shot_ids
        )
        return ToolExecution(output=response, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="change.apply",
            version=1,
            category=ToolCategory.CHANGE,
            description=(
                "修改已有台词、场次、人物、场景、道具、资产或镜头的指定字段；不修改项目级标题、时长或画幅。"
                "Apply typed semantic patches and invalidate only affected downstream artifacts. To create a new scene use script.save_scene; "
                "to add named character/location/prop objects use artifact.create_assets."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ChangeApplyInput,
            output_model=SemanticChangeApplyResponse,
            handler=apply,
            tags=frozenset({"change", "mutation", "partial-edit"}),
        )
    )


__all__ = ["ChangeApplyInput", "ChangePreviewInput", "register_tools"]
