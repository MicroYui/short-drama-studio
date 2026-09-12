"""Offline configuration and HTTP coverage for the GPT-6 Astra connection."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.language_models import (
    LanguagePromptCompileRequest,
    LanguageProviderConfigUpdate,
)
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.models import ProductionProfile, VideoGenerationRoute
from short_drama.models import (
    ActionBeat,
    SpatialInteractionConstraint,
    SurfaceAttachmentConstraint,
)
from short_drama.observability import LOGGER_NAME
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
    LanguageProviderContextWindowError,
    LanguageProviderTransientError,
    LanguageProviderTransportError,
    OpenAICompatibleLanguageClient,
)
from short_drama.providers.h3_language_prompt import build_language_prompt_input
from short_drama.providers.minimax_h3_compiler import build_h3_prompt
from short_drama.service import (
    ProviderOperationError,
    ShortDramaService,
    WorkflowConflictError,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
        stream_lines: list[str | bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}
        self.stream_lines = stream_lines or []
        self.closed = False

    def json(self) -> object:
        return self.payload

    def iter_lines(self, *, decode_unicode: bool = False) -> list[str | bytes]:
        del decode_unicode
        return self.stream_lines

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(
        self,
        response: FakeResponse | Exception | list[FakeResponse | Exception],
    ) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_language_client_uses_configured_url_key_and_fixed_model() -> None:
    session = FakeSession(FakeResponse(200, {"id": "gpt-6-astra"}))
    client = OpenAICompatibleLanguageClient(
        api_key="secret-sol",
        base_url="https://sol.example.test/openai/v1/",
        session=session,  # type: ignore[arg-type]
    )

    client.validate_credentials()

    assert session.calls == [
        (
            "https://sol.example.test/openai/v1/models/gpt-6-astra",
            {
                "headers": {"Authorization": "Bearer secret-sol"},
                "timeout": 30.0,
            },
        )
    ]


def test_language_client_reports_transport_and_model_mismatch() -> None:
    offline = OpenAICompatibleLanguageClient(
        api_key="secret",
        base_url="https://sol.example.test/v1",
        session=FakeSession(requests.ConnectionError("offline")),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="could not reach"):
        offline.validate_credentials()

    wrong_model = OpenAICompatibleLanguageClient(
        api_key="secret",
        base_url="https://sol.example.test/v1",
        session=FakeSession(FakeResponse(200, {"id": "another-model"})),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="expected 'gpt-6-astra'"):
        wrong_model.validate_credentials()


def test_language_client_falls_back_to_model_list_for_local_gateway() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "not found"}}),
            FakeResponse(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "gpt-5.6-luna"},
                        {"id": "gpt-6-astra", "supported_endpoints": ["/responses"]},
                    ],
                },
            ),
        ]
    )
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=session,  # type: ignore[arg-type]
    )

    client.validate_credentials()

    assert [call[0] for call in session.calls] == [
        "http://localhost:4141/v1/models/gpt-6-astra",
        "http://localhost:4141/v1/models",
    ]
    assert all(call[1]["headers"] == {} for call in session.calls)


def test_language_client_rejects_model_list_without_astra() -> None:
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://127.0.0.1:4141/v1",
        session=FakeSession(
            [
                FakeResponse(404, {"error": {"message": "not found"}}),
                FakeResponse(200, {"object": "list", "data": [{"id": "gpt-5.6-luna"}]}),
            ]
        ),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="does not include 'gpt-6-astra'"):
        client.validate_credentials()


def test_language_client_generates_with_responses_api_and_reads_nested_text() -> None:
    session = FakeSession(
        FakeResponse(
            200,
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "最终镜头提示词"},
                        ],
                    }
                ],
            },
        )
    )
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1/",
        session=session,  # type: ignore[arg-type]
    )

    result = client.generate_text(instructions="只输出提示词", input_text="锁定事实")

    assert result == "最终镜头提示词"
    assert session.post_calls == [
        (
            "http://localhost:4141/v1/responses",
            {
                "headers": {"Content-Type": "application/json"},
                "json": {
                    "model": "gpt-6-astra",
                    "instructions": "只输出提示词",
                    "input": "锁定事实",
                    "reasoning": {"effort": "xhigh"},
                    "max_output_tokens": 2200,
                    "stream": True,
                },
                "timeout": (30.0, 600.0),
                "stream": True,
            },
        )
    ]


@pytest.mark.parametrize("streaming", [False, True])
def test_actual_usage_is_captured_after_text_for_both_response_formats(
    streaming: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    payload = {
        "id": "resp-actual", "model": "gpt-6-astra", "status": "completed",
        "output_text": "完成",
        "usage": {
            "input_tokens": 350, "output_tokens": 520, "total_tokens": 870,
            "input_tokens_details": {"cached_tokens": 120},
            "output_tokens_details": {"reasoning_tokens": 500},
        },
    }
    response = FakeResponse(
        200, payload,
        headers={"Content-Type": "text/event-stream"} if streaming else {},
        stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"完成"}', "",
            "event: response.completed",
            "data: " + json.dumps({"type": "response.completed", "response": payload}), "",
        ] if streaming else None,
    )
    deltas: list[str] = []
    session = FakeSession(response)
    client = OpenAICompatibleLanguageClient(
        api_key="", base_url="http://localhost:4141/v1",
        session=session,  # type: ignore[arg-type]
        text_delta_observer=deltas.append,
    )
    assert client.generate_text(instructions="当前任务", input_text="剧本") == "完成"
    event = next(json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME)
    assert event["model"] == event["response_model"] == "gpt-6-astra"
    assert event["reasoning_effort"] == "xhigh"
    assert event["response_id"] == "resp-actual"
    assert event["usage_available"] is True
    assert [event[k] for k in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")] == [350, 520, 870, 120, 500]
    assert deltas == (["完成"] if streaming else [])
    request = session.post_calls[0][1]["json"]
    assert request["reasoning"] == {"effort": "xhigh"}
    assert not {"temperature", "top_p", "top_logprobs", "logprobs"}.intersection(request)


def test_incomplete_json_does_not_publish_partial_text_and_keeps_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    client = OpenAICompatibleLanguageClient(
        api_key="", base_url="http://localhost:4141/v1",
        session=FakeSession(FakeResponse(200, {
            "status": "incomplete", "output_text": "{未完成",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 2200, "total_tokens": 2210},
        })),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="generation incomplete"):
        client.generate_text(instructions="生成剧本", input_text="校园")
    event = next(json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME)
    assert event["success"] is False
    assert event["output_tokens"] == 2200


def test_language_client_reads_streaming_responses_events() -> None:
    response = FakeResponse(
        200,
        {},
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
        stream_lines=[
            'event: response.created',
            'data: {"response":{"instructions":"unterminated',
            "",
            'event: response.output_text.delta',
            b'data: {"type":"response.output_text.delta",',
            'data: "delta":"最终"}'.encode(),
            "",
            'data: {"type":"response.output_text.delta","delta":"剧本"}',
            "",
            'data: {"type":"response.output_text.done","text":"最终剧本"}',
            "",
            'data: {"type":"response.completed","response":{"output_text":"最终剧本"}}',
            "",
            "data: [DONE]",
        ],
    )
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(response),  # type: ignore[arg-type]
    )

    result = client.generate_text(instructions="只输出剧本", input_text="锁定事实")

    assert result == "最终剧本"
    assert response.closed is True


def test_language_client_logs_content_free_stream_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    response = FakeResponse(
        200,
        {},
        headers={"Content-Type": "text/event-stream"},
        stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"最终"}',
            "",
            'data: {"type":"response.completed","response":{"output_text":"最终"}}',
            "",
        ],
    )
    clock_values = iter([10.0, 11.25, 14.0])
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        model="test-sol",
        session=FakeSession(response),  # type: ignore[arg-type]
        monotonic_clock=lambda: next(clock_values),
    )

    result = client.generate_text(instructions="只输出剧本", input_text="锁定事实")

    assert result == "最终"
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("event")
        == "provider.language_generation"
    ]
    assert events[0].pop("call_id").startswith("model-")
    assert events == [
        {
            "completion_type": "sse_response_completed",
            "elapsed_seconds": 4.0,
            "error_kind": None,
            "event": "provider.language_generation",
            "input_characters": 4,
            "instruction_characters": 5,
            "model": "test-sol",
            "reasoning_effort": "xhigh",
            "max_output_tokens": 2200,
            "image_count": 0,
            "response_id": None,
            "response_model": None,
            "usage_available": False,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "output_characters": 2,
            "provider": "openai_compatible",
            "success": True,
            "time_to_first_text_delta_seconds": 1.25,
            "timestamp": events[0]["timestamp"],
        }
    ]
    serialized = json.dumps(events, ensure_ascii=False)
    assert "只输出剧本" not in serialized
    assert "锁定事实" not in serialized
    assert "最终" not in serialized


def test_language_client_logs_transport_failure_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    clock_values = iter([20.0, 23.5])
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(requests.ConnectionError("offline")),  # type: ignore[arg-type]
        monotonic_clock=lambda: next(clock_values),
    )

    with pytest.raises(LanguageProviderTransportError, match="could not reach"):
        client.generate_text(instructions="instructions", input_text="input")

    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("event")
        == "provider.language_generation"
    )
    assert event["success"] is False
    assert event["completion_type"] == "transport_error"
    assert event["elapsed_seconds"] == 3.5
    assert event["time_to_first_text_delta_seconds"] is None
    assert event["instruction_characters"] == 12
    assert event["input_characters"] == 5
    assert event["output_characters"] == 0
    assert event["error_kind"] == "LanguageProviderTransportError"


def test_language_client_rejects_partial_stream_without_terminal_event() -> None:
    response = FakeResponse(
        200,
        {},
        headers={"Content-Type": "text/event-stream"},
        stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"未完"}',
        ],
    )
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(response),  # type: ignore[arg-type]
    )

    with pytest.raises(LanguageProviderTransportError, match="ended before completion"):
        client.generate_text(instructions="只输出剧本", input_text="锁定事实")

    assert response.closed is True


def test_language_client_keeps_first_delta_metric_when_stream_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    response = FakeResponse(
        200,
        {},
        headers={"Content-Type": "text/event-stream"},
        stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"未完"}',
        ],
    )
    clock_values = iter([30.0, 30.75, 32.0])
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(response),  # type: ignore[arg-type]
        monotonic_clock=lambda: next(clock_values),
    )

    with pytest.raises(LanguageProviderError, match="ended before completion"):
        client.generate_text(instructions="instructions", input_text="input")

    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("event")
        == "provider.language_generation"
    )
    assert event["success"] is False
    assert event["completion_type"] == "sse_error"
    assert event["elapsed_seconds"] == 2.0
    assert event["time_to_first_text_delta_seconds"] == 0.75


def test_language_client_reports_incomplete_stream_reason_and_partial_size(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    response = FakeResponse(
        200,
        {},
        headers={"Content-Type": "text/event-stream"},
        stream_lines=[
            'data: {"type":"response.output_text.delta","delta":"partial-json"}',
            "",
            (
                'data: {"type":"response.incomplete","response":'
                '{"status":"incomplete","incomplete_details":'
                '{"reason":"max_output_tokens"}}}'
            ),
            "",
        ],
    )
    clock_values = iter([40.0, 40.5, 42.0])
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(response),  # type: ignore[arg-type]
        monotonic_clock=lambda: next(clock_values),
    )

    with pytest.raises(
        LanguageProviderError,
        match="generation incomplete: max_output_tokens",
    ):
        client.generate_text(instructions="instructions", input_text="input")

    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
        and json.loads(record.getMessage()).get("event")
        == "provider.language_generation"
    )
    assert event["success"] is False
    assert event["completion_type"] == "sse_incomplete_max_output_tokens"
    assert event["time_to_first_text_delta_seconds"] == 0.5
    assert event["output_characters"] == len("partial-json")
    assert event["error_kind"] == "LanguageProviderIncompleteError"
    assert response.closed is True


def test_language_client_sends_multimodal_responses_input() -> None:
    session = FakeSession(FakeResponse(200, {"output_text": "结构化计划"}))
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=session,  # type: ignore[arg-type]
    )

    result = client.generate_text(
        instructions="读取视觉种子",
        input_text="生成短剧计划",
        image_urls=["data:image/jpeg;base64,AAAA", "https://example.test/seed.png"],
        max_output_tokens=4000,
    )

    assert result == "结构化计划"
    payload = session.post_calls[0][1]["json"]
    assert payload["max_output_tokens"] == 4000
    assert payload["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "生成短剧计划"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"},
                {"type": "input_image", "image_url": "https://example.test/seed.png"},
            ],
        }
    ]


def test_language_client_rejects_generation_without_output_text() -> None:
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(FakeResponse(200, {"status": "completed", "output": []})),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="no output text"):
        client.generate_text(instructions="instructions", input_text="input")


@pytest.mark.parametrize('status,code,error_type', [
    (400, 'context_length_exceeded', LanguageProviderContextWindowError),
    (400, 'context_window_exceeded', LanguageProviderContextWindowError),
    (429, 'rate_limit_exceeded', LanguageProviderTransientError),
    (503, 'service_unavailable', LanguageProviderTransientError),
    (401, 'invalid_api_key', LanguageProviderError),
])
def test_context_overflow_and_transient_http_failures_are_distinct(
    status: int, code: str, error_type: type[LanguageProviderError],
) -> None:
    session = FakeSession(FakeResponse(status, {'error': {'code': code, 'message': 'test failure'}}))
    client = OpenAICompatibleLanguageClient(api_key='', base_url='http://localhost:4141/v1',
        session=session)  # type: ignore[arg-type]
    with pytest.raises(error_type) as raised:
        client.generate_text(instructions='当前目标', input_text='必要内容')
    assert type(raised.value) is error_type
    assert len(session.post_calls) == 1


def test_nested_language_tool_cannot_send_text_above_the_258k_budget() -> None:
    session = FakeSession([])
    client = OpenAICompatibleLanguageClient(api_key='', base_url='http://localhost:4141/v1',
        session=session)  # type: ignore[arg-type]
    with pytest.raises(LanguageProviderContextWindowError, match='未发送超限请求'):
        client.generate_text(instructions='生成剧本', input_text='详细的角色场景和分镜。' * 60000,
                             max_output_tokens=20000)
    assert session.post_calls == []


class FakeLanguageProvider:
    def __init__(self, output_text: str = "") -> None:
        self.validations = 0
        self.output_text = output_text
        self.generations: list[tuple[str, str]] = []

    def validate_credentials(self) -> None:
        self.validations += 1

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        self.generations.append((instructions, input_text))
        return self.output_text


def test_language_config_api_saves_url_without_exposing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider()

    def factory(api_key: str, base_url: str) -> FakeLanguageProvider:
        assert api_key == "saved-secret"
        assert base_url == "https://sol.example.test/v1"
        return fake

    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=factory,
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        updated = client.post(
            "/api/short-drama/providers/gpt-sol",
            json={
                "api_key": "saved-secret",
                "base_url": "https://sol.example.test/v1/",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json() == {
            "provider": "openai-compatible",
            "model": "gpt-6-astra",
            "reasoning_effort": "xhigh",
            "base_url": "https://sol.example.test/v1",
            "has_api_key": True,
            "key_source": "LOCAL_CONFIG",
            "can_connect": True,
        }
        assert "saved-secret" not in updated.text

        validated = client.post("/api/short-drama/providers/gpt-sol/validate")
        assert validated.status_code == 200, validated.text
        assert fake.validations == 1

    config_path = tmp_path / "app-data" / "short-drama-language-provider-settings.json"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "api_key": "saved-secret",
        "base_url": "https://sol.example.test/v1",
    }


def test_language_validation_requires_a_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="https://sol.example.test/v1")
    )
    with pytest.raises(WorkflowConflictError, match="API key is not configured"):
        service.validate_language_provider_credentials()


def test_language_localhost_config_can_validate_without_a_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider()

    def factory(api_key: str, base_url: str) -> FakeLanguageProvider:
        assert api_key == ""
        assert base_url == "http://localhost:4141/v1"
        return fake

    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=factory,
    )
    response = service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    assert response.has_api_key is False
    assert response.can_connect is True

    validated = service.validate_language_provider_credentials()
    assert validated.can_connect is True
    assert fake.validations == 1


def test_language_api_compiles_h3_prompt_without_submitting_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider()

    def factory(api_key: str, base_url: str) -> FakeLanguageProvider:
        assert api_key == ""
        assert base_url == "http://localhost:4141/v1"
        return fake

    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=factory,
    )
    project = service.initialize_fixture()
    shot = next(item for item in project.document.shots if item.sequence == 2)
    compiled = build_h3_prompt(
        project.document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
    )
    fake.output_text = compiled
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            f"/api/short-drama/projects/{project.document.project.id}/h3/prompt/compile",
            json={
                "shot_id": "project-dont-trust-me-shot-02",
                "route": "REFERENCE",
                "duration_seconds": 8,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model"] == "gpt-6-astra"
    assert payload["route"] == "REFERENCE"
    assert payload["compiled_prompt"] == compiled
    assert payload["validation_notes"] == [
        "official MiniMax-H3 prompt field order preserved",
        "locked 9:16 ratio and 8-second duration preserved",
        "1 locked dialogue line(s) preserved verbatim",
        "Single-shot prompt only: no generation-unit candidate was created. "
        "route.plan_h3 authored prompt/media requires a generation-unit candidate. "
        "For complete current-group preparation use route.prepare_h3_group with enqueue=false; "
        "for a partial-group work order, hand this exact result to the group owner "
        "without changing the group or creating video tasks.",
    ]
    assert len(fake.generations) == 1
    instructions, input_text = fake.generations[0]
    assert "visible_assets 是本镜头可见资产的权威清单" in instructions
    assert '"route": "REFERENCE"' in input_text
    assert '"source_prompt"' not in input_text
    assert '"locked_shot"' in input_text
    assert service.list_h3_provider_runs(project.document.project.id).runs == []


def test_language_prompt_uses_profile_visible_assets_and_previous_terminal_state() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_2 = next(shot for shot in document.shots if shot.sequence == 2)
    instructions, input_text = build_language_prompt_input(
        document,
        shot_2,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
    )
    payload = json.loads(input_text)

    assert "当前制作类型为按项目和已选上游 Skill 声明的视觉媒介" in instructions
    assert "当前制片技能：AI 短剧（本地格式适配）。" in instructions
    assert "视频提示词编译的通用职责" in instructions
    assert "类型创作原则：" not in instructions
    assert payload["production_profile"] == "AI_SHORT_DRAMA"
    assert payload["visible_assets"] == ["林夏"]
    assert [
        character["name"]
        for character in payload["visible_asset_bible_for_disambiguation_only"]["characters"]
    ] == ["林夏"]
    assert payload["visible_asset_bible_for_disambiguation_only"]["locations"] == []
    assert payload["visible_asset_bible_for_disambiguation_only"]["props"] == []
    assert payload["previous_shot_planned_terminal_state"] == {
        "evidence_kind": "PLANNED_ONLY",
        "shot_id": document.shots[0].id,
        "sequence": 1,
        "visual": "雨夜便利店外，镜头靠近带雨痕的玻璃门",
        "framing": "大全景",
        "action_out": "门内两人形成对峙",
        "prop_state_out": "门内两人形成对峙",
        "character_positions": "林夏在镜头左侧轴线，周衡在右侧轴线",
        "eyeline": "双方视线保持同一对话轴",
        "transition_out": "硬切",
    }
    assert payload["actual_previous_media_boundary"] is None
    assert "dialogue_spans" in payload["production_rules"]["dialogue"]
    assert "speaker_id" in payload["production_rules"]["dialogue"]
    assert {key: value for key, value in payload["production_rules"].items() if key != "dialogue"} == {
        "route_selected_upstream": True,
        "action_density_policy": "按实际镜头上下文编译，不使用固定动作拍数阈值，也不擅自拆镜。",
        "timing_policy": "在不违背锁定事实时保留自然起止呼吸空间，不使用固定秒数作为硬规则。",
        "timing_markers": "在正文中按顺序使用每个 beat 的 required_timing_marker；可用数值完全等价的 MM:SS.mmm 起止区间，不能改变边界、漏段或把对白移到其他区间。",
        "continuity_handoff": "有实际连续性依赖时使用已观察的采用媒体边界；未提供观察时只保留计划意图，不携入当前镜头未列出的可见资产。",
    }

    document.project.production_profile = ProductionProfile.GENERAL_VIDEO
    general_instructions, general_input = build_language_prompt_input(
        document,
        shot_2,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
    )
    assert "当前制作类型为按项目声明的视觉媒介" in general_instructions
    assert "当前制片技能：通用视频。" in general_instructions
    assert "视频提示词编译的通用职责" in general_instructions
    assert "AI 原生短剧" not in general_instructions
    assert json.loads(general_input)["production_profile"] == "GENERAL_VIDEO"


def test_language_prompt_includes_structured_physical_interactions() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot = next(item for item in document.shots if item.sequence == 8)
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    linxia = next(asset for asset in document.assets if asset.name == "林夏定妆")
    store = next(asset for asset in document.assets if asset.name == "雨夜便利店")
    plan.action_beats = [
        ActionBeat(
            order=1,
            actor_asset_id=linxia.id,
            action="用右手打开指定门体",
            target_anchor="entrance.primary_door",
        )
    ]
    plan.interactions = [
        SpatialInteractionConstraint(
            id="exit-door",
            actor_asset_id=linxia.id,
            effector="RIGHT_HAND",
            action="打开",
            target_asset_id=store.id,
            target_anchor="entrance.primary_door",
            start_state="关闭",
            end_state="打开",
            contact_anchor="entrance.primary_door.handle.interior",
            contact_surface="entrance.primary_door.face.interior",
            contact_persists=True,
            rigid_attachments=[
                SurfaceAttachmentConstraint(
                    anchor="entrance.primary_door.handle.exterior",
                    surface="entrance.primary_door.face.exterior",
                )
            ],
        )
    ]

    instructions, input_text = build_language_prompt_input(
        document,
        shot,
        route=VideoGenerationRoute.FRAME_FIRST_LAST,
        duration_seconds=5,
        source_prompt="baseline",
    )
    payload = json.loads(input_text)

    assert "structured_action_plan 是导演 Agent" in instructions
    assert payload["structured_action_plan"]["action_beats"][0]["actor"] == "林夏定妆"
    interaction = payload["structured_action_plan"]["spatial_interactions"][0]
    assert interaction["contact_persists"] is True
    assert interaction["rigid_attachments"] == [
        {
            "anchor": "entrance.primary_door.handle.exterior",
            "surface": "entrance.primary_door.face.exterior",
        }
    ]


def test_language_prompt_uses_explicit_prop_binding_without_inferring_voiceover_character() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_5 = next(shot for shot in document.shots if shot.sequence == 5)
    _, input_text = build_language_prompt_input(
        document,
        shot_5,
        route=VideoGenerationRoute.FRAME_FIRST,
        duration_seconds=8,
        source_prompt="baseline",
    )
    payload = json.loads(input_text)
    bible = payload["visible_asset_bible_for_disambiguation_only"]

    assert payload["visible_assets"] == ["红色旧录音笔"]
    assert bible["characters"] == []
    assert bible["locations"] == []
    assert len(bible["props"]) == 1
    assert bible["props"][0]["name"] == "红色旧录音笔"
    assert all(state["shot_id"] == shot_5.id for state in bible["props"][0]["states"])
    assert payload["locked_dialogue"][0]["speaker"] == "录音笔中的林夏"


def test_language_prompt_does_not_infer_visible_assets_from_narrative_text() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_1 = next(shot for shot in document.shots if shot.sequence == 1)
    _, input_text = build_language_prompt_input(
        document,
        shot_1,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=5,
        source_prompt="baseline",
    )
    payload = json.loads(input_text)

    assert "雨夜便利店" in shot_1.visual
    assert "两人" in shot_1.continuity.action_out
    assert payload["visible_assets"] == []
    assert payload["visible_asset_bible_for_disambiguation_only"] == {
        "characters": [],
        "locations": [],
        "props": [],
        "style": document.bible_versions[0].style.model_dump(mode="json"),
    }


def test_language_prompt_resolves_bible_entities_only_from_typed_asset_links() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot = next(item for item in document.shots if item.sequence == 2)
    character_asset = next(
        item for item in document.assets if item.name == "林夏定妆"
    )
    character_asset.name = "周衡定妆"
    character_asset.bible_entity_id = "character-linxia"

    _, input_text = build_language_prompt_input(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
        media_inputs=[
            H3MediaInput(
                kind=H3MediaKind.IMAGE,
                role=H3MediaRole.REFERENCE_IMAGE,
                source=H3MediaSource(external_url="https://preview.invalid/character.png"),
                asset_version_id=character_asset.current_version_id,
            )
        ],
    )
    payload = json.loads(input_text)

    assert payload["visible_assets"] == ["林夏"]
    assert [
        item["name"]
        for item in payload["visible_asset_bible_for_disambiguation_only"]["characters"]
    ] == ["林夏"]

    character_asset.bible_entity_id = None
    _, unlinked_input = build_language_prompt_input(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
        media_inputs=[
            H3MediaInput(
                kind=H3MediaKind.IMAGE,
                role=H3MediaRole.REFERENCE_IMAGE,
                source=H3MediaSource(external_url="https://preview.invalid/character.png"),
                asset_version_id=character_asset.current_version_id,
            )
        ],
    )
    unlinked_payload = json.loads(unlinked_input)
    assert unlinked_payload["visible_assets"] == ["周衡定妆"]
    assert unlinked_payload["visible_asset_bible_for_disambiguation_only"][
        "characters"
    ] == []


def test_language_prompt_binds_route_media_descriptors() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_2 = next(shot for shot in document.shots if shot.sequence == 2)
    linxia = next(asset for asset in document.assets if asset.name == "林夏定妆")
    version = next(item for item in linxia.versions if item.id == linxia.current_version_id)
    _, input_text = build_language_prompt_input(
        document,
        shot_2,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
        media_inputs=[
            H3MediaInput(
                kind=H3MediaKind.IMAGE,
                role=H3MediaRole.REFERENCE_IMAGE,
                source=H3MediaSource(external_url="https://preview.invalid/linxia.png"),
                asset_version_id=version.id,
            )
        ],
    )
    payload = json.loads(input_text)

    assert payload["route_media_bindings"] == [
        {
            "role": "reference_image",
            "kind": "IMAGE",
            "provider_label": "<Picture 1>",
            "prompt_label": "<Subject 1>",
            "reference_alias": "@林夏定妆",
            "required_binding": "@林夏定妆 = <Subject 1>",
            "subject": "林夏定妆",
            "subject_asset_id": linxia.id,
            "reference_tags": [],
            "image_reference_role": None,
            "purpose": "identity, wardrobe, object appearance, or location geometry",
            "asset_version_id": version.id,
            "asset_name": "林夏定妆",
            "asset_kind": "CHARACTER",
            "version": version.version,
            "descriptor": version.descriptor,
            "review_notes": version.review_notes,
            "width": None,
            "height": None,
        }
    ]


def test_language_compiler_rejects_changed_locked_dialogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    shot = next(item for item in project.document.shots if item.sequence == 2)
    valid = build_h3_prompt(
        project.document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
    )
    fake.output_text = valid.replace(
        "你说他十点离开，可监控里，十点零三分他还在这里。",
        "你说他九点离开。",
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    with pytest.raises(ProviderOperationError, match="changed or omitted locked dialogue"):
        service.compile_h3_prompt_with_language_model(
            "project-dont-trust-me",
            LanguagePromptCompileRequest(
                shot_id="project-dont-trust-me-shot-02",
                route=VideoGenerationRoute.REFERENCE,
                duration_seconds=8,
            ),
        )


def test_language_compiler_accepts_official_voiceover_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    shot = next(item for item in project.document.shots if item.sequence == 5)
    fake.output_text = build_h3_prompt(
        project.document,
        shot,
        route=VideoGenerationRoute.FRAME_FIRST,
        duration_seconds=8,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    result = service.compile_h3_prompt_with_language_model(
        "project-dont-trust-me",
        LanguagePromptCompileRequest(
            shot_id="project-dont-trust-me-shot-05",
            route=VideoGenerationRoute.FRAME_FIRST,
            duration_seconds=8,
        ),
    )

    assert result.compiled_prompt == fake.output_text
    assert result.validation_notes[-2:] == [
        "1 locked dialogue line(s) preserved verbatim",
        "Single-shot prompt only: no generation-unit candidate was created. "
        "route.plan_h3 authored prompt/media requires a generation-unit candidate. "
        "For complete current-group preparation use route.prepare_h3_group with enqueue=false; "
        "for a partial-group work order, hand this exact result to the group owner "
        "without changing the group or creating video tasks.",
    ]
    assert "says in an off-screen voiceover" in result.compiled_prompt
    assert "lips remain completely closed" in result.compiled_prompt
