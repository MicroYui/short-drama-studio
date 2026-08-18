"""Generic reference-pack selection and spatial-interaction brief compilation."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from short_drama.models import (
    ActionBeat,
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetVersion,
    ContinuityState,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    DomainModel,
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
    SpatialInteractionConstraint,
)
from short_drama.quality_learning import H3CapabilityProfileResponse


class KeyframePosition(StrEnum):
    TOPOLOGY_REFERENCE = "TOPOLOGY_REFERENCE"
    START = "START"
    END = "END"


class ReferencePackEntryCreateRequest(DomainModel):
    subject_asset_id: str
    asset_version_id: str
    role: ReferenceRole
    angle: ReferenceAngle = ReferenceAngle.NOT_APPLICABLE
    framing: ReferenceFraming = ReferenceFraming.NOT_APPLICABLE
    tags: list[str] = Field(default_factory=list[str], max_length=32)
    description: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=0, ge=-100, le=100)


class ReferencePackEntryMutationResponse(DomainModel):
    pack: ReferencePack
    entry: ReferencePackEntry
    project: ProjectAggregate


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


class DirectorAdvisoryCategory(StrEnum):
    REFERENCE_GAP = "REFERENCE_GAP"
    TOPOLOGY = "TOPOLOGY"
    ACTION_COMPLEXITY = "ACTION_COMPLEXITY"
    SPLIT_SHOT = "SPLIT_SHOT"
    CAMERA = "CAMERA"
    CONTINUITY = "CONTINUITY"
    ROUTE = "ROUTE"
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
    model: str = "gpt-5.6-sol"
    shot_id: str
    capability_profile_version: str
    qa_issue_codes: list[str] = Field(default_factory=list[str])
    proceed_allowed: Literal[True] = True


class DirectorLoopResponse(DomainModel):
    advice: DirectorAdviceResponse
    repair_applied: bool
    next_action: Literal["REGENERATE_SHOT", "AWAIT_USER", "NO_CHANGE"]
    project: ProjectAggregate


class ReferenceSelection(DomainModel):
    pack_id: str
    entry_id: str
    subject_asset_id: str
    asset_version_id: str
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
        ReferenceRole.CHARACTER_FACE,
        ReferenceRole.CHARACTER_BODY,
        ReferenceRole.CHARACTER_PROFILE,
        ReferenceRole.CHARACTER_BACK,
    },
    AssetKind.LOCATION: {
        ReferenceRole.LOCATION_MASTER,
        ReferenceRole.LOCATION_REVERSE,
        ReferenceRole.LOCATION_INTERACTION,
    },
    AssetKind.PROP: {
        ReferenceRole.PROP_PRODUCT,
        ReferenceRole.PROP_IN_HAND,
        ReferenceRole.PROP_STATE,
    },
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
    max_images = request.max_images or plan.max_reference_images
    valid_entries = _valid_pack_entries(document)
    selected: list[ReferenceSelection] = []
    missing: list[str] = []
    warnings: list[str] = []

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
        ranked = sorted(
            (
                (_entry_score(requirement, entry), pack, entry)
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

    if not requirements and not request.requirement_ids:
        binding_selected, binding_missing, binding_warnings = _binding_selections(
            document,
            plan,
            max_images=max_images,
        )
        selected.extend(binding_selected)
        missing.extend(binding_missing)
        warnings.extend(binding_warnings)

    complexity = _assess_complexity(shot, plan, selected)
    if complexity.missing_topology_anchors:
        warnings.append(
            "Create or approve LOCATION_INTERACTION views for: "
            + ", ".join(complexity.missing_topology_anchors)
        )
    if _has_competing_scene_geometry(selected):
        warnings.append(
            "A selected character reference contains embedded scene geometry while a separate "
            "location reference is also selected. Prefer a background-neutral character view "
            "or bake both into one shot-specific keyframe before video generation."
        )
    return ReferencePlanPreviewResponse(
        shot_id=shot.id,
        selected=selected,
        missing_requirement_ids=missing,
        warnings=warnings,
        complexity=complexity,
    )


def _binding_selections(
    document: ProjectDocument,
    plan: ReferencePlanEntry,
    *,
    max_images: int,
) -> tuple[list[ReferenceSelection], list[str], list[str]]:
    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    defaults = {
        AssetKind.CHARACTER: (
            ReferenceRole.CHARACTER_BODY,
            ReferenceAngle.FRONT,
            ReferenceFraming.FULL_BODY,
        ),
        AssetKind.LOCATION: (
            ReferenceRole.LOCATION_MASTER,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
        ),
        AssetKind.PROP: (
            ReferenceRole.PROP_PRODUCT,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.DETAIL,
        ),
        AssetKind.REFERENCE: (
            ReferenceRole.LOCATION_MASTER,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
        ),
    }
    selected: list[ReferenceSelection] = []
    missing: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for index, binding in enumerate(plan.bindings, start=1):
        binding_id = f"binding-{index}"
        resolved = version_index.get(binding.target_id)
        required = binding.requirement.strip().upper() == "REQUIRED"
        if resolved is None:
            if required:
                missing.append(binding_id)
            warnings.append(f"{binding_id}: referenced AssetVersion is unavailable")
            continue
        asset, version = resolved
        if (
            version.id in seen
            or asset.approved_version_id != version.id
            or version.approval_status != ApprovalStatus.APPROVED
            or version.media_type != AssetMediaType.IMAGE
            or (version.local_path is None and version.external_url is None)
        ):
            if version.id not in seen and required:
                missing.append(binding_id)
            if version.id not in seen:
                warnings.append(f"{binding_id}: approved visual media is unavailable")
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
            "Image 1: PRIMARY EDIT CANVAS; "
            f"source={source_asset.name} v{source_version.version}; preserve its exact camera, "
            "crop, object instances, topology, handedness, and contact side."
        )
    reference_lines.extend(
        (
            f"Image {index}: {asset_index[item.subject_asset_id].name}; "
            f"role={item.role.value}; angle={item.angle.value}; "
            f"framing={item.framing.value}; use only for {item.description}."
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

    sections = [
        (
            "Generate ONE single vertical 9:16 live-action environment topology plate. "
            "Show no people, body parts, carried props, character silhouettes, or reflections "
            "of people. Do not create a collage, grid, split panel, turnaround sheet, caption, "
            "logo, watermark, arrows, labels, or readable signage."
            if is_topology_reference
            else "Generate ONE single vertical 9:16 live-action cinematic still. "
            "Do not create a collage, grid, split panel, turnaround sheet, caption, logo, "
            "watermark, or readable signage."
        ),
        (
            "LOCATION AND CAMERA CONTEXT:\n"
            f"Framing: {shot.framing}\n"
            f"Camera side: {shot.camera}\n"
            "Use the shot only to identify which part of the location needs an unambiguous "
            "interaction layout; do not render its characters or action."
            if is_topology_reference
            else "LOCKED SHOT:\n"
            f"Purpose: {shot.narrative_purpose}\n"
            f"Visual: {shot.visual}\n"
            f"Framing: {shot.framing}\n"
            f"Camera: {shot.camera}"
        ),
        "REFERENCE IMAGE MAP:\n"
        + ("\n".join(reference_lines) if reference_lines else "No visual references supplied."),
        "FRAME INTENT:\n" + position_guidance,
    ]
    if primary_canvas is not None:
        sections.append(
            "PRIMARY CANVAS LOCKS:\n"
            "Image 1 is the authoritative physical frame, not an alternative visual reference. "
            "Keep its camera position, lens, crop, background layout, number of object instances, "
            "mechanical attachments, left/right orientation, actor handedness, and hand-to-object "
            "contact side, including which physical surface owns each subpart. Apply only the "
            "state changes required by FRAME INTENT and the declared action beats. Later reference "
            "images may refine identity or appearance but must not replace Image 1 geometry."
        )
    if beat_lines and not is_topology_reference:
        if start_boundary is None:
            sections.append("ORDERED ACTION BEATS:\n" + "\n".join(beat_lines))
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
                    "ACTION BEATS COMPLETED BEFORE THIS FRAME:\n"
                    + "\n".join(completed_lines)
                )
            sections.append(
                "ACTION BEATS REMAINING AFTER THIS FRAME:\n"
                + "\n".join(remaining_lines)
            )
    if interaction_lines:
        sections.append(
            (
                "INTERACTION TOPOLOGY TO MAKE VISUALLY UNAMBIGUOUS:\n"
                + "\n".join(
                    _format_topology_interaction(item, asset_index)
                    for item in plan.interactions
                )
                if is_topology_reference
                else "SPATIAL INTERACTIONS AND INVARIANTS:\n"
                + "\n".join(interaction_lines)
            )
        )
    if physical_topology_lines and not is_topology_reference:
        sections.append(
            "RIGID ASSEMBLY AND CONTACT RULES:\n"
            + "\n".join(physical_topology_lines)
            + "\nVisibility may change only through normal perspective or occlusion. A declared "
            "part must not disappear physically, migrate to another surface, swap with a "
            "paired part, or detach from the moving target."
        )
    if not is_topology_reference:
        character_continuity = _character_continuity_lines(
            shot,
            plan,
            preview.selected,
            asset_index,
        )
        sections.append(
            "CONTINUITY:\n"
            + character_continuity
            + _prop_continuity_lines(
                shot,
                plan,
                request.position,
                start_boundary,
            )
        )
    if style:
        sections.append(
            "PROJECT LOOK:\n"
            f"Medium: {style.medium}; cinematography: {style.cinematography}; "
            f"color: {style.color}; texture: {style.texture}.\n"
            "Forbidden: " + "; ".join(style.forbidden)
        )
    if extra:
        sections.append("EXTRA LOCKS:\n" + "\n".join(f"- {item}" for item in extra))
    sections.append(
        (
            "The result must remain one physically coherent environment. Make the movable "
            "target, its mechanical boundary, and every fixed neighboring anchor visually "
            "distinct without depicting a person performing the action."
            if is_topology_reference
            else "The result must remain one physically coherent space. Any hand, tool, door, "
            "button, drawer, vehicle part, or other interacted object must visibly obey the "
            "specified target and state transition; unrelated anchors must not move."
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


def _valid_pack_entries(
    document: ProjectDocument,
) -> list[tuple[ReferencePack, ReferencePackEntry]]:
    approved_versions: dict[str, AssetVersion] = {}
    for asset in document.assets:
        for version in asset.versions:
            if (
                asset.approved_version_id == version.id
                and version.approval_status == ApprovalStatus.APPROVED
                and (version.local_path is not None or version.external_url is not None)
            ):
                approved_versions[version.id] = version
    return [
        (pack, entry)
        for pack in document.reference_packs
        for entry in pack.entries
        if entry.asset_version_id in approved_versions
    ]


def _entry_score(
    requirement: ReferenceRequirement,
    entry: ReferencePackEntry,
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
    return score, reasons


def _assess_complexity(
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    selected: list[ReferenceSelection],
) -> ReferenceComplexityAssessment:
    observations = [
        f"Shot duration is {shot.duration_seconds:g}s with "
        f"{len(plan.action_beats)} declared action beat(s)."
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
            "Declared interaction anchors without a matching approved "
            "LOCATION_INTERACTION view: " + ", ".join(sorted(set(missing_topology)))
        )
    if len({item.target_anchor for item in plan.interactions}) > 1:
        observations.append(
            "More than one declared interaction target appears in this shot."
        )
    if not plan.interactions:
        observations.append("No explicit spatial interaction was declared for this shot.")
    if _has_competing_scene_geometry(selected):
        observations.append(
            "A character reference embeds its own scene geometry while a separate location "
            "reference is selected; independent video references may create duplicate or "
            "drifting entrances, windows, counters, or other location structures."
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
) -> tuple[str, str]:
    assets = {item.id: item for item in document.assets}
    instructions = (
        "You are a film director agent advising a user who is producing a live-action "
        "vertical short drama. Return JSON only, matching the supplied schema. Evaluate "
        "the actual shot context; do not apply a fixed action-count or duration threshold. "
        "Compare prompt retry, keyframe replacement, shot split, and coverage-camera change "
        "against the actual QA failure and provider capability profile. A prompt retry should "
        "not be the default when prior evidence indicates a route or keyframe mismatch. Every "
        "item is optional and dismissible. Never claim that generation is blocked or that the "
        "user must follow your advice. Use an empty advisories array and repair_options array "
        "when no suggestion would materially improve quality."
    )
    payload = {
        "output_schema": {
            "advisories": [
                {
                    "category": "one of: "
                    + " | ".join(item.value for item in DirectorAdvisoryCategory),
                    "message": "concise recommendation",
                    "rationale": "shot-specific reason",
                    "suggested_action": "optional concrete next action",
                    "dismissible": True,
                }
            ],
            "repair_options": [
                {
                    "strategy": "one of: "
                    + " | ".join(item.value for item in DirectorRepairStrategy),
                    "summary": "what would change",
                    "rationale": "why it addresses the observed QA failure",
                    "tradeoff": "quality, continuity, time, or cost tradeoff",
                    "recommended": "boolean; at most one true",
                    "dismissible": True,
                }
            ],
            "recommended_strategy": "one strategy value or null",
            "proposed_repair": {
                "decision": "APPLIED",
                "strategy": "same value as recommended_strategy",
                "reason": "concise executable rationale",
                "source_take_id": request.take_id,
                "issue_codes": ["codes from qa_failures"],
                "shot_patch": (
                    "optional provider-neutral ShotPlan field patch; use null when unchanged"
                ),
                "reference_plan": (
                    "optional complete provider-neutral reference-plan update; use null when unchanged"
                ),
                "split_shots": (
                    "for SPLIT_SHOT only, at least two complete replacement shot drafts; "
                    "otherwise an empty array"
                ),
            },
        },
        "shot": shot.model_dump(mode="json"),
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
        "provider_capability_profile": capability_profile.model_dump(mode="json"),
        "repair_strategy_library": {
            DirectorRepairStrategy.PROMPT_RETRY.value: (
                "Regenerate with a materially revised prompt while keeping the current shot, "
                "camera, and keyframe inputs."
            ),
            DirectorRepairStrategy.CHANGE_KEYFRAME.value: (
                "Replace or repair one or more keyframes, then regenerate the same shot intent."
            ),
            DirectorRepairStrategy.SPLIT_SHOT.value: (
                "Distribute competing action beats across multiple shots and update dependencies."
            ),
            DirectorRepairStrategy.CHANGE_CAMERA.value: (
                "Keep the narrative beat but use a coverage angle that reduces hidden topology, "
                "contact, or orientation requirements."
            ),
        },
        "user_direction": request.user_direction,
        "run_mode": document.project.run_mode.value,
        "product_rule": (
            "Advice never blocks generation. In AUTO_DRAFT, return an executable proposed_repair "
            "for the recommended strategy so the workflow can continue without a user pause. In "
            "GUIDED, the user may apply or ignore it. Never invent scene-specific schema fields."
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
        return DirectorAdvicePayload.model_validate_json(text)
    except ValueError as exc:
        raise ValueError("Director Agent returned invalid advisory JSON") from exc


def build_director_advice_repair_input(
    *,
    original_input: str,
    invalid_output: str,
    validation_error: ValueError,
) -> tuple[str, str]:
    instructions = (
        "You are repairing a previously invalid JSON response from a film director agent. "
        "Return one complete corrected JSON object only and match the supplied schema exactly. "
        "Preserve valid recommendations and the original narrative intent. Do not introduce a "
        "new scene-specific field, mandatory gate, or non-dismissible instruction. When a "
        "recommended strategy is present, its proposed_repair must be executable, use the same "
        "strategy, and keep decision=APPLIED."
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
    target = f"; target={beat.target_anchor}" if beat.target_anchor else ""
    end_state = (
        f"; required end state={beat.required_end_state}"
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
        "; unchanged anchors=" + ", ".join(interaction.unchanged_anchors)
        if interaction.unchanged_anchors
        else ""
    )
    contact = ""
    if interaction.contact_anchor is not None:
        contact = f"; exact contact anchor={interaction.contact_anchor}"
        if interaction.contact_surface is not None:
            contact += f" on surface={interaction.contact_surface}"
        if interaction.contact_persists:
            contact += "; keep the same effector on that exact anchor and surface until release"
    attachments = (
        "; rigid attachments="
        + ", ".join(
            f"{item.anchor} stays on {item.surface}"
            for item in interaction.rigid_attachments
        )
        if interaction.rigid_attachments
        else ""
    )
    return (
        f"- {actor_name} uses {interaction.effector.value} to {interaction.action} "
        f"{target_name}:{interaction.target_anchor}; state "
        f"{interaction.start_state} -> {interaction.end_state}{contact}{attachments}{invariants}."
    )


def _format_topology_interaction(
    interaction: SpatialInteractionConstraint,
    assets: dict[str, Asset],
) -> str:
    target = assets.get(interaction.target_asset_id)
    target_name = target.name if target is not None else interaction.target_asset_id
    invariants = (
        "; visually separate fixed/invariant anchors="
        + ", ".join(interaction.unchanged_anchors)
        if interaction.unchanged_anchors
        else ""
    )
    contact = ""
    if interaction.contact_anchor is not None:
        contact = f"; actionable contact={interaction.contact_anchor}"
        if interaction.contact_surface is not None:
            contact += f" on {interaction.contact_surface}"
    attachments = (
        "; show attached subparts on their owning surfaces="
        + ", ".join(
            f"{item.anchor} on {item.surface}"
            for item in interaction.rigid_attachments
        )
        if interaction.rigid_attachments
        else ""
    )
    return (
        f"- Actionable target={target_name}:{interaction.target_anchor}; intended state "
        f"transition={interaction.start_state} -> {interaction.end_state}; make its handle, "
        f"hinge/pivot/track, boundary, and opening path physically readable{contact}"
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
            "move "
            + ", ".join(
                f"{item.anchor} on {item.surface}"
                for item in interaction.rigid_attachments
            )
            + f" as one rigid assembly with {interaction.target_anchor}"
        )
    if interaction.contact_persists:
        anchor = interaction.contact_anchor or interaction.target_anchor
        surface = (
            f" on {interaction.contact_surface}"
            if interaction.contact_surface is not None
            else ""
        )
        parts.append(
            f"keep {interaction.effector.value} continuously attached to {anchor}{surface} "
            "without crossing through the object or transferring to another side"
        )
    return "- " + "; ".join(parts) + "."


def _position_guidance(
    position: KeyframePosition,
    plan: ReferencePlanEntry,
    *,
    start_after_action_beat_order: int | None = None,
) -> str:
    if position == KeyframePosition.TOPOLOGY_REFERENCE:
        return (
            "Create a clean interaction-topology reference view before the action. "
            "The actionable target, its control/handle/hinge/track, and every invariant "
            "anchor must be visually distinct. Do not depict an ambiguous duplicate control. "
            "Do not perform the action yet."
        )
    if position == KeyframePosition.START:
        states = "; ".join(
            f"{item.target_anchor}="
            f"{_interaction_state_at_boundary(item, plan, start_after_action_beat_order)}"
            for item in plan.interactions
        )
        if start_after_action_beat_order is not None:
            return (
                "Show the exact video opening state after all action beats through order "
                f"{start_after_action_beat_order} have completed and immediately before the "
                "next declared beat. Do not replay completed beats. "
                + (f"Locked boundary states: {states}." if states else "")
            )
        return (
            "Show the exact opening state immediately before the first required action beat. "
            "The actor may be poised to act but has not completed the first state transition. "
            + (f"Locked starting states: {states}." if states else "")
        )
    ends = "; ".join(
        f"{item.target_anchor}={item.end_state}" for item in plan.interactions
    )
    return (
        "Show the exact final state after every required action beat has completed. "
        "Keep the same identities, wardrobe, location geometry, camera side, and object design. "
        + (f"Locked ending states: {ends}." if ends else "")
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
            f"Visible characters: {visible_names[0]} only. Other characters mentioned by "
            "broader continuity must remain off-screen.\n"
        )
    if len(visible_names) > 1:
        return (
            "Characters and eyeline: "
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
        return f"Prop/object state at this frame: {shot.continuity.prop_state_out}"
    if start_boundary is None:
        return (
            f"Prop/object state at this frame: {shot.continuity.prop_state_in}\n"
            f"Final state required after the video: {shot.continuity.prop_state_out}"
        )
    completed_states = [
        beat.required_end_state
        for beat in plan.action_beats
        if beat.order <= start_boundary and beat.required_end_state
    ]
    boundary_state = "; ".join(completed_states) or shot.continuity.prop_state_in
    return (
        f"State at this frame from completed beats: {boundary_state}\n"
        f"Final state required after the video: {shot.continuity.prop_state_out}"
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
