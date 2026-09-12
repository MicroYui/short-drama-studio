"""The actual s04 QA declarations reach the Tool with exact reference context."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from short_drama.image_models import ImageGenerationTask, ImageOperation, ImageProviderRequest, ImageTaskStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ApprovalStatus, Asset, AssetDerivationReference, AssetKind, AssetMediaType,
    AssetVersion, CoreAssetBrief, Freshness, OriginType, ShotPlan,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient


FIXTURE: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/asset_inspection_spatial_context.json").read_text())


@pytest.mark.parametrize("explicit_references", [False, True])
def test_exact_saved_spatial_sources_and_current_shot_reach_real_qa_tool(
    tmp_path: Path, explicit_references: bool,
) -> None:
    language = CapturingLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "qa.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    pid = document.project.id
    assets: dict[str, Asset] = {}
    candidate: AssetVersion | None = None
    candidate_asset_id = ""
    for index, reference in enumerate(FIXTURE["references"]):
        mapped = reference["image_map"]
        path = tmp_path / f"image-{index}.png"
        Image.new("RGB", (160, 96), (30 + index * 35, 90, 120)).save(path)
        version = AssetVersion(id=mapped["version_id"], version=reference["version"],
            origin_type=OriginType.MANUAL, descriptor=reference["descriptor"],
            media_type=AssetMediaType.IMAGE, local_path=str(path), width=160, height=96,
            approval_status=ApprovalStatus(reference["approval_status"]), freshness=Freshness.CURRENT,
            created_at="2026-09-11T22:23:34.139+00:00")
        asset = assets.get(mapped["asset_id"])
        if asset is None:
            asset = Asset(id=mapped["asset_id"], project_id=pid, kind=AssetKind(reference["asset_kind"]),
                name=mapped["asset_name"], current_version_id=version.id, versions=[])
            assets[asset.id] = asset
        asset.versions.append(version)
        if index == 0:
            candidate, candidate_asset_id = version, asset.id
            asset.brief = CoreAssetBrief.model_validate(FIXTURE["candidate_brief"])
        elif version.approval_status == ApprovalStatus.APPROVED:
            asset.approved_version_id = version.id
    assert candidate is not None
    refs = FIXTURE["references"][1:]
    candidate.derivation_references = [AssetDerivationReference(
        asset_version_id=entry["image_map"]["version_id"], source_project_id=pid,
        reference_role=entry["image_map"]["reference_role"],
        reference_authority=entry["image_map"]["reference_authority"],
        purpose=entry["image_map"]["purpose"], subject_alias=entry["image_map"]["subject_alias"],
    ) for entry in refs]
    room = next(asset for asset in assets.values() if asset.kind == AssetKind.LOCATION)
    room.brief = CoreAssetBrief(identity_anchor="UNRELATED_CURRENT_BRIEF_NOT_AN_EVIDENCE_SOURCE",
        visual_attributes=["UNRELATED_CURRENT_ROOM_LAYOUT"])
    newer_room = room.versions[0].model_copy(deep=True, update={
        "id": "newer-room-image", "version": room.versions[0].version + 1,
        "descriptor": "UNSELECTED_LATER_VIEW_NOT_THE_REFERENCE"})
    room.versions.append(newer_room)
    room.current_version_id = newer_room.id
    shot = ShotPlan.model_validate({**FIXTURE["shot"], "project_id": pid})
    document.assets = list(assets.values())
    document.shots = [shot]
    candidate.image_task_id = "exact-s04-image-task"
    service.repository.save_project(document)
    task = ImageGenerationTask(id=candidate.image_task_id, project_id=pid, asset_id=candidate_asset_id,
        output_version_id=candidate.id, output_version_number=candidate.version,
        descriptor=candidate.descriptor, operation=ImageOperation.REFERENCE_GENERATE,
        status=ImageTaskStatus.SUCCEEDED,
        request=ImageProviderRequest(operation=ImageOperation.REFERENCE_GENERATE,
            prompt=FIXTURE["candidate_creation"]["generation_prompt"],
            input_version_ids=[entry["image_map"]["version_id"] for entry in refs]),
        created_at=candidate.created_at, updated_at=candidate.created_at)
    service.repository.save_image_task(task)
    before = service.get_project(pid).document.model_dump(mode="json")
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": pid, "asset_id": candidate_asset_id, "version_id": candidate.id,
        "request": {"shot_id": shot.id, "review_focus": FIXTURE["review_focus"],
            "canonical_reference_version_ids": [entry["image_map"]["version_id"] for entry in refs] if explicit_references else []}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    payload = json.loads(language.input_text)
    assert payload["shot"] == shot.model_dump(mode="json")
    assert payload["candidate_creation"]["generation_prompt"] == task.request.prompt
    assert payload["candidate_creation"]["output_layout_profile"] == "LIGHTWEIGHT_SINGLE"
    assert payload["review_focus"] == FIXTURE["review_focus"]
    assert len(payload["image_map"]) == len(language.image_urls) == 5
    for index, entry in enumerate(refs, start=1):
        actual = payload["image_map"][index]
        assert actual["version_id"] == entry["image_map"]["version_id"]
        assert actual["descriptor"] == entry["descriptor"]
        for key in ("reference_role", "reference_authority", "purpose", "subject_alias"):
            assert actual[key] == entry["image_map"][key]
    for index, url in enumerate(language.image_urls):
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as decoded:
            assert decoded.size == (160, 96)
            red, green, blue = decoded.getpixel((80, 48))
            assert abs(red - (30 + index * 35)) <= 2 and abs(green - 90) <= 2 and abs(blue - 120) <= 2
    assert "南侧中央向北" in payload["image_map"][2]["descriptor"]
    assert "UNRELATED_CURRENT_BRIEF" not in language.input_text
    assert "UNSELECTED_LATER_VIEW" not in language.input_text
    assert "资产验收的通用职责" in language.instructions
    assert "旧版资产职责" not in language.instructions
    assert "空间先列本图可见" in language.instructions
    assert "本机位预期可见的背景方位" in language.instructions
    assert "分别报告可见矛盾与无法判定" in language.instructions
    assert "不是当前候选已符合要求的结论" in language.instructions
    assert "不把 Asset 总体四视图要求套到单张脸部/侧面图" in language.instructions
    assert "若某空间关系必须可见却无法辨认，明确它不能作为该职责已满足的依据" in language.instructions
    assert service.get_project(pid).document.model_dump(mode="json") == before
