"""Small, concrete repair feedback and write acknowledgements for Tool callers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import JsonValue, ValidationError

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.tools.contracts import ToolError, ToolErrorDetail, ToolWriteReceipt


_SCOPE_FIELDS = {
    "id", "project_id", "shot_id", "shot_ids", "scene_id", "asset_id", "asset_version_id",
    "work_order_id", "work_item_id", "generation_unit_id", "clip_group_id", "take_id",
    "document_id", "version_id", "expected_revision", "base_version_id", "anchor_clip_id",
    "source_in_seconds", "source_out_seconds", "start_seconds", "end_seconds",
    "timeline_id", "subtitle_ids", "clip_ids",
}


def argument_scope(arguments: Mapping[str, object], *, nested: bool = False) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in arguments.items():
        if key not in _SCOPE_FIELDS:
            if nested and isinstance(value, dict):
                children = argument_scope(cast(dict[str, object], value), nested=True)
                if children:
                    result[key] = children
            elif nested and isinstance(value, list):
                children_by_index = {str(index): selected for index, item in enumerate(cast(list[object], value))
                    if isinstance(item, dict) and (selected := argument_scope(cast(dict[str, object], item), nested=True))}
                if children_by_index:
                    result[key] = cast(dict[str, JsonValue], children_by_index)
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in cast(list[object], value)):
            result[key] = cast(list[JsonValue], value)
    return result


def validation_feedback(error: ValidationError, arguments: Mapping[str, object]) -> ToolError:
    details: list[ToolErrorDetail] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = issue["loc"]
        pointer = ""
        node: object = dict(arguments)
        identifiers: dict[str, JsonValue] = {}
        for segment in location:
            if isinstance(node, dict):
                selected = argument_scope(cast(dict[str, object], node))
                if selected:
                    identifiers[pointer or "/"] = selected
                node = cast(dict[object, object], node).get(segment)
            elif isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(cast(list[object], node)):
                node = cast(list[object], node)[segment]
            else:
                node = None
            pointer += "/" + str(segment).replace("~", "~0").replace("/", "~1")
        constraints: dict[str, JsonValue] = {}
        for key, value in (issue.get("ctx") or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                constraints[key] = value
        details.append(ToolErrorDetail(path=pointer or "/", code=issue["type"], message=issue["msg"],
                                       identifiers=identifiers, constraints=constraints))
    summary = "; ".join(f"{item.path}: {item.message}" for item in details[:8])
    if len(details) > 8:
        summary += f"; {len(details) - 8} further field errors are in details"
    return ToolError(kind="ValidationError", phase="VALIDATION", message=summary,
        scope=argument_scope(arguments, nested=True), details=details, retryable=False,
        suggested_actions=["Correct the indicated fields in the existing arguments, preserving unrelated IDs and values; the operation has not started."])


_STATE_FIELDS = {
    "id", "project_id", "status", "state", "revision", "version", "current_version_id",
    "current_version", "editorial_revision", "shot_revisions", "timeline_id", "saved", "is_saved",
    "output_path", "media_path", "local_path", "file_path", "video_path", "srt_path", "clean_video_path", "duration_seconds", "availability",
    "generation_unit_id", "clip_group_id", "output_slot_id", "selected_take_id", "overlay_id",
    "asset_version_id", "task_id", "provider_task_id", "semantic_status", "next_poll_at",
}


def write_receipt(output: Mapping[str, object], refs: Sequence[ArtifactRef]) -> ToolWriteReceipt:
    """Select response facts; never infer changed IDs or media completion from input intent."""
    state: dict[str, JsonValue] = {}
    changes: dict[str, list[str]] = {}
    nodes = [("", output)]
    nodes.extend((key, cast(dict[str, object], value)) for key, value in output.items()
                 if key in {"content", "document", "version", "output_slot", "task", "asset", "report", "take"}
                 and isinstance(value, dict))
    for prefix, node in nodes:
        for key, value in node.items():
            label = f"{prefix}.{key}" if prefix else key
            if key in _STATE_FIELDS and (isinstance(value, (str, int, float, bool)) or value is None):
                state[label] = value
            elif key == "shot_revisions" and isinstance(value, dict):
                state[label] = cast(dict[str, JsonValue], value)
            elif key.startswith(("changed_", "updated_", "created_", "removed_")) and key.endswith("_ids"):
                if isinstance(value, list) and all(isinstance(item, str) for item in cast(list[object], value)):
                    changes[label] = cast(list[str], value)
    return ToolWriteReceipt(artifact_refs=list(refs), current_state=state, changed_ids=changes)
