"""Optional paper transfer keeps observed ink and confines old-print cleanup."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from short_drama.asset_inspection import SemanticAssetReview
from short_drama.image_paper import clean_target_paper, normalize_paper_ink
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest, validate_plane_composite_geometry, render_plane_composite
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_plane_composite_suggestion import _setup


def _quad(x0: float, y0: float, x1: float, y1: float) -> list[dict[str, float]]:
    return [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]


def _request() -> dict:
    return {"base_asset_version_id": "base", "descriptor": "Observed paper content only",
        "layers": [{"source_asset_version_id": "source", "target_quad": _quad(.1, .1, .9, .9),
            "blend_mode": "multiply", "paper_ink": {
                "blank_sample": {"x": .2, "y": .1, "width": .15, "height": .1}, "background_window_px": 31}}]}


def test_local_illumination_removal_preserves_ink_positions_red_strokes_and_alpha() -> None:
    rgba = np.full((101, 101, 4), 255, dtype=np.uint8)
    # Source photographic paper has a horizontal lighting gradient.
    rgba[:, :, :3] = np.linspace(175, 230, 101, dtype=np.uint8)[None, :, None]
    rgba[40:70, 40:43, :3] = 10
    rgba[50:53, 60:80, :3] = [170, 25, 20]
    rgba[80:, 90:, 3] = 80
    original = Image.fromarray(rgba)
    with normalize_paper_ink(original, (20, 10, 35, 20), 31) as normalized:
        result = np.array(normalized)
    assert np.array_equal(np.array(original), rgba)
    assert np.array_equal(result[:, :, 3], rgba[:, :, 3])
    assert result[50, 20, :3].min() >= 253
    assert result[50, 85, :3].min() >= 253
    assert result[45:65, 40:43, :3].max() < 20  # Original black stroke stays dark and in place.
    assert result[51, 70, 0] > 180 and result[51, 70, 1:3].max() < 40


def test_cleanup_only_restores_selected_print_with_original_alpha_and_protected_pixels() -> None:
    base = Image.new("RGBA", (101, 101), (210, 220, 230, 255))
    draw = ImageDraw.Draw(base)
    draw.line((15, 45, 85, 45), fill=(20, 20, 20, 255), width=2)
    draw.rectangle((48, 35, 54, 55), fill=(145, 95, 70, 255))  # Foreground.
    draw.rectangle((70, 30, 75, 60), fill=(0, 0, 0, 90))
    selected = Image.new("L", base.size)
    ImageDraw.Draw(selected).rectangle((30, 40, 80, 50), fill=255)
    ImageDraw.Draw(selected).rectangle((48, 35, 54, 55), fill=0)
    before = np.array(base)
    with clean_target_paper(base, selected, 13) as cleaned:
        result = np.array(cleaned)
    writable = (np.array(selected) > 0) & (before[:, :, 3] == 255)
    assert np.array_equal(result[~writable], before[~writable])
    assert np.array_equal(result[:, :, 3], before[:, :, 3])
    assert result[45, 35, :3].min() > 195
    assert np.array_equal(result[35, 35], before[35, 35])
    assert np.array_equal(np.array(base), before)


def test_paper_renderer_confines_cleanup_and_warp_to_planes_minus_protection(tmp_path: Path) -> None:
    base_path, source_path = tmp_path / "base.png", tmp_path / "source.png"
    with Image.new("RGBA", (101, 101), (210, 220, 230, 255)) as base:
        ImageDraw.Draw(base).line((0, 45, 100, 45), fill="black", width=2)
        base.save(base_path)
    with Image.new("RGBA", (101, 101), (205, 205, 205, 255)) as source:
        ImageDraw.Draw(source).line((20, 60, 80, 60), fill="red", width=2)
        source.save(source_path)
    raw = _request()
    raw["protected_regions"] = [{"label": "observed foreground", "polygon": _quad(.45, .2, .55, .8)}]
    raw["target_paper_cleanup"] = {"regions": [{"label": "old line", "polygon": _quad(0, .4, 1, .5)}]}
    request = AssetImagePlaneCompositeRequest.model_validate(raw)
    old_files = (base_path.read_bytes(), source_path.read_bytes())
    output = tmp_path / "new.png"
    render_plane_composite(base_path, {"source": source_path}, request, output_path=output)
    with Image.open(base_path) as base, Image.open(output) as actual:
        before, result = np.array(base), np.array(actual)
    for x in (0, 9, 45, 50, 55, 91, 100):
        assert np.array_equal(before[:, x], result[:, x])
    assert result[45, 25, :3].min() > 195  # Old target ink gone.
    assert result[59, 35, 0] > 190 and result[59, 35, 1] < 100  # Original 2px red ink, area-sampled at 0.8x.
    assert result[30, 25, :3].tolist() == [210, 220, 230]  # No gray source rectangle.
    assert old_files == (base_path.read_bytes(), source_path.read_bytes())


@pytest.mark.parametrize("mutation", ["replace", "white_point", "even_source", "even_target", "missing_paper", "collapsed_cleanup"])
def test_incoherent_paper_requests_reject_before_render(mutation: str) -> None:
    raw = _request()
    layer = raw["layers"][0]
    if mutation == "replace":
        layer["blend_mode"] = "replace"
    elif mutation == "white_point":
        layer["source_white_point"] = 230
    elif mutation == "even_source":
        layer["paper_ink"]["background_window_px"] = 30
    else:
        raw["target_paper_cleanup"] = {"regions": [{"label": "old print", "polygon": _quad(.2, .2, .4, .4)}]}
        if mutation == "even_target":
            raw["target_paper_cleanup"]["background_window_px"] = 12
        elif mutation == "missing_paper":
            layer.pop("paper_ink")
        else:
            raw["target_paper_cleanup"]["regions"][0]["polygon"] = _quad(.2, .2, .20001, .4)
    with pytest.raises(ValueError):
        request = AssetImagePlaneCompositeRequest.model_validate(raw)
        validate_plane_composite_geometry(request, (101, 101), {"source": (101, 101)})


@pytest.mark.parametrize("problem", ["window", "sample_size", "sample_alpha", "cleanup_outside"])
def test_actual_image_prerequisites_fail_without_output(tmp_path: Path, problem: str) -> None:
    raw = _request()
    base, source = tmp_path / "base.png", tmp_path / "source.png"
    Image.new("RGBA", (101, 101), "white").save(base)
    Image.new("RGBA", (101, 101), "white").save(source)
    if problem == "window":
        raw["layers"][0]["paper_ink"]["background_window_px"] = 103
    elif problem == "sample_size":
        raw["layers"][0]["paper_ink"]["blank_sample"].update(width=.0001, height=.0001)
    elif problem == "sample_alpha":
        Image.new("RGBA", (101, 101), (100, 100, 100, 20)).save(source)
    else:
        raw["target_paper_cleanup"] = {"regions": [{"label": "outside", "polygon": _quad(0, 0, .05, .05)}]}
    output = tmp_path / "bad.png"
    with pytest.raises(ValueError):
        render_plane_composite(base, {"source": source}, AssetImagePlaneCompositeRequest.model_validate(raw), output_path=output)
    assert not output.exists()


def test_real_tool_persists_optional_processing_and_reopened_qa_observes_same_schema(tmp_path: Path) -> None:
    service, language, pid, aid, base, source = _setup(tmp_path)
    raw = _request()
    raw["base_asset_version_id"] = base.id
    raw["layers"][0]["source_asset_version_id"] = source.id
    raw["layers"][0]["paper_ink"]["blank_sample"] = {"x": .2, "y": .2, "width": .2, "height": .1}
    raw["target_paper_cleanup"] = {"regions": [{"label": "observed blank target", "polygon": _quad(.2, .2, .4, .4)}]}
    request = AssetImagePlaneCompositeRequest.model_validate(raw)
    before = service.get_project(pid).document
    call = service.tool_registry.invoke("image.composite_plane", {"project_id": pid, "request": raw})
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    assert call.output is not None
    candidate_id = str(call.output["version"]["id"])
    reopened = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    document = reopened.get_project(pid).document
    old_owner = next(a for a in before.assets if a.id == aid)
    owner = next(a for a in document.assets if a.id == aid)
    candidate = next(v for v in owner.versions if v.id == candidate_id)
    assert candidate.source is not None
    assert AssetImagePlaneCompositeRequest.model_validate_json(candidate.source.prompt or "") == request
    assert owner.current_version_id == old_owner.current_version_id
    assert owner.approved_version_id == old_owner.approved_version_id
    assert document.shots == before.shots
    assert language.calls == 0 and reopened.list_image_tasks(pid).tasks == []
    review = SemanticAssetReview.model_validate({"recommended": False, "score": 2,
        "issues": [], "summary": "Optional suggestion", "plane_composite_suggestion": raw})
    assert review.plane_composite_suggestion == request
    inspected = reopened.tool_registry.invoke("qa.inspect_asset_version", {"project_id": pid,
        "asset_id": aid, "version_id": candidate_id, "request": {"canonical_reference_version_ids": [source.id]}})
    assert inspected.status == ToolCallStatus.SUCCEEDED, inspected.error
    payload = json.loads(language.input_text)
    assert json.loads(payload["candidate_creation"]["generation_prompt"]) == request.model_dump(mode="json")
    schema = service.tool_registry.describe("image.composite_plane").input_schema
    for model in ("ImagePlanePaperInk", "ImagePlanePaperCleanup"):
        # Model input omits decorative titles; actual fields, constraints,
        # descriptions and defaults must still match the executable Tool.
        contract = json.loads(json.dumps(schema["$defs"][model]),
            object_hook=lambda value: {key: child for key, child in value.items() if key != "title"})
        assert payload["output_schema"]["$defs"][model] == contract
    assert language.calls == 1 and reopened.list_image_tasks(pid).tasks == []
