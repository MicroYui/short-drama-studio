"""HTTP client for the current MiniMax-H3 V2 API contract."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, cast

import requests
from pydantic import ValidationError
from requests import Response, Session

from short_drama.h3_models import (
    H3CreateResponse,
    H3DeleteResponse,
    H3ListResponse,
    H3QueryResponse,
    H3RemoteTask,
    H3UploadResponse,
    H3VideoRequest,
)


class H3ClientError(RuntimeError):
    """Base error for MiniMax request and response failures."""


class H3DefiniteRequestError(H3ClientError):
    """The provider definitely rejected the request and did not create a task."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class H3SubmissionUnknown(H3ClientError):
    """The submit outcome cannot be known safely after a transport/server failure."""


class H3TransientError(H3ClientError):
    """A query, list, cancel, upload, or download operation can be retried."""


class H3Client(Protocol):
    def submit(self, request: H3VideoRequest) -> str: ...

    def query(self, task_id: str) -> H3RemoteTask: ...

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]: ...

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse: ...

    def upload_file(self, path: Path) -> str: ...

    def download(self, url: str, destination: Path) -> None: ...


class MiniMaxH3Client:
    """Thin synchronous client; orchestration and persistence live above it."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimax.io",
        session: Session | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("MiniMax API key is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def submit(self, request: H3VideoRequest) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/v2/video_generation",
                headers=self._headers(),
                json=request.model_dump(mode="json", exclude_none=True),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise H3SubmissionUnknown("MiniMax submission transport outcome is unknown") from exc
        if response.status_code >= 500:
            raise H3SubmissionUnknown(
                f"MiniMax submission returned {response.status_code}; outcome is unknown"
            )
        self._raise_definite_error(response)
        try:
            return H3CreateResponse.model_validate(response.json()).task_id
        except (ValueError, ValidationError) as exc:
            raise H3SubmissionUnknown("MiniMax submit response did not contain a valid task_id") from exc

    def query(self, task_id: str) -> H3RemoteTask:
        response = self._request(
            "GET",
            f"/v2/query/video_generation/{task_id}",
        )
        try:
            return H3QueryResponse.model_validate(response.json()).task
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("MiniMax query response was not valid") from exc

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]:
        response = self._request(
            "GET",
            "/v2/query/video_generation",
            params={"page_num": 1, "page_size": max(1, min(page_size, 100))},
        )
        try:
            return H3ListResponse.model_validate(response.json()).items
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("MiniMax task-list response was not valid") from exc

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse:
        response = self._request("DELETE", f"/v2/video_generation/{task_id}")
        try:
            return H3DeleteResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("MiniMax cancel response was not valid") from exc

    def upload_file(self, path: Path) -> str:
        if not path.is_file():
            raise H3DefiniteRequestError(400, "LOCAL_FILE_MISSING", str(path))
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as handle:
                response = self.session.post(
                    f"{self.base_url}/v1/files/upload",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"purpose": "video_generation_input"},
                    files={"file": (path.name, handle, mime_type)},
                    timeout=max(self.timeout_seconds, 180.0),
                )
        except requests.RequestException as exc:
            raise H3TransientError("MiniMax input upload failed") from exc
        self._raise_definite_error(response)
        try:
            parsed = H3UploadResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("MiniMax upload response was not valid") from exc
        if parsed.base_resp is not None and parsed.base_resp.status_code != 0:
            raise H3DefiniteRequestError(
                400,
                str(parsed.base_resp.status_code),
                parsed.base_resp.status_msg,
            )
        return str(parsed.file.file_id)

    def download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            with self._stream(url) as response, partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            partial.replace(destination)
        except (OSError, requests.RequestException) as exc:
            partial.unlink(missing_ok=True)
            raise H3TransientError("MiniMax output download failed") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, int] | None = None,
    ) -> Response:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise H3TransientError(f"MiniMax {method} request failed") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise H3TransientError(f"MiniMax request returned {response.status_code}")
        self._raise_definite_error(response)
        return response

    @contextmanager
    def _stream(self, url: str) -> Iterator[Response]:
        response = self.session.get(url, stream=True, timeout=max(self.timeout_seconds, 180.0))
        response.raise_for_status()
        try:
            yield response
        finally:
            response.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_definite_error(response: Response) -> None:
        if response.status_code < 400:
            return
        code = str(response.status_code)
        message = response.text or f"MiniMax request failed with {response.status_code}"
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    typed_error = cast(dict[str, object], error)
                    raw_type = typed_error.get("type")
                    raw_message = typed_error.get("message")
                    if isinstance(raw_type, str):
                        code = raw_type
                    if isinstance(raw_message, str):
                        message = raw_message
        except ValueError:
            pass
        raise H3DefiniteRequestError(response.status_code, code, message)
