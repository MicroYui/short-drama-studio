"""One masked response exposes two precise choices without selecting its unmasked pixels."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.asset_workbench import AssetWorkbenchConflict
from short_drama.image_edit_results import AssetImageEditResultRequest
from short_drama.image_models import ImageTaskCreateRequest, ImageTaskResponse, ImageTaskStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, Freshness
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_image_edit_result_tool import _finished_edit
from tests.test_image_edit_results import RecordingProvider, _completed, _queued
from tests.test_short_drama_assets import _service


def test_completed_tool_returns_precise_raw_and_final_and_raw_is_directly_inspectable(tmp_path: Path) -> None:
    fixture = _finished_edit(tmp_path)
    language = CapturingLanguageClient()
    service = ShortDramaService(database_path=fixture.service.repository.database_path,
        workspace_root=fixture.service.workspace_root,
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    before = service.get_project(fixture.project_id).document
    result = service.tool_registry.invoke("image.advance", {"task_id": fixture.task_id})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    task = ImageTaskResponse.model_validate(result.output).task
    assert task.provider_output_version_id is not None
    assert task.provider_output_version_number is not None
    refs = {ref.artifact_id: ref.version for ref in result.artifact_refs if ref.artifact_type == "asset_version"}
    assert refs == {task.output_version_id: task.output_version_number,
                    task.provider_output_version_id: task.provider_output_version_number}
    asset = next(a for a in before.assets if a.id == fixture.asset_id)
    raw = next(v for v in asset.versions if v.id == task.provider_output_version_id)
    final = next(v for v in asset.versions if v.id == task.output_version_id)
    assert raw.image_task_id is None and raw.approval_status == ApprovalStatus.DRAFT
    assert raw.version < final.version and asset.versions[-1].id == final.id
    assert max(asset.versions, key=lambda v: v.version).id == final.id
    assert raw.local_path == task.provider_output_path
    # The earlier fixture rejected only the visibly truncated final, not this unseen raw.
    assert final.approval_status == ApprovalStatus.REJECTED
    inspected = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": fixture.project_id, "asset_id": fixture.asset_id,
        "version_id": raw.id, "request": {},
    })
    assert inspected.status == ToolCallStatus.SUCCEEDED, inspected.error
    assert raw.id in {r.artifact_id for r in inspected.artifact_refs}
    payload = json.loads(language.input_text)
    assert payload["candidate_creation"]["masked_edit"] is None
    assert service.get_project(fixture.project_id).document == before
    assert len(fixture.provider.calls) == 1


def test_raw_is_registered_before_final_once_and_derive_and_restart_reuse_it(tmp_path: Path) -> None:
    service, provider, asset, _base, _mask, task = _completed(tmp_path)
    assert task.provider_output_version_id is not None
    before = service.get_project(asset.project_id).document
    target = next(a for a in before.assets if a.id == asset.id)
    raw = next(v for v in target.versions if v.id == task.provider_output_version_id)
    assert target.versions[-2:] == [raw, next(v for v in target.versions if v.id == task.output_version_id)]
    assert target.current_version_id == task.output_version_id
    assert raw.version + 1 == task.output_version_number
    files = {p for p in service.workspace_root.rglob("*") if p.is_file()}
    reopened = _service(tmp_path, provider)
    for _ in range(2):
        again = reopened.advance_image_task(task.id).task
        result = reopened.asset_workbench.derive_edit_result(asset.project_id,
            AssetImageEditResultRequest(image_task_id=task.id, mode="PROVIDER_OUTPUT", descriptor="Different natural description"))
        assert again == task and result == raw
    assert reopened.get_project(asset.project_id).document == before
    assert {p for p in service.workspace_root.rglob("*") if p.is_file()} == files
    assert len(provider.calls) == 1


@pytest.mark.parametrize("state", ["rejected", "stale", "missing"])
def test_existing_raw_is_not_duplicated_to_bypass_its_current_state(tmp_path: Path, state: str) -> None:
    service, provider, asset, _base, _mask, task = _completed(tmp_path)
    document = service.get_project(asset.project_id).document
    raw = next(v for a in document.assets for v in a.versions if v.id == task.provider_output_version_id)
    if state == "rejected":
        raw.approval_status = ApprovalStatus.REJECTED
    elif state == "stale":
        raw.freshness = Freshness.STALE
    else:
        Path(raw.local_path or "").unlink()
    service.repository.save_project(document)
    before = service.get_project(asset.project_id).document
    with pytest.raises(AssetWorkbenchConflict):
        service.asset_workbench.derive_edit_result(asset.project_id,
            AssetImageEditResultRequest(image_task_id=task.id, mode="PROVIDER_OUTPUT", descriptor="Try a fresh candidate"))
    assert service.get_project(asset.project_id).document == before
    assert len(provider.calls) == 1


def test_legacy_manual_raw_reuses_exact_task_provenance_not_just_shared_path(tmp_path: Path) -> None:
    service, provider, asset, _base, _mask, task = _completed(tmp_path)
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    old_raw = next(v for v in target.versions if v.id == task.provider_output_version_id)
    assert old_raw.source is not None
    unrelated = AssetImageEditResultRequest(image_task_id="other-task", mode="PROVIDER_OUTPUT", descriptor="Same path, other origin")
    old_raw.source.prompt = unrelated.model_dump_json()
    service.repository.save_project(document)
    task.provider_output_version_id = task.provider_output_version_number = None
    path = Path(task.provider_output_path or "")
    task.provider_output_path = f"{path.parent}/../{path.parent.name}/{path.name}"
    service.repository.save_image_task(task)
    request = AssetImageEditResultRequest(image_task_id=task.id, mode="PROVIDER_OUTPUT", descriptor="Manual legacy raw")
    first = service.asset_workbench.derive_edit_result(asset.project_id, request)
    assert first.id != old_raw.id
    before = service.get_project(asset.project_id).document
    second = _service(tmp_path, provider).asset_workbench.derive_edit_result(asset.project_id, request)
    assert second == first
    assert service.get_project(asset.project_id).document == before
    assert len(provider.calls) == 1


def test_legacy_inflight_and_other_reservations_keep_all_saved_numbers_and_final_latest(tmp_path: Path) -> None:
    provider = RecordingProvider()
    service, asset, _base, _mask, task = _queued(tmp_path, provider)
    task = service.repository.get_image_task(task.id)
    assert task is not None
    task.provider_output_version_number = None  # Older in-flight record.
    service.repository.save_image_task(task)
    before = service.get_project(asset.project_id).document
    saved_numbers = {v.id: v.version for a in before.assets for v in a.versions}
    pending = service.enqueue_image_task(asset.project_id, asset.id, ImageTaskCreateRequest(
        operation="GENERATE", prompt="A different candidate", descriptor="Other reservation")).task
    finished = service.advance_image_task(task.id).task
    target = next(a for a in service.get_project(asset.project_id).document.assets if a.id == asset.id)
    assert {v.id: v.version for a in before.assets for v in a.versions} == saved_numbers
    assert all(v.version == saved_numbers[v.id] for v in target.versions if v.id in saved_numbers)
    assert finished.provider_output_version_number is not None
    assert finished.provider_output_version_number > pending.output_version_number
    assert target.versions[-1].id == max(target.versions, key=lambda v: v.version).id == finished.output_version_id
    assert len({v.version for v in target.versions}) == len(target.versions)
    assert len(provider.calls) == 1


def test_restart_after_atomic_project_registration_keeps_pair_and_user_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = RecordingProvider()
    service, asset, base, _mask, queued = _queued(tmp_path, provider)
    original_save = service.repository.save_image_task

    def stop_after_project_save(task):
        if task.status == ImageTaskStatus.SUCCEEDED:
            raise SystemExit("Stop after the two versions are atomically registered")
        original_save(task)

    monkeypatch.setattr(service.repository, "save_image_task", stop_after_project_save)
    with pytest.raises(SystemExit):
        service.advance_image_task(queued.id)
    document = service.get_project(asset.project_id).document
    target = next(a for a in document.assets if a.id == asset.id)
    raw, final = target.versions[-2:]
    assert raw.parent_version_id == final.id == queued.output_version_id
    target.current_version_id = target.approved_version_id = base.id
    service.repository.save_project(document)
    reopened = _service(tmp_path, provider)
    recovered = reopened.asset_workbench.recover_image_output(queued.id)
    assert recovered.provider_output_version_id == raw.id
    assert recovered.output_version_id == final.id
    saved = reopened.get_project(asset.project_id).document
    saved_asset = next(a for a in saved.assets if a.id == asset.id)
    assert saved_asset.versions == target.versions
    assert saved_asset.current_version_id == saved_asset.approved_version_id == base.id
    assert saved.production_documents == document.production_documents
    assert saved.execution_plan == document.execution_plan
    assert saved.shots == document.shots and saved.reference_packs == document.reference_packs
    assert len(provider.calls) == 1


def test_two_masked_tasks_finished_in_reverse_keep_distinct_reservations_and_final_defaults(tmp_path: Path) -> None:
    provider = RecordingProvider()
    service, asset, base, mask, first = _queued(tmp_path, provider)
    second = service.enqueue_image_task(asset.project_id, asset.id, ImageTaskCreateRequest(
        operation="EDIT", prompt="Different edit", descriptor="Second masked request",
        parent_version_id=base.id, primary_input_version_id=base.id,
        input_version_ids=[base.id], mask_version_id=mask.id)).task
    service.advance_image_task(second.id)
    completed_second = service.advance_image_task(second.id).task
    completed_first = service.advance_image_task(first.id).task
    target = next(a for a in service.get_project(asset.project_id).document.assets if a.id == asset.id)
    assert completed_first.output_version_number == first.output_version_number
    assert completed_second.output_version_number == second.output_version_number
    assert len({v.version for v in target.versions}) == len(target.versions)
    assert target.current_version_id == target.versions[-1].id == first.output_version_id
    assert max(target.versions, key=lambda v: v.version).id == second.output_version_id
    assert len(provider.calls) == 2
