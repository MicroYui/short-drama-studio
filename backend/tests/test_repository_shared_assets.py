"""Observe actual root writes while episodes keep shared content and concurrent edits."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from short_drama.models import ReferencePack
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioEpisodeCreate, StudioSceneSave, StudioTimelineSave


@pytest.fixture
def family(tmp_path: Path) -> tuple[ShortDramaService, str, str]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    root = service.initialize_fixture().document
    root.reference_packs.append(ReferencePack(id="shared-pack", project_id=root.project.id,
        subject_asset_id=root.assets[0].id, name="共享参考图集", created_at=root.project.created_at,
        updated_at=root.project.updated_at))
    service.repository.save_project(root)
    child_id = service.studio.create_episode(root.project.id, StudioEpisodeCreate(title="第二集")).document.project.id
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.executescript("""
            CREATE TABLE observed_project_writes(project_id TEXT NOT NULL);
            CREATE TRIGGER observe_project_update AFTER UPDATE ON projects
            BEGIN INSERT INTO observed_project_writes(project_id) VALUES(NEW.id); END;
        """)
    return service, root.project.id, child_id


def _writes(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [row[0] for row in connection.execute("SELECT project_id FROM observed_project_writes")]


@pytest.mark.parametrize("edit", ["scene", "timeline", "normalized_shared_ids"])
def test_episode_content_save_does_not_write_unchanged_root(
    family: tuple[ShortDramaService, str, str], edit: str,
) -> None:
    service, root_id, child_id = family
    before = service.repository.get_project(root_id)
    events = service.repository.list_studio_events(root_id)
    if edit == "scene":
        service.studio.save_scene(child_id, StudioSceneSave(heading="新场次", screenplay_text="只修改本集正文"))
    elif edit == "timeline":
        service.studio.save_timeline(child_id, StudioTimelineSave(timeline={"timelines": []}))
    else:
        child = service.repository.get_project(child_id)
        assert child is not None
        child.project.episode_title = "本集标题"
        for asset in child.assets:
            asset.project_id = child_id
        for pack in child.reference_packs:
            pack.project_id = child_id
        service.repository.save_project(child)
    assert _writes(service.repository.database_path) == [child_id]
    assert service.repository.get_project(root_id) == before
    assert service.repository.list_studio_events(root_id) == events


@pytest.mark.parametrize("shared_field", ["assets", "reference_packs"])
def test_changed_shared_content_writes_root_once_and_stays_shared(
    family: tuple[ShortDramaService, str, str], shared_field: str,
) -> None:
    service, root_id, child_id = family
    child = service.repository.get_project(child_id)
    assert child is not None
    events = len(service.repository.list_studio_events(root_id))
    if shared_field == "assets":
        child.assets[0].name = "从第二集修改的人物"
    else:
        child.reference_packs[0].name = "从第二集修改的图集"
    service.repository.save_project(child)
    root = service.repository.get_project(root_id)
    assert root is not None
    assert root.assets == child.assets and root.reference_packs == child.reference_packs
    assert _writes(service.repository.database_path) == [root_id, child_id]
    assert [event.kind for event in service.repository.list_studio_events(root_id)[events:]] == ["assets.updated"]
    service.repository.save_project(child)
    assert _writes(service.repository.database_path) == [root_id, child_id, child_id]
    assert len(service.repository.list_studio_events(root_id)) == events + 1
    with sqlite3.connect(service.repository.database_path) as connection:
        raw = json.loads(connection.execute("SELECT document_json FROM projects WHERE id=?", (child_id,)).fetchone()[0])
    assert raw["assets"] == [] and raw["reference_packs"] == []


@pytest.mark.parametrize("change_shared_field", [False, True])
def test_late_episode_save_merges_latest_manual_shared_edits_without_needless_root_write(
    family: tuple[ShortDramaService, str, str], change_shared_field: bool,
) -> None:
    service, root_id, child_id = family
    late = service.repository.get_project(child_id)
    assert late is not None
    late.project.episode_title = "Agent 完成的本集标题"
    if change_shared_field:
        late.assets[0].name = "迟到的人物名称"
        late.assets[0].versions[0].descriptor = "Agent 补充的独立素材说明"

    def manual_save() -> None:
        # A second repository owns a different Python lock and SQLite connection.
        repository = ShortDramaRepository(service.repository.database_path)
        current = repository.get_project(root_id)
        assert current is not None
        current.project.name = "用户刚保存的项目名称"
        current.assets[0].name = "用户刚保存的人物名称"
        current.reference_packs[0].name = "用户刚保存的参考图集"
        repository.save_project(current)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(manual_save).result(timeout=10)
    events = len(service.repository.list_studio_events(root_id))
    service.repository.save_project(late)
    root = service.repository.get_project(root_id)
    assert root is not None
    assert root.project.name == "用户刚保存的项目名称"
    assert root.assets[0].name == "用户刚保存的人物名称"
    assert root.reference_packs[0].name == "用户刚保存的参考图集"
    assert late.project.episode_title == "Agent 完成的本集标题"
    assert late.assets == root.assets and late.reference_packs == root.reference_packs
    if change_shared_field:
        assert root.assets[0].versions[0].descriptor == "Agent 补充的独立素材说明"
    assert _writes(service.repository.database_path).count(root_id) == (2 if change_shared_field else 1)
    assert len(service.repository.list_studio_events(root_id)) == events + int(change_shared_field)
