"""Local choices from one preserved masked-edit Provider response."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import shutil

from PIL import Image, ImageChops, ImageFilter
from pydantic import Field, model_validator

from short_drama.models import DomainModel


class ImageEditResultMode(StrEnum):
    PROVIDER_OUTPUT = "PROVIDER_OUTPUT"
    MASKED_COMPOSITE = "MASKED_COMPOSITE"


class AssetImageEditResultRequest(DomainModel):
    image_task_id: str = Field(min_length=1)
    mode: ImageEditResultMode = Field(description=(
        "PROVIDER_OUTPUT copies the preserved response pixels without local masking. "
        "MASKED_COMPOSITE blends that same response with the task's original primary "
        "image using an explicitly selected mask. Neither mode calls a Provider."
    ))
    mask_version_id: str | None = Field(default=None, min_length=1, description=(
        "Required only for MASKED_COMPOSITE: same-size local PNG mask; transparent "
        "pixels take the saved Provider output, opaque pixels preserve the original primary."
    ))
    mask_feather_pixels: int = Field(default=0, ge=0, le=64, description=(
        "Local inward edge blending only. Zero replaces editable pixels directly; "
        "nonzero blends original pixels back near the mask edge and does not enlarge "
        "the editable region. PROVIDER_OUTPUT requires zero."
    ))
    descriptor: str = Field(min_length=1, max_length=4000, description=(
        "Description for a newly registered candidate. PROVIDER_OUTPUT reuses the exact "
        "task's existing raw candidate without changing its description, review state or selection."
    ))

    @model_validator(mode="after")
    def validate_mode(self) -> AssetImageEditResultRequest:
        if self.mode == ImageEditResultMode.PROVIDER_OUTPUT:
            if self.mask_version_id is not None or self.mask_feather_pixels != 0:
                raise ValueError("PROVIDER_OUTPUT requires no mask_version_id and mask_feather_pixels=0")
        elif self.mask_version_id is None:
            raise ValueError("MASKED_COMPOSITE requires an explicit mask_version_id")
        return self


def render_masked_edit(
    provider_path: Path, primary_path: Path, mask_path: Path, *,
    output_path: Path, mask_feather_pixels: int, output_format: str = "png",
) -> tuple[int, int]:
    """Blend once from the supplied Provider pixels; never expand the edit mask."""
    if not 0 <= mask_feather_pixels <= 64:
        raise ValueError("mask_feather_pixels must be between 0 and 64")
    with Image.open(primary_path) as source_image, Image.open(mask_path) as mask_image, Image.open(provider_path) as generated_image:
        source = source_image.convert("RGBA")
        mask_alpha = mask_image.convert("RGBA").getchannel("A")
        generated = generated_image.convert("RGBA")
    with source, mask_alpha, generated:
        if source.size != generated.size or mask_alpha.size != generated.size:
            raise ValueError("Masked composite dimensions do not match provider output")
        editable = Image.eval(mask_alpha, lambda value: 255 - value)
        if mask_feather_pixels:
            with editable.filter(ImageFilter.GaussianBlur(radius=mask_feather_pixels)) as softened:
                bounded = ImageChops.multiply(editable, softened)
            editable.close()
            editable = bounded
        with editable, Image.composite(generated, source, editable) as composite:
            if output_format == "jpeg":
                with composite.convert("RGB") as rgb:
                    rgb.save(output_path, format="JPEG", quality=95)
            elif output_format == "webp":
                composite.save(output_path, format="WEBP", quality=95)
            else:
                composite.save(output_path, format="PNG")
        return generated.size


def copy_provider_output(provider_path: Path, *, output_path: Path) -> tuple[int, int]:
    """Keep the original response bytes, including its encoding, for direct inspection."""
    with Image.open(provider_path) as opened:
        opened.load()
        size = opened.size
    shutil.copyfile(provider_path, output_path)
    return size


def validate_local_edit_mask(mask_path: Path) -> None:
    """Check the new local re-composition input without tightening legacy Provider edits."""
    with Image.open(mask_path) as opened:
        if opened.format != "PNG":
            raise ValueError("Local edit mask must contain an actual PNG image")
        if "A" not in opened.getbands() and "transparency" not in opened.info:
            raise ValueError("Local edit mask needs an alpha channel or PNG transparency table")
        if opened.getexif().get(274, 1) != 1:
            raise ValueError("Local edit mask pixels must already be upright; EXIF rotation would misalign the edit region")


def validate_local_edit_orientation(image_path: Path, *, source_name: str) -> None:
    """New local masking uses stored pixel coordinates, not display-time rotation."""
    with Image.open(image_path) as opened:
        if opened.getexif().get(274, 1) != 1:
            raise ValueError(
                f"{source_name} pixels must already be upright for MASKED_COMPOSITE; "
                "EXIF rotation would misalign the edit region. PROVIDER_OUTPUT can "
                "still copy the preserved original bytes for inspection"
            )
