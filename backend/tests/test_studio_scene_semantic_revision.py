"""A causal revision updates only its authored scenes and their actual dependents."""

from pathlib import Path

import pytest

from short_drama.models import AudioKind, SceneContinuitySummary, ScriptLine, StructuredScene
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioRelatedSceneUpdate, StudioSceneSave
from short_drama.studio_service import StudioContentConflict


@pytest.fixture
def story(tmp_path: Path) -> tuple[ShortDramaService, str]:
    service = ShortDramaService(database_path=tmp_path / "story.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    script = document.script_versions[-1]
    script.structured_scenes = [
        StructuredScene(id="setup", heading="食堂，午间", summary="特别陈列的鸡腿引起期待。",
                        narrative_purpose="为结尾赠送鸡腿做铺垫。", line_ids=[],
                        screenplay_text="他推着餐盘等饭，台面边另放着一盘卤鸡腿。"),
        StructuredScene(id="reaction", heading="食堂队伍", summary="同学惊讶。",
                        line_ids=[], screenplay_text="用户保存：同学张了张嘴，又闭上。"),
        StructuredScene(id="payoff", heading="食堂窗口", summary="阿姨送给他一只鸡腿。",
                        line_ids=["thanks"], screenplay_text="阿姨送来一只鸡腿。他说：谢谢。"),
    ]
    script.lines = [ScriptLine(id="thanks", speaker="学生", original_text="谢谢。", spoken_text="谢谢。",
                              subtitle_text="谢谢。", audio_kind=AudioKind.DIALOGUE)]
    for index, shot in enumerate(document.shots):
        shot.scene_id = ("setup", "reaction", "payoff", "setup")[min(index, 3)]
        shot.dialogue_line_ids = ["thanks"] if index == 2 else []
        shot.camera = f"用户保存的机位 {index}"
    service.repository.save_project(document)
    return service, document.project.id


def _revision(service: ShortDramaService, project_id: str) -> StudioSceneSave:
    document = service.get_project(project_id).document
    scenes = {scene.id: scene for scene in document.script_versions[-1].structured_scenes}
    return StudioSceneSave(
        scene_id="payoff", heading=scenes["payoff"].heading, expected_text=scenes["payoff"].screenplay_text,
        screenplay_text="阿姨从原菜盆里给他多加一勺肉。他说：谢谢。", summary="阿姨多加一勺肉。",
        narrative_purpose="用一次朴素回报回应他开场的愿望。",
        exit_state=SceneContinuitySummary(setting="食堂窗口", prop_states=["原餐盘多了一勺肉"]),
        affected_shot_ids=[document.shots[2].id],
        related_scene_updates=[StudioRelatedSceneUpdate(
            scene_id="setup", heading=scenes["setup"].heading, expected_text=scenes["setup"].screenplay_text,
            screenplay_text="他推着餐盘等饭，期待地看着菜盆。", summary="他期待今天能多吃一口。",
            narrative_purpose="建立日常愿望，呼应结尾多加的一勺。", lines=[],
            affected_shot_ids=[document.shots[0].id], reason="结尾不再送鸡腿，移除会承诺错误回报的特设陈列。",
        )],
    )


def test_setup_and_payoff_save_together_with_current_metadata_and_exact_shot_scope(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    before = service.get_project(project_id).document
    result = service.studio.save_scene(project_id, _revision(service, project_id))
    saved = result.project.document
    script = saved.script_versions[-1]
    assert script.version == before.script_versions[-1].version + 1
    assert result.changed_scene_ids == ["payoff", "setup"]
    assert set(result.changed_shot_ids) == {before.shots[0].id, before.shots[2].id}
    assert "鸡腿" not in script.screenplay_text
    assert "鸡腿" not in script.structured_scenes[0].summary
    assert script.structured_scenes[0].narrative_purpose == "建立日常愿望，呼应结尾多加的一勺。"
    assert script.structured_scenes[1] == before.script_versions[-1].structured_scenes[1]
    for original, current in zip(before.shots, saved.shots, strict=True):
        if original.id in result.changed_shot_ids:
            assert current.freshness.value == "STALE"
            assert current.revision == original.revision + 1
        else:
            assert current == original
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    persisted = restarted.get_project(project_id).document.script_versions[-1]
    assert persisted == script
    assert persisted.structured_scenes[2].exit_state is not None
    assert persisted.structured_scenes[2].exit_state.prop_states == ["原餐盘多了一勺肉"]


def test_stale_related_scene_preserves_the_entire_current_revision(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    revision = _revision(service, project_id)
    first = revision.related_scene_updates[0]
    service.studio.save_scene(project_id, StudioSceneSave(
        scene_id=first.scene_id, heading=first.heading, screenplay_text="用户刚改过的开场。", affected_shot_ids=[],
    ))
    before = service.get_project(project_id).document
    with pytest.raises(StudioContentConflict, match="关联修改均未保存"):
        service.studio.save_scene(project_id, revision)
    assert service.get_project(project_id).document == before


def test_related_scene_scope_and_line_ownership_validate_before_any_write(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    before = service.get_project(project_id).document
    revision = _revision(service, project_id)
    revision.related_scene_updates[0].affected_shot_ids = [before.shots[1].id]
    with pytest.raises(ValueError, match="受影响镜头必须属于"):
        service.studio.save_scene(project_id, revision)
    assert service.get_project(project_id).document == before
    revision = _revision(service, project_id)
    revision.related_scene_updates[0].lines = before.script_versions[-1].lines
    revision.related_scene_updates[0].screenplay_text += "谢谢。"
    with pytest.raises(ValueError, match="台词 ID 必须属于唯一场次"):
        service.studio.save_scene(project_id, revision)
    assert service.get_project(project_id).document == before


def test_changed_dialogue_is_included_even_when_author_preserves_visual_scope(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    revision = _revision(service, project_id)
    line = service.get_project(project_id).document.script_versions[-1].lines[0].model_copy(deep=True)
    line.spoken_text = line.original_text = line.subtitle_text = "这下够了。"
    revision.screenplay_text = "阿姨多加一勺肉。他说：这下够了。"
    revision.lines = [line]
    revision.affected_shot_ids = []
    result = service.studio.save_scene(project_id, revision)
    assert result.project.document.shots[2].id in result.changed_shot_ids
    assert result.project.document.script_versions[-1].lines == [line]


def test_registry_exposes_real_revision_result_and_current_scene_purpose(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    revision = _revision(service, project_id)
    # exclude_unset mirrors a real partial request: omitted state fields preserve
    # current facts, while explicit null is available to clear an obsolete one.
    result = service.tool_registry.invoke("script.save_scene", {
        "project_id": project_id, "request": revision.model_dump(mode="json", exclude_unset=True),
    })
    assert result.status.value == "SUCCEEDED", result.error
    assert result.output["changed_scene_ids"] == ["payoff", "setup"]
    assert len(result.output["changed_shot_ids"]) == 2
    current = service.tool_registry.invoke("artifact.read_current", {"project_id": project_id, "scene_id": "payoff"})
    content = current.output["content"]
    assert content["scene_index"][0]["narrative_purpose"] == "建立日常愿望，呼应结尾多加的一勺。"
    assert content["scene"]["exit_state"]["prop_states"] == ["原餐盘多了一勺肉"]
    assert "scene" in content and "screenplay_text" not in content["scene_index"][0]


def test_unchanged_scene_does_not_invalidate_media_or_require_semantic_metadata(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    before = service.get_project(project_id).document
    scene = before.script_versions[-1].structured_scenes[1]
    result = service.studio.save_scene(project_id, StudioSceneSave(
        scene_id=scene.id, heading=scene.heading, screenplay_text=scene.screenplay_text, expected_text=scene.screenplay_text,
    ))
    assert result.changed_scene_ids == [] and result.changed_shot_ids == []
    assert result.project.document == before


def test_explicit_null_can_remove_obsolete_authored_state(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    saved = service.studio.save_scene(project_id, _revision(service, project_id)).project.document
    scene = saved.script_versions[-1].structured_scenes[2]
    result = service.studio.save_scene(project_id, StudioSceneSave(
        scene_id=scene.id, heading=scene.heading, screenplay_text=scene.screenplay_text,
        expected_text=scene.screenplay_text, exit_state=None, affected_shot_ids=[],
    ))
    assert result.project.document.script_versions[-1].structured_scenes[2].exit_state is None
    assert result.changed_shot_ids == []
