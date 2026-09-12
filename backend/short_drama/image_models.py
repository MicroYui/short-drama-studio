"""Provider-neutral asset workbench and image generation contracts."""

from __future__ import annotations

from enum import StrEnum
import math
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationInfo, field_validator, model_validator

from short_drama.models import (
    Asset,
    AssetKind,
    AssetLayoutProfile,
    AssetMediaType,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    AssetTextPlateContentState,
    AssetVersion,
    CoreAssetBrief,
    DomainModel,
    ImageReferenceAuthority,
    ImageReferenceRole,
    ProjectAggregate,
    TextPlateLine,
    TextPlateTextBlock,
)


IMAGE_DIMENSION_MULTIPLE = 16
IMAGE_MINIMUM_DIMENSION_PIXELS = 16
IMAGE_MINIMUM_TOTAL_PIXELS = 655_360
IMAGE_MAXIMUM_EDGE_PIXELS = 3_840
IMAGE_MAXIMUM_TOTAL_PIXELS = 8_294_400
LOCAL_REFERENCE_SHEET_MINIMUM_DIMENSION_PIXELS = 256


class ImageOperation(StrEnum):
    GENERATE = "GENERATE"
    REFERENCE_GENERATE = "REFERENCE_GENERATE"
    EDIT = "EDIT"


class ImageTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OUTPUT_MISSING = "OUTPUT_MISSING"


class ImageRetryDisposition(StrEnum):
    """The structurally valid next action after an image task did not succeed."""

    RETRY_SAME_REQUEST = "RETRY_SAME_REQUEST"
    REVISE_AND_ENQUEUE = "REVISE_AND_ENQUEUE"
    RECONCILE_SUBMISSION = "RECONCILE_SUBMISSION"
    RECOVER_OUTPUT = "RECOVER_OUTPUT"
    NEW_ENQUEUE = "NEW_ENQUEUE"


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


class AssetBatchCreateRequest(DomainModel):
    assets: list[AssetCreateRequest] = Field(min_length=1, max_length=50,
        description="Already determined new characters, locations, props or references to save together. Creates text definitions only; does not generate media or replace existing objects.")


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


class ImageRegion(DomainModel):
    """Normalized rectangle in the visible image after EXIF orientation."""

    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> ImageRegion:
        if self.x + self.width > 1.000000001 or self.y + self.height > 1.000000001:
            raise ValueError("Image region must be contained within the visible image")
        return self


class AssetImageCropRequest(DomainModel):
    source_asset_version_id: str = Field(min_length=1)
    region: ImageRegion
    descriptor: str = Field(min_length=1, max_length=4000)
    reference_role: ImageReferenceRole = Field(
        description="What the selected pixels provide: identity, wardrobe, space, prop, etc.",
    )


class AssetReviewRequest(DomainModel):
    decision: AssetReviewDecision
    purpose: AssetReviewPurpose
    notes: str = Field(min_length=1, max_length=4000)


class AssetReferenceSheetCellRequest(DomainModel):
    asset_version_id: str = Field(min_length=1, max_length=200)
    view: AssetStandardView
    label: str | None = Field(default=None, min_length=1, max_length=80)


_REFERENCE_SHEET_LAYOUT_VIEW_SLOTS: dict[
    AssetLayoutProfile,
    tuple[tuple[str, frozenset[AssetStandardView]], ...],
] = {
    AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: (
        ("canonical face", frozenset({AssetStandardView.CANONICAL_MASTER})),
        ("front body", frozenset({AssetStandardView.CHARACTER_FRONT})),
        (
            "profile body",
            frozenset(
                {
                    AssetStandardView.CHARACTER_LEFT_PROFILE,
                    AssetStandardView.CHARACTER_RIGHT_PROFILE,
                }
            ),
        ),
        ("back body", frozenset({AssetStandardView.CHARACTER_BACK})),
    ),
    AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1: (
        ("front body", frozenset({AssetStandardView.CHARACTER_FRONT})),
        ("left profile", frozenset({AssetStandardView.CHARACTER_LEFT_PROFILE})),
        ("back body", frozenset({AssetStandardView.CHARACTER_BACK})),
        ("right profile", frozenset({AssetStandardView.CHARACTER_RIGHT_PROFILE})),
    ),
    AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: (
        ("canonical face", frozenset({AssetStandardView.CANONICAL_MASTER})),
        ("front body", frozenset({AssetStandardView.CHARACTER_FRONT})),
        ("left profile", frozenset({AssetStandardView.CHARACTER_LEFT_PROFILE})),
        ("right profile", frozenset({AssetStandardView.CHARACTER_RIGHT_PROFILE})),
        ("back body", frozenset({AssetStandardView.CHARACTER_BACK})),
        (
            "wardrobe detail",
            frozenset({AssetStandardView.CHARACTER_WARDROBE_DETAIL}),
        ),
    ),
    AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2: (
        (
            "scene front",
            frozenset(
                {
                    AssetStandardView.SCENE_FRONT,
                    AssetStandardView.SCENE_MASTER,
                }
            ),
        ),
        ("scene reverse", frozenset({AssetStandardView.SCENE_REVERSE})),
        ("scene left", frozenset({AssetStandardView.SCENE_LEFT})),
        ("scene right", frozenset({AssetStandardView.SCENE_RIGHT})),
    ),
    AssetLayoutProfile.PROP_MASTER_SINGLE: (
        ("prop product", frozenset({AssetStandardView.PROP_PRODUCT})),
    ),
}


class AssetReferenceSheetComposeRequest(DomainModel):
    strategy: AssetReferenceStrategy
    cells: list[AssetReferenceSheetCellRequest] = Field(min_length=1, max_length=9)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None
    input_policy: AssetSheetInputPolicy = AssetSheetInputPolicy.EXPLICIT_VERSIONS
    columns: int = Field(default=2, ge=1)
    layout_profile: AssetLayoutProfile | None = Field(
        default=None,
        description=(
            "普通多物件拼板留空，布局由 columns 决定；例如四件道具使用 "
            "strategy=LIGHTWEIGHT、columns=2、layout_profile=null。"
            "命名布局用于角色或场景的固定视图槽；LIGHTWEIGHT_SINGLE 仅含一个格子。"
        ),
    )
    show_labels: bool = True
    background_color: str = Field(default="#FFFFFF", min_length=1, max_length=64)
    target_size: str | None = None

    @model_validator(mode="after")
    def validate_layout(self) -> AssetReferenceSheetComposeRequest:
        layout_profile = self.layout_profile
        views = [cell.view for cell in self.cells]
        if (
            self.strategy != AssetReferenceStrategy.LIGHTWEIGHT
            and len(views) != len(set(views))
        ):
            raise ValueError("Reference sheet views must be unique")
        if layout_profile is None and self.columns > len(self.cells):
            raise ValueError("Reference sheet columns cannot exceed its cell count")
        expected_cells: int | None = None
        if layout_profile is not None:
            expected_cells = {
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: 4,
                AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: 6,
                AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1: 4,
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2: 4,
                AssetLayoutProfile.PROP_MASTER_SINGLE: 1,
                AssetLayoutProfile.LIGHTWEIGHT_SINGLE: 1,
            }.get(layout_profile)
        if expected_cells is not None and len(self.cells) != expected_cells:
            assert layout_profile is not None
            raise ValueError(
                f"{layout_profile.value} requires exactly {expected_cells} cells"
            )
        semantic_slots = (
            _REFERENCE_SHEET_LAYOUT_VIEW_SLOTS.get(layout_profile)
            if layout_profile is not None
            else None
        )
        if semantic_slots is not None:
            assert layout_profile is not None
            cells_by_slot: dict[int, AssetReferenceSheetCellRequest] = {}
            for cell in self.cells:
                matching_slots = [
                    index
                    for index, (_slot_name, accepted_views) in enumerate(semantic_slots)
                    if cell.view in accepted_views
                ]
                if not matching_slots:
                    raise ValueError(
                        f"{layout_profile.value} does not accept view {cell.view.value}"
                    )
                slot_index = matching_slots[0]
                if slot_index in cells_by_slot:
                    slot_name = semantic_slots[slot_index][0]
                    raise ValueError(
                        f"{layout_profile.value} contains duplicate {slot_name} views"
                    )
                cells_by_slot[slot_index] = cell
            missing_slots = [
                slot_name
                for index, (slot_name, _accepted_views) in enumerate(semantic_slots)
                if index not in cells_by_slot
            ]
            if missing_slots:
                raise ValueError(
                    f"{layout_profile.value} is missing required view slots: "
                    + ", ".join(missing_slots)
                )
            # Layout profiles own their visual slot order. Callers identify each cell
            # semantically; rendering never relies on the caller's incidental ordering.
            self.cells = [cells_by_slot[index] for index in range(len(semantic_slots))]
        if layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2,
            AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1,
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
            width, height = _parse_local_reference_sheet_size(self.target_size)
            self.target_size = f"{width}x{height}"
            if layout_profile in {
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
            } and width * 9 != height * 16:
                assert layout_profile is not None
                raise ValueError(f"{layout_profile.value} target_size must be 16:9")
            if (
                layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2
                and width * 2 != height * 3
            ):
                raise ValueError(
                    "CHARACTER_SIX_VIEW_3X2 target_size must be 3:2"
                )
            if (
                layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE
                and width != height
            ):
                raise ValueError("PROP_MASTER_SINGLE target_size must be square")
        return self


def _parse_local_reference_sheet_size(value: str) -> tuple[int, int]:
    """Validate a Pillow canvas without leaking Provider-only size limits into it."""

    try:
        raw_width, raw_height = value.lower().split("x", maxsplit=1)
        width = int(raw_width)
        height = int(raw_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("Reference sheet size must use WIDTHxHEIGHT") from exc
    if (
        width < LOCAL_REFERENCE_SHEET_MINIMUM_DIMENSION_PIXELS
        or height < LOCAL_REFERENCE_SHEET_MINIMUM_DIMENSION_PIXELS
    ):
        raise ValueError(
            "Reference sheet width and height must each be at least "
            f"{LOCAL_REFERENCE_SHEET_MINIMUM_DIMENSION_PIXELS} pixels"
        )
    if max(width, height) > 4096 or width * height > 16_777_216:
        raise ValueError("Reference sheet canvas is too large for local rendering")
    return width, height


def _parse_local_text_plate_size(value: str) -> tuple[int, int]:
    try:
        raw_width, raw_height = value.lower().split("x", maxsplit=1)
        width = int(raw_width)
        height = int(raw_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("Text plate size must use WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise ValueError("Text plate width and height must be positive")
    if max(width, height) > 4096 or width * height > 16_777_216:
        raise ValueError("Text plate canvas is too large for local rendering")
    return width, height


def _text_plate_line(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Use one line per exact_lines item; use an empty item for a blank layout row")
    return value.strip()


class AssetTextPlateComposeRequest(DomainModel):
    """Deterministic exact-copy plate rendered locally without an image provider."""

    size: str = "1536x864"
    base_version_id: str | None = Field(default=None, min_length=1, max_length=200,
        description="Explicit existing usable local TextPlate PNG to copy before drawing append_text_blocks. Inherits actual dimensions and every original layout element; parent_version_id alone remains lineage only.")
    append_text_blocks: list[TextPlateTextBlock] = Field(default_factory=list[TextPlateTextBlock], max_length=64,
        description="Only new text to draw on base_version_id. Requires an explicit complete content_state; omit all full-layout fields. Never redraw, move or erase existing content.")
    content_state: AssetTextPlateContentState | None = Field(default=None,
        description="Optional explicit narrative state and full exact visible copy for this candidate. Allows declared additions such as a signature while preserving all fixed brief text. Omit to use brief.required_visible_text unchanged; never rewrite the global brief merely to create a later state.")
    background_color: str = Field(default="#F5F1E8", min_length=1, max_length=64)
    foreground_color: str = Field(default="#171717", min_length=1, max_length=64)
    content_box: tuple[float, float, float, float] = Field(default=(0, 0, 1, 1), description="Normalized x/y/width/height of the text area. Set a full-frame transparent background with #00000000 for an overlay.")
    text_align: Literal["left", "center", "right"] = "left"
    vertical_align: Literal["top", "center", "bottom"] = "center"
    stroke_width: int = Field(default=0, ge=0, le=16)
    stroke_color: str = "#000000"
    title: str | None = Field(default=None, min_length=1, max_length=500)
    exact_lines: list[Annotated[str, AfterValidator(_text_plate_line)]] = Field(default_factory=list[str], max_length=64,
        description="Legacy ordered lines. Empty strings reserve blank layout rows, not visible wording. For precise positions use text_blocks instead.")
    text_blocks: list[TextPlateTextBlock] = Field(default_factory=list[TextPlateTextBlock], max_length=64,
        description="Ordered exact text with explicit canvas boxes, pixel font sizes and alignment. Use instead of title/exact_lines; the array order follows required_visible_text even when positions differ.")
    lines: list[TextPlateLine] = Field(default_factory=list[TextPlateLine], max_length=64,
        description="Optional ruled/signature line geometry, separate from visible wording. Works with either text mode.")
    layout_description: str = Field(min_length=1, max_length=2000)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None

    @field_validator("content_box")
    @classmethod
    def validate_content_box(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x, y, width, height = value
        if (not all(math.isfinite(item) for item in value) or x < 0 or y < 0
                or width <= 0 or height <= 0 or x + width > 1 or y + height > 1):
            raise ValueError("Text content_box must stay inside the normalized canvas")
        return value

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        width, height = _parse_local_text_plate_size(value)
        return f"{width}x{height}"

    @model_validator(mode="after")
    def validate_exact_copy(self) -> AssetTextPlateComposeRequest:
        for field_name in ("layout_description", "descriptor"):
            value = getattr(self, field_name)
            if value != value.strip():
                raise ValueError(f"Text plate {field_name} must be trimmed")
        if self.base_version_id is not None:
            if not self.append_text_blocks:
                raise ValueError("base_version_id requires non-empty append_text_blocks")
            if self.content_state is None:
                raise ValueError("Appending text requires content_state.name and complete required_visible_text, including existing text")
            if self.title is not None or self.exact_lines or self.text_blocks or self.lines:
                raise ValueError("base_version_id inherits all original content; omit title/exact_lines/text_blocks/lines. Only append_text_blocks may change pixels")
            return self
        if self.append_text_blocks:
            raise ValueError("append_text_blocks requires an explicit base_version_id")
        if self.title is not None:
            if self.title != self.title.strip() or "\n" in self.title or "\r" in self.title:
                raise ValueError("Text plate title must be one trimmed line")
        if self.text_blocks and (self.title is not None or self.exact_lines):
            raise ValueError("Use text_blocks or title/exact_lines, not both; lines may be used with either mode")
        if self.title is None and not any(self.exact_lines) and not self.text_blocks:
            raise ValueError("Text plate requires a title, visible exact_lines or text_blocks; blank rows and lines alone contain no wording")
        return self

    def required_visible_text(self) -> list[str]:
        if self.text_blocks:
            return [block.text for block in self.text_blocks]
        return ([self.title] if self.title is not None else []) + [line for line in self.exact_lines if line]


class ImageReferenceInput(DomainModel):
    """One ordered image input and the semantic job it performs."""

    asset_version_id: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    subject_alias: str | None = Field(default=None, min_length=1, max_length=120)
    reference_role: ImageReferenceRole = ImageReferenceRole.SUPPORTING_VISUAL
    reference_authority: ImageReferenceAuthority = (
        ImageReferenceAuthority.SUPPORTING
    )

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


def parse_image_size(value: str) -> tuple[int, int]:
    try:
        raw_width, raw_height = value.lower().split("x", maxsplit=1)
        width = int(raw_width)
        height = int(raw_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("Image size must use WIDTHxHEIGHT") from exc
    if (
        width < IMAGE_MINIMUM_DIMENSION_PIXELS
        or height < IMAGE_MINIMUM_DIMENSION_PIXELS
        or width % IMAGE_DIMENSION_MULTIPLE
        or height % IMAGE_DIMENSION_MULTIPLE
    ):
        raise ValueError("Image width and height must be positive multiples of 16")
    ratio = width / height
    if ratio < (1 / 3) or ratio > 3:
        raise ValueError("Image aspect ratio must be between 1:3 and 3:1")
    if max(width, height) > IMAGE_MAXIMUM_EDGE_PIXELS:
        raise ValueError("Image long edge must not exceed 3840 pixels")
    pixel_count = width * height
    if (
        pixel_count < IMAGE_MINIMUM_TOTAL_PIXELS
        or pixel_count > IMAGE_MAXIMUM_TOTAL_PIXELS
    ):
        raise ValueError(
            "Image pixel count must be between 655360 and 8294400 pixels"
        )
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
    layout_profile: AssetLayoutProfile = Field(
        default=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
        description=(
            "当前这一张输出的布局，不从所属 Asset.brief 的总体参考卡"
            "布局继承。"
        ),
    )
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    reference_inputs: list[ImageReferenceInput] = Field(
        default_factory=list[ImageReferenceInput],
        max_length=16,
    )
    primary_input_version_id: str | None = None
    mask_version_id: str | None = None
    mask_feather_pixels: int = Field(default=8, ge=0, le=64)

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> ImageProviderRequest:
        effective_ids = _validate_reference_inputs(
            input_version_ids=self.input_version_ids,
            reference_inputs=self.reference_inputs,
            primary_input_version_id=self.primary_input_version_id,
        )
        if self.operation == ImageOperation.GENERATE and effective_ids:
            raise ValueError("Image generation does not accept reference inputs")
        if self.operation in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        } and not effective_ids:
            raise ValueError(
                f"{self.operation.value} requires at least one reference input"
            )
        if self.primary_input_version_id is None:
            return self
        if self.operation not in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        }:
            raise ValueError(
                "Only reference generation or image edits can declare a primary input"
            )
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
        width, height = parse_image_size(value)
        return f"{width}x{height}"


_IMAGE_TASK_OPERATION_DESCRIPTION = (
    "GENERATE: no reference images or target parent. REFERENCE_GENERATE: ordered "
    "reference images with parent_version_id omitted; it can create another candidate "
    "in an existing Asset. EDIT: ordered reference images plus an explicit target "
    "parent_version_id. A planned REFERENCE_GENERATE does not itself select a parent; "
    "when the actual task explicitly derives from a target version, use EDIT."
)
_IMAGE_TASK_PARENT_DESCRIPTION = (
    "Explicit target AssetVersion lineage for EDIT; requires operation=EDIT. "
    "Omit for GENERATE and REFERENCE_GENERATE. A reference, dependency, primary input, "
    "or existing target Asset does not automatically become this parent."
)


class ImageTaskCreateRequest(DomainModel):
    operation: ImageOperation = Field(default=ImageOperation.GENERATE, description=_IMAGE_TASK_OPERATION_DESCRIPTION)
    prompt: str = Field(min_length=1, max_length=32000)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = Field(default=None, description=_IMAGE_TASK_PARENT_DESCRIPTION)
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
    layout_profile: AssetLayoutProfile = Field(
        default=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
        description=(
            "本次生成输出的布局。普通脸部、正面、侧面或背面素材使用 "
            "LIGHTWEIGHT_SINGLE；只有直接生成一张多视图卡时才显式指定"
            "多区布局。该字段不从 Asset.brief.layout_profile 继承。"
        ),
    )

    @field_validator("parent_version_id")
    @classmethod
    def validate_reference_generation_parent(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and info.data.get("operation") == ImageOperation.REFERENCE_GENERATE:
            raise ValueError(
                "REFERENCE_GENERATE runs without a target parent; when parent_version_id "
                "explicitly selects an existing target version, set operation='EDIT' and "
                "keep the intended parent and ordered references. If no target parent "
                "is intended, omit parent_version_id; an existing Asset is still allowed."
            )
        return value

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> ImageTaskCreateRequest:
        effective_ids = _validate_reference_inputs(
            input_version_ids=self.input_version_ids,
            reference_inputs=self.reference_inputs,
            primary_input_version_id=self.primary_input_version_id,
        )
        if self.operation == ImageOperation.GENERATE:
            if effective_ids:
                raise ValueError("Image generation does not accept reference inputs")
            if self.parent_version_id is not None:
                raise ValueError("Image generation does not accept a target parent")
        elif self.operation == ImageOperation.REFERENCE_GENERATE:
            if not effective_ids:
                raise ValueError(
                    "REFERENCE_GENERATE requires at least one reference input"
                )
        else:
            if not effective_ids:
                raise ValueError("EDIT requires at least one reference input")
            if self.parent_version_id is None:
                raise ValueError("EDIT requires a target parent version")
        if self.primary_input_version_id is None:
            return self
        if self.operation not in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        }:
            raise ValueError(
                "Only reference generation or image edits can declare a primary input"
            )
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


class AgentImageTaskCreateRequest(DomainModel):
    """Agent-facing request exposing only the canonical reference contract.

    The public API still accepts ``input_version_ids`` for older callers. Tool
    discovery must not expose both that legacy representation and structured
    ``reference_inputs`` because doing so makes an otherwise simple Agent call
    ambiguous.
    """

    operation: ImageOperation = Field(default=ImageOperation.GENERATE, description=_IMAGE_TASK_OPERATION_DESCRIPTION)
    prompt: str = Field(min_length=1, max_length=32000)
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = Field(default=None, description=_IMAGE_TASK_PARENT_DESCRIPTION)
    reference_inputs: list[ImageReferenceInput] = Field(
        default_factory=list[ImageReferenceInput],
        max_length=16,
        description=(
            "Ordered visual inputs. Each item uses asset_version_id and declares "
            "its role, authority, and purpose. Omit for GENERATE."
        ),
    )
    primary_input_version_id: str | None = Field(
        default=None,
        description=(
            "Primary AssetVersion for REFERENCE_GENERATE or EDIT; it must be the "
            "first reference_inputs item."
        ),
    )
    mask_version_id: str | None = None
    mask_feather_pixels: int = Field(default=8, ge=0, le=64)
    size: str = "768x1344"
    quality: ImageQuality = ImageQuality.HIGH
    background: ImageBackground = ImageBackground.OPAQUE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    layout_profile: AssetLayoutProfile = Field(
        default=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
        description=(
            "本次图像产物自身的布局；不要把父资产最终参考卡的布局"
            "复制给单个基础视图。"
        ),
    )

    @model_validator(mode="after")
    def validate_canonical_request(self) -> AgentImageTaskCreateRequest:
        self.to_service_request()
        return self

    def to_service_request(self) -> ImageTaskCreateRequest:
        return ImageTaskCreateRequest.model_validate(self.model_dump(mode="python"))


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
    provider_output_path: str | None = Field(default=None, description=(
        "Original Provider response file retained before local masked composition. "
        "Available only for newly saved masked EDIT results; absent on legacy tasks."
    ))
    staged_path: str | None = None
    output_path: str | None = None
    output_mime_type: str | None = None
    revised_prompt: str | None = None
    usage: ImageUsage | None = None
    cost: ImageCostRecord | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_disposition: ImageRetryDisposition | None = None
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


class AssetBatchMutationResponse(DomainModel):
    assets: list[Asset]
    created_asset_ids: list[str]


class ImageTaskResponse(DomainModel):
    task: ImageGenerationTask
    project: ProjectAggregate


class ImageTaskListResponse(DomainModel):
    tasks: list[ImageGenerationTask]
