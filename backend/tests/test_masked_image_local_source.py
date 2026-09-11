"""Masked edits upload the same saved pixels used for local mask compositing."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from short_drama.asset_workbench import AssetWorkbenchConflict
from short_drama.image_models import AssetImportLocalRequest, ImageOperation, ImageTaskCreateRequest
from tests.test_openai_image_client import FakeResponse, FakeSession, _client, _success_payload
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


def _queued_inputs(tmp_path: Path, *, masked: bool):
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    versions = []
    colors = [(18, 52, 86, 255), (240, 210, 10, 255), (255, 255, 255, 0)]
    for index, color in enumerate(colors):
        path = tmp_path / f"local-{index}.png"
        with Image.new("RGBA", (768, 1344), color) as image:
            image.save(path)
        version = service.import_local_asset_version(asset.project_id, asset.id,
            AssetImportLocalRequest(local_path=str(path), descriptor=f"Saved local input {index}")).version
        versions.append(version)
    document = service.get_project(asset.project_id).document
    saved_asset = next(item for item in document.assets if item.id == asset.id)
    for index, version in enumerate(versions):
        saved_version = next(item for item in saved_asset.versions if item.id == version.id)
        # These URLs intentionally name different remote content. No HTTP is allowed here.
        saved_version.external_url = f"https://remote.example.invalid/different-{index}.png"
    service.repository.save_project(document)
    source, guide, mask = versions
    task = service.enqueue_image_task(asset.project_id, asset.id, ImageTaskCreateRequest(
        operation=ImageOperation.EDIT, parent_version_id=source.id,
        primary_input_version_id=source.id, input_version_ids=[source.id, guide.id],
        mask_version_id=mask.id if masked else None,
        prompt="Edit the observed local candidate using the guide.", descriptor="Local edit",
    )).task
    return service, provider, task, versions, colors


def test_masked_local_inputs_reach_actual_multipart_without_remote_download(tmp_path: Path) -> None:
    service, provider, task, versions, colors = _queued_inputs(tmp_path, masked=True)
    inputs = service.asset_workbench._resolve_provider_inputs(task)
    assert [item.asset_version_id for item in inputs] == [item.id for item in versions]
    assert all(item.image_url.startswith("data:image/png;base64,") for item in inputs)
    session = FakeSession()
    session.responses.append(FakeResponse(200, _success_payload()))
    _client(session).create(task.request, inputs)
    assert [method for method, _url, _arguments in session.calls] == ["POST"]
    files = session.calls[0][2]["files"]
    assert [item[0] for item in files] == ["image[]", "image[]", "mask"]
    for file, expected in zip(files, colors, strict=True):
        with Image.open(BytesIO(file[1][1])) as image:
            assert image.size == (768, 1344)
            assert image.convert("RGBA").getpixel((300, 500)) == expected
    assert provider.calls == []


def test_unmasked_input_keeps_existing_remote_priority(tmp_path: Path) -> None:
    service, provider, task, _versions, _colors = _queued_inputs(tmp_path, masked=False)
    inputs = service.asset_workbench._resolve_provider_inputs(task)
    assert [item.image_url for item in inputs] == [
        "https://remote.example.invalid/different-0.png",
        "https://remote.example.invalid/different-1.png",
    ]
    assert provider.calls == []


@pytest.mark.parametrize("missing_index", [0, 1, 2])
def test_masked_missing_local_input_never_falls_back_to_remote(tmp_path: Path, missing_index: int) -> None:
    service, provider, task, versions, _colors = _queued_inputs(tmp_path, masked=True)
    missing = versions[missing_index]
    assert missing.local_path is not None
    Path(missing.local_path).unlink()
    with pytest.raises(AssetWorkbenchConflict, match=missing.id):
        service.asset_workbench._resolve_provider_inputs(task)
    assert provider.calls == []
