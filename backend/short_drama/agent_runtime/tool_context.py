"""Stable tool discovery and field-level views of durable read observations."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from difflib import get_close_matches
import json
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.models import ArtifactRef, WorkOrder
from short_drama.agent_runtime.context_markers import context_marker, context_read_contract
from short_drama.agent_runtime.model_input import json_text, pointer_value
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.studio_trace import record_trace


# These are starting contracts, never an execution allowlist or a workflow.
_COMMON = ("artifact.read_current", "agent.read_context")
_STAGE_TOOLS: dict[str, tuple[str, ...]] = {
    "STORY": ("script.save_scene",),
    "STORYBOARD": ("shot.save_current", "shot.get_storyboard"),
    "POST": ("timeline.read_editorial", "timeline.edit_clips", "timeline.export_editorial", "timeline.read_export"),
    "VISUAL_GEN": ("route.prepare_h3_group", "route.h3_capability_profile"),
}
_ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "WRITER": ("script.save_scene", "artifact.create_assets"),
    "DIRECTOR": ("shot.save_current",),
    "ASSET_PLANNER": ("artifact.get_asset_work_context", "artifact.patch_asset_plan"),
    "ASSET_BUILDER": ("artifact.get_asset_work_context", "artifact.patch_asset_plan_fields", "image.capability", "image.enqueue", "image.view"),
    "ASSET_VERIFIER": ("artifact.get_asset_work_context", "artifact.inspect_asset", "image.view"),
    "VIDEO_PROMPT_COMPILER": ("route.get_generation_unit_context", "route.h3_capability_profile", "route.compile_h3_prompt", "route.plan_h3"),
    "ROUTE_PLANNER": ("route.get_generation_unit_context", "route.h3_capability_profile", "route.plan_h3"),
    "EPISODE_REVIEWER": ("timeline.read_editorial", "video.inspect_take"),
}


def leader_artifact_tool_schema_ids(refs: list[ArtifactRef], project_id: str) -> list[str]:
    """Starting contracts for saved domains already visible in this planning call.

    This only discovers schemas: no goal-word matching, execution, or assertion
    that a referenced historical result is currently usable. Drafts are distinct.
    """
    kinds = {ref.artifact_type for ref in refs if ref.project_id == project_id}
    ids: list[str] = []
    if kinds & {"storyboard", "shot", "clip_group"}:
        ids.extend(("shot.get_storyboard", "shot.patch_storyboard", "change.apply"))
    if kinds & {"generation_plan", "generation_unit"}:
        ids.extend(("route.get_generation_unit_context", "route.compile_generation_plan"))
    if kinds & {"h3_route_plan", "h3_group_operation"}:
        ids.extend(("route.list_h3_plans", "route.get_h3_plan", "route.prepare_h3_group", "route.plan_h3"))
    return ids


def initial_tool_schema_ids(work_order: WorkOrder | None = None) -> list[str]:
    if work_order is None:
        return [*_COMMON, "artifact.get_production_document"]
    stage = work_order.production_prompt_stage
    role = work_order.production_skill_role
    # An explicit post-production task takes precedence over a general director role.
    task_tools = _STAGE_TOOLS.get(stage.value, ()) if stage is not None else ()
    role_tools = _ROLE_TOOLS.get(role.value, ()) if role is not None and (stage is None or stage.value != "POST") else ()
    return list(dict.fromkeys([*_COMMON, *task_tools, *role_tools]))


def schema_request_feedback(
    requested: list[str], available: list[str], loaded: list[str],
) -> dict[str, JsonValue]:
    unknown = [tool_id for tool_id in requested if tool_id not in available]
    return {
        "kind": "UNKNOWN_TOOL_SCHEMA" if unknown else "TOOL_SCHEMA_ALREADY_LOADED",
        "requested_ids": cast(JsonValue, requested),
        "unknown_ids": cast(JsonValue, unknown),
        "suggestions": {tool_id: cast(JsonValue, get_close_matches(tool_id, available, n=3, cutoff=0.35)) for tool_id in unknown},
        "loaded_ids": cast(JsonValue, loaded),
        "instruction": "Use exact IDs from available_tools. Current loaded definitions are already in tool_schemas; return the needed operation or result without requesting them again.",
    }


def normalized_read_arguments(
    arguments: Mapping[str, JsonValue], schema: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    """Remove explicit defaults using the current schema, including nested refs.

    Only identity changes: invocation arguments and Trace are never rewritten.
    Array order is preserved because ordered references and page ranges matter.
    """
    if schema is None:
        return dict(arguments)

    def resolve(node: Mapping[str, JsonValue], value: JsonValue) -> Mapping[str, JsonValue]:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            definitions = schema.get("$defs")
            target = definitions.get(ref.removeprefix("#/$defs/")) if isinstance(definitions, dict) else None
            if isinstance(target, dict):
                node = {**target, **{key: item for key, item in node.items() if key != "$ref"}}
        alternatives = node.get("anyOf")
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if not isinstance(alternative, dict):
                    continue
                candidate = resolve(alternative, value)
                kind = candidate.get("type")
                if (isinstance(value, dict) and kind == "object") or (isinstance(value, list) and kind == "array"):
                    return {**candidate, **{key: item for key, item in node.items() if key != "anyOf"}}
        return node

    def visit(value: JsonValue, node: Mapping[str, JsonValue]) -> JsonValue:
        node = resolve(node, value)
        if isinstance(value, dict):
            properties = node.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                child = properties.get(key)
                child = child if isinstance(child, dict) else {}
                if "default" in child and item == child["default"]:
                    continue
                result[key] = visit(item, child)
            return result
        if isinstance(value, list):
            child = node.get("items")
            return [visit(item, child if isinstance(child, dict) else {}) for item in value]
        return value

    return cast(dict[str, JsonValue], visit(dict(arguments), schema))


def _pointer(path: str, key: str | int) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _selected_ids(value: JsonValue) -> set[str]:
    selected: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "id" or key.endswith("_id")) and key not in {"project_id", "run_id", "parent_run_id", "version_id", "script_version_id", "bible_version_id"} and isinstance(item, str):
                selected.add(item)
            elif key.endswith("_ids") and isinstance(item, list):
                selected.update(item for item in item if isinstance(item, str))
            elif isinstance(item, dict):
                selected.update(_selected_ids(item))
    return selected


def project_retained_read_results(
    working_set: dict[str, JsonValue], *, store: AgentRuntimeStore | None,
    project_id: str, work_order: WorkOrder,
) -> dict[str, JsonValue]:
    """Keep read identities and selected facts; externalize large field bodies.

    This is a normal model-view policy, independent of the 258k overflow guard.
    The immediately requested read is delivered intact, as are explicit context
    pages and repair payloads. Older distinct reads remain separately addressable;
    no last-N history filter or lossy truncation is used.
    """
    if store is None:
        return working_set
    observations = working_set.get("read_observations")
    if not isinstance(observations, list):
        return working_set
    projected = deepcopy(working_set)
    has_context_refs = False
    selected = _selected_ids(work_order.task_inputs)
    selected.update(ref.artifact_id for ref in work_order.scope)
    latest = working_set.get("latest_handoff")
    if isinstance(latest, dict):
        request = latest.get("request")
        if isinstance(request, dict):
            selected.update(_selected_ids(request.get("arguments")))
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict) or observation.get("tool_name") == "agent.read_context":
            continue
        result = observation.get("result")
        if not isinstance(result, dict) or count_text_tokens(json_text(result)) <= 6000:
            continue
        sequence = observation.get("source_sequence")
        snapshot_id = f"context-read-{work_order.id}-{sequence}"
        snapshot = store.get_context_snapshot(snapshot_id, work_order.parent_run_id)
        if snapshot is None:
            store.save_context_snapshot(snapshot_id, work_order.parent_run_id, result)
            record_trace("context.offloaded", {"snapshot_id": snapshot_id,
                "run_id": work_order.parent_run_id, "payload": result,
                "reason": "retained_read_projection"})

        def marker(value: JsonValue, path: str) -> dict[str, JsonValue]:
            return context_marker(snapshot_id, path, estimated_tokens=count_text_tokens(json_text(value)))

        def brief(value: JsonValue, path: str) -> JsonValue:
            if not isinstance(value, dict):
                return marker(value, path)
            kept = {key: item for key, item in value.items() if (
                key in {"id", "title", "name", "status", "kind", "role", "summary", "description", "duration_seconds", "start_seconds", "end_seconds"}
                or key.endswith("_id") or key.endswith("_ids")
            ) and count_text_tokens(json_text(item)) <= 180}
            return {**kept, "saved_content": marker(value, path)}

        def project(value: JsonValue, path: str) -> JsonValue:
            size = count_text_tokens(json_text(value))
            if size <= 2000:
                return value
            if isinstance(value, list) and size > 4000:
                return {**marker(value, path), "item_count": len(value), "items": [
                    project(item, _pointer(path, position))
                    if _selected_ids(item) & selected else brief(item, _pointer(path, position))
                    for position, item in enumerate(value)
                ]}
            if isinstance(value, dict):
                return {key: project(item, _pointer(path, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [project(item, _pointer(path, position)) for position, item in enumerate(value)]
            return marker(value, path)

        target = projected.get("read_observations")
        assert isinstance(target, list) and isinstance(target[index], dict)
        output = result.get("output")
        content = output.get("content") if isinstance(output, dict) else None
        editor = content.get("project") if isinstance(content, dict) else None
        view: JsonValue
        if observation.get("tool_name") == "timeline.read_editorial" and isinstance(editor, dict):
            # The typed timeline read already provides clip/subtitle summaries.
            # Its optional full editor serialization is needed for the immediate
            # read, but repeating every asset and clip in later decisions is not.
            summary: dict[str, JsonValue] = {
                key: editor[key] for key in ("id", "name", "activeTimelineId")
                if key in editor and count_text_tokens(json_text(editor[key])) <= 180
            }
            for key, count_key in (("assets", "asset_count"), ("timelines", "timeline_count")):
                values = editor.get(key)
                if isinstance(values, list):
                    summary[count_key] = len(values)
            summary["saved_content"] = marker(editor, "/output/content/project")
            assert isinstance(output, dict) and isinstance(content, dict)
            view = {**result, "output": {**output, "content": {**content, "project": summary}}}
        else:
            view = project(result, "")
        if count_text_tokens(json_text(view)) < count_text_tokens(json_text(result)):
            target_observation = cast(dict[str, JsonValue], target[index])
            target_observation["result"] = view
            has_context_refs = True
    shared = _share_retained_reference_entities(projected, store=store, work_order=work_order)
    if has_context_refs or shared:
        projected["context_read"] = context_read_contract(project_id=project_id, run_id=work_order.parent_run_id)
    return projected


def _share_retained_reference_entities(
    working_set: dict[str, JsonValue], *, store: AgentRuntimeStore,
    work_order: WorkOrder,
) -> bool:
    """Share identical reference bodies with an already durable retained read.

    Read scope/order and entity provenance remain intact. The visible read avoids
    another Tool turn; its existing snapshot is still resolvable if the final
    request budget externalizes that read. Small entities stay inline.
    """
    entities = working_set.get("entities")
    observations = working_set.get("read_observations")
    if not isinstance(entities, list) or not isinstance(observations, list):
        return False
    shared = False
    paths = {"reference_pack": ("/output/reference_packs",),
             "shot_reference_plan": ("/output/shot_reference_plans",),
             "shot": ("/output/storyboard/shots", "/output/content/shot")}
    remaining = [entity for entity in entities if isinstance(entity, dict)
                 and isinstance(entity.get("kind"), str) and entity.get("kind") in paths
                 and isinstance(entity.get("state"), dict)]

    def same_value(left: JsonValue, right: JsonValue) -> bool:
        # Object key order is immaterial; array order and JSON scalar types are
        # significant (Python equality alone treats True and 1 as equal).
        return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)

    for index in range(len(observations) - 1, -1, -1):
        if not remaining:
            break
        observation = observations[index]
        if not isinstance(observation, dict) or observation.get("tool_name") == "agent.read_context":
            continue
        result = observation.get("result")
        output = result.get("output") if isinstance(result, dict) and result.get("status") == "SUCCEEDED" else None
        sequence = observation.get("source_sequence")
        if not isinstance(output, dict) or not isinstance(sequence, int) or isinstance(sequence, bool):
            continue
        snapshot_id = f"context-read-{work_order.id}-{sequence}"
        snapshot: dict[str, JsonValue] | None = None
        for entity in list(remaining):
            kind = cast(str, entity["kind"])
            state = cast(dict[str, JsonValue], entity["state"])
            body = state.get("shot") if kind == "shot" else state
            candidates: list[tuple[str, JsonValue]] = []
            for base_path in paths[kind]:
                try:
                    values = pointer_value(cast(dict[str, JsonValue], result), base_path)
                except ValueError:
                    continue
                if isinstance(values, list):
                    candidates.extend((f"{base_path}/{position}", value) for position, value in enumerate(values))
                elif isinstance(values, dict):
                    candidates.append((base_path, values))
            for path, value in candidates:
                if not isinstance(value, dict) or not same_value(body, value):
                    continue
                marker = context_marker(snapshot_id, path)
                marker["visible_at"] = f"/working_set/read_observations/{index}/result{path}"
                if count_text_tokens(json_text(marker)) >= count_text_tokens(json_text(body)):
                    break
                if snapshot is None:
                    snapshot = store.get_context_snapshot(snapshot_id, work_order.parent_run_id)
                if snapshot is None:
                    break
                try:
                    saved = pointer_value(snapshot, path)
                except ValueError:
                    break
                if same_value(saved, value):
                    if kind == "shot":
                        state["shot"] = marker
                    else:
                        entity["state"] = marker
                    remaining.remove(entity)
                    shared = True
                break
    return shared
