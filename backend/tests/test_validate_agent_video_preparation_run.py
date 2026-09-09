"""The real-run recorder must observe calls without changing their payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from requests import Response, Session

from validate_agent_video_preparation_run import RecordingLanguageClient, TraceRecorder


class FakeStream:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def close(self) -> None:
        pass

    def iter_lines(self, **_kwargs: Any):
        for event in [
            {"type": "response.output_text.delta", "delta": "A complete answer."},
            {"type": "response.completed", "response": {
                "id": "response-test", "model": "gpt-5.6-sol", "status": "completed",
                "usage": {"input_tokens": 87, "output_tokens": 12, "total_tokens": 99},
            }},
        ]:
            yield "data: " + json.dumps(event)
            yield ""
        yield "data: [DONE]"
        yield ""


class FakeSession:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Response:
        self.requests.append({"url": url, **kwargs})
        return cast(Response, FakeStream())


def test_recording_preserves_wire_payload_and_captures_provider_usage(tmp_path: Path) -> None:
    session = FakeSession()
    client = RecordingLanguageClient(
        TraceRecorder(tmp_path), api_key="example-secret", base_url="https://example.test/v1",
        session=cast(Session, session),
    )
    image = "data:image/png;base64,private-image-bytes"
    result = client.generate_text(
        instructions="Original instructions", input_text='{"work_order":{"id":"work-1"}}',
        image_urls=[image], max_output_tokens=3210,
    )
    assert result == "A complete answer."
    body = session.requests[0]["json"]
    assert body["max_output_tokens"] == 3210
    assert body["input"][0]["content"][1]["image_url"] == image
    call = tmp_path / "model_calls" / "00001"
    request_text = (call / "request.json").read_text()
    assert "example-secret" not in request_text
    assert "private-image-bytes" not in request_text
    assert json.loads(request_text)["work_order_id"] == "work-1"
    assert json.loads((call / "usage.json").read_text())["usage"]["total_tokens"] == 99
    assert (call / "response.txt").read_text() == result
    assert "response.completed" in (call / "provider-stream.txt").read_text()


def test_media_boundary_records_attempt_without_provider_io(tmp_path: Path) -> None:
    recorder = TraceRecorder(tmp_path)
    with pytest.raises(RuntimeError, match="stops before H3"):
        recorder.block_media("H3")
    event = json.loads((tmp_path / "events.jsonl").read_text())
    assert event["event"] == "media_boundary_attempt"
    assert event["network_called"] is False
