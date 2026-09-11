"""Real failed document requests, precise local layout, and physical carrier integrity."""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from short_drama.asset_inspection import inspect_asset_image
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest
from short_drama.models import AssetKind, AssetPresentationMode, CoreAssetBrief
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient


WORDS = ["情况说明", "本次报价出现错误，现补充核对。", "经办", "复核"]


def _setup(tmp_path: Path, *, physical: bool = False, words: list[str] | None = None):
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    pid = service.initialize_fixture().document.project.id
    created = service.create_asset(pid, AssetCreateRequest(kind=AssetKind.PROP, name="情况说明",
        descriptor="白色A4纸，上部正文、下半部两个空白签字栏。",
        brief=CoreAssetBrief(identity_anchor="同一张情况说明", visual_attributes=["普通哑光白纸"],
            presentation_mode=AssetPresentationMode.PHYSICAL_MASTER if physical else AssetPresentationMode.SCREEN_CONTENT_PLATE,
            required_visible_text=words or WORDS)))
    return service, pid, created.asset.id


def _request() -> dict[str, object]:
    return {"size": "1536x2176", "background_color": "#FFFFFF", "foreground_color": "#191919",
        "content_box": [0.12, 0.08, 0.76, 0.84], "text_align": "left", "vertical_align": "top",
        "title": WORDS[0], "exact_lines": [WORDS[1], "经办：________", "复核：________"],
        "layout_description": "本地准确文字底稿，正文居上、签字栏位于下半部。", "descriptor": "双空白状态的准确文字内容。"}


def _invoke(service: ShortDramaService, pid: str, aid: str, request: dict[str, object]):
    return service.tool_registry.invoke("image.compose_text_plate", {"project_id": pid, "asset_id": aid, "request": request})


def _bottom_ink(path: str) -> int:
    with Image.open(path).convert("RGB") as image:
        bounds = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
        assert bounds is not None
        return bounds[3]


def test_actual_failed_empty_rows_render_and_remain_layout_only(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _request()
    request["exact_lines"] = ["", "", WORDS[1], "", "", "", "", "", "经办：________________", "", "", "复核：________________"]
    spaced = _invoke(service, pid, aid, request)
    assert spaced.status == ToolCallStatus.SUCCEEDED, spaced.error
    assert spaced.output is not None
    assert spaced.output["version"]["text_plate"]["exact_lines"] == request["exact_lines"]
    compact = _invoke(service, pid, aid, _request())
    assert compact.status == ToolCallStatus.SUCCEEDED, compact.error
    assert compact.output is not None
    assert _bottom_ink(spaced.output["version"]["local_path"]) > _bottom_ink(compact.output["version"]["local_path"]) + 200
    assert service.list_image_tasks(pid).tasks == []
    asset = next(item for item in service.get_project(pid).document.assets if item.id == aid)
    assert asset.brief is not None and asset.brief.required_visible_text == WORDS


def test_actual_pending_no_empty_rows_request_accepts_label_punctuation_without_rewriting_brief(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _request()
    request.update(size="1440x2032", content_box=[0.1, 0.08, 0.8, 0.8])
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    assert result.output["asset"]["brief"]["required_visible_text"] == WORDS
    with Image.open(result.output["version"]["local_path"]) as image:
        assert image.size == (1440, 2032)


def _blocks_request() -> dict[str, object]:
    request = _request()
    request.update(title=None, exact_lines=[], text_blocks=[
        {"text": WORDS[0], "content_box": [0.15, 0.08, 0.7, 0.1], "font_size": 72, "text_align": "center"},
        {"text": WORDS[1], "content_box": [0.12, 0.25, 0.76, 0.08], "font_size": 45},
        {"text": "经办：", "content_box": [0.12, 0.58, 0.16, 0.06], "font_size": 48, "vertical_align": "center"},
        {"text": "复核：", "content_box": [0.12, 0.73, 0.16, 0.06], "font_size": 48, "vertical_align": "center"},
    ], lines=[
        {"start": [0.30, 0.622], "end": [0.86, 0.622], "width": 3},
        {"start": [0.30, 0.772], "end": [0.86, 0.772], "width": 3},
    ])
    return request


def test_explicit_blocks_and_signature_rules_use_actual_bounds_and_preserve_writing_space(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _blocks_request()
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    metadata = result.output["version"]["text_plate"]
    assert metadata["text_blocks"][2]["content_box"] == [0.12, 0.58, 0.16, 0.06]
    assert metadata["text_blocks"][0]["font_size"] == 72
    assert len(metadata["lines"]) == 2
    with Image.open(result.output["version"]["local_path"]).convert("RGB") as image:
        width, height = image.size
        assert image.getpixel((round(.5 * (width - 1)), round(.622 * (height - 1)))) == (25, 25, 25)
        writing_area = image.crop((round(.3 * width), round(.58 * height), round(.86 * width), round(.61 * height)))
        assert ImageChops.difference(writing_area, Image.new("RGB", writing_area.size, "white")).getbbox() is None
        assert ImageChops.difference(image.crop((0, round(.4 * height), width, round(.5 * height))), Image.new("RGB", (width, round(.5 * height) - round(.4 * height)), "white")).getbbox() is None


def test_block_overflow_identifies_the_exact_block_and_saves_no_partial_version(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _blocks_request()
    blocks = request["text_blocks"]
    assert isinstance(blocks, list)
    blocks[1]["content_box"] = [0.1, 0.25, 0.1, 0.01]
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None
    assert "/request/text_blocks/1" in result.error.message
    assert "font_size=45" in result.error.message and "No text was clipped" in result.error.message
    asset = next(item for item in service.get_project(pid).document.assets if item.id == aid)
    assert len(asset.versions) == 1
    assert not list((tmp_path / "workspace").rglob("*text-plate.png"))


@pytest.mark.parametrize("received", ["金额：13.10元", "金额：1300元", "金额：-13.00元", "金额：13.00%元", "金额：13.00元赵衡"])
def test_key_words_and_numeric_signs_cannot_be_disguised_as_layout(tmp_path: Path, received: str) -> None:
    service, pid, aid = _setup(tmp_path, words=["金额：13.00元"])
    request = _request()
    request.update(title=received, exact_lines=[])
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None and "13.00元" in result.error.message and received in result.error.message
    assert aid in result.error.message


@pytest.mark.parametrize("received", ["情况说明", "情况说名", "情况说明赵衡"])
def test_missing_changed_or_extra_words_remain_errors(tmp_path: Path, received: str) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _request()
    request.update(title=received, exact_lines=[])
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED


@pytest.mark.parametrize(("expected", "received"), [("don't", "dont"), ("AB/C", "ABC"), ("甲（乙）", "甲乙"), ("A_B", "AB"), ("金额：13.00元", "金额13.00元")])
def test_internal_punctuation_and_identifiers_are_not_layout_decoration(tmp_path: Path, expected: str, received: str) -> None:
    service, pid, aid = _setup(tmp_path, words=[expected])
    request = _request()
    request.update(title=received, exact_lines=[])
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None and expected in result.error.message


def test_invalid_geometry_and_line_break_errors_have_precise_field_paths(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path)
    request = _request()
    request["exact_lines"] = ["first\nsecond"]
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.INVALID_ARGUMENTS and result.error is not None
    assert result.error.details[0].path == "/request/exact_lines/0"
    request = _blocks_request()
    blocks = request["text_blocks"]
    assert isinstance(blocks, list)
    blocks[0]["content_box"] = [0.9, 0.2, 0.2, 0.1]
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.INVALID_ARGUMENTS and result.error is not None
    assert result.error.details[0].path == "/request/text_blocks/0/content_box"


def test_physical_surface_candidate_preserves_current_photo_and_brief(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    photo = tmp_path / "physical-carrier.png"
    Image.new("RGB", (256, 320), "lightgray").save(photo)
    original = service.import_local_asset_version(pid, aid, AssetImportLocalRequest(local_path=str(photo), descriptor="测试用已存物理载体媒体"))
    before = original.asset.model_dump(mode="json")
    result = _invoke(service, pid, aid, _blocks_request())
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    assert result.output["version"]["text_plate"]["purpose"] == "PHYSICAL_SURFACE_CONTENT"
    assert result.output["version"]["parent_version_id"] == original.version.id
    for key in ("current_version_id", "approved_version_id", "brief"):
        assert result.output["asset"][key] == before[key]
    assert result.output["asset"]["versions"][:-1] == before["versions"]
    assert service.list_image_tasks(pid).tasks == []


def test_physical_surface_inspection_uses_candidate_content_role_not_photo_requirements(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    result = _invoke(service, pid, aid, _blocks_request())
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
    document = service.get_project(pid).document
    asset = next(item for item in document.assets if item.id == aid)
    version = next(item for item in asset.versions if item.id == result.output["version"]["id"])
    client = CapturingLanguageClient()
    inspect_asset_image(document=document, asset=asset, version=version, client=client)
    payload = json.loads(client.input_text)
    assert payload["candidate_creation"]["text_plate"]["purpose"] == "PHYSICAL_SURFACE_CONTENT"
    assert payload["candidate_creation"]["text_plate"]["text_blocks"][2]["text"] == "经办："
    scope = payload["decision_guidance"]["asset_kind_scope"]
    assert "准确文字" in scope and "不要求本图出现纸张厚度" in scope
    assert "只审核道具的形状、尺度、材质" not in client.instructions
    assert len(client.image_urls) == 1
