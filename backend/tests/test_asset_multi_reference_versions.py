"""Integration coverage for canonical masters and approved supplemental images."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.asset_catalog import AssetCatalogSearchRequest
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    Freshness,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRequirement,
    ReferenceRole,
)
from short_drama.reference_planner import (
    ReferencePackEntryCreateRequest,
    ReferencePlanPreviewRequest,
    ShotReferencePlanUpdateRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.visual_input_quality import (
    ReferenceVisualRerankRequest,
    build_reference_visual_candidates,
)


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
    source = tmp_path / f"{name}.png"
    Image.new("RGB", (768, 1344), color).save(source)
    return service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(local_path=str(source), descriptor=name),
    ).version.id


def test_character_keeps_multiple_approved_image_references(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    asset_id = f"{project_id}-asset-linxia"
    shot_id = project.document.shots[0].id

    canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=asset_id,
        name="character-canonical",
        color="#d6d3d1",
    )
    service.review_asset_version(
        project_id,
        asset_id,
        canonical_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="canonical identity master",
        ),
    )
    supplemental_specs = [
        ("face", "#fecaca"),
        ("profile", "#bfdbfe"),
        ("outfit", "#bbf7d0"),
    ]
    supplemental_ids: dict[str, str] = {}
    for name, color in supplemental_specs:
        version_id = _import_image(
            service,
            tmp_path,
            project_id=project_id,
            asset_id=asset_id,
            name=f"character-{name}",
            color=color,
        )
        reviewed = service.review_asset_version(
            project_id,
            asset_id,
            version_id,
            AssetReviewRequest(
                decision="APPROVED",
                purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                notes=f"approved {name} reference",
            ),
        )
        assert reviewed.asset.approved_version_id == canonical_id
        assert reviewed.version.approval_status == ApprovalStatus.APPROVED
        assert reviewed.version.freshness == Freshness.CURRENT
        supplemental_ids[name] = version_id

    entries = [
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=asset_id,
                asset_version_id=supplemental_ids["face"],
                lineage_root_version_id=canonical_id,
                role=ReferenceRole.CHARACTER_FACE,
                angle=ReferenceAngle.FRONT,
                framing=ReferenceFraming.FACE_CLOSE_UP,
                tags=["identity", "face"],
                description="frontal face identity reference",
            ),
        ).entry,
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=asset_id,
                asset_version_id=supplemental_ids["profile"],
                lineage_root_version_id=canonical_id,
                role=ReferenceRole.CHARACTER_PROFILE,
                angle=ReferenceAngle.LEFT_PROFILE,
                framing=ReferenceFraming.MEDIUM,
                tags=["identity", "profile"],
                description="left profile identity reference",
            ),
        ).entry,
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=asset_id,
                asset_version_id=supplemental_ids["outfit"],
                role=ReferenceRole.CHARACTER_BODY,
                angle=ReferenceAngle.FRONT,
                framing=ReferenceFraming.FULL_BODY,
                tags=["outfit"],
                description="independent approved outfit reference",
            ),
        ).entry,
    ]

    requirements = [
        ReferenceRequirement(
            id="face-required",
            subject_asset_id=asset_id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            required_tags=["identity", "face"],
            purpose="preserve facial identity",
        ),
        ReferenceRequirement(
            id="profile-required",
            subject_asset_id=asset_id,
            acceptable_roles=[ReferenceRole.CHARACTER_PROFILE],
            preferred_angles=[ReferenceAngle.LEFT_PROFILE],
            purpose="preserve profile identity",
        ),
        ReferenceRequirement(
            id="outfit-required",
            subject_asset_id=asset_id,
            acceptable_roles=[ReferenceRole.CHARACTER_BODY],
            required_tags=["outfit"],
            purpose="preserve the outfit",
        ),
    ]
    service.update_shot_reference_plan(
        project_id,
        shot_id,
        ShotReferencePlanUpdateRequest(
            requirements=requirements,
            max_reference_images=3,
        ),
    )
    preview = service.preview_shot_reference_plan(
        project_id,
        shot_id,
        ReferencePlanPreviewRequest(),
    )
    assert {item.entry_id for item in preview.selected} == {item.id for item in entries}
    assert preview.missing_requirement_ids == []

    document = service.repository.get_project(project_id)
    assert document is not None
    plan = next(item for item in document.reference_plan if item.shot_id == shot_id)
    shot = next(item for item in document.shots if item.id == shot_id)
    candidates, _max_selected, _warnings = build_reference_visual_candidates(
        document=document,
        shot=shot,
        plan=plan,
        request=ReferenceVisualRerankRequest(),
    )
    assert {item.entry_id for item in candidates} == {item.id for item in entries}
    catalog = service.search_asset_catalog(
        AssetCatalogSearchRequest(roles=[ReferenceRole.CHARACTER_PROFILE]),
        current_project_id=project_id,
    )
    assert [item.asset_version_id for item in catalog.candidates] == [
        supplemental_ids["profile"]
    ]
    resolved = service._resolve_approved_asset_inputs(
        document,
        [
            H3MediaInput(
                kind=H3MediaKind.IMAGE,
                role=H3MediaRole.REFERENCE_IMAGE,
                source=H3MediaSource(external_url="https://ignored.example/profile.png"),
                asset_version_id=supplemental_ids["profile"],
            )
        ],
    )
    assert resolved[0].source.local_path is not None
    assert service._h3_image_input(
        document,
        supplemental_ids["face"],
        H3MediaRole.REFERENCE_IMAGE,
    ).asset_version_id == supplemental_ids["face"]

    next_canonical_id = _import_image(
        service,
        tmp_path,
        project_id=project_id,
        asset_id=asset_id,
        name="character-canonical-v2",
        color="#fef3c7",
    )
    reviewed = service.review_asset_version(
        project_id,
        asset_id,
        next_canonical_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="new canonical identity master",
        ),
    )
    versions = {item.id: item for item in reviewed.asset.versions}
    assert reviewed.asset.approved_version_id == next_canonical_id
    assert versions[canonical_id].approval_status == ApprovalStatus.SUPERSEDED
    assert versions[canonical_id].freshness == Freshness.STALE
    for version_id in supplemental_ids.values():
        assert versions[version_id].approval_status == ApprovalStatus.APPROVED
        assert versions[version_id].freshness == Freshness.CURRENT

    refreshed = service.preview_shot_reference_plan(
        project_id,
        shot_id,
        ReferencePlanPreviewRequest(),
    )
    assert [item.entry_id for item in refreshed.selected] == [entries[2].id]
    assert set(refreshed.missing_requirement_ids) == {
        "face-required",
        "profile-required",
    }
    stale_catalog = service.search_asset_catalog(
        AssetCatalogSearchRequest(roles=[ReferenceRole.CHARACTER_PROFILE]),
        current_project_id=project_id,
    )
    assert stale_catalog.candidates == []
    with pytest.raises(WorkflowConflictError, match="current approved canonical image"):
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=asset_id,
                asset_version_id=supplemental_ids["face"],
                lineage_root_version_id=canonical_id,
                role=ReferenceRole.CHARACTER_FACE,
                description="stale lineage must not be reattached",
            ),
        )


def test_supplemental_approval_does_not_relax_audio_master_rules(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    audio_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Voice reference",
            descriptor="voice identity reference",
        ),
    ).asset
    audio = service.import_url_asset_version(
        project_id,
        audio_asset.id,
        AssetImportUrlRequest(
            external_url="https://example.test/voice.wav",
            descriptor="voice candidate",
            media_type="AUDIO",
        ),
    ).version

    with pytest.raises(WorkflowConflictError, match="supplemental reference must be an image"):
        service.review_asset_version(
            project_id,
            audio_asset.id,
            audio.id,
            AssetReviewRequest(
                decision="APPROVED",
                purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                notes="must stay canonical-only",
            ),
        )

    approved = service.review_asset_version(
        project_id,
        audio_asset.id,
        audio.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="approved voice master",
        ),
    )
    assert approved.asset.approved_version_id == audio.id
