"""Observe actual transport serialization without changing submission behavior."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
from requests import PreparedRequest, Response
from requests.adapters import BaseAdapter

from short_drama.h3_models import H3ContentItem, H3UrlValue, H3VideoRequest
from short_drama.providers.minimax_h3_client import H3SubmissionUnknown, MiniMaxH3Client
from validate_agent_video_generation_run import RecordingH3Session, VideoTraceRecorder


class FakeAdapter(BaseAdapter):
    def __init__(self, responses: list[tuple[int, dict[str, object]] | requests.RequestException]) -> None:
        super().__init__()
        self.responses = responses
        self.requests: list[PreparedRequest] = []

    def send(self, request: PreparedRequest, **kwargs: Any) -> Response:
        del kwargs
        self.requests.append(request)
        next_response = self.responses.pop(0)
        if isinstance(next_response, requests.RequestException):
            raise next_response
        status, payload = next_response
        response = Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        response.request = request
        response.url = request.url or ""
        return response

    def close(self) -> None:
        pass


def test_recording_keeps_exact_wire_body_off_model_and_omits_credentials(tmp_path: Path) -> None:
    session = RecordingH3Session(VideoTraceRecorder(tmp_path))
    adapter = FakeAdapter([(200, {"task_id": "remote-1"})])
    session.mount("https://", adapter)
    request = H3VideoRequest(duration=13, ratio="9:16", content=[
        H3ContentItem(type="text", text="原始提示词"),
        H3ContentItem(type="image_url", image_url=H3UrlValue(url="data:image/png;base64,IMAGE_BODY")),
    ])
    client = MiniMaxH3Client(api_key="SECRET_HEADER", session=session)
    assert client.submit(request) == "remote-1"
    actual = adapter.requests[0]
    call = tmp_path / "provider_calls/00001"
    assert (call / "request-body.json").read_bytes() == actual.body
    assert json.loads((call / "request-body.json").read_text())["async"] is True
    assert actual.headers["Authorization"] == "Bearer SECRET_HEADER"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"SECRET_HEADER" not in path.read_bytes()
    assert "IMAGE_BODY" not in (tmp_path / "events.jsonl").read_text()
    assert json.loads((call / "result.json").read_text())["status_code"] == 200


def test_submission_transport_error_is_recorded_without_hidden_resubmit(tmp_path: Path) -> None:
    session = RecordingH3Session(VideoTraceRecorder(tmp_path))
    adapter = FakeAdapter([requests.ConnectionError("connection dropped")])
    session.mount("https://", adapter)
    client = MiniMaxH3Client(api_key="SECRET_HEADER", session=session)
    with pytest.raises(H3SubmissionUnknown):
        client.submit(H3VideoRequest(duration=5, content=[H3ContentItem(type="text", text="original")]))
    assert len(adapter.requests) == 1
    result = json.loads((tmp_path / "provider_calls/00001/result.json").read_text())
    assert result["error_kind"] == "ConnectionError"
    assert result["success"] is False
