"""Choose a delivery canvas from actual selected media, without changing that media."""

from collections.abc import Sequence
from fractions import Fraction
from math import ceil


def source_output_dimensions(
    sizes: Sequence[tuple[int, int]], aspect_ratio: str,
) -> tuple[int, int]:
    """Fit every selected source at native size in the intended output ratio."""
    try:
        left, right = aspect_ratio.split(":")
        ratio = Fraction(left) / Fraction(right)
        if ratio <= 0:
            raise ValueError("non-positive aspect ratio")
    except (ValueError, ZeroDivisionError):
        ratio = Fraction(16, 9)
    valid = [(width, height) for width, height in sizes if width > 0 and height > 0]
    if valid:
        width = max(size[0] for size in valid)
        height = max(size[1] for size in valid)
        if Fraction(width, height) < ratio:
            width = ceil(height * ratio)
        else:
            height = ceil(width / ratio)
    elif ratio == 1:
        width = height = 1080
    elif ratio > 1:
        height, width = 720, ceil(720 * ratio)
    else:
        width, height = 720, ceil(720 / ratio)
    # H.264 yuv420p needs even dimensions. Round outward to retain source pixels.
    return max(2, (width + 1) // 2 * 2), max(2, (height + 1) // 2 * 2)
