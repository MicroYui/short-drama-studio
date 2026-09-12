"""Actual Registry saves selected media once across Shot, plan, and reference pack."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from collections.abc import Callable, Sequence
from threading import Event
from typing import Any, cast

import pytest
from PIL import Image
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.image_models import AssetImportLocalRequest
from short_drama.models import (
    ApprovalStatus, AssetKind, AssetVersion, ContentPlanningOutput, Freshness, GenerationTask,
    ProductionDocumentOrigin, ProjectDocument, ProjectStage, QAReport, ShotPlan, ShotReferenceCue, Take,
)
from short_drama.production_contracts import (
    AssetPlanCreateRequest, AssetPlanFieldPatch, AssetPlanPatchRequest, ProductionSpecCreateRequest,
    StoryboardClipGroupDraft, StoryboardDraftAppendRequest, StoryboardDraftBeginRequest,
    StoryboardDraftContent, StoryboardDraftSceneSeed, StoryboardDraftSeed, StoryboardSourceRange,
    StoryboardWorkUnitContract,
)
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.production_profiles import ProductionSkillRole
from short_drama.reference_planner import ReferencePackEntryCreateRequest
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioShotSave
from short_drama.tools.contracts import ToolCallStatus, ToolResult
from tests.test_asset_plan_runtime import _asset_plan, _service, _setup_story_truth
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_storyboard_agent_interfaces import _production_spec
from tests.test_storyboard_dialogue_continuation import _group


@dataclass
class Case:
    service: ShortDramaService
    project_id: str
    shot: ShotPlan
    version: AssetVersion
    asset_id: str
    arguments: dict[str, Any]


@pytest.fixture
def case(tmp_path: Path) -> Case:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    content = _asset_plan(service, pid, spec=spec, storyboard=board, approve_sources=False)
    item = content.items[0]
    assert item.completed_asset_id
    asset_id = item.completed_asset_id
    item.completed_asset_id = item.completed_asset_version_id = None
    item.work_items[0].completed_asset_id = item.work_items[0].completed_asset_version_id = None
    saved = service.create_asset_plan(pid, AssetPlanCreateRequest(asset_plan=content, origin=ProductionDocumentOrigin.AGENT))
    path = tmp_path / "selected.png"
    Image.new("RGB", (96, 128), "#456789").save(path)
    version = service.import_local_asset_version(pid, asset_id,
        AssetImportLocalRequest(local_path=str(path), descriptor="Explicitly chosen close-up; not reviewed.")).version
    shot = service.get_project(pid).document.shots[0]
    changed = shot.model_copy(deep=True)
    changed.references.append(ShotReferenceCue(subject_asset_id=asset_id, asset_version_id=version.id,
        alias="当前近景", purpose="Use this exact identity and composition for this Shot.",
        usage="GENERATION", image_reference_role="COMPOSITION"))
    arguments: dict[str, Any] = {"project_id": pid, "shot_id": shot.id,
        "request": {"expected_revision": shot.revision, "shot": changed.model_dump(mode="json")},
        "media_registration": {"asset_version_id": version.id,
            "plan_completion": {"base_version_id": saved.version.id,
                "item_ids": [item.id], "work_item_ids": [item.work_items[0].id]},
            "reference_entries": [{"subject_asset_id": asset_id, "asset_version_id": version.id,
                "role": "CANONICAL_MASTER", "description": "Chosen identity image for this Shot."}]}}
    return Case(service, pid, shot, version, asset_id, arguments)


def _invoke(case: Case) -> ToolResult:
    return case.service.tool_registry.invoke("shot.save_current", case.arguments)


def _turn(name: str, arguments: dict[str, JsonValue], result: ToolResult, seq: int) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"turn-{seq}", sequence=seq, tool_name=name, arguments=arguments,
        reason="Use explicit current media.", status=SpecialistTurnStatus.COMPLETED, result=result.model_dump(mode="json"))


def _take(case: Case, shot: ShotPlan) -> Take:
    now = "2026-09-12T00:00:00Z"
    return Take(id=f"take-{shot.id}", project_id=case.project_id, shot_id=shot.id, task_id="existing-task",
        candidate_index=1, operation="GENERATE", media_path="existing.mp4", thumbnail_path="existing.jpg",
        duration_seconds=shot.duration_seconds, width=720, height=1280, availability="AVAILABLE",
        qa_status="PASSED", is_selected=True, freshness="CURRENT", created_at=now,
        qa_report=QAReport(id=f"qa-{shot.id}", take_id=f"take-{shot.id}", status="PASSED",
            hard_gate_passed=True, score=4, issues=[], checks=[], created_at=now))



def _save_take(case: Case, take: Take) -> None:
    document = case.service.get_project(case.project_id).document
    take.task_id = "task-" + take.id
    case.service.repository.save_task(GenerationTask(id=take.task_id, project_id=case.project_id, shot_id=take.shot_id,
        operation="GENERATE", candidate_index=1, status="SUCCEEDED", provider="mock", model="local-test-fixture",
        request_fingerprint=take.id, input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id, requested_duration_seconds=take.duration_seconds,
        attempt=1, created_at=take.created_at, updated_at=take.created_at))
    case.service.repository.save_take(take)

def test_selected_draft_saves_all_content_once_and_replays_after_restart(case: Case, tmp_path: Path) -> None:
    before = case.service.get_project(case.project_id).document
    events_before = len(case.service.repository.list_studio_events(case.project_id))
    selected, other = _take(case, before.shots[0]), _take(case, before.shots[1])
    _save_take(case, selected)
    _save_take(case, other)
    original_tasks = case.service.repository.list_tasks(case.project_id)
    result = _invoke(case)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output and result.receipt
    document = case.service.get_project(case.project_id).document
    assert document.assets == before.assets  # No approval/current/identity changes.
    assert document.shots[1:] == before.shots[1:]
    assert document.shots[0].references[-1].asset_version_id == case.version.id
    assert document.shots[0].revision == case.shot.revision + 1
    board = case.service.get_storyboard(case.project_id).storyboard
    assert board.shots[0] == document.shots[0]
    plan = case.service.get_asset_plan(case.project_id)
    assert plan.asset_plan.items[0].completed_asset_version_id == case.version.id
    assert plan.asset_plan.items[0].work_items[0].completed_asset_version_id == case.version.id
    assert plan.version.version == 2
    assert result.output["asset_plan_identity"] == result.receipt.current_state["asset_plan_identity"]
    entries = [entry for pack in document.reference_packs for entry in pack.entries if entry.asset_version_id == case.version.id]
    assert len(entries) == 1
    assert len(case.service.repository.list_studio_events(case.project_id)) == events_before + 1
    changed_take = case.service.repository.get_take(selected.id)
    assert changed_take and changed_take.freshness == Freshness.STALE and not changed_take.is_selected
    assert case.service.repository.get_take(other.id) == other
    assert not case.service.repository.list_image_tasks(case.project_id)
    assert case.service.repository.list_tasks(case.project_id) == original_tasks

    restarted = _service(tmp_path)
    result2 = restarted.tool_registry.invoke("shot.save_current", case.arguments)
    assert result2.status == ToolCallStatus.SUCCEEDED, result2.error
    assert result2.output and result2.receipt
    assert cast(dict[str, Any], result2.output["media_registration"])["reused"] is True
    assert result2.output["changed_shot_ids"] == []
    assert result2.receipt.changed_ids["changed_item_ids"] == []
    assert restarted.get_project(case.project_id).document == document
    assert len(restarted.repository.list_studio_events(case.project_id)) == events_before + 1


@pytest.mark.parametrize("change", ["shot", "plan_target", "reference_metadata", "bad_cue"])
def test_conflicts_and_late_cue_failure_write_none_of_the_registration(case: Case, change: str) -> None:
    if change == "shot":
        revised = case.shot.model_copy(deep=True, update={"camera": "User changed the actual camera."})
        case.service.studio.save_shot(case.project_id, revised.id,
            StudioShotSave(shot=revised, expected_revision=revised.revision))
    elif change == "plan_target":
        case.service.patch_asset_plan(case.project_id, AssetPlanPatchRequest(
            base_version_id=case.arguments["media_registration"]["plan_completion"]["base_version_id"],
            field_patches=[AssetPlanFieldPatch(work_item_id="work-linxia-master", op="replace", path="/final_prompt",
                value="User changed the source intent, preserve this edit.")], origin=ProductionDocumentOrigin.USER))
    elif change == "reference_metadata":
        entry = deepcopy(case.arguments["media_registration"]["reference_entries"][0])
        entry["description"] = "User's different use of the same reference key."
        case.service.add_reference_pack_entry(case.project_id, ReferencePackEntryCreateRequest.model_validate(entry))
    else:
        case.arguments["request"]["shot"]["references"][-1].update(field="visual", text_start=0, text_end=100000)
    before = case.service.get_project(case.project_id).document
    result = _invoke(case)
    assert result.status != ToolCallStatus.SUCCEEDED
    assert case.service.get_project(case.project_id).document == before


def test_stale_base_merges_unrelated_plan_and_shot_user_edits(case: Case) -> None:
    base = case.arguments["media_registration"]["plan_completion"]["base_version_id"]
    case.service.patch_asset_plan(case.project_id, AssetPlanPatchRequest(base_version_id=base,
        field_patches=[AssetPlanFieldPatch(item_id="asset-plan-prop-recorder", op="replace", path="/rationale",
            value="User keeps the current prop plan.")], notes=["Keep this shared user note."], origin=ProductionDocumentOrigin.USER))
    other = case.service.get_project(case.project_id).document.shots[1].model_copy(deep=True)
    other.camera = "User's unrelated shot stays untouched."
    case.service.studio.save_shot(case.project_id, other.id, StudioShotSave(shot=other, expected_revision=other.revision))
    before = case.service.get_project(case.project_id).document
    result = _invoke(case)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.receipt
    plan = case.service.get_asset_plan(case.project_id)
    assert plan.asset_plan.notes == ["Keep this shared user note."]
    assert plan.asset_plan.items[1].rationale == "User keeps the current prop plan."
    assert case.service.get_project(case.project_id).document.shots[1:] == before.shots[1:]
    assert cast(dict[str, Any], result.receipt.current_state["base_asset_plan_identity"])["version"] == 2


def test_work_only_completion_preserves_a_concurrent_sibling_work_edit(case: Case) -> None:
    completion = case.arguments["media_registration"]["plan_completion"]
    completion["item_ids"] = []
    case.service.patch_asset_plan(case.project_id, AssetPlanPatchRequest(base_version_id=completion["base_version_id"],
        field_patches=[AssetPlanFieldPatch(work_item_id="work-linxia-derived", op="replace", path="/final_prompt",
            value="Another worker's intended profile.")], origin=ProductionDocumentOrigin.AGENT))
    result = _invoke(case)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    plan = case.service.get_asset_plan(case.project_id).asset_plan
    assert plan.items[0].completed_asset_id is None
    assert plan.items[0].work_items[0].completed_asset_version_id == case.version.id
    assert plan.items[0].work_items[1].final_prompt == "Another worker's intended profile."


@pytest.mark.parametrize("state", ["missing", "REJECTED", "STALE"])
def test_unusable_exact_media_is_rejected_without_any_content_write(case: Case, state: str) -> None:
    document = case.service.get_project(case.project_id).document
    version = next(version for asset in document.assets for version in asset.versions if version.id == case.version.id)
    if state == "missing":
        assert version.local_path
        Path(version.local_path).unlink()
    elif state == "REJECTED":
        version.approval_status = ApprovalStatus.REJECTED
    else:
        version.freshness = Freshness.STALE
    case.service.repository.save_project(document)
    before = case.service.get_project(case.project_id).document
    result = _invoke(case)
    assert result.status == ToolCallStatus.FAILED
    assert case.service.get_project(case.project_id).document == before


def test_take_update_failure_rolls_back_the_content_and_events(case: Case) -> None:
    _save_take(case, _take(case, case.shot))
    before = case.service.get_project(case.project_id).document
    events = case.service.repository.list_studio_events(case.project_id)
    with sqlite3.connect(case.service.repository.database_path) as connection:
        connection.execute("CREATE TRIGGER fail_take BEFORE UPDATE ON takes BEGIN SELECT RAISE(ABORT, 'test take write failure'); END")
    result = _invoke(case)
    assert result.status == ToolCallStatus.FAILED
    assert result.error and "test take write failure" in result.error.message
    assert case.service.get_project(case.project_id).document == before
    assert case.service.repository.list_studio_events(case.project_id) == events
    persisted = case.service.repository.get_take(f"take-{case.shot.id}")
    assert persisted and persisted.is_selected and persisted.freshness == Freshness.CURRENT


class PausingRepository(ShortDramaRepository):
    """A real SQLite repository with an explicit pause at the commit boundary."""

    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self.validated = Event()
        self.continue_save = Event()

    def save_shot_media_registration(
        self, document: ProjectDocument, changed_shot_ids: Sequence[str], *,
        validate_merged: Callable[[ProjectDocument], None], write_content: bool = True,
    ) -> None:
        self.validated.set()
        assert self.continue_save.wait(10), "Concurrent test writer did not finish"
        super().save_shot_media_registration(document, changed_shot_ids,
            validate_merged=validate_merged, write_content=write_content)


def test_another_service_user_edit_between_validation_and_commit_is_not_partially_overwritten(case: Case, tmp_path: Path) -> None:
    repository = PausingRepository(case.service.repository.database_path)
    case.service.repository = repository
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(_invoke, case)
        try:
            assert repository.validated.wait(10)
            writer = _service(tmp_path)
            shot = writer.get_project(case.project_id).document.shots[0].model_copy(deep=True)
            shot.camera = "User wins this edit while the other service is ready to commit."
            writer.studio.save_shot(case.project_id, shot.id, StudioShotSave(shot=shot, expected_revision=shot.revision))
            before_commit = writer.get_project(case.project_id).document
        finally:
            repository.continue_save.set()
        result = pending.result(timeout=10)
    assert result.status == ToolCallStatus.FAILED
    assert result.error and "Selected content changed" in result.error.message
    assert case.service.get_project(case.project_id).document == before_commit


@pytest.mark.parametrize("change", ["reject_media", "source_path", "remove_cue", "camera", "review_notes", "current_pointer", "same_reference_key", "other_reference_key"])
def test_commit_rechecks_actual_media_and_cue_without_rejecting_unrelated_edits(case: Case, tmp_path: Path, change: str) -> None:
    if change in {"same_reference_key", "other_reference_key"}:
        # Force merging entries inside an existing pack, not just conflicting
        # creation of the pack object itself.
        entry = deepcopy(case.arguments["media_registration"]["reference_entries"][0])
        entry["angle"] = "LEFT_PROFILE"
        case.service.add_reference_pack_entry(case.project_id, ReferencePackEntryCreateRequest.model_validate(entry))
    if change in {"remove_cue", "camera", "same_reference_key", "other_reference_key"}:
        # Registration alone must respect a cue's current use, not overwrite the
        # Shot merely to turn a read dependency into a write-conflict sentinel.
        plain = case.service.tool_registry.invoke("shot.save_current", {
            key: value for key, value in case.arguments.items() if key != "media_registration"})
        assert plain.status == ToolCallStatus.SUCCEEDED, plain.error
        current = case.service.get_project(case.project_id).document.shots[0]
        case.arguments["request"] = {"expected_revision": current.revision, "shot": current.model_dump(mode="json")}
    repository = PausingRepository(case.service.repository.database_path)
    case.service.repository = repository
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(_invoke, case)
        try:
            assert repository.validated.wait(10)
            writer = _service(tmp_path)
            document = writer.get_project(case.project_id).document
            if change in {"reject_media", "review_notes", "source_path"}:
                version = next(version for asset in document.assets for version in asset.versions if version.id == case.version.id)
                if change == "reject_media":
                    version.approval_status = ApprovalStatus.REJECTED
                elif change == "source_path":
                    path = tmp_path / "different-source.png"
                    Image.new("RGB", (96, 128), "#fedcba").save(path)
                    version.local_path = str(path)
                else:
                    version.review_notes = "User has recorded an additional review observation."
                writer.repository.save_project(document)
            elif change == "current_pointer":
                path = tmp_path / "new-current.png"
                Image.new("RGB", (96, 128), "#abcdef").save(path)
                newer = writer.import_local_asset_version(case.project_id, case.asset_id,
                    AssetImportLocalRequest(local_path=str(path), descriptor="A separate current choice.")).version
                document = writer.get_project(case.project_id).document
                asset = next(asset for asset in document.assets if asset.id == case.asset_id)
                asset.current_version_id = newer.id
                writer.repository.save_project(document)
            elif change in {"same_reference_key", "other_reference_key"}:
                entry = deepcopy(case.arguments["media_registration"]["reference_entries"][0])
                if change == "other_reference_key":
                    entry["angle"] = "RIGHT_PROFILE"
                writer.add_reference_pack_entry(case.project_id, ReferencePackEntryCreateRequest.model_validate(entry))
            else:
                shot = document.shots[0].model_copy(deep=True)
                if change == "remove_cue":
                    shot.references = []
                else:
                    shot.camera = "User changes camera but keeps the exact selected image cue."
                writer.studio.save_shot(case.project_id, shot.id, StudioShotSave(shot=shot, expected_revision=shot.revision))
            latest = writer.get_project(case.project_id).document
        finally:
            repository.continue_save.set()
        result = pending.result(timeout=10)
    saved = case.service.get_project(case.project_id).document
    if change in {"camera", "review_notes", "current_pointer", "other_reference_key"}:
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        assert saved.assets == latest.assets
        if change == "camera":
            assert saved.shots == latest.shots
        if change == "other_reference_key":
            assert {entry.angle.value for pack in saved.reference_packs for entry in pack.entries
                if entry.asset_version_id == case.version.id} == {"LEFT_PROFILE", "RIGHT_PROFILE", "NOT_APPLICABLE"}
    else:
        assert result.status == ToolCallStatus.FAILED
        assert saved == latest
    if change == "same_reference_key":
        retried = _invoke(case)
        assert retried.status == ToolCallStatus.SUCCEEDED, retried.error
        assert len([entry for pack in case.service.get_project(case.project_id).document.reference_packs
            for entry in pack.entries if entry.asset_version_id == case.version.id
            and entry.angle.value == "NOT_APPLICABLE"]) == 1


def test_retry_acknowledges_actual_plan_identity_without_claiming_a_parallel_delta(case: Case, tmp_path: Path) -> None:
    assert _invoke(case).status == ToolCallStatus.SUCCEEDED
    repository = PausingRepository(case.service.repository.database_path)
    case.service.repository = repository
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(_invoke, case)
        try:
            assert repository.validated.wait(10)
            writer = _service(tmp_path)
            plan = writer.get_asset_plan(case.project_id)
            changed = writer.patch_asset_plan(case.project_id, AssetPlanPatchRequest(base_version_id=plan.version.id,
                field_patches=[AssetPlanFieldPatch(item_id=plan.asset_plan.items[1].id, op="replace", path="/rationale",
                    value="Unrelated current work stays visible.")], origin=ProductionDocumentOrigin.USER))
            latest = writer.get_project(case.project_id).document
            events = writer.repository.list_studio_events(case.project_id)
        finally:
            repository.continue_save.set()
        result = pending.result(timeout=10)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.receipt and result.output
    identity = result.receipt.current_state["asset_plan_identity"]
    assert isinstance(identity, dict) and identity["version_id"] == changed.version.id
    assert result.receipt.current_state["base_asset_plan_identity"] == identity
    assert not result.receipt.changed_ids["changed_item_ids"]
    assert result.output["media_registration"]["reused"] is True
    assert case.service.get_project(case.project_id).document == latest
    assert case.service.repository.list_studio_events(case.project_id) == events


def test_reference_only_save_updates_the_actual_draft_and_replays_without_a_plan(tmp_path: Path) -> None:
    service = _service(tmp_path, "draft")
    document = service.initialize_fixture().document
    original = document.shots[:2]
    asset = next(asset for asset in document.assets if asset.kind == AssetKind.CHARACTER)
    path = tmp_path / "draft-reference.png"
    Image.new("RGB", (64, 96), "#789abc").save(path)
    version = service.import_local_asset_version(document.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="Chosen draft Shot image.")).version
    document = service.get_project(document.project.id).document
    script = next(script for script in document.script_versions if script.id == document.project.current_script_version_id)
    scene = script.structured_scenes[0]
    document.project.content_planning_output = ContentPlanningOutput.SCRIPT_ONLY
    document.project.stage = ProjectStage.SCRIPT_READY
    document.shots, document.execution_plan, document.reference_plan = [], [], []
    service.repository.save_project(document)
    pid = document.project.id
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    progress = service.begin_storyboard_draft(pid, StoryboardDraftBeginRequest(seed=StoryboardDraftSeed(
        project_id=pid, title="Partial draft", script_version_id=script.id,
        bible_version_id=document.project.current_bible_version_id, production_spec_document_id=spec.document.id,
        production_spec_version_id=spec.version.id,
        scene_nodes=[StoryboardDraftSceneSeed(id="scene-node", scene_id=scene.id, sequence=1)]),
        origin=ProductionDocumentOrigin.AGENT)).progress
    for index, shot in enumerate(original, 1):
        shot = shot.model_copy(deep=True, update={"scene_id": scene.id})
        source = StoryboardSourceRange(id=f"source-{index}", scene_ids=[scene.id], script_line_ids=shot.dialogue_line_ids,
            description="Keep the actual current source.")
        work = StoryboardWorkUnitContract(id=f"work-{index}", title="Current group", source_range=source,
            objective="Keep the original current group.", completion_criteria=["Saved current content"])
        progress = service.append_storyboard_group(pid, StoryboardDraftAppendRequest(
            draft_document_id=progress.draft_ref.artifact_id, base_version_id=progress.draft_version_id,
            work_unit=work, group=StoryboardClipGroupDraft(work_unit_id=work.id, scene_node_id="scene-node",
                scene_group_sequence=index, source_range=source, shots=[shot], clip_group=_group([shot], f"group-{index}")),
            origin=ProductionDocumentOrigin.AGENT)).progress
    before = service.studio.current_project(pid).document
    changed = before.shots[0].model_copy(deep=True)
    changed.references = [ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
        alias="当前图片", purpose="Use the selected evidence image.", usage="EDITORIAL")]
    args: dict[str, JsonValue] = {"project_id": pid, "shot_id": changed.id,
        "request": {"shot": changed.model_dump(mode="json"), "expected_revision": changed.revision},
        "media_registration": {"asset_version_id": version.id, "reference_entries": [
            {"subject_asset_id": asset.id, "asset_version_id": version.id, "role": "CANONICAL_MASTER",
             "description": "Current draft reference."}]}}
    result = service.tool_registry.invoke("shot.save_current", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    saved = service.get_project(pid).document
    actual = service.get_production_document(pid, progress.draft_ref.artifact_id)
    draft = StoryboardDraftContent.model_validate(actual.version.payload)
    assert draft.committed_groups[0].shots[0].references[0].asset_version_id == version.id
    assert draft.committed_groups[1].shots[0] == before.shots[1]
    assert saved.asset_plan_document_id is None and saved.assets == before.assets
    repeated = service.tool_registry.invoke("shot.save_current", args)
    assert repeated.status == ToolCallStatus.SUCCEEDED, repeated.error
    assert service.get_project(pid).document == saved


def test_real_tool_receipt_updates_restarted_worker_plan_and_shot_facts(case: Case, tmp_path: Path) -> None:
    read_args: dict[str, JsonValue] = {"project_id": case.project_id, "scope": {"mode": "ITEM", "item_ids": [
        "asset-plan-character-linxia", "asset-plan-prop-recorder"]}}
    read = case.service.tool_registry.invoke("artifact.get_asset_work_context", read_args)
    assert read.status == ToolCallStatus.SUCCEEDED, read.error
    written = _invoke(case)
    assert written.status == ToolCallStatus.SUCCEEDED and written.output, written.error
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = case.project_id
    work.production_skill_role = ProductionSkillRole.ASSET_BUILDER
    work.specialist_turns = [_turn("artifact.get_asset_work_context", read_args, read, 1),
        _turn("shot.save_current", case.arguments, written, 2)]
    work.loaded_tool_schema_ids = ["artifact.read_current"]
    store.save_work_order(work)
    original = work.model_dump(mode="json")
    restarted = AgentRuntimeStore(store.database_path)
    resumed = restarted.get_work_order(work.id)
    assert resumed
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue with another shot.",
        "arguments": {"project_id": case.project_id, "shot_id": case.service.get_project(case.project_id).document.shots[1].id}})])
    worker = LanguageSpecialistAgent(client, case.service.tool_registry)
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Continue chosen media work."))
    assert len(client.payloads) == 1
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    items = [entry for entry in working["entities"] if entry["kind"] == "asset_plan_item"]
    assert [entry["id"] for entry in items] == ["asset-plan-prop-recorder"]
    assert items[0]["state"]["asset_plan_identity"]["version"] == 1
    assert items[0]["state"]["unchanged_through_asset_plan_identity"] == written.output["asset_plan_identity"]
    latest = working["latest_handoff"]["result"]["receipt"]["current_state"]
    assert latest["asset_plan_identity"] == written.output["asset_plan_identity"]
    assert latest["saved_shot"]["shot_cues"][-1]["cue"]["asset_version_id"] == case.version.id
    persisted = restarted.get_work_order(work.id)
    assert persisted and persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == {
        key: value for key, value in original.items() if key != "loaded_tool_schema_ids"}
