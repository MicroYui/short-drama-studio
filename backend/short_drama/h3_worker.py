"""Persistent MiniMax-H3 task orchestration on top of the shared task state machine."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from short_drama.h3_models import (
    H3DeleteResponse,
    H3MediaInput,
    H3ProviderRun,
    H3RemoteStatus,
    H3RemoteTask,
    H3UploadedInput,
)
from short_drama.media import H3MediaIngestor, MediaGenerationError
from short_drama.models import (
    EvidenceStatus,
    Freshness,
    GenerationTask,
    QAReport,
    QaIssue,
    QaStatus,
    RouteEvidence,
    Take,
    TakeAvailability,
    TaskStatus,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.minimax_h3_client import (
    H3Client,
    H3DefiniteRequestError,
    H3SubmissionUnknown,
    H3TransientError,
    MiniMaxH3Client,
)
from short_drama.providers.minimax_h3_compiler import compile_h3_request
from short_drama.repository import ShortDramaRepository
from short_drama.worker import InvalidTaskTransition, utc_now


class H3ProviderConfigurationError(ValueError):
    """Raised when a real H3 action needs a missing API key."""


H3ClientFactory = Callable[[str], H3Client]


def default_h3_client_factory(api_key: str) -> H3Client:
    return MiniMaxH3Client(api_key=api_key)


class PersistentH3Worker:
    """Submits, polls, downloads, and reconciles real H3 tasks without in-memory truth."""

    def __init__(
        self,
        *,
        repository: ShortDramaRepository,
        workspace_root: Path,
        config_store: H3ProviderConfigStore,
        client_factory: H3ClientFactory = default_h3_client_factory,
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root
        self.config_store = config_store
        self.client_factory = client_factory
        self.ingestor = H3MediaIngestor()

    def advance(self, task_id: str) -> GenerationTask:
        task = self._require_h3_task(task_id)
        if task.status == TaskStatus.QUEUED:
            return self._prepare(task)
        if task.status == TaskStatus.SUBMITTING:
            return self._submit(task)
        if task.status in {TaskStatus.ACKNOWLEDGED, TaskStatus.RUNNING}:
            return self._poll(task)
        raise InvalidTaskTransition(f"Task {task.id} cannot advance from {task.status.value}")

    def cancel(self, task_id: str) -> GenerationTask:
        task = self._require_h3_task(task_id)
        now = utc_now()
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.CANCELLED
            task.error_code = "CANCELLED_BY_USER"
            task.error_message = "尚未提交到 MiniMax 的本地任务已取消。"
            task.finished_at = now
            task.updated_at = now
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.CANCELLED)
            return task
        if task.status in {TaskStatus.SUBMITTING, TaskStatus.SUBMIT_UNKNOWN}:
            raise InvalidTaskTransition(
                "H3 submit outcome must be reconciled before cancellation"
            )
        if task.status == TaskStatus.RUNNING:
            raise InvalidTaskTransition("MiniMax does not allow cancelling a running H3 task")
        if task.status != TaskStatus.ACKNOWLEDGED or task.provider_task_id is None:
            raise InvalidTaskTransition(f"Task {task.id} cannot be cancelled from {task.status.value}")

        client = self._client()
        remote = client.query(task.provider_task_id)
        run = self._require_run(task.id)
        run.latest_remote_task = remote
        run.updated_at = now
        self.repository.save_provider_run(run)
        if remote.status == H3RemoteStatus.RUNNING:
            task.status = TaskStatus.RUNNING
            task.started_at = task.started_at or now
            task.updated_at = now
            self.repository.save_task(task)
            raise InvalidTaskTransition("MiniMax does not allow cancelling a running H3 task")
        if remote.status != H3RemoteStatus.QUEUED:
            return self._apply_remote_state(task, run, remote)
        result = client.cancel_or_delete(task.provider_task_id)
        return self._record_cancel(task, result, now)

    def reconcile(self, task_id: str, provider_task_id: str) -> GenerationTask:
        task = self._require_h3_task(task_id)
        if task.status != TaskStatus.SUBMIT_UNKNOWN:
            raise InvalidTaskTransition(f"Task {task.id} is not waiting for reconciliation")
        client = self._client()
        remote = client.query(provider_task_id)
        if remote.model != task.model:
            raise InvalidTaskTransition("The selected MiniMax task uses a different model")
        if remote.resolution not in {None, task.resolution}:
            raise InvalidTaskTransition("The selected MiniMax task uses a different resolution")
        if remote.duration not in {None, int(task.requested_duration_seconds)}:
            raise InvalidTaskTransition("The selected MiniMax task uses a different duration")
        now = utc_now()
        self.repository.record_provider_submission(
            request_fingerprint=task.request_fingerprint,
            provider_task_id=provider_task_id,
            provider=task.provider,
            created_at=now,
        )
        task.provider_task_id = provider_task_id
        task.error_code = None
        task.error_message = None
        task.updated_at = now
        run = self._require_run(task.id)
        run.latest_remote_task = remote
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = now
        self.repository.save_provider_run(run)
        self._set_evidence_status(task.id, EvidenceStatus.AWAITING_OUTPUT)
        return self._apply_remote_state(task, run, remote)

    def resolve_not_submitted(self, task_id: str, reason: str) -> GenerationTask:
        task = self._require_h3_task(task_id)
        if task.status != TaskStatus.SUBMIT_UNKNOWN:
            raise InvalidTaskTransition(f"Task {task.id} is not waiting for reconciliation")
        now = utc_now()
        message = reason.strip()
        if not message:
            raise InvalidTaskTransition("A reconciliation reason is required")
        run = self._require_run(task.id)
        return self._mark_failed(
            task,
            run,
            "H3_SUBMISSION_NOT_CREATED",
            message,
            now,
        )

    def recover_output(self, task_id: str) -> GenerationTask:
        task = self._require_h3_task(task_id)
        if task.status != TaskStatus.OUTPUT_MISSING:
            raise InvalidTaskTransition(f"Task {task.id} has no missing H3 output")
        if task.provider_task_id is None:
            raise InvalidTaskTransition("H3 task has no provider task ID")
        remote = self._client().query(task.provider_task_id)
        if remote.status != H3RemoteStatus.SUCCEEDED:
            raise InvalidTaskTransition(
                f"MiniMax task is {remote.status.value}; output cannot be recovered"
            )
        run = self._require_run(task.id)
        run.latest_remote_task = remote
        run.updated_at = utc_now()
        self.repository.save_provider_run(run)
        return self._download_success(task, run, remote)

    def list_reconciliation_candidates(self) -> list[H3RemoteTask]:
        return self._client().list_tasks(page_size=50)

    def validate_credentials(self) -> None:
        self._client().list_tasks(page_size=1)

    def _prepare(self, task: GenerationTask) -> GenerationTask:
        run = self._require_run(task.id)
        client = self._client()
        uploaded_inputs: list[H3UploadedInput] = []

        def resolve_url(media: H3MediaInput) -> str:
            if media.source.external_url is not None:
                return media.source.external_url
            if media.source.file_id is not None:
                return f"mm_file://{media.source.file_id}"
            if media.source.local_path is None:
                raise ValueError("H3 media source is empty")
            uploaded = self._upload_local_input(client, media)
            uploaded_inputs.append(uploaded)
            return uploaded.provider_file_id

        request = compile_h3_request(
            route=task.route,
            prompt=run.prompt,
            duration_seconds=int(task.requested_duration_seconds),
            media_inputs=run.media_inputs,
            resolve_url=resolve_url,
        )
        now = utc_now()
        run.request = request
        run.uploaded_inputs = uploaded_inputs
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = now
        self.repository.save_provider_run(run)
        task.status = TaskStatus.SUBMITTING
        task.updated_at = now
        self.repository.save_task(task)
        return task

    def _submit(self, task: GenerationTask) -> GenerationTask:
        run = self._require_run(task.id)
        if run.request is None:
            raise InvalidTaskTransition("H3 task has no compiled request")
        now = utc_now()
        try:
            provider_task_id = self._client().submit(run.request)
        except H3DefiniteRequestError as exc:
            return self._mark_failed(task, run, exc.code, str(exc), now)
        except H3SubmissionUnknown as exc:
            task.status = TaskStatus.SUBMIT_UNKNOWN
            task.error_code = "H3_SUBMIT_UNKNOWN"
            task.error_message = str(exc)
            task.submitted_at = now
            task.updated_at = now
            run.last_error_code = task.error_code
            run.last_error_message = task.error_message
            run.updated_at = now
            self.repository.save_provider_run(run)
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.SUBMIT_UNKNOWN)
            return task
        recorded_id, _created = self.repository.record_provider_submission(
            request_fingerprint=task.request_fingerprint,
            provider_task_id=provider_task_id,
            provider=task.provider,
            created_at=now,
        )
        task.provider_task_id = recorded_id
        task.status = TaskStatus.ACKNOWLEDGED
        task.submitted_at = now
        task.error_code = None
        task.error_message = None
        task.updated_at = now
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = now
        self.repository.save_provider_run(run)
        self.repository.save_task(task)
        return task

    def _poll(self, task: GenerationTask) -> GenerationTask:
        if task.provider_task_id is None:
            raise InvalidTaskTransition("Acknowledged H3 task has no provider task ID")
        run = self._require_run(task.id)
        now = utc_now()
        try:
            remote = self._client().query(task.provider_task_id)
        except H3TransientError as exc:
            task.error_code = "H3_POLL_TRANSIENT"
            task.error_message = str(exc)
            task.updated_at = now
            run.last_error_code = task.error_code
            run.last_error_message = task.error_message
            run.updated_at = now
            self.repository.save_provider_run(run)
            self.repository.save_task(task)
            return task
        run.latest_remote_task = remote
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = now
        self.repository.save_provider_run(run)
        return self._apply_remote_state(task, run, remote)

    def _apply_remote_state(
        self,
        task: GenerationTask,
        run: H3ProviderRun,
        remote: H3RemoteTask,
    ) -> GenerationTask:
        now = utc_now()
        task.error_code = None
        task.error_message = None
        task.updated_at = now
        if remote.status == H3RemoteStatus.QUEUED:
            task.status = TaskStatus.ACKNOWLEDGED
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.AWAITING_OUTPUT)
            return task
        if remote.status == H3RemoteStatus.RUNNING:
            task.status = TaskStatus.RUNNING
            task.started_at = task.started_at or now
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.AWAITING_OUTPUT)
            return task
        if remote.status == H3RemoteStatus.FAILED:
            error = remote.error
            return self._mark_failed(
                task,
                run,
                error.code if error and error.code else "H3_REMOTE_FAILED",
                error.message if error and error.message else "MiniMax H3 task failed",
                now,
            )
        if remote.status == H3RemoteStatus.CANCELLED:
            task.status = TaskStatus.CANCELLED
            task.error_code = "H3_REMOTE_CANCELLED"
            task.error_message = "MiniMax H3 task was cancelled"
            task.finished_at = now
            run.latest_remote_task = remote
            run.updated_at = now
            self.repository.save_provider_run(run)
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.CANCELLED)
            return task
        return self._download_success(task, run, remote)

    def _download_success(
        self,
        task: GenerationTask,
        run: H3ProviderRun,
        remote: H3RemoteTask,
    ) -> GenerationTask:
        url = remote.content.url if remote.content is not None else None
        now = utc_now()
        if not url:
            return self._mark_output_missing(
                task,
                run,
                "H3_OUTPUT_URL_MISSING",
                "MiniMax succeeded without a downloadable content.url",
                now,
            )
        destination = (
            self.workspace_root
            / task.project_id
            / "takes"
            / task.shot_id
            / f"h3-{task.id}.mp4"
        )
        run.output_url = url
        run.updated_at = now
        self.repository.save_provider_run(run)
        try:
            self._client().download(url, destination)
            media = self.ingestor.ingest(destination)
        except (H3TransientError, MediaGenerationError) as exc:
            return self._mark_output_missing(task, run, "H3_OUTPUT_MISSING", str(exc), now)

        aspect_ok = media.height > media.width and abs(media.width / media.height - 9 / 16) < 0.04
        resolution_ok = 700 <= min(media.width, media.height) <= 820
        duration_ok = abs(media.duration_seconds - task.requested_duration_seconds) <= 0.75
        technical_passed = aspect_ok and resolution_ok and duration_ok and media.audio_present
        issues: list[QaIssue] = []
        if not aspect_ok:
            issues.append(
                QaIssue(
                    code="H3_ASPECT_RATIO",
                    severity="ERROR",
                    message=f"Expected portrait 9:16, got {media.width}x{media.height}",
                    repair_layer="PROVIDER_RETRY",
                )
            )
        if not resolution_ok:
            issues.append(
                QaIssue(
                    code="H3_RESOLUTION",
                    severity="ERROR",
                    message=f"Expected a 768P-class source, got {media.width}x{media.height}",
                    repair_layer="PROVIDER_RETRY",
                )
            )
        if not duration_ok:
            issues.append(
                QaIssue(
                    code="H3_DURATION",
                    severity="ERROR",
                    message=(
                        f"Expected {task.requested_duration_seconds:.1f}s, "
                        f"got {media.duration_seconds:.2f}s"
                    ),
                    repair_layer="PROVIDER_RETRY",
                )
            )
        if not media.audio_present:
            issues.append(
                QaIssue(
                    code="H3_AUDIO_MISSING",
                    severity="ERROR",
                    message="H3 source has no audio stream",
                    repair_layer="AUDIO_ROUTE",
                )
            )

        take_id = f"take-{task.id}"
        report = QAReport(
            id=f"qa-{take_id}",
            take_id=take_id,
            status=QaStatus.PENDING,
            hard_gate_passed=False,
            score=0.0,
            issues=issues,
            checks=[
                f"source={media.width}x{media.height}",
                f"duration={media.duration_seconds:.3f}s",
                f"fps={media.fps:.3f}",
                f"audio={'present' if media.audio_present else 'missing'}",
                "真人身份、表演、对白、声线、口型与连续性等待人工审片",
            ],
            created_at=now,
        )
        take = Take(
            id=take_id,
            project_id=task.project_id,
            shot_id=task.shot_id,
            task_id=task.id,
            candidate_index=task.candidate_index,
            operation=task.operation,
            provider=task.provider,
            model=task.model,
            route=task.route,
            media_path=str(media.media_path.resolve()),
            thumbnail_path=str(media.thumbnail_path.resolve()),
            duration_seconds=media.duration_seconds,
            width=media.width,
            height=media.height,
            availability=TakeAvailability.AVAILABLE,
            qa_status=QaStatus.PENDING,
            is_selected=False,
            freshness=Freshness.CURRENT,
            qa_report=report,
            created_at=now,
        )
        self.repository.save_take(take)
        evidence = self._require_evidence(task.id)
        evidence.take_id = take.id
        evidence.status = EvidenceStatus.AWAITING_REVIEW
        evidence.technical_passed = technical_passed
        self.repository.save_route_evidence(evidence)

        run.latest_remote_task = remote
        run.output_url = url
        run.downloaded_path = str(destination.resolve())
        run.estimated_cost_usd = self._estimate_cost(run, remote)
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = now
        self.repository.save_provider_run(run)
        task.status = TaskStatus.SUCCEEDED
        task.error_code = None
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        self.repository.save_task(task)
        return task

    def _upload_local_input(self, client: H3Client, media: H3MediaInput) -> H3UploadedInput:
        assert media.source.local_path is not None
        path = Path(media.source.local_path).expanduser().resolve()
        stat = path.stat()
        cache_key = hashlib.sha256(
            f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        ).hexdigest()
        now = utc_now()
        cached = self.repository.get_provider_upload(cache_key, now)
        if cached is not None:
            return cached.model_copy(update={"role": media.role, "media_kind": media.kind})
        file_id = client.upload_file(path)
        expires_at = (
            datetime.now(UTC) + timedelta(days=6, hours=23)
        ).isoformat(timespec="milliseconds")
        uploaded = H3UploadedInput(
            local_path=str(path),
            provider_file_id=file_id,
            media_kind=media.kind,
            role=media.role,
            expires_at=expires_at,
        )
        self.repository.save_provider_upload(
            cache_key=cache_key,
            provider="minimax",
            record=uploaded,
        )
        return uploaded

    def _mark_failed(
        self,
        task: GenerationTask,
        run: H3ProviderRun,
        code: str,
        message: str,
        now: str,
    ) -> GenerationTask:
        task.status = TaskStatus.FAILED
        task.error_code = code
        task.error_message = message
        task.finished_at = now
        task.updated_at = now
        run.last_error_code = code
        run.last_error_message = message
        run.updated_at = now
        self.repository.save_provider_run(run)
        self.repository.save_task(task)
        self._set_evidence_status(task.id, EvidenceStatus.FAILED)
        return task

    def _mark_output_missing(
        self,
        task: GenerationTask,
        run: H3ProviderRun,
        code: str,
        message: str,
        now: str,
    ) -> GenerationTask:
        task.status = TaskStatus.OUTPUT_MISSING
        task.error_code = code
        task.error_message = message
        task.finished_at = now
        task.updated_at = now
        run.last_error_code = code
        run.last_error_message = message
        run.updated_at = now
        self.repository.save_provider_run(run)
        self.repository.save_task(task)
        self._set_evidence_status(task.id, EvidenceStatus.OUTPUT_MISSING)
        return task

    def _record_cancel(
        self,
        task: GenerationTask,
        response: H3DeleteResponse,
        now: str,
    ) -> GenerationTask:
        if response.action != "cancelled":
            raise InvalidTaskTransition(
                f"MiniMax performed {response.action}; queued cancellation was expected"
            )
        task.status = TaskStatus.CANCELLED
        task.error_code = "CANCELLED_BY_USER"
        task.error_message = "MiniMax queued task was cancelled before processing."
        task.finished_at = now
        task.updated_at = now
        self.repository.save_task(task)
        self._set_evidence_status(task.id, EvidenceStatus.CANCELLED)
        return task

    @staticmethod
    def _estimate_cost(run: H3ProviderRun, remote: H3RemoteTask) -> float:
        usage = remote.usage
        if usage is not None and usage.output_seconds is not None:
            output_seconds = usage.output_seconds
        elif remote.duration is not None:
            output_seconds = remote.duration
        elif run.request is not None:
            output_seconds = run.request.duration
        else:
            output_seconds = 0
        input_video_seconds = (
            usage.input_seconds
            if usage is not None and usage.input_seconds is not None
            else 0
        )
        image_count = (
            usage.input_image_count
            if usage is not None and usage.input_image_count is not None
            else 0
        )
        additional_images = max(0, image_count - run.pricing.included_images)
        return round(
            output_seconds * run.pricing.output_per_second
            + input_video_seconds * run.pricing.reference_video_per_second
            + additional_images * run.pricing.additional_image_each,
            4,
        )

    def _client(self) -> H3Client:
        api_key = self.config_store.api_key()
        if not api_key:
            raise H3ProviderConfigurationError(
                "AceData API token is required before a real H3 provider action"
            )
        return self.client_factory(api_key)

    def _require_h3_task(self, task_id: str) -> GenerationTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.provider != "minimax" or task.model != "MiniMax-H3":
            raise InvalidTaskTransition(f"Task {task.id} is not a MiniMax-H3 task")
        return task

    def _require_run(self, task_id: str) -> H3ProviderRun:
        run = self.repository.get_provider_run(task_id)
        if run is None:
            raise InvalidTaskTransition(f"H3 provider run for task {task_id} is missing")
        return run

    def _require_evidence(self, task_id: str) -> RouteEvidence:
        self._require_h3_task(task_id)
        evidence = self.repository.get_route_evidence_for_task(task_id)
        if evidence is not None:
            return evidence
        raise InvalidTaskTransition(f"Route evidence for task {task_id} is missing")

    def _set_evidence_status(self, task_id: str, status: EvidenceStatus) -> None:
        evidence = self._require_evidence(task_id)
        evidence.status = status
        self.repository.save_route_evidence(evidence)
