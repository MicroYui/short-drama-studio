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
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
    OpenAICompatibleLanguageClient,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


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


class FakeLanguageProvider:
    def __init__(self) -> None:
        self.validations = 0

    def validate_credentials(self) -> None:
        self.validations += 1


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
