"""Actual pixel and Agent contract checks for local asset crops and detailed review."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from short_drama.asset_inspection import AssetVersionInspectionRequest
from short_drama.image_models import (
    AssetImageCropRequest, AssetImportLocalRequest, ImageGenerationTask,
    ImageOperation, ImageProviderRequest, ImageRegion, ImageTaskStatus,
    ImageTaskCreateRequest, ImageReferenceInput,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ApprovalStatus, AssetKind, AssetLayoutProfile, AssetReferenceStrategy,
    CoreAssetBrief, Freshness, ImageReferenceRole,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_short_drama_assets import FakeImageProvider, _service as image_service


def _setup(tmp_path: Path):
    language = CapturingLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language,
    )
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    asset = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    path = tmp_path / "source.png"
    image = Image.new("RGBA", (400, 200), (80, 80, 80, 0))
    image.paste((255, 20, 40, 192), (100, 0, 200, 100))
    image.save(path)
    version = service.import_local_asset_version(
        document.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="四格中的第二格脸部"),
    ).version
    return service, language, document.project.id, asset.id, version


def test_crop_tool_preserves_actual_pixels_alpha_and_records_source(tmp_path: Path) -> None:
    service, _language, project_id, asset_id, source = _setup(tmp_path)
    before = service.get_project(project_id).document
    asset_before = next(a for a in before.assets if a.id == asset_id)
    source_bytes = Path(source.local_path or "").read_bytes()
    result = service.tool_registry.invoke("image.crop", {
        "project_id": project_id, "asset_id": asset_id,
        "request": {"source_asset_version_id": source.id,
                    "region": {"x": .25, "y": 0, "width": .25, "height": .5},
                    "descriptor": "同一人物的脸部局部", "reference_role": "SUBJECT_IDENTITY"},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    saved = result.output["version"]
    assert saved["image_crop"] == {
        "source_asset_version_id": source.id, "source_width": 400, "source_height": 200,
        "box_pixels": [100, 0, 200, 100],
    }
    with Image.open(saved["local_path"]) as cropped:
        assert cropped.size == (100, 100)
        assert cropped.mode == "RGBA"
        assert cropped.getpixel((30, 30)) == (255, 20, 40, 192)
    assert Path(source.local_path or "").read_bytes() == source_bytes
    assert saved["approval_status"] == "DRAFT"
    assert saved["parent_version_id"] == source.id
    assert saved["derivation_references"][0]["reference_role"] == "SUBJECT_IDENTITY"
    assert result.output["asset"]["approved_version_id"] == asset_before.approved_version_id
    assert service.list_image_tasks(project_id).tasks == []
    assert any(ref.artifact_id == saved["id"] for ref in result.artifact_refs)
    after = service.get_project(project_id).document
    assert after.shots == before.shots
    assert after.reference_packs == before.reference_packs


def test_crop_coordinates_follow_exif_visible_orientation(tmp_path: Path) -> None:
    service, _language, project_id, asset_id, _source = _setup(tmp_path)
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (40, 80), "red")
    image.paste("blue", (0, 40, 40, 80))
    exif = image.getexif()
    exif[274] = 6  # Visible image: 80 wide, 40 high; blue on left, red on right.
    image.save(path, exif=exif)
    source = service.import_local_asset_version(project_id, asset_id, AssetImportLocalRequest(
        local_path=str(path), descriptor="旋转的源图",
    )).version
    cropped = service.crop_asset_image(project_id, asset_id, AssetImageCropRequest(
        source_asset_version_id=source.id, region=ImageRegion(x=0, y=0, width=.5, height=1),
        descriptor="可见图左半幅", reference_role=ImageReferenceRole.STYLE,
    )).version
    assert cropped.image_crop is not None
    assert (cropped.image_crop.source_width, cropped.image_crop.source_height) == (80, 40)
    with Image.open(cropped.local_path or "") as result:
        assert result.size == (40, 40)
        pixel = result.convert("RGB").getpixel((5, 5))
        assert isinstance(pixel, tuple) and pixel[2] > 200 and pixel[0] < 30


@pytest.mark.parametrize("region", [
    {"x": .9, "y": 0, "width": .2, "height": 1},
    {"x": 0, "y": 0, "width": 0, "height": 1},
    {"x": float("nan"), "y": 0, "width": .2, "height": 1},
])
def test_outside_empty_or_nonfinite_region_rejected(region: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ImageRegion.model_validate(region)


@pytest.mark.parametrize("unusable", ["REJECTED", "STALE", "MISSING", "EMPTY"])
def test_crop_rejects_unusable_source_without_writing_version(tmp_path: Path, unusable: str) -> None:
    service, _language, project_id, asset_id, source = _setup(tmp_path)
    document = service.get_project(project_id).document
    asset = next(a for a in document.assets if a.id == asset_id)
    stored = next(v for v in asset.versions if v.id == source.id)
    if unusable == "REJECTED":
        stored.approval_status = ApprovalStatus.REJECTED
    elif unusable == "STALE":
        stored.freshness = Freshness.STALE
    elif unusable == "MISSING":
        Path(source.local_path or "").unlink()
    else:
        Path(source.local_path or "").write_bytes(b"")
    service.repository.save_project(document)
    count = len(asset.versions)
    with pytest.raises(WorkflowConflictError):
        service.crop_asset_image(project_id, asset_id, AssetImageCropRequest(
            source_asset_version_id=source.id, region=ImageRegion(x=0, y=0, width=.5, height=.5),
            descriptor="局部", reference_role=ImageReferenceRole.SUBJECT_IDENTITY,
        ))
    assert len(next(a for a in service.get_project(project_id).document.assets if a.id == asset_id).versions) == count


def test_review_reads_actual_single_output_intent_and_adds_detail_in_one_call(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path)
    document = service.get_project(project_id).document
    asset = next(a for a in document.assets if a.id == asset_id)
    asset.brief = CoreAssetBrief(
        identity_anchor="同一演员", visual_attributes=["同一鼻形和发际"],
        reference_strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
        layout_profile=AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1,
    )
    stored = next(v for v in asset.versions if v.id == version.id)
    stored.image_task_id = "image-task-single-face"
    service.repository.save_project(document)
    task = ImageGenerationTask(
        id=stored.image_task_id, project_id=project_id, asset_id=asset_id,
        output_version_id=version.id, output_version_number=version.version,
        descriptor="仅正脸近景，最终卡由其他视图组成", operation=ImageOperation.GENERATE,
        status=ImageTaskStatus.SUCCEEDED,
        request=ImageProviderRequest(operation=ImageOperation.GENERATE, prompt="只生成正脸近景，保留鼻形和发际"),
        created_at=version.created_at, updated_at=version.created_at,
    )
    service.repository.save_image_task(task)
    result = service.inspect_asset_version(project_id, asset_id, version.id, AssetVersionInspectionRequest.model_validate({
        "review_focus": "本张正脸是否清楚，不审核尚未组合的背面",
        "regions": [{"label": "脸部", "region": {"x": .25, "y": 0, "width": .25, "height": .5}}],
    }))
    payload = json.loads(language.input_text)
    assert payload["asset"]["brief"]["layout_profile"] == "CHARACTER_FOUR_VIEW_4X1"
    assert payload["candidate_creation"]["output_layout_profile"] == "LIGHTWEIGHT_SINGLE"
    assert payload["candidate_creation"]["generation_prompt"] == task.request.prompt
    assert len(language.image_urls) == 2
    detail = Image.open(BytesIO(base64.b64decode(language.image_urls[1].split(",", 1)[1])))
    assert detail.size == (100, 100)
    assert payload["image_map"][-1]["role"] == "DETAIL_OF_EXISTING_IMAGE"
    assert payload["image_map"][-1]["image"] == 2
    assert result.candidate_output_layout == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
    assert result.region_evidence[0].box_pixels == (100, 0, 200, 100)
    assert result.review_focus == payload["review_focus"]
    assert "总体brief列四视图" in language.instructions


def test_unknown_region_reference_fails_before_language_call(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path)
    result = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": project_id, "asset_id": asset_id, "version_id": version.id,
        "request": {"regions": [{"asset_version_id": "not-an-evidence-image", "label": "脸部",
                                 "region": {"x": 0, "y": 0, "width": .5, "height": .5}}]},
    })
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None
    assert "not-an-evidence-image" in result.error.message
    assert language.input_text == ""


def test_draft_crop_is_the_actual_ordered_provider_input_without_full_sheet(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    service = image_service(tmp_path, provider)
    document = service.initialize_fixture().document
    asset = next(a for a in document.assets if a.kind == AssetKind.CHARACTER)
    source_path = tmp_path / "two-views.png"
    sheet = Image.new("RGB", (400, 200), "blue")
    sheet.paste("red", (200, 0, 400, 200))
    sheet.save(source_path)
    source = service.import_local_asset_version(document.project.id, asset.id, AssetImportLocalRequest(
        local_path=str(source_path), descriptor="右侧为可用脸部",
    )).version
    crop = service.crop_asset_image(document.project.id, asset.id, AssetImageCropRequest(
        source_asset_version_id=source.id, region=ImageRegion(x=.5, y=0, width=.5, height=1),
        descriptor="右侧身份参考", reference_role=ImageReferenceRole.SUBJECT_IDENTITY,
    )).version
    task = service.enqueue_image_task(document.project.id, asset.id, ImageTaskCreateRequest(
        operation=ImageOperation.REFERENCE_GENERATE, prompt="使用明确脸部身份参考，制作同一演员的侧面近景。",
        descriptor="侧面近景", reference_inputs=[ImageReferenceInput(
            asset_version_id=crop.id, reference_role=ImageReferenceRole.SUBJECT_IDENTITY,
            purpose="唯一身份来源", subject_alias=asset.name,
        )],
    )).task
    service.advance_image_task(task.id)
    finished = service.advance_image_task(task.id).task
    assert finished.status == ImageTaskStatus.SUCCEEDED
    assert len(provider.calls) == 1
    inputs = provider.calls[0][1]
    assert [entry.asset_version_id for entry in inputs] == [crop.id]
    assert inputs[0].reference_role == ImageReferenceRole.SUBJECT_IDENTITY
    with Image.open(BytesIO(base64.b64decode(inputs[0].image_url.split(",", 1)[1]))) as actual:
        assert actual.size == (200, 200)
        assert actual.getpixel((50, 50)) == (255, 0, 0)
    result = next(v for a in service.get_project(document.project.id).document.assets
                  for v in a.versions if v.id == finished.output_version_id)
    assert result.derivation_references[0].asset_version_id == crop.id
