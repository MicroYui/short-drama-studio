"""H3 media resolution follows canonical and ReferencePack lineage truth."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.image_models import (
    AssetImportLocalRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import AssetKind, ReferenceBinding, ReferenceRole
from short_drama.reference_planner import (
    ReferencePackEntryCreateRequest,
    ReferencePlanPreviewRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _import_image(
    service: ShortDramaService,
    tmp_path: Path,
    *,
    project_id: str,
    asset_id: str,
    name: str,
    color: str,
) -> str:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (256, 256), color).save(path)
    return service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(local_path=str(path), descriptor=name),
    ).version.id


def _subject_input(subject_asset_id: str, version_id: str) -> H3MediaInput:
    return H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url="https://ignored.example/reference.png"),
        asset_version_id=version_id,
        subject_asset_id=subject_asset_id,
    )


def test_subject_image_accepts_current_lineage_and_rejects_it_after_canonical_change(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    subject = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        name="canonical-v1",
        color="#d6d3d1",
    )
    service.review_asset_version(
        project_id,
        subject.id,
        canonical_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="current canonical",
        ),
    )
    profile_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        name="profile-v1",
        color="#bfdbfe",
    )
    service.review_asset_version(
        project_id,
        subject.id,
        profile_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
            notes="current lineage profile",
        ),
    )
    service.add_reference_pack_entry(
        project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id,
            asset_version_id=profile_id,
            lineage_root_version_id=canonical_id,
            role=ReferenceRole.CHARACTER_PROFILE,
            description="profile derived from canonical v1",
        ),
    )
    persisted_route_input = H3MediaInput.model_validate(
        _subject_input(subject.id, profile_id).model_dump(mode="json")
    )
    current = service.repository.get_project(project_id)
    assert current is not None
    assert service._resolve_approved_asset_inputs(
        current,
        [persisted_route_input],
    )[0].source.local_path is not None

    canonical_v2_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        name="canonical-v2",
        color="#fef3c7",
    )
    service.review_asset_version(
        project_id,
        subject.id,
        canonical_v2_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="replacement canonical",
        ),
    )
    changed = service.repository.get_project(project_id)
    assert changed is not None
    with pytest.raises(WorkflowConflictError, match="current valid ReferencePack"):
        service._resolve_approved_asset_inputs(changed, [persisted_route_input])


def test_subject_image_keeps_independent_reference_pack_entry_across_canonicals(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    subject = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    independent_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        name="independent-outfit",
        color="#bbf7d0",
    )
    service.review_asset_version(
        project_id,
        subject.id,
        independent_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
            notes="independent approved outfit",
        ),
    )
    service.add_reference_pack_entry(
        project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id,
            asset_version_id=independent_id,
            role=ReferenceRole.CHARACTER_BODY,
            description="independent outfit reference",
        ),
    )
    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        name="later-canonical",
        color="#fecaca",
    )
    service.review_asset_version(
        project_id,
        subject.id,
        canonical_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="later canonical",
        ),
    )
    document = service.repository.get_project(project_id)
    assert document is not None
    resolved = service._resolve_approved_asset_inputs(
        document,
        [_subject_input(subject.id, independent_id)],
    )
    assert resolved[0].asset_version_id == independent_id
    explicit_independent = _subject_input(subject.id, independent_id).model_copy(
        update={"subject_asset_id": None}
    )
    assert service._resolve_approved_asset_inputs(
        document,
        [explicit_independent],
    )[0].asset_version_id == independent_id


def test_reference_binding_rejects_asset_kind_mismatch_during_preview(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    shot = document.shots[0]
    character = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    plan.bindings = [
        ReferenceBinding(
            kind=AssetKind.LOCATION,
            target_id=character.versions[0].id,
            purpose="intentionally mismatched binding",
        )
    ]
    service.repository.save_project(document)
    with pytest.raises(WorkflowConflictError, match="does not match AssetVersion"):
        service.preview_shot_reference_plan(
            document.project.id,
            shot.id,
            ReferencePlanPreviewRequest(),
        )
