"""Perspective minification preserves fine ink and the existing pixel boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageChops, ImageDraw, ImageStat
import pytest

from short_drama.image_plane_composite import (
    AssetImagePlaneCompositeRequest, ImagePlaneLayer, ImagePlanePoint,
    ImagePlaneProtectedRegion, _coefficients, _plane_sampling_scale,
    render_plane_composite,
)


def _quad(points: list[tuple[float, float]]) -> list[ImagePlanePoint]:
    return [ImagePlanePoint(x=x, y=y) for x, y in points]


FULL = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def _request(layer: ImagePlaneLayer) -> AssetImagePlaneCompositeRequest:
    return AssetImagePlaneCompositeRequest(base_asset_version_id="base", layers=[layer], descriptor="Local paper sampling")


@pytest.mark.parametrize(("source_size", "target"), [
    ((65, 65), FULL), ((17, 17), FULL),
    ((33, 33), [(.25, .25), (.75, .25), (.75, .75), (.25, .75)]),
    ((17, 17), [(.25, .25), (.75, .25), (.75, .75), (.25, .75)]),
])
def test_identity_and_enlargement_keep_the_existing_bicubic_pixels(
    tmp_path: Path, source_size: tuple[int, int], target: list[tuple[float, float]],
) -> None:
    base, source, output = tmp_path / "base.png", tmp_path / "source.png", tmp_path / "out.png"
    Image.new("RGBA", (65, 65), (70, 90, 110, 255)).save(base)
    with Image.new("RGBA", source_size, (210, 220, 230, 255)) as image:
        ImageDraw.Draw(image).line((2, 3, source_size[0] - 3, source_size[1] - 2), fill=(10, 20, 30, 128), width=2)
        image.save(source)
    layer = ImagePlaneLayer(source_asset_version_id="source", target_quad=_quad(target))
    coefficients = _coefficients(layer, (65, 65), source_size)
    assert _plane_sampling_scale(coefficients, layer, (65, 65), (65, 65)) == 1
    render_plane_composite(base, {"source": source}, _request(layer), output_path=output)
    with Image.open(base) as bottom, Image.open(source) as ink, Image.open(output) as actual:
        with ink.transform(bottom.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC) as warped:
            mask = Image.new("L", bottom.size)
            ImageDraw.Draw(mask).polygon([(round(x * 64), round(y * 64)) for x, y in target], fill=255)
            warped.putalpha(ImageChops.multiply(warped.getchannel("A"), mask))
            with Image.alpha_composite(bottom, warped) as expected:
                assert actual.tobytes() == expected.tobytes()


def test_subpixel_rules_suppress_periodic_aliasing_and_preserve_average_coverage(tmp_path: Path) -> None:
    base, source, output = tmp_path / "base.png", tmp_path / "source.png", tmp_path / "out.png"
    Image.new("RGBA", (121, 121), "white").save(base)
    with Image.new("RGBA", (541, 541), "white") as image:
        draw = ImageDraw.Draw(image)
        for x in range(0, 541, 2):
            draw.line((x, 0, x, 540), fill="black")
        image.save(source)
    layer = ImagePlaneLayer(source_asset_version_id="source", target_quad=_quad(FULL))
    render_plane_composite(base, {"source": source}, _request(layer), output_path=output)
    with Image.open(source) as original, Image.open(output) as actual:
        with original.transform((121, 121), Image.Transform.PERSPECTIVE,
                _coefficients(layer, (121, 121), original.size), Image.Resampling.BICUBIC) as old:
            old_stats = ImageStat.Stat(old.crop((10, 10, 110, 110)).convert("L"))
        stats = ImageStat.Stat(actual.crop((10, 10, 110, 110)).convert("L"))
    assert old_stats.stddev[0] > 60
    # Bounded 4x sampling is not an exact area integral for 4.5x reduction:
    # residual modulation is allowed, but at least 75% of the old alias must go.
    assert stats.stddev[0] < old_stats.stddev[0] * .25
    assert 118 <= stats.mean[0] <= 137  # Equal black/white source coverage, not invented ink.


@pytest.mark.parametrize("mode", ["replace", "multiply"])
def test_downsampled_clipped_alpha_never_overbrightens_or_changes_protected_base(
    tmp_path: Path, mode: Literal["replace", "multiply"],
) -> None:
    base, source, output = tmp_path / "base.png", tmp_path / "source.png", tmp_path / "out.png"
    bottom = (90, 100, 110, 255)
    Image.new("RGBA", (301, 401), bottom).save(base)
    with Image.new("RGBA", (721, 1017), "magenta") as image:
        ImageDraw.Draw(image).rectangle((70, 75, 670, 940), fill=(220, 221, 222, 255))
        image.save(source)
    layer = ImagePlaneLayer(source_asset_version_id="source", blend_mode=mode,
        source_quad=_quad([(.12, .1), (.9, .11), (.91, .9), (.11, .91)]),
        target_quad=_quad([(.83, .77), (.4, .80), (.33, .61), (.66, .59)]))
    request = _request(layer)
    request.protected_regions = [ImagePlaneProtectedRegion(label="visible hand",
        polygon=_quad([(.5, .60), (.58, .60), (.58, .77), (.5, .77)]))]
    render_plane_composite(base, {"source": source}, request, output_path=output)
    permitted = Image.new("L", (301, 401))
    draw = ImageDraw.Draw(permitted)
    draw.polygon([(round(p.x * 300), round(p.y * 400)) for p in layer.target_quad], fill=255)
    draw.polygon([(round(p.x * 300), round(p.y * 400)) for p in request.protected_regions[0].polygon], fill=0)
    with Image.open(output) as actual:
        assert actual.getextrema()[0][1] <= 222
        assert actual.getextrema()[1][1] <= 223
        assert actual.getextrema()[2][1] <= 224
        assert all(actual.getpixel((x, y)) == bottom for y in range(401) for x in range(301)
                   if permitted.getpixel((x, y)) == 0)


def test_sampling_allocations_are_local_and_large_roi_limit_does_not_reject_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, source, output = tmp_path / "base.png", tmp_path / "source.png", tmp_path / "out.png"
    Image.new("RGBA", (1001, 1001), "gray").save(base)
    Image.new("RGBA", (1001, 1001), "white").save(source)
    layer = ImagePlaneLayer(source_asset_version_id="source", target_quad=_quad([(.4, .4), (.6, .4), (.6, .6), (.4, .6)]))
    allocations: list[tuple[int, int]] = []
    original = Image.Image.transform

    def record(image: Image.Image, size: tuple[int, int], *args: Any, **kwargs: Any) -> Image.Image:
        allocations.append(size)
        return original(image, size, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "transform", record)
    render_plane_composite(base, {"source": source}, _request(layer), output_path=output)
    assert allocations
    assert max(w * h for w, h in allocations) < 1_000_000
    coefficients = _coefficients(layer, (1001, 1001), (1001, 1001))
    assert _plane_sampling_scale(coefficients, layer, (1001, 1001), (1400, 1400)) == 2
    assert _plane_sampling_scale(coefficients, layer, (1001, 1001), (3500, 3500)) == 1
    assert output.is_file()


@pytest.mark.parametrize("mode", ["replace", "multiply"])
def test_explicit_white_point_changes_only_rgb_before_compositing(
    tmp_path: Path, mode: Literal["replace", "multiply"],
) -> None:
    base, source = tmp_path / "base.png", tmp_path / "source.png"
    Image.new("RGBA", (33, 33), (180, 170, 160, 255)).save(base)
    with Image.new("RGBA", (33, 33), (220, 221, 222, 255)) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 4, 10, 10), fill=(110, 20, 30, 128))
        draw.rectangle((20, 20, 25, 25), fill=(30, 40, 50, 0))
        image.save(source)
    original = source.read_bytes()
    images: dict[int | None, bytes] = {}
    for point in (None, 255, 220):
        output = tmp_path / f"wp-{point}.png"
        layer = ImagePlaneLayer(source_asset_version_id="source", blend_mode=mode,
            target_quad=_quad(FULL), source_white_point=point)
        render_plane_composite(base, {"source": source}, _request(layer), output_path=output)
        with Image.open(output) as actual:
            images[point] = actual.tobytes()
            if point == 220:
                assert actual.getpixel((22, 22)) == (180, 170, 160, 255)
                assert actual.getpixel((15, 15)) == ((255, 255, 255, 255) if mode == "replace" else (180, 170, 160, 255))
                corrected = Image.new("RGBA", (1, 1), (128, 23, 35, 128))
                # The exact LUT value is 128/23/35. Existing Pillow RGBA
                # interpolation premultiplies/unpremultiplies even at 1:1,
                # quantizing red to 127; compare to that same sampling contract.
                pixel = corrected.transform(corrected.size, Image.Transform.PERSPECTIVE,
                    (1, 0, 0, 0, 1, 0, 0, 0), Image.Resampling.BICUBIC)
                assert pixel.getchannel("A").getpixel((0, 0)) == 128
                bottom = Image.new("RGBA", (1, 1), (180, 170, 160, 255))
                if mode == "multiply":
                    ink = ImageChops.multiply(bottom.convert("RGB"), pixel.convert("RGB")).convert("RGBA")
                    expected = Image.composite(ink, bottom, pixel.getchannel("A"))
                else:
                    expected = Image.alpha_composite(bottom, pixel)
                assert actual.getpixel((6, 6)) == expected.getpixel((0, 0))
    assert images[None] == images[255]
    assert images[220] != images[None]
    assert source.read_bytes() == original
