"""A pending new-group append survives saved edits to other draft shots."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.models import ApprovalStatus, AssetMediaType, ProductionDocumentOrigin, ShotReferenceCue
from short_drama.production_contracts import (
    ClipGroup, StoryboardClipGroupDraft, StoryboardDraftAppendRequest, StoryboardDraftBeginRequest,
    StoryboardDraftContent, StoryboardWorkUnitContract,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.reference_planner import ReferencePlanPreviewResponse
from short_drama.studio_models import StudioMutationResult, StudioShotSave
from short_drama.studio_service import StudioWorkspaceService
from short_drama.studio_storyboard import current_draft
from short_drama.tools.contracts import ToolCallStatus
from tests.test_storyboard_incremental_service import (
    _draft, _prepare_script_only_project, _seed_from_draft, _service,
)


def _pending_append(tmp_path: Path) -> tuple[ShortDramaService, str, StoryboardDraftAppendRequest, str, str]:
    service = _service(tmp_path)
    document, script, spec, shots = _prepare_script_only_project(service)
    draft = _draft(document, script, spec, dialogue_shots=shots)
    assert len(shots) >= 4
    asset = document.assets[0]
    version = asset.versions[0]
    image = tmp_path / "reference.png"
    Image.new("RGB", (24, 36), "navy").save(image)
    version.local_path, version.external_url = str(image), None
    version.media_type, version.approval_status = AssetMediaType.IMAGE, ApprovalStatus.DRAFT
    # Keep the newly saved spec while adding the local fixture media.
    current = service.repository.get_project(document.project.id)
    assert current is not None
    current.assets[0] = asset
    service.repository.save_project(current)
    progress = service.begin_storyboard_draft(document.project.id, StoryboardDraftBeginRequest(
        seed=_seed_from_draft(draft), origin=ProductionDocumentOrigin.AGENT)).progress
    requests: list[StoryboardDraftAppendRequest] = []
    for index in range(4):
        outline = draft.planned_clip_groups[index]
        shot = shots[index].model_copy(update={"id": f"pending-shot-{index}", "sequence": index + 1,
            "scene_id": outline.source_range.scene_ids[0]}, deep=True)
        group = StoryboardClipGroupDraft(work_unit_id=f"work-{index}",
            scene_node_id=outline.scene_node_id, scene_group_sequence=index + 1,
            source_range=outline.source_range, shots=[shot],
            clip_group=ClipGroup.model_validate(outline.model_dump(include={"id", "title", "duration_seconds", "continuity_in", "continuity_out", "visible_changes"}) | {
                "shot_ids": [shot.id], "generation_intent": "Render the authored scene."}))
        request = StoryboardDraftAppendRequest(draft_document_id=progress.draft_ref.artifact_id,
            base_version_id=progress.draft_version_id, group=group,
            work_unit=StoryboardWorkUnitContract(id=group.work_unit_id, title=f"Author group {index}",
                source_range=group.source_range, objective="Persist this group", completion_criteria=["Saved"]),
            origin=ProductionDocumentOrigin.AGENT)
        requests.append(request)
        if index < 3:
            progress = service.append_storyboard_group(document.project.id, request).progress
    return service, document.project.id, requests[-1], asset.id, version.id


def _save_bindings(service: ShortDramaService, pid: str, asset_id: str, version_id: str) -> None:
    for index in [0, 2, 1]:
        shot = service.studio.current_project(pid).document.shots[index].model_copy(deep=True)
        shot.camera += "；本镜局部机位说明"
        shot.references = [ShotReferenceCue(subject_asset_id=asset_id, asset_version_id=version_id,
            alias="人物参考", purpose=f"镜头{index}的已选身份参考")]
        result = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
            "request": StudioShotSave(expected_revision=shot.revision, shot=shot).model_dump(mode="json")})
        assert result.status == ToolCallStatus.SUCCEEDED


def test_pending_append_retains_three_other_shot_saves_and_reopens(tmp_path: Path) -> None:
    service, pid, pending, asset_id, version_id = _pending_append(tmp_path)
    # The director already holds this request while a second service saves the
    # three ready shots, as happened during the real language call.
    builder = _service(tmp_path)
    _save_bindings(builder, pid, asset_id, version_id)
    before = service.get_production_document(pid, pending.draft_document_id).version
    assert before.id != pending.base_version_id
    result = service.append_storyboard_group(pid, pending)
    assert result.progress.draft_version_id != before.id
    reopened = _service(tmp_path).get_production_document(pid, pending.draft_document_id).version
    current = StoryboardDraftContent.model_validate(reopened.payload)
    old = StoryboardDraftContent.model_validate(before.payload)
    assert current.committed_groups[:3] == old.committed_groups
    assert current.committed_groups[3] == pending.group
    assert reopened.parent_version_id == before.id


def test_draft_saved_cues_return_usable_preview_and_legacy_plan_guidance(tmp_path: Path) -> None:
    service, pid, _pending, asset_id, version_id = _pending_append(tmp_path)
    shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
    shot.references = [ShotReferenceCue(subject_asset_id=asset_id, asset_version_id=version_id,
        alias="身份", purpose="当前已选人物图")]
    result = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
        "request": StudioShotSave(expected_revision=shot.revision, shot=shot).model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert result.output["reference_plan_preview_error"] is None
    assert result.output["reference_plan_preview"] is not None
    preview = ReferencePlanPreviewResponse.model_validate(result.output["reference_plan_preview"])
    assert [item.asset_version_id for item in preview.selected] == [version_id]
    assert preview.missing_requirement_ids == []
    before = service.repository.get_project(pid)
    legacy = service.tool_registry.invoke("shot.update_reference_plan", {"project_id": pid,
        "shot_id": shot.id, "request": {"requirements": [], "action_beats": [], "interactions": []}})
    assert legacy.status == ToolCallStatus.FAILED
    assert legacy.error is not None and "committed draft" in legacy.error.message and "shot.save_current" in legacy.error.message
    assert "already saved" in legacy.error.message
    assert service.repository.get_project(pid) == before
    assert before is not None and before.reference_plan == []


@pytest.mark.parametrize("changed", ["outline", "source", "phase", "group_identity", "same_work_unit", "same_group", "unknown_base", "continuity"])
def test_pending_append_still_rejects_relevant_changes(tmp_path: Path, changed: str) -> None:
    service, pid, pending, asset_id, version_id = _pending_append(tmp_path)
    _save_bindings(service, pid, asset_id, version_id)
    document = service.repository.get_project(pid)
    assert document is not None
    draft_document = next(d for d in document.production_documents if d.id == pending.draft_document_id)
    version = next(v for v in draft_document.versions if v.id == draft_document.current_version_id)
    draft = StoryboardDraftContent.model_validate(version.payload)
    if changed == "outline":
        draft.timing_notes.append("Updated shared timing direction")
    elif changed == "source":
        spec = next(d for d in document.production_documents if d.id == draft.production_spec_document_id)
        source = next(v for v in spec.versions if v.id == spec.current_version_id)
        revised = source.model_copy(update={"id": source.id + "-updated", "version": source.version + 1,
            "parent_version_id": source.id}, deep=True)
        spec.versions.append(revised)
        spec.current_version_id = revised.id
        draft.production_spec_version_id = revised.id
    elif changed == "phase":
        version.payload["phase"] = "LEDGER_BUILDING"
    elif changed == "group_identity":
        draft.committed_groups[0].clip_group.id = "another-group"
    elif changed == "same_work_unit":
        pending.work_unit.id = draft.committed_groups[0].work_unit_id
        pending.group.work_unit_id = pending.work_unit.id
    elif changed == "same_group":
        pending.group.clip_group.id = draft.committed_groups[0].clip_group.id
    elif changed == "unknown_base":
        pending.base_version_id = "not-this-drafts-version"
    elif changed == "continuity":
        draft.committed_groups[-1].shots[-1].continuity.action_out = "Changed actual outgoing action"
    if changed != "phase":
        version.payload = draft.model_dump(mode="json")
    service.repository.save_project(document)
    before = service.repository.get_project(pid)
    with pytest.raises(WorkflowConflictError):
        service.append_storyboard_group(pid, pending)
    assert service.repository.get_project(pid) == before


def test_draft_preview_reports_exact_unselected_cue_without_writing_a_plan(tmp_path: Path) -> None:
    service, pid, _pending, asset_id, version_id = _pending_append(tmp_path)
    document = service.repository.get_project(pid)
    assert document is not None
    missing_asset = document.assets[1]
    for version in missing_asset.versions:
        version.media_type = AssetMediaType.TEXT_REFERENCE
    service.repository.save_project(document)
    shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
    shot.references = [
        ShotReferenceCue(id="selected-cue", subject_asset_id=asset_id, asset_version_id=version_id,
            alias="已选身份", purpose="使用已保存图"),
        ShotReferenceCue(id="unselected-cue", subject_asset_id=missing_asset.id,
            alias="缺少图片", purpose="仍需图像的真实用途"),
    ]
    result = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
        "request": StudioShotSave(expected_revision=shot.revision, shot=shot).model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert "unselected-cue" in str(result.output["reference_plan_preview_error"])
    saved = service.studio.current_project(pid).document.shots[0]
    assert saved.references[0].asset_version_id == version_id
    assert saved.references[1].asset_version_id is None
    reopened = service.repository.get_project(pid)
    assert reopened is not None and reopened.reference_plan == []


@pytest.mark.parametrize("preview_change", ["missing_selected_version", "shot_not_in_draft"])
def test_saved_receipt_survives_changed_preview_inputs(tmp_path: Path, preview_change: str) -> None:
    service, pid, _pending, asset_id, version_id = _pending_append(tmp_path)
    shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
    shot.references = [ShotReferenceCue(id="saved-choice", subject_asset_id=asset_id,
        asset_version_id=version_id, alias="身份", purpose="实际保存的参考图")]

    class ChangedPreviewWorkspace(StudioWorkspaceService):
        def save_shot(self, project_id: str, shot_id: str, request: StudioShotSave) -> StudioMutationResult:
            result = super().save_shot(project_id, shot_id, request)
            # The write is real. Only its later read projection changes, as can
            # happen when current assets/draft membership change after saving.
            document = result.project.document
            if preview_change == "missing_selected_version":
                for asset in document.assets:
                    asset.versions = [version for version in asset.versions if version.id != version_id]
            else:
                draft = current_draft(document)
                assert draft is not None
                draft[2].committed_groups = [group for group in draft[2].committed_groups
                    if all(item.id != shot_id for item in group.shots)]
                draft[1].payload = draft[2].model_dump(mode="json")
                assert document.shots and draft[2].committed_groups
            return result

    service.studio = ChangedPreviewWorkspace(service, lock=service.studio.lock)
    result = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
        "request": StudioShotSave(expected_revision=shot.revision, shot=shot).model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    assert result.output["reference_plan_preview"] is None
    error = str(result.output["reference_plan_preview_error"])
    if preview_change == "missing_selected_version":
        assert f"unknown selected AssetVersion {version_id}" in error
    else:
        assert "尚无参考计划" in error
        assert "草稿与已有绑定" not in error
    assert result.receipt is not None
    assert result.receipt.current_state["saved_shot"] == result.output["saved_shot"]
    assert result.output["shot_revisions"] == {shot.id: shot.revision + 1}
    reopened = _service(tmp_path).studio.current_project(pid).document
    saved = next(item for item in reopened.shots if item.id == shot.id)
    assert saved.revision == shot.revision + 1
    assert saved.references[0].asset_version_id == version_id
    assert any(version.id == version_id for asset in reopened.assets for version in asset.versions)
    assert reopened.reference_plan == []
