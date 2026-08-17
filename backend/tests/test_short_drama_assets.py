"""Integration-first asset workbench and persistent image task coverage."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.image_models import (
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetReviewRequest,
    ImageBackground,
    ImageOperation,
    ImageProviderConfigUpdate,
    ImageTaskCreateRequest,
    ImageTaskStatus,
    ImageTokenDetails,
    ImageUsage,
)
from short_drama.h3_models import H3MediaInput, H3MediaSource, H3ProbeRequest
from short_drama.models import (
    ApprovalStatus,
    AssetMediaType,
    OriginType,
    VideoGenerationRoute,
)
from short_drama.providers.image_provider import (
    ImageProviderInput,
    ImageProviderResult,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


class FakeImageProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[object, list[ImageProviderInput]]] = []
        self.validations = 0

    def validate_credentials(self) -> None:
        self.validations += 1

    def create(self, request, inputs: list[ImageProviderInput]) -> ImageProviderResult:
        self.calls.append((request.model_copy(deep=True), list(inputs)))
        buffer = BytesIO()
        Image.new("RGB", (768, 1344), "#7f1d1d").save(buffer, format="PNG")
        usage = ImageUsage(
            input_tokens=150,
            input_tokens_details=ImageTokenDetails(image_tokens=100, text_tokens=50),
            output_tokens=1000,
            output_tokens_details=ImageTokenDetails(image_tokens=1000, text_tokens=0),
            total_tokens=1150,
        )
        return ImageProviderResult(
            image_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            mime_type="image/png",
            provider_request_id=f"req-{len(self.calls)}",
            usage=usage,
        )


def _factory(fake: FakeImageProvider):
    def create(api_key: str, base_url: str) -> FakeImageProvider:
        assert api_key == "test-openai-key"
        assert base_url == "https://image.example.test/v1"
        return fake

    return create


def _service(tmp_path: Path, fake: FakeImageProvider) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "app_data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=_factory(fake),
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="test-openai-key",
            base_url="https://image.example.test/v1/",
        )
    )
    return service


def _fixture_asset(service: ShortDramaService, suffix: str):
    project = service.initialize_fixture()
    return next(asset for asset in project.document.assets if asset.id.endswith(suffix))


def test_local_url_import_version_lineage_and_approval(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-recorder")
    local = tmp_path / "red-recorder.png"
    Image.new("RGB", (512, 512), "red").save(local)

    imported = service.import_local_asset_version(
        asset.project_id,
        asset.id,
        AssetImportLocalRequest(
            local_path=str(local),
            descriptor="红色旧录音笔本地候选",
        ),
    )
    assert imported.version.origin_type == OriginType.UPLOAD
    assert imported.version.media_type == AssetMediaType.IMAGE
    assert imported.version.approval_status == ApprovalStatus.DRAFT
    assert imported.version.width == 512
    assert imported.version.local_path is not None
    assert Path(imported.version.local_path).is_file()

    linked = service.import_url_asset_version(
        asset.project_id,
        asset.id,
        AssetImportUrlRequest(
            external_url="https://example.com/recorder.png",
            descriptor="外部素材候选",
            parent_version_id=imported.version.id,
        ),
    )
    assert linked.version.parent_version_id == imported.version.id
    assert linked.version.origin_type == OriginType.LIBRARY_IMPORT

    reviewed = service.review_asset_version(
        asset.project_id,
        asset.id,
        imported.version.id,
        AssetReviewRequest(decision="APPROVED", notes="外形正确，不像对讲机"),
    )
    assert reviewed.version.approval_status == ApprovalStatus.APPROVED
    assert reviewed.asset.approved_version_id == imported.version.id


def test_generate_edit_cost_and_candidate_gate(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-linxia")

    queued = service.enqueue_image_task(
        asset.project_id,
        asset.id,
        ImageTaskCreateRequest(
            operation=ImageOperation.GENERATE,
            prompt="林夏真人角色定妆，短黑发，米白风衣",
            descriptor="林夏首批可审片候选",
        ),
    )
    assert queued.task.status == ImageTaskStatus.QUEUED
    assert service.advance_image_task(queued.task.id).task.status == ImageTaskStatus.SUBMITTING
    completed = service.advance_image_task(queued.task.id)
    assert completed.task.status == ImageTaskStatus.SUCCEEDED
    assert completed.task.cost is not None
    assert completed.task.cost.estimated_amount == 0.03105
    generated = next(
        version
        for version in completed.project.document.assets[0].versions
        if version.id == completed.task.output_version_id
    )
    assert generated.approval_status == ApprovalStatus.DRAFT
    assert completed.project.document.assets[0].approved_version_id is None
    assert generated.local_path is not None and Path(generated.local_path).is_file()

    support_path = tmp_path / "edit-support.png"
    Image.new("RGB", (768, 1344), "#1e3a5f").save(support_path)
    support = service.import_local_asset_version(
        asset.project_id,
        asset.id,
        AssetImportLocalRequest(
            local_path=str(support_path),
            descriptor="secondary identity or style reference",
            parent_version_id=generated.id,
        ),
    ).version

    edit = service.enqueue_image_task(
        asset.project_id,
        asset.id,
        ImageTaskCreateRequest(
            operation=ImageOperation.EDIT,
            prompt="保持身份与服装，只增强真实皮肤和电影光线",
            descriptor="林夏修整候选",
            parent_version_id=generated.id,
            input_version_ids=[support.id, generated.id],
            primary_input_version_id=generated.id,
        ),
    )
    service.advance_image_task(edit.task.id)
    edited = service.advance_image_task(edit.task.id)
    assert edited.task.status == ImageTaskStatus.SUCCEEDED
    assert len(fake.calls) == 2
    assert fake.calls[1][1][0].asset_version_id == generated.id
    assert fake.calls[1][1][1].asset_version_id == support.id
    assert fake.calls[1][0].primary_input_version_id == generated.id
    assert fake.calls[1][1][0].image_url.startswith("data:image/png;base64,")


def test_azure_transparent_background_is_rejected_before_provider_call(
    tmp_path: Path,
) -> None:
    fake = FakeImageProvider()
    service = ShortDramaService(
        database_path=tmp_path / "app_data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=lambda _api_key, _base_url: fake,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="azure-key",
            base_url=(
                "https://resource.openai.azure.com/openai/deployments/gpt-image-2/"
                "images/generations?api-version=2025-04-01-preview"
            ),
        )
    )
    asset = _fixture_asset(service, "asset-recorder")

    with pytest.raises(WorkflowConflictError, match="does not support transparent"):
        service.enqueue_image_task(
            asset.project_id,
            asset.id,
            ImageTaskCreateRequest(
                prompt="red recorder",
                descriptor="candidate",
                background=ImageBackground.TRANSPARENT,
            ),
        )
    assert fake.calls == []


def test_h3_resolves_only_the_current_approved_asset_version(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    asset = _fixture_asset(service, "asset-linxia")
    local = tmp_path / "linxia-approved.png"
    Image.new("RGB", (768, 1344), "white").save(local)
    imported = service.import_local_asset_version(
        asset.project_id,
        asset.id,
        AssetImportLocalRequest(local_path=str(local), descriptor="林夏正式定妆"),
    )
    shot_id = project.document.shots[0].id
    linked_input = H3MediaInput(
        kind="IMAGE",
        role="first_frame",
        source=H3MediaSource(external_url="https://ignored.example/draft.png"),
        asset_version_id=imported.version.id,
    )

    with pytest.raises(WorkflowConflictError, match="not the asset's approved formal version"):
        service.preview_h3_probe(
            project.document.project.id,
            H3ProbeRequest(
                shot_id=shot_id,
                route=VideoGenerationRoute.FRAME_FIRST,
                media_inputs=[linked_input],
            ),
        )

    service.review_asset_version(
        asset.project_id,
        asset.id,
        imported.version.id,
        AssetReviewRequest(decision="APPROVED", notes="身份和服装可作为正式首帧"),
    )
    preview = service.preview_h3_probe(
        project.document.project.id,
        H3ProbeRequest(
            shot_id=shot_id,
            route=VideoGenerationRoute.FRAME_FIRST,
            media_inputs=[linked_input],
        ),
    )
    assert preview.request.content[1].image_url is not None
    assert imported.version.local_path is not None
    assert preview.request.content[1].image_url.url == (
        f"local-upload://{Path(imported.version.local_path).name}"
    )


def test_interrupted_submission_recovers_without_automatic_resubmit(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    asset = _fixture_asset(service, "asset-store")
    queued = service.enqueue_image_task(
        asset.project_id,
        asset.id,
        ImageTaskCreateRequest(
            prompt="雨夜便利店空间锚点",
            descriptor="便利店候选",
        ),
    )
    submitting = service.advance_image_task(queued.task.id)
    assert submitting.task.status == ImageTaskStatus.SUBMITTING

    restarted = ShortDramaService(
        database_path=tmp_path / "app_data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=_factory(fake),
    )
    recovered = restarted.list_image_tasks(asset.project_id).tasks[0]
    assert recovered.status == ImageTaskStatus.SUBMIT_UNKNOWN
    assert fake.calls == []


def test_asset_and_image_task_product_api(tmp_path: Path) -> None:
    fake = FakeImageProvider()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    local = tmp_path / "reference.png"
    Image.new("RGB", (128, 256), "blue").save(local)

    with TestClient(app) as client:
        created = client.post(
            f"/api/short-drama/projects/{project.document.project.id}/assets",
            json={
                "kind": "REFERENCE",
                "name": "雨夜氛围参考",
                "descriptor": "雨滴与蓝色霓虹",
            },
        )
        assert created.status_code == 200, created.text
        asset_id = created.json()["asset"]["id"]
        imported = client.post(
            f"/api/short-drama/projects/{project.document.project.id}/assets/{asset_id}/versions/import-local",
            json={"local_path": str(local), "descriptor": "本地氛围图"},
        )
        assert imported.status_code == 200, imported.text
        task = client.post(
            f"/api/short-drama/projects/{project.document.project.id}/assets/{asset_id}/image-tasks",
            json={
                "operation": "GENERATE",
                "prompt": "雨夜便利店电影氛围，无文字",
                "descriptor": "AI 氛围候选",
            },
        )
        assert task.status_code == 200, task.text
        listed = client.get(
            f"/api/short-drama/projects/{project.document.project.id}/image-tasks",
            params={"asset_id": asset_id},
        )
        assert listed.status_code == 200
        assert len(listed.json()["tasks"]) == 1
