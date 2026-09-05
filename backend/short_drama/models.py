"""Typed domain and API models for the short-drama production workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from short_drama.reference_aliases import normalize_reference_alias


class DomainModel(BaseModel):
    """Base model shared by persisted domain documents and API payloads."""

    model_config = ConfigDict(extra="forbid")


class SourceMode(StrEnum):
    AUTO_ORIGINAL = "AUTO_ORIGINAL"
    IMAGE_SEEDED = "IMAGE_SEEDED"
    IDEA_EXPANSION = "IDEA_EXPANSION"
    SCRIPT_LOCKED = "SCRIPT_LOCKED"


class RunMode(StrEnum):
    AUTO_DRAFT = "AUTO_DRAFT"
    GUIDED = "GUIDED"


class ProductionProfile(StrEnum):
    GENERAL_VIDEO = "GENERAL_VIDEO"
    AI_SHORT_DRAMA = "AI_SHORT_DRAMA"
    DIGITAL_HUMAN = "DIGITAL_HUMAN"
    ANIME = "ANIME"


class ProductionExecutionTarget(StrEnum):
    MOCK = "MOCK"
    REAL_H3 = "REAL_H3"


class ContentPlanningOutput(StrEnum):
    SCRIPT_ONLY = "SCRIPT_ONLY"
    SCRIPT_AND_SHOTS = "SCRIPT_AND_SHOTS"


class PlanningRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"


class PlanningRunOperation(StrEnum):
    INITIAL = "INITIAL"
    SCRIPT_REVISION = "SCRIPT_REVISION"


class ScriptRevisionOrigin(StrEnum):
    USER_FEEDBACK = "USER_FEEDBACK"
    PRODUCTION_CONSTRAINT = "PRODUCTION_CONSTRAINT"


class StoryVerificationSource(StrEnum):
    SCRIPT_VERIFIER = "SCRIPT_VERIFIER"
    REVISION_VERIFIER = "REVISION_VERIFIER"


class StoryVerificationResolutionDecision(StrEnum):
    CONTINUE_FOCUSED_REVISION = "CONTINUE_FOCUSED_REVISION"
    RETURN_TO_STORY_ARCHITECT = "RETURN_TO_STORY_ARCHITECT"
    ACCEPT_TRADEOFF = "ACCEPT_TRADEOFF"


class PlanningRunStep(StrEnum):
    STORY_ORIENTATION = "STORY_ORIENTATION"
    STORY_ARCHITECT = "STORY_ARCHITECT"
    SCENE_WRITER = "SCENE_WRITER"
    SCRIPT_VERIFIER = "SCRIPT_VERIFIER"
    # Compatibility-only cursor for jobs persisted before the producer/verifier
    # topology made the active responsibility explicit.
    SCRIPT_CRITIC = "SCRIPT_CRITIC"
    REVIEW_ROUTER = "REVIEW_ROUTER"
    SOURCE_CONTRACT = "SOURCE_CONTRACT"
    STORY_REVISION = "STORY_REVISION"
    STORY_METADATA_REVISION = "STORY_METADATA_REVISION"
    REVISION_SCOPE = "REVISION_SCOPE"
    SCENE_REVISION = "SCENE_REVISION"
    REVISION_VERIFIER = "REVISION_VERIFIER"
    REVISION_JUDGE = "REVISION_JUDGE"
    FINAL_COLD_READ = "FINAL_COLD_READ"
    SCENE_DIRECTOR = "SCENE_DIRECTOR"
    FINALIZE = "FINALIZE"
    COMPLETE = "COMPLETE"


class EpisodeReviewStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class EpisodeReviewDimension(StrEnum):
    STORY_CLARITY = "STORY_CLARITY"
    SCENE_CAUSALITY = "SCENE_CAUSALITY"
    CROSS_SCENE_CONTINUITY = "CROSS_SCENE_CONTINUITY"
    CHARACTER_PROP_STATE = "CHARACTER_PROP_STATE"
    SOUND_SOURCE_SEMANTICS = "SOUND_SOURCE_SEMANTICS"
    PACING = "PACING"
    OTHER = "OTHER"


class RevisionJudgeConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class Freshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class ProjectStage(StrEnum):
    INPUT = "INPUT"
    SCRIPT_READY = "SCRIPT_READY"
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    REVIEW = "REVIEW"
    TIMELINE_READY = "TIMELINE_READY"
    EXPORTED = "EXPORTED"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OUTPUT_MISSING = "OUTPUT_MISSING"


class QaStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    REJECTED = "REJECTED"


class QaFailureCategory(StrEnum):
    TECHNICAL_MEDIA = "TECHNICAL_MEDIA"
    SPATIAL_CONTINUITY = "SPATIAL_CONTINUITY"
    CONTACT_CONTINUITY = "CONTACT_CONTINUITY"
    CHARACTER_ORIENTATION = "CHARACTER_ORIENTATION"
    COMPOSITION_QUALITY = "COMPOSITION_QUALITY"
    IDENTITY_CONTINUITY = "IDENTITY_CONTINUITY"
    PROP_CONTINUITY = "PROP_CONTINUITY"
    DIALOGUE_AUDIO = "DIALOGUE_AUDIO"
    OTHER = "OTHER"


class ScriptReviewSeverity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ScriptReviewDispositionAction(StrEnum):
    """Default autonomous action chosen by the active Production Skill."""

    REVISE_STORY = "REVISE_STORY"
    CONTINUE_WITH_HANDOFF = "CONTINUE_WITH_HANDOFF"
    CONTINUE = "CONTINUE"


class DurationTargetOrigin(StrEnum):
    USER_DECLARED = "USER_DECLARED"
    PROFILE_DEFAULT = "PROFILE_DEFAULT"
    SOURCE_DECLARED = "SOURCE_DECLARED"
    MASTER_AUDIO = "MASTER_AUDIO"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class DurationAuthority(StrEnum):
    TARGET_LED = "TARGET_LED"
    SOURCE_LED = "SOURCE_LED"
    AUDIO_LED = "AUDIO_LED"


class DurationFidelityPolicy(StrEnum):
    PRESERVE_VERBATIM = "PRESERVE_VERBATIM"
    PRESERVE_STORY_COMMITMENTS = "PRESERVE_STORY_COMMITMENTS"
    REWRITABLE = "REWRITABLE"


class DurationMismatchResolution(StrEnum):
    REVISE_SCRIPT = "REVISE_SCRIPT"
    REPORT_NATURAL_RUNTIME = "REPORT_NATURAL_RUNTIME"
    FOLLOW_MASTER_AUDIO = "FOLLOW_MASTER_AUDIO"
    ASK_USER = "ASK_USER"


class DurationAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    OUTSIDE_ACCEPTED_RANGE = "OUTSIDE_ACCEPTED_RANGE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


class DurationRange(DomainModel):
    minimum_seconds: float = Field(gt=0.0)
    maximum_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_order(self) -> DurationRange:
        if self.minimum_seconds > self.maximum_seconds:
            raise ValueError("Duration range minimum cannot exceed maximum")
        return self


class DurationRhythmSection(DomainModel):
    label: str = Field(min_length=1, max_length=120)
    narrative_function: str = Field(min_length=1, max_length=1200)
    relative_density: str = Field(min_length=1, max_length=500)
    scene_sequences: list[int] = Field(default_factory=list[int])
    payoff_or_handoff: str | None = Field(default=None, min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_scene_sequences(self) -> DurationRhythmSection:
        if any(sequence < 1 for sequence in self.scene_sequences):
            raise ValueError("Duration rhythm scene sequences must be positive")
        if len(self.scene_sequences) != len(set(self.scene_sequences)):
            raise ValueError("Duration rhythm scene sequences must be unique")
        return self


class DurationContract(DomainModel):
    """Semantic runtime authority shared across story and production planning."""

    target_seconds: float | None = Field(default=None, gt=0.0)
    target_origin: DurationTargetOrigin
    authority: DurationAuthority
    acceptable_range_seconds: DurationRange | None = None
    source_fidelity_policy: DurationFidelityPolicy
    mismatch_resolution: DurationMismatchResolution
    decision_basis: str = Field(min_length=1, max_length=2000)
    rhythm_shape: list[DurationRhythmSection] = Field(
        default_factory=list[DurationRhythmSection]
    )
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> DurationContract:
        accepted = self.acceptable_range_seconds
        if (
            self.target_seconds is not None
            and accepted is not None
            and not (
                accepted.minimum_seconds
                <= self.target_seconds
                <= accepted.maximum_seconds
            )
        ):
            raise ValueError("Duration target must fall inside its acceptable range")
        if not self.user_overridable:
            raise ValueError("Duration decisions must remain user-overridable")
        if self.authority == DurationAuthority.AUDIO_LED:
            if self.target_origin != DurationTargetOrigin.MASTER_AUDIO:
                raise ValueError(
                    "Audio-led duration must identify Master Audio as its target origin"
                )
            if self.target_seconds is None:
                raise ValueError("Audio-led duration requires an exact Master Audio target")
            if self.mismatch_resolution != DurationMismatchResolution.FOLLOW_MASTER_AUDIO:
                raise ValueError(
                    "Audio-led duration mismatches must follow the bound Master Audio"
                )
        return self


class DurationFitAssessment(DomainModel):
    estimated_range_seconds: DurationRange | None = None
    alignment: DurationAlignment
    pressure_points: list[str] = Field(default_factory=list[str])
    concurrent_performance: list[str] = Field(default_factory=list[str])
    resolution: DurationMismatchResolution
    rationale: str = Field(min_length=1, max_length=2000)


class StoryReviewFollowup(StrEnum):
    """Agent-recommended continuation after a complete story read or repair check."""

    FINALIZE = "FINALIZE"
    FINAL_COLD_READ = "FINAL_COLD_READ"


class ReviewRepairOwner(StrEnum):
    """Smallest production layer that can reasonably address a review issue."""

    STORY_ARCHITECT = "STORY_ARCHITECT"
    SCENE_DIRECTOR = "SCENE_DIRECTOR"
    ASSET_AGENT = "ASSET_AGENT"
    VIDEO_DIRECTOR = "VIDEO_DIRECTOR"
    TIMELINE_AGENT = "TIMELINE_AGENT"
    USER_OPTIONAL = "USER_OPTIONAL"


class ContinuationPreference(StrEnum):
    """What the user explicitly asked for before the story Agent decides."""

    SERIES = "SERIES"
    STANDALONE = "STANDALONE"
    AGENT_DECIDES = "AGENT_DECIDES"


class ResolvedEndingMode(StrEnum):
    """The concrete episode shape selected by the Story Architect."""

    SERIES = "SERIES"
    STANDALONE = "STANDALONE"


class StoryRepairDiagnosis(StrEnum):
    """Where a routed story issue actually originates after a second look."""

    SOURCE_CONCEPT_GAP = "SOURCE_CONCEPT_GAP"
    OUTLINE_DEFECT = "OUTLINE_DEFECT"
    CRITIC_OVERREACH = "CRITIC_OVERREACH"
    USER_REQUEST = "USER_REQUEST"


class StoryMetadataTarget(StrEnum):
    """Global story state that a semantic repair must keep aligned."""

    TITLE = "TITLE"
    STORY_SUMMARY = "STORY_SUMMARY"
    STORY_INTENT = "STORY_INTENT"
    ENDING_CONTRACT = "ENDING_CONTRACT"
    PROJECT_BIBLE = "PROJECT_BIBLE"


class VideoInspectionScope(StrEnum):
    INTRA_SHOT = "INTRA_SHOT"
    SHOT_BOUNDARY = "SHOT_BOUNDARY"


class SemanticReviewStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    UNAVAILABLE = "UNAVAILABLE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class TakeComparisonDecision(StrEnum):
    SELECT_TAKE = "SELECT_TAKE"
    NONE_ACCEPTABLE = "NONE_ACCEPTABLE"
    UNAVAILABLE = "UNAVAILABLE"


class AudioContinuityAssessment(StrEnum):
    PASSED = "PASSED"
    REVIEW = "REVIEW"
    UNKNOWN = "UNKNOWN"


class ObservationConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DirectorRepairStrategy(StrEnum):
    PROMPT_RETRY = "PROMPT_RETRY"
    CHANGE_KEYFRAME = "CHANGE_KEYFRAME"
    SPLIT_SHOT = "SPLIT_SHOT"
    CHANGE_CAMERA = "CHANGE_CAMERA"


class DirectorRepairDecision(StrEnum):
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"


class TakeAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"


class OriginType(StrEnum):
    MANUAL = "MANUAL"
    UPLOAD = "UPLOAD"
    LIBRARY_IMPORT = "LIBRARY_IMPORT"
    LOCAL_DERIVED = "LOCAL_DERIVED"
    AI_GENERATED = "AI_GENERATED"
    AI_EDITED = "AI_EDITED"
    FIXTURE = "FIXTURE"


class AssetKind(StrEnum):
    CHARACTER = "CHARACTER"
    LOCATION = "LOCATION"
    PROP = "PROP"
    REFERENCE = "REFERENCE"


class AssetMediaType(StrEnum):
    TEXT_REFERENCE = "TEXT_REFERENCE"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class AssetPresentationMode(StrEnum):
    """Explicit rendering intent for an asset master; absence stays unknown."""

    PHYSICAL_MASTER = "PHYSICAL_MASTER"
    SCREEN_CONTENT_PLATE = "SCREEN_CONTENT_PLATE"


class AssetReferenceStrategy(StrEnum):
    """How much canonical visual coverage one asset needs for the current work."""

    LIGHTWEIGHT = "LIGHTWEIGHT"
    CHARACTER_STANDARD_VIEWS = "CHARACTER_STANDARD_VIEWS"
    SCENE_COVERAGE = "SCENE_COVERAGE"


class AssetStandardView(StrEnum):
    """Reusable view labels for canonical character, location, and prop images."""

    CANONICAL_MASTER = "CANONICAL_MASTER"
    CHARACTER_FRONT = "CHARACTER_FRONT"
    CHARACTER_LEFT_PROFILE = "CHARACTER_LEFT_PROFILE"
    CHARACTER_RIGHT_PROFILE = "CHARACTER_RIGHT_PROFILE"
    CHARACTER_BACK = "CHARACTER_BACK"
    CHARACTER_WARDROBE_DETAIL = "CHARACTER_WARDROBE_DETAIL"
    SCENE_MASTER = "SCENE_MASTER"
    SCENE_OVERHEAD = "SCENE_OVERHEAD"
    SCENE_REVERSE = "SCENE_REVERSE"
    SCENE_INTERACTION_ZONE = "SCENE_INTERACTION_ZONE"
    PROP_PRODUCT = "PROP_PRODUCT"
    PROP_IN_HAND = "PROP_IN_HAND"
    PROP_STATE = "PROP_STATE"


class AssetSheetInputPolicy(StrEnum):
    """Whether a local reference sheet may use explicitly selected draft versions."""

    APPROVED_ONLY = "APPROVED_ONLY"
    EXPLICIT_VERSIONS = "EXPLICIT_VERSIONS"


class ReferenceRole(StrEnum):
    CANONICAL_MASTER = "CANONICAL_MASTER"
    CHARACTER_FACE = "CHARACTER_FACE"
    CHARACTER_BODY = "CHARACTER_BODY"
    CHARACTER_PROFILE = "CHARACTER_PROFILE"
    CHARACTER_BACK = "CHARACTER_BACK"
    LOCATION_MASTER = "LOCATION_MASTER"
    LOCATION_REVERSE = "LOCATION_REVERSE"
    LOCATION_INTERACTION = "LOCATION_INTERACTION"
    PROP_PRODUCT = "PROP_PRODUCT"
    PROP_IN_HAND = "PROP_IN_HAND"
    PROP_STATE = "PROP_STATE"


class ReferenceAngle(StrEnum):
    FRONT = "FRONT"
    THREE_QUARTER_LEFT = "THREE_QUARTER_LEFT"
    THREE_QUARTER_RIGHT = "THREE_QUARTER_RIGHT"
    LEFT_PROFILE = "LEFT_PROFILE"
    RIGHT_PROFILE = "RIGHT_PROFILE"
    BACK = "BACK"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReferenceFraming(StrEnum):
    FACE_CLOSE_UP = "FACE_CLOSE_UP"
    CLOSE_UP = "CLOSE_UP"
    MEDIUM = "MEDIUM"
    FULL_BODY = "FULL_BODY"
    WIDE = "WIDE"
    DETAIL = "DETAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class InteractionEffector(StrEnum):
    LEFT_HAND = "LEFT_HAND"
    RIGHT_HAND = "RIGHT_HAND"
    BOTH_HANDS = "BOTH_HANDS"
    BODY = "BODY"
    GAZE = "GAZE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OperationKind(StrEnum):
    GENERATE = "GENERATE"
    RETAKE = "RETAKE"
    EXTEND = "EXTEND"
    GAP_FILL = "GAP_FILL"


class ExecutionStrategy(StrEnum):
    DIRECT_AUDIOVISUAL = "direct_audiovisual"
    IMAGE_GUIDED = "image_guided"
    AUDIO_DRIVEN = "audio_driven"
    HYBRID_REPAIR = "hybrid_repair"


class VideoGenerationRoute(StrEnum):
    DIRECT = "DIRECT"
    FRAME_FIRST = "FRAME_FIRST"
    FRAME_LAST = "FRAME_LAST"
    FRAME_FIRST_LAST = "FRAME_FIRST_LAST"
    REFERENCE = "REFERENCE"


class EvidencePurpose(StrEnum):
    CONTRACT_SMOKE = "CONTRACT_SMOKE"
    QUALITY_PROBE = "QUALITY_PROBE"
    PRODUCTION = "PRODUCTION"


class EvidenceStatus(StrEnum):
    AWAITING_OUTPUT = "AWAITING_OUTPUT"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REVIEWED = "REVIEWED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MockBehavior(StrEnum):
    SUCCESS = "SUCCESS"
    FAIL_ONCE = "FAIL_ONCE"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OUTPUT_MISSING = "OUTPUT_MISSING"


class AudioKind(StrEnum):
    DIALOGUE = "DIALOGUE"
    VOICEOVER = "VOICEOVER"
    AMBIENCE = "AMBIENCE"
    BREATH = "BREATH"
    EFFECT = "EFFECT"


class CreativeInputImage(DomainModel):
    local_path: str | None = Field(default=None, min_length=1, max_length=8192)
    external_url: str | None = Field(default=None, min_length=1, max_length=8192)
    descriptor: str = Field(default="用户提供的视觉种子", min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_one_source(self) -> CreativeInputImage:
        if (self.local_path is None) == (self.external_url is None):
            raise ValueError("Exactly one of local_path or external_url is required")
        return self


class ProjectRecord(DomainModel):
    id: str
    name: str
    source_mode: SourceMode
    run_mode: RunMode
    production_profile: ProductionProfile = ProductionProfile.AI_SHORT_DRAMA
    content_planning_output: ContentPlanningOutput = (
        ContentPlanningOutput.SCRIPT_AND_SHOTS
    )
    language: str = "zh-CN"
    aspect_ratio: str = "9:16"
    target_duration_seconds: float = 50.0
    input_text: str
    input_images: list[CreativeInputImage] = Field(
        default_factory=list[CreativeInputImage],
        max_length=8,
    )
    linked_asset_project_ids: list[str] = Field(
        default_factory=list[str],
        max_length=32,
    )
    voice_reference_revision: int = Field(default=0, ge=0)
    stage: ProjectStage
    current_script_version_id: str
    current_bible_version_id: str
    current_manifest_id: str | None = None
    created_at: str
    updated_at: str


class ScriptLine(DomainModel):
    id: str
    speaker: str
    speaker_character_id: str | None = None
    original_text: str
    spoken_text: str
    subtitle_text: str
    audio_kind: AudioKind


class SceneContinuitySummary(DomainModel):
    setting: str = Field(min_length=1, max_length=500)
    character_states: list[str] = Field(default_factory=list[str], max_length=20)
    prop_states: list[str] = Field(default_factory=list[str], max_length=20)
    known_facts: list[str] = Field(default_factory=list[str], max_length=20)
    open_threads: list[str] = Field(default_factory=list[str], max_length=20)


class StructuredScene(DomainModel):
    id: str
    heading: str
    summary: str
    line_ids: list[str]
    screenplay_text: str = ""
    narrative_purpose: str | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0.0)
    entry_state: SceneContinuitySummary | None = None
    exit_state: SceneContinuitySummary | None = None


class ScriptReviewIssue(DomainModel):
    issue_id: str | None = Field(default=None, min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    severity: ScriptReviewSeverity
    owner: ReviewRepairOwner
    scene_sequences: list[int] = Field(default_factory=list[int])
    observation: str = Field(min_length=1, max_length=1200)
    recommendation: str = Field(min_length=1, max_length=1200)


class ScriptReviewReport(DomainModel):
    summary: str = Field(min_length=1, max_length=2000)
    strengths: list[str] = Field(default_factory=list[str], max_length=12)
    issues: list[ScriptReviewIssue] = Field(default_factory=list[ScriptReviewIssue], max_length=30)
    performance_timing_assessment: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    duration_fit_assessment: DurationFitAssessment | None = None
    requires_revision: bool
    recommended_followup: StoryReviewFollowup = StoryReviewFollowup.FINALIZE
    followup_rationale: str | None = Field(default=None, max_length=1200)

    @model_validator(mode="after")
    def validate_issue_ids(self) -> ScriptReviewReport:
        issue_ids = [issue.issue_id for issue in self.issues if issue.issue_id is not None]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("Script review issue IDs must be unique")
        return self


class ScriptReviewIssueRoute(DomainModel):
    issue_index: int = Field(ge=1)
    owner: ReviewRepairOwner
    rationale: str = Field(min_length=1, max_length=600)


class ScriptReviewDisposition(DomainModel):
    """Skill-aware routing recommendation; users may always override it."""

    action: ScriptReviewDispositionAction
    issue_routes: list[ScriptReviewIssueRoute] = Field(
        default_factory=list[ScriptReviewIssueRoute],
        max_length=30,
    )
    rationale: str = Field(min_length=1, max_length=1200)
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_disposition(self) -> ScriptReviewDisposition:
        issue_indices = [route.issue_index for route in self.issue_routes]
        if len(issue_indices) != len(set(issue_indices)):
            raise ValueError("Each script-review issue may be routed only once")
        if not self.user_overridable:
            raise ValueError("Script-review routing must remain user-overridable")
        return self


class EndingContract(DomainModel):
    """Episode payoff and continuation policy protected across later revisions."""

    requested_preference: ContinuationPreference = ContinuationPreference.AGENT_DECIDES
    resolved_mode: ResolvedEndingMode
    episode_payoff: str = Field(min_length=1, max_length=1200)
    required_closures: list[str] = Field(default_factory=list[str], max_length=16)
    allowed_open_threads: list[str] = Field(default_factory=list[str], max_length=12)
    continuation_hook: str | None = Field(default=None, max_length=1200)
    final_button: str = Field(min_length=1, max_length=1200)
    decision_basis: str = Field(min_length=1, max_length=1200)


class StoryLockedDialogueLine(DomainModel):
    """One user-locked spoken line structured by the intake Agent or API caller."""

    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    audio_kind: AudioKind = AudioKind.DIALOGUE


class StorySourceContract(DomainModel):
    """Explicit user promises separated from creative choices left to the Agents."""

    explicit_commitments: list[str] = Field(min_length=1, max_length=30)
    explicit_prohibitions: list[str] = Field(default_factory=list[str], max_length=20)
    open_creative_choices: list[str] = Field(default_factory=list[str], max_length=20)
    authoritative_user_overrides: list[str] = Field(
        default_factory=list[str],
    )
    locked_dialogue: list[StoryLockedDialogueLine] = Field(
        default_factory=list[StoryLockedDialogueLine],
        max_length=5000,
    )
    continuation_preference: ContinuationPreference = (
        ContinuationPreference.AGENT_DECIDES
    )
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_source_contract(self) -> StorySourceContract:
        if not self.user_overridable:
            raise ValueError("Story source commitments must remain user-overridable")
        return self


class StoryIntent(DomainModel):
    """Versioned creative direction for one story, not a quality gate."""

    audience_experience: str = Field(min_length=1, max_length=2000)
    dramatic_engine: str = Field(min_length=1, max_length=2000)
    progression_strategy: str = Field(min_length=1, max_length=2000)
    tone_and_plausibility: str = Field(min_length=1, max_length=2000)
    duration_contract: DurationContract | None = None
    protected_elements: list[str] = Field(default_factory=list[str], max_length=20)
    drift_risks: list[str] = Field(default_factory=list[str], max_length=20)


class StoryIssueLedgerEntry(DomainModel):
    """Natural-language history for one semantic story concern, never a gate."""

    issue_id: str = Field(min_length=1, max_length=120)
    semantic_cluster: str = Field(min_length=1, max_length=240)
    state: str = Field(min_length=1, max_length=240)
    observation: str = Field(min_length=1, max_length=2000)
    text_evidence: list[str] = Field(default_factory=list[str])
    fix_evidence: list[str] = Field(default_factory=list[str])
    evolution_notes: list[str] = Field(default_factory=list[str])
    accepted_tradeoff: str | None = Field(default=None, max_length=2000)
    handoff: str | None = Field(default=None, max_length=2000)


class StoryRepairAction(DomainModel):
    """One evidence-backed recommendation; it is not an execution gate."""

    # ``issue_index`` remains readable for planning records written before the
    # Critic ledger gained stable IDs.  New repair work uses ``issue_id`` as the
    # semantic identity so filtering or reordering review items cannot retarget
    # an action.
    issue_index: int = Field(ge=1)
    issue_id: str | None = Field(default=None, min_length=1, max_length=120)
    diagnosis: StoryRepairDiagnosis
    should_revise: bool
    rationale: str = Field(min_length=1, max_length=1200)
    affected_scene_sequences: list[int] = Field(
        default_factory=list[int],
    )
    metadata_targets: list[StoryMetadataTarget] = Field(
        default_factory=list[StoryMetadataTarget],
    )
    target_duration_change_required: bool = False
    recommended_change: str = Field(min_length=1, max_length=1600)
    remove_or_simplify: list[str] = Field(default_factory=list[str], max_length=12)
    preserve: list[str] = Field(default_factory=list[str], max_length=12)
    validation_questions: list[str] = Field(default_factory=list[str], max_length=12)

    @model_validator(mode="after")
    def validate_action(self) -> StoryRepairAction:
        if self.diagnosis == StoryRepairDiagnosis.CRITIC_OVERREACH and self.should_revise:
            raise ValueError("Critic overreach cannot require an automatic story rewrite")
        if self.target_duration_change_required and not self.should_revise:
            raise ValueError(
                "Scene duration changes require an active story repair action"
            )
        if self.target_duration_change_required and not self.affected_scene_sequences:
            raise ValueError(
                "Scene duration changes must identify their affected scenes"
            )
        if self.metadata_targets and not self.should_revise:
            raise ValueError(
                "Story metadata targets require an active story repair action"
            )
        if len(self.metadata_targets) != len(set(self.metadata_targets)):
            raise ValueError("Story repair metadata targets must be unique")
        return self


class StoryRepairPlan(DomainModel):
    """Cross-round diagnosis and minimal repair plan prepared before rewriting."""

    summary: str = Field(min_length=1, max_length=1600)
    source_promise: str = Field(min_length=1, max_length=1200)
    invariants: list[str] = Field(default_factory=list[str], max_length=20)
    actions: list[StoryRepairAction] = Field(
        default_factory=list[StoryRepairAction],
        max_length=30,
    )
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_plan(self) -> StoryRepairPlan:
        legacy_issue_indices = [
            action.issue_index
            for action in self.actions
            if action.issue_id is None
        ]
        if len(legacy_issue_indices) != len(set(legacy_issue_indices)):
            raise ValueError(
                "Legacy repair actions must use each routed issue index once"
            )
        issue_ids = [
            action.issue_id
            for action in self.actions
            if action.issue_id is not None
        ]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("Story repair action issue IDs must be unique")
        if not self.user_overridable:
            raise ValueError("Story repair recommendations must remain user-overridable")
        return self


class StoryCharacterMetadataPatch(DomainModel):
    """Sparse descriptive updates for one existing character selected by stable ID."""

    character_id: str = Field(min_length=1, max_length=200)
    appearance: str | None = Field(default=None, min_length=1, max_length=500)
    wardrobe: str | None = Field(default=None, min_length=1, max_length=500)
    performance: str | None = Field(default=None, min_length=1, max_length=500)
    voice_identity: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator(
        "character_id",
        "appearance",
        "wardrobe",
        "performance",
        "voice_identity",
        mode="before",
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_has_update(self) -> StoryCharacterMetadataPatch:
        if all(
            value is None
            for value in (
                self.appearance,
                self.wardrobe,
                self.performance,
                self.voice_identity,
            )
        ):
            raise ValueError("Character metadata patch must change a description")
        return self


class StoryLocationMetadataPatch(DomainModel):
    """Sparse descriptive updates for one existing location selected by stable ID."""

    location_id: str = Field(min_length=1, max_length=200)
    layout: str | None = Field(default=None, min_length=1, max_length=800)
    lighting: str | None = Field(default=None, min_length=1, max_length=500)
    palette: str | None = Field(default=None, min_length=1, max_length=500)
    key_views: list[str] | None = Field(default=None, min_length=1, max_length=8)

    @field_validator("location_id", "layout", "lighting", "palette", mode="before")
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
    def validate_has_update(self) -> StoryLocationMetadataPatch:
        if all(
            value is None
            for value in (self.layout, self.lighting, self.palette, self.key_views)
        ):
            raise ValueError("Location metadata patch must change a description")
        if self.key_views is not None and (
            any(not value for value in self.key_views)
            or len(self.key_views) != len(set(self.key_views))
        ):
            raise ValueError("Location metadata key views must be non-empty and unique")
        return self


class StoryPropMetadataPatch(DomainModel):
    """Sparse descriptive updates for one existing prop selected by stable ID."""

    prop_id: str = Field(min_length=1, max_length=200)
    appearance: str = Field(min_length=1, max_length=500)

    @field_validator("prop_id", "appearance", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StoryStyleMetadataPatch(DomainModel):
    """Sparse updates to the existing global style record."""

    medium: str | None = Field(default=None, min_length=1, max_length=300)
    genre: str | None = Field(default=None, min_length=1, max_length=120)
    cinematography: str | None = Field(default=None, min_length=1, max_length=800)
    color: str | None = Field(default=None, min_length=1, max_length=500)
    texture: str | None = Field(default=None, min_length=1, max_length=500)
    forbidden: list[str] | None = Field(default=None, max_length=12)

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

    @field_validator("forbidden", mode="before")
    @classmethod
    def trim_forbidden(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            item.strip() if isinstance(item, str) else item
            for item in cast(list[object], value)
        ]

    @model_validator(mode="after")
    def validate_has_update(self) -> StoryStyleMetadataPatch:
        if all(
            value is None
            for value in (
                self.medium,
                self.genre,
                self.cinematography,
                self.color,
                self.texture,
                self.forbidden,
            )
        ):
            raise ValueError("Style metadata patch must change at least one field")
        if self.forbidden is not None and (
            any(not value for value in self.forbidden)
            or len(self.forbidden) != len(set(self.forbidden))
        ):
            raise ValueError("Style forbidden entries must be non-empty and unique")
        return self


class StoryMetadataRevisionPatch(DomainModel):
    """Typed, sparse patch for global story metadata only.

    Stable entity IDs are selectors, not editable values. Scene contracts, dialogue,
    and a standalone target-duration field are intentionally absent from this schema.
    When StoryIntent is deliberately revised, its nested DurationContract remains the
    authoritative story-level duration decision and is projected by the workflow.
    """

    title: str | None = Field(default=None, min_length=1, max_length=120)
    story_summary: str | None = Field(default=None, min_length=1, max_length=2400)
    story_intent: StoryIntent | None = None
    ending_contract: EndingContract | None = None
    character_patches: list[StoryCharacterMetadataPatch] = Field(
        default_factory=list[StoryCharacterMetadataPatch],
        max_length=12,
    )
    location_patches: list[StoryLocationMetadataPatch] = Field(
        default_factory=list[StoryLocationMetadataPatch],
        max_length=20,
    )
    prop_patches: list[StoryPropMetadataPatch] = Field(
        default_factory=list[StoryPropMetadataPatch],
        max_length=20,
    )
    style: StoryStyleMetadataPatch | None = None

    @field_validator("title", "story_summary", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_unique_asset_selectors(self) -> StoryMetadataRevisionPatch:
        for label, entity_ids in (
            ("character", [item.character_id for item in self.character_patches]),
            ("location", [item.location_id for item in self.location_patches]),
            ("prop", [item.prop_id for item in self.prop_patches]),
        ):
            if len(entity_ids) != len(set(entity_ids)):
                raise ValueError(f"Story metadata patch repeats a {label} selector")
        return self


class StoryRevisionViolation(DomainModel):
    # Optional only so old persisted verifier results remain loadable.  Fresh
    # verifier output is validated more strictly by the production planner.
    issue_id: str | None = Field(default=None, min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=120)
    scene_sequences: list[int] = Field(default_factory=list[int])
    metadata_targets: list[StoryMetadataTarget] = Field(
        default_factory=list[StoryMetadataTarget],
    )
    target_duration_change_required: bool = False
    observation: str = Field(min_length=1, max_length=1200)
    repair_instruction: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_duration_scope(self) -> StoryRevisionViolation:
        if self.target_duration_change_required and not self.scene_sequences:
            raise ValueError(
                "Duration repair violations must identify at least one scene"
            )
        if len(self.metadata_targets) != len(set(self.metadata_targets)):
            raise ValueError("Story revision metadata targets must be unique")
        return self


class StoryRevisionVerification(DomainModel):
    """Repair verification plus a natural cold read of the revised screenplay."""

    accepted: bool
    summary: str = Field(min_length=1, max_length=1600)
    satisfied_issue_ids: list[str] = Field(default_factory=list[str])
    # Compatibility projection for records produced before stable issue IDs.
    satisfied_issue_indices: list[int] = Field(default_factory=list[int], max_length=30)
    violations: list[StoryRevisionViolation] = Field(
        default_factory=list[StoryRevisionViolation],
        max_length=20,
    )
    duration_fit_assessment: DurationFitAssessment | None = None
    recommended_followup: StoryReviewFollowup = StoryReviewFollowup.FINALIZE
    followup_rationale: str | None = Field(default=None, max_length=1200)
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_verification(self) -> StoryRevisionVerification:
        if self.accepted and self.violations:
            raise ValueError("Accepted story revisions cannot retain plan violations")
        if not self.accepted and not self.violations:
            raise ValueError("Rejected story revisions must name a concrete violation")
        if len(self.satisfied_issue_indices) != len(set(self.satisfied_issue_indices)):
            raise ValueError("Satisfied repair issue indices must be unique")
        if len(self.satisfied_issue_ids) != len(set(self.satisfied_issue_ids)):
            raise ValueError("Satisfied repair issue IDs must be unique")
        if not self.user_overridable:
            raise ValueError("Story revision verification must remain user-overridable")
        return self


class StoryVerificationResolution(DomainModel):
    """Persisted Leader decision over one exact verifier result.

    The captured verifier payload remains unchanged so accepting a tradeoff never
    rewrites a rejected or unresolved result into an accepted one.
    """

    source: StoryVerificationSource
    decision: StoryVerificationResolutionDecision
    rationale: str = Field(min_length=1, max_length=2400)
    owner_agent_run_id: str = Field(min_length=1, max_length=240)
    script_review_report: ScriptReviewReport | None = None
    revision_verification: StoryRevisionVerification | None = None
    created_at: str

    @model_validator(mode="after")
    def validate_exact_source_payload(self) -> StoryVerificationResolution:
        if self.source == StoryVerificationSource.SCRIPT_VERIFIER:
            if self.script_review_report is None or self.revision_verification is not None:
                raise ValueError(
                    "Script-verifier resolution must capture only its exact review report"
                )
        elif (
            self.revision_verification is None
            or self.script_review_report is not None
        ):
            raise ValueError(
                "Revision-verifier resolution must capture only its exact verification"
            )
        return self


class PlanningOutlineSnapshot(DomainModel):
    revision_round: int = Field(ge=0)
    outline_json: str
    source: str = Field(min_length=1, max_length=240)
    source_contract: StorySourceContract | None = None
    story_intent: StoryIntent | None = None
    repair_plan: StoryRepairPlan | None = None
    revision_verification: StoryRevisionVerification | None = None
    created_at: str


class PlanningReviewSnapshot(DomainModel):
    review_round: int = Field(ge=1)
    revision_round: int = Field(ge=0)
    report: ScriptReviewReport
    disposition: ScriptReviewDisposition | None = None
    created_at: str


class RevisionJudgeRecommendation(DomainModel):
    recommended_revision_round: int = Field(ge=0)
    alternative_revision_round: int = Field(ge=0)
    confidence: RevisionJudgeConfidence
    summary: str = Field(min_length=1, max_length=1200)
    evidence: list[str] = Field(default_factory=list[str], max_length=8)
    tradeoffs: list[str] = Field(default_factory=list[str], max_length=8)
    advisory_only: bool = True

    @model_validator(mode="after")
    def validate_recommendation(self) -> RevisionJudgeRecommendation:
        if self.recommended_revision_round == self.alternative_revision_round:
            raise ValueError("Revision Judge must compare two different revisions")
        if not self.advisory_only:
            raise ValueError("Revision Judge recommendations must remain advisory")
        return self


class ScriptVersion(DomainModel):
    id: str
    project_id: str
    version: int
    source_mode: SourceMode
    original_text: str
    screenplay_text: str = ""
    spoken_text: str
    subtitle_text: str
    lines: list[ScriptLine]
    structured_scenes: list[StructuredScene]
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    final_cold_read_report: ScriptReviewReport | None = None
    ending_contract: EndingContract | None = None
    story_intent: StoryIntent | None = None
    source_contract: StorySourceContract | None = None
    planning_run_id: str | None = None
    approval_status: ApprovalStatus
    freshness: Freshness
    parent_version_id: str | None = None
    change_reason: str | None = None
    created_at: str


class CharacterProfile(DomainModel):
    id: str
    name: str
    age: int
    appearance: str
    wardrobe: str
    performance: str
    voice_identity: str


class LocationProfile(DomainModel):
    id: str
    name: str
    layout: str
    lighting: str
    palette: str
    key_views: list[str]


class PropState(DomainModel):
    shot_id: str
    holder: str
    location: str


class PropProfile(DomainModel):
    id: str
    name: str
    appearance: str
    states: list[PropState]


class StyleProfile(DomainModel):
    medium: str
    genre: str
    cinematography: str
    color: str
    texture: str
    forbidden: list[str]


class ProjectBibleVersion(DomainModel):
    id: str
    project_id: str
    version: int
    characters: list[CharacterProfile]
    locations: list[LocationProfile]
    props: list[PropProfile]
    style: StyleProfile
    approval_status: ApprovalStatus
    freshness: Freshness
    parent_version_id: str | None = None
    created_at: str


class CoreAssetBrief(DomainModel):
    """Structured identity and continuity anchors shared by all asset versions."""

    identity_anchor: str = Field(min_length=1, max_length=1200)
    tone_or_temperament: str | None = Field(default=None, max_length=1200)
    visual_attributes: list[str] = Field(
        min_length=1,
        max_length=24,
    )
    wardrobe_or_materials: list[str] = Field(
        default_factory=list[str],
        max_length=16,
    )
    background_constraints: list[str] = Field(
        default_factory=list[str],
        max_length=16,
    )
    texture_constraints: list[str] = Field(
        default_factory=list[str],
        max_length=16,
    )
    continuity_anchors: list[str] = Field(
        default_factory=list[str],
        max_length=24,
    )
    named_views_or_zones: list[str] = Field(
        default_factory=list[str],
        max_length=16,
    )
    presentation_mode: AssetPresentationMode | None = None
    required_visible_text: list[str] = Field(default_factory=list[str])
    reference_strategy: AssetReferenceStrategy = AssetReferenceStrategy.LIGHTWEIGHT
    requested_views: list[AssetStandardView] = Field(
        default_factory=list[AssetStandardView],
        max_length=12,
    )
    strategy_rationale: str | None = Field(default=None, max_length=1200)

    @model_validator(mode="after")
    def validate_requested_views(self) -> CoreAssetBrief:
        if self.identity_anchor != self.identity_anchor.strip():
            raise ValueError("Core asset identity_anchor must be trimmed")
        for field_name in (
            "visual_attributes",
            "wardrobe_or_materials",
            "background_constraints",
            "texture_constraints",
            "continuity_anchors",
            "named_views_or_zones",
            "required_visible_text",
        ):
            values = getattr(self, field_name)
            normalized = [item.strip() for item in values if item.strip()]
            if normalized != values or len(normalized) != len(set(normalized)):
                raise ValueError(
                    f"Core asset {field_name} entries must be trimmed, non-empty, and unique"
                )
        if len(self.requested_views) != len(set(self.requested_views)):
            raise ValueError("Core asset requested views must be unique")
        return self


class AssetReferenceSheetCell(DomainModel):
    view: AssetStandardView
    label: str = Field(min_length=1, max_length=80)
    source_asset_id: str = Field(min_length=1, max_length=200)
    source_version_id: str = Field(min_length=1, max_length=200)


class AssetReferenceSheet(DomainModel):
    strategy: AssetReferenceStrategy
    input_policy: AssetSheetInputPolicy
    columns: int = Field(ge=1, le=3)
    cells: list[AssetReferenceSheetCell] = Field(
        min_length=2,
        max_length=9,
    )

    @model_validator(mode="after")
    def validate_layout(self) -> AssetReferenceSheet:
        if self.columns > len(self.cells):
            raise ValueError("Reference sheet columns cannot exceed its cell count")
        return self


class AssetSourceRecord(DomainModel):
    source_type: OriginType
    source_label: str
    source_uri: str | None = None
    original_filename: str | None = None
    provider: str | None = None
    provider_asset_id: str | None = None
    creator: str | None = None
    creator_uri: str | None = None
    license_name: str | None = None
    license_uri: str | None = None
    attribution: str | None = None
    stock_import_mode: str | None = None
    model: str | None = None
    prompt: str | None = None
    created_at: str


class AssetVersion(DomainModel):
    id: str
    version: int
    origin_type: OriginType
    descriptor: str
    media_type: AssetMediaType = AssetMediaType.TEXT_REFERENCE
    local_path: str | None = None
    external_url: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = Field(default=None, gt=0.0)
    file_size_bytes: int | None = None
    parent_version_id: str | None = None
    image_task_id: str | None = None
    reference_sheet: AssetReferenceSheet | None = None
    source: AssetSourceRecord | None = None
    approval_status: ApprovalStatus
    review_notes: str | None = None
    freshness: Freshness
    created_at: str


class Asset(DomainModel):
    id: str
    project_id: str
    kind: AssetKind
    name: str
    bible_entity_id: str | None = Field(default=None, max_length=200)
    brief: CoreAssetBrief | None = None
    current_version_id: str
    approved_version_id: str | None = None
    versions: list[AssetVersion]


class ReferencePackEntry(DomainModel):
    id: str
    asset_version_id: str
    lineage_root_version_id: str | None = None
    role: ReferenceRole
    angle: ReferenceAngle = ReferenceAngle.NOT_APPLICABLE
    framing: ReferenceFraming = ReferenceFraming.NOT_APPLICABLE
    tags: list[str] = Field(default_factory=list[str], max_length=32)
    description: str
    priority: int = Field(default=0, ge=-100, le=100)


class ReferencePack(DomainModel):
    id: str
    project_id: str
    subject_asset_id: str
    name: str
    entries: list[ReferencePackEntry] = Field(default_factory=list[ReferencePackEntry])
    created_at: str
    updated_at: str


class VoiceReferenceBinding(DomainModel):
    character_id: str
    asset_id: str
    created_at: str
    updated_at: str


class ContinuityState(DomainModel):
    character_positions: str
    eyeline: str
    prop_state_in: str
    prop_state_out: str
    action_in: str
    action_out: str


class ShotReferenceCue(DomainModel):
    """Semantic reference requested by one time-bounded part of a shot."""

    subject_asset_id: str = Field(min_length=1, max_length=200)
    alias: str = Field(min_length=1, max_length=80)
    required_tags: list[str] = Field(default_factory=list[str], max_length=12)
    purpose: str = Field(min_length=1, max_length=500)

    @field_validator("alias", mode="before")
    @classmethod
    def normalize_alias(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if value != value.strip() or value.startswith("@"):
            raise ValueError(
                "Shot reference aliases must be trimmed and must not include the @ prefix"
            )
        return normalize_reference_alias(value, max_length=80)

    @model_validator(mode="after")
    def validate_reference_cue(self) -> ShotReferenceCue:
        normalized_tags = [item.strip() for item in self.required_tags if item.strip()]
        if normalized_tags != self.required_tags or len(normalized_tags) != len(
            set(normalized_tags)
        ):
            raise ValueError("Shot reference cue tags must be trimmed and unique")
        return self


class ShotTimelineBeat(DomainModel):
    """One observable camera/action/performance phase inside an editorial shot."""

    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    visual: str = Field(min_length=1, max_length=1200)
    framing: str = Field(min_length=1, max_length=200)
    camera: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=800)
    performance: str | None = Field(default=None, min_length=1, max_length=500)
    emotion: str | None = Field(default=None, min_length=1, max_length=300)
    dialogue_line_ids: list[str] = Field(default_factory=list[str], max_length=40)
    sound: str | None = Field(default=None, min_length=1, max_length=500)
    references: list[ShotReferenceCue] = Field(
        default_factory=list[ShotReferenceCue],
        max_length=12,
    )

    @model_validator(mode="after")
    def validate_time_range(self) -> ShotTimelineBeat:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Shot timeline beat end_seconds must follow start_seconds")
        if len(self.dialogue_line_ids) != len(set(self.dialogue_line_ids)):
            raise ValueError("Shot timeline beat dialogue line IDs must be unique")
        aliases = [item.alias for item in self.references]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Shot timeline beat reference aliases must be unique")
        return self


class ShotPlan(DomainModel):
    id: str
    project_id: str
    sequence: int
    scene_id: str | None = None
    duration_seconds: float
    narrative_purpose: str
    visual: str
    framing: str
    camera: str
    audio_intent: str
    dialogue_line_ids: list[str]
    timeline_beats: list[ShotTimelineBeat] = Field(
        default_factory=list[ShotTimelineBeat],
        max_length=24,
    )
    continuity: ContinuityState
    transition_out: str
    merge_candidate: bool
    local_rework_risk: str
    approval_status: ApprovalStatus
    freshness: Freshness
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_timeline_beats(self) -> ShotPlan:
        if not self.timeline_beats:
            return self
        tolerance = 0.05
        if abs(self.timeline_beats[0].start_seconds) > tolerance:
            raise ValueError("Shot timeline beats must start at 0 seconds")
        for previous, current in zip(
            self.timeline_beats,
            self.timeline_beats[1:],
            strict=False,
        ):
            if abs(previous.end_seconds - current.start_seconds) > tolerance:
                raise ValueError("Shot timeline beats must be contiguous and ordered")
        if abs(self.timeline_beats[-1].end_seconds - self.duration_seconds) > tolerance:
            raise ValueError("Shot timeline beats must cover the editorial shot duration")
        assigned_dialogue = [
            line_id
            for beat in self.timeline_beats
            for line_id in beat.dialogue_line_ids
        ]
        ordered_dialogue: list[str] = []
        for line_id in assigned_dialogue:
            if not ordered_dialogue or ordered_dialogue[-1] != line_id:
                ordered_dialogue.append(line_id)
        if ordered_dialogue != self.dialogue_line_ids:
            raise ValueError(
                "Shot timeline beats must cover shot dialogue lines in order; "
                "one line may span adjacent beats"
            )
        alias_targets: dict[str, tuple[str, tuple[str, ...]]] = {}
        for beat in self.timeline_beats:
            for cue in beat.references:
                target = (cue.subject_asset_id, tuple(cue.required_tags))
                existing = alias_targets.setdefault(cue.alias, target)
                if existing != target:
                    raise ValueError(
                        "A shot reference alias cannot identify different subjects or views"
                    )
        return self


class CapabilitySnapshot(DomainModel):
    provider: str
    model: str
    resolution: str
    native_audio: bool
    supports_frame_reference: bool
    supports_asset_reference: bool


class ExecutionPlanEntry(DomainModel):
    shot_id: str
    strategy: ExecutionStrategy
    rationale: str
    skipped_optional_steps: list[str]
    provider: str
    capability_snapshot: CapabilitySnapshot
    candidate_count: int
    fallback: str


class ReferenceBinding(DomainModel):
    kind: AssetKind
    target_id: str
    purpose: str
    required: bool = True


class ReferenceRequirement(DomainModel):
    id: str
    subject_asset_id: str
    acceptable_roles: list[ReferenceRole] = Field(min_length=1, max_length=8)
    preferred_angles: list[ReferenceAngle] = Field(
        default_factory=list[ReferenceAngle],
        max_length=8,
    )
    preferred_framings: list[ReferenceFraming] = Field(
        default_factory=list[ReferenceFraming],
        max_length=8,
    )
    required_tags: list[str] = Field(default_factory=list[str], max_length=16)
    purpose: str
    required: bool = True


class ActionBeat(DomainModel):
    order: int = Field(ge=1, le=20)
    actor_asset_id: str
    action: str
    target_anchor: str | None = None
    required_end_state: str | None = None


class SurfaceAttachmentConstraint(DomainModel):
    """A named subpart that remains attached to one physical surface."""

    anchor: str = Field(min_length=1, max_length=200)
    surface: str = Field(min_length=1, max_length=200)


class SpatialInteractionConstraint(DomainModel):
    id: str
    actor_asset_id: str
    effector: InteractionEffector
    action: str
    target_asset_id: str
    target_anchor: str
    start_state: str
    end_state: str
    unchanged_anchors: list[str] = Field(default_factory=list[str], max_length=16)
    contact_anchor: str | None = Field(default=None, min_length=1, max_length=200)
    contact_surface: str | None = Field(default=None, min_length=1, max_length=200)
    contact_persists: bool = False
    rigid_attachments: list[SurfaceAttachmentConstraint] = Field(
        default_factory=list[SurfaceAttachmentConstraint],
        max_length=16,
    )


class ReferencePlanEntry(DomainModel):
    shot_id: str
    bindings: list[ReferenceBinding]
    capability_valid: bool
    validation_notes: list[str]
    requirements: list[ReferenceRequirement] = Field(
        default_factory=list[ReferenceRequirement]
    )
    action_beats: list[ActionBeat] = Field(default_factory=list[ActionBeat])
    interactions: list[SpatialInteractionConstraint] = Field(
        default_factory=list[SpatialInteractionConstraint]
    )
    max_reference_images: int = Field(default=3, ge=1, le=9)
    planning_notes: str | None = None


class TimelineVideoClip(DomainModel):
    id: str
    shot_id: str
    take_id: str
    source_script_version_id: str | None = None
    source_bible_version_id: str | None = None
    source_storyboard_document_id: str | None = None
    source_storyboard_version_id: str | None = None
    media_path: str
    start_seconds: float
    source_in_seconds: float
    duration_seconds: float
    transition_in: str
    transition_out: str

    @model_validator(mode="after")
    def validate_storyboard_source(self) -> TimelineVideoClip:
        if (self.source_storyboard_document_id is None) != (
            self.source_storyboard_version_id is None
        ):
            raise ValueError(
                "Timeline video source Storyboard document and version IDs "
                "must be supplied together"
            )
        return self


class TimelineAudioClip(DomainModel):
    id: str
    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    media_path: str
    start_seconds: float
    source_in_seconds: float = Field(default=0.0, ge=0.0)
    source_out_seconds: float | None = Field(default=None, gt=0.0)
    duration_seconds: float
    gain_db: float
    role: str

    @model_validator(mode="after")
    def validate_source_binding(self) -> TimelineAudioClip:
        if (self.shot_id is None) != (self.take_id is None):
            raise ValueError("Timeline audio shot_id and take_id must be supplied together")
        if self.take_id is None and self.asset_version_id is None:
            raise ValueError(
                "Timeline audio must reference either a Take or an audio AssetVersion"
            )
        if (
            self.source_out_seconds is not None
            and self.source_out_seconds <= self.source_in_seconds
        ):
            raise ValueError("Timeline audio source_out_seconds must follow source_in_seconds")
        if self.source_out_seconds is not None and abs(
            self.source_out_seconds
            - self.source_in_seconds
            - self.duration_seconds
        ) > 0.001:
            raise ValueError(
                "Timeline audio duration_seconds must match its selected source window"
            )
        return self


class SubtitleCue(DomainModel):
    id: str
    script_line_id: str | None = None
    timed_text_id: str | None = None
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None
    language: str | None = None

    @model_validator(mode="after")
    def validate_cue_source(self) -> SubtitleCue:
        if self.script_line_id is None and self.timed_text_id is None:
            raise ValueError(
                "Subtitle cues must reference a script line or locked timed text"
            )
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Subtitle cue end_seconds must follow start_seconds")
        return self


class OutputSpec(DomainModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_codec: str = "h264"
    profile: str = "high"
    pixel_format: str = "yuv420p"
    color_space: str = "bt709"
    crf: int = 18
    audio_codec: str = "aac"
    audio_sample_rate: int = 48000
    audio_bitrate_kbps: int = 192


class TimelineManifest(DomainModel):
    id: str
    project_id: str
    version: int
    script_version_id: str
    bible_version_id: str
    video_clips: list[TimelineVideoClip]
    audio_clips: list[TimelineAudioClip]
    subtitle_cues: list[SubtitleCue]
    output_spec: OutputSpec
    duration_seconds: float
    approval_status: ApprovalStatus
    freshness: Freshness
    created_at: str


class DirectorRepairRecord(DomainModel):
    id: str
    source_shot_id: str
    source_take_id: str | None = None
    strategy: DirectorRepairStrategy
    decision: DirectorRepairDecision
    reason: str
    issue_codes: list[str] = Field(default_factory=list[str])
    affected_shot_ids: list[str] = Field(default_factory=list[str])
    created_at: str


class ProjectPlanningMetadata(DomainModel):
    """Project-visible projection of planning provenance or an owned planning job."""

    run_id: str | None = None
    status: PlanningRunStatus
    planner: str | None = None
    warnings: list[str] = Field(default_factory=list[str])
    owner_agent_run_id: str | None = None
    planning_job_kind: Literal["CONTENT_PLANNING"] | None = None

    @model_validator(mode="after")
    def validate_owned_job_projection(self) -> ProjectPlanningMetadata:
        if self.run_id is None:
            if self.owner_agent_run_id is not None or self.planning_job_kind is not None:
                raise ValueError(
                    "Planning job ownership and kind require a persisted planning run id"
                )
            return self
        if self.owner_agent_run_id is not None and self.planning_job_kind is None:
            self.planning_job_kind = "CONTENT_PLANNING"
        if self.planning_job_kind is not None and self.owner_agent_run_id is None:
            raise ValueError(
                "A content planning job projection requires an owning AgentRun"
            )
        return self


class ProductionDocumentOrigin(StrEnum):
    """Who supplied one durable production-method document version."""

    USER = "USER"
    AGENT = "AGENT"
    IMPORT = "IMPORT"


class MasterAudioBinding(DomainModel):
    """Approved audio window that defines time for an audio-led production."""

    asset_id: str = Field(min_length=1)
    asset_version_id: str = Field(min_length=1)
    source_in_seconds: float = Field(default=0.0, ge=0.0)
    source_out_seconds: float = Field(gt=0.0)
    time_origin_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_source_window(self) -> MasterAudioBinding:
        if self.source_out_seconds <= self.source_in_seconds:
            raise ValueError("Master Audio source window must have positive duration")
        source_duration = self.source_out_seconds - self.source_in_seconds
        if abs(source_duration - self.duration_seconds) > 0.001:
            raise ValueError(
                "Master Audio duration_seconds must match its selected source window"
            )
        return self


class AudioEnergyPoint(DomainModel):
    time_seconds: float = Field(ge=0.0)
    normalized_energy: float = Field(ge=0.0, le=1.0)


class AudioStructureSection(DomainModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    label: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_time_range(self) -> AudioStructureSection:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Audio section end_seconds must follow start_seconds")
        return self


class AudioKeyMoment(DomainModel):
    time_seconds: float = Field(ge=0.0)
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    strength: float | None = Field(default=None, ge=0.0)


class AudioStructureMap(DomainModel):
    """Measured music structure; unavailable dimensions remain explicitly empty."""

    asset_id: str = Field(min_length=1)
    asset_version_id: str = Field(min_length=1)
    source_in_seconds: float = Field(ge=0.0)
    source_out_seconds: float = Field(gt=0.0)
    time_origin_seconds: float = Field(ge=0.0)
    duration_seconds: float = Field(gt=0.0)
    analysis_level: Literal["BASIC_METADATA", "RHYTHMIC"]
    analyzer: str = Field(min_length=1, max_length=160)
    bpm: float | None = Field(default=None, gt=0.0)
    beats: list[float] = Field(default_factory=list[float])
    onsets: list[float] = Field(default_factory=list[float])
    energy: list[AudioEnergyPoint] = Field(default_factory=list[AudioEnergyPoint])
    sections: list[AudioStructureSection] = Field(
        default_factory=list[AudioStructureSection]
    )
    key_moments: list[AudioKeyMoment] = Field(
        default_factory=list[AudioKeyMoment]
    )
    sample_rate_hz: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    file_size_bytes: int = Field(ge=0)
    mime_type: str | None = None
    notes: list[str] = Field(default_factory=list[str])
    created_at: str = Field(min_length=1)


class TimedTextLock(DomainModel):
    """Exact on-screen text anchored to the Master Audio timeline."""

    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    language: str | None = Field(default=None, min_length=1, max_length=40)
    speaker: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_exact_text(self) -> TimedTextLock:
        if not self.text.strip():
            raise ValueError("Timed text must contain visible characters")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Timed text end_seconds must follow start_seconds")
        return self


class TimedTextLockSet(DomainModel):
    master_audio_version_id: str = Field(min_length=1)
    entries: list[TimedTextLock] = Field(default_factory=list[TimedTextLock])

    @model_validator(mode="after")
    def validate_unique_entries(self) -> TimedTextLockSet:
        entry_ids = [item.id for item in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("Timed text IDs must be unique")
        return self


class ProductionDocumentArtifactRef(DomainModel):
    """Domain-side ArtifactRef compatible with the Agent runtime contract.

    The Agent runtime imports domain models, so the canonical project document
    cannot import ``agent_runtime.ArtifactRef`` without creating a cycle.  This
    deliberately keeps the same serialized shape for lossless handoff.
    """

    project_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)


class ProjectOutputSlotRevision(DomainModel):
    """Immutable snapshot of one logical output-slot revision."""

    revision: int = Field(ge=1)
    current_artifact_ref: ProductionDocumentArtifactRef | None = None
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    producer_agent_run_id: str | None = Field(default=None, min_length=1)
    producer_work_order_id: str | None = Field(default=None, min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot_state(self) -> ProjectOutputSlotRevision:
        if self.current_artifact_ref is None:
            if self.invalidation_reason is None:
                raise ValueError(
                    "An invalidated output-slot revision requires a reason"
                )
            return self
        if self.invalidation_reason is not None:
            raise ValueError(
                "A bound output-slot revision cannot have an invalidation reason"
            )
        return self


class ProjectOutputSlot(DomainModel):
    """Stable logical pointer to the latest artifact produced for one purpose.

    A slot is pointer metadata only.  The referenced artifact remains the
    canonical content, while prior bindings stay available for provenance and
    rollback-aware Agent handoffs.
    """

    id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    current_artifact_ref: ProductionDocumentArtifactRef | None = None
    previous_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    revision_history: list[ProjectOutputSlotRevision] = Field(
        default_factory=list[ProjectOutputSlotRevision]
    )
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    producer_agent_run_id: str | None = Field(default=None, min_length=1)
    producer_work_order_id: str | None = Field(default=None, min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_artifact_history(self) -> ProjectOutputSlot:
        history_revisions = [item.revision for item in self.revision_history]
        if history_revisions != sorted(history_revisions):
            raise ValueError("Output slot revision history must be ordered")
        if len(history_revisions) != len(set(history_revisions)):
            raise ValueError("Output slot revision history must be unique")
        if any(revision >= self.revision for revision in history_revisions):
            raise ValueError(
                "Output slot revision history must precede the current revision"
            )
        previous_keys = [
            _production_artifact_ref_key(item)
            for item in self.previous_artifact_refs
        ]
        if len(previous_keys) != len(set(previous_keys)):
            raise ValueError("Output slot previous artifact refs must be unique")
        if self.current_artifact_ref is None:
            if not self.previous_artifact_refs:
                raise ValueError(
                    "An invalidated output slot must retain at least one prior artifact ref"
                )
            if self.invalidation_reason is None:
                raise ValueError("An invalidated output slot requires a reason")
            return self
        if self.invalidation_reason is not None:
            raise ValueError("A bound output slot cannot have an invalidation reason")
        if _production_artifact_ref_key(self.current_artifact_ref) in previous_keys:
            raise ValueError(
                "Output slot current artifact ref cannot also be in its history"
            )
        return self


def _production_artifact_ref_key(
    artifact_ref: ProductionDocumentArtifactRef,
) -> tuple[str, str, str, int | None]:
    return (
        artifact_ref.project_id,
        artifact_ref.artifact_type,
        artifact_ref.artifact_id,
        artifact_ref.version,
    )


class ProductionDocumentVersion(DomainModel):
    """One immutable structured snapshot of a production-method document."""

    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    parent_version_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin
    created_at: str = Field(min_length=1)


class ProductionDocument(DomainModel):
    """Stable identity and version history for reusable production knowledge."""

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    current_version_id: str = Field(min_length=1)
    versions: list[ProductionDocumentVersion] = Field(min_length=1)

    @field_validator("kind", "title", mode="before")
    @classmethod
    def trim_descriptive_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_version_history(self) -> ProductionDocument:
        version_ids = [item.id for item in self.versions]
        version_numbers = [item.version for item in self.versions]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("Production document version IDs must be unique")
        if len(version_numbers) != len(set(version_numbers)):
            raise ValueError("Production document version numbers must be unique")
        if self.current_version_id not in version_ids:
            raise ValueError(
                "Production document current_version_id must reference a stored version"
            )
        return self


class ProductionDocumentCreateRequest(DomainModel):
    kind: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    payload: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin

    @field_validator("kind", "title", mode="before")
    @classmethod
    def trim_descriptive_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ProductionDocumentReviseRequest(DomainModel):
    base_version_id: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class ProductionDocumentReadResponse(DomainModel):
    document: ProductionDocument
    version: ProductionDocumentVersion


class ProductionDocumentMutationResponse(ProductionDocumentReadResponse):
    pass


class ProductionDocumentListResponse(DomainModel):
    documents: list[ProductionDocument] = Field(
        default_factory=list[ProductionDocument]
    )


class MasterAudioBindRequest(DomainModel):
    asset_version_id: str = Field(min_length=1)
    source_in_seconds: float = Field(default=0.0, ge=0.0)
    source_out_seconds: float | None = Field(default=None, gt=0.0)
    time_origin_seconds: float = Field(default=0.0, ge=0.0)
    base_version_id: str | None = Field(default=None, min_length=1)
    origin: ProductionDocumentOrigin


class MasterAudioBindingResponse(DomainModel):
    binding: MasterAudioBinding
    document: ProductionDocument
    version: ProductionDocumentVersion


class TimedTextLockUpdateRequest(DomainModel):
    master_audio_version_id: str = Field(min_length=1)
    entries: list[TimedTextLock] = Field(default_factory=list[TimedTextLock])
    base_version_id: str | None = Field(default=None, min_length=1)
    origin: ProductionDocumentOrigin


class TimedTextLockResponse(DomainModel):
    lock: TimedTextLockSet
    document: ProductionDocument
    version: ProductionDocumentVersion


class MusicAnalysisRequest(DomainModel):
    master_audio_version_id: str = Field(min_length=1)


class MusicAnalysisResponse(DomainModel):
    structure: AudioStructureMap
    document: ProductionDocument
    version: ProductionDocumentVersion


class ProjectDocument(DomainModel):
    project: ProjectRecord
    planning: ProjectPlanningMetadata | None = None
    script_versions: list[ScriptVersion]
    bible_versions: list[ProjectBibleVersion]
    assets: list[Asset]
    shots: list[ShotPlan]
    execution_plan: list[ExecutionPlanEntry]
    reference_plan: list[ReferencePlanEntry]
    manifests: list[TimelineManifest]
    reference_packs: list[ReferencePack] = Field(default_factory=list[ReferencePack])
    voice_references: list[VoiceReferenceBinding] = Field(
        default_factory=list[VoiceReferenceBinding]
    )
    repair_history: list[DirectorRepairRecord] = Field(
        default_factory=list[DirectorRepairRecord]
    )
    production_documents: list[ProductionDocument] = Field(
        default_factory=list[ProductionDocument]
    )
    master_audio_document_id: str | None = None
    audio_structure_document_id: str | None = None
    timed_text_document_id: str | None = None
    production_spec_document_id: str | None = None
    storyboard_document_id: str | None = None
    generation_plan_document_id: str | None = None
    output_slots: list[ProjectOutputSlot] = Field(
        default_factory=list[ProjectOutputSlot]
    )

    @model_validator(mode="after")
    def validate_production_documents(self) -> ProjectDocument:
        document_ids = [item.id for item in self.production_documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Production document IDs must be unique within a project")
        if any(
            item.project_id != self.project.id for item in self.production_documents
        ):
            raise ValueError("Production documents must belong to their containing project")
        for field_name in (
            "master_audio_document_id",
            "audio_structure_document_id",
            "timed_text_document_id",
            "production_spec_document_id",
            "storyboard_document_id",
            "generation_plan_document_id",
        ):
            value = getattr(self, field_name)
            if value is not None and value not in document_ids:
                raise ValueError(
                    f"{field_name} must reference a stored ProductionDocument"
                )
        output_slot_ids = [item.id for item in self.output_slots]
        if len(output_slot_ids) != len(set(output_slot_ids)):
            raise ValueError("Project output slot IDs must be unique")
        if any(
            (
                slot.current_artifact_ref is not None
                and slot.current_artifact_ref.project_id != self.project.id
            )
            or any(
                item.project_id != self.project.id
                for item in slot.previous_artifact_refs
            )
            for slot in self.output_slots
        ):
            raise ValueError("Project output slots may only reference project artifacts")
        return self


class GenerationUnitShotWindow(DomainModel):
    """One editorial Shot's source window inside a generated media artifact."""

    shot_id: str = Field(min_length=1)
    shot_revision: int = Field(ge=1)
    source_in_seconds: float = Field(ge=0.0)
    source_out_seconds: float = Field(gt=0.0)
    duration_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_source_window(self) -> GenerationUnitShotWindow:
        if self.source_out_seconds <= self.source_in_seconds:
            raise ValueError("Generation-unit source window must have positive duration")
        if abs(
            self.source_out_seconds
            - self.source_in_seconds
            - self.duration_seconds
        ) > 0.001:
            raise ValueError(
                "Generation-unit source window must match the shot duration"
            )
        return self


class GenerationTask(DomainModel):
    id: str
    project_id: str
    shot_id: str
    operation: OperationKind
    candidate_index: int
    status: TaskStatus
    provider: str
    model: str
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    resolution: str = "432x768"
    provider_run_id: str | None = None
    provider_task_id: str | None = None
    request_fingerprint: str
    input_script_version_id: str
    input_bible_version_id: str
    input_shot_revision: int = Field(default=1, ge=1)
    input_shot_revisions: dict[str, int] = Field(default_factory=dict[str, int])
    storyboard_document_id: str | None = None
    storyboard_version_id: str | None = None
    generation_plan_document_id: str | None = None
    generation_plan_version_id: str | None = None
    generation_unit_id: str | None = None
    clip_group_id: str | None = None
    output_slot_id: str | None = None
    shot_windows: list[GenerationUnitShotWindow] = Field(
        default_factory=list[GenerationUnitShotWindow]
    )
    input_voice_reference_revision: int = Field(default=0, ge=0)
    requested_duration_seconds: float
    attempt: int
    retry_of_task_id: str | None = None
    mock_behavior: MockBehavior = MockBehavior.SUCCESS
    error_code: str | None = None
    error_message: str | None = None
    submitted_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_generation_unit_binding(self) -> GenerationTask:
        if (self.storyboard_document_id is None) != (
            self.storyboard_version_id is None
        ):
            raise ValueError(
                "Storyboard document and version IDs must be supplied together"
            )
        if (self.generation_plan_document_id is None) != (
            self.generation_plan_version_id is None
        ):
            raise ValueError(
                "Generation-plan document and version IDs must be supplied together"
            )
        if not self.shot_windows:
            return self
        shot_ids = [item.shot_id for item in self.shot_windows]
        if shot_ids[0] != self.shot_id or len(shot_ids) != len(set(shot_ids)):
            raise ValueError(
                "Generation-task shot windows must be unique and start with shot_id"
            )
        if self.generation_unit_id is None:
            raise ValueError("Shot windows require a generation_unit_id")
        if set(self.input_shot_revisions) != set(shot_ids):
            raise ValueError(
                "Generation-task input revisions must cover every shot window"
            )
        if any(
            self.input_shot_revisions[item.shot_id] != item.shot_revision
            for item in self.shot_windows
        ):
            raise ValueError(
                "Generation-task shot windows and input revisions must agree"
            )
        return self

    def covered_shot_ids(self) -> list[str]:
        return (
            [item.shot_id for item in self.shot_windows]
            if self.shot_windows
            else [self.shot_id]
        )


class QaIssue(DomainModel):
    code: str
    severity: str
    message: str
    repair_layer: str
    category: QaFailureCategory = QaFailureCategory.OTHER


class QAReport(DomainModel):
    id: str
    take_id: str
    status: QaStatus
    hard_gate_passed: bool
    score: float = Field(ge=0.0, le=5.0)
    issues: list[QaIssue]
    checks: list[str]
    reviewer_notes: str | None = None
    reviewed_at: str | None = None
    created_at: str


class VideoFrameSample(DomainModel):
    source_take_id: str
    timestamp_seconds: float = Field(ge=0.0)
    image_path: str
    reasons: list[str] = Field(min_length=1, max_length=8)
    visual_change_score: float = Field(default=0.0, ge=0.0, le=1.0)


class VideoObservation(DomainModel):
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)
    description: str = Field(min_length=1, max_length=2000)
    confidence: ObservationConfidence

    @model_validator(mode="after")
    def validate_time_range(self) -> VideoObservation:
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("Video observation end_seconds must not precede start_seconds")
        return self


class Take(DomainModel):
    id: str
    project_id: str
    shot_id: str
    task_id: str
    candidate_index: int
    operation: OperationKind
    provider: str = "mock"
    model: str = "deterministic-media-v1"
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    storyboard_document_id: str | None = None
    storyboard_version_id: str | None = None
    generation_plan_document_id: str | None = None
    generation_plan_version_id: str | None = None
    generation_unit_id: str | None = None
    clip_group_id: str | None = None
    output_slot_id: str | None = None
    shot_windows: list[GenerationUnitShotWindow] = Field(
        default_factory=list[GenerationUnitShotWindow]
    )
    selected_shot_ids: list[str] = Field(default_factory=list[str])
    media_path: str
    thumbnail_path: str
    duration_seconds: float
    width: int
    height: int
    availability: TakeAvailability
    qa_status: QaStatus
    is_selected: bool
    freshness: Freshness
    qa_report: QAReport
    created_at: str

    @model_validator(mode="after")
    def validate_generation_unit_binding(self) -> Take:
        if (self.storyboard_document_id is None) != (
            self.storyboard_version_id is None
        ):
            raise ValueError(
                "Storyboard document and version IDs must be supplied together"
            )
        if (self.generation_plan_document_id is None) != (
            self.generation_plan_version_id is None
        ):
            raise ValueError(
                "Generation-plan document and version IDs must be supplied together"
            )
        covered = self.covered_shot_ids()
        if self.shot_windows:
            if covered[0] != self.shot_id or len(covered) != len(set(covered)):
                raise ValueError(
                    "Take shot windows must be unique and start with shot_id"
                )
            if self.generation_unit_id is None:
                raise ValueError("Take shot windows require a generation_unit_id")
        if self.is_selected and not self.selected_shot_ids:
            self.selected_shot_ids = list(covered)
        if len(self.selected_shot_ids) != len(set(self.selected_shot_ids)):
            raise ValueError("Selected Take shot IDs must be unique")
        if any(shot_id not in covered for shot_id in self.selected_shot_ids):
            raise ValueError("A Take may select only shots covered by its media")
        self.is_selected = bool(self.selected_shot_ids)
        return self

    def covered_shot_ids(self) -> list[str]:
        return (
            [item.shot_id for item in self.shot_windows]
            if self.shot_windows
            else [self.shot_id]
        )

    def source_window_for_shot(
        self,
        shot_id: str,
    ) -> GenerationUnitShotWindow | None:
        return next(
            (item for item in self.shot_windows if item.shot_id == shot_id),
            None,
        )


class RouteEvidence(DomainModel):
    id: str
    project_id: str
    shot_id: str
    task_id: str
    take_id: str | None = None
    provider: str
    model: str
    route: VideoGenerationRoute
    resolution: str
    purpose: EvidencePurpose
    status: EvidenceStatus
    technical_passed: bool | None = None
    hard_gate_passed: bool | None = None
    score: float | None = Field(default=None, ge=0.0, le=5.0)
    notes: str | None = None
    created_at: str
    reviewed_at: str | None = None


class TechnicalCheck(DomainModel):
    name: str
    passed: bool
    detail: str


class VideoInspectionReport(DomainModel):
    id: str
    project_id: str
    shot_id: str
    take_id: str
    previous_take_id: str | None = None
    scopes: list[VideoInspectionScope] = Field(min_length=1, max_length=2)
    duration_seconds: float = Field(gt=0.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0.0)
    audio_present: bool
    samples: list[VideoFrameSample] = Field(min_length=2, max_length=32)
    contact_sheet_path: str
    boundary_contact_sheet_path: str | None = None
    technical_checks: list[TechnicalCheck]
    semantic_status: SemanticReviewStatus
    semantic_model: str | None = None
    observations: list[VideoObservation] = Field(default_factory=list[VideoObservation])
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])
    summary: str = Field(min_length=1, max_length=3000)
    warnings: list[str] = Field(default_factory=list[str])
    created_at: str


class TakeComparisonCandidateReview(DomainModel):
    take_id: str
    relative_score: float = Field(ge=0.0, le=5.0)
    strengths: list[str] = Field(default_factory=list[str], max_length=12)
    weaknesses: list[str] = Field(default_factory=list[str], max_length=12)


class TakeComparisonReport(DomainModel):
    id: str
    project_id: str
    shot_id: str
    candidate_take_ids: list[str] = Field(min_length=2)
    inspection_ids: list[str] = Field(min_length=2)
    decision: TakeComparisonDecision
    selected_take_id: str | None = None
    semantic_status: SemanticReviewStatus
    semantic_model: str | None = None
    candidate_reviews: list[TakeComparisonCandidateReview] = Field(
        default_factory=list[TakeComparisonCandidateReview]
    )
    summary: str = Field(min_length=1, max_length=3000)
    warnings: list[str] = Field(default_factory=list[str], max_length=16)
    advisory_only: bool = True
    created_at: str

    @model_validator(mode="after")
    def validate_decision(self) -> TakeComparisonReport:
        if len(set(self.candidate_take_ids)) != len(self.candidate_take_ids):
            raise ValueError("Take comparison candidates must be unique")
        if self.decision == TakeComparisonDecision.SELECT_TAKE:
            if self.selected_take_id not in self.candidate_take_ids:
                raise ValueError("Selected Take must be one of the compared candidates")
        elif self.selected_take_id is not None:
            raise ValueError("Only SELECT_TAKE may declare selected_take_id")
        review_ids = [item.take_id for item in self.candidate_reviews]
        if len(set(review_ids)) != len(review_ids):
            raise ValueError("Take comparison candidate reviews must be unique")
        if any(take_id not in self.candidate_take_ids for take_id in review_ids):
            raise ValueError("Candidate review refers to an unlisted Take")
        return self


class AudioTimeRange(DomainModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_order(self) -> AudioTimeRange:
        if self.end_seconds < self.start_seconds:
            raise ValueError("Audio time range end must not precede start")
        return self


class AudioSignalMetrics(DomainModel):
    duration_seconds: float = Field(ge=0.0)
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    peak_dbfs: float
    rms_dbfs: float
    clipped_sample_ratio: float = Field(ge=0.0, le=1.0)
    silent_window_ratio: float = Field(ge=0.0, le=1.0)
    leading_silence_seconds: float = Field(ge=0.0)
    trailing_silence_seconds: float = Field(ge=0.0)
    speech_intervals: list[AudioTimeRange] = Field(
        default_factory=list[AudioTimeRange],
        max_length=128,
    )


class AudioClipInspection(DomainModel):
    clip_id: str
    shot_id: str
    take_id: str
    media_path: str
    timeline_start_seconds: float = Field(ge=0.0)
    expected_duration_seconds: float = Field(gt=0.0)
    extracted_audio_path: str | None = None
    metrics: AudioSignalMetrics | None = None
    technical_checks: list[TechnicalCheck] = Field(default_factory=list[TechnicalCheck])
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])


class DialogueAudioCheck(DomainModel):
    cue_id: str
    script_line_id: str
    shot_id: str
    take_id: str
    expected_speaker: str
    expected_text: str
    cue_start_seconds: float = Field(ge=0.0)
    cue_end_seconds: float = Field(ge=0.0)
    extracted_audio_path: str | None = None
    transcription_status: SemanticReviewStatus
    transcript_text: str | None = None
    normalized_expected: str
    normalized_transcript: str | None = None
    exact_match: bool | None = None
    character_error_rate: float | None = Field(default=None, ge=0.0)
    speech_coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    provider: str | None = None
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])

    @model_validator(mode="after")
    def validate_cue_order(self) -> DialogueAudioCheck:
        if self.cue_end_seconds < self.cue_start_seconds:
            raise ValueError("Dialogue cue end must not precede start")
        return self


class SpeakerVoiceContinuityCheck(DomainModel):
    speaker: str
    voice_identity: str | None = None
    script_line_ids: list[str] = Field(default_factory=list[str])
    comparison_count: int = Field(ge=0)
    minimum_same_speaker_similarity: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
    )
    median_same_speaker_similarity: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
    )
    maximum_other_speaker_similarity: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
    )
    assessment: AudioContinuityAssessment
    detail: str


class AudioInspectionReport(DomainModel):
    id: str
    project_id: str
    manifest_id: str
    source_script_version_id: str
    semantic_status: SemanticReviewStatus
    semantic_provider: str | None = None
    transcription_model: str | None = None
    speaker_model: str | None = None
    clip_inspections: list[AudioClipInspection] = Field(
        default_factory=list[AudioClipInspection]
    )
    dialogue_checks: list[DialogueAudioCheck] = Field(
        default_factory=list[DialogueAudioCheck]
    )
    speaker_checks: list[SpeakerVoiceContinuityCheck] = Field(
        default_factory=list[SpeakerVoiceContinuityCheck]
    )
    technical_checks: list[TechnicalCheck] = Field(default_factory=list[TechnicalCheck])
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])
    summary: str = Field(min_length=1, max_length=4000)
    warnings: list[str] = Field(default_factory=list[str])
    created_at: str


class EpisodeReviewFinding(DomainModel):
    id: str
    dimension: EpisodeReviewDimension
    issue: QaIssue
    evidence: str = Field(min_length=1, max_length=2400)
    scene_ids: list[str] = Field(default_factory=list[str], max_length=24)
    shot_ids: list[str] = Field(default_factory=list[str], max_length=64)
    recommended_action: str = Field(min_length=1, max_length=1600)
    repair_recommended: bool = False
    dismissible: bool = True


class SceneEpisodeReview(DomainModel):
    scene_id: str
    scene_sequence: int = Field(ge=1)
    shot_ids: list[str] = Field(min_length=1, max_length=128)
    inspection_ids: list[str] = Field(default_factory=list[str], max_length=128)
    observations: list[str] = Field(default_factory=list[str], max_length=32)
    findings: list[EpisodeReviewFinding] = Field(
        default_factory=list[EpisodeReviewFinding],
        max_length=32,
    )
    summary: str = Field(min_length=1, max_length=3000)


class EpisodeReviewReport(DomainModel):
    id: str
    project_id: str
    manifest_id: str
    source_script_version_id: str
    status: EpisodeReviewStatus
    semantic_model: str | None = None
    scene_reviews: list[SceneEpisodeReview] = Field(
        default_factory=list[SceneEpisodeReview],
        max_length=500,
    )
    next_scene_index: int = Field(default=0, ge=0, le=500)
    findings: list[EpisodeReviewFinding] = Field(
        default_factory=list[EpisodeReviewFinding],
        max_length=256,
    )
    summary: str = Field(default="整集审阅进行中。", min_length=1, max_length=5000)
    warnings: list[str] = Field(default_factory=list[str])
    active_stage_key: str | None = Field(default=None, max_length=240)
    active_stage_attempts: int = Field(default=0, ge=0)
    repair_applied_shot_ids: list[str] = Field(default_factory=list[str], max_length=128)
    repair_skipped_shot_ids: list[str] = Field(default_factory=list[str], max_length=128)
    repairs_complete: bool = False
    advisory_only: bool = True
    created_at: str
    updated_at: str


class ExportRecord(DomainModel):
    id: str
    project_id: str
    manifest_id: str
    clean_video_path: str
    subtitled_video_path: str
    srt_path: str
    ass_path: str
    manifest_path: str
    qa_report_path: str
    checks: list[TechnicalCheck]
    created_at: str


class ProgressSummary(DomainModel):
    total_shots: int
    shots_with_selected_take: int
    total_tasks: int
    terminal_tasks: int
    failed_tasks: int
    unresolved_tasks: int
    passed_takes: int
    stale_takes: int
    manifest_ready: bool
    export_ready: bool


class ProjectAggregate(DomainModel):
    document: ProjectDocument
    tasks: list[GenerationTask]
    takes: list[Take]
    exports: list[ExportRecord]
    route_evidence: list[RouteEvidence] = Field(default_factory=list[RouteEvidence])
    video_inspections: list[VideoInspectionReport] = Field(
        default_factory=list[VideoInspectionReport]
    )
    take_comparisons: list[TakeComparisonReport] = Field(
        default_factory=list[TakeComparisonReport]
    )
    audio_inspections: list[AudioInspectionReport] = Field(
        default_factory=list[AudioInspectionReport]
    )
    episode_reviews: list[EpisodeReviewReport] = Field(
        default_factory=list[EpisodeReviewReport]
    )
    progress: ProgressSummary


class ProjectListResponse(DomainModel):
    projects: list[ProjectRecord]


class CreateProjectRequest(DomainModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_mode: SourceMode
    production_profile: ProductionProfile = ProductionProfile.AI_SHORT_DRAMA
    content_planning_output: ContentPlanningOutput = (
        ContentPlanningOutput.SCRIPT_AND_SHOTS
    )
    target_duration_seconds: float | None = Field(default=None, ge=4.0)
    input_text: str = Field(default="", max_length=50000)
    locked_dialogue: list[StoryLockedDialogueLine] = Field(
        default_factory=list[StoryLockedDialogueLine],
        max_length=5000,
    )
    input_images: list[CreativeInputImage] = Field(
        default_factory=list[CreativeInputImage],
        max_length=8,
    )
    linked_asset_project_ids: list[str] = Field(
        default_factory=list[str],
        max_length=32,
    )
    run_mode: RunMode = RunMode.AUTO_DRAFT

    @model_validator(mode="after")
    def validate_input_contract(self) -> CreateProjectRequest:
        text = self.input_text.strip()
        if self.source_mode in {SourceMode.IDEA_EXPANSION, SourceMode.SCRIPT_LOCKED} and not text:
            raise ValueError(f"{self.source_mode.value} requires input_text")
        if self.locked_dialogue and self.source_mode != SourceMode.SCRIPT_LOCKED:
            raise ValueError("locked_dialogue is only valid for SCRIPT_LOCKED input")
        cursor = 0
        for line in self.locked_dialogue:
            position = text.find(line.text, cursor)
            if position < 0:
                raise ValueError(
                    "Every structured locked-dialogue line must appear verbatim in "
                    "input_text and preserve source order"
                )
            cursor = position + len(line.text)
        if self.source_mode == SourceMode.IMAGE_SEEDED and not self.input_images:
            raise ValueError("IMAGE_SEEDED requires at least one input image")
        return self

    def resolved_target_duration_seconds(self) -> float:
        if self.target_duration_seconds is not None:
            return self.target_duration_seconds
        if self.production_profile == ProductionProfile.AI_SHORT_DRAMA:
            return 300.0
        return 60.0


class VideoInspectionRequest(DomainModel):
    previous_take_id: str | None = Field(default=None, max_length=200)
    include_previous_boundary: bool = True
    semantic_review: bool = True
    max_frames: int = Field(default=14, ge=6, le=24)
    review_focus: str | None = Field(default=None, max_length=2000)


class TakeComparisonRequest(DomainModel):
    shot_id: str
    take_ids: list[str] = Field(default_factory=list[str])
    inspect_missing: bool = True
    include_previous_boundary: bool = True
    review_focus: str | None = Field(default=None, max_length=2000)
    apply_selection: bool = False


class TakeComparisonResponse(DomainModel):
    report: TakeComparisonReport
    project: ProjectAggregate


class AudioInspectionRequest(DomainModel):
    semantic_review: bool = True
    speaker_continuity: bool = True
    dialogue_padding_seconds: float = Field(default=0.35, ge=0.0, le=1.5)


class EpisodeReviewRequest(DomainModel):
    max_actions: int = Field(default=32, ge=1, le=200)
    force_new: bool = False


class SceneTimingAssessmentRecord(DomainModel):
    """One Writer timing self-assessment kept as inspectable evidence.

    The record does not approve, reject, or route creative work.  Later Agents
    remain responsible for their own semantic reading of the screenplay.
    """

    scene_sequence: int = Field(ge=1, le=500)
    stage: Literal["INITIAL_WRITE", "REVISION"]
    revision_round: int = Field(ge=0)
    target_duration_seconds: float = Field(gt=0.0)
    assessment: str = Field(min_length=1, max_length=1600)


class PlanningRunRecord(DomainModel):
    """Persisted Content Planning Job cursor owned by one AgentRun.

    ``PlanningRunRecord`` remains the compatibility name used by persisted data.
    It owns only staged content-planning progress; canonical project content lives
    in :class:`ProjectDocument`, while the owning AgentRun remains the orchestrator.
    """

    id: str
    job_kind: Literal["CONTENT_PLANNING"] = "CONTENT_PLANNING"
    operation: PlanningRunOperation = PlanningRunOperation.INITIAL
    status: PlanningRunStatus
    current_step: PlanningRunStep
    completed_steps: list[PlanningRunStep] = Field(
        default_factory=list[PlanningRunStep]
    )
    request: CreateProjectRequest
    revision_instruction: str | None = Field(default=None, max_length=50000)
    revision_origin: ScriptRevisionOrigin = ScriptRevisionOrigin.USER_FEEDBACK
    revision_evidence_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    base_script_version_id: str | None = Field(default=None, max_length=240)
    base_bible_version_id: str | None = Field(default=None, max_length=240)
    parent_planning_run_id: str | None = Field(default=None, max_length=240)
    outline_json: str | None = None
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    final_cold_read_report: ScriptReviewReport | None = None
    source_contract: StorySourceContract | None = None
    story_intent: StoryIntent | None = None
    selected_narrative_skill_ids: list[str] = Field(
        default_factory=list[str],
        max_length=24,
    )
    skill_selection_reasons: list[str] = Field(
        default_factory=list[str],
        max_length=24,
    )
    narrative_skill_decision: str | None = Field(default=None, max_length=2400)
    selected_method_skill_ids: list[str] = Field(
        default_factory=list[str],
        max_length=24,
    )
    method_selection_reasons: list[str] = Field(
        default_factory=list[str],
        max_length=24,
    )
    method_selection_rationale: str | None = Field(default=None, max_length=2400)
    issue_ledger: list[StoryIssueLedgerEntry] = Field(
        default_factory=list[StoryIssueLedgerEntry]
    )
    latest_repair_plan: StoryRepairPlan | None = None
    latest_story_metadata_patch: StoryMetadataRevisionPatch | None = None
    latest_revision_verification: StoryRevisionVerification | None = None
    story_verification_resolutions: list[StoryVerificationResolution] = Field(
        default_factory=list[StoryVerificationResolution]
    )
    revision_judge: RevisionJudgeRecommendation | None = None
    revision_base_outline_json: str | None = None
    revision_scene_sequences: list[int] = Field(
        default_factory=list[int],
        max_length=500,
    )
    next_revision_scene_index: int = Field(default=0, ge=0, le=500)
    revision_scene_retry_feedback: str | None = Field(
        default=None,
        max_length=2000,
    )
    revision_scene_retry_count: int = Field(default=0, ge=0)
    revision_changed_scene_sequences: list[int] = Field(
        default_factory=list[int],
        max_length=500,
    )
    latest_revision_target_scene_sequences: list[int] = Field(
        default_factory=list[int],
        max_length=500,
    )
    latest_revision_changed_scene_sequences: list[int] = Field(
        default_factory=list[int],
        max_length=500,
    )
    revision_repair_round: int = Field(default=0, ge=0)
    scene_script_json: list[str] = Field(default_factory=list[str], max_length=500)
    scene_timing_assessments: list[SceneTimingAssessmentRecord] = Field(
        default_factory=list[SceneTimingAssessmentRecord],
        max_length=1000,
    )
    next_script_scene_sequence: int = Field(default=1, ge=1, le=501)
    scene_script_retry_feedback: str | None = Field(
        default=None,
        max_length=2000,
    )
    scene_script_retry_count: int = Field(default=0, ge=0)
    # This field also carries structured semantic handoffs (for example, a
    # Leader repair plan returned to Story Architect), not only short provider
    # retry text.  Those handoffs are already bounded by their typed source
    # models and can legitimately exceed the legacy 2,000-character limit.
    step_retry_feedback: str | None = None
    step_retry_count: int = Field(default=0, ge=0)
    step_retry_events: list[str] = Field(default_factory=list[str])
    directed_scene_json: list[str] = Field(default_factory=list[str], max_length=500)
    next_scene_sequence: int = Field(default=1, ge=1, le=501)
    total_scenes: int = Field(default=0, ge=0, le=500)
    revision_round: int = Field(default=0, ge=0)
    # Compatibility-only field for records written before revision rounds became
    # open-ended. The planning state machine no longer consults this value.
    max_revision_rounds: int | None = Field(default=None, ge=0)
    outline_source: str | None = Field(default=None, max_length=240)
    critic_used: bool = False
    final_cold_read_used: bool = False
    used_scene_repair: bool = False
    used_scene_fallback: bool = False
    warnings: list[str] = Field(default_factory=list[str])
    error_message: str | None = None
    project_id: str = Field(min_length=1, max_length=200)
    owner_agent_run_id: str = Field(min_length=1, max_length=200)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class PlanningRunResponse(DomainModel):
    run: PlanningRunRecord
    project: ProjectAggregate | None = None


class EnqueueRequest(DomainModel):
    shot_ids: list[str] | None = None
    candidate_count: int = Field(default=2, ge=1)
    operation: OperationKind = OperationKind.GENERATE
    behavior_by_candidate: dict[str, MockBehavior] = Field(default_factory=dict)


class WorkerDrainRequest(DomainModel):
    project_id: str
    max_steps: int = Field(default=500, ge=1, le=5000)


class WorkerDrainResponse(DomainModel):
    processed_steps: int
    remaining_runnable: int
    project: ProjectAggregate


class TaskActionResponse(DomainModel):
    task: GenerationTask
    project: ProjectAggregate


class TakeSelectionResponse(DomainModel):
    selected_take: Take
    project: ProjectAggregate


class ReviseScriptLineRequest(DomainModel):
    line_id: str
    spoken_text: str = Field(min_length=1)
    subtitle_text: str = Field(min_length=1)
    change_reason: str = Field(min_length=1)


class ScriptLineRevision(DomainModel):
    line_id: str
    spoken_text: str = Field(min_length=1)
    subtitle_text: str = Field(min_length=1)


class ReviseScriptLinesRequest(DomainModel):
    revisions: list[ScriptLineRevision] = Field(min_length=1, max_length=500)
    change_reason: str = Field(min_length=1)


class LocalReworkRequest(DomainModel):
    behavior: MockBehavior = MockBehavior.SUCCESS
    duration_delta_seconds: float = Field(default=0.0, ge=-2.0, le=4.0)


class ExportRequest(DomainModel):
    rebuild_manifest: bool = True


class VideoInspectionResponse(DomainModel):
    report: VideoInspectionReport
    project: ProjectAggregate


class AudioInspectionResponse(DomainModel):
    report: AudioInspectionReport
    project: ProjectAggregate


class EpisodeReviewResponse(DomainModel):
    report: EpisodeReviewReport
    project: ProjectAggregate


class StatusMessage(DomainModel):
    status: str
    message: str
