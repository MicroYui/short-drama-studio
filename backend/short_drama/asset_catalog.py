"""Agent-facing asset catalog search and minimal reference-pack planning."""

from __future__ import annotations

import re

from pydantic import Field

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetVersion,
    DomainModel,
    ProjectDocument,
    ReferenceAngle,
    ReferenceFraming,
    ReferencePack,
    ReferencePackEntry,
    ReferenceRequirement,
    ReferenceRole,
)


class AssetCatalogSearchRequest(DomainModel):
    query: str = Field(default="", max_length=1000)
    kinds: list[AssetKind] = Field(default_factory=list[AssetKind], max_length=4)
    roles: list[ReferenceRole] = Field(
        default_factory=list[ReferenceRole],
        max_length=12,
    )
    tags: list[str] = Field(default_factory=list[str], max_length=24)
    include_current_project: bool = True
    limit: int = Field(default=12, ge=1, le=50)


class AssetCatalogCandidate(DomainModel):
    source_project_id: str
    source_project_name: str
    subject_asset_id: str
    subject_asset_name: str
    asset_kind: AssetKind
    asset_version_id: str
    descriptor: str
    role: ReferenceRole
    angle: ReferenceAngle
    framing: ReferenceFraming
    tags: list[str]
    lineage_root_version_id: str | None = None
    local_path: str | None = None
    external_url: str | None = None
    same_project: bool
    score: int
    reasons: list[str]


class AssetCatalogSearchResponse(DomainModel):
    query: str
    candidates: list[AssetCatalogCandidate]


class AssetCatalogImportRequest(DomainModel):
    source_project_id: str
    source_asset_id: str
    source_version_id: str
    target_asset_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    descriptor: str | None = Field(default=None, min_length=1, max_length=4000)


class ReferenceViewNeed(DomainModel):
    subject_asset_id: str
    requirement_ids: list[str]
    shot_ids: list[str]
    role: ReferenceRole
    angle: ReferenceAngle
    framing: ReferenceFraming
    tags: list[str]
    purpose: str
    lineage_root_version_id: str


class AssetCoverageResponse(DomainModel):
    project_id: str
    ready: bool
    missing_views: list[ReferenceViewNeed]


_DEFAULT_PACK_ROLE: dict[
    AssetKind,
    tuple[ReferenceRole, ReferenceAngle, ReferenceFraming, list[str]],
] = {
    AssetKind.CHARACTER: (
        ReferenceRole.CHARACTER_BODY,
        ReferenceAngle.FRONT,
        ReferenceFraming.FULL_BODY,
        ["identity", "wardrobe"],
    ),
    AssetKind.LOCATION: (
        ReferenceRole.LOCATION_MASTER,
        ReferenceAngle.NOT_APPLICABLE,
        ReferenceFraming.WIDE,
        ["master"],
    ),
    AssetKind.PROP: (
        ReferenceRole.PROP_PRODUCT,
        ReferenceAngle.NOT_APPLICABLE,
        ReferenceFraming.DETAIL,
        ["neutral"],
    ),
}


def ensure_base_reference_pack_entries(
    document: ProjectDocument,
    *,
    now: str,
) -> bool:
    """Register every approved canonical image as its subject's pack root."""

    changed = False
    for asset in document.assets:
        metadata = _DEFAULT_PACK_ROLE.get(asset.kind)
        if metadata is None or asset.approved_version_id is None:
            continue
        version = next(
            (item for item in asset.versions if item.id == asset.approved_version_id),
            None,
        )
        if not _usable_approved_image(asset, version):
            continue
        assert version is not None
        role, angle, framing, tags = metadata
        pack = _pack_for_subject(document, asset, now=now)
        canonical_entry_id = f"reference-entry-{asset.id}-canonical"
        existing = next(
            (
                item
                for item in pack.entries
                if item.id == canonical_entry_id
                or (item.asset_version_id == version.id and item.role == role)
            ),
            None,
        )
        if existing is not None:
            expected = {
                "asset_version_id": version.id,
                "lineage_root_version_id": version.id,
                "role": role,
                "angle": angle,
                "framing": framing,
                "tags": tags,
                "description": f"{asset.name}的当前批准母版",
                "priority": 100,
            }
            if any(getattr(existing, key) != value for key, value in expected.items()):
                for key, value in expected.items():
                    setattr(existing, key, value)
                pack.updated_at = now
                changed = True
            continue
        pack.entries.append(
            ReferencePackEntry(
                id=canonical_entry_id,
                asset_version_id=version.id,
                lineage_root_version_id=version.id,
                role=role,
                angle=angle,
                framing=framing,
                tags=tags,
                description=f"{asset.name}的当前批准母版",
                priority=100,
            )
        )
        pack.updated_at = now
        changed = True
    return changed


def missing_reference_views(
    document: ProjectDocument,
    *,
    limit: int = 500,
) -> list[ReferenceViewNeed]:
    """Return deduplicated requested views not covered by an approved pack entry."""

    valid_entries = _valid_pack_entries(document)
    assets = {item.id: item for item in document.assets}
    pending: dict[
        tuple[str, ReferenceRole, ReferenceAngle, ReferenceFraming, tuple[str, ...]],
        ReferenceViewNeed,
    ] = {}
    for plan in document.reference_plan:
        for requirement in plan.requirements:
            if _requirement_has_match(requirement, valid_entries):
                continue
            subject = assets.get(requirement.subject_asset_id)
            if subject is None or subject.kind not in _DEFAULT_PACK_ROLE:
                continue
            root = _approved_image(subject)
            if root is None:
                continue
            role = requirement.acceptable_roles[0]
            angle = (
                requirement.preferred_angles[0]
                if requirement.preferred_angles
                else ReferenceAngle.NOT_APPLICABLE
            )
            framing = (
                requirement.preferred_framings[0]
                if requirement.preferred_framings
                else ReferenceFraming.NOT_APPLICABLE
            )
            tags = tuple(
                dict.fromkeys(
                    item.strip() for item in requirement.required_tags if item.strip()
                )
            )
            key = (subject.id, role, angle, framing, tags)
            existing = pending.get(key)
            if existing is None:
                pending[key] = ReferenceViewNeed(
                    subject_asset_id=subject.id,
                    requirement_ids=[requirement.id],
                    shot_ids=[plan.shot_id],
                    role=role,
                    angle=angle,
                    framing=framing,
                    tags=list(tags),
                    purpose=requirement.purpose,
                    lineage_root_version_id=root.id,
                )
            else:
                existing.requirement_ids.append(requirement.id)
                if plan.shot_id not in existing.shot_ids:
                    existing.shot_ids.append(plan.shot_id)
    return list(pending.values())[:limit]


def build_reference_view_prompt(
    document: ProjectDocument,
    subject: Asset,
    need: ReferenceViewNeed,
) -> str:
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    tags = "、".join(need.tags) if need.tags else "无额外标签"
    shared = (
        "基于输入的当前批准母版，生成一张单幅9:16真人短剧制片参考图。"
        "母版是身份或空间几何的唯一来源；保持同一人物、服装、物体实例、材质、"
        "灯光状态和左右关系。不要宫格、拼贴、说明文字、箭头、品牌、水印或可读标牌。"
    )
    if subject.kind == AssetKind.CHARACTER:
        task = (
            f"输出同一角色的{need.angle.value}、{need.framing.value}视图；背景干净中性。"
            "不得换脸、换发型、换装、改变年龄体型或复制人物。"
        )
    elif subject.kind == AssetKind.LOCATION:
        task = (
            f"输出{need.role.value}、{need.framing.value}视图，重点区域标签：{tags}。"
            "这是同一个空间的新机位或局部覆盖，不是重新设计场景；入口、出口、通道、"
            "门窗、桌椅和主要光源必须与母版属于同一套可解释布局。不要人物。"
        )
    else:
        task = (
            f"输出同一道具的{need.role.value}、{need.framing.value}状态，状态标签：{tags}。"
            "保持形状、颜色、尺度、部件数量和材质；若需要手持比例，只出现匿名裁切手部。"
        )
    return (
        f"{shared}\n{task}\n资产：{subject.name}\n用途：{need.purpose}\n"
        f"项目摄影：{bible.style.cinematography}\n色彩：{bible.style.color}\n"
        f"禁止：{'；'.join(bible.style.forbidden)}。"
    )


def search_asset_catalog(
    documents: list[ProjectDocument],
    *,
    current_project_id: str | None,
    request: AssetCatalogSearchRequest,
) -> AssetCatalogSearchResponse:
    requested_tags = {item.strip().lower() for item in request.tags if item.strip()}
    query_tokens = _search_tokens(request.query)
    candidates: list[AssetCatalogCandidate] = []
    for document in documents:
        if (
            document.project.id == current_project_id
            and not request.include_current_project
        ):
            continue
        assets = {item.id: item for item in document.assets}
        versions = {
            version.id: (asset, version)
            for asset in document.assets
            for version in asset.versions
        }
        rows: list[tuple[Asset, AssetVersion, ReferencePackEntry]] = []
        seen: set[tuple[str, ReferenceRole]] = set()
        for pack in document.reference_packs:
            subject = assets.get(pack.subject_asset_id)
            if subject is None:
                continue
            for entry in pack.entries:
                resolved = versions.get(entry.asset_version_id)
                if resolved is None:
                    continue
                source_asset, version = resolved
                if not _usable_approved_image(source_asset, version):
                    continue
                if (
                    entry.lineage_root_version_id is not None
                    and subject.approved_version_id != entry.lineage_root_version_id
                ):
                    continue
                rows.append((subject, version, entry))
                seen.add((entry.asset_version_id, entry.role))
        for subject in document.assets:
            metadata = _DEFAULT_PACK_ROLE.get(subject.kind)
            version = _approved_image(subject)
            if metadata is None or version is None:
                continue
            role, angle, framing, tags = metadata
            if (version.id, role) in seen:
                continue
            rows.append(
                (
                    subject,
                    version,
                    ReferencePackEntry(
                        id=f"catalog-default-{subject.id}",
                        asset_version_id=version.id,
                        lineage_root_version_id=version.id,
                        role=role,
                        angle=angle,
                        framing=framing,
                        tags=tags,
                        description=f"{subject.name}的当前批准母版",
                        priority=0,
                    ),
                )
            )
        for subject, version, entry in rows:
            if request.kinds and subject.kind not in request.kinds:
                continue
            if request.roles and entry.role not in request.roles:
                continue
            entry_tags = {item.lower() for item in entry.tags}
            if requested_tags and not requested_tags.issubset(entry_tags):
                continue
            searchable = " ".join(
                (
                    subject.name,
                    version.descriptor,
                    entry.description,
                    entry.role.value,
                    entry.angle.value,
                    entry.framing.value,
                    *entry.tags,
                )
            )
            text_score = _token_overlap(query_tokens, _search_tokens(searchable))
            reasons: list[str] = []
            score = entry.priority
            if not query_tokens:
                score += 30
                reasons.append("empty query accepts structured catalog match +30")
            else:
                score += round(text_score * 70)
                reasons.append(f"text and tag overlap +{round(text_score * 70)}")
            canonical_query = _canonical(request.query)
            name_match = bool(
                canonical_query and canonical_query in _canonical(subject.name)
            )
            if name_match:
                score += 35
                reasons.append("asset name match +35")
            if request.roles:
                score += 20
                reasons.append("requested reference role +20")
            if requested_tags:
                score += len(requested_tags) * 8
                reasons.append(f"required tags +{len(requested_tags) * 8}")
            same_project = document.project.id == current_project_id
            if same_project:
                score += 15
                reasons.append("current project pack +15")
            if query_tokens and text_score == 0 and not name_match:
                continue
            candidates.append(
                AssetCatalogCandidate(
                    source_project_id=document.project.id,
                    source_project_name=document.project.name,
                    subject_asset_id=subject.id,
                    subject_asset_name=subject.name,
                    asset_kind=subject.kind,
                    asset_version_id=version.id,
                    descriptor=version.descriptor,
                    role=entry.role,
                    angle=entry.angle,
                    framing=entry.framing,
                    tags=list(entry.tags),
                    lineage_root_version_id=entry.lineage_root_version_id,
                    local_path=version.local_path,
                    external_url=version.external_url,
                    same_project=same_project,
                    score=score,
                    reasons=reasons,
                )
            )
    candidates.sort(
        key=lambda item: (-item.score, item.source_project_id, item.asset_version_id)
    )
    return AssetCatalogSearchResponse(
        query=request.query,
        candidates=candidates[: request.limit],
    )


def _pack_for_subject(
    document: ProjectDocument,
    subject: Asset,
    *,
    now: str,
) -> ReferencePack:
    pack_id = f"reference-pack-{subject.id}"
    pack = next((item for item in document.reference_packs if item.id == pack_id), None)
    if pack is not None:
        return pack
    pack = ReferencePack(
        id=pack_id,
        project_id=document.project.id,
        subject_asset_id=subject.id,
        name=f"{subject.name}参考包",
        created_at=now,
        updated_at=now,
    )
    document.reference_packs.append(pack)
    return pack


def _valid_pack_entries(
    document: ProjectDocument,
) -> list[tuple[ReferencePack, ReferencePackEntry]]:
    subjects = {asset.id: asset for asset in document.assets}
    approved = {
        version.id
        for asset in document.assets
        for version in asset.versions
        if _usable_approved_image(asset, version)
    }
    return [
        (pack, entry)
        for pack in document.reference_packs
        for entry in pack.entries
        if entry.asset_version_id in approved
        and (
            entry.lineage_root_version_id is None
            or subjects.get(pack.subject_asset_id) is not None
            and subjects[pack.subject_asset_id].approved_version_id
            == entry.lineage_root_version_id
        )
    ]


def _requirement_has_match(
    requirement: ReferenceRequirement,
    entries: list[tuple[ReferencePack, ReferencePackEntry]],
) -> bool:
    required_tags = set(requirement.required_tags)
    for pack, entry in entries:
        if pack.subject_asset_id != requirement.subject_asset_id:
            continue
        if entry.role not in requirement.acceptable_roles:
            continue
        if not required_tags.issubset(entry.tags):
            continue
        if (
            requirement.preferred_angles
            and entry.angle not in requirement.preferred_angles
        ):
            continue
        if (
            requirement.preferred_framings
            and entry.framing not in requirement.preferred_framings
        ):
            continue
        return True
    return False


def _approved_image(asset: Asset) -> AssetVersion | None:
    if asset.approved_version_id is None:
        return None
    version = next(
        (item for item in asset.versions if item.id == asset.approved_version_id),
        None,
    )
    return version if _usable_approved_image(asset, version) else None


def _usable_approved_image(asset: Asset, version: AssetVersion | None) -> bool:
    return bool(
        version is not None
        and asset.approved_version_id == version.id
        and version.approval_status == ApprovalStatus.APPROVED
        and version.media_type == AssetMediaType.IMAGE
        and (version.local_path is not None or version.external_url is not None)
    )


def _search_tokens(value: str) -> set[str]:
    lowered = value.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_]+", lowered))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese_tokens: set[str] = set()
    for run in chinese_runs:
        if len(run) == 1:
            chinese_tokens.add(run)
            continue
        chinese_tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return ascii_tokens | chinese_tokens


def _token_overlap(left: set[str], right: set[str]) -> float:
    if not left:
        return 1.0
    return len(left & right) / len(left)


def _canonical(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())
