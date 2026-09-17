"""Read-batch images survive persistence without claiming failed Tools were seen."""
from __future__ import annotations

import base64
from copy import deepcopy
from io import BytesIO

from PIL import Image
import pytest

from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord
from short_drama.agent_runtime.visual_input import prepare_worker_visual_input
from short_drama.image_models import AssetImportLocalRequest
from short_drama.production_manager import _specialist_tool_result_for_persistence
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_production_manager import _start_pending_delegation
from tests.test_short_drama_assets import FakeImageProvider, _service


@pytest.fixture
def visual_case(tmp_path):
    provider = FakeImageProvider()
    service = _service(tmp_path, provider)
    asset = service.initialize_fixture().document.assets[0]
    results = []
    paths = []
    for name, color in (("red", "#ff0000"), ("green", "#00ff00"), ("blue", "#0000ff")):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (32, 24), color).save(path)
        version = service.import_local_asset_version(asset.project_id, asset.id,
            AssetImportLocalRequest(local_path=str(path), descriptor=f"Diagnostic {name} pixels")).version
        paths.append(version.local_path)
        arguments = {"project_id": asset.project_id, "images": [{"asset_version_id": version.id}],
            "purpose": f"Read exact {name} reference"}
        result = service.tool_registry.invoke("image.view", arguments)
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        results.append(_specialist_tool_result_for_persistence(result.tool_id,
            result.model_dump(mode="json"), arguments=arguments))
    _run, work_id, _action = _start_pending_delegation(service, asset.project_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None
    return service, provider, asset.project_id, work, results, paths


def _turn(sequence, result, *, batch_id="read-batch-current", status="COMPLETED", name="image.view"):
    return SpecialistTurnRecord(id=f"attempt-{sequence}", sequence=sequence, batch_id=batch_id,
        tool_name=name, reason="Read independently selected current facts", status=status, result=result)


def _color(image_url):
    with Image.open(BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as image:
        pixel = image.convert("RGB").getpixel((10, 10))
    return max(range(3), key=lambda index: pixel[index])


def test_batch_saved_images_keep_item_identity_and_order_when_last_tool_failed(visual_case):
    service, provider, project_id, work, results, _paths = visual_case
    before = service.get_project(project_id).document
    failed = {**deepcopy(results[2]), "status": "FAILED"}
    # The persisted list need not itself be in sequence order. The old batch and
    # attachment-looking metadata on the failed last Tool must not be replayed.
    work.specialist_turns = [
        _turn(12, results[1]),
        _turn(2, results[2], batch_id="older-batch"),
        _turn(10, results[0]),
        _turn(13, failed, name="timeline.read_export"),
        _turn(11, {"status": "SUCCEEDED", "output": {"text": "metadata only"}}, name="artifact.read_current"),
    ]
    service.agent_runtime.store.save_work_order(work)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    saved = reopened.agent_runtime.store.get_work_order(work.id)
    assert saved is not None
    actual = prepare_worker_visual_input(saved, project_id=project_id)
    assert actual is not None
    assert [_color(url) for url in actual.image_urls] == [0, 1]
    assert actual.observations["batch_id"] == "read-batch-current"
    assert [(item["source_sequence"], item["tool_attempt_id"], item["selection_index"], item["image_index"])
        for item in actual.observations["images"]] == [(10, "attempt-10", 1, 1), (12, "attempt-12", 1, 2)]
    attempts = actual.observations["tool_attempts"]
    assert [item["source_sequence"] for item in attempts] == [10, 11, 12, 13]
    assert attempts[-1]["result_status"] == "FAILED"
    assert actual.observations["unavailable"] == []
    assert reopened.get_project(project_id).document == before
    assert provider.calls == []


def test_batch_selection_failures_keep_local_indices_without_gaps_in_image_indices(visual_case):
    _service, _provider, project_id, work, results, paths = visual_case
    first = deepcopy(results[0])
    missing = deepcopy(first["visual_attachments"][0])
    missing["local_path"] = str(paths[0]) + ".missing"
    foreign = deepcopy(first["visual_attachments"][0])
    foreign["artifact_ref"]["project_id"] = "other-project"
    first["visual_attachments"] = [missing, {"unexpected": "invalid"}, foreign, first["visual_attachments"][0]]
    second = deepcopy(results[1])
    second["visual_attachments"] += deepcopy(results[2]["visual_attachments"])
    work.specialist_turns = [_turn(1, first), _turn(2, second)]
    actual = prepare_worker_visual_input(work, project_id=project_id)
    assert actual is not None
    assert [_color(url) for url in actual.image_urls] == [0, 1, 2]
    assert [(item["source_sequence"], item["selection_index"], item["image_index"])
        for item in actual.observations["images"]] == [(1, 4, 1), (2, 1, 2), (2, 2, 3)]
    assert [(item["source_sequence"], item["selection_index"], item["tool_attempt_id"])
        for item in actual.observations["unavailable"]] == [(1, 1, "attempt-1"), (1, 2, "attempt-1"), (1, 3, "attempt-1")]
    assert "Invalid image selection metadata" in actual.observations["unavailable"][1]["error"]
    assert "different project" in actual.observations["unavailable"][2]["error"]


@pytest.mark.parametrize("pending_status", ["REQUESTED", "IN_PROGRESS"])
def test_pending_batch_never_supplies_partial_decision_images(visual_case, pending_status):
    _service, _provider, project_id, work, results, _paths = visual_case
    work.specialist_turns = [_turn(1, results[0]), _turn(2, None, status=pending_status)]
    assert prepare_worker_visual_input(work, project_id=project_id) is None
    work.specialist_turns[1].status = "INTERRUPTED"
    actual = prepare_worker_visual_input(work, project_id=project_id)
    assert actual is not None and len(actual.image_urls) == 1
    assert actual.observations["tool_attempts"][1]["status"] == "INTERRUPTED"


def test_no_attachment_inference_or_replay_after_a_later_single_read(visual_case):
    _service, _provider, project_id, work, results, _paths = visual_case
    work.specialist_turns = [_turn(1, results[0]), _turn(2, {"status": "SUCCEEDED"})]
    assert prepare_worker_visual_input(work, project_id=project_id) is not None
    work.specialist_turns.append(_turn(3, {"status": "SUCCEEDED", "output": {"image_path": "not-a-selection"}},
        batch_id=None, name="artifact.read_current"))
    assert prepare_worker_visual_input(work, project_id=project_id) is None
    work.specialist_turns = [_turn(4, {"status": "SUCCEEDED", "output": {"image": results[0]}}),
        _turn(5, {"status": "FAILED", "visual_attachments": results[0]["visual_attachments"]})]
    assert prepare_worker_visual_input(work, project_id=project_id) is None


def test_batch_reports_all_unavailable_selections_without_claiming_pixels(visual_case):
    _service, _provider, project_id, work, results, paths = visual_case
    missing = deepcopy(results[0])
    missing["visual_attachments"][0]["local_path"] = str(paths[0]) + ".missing"
    work.specialist_turns = [_turn(1, missing), _turn(2, {"status": "SUCCEEDED"})]
    actual = prepare_worker_visual_input(work, project_id=project_id)
    assert actual is not None and actual.image_urls == ()
    assert actual.observations["images"] == []
    assert actual.observations["unavailable"][0]["tool_attempt_id"] == "attempt-1"


def test_explicit_export_frame_before_text_read_reaches_model_with_exact_provenance(visual_case):
    service, _provider, project_id, work, results, _paths = visual_case
    export = deepcopy(results[1])
    frame = export["visual_attachments"][0]
    frame["artifact_ref"] = {"project_id": project_id, "artifact_type": "studio_export",
        "artifact_id": "export-local-fixture", "version": 7}
    frame["asset_id"] = "export-local-fixture"
    frame["role"] = "SOURCE"
    frame["purpose"] = "Diagnostic export frame transport: requested 1.0s; actual PTS 1.041667s; revision 7"
    work.specialist_turns = [_turn(1, results[0]), _turn(2, export, name="timeline.read_export"),
        _turn(3, {"status": "SUCCEEDED", "output": {"text": "metadata"}}, name="artifact.read_current")]
    actual = prepare_worker_visual_input(work, project_id=project_id)
    assert actual is not None
    prepared = ModelInputCompiler(service.agent_runtime.store).prepare(run_id=work.parent_run_id,
        project_id=project_id, instructions="Compare the explicitly selected pixels",
        payload={"work_order_id": work.id}, max_output_tokens=2000, visual_input=actual)
    import json
    observed = json.loads(prepared.input_text)["image_observations"]
    assert prepared.image_urls == actual.image_urls
    assert [_color(url) for url in prepared.image_urls] == [0, 1]
    export_frame = observed["images"][1]
    assert export_frame["artifact_ref"] == frame["artifact_ref"]
    assert export_frame["purpose"] == frame["purpose"]
    assert export_frame["tool_name"] == "timeline.read_export"
    assert export_frame["source_sequence"] == 2 and export_frame["image_index"] == 2
