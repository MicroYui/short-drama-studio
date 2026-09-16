"""Real SQLite migration, record writes, pagination and content/decision commits."""

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
from typing import cast
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.studio_changes import decide_change, page_changes, propose_changes, read_changes
from short_drama.studio_export import _save, get_export, list_exports
from short_drama.studio_media_recovery import relink_media
from short_drama.studio_models import StudioChangeDecision, StudioChangeRequest, StudioChangeSet, StudioExport, StudioTextChangeInput
from short_drama.studio_project_package import create_project_package, import_project_package
from short_drama.studio_trace_export import export_studio_trace
from tests.test_studio_shot_editorial import episode as episode


def _export(pid: str, identity: str = "export-1") -> StudioExport:
    return StudioExport(id=identity, project_id=pid, editorial_revision=1, width=320, height=180,
        fps=24, created_at="2026-09-16T00:00:00+00:00")


def _proposal(service: ShortDramaService, pid: str) -> StudioChangeSet:
    shot = service.get_project(pid).document.shots[0]
    return propose_changes(service, pid, StudioChangeRequest(title="镜头调整", items=[StudioTextChangeInput(
        target="shot", target_id=shot.id, field="camera", title="改为平视", before=shot.camera, after="平视，保持固定。")]))


def _raw(service: ShortDramaService, pid: str) -> str:
    with sqlite3.connect(service.repository.database_path) as connection:
        return cast(str, connection.execute("SELECT document_json FROM projects WHERE id=?", (pid,)).fetchone()[0])


def _legacy(service: ShortDramaService, pid: str) -> str:
    document = service.get_project(pid).document
    raw = document.model_dump_json()
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("UPDATE projects SET document_json=? WHERE id=?", (raw, pid))
        connection.execute("DELETE FROM studio_exports")
        connection.execute("DELETE FROM studio_changes")
        connection.execute("DELETE FROM schema_migrations WHERE version=12")
    return raw


def test_legacy_migration_is_complete_idempotent_and_does_not_change_content(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    _save(service, _export(pid))
    change = _proposal(service, pid)
    before = service.get_project(pid).document.model_dump(mode="json")
    legacy = _legacy(service, pid)
    assert '"studio_exports"' in legacy
    service.repository = ShortDramaRepository(service.repository.database_path)
    raw = json.loads(_raw(service, pid))
    assert "studio_exports" not in raw and "studio_changes" not in raw
    assert raw == {key: value for key, value in before.items() if key not in {"studio_exports", "studio_changes"}}
    assert service.get_project(pid).document.model_dump(mode="json") == before
    service.repository = ShortDramaRepository(service.repository.database_path)
    assert len(service.repository.list_studio_exports(pid)) == 1
    assert service.repository.get_studio_change(pid, change.id) == change


def test_migration_failure_rolls_back_records_and_embedded_removal(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    _save(service, _export(pid))
    _proposal(service, pid)
    legacy = _legacy(service, pid)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER fail_migration BEFORE UPDATE OF document_json ON projects "
                           "BEGIN SELECT RAISE(ABORT, 'migration write failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="migration write failed"):
        ShortDramaRepository(service.repository.database_path)
    with sqlite3.connect(service.repository.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM studio_exports").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM studio_changes").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM schema_migrations WHERE version=12").fetchone()[0] == 0
        assert connection.execute("SELECT document_json FROM projects WHERE id=?", (pid,)).fetchone()[0] == legacy
        connection.execute("DROP TRIGGER fail_migration")
    restored = ShortDramaRepository(service.repository.database_path)
    assert len(restored.list_studio_exports(pid)) == len(restored.list_studio_changes(pid)) == 1


def test_migration_keeps_a_newer_independent_record(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    record = _export(pid)
    _save(service, record)
    _legacy(service, pid)
    record.status = "completed"
    _save(service, record)
    service.repository = ShortDramaRepository(service.repository.database_path)
    assert get_export(service, pid, record.id).status == "completed"
    assert service.get_project(pid).document.studio_exports[0]["status"] == "completed"


class _ObservedRepository(ShortDramaRepository):
    def __init__(self, path: Path) -> None:
        self.statements: list[str] = []
        super().__init__(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with super()._connection() as connection:
            connection.set_trace_callback(self.statements.append)
            yield connection


def test_record_reads_progress_proposals_and_rejections_do_not_rewrite_body(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    before = _raw(service, pid)
    event_cursor = max((event.id for event in service.repository.list_studio_events(pid)), default=0)
    observed = _ObservedRepository(service.repository.database_path)
    service.repository = observed
    observed.statements.clear()
    record = _export(pid)
    _save(service, record)
    record.progress = "实际进度已更新"
    _save(service, record)
    assert get_export(service, pid, record.id).progress == record.progress
    assert list_exports(service, pid).exports == [record]
    assert not any("document_json" in statement for statement in observed.statements)
    change = _proposal(service, pid)
    decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="reject"))
    assert _raw(service, pid) == before
    assert not any(statement.lstrip().upper().startswith(("UPDATE PROJECTS", "INSERT INTO PROJECTS")) for statement in observed.statements)
    assert not any(event.kind == "content.updated" for event in service.repository.list_studio_events(pid, after=event_cursor))
    observed.statements.clear()
    assert read_changes(service, pid)[0].items[0].status == "rejected"
    assert page_changes(service, pid, pending_only=True).changes == []
    assert not any("document_json" in statement for statement in observed.statements)


def test_stale_snapshots_cannot_overwrite_or_delete_record_history(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    record = _export(pid)
    _save(service, record)
    change = _proposal(service, pid)
    stale = service.get_project(pid).document
    record.status, record.progress = "completed", "已完成"
    _save(service, record)
    decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="reject"))
    stale.project.name = "旧快照中的无关重命名"
    stale.studio_changes = []
    service.repository.save_project(stale)
    assert get_export(service, pid, record.id).status == "completed"
    assert read_changes(service, pid)[0].items[0].status == "rejected"
    assert stale.studio_exports[0]["status"] == "completed" and stale.studio_changes[0]["id"] == change.id
    stale.project.name = "保存之后尚未保存的新名字"
    stale.shots[0].camera = "保存之后未保存的机位"
    saved = service.get_project(pid).document
    assert saved.project.name == "旧快照中的无关重命名"
    assert saved.shots[0].camera != stale.shots[0].camera
    # A snapshot loaded before independent history existed must not remove it.
    minimal = service.repository.get_project(pid, include_studio_records=False)
    assert minimal is not None and not minimal.studio_exports
    service.repository.save_project(minimal)
    assert len(service.repository.list_studio_exports(pid)) == len(read_changes(service, pid)) == 1


def test_fast_save_keeps_detached_snapshot_and_concurrent_script_protection(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    document.shots[0].camera = "本次直接保存的机位"
    service.repository.save_project(document)
    document.shots[0].camera = "尚未保存的下一次改动"
    assert service.get_project(pid).document.shots[0].camera == "本次直接保存的机位"
    assert document.loaded_snapshot is not None
    assert "尚未保存的下一次改动" not in json.dumps(document.loaded_snapshot, ensure_ascii=False)

    stale = service.get_project(pid).document
    current = service.get_project(pid).document
    old = current.script_versions[-1]
    next_script = old.model_copy(deep=True, update={"id": "new-current-script", "version": old.version + 1,
        "screenplay_text": "另一处已经保存的新剧本"})
    current.script_versions.append(next_script)
    current.project.current_script_version_id = next_script.id
    service.repository.save_project(current)
    stale.script_versions[-1].screenplay_text = "旧快照错误回写旧剧本"
    stale.project.name = "不相关的重命名保留"
    service.repository.save_project(stale)
    saved = service.get_project(pid).document
    assert saved.project.current_script_version_id == next_script.id
    assert saved.script_versions[-1].screenplay_text == next_script.screenplay_text
    assert next(script for script in saved.script_versions if script.id == old.id).screenplay_text == old.screenplay_text
    assert saved.project.name == "不相关的重命名保留"
    assert f"/script_versions/{old.id}" in stale.save_conflicts


def test_caught_nested_transaction_error_still_rolls_back(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    with pytest.raises(RuntimeError, match="atomic transaction"):
        with service.repository.atomic():
            service.repository.save_studio_export(_export(pid))
            try:
                with service.repository.atomic():
                    raise ValueError("nested domain failure")
            except ValueError:
                pass
    assert service.repository.list_studio_exports(pid) == []


def test_equal_timestamp_pagination_and_legacy_api_do_not_skip_rows(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    template = _proposal(service, pid)
    for index in range(7):
        service.repository.save_studio_export(_export(pid, f"export-{index}"))
        service.repository.save_studio_change(template.model_copy(deep=True, update={
            "id": f"change-{index}", "created_at": "2026-09-16T00:00:00+00:00"}))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        for endpoint, key, expected in (("exports", "exports", 7), ("changes/page", "changes", 8)):
            identities: list[str] = []
            before: str | None = None
            while True:
                parameters = {"limit": "2", **({"before": before} if before else {})}
                result = client.get(f"/api/short-drama/studio/projects/{pid}/{endpoint}", params=parameters)
                assert result.status_code == 200, result.text
                page = result.json()
                identities.extend(item["id"] for item in page[key])
                before = page["next_cursor"]
                if before is None:
                    break
            assert len(identities) == len(set(identities)) == expected
        legacy = client.get(f"/api/short-drama/studio/projects/{pid}/changes")
        assert isinstance(legacy.json(), list) and len(legacy.json()) == 8
        invalid = client.get(f"/api/short-drama/studio/projects/{pid}/exports", params={"before": "invalid"})
        assert invalid.status_code == 422


@pytest.mark.parametrize("target", ["scene", "shot"])
def test_accept_failure_rolls_back_content_take_invalidation_and_decision(
    episode: tuple[ShortDramaService, str, list[str]], target: str,
) -> None:
    service, pid, _ = episode
    document = service.get_project(pid).document
    scene = document.script_versions[-1].structured_scenes[0]
    request = StudioTextChangeInput(target="scene", target_id=scene.id, field="heading", title="场景标题",
        before=scene.heading, after=scene.heading + "（修改）") if target == "scene" else StudioTextChangeInput(
            target="shot", target_id=document.shots[0].id, field="camera", title="机位",
            before=document.shots[0].camera, after="平视，保持固定。")
    change = propose_changes(service, pid, StudioChangeRequest(title="审阅原子提交", items=[request]))
    before = _raw(service, pid)
    takes = service.repository.list_takes(pid)
    events = service.repository.list_studio_events(pid)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER fail_decision BEFORE UPDATE ON studio_changes "
                           "BEGIN SELECT RAISE(ABORT, 'decision write failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="decision write failed"):
        decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    assert _raw(service, pid) == before
    assert service.repository.list_takes(pid) == takes
    assert service.repository.list_studio_events(pid) == events
    assert read_changes(service, pid)[0].items[0].status == "pending"
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("DROP TRIGGER fail_decision")
    accepted = decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    assert accepted.change.items[0].status == "accepted"
    assert _raw(service, pid) != before


def test_family_relink_record_paths_rollback_and_package_trace_roundtrip(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, _ = episode
    old = Path(service.repository.list_takes(pid)[0].media_path)
    moved = tmp_path / "moved.mp4"
    shutil.copy2(old, moved)
    record = _export(pid)
    record.status, record.video_path, record.clean_video_path = "completed", str(old), str(old)
    _save(service, record)
    change = _proposal(service, pid)
    before = _raw(service, pid)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER fail_relink BEFORE INSERT ON studio_events WHEN NEW.kind='assets.updated' "
                           "BEGIN SELECT RAISE(ABORT, 'relink event failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="relink event failed"):
        relink_media(service, pid, str(old), moved)
    assert get_export(service, pid, record.id) == record
    assert read_changes(service, pid) == [change]
    assert _raw(service, pid) == before
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("DROP TRIGGER fail_relink")
    relinked = relink_media(service, pid, str(old), moved)
    assert get_export(service, pid, record.id).video_path == relinked.path
    trace = export_studio_trace(service, pid)
    with ZipFile(trace) as archive:
        exported = json.loads(archive.read("project.json"))["document"]
        assert len(exported["studio_exports"]) == len(exported["studio_changes"]) == 1
    package = create_project_package(service, pid)
    destination = ShortDramaService(database_path=tmp_path / "other.sqlite", workspace_root=tmp_path / "other-media")
    imported = import_project_package(destination, Path(package.path))
    imported_record = get_export(destination, pid, record.id)
    assert imported_record.video_path and Path(imported_record.video_path).is_file()
    assert imported_record.video_path != relinked.path
    assert read_changes(destination, pid) == [change]
    assert imported.project.document.studio_exports[0]["id"] == record.id
    assert "studio_exports" not in json.loads(_raw(destination, pid))
