"""Typed MiniMax-H3 adapter records kept outside the provider-neutral project document."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from short_drama.models import (
    DomainModel,
    EvidencePurpose,
    ExportRecord,
    GenerationTask,
    ProjectAggregate,
    QaStatus,
    QaIssue,
    QaFailureCategory,
    RouteEvidence,
    Take,
    TimelineManifest,
    VideoInspectionReport,
    VideoInspectionScope,
    VideoGenerationRoute,
)


class H3MediaKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class H3MediaRole(StrEnum):
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    REFERENCE_IMAGE = "reference_image"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_AUDIO = "reference_audio"


class H3VideoGuideKind(StrEnum):
    PREVIOUS_TAKE = "PREVIOUS_TAKE"
    TAIL_WINDOW = "TAIL_WINDOW"
    CAMERA_PREVIS = "CAMERA_PREVIS"


class H3CameraMotionPreset(StrEnum):
    STATIC = "STATIC"
    DOLLY_IN = "DOLLY_IN"
    DOLLY_OUT = "DOLLY_OUT"
    TRACK_LEFT = "TRACK_LEFT"
    TRACK_RIGHT = "TRACK_RIGHT"
    JIB_UP = "JIB_UP"
    JIB_DOWN = "JIB_DOWN"
    FOLLOW = "FOLLOW"


class H3VideoGuideBaseSource(StrEnum):
    TARGET_START_FRAME = "TARGET_START_FRAME"
    PREVIOUS_BOUNDARY_FRAME = "PREVIOUS_BOUNDARY_FRAME"


class H3RemoteStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class H3MediaSource(DomainModel):
    external_url: str | None = None
    local_path: str | None = None
    file_id: str | None = None

    @model_validator(mode="after")
    def validate_single_source(self) -> H3MediaSource:
        values = [self.external_url, self.local_path, self.file_id]
        if sum(bool(value and value.strip()) for value in values) != 1:
            raise ValueError("Exactly one of external_url, local_path, or file_id is required")
        return self


class H3MediaInput(DomainModel):
    kind: H3MediaKind
    role: H3MediaRole
    source: H3MediaSource
    asset_version_id: str | None = None
    video_guide_id: str | None = None


class H3ProbeRequest(DomainModel):
    shot_id: str
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    duration_seconds: int | None = Field(default=None, ge=4, le=15)
    prompt_override: str | None = Field(default=None, max_length=7000)
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
    )
    purpose: EvidencePurpose = EvidencePurpose.CONTRACT_SMOKE
    learning_objective: str | None = Field(default=None, max_length=2000)
    regression_case_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_quality_learning_objective(self) -> H3ProbeRequest:
        if self.purpose == EvidencePurpose.QUALITY_PROBE:
            if self.learning_objective is None or not self.learning_objective.strip():
                raise ValueError("A QUALITY_PROBE requires a learning_objective")
        return self


class H3RoutePlanStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    EXECUTING = "EXECUTING"
    COMPLETE = "COMPLETE"
    STALE = "STALE"


class H3ShotRouteAction(StrEnum):
    REUSE_TAKE = "REUSE_TAKE"
    GENERATE = "GENERATE"


class H3ShotRouteReadiness(StrEnum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NEEDS_ASSET = "NEEDS_ASSET"
    UNSUPPORTED = "UNSUPPORTED"


class H3ShotRouteExecutionStatus(StrEnum):
    PLANNED = "PLANNED"
    REUSED = "REUSED"
    QUEUED = "QUEUED"
    GENERATED = "GENERATED"
    SKIPPED = "SKIPPED"


class H3RouteAssetPreparationStatus(StrEnum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    READY = "READY"
    REJECTED = "REJECTED"


class H3InspectionPolicy(DomainModel):
    scopes: list[VideoInspectionScope] = Field(min_length=1, max_length=2)
    max_frames: int = Field(ge=6, le=24)
    semantic_review: bool = True
    review_focus: str | None = Field(default=None, max_length=2000)


class H3RouteAssetPreparation(DomainModel):
    id: str
    shot_id: str
    role: H3MediaRole
    name: str = Field(min_length=1, max_length=120)
    descriptor: str = Field(min_length=1, max_length=4000)
    prompt: str = Field(min_length=1, max_length=32000)
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    rationale: str = Field(min_length=1, max_length=2000)
    advisory_only: Literal[True] = True
    status: H3RouteAssetPreparationStatus = H3RouteAssetPreparationStatus.PLANNED
    asset_id: str | None = None
    image_task_ids: list[str] = Field(default_factory=list[str], max_length=10)
    approved_version_id: str | None = None
    review_summary: str | None = Field(default=None, max_length=4000)


class H3VideoGuide(DomainModel):
    id: str
    shot_id: str
    source_shot_id: str
    source_shot_revision: int = Field(ge=1)
    source_take_id: str
    kind: H3VideoGuideKind
    media_path: str
    duration_seconds: float = Field(gt=0.0, le=15.0)
    source_in_seconds: float = Field(default=0.0, ge=0.0)
    camera_motion: H3CameraMotionPreset | None = None
    previs_base_source: H3VideoGuideBaseSource | None = None
    base_asset_version_id: str | None = None
    source_qa_status: QaStatus
    source_quality_score: float = Field(ge=0.0, le=5.0)
    source_issue_codes: list[str] = Field(default_factory=list[str], max_length=24)
    rationale: str = Field(min_length=1, max_length=2000)
    warnings: list[str] = Field(default_factory=list[str], max_length=16)
    advisory_only: Literal[True] = True
    created_at: str


class H3ShotRoutePlan(DomainModel):
    shot_id: str
    shot_sequence: int = Field(ge=1)
    input_script_version_id: str
    input_bible_version_id: str
    input_shot_revision: int = Field(ge=1)
    action: H3ShotRouteAction
    route: VideoGenerationRoute
    duration_seconds: int = Field(ge=4, le=15)
    prompt: str = Field(min_length=1, max_length=7000)
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
    )
    reuse_take_id: str | None = None
    route_evidence_ids: list[str] = Field(default_factory=list[str], max_length=100)
    rationale: str = Field(min_length=1, max_length=3000)
    risk_ids: list[str] = Field(default_factory=list[str], max_length=20)
    qa_focus_categories: list[QaFailureCategory] = Field(
        default_factory=list[QaFailureCategory],
        max_length=12,
    )
    warnings: list[str] = Field(default_factory=list[str], max_length=32)
    missing_inputs: list[str] = Field(default_factory=list[str], max_length=32)
    asset_preparations: list[H3RouteAssetPreparation] = Field(
        default_factory=list[H3RouteAssetPreparation],
        max_length=4,
    )
    video_guides: list[H3VideoGuide] = Field(
        default_factory=list[H3VideoGuide],
        max_length=3,
    )
    selected_video_guide_id: str | None = None
    candidate_count: int = Field(default=1, ge=1, le=5)
    inspection: H3InspectionPolicy
    readiness: H3ShotRouteReadiness
    execution_status: H3ShotRouteExecutionStatus = H3ShotRouteExecutionStatus.PLANNED
    task_ids: list[str] = Field(default_factory=list[str], max_length=10)
    inspection_ids: list[str] = Field(default_factory=list[str], max_length=10)
    inspection_issue_codes: list[str] = Field(default_factory=list[str], max_length=50)
    override_allowed: Literal[True] = True

    @model_validator(mode="after")
    def validate_action_target(self) -> H3ShotRoutePlan:
        if self.action == H3ShotRouteAction.REUSE_TAKE and self.reuse_take_id is None:
            raise ValueError("REUSE_TAKE requires reuse_take_id")
        if self.action == H3ShotRouteAction.GENERATE and self.reuse_take_id is not None:
            raise ValueError("GENERATE cannot declare reuse_take_id")
        if self.selected_video_guide_id is not None:
            known = {item.id for item in self.video_guides}
            if self.selected_video_guide_id not in known:
                raise ValueError("selected_video_guide_id must reference a declared video guide")
            if not any(
                item.video_guide_id == self.selected_video_guide_id
                for item in self.media_inputs
            ):
                raise ValueError("Selected video guide must be present in media_inputs")
        return self


class H3ProductionRoutePlan(DomainModel):
    id: str
    project_id: str
    version: int = Field(ge=1)
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    resolution: str = "768P"
    status: H3RoutePlanStatus
    planner: str
    input_script_version_id: str
    input_bible_version_id: str
    input_voice_reference_revision: int = Field(default=0, ge=0)
    decisions: list[H3ShotRoutePlan] = Field(min_length=1)
    estimated_new_h3_seconds: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)
    estimated_cost_credits: float = Field(default=0.0, ge=0.0)
    warnings: list[str] = Field(default_factory=list[str], max_length=100)
    created_at: str
    updated_at: str
    executed_at: str | None = None


class H3ProductionRoutePlanRequest(DomainModel):
    shot_ids: list[str] = Field(default_factory=list[str], max_length=500)
    use_language_agent: bool = True
    candidate_count: int = Field(default=1, ge=1, le=5)
    consider_video_guides: bool = True


class H3ProductionRoutePlanResponse(DomainModel):
    plan: H3ProductionRoutePlan


class H3ProductionRoutePlanListResponse(DomainModel):
    plans: list[H3ProductionRoutePlan]


class H3ProductionRouteExecuteRequest(DomainModel):
    reuse_passed_takes: bool = True
    enqueue_new_generations: bool = False
    adaptive_sequential: bool = False
    inspect_reused_takes: bool = True
    semantic_review: bool = True
    rebuild_manifest: bool = True
    render_export: bool = False


class H3ProductionRouteExecuteResponse(DomainModel):
    plan: H3ProductionRoutePlan
    project: ProjectAggregate
    queued_tasks: list[GenerationTask] = Field(default_factory=list[GenerationTask])
    inspections: list[VideoInspectionReport] = Field(
        default_factory=list[VideoInspectionReport]
    )
    manifest: TimelineManifest | None = None
    export: ExportRecord | None = None


class H3UrlValue(DomainModel):
    url: str


class H3ContentItem(DomainModel):
    type: str
    text: str | None = None
    image_url: H3UrlValue | None = None
    video_url: H3UrlValue | None = None
    audio_url: H3UrlValue | None = None
    role: H3MediaRole | None = None


class H3VideoRequest(DomainModel):
    model: str = "MiniMax-H3"
    content: list[H3ContentItem]
    resolution: str = "768P"
    duration: int = Field(ge=4, le=15)
    ratio: str | None = None


class H3CreateResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    trace_id: str | None = None


class H3TaskError(DomainModel):
    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    message: str | None = None


class H3TaskContent(DomainModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    prompt: str | None = None


class H3TaskUsage(DomainModel):
    model_config = ConfigDict(extra="ignore")

    total_seconds: int | None = None
    input_seconds: int | None = None
    output_seconds: int | None = None
    input_image_count: int | None = None
    input_audio_seconds: int | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class H3RemoteTask(DomainModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    model: str = "MiniMax-H3"
    status: H3RemoteStatus
    error: H3TaskError | None = None
    created_at: int | None = None
    updated_at: int | None = None
    content: H3TaskContent | None = None
    resolution: str | None = None
    duration: int | None = None
    usage: H3TaskUsage | None = None
    ratio: str | None = None
    task_type: str | None = None
    modality: str | None = None


class H3QueryResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    task: H3RemoteTask


class H3ListResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    items: list[H3RemoteTask] = Field(default_factory=list[H3RemoteTask])
    total: int = 0


class H3DeleteResponse(DomainModel):
    task_id: str
    action: str
    status: str


class H3UploadedInput(DomainModel):
    local_path: str
    provider_file_id: str
    media_kind: H3MediaKind
    role: H3MediaRole
    expires_at: str


class H3PricingSnapshot(DomainModel):
    currency: str = "USD"
    output_per_second: float = 0.08
    reference_video_per_second: float = 0.08
    included_images: int = 5
    additional_image_each: float = 0.04
    reference_audio_each: float = 0.0
    captured_at: str
    source_url: str = "https://platform.minimax.io/docs/guides/pricing-paygo#video"
    billing_unit: str = "ACE_CREDIT"
    billing_resolution: str = "768P"
    billing_output_per_second: float = 0.60070822
    billing_source_url: str = (
        "https://platform.acedata.cloud/api/v1/services/"
        "2add8a2a-3577-4a0f-8fd4-e08aaabe8290"
    )


class H3ProviderRun(DomainModel):
    id: str
    task_id: str
    project_id: str
    shot_id: str
    route: VideoGenerationRoute
    purpose: EvidencePurpose
    learning_objective: str | None = None
    regression_case_id: str | None = None
    prompt: str
    media_inputs: list[H3MediaInput] = Field(default_factory=list[H3MediaInput])
    request: H3VideoRequest | None = None
    uploaded_inputs: list[H3UploadedInput] = Field(default_factory=list[H3UploadedInput])
    latest_remote_task: H3RemoteTask | None = None
    pricing: H3PricingSnapshot
    estimated_cost_usd: float | None = None
    estimated_cost_credits: float | None = None
    output_url: str | None = None
    downloaded_path: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str
    updated_at: str


class H3ProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, min_length=1)
    clear_api_key: bool = False


class H3ProviderConfigResponse(DomainModel):
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    base_url: str = "https://api.acedata.cloud"
    resolution: str = "768P"
    has_api_key: bool
    key_source: str
    can_submit: bool
    pricing: H3PricingSnapshot


class H3ReconcileRequest(DomainModel):
    provider_task_id: str = Field(min_length=1)


class H3NotSubmittedResolutionRequest(DomainModel):
    reason: str = Field(min_length=1, max_length=2000)


class H3TakeReviewRequest(DomainModel):
    hard_gate_passed: bool
    score: float = Field(ge=0.0, le=5.0)
    notes: str = Field(min_length=1)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])

    @model_validator(mode="after")
    def require_rejection_classification(self) -> H3TakeReviewRequest:
        if not self.hard_gate_passed and not self.issues:
            raise ValueError("A rejected H3 Take requires at least one classified QA issue")
        return self


class H3TaskActionResponse(DomainModel):
    task: GenerationTask
    project: ProjectAggregate
    provider_run: H3ProviderRun


class H3ProbeResponse(H3TaskActionResponse):
    pass


class H3CompilePreviewResponse(DomainModel):
    prompt: str
    request: H3VideoRequest


class H3ProviderRunsResponse(DomainModel):
    runs: list[H3ProviderRun]


class H3ReconciliationCandidatesResponse(DomainModel):
    tasks: list[H3RemoteTask]


class H3TakeReviewResponse(DomainModel):
    take: Take
    evidence: RouteEvidence
    project: ProjectAggregate
