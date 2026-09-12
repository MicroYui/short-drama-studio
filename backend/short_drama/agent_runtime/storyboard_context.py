"""Current scoped Storyboard facts from exact reads and actual saved deltas."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.asset_plan_context import EntityT
from short_drama.agent_runtime.models import SpecialistTurnRecord

_FIELDS = {"shots": ("shot", "shot"), "clip_groups": ("clip_group", "clip_group"),
           "scene_nodes": ("storyboard_scene_node", "scene_node")}
_KINDS = frozenset({"shot", "clip_group", "storyboard_scene_node", "storyboard_state"})


def _output(result: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    output = result.get("output")
    return output if isinstance(output, dict) else {}


def _read(output: Mapping[str, JsonValue]) -> tuple[dict[str, JsonValue], dict[str, JsonValue]] | None:
    document, version = output.get("document"), output.get("version")
    if not isinstance(document, dict) or not isinstance(version, dict):
        return None
    body = output.get("storyboard")
    if body is None and document.get("kind") == "storyboard":
        body = version.get("payload")
    if not isinstance(body, dict) or not isinstance(body.get("shots"), list):
        return None
    shots = cast(list[JsonValue], body["shots"])
    if not shots or not all(isinstance(item, dict) for item in shots):
        return None
    if not isinstance(document.get("id"), str) or not isinstance(version.get("id"), str) or not isinstance(version.get("version"), int):
        return None
    return body, {"document_id": document["id"], "version_id": version["id"], "version": version["version"]}


def changed_storyboard_shots(turn: SpecialistTurnRecord) -> set[str] | None:
    """None means an old/unknown write, not an empty exact change set."""
    output = _output(turn.result or {})
    context = output.get("write_context")
    if turn.tool_name != "shot.patch_storyboard" or not isinstance(context, dict):
        return None
    objects = context.get("objects")
    shots = objects.get("shots") if isinstance(objects, dict) else None
    if not isinstance(shots, dict) or not isinstance(shots.get("changed_ids"), list) or not isinstance(shots.get("removed_ids"), list):
        return None
    return {item for key in ("changed_ids", "removed_ids") for item in cast(list[JsonValue], shots[key]) if isinstance(item, str)}


def storyboard_read_is_stale(result: Mapping[str, JsonValue], known_versions: Mapping[str, int]) -> bool:
    selected = _read(_output(result))
    if selected is None:
        return False
    _, identity = selected
    return cast(int, identity["version"]) < known_versions.get(cast(str, identity["document_id"]), 0)


def fold_storyboard_context(
    entities: dict[tuple[str, str], EntityT], turn: SpecialistTurnRecord, *,
    known_versions: dict[str, int], shot_write_revisions: Mapping[str, int | None],
    entity_type: type[EntityT], is_write: bool,
) -> None:
    result = turn.result
    if not isinstance(result, dict) or result.get("status") != "SUCCEEDED":
        return
    output = _output(result)
    selected = _read(output)
    delta = output.get("write_context") if turn.tool_name == "shot.patch_storyboard" else None
    document, version = output.get("document"), output.get("version")
    if not isinstance(document, dict) or not isinstance(version, dict):
        # A different Shot writer has no verified whole-board parent/delta.
        # Its exact Shot retirement is handled by the existing PM hook.
        if is_write:
            changed = output.get("changed_shot_ids")
            if isinstance(changed, list) and changed:
                for key, entity in list(entities.items()):
                    if entity.kind in _KINDS - {"shot"} and isinstance(entity.state.get("storyboard_identity"), dict):
                        entities.pop(key)
        return
    if document.get("kind") != "storyboard" or not isinstance(document.get("id"), str):
        return
    doc_id = cast(str, document["id"])
    number = version.get("version")
    if not isinstance(number, int) or isinstance(number, bool) or not isinstance(version.get("id"), str):
        return
    observed = document.get("current_version")
    known_versions[doc_id] = max(known_versions.get(doc_id, 0), number,
        observed if isinstance(observed, int) and not isinstance(observed, bool) else 0)
    if number < known_versions[doc_id]:
        return  # Explicit historical reads remain addressable, never current entities.
    identity: dict[str, JsonValue] = {"document_id": doc_id, "version_id": version["id"], "version": number}
    objects = delta.get("objects") if isinstance(delta, dict) else None
    can_merge = is_write and isinstance(delta, dict) and isinstance(objects, dict) and delta.get("sources_changed") is False
    parent = delta.get("parent_version_id") if isinstance(delta, dict) else None
    for key, entity in list(entities.items()):
        source = entity.state.get("storyboard_identity")
        if entity.kind not in _KINDS or not isinstance(source, dict) or source.get("document_id") != doc_id:
            continue
        through = entity.state.get("unchanged_through_storyboard_identity", source)
        field = next((name for name, (kind, _) in _FIELDS.items() if kind == entity.kind), None)
        change = objects.get(field) if isinstance(objects, dict) and field is not None else None
        affected: set[str] = ({item for name in ("changed_ids", "removed_ids")
                     for item in cast(list[JsonValue], change.get(name, [])) if isinstance(item, str)}
                    if isinstance(change, dict) else set())
        if (can_merge and field is not None and isinstance(through, dict)
            and through.get("version_id") == parent and entity.id not in affected):
            entity.state["unchanged_through_storyboard_identity"] = deepcopy(identity)
        else:
            entities.pop(key)
    if selected is None and not isinstance(delta, dict):
        return  # Legacy/all-content writes expose identity only; no guessed prose.
    body = selected[0] if selected is not None else cast(dict[str, JsonValue], delta)
    metadata = ({key: value for key, value in body.items() if key not in _FIELDS}
                if selected is not None else body.get("metadata"))
    orders: dict[str, JsonValue] = {}
    for field, (kind, body_key) in _FIELDS.items():
        part = objects.get(field) if isinstance(objects, dict) else None
        values = body.get(field) if selected is not None else part.get("saved") if isinstance(part, dict) else None
        if not isinstance(values, list):
            continue
        orders[field] = ([item["id"] for item in values if isinstance(item, dict)]
                         if selected is not None else part.get("order") if isinstance(part, dict) else [])
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            identifier = cast(str, value["id"])
            key = (kind, identifier if kind == "shot" else doc_id + ":" + identifier)
            if kind == "shot":
                revision = value.get("revision")
                existing = entities.get(key)
                old_shot = existing.state.get("shot") if existing is not None else None
                old_revision = old_shot.get("revision") if isinstance(old_shot, dict) else None
                written = shot_write_revisions.get(identifier)
                if not isinstance(revision, int) or (isinstance(old_revision, int) and revision < old_revision):
                    continue
                if not is_write and (isinstance(written, int) and revision < written
                    or identifier in shot_write_revisions and written is None):
                    continue
                entities.pop(("saved_shot", identifier), None)
            state: dict[str, JsonValue] = {body_key: deepcopy(value), "storyboard_identity": deepcopy(identity),
                "source": "SAVED_STORYBOARD_PATCH" if is_write else "CURRENT_STORYBOARD_READ",
                "artifact_ref": {"project_id": turn.arguments.get("project_id"),
                    "artifact_type": "shot" if kind == "shot" else "production_document_version",
                    "artifact_id": identifier if kind == "shot" else identity["version_id"],
                    "version": value.get("revision") if kind == "shot" else number}}
            entities[key] = entity_type(kind=kind, id=identifier, source_tool=turn.tool_name,
                source_sequence=turn.sequence, state=state)
    entities[("storyboard_state", doc_id)] = entity_type(kind="storyboard_state", id=doc_id,
        source_tool=turn.tool_name, source_sequence=turn.sequence,
        state={"storyboard_identity": identity, "metadata": deepcopy(metadata), "order": orders})


def hide_visible_storyboard_entities(
    entities: dict[tuple[str, str], EntityT], visible_sequences: set[int],
) -> None:
    for key, entity in list(entities.items()):
        if (entity.kind in _KINDS and getattr(entity, "source_sequence", None) in visible_sequences
            and isinstance(entity.state.get("storyboard_identity"), dict)):
            entities.pop(key)  # The same successful read is already visible once.
