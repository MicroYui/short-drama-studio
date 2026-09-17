"""Mechanical model views of already retained historical completion receipts."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from pydantic import JsonValue


_GUIDANCE = (
    "Only historical completion_receipts use this view. ArtifactRef without project_id uses this "
    "project_id; version:null remains unspecified. shared_read:true uses the read contract here. "
    "saved_source names one exact context_ref with separate result_pointer and arguments_pointer; "
    "use agent.read_context to read the original saved bodies. current_refs_match_saved:true means "
    "current_artifact_refs exactly equals artifact_refs, including version. Missing/false makes no "
    "assertion. Timestamps and currentness describe the saved handoff, not a new verification; "
    "refresh mutable content before editing. Delivery conclusions and current Tool results are unchanged."
)
_REF_KEYS = frozenset({"project_id", "artifact_type", "artifact_id", "version"})
_SOURCE_KEYS = frozenset({"context_ref", "pointer"})


def _ref(value: JsonValue, project_id: str) -> JsonValue:
    if (isinstance(value, dict) and frozenset(value) == _REF_KEYS
            and value.get("project_id") == project_id
            and isinstance(value.get("artifact_type"), str)
            and isinstance(value.get("artifact_id"), str)):
        return {key: item for key, item in value.items() if key != "project_id"}
    return value


def _receipt(row: dict[str, JsonValue], project_id: str) -> dict[str, JsonValue]:
    kind = row.get("kind")
    if kind is not None and (not isinstance(kind, str) or kind not in {
        "saved_tool_result", "saved_media_metadata", "saved_media_observation",
    }):
        return row
    # Unknown/future projected shapes remain untouched, including their markers.
    if "saved_source" in row or "current_refs_match_saved" in row:
        return row
    result = deepcopy(row)
    if "artifact_ref" in row:
        result["artifact_ref"] = _ref(row["artifact_ref"], project_id)
    for key in ("artifact_refs", "current_artifact_refs"):
        refs = row.get(key)
        if isinstance(refs, list):
            result[key] = [_ref(ref, project_id) for ref in refs]
    currentness = result.get("artifact_currentness")
    if isinstance(currentness, list):
        for state in currentness:
            if isinstance(state, dict) and "artifact_ref" in state:
                state["artifact_ref"] = _ref(state["artifact_ref"], project_id)
    if kind not in {"saved_tool_result", "saved_media_observation"}:
        return result
    saved, arguments = row.get("saved_result"), row.get("saved_arguments")
    if (isinstance(saved, dict) and isinstance(arguments, dict)
            and frozenset(saved) == _SOURCE_KEYS and frozenset(arguments) == _SOURCE_KEYS
            and isinstance(saved.get("context_ref"), str) and bool(saved["context_ref"])
            and saved["context_ref"] == arguments.get("context_ref")
            and isinstance(saved.get("pointer"), str) and isinstance(arguments.get("pointer"), str)):
        result["saved_source"] = {"context_ref": saved["context_ref"],
            "result_pointer": saved["pointer"], "arguments_pointer": arguments["pointer"]}
        del result["saved_result"]
        del result["saved_arguments"]
    if (isinstance(row.get("artifact_refs"), list)
            and isinstance(row.get("current_artifact_refs"), list)
            and row["artifact_refs"] == row["current_artifact_refs"]):
        result["current_refs_match_saved"] = True
        del result["current_artifact_refs"]
    return result


def project_completion_receipt_view(
    working_set: dict[str, JsonValue], *, project_id: str,
) -> dict[str, JsonValue]:
    """Project only after normal handoff selection/retention; never save this view.

    Every receipt keeps its own row. Shared explanation lives outside handoffs so
    the existing model-input offloader cannot remove it with an individual body.
    No budget, source snapshot, current result or delivery text is changed.
    """
    handoffs = working_set.get("completion_handoffs")
    if not project_id or not isinstance(handoffs, list) or "completion_receipt_view" in working_set:
        return working_set
    projected = deepcopy(working_set)
    shared: dict[str, JsonValue] = {"version": 1, "project_id": project_id, "guidance": _GUIDANCE}
    changed = False
    for handoff in cast(list[JsonValue], projected["completion_handoffs"]):
        if not isinstance(handoff, dict):
            continue
        body = handoff.get("result")
        receipts = body.get("completion_receipts") if isinstance(body, dict) else None
        if not isinstance(receipts, list):
            continue  # Already externalized deliveries stay externalized.
        for index, row in enumerate(receipts):
            if not isinstance(row, dict):
                continue
            view: dict[str, JsonValue] = _receipt(row, project_id)
            read = row.get("read")
            if (set(row) == {"kind", "read"} and row.get("kind") == "saved_result_read"
                    and isinstance(read, dict) and read.get("project_id") == project_id
                    and read.get("tool_name") == "agent.read_context"):
                if "read" not in shared:
                    shared["read"] = deepcopy(read)
                if shared["read"] == read:
                    view = {"kind": "saved_result_read", "shared_read": True}
            if view != row:
                changed = True
                receipts[index] = view
    if not changed:
        return working_set
    projected["completion_receipt_view"] = shared
    return projected
