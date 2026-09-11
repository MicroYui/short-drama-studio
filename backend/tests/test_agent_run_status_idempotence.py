"""Scheduler checks must not create a new event for an unchanged run status."""

import json
import logging
from pathlib import Path

import pytest

from short_drama.agent_runtime import AgentRun, AgentRunStatus, AgentRuntime, AgentRuntimeStore
from short_drama.agent_runtime.models import utc_now
from short_drama.observability import LOGGER_NAME


class RecordingStore(AgentRuntimeStore):
    run_saves = 0

    def save_run(self, run: AgentRun) -> None:
        self.run_saves += 1
        super().save_run(run)


@pytest.mark.parametrize("status", list(AgentRunStatus))
def test_repeated_same_status_does_not_write_or_repeat_the_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, status: AgentRunStatus,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    store = RecordingStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project", objective="Continue current work", root_agent="leader")
    current = runtime.set_run_status(run.id, status)
    saves = store.run_saves
    events = len(caplog.records)
    for _ in range(8):  # The shape of real events 4720–4727.
        assert runtime.set_run_status(run.id, status) == current
    assert store.run_saves == saves
    assert len(caplog.records) == events
    assert AgentRuntimeStore(store.database_path).get_run(run.id) == current


def test_real_transitions_keep_events_and_clear_terminal_timestamp(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    runtime = AgentRuntime(AgentRuntimeStore(tmp_path / "runtime.sqlite3"))
    run = runtime.create_run(project_id="project", objective="Continue current work", root_agent="leader")
    runtime.set_run_status(run.id, AgentRunStatus.WAITING)
    failed = runtime.set_run_status(run.id, AgentRunStatus.FAILED)
    assert failed.completed_at == failed.updated_at
    runtime.checkpoint(run.id, current_focus="Previous request failed", blockers=["Correct the invalid source"])
    resumed = runtime.set_run_status(run.id, AgentRunStatus.RUNNING)
    assert resumed.completed_at is None and resumed.updated_at >= failed.updated_at
    runtime.checkpoint(run.id, current_focus="Current source is available", blockers=[], next_actions=["Continue the saved work"])
    assert runtime.store.get_run_memory(run.id).blockers == []
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER_NAME]
    assert [(event["previous_status"], event["status"]) for event in events if event["event"] == "agent.run_status"] == [
        (None, "RUNNING"), ("RUNNING", "WAITING"), ("WAITING", "FAILED"), ("FAILED", "RUNNING")]


def test_reason_changes_and_cleanup_still_save_when_status_is_unchanged(tmp_path: Path) -> None:
    store = RecordingStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project", objective="Continue current work", root_agent="leader")
    failed = runtime.set_run_status(run.id, AgentRunStatus.FAILED)
    old = runtime.checkpoint(run.id, current_focus="Invalid request", blockers=["Wrong version"])
    runtime.set_run_status(run.id, AgentRunStatus.FAILED)
    changed = runtime.checkpoint(run.id, current_focus="Missing source", blockers=["Provide the current source"], open_questions=["Which source?"])
    assert changed.updated_at >= old.updated_at
    assert store.get_run_memory(run.id) == changed
    assert store.get_run(run.id) == failed
    runtime.set_run_status(run.id, AgentRunStatus.FAILED)
    cleared = runtime.checkpoint(run.id, current_focus="Repair recorded", blockers=[], open_questions=[])
    assert store.get_run_memory(run.id) == cleared
    assert cleared.blockers == [] and cleared.open_questions == []


@pytest.mark.parametrize("status", [AgentRunStatus.RUNNING, AgentRunStatus.FAILED])
def test_same_status_still_repairs_inconsistent_legacy_completion_timestamp(tmp_path: Path, status: AgentRunStatus) -> None:
    store = RecordingStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project", objective="Continue current work", root_agent="leader")
    run.status = status
    run.completed_at = utc_now() if status == AgentRunStatus.RUNNING else None
    store.save_run(run)
    saves = store.run_saves
    repaired = runtime.set_run_status(run.id, status)
    assert (repaired.completed_at is not None) == (status == AgentRunStatus.FAILED)
    assert store.run_saves == saves + 1
    runtime.set_run_status(run.id, status)
    assert store.run_saves == saves + 1
