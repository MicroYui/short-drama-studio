"""Persistent lip-sync submission, explicit-ID recovery and candidate ingestion."""
from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from threading import Lock
import time
from typing import cast

from pydantic import JsonValue
from short_drama.media import H3MediaIngestor, MediaGenerationError
from short_drama.models import Freshness, GenerationTask, QAReport, QaIssue, QaStatus, Take, TakeAvailability, TaskStatus
from short_drama.providers.kling_config import KlingProviderConfigStore
from short_drama.providers.kling_lip_sync_client import (KlingLipSyncClient,
    KlingClientError, KlingRemoteTask, KlingRequestRejected, KlingTransientError)
from short_drama.repository import ShortDramaRepository
from short_drama.studio_trace import record_trace
from short_drama.video_edit_media import prepared_inputs, select_source, source_file
from short_drama.video_edit_models import VideoEditRun
from short_drama.worker import InvalidTaskTransition, utc_now

KlingClientFactory = Callable[[str, str], KlingLipSyncClient]
LIP_SYNC_UNAVAILABLE_MESSAGE = "当前视频生成仅支持 MiniMax H3；历史 Kling 任务只可读取或取消。"


def remote_summary(remote: KlingRemoteTask) -> dict[str, JsonValue]:
    value = remote.model_dump(mode="json")
    request = dict(remote.request)
    encoded = request.pop("audio_file", None)
    if isinstance(encoded, str):
        try:
            request["audio_file_sha256"] = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
        except ValueError:
            request["audio_file_sha256"] = None
    value["request"] = request
    return cast(dict[str, JsonValue], value)


def capture_usage(run: VideoEditRun, phase: str, response: dict[str, JsonValue]) -> None:
    """Keep only actual supplied billing/usage values; never calculate a price."""
    fields = ("usage", "cost", "billing", "billed_amount", "credits_consumed", "currency", "unit", "cost_unit")
    values = {key: response[key] for key in fields if key in response and response[key] is not None}
    if not any(key in values for key in ("usage", "cost", "billing", "billed_amount", "credits_consumed")):
        return
    run.actual_usage = {**(run.actual_usage or {}), phase: values}
    run.usage_status = "PROVIDED"


class PersistentVideoEditWorker:
    def __init__(self, *, repository: ShortDramaRepository, workspace_root: Path,
                 config_store: KlingProviderConfigStore, client_factory: KlingClientFactory | None = None) -> None:
        self.repository, self.workspace_root = repository, workspace_root
        self.config_store, self.client_factory = config_store, client_factory
        self._guard = Lock()
        self._locks: dict[str, Lock] = {}

    def _client(self) -> KlingLipSyncClient:
        if self.client_factory is None:
            raise InvalidTaskTransition(LIP_SYNC_UNAVAILABLE_MESSAGE)
        key, upload_token = self.config_store.credentials()
        if not key:
            raise ValueError("口型修复需要已配置的 AceData API Key。")
        return self.client_factory(key, upload_token)

    def _read(self, task_id: str) -> tuple[GenerationTask, VideoEditRun]:
        task = self.repository.get_task(task_id)
        run = self.repository.get_video_edit_run_for_task(task_id)
        if task is None or run is None:
            raise KeyError(task_id)
        if task.provider != "kling":
            raise InvalidTaskTransition("此执行器只处理已有视频的 Kling 口型修复。")
        return task, run

    def _save(self, task: GenerationTask, run: VideoEditRun) -> GenerationTask:
        task.updated_at = run.updated_at = utc_now()
        task.error_code, task.error_message = run.last_error_code, run.last_error_message
        with self.repository.atomic():
            self.repository.save_task(task)
            self.repository.save_video_edit_run(run)
        record_trace("provider.video_edit", {"task_id": task.id, "project_id": task.project_id, "run_id": run.id,
            "provider": "kling", "model": run.model, "operation": "LIP_SYNC", "status": run.status,
            "provider_task_id": run.provider_task_id, "source_take_id": run.source_take_id,
            "candidate_take_id": run.candidate_take_id, "prepared_video_path": run.prepared_video_path,
            "prepared_audio_path": run.prepared_audio_path, "phase_seconds": cast(JsonValue, run.phase_seconds),
            "error_code": run.last_error_code, "error_message": run.last_error_message, "actual_usage": run.actual_usage,
            "usage_status": run.usage_status, "last_error_stage": run.last_error_stage,
            "upload_http_status": run.upload_http_status, "upload_error_detail": run.upload_error_detail,
            "preparation_retry_count": run.preparation_retry_count, "submission_attempt_state": run.submission_attempt_state})
        return task

    def _failed(self, task_id: str, code: str, message: str, *, output: bool = False) -> GenerationTask:
        with self.repository.atomic():
            task, run = self._read(task_id)
            if run.status in {"CANCELLED", "PAUSED"}:
                return task
            task.status = TaskStatus.OUTPUT_MISSING if output else TaskStatus.FAILED
            run.status = "OUTPUT_MISSING" if output else "FAILED"
            run.last_error_code, run.last_error_message = code, message
            if not output:
                task.finished_at = run.finished_at = utc_now()
            return self._save(task, run)

    def _source_bindings_changed(self, run: VideoEditRun) -> str | None:
        document = self.repository.get_project(run.project_id)
        if document is None:
            return "项目已不存在。"
        try:
            selection = select_source(document, self.repository.list_takes(run.project_id), run.request)
            if selection.baseline != run.baseline:
                return "所选使用位置、源区间或相关声音已改变。"
        except (ValueError, OSError, KeyError) as exc:
            return f"原使用位置已不可用：{exc}"
        return None

    def source_changed(self, run: VideoEditRun) -> str | None:
        changed = self._source_bindings_changed(run)
        if changed:
            return changed
        try:
            if any(source_file(item.path) != item for item in run.source_files):
                return "源媒体文件内容或位置已改变。"
        except (ValueError, OSError) as exc:
            return f"源媒体文件已不可用：{exc}"
        return None

    def advance(self, task_id: str) -> GenerationTask:
        self.require_execution()
        with self._operation_lock(task_id):
            return self._advance(task_id)

    def require_execution(self) -> None:
        if self.client_factory is None:
            raise InvalidTaskTransition(LIP_SYNC_UNAVAILABLE_MESSAGE)

    def _operation_lock(self, task_id: str) -> Lock:
        with self._guard:
            return self._locks.setdefault(task_id, Lock())

    def _advance(self, task_id: str) -> GenerationTask:
        # advance and explicit recovery share one local operation; cancellation
        # remains independent and never waits for provider I/O.
        task, run = self._read(task_id)
        if run.status in {"CANCELLED", "PAUSED", "SUCCEEDED", "FAILED"}:
            return task
        if run.next_poll_at and datetime.now(UTC) < datetime.fromisoformat(run.next_poll_at):
            return task
        if run.provider_task_id:
            return self._query(task_id)
        if run.status == "SUBMIT_UNKNOWN" or run.submission_attempt_state in {"STARTED", "UNKNOWN"}:
            with self.repository.atomic():
                task, run = self._read(task_id)
                if run.status in {"CANCELLED", "PAUSED", "SUCCEEDED", "FAILED"} or run.provider_task_id:
                    return task
                task.status, run.status = TaskStatus.SUBMIT_UNKNOWN, "SUBMIT_UNKNOWN"
                run.submission_attempt_state = "UNKNOWN"
                run.last_error_code = "KLING_SUBMIT_UNKNOWN"
                run.last_error_message = "原提交结果未知；请提供并核对原任务 ID，不能自动重发。"
                return self._save(task, run)
        if run.submission_attempt_state == "PREPARED":
            return self._submit(task_id)
        return self._prepare(task_id)

    def _prepare(self, task_id: str) -> GenerationTask:
        task, run = self._read(task_id)
        changed = self.source_changed(run)
        if changed:
            return self._failed(task_id, "LIP_SYNC_SOURCE_CHANGED", changed)
        started = time.monotonic()
        phase = "prepare"
        client: KlingLipSyncClient | None = None
        try:
            client = self._client()
            if not run.prepared_video_path or not run.prepared_audio_path or not (
                    Path(run.prepared_video_path).is_file() and Path(run.prepared_audio_path).is_file()):
                run = prepared_inputs(run, self.workspace_root / run.project_id / "video-edits" / run.id)
            with self.repository.atomic():
                task, current = self._read(task_id)
                if current.status != "QUEUED":
                    return task
                for name in ("prepared_video_path", "prepared_audio_path", "prepared_width", "prepared_height",
                             "prepared_fps", "prepared_duration_seconds", "prepared_audio_sha256"):
                    setattr(current, name, getattr(run, name))
                current.phase_seconds["prepare"] = time.monotonic() - started
                self._save(task, current)
            # The network operation is outside the project/SQLite write lock.
            if not current.uploaded_video_url:
                phase = "upload"
                started = time.monotonic()
                url = client.upload_video(Path(current.prepared_video_path or ""))
            else:
                url = current.uploaded_video_url
                started = time.monotonic()
            with self.repository.atomic():
                task, current = self._read(task_id)
                current.uploaded_video_url = url
                if phase == "upload":
                    current.phase_seconds["upload"] = time.monotonic() - started
                    current.upload_http_status = client.upload_http_status
                    current.upload_error_detail = {}
                if current.status != "QUEUED":
                    return self._save(task, current)
                current.status, current.submission_attempt_state = "PREPARED", "PREPARED"
                current.last_error_code = current.last_error_message = None
                current.last_error_stage = None
                task.status = TaskStatus.SUBMITTING
                return self._save(task, current)
        except (ValueError, OSError, MediaGenerationError, KlingRequestRejected, KlingTransientError) as exc:
            with self.repository.atomic():
                task, current = self._read(task_id)
                current.phase_seconds[phase] = time.monotonic() - started
                current.last_error_stage = "upload" if phase == "upload" else "prepare"
                if phase == "upload":
                    current.upload_http_status = client.upload_http_status if client is not None else None
                    current.upload_error_detail = exc.details if isinstance(exc, KlingClientError) else {}
                self._save(task, current)
            return self._failed(task_id, "KLING_UPLOAD_FAILED" if phase == "upload" else "LIP_SYNC_PREPARATION_FAILED", str(exc))

    def retry_preparation(self, task_id: str) -> GenerationTask:
        """Explicitly requeue the same unsubmitted operation; advance remains separate."""
        self.require_execution()
        def require_unsubmitted_failure(task: GenerationTask, run: VideoEditRun) -> None:
            if (task.status != TaskStatus.FAILED or run.status != "FAILED"
                    or run.submission_attempt_state != "NOT_STARTED" or run.provider_task_id
                    or task.provider_task_id or run.submitted_at or task.submitted_at):
                raise InvalidTaskTransition("只能重试明确未提交的准备/上传失败；已提交或未知结果须保留原任务并对账。")

        with self._operation_lock(task_id):
            task, checked = self._read(task_id)
            require_unsubmitted_failure(task, checked)
            # File checks may touch slow/offline storage; keep all of them
            # outside the SQLite write transaction and recheck bindings below.
            changed = self.source_changed(checked)
            if changed:
                raise InvalidTaskTransition(changed)
            with self.repository.atomic():
                task, run = self._read(task_id)
                require_unsubmitted_failure(task, run)
                if (run.control_revision != checked.control_revision or run.request != checked.request
                        or run.baseline != checked.baseline or run.source_files != checked.source_files):
                    raise InvalidTaskTransition("本次修复状态或输入已改变，请读取当前操作后再决定是否重试。")
                changed = self._source_bindings_changed(run)
                if changed:
                    raise InvalidTaskTransition(changed)
                run.control_revision += 1
                run.preparation_retry_count += 1
                run.status, task.status = "QUEUED", TaskStatus.QUEUED
                run.last_error_code = run.last_error_message = run.last_error_stage = None
                run.finished_at = task.finished_at = None
                run.next_poll_at = None
                return self._save(task, run)

    def _submit(self, task_id: str) -> GenerationTask:
        task, run = self._read(task_id)
        changed = self.source_changed(run)
        if changed:
            return self._failed(task_id, "LIP_SYNC_SOURCE_CHANGED", changed)
        client = self._client()
        with self.repository.atomic():
            task, run = self._read(task_id)
            if run.status != "PREPARED" or run.submission_attempt_state != "PREPARED":
                return task
            # Re-read relevant bindings in the same short transaction that
            # crosses the POST boundary; the client factory may have yielded.
            changed = self.source_changed(run)
            if changed:
                return self._failed(task_id, "LIP_SYNC_SOURCE_CHANGED", changed)
            known_id = self.repository.find_provider_submission(task.request_fingerprint)
            if known_id:
                return self._acknowledge(task_id, known_id)
            if not run.prepared_audio_path or not run.uploaded_video_url:
                raise InvalidTaskTransition("口型修复尚未准备完整输入。")
            run.submission_attempt_state, run.status = "STARTED", "SUBMITTING"
            task.submitted_at = run.submitted_at = utc_now()
            self._save(task, run)
        started = time.monotonic()
        try:
            submitted = client.submit(video_url=run.uploaded_video_url, audio_path=Path(run.prepared_audio_path))
        except KlingRequestRejected as exc:
            with self.repository.atomic():
                task, current = self._read(task_id)
                current.submission_attempt_state = "REJECTED"
                current.last_error_stage = "submit"
                current.submission_http_status = exc.status_code
                current.phase_seconds["submit"] = time.monotonic() - started
                self._save(task, current)
            return self._failed(task_id, "KLING_REQUEST_REJECTED", str(exc))
        except Exception as exc:
            # Once POST may have begun, even an unexpected client exception is
            # an unknown submission; never infer that it is safe to resend.
            with self.repository.atomic():
                task, current = self._read(task_id)
                current.submission_attempt_state = "UNKNOWN"
                if current.status != "PAUSED":
                    current.status = "SUBMIT_UNKNOWN"
                task.status = TaskStatus.SUBMIT_UNKNOWN
                current.last_error_code, current.last_error_message = "KLING_SUBMIT_UNKNOWN", str(exc)
                current.last_error_stage = "submit"
                current.phase_seconds["submit"] = time.monotonic() - started
                return self._save(task, current)
        with self.repository.atomic():
            task, current = self._read(task_id)
            current.submission_response, current.submission_http_status = submitted.response, submitted.http_status
            capture_usage(current, "submission", submitted.response)
            current.phase_seconds["submit"] = time.monotonic() - started
            self.repository.save_video_edit_run(current)
            return self._acknowledge(task_id, submitted.task_id)

    def _acknowledge(self, task_id: str, provider_id: str) -> GenerationTask:
        task, run = self._read(task_id)
        recorded, _ = self.repository.record_provider_submission(request_fingerprint=task.request_fingerprint,
            provider_task_id=provider_id, provider="kling", created_at=utc_now())
        task.provider_task_id = run.provider_task_id = recorded
        run.submission_attempt_state = "ACKNOWLEDGED"
        if run.status != "PAUSED":
            run.status = "RUNNING"
            run.last_error_code = run.last_error_message = None
        task.status = TaskStatus.ACKNOWLEDGED
        return self._save(task, run)

    def _query(self, task_id: str) -> GenerationTask:
        task, run = self._read(task_id)
        assert run.provider_task_id
        started = time.monotonic()
        try:
            remote = self._client().query(run.provider_task_id)
        except KlingTransientError as exc:
            with self.repository.atomic():
                task, run = self._read(task_id)
                if run.status == "PAUSED":
                    return task
                run.query_failures += 1
                run.last_error_code, run.last_error_message = "KLING_QUERY_RETRY", str(exc)
                run.next_poll_at = (datetime.now(UTC) + timedelta(seconds=min(30, 2 ** run.query_failures))).isoformat()
                if run.query_failures >= 3:
                    run.status, run.paused_after_submit = "PAUSED", True
                    run.last_error_message += "；已暂停本地等待，可按原任务 ID 恢复，未停止供应商执行。"
                return self._save(task, run)
        with self.repository.atomic():
            task, run = self._read(task_id)
            run.latest_remote = remote_summary(remote)
            capture_usage(run, "query", remote.response or {})
            capture_usage(run, "query_task", cast(dict[str, JsonValue], remote.model_dump(mode="json")))
            run.phase_seconds["query"] = run.phase_seconds.get("query", 0) + time.monotonic() - started
            run.query_failures = 0
            run.next_poll_at = None
            self._save(task, run)
            if run.status == "PAUSED":
                return task
        if remote.status == "failed":
            return self._failed(task_id, "KLING_REMOTE_FAILED", "供应商报告口型修复失败；原片与剪辑保持。")
        if remote.status == "succeeded":
            return self._download(task_id, remote)
        with self.repository.atomic():
            task, run = self._read(task_id)
            if run.status != "PAUSED":
                task.status, run.status = TaskStatus.RUNNING, "RUNNING"
                run.last_error_code = run.last_error_message = None
                run.next_poll_at = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
            return self._save(task, run)

    def _download(self, task_id: str, remote: KlingRemoteTask) -> GenerationTask:
        task, run = self._read(task_id)
        if not remote.video_url:
            return self._failed(task_id, "KLING_OUTPUT_MISSING", "供应商未返回结果视频地址，可按原任务重新查询。", output=True)
        destination = self.workspace_root / run.project_id / "video-edits" / run.id / "candidate.mp4"
        started = time.monotonic()
        with self.repository.atomic():
            task, run = self._read(task_id)
            if run.status == "PAUSED":
                return task
            run.output_url = remote.video_url
            self._save(task, run)
        try:
            self._client().download(remote.video_url, destination)
            media = H3MediaIngestor().ingest(destination)
        except (OSError, MediaGenerationError, KlingTransientError) as exc:
            return self._failed(task_id, "KLING_OUTPUT_MISSING", str(exc), output=True)
        duration = run.request.end_seconds - run.request.start_seconds
        issues: list[QaIssue] = []
        if (abs(media.duration_seconds - duration) > max(.08, 1 / max(media.fps, 1))
                or media.duration_seconds + .02 < duration or media.width <= 0 or media.height <= 0
                or run.prepared_width and run.prepared_height and abs(media.width / media.height - run.prepared_width / run.prepared_height) > .025):
            issues.append(QaIssue(code="LIP_SYNC_OUTPUT_GEOMETRY", severity="ERROR",
                message="结果时长或画幅不匹配所选区间，不能自动采用。", repair_layer="PROVIDER_OUTPUT"))
        with self.repository.atomic():
            task, run = self._read(task_id)
            identity = run.candidate_take_id or f"lip-sync-{run.id}"
            qa = QAReport(id=f"qa-{identity}", take_id=identity, status=QaStatus.REJECTED if issues else QaStatus.PENDING,
                hard_gate_passed=not issues, score=0, issues=issues, checks=["实际解码、时长与画幅检查；口型、身份与表演质量尚待实际播放观察"], created_at=utc_now())
            take = Take(id=identity, project_id=task.project_id, shot_id=task.shot_id, task_id=task.id,
                candidate_index=task.candidate_index, operation=task.operation, provider="kling", model=run.model,
                media_path=str(media.media_path), thumbnail_path=str(media.thumbnail_path), duration_seconds=media.duration_seconds,
                width=media.width, height=media.height, availability=TakeAvailability.AVAILABLE, qa_status=qa.status,
                is_selected=False, freshness=Freshness.CURRENT, qa_report=qa, created_at=utc_now())
            existing = self.repository.get_take(identity)
            if existing is not None:
                # Recovering the same remote result must preserve actual review
                # and adoption, including a concurrently selected candidate.
                take.is_selected, take.selected_shot_ids = existing.is_selected, existing.selected_shot_ids
                take.created_at = existing.created_at
                if not issues:
                    take.qa_status, take.qa_report = existing.qa_status, existing.qa_report
            self.repository.save_take(take)
            run.candidate_take_id, run.candidate_media_path = identity, take.media_path
            run.candidate_thumbnail_path = take.thumbnail_path
            run.quality_status = "TECHNICAL_FAILED" if issues else "AWAITING_REVIEW"
            run.phase_seconds["download"] = time.monotonic() - started
            task.status = TaskStatus.SUCCEEDED
            if run.status != "PAUSED":
                run.status = "SUCCEEDED"
            task.finished_at = run.finished_at = utc_now()
            run.last_error_code = run.last_error_message = None
            return self._save(task, run)

    def cancel(self, task_id: str) -> GenerationTask:
        with self.repository.atomic():
            task, run = self._read(task_id)
            if run.status in {"CANCELLED", "SUCCEEDED", "FAILED"}:
                return task
            run.control_revision += 1
            if run.provider_task_id or run.submission_attempt_state in {"STARTED", "UNKNOWN", "ACKNOWLEDGED"}:
                run.status, run.paused_after_submit = "PAUSED", True
                run.last_error_code = "LIP_SYNC_WAIT_PAUSED"
                run.last_error_message = (
                    "已暂停本地等待和采用；供应商可能仍在执行或计费。"
                    + ("保留原任务 ID 后可恢复查询。" if self.client_factory is not None
                       else "原任务 ID 与历史记录已保留；当前产品不再恢复 Kling 执行。")
                )
            else:
                task.status, run.status = TaskStatus.CANCELLED, "CANCELLED"
                task.finished_at = run.finished_at = utc_now()
                run.last_error_code, run.last_error_message = "LIP_SYNC_CANCELLED_BEFORE_SUBMIT", "已取消，未提交口型生成。"
            return self._save(task, run)

    def reconcile(self, task_id: str, provider_task_id: str | None = None) -> GenerationTask:
        self.require_execution()
        with self._operation_lock(task_id):
            return self._reconcile(task_id, provider_task_id)

    def _reconcile(self, task_id: str, provider_task_id: str | None = None) -> GenerationTask:
        original_task, run = self._read(task_id)
        if run.status == "SUCCEEDED" and run.candidate_media_path and Path(run.candidate_media_path).is_file():
            return original_task
        if run.status == "CANCELLED":
            raise InvalidTaskTransition("提交前已取消的操作不能接管远端任务。")
        control_revision = run.control_revision
        identity = run.provider_task_id or provider_task_id
        if not identity:
            raise InvalidTaskTransition("未知提交需要明确的原任务 ID；不能用宽列表或未找到记录来认定未提交。")
        if provider_task_id and run.provider_task_id and provider_task_id != run.provider_task_id:
            raise InvalidTaskTransition("不能把已有回执替换为另一个供应商任务。")
        remote = self._client().query(identity)
        if run.provider_task_id is None:
            request = remote.request
            encoded = request.get("audio_file")
            try:
                digest = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest() if isinstance(encoded, str) else None
            except ValueError:
                digest = None
            if (request.get("video_url") != run.uploaded_video_url or request.get("mode") != "audio2video"
                    or request.get("audio_type") != "file" or digest != run.prepared_audio_sha256 or digest is None):
                raise InvalidTaskTransition("该任务的原视频或驱动声音无法与本地请求精确匹配，未接管记录。")
        with self.repository.atomic():
            current_task, run = self._read(task_id)
            if run.control_revision != control_revision:
                return current_task
            if run.status == "SUCCEEDED" and run.candidate_media_path and Path(run.candidate_media_path).is_file():
                return current_task
            run.status, run.paused_after_submit = "RUNNING", False
            run.next_poll_at, run.query_failures = None, 0
            run.latest_remote = remote_summary(remote)
            capture_usage(run, "query", remote.response or {})
            capture_usage(run, "query_task", cast(dict[str, JsonValue], remote.model_dump(mode="json")))
            self.repository.save_video_edit_run(run)
            self._acknowledge(task_id, identity)
        if remote.status == "succeeded":
            return self._download(task_id, remote)
        if remote.status == "failed":
            return self._failed(task_id, "KLING_REMOTE_FAILED", "原供应商任务已失败；原片与剪辑保持。")
        return self._read(task_id)[0]
