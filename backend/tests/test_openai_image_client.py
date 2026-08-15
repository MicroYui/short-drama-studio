"""Offline HTTP contract coverage for the GPT Image 2 adapter."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
import requests

from short_drama.image_models import ImageOperation, ImageProviderRequest
from short_drama.providers.image_provider import (
    ImageProviderDefiniteError,
    ImageProviderInput,
    ImageProviderSubmissionUnknown,
)
from short_drama.providers.openai_image_client import OpenAIImageClient


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.responses: list[FakeResponse | Exception] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self) -> FakeResponse:
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._next()


def _client(session: FakeSession) -> OpenAIImageClient:
    return OpenAIImageClient(
        api_key="secret",
        base_url="https://image.example.test/v1/",
        session=session,  # type: ignore[arg-type]
        timeout_seconds=3,
    )


def _success_payload() -> dict[str, object]:
    return {
        "created": 1,
        "data": [
            {
                "b64_json": base64.b64encode(b"image-bytes").decode("ascii"),
                "revised_prompt": "revised",
            }
        ],
        "usage": {
            "input_tokens": 30,
            "input_tokens_details": {"image_tokens": 20, "text_tokens": 10},
            "output_tokens": 100,
            "output_tokens_details": {"image_tokens": 100, "text_tokens": 0},
            "total_tokens": 130,
        },
    }


def test_generation_uses_current_gpt_image_json_contract() -> None:
    session = FakeSession()
    session.responses.append(
        FakeResponse(200, _success_payload(), headers={"x-request-id": "req-generation"})
    )
    request = ImageProviderRequest(
        operation=ImageOperation.GENERATE,
        prompt="真人都市悬疑角色定妆",
        size="768x1344",
    )

    result = _client(session).create(request, [])

    call = session.calls[0]
    assert call[0:2] == ("POST", "https://image.example.test/v1/images/generations")
    assert call[2]["headers"] == {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
    }
    assert call[2]["json"] == {
        "model": "gpt-image-2",
        "prompt": "真人都市悬疑角色定妆",
        "n": 1,
        "size": "768x1344",
        "quality": "high",
        "background": "opaque",
        "output_format": "png",
    }
    assert result.provider_request_id == "req-generation"
    assert result.usage.output_tokens_details.image_tokens == 100


def test_edit_uses_high_fidelity_multi_image_inputs() -> None:
    session = FakeSession()
    session.responses.append(FakeResponse(200, _success_payload()))
    request = ImageProviderRequest(
        operation=ImageOperation.EDIT,
        prompt="保留人物身份并放入便利店",
        input_version_ids=["version-1", "version-2"],
    )
    inputs = [
        ImageProviderInput(asset_version_id="version-1", image_url="data:image/png;base64,AA=="),
        ImageProviderInput(asset_version_id="version-2", image_url="https://example.com/store.png"),
    ]

    _client(session).create(request, inputs)

    payload = session.calls[0][2]["json"]
    assert payload["images"] == [
        {"image_url": "data:image/png;base64,AA=="},
        {"image_url": "https://example.com/store.png"},
    ]
    assert payload["input_fidelity"] == "high"
    assert session.calls[0][1] == "https://image.example.test/v1/images/edits"


def test_validation_and_error_classification() -> None:
    session = FakeSession()
    session.responses.append(FakeResponse(200, {"id": "gpt-image-2"}))
    client = _client(session)
    client.validate_credentials()
    assert session.calls[0][0:2] == (
        "GET",
        "https://image.example.test/v1/models/gpt-image-2",
    )

    session.responses.append(
        FakeResponse(400, {"error": {"code": "invalid_image", "message": "bad image"}})
    )
    with pytest.raises(ImageProviderDefiniteError) as definite:
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])
    assert definite.value.code == "invalid_image"

    session.responses.append(FakeResponse(500, {"error": {"message": "server error"}}))
    with pytest.raises(ImageProviderSubmissionUnknown):
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])

    session.responses.append(requests.ConnectionError("offline"))
    with pytest.raises(ImageProviderSubmissionUnknown):
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])
