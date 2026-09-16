"""Real local package IO, durable job results and cancellation/commit boundaries."""

from datetime import UTC, datetime
from pathlib import Path
import shutil
import sqlite3
from threading import Thread
import time
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_package_models import StudioProjectTransfer
from short_drama.studio_package_progress import PackageCancelled, PackageProgress
from short_drama.studio_project_package import create_project_package, import_project_package
from short_drama.studio_project_transfers import (
    _ACTIVE, _ACTIVE_LOCK, _TransferControl, _key, _recover_one, _run, _save, cancel_project_transfer,
    get_project_transfer, list_project_transfers, recover_interrupted_project_transfers,
    start_project_collection, start_project_import,
)
from short_drama.studio_trace_export import export_studio_trace, write_studio_trace
from tests.test_studio_project_package import _client
from tests.test_studio_export_lifecycle import _video_project


def _wait(service: ShortDramaService, identity: str) -> StudioProjectTransfer:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = get_project_transfer(service, identity)
        with _ACTIVE_LOCK:
            active = _key(service, identity) in _ACTIVE
        if record.status in {"completed", "failed", "cancelled"} and not active:
            return record
        time.sleep(0.01)
    raise AssertionError("Local transfer did not finish")


def test_background_http_results_survive_close_reopen_and_small_poll_response(tmp_path: Path) -> None:
    source, project_id, _revision = _video_project(tmp_path / "source")
    with _client(source) as client:
        response = client.post(f"/api/short-drama/studio/projects/{project_id}/package-jobs")
        assert response.status_code == 202 and response.json()["status"] == "queued"
        identity = response.json()["id"]
        assert "input_path" not in response.json() and "working_path" not in response.json()
    result = _wait(source, identity)
    assert result.status == "completed" and result.package is not None, result.error
    reopened = ShortDramaService(database_path=source.repository.database_path, workspace_root=source.workspace_root)
    recover_interrupted_project_transfers(reopened)
    with _client(reopened) as client:
        listed = client.get("/api/short-drama/studio/project-transfers").json()
        assert listed["transfers"][0]["package"]["package_id"] == result.package.package_id
        assert client.post(f"/api/short-drama/studio/project-transfers/{identity}/cancel").json()["status"] == "completed"
        download = client.get(result.package.download_url)
        assert download.status_code == 200
    destination = ShortDramaService(database_path=tmp_path / "destination.sqlite3", workspace_root=tmp_path / "destination")
    with _client(destination) as client:
        imported = client.post("/api/short-drama/studio/projects/import-package-jobs",
            files={"file": ("collected.zip", download.content, "application/zip")})
        assert imported.status_code == 202
    finished = _wait(destination, imported.json()["id"])
    assert finished.status == "completed" and finished.imported is not None, finished.error
    assert finished.imported.project_id == project_id
    assert "project" not in finished.imported.model_dump()  # Never poll the full ProjectDocument.
    assert destination.repository.get_project(project_id) is not None
    assert not list((destination.workspace_root / "package-uploads").glob("*.zip"))
    assert destination.repository.list_studio_jobs() == []
    assert destination.repository.list_provider_runs(project_id) == []


def test_direct_trace_writer_preserves_every_record_and_reports_real_bytes(tmp_path: Path) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    trace = service.workspace_root / project_id / "studio-trace"
    trace.mkdir(parents=True)
    (trace / "history.json").write_text('{"old_result":"保留完整历史"}')
    original = export_studio_trace(service, project_id)
    direct = tmp_path / "direct.zip"
    progress = PackageProgress()
    with ZipFile(direct, "w", compression=ZIP_DEFLATED) as archive:
        write_studio_trace(service, project_id, archive, prefix="trace/", progress=progress)
    with ZipFile(original) as old, ZipFile(direct) as new:
        assert {"trace/" + name for name in old.namelist()} == set(new.namelist())
        assert all(old.read(name) == new.read("trace/" + name) for name in old.namelist())
        assert progress.processed_files == len(new.namelist())
        assert progress.processed_bytes == sum(info.file_size for info in new.infolist())


@pytest.mark.parametrize("cancel_stage", ["media", "trace"])
def test_collection_cancel_cleans_partial_package_after_real_io(tmp_path: Path, cancel_stage: str) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    observed: list[tuple[str, int, int]] = []

    def stop(progress: PackageProgress) -> None:
        observed.append((progress.stage, progress.processed_files, progress.processed_bytes))
        if progress.stage == cancel_stage and progress.processed_bytes > 0:
            raise PackageCancelled()

    with pytest.raises(PackageCancelled):
        create_project_package(service, project_id, progress=PackageProgress(on_update=stop))
    assert any(stage == cancel_stage and size > 0 for stage, _count, size in observed)
    assert list((service.workspace_root / project_id / "project-packages").iterdir()) == []
    assert service.repository.get_project(project_id) is not None


@pytest.mark.parametrize("cancel_stage", ["extracting", "committing"])
def test_import_cancel_before_commit_keeps_no_project_or_partial_media(tmp_path: Path, cancel_stage: str) -> None:
    service, project_id, _revision = _video_project(tmp_path / "source")
    package = create_project_package(service, project_id)
    destination = ShortDramaService(database_path=tmp_path / "destination.sqlite3", workspace_root=tmp_path / "destination")

    def stop(progress: PackageProgress) -> None:
        if progress.stage == cancel_stage and (progress.processed_bytes > 0 or cancel_stage == "committing"):
            raise PackageCancelled()

    with pytest.raises(PackageCancelled):
        import_project_package(destination, Path(package.path), progress=PackageProgress(on_update=stop))
    assert destination.repository.list_projects() == []
    assert list((destination.workspace_root / "imported-projects").iterdir()) == []


def test_cancel_queued_worker_is_acknowledged_after_cleanup_and_late_update_cannot_erase(tmp_path: Path) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    now = datetime.now(UTC).isoformat()
    record = StudioProjectTransfer(id="test-cancel", operation="collect", project_id=project_id,
        project_name="test", created_at=now, updated_at=now,
        working_path=str(tmp_path / "partial.zip"))
    partial = Path(record.working_path or "").with_suffix(".tmp")
    partial.write_bytes(b"unfinished")
    control = _TransferControl()
    with service.studio.lock:
        _save(service, record)
        with _ACTIVE_LOCK:
            _ACTIVE[_key(service, record.id)] = control
        thread = Thread(target=_run, args=(service, record, control, "unused-package-id"))
        thread.start()
        accepted = cancel_project_transfer(service, record.id)
        assert accepted.status == "cancelling" and partial.exists()
    assert control.done.wait(5)
    thread.join()
    cancelled = get_project_transfer(service, record.id)
    assert cancelled.status == "cancelled" and not partial.exists()
    late = cancelled.model_copy(update={"status": "running", "message": "late"})
    _save(service, late)
    assert get_project_transfer(service, record.id) == cancelled


def test_import_and_result_share_one_transaction_if_result_save_fails(tmp_path: Path) -> None:
    source, project_id, _revision = _video_project(tmp_path / "source")
    package = create_project_package(source, project_id)
    destination = ShortDramaService(database_path=tmp_path / "destination.sqlite3", workspace_root=tmp_path / "destination")
    uploaded = tmp_path / "upload.zip"
    shutil.copyfile(package.path, uploaded)
    with sqlite3.connect(destination.repository.database_path) as connection:
        connection.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE ON studio_project_transfers
            WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT, 'completion failed'); END""")
    result = start_project_import(destination, uploaded, "package.zip", background=False)
    assert result.status == "failed" and "completion failed" in (result.error or "")
    assert destination.repository.get_project(project_id) is None
    assert list((destination.workspace_root / "imported-projects").iterdir()) == []
    assert not uploaded.exists()


def test_recovery_pages_all_pending_jobs_and_keeps_completed_results(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    now = datetime.now(UTC).isoformat()
    for index in range(105):
        folder = tmp_path / f"partial-{index}"
        folder.mkdir()
        (folder / "file.txt").write_text("partial")
        service.repository.save_project_transfer(StudioProjectTransfer(id=f"transfer-{index:03d}", operation="import",
            project_name="old upload", created_at=now, updated_at=now, status="running", working_path=str(folder)))
    recover_interrupted_project_transfers(service)
    assert not list(tmp_path.glob("partial-*"))
    ids: list[str] = []
    before: str | None = None
    while True:
        page = list_project_transfers(service, limit=17, before=before)
        assert all(record.status == "failed" and record.stage == "interrupted" for record in page.transfers)
        ids.extend(record.id for record in page.transfers)
        before = page.next_cursor
        if before is None:
            break
    assert len(ids) == len(set(ids)) == 105
    assert service.repository.list_studio_jobs() == []
    with _client(service) as client:
        assert client.get("/api/short-drama/studio/project-transfers?before=bad").status_code == 422
        assert client.get("/api/short-drama/studio/project-transfers/missing").status_code == 404
    assert service.repository.list_projects() == []


def test_progress_updates_do_not_rewrite_creative_content(tmp_path: Path) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER no_content_write BEFORE UPDATE ON projects BEGIN SELECT RAISE(ABORT, 'content rewritten'); END")
    result = start_project_collection(service, project_id, background=False)
    assert result.status == "completed", result.error


def test_waiting_transfer_can_cancel_without_starting_second_large_io(tmp_path: Path) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    first = _TransferControl()
    first.io_slot.acquire()
    first_key = _key(service, "already-working")
    with _ACTIVE_LOCK:
        _ACTIVE[first_key] = first
    try:
        accepted = start_project_collection(service, project_id)
        assert accepted.status == "queued"
        with _ACTIVE_LOCK:
            waiting = _ACTIVE[_key(service, accepted.id)]
        assert waiting.io_slot is first.io_slot
        assert cancel_project_transfer(service, accepted.id).status == "cancelling"
        assert waiting.done.wait(5)
        assert get_project_transfer(service, accepted.id).status == "cancelled"
        assert not (service.workspace_root / project_id / "project-packages").exists()
    finally:
        first.io_slot.release()
        with _ACTIVE_LOCK:
            _ACTIVE.pop(first_key, None)


def test_stale_recovery_snapshot_never_deletes_just_completed_result(tmp_path: Path) -> None:
    service, project_id, _revision = _video_project(tmp_path)
    completed = start_project_collection(service, project_id, background=False)
    assert completed.package is not None
    stale = completed.model_copy(update={"status": "running", "stage": "trace", "package": None})
    _recover_one(service, stale)
    assert get_project_transfer(service, completed.id) == completed
    assert Path(completed.package.path).is_file()
