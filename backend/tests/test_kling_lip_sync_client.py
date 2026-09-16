"""The new edit adapter must not turn an uncertain POST into duplicate spending."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from short_drama.providers.kling_lip_sync_client import (
    AceDataKlingLipSyncClient, KlingRemoteTask, KlingRequestRejected,
    KlingSubmissionUnknown, KlingTransientError,
)


def test_task_preserves_actual_provider_billing_without_inventing_usage() -> None:
    reported = {"id": "known-task", "cost": 2.45, "unit": "credits"}
    assert KlingRemoteTask.model_validate(reported).model_dump()["cost"] == 2.45
    assert KlingRemoteTask.model_validate(reported).model_dump()["unit"] == "credits"
    assert "cost" not in KlingRemoteTask(id="without-usage").model_dump()


class FakeResponse:
    def __init__(self, status: int, payload: object, body: bytes = b"edited-video",
                 stream_error: bool = False) -> None:
        self.status_code = status
        self.payload = payload
        self.text = json.dumps(payload)
        self.body = body
        self.closed = False
        self.stream_error = stream_error

    def json(self) -> object:
        if self.payload == "invalid-json":
            raise ValueError("invalid JSON")
        return self.payload

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body
        if self.stream_error:
            raise requests.ConnectionError("download interrupted")


class FakeSession:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self, method: str, url: str, values: dict[str, Any]) -> FakeResponse:
        self.calls.append((method, url, values))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        if "files" in kwargs:
            name, stream, mime = kwargs["files"]["file"]
            kwargs["files"] = {"file": (name, stream.read(), mime)}
        return self._next("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("GET", url, kwargs)


def client(session: FakeSession) -> AceDataKlingLipSyncClient:
    return AceDataKlingLipSyncClient(api_key="secret", session=session, timeout_seconds=3)  # type: ignore[arg-type]


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "speech.wav"
    path.write_bytes(b"existing-speech")
    return path


def test_upload_submit_query_download_use_separate_hosts_and_existing_audio(tmp_path: Path, audio: Path) -> None:
    responses = [FakeResponse(200, {"file_url": "https://cdn.acedata.cloud/source.mp4"}),
        FakeResponse(200, {"task_id": "task-1"}),
        FakeResponse(200, {"id": "task-1", "type": "lip-sync", "request": {"mode": "audio2video"},
            "response": {"success": True, "state": "succeed", "video_url": "https://cdn.acedata.cloud/result.mp4",
                         "cost": {"amount": 2.45, "currency": "credit"}}}),
        FakeResponse(200, {})]
    session = FakeSession(*responses)
    adapter = client(session)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original-video")
    url = adapter.upload_video(source)
    submitted = adapter.submit(video_url=url, audio_path=audio)
    remote = adapter.query(submitted.task_id)
    assert remote.status == "succeeded" and remote.video_url is not None
    result = tmp_path / "results" / "edit.mp4"
    adapter.download(remote.video_url, result)
    assert result.read_bytes() == b"edited-video" and source.read_bytes() == b"original-video"
    assert not result.with_suffix(".mp4.part").exists()
    assert [(m, u) for m, u, _ in session.calls] == [
        ("POST", "https://platform.acedata.cloud/api/v1/files/"),
        ("POST", "https://api.acedata.cloud/kling/lip-sync"),
        ("POST", "https://api.acedata.cloud/kling/tasks"),
        ("GET", "https://cdn.acedata.cloud/result.mp4")]
    assert session.calls[0][2]["files"] == {"file": ("source.mp4", b"original-video", "video/mp4")}
    assert session.calls[1][2]["json"] == {"mode": "audio2video", "video_url": url,
        "audio_type": "file", "audio_file": base64.b64encode(audio.read_bytes()).decode("ascii"), "async": True}
    assert session.calls[2][2]["json"] == {"action": "retrieve", "id": "task-1"}
    assert "headers" not in session.calls[3][2]
    assert all(r.closed for r in responses)


@pytest.mark.parametrize("status", [400, 403, 500])
def test_error_with_task_id_still_requires_query_before_new_submit(status: int, audio: Path) -> None:
    response = FakeResponse(status, {"success": False, "task_id": "known-task", "error": {"code": "api_error"}})
    session = FakeSession(response)
    result = client(session).submit(video_url="https://cdn.invalid/source.mp4", audio_path=audio)
    assert result.task_id == "known-task" and result.http_status == status
    assert result.response["success"] is False
    assert len(session.calls) == 1 and response.closed


@pytest.mark.parametrize("response", [requests.Timeout("timeout"), FakeResponse(500, {"error": {"message": "secret"}}),
    FakeResponse(408, {}), FakeResponse(200, {}), FakeResponse(200, "invalid-json")])
def test_uncertain_submit_never_retries_or_exposes_credentials(response: FakeResponse | Exception, audio: Path) -> None:
    session = FakeSession(response)
    with pytest.raises(KlingSubmissionUnknown) as result:
        client(session).submit(video_url="https://cdn.invalid/source.mp4", audio_path=audio)
    assert len(session.calls) == 1 and "secret" not in str(result.value)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422, 429])
def test_explicit_rejection_without_task_id_is_distinct(status: int, audio: Path) -> None:
    session = FakeSession(FakeResponse(status, {"error": {"message": "bad source"}, "trace_id": "trace-1"}))
    with pytest.raises(KlingRequestRejected) as result:
        client(session).submit(video_url="https://cdn.invalid/source.mp4", audio_path=audio)
    assert result.value.status_code == status and "trace-1" in str(result.value)
    assert len(session.calls) == 1


def test_local_input_rejection_happens_before_upload_or_submit(tmp_path: Path, audio: Path) -> None:
    session = FakeSession()
    adapter = client(session)
    with pytest.raises(KlingRequestRejected):
        adapter.upload_video(tmp_path / "missing.mp4")
    with pytest.raises(KlingRequestRejected):
        adapter.submit(video_url="data:video/mp4;base64,AA", audio_path=audio)
    audio.write_bytes(b"")
    with pytest.raises(KlingRequestRejected):
        adapter.submit(video_url="https://cdn.invalid/source.mp4", audio_path=audio)
    assert session.calls == []


@pytest.mark.parametrize("payload", [{}, {"id": "another-task"}, "invalid-json"])
def test_query_bad_response_does_not_lose_task_identity(payload: object) -> None:
    session = FakeSession(FakeResponse(200, payload))
    with pytest.raises(KlingTransientError):
        client(session).query("task-1")
    assert len(session.calls) == 1


def test_unfinished_task_is_not_failure_and_unknown_finished_result_is_not_success() -> None:
    assert KlingRemoteTask(id="one", started_at=100).status == "running"
    assert KlingRemoteTask(id="one", finished_at=102, response={}).status == "running"
    assert KlingRemoteTask(id="one", response={"success": False, "error": {"code": "failed"}}).status == "failed"


@pytest.mark.parametrize("response", [FakeResponse(200, {}, body=b"", stream_error=False),
    FakeResponse(200, {}, stream_error=True), FakeResponse(503, {})])
def test_failed_download_preserves_previous_file_and_removes_partial(response: FakeResponse, tmp_path: Path) -> None:
    session = FakeSession(response)
    destination = tmp_path / "original.mp4"
    destination.write_bytes(b"existing-output")
    with pytest.raises(KlingTransientError):
        client(session).download("https://cdn.invalid/result.mp4", destination)
    assert destination.read_bytes() == b"existing-output"
    assert not destination.with_suffix(".mp4.part").exists()
    assert response.closed and len(session.calls) == 1


def test_upload_denial_does_not_fall_back_to_another_host(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    session = FakeSession(FakeResponse(401, {"error": {"message": "invalid_token secret"}}))
    with pytest.raises(KlingRequestRejected) as result:
        client(session).upload_video(source)
    assert "secret" not in str(result.value) and len(session.calls) == 1


def test_separate_upload_token_is_used_only_for_file_upload(tmp_path: Path, audio: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    session = FakeSession(FakeResponse(201, {"file_url": "https://cdn.invalid/source.mp4"}),
        FakeResponse(200, {"task_id": "one"}), FakeResponse(200, {"id": "one"}))
    adapter = AceDataKlingLipSyncClient(api_key="generation-key", upload_token="upload-only", session=session)  # type: ignore[arg-type]
    url = adapter.upload_video(source)
    assert adapter.upload_http_status == 201
    adapter.submit(video_url=url, audio_path=audio)
    adapter.query("one")
    assert [values["headers"]["Authorization"] for _, _, values in session.calls] == [
        "Bearer upload-only", "Bearer generation-key", "Bearer generation-key"]


@pytest.mark.parametrize("status", [401, 429, 503])
def test_upload_error_preserves_whitelisted_detail_and_redacts_both_credentials(tmp_path: Path, status: int) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    payload = {"detail": "Invalid authentication credentials generation-key", "message": "upload permission denied upload-only",
        "code": "upload_auth", "error": {"detail": "Bearer abc.def.ghi", "code": "bad_token", "headers": "must not retain"},
        "authorization": "must not retain", "request": {"api_key": "must not retain"}}
    session = FakeSession(FakeResponse(status, payload))
    adapter = AceDataKlingLipSyncClient(api_key="generation-key", upload_token="upload-only", session=session)  # type: ignore[arg-type]
    with pytest.raises((KlingRequestRejected, KlingTransientError)) as result:
        adapter.upload_video(source)
    assert result.value.status_code == status and adapter.upload_http_status == status
    assert "Invalid authentication credentials" in str(result.value) and "upload_auth" in str(result.value)
    assert set(result.value.details) == {"detail", "message", "code", "error"}
    assert result.value.details["error"] == {"detail": "Bearer [credential omitted]", "code": "bad_token"}
    assert all(secret not in str(result.value) + json.dumps(result.value.details) for secret in (
        "generation-key", "upload-only", "abc.def.ghi", "must not retain"))
    assert len(session.calls) == 1
