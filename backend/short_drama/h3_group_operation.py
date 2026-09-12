"""Recoverable preparation of one current H3 group using existing domain artifacts.

The operation is a progress cursor, not another project or storyboard. Creative
choices stay in the request/current bindings. Video execution stays in H3 tasks.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import math
from pathlib import Path
from threading import Lock, RLock
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from short_drama.generation_plan_state import validate_generation_units_current
from short_drama.h3_models import (
    H3MediaInput, H3MediaRole, H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlan, H3ProductionRoutePlanRequest, H3ShotRouteDirective,
)
from short_drama.h3_route_planner import generation_unit_voice_reference_inputs, validate_route_plan_freshness
from short_drama.language_models import H3GenerationUnitContextRequest, LanguagePromptCompileRequest
from short_drama.models import DomainModel, ProductionDocumentOrigin, TaskStatus, VideoGenerationRoute
from short_drama.production_contracts import GenerationPlanCompileRequest, GenerationPlanReadResponse, GenerationUnit
from short_drama.skill_ids import SkillIds, canonical_skill_ids
from short_drama.studio_trace import current_trace_context, record_trace
from short_drama.providers.h3_output_format import resolve_h3_output_format
from short_drama.video_inspection import selected_generation_reference_ids

if TYPE_CHECKING:
    from short_drama.providers.h3_context_ir import ContextIRClient
    from short_drama.service import ShortDramaService


class H3GroupOperationRequest(DomainModel):
    resolution: str | None = Field(default=None, description="AceData H3 output: 768P or 2K; omit to use this generation unit's choice or the 768P default.")
    aspect_ratio: str | None = Field(default=None, description="Fixed output shape for text/reference generation, or adaptive for frame routes. Omit to use the project's shape, with frame routes following their input frame.")
    operation_id: str | None = Field(default=None, min_length=1)
    shot_id: str | None = Field(default=None, min_length=1)
    clip_group_id: str | None = Field(default=None, min_length=1)
    compile_mode: Literal["LANGUAGE", "H3_CONTEXT_IR"] = Field(default="LANGUAGE",
        description="Default Astra compilation, or opt-in official asynchronous H3 Context IR with separate MiniMax credentials. Exactly one compiler is used.")
    route: VideoGenerationRoute | None = Field(default=None, description=(
        "Choose the shot's input strategy when needed. Omit to honor the selected media roles: "
        "reference media stay REFERENCE; a prepared complete first/last frame uses its hard frame route. "
        "Identity cards alone do not establish a shot's staging. A REFERENCE composition image is soft guidance."
    ))
    media_inputs: list[H3MediaInput] | None = Field(default=None, max_length=12, description=(
        "Explicit ordered media for this request, at most 9 reference images. Other prose references remain saved but are not sent. "
        "Omit/null to include all current generation cues and applicable voices; [] explicitly selects no media. "
        "Choose coverage by actual purpose, not by automatically excluding a reference role."
    ))
    method_skill_ids: SkillIds | None = None
    revision_instructions: str | None = Field(default=None, min_length=1, max_length=2000)
    source_take_id: str | None = Field(default=None, min_length=1,
        description="Existing candidate this new group candidate is intended to improve; does not imply video inpainting.")
    source_repair_id: str | None = Field(default=None, min_length=1)
    candidate_count: int = Field(default=1, ge=1)
    enqueue: bool = Field(default=False, description="Explicitly hand the prepared candidate to real H3 tasks. Preparation alone never queues video.")
    retry_failed_step: bool = Field(default=False, description="Retry a previously failed preparation step once, retaining completed prompt/route/task artifacts.")

    @model_validator(mode="after")
    def one_target(self) -> H3GroupOperationRequest:
        if sum(value is not None for value in (self.operation_id, self.shot_id, self.clip_group_id)) != 1:
            raise ValueError("Choose operation_id to resume, or one current shot_id/clip_group_id")
        if self.operation_id is not None and (
            any(value is not None for value in (self.resolution, self.aspect_ratio, self.route, self.media_inputs, self.method_skill_ids,
                self.revision_instructions, self.source_take_id, self.source_repair_id))
            or self.candidate_count != 1 or self.compile_mode != "LANGUAGE"
        ):
            raise ValueError("Resume the saved operation unchanged; use a shot/group target for a new creative choice")
        return self


class H3GroupOperationResponse(DomainModel):
    operation_id: str
    project_id: str
    clip_group_id: str | None = None
    shot_ids: list[str] = Field(default_factory=list[str])
    status: str
    phase: str
    compile_mode: Literal["LANGUAGE", "H3_CONTEXT_IR"] = "LANGUAGE"
    enqueue_requested: bool = False
    provider_prompt_cache_id: str | None = None
    provider_prompt_task_id: str | None = None
    next_poll_at: str | None = None
    completed_steps: list[str] = Field(default_factory=list[str])
    generation_plan_document_id: str | None = None
    generation_plan_version_id: str | None = None
    generation_plan_version: int | None = None
    generation_unit_id: str | None = None
    route_plan_id: str | None = None
    route_plan_state_revision: int | None = None
    task_ids: list[str] = Field(default_factory=list[str])
    task_statuses: dict[str, str] = Field(default_factory=dict[str, str])
    source_take_id: str | None = None
    source_repair_id: str | None = None
    next_action: str
    error: str | None = None
    retryable: bool = False
    warnings: list[str] = Field(default_factory=list[str])
    input_strategy: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    reused: bool = False


class H3GroupOperation(DomainModel):
    id: str
    project_id: str
    request: H3GroupOperationRequest
    state: H3GroupOperationResponse
    input_context: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    compiled_prompt: str | None = None
    resolved_route: VideoGenerationRoute | None = None
    resolved_media_inputs: list[H3MediaInput] = Field(default_factory=list[H3MediaInput])
    created_at: str
    updated_at: str

    def public_response(self) -> H3GroupOperationResponse:
        return self.state.model_copy(deep=True)


_LOCKS_GUARD = Lock()
_GROUP_LOCKS: dict[tuple[str, str, str], RLock] = {}


def _group_lock(service: ShortDramaService, project_id: str, group_id: str) -> RLock:
    # Only matching operations wait here. Project locks never span language IO.
    key = (str(service.repository.database_path.resolve()), project_id, group_id)
    with _LOCKS_GUARD:
        return _GROUP_LOCKS.setdefault(key, RLock())


def _persist(service: ShortDramaService, record: H3GroupOperation) -> None:
    record.updated_at = datetime.now(UTC).isoformat()
    service.repository.save_h3_group_operation(record)
    record_trace("h3.group_operation", record.state.model_dump(mode="json"))


def _step(service: ShortDramaService, record: H3GroupOperation, step: str) -> None:
    if step not in record.state.completed_steps:
        record.state.completed_steps.append(step)
    _persist(service, record)


def _identity(request: H3GroupOperationRequest) -> dict[str, JsonValue]:
    return request.model_dump(mode="json", exclude={"operation_id", "shot_id", "clip_group_id", "enqueue", "retry_failed_step"})


def _source_identity(source: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Ignore display numbering and order of the known per-shot visibility set only."""
    result = deepcopy(source)
    context = result.get("source_context")
    if isinstance(context, dict):
        shots = context.get("locked_shots")
        if isinstance(shots, list):
            for row in shots:
                if isinstance(row, dict) and isinstance(shot := row.get("locked_shot"), dict):
                    shot.pop("sequence", None)
                    visible = row.get("visible_asset_ids")
                    if isinstance(visible, list) and all(isinstance(value, str) for value in visible):
                        row["visible_asset_ids"] = cast(JsonValue, sorted(set(cast(list[str], visible))))
        previous = context.get("previous_clip_planned_terminal_state")
        if isinstance(previous, dict):
            previous.pop("sequence", None)
    return result


def operation_source_staleness_reason(
    service: ShortDramaService, record: H3GroupOperation, *, media_inputs: list[H3MediaInput],
) -> str | None:
    """Recheck the saved group's facts and actual files without compiling or mutating it."""
    state = record.state
    if not state.generation_plan_document_id or not state.generation_plan_version_id or not state.generation_unit_id:
        return "Saved H3 operation is missing its generation-unit source"
    try:
        context = service.get_h3_generation_unit_context(record.project_id, H3GenerationUnitContextRequest(
            generation_plan_document_id=state.generation_plan_document_id,
            generation_plan_version_id=state.generation_plan_version_id,
            generation_unit_id=state.generation_unit_id,
        ))
        media = service._resolve_approved_asset_inputs(  # pyright: ignore[reportPrivateUsage]
            service.get_project(record.project_id).document, media_inputs)
        current: dict[str, JsonValue] = {**record.input_context, "source_context": context.source_context,
            "media_inputs": [item.model_dump(mode="json") for item in media],
            "media_file_states": cast(JsonValue, _media_file_states(media))}
        if _source_identity(record.input_context) != _source_identity(current):
            return "Current group/media differs from the saved H3 operation source; prepare the current group before submitting"
    except (ValueError, OSError, KeyError) as exc:
        return str(exc)
    return None


def _new_operation(service: ShortDramaService, project_id: str, request: H3GroupOperationRequest, group_id: str) -> H3GroupOperation:
    now = datetime.now(UTC).isoformat()
    operation_id = f"h3-group-operation-{uuid4().hex}"
    record = H3GroupOperation(
        id=operation_id, project_id=project_id, request=request,
        state=H3GroupOperationResponse(operation_id=operation_id, project_id=project_id,
            clip_group_id=group_id, status="PREPARING", phase="CURRENT_INPUTS",
            compile_mode=request.compile_mode, enqueue_requested=request.enqueue,
            source_take_id=request.source_take_id, source_repair_id=request.source_repair_id,
            next_action="PREPARE_CURRENT_GROUP"), created_at=now, updated_at=now,
    )
    _persist(service, record)
    return record


def _current_plan(service: ShortDramaService, project_id: str, group_id: str, saved: H3GroupOperation) -> tuple[GenerationPlanReadResponse, GenerationUnit]:
    from short_drama.service import WorkflowConflictError

    document = service.get_project(project_id).document
    current = service.get_generation_plan(project_id, version_id=saved.state.generation_plan_version_id) if document.generation_plan_document_id else None
    if current is not None:
        unit = next((item for item in current.generation_plan.units if item.clip_group_id == group_id), None)
        if unit is not None:
            try:
                validate_generation_units_current(document, current.generation_plan, [unit.id])
            except ValueError:
                pass
            else:
                return current, unit
    # Compile only when this group changed or its execution plan does not exist.
    # Unrelated storyboard versions do not make the previous branch fail.
    current = service.get_generation_plan(project_id) if document.generation_plan_document_id else None
    board = service.get_storyboard(project_id)
    spec = service.get_production_spec(project_id)
    try:
        current = service.compile_generation_plan(project_id, GenerationPlanCompileRequest(
            storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
            production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
            base_version_id=current.version.id if current is not None else None,
            origin=ProductionDocumentOrigin.AGENT,
        ))
    except WorkflowConflictError:
        # Another group may have advanced the shared derived plan while this
        # operation was reading. Reuse it only if our selected unit is current.
        current = service.get_generation_plan(project_id)
        unit = next((item for item in current.generation_plan.units if item.clip_group_id == group_id), None)
        if unit is None:
            raise
        validate_generation_units_current(service.get_project(project_id).document, current.generation_plan, [unit.id])
        return current, unit
    unit = next((item for item in current.generation_plan.units if item.clip_group_id == group_id), None)
    if unit is None:
        raise ValueError(f"Current Storyboard no longer contains group {group_id}")
    return current, unit


def _route_for(media: list[H3MediaInput]) -> VideoGenerationRoute:
    roles = {item.role for item in media}
    if not roles:
        return VideoGenerationRoute.DIRECT
    if roles <= {H3MediaRole.REFERENCE_IMAGE, H3MediaRole.REFERENCE_VIDEO, H3MediaRole.REFERENCE_AUDIO}:
        return VideoGenerationRoute.REFERENCE
    if roles == {H3MediaRole.FIRST_FRAME, H3MediaRole.LAST_FRAME}:
        return VideoGenerationRoute.FRAME_FIRST_LAST
    if roles == {H3MediaRole.FIRST_FRAME}:
        return VideoGenerationRoute.FRAME_FIRST
    if roles == {H3MediaRole.LAST_FRAME}:
        return VideoGenerationRoute.FRAME_LAST
    raise ValueError("Current media mixes H3 modes; choose the route and ordered media_inputs explicitly")


def _method_ids(service: ShortDramaService, project_id: str, request: H3GroupOperationRequest) -> list[str]:
    if request.method_skill_ids is not None:
        return list(request.method_skill_ids)
    work_id = current_trace_context().get("work_order_id")
    work = service.agent_runtime.store.get_work_order(work_id) if isinstance(work_id, str) else None
    run = service.agent_runtime.store.get_run(work.parent_run_id) if work is not None else None
    ids = (work.context_manifest.selected_skill_ids
           if work is not None and work.context_manifest is not None and run is not None and run.project_id == project_id
           else service.get_project(project_id).document.project.preferred_method_skill_ids)
    return [value for value in canonical_skill_ids(ids) if value.startswith("method.")]


def _media_file_states(media: list[H3MediaInput]) -> list[dict[str, JsonValue]]:
    states: list[dict[str, JsonValue]] = []
    for item in media:
        if item.source.local_path:
            path = Path(item.source.local_path).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"Selected input media is missing: {path}")
            stat = path.stat()
            states.append({"path": str(path), "size": stat.st_size, "modified_ns": stat.st_mtime_ns})
        else:
            states.append({"external_url": item.source.external_url})
    return states


def _matching_route(service: ShortDramaService, record: H3GroupOperation) -> H3ProductionRoutePlan | None:
    document = service.get_project(record.project_id).document
    candidates = service.repository.list_h3_route_plans(record.project_id)
    if record.state.route_plan_id:
        candidates = [item for item in candidates if item.id == record.state.route_plan_id]
    for plan in reversed(candidates):
        if plan.source_operation_id not in {None, record.id}:
            continue
        if (record.request.source_take_id or record.request.source_repair_id) and plan.source_operation_id != record.id:
            continue
        if len(plan.decisions) != 1 or plan.input_generation_plan_version_id != record.state.generation_plan_version_id:
            continue
        decision = plan.decisions[0]
        if (decision.generation_unit_id != record.state.generation_unit_id
                or decision.prompt != record.compiled_prompt or decision.route != record.resolved_route
                or decision.media_inputs != record.resolved_media_inputs
                or decision.candidate_count != record.request.candidate_count):
            continue
        decision_ratio = resolve_h3_output_format(route=decision.route, resolution=decision.resolution,
            aspect_ratio=decision.aspect_ratio)[1]
        expected_ratio = record.input_context.get("aspect_ratio", None if decision.route in {
            VideoGenerationRoute.FRAME_FIRST, VideoGenerationRoute.FRAME_LAST, VideoGenerationRoute.FRAME_FIRST_LAST} else "9:16")
        if decision.resolution != record.input_context.get("resolution", "768P") or decision_ratio != expected_ratio:
            continue
        # A source Take explicitly asks for a new candidate; a previous route
        # already producing that Take is not its replacement.
        if record.request.source_take_id and record.request.source_take_id in {
            take.id for take in service.repository.list_takes(record.project_id)
            if take.task_id in decision.task_ids
        }:
            continue
        try:
            validate_route_plan_freshness(plan, document)
        except ValueError:
            continue
        return plan
    return None


def _refresh_tasks(service: ShortDramaService, record: H3GroupOperation, plan: H3ProductionRoutePlan) -> None:
    record.state.route_plan_id = plan.id
    record.state.route_plan_state_revision = plan.state_revision
    record.state.task_ids = [task_id for item in plan.decisions for task_id in item.task_ids]
    tasks = [service.repository.get_task(task_id) for task_id in record.state.task_ids]
    record.state.task_statuses = {task.id: task.status.value for task in tasks if task is not None}
    statuses = set(record.state.task_statuses.values())
    if record.state.error and not record.state.task_ids:
        return
    if not record.state.task_ids:
        record.state.status, record.state.phase, record.state.next_action = "PREPARED", "ROUTE_SAVED", "ENQUEUE_WHEN_REQUESTED"
    elif TaskStatus.SUBMIT_UNKNOWN.value in statuses:
        record.state.status, record.state.phase, record.state.next_action = "SUBMIT_UNKNOWN", "ENQUEUED", "RECONCILE_EXISTING_TASK"
    elif statuses == {TaskStatus.SUCCEEDED.value}:
        record.state.status, record.state.phase, record.state.next_action = "MEDIA_READY", "ENQUEUED", "INSPECT_OR_SELECT_CANDIDATES"
    elif statuses & {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value, TaskStatus.OUTPUT_MISSING.value}:
        record.state.status, record.state.phase, record.state.next_action = "MEDIA_NEEDS_ATTENTION", "ENQUEUED", "READ_TASK_FAILURE_OR_PREPARE_CHANGED_CANDIDATE"
    else:
        record.state.status, record.state.phase, record.state.next_action = "WAITING_MEDIA", "ENQUEUED", "WAIT_FOR_EXISTING_TASKS"


def read_h3_group_operation(service: ShortDramaService, project_id: str, operation_id: str) -> H3GroupOperationResponse:
    from short_drama.service import ArtifactNotFoundError

    record = service.repository.get_h3_group_operation(operation_id)
    if record is None or record.project_id != project_id:
        raise ArtifactNotFoundError(operation_id)
    if record.state.route_plan_id:
        plan = service.repository.get_h3_route_plan(record.state.route_plan_id)
        if plan is not None:
            _refresh_tasks(service, record, plan)
    return record.public_response()


def prepare_h3_group(service: ShortDramaService, project_id: str, request: H3GroupOperationRequest,
                     *, context_ir_client: ContextIRClient | None = None) -> H3GroupOperationResponse:
    from short_drama.providers.h3_context_ir import ContextIRSubmissionUnknown, H3ContextIRPending, compile_h3_context_ir
    from short_drama.service import ArtifactNotFoundError, ProviderOperationError, WorkflowConflictError

    saved = service.repository.get_h3_group_operation(request.operation_id) if request.operation_id else None
    if request.operation_id and (saved is None or saved.project_id != project_id):
        raise ArtifactNotFoundError(request.operation_id)
    intent = saved.request if saved is not None else request
    if saved is None:
        # Store inherited creative method choices once so a later resume does
        # not accidentally depend on whichever WorkOrder happens to be active.
        intent = intent.model_copy(update={"method_skill_ids": _method_ids(service, project_id, intent)})
    board = service.get_storyboard(project_id)
    group = next((item for item in board.storyboard.clip_groups
                  if item.id == intent.clip_group_id or intent.shot_id in item.shot_ids), None)
    if group is None:
        raise WorkflowConflictError("No current storyboard group matches this target; choose a current shot/group")
    with _group_lock(service, project_id, group.id):
        if saved is not None:
            saved = service.repository.get_h3_group_operation(saved.id)
        if saved is None:
            saved = next((item for item in reversed(service.repository.list_h3_group_operations(project_id))
                          if item.state.clip_group_id == group.id and _identity(item.request) == _identity(intent)), None)
        record = saved or _new_operation(service, project_id, intent, group.id)
        record.state.reused = saved is not None
        record.state.enqueue_requested = record.state.enqueue_requested or request.enqueue
        try:
            plan, unit = _current_plan(service, project_id, group.id, record)
            if not 4 <= math.ceil(unit.requested_duration_seconds) <= 15:
                raise ValueError(f"H3 group {group.id} requests {unit.requested_duration_seconds:g} seconds; "
                    "H3 generation supports 4–15 seconds per request. Adjust this group's duration or grouping "
                    "with shot.patch_storyboard; the saved storyboard is unchanged.")
            context = service.get_h3_generation_unit_context(project_id, H3GenerationUnitContextRequest(
                generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id,
                generation_unit_id=unit.id,
            ))
            document = service.get_project(project_id).document
            repair = next((item for item in document.repair_history if item.id == intent.source_repair_id), None)
            repair_covers_group = repair is not None and bool(set(repair.affected_shot_ids) & set(unit.shot_ids))
            if intent.source_take_id is not None:
                take = service.repository.get_take(intent.source_take_id)
                repair_links_source = repair_covers_group and repair is not None and repair.source_take_id == intent.source_take_id
                if take is None or take.project_id != project_id or not (set(take.covered_shot_ids()) & set(unit.shot_ids) or repair_links_source):
                    raise ValueError("source_take_id must refer to an existing candidate covering this current group")
            if intent.source_repair_id is not None and not repair_covers_group:
                raise ValueError("source_repair_id must refer to a saved repair affecting this group")
            if intent.media_inputs is None:
                selected_ids = list(dict.fromkeys(version_id
                    for ref_plan in document.reference_plan if ref_plan.shot_id in unit.shot_ids
                    for version_id in selected_generation_reference_ids(document, ref_plan)))
                media = [item.media_input for item in context.media_candidates if item.media_input.asset_version_id in selected_ids]
                missing = set(selected_ids) - {item.asset_version_id for item in media}
                if missing:
                    raise ValueError("Current selected references are unavailable: " + ", ".join(sorted(missing)))
                shots_by_id = {shot.id: shot for shot in document.shots}
                from short_drama.studio_references import assert_generation_references
                for shot_id in unit.shot_ids:
                    assert_generation_references(document, shots_by_id[shot_id], media)
                media.extend(generation_unit_voice_reference_inputs(document,
                    [shots_by_id[shot_id] for shot_id in unit.shot_ids], selected_media_inputs=media))
            else:
                media = list(intent.media_inputs)
            # AssetVersion is the canonical source used by compilation and the
            # saved route. Observe those files before deciding cache freshness,
            # not a caller's stale source path; the resolver preserves order.
            media = service._resolve_approved_asset_inputs(document, media)  # pyright: ignore[reportPrivateUsage]
            route = intent.route or _route_for(media)
            resolution, aspect_ratio = resolve_h3_output_format(route=route,
                resolution=intent.resolution or unit.resolution or "768P", aspect_ratio=intent.aspect_ratio,
                default_aspect_ratio=document.project.aspect_ratio)
            methods = _method_ids(service, project_id, intent)
            source: dict[str, JsonValue] = {
                "source_context": context.source_context, "route": route.value,
                "media_inputs": [item.model_dump(mode="json") for item in media],
                "media_file_states": cast(JsonValue, _media_file_states(media)), "method_skill_ids": list(methods),
                "revision_instructions": intent.revision_instructions,
                "compile_mode": intent.compile_mode,
                "resolution": resolution, "aspect_ratio": aspect_ratio,
            }
            previous_source = dict(record.input_context)
            if previous_source:
                previous_source.setdefault("resolution", "768P")
                previous_source.setdefault("aspect_ratio", None if route in {
                    VideoGenerationRoute.FRAME_FIRST, VideoGenerationRoute.FRAME_LAST, VideoGenerationRoute.FRAME_FIRST_LAST} else "9:16")
            if previous_source and _source_identity(previous_source) != _source_identity(source):
                if request.operation_id:
                    record.state.status, record.state.next_action = "INPUTS_CHANGED", "PREPARE_CURRENT_SHOT_OR_GROUP"
                    record.state.error = "Current group/media differs from this saved operation; its previous prompt/tasks remain retained."
                    _persist(service, record)
                    return record.public_response()
                record = _new_operation(service, project_id, intent, group.id)
            if record.state.error and record.state.phase == "PROMPT_COMPILATION" and not request.retry_failed_step:
                return record.public_response()
            record.input_context = source
            record.resolved_route, record.resolved_media_inputs = route, media
            record.state.input_strategy = {
                "route": route.value,
                "resolution": resolution, "aspect_ratio": aspect_ratio,
                "selected_by": "explicit_request" if intent.route is not None else "selected_media_roles",
                "media_source": "explicit_request" if intent.media_inputs is not None else "current_shot_references_and_speaking_character_voices",
                "references": [{"asset_version_id": item.asset_version_id, "role": item.role.value,
                    "image_reference_role": item.image_reference_role.value if item.image_reference_role else None,
                    "purpose": item.binding_purpose} for item in media],
                "guidance": (
                    "REFERENCE composition/layout pictures guide staging softly and can accompany identity references; "
                    "FRAME_FIRST uses a complete prepared starting image and cannot mix reference media. "
                    "The selected route is an input choice, not a visual-quality judgment. "
                    "Use route.get_generation_unit_context for alternatives and image tools or video.extract_frame "
                    "when the required visible starting state has not been prepared."
                ),
            }
            record.state.error, record.state.retryable = None, False
            record.state.shot_ids = list(unit.shot_ids)
            record.state.generation_plan_document_id = plan.document.id
            record.state.generation_plan_version_id = plan.version.id
            record.state.generation_plan_version = plan.version.version
            record.state.generation_unit_id = unit.id
            record.state.warnings = list(context.warnings)
            _step(service, record, "CURRENT_GROUP_INPUTS_RESOLVED")
            if record.compiled_prompt is None:
                cached = next((item for item in reversed(service.repository.list_h3_group_operations(project_id))
                               if item.id != record.id and _source_identity(item.input_context) == _source_identity(source) and item.compiled_prompt is not None), None)
                if cached is not None:
                    record.compiled_prompt = cached.compiled_prompt
                    record.resolved_media_inputs = list(cached.resolved_media_inputs)
                    _step(service, record, "PROMPT_REUSED")
            if record.compiled_prompt is None:
                record.state.phase = "PROMPT_COMPILATION"
                _persist(service, record)
                compiler_request = LanguagePromptCompileRequest(
                    resolution=resolution, aspect_ratio=aspect_ratio,
                    generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id,
                    generation_unit_id=unit.id, route=route, media_inputs=media,
                    method_skill_ids=methods, revision_instructions=intent.revision_instructions,
                )
                compiled = (
                    compile_h3_context_ir(service, project_id, compiler_request, client=context_ir_client,
                        retry_failed=request.retry_failed_step)
                    if intent.compile_mode == "H3_CONTEXT_IR"
                    else service.compile_h3_prompt_with_language_model(project_id, compiler_request)
                )
                record.compiled_prompt = compiled.compiled_prompt
                record.resolved_media_inputs = compiled.media_inputs
                record.state.next_poll_at = None
                _step(service, record, "PROMPT_COMPILED")
            record.state.phase = "ROUTE_PLANNING"
            # Compilation may run while the user edits or a selected file changes.
            # Check the same complete local source again before saving/queueing its result.
            source_reason = operation_source_staleness_reason(service, record, media_inputs=record.resolved_media_inputs)
            if source_reason is not None:
                record.state.status, record.state.next_action = "INPUTS_CHANGED", "PREPARE_CURRENT_SHOT_OR_GROUP"
                record.state.error = source_reason + "; the draft is retained but no new route/tasks were saved."
                _persist(service, record)
                return record.public_response()
            route_plan = _matching_route(service, record)
            if route_plan is None:
                # The compiler has made the one language call. Route creation is
                # now deterministic and consumes that exact authored result.
                route_plan = service.compile_h3_production_route_plan(project_id, H3ProductionRoutePlanRequest(
                        resolution=resolution, aspect_ratio=aspect_ratio,
                        source_operation_id=record.id,
                        generation_plan_document_id=plan.document.id, generation_plan_version_id=plan.version.id,
                        unit_ids=[unit.id], use_language_agent=False, consider_video_guides=False,
                        candidate_count=intent.candidate_count,
                        shot_directives=[H3ShotRouteDirective(generation_unit_id=unit.id,
                            candidate_id=f"{route.value.lower().replace('_', '-')}:{unit.id}",
                            prompt_override=record.compiled_prompt, media_inputs=record.resolved_media_inputs)],
                    )).plan
            _refresh_tasks(service, record, route_plan)
            _step(service, record, "ROUTE_SAVED")
            if request.enqueue:
                record.state.phase = "TASK_ENQUEUE"
                _persist(service, record)
                route_plan = service.execute_h3_production_route_plan(project_id, route_plan.id, H3ProductionRouteExecuteRequest(
                    expected_state_revision=route_plan.state_revision, enqueue_new_generations=True,
                    reuse_passed_takes=False, inspect_reused_takes=False, semantic_review=False,
                    rebuild_manifest=False, render_export=False,
                )).plan
                _step(service, record, "TASKS_ENQUEUED")
            _refresh_tasks(service, record, route_plan)
            _persist(service, record)
        except H3ContextIRPending as exc:
            record.state.status, record.state.phase = "WAITING_PROMPT", "PROMPT_COMPILATION"
            record.state.provider_prompt_cache_id, record.state.provider_prompt_task_id = exc.cache_id, exc.task_id
            record.state.next_poll_at = exc.next_poll_at
            record.state.error, record.state.retryable = None, False
            record.state.next_action = "RESUME_SAVED_OPERATION"
            _persist(service, record)
        except (ValueError, ProviderOperationError, WorkflowConflictError, ArtifactNotFoundError, OSError) as exc:
            record.state.status = "BLOCKED"
            record.state.error = str(exc)
            record.state.retryable = isinstance(exc, (ProviderOperationError, OSError))
            record.state.next_action = "RETRY_FAILED_STEP" if record.state.retryable else "CORRECT_CURRENT_INPUTS_OR_CONFIGURATION"
            if isinstance(exc.__cause__, ContextIRSubmissionUnknown):
                record.state.status, record.state.next_action = "PROMPT_SUBMIT_UNKNOWN", "RECONCILE_PROMPT_SUBMISSION"
                record.state.provider_prompt_cache_id = exc.__cause__.cache_id
                record.state.retryable = False
            _persist(service, record)
        return record.public_response()
