"""Provider-neutral asset workbench and image generation contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from short_drama.models import (
    Asset,
    AssetKind,
    AssetMediaType,
    AssetVersion,
    DomainModel,
    ProjectAggregate,
)


class ImageOperation(StrEnum):
    GENERATE = "GENERATE"
    EDIT = "EDIT"


class ImageTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OUTPUT_MISSING = "OUTPUT_MISSING"


class ImageQuality(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImageBackground(StrEnum):
    AUTO = "auto"
    OPAQUE = "opaque"
    TRANSPARENT = "transparent"


class ImageOutputFormat(StrEnum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class AssetReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AssetCreateRequest(DomainModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=120)
    descriptor: str = Field(min_length=1, max_length=4000)


class AssetImportLocalRequest(DomainModel):
    local_path: str = Field(min_length=1)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None


class AssetImportUrlRequest(DomainModel):
    external_url: str = Field(min_length=1, max_length=8192)
    descriptor: str = Field(min_length=1, max_length=4000)
    media_type: AssetMediaType = AssetMediaType.IMAGE
    parent_version_id: str | None = None


class AssetReviewRequest(DomainModel):
    decision: AssetReviewDecision
    notes: str = Field(min_length=1, max_length=4000)


class ImageProviderRequest(DomainModel):
    operation: ImageOperation
    prompt: str = Field(min_length=1, max_length=32000)
    size: str = "768x1344"
    quality: ImageQuality = ImageQuality.HIGH
    background: ImageBackground = ImageBackground.OPAQUE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    primary_input_version_id: str | None = None

    @model_validator(mode="after")
    def validate_primary_input(self) -> ImageProviderRequest:
        if self.primary_input_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a primary input")
        if self.primary_input_version_id not in self.input_version_ids:
            raise ValueError("Primary image input must also appear in input_version_ids")
        return self

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        try:
            raw_width, raw_height = value.lower().split("x", maxsplit=1)
            width = int(raw_width)
            height = int(raw_height)
        except (TypeError, ValueError) as exc:
            raise ValueError("Image size must use WIDTHxHEIGHT") from exc
        if width <= 0 or height <= 0 or width % 16 or height % 16:
            raise ValueError("Image width and height must be positive multiples of 16")
        ratio = width / height
        if ratio < (1 / 3) or ratio > 3:
            raise ValueError("Image aspect ratio must be between 1:3 and 3:1")
        return f"{width}x{height}"


class ImageTaskCreateRequest(DomainModel):
    operation: ImageOperation = ImageOperation.GENERATE
    prompt: str = Field(min_length=1, max_length=32000)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    primary_input_version_id: str | None = None
    size: str = "768x1344"
    quality: ImageQuality = ImageQuality.HIGH
    background: ImageBackground = ImageBackground.OPAQUE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG

    @model_validator(mode="after")
    def validate_primary_input(self) -> ImageTaskCreateRequest:
        if self.primary_input_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a primary input")
        if self.primary_input_version_id not in self.input_version_ids:
            raise ValueError("Primary image input must also appear in input_version_ids")
        return self


class ImageTokenDetails(DomainModel):
    image_tokens: int = 0
    text_tokens: int = 0


class ImageUsage(DomainModel):
    input_tokens: int = 0
    input_tokens_details: ImageTokenDetails = Field(default_factory=ImageTokenDetails)
    output_tokens: int = 0
    output_tokens_details: ImageTokenDetails = Field(default_factory=ImageTokenDetails)
    total_tokens: int = 0


class ImagePricingSnapshot(DomainModel):
    currency: str = "USD"
    unit: str = "PER_1M_TOKENS"
    image_input: float = 8.0
    text_input: float = 5.0
    image_output: float = 30.0
    captured_at: str


class ImageCostRecord(DomainModel):
    estimated_amount: float
    currency: str = "USD"
    pricing: ImagePricingSnapshot
    usage: ImageUsage
    note: str = "Estimate from provider token usage; provider billing is authoritative."


class ImageGenerationTask(DomainModel):
    id: str
    project_id: str
    asset_id: str
    output_version_id: str
    output_version_number: int
    descriptor: str
    parent_version_id: str | None = None
    operation: ImageOperation
    status: ImageTaskStatus
    provider: str = "openai"
    model: str = "gpt-image-2"
    request: ImageProviderRequest
    attempt: int = 1
    retry_of_task_id: str | None = None
    provider_request_id: str | None = None
    staged_path: str | None = None
    output_path: str | None = None
    output_mime_type: str | None = None
    revised_prompt: str | None = None
    usage: ImageUsage | None = None
    cost: ImageCostRecord | None = None
    error_code: str | None = None
    error_message: str | None = None
    submitted_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str


class ImageProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    clear_api_key: bool = False


class ImageProviderConfigResponse(DomainModel):
    provider: str = "openai-compatible"
    model: str = "gpt-image-2"
    base_url: str = "https://api.openai.com/v1"
    has_api_key: bool
    key_source: str
    can_generate: bool
    pricing: ImagePricingSnapshot


class AssetMutationResponse(DomainModel):
    asset: Asset
    version: AssetVersion
    project: ProjectAggregate


class ImageTaskResponse(DomainModel):
    task: ImageGenerationTask
    project: ProjectAggregate


class ImageTaskListResponse(DomainModel):
    tasks: list[ImageGenerationTask]
