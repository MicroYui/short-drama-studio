"""A real planner's mixed work/media reference fails with a local, actionable repair."""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest

from short_drama.models import ProductionDocumentOrigin
from short_drama.production_contracts import AssetPlanCreateRequest
from short_drama.tools import ToolCallStatus
from tests.test_asset_plan_runtime import _asset_plan, _service, _setup_story_truth


@pytest.mark.parametrize("selected_source", ["work", "exact_media"])
def test_mixed_reference_repair_preserves_selected_source_and_other_work(
    tmp_path: Path, selected_source: str,
) -> None:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    created = service.create_asset_plan(pid, AssetPlanCreateRequest(
        asset_plan=_asset_plan(service, pid, spec=spec, storyboard=board, approve_sources=False),
        origin=ProductionDocumentOrigin.AGENT,
    ))
    item = created.asset_plan.items[0]
    master, derived = item.work_items
    work = derived.model_dump(mode="json")
    # This is the shape returned at real debug t28: both a completed work
    # selector and the exact media it produced are supplied on one input.
    reference = derived.ordered_references[0].model_dump(mode="json")
    reference.update(asset_id=master.completed_asset_id,
                     asset_version_id=master.completed_asset_version_id)
    work["ordered_references"] = [reference]
    arguments: dict[str, object] = {"project_id": pid, "request": {
        "base_version_id": created.version.id,
        "work_item_patches": [{"item_id": item.id, "upsert_work_items": [work]}],
        "origin": "AGENT",
    }}
    before = service.get_project(pid).document
    failed = service.tool_registry.invoke("artifact.patch_asset_plan", arguments)
    assert failed.status == ToolCallStatus.INVALID_ARGUMENTS
    assert failed.error is not None
    leaf = "/request/work_item_patches/0/upsert_work_items/0/ordered_references/0"
    issue = next(detail for detail in failed.error.details if detail.path == leaf)
    assert issue.identifiers[leaf] == {
        "source_work_item_id": master.id, "asset_id": master.completed_asset_id,
        "asset_version_id": master.completed_asset_version_id,
    }
    assert "omit source_work_item_id" in issue.message
    assert "dependency_ids" in issue.message
    assert service.get_project(pid).document == before

    repaired = deepcopy(arguments)
    request = cast(dict[str, object], repaired["request"])
    patches = cast(list[dict[str, object]], request["work_item_patches"])
    saved_work = cast(list[dict[str, object]], patches[0]["upsert_work_items"])[0]
    chosen = cast(list[dict[str, object]], saved_work["ordered_references"])[0]
    if selected_source == "work":
        chosen.pop("asset_id")
        chosen.pop("asset_version_id")
    else:
        chosen.pop("source_work_item_id")
    saved = service.tool_registry.invoke("artifact.patch_asset_plan", repaired)
    assert saved.status == ToolCallStatus.SUCCEEDED, saved.error
    current = service.get_asset_plan(pid).asset_plan
    actual = current.items[0].work_items[1]
    assert actual.dependency_ids == derived.dependency_ids
    assert actual.ordered_references[0].model_dump(mode="json", exclude_none=True) == {
        key: value for key, value in chosen.items() if value is not None
    }
    assert current.items[0].work_items[0] == master
    assert current.items[1:] == created.asset_plan.items[1:]
    assert service.list_image_tasks(pid).tasks == []

    schema = service.tool_registry.describe("artifact.patch_asset_plan").input_schema
    fields = json.loads(json.dumps(schema))["$defs"]["AssetPlanReference"]["properties"]
    assert "asset_id" in fields["source_work_item_id"]["description"]
    assert "dependency_ids" in fields["source_work_item_id"]["description"]
    assert "source_work_item_id" in fields["asset_version_id"]["description"]
