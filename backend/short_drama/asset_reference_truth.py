"""Shared truth rules for media inputs backed by project AssetVersions."""

from __future__ import annotations

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
    """Resolve an H3 media reference against canonical and ReferencePack truth.

    Images without a semantic subject may use any current approved image. Once a
    subject is declared, the image must be that subject's current canonical image
    or an entry in its currently valid ReferencePack. Audio and video remain tied
    to the owning asset's current canonical version.
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
    if version.approval_status != ApprovalStatus.APPROVED:
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
    if not valid_pack_entry:
        raise AssetReferenceTruthError(
            f"Image AssetVersion {asset_version_id} is neither subject {subject.id}'s "
            "current canonical image nor a current valid ReferencePack entry"
        )
    return owner, version
