"""Asset-Agent tool contracts for catalog reuse and same-master coverage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.asset_catalog import (
    build_reference_view_prompt,
    ensure_base_reference_pack_entries,
    missing_canonical_assets,
    missing_reference_views,
    recommended_reusable_reference_views,
)
from short_drama.deps import get_short_drama_service
from short_drama.image_models import (
    AssetImportLocalRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    CreateProjectRequest,
    Freshness,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRequirement,
    ReferenceRole,
    SourceMode,
)
from short_drama.production_planner import (
    build_project_document_from_plan,
    create_production_plan,
)
from short_drama.service import ShortDramaService
from short_drama.stock_assets import (
    StockAssetCandidate,
    StockAssetProvider,
    StockAssetRecommendedUse,
    StockAssetSearchRequest,
    StockAssetSearchResponse,
)


class FakeStockAssetSearchClient:
    def __init__(self) -> None:
        self.requests: list[StockAssetSearchRequest] = []

    def search(self, request: StockAssetSearchRequest) -> StockAssetSearchResponse:
        self.requests.append(request)
        candidate = StockAssetCandidate(
            provider=request.provider,
            provider_asset_id="portrait-101",
            asset_kind=request.asset_kind,
            title="Natural light portrait",
            source_page_url="https://example.test/photos/portrait-101",
            image_url="https://example.test/media/portrait-101.jpg",
            preview_url="https://example.test/media/portrait-101-small.jpg",
            creator="Example Photographer",
            creator_url="https://example.test/creators/example",
            license_name="Example Free License",
            license_url="https://example.test/license",
            attribution="Portrait by Example Photographer",
            width=2400,
            height=3600,
            tags=["portrait", "natural light"],
            recommended_use=(
                StockAssetRecommendedUse.VISUAL_GROUNDING_NEW_IDENTITY
                if request.asset_kind == AssetKind.CHARACTER
                else StockAssetRecommendedUse.DIRECT_OR_DERIVE
            ),
            advisory_notes=["用户可选择、上传其他素材或忽略该建议。"],
        )
        return StockAssetSearchResponse(
            provider=request.provider,
            query=request.query,
            page=request.page,
            candidates=[candidate],
            warnings=["候选需由用户或 Agent 按当前用途判断。"],
        )


def _client(service: ShortDramaService) -> TestClient:
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return TestClient(app)


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def test_coverage_tool_requests_only_views_missing_from_current_master(
    tmp_path: Path,
) -> None:
    request = CreateProjectRequest(
        name="资产覆盖测试",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="深夜办公室，两人在走廊边走边说，会议室揭晓误会。",
    )
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-asset-coverage",
        request=request,
        planning=planning,
        created_at="2026-08-20T00:00:00Z",
    )
    character_asset = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    location_asset = next(
        asset for asset in document.assets if asset.kind == AssetKind.LOCATION
    )
    reference_plan = document.reference_plan[0]
    reference_plan.requirements = [
        ReferenceRequirement(
            id="explicit-character-profile",
            subject_asset_id=character_asset.id,
            acceptable_roles=[ReferenceRole.CHARACTER_PROFILE],
            preferred_angles=[ReferenceAngle.LEFT_PROFILE],
            preferred_framings=[ReferenceFraming.MEDIUM],
            required_tags=["identity", "profile"],
            purpose="Agent 显式要求可跨镜复用的角色侧面身份视图。",
        ),
        ReferenceRequirement(
            id="explicit-location-interaction",
            subject_asset_id=location_asset.id,
            acceptable_roles=[ReferenceRole.LOCATION_INTERACTION],
            preferred_angles=[ReferenceAngle.NOT_APPLICABLE],
            preferred_framings=[ReferenceFraming.WIDE],
            required_tags=["topology", "interaction"],
            purpose="Agent 显式要求可跨镜复用的场景交互区拓扑视图。",
        ),
    ]
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    bible.style.medium = "纸雕与水墨混合视觉"
    bible.style.cinematography = "平面构图按叙事关系变化"
    bible.style.color = "朱红与墨黑"
    bible.style.texture = "纸张纤维与墨色层次"
    for index, asset in enumerate(document.assets, start=1):
        if asset.kind not in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}:
            continue
        path = tmp_path / f"master-{index}.png"
        Image.new("RGB", (768, 1344), (index * 30, 80, 120)).save(path)
        original = asset.versions[0]
        approved = original.model_copy(
            deep=True,
            update={
                "media_type": AssetMediaType.IMAGE,
                "local_path": str(path),
                "approval_status": ApprovalStatus.APPROVED,
                "freshness": Freshness.CURRENT,
            },
        )
        asset.versions = [approved]
        asset.current_version_id = approved.id
        asset.approved_version_id = approved.id

    assert ensure_base_reference_pack_entries(
        document,
        now="2026-08-20T00:00:01Z",
    )
    missing = missing_reference_views(document)
    recommended = recommended_reusable_reference_views(document)

    assert missing
    assert recommended
    assert recommended == missing
    assets_by_id = {item.id: item for item in document.assets}
    character_need = next(
        item
        for item in recommended
        if assets_by_id[item.subject_asset_id].kind == AssetKind.CHARACTER
    )
    character_prompt = build_reference_view_prompt(
        document,
        assets_by_id[character_need.subject_asset_id],
        character_need,
    )
    assert "纸雕与水墨混合视觉" in character_prompt
    assert "朱红与墨黑" in character_prompt
    assert "纸张纤维与墨色层次" in character_prompt
    assert "中性柔光" not in character_prompt
    assert "禁止：" not in character_prompt
    assert "当前制片技能：AI 短剧（本地格式适配）。" in character_prompt
    assert "资产审核的通用职责：" in character_prompt
    assert "资产审核的当前格式补充：" not in character_prompt
    assert "类型创作原则：" not in character_prompt
    location_need = next(
        item
        for item in recommended
        if assets_by_id[item.subject_asset_id].kind == AssetKind.LOCATION
    )
    location_prompt = build_reference_view_prompt(
        document,
        assets_by_id[location_need.subject_asset_id],
        location_need,
    )
    assert "项目 Style：" in location_prompt
    assert "禁止：" not in location_prompt
    assert "资产审核的通用职责：" in location_prompt
    assert all(
        need.lineage_root_version_id
        == next(
            asset.approved_version_id
            for asset in document.assets
            if asset.id == need.subject_asset_id
        )
        for need in missing
    )
    assert any(
        ReferenceRole.LOCATION_INTERACTION in need.acceptable_roles
        for need in missing
    )
    canonical_entries = [
        entry
        for pack in document.reference_packs
        for entry in pack.entries
        if entry.priority == 100
    ]
    assert canonical_entries
    assert all(
        entry.asset_version_id == entry.lineage_root_version_id
        for entry in canonical_entries
    )
    assert all(entry.role == ReferenceRole.CANONICAL_MASTER for entry in canonical_entries)
    assert all(entry.angle == ReferenceAngle.NOT_APPLICABLE for entry in canonical_entries)
    assert all(entry.framing == ReferenceFraming.NOT_APPLICABLE for entry in canonical_entries)


def test_coverage_reports_missing_canonical_instead_of_false_ready(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    document = project.document
    character = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    document.reference_plan[0].requirements = [
        ReferenceRequirement(
            id="needs-face-before-master",
            subject_asset_id=character.id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            preferred_angles=[ReferenceAngle.FRONT],
            preferred_framings=[ReferenceFraming.FACE_CLOSE_UP],
            required_tags=["identity"],
            purpose="镜头需要稳定的主角面部身份。",
        )
    ]
    service.repository.save_project(document)

    missing_base = missing_canonical_assets(document)
    assert [item.subject_asset_id for item in missing_base] == [character.id]
    coverage = service.inspect_asset_coverage(document.project.id)
    assert coverage.ready is False
    assert [item.subject_asset_id for item in coverage.missing_canonical_assets] == [
        character.id
    ]
    assert coverage.missing_views == []


def test_catalog_tools_search_and_import_an_approved_asset(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = service.initialize_fixture()
    source_asset = next(
        asset for asset in source.document.assets if asset.kind == AssetKind.CHARACTER
    )
    image_path = tmp_path / "linxia-master.png"
    Image.new("RGB", (768, 1344), "#d6d3d1").save(image_path)
    imported = service.import_local_asset_version(
        source.document.project.id,
        source_asset.id,
        AssetImportLocalRequest(
            local_path=str(image_path),
            descriptor="林夏的当前批准全身母版",
        ),
    )
    approved = service.review_asset_version(
        source.document.project.id,
        source_asset.id,
        imported.version.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="可用的身份母版",
        ),
    )
    target = service.create_project(
        CreateProjectRequest(
            name="跨项目资产复用",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="一段都市职场误会与反转。",
        )
    )
    target_character = next(
        asset for asset in target.document.assets if asset.kind == AssetKind.CHARACTER
    )
    client = _client(service)

    isolated_search = client.post(
        "/api/short-drama/asset-agent/tools/search-assets",
        params={"current_project_id": target.document.project.id},
        json={
            "query": source_asset.name,
            "kinds": ["CHARACTER"],
            "roles": ["CANONICAL_MASTER"],
            "include_current_project": True,
        },
    )
    assert isolated_search.status_code == 200, isolated_search.text
    assert isolated_search.json()["candidates"] == []

    searched = client.post(
        "/api/short-drama/asset-agent/tools/search-assets",
        params={"current_project_id": target.document.project.id},
        json={
            "query": source_asset.name,
            "kinds": ["CHARACTER"],
            "roles": ["CANONICAL_MASTER"],
            "include_current_project": False,
            "include_cross_project": True,
        },
    )
    assert searched.status_code == 200, searched.text
    candidate = next(
        item
        for item in searched.json()["candidates"]
        if item["asset_version_id"] == approved.version.id
    )

    cloned = client.post(
        f"/api/short-drama/projects/{target.document.project.id}/asset-agent/tools/import-asset",
        json={
            "source_project_id": candidate["source_project_id"],
            "source_asset_id": candidate["source_asset_id"],
            "source_version_id": candidate["asset_version_id"],
            "target_asset_id": target_character.id,
            "descriptor": "资产 Agent 选中的跨项目母版候选",
        },
    )
    assert cloned.status_code == 200, cloned.text
    body = cloned.json()
    assert body["version"]["approval_status"] == "DRAFT"
    assert body["version"]["source"]["source_type"] == "LIBRARY_IMPORT"
    assert body["version"]["source"]["source_uri"].endswith(
        f"/versions/{approved.version.id}"
    )
    assert Path(body["version"]["local_path"]).is_file()

    coverage = client.get(
        f"/api/short-drama/projects/{target.document.project.id}/asset-agent/tools/inspect-coverage"
    )
    assert coverage.status_code == 200, coverage.text
    assert coverage.json()["project_id"] == target.document.project.id


def test_stock_search_and_import_preserve_source_and_remain_user_overridable(
    tmp_path: Path,
) -> None:
    stock_client = FakeStockAssetSearchClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        stock_asset_client=stock_client,
    )
    project = service.initialize_fixture()
    character = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    client = _client(service)

    searched = client.post(
        "/api/short-drama/asset-agent/tools/search-stock",
        json={
            "provider": "OPENVERSE",
            "query": "Asian woman natural portrait",
            "asset_kind": "CHARACTER",
            "orientation": "PORTRAIT",
            "limit": 8,
        },
    )
    assert searched.status_code == 200, searched.text
    body = searched.json()
    assert body["candidates"][0]["recommended_use"] == (
        "VISUAL_GROUNDING_NEW_IDENTITY"
    )
    assert stock_client.requests[0].provider == StockAssetProvider.OPENVERSE

    imported = client.post(
        f"/api/short-drama/projects/{project.document.project.id}/asset-agent/tools/import-stock",
        json={
            "target_asset_id": character.id,
            "candidate": body["candidates"][0],
            "descriptor": "真实摄影质感母版；后续生成新的虚构人物身份",
        },
    )
    assert imported.status_code == 200, imported.text
    version = imported.json()["version"]
    assert version["approval_status"] == "DRAFT"
    assert version["external_url"].endswith("portrait-101.jpg")
    assert version["source"]["provider"] == "OPENVERSE"
    assert version["source"]["provider_asset_id"] == "portrait-101"
    assert version["source"]["license_name"] == "Example Free License"
    assert version["source"]["stock_import_mode"] == "AUTO"
    assert "忽略该建议" in version["review_notes"]
