"""Structured local observability for persisted provider tasks."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from short_drama.models import EnqueueRequest, TaskStatus
from short_drama.observability import LOGGER_NAME
from short_drama.service import ShortDramaService


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
