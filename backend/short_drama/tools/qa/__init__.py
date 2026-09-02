"""Episode and pre-generation visual quality tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import EpisodeReviewRequest, EpisodeReviewResponse
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry
from short_drama.visual_input_quality import (
    InputCompatibilityRequest,
    InputCompatibilityResponse,
    KeyframeComparisonRequest,
    KeyframeComparisonResponse,
    ReferenceVisualRerankRequest,
    ReferenceVisualRerankResponse,
    VisualInputReportsResponse,
)


class QaToolService(Protocol):
    def review_current_episode(
        self,
        project_id: str,
        request: EpisodeReviewRequest,
    ) -> EpisodeReviewResponse: ...

    def rerank_shot_reference_visuals(
        self,
        project_id: str,
        shot_id: str,
        request: ReferenceVisualRerankRequest,
    ) -> ReferenceVisualRerankResponse: ...

    def compare_shot_keyframes(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeComparisonRequest,
    ) -> KeyframeComparisonResponse: ...

    def check_shot_input_compatibility(
        self,
        project_id: str,
        shot_id: str,
        request: InputCompatibilityRequest,
    ) -> InputCompatibilityResponse: ...

    def list_visual_input_reports(
        self,
        project_id: str,
    ) -> VisualInputReportsResponse: ...


class QaReviewEpisodeInput(ToolModel):
    project_id: str
    request: EpisodeReviewRequest


class QaReferenceRerankInput(ToolModel):
    project_id: str
    shot_id: str
    request: ReferenceVisualRerankRequest


class QaKeyframeCompareInput(ToolModel):
    project_id: str
    shot_id: str
    request: KeyframeComparisonRequest


class QaInputCompatibilityInput(ToolModel):
    project_id: str
    shot_id: str
    request: InputCompatibilityRequest


class QaVisualReportsInput(ToolModel):
    project_id: str


def register_tools(registry: ToolRegistry, service: QaToolService) -> None:
    def review(request: QaReviewEpisodeInput) -> ToolExecution[EpisodeReviewResponse]:
        result = service.review_current_episode(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="episode_review",
                    artifact_id=result.report.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="qa.review_episode",
            version=1,
            category=ToolCategory.QA,
            description="Review the current episode timeline and persist the result.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaReviewEpisodeInput,
            output_model=EpisodeReviewResponse,
            handler=review,
            tags=frozenset({"episode", "qa", "review"}),
        )
    )

    def rerank(
        request: QaReferenceRerankInput,
    ) -> ToolExecution[ReferenceVisualRerankResponse]:
        result = service.rerank_shot_reference_visuals(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="reference_visual_rerank",
                    artifact_id=result.report.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="qa.rerank_reference_visuals",
            version=1,
            category=ToolCategory.QA,
            description="Observe and rank eligible reference visuals before generation.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaReferenceRerankInput,
            output_model=ReferenceVisualRerankResponse,
            handler=rerank,
            tags=frozenset({"qa", "reference", "visual-input"}),
        )
    )

    def compare_keyframes(
        request: QaKeyframeCompareInput,
    ) -> ToolExecution[KeyframeComparisonResponse]:
        result = service.compare_shot_keyframes(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="keyframe_comparison",
                    artifact_id=result.report.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="qa.compare_keyframes",
            version=1,
            category=ToolCategory.QA,
            description="Compare candidate keyframes and allow an explicit none-acceptable result.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaKeyframeCompareInput,
            output_model=KeyframeComparisonResponse,
            handler=compare_keyframes,
            tags=frozenset({"keyframe", "qa", "visual-input"}),
        )
    )

    def compatibility(
        request: QaInputCompatibilityInput,
    ) -> ToolExecution[InputCompatibilityResponse]:
        result = service.check_shot_input_compatibility(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="input_compatibility",
                    artifact_id=result.report.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="qa.check_input_compatibility",
            version=1,
            category=ToolCategory.QA,
            description="Assess whether prepared visual inputs are mutually compatible.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaInputCompatibilityInput,
            output_model=InputCompatibilityResponse,
            handler=compatibility,
            tags=frozenset({"compatibility", "qa", "visual-input"}),
        )
    )

    registry.register(
        ToolSpec(
            id="qa.list_visual_input_reports",
            version=1,
            category=ToolCategory.QA,
            description="List persisted advisory visual-input quality reports for a project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=QaVisualReportsInput,
            output_model=VisualInputReportsResponse,
            handler=lambda request: ToolExecution(
                output=service.list_visual_input_reports(request.project_id)
            ),
            tags=frozenset({"qa", "report", "visual-input"}),
        )
    )


__all__ = [
    "QaInputCompatibilityInput",
    "QaKeyframeCompareInput",
    "QaReferenceRerankInput",
    "QaReviewEpisodeInput",
    "QaVisualReportsInput",
    "register_tools",
]
