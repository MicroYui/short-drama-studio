"""Single saved-shot picture preparation through the existing H3 operation/tasks."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.h3_models import H3InspectionPolicy, H3ProductionRouteExecuteRequest, H3ProductionRoutePlan, H3RoutePlanStatus, H3ShotRouteAction, H3ShotRoutePlan, H3ShotRouteReadiness
from short_drama.h3_picture_source import assert_picture_binding_current, picture_only_binding
from short_drama.h3_route_planner import generation_unit_media_candidates
from short_drama.h3_source_checks import H3SourceStore, resolve_h3_asset_inputs
from short_drama.language_models import LanguagePromptCompileRequest
from short_drama.models import H3PictureOnlyBinding, ProjectDocument, VideoInspectionScope
from short_drama.providers.h3_output_format import resolve_h3_output_format
from short_drama.worker import utc_now

if TYPE_CHECKING:
    from short_drama.h3_group_operation import H3GroupOperation, H3GroupOperationRequest, H3GroupOperationResponse
    from short_drama.service import ShortDramaService
    from short_drama.h3_models import H3MediaInput


def picture_source(document: ProjectDocument, binding: H3PictureOnlyBinding, media: list[H3MediaInput], *, store: H3SourceStore) -> dict[str, JsonValue]:
    from short_drama.h3_group_operation import _media_file_states  # pyright: ignore[reportPrivateUsage]
    from short_drama.production_media_context import previous_adopted_media_boundary
    from short_drama.studio_editorial import project_editorial_from_document
    shot = next(item for item in document.shots if item.id == binding.shot_id)
    takes = store.list_takes(document.project.id)
    saved = document.editorial_timeline or {}
    editorial = (cast(dict[str, JsonValue], saved["project"]) if isinstance(saved.get("project"), dict)
                 else project_editorial_from_document(document, takes))
    boundary = previous_adopted_media_boundary(document, target_shot_id=shot.id,
        editorial_project=editorial, takes=takes,
        inspections=store.list_video_inspections(document.project.id), audio_inspections=store.list_audio_inspections(document.project.id))
    if boundary is not None:
        for key in ("timeline_start_seconds", "timeline_end_seconds", "extract_frame_request"):
            boundary.pop(key, None)
    return {"picture_only_binding": binding.model_dump(mode="json"), "shot": shot.model_dump(mode="json"),
        "script_version_id": document.project.current_script_version_id, "bible_version_id": document.project.current_bible_version_id,
        "voice_reference_revision": document.project.voice_reference_revision,
        "actual_previous_media_boundary": cast(JsonValue, boundary),
        "media_inputs": [item.model_dump(mode="json") for item in media],
        "media_file_states": cast(JsonValue, _media_file_states(media))}


def picture_operation_reason(document: ProjectDocument, record: H3GroupOperation, *, store: H3SourceStore,
                             media_inputs: list[H3MediaInput]) -> str | None:
    try:
        binding = H3PictureOnlyBinding.model_validate(record.input_context.get("picture_only_binding"))
        assert_picture_binding_current(document, binding, store=store)
        current = picture_source(document, binding, resolve_h3_asset_inputs(document, media_inputs), store=store)
        if current != record.input_context:
            return "PICTURE_ONLY shot or reference source changed; prepare the current saved picture target"
    except (ValueError, OSError, KeyError, StopIteration) as error:
        return str(error)
    return None


def prepare_picture_only(service: ShortDramaService, project_id: str, request: H3GroupOperationRequest, *,
                         saved: H3GroupOperation | None) -> H3GroupOperationResponse:
    from short_drama.h3_group_operation import (  # pyright: ignore[reportPrivateUsage]
        _group_lock, _identity, _method_ids, _new_operation, _persist, _refresh_tasks, _route_for, _step, _storyboard_for_preparation,  # pyright: ignore[reportPrivateUsage]
    )
    from short_drama.service import ArtifactNotFoundError, ProviderOperationError, WorkflowConflictError
    intent = saved.request if saved else request
    assert intent.picture_only is not None and intent.shot_id is not None
    board = _storyboard_for_preparation(service, project_id, intent)
    if board.document.kind != "storyboard":
        raise WorkflowConflictError("PICTURE_ONLY requires a current formal storyboard and its saved editorial occurrence; draft full-group generation is available through ordinary route.prepare_h3_group")
    group = next((group for group in board.clip_groups if intent.shot_id in group.shot_ids), None)
    if group is None:
        raise WorkflowConflictError("PICTURE_ONLY requires a current formal storyboard shot")
    with _group_lock(service, project_id, intent.picture_only.clip_id):
        if saved is None:
            intent = intent.model_copy(update={"method_skill_ids": _method_ids(service, project_id, intent)})
            saved = next((item for item in reversed(service.repository.list_h3_group_operations(project_id))
                if item.request.shot_id == intent.shot_id and _identity(item.request) == _identity(intent)), None)
        assert intent.picture_only is not None and intent.shot_id is not None
        record = saved or _new_operation(service, project_id, intent, group.id)
        record.state.reused = saved is not None
        record.state.enqueue_requested = record.state.enqueue_requested or request.enqueue
        try:
            document = service.get_project(project_id).document
            if record.input_context:
                reason = picture_operation_reason(document, record, store=service.repository, media_inputs=record.resolved_media_inputs)
                if reason:
                    record.state.status, record.state.next_action, record.state.error = "INPUTS_CHANGED", "PREPARE_CURRENT_SHOT_OR_GROUP", reason
                    _persist(service, record)
                    return record.public_response()
                binding = H3PictureOnlyBinding.model_validate(record.input_context["picture_only_binding"])
            else:
                binding = picture_only_binding(document, intent.shot_id, intent.picture_only, store=service.repository)
            shot = next(item for item in document.shots if item.id == intent.shot_id)
            source_clip = binding.picture["clip"]
            assert isinstance(source_clip, dict)
            if intent.source_take_id is not None and source_clip.get("sourceTakeId") != intent.source_take_id:
                raise ValueError("PICTURE_ONLY source_take_id must match the actual saved target source")
            if intent.source_repair_id is not None:
                repair = next((item for item in document.repair_history if item.id == intent.source_repair_id), None)
                if repair is None or shot.id not in repair.affected_shot_ids or repair.source_take_id != source_clip.get("sourceTakeId"):
                    raise ValueError("PICTURE_ONLY source_repair_id must match a saved repair of this target and its actual source Take")
            candidates, warnings = generation_unit_media_candidates(document, [shot])
            media = list(intent.media_inputs) if intent.media_inputs is not None else [candidate.media_input for candidate in candidates]
            media = resolve_h3_asset_inputs(document, media)
            from short_drama.studio_references import assert_generation_references
            assert_generation_references(document, shot, media, require_all=False)
            route = intent.route or _route_for(media)
            resolution, ratio = resolve_h3_output_format(route=route, resolution=intent.resolution or "768P",
                aspect_ratio=intent.aspect_ratio, default_aspect_ratio=document.project.aspect_ratio)
            clip = binding.picture["clip"]
            assert isinstance(clip, dict)
            duration = max(4, math.ceil(float(cast(float, clip["duration"])) * float(cast(float, clip["speed"]))))
            if duration > 15:
                raise ValueError("PICTURE_ONLY target exceeds H3's 15-second request limit; select a shorter saved occurrence")
            if not record.input_context:
                record.input_context = picture_source(document, binding, media, store=service.repository)
            record.resolved_route, record.resolved_media_inputs = route, media
            record.state.shot_ids = [shot.id]
            record.state.warnings = [*warnings, "Picture-only candidate: generated sound is not used. This binding is not a hearing or lip-sync approval."]
            record.state.input_strategy = {"mode": "PICTURE_ONLY", "audio_policy": "KEEP_CURRENT_AUDIO",
                "target": intent.picture_only.model_dump(mode="json"), "generated_seconds": duration,
                "audio_clip_ids": [row["clip"]["id"] for row in binding.audio_sources if isinstance(row["clip"], dict)],
                "adoption": "video.select_take with exact clip_ids/source interval/current revision and audio_policy=KEEP_CURRENT_AUDIO"}
            _step(service, record, "CURRENT_PICTURE_AND_AUDIO_RESOLVED")
            if record.state.error and record.state.phase == "PROMPT_COMPILATION" and not request.retry_failed_step:
                return record.public_response()
            record.state.error, record.state.retryable = None, False
            if record.compiled_prompt is None:
                record.state.phase = "PROMPT_COMPILATION"
                _persist(service, record)
                current_revision = int(cast(int, (document.editorial_timeline or {}).get("revision", 0)))
                compiled = service.compile_h3_prompt_with_language_model(project_id, LanguagePromptCompileRequest(
                    picture_only=intent.picture_only.model_copy(update={"expected_editorial_revision": current_revision}),
                    shot_id=shot.id, resolution=resolution, aspect_ratio=ratio, route=route, duration_seconds=duration,
                    media_inputs=media, method_skill_ids=intent.method_skill_ids or [], revision_instructions=intent.revision_instructions))
                record.compiled_prompt = compiled.compiled_prompt
                _step(service, record, "PROMPT_COMPILED")
            record.state.phase = "ROUTE_PLANNING"
            document = service.get_project(project_id).document
            reason = picture_operation_reason(document, record, store=service.repository, media_inputs=media)
            if reason:
                record.state.status, record.state.next_action, record.state.error = "INPUTS_CHANGED", "PREPARE_CURRENT_SHOT_OR_GROUP", reason
                _persist(service, record)
                return record.public_response()
            plan = service.repository.get_h3_route_plan(record.state.route_plan_id) if record.state.route_plan_id else None
            if plan is None:
                now = utc_now()
                decision = H3ShotRoutePlan(picture_only=binding, shot_id=shot.id, shot_ids=[shot.id], shot_sequence=shot.sequence,
                    input_script_version_id=document.project.current_script_version_id, input_bible_version_id=document.project.current_bible_version_id,
                    input_shot_revision=shot.revision, input_shot_revisions={shot.id: shot.revision}, action=H3ShotRouteAction.GENERATE,
                    resolution=resolution, aspect_ratio=ratio, route=route, duration_seconds=duration, prompt=record.compiled_prompt,
                    media_inputs=media, rationale="Retake only this saved picture; keep its existing local soundtrack on adoption.",
                    candidate_count=intent.candidate_count, inspection=H3InspectionPolicy(scopes=[VideoInspectionScope.INTRA_SHOT], max_frames=8), readiness=H3ShotRouteReadiness.READY)
                # The current shot and source binding are pinned directly. No fake
                # global generation-plan version or rewritten storyboard is saved.
                plan = H3ProductionRoutePlan(id=f"h3-route-plan-{uuid4().hex}", project_id=project_id,
                    version=max((p.version for p in service.repository.list_h3_route_plans(project_id)), default=0) + 1,
                    status=H3RoutePlanStatus.READY, planner="picture-only-current-edit", resolution=resolution,
                    input_script_version_id=document.project.current_script_version_id, input_bible_version_id=document.project.current_bible_version_id,
                    input_voice_reference_revision=document.project.voice_reference_revision, decisions=[decision],
                    estimated_new_h3_seconds=duration * intent.candidate_count, source_operation_id=record.id, created_at=now, updated_at=now)
                with service._lock:  # pyright: ignore[reportPrivateUsage]
                    current = service.get_project(project_id).document
                    reason = picture_operation_reason(current, record, store=service.repository, media_inputs=media)
                    if reason:
                        raise ValueError(reason)
                    plan.version = max((p.version for p in service.repository.list_h3_route_plans(project_id)), default=0) + 1
                    _refresh_tasks(service, record, plan)
                    with service.repository.atomic():
                        service.repository.save_h3_route_plan(plan)
                        service.repository.save_h3_group_operation(record)
                _step(service, record, "ROUTE_SAVED")
            if request.enqueue:
                record.state.phase = "TASK_ENQUEUE"
                plan = service.execute_h3_production_route_plan(project_id, plan.id, H3ProductionRouteExecuteRequest(
                    expected_state_revision=plan.state_revision, enqueue_new_generations=True, reuse_passed_takes=False,
                    inspect_reused_takes=False, semantic_review=False, rebuild_manifest=False, render_export=False)).plan
                _step(service, record, "TASKS_ENQUEUED")
            _refresh_tasks(service, record, plan)
        except (ValueError, OSError, ArtifactNotFoundError, WorkflowConflictError, ProviderOperationError) as error:
            record.state.status, record.state.error = "BLOCKED", str(error)
            record.state.retryable = isinstance(error, (OSError, ProviderOperationError))
            record.state.next_action = "RETRY_FAILED_STEP" if record.state.retryable else "CORRECT_CURRENT_INPUTS_OR_CONFIGURATION"
        _persist(service, record)
        return record.public_response()
