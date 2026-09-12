"""Actual draft, formal, and semantic-text saves keep one current reference truth."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router
from short_drama.change_executor import SemanticArtifactPatch, SemanticChangeApplyRequest
from short_drama.change_planner import ChangeTargetKind
from short_drama.deps import get_short_drama_service
from short_drama.models import DirectorRepairDecision, DirectorRepairStrategy, GenerationTask, OperationKind, ProductionDocumentOrigin, ShotPlan, ShotReferenceCue, TaskStatus
from short_drama.production_contracts import (
    StoryboardDraftBeginRequest, StoryboardDraftContent, StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupReplaceRequest, StoryboardWorkUnitContract,
    ProductionSpecCreateRequest,
)
from short_drama.service import ShortDramaService
from short_drama.reference_planner import DirectorRepairDecisionRequest, ShotPlanPatch
from short_drama.studio_models import StudioShotSave
from short_drama.tools.contracts import ToolCallStatus
from short_drama.tools.studio import ShotWriteInput
from tests.test_semantic_change_executor import _plan
from tests.test_shot_reference_anchors import _anchored_shot
from tests.test_storyboard_incremental_service import (
    _adjacent_prefix_batch, _append_all_groups, _draft, _prepare_script_only_project, _seed_from_draft,
)
from tests.test_storyboard_local_edits import _fixture
from tests.test_shot_media_registration import PausingRepository
from tests.test_storyboard_clip_group_invalidation import _selected_take
from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard


def _semantic_request(shot: ShotPlan, **updates: str) -> SemanticChangeApplyRequest:
    return SemanticChangeApplyRequest(plan=_plan(project_id=shot.project_id,
        kind=ChangeTargetKind.SHOT, target_id=shot.id, affected_shot_ids=[shot.id]),
        patches=[SemanticArtifactPatch(target_kind=ChangeTargetKind.SHOT, target_id=shot.id, updates=updates)],
        change_reason="局部正文修改，保留已选图片和其他字段")


def test_draft_append_replace_semantic_edit_and_finalize_store_the_same_resolved_spans(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "draft.sqlite", workspace_root=tmp_path / "media")
    document, script, spec, shots = _prepare_script_only_project(service)
    pid = document.project.id
    draft = _draft(document, script, spec, dialogue_shots=shots)
    begun = service.begin_storyboard_draft(pid, StoryboardDraftBeginRequest(
        seed=_seed_from_draft(draft), origin=ProductionDocumentOrigin.AGENT)).progress
    work, groups = _adjacent_prefix_batch(script=script, dialogue_shots=shots, draft=draft)
    group = groups[0].model_dump(mode="json")
    first = group["shots"][0]
    first["visual"] = "🙂先看@人物，随后停住。"
    first["references"] = [{"subject_asset_id": document.assets[0].id, "alias": "自定人物_v3",
        "purpose": "待制作的人物身份参考", "field": "visual", "text_anchor": {"exact": "@人物"}}]
    args = {"project_id": pid, "request": {"draft_document_id": begun.draft_ref.artifact_id,
        "base_version_id": begun.draft_version_id, "work_unit": work.model_dump(mode="json"),
        "group": group, "origin": "AGENT"}}
    before_args = deepcopy(args)
    result = service.tool_registry.invoke("shot.append_storyboard_group", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert args == before_args
    saved = service.get_production_document(pid, begun.draft_ref.artifact_id)
    current = StoryboardDraftContent.model_validate(saved.version.payload)
    selected = current.committed_groups[0].shots[0]
    assert (selected.references[0].text_start, selected.references[0].text_end) == (3, 6)
    replacement = current.committed_groups[0].model_dump(mode="json")
    replacement["shots"][0]["visual"] = "门边。" + first["visual"]
    replacement["shots"][0]["references"][0].update(text_start=None, text_end=None,
        text_anchor={"exact": "@人物"})
    request = StoryboardDraftGroupReplaceRequest.model_validate({
        "draft_document_id": saved.document.id, "base_version_id": saved.version.id,
        "replaced_clip_group_id": current.committed_groups[0].clip_group.id,
        "group": replacement, "origin": "AGENT", "work_unit": StoryboardWorkUnitContract(
            id=current.committed_groups[0].work_unit_id, title="调整这一组",
            source_range=current.committed_groups[0].source_range,
            objective="保留当前人物和其他内容", completion_criteria=["完成该组"]).model_dump(mode="json")})
    service.replace_storyboard_draft_group(pid, request)
    current_shot = service.studio.current_project(pid).document.shots[0]
    assert current_shot.references[0].text_start == 6
    # The legacy semantic Tool must edit the committed draft, not just its view.
    semantic = service.tool_registry.invoke("change.apply", {"project_id": pid,
        "request": _semantic_request(current_shot, visual="前景。" + current_shot.visual).model_dump(mode="json")})
    assert semantic.status == ToolCallStatus.SUCCEEDED, semantic.error
    saved = service.get_production_document(pid, begun.draft_ref.artifact_id)
    current = StoryboardDraftContent.model_validate(saved.version.payload)
    revised = current.committed_groups[0].shots[0]
    assert revised.references[0].text_start == 9
    assert revised.revision == current_shot.revision + 1
    assert revised.timeline_beats == current_shot.timeline_beats
    assert service.studio.current_project(pid).document.shots[0] == revised
    progress = _append_all_groups(service, document=document,
        script=script.model_copy(update={"lines": script.lines[1:]}), dialogue_shots=shots[1:],
        draft_document_id=begun.draft_ref.artifact_id, base_version_id=saved.version.id,
        starting_sequence=2, starting_continuity_in=[f"after-{script.lines[0].id}"])
    formal = service.finalize_storyboard(pid, StoryboardDraftFinalizeRequest(
        draft_document_id=begun.draft_ref.artifact_id, draft_version_id=progress.draft_version_id,
        origin=ProductionDocumentOrigin.AGENT))
    assert formal.storyboard.shots[0].model_dump(exclude={"revision"}) == revised.model_dump(exclude={"revision"})
    assert service.get_project(pid).document.shots[0] == formal.storyboard.shots[0]
    assert "text_anchor" not in formal.version.model_dump_json()


def test_semantic_http_patch_retains_beats_and_syncs_formal_body_ref_and_revision(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    service.studio.save_shot(pid, shot.id, StudioShotSave.model_validate({
        "expected_revision": shot.revision, "shot": _anchored_shot(shot)}))
    before = service.get_storyboard(pid)
    selected = before.storyboard.shots[0]
    original_beats = deepcopy(selected.timeline_beats)
    request = _semantic_request(selected, visual="先听。" + selected.visual, camera="用户要的机位；" + selected.camera)
    original_request = request.model_dump(mode="json")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    response = TestClient(app).post(f"/api/short-drama/projects/{pid}/agent-tools/apply-change", json=original_request)
    assert response.status_code == 200, response.text
    assert request.model_dump(mode="json") == original_request
    saved = service.get_storyboard(pid)
    assert saved.version.parent_version_id == before.version.id
    assert saved.storyboard.shots[1:] == before.storyboard.shots[1:]
    actual = saved.storyboard.shots[0]
    assert actual.timeline_beats == original_beats
    assert actual.revision == selected.revision + 1
    assert (actual.references[0].text_start, actual.references[1].text_start) == (9, 8)
    returned = response.json()["project"]["document"]["shots"][0]
    assert returned == actual.model_dump(mode="json")
    assert service.get_project(pid).document.shots[0] == actual
    assert saved.storyboard.clip_groups == before.storyboard.clip_groups


def test_semantic_ambiguous_repeated_mention_does_not_partially_write_or_clear_beats(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    service.studio.save_shot(pid, shot.id, StudioShotSave.model_validate({
        "expected_revision": shot.revision, "shot": _anchored_shot(shot)}))
    before = service.get_project(pid).document
    selected = before.shots[0]
    request = _semantic_request(selected, visual="从@人物移到@人物，再从窗边退开。", camera="这项也不能先保存")
    result = service.tool_registry.invoke("change.apply", {"project_id": pid, "request": request.model_dump(mode="json")})
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert "AMBIGUOUS" in result.error.message
    assert service.get_project(pid).document == before


def test_semantic_removal_only_removes_that_prose_occurrence(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    service.studio.save_shot(pid, shot.id, StudioShotSave.model_validate({
        "expected_revision": shot.revision, "shot": _anchored_shot(shot)}))
    before = service.get_project(pid).document
    selected = before.shots[0]
    result = service.apply_semantic_change(pid, _semantic_request(selected, visual="只拍空景。"))
    actual = result.project.document.shots[0]
    assert [cue.field for cue in actual.references] == ["camera"]
    assert actual.timeline_beats == selected.timeline_beats
    assert result.project.document.assets == before.assets
    assert service.get_storyboard(pid).storyboard.shots[0] == actual


def test_api_schema_exposes_anchor_on_each_shared_owner_without_requiring_offsets(tmp_path: Path) -> None:
    _service, _board = _fixture(tmp_path)
    schema: dict[str, Any] = ShotWriteInput.model_json_schema()
    definition = schema["$defs"]["ShotReferenceCue"]
    assert "text_anchor" in definition["properties"]
    assert "text_start" not in definition.get("required", [])
    assert schema["$defs"]["ShotTextAnchor"]["required"] == ["exact"]
    assert "text_anchor" in schema["$defs"]["ShotReferenceCue"]["properties"]
    with pytest.raises(ValueError, match="requires.*field"):
        ShotPlan.model_validate({**_board.storyboard.shots[0].model_dump(), "references": [{
            "subject_asset_id": "asset", "alias": "人物", "purpose": "参考", "text_anchor": {"exact": "人物"}}]})


def test_director_partial_request_resolves_typed_cues_against_actual_owning_shot(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    payload = _anchored_shot(shot)
    patch = ShotPlanPatch.model_validate({key: payload[key] for key in ("visual", "camera", "references", "timeline_beats")})
    cue = patch.references[0] if patch.references else None
    assert cue and cue.text_anchor and cue.text_start is None
    request = DirectorRepairDecisionRequest(decision=DirectorRepairDecision.APPLIED,
        strategy=DirectorRepairStrategy.CHANGE_CAMERA, reason="明确换机位及正文引用",
        expected_shot_revision=shot.revision, shot_patch=patch)
    # Advice can be returned as JSON before the owning Shot is assembled. A
    # pending quote must survive that handoff, then disappear after resolution.
    wire = request.model_dump_json()
    assert '"text_anchor"' in wire
    response = service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest.model_validate_json(wire))
    saved = service.get_storyboard(pid).storyboard.shots[0]
    assert saved == response.document.shots[0]
    assert [ref.text_start for ref in saved.references] == [6, 1]
    assert saved.timeline_beats[0].references[0].text_start == 2
    assert cue.text_anchor and cue.text_start is None
    assert '"text_anchor"' not in saved.model_dump_json()


def test_two_same_alias_occurrences_get_distinct_saved_ids_without_handmade_ids(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    payload = _anchored_shot(shot)
    beat = payload["timeline_beats"][0]
    first = beat["references"][0]
    second = deepcopy(first)
    first["text_anchor"] = {"exact": "@人物", "prefix": "左侧"}
    second["text_anchor"] = {"exact": "@人物", "suffix": "停住。"}
    beat["visual"] = "左侧@人物转身；右侧@人物停住。"
    beat["references"] = [first, second]
    result = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
        "request": {"expected_revision": shot.revision, "shot": payload}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    saved = service.get_storyboard(pid).storyboard.shots[0]
    refs = saved.timeline_beats[0].references
    assert refs[0].id != refs[1].id and all(cue.id for cue in refs)
    assert [cue.text_start for cue in refs] == [2, 10]
    assert refs[0].asset_version_id == refs[1].asset_version_id == shot.references[0].asset_version_id


def test_semantic_save_late_user_conflict_does_not_commit_formal_content_or_take_invalidation(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    service.studio.save_shot(pid, shot.id, StudioShotSave.model_validate({
        "expected_revision": shot.revision, "shot": _anchored_shot(shot)}))
    selected = service.get_storyboard(pid).storyboard.shots[0]
    service.repository.save_task(GenerationTask(id="local-test-task", project_id=pid, shot_id=selected.id,
        operation=OperationKind.GENERATE, status=TaskStatus.SUCCEEDED, candidate_index=1,
        provider="minimax", model="MiniMax-H3", requested_duration_seconds=selected.duration_seconds,
        attempt=1, request_fingerprint="local-test", input_script_version_id=board.storyboard.script_version_id,
        input_bible_version_id=board.storyboard.bible_version_id, input_shot_revision=selected.revision,
        created_at="2026-09-12T00:00:00Z", updated_at="2026-09-12T00:00:00Z"))
    take = _selected_take(pid, selected.id, "local-test-task", selected.duration_seconds,
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        generation_plan_document_id="test-plan", generation_plan_version_id="test-plan-version")
    service.repository.save_take(take)
    repository = PausingRepository(service.repository.database_path)
    service.repository = repository
    request = _semantic_request(selected, visual="将被冲突保护的正文。" + selected.visual)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.tool_registry.invoke, "change.apply", {
            "project_id": pid, "request": request.model_dump(mode="json")})
        try:
            assert repository.validated.wait(10)
            writer = ShortDramaService(database_path=repository.database_path, workspace_root=tmp_path / "work")
            # A real independent save wins after preparation and before commit.
            current = writer.get_project(pid).document.shots[0]
            human = _anchored_shot(current)
            human["visual"] = "用户已经改动目标正文。" + human["visual"]
            writer.studio.save_shot(pid, current.id, StudioShotSave.model_validate({
                "expected_revision": current.revision, "shot": human}))
            before = writer.get_project(pid).document
            before_take = writer.repository.get_take(take.id)
        finally:
            repository.continue_save.set()
        failed = pending.result(timeout=10)
    assert failed.status == ToolCallStatus.FAILED and failed.error
    assert "Selected content changed" in failed.error.message
    assert service.get_project(pid).document == before
    actual_take = service.repository.get_take(take.id)
    assert actual_take and actual_take == before_take


def test_full_formal_http_create_and_replace_resolve_quotes_before_storage(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "full.sqlite", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    board = _storyboard(service, pid, production_spec_document_id=spec.document.id,
        production_spec_version_id=spec.version.id)
    shot = board.shots[0].model_copy(update={"references": [ShotReferenceCue(
        subject_asset_id=document.assets[0].id, alias="待绑定身份", purpose="待选择图片")]})
    payload = board.model_dump(mode="json")
    payload["shots"][0] = _anchored_shot(shot)
    original = deepcopy(payload)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)
    response = client.post(f"/api/short-drama/projects/{pid}/storyboard", json={"storyboard": payload, "origin": "AGENT"})
    assert response.status_code == 200, response.text
    saved = service.get_storyboard(pid)
    assert saved.storyboard.shots[0].references[0].text_start == 6
    assert saved.storyboard.shots[0].timeline_beats[0].references[0].text_start == 2
    assert response.json()["storyboard"]["shots"][0] == saved.storyboard.shots[0].model_dump(mode="json")
    assert payload == original
    # Full replacement is another domain owner, not a separate offset algorithm.
    payload["shots"][0]["visual"] = "新前景。" + payload["shots"][0]["visual"]
    response = client.post(f"/api/short-drama/projects/{pid}/storyboard/replacements",
        json={"storyboard": payload, "base_version_id": saved.version.id, "origin": "AGENT"})
    assert response.status_code == 200, response.text
    actual = service.get_storyboard(pid).storyboard.shots[0]
    assert actual.references[0].text_start == 10
    assert service.get_project(pid).document.shots[0] == actual


def test_structural_splice_resolves_new_cues_and_preserves_other_groups(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    raw = _anchored_shot(shot)
    before = deepcopy(raw)
    result = service.tool_registry.invoke("shot.patch_storyboard", {"project_id": pid, "request": {
        "base_version_id": board.version.id, "origin": "AGENT",
        "shot_splices": [{"shot_ids": [shot.id], "shots": [raw]}]}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    actual = service.get_storyboard(pid).storyboard
    assert actual.shots[0].references[0].text_start == 6
    assert actual.shots[0].timeline_beats[0].references[0].text_start == 2
    assert actual.shots[1:] == board.storyboard.shots[1:]
    assert actual.clip_groups == board.storyboard.clip_groups
    assert raw == before
