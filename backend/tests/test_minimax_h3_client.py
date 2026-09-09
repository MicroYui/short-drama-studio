"""HTTP contract tests for MiniMax-H3 through AceData without network access."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import pytest
import requests
from PIL import Image

from short_drama.h3_models import H3ContentItem, H3VideoRequest
from short_drama.providers.minimax_h3_client import (
    H3DefiniteRequestError,
    H3SubmissionUnknown,
    H3TransientError,
    MiniMaxH3Client,
)


FIXTURES = Path(__file__).parent / "fixtures" / "minimax_h3"


class FakeResponse:
    def __init__(self, status_code: int, payload: object, body: bytes = b"video") -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)
        self.body = body
        self.closed = False

    def json(self) -> object:
        return self.payload

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.responses: list[FakeResponse | Exception] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self) -> FakeResponse:
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._next()

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._next()

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._next()


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request() -> H3VideoRequest:
    return H3VideoRequest(
        content=[H3ContentItem(type="text", text="真人都市短剧")],
        duration=5,
        ratio="9:16",
    )


def _client(session: FakeSession) -> MiniMaxH3Client:
    return MiniMaxH3Client(
        api_key="secret",
        session=session,  # type: ignore[arg-type]
        timeout_seconds=3,
    )


def test_client_uses_acedata_endpoints_and_exact_payload(tmp_path: Path) -> None:
    session = FakeSession()
    session.responses.extend(
        [
            FakeResponse(200, _fixture("create_success.json")),
            FakeResponse(200, _fixture("query_succeeded.json")),
            FakeResponse(200, _fixture("list_tasks.json")),
            FakeResponse(200, {"id": "424010985738629", "deleted": True}),
            FakeResponse(200, {}, body=b"downloaded-video"),
        ]
    )
    client = _client(session)

    task_id = client.submit(_request())
    assert task_id == "424010985738629"
    submit_call = session.calls[0]
    assert submit_call[0:2] == ("POST", "https://api.acedata.cloud/minimax/videos")
    assert submit_call[2]["headers"]["Authorization"] == "Bearer secret"
    assert submit_call[2]["json"] == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "真人都市短剧"}],
        "resolution": "768P",
        "duration": 5,
        "ratio": "9:16",
        "async": True,
    }

    assert client.query(task_id).status == "succeeded"
    assert client.list_tasks(page_size=500)[0].id == task_id
    assert client.cancel_or_delete(task_id).action == "cancelled"
    assert session.calls[1][0:2] == ("POST", "https://api.acedata.cloud/minimax/tasks")
    assert session.calls[1][2]["json"] == {"action": "retrieve", "id": task_id}
    assert session.calls[2][2]["json"] == {
        "action": "retrieve_batch",
        "offset": 0,
        "limit": 100,
    }
    assert session.calls[3][2]["json"] == {"action": "delete", "id": task_id}

    source = tmp_path / "reference.png"
    Image.new("RGB", (16, 16), "#ababab").save(source)
    assert client.upload_file(source) == "data:image/png;base64," + base64.b64encode(source.read_bytes()).decode("ascii")
    assert len(session.calls) == 4

    destination = tmp_path / "output" / "take.mp4"
    client.download("https://signed.invalid/output.mp4", destination)
    assert destination.read_bytes() == b"downloaded-video"
    assert not destination.with_suffix(".mp4.part").exists()


@pytest.mark.parametrize("size,expected", [((344, 976), (391, 976)), ((1536, 512), (1536, 615))])
def test_local_reference_padding_preserves_every_source_pixel(
    tmp_path: Path, size: tuple[int, int], expected: tuple[int, int],
) -> None:
    source = tmp_path / "reference.png"
    original = Image.new("RGB", size, "#232b32")
    original.putpixel((0, 0), (10, 20, 30))
    original.putpixel((size[0] - 1, size[1] - 1), (230, 240, 250))
    original.save(source)
    source_bytes = source.read_bytes()
    session = FakeSession()
    client = MiniMaxH3Client(api_key="secret", session=session)  # type: ignore[arg-type]
    value = client.upload_file(source)
    with Image.open(BytesIO(base64.b64decode(value.split(",", 1)[1]))) as result:
        assert result.size == expected
        assert 0.4 <= result.width / result.height <= 2.5
        left, top = (result.width - size[0]) // 2, (result.height - size[1]) // 2
        assert result.crop((left, top, left + size[0], top + size[1])).tobytes() == original.tobytes()
    assert source.read_bytes() == source_bytes
    assert session.calls == []


def test_local_image_decode_failure_is_reported_before_network(tmp_path: Path) -> None:
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    session = FakeSession()
    client = MiniMaxH3Client(api_key="secret", session=session)  # type: ignore[arg-type]
    with pytest.raises(H3DefiniteRequestError) as exc_info:
        client.upload_file(source)
    assert exc_info.value.code == "LOCAL_IMAGE_INVALID"
    assert session.calls == []


def test_submit_unknown_and_definite_errors_are_distinct() -> None:
    session = FakeSession()
    client = _client(session)

    session.responses.append(requests.ConnectionError("offline"))
    with pytest.raises(H3SubmissionUnknown):
        client.submit(_request())

    session.responses.append(FakeResponse(500, {"error": {"message": "server error"}}))
    with pytest.raises(H3SubmissionUnknown):
        client.submit(_request())

    session.responses.append(
        FakeResponse(
            400,
            {"error": {"code": "invalid_request", "message": "bad content"}},
        )
    )
    with pytest.raises(H3DefiniteRequestError) as definite:
        client.submit(_request())
    assert definite.value.code == "invalid_request"

    session.responses.append(FakeResponse(429, {"error": {"message": "rate limited"}}))
    with pytest.raises(H3TransientError):
        client.query("task")


def test_submit_falls_back_to_synchronous_when_live_gateway_rejects_async() -> None:
    session = FakeSession()
    session.responses.extend(
        [
            FakeResponse(
                400,
                {
                    "error": {
                        "code": "bad_request_error",
                        "message": "async is not supported by the MiniMax H3 V2 contract",
                    }
                },
            ),
            FakeResponse(200, _fixture("query_succeeded.json")),
        ]
    )
    client = _client(session)

    assert client.submit(_request()) == "424010985738629"
    assert len(session.calls) == 2
    assert session.calls[0][2]["json"]["async"] is True
    assert "async" not in session.calls[1][2]["json"]
    assert session.calls[1][2]["timeout"] == 900.0
