"""Local visual instructions and alpha masks for an existing image edit."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import Field, model_validator

from short_drama.image_models import parse_image_size
from short_drama.image_regions import MAX_SOURCE_PIXELS
from short_drama.models import AssetVersion, DomainModel


class ImageGuidePoint(DomainModel):
    x: float = Field(ge=0, le=1, description="Visible image coordinate, left=0 and right=1.")
    y: float = Field(ge=0, le=1, description="Visible image coordinate, top=0 and bottom=1.")


class ImageEditGuideRegion(DomainModel):
    polygon: list[ImageGuidePoint] = Field(min_length=3, max_length=20,
        description="Ordered boundary of the actual editable area, including the intended new position; coordinates are normalized after EXIF orientation.")
    label: str = Field(min_length=1, max_length=120,
        description="Concrete change inside this area. Identify the visible feature and desired result, not only world directions.")

    @model_validator(mode="after")
    def nonempty_polygon(self) -> ImageEditGuideRegion:
        points = self.polygon
        area = sum(a.x * b.y - b.x * a.y for a, b in zip(points, [*points[1:], points[0]]))
        if abs(area) < 1e-8:
            raise ValueError("Edit region must enclose a nonzero area")
        return self


class ImageGuideArrow(DomainModel):
    start: ImageGuidePoint
    end: ImageGuidePoint
    label: str = Field(min_length=1, max_length=120,
        description="Meaning of the arrow, for example 'title edge here'; annotations are guidance, not content to reproduce.")

    @model_validator(mode="after")
    def nonempty_arrow(self) -> ImageGuideArrow:
        if self.start == self.end:
            raise ValueError("Guide arrow must have different start and end points")
        return self


class AssetImageEditGuideRequest(DomainModel):
    source_asset_version_id: str = Field(min_length=1,
        description="Exact existing local candidate to edit; first inspect its actual pixels.")
    regions: list[ImageEditGuideRegion] = Field(min_length=1, max_length=16)
    arrows: list[ImageGuideArrow] = Field(default_factory=list[ImageGuideArrow], max_length=16)
    descriptor: str = Field(min_length=1, max_length=2000)


class AssetImageEditGuideResponse(DomainModel):
    project_id: str
    asset_id: str
    source_asset_version_id: str
    guide_version: AssetVersion
    mask_version: AssetVersion


def validate_edit_guide_geometry(
    regions: list[ImageEditGuideRegion], arrows: list[ImageGuideArrow], width: int, height: int,
) -> None:
    """Reject normalized geometry that disappears at the actual pixel resolution."""
    for region in regions:
        points = [_guide_pixel(point, width, height) for point in region.polygon]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, [*points[1:], points[0]]))
        if len(set(points)) < 3 or area == 0:
            raise ValueError("Edit region is too small at this image resolution")
    for arrow in arrows:
        if _guide_pixel(arrow.start, width, height) == _guide_pixel(arrow.end, width, height):
            raise ValueError("Guide arrow is too small at this image resolution")


def _guide_pixel(point: ImageGuidePoint, width: int, height: int) -> tuple[int, int]:
    return (round(point.x * (width - 1)), round(point.y * (height - 1)))


def render_edit_guide(
    source_path: Path, request: AssetImageEditGuideRequest, *,
    guide_path: Path, mask_path: Path, font_source: str,
) -> tuple[int, int]:
    """Draw a separate guide and an opaque-preserve / transparent-edit mask."""

    with Image.open(source_path) as opened:
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("Image exceeds the 50 million pixel local processing limit")
        if opened.getexif().get(274, 1) != 1:
            raise ValueError("Edit guide needs upright source pixels; use image.crop with the full image region first, then use that returned version as the edit source")
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    with source:
        width, height = source.size
        try:
            parse_image_size(f"{width}x{height}")
        except ValueError as exc:
            raise ValueError(f"Source size {width}x{height} cannot feed the existing masked EDIT: {exc}. Use a compatible source, or full-image EDIT without a mask; changing only the requested output size does not align this mask.") from exc
        validate_edit_guide_geometry(request.regions, request.arrows, width, height)
        stroke = max(2, round(min(width, height) / 240))
        font = ImageFont.truetype(font_source, max(12, round(min(width, height) / 45)))
        overlay = Image.new("RGBA", source.size)
        mask = Image.new("RGBA", source.size, (255, 255, 255, 255))
        drawing = ImageDraw.Draw(overlay)
        masking = ImageDraw.Draw(mask)

        def pixel(point: ImageGuidePoint) -> tuple[int, int]:
            return _guide_pixel(point, width, height)

        def label(text: str, at: tuple[int, int]) -> None:
            # Wrap using the actual font metrics, including CJK text.
            lines: list[str] = []
            current = ""
            limit = max(1, width - 4 * stroke)
            for char in text:
                if current and drawing.textlength(current + char, font=font) > limit:
                    lines.append(current)
                    current = ""
                current += char
            if current:
                lines.append(current)
            value = "\n".join(lines)
            bounds = drawing.multiline_textbbox((0, 0), value, font=font, spacing=stroke)
            bw, bh = bounds[2] - bounds[0], bounds[3] - bounds[1]
            x = max(stroke, min(at[0], width - bw - 2 * stroke))
            y = max(stroke, min(at[1], height - bh - 2 * stroke))
            drawing.rectangle((x - stroke, y - stroke, x + bw + stroke, y + bh + stroke), fill=(255, 255, 255, 235))
            drawing.multiline_text((x - bounds[0], y - bounds[1]), value, font=font, spacing=stroke, fill=(170, 0, 0, 255))

        for index, region in enumerate(request.regions, start=1):
            points = [pixel(point) for point in region.polygon]
            masking.polygon(points, fill=(255, 255, 255, 0))
            drawing.polygon(points, fill=(255, 40, 40, 24), outline=(220, 0, 0, 255), width=stroke)
            label(f"{index}. {region.label}", points[0])
        for arrow in request.arrows:
            start, end = pixel(arrow.start), pixel(arrow.end)
            angle = math.atan2(end[1] - start[1], end[0] - start[0])
            length = max(10, stroke * 4)
            head = [end, *( (round(end[0] - length * math.cos(angle + offset)),
                              round(end[1] - length * math.sin(angle + offset)))
                            for offset in (-0.55, 0.55))]
            drawing.line((start, end), fill=(220, 0, 0, 255), width=stroke)
            drawing.polygon(head, fill=(220, 0, 0, 255))
            label(arrow.label, start)
        with Image.alpha_composite(source, overlay) as guide:
            guide.convert("RGB").save(guide_path, format="PNG")
        mask.save(mask_path, format="PNG")
        overlay.close()
        mask.close()
        return width, height
