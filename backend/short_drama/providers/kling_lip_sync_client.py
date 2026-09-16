"""Narrow AceData adapter for editing an existing video with existing speech.

This module never retries generation POSTs. A returned task ID takes precedence
over an HTTP error because it must be reconciled before another submission.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
from typing import Literal, Protocol, cast

import requests
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from requests import Response, Session


class KlingClientError(RuntimeError):
    """An operation failed without changing local project content."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 details: dict[str, JsonValue] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class KlingRequestRejected(KlingClientError):
    def __init__(self, status_code: int, message: str, *, details: dict[str, JsonValue] | None = None) -> None:
        super().__init__(message, status_code=status_code, details=details)


class KlingSubmissionUnknown(KlingClientError):
    """A request may have reached the provider; do not resubmit automatically."""


class KlingTransientError(KlingClientError):
    """Query, upload or download can be retried without generating another video."""


class KlingSubmission(BaseModel):
    task_id: str = Field(min_length=1)
    http_status: int
    response: dict[str, JsonValue]


class KlingRemoteTask(BaseModel):
    # Preserve provider-reported billing/usage when present at the task level.
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    request: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    response: dict[str, JsonValue] | None = None
    created_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    elapsed: float | None = None
    type: str | None = None

    @property
    def status(self) -> Literal["running", "succeeded", "failed"]:
        response = self.response or {}
        if response.get("success") is False or response.get("state") == "failed":
            return "failed"
        if response.get("state") == "succeed":
            return "succeeded"
        # A timestamp without a recognizable result is not a successful video.
        return "running"

    @property
    def video_url(self) -> str | None:
        value = (self.response or {}).get("video_url")
        return value if isinstance(value, str) and value.startswith("https://") else None


class KlingLipSyncClient(Protocol):
    upload_http_status: int | None
    def upload_video(self, path: Path) -> str: ...
    def submit(self, *, video_url: str, audio_path: Path) -> KlingSubmission: ...
    def query(self, task_id: str) -> KlingRemoteTask: ...
    def download(self, url: str, destination: Path) -> None: ...


class AceDataKlingLipSyncClient:
    def __init__(self, *, api_key: str, upload_token: str | None = None, session: Session | None = None,
                 timeout_seconds: float = 60.0) -> None:
        if not api_key.strip():
            raise ValueError("口型修复需要已配置的 AceData API Key")
        self._api_key = api_key.strip()
        self._upload_token = (upload_token or "").strip() or self._api_key
        self.upload_http_status: int | None = None
        self._session = session or requests.Session()
        self._timeout = timeout_seconds

    def _headers(self, *, upload: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._upload_token if upload else self._api_key}", "Accept": "application/json"}

    @staticmethod
    def _payload(response: Response) -> dict[str, JsonValue]:
        try:
            payload = cast(object, response.json())
        except ValueError:
            return {}
        return cast(dict[str, JsonValue], payload) if isinstance(payload, dict) else {}

    def _safe_details(self, payload: dict[str, JsonValue], depth: int = 0) -> dict[str, JsonValue]:
        details: dict[str, JsonValue] = {}
        for key in ("detail", "message", "code", "trace_id", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                for credential in (self._api_key, self._upload_token):
                    value = value.replace(credential, "[credential omitted]")
                details[key] = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [credential omitted]", value)[:700]
            elif isinstance(value, (int, float, bool)):
                details[key] = value
            elif key == "error" and isinstance(value, dict) and depth < 3:
                details[key] = self._safe_details(value, depth + 1)
        return details

    def _detail(self, response: Response, payload: dict[str, JsonValue]) -> str:
        details = self._safe_details(payload)
        return f"HTTP {response.status_code}" + ("：" + json.dumps(details, ensure_ascii=False)[:1000] if details else "")

    def upload_video(self, path: Path) -> str:
        self.upload_http_status = None
        if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov"}:
            raise KlingRequestRejected(400, "口型修复需要可用的 MP4 或 MOV 输入")
        if not 0 < path.stat().st_size <= 100 * 1024 * 1024:
            raise KlingRequestRejected(400, "口型修复输入视频须大于 0 且不超过 100 MB")
        try:
            with path.open("rb") as stream:
                response = self._session.post(
                    "https://platform.acedata.cloud/api/v1/files/",
                    headers=self._headers(upload=True), files={"file": (path.name, stream,
                        "video/quicktime" if path.suffix.lower() == ".mov" else "video/mp4")},
                    timeout=self._timeout,
                )
        except (OSError, requests.RequestException) as exc:
            raise KlingTransientError("原视频上传未完成，可重试上传；尚未提交生成") from exc
        try:
            self.upload_http_status = response.status_code
            payload = self._payload(response)
            if response.status_code >= 400:
                if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                    raise KlingRequestRejected(response.status_code, f"原视频上传被拒绝：{self._detail(response, payload)}；尚未提交口型生成。请检查文件上传凭据及权限。",
                        details=self._safe_details(payload))
                raise KlingTransientError(f"原视频上传未完成：{self._detail(response, payload)}；尚未提交口型生成。",
                    status_code=response.status_code, details=self._safe_details(payload))
            url = payload.get("file_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise KlingTransientError("上传响应未提供可用的视频地址；尚未提交生成", status_code=response.status_code)
            return url
        finally:
            response.close()

    def submit(self, *, video_url: str, audio_path: Path) -> KlingSubmission:
        if not video_url.startswith("https://"):
            raise KlingRequestRejected(400, "口型修复需要已上传视频的 HTTPS 地址")
        try:
            if not 0 < audio_path.stat().st_size <= 5 * 1024 * 1024:
                raise KlingRequestRejected(400, "驱动声音须大于 0 且不超过 5 MB")
            encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise KlingRequestRejected(400, "驱动声音文件不可读取") from exc
        payload: dict[str, JsonValue] = {"mode": "audio2video", "video_url": video_url,
            "audio_type": "file", "audio_file": encoded, "async": True}
        try:
            response = self._session.post("https://api.acedata.cloud/kling/lip-sync",
                headers=self._headers(), json=payload, timeout=self._timeout)
        except requests.RequestException as exc:
            raise KlingSubmissionUnknown("口型修复提交结果未知；需找回原任务，不能自动重发") from exc
        try:
            result = self._payload(response)
            task_id = result.get("task_id")
            if isinstance(task_id, str) and task_id.strip():
                return KlingSubmission(task_id=task_id, http_status=response.status_code, response=result)
            detail = self._detail(response, result)
            if response.status_code in {400, 401, 402, 403, 404, 422, 429}:
                raise KlingRequestRejected(response.status_code, f"口型修复请求被拒绝：{detail}")
            raise KlingSubmissionUnknown(f"口型修复响应没有可找回的任务 ID：{detail}；不能自动重发")
        finally:
            response.close()

    def query(self, task_id: str) -> KlingRemoteTask:
        if not task_id.strip():
            raise ValueError("查询需要供应商任务 ID")
        try:
            response = self._session.post("https://api.acedata.cloud/kling/tasks",
                headers=self._headers(), json={"action": "retrieve", "id": task_id}, timeout=self._timeout)
        except requests.RequestException as exc:
            raise KlingTransientError("暂时无法查询口型修复，保留原任务 ID 后重试") from exc
        try:
            payload = self._payload(response)
            if response.status_code >= 400:
                raise KlingTransientError(f"口型修复查询失败：{self._detail(response, payload)}")
            try:
                task = KlingRemoteTask.model_validate(payload)
            except ValidationError as exc:
                raise KlingTransientError("口型修复查询响应不完整，保留原任务 ID") from exc
            if task.id != task_id:
                raise KlingTransientError("口型修复查询返回了不同任务，未采用结果")
            return task
        finally:
            response.close()

    def download(self, url: str, destination: Path) -> None:
        if not url.startswith("https://"):
            raise KlingTransientError("口型修复结果缺少有效的 HTTPS 视频地址")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        response: Response | None = None
        try:
            # CDN requests must not receive the account credential.
            response = self._session.get(url, stream=True, timeout=self._timeout)
            response.raise_for_status()
            with partial.open("wb") as stream:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        stream.write(block)
            if not partial.stat().st_size:
                raise KlingTransientError("口型修复结果为空，可按原任务重新下载")
            partial.replace(destination)
        except (OSError, requests.RequestException) as exc:
            raise KlingTransientError("口型修复结果下载失败，可按原任务重新下载") from exc
        finally:
            if response is not None:
                response.close()
            partial.unlink(missing_ok=True)
