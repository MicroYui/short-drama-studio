"""Actual local perspective pixels, alpha, and observed foreground protection."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from PIL import Image, ImageDraw, ImageOps

from short_drama.image_plane_composite import (
    AssetImagePlaneCompositeRequest, ImagePlaneLayer, ImagePlanePoint,
    ImagePlaneProtectedRegion, render_plane_composite, validate_plane_composite_geometry,
)


def _quad(values: list[tuple[float, float]]) -> list[ImagePlanePoint]:
    return [ImagePlanePoint(x=x, y=y) for x, y in values]


def _layer(*, mode: Literal["replace", "multiply"] = "replace", source: str = "source") -> ImagePlaneLayer:
    return ImagePlaneLayer(source_asset_version_id=source, blend_mode=mode,
        target_quad=_quad([(.2, .2), (.8, .2), (.8, .8), (.2, .8)]))


def _request(layer: ImagePlaneLayer) -> AssetImagePlaneCompositeRequest:
    return AssetImagePlaneCompositeRequest(base_asset_version_id="base", layers=[layer], descriptor="Observed paper plane only")


def _sources(tmp_path: Path, *, size: tuple[int, int] = (101, 101)) -> tuple[Path, Path]:
    base, source = tmp_path / "base.png", tmp_path / "source.png"
    Image.new("RGB", size, (90, 120, 150)).save(base)
    Image.new("RGBA", size, (255, 255, 255, 255)).save(source)
    return base, source


def test_corresponding_corners_rotate_all_source_content_and_preserve_outside(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    with Image.open(source) as image:
        draw = ImageDraw.Draw(image)
        for box, color in [((0, 0, 49, 49), "red"), ((50, 0, 100, 49), "green"),
                           ((50, 50, 100, 100), "blue"), ((0, 50, 49, 100), "yellow")]:
            draw.rectangle(box, fill=color)
        image.save(source)
    layer = _layer()
    layer.target_quad = _quad([(.8, .2), (.8, .8), (.2, .8), (.2, .2)])
    before_base, before_source = base.read_bytes(), source.read_bytes()
    output = tmp_path / "rotated.png"
    assert render_plane_composite(base, {"source": source}, _request(layer), output_path=output) == (101, 101)
    with Image.open(output) as result:
        assert result.getpixel((65, 35)) == (255, 0, 0, 255)
        assert result.getpixel((65, 65)) == (0, 128, 0, 255)
        assert result.getpixel((35, 65)) == (0, 0, 255, 255)
        assert result.getpixel((35, 35)) == (255, 255, 0, 255)
        assert all(result.getpixel((x, y)) == (90, 120, 150, 255)
                   for y in range(101) for x in range(101) if not (20 <= x <= 80 and 20 <= y <= 80))
    assert (base.read_bytes(), source.read_bytes()) == (before_base, before_source)


def test_photographic_source_quad_excludes_background_under_perspective(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    with Image.open(source) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 100, 100), fill="magenta")
        draw.rectangle((20, 20, 80, 80), fill=(215, 210, 200))
        draw.rectangle((30, 30, 40, 40), fill="black")
        image.save(source)
    layer = _layer()
    layer.source_quad = _quad([(.2, .2), (.8, .2), (.8, .8), (.2, .8)])
    layer.target_quad = _quad([(.3, .1), (.9, .3), (.75, .85), (.1, .7)])
    output = tmp_path / "perspective.png"
    render_plane_composite(base, {"source": source}, _request(layer), output_path=output)
    with Image.open(output) as result:
        assert result.getpixel((50, 50)) == (215, 210, 200, 255)
        assert not any(result.getpixel((x, y))[:3] == (255, 0, 255) for y in range(101) for x in range(101))
        assert result.getpixel((0, 0)) == (90, 120, 150, 255)


def test_layer_order_and_foreground_protection_keep_exact_original_pixels(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    Image.new("RGB", (101, 101), "red").save(source)
    second = tmp_path / "second.png"
    Image.new("RGB", (101, 101), "blue").save(second)
    request = _request(_layer())
    request.layers.append(_layer(source="second"))
    request.protected_regions = [ImagePlaneProtectedRegion(label="observed finger",
        polygon=_quad([(.4, .1), (.55, .1), (.55, .9), (.4, .9)]))]
    output = tmp_path / "protected.png"
    render_plane_composite(base, {"source": source, "second": second}, request, output_path=output)
    with Image.open(output) as result:
        assert result.getpixel((35, 50)) == (0, 0, 255, 255)
        assert all(result.getpixel((x, y)) == (90, 120, 150, 255) for x in range(40, 56) for y in range(10, 91))


def test_multiply_retains_lighting_but_cannot_remove_existing_ink(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    with Image.open(base) as image:
        ImageDraw.Draw(image).rectangle((30, 30, 35, 70), fill="black")
        image.save(base)
    with Image.open(source) as image:
        ImageDraw.Draw(image).rectangle((60, 20, 65, 80), fill="black")
        image.save(source)
    output = tmp_path / "multiply.png"
    render_plane_composite(base, {"source": source}, _request(_layer(mode="multiply")), output_path=output)
    with Image.open(output) as result:
        assert result.getpixel((45, 50)) == (90, 120, 150, 255)
        assert result.getpixel((32, 50)) == (0, 0, 0, 255)  # Old ink stays: not an eraser.
        assert result.getpixel((57, 50)) == (0, 0, 0, 255)


def test_transparent_source_does_not_paint_a_black_rectangle(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    image = Image.new("RGBA", (101, 101), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((40, 40, 60, 60), fill=(250, 20, 30, 255))
    image.save(source)
    output = tmp_path / "alpha.png"
    render_plane_composite(base, {"source": source}, _request(_layer()), output_path=output)
    with Image.open(output) as result:
        assert result.getpixel((50, 50)) == (250, 20, 30, 255)
        assert result.getpixel((30, 30)) == (90, 120, 150, 255)


def test_upright_coordinates_follow_exif_and_preserve_unedited_visible_base(tmp_path: Path) -> None:
    base, source = _sources(tmp_path, size=(80, 120))
    original = Image.new("RGB", (80, 120), "navy")
    ImageDraw.Draw(original).rectangle((0, 0, 20, 30), fill="orange")
    exif = original.getexif()
    exif[274] = 6
    original.save(base, exif=exif)
    output = tmp_path / "upright.png"
    assert render_plane_composite(base, {"source": source}, _request(_layer()), output_path=output) == (120, 80)
    with Image.open(base) as opened, Image.open(output) as result:
        upright = ImageOps.exif_transpose(opened).convert("RGBA")
        assert result.getpixel((110, 10)) == upright.getpixel((110, 10))
        assert result.getexif().get(274, 1) == 1


@pytest.mark.parametrize("points", [
    [(0, 0), (1, 1), (1, 0), (0, 1)],  # crossed
    [(0, 0), (.5, .5), (1, 1), (0, 1)],  # collinear/concave
    [(0, 0), (0, 1), (1, 1), (1, 0)],  # mirrored relative to default source
])
def test_invalid_plane_correspondence_is_rejected(points: list[tuple[float, float]]) -> None:
    with pytest.raises(ValueError):
        ImagePlaneLayer(source_asset_version_id="source", target_quad=_quad(points))


def test_geometry_uses_actual_dimensions_for_qa_and_render(tmp_path: Path) -> None:
    base, source = _sources(tmp_path)
    request = _request(ImagePlaneLayer(source_asset_version_id="source",
        target_quad=_quad([(.2, .2), (.201, .2), (.201, .201), (.2, .201)])))
    with pytest.raises(ValueError, match="actual pixel resolution"):
        validate_plane_composite_geometry(request, (101, 101), {"source": (101, 101)})
    with pytest.raises(ValueError, match="actual pixel resolution"):
        render_plane_composite(base, {"source": source}, request, output_path=tmp_path / "bad.png")
    assert not (tmp_path / "bad.png").exists()


def test_invalid_protection_missing_source_and_overwrite_fail_before_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="intersecting|nonzero"):
        ImagePlaneProtectedRegion(label="wrong", polygon=_quad([(0, 0), (1, 1), (1, 0), (0, 1)]))
    base, source = _sources(tmp_path)
    request = _request(_layer())
    original = source.read_bytes()
    with pytest.raises(ValueError, match="new file"):
        render_plane_composite(base, {"source": source}, request, output_path=source)
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="Missing actual source"):
        render_plane_composite(base, {}, request, output_path=tmp_path / "missing.png")
