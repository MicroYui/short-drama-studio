"""A prior clip's FILM placement is not a change to its generation source."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest, _source_identity
from short_drama.h3_models import H3ProviderConfigUpdate
from short_drama.models import EnqueueRequest, TaskStatus
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave
from tests.test_h3_explicit_reference_selection import BlockingCompiler
from tests.test_h3_generation_unit_language import FakePromptCompiler, _fixture
from tests.test_short_drama_h3 import FakeH3Client


def _adopt_previous(service: ShortDramaService, project_id: str) -> None:
    document = service.get_project(project_id).document
    service.enqueue(project_id, EnqueueRequest(shot_ids=[document.shots[1].id], candidate_count=1))
    service.drain(project_id, 50)
    _edit_previous(service, project_id, "initial_position")


def _source_fixture(tmp_path: Path, *, adopted: bool = True) -> tuple[ShortDramaService, FakePromptCompiler, str, str]:
    service, fake, document, generation, _unit, _request = _fixture(tmp_path)
    project_id = document.project.id
    if adopted:
        _adopt_previous(service, project_id)
    document = service.get_project(project_id).document
    unit = generation.generation_plan.units[1]
    fake.output_text = build_h3_generation_unit_prompt(document, unit,
        [shot for shot in document.shots if shot.id in unit.shot_ids])
    return service, fake, project_id, unit.id


def _edit_previous(service: ShortDramaService, project_id: str, change: str) -> None:
    editorial = read_editorial(service, project_id)
    project = EditorProject.model_validate(editorial.project)
    clip = project.timelines[0].clips[0]
    if change == "initial_position":
        clip.startTime = 25.541667
    elif change == "position":
        clip.startTime -= 0.5
    elif change == "source_window":
        clip.trimStart += 0.5
        clip.duration -= 0.5
    elif change == "speed":
        clip.speed = 0.5
    elif change == "reversed":
        clip.reversed = True
    elif change == "take":
        take = service.repository.list_takes(project_id)[0]
        replacement = take.model_copy(deep=True, update={"id": "another-existing-take"})
        service.repository.save_take(replacement)
        clip.sourceTakeId = replacement.id
    else:
        raise AssertionError(change)
    service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=editorial.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))


@pytest.mark.parametrize("change", ["position", "source_window", "take", "speed", "reversed", "new_adoption"])
def test_preparation_rechecks_actual_predecessor_after_slow_compile(tmp_path: Path, change: str) -> None:
    original, fake, project_id, group_id = _source_fixture(tmp_path, adopted=change != "new_adoption")
    compiler = BlockingCompiler(fake.output_text)
    service = ShortDramaService(database_path=original.repository.database_path, workspace_root=original.workspace_root,
        language_provider_factory=lambda _key, _url: compiler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.prepare_h3_group, project_id,
            H3GroupOperationRequest(clip_group_id=group_id, media_inputs=[]))
        assert compiler.started.wait(10)
        try:
            if change == "new_adoption":
                _adopt_previous(original, project_id)
            else:
                _edit_previous(original, project_id, change)
        finally:
            compiler.release.set()
        result = future.result(timeout=10)
    assert result.status == ("PREPARED" if change == "position" else "INPUTS_CHANGED"), result.error
    operation = service.repository.get_h3_group_operation(result.operation_id)
    assert operation is not None and operation.compiled_prompt == compiler.output_text
    assert len(compiler.calls) == 1
    assert bool(service.repository.list_h3_route_plans(project_id)) == (change == "position")
    assert all(task.provider == "mock" for task in service.repository.list_tasks(project_id))


@pytest.mark.parametrize("change", ["position", "source_window", "take", "speed", "reversed"])
def test_reopened_preparation_and_worker_share_previous_source_currentness(tmp_path: Path, change: str) -> None:
    service, fake, project_id, group_id = _source_fixture(tmp_path)
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    prepared = service.prepare_h3_group(project_id, H3GroupOperationRequest(clip_group_id=group_id, media_inputs=[]))
    assert prepared.status == "PREPARED", prepared.error
    operation = service.repository.get_h3_group_operation(prepared.operation_id)
    assert operation is not None
    # Simulate a preparation saved before the optional extraction hint existed.
    source_context = operation.input_context.get("source_context")
    assert isinstance(source_context, dict)
    boundary = source_context.get("actual_previous_media_boundary")
    assert isinstance(boundary, dict)
    boundary.pop("extract_frame_request")
    service.repository.save_h3_group_operation(operation)
    saved_context = deepcopy(operation.input_context)
    _edit_previous(service, project_id, "position")
    client = FakeH3Client()
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: fake, h3_client_factory=lambda _key: client)
    # This also exercises route execution's saved-operation check before queueing.
    resumed = reopened.prepare_h3_group(project_id,
        H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert resumed.status == "WAITING_MEDIA", resumed.error
    assert resumed.route_plan_id == prepared.route_plan_id and len(resumed.task_ids) == 1
    assert len(fake.calls) == 1
    task = reopened.repository.get_task(resumed.task_ids[0])
    assert task is not None and reopened.h3_worker.input_staleness_reason(task) is None
    # Another shift or a real media/control change occurs after queueing.
    _edit_previous(reopened, project_id, change)
    reason = reopened.h3_worker.input_staleness_reason(task)
    if change != "position":
        assert reason is not None and "saved H3 operation source" in reason
        failed = reopened.h3_worker.advance(task.id)
        assert failed.status == TaskStatus.FAILED and failed.error_code == "H3_INPUTS_STALE"
        assert not client.upload_calls and not client.submitted_requests
    else:
        assert reason is None
        uploaded = reopened.h3_worker.advance(task.id)
        assert uploaded.status == TaskStatus.SUBMITTING
        # The actual submission boundary rechecks the same source too.
        _edit_previous(reopened, project_id, "position")
        submitted = reopened.h3_worker.advance(task.id)
        assert submitted.status == TaskStatus.ACKNOWLEDGED and len(client.submitted_requests) == 1
    assert operation.input_context == saved_context  # Comparison never mutates a loaded record.
    assert len(fake.calls) == 1


def test_comparison_only_omits_film_placement_keeps_boundary_evidence_and_history() -> None:
    boundary: dict[str, JsonValue] = {
        "status": "ADOPTED_MEDIA", "take_id": "prior-take", "media_path": "/local/prior.mp4",
        "source_in_seconds": 3, "source_out_seconds": 9.541667, "speed": 1, "reversed": False,
        "timeline_start_seconds": 25.541667, "timeline_end_seconds": 32.083334,
        "playback_end_source_seconds": 9.541667, "inspection_id": "saved-inspection",
        "observation_status": "TIMESTAMPED_VISUAL_EVIDENCE",
        "frame_samples": [{"timestamp_seconds": 9.416667, "image_path": "/local/end.jpg"}],
    }
    original: dict[str, JsonValue] = {"source_context": {"actual_previous_media_boundary": boundary}}
    before = deepcopy(original)
    moved = deepcopy(boundary)
    moved.update(timeline_start_seconds=25.041667, timeline_end_seconds=31.583334)
    # Older saved preparations have no derived extraction hint. Adding it must
    # not invalidate the same Take/window or mutate the historical snapshot.
    moved["extract_frame_request"] = {"project_id": "project", "request": {
        "take_id": "prior-take", "clip_id": "clip", "timeline_id": "timeline",
        "time_seconds": 6.541667, "description": "Optional endpoint reference",
    }}
    assert _source_identity(original) == _source_identity({"source_context": {"actual_previous_media_boundary": moved}})
    for key, value in {
        "status": "NO_ADOPTED_PREVIOUS_MEDIA", "take_id": "other-take", "media_path": "/local/other.mp4",
        "source_in_seconds": 4, "source_out_seconds": 8, "speed": 0.5, "reversed": True,
        "playback_end_source_seconds": 3, "inspection_id": "new-inspection",
        "observation_status": "UNKNOWN", "frame_samples": [],
    }.items():
        changed = deepcopy(moved)
        changed[key] = cast(JsonValue, value)
        assert _source_identity(original) != _source_identity({"source_context": {"actual_previous_media_boundary": changed}}), key
    assert original == before
