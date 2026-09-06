"""Provider-neutral asset workbench and image generation contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from short_drama.models import (
    Asset,
    AssetKind,
    AssetLayoutProfile,
    AssetMediaType,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    AssetVersion,
    CoreAssetBrief,
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


class AssetReviewPurpose(StrEnum):
    """How an approved image version participates in the asset contract."""

    CANONICAL_MASTER = "CANONICAL_MASTER"
    SUPPLEMENTAL_REFERENCE = "SUPPLEMENTAL_REFERENCE"


class AssetCreateRequest(DomainModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=120)
    descriptor: str = Field(min_length=1, max_length=4000)
    brief: CoreAssetBrief | None = None


class AssetBriefUpdateRequest(DomainModel):
    brief: CoreAssetBrief


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
    purpose: AssetReviewPurpose
    notes: str = Field(min_length=1, max_length=4000)


class AssetReferenceSheetCellRequest(DomainModel):
    asset_version_id: str = Field(min_length=1, max_length=200)
    view: AssetStandardView
    label: str | None = Field(default=None, min_length=1, max_length=80)


class AssetReferenceSheetComposeRequest(DomainModel):
    strategy: AssetReferenceStrategy
    cells: list[AssetReferenceSheetCellRequest] = Field(min_length=1, max_length=9)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None
    input_policy: AssetSheetInputPolicy = AssetSheetInputPolicy.EXPLICIT_VERSIONS
    columns: int = Field(default=2, ge=1, le=3)
    layout_profile: AssetLayoutProfile | None = None
    show_labels: bool = True
    background_color: str = Field(default="#FFFFFF", min_length=1, max_length=64)
    target_size: str | None = None

    @model_validator(mode="after")
    def validate_layout(self) -> AssetReferenceSheetComposeRequest:
        layout_profile = self.layout_profile
        if (
            layout_profile is None
            or layout_profile == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
        ) and self.columns > len(self.cells):
            raise ValueError("Reference sheet columns cannot exceed its cell count")
        expected_cells: int | None = None
        if layout_profile is not None:
            expected_cells = {
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: 4,
                AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: 6,
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2: 4,
                AssetLayoutProfile.PROP_MASTER_SINGLE: 1,
            }.get(layout_profile)
        if expected_cells is not None and len(self.cells) != expected_cells:
            assert layout_profile is not None
            raise ValueError(
                f"{layout_profile.value} requires exactly {expected_cells} cells"
            )
        if layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2,
        } and self.strategy != AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS:
            raise ValueError("Character sheet layouts require CHARACTER_STANDARD_VIEWS")
        if (
            layout_profile == AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2
            and self.strategy != AssetReferenceStrategy.SCENE_COVERAGE
        ):
            raise ValueError("Scene four-direction layout requires SCENE_COVERAGE")
        if (
            layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE
            and self.strategy != AssetReferenceStrategy.LIGHTWEIGHT
        ):
            raise ValueError("Prop master layout requires LIGHTWEIGHT strategy")
        if self.target_size is not None:
            width, height = _parse_image_size(self.target_size)
            self.target_size = f"{width}x{height}"
            if layout_profile in {
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
            } and width * 9 != height * 16:
                assert layout_profile is not None
                raise ValueError(f"{layout_profile.value} target_size must be 16:9")
        return self


class ImageReferenceInput(DomainModel):
    """One ordered image input and the semantic job it performs."""

    asset_version_id: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    subject_alias: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_text(self) -> ImageReferenceInput:
        self.asset_version_id = self.asset_version_id.strip()
        self.purpose = self.purpose.strip()
        if not self.asset_version_id or not self.purpose:
            raise ValueError("Image reference id and purpose must be non-empty")
        if self.subject_alias is not None:
            self.subject_alias = self.subject_alias.strip()
            if not self.subject_alias:
                raise ValueError("Image reference subject_alias must be non-empty")
        return self


def _parse_image_size(value: str) -> tuple[int, int]:
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
    return width, height


def _validate_reference_inputs(
    *,
    input_version_ids: list[str],
    reference_inputs: list[ImageReferenceInput],
    primary_input_version_id: str | None,
) -> list[str]:
    if reference_inputs and input_version_ids:
        raise ValueError(
            "Use ordered reference_inputs or legacy input_version_ids, not both"
        )
    normalized_ids = [item.strip() for item in input_version_ids]
    if any(not item for item in normalized_ids) or len(normalized_ids) != len(
        set(normalized_ids)
    ):
        raise ValueError("Image input_version_ids must be non-empty and unique")
    input_version_ids[:] = normalized_ids
    reference_ids = [item.asset_version_id for item in reference_inputs]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("Image reference_inputs must use unique AssetVersions")
    aliases = [
        item.subject_alias for item in reference_inputs if item.subject_alias is not None
    ]
    if len(aliases) != len(set(aliases)):
        raise ValueError("Image reference subject_alias values must be unique")
    effective_ids = reference_ids or normalized_ids
    if (
        reference_inputs
        and primary_input_version_id is not None
        and effective_ids
        and effective_ids[0] != primary_input_version_id
    ):
        raise ValueError(
            "Ordered reference_inputs must place the primary input first"
        )
    return effective_ids


class ImageProviderRequest(DomainModel):
    operation: ImageOperation
    prompt: str = Field(min_length=1, max_length=32000)
    size: str = "768x1344"
    quality: ImageQuality = ImageQuality.HIGH
    background: ImageBackground = ImageBackground.OPAQUE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    reference_inputs: list[ImageReferenceInput] = Field(
        default_factory=list[ImageReferenceInput],
        max_length=16,
    )
    primary_input_version_id: str | None = None
    mask_version_id: str | None = None
    mask_feather_pixels: int = Field(default=8, ge=0, le=64)

    @model_validator(mode="after")
    def validate_primary_input(self) -> ImageProviderRequest:
        effective_ids = _validate_reference_inputs(
            input_version_ids=self.input_version_ids,
            reference_inputs=self.reference_inputs,
            primary_input_version_id=self.primary_input_version_id,
        )
        if self.primary_input_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a primary input")
        if self.primary_input_version_id not in effective_ids:
            raise ValueError("Primary image input must also appear in image references")
        if self.mask_version_id == self.primary_input_version_id:
            raise ValueError("Edit mask must be separate from the primary image input")
        return self

    @model_validator(mode="after")
    def validate_mask_input(self) -> ImageProviderRequest:
        if self.mask_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a mask input")
        if self.mask_version_id in self.ordered_input_version_ids():
            raise ValueError("Edit mask must not also be listed as a visual input")
        return self

    def ordered_input_version_ids(self) -> list[str]:
        if self.reference_inputs:
            return [item.asset_version_id for item in self.reference_inputs]
        return list(self.input_version_ids)

    def ordered_reference_inputs(self) -> list[ImageReferenceInput]:
        if self.reference_inputs:
            return [item.model_copy(deep=True) for item in self.reference_inputs]
        return [
            ImageReferenceInput(
                asset_version_id=version_id,
                purpose="Legacy ordered visual reference",
            )
            for version_id in self.input_version_ids
        ]

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        width, height = _parse_image_size(value)
        return f"{width}x{height}"


class ImageTaskCreateRequest(DomainModel):
    operation: ImageOperation = ImageOperation.GENERATE
    prompt: str = Field(min_length=1, max_length=32000)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    reference_inputs: list[ImageReferenceInput] = Field(
        default_factory=list[ImageReferenceInput],
        max_length=16,
    )
    primary_input_version_id: str | None = None
    mask_version_id: str | None = None
    mask_feather_pixels: int = Field(default=8, ge=0, le=64)
    size: str = "768x1344"
    quality: ImageQuality = ImageQuality.HIGH
    background: ImageBackground = ImageBackground.OPAQUE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG

    @model_validator(mode="after")
    def validate_primary_input(self) -> ImageTaskCreateRequest:
        effective_ids = _validate_reference_inputs(
            input_version_ids=self.input_version_ids,
            reference_inputs=self.reference_inputs,
            primary_input_version_id=self.primary_input_version_id,
        )
        if self.primary_input_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a primary input")
        if self.primary_input_version_id not in effective_ids:
            raise ValueError("Primary image input must also appear in image references")
        if self.mask_version_id == self.primary_input_version_id:
            raise ValueError("Edit mask must be separate from the primary image input")
        return self

    @model_validator(mode="after")
    def validate_mask_input(self) -> ImageTaskCreateRequest:
        if self.mask_version_id is None:
            return self
        if self.operation != ImageOperation.EDIT:
            raise ValueError("Only image edits can declare a mask input")
        if self.mask_version_id in self.ordered_input_version_ids():
            raise ValueError("Edit mask must not also be listed as a visual input")
        return self

    def ordered_input_version_ids(self) -> list[str]:
        if self.reference_inputs:
            return [item.asset_version_id for item in self.reference_inputs]
        return list(self.input_version_ids)


class ImageNotSubmittedResolutionRequest(DomainModel):
    reason: str = Field(min_length=1, max_length=2000)


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
