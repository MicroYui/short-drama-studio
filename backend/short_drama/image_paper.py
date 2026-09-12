"""Explicit local paper/ink processing; callers supply observed regions and scale."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image


def normalize_paper_ink(
    source: Image.Image, blank_box: tuple[int, int, int, int], background_window_px: int,
) -> Image.Image:
    """Return RGBA with normalized source ink and original alpha; never modify source."""
    rgba: NDArray[np.uint8] = np.array(source, dtype=np.uint8)
    left, top, right, bottom = blank_box
    if not np.all(rgba[top:bottom, left:right, 3] == 255):
        raise ValueError("paper_ink blank_sample must be fully opaque source paper")
    rgb = rgba[:, :, :3].copy()
    # Hidden RGB is not source paper. Neutralize it for the local estimate only.
    rgb[rgba[:, :, 3] == 0] = 255
    background = cv2.GaussianBlur(cv2.medianBlur(rgb, background_window_px),
        (0, 0), (background_window_px - 1) / 10).astype(np.float32)
    density = 1 - np.clip(rgb.astype(np.float32) / np.maximum(background, 1), 0, 1)
    floor = np.quantile(density[top:bottom, left:right], .99, axis=(0, 1))
    if np.any(floor >= 1):
        raise ValueError("paper_ink blank_sample contains dark content, not usable blank paper")
    ink = np.rint((1 - np.maximum(density - floor, 0) / (1 - floor)) * 255).astype(np.uint8)
    rgba[:, :, :3] = ink
    return Image.fromarray(rgba)


def clean_target_paper(base: Image.Image, allowed: Image.Image, background_window_px: int) -> Image.Image:
    """Restore only selected thin dark print; the rest of base RGBA is exact."""
    rgba: NDArray[np.uint8] = np.array(base, dtype=np.uint8)
    rgb = rgba[:, :, :3].copy()
    selected = (np.array(allowed, dtype=np.uint8) > 0) & (rgba[:, :, 3] == 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (background_window_px, background_window_px))
    estimate = cv2.morphologyEx(rgb, cv2.MORPH_CLOSE, kernel)
    marks = (estimate.astype(np.int16) - rgb.astype(np.int16)).max(axis=-1) > 5
    mask = ((marks & selected).astype(np.uint8) * 255)
    mask = cast("NDArray[np.uint8]", cv2.dilate(mask, np.ones((3, 3), dtype=np.uint8)))
    mask[~selected] = 0  # Dilation cannot grow outside explicit regions or protection.
    if np.any(mask):
        repaired = cv2.inpaint(rgb, mask, 3, cv2.INPAINT_TELEA)
        changed = mask > 0
        rgba[changed, :3] = repaired[changed]
    return Image.fromarray(rgba)
