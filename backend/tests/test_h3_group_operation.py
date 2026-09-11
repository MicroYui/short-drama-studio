"""Real H3 task preparation/resumption with Fake language and no provider submits."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.agent_runtime.models import ArtifactRef
from short_drama.h3_group_operation import H3GroupOperationRequest, prepare_h3_group
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProviderConfigUpdate
from short_drama.models import ApprovalStatus, AssetMediaType, DirectorRepairDecision, DirectorRepairStrategy, EnqueueRequest, Freshness, ProductionDocumentOrigin, ReferenceBinding, ShotReferenceCue, TaskStatus, VideoGenerationRoute
from short_drama.production_contracts import GenerationPlanCompileRequest, StoryboardPatchRequest, StoryboardShotReplacement
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.reference_planner import DirectorRepairDecisionRequest, ReplacementShotDraft
from short_drama.service import ShortDramaService
from short_drama.studio_trace import trace_context
from short_drama.tools.contracts import ToolCallStatus
from short_drama.video_inspection import selected_generation_reference_ids, semantic_reference_entries
from tests.test_h3_generation_unit_language import _fixture
from tests.test_h3_context_ir import FakeIR, due
from tests.test_short_drama_h3 import FakeH3Client


def test_prepare_and_restart_enqueue_keep_one_language_result_route_and_real_task(tmp_path: Path) -> None:
    service, fake, document, generation, unit, _request = _fixture(tmp_path)
    request = H3GroupOperationRequest(shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[])
    prepared = service.prepare_h3_group(document.project.id, request)
    assert prepared.status == "PREPARED", prepared.error
    assert prepared.generation_plan_version_id == generation.version.id
    assert prepared.shot_ids == unit.shot_ids
    assert prepared.task_ids == []
    assert len(fake.calls) == 1
    repeated = service.prepare_h3_group(document.project.id, request)
    assert repeated.operation_id == prepared.operation_id
    assert repeated.route_plan_id == prepared.route_plan_id
    assert len(fake.calls) == 1
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: fake)
    queued = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    assert len(queued.task_ids) == 1
    task = restarted.repository.get_task(queued.task_ids[0])
    assert task is not None and task.provider == "minimax" and task.model == "MiniMax-H3"
    assert task.generation_unit_id == unit.id
    assert [window.shot_id for window in task.shot_windows] == unit.shot_ids
    again = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert again.task_ids == queued.task_ids
    assert len(restarted.repository.list_h3_route_plans(document.project.id)) == 1
    assert len(fake.calls) == 1
    task.status = TaskStatus.SUBMIT_UNKNOWN
    restarted.repository.save_task(task)
    uncertain = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert uncertain.status == "SUBMIT_UNKNOWN"
    assert uncertain.next_action == "RECONCILE_EXISTING_TASK"
    assert uncertain.task_ids == queued.task_ids
    assert len(restarted.repository.list_tasks(document.project.id)) == 1
    assert len(fake.calls) == 1


def test_enqueue_failure_retains_prompt_and_route_and_resumes_after_configuration_is_fixed(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    first = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.clip_group_id, route=VideoGenerationRoute.DIRECT, media_inputs=[], enqueue=True,
    ))
    assert first.status == "BLOCKED"
    assert first.phase == "TASK_ENQUEUE"
    assert "API token" in (first.error or "")
    assert first.completed_steps == ["CURRENT_GROUP_INPUTS_RESOLVED", "PROMPT_COMPILED", "ROUTE_SAVED"]
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=first.operation_id, enqueue=True))
    assert resumed.status == "WAITING_MEDIA", resumed.error
    assert resumed.route_plan_id == first.route_plan_id
    assert len(fake.calls) == 1


def test_failed_compiler_is_not_repeated_without_requested_retry(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    good = fake.output_text
    fake.output_text = "Invalid provider output"
    request = H3GroupOperationRequest(clip_group_id=unit.clip_group_id, route=VideoGenerationRoute.DIRECT, media_inputs=[])
    first = service.prepare_h3_group(document.project.id, request)
    assert first.status == "BLOCKED" and first.phase == "PROMPT_COMPILATION"
    second = service.prepare_h3_group(document.project.id, request)
    assert second.operation_id == first.operation_id
    assert len(fake.calls) == 1
    fake.output_text = good
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=first.operation_id, retry_failed_step=True))
    assert resumed.status == "PREPARED", resumed.error
    assert len(fake.calls) == 2


def test_unrelated_group_edit_and_global_recompile_reuse_exact_prepared_group(tmp_path: Path) -> None:
    service, fake, document, generation, unit, _request = _fixture(tmp_path)
    request = H3GroupOperationRequest(shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[])
    first = service.prepare_h3_group(document.project.id, request)
    board = service.get_storyboard(document.project.id)
    other = board.storyboard.shots[2]
    changed = other.model_copy(deep=True)
    changed.camera = "A fixed composition for the unrelated clip."
    revised = service.patch_storyboard(document.project.id, StoryboardPatchRequest(
        base_version_id=board.version.id, origin=ProductionDocumentOrigin.USER,
        replacements=[StoryboardShotReplacement(shot_id=other.id, expected_revision=other.revision, replacement=changed)],
    ))
    service.compile_generation_plan(document.project.id, GenerationPlanCompileRequest(
        origin=ProductionDocumentOrigin.AGENT, base_version_id=generation.version.id,
        storyboard_document_id=revised.document.id, storyboard_version_id=revised.version.id,
        production_spec_document_id=generation.generation_plan.production_spec_document_id,
        production_spec_version_id=generation.generation_plan.production_spec_version_id,
    ))
    second = service.prepare_h3_group(document.project.id, request)
    assert second.status == "PREPARED", second.error
    assert second.operation_id == first.operation_id
    assert second.generation_plan_version_id == first.generation_plan_version_id
    assert second.route_plan_id == first.route_plan_id
    assert len(fake.calls) == 1


def test_current_group_change_recompiles_plan_and_preserves_old_operation(tmp_path: Path) -> None:
    service, fake, document, generation, unit, _request = _fixture(tmp_path)
    request = H3GroupOperationRequest(shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[])
    first = service.prepare_h3_group(document.project.id, request)
    document = service.get_project(document.project.id).document
    document.shots[0].revision += 1
    service.repository.save_project(document)
    stale = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=first.operation_id))
    assert stale.status == "INPUTS_CHANGED"
    assert len(fake.calls) == 1
    second = service.prepare_h3_group(document.project.id, request)
    assert second.status == "PREPARED", second.error
    assert second.operation_id != first.operation_id
    assert second.generation_plan_version_id != generation.version.id
    assert len(fake.calls) == 2
    old = service.repository.get_h3_group_operation(first.operation_id)
    assert old is not None and old.compiled_prompt == fake.output_text


def test_parallel_identical_group_calls_share_compilation_and_tool_receipt_refs(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    request = H3GroupOperationRequest(shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.prepare_h3_group, document.project.id, request) for _ in range(2)]
        results = [future.result() for future in futures]
    assert all(result.status == "PREPARED" for result in results)
    assert results[0].operation_id == results[1].operation_id
    assert len(fake.calls) == 1
    tool = service.tool_registry.invoke("route.prepare_h3_group", {
        "project_id": document.project.id, "request": {"operation_id": results[0].operation_id},
    })
    assert tool.status == ToolCallStatus.SUCCEEDED, tool.error
    assert tool.receipt is not None and tool.receipt.current_state["status"] == "PREPARED"
    assert {ref.artifact_type for ref in tool.artifact_refs} == {"h3_group_operation", "generation_plan", "generation_unit", "h3_route_plan"}
    resolved = ProjectArtifactResolver(service.repository).summarize_artifact(ArtifactRef(
        project_id=document.project.id, artifact_type="h3_group_operation", artifact_id=results[0].operation_id,
    ))
    assert resolved is not None and "PREPARED" in resolved.summary
    assert fake.output_text not in resolved.summary


def test_rework_creates_new_group_route_but_reuses_same_valid_prompt(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[],
    ))
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[unit.shot_ids[0]], candidate_count=1))
    source = service.drain(document.project.id, 50).project.takes[0]
    replacement = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[], source_take_id=source.id,
    ))
    assert replacement.status == "PREPARED", replacement.error
    assert replacement.operation_id != prepared.operation_id
    assert replacement.route_plan_id != prepared.route_plan_id
    assert replacement.source_take_id == source.id
    assert replacement.shot_ids == unit.shot_ids
    assert "PROMPT_REUSED" in replacement.completed_steps
    assert len(fake.calls) == 1


def test_operation_retains_inherited_methods_when_resumed_outside_the_worker(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    run = service.agent_runtime.create_run(project_id=document.project.id, objective="Generate group", root_agent="leader",
        active_skill_ids=["method.drama_series"])
    work = service.agent_runtime.create_work_order(run.id, sender="leader", recipient_role="video",
        objective="Prepare this group", why_now="Current references are ready",
        production_skill_role=ProductionSkillRole.VIDEO_PROMPT_COMPILER, production_prompt_stage=ProductionPromptStage.VISUAL_GEN)
    with trace_context(work_order_id=work.id, run_id=run.id):
        first = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
            shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[]))
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=first.operation_id))
    assert resumed.status == "PREPARED", resumed.error
    assert resumed.operation_id == first.operation_id
    assert len(fake.calls) == 1
    assert "角色声音签名" in fake.calls[0][0]


def test_current_selected_draft_reference_is_resolved_without_copying_media_ids(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    asset = document.assets[0]
    version = asset.versions[0]
    version.media_type, version.approval_status, version.freshness = AssetMediaType.IMAGE, ApprovalStatus.DRAFT, Freshness.CURRENT
    version.local_path, version.external_url = None, "https://example.test/current-character.png"
    asset.current_version_id = version.id
    document.shots[0].references = [ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
        alias="actor", purpose="Preserve the selected character identity")]
    for ref_plan in document.reference_plan:
        if ref_plan.shot_id in unit.shot_ids:
            ref_plan.bindings = []
    service.repository.save_project(document)
    media = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url=version.external_url), asset_version_id=version.id,
        subject_asset_id=asset.id, reference_alias="actor", binding_purpose="Preserve the selected character identity")
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2], route=VideoGenerationRoute.REFERENCE, media_inputs=[media])
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id))
    assert result.status == "PREPARED", result.error
    saved = service.repository.get_h3_group_operation(result.operation_id)
    assert saved is not None and saved.resolved_route == VideoGenerationRoute.REFERENCE
    assert saved.resolved_media_inputs == [media]
    assert len(fake.calls) == 1
    assert service.repository.list_tasks(document.project.id) == []


@pytest.mark.parametrize("stale_source_exists", [True, False])
def test_prepare_tracks_canonical_local_files_before_cache_and_compilation(
    tmp_path: Path, stale_source_exists: bool,
) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path)
    video = FakeH3Client()
    service = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language, h3_client_factory=lambda _key: video)
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-token"))
    old_path = tmp_path / "previous-source.png"
    if stale_source_exists:
        Image.new("RGB", (64, 96), "gray").save(old_path)
    media: list[H3MediaInput] = []
    for index, asset in enumerate(reversed(document.assets[:2])):
        version = asset.versions[0]
        path = tmp_path / f"actual-{index}.png"
        Image.new("RGB", (96, 144), (40 + index * 80, 50, 100)).save(path)
        version.local_path, version.external_url = str(path), None
        version.media_type, version.approval_status, version.freshness = AssetMediaType.IMAGE, ApprovalStatus.DRAFT, Freshness.CURRENT
        asset.current_version_id = version.id
        item = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path=str(path)), asset_version_id=version.id,
            subject_asset_id=asset.id, reference_alias=f"actor-{index}")
        media.append(item)
        document.shots[0].references.append(ShotReferenceCue(subject_asset_id=asset.id,
            asset_version_id=version.id, alias=f"actor-{index}", purpose="Preserve this selected identity"))
    for reference_plan in document.reference_plan:
        if reference_plan.shot_id in unit.shot_ids:
            reference_plan.bindings = []
    service.repository.save_project(document)
    language.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    request = H3GroupOperationRequest(shot_id=unit.shot_ids[0], resolution="2K", aspect_ratio="9:16",
        route=VideoGenerationRoute.REFERENCE, enqueue=False,
        media_inputs=[item.model_copy(update={"source": H3MediaSource(local_path=str(old_path))}) for item in media])
    prepared = service.prepare_h3_group(document.project.id, request)
    assert prepared.status == "PREPARED", prepared.error
    operation = service.repository.get_h3_group_operation(prepared.operation_id)
    assert operation is not None and prepared.route_plan_id is not None
    assert operation.input_context["media_inputs"] == [item.model_dump(mode="json") for item in media]
    assert operation.input_context["media_file_states"] == [{
        "path": str(Path(item.source.local_path or "").resolve()),
        "size": Path(item.source.local_path or "").stat().st_size,
        "modified_ns": Path(item.source.local_path or "").stat().st_mtime_ns,
    } for item in media]
    assert operation.resolved_media_inputs == media
    assert prepared.input_strategy["resolution"] == "2K" and prepared.input_strategy["aspect_ratio"] == "9:16"
    read = service.tool_registry.invoke("route.get_h3_plan", {"project_id": document.project.id, "plan_id": prepared.route_plan_id})
    assert read.status == ToolCallStatus.SUCCEEDED and read.output is not None
    plan = service.get_h3_production_route_plan(document.project.id, prepared.route_plan_id).plan
    assert read.output == {"plan": plan.model_dump(mode="json")}
    assert plan.decisions[0].media_inputs == media and plan.decisions[0].prompt == language.output_text
    assert plan.decisions[0].resolution == "2K" and plan.decisions[0].aspect_ratio == "9:16"
    payload = json.loads(language.calls[0][1])
    assert payload["model_lock"]["resolution"] == "2K" and payload["aspect_ratio"] == "9:16"
    # A stale caller path is neither the actual compiler input nor a dependency.
    Image.new("RGB", (80, 100), "red").save(old_path)
    repeated = service.prepare_h3_group(document.project.id, request)
    assert repeated.operation_id == prepared.operation_id and len(language.calls) == 1
    actual = Path(media[0].source.local_path or "")
    previous = actual.stat()
    Image.new("RGB", (100, 150), "blue").save(actual)
    os.utime(actual, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    changed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert changed.status == "INPUTS_CHANGED" and len(language.calls) == 1
    fresh = service.prepare_h3_group(document.project.id, request)
    assert fresh.status == "PREPARED" and fresh.operation_id != prepared.operation_id
    assert len(language.calls) == 2
    actual.unlink()
    missing = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=fresh.operation_id))
    assert missing.status == "BLOCKED" and str(actual) in (missing.error or "")
    assert len(language.calls) == 2
    assert prepared.task_ids == fresh.task_ids == []
    assert service.repository.list_tasks(document.project.id) == []
    assert service.repository.list_provider_runs(document.project.id) == []
    assert video.submitted_requests == []


def test_official_compiler_pending_operation_resumes_after_restart_without_language_or_duplicate_submit(tmp_path: Path) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path)
    client = FakeIR(language.output_text)
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-video-token"))
    pending = prepare_h3_group(service, document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], compile_mode="H3_CONTEXT_IR", route=VideoGenerationRoute.DIRECT,
        media_inputs=[], enqueue=True), context_ir_client=client)
    assert pending.status == "WAITING_PROMPT", pending.error
    assert pending.phase == "PROMPT_COMPILATION"
    assert pending.compile_mode == "H3_CONTEXT_IR" and pending.enqueue_requested
    assert pending.provider_prompt_task_id == "ir-saved" and pending.provider_prompt_cache_id
    assert pending.next_poll_at is not None and pending.next_poll_at.endswith("+00:00")
    assert pending.error is None and pending.next_action == "RESUME_SAVED_OPERATION"
    assert pending.task_ids == [] and language.calls == []
    # A normal Tool resume before the saved deadline needs no credential or
    # remote query and gives the scheduler the exact same waiting receipt.
    tool = service.tool_registry.invoke("route.prepare_h3_group", {
        "project_id": document.project.id, "request": {"operation_id": pending.operation_id, "enqueue": True}})
    assert tool.status == ToolCallStatus.SUCCEEDED, tool.error
    assert tool.receipt is not None and tool.receipt.current_state["next_poll_at"] == pending.next_poll_at
    assert tool.receipt.current_state["operation_id"] == pending.operation_id
    assert len(client.submitted) == 1 and client.queries == []
    due(service.workspace_root / "h3-context-ir")
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language)
    resumed = prepare_h3_group(restarted, document.project.id, H3GroupOperationRequest(
        operation_id=pending.operation_id, enqueue=True), context_ir_client=client)
    assert resumed.status == "WAITING_MEDIA", resumed.error
    assert resumed.operation_id == pending.operation_id and resumed.next_poll_at is None
    assert len(resumed.task_ids) == 1 and language.calls == []
    again = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        operation_id=pending.operation_id, enqueue=True))
    assert again.task_ids == resumed.task_ids
    assert len(client.submitted) == len(client.queries) == 1
    assert language.calls == []


def test_official_unknown_prompt_submit_stays_saved_and_never_blindly_retries(tmp_path: Path) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path)
    client = FakeIR(language.output_text, unknown=True)
    uncertain = prepare_h3_group(service, document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], compile_mode="H3_CONTEXT_IR", route=VideoGenerationRoute.DIRECT,
        media_inputs=[]), context_ir_client=client)
    assert uncertain.status == "PROMPT_SUBMIT_UNKNOWN"
    assert uncertain.provider_prompt_cache_id and uncertain.provider_prompt_task_id is None
    assert uncertain.next_action == "RECONCILE_PROMPT_SUBMISSION" and not uncertain.retryable
    again = prepare_h3_group(service, document.project.id, H3GroupOperationRequest(
        operation_id=uncertain.operation_id, retry_failed_step=True), context_ir_client=client)
    assert again.status == "PROMPT_SUBMIT_UNKNOWN"
    assert again.provider_prompt_cache_id == uncertain.provider_prompt_cache_id
    assert len(client.submitted) == 1 and client.queries == [] and language.calls == []
    assert service.repository.list_h3_route_plans(document.project.id) == []


def test_split_successor_accepts_original_take_only_with_saved_repair_link(tmp_path: Path) -> None:
    service, language, document, generation, _unit, _request = _fixture(tmp_path)
    shot = document.shots[0]
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[shot.id], candidate_count=1))
    source = service.drain(document.project.id, 50).project.takes[0]
    def replacement(suffix: str, with_dialogue: bool) -> ReplacementShotDraft:
        return ReplacementShotDraft(id=f"{shot.id}-{suffix}", duration_seconds=shot.duration_seconds / 2,
            narrative_purpose=shot.narrative_purpose, visual=shot.visual, framing=shot.framing,
            camera=shot.camera, audio_intent=shot.audio_intent, dialogue_line_ids=shot.dialogue_line_ids if with_dialogue else [],
            continuity=shot.continuity, transition_out=shot.transition_out, local_rework_risk=shot.local_rework_risk)
    repaired = service.decide_director_repair(document.project.id, shot.id, DirectorRepairDecisionRequest(
        decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.SPLIT_SHOT,
        source_take_id=source.id, reason="Separate the original action and its reaction",
        split_shots=[replacement("action", True), replacement("reaction", False)]))
    board, spec = service.get_storyboard(document.project.id), service.get_production_spec(document.project.id)
    plan = service.compile_generation_plan(document.project.id, GenerationPlanCompileRequest(
        origin=ProductionDocumentOrigin.AGENT, base_version_id=generation.version.id,
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id))
    unit = plan.generation_plan.units[0]
    shots = [item for item in repaired.document.shots if item.id in unit.shot_ids]
    language.output_text = build_h3_generation_unit_prompt(repaired.document, unit, shots)
    without_repair = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=shots[0].id, route=VideoGenerationRoute.DIRECT, media_inputs=[], source_take_id=source.id))
    assert without_repair.status == "BLOCKED" and language.calls == []
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=shots[0].id, route=VideoGenerationRoute.DIRECT, media_inputs=[], source_take_id=source.id,
        source_repair_id=repaired.document.repair_history[-1].id))
    assert result.status == "PREPARED", result.error
    assert result.shot_ids == unit.shot_ids and result.source_take_id == source.id
    assert shot.id not in result.shot_ids
    assert len(language.calls) == 1 and result.task_ids == []


def test_current_cues_supersede_older_generation_and_editorial_versions_in_legacy_plan(tmp_path: Path) -> None:
    _service, _language, document, _generation, _unit, _request = _fixture(tmp_path)
    selected, editorial, legacy_only = document.assets[:3]
    selected_new = selected.versions[0].model_copy(update={"id": "selected-current", "approval_status": ApprovalStatus.DRAFT})
    editorial_new = editorial.versions[0].model_copy(update={"id": "editorial-current", "approval_status": ApprovalStatus.DRAFT})
    selected.versions.append(selected_new)
    editorial.versions.append(editorial_new)
    shot = document.shots[0]
    shot.references = [
        ShotReferenceCue(subject_asset_id=selected.id, asset_version_id=selected_new.id, alias="current", purpose="Current identity"),
        ShotReferenceCue(subject_asset_id=editorial.id, asset_version_id=editorial_new.id, alias="title", purpose="Accurate editorial title", usage="EDITORIAL"),
    ]
    shot.timeline_beats = []
    plan = next(plan for plan in document.reference_plan if plan.shot_id == shot.id)
    plan.bindings = [ReferenceBinding(kind=asset.kind, target_id=asset.versions[0].id, purpose="Legacy binding")
        for asset in (selected, editorial, legacy_only)]
    assert selected_generation_reference_ids(document, plan) == [selected_new.id, legacy_only.versions[0].id]
    for asset in (selected, editorial, legacy_only):
        asset.approved_version_id = asset.versions[0].id
        for version in asset.versions:
            version.media_type, version.freshness = AssetMediaType.IMAGE, Freshness.CURRENT
            version.local_path, version.external_url = None, f"https://example.test/{version.id}.png"
    assert [entry[0] for entry in semantic_reference_entries(document, plan, load_images=False)] == [selected_new.id, legacy_only.versions[0].id]
