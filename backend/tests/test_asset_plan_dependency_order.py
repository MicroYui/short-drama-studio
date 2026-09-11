"""Display order must not replace the AssetPlan's explicit dependency graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.asset_plan_reconciler import assess_asset_plan_execution_contract
from short_drama.models import ProductionDocumentOrigin
from short_drama.production_contracts import (
    AssetPlanContent,
    AssetPlanCreateRequest,
    AssetPlanExecutionStatus,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from tests.test_asset_plan_runtime import _asset_plan, _service, _setup_story_truth


def _reversed_plan(
    tmp_path: Path, *, completed_subject: bool = False,
) -> tuple[ShortDramaService, str, AssetPlanContent]:
    service = _service(tmp_path)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character = plan.items[0].model_copy(deep=True)
    character.work_items.reverse()
    if not completed_subject:
        character.completed_asset_id = None
        character.completed_asset_version_id = None
    plan.items[0] = character
    return service, project_id, plan


@pytest.mark.parametrize("completed_subject", [False, True])
def test_later_canonical_saves_and_resolves_ready_work_without_reordering(
    tmp_path: Path, completed_subject: bool,
) -> None:
    service, project_id, plan = _reversed_plan(
        tmp_path, completed_subject=completed_subject,
    )
    before = plan.model_dump(mode="json")
    # Exercise current schema validation and the actual service persistence path.
    validated = AssetPlanContent.model_validate(before)
    saved = service.create_asset_plan(
        project_id, AssetPlanCreateRequest(
            asset_plan=validated, origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    expected_order = ["work-linxia-derived", "work-linxia-master"]
    assert [work.id for work in saved.asset_plan.items[0].work_items] == expected_order
    assert plan.model_dump(mode="json") == before
    assert assess_asset_plan_execution_contract(saved.asset_plan).issues == []

    index = service.get_asset_plan_index(project_id)
    assert [work.id for work in index.items[0].work_items] == expected_order
    assert index.items[0].work_items[0].status == AssetPlanExecutionStatus.READY
    context = service.get_asset_work_context(
        project_id, item_ids=[], work_item_ids=[expected_order[0]],
        shot_ids=[], clip_group_ids=[],
    )
    assert context.dependency_work_item_ids == [expected_order[1]]
    assert [work.id for work in context.asset_plan_items[0].work_items] == expected_order
    canonical = saved.asset_plan.items[0].work_items[1]
    assert any(
        media.asset_id == canonical.completed_asset_id
        and media.asset_version_id == canonical.completed_asset_version_id
        for media in context.media_versions
    )


@pytest.mark.parametrize("missing", ["lineage", "dependency"])
def test_later_canonical_still_requires_real_lineage_and_explicit_dependency(
    tmp_path: Path, missing: str,
) -> None:
    service, project_id, plan = _reversed_plan(tmp_path)
    supplemental = plan.items[0].work_items[0]
    assert supplemental.reference_pack_intent is not None
    if missing == "lineage":
        supplemental.reference_pack_intent.lineage_root = None
    else:
        supplemental.dependency_ids = []

    review = assess_asset_plan_execution_contract(plan)
    assert [issue.code.value for issue in review.issues] == [
        "SUPPLEMENTAL_LINEAGE_REQUIRES_RECONCILIATION",
    ]
    # Exercise this service boundary independently of the schema-level graph
    # validation, which also rejects undeclared reference dependencies.
    with pytest.raises(WorkflowConflictError, match=f"as (?:its |a )?{missing}"):
        service._validate_asset_plan(service.get_project(project_id).document, plan)
    assert service.get_project(project_id).document.asset_plan_document_id is None
