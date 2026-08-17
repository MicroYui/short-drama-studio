"""Offline configuration and HTTP coverage for the GPT-5.6-sol connection."""

from __future__ import annotations

import json
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
from short_drama.models import VideoGenerationRoute
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
    OpenAICompatibleLanguageClient,
)
from short_drama.providers.h3_language_prompt import build_language_prompt_input
from short_drama.service import (
    ProviderOperationError,
    ShortDramaService,
    WorkflowConflictError,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self.payload


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
    session = FakeSession(FakeResponse(200, {"id": "gpt-5.6-sol"}))
    client = OpenAICompatibleLanguageClient(
        api_key="secret-sol",
        base_url="https://sol.example.test/openai/v1/",
        session=session,  # type: ignore[arg-type]
    )

    client.validate_credentials()

    assert session.calls == [
        (
            "https://sol.example.test/openai/v1/models/gpt-5.6-sol",
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
    with pytest.raises(LanguageProviderError, match="expected 'gpt-5.6-sol'"):
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
                        {"id": "gpt-5.6-sol", "supported_endpoints": ["/responses"]},
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
        "http://localhost:4141/v1/models/gpt-5.6-sol",
        "http://localhost:4141/v1/models",
    ]
    assert all(call[1]["headers"] == {} for call in session.calls)


def test_language_client_rejects_model_list_without_sol() -> None:
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
    with pytest.raises(LanguageProviderError, match="does not include 'gpt-5.6-sol'"):
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
                    "model": "gpt-5.6-sol",
                    "instructions": "只输出提示词",
                    "input": "锁定事实",
                    "reasoning": {"effort": "low"},
                    "max_output_tokens": 2200,
                },
                "timeout": 180.0,
            },
        )
    ]


def test_language_client_rejects_generation_without_output_text() -> None:
    client = OpenAICompatibleLanguageClient(
        api_key="",
        base_url="http://localhost:4141/v1",
        session=FakeSession(FakeResponse(200, {"status": "completed", "output": []})),  # type: ignore[arg-type]
    )
    with pytest.raises(LanguageProviderError, match="no output text"):
        client.generate_text(instructions="instructions", input_text="input")


class FakeLanguageProvider:
    def __init__(self, output_text: str = "") -> None:
        self.validations = 0
        self.output_text = output_text
        self.generations: list[tuple[str, str]] = []

    def validate_credentials(self) -> None:
        self.validations += 1

    def generate_text(self, *, instructions: str, input_text: str) -> str:
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
            "model": "gpt-5.6-sol",
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
    compiled = (
        "真人都市悬疑短剧，电影级写实，9:16竖屏，精确8秒，单一连续镜头。"
        "林夏近景，短黑发、米白色风衣，视线越过镜头，稳定机位并在末段轻推。"
        "林夏用普通话逐字说：“你说他十点离开，可监控里，十点零三分他还在这里。”"
        "周衡只在画外作为视线目标，不入画；无其他人物或道具。禁止字幕、水印和画面内可读文字。"
    )
    fake = FakeLanguageProvider(compiled)

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
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["route"] == "REFERENCE"
    assert payload["compiled_prompt"] == compiled
    assert payload["validation_notes"] == [
        "non-empty prompt within MiniMax-H3 7000-character limit",
        "locked 9:16 ratio and 8-second duration preserved",
        "1 locked dialogue line(s) preserved verbatim",
    ]
    assert len(fake.generations) == 1
    instructions, input_text = fake.generations[0]
    assert "visible_assets 是本镜头可见资产的权威清单" in instructions
    assert '"route": "REFERENCE"' in input_text
    assert '"source_prompt"' in input_text
    assert service.list_h3_provider_runs(project.document.project.id).runs == []


def test_language_prompt_uses_visible_assets_and_previous_terminal_state() -> None:
    from short_drama.fixture import build_dont_trust_me_fixture

    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_2 = next(shot for shot in document.shots if shot.sequence == 2)
    _, input_text = build_language_prompt_input(
        document,
        shot_2,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        source_prompt="baseline",
    )
    payload = json.loads(input_text)

    assert payload["visible_assets"] == ["林夏", "雨夜便利店"]
    assert [
        character["name"]
        for character in payload["visible_asset_bible_for_disambiguation_only"]["characters"]
    ] == ["林夏"]
    assert payload["visible_asset_bible_for_disambiguation_only"]["props"] == []
    assert payload["previous_shot_terminal_state"] == {
        "sequence": 1,
        "visual": "雨夜便利店外，镜头靠近带雨痕的玻璃门",
        "framing": "大全景",
        "action_out": "门内两人形成对峙",
        "prop_state_out": "门内两人形成对峙",
        "character_positions": "林夏在镜头左侧轴线，周衡在右侧轴线",
        "eyeline": "双方视线保持同一对话轴",
        "transition_out": "硬切",
    }
    assert payload["production_rules"] == {
        "route_selected_upstream": True,
        "action_density_policy": "按实际镜头上下文编译，不使用固定动作拍数阈值，也不擅自拆镜。",
        "timing_policy": "在不违背锁定事实时保留自然起止呼吸空间，不使用固定秒数作为硬规则。",
        "continuity_handoff": "从上一镜终态自然承接，但不得携入当前镜头未列出的可见资产。",
    }


def test_language_prompt_filters_voiceover_character_but_keeps_current_prop_state() -> None:
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

    assert payload["visible_assets"] == ["周衡", "雨夜便利店", "红色旧录音笔"]
    assert [character["name"] for character in bible["characters"]] == ["周衡"]
    assert len(bible["props"]) == 1
    assert bible["props"][0]["name"] == "红色旧录音笔"
    assert all(state["shot_id"] == shot_5.id for state in bible["props"][0]["states"])
    assert payload["locked_dialogue"][0]["speaker"] == "录音笔中的林夏"


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
            "purpose": "只锚定描述中明确对应的视觉元素",
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
    fake = FakeLanguageProvider(
        "真人都市悬疑，9:16，8秒。林夏用普通话说：“你说他九点离开。”"
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    service.initialize_fixture()
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


def test_language_compiler_accepts_voiceover_source_and_speaker_paraphrase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("GPT_SOL_API_KEY", "OPENAI_API_KEY", "GPT_SOL_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    fake = FakeLanguageProvider(
        "真人都市悬疑，9:16，精确8秒。录音笔扬声器传出林夏的普通话录音："
        "“如果你听到这段话，别相信我。”人物全程闭嘴。"
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    service.initialize_fixture()
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
    assert result.validation_notes[-1] == "1 locked dialogue line(s) preserved verbatim"
