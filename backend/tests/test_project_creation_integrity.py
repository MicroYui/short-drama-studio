"""Creation integrity and persisted planning metadata coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from short_drama.models import (
    CreateProjectRequest,
    CreativeInputImage,
    PlanningRunStatus,
    ProductionProfile,
    ProjectDocument,
    RunMode,
    SourceMode,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(root: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
    )


def _request(name: str) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=name,
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=30,
        input_text="雨夜，一张旧车票让两个陌生人认出了彼此。",
        run_mode=RunMode.GUIDED,
    )


def test_missing_local_input_image_leaves_no_project_or_workspace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = "project-missing-input"
    request = CreateProjectRequest(
        name="缺失图片",
        source_mode=SourceMode.IMAGE_SEEDED,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=30,
        input_images=[
            CreativeInputImage(
                local_path=str(tmp_path / "does-not-exist.png"),
                descriptor="冷蓝夜景中的人物剪影",
            )
        ],
        run_mode=RunMode.GUIDED,
    )

    with pytest.raises(WorkflowConflictError, match="does not exist"):
        service.create_project(request, project_id=project_id)

    assert service.repository.get_project(project_id) is None
    assert service.repository.list_projects() == []
    assert not (tmp_path / "workspace" / project_id).exists()


def test_input_import_failure_compensates_the_inserted_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = "project-invalid-input-url"
    request = CreateProjectRequest(
        name="无效图片链接",
        source_mode=SourceMode.IMAGE_SEEDED,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=30,
        input_images=[
            CreativeInputImage(
                external_url="ftp://example.test/seed.png",
                descriptor="需要导入的视觉种子",
            )
        ],
        run_mode=RunMode.GUIDED,
    )

    with pytest.raises(WorkflowConflictError, match="HTTP or HTTPS"):
        service.create_project(request, project_id=project_id)

    assert service.repository.get_project(project_id) is None
    assert service.repository.list_projects() == []
    assert not (tmp_path / "workspace" / project_id).exists()


def test_partial_input_import_restores_the_document_and_removes_new_asset_files(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = "project-partial-input-import"
    baseline = service.create_project(_request("导入基线"), project_id=project_id)
    image_path = tmp_path / "valid-seed.png"
    Image.new("RGB", (32, 48), "#223344").save(image_path)
    import_request = CreateProjectRequest(
        name="两张图片",
        source_mode=SourceMode.IMAGE_SEEDED,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=30,
        input_images=[
            CreativeInputImage(
                local_path=str(image_path),
                descriptor="先成功导入的本地图片",
            ),
            CreativeInputImage(
                external_url="ftp://example.test/invalid.png",
                descriptor="后续失败的链接",
            ),
        ],
        run_mode=RunMode.GUIDED,
    )

    with pytest.raises(WorkflowConflictError, match="HTTP or HTTPS"):
        service._import_creative_input_images(project_id, import_request)

    restored = service.get_project(project_id)
    assert restored.document.model_dump() == baseline.document.model_dump()
    assets_root = tmp_path / "workspace" / project_id / "assets"
    assert not assets_root.exists() or list(assets_root.iterdir()) == []


def test_duplicate_project_id_never_overwrites_the_first_document(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = "project-stable-id"
    first = service.create_project(_request("第一版"), project_id=project_id)

    with pytest.raises(WorkflowConflictError, match="already exists"):
        service.create_project(_request("不应覆盖"), project_id=project_id)

    persisted = service.get_project(project_id)
    assert persisted.document.project.name == "第一版"
    assert persisted.document.model_dump() == first.document.model_dump()


def test_planning_metadata_persists_and_old_documents_remain_readable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    created = service.create_project(
        _request("规划来源"),
        project_id="project-planning-metadata",
    )

    planning = created.document.planning
    assert planning is not None
    assert planning.status == PlanningRunStatus.COMPLETE
    assert planning.planner == "deterministic-structure-scaffold-v3"
    assert planning.warnings == [
        "GPT-5.6-sol 未配置；仅建立不补写创意的本地结构骨架，"
        "具体内容、Style 与节奏需要由 Agent 完成。"
    ]

    restarted = _service(tmp_path)
    restored = restarted.get_project(created.document.project.id).document
    assert restored.planning == planning

    legacy_payload = restored.model_dump(mode="python")
    legacy_payload.pop("planning")
    legacy = ProjectDocument.model_validate(legacy_payload)
    assert legacy.planning is None
