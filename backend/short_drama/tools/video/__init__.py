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
    VideoInspectionLookupResponse,
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

    def get_take_video_inspection(
        self, take_id: str, report_id: str | None = None,
    ) -> VideoInspectionLookupResponse: ...

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


class VideoGetTakeInspectionInput(ToolModel):
    take_id: str
    report_id: str | None = None


class VideoCompareTakesInput(ToolModel):
    project_id: str
    request: TakeComparisonRequest


class VideoLocalReworkInput(ToolModel):
    project_id: str
    shot_id: str
    request: LocalReworkRequest


def register_tools(registry: ToolRegistry, service: VideoToolService) -> None:
    def get_inspection(request: VideoGetTakeInspectionInput) -> ToolExecution[VideoInspectionLookupResponse]:
        result = service.get_take_video_inspection(request.take_id, request.report_id)
        refs = [ArtifactRef(
            project_id=result.take.project_id, artifact_type="take", artifact_id=result.take.id,
        )]
        if result.report is not None:
            refs.append(ArtifactRef(
                project_id=result.take.project_id, artifact_type="video_inspection",
                artifact_id=result.report.id,
            ))
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(ToolSpec(
        id="video.get_take_inspection", version=1, category=ToolCategory.VIDEO,
        description=(
            "Read a Take and its latest persisted video inspection, or an exact report_id. "
            "A null report means no saved observation exists. Reuses evidence without "
            "sampling media, calling a model, changing QA or generating video."
        ),
        effect=ToolEffect.READ, idempotent=True,
        input_model=VideoGetTakeInspectionInput, output_model=VideoInspectionLookupResponse,
        handler=get_inspection, tags=frozenset({"inspection", "take", "read"}),
    ))

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
            description=(
                "Queue the calling Agent's explicit deterministic Mock candidate batch for "
                "selected project shots; "
                "real MiniMax-H3 runs use route.execute_h3_plan instead."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=VideoEnqueueInput,
            output_model=ProjectAggregate,
            handler=enqueue,
            tags=frozenset({"video", "generation", "task", "mock"}),
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
            description=(
                "Inspect a generated Take's technical metadata and sampled video frames; persist visual observations. "
                "This tool does not listen to speech or provide dialogue timestamps. "
                "Reuse video.get_take_inspection when a relevant report already exists. "
                "After assembling current Takes, use audio.align_subtitles or audio.inspect_manifest for native audio."
            ),
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
            description=(
                "Compare the calling Agent's explicit same-shot candidate Take set and "
                "optionally apply the selection."
            ),
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
                tags=frozenset({"video", "shot", "local-rework", "mock"}),
            )
        )

    register_rework(
        tool_id="video.retake",
        operation=OperationKind.RETAKE,
        description="Queue a deterministic Mock replacement candidate for one shot.",
    )
    register_rework(
        tool_id="video.extend",
        operation=OperationKind.EXTEND,
        description="Queue a deterministic Mock extension candidate for one shot.",
    )
    register_rework(
        tool_id="video.gap_fill",
        operation=OperationKind.GAP_FILL,
        description="Queue a deterministic Mock gap-fill candidate for one shot.",
    )


__all__ = [
    "VideoCompareTakesInput",
    "VideoEnqueueInput",
    "VideoInspectTakeInput",
    "VideoLocalReworkInput",
    "VideoSelectTakeInput",
    "register_tools",
]
