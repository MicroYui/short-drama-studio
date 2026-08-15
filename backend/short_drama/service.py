"""Application service for the provider-neutral short-drama workflow."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from uuid import uuid4

from short_drama.fixture import build_dont_trust_me_fixture, build_project_document
from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3MediaInput,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
    H3ProviderRun,
    H3ProviderRunsResponse,
    H3ReconciliationCandidatesResponse,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.h3_worker import (
    H3ClientFactory,
    H3ProviderConfigurationError,
    PersistentH3Worker,
    default_h3_client_factory,
)
from short_drama.media import MockMediaRenderer, TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    AutoDraftResponse,
    CreateProjectRequest,
    EnqueueRequest,
    EvidencePurpose,
    EvidenceStatus,
    ExportRecord,
    Freshness,
    GenerationTask,
    LocalReworkRequest,
    MockBehavior,
    OperationKind,
    OutputSpec,
    ProgressSummary,
    ProjectAggregate,
    ProjectDocument,
    ProjectListResponse,
    ProjectStage,
    RouteEvidence,
    QaStatus,
    ReviseScriptLineRequest,
    ScriptVersion,
    ShotPlan,
    SubtitleCue,
    Take,
    TaskStatus,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
    VideoGenerationRoute,
    WorkerDrainResponse,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.minimax_h3_client import H3ClientError
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    compile_h3_request,
    validate_h3_probe,
)
from short_drama.repository import ShortDramaRepository
from short_drama.worker import InvalidTaskTransition, PersistentMockWorker, utc_now


class ProjectNotFoundError(KeyError):
    """Raised when an API operation references an unknown project."""


class ArtifactNotFoundError(KeyError):
    """Raised when an API operation references an unknown task, take, shot, or line."""


class WorkflowConflictError(ValueError):
    """Raised when persisted workflow state does not allow the requested action."""


class ProviderOperationError(RuntimeError):
    """Raised when a configured external provider cannot complete an operation."""


_TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


class ShortDramaService:
    """Owns the SQLite truth source, worker, Mock adapter, and manifest exporter."""

    def __init__(
        self,
        *,
        database_path: Path,
        workspace_root: Path,
        h3_client_factory: H3ClientFactory | None = None,
    ) -> None:
        self.repository = ShortDramaRepository(database_path)
        self.workspace_root = workspace_root
        self.media_renderer = MockMediaRenderer(workspace_root)
        self.exporter = TimelineExporter(workspace_root)
        self.worker = PersistentMockWorker(self.repository, self.media_renderer)
        self.h3_config = H3ProviderConfigStore(
            database_path.parent / "short-drama-provider-settings.json"
        )
        self.h3_worker = PersistentH3Worker(
            repository=self.repository,
            workspace_root=workspace_root,
            config_store=self.h3_config,
            client_factory=h3_client_factory or default_h3_client_factory,
        )
        self._lock = threading.RLock()

    def list_projects(self) -> ProjectListResponse:
        return ProjectListResponse(
            projects=[document.project for document in self.repository.list_projects()]
        )

    def initialize_fixture(self) -> ProjectAggregate:
        with self._lock:
            fixture = build_dont_trust_me_fixture(utc_now())
            if not self.repository.project_exists(fixture.project.id):
                self.repository.save_project(fixture)
            return self.get_project(fixture.project.id)

    def create_project(self, request: CreateProjectRequest) -> ProjectAggregate:
        with self._lock:
            project_id = f"project-{uuid4().hex[:12]}"
            document = build_project_document(
                project_id=project_id,
                name=request.name.strip(),
                source_mode=request.source_mode,
                input_text=request.input_text,
                run_mode=request.run_mode,
                created_at=utc_now(),
            )
            self.repository.save_project(document)
            return self._aggregate(document)

    def delete_project(self, project_id: str) -> bool:
        return self.repository.delete_project(project_id)

    def get_project(self, project_id: str) -> ProjectAggregate:
        document = self._require_project_document(project_id)
        return self._aggregate(document)

    def get_h3_config(self) -> H3ProviderConfigResponse:
        return self.h3_config.response()

    def update_h3_config(
        self,
        request: H3ProviderConfigUpdate,
    ) -> H3ProviderConfigResponse:
        return self.h3_config.update(request)

    def validate_h3_credentials(self) -> H3ProviderConfigResponse:
        try:
            self.h3_worker.validate_credentials()
        except H3ProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return self.h3_config.response()

    def preview_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3CompilePreviewResponse:
        _document, _shot, prompt, duration = self._resolve_h3_probe(
            project_id=project_id,
            request=request,
        )

        def preview_url(media: H3MediaInput) -> str:
            if media.source.external_url is not None:
                return media.source.external_url
            if media.source.file_id is not None:
                return f"mm_file://{media.source.file_id}"
            assert media.source.local_path is not None
            return f"local-upload://{Path(media.source.local_path).name}"

        try:
            compiled = compile_h3_request(
                route=request.route,
                prompt=prompt,
                duration_seconds=duration,
                media_inputs=request.media_inputs,
                resolve_url=preview_url,
            )
        except H3CapabilityError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return H3CompilePreviewResponse(prompt=prompt, request=compiled)

    def enqueue_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3ProbeResponse:
        with self._lock:
            if not self.h3_config.api_key():
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing a paid H3 probe"
                )
            document, shot, prompt, duration = self._resolve_h3_probe(
                project_id=project_id,
                request=request,
            )
            existing = self.repository.list_tasks_for_shot(project_id, shot.id)
            candidate_index = max((task.candidate_index for task in existing), default=0) + 1
            task, run, evidence = self._new_h3_task_and_run(
                document=document,
                shot_id=shot.id,
                route=request.route,
                duration_seconds=duration,
                prompt=prompt,
                media_inputs=request.media_inputs,
                purpose=request.purpose,
                candidate_index=candidate_index,
                attempt=1,
                retry_of_task_id=None,
            )
            self.repository.save_task(task)
            self.repository.save_provider_run(run)
            self.repository.save_route_evidence(evidence)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return H3ProbeResponse(
                task=task,
                provider_run=run,
                project=self._aggregate(document),
            )

    def list_h3_provider_runs(self, project_id: str) -> H3ProviderRunsResponse:
        self._require_project_document(project_id)
        return H3ProviderRunsResponse(runs=self.repository.list_provider_runs(project_id))

    def list_h3_reconciliation_candidates(self) -> H3ReconciliationCandidatesResponse:
        try:
            tasks = self.h3_worker.list_reconciliation_candidates()
        except H3ProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return H3ReconciliationCandidatesResponse(tasks=tasks)

    def enqueue(self, project_id: str, request: EnqueueRequest) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            available_shot_ids = {shot.id for shot in document.shots}
            requested_shot_ids = request.shot_ids or [shot.id for shot in document.shots]
            unknown = set(requested_shot_ids) - available_shot_ids
            if unknown:
                raise ArtifactNotFoundError(", ".join(sorted(unknown)))
            for shot in document.shots:
                if shot.id not in requested_shot_ids:
                    continue
                for candidate_index in range(1, request.candidate_count + 1):
                    if self._has_current_take_for_candidate(project_id, shot.id, candidate_index):
                        continue
                    behavior = self._resolve_behavior(
                        request.behavior_by_candidate,
                        shot.id,
                        candidate_index,
                    )
                    task = self._new_task(
                        document=document,
                        shot_id=shot.id,
                        operation=request.operation,
                        candidate_index=candidate_index,
                        requested_duration_seconds=shot.duration_seconds,
                        behavior=behavior,
                        attempt=1,
                        retry_of_task_id=None,
                    )
                    self.repository.save_task(task)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return self._aggregate(document)

    def advance_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.advance(task_id)
                if persisted.provider == "minimax"
                else self.worker.advance(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError, H3CapabilityError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def drain(self, project_id: str, max_steps: int) -> WorkerDrainResponse:
        self._require_project_document(project_id)
        processed_steps = self.worker.drain(project_id, max_steps)
        self._refresh_project_stage(project_id)
        return WorkerDrainResponse(
            processed_steps=processed_steps,
            remaining_runnable=self.repository.count_runnable_tasks(
                project_id,
                provider="mock",
            ),
            project=self.get_project(project_id),
        )

    def cancel_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.cancel(task_id)
                if persisted.provider == "minimax"
                else self.worker.cancel(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def retry_task(self, task_id: str) -> GenerationTask:
        with self._lock:
            task = self._require_task(task_id)
            if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                raise WorkflowConflictError(
                    f"Task {task.id} cannot retry from {task.status.value}"
                )
            document = self._require_project_document(task.project_id)
            retry_run: H3ProviderRun | None = None
            retry_evidence: RouteEvidence | None = None
            if task.provider == "minimax":
                run = self.repository.get_provider_run(task.id)
                if run is None:
                    raise WorkflowConflictError(f"H3 provider run for task {task.id} is missing")
                retry, retry_run, retry_evidence = self._new_h3_task_and_run(
                    document=document,
                    shot_id=task.shot_id,
                    route=run.route,
                    duration_seconds=int(task.requested_duration_seconds),
                    prompt=run.prompt,
                    media_inputs=run.media_inputs,
                    purpose=run.purpose,
                    candidate_index=task.candidate_index,
                    attempt=task.attempt + 1,
                    retry_of_task_id=task.id,
                )
            else:
                retry = self._new_task(
                    document=document,
                    shot_id=task.shot_id,
                    operation=task.operation,
                    candidate_index=task.candidate_index,
                    requested_duration_seconds=task.requested_duration_seconds,
                    behavior=MockBehavior.SUCCESS,
                    attempt=task.attempt + 1,
                    retry_of_task_id=task.id,
                )
            self.repository.save_task(retry)
            if retry_run is not None and retry_evidence is not None:
                self.repository.save_provider_run(retry_run)
                self.repository.save_route_evidence(retry_evidence)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return retry

    def reconcile_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        if persisted.provider == "minimax":
            raise WorkflowConflictError(
                "Use explicit MiniMax task reconciliation for an H3 SUBMIT_UNKNOWN task"
            )
        try:
            task = self.worker.reconcile(task_id)
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except InvalidTaskTransition as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return task

    def resolve_h3_submission(
        self,
        task_id: str,
        provider_task_id: str,
    ) -> H3TaskActionResponse:
        try:
            task = self.h3_worker.reconcile(task_id, provider_task_id.strip())
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        run = self.repository.get_provider_run(task.id)
        if run is None:
            raise WorkflowConflictError(f"H3 provider run for task {task.id} is missing")
        return H3TaskActionResponse(
            task=task,
            project=self.get_project(task.project_id),
            provider_run=run,
        )

    def recover_task_output(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.recover_output(task_id)
                if persisted.provider == "minimax"
                else self.worker.recover_output(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def select_take(self, take_id: str) -> ProjectAggregate:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            if take.freshness != Freshness.CURRENT:
                raise WorkflowConflictError("A stale Take cannot be selected for the current timeline")
            if take.qa_status != QaStatus.PASSED:
                raise WorkflowConflictError("Only a QA-passed Take can be selected")
            evidence = self.repository.get_route_evidence_for_take(take_id)
            if evidence is not None and evidence.purpose == EvidencePurpose.CONTRACT_SMOKE:
                raise WorkflowConflictError(
                    "A contract-smoke Take cannot be selected for the production timeline"
                )
            selected = self.repository.select_take(take_id)
            if selected is None:
                raise ArtifactNotFoundError(take_id)
            document = self._require_project_document(take.project_id)
            self._invalidate_current_manifests(document)
            document.project.stage = ProjectStage.REVIEW
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return self._aggregate(document)

    def review_h3_take(
        self,
        take_id: str,
        request: H3TakeReviewRequest,
    ) -> H3TakeReviewResponse:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            if take.provider != "minimax" or take.model != "MiniMax-H3":
                raise WorkflowConflictError("Only a MiniMax-H3 Take uses H3 route review")
            if take.freshness != Freshness.CURRENT:
                raise WorkflowConflictError("A stale H3 Take cannot be reviewed")
            evidence = self.repository.get_route_evidence_for_take(take_id)
            if evidence is None:
                raise WorkflowConflictError(f"Route evidence for Take {take_id} is missing")
            if request.hard_gate_passed and evidence.technical_passed is not True:
                raise WorkflowConflictError(
                    "Technical media gates failed; this Take cannot be marked hard-gate passed"
                )

            now = utc_now()
            status = QaStatus.PASSED if request.hard_gate_passed else QaStatus.REJECTED
            merged_issues = {issue.code: issue for issue in take.qa_report.issues}
            merged_issues.update({issue.code: issue for issue in request.issues})
            take.qa_status = status
            take.qa_report.status = status
            take.qa_report.hard_gate_passed = request.hard_gate_passed
            take.qa_report.score = request.score
            take.qa_report.issues = list(merged_issues.values())
            take.qa_report.reviewer_notes = request.notes
            take.qa_report.reviewed_at = now
            if status != QaStatus.PASSED:
                take.is_selected = False
            self.repository.save_take(take)

            evidence.status = EvidenceStatus.REVIEWED
            evidence.hard_gate_passed = request.hard_gate_passed
            evidence.score = request.score
            evidence.notes = request.notes
            evidence.reviewed_at = now
            self.repository.save_route_evidence(evidence)

            document = self._require_project_document(take.project_id)
            document.project.stage = ProjectStage.REVIEW
            document.project.updated_at = now
            self.repository.save_project(document)
            return H3TakeReviewResponse(
                take=take,
                evidence=evidence,
                project=self._aggregate(document),
            )

    def revise_script_line(
        self,
        project_id: str,
        request: ReviseScriptLineRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            current = self._current_script(document)
            matching_line = next((line for line in current.lines if line.id == request.line_id), None)
            if matching_line is None:
                raise ArtifactNotFoundError(request.line_id)
            current.approval_status = ApprovalStatus.SUPERSEDED
            next_version_number = max(version.version for version in document.script_versions) + 1
            next_lines = [line.model_copy(deep=True) for line in current.lines]
            for line in next_lines:
                if line.id == request.line_id:
                    line.spoken_text = request.spoken_text
                    line.subtitle_text = request.subtitle_text
            now = utc_now()
            next_script = ScriptVersion(
                id=f"{project_id}-script-v{next_version_number}",
                project_id=project_id,
                version=next_version_number,
                source_mode=current.source_mode,
                original_text=current.original_text,
                spoken_text="\n".join(line.spoken_text for line in next_lines),
                subtitle_text="\n".join(line.subtitle_text for line in next_lines),
                lines=next_lines,
                structured_scenes=[scene.model_copy(deep=True) for scene in current.structured_scenes],
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                parent_version_id=current.id,
                change_reason=request.change_reason,
                created_at=now,
            )
            document.script_versions.append(next_script)
            document.project.current_script_version_id = next_script.id
            affected_shot_ids = [
                shot.id for shot in document.shots if request.line_id in shot.dialogue_line_ids
            ]
            self.repository.mark_takes_stale(project_id, affected_shot_ids)
            self._invalidate_current_manifests(document)
            document.project.stage = ProjectStage.PLANNED
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def enqueue_local_rework(
        self,
        project_id: str,
        shot_id: str,
        operation: OperationKind,
        request: LocalReworkRequest,
    ) -> GenerationTask:
        if operation == OperationKind.GENERATE:
            raise WorkflowConflictError("Use the regular enqueue operation for initial generation")
        with self._lock:
            document = self._require_project_document(project_id)
            shot = next((item for item in document.shots if item.id == shot_id), None)
            if shot is None:
                raise ArtifactNotFoundError(shot_id)
            existing = self.repository.list_tasks_for_shot(project_id, shot_id)
            candidate_index = max((task.candidate_index for task in existing), default=0) + 1
            duration = shot.duration_seconds
            if operation == OperationKind.EXTEND:
                duration += request.duration_delta_seconds or 2.0
            elif operation == OperationKind.GAP_FILL:
                duration = request.duration_delta_seconds if request.duration_delta_seconds > 0 else 1.5
            duration = max(0.5, duration)
            task = self._new_task(
                document=document,
                shot_id=shot_id,
                operation=operation,
                candidate_index=candidate_index,
                requested_duration_seconds=duration,
                behavior=request.behavior,
                attempt=1,
                retry_of_task_id=None,
            )
            self.repository.save_task(task)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return task

    def rebuild_manifest(self, project_id: str) -> TimelineManifest:
        with self._lock:
            document = self._require_project_document(project_id)
            current_script = self._current_script(document)
            selected_by_shot: dict[str, Take] = {}
            for take in self.repository.list_takes(project_id):
                if (
                    take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and take.qa_status == QaStatus.PASSED
                ):
                    selected_by_shot[take.shot_id] = take
            missing = [shot.id for shot in document.shots if shot.id not in selected_by_shot]
            if missing:
                raise WorkflowConflictError(
                    "Every shot needs a current QA-passed selected Take before building the timeline: "
                    + ", ".join(missing)
                )
            self._invalidate_current_manifests(document)
            version = max((manifest.version for manifest in document.manifests), default=0) + 1
            now = utc_now()
            video_clips: list[TimelineVideoClip] = []
            audio_clips: list[TimelineAudioClip] = []
            subtitle_cues: list[SubtitleCue] = []
            line_by_id = {line.id: line for line in current_script.lines}
            cursor = 0.0
            for shot in sorted(document.shots, key=lambda item: item.sequence):
                take = selected_by_shot[shot.id]
                video_clips.append(
                    TimelineVideoClip(
                        id=f"video-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        media_path=take.media_path,
                        start_seconds=cursor,
                        source_in_seconds=0.0,
                        duration_seconds=take.duration_seconds,
                        transition_in="none",
                        transition_out=shot.transition_out,
                    )
                )
                audio_clips.append(
                    TimelineAudioClip(
                        id=f"audio-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        media_path=take.media_path,
                        start_seconds=cursor,
                        duration_seconds=take.duration_seconds,
                        gain_db=0.0,
                        role=(
                            "MODEL_NATIVE_H3"
                            if take.provider == "minimax"
                            else "MODEL_NATIVE_MOCK_PLACEHOLDER"
                        ),
                    )
                )
                lines = [line_by_id[line_id] for line_id in shot.dialogue_line_ids if line_id in line_by_id]
                if lines:
                    usable_duration = max(0.5, take.duration_seconds - 1.0)
                    cue_duration = usable_duration / len(lines)
                    for index, line in enumerate(lines):
                        cue_start = cursor + 0.5 + cue_duration * index
                        cue_end = min(cursor + take.duration_seconds - 0.25, cue_start + cue_duration)
                        subtitle_cues.append(
                            SubtitleCue(
                                id=f"subtitle-{shot.id}-{line.id}",
                                script_line_id=line.id,
                                start_seconds=cue_start,
                                end_seconds=cue_end,
                                text=line.subtitle_text,
                                speaker=line.speaker,
                            )
                        )
                cursor += take.duration_seconds
            manifest = TimelineManifest(
                id=f"{project_id}-manifest-v{version}",
                project_id=project_id,
                version=version,
                script_version_id=current_script.id,
                bible_version_id=document.project.current_bible_version_id,
                video_clips=video_clips,
                audio_clips=audio_clips,
                subtitle_cues=subtitle_cues,
                output_spec=OutputSpec(),
                duration_seconds=cursor,
                approval_status=ApprovalStatus.DRAFT,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            document.manifests.append(manifest)
            document.project.current_manifest_id = manifest.id
            document.project.stage = ProjectStage.TIMELINE_READY
            document.project.updated_at = now
            self.repository.save_project(document)
            return manifest

    def export_project(self, project_id: str, rebuild_manifest: bool = True) -> ExportRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            if rebuild_manifest or document.project.current_manifest_id is None:
                manifest = self.rebuild_manifest(project_id)
                document = self._require_project_document(project_id)
            else:
                manifest = self._current_manifest(document)
            export = self.exporter.render(manifest, utc_now())
            if not all(check.passed for check in export.checks):
                raise WorkflowConflictError("Technical export QA failed")
            self.repository.save_export(export)
            document.project.stage = ProjectStage.EXPORTED
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return export

    def auto_draft(self, project_id: str, render_export: bool) -> AutoDraftResponse:
        with self._lock:
            self.enqueue(project_id, EnqueueRequest())
            drain_result = self.drain(project_id, max_steps=5000)
            if drain_result.remaining_runnable > 0:
                raise WorkflowConflictError("Worker did not reach an idle state")
            aggregate = self.get_project(project_id)
            if aggregate.progress.unresolved_tasks > 0:
                raise WorkflowConflictError(
                    "AUTO_DRAFT stopped at actionable task failures; reconcile, recover, or retry them first"
                )
            self.rebuild_manifest(project_id)
            export = self.export_project(project_id, rebuild_manifest=False) if render_export else None
            return AutoDraftResponse(project=self.get_project(project_id), export=export)

    def _resolve_h3_probe(
        self,
        *,
        project_id: str,
        request: H3ProbeRequest,
    ) -> tuple[ProjectDocument, ShotPlan, str, int]:
        document = self._require_project_document(project_id)
        shot = next((item for item in document.shots if item.id == request.shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(request.shot_id)
        prompt = (
            request.prompt_override.strip()
            if request.prompt_override is not None
            else build_h3_prompt(document, shot)
        )
        duration = request.duration_seconds or math.ceil(shot.duration_seconds)
        try:
            validate_h3_probe(
                route=request.route,
                prompt=prompt,
                duration_seconds=duration,
                media_inputs=request.media_inputs,
            )
        except H3CapabilityError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return document, shot, prompt, duration

    def _new_h3_task_and_run(
        self,
        *,
        document: ProjectDocument,
        shot_id: str,
        route: VideoGenerationRoute,
        duration_seconds: int,
        prompt: str,
        media_inputs: list[H3MediaInput],
        purpose: EvidencePurpose,
        candidate_index: int,
        attempt: int,
        retry_of_task_id: str | None,
    ) -> tuple[GenerationTask, H3ProviderRun, RouteEvidence]:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        run_id = f"h3-run-{uuid4().hex}"
        normalized_request = {
            "provider": "minimax",
            "model": "MiniMax-H3",
            "project_id": document.project.id,
            "shot_id": shot_id,
            "route": route.value,
            "resolution": "768P",
            "duration": duration_seconds,
            "prompt": prompt,
            "media_inputs": [
                item.model_dump(mode="json", exclude_none=True) for item in media_inputs
            ],
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "attempt": attempt,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                normalized_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        task = GenerationTask(
            id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            operation=OperationKind.GENERATE,
            candidate_index=candidate_index,
            status=TaskStatus.QUEUED,
            provider="minimax",
            model="MiniMax-H3",
            route=route,
            resolution="768P",
            provider_run_id=run_id,
            request_fingerprint=fingerprint,
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            requested_duration_seconds=float(duration_seconds),
            attempt=attempt,
            retry_of_task_id=retry_of_task_id,
            created_at=now,
            updated_at=now,
        )
        run = H3ProviderRun(
            id=run_id,
            task_id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            route=route,
            purpose=purpose,
            prompt=prompt,
            media_inputs=[item.model_copy(deep=True) for item in media_inputs],
            pricing=self.h3_config.pricing_snapshot(),
            created_at=now,
            updated_at=now,
        )
        evidence = RouteEvidence(
            id=f"evidence-{uuid4().hex}",
            project_id=document.project.id,
            shot_id=shot_id,
            task_id=task_id,
            provider="minimax",
            model="MiniMax-H3",
            route=route,
            resolution="768P",
            purpose=purpose,
            status=EvidenceStatus.AWAITING_OUTPUT,
            created_at=now,
        )
        return task, run, evidence

    def _new_task(
        self,
        *,
        document: ProjectDocument,
        shot_id: str,
        operation: OperationKind,
        candidate_index: int,
        requested_duration_seconds: float,
        behavior: MockBehavior,
        attempt: int,
        retry_of_task_id: str | None,
    ) -> GenerationTask:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        normalized_request = {
            "project_id": document.project.id,
            "shot_id": shot_id,
            "operation": operation.value,
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "requested_duration_seconds": requested_duration_seconds,
            "attempt": attempt,
        }
        fingerprint = hashlib.sha256(
            json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return GenerationTask(
            id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            operation=operation,
            candidate_index=candidate_index,
            status=TaskStatus.QUEUED,
            provider="mock",
            model="deterministic-media-v1",
            resolution="432x768",
            request_fingerprint=fingerprint,
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            requested_duration_seconds=requested_duration_seconds,
            attempt=attempt,
            retry_of_task_id=retry_of_task_id,
            mock_behavior=behavior,
            created_at=now,
            updated_at=now,
        )

    def _aggregate(self, document: ProjectDocument) -> ProjectAggregate:
        tasks = self.repository.list_tasks(document.project.id)
        takes = self.repository.list_takes(document.project.id)
        exports = self.repository.list_exports(document.project.id)
        selected_shots = {
            take.shot_id
            for take in takes
            if take.is_selected
            and take.freshness == Freshness.CURRENT
            and take.qa_status == QaStatus.PASSED
        }
        current_manifest = next(
            (
                manifest
                for manifest in document.manifests
                if manifest.id == document.project.current_manifest_id
                and manifest.freshness == Freshness.CURRENT
            ),
            None,
        )
        export_ready = current_manifest is not None and any(
            export.manifest_id == current_manifest.id for export in exports
        )
        progress = ProgressSummary(
            total_shots=len(document.shots),
            shots_with_selected_take=len(selected_shots),
            total_tasks=len(tasks),
            terminal_tasks=sum(1 for task in tasks if task.status in _TERMINAL_TASK_STATUSES),
            failed_tasks=sum(1 for task in tasks if task.status == TaskStatus.FAILED),
            unresolved_tasks=sum(
                1
                for task in tasks
                if task.status
                not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            ),
            passed_takes=sum(
                1
                for take in takes
                if take.qa_status == QaStatus.PASSED and take.freshness == Freshness.CURRENT
            ),
            stale_takes=sum(1 for take in takes if take.freshness == Freshness.STALE),
            manifest_ready=current_manifest is not None,
            export_ready=export_ready,
        )
        return ProjectAggregate(
            document=document,
            tasks=tasks,
            takes=takes,
            exports=exports,
            route_evidence=self.repository.list_route_evidence(document.project.id),
            progress=progress,
        )

    def _refresh_project_stage(self, project_id: str) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            tasks = self.repository.list_tasks(project_id)
            takes = self.repository.list_takes(project_id)
            if any(task.status in _TERMINAL_TASK_STATUSES for task in tasks) and takes:
                document.project.stage = ProjectStage.REVIEW
            elif tasks:
                document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)

    def _invalidate_current_manifests(self, document: ProjectDocument) -> None:
        for manifest in document.manifests:
            if manifest.freshness == Freshness.CURRENT:
                manifest.freshness = Freshness.STALE
        document.project.current_manifest_id = None

    def _current_script(self, document: ProjectDocument) -> ScriptVersion:
        script = next(
            (
                version
                for version in document.script_versions
                if version.id == document.project.current_script_version_id
            ),
            None,
        )
        if script is None:
            raise WorkflowConflictError("Project current ScriptVersion is missing")
        return script

    def _current_manifest(self, document: ProjectDocument) -> TimelineManifest:
        manifest = next(
            (
                item
                for item in document.manifests
                if item.id == document.project.current_manifest_id
                and item.freshness == Freshness.CURRENT
            ),
            None,
        )
        if manifest is None:
            raise WorkflowConflictError("Project has no current TimelineManifest")
        return manifest

    def _require_project_document(self, project_id: str) -> ProjectDocument:
        document = self.repository.get_project(project_id)
        if document is None:
            raise ProjectNotFoundError(project_id)
        return document

    def _require_task(self, task_id: str) -> GenerationTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ArtifactNotFoundError(task_id)
        return task

    def _has_current_take_for_candidate(
        self,
        project_id: str,
        shot_id: str,
        candidate_index: int,
    ) -> bool:
        return any(
            take.candidate_index == candidate_index and take.freshness == Freshness.CURRENT
            for take in self.repository.list_takes_for_shot(project_id, shot_id)
        )

    def _resolve_behavior(
        self,
        behavior_by_candidate: dict[str, MockBehavior],
        shot_id: str,
        candidate_index: int,
    ) -> MockBehavior:
        return behavior_by_candidate.get(
            f"{shot_id}:{candidate_index}",
            behavior_by_candidate.get(str(candidate_index), MockBehavior.SUCCESS),
        )
