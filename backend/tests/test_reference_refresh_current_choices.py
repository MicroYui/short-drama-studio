"""Automatic refresh may fill legacy facts, never replace explicit prose choices."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.asset_reference_truth import AssetReferenceTruthError, resolve_current_asset_media
from short_drama.h3_route_planner import generation_unit_media_candidates
from short_drama.models import ApprovalStatus, Freshness, ReferenceBinding, ReferenceRole, ShotReferenceCue, ShotTimelineBeat
from short_drama.production_planner import refresh_lightweight_reference_plans
from short_drama.studio_references import project_legacy_references
from short_drama.tools.contracts import ToolCallStatus
from tests.test_h3_explicit_reference_selection import selection_fixture
from tests.test_h3_subject_reference_truth import _import_image, _service


def test_refresh_exact_multiple_states_editorial_unbound_and_other_group_survive(tmp_path: Path) -> None:
    service, _fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 4)
    shot = document.shots[0]
    asset = document.assets[0]
    shot.references = [shot.references[0]]
    shot.timeline_beats = [ShotTimelineBeat(start_seconds=0, end_seconds=shot.duration_seconds,
        visual=shot.visual, framing=shot.framing, camera=shot.camera, action="Keep the selected state", performance="Still",
        dialogue_line_ids=shot.dialogue_line_ids,
        references=[ShotReferenceCue(subject_asset_id=asset.id,
        asset_version_id=media[1].asset_version_id, alias="after", purpose="After state")])]
    other = document.assets[1]
    third = document.assets[2]
    shot.references.extend([
        ShotReferenceCue(subject_asset_id=other.id, asset_version_id=other.current_version_id,
                         alias="editorial", usage="EDITORIAL", purpose="Only cutaway"),
        ShotReferenceCue(subject_asset_id=third.id, asset_version_id=None, alias="unbound", purpose="Not chosen yet"),
    ])
    target = next(plan for plan in document.reference_plan if plan.shot_id == shot.id)
    target.bindings = [ReferenceBinding(kind=asset.kind, target_id=media[-1].asset_version_id, purpose="Wrong sibling"),
        ReferenceBinding(kind=other.kind, target_id=other.current_version_id, purpose="Old automatic state"),
        ReferenceBinding(kind=third.kind, target_id=third.current_version_id, purpose="Old automatic state")]
    before = document.model_copy(deep=True)
    assert refresh_lightweight_reference_plans(document, shot_ids={shot.id})
    assert [binding.target_id for binding in target.bindings] == [media[0].asset_version_id, media[1].asset_version_id]
    assert document.shots == before.shots and document.assets == before.assets
    assert [plan for plan in document.reference_plan if plan.shot_id != shot.id] == [plan for plan in before.reference_plan if plan.shot_id != shot.id]
    assert not refresh_lightweight_reference_plans(document, shot_ids={shot.id})
    # The old page fallback must not import the sibling from the stale plan either.
    projected = before.model_copy(deep=True)
    project_legacy_references(projected)
    assert [cue.asset_version_id for cue in projected.shots[0].references] == [cue.asset_version_id for cue in before.shots[0].references]
    assert service.repository.list_tasks(document.project.id) == [] and unit.shot_ids[0] == shot.id


@pytest.mark.parametrize("cross_owner", [False, True])
def test_registry_original_draft_lineage_remains_valid_after_reopen(tmp_path: Path, cross_owner: bool) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    subject = document.assets[0]
    root = _import_image(service, tmp_path, project_id=document.project.id, asset_id=subject.id, name="real-root", color="white")
    owner = document.assets[1] if cross_owner else subject
    selected = _import_image(service, tmp_path, project_id=document.project.id, asset_id=owner.id, name="selected-state", color="gray")
    current = service.repository.get_project(document.project.id)
    assert current is not None
    current.shots[0].references = [ShotReferenceCue(subject_asset_id=subject.id, asset_version_id=selected,
        alias="state", purpose="Exact selected state")]
    service.repository.save_project(current)
    arguments = {"project_id": document.project.id, "request": {"subject_asset_id": subject.id,
        "asset_version_id": selected, "role": ReferenceRole.CHARACTER_PROFILE.value,
        "lineage_root_version_id": root, "description": "Actual view retaining its real lineage"}}
    registered = service.tool_registry.invoke("artifact.add_reference_entry", arguments)
    assert registered.status == ToolCallStatus.SUCCEEDED, registered.error
    persisted = service.repository.get_project(document.project.id)
    assert persisted is not None
    reopened = _service(tmp_path).repository.get_project(document.project.id)
    assert reopened == persisted
    resolved_owner, version = resolve_current_asset_media(reopened, asset_version_id=selected, subject_asset_id=subject.id)
    assert resolved_owner.id == owner.id and version.approval_status == ApprovalStatus.DRAFT
    assert reopened == persisted
    candidates, warnings = generation_unit_media_candidates(reopened, [reopened.shots[0]])
    assert any(item.media_input.asset_version_id == selected for item in candidates), warnings
    assert not any(selected in warning for warning in warnings)
    assert next(entry for pack in reopened.reference_packs for entry in pack.entries if entry.asset_version_id == selected).lineage_root_version_id == root
    for failure in ("rejected", "stale", "missing", "wrong-root"):
        broken = reopened.model_copy(deep=True)
        selected_version = next(v for asset in broken.assets for v in asset.versions if v.id == selected)
        if failure == "rejected":
            selected_version.approval_status = ApprovalStatus.REJECTED
        elif failure == "stale":
            selected_version.freshness = Freshness.STALE
        elif failure == "missing":
            selected_version.local_path = str(tmp_path / "missing.png")
        else:
            entry = next(entry for pack in broken.reference_packs for entry in pack.entries if entry.asset_version_id == selected)
            entry.lineage_root_version_id = broken.assets[2].current_version_id
        with pytest.raises(AssetReferenceTruthError):
            resolve_current_asset_media(broken, asset_version_id=selected, subject_asset_id=subject.id)
