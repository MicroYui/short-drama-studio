"""Asset-Agent tool contracts for catalog reuse and same-master coverage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.asset_catalog import (
    ensure_base_reference_pack_entries,
    missing_reference_views,
)
from short_drama.deps import get_short_drama_service
from short_drama.image_models import AssetImportLocalRequest, AssetReviewRequest
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    CreateProjectRequest,
    Freshness,
    SourceMode,
)
from short_drama.production_planner import (
    build_project_document_from_plan,
    create_production_plan,
)
from short_drama.service import ShortDramaService


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

    assert missing
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

    searched = client.post(
        "/api/short-drama/asset-agent/tools/search-assets",
        params={"current_project_id": target.document.project.id},
        json={
            "query": source_asset.name,
            "kinds": ["CHARACTER"],
            "roles": ["CHARACTER_BODY"],
            "include_current_project": False,
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

