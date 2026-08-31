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
    missing_reference_views,
    recommended_reusable_reference_views,
)
from short_drama.deps import get_short_drama_service
from short_drama.image_models import (
    AssetImportLocalRequest,
    AssetReviewRequest,
    ImageOperation,
    ImageProviderConfigUpdate,
    ImageTaskStatus,
)
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    CreateProjectRequest,
    Freshness,
    OriginType,
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
    StockAssetImportMode,
    StockAssetImportRequest,
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
    assert len(recommended) < len(missing)
    assert len({(item.subject_asset_id, item.role) for item in recommended}) == len(
        recommended
    )
    assert all(
        item.role
        in {
            ReferenceRole.CHARACTER_FACE,
            ReferenceRole.CHARACTER_PROFILE,
            ReferenceRole.CHARACTER_BACK,
            ReferenceRole.LOCATION_REVERSE,
            ReferenceRole.LOCATION_INTERACTION,
        }
        for item in recommended
    )
    assert all("current shot" not in item.purpose.lower() for item in recommended)
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
    assert "中性柔光" in character_prompt
    assert "禁止：" not in character_prompt
    assert (
        "Active Production Skill: live-action Douyin vertical short drama."
        in character_prompt
    )
    assert "Asset Reviewer rubric:" in character_prompt
    assert "Production constitution:" not in character_prompt
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
    assert "项目摄影：" in location_prompt
    assert "禁止：" not in location_prompt
    assert "Asset Reviewer rubric:" in location_prompt
    assert all(
        need.lineage_root_version_id
        == next(
            asset.approved_version_id
            for asset in document.assets
            if asset.id == need.subject_asset_id
        )
        for need in missing
    )
    assert any(need.role.value == "LOCATION_INTERACTION" for need in missing)
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
        AssetReviewRequest(decision="APPROVED", notes="可用的身份母版"),
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
            "roles": ["CHARACTER_BODY"],
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
            "roles": ["CHARACTER_BODY"],
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
            "source_asset_id": candidate["subject_asset_id"],
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


def test_stock_character_is_derived_into_a_new_identity_before_approval(
    tmp_path: Path,
) -> None:
    stock_client = FakeStockAssetSearchClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        stock_asset_client=stock_client,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="image-key",
            base_url="https://image.example.test/v1",
        )
    )
    project = service.initialize_fixture()
    character = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )
    candidate = stock_client.search(
        StockAssetSearchRequest(
            provider=StockAssetProvider.OPENVERSE,
            query="natural portrait",
            asset_kind=AssetKind.CHARACTER,
        )
    ).candidates[0]
    imported = service.import_stock_asset(
        project.document.project.id,
        StockAssetImportRequest(
            target_asset_id=character.id,
            candidate=candidate,
            descriptor="真人摄影和造型参考；生成新的虚构人物身份",
        ),
    )
    refreshed = service.get_project(project.document.project.id).document
    refreshed_character = next(
        asset for asset in refreshed.assets if asset.id == character.id
    )
    grounding = service._stock_character_grounding_candidate(
        refreshed_character,
        next(
            version
            for version in refreshed_character.versions
            if version.id == imported.version.id
        ),
    )

    task = service._enqueue_base_asset_image(
        refreshed,
        refreshed_character,
        previous=None,
        stock_grounding=grounding,
    )

    assert task.operation == ImageOperation.EDIT
    assert task.parent_version_id == imported.version.id
    assert task.request.input_version_ids == [imported.version.id]
    assert task.request.primary_input_version_id == imported.version.id
    assert "全新的虚构人物身份" in task.request.prompt
    assert "不得复制" in task.request.prompt


def test_minor_character_remains_supported_without_sending_stock_identity(
    tmp_path: Path,
) -> None:
    stock_client = FakeStockAssetSearchClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        stock_asset_client=stock_client,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="image-key",
            base_url="https://image.example.test/v1",
        )
    )
    project = service.initialize_fixture()
    document = project.document
    character = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    profile = next(item for item in bible.characters if item.name in character.name)
    profile.age = 10
    profile.appearance = "扎高马尾，圆脸，健康自然肤色"
    service.repository.save_project(document)
    candidate = stock_client.search(
        StockAssetSearchRequest(
            provider=StockAssetProvider.OPENVERSE,
            query="natural portrait",
            asset_kind=AssetKind.CHARACTER,
        )
    ).candidates[0]
    imported = service.import_stock_asset(
        project.document.project.id,
        StockAssetImportRequest(
            target_asset_id=character.id,
            candidate=candidate,
            descriptor="真人摄影选型参考；不把真实儿童身份发送给派生任务",
        ),
    )
    refreshed = service.get_project(project.document.project.id).document
    refreshed_character = next(
        asset for asset in refreshed.assets if asset.id == character.id
    )
    grounding = service._stock_character_grounding_candidate(
        refreshed_character,
        next(
            version
            for version in refreshed_character.versions
            if version.id == imported.version.id
        ),
    )

    task = service._enqueue_base_asset_image(
        refreshed,
        refreshed_character,
        previous=None,
        stock_grounding=grounding,
    )

    assert task.operation == ImageOperation.GENERATE
    assert task.parent_version_id is None
    assert task.request.input_version_ids == []
    assert task.request.primary_input_version_id is None
    assert "扎高马尾" in task.request.prompt

    task.status = ImageTaskStatus.FAILED
    task.error_code = "moderation_blocked"
    task.error_message = "provider rejected neutral child generation"
    service.repository.save_image_task(task)
    persisted = service.get_project(project.document.project.id).document
    persisted_character = next(
        asset for asset in persisted.assets if asset.id == character.id
    )
    assert service._minor_reference_derivation_unavailable(
        persisted,
        persisted_character,
    )


def test_agent_can_mark_a_stock_location_for_gpt_derivation(
    tmp_path: Path,
) -> None:
    stock_client = FakeStockAssetSearchClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        stock_asset_client=stock_client,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="image-key",
            base_url="https://image.example.test/v1",
        )
    )
    project = service.initialize_fixture()
    location = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.LOCATION
    )
    candidate = stock_client.search(
        StockAssetSearchRequest(
            provider=StockAssetProvider.OPENVERSE,
            query="industrial room",
            asset_kind=AssetKind.LOCATION,
        )
    ).candidates[0]
    imported = service.import_stock_asset(
        project.document.project.id,
        StockAssetImportRequest(
            target_asset_id=location.id,
            candidate=candidate,
            descriptor="现实空间拓扑与材质参考；派生为本项目虚构地点",
            import_mode=StockAssetImportMode.DERIVE,
        ),
    )
    refreshed = service.get_project(project.document.project.id).document
    refreshed_location = next(
        asset for asset in refreshed.assets if asset.id == location.id
    )
    imported_version = next(
        version
        for version in refreshed_location.versions
        if version.id == imported.version.id
    )
    grounding = service._stock_derivation_candidate(
        refreshed_location,
        imported_version,
    )

    task = service._enqueue_base_asset_image(
        refreshed,
        refreshed_location,
        previous=None,
        stock_grounding=grounding,
    )

    assert task.operation == ImageOperation.EDIT
    assert task.request.primary_input_version_id == imported.version.id
    assert "虚构地点" in task.request.prompt
    assert "空间拓扑" in task.request.prompt


def test_visual_retry_edits_the_rejected_candidate_instead_of_restarting_from_stock(
    tmp_path: Path,
) -> None:
    stock_client = FakeStockAssetSearchClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        stock_asset_client=stock_client,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="image-key",
            base_url="https://image.example.test/v1",
        )
    )
    project = service.initialize_fixture()
    location = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.LOCATION
    )
    candidate = stock_client.search(
        StockAssetSearchRequest(
            provider=StockAssetProvider.OPENVERSE,
            query="courtyard",
            asset_kind=AssetKind.LOCATION,
        )
    ).candidates[0]
    imported = service.import_stock_asset(
        project.document.project.id,
        StockAssetImportRequest(
            target_asset_id=location.id,
            candidate=candidate,
            descriptor="现实空间拓扑与材质参考",
            import_mode=StockAssetImportMode.DERIVE,
        ),
    )
    refreshed = service.get_project(project.document.project.id).document
    refreshed_location = next(
        asset for asset in refreshed.assets if asset.id == location.id
    )
    stock_version = next(
        version
        for version in refreshed_location.versions
        if version.id == imported.version.id
    )
    first = service._enqueue_base_asset_image(
        refreshed,
        refreshed_location,
        previous=None,
        stock_grounding=stock_version,
    )
    rejected_path = tmp_path / "rejected-courtyard.png"
    Image.new("RGB", (768, 1344), "#b8a58e").save(rejected_path)
    rejected = stock_version.model_copy(
        deep=True,
        update={
            "id": first.output_version_id,
            "version": first.output_version_number,
            "origin_type": OriginType.AI_EDITED,
            "local_path": str(rejected_path),
            "external_url": None,
            "mime_type": "image/png",
            "width": 768,
            "height": 1344,
            "parent_version_id": stock_version.id,
            "image_task_id": first.id,
            "source": None,
            "approval_status": ApprovalStatus.REJECTED,
            "review_notes": "保留院落，修正锁闭入口和墙上控制箱。",
            "freshness": Freshness.STALE,
        },
    )
    refreshed_location.versions.append(rejected)
    refreshed_location.current_version_id = rejected.id
    service.repository.save_project(refreshed)
    first.status = ImageTaskStatus.SUCCEEDED
    first.output_path = str(rejected_path)
    service.repository.save_image_task(first)

    current = service.get_project(project.document.project.id).document
    current_location = next(asset for asset in current.assets if asset.id == location.id)
    retry = service._enqueue_base_asset_image(
        current,
        current_location,
        previous=first,
        stock_grounding=stock_version,
    )

    assert retry.operation == ImageOperation.EDIT
    assert retry.parent_version_id == rejected.id
    assert retry.request.input_version_ids == [rejected.id]
    assert retry.request.primary_input_version_id == rejected.id
    assert imported.version.id not in retry.request.input_version_ids
    assert "输入图像是上一版候选资产" in retry.request.prompt
    assert "修正锁闭入口和墙上控制箱" in retry.request.prompt


def test_autonomous_catalog_reuse_requires_an_explicit_linked_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = service.initialize_fixture()
    source_asset = next(
        asset for asset in source.document.assets if asset.kind == AssetKind.CHARACTER
    )
    image_path = tmp_path / "approved-source-character.png"
    Image.new("RGB", (768, 1344), "#a8a29e").save(image_path)
    imported = service.import_local_asset_version(
        source.document.project.id,
        source_asset.id,
        AssetImportLocalRequest(
            local_path=str(image_path),
            descriptor="同一系列可复用的角色母版",
        ),
    )
    service.review_asset_version(
        source.document.project.id,
        source_asset.id,
        imported.version.id,
        AssetReviewRequest(decision="APPROVED", notes="批准为系列资产"),
    )

    target = service.create_project(
        CreateProjectRequest(
            name="默认项目隔离",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="另一个完全独立的都市故事。",
        )
    )
    target_document = target.document
    target_asset = next(
        asset for asset in target_document.assets if asset.kind == AssetKind.CHARACTER
    )
    target_asset.name = source_asset.name
    service.repository.save_project(target_document)

    assert not service._try_catalog_asset_reuse(target_document, target_asset)
    isolated = service.get_project(target_document.project.id).document
    isolated_asset = next(item for item in isolated.assets if item.id == target_asset.id)
    assert len(isolated_asset.versions) == 1

    isolated.project.linked_asset_project_ids = [source.document.project.id]
    service.repository.save_project(isolated)
    linked_asset = next(item for item in isolated.assets if item.id == target_asset.id)

    assert service._try_catalog_asset_reuse(isolated, linked_asset)
    refreshed = service.get_project(isolated.project.id).document
    reused = next(item for item in refreshed.assets if item.id == target_asset.id)
    assert len(reused.versions) == 2
    assert reused.versions[-1].source is not None
    assert reused.versions[-1].source.source_uri is not None
    assert source.document.project.id in reused.versions[-1].source.source_uri
