"""Real repository and HTTP coverage for current episode content."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.models import ProjectDocument
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioEpisodeCreate, StudioSceneSave, StudioTimelineSave


@pytest.fixture
def studio(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")


def test_episodes_share_one_asset_inventory_and_keep_independent_content(studio: ShortDramaService) -> None:
    first = studio.initialize_fixture().document
    second = studio.studio.create_episode(first.project.id, StudioEpisodeCreate(title="第二集"))
    second_id = second.document.project.id
    assert second.document.project.parent_project_id == first.project.id
    assert second.document.project.episode_number == 2
    assert second.document.assets == first.assets
    assert second.document.shots == []
    assert second.document.script_versions[-1].structured_scenes == []
    assert len(studio.list_projects().projects) == 1
    assert [item.id for item in studio.studio.episodes(second_id).episodes] == [first.project.id, second_id]

    from_child = studio.repository.get_project(second_id)
    assert from_child is not None
    from_child.assets[0].name = "共享人物修改"
    studio.repository.save_project(from_child)
    assert studio.get_project(first.project.id).document.assets[0].name == "共享人物修改"

    with sqlite3.connect(studio.repository.database_path) as connection:
        row = connection.execute("SELECT document_json FROM projects WHERE id=?", (second_id,)).fetchone()
    assert json.loads(row[0])["assets"] == []
    assert json.loads(row[0])["reference_packs"] == []

    saved = studio.studio.save_scene(second_id, StudioSceneSave(heading="第二集的新场次", screenplay_text="清晨，人物走进教室。", lines=[]))
    assert saved.project.document.script_versions[-1].structured_scenes[0].heading == "第二集的新场次"
    assert studio.get_project(first.project.id).document.script_versions == first.script_versions

    restarted = ShortDramaService(database_path=studio.repository.database_path, workspace_root=studio.workspace_root)
    assert len(restarted.studio.episodes(first.project.id).episodes) == 2
    assert restarted.get_project(second_id).document.assets[0].name == "共享人物修改"


def test_late_agent_write_keeps_user_text_and_merges_unrelated_changes(studio: ShortDramaService) -> None:
    initial = studio.initialize_fixture().document
    project_id = initial.project.id
    agent_copy = studio.repository.get_project(project_id)
    assert agent_copy is not None
    script = agent_copy.script_versions[-1]
    scene = script.structured_scenes[0]
    scene_id = scene.id
    studio.studio.save_scene(project_id, StudioSceneSave(scene_id=scene_id, heading=scene.heading, screenplay_text="用户已经写好的正文。"))
    scene.screenplay_text = "晚到的模型文本。"
    agent_copy.shots[0].camera = "平稳向前推进"
    studio.repository.save_project(agent_copy)
    saved = studio.get_project(project_id).document
    assert saved.script_versions[-1].structured_scenes[0].screenplay_text == "用户已经写好的正文。"
    assert saved.shots[0].camera == "平稳向前推进"
    assert agent_copy._save_conflicts
    assert agent_copy.script_versions[-1].structured_scenes[0].screenplay_text == "用户已经写好的正文。"


def test_late_root_save_keeps_asset_edit_from_another_episode(studio: ShortDramaService) -> None:
    first = studio.initialize_fixture().document
    second = studio.studio.create_episode(first.project.id, StudioEpisodeCreate())
    root_copy = studio.repository.get_project(first.project.id)
    child_copy = studio.repository.get_project(second.document.project.id)
    assert root_copy is not None and child_copy is not None
    child_copy.assets[0].versions[0].descriptor = "用户修正后的素材说明"
    studio.repository.save_project(child_copy)
    root_copy.project.name = "新的项目名称"
    studio.repository.save_project(root_copy)
    assert studio.get_project(second.document.project.id).document.assets[0].versions[0].descriptor == "用户修正后的素材说明"


def test_scene_and_timeline_http_save_reject_stale_editor_without_losing_content(studio: ShortDramaService) -> None:
    first = studio.initialize_fixture().document
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: studio
    base = f"/api/short-drama/studio/projects/{first.project.id}"
    scene = first.script_versions[-1].structured_scenes[0]
    with TestClient(app) as client:
        saved = client.put(base + "/scene", json={"scene_id": scene.id, "heading": scene.heading, "screenplay_text": "新正文", "expected_text": scene.screenplay_text})
        assert saved.status_code == 200, saved.text
        conflict = client.put(base + "/scene", json={"scene_id": scene.id, "heading": scene.heading, "screenplay_text": "旧页面的文字", "expected_text": scene.screenplay_text})
        assert conflict.status_code == 409
        timeline = client.put(base + "/editorial", json={"expected_revision": 0, "timeline": {"timelines": [{"id": "edit-1", "clips": []}]}})
        assert timeline.status_code == 200, timeline.text
        conflict = client.put(base + "/editorial", json={"expected_revision": 0, "timeline": {"timelines": []}})
        assert conflict.status_code == 409
    current = studio.get_project(first.project.id).document
    assert current.script_versions[-1].structured_scenes[0].screenplay_text == "新正文"
    assert current.script_versions[-1].structure_stale_scene_ids == [scene.id]
    assert current.editorial_timeline is not None and current.editorial_timeline["revision"] == 1


def test_editorial_timelines_are_per_episode(studio: ShortDramaService) -> None:
    first = studio.initialize_fixture().document
    second = studio.studio.create_episode(first.project.id, StudioEpisodeCreate())
    studio.studio.save_timeline(second.document.project.id, StudioTimelineSave(timeline={"timelines": []}))
    assert studio.get_project(first.project.id).document.editorial_timeline is None
    assert studio.get_project(second.document.project.id).document.editorial_timeline == {"timelines": [], "revision": 1}


def test_legacy_project_is_the_first_episode_without_rewriting_original_files(studio: ShortDramaService) -> None:
    original = studio.initialize_fixture().document.model_dump(mode="json")
    for key in ("parent_project_id", "episode_number", "episode_title"):
        original["project"].pop(key, None)
    original.pop("editorial_timeline", None)
    restored = ProjectDocument.model_validate(original)
    assert restored.project.parent_project_id is None
    assert restored.project.episode_number == 1
    assert restored.editorial_timeline is None


def test_legacy_method_preferences_load_and_save_through_current_http_api(studio: ShortDramaService) -> None:
    original = studio.initialize_fixture().document.model_dump(mode="json")
    original["project"]["preferred_method_skill_ids"] = [
        "method.minimax_design.drama_series", "method.minimax_h3.brand_promo_video",
    ]
    project_id = original["project"]["id"]
    original_json = json.dumps(original, ensure_ascii=False)
    with sqlite3.connect(studio.repository.database_path) as connection:
        connection.execute("UPDATE projects SET document_json=? WHERE id=?", (original_json, project_id))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: studio
    base = f"/api/short-drama/studio/projects/{project_id}"
    with TestClient(app) as client:
        loaded = client.get(base)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["document"]["project"]["preferred_method_skill_ids"] == [
            "method.drama_series", "method.brand_promo",
        ]
        # Reading resolves names without rewriting the existing project record.
        with sqlite3.connect(studio.repository.database_path) as connection:
            row = connection.execute("SELECT document_json FROM projects WHERE id=?", (project_id,)).fetchone()
        assert row[0] == original_json
        saved = client.put(base + "/preferences", json={
            "method_skill_ids": ["method.minimax_h3.brand_promo_video"],
            "creative_direction": "保留用户的创作方向", "aspect_ratio": "9:16",
        })
        assert saved.status_code == 200, saved.text
        project = saved.json()["document"]["project"]
        assert project["preferred_method_skill_ids"] == ["method.brand_promo"]
        assert project["creative_direction"] == "保留用户的创作方向"
    assert studio.get_project(project_id).document.project.preferred_method_skill_ids == ["method.brand_promo"]
