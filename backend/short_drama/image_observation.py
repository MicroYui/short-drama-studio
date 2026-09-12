"""Exact image selections and bounded in-memory pixels for an Agent observation.

Selections are durable metadata. Encoded pixels only exist at the language
request boundary; observing an image does not review, approve, or select it.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, JsonValue

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.image_models import ImageRegion
from short_drama.image_regions import read_image_region
from short_drama.models import ApprovalStatus, AssetImageCrop, AssetMediaType, AssetVersion, Freshness


ANALYSIS_IMAGE_MAX_EDGE = 2048
ANALYSIS_IMAGE_MAX_BYTES = 3 * 1024 * 1024
ANALYSIS_IMAGE_MAX_SOURCE_PIXELS = 50_000_000
VisualRole = Literal["CANDIDATE", "SOURCE", "PROVIDER_OUTPUT", "REFERENCE", "GUIDE", "MASK"]


class VisualAttachment(BaseModel):
    """An exact Tool-selected version, never an asset's moving current pointer."""

    model_config = ConfigDict(extra="forbid")
    artifact_ref: ArtifactRef
    asset_id: str
    role: VisualRole
    purpose: str
    local_path: str | None = None
    external_url: str | None = None
    approval_status: ApprovalStatus
    freshness: Freshness
    region: ImageRegion | None = None
    image_crop: AssetImageCrop | None = None


def visual_attachment(
    project_id: str, asset_id: str, version: AssetVersion, *, role: VisualRole,
    purpose: str, region: ImageRegion | None = None,
) -> VisualAttachment:
    if version.media_type != AssetMediaType.IMAGE:
        raise ValueError(f"AssetVersion {version.id} is not an image")
    return VisualAttachment(
        artifact_ref=ArtifactRef(project_id=project_id, artifact_type="asset_version",
            artifact_id=version.id, version=version.version),
        asset_id=asset_id, role=role, purpose=purpose, local_path=version.local_path,
        external_url=version.external_url, approval_status=version.approval_status,
        freshness=version.freshness, region=region, image_crop=version.image_crop,
    )


def analysis_thumbnail_bytes(path: Path) -> bytes:
    """EXIF-correct bounded review pixels; the source file stays unchanged."""
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > ANALYSIS_IMAGE_MAX_SOURCE_PIXELS:
                raise ValueError(
                    "AssetVersion image is too large for in-process semantic review; "
                    "create a bounded derivative while preserving the original source"
                )
            opened.draft("RGB", (ANALYSIS_IMAGE_MAX_EDGE, ANALYSIS_IMAGE_MAX_EDGE))
            return encode_review_image(ImageOps.exif_transpose(opened))
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(f"AssetVersion image cannot be decoded: {path}") from exc


def encode_review_image(source: Image.Image) -> bytes:
    image = source.copy()
    image.thumbnail((ANALYSIS_IMAGE_MAX_EDGE, ANALYSIS_IMAGE_MAX_EDGE),
        Image.Resampling.LANCZOS, reducing_gap=3.0)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, "#808080")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        image = flattened
    elif image.mode != "RGB":
        image = image.convert("RGB")
    quality = 90
    while True:
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
        output = buffer.getvalue()
        if len(output) <= ANALYSIS_IMAGE_MAX_BYTES:
            return output
        if quality > 72:
            quality -= 6
            continue
        next_size = (max(1, int(image.width * 0.85)), max(1, int(image.height * 0.85)))
        if next_size == image.size:
            raise ValueError("AssetVersion review thumbnail cannot be bounded")
        image = image.resize(next_size, Image.Resampling.LANCZOS)


def asset_image_url(version: AssetVersion) -> str:
    if version.external_url is not None:
        return version.external_url
    if version.local_path is None:
        raise ValueError(f"AssetVersion {version.id} has no usable image")
    path = Path(version.local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"AssetVersion image is missing: {path}")
    return _jpeg_url(analysis_thumbnail_bytes(path))


def _jpeg_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


@dataclass(frozen=True)
class EncodedImageObservation:
    image_url: str
    metadata: dict[str, JsonValue]


def encode_attachment(attachment: VisualAttachment) -> EncodedImageObservation:
    metadata: dict[str, JsonValue] = attachment.model_dump(mode="json", exclude={"external_url", "local_path"})
    if attachment.external_url is not None and attachment.region is None:
        metadata.update(delivery="EXACT_EXTERNAL_URL", observed_dimensions=None,
            observation_transform="External image supplied directly; dimensions are provider-resolved.")
        return EncodedImageObservation(attachment.external_url, metadata)
    if attachment.local_path is None:
        raise ValueError("No local image is available for this exact version/region")
    path = Path(attachment.local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Exact image file is missing: {path}")
    try:
        if attachment.region is not None:
            cropped, source_size, box = read_image_region(path, attachment.region)
            data = encode_review_image(cropped)
            metadata.update(source_dimensions=list(source_size), source_pixel_box=list(box))
        else:
            with Image.open(path) as opened:
                # Read the original header, not JPEG draft decoding dimensions.
                size = opened.size[::-1] if opened.getexif().get(274, 1) in {5, 6, 7, 8} else opened.size
            metadata.update(source_dimensions=list(size), source_pixel_box=[0, 0, *size])
            data = analysis_thumbnail_bytes(path)
        with Image.open(BytesIO(data)) as observed:
            metadata["observed_dimensions"] = [observed.width, observed.height]
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Exact image cannot be decoded: {path}") from exc
    metadata.update(delivery="LOCAL_REVIEW_IMAGE", observation_transform=(
        "EXIF oriented; requested region uses native oriented pixels; review JPEG longest edge <=2048, "
        "<=3MiB; transparency shown on mid-grey. The original file is unchanged. "
        "Use image.view with a region when fine detail is not visible at this observation size."
    ))
    return EncodedImageObservation(_jpeg_url(data), metadata)
