"""Typed MiniMax-H3 adapter records kept outside the provider-neutral project document."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field, model_validator

from short_drama.models import (
    DomainModel,
    EvidencePurpose,
    GenerationTask,
    ProjectAggregate,
    QaIssue,
    RouteEvidence,
    Take,
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
    task_id: str


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


class H3ProviderRun(DomainModel):
    id: str
    task_id: str
    project_id: str
    shot_id: str
    route: VideoGenerationRoute
    purpose: EvidencePurpose
    prompt: str
    media_inputs: list[H3MediaInput] = Field(default_factory=list[H3MediaInput])
    request: H3VideoRequest | None = None
    uploaded_inputs: list[H3UploadedInput] = Field(default_factory=list[H3UploadedInput])
    latest_remote_task: H3RemoteTask | None = None
    pricing: H3PricingSnapshot
    estimated_cost_usd: float | None = None
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


class H3TakeReviewRequest(DomainModel):
    hard_gate_passed: bool
    score: float = Field(ge=0.0, le=5.0)
    notes: str = Field(min_length=1)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])


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
