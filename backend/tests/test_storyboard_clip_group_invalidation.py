"""Focused invalidation coverage for ClipGroup-only storyboard revisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.models import (
    EnqueueRequest,
    Freshness,
    GenerationTask,
    OperationKind,
    ProductionDocumentOrigin,
    QAReport,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
)
from short_drama.production_contracts import (
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    ClipGroup,
    GenerationPlanCompileRequest,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionSpecReplaceRequest,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardReplaceRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_models import StudioSceneSave, StudioShotSave


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _selected_take(
    project_id: str,
    shot_id: str,
    task_id: str,
    duration: float,
    *,
    storyboard_document_id: str,
    storyboard_version_id: str,
    generation_plan_document_id: str,
    generation_plan_version_id: str,
) -> Take:
    take_id = f"take-{shot_id}"
    return Take(
        id=take_id,
        project_id=project_id,
        shot_id=shot_id,
        task_id=task_id,
        candidate_index=1,
        operation=OperationKind.GENERATE,
        provider="minimax",
        model="MiniMax-H3",
        storyboard_document_id=storyboard_document_id,
        storyboard_version_id=storyboard_version_id,
        generation_plan_document_id=generation_plan_document_id,
        generation_plan_version_id=generation_plan_version_id,
        media_path=f"/tmp/{take_id}.mp4",
        thumbnail_path=f"/tmp/{take_id}.jpg",
        duration_seconds=duration,
        width=768,
        height=1366,
        availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.PASSED,
        is_selected=True,
        freshness=Freshness.CURRENT,
        qa_report=QAReport(
            id=f"qa-{take_id}",
            take_id=take_id,
            status=QaStatus.PASSED,
            hard_gate_passed=True,
            score=5.0,
            issues=[],
            checks=["focused-test"],
            created_at="2026-09-04T12:00:00+08:00",
        ),
        created_at="2026-09-04T12:00:00+08:00",
    )


def test_timeline_rejects_legacy_take_from_previous_script_version(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    service.enqueue(project_id, EnqueueRequest(candidate_count=1))
    assert service.drain(project_id, 500).remaining_runnable == 0
    assert service.rebuild_manifest(project_id).script_version_id == (
        document.project.current_script_version_id
    )

    document = service.get_project(project_id).document
    current_script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    changed_line = current_script.lines[0].model_copy(
        update={
            "spoken_text": "这句新台词没有经过正常的分镜与 Take 失效传播。",
            "subtitle_text": "这句新台词没有经过正常的分镜与 Take 失效传播。",
        }
    )
    replacement = current_script.model_copy(
        deep=True,
        update={
            "id": f"{project_id}-script-v2",
            "version": current_script.version + 1,
            "lines": [changed_line, *current_script.lines[1:]],
            "parent_version_id": current_script.id,
            "change_reason": "验证时间线不得把旧 Take 冒充为新剧本产物。",
        },
    )
    document.script_versions.append(replacement)
    document.project.current_script_version_id = replacement.id
    service.repository.save_project(document)

    with pytest.raises(WorkflowConflictError, match="outdated ScriptVersion"):
        service.rebuild_manifest(project_id)


def test_clip_group_only_revision_stales_exactly_affected_takes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shots = [item.model_copy(deep=True) for item in document.shots]

    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="ClipGroup invalidation spec",
                production_scope="Validate storyboard grouping changes.",
                production_mode="Agent-managed production.",
                genre_and_setting="Rainy-night convenience store thriller.",
                language="zh-CN",
                aspect_ratio="9:16",
                target_duration_seconds=sum(item.duration_seconds for item in shots),
                visual=ProductionVisualDirection(medium_lock="Live action"),
                audio=ProductionAudioDirection(dialogue_intent="Clear dialogue"),
                provider_preference=ProductionProviderPreference(
                    provider="minimax",
                    model="MiniMax-H3",
                    resolution="768P",
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    original_groups = [
        ClipGroup(
            id=f"clip-group-{shot.sequence:02d}",
            title=f"Shot {shot.sequence}",
            shot_ids=[shot.id],
            duration_seconds=shot.duration_seconds,
            generation_intent=f"Generate shot {shot.sequence} independently.",
        )
        for shot in shots
    ]
    created = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=StoryboardContent(
                project_id=project_id,
                title="ClipGroup invalidation storyboard",
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
                script_line_ids=[item.id for item in script.lines],
                shots=shots,
                clip_groups=original_groups,
                computed_duration_seconds=sum(
                    item.duration_seconds for item in shots
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    generation_plan = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=created.document.id,
            storyboard_version_id=created.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    for shot in shots:
        task = GenerationTask(
            id=f"task-{shot.id}",
            project_id=project_id,
            shot_id=shot.id,
            operation=OperationKind.GENERATE,
            candidate_index=1,
            status=TaskStatus.SUCCEEDED,
            provider="minimax",
            model="MiniMax-H3",
            request_fingerprint=f"fingerprint-{shot.id}",
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            input_shot_revision=shot.revision,
            storyboard_document_id=created.document.id,
            storyboard_version_id=created.version.id,
            generation_plan_document_id=generation_plan.document.id,
            generation_plan_version_id=generation_plan.version.id,
            requested_duration_seconds=shot.duration_seconds,
            attempt=1,
            finished_at="2026-09-04T12:00:00+08:00",
            created_at="2026-09-04T12:00:00+08:00",
            updated_at="2026-09-04T12:00:00+08:00",
        )
        service.repository.save_task(task)
        service.repository.save_take(
            _selected_take(
                project_id,
                shot.id,
                task.id,
                shot.duration_seconds,
                storyboard_document_id=created.document.id,
                storyboard_version_id=created.version.id,
                generation_plan_document_id=generation_plan.document.id,
                generation_plan_version_id=generation_plan.version.id,
            )
        )
    manifest = service.rebuild_manifest(project_id)

    revised_groups = [
        ClipGroup(
            id="clip-group-merged-opening",
            title="Merged opening",
            shot_ids=[shots[0].id, shots[1].id],
            duration_seconds=shots[0].duration_seconds + shots[1].duration_seconds,
            generation_intent="Generate the opening shots as one continuous clip.",
        ),
        original_groups[2].model_copy(
            update={
                "generation_intent": "Generate shot 3 with a different performance intent."
            },
            deep=True,
        ),
        *[item.model_copy(deep=True) for item in original_groups[3:]],
    ]
    service.replace_storyboard(
        project_id,
        StoryboardReplaceRequest(
            storyboard=created.storyboard.model_copy(
                update={"clip_groups": revised_groups},
                deep=True,
            ),
            base_version_id=created.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    affected_ids = {item.id for item in shots[:3]}
    for shot in shots:
        take = service.repository.get_take(f"take-{shot.id}")
        assert take is not None
        if shot.id in affected_ids:
            assert take.freshness == Freshness.STALE
            assert take.selected_shot_ids == []
        else:
            assert take.freshness == Freshness.CURRENT
            assert take.selected_shot_ids == [shot.id]

    revised_document = service.get_project(project_id).document
    persisted_manifest = next(
        item for item in revised_document.manifests if item.id == manifest.id
    )
    assert persisted_manifest.freshness == Freshness.STALE
    assert revised_document.project.current_manifest_id is None
    generation_slot = service.get_output_slot(
        project_id,
        GENERATION_PLAN_OUTPUT_SLOT_ID,
    ).output_slot
    assert generation_slot.current_artifact_ref is None
    assert generation_slot.previous_artifact_refs[-1].artifact_id == (
        generation_plan.document.id
    )

    # Unchanged shots still refer honestly to their original source version and
    # can be assembled after other groups change. A global version cursor is
    # not a reason to throw away these already completed videos.
    current_shots, current_id, current_version = service._timeline_storyboard_truth(revised_document)
    for shot in current_shots[3:]:
        take = service.repository.get_take(f"take-{shot.id}")
        assert take is not None and take.storyboard_version_id == created.version.id
        service._validate_timeline_take_source(revised_document, take, shot,
            storyboard_document_id=current_id, storyboard_version_id=current_version)

    untouched = current_shots[3]
    take = service.repository.get_take(f"take-{untouched.id}")
    assert take is not None
    take.storyboard_version_id = current_version
    with pytest.raises(WorkflowConflictError, match="different Storyboard source"):
        service._validate_timeline_take_source(revised_document, take, untouched,
            storyboard_document_id=current_id, storyboard_version_id=current_version)


def test_formal_shot_reuses_take_after_another_part_of_its_scene_changes(tmp_path: Path) -> None:
    from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard

    service = _service(tmp_path)
    document = service.initialize_fixture().document
    pid = document.project.id
    script = document.script_versions[-1]
    scene = script.structured_scenes[0]
    scene.screenplay_text = "\n".join(line.spoken_text for line in script.lines if line.id in scene.line_ids)
    chosen_index = next(index for index, shot in enumerate(document.shots) if shot.dialogue_line_ids)
    document.shots[chosen_index].scene_id = scene.id
    service.repository.save_project(document)
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    created = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=_storyboard(service, pid, production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id), origin=ProductionDocumentOrigin.AGENT))
    original = service.get_project(pid).document.shots[chosen_index]
    task = GenerationTask(id=f"task-{original.id}", project_id=pid, shot_id=original.id,
        operation=OperationKind.GENERATE, candidate_index=1, status=TaskStatus.SUCCEEDED,
        provider="minimax", model="MiniMax-H3", request_fingerprint="unchanged-shot",
        input_script_version_id=script.id, input_bible_version_id=document.project.current_bible_version_id,
        input_shot_revision=original.revision, storyboard_document_id=created.document.id,
        storyboard_version_id=created.version.id, requested_duration_seconds=original.duration_seconds,
        attempt=1, created_at="2026-09-10T00:00:00Z", updated_at="2026-09-10T00:00:00Z")
    service.repository.save_task(task)
    take = _selected_take(pid, original.id, task.id, original.duration_seconds,
        storyboard_document_id=created.document.id, storyboard_version_id=created.version.id,
        generation_plan_document_id="source-plan", generation_plan_version_id="source-plan-v1")
    service.repository.save_take(take)
    edited = service.studio.save_scene(pid, StudioSceneSave(scene_id=scene.id, heading=scene.heading,
        screenplay_text=scene.screenplay_text + "\n场次末尾补充一个未涉及本镜的动作。",
        expected_text=scene.screenplay_text, affected_shot_ids=[],
        lines=[line for line in script.lines if line.id in scene.line_ids])).project.document
    updated_spec = service.replace_production_spec(pid, ProductionSpecReplaceRequest(
        spec=spec.spec.model_copy(update={"script_version_id":edited.project.current_script_version_id}),
        base_version_id=spec.version.id, origin=ProductionDocumentOrigin.AGENT))
    service.replace_storyboard(pid, StoryboardReplaceRequest(
        storyboard=created.storyboard.model_copy(update={
            "script_version_id":edited.project.current_script_version_id,
            "production_spec_version_id":updated_spec.version.id,
        }, deep=True), base_version_id=created.version.id, origin=ProductionDocumentOrigin.AGENT))
    current = service.get_project(pid).document
    shots, current_id, current_version = service._timeline_storyboard_truth(current)
    current_shot = next(shot for shot in shots if shot.id == original.id)
    service._validate_timeline_take_source(current, take, current_shot,
        storyboard_document_id=current_id, storyboard_version_id=current_version)
    assert take.storyboard_version_id == created.version.id

    # A real dialogue change still makes this video's spoken content outdated.
    changed = current.model_copy(deep=True)
    active = next(item for item in changed.script_versions if item.id == changed.project.current_script_version_id)
    line = next(item for item in active.lines if item.id in current_shot.dialogue_line_ids)
    line.spoken_text = "本镜现在需要另一句对白。"
    with pytest.raises(WorkflowConflictError, match="outdated ScriptVersion"):
        service._validate_timeline_take_source(changed, take, current_shot,
            storyboard_document_id=current_id, storyboard_version_id=current_version)


@pytest.mark.parametrize("entrypoint", ["workspace", "shot.save_current", "shot.patch_storyboard"])
def test_current_shot_remains_editable_during_upstream_script_revision(tmp_path: Path, entrypoint: str) -> None:
    from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard

    service = _service(tmp_path)
    document = service.initialize_fixture().document
    pid = document.project.id
    script = document.script_versions[-1]
    scene = script.structured_scenes[0]
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    created = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=_storyboard(service, pid, production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id), origin=ProductionDocumentOrigin.AGENT))
    service.studio.save_scene(pid, StudioSceneSave(
        scene_id=scene.id, heading=scene.heading,
        screenplay_text=scene.screenplay_text + "\n" + "\n".join(
            line.spoken_text for line in script.lines if line.id in scene.line_ids
        ) + "\n结尾动作仍在逐项审阅。",
        expected_text=scene.screenplay_text, affected_shot_ids=[],
        lines=[line for line in script.lines if line.id in scene.line_ids]))
    before = service.studio.current_project(pid).document
    original = before.shots[0]
    edited = original.model_copy(update={"camera": "固定机位，保留原构图。"}, deep=True)
    if entrypoint == "workspace":
        service.studio.save_shot(pid, original.id, StudioShotSave(
            shot=edited, expected_revision=original.revision))
    elif entrypoint == "shot.save_current":
        result = service.tool_registry.invoke(entrypoint, {"project_id": pid, "shot_id": original.id,
            "request": {"shot": edited.model_dump(mode="json"), "expected_revision": original.revision}})
        assert result.status.value == "SUCCEEDED", result.error
    else:
        result = service.tool_registry.invoke(entrypoint, {"project_id": pid, "request": {
            "base_version_id": created.version.id,
            "replacements": [{"shot_id": original.id, "expected_revision": original.revision,
                              "replacement": edited.model_dump(mode="json")}],
            "origin": "AGENT",
        }})
        assert result.status.value == "SUCCEEDED", result.error
    after = service.studio.current_project(pid).document
    saved = next(shot for shot in after.shots if shot.id == original.id)
    assert saved.camera == edited.camera
    assert saved.revision > original.revision
    assert after.shots[1:] == before.shots[1:]
    assert after.assets == before.assets
    assert after.editorial_timeline == before.editorial_timeline
    storyboard = service.get_storyboard(pid)
    assert storyboard.storyboard.script_version_id == created.storyboard.script_version_id
    assert storyboard.storyboard.script_version_id != after.project.current_script_version_id
    refs = storyboard.version.source_artifact_refs
    assert any(ref.artifact_type == "script_version" and ref.artifact_id == script.id for ref in refs)
    assert not any(ref.artifact_type == "script_version"
                   and ref.artifact_id == after.project.current_script_version_id for ref in refs)

    # Editing current content does not claim it has adopted a new script or
    # make an invalid source acceptable to the generation path.
    with pytest.raises(WorkflowConflictError, match="current ScriptVersion"):
        service._validate_storyboard(after, storyboard.storyboard)


def test_source_and_scene_sync_does_not_require_rewriting_all_shots(tmp_path: Path) -> None:
    from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard
    from short_drama.production_contracts import StoryboardSceneNode, populate_storyboard_scene_nodes

    service = _service(tmp_path)
    document = service.initialize_fixture().document
    pid = document.project.id
    script = document.script_versions[-1]
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    content = _storyboard(service, pid, production_spec_document_id=spec.document.id,
                         production_spec_version_id=spec.version.id)
    scene_for_line = {line_id: scene.id for scene in script.structured_scenes for line_id in scene.line_ids}
    content = content.model_copy(update={"shots": [shot.model_copy(update={"scene_id": next(
        (scene_for_line[line_id] for line_id in shot.dialogue_line_ids), script.structured_scenes[0].id
    )}) for shot in content.shots]})
    nodes = [StoryboardSceneNode(id=f"node-{scene.id}", scene_id=scene.id, sequence=index+1,
             heading=scene.heading, summary=scene.summary, source_line_ids=scene.line_ids)
             for index, scene in enumerate(script.structured_scenes)]
    content = content.model_copy(update={"scene_nodes": populate_storyboard_scene_nodes(nodes, content.shots, content.clip_groups)})
    created = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=content, origin=ProductionDocumentOrigin.AGENT))
    scene = script.structured_scenes[-1]
    service.studio.save_scene(pid, StudioSceneSave(scene_id=scene.id, heading=scene.heading,
        screenplay_text=scene.screenplay_text + "\n" + "\n".join(
            line.spoken_text for line in script.lines if line.id in scene.line_ids) + "\n更新结尾的制作说明。",
        expected_text=scene.screenplay_text, affected_shot_ids=[],
        lines=[line for line in script.lines if line.id in scene.line_ids]))
    current = service.get_project(pid).document
    updated_spec = service.replace_production_spec(pid, ProductionSpecReplaceRequest(
        base_version_id=spec.version.id,
        spec=spec.spec.model_copy(update={"script_version_id": current.project.current_script_version_id}),
        origin=ProductionDocumentOrigin.AGENT))
    scene_node = created.storyboard.scene_nodes[-1].model_copy(update={"summary": "更新后的结尾说明。"})
    result = service.tool_registry.invoke("shot.patch_storyboard", {"project_id": pid, "request": {
        "base_version_id": created.version.id,
        "script_version_id": current.project.current_script_version_id,
        "production_spec_version_id": updated_spec.version.id,
        "scene_nodes": [scene_node.model_dump(mode="json")], "origin": "AGENT",
    }})
    assert result.status.value == "SUCCEEDED", result.error
    saved = service.get_storyboard(pid)
    assert saved.storyboard.shots == created.storyboard.shots
    assert saved.storyboard.clip_groups == created.storyboard.clip_groups
    assert saved.storyboard.scene_nodes[:-1] == created.storyboard.scene_nodes[:-1]
    assert saved.storyboard.scene_nodes[-1].summary == scene_node.summary
    assert saved.storyboard.script_version_id == current.project.current_script_version_id
    assert any(ref.artifact_type == "script_version" and ref.artifact_id == current.project.current_script_version_id
               for ref in saved.version.source_artifact_refs)
    compiled = service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=saved.document.id, storyboard_version_id=saved.version.id,
        production_spec_document_id=updated_spec.document.id, production_spec_version_id=updated_spec.version.id,
        origin=ProductionDocumentOrigin.AGENT))
    assert compiled.generation_plan.script_version_id == current.project.current_script_version_id


@pytest.mark.parametrize("change", ["delivery", "visual", "audio", "shot", "group"])
def test_explicit_take_selection_rechecks_content_after_spec_invalidation(tmp_path: Path, change: str) -> None:
    from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard

    service = _service(tmp_path)
    document = service.initialize_fixture().document
    pid = document.project.id
    initial_spec = _production_spec(service, pid)
    initial_spec.provider_preference.resolution = "2K"
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=initial_spec, origin=ProductionDocumentOrigin.AGENT))
    board = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=_storyboard(service, pid, production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id), origin=ProductionDocumentOrigin.AGENT))
    plan = service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        origin=ProductionDocumentOrigin.AGENT))
    shot = service.get_project(pid).document.shots[0]
    unit = plan.generation_plan.units[0]
    task = GenerationTask(id="task-before-delivery-change", project_id=pid, shot_id=shot.id,
        operation=OperationKind.GENERATE, candidate_index=1, status=TaskStatus.SUCCEEDED,
        provider="minimax", model="MiniMax-H3", resolution="768P", request_fingerprint="existing-result",
        input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id,
        input_shot_revision=shot.revision, input_shot_revisions={shot.id: shot.revision},
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id,
        generation_unit_id=unit.id, clip_group_id=unit.clip_group_id, shot_windows=unit.shot_windows,
        requested_duration_seconds=shot.duration_seconds, attempt=1,
        created_at="2026-09-13T01:00:00Z", updated_at="2026-09-13T01:00:00Z")
    service.repository.save_task(task)
    path = tmp_path / "existing-source.mp4"
    path.write_bytes(b"existing source bytes; selection does not decode media")
    original_bytes = path.read_bytes()
    take = _selected_take(pid, shot.id, task.id, shot.duration_seconds,
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id)
    take.media_path = str(path)
    take.clip_group_id, take.generation_unit_id = unit.clip_group_id, unit.id
    take.shot_windows = unit.shot_windows
    service.repository.save_take(take)

    revised_spec = spec.spec.model_copy(deep=True)
    revised_spec.provider_preference.resolution = "768P"
    revised_spec.notes = ["Use the generated result directly in the presentation."]
    if change == "visual":
        revised_spec.visual.medium_lock = "Hand-drawn animation"
    if change == "audio":
        revised_spec.audio.dialogue_intent = "Replace spoken dialogue with singing."
    updated_spec = service.replace_production_spec(pid, ProductionSpecReplaceRequest(
        base_version_id=spec.version.id, spec=revised_spec, origin=ProductionDocumentOrigin.AGENT))
    revised_board = board.storyboard.model_copy(deep=True)
    revised_board.production_spec_version_id = updated_spec.version.id
    if change == "shot":
        revised_board.shots[0].visual += " 人物转身离开。"
    if change == "group":
        revised_board.clip_groups[0].generation_intent = "Show a different ending to the action."
    service.replace_storyboard(pid, StoryboardReplaceRequest(
        base_version_id=board.version.id, storyboard=revised_board, origin=ProductionDocumentOrigin.AGENT))
    before_selection = service.repository.get_take(take.id)
    assert before_selection is not None and before_selection.freshness == Freshness.STALE
    if change != "delivery":
        with pytest.raises(WorkflowConflictError):
            service.select_take(take.id)
        retained = service.repository.get_take(take.id)
        assert retained is not None and retained.freshness == Freshness.STALE and not retained.is_selected
        return

    selected = service.select_take(take.id)
    reused = next(item for item in selected.takes if item.id == take.id)
    assert reused.freshness == Freshness.CURRENT and reused.is_selected
    assert reused.storyboard_version_id == board.version.id
    assert reused.generation_plan_version_id == plan.version.id
    assert reused.task_id == task.id
    assert reused.qa_report == take.qa_report
    assert Path(reused.media_path).read_bytes() == original_bytes
