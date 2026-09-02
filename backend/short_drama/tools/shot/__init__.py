"""Shot planning and director-loop tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import ProjectAggregate
from short_drama.reference_planner import (
    DirectorAdviceRequest,
    DirectorAdviceResponse,
    DirectorLoopResponse,
    DirectorRepairDecisionRequest,
    KeyframeBriefCompileRequest,
    KeyframeBriefCompileResponse,
    ReferencePlanPreviewRequest,
    ReferencePlanPreviewResponse,
    ShotReferencePlanUpdateRequest,
)
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class ShotToolService(Protocol):
    def update_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> ProjectAggregate: ...

    def preview_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ReferencePlanPreviewRequest,
    ) -> ReferencePlanPreviewResponse: ...

    def compile_keyframe_brief(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeBriefCompileRequest,
    ) -> KeyframeBriefCompileResponse: ...

    def get_director_advice(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorAdviceResponse: ...

    def decide_director_repair(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorRepairDecisionRequest,
    ) -> ProjectAggregate: ...

    def run_director_loop(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorLoopResponse: ...


class ShotReferencePlanUpdateInput(ToolModel):
    project_id: str
    shot_id: str
    request: ShotReferencePlanUpdateRequest


class ShotReferencePlanPreviewInput(ToolModel):
    project_id: str
    shot_id: str
    request: ReferencePlanPreviewRequest


class ShotKeyframeBriefCompileInput(ToolModel):
    project_id: str
    shot_id: str
    request: KeyframeBriefCompileRequest


class ShotDirectorAdviceInput(ToolModel):
    project_id: str
    shot_id: str
    request: DirectorAdviceRequest


class ShotDirectorRepairInput(ToolModel):
    project_id: str
    shot_id: str
    request: DirectorRepairDecisionRequest


def _shot_ref(project_id: str, shot_id: str, revision: int | None = None) -> ArtifactRef:
    return ArtifactRef(
        project_id=project_id,
        artifact_type="shot",
        artifact_id=shot_id,
        version=revision,
    )


def register_tools(registry: ToolRegistry, service: ShotToolService) -> None:
    def update_plan(
        request: ShotReferencePlanUpdateInput,
    ) -> ToolExecution[ProjectAggregate]:
        result = service.update_shot_reference_plan(
            request.project_id,
            request.shot_id,
            request.request,
        )
        shot = next(item for item in result.document.shots if item.id == request.shot_id)
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, shot.id, shot.revision),),
        )

    registry.register(
        ToolSpec(
            id="shot.update_reference_plan",
            version=1,
            category=ToolCategory.SHOT,
            description="Update one shot's reference, action, and interaction plan.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=ShotReferencePlanUpdateInput,
            output_model=ProjectAggregate,
            handler=update_plan,
            tags=frozenset({"shot", "reference-plan", "partial-edit"}),
        )
    )

    def preview_plan(
        request: ShotReferencePlanPreviewInput,
    ) -> ToolExecution[ReferencePlanPreviewResponse]:
        result = service.preview_shot_reference_plan(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
            warnings=tuple(result.warnings),
        )

    registry.register(
        ToolSpec(
            id="shot.preview_reference_plan",
            version=1,
            category=ToolCategory.SHOT,
            description="Preview resolved shot references without mutating the project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ShotReferencePlanPreviewInput,
            output_model=ReferencePlanPreviewResponse,
            handler=preview_plan,
            tags=frozenset({"shot", "reference-plan", "preview"}),
        )
    )

    def compile_keyframe(
        request: ShotKeyframeBriefCompileInput,
    ) -> ToolExecution[KeyframeBriefCompileResponse]:
        result = service.compile_keyframe_brief(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
            warnings=tuple(result.warnings),
        )

    registry.register(
        ToolSpec(
            id="shot.compile_keyframe_brief",
            version=1,
            category=ToolCategory.SHOT,
            description="Compile a topology, start, or end keyframe brief for one shot.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ShotKeyframeBriefCompileInput,
            output_model=KeyframeBriefCompileResponse,
            handler=compile_keyframe,
            tags=frozenset({"shot", "keyframe", "planning"}),
        )
    )

    def director_advice(
        request: ShotDirectorAdviceInput,
    ) -> ToolExecution[DirectorAdviceResponse]:
        result = service.get_director_advice(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.director_advice",
            version=1,
            category=ToolCategory.SHOT,
            description="Ask the active format's director role for shot-specific advice.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=ShotDirectorAdviceInput,
            output_model=DirectorAdviceResponse,
            handler=director_advice,
            tags=frozenset({"shot", "director", "advice"}),
        )
    )

    def apply_director_repair(
        request: ShotDirectorRepairInput,
    ) -> ToolExecution[ProjectAggregate]:
        result = service.decide_director_repair(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.apply_director_repair",
            version=1,
            category=ToolCategory.SHOT,
            description="Apply or record a typed director repair decision for one shot.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ShotDirectorRepairInput,
            output_model=ProjectAggregate,
            handler=apply_director_repair,
            tags=frozenset({"shot", "director", "repair"}),
        )
    )

    def director_loop(
        request: ShotDirectorAdviceInput,
    ) -> ToolExecution[DirectorLoopResponse]:
        result = service.run_director_loop(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.run_director_loop",
            version=1,
            category=ToolCategory.SHOT,
            description="Run director advice and apply its proposed repair when available.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=ShotDirectorAdviceInput,
            output_model=DirectorLoopResponse,
            handler=director_loop,
            tags=frozenset({"shot", "director", "repair"}),
        )
    )


__all__ = [
    "ShotDirectorAdviceInput",
    "ShotDirectorRepairInput",
    "ShotKeyframeBriefCompileInput",
    "ShotReferencePlanPreviewInput",
    "ShotReferencePlanUpdateInput",
    "register_tools",
]
