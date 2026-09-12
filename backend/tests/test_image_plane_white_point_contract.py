"""The optional paper tone survives real Tool storage and the existing QA request."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from short_drama.asset_inspection import SemanticAssetReview
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest, ImagePlaneLayer
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_plane_composite_suggestion import _setup


def _layer(**extra: object) -> dict[str, object]:
    return {
        "source_asset_version_id": "observed-photographic-source",
        "target_quad": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1},
                        {"x": 0.9, "y": 0.9}, {"x": 0.1, "y": 0.9}],
        "blend_mode": "multiply", **extra,
    }


def test_legacy_plane_layer_does_not_choose_a_paper_tone() -> None:
    layer = ImagePlaneLayer.model_validate(_layer())
    assert layer.source_white_point is None
    assert "source_white_point" not in layer.model_fields_set


@pytest.mark.parametrize("white_point", [None, 1, 220, 235, 255])
def test_explicit_paper_tone_round_trips_without_becoming_a_default(white_point: int | None) -> None:
    request = AssetImagePlaneCompositeRequest.model_validate({
        "base_asset_version_id": "observed-base", "descriptor": "Use the observed paper tone.",
        "layers": [_layer(source_white_point=white_point), _layer()],
    })
    restored = AssetImagePlaneCompositeRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.layers[0].source_white_point == white_point
    assert restored.layers[1].source_white_point is None
    review = SemanticAssetReview.model_validate({"recommended": False, "score": 2,
        "issues": [], "summary": "Optional local repair; not adopted.",
        "plane_composite_suggestion": restored.model_dump(mode="json")})
    assert review.plane_composite_suggestion == restored


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 256, 220.0, "220"])
def test_paper_tone_rejects_non_integer_or_out_of_range_values(invalid: object) -> None:
    with pytest.raises(ValidationError) as error:
        ImagePlaneLayer.model_validate(_layer(source_white_point=invalid))
    assert error.value.errors()[0]["loc"] == ("source_white_point",)


def test_bad_optional_paper_tone_does_not_discard_the_actual_visual_review() -> None:
    review = SemanticAssetReview.model_validate({"recommended": False, "score": 2,
        "issues": [], "summary": "The paper background still mismatches the lit base.",
        "plane_composite_suggestion": {"base_asset_version_id": "observed-base",
            "descriptor": "Malformed optional repair.", "layers": [_layer(source_white_point=True)]}})
    assert review.plane_composite_suggestion is None
    assert review.recommended is False and review.score == 2
    assert review.summary == "The paper background still mismatches the lit base."


def test_actual_tool_schema_and_restart_qa_keep_the_selected_layer_tone(tmp_path: Path) -> None:
    service, language, pid, aid, base, source = _setup(tmp_path)
    schema: dict[str, Any] = service.tool_registry.describe("image.composite_plane").input_schema
    layer_schema = schema["$defs"]["ImagePlaneLayer"]
    field = layer_schema["properties"]["source_white_point"]
    assert field["default"] is None
    assert field["anyOf"] == [{"maximum": 255, "minimum": 1, "type": "integer"}, {"type": "null"}]
    assert "source_white_point" not in layer_schema["required"]
    request = AssetImagePlaneCompositeRequest.model_validate({
        "base_asset_version_id": base.id, "descriptor": "Observed paper gray; keep source handwriting.",
        "layers": [_layer(source_asset_version_id=source.id, source_white_point=220)],
    })
    before = service.get_project(pid).document
    composed = service.tool_registry.invoke("image.composite_plane", {
        "project_id": pid, "request": request.model_dump(mode="json")})
    assert composed.status == ToolCallStatus.SUCCEEDED, composed.error
    assert composed.output is not None
    candidate_id = str(composed.output["version"]["id"])
    assert language.calls == 0 and service.list_image_tasks(pid).tasks == []

    reopened = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    document = reopened.get_project(pid).document
    owner = next(asset for asset in document.assets if asset.id == aid)
    old_owner = next(asset for asset in before.assets if asset.id == aid)
    candidate = next(version for version in owner.versions if version.id == candidate_id)
    assert candidate.source is not None
    assert AssetImagePlaneCompositeRequest.model_validate_json(candidate.source.prompt or "") == request
    assert owner.current_version_id == old_owner.current_version_id
    assert owner.approved_version_id == old_owner.approved_version_id
    assert document.shots == before.shots
    inspected = reopened.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": aid, "version_id": candidate_id,
        "request": {"canonical_reference_version_ids": [source.id]}})
    assert inspected.status == ToolCallStatus.SUCCEEDED, inspected.error
    payload = json.loads(language.input_text)
    assert json.loads(payload["candidate_creation"]["generation_prompt"]) == request.model_dump(mode="json")
    # QA uses the same nested request schema; no parallel paper-tone metadata is needed.
    qa_field = payload["output_schema"]["$defs"]["ImagePlaneLayer"]["properties"]["source_white_point"]
    assert qa_field == field
    assert language.calls == 1 and reopened.list_image_tasks(pid).tasks == []
