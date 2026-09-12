"""Original edit pixels survive local blending and support offline candidate repair."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any

from PIL import Image, ImageDraw
import pytest
from pydantic import ValidationError

from short_drama import asset_workbench as workbench_module
from short_drama.asset_workbench import AssetWorkbenchConflict
from short_drama.image_edit_results import AssetImageEditResultRequest
from short_drama.image_models import AssetImportLocalRequest, ImageOperation, ImageProviderRequest, ImageTaskCreateRequest, ImageTaskStatus
from short_drama.models import ApprovalStatus, Freshness, OriginType
from short_drama.providers.image_provider import ImageProviderInput, ImageProviderResult
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


class RecordingProvider(FakeImageProvider):
    raw: bytes = b""

    def create(self, request: ImageProviderRequest, inputs: list[ImageProviderInput]) -> ImageProviderResult:
        result = super().create(request, inputs)
        self.raw = base64.b64decode(result.image_base64)
        return result


def _queued(tmp_path: Path, provider: RecordingProvider, *, masked: bool = True):
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    source_path = tmp_path / "base.png"
    Image.new("RGB", (768, 1344), (18, 52, 86)).save(source_path)
    base = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(source_path), descriptor="original photograph")).version
    mask_path = tmp_path / "mask.png"
    with Image.new("RGBA", (768, 1344), (255, 255, 255, 255)) as image:
        ImageDraw.Draw(image).rectangle((200, 200, 568, 1200), fill=(255, 255, 255, 0))
        image.save(mask_path)
    mask = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(mask_path), descriptor="editable region")).version
    task = service.enqueue_image_task(asset.project_id, asset.id, ImageTaskCreateRequest(
        operation=ImageOperation.EDIT, prompt="Repair the observed hand and sleeve.",
        descriptor="Provider candidate", parent_version_id=base.id,
        input_version_ids=[base.id], primary_input_version_id=base.id,
        mask_version_id=mask.id if masked else None, mask_feather_pixels=8,
    )).task
    service.advance_image_task(task.id)
    return service, asset, base, mask, task


def _completed(tmp_path: Path, *, masked: bool = True):
    provider = RecordingProvider()
    service, asset, base, mask, task = _queued(tmp_path, provider, masked=masked)
    task = service.advance_image_task(task.id).task
    assert task.status == ImageTaskStatus.SUCCEEDED
    return service, provider, asset, base, mask, task


def test_new_masked_task_preserves_exact_response_before_inward_blending(tmp_path: Path) -> None:
    service, provider, asset, base, _mask, task = _completed(tmp_path)
    assert task.provider_output_path is not None
    assert Path(task.provider_output_path).read_bytes() == provider.raw
    assert task.provider_output_path != task.output_path
    assert not Path(task.staged_path or "").is_file()
    with Image.open(task.output_path or "") as final, Image.open(task.provider_output_path) as raw:
        assert raw.convert("RGB").getpixel((10, 500)) == (127, 29, 29)
        assert final.convert("RGB").getpixel((10, 500)) == (18, 52, 86)
        edge = final.convert("RGB").getpixel((200, 500))
        assert edge not in ((18, 52, 86), (127, 29, 29))
        assert final.convert("RGB").getpixel((300, 500)) == (127, 29, 29)
    reopened = _service(tmp_path, provider)
    assert reopened.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1
    assert Path(base.local_path or "").is_file()
    assert reopened.get_project(asset.project_id).document.project.id == asset.project_id


def test_unmasked_task_keeps_legacy_storage_and_does_not_claim_raw_backup(tmp_path: Path) -> None:
    _service_instance, provider, _asset, _base, _mask, task = _completed(tmp_path, masked=False)
    assert task.provider_output_path is None
    assert Path(task.output_path or "").read_bytes() == provider.raw


def test_pure_response_survives_rejected_final_missing_base_pixels_and_restart(tmp_path: Path) -> None:
    service, provider, asset, base, _mask, task = _completed(tmp_path)
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    final = next(v for v in target.versions if v.id == task.output_version_id)
    final.approval_status = ApprovalStatus.REJECTED
    final.freshness = Freshness.STALE
    service.repository.save_project(document)
    Path(base.local_path or "").unlink()
    reopened = _service(tmp_path, provider)
    request = AssetImageEditResultRequest(image_task_id=task.id, mode="PROVIDER_OUTPUT", descriptor="Inspect the original response")
    version = reopened.asset_workbench.derive_edit_result(asset.project_id, request)
    after = reopened.get_project(asset.project_id).document
    saved = next(a for a in after.assets if a.id == asset.id)
    assert Path(version.local_path or "").read_bytes() == provider.raw
    assert version.origin_type == OriginType.LOCAL_DERIVED
    assert version.approval_status == ApprovalStatus.DRAFT
    assert saved.current_version_id == target.current_version_id
    assert saved.approved_version_id == target.approved_version_id
    assert saved.versions == target.versions
    assert version.id == task.provider_output_version_id
    assert after.shots == document.shots
    assert after.reference_packs == document.reference_packs
    assert version.parent_version_id == task.output_version_id
    assert [ref.asset_version_id for ref in version.derivation_references] == [base.id, task.output_version_id]
    assert version.source is not None
    assert version.source.source_uri == task.provider_output_path
    provenance = AssetImageEditResultRequest.model_validate_json(version.source.prompt or "")
    assert provenance.image_task_id == request.image_task_id and provenance.mode == request.mode
    assert reopened.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1


def test_new_mask_uses_original_response_and_never_reblends_old_candidate(tmp_path: Path) -> None:
    service, provider, asset, base, mask, task = _completed(tmp_path)
    original_final = Path(task.output_path or "").read_bytes()
    original_raw = Path(task.provider_output_path or "").read_bytes()
    same = service.asset_workbench.derive_edit_result(asset.project_id, AssetImageEditResultRequest(
        image_task_id=task.id, mode="MASKED_COMPOSITE", mask_version_id=mask.id,
        mask_feather_pixels=8, descriptor="Same response and same blend"))
    assert Path(same.local_path or "").read_bytes() == original_final
    wider_path = tmp_path / "wider.png"
    with Image.new("RGBA", (768, 1344), (255, 255, 255, 255)) as image:
        ImageDraw.Draw(image).rectangle((100, 200, 620, 1200), fill=(255, 255, 255, 0))
        image.save(wider_path)
    wider = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(wider_path), descriptor="Expanded observed edit region")).version
    before = service.get_project(asset.project_id).document
    result = service.asset_workbench.derive_edit_result(asset.project_id, AssetImageEditResultRequest(
        image_task_id=task.id, mode="MASKED_COMPOSITE", mask_version_id=wider.id,
        mask_feather_pixels=0, descriptor="Use existing pixels in the larger region"))
    with Image.open(result.local_path or "") as image:
        assert image.convert("RGB").getpixel((150, 500)) == (127, 29, 29)
        assert image.convert("RGB").getpixel((99, 500)) == (18, 52, 86)
    assert [r.asset_version_id for r in result.derivation_references] == [base.id, task.output_version_id, wider.id]
    assert Path(task.output_path or "").read_bytes() == original_final
    assert Path(task.provider_output_path or "").read_bytes() == original_raw
    after = service.get_project(asset.project_id).document
    assert next(a for a in after.assets if a.id == asset.id).current_version_id == next(a for a in before.assets if a.id == asset.id).current_version_id
    assert service.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1


@pytest.mark.parametrize("payload", [
    {}, {"mode": "MASKED_COMPOSITE"},
    {"mode": "PROVIDER_OUTPUT", "mask_version_id": "mask"},
    {"mode": "PROVIDER_OUTPUT", "mask_feather_pixels": 1},
    {"mode": "MASKED_COMPOSITE", "mask_version_id": "mask", "mask_feather_pixels": 65},
    {"mode": "MASKED_COMPOSITE", "mask_version_id": "mask", "mask_feather_pixels": -1},
])
def test_modes_reject_ambiguous_or_ignored_options(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AssetImageEditResultRequest.model_validate({"image_task_id": "task", "descriptor": "candidate", **payload})


@pytest.mark.parametrize("failure", ["legacy", "missing_raw", "wrong_project", "unfinished", "unmasked",
    "rejected_base", "stale_mask", "mask_size", "mask_jpeg", "mask_opaque_rgb", "mask_exif"])
def test_ineligible_derivation_changes_no_saved_data(tmp_path: Path, failure: str) -> None:
    service, provider, asset, base, mask, task = _completed(tmp_path)
    if failure == "legacy":
        task.provider_output_path = None
        service.repository.save_image_task(task)
    elif failure == "missing_raw":
        Path(task.provider_output_path or "").unlink()
    elif failure == "unfinished":
        task.status = ImageTaskStatus.SUBMITTING
        service.repository.save_image_task(task)
    elif failure == "unmasked":
        task.request.mask_version_id = None
        service.repository.save_image_task(task)
    elif failure in {"rejected_base", "stale_mask"}:
        document = service.get_project(asset.project_id).document
        version = next(v for a in document.assets for v in a.versions if v.id == (base.id if failure == "rejected_base" else mask.id))
        if failure == "rejected_base":
            version.approval_status = ApprovalStatus.REJECTED
        else:
            version.freshness = Freshness.STALE
        service.repository.save_project(document)
    elif failure == "mask_size":
        Image.new("RGBA", (20, 20)).save(mask.local_path or "")
    elif failure in {"mask_jpeg", "mask_opaque_rgb"}:
        Image.new("RGB", (768, 1344), "white").save(mask.local_path or "",
            format="JPEG" if failure == "mask_jpeg" else "PNG")
    elif failure == "mask_exif":
        with Image.new("RGBA", (768, 1344)) as image:
            exif = image.getexif()
            exif[274] = 6
            image.save(mask.local_path or "", format="PNG", exif=exif)
    before = service.get_project(asset.project_id).document
    with pytest.raises(AssetWorkbenchConflict):
        service.asset_workbench.derive_edit_result("other-project" if failure == "wrong_project" else asset.project_id,
            AssetImageEditResultRequest(image_task_id=task.id, mode="MASKED_COMPOSITE", mask_version_id=mask.id, descriptor="candidate"))
    assert service.get_project(asset.project_id).document == before
    assert not list(Path(task.output_path or "").parent.glob("*edit-result*"))
    assert len(provider.calls) == 1


@pytest.mark.parametrize("source_name", ["primary", "raw"])
@pytest.mark.parametrize("orientation", [6, 8])
def test_local_masking_rejects_rotated_pixels_but_raw_copy_preserves_encoding(
    tmp_path: Path, source_name: str, orientation: int,
) -> None:
    service, provider, asset, base, mask, task = _completed(tmp_path)
    source_path = Path(base.local_path or "") if source_name == "primary" else Path(task.provider_output_path or "")
    with Image.open(source_path) as image:
        pixels = image.copy()
        exif = image.getexif()
    with pixels:
        exif[274] = orientation
        pixels.save(source_path, format="PNG", exif=exif)
    before = service.get_project(asset.project_id).document
    raw_bytes = Path(task.provider_output_path or "").read_bytes()
    reopened = _service(tmp_path, provider)
    expected_source = "Original primary image" if source_name == "primary" else "Preserved Provider output"
    with pytest.raises(AssetWorkbenchConflict, match=expected_source + " pixels must already be upright"):
        reopened.asset_workbench.derive_edit_result(asset.project_id, AssetImageEditResultRequest(
            image_task_id=task.id, mode="MASKED_COMPOSITE", mask_version_id=mask.id,
            descriptor="Reuse the existing edit with local protection"))
    assert reopened.get_project(asset.project_id).document == before
    assert not list(Path(task.output_path or "").parent.glob("*edit-result*"))
    result = reopened.asset_workbench.derive_edit_result(asset.project_id, AssetImageEditResultRequest(
        image_task_id=task.id, mode="PROVIDER_OUTPUT", descriptor="Inspect the preserved original bytes"))
    assert Path(result.local_path or "").read_bytes() == raw_bytes
    assert reopened.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1


def test_recover_after_composition_failure_uses_saved_raw_without_resubmission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = RecordingProvider()
    service, asset, _base, _mask, task = _queued(tmp_path, provider)
    original = workbench_module.render_masked_edit

    def fail(*args: Any, **kwargs: Any) -> tuple[int, int]:
        raise OSError("simulated local composition interruption")

    monkeypatch.setattr(workbench_module, "render_masked_edit", fail)
    failed = service.advance_image_task(task.id).task
    assert failed.status == ImageTaskStatus.OUTPUT_MISSING
    assert Path(failed.provider_output_path or "").read_bytes() == provider.raw
    assert Path(failed.staged_path or "").read_bytes() == provider.raw
    monkeypatch.setattr(workbench_module, "render_masked_edit", original)
    reopened = _service(tmp_path, provider)
    recovered = reopened.asset_workbench.recover_image_output(task.id)
    assert recovered.status == ImageTaskStatus.SUCCEEDED
    with Image.open(recovered.output_path or "") as image:
        assert image.convert("RGB").getpixel((10, 500)) == (18, 52, 86)
        assert image.convert("RGB").getpixel((300, 500)) == (127, 29, 29)
    assert len(provider.calls) == 1
    assert len([v for a in reopened.get_project(asset.project_id).document.assets for v in a.versions if v.id == task.output_version_id]) == 1


@pytest.mark.parametrize("crash_phase", ["before_composite", "after_composite", "missing_raw"])
def test_restart_advance_recovers_submitting_response_without_another_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_phase: str,
) -> None:
    provider = RecordingProvider()
    service, _asset, _base, _mask, task = _queued(tmp_path, provider)
    original = workbench_module.render_masked_edit

    class ProcessStopped(BaseException):
        pass

    def stop_process(*args: Any, **kwargs: Any) -> tuple[int, int]:
        if crash_phase == "after_composite":
            original(*args, **kwargs)
        raise ProcessStopped()

    monkeypatch.setattr(workbench_module, "render_masked_edit", stop_process)
    with pytest.raises(ProcessStopped):
        service.advance_image_task(task.id)
    interrupted = service.repository.get_image_task(task.id)
    assert interrupted is not None and interrupted.status == ImageTaskStatus.SUBMITTING
    assert interrupted.provider_output_path is not None
    assert Path(interrupted.provider_output_path).read_bytes() == provider.raw
    if crash_phase == "missing_raw":
        Path(interrupted.provider_output_path).unlink()
    monkeypatch.setattr(workbench_module, "render_masked_edit", original)
    reopened = _service(tmp_path, provider)
    if crash_phase == "missing_raw":
        with pytest.raises(AssetWorkbenchConflict, match="Preserved Provider output is missing"):
            reopened.asset_workbench.advance_image_task(task.id)
        saved = reopened.repository.get_image_task(task.id)
        assert saved is not None and saved.status == ImageTaskStatus.OUTPUT_MISSING
    else:
        recovered = reopened.asset_workbench.advance_image_task(task.id)
        assert recovered.status == ImageTaskStatus.SUCCEEDED
        with Image.open(recovered.output_path or "") as image:
            assert image.convert("RGB").getpixel((10, 500)) == (18, 52, 86)
            assert image.convert("RGB").getpixel((300, 500)) == (127, 29, 29)
            assert image.convert("RGB").getpixel((200, 500)) not in ((18, 52, 86), (127, 29, 29))
    assert len(provider.calls) == 1


@pytest.mark.parametrize("staged_is_composited", [False, True])
def test_recovery_ignores_ambiguous_staging_when_raw_is_preserved(tmp_path: Path, staged_is_composited: bool) -> None:
    service, provider, _asset, _base, _mask, task = _completed(tmp_path)
    expected = Path(task.output_path or "").read_bytes()
    Path(task.staged_path or "").write_bytes(expected if staged_is_composited else provider.raw)
    Path(task.output_path or "").unlink()
    task.status = ImageTaskStatus.OUTPUT_MISSING
    service.repository.save_image_task(task)
    recovered = _service(tmp_path, provider).asset_workbench.recover_image_output(task.id)
    assert Path(recovered.output_path or "").read_bytes() == expected
    assert Path(recovered.provider_output_path or "").read_bytes() == provider.raw
    assert len(provider.calls) == 1


@pytest.mark.parametrize("final_exists", [False, True])
def test_legacy_masked_recovery_requires_known_final_instead_of_guessing_stage(tmp_path: Path, final_exists: bool) -> None:
    service, provider, _asset, _base, _mask, task = _completed(tmp_path)
    task.provider_output_path = None
    task.status = ImageTaskStatus.OUTPUT_MISSING
    Path(task.staged_path or "").write_bytes(provider.raw)
    if not final_exists:
        Path(task.output_path or "").unlink()
    service.repository.save_image_task(task)
    if final_exists:
        assert service.asset_workbench.recover_image_output(task.id).status == ImageTaskStatus.SUCCEEDED
    else:
        with pytest.raises(AssetWorkbenchConflict, match="ambiguous staged file"):
            service.asset_workbench.recover_image_output(task.id)
        assert service.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1


def test_restart_raw_recovery_preserves_user_selection_in_latest_project(tmp_path: Path) -> None:
    service, provider, asset, _base, _mask, task = _completed(tmp_path)
    expected_output = Path(task.output_path or "").read_bytes()
    selection_path = tmp_path / "user-selection.png"
    Image.new("RGB", (64, 64), "orange").save(selection_path)
    selected = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(selection_path), descriptor="User chose a later image")).version
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    target.approved_version_id = selected.id
    service.repository.save_project(document)
    Path(task.output_path or "").unlink()
    task.status = ImageTaskStatus.OUTPUT_MISSING
    service.repository.save_image_task(task)
    reopened = _service(tmp_path, provider)
    recovered = reopened.asset_workbench.recover_image_output(task.id)
    assert recovered.status == ImageTaskStatus.SUCCEEDED
    assert Path(recovered.output_path or "").read_bytes() == expected_output
    after = reopened.get_project(asset.project_id).document
    saved = next(a for a in after.assets if a.id == asset.id)
    assert saved.current_version_id == selected.id
    assert saved.approved_version_id == selected.id
    assert saved.versions == target.versions
    assert after.shots == document.shots and after.reference_packs == document.reference_packs
    assert len(provider.calls) == 1


@pytest.mark.parametrize("change_source", [False, True])
def test_local_render_preserves_concurrent_selection_and_checks_used_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change_source: bool,
) -> None:
    service, provider, asset, base, mask, task = _completed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = workbench_module.render_masked_edit

    def held(*args: Any, **kwargs: Any) -> tuple[int, int]:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(workbench_module, "render_masked_edit", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.asset_workbench.derive_edit_result, asset.project_id,
            AssetImageEditResultRequest(image_task_id=task.id, mode="MASKED_COMPOSITE", mask_version_id=mask.id, descriptor="local candidate"))
        assert entered.wait(10)
        try:
            reserved = service.enqueue_image_task(asset.project_id, asset.id,
                ImageTaskCreateRequest(prompt="independent future candidate", descriptor="reserved")).task
            path = tmp_path / "selection.png"
            Image.new("RGB", (32, 32), "orange").save(path)
            selection = service.import_local_asset_version(asset.project_id, asset.id,
                AssetImportLocalRequest(local_path=str(path), descriptor="new selection")).version
            if change_source:
                Image.new("RGB", (768, 1344), "purple").save(base.local_path or "")
        finally:
            release.set()
        if change_source:
            with pytest.raises(AssetWorkbenchConflict, match="source changed"):
                pending.result(timeout=10)
        else:
            result = pending.result(timeout=10)
            assert result.version > selection.version > reserved.output_version_number
    after = service.get_project(asset.project_id).document
    assert next(a for a in after.assets if a.id == asset.id).current_version_id == selection.id
    if change_source:
        assert not list(Path(task.output_path or "").parent.glob("*edit-result*"))
    assert service.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1
