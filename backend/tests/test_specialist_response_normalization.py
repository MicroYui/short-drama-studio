"""Observed Worker serialization errors do not require a second model decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
import requests

from short_drama.agent_runtime import AgentResultStatus, AgentRuntimeStore, WorkOrderNextOwner
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import (
    AgentWorkOrderView,
    LanguageSpecialistAgent,
    SpecialistToolRequest,
    _normalize_specialist_response_payload,
)
from short_drama.providers.openai_compatible_language_client import OpenAICompatibleLanguageClient
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace
from short_drama.tools import ToolCallStatus
from tests.test_harness_tool_context import registry, runtime_work
from tests.test_language_provider import FakeResponse, FakeSession
from tests.test_production_manager import SequencedLanguageClient, _start_pending_delegation


_FIXTURES = cast(list[dict[str, JsonValue]], json.loads(
    (Path(__file__).parent / "fixtures/specialist_response_serialization.json").read_text()
))


@pytest.mark.parametrize("case", _FIXTURES, ids=[str(case["call_id"]) for case in _FIXTURES])
def test_actual_eight_responses_keep_every_tool_argument_and_working_note(
    case: dict[str, JsonValue],
) -> None:
    raw = cast(str, case["raw_response"])
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        json.loads(raw)
    normalized = _normalize_specialist_response_payload(raw)
    expected = cast(dict[str, JsonValue], case["expected_request"])
    assert json.loads(normalized) == expected
    request = SpecialistToolRequest.model_validate_json(normalized)
    assert request.model_dump(mode="json") == expected
    assert request.working_notes
    assert _normalize_specialist_response_payload(normalized) == normalized


@pytest.mark.parametrize("payload", [
    '{"tool_name":"project.get","arguments":{},"working_notes":"one"}} trailing prose',
    '{"tool_name":"project.get","arguments":{},"working_notes":"one"}}}',
    '{"tool_name":"project.get","arguments":{},"working_notes":"one"}\n{"tool_name":"project.get","arguments":{}}',
    '{"tool_name":"project.get","arguments":{},"working_notes":"one"},"working_notes":"two"}',
    '{"tool_name":"project.get","arguments":{},"reason":"first"},"reason":"second","working_notes":"note"}',
    '{"tool_name":"project.get","arguments":{}},"arguments":{"project_id":"changed"}}',
    '{"tool_name":"project.get","arguments":{}},"tool_name":"other"}',
    '{"tool_name":"project.get","arguments":{}},"working_notes":"keep","unknown":true}',
    '{"tool_name":"project.get","arguments":{}},"working_notes":"one","working_notes":"two"}',
    '{"tool_name":"project.get","arguments":{"project_id":"one","project_id":"two"},"working_notes":"note"}}',
    '{"tool_name":"project.get","arguments":{}},"working_notes":"unfinished',
    '{"tool_name":"project.get","arguments":{"nested":',
])
def test_ambiguous_suffixes_and_incomplete_arguments_stay_in_protocol_feedback(payload: str) -> None:
    assert _normalize_specialist_response_payload(payload) == payload


@pytest.mark.parametrize("suffix", ["}", ' , "working_notes": "Keep \\\"quotes\\\", } and \\u4e2d" }'])
def test_normalization_preserves_original_number_and_string_spelling(suffix: str) -> None:
    prefix = ('{ "tool_name": "project.get", "arguments": '
              '{"decimal": 1.234567890123456789, "integer": 9007199254740993, '
              '"exponent": 1e-09, "escaped": "\\u4e2d\\n{brace}"}}')
    normalized = _normalize_specialist_response_payload(prefix + suffix)
    expected = prefix if suffix == "}" else prefix[:-1] + suffix
    assert normalized == expected


def test_missing_nonexecuting_fields_can_continue_together_without_replacing_arguments() -> None:
    payload = ('{"tool_name":"project.get","arguments":{"project_id":"current"}}'
               ',"reason":"Read the current project","working_notes":null}')
    request = SpecialistToolRequest.model_validate_json(_normalize_specialist_response_payload(payload))
    assert request.arguments == {"project_id": "current"}
    assert request.reason == "Read the current project"
    assert request.working_notes is None


def test_normalization_does_not_bypass_existing_envelope_or_registry_validation() -> None:
    invalid_notes = json.dumps({"tool_name": "project.get", "arguments": {}, "working_notes": ["invalid"]}) + "}"
    with pytest.raises(ValidationError):
        SpecialistToolRequest.model_validate_json(_normalize_specialist_response_payload(invalid_notes))
    missing_project_id = json.dumps({"tool_name": "project.get", "arguments": {}, "working_notes": "Keep"}) + "}"
    request = SpecialistToolRequest.model_validate_json(_normalize_specialist_response_payload(missing_project_id))
    result = registry().invoke(request.tool_name, request.arguments)
    assert result.status == ToolCallStatus.INVALID_ARGUMENTS


@pytest.mark.parametrize("shape", ["extra_closing_brace", "continued_working_notes"])
def test_one_worker_decision_persists_the_exact_request_and_notes_across_restart(
    tmp_path: Path, shape: str,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    _run_id, work_id, _action_id = _start_pending_delegation(service, project_id)
    expected = {"tool_name": "project.get", "arguments": {"project_id": project_id},
        "reason": "Read the current project", "working_notes": 'Preserve the current state, literal } and "quote".'}
    if shape == "extra_closing_brace":
        raw = json.dumps(expected, ensure_ascii=False) + "}"
    else:
        raw = json.dumps({key: value for key, value in expected.items() if key != "working_notes"})
        raw += ',"working_notes":' + json.dumps(expected["working_notes"]) + "}"
    client.responses = [raw]
    requested = service.execute_agent_work_order(work_id)
    assert requested.result.status == AgentResultStatus.PARTIAL
    assert len(client.inputs) == 1
    saved = AgentRuntimeStore(service.agent_runtime_store.database_path).get_work_order(work_id)
    assert saved is not None
    assert saved.pending_protocol_feedback is None
    assert saved.next_owner == WorkOrderNextOwner.WORKER_TOOL
    assert saved.working_notes == expected["working_notes"]
    assert len(saved.specialist_turns) == 1
    turn = saved.specialist_turns[0]
    assert turn.tool_name == expected["tool_name"]
    assert turn.arguments == expected["arguments"]
    assert turn.reason == expected["reason"]


def test_real_language_adapter_traces_original_text_before_worker_normalization(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    expected = {"tool_name": "project.get", "arguments": {"project_id": context.project_id},
        "reason": "Read", "working_notes": "Preserve this exact note"}
    raw = json.dumps(expected) + "}"
    session = FakeSession(FakeResponse(200, {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": raw}]}]}))
    client = OpenAICompatibleLanguageClient(api_key="", base_url="http://127.0.0.1:4141/v1",
        session=cast(requests.Session, session))
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(store)
    events: list[tuple[str, dict[str, JsonValue]]] = []
    with production_trace(tmp_path / "trace", lambda kind, data: events.append((kind, data))):
        response = worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Read current state."))
    assert isinstance(response, SpecialistToolRequest)
    assert response.model_dump(mode="json") == expected
    assert len(session.post_calls) == 1
    assert len([data for kind, data in events if kind == "model.started"]) == 1
    files = list((tmp_path / "trace").glob("*model.output.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["data"]["text"] == raw
