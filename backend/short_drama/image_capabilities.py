"""Read-only capability snapshot for the configured GPT Image adapter."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from short_drama.image_models import (
    IMAGE_DIMENSION_MULTIPLE,
    IMAGE_MAXIMUM_EDGE_PIXELS,
    IMAGE_MAXIMUM_TOTAL_PIXELS,
    IMAGE_MINIMUM_DIMENSION_PIXELS,
    IMAGE_MINIMUM_TOTAL_PIXELS,
    ImageOperation,
    ImagePricingSnapshot,
    ImageProviderConfigResponse,
)
from short_drama.models import AssetLayoutProfile, DomainModel
from short_drama.providers.openai_image_endpoint import resolve_image_endpoint


class ImageCapabilityOperation(StrEnum):
    """Stable Agent-facing names for the image creation routes."""

    GENERATE = "GENERATE"
    REFERENCE_GENERATE = "REFERENCE_GENERATE"
    EDIT = "EDIT"


class ImageOperationCapability(DomainModel):
    operation: ImageCapabilityOperation
    supported: bool
    accepts_reference_images: bool
    requires_reference_images: bool
    uses_high_input_fidelity: bool
    supports_mask: bool
    note: str


class ImageSizeCapability(DomainModel):
    """Constraints currently enforced by this repository's request adapter."""

    dimension_multiple: int = IMAGE_DIMENSION_MULTIPLE
    minimum_dimension_pixels: int = IMAGE_MINIMUM_DIMENSION_PIXELS
    minimum_total_pixels: int = IMAGE_MINIMUM_TOTAL_PIXELS
    minimum_aspect_ratio: str = "1:3"
    maximum_aspect_ratio: str = "3:1"
    maximum_edge_pixels: int = IMAGE_MAXIMUM_EDGE_PIXELS
    maximum_total_pixels: int = IMAGE_MAXIMUM_TOTAL_PIXELS
    layout_aspect_ratios: dict[str, str] = Field(default_factory=dict[str, str])
    note: str


class ImageTransparentBackgroundCapability(DomainModel):
    accepted_by_adapter: bool
    allowed_output_formats: list[str] = Field(default_factory=list[str])
    note: str


class ImageProviderCapabilityResponse(DomainModel):
    """Secret-free, no-network image capability view for production Agents."""

    provider: str
    model: str
    is_azure: bool
    credential_configured: bool
    can_generate: bool
    operations: list[ImageOperationCapability]
    supports_multiple_references: bool
    maximum_reference_images: int
    size: ImageSizeCapability
    transparent_background: ImageTransparentBackgroundCapability
    pricing: ImagePricingSnapshot


def build_image_provider_capability(
    config: ImageProviderConfigResponse,
) -> ImageProviderCapabilityResponse:
    """Describe local adapter behavior without validating credentials remotely."""

    endpoint = resolve_image_endpoint(config.base_url)
    implemented_operations = {item.value for item in ImageOperation}
    reference_generate_supported = (
        ImageCapabilityOperation.REFERENCE_GENERATE.value in implemented_operations
    )
    operations = [
        ImageOperationCapability(
            operation=ImageCapabilityOperation.GENERATE,
            supported=ImageCapabilityOperation.GENERATE.value
            in implemented_operations,
            accepts_reference_images=False,
            requires_reference_images=False,
            uses_high_input_fidelity=False,
            supports_mask=False,
            note="只根据文本创建新图像，不接收视觉参考。",
        ),
        ImageOperationCapability(
            operation=ImageCapabilityOperation.REFERENCE_GENERATE,
            supported=reference_generate_supported,
            accepts_reference_images=True,
            requires_reference_images=True,
            uses_high_input_fidelity=reference_generate_supported,
            supports_mask=False,
            note=(
                "使用已声明职责的身份、服饰、布局、风格、场景、"
                "道具或构图参考，创建该资产的首个视觉版本。"
            ),
        ),
        ImageOperationCapability(
            operation=ImageCapabilityOperation.EDIT,
            supported=ImageCapabilityOperation.EDIT.value in implemented_operations,
            accepts_reference_images=True,
            requires_reference_images=True,
            uses_high_input_fidelity=True,
            supports_mask=True,
            note="从目标资产的已有父版本派生新版本。",
        ),
    ]
    if endpoint.is_azure:
        transparent = ImageTransparentBackgroundCapability(
            accepted_by_adapter=False,
            allowed_output_formats=[],
            note=(
                "当前 Azure 适配器会在提交 Provider 前拒绝透明背景，"
                "请改用不透明背景。"
            ),
        )
    else:
        transparent = ImageTransparentBackgroundCapability(
            accepted_by_adapter=True,
            allowed_output_formats=["png", "webp"],
            note=(
                "适配器接受透明 PNG 或 WebP 请求；实际是否可用仍以"
                "Provider 返回为准。"
            ),
        )
    return ImageProviderCapabilityResponse(
        provider=config.provider,
        model=config.model,
        is_azure=endpoint.is_azure,
        credential_configured=config.has_api_key,
        can_generate=config.can_generate,
        operations=operations,
        supports_multiple_references=True,
        maximum_reference_images=16,
        size=ImageSizeCapability(
            layout_aspect_ratios={
                AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9.value: "16:9",
                AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2.value: "16:9",
                AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2.value: "3:2",
                AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1.value: "16:9",
                AssetLayoutProfile.PROP_MASTER_SINGLE.value: "1:1",
            },
            note=(
                "当前 GPT Image 请求适配器在提交 Provider 前强制："
                "宽高均为 16 的倍数、宽高比介于 1:3 至 3:1、"
                "最长边不超过 3840，总像素数介于 "
                "655360 至 8294400。"
            ),
        ),
        transparent_background=transparent,
        pricing=config.pricing.model_copy(deep=True),
    )


__all__ = [
    "ImageCapabilityOperation",
    "ImageOperationCapability",
    "ImageProviderCapabilityResponse",
    "ImageSizeCapability",
    "ImageTransparentBackgroundCapability",
    "build_image_provider_capability",
]
