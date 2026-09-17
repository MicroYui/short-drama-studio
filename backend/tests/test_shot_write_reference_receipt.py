"""Saving a shot returns the actual reference resolution without another Agent turn."""

from pathlib import Path

import pytest
from PIL import Image

from short_drama.image_models import AssetImportLocalRequest
from short_drama.models import ApprovalStatus, AssetKind, ShotReferenceCue
from short_drama.reference_planner import ReferencePlanPreviewRequest
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus


@pytest.mark.parametrize("rejected", [False, True])
def test_shot_write_returns_saved_preview_and_keeps_unusable_choice_visible(
    tmp_path: Path, rejected: bool,
) -> None:
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "work")
    document = service.initialize_fixture().document
    project_id = document.project.id
    asset = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    path = tmp_path / "candidate.png"
    Image.new("RGB", (120, 180), "#adbdc6").save(path)
    candidate = service.import_local_asset_version(project_id, asset.id, AssetImportLocalRequest(
        local_path=str(path), descriptor="本镜明确选定的构图图片",
    )).version
    if rejected:
        document = service.get_project(project_id).document
        saved_asset = next(item for item in document.assets if item.id == asset.id)
        next(item for item in saved_asset.versions if item.id == candidate.id).approval_status = ApprovalStatus.REJECTED
        service.repository.save_project(document)
    before = service.get_project(project_id).document
    shot = before.shots[0]
    replacement = shot.model_copy(deep=True, update={
        "camera": "固定机位，保留原有动作。",
        "references": [ShotReferenceCue(
            id="chosen-frame", subject_asset_id=asset.id, asset_version_id=candidate.id,
            alias="本镜构图", purpose="保留本镜构图与人物位置", image_reference_role="COMPOSITION",
        )],
    })
    request = {"project_id": project_id, "shot_id": shot.id, "request": {
        "expected_revision": shot.revision, "shot": replacement.model_dump(mode="json"),
    }}
    result = service.tool_registry.invoke("shot.save_current", request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    assert result.output["reference_plan_preview_error"] is None
    preview = result.output["reference_plan_preview"]
    assert preview == service.preview_shot_reference_plan(
        project_id, shot.id, ReferencePlanPreviewRequest(),
    ).model_dump(mode="json")
    assert isinstance(preview, dict)
    selected = preview["selected"]
    assert isinstance(selected, list)
    assert {item["asset_version_id"] for item in selected if isinstance(item, dict)} == (
        set() if rejected else {candidate.id}
    )
    assert bool(preview["missing_requirement_ids"]) == rejected
    assert bool(result.warnings) == rejected
    after = service.get_project(project_id).document
    saved = next(item for item in after.shots if item.id == shot.id)
    assert saved.revision == result.output["shot_revisions"][shot.id]
    assert saved.camera == replacement.camera
    assert saved.references[0].asset_version_id == candidate.id
    assert [item for item in after.shots if item.id != shot.id] == [
        item for item in before.shots if item.id != shot.id
    ]
    assert after.assets == before.assets
    assert service.list_image_tasks(project_id).tasks == []
    # A stale repeat still conflicts instead of saving again to obtain a preview.
    repeated = service.tool_registry.invoke("shot.save_current", request)
    assert repeated.status == ToolCallStatus.FAILED
    expected_error = (
        f"镜头 {shot.id} 已更新（请求修订 {shot.revision}，当前修订 {saved.revision}）；"
        "本次未保存。请读取最新镜头，合并所需修改后重试。"
    )
    assert repeated.error is not None
    assert repeated.error.kind == "StudioContentConflict"
    assert repeated.error.message == expected_error
    assert repeated.receipt is None
    # A different stale write must not discard the latest saved reference either.
    stale = replacement.model_copy(deep=True, update={"references": []})
    conflicting = service.tool_registry.invoke("shot.save_current", {
        "project_id": project_id, "shot_id": shot.id, "request": {
            "expected_revision": shot.revision, "shot": stale.model_dump(mode="json"),
        },
    })
    assert conflicting.status == ToolCallStatus.FAILED
    assert conflicting.error is not None
    assert conflicting.error.kind == "StudioContentConflict"
    assert conflicting.error.message == expected_error
    assert service.get_project(project_id).document == after
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert reopened.get_project(project_id).document == after
