"""Actual Tool validation and two reference paths on an existing image Asset."""

from copy import deepcopy
from pathlib import Path

from PIL import Image

from short_drama.image_models import AgentImageTaskCreateRequest, AssetImportLocalRequest, ImageTaskCreateRequest
from short_drama.tools import ToolCallStatus
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


def test_existing_asset_reference_generation_and_explicit_parent_edit(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-linxia")
    image = tmp_path / "identity.png"
    Image.new("RGB", (256, 384), "lightgray").save(image)
    version = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(image), descriptor="已有身份源图")).version
    before = service.get_project(asset.project_id).document.model_dump(mode="json")
    request = {
        "operation": "REFERENCE_GENERATE", "parent_version_id": version.id,
        "primary_input_version_id": version.id,
        "prompt": "使用已选身份制作补充近景，保留身份与服装。", "descriptor": "反应近景候选",
        "reference_inputs": [{"asset_version_id": version.id, "purpose": "实际身份参考"}],
    }
    unchanged = deepcopy(request)
    rejected = service.tool_registry.invoke("image.enqueue", {"project_id": asset.project_id,
        "asset_id": asset.id, "request": request})
    assert rejected.status == ToolCallStatus.INVALID_ARGUMENTS and rejected.error is not None
    assert rejected.error.details[0].path == "/request/parent_version_id"
    assert "operation='EDIT'" in rejected.error.message
    assert "existing Asset is still allowed" in rejected.error.message
    assert request == unchanged  # No silent conversion or parent deletion.
    assert service.list_image_tasks(asset.project_id).tasks == []
    assert service.get_project(asset.project_id).document.model_dump(mode="json") == before
    assert fake.calls == []

    for operation, parent in (("REFERENCE_GENERATE", None), ("EDIT", version.id)):
        valid = {**request, "operation": operation, "parent_version_id": parent}
        result = service.tool_registry.invoke("image.enqueue", {"project_id": asset.project_id,
            "asset_id": asset.id, "request": valid})
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        assert result.output is not None
        assert result.output["task"]["operation"] == operation
        assert result.output["task"]["parent_version_id"] == parent
        assert result.output["task"]["request"]["primary_input_version_id"] == version.id
    assert len(service.list_image_tasks(asset.project_id).tasks) == 2
    assert fake.calls == []  # Enqueue is not a Provider submission.


def test_both_discoverable_request_schemas_explain_operation_parent_mapping() -> None:
    for model in (ImageTaskCreateRequest, AgentImageTaskCreateRequest):
        properties = model.model_json_schema()["properties"]
        assert "existing Asset" in properties["operation"]["description"]
        assert "operation=EDIT" in properties["parent_version_id"]["description"]
