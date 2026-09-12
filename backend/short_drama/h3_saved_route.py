"""Explicit saved-prompt reuse, prepared in memory before the route is saved."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.h3_group_operation import H3GroupOperation, H3GroupOperationRequest, H3GroupOperationResponse
from short_drama.h3_group_operation import _media_file_states  # pyright: ignore[reportPrivateUsage]
from short_drama.h3_models import H3ProductionRoutePlan, H3ProductionRoutePlanRequest, H3ShotRouteAction, H3ShotRouteDirective
from short_drama.language_models import H3GenerationUnitContextRequest
from short_drama.models import ProjectDocument
from short_drama.worker import utc_now

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


@dataclass
class SavedRouteReuse:
    original: H3ProductionRoutePlan
    request: H3ProductionRoutePlanRequest
    operation: H3GroupOperation


def prepare_saved_route_reuse(
    service: ShortDramaService, document: ProjectDocument, request: H3ProductionRoutePlanRequest,
) -> SavedRouteReuse | None:
    directive = next((d for d in request.shot_directives if d.saved_route_source is not None), None)
    if directive is None or directive.saved_route_source is None:
        return None
    source = directive.saved_route_source
    original = service.repository.get_h3_route_plan(source.route_plan_id)
    if original is None or original.project_id != document.project.id:
        raise ValueError("Saved route source is missing or belongs to another project")
    if original.state_revision != source.state_revision:
        raise ValueError(f"Saved route source state revision changed: expected {source.state_revision}, current {original.state_revision}")
    choices = [d for d in original.decisions if d.generation_unit_id == source.generation_unit_id]
    if len(choices) != 1 or choices[0].action != H3ShotRouteAction.GENERATE:
        raise ValueError("Saved route source must identify one GENERATE decision")
    selected = choices[0]
    if request.resolution not in (None, selected.resolution) or request.aspect_ratio not in (None, selected.aspect_ratio):
        raise ValueError("Saved route reuse preserves its output format; use an authored candidate to change it")
    assert request.generation_plan_document_id and request.generation_plan_version_id
    context = service.get_h3_generation_unit_context(document.project.id, H3GenerationUnitContextRequest(
        generation_plan_document_id=request.generation_plan_document_id,
        generation_plan_version_id=request.generation_plan_version_id,
        generation_unit_id=source.generation_unit_id,
    ), require_current_plan=True)
    group = context.current_clip_group
    if group is None:
        raise ValueError("Saved route reuse requires the current formal clip group")
    media = service._resolve_approved_asset_inputs(document, selected.media_inputs)  # pyright: ignore[reportPrivateUsage]
    if media != selected.media_inputs:
        raise ValueError("Saved route selected media locators or bindings changed; inspect the current media before authoring a new request")
    states = cast(JsonValue, _media_file_states(media))
    if original.source_operation_id is not None:
        prior = service.repository.get_h3_group_operation(original.source_operation_id)
        if prior is None or prior.project_id != document.project.id:
            raise ValueError("Saved route's original media source is missing")
        if prior.input_context.get("media_file_states") != states:
            raise ValueError("Saved route's selected media files changed; its prompt cannot be reused unchanged")
    authored = H3ShotRouteDirective(
        generation_unit_id=source.generation_unit_id,
        candidate_id=f"{selected.route.value.lower().replace('_', '-')}:{source.generation_unit_id}",
        prompt_override=selected.prompt, media_inputs=media,
    )
    operation_id = f"h3-group-operation-{uuid4().hex}"
    now = utc_now()
    intent = H3GroupOperationRequest(clip_group_id=group.id,
        route=selected.route, media_inputs=media, resolution=selected.resolution,
        aspect_ratio=selected.aspect_ratio, method_skill_ids=[], candidate_count=request.candidate_count)
    operation = H3GroupOperation(
        id=operation_id, project_id=document.project.id, request=intent,
        state=H3GroupOperationResponse(operation_id=operation_id, project_id=document.project.id,
            clip_group_id=group.id, shot_ids=list(group.shot_ids),
            status="PREPARED", phase="ROUTE_SAVED", compile_mode="SAVED_ROUTE",
            generation_plan_document_id=context.generation_plan_document_id,
            generation_plan_version_id=context.generation_plan_version_id,
            generation_plan_version=context.generation_plan_version, generation_unit_id=source.generation_unit_id,
            completed_steps=["CURRENT_GROUP_INPUTS_RESOLVED", "SAVED_ROUTE_SELECTED", "PROMPT_REUSED", "ROUTE_SAVED"],
            next_action="ENQUEUE_WHEN_REQUESTED", input_strategy={"saved_route_source": source.model_dump(mode="json")}),
        input_context={"source_context": context.source_context, "route": selected.route.value,
            "media_inputs": [m.model_dump(mode="json") for m in media], "media_file_states": states,
            "method_skill_ids": [], "revision_instructions": None, "compile_mode": "SAVED_ROUTE",
            "resolution": selected.resolution, "aspect_ratio": selected.aspect_ratio},
        compiled_prompt=selected.prompt, resolved_route=selected.route, resolved_media_inputs=media,
        created_at=now, updated_at=now,
    )
    effective = request.model_copy(update={"shot_directives": [authored], "use_language_agent": False,
        "consider_video_guides": False, "resolution": selected.resolution, "aspect_ratio": selected.aspect_ratio,
        "source_operation_id": operation_id})
    return SavedRouteReuse(original=original, request=effective, operation=operation)
