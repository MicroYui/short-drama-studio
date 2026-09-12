"""Retain selected media facts and local construction parameters, not project history."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import JsonValue

from short_drama.agent_runtime.models import WorkOrder


def selected_asset_version_ids(work: WorkOrder) -> frozenset[str]:
    """Version selectors in this task may select non-current DRAFT candidates.

    Inspect only explicit inputs and successful output identities. A broad
    project read must not make every historical version part of the workset.
    """
    selected = {ref.artifact_id for ref in work.scope if ref.artifact_type == "asset_version"}

    def visit(value: JsonValue) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("artifact_type") == "asset_version" and isinstance(value.get("artifact_id"), str):
                selected.add(str(value["artifact_id"]))
            for key, item in value.items():
                if key == "version_id" or key.endswith("_version_id"):
                    if isinstance(item, str):
                        selected.add(item)
                elif key == "version_ids" or key.endswith("_version_ids"):
                    if isinstance(item, list):
                        selected.update(entry for entry in item if isinstance(entry, str))
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(work.task_inputs)
    for turn in work.specialist_turns:
        visit(turn.arguments)
        result = turn.result
        if result is None or result.get("status") != "SUCCEEDED" or turn.tool_name == "project.get":
            continue
        visit(result.get("artifact_refs"))
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        version = output.get("version")
        if isinstance(version, dict) and "media_type" in version and isinstance(version.get("id"), str):
            selected.add(str(version["id"]))
        visit(output.get("task"))
        visit(output.get("tasks"))
    return frozenset(selected)


def asset_construction_state(version: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Use saved deterministic metadata verbatim; never reconstruct it from prose.

    Provider prompts remain in the exact Tool attempt. Local operation JSON is
    a reproducible recipe (for example the actual plane quadrilaterals), and is
    retained alongside typed crop/plate/sheet metadata. Large values remain
    eligible for the existing context compiler's lossless offload.
    """
    state = {key: version[key] for key in (
        "descriptor", "derivation_references", "reference_sheet", "image_crop", "text_plate",
    ) if key in version and version[key] is not None}
    source = version.get("source")
    if not isinstance(source, dict):
        return state
    state["source"] = {key: source[key] for key in (
        "source_type", "source_label", "source_uri", "provider", "model", "created_at",
    ) if key in source}
    prompt = source.get("prompt")
    if source.get("source_type") == "LOCAL_DERIVED" and source.get("provider") == "local" and isinstance(prompt, str):
        try:
            parameters: JsonValue = json.loads(prompt)
        except (ValueError, TypeError):
            return state
        if isinstance(parameters, dict):
            state["local_operation"] = parameters
    return state
