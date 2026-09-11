"""A visual framing diagnosis can hand its exact candidate crop to the local tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from short_drama.asset_inspection import AssetVersionInspectionRequest, inspect_asset_image
from short_drama.image_models import AssetImportLocalRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, AssetKind, Freshness
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient


class CropLanguageClient(CapturingLanguageClient):
    def __init__(self, *, suggest: bool = True, tiny: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.suggest = suggest
        self.tiny = tiny

    def generate_text(
        self, *, instructions: str, input_text: str,
        image_urls: list[str] | None = None, max_output_tokens: int = 2200,
    ) -> str:
        super().generate_text(instructions=instructions, input_text=input_text,
                              image_urls=image_urls, max_output_tokens=max_output_tokens)
        self.calls += 1
        response = {
            "recommended": False, "score": 3.7,
            "issues": [{"code": "FRAMING_MISMATCH", "severity": "major",
                        "message": "裁去右侧多余内容后再判断用途。", "repair_layer": "image.crop"}],
            "summary": "源图有可用内容，当前构图尚未符合要求。",
        }
        if not self.suggest:
            return json.dumps(response, ensure_ascii=False)
        return json.dumps({**response, "crop_suggestion": {
            "region": {"x": 0, "y": 0, "width": .0025 if self.tiny else .5, "height": 1},
            "reason": "左侧保留所需主体，右侧是多余背景；不改变可见细节。",
            # Runtime source comes from the actual candidate, never model IDs.
            "source": {"source_asset_version_id": "wrong-reference-id", "source_width": 2,
                       "source_height": 2, "box_pixels": [0, 0, 1, 2]},
        }}, ensure_ascii=False)


def _setup(tmp_path: Path, *, rotated: bool = False, suggest: bool = True):
    language = CropLanguageClient(suggest=suggest)
    service = ShortDramaService(
        database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language,
    )
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    document = service.initialize_fixture().document
    asset = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    path = tmp_path / ("source.jpg" if rotated else "source.png")
    if rotated:
        image = Image.new("RGB", (40, 80), "red")
        image.paste("blue", (0, 40, 40, 80))
        exif = image.getexif()
        exif[274] = 6
        image.save(path, exif=exif)
    else:
        image = Image.new("RGBA", (400, 200), (0, 0, 255, 160))
        image.paste((255, 0, 0, 255), (200, 0, 400, 200))
        image.save(path)
    version = service.import_local_asset_version(document.project.id, asset.id, AssetImportLocalRequest(
        local_path=str(path), descriptor="本次待裁切构图",
    )).version
    return service, language, document.project.id, asset.id, version


def test_inspection_suggestion_runs_existing_crop_once_without_adopting_source(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path)
    before = service.get_project(project_id).document
    source_bytes = Path(version.local_path or "").read_bytes()
    review = service.tool_registry.invoke("qa.inspect_asset_version", {
        "project_id": project_id, "asset_id": asset_id, "version_id": version.id, "request": {},
    })
    assert review.status == ToolCallStatus.SUCCEEDED, review.error
    assert review.output is not None
    assert review.output["recommended"] is False
    assert review.output["score"] == 3.7
    suggestion = review.output["crop_suggestion"]
    assert isinstance(suggestion, dict)
    assert suggestion["source"] == {
        "source_asset_version_id": version.id, "source_width": 400, "source_height": 200,
        "box_pixels": [0, 0, 200, 200],
    }
    assert service.get_project(project_id).document.assets == before.assets
    assert language.calls == 1
    result = service.tool_registry.invoke("image.crop", {
        "project_id": project_id, "asset_id": asset_id,
        "request": {"source_asset_version_id": version.id, "region": suggestion["region"],
                    "descriptor": suggestion["reason"], "reference_role": "COMPOSITION"},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    saved = result.output["version"]
    assert saved["image_crop"] == suggestion["source"]
    with Image.open(saved["local_path"]) as actual:
        assert actual.size == (200, 200)
        assert actual.getpixel((100, 100)) == (0, 0, 255, 160)
    assert saved["approval_status"] == "DRAFT"
    assert Path(version.local_path or "").read_bytes() == source_bytes
    assert language.calls == 1
    assert service.list_image_tasks(project_id).tasks == []
    after = service.get_project(project_id).document
    assert after.shots == before.shots
    assert after.reference_packs == before.reference_packs


def test_suggestion_uses_exif_visible_dimensions_and_real_crop_pixels(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path, rotated=True)
    review = service.inspect_asset_version(project_id, asset_id, version.id, AssetVersionInspectionRequest())
    assert review.crop_suggestion is not None
    source = review.crop_suggestion.source
    assert source is not None
    assert (source.source_width, source.source_height, source.box_pixels) == (80, 40, (0, 0, 40, 40))
    assert json.loads(language.input_text)["candidate_creation"]["actual_size"] == {"width": 80, "height": 40}
    cropped = service.tool_registry.invoke("image.crop", {
        "project_id": project_id, "asset_id": asset_id,
        "request": {"source_asset_version_id": source.source_asset_version_id,
                    "region": review.crop_suggestion.region.model_dump(),
                    "descriptor": review.crop_suggestion.reason, "reference_role": "COMPOSITION"},
    })
    assert cropped.status == ToolCallStatus.SUCCEEDED, cropped.error
    assert cropped.output is not None
    with Image.open(cropped.output["version"]["local_path"]) as actual:
        assert actual.size == (40, 40)
        pixel = actual.convert("RGB").getpixel((5, 5))
        assert isinstance(pixel, tuple) and pixel[2] > 200 and pixel[0] < 30


def test_remote_candidate_does_not_offer_crop_against_different_local_pixels(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path)
    document = service.get_project(project_id).document
    asset = next(item for item in document.assets if item.id == asset_id)
    remote = version.model_copy(update={"external_url": "https://example.invalid/remote.png"})
    review = inspect_asset_image(document=document, asset=asset, version=remote, client=language)
    assert review.crop_suggestion is None
    assert review.recommended is False
    assert json.loads(language.input_text)["candidate_creation"]["local_crop_available"] is False


def test_legacy_review_without_suggestion_remains_valid(tmp_path: Path) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path, suggest=False)
    review = service.inspect_asset_version(project_id, asset_id, version.id, AssetVersionInspectionRequest())
    assert review.crop_suggestion is None
    assert review.recommended is False
    assert review.score == 3.7
    assert language.calls == 1


@pytest.mark.parametrize("ineligible", ["rejected", "stale", "one_pixel"])
def test_nonexecutable_suggestion_keeps_complete_review_without_an_extra_call(
    tmp_path: Path, ineligible: str,
) -> None:
    service, language, project_id, asset_id, version = _setup(tmp_path)
    document = service.get_project(project_id).document
    asset = next(item for item in document.assets if item.id == asset_id)
    if ineligible == "rejected":
        version = version.model_copy(update={"approval_status": ApprovalStatus.REJECTED})
    elif ineligible == "stale":
        version = version.model_copy(update={"freshness": Freshness.STALE})
    else:
        language.tiny = True
    review = inspect_asset_image(document=document, asset=asset, version=version, client=language)
    assert review.crop_suggestion is None
    assert review.recommended is False
    assert review.score == 3.7
    assert review.issues[0].code == "FRAMING_MISMATCH"
    assert language.calls == 1
