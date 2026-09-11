"""Local structure edits preserve concurrent content and actual media handoffs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from PIL import Image

from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.models import ApprovalStatus, AssetMediaType, Freshness, GenerationTask, OperationKind, ProductionDocumentOrigin, ShotPlan, ShotReferenceCue, ShotTimelineBeat, TaskStatus
from short_drama.production_contracts import (
    GenerationPlanCompileRequest, ProductionSpecCreateRequest,
    StoryboardClipGroupSplice, StoryboardCreateRequest, StoryboardMutationResponse,
    StoryboardPatchRequest, StoryboardReplaceRequest,
    StoryboardSceneNode, StoryboardShotSplice, apply_storyboard_patch, compile_generation_plan,
    populate_storyboard_scene_nodes,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_models import StudioShotSave
from short_drama.tools.contracts import ToolCallStatus
from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard
from tests.test_storyboard_clip_group_invalidation import _selected_take


def _fixture(tmp_path: Path) -> tuple[ShortDramaService, StoryboardMutationResponse]:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "work")
    document = service.initialize_fixture().document
    pid = document.project.id
    version = document.assets[0].versions[0]
    image_path = tmp_path / "selected-current.png"
    Image.new("RGB", (24, 36), "navy").save(image_path)
    version.local_path, version.external_url = str(image_path), None
    version.media_type, version.approval_status = AssetMediaType.IMAGE, ApprovalStatus.DRAFT
    document.assets[0].current_version_id = version.id
    for shot in (document.shots[0], document.shots[-1]):
        shot.references = [ShotReferenceCue(subject_asset_id=document.assets[0].id,
            asset_version_id=version.id, alias="人物依据", purpose="保留本镜已选身份图片")]
    service.repository.save_project(document)
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    board = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=_storyboard(service, pid, production_spec_document_id=spec.document.id,
                               production_spec_version_id=spec.version.id), origin=ProductionDocumentOrigin.AGENT))
    return service, board


def _split(shot: ShotPlan) -> list[ShotPlan]:
    seconds = shot.duration_seconds / 2
    result: list[ShotPlan] = []
    for index in range(2):
        dialogue = shot.dialogue_line_ids if index == 0 else []
        result.append(shot.model_copy(update={
            "id": shot.id if index == 0 else shot.id + "-result",
            "visual": shot.visual + ("；落笔动作" if index == 0 else "；可读结果插入镜"),
            "duration_seconds": seconds,
            "dialogue_line_ids": dialogue,
            "timeline_beats": [ShotTimelineBeat(start_seconds=0, end_seconds=seconds,
                visual=shot.visual, framing=shot.framing, camera=shot.camera,
                action=shot.continuity.action_in, dialogue_line_ids=dialogue,
                references=shot.references)],
        }, deep=True))
    return result


def _request(board: StoryboardMutationResponse, index: int = 0) -> StoryboardPatchRequest:
    target = board.storyboard.shots[index]
    return StoryboardPatchRequest(base_version_id=board.version.id,
        shot_splices=[StoryboardShotSplice(shot_ids=[target.id], shots=_split(target))],
        origin=ProductionDocumentOrigin.AGENT)


def test_local_split_tool_preserves_refs_revisions_takes_and_unrelated_unit(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    original = service.get_project(pid).document
    plan = service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        production_spec_document_id=board.storyboard.production_spec_document_id,
        production_spec_version_id=board.storyboard.production_spec_version_id,
        origin=ProductionDocumentOrigin.AGENT))
    last = original.shots[-1]
    service.repository.save_task(GenerationTask(id="test-task", project_id=pid, shot_id=last.id,
        operation=OperationKind.GENERATE, status=TaskStatus.SUCCEEDED, candidate_index=1,
        provider="minimax", model="MiniMax-H3", requested_duration_seconds=last.duration_seconds, attempt=1,
        request_fingerprint="saved-source", input_script_version_id=original.project.current_script_version_id,
        input_bible_version_id=original.project.current_bible_version_id, input_shot_revision=last.revision,
        created_at="2026-09-12T00:00:00Z", updated_at="2026-09-12T00:00:00Z"))
    selected = _selected_take(pid, last.id, "test-task", last.duration_seconds,
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id)
    service.repository.save_take(selected)
    document = service.get_project(pid).document
    document.reference_plan[-1].planning_notes = "Keep exact current image bindings"
    service.repository.save_project(document)
    original_reference_plan = document.reference_plan[-1].model_copy(deep=True)
    response = service.tool_registry.invoke("shot.patch_storyboard", {
        "project_id": pid, "request": _request(board).model_dump(mode="json")})
    assert response.status == ToolCallStatus.SUCCEEDED, response.error
    assert response.output is not None
    summary = response.output["storyboard"]
    assert isinstance(summary, dict) and all(isinstance(value, str) for value in summary["shots"])
    saved = service.get_project(pid).document
    assert saved.shots[0].revision == original.shots[0].revision + 1
    assert saved.shots[1].revision == 1
    assert saved.shots[1].references == original.shots[0].references
    assert saved.shots[-1].sequence == last.sequence + 1
    assert saved.shots[-1].model_dump(exclude={"sequence"}) == last.model_dump(exclude={"sequence"})
    assert saved.reference_plan[-1] == original_reference_plan
    assert saved.assets == original.assets
    assert Path(saved.assets[0].versions[0].local_path or "").read_bytes() == Path(original.assets[0].versions[0].local_path or "").read_bytes()
    assert saved.editorial_timeline == original.editorial_timeline
    take = service.repository.get_take(selected.id)
    assert take is not None and take.freshness == Freshness.CURRENT and take.is_selected
    current = service.get_storyboard(pid).storyboard
    assert current.computed_duration_seconds == board.storyboard.computed_duration_seconds
    assert current.clip_groups[1:] == board.storyboard.clip_groups[1:]
    compiled = compile_generation_plan(storyboard=current, storyboard_document_id=board.document.id,
        storyboard_version_id="new", production_spec=service.get_production_spec(pid).spec,
        production_spec_document_id=current.production_spec_document_id,
        production_spec_version_id=current.production_spec_version_id)
    assert compiled.units[1:] == plan.generation_plan.units[1:]
    assert compiled.units[0].shot_windows[0].source_in_seconds == 0
    assert compiled.units[0].shot_windows[0].source_out_seconds == saved.shots[0].duration_seconds
    assert compiled.units[0].shot_windows[1].source_in_seconds == saved.shots[0].duration_seconds
    assert compiled.units[0].shot_windows[1].source_out_seconds == original.shots[0].duration_seconds


def test_split_merges_an_unrelated_human_edit_from_same_read_version(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    request = _request(board)
    other = board.storyboard.shots[-1]
    service.studio.save_shot(pid, other.id, StudioShotSave(
        shot=other.model_copy(update={"camera": "人工选择：固定镜头"}), expected_revision=other.revision))
    human = service.get_project(pid).document.shots[-1]
    result = service.patch_storyboard(pid, request, current_content_edit=True)
    assert result.storyboard.shots[-1].model_dump(exclude={"sequence"}) == human.model_dump(exclude={"sequence"})
    assert result.version.parent_version_id != board.version.id


def test_concurrent_disjoint_splits_merge_but_same_shot_change_rejects(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    barrier = Barrier(2)

    def apply(index: int) -> str:
        request = _request(board, index)
        barrier.wait(timeout=5)
        return service.patch_storyboard(pid, request, current_content_edit=True).version.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply, index) for index in (0, 1)]
        assert len({future.result(timeout=10) for future in futures}) == 2
    saved = service.get_storyboard(pid)
    assert len(saved.storyboard.shots) == len(board.storyboard.shots) + 2
    with pytest.raises(WorkflowConflictError, match="changed since the read version"):
        service.patch_storyboard(pid, _request(board), current_content_edit=True)
    assert service.get_storyboard(pid) == saved


@pytest.mark.parametrize("position", ["start", "after", "end"])
def test_pure_insertion_and_deletion_keep_order_and_auto_group_membership(tmp_path: Path, position: str) -> None:
    _, board = _fixture(tmp_path)
    original = board.storyboard
    anchor = None if position == "start" else original.shots[-1 if position == "end" else 0].id
    new = _split(original.shots[0])[1].model_copy(update={"id": "new-insert"})
    request = StoryboardPatchRequest(base_version_id=board.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(after_shot_id=anchor, shots=[new])])
    inserted = apply_storyboard_patch(original, request)
    index = 0 if anchor is None else next(i for i, shot in enumerate(inserted.shots) if shot.id == anchor) + 1
    assert inserted.shots[index].id == new.id
    deleted = apply_storyboard_patch(inserted, StoryboardPatchRequest(base_version_id="new",
        origin=ProductionDocumentOrigin.AGENT, shot_splices=[StoryboardShotSplice(shot_ids=[new.id])]))
    assert deleted == original


def test_two_local_group_splits_do_not_rewrite_neighboring_groups(tmp_path: Path) -> None:
    _, board = _fixture(tmp_path)
    request = _request(board)
    first = board.storyboard.clip_groups[0]
    children = request.shot_splices[0].shots
    groups = [first.model_copy(update={"id": first.id if i == 0 else first.id + "-result",
        "shot_ids": [shot.id], "duration_seconds": 999}) for i, shot in enumerate(children)]
    request.clip_group_splices = [StoryboardClipGroupSplice(clip_group_ids=[first.id], clip_groups=groups)]
    result = apply_storyboard_patch(board.storyboard, request)
    assert result.clip_groups[2:] == board.storyboard.clip_groups[1:]
    assert [group.duration_seconds for group in result.clip_groups[:2]] == [shot.duration_seconds for shot in children]


def test_splice_can_merge_across_group_boundary_only_with_explicit_local_groups(tmp_path: Path) -> None:
    _, board = _fixture(tmp_path)
    first, second = board.storyboard.shots[:2]
    request = StoryboardPatchRequest(base_version_id=board.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(shot_ids=[first.id, second.id], shots=[first, second])])
    with pytest.raises(ValueError, match="crossing groups requires"):
        apply_storyboard_patch(board.storyboard, request)
    group = board.storyboard.clip_groups[0].model_copy(update={"shot_ids": [first.id, second.id]})
    request.clip_group_splices = [StoryboardClipGroupSplice(
        clip_group_ids=[g.id for g in board.storyboard.clip_groups[:2]], clip_groups=[group])]
    result = apply_storyboard_patch(board.storyboard, request)
    assert result.clip_groups[0].duration_seconds == first.duration_seconds + second.duration_seconds
    assert result.shots == board.storyboard.shots


@pytest.mark.parametrize("fault", ["overlap", "unknown", "borrow_id", "dialogue", "beat_window"])
def test_invalid_splice_is_atomic(tmp_path: Path, fault: str) -> None:
    service, board = _fixture(tmp_path)
    request = _request(board)
    if fault == "overlap":
        request.shot_splices.append(request.shot_splices[0].model_copy(deep=True))
    elif fault == "unknown":
        request.shot_splices[0].shot_ids = ["missing"]
    elif fault == "borrow_id":
        request.shot_splices[0].shots[1].id = board.storyboard.shots[-1].id
    elif fault == "dialogue":
        request.shot_splices[0].shots[1].dialogue_line_ids = ["invented-dialogue-line"]
    else:
        request.shot_splices[0].shots[1].timeline_beats[0].end_seconds = 999
    with pytest.raises((WorkflowConflictError, ValueError)):
        service.patch_storyboard(board.storyboard.project_id, request, current_content_edit=True)
    assert service.get_storyboard(board.storyboard.project_id).model_dump() == board.model_dump()


def test_two_local_splits_payload_is_smaller_than_whole_film(tmp_path: Path) -> None:
    _, board = _fixture(tmp_path)
    request = _request(board)
    request.shot_splices.extend(_request(board, len(board.storyboard.shots) - 1).shot_splices)
    result = apply_storyboard_patch(board.storyboard, request)
    full = StoryboardReplaceRequest(storyboard=result, base_version_id=board.version.id, origin=ProductionDocumentOrigin.AGENT)
    patch_tokens = count_text_tokens(json.dumps(request.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False))
    full_tokens = count_text_tokens(json.dumps(full.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False))
    assert patch_tokens < full_tokens * 0.8, (patch_tokens, full_tokens)
    # This is deterministic payload volume, not measured Provider speed/quality.
    assert len(request.shot_splices) == 2
    assert len(result.shots) == len(board.storyboard.shots) + 2


def test_insertion_rejects_a_concurrent_change_to_the_same_gap(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    anchor = board.storyboard.shots[0]
    inserted = _split(anchor)[1]
    request = StoryboardPatchRequest(base_version_id=board.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(after_shot_id=anchor.id, shots=[inserted])])
    service.patch_storyboard(board.storyboard.project_id, request, current_content_edit=True)
    request.shot_splices[0].shots[0].id = "other-insert"
    before = service.get_storyboard(board.storyboard.project_id)
    with pytest.raises(WorkflowConflictError, match="boundary changed"):
        service.patch_storyboard(board.storyboard.project_id, request, current_content_edit=True)
    assert service.get_storyboard(board.storyboard.project_id) == before


def test_group_changes_since_read_are_not_overwritten_by_local_split(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    group = board.storyboard.clip_groups[0].model_copy(update={"reference_notes": ["人工更新构图参考"]})
    service.patch_storyboard(board.storyboard.project_id, StoryboardPatchRequest(
        base_version_id=board.version.id, origin=ProductionDocumentOrigin.USER, clip_groups=[group]),
        current_content_edit=True)
    before = service.get_storyboard(board.storyboard.project_id)
    with pytest.raises(WorkflowConflictError, match="clip group.*changed"):
        service.patch_storyboard(board.storyboard.project_id, _request(board), current_content_edit=True)
    assert service.get_storyboard(board.storyboard.project_id) == before


def test_earlier_split_does_not_recompile_or_replace_unaffected_h3_operation(tmp_path: Path) -> None:
    from short_drama.h3_group_operation import H3GroupOperationRequest
    from short_drama.models import VideoGenerationRoute
    from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
    from tests.test_h3_generation_unit_language import _fixture as h3_fixture

    service, fake, document, generation, _first_unit, _ = h3_fixture(tmp_path)
    unit = generation.generation_plan.units[-1]
    shots = [shot for shot in document.shots if shot.id in unit.shot_ids]
    fake.output_text = build_h3_generation_unit_prompt(document, unit, shots)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.id, route=VideoGenerationRoute.DIRECT, media_inputs=[]))
    assert prepared.status == "PREPARED", prepared.error
    original = service.get_storyboard(document.project.id)
    first = original.storyboard.shots[0]
    service.patch_storyboard(document.project.id, StoryboardPatchRequest(
        base_version_id=original.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(shot_ids=[first.id], shots=_split(first))]), current_content_edit=True)
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert resumed.status == "PREPARED", resumed.error
    assert resumed.operation_id == prepared.operation_id
    assert resumed.route_plan_id == prepared.route_plan_id
    assert resumed.generation_plan_version_id == prepared.generation_plan_version_id
    assert len(fake.calls) == 1
    assert service.repository.list_tasks(document.project.id) == []


def test_local_split_and_regroup_update_scene_index_without_rewriting_scene(tmp_path: Path) -> None:
    _, board = _fixture(tmp_path)
    shots = [shot.model_copy(update={"scene_id": "scene-local"}) for shot in board.storyboard.shots]
    node = StoryboardSceneNode(id="node-local", scene_id="scene-local", sequence=1,
        heading="原场次", summary="人工保留的场次说明", source_line_ids=board.storyboard.script_line_ids,
        notes=["保留已确认的表演意图"])
    content = board.storyboard.model_copy(update={"shots": shots,
        "scene_nodes": populate_storyboard_scene_nodes([node], shots, board.storyboard.clip_groups)})
    children = _split(shots[0])
    group = content.clip_groups[0]
    request = StoryboardPatchRequest(base_version_id=board.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(shot_ids=[shots[0].id], shots=children)],
        clip_group_splices=[StoryboardClipGroupSplice(clip_group_ids=[group.id], clip_groups=[
            group.model_copy(update={"shot_ids": [children[0].id]}),
            group.model_copy(update={"id": group.id + "-result", "shot_ids": [children[1].id]}),
        ])])
    result = apply_storyboard_patch(content, request)
    updated = result.scene_nodes[0]
    assert updated.model_dump(exclude={"shot_ids", "clip_group_ids", "continuity_in", "continuity_out"}) == node.model_dump(
        exclude={"shot_ids", "clip_group_ids", "continuity_in", "continuity_out"})
    assert updated.shot_ids == [shot.id for shot in result.shots]
    assert updated.clip_group_ids == [item.id for item in result.clip_groups]
    assert result.shots[2:] == [shot.model_copy(update={"sequence": shot.sequence + 1}) for shot in shots[1:]]
