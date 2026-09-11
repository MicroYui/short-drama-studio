"""Output geometry from the saved request and the actual boundary images."""

from __future__ import annotations

import base64
from binascii import Error as Base64Error
from io import BytesIO
from pathlib import Path

from PIL import Image

from short_drama.h3_models import H3MediaRole, H3ProviderRun


def ratio_value(value: str | None) -> float | None:
    if not value or value == "adaptive":
        return None
    try:
        width, height = (float(part) for part in value.split(":"))
        return width / height if width > 0 and height > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def aspect_matches(actual: float, expected: float) -> bool:
    # Relative tolerance handles encoder alignment equally in wide and tall frames.
    return abs(actual / expected - 1) <= 0.03


def _image_size(url: str | None, path: str | None) -> tuple[int, int] | None:
    try:
        if url is not None and url.startswith("data:image/") and ";base64," in url:
            source = BytesIO(base64.b64decode(url.split(",", 1)[1], validate=True))
        elif path is not None:
            source = Path(path).expanduser()
        else:
            return None
        with Image.open(source) as image:
            width, height = image.size
            if image.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            return (width, height) if width > 0 and height > 0 else None
    except (OSError, ValueError, Base64Error, Image.DecompressionBombError):
        return None


def expected_output_aspect(run: H3ProviderRun) -> tuple[float | None, str]:
    request = run.request
    if request is None:
        return None, "aspect=UNVERIFIED (no saved provider request)"
    fixed = ratio_value(request.ratio)
    if fixed is not None:
        return fixed, f"aspect={request.ratio} (saved provider request)"
    boundaries = {H3MediaRole.FIRST_FRAME, H3MediaRole.LAST_FRAME}
    observations: list[tuple[float, str]] = []
    unknown = False
    for item in request.content:
        if item.role is None or item.role not in boundaries or item.image_url is None:
            continue
        local = next((entry.source.local_path for entry in run.media_inputs if entry.role == item.role), None)
        size = _image_size(item.image_url.url, local)
        if size is None:
            unknown = True
            continue
        observations.append((size[0] / size[1], f"{item.role.value} {size[0]}x{size[1]}"))
    if unknown or not observations:
        return None, "aspect=UNVERIFIED (adaptive boundary image dimensions unavailable)"
    expected = observations[0][0]
    if any(not aspect_matches(value, expected) for value, _label in observations[1:]):
        return None, "aspect=UNVERIFIED (adaptive boundary images have different aspect ratios)"
    return expected, f"aspect={expected:.6f} (boundary image: {', '.join(label for _value, label in observations)})"
