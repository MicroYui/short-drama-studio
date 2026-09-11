"""Small local image-region operations shared by asset derivation and visual review."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageOps

from short_drama.image_models import ImageRegion


MAX_SOURCE_PIXELS = 50_000_000


def region_box(region: ImageRegion, width: int, height: int) -> tuple[int, int, int, int]:
    """Include all requested pixels, with no padding or resampling."""

    return (
        math.floor(region.x * width),
        math.floor(region.y * height),
        min(width, math.ceil((region.x + region.width) * width)),
        min(height, math.ceil((region.y + region.height) * height)),
    )


def read_image_region(
    path: Path, region: ImageRegion,
) -> tuple[Image.Image, tuple[int, int], tuple[int, int, int, int]]:
    with Image.open(path) as opened:
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("Image exceeds the 50 million pixel local processing limit")
        oriented = ImageOps.exif_transpose(opened)
        box = region_box(region, oriented.width, oriented.height)
        return oriented.crop(box), oriented.size, box
