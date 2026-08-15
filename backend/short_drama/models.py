"""Typed domain and API models for the short-drama production workflow."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    """Base model shared by persisted domain documents and API payloads."""

    model_config = ConfigDict(extra="forbid")


class SourceMode(StrEnum):
    IDEA_EXPANSION = "IDEA_EXPANSION"
    SCRIPT_LOCKED = "SCRIPT_LOCKED"


class RunMode(StrEnum):
    AUTO_DRAFT = "AUTO_DRAFT"
    GUIDED = "GUIDED"


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


class ProjectRecord(DomainModel):
    id: str
    name: str
    source_mode: SourceMode
    run_mode: RunMode
    language: str = "zh-CN"
    aspect_ratio: str = "9:16"
    target_duration_seconds: float = 50.0
    input_text: str
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


class StructuredScene(DomainModel):
    id: str
    heading: str
    summary: str
    line_ids: list[str]


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


class ReferencePlanEntry(DomainModel):
    shot_id: str
    bindings: list[ReferenceBinding]
    capability_valid: bool
    validation_notes: list[str]


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


class ProjectDocument(DomainModel):
    project: ProjectRecord
    script_versions: list[ScriptVersion]
    bible_versions: list[ProjectBibleVersion]
    assets: list[Asset]
    shots: list[ShotPlan]
    execution_plan: list[ExecutionPlanEntry]
    reference_plan: list[ReferencePlanEntry]
    manifests: list[TimelineManifest]


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
    progress: ProgressSummary


class ProjectListResponse(DomainModel):
    projects: list[ProjectRecord]


class CreateProjectRequest(DomainModel):
    name: str = Field(min_length=1, max_length=120)
    source_mode: SourceMode
    input_text: str = Field(min_length=1)
    run_mode: RunMode = RunMode.AUTO_DRAFT


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


class LocalReworkRequest(DomainModel):
    behavior: MockBehavior = MockBehavior.SUCCESS
    duration_delta_seconds: float = Field(default=0.0, ge=-2.0, le=4.0)


class ExportRequest(DomainModel):
    rebuild_manifest: bool = True


class AutoDraftRequest(DomainModel):
    render_export: bool = True


class AutoDraftResponse(DomainModel):
    project: ProjectAggregate
    export: ExportRecord | None


class StatusMessage(DomainModel):
    status: str
    message: str
