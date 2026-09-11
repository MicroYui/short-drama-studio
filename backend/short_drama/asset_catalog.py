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
    Freshness,
    ProjectDocument,
    ReferenceAngle,
    ReferenceFraming,
    ReferencePack,
    ReferencePackEntry,
    ReferenceRequirement,
    ReferenceRole,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
)
from short_drama.style_context import declared_style_value
from short_drama.reference_planner import usable_reference_pack_entries


class AssetCatalogSearchRequest(DomainModel):
    query: str = Field(default="", max_length=1000)
    kinds: list[AssetKind] = Field(default_factory=list[AssetKind], max_length=4)
    roles: list[ReferenceRole] = Field(
        default_factory=list[ReferenceRole],
        max_length=12,
    )
    tags: list[str] = Field(default_factory=list[str], max_length=24)
    include_current_project: bool = True
    include_cross_project: bool = False
    source_project_ids: list[str] = Field(
        default_factory=list[str],
        max_length=32,
    )
    limit: int = Field(default=12, ge=1, le=50)


class AssetCatalogCandidate(DomainModel):
    source_project_id: str
    source_project_name: str
    source_asset_id: str
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
    acceptable_roles: list[ReferenceRole]
    preferred_angles: list[ReferenceAngle]
    preferred_framings: list[ReferenceFraming]
    required_tags: list[str]
    purposes: list[str]
    required: bool
    lineage_root_version_id: str


class AssetCanonicalNeed(DomainModel):
    """A requested subject that needs a base before derived views can exist."""

    subject_asset_id: str
    requirement_ids: list[str]
    shot_ids: list[str]
    purposes: list[str]
    required: bool


class AssetCoverageResponse(DomainModel):
    project_id: str
    ready: bool
    missing_canonical_assets: list[AssetCanonicalNeed] = Field(
        default_factory=list[AssetCanonicalNeed]
    )
    missing_views: list[ReferenceViewNeed]


_DEFAULT_PACK_ROLE: dict[
    AssetKind,
    tuple[ReferenceRole, ReferenceAngle, ReferenceFraming, list[str]],
] = {
    AssetKind.CHARACTER: (
        ReferenceRole.CANONICAL_MASTER,
        ReferenceAngle.NOT_APPLICABLE,
        ReferenceFraming.NOT_APPLICABLE,
        ["canonical"],
    ),
    AssetKind.LOCATION: (
        ReferenceRole.CANONICAL_MASTER,
        ReferenceAngle.NOT_APPLICABLE,
        ReferenceFraming.NOT_APPLICABLE,
        ["canonical"],
    ),
    AssetKind.PROP: (
        ReferenceRole.CANONICAL_MASTER,
        ReferenceAngle.NOT_APPLICABLE,
        ReferenceFraming.NOT_APPLICABLE,
        ["canonical"],
    ),
}


def ensure_base_reference_pack_entries(
    document: ProjectDocument,
    *,
    now: str,
) -> bool:
    """Register the available selected image as its subject's default pack root."""

    changed = False
    for asset in document.assets:
        metadata = _DEFAULT_PACK_ROLE.get(asset.kind)
        if metadata is None:
            continue
        version = _catalog_source_image(asset)
        if not _usable_selected_image(asset, version):
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
        tuple[
            str,
            tuple[ReferenceRole, ...],
            tuple[ReferenceAngle, ...],
            tuple[ReferenceFraming, ...],
            tuple[str, ...],
            bool,
        ],
        ReferenceViewNeed,
    ] = {}
    for plan in document.reference_plan:
        for requirement in plan.requirements:
            if _requirement_has_match(requirement, valid_entries):
                continue
            subject = assets.get(requirement.subject_asset_id)
            if subject is None or subject.kind not in _DEFAULT_PACK_ROLE:
                continue
            root = _catalog_source_image(subject)
            if root is None:
                continue
            acceptable_roles = tuple(requirement.acceptable_roles)
            preferred_angles = tuple(requirement.preferred_angles)
            preferred_framings = tuple(requirement.preferred_framings)
            tags = tuple(
                dict.fromkeys(
                    item.strip() for item in requirement.required_tags if item.strip()
                )
            )
            key = (
                subject.id,
                acceptable_roles,
                preferred_angles,
                preferred_framings,
                tags,
                requirement.required,
            )
            existing = pending.get(key)
            if existing is None:
                pending[key] = ReferenceViewNeed(
                    subject_asset_id=subject.id,
                    requirement_ids=[requirement.id],
                    shot_ids=[plan.shot_id],
                    acceptable_roles=list(acceptable_roles),
                    preferred_angles=list(preferred_angles),
                    preferred_framings=list(preferred_framings),
                    required_tags=list(tags),
                    purposes=[requirement.purpose],
                    required=requirement.required,
                    lineage_root_version_id=root.id,
                )
            else:
                if requirement.id not in existing.requirement_ids:
                    existing.requirement_ids.append(requirement.id)
                if plan.shot_id not in existing.shot_ids:
                    existing.shot_ids.append(plan.shot_id)
                if requirement.purpose not in existing.purposes:
                    existing.purposes.append(requirement.purpose)
    return list(pending.values())[:limit]


def asset_coverage_ready(document: ProjectDocument) -> bool:
    """Return readiness from every required requirement, without display truncation."""

    valid_entries = _valid_pack_entries(document)
    assets = {item.id: item for item in document.assets}
    for plan in document.reference_plan:
        for requirement in plan.requirements:
            if not requirement.required:
                continue
            subject = assets.get(requirement.subject_asset_id)
            if subject is None or subject.kind not in _DEFAULT_PACK_ROLE:
                continue
            if not _requirement_has_match(requirement, valid_entries):
                return False
    return True


def missing_canonical_assets(
    document: ProjectDocument,
    *,
    limit: int = 500,
) -> list[AssetCanonicalNeed]:
    """Report requested subjects whose current canonical image is unavailable.

    This is a factual dependency report.  It does not decide whether the Agent
    should generate, import, reuse, or intentionally keep a subject text-only.
    """

    valid_entries = _valid_pack_entries(document)
    assets = {item.id: item for item in document.assets}
    pending: dict[str, AssetCanonicalNeed] = {}
    for plan in document.reference_plan:
        for requirement in plan.requirements:
            if _requirement_has_match(requirement, valid_entries):
                continue
            subject = assets.get(requirement.subject_asset_id)
            if subject is None or subject.kind not in _DEFAULT_PACK_ROLE:
                continue
            if _catalog_source_image(subject) is not None:
                continue
            existing = pending.get(subject.id)
            if existing is None:
                pending[subject.id] = AssetCanonicalNeed(
                    subject_asset_id=subject.id,
                    requirement_ids=[requirement.id],
                    shot_ids=[plan.shot_id],
                    purposes=[requirement.purpose],
                    required=requirement.required,
                )
                continue
            existing.required = existing.required or requirement.required
            if requirement.id not in existing.requirement_ids:
                existing.requirement_ids.append(requirement.id)
            if plan.shot_id not in existing.shot_ids:
                existing.shot_ids.append(plan.shot_id)
            if requirement.purpose not in existing.purposes:
                existing.purposes.append(requirement.purpose)
    return list(pending.values())[:limit]


def recommended_reusable_reference_views(
    document: ProjectDocument,
    *,
    limit: int = 24,
) -> list[ReferenceViewNeed]:
    """Compatibility name for the factual unresolved-view report.

    Reusable-view selection is now a semantic Agent decision informed by the
    loaded Method Skill, not a fixed role allowlist inside this Tool.
    """

    return missing_reference_views(document, limit=limit)


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
    tags = "、".join(need.required_tags) if need.required_tags else "无额外标签"
    roles = "、".join(item.value for item in need.acceptable_roles)
    angles = (
        "、".join(item.value for item in need.preferred_angles)
        if need.preferred_angles
        else "未指定，由 Agent 依用途判断"
    )
    framings = (
        "、".join(item.value for item in need.preferred_framings)
        if need.preferred_framings
        else "未指定，由 Agent 依用途判断"
    )
    purposes = "；".join(need.purposes)
    requirement_level = "必需" if need.required else "可选建议"
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_BUILDER,),
        include_constitution=False,
        stage=ProductionPromptStage.ASSETS,
    )
    style = bible.style
    medium = declared_style_value(style.medium)
    shared = (
        "基于输入的当前批准母版，生成一张单幅、9:16 的"
        + (f"{medium}制片参考图。" if medium is not None else "制片参考图。")
        + "母版是已声明身份、造型或空间关系的来源；只保持当前 Style 与参考职责要求的"
        "主体、物体、材质、状态和方向关系。不要宫格、拼贴、说明文字、箭头、品牌、"
        "水印或可读标牌。"
    )
    if subject.kind == AssetKind.CHARACTER:
        task = (
            f"可接受职责：{roles}；偏好角度：{angles}；偏好景别：{framings}。"
            "Agent 依镜头用途选择实际要制作的角色视图；背景服从参考职责。"
            "保持项目为该角色声明的身份与造型锚点，不自行补充或替换未声明特征。"
        )
    elif subject.kind == AssetKind.LOCATION:
        task = (
            f"可接受职责：{roles}；偏好角度：{angles}；偏好景别：{framings}；"
            f"重点区域标签：{tags}。Agent 依镜头用途选择实际视图。"
            "这是同一个空间的新机位或局部覆盖，不是重新设计场景；入口、出口、通道、"
            "门窗、桌椅和主要光源必须与母版属于同一套可解释布局。不要人物。"
        )
    else:
        task = (
            f"可接受职责：{roles}；偏好角度：{angles}；偏好景别：{framings}；"
            f"状态标签：{tags}。Agent 依镜头用途选择实际状态视图。"
            "保持形状、颜色、尺度、部件数量和材质；若需要手持比例，只出现匿名裁切手部。"
        )
    style_fields = [
        f"{label}={value}"
        for label, raw_value in (
            ("媒介", style.medium),
            ("镜头语言", style.cinematography),
            ("色彩", style.color),
            ("质感", style.texture),
        )
        if (value := declared_style_value(raw_value)) is not None
    ]
    style_context = (
        "\n项目 Style：" + "；".join(style_fields) + "。"
        if style_fields
        else "\n项目尚未声明具体视觉 Style。"
    )
    style_context += (
        "背景、光线和细节只按当前 Style 与参考职责决定，"
        "不另加默认审美。"
    )
    return (
        f"{shared}\n{task}\n资产：{subject.name}\n要求级别：{requirement_level}\n"
        f"用途：{purposes}\n"
        f"{style_context}\n制作类型策略：{skill_prompt.text}"
    )


def search_asset_catalog(
    documents: list[ProjectDocument],
    *,
    current_project_id: str | None,
    request: AssetCatalogSearchRequest,
) -> AssetCatalogSearchResponse:
    requested_tags = {item.strip().lower() for item in request.tags if item.strip()}
    source_project_ids = set(request.source_project_ids)
    query_tokens = _search_tokens(request.query)
    candidates: list[AssetCatalogCandidate] = []
    for document in documents:
        if source_project_ids and document.project.id not in source_project_ids:
            continue
        if (
            current_project_id is not None
            and document.project.id != current_project_id
            and not request.include_cross_project
        ):
            continue
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
        rows: list[tuple[Asset, Asset, AssetVersion, ReferencePackEntry]] = []
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
                if not _usable_selected_image(source_asset, version):
                    continue
                if (
                    entry.lineage_root_version_id is not None
                    and not any(
                        item.id == entry.lineage_root_version_id
                        and _usable_selected_image(subject, item)
                        for item in subject.versions
                    )
                ):
                    continue
                rows.append((subject, source_asset, version, entry))
                seen.add((entry.asset_version_id, entry.role))
        for subject in document.assets:
            metadata = _DEFAULT_PACK_ROLE.get(subject.kind)
            version = _catalog_source_image(subject)
            if metadata is None or version is None:
                continue
            role, angle, framing, tags = metadata
            if (version.id, role) in seen:
                continue
            rows.append(
                (
                    subject,
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
        for subject, source_asset, version, entry in rows:
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
                    source_asset_id=source_asset.id,
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


def catalog_semantic_asset_kinds(
    document: ProjectDocument,
    asset_version_id: str,
) -> set[AssetKind]:
    """Return target kinds under which a usable catalog version is advertised.

    A supplemental image may physically live on a REFERENCE or another asset while
    serving a character, location, or prop pack. Import validation follows that
    explicit pack relationship instead of pretending the pack subject owns the file.
    """

    assets = {asset.id: asset for asset in document.assets}
    result: set[AssetKind] = set()
    for pack, entry in _valid_pack_entries(document):
        if entry.asset_version_id != asset_version_id:
            continue
        subject = assets.get(pack.subject_asset_id)
        if subject is not None:
            result.add(subject.kind)
    return result


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
    return usable_reference_pack_entries(document)


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


def _catalog_source_image(asset: Asset) -> AssetVersion | None:
    selected_id = asset.approved_version_id or asset.current_version_id
    version = next(
        (item for item in asset.versions if item.id == selected_id),
        None,
    )
    return (
        version
        if version is not None
        and _usable_selected_image(asset, version)
        else None
    )


def _usable_selected_image(asset: Asset, version: AssetVersion | None) -> bool:
    del asset
    return bool(
        version is not None
        and version.approval_status in {
            ApprovalStatus.DRAFT, ApprovalStatus.APPROVED,
        }
        and version.freshness == Freshness.CURRENT
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
