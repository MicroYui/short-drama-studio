"""Small acknowledgements derived from the exact persisted AssetPlan result."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.asset_plan_reconciler import assess_asset_plan_execution_contract
from short_drama.production_contracts import AssetPlanContent, AssetPlanMutationResponse
if TYPE_CHECKING:
    from short_drama.tools.contracts import ToolWriteReceipt


ASSET_PLAN_WRITE_TOOLS = frozenset({
    "artifact.create_asset_plan", "artifact.replace_asset_plan", "artifact.patch_asset_plan",
    "artifact.patch_asset_plan_fields", "artifact.reconcile_asset_plan_execution_contract",
    "artifact.retry_asset_plan_attempt",
})
LOCAL_ASSET_PLAN_WRITE_TOOLS = frozenset({
    "artifact.patch_asset_plan", "artifact.patch_asset_plan_fields",
    "artifact.reconcile_asset_plan_execution_contract",
})


def asset_plan_write_receipt(
    result: AssetPlanMutationResponse, refs: Sequence[ArtifactRef],
) -> ToolWriteReceipt:
    """Compare actual saved values with the actual parent, including merged writes."""
    from short_drama.tools.contracts import ToolWriteReceipt

    parent = next((version for version in result.document.versions
                   if version.id == result.version.parent_version_id), None)
    before = None if parent is None else AssetPlanContent.model_validate(parent.payload)
    previous_items = {} if before is None else {item.id: item for item in before.items}
    items = {item.id: item for item in result.asset_plan.items}
    old_work = {work.id: work for item in previous_items.values() for work in item.work_items}
    work = {work.id: work for item in items.values() for work in item.work_items}
    state: dict[str, JsonValue] = {
        "project_id": result.document.project_id,
        "asset_plan_identity": {"document_id": result.document.id,
            "version_id": result.version.id, "version": result.version.version},
        "base_asset_plan_identity": None if parent is None else {
            "document_id": result.document.id, "version_id": parent.id, "version": parent.version},
        "sources_changed": before is None or (
            before.model_dump(exclude={"items", "notes"})
            != result.asset_plan.model_dump(exclude={"items", "notes"})),
        "execution_contract": assess_asset_plan_execution_contract(result.asset_plan).model_dump(mode="json"),
    }
    return ToolWriteReceipt(artifact_refs=list(refs), current_state=state, changed_ids={
        "changed_item_ids": [key for key, item in items.items() if previous_items.get(key) != item],
        "removed_item_ids": [key for key in previous_items if key not in items],
        "changed_work_item_ids": [key for key, item in work.items() if old_work.get(key) != item],
        "removed_work_item_ids": [key for key in old_work if key not in work],
    })


def compatible_asset_plan_receipt(
    tool_name: str, result: Mapping[str, JsonValue],
) -> JsonValue:
    """Recover facts already present in old successful output; never invent a delta.

    Historical summaries lack the actual parent and field-patch outcome. They
    recover the exact new version and acknowledged IDs, but cannot certify that
    an unrelated item survived an unseen concurrent write.
    """
    receipt = result.get("receipt")
    if tool_name not in ASSET_PLAN_WRITE_TOOLS or result.get("status") != "SUCCEEDED":
        return receipt
    output = result.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("asset_plan_identity"), dict):
        return receipt
    copied = deepcopy(receipt) if isinstance(receipt, dict) else {
        "operation_completed": True, "artifact_refs": deepcopy(result.get("artifact_refs", [])),
    }
    state_value = copied.get("current_state")
    state = cast(dict[str, JsonValue], state_value) if isinstance(state_value, dict) else {}
    state.setdefault("asset_plan_identity", deepcopy(output["asset_plan_identity"]))
    for key in ("project_id", "operation", "base_version_id", "item_count", "work_item_count", "notes_updated", "execution_contract"):
        if key in output:
            state.setdefault(key, deepcopy(output[key]))
    copied["current_state"] = state
    changes_value = copied.get("changed_ids")
    changes = cast(dict[str, JsonValue], changes_value) if isinstance(changes_value, dict) else {}
    for key, value in output.items():
        if key.endswith("_ids") and isinstance(value, list) and all(isinstance(item, str) for item in value):
            changes.setdefault(key, deepcopy(value))
    copied["changed_ids"] = changes
    return copied
