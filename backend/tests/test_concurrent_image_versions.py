"""Persisted reservations and real local renders share one asset number space."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import threading
from typing import Any
import wave

import pytest
from PIL import Image

from short_drama import asset_workbench as workbench_module
from short_drama.asset_workbench import AssetWorkbench
from short_drama.change_executor import SemanticArtifactPatch, SemanticChangeApplyRequest
from short_drama.change_planner import ChangeTargetKind
from short_drama.image_models import (
    AssetCreateRequest, AssetImageCropRequest, AssetImportLocalRequest,
    AssetReferenceSheetComposeRequest, AssetTextPlateComposeRequest,
    ImageGenerationTask, ImageOperation, ImageRegion, ImageTaskCreateRequest, ImageTaskStatus,
)
from short_drama.models import (
    AssetKind, AssetPresentationMode, AssetVersion, CoreAssetBrief, ImageReferenceRole,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service
from tests.test_semantic_change_executor import _plan


def _enqueue(service: ShortDramaService, project_id: str, asset_id: str) -> ImageGenerationTask:
    return service.enqueue_image_task(project_id, asset_id, ImageTaskCreateRequest(
        operation=ImageOperation.GENERATE, prompt="An independent candidate.", descriptor="candidate",
    )).task


def _asset(service: ShortDramaService, project_id: str, asset_id: str):
    return next(a for a in service.get_project(project_id).document.assets if a.id == asset_id)


def test_parallel_tasks_reserve_distinct_numbers_and_do_not_hold_provider_lock(tmp_path: Path) -> None:
    entered = threading.Barrier(4)
    release = threading.Event()

    class HoldingProvider(FakeImageProvider):
        def create(self, request: Any, inputs: Any):
            entered.wait(timeout=10)
            assert release.wait(10)
            return super().create(request, inputs)

    provider = HoldingProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    initial = max(v.version for v in asset.versions)
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = list(pool.map(lambda _: _enqueue(service, asset.project_id, asset.id), range(3)))
        assert sorted(t.output_version_number for t in tasks) == list(range(initial + 1, initial + 4))
        for task in tasks:
            service.advance_image_task(task.id)
        pending = [pool.submit(service.advance_image_task, task.id) for task in tasks]
        try:
            # All three paid-call substitutes must enter before any is released.
            entered.wait(timeout=10)
            source = tmp_path / "local.png"
            Image.new("RGB", (64, 64), "blue").save(source)
            local = service.import_local_asset_version(asset.project_id, asset.id,
                AssetImportLocalRequest(local_path=str(source), descriptor="local during generation")).version
            assert local.version == initial + 4
        finally:
            release.set()
        results = [item.result(timeout=10).task for item in pending]
    assert len(provider.calls) == 3
    saved = _asset(service, asset.project_id, asset.id)
    assert len({v.version for v in saved.versions}) == len(saved.versions)
    for task in results:
        assert task.status == ImageTaskStatus.SUCCEEDED
        version = next(v for v in saved.versions if v.id == task.output_version_id)
        assert task.output_version_number == version.version
        assert task.output_path == version.local_path
        assert Path(task.output_path or "").name.startswith(f"v{version.version}-")
        assert Path(task.output_path or "").is_file()
    # Reservations belong to their asset, not a project-wide counter.
    other = service.create_asset(asset.project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name="other", descriptor="separate asset")).asset
    assert _enqueue(service, asset.project_id, other.id).output_version_number == 2


def test_reservations_survive_restart_and_cancel_without_reusing_public_numbers(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    first = _enqueue(service, asset.project_id, asset.id)
    service.cancel_image_task(first.id)
    reopened = _service(tmp_path, provider)
    second = _enqueue(reopened, asset.project_id, asset.id)
    assert second.output_version_number == first.output_version_number + 1
    assert reopened.repository.get_image_task(first.id) == service.repository.get_image_task(first.id)
    assert provider.calls == []


def test_old_colliding_inflight_output_reassigned_once_and_recovered_without_submission(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    first, second = [_enqueue(service, asset.project_id, asset.id) for _ in range(2)]
    # Reproduce an old persisted queue created before reservations existed.
    second.output_version_number = first.output_version_number
    service.repository.save_image_task(second)
    for task in (first, second):
        service.advance_image_task(task.id)
        result = service.tool_registry.invoke("image.advance", {"task_id": task.id})
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        done = service.repository.get_image_task(task.id)
        assert done is not None and done.status == ImageTaskStatus.SUCCEEDED
        assert any(ref.artifact_id == done.output_version_id and ref.version == done.output_version_number
                   for ref in result.artifact_refs)
    saved = _asset(service, asset.project_id, asset.id)
    original = next(v for v in saved.versions if v.id == first.output_version_id).model_copy(deep=True)
    done = service.repository.get_image_task(second.id)
    assert done is not None and done.output_path is not None
    assert done.output_version_number > first.output_version_number
    assert Path(done.output_path).name.startswith(f"v{done.output_version_number}-")
    assert not Path(done.output_path).with_name(f"v{first.output_version_number}-{done.output_version_id}.png").exists()
    before = [v.model_dump(mode="json") for v in saved.versions]
    done.status = ImageTaskStatus.OUTPUT_MISSING
    service.repository.save_image_task(done)
    reopened = _service(tmp_path, provider)
    recovered = reopened.recover_image_task_output(done.id).task
    after = _asset(reopened, asset.project_id, asset.id)
    assert recovered.status == ImageTaskStatus.SUCCEEDED
    assert recovered.output_version_number == done.output_version_number
    assert recovered.output_path == done.output_path
    assert [v.model_dump(mode="json") for v in after.versions] == before
    assert next(v for v in after.versions if v.id == first.output_version_id) == original
    assert len(provider.calls) == 2


@pytest.mark.parametrize("operation", ["crop", "sheet", "plate", "voice"])
def test_slow_local_derivation_allocates_after_new_task_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    project_id = service.initialize_fixture().document.project.id
    target = service.create_asset(project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name="local output", descriptor="local output",
        brief=CoreAssetBrief(identity_anchor="local output", visual_attributes=["clear"],
            presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE, required_visible_text=["OK"]),
    )).asset
    source_path = tmp_path / "source.png"
    Image.new("RGB", (128, 128), "blue").save(source_path)
    source = service.import_local_asset_version(project_id, target.id,
        AssetImportLocalRequest(local_path=str(source_path), descriptor="source")).version
    before = source_path.read_bytes()
    entered, release = threading.Event(), threading.Event()

    if operation == "crop":
        original = workbench_module.read_image_region
        owner, name = workbench_module, "read_image_region"
    elif operation == "voice":
        original = workbench_module.subprocess.run
        owner, name = workbench_module.subprocess, "run"
    else:
        name = "_render_reference_sheet" if operation == "sheet" else "_render_text_plate"
        original = getattr(AssetWorkbench, name)
        owner = AssetWorkbench

    def hold(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, staticmethod(hold) if owner is AssetWorkbench else hold)

    def derive() -> AssetVersion:
        if operation == "crop":
            return service.crop_asset_image(project_id, target.id, AssetImageCropRequest(
                source_asset_version_id=source.id, region=ImageRegion(x=0, y=0, width=.5, height=.5),
                descriptor="exact crop", reference_role=ImageReferenceRole.COMPOSITION,
            )).version
        if operation == "sheet":
            return service.asset_workbench.compose_reference_sheet(project_id, target.id,
                AssetReferenceSheetComposeRequest.model_validate({"strategy": "LIGHTWEIGHT", "columns": 1,
                    "descriptor": "sheet", "cells": [{"asset_version_id": source.id, "view": "CANONICAL_MASTER", "label": "source"}]}))
        if operation == "plate":
            return service.asset_workbench.compose_text_plate(project_id, target.id,
                AssetTextPlateComposeRequest(size="512x512", exact_lines=["OK"], layout_description="centered", descriptor="plate"))
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 16000 * 3)
        return service.asset_workbench.import_voice_reference_bytes(project_id, asset_id=target.id,
            asset_name=target.name, descriptor="voice", filename="source.wav", data=buffer.getvalue())

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(derive)
        try:
            assert entered.wait(10)
            queued = _enqueue(service, project_id, target.id)
        finally:
            release.set()
        local = pending.result(timeout=10)
    assert local.version == queued.output_version_number + 1
    assert Path(local.local_path or "").name.startswith(f"v{local.version}-")
    assert source_path.read_bytes() == before
    assert provider.calls == []
    service.advance_image_task(queued.id)
    completed = service.advance_image_task(queued.id).task
    assert completed.status == ImageTaskStatus.SUCCEEDED
    assert completed.output_version_number == queued.output_version_number
    saved = _asset(service, project_id, target.id)
    assert len({v.version for v in saved.versions}) == len(saved.versions)


def test_old_duplicate_versions_and_files_are_not_rewritten(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    path = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "red").save(path)
    for _ in range(2):
        service.import_local_asset_version(asset.project_id, asset.id,
            AssetImportLocalRequest(local_path=str(path), descriptor="historical candidate"))
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    target.versions[-1].version = target.versions[-2].version
    service.repository.save_project(document)
    history = [v.model_dump(mode="json") for v in target.versions]
    files = {v.local_path: Path(v.local_path).read_bytes() for v in target.versions if v.local_path}
    queued = _enqueue(service, asset.project_id, asset.id)
    assert queued.output_version_number > max(v.version for v in target.versions)
    service.advance_image_task(queued.id)
    service.advance_image_task(queued.id)
    assert [v.model_dump(mode="json") for v in _asset(service, asset.project_id, asset.id).versions[:-1]] == history
    assert all(Path(path).read_bytes() == contents for path, contents in files.items())


def test_semantic_asset_version_skips_inflight_reservation(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    task = _enqueue(service, asset.project_id, asset.id)
    result = service.apply_semantic_change(asset.project_id, SemanticChangeApplyRequest(
        plan=_plan(project_id=asset.project_id, kind=ChangeTargetKind.ASSET,
            target_id=asset.id, affected_shot_ids=[], affected_asset_ids=[asset.id]),
        patches=[SemanticArtifactPatch(target_kind=ChangeTargetKind.ASSET, target_id=asset.id,
            updates={"descriptor": "Only the requested asset description changed."})],
        change_reason="user edited asset description",
    ))
    saved = next(a for a in result.project.document.assets if a.id == asset.id)
    new = next(v for v in saved.versions if v.id in result.result.asset_version_ids)
    assert new.version == task.output_version_number + 1
    assert service.repository.get_image_task(task.id) == task
    assert provider.calls == []
