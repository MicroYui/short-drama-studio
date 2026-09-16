"""Read current H3 inputs from saved content; never prepare, compile or mutate them."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import JsonValue

from short_drama.asset_reference_truth import resolve_current_asset_media
from short_drama.generation_plan_state import derive_current_generation_plan, validate_generation_units_current
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaSource, H3ProductionRoutePlan, H3RouteSourceCheck, H3ShotRouteAction
from short_drama.h3_route_planner import generation_unit_media_candidates, validate_route_plan_freshness
from short_drama.language_models import H3CurrentSourceDocument, H3GenerationUnitContextRequest, H3GenerationUnitContextResponse
from short_drama.models import AssetMediaType, AudioInspectionReport, ProjectDocument, Take, VideoGenerationRoute, VideoInspectionReport
from short_drama.production_contracts import GENERATION_PLAN_DOCUMENT_KIND, GenerationPlanContent
from short_drama.production_media_context import previous_adopted_media_boundary
from short_drama.providers.h3_language_prompt import build_generation_unit_language_prompt_input
from short_drama.providers.minimax_h3_compiler import validate_h3_audio_durations, validate_h3_probe
from short_drama.studio_editorial import project_editorial_from_document
from short_drama.studio_references import assert_generation_references
from short_drama.worker import utc_now

if TYPE_CHECKING:
    from short_drama.h3_group_operation import H3GroupOperation


class H3SourceStore(Protocol):
    def get_h3_group_operation(self, operation_id: str) -> H3GroupOperation | None: ...
    def list_takes(self, project_id: str) -> list[Take]: ...
    def list_video_inspections(self, project_id: str) -> list[VideoInspectionReport]: ...
    def list_audio_inspections(self, project_id: str) -> list[AudioInspectionReport]: ...


def resolve_h3_asset_inputs(document: ProjectDocument, media_inputs: list[H3MediaInput]) -> list[H3MediaInput]:
    expected_kinds = {AssetMediaType.IMAGE: H3MediaKind.IMAGE,
                      AssetMediaType.VIDEO: H3MediaKind.VIDEO, AssetMediaType.AUDIO: H3MediaKind.AUDIO}
    resolved: list[H3MediaInput] = []
    for media in media_inputs:
        if media.asset_version_id is None:
            if media.subject_asset_id is not None:
                raise ValueError("A subject-linked H3 media input requires an AssetVersion")
            resolved.append(media)
            continue
        _asset, version = resolve_current_asset_media(document, asset_version_id=media.asset_version_id,
            subject_asset_id=media.subject_asset_id, explicit_choice=True)
        if expected_kinds.get(version.media_type) != media.kind:
            raise ValueError(f"Approved asset version {version.id} is not compatible with {media.kind.value}")
        if version.local_path is not None:
            source = H3MediaSource(local_path=version.local_path)
        elif version.external_url is not None:
            source = H3MediaSource(external_url=version.external_url)
        else:
            raise ValueError(f"Approved asset version {version.id} has no usable media source")
        resolved.append(media.model_copy(update={"source": source}))
    validate_h3_audio_durations(document, resolved)
    return resolved


def generation_unit_context(document: ProjectDocument, request: H3GenerationUnitContextRequest, *,
                            store: H3SourceStore, require_current_plan: bool = False) -> H3GenerationUnitContextResponse:
    """The public current projection and strict source check share this exact read."""
    project_id = document.project.id
    requested_document = next((item for item in document.production_documents
                               if item.id == request.generation_plan_document_id), None)
    if requested_document is None:
        raise KeyError(request.generation_plan_document_id)
    if requested_document.kind != GENERATION_PLAN_DOCUMENT_KIND:
        raise ValueError(f"Production document {requested_document.id} has kind {requested_document.kind!r}; expected {GENERATION_PLAN_DOCUMENT_KIND!r}")
    if requested_document.id != document.generation_plan_document_id:
        raise ValueError("Requested GenerationPlan is not this project's plan")
    requested_version = next((item for item in requested_document.versions if item.id == request.generation_plan_version_id), None)
    if requested_version is None:
        raise KeyError(request.generation_plan_version_id)
    try:
        requested_plan = GenerationPlanContent.model_validate(requested_version.payload)
    except ValueError as exc:
        raise ValueError(f"GenerationPlan version {requested_version.id} is structurally invalid: {exc}") from exc
    if requested_plan.project_id != project_id:
        raise ValueError("Requested GenerationPlan belongs to another project")
    requested_unit = next((unit for unit in requested_plan.units if unit.id == request.generation_unit_id), None)
    if requested_unit is None:
        raise KeyError(request.generation_unit_id)
    current_plan, board, spec = derive_current_generation_plan(document)
    unit = next((unit for unit in current_plan.units if unit.id == request.generation_unit_id), None)
    if unit is None:
        raise ValueError(f"Generation unit {request.generation_unit_id} no longer exists in the current Storyboard")
    changes: list[str] = []
    try:
        validate_generation_units_current(document, requested_plan, [unit.id])
    except ValueError as exc:
        if require_current_plan:
            raise
        changes.append(str(exc))
    for name in ("script_version_id", "bible_version_id", "storyboard_document_id", "storyboard_version_id",
                 "production_spec_document_id", "production_spec_version_id"):
        if getattr(requested_plan, name) != getattr(current_plan, name):
            changes.append(f"/{name}: {getattr(requested_plan, name)} -> {getattr(current_plan, name)}")
    for name in type(unit).model_fields:
        if getattr(requested_unit, name) != getattr(unit, name):
            changes.append(f"/generation_unit/{name}")
    if require_current_plan:
        unit = requested_unit
    by_shot = {shot.id: shot for shot in document.shots}
    shots = [by_shot[shot_id] for shot_id in unit.shot_ids]
    current_group = next(group for group in board.clip_groups if group.id == unit.clip_group_id)
    takes = store.list_takes(project_id)
    saved = document.editorial_timeline or {}
    editorial = (cast(dict[str, JsonValue], saved["project"]) if isinstance(saved.get("project"), dict)
                 else project_editorial_from_document(document, takes))
    boundary = previous_adopted_media_boundary(document, target_shot_id=shots[0].id,
        editorial_project=editorial, takes=takes, inspections=store.list_video_inspections(project_id),
        audio_inspections=store.list_audio_inspections(project_id))
    _instructions, source_text = build_generation_unit_language_prompt_input(document, unit, shots,
        route=VideoGenerationRoute.DIRECT, duration_seconds=math.ceil(unit.requested_duration_seconds),
        production_spec=spec, media_inputs=[], actual_previous_media_boundary=boundary)
    source: dict[str, JsonValue] = json.loads(source_text)
    for name in ("task", "operation_scope", "route", "required_prompt_schema", "route_media_bindings", "production_rules"):
        source.pop(name, None)
    candidates, warnings = generation_unit_media_candidates(document, shots)

    def identity(document_id: str, version_id: str) -> H3CurrentSourceDocument:
        item = next(item for item in document.production_documents if item.id == document_id)
        version = next(version for version in item.versions if version.id == version_id)
        return H3CurrentSourceDocument(document_id=item.id, version_id=version.id, version=version.version)

    return H3GenerationUnitContextResponse(project_id=project_id,
        generation_plan_document_id=requested_document.id, generation_plan_version_id=requested_version.id,
        generation_plan_version=requested_version.version, generation_unit_id=unit.id,
        source_status="CURRENT_PROJECTION" if changes else "CURRENT_PLAN", source_changes=changes,
        current_clip_group=current_group,
        current_storyboard=identity(current_plan.storyboard_document_id, current_plan.storyboard_version_id),
        current_production_spec=identity(current_plan.production_spec_document_id, current_plan.production_spec_version_id),
        source_context=source, media_candidates=candidates, warnings=warnings)


def operation_source_reason(document: ProjectDocument, record: H3GroupOperation, *,
                            store: H3SourceStore, media_inputs: list[H3MediaInput]) -> str | None:
    from short_drama.h3_group_operation import _media_file_states, _source_identity  # pyright: ignore[reportPrivateUsage]

    state = record.state
    if not state.generation_plan_document_id or not state.generation_plan_version_id or not state.generation_unit_id:
        return "Saved H3 operation is missing its generation-unit source"
    try:
        context = generation_unit_context(document, H3GenerationUnitContextRequest(
            generation_plan_document_id=state.generation_plan_document_id,
            generation_plan_version_id=state.generation_plan_version_id,
            generation_unit_id=state.generation_unit_id), store=store, require_current_plan=True)
        media = resolve_h3_asset_inputs(document, media_inputs)
        current: dict[str, JsonValue] = {**record.input_context, "source_context": context.source_context,
            "media_inputs": [item.model_dump(mode="json") for item in media],
            "media_file_states": cast(JsonValue, _media_file_states(media))}
        if _source_identity(record.input_context, document=document) != _source_identity(current, document=document):
            return "Current group/media differs from the saved H3 operation source; prepare the current group before submitting"
    except (ValueError, OSError, KeyError) as exc:
        return str(exc)
    return None


def route_source_checks(document: ProjectDocument, plan: H3ProductionRoutePlan, *,
                        store: H3SourceStore) -> list[H3RouteSourceCheck]:
    checks: list[H3RouteSourceCheck] = []
    for decision in plan.decisions:
        status: Literal["CURRENT", "STALE", "UNKNOWN"] = "CURRENT"
        reason: str | None = None
        try:
            scoped = plan.model_copy(update={"decisions": [decision]})
            validate_route_plan_freshness(scoped, document)
            media = resolve_h3_asset_inputs(document, decision.media_inputs)
            by_shot = {shot.id: shot for shot in document.shots}
            for shot_id in decision.shot_ids or [decision.shot_id]:
                assert_generation_references(document, by_shot[shot_id], media, require_all=False)
            if decision.action == H3ShotRouteAction.GENERATE:
                validate_h3_probe(route=decision.route, prompt=decision.prompt,
                    duration_seconds=decision.duration_seconds, media_inputs=media)
            if plan.source_operation_id:
                operation = store.get_h3_group_operation(plan.source_operation_id)
                if operation is None or operation.project_id != plan.project_id:
                    reason = "The H3 route's saved source operation is missing"
                else:
                    reason = operation_source_reason(document, operation, store=store, media_inputs=media)
                if reason is not None:
                    status = "STALE"
        except (ValueError, OSError, KeyError) as exc:
            status, reason = "STALE", str(exc)
        checks.append(H3RouteSourceCheck(route_plan_id=plan.id, state_revision=plan.state_revision,
            generation_unit_id=decision.generation_unit_id, shot_id=decision.shot_id,
            checked_at=utc_now(), status=status,
            scope="SAVED_OPERATION_SOURCE" if plan.source_operation_id else "CURRENT_INPUTS_ONLY",
            reason=reason if reason is not None else (None if plan.source_operation_id else
                "Current structured inputs checked; this legacy route has no saved operation source snapshot.")))
    return checks


def source_check_summary(checks: list[H3RouteSourceCheck]) -> dict[str, JsonValue]:
    status = "STALE" if any(check.status == "STALE" for check in checks) else (
        "CURRENT" if checks and all(check.status == "CURRENT" for check in checks) else "UNKNOWN")
    return {"source_status": status, "source_checks": [check.model_dump(mode="json") for check in checks]}
