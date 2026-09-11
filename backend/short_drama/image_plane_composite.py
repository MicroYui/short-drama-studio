"""Deterministic four-corner image compositing; no lighting or occlusion inference."""

from __future__ import annotations

from contextlib import ExitStack
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
                warped = temporary.enter_context(clipped.transform(base.size, Image.Transform.PERSPECTIVE,
                    _coefficients(layer, base.size, source.size), Image.Resampling.BICUBIC))
                plane_mask = temporary.enter_context(Image.new("L", base.size))
                ImageDraw.Draw(plane_mask).polygon(_pixel_points(layer.target_quad, base.size), fill=255)
                permitted = temporary.enter_context(ImageChops.subtract(plane_mask, protected))
                warped_alpha = temporary.enter_context(warped.getchannel("A"))
                alpha = temporary.enter_context(ImageChops.multiply(warped_alpha, permitted))
                if layer.blend_mode == "multiply":
                    # White source pixels are neutral; target lighting is preserved, existing ink is not removed.
                    result_rgb = temporary.enter_context(result.convert("RGB"))
                    warped_rgb = temporary.enter_context(warped.convert("RGB"))
                    product = temporary.enter_context(ImageChops.multiply(result_rgb, warped_rgb))
                    multiplied = temporary.enter_context(product.convert("RGBA"))
                    result_alpha = temporary.enter_context(result.getchannel("A"))
                    multiplied.putalpha(result_alpha)
                    updated = images.enter_context(Image.composite(multiplied, result, alpha))
                else:
                    warped.putalpha(alpha)
                    updated = images.enter_context(Image.alpha_composite(result, warped))
                result.close()
                result = updated
        result.save(output_path, format="PNG")
        return base.size
