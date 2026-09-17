"""Structured local observability for persisted provider tasks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from zipfile import ZipFile

import pytest

from short_drama.models import EnqueueRequest, TaskStatus
from short_drama.observability import LOGGER_NAME, log_language_generation
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace, trace_context
from short_drama.studio_trace_export import export_studio_trace


def test_provider_task_transition_is_structured_and_content_free(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    service = ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    project = service.initialize_fixture()
    aggregate = service.enqueue(
        project.document.project.id,
        EnqueueRequest(
            shot_ids=[project.document.shots[0].id],
            candidate_count=1,
        ),
    )
    task = aggregate.tasks[0]

    prepared = service.advance_task(task.id)

    assert prepared.status == TaskStatus.SUBMITTING
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("event")
        == "provider.task_transition"
    ]
    assert len(events) == 2
    assert events[0] == {
        "action": "enqueue",
        "error_kind": None,
        "event": "provider.task_transition",
        "parent_task_id": None,
        "previous_status": None,
        "project_id": project.document.project.id,
        "provider": "mock",
        "provider_task_id": None,
        "status": "QUEUED",
        "task_id": task.id,
        "task_kind": "video",
        "timestamp": events[0]["timestamp"],
    }
    assert events[1] == {
        "action": "advance",
        "error_kind": None,
        "event": "provider.task_transition",
        "parent_task_id": None,
        "previous_status": "QUEUED",
        "project_id": project.document.project.id,
        "provider": "mock",
        "provider_task_id": None,
        "status": "SUBMITTING",
        "task_id": task.id,
        "task_kind": "video",
        "timestamp": events[1]["timestamp"],
    }
    serialized = json.dumps(events, ensure_ascii=False)
    assert "prompt" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()


def test_mock_drain_logs_only_tasks_that_changed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    service = ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    project = service.initialize_fixture()
    aggregate = service.enqueue(
        project.document.project.id,
        EnqueueRequest(shot_ids=[project.document.shots[0].id], candidate_count=1),
    )
    task = aggregate.tasks[0]
    caplog.clear()

    service.drain(project.document.project.id, max_steps=10)

    drain_events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("action") == "drain"
    ]
    assert len(drain_events) == 1
    assert drain_events[0]["task_id"] == task.id
    assert drain_events[0]["previous_status"] == "QUEUED"
    assert drain_events[0]["status"] == "SUCCEEDED"

    caplog.clear()
    service.drain(project.document.project.id, max_steps=10)
    assert [
        record
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("action") == "drain"
    ] == []


def test_usage_details_persist_in_sqlite_and_export_without_changing_legacy_events(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "usage.sqlite3", workspace_root=tmp_path / "workspace")
    pid = service.initialize_fixture().document.project.id
    legacy = service.repository.append_studio_event(pid, "provider.language_generation", {
        "call_id": "old-call", "input_tokens": 12, "output_tokens": 3,
    })
    trace_dir = service.workspace_root / pid / "studio-trace" / "usage-job"
    with production_trace(trace_dir, lambda kind, data: service.repository.append_studio_event(pid, kind, data)), trace_context(job_id="usage-job"):
        log_language_generation(
            call_id="new-call", model="gpt-6-astra", reasoning_effort="xhigh", max_output_tokens=2000,
            image_count=1, response_metadata={"service_tier": "priority", "usage": {
                "input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                "cache_write_tokens": 0,
                "input_tokens_details": {"cached_tokens": 20, "text_tokens": 60, "image_tokens": 40},
                "output_tokens_details": {"reasoning_tokens": 30, "text_tokens": 10},
                "authorization": "Bearer must-never-be-recorded",
            }}, success=True, completion_type="json_response", elapsed_seconds=2,
            time_to_first_text_delta_seconds=None, instruction_characters=10, input_characters=20,
            output_characters=5,
        )
    reopened = ShortDramaService(database_path=tmp_path / "usage.sqlite3", workspace_root=tmp_path / "workspace")
    persisted = reopened.repository.list_studio_events(pid)
    assert next(item for item in persisted if item.id == legacy.id).data == legacy.data
    new = next(item.data for item in persisted if item.data.get("call_id") == "new-call")
    assert new["service_tier"] == "priority"
    assert new["cache_write_tokens"] == 0
    assert new["cached_input_tokens"] == 20
    assert "must-never-be-recorded" not in json.dumps(new)
    with ZipFile(export_studio_trace(reopened, pid)) as archive:
        summary = json.loads(archive.read("production-summary.json"))
        old_call, new_call = summary["language_calls"]
        assert old_call["input_tokens"] == 12
        assert old_call["service_tier"] is None
        assert old_call["cache_write_tokens"] is None
        assert old_call["usage"] is None
        assert new_call["usage"] == new["usage"]
        assert new_call["service_tier"] == "priority"
        assert new_call["cache_write_tokens"] == 0
        assert new_call["time_to_first_text_delta_seconds"] is None
        assert "must-never-be-recorded" not in archive.read("conversation.jsonl").decode()


@pytest.mark.parametrize("usage, expected_write", [
    ({"input_tokens_details": {"cache_write_tokens": 9}}, 9),
    ({"cache_creation_input_tokens": 7}, 7),
    ({"cache_write_tokens": 0}, 0),
    ({"input_tokens": True, "output_tokens": -1, "cache_write_tokens": "15"}, None),
])
def test_only_reported_valid_usage_counters_are_recorded(
    usage: dict[str, object], expected_write: int | None, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    log_language_generation(
        model="model", reasoning_effort="xhigh", max_output_tokens=100, image_count=0,
        response_metadata={"usage": usage}, success=False, completion_type="incomplete",
        elapsed_seconds=1, time_to_first_text_delta_seconds=None,
        instruction_characters=0, input_characters=0, output_characters=0,
    )
    event = json.loads(next(record.getMessage() for record in caplog.records if record.name == LOGGER_NAME))
    assert event["cache_write_tokens"] == expected_write
    assert event["input_tokens"] is None
    assert event["output_tokens"] is None
    assert event["service_tier"] is None
