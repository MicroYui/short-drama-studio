"""A complete Worker Tool decision survives a trailing-whitespace output limit."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from requests import Session

from short_drama.agent_runtime import SpecialistTurnStatus, WorkOrderNextOwner
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.observability import LOGGER_NAME
from short_drama.production_manager import LanguageSpecialistAgent, ProductionManagerError
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderIncompleteError, OpenAICompatibleLanguageClient,
)
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace
from short_drama.tools.video import VideoInspectTakeInput
from tests.test_language_provider import FakeResponse, FakeSession
from tests.test_studio_harness import HarnessLanguageClient, _done, _service
from tests.test_worker_recovery_ownership import _delegate


FIXTURE = Path(__file__).parent / "fixtures/farewell_worker_incomplete.json"


def _incomplete(text: str | None, *, reason: str = "max_output_tokens") -> LanguageProviderIncompleteError:
    return LanguageProviderIncompleteError(reason=reason, partial_output_characters=len(text or ""),
                                           partial_output_text=text)


def _read(payload: dict[str, Any]) -> dict[str, Any]:
    return {"tool_name": "artifact.read_current", "reason": "Read the saved current project.",
            "arguments": {"project_id": payload["context"]["project_id"]},
            "working_notes": "Keep prior saved results; use this current read."}


def test_actual_739_character_object_preserves_incomplete_trace_and_provider_usage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    fixture = json.loads(FIXTURE.read_text())
    text: str = fixture["partial_output_text"]
    complete = text.rstrip(" \t\r\n")
    assert len(complete) == 739 and len(text) == 72108
    request = json.loads(complete)
    terminal = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 61816, "output_tokens": 20000, "total_tokens": 81816,
                          "output_tokens_details": {"reasoning_tokens": 516}}}
    response = FakeResponse(200, {}, headers={"Content-Type": "text/event-stream"}, stream_lines=[
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": complete}), "",
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": text[len(complete):]}), "",
        "data: " + json.dumps({"type": "response.incomplete", "response": terminal}), "",
    ])
    session = FakeSession(response)
    client = OpenAICompatibleLanguageClient(api_key="", base_url="http://127.0.0.1:4141/v1",
                                           session=cast(Session, session))
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
                               language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    _run_id, work_id, _ = _delegate(service, project_id)
    before = service.get_project(project_id).model_dump(mode="json")
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, value: events.append((kind, value))):
        service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.next_owner == WorkOrderNextOwner.WORKER_TOOL
    assert len(work.specialist_turns) == 1
    turn = work.specialist_turns[0]
    assert turn.status == SpecialistTurnStatus.REQUESTED and turn.result is None
    assert turn.tool_name == request["tool_name"] and turn.arguments == request["arguments"]
    assert work.working_notes == request["working_notes"]
    parsed = VideoInspectTakeInput.model_validate(turn.arguments)
    assert parsed.request.keyframe_times_seconds == [3.37499, 6.04166, 10.16666]
    assert parsed.request.semantic_review is False
    assert service.get_project(project_id).model_dump(mode="json") == before
    assert len(session.post_calls) == 1 and response.closed
    assert session.post_calls[0][1]["json"]["max_output_tokens"] == 20000
    traces = [json.loads(p.read_text()) for p in (tmp_path / "trace").glob("*.json")]
    incomplete = next(t["data"] for t in traces if t["kind"] == "model.incomplete")
    assert incomplete["text"] == text and incomplete["saved"] is False
    assert not any(t["kind"] == "model.output" for t in traces)
    recovery = next(value for kind, value in events if kind == "agent.worker_output_recovery")
    assert recovery["work_order_id"] == work_id
    assert recovery["recovered_json_characters"] == 739
    assert recovery["trailing_whitespace_characters"] == 71369
    assert recovery["outcome"] == "COMPLETE_TOOL_REQUEST_RECOVERED"
    assert not any(kind == "tool.started" for kind, _ in events)
    usage = next(json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME
                 and json.loads(r.getMessage()).get("event") == "provider.language_generation")
    assert usage["success"] is False and usage["completion_type"] == "sse_incomplete_max_output_tokens"
    assert usage["input_tokens"] == 61816 and usage["output_tokens"] == 20000
    assert usage["reasoning_tokens"] == 516 and usage["error_kind"] == "LanguageProviderIncompleteError"


@pytest.mark.parametrize("boundary_reason", ["pause requested", "new user input arrived"])
def test_recovered_tool_uses_existing_boundary_once_and_preserves_prior_results(
    tmp_path: Path, boundary_reason: str,
) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if len(client.inputs) == 1:
            return {"tool_name": "project.get", "arguments": {"project_id": payload["context"]["project_id"]}}
        if len(client.inputs) == 2:
            raise _incomplete(json.dumps(_read(payload)) + "\n \n")
        return _done("Read the current saved facts; no production change.")

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, _ = _delegate(service, project_id)
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    prior = service.agent_runtime.store.get_work_order(work_id)
    assert prior is not None and prior.specialist_turns[0].result is not None
    saved_turn = prior.specialist_turns[0].model_copy(deep=True)
    before = service.get_project(project_id).model_dump(mode="json")
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, value: events.append((kind, value))):
        service.execute_agent_work_order(work_id)  # Recover and persist a decision, not execute it.

        def stopped() -> None:
            raise ExecutionYielded(boundary_reason)

        with execution_control(stopped), pytest.raises(ExecutionYielded, match=boundary_reason):
            service.execute_agent_work_order(work_id)
        pending = service.agent_runtime.store.get_work_order(work_id)
        assert pending is not None and len(pending.specialist_turns) == 2
        assert pending.specialist_turns[0] == saved_turn
        assert pending.specialist_turns[1].result is None
        service.execute_agent_work_order(work_id)  # Normal resume executes just the new read.
        service.execute_agent_work_order(work_id)  # Normal next decision completes the work.
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and len(work.specialist_turns) == 2
    assert work.specialist_turns[0] == saved_turn
    assert work.specialist_turns[1].result is not None
    assert work.specialist_turns[1].result["status"] == "SUCCEEDED"
    assert [value["tool_name"] for kind, value in events if kind == "tool.started"] == ["artifact.read_current"]
    assert len(client.inputs) == 3
    assert service.get_project(project_id).model_dump(mode="json") == before


@pytest.mark.parametrize("shape", [
    "truncated", "two_objects", "array", "prefix", "fence", "suffix", "no_tail",
    "other_reason", "invalid_envelope", "unknown_tool", "missing_text", "blank",
])
def test_other_incomplete_outputs_keep_existing_failure_without_tool_action(tmp_path: Path, shape: str) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        request = _read(payload)
        text: str | None = json.dumps(request) + "\n "
        reason = "max_output_tokens"
        if shape == "truncated":
            text = json.dumps(request)[:-1] + "\n "
        elif shape == "two_objects":
            text = json.dumps(request) + " {}\n "
        elif shape == "array":
            text = json.dumps([request]) + "\n "
        elif shape == "prefix":
            text = "I will read the project. " + text
        elif shape == "fence":
            text = "```json\n" + text + "```\n"
        elif shape == "suffix":
            text += "Continue after this.\n"
        elif shape == "no_tail":
            text = json.dumps(request)
        elif shape == "other_reason":
            reason = "content_filter"
        elif shape == "invalid_envelope":
            text = json.dumps({**request, "unrecognized_decision": True}) + "\n "
        elif shape == "unknown_tool":
            text = json.dumps({**request, "tool_name": "unknown.unsupported"}) + "\n "
        elif shape == "missing_text":
            text = None
        elif shape == "blank":
            text = "\n \n"
        raise _incomplete(text, reason=reason)

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, _ = _delegate(service, project_id)
    before = service.get_project(project_id).model_dump(mode="json")
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, value: events.append((kind, value))):
        with pytest.raises(ProductionManagerError, match="generation incomplete"):
            service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and work.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert not work.specialist_turns and len(client.inputs) == 1
    assert not any(kind in {"tool.started", "agent.worker_output_recovery"} for kind, _ in events)
    assert service.get_project(project_id).model_dump(mode="json") == before


def test_recovered_envelope_does_not_bypass_tool_argument_validation(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        request = _read(payload)
        request["arguments"] = {"project_id": 42}
        raise _incomplete(json.dumps(request) + "\n ")

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, _ = _delegate(service, project_id)
    before = service.get_project(project_id).model_dump(mode="json")
    service.execute_agent_work_order(work_id)
    service.execute_agent_work_order(work_id)
    work = service.agent_runtime.store.get_work_order(work_id)
    assert work is not None and len(work.specialist_turns) == 1
    result = work.specialist_turns[0].result
    assert result is not None and result["status"] == "INVALID_ARGUMENTS"
    assert service.get_project(project_id).model_dump(mode="json") == before
    assert len(client.inputs) == 1


def test_result_only_worker_preserves_original_incomplete_error(tmp_path: Path) -> None:
    raised: list[LanguageProviderIncompleteError] = []

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        error = _incomplete(json.dumps(_read(payload)) + "\n ")
        raised.append(error)
        raise error

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    _run_id, work_id, _ = _delegate(service, project_id)
    view = service.get_agent_work_order(work_id)
    before = view.model_copy(deep=True)
    agent = LanguageSpecialistAgent(client)  # Existing result-only mode, without Tools.
    events: list[tuple[str, dict[str, Any]]] = []
    with production_trace(tmp_path / "trace", lambda kind, value: events.append((kind, value))):
        with pytest.raises(LanguageProviderIncompleteError) as caught:
            agent.execute(view)
    assert len(raised) == 1 and caught.value is raised[0]
    assert view == before and not service.get_agent_work_order(work_id).work_order.specialist_turns
    assert not any(kind in {"tool.started", "agent.worker_output_recovery"} for kind, _ in events)
