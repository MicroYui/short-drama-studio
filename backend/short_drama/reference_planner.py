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
    AssetVersion,
    DomainModel,
    ProjectAggregate,
    ProjectDocument,
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
    extra_constraints: list[str] = Field(default_factory=list[str], max_length=32)


class DirectorAdviceRequest(ReferencePlanPreviewRequest):
    user_direction: str | None = Field(default=None, max_length=2000)


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


class DirectorAdvicePayload(DomainModel):
    advisories: list[DirectorAdvisory] = Field(
        default_factory=list[DirectorAdvisory],
        max_length=12,
    )


class DirectorAdviceResponse(DirectorAdvicePayload):
    provider: str = "openai-compatible"
    model: str = "gpt-5.6-sol"
    shot_id: str
    proceed_allowed: Literal[True] = True


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

    complexity = _assess_complexity(shot, plan, selected)
    if complexity.missing_topology_anchors:
        warnings.append(
            "Create or approve LOCATION_INTERACTION views for: "
            + ", ".join(complexity.missing_topology_anchors)
        )
    return ReferencePlanPreviewResponse(
        shot_id=shot.id,
        selected=selected,
        missing_requirement_ids=missing,
        warnings=warnings,
        complexity=complexity,
    )


def compile_keyframe_brief(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    request: KeyframeBriefCompileRequest,
) -> KeyframeBriefCompileResponse:
    preview = preview_reference_plan(document, shot, plan, request)

    asset_index = {asset.id: asset for asset in document.assets}
    reference_lines = [
        (
            f"Image {index}: {asset_index[item.subject_asset_id].name}; "
            f"role={item.role.value}; angle={item.angle.value}; "
            f"framing={item.framing.value}; use only for {item.description}."
        )
        for index, item in enumerate(preview.selected, start=1)
    ]
    beat_lines = [
        _format_action_beat(item, asset_index)
        for item in sorted(plan.action_beats, key=lambda item: item.order)
    ]
    interaction_lines = [
        _format_interaction(item, asset_index)
        for item in plan.interactions
    ]
    style = _current_style(document)
    position_guidance = _position_guidance(request.position, plan)
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
    if beat_lines and not is_topology_reference:
        sections.append("ORDERED ACTION BEATS:\n" + "\n".join(beat_lines))
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
    if not is_topology_reference:
        sections.append(
            "CONTINUITY:\n"
            f"Characters and eyeline: {shot.continuity.character_positions}; {shot.continuity.eyeline}\n"
            f"Prop state in: {shot.continuity.prop_state_in}\n"
            f"Prop state out: {shot.continuity.prop_state_out}"
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
    return KeyframeBriefCompileResponse(
        **preview.model_dump(),
        position=request.position,
        prompt="\n\n".join(sections),
        input_version_ids=[item.asset_version_id for item in preview.selected],
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
        has_anchor = any(
            item.role == ReferenceRole.LOCATION_INTERACTION
            and interaction.target_anchor in item.tags
            for item in target_views
        )
        if not has_anchor:
            missing_topology.append(interaction.target_anchor)
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
    return ReferenceComplexityAssessment(
        duration_seconds=shot.duration_seconds,
        action_beat_count=len(plan.action_beats),
        interaction_count=len(plan.interactions),
        missing_topology_anchors=sorted(set(missing_topology)),
        observations=observations,
    )


def build_director_advice_input(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    preview: ReferencePlanPreviewResponse,
    request: DirectorAdviceRequest,
) -> tuple[str, str]:
    assets = {item.id: item for item in document.assets}
    instructions = (
        "You are a film director agent advising a user who is producing a live-action "
        "vertical short drama. Return JSON only, matching the supplied schema. Evaluate "
        "the actual shot context; do not apply a fixed action-count or duration threshold. "
        "You may recommend another reference, a simpler action, a different route, or a "
        "shot split, but every item is optional and dismissible. Never claim that generation "
        "is blocked or that the user must follow your advice. Use an empty advisories array "
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
            ]
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
        "user_direction": request.user_direction,
        "product_rule": (
            "Advice never blocks generation. The user may ignore every recommendation."
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
    return (
        f"- {actor_name} uses {interaction.effector.value} to {interaction.action} "
        f"{target_name}:{interaction.target_anchor}; state "
        f"{interaction.start_state} -> {interaction.end_state}{invariants}."
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
    return (
        f"- Actionable target={target_name}:{interaction.target_anchor}; intended state "
        f"transition={interaction.start_state} -> {interaction.end_state}; make its handle, "
        f"hinge/pivot/track, boundary, and opening path physically readable{invariants}."
    )


def _position_guidance(
    position: KeyframePosition,
    plan: ReferencePlanEntry,
) -> str:
    if position == KeyframePosition.TOPOLOGY_REFERENCE:
        return (
            "Create a clean interaction-topology reference view before the action. "
            "The actionable target, its control/handle/hinge/track, and every invariant "
            "anchor must be visually distinct. Do not depict an ambiguous duplicate control. "
            "Do not perform the action yet."
        )
    if position == KeyframePosition.START:
        starts = "; ".join(
            f"{item.target_anchor}={item.start_state}" for item in plan.interactions
        )
        return (
            "Show the exact opening state immediately before the first required action beat. "
            "The actor may be poised to act but has not completed the first state transition. "
            + (f"Locked starting states: {starts}." if starts else "")
        )
    ends = "; ".join(
        f"{item.target_anchor}={item.end_state}" for item in plan.interactions
    )
    return (
        "Show the exact final state after every required action beat has completed. "
        "Keep the same identities, wardrobe, location geometry, camera side, and object design. "
        + (f"Locked ending states: {ends}." if ends else "")
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
