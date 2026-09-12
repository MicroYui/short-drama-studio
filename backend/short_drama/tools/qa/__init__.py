"""Episode and pre-generation visual quality tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.asset_inspection import (
    AssetVersionInspectionRequest,
    SemanticAssetReview,
    SemanticAssetReviewEvidenceReference,
    SemanticSourceImageAnalysis,
    SourceImageAnalysisRequest,
)
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
    def asset_version_artifact_refs(
        self,
        project_id: str,
        version_ids: list[str],
    ) -> list[ArtifactRef]: ...

    def analyze_source_image(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: SourceImageAnalysisRequest,
    ) -> SemanticSourceImageAnalysis: ...

    def inspect_asset_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetVersionInspectionRequest,
    ) -> SemanticAssetReview: ...

    def asset_inspection_reference_version_refs(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetVersionInspectionRequest,
    ) -> list[tuple[str, str]]: ...

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


class QaAssetVersionInspectInput(ToolModel):
    project_id: str
    asset_id: str
    version_id: str
    request: AssetVersionInspectionRequest


class QaSourceImageAnalyzeInput(ToolModel):
    project_id: str
    asset_id: str
    version_id: str
    request: SourceImageAnalysisRequest


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
    def analyze_source_image(
        request: QaSourceImageAnalyzeInput,
    ) -> ToolExecution[SemanticSourceImageAnalysis]:
        result = service.analyze_source_image(
            request.project_id,
            request.asset_id,
            request.version_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset",
                    artifact_id=request.asset_id,
                ),
                *service.asset_version_artifact_refs(
                    request.project_id, [request.version_id]
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="qa.analyze_source_image",
            version=1,
            category=ToolCategory.QA,
            description=(
                "在 AssetPlan 分配用户或外部源图用途前观察精确图片。Agent 可把"
                "对话中已确定的目标资产、参考职责与权威级别结构化传入；工具只依据"
                "可见证据说明覆盖、缺口、可直接复用还是应从原图派生，不会用文件名"
                "或关键词替 Agent 路由，也不会把未分配素材自动绑定全片。"
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaSourceImageAnalyzeInput,
            output_model=SemanticSourceImageAnalysis,
            handler=analyze_source_image,
            tags=frozenset({"asset", "image", "source-intake", "semantic-analysis"}),
        )
    )

    def inspect_asset_version(
        request: QaAssetVersionInspectInput,
    ) -> ToolExecution[SemanticAssetReview]:
        result = service.inspect_asset_version(
            request.project_id,
            request.asset_id,
            request.version_id,
            request.request,
        )
        resolved_reference_refs = service.asset_inspection_reference_version_refs(
            request.project_id,
            request.asset_id,
            request.version_id,
            request.request,
        )
        unique_reference_refs = list(dict.fromkeys(resolved_reference_refs))
        result = result.model_copy(
            update={
                "evidence_references": [
                    SemanticAssetReviewEvidenceReference(
                        source_project_id=reference_project_id,
                        asset_version_id=reference_version_id,
                    )
                    for reference_project_id, reference_version_id in (
                        unique_reference_refs
                    )
                ]
            }
        )
        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="asset",
                artifact_id=request.asset_id,
            ),
        ]
        if request.request.shot_id is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="shot_plan",
                    artifact_id=request.request.shot_id,
                )
            )
        if result.narrative_context is not None:
            refs.append(ArtifactRef(
                project_id=result.narrative_context.project_id,
                artifact_type="script_version",
                artifact_id=result.narrative_context.script_version_id,
                version=result.narrative_context.script_version,
            ))
        refs.extend(service.asset_version_artifact_refs(
            request.project_id,
            [request.version_id, *(
                version_id
                for reference_project_id, version_id in unique_reference_refs
                if reference_project_id == request.project_id
            )],
        ))
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="qa.inspect_asset_version",
            version=4,
            category=ToolCategory.QA,
            description=(
                "生成或导入一个实际图片版本后调用；对照资产 brief、"
                "本张真实 ImageTask 的生成意图和 output_layout、"
                "可选镜头、明确选中的可用母版与候选固化的派生来源进行多模态语义检查。"
                "review_focus 声明本次用途/疑问；叙事影响判断时可用 narrative_context 选择已保存场次与台词ID，"
                "程序仅读取该场摘要与所选台词。自定实现条件放 implementation_preferences，不冒充用户要求。"
                "本张局部视图不按整个资产的最终多视图卡误审。"
                "regions 在同一次检查中追加候选/已声明参考的实际脸部、文字、部件裁图，"
                "保留全图与真实像素范围，细节不足应报告未观察。"
                "仅需裁切且边界可见时，crop_suggestion 可在同次观察给出候选的归一化region、"
                "原因及实际源版本/像素范围，供 image.crop 使用；它不自动执行，也不代表裁后已通过。"
                "审图保留每个派生来源的参考职责、权威级别、用途和主体别名；"
                "用户明确锁定的原始图可在它声明的职责内作为历史证据，但不因此"
                "被泛化为已批准资产。它只返回"
                "建议和问题，不会自动批准或拒绝。问题的重要性取决于"
                "已声明的下游职责；Provider 直接生成卡的近似分区或轻微中性"
                "背景渐变，在不影响身份、视图可读性或后续使用时不应单独成为"
                "拒绝理由。通用轮廓侧面的左/右反向也只在非对称身份特征"
                "或 Shot/裁切明确要求某一侧时成为阻断问题，不从 LEFT/RIGHT 枚举名"
                "自动推导。同规格重复载体中能独立裁用的单件，不因副本间"
                "轻微尺寸或透视差就默认拒绝；只有结构、材质、组件或可裁用性"
                "已影响声明的制作职责时才是实质问题。随后由 Agent 决定是否调用 "
                "artifact.review_version 记录结论。"
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=QaAssetVersionInspectInput,
            output_model=SemanticAssetReview,
            handler=inspect_asset_version,
            tags=frozenset({"asset", "image", "qa", "semantic-review"}),
        )
    )

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
            description=(
                "Optionally review narrative continuity in the current episode from persisted "
                "visual evidence. Reuse an existing report unless new evidence needs review; "
                "Saved Studio editorial clips, timing, transforms, layers and subtitles take precedence "
                "over a legacy TimelineManifest. Source Take contact sheets are not composited export frames. "
                "findings do not gate editing or export. This does not listen to the soundtrack. "
                "A failed stage returns its saved progress for the Agent to decide what to do next."
            ),
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
            version=2,
            category=ToolCategory.QA,
            description=("Jointly assess selected generation images against the current generation group and its shot windows. "
                         "Omit image IDs to resolve current group references, or supply a proposed set; omitted current inputs are reported as unobserved. "
                         "Use scope=SHOT for an explicit single-shot check. Reports are advice, not approval gates."),
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
            description=(
                "读取项目已保存的三类镜头输入报告：参考图排序 "
                "reference_reranks、关键帧比较 keyframe_comparisons、"
                "输入兼容检查 compatibility_checks。它们对应 qa.rerank_reference_visuals、"
                "qa.compare_keyframes 和 qa.check_input_compatibility；"
                "单张图片 qa.inspect_asset_version 的语义检查保留在原 Tool "
                "结果及工单回执中，不在本清单内。空清单不表示现有图片尚未检查。"
            ),
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
    "QaAssetVersionInspectInput",
    "QaInputCompatibilityInput",
    "QaKeyframeCompareInput",
    "QaReferenceRerankInput",
    "QaReviewEpisodeInput",
    "QaVisualReportsInput",
    "register_tools",
]
