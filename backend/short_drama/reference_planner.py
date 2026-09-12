"""Generic reference-pack selection and spatial-interaction brief compilation."""

from __future__ import annotations

from short_drama.language_defaults import LANGUAGE_MODEL

import json
from enum import StrEnum
from typing import Literal, cast

from pydantic import Field, JsonValue, model_validator
from short_drama.skill_ids import SkillIds

from short_drama.models import (
    ActionBeat,
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetVersion,
    ContinuityState,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    DomainModel,
    Freshness,
    ProjectAggregate,
    ProjectDocument,
    QaIssue,
    ReferenceAngle,
    ReferenceFraming,
    ReferencePack,
    ReferencePackEntry,
    ReferencePlanEntry,
    ReferenceRequirement,
    ReferenceRole,
    ShotPlan,
    ShotReferenceCue,
    ShotTimelineBeat,
    SpatialInteractionConstraint,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
)
from short_drama.quality_learning import H3CapabilityProfileResponse
from short_drama.style_context import declared_style_value
from short_drama.video_inspection import (
    selected_generation_reference_ids,
    semantic_reference_entries,
)


class KeyframePosition(StrEnum):
    TOPOLOGY_REFERENCE = "TOPOLOGY_REFERENCE"
    START = "START"
    END = "END"


class ReferencePackEntryCreateRequest(DomainModel):
    subject_asset_id: str = Field(description=(
        "CHARACTER/LOCATION/PROP whose reference pack receives this use. This is the depicted subject, "
        "not necessarily the asset containing the image. A REFERENCE container can be used directly "
        "by its own COMPOSITION Shot cue without a pack; do not register a pack on that container."
    ))
    asset_version_id: str = Field(description=(
        "Exact current usable DRAFT/APPROVED image, including one stored in another asset. "
        "One composition image may have separate entries for several depicted subjects; "
        "register only the actual uses needed by their cues. No file is moved or approved."
    ))
    role: ReferenceRole = Field(description=(
        "Use within this subject's pack; must match its kind. CANONICAL_MASTER requires the subject's "
        "own image. An interaction/state/composition reference does not replace that identity source."
    ))
    lineage_root_version_id: str | None = Field(default=None, description=(
        "Optional exact current usable IMAGE owned by subject_asset_id that establishes this subject's "
        "lineage. Not an arbitrary editing parent or the composition container's base image. "
        "Omit when no such lineage is being asserted; CANONICAL_MASTER uses its own source image."
    ))
    angle: ReferenceAngle = ReferenceAngle.NOT_APPLICABLE
    framing: ReferenceFraming = ReferenceFraming.NOT_APPLICABLE
    tags: list[str] = Field(default_factory=list[str], max_length=32)
    description: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=0, ge=-100, le=100)


class ReferencePackEntryMutationResponse(DomainModel):
    pack: ReferencePack
    entry: ReferencePackEntry
    project: ProjectAggregate


class ReferencePackEntriesCreateRequest(DomainModel):
    entries: list[ReferencePackEntryCreateRequest] = Field(min_length=1, max_length=16,
        description="Already selected uses of existing images for one subject. Different versions and roles may share the subject's exact lineage root; no generation or approval is performed.")

    @model_validator(mode="after")
    def one_subject_and_distinct_uses(self) -> ReferencePackEntriesCreateRequest:
        subject_id = self.entries[0].subject_asset_id
        seen: dict[tuple[str, ReferenceRole, ReferenceAngle, ReferenceFraming], int] = {}
        for index, entry in enumerate(self.entries):
            if entry.subject_asset_id != subject_id:
                raise ValueError(f"entries[{index}] belongs to a different subject; a batch registers one subject's uses")
            key = (entry.asset_version_id, entry.role, entry.angle, entry.framing)
            if key in seen:
                raise ValueError(f"entries[{index}] repeats the same version/role/angle/framing as entries[{seen[key]}]")
            seen[key] = index
        return self


class ReferencePackEntriesMutationResponse(DomainModel):
    pack: ReferencePack
    entry_ids: list[str] = Field(description="Actual saved entry IDs in request order; full entries occur once in pack.entries.")
    created_entry_ids: list[str]
    reused_entry_ids: list[str]


def reference_entry_context(document: ProjectDocument, request: ReferencePackEntryCreateRequest) -> str:
    """Small relationship facts for a failed request, not a saved entry."""
    assets = {asset.id: asset for asset in document.assets}
    owners = {version.id: asset.id for asset in document.assets for version in asset.versions}
    subject = assets.get(request.subject_asset_id)
    return (
        f"subject={request.subject_asset_id} (kind={subject.kind.value if subject else 'MISSING'}), "
        f"source={request.asset_version_id} (owner={owners.get(request.asset_version_id, 'MISSING')}), "
        f"role={request.role.value}, root={request.lineage_root_version_id} "
        f"(owner={owners.get(request.lineage_root_version_id or '', 'NONE_OR_MISSING')})"
    )


class ShotReferencePlanUpdateRequest(DomainModel):
    requirements: list[ReferenceRequirement] = Field(
        default_factory=list[ReferenceRequirement]
    )
    action_beats: list[ActionBeat] = Field(default_factory=list[ActionBeat])
    interactions: list[SpatialInteractionConstraint] = Field(
        default_factory=list[SpatialInteractionConstraint]
    )
    max_reference_images: int = Field(default=3, ge=1, le=9)
    planning_notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> ShotReferencePlanUpdateRequest:
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Reference requirement IDs must be unique")
        interaction_ids = [item.id for item in self.interactions]
        if len(interaction_ids) != len(set(interaction_ids)):
            raise ValueError("Spatial interaction IDs must be unique")
        beat_orders = [item.order for item in self.action_beats]
        if len(beat_orders) != len(set(beat_orders)):
            raise ValueError("Action beat order values must be unique")
        return self


class ReferencePlanPreviewRequest(DomainModel):
    requirement_ids: list[str] = Field(default_factory=list[str], max_length=32)
    max_images: int | None = Field(default=None, ge=1, le=9)


class KeyframeBriefCompileRequest(ReferencePlanPreviewRequest):
    position: KeyframePosition
    start_after_action_beat_order: int | None = Field(default=None, ge=0)
    primary_canvas_version_id: str | None = None
    extra_constraints: list[str] = Field(default_factory=list[str], max_length=32)

    @model_validator(mode="after")
    def validate_action_boundary(self) -> KeyframeBriefCompileRequest:
        if (
            self.start_after_action_beat_order is not None
            and self.position != KeyframePosition.START
        ):
            raise ValueError(
                "start_after_action_beat_order is only valid for a START keyframe"
            )
        if (
            self.primary_canvas_version_id is not None
            and self.position == KeyframePosition.TOPOLOGY_REFERENCE
        ):
            raise ValueError(
                "A topology reference cannot use a character/action continuity canvas"
            )
        return self


class DirectorAdviceRequest(ReferencePlanPreviewRequest):
    user_direction: str | None = Field(default=None, max_length=2000)
    take_id: str | None = Field(default=None, max_length=200)
    inspection_id: str | None = Field(default=None, max_length=200)
    audio_inspection_id: str | None = Field(default=None, max_length=200)
    episode_review_id: str | None = Field(default=None, max_length=200)
    method_skill_ids: SkillIds | None = Field(
        default=None, description="Explicit method selection; omit to inherit the current WorkOrder, or pass [] for none.",
    )
    production_prompt_stage: ProductionPromptStage | None = Field(
        default=None, description="Current task stage; omitted values inherit the WorkOrder rather than a role-name guess.",
    )


class DirectorAdvisoryCategory(StrEnum):
    REFERENCE_GAP = "REFERENCE_GAP"
    TOPOLOGY = "TOPOLOGY"
    ACTION_COMPLEXITY = "ACTION_COMPLEXITY"
    SPLIT_SHOT = "SPLIT_SHOT"
    CAMERA = "CAMERA"
    CONTINUITY = "CONTINUITY"
    ROUTE = "ROUTE"
    AUDIO = "AUDIO"
    OTHER = "OTHER"


class DirectorAdvisory(DomainModel):
    category: DirectorAdvisoryCategory
    message: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    suggested_action: str | None = Field(default=None, max_length=1000)
    dismissible: Literal[True] = True


class DirectorRepairOption(DomainModel):
    strategy: DirectorRepairStrategy
    summary: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    tradeoff: str = Field(min_length=1, max_length=1000)
    recommended: bool = False
    dismissible: Literal[True] = True


class ShotPlanPatch(DomainModel):
    duration_seconds: float | None = Field(default=None, gt=0)
    narrative_purpose: str | None = Field(default=None, min_length=1)
    visual: str | None = Field(default=None, min_length=1)
    framing: str | None = Field(default=None, min_length=1)
    camera: str | None = Field(default=None, min_length=1)
    audio_intent: str | None = Field(default=None, min_length=1)
    dialogue_line_ids: list[str] | None = None
    timeline_beats: list[ShotTimelineBeat] | None = None
    references: list[ShotReferenceCue] | None = None
    continuity: ContinuityState | None = None
    transition_out: str | None = Field(default=None, min_length=1)
    merge_candidate: bool | None = None
    local_rework_risk: str | None = Field(default=None, min_length=1)


class ReplacementShotDraft(DomainModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    duration_seconds: float = Field(gt=0)
    narrative_purpose: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    framing: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    audio_intent: str = Field(min_length=1)
    dialogue_line_ids: list[str] = Field(default_factory=list[str])
    timeline_beats: list[ShotTimelineBeat] = Field(
        default_factory=list[ShotTimelineBeat],
        max_length=24,
    )
    references: list[ShotReferenceCue] | None = Field(default=None, description="显式指定拆分镜引用；省略时保留原镜当前选用媒体，不虚构正文锚点。")
    continuity: ContinuityState
    transition_out: str = Field(min_length=1)
    merge_candidate: bool = False
    local_rework_risk: str = Field(min_length=1)
    inherits_source_dependencies: bool = False
    reference_plan: ShotReferencePlanUpdateRequest | None = None


class DirectorRepairDecisionRequest(DomainModel):
    decision: DirectorRepairDecision
    strategy: DirectorRepairStrategy
    reason: str = Field(min_length=1, max_length=4000)
    source_take_id: str | None = Field(default=None, max_length=200)
    expected_shot_revision: int | None = Field(default=None, ge=1, description="读取修复对象时的当前修订；仅保护该镜头，不阻挡无关修改。")
    issue_codes: list[str] = Field(default_factory=list[str], max_length=32)
    shot_patch: ShotPlanPatch | None = None
    reference_plan: ShotReferencePlanUpdateRequest | None = None
    split_shots: list[ReplacementShotDraft] = Field(
        default_factory=list[ReplacementShotDraft],
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_repair_shape(self) -> DirectorRepairDecisionRequest:
        if self.decision == DirectorRepairDecision.IGNORED:
            return self
        if self.strategy == DirectorRepairStrategy.SPLIT_SHOT:
            if len(self.split_shots) < 2:
                raise ValueError("Applying SPLIT_SHOT requires at least two replacement shots")
            if self.shot_patch is not None:
                raise ValueError("SPLIT_SHOT cannot also patch the original shot")
            if sum(item.inherits_source_dependencies for item in self.split_shots) > 1:
                raise ValueError(
                    "At most one replacement shot may inherit source-shot dependencies"
                )
            return self
        if self.split_shots:
            raise ValueError("split_shots are only valid for the SPLIT_SHOT strategy")
        if self.strategy == DirectorRepairStrategy.CHANGE_CAMERA:
            if self.shot_patch is None or self.shot_patch.camera is None:
                raise ValueError("Applying CHANGE_CAMERA requires a camera shot patch")
        if self.strategy == DirectorRepairStrategy.CHANGE_KEYFRAME:
            if self.shot_patch is None and self.reference_plan is None:
                raise ValueError(
                    "Applying CHANGE_KEYFRAME requires a shot or reference-plan change"
                )
        if self.strategy == DirectorRepairStrategy.PROMPT_RETRY:
            if self.shot_patch is None:
                raise ValueError(
                    "Applying PROMPT_RETRY requires a shot patch that changes compiled intent"
                )
        return self


class DirectorAdvicePayload(DomainModel):
    advisories: list[DirectorAdvisory] = Field(
        default_factory=list[DirectorAdvisory],
        max_length=12,
    )
    repair_options: list[DirectorRepairOption] = Field(
        default_factory=list[DirectorRepairOption],
        max_length=4,
    )
    recommended_strategy: DirectorRepairStrategy | None = None
    proposed_repair: DirectorRepairDecisionRequest | None = None

    @model_validator(mode="after")
    def validate_recommendation_contract(self) -> DirectorAdvicePayload:
        recommended_options = [item for item in self.repair_options if item.recommended]
        if len(recommended_options) > 1:
            raise ValueError("Director Agent may recommend at most one repair option")
        if self.recommended_strategy is None:
            if recommended_options or self.proposed_repair is not None:
                raise ValueError(
                    "A recommended option or proposed repair requires recommended_strategy"
                )
            return self
        if self.repair_options:
            if not recommended_options:
                raise ValueError(
                    "recommended_strategy requires one matching recommended repair option"
                )
            if recommended_options[0].strategy != self.recommended_strategy:
                raise ValueError(
                    "The recommended repair option must match recommended_strategy"
                )
        if self.proposed_repair is None:
            raise ValueError("recommended_strategy requires an executable proposed_repair")
        if self.proposed_repair.strategy != self.recommended_strategy:
            raise ValueError("proposed_repair strategy must match recommended_strategy")
        if self.proposed_repair.decision != DirectorRepairDecision.APPLIED:
            raise ValueError("proposed_repair must describe the APPLIED repair")
        return self


class DirectorAdviceResponse(DirectorAdvicePayload):
    provider: str = "openai-compatible"
    model: str = LANGUAGE_MODEL
    shot_id: str
    capability_profile_version: str
    qa_issue_codes: list[str] = Field(default_factory=list[str])
    proceed_allowed: Literal[True] = True
    method_skill_ids: list[str] = Field(default_factory=list[str])
    production_prompt_stage: ProductionPromptStage = ProductionPromptStage.STORYBOARD


class DirectorLoopResponse(DomainModel):
    advice: DirectorAdviceResponse
    repair_applied: bool
    next_action: Literal["PREPARE_H3_GROUP", "REGENERATE_SHOT", "AWAIT_USER", "NO_CHANGE"]
    next_tool: str | None = None
    next_tool_arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    project: ProjectAggregate


class ReferenceSelection(DomainModel):
    pack_id: str
    entry_id: str
    subject_asset_id: str
    asset_version_id: str
    lineage_root_version_id: str | None = None
    role: ReferenceRole
    angle: ReferenceAngle
    framing: ReferenceFraming
    tags: list[str]
    description: str
    requirement_ids: list[str]
    score: int
    reasons: list[str]


class ReferenceComplexityAssessment(DomainModel):
    duration_seconds: float
    action_beat_count: int
    interaction_count: int
    missing_topology_anchors: list[str]
    observations: list[str]
    advisory_only: Literal[True] = True


class ReferencePlanPreviewResponse(DomainModel):
    shot_id: str
    selected: list[ReferenceSelection]
    missing_requirement_ids: list[str]
    warnings: list[str]
    complexity: ReferenceComplexityAssessment
    proceed_allowed: Literal[True] = True


class KeyframeBriefCompileResponse(ReferencePlanPreviewResponse):
    position: KeyframePosition
    prompt: str
    input_version_ids: list[str]
    primary_input_version_id: str | None = None


_ROLES_BY_ASSET_KIND: dict[AssetKind, set[ReferenceRole]] = {
    AssetKind.CHARACTER: {
        ReferenceRole.CANONICAL_MASTER,
        ReferenceRole.CHARACTER_FACE,
        ReferenceRole.CHARACTER_BODY,
        ReferenceRole.CHARACTER_PROFILE,
        ReferenceRole.CHARACTER_BACK,
        ReferenceRole.CHARACTER_REFERENCE_SHEET,
    },
    AssetKind.LOCATION: {
        ReferenceRole.CANONICAL_MASTER,
        ReferenceRole.LOCATION_MASTER,
        ReferenceRole.LOCATION_REVERSE,
        ReferenceRole.LOCATION_INTERACTION,
    },
    AssetKind.PROP: {
        ReferenceRole.CANONICAL_MASTER,
        ReferenceRole.PROP_PRODUCT,
        ReferenceRole.PROP_IN_HAND,
        ReferenceRole.PROP_STATE,
    },
    AssetKind.REFERENCE: {ReferenceRole.CANONICAL_MASTER},
}


def role_is_compatible(asset_kind: AssetKind, role: ReferenceRole) -> bool:
    return role in _ROLES_BY_ASSET_KIND.get(asset_kind, set())


def preview_reference_plan(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: ReferencePlanPreviewRequest,
) -> ReferencePlanPreviewResponse:
    requirements = _filtered_requirements(plan, request.requirement_ids)
    cues = [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]
    explicit_subject_ids = {cue.subject_asset_id for cue in cues}
    editorial_only_subject_ids = explicit_subject_ids - {
        cue.subject_asset_id for cue in cues if cue.usage == "GENERATION"
    }
    editorial_requirement_ids = [
        item.id for item in requirements if item.subject_asset_id in editorial_only_subject_ids
    ]
    requirements = [
        item for item in requirements if item.subject_asset_id not in editorial_only_subject_ids
    ]
    max_images = request.max_images or plan.max_reference_images
    valid_entries = usable_reference_pack_entries(document)
    selected, missing, warnings = _current_cue_selections(
        document, shot, plan, requirements, valid_entries,
        max_images=max_images, restrict_subjects=bool(request.requirement_ids),
    )
    current_version_ids = {
        cue.asset_version_id for cue in cues if cue.asset_version_id is not None
    }
    warnings.extend(
        f"{requirement_id}: current subject is EDITORIAL-only; excluded from generation references"
        for requirement_id in editorial_requirement_ids
    )
    # A current media choice also overrides older semantic views of that object.
    # In particular, an EDITORIAL cue must not revive its previous generation image.
    valid_entries = [
        (pack, entry) for pack, entry in valid_entries
        if pack.subject_asset_id not in explicit_subject_ids
    ]

    for requirement in requirements:
        reusable = next(
            (
                item
                for item in selected
                if item.subject_asset_id == requirement.subject_asset_id
                and item.role in requirement.acceptable_roles
                and set(requirement.required_tags).issubset(item.tags)
            ),
            None,
        )
        if reusable is not None:
            reusable.requirement_ids.append(requirement.id)
            reusable.reasons.append("same selected view satisfies another requirement")
            continue

        candidates = [
            (pack, entry)
            for pack, entry in valid_entries
            if pack.subject_asset_id == requirement.subject_asset_id
            and entry.role in requirement.acceptable_roles
            and set(requirement.required_tags).issubset(entry.tags)
            and all(item.entry_id != entry.id for item in selected)
        ]
        preferred_lineages = {
            item.lineage_root_version_id
            for item in selected
            if item.subject_asset_id == requirement.subject_asset_id
            and item.lineage_root_version_id is not None
        }
        ranked = sorted(
            (
                (
                    _entry_score(
                        requirement,
                        entry,
                        preferred_lineages=preferred_lineages,
                    ),
                    pack,
                    entry,
                )
                for pack, entry in candidates
            ),
            key=lambda item: (-item[0][0], item[2].id),
        )
        if not ranked or len(selected) >= max_images:
            if requirement.required:
                missing.append(requirement.id)
            reason = "no approved matching reference"
            if ranked and len(selected) >= max_images:
                reason = f"reference limit {max_images} reached"
            warnings.append(f"{requirement.id}: {reason}")
            continue
        (score, reasons), pack, entry = ranked[0]
        selected.append(
            ReferenceSelection(
                pack_id=pack.id,
                entry_id=entry.id,
                subject_asset_id=pack.subject_asset_id,
                asset_version_id=entry.asset_version_id,
                lineage_root_version_id=entry.lineage_root_version_id,
                role=entry.role,
                angle=entry.angle,
                framing=entry.framing,
                tags=list(entry.tags),
                description=entry.description,
                requirement_ids=[requirement.id],
                score=score,
                reasons=reasons,
            )
        )

    if requirements and len(selected) < max_images:
        binding_selected, binding_missing, binding_warnings = _binding_selections(
            document,
            plan,
            max_images=max(1, len(plan.bindings)),
            excluded_version_ids=current_version_ids,
        )
        requirements_by_subject = {
            requirement.subject_asset_id: requirement
            for requirement in requirements
        }
        ordered_bindings = sorted(
            binding_selected,
            key=lambda item: (
                item.subject_asset_id not in requirements_by_subject,
                next(
                    (
                        index
                        for index, requirement in enumerate(requirements)
                        if requirement.subject_asset_id == item.subject_asset_id
                    ),
                    len(requirements),
                ),
                -item.score,
            ),
        )
        selected_version_ids = {item.asset_version_id for item in selected}
        for fallback in ordered_bindings:
            if len(selected) >= max_images:
                break
            if request.requirement_ids and fallback.subject_asset_id not in requirements_by_subject:
                continue
            if fallback.asset_version_id in selected_version_ids:
                continue
            requirement = requirements_by_subject.get(fallback.subject_asset_id)
            if requirement is not None:
                fallback.requirement_ids = [requirement.id]
                fallback.reasons.append(
                    "approved binding used as a coarse fallback because no matching semantic "
                    "reference-pack entry is available"
                )
                warnings.append(
                    f"{requirement.id}: using an approved core asset as a coarse visual fallback; "
                    "the requested role, angle, or framing remains advisory"
                )
            else:
                fallback.reasons.append(
                    "approved binding supplements the shot-specific visual context"
                )
            selected.append(fallback)
            selected_version_ids.add(fallback.asset_version_id)
        missing.extend(item for item in binding_missing if item not in missing)
        warnings.extend(item for item in binding_warnings if item not in warnings)

    if not requirements and not request.requirement_ids:
        binding_selected, binding_missing, binding_warnings = _binding_selections(
            document,
            plan,
            max_images=max_images - len(selected),
            excluded_version_ids=current_version_ids,
        )
        selected.extend(binding_selected)
        missing.extend(binding_missing)
        warnings.extend(binding_warnings)

    if requirements:
        _ensure_required_subject_coverage(
            document=document,
            plan=plan,
            requirements=requirements,
            selected=selected,
            max_images=max_images,
            warnings=warnings,
            protected_version_ids=current_version_ids,
            allowed_subject_ids=(
                {item.subject_asset_id for item in requirements}
                if request.requirement_ids else None
            ),
        )

    complexity = _assess_complexity(shot, plan, selected)
    if complexity.missing_topology_anchors:
        warnings.append(
            "需为以下锚点生成或审批 LOCATION_INTERACTION 视图："
            + ", ".join(complexity.missing_topology_anchors)
        )
    if _has_competing_scene_geometry(selected):
        warnings.append(
            "已选人物参考图内嵌了场景几何，同时又选择了独立场景参考图。"
            "优先使用背景中性的人物视图，或在视频生成前把两者整合到一张镜头专用关键帧中。"
        )
    return ReferencePlanPreviewResponse(
        shot_id=shot.id,
        selected=selected,
        missing_requirement_ids=missing,
        warnings=warnings,
        complexity=complexity,
    )


def _current_cue_selections(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    requirements: list[ReferenceRequirement],
    entries: list[tuple[ReferencePack, ReferencePackEntry]],
    *,
    max_images: int,
    restrict_subjects: bool,
) -> tuple[list[ReferenceSelection], list[str], list[str]]:
    """Project current cue choices into the existing reference-selection contract."""
    cues = [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]
    if not cues:
        return [], [], []
    usable_ids = {
        version_id for version_id, _label, _path in semantic_reference_entries(
            document, plan, load_images=False,
        )
    }
    subjects = {asset.id: asset for asset in document.assets}
    scope = {item.subject_asset_id for item in requirements}
    selected: list[ReferenceSelection] = []
    missing: list[str] = []
    warnings: list[str] = []
    by_version: dict[str, ReferenceSelection] = {}
    for index, cue in enumerate(cues, start=1):
        if cue.usage != "GENERATION" or (restrict_subjects and cue.subject_asset_id not in scope):
            continue
        cue_id = cue.id or f"current-reference-{index}"
        asset = subjects.get(cue.subject_asset_id)
        if asset is None or cue.asset_version_id not in usable_ids:
            missing.append(cue_id)
            warnings.append(f"{cue_id}: current generation reference is unavailable; no older image substituted")
            continue
        version_id = cue.asset_version_id
        assert version_id is not None
        previous = by_version.get(version_id)
        if previous is not None:
            if cue.purpose not in previous.description:
                previous.description += f"; {cue.purpose}"
            continue
        if len(selected) >= max_images:
            missing.append(cue_id)
            warnings.append(f"{cue_id}: current generation reference limit {max_images} reached")
            continue
        matching_entries = [
            (pack, entry) for pack, entry in entries
            if pack.subject_asset_id == asset.id and entry.asset_version_id == version_id
        ]
        matching_requirements = [item for item in requirements if item.subject_asset_id == asset.id]
        ranked_entries = sorted(matching_entries, key=lambda item: -max(
            (_entry_score(requirement, item[1], preferred_lineages=set())[0]
             for requirement in matching_requirements
             if item[1].role in requirement.acceptable_roles
             and set(requirement.required_tags).issubset(item[1].tags)),
            default=item[1].priority,
        ))
        pack, entry = ranked_entries[0] if ranked_entries else (None, None)
        selection = ReferenceSelection(
            pack_id=pack.id if pack is not None else f"current-shot-reference:{shot.id}",
            entry_id=entry.id if entry is not None else f"current-shot-reference:{shot.id}:{cue_id}",
            subject_asset_id=asset.id,
            asset_version_id=version_id,
            lineage_root_version_id=(
                entry.lineage_root_version_id if entry is not None
                else version_id if asset.kind == AssetKind.LOCATION else None
            ),
            role=entry.role if entry is not None else ReferenceRole.CANONICAL_MASTER,
            angle=entry.angle if entry is not None else ReferenceAngle.NOT_APPLICABLE,
            framing=entry.framing if entry is not None else ReferenceFraming.NOT_APPLICABLE,
            tags=list(entry.tags) if entry is not None else [],
            description=cue.purpose,
            requirement_ids=[],
            score=2000 - index,
            reasons=["current ShotReferenceCue GENERATION media and purpose take priority"],
        )
        selected.append(selection)
        by_version[version_id] = selection
    return selected, missing, warnings


def _ensure_required_subject_coverage(
    *,
    document: ProjectDocument,
    plan: ReferencePlanEntry,
    requirements: list[ReferenceRequirement],
    selected: list[ReferenceSelection],
    max_images: int,
    warnings: list[str],
    protected_version_ids: set[str],
    allowed_subject_ids: set[str] | None,
) -> None:
    """Reserve one approved reference for every visible subject before extra views."""

    required_subject_ids: list[str] = []

    def require(subject_id: str) -> None:
        if allowed_subject_ids is not None and subject_id not in allowed_subject_ids:
            return
        if subject_id not in required_subject_ids:
            required_subject_ids.append(subject_id)

    for requirement in requirements:
        if requirement.required:
            require(requirement.subject_asset_id)
    for beat in plan.action_beats:
        require(beat.actor_asset_id)
    for interaction in plan.interactions:
        require(interaction.actor_asset_id)
        require(interaction.target_asset_id)
    for requirement in requirements:
        require(requirement.subject_asset_id)

    bindings, _binding_missing, _binding_warnings = _binding_selections(
        document,
        plan,
        max_images=max(1, len(plan.bindings)),
        excluded_version_ids=protected_version_ids,
    )
    binding_by_subject = {item.subject_asset_id: item for item in bindings}
    counts: dict[str, int] = {}
    for item in selected:
        counts[item.subject_asset_id] = counts.get(item.subject_asset_id, 0) + 1

    for subject_id in required_subject_ids:
        if counts.get(subject_id, 0) > 0:
            continue
        fallback = binding_by_subject.get(subject_id)
        if fallback is None:
            continue
        requirement = next(
            (item for item in requirements if item.subject_asset_id == subject_id),
            None,
        )
        if requirement is not None:
            fallback.requirement_ids = [requirement.id]
        fallback.reasons.append(
            "approved core asset reserved to prevent specialised views for another subject "
            "from consuming the whole provider input budget"
        )
        if len(selected) < max_images:
            selected.append(fallback)
        else:
            replaceable = [
                (index, item)
                for index, item in enumerate(selected)
                if counts.get(item.subject_asset_id, 0) > 1
                and item.asset_version_id not in protected_version_ids
            ]
            if not replaceable:
                continue
            replace_index, replaced = min(
                replaceable,
                key=lambda item: (item[1].score, -item[0]),
            )
            selected[replace_index] = fallback
            counts[replaced.subject_asset_id] -= 1
        counts[subject_id] = counts.get(subject_id, 0) + 1
        warning = (
            f"{requirement.id if requirement is not None else subject_id}: using an approved "
            "core asset to preserve per-subject visual coverage within the reference limit"
        )
        if warning not in warnings:
            warnings.append(warning)


def _binding_selections(
    document: ProjectDocument,
    plan: ReferencePlanEntry,
    *,
    max_images: int,
    excluded_version_ids: set[str] | None = None,
) -> tuple[list[ReferenceSelection], list[str], list[str]]:
    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    defaults = {
        kind: (
            ReferenceRole.CANONICAL_MASTER,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.NOT_APPLICABLE,
        )
        for kind in AssetKind
    }
    selected: list[ReferenceSelection] = []
    missing: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    current_generation_ids = set(selected_generation_reference_ids(document, plan))
    for index, binding in enumerate(plan.bindings, start=1):
        if binding.target_id not in current_generation_ids or (
            excluded_version_ids is not None and binding.target_id in excluded_version_ids
        ):
            continue
        binding_id = f"binding-{index}"
        resolved = version_index.get(binding.target_id)
        required = binding.required
        if resolved is None:
            if required:
                missing.append(binding_id)
            warnings.append(f"{binding_id}: referenced AssetVersion is unavailable")
            continue
        asset, version = resolved
        if asset.kind != binding.kind:
            raise ValueError(
                f"{binding_id}: ReferenceBinding kind {binding.kind.value} does not "
                f"match AssetVersion {version.id}'s asset kind {asset.kind.value}"
            )
        if (
            version.id in seen
            or version.approval_status not in {
                ApprovalStatus.DRAFT, ApprovalStatus.APPROVED,
            }
            or version.freshness != Freshness.CURRENT
            or version.media_type != AssetMediaType.IMAGE
            or (version.local_path is None and version.external_url is None)
        ):
            if version.id not in seen and required:
                missing.append(binding_id)
            if version.id not in seen:
                warnings.append(f"{binding_id}: selected visual media is unavailable")
            continue
        if len(selected) >= max_images:
            if required:
                missing.append(binding_id)
            warnings.append(f"{binding_id}: reference limit {max_images} reached")
            continue
        role, angle, framing = defaults[asset.kind]
        selected.append(
            ReferenceSelection(
                pack_id=f"lightweight-binding:{asset.id}",
                entry_id=f"lightweight-binding:{plan.shot_id}:{index}",
                subject_asset_id=asset.id,
                asset_version_id=version.id,
                lineage_root_version_id=(
                    version.id if asset.kind == AssetKind.LOCATION else None
                ),
                role=role,
                angle=angle,
                framing=framing,
                tags=[],
                description=binding.purpose,
                requirement_ids=[binding_id],
                score=1000 - index,
                reasons=["approved provider-neutral ReferenceBinding"],
            )
        )
        seen.add(version.id)
    return selected, missing, warnings


def compile_keyframe_brief(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: KeyframeBriefCompileRequest,
) -> KeyframeBriefCompileResponse:
    preview = preview_reference_plan(document, shot, plan, request)
    start_boundary = request.start_after_action_beat_order
    primary_canvas = (
        _resolve_primary_canvas(document, request.primary_canvas_version_id)
        if request.primary_canvas_version_id is not None
        else None
    )
    sorted_beats = sorted(plan.action_beats, key=lambda item: item.order)
    if start_boundary is not None:
        beat_orders = [item.order for item in sorted_beats]
        if start_boundary and start_boundary not in beat_orders:
            raise ValueError(
                f"Action beat order {start_boundary} does not exist in shot {shot.id}"
            )
        if beat_orders and start_boundary >= beat_orders[-1]:
            raise ValueError(
                "A START keyframe boundary must leave at least one action beat to perform"
            )

    asset_index = {asset.id: asset for asset in document.assets}
    selected_inputs = [
        item
        for item in preview.selected
        if item.asset_version_id != request.primary_canvas_version_id
    ]
    reference_lines: list[str] = []
    if primary_canvas is not None:
        source_asset, source_version = primary_canvas
        reference_lines.append(
            "图片 1：主编辑画布；"
            f"来源={source_asset.name} v{source_version.version}；严格保留其机位、裁切、"
            "物体实例、拓扑、左右手与接触侧。"
        )
    reference_lines.extend(
        (
            f"图片 {index}：{asset_index[item.subject_asset_id].name}；"
            f"角色={item.role.value}；角度={item.angle.value}；"
            f"景别={item.framing.value}；仅用于 {item.description}。"
        )
        for index, item in enumerate(
            selected_inputs,
            start=2 if primary_canvas is not None else 1,
        )
    )
    beat_lines = [
        _format_action_beat(item, asset_index)
        for item in sorted_beats
    ]
    interaction_lines = [
        _format_interaction(item, asset_index)
        for item in plan.interactions
    ]
    physical_topology_lines = [
        line
        for item in plan.interactions
        if (line := _format_physical_topology_rule(item)) is not None
    ]
    style = _current_style(document)
    position_guidance = _position_guidance(
        request.position,
        plan,
        start_after_action_beat_order=start_boundary,
    )
    extra = [item.strip() for item in request.extra_constraints if item.strip()]
    is_topology_reference = request.position == KeyframePosition.TOPOLOGY_REFERENCE
    production_medium = declared_style_value(style.medium) if style is not None else None
    medium_suffix = f"，制作媒介为 {production_medium}" if production_medium is not None else ""

    sections = [
        (
            f"生成一张独立的环境拓扑参考图{medium_suffix}。"
            "画面不得出现人物、身体部位、被携带的道具、人物剪影或人物倒影。"
            "不要生成拼贴图、网格图、分屏、转面图、字幕、标志、水印、箭头、标签"
            "或可读的标牌文字。"
            if is_topology_reference
            else f"生成一张独立的镜头关键帧{medium_suffix}。"
            "不要生成拼贴图、网格图、分屏、转面图、字幕、标志、水印"
            "或可读的标牌文字。"
        ),
        (
            "场景与机位上下文：\n"
            f"景别：{shot.framing}\n"
            f"机位方向：{shot.camera}\n"
            "镜头信息仅用于确定场景中哪一部分需要清晰无歧义的交互布局；"
            "不要呈现镜头中的人物或动作。"
            if is_topology_reference
            else "已锁定镜头：\n"
            f"叙事目的：{shot.narrative_purpose}\n"
            f"画面：{shot.visual}\n"
            f"景别：{shot.framing}\n"
            f"机位：{shot.camera}"
        ),
        "参考图对照表：\n"
        + ("\n".join(reference_lines) if reference_lines else "未提供视觉参考图。"),
        "画面意图：\n" + position_guidance,
    ]
    if primary_canvas is not None:
        sections.append(
            "主画布锁定项：\n"
            "图片 1 是权威的物理画面，不是可互换的参考图。保留其机位、镜头、裁切、"
            "背景布局、物体实例数量、机械连接、左右方向、演员用手习惯以及手与物体的"
            "接触侧，包括每个子部件归属的物理表面。只应用「画面意图」和已声明动作节拍"
            "所要求的状态变化。后续参考图可以完善身份或外观，但不得取代图片 1 的几何结构。"
        )
    if beat_lines and not is_topology_reference:
        if start_boundary is None:
            sections.append("有序动作节拍：\n" + "\n".join(beat_lines))
        else:
            completed_lines = [
                line
                for line, beat in zip(beat_lines, sorted_beats, strict=True)
                if beat.order <= start_boundary
            ]
            remaining_lines = [
                line
                for line, beat in zip(beat_lines, sorted_beats, strict=True)
                if beat.order > start_boundary
            ]
            if completed_lines:
                sections.append(
                    "此帧之前已完成的动作节拍：\n"
                    + "\n".join(completed_lines)
                )
            sections.append(
                "此帧之后待完成的动作节拍：\n"
                + "\n".join(remaining_lines)
            )
    if interaction_lines:
        sections.append(
            (
                "需要在画面中清晰表达的交互拓扑：\n"
                + "\n".join(
                    _format_topology_interaction(item, asset_index)
                    for item in plan.interactions
                )
                if is_topology_reference
                else "空间交互与不变项：\n"
                + "\n".join(interaction_lines)
            )
        )
    if physical_topology_lines and not is_topology_reference:
        sections.append(
            "刚性组装与接触规则：\n"
            + "\n".join(physical_topology_lines)
            + "\n可见性只能因正常的透视或遮挡发生变化。已声明部件不得物理消失、"
            "转移到其他表面、与成对部件互换，或从移动目标上脱离。"
        )
    if not is_topology_reference:
        character_continuity = _character_continuity_lines(
            shot,
            plan,
            preview.selected,
            asset_index,
        )
        sections.append(
            "连续性：\n"
            + character_continuity
            + _prop_continuity_lines(
                shot,
                plan,
                request.position,
                start_boundary,
            )
        )
    if style:
        style_fields = [
            f"{label}: {value}"
            for label, raw_value in (
                ("媒介", style.medium),
                ("摄影", style.cinematography),
                ("色彩", style.color),
                ("质感", style.texture),
            )
            if (value := declared_style_value(raw_value)) is not None
        ]
        look_lines: list[str] = []
        if style_fields:
            look_lines.append("; ".join(style_fields) + ".")
        if style.forbidden:
            look_lines.append("禁止项：" + "；".join(style.forbidden))
    else:
        look_lines = []
    if look_lines:
        sections.append(
            "项目视觉风格：\n" + "\n".join(look_lines)
        )
    if extra:
        sections.append("额外锁定项：\n" + "\n".join(f"- {item}" for item in extra))
    sections.append(
        (
            "结果必须保持为一个物理连贯的环境。在不描绘人物执行动作的前提下，"
            "让可移动目标、它的机械边界与周边每个固定锚点在视觉上清晰可区分。"
            if is_topology_reference
            else "结果必须保持为一个物理连贯的空间。手、工具、门、按钮、抽屉、车辆部件"
            "或其他交互物体，都必须在画面中遵循指定的目标与状态转变；无关锚点不得移动。"
        )
    )
    input_version_ids: list[str] = []
    if request.primary_canvas_version_id is not None:
        input_version_ids.append(request.primary_canvas_version_id)
    for item in selected_inputs:
        if item.asset_version_id not in input_version_ids:
            input_version_ids.append(item.asset_version_id)
    return KeyframeBriefCompileResponse(
        **preview.model_dump(),
        position=request.position,
        prompt="\n\n".join(sections),
        input_version_ids=input_version_ids,
        primary_input_version_id=request.primary_canvas_version_id,
    )


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
        raise ValueError("Unknown reference requirement IDs: " + ", ".join(unknown))
    return [item for item in plan.requirements if item.id in requested]


def _resolve_primary_canvas(
    document: ProjectDocument,
    version_id: str,
) -> tuple[Asset, AssetVersion]:
    for asset in document.assets:
        version = next((item for item in asset.versions if item.id == version_id), None)
        if version is None:
            continue
        if version.media_type != AssetMediaType.IMAGE:
            raise ValueError("Primary edit canvas must be an image AssetVersion")
        if version.local_path is None and version.external_url is None:
            raise ValueError("Primary edit canvas has no usable media")
        return asset, version
    raise ValueError(f"Unknown primary edit canvas AssetVersion: {version_id}")


def usable_reference_pack_entries(
    document: ProjectDocument,
) -> list[tuple[ReferencePack, ReferencePackEntry]]:
    """Usable, explicitly selected entries shared by reads and shot selection."""

    usable_version_owners: dict[str, str] = {}
    for asset in document.assets:
        for version in asset.versions:
            if (
                version.approval_status in {
                    ApprovalStatus.DRAFT, ApprovalStatus.APPROVED,
                }
                and version.freshness == Freshness.CURRENT
                and version.media_type == AssetMediaType.IMAGE
                and (version.local_path is not None or version.external_url is not None)
            ):
                usable_version_owners[version.id] = asset.id
    return [
        (pack, entry)
        for pack in document.reference_packs
        for entry in pack.entries
        if entry.asset_version_id in usable_version_owners
        and (
            entry.lineage_root_version_id is None
            or usable_version_owners.get(entry.lineage_root_version_id)
            == pack.subject_asset_id
        )
    ]


def _entry_score(
    requirement: ReferenceRequirement,
    entry: ReferencePackEntry,
    *,
    preferred_lineages: set[str],
) -> tuple[int, list[str]]:
    score = entry.priority
    reasons = [f"priority {entry.priority}"]
    role_rank = requirement.acceptable_roles.index(entry.role)
    role_score = 100 - role_rank * 10
    score += role_score
    reasons.append(f"role rank +{role_score}")
    if entry.angle in requirement.preferred_angles:
        angle_rank = requirement.preferred_angles.index(entry.angle)
        angle_score = 30 - angle_rank * 5
        score += angle_score
        reasons.append(f"preferred angle +{angle_score}")
    if entry.framing in requirement.preferred_framings:
        framing_rank = requirement.preferred_framings.index(entry.framing)
        framing_score = 20 - framing_rank * 4
        score += framing_score
        reasons.append(f"preferred framing +{framing_score}")
    if requirement.required_tags:
        tag_score = len(requirement.required_tags) * 10
        score += tag_score
        reasons.append(f"required tags +{tag_score}")
    if (
        preferred_lineages
        and entry.lineage_root_version_id is not None
        and entry.lineage_root_version_id in preferred_lineages
    ):
        score += 40
        reasons.append("same canonical master lineage +40")
    return score, reasons


def _assess_complexity(
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    selected: list[ReferenceSelection],
) -> ReferenceComplexityAssessment:
    observations = [
        f"镜头时长为 {shot.duration_seconds:g} 秒，"
        f"共声明 {len(plan.action_beats)} 个动作节拍。"
    ]

    selected_by_subject = {
        item.subject_asset_id: [candidate for candidate in selected if candidate.subject_asset_id == item.subject_asset_id]
        for item in selected
    }
    missing_topology: list[str] = []
    for interaction in plan.interactions:
        target_views = selected_by_subject.get(interaction.target_asset_id, [])
        topology_tokens = [interaction.target_anchor]
        if interaction.contact_anchor is not None:
            topology_tokens.append(interaction.contact_anchor)
        if interaction.contact_surface is not None:
            topology_tokens.append(interaction.contact_surface)
        for attachment in interaction.rigid_attachments:
            topology_tokens.extend((attachment.anchor, attachment.surface))
        for token in dict.fromkeys(topology_tokens):
            has_token = any(
                item.role == ReferenceRole.LOCATION_INTERACTION and token in item.tags
                for item in target_views
            )
            if not has_token:
                missing_topology.append(token)
    if missing_topology:
        observations.append(
            "以下已声明交互锚点没有匹配的已审批 LOCATION_INTERACTION 视图："
            + ", ".join(sorted(set(missing_topology)))
        )
    if len({item.target_anchor for item in plan.interactions}) > 1:
        observations.append(
            "当前镜头中声明了不止一个交互目标。"
        )
    if not plan.interactions:
        observations.append("当前镜头未显式声明空间交互。")
    if _has_competing_scene_geometry(selected):
        observations.append(
            "人物参考图内嵌了自身场景几何，同时又选择了独立场景参考图；"
            "独立的视频参考可能造成入口、窗户、柜台或其他场景结构重复或漂移。"
        )
    return ReferenceComplexityAssessment(
        duration_seconds=shot.duration_seconds,
        action_beat_count=len(plan.action_beats),
        interaction_count=len(plan.interactions),
        missing_topology_anchors=sorted(set(missing_topology)),
        observations=observations,
    )


def _has_competing_scene_geometry(selected: list[ReferenceSelection]) -> bool:
    character_roles = {
        ReferenceRole.CHARACTER_FACE,
        ReferenceRole.CHARACTER_BODY,
        ReferenceRole.CHARACTER_PROFILE,
        ReferenceRole.CHARACTER_BACK,
        ReferenceRole.CHARACTER_REFERENCE_SHEET,
    }
    location_roles = {
        ReferenceRole.LOCATION_MASTER,
        ReferenceRole.LOCATION_REVERSE,
        ReferenceRole.LOCATION_INTERACTION,
    }
    character_embeds_scene = any(
        item.role in character_roles and "background.scene_embedded" in item.tags
        for item in selected
    )
    has_location_reference = any(item.role in location_roles for item in selected)
    return character_embeds_scene and has_location_reference


def build_director_advice_input(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    preview: ReferencePlanPreviewResponse,
    request: DirectorAdviceRequest,
    qa_issues: list[QaIssue],
    reviewer_notes: list[str],
    capability_profile: H3CapabilityProfileResponse,
    prior_repairs: list[DirectorRepairRecord],
    *,
    current_group: dict[str, JsonValue] | None = None,
    media_evidence: dict[str, JsonValue] | None = None,
    actual_previous_media_boundary: dict[str, object] | None = None,
) -> tuple[str, str]:
    assets = {item.id: item for item in document.assets}
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.DIRECTOR,),
        include_constitution=False,
        stage=request.production_prompt_stage or ProductionPromptStage.STORYBOARD,
        method_skill_ids=request.method_skill_ids or (),
    )
    instructions = (
        "你是当前视频制作流程中的导演 Agent，负责基于真实制作上下文向用户提出建议。"
        f"{skill_prompt.text} 只返回符合所给 schema 的 JSON。请结合当前镜头的具体情况判断，"
        "不要用固定的动作数量或时长阈值代替导演判断。针对实际 QA 失败和 Provider 能力画像，"
        "比较修改提示词、替换关键帧、拆分镜头和更换覆盖机位等修复路径。若已有证据指向路线选择"
        "或关键帧不匹配，不要默认再次修改提示词。某项修复已经执行、随后同类 QA 仍然失败时，"
        "应把这段历史视为因果证据；除非新方案实质改变输入并解释为何这次会奏效，否则不要重复"
        "同一套无效策略，必要时从修复策略库中选择更合适的方案。所有建议均可选、可忽略，"
        "不得声称生成被阻塞，也不得要求用户必须采纳。若没有能实质提升质量的建议，请返回空的 "
        "advisories 和 repair_options。"
        "当前素材证据区分计划、已采用媒体与实际观察，检查只覆盖其文件、时间与问题。"
        "抽帧或画面描述不能证明原声或两帧之间的运动。若当前阶段为 POST，优先给出可行的剪辑、"
        "采用区间、声音或图版建议；不能把没有对应执行能力的建议伪装成 proposed_repair。"
    )
    payload = {
        "output_schema": {
            "advisories": [
                {
                    "category": "以下枚举值之一："
                    + " | ".join(item.value for item in DirectorAdvisoryCategory),
                    "message": "简洁的建议",
                    "rationale": "针对当前镜头的具体理由",
                    "suggested_action": "可选的下一步具体行动",
                    "dismissible": True,
                }
            ],
            "repair_options": [
                {
                    "strategy": "以下枚举值之一："
                    + " | ".join(item.value for item in DirectorRepairStrategy),
                    "summary": "将要改变什么",
                    "rationale": "为何能处理已观察到的 QA 失败",
                    "tradeoff": "在质量、连续性、时间或成本上的取舍",
                    "recommended": "布尔值；最多一个选项为 true",
                    "dismissible": True,
                }
            ],
            "recommended_strategy": "一个策略枚举值或 null",
            "proposed_repair": {
                "decision": "APPLIED",
                "strategy": "与 recommended_strategy 相同的枚举值",
                "reason": "简洁且可执行的理由",
                "source_take_id": request.take_id,
                "issue_codes": ["来自 qa_failures 的问题代码"],
                "shot_patch": (
                    "可选、与 Provider 无关的 ShotPlan 字段补丁；不变时使用 null"
                ),
                "reference_plan": (
                    "可选更新对象，只能包含 requirements、action_beats、interactions、"
                    "max_reference_images 和 planning_notes；不得返回 shot_id、bindings、"
                    "capability_valid 或 validation_notes；不变时使用 null"
                ),
                "split_shots": (
                    "仅供 SPLIT_SHOT 使用，至少包含两个完整的替代镜头草案；"
                    "其他策略返回空数组"
                ),
            },
        },
        "shot": shot.model_dump(mode="json"),
        "current_group": current_group,
        "media_evidence": media_evidence,
        "actual_previous_media_boundary": actual_previous_media_boundary,
        "selected_method_skill_ids": list(request.method_skill_ids or ()),
        "production_prompt_stage": (request.production_prompt_stage or ProductionPromptStage.STORYBOARD).value,
        "reference_plan": plan.model_dump(mode="json"),
        "recommended_reference_selection": [
            {
                **item.model_dump(mode="json"),
                "subject_name": assets[item.subject_asset_id].name,
            }
            for item in preview.selected
        ],
        "uncovered_reference_preferences": preview.missing_requirement_ids,
        "planning_signals": preview.complexity.model_dump(mode="json"),
        "qa_failures": [item.model_dump(mode="json") for item in qa_issues],
        "reviewer_notes": reviewer_notes,
        "prior_repairs": [
            item.model_dump(mode="json") for item in prior_repairs
        ],
        "provider_capability_profile": capability_profile.model_dump(mode="json"),
        "repair_strategy_library": {
            DirectorRepairStrategy.PROMPT_RETRY.value: (
                "保留当前镜头、机位和关键帧输入，在实质修改提示词后重新生成。"
            ),
            DirectorRepairStrategy.CHANGE_KEYFRAME.value: (
                "替换或修复一个或多个关键帧，再按同一镜头意图重新生成。"
            ),
            DirectorRepairStrategy.SPLIT_SHOT.value: (
                "把相互争夺表达空间的动作节拍分配到多个镜头，并同步更新依赖关系。"
            ),
            DirectorRepairStrategy.CHANGE_CAMERA.value: (
                "保留叙事节拍，改用能减少隐藏拓扑、接触关系或朝向要求的覆盖机位。"
            ),
        },
        "user_direction": request.user_direction,
        "run_mode": document.project.run_mode.value,
        "production_profile": document.project.production_profile.value,
        "product_rule": (
            "建议不得阻塞生成。在 AUTO_DRAFT 模式下，如推荐某个策略，应同时返回可执行的 "
            "proposed_repair，使流程无需等待用户即可继续；在 GUIDED 模式下，用户可以采纳或忽略。"
            "不得虚构场景专属的 schema 字段，不得要求最低节拍数量、固定镜头数量，也不得要求按固定"
            "时间间隔加入新信息。JSON 应保持精炼：advisories 最多三个，repair_options 最多四个，"
            "省略未变化的可选字段，每个叙述性字段不超过 300 个字符。"
        ),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def parse_director_advice(output_text: str) -> DirectorAdvicePayload:
    text = output_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = cast(object, json.loads(text))
        if isinstance(payload, dict):
            payload_object = cast(dict[str, object], payload)
            proposed_repair = payload_object.get("proposed_repair")
            if isinstance(proposed_repair, dict):
                repair_object = cast(dict[str, object], proposed_repair)
                reference_plan = repair_object.get("reference_plan")
                if isinstance(reference_plan, dict):
                    reference_plan_object = cast(dict[str, object], reference_plan)
                    # The model sometimes copies read-only fields from the current
                    # ReferencePlanEntry into the smaller update object. Dropping only
                    # those known envelope fields is lossless; all other unknown fields
                    # remain schema errors and are sent through the bounded repair pass.
                    for field_name in (
                        "shot_id",
                        "bindings",
                        "capability_valid",
                        "validation_notes",
                    ):
                        reference_plan_object.pop(field_name, None)
                split_shots = repair_object.get("split_shots")
                if isinstance(split_shots, list):
                    replacement_objects = [
                        cast(dict[str, object], item)
                        for item in cast(list[object], split_shots)
                        if isinstance(item, dict)
                    ]
                    for replacement in replacement_objects:
                        # ReplacementShotDraft is an authoring payload, not a copied
                        # persisted ShotPlan. Ignore the known storage envelope fields.
                        for field_name in (
                            "project_id",
                            "sequence",
                            "approval_status",
                            "freshness",
                            "revision",
                        ):
                            replacement.pop(field_name, None)
                    inherited = [
                        item
                        for item in replacement_objects
                        if item.get("inherits_source_dependencies") is True
                    ]
                    if len(inherited) > 1:
                        # Downstream continuity belongs to the final temporal fragment.
                        # Earlier fragments remain internal to the replacement sequence.
                        for replacement in inherited[:-1]:
                            replacement["inherits_source_dependencies"] = False
        return DirectorAdvicePayload.model_validate(payload)
    except ValueError as exc:
        detail = " ".join(str(exc).split())[:2000]
        raise ValueError(
            "Director Agent returned invalid advisory JSON: " + detail
        ) from exc


def build_director_advice_repair_input(
    *,
    original_input: str,
    invalid_output: str,
    validation_error: ValueError,
) -> tuple[str, str]:
    instructions = (
        "你正在修复导演 Agent 先前返回的一份无效 JSON。只返回一个完整、修正后的 JSON 对象，"
        "并严格匹配所给 schema。保留原响应中有效的建议和原始叙事意图，不得新增场景专属字段、"
        "强制门禁或不可忽略的指令。若存在推荐策略，proposed_repair 必须可执行、使用同一策略，"
        "并保持 decision=APPLIED。修正后的 JSON 应简洁，省略未变化的可选字段，每个叙述性字段"
        "不超过 300 个字符。对于 SPLIT_SHOT，只输出创作态字段，并且只允许最后一个替代镜头继承"
        "源镜头依赖。"
    )
    payload = {
        "validation_error": str(validation_error),
        "invalid_director_json": invalid_output,
        "original_director_context": json.loads(original_input),
        "output_schema": DirectorAdvicePayload.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _format_action_beat(beat: ActionBeat, assets: dict[str, Asset]) -> str:
    actor = assets.get(beat.actor_asset_id)
    actor_name = actor.name if actor is not None else beat.actor_asset_id
    target = f"；目标={beat.target_anchor}" if beat.target_anchor else ""
    end_state = (
        f"；要求的结束状态={beat.required_end_state}"
        if beat.required_end_state
        else ""
    )
    return f"{beat.order}. {actor_name}: {beat.action}{target}{end_state}"


def _format_interaction(
    interaction: SpatialInteractionConstraint,
    assets: dict[str, Asset],
) -> str:
    actor = assets.get(interaction.actor_asset_id)
    target = assets.get(interaction.target_asset_id)
    actor_name = actor.name if actor is not None else interaction.actor_asset_id
    target_name = target.name if target is not None else interaction.target_asset_id
    invariants = (
        "；不变锚点=" + "、".join(interaction.unchanged_anchors)
        if interaction.unchanged_anchors
        else ""
    )
    contact = ""
    if interaction.contact_anchor is not None:
        contact = f"；精确接触锚点={interaction.contact_anchor}"
        if interaction.contact_surface is not None:
            contact += f"，所在表面={interaction.contact_surface}"
        if interaction.contact_persists:
            contact += "；在释放前，保持同一执行部位与该精确锚点和表面接触"
    attachments = (
        "；刚性连接="
        + ", ".join(
            f"{item.anchor} 保持位于 {item.surface}"
            for item in interaction.rigid_attachments
        )
        if interaction.rigid_attachments
        else ""
    )
    return (
        f"- {actor_name} 使用 {interaction.effector.value} 对 "
        f"{target_name}:{interaction.target_anchor} 执行「{interaction.action}」；状态 "
        f"{interaction.start_state} -> {interaction.end_state}{contact}{attachments}{invariants}."
    )


def _format_topology_interaction(
    interaction: SpatialInteractionConstraint,
    assets: dict[str, Asset],
) -> str:
    target = assets.get(interaction.target_asset_id)
    target_name = target.name if target is not None else interaction.target_asset_id
    invariants = (
        "；在视觉上分离固定/不变锚点="
        + "、".join(interaction.unchanged_anchors)
        if interaction.unchanged_anchors
        else ""
    )
    contact = ""
    if interaction.contact_anchor is not None:
        contact = f"；可交互接触点={interaction.contact_anchor}"
        if interaction.contact_surface is not None:
            contact += f"，位于 {interaction.contact_surface}"
    attachments = (
        "；在归属表面上显示附属子部件="
        + ", ".join(
            f"{item.anchor} 位于 {item.surface}"
            for item in interaction.rigid_attachments
        )
        if interaction.rigid_attachments
        else ""
    )
    return (
        f"- 可交互目标={target_name}:{interaction.target_anchor}；预期状态"
        f"转变={interaction.start_state} -> {interaction.end_state}；让其把手、铰链/旋转轴/"
        f"轨道、边界与开启路径在物理上清晰可读{contact}"
        f"{attachments}{invariants}."
    )


def _format_physical_topology_rule(
    interaction: SpatialInteractionConstraint,
) -> str | None:
    if not interaction.rigid_attachments and not interaction.contact_persists:
        return None
    parts: list[str] = []
    if interaction.rigid_attachments:
        parts.append(
            "让 "
            + ", ".join(
                f"位于 {item.surface} 的 {item.anchor}"
                for item in interaction.rigid_attachments
            )
            + f" 与 {interaction.target_anchor} 作为同一刚性组件运动"
        )
    if interaction.contact_persists:
        anchor = interaction.contact_anchor or interaction.target_anchor
        surface = (
            f"，位于 {interaction.contact_surface}"
            if interaction.contact_surface is not None
            else ""
        )
        parts.append(
            f"保持 {interaction.effector.value} 持续连接于 {anchor}{surface}，"
            "不得穿过物体或转移到另一侧"
        )
    return "- " + "；".join(parts) + "。"


def _position_guidance(
    position: KeyframePosition,
    plan: ReferencePlanEntry,
    *,
    start_after_action_beat_order: int | None = None,
) -> str:
    if position == KeyframePosition.TOPOLOGY_REFERENCE:
        return (
            "生成一张动作发生前的干净交互拓扑参考图。可交互目标、它的控件/"
            "把手/铰链/轨道，以及每个不变锚点，都必须在视觉上清晰可区分。"
            "不要画出存在歧义的重复控件，此时也不要执行动作。"
        )
    if position == KeyframePosition.START:
        states = "; ".join(
            f"{item.target_anchor}="
            f"{_interaction_state_at_boundary(item, plan, start_after_action_beat_order)}"
            for item in plan.interactions
        )
        if start_after_action_beat_order is not None:
            return (
                f"呈现截至顺序 {start_after_action_beat_order} 的所有动作节拍都已完成后、"
                "下一个已声明节拍即将开始前的精确视频开场状态。不要重演已完成的节拍。"
                + (f"已锁定边界状态：{states}。" if states else "")
            )
        return (
            "呈现第一个必要动作节拍发生前一刻的精确开场状态。演员可以摆好"
            "准备动作的姿势，但尚未完成第一次状态转变。"
            + (f"已锁定起始状态：{states}。" if states else "")
        )
    ends = "; ".join(
        f"{item.target_anchor}={item.end_state}" for item in plan.interactions
    )
    return (
        "呈现所有必要动作节拍完成后的精确最终状态。保持人物身份、服装、"
        "场景几何、机位方向与物体设计一致。"
        + (f"已锁定结束状态：{ends}。" if ends else "")
    )


def _interaction_state_at_boundary(
    interaction: SpatialInteractionConstraint,
    plan: ReferencePlanEntry,
    boundary: int | None,
) -> str:
    if boundary is None:
        return interaction.start_state
    target_orders = [
        beat.order
        for beat in plan.action_beats
        if beat.target_anchor == interaction.target_anchor
    ]
    if target_orders and min(target_orders) <= boundary:
        return interaction.end_state
    return interaction.start_state


def _character_continuity_lines(
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    selected: list[ReferenceSelection],
    assets: dict[str, Asset],
) -> str:
    candidate_ids = list(
        dict.fromkeys(
            [
                *(item.subject_asset_id for item in selected),
                *(item.actor_asset_id for item in plan.action_beats),
                *(item.actor_asset_id for item in plan.interactions),
            ]
        )
    )
    visible_names = [
        asset.name
        for asset_id in candidate_ids
        if (asset := assets.get(asset_id)) is not None
        and asset.kind == AssetKind.CHARACTER
    ]
    visible_names = list(dict.fromkeys(visible_names))
    if len(visible_names) == 1:
        return (
            f"可见人物：仅有 {visible_names[0]}。更大范围连续性中提到的其他人物"
            "必须保持在画外。\n"
        )
    if len(visible_names) > 1:
        return (
            "人物与视线："
            f"{shot.continuity.character_positions}; {shot.continuity.eyeline}\n"
        )
    return ""


def _prop_continuity_lines(
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    position: KeyframePosition,
    start_boundary: int | None,
) -> str:
    if position == KeyframePosition.END:
        return f"此帧的道具/物体状态：{shot.continuity.prop_state_out}"
    if start_boundary is None:
        return (
            f"此帧的道具/物体状态：{shot.continuity.prop_state_in}\n"
            f"视频结束后要求的最终状态：{shot.continuity.prop_state_out}"
        )
    completed_states = [
        beat.required_end_state
        for beat in plan.action_beats
        if beat.order <= start_boundary and beat.required_end_state
    ]
    boundary_state = "; ".join(completed_states) or shot.continuity.prop_state_in
    return (
        f"由已完成节拍确定的此帧状态：{boundary_state}\n"
        f"视频结束后要求的最终状态：{shot.continuity.prop_state_out}"
    )


def _current_style(document: ProjectDocument):
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )
    return bible.style if bible is not None else None
