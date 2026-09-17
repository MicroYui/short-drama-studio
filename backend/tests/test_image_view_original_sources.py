"""Exact image originals survive the ordinary Tool and Agent read envelopes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from PIL import Image
from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import ActionKind
from short_drama.agent_runtime.leader_context import persist_leader_tool_result
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.visual_input import prepare_worker_visual_input
from short_drama.image_models import AssetImportLocalRequest, AssetImportUrlRequest
from short_drama.models import ApprovalStatus, AssetMediaType, Freshness
from short_drama.production_manager import LanguageSpecialistAgent
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from short_drama.tools.image import ImageViewOutput
from tests.test_leader_read_context import working
from tests.test_short_drama_assets import FakeImageProvider, _service
from tests.test_worker_visual_observations import VisionClient, _save_and_reopen


def test_original_path_survives_exact_version_region_resize_and_both_agent_views(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = service.initialize_fixture().document.assets[0]
    source = tmp_path / "original.png"
    Image.new("RGB", (3000, 2400), "#506070").save(source)
    version = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(source), descriptor="Exact original")).version
    original_path = Path(cast(str, version.local_path))
    original_bytes = original_path.read_bytes()
    Image.new("RGB", (64, 64), "#ee1010").save(source)
    newer = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(source), descriptor="Different current image")).version
    document = service.get_project(asset.project_id).document
    selected_asset = next(item for item in document.assets if item.id == asset.id)
    selected_asset.current_version_id = newer.id
    selected_version = next(item for item in selected_asset.versions if item.id == version.id)
    selected_version.approval_status = ApprovalStatus.REJECTED
    selected_version.freshness = Freshness.STALE
    service.repository.save_project(document)
    arguments: dict[str, JsonValue] = {"project_id": asset.project_id, "purpose": "Observe an exact old original and region", "images": [
        {"asset_version_id": version.id},
        {"asset_version_id": version.id, "region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}},
    ]}
    result = service.tool_registry.invoke("image.view", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    originals = result.output["original_sources"]
    assert originals == [{"artifact_ref": {"project_id": asset.project_id, "artifact_type": "asset_version",
        "artifact_id": version.id, "version": version.version}, "asset_id": asset.id,
        "source": {"local_path": str(original_path)}, "approval_status": "REJECTED", "freshness": "STALE"}] * 2
    reopened, view = _save_and_reopen(service, asset.project_id, result, arguments)
    client = VisionClient()
    agent = LanguageSpecialistAgent(client, reopened.tool_registry)
    agent.context_compiler = ModelInputCompiler(reopened.agent_runtime.store)
    agent.execute(view)
    payload = json.loads(client.inputs[-1])
    assert payload["working_set"]["latest_handoff"]["result"]["output"]["original_sources"] == originals
    full, region = payload["image_observations"]["images"]
    assert full["observed_dimensions"] == [2048, 1638]
    assert region["observed_dimensions"] == [1500, 1200]
    assert region["source_pixel_box"] == [750, 600, 2250, 1800]
    assert len(client.images[-1]) == 2
    # Leader READ uses the same compact projection, reconstructed from SQLite.
    runtime = reopened.production_manager.runtime
    run = runtime.create_run(project_id=asset.project_id, objective="Use the exact observed original", root_agent="leader")
    action = runtime.begin_action(run.id, kind=ActionKind.TOOL_CALL, agent_id="leader",
        tool_name="image.view", input_summary="Observe exact image originals")
    persist_leader_tool_result(runtime.store, action, arguments=arguments,
        result=cast(dict[str, JsonValue], result.model_dump(mode="json")))
    runtime.complete_action(action.id, output_summary="Observed; not approved")
    reopened_again = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    leader = working(reopened_again, run.id, asset.project_id)
    observations = cast(list[dict[str, JsonValue]], leader["read_observations"])
    leader_result = cast(dict[str, JsonValue], observations[0]["result"])
    assert cast(dict[str, JsonValue], leader_result["output"])["original_sources"] == originals
    assert reopened_again.get_project(asset.project_id).document == document
    assert original_path.read_bytes() == original_bytes
    assert provider.calls == []


@pytest.mark.parametrize("missing", ["version", "file", "not_image"])
def test_missing_selection_returns_no_success_or_original_source(tmp_path: Path, missing: str) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = service.initialize_fixture().document.assets[0]
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "blue").save(source)
    version = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(source), descriptor="Original")).version
    if missing == "file":
        Path(cast(str, version.local_path)).unlink()
    elif missing == "not_image":
        document = service.get_project(asset.project_id).document
        saved_asset = next(item for item in document.assets if item.id == asset.id)
        next(item for item in saved_asset.versions if item.id == version.id).media_type = AssetMediaType.VIDEO
        service.repository.save_project(document)
    arguments = {"project_id": asset.project_id, "purpose": "Read unavailable image", "images": [
        {"asset_version_id": "missing-version" if missing == "version" else version.id}]}
    before = service.get_project(asset.project_id).document
    result = service.tool_registry.invoke("image.view", arguments)
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert result.output is None and result.visual_attachments == []
    assert service.get_project(asset.project_id).document == before
    assert provider.calls == []


def test_external_original_preserves_existing_url_delivery_without_local_file(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = service.initialize_fixture().document.assets[0]
    url = "https://example.test/exact-original.png"
    version = service.import_url_asset_version(asset.project_id, asset.id,
        AssetImportUrlRequest(external_url=url, descriptor="Exact remote original")).version
    arguments = {"project_id": asset.project_id, "purpose": "View remote original", "images": [{"asset_version_id": version.id}]}
    before = service.get_project(asset.project_id).document
    result = service.tool_registry.invoke("image.view", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    originals = cast(list[dict[str, JsonValue]], result.output["original_sources"])
    assert originals[0]["source"] == {"external_url": url}
    assert cast(dict[str, JsonValue], originals[0]["artifact_ref"])["artifact_id"] == version.id
    _reopened, view = _save_and_reopen(service, asset.project_id, result, arguments)
    actual = prepare_worker_visual_input(view.work_order, project_id=asset.project_id)
    assert actual is not None and actual.image_urls == (url,)
    assert service.get_project(asset.project_id).document == before
    assert provider.calls == []


def test_legacy_output_remains_readable_without_inferred_sources() -> None:
    output = ImageViewOutput.model_validate({"images": [], "purpose": "Historical view"})
    assert output.original_sources == []
