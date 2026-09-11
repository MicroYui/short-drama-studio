"""Asset catalog and coverage semantics exposed to the production Agent."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
)
from short_drama.asset_inspection import AssetVersionInspectionRequest
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import (
    AssetKind,
    CreateProjectRequest,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRequirement,
    ReferenceRole,
    SourceMode,
)
from short_drama.reference_planner import ReferencePackEntryCreateRequest
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
    label: str,
    color: str,
) -> str:
    path = tmp_path / f"{label}.png"
    Image.new("RGB", (768, 1344), color).save(path)
    return service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(local_path=str(path), descriptor=label),
    ).version.id


def _approve(
    service: ShortDramaService,
    *,
    project_id: str,
    asset_id: str,
    version_id: str,
    purpose: AssetReviewPurpose,
) -> None:
    service.review_asset_version(
        project_id,
        asset_id,
        version_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=purpose,
            notes=f"approved as {purpose.value}",
        ),
    )


def test_coverage_preserves_agent_choices_and_treats_optional_gaps_as_advisory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    character = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    for plan in project.document.reference_plan:
        plan.requirements = []
    project.document.reference_plan[0].requirements = [
        ReferenceRequirement(
            id="optional-character-identity",
            subject_asset_id=character.id,
            acceptable_roles=[
                ReferenceRole.CHARACTER_FACE,
                ReferenceRole.CHARACTER_PROFILE,
            ],
            preferred_angles=[
                ReferenceAngle.FRONT,
                ReferenceAngle.LEFT_PROFILE,
            ],
            preferred_framings=[
                ReferenceFraming.FACE_CLOSE_UP,
                ReferenceFraming.MEDIUM,
            ],
            required_tags=["identity", "school-uniform"],
            purpose="Optional reusable identity coverage for later close shots.",
            required=False,
        )
    ]
    service.repository.save_project(project.document)

    without_master = service.inspect_asset_coverage(project_id)
    assert without_master.ready is True
    assert len(without_master.missing_canonical_assets) == 1
    assert without_master.missing_canonical_assets[0].required is False

    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=character.id,
        label="coverage-canonical",
        color="#d6d3d1",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=character.id,
        version_id=canonical_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )
    advisory = service.inspect_asset_coverage(project_id)
    assert advisory.ready is True
    assert advisory.missing_canonical_assets == []
    assert len(advisory.missing_views) == 1
    need = advisory.missing_views[0]
    assert need.required is False
    assert need.acceptable_roles == [
        ReferenceRole.CHARACTER_FACE,
        ReferenceRole.CHARACTER_PROFILE,
    ]
    assert need.preferred_angles == [
        ReferenceAngle.FRONT,
        ReferenceAngle.LEFT_PROFILE,
    ]
    assert need.preferred_framings == [
        ReferenceFraming.FACE_CLOSE_UP,
        ReferenceFraming.MEDIUM,
    ]
    assert need.required_tags == ["identity", "school-uniform"]

    document = service.repository.get_project(project_id)
    assert document is not None
    document.reference_plan[0].requirements[0].required = True
    service.repository.save_project(document)
    required = service.inspect_asset_coverage(project_id)
    assert required.ready is False
    assert required.missing_views[0].required is True


def test_coverage_readiness_checks_required_gaps_beyond_display_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    character = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=character.id,
        label="coverage-limit-canonical",
        color="#e7e5e4",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=character.id,
        version_id=canonical_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )

    document = service.repository.get_project(project_id)
    assert document is not None
    for plan in document.reference_plan:
        plan.requirements = []
    document.reference_plan[0].requirements = [
        ReferenceRequirement(
            id=f"optional-gap-{index:03d}",
            subject_asset_id=character.id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            required_tags=[f"optional-{index:03d}"],
            purpose=f"Optional coverage suggestion {index}.",
            required=False,
        )
        for index in range(500)
    ]
    document.reference_plan[0].requirements.append(
        ReferenceRequirement(
            id="required-gap-after-display-limit",
            subject_asset_id=character.id,
            acceptable_roles=[ReferenceRole.CHARACTER_PROFILE],
            required_tags=["required-after-limit"],
            purpose="This required gap must determine readiness even when not displayed.",
            required=True,
        )
    )
    service.repository.save_project(document)

    coverage = service.inspect_asset_coverage(project_id)
    assert len(coverage.missing_views) == 500
    assert all(not item.required for item in coverage.missing_views)
    assert coverage.ready is False


def test_catalog_candidate_identifies_supplement_owner_and_imports_by_pack_kind(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = service.initialize_fixture()
    source_project_id = source.document.project.id
    subject = next(
        asset for asset in source.document.assets if asset.kind == AssetKind.CHARACTER
    )
    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=source_project_id,
        asset_id=subject.id,
        label="catalog-subject-canonical",
        color="#f5f5f4",
    )
    _approve(
        service,
        project_id=source_project_id,
        asset_id=subject.id,
        version_id=canonical_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )

    holder = service.create_asset(
        source_project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Profile source holder",
            descriptor="An independently stored profile reference.",
        ),
    ).asset
    supplemental_id = _import_image(
        service,
        tmp_path,
        project_id=source_project_id,
        asset_id=holder.id,
        label="catalog-profile-supplement",
        color="#bfdbfe",
    )
    _approve(
        service,
        project_id=source_project_id,
        asset_id=holder.id,
        version_id=supplemental_id,
        purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
    )
    service.add_reference_pack_entry(
        source_project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id,
            asset_version_id=supplemental_id,
            lineage_root_version_id=canonical_id,
            role=ReferenceRole.CHARACTER_PROFILE,
            angle=ReferenceAngle.LEFT_PROFILE,
            framing=ReferenceFraming.MEDIUM,
            tags=["identity", "profile"],
            description="Approved left profile for the character subject.",
        ),
    )

    target = service.create_project(
        CreateProjectRequest(
            name="Catalog target",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A short school story for catalog reuse.",
        )
    )
    target_character = next(
        asset for asset in target.document.assets if asset.kind == AssetKind.CHARACTER
    )
    result = service.search_asset_catalog(
        AssetCatalogSearchRequest(
            query=subject.name,
            kinds=[AssetKind.CHARACTER],
            roles=[ReferenceRole.CHARACTER_PROFILE],
            include_current_project=False,
            include_cross_project=True,
        ),
        current_project_id=target.document.project.id,
    )
    candidate = next(
        item for item in result.candidates if item.asset_version_id == supplemental_id
    )
    assert candidate.subject_asset_id == subject.id
    assert candidate.source_asset_id == holder.id
    assert candidate.asset_kind == AssetKind.CHARACTER

    imported = service.import_catalog_asset(
        target.document.project.id,
        AssetCatalogImportRequest(
            source_project_id=candidate.source_project_id,
            source_asset_id=candidate.source_asset_id,
            source_version_id=candidate.asset_version_id,
            target_asset_id=target_character.id,
            descriptor="Imported approved profile reference.",
        ),
    )
    assert imported.asset.kind == AssetKind.CHARACTER
    assert imported.version.source is not None
    assert f"/assets/{holder.id}/" in (imported.version.source.source_uri or "")
    assert [
        reference.model_dump(mode="json")
        for reference in imported.version.derivation_references
    ] == [
        {
            "asset_version_id": supplemental_id,
            "source_project_id": source_project_id,
            "reference_role": "SUBJECT_IDENTITY",
            "reference_authority": "SUPPORTING",
            "purpose": "Imported approved profile reference.",
            "subject_alias": target_character.name,
        }
    ]


def test_explicit_target_can_reuse_user_source_intake_reference(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    target_character = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    source = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="用户上传的完整主角卡",
            descriptor="用户明确指定为主角，画面已覆盖当前需要的标准视图。",
        ),
    ).asset
    source_version_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=source.id,
        label="uploaded-complete-character-card",
        color="#dbeafe",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=source.id,
        version_id=source_version_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )

    imported = service.import_catalog_asset(
        project_id,
        AssetCatalogImportRequest(
            source_project_id=project_id,
            source_asset_id=source.id,
            source_version_id=source_version_id,
            target_asset_id=target_character.id,
            descriptor="精确复用用户完整角色卡，不重新生成身份。",
        ),
    )

    assert imported.asset.kind == AssetKind.CHARACTER
    assert imported.version.source is not None
    assert imported.version.source.source_uri is not None
    assert f"/assets/{source.id}/" in imported.version.source.source_uri
    assert [
        reference.model_dump(mode="json")
        for reference in imported.version.derivation_references
    ] == [
        {
            "asset_version_id": source_version_id,
            "source_project_id": project_id,
            "reference_role": "SUBJECT_IDENTITY",
            "reference_authority": "AUTHORITATIVE",
            "purpose": "精确复用用户完整角色卡，不重新生成身份。",
            "subject_alias": target_character.name,
        }
    ]


def test_cross_project_reference_clone_keeps_provenance_without_dangling_lineage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_project = service.create_project(
        CreateProjectRequest(
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="用户素材库项目",
            target_duration_seconds=30.0,
        ),
        project_id="source-reference-project",
    )
    source = service.create_asset(
        source_project.document.project.id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="用户上传的主角照片",
            descriptor="用户声明为主角的身份参考",
        ),
    ).asset
    source_version_id = _import_image(
        service,
        tmp_path,
        project_id=source_project.document.project.id,
        asset_id=source.id,
        label="cross-project-user-protagonist",
        color="#bfdbfe",
    )
    _approve(
        service,
        project_id=source_project.document.project.id,
        asset_id=source.id,
        version_id=source_version_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )

    target_project = service.initialize_fixture()
    target = next(
        asset
        for asset in target_project.document.assets
        if asset.kind == AssetKind.CHARACTER
    )
    imported = service.import_catalog_asset(
        target_project.document.project.id,
        AssetCatalogImportRequest(
            source_project_id=source_project.document.project.id,
            source_asset_id=source.id,
            source_version_id=source_version_id,
            target_asset_id=target.id,
            descriptor="跨项目精确复用用户主角照片",
        ),
    )

    assert [
        reference.model_dump(mode="json")
        for reference in imported.version.derivation_references
    ] == [
        {
            "asset_version_id": source_version_id,
            "source_project_id": source_project.document.project.id,
            "reference_role": "SUBJECT_IDENTITY",
            "reference_authority": "AUTHORITATIVE",
            "purpose": "跨项目精确复用用户主角照片",
            "subject_alias": target.name,
        }
    ]
    assert imported.version.source is not None
    assert imported.version.source.source_uri is not None
    assert source_version_id in imported.version.source.source_uri
    assert service.asset_inspection_reference_version_refs(
        target_project.document.project.id,
        target.id,
        imported.version.id,
        AssetVersionInspectionRequest(),
    ) == [(source_project.document.project.id, source_version_id)]


def test_cross_project_character_master_clone_keeps_authoritative_lineage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_project = service.initialize_fixture()
    source_project_id = source_project.document.project.id
    source_character = next(
        asset
        for asset in source_project.document.assets
        if asset.kind == AssetKind.CHARACTER
    )
    source_version_id = _import_image(
        service,
        tmp_path,
        project_id=source_project_id,
        asset_id=source_character.id,
        label="cross-project-character-master",
        color="#fde68a",
    )
    _approve(
        service,
        project_id=source_project_id,
        asset_id=source_character.id,
        version_id=source_version_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )

    target_project = service.create_project(
        CreateProjectRequest(
            name="Character lineage target",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A compact school story.",
        )
    )
    target_character = next(
        asset
        for asset in target_project.document.assets
        if asset.kind == AssetKind.CHARACTER
    )
    imported = service.import_catalog_asset(
        target_project.document.project.id,
        AssetCatalogImportRequest(
            source_project_id=source_project_id,
            source_asset_id=source_character.id,
            source_version_id=source_version_id,
            target_asset_id=target_character.id,
            descriptor="Reuse the approved character master exactly.",
        ),
    )

    assert [
        reference.model_dump(mode="json")
        for reference in imported.version.derivation_references
    ] == [
        {
            "asset_version_id": source_version_id,
            "source_project_id": source_project_id,
            "reference_role": "SUBJECT_IDENTITY",
            "reference_authority": "AUTHORITATIVE",
            "purpose": "Reuse the approved character master exactly.",
            "subject_alias": target_character.name,
        }
    ]
    assert service.asset_inspection_reference_version_refs(
        target_project.document.project.id,
        target_character.id,
        imported.version.id,
        AssetVersionInspectionRequest(),
    ) == [(source_project_id, source_version_id)]


def test_canonical_pack_role_uses_an_explicitly_selected_subject_image(
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
        label="truthful-canonical",
        color="#fef3c7",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=subject.id,
        version_id=canonical_id,
        purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )
    same_asset_supplement_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=subject.id,
        label="same-asset-supplement",
        color="#fecaca",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=subject.id,
        version_id=same_asset_supplement_id,
        purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
    )
    other_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Other image holder",
            descriptor="Not the character's canonical image.",
        ),
    ).asset
    other_version_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=other_asset.id,
        label="other-asset-supplement",
        color="#bbf7d0",
    )
    _approve(
        service,
        project_id=project_id,
        asset_id=other_asset.id,
        version_id=other_version_id,
        purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
    )

    for fake_version_id in (other_version_id,):
        with pytest.raises(WorkflowConflictError, match="CANONICAL_MASTER"):
            service.add_reference_pack_entry(
                project_id,
                ReferencePackEntryCreateRequest(
                    subject_asset_id=subject.id,
                    asset_version_id=fake_version_id,
                    role=ReferenceRole.CANONICAL_MASTER,
                    description="Must not masquerade as the canonical master.",
                ),
            )

    selected_supplement = service.add_reference_pack_entry(
        project_id, ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id, asset_version_id=same_asset_supplement_id,
            role=ReferenceRole.CANONICAL_MASTER,
            description="Explicitly use this image of the same subject as a master.",
        ),
    )
    assert selected_supplement.entry.asset_version_id == same_asset_supplement_id

    truthful = service.add_reference_pack_entry(
        project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id,
            asset_version_id=canonical_id,
            role=ReferenceRole.CANONICAL_MASTER,
            description="The subject's actual current approved canonical image.",
        ),
    )
    assert truthful.entry.asset_version_id == canonical_id
    assert truthful.entry.lineage_root_version_id == canonical_id
