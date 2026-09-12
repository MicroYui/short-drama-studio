"""Retire disproven domains of a broad current read without rewriting its source."""

from __future__ import annotations

from pydantic import JsonValue


SPEC_WRITES = frozenset({"artifact.create_production_spec", "artifact.replace_production_spec"})
SETTINGS_FIELDS = frozenset({"language", "aspect_ratio", "target_duration_seconds"})


def retire_project_read(
    result: dict[str, JsonValue], *, script_changed: bool, spec_changed: bool,
) -> dict[str, JsonValue]:
    """Keep unrelated Bible/assets and the original read's identity intact.

    A new script version does not prove other scenes in an old aggregate are
    current: another Worker may have changed them before this write's parent.
    New selected bodies already arrive from their own actual save receipts.
    """
    output = result.get("output")
    document = output.get("document") if isinstance(output, dict) else None
    project = document.get("project") if isinstance(document, dict) else None
    if not isinstance(output, dict) or not isinstance(document, dict) or not isinstance(project, dict):
        return result
    if not script_changed and not spec_changed:
        return result
    remaining = dict(document)
    header = dict(project)
    header.pop("updated_at", None)
    if script_changed:
        header.pop("current_script_version_id", None)
        remaining.pop("script_versions", None)
    if spec_changed:
        for field in SETTINGS_FIELDS:
            header.pop(field, None)
        remaining.pop("production_spec_document_id", None)
        documents = remaining.get("production_documents")
        if isinstance(documents, list):
            remaining["production_documents"] = [item for item in documents
                if not (isinstance(item, dict) and item.get("kind") == "production.spec")]
        slots = remaining.get("output_slots")
        if isinstance(slots, list):
            remaining["output_slots"] = [item for item in slots
                if not (isinstance(item, dict) and item.get("id") == "production.spec.current")]
    remaining["project"] = header
    return {**result, "output": {**output, "document": remaining}}
