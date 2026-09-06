"""Typed production-spec, storyboard, asset-plan, generation-plan, and output-slot contracts.

These models keep the durable content in versioned ``ProductionDocument``
artifacts.  ``ProjectDocument.shots`` and downstream provider tasks are
projections or executions of those artifacts, not competing content stores.
The helpers in this module are deterministic structural compilers; creative
choices remain the responsibility of the active Agent and its loaded Skills.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import cast

from pydantic import Field, JsonValue, model_validator

from short_drama.image_models import AssetReviewPurpose
from short_drama.models import (
    AssetKind,
    AssetLayoutProfile,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    CoreAssetBrief,
    DomainModel,
    DurationAlignment,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationFitAssessment,
    DurationMismatchResolution,
    DurationTargetOrigin,
    GenerationUnitShotWindow,
    ProductionDocument,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ProductionDocumentVersion,
    ProjectOutputSlot,
    ProjectOutputSlotRevision,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRole,
    ShotPlan,
)


PRODUCTION_SPEC_DOCUMENT_KIND = "production.spec"
STORYBOARD_DOCUMENT_KIND = "storyboard"
STORYBOARD_DRAFT_DOCUMENT_KIND = "storyboard.draft"
ASSET_PLAN_DOCUMENT_KIND = "asset.plan"
GENERATION_PLAN_DOCUMENT_KIND = "video.generation-plan"

PRODUCTION_SPEC_OUTPUT_SLOT_ID = "production.spec.current"
STORYBOARD_OUTPUT_SLOT_ID = "storyboard.current"
ASSET_PLAN_OUTPUT_SLOT_ID = "asset.plan.current"
GENERATION_PLAN_OUTPUT_SLOT_ID = "video.generation-plan.current"


def _duration_total(values: list[float]) -> Decimal:
    """Compare authored decimal seconds without binary-float tolerance drift."""

    return sum((Decimal(str(value)) for value in values), start=Decimal(0))


class ProductionVisualDirection(DomainModel):
    """Project-level visual intent without turning style into a fixed template."""

    medium_lock: str | None = Field(default=None, min_length=1, max_length=2000)
    style_brief: list[str] = Field(default_factory=list[str])
    color_palette: list[str] = Field(default_factory=list[str])
    lighting_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    forbidden_elements: list[str] = Field(default_factory=list[str])


class ProductionAudioDirection(DomainModel):
    """Project-level sound and text treatment expressed in natural language."""

    dialogue_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    ambience_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    bgm_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    subtitle_intent: str | None = Field(default=None, min_length=1, max_length=2000)


class ProductionProviderPreference(DomainModel):
    """A user or Agent preference, not an authorization to call the Provider."""

    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    resolution: str | None = Field(default=None, min_length=1, max_length=80)


class ProductionSpecContent(DomainModel):
    """Versioned production decisions shared by planning and execution Agents."""

    project_id: str = Field(min_length=1)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    production_scope: str = Field(min_length=1, max_length=1000)
    production_mode: str = Field(min_length=1, max_length=500)
    genre_and_setting: str = Field(min_length=1, max_length=2000)
    language: str = Field(min_length=1, max_length=80)
    aspect_ratio: str = Field(min_length=1, max_length=40)
    target_duration_seconds: float | None = Field(default=None, gt=0.0)
    duration_intent: str | None = Field(default=None, min_length=1, max_length=1000)
    duration_contract: DurationContract
    master_audio_binding_version_id: str | None = Field(
        default=None,
        min_length=1,
    )
    visual: ProductionVisualDirection = Field(
        default_factory=ProductionVisualDirection
    )
    audio: ProductionAudioDirection = Field(default_factory=ProductionAudioDirection)
    provider_preference: ProductionProviderPreference | None = None
    user_constraints: list[str] = Field(default_factory=list[str])
    agent_decisions: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_duration_contract(cls, value: object) -> object:
        """Keep legacy production specs readable without losing their wording."""

        if not isinstance(value, dict):
            return value
        payload = cast(dict[str, object], value)
        if payload.get("duration_contract") is not None:
            return payload
        duration_intent = payload.get("duration_intent")
        decision_basis = (
            duration_intent
            if isinstance(duration_intent, str) and duration_intent
            else "兼容旧 ProductionSpec：原记录未提供结构化时长决策。"
        )
        return {
            **payload,
            "duration_contract": {
                "target_seconds": payload.get("target_duration_seconds"),
                "target_origin": DurationTargetOrigin.LEGACY_UNKNOWN,
                "authority": DurationAuthority.SOURCE_LED,
                "acceptable_range_seconds": None,
                "source_fidelity_policy": (
                    DurationFidelityPolicy.PRESERVE_VERBATIM
                ),
                "mismatch_resolution": (
                    DurationMismatchResolution.REPORT_NATURAL_RUNTIME
                ),
                "decision_basis": decision_basis,
                "rhythm_shape": [],
                "user_overridable": True,
            },
        }

    @model_validator(mode="after")
    def validate_duration_projection(self) -> ProductionSpecContent:
        if (
            self.target_duration_seconds is not None
            and self.duration_contract.target_seconds is not None
            and Decimal(str(self.target_duration_seconds))
            != Decimal(str(self.duration_contract.target_seconds))
        ):
            raise ValueError(
                "ProductionSpec legacy and structured duration targets must match"
            )
        if (
            self.duration_contract.authority == DurationAuthority.AUDIO_LED
            and self.master_audio_binding_version_id is None
        ):
            raise ValueError(
                "Audio-led ProductionSpec requires an exact Master Audio binding version"
            )
        return self


class ClipGroup(DomainModel):
    """Ordered editorial shots rendered together as one generated media clip."""

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    shot_ids: list[str] = Field(min_length=1)
    duration_seconds: int = Field(ge=4, le=15)
    generation_intent: str = Field(min_length=1, max_length=4000)
    transition_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    continuity_in: list[str] = Field(default_factory=list[str])
    visible_changes: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])
    reference_notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_shot_ids(self) -> ClipGroup:
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("Clip group shot IDs must be unique")
        return self


class StoryboardSourceRange(DomainModel):
    """A semantic source span selected by the Leader, not a timed chunk.

    A range may cover part of one scene or cross scene boundaries.  Dialogue
    line IDs provide exact traceability where the script has spoken lines;
    natural-language anchors retain action-only beats without inventing a
    second screenplay representation.
    """

    id: str = Field(min_length=1, max_length=200)
    scene_ids: list[str] = Field(min_length=1)
    script_line_ids: list[str] = Field(default_factory=list[str])
    description: str = Field(min_length=1, max_length=2000)
    starts_after: str | None = Field(default=None, min_length=1, max_length=1000)
    ends_before: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_source_identity(self) -> StoryboardSourceRange:
        if len(self.scene_ids) != len(set(self.scene_ids)):
            raise ValueError("Storyboard source-range scene IDs must be unique")
        if len(self.script_line_ids) != len(set(self.script_line_ids)):
            raise ValueError(
                "Storyboard source-range script line IDs must be unique"
            )
        return self


class StoryboardWorkUnitContract(DomainModel):
    """One dynamically planned authoring scope carried by an Agent WorkOrder.

    This contract deliberately does not equate a WorkOrder with a script scene,
    an editorial Shot, or a provider ClipGroup.  One unit may produce multiple
    groups, and a later Leader revision may split or combine semantic ranges.
    """

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    source_range: StoryboardSourceRange
    objective: str = Field(min_length=1, max_length=3000)
    continuity_context: list[str] = Field(default_factory=list[str])
    constraints: list[str] = Field(default_factory=list[str])
    completion_criteria: list[str] = Field(min_length=1)
    ordering_guidance: str | None = Field(
        default=None,
        min_length=1,
        max_length=1500,
    )


class StoryboardSceneNode(DomainModel):
    """Stable scene index inside the single canonical Storyboard document.

    The name describes a semantic aggregation node in the content graph.  It
    is not a separate MiniMax canvas document or a ComfyUI execution node.
    """

    id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=2000)
    source_target_duration_seconds: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "The current Script scene's upstream pacing reference. It informs "
            "semantic redistribution across scenes but is not a hard scene cap; "
            "the episode DurationContract remains authoritative."
        ),
    )
    source_line_ids: list[str] = Field(default_factory=list[str])
    clip_group_ids: list[str] = Field(default_factory=list[str])
    shot_ids: list[str] = Field(default_factory=list[str])
    continuity_in: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_source_lines(self) -> StoryboardSceneNode:
        if len(self.source_line_ids) != len(set(self.source_line_ids)):
            raise ValueError("Storyboard scene-node source line IDs must be unique")
        if len(self.clip_group_ids) != len(set(self.clip_group_ids)):
            raise ValueError("Storyboard scene-node ClipGroup IDs must be unique")
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("Storyboard scene-node Shot IDs must be unique")
        return self


class StoryboardClipGroupDraft(DomainModel):
    """One bounded, incrementally persisted ClipGroup with its editorial Shots."""

    work_unit_id: str = Field(min_length=1, max_length=200)
    scene_node_id: str = Field(min_length=1, max_length=200)
    scene_group_sequence: int = Field(ge=1)
    source_range: StoryboardSourceRange
    shots: list[ShotPlan] = Field(min_length=1)
    clip_group: ClipGroup
    handoff_notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_group_payload(self) -> StoryboardClipGroupDraft:
        shot_ids = [item.id for item in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("Storyboard draft shot IDs must be unique within a group")
        if self.clip_group.shot_ids != shot_ids:
            raise ValueError(
                "Storyboard draft ClipGroup must cover its editorial Shots in order"
            )
        project_ids = {item.project_id for item in self.shots}
        if len(project_ids) != 1:
            raise ValueError("Storyboard draft Shots must belong to one project")
        scene_ids = {item.scene_id for item in self.shots}
        if scene_ids != {self.source_range.scene_ids[0]} or len(
            self.source_range.scene_ids
        ) != 1:
            raise ValueError(
                "One draft ClipGroup must stay inside one script scene; "
                "the WorkOrder may still span multiple scenes"
            )
        shot_line_ids = [
            line_id for shot in self.shots for line_id in shot.dialogue_line_ids
        ]
        if shot_line_ids != self.source_range.script_line_ids:
            raise ValueError(
                "Storyboard draft Shots must preserve the group's source lines in order"
            )
        duration = _duration_total(
            [item.duration_seconds for item in self.shots]
        )
        if duration != Decimal(self.clip_group.duration_seconds):
            raise ValueError(
                "Storyboard draft ClipGroup duration must equal its Shot durations"
            )
        return self


class StoryboardTimingLedgerEntry(DomainModel):
    """One dialogue or visible-action row in the private timing ledger."""

    id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    scene_node_id: str = Field(min_length=1, max_length=200)
    source_range: StoryboardSourceRange
    source_text: str = Field(min_length=1, max_length=5000)
    dialogue_units: int = Field(ge=0)
    speech_rate_units_per_second: float | None = Field(
        default=None,
        gt=0.0,
    )
    minimum_spoken_duration_seconds: float = Field(ge=0.0)
    non_speech_reserve_seconds: float = Field(
        ge=0.0,
        description=(
            "This row's incremental non-speech time after dialogue, action, reaction, "
            "and camera beats that can play concurrently have been collapsed. It is "
            "not a per-row copy of the ClipGroup's shared performance reserve."
        ),
    )
    allocated_duration_seconds: float = Field(gt=0.0)
    natural_cut: str = Field(min_length=1, max_length=1000)
    continuity_in: list[str] = Field(default_factory=list[str])
    visible_changes: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])
    completes_scene: bool = False
    planned_clip_group_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    planned_shot_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_timing_math(self) -> StoryboardTimingLedgerEntry:
        if self.dialogue_units == 0:
            if self.speech_rate_units_per_second is not None:
                raise ValueError(
                    "An action-only timing entry cannot declare a speech rate"
                )
            if self.minimum_spoken_duration_seconds > 0.001:
                raise ValueError(
                    "An action-only timing entry must have zero spoken duration"
                )
        else:
            if self.speech_rate_units_per_second is None:
                raise ValueError(
                    "A dialogue timing entry requires a speech rate"
                )
            computed_spoken = (
                Decimal(self.dialogue_units)
                / Decimal(str(self.speech_rate_units_per_second))
            )
            if (
                Decimal(str(self.minimum_spoken_duration_seconds))
                < computed_spoken
            ):
                raise ValueError(
                    "Timing-entry spoken duration cannot be shorter than dialogue "
                    "units divided by speech rate"
                )
        if (
            self.minimum_spoken_duration_seconds
            + self.non_speech_reserve_seconds
            > self.allocated_duration_seconds + 0.05
        ):
            raise ValueError(
                "Timing-entry allocation cannot be shorter than spoken duration "
                "plus non-speech reserve"
            )
        return self


class StoryboardClipGroupOutline(DomainModel):
    """One immutable row in the private whole-episode ClipGroup ledger.

    The Agent derives this outline from the dialogue/action timing scratch pass
    before any detailed Shot is authored. It is a factual authoring plan, not a
    target-duration allocation: each later commit must land on the exact row.
    """

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    scene_node_id: str = Field(min_length=1, max_length=200)
    scene_group_sequence: int = Field(ge=1)
    source_range: StoryboardSourceRange
    timing_entry_ids: list[str] = Field(min_length=1)
    duration_seconds: int = Field(ge=4, le=15)
    objective: str = Field(min_length=1, max_length=3000)
    continuity_in: list[str] = Field(default_factory=list[str])
    visible_changes: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_timing_entry_ids(self) -> StoryboardClipGroupOutline:
        if len(self.timing_entry_ids) != len(set(self.timing_entry_ids)):
            raise ValueError("Storyboard outline timing-entry IDs must be unique")
        return self


class StoryboardTimingAudit(DomainModel):
    """Persisted summary of MiniMax Design's private timing scratch pass.

    Neither a source declaration nor a target may be divided into synthetic
    ClipGroup budgets. ``computed_episode_duration_s`` is derived from the
    natural ledger and is then compared with the pinned DurationContract.
    """

    source_declared_duration_s: float | None = Field(default=None, gt=0.0)
    target_duration_s: float | None = Field(default=None, gt=0.0)
    dialogue_units_total: int = Field(ge=0)
    minimum_spoken_duration_s: float = Field(ge=0.0)
    non_speech_reserve_s: float = Field(ge=0.0)
    computed_episode_duration_s: float = Field(gt=0.0)
    clip_group_count: int = Field(ge=1)
    alignment: DurationAlignment = DurationAlignment.NOT_APPLICABLE
    difference_seconds: float | None = None
    assessment: DurationFitAssessment | None = None
    overloaded_shot_ids: list[str] = Field(default_factory=list[str])
    overloaded_group_ids: list[str] = Field(default_factory=list[str])
    timing_audit_passed: bool
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_audit_claim(self) -> StoryboardTimingAudit:
        if len(self.overloaded_shot_ids) != len(set(self.overloaded_shot_ids)):
            raise ValueError("Timing-audit overloaded Shot IDs must be unique")
        if len(self.overloaded_group_ids) != len(set(self.overloaded_group_ids)):
            raise ValueError("Timing-audit overloaded ClipGroup IDs must be unique")
        if self.timing_audit_passed and (
            self.overloaded_shot_ids or self.overloaded_group_ids
        ):
            raise ValueError(
                "A passed timing audit cannot retain overloaded Shot or ClipGroup IDs"
            )
        if (
            self.minimum_spoken_duration_s + self.non_speech_reserve_s
            > self.computed_episode_duration_s + 0.05
        ):
            raise ValueError(
                "Computed episode duration cannot be shorter than spoken duration "
                "plus non-speech reserve"
            )
        if (
            self.assessment is not None
            and self.assessment.alignment != self.alignment
        ):
            raise ValueError(
                "Timing-audit assessment alignment must match the audit alignment"
            )
        return self


class StoryboardDraftVerificationStatus(StrEnum):
    READY = "READY"
    NEEDS_REVISION = "NEEDS_REVISION"


class StoryboardRepairScope(StrEnum):
    CLIP_GROUP = "CLIP_GROUP"
    STORYBOARD_PLAN = "STORYBOARD_PLAN"
    SCRIPT = "SCRIPT"
    PRODUCTION_SPEC = "PRODUCTION_SPEC"


class StoryboardDraftVerificationFinding(DomainModel):
    id: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=3000)
    repair_scope: StoryboardRepairScope = StoryboardRepairScope.CLIP_GROUP
    affected_clip_group_ids: list[str] = Field(default_factory=list[str])
    affected_scene_sequences: list[int] = Field(default_factory=list[int])
    suggested_revision: str | None = Field(default=None, min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_affected_groups(self) -> StoryboardDraftVerificationFinding:
        if len(self.affected_clip_group_ids) != len(
            set(self.affected_clip_group_ids)
        ):
            raise ValueError("Verification finding ClipGroup IDs must be unique")
        if any(sequence < 1 for sequence in self.affected_scene_sequences):
            raise ValueError("Verification finding scene sequences must be positive")
        if len(self.affected_scene_sequences) != len(
            set(self.affected_scene_sequences)
        ):
            raise ValueError("Verification finding scene sequences must be unique")
        return self


class StoryboardDraftVerificationReport(DomainModel):
    """Semantic Verifier output attached to one exact draft content version.

    This is Agent state, not a user approval or publishing gate. Any subsequent
    group append/replacement clears it so stale conclusions cannot look current.
    """

    status: StoryboardDraftVerificationStatus
    summary: str = Field(min_length=1, max_length=5000)
    checked_clip_group_ids: list[str] = Field(min_length=1)
    findings: list[StoryboardDraftVerificationFinding] = Field(
        default_factory=list[StoryboardDraftVerificationFinding]
    )
    continuity_assessment: list[str] = Field(default_factory=list[str])
    timing_assessment: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_report(self) -> StoryboardDraftVerificationReport:
        if len(self.checked_clip_group_ids) != len(
            set(self.checked_clip_group_ids)
        ):
            raise ValueError("Verified ClipGroup IDs must be unique")
        if self.status == StoryboardDraftVerificationStatus.READY and self.findings:
            raise ValueError("A READY verification report cannot retain findings")
        if (
            self.status == StoryboardDraftVerificationStatus.NEEDS_REVISION
            and not self.findings
        ):
            raise ValueError(
                "A NEEDS_REVISION verification report must explain at least one finding"
            )
        return self


class StoryboardDraftPhase(StrEnum):
    """Private authoring phase of one recoverable storyboard draft."""

    LEDGER_BUILDING = "LEDGER_BUILDING"
    OUTLINE_BUILDING = "OUTLINE_BUILDING"
    DURATION_RECONCILIATION = "DURATION_RECONCILIATION"
    GROUP_AUTHORING = "GROUP_AUTHORING"


class StoryboardDraftSceneSeed(DomainModel):
    """Agent-selected scene identity and continuity, before source hydration."""

    id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    continuity_in: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])


class StoryboardDraftSeed(DomainModel):
    """Small source lock used to open a draft without emitting its whole plan."""

    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    base_storyboard_version_id: str | None = Field(default=None, min_length=1)
    scene_nodes: list[StoryboardDraftSceneSeed] = Field(min_length=1)
    timing_notes: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_scene_identity(self) -> StoryboardDraftSeed:
        node_ids = [item.id for item in self.scene_nodes]
        scene_ids = [item.scene_id for item in self.scene_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Storyboard draft seed scene-node IDs must be unique")
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Storyboard draft seed script scene IDs must be unique")
        if [item.sequence for item in self.scene_nodes] != list(
            range(1, len(self.scene_nodes) + 1)
        ):
            raise ValueError(
                "Storyboard draft seed scene nodes must use contiguous sequence order"
            )
        return self


class StoryboardDraftContent(DomainModel):
    """Recoverable private plan assembled into one canonical Storyboard.

    Planning is persisted in semantic segments so a long episode never needs to
    fit in one model response. Segment boundaries are checkpoints only: the
    sealed outline still defines the provider-bounded ClipGroups.
    """

    phase: StoryboardDraftPhase
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    base_storyboard_version_id: str | None = Field(default=None, min_length=1)
    script_line_ids: list[str] = Field(default_factory=list[str])
    scene_nodes: list[StoryboardSceneNode] = Field(min_length=1)
    timing_ledger: list[StoryboardTimingLedgerEntry] = Field(
        default_factory=list[StoryboardTimingLedgerEntry]
    )
    planned_clip_groups: list[StoryboardClipGroupOutline] = Field(
        default_factory=list[StoryboardClipGroupOutline]
    )
    timing_audit: StoryboardTimingAudit | None = None
    committed_groups: list[StoryboardClipGroupDraft] = Field(
        default_factory=list[StoryboardClipGroupDraft]
    )
    verification: StoryboardDraftVerificationReport | None = None
    timing_notes: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_phase(cls, value: object) -> object:
        """Read drafts persisted before staged planning without a DB migration."""

        if not isinstance(value, dict):
            return value
        payload = cast(dict[str, object], value)
        inferred: object = payload.get("phase")
        if inferred is None:
            inferred = StoryboardDraftPhase.LEDGER_BUILDING
            if payload.get("planned_clip_groups"):
                inferred = StoryboardDraftPhase.OUTLINE_BUILDING
            if (
                payload.get("timing_audit") is not None
                or payload.get("committed_groups")
                or payload.get("verification") is not None
            ):
                inferred = StoryboardDraftPhase.GROUP_AUTHORING
        # Older drafts used dialogue coverage as the implicit scene cursor. Keep
        # them readable by marking the final persisted entry of each represented
        # scene as complete, whether or not they already persisted a staged phase.
        # New request models materialize the explicit False default, so they must
        # deliberately set ``completes_scene`` on the actual final beat.
        raw_ledger = payload.get("timing_ledger")
        upgraded_ledger: list[object] | None = None
        if isinstance(raw_ledger, list):
            raw_items = cast(list[object], raw_ledger)
            last_index_by_node: dict[str, int] = {}
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    item_payload = cast(dict[str, object], item)
                    scene_node_id = item_payload.get("scene_node_id")
                    if isinstance(scene_node_id, str):
                        last_index_by_node[scene_node_id] = index
            upgraded_items: list[object] = []
            for index, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    upgraded_items.append(item)
                    continue
                item_payload = cast(dict[str, object], item)
                if "completes_scene" in item_payload:
                    upgraded_items.append(item_payload)
                    continue
                scene_node_id = item_payload.get("scene_node_id")
                upgraded_items.append(
                    {
                        **item_payload,
                        "completes_scene": (
                            isinstance(scene_node_id, str)
                            and last_index_by_node.get(scene_node_id) == index
                        ),
                    }
                )
            upgraded_ledger = upgraded_items
        upgraded_payload = {**payload, "phase": inferred}
        if upgraded_ledger is not None:
            upgraded_payload["timing_ledger"] = upgraded_ledger
        return upgraded_payload

    @model_validator(mode="after")
    def validate_draft_structure(self) -> StoryboardDraftContent:
        if len(self.script_line_ids) != len(set(self.script_line_ids)):
            raise ValueError("Storyboard draft source script line IDs must be unique")

        node_ids = [item.id for item in self.scene_nodes]
        scene_ids = [item.scene_id for item in self.scene_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Storyboard draft scene-node IDs must be unique")
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Storyboard draft script scene IDs must be unique")
        if [item.sequence for item in self.scene_nodes] != list(
            range(1, len(self.scene_nodes) + 1)
        ):
            raise ValueError(
                "Storyboard draft scene nodes must use contiguous sequence order"
            )
        if any(item.clip_group_ids or item.shot_ids for item in self.scene_nodes):
            raise ValueError(
                "Storyboard draft scene nodes are seeds; committed mapping is derived"
            )
        node_line_ids = [
            line_id for node in self.scene_nodes for line_id in node.source_line_ids
        ]
        if node_line_ids != self.script_line_ids:
            raise ValueError(
                "Storyboard scene nodes must cover source script lines exactly in order"
            )
        source_metadata_deferred = not self.script_line_ids and all(
            not node.source_line_ids for node in self.scene_nodes
        )
        nodes_by_id = {item.id: item for item in self.scene_nodes}
        node_order = {item.id: item.sequence for item in self.scene_nodes}

        ledger_ids = [item.id for item in self.timing_ledger]
        if len(ledger_ids) != len(set(ledger_ids)):
            raise ValueError("Storyboard timing-ledger entry IDs must be unique")
        if [item.sequence for item in self.timing_ledger] != list(
            range(1, len(self.timing_ledger) + 1)
        ):
            raise ValueError(
                "Storyboard timing-ledger entries must use contiguous sequence order"
            )
        ledger_scene_order: list[int] = []
        for entry in self.timing_ledger:
            node = nodes_by_id.get(entry.scene_node_id)
            if node is None:
                raise ValueError(
                    "Storyboard timing entry references unknown scene node "
                    f"{entry.scene_node_id}"
                )
            ledger_scene_order.append(node.sequence)
            if entry.source_range.scene_ids != [node.scene_id]:
                raise ValueError(
                    "Storyboard timing-entry source scene must match its scene node"
                )
            if not source_metadata_deferred and any(
                line_id not in node.source_line_ids
                for line_id in entry.source_range.script_line_ids
            ):
                raise ValueError(
                    "Storyboard timing entry contains a line outside its scene node"
                )
        if ledger_scene_order != sorted(ledger_scene_order):
            raise ValueError("Storyboard timing ledger must preserve scene order")
        entries_by_node = {
            node.id: [
                entry
                for entry in self.timing_ledger
                if entry.scene_node_id == node.id
            ]
            for node in self.scene_nodes
        }
        represented_node_ids = list(
            dict.fromkeys(item.scene_node_id for item in self.timing_ledger)
        )
        expected_represented_node_ids = node_ids[: len(represented_node_ids)]
        if represented_node_ids != expected_represented_node_ids:
            raise ValueError(
                "Storyboard timing ledger must advance through scene nodes in order"
            )
        completed_scene_node_ids: list[str] = []
        for node in self.scene_nodes:
            node_entries = entries_by_node[node.id]
            if not node_entries:
                continue
            node_ledger_line_ids = [
                line_id
                for entry in node_entries
                for line_id in entry.source_range.script_line_ids
            ]
            if node_ledger_line_ids != node.source_line_ids[: len(node_ledger_line_ids)]:
                raise ValueError(
                    "Storyboard timing entries must cover each scene's source-line "
                    "prefix in order"
                )
            completion_positions = [
                index
                for index, entry in enumerate(node_entries)
                if entry.completes_scene
            ]
            if len(completion_positions) > 1 or (
                completion_positions
                and completion_positions[0] != len(node_entries) - 1
            ):
                raise ValueError(
                    "Only the final timing entry in a scene may complete that scene"
                )
            if completion_positions:
                if node_ledger_line_ids != node.source_line_ids:
                    raise ValueError(
                        "A timing entry cannot complete its scene before all source "
                        "dialogue lines are covered"
                    )
                completed_scene_node_ids.append(node.id)
        for previous_node_id in represented_node_ids[:-1]:
            if previous_node_id not in completed_scene_node_ids:
                raise ValueError(
                    "A storyboard timing ledger cannot advance to the next scene "
                    "before the previous scene is explicitly complete"
                )
        ledger_line_ids = [
            line_id
            for entry in self.timing_ledger
            for line_id in entry.source_range.script_line_ids
        ]
        if len(ledger_line_ids) != len(set(ledger_line_ids)):
            raise ValueError("Storyboard timing ledger cannot repeat script lines")
        if (
            not source_metadata_deferred
            and ledger_line_ids != self.script_line_ids[: len(ledger_line_ids)]
        ):
            raise ValueError(
                "Storyboard timing ledger must cover a contiguous source prefix"
            )
        full_ledger = (
            (source_metadata_deferred or ledger_line_ids == self.script_line_ids)
            and completed_scene_node_ids == node_ids
        )

        if self.phase == StoryboardDraftPhase.LEDGER_BUILDING:
            if (
                self.planned_clip_groups
                or self.timing_audit is not None
                or self.committed_groups
                or self.verification is not None
            ):
                raise ValueError(
                    "Ledger-building drafts cannot contain outline, audit, groups, "
                    "or verification"
                )
            return self
        if not full_ledger:
            raise ValueError(
                "Storyboard timing ledger must cover all source lines and explicitly "
                "complete every scene before outlining"
            )
        empty_ledger_scene_ids = [
            node.id for node in self.scene_nodes if not entries_by_node[node.id]
        ]
        if empty_ledger_scene_ids:
            raise ValueError(
                "Every storyboard scene needs at least one timing-ledger entry "
                "before outlining: "
                + ", ".join(empty_ledger_scene_ids)
            )

        ledger_by_id = {item.id: item for item in self.timing_ledger}
        planned_ids = [item.id for item in self.planned_clip_groups]
        if len(planned_ids) != len(set(planned_ids)):
            raise ValueError("Storyboard planned ClipGroup IDs must be unique")
        plans_by_id = {item.id: item for item in self.planned_clip_groups}
        planned_positions = [
            (item.scene_node_id, item.scene_group_sequence)
            for item in self.planned_clip_groups
        ]
        if len(planned_positions) != len(set(planned_positions)):
            raise ValueError(
                "Storyboard planned ClipGroups cannot share one in-scene sequence"
            )
        ordered_plans = sorted(
            self.planned_clip_groups,
            key=lambda item: (
                node_order.get(item.scene_node_id, 10**9),
                item.scene_group_sequence,
            ),
        )
        if self.planned_clip_groups != ordered_plans:
            raise ValueError("Storyboard planned ClipGroups must preserve scene order")
        plans_by_node: dict[str, list[StoryboardClipGroupOutline]] = {
            item.id: [] for item in self.scene_nodes
        }
        for planned in self.planned_clip_groups:
            node = nodes_by_id.get(planned.scene_node_id)
            if node is None:
                raise ValueError(
                    "Storyboard planned ClipGroup references unknown scene node "
                    f"{planned.scene_node_id}"
                )
            if planned.source_range.scene_ids != [node.scene_id]:
                raise ValueError(
                    "Storyboard planned ClipGroup source scene must match its scene node"
                )
            unknown_timing_entry_ids = [
                entry_id
                for entry_id in planned.timing_entry_ids
                if entry_id not in ledger_by_id
            ]
            if unknown_timing_entry_ids:
                raise ValueError(
                    "Storyboard planned ClipGroup references unknown timing entries: "
                    + ", ".join(unknown_timing_entry_ids)
                )
            timing_entries = [
                ledger_by_id[entry_id] for entry_id in planned.timing_entry_ids
            ]
            if any(
                entry.scene_node_id != planned.scene_node_id
                or (
                    entry.planned_clip_group_id is not None
                    and entry.planned_clip_group_id != planned.id
                )
                for entry in timing_entries
            ):
                raise ValueError(
                    "Storyboard timing entries must match their outlined scene and "
                    "optional ClipGroup hint"
                )
            timing_line_ids = [
                line_id
                for entry in timing_entries
                for line_id in entry.source_range.script_line_ids
            ]
            if timing_line_ids != planned.source_range.script_line_ids:
                raise ValueError(
                    "Storyboard planned ClipGroup source lines must equal its timing "
                    "entries in order"
                )
            if _duration_total(
                [item.allocated_duration_seconds for item in timing_entries]
            ) != Decimal(planned.duration_seconds):
                raise ValueError(
                    "Storyboard planned ClipGroup duration must equal timing allocations"
                )
            plans_by_node[node.id].append(planned)

        for node in self.scene_nodes:
            node_plans = plans_by_node[node.id]
            if [item.scene_group_sequence for item in node_plans] != list(
                range(1, len(node_plans) + 1)
            ):
                raise ValueError(
                    f"Storyboard scene node {node.id} planned ClipGroups must be "
                    "contiguous"
                )
        planned_timing_entry_ids = [
            entry_id
            for item in self.planned_clip_groups
            for entry_id in item.timing_entry_ids
        ]
        if planned_timing_entry_ids != ledger_ids[: len(planned_timing_entry_ids)]:
            raise ValueError(
                "Storyboard outline must consume a contiguous timing-ledger prefix"
            )
        full_outline = planned_timing_entry_ids == ledger_ids

        if self.phase == StoryboardDraftPhase.OUTLINE_BUILDING:
            if (
                self.timing_audit is not None
                or self.committed_groups
                or self.verification is not None
            ):
                raise ValueError(
                    "Outline-building drafts cannot contain audit, authored groups, "
                    "or verification"
                )
            return self
        if not full_outline:
            raise ValueError(
                "Storyboard outline must consume the full timing ledger before "
                "group authoring"
            )
        empty_scene_ids = [
            node.id for node in self.scene_nodes if not plans_by_node[node.id]
        ]
        if empty_scene_ids:
            raise ValueError(
                "Every storyboard scene needs an outlined ClipGroup before authoring: "
                + ", ".join(empty_scene_ids)
            )
        if self.timing_audit is None:
            raise ValueError("Group-authoring drafts require a sealed timing audit")
        planned_duration = sum(item.duration_seconds for item in ordered_plans)
        if abs(
            planned_duration - self.timing_audit.computed_episode_duration_s
        ) > 0.05:
            raise ValueError(
                "Timing-audit episode duration must equal planned ClipGroup durations"
            )
        if self.timing_audit.clip_group_count != len(ordered_plans):
            raise ValueError("Timing-audit ClipGroup count must equal the outline")
        if self.timing_audit.dialogue_units_total != sum(
            item.dialogue_units for item in self.timing_ledger
        ):
            raise ValueError(
                "Timing-audit dialogue-unit total must equal the timing ledger"
            )
        if abs(
            self.timing_audit.minimum_spoken_duration_s
            - sum(
                item.minimum_spoken_duration_seconds
                for item in self.timing_ledger
            )
        ) > 0.05:
            raise ValueError(
                "Timing-audit minimum spoken duration must equal the timing ledger"
            )
        if abs(
            self.timing_audit.non_speech_reserve_s
            - sum(item.non_speech_reserve_seconds for item in self.timing_ledger)
        ) > 0.05:
            raise ValueError(
                "Timing-audit non-speech reserve must equal the timing ledger"
            )

        if (
            not self.timing_audit.timing_audit_passed
            or self.timing_audit.overloaded_group_ids
            or self.timing_audit.overloaded_shot_ids
        ):
            raise ValueError(
                "A sealed storyboard outline requires a clean timing audit without "
                "overloads"
            )
        if self.timing_audit.alignment in {
            DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
            DurationAlignment.UNRESOLVED,
        }:
            if self.phase != StoryboardDraftPhase.DURATION_RECONCILIATION:
                raise ValueError(
                    "An outline with an unresolved or out-of-range duration decision "
                    "must return to duration reconciliation before group authoring"
                )
            if self.committed_groups or self.verification is not None:
                raise ValueError(
                    "Duration-reconciliation drafts cannot contain authored groups "
                    "or verification"
                )
            return self
        if self.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
            raise ValueError(
                "Duration reconciliation requires an unresolved or out-of-range audit"
            )
        group_ids: list[str] = []
        shot_ids: list[str] = []
        for commit in self.committed_groups:
            planned = plans_by_id.get(commit.clip_group.id)
            if planned is None:
                raise ValueError(
                    "Storyboard draft commit has no matching planned ClipGroup "
                    f"{commit.clip_group.id}"
                )
            if (
                commit.scene_node_id != planned.scene_node_id
                or commit.scene_group_sequence != planned.scene_group_sequence
                or commit.source_range != planned.source_range
                or commit.clip_group.duration_seconds != planned.duration_seconds
            ):
                raise ValueError(
                    "Storyboard draft commit must match its planned ClipGroup slot"
                )
            group_ids.append(commit.clip_group.id)
            shot_ids.extend(item.id for item in commit.shots)
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("Storyboard draft ClipGroup IDs must be unique")
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("Storyboard draft Shot IDs must be globally unique")
        if group_ids != planned_ids[: len(group_ids)]:
            raise ValueError("Storyboard draft groups must be committed in planned order")
        if self.verification is not None:
            unknown_checked_ids = [
                item
                for item in self.verification.checked_clip_group_ids
                if item not in group_ids
            ]
            if unknown_checked_ids:
                raise ValueError(
                    "Storyboard verification references uncommitted ClipGroups: "
                    + ", ".join(unknown_checked_ids)
                )
        return self


class StoryboardDraftSceneProgress(DomainModel):
    scene_node_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    planned_clip_group_count: int = Field(ge=0)
    committed_clip_group_count: int = Field(ge=0)
    planned_duration_seconds: float = Field(ge=0.0)
    committed_duration_seconds: float = Field(ge=0.0)
    remaining_duration_seconds: float = Field(ge=0.0)
    planned_script_line_count: int = Field(ge=0)
    covered_script_line_count: int = Field(ge=0)
    remaining_script_line_count: int = Field(ge=0)


class StoryboardDraftProgress(DomainModel):
    """Compact Tool result suitable for traces and long-running Agent context."""

    draft_ref: ProductionDocumentArtifactRef
    draft_version_id: str = Field(min_length=1)
    phase: StoryboardDraftPhase
    scene_node_count: int = Field(ge=1)
    timing_ledger_entry_count: int = Field(ge=0)
    ledger_covered_script_line_count: int = Field(ge=0)
    outlined_timing_entry_count: int = Field(ge=0)
    outlined_script_line_count: int = Field(ge=0)
    planned_clip_group_count: int = Field(ge=0)
    committed_clip_group_count: int = Field(ge=0)
    committed_shot_count: int = Field(ge=0)
    total_planned_duration_seconds: float | None = Field(default=None, gt=0.0)
    committed_duration_seconds: float = Field(ge=0.0)
    remaining_duration_seconds: float | None = Field(default=None, ge=0.0)
    covered_script_line_count: int = Field(ge=0)
    remaining_script_line_count: int = Field(ge=0)
    scene_progress: list[StoryboardDraftSceneProgress] = Field(min_length=1)
    next_unplanned_scene_node_id: str | None = Field(default=None, min_length=1)
    next_unplanned_script_line_id: str | None = Field(default=None, min_length=1)
    latest_planned_continuity_out: list[str] = Field(default_factory=list[str])
    latest_scene_node_id: str | None = Field(default=None, min_length=1)
    latest_clip_group_id: str | None = Field(default=None, min_length=1)
    latest_shot_ids: list[str] = Field(default_factory=list[str])
    latest_continuity_out: list[str] = Field(default_factory=list[str])
    verification_status: StoryboardDraftVerificationStatus | None = None

    @model_validator(mode="after")
    def validate_draft_ref(self) -> StoryboardDraftProgress:
        if self.draft_ref.artifact_type != "storyboard_draft":
            raise ValueError("Storyboard draft progress must reference a draft artifact")
        if self.draft_ref.version is None:
            raise ValueError("Storyboard draft progress requires a persisted version")
        return self


class StoryboardDraftBeginRequest(DomainModel):
    seed: StoryboardDraftSeed
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftLedgerSegmentAppendRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    entries: list[StoryboardTimingLedgerEntry] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list[str])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftLedgerCompleteRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list[str])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftOutlineSegmentAppendRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    groups: list[StoryboardClipGroupOutline] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list[str])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftOutlineCompleteRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    timing_audit: StoryboardTimingAudit
    notes: list[str] = Field(default_factory=list[str])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftAppendRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    work_unit: StoryboardWorkUnitContract
    group: StoryboardClipGroupDraft
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin

    @model_validator(mode="after")
    def validate_work_unit_handoff(self) -> StoryboardDraftAppendRequest:
        if self.group.work_unit_id != self.work_unit.id:
            raise ValueError(
                "Storyboard draft group must identify the WorkOrder semantic unit"
            )
        work_scenes = set(self.work_unit.source_range.scene_ids)
        group_scenes = set(self.group.source_range.scene_ids)
        if not group_scenes <= work_scenes:
            raise ValueError(
                "Storyboard draft group must stay inside its WorkOrder source scenes"
            )
        work_lines = set(self.work_unit.source_range.script_line_ids)
        if any(
            line_id not in work_lines
            for line_id in self.group.source_range.script_line_ids
        ):
            raise ValueError(
                "Storyboard draft group must stay inside its WorkOrder source lines"
            )
        return self


class StoryboardDraftGroupsAppendRequest(DomainModel):
    """Append one coherent adjacent prefix while retaining per-group versions."""

    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    work_unit: StoryboardWorkUnitContract
    groups: list[StoryboardClipGroupDraft] = Field(min_length=1)
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin

    @model_validator(mode="after")
    def validate_work_unit_handoff(self) -> StoryboardDraftGroupsAppendRequest:
        group_ids = [item.clip_group.id for item in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError(
                "Storyboard draft group segment cannot repeat a ClipGroup ID"
            )
        work_scenes = set(self.work_unit.source_range.scene_ids)
        work_lines = set(self.work_unit.source_range.script_line_ids)
        for group in self.groups:
            if group.work_unit_id != self.work_unit.id:
                raise ValueError(
                    "Every storyboard draft group must identify the WorkOrder "
                    "semantic unit"
                )
            if not set(group.source_range.scene_ids) <= work_scenes:
                raise ValueError(
                    "Every storyboard draft group must stay inside its WorkOrder "
                    "source scenes"
                )
            if any(
                line_id not in work_lines
                for line_id in group.source_range.script_line_ids
            ):
                raise ValueError(
                    "Every storyboard draft group must stay inside its WorkOrder "
                    "source lines"
                )
        return self


class StoryboardDraftGroupReplaceRequest(StoryboardDraftAppendRequest):
    replaced_clip_group_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_replacement_identity(self) -> StoryboardDraftGroupReplaceRequest:
        if self.group.clip_group.id != self.replaced_clip_group_id:
            raise ValueError(
                "A storyboard draft replacement must retain the planned ClipGroup ID"
            )
        return self


class StoryboardDraftVerificationRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    report: StoryboardDraftVerificationReport
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftFinalizeRequest(DomainModel):
    draft_document_id: str = Field(min_length=1)
    draft_version_id: str = Field(min_length=1)
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardDraftMutationResponse(DomainModel):
    progress: StoryboardDraftProgress


class StoryboardDraftGroupAppendCheckpoint(DomainModel):
    """Compact handoff for one group version created inside a segment append."""

    draft_ref: ProductionDocumentArtifactRef
    draft_version_id: str = Field(min_length=1)
    scene_node_id: str = Field(min_length=1)
    clip_group_id: str = Field(min_length=1)
    shot_ids: list[str] = Field(min_length=1)
    continuity_out: list[str] = Field(default_factory=list[str])


class StoryboardDraftGroupsMutationResponse(DomainModel):
    """Final progress plus every immutable checkpoint created by one segment."""

    progress: StoryboardDraftProgress
    checkpoints: list[StoryboardDraftGroupAppendCheckpoint] = Field(min_length=1)


def populate_storyboard_scene_nodes(
    scene_nodes: list[StoryboardSceneNode],
    shots: list[ShotPlan],
    clip_groups: list[ClipGroup],
) -> list[StoryboardSceneNode]:
    """Project ordered canonical group/shot IDs onto stable scene nodes."""

    shot_by_id = {item.id: item for item in shots}
    node_by_scene_id = {item.scene_id: item for item in scene_nodes}
    group_ids_by_node: dict[str, list[str]] = {
        item.id: [] for item in scene_nodes
    }
    shot_ids_by_node: dict[str, list[str]] = {
        item.id: [] for item in scene_nodes
    }
    groups_by_node: dict[str, list[ClipGroup]] = {
        item.id: [] for item in scene_nodes
    }
    for group in clip_groups:
        group_scene_ids = {shot_by_id[shot_id].scene_id for shot_id in group.shot_ids}
        if len(group_scene_ids) != 1 or None in group_scene_ids:
            raise ValueError("A scene-aware ClipGroup cannot cross script scenes")
        scene_id = next(iter(group_scene_ids))
        if scene_id is None:
            raise ValueError("A scene-aware ClipGroup requires a script scene")
        node = node_by_scene_id.get(scene_id)
        if node is None:
            raise ValueError(
                f"Storyboard ClipGroup {group.id} references unknown scene {scene_id}"
            )
        group_ids_by_node[node.id].append(group.id)
        shot_ids_by_node[node.id].extend(group.shot_ids)
        groups_by_node[node.id].append(group)
    return [
        item.model_copy(
            update={
                "clip_group_ids": group_ids_by_node[item.id],
                "shot_ids": shot_ids_by_node[item.id],
                "continuity_in": (
                    list(groups_by_node[item.id][0].continuity_in)
                    if groups_by_node[item.id]
                    else []
                ),
                "continuity_out": (
                    list(groups_by_node[item.id][-1].continuity_out)
                    if groups_by_node[item.id]
                    else []
                ),
            }
        )
        for item in scene_nodes
    ]


def assemble_storyboard_draft(draft: StoryboardDraftContent) -> StoryboardContent:
    """Deterministically assemble a complete draft into the canonical contract."""

    if draft.phase != StoryboardDraftPhase.GROUP_AUTHORING:
        raise ValueError(
            "Storyboard draft must finish ledger and outline planning before assembly"
        )
    if draft.timing_audit is None:
        raise ValueError("Storyboard draft requires a timing audit before assembly")
    if (
        not draft.timing_audit.timing_audit_passed
        or draft.timing_audit.overloaded_shot_ids
        or draft.timing_audit.overloaded_group_ids
    ):
        raise ValueError(
            "Storyboard timing audit must pass without overloaded Shots or "
            "ClipGroups before assembly"
        )
    node_by_id = {item.id: item for item in draft.scene_nodes}
    planned_ids = [
        item.id
        for item in sorted(
            draft.planned_clip_groups,
            key=lambda item: (
                node_by_id[item.scene_node_id].sequence,
                item.scene_group_sequence,
            ),
        )
    ]
    commits = sorted(
        draft.committed_groups,
        key=lambda item: (
            node_by_id[item.scene_node_id].sequence,
            item.scene_group_sequence,
        ),
    )
    committed_ids = [item.clip_group.id for item in commits]
    if committed_ids != planned_ids:
        missing_ids = [item for item in planned_ids if item not in committed_ids]
        raise ValueError(
            "Storyboard draft is incomplete: every planned ClipGroup must be "
            "committed before assembly"
            + (f"; missing {', '.join(missing_ids)}" if missing_ids else "")
        )
    commits_by_node: dict[str, list[StoryboardClipGroupDraft]] = {
        item.id: [] for item in draft.scene_nodes
    }
    for commit in commits:
        commits_by_node[commit.scene_node_id].append(commit)
    for node in draft.scene_nodes:
        node_commits = commits_by_node[node.id]
        if not node_commits:
            raise ValueError(
                f"Storyboard scene node {node.id} has no committed ClipGroup"
            )
        actual_sequences = [item.scene_group_sequence for item in node_commits]
        if actual_sequences != list(range(1, len(node_commits) + 1)):
            raise ValueError(
                f"Storyboard scene node {node.id} ClipGroups must be contiguous"
            )

    shots: list[ShotPlan] = []
    clip_groups: list[ClipGroup] = []
    for commit in commits:
        resequenced = [
            shot.model_copy(update={"sequence": len(shots) + offset})
            for offset, shot in enumerate(commit.shots, start=1)
        ]
        shots.extend(resequenced)
        clip_groups.append(
            commit.clip_group.model_copy(
                update={"shot_ids": [item.id for item in resequenced]}
            )
        )
    scene_nodes = populate_storyboard_scene_nodes(
        draft.scene_nodes,
        shots,
        clip_groups,
    )
    dialogue_line_ids = [
        line_id for shot in shots for line_id in shot.dialogue_line_ids
    ]
    if dialogue_line_ids != draft.script_line_ids:
        raise ValueError(
            "Storyboard draft is incomplete: all source lines must be covered in order"
        )
    if not shots:
        raise ValueError("Storyboard draft has no editorial Shots")
    shot_duration = sum(item.duration_seconds for item in shots)
    group_duration = sum(item.duration_seconds for item in clip_groups)
    outline_duration = sum(
        item.duration_seconds for item in draft.planned_clip_groups
    )
    audit_duration = draft.timing_audit.computed_episode_duration_s
    if (
        abs(shot_duration - group_duration) > 0.05
        or abs(group_duration - outline_duration) > 0.05
        or abs(outline_duration - audit_duration) > 0.05
    ):
        raise ValueError(
            "Storyboard Shot, ClipGroup, outline, and timing-audit durations "
            "must agree before assembly"
        )
    return StoryboardContent(
        project_id=draft.project_id,
        title=draft.title,
        script_version_id=draft.script_version_id,
        bible_version_id=draft.bible_version_id,
        production_spec_document_id=draft.production_spec_document_id,
        production_spec_version_id=draft.production_spec_version_id,
        script_line_ids=draft.script_line_ids,
        scene_nodes=scene_nodes,
        shots=shots,
        clip_groups=clip_groups,
        computed_duration_seconds=shot_duration,
        timing_notes=draft.timing_notes,
        notes=draft.notes,
    )


class StoryboardContent(DomainModel):
    """Complete structured storyboard and its provider-independent clip groups."""

    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    script_line_ids: list[str] = Field(default_factory=list[str])
    # Empty only for documents persisted before scene aggregation was added.
    scene_nodes: list[StoryboardSceneNode] = Field(
        default_factory=list[StoryboardSceneNode]
    )
    shots: list[ShotPlan] = Field(min_length=1)
    clip_groups: list[ClipGroup] = Field(min_length=1)
    computed_duration_seconds: float = Field(gt=0.0)
    timing_notes: list[str] = Field(default_factory=list[str])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_storyboard_structure(self) -> StoryboardContent:
        shot_ids = [item.id for item in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("Storyboard shot IDs must be unique")
        if any(item.project_id != self.project_id for item in self.shots):
            raise ValueError("Storyboard shots must belong to the storyboard project")
        expected_sequences = list(range(1, len(self.shots) + 1))
        if [item.sequence for item in self.shots] != expected_sequences:
            raise ValueError(
                "Storyboard shots must be stored in contiguous sequence order"
            )

        dialogue_line_ids = [
            line_id for shot in self.shots for line_id in shot.dialogue_line_ids
        ]
        if len(dialogue_line_ids) != len(set(dialogue_line_ids)):
            raise ValueError(
                "Storyboard dialogue line IDs must be assigned exactly once"
            )
        if len(self.script_line_ids) != len(set(self.script_line_ids)):
            raise ValueError("Storyboard source script line IDs must be unique")
        if dialogue_line_ids != self.script_line_ids:
            raise ValueError(
                "Storyboard shots must preserve every source script line in order"
            )

        group_ids = [item.id for item in self.clip_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("Storyboard clip group IDs must be unique")
        grouped_shot_ids = [
            shot_id for group in self.clip_groups for shot_id in group.shot_ids
        ]
        if grouped_shot_ids != shot_ids:
            raise ValueError(
                "Storyboard clip groups must cover every shot exactly once and in order"
            )

        if self.scene_nodes:
            node_ids = [item.id for item in self.scene_nodes]
            node_scene_ids = [item.scene_id for item in self.scene_nodes]
            if len(node_ids) != len(set(node_ids)):
                raise ValueError("Storyboard scene-node IDs must be unique")
            if len(node_scene_ids) != len(set(node_scene_ids)):
                raise ValueError("Storyboard scene-node script scene IDs must be unique")
            if [item.sequence for item in self.scene_nodes] != list(
                range(1, len(self.scene_nodes) + 1)
            ):
                raise ValueError(
                    "Storyboard scene nodes must use contiguous sequence order"
                )
            node_source_line_ids = [
                line_id
                for node in self.scene_nodes
                for line_id in node.source_line_ids
            ]
            if node_source_line_ids != self.script_line_ids:
                raise ValueError(
                    "Storyboard scene nodes must cover source lines exactly in order"
                )
            node_group_ids = [
                group_id
                for node in self.scene_nodes
                for group_id in node.clip_group_ids
            ]
            if node_group_ids != group_ids:
                raise ValueError(
                    "Storyboard scene nodes must cover ClipGroups exactly in order"
                )
            node_shot_ids = [
                shot_id for node in self.scene_nodes for shot_id in node.shot_ids
            ]
            if node_shot_ids != shot_ids:
                raise ValueError(
                    "Storyboard scene nodes must cover Shots exactly in order"
                )
            shot_by_id = {item.id: item for item in self.shots}
            group_by_id = {item.id: item for item in self.clip_groups}
            for node in self.scene_nodes:
                if not node.clip_group_ids or not node.shot_ids:
                    raise ValueError(
                        "Every storyboard scene node requires a ClipGroup and a Shot"
                    )
                if any(
                    shot_by_id[shot_id].scene_id != node.scene_id
                    for shot_id in node.shot_ids
                ):
                    raise ValueError(
                        "Storyboard scene-node Shots must belong to its script scene"
                    )
                expected_node_shots = [
                    shot_id
                    for group_id in node.clip_group_ids
                    for shot_id in group_by_id[group_id].shot_ids
                ]
                if expected_node_shots != node.shot_ids:
                    raise ValueError(
                        "Storyboard scene node must preserve ClipGroup-to-Shot order"
                    )
                node_dialogue_line_ids = [
                    line_id
                    for shot_id in node.shot_ids
                    for line_id in shot_by_id[shot_id].dialogue_line_ids
                ]
                if node_dialogue_line_ids != node.source_line_ids:
                    raise ValueError(
                        "Storyboard scene-node Shots must cover its source lines in order"
                    )

        tolerance = 0.05
        shot_by_id = {item.id: item for item in self.shots}
        for group in self.clip_groups:
            actual_duration = _duration_total(
                [
                    shot_by_id[shot_id].duration_seconds
                    for shot_id in group.shot_ids
                ]
            )
            if actual_duration != Decimal(group.duration_seconds):
                raise ValueError(
                    f"Clip group {group.id} duration must equal its shot durations"
                )
        actual_total = sum(item.duration_seconds for item in self.shots)
        if abs(actual_total - self.computed_duration_seconds) > tolerance:
            raise ValueError(
                "Storyboard computed duration must equal the sum of its shots"
            )
        return self


class AssetPlanDisposition(StrEnum):
    """The Agent's explicit decision for one potential reusable asset."""

    ANCHOR = "ANCHOR"
    TEXT_ONLY = "TEXT_ONLY"
    REUSE_EXISTING = "REUSE_EXISTING"


class AssetPlanOperation(StrEnum):
    """Provider-neutral work that may realize one asset-plan output."""

    GENERATE = "GENERATE"
    EDIT = "EDIT"
    COMPOSE = "COMPOSE"
    IMPORT = "IMPORT"


class AssetPlanReference(DomainModel):
    """One ordered input pinned to prior work or an exact asset version."""

    role: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    source_work_item_id: str | None = Field(default=None, min_length=1, max_length=200)
    asset_id: str | None = Field(default=None, min_length=1, max_length=200)
    asset_version_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_reference_source(self) -> AssetPlanReference:
        from_work_item = self.source_work_item_id is not None
        from_asset_version = self.asset_id is not None or self.asset_version_id is not None
        if from_work_item == from_asset_version:
            raise ValueError(
                "Asset-plan reference must select exactly one work item or asset version"
            )
        if (self.asset_id is None) != (self.asset_version_id is None):
            raise ValueError(
                "Asset-plan reference asset and version IDs must be supplied together"
            )
        return self


class AssetPlanReferencePackIntent(DomainModel):
    """How one completed image should enter the subject's ReferencePack."""

    subject_item_id: str = Field(min_length=1, max_length=200)
    role: ReferenceRole = Field(
        description=(
            "完成图像进入 ReferencePack 时承担的角色。当所属工作项使用 "
            "review_purpose=CANONICAL_MASTER 且提供本意图时，这里必须是 "
            "CANONICAL_MASTER；LOCATION_MASTER、PROP_PRODUCT 等语义角色"
            "用于 SUPPLEMENTAL_REFERENCE 工作项。"
        )
    )
    angle: ReferenceAngle
    framing: ReferenceFraming
    tags: list[str] = Field(max_length=32)
    lineage_root: AssetPlanReference | None = None

    @model_validator(mode="after")
    def validate_tags(self) -> AssetPlanReferencePackIntent:
        normalized = [item.strip() for item in self.tags if item.strip()]
        if normalized != self.tags or len(normalized) != len(set(normalized)):
            raise ValueError(
                "Asset-plan ReferencePack tags must be trimmed, non-empty, and unique"
            )
        return self


class AssetPlanImportIntent(DomainModel):
    """Exact existing AssetVersion to import, including its owning project."""

    source_project_id: str = Field(min_length=1, max_length=200)
    source_asset_id: str = Field(min_length=1, max_length=200)
    source_asset_version_id: str = Field(min_length=1, max_length=200)


class AssetPlanComposeCell(DomainModel):
    """One exact or dependency-produced image cell in a deterministic sheet."""

    source: AssetPlanReference
    view: AssetStandardView
    label: str | None = Field(default=None, min_length=1, max_length=80)


class AssetPlanComposeIntent(DomainModel):
    """Complete provider-neutral payload for image.compose_reference_sheet."""

    strategy: AssetReferenceStrategy
    cells: list[AssetPlanComposeCell] = Field(min_length=1, max_length=9)
    input_policy: AssetSheetInputPolicy = AssetSheetInputPolicy.EXPLICIT_VERSIONS
    columns: int = Field(ge=1, le=3)
    layout_profile: AssetLayoutProfile | None = None
    show_labels: bool
    background_color: str = Field(min_length=1, max_length=64)
    target_size: str | None = Field(default=None, min_length=1, max_length=80)
    parent_version_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_layout(self) -> AssetPlanComposeIntent:
        if self.columns > len(self.cells):
            raise ValueError("AssetPlan compose columns cannot exceed its cell count")
        expected_cells = (
            None
            if self.layout_profile is None
            else {
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: 4,
                AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: 6,
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2: 4,
                AssetLayoutProfile.PROP_MASTER_SINGLE: 1,
            }.get(self.layout_profile)
        )
        if expected_cells is not None and len(self.cells) != expected_cells:
            assert self.layout_profile is not None
            raise ValueError(
                f"{self.layout_profile.value} requires exactly {expected_cells} cells"
            )
        return self


class AssetPlanWorkItem(DomainModel):
    """A stable, dependency-aware execution instruction authored by the Agent."""

    id: str = Field(min_length=1, max_length=200)
    operation: AssetPlanOperation
    output_role: str = Field(min_length=1, max_length=200)
    output_view: AssetStandardView | None = None
    final_prompt: str = Field(min_length=1, max_length=12000)
    size: str = Field(min_length=1, max_length=80)
    review_purpose: AssetReviewPurpose = Field(
        description=(
            "CANONICAL_MASTER 会把完成图像审核为主体资产的 canonical "
            "母版；若同时提供 reference_pack_intent，其 role 必须为 "
            "CANONICAL_MASTER。SUPPLEMENTAL_REFERENCE 必须提供非 canonical "
            "的 ReferencePack 角色。"
        )
    )
    reference_pack_intent: AssetPlanReferencePackIntent | None = Field(
        default=None,
        description=(
            "完成图像如何进入所属主体的 ReferencePack。"
            "SUPPLEMENTAL_REFERENCE 必须提供本意图；CANONICAL_MASTER "
            "可以不提供，但若提供则 role 必须为 CANONICAL_MASTER。"
        ),
    )
    import_intent: AssetPlanImportIntent | None = None
    compose_intent: AssetPlanComposeIntent | None = None
    ordered_references: list[AssetPlanReference] = Field(
        default_factory=list[AssetPlanReference]
    )
    dependency_ids: list[str] = Field(default_factory=list[str])
    completed_asset_id: str | None = Field(default=None, min_length=1, max_length=200)
    completed_asset_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    @model_validator(mode="after")
    def validate_work_item(self) -> AssetPlanWorkItem:
        if len(self.dependency_ids) != len(set(self.dependency_ids)):
            raise ValueError("Asset-plan work-item dependencies must be unique")
        if (self.completed_asset_id is None) != (
            self.completed_asset_version_id is None
        ):
            raise ValueError(
                "Completed asset-plan work-item asset and version IDs must be supplied together"
            )
        if (
            self.review_purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
            and self.reference_pack_intent is None
        ):
            raise ValueError(
                "A supplemental AssetPlan work item requires explicit ReferencePack intent"
            )
        if self.reference_pack_intent is not None:
            role = self.reference_pack_intent.role
            if (
                self.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
                and role != ReferenceRole.CANONICAL_MASTER
            ):
                raise ValueError(
                    "A canonical AssetPlan work item can only declare the canonical "
                    "ReferencePack role"
                )
            if (
                self.review_purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
                and role == ReferenceRole.CANONICAL_MASTER
            ):
                raise ValueError(
                    "A supplemental AssetPlan work item cannot declare the canonical "
                    "ReferencePack role"
                )
        if self.operation == AssetPlanOperation.IMPORT:
            if self.import_intent is None or self.compose_intent is not None:
                raise ValueError(
                    "An IMPORT AssetPlan work item requires only import_intent"
                )
        elif self.operation == AssetPlanOperation.COMPOSE:
            if self.compose_intent is None or self.import_intent is not None:
                raise ValueError(
                    "A COMPOSE AssetPlan work item requires only compose_intent"
                )
        elif self.import_intent is not None or self.compose_intent is not None:
            raise ValueError(
                "Only IMPORT or COMPOSE AssetPlan work items accept operation payloads"
            )
        return self


class AssetPlanItem(DomainModel):
    """One explicit anchor, text-only, or reuse decision in an AssetPlan."""

    id: str = Field(min_length=1, max_length=200)
    disposition: AssetPlanDisposition
    asset_kind: AssetKind
    name: str = Field(min_length=1, max_length=240)
    bible_entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    serves_shot_ids: list[str] = Field(
        default_factory=list[str],
        description="真正使用该资产的 Storyboard Shot ID，不广泛标记同场景镜头。",
    )
    serves_clip_group_ids: list[str] = Field(
        default_factory=list[str],
        description=(
            "该资产服务的 Storyboard ClipGroup ID；至少包含每个 "
            "serves_shot_ids 所属的生成组。"
        ),
    )
    brief: CoreAssetBrief | None = None
    rationale: str = Field(min_length=1, max_length=4000)
    work_items: list[AssetPlanWorkItem] = Field(
        default_factory=list[AssetPlanWorkItem]
    )
    completed_asset_id: str | None = Field(default=None, min_length=1, max_length=200)
    completed_asset_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    @model_validator(mode="after")
    def validate_item(self) -> AssetPlanItem:
        for field_name in ("serves_shot_ids", "serves_clip_group_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"Asset-plan {field_name} must be unique")
        work_item_ids = [item.id for item in self.work_items]
        if len(work_item_ids) != len(set(work_item_ids)):
            raise ValueError("Asset-plan work-item IDs must be unique")
        if (self.completed_asset_id is None) != (
            self.completed_asset_version_id is None
        ):
            raise ValueError(
                "Completed asset-plan item asset and version IDs must be supplied together"
            )
        if self.disposition == AssetPlanDisposition.TEXT_ONLY:
            if self.work_items:
                raise ValueError("A text-only AssetPlan item cannot contain work items")
            if self.completed_asset_id is not None:
                raise ValueError("A text-only AssetPlan item cannot declare completed media")
        if (
            self.disposition == AssetPlanDisposition.REUSE_EXISTING
            and self.completed_asset_id is None
        ):
            raise ValueError(
                "A reuse-existing AssetPlan item requires an exact completed asset version"
            )
        return self


class AssetPlanContent(DomainModel):
    """Versioned Agent-authored inventory and executable core-asset plan."""

    project_id: str = Field(min_length=1)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    storyboard_document_id: str = Field(min_length=1)
    storyboard_version_id: str = Field(min_length=1)
    items: list[AssetPlanItem] = Field(default_factory=list[AssetPlanItem])
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def validate_plan_structure(self) -> AssetPlanContent:
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Asset-plan item IDs must be unique")

        work_items = [work for item in self.items for work in item.work_items]
        work_item_ids = [item.id for item in work_items]
        if len(work_item_ids) != len(set(work_item_ids)):
            raise ValueError("Asset-plan work-item IDs must be globally unique")
        known_work_item_ids = set(work_item_ids)
        item_by_id = {item.id: item for item in self.items}
        role_kinds: dict[ReferenceRole, set[AssetKind]] = {
            ReferenceRole.CANONICAL_MASTER: {
                AssetKind.CHARACTER,
                AssetKind.LOCATION,
                AssetKind.PROP,
            },
            ReferenceRole.CHARACTER_FACE: {AssetKind.CHARACTER},
            ReferenceRole.CHARACTER_BODY: {AssetKind.CHARACTER},
            ReferenceRole.CHARACTER_PROFILE: {AssetKind.CHARACTER},
            ReferenceRole.CHARACTER_BACK: {AssetKind.CHARACTER},
            ReferenceRole.LOCATION_MASTER: {AssetKind.LOCATION},
            ReferenceRole.LOCATION_REVERSE: {AssetKind.LOCATION},
            ReferenceRole.LOCATION_INTERACTION: {AssetKind.LOCATION},
            ReferenceRole.PROP_PRODUCT: {AssetKind.PROP},
            ReferenceRole.PROP_IN_HAND: {AssetKind.PROP},
            ReferenceRole.PROP_STATE: {AssetKind.PROP},
        }
        preceding_work_item_ids: set[str] = set()
        for work_item in work_items:
            for dependency_id in work_item.dependency_ids:
                if dependency_id not in known_work_item_ids:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} references unknown dependency "
                        f"{dependency_id}"
                    )
                if dependency_id not in preceding_work_item_ids:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} dependencies must precede it"
                    )
            for reference in work_item.ordered_references:
                source_work_item_id = reference.source_work_item_id
                if source_work_item_id is None:
                    continue
                if source_work_item_id not in known_work_item_ids:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} references unknown work item "
                        f"{source_work_item_id}"
                    )
                if source_work_item_id not in preceding_work_item_ids:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} references must precede it"
                    )
                if source_work_item_id not in work_item.dependency_ids:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} must declare referenced work "
                        f"item {source_work_item_id} as a dependency"
                    )
            if work_item.compose_intent is not None:
                for cell in work_item.compose_intent.cells:
                    source_work_item_id = cell.source.source_work_item_id
                    if source_work_item_id is None:
                        continue
                    if source_work_item_id not in known_work_item_ids:
                        raise ValueError(
                            f"Asset-plan compose work item {work_item.id} references "
                            f"unknown work item {source_work_item_id}"
                        )
                    if source_work_item_id not in preceding_work_item_ids:
                        raise ValueError(
                            f"Asset-plan compose work item {work_item.id} source work "
                            "items must precede it"
                        )
                    if source_work_item_id not in work_item.dependency_ids:
                        raise ValueError(
                            f"Asset-plan compose work item {work_item.id} must declare "
                            f"cell source {source_work_item_id} as a dependency"
                        )
            pack_intent = work_item.reference_pack_intent
            if pack_intent is not None:
                subject_item = item_by_id.get(pack_intent.subject_item_id)
                if subject_item is None:
                    raise ValueError(
                        f"Asset-plan work item {work_item.id} ReferencePack intent "
                        f"references unknown subject item {pack_intent.subject_item_id}"
                    )
                if subject_item.asset_kind not in role_kinds[pack_intent.role]:
                    raise ValueError(
                        f"ReferencePack role {pack_intent.role.value} is incompatible "
                        f"with subject item kind {subject_item.asset_kind.value}"
                    )
            if pack_intent is not None and pack_intent.lineage_root is not None:
                source_work_item_id = pack_intent.lineage_root.source_work_item_id
                if source_work_item_id is not None:
                    if source_work_item_id not in known_work_item_ids:
                        raise ValueError(
                            f"Asset-plan work item {work_item.id} lineage root references "
                            f"unknown work item {source_work_item_id}"
                        )
                    if source_work_item_id not in preceding_work_item_ids:
                        raise ValueError(
                            f"Asset-plan work item {work_item.id} lineage-root work item "
                            "must precede it"
                        )
                    if source_work_item_id not in work_item.dependency_ids:
                        raise ValueError(
                            f"Asset-plan work item {work_item.id} must declare lineage-root "
                            f"work item {source_work_item_id} as a dependency"
                        )
            preceding_work_item_ids.add(work_item.id)

        for item in self.items:
            if (
                item.disposition == AssetPlanDisposition.ANCHOR
                and item.completed_asset_id is not None
                and not any(
                    work_item.completed_asset_id == item.completed_asset_id
                    and work_item.completed_asset_version_id
                    == item.completed_asset_version_id
                    for work_item in item.work_items
                )
            ):
                raise ValueError(
                    f"Completed anchor item {item.id} must match one completed work item"
                )
        return self


class GenerationUnit(DomainModel):
    """Provider-independent execution unit compiled from one ClipGroup."""

    id: str = Field(min_length=1, max_length=200)
    clip_group_id: str = Field(min_length=1, max_length=200)
    output_slot_id: str = Field(min_length=1, max_length=240)
    shot_ids: list[str] = Field(min_length=1)
    shot_windows: list[GenerationUnitShotWindow] = Field(min_length=1)
    requested_duration_seconds: float = Field(gt=0.0)
    generation_intent: str = Field(min_length=1, max_length=4000)
    transition_intent: str | None = Field(default=None, min_length=1, max_length=2000)
    continuity_in: list[str] = Field(default_factory=list[str])
    visible_changes: list[str] = Field(default_factory=list[str])
    continuity_out: list[str] = Field(default_factory=list[str])
    reference_notes: list[str] = Field(default_factory=list[str])
    provider: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    resolution: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_windows(self) -> GenerationUnit:
        window_shot_ids = [item.shot_id for item in self.shot_windows]
        if self.shot_ids != window_shot_ids:
            raise ValueError(
                "Generation-unit shot IDs must match its ordered source windows"
            )
        if len(window_shot_ids) != len(set(window_shot_ids)):
            raise ValueError("Generation-unit shot IDs must be unique")
        tolerance = 0.001
        if abs(self.shot_windows[0].source_in_seconds) > tolerance:
            raise ValueError("Generation-unit source windows must start at 0 seconds")
        for previous, current in zip(
            self.shot_windows,
            self.shot_windows[1:],
            strict=False,
        ):
            if abs(previous.source_out_seconds - current.source_in_seconds) > tolerance:
                raise ValueError(
                    "Generation-unit source windows must be contiguous and ordered"
                )
        if abs(
            self.shot_windows[-1].source_out_seconds
            - self.requested_duration_seconds
        ) > tolerance:
            raise ValueError(
                "Generation-unit windows must cover the requested clip duration"
            )
        return self


class GenerationPlanContent(DomainModel):
    """Versioned bridge from a storyboard to provider generation tasks."""

    project_id: str = Field(min_length=1)
    storyboard_document_id: str = Field(min_length=1)
    storyboard_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    shot_ids: list[str] = Field(min_length=1)
    units: list[GenerationUnit] = Field(min_length=1)
    total_duration_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_generation_plan(self) -> GenerationPlanContent:
        unit_ids = [item.id for item in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("Generation-plan unit IDs must be unique")
        clip_group_ids = [item.clip_group_id for item in self.units]
        if len(clip_group_ids) != len(set(clip_group_ids)):
            raise ValueError("Generation-plan clip group IDs must be unique")
        planned_shot_ids = [
            shot_id for unit in self.units for shot_id in unit.shot_ids
        ]
        if planned_shot_ids != self.shot_ids:
            raise ValueError(
                "Generation-plan units must cover every shot exactly once and in order"
            )
        if abs(
            sum(item.requested_duration_seconds for item in self.units)
            - self.total_duration_seconds
        ) > 0.05:
            raise ValueError(
                "Generation-plan duration must equal the sum of generation units"
            )
        return self


class ProductionSpecCreateRequest(DomainModel):
    spec: ProductionSpecContent
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class ProductionSpecReplaceRequest(ProductionSpecCreateRequest):
    base_version_id: str = Field(min_length=1)


class StoryboardCreateRequest(DomainModel):
    storyboard: StoryboardContent
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class StoryboardReplaceRequest(StoryboardCreateRequest):
    base_version_id: str = Field(min_length=1)


class AssetPlanCreateRequest(DomainModel):
    asset_plan: AssetPlanContent
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class AssetPlanReplaceRequest(AssetPlanCreateRequest):
    base_version_id: str = Field(min_length=1)


class StoryboardShotReplacement(DomainModel):
    shot_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    replacement: ShotPlan

    @model_validator(mode="after")
    def validate_replacement_identity(self) -> StoryboardShotReplacement:
        if self.replacement.id != self.shot_id:
            raise ValueError("Storyboard replacement must preserve the shot ID")
        return self


class StoryboardPatchRequest(DomainModel):
    """Targeted shot revisions; structural rewrites use StoryboardReplaceRequest."""

    base_version_id: str = Field(min_length=1)
    replacements: list[StoryboardShotReplacement] = Field(
        default_factory=list[StoryboardShotReplacement]
    )
    clip_groups: list[ClipGroup] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    timing_notes: list[str] | None = None
    notes: list[str] | None = None
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin

    @model_validator(mode="after")
    def validate_patch(self) -> StoryboardPatchRequest:
        replacement_ids = [item.shot_id for item in self.replacements]
        if len(replacement_ids) != len(set(replacement_ids)):
            raise ValueError("A storyboard patch may replace each shot only once")
        if not self.replacements and all(
            item is None
            for item in (
                self.clip_groups,
                self.title,
                self.timing_notes,
                self.notes,
            )
        ):
            raise ValueError("Storyboard patch must contain a semantic change")
        return self


class GenerationPlanCompileRequest(DomainModel):
    storyboard_document_id: str = Field(min_length=1)
    storyboard_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    base_version_id: str | None = Field(default=None, min_length=1)
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class OutputSlotBindRequest(DomainModel):
    slot_id: str = Field(min_length=1, max_length=200)
    artifact_ref: ProductionDocumentArtifactRef
    expected_revision: int | None = Field(default=None, ge=1)
    producer_agent_run_id: str | None = Field(default=None, min_length=1)
    producer_work_order_id: str | None = Field(default=None, min_length=1)


class OutputSlotInvalidateRequest(DomainModel):
    slot_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int | None = Field(default=None, ge=1)
    producer_agent_run_id: str | None = Field(default=None, min_length=1)
    producer_work_order_id: str | None = Field(default=None, min_length=1)


class ProductionSpecReadResponse(DomainModel):
    spec: ProductionSpecContent
    document: ProductionDocument
    version: ProductionDocumentVersion
    output_slot: ProjectOutputSlot | None = None


class ProductionSpecMutationResponse(ProductionSpecReadResponse):
    pass


class StoryboardReadResponse(DomainModel):
    storyboard: StoryboardContent
    document: ProductionDocument
    version: ProductionDocumentVersion
    output_slot: ProjectOutputSlot | None = None


class StoryboardMutationResponse(StoryboardReadResponse):
    pass


class AssetPlanReadResponse(DomainModel):
    asset_plan: AssetPlanContent
    document: ProductionDocument
    version: ProductionDocumentVersion
    output_slot: ProjectOutputSlot | None = None


class AssetPlanMutationResponse(AssetPlanReadResponse):
    pass


class GenerationPlanReadResponse(DomainModel):
    generation_plan: GenerationPlanContent
    document: ProductionDocument
    version: ProductionDocumentVersion
    output_slot: ProjectOutputSlot | None = None


class GenerationPlanMutationResponse(GenerationPlanReadResponse):
    pass


class OutputSlotReadResponse(DomainModel):
    output_slot: ProjectOutputSlot


class OutputSlotListResponse(DomainModel):
    output_slots: list[ProjectOutputSlot] = Field(default_factory=list[ProjectOutputSlot])


class OutputSlotMutationResponse(OutputSlotReadResponse):
    pass


def production_document_payload(content: DomainModel) -> dict[str, JsonValue]:
    """Serialize typed content into the generic ProductionDocument payload."""

    return cast(dict[str, JsonValue], content.model_dump(mode="json"))


def bind_project_output_slot(
    *,
    project_id: str,
    current: ProjectOutputSlot | None,
    request: OutputSlotBindRequest,
    updated_at: str,
) -> ProjectOutputSlot:
    """Return the next idempotent binding for one stable logical output slot."""

    if request.artifact_ref.project_id != project_id:
        raise ValueError("Output slot artifact must belong to the project")
    if current is None:
        if request.expected_revision is not None:
            raise ValueError("A new output slot cannot have an expected revision")
        return ProjectOutputSlot(
            id=request.slot_id,
            revision=1,
            current_artifact_ref=request.artifact_ref,
            producer_agent_run_id=request.producer_agent_run_id,
            producer_work_order_id=request.producer_work_order_id,
            updated_at=updated_at,
        )
    if current.id != request.slot_id:
        raise ValueError("Output slot update must preserve the slot ID")
    if (
        request.expected_revision is not None
        and request.expected_revision != current.revision
    ):
        raise ValueError(
            f"Output slot {current.id} revision changed from "
            f"{request.expected_revision} to {current.revision}"
        )
    if current.current_artifact_ref == request.artifact_ref:
        return current

    revision_history = [
        *current.revision_history,
        _output_slot_revision_snapshot(current),
    ]
    next_ref_key = _artifact_ref_key(request.artifact_ref)
    previous_refs = [
        item
        for item in current.previous_artifact_refs
        if _artifact_ref_key(item) != next_ref_key
    ]
    if current.current_artifact_ref is not None:
        previous_refs.append(current.current_artifact_ref)
    return ProjectOutputSlot(
        id=current.id,
        revision=current.revision + 1,
        current_artifact_ref=request.artifact_ref,
        previous_artifact_refs=previous_refs,
        revision_history=revision_history,
        producer_agent_run_id=request.producer_agent_run_id,
        producer_work_order_id=request.producer_work_order_id,
        updated_at=updated_at,
    )


def invalidate_project_output_slot(
    *,
    project_id: str,
    current: ProjectOutputSlot,
    request: OutputSlotInvalidateRequest,
    updated_at: str,
) -> ProjectOutputSlot:
    """Clear a stale logical result without discarding its artifact history."""

    if current.id != request.slot_id:
        raise ValueError("Output slot invalidation must preserve the slot ID")
    if (
        request.expected_revision is not None
        and request.expected_revision != current.revision
    ):
        raise ValueError(
            f"Output slot {current.id} revision changed from "
            f"{request.expected_revision} to {current.revision}"
        )
    if current.current_artifact_ref is None:
        return current
    if current.current_artifact_ref.project_id != project_id:
        raise ValueError("Output slot artifact must belong to the project")
    previous_refs = [*current.previous_artifact_refs, current.current_artifact_ref]
    return ProjectOutputSlot(
        id=current.id,
        revision=current.revision + 1,
        current_artifact_ref=None,
        previous_artifact_refs=previous_refs,
        revision_history=[
            *current.revision_history,
            _output_slot_revision_snapshot(current),
        ],
        invalidation_reason=request.reason,
        producer_agent_run_id=request.producer_agent_run_id,
        producer_work_order_id=request.producer_work_order_id,
        updated_at=updated_at,
    )


def apply_storyboard_patch(
    current: StoryboardContent,
    request: StoryboardPatchRequest,
) -> StoryboardContent:
    """Apply a targeted patch while preserving stable shot identities."""

    replacements = {item.shot_id: item for item in request.replacements}
    known_shot_ids = {item.id for item in current.shots}
    unknown_shot_ids = set(replacements) - known_shot_ids
    if unknown_shot_ids:
        unknown = ", ".join(sorted(unknown_shot_ids))
        raise ValueError(f"Storyboard patch references unknown shots: {unknown}")

    next_shots: list[ShotPlan] = []
    for shot in current.shots:
        replacement = replacements.get(shot.id)
        if replacement is None:
            next_shots.append(shot)
            continue
        if replacement.expected_revision != shot.revision:
            raise ValueError(
                f"Storyboard shot {shot.id} revision changed from "
                f"{replacement.expected_revision} to {shot.revision}"
            )
        candidate = replacement.replacement.model_copy(
            update={"revision": shot.revision}
        )
        changed = candidate.model_dump(mode="json") != shot.model_dump(mode="json")
        next_shots.append(
            candidate.model_copy(
                update={"revision": shot.revision + 1 if changed else shot.revision}
            )
        )

    next_clip_groups = (
        request.clip_groups
        if request.clip_groups is not None
        else current.clip_groups
    )
    next_scene_nodes = (
        populate_storyboard_scene_nodes(
            current.scene_nodes,
            next_shots,
            next_clip_groups,
        )
        if current.scene_nodes
        else []
    )

    return StoryboardContent(
        project_id=current.project_id,
        title=request.title if request.title is not None else current.title,
        script_version_id=current.script_version_id,
        bible_version_id=current.bible_version_id,
        production_spec_document_id=current.production_spec_document_id,
        production_spec_version_id=current.production_spec_version_id,
        script_line_ids=current.script_line_ids,
        scene_nodes=next_scene_nodes,
        shots=next_shots,
        clip_groups=next_clip_groups,
        computed_duration_seconds=sum(
            item.duration_seconds for item in next_shots
        ),
        timing_notes=(
            request.timing_notes
            if request.timing_notes is not None
            else current.timing_notes
        ),
        notes=request.notes if request.notes is not None else current.notes,
    )


def compile_generation_plan(
    *,
    storyboard: StoryboardContent,
    storyboard_document_id: str,
    storyboard_version_id: str,
    production_spec: ProductionSpecContent,
    production_spec_document_id: str,
    production_spec_version_id: str,
) -> GenerationPlanContent:
    """Compile ClipGroups into source-window-preserving generation units."""

    if storyboard.project_id != production_spec.project_id:
        raise ValueError("Production spec and storyboard must belong to one project")
    if storyboard.production_spec_document_id != production_spec_document_id:
        raise ValueError("Storyboard references a different production spec document")
    if storyboard.production_spec_version_id != production_spec_version_id:
        raise ValueError("Storyboard references a different production spec version")
    if storyboard.script_version_id != production_spec.script_version_id:
        raise ValueError("Production spec and storyboard script versions must match")
    if storyboard.bible_version_id != production_spec.bible_version_id:
        raise ValueError("Production spec and storyboard bible versions must match")

    shot_by_id = {item.id: item for item in storyboard.shots}
    preference = production_spec.provider_preference
    units: list[GenerationUnit] = []
    for group in storyboard.clip_groups:
        cursor = 0.0
        windows: list[GenerationUnitShotWindow] = []
        for shot_id in group.shot_ids:
            shot = shot_by_id[shot_id]
            source_out = cursor + shot.duration_seconds
            windows.append(
                GenerationUnitShotWindow(
                    shot_id=shot.id,
                    shot_revision=shot.revision,
                    source_in_seconds=cursor,
                    source_out_seconds=source_out,
                    duration_seconds=shot.duration_seconds,
                )
            )
            cursor = source_out
        units.append(
            GenerationUnit(
                id=group.id,
                clip_group_id=group.id,
                output_slot_id=f"video.generation-unit.{group.id}",
                shot_ids=group.shot_ids,
                shot_windows=windows,
                requested_duration_seconds=cursor,
                generation_intent=group.generation_intent,
                transition_intent=group.transition_intent,
                continuity_in=group.continuity_in,
                visible_changes=group.visible_changes,
                continuity_out=group.continuity_out,
                reference_notes=group.reference_notes,
                provider=preference.provider if preference is not None else None,
                model=preference.model if preference is not None else None,
                resolution=(
                    preference.resolution if preference is not None else None
                ),
            )
        )

    return GenerationPlanContent(
        project_id=storyboard.project_id,
        storyboard_document_id=storyboard_document_id,
        storyboard_version_id=storyboard_version_id,
        production_spec_document_id=production_spec_document_id,
        production_spec_version_id=production_spec_version_id,
        script_version_id=storyboard.script_version_id,
        bible_version_id=storyboard.bible_version_id,
        shot_ids=[item.id for item in storyboard.shots],
        units=units,
        total_duration_seconds=sum(
            item.requested_duration_seconds for item in units
        ),
    )


def _artifact_ref_key(
    artifact_ref: ProductionDocumentArtifactRef,
) -> tuple[str, str, str, int | None]:
    return (
        artifact_ref.project_id,
        artifact_ref.artifact_type,
        artifact_ref.artifact_id,
        artifact_ref.version,
    )


def _output_slot_revision_snapshot(
    output_slot: ProjectOutputSlot,
) -> ProjectOutputSlotRevision:
    return ProjectOutputSlotRevision(
        revision=output_slot.revision,
        current_artifact_ref=(
            None
            if output_slot.current_artifact_ref is None
            else output_slot.current_artifact_ref.model_copy(deep=True)
        ),
        invalidation_reason=output_slot.invalidation_reason,
        producer_agent_run_id=output_slot.producer_agent_run_id,
        producer_work_order_id=output_slot.producer_work_order_id,
        updated_at=output_slot.updated_at,
    )
