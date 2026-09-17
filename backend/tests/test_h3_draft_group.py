"""Saved partial groups use the same H3 path before and after finalization."""
from pathlib import Path

from short_drama.generation_plan_state import derive_current_generation_plan
from short_drama.generation_storyboard import generation_document
from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import VideoGenerationRoute
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from tests.test_draft_append_local_bindings import _pending_append, _save_bindings
from tests.test_h3_generation_unit_language import FakePromptCompiler


def test_complete_saved_draft_group_prepares_without_finalizing(tmp_path: Path) -> None:
    service, pid, _pending, aid, vid = _pending_append(tmp_path)
    _save_bindings(service, pid, aid, vid)
    fake = FakePromptCompiler()
    service.language_provider_factory = lambda _key, _url: fake
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    raw = service.repository.get_project(pid)
    assert raw is not None and raw.storyboard_document_id is None and raw.shots == []
    document = generation_document(raw)
    plan, source, _spec = derive_current_generation_plan(raw)
    unit = plan.units[0]
    fake.output_text = build_h3_generation_unit_prompt(document, unit, [document.shots[0]])
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(clip_group_id=unit.clip_group_id,
        route=VideoGenerationRoute.DIRECT, media_inputs=[]))
    assert result.status == "PREPARED", result.error
    assert len(fake.calls) == 1
    saved = service.repository.get_project(pid)
    assert saved is not None and saved.storyboard_document_id is None and saved.shots == []
    assert saved.reference_plan == []
    generated = service.get_generation_plan(pid)
    assert generated.generation_plan.storyboard_document_id == source.document.id
    assert generated.generation_plan.storyboard_version_id == source.version.id


def _prepared(tmp_path: Path, *, enqueue: bool = False):
    from short_drama.h3_models import H3ProviderConfigUpdate
    from short_drama.service import ShortDramaService
    from tests.test_short_drama_h3 import FakeH3Client
    original, pid, pending, aid, vid = _pending_append(tmp_path)
    _save_bindings(original, pid, aid, vid)
    compiler, provider = FakePromptCompiler(), FakeH3Client()
    service = ShortDramaService(database_path=original.repository.database_path, workspace_root=original.workspace_root,
        language_provider_factory=lambda _key, _url: compiler, h3_client_factory=lambda _key: provider)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-key"))
    document = generation_document(service.get_project(pid).document)
    plan, _source, _spec = derive_current_generation_plan(document)
    unit = plan.units[0]
    compiler.output_text = build_h3_generation_unit_prompt(document, unit, [document.shots[0]])
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(clip_group_id=unit.clip_group_id,
        route=VideoGenerationRoute.DIRECT, media_inputs=[], enqueue=enqueue))
    assert result.status == ("WAITING_MEDIA" if enqueue else "PREPARED"), result.error
    return service, pid, pending, compiler, provider, result


def _finalize(service, pid, pending):
    from short_drama.models import ProductionDocumentOrigin
    from short_drama.production_contracts import StoryboardDraftFinalizeRequest
    service.append_storyboard_group(pid, pending)
    current = service.get_production_document(pid, pending.draft_document_id)
    from short_drama.production_contracts import StoryboardDraftContent
    draft = StoryboardDraftContent.model_validate(current.version.payload)
    covered = {line for group in draft.committed_groups for line in group.source_range.script_line_ids}
    for line in draft.script_line_ids:
        if line in covered:
            continue
        extra = pending.model_copy(deep=True)
        extra.base_version_id = current.version.id
        extra.group.work_unit_id = extra.work_unit.id = "remaining-" + line
        extra.group.scene_group_sequence += 1
        extra.group.shots[0].id = "remaining-" + line
        extra.group.shots[0].dialogue_line_ids = [line]
        extra.group.source_range.script_line_ids = [line]
        extra.work_unit.source_range.script_line_ids = [line]
        extra.group.clip_group.id = "clip-" + line
        extra.group.clip_group.shot_ids = [extra.group.shots[0].id]
        service.append_storyboard_group(pid, extra)
        current = service.get_production_document(pid, pending.draft_document_id)
    return service.finalize_storyboard(pid, StoryboardDraftFinalizeRequest(
        draft_document_id=current.document.id, draft_version_id=current.version.id,
        origin=ProductionDocumentOrigin.AGENT))


def test_draft_finalize_same_group_reuses_prompt_enqueues_and_retries(tmp_path: Path) -> None:
    from short_drama.models import TaskStatus
    service, pid, pending, compiler, provider, prepared = _prepared(tmp_path)
    formal = _finalize(service, pid, pending)
    assert formal.storyboard.shots[0].revision == 1
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert result.status == "WAITING_MEDIA", result.error
    assert len(compiler.calls) == 1
    task = service.repository.get_task(result.task_ids[0])
    assert task is not None and task.input_shot_revision == 2
    assert service.h3_worker.input_staleness_reason(task) is None
    assert service.advance_task(task.id).status == TaskStatus.SUBMITTING
    assert service.advance_task(task.id).status == TaskStatus.ACKNOWLEDGED
    assert len(provider.submitted_requests) == 1
    failed = service.repository.get_task(task.id)
    assert failed is not None
    failed.status = TaskStatus.FAILED
    service.repository.save_task(failed)
    retried = service.retry_task(task.id)
    assert retried.shot_windows == task.shot_windows
    assert retried.storyboard_document_id == pending.draft_document_id
    assert retried.input_shot_revision == 2
    assert service.h3_worker.input_staleness_reason(retried) is None


def test_draft_task_submits_then_other_group_append_does_not_change_source(tmp_path: Path) -> None:
    from short_drama.models import TaskStatus
    service, pid, pending, compiler, provider, prepared = _prepared(tmp_path, enqueue=True)
    task = service.repository.get_task(prepared.task_ids[0])
    assert task is not None and service.h3_worker.input_staleness_reason(task) is None
    service.append_storyboard_group(pid, pending)
    again = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert again.task_ids == [task.id] and len(compiler.calls) == 1
    assert service.advance_task(task.id).status == TaskStatus.SUBMITTING
    assert service.advance_task(task.id).status == TaskStatus.ACKNOWLEDGED
    assert len(provider.submitted_requests) == 1
    raw = service.repository.get_project(pid)
    assert raw is not None and raw.shots == [] and raw.reference_plan == []


def test_draft_generated_media_can_be_observed_adopted_and_finalized(tmp_path: Path) -> None:
    from short_drama.h3_models import H3RemoteStatus
    from short_drama.models import Freshness, TaskStatus, VideoInspectionRequest
    from tests.test_short_drama_h3 import _render_h3_output
    service, pid, pending, _compiler, provider, prepared = _prepared(tmp_path, enqueue=True)
    task = service.repository.get_task(prepared.task_ids[0])
    assert task is not None
    provider.output_source = tmp_path / "actual-output.mp4"
    _render_h3_output(provider.output_source, duration=int(task.requested_duration_seconds))
    assert service.advance_task(task.id).status == TaskStatus.SUBMITTING
    running = service.advance_task(task.id)
    assert running.provider_task_id is not None
    provider.set_status(running.provider_task_id, H3RemoteStatus.SUCCEEDED)
    assert service.advance_task(task.id).status == TaskStatus.SUCCEEDED
    take = service.repository.list_takes(pid)[0]
    assert take.freshness == Freshness.CURRENT
    observed = service.inspect_take_video(take.id, VideoInspectionRequest(semantic_review=False,
        include_previous_boundary=False, max_frames=6))
    assert observed.report.samples and all(sample.timestamp_is_source_pts for sample in observed.report.samples)
    selected = service.select_take(take.id)
    assert selected.progress.total_shots == 3
    assert service.repository.get_take(take.id).is_selected  # type: ignore[union-attr]
    from short_drama.studio_editorial import read_editorial, EditorProject, EditorAsset, EditorClip, EditorSubtitle
    from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
    from short_drama.studio_models import StudioTimelineSave
    from typing import cast
    from pydantic import JsonValue
    import wave
    local = read_shot_editorial(service, pid, take.shot_id)
    assert local.scope is not None and local.scope.duration_seconds > 0
    saved_local = save_shot_editorial(service, pid, take.shot_id,
        StudioTimelineSave(expected_revision=local.revision, anchor_clip_id=local.scope.anchor_clip_id,
            timeline={"project": local.project}))
    assert saved_local.project.document.editorial_timeline is not None
    full = read_editorial(service, pid)
    editor = EditorProject.model_validate(full.project)
    pcm = tmp_path / "retained-voice.wav"
    with wave.open(str(pcm), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 16000)
    from short_drama.studio_media import import_studio_media
    from short_drama.models import AssetKind
    imported = import_studio_media(service, pid, pcm, name="保留的独立声音", kind=AssetKind.REFERENCE, asset_id=None)
    pcm_path = imported.document.assets[-1].versions[-1].local_path
    assert pcm_path is not None
    pcm = Path(pcm_path)
    original_pcm = pcm.read_bytes()
    editor.assets.append(EditorAsset(id="independent-voice", path=str(pcm), type="audio", duration=1))
    editor.timelines[0].clips.append(EditorClip(id="retained-voice", assetId="independent-voice",
        type="audio", startTime=0, duration=1, trimStart=0, trackIndex=0, volume=.5))
    editor.timelines[0].subtitles.append(EditorSubtitle(id="retained-caption", text="保留人工台词",
        startTime=.1, endTime=.9, trackIndex=0))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=full.revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    from tests.test_studio_subtitle_alignment import EditorialTimingService
    from short_drama.tools.contracts import ToolCallStatus
    timing = EditorialTimingService()
    service.audio_semantic_service = timing
    alignment = service.tool_registry.invoke("audio.align_subtitles", {"project_id": pid,
        "request": {"shot_ids": [take.shot_id], "cue_ids": ["retained-caption"],
            "expected_editorial_revision": read_editorial(service, pid).revision}})
    assert alignment.status == ToolCallStatus.SUCCEEDED, alignment.error
    assert timing.calls
    raw_before = service.repository.get_project(pid)
    assert raw_before is not None and raw_before.shots == [] and raw_before.reference_plan == []
    saved_edit = raw_before.editorial_timeline
    before = service.repository.get_take(take.id)
    _finalize(service, pid, pending)
    after = service.repository.get_take(take.id)
    assert after == before
    assert pcm.read_bytes() == original_pcm
    document = service.repository.get_project(pid)
    assert document is not None and document.editorial_timeline == saved_edit
    shot = document.shots[0]
    service._validate_timeline_take_source(document, take, shot,  # pyright: ignore[reportPrivateUsage]
        storyboard_document_id=document.storyboard_document_id,
        storyboard_version_id=service.get_storyboard(pid).version.id)
    # Existing editorial writes still use current draft shot order without
    # copying that projection back into the project's raw shots field.
    assert document.shots[0].revision == 1 and take.shot_windows[0].shot_revision == 2


def test_missing_selected_draft_reference_blocks_before_language(tmp_path: Path) -> None:
    from short_drama.models import ShotReferenceCue, AssetMediaType
    from short_drama.studio_models import StudioShotSave
    service, pid, _pending, aid, _vid = _pending_append(tmp_path)
    raw = service.repository.get_project(pid)
    assert raw is not None
    missing = raw.assets[1]
    for version in missing.versions:
        version.media_type = AssetMediaType.TEXT_REFERENCE
        version.local_path = version.external_url = None
    service.repository.save_project(raw)
    shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
    shot.references = [ShotReferenceCue(subject_asset_id=missing.id, alias="待补参考", purpose="当前镜头身份")]
    service.studio.save_shot(pid, shot.id, StudioShotSave(expected_revision=shot.revision, shot=shot))
    assert service.studio.current_project(pid).document.shots[0].references[0].asset_version_id is None
    fake = FakePromptCompiler()
    service.language_provider_factory = lambda _key, _url: fake
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=shot.id))
    assert result.status == "BLOCKED" and "unbound generation references" in str(result.error)
    assert fake.calls == [] and service.repository.list_tasks(pid) == []


def test_other_group_missing_media_does_not_block_ready_group(tmp_path: Path) -> None:
    service, pid, _pending, compiler, _provider, prepared = _prepared(tmp_path)
    from short_drama.studio_models import StudioShotSave
    shot = service.studio.current_project(pid).document.shots[2].model_copy(deep=True)
    shot.references[0].asset_version_id = None
    service.studio.save_shot(pid, shot.id, StudioShotSave(expected_revision=shot.revision, shot=shot))
    again = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert again.status == "PREPARED", again.error
    assert len(compiler.calls) == 1


import pytest


@pytest.mark.parametrize("change", ["visual", "duration", "binding", "script", "bible", "source_range"])
def test_current_group_changes_prevent_original_task_submission(tmp_path: Path, change: str) -> None:
    from short_drama.studio_models import StudioShotSave
    from short_drama.studio_storyboard import current_draft
    service, pid, _pending, _compiler, provider, prepared = _prepared(tmp_path, enqueue=True)
    document = service.repository.get_project(pid)
    assert document is not None
    if change in {"visual", "duration", "binding"}:
        shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
        if change == "visual":
            shot.visual += "Changed the actual action."
        elif change == "duration":
            shot.duration_seconds += 1
        else:
            shot.references[0].purpose += " changed visible use"
        service.studio.save_shot(pid, shot.id, StudioShotSave(expected_revision=shot.revision, shot=shot))
    elif change == "script":
        document.project.current_script_version_id = "changed-script"
        service.repository.save_project(document)
    elif change == "bible":
        document.project.current_bible_version_id = "changed-bible"
        service.repository.save_project(document)
    else:
        draft = current_draft(document)
        assert draft is not None
        draft[2].committed_groups[0].source_range.description += " Changed source scope"
        next_version = draft[1].model_copy(update={"id": draft[1].id + "-changed", "version": draft[1].version + 1,
            "parent_version_id": draft[1].id, "payload": draft[2].model_dump(mode="json")}, deep=True)
        draft[0].versions.append(next_version)
        draft[0].current_version_id = next_version.id
        service.repository.save_project(document)
    task = service.repository.get_task(prepared.task_ids[0])
    assert task is not None
    reason = service.h3_worker.input_staleness_reason(task)
    assert reason is not None
    assert provider.submitted_requests == []


def test_draft_default_reference_files_are_real_selected_inputs(tmp_path: Path) -> None:
    from short_drama.h3_models import H3ProviderConfigUpdate
    from short_drama.h3_route_planner import generation_unit_media_candidates
    from short_drama.service import ShortDramaService
    from tests.test_short_drama_h3 import FakeH3Client
    original, pid, _pending, aid, vid = _pending_append(tmp_path)
    _save_bindings(original, pid, aid, vid)
    compiler, provider = FakePromptCompiler(), FakeH3Client()
    service = ShortDramaService(database_path=original.repository.database_path, workspace_root=original.workspace_root,
        language_provider_factory=lambda _key, _url: compiler, h3_client_factory=lambda _key: provider)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-key"))
    document = generation_document(service.get_project(pid).document)
    unit = derive_current_generation_plan(document)[0].units[0]
    candidates, _warnings = generation_unit_media_candidates(document, [document.shots[0]])
    media = [item.media_input for item in candidates if item.media_input.asset_version_id == vid]
    assert len(media) == 1
    compiler.output_text = build_h3_generation_unit_prompt(document, unit, [document.shots[0]],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    prepared = service.prepare_h3_group(pid, H3GroupOperationRequest(clip_group_id=unit.clip_group_id, enqueue=True))
    assert prepared.status == "WAITING_MEDIA", prepared.error
    task = service.repository.get_task(prepared.task_ids[0])
    assert task is not None
    run = service.repository.get_provider_run(task.id)
    assert run is not None and [item.asset_version_id for item in run.media_inputs] == [vid]
    assert service.h3_worker.input_staleness_reason(task) is None
    # In-place local-file change is still checked, even with identical IDs.
    assert media[0].source.local_path is not None
    Path(media[0].source.local_path).write_bytes(b"changed fixture")
    assert service.h3_worker.input_staleness_reason(task) is not None
    assert provider.submitted_requests == []


def test_changed_draft_group_stays_stale_after_finalization(tmp_path: Path) -> None:
    from short_drama.studio_models import StudioShotSave
    from short_drama.service import WorkflowConflictError
    from short_drama.models import TaskStatus
    service, pid, pending, _compiler, provider, prepared = _prepared(tmp_path, enqueue=True)
    original = service.repository.get_task(prepared.task_ids[0])
    assert original is not None
    shot = service.studio.current_project(pid).document.shots[0].model_copy(deep=True)
    shot.visual += "The revised action changes this exact group's source."
    service.studio.save_shot(pid, shot.id, StudioShotSave(expected_revision=shot.revision, shot=shot))
    _finalize(service, pid, pending)
    assert service.h3_worker.input_staleness_reason(original) is not None
    original.status = TaskStatus.FAILED
    service.repository.save_task(original)
    with pytest.raises(WorkflowConflictError, match="changed|outdated"):
        service.retry_task(original.id)
    assert provider.submitted_requests == []


def test_formal_source_wins_over_a_later_draft_with_same_ids(tmp_path: Path) -> None:
    from short_drama.generation_storyboard import generation_storyboard_source
    service, pid, pending, _compiler, _provider, prepared = _prepared(tmp_path)
    formal = _finalize(service, pid, pending)
    document = service.repository.get_project(pid)
    assert document is not None
    draft = next(item for item in document.production_documents if item.id == pending.draft_document_id)
    version = next(v for v in draft.versions if v.id == draft.current_version_id)
    newer = version.model_copy(update={"id": version.id + "-later", "version": version.version + 1}, deep=True)
    newer.payload["title"] = "A later draft must not replace the current formal source"
    draft.versions.append(newer)
    draft.current_version_id = newer.id
    service.repository.save_project(document)
    selected = generation_storyboard_source(document)
    assert selected.document.id == formal.document.id
    assert selected.version.id == formal.version.id
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert result.status == "PREPARED", result.error
