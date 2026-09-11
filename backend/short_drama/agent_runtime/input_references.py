"""Share identical, visible ArtifactRefs in the final private model input only."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.agent_runtime.token_budget import count_text_tokens


def _text(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _keys(value: JsonValue) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    for item in value:
        if not isinstance(item, dict):
            return None
        try:
            ArtifactRef.model_validate(item)
        except ValueError:
            return None
    # Compare the entire original value, including any optional fields. Do not
    # collapse different versions/projects or normalize an unfamiliar shape.
    return [_text(item) for item in value]


def project_visible_artifact_refs(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Use exact JSON-pointer/index lookups only after budget offloading is done.

    The canonical table remains a visible list. Saved Tool results, snapshots,
    read bodies and user-authored JSON are never rewritten by this projection.
    """
    projected = deepcopy(payload)
    working = projected.get("working_set")
    if not isinstance(working, dict):
        return projected
    working = dict(working)
    projected["working_set"] = working
    central = working.get("artifact_refs")
    central_keys = _keys(central)

    def share(node: JsonValue, table_keys: list[str] | None, pointer: str) -> JsonValue:
        if not isinstance(node, dict) or table_keys is None or "artifact_refs_from" in node:
            return node
        values = node.get("artifact_refs")
        value_keys = _keys(values)
        if value_keys is None or any(key not in table_keys for key in value_keys):
            return node
        lookup: dict[str, JsonValue] = {
            "pointer": pointer,
            "indices": cast(list[JsonValue], [table_keys.index(key) for key in value_keys]),
        }
        if count_text_tokens(_text({"artifact_refs_from": lookup})) >= count_text_tokens(_text({"artifact_refs": values})):
            return node
        # Copy this envelope instead of mutating it: callers can construct two
        # payload paths from the same Python object, including an authored body.
        return {**{key: value for key, value in node.items() if key != "artifact_refs"},
                "artifact_refs_from": lookup}

    central_pointer = "/working_set/artifact_refs"
    latest = working.get("latest_handoff")
    result = latest.get("result") if isinstance(latest, dict) else None
    if isinstance(result, dict):
        result = cast(dict[str, JsonValue], share(result, central_keys, central_pointer))
        # If the central table was offloaded or cannot represent this result,
        # its still-visible original top-level refs can serve the receipt.
        top_keys = _keys(result.get("artifact_refs"))
        if "receipt" in result:
            result = {**result, "receipt": share(result["receipt"], top_keys if top_keys is not None else central_keys,
                "/working_set/latest_handoff/result/artifact_refs" if top_keys is not None else central_pointer)}
        assert isinstance(latest, dict)
        working["latest_handoff"] = {**latest, "result": result}
    checkpoints = working.get("tool_checkpoints")
    if isinstance(checkpoints, list):
        working["tool_checkpoints"] = [share(checkpoint, central_keys, central_pointer) for checkpoint in checkpoints]
    progress = working.get("progress")
    if isinstance(progress, dict):
        progress = dict(cast(dict[str, JsonValue], share(progress, central_keys, central_pointer)))
        for key in ("lifecycle", "leader_handoff", "worker_handoff", "external_handoff"):
            if key in progress:
                progress[key] = share(progress[key], central_keys, central_pointer)
        working["progress"] = progress
    return projected
