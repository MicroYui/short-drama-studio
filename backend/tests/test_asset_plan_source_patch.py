"""Newly split shots and their source can be saved in one local asset patch."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from short_drama.models import AssetKind, ProductionDocumentOrigin
from short_drama.production_contracts import (
    AssetPlanCreateRequest, AssetPlanDisposition, AssetPlanFieldPatch,
    AssetPlanItem, AssetPlanPatchRequest, StoryboardMutationResponse,
)
from short_drama.service import WorkflowConflictError
from tests.test_asset_plan_runtime import _asset_plan, _service, _setup_story_truth
from tests.test_storyboard_local_edits import _request


def test_current_shot_source_error_explains_one_atomic_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pid, spec, untyped_board = _setup_story_truth(service)
    board = cast(StoryboardMutationResponse, untyped_board)
    plan = service.create_asset_plan(pid, AssetPlanCreateRequest(
        asset_plan=_asset_plan(service, pid, spec=spec, storyboard=board),
        origin=ProductionDocumentOrigin.AGENT,
    ))
    split = service.patch_storyboard(pid, _request(board), current_content_edit=True)
    new_shot_id = split.storyboard.shots[1].id
    item = AssetPlanItem(
        id="item-new-evidence", disposition=AssetPlanDisposition.TEXT_ONLY,
        asset_kind=AssetKind.REFERENCE, name="New evidence shot preparation",
        serves_shot_ids=[new_shot_id], serves_clip_group_ids=[split.storyboard.clip_groups[0].id],
        rationale="The current saved split needs an evidence image.", work_items=[],
    )
    request = AssetPlanPatchRequest(base_version_id=plan.version.id, upsert_items=[item],
                                   origin=ProductionDocumentOrigin.AGENT)
    with pytest.raises(WorkflowConflictError) as failure:
        service.patch_asset_plan(pid, request)
    assert split.version.id in str(failure.value)
    assert '"path": "/storyboard_version_id"' in str(failure.value)
    assert "source_artifact_refs records provenance" in str(failure.value)
    assert service.get_asset_plan(pid).version.id == plan.version.id
    request.field_patches = [AssetPlanFieldPatch(op="replace", path="/storyboard_version_id", value=split.version.id)]
    saved = service.patch_asset_plan(pid, request)
    assert saved.asset_plan.storyboard_version_id == split.version.id
    assert saved.asset_plan.items[-1] == item
    assert saved.asset_plan.items[:-1] == plan.asset_plan.items


def test_unknown_shot_is_still_rejected_without_a_source_guess(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    plan = service.create_asset_plan(pid, AssetPlanCreateRequest(
        asset_plan=_asset_plan(service, pid, spec=spec, storyboard=board),
        origin=ProductionDocumentOrigin.AGENT,
    ))
    item = AssetPlanItem(
        id="item-imaginary", disposition=AssetPlanDisposition.TEXT_ONLY,
        asset_kind=AssetKind.REFERENCE, name="A shot that was never saved",
        serves_shot_ids=["shot-not-saved"], serves_clip_group_ids=[],
        rationale="This is not a saved object.", work_items=[],
    )
    with pytest.raises(WorkflowConflictError, match="unknown Storyboard shots: shot-not-saved"):
        service.patch_asset_plan(pid, AssetPlanPatchRequest(
            base_version_id=plan.version.id, upsert_items=[item], origin=ProductionDocumentOrigin.AGENT,
        ))
    assert service.get_asset_plan(pid).version.id == plan.version.id
