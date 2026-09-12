"""Preserve exact previously read item scopes across proven local plan writes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Protocol, TypeVar, cast

from pydantic import JsonValue

from short_drama.agent_runtime.models import SpecialistTurnRecord
from short_drama.asset_plan_receipts import ASSET_PLAN_WRITE_TOOLS, LOCAL_ASSET_PLAN_WRITE_TOOLS


class EntitySnapshot(Protocol):
    kind: str
    id: str
    state: dict[str, JsonValue]

    def __init__(self, *, kind: str, id: str, source_tool: str,
                 source_sequence: int, state: dict[str, JsonValue]) -> None: ...


EntityT = TypeVar("EntityT", bound=EntitySnapshot)


# Current AssetPlan write receipts plus the summary fields retained by the
# legacy receipt adapter. A compound Shot save also has Shot/media facts;
# those belong to its receipt and Shot entities, not this plan's current state.
_PLAN_STATE_FIELDS = frozenset({
    "project_id", "asset_plan_identity", "base_asset_plan_identity",
    "sources_changed", "execution_contract", "operation", "base_version_id",
    "item_count", "work_item_count", "notes_updated",
})


def _plan_state(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: deepcopy(item) for key, item in value.items() if key in _PLAN_STATE_FIELDS}


def asset_work_read_is_stale(
    tool_name: str, result: Mapping[str, JsonValue], *, known_versions: Mapping[str, int],
) -> bool:
    """Retire the whole old slice once its plan has an observed newer version.

    Proven unchanged items are retained separately by ``fold_asset_plan_items``.
    That proof never applies to the rest of a multi-object Tool response.
    """
    if tool_name != "artifact.get_asset_work_context":
        return False
    output = result.get("output")
    identity = output.get("asset_plan_identity") if isinstance(output, dict) else None
    if not isinstance(identity, dict):
        return False
    document_id, version = identity.get("document_id"), identity.get("version")
    return (
        isinstance(document_id, str) and isinstance(version, int)
        and not isinstance(version, bool) and version < known_versions.get(document_id, version)
    )


def fold_asset_plan_items(
    entities: dict[tuple[str, str], EntityT],
    turn: SpecialistTurnRecord, *, read_scope_key: str,
    read_scope: Mapping[str, JsonValue], known_versions: dict[str, int], entity_type: type[EntityT],
) -> None:
    result = turn.result
    if not isinstance(result, dict) or result.get("status") != "SUCCEEDED":
        return
    output = result.get("output")
    if not isinstance(output, dict):
        return
    identity = output.get("asset_plan_identity")
    if not isinstance(identity, dict):
        return
    document_id, version = identity.get("document_id"), identity.get("version")
    if not isinstance(document_id, str) or not isinstance(version, int):
        return
    previous_version = known_versions.get(document_id, 0)
    if version >= previous_version:
        receipt_value = result.get("receipt")
        saved_state = receipt_value.get("current_state") if isinstance(receipt_value, dict) else None
        prior = entities.get(("asset_plan_state", document_id))
        state_value: dict[str, JsonValue] = (
            _plan_state(prior.state) if prior is not None and prior.state.get("asset_plan_identity") == identity
            else {"asset_plan_identity": deepcopy(identity)}
        )
        if isinstance(saved_state, dict):
            state_value.update(_plan_state(saved_state))
        elif isinstance(output.get("execution_contract"), dict):
            state_value["execution_contract"] = deepcopy(output["execution_contract"])
        entities[("asset_plan_state", document_id)] = entity_type(
            kind="asset_plan_state", id=document_id, source_tool=turn.tool_name,
            source_sequence=turn.sequence, state=state_value,
        )
        # A compact historical document read must not keep advertising its old
        # current_version_id after a newer exact write/read has been observed.
        entities.pop(("production_document", document_id), None)
    if turn.tool_name in ASSET_PLAN_WRITE_TOOLS:
        known_versions[document_id] = max(version, known_versions.get(document_id, 0))
        receipt = result.get("receipt")
        state = receipt.get("current_state") if isinstance(receipt, dict) else None
        changed = receipt.get("changed_ids") if isinstance(receipt, dict) else None
        base = state.get("base_asset_plan_identity") if isinstance(state, dict) else None
        ids = changed.get("changed_item_ids") if isinstance(changed, dict) else None
        removed = changed.get("removed_item_ids") if isinstance(changed, dict) else None
        can_preserve = (turn.tool_name in LOCAL_ASSET_PLAN_WRITE_TOOLS and isinstance(state, dict)
                        and state.get("sources_changed") is False and isinstance(base, dict)
                        and isinstance(ids, list) and isinstance(removed, list))
        affected = {item for values in (ids, removed) if isinstance(values, list)
                    for item in values if isinstance(item, str)}
        for key, entity in list(entities.items()):
            if entity.kind != "asset_plan_item":
                continue
            source = entity.state.get("asset_plan_identity")
            if not isinstance(source, dict) or source.get("document_id") != document_id:
                continue
            valid_through = entity.state.get("unchanged_through_asset_plan_identity", source)
            if not can_preserve or valid_through != base or entity.id in affected:
                entities.pop(key)
            else:
                # Original source identity/body are unchanged. This is evidence
                # of equality through the local write, not a relabelled snapshot.
                entity.state["unchanged_through_asset_plan_identity"] = deepcopy(identity)
        return
    if version > previous_version:
        # An index can also reveal another writer's version. No observed delta
        # proves old item slices unchanged through that version.
        for key, entity in list(entities.items()):
            source = entity.state.get("asset_plan_identity")
            if entity.kind == "asset_plan_item" and isinstance(source, dict) and source.get("document_id") == document_id:
                entities.pop(key)
    if turn.tool_name != "artifact.get_asset_work_context":
        known_versions[document_id] = max(version, previous_version)
        return
    items = output.get("asset_plan_items")
    if not isinstance(items, list) or version < previous_version:
        return
    known_versions[document_id] = version
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        item_id = cast(str, item["id"])
        entities[("asset_plan_item", read_scope_key + ":" + item_id)] = entity_type(
            kind="asset_plan_item", id=item_id, source_tool=turn.tool_name,
            source_sequence=turn.sequence, state={
                "asset_plan_identity": deepcopy(identity), "read_scope": deepcopy(dict(read_scope)),
                "item": deepcopy(item),
            },
        )
