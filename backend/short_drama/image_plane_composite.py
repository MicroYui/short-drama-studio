"""Deterministic four-corner image compositing; no lighting or occlusion inference."""

from __future__ import annotations

from contextlib import ExitStack
import math
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageDraw, ImageOps
from pydantic import Field, model_validator

from short_drama.image_regions import MAX_SOURCE_PIXELS
from short_drama.models import DomainModel


class ImagePlanePoint(DomainModel):
    x: float = Field(ge=0, le=1, description="Visible upright image coordinate: left=0, right=1.")
    y: float = Field(ge=0, le=1, description="Visible upright image coordinate: top=0, bottom=1.")


class ImagePlaneLayer(DomainModel):
    source_asset_version_id: str = Field(min_length=1, description="Exact usable image containing the intended existing content.")
    source_quad: list[ImagePlanePoint] | None = Field(default=None, min_length=4, max_length=4,
        description="Four visible source-region corners in boundary order; an interior coplanar region is valid if it contains all required content. Exclude photographic background. Omit only for a whole-image isolated plane (TL,TR,BR,BL).")
    target_quad: list[ImagePlanePoint] = Field(min_length=4, max_length=4,
        description="Four visible base-region corners matching source_quad one-for-one; physical object corners are not required. Preserve corner identities, usable direction and proportions, not independent screen sorting. Only this region is changed, without whole-page extrapolation. Convex, noncrossing, same winding; no mirror.")
    blend_mode: Literal["replace", "multiply"] = Field(default="replace",
        description="replace covers existing pixels with source pixels/alpha, including their lighting. multiply adds dark ink onto an already blank lit surface; it cannot erase old text. Neither mode infers shadows, removes old paper outside the plane, or repairs foreground hands.")
    source_white_point: int | None = Field(default=None, ge=1, le=255, strict=True,
        description="Optional photographic-paper white point from an observed blank source area (1–255). None preserves source RGB; otherwise RGB=min(255, round(channel*255/value)), alpha unchanged. Use only when source paper gray interferes, usually with multiply on a blank base. Keeps original ink geometry, but light ink/color can weaken or clip; inspect handwriting, red edits and fine lines. No automatic value.")

    @model_validator(mode="after")
    def coherent_corners(self) -> ImagePlaneLayer:
        source = _points(self.source_quad or _full_quad())
        target = _points(self.target_quad)
        source_area = _convex_area(source, "source_quad")
        target_area = _convex_area(target, "target_quad")
        if source_area * target_area <= 0:
            raise ValueError("source_quad and target_quad must have the same winding; preserve matching corner identities without mirroring")
        return self


class ImagePlaneProtectedRegion(DomainModel):
    polygon: list[ImagePlanePoint] = Field(min_length=3, max_length=20,
        description="Observed foreground area in the actual base image, for example fingers or a pen. Original base pixels here survive every layer; no automatic segmentation.")
    label: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def valid_polygon(self) -> ImagePlaneProtectedRegion:
        _validate_polygon(_points(self.polygon), "protected region")
        return self


class AssetImagePlaneCompositeRequest(DomainModel):
    base_asset_version_id: str = Field(min_length=1, description="Exact existing base image whose visible pixels and foreground were inspected.")
    layers: list[ImagePlaneLayer] = Field(min_length=1, max_length=8,
        description="Apply in order, later layers above earlier ones. Flat content only; inspect source/base corners and lighting before choosing a mode.")
    protected_regions: list[ImagePlaneProtectedRegion] = Field(default_factory=list[ImagePlaneProtectedRegion], max_length=16)
    descriptor: str = Field(min_length=1, max_length=2000)


def _full_quad() -> list[ImagePlanePoint]:
    return [ImagePlanePoint(x=x, y=y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]


def _points(points: list[ImagePlanePoint]) -> list[tuple[float, float]]:
    return [(point.x, point.y) for point in points]


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _area(points: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, [*points[1:], points[0]]))


def _convex_area(points: list[tuple[float, float]], label: str) -> float:
    turns = [_cross(points[index], points[(index + 1) % 4], points[(index + 2) % 4]) for index in range(4)]
    if not (all(turn > 1e-10 for turn in turns) or all(turn < -1e-10 for turn in turns)):
        raise ValueError(f"{label} must be a nondegenerate convex four-corner boundary without crossed edges")
    return _area(points)


def _validate_polygon(points: list[tuple[float, float]], label: str) -> None:
    if len(set(points)) != len(points) or abs(_area(points)) <= 1e-10:
        raise ValueError(f"{label} must enclose a nonzero area without duplicate vertices")
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue
            c, d = points[j], points[(j + 1) % count]
            # Nonadjacent edges may not touch or cross, including collinear overlaps.
            if _cross(a, b, c) * _cross(a, b, d) <= 0 and _cross(c, d, a) * _cross(c, d, b) <= 0:
                if max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1])):
                    raise ValueError(f"{label} has intersecting edges")


def _pixel_points(points: list[ImagePlanePoint], size: tuple[int, int]) -> list[tuple[int, int]]:
    return [(round(point.x * (size[0] - 1)), round(point.y * (size[1] - 1))) for point in points]


def validate_plane_composite_geometry(
    request: AssetImagePlaneCompositeRequest, base_size: tuple[int, int],
    source_sizes: dict[str, tuple[int, int]],
) -> None:
    """Validate the same observed geometry for QA advice and actual rendering."""
    def size_ok(size: tuple[int, int], label: str) -> None:
        if min(size) < 2 or size[0] * size[1] > MAX_SOURCE_PIXELS:
            raise ValueError(f"{label} size {size[0]}x{size[1]} must have both dimensions >=2 and at most 50 million pixels")

    size_ok(base_size, "Base")
    for layer in request.layers:
        source_size = source_sizes.get(layer.source_asset_version_id)
        if source_size is None:
            raise ValueError(f"Missing actual source image size for {layer.source_asset_version_id}")
        size_ok(source_size, layer.source_asset_version_id)
        for label, quad, size in (("source_quad", layer.source_quad or _full_quad(), source_size), ("target_quad", layer.target_quad, base_size)):
            _convex_area([(float(x), float(y)) for x, y in _pixel_points(quad, size)], f"{label} at actual pixel resolution")
    for region in request.protected_regions:
        _validate_polygon([(float(x), float(y)) for x, y in _pixel_points(region.polygon, base_size)], "protected region at actual pixel resolution")


def _solve(matrix: list[list[float]], values: list[float]) -> list[float]:
    """Eight-variable elimination on normalized coordinates, without a new dependency."""
    rows = [[*row, value] for row, value in zip(matrix, values)]
    for column in range(8):
        pivot = max(range(column, 8), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("Plane corners do not define a stable perspective transform")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for row in range(8):
            if row != column:
                factor = rows[row][column]
                rows[row] = [value - factor * pivot_value for value, pivot_value in zip(rows[row], rows[column])]
    return [row[-1] for row in rows]


def _coefficients(layer: ImagePlaneLayer, base_size: tuple[int, int], source_size: tuple[int, int]) -> tuple[float, ...]:
    matrix: list[list[float]] = []
    values: list[float] = []
    for target, source in zip(layer.target_quad, layer.source_quad or _full_quad()):
        x, y, u, v = target.x, target.y, source.x, source.y
        matrix.extend([[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]])
        values.extend([u, v])
    a, b, c, d, e, f, g, h = _solve(matrix, values)
    bw, bh = base_size[0] - 1, base_size[1] - 1
    sw, sh = source_size[0] - 1, source_size[1] - 1
    return (a * sw / bw, b * sw / bh, c * sw, d * sh / bw, e * sh / bh, f * sh, g / bw, h / bh)


def _read_upright(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("Image exceeds the 50 million pixel local processing limit")
        with ImageOps.exif_transpose(opened) as upright:
            return upright.convert("RGBA")


_MAX_SUPERSAMPLED_PIXELS = 8_000_000


def _plane_sampling_scale(
    coefficients: tuple[float, ...], layer: ImagePlaneLayer,
    base_size: tuple[int, int], roi_size: tuple[int, int],
) -> int:
    """Choose bounded area sampling only when the inverse map reduces detail."""
    a, b, c, d, e, f, g, h = coefficients
    corners = [(p.x * (base_size[0] - 1), p.y * (base_size[1] - 1)) for p in layer.target_quad]
    samples = [*corners,
        *[((x + xx) / 2, (y + yy) / 2) for (x, y), (xx, yy) in zip(corners, [*corners[1:], corners[0]])],
        (sum(x for x, _y in corners) / 4, sum(y for _x, y in corners) / 4)]
    maximum_stretch = 1.0
    for x, y in samples:
        denominator = g * x + h * y + 1
        if abs(denominator) < 1e-12:
            maximum_stretch = 4.0
            break
        u, v = a * x + b * y + c, d * x + e * y + f
        square = denominator * denominator
        j00, j01 = (a * denominator - g * u) / square, (b * denominator - h * u) / square
        j10, j11 = (d * denominator - g * v) / square, (e * denominator - h * v) / square
        aa, ab, bb = j00 * j00 + j10 * j10, j00 * j01 + j10 * j11, j01 * j01 + j11 * j11
        stretch = math.sqrt(max(0.0, (aa + bb + math.hypot(aa - bb, 2 * ab)) / 2))
        maximum_stretch = max(maximum_stretch, stretch)
    if maximum_stretch <= 1 + 1e-7:
        return 1  # Preserve the existing sharp 1:1/enlargement interpolation.
    desired = max(2, math.ceil(min(4.0, maximum_stretch)))
    allocation_limit = math.isqrt(_MAX_SUPERSAMPLED_PIXELS // (roi_size[0] * roi_size[1]))
    # Large previously valid planes keep their old sampling if even 2x cannot fit.
    return max(1, min(desired, allocation_limit))


def _plane_sampling_box(layer: ImagePlaneLayer, base_size: tuple[int, int]) -> tuple[int, int, int, int]:
    points = _pixel_points(layer.target_quad, base_size)
    return (max(0, min(x for x, _y in points) - 3),
            max(0, min(y for _x, y in points) - 3),
            min(base_size[0], max(x for x, _y in points) + 4),
            min(base_size[1], max(y for _x, y in points) + 4))


def _roi_coefficients(coefficients: tuple[float, ...], left: int, top: int, scale: int) -> tuple[float, ...]:
    a, b, c, d, e, f, g, h = coefficients
    divisor = g * left + h * top + 1
    return (a / scale / divisor, b / scale / divisor, (a * left + b * top + c) / divisor,
            d / scale / divisor, e / scale / divisor, (d * left + e * top + f) / divisor,
            g / scale / divisor, h / scale / divisor)


def render_plane_composite(
    base_path: Path, source_paths: dict[str, Path], request: AssetImagePlaneCompositeRequest, *, output_path: Path,
) -> tuple[int, int]:
    """Warp exact existing pixels. Keep visible base pixels outside planes/protection."""
    if output_path.resolve() in {base_path.resolve(), *(path.resolve() for path in source_paths.values())}:
        raise ValueError("Composite output must be a new file, not an existing source or base")
    with ExitStack() as images:
        base = images.enter_context(_read_upright(base_path))
        sources: dict[str, Image.Image] = {}
        for layer in request.layers:
            source_id = layer.source_asset_version_id
            if source_id not in source_paths:
                raise ValueError(f"Missing actual source image for {source_id}")
            if source_id not in sources:
                sources[source_id] = images.enter_context(_read_upright(source_paths[source_id]))
        validate_plane_composite_geometry(request, base.size, {key: image.size for key, image in sources.items()})
        protected = images.enter_context(Image.new("L", base.size))
        for region in request.protected_regions:
            ImageDraw.Draw(protected).polygon(_pixel_points(region.polygon, base.size), fill=255)
        result = images.enter_context(base.copy())
        for layer in request.layers:
            with ExitStack() as temporary:
                source = sources[layer.source_asset_version_id]
                # Clip the source plane too: bicubic sampling must not pull photographic background into its boundary.
                source_mask = temporary.enter_context(Image.new("L", source.size))
                ImageDraw.Draw(source_mask).polygon(_pixel_points(layer.source_quad or _full_quad(), source.size), fill=255)
                clipped = temporary.enter_context(source.copy())
                source_alpha = temporary.enter_context(source.getchannel("A"))
                clipped_alpha = temporary.enter_context(ImageChops.multiply(source_alpha, source_mask))
                clipped.putalpha(clipped_alpha)
                if layer.source_white_point not in (None, 255):
                    lut = [min(255, round(channel * 255 / layer.source_white_point)) for channel in range(256)]
                    # Transform existing RGB only; retain clipped alpha and exact ink positions.
                    clipped = temporary.enter_context(clipped.point([*lut, *lut, *lut, *range(256)]))
                coefficients = _coefficients(layer, base.size, source.size)
                box = _plane_sampling_box(layer, base.size)
                if abs(coefficients[6] * box[0] + coefficients[7] * box[1] + 1) < 1e-12:
                    # A valid plane may have its homography horizon just outside its quad.
                    # Preserve the original origin instead of rejecting that input.
                    box = (0, 0, *base.size)
                roi_size = (box[2] - box[0], box[3] - box[1])
                scale = _plane_sampling_scale(coefficients, layer, base.size, roi_size)
                if scale == 1:
                    # Keep the original 1x map, including its floating-point
                    # origin: ROI normalization can round a few colors by 1.
                    box = (0, 0, *base.size)
                    roi_size = base.size
                    sampling_coefficients = coefficients
                else:
                    sampling_coefficients = _roi_coefficients(coefficients, box[0], box[1], scale)
                warped = temporary.enter_context(clipped.transform(
                    (roi_size[0] * scale, roi_size[1] * scale), Image.Transform.PERSPECTIVE,
                    sampling_coefficients,
                    Image.Resampling.BILINEAR if scale > 1 else Image.Resampling.BICUBIC))
                if scale > 1:
                    # Positive-weight area averaging avoids cubic alpha-edge overshoot;
                    # the supersampled destination area has a bounded pixel allocation.
                    warped = temporary.enter_context(warped.resize(roi_size, Image.Resampling.BOX))
                plane_mask = temporary.enter_context(Image.new("L", base.size))
                ImageDraw.Draw(plane_mask).polygon(_pixel_points(layer.target_quad, base.size), fill=255)
                permitted = temporary.enter_context(ImageChops.subtract(plane_mask, protected))
                local_permitted = temporary.enter_context(permitted.crop(box))
                warped_alpha = temporary.enter_context(warped.getchannel("A"))
                alpha = temporary.enter_context(ImageChops.multiply(warped_alpha, local_permitted))
                current = temporary.enter_context(result.crop(box))
                if layer.blend_mode == "multiply":
                    # White source pixels are neutral; target lighting is preserved, existing ink is not removed.
                    result_rgb = temporary.enter_context(current.convert("RGB"))
                    warped_rgb = temporary.enter_context(warped.convert("RGB"))
                    product = temporary.enter_context(ImageChops.multiply(result_rgb, warped_rgb))
                    multiplied = temporary.enter_context(product.convert("RGBA"))
                    result_alpha = temporary.enter_context(current.getchannel("A"))
                    multiplied.putalpha(result_alpha)
                    updated = temporary.enter_context(Image.composite(multiplied, current, alpha))
                else:
                    warped.putalpha(alpha)
                    updated = temporary.enter_context(Image.alpha_composite(current, warped))
                result.paste(updated, box)
        result.save(output_path, format="PNG")
        return base.size
