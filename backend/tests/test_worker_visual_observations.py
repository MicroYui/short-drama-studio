"""Real saved crop arguments through Registry, persisted turns and Worker images.

Diagnostic pixels verify transport/geometry only; no model or quality claims.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from short_drama.agent_runtime.model_input import ModelInputCompiler, generate_context_managed_text, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord
from short_drama.agent_runtime.token_budget import ContextBudget, reserved_visual_tokens
from short_drama.agent_runtime.visual_input import PreparedVisualInput, prepare_worker_visual_input
from short_drama.image_models import AssetImportLocalRequest
from short_drama.production_manager import LanguageSpecialistAgent, _specialist_tool_result_for_persistence
from short_drama.providers.openai_compatible_language_client import LanguageProviderContextWindowError, OpenAICompatibleLanguageClient
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace
from short_drama.tools import ToolCallStatus
from tests.test_image_edit_result_tool import _finished_edit
from tests.test_language_provider import FakeResponse, FakeSession
from tests.test_model_context_budget import _service as context_service
from tests.test_production_manager import FakeSpecialistLanguageClient, _start_pending_delegation
from tests.test_short_drama_assets import FakeImageProvider, _service


FIXTURE = json.loads((Path(__file__).parent / "fixtures/fresh6_worker_crop_observations.json").read_text())


class VisionClient(FakeSpecialistLanguageClient):
    def __init__(self, *, discover: bool = False) -> None:
        super().__init__()
        self.images: list[list[str]] = []
        self.discover = discover

    def generate_text(self, *, instructions, input_text, image_urls=None, max_output_tokens=2200):
        self.images.append(list(image_urls or []))
        if self.discover and len(self.images) == 1:
            self.inputs.append(input_text)
            self.instructions.append(instructions)
            return json.dumps({"requested_tool_schema_ids": ["image.view"], "rationale": "Read exact images when needed."})
        return super().generate_text(instructions=instructions, input_text=input_text,
            image_urls=image_urls, max_output_tokens=max_output_tokens)


def _image(url):
    return Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


def _save_and_reopen(service, project_id, result, arguments, *, sequence=1):
    _run, work_id, _action = _start_pending_delegation(service, project_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    work.specialist_turns = [SpecialistTurnRecord(id="saved-visual-turn", sequence=sequence,
        tool_name=result.tool_id, arguments=arguments, reason="Inspect the exact latest output.", status="COMPLETED",
        result=_specialist_tool_result_for_persistence(result.tool_id, result.model_dump(mode="json"), arguments=arguments))]
    service.agent_runtime.store.save_work_order(work)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    return reopened, reopened.get_agent_work_order(work_id)


@pytest.mark.parametrize("crop_index", [0, 1])
def test_saved_s04_crop_pixels_and_actual_source_box_reach_next_worker_after_reopen(tmp_path, crop_index):
    service = _service(tmp_path, FakeImageProvider())
    document = service.initialize_fixture().document
    asset = document.assets[0]
    source_path = tmp_path / "diagnostic-door-location.png"
    pixels = Image.new("RGB", tuple(FIXTURE["provenance"]["source_size"]), "#304050")
    ImageDraw.Draw(pixels).rectangle((170, 0, 209, 2047), fill="#ee1111")
    pixels.save(source_path)
    source = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(source_path), descriptor="Diagnostic pixels, not a photographic quality fixture")).version
    saved = FIXTURE["crops"][crop_index]
    arguments = deepcopy(saved["arguments"])
    arguments.update(project_id=asset.project_id, asset_id=asset.id)
    arguments["request"]["source_asset_version_id"] = source.id
    result = service.tool_registry.invoke("image.crop", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert [item.role for item in result.visual_attachments] == ["CANDIDATE", "SOURCE"]
    before = service.get_project(asset.project_id).document
    reopened, view = _save_and_reopen(service, asset.project_id, result, arguments, sequence=saved["source_sequence"])
    client = VisionClient(discover=True)
    agent = LanguageSpecialistAgent(client, reopened.tool_registry)
    agent.context_compiler = ModelInputCompiler(reopened.agent_runtime.store)
    agent.execute(view)
    assert len(client.images) == 2 and client.images[0] == client.images[1]
    assert len(client.images[0]) == 2
    value = json.loads(client.inputs[-1])
    observed = value["image_observations"]
    candidate, original = observed["images"]
    assert observed["source_sequence"] == saved["source_sequence"] and observed["unavailable"] == []
    assert candidate["image_crop"]["box_pixels"] == saved["actual_image_crop"]["box_pixels"]
    assert original["artifact_ref"]["artifact_id"] == source.id
    box = candidate["image_crop"]["box_pixels"]
    assert candidate["observed_dimensions"] == [box[2] - box[0], box[3] - box[1]]
    assert _image(client.images[-1][0]).size == tuple(candidate["observed_dimensions"])
    # The first real crop box excludes the marker, while the second includes it.
    pixel = _image(client.images[-1][0]).getpixel((78, 50))
    assert (pixel[0] > pixel[1] * 3) is (crop_index == 1)
    assert "base64," not in client.inputs[-1]
    assert reopened.get_project(asset.project_id).document == before


def test_masked_final_raw_and_primary_are_separate_exact_images_without_adoption(tmp_path):
    fixture = _finished_edit(tmp_path)
    service = fixture.service
    result = service.tool_registry.invoke("image.advance", {"task_id": fixture.task_id})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert [item.role for item in result.visual_attachments] == ["CANDIDATE", "PROVIDER_OUTPUT", "SOURCE"]
    assert result.visual_attachments[0].approval_status.value == "REJECTED"
    before = service.get_project(fixture.project_id).document
    reopened, view = _save_and_reopen(service, fixture.project_id, result, {"task_id": fixture.task_id})
    actual = prepare_worker_visual_input(view.work_order, project_id=fixture.project_id)
    assert actual is not None and len(actual.image_urls) == 3
    # The existing fixture really has a cut final and an uncut raw output.
    final, raw, _source = [_image(url) for url in actual.image_urls]
    assert raw.getpixel((500, 650))[0] > 180
    assert final.getpixel((500, 650))[0] < 40
    assert reopened.get_project(fixture.project_id).document == before
    assert len(fixture.provider.calls) == 1
    view.work_order.specialist_turns.append(SpecialistTurnRecord(id="later-text-read", sequence=2,
        tool_name="artifact.read_current", reason="Read current text after observation", status="COMPLETED",
        result={"status": "SUCCEEDED", "output": {"text": "Current shot"}}))
    assert prepare_worker_visual_input(view.work_order, project_id=fixture.project_id) is None


def test_image_view_exact_rejected_version_region_and_missing_file_no_current_fallback(tmp_path):
    fixture = _finished_edit(tmp_path)
    task = fixture.service.repository.get_image_task(fixture.task_id)
    assert task is not None
    before = fixture.service.get_project(fixture.project_id).document
    arguments = {"project_id": fixture.project_id, "images": [{"asset_version_id": task.output_version_id,
        "region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}}], "purpose": "Inspect the rejected mask boundary"}
    result = fixture.service.tool_registry.invoke("image.view", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    reopened, view = _save_and_reopen(fixture.service, fixture.project_id, result, arguments)
    actual = prepare_worker_visual_input(view.work_order, project_id=fixture.project_id)
    assert actual is not None
    selected = actual.observations["images"][0]
    assert selected["approval_status"] == "REJECTED"
    assert selected["source_pixel_box"] == [192, 336, 576, 1008]
    assert selected["observed_dimensions"] == [384, 672]
    assert reopened.get_project(fixture.project_id).document == before
    Path(task.output_path).unlink()
    missing = reopened.tool_registry.invoke("image.view", arguments)
    assert missing.status == ToolCallStatus.FAILED and "no current-version fallback" in missing.error.message
    unavailable = prepare_worker_visual_input(view.work_order, project_id=fixture.project_id)
    assert unavailable is not None and unavailable.image_urls == ()
    assert unavailable.observations["images"] == [] and len(unavailable.observations["unavailable"]) == 1
    assert len(fixture.provider.calls) == 1


def test_images_are_not_inferred_from_old_records_or_broadcast_after_unrelated_tool(tmp_path):
    service, project_id, _run = context_service(tmp_path)
    result = service.tool_registry.invoke("artifact.read_current", {"project_id": project_id})
    assert result.status == ToolCallStatus.SUCCEEDED
    _reopened, view = _save_and_reopen(service, project_id, result, {"project_id": project_id})
    assert prepare_worker_visual_input(view.work_order, project_id=project_id) is None


def test_image_view_uses_exif_oriented_native_region_without_modifying_source(tmp_path):
    service = _service(tmp_path, FakeImageProvider())
    asset = service.initialize_fixture().document.assets[0]
    path = tmp_path / "rotated-camera-image.jpg"
    pixels = Image.new("RGB", (80, 120), "#304050")
    ImageDraw.Draw(pixels).rectangle((0, 0, 39, 59), fill="#ee1111")
    exif = Image.Exif()
    exif[274] = 6
    pixels.save(path, exif=exif, quality=95)
    version = service.import_local_asset_version(asset.project_id, asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="EXIF observation fixture")).version
    original = Path(version.local_path).read_bytes()
    arguments = {"project_id": asset.project_id, "purpose": "Inspect the oriented source and its native upper-right region", "images": [
        {"asset_version_id": version.id},
        {"asset_version_id": version.id, "region": {"x": 0.5, "y": 0, "width": 0.5, "height": 0.5}},
    ]}
    result = service.tool_registry.invoke("image.view", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    _reopened, view = _save_and_reopen(service, asset.project_id, result, arguments)
    actual = prepare_worker_visual_input(view.work_order, project_id=asset.project_id)
    assert actual is not None and len(actual.image_urls) == 2
    full, region = actual.observations["images"]
    assert full["source_dimensions"] == full["observed_dimensions"] == [120, 80]
    assert region["source_dimensions"] == [120, 80]
    assert region["source_pixel_box"] == [60, 0, 120, 40]
    assert region["observed_dimensions"] == [60, 40]
    red = _image(actual.image_urls[1]).getpixel((30, 20))
    assert red[0] > 3 * red[1]
    assert Path(version.local_path).read_bytes() == original


def test_image_map_and_order_survive_text_compaction_and_one_provider_overflow(tmp_path):
    service, project_id, run_id = context_service(tmp_path)
    visual = PreparedVisualInput(("data:image/jpeg;base64,first", "data:image/jpeg;base64,second"),
        {"images": [{"image_index": 1, "version": "crop"}, {"image_index": 2, "version": "source"}], "unavailable": []})
    payload = {"goal": {"original_request": "Keep both comparison images"},
        "working_set": {"read_observations": [{"result": "Long saved detail. " * 70000}]}}
    compiler = ModelInputCompiler(service.agent_runtime.store)
    calls = []

    class OverflowOnce:
        def validate_credentials(self):
            pass

        def generate_text(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise LanguageProviderContextWindowError("context_length_exceeded")
            return "continued"

    # First input is below normal compaction threshold; provider overflow then offloads it.
    payload["working_set"]["read_observations"][0]["result"] = "Saved detail. " * 18000
    assert generate_context_managed_text(OverflowOnce(), compiler, run_id=run_id, project_id=project_id,
        instructions="Compare actual pixels", payload=payload, max_output_tokens=20000, visual_input=visual) == "continued"
    assert calls[0]["image_urls"] == calls[1]["image_urls"] == list(visual.image_urls)
    assert json.loads(calls[0]["input_text"])["image_observations"] == json.loads(calls[1]["input_text"])["image_observations"]
    assert len(calls[1]["input_text"]) < len(calls[0]["input_text"])
    normal = compiler.prepare(run_id=run_id, project_id=project_id, instructions="Normal working context",
        payload={"work_order": {"task_inputs": {"facts": " word" * 105000}}}, max_output_tokens=20000, visual_input=visual)
    assert 100000 < normal.input_tokens < 110000 and normal.offloaded_blocks == 0
    assert normal.reserved_visual_tokens == reserved_visual_tokens(2)
    assert ContextBudget().input_limit(20000, visual_tokens=normal.reserved_visual_tokens) == 236976 - 16384


def test_real_client_serializes_ordered_input_images_and_reports_text_vs_visual_allowance(tmp_path):
    session = FakeSession(FakeResponse(200, {"output_text": "observed"}))
    client = OpenAICompatibleLanguageClient(api_key="local-test", base_url="http://127.0.0.1:4141/v1", session=session)
    events = []
    with production_trace(tmp_path / "trace", lambda kind, data: events.append((kind, data))):
        assert client.generate_text(instructions="Inspect", input_text="Exact image map", image_urls=["first", "second"]) == "observed"
    content = session.post_calls[0][1]["json"]["input"][0]["content"]
    assert content == [{"type": "input_text", "text": "Exact image map"},
        {"type": "input_image", "image_url": "first"}, {"type": "input_image", "image_url": "second"}]
    preflight = next(data for kind, data in events if kind == "model.preflight")
    assert preflight["image_count"] == 2 and preflight["reserved_visual_tokens"] == 16384
    assert preflight["image_tokens_included"] is False


def test_context_pointer_failure_returns_real_parent_keys_without_guessing_aliases():
    saved = {"arguments": {"request": "long prompt"}, "result": {"output": {"route_plan_id": "saved-route"}}}
    with pytest.raises(ValueError, match='available keys=\\["arguments", "result"\\]'):
        pointer_value(saved, "/output")
    assert pointer_value(saved, "/result/output/route_plan_id") == "saved-route"
