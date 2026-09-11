"""Persistent MiniMax-H3 task orchestration on top of the shared task state machine."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from short_drama.asset_reference_truth import (
    AssetReferenceTruthError,
    resolve_current_asset_media,
)
from short_drama.h3_models import (
    H3DeleteResponse,
    H3MediaInput,
    H3ProviderRun,
    H3RemoteStatus,
    H3RemoteTask,
    H3UploadedInput,
)
from short_drama.media import H3MediaIngestor, MediaGenerationError
from short_drama.generation_plan_state import validate_generation_units_current
from short_drama.production_contracts import GenerationPlanContent
from short_drama.models import (
    EvidenceStatus,
    Freshness,
    GenerationTask,
    ProjectDocument,
    QAReport,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    RouteEvidence,
    Take,
    TakeAvailability,
    TaskStatus,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.minimax_h3_client import (
    H3_LOCAL_INPUT_ENCODING_PROFILE,
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
        self._advance_locks_guard = threading.Lock()
        self._advance_locks: dict[str, threading.Lock] = {}

    def advance(self, task_id: str) -> GenerationTask:
        initial = self._require_h3_task(task_id)
        initial_status = initial.status
        with self._advance_lock(initial.request_fingerprint):
            task = self._require_h3_task(task_id)
            if task.status != initial_status:
                return task
            if task.status == TaskStatus.QUEUED:
                return self._prepare(task)
            if task.status == TaskStatus.SUBMITTING:
                return self._submit(task)
            if task.status in {TaskStatus.ACKNOWLEDGED, TaskStatus.RUNNING}:
                return self._poll(task)
            raise InvalidTaskTransition(
                f"Task {task.id} cannot advance from {task.status.value}"
            )

    def _advance_lock(self, request_fingerprint: str) -> threading.Lock:
        with self._advance_locks_guard:
            lock = self._advance_locks.get(request_fingerprint)
            if lock is None:
                lock = threading.Lock()
                self._advance_locks[request_fingerprint] = lock
            return lock

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
        run = self._require_run(task.id)
        client = self._client()
        remote = client.query(provider_task_id)
        mismatch = self._reconciliation_mismatch_reason(task, run, remote)
        if mismatch is not None:
            raise InvalidTaskTransition(mismatch)
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

    def list_reconciliation_candidates_for_task(
        self,
        task_id: str,
    ) -> list[H3RemoteTask]:
        task = self._require_h3_task(task_id)
        if task.status != TaskStatus.SUBMIT_UNKNOWN:
            raise InvalidTaskTransition(f"Task {task.id} is not waiting for reconciliation")
        run = self._require_run(task.id)
        return [
            remote
            for remote in self.list_reconciliation_candidates()
            if self._reconciliation_mismatch_reason(task, run, remote) is None
        ]

    @staticmethod
    def _reconciliation_mismatch_reason(
        task: GenerationTask,
        run: H3ProviderRun,
        remote: H3RemoteTask,
    ) -> str | None:
        if remote.model != task.model:
            return "The selected MiniMax task uses a different model"
        if remote.resolution not in {None, task.resolution}:
            return "The selected MiniMax task uses a different resolution"
        if remote.duration not in {None, int(task.requested_duration_seconds)}:
            return "The selected MiniMax task uses a different duration"
        expected_ratio = None if run.request is None else run.request.ratio
        if remote.ratio is not None and remote.ratio != expected_ratio:
            return "The selected MiniMax task uses a different aspect ratio"
        if remote.task_type not in {None, "generation"}:
            return "The selected MiniMax task is not a generation task"
        if remote.modality not in {None, "video"}:
            return "The selected MiniMax task is not a video task"
        return None

    def validate_credentials(self) -> None:
        self._client().list_tasks(page_size=1)

    def input_staleness_reason(
        self,
        task: GenerationTask,
        run: H3ProviderRun | None = None,
    ) -> str | None:
        """Return why a not-yet-submitted H3 task no longer matches project truth."""

        document = self.repository.get_project(task.project_id)
        if document is None:
            return "The project document no longer exists"
        revision_by_shot = {item.id: item.revision for item in document.shots}
        expected_revisions = task.input_shot_revisions or {
            task.shot_id: task.input_shot_revision
        }
        missing_shots = [
            shot_id for shot_id in expected_revisions if shot_id not in revision_by_shot
        ]
        if missing_shots:
            return (
                "The task's editorial shot no longer exists: "
                + ", ".join(missing_shots)
            )
        stale_shots = [
            shot_id
            for shot_id, revision in expected_revisions.items()
            if revision_by_shot[shot_id] != revision
        ]
        if stale_shots:
            return (
                "The shot plan changed after this H3 task was compiled: "
                + ", ".join(stale_shots)
            )
        if task.input_script_version_id != document.project.current_script_version_id:
            return "The script changed after this H3 task was compiled"
        if task.input_bible_version_id != document.project.current_bible_version_id:
            return "The project bible changed after this H3 task was compiled"
        if (
            task.input_voice_reference_revision
            != document.project.voice_reference_revision
        ):
            return "Character voice references changed after this H3 task was compiled"
        if task.generation_unit_id is not None:
            if not task.shot_windows:
                return "The H3 generation unit has no editorial shot windows"
            if task.storyboard_document_id is None:
                return "The H3 generation unit has no pinned storyboard"
            if task.generation_plan_document_id is None:
                return "The H3 generation unit has no pinned generation plan"

        generation_plan: GenerationPlanContent | None = None
        if task.generation_unit_id is not None:
            generation_document = next((item for item in document.production_documents
                if item.id == task.generation_plan_document_id), None)
            generation_version = next((item for item in generation_document.versions
                if item.id == task.generation_plan_version_id), None) if generation_document else None
            if generation_version is not None:
                try:
                    generation_plan = GenerationPlanContent.model_validate(generation_version.payload)
                except ValueError:
                    # Legacy tasks without typed units retain their exact-document check.
                    pass

        pinned_storyboard_reason = self._pinned_document_staleness_reason(
            document=document,
            label="storyboard",
            project_document_id=document.storyboard_document_id,
            task_document_id=task.storyboard_document_id,
            task_version_id=task.storyboard_version_id,
            require_current=generation_plan is None,
        )
        if pinned_storyboard_reason is not None:
            return pinned_storyboard_reason
        pinned_generation_plan_reason = self._pinned_document_staleness_reason(
            document=document,
            label="generation plan",
            project_document_id=document.generation_plan_document_id,
            task_document_id=task.generation_plan_document_id,
            task_version_id=task.generation_plan_version_id,
            require_current=generation_plan is None,
        )
        if pinned_generation_plan_reason is not None:
            return pinned_generation_plan_reason
        if generation_plan is not None and task.generation_unit_id is not None:
            try:
                validate_generation_units_current(document, generation_plan, [task.generation_unit_id])
            except ValueError as exc:
                return str(exc)

        provider_run = run or self.repository.get_provider_run(task.id)
        if provider_run is None:
            return "The H3 provider run is missing"
        for field_name in (
            "storyboard_document_id",
            "storyboard_version_id",
            "generation_plan_document_id",
            "generation_plan_version_id",
            "generation_unit_id",
            "clip_group_id",
            "output_slot_id",
        ):
            if getattr(provider_run, field_name) != getattr(task, field_name):
                return (
                    "The H3 provider run no longer matches task metadata: "
                    + field_name
                )
        if provider_run.shot_windows != task.shot_windows:
            return "The H3 provider run no longer matches task shot windows"
        if (
            (task.generation_unit_id is not None or provider_run.shot_ids)
            and provider_run.shot_ids != task.covered_shot_ids()
        ):
            return "The H3 provider run no longer matches task editorial shots"
        for media in provider_run.media_inputs:
            version_id = media.asset_version_id
            if version_id is None:
                if media.subject_asset_id is not None:
                    return "A subject-linked H3 media input requires an AssetVersion"
                continue
            try:
                resolve_current_asset_media(
                    document,
                    asset_version_id=version_id,
                    subject_asset_id=media.subject_asset_id,
                )
            except AssetReferenceTruthError as exc:
                return str(exc)
        return None

    @staticmethod
    def _pinned_document_staleness_reason(
        *,
        document: ProjectDocument,
        label: str,
        project_document_id: str | None,
        task_document_id: str | None,
        task_version_id: str | None,
        require_current: bool = True,
    ) -> str | None:
        if task_document_id is None and task_version_id is None:
            return None
        if task_document_id is None or task_version_id is None:
            return f"The H3 task has an incomplete pinned {label} reference"
        if project_document_id != task_document_id:
            return f"The current {label} document changed after this H3 task was compiled"
        production_document = next(
            (
                item
                for item in document.production_documents
                if item.id == task_document_id
            ),
            None,
        )
        if production_document is None:
            return f"The pinned {label} document no longer exists"
        if not any(
            item.id == task_version_id for item in production_document.versions
        ):
            return f"The pinned {label} version no longer exists"
        if require_current and production_document.current_version_id != task_version_id:
            return f"The {label} changed after this H3 task was compiled"
        return None

    def _prepare(self, task: GenerationTask) -> GenerationTask:
        run = self._require_run(task.id)
        stale_reason = self.input_staleness_reason(task, run)
        if stale_reason is not None:
            return self._mark_failed(
                task,
                run,
                "H3_INPUTS_STALE",
                stale_reason,
                utc_now(),
            )
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
        run.submission_attempt_state = "PREPARED"
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
        existing_provider_task_id = self.repository.find_provider_submission(
            task.request_fingerprint
        )
        if existing_provider_task_id is not None:
            task.provider_task_id = existing_provider_task_id
            task.status = TaskStatus.ACKNOWLEDGED
            task.submitted_at = task.submitted_at or now
            task.error_code = None
            task.error_message = None
            task.updated_at = now
            run.last_error_code = None
            run.last_error_message = None
            run.updated_at = now
            self.repository.save_provider_run(run)
            self.repository.save_task(task)
            self._set_evidence_status(task.id, EvidenceStatus.AWAITING_OUTPUT)
            return task
        stale_reason = self.input_staleness_reason(task, run)
        if stale_reason is not None:
            return self._mark_failed(
                task,
                run,
                "H3_INPUTS_STALE",
                stale_reason,
                now,
            )
        client = self._client()
        run.submission_attempt_state = "STARTED"
        run.updated_at = now
        self.repository.save_provider_run(run)
        try:
            provider_task_id = client.submit(run.request)
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
            / (task.generation_unit_id or task.shot_id)
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
        required_window_end = max(
            (item.source_out_seconds for item in task.shot_windows),
            default=0.0,
        )
        windows_covered = (
            not task.shot_windows
            or media.duration_seconds + 0.05 >= required_window_end
        )
        technical_passed = (
            aspect_ok
            and resolution_ok
            and duration_ok
            and windows_covered
            and media.audio_present
        )
        issues: list[QaIssue] = []
        if not aspect_ok:
            issues.append(
                QaIssue(
                    code="H3_ASPECT_RATIO",
                    severity="ERROR",
                    message=f"Expected portrait 9:16, got {media.width}x{media.height}",
                    repair_layer="PROVIDER_RETRY",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
                )
            )
        if not resolution_ok:
            issues.append(
                QaIssue(
                    code="H3_RESOLUTION",
                    severity="ERROR",
                    message=f"Expected a 768P-class source, got {media.width}x{media.height}",
                    repair_layer="PROVIDER_RETRY",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
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
                    category=QaFailureCategory.TECHNICAL_MEDIA,
                )
            )
        if not windows_covered:
            issues.append(
                QaIssue(
                    code="H3_SHOT_WINDOWS_TRUNCATED",
                    severity="ERROR",
                    message=(
                        f"Generated media ends at {media.duration_seconds:.2f}s but the "
                        f"last editorial shot window ends at {required_window_end:.2f}s"
                    ),
                    repair_layer="PROVIDER_RETRY",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
                )
            )
        if not media.audio_present:
            issues.append(
                QaIssue(
                    code="H3_AUDIO_MISSING",
                    severity="ERROR",
                    message="H3 source has no audio stream",
                    repair_layer="AUDIO_ROUTE",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
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
                "视觉、对白和连续性可按当前任务观察；尚未检查不阻止预览、选用或剪辑",
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
            storyboard_document_id=task.storyboard_document_id,
            storyboard_version_id=task.storyboard_version_id,
            generation_plan_document_id=task.generation_plan_document_id,
            generation_plan_version_id=task.generation_plan_version_id,
            generation_unit_id=task.generation_unit_id,
            clip_group_id=task.clip_group_id,
            output_slot_id=task.output_slot_id,
            shot_windows=[item.model_copy(deep=True) for item in task.shot_windows],
            media_path=str(media.media_path.resolve()),
            thumbnail_path=str(media.thumbnail_path.resolve()),
            duration_seconds=media.duration_seconds,
            width=media.width,
            height=media.height,
            availability=TakeAvailability.AVAILABLE,
            qa_status=QaStatus.PENDING,
            is_selected=False,
            freshness=self._output_freshness(task, run),
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
        run.estimated_cost_credits = self._estimate_billing_credits(run, remote)
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

    def _output_freshness(
        self,
        task: GenerationTask,
        run: H3ProviderRun,
    ) -> Freshness:
        return (
            Freshness.CURRENT
            if self.input_staleness_reason(task, run) is None
            else Freshness.STALE
        )

    def _upload_local_input(self, client: H3Client, media: H3MediaInput) -> H3UploadedInput:
        assert media.source.local_path is not None
        path = Path(media.source.local_path).expanduser().resolve()
        stat = path.stat()
        cache_key = hashlib.sha256(
            f"{H3_LOCAL_INPUT_ENCODING_PROFILE}:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()
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
        output_seconds = PersistentH3Worker._billable_output_seconds(run, remote)
        usage = remote.usage
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

    @staticmethod
    def _estimate_billing_credits(
        run: H3ProviderRun,
        remote: H3RemoteTask,
    ) -> float:
        return round(
            PersistentH3Worker._billable_output_seconds(run, remote)
            * run.pricing.billing_output_per_second,
            6,
        )

    @staticmethod
    def _billable_output_seconds(
        run: H3ProviderRun,
        remote: H3RemoteTask,
    ) -> float:
        usage = remote.usage
        if usage is not None and usage.output_seconds is not None:
            return usage.output_seconds
        if remote.duration is not None:
            return remote.duration
        if run.request is not None:
            return run.request.duration
        return 0

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
