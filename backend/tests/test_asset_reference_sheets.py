"""Structured core-asset briefs and deterministic local reference-sheet Tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReferenceSheetCellRequest,
    AssetReferenceSheetComposeRequest,
)
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    CoreAssetBrief,
    OriginType,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app_data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _fixture_character(service: ShortDramaService):
    project = service.initialize_fixture()
    return next(
        asset
        for asset in project.document.assets
        if asset.kind == AssetKind.CHARACTER
    )


def _import_solid_image(
    service: ShortDramaService,
    *,
    project_id: str,
    asset_id: str,
    path: Path,
    color: str,
    descriptor: str,
):
    Image.new("RGB", (96, 96), color).save(path, format="PNG")
    return service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(
            local_path=str(path),
            descriptor=descriptor,
        ),
    ).version


def test_asset_tools_persist_structured_briefs_without_forcing_heavy_views(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id

    created = service.tool_registry.invoke(
        "artifact.create_asset",
        {
            "project_id": project_id,
            "request": AssetCreateRequest(
                kind=AssetKind.CHARACTER,
                name="学生群像",
                descriptor="用于食堂背景的普通高中生群像",
                brief=CoreAssetBrief(
                    identity_anchor="16-18 岁的中学生群像，无固定主角身份",
                    tone_or_temperament="自然、日常、略显嘈杂",
                    visual_attributes=["年龄感真实", "群体外观有差异"],
                    wardrobe_or_materials=["蓝白运动校服"],
                    background_constraints=["与学校食堂空间兼容"],
                    texture_constraints=["无明显 AI 塑料感"],
                    continuity_anchors=["不抢主角注意力"],
                    reference_strategy=AssetReferenceStrategy.LIGHTWEIGHT,
                    requested_views=[],
                    strategy_rationale="群像仅用于背景，不需要四视图。",
                ),
            ).model_dump(mode="json"),
        },
    )

    assert created.status == ToolCallStatus.SUCCEEDED
    assert created.output is not None
    assert created.output["asset"]["brief"]["reference_strategy"] == "LIGHTWEIGHT"
    assert created.output["asset"]["brief"]["requested_views"] == []

    target = _fixture_character(service)
    updated = service.tool_registry.invoke(
        "artifact.update_brief",
        {
            "project_id": target.project_id,
            "asset_id": target.id,
            "request": {
                "brief": {
                    "identity_anchor": "18 岁男高中生，五官清秀，黑色碎发",
                    "tone_or_temperament": "清爽、专注",
                    "visual_attributes": ["发量浓密蓬松", "目光直视镜头"],
                    "wardrobe_or_materials": ["白色高中短袖运动校服"],
                    "background_constraints": ["纯白背景"],
                    "texture_constraints": ["无颗粒感", "轻雾感"],
                    "continuity_anchors": ["黑色碎发与白色校服始终不变"],
                    "reference_strategy": "CHARACTER_STANDARD_VIEWS",
                    "requested_views": ["CHARACTER_FRONT", "CHARACTER_LEFT_PROFILE"],
                    "strategy_rationale": "主角跨多镜头，先做必要视角，不把四视图作为硬门槛。",
                }
            },
        },
    )
    assert updated.status == ToolCallStatus.SUCCEEDED
    assert updated.output is not None
    assert updated.output["asset"]["brief"]["requested_views"] == [
        "CHARACTER_FRONT",
        "CHARACTER_LEFT_PROFILE",
    ]


def test_compose_reference_sheet_creates_traceable_draft_without_provider(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    target = _fixture_character(service)
    views = [
        (AssetStandardView.CHARACTER_FRONT, "red", "正面"),
        (AssetStandardView.CHARACTER_LEFT_PROFILE, "green", "左侧面"),
        (AssetStandardView.CHARACTER_RIGHT_PROFILE, "blue", "右侧面"),
        (AssetStandardView.CHARACTER_BACK, "yellow", "背面"),
    ]
    versions = [
        _import_solid_image(
            service,
            project_id=target.project_id,
            asset_id=target.id,
            path=tmp_path / f"view-{index}.png",
            color=color,
            descriptor=label,
        )
        for index, (_view, color, label) in enumerate(views)
    ]

    arguments = {
        "project_id": target.project_id,
        "asset_id": target.id,
        "request": {
            "strategy": "CHARACTER_STANDARD_VIEWS",
            "cells": [
                {
                    "asset_version_id": version.id,
                    "view": view.value,
                    "label": label,
                }
                for (view, _color, label), version in zip(
                    views,
                    versions,
                    strict=True,
                )
            ],
            "descriptor": "男主标准四视图参考板",
            "input_policy": "EXPLICIT_VERSIONS",
            "columns": 2,
        },
    }
    result = service.tool_registry.invoke(
        "image.compose_reference_sheet",
        arguments,
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    version_payload = result.output["version"]
    assert version_payload["origin_type"] == OriginType.LOCAL_DERIVED.value
    assert version_payload["approval_status"] == ApprovalStatus.DRAFT.value
    assert version_payload["reference_sheet"]["strategy"] == (
        AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS.value
    )
    assert [
        cell["source_version_id"]
        for cell in version_payload["reference_sheet"]["cells"]
    ] == [version.id for version in versions]
    assert {reference.artifact_type for reference in result.artifact_refs} == {
        "asset",
        "asset_version",
    }

    output_path = Path(str(version_payload["local_path"]))
    assert output_path.is_file()
    with Image.open(output_path).convert("RGB") as sheet:
        assert sheet.size == (1072, 1152)
        assert sheet.getpixel((272, 272)) == (255, 0, 0)
        assert sheet.getpixel((800, 272)) == (0, 128, 0)
        assert sheet.getpixel((272, 840)) == (0, 0, 255)
        assert sheet.getpixel((800, 840)) == (255, 255, 0)

    repeated = service.tool_registry.invoke(
        "image.compose_reference_sheet",
        arguments,
    )
    assert repeated.status == ToolCallStatus.SUCCEEDED
    assert repeated.output is not None
    repeated_path = Path(str(repeated.output["version"]["local_path"]))
    assert repeated_path.read_bytes() == output_path.read_bytes()

    persisted = service.get_project(target.project_id)
    persisted_version = next(
        version
        for asset in persisted.document.assets
        for version in asset.versions
        if version.id == version_payload["id"]
    )
    assert persisted_version.reference_sheet is not None
    assert persisted_version.reference_sheet.columns == 2


def test_reference_sheet_policy_and_strategy_reject_mismatched_inputs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    target = _fixture_character(service)
    first = _import_solid_image(
        service,
        project_id=target.project_id,
        asset_id=target.id,
        path=tmp_path / "draft-a.png",
        color="red",
        descriptor="draft a",
    )
    second = _import_solid_image(
        service,
        project_id=target.project_id,
        asset_id=target.id,
        path=tmp_path / "draft-b.png",
        color="blue",
        descriptor="draft b",
    )

    with pytest.raises(WorkflowConflictError, match="APPROVED_ONLY"):
        service.compose_asset_reference_sheet(
            target.project_id,
            target.id,
            AssetReferenceSheetComposeRequest(
                strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
                cells=[
                    AssetReferenceSheetCellRequest(
                        asset_version_id=first.id,
                        view=AssetStandardView.CHARACTER_FRONT,
                    ),
                    AssetReferenceSheetCellRequest(
                        asset_version_id=second.id,
                        view=AssetStandardView.CHARACTER_LEFT_PROFILE,
                    ),
                ],
                descriptor="strict approval sheet",
                input_policy=AssetSheetInputPolicy.APPROVED_ONLY,
            ),
        )

    with pytest.raises(WorkflowConflictError, match="only be used for a character"):
        service.create_asset(
            target.project_id,
            AssetCreateRequest(
                kind=AssetKind.LOCATION,
                name="食堂",
                descriptor="食堂场景",
                brief=CoreAssetBrief(
                    identity_anchor="中学食堂",
                    visual_attributes=["打饭窗口、用餐区和通道关系明确"],
                    reference_strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
                ),
            ),
        )
