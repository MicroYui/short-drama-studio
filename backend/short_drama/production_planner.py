"""Structured autonomous planning for empty, image, idea, and locked-script inputs."""

from __future__ import annotations

import base64
from collections import Counter
import json
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import Field, field_validator, model_validator

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetSourceRecord,
    AssetVersion,
    AudioKind,
    CapabilitySnapshot,
    CharacterProfile,
    ContinuationPreference,
    ContentPlanningOutput,
    ContinuityState,
    CoreAssetBrief,
    CreateProjectRequest,
    DomainModel,
    DurationAlignment,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationFitAssessment,
    DurationMismatchResolution,
    DurationTargetOrigin,
    EndingContract,
    ExecutionPlanEntry,
    ExecutionStrategy,
    Freshness,
    LocationProfile,
    OriginType,
    PlanningRunStatus,
    ProjectBibleVersion,
    ProjectDocument,
    ProjectPlanningMetadata,
    ProjectRecord,
    ProjectStage,
    PropProfile,
    ReferenceAngle,
    ReferenceBinding,
    ReferenceFraming,
    ReferencePlanEntry,
    ReferenceRequirement,
    ReferenceRole,
    ReviewRepairOwner,
    ResolvedEndingMode,
    RevisionJudgeRecommendation,
    ScriptLine,
    ScriptReviewDisposition,
    ScriptReviewDispositionAction,
    ScriptReviewIssueRoute,
    ScriptReviewReport,
    StoryRepairAction,
    StoryRepairDiagnosis,
    StoryRepairPlan,
    StoryMetadataRevisionPatch,
    StoryIntent,
    StoryIssueLedgerEntry,
    StorySourceContract,
    StoryRevisionVerification,
    ScriptVersion,
    SceneContinuitySummary,
    ShotPlan,
    ShotReferenceCue,
    ShotTimelineBeat,
    SourceMode,
    StructuredScene,
    StyleProfile,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    narrative_skill_catalog,
    production_method_skill_catalog,
    production_skill,
)
from short_drama.reference_aliases import normalize_reference_alias
from short_drama.style_context import UNSPECIFIED_STYLE_VALUE
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)


_STORY_INTERACTION_PRIORITY = (
    "交互意图按以下优先级理解：用户已明确的设定、数值和选择直接采用，"
    "不得重复询问；用户明确要求在某个内容节点确认、审阅或选择时，完整保留该要求，"
    "交由下游 Production Manager 只在对应语义节点暂停；其余部分若用户要求不交互、"
    "直接完成或由 Agent 决定，则由 Agent 自主补齐并继续。"
    "RunMode.GUIDED 只表示用户可参与，不代表每个阶段都必须确认；"
    "RunMode.AUTO_DRAFT 表示除用户明确要求的确认外，应自主继续。"
)

# PlannedShot remains a positive-duration runtime model. Local fallback shots use
# this schema-compatible technical placeholder only; it is not an estimate of
# natural performance time and must not be scaled to the episode/scene target.
_UNRESOLVED_FALLBACK_SHOT_DURATION_SECONDS = 4.0


class PlannedDialogueLine(DomainModel):
    speaker: str = Field(min_length=1, max_length=80)
    speaker_character_id: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=500)
    audio_kind: AudioKind = AudioKind.DIALOGUE


class PlannedCharacter(DomainModel):
    character_id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    age: int = Field(ge=0)
    appearance: str = Field(min_length=1, max_length=500)
    wardrobe: str = Field(min_length=1, max_length=500)
    performance: str = Field(min_length=1, max_length=500)
    voice_identity: str = Field(min_length=1, max_length=80)

    @field_validator(
        "character_id",
        "name",
        "appearance",
        "wardrobe",
        "performance",
        "voice_identity",
        mode="before",
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PlannedLocation(DomainModel):
    location_id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    layout: str = Field(min_length=1, max_length=800)
    lighting: str = Field(min_length=1, max_length=500)
    palette: str = Field(min_length=1, max_length=500)
    key_views: list[str] = Field(min_length=1, max_length=8)

    @field_validator(
        "location_id", "name", "layout", "lighting", "palette", mode="before"
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("key_views", mode="before")
    @classmethod
    def trim_key_views(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            item.strip() if isinstance(item, str) else item
            for item in cast(list[object], value)
        ]

    @model_validator(mode="after")
    def validate_key_views(self) -> PlannedLocation:
        normalized = [item.strip() for item in self.key_views if item.strip()]
        if normalized != self.key_views or len(normalized) != len(set(normalized)):
            raise ValueError("Planned location key views must be trimmed and unique")
        return self


class PlannedProp(DomainModel):
    prop_id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    appearance: str = Field(min_length=1, max_length=500)

    @field_validator("prop_id", "name", "appearance", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PlannedStyle(DomainModel):
    medium: str = Field(min_length=1, max_length=300)
    genre: str = Field(min_length=1, max_length=120)
    cinematography: str = Field(min_length=1, max_length=800)
    color: str = Field(min_length=1, max_length=500)
    texture: str = Field(min_length=1, max_length=500)
    forbidden: list[str] = Field(default_factory=list[str], max_length=12)

    @field_validator(
        "medium",
        "genre",
        "cinematography",
        "color",
        "texture",
        mode="before",
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PlannedReferenceNeed(DomainModel):
    """Provider-neutral visual coverage requested by the planning Agent."""

    subject_name: str = Field(min_length=1, max_length=120)
    acceptable_roles: list[ReferenceRole] = Field(min_length=1, max_length=4)
    preferred_angles: list[ReferenceAngle] = Field(
        default_factory=list[ReferenceAngle],
        max_length=4,
    )
    preferred_framings: list[ReferenceFraming] = Field(
        default_factory=list[ReferenceFraming],
        max_length=4,
    )
    tags: list[str] = Field(default_factory=list[str], max_length=12)
    purpose: str = Field(min_length=1, max_length=500)
    required: bool = True

    @field_validator("subject_name", "purpose", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PlannedShotReferenceCue(DomainModel):
    subject_name: str = Field(min_length=1, max_length=120)
    alias: str = Field(min_length=1, max_length=80)
    required_tags: list[str] = Field(default_factory=list[str], max_length=12)
    purpose: str = Field(min_length=1, max_length=500)

    @field_validator("subject_name", "purpose", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("alias", mode="before")
    @classmethod
    def normalize_alias(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if value != value.strip() or value.startswith("@"):
            raise ValueError(
                "Planned shot reference aliases must be trimmed and omit the @ prefix"
            )
        return normalize_reference_alias(value, max_length=80)

    @model_validator(mode="after")
    def validate_reference_cue(self) -> PlannedShotReferenceCue:
        normalized_tags = [item.strip() for item in self.required_tags if item.strip()]
        if normalized_tags != self.required_tags or len(normalized_tags) != len(
            set(normalized_tags)
        ):
            raise ValueError("Planned shot reference tags must be trimmed and unique")
        return self


class PlannedShotTimelineBeat(DomainModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    visual: str = Field(min_length=1, max_length=1200)
    framing: str = Field(min_length=1, max_length=200)
    camera: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=800)
    performance: str | None = Field(default=None, min_length=1, max_length=500)
    emotion: str | None = Field(default=None, min_length=1, max_length=300)
    dialogue_line_indices: list[int] = Field(default_factory=list[int], max_length=40)
    sound: str | None = Field(default=None, min_length=1, max_length=500)
    references: list[PlannedShotReferenceCue] = Field(
        default_factory=list[PlannedShotReferenceCue],
        max_length=12,
    )

    @model_validator(mode="after")
    def validate_time_range(self) -> PlannedShotTimelineBeat:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Planned shot timeline beat end_seconds must follow start_seconds")
        if any(index < 1 for index in self.dialogue_line_indices):
            raise ValueError("Planned shot timeline dialogue indices must be 1-based")
        if len(self.dialogue_line_indices) != len(set(self.dialogue_line_indices)):
            raise ValueError("Planned shot timeline dialogue indices must be unique")
        aliases = [item.alias for item in self.references]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Planned shot timeline reference aliases must be unique")
        return self


class PlannedShot(DomainModel):
    scene_sequence: int = Field(default=1, ge=1)
    duration_seconds: float = Field(gt=0.0)
    narrative_purpose: str = Field(min_length=1, max_length=500)
    visual: str = Field(min_length=1, max_length=1200)
    framing: str = Field(min_length=1, max_length=200)
    camera: str = Field(min_length=1, max_length=500)
    audio_intent: str = Field(min_length=1, max_length=800)
    dialogue_line_indices: list[int] = Field(default_factory=list[int], max_length=40)
    timeline_beats: list[PlannedShotTimelineBeat] = Field(
        default_factory=list[PlannedShotTimelineBeat],
        max_length=24,
    )
    character_positions: str = Field(min_length=1, max_length=500)
    eyeline: str = Field(min_length=1, max_length=500)
    prop_state_in: str = Field(min_length=1, max_length=500)
    prop_state_out: str = Field(min_length=1, max_length=500)
    action_in: str = Field(min_length=1, max_length=500)
    action_out: str = Field(min_length=1, max_length=500)
    transition_out: str = Field(min_length=1, max_length=120)
    merge_candidate: bool = False
    local_rework_risk: str = Field(min_length=1, max_length=120)
    reference_needs: list[PlannedReferenceNeed] = Field(
        default_factory=list[PlannedReferenceNeed],
        max_length=12,
    )

    @model_validator(mode="after")
    def validate_timeline_beats(self) -> PlannedShot:
        if not self.timeline_beats:
            return self
        tolerance = 0.05
        if abs(self.timeline_beats[0].start_seconds) > tolerance:
            raise ValueError("Planned shot timeline beats must start at 0 seconds")
        for previous, current in zip(
            self.timeline_beats,
            self.timeline_beats[1:],
            strict=False,
        ):
            if abs(previous.end_seconds - current.start_seconds) > tolerance:
                raise ValueError("Planned shot timeline beats must be contiguous and ordered")
        if abs(self.timeline_beats[-1].end_seconds - self.duration_seconds) > tolerance:
            raise ValueError(
                "Planned shot timeline beats must cover the editorial shot duration"
            )
        assigned_dialogue = [
            index
            for beat in self.timeline_beats
            for index in beat.dialogue_line_indices
        ]
        ordered_dialogue: list[int] = []
        for index in assigned_dialogue:
            if not ordered_dialogue or ordered_dialogue[-1] != index:
                ordered_dialogue.append(index)
        if ordered_dialogue != self.dialogue_line_indices:
            raise ValueError(
                "Planned shot timeline beats must cover shot dialogue lines in order; "
                "one line may span adjacent beats"
            )
        alias_targets: dict[str, tuple[str, tuple[str, ...]]] = {}
        for beat in self.timeline_beats:
            for cue in beat.references:
                target = (cue.subject_name, tuple(cue.required_tags))
                existing = alias_targets.setdefault(cue.alias, target)
                if existing != target:
                    raise ValueError(
                        "A planned shot reference alias cannot identify different subjects or views"
                    )
                supported = any(
                    need.subject_name == cue.subject_name
                    and set(cue.required_tags).issubset(need.tags)
                    for need in self.reference_needs
                )
                if not supported:
                    raise ValueError(
                        f"Timeline reference @{cue.alias} needs a matching reference_needs entry"
                    )
        return self


class PlannedSceneContract(DomainModel):
    sequence: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1600)
    narrative_purpose: str = Field(min_length=1, max_length=800)
    playable_actions: list[str] = Field(default_factory=list[str], max_length=24)
    screenplay_text: str = Field(default="", max_length=60000)
    target_duration_seconds: float = Field(gt=0.0)
    dialogue_line_indices: list[int] = Field(default_factory=list[int], max_length=500)
    location_names: list[str] = Field(min_length=1, max_length=6)
    prop_ids: list[str] = Field(default_factory=list[str])
    continuity_in: SceneContinuitySummary
    continuity_out: SceneContinuitySummary


class EpisodeOutline(DomainModel):
    title: str = Field(min_length=1, max_length=120)
    story_summary: str = Field(min_length=1, max_length=2400)
    target_duration_seconds: float = Field(ge=4.0)
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=5000,
    )
    characters: list[PlannedCharacter] = Field(min_length=1, max_length=12)
    locations: list[PlannedLocation] = Field(min_length=1, max_length=20)
    props: list[PlannedProp] = Field(default_factory=list[PlannedProp], max_length=20)
    style: PlannedStyle
    ending_contract: EndingContract | None = None
    story_intent: StoryIntent | None = None
    scenes: list[PlannedSceneContract] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_outline(self) -> EpisodeOutline:
        character_ids = [
            item.character_id
            for item in self.characters
            if item.character_id is not None
        ]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("Planned character IDs must be unique")
        location_ids = [
            item.location_id for item in self.locations if item.location_id is not None
        ]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("Planned location IDs must be unique")
        prop_ids = [item.prop_id for item in self.props if item.prop_id is not None]
        if len(prop_ids) != len(set(prop_ids)):
            raise ValueError("Planned prop IDs must be unique")
        known_character_ids = set(character_ids)
        unknown_speaker_ids = sorted(
            {
                line.speaker_character_id
                for line in self.lines
                if line.speaker_character_id is not None
                and line.speaker_character_id not in known_character_ids
            }
        )
        if unknown_speaker_ids:
            raise ValueError(
                "Dialogue lines reference unknown planned character IDs: "
                + ", ".join(unknown_speaker_ids)
            )
        subject_names = [
            *(item.name for item in self.characters),
            *(item.name for item in self.locations),
            *(item.name for item in self.props),
        ]
        duplicate_subjects = sorted(
            name for name, count in Counter(subject_names).items() if count > 1
        )
        if duplicate_subjects:
            raise ValueError(
                "Character, location, and prop names must be globally unique: "
                + ", ".join(duplicate_subjects)
            )
        sequences = [scene.sequence for scene in self.scenes]
        if sequences != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scene sequences must be contiguous and 1-based")
        line_count = len(self.lines)
        assigned = [
            index
            for scene in self.scenes
            for index in scene.dialogue_line_indices
        ]
        invalid = sorted({index for index in assigned if index < 1 or index > line_count})
        if invalid:
            raise ValueError(f"Scene dialogue indices are out of range: {invalid}")
        duplicates = sorted(index for index, count in Counter(assigned).items() if count > 1)
        if duplicates:
            raise ValueError(f"Dialogue lines are assigned to multiple scenes: {duplicates}")
        missing = sorted(set(range(1, line_count + 1)) - set(assigned))
        if missing:
            raise ValueError(f"Dialogue lines are not assigned to a scene: {missing}")
        known_locations = {location.name for location in self.locations}
        unknown_locations = sorted(
            {
                name
                for scene in self.scenes
                for name in scene.location_names
                if name not in known_locations
            }
        )
        if unknown_locations:
            raise ValueError(
                "Scene contracts use unknown locations: " + ", ".join(unknown_locations)
            )
        known_prop_ids = set(prop_ids)
        unknown_scene_prop_ids = sorted(
            {
                prop_id
                for scene in self.scenes
                for prop_id in scene.prop_ids
                if prop_id not in known_prop_ids
            }
        )
        if unknown_scene_prop_ids:
            raise ValueError(
                "Scene contracts use unknown prop IDs: "
                + ", ".join(unknown_scene_prop_ids)
            )
        scenes_with_duplicate_prop_ids = [
            scene.sequence
            for scene in self.scenes
            if len(scene.prop_ids) != len(set(scene.prop_ids))
        ]
        if scenes_with_duplicate_prop_ids:
            raise ValueError(
                "Scene contracts cannot repeat prop IDs: "
                + ", ".join(str(sequence) for sequence in scenes_with_duplicate_prop_ids)
            )
        # Per-scene targets are planning evidence, not an atomic accounting
        # invariant.  Incremental revision applies one scene patch at a time,
        # so a legitimate cross-scene reallocation can temporarily make their
        # sum drift from the episode target.  The semantic Verifier reviews the
        # completed revision; this model only enforces structural correctness
        # (including each scene target remaining positive).
        return self


def _with_outline_entity_ids(outline: EpisodeOutline) -> EpisodeOutline:
    """Fill missing structural IDs without inferring any creative relationship."""

    used_ids = {
        character.character_id
        for character in outline.characters
        if character.character_id is not None
    }
    characters: list[PlannedCharacter] = []
    next_sequence = 1
    for character in outline.characters:
        character_id = character.character_id
        if character_id is None:
            while f"character-{next_sequence:02d}" in used_ids:
                next_sequence += 1
            character_id = f"character-{next_sequence:02d}"
            used_ids.add(character_id)
            next_sequence += 1
        characters.append(
            character.model_copy(
                deep=True,
                update={"character_id": character_id},
            )
        )
    used_location_ids = {
        location.location_id
        for location in outline.locations
        if location.location_id is not None
    }
    locations: list[PlannedLocation] = []
    next_location_sequence = 1
    for location in outline.locations:
        location_id = location.location_id
        if location_id is None:
            while f"location-{next_location_sequence:02d}" in used_location_ids:
                next_location_sequence += 1
            location_id = f"location-{next_location_sequence:02d}"
            used_location_ids.add(location_id)
            next_location_sequence += 1
        locations.append(
            location.model_copy(deep=True, update={"location_id": location_id})
        )
    used_prop_ids = {
        prop.prop_id for prop in outline.props if prop.prop_id is not None
    }
    props: list[PlannedProp] = []
    next_prop_sequence = 1
    for prop in outline.props:
        prop_id = prop.prop_id
        if prop_id is None:
            while f"prop-{next_prop_sequence:02d}" in used_prop_ids:
                next_prop_sequence += 1
            prop_id = f"prop-{next_prop_sequence:02d}"
            used_prop_ids.add(prop_id)
            next_prop_sequence += 1
        props.append(prop.model_copy(deep=True, update={"prop_id": prop_id}))
    return EpisodeOutline.model_validate(
        {
            **outline.model_dump(mode="python"),
            "characters": [item.model_dump(mode="python") for item in characters],
            "locations": [item.model_dump(mode="python") for item in locations],
            "props": [item.model_dump(mode="python") for item in props],
        }
    )


class NarrativeSkillSelection(DomainModel):
    """One optional narrative method selected by the Story Orientation Agent."""

    skill_id: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1200)


class ProductionMethodSelection(DomainModel):
    """One optional production method selected by Story Orientation."""

    skill_id: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1200)


class StoryOrientation(DomainModel):
    """Semantic interpretation prepared before the concrete story blueprint."""

    source_contract: StorySourceContract
    story_intent: StoryIntent
    selected_narrative_skills: list[NarrativeSkillSelection] = Field(
        default_factory=list[NarrativeSkillSelection],
        max_length=24,
    )
    narrative_skill_decision: str = Field(min_length=1, max_length=2400)
    selected_method_skills: list[ProductionMethodSelection] = Field(
        default_factory=list[ProductionMethodSelection],
        max_length=24,
    )
    method_selection_rationale: str | None = Field(default=None, max_length=2400)

    @model_validator(mode="after")
    def validate_unique_skill_ids(self) -> StoryOrientation:
        skill_ids = [selection.skill_id for selection in self.selected_narrative_skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Story orientation repeats a narrative Skill id")
        method_ids = [selection.skill_id for selection in self.selected_method_skills]
        if len(method_ids) != len(set(method_ids)):
            raise ValueError("Story orientation repeats a Production Method Skill id")
        return self


class SceneShotPlan(DomainModel):
    scene_sequence: int = Field(ge=1)
    shots: list[PlannedShot] = Field(min_length=1, max_length=80)
    continuity_out: SceneContinuitySummary


class ProductionPlan(DomainModel):
    title: str = Field(min_length=1, max_length=120)
    scene_heading: str = Field(min_length=1, max_length=200)
    scene_summary: str = Field(min_length=1, max_length=2400)
    target_duration_seconds: float = Field(default=50.0, ge=4.0)
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=5000,
    )
    characters: list[PlannedCharacter] = Field(min_length=1, max_length=12)
    locations: list[PlannedLocation] = Field(min_length=1, max_length=20)
    props: list[PlannedProp] = Field(default_factory=list[PlannedProp], max_length=20)
    style: PlannedStyle
    ending_contract: EndingContract | None = None
    story_intent: StoryIntent | None = None
    content_planning_output: ContentPlanningOutput = (
        ContentPlanningOutput.SCRIPT_AND_SHOTS
    )
    scenes: list[PlannedSceneContract] = Field(
        default_factory=list[PlannedSceneContract],
    )
    shots: list[PlannedShot] = Field(default_factory=list[PlannedShot], max_length=500)

    @model_validator(mode="after")
    def validate_timeline(self) -> ProductionPlan:
        if self.content_planning_output == ContentPlanningOutput.SCRIPT_ONLY:
            if self.shots:
                raise ValueError("SCRIPT_ONLY production plans cannot contain shots")
        elif not self.shots:
            raise ValueError("SCRIPT_AND_SHOTS production plans require at least one shot")
        subject_names = [
            *(item.name for item in self.characters),
            *(item.name for item in self.locations),
            *(item.name for item in self.props),
        ]
        duplicate_subjects = sorted(
            name for name, count in Counter(subject_names).items() if count > 1
        )
        if duplicate_subjects:
            raise ValueError(
                "Character, location, and prop names must be globally unique: "
                + ", ".join(duplicate_subjects)
            )
        character_ids = [
            item.character_id
            for item in self.characters
            if item.character_id is not None
        ]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("Planned character IDs must be unique")
        location_ids = [
            item.location_id for item in self.locations if item.location_id is not None
        ]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("Planned location IDs must be unique")
        prop_ids = [item.prop_id for item in self.props if item.prop_id is not None]
        if len(prop_ids) != len(set(prop_ids)):
            raise ValueError("Planned prop IDs must be unique")
        known_character_ids = set(character_ids)
        unknown_speaker_ids = sorted(
            {
                line.speaker_character_id
                for line in self.lines
                if line.speaker_character_id is not None
                and line.speaker_character_id not in known_character_ids
            }
        )
        if unknown_speaker_ids:
            raise ValueError(
                "Dialogue lines reference unknown planned character IDs: "
                + ", ".join(unknown_speaker_ids)
            )
        line_count = len(self.lines)
        invalid = sorted(
            {
                index
                for shot in self.shots
                for index in shot.dialogue_line_indices
                if index < 1 or index > line_count
            }
        )
        if invalid:
            raise ValueError(f"Shot dialogue indices are out of range: {invalid}")
        shot_assignments = [
            index
            for shot in self.shots
            for index in shot.dialogue_line_indices
        ]
        expected_line_indices = set(range(1, line_count + 1))
        duplicated_shot_lines = sorted(
            index for index, count in Counter(shot_assignments).items() if count > 1
        )
        missing_shot_lines = sorted(expected_line_indices - set(shot_assignments))
        if self.shots and (duplicated_shot_lines or missing_shot_lines):
            raise ValueError(
                "Shots must use every declared dialogue line exactly once: "
                f"duplicates={duplicated_shot_lines}, missing={missing_shot_lines}"
            )
        known_subjects = (
            {item.name for item in self.characters}
            | {item.name for item in self.locations}
            | {item.name for item in self.props}
        )
        unknown_subjects = sorted(
            {
                need.subject_name
                for shot in self.shots
                for need in shot.reference_needs
                if need.subject_name not in known_subjects
            }
        )
        if unknown_subjects:
            raise ValueError(
                "Reference needs use unknown production subjects: "
                + ", ".join(unknown_subjects)
            )
        if self.scenes:
            sequences = [scene.sequence for scene in self.scenes]
            if sequences != list(range(1, len(self.scenes) + 1)):
                raise ValueError("Scene sequences must be contiguous and 1-based")
            scene_assignments = [
                index
                for scene in self.scenes
                for index in scene.dialogue_line_indices
            ]
            invalid_scene_lines = sorted(
                {
                    index
                    for index in scene_assignments
                    if index < 1 or index > line_count
                }
            )
            duplicated_scene_lines = sorted(
                index
                for index, count in Counter(scene_assignments).items()
                if count > 1
            )
            missing_scene_lines = sorted(expected_line_indices - set(scene_assignments))
            if invalid_scene_lines or duplicated_scene_lines or missing_scene_lines:
                raise ValueError(
                    "Scenes must assign every declared dialogue line exactly once: "
                    f"invalid={invalid_scene_lines}, duplicates={duplicated_scene_lines}, "
                    f"missing={missing_scene_lines}"
                )
            known_sequences = {scene.sequence for scene in self.scenes}
            invalid_scene_refs = sorted(
                {
                    shot.scene_sequence
                    for shot in self.shots
                    if shot.scene_sequence not in known_sequences
                }
            )
            if invalid_scene_refs:
                raise ValueError(
                    f"Shots reference unknown scene sequences: {invalid_scene_refs}"
                )
        return self


@dataclass(frozen=True)
class ProductionPlanningResult:
    plan: ProductionPlan
    planner: str
    warnings: list[str]
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    final_cold_read_report: ScriptReviewReport | None = None


@dataclass(frozen=True)
class StoryArchitectureResult:
    outline: EpisodeOutline
    source: str
    warnings: list[str]
    source_contract: StorySourceContract


@dataclass(frozen=True)
class StoryOrientationResult:
    orientation: StoryOrientation | None
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class ScriptCritiqueResult:
    report: ScriptReviewReport | None
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class ScriptReviewRoutingResult:
    disposition: ScriptReviewDisposition
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class RevisionJudgeResult:
    recommendation: RevisionJudgeRecommendation | None
    warnings: list[str]


@dataclass(frozen=True)
class StorySourceContractResult:
    contract: StorySourceContract
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryRepairPlanningResult:
    plan: StoryRepairPlan
    source_contract: StorySourceContract
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryMetadataRevisionResult:
    outline: EpisodeOutline
    patch: StoryMetadataRevisionPatch | None
    revised: bool
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryRevisionVerificationResult:
    verification: StoryRevisionVerification | None
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class SceneDirectionResult:
    scene: SceneShotPlan | None
    repaired: bool
    used_fallback: bool
    retry_required: bool
    retry_feedback: str | None
    warnings: list[str]


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


def _compact_error(error: BaseException, *, max_chars: int = 800) -> str:
    text = " ".join(str(error).split())
    lowered = text.lower()
    if "<!doctype html" in lowered or "<html" in lowered:
        title_start = lowered.find("<title>")
        title_end = lowered.find("</title>", title_start + len("<title>"))
        detail = (
            " ".join(text[title_start + len("<title>") : title_end].split())
            if title_start >= 0 and title_end > title_start
            else ""
        )
        text = "Provider returned an HTML error page"
        if detail:
            text += f": {detail}"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _compact_json(value: object) -> str:
    """Serialize hot-path prompt payloads without whitespace-only token overhead."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _planned_locked_dialogue(
    source_contract: StorySourceContract,
) -> list[PlannedDialogueLine]:
    """Project the intake Agent's explicit contract into planning records."""

    return [
        PlannedDialogueLine(
            speaker=line.speaker,
            text=line.text,
            audio_kind=line.audio_kind,
        )
        for line in source_contract.locked_dialogue
    ]


def _validate_locked_source_contract(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
) -> None:
    """Protect verbatim SCRIPT_LOCKED text without inferring dialogue by keywords."""

    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        return
    if request.locked_dialogue:
        requested = [line.model_dump(mode="python") for line in request.locked_dialogue]
        contracted = [
            line.model_dump(mode="python") for line in source_contract.locked_dialogue
        ]
        if contracted != requested:
            raise ValueError(
                "Story intake changed caller-provided structured locked dialogue"
            )
        return
    if not source_contract.locked_dialogue:
        raise ValueError(
            "SCRIPT_LOCKED requires the Story Orientation Agent to return structured "
            "locked_dialogue"
        )
    source = request.input_text
    cursor = 0
    for line in source_contract.locked_dialogue:
        position = source.find(line.text, cursor)
        if position < 0:
            raise ValueError(
                "Story intake changed, invented, or reordered locked dialogue text"
            )
        cursor = position + len(line.text)


def _planner_image_urls(request: CreateProjectRequest) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    warnings: list[str] = []
    for index, image in enumerate(request.input_images, start=1):
        if image.external_url is not None:
            urls.append(image.external_url)
            continue
        assert image.local_path is not None
        path = Path(image.local_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Input image does not exist: {path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            warnings.append(
                f"输入图片 {index} 超过 20 MB，已作为项目资产导入，但未发送给规划模型。"
            )
            continue
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            raise ValueError(f"Input seed is not an image: {path}")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        urls.append(f"data:{mime_type};base64,{encoded}")
    return urls, warnings


def _fallback_story_intent(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
) -> StoryIntent:
    """Preserve the user's premise without inventing a genre-specific recipe."""

    target = request.resolved_target_duration_seconds()
    return StoryIntent(
        audience_experience=(
            "让观众在完整观看过程中清楚感受到用户明确提出的核心创作承诺，"
            "并在结尾获得与本集形式相称的结果或余韵。"
        ),
        dramatic_engine=(
            "围绕用户输入中的核心人物、处境、变化或问题持续制造可见推进；"
            "具体冲突机制由故事 Agent 根据素材决定。"
        ),
        progression_strategy=(
            f"在约 {target:g} 秒内按当前故事真正需要的节奏展开，"
            "让每个场景都改变人物处境、观众理解或情绪状态，而不是套用固定节拍。"
        ),
        tone_and_plausibility=(
            "服从用户明确指定的调性与世界规则；未指定之处由 Agent 自主选择。"
            "可信度以本剧自身建立的规则和预期为准，不默认等同于现实主义。"
        ),
        protected_elements=list(source_contract.explicit_commitments),
        drift_risks=[
            "不要用通用类型惯例取代用户的核心命题。",
            "不要为了表面严谨而削弱本剧真正承诺的观看体验。",
        ],
        duration_contract=_fallback_duration_contract(request),
    )


def _fallback_duration_contract(request: CreateProjectRequest) -> DurationContract:
    """Keep compatibility deterministic without inventing a timing tolerance."""

    target_origin = (
        DurationTargetOrigin.USER_DECLARED
        if request.target_duration_seconds is not None
        else DurationTargetOrigin.PROFILE_DEFAULT
    )
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return DurationContract(
            target_seconds=request.resolved_target_duration_seconds(),
            target_origin=target_origin,
            authority=DurationAuthority.SOURCE_LED,
            acceptable_range_seconds=None,
            source_fidelity_policy=DurationFidelityPolicy.PRESERVE_VERBATIM,
            mismatch_resolution=DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
            decision_basis=(
                "本地兼容路径优先保留 SCRIPT_LOCKED 来源中的完整内容；"
                "目标秒数保留为参考，由后续 Agent 自然演读并报告错配。"
            ),
            rhythm_shape=[],
        )
    return DurationContract(
        target_seconds=request.resolved_target_duration_seconds(),
        target_origin=target_origin,
        authority=DurationAuthority.TARGET_LED,
        acceptable_range_seconds=None,
        source_fidelity_policy=(
            DurationFidelityPolicy.REWRITABLE
            if request.source_mode == SourceMode.AUTO_ORIGINAL
            else DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
        ),
        mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
        decision_basis=(
            "本地兼容路径将用户明确时长或格式默认值作为原创规模；"
            "不自动虚构可接受容差。"
        ),
        rhythm_shape=[],
    )


def _resolved_duration_contract(
    request: CreateProjectRequest,
    story_intent: StoryIntent | None,
) -> DurationContract:
    if story_intent is not None and story_intent.duration_contract is not None:
        return story_intent.duration_contract
    return _fallback_duration_contract(request)


def _unresolved_duration_fit_assessment(
    request: CreateProjectRequest,
    story_intent: StoryIntent | None,
    *,
    verifier_name: str,
) -> DurationFitAssessment:
    """Preserve a missing fresh Verifier judgment as an explicit Leader handoff."""

    contract = _resolved_duration_contract(request, story_intent)
    return DurationFitAssessment(
        estimated_range_seconds=None,
        alignment=DurationAlignment.UNRESOLVED,
        pressure_points=[],
        concurrent_performance=[],
        resolution=contract.mismatch_resolution,
        rationale=(
            f"{verifier_name} 未返回结构化时长演读结果；系统不用固定容差或"
            "确定性规则代替 Agent 判断，将本次时长判断显式交回 Leader，"
            "由 Leader 结合 DurationContract 与剧本文本证据决定后续。"
        ),
    )


def _fresh_story_orientation_schema() -> dict[str, object]:
    """Require new runs to persist timing intent while loading old records."""

    schema = StoryOrientation.model_json_schema()
    definitions = cast(dict[str, object], schema["$defs"])
    intent_schema = cast(dict[str, object], definitions["StoryIntent"])
    required = cast(list[str], intent_schema.setdefault("required", []))
    if "duration_contract" not in required:
        required.append("duration_contract")
    return schema


def _story_orientation_prompt(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
    retry_feedback: str | None = None,
) -> tuple[str, str]:
    profile_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.CONCEPT,),
    )
    catalog = narrative_skill_catalog()
    method_catalog = production_method_skill_catalog()
    instructions = (
        "你是 Story Orientation Agent（故事定向 Agent）。"
        "你在正式故事架构之前理解用户真正想看的体验，不写剧本、不拆场景、不写镜头。"
        "只返回 JSON，并严格匹配 StoryOrientation。"
        f"{_STORY_INTERACTION_PRIORITY}\n\n"
        "先把用户明确说出的承诺、禁止项、可自由创作部分与续集偏好整理进 "
        "source_contract；不得把你自己的创作选择伪装成用户要求。"
        "再用自然语言形成 story_intent：说清观众体验、戏剧发动机、推进方法、"
        "调性与本剧所需的可信度、必须保护的元素和最容易发生的创作漂移。"
        "StoryIntent 是后续 Agent 可讨论、可修订的创作方向，不是评分表或门禁。"
        "在 story_intent.duration_contract 中单独处理时长：区分用户明确提出的"
        " user_declared_target_duration_seconds 与 profile 仅供创作启动的"
        " working_target_duration_seconds，再根据完整需求、来源约束、主音轨与当前 Method"
        "语义判断 target_origin、authority、source_fidelity_policy 和 mismatch_resolution，"
        "不得用关键词或 source_mode 枚举代替这个判断。acceptable_range_seconds "
        "只在完整项目语义真正支持时填写：可以来自用户、来源或已定制作上下文，也可由你"
        "根据内容不确定性、媒介用途、来源保真与当前 Method 提出项目级范围，但必须在"
        "decision_basis 说明依据，不按固定百分比或全局默认自动造容差。"
        "decision_basis 还要说清时间权威与错配处理的语义依据。"
        "rhythm_shape 只在对创作有帮助时描述叙事功能、相邻段的相对密度与回报或交接；"
        "可根据题材合并、省略或改名，不把‘钩—缓—爽—顶—尾’变成固定五段、秒数、百分比或次数配额。"
        "不要默认现实主义，也不要因为题材夸张、荒诞、类型化就主动把它改得理性克制；"
        "同样不要把任何输入都强行改成爽剧、喜剧或高反转。\n\n"
        "若 source_mode 为 SCRIPT_LOCKED，你还负责把所有用户要求锁定的有声台词"
        "结构化写入 source_contract.locked_dialogue。speaker 与 audio_kind 依据完整语义判断，"
        "每条 text 必须逐字取自 user_brief 并保持顺序；不要用关键词、正则或固定标点格式"
        "代替语义理解。若调用方已经提供 caller_structured_locked_dialogue，必须原样保留。\n\n"
        "从 available_narrative_skills 中按完整语义选择真正需要的叙事方法；目录为空时"
        "selected_narrative_skills 保持为空，不得临时自创一个 Skill 冒充已收录能力。"
        "narrative_skill_decision 只记录本次是否需要目录中的叙事方法。该字段继续保留，"
        "用于兼容既有 ProjectDocument 和以后接入的新方法。\n\n"
        "同一次语义判断中，从 available_production_methods 选择零个、一个或多个"
        "真正能帮助这个项目的 Production Method。MiniMax Design 的 drama-series、"
        "ad-tvc、mv 提供项目级主 Workflow；MiniMax-H3 方法提供可组合的任务级表达方法。"
        "先判断当前项目是否需要一个项目 Workflow 作为主骨架，再按实际任务组合任务方法；"
        "这不是固定的一主多辅规则，也不限制选择数量。只根据整段需求、素材、已有工件和目标"
        "进行语义选择，不得用关键词匹配、题材枚举或固定类型路由。"
        "选中时在 selected_method_skills 中逐项说明理由；method_selection_rationale "
        "用于说明整体取舍，旧调用方没有这些字段时也可保持为空。\n\n"
        f"{profile_prompt.text}"
    )
    if retry_feedback:
        instructions += (
            " previous_structural_feedback 中可能包含 Scene Writer 根据实际扩写发现的"
            "定向偏差。请把它当作可追溯的创作证据，重新对照用户原意判断；"
            "不必然接受请求中的具体方案，但必须回应它指出的冲突。"
        )
    payload = {
        "source_mode": request.source_mode.value,
        "run_mode": request.run_mode.value,
        "production_profile": request.production_profile.value,
        "project_name": request.name,
        "user_brief": request.input_text.strip(),
        "caller_structured_locked_dialogue": [
            line.model_dump(mode="json") for line in request.locked_dialogue
        ],
        "user_declared_target_duration_seconds": request.target_duration_seconds,
        "working_target_duration_seconds": request.resolved_target_duration_seconds(),
        "lossless_source_contract": source_contract.model_dump(mode="json"),
        "image_references": [
            {
                "index": index,
                "descriptor": image.descriptor,
                "filename": Path(image.local_path).name if image.local_path else None,
                "url": image.external_url,
            }
            for index, image in enumerate(request.input_images, start=1)
        ],
        "available_narrative_skills": [
            {
                "id": descriptor.id,
                "label": descriptor.label,
                "description": descriptor.description,
                "supported_roles": [role.value for role in descriptor.supported_roles],
                "user_overridable": descriptor.user_overridable,
            }
            for descriptor in catalog
        ],
        "available_production_methods": [
            {
                "id": descriptor.id,
                "label": descriptor.label,
                "description": descriptor.description,
                "supported_roles": [role.value for role in descriptor.supported_roles],
                "user_overridable": descriptor.user_overridable,
            }
            for descriptor in method_catalog
        ],
        "previous_structural_feedback": retry_feedback,
        "output_schema": _fresh_story_orientation_schema(),
    }
    return instructions, _compact_json(payload)


def orient_story(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
    *,
    retry_feedback: str | None = None,
) -> StoryOrientationResult:
    """Interpret one story and let the Agent select optional narrative methods."""

    source_contract = build_story_source_contract(request)
    if client is None:
        return StoryOrientationResult(
            orientation=StoryOrientation(
                source_contract=source_contract,
                story_intent=_fallback_story_intent(request, source_contract),
                selected_narrative_skills=[],
                narrative_skill_decision=(
                    "当前内置 Narrative Skill Catalog 为空；"
                    "本地兼容路径未虚构叙事方法。"
                ),
                selected_method_skills=[],
                method_selection_rationale=(
                    "未配置 Story Orientation Agent；本地兼容路径不替 Agent 选择"
                    "MiniMax Design Workflow 或 MiniMax-H3 任务方法。"
                ),
            ),
            used=False,
            warnings=[
                "Story Orientation Agent 未配置；已保留用户原始承诺，"
                "且未由确定性代码猜测应采用的叙事或制片方法。"
            ],
        )
    instructions, input_text = _story_orientation_prompt(
        request,
        source_contract,
        retry_feedback,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=7000,
        )
        orientation = StoryOrientation.model_validate_json(_strip_json_fence(output))
        warnings: list[str] = []
        if orientation.story_intent.duration_contract is None:
            orientation = orientation.model_copy(
                deep=True,
                update={
                    "story_intent": orientation.story_intent.model_copy(
                        deep=True,
                        update={
                            "duration_contract": _fallback_duration_contract(request)
                        },
                    )
                },
            )
            warnings.append(
                "Story Orientation Agent 返回了旧版合法结构，但未包含 "
                "story_intent.duration_contract；已注入不虚构固定容差的"
                "兼容 DurationContract，其他字段仍按新结构正常验证。"
            )
        _validate_locked_source_contract(request, orientation.source_contract)
        known_skill_ids = {descriptor.id for descriptor in narrative_skill_catalog()}
        unknown_skill_ids = sorted(
            {
                selection.skill_id
                for selection in orientation.selected_narrative_skills
                if selection.skill_id not in known_skill_ids
            }
        )
        if unknown_skill_ids:
            raise ValueError(
                "Story Orientation selected unknown narrative Skills: "
                + ", ".join(unknown_skill_ids)
            )
        known_method_ids = {
            descriptor.id for descriptor in production_method_skill_catalog()
        }
        unknown_method_ids = sorted(
            {
                selection.skill_id
                for selection in orientation.selected_method_skills
                if selection.skill_id not in known_method_ids
            }
        )
        if unknown_method_ids:
            raise ValueError(
                "Story Orientation selected unknown Production Method Skills: "
                + ", ".join(unknown_method_ids)
            )
        return StoryOrientationResult(
            orientation=orientation,
            used=True,
            warnings=warnings,
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryOrientationResult(
            orientation=None,
            used=False,
            warnings=[
                "Story Orientation Agent 本次未形成可采用的语义定向；"
                "保留当前游标，等待下一次 planning.advance 重试："
                + _compact_error(error)
            ],
        )


def _outline_prompt(
    request: CreateProjectRequest,
    source_contract: StorySourceContract | None = None,
    story_intent: StoryIntent | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
    revision_instruction: str | None = None,
    current_outline: EpisodeOutline | None = None,
) -> tuple[str, str]:
    resolved_source_contract = source_contract or build_story_source_contract(request)
    resolved_story_intent = story_intent or _fallback_story_intent(
        request,
        resolved_source_contract,
    )
    duration_contract = _resolved_duration_contract(request, resolved_story_intent)
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.CONCEPT,),
        additional_skill_ids=selected_narrative_skill_ids,
        method_skill_ids=selected_method_skill_ids,
    )
    locked = (
        _planned_locked_dialogue(resolved_source_contract)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    output_schema = EpisodeOutline.model_json_schema()
    schema_definitions = cast(dict[str, object], output_schema["$defs"])
    scene_schema = cast(
        dict[str, object],
        schema_definitions["PlannedSceneContract"],
    )
    scene_properties = cast(dict[str, object], scene_schema["properties"])
    scene_properties.pop("screenplay_text", None)
    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        outline_properties = cast(
            dict[str, object],
            output_schema["properties"],
        )
        outline_properties.pop("lines", None)
        scene_properties.pop("dialogue_line_indices", None)
    instructions = (
        "你是 story-architect Agent（故事架构 Agent），负责当前视频作品的通用叙事架构。"
        "只返回 JSON，并严格匹配 EpisodeOutline。你要输出紧凑的全局蓝图，而不是完整剧本："
        "确定本集故事、人物、可复用场景与道具，再拆成下游 Scene Writer 可逐场展开的场景节点。"
        "不要写镜头、机位、关键帧或 Provider 提示词。\n\n"
        f"{skill_prompt.text}\n\n"
        "以已确定的 StoryIntent 为本剧创作方向，不得把共享职责或格式惯例当成题材答案。"
        "先稳定主导类型、主角行动路线与核心情绪，再让开篇尽快出现有冲击力的局面或台词，"
        "使观众理解核心冲突、主角处境和继续看下去的动力。爽点、情感释放或反转都应从前面的"
        "铺垫生长出来；决定性回报优先由主角可见的选择、行动或能力触发，并让对立力量的受挫、"
        "改口或后果在剧中可见。夸张、荒诞、无厘头、类型化跳跃以及为了升级惊喜、压力或喜剧效果而设计的"
        "重复都可以成立；只要观众能理解当前局面与结果，不必把类型爽感磨平成现实流程。"
        "场景数量、长短、转折密度与解释程度由素材决定，不套固定节拍或固定反转数。"
        "每个场景节点只需写清叙事作用、地点、少量可表演动作和下游必须承接的紧凑连续性。"
        "观众必须理解的推进应落到可表演动作、道具、界面或对白中，而不能只藏在元数据里。"
        "为每个常驻角色提供稳定 character_id，为每个地点和道具分别提供稳定 "
        "location_id 和 prop_id。每个场景的 prop_ids 只引用本场实际使用的已声明道具；"
        "道具的稳定外观留在资产描述，本场用法、状态和观众必须看懂的因果"
        "必须写进 playable_actions 或 continuity，不得让下游从全项目道具中猜测。"
        "SCRIPT_LOCKED 的对白若属于该角色，"
        "用 speaker_character_id 显式引用该 ID。旁白、环境声或未建模说话者可以不绑定，"
        "不要让下游根据说话人名称反猜角色。"
        "先读取 StoryIntent.duration_contract，再确定作品的内容规模、场景数量和每场"
        " target_duration_seconds envelope。TARGET_LED 项目若在蓝图阶段已能看出事件、动作"
        "与对白不可能在契约尺度内自然完成，现在就重做场次与因果蓝图，不把超载"
        "留给 Writer 或分镜。SOURCE_LED 项目按来源保真策略保留事实源并容纳自然演读"
        "时长；AUDIO_LED 项目服从已定主音轨。rhythm_shape 是可合并、可省略的相对叙事"
        "功能，不是固定五段或配额。场景时长分配是内容 envelope，时长算术本身不代表"
        "可表演内容成立。\n\n"
        "在逐场写作前建立 ending_contract。只按用户原意判断续集偏好："
        "用户明确要求完整无续集时才是 STANDALONE，明确要求延续时才是 SERIES，"
        "否则为 AGENT_DECIDES，并由你根据这一个故事自主选择、说明依据。"
        "无论哪种模式，本集都必须兑现 episode_payoff，并用真实可见的动作、反应、画面或对白"
        "形成 final_button；SERIES 可以在本集兑现后保留声明过的延续问题，"
        "STANDALONE 则关闭核心因果与情感任务，不保留续集钩子。\n\n"
        "EndingContract 描述本集最终必须到达的状态；将相互重叠的 closure 收束在最有戏剧作用的"
        "决定性行动和反应中，不把结尾写成事务清单或产品流程。\n\n"
        "可复用资产描述只写稳定身份和稳定外观；临时状态、用途和剧情意义写入场景动作与连续性。"
        "蓝图不返回 screenplay_text；最终动作与对白由 Scene Writer 逐场创作。"
        "AUTO_ORIGINAL 与 IDEA_EXPANSION 不返回 lines 或 dialogue_line_indices。"
        "SCRIPT_LOCKED 必须逐字、逐序保留 Story Orientation Agent 已结构化的 speaker、"
        "text 与 audio_kind，并把每个从 1 开始的行号恰好分配到一个场景；"
        "不要重新从原文猜测说话人。"
        "只能引用已声明的地点名称，不添加 schema 之外的字段。"
    )
    if retry_feedback:
        instructions += (
            " previous_structural_feedback 可能是上次结构输出的校验反馈，也可能是 "
            "Scene Writer 基于实际扩写证据提交的上游重规划请求。请根据其完整"
            "语义修正相应层级，保留其明确列出的用户承诺和已成立内容。"
        )
    if revision_instruction is not None:
        instructions += (
            " 这是已有完整剧本的结构级修订。将 latest_user_revision_instruction "
            "视为本轮权威要求，同时保留 original user brief 中未被新意见"
            "明确覆盖的承诺。先读 current_complete_outline，再只重构确实需要改变的"
            "全局故事、场次、角色或地点契约；不要把修订误当成从零创作。"
        )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "project_name": request.name,
        "user_brief": request.input_text.strip(),
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "user_declared_target_duration_seconds": request.target_duration_seconds,
        "working_target_duration_seconds": request.resolved_target_duration_seconds(),
        "duration_contract": duration_contract.model_dump(mode="json"),
        "story_source_contract": resolved_source_contract.model_dump(mode="json"),
        "story_intent": resolved_story_intent.model_dump(mode="json"),
        "selected_narrative_skill_ids": list(selected_narrative_skill_ids),
        "selected_method_skill_ids": list(selected_method_skill_ids),
        "previous_structural_feedback": retry_feedback,
        "latest_user_revision_instruction": revision_instruction,
        "current_complete_outline": (
            current_outline.model_dump(mode="json")
            if current_outline is not None
            else None
        ),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "image_references": [
            {
                "index": index,
                "descriptor": image.descriptor,
                "filename": Path(image.local_path).name if image.local_path else None,
                "url": image.external_url,
            }
            for index, image in enumerate(request.input_images, start=1)
        ],
        "handoff_contract": {
            "story_architect_owns": "story, assets, scenes, dialogue allocation, expected states",
            "scene_director_owns": "shot execution inside one scene",
            "continuity": "compact state summaries, not accumulated prior-shot prose",
        },
        "output_schema": output_schema,
    }
    return instructions, _compact_json(payload)


def _story_critic_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    *,
    source_contract: StorySourceContract | None = None,
    issue_ledger: list[StoryIssueLedgerEntry] | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> tuple[str, str]:
    duration_contract = _resolved_duration_contract(request, outline.story_intent)
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
        include_constitution=False,
        additional_skill_ids=selected_narrative_skill_ids,
        method_skill_ids=selected_method_skill_ids,
    )
    instructions = (
        "你是这个 Leader / Worker 链路中唯一的 Script Verifier Agent，在分镜前以目标观众的身份阅读整部剧本。"
        "只返回 JSON，并严格匹配 ScriptReviewReport。你的第一判断依据是 StoryIntent 与用户明确承诺，"
        "不是隐藏答案、通用编剧教科书或你个人偏好的现实主义。先判断作品是否真的提供了它承诺的"
        "观看体验、戏剧推进、情绪释放和结尾效果，再指出有文本证据、且确实值得修复的问题。\n\n"
        "整稿只聚焦五件事：开篇是否迅速给出吸引人的局面或台词；主角的处境、目标和行动路线是否清楚；"
        "铺垫是否真正转化为爽点、情绪释放或有效反转；对立力量的反应、受挫或反噬是否可见；结尾是否完成"
        "EndingContract 承诺的本集回报。主角的决定性胜利应能看到其自身选择、行动或能力的作用；外部权威可以确认结果，"
        "但不应成为唯一的戏剧发动机。\n\n"
        "逻辑只服务于观众理解和本剧自身规则：夸张、荒诞、类型化跳跃、留白和无厘头并不天然是缺陷。"
        "只有观众已经无法理解当前行动与结果，或文本违反了自己明确建立的规则时，才把逻辑列为需要修复的问题。"
        "不要因局部现实性不足、升级过快或重复结构本身否决类型效果，也不假定所有作品都需要更多解释、场景或反转。"
        "在同一次通读中按 DurationContract 的时间权威完成自然演读，并必须填写"
        " duration_fit_assessment：TARGET_LED 原创稿要判断是否仍处于契约尺度，并将可聚焦修复"
        "的压力点落到准确场次；SOURCE_LED 且未授权缩写时，报告自然时长而不把超出"
        "参考目标误判为剧本缺陷；AUDIO_LED 服从主音轨。估算要包含对白、动作、停顿、"
        "反应及明确同时发生的表演，不用字数公式、固定容差或逐句账本代替语义判断。"
        "同时判断 rhythm_shape 声明的相对密度与叙事功能是否真正落地，不检查固定五段、"
        "固定位置或固定次数。旧的 performance_timing_assessment 可仅作兼容摘要。"
        "每个问题写明具体文本证据、受影响场次和最小修复建议；"
        "severity 只描述影响，不触发代码阈值。\n\n"
        "为每个问题提供稳定 issue_id。参考 issue_ledger：语义上是同一个问题时沿用原 ID，并在 observation"
        "中说明它是持续存在、变形、回归还是进一步细化；确实不同的问题再建立新 ID。"
        "已经作为创作取舍被接受的问题，没有新的文本证据时不要反复打开。"
        "owner=STORY_ARCHITECT 只用于剧本文本、人物选择、因果、兑现或可表演内容；"
        "表演、资产、视频生成和剪辑问题交给对应下游 owner，不要靠给剧本堆解释来修复。\n\n"
        "requires_revision 由你对整部作品的语义判断决定：只有当前剧本文本确实需要一次聚焦修改，"
        "才能作为用户要求的本集成立时才设为 true。SCRIPT_LOCKED 中不得建议改写用户锁定对白。"
        "ScriptReviewReport 本身就是独立交付，不要求把审校附录写进场景。"
        "你已经完成了整稿冷读：若仍有具体疑点，就将它写成有文本证据的问题并语义决定"
        "是否修订；若已无需修订，recommended_followup 选择 FINALIZE，不再把同一份稿件交给另一个"
        "独立审校角色重复阅读。"
        "用 followup_rationale 说明收尾判断的文本依据。"
        "ending_contract 也按本剧选择判断：STANDALONE 完成本集核心任务；SERIES 先兑现本集，再保留"
        "有依据的延续问题。结尾要看实际可读剧本中的最后动作、反应、画面或台词，而不是元数据自称完成。\n\n"
        f"{skill_prompt.text}"
    )
    resolved_source_contract = source_contract or build_story_source_contract(request)
    payload = {
        "request_context": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "user_declared_target_duration_seconds": request.target_duration_seconds,
            "source_text": request.input_text.strip(),
        },
        "duration_contract": duration_contract.model_dump(mode="json"),
        "source_contract": resolved_source_contract.model_dump(mode="json"),
        "ending_contract": (
            outline.ending_contract.model_dump(mode="json")
            if outline.ending_contract is not None
            else None
        ),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "issue_ledger": [
            entry.model_dump(mode="json") for entry in (issue_ledger or [])
        ],
        "previous_structural_feedback": retry_feedback,
        "readable_screenplay": [
            {
                "scene_sequence": scene.sequence,
                "heading": scene.heading,
                "target_duration_seconds": scene.target_duration_seconds,
                "screenplay_text": scene.screenplay_text,
            }
            for scene in outline.scenes
        ],
        "output_schema": _fresh_script_review_schema(),
    }
    return instructions, _compact_json(payload)


def _normalize_review_report(
    report: ScriptReviewReport,
    *,
    force_story_owner: bool = False,
) -> ScriptReviewReport:
    _validate_review_report_semantics(report)
    missing_ids = [index for index, issue in enumerate(report.issues, start=1) if issue.issue_id is None]
    if missing_ids:
        raise ValueError(
            "Script Verifier must provide a semantic issue_id for every issue: "
            + ", ".join(str(index) for index in missing_ids)
        )
    issues = [
        issue.model_copy(
            deep=True,
            update=(
                {"owner": ReviewRepairOwner.STORY_ARCHITECT}
                if force_story_owner
                else {}
            ),
        )
        for issue in report.issues
    ]
    return report.model_copy(
        deep=True,
        update={
            "issues": issues,
        },
    )


def _validate_review_report_semantics(report: ScriptReviewReport) -> None:
    if report.requires_revision and not any(
        issue.owner == ReviewRepairOwner.STORY_ARCHITECT
        for issue in report.issues
    ):
        raise ValueError(
            "Script Verifier set requires_revision=true without a "
            "STORY_ARCHITECT issue describing what should change"
        )


def _fresh_script_review_schema() -> dict[str, object]:
    """Require issue identity without making optional review notes mandatory."""

    schema = ScriptReviewReport.model_json_schema()
    definitions = cast(dict[str, object], schema["$defs"])
    issue_schema = cast(dict[str, object], definitions["ScriptReviewIssue"])
    required = cast(list[str], issue_schema.setdefault("required", []))
    if "issue_id" not in required:
        required.append("issue_id")
    report_required = cast(list[str], schema.setdefault("required", []))
    if "duration_fit_assessment" not in report_required:
        report_required.append("duration_fit_assessment")
    return schema


def _final_cold_read_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    *,
    source_contract: StorySourceContract | None = None,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.FINAL_COLD_READ,),
        include_constitution=False,
    )
    instructions = (
        "你是独立的 final cold-read Agent（最终冷读编辑）。"
        "只阅读用户原始需求、StoryIntent、ending_contract、人物名字和真正可读的 screenplay；"
        "不要相信 summary、narrative_purpose、连续性元数据或上一位审校者自称已经解决。"
        "只返回 JSON，并严格匹配 ScriptReviewReport。\n\n"
        "判断整部作品是否真的兑现目标观众体验、本集释放和最后一个可见戏剧动作，"
        "以及修订是否误删了铺垫、兑现、人物声音、情绪释放或结尾停顿。"
        "行政指令、索要文件、事务清单或单纯安排下一步，不能独自充当结尾，除非此前可见结果已经赋予"
        "它明确的戏剧意义。STANDALONE 应完成核心任务；SERIES 也必须先完成本集兑现，再给延续钩子。\n\n"
        "只报告仍需修改剧本文本的问题，owner 固定为 STORY_ARCHITECT，并写准确场次。"
        "这是脱离前一轮审校框架的独立阅读；不要猜测或复用旧问题，发现问题时按本次文本语义给出稳定 issue_id。"
        "requires_revision 来自完整阅读判断，不使用严重度、问题数量或关键词门槛。"
        "填写 performance_timing_assessment，按自然语速、动作、停顿和反应完整通读实际 screenplay。"
        "除非文字明确写成同时发生、蒙太奇或可连续掠过，否则按成稿顺序占用时间，不要替剧本假设不存在的"
        "快切；分别给出各场可信范围、全剧范围和主要压力点，并说明哪些内容确实可以同时表演。不得根据场次"
        "秒数标签推断合时长；‘约’允许自然浮动，但不能把明显属于另一时长尺度的成稿称为可执行。该字段只"
        "提供语义证据，不触发代码阈值。"
        "同时从第一场起承接人物关系、群体态度、资源与权限：逐场问本场究竟改变了什么。后续若仅再次表演"
        "已经成立的震惊、认可、让位、授予、交接或庆功，而没有新的选择、代价或后果，应作为具体文本问题；"
        "不能因为换了道具或把口头承诺改成文件、挂牌就自动视为新回报。"
        "夸张、荒诞、类型化或不追求现实解释本身不是问题，除非它违背本剧 StoryIntent 或自身规则。\n\n"
        f"{skill_prompt.text}"
    )
    resolved_source_contract = source_contract or build_story_source_contract(request)
    payload = {
        "user_brief": request.input_text.strip(),
        "source_contract": resolved_source_contract.model_dump(mode="json"),
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "ending_contract": (
            outline.ending_contract.model_dump(mode="json")
            if outline.ending_contract is not None
            else None
        ),
        "character_names": [character.name for character in outline.characters],
        "screenplay": [
            {
                "scene_sequence": scene.sequence,
                "heading": scene.heading,
                "screenplay_text": scene.screenplay_text,
            }
            for scene in outline.scenes
        ],
        "output_schema": _fresh_script_review_schema(),
    }
    return instructions, _compact_json(payload)


def final_cold_read_story(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    client: LanguageProviderClient | None,
    *,
    source_contract: StorySourceContract | None = None,
) -> ScriptCritiqueResult:
    """Run one metadata-blind final read before the screenplay becomes canonical."""

    if client is None:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=["GPT-5.6-sol 未配置，已跳过语义最终冷读。"],
        )
    instructions, input_text = _final_cold_read_prompt(
        request,
        outline,
        source_contract=source_contract,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=3500,
        )
        report = ScriptReviewReport.model_validate_json(_strip_json_fence(output))
        report = _normalize_review_report(
            report,
            force_story_owner=True,
        )
        return ScriptCritiqueResult(report=report, used=True, warnings=[])
    except (LanguageProviderError, ValueError) as error:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=["最终冷读不可用，已保留当前剧本等待同一步重试：" + _compact_error(error)],
        )


def _revision_judge_prompt(
    request: CreateProjectRequest,
    *,
    older_revision_round: int,
    older_outline: EpisodeOutline,
    older_review: ScriptReviewReport,
    newer_revision_round: int,
    newer_outline: EpisodeOutline,
    newer_review: ScriptReviewReport,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
    )
    instructions = (
        "你是独立的 revision-judge Agent，负责比较同一作品的两个相邻剧本版本。"
        "只返回 JSON，并严格匹配 RevisionJudgeRecommendation。在两个候选中推荐一个，"
        "并把另一个列为备选；不改写任何版本，也不默认新版一定更好。问题数量和严重度只是证据，"
        "不是评分或门禁。阅读实际剧本与各自的审校报告，比较哪个版本更忠于用户承诺与 "
        "StoryIntent，更好地完成人物行动、观众理解、情绪回报和本集结尾，以及修订是否引入"
        "新的矛盾或磨掉原有趣味。可信度以当前作品建立的调性和规则为准，不默认现实主义、"
        "技术程序或制度细节天然优先。给出具体文本证据和有意义的取舍。advisory_only 必须为 true；"
        "建议不得阻断规划，也不得静默替换用户当前版本。"
        f"{skill_prompt.text}"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "candidates": [
            {
                "revision_round": older_revision_round,
                "outline": older_outline.model_dump(mode="json"),
                "script_review": older_review.model_dump(mode="json"),
            },
            {
                "revision_round": newer_revision_round,
                "outline": newer_outline.model_dump(mode="json"),
                "script_review": newer_review.model_dump(mode="json"),
            },
        ],
        "output_schema": RevisionJudgeRecommendation.model_json_schema(),
    }
    return instructions, _compact_json(payload)


def story_revision_history_entry(
    *,
    revision_round: int,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
) -> dict[str, object]:
    """Build compact cross-round context without replaying every planning artifact."""

    return {
        "revision_round": revision_round,
        "title": outline.title,
        "story_summary": outline.story_summary,
        "scenes": [
            {
                "sequence": scene.sequence,
                "summary": scene.summary,
                "narrative_purpose": scene.narrative_purpose,
            }
            for scene in outline.scenes
        ],
        "critic_summary": review.summary,
        "critic_strengths": list(review.strengths),
        "critic_issues": [issue.model_dump(mode="json") for issue in review.issues],
    }


def _story_source_contract_prompt(
    request: CreateProjectRequest,
) -> tuple[str, str]:
    profile = production_skill(request.production_profile)
    instructions = (
        "你是 source-contract Agent，只负责从用户输入中提取明确承诺，不创作、不润色剧本。"
        "只返回 JSON，并严格匹配 StorySourceContract。区分用户明确要求、明确禁止和仍可自由选择的事项；"
        f"{_STORY_INTERACTION_PRIORITY} source-contract Agent 本身不创建暂停或确认步骤；"
        "只将用户明确指定的确认节点作为承诺完整保留。"
        "保留用户说出的角色数量、人物名称、必要动作、先后因果、伦理约束、题材、时长、冲突规模、"
        "要求的结果与续集要求，不得把明确事件降级成建议，也不得从类型惯例或 Production Skill"
        "中发明用户承诺。若两项原始要求确实冲突，同时保留并把如何协调列为开放创作选择。"
        "这是首次编译，authoritative_user_overrides 必须为空，user_overridable 必须为 true。"
        "SCRIPT_LOCKED 时还要把每一句用户锁定的有声台词整理进 locked_dialogue："
        "speaker 和 audio_kind 由你根据完整语义判断，text 必须逐字取自 source_text，"
        "并保持原始先后顺序；不要用关键词或标点模板猜测。非 SCRIPT_LOCKED 时保持为空。"
        "只有用户明确要求单集完整、无续集或不留开放钩子时才设 STANDALONE；"
        "明确要求续集、系列或下一集钩子时才设 SERIES；其他情况为 AGENT_DECIDES。"
        f"当前宽粒度制片格式为：{profile.label}，它不能替用户决定承诺。"
    )
    payload = {
        "source_mode": request.source_mode.value,
        "run_mode": request.run_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "caller_structured_locked_dialogue": [
            line.model_dump(mode="json") for line in request.locked_dialogue
        ],
        "input_image_count": len(request.input_images),
        "output_schema": StorySourceContract.model_json_schema(),
    }
    return instructions, _compact_json(payload)


def _fallback_story_source_contract(
    request: CreateProjectRequest,
) -> StorySourceContract:
    source = request.input_text.strip()
    if source:
        commitments = [source]
    else:
        commitments = [
            f"创作一个约 {request.resolved_target_duration_seconds():g} 秒的"
            f"{request.production_profile.value} 原创视频作品。"
        ]
    return StorySourceContract(
        explicit_commitments=commitments,
        explicit_prohibitions=[],
        open_creative_choices=[
            "用户没有明确固定的故事细节均由 Agent 自主创作，并可被用户修改。"
        ],
        locked_dialogue=[line.model_copy(deep=True) for line in request.locked_dialogue],
        continuation_preference=ContinuationPreference.AGENT_DECIDES,
    )


def build_story_source_contract(request: CreateProjectRequest) -> StorySourceContract:
    """Compile the lossless minimum contract without adding a mandatory LLM call."""

    return _fallback_story_source_contract(request)


def compile_story_source_contract(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> StorySourceContractResult:
    """Extract explicit user promises independently from Writer and Verifier."""

    fallback = _fallback_story_source_contract(request)
    if client is None:
        return StorySourceContractResult(
            contract=fallback,
            used=False,
            warnings=["Source Contract Agent 未配置，已把原始输入整体作为显式承诺。"],
        )
    instructions, input_text = _story_source_contract_prompt(request)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=5000,
        )
        contract = StorySourceContract.model_validate_json(_strip_json_fence(output))
        _validate_locked_source_contract(request, contract)
        return StorySourceContractResult(
            contract=contract,
            used=True,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return StorySourceContractResult(
            contract=fallback,
            used=False,
            warnings=[
                "Source Contract Agent 不可用，已把原始输入整体作为显式承诺："
                + _compact_error(error)
            ],
        )


def _story_repair_plan_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    source_contract: StorySourceContract,
    revision_history: list[dict[str, object]] | None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> tuple[str, str]:
    instructions = (
        "你正在执行 Leader 的返修分派步骤，把 Verifier 的具体问题退回原 Writer Worker。"
        "只返回 JSON，并严格匹配 StoryRepairPlan；你只制定成组修复方案，不直接重写整集。"
        "不要把 Verifier 当答案，逐项对照用户 SourceContract、StoryIntent、当前剧本和历史版本重新诊断。"
        "当 routed issue 来自用户对当前版本的直接修改意见时标为 USER_REQUEST；"
        "它是本轮权威目标，不是 Critic 缺陷，后来的 authoritative_user_override 优先于"
        "与其冲突的旧创作选择。你仍需根据完整语义决定最小连贯范围：少数现有场可完成时列出"
        "具体 affected_scene_sequences；需要修改全部现有场但不改变全局结构时列出全部场次；"
        "只有必须增删重排场次、改变全局角色地点契约或重建主要因果结构时才不列场次，"
        "将任务交回 Story Architect。"
        "输入前提本身缺少关键选择时标为 SOURCE_CONCEPT_GAP；当前文本制造了矛盾、动机断裂、"
        "负担过重或修订回归时标为 OUTLINE_DEFECT；缺少文本证据，或实际属于表演、资产、生成、"
        "镜头、剪辑的问题标为 CRITIC_OVERREACH，并设 should_revise=false。"
        "DURATION_CONTRACT 类问题是 Leader 根据精确 ScriptVersion、ProductionSpec 与"
        "StoryboardDraft 形成的版本化制片约束交接，不是普通 Critic 意见。应结合其中的"
        "自然实算、alignment 和用户时长权威重新判断：若 StoryIntent 契约缺少使 alignment"
        "可判定的字段，或完整剧本密度与 TARGET_LED 明确冲突，必须形成可执行的语义修复；"
        "仅修契约时设置 should_revise=true、metadata_targets=[STORY_INTENT] 且不虚构场次改写，"
        "确需压缩或重分配剧情时再列受影响场次。只有版本化证据已经证明问题解决时，才可"
        "判为无需修订；不得仅因它不是新的用户反馈而归类为 CRITIC_OVERREACH。"
        "每个 routed issue 恰好诊断一次，但多个相关问题可以组织成同一组连贯修复。\n\n"
        "每个 action 必须原样引用 routed issue 的稳定 issue_id；issue_index 只是旧记录"
        "兼容字段，不能用它取代语义身份。"
        "affected_scene_sequences 与 metadata_targets 是正交的语义分派：前者表示需要 Writer "
        "修改的具体场次，后者表示还必须同步 TITLE、STORY_SUMMARY、STORY_INTENT、"
        "ENDING_CONTRACT 或 PROJECT_BIBLE。只改全局元数据时允许不列场次但必须列出 "
        "metadata_targets；场次和元数据都要改时两者都列。只有必须增删重排场次、改变"
        "全局角色地点身份契约或重建主要因果结构，且不是现有元数据字段即可表达时，才让"
        "场次和 metadata_targets 同时为空并交回 Story Architect。"
        "保护用户明确承诺、StoryIntent 和已经成立的优点；不能为了省制作成本删减用户承诺。"
        "后来的 authoritative_user_override 优先，不能偷偷回滚用户已经编辑的版本。"
        "审校报告是独立工件，不要求把审校文字或附录塞进故事。"
        "建议足以解决问题的最小连贯修改，优先替换、删除、压缩或重新分配已有内容。若问题涉及重复证明、"
        "反应或权力交接，应明确哪一次承担真正的状态变化、其余位置删除、压成过渡还是改为新的实际后果，"
        "不要只写笼统的‘精简’或‘合并’。"
        "避免为了显得严谨继续堆解释、规则和便利设定；但若本剧 StoryIntent 本就需要夸张、荒诞、"
        "快速兑现或无厘头，不要用现实主义标准把它磨平。修复计划停留在命题、场景、动作、对白"
        "与时长分配层，不写镜头。user_overridable 必须为 true。"
        "只有修复确实需要场景间移动时长时才设 target_duration_change_required=true，"
        "并列出所有提供或接收时长的场景。"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "current_outline": outline.model_dump(mode="json"),
        "routed_story_review": review.model_dump(mode="json"),
        "selected_narrative_skill_ids": list(selected_narrative_skill_ids),
        "selected_official_method_skill_ids": list(selected_method_skill_ids),
        "prior_revision_history": (revision_history or [])[-3:],
        "previous_structural_feedback": retry_feedback,
        "output_schema": _fresh_story_repair_plan_schema(),
    }
    return instructions, _compact_json(payload)


def _fallback_story_repair_plan(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
) -> StoryRepairPlan:
    source_promise = request.input_text.strip() or outline.story_summary
    return StoryRepairPlan(
        summary=(
            "Leader 未取得新的语义返修方案；按 Verifier 已明确归属给 "
            "Writer 的问题形成最小修订计划。"
        ),
        source_promise=source_promise[:1200],
        invariants=list(review.strengths),
        actions=[
            StoryRepairAction(
                issue_index=index,
                issue_id=issue.issue_id,
                diagnosis=(
                    StoryRepairDiagnosis.USER_REQUEST
                    if issue.category == "USER_REQUEST"
                    else StoryRepairDiagnosis.OUTLINE_DEFECT
                ),
                should_revise=True,
                rationale=(
                    "该问题已由 Verifier 归属到 Writer，"
                    "但未完成独立来源诊断。"
                ),
                affected_scene_sequences=list(issue.scene_sequences),
                recommended_change=issue.recommendation,
                remove_or_simplify=[],
                preserve=list(review.strengths),
                validation_questions=[f"修订后是否直接消除了该观察：{issue.observation}"],
            )
            for index, issue in enumerate(review.issues, start=1)
        ],
    )


def _fresh_story_repair_plan_schema() -> dict[str, object]:
    """Require stable IDs from new Agent output without breaking stored plans."""

    schema = StoryRepairPlan.model_json_schema()
    definitions = cast(dict[str, object], schema["$defs"])
    action_schema = cast(dict[str, object], definitions["StoryRepairAction"])
    required = cast(list[str], action_schema.setdefault("required", []))
    if "issue_id" not in required:
        required.append("issue_id")
    if "metadata_targets" not in required:
        required.append("metadata_targets")
    return schema


def normalize_story_repair_plan_issue_ids(
    review: ScriptReviewReport,
    plan: StoryRepairPlan,
) -> StoryRepairPlan:
    """Resolve legacy indices once, then make stable Verifier IDs authoritative."""

    issue_by_index = {
        index: issue for index, issue in enumerate(review.issues, start=1)
    }
    index_by_id = {
        issue.issue_id: index
        for index, issue in issue_by_index.items()
        if issue.issue_id is not None
    }
    actions: list[StoryRepairAction] = []
    for action in plan.actions:
        issue_id = action.issue_id
        if issue_id is None:
            issue = issue_by_index.get(action.issue_index)
            issue_id = issue.issue_id if issue is not None else None
        if issue_id is not None and issue_id in index_by_id:
            actions.append(
                action.model_copy(
                    deep=True,
                    update={
                        "issue_id": issue_id,
                        "issue_index": index_by_id[issue_id],
                    },
                )
            )
        else:
            actions.append(
                action.model_copy(deep=True, update={"issue_id": issue_id})
            )
    return plan.model_copy(deep=True, update={"actions": actions})


def _validate_story_repair_plan(
    review: ScriptReviewReport,
    outline: EpisodeOutline,
    plan: StoryRepairPlan,
) -> None:
    expected_ids = {
        issue.issue_id for issue in review.issues if issue.issue_id is not None
    }
    actual_id_list = [
        action.issue_id for action in plan.actions if action.issue_id is not None
    ]
    actual_ids = set(actual_id_list)
    if expected_ids and (
        actual_ids != expected_ids
        or len(actual_id_list) != len(expected_ids)
        or len(plan.actions) != len(expected_ids)
    ):
        raise ValueError(
            "Leader must route every Verifier issue ID exactly once: "
            f"expected={sorted(expected_ids)}, actual={sorted(actual_ids)}"
        )
    if not expected_ids:
        expected_indices = set(range(1, len(review.issues) + 1))
        actual_indices = {action.issue_index for action in plan.actions}
        if actual_indices != expected_indices:
            raise ValueError(
                "Legacy repair plan must diagnose every routed issue exactly once: "
                f"expected={sorted(expected_indices)}, actual={sorted(actual_indices)}"
            )
    known_scenes = {scene.sequence for scene in outline.scenes}
    invalid_scenes = sorted(
        {
            sequence
            for action in plan.actions
            for sequence in action.affected_scene_sequences
            if sequence not in known_scenes
        }
    )
    if invalid_scenes:
        raise ValueError(
            "Leader repair plan referenced unknown scenes: "
            + ", ".join(str(value) for value in invalid_scenes)
        )


def _story_metadata_revision_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    verification: StoryRevisionVerification | None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.CONCEPT, ProductionSkillRole.WRITER),
        additional_skill_ids=selected_narrative_skill_ids,
        method_skill_ids=selected_method_skill_ids,
    )
    instructions = (
        "你是 story-metadata revision Agent。只返回 JSON，并严格匹配 StoryMetadataRevisionPatch。"
        "这是稀疏的全局元数据对齐，不是场景或剧本重写。只可更新 title、story_summary、"
        "StoryIntent、EndingContract、已经存在的人物/地点/道具描述，以及现有全局 style。"
        "只处理 repair plan 的 active action 在 metadata_targets 中明确列出的类别；"
        "资产 patch 只能用 "
        "character_id、location_id 或 prop_id 精确选择已有实体，不得用名称反推身份；"
        "不得增加、删除或改名资产，不得改变年龄，也不得返回场景、对白、独立的"
        " target_duration_seconds、镜头语言或 schema 外字段。如果 metadata_targets 明确包含"
        " STORY_INTENT，且上游语义确实要求改变时长权威，可以在 StoryIntent.duration_contract"
        " 内修改；其非空 target_seconds 会同步成为 EpisodeOutline 的目标时长，但场次时长"
        "仍由后续场次修订与 Verifier 结合完整语义处理。无需变化的字段全部省略；"
        "当前元数据已符合修复计划时允许空 patch。"
        "最新 authoritative_user_override 与 SourceContract 优先于继承下来的 StoryIntent、"
        "EndingContract 和实际可读剧本；当用户本轮正是在纠正旧契约时，必须同步修正这些"
        "字段，而不是把旧值当作不可修改的保护项。除此以外，SourceContract、StoryIntent、"
        "EndingContract 与实际可读剧本优先于 repair-plan invariants。"
        "invariants 只表示尽量保留的已成立内容；若它与上游契约冲突，或只是声称了"
        "观众在剧本中看不到的信息，不得用它为缺失文本辩护。若提供 Verifier 反馈，"
        "只处理反馈涉及的元数据，保留无关内容。\n\n"
        f"{skill_prompt.text}"
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "source_text": request.input_text.strip(),
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "story_repair_plan": repair_plan.model_dump(mode="json"),
        "requested_metadata_targets": sorted(
            {
                *(
                    target.value
                    for action in repair_plan.actions
                    if action.should_revise
                    for target in action.metadata_targets
                ),
                *(
                    target.value
                    for violation in (
                        verification.violations
                        if verification is not None
                        else []
                    )
                    for target in violation.metadata_targets
                ),
            }
        ),
        "revision_verification": (
            verification.model_dump(mode="json")
            if verification is not None
            else None
        ),
        "current_global_metadata": {
            "title": outline.title,
            "story_summary": outline.story_summary,
            "story_intent": (
                outline.story_intent.model_dump(mode="json")
                if outline.story_intent is not None
                else None
            ),
            "ending_contract": (
                outline.ending_contract.model_dump(mode="json")
                if outline.ending_contract is not None
                else None
            ),
            "characters": [
                item.model_dump(mode="json") for item in outline.characters
            ],
            "locations": [
                item.model_dump(mode="json") for item in outline.locations
            ],
            "props": [item.model_dump(mode="json") for item in outline.props],
            "style": outline.style.model_dump(mode="json"),
        },
        "scene_contract_context": [
            {
                "sequence": scene.sequence,
                "heading": scene.heading,
                "summary": scene.summary,
                "narrative_purpose": scene.narrative_purpose,
                "playable_actions": list(scene.playable_actions),
                "target_duration_seconds": scene.target_duration_seconds,
                "location_names": list(scene.location_names),
                "continuity_in": scene.continuity_in.model_dump(mode="json"),
                "continuity_out": scene.continuity_out.model_dump(mode="json"),
            }
            for scene in outline.scenes
        ],
        "previous_structural_feedback": retry_feedback,
        "output_schema": StoryMetadataRevisionPatch.model_json_schema(),
    }
    return instructions, _compact_json(payload)


def _readable_verifier_screenplay(
    outline: EpisodeOutline,
) -> list[dict[str, object]]:
    """Expose audience-readable text without outline-only causal metadata."""

    readable: list[dict[str, object]] = []
    for scene in outline.scenes:
        scene_text = scene.screenplay_text.strip()
        if not scene_text:
            scene_text = "\n".join(
                f"{outline.lines[index - 1].speaker}：{outline.lines[index - 1].text}"
                for index in scene.dialogue_line_indices
                if 1 <= index <= len(outline.lines)
            )
        readable.append(
            {
                "scene_sequence": scene.sequence,
                "heading": scene.heading,
                "target_duration_seconds": scene.target_duration_seconds,
                "screenplay_text": scene_text,
            }
        )
    return readable


def _story_revision_verifier_prompt(
    request: CreateProjectRequest,
    original_outline: EpisodeOutline,
    revised_outline: EpisodeOutline,
    review: ScriptReviewReport,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
    previous_verification: StoryRevisionVerification | None = None,
    issue_ledger: list[StoryIssueLedgerEntry] | None = None,
) -> tuple[str, str]:
    duration_contract = _resolved_duration_contract(
        request,
        revised_outline.story_intent,
    )
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
        include_constitution=False,
        additional_skill_ids=selected_narrative_skill_ids,
        method_skill_ids=selected_method_skill_ids,
    )
    instructions = (
        "你是 story-revision verifier Agent（终稿修订验收 Agent）。只返回 JSON，并严格匹配 "
        "StoryRevisionVerification。你是唯一的 Script Verifier：在同一次阅读中先核对已分派的修复，再从头到尾"
        "读一遍修订稿，不再转交第二个冷读角色。最新 authoritative_user_override 与 SourceContract "
        "优先于继承的 StoryIntent、EndingContract；保护与本轮用户修改不冲突的契约和已成立内容。"
        "只处理剧本文本，不把镜头、资产、表演、生成或剪辑问题退回 Writer。修订采用了不同写法但已经解决问题时，"
        "不因个人偏好拒绝。\n\n"
        "核对修复时使用 repair_verification_context；若 active repair action 声明了 metadata_targets，"
        "必须同时比较 original_global_story_metadata 与 revised_global_story_metadata，确认对应字段已经"
        "与最新用户要求和可读剧本一致。未声明 metadata target 时不要无故改动或审判隐藏元数据。"
        "整稿通读时只使用 audience_cold_read 中的 user_brief、"
        "SourceContract、StoryIntent、EndingContract 和修订后可读剧本；不用 outline summary、continuity、repair claim 或其他"
        "隐藏元数据替观众补齐剧情。\n\n"
        "整稿只聚焦五项：开篇是否迅速建立冲突和观看动力；主角的处境、目标与行动路线是否清楚；"
        "铺垫是否释放成爽点、情绪回报或反转；对立力量的受挫、反应或反噬是否可见；结尾是否完成 EndingContract。"
        "决定性胜利应能看到主角自身选择、行动或能力的作用。夸张、荒诞、无厘头、类型化跳跃或为了喜剧与爽感升级的"
        "重复都不是天然缺陷。只有观众无法理解当前行动与结果，或文本违反自己明确建立的规则时，才以逻辑理由否决。"
        "在同一次通读中按 DurationContract 声明的时间权威完成自然演读，并必须返回"
        " duration_fit_assessment。TARGET_LED 原创稿已超出契约尺度时，将真正压力点聚焦到"
        "最小可修场次；SOURCE_LED 且未授权缩写时，保留来源承诺并报告自然时长；"
        "AUDIO_LED 服从已定主音轨。估算要自然纳入对白、动作、停顿、反应与明确并行表演，"
        "不用字数公式、固定容差或逐句账本代替判断。同时阅读 rhythm_shape 的叙事功能和"
        "相邻段相对密度，但不要检查固定五段、固定位置或固定次数。\n\n"
        "以前次 Verifier 的稳定 issue_id 作为修复身份：把已满足的 repair-plan 问题写入 "
        "satisfied_issue_ids；satisfied_issue_indices 只作旧记录兼容。若输入带有上一轮"
        "Issue Ledger，用它区分持续问题、已解决问题与新回归，不要把历史本身当成新问题。"
        "若有 previous_revision_verification，已解决的 violation 将 issue_id 写入"
        "satisfied_issue_ids，仍未解决就在新 violation 中沿用同一 issue_id，不得遗忘"
        "或换名。新发现的交付级问题使用新的稳定 issue_id。只有原问题未解决，或五项通读发现会实质破坏当前观看承诺的"
        "具体问题时，才 accepted=false。每个 violation 引用准确场次与文本证据，并给出不破坏已成立内容的聚焦返修指令。"
        "不得因审校附录没有写进场景而拒绝。只有返修确实需要移动场景目标秒数时才标记时长变化。"
        "若修复事项和整稿通读都已通过，则 accepted=true 且 recommended_followup=FINALIZE："
        "你已经完成了终稿冷读，不再把稿件转交给另一个独立审校角色。"
        "结果始终可被用户覆盖。\n\n"
        f"{skill_prompt.text}"
    )
    original_readable_screenplay = _readable_verifier_screenplay(original_outline)
    revised_readable_screenplay = _readable_verifier_screenplay(revised_outline)
    resolved_story_intent = (
        revised_outline.story_intent.model_dump(mode="json")
        if revised_outline.story_intent is not None
        else None
    )
    resolved_ending_contract = (
        revised_outline.ending_contract.model_dump(mode="json")
        if revised_outline.ending_contract is not None
        else None
    )
    repair_verification_context = {
        "original_readable_screenplay": original_readable_screenplay,
        "revised_readable_screenplay": revised_readable_screenplay,
        "original_global_story_metadata": {
            "title": original_outline.title,
            "story_summary": original_outline.story_summary,
            "story_intent": (
                original_outline.story_intent.model_dump(mode="json")
                if original_outline.story_intent is not None
                else None
            ),
            "ending_contract": (
                original_outline.ending_contract.model_dump(mode="json")
                if original_outline.ending_contract is not None
                else None
            ),
            "characters": [
                item.model_dump(mode="json") for item in original_outline.characters
            ],
            "locations": [
                item.model_dump(mode="json") for item in original_outline.locations
            ],
            "props": [item.model_dump(mode="json") for item in original_outline.props],
            "style": original_outline.style.model_dump(mode="json"),
        },
        "revised_global_story_metadata": {
            "title": revised_outline.title,
            "story_summary": revised_outline.story_summary,
            "story_intent": resolved_story_intent,
            "ending_contract": resolved_ending_contract,
            "characters": [
                item.model_dump(mode="json") for item in revised_outline.characters
            ],
            "locations": [
                item.model_dump(mode="json") for item in revised_outline.locations
            ],
            "props": [item.model_dump(mode="json") for item in revised_outline.props],
            "style": revised_outline.style.model_dump(mode="json"),
        },
        "routed_story_review": review.model_dump(mode="json"),
        "story_repair_plan": repair_plan.model_dump(mode="json"),
        "issue_ledger": [
            entry.model_dump(mode="json") for entry in (issue_ledger or [])
        ],
        "previous_revision_verification": (
            previous_verification.model_dump(mode="json")
            if previous_verification is not None
            else None
        ),
    }
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "user_declared_target_duration_seconds": request.target_duration_seconds,
            "source_text": request.input_text.strip(),
        },
        "duration_contract": duration_contract.model_dump(mode="json"),
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "story_intent": resolved_story_intent,
        "ending_contract": resolved_ending_contract,
        "routed_story_review": review.model_dump(mode="json"),
        "story_repair_plan": repair_plan.model_dump(mode="json"),
        "issue_ledger": [
            entry.model_dump(mode="json") for entry in (issue_ledger or [])
        ],
        "previous_revision_verification": (
            previous_verification.model_dump(mode="json")
            if previous_verification is not None
            else None
        ),
        "previous_structural_feedback": retry_feedback,
        "repair_verification_context": repair_verification_context,
        "audience_cold_read": {
            "user_brief": request.input_text.strip(),
            "source_contract": source_contract.model_dump(mode="json"),
            "story_intent": resolved_story_intent,
            "ending_contract": resolved_ending_contract,
            "duration_contract": duration_contract.model_dump(mode="json"),
            "readable_final_screenplay": revised_readable_screenplay,
        },
        "output_schema": _fresh_story_revision_verification_schema(),
    }
    return instructions, _compact_json(payload)


def _scene_director_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff_in: SceneContinuitySummary,
    review_handoff: list[dict[str, object]] | None = None,
    retry_feedback: str | None = None,
    selected_method_skill_ids: tuple[str, ...] = (),
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.PLANNER, ProductionSkillRole.DIRECTOR),
        method_skill_ids=selected_method_skill_ids,
    )
    next_scene = next(
        (item for item in outline.scenes if item.sequence == scene.sequence + 1),
        None,
    )
    relevant_lines = [
        {
            "index": index,
            **outline.lines[index - 1].model_dump(mode="json"),
        }
        for index in scene.dialogue_line_indices
    ]
    instructions = (
        "你是场景导演 Agent。只返回 JSON，并严格匹配 SceneShotPlan。"
        "只把当前场次契约展开为剪辑分镜。只要戏剧动作和摄影构思仍是一个连续单元，"
        "镜头可短于 4 秒或长于 15 秒；H3 的 4–15 秒整数请求边界属于后续执行规划，"
        "不得反过来强迫此处切镜。Story Architect 拥有全局事实、角色、场景、道具和台词；"
        "不得新增或改名。每个分配的台词索引必须恰好出现在一个镜头中，也不得引入其他台词索引。"
        "每个 shot.scene_sequence 与顶层 scene_sequence 都必须等于当前场次。以上一场的"
        "实际 handoff 作为物理事实，同时朝向建筑师规定的离场状态和下一场入场需求。"
        "不要把更早的所有镜头复制进新计划；紧凑 handoff 就是跨场记忆边界。"
        "只要内部有有意义的推进，可以使用长镜头。不要默认一句台词一个镜头：连续台词的"
        "参与者、轴线和简单动作状态一致时，可将它们组成一个完整的戏剧节拍，并给可见反应留出空间。"
        "只在反应镜头会改变解读或连续性时添加它。这是节奏建议，不是固定镜头数规则。"
        "不要为填满时长而拉长高风险的多接触动作。不要把场次粗略的非对白时间填成泛化的"
        "观望、等待、行走或重复证明；只使用场次契约已支持的动作，并让每个镜头揭示变化或必要因果。"
        "路由过来的 Verifier 意见只是制作建议：只处理由本层或后续制作层负责的部分，"
        "不改写故事事实，不添加解释性对白。reference_needs 只使用全局资产已声明的名称，"
        "且只为镜头里真实可见的主体添加最小需求。只在台词中被提到的角色、明确留在画外的道具，"
        "或仅由离场转场提到的下一地点，都不应成为视觉参考需求。若一个镜头含有多个有意义的"
        "内部阶段，用连续 timeline_beats 覆盖从 0 到该镜头剪辑时长的全部区间。每个 beat 应负责"
        "可观察画面、景别、运镜/焦点、动作、可选表演与情绪、精确台词索引、声音和语义参考线索。"
        "线索要指向已声明主体、不含 @ 的稳定短别名、所需视角/区域/状态标签及用途；"
        "镜头必须有匹配的 reference_needs，以便后续选中具体的已审核图片。单一连续阶段时将"
        "timeline_beats 留空。可见切镜、反打或新的固定机位应拆为相邻 PlannedShot，不得隐藏在内部 beat 中。"
        "不要规定固定 beat 数，也不要仅用时码假装快节奏。"
        f"{skill_prompt.text} 不得添加 schema 之外的字段。"
    )
    if retry_feedback:
        instructions += (
            " 上一次对当前场次的返回未满足结构契约。请先解决 previous_retry_feedback "
            "指出的具体问题，再返回当前场次的完整 SceneShotPlan；不要跳过、替换或扩大任务。"
        )
    payload = {
        "episode": {
            "title": outline.title,
            "story_summary": outline.story_summary,
            "target_duration_seconds": outline.target_duration_seconds,
            "style": outline.style.model_dump(mode="json"),
            "characters": [item.model_dump(mode="json") for item in outline.characters],
            "locations": [
                item.model_dump(mode="json")
                for item in outline.locations
                if item.name in scene.location_names
            ],
            "props": [item.model_dump(mode="json") for item in outline.props],
        },
        "scene_contract": scene.model_dump(mode="json"),
        "assigned_dialogue": relevant_lines,
        "previous_actual_handoff": handoff_in.model_dump(mode="json"),
        "routed_review_handoff": review_handoff or [],
        "previous_retry_feedback": retry_feedback,
        "next_scene": (
            {
                "heading": next_scene.heading,
                "narrative_purpose": next_scene.narrative_purpose,
                "expected_entry": next_scene.continuity_in.model_dump(mode="json"),
            }
            if next_scene is not None
            else None
        ),
        "output_schema": SceneShotPlan.model_json_schema(),
    }
    return instructions, _compact_json(payload)


def _validate_locked_outline(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
    outline: EpisodeOutline,
) -> None:
    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        return
    _validate_locked_source_contract(request, source_contract)
    expected_lines = _planned_locked_dialogue(source_contract)
    expected = [(line.speaker, line.text, line.audio_kind) for line in expected_lines]
    actual = [(line.speaker, line.text, line.audio_kind) for line in outline.lines]
    if actual != expected:
        raise ValueError("Story architect changed or omitted locked dialogue")
    # Entity binding is carried by speaker_character_id and validated by EpisodeOutline.
    # A display name is not an identity key, and unbound narration or off-cast voices remain valid.


def _validate_story_blueprint(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
    outline: EpisodeOutline,
) -> None:
    """Validate the common blueprint contract before scene-local writing begins."""

    scripted = [
        scene.sequence for scene in outline.scenes if scene.screenplay_text.strip()
    ]
    if scripted:
        raise ValueError(
            "Story architect must leave screenplay_text to the scene-writer stage: "
            + ", ".join(str(sequence) for sequence in scripted)
        )
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        _validate_locked_outline(request, source_contract, outline)
        return
    if outline.lines:
        raise ValueError(
            "Story architect must leave unlocked dialogue to the scene-writer stage"
        )
    populated = [
        scene.sequence for scene in outline.scenes if scene.dialogue_line_indices
    ]
    if populated:
        raise ValueError(
            "Unlocked blueprint scenes must not pre-assign dialogue indices: "
            + ", ".join(str(sequence) for sequence in populated)
        )


def _require_ending_contract(outline: EpisodeOutline) -> EpisodeOutline:
    """Require the Story Architect to make its own semantic ending decision."""

    if outline.ending_contract is None:
        raise ValueError(
            "Story Architect must explicitly return ending_contract after reading "
            "the original user brief"
        )
    return outline


def _validate_locked_dialogue(
    request: CreateProjectRequest,
    source_contract: StorySourceContract,
    plan: ProductionPlan,
) -> None:
    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        return
    _validate_locked_source_contract(request, source_contract)
    expected_lines = _planned_locked_dialogue(source_contract)
    if not expected_lines:
        raise ValueError("Locked script did not contain recognizable dialogue")
    expected = [
        (line.speaker, line.text, line.audio_kind)
        for line in expected_lines
    ]
    actual = [
        (line.speaker, line.text, line.audio_kind)
        for line in plan.lines
    ]
    if actual != expected:
        raise ValueError("Planning model changed or omitted locked dialogue")
    # ProductionPlan validates every explicit speaker_character_id against declared
    # character IDs. Do not reconstruct identity from presentation names here.
    assigned = [
        index
        for shot in plan.shots
        for index in shot.dialogue_line_indices
    ]
    expected_indices = list(range(1, len(plan.lines) + 1))
    if sorted(assigned) != expected_indices or len(assigned) != len(set(assigned)):
        raise ValueError("Planning model duplicated or omitted locked dialogue in shots")


def _fallback_source_summary(request: CreateProjectRequest) -> str:
    text = request.input_text.strip()
    if text:
        return text[:2400]
    image_descriptions = [
        item.descriptor.strip()
        for item in request.input_images
        if item.descriptor.strip()
    ]
    if image_descriptions:
        return ("；".join(image_descriptions))[:2400]
    return "本地回退只建立可继续编辑的制作骨架；具体内容、Style 与节奏等待 Agent 完成。"


def _fallback_ending_contract(request: CreateProjectRequest) -> EndingContract:
    del request
    return EndingContract(
        requested_preference=ContinuationPreference.AGENT_DECIDES,
        resolved_mode=ResolvedEndingMode.STANDALONE,
        episode_payoff="完成用户在本集提出的主要因果与情绪承诺。",
        required_closures=["本集主要冲突必须得到可见结果"],
        allowed_open_threads=[],
        continuation_hook=None,
        final_button="以可见结果、人物反应或自然台词完成本集落点。",
        decision_basis=(
            "本地兼容骨架不代替 Story Architect 的语义判断；"
            "正式创作时由 Agent 根据原始需求选择。"
        ),
    )


def _fallback_scaffold_plan(request: CreateProjectRequest) -> ProductionPlan:
    """Preserve user facts without pretending a local template made creative decisions."""

    source_contract = build_story_source_contract(request)
    lines = (
        _planned_locked_dialogue(source_contract)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )

    speakers = list(
        dict.fromkeys(
            line.speaker
            for line in lines
            if line.audio_kind == AudioKind.DIALOGUE
        )
    )
    if not speakers:
        speakers = ["项目主体"]

    characters = [
        PlannedCharacter(
            character_id=f"character-{index + 1:02d}",
            name=name,
            age=0,
            appearance="只保留用户输入中明确的身份锚点；未声明特征由 Agent 与项目 Style 决定",
            wardrobe="不由本地回退预设服装、材质或造型规则",
            performance="表现方式、静动关系与情绪强度由内容和项目 Style 决定",
            voice_identity=f"S{index + 1}",
        )
        for index, name in enumerate(speakers[:12])
    ]
    premise = _fallback_source_summary(request)
    target = request.resolved_target_duration_seconds()
    # This split only satisfies the locked-text field capacity; it is not a beat rule.
    shot_count = max(1, math.ceil(len(lines) / 40))
    line_buckets = _distribute_line_indices(len(lines), shot_count)
    shots = [
        PlannedShot(
            duration_seconds=_UNRESOLVED_FALLBACK_SHOT_DURATION_SECONDS,
            narrative_purpose=(
                f"承载用户输入中的第 {sequence} 个制作单元；"
                "本地回退不补写题材、反转或节奏模板"
            ),
            visual=(
                "依据用户输入与当前 Style 呈现本制作单元，只使用已声明的主体、"
                f"环境和状态：{premise[:700]}"
            ),
            framing="由 Agent 根据当前内容、Style 与画面职责决定",
            camera="由 Agent 根据当前内容、Style 与画面职责决定",
            audio_intent=(
                "严格保留分配到本镜头的锁定文本，其他声音由 Agent 决定"
                if line_buckets[sequence - 1]
                else "声音、静默与文字的分工由 Agent 根据当前内容决定"
            ),
            dialogue_line_indices=line_buckets[sequence - 1],
            character_positions="只沿用用户输入中明确的主体与关系，不新增固定站位",
            eyeline="仅在当前视觉媒介和剧情确有需要时定义视线或方向关系",
            prop_state_in="只沿用用户输入中明确的道具或状态",
            prop_state_out="状态变化由当前内容决定，本地回退不补写",
            action_in="本制作单元的进入状态由用户输入和前序内容决定",
            action_out="本制作单元的离开状态由用户输入和后续内容决定",
            transition_out="由 Agent 根据当前段落与整体节奏决定",
            merge_candidate=False,
            local_rework_risk=(
                "自然时长尚未由 Agent 判定；生成前必须用正式分镜时序替换技术占位"
            ),
        )
        for sequence in range(1, shot_count + 1)
    ]
    return ProductionPlan(
        title=request.name or "未命名视频",
        scene_heading="场景与段落由用户输入和 Agent 规划决定",
        scene_summary=premise,
        target_duration_seconds=target,
        lines=lines,
        characters=characters,
        locations=[
            PlannedLocation(
                location_id="location-01",
                name="项目场景",
                layout="空间、图层或画面组织关系由当前内容与 Style 决定",
                lighting="光线或明暗规则由当前 Style 决定",
                palette="色彩关系由当前 Style 决定",
                key_views=["只按实际镜头需要建立参考"],
            )
        ],
        props=[],
        style=PlannedStyle(
            medium=UNSPECIFIED_STYLE_VALUE,
            genre=UNSPECIFIED_STYLE_VALUE,
            cinematography=UNSPECIFIED_STYLE_VALUE,
            color=UNSPECIFIED_STYLE_VALUE,
            texture=UNSPECIFIED_STYLE_VALUE,
            forbidden=[],
        ),
        ending_contract=_fallback_ending_contract(request),
        shots=shots,
    )


def _select_fallback_shots(
    templates: list[PlannedShot],
    *,
    scene_sequence: int,
    preferred_count: int | None = None,
) -> list[PlannedShot]:
    """Select structural containers without inventing Agent-authored timing."""

    if not templates:
        raise ValueError("At least one shot template is required")
    requested_count = preferred_count if preferred_count is not None else len(templates)
    count = max(1, requested_count)
    if count == 1:
        selected = [templates[0]]
    elif count <= len(templates):
        selected = [
            templates[round(index * (len(templates) - 1) / (count - 1))]
            for index in range(count)
        ]
    else:
        selected = [templates[index % len(templates)] for index in range(count)]
    return [
        template.model_copy(
            deep=True,
            update={"scene_sequence": scene_sequence},
        )
        for template in selected
    ]


def _fallback_scene_state(
    *,
    setting: str,
    scene_sequence: int,
    phase: str,
) -> SceneContinuitySummary:
    return SceneContinuitySummary(
        setting=setting,
        character_states=[
            f"第 {scene_sequence} 个制作单元的{phase}主体状态由用户输入与 Agent 规划决定"
        ],
        prop_states=["只继承项目已经声明的状态，不由本地回退补写"],
        known_facts=[],
        open_threads=["等待 Agent 根据完整项目上下文定义本单元的推进与衔接"],
    )


def _distribute_line_indices(line_count: int, scene_count: int) -> list[list[int]]:
    buckets: list[list[int]] = [[] for _ in range(scene_count)]
    for offset, index in enumerate(range(1, line_count + 1)):
        bucket = min(scene_count - 1, offset * scene_count // max(1, line_count))
        buckets[bucket].append(index)
    return buckets


def _fallback_episode_outline(request: CreateProjectRequest) -> EpisodeOutline:
    base = _fallback_scaffold_plan(request)
    source_contract = build_story_source_contract(request)
    target = request.resolved_target_duration_seconds()
    # Use the minimum valid container. Creative scene segmentation belongs to the Agent.
    scene_count = 1
    lines = list(base.lines)
    line_buckets = _distribute_line_indices(len(lines), scene_count)
    setting = base.locations[0].name
    scenes = [
        PlannedSceneContract(
            sequence=sequence,
            heading=(
                base.scene_heading
                if scene_count == 1
                else f"制作单元 {sequence:02d}；具体场景由 Agent 决定"
            ),
            summary=(
                f"{base.scene_summary}；这是本地回退划分的第 {sequence} 个可编辑制作单元，"
                "不代表最终叙事结构。"
            )[:1600],
            narrative_purpose=(
                "保留用户输入与锁定事实，为 Agent 后续补全本单元的剧情、Style 和节奏"
            ),
            playable_actions=[],
            # This is the episode's working envelope, not estimated performance time.
            target_duration_seconds=target,
            dialogue_line_indices=line_buckets[sequence - 1],
            location_names=[setting],
            continuity_in=_fallback_scene_state(
                setting=setting,
                scene_sequence=sequence,
                phase="entry",
            ),
            continuity_out=(
                _fallback_scene_state(
                    setting=setting,
                    scene_sequence=sequence,
                    phase="exit",
                )
                if base.ending_contract is not None
                and base.ending_contract.resolved_mode == ResolvedEndingMode.SERIES
                else SceneContinuitySummary(
                    setting=setting,
                    character_states=["本集主要人物已到达可见结果状态"],
                    prop_states=[],
                    known_facts=["本集主要因果已闭合"],
                    open_threads=[],
                )
            ),
        )
        for sequence in range(1, scene_count + 1)
    ]
    return EpisodeOutline(
        title=base.title,
        story_summary=base.scene_summary,
        target_duration_seconds=target,
        lines=lines,
        characters=base.characters,
        locations=base.locations,
        props=base.props,
        style=base.style,
        ending_contract=base.ending_contract,
        story_intent=_fallback_story_intent(request, source_contract),
        scenes=scenes,
    )
def _story_blueprint(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
) -> EpisodeOutline:
    """Project any valid outline into the shared scene-node writing contract."""

    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return outline.model_copy(deep=True)
    payload = outline.model_dump(mode="python")
    payload["lines"] = []
    for scene in payload["scenes"]:
        scene["dialogue_line_indices"] = []
    return EpisodeOutline.model_validate(payload)


def _fallback_directed_scene_plan(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff_in: SceneContinuitySummary,
) -> SceneShotPlan:
    base = _fallback_scaffold_plan(request)
    # Use one placeholder shot unless locked-text capacity requires another container.
    preferred_count = max(1, math.ceil(len(scene.dialogue_line_indices) / 40))
    shots = _select_fallback_shots(
        base.shots,
        scene_sequence=scene.sequence,
        preferred_count=preferred_count,
    )
    for shot in shots:
        shot.dialogue_line_indices = []
        shot.narrative_purpose = f"{scene.narrative_purpose}；{shot.narrative_purpose}"
        shot.visual = f"{scene.heading}。{shot.visual}"
    for offset, line_index in enumerate(scene.dialogue_line_indices):
        shots[offset % len(shots)].dialogue_line_indices.append(line_index)
    shots[0].character_positions = "; ".join(handoff_in.character_states) or shots[0].character_positions
    shots[0].prop_state_in = "; ".join(handoff_in.prop_states) or shots[0].prop_state_in
    shots[-1].prop_state_out = "; ".join(scene.continuity_out.prop_states) or shots[-1].prop_state_out
    shots[-1].action_out = "; ".join(scene.continuity_out.open_threads) or shots[-1].action_out
    return SceneShotPlan(
        scene_sequence=scene.sequence,
        shots=shots,
        continuity_out=scene.continuity_out.model_copy(deep=True),
    )


def _validate_directed_scene_plan(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    plan: SceneShotPlan,
) -> None:
    if plan.scene_sequence != scene.sequence:
        raise ValueError("Scene director returned the wrong scene sequence")
    if any(shot.scene_sequence != scene.sequence for shot in plan.shots):
        raise ValueError("Scene director shots reference another scene")
    assigned = [
        index
        for shot in plan.shots
        for index in shot.dialogue_line_indices
    ]
    expected = scene.dialogue_line_indices
    if sorted(assigned) != sorted(expected) or len(assigned) != len(set(assigned)):
        raise ValueError("Scene director changed, duplicated, or omitted assigned dialogue lines")
    known_subjects = (
        {item.name for item in outline.characters}
        | {item.name for item in outline.locations}
        | {item.name for item in outline.props}
    )
    unknown_subjects = sorted(
        {
            need.subject_name
            for shot in plan.shots
            for need in shot.reference_needs
            if need.subject_name not in known_subjects
        }
    )
    if unknown_subjects:
        raise ValueError(
            "Scene director reference needs use subjects outside the episode asset "
            "contract: " + ", ".join(unknown_subjects)
        )


def _assemble_production_plan(
    outline: EpisodeOutline,
    directed_scenes: list[SceneShotPlan],
) -> ProductionPlan:
    if [scene.scene_sequence for scene in directed_scenes] != [
        scene.sequence for scene in outline.scenes
    ]:
        raise ValueError("Directed scenes do not match the episode outline")
    shots = [shot for scene in directed_scenes for shot in scene.shots]
    return ProductionPlan(
        title=outline.title,
        scene_heading=outline.scenes[0].heading,
        scene_summary=outline.story_summary,
        target_duration_seconds=outline.target_duration_seconds,
        lines=outline.lines,
        characters=outline.characters,
        locations=outline.locations,
        props=outline.props,
        style=outline.style,
        ending_contract=outline.ending_contract,
        story_intent=outline.story_intent,
        scenes=outline.scenes,
        shots=shots,
    )


def _fallback_plan(request: CreateProjectRequest) -> ProductionPlan:
    outline = _fallback_episode_outline(request)
    directed: list[SceneShotPlan] = []
    handoff = outline.scenes[0].continuity_in.model_copy(deep=True)
    for scene in outline.scenes:
        plan = _fallback_directed_scene_plan(request, outline, scene, handoff)
        _validate_directed_scene_plan(outline, scene, plan)
        directed.append(plan)
        handoff = plan.continuity_out.model_copy(deep=True)
    return _assemble_production_plan(outline, directed)


def create_story_architecture(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
    *,
    source_contract: StorySourceContract | None = None,
    story_intent: StoryIntent | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
    revision_instruction: str | None = None,
    current_outline: EpisodeOutline | None = None,
) -> StoryArchitectureResult:
    """Run one recoverable Story Architect unit, with a local scaffold fallback."""

    resolved_source_contract = source_contract or build_story_source_contract(request)
    resolved_story_intent = story_intent or _fallback_story_intent(
        request,
        resolved_source_contract,
    )
    if client is None:
        fallback_outline = _with_outline_entity_ids(
            _story_blueprint(request, _fallback_episode_outline(request))
        )
        return StoryArchitectureResult(
            outline=fallback_outline.model_copy(
                deep=True,
                update={"story_intent": resolved_story_intent.model_copy(deep=True)},
            ),
            source="deterministic structure scaffold",
            warnings=[
                "GPT-5.6-sol 未配置；Story Architect 仅建立了不补写创意的"
                "本地结构骨架。"
            ],
            source_contract=resolved_source_contract,
        )
    image_urls, image_warnings = _planner_image_urls(request)
    warnings = list(image_warnings)
    source = "gpt-5.6-sol story architect"
    instructions, input_text = _outline_prompt(
        request,
        resolved_source_contract,
        resolved_story_intent,
        selected_narrative_skill_ids,
        selected_method_skill_ids,
        retry_feedback,
        revision_instruction,
        current_outline,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=14000,
        )
        outline = _with_outline_entity_ids(
            _require_ending_contract(
                EpisodeOutline.model_validate_json(_strip_json_fence(output))
            )
        )
        _validate_story_blueprint(request, resolved_source_contract, outline)
        outline = outline.model_copy(
            deep=True,
            update={"story_intent": resolved_story_intent.model_copy(deep=True)},
        )
    except (LanguageProviderError, ValueError) as error:
        fallback_outline = _with_outline_entity_ids(
            _story_blueprint(request, _fallback_episode_outline(request))
        )
        outline = fallback_outline.model_copy(
            deep=True,
            update={"story_intent": resolved_story_intent.model_copy(deep=True)},
        )
        source = "deterministic structure scaffold"
        warnings.append(
            "故事统筹 Agent 本次未形成可采用的结构化蓝图；已保留输入和当前定向，"
            "由 PlanningRun 在同一游标上等待下一次推进："
            + _compact_error(error)
        )
    return StoryArchitectureResult(
        outline=outline,
        source=source,
        warnings=warnings,
        source_contract=resolved_source_contract,
    )


def review_story_outline(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    client: LanguageProviderClient | None,
    *,
    source_contract: StorySourceContract | None = None,
    issue_ledger: list[StoryIssueLedgerEntry] | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> ScriptCritiqueResult:
    """Run the script Verifier without blocking later planning."""

    if client is None:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=["GPT-5.6-sol 未配置，已跳过语义剧本审阅并保留结构校验。"],
        )
    instructions, input_text = _story_critic_prompt(
        request,
        outline,
        source_contract=source_contract,
        issue_ledger=issue_ledger,
        selected_narrative_skill_ids=selected_narrative_skill_ids,
        selected_method_skill_ids=selected_method_skill_ids,
        retry_feedback=retry_feedback,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=6000,
        )
        report = ScriptReviewReport.model_validate_json(_strip_json_fence(output))
        warnings: list[str] = []
        if report.duration_fit_assessment is None:
            report = report.model_copy(
                deep=True,
                update={
                    "duration_fit_assessment": _unresolved_duration_fit_assessment(
                        request,
                        outline.story_intent,
                        verifier_name="Script Verifier",
                    )
                },
            )
            warnings.append(
                "Script Verifier 未返回 duration_fit_assessment；"
                "已将本次时长判断显式记录为 UNRESOLVED 并交回 Leader，"
                "不自动重试、不虚构剧本缺陷。"
            )
        report = _normalize_review_report(report)
        return ScriptCritiqueResult(
            report=report,
            used=True,
            warnings=warnings,
        )
    except (LanguageProviderError, ValueError) as error:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=[
                "剧本审阅 Agent 本次未形成可采用的结构化冷读；"
                "保留当前游标等待下一次 planning.advance："
                + _compact_error(error)
            ],
        )


def _fallback_review_disposition(
    review: ScriptReviewReport,
    *,
    rationale: str,
) -> ScriptReviewDisposition:
    _validate_review_report_semantics(review)
    if not review.issues:
        return ScriptReviewDisposition(
            action=ScriptReviewDispositionAction.CONTINUE,
            issue_routes=[],
            rationale=rationale,
        )
    has_story_revision = review.requires_revision and any(
        issue.owner == ReviewRepairOwner.STORY_ARCHITECT
        for issue in review.issues
    )
    return ScriptReviewDisposition(
        action=(
            ScriptReviewDispositionAction.REVISE_STORY
            if has_story_revision
            else ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
        ),
        issue_routes=[
            ScriptReviewIssueRoute(
                issue_index=index,
                owner=issue.owner,
                rationale=(
                    f"Script Verifier 已把 {issue.issue_id or '未提供 issue_id 的旧问题'} "
                    f"归属给 {issue.owner.value}；是否回到剧本修订由 Verifier "
                    "的 requires_revision 语义判断决定。"
                ),
            )
            for index, issue in enumerate(review.issues, start=1)
        ],
        rationale=rationale,
    )


def _validate_review_disposition(
    review: ScriptReviewReport,
    disposition: ScriptReviewDisposition,
) -> None:
    expected = set(range(1, len(review.issues) + 1))
    actual = {route.issue_index for route in disposition.issue_routes}
    if actual != expected:
        raise ValueError(
            "Review Router must route every critic issue exactly once: "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    has_story_revision = bool(
        story_revision_issue_indices(review, disposition)
    )
    if disposition.action == ScriptReviewDispositionAction.REVISE_STORY:
        if not review.requires_revision or not has_story_revision:
            raise ValueError(
                "REVISE_STORY requires the Verifier to request revision and route a story issue"
            )
    elif review.requires_revision and has_story_revision:
        raise ValueError("A Verifier-requested story revision must return to the writer")
    if not review.issues and disposition.action != ScriptReviewDispositionAction.CONTINUE:
        raise ValueError("A review without issues must continue")
    if review.issues and disposition.action == ScriptReviewDispositionAction.CONTINUE:
        raise ValueError("A review with routed issues must continue with handoff or revise story")


def story_revision_issue_indices(
    review: ScriptReviewReport,
    disposition: ScriptReviewDisposition,
) -> set[int]:
    """Return story issues selected by the Verifier for the current revision pass."""

    issue_by_index = {
        index: issue for index, issue in enumerate(review.issues, start=1)
    }
    return {
        route.issue_index
        for route in disposition.issue_routes
        if route.owner == ReviewRepairOwner.STORY_ARCHITECT
        and issue_by_index.get(route.issue_index) is not None
        and review.requires_revision
    }


def route_story_review(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    client: LanguageProviderClient | None,
) -> ScriptReviewRoutingResult:
    """Route the Verifier's typed ownership without another serial model call."""

    del request, outline, client
    disposition = _fallback_review_disposition(
        review,
        rationale=(
            "直接执行 Script Verifier 的结构化 owner 与 "
            "requires_revision 判断，不另加关键词或次数门槛。"
            if review.issues
            else "Script Verifier 未报告问题，按其语义判断直接完成。"
        ),
    )
    _validate_review_disposition(review, disposition)
    return ScriptReviewRoutingResult(
        disposition=disposition,
        used=False,
        warnings=[],
    )


def story_revision_review(
    review: ScriptReviewReport,
    disposition: ScriptReviewDisposition,
) -> ScriptReviewReport:
    """Return only findings explicitly owned by Story Architect."""

    indices = story_revision_issue_indices(review, disposition)
    return review.model_copy(
        deep=True,
        update={
            "issues": [
                issue.model_copy(deep=True)
                for index, issue in enumerate(review.issues, start=1)
                if index in indices
            ],
            "requires_revision": bool(indices),
        },
    )


def scene_review_handoff(
    review: ScriptReviewReport | None,
    disposition: ScriptReviewDisposition | None,
    scene_sequence: int,
) -> list[dict[str, object]]:
    """Expose relevant routed observations to a scene Agent without rewriting the story."""

    if review is None or disposition is None:
        return []
    route_by_index = {
        route.issue_index: route for route in disposition.issue_routes
    }
    handoff: list[dict[str, object]] = []
    for index, issue in enumerate(review.issues, start=1):
        route = route_by_index.get(index)
        if route is None or route.owner in {
            ReviewRepairOwner.STORY_ARCHITECT,
            ReviewRepairOwner.USER_OPTIONAL,
        }:
            continue
        if issue.scene_sequences and scene_sequence not in issue.scene_sequences:
            continue
        handoff.append(
            {
                "issue_index": index,
                "owner": route.owner.value,
                "observation": issue.observation,
                "recommendation": issue.recommendation,
                "routing_rationale": route.rationale,
            }
        )
    return handoff


def judge_story_revisions(
    request: CreateProjectRequest,
    *,
    older_revision_round: int,
    older_outline: EpisodeOutline,
    older_review: ScriptReviewReport,
    newer_revision_round: int,
    newer_outline: EpisodeOutline,
    newer_review: ScriptReviewReport,
    client: LanguageProviderClient | None,
) -> RevisionJudgeResult:
    """Recommend between two reviewed outlines without mutating the current selection."""

    if older_revision_round >= newer_revision_round:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "Revision Judge 缺少按时间排序的两版大纲，已保留最新版继续规划。"
            ],
        )
    if client is None:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "GPT-5.6-sol 未配置，已跳过 Revision Judge；最新版大纲继续进入逐场规划。"
            ],
        )
    instructions, input_text = _revision_judge_prompt(
        request,
        older_revision_round=older_revision_round,
        older_outline=older_outline,
        older_review=older_review,
        newer_revision_round=newer_revision_round,
        newer_outline=newer_outline,
        newer_review=newer_review,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=4000,
        )
        recommendation = RevisionJudgeRecommendation.model_validate_json(
            _strip_json_fence(output)
        )
        candidate_rounds = {older_revision_round, newer_revision_round}
        returned_rounds = {
            recommendation.recommended_revision_round,
            recommendation.alternative_revision_round,
        }
        if returned_rounds != candidate_rounds:
            raise ValueError(
                "Revision Judge selected a revision outside the supplied candidates"
            )
        return RevisionJudgeResult(recommendation=recommendation, warnings=[])
    except (LanguageProviderError, ValueError) as error:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "Revision Judge 不可用，未替用户改选版本；最新版大纲继续进入逐场规划："
                + _compact_error(error)
            ],
        )


def plan_story_repair(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review_report: ScriptReviewReport,
    client: LanguageProviderClient | None,
    *,
    source_contract: StorySourceContract | None = None,
    revision_history: list[dict[str, object]] | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> StoryRepairPlanningResult:
    """Let the Leader route Verifier findings back to the Writer."""

    contract_result = (
        compile_story_source_contract(request, client)
        if source_contract is None
        else StorySourceContractResult(
            contract=source_contract,
            used=True,
            warnings=[],
        )
    )
    resolved_source_contract = contract_result.contract
    fallback = _fallback_story_repair_plan(request, outline, review_report)
    if client is None:
        return StoryRepairPlanningResult(
            plan=fallback,
            source_contract=resolved_source_contract,
            used=False,
            warnings=[
                *contract_result.warnings,
                "Leader 未配置语言模型，采用 Verifier 交接的最小修订计划。",
            ],
        )
    instructions, input_text = _story_repair_plan_prompt(
        request,
        outline,
        review_report,
        resolved_source_contract,
        revision_history,
        selected_narrative_skill_ids,
        selected_method_skill_ids,
        retry_feedback,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=7000,
        )
        plan = StoryRepairPlan.model_validate_json(_strip_json_fence(output))
        if any(issue.issue_id is not None for issue in review_report.issues) and any(
            action.issue_id is None for action in plan.actions
        ):
            raise ValueError(
                "Fresh Leader repair actions require stable issue IDs"
            )
        plan = normalize_story_repair_plan_issue_ids(review_report, plan)
        _validate_story_repair_plan(review_report, outline, plan)
        return StoryRepairPlanningResult(
            plan=plan,
            source_contract=resolved_source_contract,
            used=True,
            warnings=list(contract_result.warnings),
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRepairPlanningResult(
            plan=fallback,
            source_contract=resolved_source_contract,
            used=False,
            warnings=[
                *contract_result.warnings,
                "Leader 本次未形成可采用的语义返修方案，"
                "采用 Verifier 交接的最小修订计划："
                + _compact_error(error)
            ],
        )


def apply_story_metadata_revision_patch(
    outline: EpisodeOutline,
    patch: StoryMetadataRevisionPatch,
) -> EpisodeOutline:
    """Apply only the global metadata fields exposed by the typed patch.

    ``target_duration_seconds`` cannot be patched independently. If the Agent
    intentionally replaces StoryIntent with a DurationContract carrying an
    exact target, that target is projected onto the episode envelope so the
    future ScriptVersion has one duration truth.
    """

    character_by_id = {
        item.character_id: item for item in patch.character_patches
    }
    location_by_id = {item.location_id: item for item in patch.location_patches}
    prop_by_id = {item.prop_id: item for item in patch.prop_patches}
    unknown_characters = sorted(
        set(character_by_id)
        - {
            item.character_id
            for item in outline.characters
            if item.character_id is not None
        }
    )
    unknown_locations = sorted(
        set(location_by_id)
        - {
            item.location_id
            for item in outline.locations
            if item.location_id is not None
        }
    )
    unknown_props = sorted(
        set(prop_by_id)
        - {item.prop_id for item in outline.props if item.prop_id is not None}
    )
    unknown = [
        *(f"character:{name}" for name in unknown_characters),
        *(f"location:{name}" for name in unknown_locations),
        *(f"prop:{name}" for name in unknown_props),
    ]
    if unknown:
        raise ValueError(
            "Story metadata patch referenced an unknown asset selector: "
            + ", ".join(unknown)
        )

    characters: list[PlannedCharacter] = []
    for character in outline.characters:
        item_patch = character_by_id.get(character.character_id or "")
        updates = (
            item_patch.model_dump(exclude={"character_id"}, exclude_none=True)
            if item_patch is not None
            else {}
        )
        characters.append(character.model_copy(update=updates))
    locations: list[PlannedLocation] = []
    for location in outline.locations:
        item_patch = location_by_id.get(location.location_id or "")
        updates = (
            item_patch.model_dump(exclude={"location_id"}, exclude_none=True)
            if item_patch is not None
            else {}
        )
        locations.append(location.model_copy(update=updates))
    props: list[PlannedProp] = []
    for prop in outline.props:
        item_patch = prop_by_id.get(prop.prop_id or "")
        updates = (
            item_patch.model_dump(exclude={"prop_id"}, exclude_none=True)
            if item_patch is not None
            else {}
        )
        props.append(prop.model_copy(update=updates))
    style = outline.style.model_copy(
        update=(
            patch.style.model_dump(exclude_none=True)
            if patch.style is not None
            else {}
        )
    )
    revised_story_intent = (
        patch.story_intent
        if patch.story_intent is not None
        else outline.story_intent
    )
    revised_duration_target = (
        None
        if revised_story_intent is None
        or revised_story_intent.duration_contract is None
        else revised_story_intent.duration_contract.target_seconds
    )
    revised = EpisodeOutline.model_validate(
        {
            **outline.model_dump(mode="python"),
            **(
                {"title": patch.title}
                if patch.title is not None
                else {}
            ),
            **(
                {"story_summary": patch.story_summary}
                if patch.story_summary is not None
                else {}
            ),
            **(
                {"story_intent": revised_story_intent}
                if patch.story_intent is not None
                else {}
            ),
            **(
                {"target_duration_seconds": revised_duration_target}
                if patch.story_intent is not None
                and revised_duration_target is not None
                else {}
            ),
            **(
                {"ending_contract": patch.ending_contract}
                if patch.ending_contract is not None
                else {}
            ),
            "characters": characters,
            "locations": locations,
            "props": props,
            "style": style,
        }
    )
    if revised.lines != outline.lines:
        raise ValueError("Story metadata revision changed dialogue lines")
    if revised.scenes != outline.scenes:
        raise ValueError("Story metadata revision changed scene contracts")
    if [(item.character_id, item.name, item.age) for item in revised.characters] != [
        (item.character_id, item.name, item.age) for item in outline.characters
    ]:
        raise ValueError("Story metadata revision changed character identity")
    if [(item.location_id, item.name) for item in revised.locations] != [
        (item.location_id, item.name) for item in outline.locations
    ]:
        raise ValueError("Story metadata revision changed location identity")
    if [(item.prop_id, item.name) for item in revised.props] != [
        (item.prop_id, item.name) for item in outline.props
    ]:
        raise ValueError("Story metadata revision changed prop identity")
    return revised


def revise_story_metadata(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    client: LanguageProviderClient | None,
    *,
    verification: StoryRevisionVerification | None = None,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
) -> StoryMetadataRevisionResult:
    """Run one recoverable provider call that can change global metadata only."""

    if client is None:
        return StoryMetadataRevisionResult(
            outline=outline,
            patch=None,
            revised=False,
            used=False,
            warnings=[
                "Story Metadata Revision Agent 未配置，已保留原有全局元数据。"
            ],
        )
    instructions, input_text = _story_metadata_revision_prompt(
        request,
        outline,
        repair_plan,
        source_contract,
        verification,
        selected_narrative_skill_ids,
        selected_method_skill_ids,
        retry_feedback,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=5000,
        )
        patch = StoryMetadataRevisionPatch.model_validate_json(
            _strip_json_fence(output)
        )
        revised = apply_story_metadata_revision_patch(outline, patch)
        _validate_locked_outline(request, source_contract, revised)
        return StoryMetadataRevisionResult(
            outline=revised,
            patch=patch,
            revised=revised != outline,
            used=True,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryMetadataRevisionResult(
            outline=outline,
            patch=None,
            revised=False,
            used=False,
            warnings=[
                "Story Metadata Revision Agent 不可用，"
                "已保留原有全局元数据："
                + _compact_error(error)
            ],
        )


def _fresh_story_revision_verification_schema() -> dict[str, object]:
    """Require stable IDs from new Verifier output while loading old records."""

    schema = StoryRevisionVerification.model_json_schema()
    required = cast(list[str], schema.setdefault("required", []))
    if "satisfied_issue_ids" not in required:
        required.append("satisfied_issue_ids")
    if "duration_fit_assessment" not in required:
        required.append("duration_fit_assessment")
    definitions = cast(dict[str, object], schema["$defs"])
    violation_schema = cast(
        dict[str, object], definitions["StoryRevisionViolation"]
    )
    violation_required = cast(
        list[str], violation_schema.setdefault("required", [])
    )
    if "issue_id" not in violation_required:
        violation_required.append("issue_id")
    if "metadata_targets" not in violation_required:
        violation_required.append("metadata_targets")
    return schema


def normalize_story_revision_verification_issue_ids(
    repair_plan: StoryRepairPlan,
    verification: StoryRevisionVerification,
) -> StoryRevisionVerification:
    """Project legacy satisfied indices onto stable repair issue IDs."""

    action_by_index = {
        action.issue_index: action for action in repair_plan.actions
    }
    index_by_id = {
        action.issue_id: action.issue_index
        for action in repair_plan.actions
        if action.issue_id is not None
    }
    satisfied_ids = list(verification.satisfied_issue_ids)
    for issue_index in verification.satisfied_issue_indices:
        action = action_by_index.get(issue_index)
        if (
            action is not None
            and action.issue_id is not None
            and action.issue_id not in satisfied_ids
        ):
            satisfied_ids.append(action.issue_id)
    satisfied_indices = list(verification.satisfied_issue_indices)
    for issue_id in satisfied_ids:
        issue_index = index_by_id.get(issue_id)
        if issue_index is not None and issue_index not in satisfied_indices:
            satisfied_indices.append(issue_index)
    return verification.model_copy(
        deep=True,
        update={
            "satisfied_issue_ids": satisfied_ids,
            "satisfied_issue_indices": satisfied_indices,
        },
    )


def _validate_story_revision_verification(
    outline: EpisodeOutline,
    repair_plan: StoryRepairPlan,
    verification: StoryRevisionVerification,
    *,
    previous_verification: StoryRevisionVerification | None = None,
) -> None:
    known_issue_indices = {action.issue_index for action in repair_plan.actions}
    satisfied_indices = set(verification.satisfied_issue_indices)
    if not satisfied_indices.issubset(known_issue_indices):
        raise ValueError("Story Revision Verifier returned an unknown repair issue index")
    required_legacy_indices = {
        action.issue_index
        for action in repair_plan.actions
        if action.should_revise and action.issue_id is None
    }
    if verification.accepted and not required_legacy_indices.issubset(
        satisfied_indices
    ):
        raise ValueError(
            "Accepted story revision did not mark every required repair action satisfied"
        )
    repair_issue_ids = {
        action.issue_id
        for action in repair_plan.actions
        if action.issue_id is not None
    }
    required_issue_ids = {
        action.issue_id
        for action in repair_plan.actions
        if action.should_revise and action.issue_id is not None
    }
    previous_violation_ids = {
        violation.issue_id
        for violation in (
            previous_verification.violations
            if previous_verification is not None
            else []
        )
        if violation.issue_id is not None
    }
    satisfied_issue_ids = set(verification.satisfied_issue_ids)
    allowed_satisfied_ids = repair_issue_ids | previous_violation_ids
    if not satisfied_issue_ids.issubset(allowed_satisfied_ids):
        raise ValueError("Story Revision Verifier returned an unknown satisfied issue ID")
    violation_issue_ids = [
        violation.issue_id for violation in verification.violations
    ]
    if any(issue_id is None for issue_id in violation_issue_ids):
        raise ValueError("Story Revision Verifier violations require a stable issue ID")
    concrete_violation_ids = {
        issue_id for issue_id in violation_issue_ids if issue_id is not None
    }
    if len(concrete_violation_ids) != len(violation_issue_ids):
        raise ValueError("Story Revision Verifier repeated a violation issue ID")
    if satisfied_issue_ids & concrete_violation_ids:
        raise ValueError(
            "Story Revision Verifier cannot satisfy and reject the same issue ID"
        )
    carried_issue_ids = required_issue_ids | previous_violation_ids
    if verification.accepted and not carried_issue_ids.issubset(
        satisfied_issue_ids
    ):
        raise ValueError(
            "Accepted story revision did not satisfy every repair or prior violation ID"
        )
    if not verification.accepted and not carried_issue_ids.issubset(
        satisfied_issue_ids | concrete_violation_ids
    ):
        raise ValueError(
            "Rejected story revision dropped a repair or prior violation ID"
        )
    known_scenes = {scene.sequence for scene in outline.scenes}
    invalid_scenes = sorted(
        {
            sequence
            for violation in verification.violations
            for sequence in violation.scene_sequences
            if sequence not in known_scenes
        }
    )
    if invalid_scenes:
        raise ValueError(
            "Story Revision Verifier referenced unknown scenes: "
            + ", ".join(str(value) for value in invalid_scenes)
        )
    unscoped_categories = [
        violation.category
        for violation in verification.violations
        if not violation.scene_sequences and not violation.metadata_targets
    ]
    if unscoped_categories:
        raise ValueError(
            "Story Revision Verifier violations must identify at least one repairable "
            "scene or global metadata target; independent ScriptReviewReport delivery "
            "is not a repair violation: "
            + ", ".join(unscoped_categories)
        )


def verify_story_revision(
    request: CreateProjectRequest,
    original_outline: EpisodeOutline,
    revised_outline: EpisodeOutline,
    review_report: ScriptReviewReport,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    client: LanguageProviderClient | None,
    *,
    selected_narrative_skill_ids: tuple[str, ...] = (),
    selected_method_skill_ids: tuple[str, ...] = (),
    retry_feedback: str | None = None,
    previous_verification: StoryRevisionVerification | None = None,
    issue_ledger: list[StoryIssueLedgerEntry] | None = None,
) -> StoryRevisionVerificationResult:
    """Verify the repair and cold-read the revised screenplay in one pass."""

    if client is None:
        return StoryRevisionVerificationResult(
            verification=None,
            used=False,
            warnings=["Story Revision Verifier 未配置，保留结构校验后的修订稿。"],
        )
    instructions, input_text = _story_revision_verifier_prompt(
        request,
        original_outline,
        revised_outline,
        review_report,
        repair_plan,
        source_contract,
        selected_narrative_skill_ids,
        selected_method_skill_ids,
        retry_feedback,
        previous_verification,
        issue_ledger,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=6000,
        )
        verification = StoryRevisionVerification.model_validate_json(
            _strip_json_fence(output)
        )
        warnings: list[str] = []
        if verification.duration_fit_assessment is None:
            verification = verification.model_copy(
                deep=True,
                update={
                    "duration_fit_assessment": _unresolved_duration_fit_assessment(
                        request,
                        revised_outline.story_intent,
                        verifier_name="Story Revision Verifier",
                    )
                },
            )
            warnings.append(
                "Story Revision Verifier 未返回 duration_fit_assessment；"
                "已将本次时长判断显式记录为 UNRESOLVED 并交回 Leader，"
                "不改写其剧情验收结论，也不自动开启新一轮修订。"
            )
        verification = normalize_story_revision_verification_issue_ids(
            repair_plan,
            verification,
        )
        _validate_story_revision_verification(
            revised_outline,
            repair_plan,
            verification,
            previous_verification=previous_verification,
        )
        return StoryRevisionVerificationResult(
            verification=verification,
            used=True,
            warnings=warnings,
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRevisionVerificationResult(
            verification=None,
            used=False,
            warnings=[
                "Story Revision Verifier 不可用，保留结构校验后的修订稿："
                + _compact_error(error)
            ],
        )


def direct_story_scene(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff: SceneContinuitySummary,
    client: LanguageProviderClient | None,
    review_handoff: list[dict[str, object]] | None = None,
    retry_feedback: str | None = None,
    selected_method_skill_ids: tuple[str, ...] = (),
) -> SceneDirectionResult:
    """Direct exactly one scene so callers can persist a recovery point after it."""

    if client is None:
        directed = _fallback_directed_scene_plan(request, outline, scene, handoff)
        _validate_directed_scene_plan(outline, scene, directed)
        return SceneDirectionResult(
            scene=directed,
            repaired=False,
            used_fallback=True,
            retry_required=False,
            retry_feedback=None,
            warnings=[
                f"场景 {scene.sequence:02d} 未调用语言模型，仅建立了本地分镜结构骨架。"
            ],
        )
    instructions, input_text = _scene_director_prompt(
        request,
        outline,
        scene,
        handoff,
        review_handoff,
        retry_feedback,
        selected_method_skill_ids,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=10000,
        )
        directed = SceneShotPlan.model_validate_json(_strip_json_fence(output))
        _validate_directed_scene_plan(outline, scene, directed)
        return SceneDirectionResult(
            scene=directed,
            repaired=False,
            used_fallback=False,
            retry_required=False,
            retry_feedback=None,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return SceneDirectionResult(
            scene=None,
            repaired=False,
            used_fallback=False,
            retry_required=True,
            retry_feedback=_compact_error(error),
            warnings=[],
        )


def assemble_multi_agent_planning_result(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    directed_scenes: list[SceneShotPlan],
    *,
    source_contract: StorySourceContract | None = None,
    outline_source: str,
    critic_used: bool,
    revision_rounds: int,
    used_scene_repair: bool,
    used_scene_fallback: bool,
    warnings: list[str],
    review_report: ScriptReviewReport | None,
    review_disposition: ScriptReviewDisposition | None = None,
    final_cold_read_report: ScriptReviewReport | None = None,
) -> ProductionPlanningResult:
    plan = _assemble_production_plan(outline, directed_scenes)
    resolved_source_contract = source_contract or build_story_source_contract(request)
    _validate_locked_dialogue(request, resolved_source_contract, plan)
    planner = f"multi-agent:{outline_source}"
    if critic_used:
        planner += " + script verifier"
    if revision_rounds:
        planner += f" revised-{revision_rounds}x"
    planner += " + scene directors"
    if used_scene_repair:
        planner += " repaired"
    if used_scene_fallback:
        planner += " mixed-fallback"
    return ProductionPlanningResult(
        plan=plan,
        planner=planner,
        warnings=list(dict.fromkeys(warnings)),
        review_report=review_report,
        review_disposition=review_disposition,
        final_cold_read_report=final_cold_read_report,
    )


def assemble_script_only_planning_result(
    outline: EpisodeOutline,
    *,
    outline_source: str,
    critic_used: bool,
    revision_rounds: int,
    warnings: list[str],
    review_report: ScriptReviewReport | None,
    review_disposition: ScriptReviewDisposition | None = None,
    final_cold_read_report: ScriptReviewReport | None = None,
) -> ProductionPlanningResult:
    """Materialize an approved screenplay without inventing downstream shot work."""

    planner = f"multi-agent:{outline_source}"
    if critic_used:
        planner += " + script verifier"
    if revision_rounds:
        planner += f" revised-{revision_rounds}x"
    planner += " + script-only"
    return ProductionPlanningResult(
        plan=ProductionPlan(
            title=outline.title,
            scene_heading=outline.scenes[0].heading,
            scene_summary=outline.story_summary,
            target_duration_seconds=outline.target_duration_seconds,
            lines=outline.lines,
            characters=outline.characters,
            locations=outline.locations,
            props=outline.props,
            style=outline.style,
            ending_contract=outline.ending_contract,
            story_intent=outline.story_intent,
            content_planning_output=ContentPlanningOutput.SCRIPT_ONLY,
            scenes=outline.scenes,
            shots=[],
        ),
        planner=planner,
        warnings=list(dict.fromkeys(warnings)),
        review_report=review_report,
        review_disposition=review_disposition,
        final_cold_read_report=final_cold_read_report,
    )


def create_production_plan(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> ProductionPlanningResult:
    if client is not None:
        raise ValueError(
            "Language-backed production planning must run through an AgentRun-owned "
            "Content Planning Job so blueprint, scenes, review, and repair progress are "
            "persisted incrementally. create_production_plan only builds a local scaffold."
        )
    return ProductionPlanningResult(
        plan=_fallback_plan(request),
        planner="deterministic-structure-scaffold-v3",
        warnings=[
            "仅建立不补写创意的本地结构骨架；语言创作必须由 AgentRun "
            "持有的 Content Planning Job 逐步完成。"
        ],
    )


def _asset(
    *,
    project_id: str,
    suffix: str,
    kind: AssetKind,
    name: str,
    bible_entity_id: str,
    descriptor: str,
    brief: CoreAssetBrief,
    planner: str,
    created_at: str,
) -> Asset:
    version_id = f"{project_id}-asset-{suffix}-v1"
    return Asset(
        id=f"{project_id}-asset-{suffix}",
        project_id=project_id,
        kind=kind,
        name=name,
        bible_entity_id=bible_entity_id,
        brief=brief.model_copy(deep=True),
        current_version_id=version_id,
        versions=[
            AssetVersion(
                id=version_id,
                version=1,
                origin_type=OriginType.AI_GENERATED,
                descriptor=descriptor,
                media_type=AssetMediaType.TEXT_REFERENCE,
                source=AssetSourceRecord(
                    source_type=OriginType.AI_GENERATED,
                    source_label="自动制片规划",
                    provider=(
                        "openai-compatible"
                        if planner.startswith("gpt-5.6-sol")
                        else "local"
                    ),
                    model=planner,
                    created_at=created_at,
                ),
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                created_at=created_at,
            )
        ],
    )


def _brief_values(*values: str) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _canonical_scene_screenplay(
    scene: PlannedSceneContract,
    scene_lines: list[PlannedDialogueLine],
) -> str:
    existing = scene.screenplay_text.strip()
    # Scene Writer owns the human-readable screenplay.  Its playable_actions are
    # semantic intentions, so an exact substring check here would discard valid
    # compression, reordering, and visual substitution.  SCRIPT_LOCKED verbatim
    # dialogue is validated when the scene patch is accepted.
    if existing:
        return existing
    blocks = [f"【{scene.heading}】"]
    for index in range(max(len(scene.playable_actions), len(scene_lines))):
        if index < len(scene.playable_actions):
            blocks.append(f"动作/反应/情绪：{scene.playable_actions[index]}")
        if index < len(scene_lines):
            line = scene_lines[index]
            blocks.append(f"{line.speaker}：{line.text}")
    return "\n".join(blocks)


def build_project_document_from_plan(
    *,
    project_id: str,
    request: CreateProjectRequest,
    planning: ProductionPlanningResult,
    created_at: str,
    planning_run_id: str | None = None,
    source_contract: StorySourceContract | None = None,
    planning_status: PlanningRunStatus = PlanningRunStatus.COMPLETE,
    owner_agent_run_id: str | None = None,
) -> ProjectDocument:
    plan = planning.plan
    planned_lines = list(plan.lines)
    lines = [
        ScriptLine(
            id=f"{project_id}-line-{index:02d}",
            speaker=line.speaker,
            speaker_character_id=line.speaker_character_id,
            original_text=line.text,
            spoken_text=line.text,
            subtitle_text=line.text,
            audio_kind=line.audio_kind,
        )
        for index, line in enumerate(planned_lines, start=1)
    ]
    line_ids = {index: line.id for index, line in enumerate(lines, start=1)}
    planned_scenes = plan.scenes or [
        PlannedSceneContract(
            sequence=1,
            heading=plan.scene_heading,
            summary=plan.scene_summary,
            narrative_purpose="完成当前短片的完整叙事目的",
            target_duration_seconds=sum(shot.duration_seconds for shot in plan.shots),
            dialogue_line_indices=list(range(1, len(lines) + 1)),
            location_names=[plan.locations[0].name],
            continuity_in=SceneContinuitySummary(
                setting=plan.locations[0].name,
                open_threads=[plan.scene_summary],
            ),
            continuity_out=SceneContinuitySummary(
                setting=plan.locations[0].name,
                known_facts=[plan.scene_summary],
            ),
        )
    ]
    scene_id_by_sequence = {
        scene.sequence: f"{project_id}-scene-{scene.sequence:02d}"
        for scene in planned_scenes
    }
    screenplay_by_sequence = {
        scene.sequence: _canonical_scene_screenplay(
            scene,
            [
                planned_lines[index - 1]
                for index in scene.dialogue_line_indices
                if 1 <= index <= len(planned_lines)
            ],
        )
        for scene in planned_scenes
    }
    original_text = request.input_text.strip() or "\n".join(
        f"{line.speaker}：{line.original_text}" for line in lines
    )
    script = ScriptVersion(
        id=f"{project_id}-script-v1",
        project_id=project_id,
        version=1,
        source_mode=request.source_mode,
        original_text=original_text,
        screenplay_text="\n\n".join(
            screenplay_by_sequence[scene.sequence] for scene in planned_scenes
        ),
        spoken_text="\n".join(line.spoken_text for line in lines),
        subtitle_text="\n".join(line.subtitle_text for line in lines),
        lines=lines,
        structured_scenes=[
            StructuredScene(
                id=scene_id_by_sequence[scene.sequence],
                heading=scene.heading,
                summary=scene.summary,
                screenplay_text=screenplay_by_sequence[scene.sequence],
                line_ids=[
                    line_ids[index]
                    for index in scene.dialogue_line_indices
                    if index in line_ids
                ],
                narrative_purpose=scene.narrative_purpose,
                target_duration_seconds=scene.target_duration_seconds,
                entry_state=scene.continuity_in.model_copy(deep=True),
                exit_state=scene.continuity_out.model_copy(deep=True),
            )
            for scene in planned_scenes
        ],
        review_report=(
            planning.review_report.model_copy(deep=True)
            if planning.review_report is not None
            else None
        ),
        review_disposition=(
            planning.review_disposition.model_copy(deep=True)
            if planning.review_disposition is not None
            else None
        ),
        final_cold_read_report=(
            planning.final_cold_read_report.model_copy(deep=True)
            if planning.final_cold_read_report is not None
            else None
        ),
        ending_contract=(
            planning.plan.ending_contract.model_copy(deep=True)
            if planning.plan.ending_contract is not None
            else None
        ),
        story_intent=(
            planning.plan.story_intent.model_copy(deep=True)
            if planning.plan.story_intent is not None
            else None
        ),
        source_contract=(
            source_contract.model_copy(deep=True)
            if source_contract is not None
            else None
        ),
        planning_run_id=planning_run_id,
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )

    planned_characters = list(plan.characters)
    character_profiles = [
        CharacterProfile(
            id=character.character_id or f"character-{index:02d}",
            name=character.name,
            age=character.age,
            appearance=character.appearance,
            wardrobe=character.wardrobe,
            performance=character.performance,
            voice_identity=character.voice_identity,
        )
        for index, character in enumerate(planned_characters, start=1)
    ]
    location_profiles = [
        LocationProfile(
            id=location.location_id or f"location-{index:02d}",
            name=location.name,
            layout=location.layout,
            lighting=location.lighting,
            palette=location.palette,
            key_views=location.key_views,
        )
        for index, location in enumerate(plan.locations, start=1)
    ]
    prop_profiles = [
        PropProfile(
            id=prop.prop_id or f"prop-{index:02d}",
            name=prop.name,
            appearance=prop.appearance,
            states=[],
        )
        for index, prop in enumerate(plan.props, start=1)
    ]
    bible = ProjectBibleVersion(
        id=f"{project_id}-bible-v1",
        project_id=project_id,
        version=1,
        characters=character_profiles,
        locations=location_profiles,
        props=prop_profiles,
        style=StyleProfile(
            medium=plan.style.medium,
            genre=plan.style.genre,
            cinematography=plan.style.cinematography,
            color=plan.style.color,
            texture=plan.style.texture,
            forbidden=plan.style.forbidden,
        ),
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )

    script_only = request.content_planning_output == ContentPlanningOutput.SCRIPT_ONLY
    assets: list[Asset] = []
    asset_characters: list[PlannedCharacter] = (
        [] if script_only else planned_characters
    )
    for index, character in enumerate(asset_characters, start=1):
        age_descriptor = f"{character.age}岁；" if character.age > 0 else ""
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"character-{index:02d}",
                kind=AssetKind.CHARACTER,
                name=f"{character.name}定妆",
                bible_entity_id=character_profiles[index - 1].id,
                descriptor=(
                    f"{age_descriptor}{character.appearance}；{character.wardrobe}；"
                    f"表演：{character.performance}；声线：{character.voice_identity}"
                ),
                brief=CoreAssetBrief(
                    identity_anchor=(
                        f"{character.name.strip()}；"
                        f"{age_descriptor}{character.appearance.strip()}"
                    ).strip(),
                    tone_or_temperament=character.performance.strip(),
                    visual_attributes=_brief_values(character.appearance),
                    wardrobe_or_materials=_brief_values(character.wardrobe),
                    background_constraints=[
                        "核心资产图不混入未声明的剧情场景与临时状态"
                    ],
                    texture_constraints=(
                        []
                        if plan.style.texture.strip() == UNSPECIFIED_STYLE_VALUE
                        else _brief_values(plan.style.texture)
                    ),
                    continuity_anchors=_brief_values(
                        character.appearance,
                        character.wardrobe,
                    ),
                    strategy_rationale=(
                        "初始保留轻量母版；Asset Agent 按重要性、跨镜复用和"
                        "连续性风险决定是否升级标准视图"
                    ),
                ),
                planner=planning.planner,
                created_at=created_at,
            )
        )
    asset_locations: list[PlannedLocation] = [] if script_only else plan.locations
    for index, location in enumerate(asset_locations, start=1):
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"location-{index:02d}",
                kind=AssetKind.LOCATION,
                name=location.name,
                bible_entity_id=location_profiles[index - 1].id,
                descriptor=(
                    f"布局：{location.layout}；灯光：{location.lighting}；色彩：{location.palette}"
                ),
                brief=CoreAssetBrief(
                    identity_anchor=(
                        f"{location.name.strip()}；{location.layout.strip()}"
                    ).strip(),
                    tone_or_temperament=location.lighting.strip(),
                    visual_attributes=_brief_values(
                        location.layout,
                        location.lighting,
                        location.palette,
                    ),
                    background_constraints=["保持空间几何、轴线与关键区域关系可识别"],
                    texture_constraints=(
                        []
                        if plan.style.texture.strip() == UNSPECIFIED_STYLE_VALUE
                        else _brief_values(plan.style.texture)
                    ),
                    continuity_anchors=_brief_values(
                        location.layout,
                        location.palette,
                        *location.key_views,
                    ),
                    named_views_or_zones=_brief_values(*location.key_views),
                    strategy_rationale=(
                        "初始保留轻量母版；Asset Agent 按多镜头复用、运镜路径"
                        "与交互区需求决定是否升级场景覆盖"
                    ),
                ),
                planner=planning.planner,
                created_at=created_at,
            )
        )
    asset_props: list[PlannedProp] = [] if script_only else plan.props
    for index, prop in enumerate(asset_props, start=1):
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"prop-{index:02d}",
                kind=AssetKind.PROP,
                name=prop.name,
                bible_entity_id=prop_profiles[index - 1].id,
                descriptor=prop.appearance,
                brief=CoreAssetBrief(
                    identity_anchor=(
                        f"{prop.name.strip()}；{prop.appearance.strip()}"
                    ).strip(),
                    visual_attributes=_brief_values(prop.appearance),
                    texture_constraints=(
                        []
                        if plan.style.texture.strip() == UNSPECIFIED_STYLE_VALUE
                        else _brief_values(plan.style.texture)
                    ),
                    continuity_anchors=_brief_values(prop.appearance),
                    strategy_rationale=(
                        "默认使用轻量道具母版；仅在手持、状态变化或特写时"
                        "由 Asset Agent 增补对应视图"
                    ),
                ),
                planner=planning.planner,
                created_at=created_at,
            )
        )

    assets_by_bible_entity_id = {
        asset.bible_entity_id: asset
        for asset in assets
        if asset.bible_entity_id is not None
    }
    subject_assets = {
        **{
            character.name: assets_by_bible_entity_id[profile.id]
            for character, profile in zip(asset_characters, character_profiles)
        },
        **{
            location.name: assets_by_bible_entity_id[profile.id]
            for location, profile in zip(asset_locations, location_profiles)
        },
        **{
            prop.name: assets_by_bible_entity_id[profile.id]
            for prop, profile in zip(asset_props, prop_profiles)
        },
    }
    shots = [] if script_only else [
        ShotPlan(
            id=f"{project_id}-shot-{sequence:02d}",
            project_id=project_id,
            sequence=sequence,
            scene_id=scene_id_by_sequence.get(shot.scene_sequence),
            duration_seconds=shot.duration_seconds,
            narrative_purpose=shot.narrative_purpose,
            visual=shot.visual,
            framing=shot.framing,
            camera=shot.camera,
            audio_intent=shot.audio_intent,
            dialogue_line_ids=[
                line_ids[index]
                for index in shot.dialogue_line_indices
                if index in line_ids
            ],
            timeline_beats=[
                ShotTimelineBeat(
                    start_seconds=beat.start_seconds,
                    end_seconds=beat.end_seconds,
                    visual=beat.visual,
                    framing=beat.framing,
                    camera=beat.camera,
                    action=beat.action,
                    performance=beat.performance,
                    emotion=beat.emotion,
                    dialogue_line_ids=[
                        line_ids[index]
                        for index in beat.dialogue_line_indices
                        if index in line_ids
                    ],
                    sound=beat.sound,
                    references=[
                        ShotReferenceCue(
                            subject_asset_id=subject_assets[cue.subject_name].id,
                            alias=cue.alias,
                            required_tags=list(cue.required_tags),
                            purpose=cue.purpose,
                        )
                        for cue in beat.references
                    ],
                )
                for beat in shot.timeline_beats
            ],
            continuity=ContinuityState(
                character_positions=shot.character_positions,
                eyeline=shot.eyeline,
                prop_state_in=shot.prop_state_in,
                prop_state_out=shot.prop_state_out,
                action_in=shot.action_in,
                action_out=shot.action_out,
            ),
            transition_out=shot.transition_out,
            merge_candidate=shot.merge_candidate,
            local_rework_risk=shot.local_rework_risk,
            approval_status=ApprovalStatus.APPROVED,
            freshness=Freshness.CURRENT,
        )
        for sequence, shot in enumerate(plan.shots, start=1)
    ]
    capability = CapabilitySnapshot(
        provider="mock",
        model="deterministic-media-v1",
        resolution="432x768",
        native_audio=True,
        supports_frame_reference=True,
        supports_asset_reference=True,
    )
    execution_plan = [
        ExecutionPlanEntry(
            shot_id=shot.id,
            strategy=(
                ExecutionStrategy.AUDIO_DRIVEN
                if shot.dialogue_line_ids
                else ExecutionStrategy.DIRECT_AUDIOVISUAL
            ),
            rationale="自动规划先以供应商中立 Mock 验证完整编排；真实执行再按能力画像选路",
            skipped_optional_steps=["真实视频生成", "可选关键帧生成", "可选外部声音修复"],
            provider="mock",
            capability_snapshot=capability.model_copy(deep=True),
            candidate_count=1,
            fallback=(
                "Leader 读取当前生成结果与错误证据后，自主决定重试当前镜头、"
                "调整输入或路线、局部修复，或停止并说明缺失条件；不自动进入固定循环。"
            ),
        )
        for shot in shots
    ]
    asset_versions = {
        asset.name: asset.versions[0].id
        for asset in assets
    }
    reference_plan: list[ReferencePlanEntry] = []
    planned_reference_shots = [] if script_only else plan.shots
    for shot, planned_shot in zip(shots, planned_reference_shots, strict=True):
        requirements: list[ReferenceRequirement] = []
        for index, need in enumerate(planned_shot.reference_needs, start=1):
            subject = subject_assets.get(need.subject_name)
            if subject is None:
                raise ValueError(
                    f"Planned reference subject has no canonical asset: {need.subject_name}"
                )
            requirements.append(
                ReferenceRequirement(
                    id=f"{shot.id}-reference-{index:02d}",
                    subject_asset_id=subject.id,
                    acceptable_roles=list(need.acceptable_roles),
                    preferred_angles=list(need.preferred_angles),
                    preferred_framings=list(need.preferred_framings),
                    required_tags=list(dict.fromkeys(need.tags)),
                    purpose=need.purpose,
                    required=need.required,
                )
            )
        requested_asset_ids = {
            requirement.subject_asset_id for requirement in requirements
        }
        bindings = [
            ReferenceBinding(
                kind=asset.kind,
                target_id=asset_versions[asset.name],
                purpose=_PLANNED_REFERENCE_BINDING_PURPOSE,
                required=asset.kind != AssetKind.PROP,
            )
            for asset in assets
            if asset.id in requested_asset_ids
        ]
        reference_plan.append(
            ReferencePlanEntry(
                shot_id=shot.id,
                bindings=bindings,
                capability_valid=True,
                validation_notes=[
                    "Mock 阶段保存供应商中立绑定；真实 Provider 提交前重新校验",
                    "逐镜资产需求由规划 Agent 显式给出；空列表表示本镜不需要引用资产",
                ],
                requirements=requirements,
                max_reference_images=min(9, max(3, len(requirements))),
                planning_notes="asset-manager:agent-explicit",
            )
        )

    project = ProjectRecord(
        id=project_id,
        name=(request.name or plan.title).strip(),
        source_mode=request.source_mode,
        run_mode=request.run_mode,
        production_profile=request.production_profile,
        content_planning_output=request.content_planning_output,
        target_duration_seconds=(
            script.story_intent.duration_contract.target_seconds
            if script.story_intent is not None
            and script.story_intent.duration_contract is not None
            and script.story_intent.duration_contract.target_seconds is not None
            else request.resolved_target_duration_seconds()
        ),
        input_text=request.input_text,
        input_images=[image.model_copy(deep=True) for image in request.input_images],
        linked_asset_project_ids=list(request.linked_asset_project_ids),
        stage=(ProjectStage.SCRIPT_READY if script_only else ProjectStage.PLANNED),
        current_script_version_id=script.id,
        current_bible_version_id=bible.id,
        created_at=created_at,
        updated_at=created_at,
    )
    document = ProjectDocument(
        project=project,
        planning=ProjectPlanningMetadata(
            run_id=planning_run_id,
            status=planning_status,
            planner=planning.planner,
            warnings=list(dict.fromkeys(planning.warnings)),
            owner_agent_run_id=owner_agent_run_id,
            planning_job_kind=(
                "CONTENT_PLANNING"
                if planning_run_id is not None and owner_agent_run_id is not None
                else None
            ),
        ),
        script_versions=[script],
        bible_versions=[bible],
        assets=assets,
        shots=shots,
        execution_plan=execution_plan,
        reference_plan=reference_plan,
        manifests=[],
    )
    refresh_lightweight_reference_plans(document)
    return document


def refresh_lightweight_reference_plans(document: ProjectDocument) -> bool:
    """Reconcile automatic bindings from explicit structured asset ids only.

    An empty requirement/cue/action structure is an intentional request for no references. Text,
    names, speakers, and neighboring shots never add an asset implicitly.
    """

    assets_by_version = {
        version.id: asset
        for asset in document.assets
        for version in asset.versions
    }
    plans_by_shot = {item.shot_id: item for item in document.reference_plan}
    changed = False

    for shot in document.shots:
        plan = plans_by_shot.get(shot.id)
        if plan is None:
            continue

        existing_by_asset: dict[str, ReferenceBinding] = {}
        unresolved: list[ReferenceBinding] = []
        relevant_asset_ids = {
            requirement.subject_asset_id for requirement in plan.requirements
        }
        relevant_asset_ids.update(beat.actor_asset_id for beat in plan.action_beats)
        for interaction in plan.interactions:
            relevant_asset_ids.add(interaction.actor_asset_id)
            relevant_asset_ids.add(interaction.target_asset_id)
        relevant_asset_ids.update(
            cue.subject_asset_id
            for beat in shot.timeline_beats
            for cue in beat.references
        )
        for binding in plan.bindings:
            asset = assets_by_version.get(binding.target_id)
            if asset is None:
                unresolved.append(binding.model_copy(deep=True))
                continue
            if (
                binding.purpose in _AUTOMATIC_REFERENCE_BINDING_PURPOSES
                and asset.id not in relevant_asset_ids
            ):
                continue
            existing_by_asset.setdefault(asset.id, binding)

        selected_assets = [
            asset
            for asset in document.assets
            if asset.id in existing_by_asset or asset.id in relevant_asset_ids
        ]

        bindings: list[ReferenceBinding] = []
        for asset in selected_assets:
            existing = existing_by_asset.get(asset.id)
            target_id = asset.approved_version_id or asset.current_version_id
            bindings.append(
                ReferenceBinding(
                    kind=asset.kind,
                    target_id=target_id,
                    purpose=(
                        existing.purpose
                        if existing is not None
                        else _ENRICHED_REFERENCE_BINDING_PURPOSE
                    ),
                    required=(
                        existing.required
                        if existing is not None
                        else True
                    ),
                )
            )
        bindings.extend(unresolved)
        max_images = min(9, max(plan.max_reference_images, len(bindings), 1))
        before = [item.model_dump(mode="json") for item in plan.bindings]
        after = [item.model_dump(mode="json") for item in bindings]
        if before != after or plan.max_reference_images != max_images:
            plan.bindings = bindings
            plan.max_reference_images = max_images
            changed = True
    return changed


_PLANNED_REFERENCE_BINDING_PURPOSE = "自动规划的身份、场景或道具连续性参考"
_ENRICHED_REFERENCE_BINDING_PURPOSE = "自动补全的当前镜头身份、空间或道具连续性参考"
_AUTOMATIC_REFERENCE_BINDING_PURPOSES = {
    _PLANNED_REFERENCE_BINDING_PURPOSE,
    _ENRICHED_REFERENCE_BINDING_PURPOSE,
}
