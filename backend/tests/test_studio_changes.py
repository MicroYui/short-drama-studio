"""Review clicks change only the accepted text and preserve current edits."""
from pathlib import Path

import pytest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioSceneSave, StudioChangeRequest, StudioTextChangeInput, StudioChangeDecision
from short_drama.studio_changes import propose_changes, decide_change, list_changes
from short_drama.studio_service import StudioContentConflict


def test_independent_review_accept_reject_and_restart(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    project = service.initialize_fixture().document
    pid = project.project.id
    scene = project.script_versions[-1].structured_scenes[0]
    service.studio.save_scene(pid, StudioSceneSave(scene_id=scene.id, heading=scene.heading,
        screenplay_text="林舟低头算钱。\n\n阿姨点了点头。", lines=[]))
    request = StudioChangeRequest(title="加强反应", items=[
        StudioTextChangeInput(target="scene", target_id=scene.id, field="screenplay_text", title="主角随口报数",
            before="林舟低头算钱。", after="林舟随口报数，端盘就走。"),
        StudioTextChangeInput(target="scene", target_id=scene.id, field="screenplay_text", title="阿姨惊讶",
            before="阿姨点了点头。", after="阿姨愣住，饭勺当啷落下。"),
    ])
    change = propose_changes(service, pid, request)
    assert propose_changes(service, pid, request).id == change.id
    assert "端盘就走" not in service.get_project(pid).document.script_versions[-1].screenplay_text
    # An unrelated user addition survives both review decisions.
    current = service.get_project(pid).document.script_versions[-1].structured_scenes[0]
    service.studio.save_scene(pid, StudioSceneSave(scene_id=scene.id, heading=scene.heading,
        screenplay_text=current.screenplay_text + "\n\n用户补充的结尾。", lines=[]))
    result = decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    assert "林舟随口报数，端盘就走。" in result.project.document.script_versions[-1].screenplay_text
    assert "阿姨点了点头。" in result.project.document.script_versions[-1].screenplay_text
    assert "用户补充的结尾。" in result.project.document.script_versions[-1].screenplay_text
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    result = decide_change(service, pid, change.id, change.items[1].id, StudioChangeDecision(decision="reject"))
    assert [item.status for item in result.change.items] == ["accepted", "rejected"]
    assert "饭勺" not in result.project.document.script_versions[-1].screenplay_text
    version = result.project.document.project.current_script_version_id
    duplicate = decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    assert duplicate.project.document.project.current_script_version_id == version


def test_review_does_not_overwrite_overlapping_user_edit(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid, shot = document.project.id, document.shots[0]
    change = propose_changes(service, pid, StudioChangeRequest(title="修改机位", items=[StudioTextChangeInput(
        target="shot", target_id=shot.id, field="camera", title="固定机位", before=shot.camera, after="固定机位，保持平视。")]))
    latest = service.repository.get_project(pid)
    assert latest is not None
    latest.shots[0].camera = "用户已经设置的横移"
    service.repository.save_project(latest)
    with pytest.raises(StudioContentConflict):
        decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    saved = service.get_project(pid).document
    assert saved.shots[0].camera == "用户已经设置的横移"
    assert list_changes(saved)[0].items[0].status == "pending"


def test_accepted_scene_edit_preserves_unaffected_shot_in_same_scene(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    scene = document.script_versions[-1].structured_scenes[0]
    scene.screenplay_text = "\n".join(line.spoken_text for line in document.script_versions[-1].lines
                                      if line.id in scene.line_ids)
    affected, protected = document.shots[:2]
    affected.scene_id = protected.scene_id = scene.id
    protected.camera = "用户保存的固定机位"
    service.repository.save_project(document)
    before = protected.model_copy(deep=True)
    change = propose_changes(service, document.project.id, StudioChangeRequest(title="局部调整", items=[
        StudioTextChangeInput(target="scene", target_id=scene.id, field="screenplay_text", title="补充一个动作",
            before=scene.screenplay_text, after=scene.screenplay_text + "\n餐盘向前移动。",
            affected_shot_ids=[affected.id]),
    ]))
    result = decide_change(service, document.project.id, change.id, change.items[0].id,
                           StudioChangeDecision(decision="accept"))
    saved = {shot.id: shot for shot in result.project.document.shots}
    assert saved[protected.id] == before
    assert saved[affected.id].freshness.value == "STALE"
    assert saved[affected.id].revision == affected.revision + 1

    with pytest.raises(ValueError, match="受影响镜头必须属于"):
        propose_changes(service, document.project.id, StudioChangeRequest(title="范围错误", items=[
            StudioTextChangeInput(target="scene", target_id=scene.id, field="screenplay_text", title="错误引用",
                before="餐盘向前移动。", after="餐盘停止。", affected_shot_ids=["another-scene-shot"]),
        ]))


def test_asset_planning_read_works_before_formal_storyboard(tmp_path: Path) -> None:
    from short_drama.studio_models import StudioProjectCreate
    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.studio.create_project(StudioProjectCreate(name="先准备人物")).document
    context = service.get_asset_planning_context(document.project.id)
    assert context.production_spec is None and context.storyboard is None
    assert context.notes


@pytest.mark.parametrize("field", ["visual", "timeline_beats.0.visual"])
def test_accepted_mention_removal_updates_bindings_without_deleting_media(
    tmp_path: Path, field: str,
) -> None:
    from PIL import Image
    from short_drama.models import AssetMediaType, ShotReferenceCue, ShotTimelineBeat

    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    shot, asset = document.shots[0], document.assets[0]
    image = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "white").save(image)
    version = asset.versions[-1].model_copy(update={
        "id": "current-image", "media_type": AssetMediaType.IMAGE, "local_path": str(image),
    })
    asset.versions.append(version)
    asset.current_version_id = version.id
    text = "阿姨拿起@道具。镜头中还能看见@道具。"
    cues = [ShotReferenceCue(
        id=f"occurrence-{index}", subject_asset_id=asset.id, asset_version_id=version.id,
        alias="道具", field="visual", text_start=start, text_end=start + len("@道具"),
        purpose="这处画面使用的参考",
    ) for index, start in enumerate((text.index("@道具"), text.rindex("@道具")))]
    shot.references = []
    shot.timeline_beats = []
    if field == "visual":
        shot.visual, shot.references = text, cues
    else:
        shot.timeline_beats = [ShotTimelineBeat(
            start_seconds=0, end_seconds=shot.duration_seconds, visual=text,
            framing="中景", camera="固定", action="看向饭盘", references=cues,
        )]
    service.repository.save_project(document)

    def accept(before: str, after: str):
        change = propose_changes(service, document.project.id, StudioChangeRequest(title="修改动作", items=[
            StudioTextChangeInput(target="shot", target_id=shot.id, field=field,
                title="移除这处道具动作", before=before, after=after),
        ]))
        return decide_change(service, document.project.id, change.id, change.items[0].id,
                             StudioChangeDecision(decision="accept"))

    result = accept("阿姨拿起@道具。", "阿姨停住。")
    current = result.project.document.shots[0]
    owner = current if field == "visual" else current.timeline_beats[0]
    assert owner.visual == "阿姨停住。镜头中还能看见@道具。"
    assert len(owner.references) == 1
    kept = owner.references[0]
    assert kept.id == "occurrence-1" and kept.asset_version_id == version.id
    assert kept.text_start == owner.visual.index("@道具")
    plan = next(p for p in result.project.document.reference_plan if p.shot_id == shot.id)
    assert [binding.target_id for binding in plan.bindings] == [version.id]

    result = accept("镜头中还能看见@道具。", "镜头回到饭盘。")
    current = result.project.document.shots[0]
    owner = current if field == "visual" else current.timeline_beats[0]
    assert owner.references == []
    plan = next(p for p in result.project.document.reference_plan if p.shot_id == shot.id)
    assert plan.bindings == []
    assert image.is_file()
    assert next(a for a in result.project.document.assets if a.id == asset.id).current_version_id == version.id


def test_accepted_first_scene_keeps_dialogue_in_narrative_order(tmp_path: Path) -> None:
    from short_drama.models import AudioKind, ScriptLine, ScriptVersion, ProductionDocumentOrigin
    from short_drama.production_contracts import ProductionSpecCreateRequest, StoryboardDraftBeginRequest, StoryboardDraftSeed, StoryboardDraftSceneSeed
    from short_drama.studio_models import StudioProjectCreate
    from tests.test_storyboard_agent_interfaces import _production_spec

    service = ShortDramaService(database_path=tmp_path / "db.sqlite", workspace_root=tmp_path / "media")
    document = service.studio.create_project(StudioProjectCreate(name="两场戏")).document
    pid = document.project.id
    for i, text in enumerate(["先吃饭。", "再出发。"], start=1):
        line = ScriptLine(id=f"line-{i}", speaker="主角", original_text=text,
                          spoken_text=text, subtitle_text=text, audio_kind=AudioKind.DIALOGUE)
        service.studio.save_scene(pid, StudioSceneSave(heading=f"场次 {i}", screenplay_text=text, lines=[line]))
    before = service.get_project(pid).document.script_versions[-1]
    scene = before.structured_scenes[0]
    new_line = before.lines[0].model_copy(update={"original_text": "先吃完饭。", "spoken_text": "先吃完饭。", "subtitle_text": "先吃完饭。"})
    change = propose_changes(service, pid, StudioChangeRequest(title="第一场修改", items=[StudioTextChangeInput(
        target="scene", target_id=scene.id, field="screenplay_text", title="改一句", before="先吃饭。", after="先吃完饭。",
        lines=[new_line],
    )]))
    result = decide_change(service, pid, change.id, change.items[0].id, StudioChangeDecision(decision="accept"))
    script = result.project.document.script_versions[-1]
    assert [line.id for line in script.lines] == ["line-1", "line-2"]
    assert script.spoken_text == "先吃完饭。\n再出发。"
    assert script.structured_scenes[1] == before.structured_scenes[1]
    # Older saves recover narrative ordering without changing any scene text or ID.
    legacy = script.model_dump(mode="json")
    legacy["lines"] = list(reversed(legacy["lines"]))
    restored = ScriptVersion.model_validate(legacy)
    assert restored.lines == script.lines
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    draft = service.begin_storyboard_draft(pid, StoryboardDraftBeginRequest(origin=ProductionDocumentOrigin.AGENT, seed=StoryboardDraftSeed(
        project_id=pid, title="继续分镜", script_version_id=script.id,
        bible_version_id=result.project.document.project.current_bible_version_id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        scene_nodes=[StoryboardDraftSceneSeed(id=f"node-{i}", scene_id=item.id, sequence=i)
                     for i, item in enumerate(script.structured_scenes, start=1)],
    )))
    assert draft.progress is not None
