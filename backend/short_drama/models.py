"""Typed domain and API models for the short-drama production workflow."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    DOUYIN_SHORT_DRAMA = "DOUYIN_SHORT_DRAMA"


class AutonomousExecutionTarget(StrEnum):
    MOCK = "MOCK"
    REAL_H3 = "REAL_H3"


class AutonomousRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"


class AutonomousRunStep(StrEnum):
    NORMALIZE_INPUT = "NORMALIZE_INPUT"
    PLAN = "PLAN"
    IMPORT_INPUT_ASSETS = "IMPORT_INPUT_ASSETS"
    PREPARE_ASSETS = "PREPARE_ASSETS"
    PLAN_ROUTES = "PLAN_ROUTES"
    PREPARE_SHOT_ASSETS = "PREPARE_SHOT_ASSETS"
    GENERATE = "GENERATE"
    QA_AND_REPAIR = "QA_AND_REPAIR"
    TIMELINE = "TIMELINE"
    EPISODE_REVIEW = "EPISODE_REVIEW"
    AUDIO_QA = "AUDIO_QA"
    EXPORT = "EXPORT"
    COMPLETE = "COMPLETE"


class PlanningRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"


class PlanningRunStep(StrEnum):
    STORY_ARCHITECT = "STORY_ARCHITECT"
    SCENE_WRITER = "SCENE_WRITER"
    SCRIPT_CRITIC = "SCRIPT_CRITIC"
    STORY_REVISION = "STORY_REVISION"
    REVISION_JUDGE = "REVISION_JUDGE"
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


class ScriptQualityTrend(StrEnum):
    NOT_COMPARABLE = "NOT_COMPARABLE"
    FEWER_REPORTED_ISSUES = "FEWER_REPORTED_ISSUES"
    MIXED_REPORTED_ISSUES = "MIXED_REPORTED_ISSUES"
    MORE_REPORTED_ISSUES = "MORE_REPORTED_ISSUES"
    UNCHANGED_REPORTED_ISSUES = "UNCHANGED_REPORTED_ISSUES"


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


class ReviewRepairOwner(StrEnum):
    """Smallest production layer that can reasonably address a review issue."""

    STORY_ARCHITECT = "STORY_ARCHITECT"
    SCENE_DIRECTOR = "SCENE_DIRECTOR"
    ASSET_AGENT = "ASSET_AGENT"
    VIDEO_DIRECTOR = "VIDEO_DIRECTOR"
    TIMELINE_AGENT = "TIMELINE_AGENT"
    USER_OPTIONAL = "USER_OPTIONAL"


class StoryRepairDiagnosis(StrEnum):
    """Where a routed story issue actually originates after a second look."""

    SOURCE_CONCEPT_GAP = "SOURCE_CONCEPT_GAP"
    OUTLINE_DEFECT = "OUTLINE_DEFECT"
    CRITIC_OVERREACH = "CRITIC_OVERREACH"


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


class ReferenceRole(StrEnum):
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
    production_profile: ProductionProfile = ProductionProfile.DOUYIN_SHORT_DRAMA
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
    narrative_purpose: str | None = None
    target_duration_seconds: float | None = Field(default=None, ge=4.0)
    entry_state: SceneContinuitySummary | None = None
    exit_state: SceneContinuitySummary | None = None


class ScriptReviewIssue(DomainModel):
    category: str = Field(min_length=1, max_length=80)
    severity: ScriptReviewSeverity
    scene_sequences: list[int] = Field(default_factory=list[int], max_length=12)
    observation: str = Field(min_length=1, max_length=1200)
    recommendation: str = Field(min_length=1, max_length=1200)


class ScriptReviewReport(DomainModel):
    summary: str = Field(min_length=1, max_length=2000)
    strengths: list[str] = Field(default_factory=list[str], max_length=12)
    issues: list[ScriptReviewIssue] = Field(default_factory=list[ScriptReviewIssue], max_length=30)
    requires_revision: bool = False


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
        if self.action == ScriptReviewDispositionAction.REVISE_STORY and not any(
            route.owner == ReviewRepairOwner.STORY_ARCHITECT
            for route in self.issue_routes
        ):
            raise ValueError("REVISE_STORY requires at least one Story Architect issue")
        return self


class StorySourceContract(DomainModel):
    """Explicit user promises separated from creative choices left to the Agents."""

    explicit_commitments: list[str] = Field(min_length=1, max_length=30)
    explicit_prohibitions: list[str] = Field(default_factory=list[str], max_length=20)
    open_creative_choices: list[str] = Field(default_factory=list[str], max_length=20)
    authoritative_user_overrides: list[str] = Field(
        default_factory=list[str],
        max_length=20,
    )
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_source_contract(self) -> StorySourceContract:
        if not self.user_overridable:
            raise ValueError("Story source commitments must remain user-overridable")
        return self


class StoryRepairAction(DomainModel):
    """One evidence-backed recommendation; it is not an execution gate."""

    issue_index: int = Field(ge=1)
    diagnosis: StoryRepairDiagnosis
    should_revise: bool
    rationale: str = Field(min_length=1, max_length=1200)
    affected_scene_sequences: list[int] = Field(
        default_factory=list[int],
        max_length=12,
    )
    recommended_change: str = Field(min_length=1, max_length=1600)
    remove_or_simplify: list[str] = Field(default_factory=list[str], max_length=12)
    preserve: list[str] = Field(default_factory=list[str], max_length=12)
    validation_questions: list[str] = Field(default_factory=list[str], max_length=12)

    @model_validator(mode="after")
    def validate_action(self) -> StoryRepairAction:
        if self.diagnosis == StoryRepairDiagnosis.CRITIC_OVERREACH and self.should_revise:
            raise ValueError("Critic overreach cannot require an automatic story rewrite")
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
        issue_indices = [action.issue_index for action in self.actions]
        if len(issue_indices) != len(set(issue_indices)):
            raise ValueError("Each routed story issue must be diagnosed exactly once")
        if not self.user_overridable:
            raise ValueError("Story repair recommendations must remain user-overridable")
        return self


class StoryRevisionViolation(DomainModel):
    category: str = Field(min_length=1, max_length=120)
    scene_sequences: list[int] = Field(default_factory=list[int], max_length=12)
    observation: str = Field(min_length=1, max_length=1200)
    repair_instruction: str = Field(min_length=1, max_length=1200)


class StoryRevisionVerification(DomainModel):
    """Narrow check that a Writer followed its repair brief without regressions."""

    accepted: bool
    summary: str = Field(min_length=1, max_length=1600)
    satisfied_issue_indices: list[int] = Field(default_factory=list[int], max_length=30)
    violations: list[StoryRevisionViolation] = Field(
        default_factory=list[StoryRevisionViolation],
        max_length=20,
    )
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_verification(self) -> StoryRevisionVerification:
        if self.accepted and self.violations:
            raise ValueError("Accepted story revisions cannot retain plan violations")
        if not self.accepted and not self.violations:
            raise ValueError("Rejected story revisions must name a concrete violation")
        if len(self.satisfied_issue_indices) != len(set(self.satisfied_issue_indices)):
            raise ValueError("Satisfied repair issue indices must be unique")
        if not self.user_overridable:
            raise ValueError("Story revision verification must remain user-overridable")
        return self


class PlanningOutlineSnapshot(DomainModel):
    revision_round: int = Field(ge=0, le=20)
    outline_json: str
    source: str = Field(min_length=1, max_length=240)
    source_contract: StorySourceContract | None = None
    repair_plan: StoryRepairPlan | None = None
    revision_verification: StoryRevisionVerification | None = None
    created_at: str


class PlanningReviewSnapshot(DomainModel):
    review_round: int = Field(ge=1, le=21)
    revision_round: int = Field(ge=0, le=20)
    report: ScriptReviewReport
    disposition: ScriptReviewDisposition | None = None
    created_at: str


class RevisionJudgeRecommendation(DomainModel):
    recommended_revision_round: int = Field(ge=0, le=20)
    alternative_revision_round: int = Field(ge=0, le=20)
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


class ScriptQualityRoundSummary(DomainModel):
    review_round: int = Field(ge=1, le=21)
    revision_round: int = Field(ge=0, le=20)
    total_issues: int = Field(ge=0)
    high_issues: int = Field(ge=0)
    medium_issues: int = Field(ge=0)
    low_issues: int = Field(ge=0)
    requires_revision: bool
    categories: list[str] = Field(default_factory=list[str], max_length=30)


class ScriptQualityComparison(DomainModel):
    run_id: str
    project_id: str | None = None
    project_name: str
    production_profile: ProductionProfile
    source_mode: SourceMode
    target_duration_seconds: float
    rounds: list[ScriptQualityRoundSummary] = Field(
        default_factory=list[ScriptQualityRoundSummary],
        max_length=21,
    )
    trend: ScriptQualityTrend
    total_issue_delta: int | None = None
    high_issue_delta: int | None = None
    latest_total_issue_delta: int | None = None
    latest_high_issue_delta: int | None = None
    latest_report_regression_risk: bool = False
    persistent_category_labels: list[str] = Field(default_factory=list[str], max_length=30)
    category_labels_no_longer_reported: list[str] = Field(
        default_factory=list[str], max_length=30
    )
    newly_reported_category_labels: list[str] = Field(
        default_factory=list[str], max_length=30
    )
    advisory_summary: str = Field(min_length=1, max_length=1200)


class ScriptQualityHistoryResponse(DomainModel):
    comparison: ScriptQualityComparison
    outline_snapshots: list[PlanningOutlineSnapshot] = Field(
        default_factory=list[PlanningOutlineSnapshot],
        max_length=21,
    )
    review_snapshots: list[PlanningReviewSnapshot] = Field(
        default_factory=list[PlanningReviewSnapshot],
        max_length=21,
    )
    revision_judge: RevisionJudgeRecommendation | None = None


class ScriptQualityCatalogResponse(DomainModel):
    evaluations: list[ScriptQualityComparison]


class ScriptVersion(DomainModel):
    id: str
    project_id: str
    version: int
    source_mode: SourceMode
    original_text: str
    spoken_text: str
    subtitle_text: str
    lines: list[ScriptLine]
    structured_scenes: list[StructuredScene]
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
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
    continuity: ContinuityState
    transition_out: str
    merge_candidate: bool
    local_rework_risk: str
    approval_status: ApprovalStatus
    freshness: Freshness
    revision: int = Field(default=1, ge=1)


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
    kind: str
    target_id: str
    purpose: str
    requirement: str


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
    media_path: str
    start_seconds: float
    source_in_seconds: float
    duration_seconds: float
    transition_in: str
    transition_out: str


class TimelineAudioClip(DomainModel):
    id: str
    shot_id: str
    take_id: str
    media_path: str
    start_seconds: float
    duration_seconds: float
    gain_db: float
    role: str


class SubtitleCue(DomainModel):
    id: str
    script_line_id: str
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str


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


class AutonomousRunRecord(DomainModel):
    id: str
    project_id: str
    status: AutonomousRunStatus
    current_step: AutonomousRunStep
    completed_steps: list[AutonomousRunStep] = Field(
        default_factory=list[AutonomousRunStep]
    )
    planner: str
    execution_target: AutonomousExecutionTarget = AutonomousExecutionTarget.MOCK
    route_plan_id: str | None = None
    episode_review_id: str | None = None
    repair_round: int = Field(default=0, ge=0)
    max_repair_rounds: int = Field(default=3, ge=0, le=5)
    render_export: bool = True
    waiting_reason: str | None = None
    warnings: list[str] = Field(default_factory=list[str])
    error_message: str | None = None
    export_id: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ProjectDocument(DomainModel):
    project: ProjectRecord
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
    autonomous_runs: list[AutonomousRunRecord] = Field(
        default_factory=list[AutonomousRunRecord]
    )


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
    candidate_take_ids: list[str] = Field(min_length=2, max_length=5)
    inspection_ids: list[str] = Field(min_length=2, max_length=10)
    decision: TakeComparisonDecision
    selected_take_id: str | None = None
    semantic_status: SemanticReviewStatus
    semantic_model: str | None = None
    candidate_reviews: list[TakeComparisonCandidateReview] = Field(
        default_factory=list[TakeComparisonCandidateReview],
        max_length=5,
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
    warnings: list[str] = Field(default_factory=list[str], max_length=128)
    active_stage_key: str | None = Field(default=None, max_length=240)
    active_stage_attempts: int = Field(default=0, ge=0, le=3)
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
    production_profile: ProductionProfile = ProductionProfile.DOUYIN_SHORT_DRAMA
    target_duration_seconds: float | None = Field(default=None, ge=4.0)
    input_text: str = Field(default="", max_length=50000)
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
        if self.source_mode == SourceMode.IMAGE_SEEDED and not self.input_images:
            raise ValueError("IMAGE_SEEDED requires at least one input image")
        return self

    def resolved_target_duration_seconds(self) -> float:
        if self.target_duration_seconds is not None:
            return self.target_duration_seconds
        if self.production_profile == ProductionProfile.DOUYIN_SHORT_DRAMA:
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
    take_ids: list[str] = Field(default_factory=list[str], max_length=5)
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


class AutonomousDraftCreateRequest(CreateProjectRequest):
    run_mode: RunMode = RunMode.AUTO_DRAFT
    render_export: bool = True
    execution_target: AutonomousExecutionTarget = AutonomousExecutionTarget.MOCK

    @model_validator(mode="after")
    def require_auto_mode(self) -> AutonomousDraftCreateRequest:
        if self.run_mode != RunMode.AUTO_DRAFT:
            raise ValueError("Autonomous draft creation requires AUTO_DRAFT")
        return self


class PlanningRunCreateRequest(CreateProjectRequest):
    """Persisted duration-agnostic planning request before a project exists."""

    render_export: bool = True
    execution_target: AutonomousExecutionTarget = AutonomousExecutionTarget.MOCK


class PlanningRunRecord(DomainModel):
    id: str
    status: PlanningRunStatus
    current_step: PlanningRunStep
    completed_steps: list[PlanningRunStep] = Field(
        default_factory=list[PlanningRunStep]
    )
    request: PlanningRunCreateRequest
    outline_json: str | None = None
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    source_contract: StorySourceContract | None = None
    latest_repair_plan: StoryRepairPlan | None = None
    latest_revision_verification: StoryRevisionVerification | None = None
    revision_judge: RevisionJudgeRecommendation | None = None
    scene_script_json: list[str] = Field(default_factory=list[str], max_length=500)
    next_script_scene_sequence: int = Field(default=1, ge=1, le=501)
    directed_scene_json: list[str] = Field(default_factory=list[str], max_length=500)
    next_scene_sequence: int = Field(default=1, ge=1, le=501)
    total_scenes: int = Field(default=0, ge=0, le=500)
    revision_round: int = Field(default=0, ge=0, le=20)
    max_revision_rounds: int = Field(default=3, ge=0, le=3)
    outline_source: str | None = Field(default=None, max_length=240)
    critic_used: bool = False
    used_scene_repair: bool = False
    used_scene_fallback: bool = False
    warnings: list[str] = Field(default_factory=list[str])
    error_message: str | None = None
    project_id: str | None = None
    autonomous_run_id: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class PlanningRunListResponse(DomainModel):
    runs: list[PlanningRunRecord]


class PlanningRunResponse(DomainModel):
    run: PlanningRunRecord
    project: ProjectAggregate | None = None


class PlanningOutlineDirectEditRequest(DomainModel):
    outline_json: str = Field(min_length=2, max_length=250000)
    change_reason: str = Field(min_length=1, max_length=2000)


class PlanningOutlineAgentRevisionRequest(DomainModel):
    instruction: str = Field(min_length=1, max_length=8000)


class AutonomousRepeatCreateRequest(DomainModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    render_export: bool = True


class EnqueueRequest(DomainModel):
    shot_ids: list[str] | None = None
    candidate_count: int = Field(default=2, ge=1, le=4)
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


class AutoDraftRequest(DomainModel):
    render_export: bool = True


class AutonomousRunAdvanceRequest(DomainModel):
    max_actions: int = Field(default=24, ge=1, le=200)


class AutoDraftResponse(DomainModel):
    project: ProjectAggregate
    export: ExportRecord | None


class AutonomousDraftResponse(AutoDraftResponse):
    run: AutonomousRunRecord


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
