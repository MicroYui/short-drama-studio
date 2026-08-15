"""HTTP client for MiniMax-H3 through the AceData Cloud API contract."""

from __future__ import annotations

import base64
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
    """Thin AceData transport; orchestration and persistence live above it."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.acedata.cloud",
        session: Session | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AceData API token is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def submit(self, request: H3VideoRequest) -> str:
        payload = cast(
            dict[str, object],
            request.model_dump(mode="json", exclude_none=True),
        )
        async_payload = {**payload, "async": True}
        try:
            response = self._submit_request(
                async_payload,
                timeout_seconds=self.timeout_seconds,
            )
            self._raise_definite_error(response)
        except H3DefiniteRequestError as exc:
            # AceData's published H3 contract documents async=true, but the live
            # gateway currently rejects it for some H3 V2 accounts/contracts.
            # A 4xx rejection is definite (no remote task was created), so it is
            # safe to retry once using the documented synchronous default.
            if not self._is_async_unsupported(exc):
                raise
            response = self._submit_request(
                payload,
                timeout_seconds=max(self.timeout_seconds, 900.0),
            )
            self._raise_definite_error(response)

        try:
            response_payload = response.json()
            try:
                return H3CreateResponse.model_validate(response_payload).task_id
            except ValidationError:
                return H3QueryResponse.model_validate(response_payload).task.id
        except (ValueError, ValidationError) as exc:
            raise H3SubmissionUnknown(
                "AceData MiniMax submit response did not contain a valid task"
            ) from exc

    def _submit_request(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> Response:
        try:
            response = self.session.post(
                f"{self.base_url}/minimax/videos",
                headers=self._headers(),
                json=payload,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            raise H3SubmissionUnknown(
                "AceData MiniMax submission transport outcome is unknown"
            ) from exc
        if response.status_code >= 500:
            raise H3SubmissionUnknown(
                f"AceData MiniMax submission returned {response.status_code}; outcome is unknown"
            )
        return response

    @staticmethod
    def _is_async_unsupported(exc: H3DefiniteRequestError) -> bool:
        message = str(exc).lower()
        return 400 <= exc.status_code < 500 and "async" in message and "not supported" in message

    def query(self, task_id: str) -> H3RemoteTask:
        response = self._request(
            "POST",
            "/minimax/tasks",
            json_data={"action": "retrieve", "id": task_id},
        )
        try:
            return H3QueryResponse.model_validate(response.json()).task
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("AceData MiniMax query response was not valid") from exc

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]:
        response = self._request(
            "POST",
            "/minimax/tasks",
            json_data={
                "action": "retrieve_batch",
                "offset": 0,
                "limit": max(1, min(page_size, 100)),
            },
        )
        try:
            return H3ListResponse.model_validate(response.json()).items
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("AceData MiniMax task-list response was not valid") from exc

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse:
        response = self._request(
            "POST",
            "/minimax/tasks",
            json_data={"action": "delete", "id": task_id},
        )
        try:
            payload = cast(object, response.json())
            if not isinstance(payload, dict):
                raise ValueError("response is not an object")
            typed = cast(dict[str, object], payload)
            returned_id = typed.get("id")
            deleted = typed.get("deleted")
            if not isinstance(returned_id, str) or deleted is not True:
                raise ValueError("response does not confirm deletion")
            return H3DeleteResponse(
                task_id=returned_id,
                action="cancelled",
                status="cancelled",
            )
        except (ValueError, ValidationError) as exc:
            raise H3TransientError("AceData MiniMax cancel response was not valid") from exc

    def upload_file(self, path: Path) -> str:
        """Encode a local input using AceData's documented Data URI form."""
        if not path.is_file():
            raise H3DefiniteRequestError(400, "LOCAL_FILE_MISSING", str(path))
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise H3TransientError("AceData local input could not be read") from exc
        reference = f"data:{mime_type};base64,{encoded}"
        if len(reference.encode("utf-8")) > 64 * 1024 * 1024:
            raise H3DefiniteRequestError(
                400,
                "LOCAL_DATA_URI_TOO_LARGE",
                "AceData request bodies are limited to 64 MB; use a public HTTPS URL for this input",
            )
        return reference

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
            raise H3TransientError("AceData MiniMax output download failed") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, object] | None = None,
    ) -> Response:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=json_data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise H3TransientError(f"AceData MiniMax {method} request failed") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise H3TransientError(
                f"AceData MiniMax request returned {response.status_code}"
            )
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
        message = response.text or f"AceData MiniMax request failed with {response.status_code}"
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    typed_error = cast(dict[str, object], error)
                    raw_type = typed_error.get("code") or typed_error.get("type")
                    raw_message = typed_error.get("message")
                    if isinstance(raw_type, (str, int)):
                        code = str(raw_type)
                    if isinstance(raw_message, str):
                        message = raw_message
                detail = cast(dict[str, object], payload).get("detail")
                if isinstance(detail, str):
                    message = detail
        except ValueError:
            pass
        raise H3DefiniteRequestError(response.status_code, code, message)
