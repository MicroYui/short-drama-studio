"""Local perspective edits are saved and handed off without a Provider or adoption."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any

import pytest
from PIL import Image, ImageDraw

from short_drama import asset_workbench as workbench_module
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest, ImageOperation, ImageTaskCreateRequest
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest
from short_drama.models import ApprovalStatus, AssetKind, Freshness
from short_drama.tools import ToolCallStatus
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


def _fixture(tmp_path: Path):
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    target = _fixture_asset(service, "asset-linxia")
    base_path = tmp_path / "base.png"
    Image.new("RGB", (160, 120), (41, 67, 89)).save(base_path)
    base = service.import_local_asset_version(target.project_id, target.id,
        AssetImportLocalRequest(local_path=str(base_path), descriptor="actual base photograph")).version
    source_asset = service.create_asset(target.project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name="exact surface", descriptor="isolated printed content")).asset
    source_path = tmp_path / "surface.png"
    source_image = Image.new("RGB", (40, 60), "white")
    draw = ImageDraw.Draw(source_image)
    for box, color in [((0, 0, 19, 29), "red"), ((20, 0, 39, 29), "green"),
                       ((20, 30, 39, 59), "blue"), ((0, 30, 19, 59), "yellow")]:
        draw.rectangle(box, fill=color)
    source_image.save(source_path)
    source = service.import_local_asset_version(target.project_id, source_asset.id,
        AssetImportLocalRequest(local_path=str(source_path), descriptor="four identifiable page corners")).version
    request = {"base_asset_version_id": base.id, "descriptor": "same page rotated clockwise on the observed plane",
        "layers": [{"source_asset_version_id": source.id, "target_quad": [
            {"x": .8, "y": .2}, {"x": .8, "y": .8}, {"x": .2, "y": .8}, {"x": .2, "y": .2}]}],
        "protected_regions": [{"label": "observed foreground finger", "polygon": [
            {"x": .45, "y": .3}, {"x": .55, "y": .3}, {"x": .55, "y": .7}, {"x": .45, "y": .7}]}]}
    return service, provider, target, base, source, request


def test_real_tool_saves_rotated_pixels_exact_sources_and_receipt_without_adoption(tmp_path: Path) -> None:
    service, provider, target, base, source, request = _fixture(tmp_path)
    before = service.get_project(target.project_id).document
    original = Path(base.local_path or "").read_bytes()
    call = service.tool_registry.invoke("image.composite_plane", {"project_id": target.project_id, "request": request})
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    assert call.receipt is not None
    state = call.receipt.current_state
    version_id = str(state["version.id"])
    after = service.get_project(target.project_id).document
    saved_asset = next(a for a in after.assets if a.id == target.id)
    old_asset = next(a for a in before.assets if a.id == target.id)
    version = next(v for v in saved_asset.versions if v.id == version_id)
    assert saved_asset.current_version_id == old_asset.current_version_id == base.id
    assert saved_asset.approved_version_id == old_asset.approved_version_id
    assert saved_asset.versions[:-1] == old_asset.versions
    assert after.shots == before.shots and after.reference_packs == before.reference_packs
    assert version.parent_version_id == base.id
    assert version.source is not None and version.source.model == "pillow-plane-composite-v1"
    assert AssetImagePlaneCompositeRequest.model_validate_json(version.source.prompt or "") == AssetImagePlaneCompositeRequest.model_validate(request)
    assert [r.asset_version_id for r in version.derivation_references] == [base.id, source.id]
    assert all(r.source_project_id == target.project_id for r in version.derivation_references)
    assert state["base_asset_version_id"] == base.id and state["source_asset_version_ids"] == [source.id]
    assert state["visual_acceptance"] == "NOT_ASSESSED"
    assert (state["version.width"], state["version.height"]) == (160, 120)
    assert next(r for r in call.artifact_refs if r.artifact_id == version_id).version == version.version
    with Image.open(version.local_path or "") as opened:
        pixels = opened.convert("RGB")
        assert pixels.getpixel((112, 36)) == (255, 0, 0)
        assert pixels.getpixel((112, 84)) == (0, 128, 0)
        assert pixels.getpixel((48, 84)) == (0, 0, 255)
        assert pixels.getpixel((48, 36)) == (255, 255, 0)
        assert pixels.getpixel((80, 48)) == (41, 67, 89)
        assert pixels.getpixel((10, 60)) == (41, 67, 89)
    assert Path(base.local_path or "").read_bytes() == original
    assert provider.calls == [] and service.list_image_tasks(target.project_id).tasks == []
    reopened = _service(tmp_path, provider).get_project(target.project_id).document
    assert next(a for a in reopened.assets if a.id == target.id).versions[-1] == version


@pytest.mark.parametrize("failure", ["missing_base", "missing_surface", "rejected", "stale"])
def test_unusable_exact_source_never_creates_candidate(tmp_path: Path, failure: str) -> None:
    service, provider, target, base, source, request = _fixture(tmp_path)
    if failure.startswith("missing"):
        Path((base if failure == "missing_base" else source).local_path or "").unlink()
    else:
        document = service.get_project(target.project_id).document
        version = next(v for a in document.assets for v in a.versions if v.id == source.id)
        if failure == "rejected":
            version.approval_status = ApprovalStatus.REJECTED
        else:
            version.freshness = Freshness.STALE
        service.repository.save_project(document)
    before = service.get_project(target.project_id).document
    call = service.tool_registry.invoke("image.composite_plane", {"project_id": target.project_id, "request": request})
    assert call.status == ToolCallStatus.FAILED
    assert (base.id if failure == "missing_base" else source.id) in str(call.error)
    assert service.get_project(target.project_id).document == before
    assert not list(Path(base.local_path or "").parent.glob("*plane.png"))
    assert provider.calls == []


@pytest.mark.parametrize("source_changed", [False, True])
def test_slow_render_preserves_new_selection_and_allocates_after_reservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_changed: bool,
) -> None:
    service, provider, target, base, source, request = _fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_render = workbench_module.render_plane_composite

    def render(*args: Any, **kwargs: Any) -> tuple[int, int]:
        entered.set()
        assert release.wait(10)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(workbench_module, "render_plane_composite", render)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.tool_registry.invoke, "image.composite_plane", {
            "project_id": target.project_id, "request": request})
        assert entered.wait(10)
        try:
            reserved = service.enqueue_image_task(target.project_id, target.id, ImageTaskCreateRequest(
                operation=ImageOperation.GENERATE, prompt="independent candidate", descriptor="other work")).task
            path = tmp_path / "new-selection.png"
            Image.new("RGB", (48, 48), "orange").save(path)
            selection = service.import_local_asset_version(target.project_id, target.id,
                AssetImportLocalRequest(local_path=str(path), descriptor="user-selected image during local render")).version
            if source_changed:
                Image.new("RGB", (40, 60), "purple").save(source.local_path or "")
        finally:
            release.set()
        call = pending.result(timeout=10)
    saved = next(a for a in service.get_project(target.project_id).document.assets if a.id == target.id)
    assert saved.current_version_id == selection.id
    assert provider.calls == []
    if source_changed:
        assert call.status == ToolCallStatus.FAILED and source.id in str(call.error)
        assert not list(Path(base.local_path or "").parent.glob("*plane.png"))
    else:
        assert call.status == ToolCallStatus.SUCCEEDED, call.error
        assert call.receipt is not None
        composite = next(v for v in saved.versions if v.id == call.receipt.current_state["version.id"])
        assert composite.version > selection.version > reserved.output_version_number
        assert composite.parent_version_id == base.id
        assert len({v.version for v in saved.versions}) == len(saved.versions)
