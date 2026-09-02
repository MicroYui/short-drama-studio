"""Video generation, selection, comparison, and local rework adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import (
    EnqueueRequest,
    GenerationTask,
    LocalReworkRequest,
    OperationKind,
    ProjectAggregate,
    TakeComparisonRequest,
    TakeComparisonResponse,
    VideoInspectionRequest,
    VideoInspectionResponse,
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


class VideoToolService(Protocol):
    def enqueue(self, project_id: str, request: EnqueueRequest) -> ProjectAggregate: ...

    def select_take(self, take_id: str) -> ProjectAggregate: ...

    def inspect_take_video(
        self,
        take_id: str,
        request: VideoInspectionRequest,
    ) -> VideoInspectionResponse: ...

    def compare_takes(
        self,
        project_id: str,
        request: TakeComparisonRequest,
    ) -> TakeComparisonResponse: ...

    def enqueue_local_rework(
        self,
        project_id: str,
        shot_id: str,
        operation: OperationKind,
        request: LocalReworkRequest,
    ) -> GenerationTask: ...


class VideoEnqueueInput(ToolModel):
    project_id: str
    request: EnqueueRequest


class VideoSelectTakeInput(ToolModel):
    take_id: str


class VideoInspectTakeInput(ToolModel):
    take_id: str
    request: VideoInspectionRequest


class VideoCompareTakesInput(ToolModel):
    project_id: str
    request: TakeComparisonRequest


class VideoLocalReworkInput(ToolModel):
    project_id: str
    shot_id: str
    request: LocalReworkRequest


def register_tools(registry: ToolRegistry, service: VideoToolService) -> None:
    def enqueue(request: VideoEnqueueInput) -> ToolExecution[ProjectAggregate]:
        project = service.enqueue(request.project_id, request.request)
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
            id="video.enqueue",
            version=1,
            category=ToolCategory.VIDEO,
            description="Queue candidate video generation tasks for selected project shots.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=VideoEnqueueInput,
            output_model=ProjectAggregate,
            handler=enqueue,
            tags=frozenset({"video", "generation", "task"}),
        )
    )

    def select(request: VideoSelectTakeInput) -> ToolExecution[ProjectAggregate]:
        project = service.select_take(request.take_id)
        project_id = project.document.project.id
        return ToolExecution(
            output=project,
            artifact_refs=(
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="take",
                    artifact_id=request.take_id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="video.select_take",
            version=1,
            category=ToolCategory.VIDEO,
            description="Select one current Take for its shot.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=VideoSelectTakeInput,
            output_model=ProjectAggregate,
            handler=select,
            tags=frozenset({"video", "take", "selection"}),
        )
    )

    def inspect(request: VideoInspectTakeInput) -> ToolExecution[VideoInspectionResponse]:
        result = service.inspect_take_video(request.take_id, request.request)
        project_id = result.project.document.project.id
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="video_inspection",
                    artifact_id=result.report.id,
                ),
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="take",
                    artifact_id=request.take_id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="video.inspect_take",
            version=1,
            category=ToolCategory.VIDEO,
            description="Inspect a generated Take and persist its observation report.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=VideoInspectTakeInput,
            output_model=VideoInspectionResponse,
            handler=inspect,
            tags=frozenset({"inspection", "take", "video"}),
        )
    )

    def compare(request: VideoCompareTakesInput) -> ToolExecution[TakeComparisonResponse]:
        result = service.compare_takes(request.project_id, request.request)
        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="take_comparison",
                artifact_id=result.report.id,
            )
        ]
        refs.extend(
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="take",
                artifact_id=take_id,
            )
            for take_id in result.report.candidate_take_ids
        )
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="video.compare_takes",
            version=1,
            category=ToolCategory.VIDEO,
            description="Compare current candidate Takes and optionally apply the selection.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=VideoCompareTakesInput,
            output_model=TakeComparisonResponse,
            handler=compare,
            tags=frozenset({"video", "take", "comparison"}),
        )
    )

    def register_rework(
        *,
        tool_id: str,
        operation: OperationKind,
        description: str,
    ) -> None:
        def enqueue_rework(
            request: VideoLocalReworkInput,
        ) -> ToolExecution[GenerationTask]:
            task = service.enqueue_local_rework(
                request.project_id,
                request.shot_id,
                operation,
                request.request,
            )
            return ToolExecution(
                output=task,
                artifact_refs=(
                    ArtifactRef(
                        project_id=request.project_id,
                        artifact_type="generation_task",
                        artifact_id=task.id,
                    ),
                    ArtifactRef(
                        project_id=request.project_id,
                        artifact_type="shot",
                        artifact_id=request.shot_id,
                    ),
                ),
            )

        registry.register(
            ToolSpec(
                id=tool_id,
                version=1,
                category=ToolCategory.VIDEO,
                description=description,
                effect=ToolEffect.WRITE,
                idempotent=False,
                input_model=VideoLocalReworkInput,
                output_model=GenerationTask,
                handler=enqueue_rework,
                tags=frozenset({"video", "shot", "local-rework"}),
            )
        )

    register_rework(
        tool_id="video.retake",
        operation=OperationKind.RETAKE,
        description="Queue a replacement candidate for one shot.",
    )
    register_rework(
        tool_id="video.extend",
        operation=OperationKind.EXTEND,
        description="Queue a local extension candidate for one shot.",
    )
    register_rework(
        tool_id="video.gap_fill",
        operation=OperationKind.GAP_FILL,
        description="Queue a local gap-fill candidate for one shot.",
    )


__all__ = [
    "VideoCompareTakesInput",
    "VideoEnqueueInput",
    "VideoInspectTakeInput",
    "VideoLocalReworkInput",
    "VideoSelectTakeInput",
    "register_tools",
]
