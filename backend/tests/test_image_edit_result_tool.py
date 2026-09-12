"""A saved masked edit can expose/recompose its actual Provider pixels without regeneration."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetMutationResponse,
    AssetReviewRequest,
    ImageOperation,
    ImageProviderRequest,
    ImageTaskCreateRequest,
    ImageTaskStatus,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ApprovalStatus, AssetKind, ShotReferenceCue
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.providers.image_provider import ImageProviderInput, ImageProviderResult
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioShotSave
from short_drama.tools import ToolCallStatus, ToolResult
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work
from tests.test_short_drama_assets import FakeImageProvider, _fixture_asset, _service


SIZE = (768, 1344)
SOURCE_COLOR = (18, 52, 86)
PROVIDER_BACKGROUND = (236, 214, 192)
CONTINUOUS_PART = (220, 92, 32)


class ConnectedShapeProvider(FakeImageProvider):
    def create(
        self, request: ImageProviderRequest, inputs: list[ImageProviderInput],
    ) -> ImageProviderResult:
        result = super().create(request, inputs)
        with Image.new("RGB", SIZE, PROVIDER_BACKGROUND) as image:
            # A narrow mask cuts the visibly continuous result at x=360,
            # even though the Provider actually returned it through x=550.
            ImageDraw.Draw(image).rectangle((200, 550, 550, 750), fill=CONTINUOUS_PART)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        return result.model_copy(update={
            "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })


@dataclass(frozen=True)
class EditFixture:
    service: ShortDramaService
    provider: ConnectedShapeProvider
    project_id: str
    asset_id: str
    task_id: str
    replacement_mask_id: str


def _finished_edit(tmp_path: Path) -> EditFixture:
    provider = ConnectedShapeProvider()
    service = _service(tmp_path, provider)
    asset = _fixture_asset(service, "asset-linxia")
    source_path = tmp_path / "original.png"
    with Image.new("RGB", SIZE, SOURCE_COLOR) as image:
        image.save(source_path)
    source = service.import_local_asset_version(asset.project_id, asset.id, AssetImportLocalRequest(
        local_path=str(source_path), descriptor="Original scene pixels",
    )).version
    service.review_asset_version(asset.project_id, asset.id, source.id, AssetReviewRequest(
        decision="APPROVED", purpose="CANONICAL_MASTER", notes="Original selected scene",
    ))
    mask_asset = service.create_asset(asset.project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name="Observed edit regions", descriptor="Local alpha masks",
    )).asset
    mask_ids: list[str] = []
    for name, right in (("narrow", 360), ("complete-part", 600)):
        path = tmp_path / f"{name}.png"
        with Image.new("RGBA", SIZE, (255, 255, 255, 255)) as mask:
            ImageDraw.Draw(mask).rectangle((180, 500, right, 800), fill=(255, 255, 255, 0))
            mask.save(path)
        mask_ids.append(service.import_local_asset_version(asset.project_id, mask_asset.id,
            AssetImportLocalRequest(local_path=str(path), descriptor=f"Observed {name} region")).version.id)
    queued = service.enqueue_image_task(asset.project_id, asset.id, ImageTaskCreateRequest(
        operation=ImageOperation.EDIT,
        parent_version_id=source.id,
        primary_input_version_id=source.id,
        input_version_ids=[source.id],
        mask_version_id=mask_ids[0],
        mask_feather_pixels=0,
        prompt="Move the whole visible connected part while keeping the rest of the scene.",
        descriptor="Masked edit with an initially too-small region",
        size="768x1344",
    )).task
    service.advance_image_task(queued.id)
    finished = service.advance_image_task(queued.id).task
    assert finished.status == ImageTaskStatus.SUCCEEDED
    assert len(provider.calls) == 1
    assert finished.output_path is not None
    with Image.open(finished.output_path) as image:
        assert image.convert("RGB").getpixel((300, 650)) == CONTINUOUS_PART
        assert image.convert("RGB").getpixel((500, 650)) == SOURCE_COLOR

    # A previously selected shot and a later rejected composite do not imply
    # that the distinct, saved Provider output is unusable or was rejected.
    document = service.get_project(asset.project_id).document
    shot = document.shots[0].model_copy(deep=True)
    shot.references = [ShotReferenceCue(
        id="existing-shot-selection", subject_asset_id=asset.id,
        asset_version_id=finished.output_version_id, alias="existing_composite",
        purpose="Current shot's selected composition",
    )]
    service.studio.save_shot(asset.project_id, shot.id, StudioShotSave(
        shot=shot, expected_revision=shot.revision,
    ))
    service.review_asset_version(asset.project_id, asset.id, finished.output_version_id,
        AssetReviewRequest(decision="REJECTED", purpose="CANONICAL_MASTER",
            notes="The mask cut the connected part."))
    return EditFixture(service, provider, asset.project_id, asset.id, finished.id, mask_ids[1])


def _derive(
    fixture: EditFixture, *, mode: str, service: ShortDramaService | None = None,
) -> ToolResult:
    request: dict[str, object] = {
        "image_task_id": fixture.task_id,
        "mode": mode,
        "mask_feather_pixels": 0,
        "descriptor": "Inspect the saved original" if mode == "PROVIDER_OUTPUT" else "Restore the observed full region",
    }
    if mode == "MASKED_COMPOSITE":
        request["mask_version_id"] = fixture.replacement_mask_id
    return (service or fixture.service).tool_registry.invoke("image.derive_edit_result", {
        "project_id": fixture.project_id, "request": request,
    })


def _derived_version(result: ToolResult) -> AssetMutationResponse:
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = AssetMutationResponse.model_validate(result.output)
    assert response.version.approval_status == ApprovalStatus.DRAFT
    assert response.version.local_path is not None
    assert result.receipt is not None and result.receipt.operation_completed
    assert result.receipt.current_state["provider_called"] is False
    assert result.receipt.current_state["visual_acceptance"] == "NOT_ASSESSED"
    assert response.version.id in {reference.artifact_id for reference in result.artifact_refs}
    return response


def test_actual_tool_restores_provider_pixels_recomposes_and_survives_restart_without_generation(
    tmp_path: Path,
) -> None:
    fixture = _finished_edit(tmp_path)
    service = fixture.service
    before = service.get_project(fixture.project_id).document
    task = service.repository.get_image_task(fixture.task_id)
    assert task is not None and task.provider_output_path is not None and task.output_path is not None
    task_before = task.model_dump(mode="json")
    raw_path, original_composite_path = Path(task.provider_output_path), Path(task.output_path)
    raw_before, composite_before = raw_path.read_bytes(), original_composite_path.read_bytes()
    before_asset = next(asset for asset in before.assets if asset.id == fixture.asset_id)

    exposed = _derived_version(_derive(fixture, mode="PROVIDER_OUTPUT"))
    with Image.open(exposed.version.local_path or "") as image:
        assert image.convert("RGB").getpixel((500, 650)) == CONTINUOUS_PART
        assert image.convert("RGB").getpixel((0, 0)) == PROVIDER_BACKGROUND

    recomposed = _derived_version(_derive(fixture, mode="MASKED_COMPOSITE"))
    with Image.open(recomposed.version.local_path or "") as image:
        assert image.convert("RGB").getpixel((300, 650)) == CONTINUOUS_PART
        assert image.convert("RGB").getpixel((500, 650)) == CONTINUOUS_PART
        assert image.convert("RGB").getpixel((0, 0)) == SOURCE_COLOR
        assert image.convert("RGB").getpixel((650, 650)) == SOURCE_COLOR

    restarted = _service(tmp_path, fixture.provider)
    resumed = _derived_version(_derive(fixture, mode="MASKED_COMPOSITE", service=restarted))
    with Image.open(resumed.version.local_path or "") as image:
        assert image.convert("RGB").getpixel((500, 650)) == CONTINUOUS_PART
        assert image.convert("RGB").getpixel((650, 650)) == SOURCE_COLOR
    assert len({exposed.version.id, recomposed.version.id, resumed.version.id}) == 3

    after = restarted.get_project(fixture.project_id).document
    after_asset = next(asset for asset in after.assets if asset.id == fixture.asset_id)
    assert after_asset.current_version_id == before_asset.current_version_id
    assert after_asset.approved_version_id == before_asset.approved_version_id
    assert after_asset.versions[:-3] == before_asset.versions
    assert after.shots == before.shots
    assert after.reference_plan == before.reference_plan and after.reference_packs == before.reference_packs
    assert [asset for asset in after.assets if asset.id != fixture.asset_id] == [
        asset for asset in before.assets if asset.id != fixture.asset_id
    ]
    saved_task = restarted.repository.get_image_task(fixture.task_id)
    assert saved_task is not None and saved_task.model_dump(mode="json") == task_before
    assert raw_path.read_bytes() == raw_before and original_composite_path.read_bytes() == composite_before
    assert len(restarted.list_image_tasks(fixture.project_id).tasks) == 1
    assert len(fixture.provider.calls) == 1


def test_actual_task_and_derivation_results_reach_restarted_worker_with_exact_original_source(
    tmp_path: Path,
) -> None:
    fixture = _finished_edit(tmp_path)
    arguments = {"project_id": fixture.project_id, "asset_id": fixture.asset_id}
    listed = fixture.service.tool_registry.invoke("image.list", arguments)
    assert listed.status == ToolCallStatus.SUCCEEDED
    task = fixture.service.repository.get_image_task(fixture.task_id)
    assert task is not None and task.provider_output_path is not None
    derived = _derive(fixture, mode="PROVIDER_OUTPUT")
    candidate = _derived_version(derived).version
    store, work, context = runtime_work(tmp_path / "worker.sqlite3")
    context.project_id = fixture.project_id
    work.specialist_turns = [
        SpecialistTurnRecord(id="actual-image-list", sequence=1, tool_name="image.list",
            arguments=arguments, reason="Locate the exact completed edit.",
            status=SpecialistTurnStatus.COMPLETED, result=listed.model_dump(mode="json")),
        SpecialistTurnRecord(id="actual-local-derivation", sequence=2,
            tool_name="image.derive_edit_result", arguments={"project_id": fixture.project_id,
                "request": {"image_task_id": task.id, "mode": "PROVIDER_OUTPUT",
                    "descriptor": "Inspect the saved original"}},
            reason="Expose the saved Provider pixels for a separate visual judgment.",
            status=SpecialistTurnStatus.COMPLETED, result=derived.model_dump(mode="json")),
    ]
    store.save_work_order(work)
    restarted = AgentRuntimeStore(store.database_path)
    saved = restarted.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
        "reason": "Continue with the saved exact candidate.",
        "arguments": {"project_id": fixture.project_id}})])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context,
        system_prompt="Inspect the selected saved result; no new generation is needed."))
    working = client.payloads[0]["working_set"]
    assert isinstance(working, dict)
    entities = working["entities"]
    assert isinstance(entities, list)
    entry = next(item for item in entities if isinstance(item, dict) and item["kind"] == "image_task")
    state = entry["state"]
    assert isinstance(state, dict)
    assert state["id"] == task.id and state["asset_id"] == fixture.asset_id
    assert state["output_version_id"] == task.output_version_id
    assert state["provider_output_path"] == task.provider_output_path
    assert state["output_path"] == task.output_path
    assert state["output_version_id"] != candidate.id
    assert saved.specialist_turns[0].result == listed.model_dump(mode="json")
    assert len(fixture.provider.calls) == 1


def test_actual_qa_input_reports_raw_availability_from_file_without_treating_composite_as_raw(
    tmp_path: Path,
) -> None:
    fixture = _finished_edit(tmp_path)
    language = CapturingLanguageClient()
    service = ShortDramaService(database_path=fixture.service.repository.database_path,
        workspace_root=fixture.service.workspace_root,
        language_provider_factory=lambda _key, _url: language)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    task = service.repository.get_image_task(fixture.task_id)
    assert task is not None and task.provider_output_path is not None
    before = service.get_project(fixture.project_id).document
    for available in (True, False):
        if not available:
            Path(task.provider_output_path).unlink()
        result = service.tool_registry.invoke("qa.inspect_asset_version", {
            "project_id": fixture.project_id, "asset_id": fixture.asset_id,
            "version_id": task.output_version_id, "request": {},
        })
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        masked = json.loads(language.input_text)["candidate_creation"]["masked_edit"]
        assert masked["image_task_id"] == task.id
        assert masked["primary_input_version_id"] == task.request.primary_input_version_id
        assert masked["mask_version_id"] == task.request.mask_version_id
        assert masked["local_mask_feather_pixels"] == 0
        assert masked["provider_output_available"] is available
    assert service.get_project(fixture.project_id).document == before
    assert len(fixture.provider.calls) == 1


@pytest.mark.parametrize("mode", ["PROVIDER_OUTPUT", "MASKED_COMPOSITE"])
@pytest.mark.parametrize("missing", ["legacy_no_raw", "raw_file_removed"])
def test_missing_raw_result_fails_without_inventing_a_provider_image_or_creating_candidates(
    tmp_path: Path, mode: str, missing: str,
) -> None:
    fixture = _finished_edit(tmp_path)
    service = fixture.service
    task = service.repository.get_image_task(fixture.task_id)
    assert task is not None and task.provider_output_path is not None
    if missing == "legacy_no_raw":
        task.provider_output_path = None
        service.repository.save_image_task(task)
    else:
        Path(task.provider_output_path).unlink()
    before = service.get_project(fixture.project_id).document
    task_before = task.model_dump(mode="json")
    files_before = {path for path in service.workspace_root.rglob("*") if path.is_file()}

    result = _derive(fixture, mode=mode)
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None
    assert "raw" in result.error.message.lower() or "provider" in result.error.message.lower()
    assert not result.artifact_refs
    assert service.get_project(fixture.project_id).document == before
    saved_task = service.repository.get_image_task(fixture.task_id)
    assert saved_task is not None and saved_task.model_dump(mode="json") == task_before
    assert {path for path in service.workspace_root.rglob("*") if path.is_file()} == files_before
    assert len(service.list_image_tasks(fixture.project_id).tasks) == 1
    assert len(fixture.provider.calls) == 1
