"""Persistent export controls verified with the local Node and FFmpeg runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
import time
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
import imageio_ffmpeg
import psutil
from pydantic import JsonValue
import pytest

from _routes.studio import router
from short_drama.deps import get_short_drama_service
from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorClip, EditorProject, read_editorial
from short_drama.studio_export import (
    _ACTIVE, _ACTIVE_LOCK, _ExportCancelled, _ExportControl, _control, _key,
    _run_process, _save, cancel_export, get_export, list_exports,
    recover_interrupted_exports, start_export,
)
from short_drama.studio_media import import_studio_media
from short_drama.studio_models import StudioExport, StudioExportRequest, StudioTimelineSave


def _video_project(tmp_path: Path, *, duration: int = 2) -> tuple[ShortDramaService, str, int]:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    project_id = service.initialize_fixture().document.project.id
    source = tmp_path / "source.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s=320x320:r=24:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True)
    imported = import_studio_media(service, project_id, source, name="本地短片", kind=AssetKind.REFERENCE, asset_id=None)
    media = imported.document.assets[-1].versions[-1]
    project = EditorProject.model_validate(read_editorial(service, project_id).project)
    project.timelines[0].clips = [EditorClip(id="local-video", assetId=media.id, type="video",
        startTime=0, duration=duration, trimStart=0, trackIndex=0)]
    project.timelines[0].subtitles = []
    saved = service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=0,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    revision = cast(int, (saved.project.document.editorial_timeline or {})["revision"])
    return service, project_id, revision


def _app(service: ShortDramaService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return app


def _alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_completed_local_export_lists_after_reopen_and_cancel_keeps_files(tmp_path: Path) -> None:
    service, project_id, revision = _video_project(tmp_path)
    exported = start_export(service, project_id,
        StudioExportRequest(expected_revision=revision, width=320, height=320, subtitles=False), background=False)
    assert exported.status == "completed", exported.error
    assert exported.video_path and exported.finished_at and exported.updated_at
    path = Path(exported.video_path)
    original = path.read_bytes()
    assert original and exported.duration_seconds == 2 and not exported.subtitles
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    with TestClient(_app(reopened)) as client:
        response = client.get(f"/studio/projects/{project_id}/exports?limit=1")
        assert response.status_code == 200
        assert response.json()["exports"] == [exported.model_dump(mode="json")]
        cancelled = client.post(f"/studio/projects/{project_id}/exports/{exported.id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == exported.model_dump(mode="json")
        assert client.get(f"/studio/projects/{project_id}/exports?limit=0").status_code == 422
        assert client.post(f"/studio/projects/{project_id}/exports/not-present/cancel").status_code == 404
    assert path.read_bytes() == original


def test_cancel_stops_real_node_and_ffmpeg_and_leaves_other_process_alive(tmp_path: Path) -> None:
    service, project_id, revision = _video_project(tmp_path, duration=8)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    control: _ExportControl | None = None
    try:
        with TestClient(_app(service)) as client:
            created = client.post(f"/studio/projects/{project_id}/exports", json={
                "expected_revision": revision, "width": 1920, "height": 1920, "fps": 60, "subtitles": False})
            assert created.status_code == 200
            export_id = str(created.json()["id"])
            control = _control(service, project_id, export_id)
            assert control is not None
            children: list[psutil.Process] = []
            parent: psutil.Process | None = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with control.process_lock:
                    process = control.process
                    if process is not None and process.poll() is None:
                        parent = psutil.Process(process.pid)
                        children = parent.children(recursive=True)
                if children:
                    break
                time.sleep(0.01)
            assert parent is not None and children, "The real render must start its FFmpeg child before cancellation"
            cancelled = client.post(f"/studio/projects/{project_id}/exports/{export_id}/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelling"
            assert control.done.wait(10), "Cancellation did not stop the render"
            latest = client.get(f"/studio/projects/{project_id}/exports/{export_id}").json()
            assert latest["status"] == "cancelled" and latest["finished_at"]
            assert latest["video_path"] is None
            assert not _alive(parent) and not any(_alive(child) for child in children)
            assert unrelated.poll() is None
            assert client.post(f"/studio/projects/{project_id}/exports/{export_id}/cancel").json() == latest
            assert client.get(f"/studio/projects/{project_id}/exports").json()["exports"][0] == latest
        # A cancelled record must release the export slot for a real retry.
        retried = start_export(service, project_id,
            StudioExportRequest(expected_revision=revision, width=320, height=320), background=False)
        assert retried.status == "completed", retried.error
        assert retried.id != export_id and retried.video_path
        assert get_export(service, project_id, export_id).status == "cancelled"
    finally:
        if control is not None and not control.done.is_set():
            control.cancelled.set()
            from short_drama.studio_export import _stop_process
            _stop_process(control)
            control.done.wait(5)
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_cancel_before_process_creation_never_launches_command(tmp_path: Path) -> None:
    control = _ExportControl()
    control.cancelled.set()
    marker = tmp_path / "unexpected.txt"
    with pytest.raises(_ExportCancelled):
        _run_process(control, [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)])
    assert not marker.exists() and control.process is None


def test_cancel_wins_completion_race_without_deleting_completed_files(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    project_id = service.initialize_fixture().document.project.id
    record = StudioExport(id="completion-race", project_id=project_id, editorial_revision=1,
        width=320, height=320, fps=24, created_at=datetime.now(UTC).isoformat())
    control = _ExportControl()
    key = _key(service, project_id, record.id)
    with _ACTIVE_LOCK:
        _ACTIVE[key] = control
    try:
        _save(service, record)
        assert cancel_export(service, project_id, record.id).status == "cancelling"
        file = tmp_path / "finished-render.mp4"
        file.write_bytes(b"saved output is preserved")
        record.status, record.video_path, record.clean_video_path = "completed", str(file), str(file)
        _save(service, record)
        assert record.status == "cancelled"
        assert file.read_bytes() == b"saved output is preserved"
        saved = get_export(service, project_id, record.id)
        record.status, record.progress = "running", "late progress"
        _save(service, record)
        assert get_export(service, project_id, record.id) == saved
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(key, None)


def test_recovery_marks_unfinished_records_failed_and_keeps_history_order(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    for index, status in enumerate(("running", "cancelling", "cancelled", "completed", "failed")):
        document.studio_exports.append({"id": f"old-{index}", "project_id": document.project.id,
            "editorial_revision": 1, "width": 320, "height": 320, "fps": 24, "status": status,
            "created_at": f"2026-09-15T00:00:0{index}+00:00"})
    service.repository.save_project(document)
    recover_interrupted_exports(service)
    result = list_exports(service, document.project.id)
    assert [record.id for record in result.exports] == ["old-4", "old-3", "old-2", "old-1", "old-0"]
    assert [record.status for record in result.exports] == ["failed", "completed", "cancelled", "failed", "failed"]
    assert all(record.finished_at and "已中断" in record.progress for record in result.exports[-2:])
