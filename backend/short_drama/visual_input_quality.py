"""Agent-callable visual input selection and compatibility advice."""

from __future__ import annotations

import base64
import json
import mimetypes
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetMediaType,
    AssetVersion,
    DomainModel,
    Freshness,
    ProjectAggregate,
    ProjectDocument,
    ReferenceAngle,
    ReferenceFraming,
    ReferencePlanEntry,
    ReferencePackEntry,
    ReferenceRequirement,
    ReferenceRole,
    SemanticReviewStatus,
    ShotPlan,
    Take,
    VideoGenerationRoute,
    VideoInspectionReport,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.reference_planner import (
    KeyframeBriefCompileRequest,
    KeyframePosition,
    compile_keyframe_brief,
    preview_reference_plan,
    ReferencePlanPreviewRequest,
)
from short_drama.video_inspection import image_data_url


class VisualInputContractError(ValueError):
    """The caller supplied visual inputs that do not belong to the requested project."""


class ReferenceVisualRerankDecision(StrEnum):
    SELECT_REFERENCES = "SELECT_REFERENCES"
    NONE_ACCEPTABLE = "NONE_ACCEPTABLE"


class KeyframeComparisonDecision(StrEnum):
    SELECT_KEYFRAME = "SELECT_KEYFRAME"
    NONE_ACCEPTABLE = "NONE_ACCEPTABLE"


class InputCompatibilityDecision(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    CHANGE_KEYFRAME = "CHANGE_KEYFRAME"
    CHANGE_ROUTE = "CHANGE_ROUTE"
    SPLIT_SHOT = "SPLIT_SHOT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class VisualInputIssueCategory(StrEnum):
    IDENTITY = "IDENTITY"
    WARDROBE = "WARDROBE"
    SCENE_TOPOLOGY = "SCENE_TOPOLOGY"
    PROP_STATE = "PROP_STATE"
    COMPOSITION = "COMPOSITION"
    ACTION_BOUNDARY = "ACTION_BOUNDARY"
    CONTACT = "CONTACT"
    CAMERA = "CAMERA"
    REFERENCE_CONFLICT = "REFERENCE_CONFLICT"
    REDUNDANCY = "REDUNDANCY"
    TECHNICAL_CONTRACT = "TECHNICAL_CONTRACT"
    OTHER = "OTHER"


class VisualInputIssue(DomainModel):
    category: VisualInputIssueCategory
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    message: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)
    affected_ids: list[str] = Field(default_factory=list[str], max_length=16)


class ReferenceVisualRerankRequest(DomainModel):
    requirement_ids: list[str] = Field(default_factory=list[str], max_length=32)
    candidate_entry_ids: list[str] = Field(default_factory=list[str], max_length=64)
    max_candidates: int | None = Field(default=None, ge=1, le=32)
    max_selected: int | None = Field(default=None, ge=1, le=9)
    review_focus: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ReferenceVisualRerankRequest:
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("Reference requirement IDs must be unique")
        if len(set(self.candidate_entry_ids)) != len(self.candidate_entry_ids):
            raise ValueError("Reference candidate entry IDs must be unique")
        return self


class ReferenceVisualCandidate(DomainModel):
    candidate_id: str
    pack_id: str
    entry_id: str
    subject_asset_id: str
    asset_version_id: str
    role: ReferenceRole
    angle: ReferenceAngle
    framing: ReferenceFraming
    tags: list[str] = Field(default_factory=list[str], max_length=32)
    description: str
    requirement_ids: list[str] = Field(default_factory=list[str], max_length=32)
    deterministic_score: int
    deterministic_reasons: list[str] = Field(default_factory=list[str], max_length=16)


class ReferenceVisualCandidateReview(DomainModel):
    candidate_id: str
    relative_score: float = Field(ge=0.0, le=5.0)
    strengths: list[str] = Field(default_factory=list[str], max_length=12)
    weaknesses: list[str] = Field(default_factory=list[str], max_length=12)


class ReferenceVisualSelectionAdvice(DomainModel):
    candidate_id: str
    responsibilities: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class ReferenceVisualRerankJudgment(DomainModel):
    decision: ReferenceVisualRerankDecision
    selections: list[ReferenceVisualSelectionAdvice] = Field(
        default_factory=list[ReferenceVisualSelectionAdvice],
        max_length=9,
    )
    candidate_reviews: list[ReferenceVisualCandidateReview] = Field(
        min_length=1,
        max_length=32,
    )
    issues: list[VisualInputIssue] = Field(
        default_factory=list[VisualInputIssue],
        max_length=24,
    )
    redundant_candidate_ids: list[str] = Field(default_factory=list[str], max_length=32)
    summary: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_decision_shape(self) -> ReferenceVisualRerankJudgment:
        if self.decision == ReferenceVisualRerankDecision.SELECT_REFERENCES:
            if not self.selections:
                raise ValueError("SELECT_REFERENCES requires at least one selection")
        elif self.selections:
            raise ValueError("NONE_ACCEPTABLE cannot include selected references")
        selection_ids = [item.candidate_id for item in self.selections]
        if len(set(selection_ids)) != len(selection_ids):
            raise ValueError("Selected reference candidates must be unique")
        return self


class ReferenceVisualRerankReport(DomainModel):
    id: str
    project_id: str
    shot_id: str
    requirement_ids: list[str]
    candidates: list[ReferenceVisualCandidate] = Field(min_length=1, max_length=32)
    max_selected: int = Field(ge=1, le=9)
    decision: ReferenceVisualRerankDecision
    selections: list[ReferenceVisualSelectionAdvice] = Field(
        default_factory=list[ReferenceVisualSelectionAdvice],
        max_length=9,
    )
    candidate_reviews: list[ReferenceVisualCandidateReview] = Field(
        default_factory=list[ReferenceVisualCandidateReview],
        max_length=32,
    )
    issues: list[VisualInputIssue] = Field(default_factory=list[VisualInputIssue], max_length=24)
    redundant_candidate_ids: list[str] = Field(default_factory=list[str], max_length=32)
    summary: str = Field(min_length=1, max_length=3000)
    warnings: list[str] = Field(default_factory=list[str], max_length=32)
    semantic_status: SemanticReviewStatus = SemanticReviewStatus.COMPLETE
    semantic_model: str = "gpt-5.6-sol"
    advisory_only: Literal[True] = True
    proceed_allowed: Literal[True] = True
    created_at: str


class ReferenceVisualRerankResponse(DomainModel):
    report: ReferenceVisualRerankReport
    project: ProjectAggregate


class KeyframeComparisonRequest(DomainModel):
    candidate_version_ids: list[str] = Field(min_length=2, max_length=12)
    position: KeyframePosition
    include_previous_boundary: bool = True
    review_focus: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_unique_versions(self) -> KeyframeComparisonRequest:
        if len(set(self.candidate_version_ids)) != len(self.candidate_version_ids):
            raise ValueError("Keyframe candidate versions must be unique")
        return self


class KeyframeCandidateReview(DomainModel):
    asset_version_id: str
    relative_score: float = Field(ge=0.0, le=5.0)
    strengths: list[str] = Field(default_factory=list[str], max_length=12)
    weaknesses: list[str] = Field(default_factory=list[str], max_length=12)
    video_generation_risks: list[str] = Field(default_factory=list[str], max_length=12)


class KeyframeComparisonJudgment(DomainModel):
    decision: KeyframeComparisonDecision
    selected_version_id: str | None = None
    candidate_reviews: list[KeyframeCandidateReview] = Field(min_length=2, max_length=12)
    issues: list[VisualInputIssue] = Field(default_factory=list[VisualInputIssue], max_length=24)
    summary: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_selected_version(self) -> KeyframeComparisonJudgment:
        if self.decision == KeyframeComparisonDecision.SELECT_KEYFRAME:
            if self.selected_version_id is None:
                raise ValueError("SELECT_KEYFRAME requires selected_version_id")
        elif self.selected_version_id is not None:
            raise ValueError("NONE_ACCEPTABLE cannot include selected_version_id")
        return self


class KeyframeComparisonReport(DomainModel):
    id: str
    project_id: str
    shot_id: str
    position: KeyframePosition
    candidate_version_ids: list[str] = Field(min_length=2, max_length=12)
    decision: KeyframeComparisonDecision
    selected_version_id: str | None = None
    candidate_reviews: list[KeyframeCandidateReview] = Field(
        default_factory=list[KeyframeCandidateReview],
        max_length=12,
    )
    issues: list[VisualInputIssue] = Field(default_factory=list[VisualInputIssue], max_length=24)
    summary: str = Field(min_length=1, max_length=3000)
    warnings: list[str] = Field(default_factory=list[str], max_length=24)
    semantic_status: SemanticReviewStatus = SemanticReviewStatus.COMPLETE
    semantic_model: str = "gpt-5.6-sol"
    advisory_only: Literal[True] = True
    proceed_allowed: Literal[True] = True
    created_at: str


class KeyframeComparisonResponse(DomainModel):
    report: KeyframeComparisonReport
    project: ProjectAggregate


class InputCompatibilityRequest(DomainModel):
    route: VideoGenerationRoute
    start_version_id: str | None = None
    end_version_id: str | None = None
    reference_version_ids: list[str] = Field(default_factory=list[str], max_length=9)
    include_previous_boundary: bool = True
    review_focus: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_reference_versions(self) -> InputCompatibilityRequest:
        if len(set(self.reference_version_ids)) != len(self.reference_version_ids):
            raise ValueError("Reference input versions must be unique")
        return self


class InputCompatibilityObservation(DomainModel):
    input_id: str
    visible_state: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=2000)


class InputCompatibilityJudgment(DomainModel):
    decision: InputCompatibilityDecision
    observations: list[InputCompatibilityObservation] = Field(
        default_factory=list[InputCompatibilityObservation],
        max_length=24,
    )
    issues: list[VisualInputIssue] = Field(default_factory=list[VisualInputIssue], max_length=24)
    recommendations: list[str] = Field(default_factory=list[str], max_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=3000)


class InputCompatibilityReport(DomainModel):
    id: str
    project_id: str
    shot_id: str
    route: VideoGenerationRoute
    start_version_id: str | None = None
    end_version_id: str | None = None
    reference_version_ids: list[str] = Field(default_factory=list[str], max_length=9)
    decision: InputCompatibilityDecision
    observations: list[InputCompatibilityObservation] = Field(
        default_factory=list[InputCompatibilityObservation],
        max_length=24,
    )
    issues: list[VisualInputIssue] = Field(default_factory=list[VisualInputIssue], max_length=24)
    recommendations: list[str] = Field(default_factory=list[str], max_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=3000)
    technical_warnings: list[str] = Field(default_factory=list[str], max_length=16)
    semantic_status: SemanticReviewStatus = SemanticReviewStatus.COMPLETE
    semantic_model: str = "gpt-5.6-sol"
    advisory_only: Literal[True] = True
    proceed_allowed: Literal[True] = True
    created_at: str


class InputCompatibilityResponse(DomainModel):
    report: InputCompatibilityReport
    project: ProjectAggregate


class VisualInputReportsResponse(DomainModel):
    reference_reranks: list[ReferenceVisualRerankReport]
    keyframe_comparisons: list[KeyframeComparisonReport]
    compatibility_checks: list[InputCompatibilityReport]


def build_reference_visual_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: ReferenceVisualRerankRequest,
    bound_candidates: bool = True,
) -> tuple[list[ReferenceVisualCandidate], int, list[str]]:
    requirements = _filtered_requirements(plan, request.requirement_ids)
    max_selected = request.max_selected or plan.max_reference_images
    version_index = _version_index(document)
    asset_index = {asset.id: asset for asset in document.assets}
    requested_ids = set(request.candidate_entry_ids)
    known_requested: set[str] = set()
    candidates: list[ReferenceVisualCandidate] = []
    warnings: list[str] = []
    relevant_subjects = _relevant_subject_ids(document, plan, requirements)

    for pack in document.reference_packs:
        if pack.project_id != document.project.id:
            continue
        for entry in pack.entries:
            explicitly_requested = entry.id in requested_ids
            if explicitly_requested:
                known_requested.add(entry.id)
            matches = [
                requirement
                for requirement in requirements
                if requirement.subject_asset_id == pack.subject_asset_id
                and entry.role in requirement.acceptable_roles
                and set(requirement.required_tags).issubset(entry.tags)
            ]
            if requested_ids and not explicitly_requested:
                continue
            if not requested_ids and requirements and not matches:
                continue
            if not requested_ids and not requirements and pack.subject_asset_id not in relevant_subjects:
                continue
            resolved = version_index.get(entry.asset_version_id)
            if resolved is None:
                if explicitly_requested:
                    raise VisualInputContractError(
                        f"Reference entry {entry.id} points to an unknown AssetVersion"
                    )
                continue
            _asset, version = resolved
            subject = asset_index.get(pack.subject_asset_id)
            lineage_is_current = (
                entry.lineage_root_version_id is None
                or subject is not None
                and subject.approved_version_id == entry.lineage_root_version_id
            )
            if not lineage_is_current:
                if explicitly_requested:
                    raise VisualInputContractError(
                        f"Reference entry {entry.id} belongs to a superseded subject lineage"
                    )
                continue
            if not _is_usable_approved_image(version):
                if explicitly_requested:
                    raise VisualInputContractError(
                        f"Reference entry {entry.id} is not a usable approved image"
                    )
                continue
            score, reasons = _candidate_score(entry, matches)
            candidates.append(
                ReferenceVisualCandidate(
                    candidate_id=entry.id,
                    pack_id=pack.id,
                    entry_id=entry.id,
                    subject_asset_id=pack.subject_asset_id,
                    asset_version_id=entry.asset_version_id,
                    role=entry.role,
                    angle=entry.angle,
                    framing=entry.framing,
                    tags=list(entry.tags),
                    description=entry.description,
                    requirement_ids=[item.id for item in matches],
                    deterministic_score=score,
                    deterministic_reasons=reasons,
                )
            )

    unknown_requested = sorted(requested_ids - known_requested)
    if unknown_requested:
        raise VisualInputContractError(
            "Unknown or cross-project reference entry IDs: " + ", ".join(unknown_requested)
        )

    if not requested_ids:
        preview = preview_reference_plan(
            document,
            shot,
            plan,
            ReferencePlanPreviewRequest(
                requirement_ids=request.requirement_ids,
                max_images=max_selected,
            ),
        )
        known_candidate_ids = {item.candidate_id for item in candidates}
        known_version_ids = {item.asset_version_id for item in candidates}
        for item in preview.selected:
            if item.entry_id in known_candidate_ids or item.asset_version_id in known_version_ids:
                continue
            candidates.append(
                ReferenceVisualCandidate(
                    candidate_id=item.entry_id,
                    pack_id=item.pack_id,
                    entry_id=item.entry_id,
                    subject_asset_id=item.subject_asset_id,
                    asset_version_id=item.asset_version_id,
                    role=item.role,
                    angle=item.angle,
                    framing=item.framing,
                    tags=list(item.tags),
                    description=item.description,
                    requirement_ids=list(item.requirement_ids),
                    deterministic_score=item.score,
                    deterministic_reasons=list(item.reasons),
                )
            )

    if not candidates:
        raise VisualInputContractError(
            "No project-local approved visual candidates match this shot and ReferencePlan"
        )

    candidates.sort(
        key=lambda item: (-item.deterministic_score, item.subject_asset_id, item.candidate_id)
    )
    if bound_candidates:
        default_budget = min(
            32,
            max(4, max_selected * 3, len(requirements) * 3),
        )
        budget = request.max_candidates or default_budget
        available_count = len(candidates)
        candidates = _bounded_with_subject_coverage(candidates, relevant_subjects, budget)
        if len(candidates) < available_count:
            warnings.append(
                f"本次多模态比较使用了 {available_count} 个可用候选项中的 {len(candidates)} 个；"
                "Agent 可以指定候选集后再次比较。"
            )
    if len(candidates) == 1:
        warnings.append(
            "只有一个可用候选项；Tool 仍可判断其不可用，但无法进行相对视觉排序。"
        )
    return candidates, max_selected, warnings


def rerank_reference_visuals(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: ReferenceVisualRerankRequest,
    client: LanguageProviderClient,
    created_at: str,
) -> ReferenceVisualRerankReport:
    candidates, max_selected, warnings = build_reference_visual_candidates(
        document=document,
        shot=shot,
        plan=plan,
        request=request,
    )
    version_index = _version_index(document)
    image_urls: list[str] = []
    image_map: list[dict[str, object]] = []
    for candidate in candidates:
        _asset, version = version_index[candidate.asset_version_id]
        image_urls.append(_asset_version_image_url(version))
        image_map.append(
            {
                "image": len(image_urls),
                "candidate_id": candidate.candidate_id,
                "subject_asset_id": candidate.subject_asset_id,
                "role": candidate.role.value,
            }
        )

    instructions = (
        "你是供电影导演 Agent 调用的视觉参考图重排 Tool。只返回 JSON，并严格匹配所给 schema。"
        "先分别描述每张图片中实际可见的内容，不要预设它满足目标答案；随后再与 ShotPlan 和 "
        "ReferencePlan 比较。选择能发挥作用的最小图片集合，每个成员都应承担清晰且互不重叠的"
        "职责。优先考虑真正支持当前镜头的人物身份、服装、机位角度、构图、场景拓扑、道具状态、"
        "版本新旧和共同谱系。指出互相冲突的人物身份、服装、布局、物体结构、图片内嵌的场景几何"
        "以及冗余视图。不要仅仅因为某张图相对没那么差就选择它；如果组合会造成实质矛盾，返回 "
        "NONE_ACCEPTABLE。不要默认要求三视图，也不要为了用满名额而选图。结果仅为建议，用户可以"
        "覆盖；不得修改资产或审批媒体。相对评分只解释本次比较，不具有全局可比性。"
    )
    payload = {
        "output_schema": ReferenceVisualRerankJudgment.model_json_schema(),
        "shot": shot.model_dump(mode="json"),
        "reference_plan": plan.model_dump(mode="json"),
        "requirements": [
            item.model_dump(mode="json")
            for item in _filtered_requirements(plan, request.requirement_ids)
        ],
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "max_selected": max_selected,
        "review_focus": request.review_focus,
        "image_map": image_map,
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls,
        max_output_tokens=5000,
    )
    judgment = ReferenceVisualRerankJudgment.model_validate_json(_strip_json_fence(output))
    candidate_ids = {item.candidate_id for item in candidates}
    review_ids = [item.candidate_id for item in judgment.candidate_reviews]
    if set(review_ids) != candidate_ids or len(review_ids) != len(candidate_ids):
        raise ValueError("Reference reranker must review every supplied candidate exactly once")
    selection_ids = [item.candidate_id for item in judgment.selections]
    if any(item not in candidate_ids for item in selection_ids):
        raise ValueError("Reference reranker selected an unknown candidate")
    if len(selection_ids) > max_selected:
        raise ValueError("Reference reranker selected more images than the requested input budget")
    if any(item not in candidate_ids for item in judgment.redundant_candidate_ids):
        raise ValueError("Reference reranker marked an unknown candidate as redundant")

    requirements = _filtered_requirements(plan, request.requirement_ids)
    selected_by_id = {item.candidate_id: item for item in candidates}
    selected_subject_ids = {
        selected_by_id[item].subject_asset_id for item in selection_ids
    }
    missing_required_subjects = sorted(
        {
            item.subject_asset_id
            for item in requirements
            if item.required and item.subject_asset_id not in selected_subject_ids
        }
    )
    if missing_required_subjects:
        warnings.append(
            "建议集合未覆盖必需的主体资产："
            + ", ".join(missing_required_subjects)
        )

    return ReferenceVisualRerankReport(
        id=f"reference-rerank-{uuid4().hex}",
        project_id=document.project.id,
        shot_id=shot.id,
        requirement_ids=[item.id for item in requirements],
        candidates=candidates,
        max_selected=max_selected,
        decision=judgment.decision,
        selections=judgment.selections,
        candidate_reviews=judgment.candidate_reviews,
        issues=judgment.issues,
        redundant_candidate_ids=judgment.redundant_candidate_ids,
        summary=judgment.summary,
        warnings=warnings,
        created_at=created_at,
    )


def compare_keyframe_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: KeyframeComparisonRequest,
    client: LanguageProviderClient,
    previous_boundary_url: str | None,
    created_at: str,
) -> KeyframeComparisonReport:
    candidates = _resolve_image_versions(document, request.candidate_version_ids)
    brief = compile_keyframe_brief(
        document,
        shot,
        plan,
        KeyframeBriefCompileRequest(position=request.position),
    )
    image_urls: list[str] = []
    image_map: list[dict[str, object]] = []
    for asset, version in candidates:
        image_urls.append(_asset_version_image_url(version))
        image_map.append(
            {
                "image": len(image_urls),
                "role": "KEYFRAME_CANDIDATE",
                "asset_id": asset.id,
                "asset_version_id": version.id,
            }
        )
    if previous_boundary_url is not None:
        image_urls.append(previous_boundary_url)
        image_map.append(
            {"image": len(image_urls), "role": "PREVIOUS_SHOT_END_BOUNDARY"}
        )
    candidate_ids = set(request.candidate_version_ids)
    version_index = _version_index(document)
    for version_id in brief.input_version_ids:
        if version_id in candidate_ids:
            continue
        resolved = version_index.get(version_id)
        if resolved is None:
            continue
        asset, version = resolved
        image_urls.append(_asset_version_image_url(version))
        image_map.append(
            {
                "image": len(image_urls),
                "role": "APPROVED_CANONICAL_REFERENCE",
                "asset_id": asset.id,
                "asset_version_id": version.id,
                "description": asset.name,
            }
        )

    instructions = (
        "你是供电影导演 Agent 调用的关键帧相对比较 Tool。只返回 JSON，并严格匹配所给 schema。"
        "先独立观察每个候选帧，再与预期关键帧状态比较。比较人物身份、服装、场景拓扑、道具状态、"
        "机位方向、构图、动作阶段、接触点、前一镜头边界，以及静帧是否为视频生成提供了清楚且"
        "物理上可达的起始或结束状态。不要推断画面中不可见的事实，也不要因为候选帧的生成提示词"
        "要求了某个结果，就认定画面已经做到。好看的构图不能抵消可见的状态矛盾。如果所有候选帧"
        "都会让视频模型面对矛盾或含混的运动问题，返回 NONE_ACCEPTABLE。结果仅为建议，不会审批"
        "或拒绝任何 AssetVersion，用户可以忽略。相对评分只在本次比较内有意义。"
    )
    payload = {
        "output_schema": KeyframeComparisonJudgment.model_json_schema(),
        "shot": shot.model_dump(mode="json"),
        "position": request.position.value,
        "expected_keyframe_brief": brief.prompt,
        "candidates": [
            {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "asset_version": version.model_dump(mode="json"),
            }
            for asset, version in candidates
        ],
        "review_focus": request.review_focus,
        "image_map": image_map,
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls,
        max_output_tokens=5000,
    )
    judgment = KeyframeComparisonJudgment.model_validate_json(_strip_json_fence(output))
    review_ids = [item.asset_version_id for item in judgment.candidate_reviews]
    if set(review_ids) != candidate_ids or len(review_ids) != len(candidate_ids):
        raise ValueError("Keyframe comparison must review every supplied candidate exactly once")
    if judgment.selected_version_id is not None and judgment.selected_version_id not in candidate_ids:
        raise ValueError("Keyframe comparison selected an unknown AssetVersion")
    warnings = [
        f"{version.id} 当前状态为 {version.approval_status.value}；本建议不会改变审批状态。"
        for _asset, version in candidates
        if version.approval_status != ApprovalStatus.APPROVED
    ]
    return KeyframeComparisonReport(
        id=f"keyframe-comparison-{uuid4().hex}",
        project_id=document.project.id,
        shot_id=shot.id,
        position=request.position,
        candidate_version_ids=list(request.candidate_version_ids),
        decision=judgment.decision,
        selected_version_id=judgment.selected_version_id,
        candidate_reviews=judgment.candidate_reviews,
        issues=judgment.issues,
        summary=judgment.summary,
        warnings=warnings,
        created_at=created_at,
    )


def check_input_compatibility(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: InputCompatibilityRequest,
    client: LanguageProviderClient,
    previous_boundary_url: str | None,
    created_at: str,
) -> InputCompatibilityReport:
    requested_version_ids = [
        *([request.start_version_id] if request.start_version_id is not None else []),
        *([request.end_version_id] if request.end_version_id is not None else []),
        *request.reference_version_ids,
    ]
    resolved = {version.id: (asset, version) for asset, version in _resolve_image_versions(
        document,
        list(dict.fromkeys(requested_version_ids)),
    )}
    image_urls: list[str] = []
    image_map: list[dict[str, object]] = []

    def add_input(role: str, version_id: str) -> None:
        asset, version = resolved[version_id]
        image_urls.append(_asset_version_image_url(version))
        image_map.append(
            {
                "image": len(image_urls),
                "input_id": version_id,
                "role": role,
                "asset_id": asset.id,
                "asset_name": asset.name,
            }
        )

    if request.start_version_id is not None:
        add_input("START_FRAME", request.start_version_id)
    if request.end_version_id is not None:
        add_input("END_FRAME", request.end_version_id)
    for version_id in request.reference_version_ids:
        add_input("REFERENCE_IMAGE", version_id)
    if previous_boundary_url is not None:
        image_urls.append(previous_boundary_url)
        image_map.append(
            {
                "image": len(image_urls),
                "input_id": "previous-shot-boundary",
                "role": "PREVIOUS_SHOT_END_BOUNDARY",
            }
        )

    technical_warnings = _technical_route_warnings(request)
    instructions = (
        "你是供电影导演 Agent 调用的输入兼容性检查 Tool。只返回 JSON，并严格匹配所给 schema。"
        "这不是标准答案校验器。先记录每张输入图片中的可见状态，不要先寻找预期结论；随后再把这些"
        "观察与 ShotPlan、有序动作节拍、连续性状态和生成路线进行比较。判断人物身份、布局、道具"
        "状态、机位方向、动作起点、动作终点、接触关系和前一镜头边界能否同时成立，以及运动路径"
        "在预期生成片段内是否物理合理。证据含混时应明确说明，不要假设已经满足。只有当改变关键帧、"
        "路线或拆分镜头确实能调整与已观察问题最相关的控制变量时，才建议 CHANGE_KEYFRAME、"
        "CHANGE_ROUTE 或 SPLIT_SHOT。不要虚构门、把手或区域专属字段。所有创作建议都只是建议，"
        "用户可以覆盖；Provider 的时长和媒体组合契约由 H3 adapter 在后续阶段执行。"
    )
    payload = {
        "output_schema": InputCompatibilityJudgment.model_json_schema(),
        "shot": shot.model_dump(mode="json"),
        "reference_plan": plan.model_dump(mode="json"),
        "route": request.route.value,
        "technical_warnings": technical_warnings,
        "review_focus": request.review_focus,
        "image_map": image_map,
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls or None,
        max_output_tokens=4500,
    )
    judgment = InputCompatibilityJudgment.model_validate_json(_strip_json_fence(output))
    known_input_ids = {
        item["input_id"]
        for item in image_map
        if isinstance(item.get("input_id"), str)
    }
    if any(item.input_id not in known_input_ids for item in judgment.observations):
        raise ValueError("Input compatibility observation refers to an unknown image input")
    return InputCompatibilityReport(
        id=f"input-compatibility-{uuid4().hex}",
        project_id=document.project.id,
        shot_id=shot.id,
        route=request.route,
        start_version_id=request.start_version_id,
        end_version_id=request.end_version_id,
        reference_version_ids=list(request.reference_version_ids),
        decision=judgment.decision,
        observations=judgment.observations,
        issues=judgment.issues,
        recommendations=judgment.recommendations,
        confidence=judgment.confidence,
        summary=judgment.summary,
        technical_warnings=technical_warnings,
        created_at=created_at,
    )


def previous_shot_boundary_image_url(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    takes: list[Take],
    inspections: list[VideoInspectionReport],
) -> str | None:
    previous_shot = max(
        (item for item in document.shots if item.sequence < shot.sequence),
        key=lambda item: item.sequence,
        default=None,
    )
    if previous_shot is None:
        return None
    previous_take = next(
        (
            item
            for item in reversed(takes)
            if item.shot_id == previous_shot.id
            and item.is_selected
            and item.freshness == Freshness.CURRENT
        ),
        None,
    )
    if previous_take is None:
        return None
    report = next(
        (
            item
            for item in reversed(inspections)
            if item.take_id == previous_take.id and item.samples
        ),
        None,
    )
    if report is None:
        return None
    sample = max(report.samples, key=lambda item: item.timestamp_seconds)
    path = Path(sample.image_path).expanduser().resolve()
    if not path.is_file():
        return None
    return image_data_url(path)


def _filtered_requirements(
    plan: ReferencePlanEntry,
    requirement_ids: list[str],
) -> list[ReferenceRequirement]:
    if not requirement_ids:
        return list(plan.requirements)
    requested = set(requirement_ids)
    known = {item.id for item in plan.requirements}
    unknown = sorted(requested - known)
    if unknown:
        raise VisualInputContractError(
            "Unknown reference requirement IDs: " + ", ".join(unknown)
        )
    return [item for item in plan.requirements if item.id in requested]


def _relevant_subject_ids(
    document: ProjectDocument,
    plan: ReferencePlanEntry,
    requirements: list[ReferenceRequirement],
) -> list[str]:
    result: list[str] = []

    def add(value: str) -> None:
        if value not in result:
            result.append(value)

    for requirement in requirements:
        add(requirement.subject_asset_id)
    version_index = _version_index(document)
    for binding in plan.bindings:
        resolved = version_index.get(binding.target_id)
        if resolved is not None:
            add(resolved[0].id)
    for beat in plan.action_beats:
        add(beat.actor_asset_id)
    for interaction in plan.interactions:
        add(interaction.actor_asset_id)
        add(interaction.target_asset_id)
    return result


def _candidate_score(
    entry: ReferencePackEntry,
    requirements: list[ReferenceRequirement],
) -> tuple[int, list[str]]:
    role = entry.role
    angle = entry.angle
    framing = entry.framing
    priority = entry.priority
    score = priority
    reasons = [f"priority {priority}"]
    for requirement in requirements:
        role_score = 100 - requirement.acceptable_roles.index(role) * 10
        local_score = role_score
        local_reasons = [f"role rank +{role_score}"]
        if angle in requirement.preferred_angles:
            angle_score = 30 - requirement.preferred_angles.index(angle) * 5
            local_score += angle_score
            local_reasons.append(f"preferred angle +{angle_score}")
        if framing in requirement.preferred_framings:
            framing_score = 20 - requirement.preferred_framings.index(framing) * 4
            local_score += framing_score
            local_reasons.append(f"preferred framing +{framing_score}")
        if local_score > score - priority:
            score = priority + local_score
            reasons = [f"priority {priority}", *local_reasons]
    return score, reasons


def _bounded_with_subject_coverage(
    candidates: list[ReferenceVisualCandidate],
    relevant_subjects: list[str],
    budget: int,
) -> list[ReferenceVisualCandidate]:
    selected: list[ReferenceVisualCandidate] = []
    for subject_id in relevant_subjects:
        candidate = next(
            (item for item in candidates if item.subject_asset_id == subject_id),
            None,
        )
        if candidate is not None and candidate not in selected:
            selected.append(candidate)
        if len(selected) >= budget:
            return selected
    for candidate in candidates:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= budget:
            break
    return selected


def _version_index(document: ProjectDocument) -> dict[str, tuple[Asset, AssetVersion]]:
    return {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }


def _resolve_image_versions(
    document: ProjectDocument,
    version_ids: list[str],
) -> list[tuple[Asset, AssetVersion]]:
    index = _version_index(document)
    result: list[tuple[Asset, AssetVersion]] = []
    for version_id in version_ids:
        resolved = index.get(version_id)
        if resolved is None:
            raise VisualInputContractError(
                f"Unknown or cross-project AssetVersion: {version_id}"
            )
        asset, version = resolved
        if version.media_type != AssetMediaType.IMAGE:
            raise VisualInputContractError(f"AssetVersion {version_id} is not an image")
        if version.local_path is None and version.external_url is None:
            raise VisualInputContractError(f"AssetVersion {version_id} has no usable media")
        result.append((asset, version))
    return result


def _is_usable_approved_image(version: AssetVersion) -> bool:
    return (
        version.approval_status == ApprovalStatus.APPROVED
        and version.freshness == Freshness.CURRENT
        and version.media_type == AssetMediaType.IMAGE
        and (version.local_path is not None or version.external_url is not None)
    )


def _asset_version_image_url(version: AssetVersion) -> str:
    if version.external_url is not None:
        return version.external_url
    if version.local_path is None:
        raise VisualInputContractError(f"AssetVersion {version.id} has no usable media")
    path = Path(version.local_path).expanduser().resolve()
    if not path.is_file():
        raise VisualInputContractError(f"AssetVersion media is missing: {version.id}")
    mime_type = version.mime_type or mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _technical_route_warnings(request: InputCompatibilityRequest) -> list[str]:
    warnings: list[str] = []
    if request.route == VideoGenerationRoute.DIRECT:
        if (
            request.start_version_id is not None
            or request.end_version_id is not None
            or request.reference_version_ids
        ):
            warnings.append("DIRECT 不使用图像输入；请改用图像引导路线或移除这些输入。")
    elif request.route == VideoGenerationRoute.FRAME_FIRST:
        if request.start_version_id is None:
            warnings.append("FRAME_FIRST 在提交 H3 之前需要起始帧。")
        if request.end_version_id is not None:
            warnings.append("FRAME_FIRST 不使用结束帧；如果需要锁定结束状态，请使用 FRAME_FIRST_LAST。")
        if request.reference_version_ids:
            warnings.append("同一次 H3 请求中不能混用帧模式和参考图模式。")
    elif request.route == VideoGenerationRoute.FRAME_LAST:
        if request.end_version_id is None:
            warnings.append("FRAME_LAST 在提交 H3 之前需要结束帧。")
        if request.start_version_id is not None:
            warnings.append("FRAME_LAST 不使用起始帧；如果需要锁定两端，请使用 FRAME_FIRST_LAST。")
        if request.reference_version_ids:
            warnings.append("同一次 H3 请求中不能混用帧模式和参考图模式。")
    elif request.route == VideoGenerationRoute.FRAME_FIRST_LAST:
        if request.start_version_id is None or request.end_version_id is None:
            warnings.append("FRAME_FIRST_LAST 在提交 H3 之前同时需要起始帧和结束帧。")
        if request.reference_version_ids:
            warnings.append("同一次 H3 请求中不能混用帧模式和参考图模式。")
    elif request.route == VideoGenerationRoute.REFERENCE:
        if not request.reference_version_ids:
            warnings.append("REFERENCE 在提交 H3 之前至少需要一张参考图。")
        if request.start_version_id is not None or request.end_version_id is not None:
            warnings.append("同一次 H3 请求中不能混用参考图模式和帧模式。")
    return warnings


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
