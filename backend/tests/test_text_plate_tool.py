"""Deterministic exact-copy text plate execution."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from short_drama.image_models import AssetCreateRequest
from short_drama.models import (
    AssetKind,
    AssetPresentationMode,
    CoreAssetBrief,
    OriginType,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _create_plate_asset(
    service: ShortDramaService,
    *,
    project_id: str,
    presentation_mode: AssetPresentationMode,
):
    return service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.PROP,
            name="食堂价格牌文字层",
            descriptor="用于后期贴入食堂菜价牌的精确文字图版",
            brief=CoreAssetBrief(
                identity_anchor="当日食堂菜价牌的平面文字内容",
                visual_attributes=["深色清晰文字", "高对比底色"],
                presentation_mode=presentation_mode,
                required_visible_text=[
                    "今日菜单",
                    "番茄炒蛋 5元",
                    "酸辣土豆丝 3元",
                ],
            ),
        ),
    )


def _arguments(project_id: str, asset_id: str) -> dict[str, object]:
    return {
        "project_id": project_id,
        "asset_id": asset_id,
        "request": {
            "size": "1200x600",
            "background_color": "#F5F1E8",
            "foreground_color": "#171717",
            "title": "今日菜单",
            "exact_lines": ["番茄炒蛋 5元", "酸辣土豆丝 3元"],
            "layout_description": "标题居中，两行菜名与价格左对齐。",
            "descriptor": "食堂菜价牌精确文字图版",
        },
    }


def test_compose_text_plate_renders_chinese_without_provider_task(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    created = _create_plate_asset(
        service,
        project_id=project_id,
        presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
    )

    result = service.tool_registry.invoke(
        "image.compose_text_plate",
        _arguments(project_id, created.asset.id),
    )

    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    payload = result.output["version"]
    assert payload["origin_type"] == OriginType.LOCAL_DERIVED.value
    assert payload["version"] == 2
    assert payload["parent_version_id"] is None
    assert payload["image_task_id"] is None
    assert payload["text_plate"]["title"] == "今日菜单"
    assert payload["text_plate"]["exact_lines"] == [
        "番茄炒蛋 5元",
        "酸辣土豆丝 3元",
    ]
    assert payload["text_plate"]["font_family"]
    assert {reference.artifact_type for reference in result.artifact_refs} == {
        "asset",
        "asset_version",
    }

    output_path = Path(str(payload["local_path"]))
    assert output_path.is_file()
    with Image.open(output_path).convert("RGB") as rendered:
        assert rendered.size == (1200, 600)
        flat_background = Image.new("RGB", rendered.size, "#F5F1E8")
        difference = ImageChops.difference(rendered, flat_background)
        assert difference.getbbox() is not None

    assert service.list_image_tasks(project_id).tasks == []
    persisted = service.get_project(project_id)
    persisted_asset = next(
        asset for asset in persisted.document.assets if asset.id == created.asset.id
    )
    assert persisted_asset.current_version_id == payload["id"]
    assert persisted_asset.versions[-1].text_plate is not None


def test_text_plate_preserves_alpha_and_explicit_top_content_box(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    created = _create_plate_asset(service, project_id=project_id,
        presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE)
    arguments = _arguments(project_id, created.asset.id)
    request = arguments["request"]
    assert isinstance(request, dict)
    request.update(size="720x1280", background_color="#00000000", foreground_color="#FFFFFF",
                   content_box=[0.05, 0.04, 0.9, 0.24], text_align="center",
                   vertical_align="top", stroke_width=2, stroke_color="#000000")
    result = service.tool_registry.invoke("image.compose_text_plate", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    version = result.output["version"]
    with Image.open(version["local_path"]) as plate:
        assert plate.mode == "RGBA"
        alpha = plate.getchannel("A")
        bounds = alpha.getbbox()
        assert bounds is not None
        assert 36 <= bounds[0] < bounds[2] <= 684
        assert 50 <= bounds[1] < bounds[3] <= 359
        assert alpha.getpixel((360, 700)) == 0
        assert alpha.getextrema() == (0, 255)
    assert version["text_plate"]["content_box"] == [0.05, 0.04, 0.9, 0.24]


def test_price_label_uses_the_requested_region_instead_of_poster_spacing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    created = service.create_asset(project_id, AssetCreateRequest(
        kind=AssetKind.PROP, name="菜价字幕", descriptor="当前镜头的菜价图版",
        brief=CoreAssetBrief(identity_anchor="准确菜价", visual_attributes=["白字透明底"],
            presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
            required_visible_text=["土豆烧肉8元"]),
    ))
    for title in (None, "土豆烧肉8元"):
        arguments = _arguments(project_id, created.asset.id)
        request = arguments["request"]
        assert isinstance(request, dict)
        request.update(size="768x1344", background_color="#00000000", foreground_color="#FFFFFF",
            content_box=[0.42, 0.13, 0.52, 0.085], text_align="left", vertical_align="top",
            stroke_width=2, stroke_color="#202020", title=title,
            exact_lines=[] if title else ["土豆烧肉8元"])
        result = service.tool_registry.invoke("image.compose_text_plate", arguments)
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        assert result.output is not None
        with Image.open(result.output["version"]["local_path"]) as image:
            bounds = image.getchannel("A").getbbox()
            assert bounds is not None
            assert bounds[2] - bounds[0] > 250
            assert bounds[3] - bounds[1] > 40
            assert 322 <= bounds[0] < bounds[2] <= 722
            assert 174 <= bounds[1] < bounds[3] <= 290


def test_text_plate_can_reflow_lines_without_changing_the_declared_words(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pid = service.initialize_fixture().document.project.id
    created = service.create_asset(pid, AssetCreateRequest(kind=AssetKind.PROP,
        name="付款提示", descriptor="付款金额文字",
        brief=CoreAssetBrief(identity_anchor="正常付款", visual_attributes=["清晰文字"],
            presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
            required_visible_text=["支付成功，", "13.00元"])))
    arguments = _arguments(pid, created.asset.id)
    request = arguments["request"]
    assert isinstance(request, dict)
    request.update(title="支付成功，13.00元", exact_lines=[])
    rendered = service.tool_registry.invoke("image.compose_text_plate", arguments)
    assert rendered.status == ToolCallStatus.SUCCEEDED, rendered.error
    assert rendered.output and Path(rendered.output["version"]["local_path"]).is_file()
    request["title"] = "支付成功，13.10元"
    mismatch = service.tool_registry.invoke("image.compose_text_plate", arguments)
    assert mismatch.status == ToolCallStatus.FAILED
    assert mismatch.error and "13.00元" in mismatch.error.message and "13.10元" in mismatch.error.message


def test_compose_text_plate_rejects_non_screen_asset_and_copy_mismatch(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    physical = _create_plate_asset(
        service,
        project_id=project_id,
        presentation_mode=AssetPresentationMode.PHYSICAL_MASTER,
    )
    non_screen = service.tool_registry.invoke(
        "image.compose_text_plate",
        _arguments(project_id, physical.asset.id),
    )
    assert non_screen.status == ToolCallStatus.FAILED, non_screen.error
    assert non_screen.error is not None
    assert "SCREEN_CONTENT_PLATE" in non_screen.error.message

    plate = _create_plate_asset(
        service,
        project_id=project_id,
        presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
    )
    mismatch_arguments = _arguments(project_id, plate.asset.id)
    request = mismatch_arguments["request"]
    assert isinstance(request, dict)
    request["exact_lines"] = ["番茄炒蛋 6元", "酸辣土豆丝 3元"]
    mismatch = service.tool_registry.invoke(
        "image.compose_text_plate",
        mismatch_arguments,
    )
    assert mismatch.status == ToolCallStatus.FAILED
    assert mismatch.error is not None
    assert "exactly match" in mismatch.error.message
    assert service.list_image_tasks(project_id).tasks == []


def test_compose_text_plate_preserves_repeated_visible_lines(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    created = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.PROP,
            name="重复文字牌",
            descriptor="用于剧情中需要逐行保留重复文案的屏幕内容",
            brief=CoreAssetBrief(
                identity_anchor="一张包含两行相同文字的提示牌",
                visual_attributes=["高对比底色", "逐行精确文字"],
                presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
                required_visible_text=["请排队", "请排队"],
            ),
        ),
    )
    arguments = _arguments(project_id, created.asset.id)
    request = arguments["request"]
    assert isinstance(request, dict)
    request["title"] = None
    request["exact_lines"] = ["请排队", "请排队"]

    result = service.tool_registry.invoke("image.compose_text_plate", arguments)

    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    assert result.output["version"]["text_plate"]["exact_lines"] == [
        "请排队",
        "请排队",
    ]
