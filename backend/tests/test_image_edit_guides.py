"""Real local guide pixels feed the existing masked Provider edit path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image

from short_drama.image_models import AssetImportLocalRequest
from short_drama.tools import ToolCallStatus
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


def _request(source_id: str) -> dict[str, Any]:
    return {"source_asset_version_id": source_id, "descriptor": "纸张局部修改，不重画人物",
        "regions": [{"polygon": [{"x": .25, "y": .25}, {"x": .75, "y": .25},
            {"x": .75, "y": .75}, {"x": .25, "y": .75}], "label": "仅改变纸张朝向"}],
        "arrows": [{"start": {"x": .5, "y": .5}, "end": {"x": .25, "y": .5}, "label": "标题边朝左"}]}


def test_guide_mask_and_exact_receipt_feed_real_edit_without_replacing_source(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-linxia")
    path = tmp_path / "source.png"
    Image.new("RGB", (768, 1344), "#123456").save(path)
    source = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="局部修改前的照片")).version
    before = service.get_project(asset.project_id).document
    original = Path(source.local_path or "").read_bytes()
    call = service.tool_registry.invoke("image.create_edit_guide", {
        "project_id": asset.project_id, "request": _request(source.id)})
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    assert call.receipt is not None and call.output is not None
    state = call.receipt.current_state
    guide_id, mask_id = str(state["guide_asset_version_id"]), str(state["mask_asset_version_id"])
    after = service.get_project(asset.project_id).document
    saved = next(item for item in after.assets if item.id == asset.id)
    old = next(item for item in before.assets if item.id == asset.id)
    assert saved.current_version_id == old.current_version_id == source.id
    assert saved.approved_version_id == old.approved_version_id
    assert len(saved.versions) == len(old.versions) + 2
    assert saved.versions[:-2] == old.versions
    assert after.shots == before.shots and after.reference_packs == before.reference_packs
    assert fake.calls == [] and service.list_image_tasks(asset.project_id).tasks == []
    assert Path(source.local_path or "").read_bytes() == original
    assert {ref.artifact_id for ref in call.artifact_refs} == {guide_id, mask_id}
    guide, mask = saved.versions[-2:]
    assert guide.id == guide_id and mask.id == mask_id
    assert guide.source is not None and guide.source.model == "pillow-edit-guide-v1"
    assert mask.source is not None and mask.source.model == "pillow-edit-mask-v1"
    with Image.open(mask.local_path or "") as pixels:
        assert pixels.size == (768, 1344)
        assert pixels.getpixel((0, 0)) == (255, 255, 255, 255)
        assert pixels.getpixel((384, 672)) == (255, 255, 255, 0)
    with Image.open(guide.local_path or "") as pixels:
        assert pixels.size == (768, 1344)
        assert pixels.getpixel((0, 0)) == (18, 52, 86)
        assert pixels.getpixel((384, 672)) != (18, 52, 86)
    queued = service.tool_registry.invoke("image.enqueue", {"project_id": asset.project_id, "asset_id": asset.id,
        "request": {"operation": "EDIT", "parent_version_id": source.id, "primary_input_version_id": source.id,
            "prompt": "只改变遮罩内纸张朝向，遵守图示；不生成红框、标签和箭头。", "descriptor": "局部修图",
            "reference_inputs": [
                {"asset_version_id": source.id, "reference_role": "COMPOSITION", "purpose": "原图"},
                {"asset_version_id": guide_id, "reference_role": "LAYOUT_GUIDE", "purpose": "修改位置与朝向，不复制标注"}],
            "mask_version_id": mask_id, "mask_feather_pixels": 0, "size": "768x1344"}})
    assert queued.status == ToolCallStatus.SUCCEEDED, queued.error
    assert queued.output is not None
    task_id = str(cast(dict[str, Any], queued.output["task"])["id"])
    service.advance_image_task(task_id)
    finished = service.advance_image_task(task_id).task
    assert finished.status == "SUCCEEDED"
    assert len(fake.calls) == 1
    assert [item.asset_version_id for item in fake.calls[0][1]] == [source.id, guide_id, mask_id]
    with Image.open(finished.output_path or "") as pixels:
        assert pixels.convert("RGB").getpixel((0, 0)) == (18, 52, 86)
        assert pixels.convert("RGB").getpixel((191, 672)) == (18, 52, 86)
        assert pixels.convert("RGB").getpixel((192, 672)) == (127, 29, 29)
        assert pixels.convert("RGB").getpixel((384, 672)) == (127, 29, 29)
    assert Path(source.local_path or "").read_bytes() == original


@pytest.mark.parametrize("failure", ["missing", "rejected", "empty_polygon", "exif", "unaligned_crop", "pixel_line", "pixel_arrow"])
def test_unusable_source_or_region_never_saves_auxiliary_versions(tmp_path: Path, failure: str) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-linxia")
    path = tmp_path / "source.jpg"
    image = Image.new("RGB", (900, 1600) if failure == "unaligned_crop" else (768, 1344), "blue")
    exif = image.getexif()
    if failure == "exif":
        exif[274] = 6
    image.save(path, exif=exif)
    source = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="原图")).version
    document = service.get_project(asset.project_id).document
    if failure == "missing":
        Path(source.local_path or "").unlink()
    elif failure == "rejected":
        from short_drama.models import ApprovalStatus
        stored = next(item for item in document.assets if item.id == asset.id)
        next(v for v in stored.versions if v.id == source.id).approval_status = ApprovalStatus.REJECTED
        service.repository.save_project(document)
    request = _request(source.id)
    if failure == "empty_polygon":
        request["regions"][0]["polygon"] = [{"x": .1, "y": .1}] * 3
    elif failure == "pixel_line":
        request["regions"][0]["polygon"] = [{"x": .25, "y": .25}, {"x": .75, "y": .25}, {"x": .5, "y": .250001}]
    elif failure == "pixel_arrow":
        request["arrows"][0]["end"] = {"x": .500001, "y": .5}
    before = service.get_project(asset.project_id).document
    call = service.tool_registry.invoke("image.create_edit_guide", {"project_id": asset.project_id, "request": request})
    assert call.status in {ToolCallStatus.FAILED, ToolCallStatus.INVALID_ARGUMENTS}
    assert service.get_project(asset.project_id).document == before
    assert fake.calls == []
    assert not list(Path(source.local_path or "").parent.glob("*-edit-*.png"))
