"""Shared truth rules for media inputs backed by project AssetVersions."""

from __future__ import annotations

from pathlib import Path

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetMediaType,
    AssetVersion,
    Freshness,
    ProjectDocument,
)


class AssetReferenceTruthError(ValueError):
    """Raised when a persisted media reference no longer matches project truth."""


def resolve_current_asset_media(
    document: ProjectDocument,
    *,
    asset_version_id: str,
    subject_asset_id: str | None,
) -> tuple[Asset, AssetVersion]:
    """Resolve an H3 media reference against current explicit project choices.

    Images without a semantic subject may use any current approved image. Once a
    subject is declared, its current canonical image, valid ReferencePack entries
    and exact images explicitly selected in shot prose can be used. Selecting a
    current draft image does not require a second approval or imply a QA pass.
    Audio uses the owner's currently selected usable version, including a draft.
    Video keeps its existing canonical-version rule.
    """

    version_owner: tuple[Asset, AssetVersion] | None = None
    for asset in document.assets:
        version = next(
            (item for item in asset.versions if item.id == asset_version_id),
            None,
        )
        if version is not None:
            version_owner = (asset, version)
            break
    if version_owner is None:
        raise AssetReferenceTruthError(
            f"Unknown AssetVersion {asset_version_id}"
        )
    owner, version = version_owner
    if version.media_type == AssetMediaType.AUDIO:
        if version.id != (owner.current_version_id or owner.approved_version_id):
            raise AssetReferenceTruthError(
                f"Audio AssetVersion {asset_version_id} is not its asset's currently selected media"
            )
        if version.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED} or version.freshness != Freshness.CURRENT:
            raise AssetReferenceTruthError(f"Audio AssetVersion {asset_version_id} is not current usable media")
        if version.local_path is None and version.external_url is None:
            raise AssetReferenceTruthError(f"Audio AssetVersion {asset_version_id} has no usable media")
        if version.local_path is not None and not Path(version.local_path).expanduser().is_file():
            raise AssetReferenceTruthError(f"Audio AssetVersion {asset_version_id} file is missing: {version.local_path}")
        if subject_asset_id is not None and not any(asset.id == subject_asset_id for asset in document.assets):
            raise AssetReferenceTruthError(f"Unknown subject Asset {subject_asset_id}")
        return owner, version
    explicit_image_choice = (
        version.media_type == AssetMediaType.IMAGE
        and (subject_asset_id is None or subject_asset_id == owner.id)
        and any(
            cue.usage == "GENERATION"
            and cue.subject_asset_id == owner.id
            and cue.asset_version_id == version.id
            for shot in document.shots
            for cue in [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]
        )
    )
    if version.approval_status != ApprovalStatus.APPROVED and not (
        explicit_image_choice and version.approval_status == ApprovalStatus.DRAFT
    ):
        raise AssetReferenceTruthError(
            f"AssetVersion {asset_version_id} is not approved"
        )
    if version.local_path is None and version.external_url is None:
        raise AssetReferenceTruthError(
            f"AssetVersion {asset_version_id} has no usable media"
        )

    if version.media_type != AssetMediaType.IMAGE:
        if owner.approved_version_id != version.id:
            raise AssetReferenceTruthError(
                f"AssetVersion {asset_version_id} is not its asset's current canonical media"
            )
        return owner, version

    if version.freshness != Freshness.CURRENT:
        raise AssetReferenceTruthError(
            f"Image AssetVersion {asset_version_id} is not current"
        )
    if subject_asset_id is None:
        return owner, version

    subject = next(
        (asset for asset in document.assets if asset.id == subject_asset_id),
        None,
    )
    if subject is None:
        raise AssetReferenceTruthError(f"Unknown subject Asset {subject_asset_id}")
    if subject.approved_version_id == version.id and owner.id == subject.id:
        return owner, version

    canonical = next(
        (
            item
            for item in subject.versions
            if item.id == subject.approved_version_id
            and item.approval_status == ApprovalStatus.APPROVED
            and item.freshness == Freshness.CURRENT
            and item.media_type == AssetMediaType.IMAGE
            and (item.local_path is not None or item.external_url is not None)
        ),
        None,
    )
    valid_pack_entry = any(
        pack.project_id == document.project.id
        and pack.subject_asset_id == subject.id
        and entry.asset_version_id == version.id
        and (
            entry.lineage_root_version_id is None
            or canonical is not None
            and entry.lineage_root_version_id == canonical.id
        )
        for pack in document.reference_packs
        for entry in pack.entries
    )
    linked_pack_entry = any(
        pack.project_id == document.project.id
        and pack.subject_asset_id == subject.id
        and entry.asset_version_id == version.id
        and entry.lineage_root_version_id is not None
        for pack in document.reference_packs for entry in pack.entries
    )
    if explicit_image_choice and (not linked_pack_entry or valid_pack_entry):
        return owner, version
    if not valid_pack_entry:
        raise AssetReferenceTruthError(
            f"Image AssetVersion {asset_version_id} is neither subject {subject.id}'s "
            "current canonical image nor a current valid ReferencePack entry"
        )
    return owner, version
