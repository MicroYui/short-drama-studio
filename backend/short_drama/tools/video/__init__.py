"""Video generation, selection, comparison, and local rework adapters."""

from __future__ import annotations

from typing import Literal, Protocol

from short_drama.video_edit_models import LipSyncResponse

from short_drama.media_frame_asset import TakeFrameAssetRequest, TakeFrameAssetResponse

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
from short_drama.studio_models import StudioTakeSelectRequest
from short_drama.tools.video.selection_receipt import take_selection_receipt


class VideoToolService(Protocol):
    def get_lip_sync(self, project_id: str, run_id: str) -> LipSyncResponse: ...

    def extract_take_frame(self, project_id: str, request: TakeFrameAssetRequest) -> TakeFrameAssetResponse: ...

    def enqueue(self, project_id: str, request: EnqueueRequest) -> ProjectAggregate: ...

    def select_take(self, take_id: str, *, shot_ids: list[str] | None = None,
                    clip_ids: list[str] | None = None, expected_editorial_revision: int | None = None,
                    source_in_seconds: float | None = None, source_out_seconds: float | None = None,
                    audio_policy: Literal["REPLACE_LINKED_AUDIO", "KEEP_CURRENT_AUDIO"] = "REPLACE_LINKED_AUDIO") -> ProjectAggregate: ...

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


class VideoLipSyncRunInput(ToolModel):
    project_id: str
    run_id: str


class VideoEnqueueInput(ToolModel):
    project_id: str
    request: EnqueueRequest


class VideoExtractFrameInput(ToolModel):
    project_id: str
    request: TakeFrameAssetRequest


class VideoSelectTakeInput(StudioTakeSelectRequest):
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
    registry.register(ToolSpec(id="video.get_lip_sync", version=1, category=ToolCategory.VIDEO,
        description=("Read only a historical lip-sync run, submission state, candidate and adoption result. "
            "No model call or state change. Current video generation uses MiniMax H3; Kling execution is unavailable."),
        effect=ToolEffect.READ, idempotent=True, input_model=VideoLipSyncRunInput, output_model=LipSyncResponse,
        handler=lambda request: ToolExecution(output=service.get_lip_sync(request.project_id, request.run_id)),
        tags=frozenset({"video", "lip_sync", "read"})))

    def extract_frame(request: VideoExtractFrameInput) -> ToolExecution[TakeFrameAssetResponse]:
        result = service.extract_take_frame(request.project_id, request.request)
        return ToolExecution(output=result, artifact_refs=(
            ArtifactRef(project_id=result.project_id, artifact_type="asset", artifact_id=result.asset_id),
            ArtifactRef(project_id=result.project_id, artifact_type="asset_version", artifact_id=result.asset_version_id),
            ArtifactRef(project_id=result.project_id, artifact_type="take", artifact_id=result.source_take_id),
        ))

    registry.register(ToolSpec(
        id="video.extract_frame", version=1, category=ToolCategory.VIDEO,
        description=(
            "Extract a full-resolution actual source frame from a Take and save a usable image asset. "
            "time_seconds is source time, or local time with shot_id/clip_id; exact clip uses preserve "
            "trim/speed/reverse mapping. Returns actual PTS plus ready image/H3 reference inputs. "
            "Use it to derive a new composition with image.enqueue, or as a soft H3 reference anchor; "
            "a hard first-frame route must be chosen explicitly. No model call, quality approval, "
            "film compositing or Take selection occurs. Repeats reuse an unchanged extracted source."
        ), effect=ToolEffect.WRITE, idempotent=True,
        input_model=VideoExtractFrameInput, output_model=TakeFrameAssetResponse,
        handler=extract_frame, tags=frozenset({"video", "frame", "image", "reference", "continuity"}),
    ))

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
        project = service.select_take(request.take_id, shot_ids=request.shot_ids,
            clip_ids=request.clip_ids, expected_editorial_revision=request.expected_editorial_revision,
            source_in_seconds=request.source_in_seconds, source_out_seconds=request.source_out_seconds, audio_policy=request.audio_policy)
        project_id = project.document.project.id
        refs = (ArtifactRef(project_id=project_id, artifact_type="take", artifact_id=request.take_id),)
        return ToolExecution(
            output=project,
            artifact_refs=refs,
            receipt=take_selection_receipt(project, request.take_id, request, refs),
        )

    registry.register(
        ToolSpec(
            id="video.select_take",
            version=4,
            category=ToolCategory.VIDEO,
            description=("Adopt a usable Take for its shot, or use clip_ids to replace only specific current uses. "
                "For an observed candidate interval, supply source_in_seconds/source_out_seconds, exactly one clip_id "
                "and expected_editorial_revision. The source span must equal that clip's duration × speed. "
                "Preserves film position, duration, user controls, subtitles and unselected uses; replaces only the "
                "selected video's source and its linked native sound by default. With audio_policy=KEEP_CURRENT_AUDIO, "
                "retain all current sound locally and mute the new picture; an unsplit native source becomes a linked audio clip. "
                "PICTURE_ONLY candidates require this policy and their bound target clip. Align changed dialogue captions separately "
                "with timeline.edit_clips after observing the actual candidate audio. The receipt reports the saved "
                "editorial revision and actual adopted clip/source intervals and native sound bindings; an unchanged "
                "revision can acknowledge an already identical adoption. An observed, usable historical Take may be "
                "explicitly selected for its current shot: STALE records changed generation inputs, remains historical "
                "after adoption, and does not itself prohibit reuse. Selection does not establish quality review or export."),
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
            warnings=tuple([*result.report.warnings,
                "视频观察没有听审。音频片段可独立播放；已采用的原声可用 audio.transcribe_native、audio.align_subtitles 或 audio.inspect_manifest 分析，所用坐标以各工具合同为准。"]),
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
            version=4,
            category=ToolCategory.VIDEO,
            description=(
                "Inspect a generated Take or explicit source_in/source_out interval; persist visual observations, exact scope and source revisions. "
                "For the current edit or neighboring cut use scope=EDITORIAL with timeline_id/clip_id from timeline.read_editorial. "
                "EDITORIAL resolves the selected clip's trim/speed/reverse and actual predecessor; omit source_in/out and previous_take_id. "
                "Multiple uses require a clip ID; unresolved or overlapping uses are reported instead of guessed. "
                "Default scope=TAKE preserves original-footage observation and source-time boundaries, not the current edit. "
                "Optional dense_sample_fps (up to 12) observes an explicit <=3s interval with at most 36 actual timestamped frames. "
                "keyframe_times_seconds requests up to four detail images (keyframe_max_dimension=null preserves original pixels). "
                "include_audio_excerpt saves a separate actual PCM excerpt of the same <=3s source interval for playback or independent audio analysis. "
                "This tool does not listen to speech or provide dialogue timestamps. "
                "Reuse a report only when its recorded range, focus and current sources match; COMPLETE alone does not establish whole-group coverage. "
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
            version=2,
            category=ToolCategory.VIDEO,
            description=(
                "Compare candidates covering any requested shot: scope=GENERATION_UNIT compares matching complete groups; "
                "scope=SHOT compares only the corresponding source windows. Reuses only current matching visual evidence. "
                "Does not assess dialogue or audio. Optionally apply the selection."
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
