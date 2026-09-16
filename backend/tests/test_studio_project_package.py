"""Move the source away, import elsewhere, edit and render the actual local video."""

import json
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import cast
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue
import pytest

from _routes.studio import router
from short_drama.deps import get_short_drama_service
from short_drama.models import AssetMediaType, EnqueueRequest, TaskStatus
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioEpisodeCreate, StudioExportRequest, StudioProjectCreate, StudioTimelineSave
from short_drama.studio_project_package import create_project_package, import_project_package
from short_drama.studio_service import StudioContentConflict
from short_drama.video_edit_models import LipSyncRequest, VideoEditRun, VideoEditSourceFile
from tests.test_studio_export_lifecycle import _video_project


def _client(service: ShortDramaService) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/short-drama")
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return TestClient(app)


def test_collect_download_move_import_reopen_edit_and_real_export(tmp_path: Path) -> None:
    source_folder = tmp_path / "source"
    source_folder.mkdir()
    service, root_id, _revision = _video_project(source_folder)
    document = service.get_project(root_id).document
    # Local mock-media provenance creates a real Take file, with no paid Provider.
    service.enqueue(root_id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = service.drain(root_id, 50).project.takes[0]
    task = service.repository.get_task(take.task_id)
    assert task is not None
    task.status = TaskStatus.RUNNING  # A source run must not be revived by a content import.
    service.repository.save_task(task)
    child = service.studio.create_episode(root_id, StudioEpisodeCreate(title="第二集迁移"))
    root_editor = read_editorial(service, root_id)
    child_editor = {**root_editor.project, "id": child.document.project.id}
    service.studio.save_timeline(child.document.project.id,
        StudioTimelineSave(expected_revision=0, timeline={"project": child_editor}))
    service.repository.append_studio_event(root_id, "test.production_record", {"text": "Original production decision"})
    run = service.agent_runtime.create_run(project_id=root_id, objective="Original production process", root_agent="leader")
    trace_folder = service.workspace_root / root_id / "studio-trace"
    trace_folder.mkdir(parents=True, exist_ok=True)
    (trace_folder / "local-trace.json").write_text(json.dumps({"request": "original model input", "response": "original output"}))
    with _client(service) as client:
        response = client.post(f"/api/short-drama/studio/projects/{child.document.project.id}/package")
        assert response.status_code == 200, response.text
        package = response.json()
        assert package["episode_count"] == 2 and package["media_count"] > 0
        download = client.get(package["download_url"])
        assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
        portable = tmp_path / "portable.zip"
        portable.write_bytes(download.content)
    with ZipFile(portable) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["root_project_id"] == root_id
        stored = json.loads(archive.read(manifest["episodes"][0]["document_path"]))
        assert stored["takes"][0]["media_path"].startswith("media/")
        assert stored["take_tasks"][0]["status"] == "RUNNING"
        assert f"traces/0/agent/{run.id}/snapshot.json" in archive.namelist()
        assert "Original production decision" in archive.read("traces/0/conversation.jsonl").decode()
        assert "original model input" in archive.read("traces/0/model-trace/local-trace.json").decode()
    shutil.move(str(source_folder), str(tmp_path / "source-offline"))
    destination = ShortDramaService(database_path=tmp_path / "destination.sqlite3", workspace_root=tmp_path / "destination-media")
    with _client(destination) as client:
        imported = client.post("/api/short-drama/studio/projects/import-package",
            files={"file": ("portable.zip", portable.read_bytes(), "application/zip")})
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert set(result["episode_ids"]) == {root_id, child.document.project.id}
        assert result["project"]["document"]["project"]["id"] == root_id
        assert Path(result["trace_path"]).is_dir()
        duplicate = client.post("/api/short-drama/studio/projects/import-package",
            files={"file": ("portable.zip", portable.read_bytes(), "application/zip")})
        assert duplicate.status_code == 409
    imported_take = destination.repository.get_take(take.id)
    assert imported_take is not None and Path(imported_take.media_path).is_file()
    assert imported_take.is_selected == take.is_selected
    imported_task = destination.repository.get_task(task.id)
    assert imported_task is not None and imported_task.status == TaskStatus.CANCELLED
    assert imported_task.error_code == "PROJECT_IMPORTED"
    assert destination.repository.list_provider_runs(root_id) == []
    assert destination.agent_runtime.store.list_runs(root_id) == [] and destination.repository.list_studio_jobs() == []
    reopened = ShortDramaService(database_path=destination.repository.database_path, workspace_root=destination.workspace_root)
    for episode_id in result["episode_ids"]:
        editorial = read_editorial(reopened, episode_id)
        project = EditorProject.model_validate(editorial.project)
        assert all(Path(asset.path).is_file() for asset in project.assets)
        clip = project.timelines[0].clips[0]
        clip.trimStart, clip.duration = 0.25, 1.5
        saved = reopened.studio.save_timeline(episode_id, StudioTimelineSave(expected_revision=editorial.revision,
            timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
        revision = cast(int, (saved.project.document.editorial_timeline or {})["revision"])
        exported = start_export(reopened, episode_id, StudioExportRequest(expected_revision=revision,
            width=320, height=320, subtitles=False), background=False)
        assert exported.status == "completed", exported.error
        assert exported.duration_seconds == 1.5 and exported.video_path and Path(exported.video_path).is_file()


def test_package_reports_missing_history_without_blocking_usable_current_export(tmp_path: Path) -> None:
    service, root_id, revision = _video_project(tmp_path / "source")
    document = service.repository.get_project(root_id)
    assert document is not None
    version = document.assets[0].versions[0]
    version.media_type, version.local_path, version.external_url = AssetMediaType.IMAGE, str(tmp_path / "missing.png"), None
    service.repository.save_project(document)
    package = create_project_package(service, root_id)
    assert len(package.missing_media) == 1 and package.missing_media[0].source_path.endswith("missing.png")
    destination = ShortDramaService(database_path=tmp_path / "other.sqlite3", workspace_root=tmp_path / "other-media")
    result = import_project_package(destination, Path(package.path))
    assert result.missing_media == package.missing_media
    missing = result.project.document.assets[0].versions[0].local_path
    assert missing and not Path(missing).exists() and str(destination.workspace_root) in missing
    exported = start_export(destination, root_id, StudioExportRequest(expected_revision=revision,
        width=320, height=320, subtitles=False), background=False)
    assert exported.status == "completed", exported.error


def test_duplicate_take_provenance_rolls_back_whole_import_and_new_media(tmp_path: Path) -> None:
    source, root_id, _revision = _video_project(tmp_path / "source")
    document = source.get_project(root_id).document
    source.enqueue(root_id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = source.drain(root_id, 50).project.takes[0]
    task = source.repository.get_task(take.task_id)
    assert task is not None
    package = create_project_package(source, root_id)
    destination = ShortDramaService(database_path=tmp_path / "other.sqlite3", workspace_root=tmp_path / "other-media")
    unrelated = destination.studio.create_project(StudioProjectCreate(name="Keep existing work"))
    other_id = unrelated.document.project.id
    existing_task = task.model_copy(update={"project_id": other_id})
    existing_take = take.model_copy(update={"project_id": other_id})
    destination.repository.save_task(existing_task)
    destination.repository.save_take(existing_take)
    before = destination.repository.get_project(other_id)
    with pytest.raises(StudioContentConflict):
        import_project_package(destination, Path(package.path))
    assert destination.repository.get_project(root_id) is None
    assert destination.repository.get_project(other_id) == before
    assert destination.repository.get_take(take.id) == existing_take
    assert list((destination.workspace_root / "imported-projects").glob("import-*")) == []


def test_incomplete_package_is_rejected_before_any_project_is_saved(tmp_path: Path) -> None:
    source, root_id, _revision = _video_project(tmp_path / "source")
    package = create_project_package(source, root_id)
    incomplete = tmp_path / "incomplete.zip"
    with ZipFile(package.path) as original, ZipFile(incomplete, "w") as changed:
        for info in original.infolist():
            if not info.filename.startswith("media/"):
                changed.writestr(info.filename, original.read(info))
    destination = ShortDramaService(database_path=tmp_path / "other.sqlite3", workspace_root=tmp_path / "other-media")
    with pytest.raises(ValueError, match="缺少已声明收集的媒体"):
        import_project_package(destination, incomplete)
    assert destination.repository.list_projects() == []
    assert list((destination.workspace_root / "imported-projects").glob("import-*")) == []


def test_video_edit_runs_keep_full_trace_and_portable_sources_but_never_resume_after_import(tmp_path: Path) -> None:
    source, root_id, _revision = _video_project(tmp_path / "source")
    document = source.get_project(root_id).document
    source.enqueue(root_id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = source.drain(root_id, 50).project.takes[0]
    task = source.repository.get_task(take.task_id)
    assert task is not None
    now = datetime.now(UTC).isoformat()
    run = VideoEditRun(id="edit-finished", task_id=task.id, project_id=root_id, shot_id=document.shots[0].id,
        status="SUCCEEDED", request=LipSyncRequest(shot_id=document.shots[0].id, timeline_id="timeline",
            clip_id="clip", start_seconds=0, end_seconds=2, expected_editorial_revision=1, intent="保留画面并修复口型"),
        source_take_id=take.id, source_media_path=take.media_path, source_in_seconds=0, source_out_seconds=2,
        source_speed=1, source_reversed=False,
        source_files=[VideoEditSourceFile(path=take.media_path, size_bytes=1, modified_ns=1)],
        target_clip={"asset": {"path": take.media_path}}, target_track={},
        audio_clip={"asset": {"path": take.media_path}}, audio_track={},
        audio_media_path=take.media_path, audio_asset_type="video", baseline={"source": {"path": take.media_path}},
        prepared_video_path=take.media_path, prepared_audio_path=take.media_path,
        candidate_media_path=take.media_path, candidate_take_id=take.id,
        provider_task_id="original-remote-id", submission_attempt_state="ACKNOWLEDGED",
        latest_remote={"original_status": "finished"}, created_at=now, updated_at=now)
    source.repository.save_video_edit_run(run)
    pending_task = task.model_copy(update={"id": "pending-edit-task", "status": TaskStatus.RUNNING})
    source.repository.save_task(pending_task)
    pending = run.model_copy(update={"id": "edit-pending", "task_id": pending_task.id, "status": "RUNNING"})
    source.repository.save_video_edit_run(pending)
    package = create_project_package(source, root_id)
    with ZipFile(package.path) as archive:
        stored = json.loads(archive.read("projects/0.json"))
        assert len(stored["video_edit_runs"]) == 2
        trace = json.loads(archive.read("traces/0/providers/video-edit/edit-pending.json"))
        assert trace["status"] == "RUNNING" and trace["provider_task_id"] == "original-remote-id"
    destination = ShortDramaService(database_path=tmp_path / "destination.sqlite3", workspace_root=tmp_path / "destination")
    import_project_package(destination, Path(package.path))
    completed = destination.repository.get_video_edit_run(run.id)
    paused = destination.repository.get_video_edit_run_for_task(pending.task_id)
    assert completed is not None and completed.status == "SUCCEEDED"
    assert paused is not None and paused.status == "PAUSED" and paused.last_error_code == "PROJECT_IMPORTED"
    for imported in destination.repository.list_video_edit_runs(root_id):
        assert imported.provider_task_id is None and imported.next_poll_at is None
        assert imported.latest_remote == run.latest_remote  # The actual result record is not erased.
        for path in (imported.source_media_path, imported.audio_media_path, imported.prepared_video_path,
                     imported.prepared_audio_path, imported.candidate_media_path):
            assert path and Path(path).is_relative_to(destination.workspace_root) and Path(path).is_file()
        assert imported.target_clip["asset"] == {"path": imported.source_media_path}
        assert imported.baseline == {"source": {"path": imported.source_media_path}}
        stat = Path(imported.source_files[0].path).stat()
        assert (imported.source_files[0].size_bytes, imported.source_files[0].modified_ns) == (stat.st_size, stat.st_mtime_ns)
    reopened = ShortDramaService(database_path=destination.repository.database_path, workspace_root=destination.workspace_root)
    assert reopened.repository.get_video_edit_run(pending.id) == paused
    assert reopened.repository.get_project(root_id) is not None
    assert reopened.repository.list_studio_jobs() == [] and reopened.repository.list_provider_runs(root_id) == []
