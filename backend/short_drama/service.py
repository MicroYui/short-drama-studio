"""Application service for the provider-neutral short-drama workflow."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from uuid import uuid4

from short_drama.asset_workbench import (
    AssetProviderConfigurationError,
    AssetWorkbench,
    AssetWorkbenchConflict,
    AssetWorkbenchNotFound,
    ImageProviderFactory,
    default_image_provider_factory,
)
from short_drama.asset_inspection import (
    SemanticAssetReview,
    build_base_asset_prompt,
    build_keyframe_correction_prompt,
    inspect_asset_image,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3ProductionRouteExecuteRequest,
    H3ProductionRouteExecuteResponse,
    H3ProductionRoutePlan,
    H3ProductionRoutePlanListResponse,
    H3ProductionRoutePlanRequest,
    H3ProductionRoutePlanResponse,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3NotSubmittedResolutionRequest,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
    H3ProviderRun,
    H3ProviderRunsResponse,
    H3ReconciliationCandidatesResponse,
    H3RoutePlanStatus,
    H3RouteAssetPreparation,
    H3RouteAssetPreparationStatus,
    H3ShotRouteAction,
    H3ShotRouteExecutionStatus,
    H3ShotRoutePlan,
    H3ShotRouteReadiness,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.h3_route_planner import (
    RoutePlanningError,
    build_h3_production_route_plan,
    validate_route_plan_freshness,
)
from short_drama.h3_worker import (
    H3ClientFactory,
    H3ProviderConfigurationError,
    PersistentH3Worker,
    default_h3_client_factory,
)
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReviewDecision,
    AssetReviewRequest,
    ImageGenerationTask,
    ImageOperation,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
    ImageTaskCreateRequest,
    ImageTaskListResponse,
    ImageTaskResponse,
    ImageTaskStatus,
)
from short_drama.language_models import (
    LanguagePromptCompileRequest,
    LanguagePromptCompileResponse,
    LanguageProviderConfigResponse,
    LanguageProviderConfigUpdate,
)
from short_drama.media import MediaGenerationError, MockMediaRenderer, TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetVersion,
    AssetMediaType,
    AutoDraftResponse,
    AutonomousExecutionTarget,
    AutonomousDraftCreateRequest,
    AutonomousDraftResponse,
    AutonomousRunAdvanceRequest,
    AutonomousRunRecord,
    AutonomousRunStatus,
    AutonomousRunStep,
    CreateProjectRequest,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    EnqueueRequest,
    EvidencePurpose,
    EvidenceStatus,
    ExecutionPlanEntry,
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
    ReferencePack,
    ReferencePackEntry,
    ReferenceBinding,
    ReferencePlanEntry,
    RouteEvidence,
    RunMode,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReviseScriptLineRequest,
    ScriptVersion,
    SemanticReviewStatus,
    ShotPlan,
    SourceMode,
    SubtitleCue,
    Take,
    TaskStatus,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionRequest,
    VideoInspectionResponse,
    WorkerDrainResponse,
)
from short_drama.production_planner import (
    ProductionPlanningResult,
    build_project_document_from_plan,
    create_production_plan,
    refresh_lightweight_reference_plans,
)
from short_drama.quality_learning import (
    H3CapabilityProfileResponse,
    build_h3_capability_profile,
    known_regression_case,
)
from short_drama.reference_planner import (
    build_director_advice_repair_input,
    DirectorAdviceRequest,
    DirectorAdviceResponse,
    DirectorLoopResponse,
    DirectorRepairDecisionRequest,
    KeyframeBriefCompileRequest,
    KeyframeBriefCompileResponse,
    ReferencePackEntryCreateRequest,
    ReferencePackEntryMutationResponse,
    ReferencePlanPreviewRequest,
    ReferencePlanPreviewResponse,
    ShotReferencePlanUpdateRequest,
    build_director_advice_input,
    compile_keyframe_brief,
    parse_director_advice,
    preview_reference_plan,
    role_is_compatible,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.h3_language_prompt import (
    LanguagePromptValidationError,
    build_language_prompt_input,
    validate_language_prompt,
)
from short_drama.providers.image_config import ImageProviderConfigStore
from short_drama.providers.image_provider import ImageProviderError
from short_drama.providers.language_config import LanguageProviderConfigStore
from short_drama.providers.minimax_h3_client import H3ClientError
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    compile_h3_request,
    validate_h3_probe,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderClientFactory,
    LanguageProviderError,
    default_language_provider_factory,
    is_loopback_openai_url,
)
from short_drama.repository import ShortDramaRepository
from short_drama.video_inspection import VideoInspector
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
_MAX_AUTONOMOUS_IMAGE_ATTEMPTS = 3
_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS = 3


def _order_keyframe_reference_versions(
    bound: list[tuple[Asset, AssetVersion]],
    reference_plan: ReferencePlanEntry,
    preferred_version_ids: list[str],
) -> list[tuple[Asset, AssetVersion]]:
    """Keep the Agent/reference-plan priority when choosing an image-edit anchor."""

    by_version_id = {version.id: (asset, version) for asset, version in bound}
    by_asset_id = {asset.id: (asset, version) for asset, version in bound}
    ordered: list[tuple[Asset, AssetVersion]] = []
    seen: set[str] = set()

    def append(candidate: tuple[Asset, AssetVersion] | None) -> None:
        if candidate is None or candidate[1].id in seen:
            return
        ordered.append(candidate)
        seen.add(candidate[1].id)

    for version_id in preferred_version_ids:
        append(by_version_id.get(version_id))
    for requirement in reference_plan.requirements:
        append(by_asset_id.get(requirement.subject_asset_id))
    for binding in reference_plan.bindings:
        append(by_version_id.get(binding.target_id))
    for candidate in bound:
        append(candidate)
    return ordered


def _filter_generation_reference_versions(
    bound: list[tuple[Asset, AssetVersion]],
    reference_plan: ReferencePlanEntry,
) -> list[tuple[Asset, AssetVersion]]:
    """Exclude continuity-only assets that the Agent did not plan to show in this shot."""

    subject_ids = {
        requirement.subject_asset_id for requirement in reference_plan.requirements
    }
    subject_ids.update(beat.actor_asset_id for beat in reference_plan.action_beats)
    for interaction in reference_plan.interactions:
        subject_ids.add(interaction.actor_asset_id)
        subject_ids.add(interaction.target_asset_id)
    if not subject_ids:
        return bound
    filtered = [item for item in bound if item[0].id in subject_ids]
    return filtered or bound


class ShortDramaService:
    """Owns the SQLite truth source, worker, Mock adapter, and manifest exporter."""

    def __init__(
        self,
        *,
        database_path: Path,
        workspace_root: Path,
        h3_client_factory: H3ClientFactory | None = None,
        image_provider_factory: ImageProviderFactory | None = None,
        language_provider_factory: LanguageProviderClientFactory | None = None,
    ) -> None:
        self.repository = ShortDramaRepository(database_path)
        self.workspace_root = workspace_root
        self.media_renderer = MockMediaRenderer(workspace_root)
        self.exporter = TimelineExporter(workspace_root)
        self.video_inspector = VideoInspector(workspace_root)
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
        self.image_config = ImageProviderConfigStore(
            database_path.parent / "short-drama-image-provider-settings.json"
        )
        self.asset_workbench = AssetWorkbench(
            repository=self.repository,
            workspace_root=workspace_root,
            config_store=self.image_config,
            provider_factory=image_provider_factory or default_image_provider_factory,
        )
        self.language_config = LanguageProviderConfigStore(
            database_path.parent / "short-drama-language-provider-settings.json"
        )
        self.language_provider_factory = (
            language_provider_factory or default_language_provider_factory
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
        project, _planning = self._create_planned_project(request)
        self._import_creative_input_images(project.document.project.id, request)
        return self.get_project(project.document.project.id)

    def create_autonomous_draft(
        self,
        request: AutonomousDraftCreateRequest,
    ) -> AutonomousDraftResponse:
        create_request = CreateProjectRequest.model_validate(
            request.model_dump(exclude={"render_export", "execution_target"})
        )
        project, planning = self._create_planned_project(create_request)
        project_id = project.document.project.id
        now = utc_now()
        run = AutonomousRunRecord(
            id=f"autonomous-run-{uuid4().hex}",
            project_id=project_id,
            status=AutonomousRunStatus.RUNNING,
            current_step=AutonomousRunStep.IMPORT_INPUT_ASSETS,
            completed_steps=[
                AutonomousRunStep.NORMALIZE_INPUT,
                AutonomousRunStep.PLAN,
            ],
            planner=planning.planner,
            execution_target=request.execution_target,
            render_export=request.render_export,
            warnings=list(planning.warnings),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            document = self._require_project_document(project_id)
            document.autonomous_runs.append(run)
            self.repository.save_project(document)
        try:
            self._import_creative_input_images(project_id, create_request)
            self._advance_autonomous_run(
                project_id,
                run.id,
                completed_step=AutonomousRunStep.IMPORT_INPUT_ASSETS,
                next_step=(
                    AutonomousRunStep.GENERATE
                    if request.execution_target == AutonomousExecutionTarget.MOCK
                    else AutonomousRunStep.PREPARE_ASSETS
                ),
            )
            if request.execution_target == AutonomousExecutionTarget.REAL_H3:
                return self.advance_autonomous_run(
                    project_id,
                    run.id,
                    AutonomousRunAdvanceRequest(max_actions=1),
                )
            result = self._execute_auto_draft(
                project_id,
                render_export=request.render_export,
                autonomous_run_id=run.id,
            )
        except (WorkflowConflictError, ProviderOperationError, MediaGenerationError) as exc:
            self._finish_autonomous_run(
                project_id,
                run.id,
                status=AutonomousRunStatus.NEEDS_ATTENTION,
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            self._finish_autonomous_run(
                project_id,
                run.id,
                status=AutonomousRunStatus.FAILED,
                error_message=str(exc),
            )
            raise
        completed = self._require_autonomous_run(project_id, run.id)
        return AutonomousDraftResponse(
            project=result.project,
            export=result.export,
            run=completed,
        )

    def _create_planned_project(
        self,
        request: CreateProjectRequest,
    ) -> tuple[ProjectAggregate, ProductionPlanningResult]:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        client = (
            self.language_provider_factory(api_key, base_url)
            if api_key or is_loopback_openai_url(base_url)
            else None
        )
        planning = create_production_plan(request, client)
        project_id = f"project-{uuid4().hex[:12]}"
        created_at = utc_now()
        document = build_project_document_from_plan(
            project_id=project_id,
            request=request,
            planning=planning,
            created_at=created_at,
        )
        with self._lock:
            self.repository.save_project(document)
            return self._aggregate(document), planning

    def _import_creative_input_images(
        self,
        project_id: str,
        request: CreateProjectRequest,
    ) -> None:
        imported_versions: list[tuple[str, str]] = []
        for index, image in enumerate(request.input_images, start=1):
            source_name = (
                Path(image.local_path).stem
                if image.local_path is not None
                else f"网络参考图 {index}"
            )
            created = self.create_asset(
                project_id,
                AssetCreateRequest(
                    kind=AssetKind.REFERENCE,
                    name=f"输入视觉种子 {index} · {source_name}"[:120],
                    descriptor=image.descriptor,
                ),
            )
            if image.local_path is not None:
                imported = self.import_local_asset_version(
                    project_id,
                    created.asset.id,
                    AssetImportLocalRequest(
                        local_path=image.local_path,
                        descriptor=image.descriptor,
                    ),
                )
            else:
                assert image.external_url is not None
                imported = self.import_url_asset_version(
                    project_id,
                    created.asset.id,
                    AssetImportUrlRequest(
                        external_url=image.external_url,
                        descriptor=image.descriptor,
                        media_type=AssetMediaType.IMAGE,
                    ),
                )
            self.review_asset_version(
                project_id,
                created.asset.id,
                imported.version.id,
                AssetReviewRequest(
                    decision=AssetReviewDecision.APPROVED,
                    notes="用户直接提供的输入图片，自动草稿可作为视觉种子使用。",
                ),
            )
            imported_versions.append((imported.version.id, image.descriptor))
        if imported_versions:
            with self._lock:
                document = self._require_project_document(project_id)
                requirement = (
                    "REQUIRED"
                    if request.source_mode == SourceMode.IMAGE_SEEDED
                    else "OPTIONAL"
                )
                for plan in document.reference_plan:
                    known_targets = {binding.target_id for binding in plan.bindings}
                    for version_id, descriptor in imported_versions:
                        if version_id in known_targets:
                            continue
                        plan.bindings.append(
                            ReferenceBinding(
                                kind=AssetKind.REFERENCE.value,
                                target_id=version_id,
                                purpose=f"用户提供的跨镜视觉种子：{descriptor}",
                                requirement=requirement,
                            )
                        )
                document.project.updated_at = utc_now()
                self.repository.save_project(document)

    def _require_autonomous_run(
        self,
        project_id: str,
        run_id: str,
    ) -> AutonomousRunRecord:
        document = self._require_project_document(project_id)
        run = next((item for item in document.autonomous_runs if item.id == run_id), None)
        if run is None:
            raise ArtifactNotFoundError(run_id)
        return run

    def _advance_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        *,
        completed_step: AutonomousRunStep,
        next_step: AutonomousRunStep,
    ) -> AutonomousRunRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next((item for item in document.autonomous_runs if item.id == run_id), None)
            if run is None:
                raise ArtifactNotFoundError(run_id)
            if completed_step not in run.completed_steps:
                run.completed_steps.append(completed_step)
            run.status = AutonomousRunStatus.RUNNING
            run.current_step = next_step
            run.error_message = None
            run.waiting_reason = None
            run.updated_at = utc_now()
            self.repository.save_project(document)
            return run

    def _finish_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        *,
        status: AutonomousRunStatus,
        error_message: str | None = None,
        export_id: str | None = None,
        completed_steps: list[AutonomousRunStep] | None = None,
    ) -> AutonomousRunRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next((item for item in document.autonomous_runs if item.id == run_id), None)
            if run is None:
                raise ArtifactNotFoundError(run_id)
            for step in completed_steps or []:
                if step not in run.completed_steps:
                    run.completed_steps.append(step)
            run.status = status
            run.error_message = error_message
            run.waiting_reason = error_message if status == AutonomousRunStatus.NEEDS_ATTENTION else None
            run.export_id = export_id or run.export_id
            now = utc_now()
            run.updated_at = now
            if status == AutonomousRunStatus.COMPLETE:
                run.current_step = AutonomousRunStep.COMPLETE
                if AutonomousRunStep.COMPLETE not in run.completed_steps:
                    run.completed_steps.append(AutonomousRunStep.COMPLETE)
                run.completed_at = now
            self.repository.save_project(document)
            return run

    def advance_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        request: AutonomousRunAdvanceRequest,
    ) -> AutonomousDraftResponse:
        run = self._require_autonomous_run(project_id, run_id)
        if run.execution_target != AutonomousExecutionTarget.REAL_H3:
            raise WorkflowConflictError("Only a REAL_H3 autonomous run uses resumable advance")
        if run.status != AutonomousRunStatus.COMPLETE:
            with self._lock:
                document = self._require_project_document(project_id)
                persisted = next(
                    item for item in document.autonomous_runs if item.id == run_id
                )
                persisted.status = AutonomousRunStatus.RUNNING
                persisted.error_message = None
                persisted.waiting_reason = None
                persisted.updated_at = utc_now()
                self.repository.save_project(document)
        try:
            for _ in range(request.max_actions):
                progressed, waiting = self._advance_real_autonomous_run_once(
                    project_id,
                    run_id,
                )
                run = self._require_autonomous_run(project_id, run_id)
                if run.status in {
                    AutonomousRunStatus.COMPLETE,
                    AutonomousRunStatus.NEEDS_ATTENTION,
                    AutonomousRunStatus.FAILED,
                }:
                    break
                if waiting or not progressed:
                    break
        except (
            WorkflowConflictError,
            ProviderOperationError,
            MediaGenerationError,
        ) as exc:
            run = self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.NEEDS_ATTENTION,
                error_message=str(exc),
            )
        except Exception as exc:
            run = self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.FAILED,
                error_message=str(exc),
            )
            raise
        else:
            run = self._require_autonomous_run(project_id, run_id)
        export = next(
            (
                item
                for item in self.repository.list_exports(project_id)
                if item.id == run.export_id
            ),
            None,
        )
        return AutonomousDraftResponse(
            project=self.get_project(project_id),
            export=export,
            run=run,
        )

    def _advance_real_autonomous_run_once(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.current_step == AutonomousRunStep.PREPARE_ASSETS:
            return self._advance_base_asset_preparation(project_id, run_id)
        if run.current_step == AutonomousRunStep.PLAN_ROUTES:
            response = self.compile_h3_production_route_plan(
                project_id,
                H3ProductionRoutePlanRequest(
                    use_language_agent=True,
                    candidate_count=2,
                ),
            )
            needs_asset = any(
                decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
                for decision in response.plan.decisions
            )
            with self._lock:
                document = self._require_project_document(project_id)
                persisted = next(
                    item for item in document.autonomous_runs if item.id == run_id
                )
                persisted.route_plan_id = response.plan.id
                if AutonomousRunStep.PLAN_ROUTES not in persisted.completed_steps:
                    persisted.completed_steps.append(AutonomousRunStep.PLAN_ROUTES)
                persisted.current_step = (
                    AutonomousRunStep.PREPARE_SHOT_ASSETS
                    if needs_asset
                    else AutonomousRunStep.GENERATE
                )
                persisted.waiting_reason = None
                persisted.updated_at = utc_now()
                self.repository.save_project(document)
            return True, False
        if run.current_step == AutonomousRunStep.PREPARE_SHOT_ASSETS:
            return self._advance_shot_asset_preparation(project_id, run_id)
        if run.current_step == AutonomousRunStep.GENERATE:
            return self._advance_real_video_generation(project_id, run_id)
        if run.current_step == AutonomousRunStep.QA_AND_REPAIR:
            return self._advance_real_qa_and_repair(project_id, run_id)
        if run.current_step == AutonomousRunStep.TIMELINE:
            self.rebuild_manifest(project_id)
            if run.render_export:
                self._advance_autonomous_run(
                    project_id,
                    run_id,
                    completed_step=AutonomousRunStep.TIMELINE,
                    next_step=AutonomousRunStep.EXPORT,
                )
            else:
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.COMPLETE,
                    completed_steps=[AutonomousRunStep.TIMELINE],
                )
            return True, False
        if run.current_step == AutonomousRunStep.EXPORT:
            export = self.export_project(project_id, rebuild_manifest=False)
            self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.COMPLETE,
                export_id=export.id,
                completed_steps=[AutonomousRunStep.EXPORT],
            )
            return True, False
        if run.current_step == AutonomousRunStep.COMPLETE:
            return False, False
        raise WorkflowConflictError(
            f"REAL_H3 run cannot advance from {run.current_step.value}"
        )

    def _advance_base_asset_preparation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        document = self._require_project_document(project_id)
        core_assets = [
            asset
            for asset in document.assets
            if asset.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
        ]
        for asset in core_assets:
            approved = self._approved_image_version(asset)
            if approved is not None:
                if self._rebind_asset_references(document, asset, approved):
                    document.project.updated_at = utc_now()
                    self.repository.save_project(document)
                    return True, False
                continue
            tasks = self.asset_workbench.list_image_tasks(project_id, asset.id)
            latest = max(tasks, key=lambda item: item.created_at, default=None)
            if latest is None:
                self._enqueue_base_asset_image(document, asset, None)
                return True, False
            if latest.status in {ImageTaskStatus.QUEUED, ImageTaskStatus.SUBMITTING}:
                self.advance_image_task(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.OUTPUT_MISSING:
                self.recover_image_task_output(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.SUBMIT_UNKNOWN:
                raise WorkflowConflictError(
                    f"Image task {latest.id} has an unknown submission result; reconcile it before continuing"
                )
            if latest.status == ImageTaskStatus.SUCCEEDED:
                version = self._find_asset_version(
                    self._require_project_document(project_id),
                    latest.output_version_id,
                )[1]
                if version.approval_status == ApprovalStatus.DRAFT:
                    _review, approved_by_agent = self._review_generated_asset(
                        project_id=project_id,
                        asset_id=asset.id,
                        version_id=version.id,
                    )
                    if approved_by_agent:
                        refreshed = self._require_project_document(project_id)
                        refreshed_asset = self._require_asset_in_document(refreshed, asset.id)
                        approved_version = self._approved_image_version(refreshed_asset)
                        assert approved_version is not None
                        self._rebind_asset_references(
                            refreshed,
                            refreshed_asset,
                            approved_version,
                        )
                        refreshed.project.updated_at = utc_now()
                        self.repository.save_project(refreshed)
                    return True, False
                if version.approval_status == ApprovalStatus.REJECTED:
                    if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                        raise WorkflowConflictError(
                            f"Automatic visual asset retries exhausted for {asset.name}"
                        )
                    self._enqueue_base_asset_image(document, asset, latest)
                    return True, False
            if latest.status in {ImageTaskStatus.FAILED, ImageTaskStatus.CANCELLED}:
                if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                    raise WorkflowConflictError(
                        f"Image task retries exhausted for {asset.name}: {latest.error_message or latest.status.value}"
                    )
                self._enqueue_base_asset_image(document, asset, latest)
                return True, False
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.PREPARE_ASSETS,
            next_step=AutonomousRunStep.PLAN_ROUTES,
        )
        return True, False

    def _enqueue_base_asset_image(
        self,
        document: ProjectDocument,
        asset: Asset,
        previous: ImageGenerationTask | None,
    ) -> ImageGenerationTask:
        prompt = build_base_asset_prompt(document, asset)
        if previous is not None:
            previous_version = self._find_asset_version(
                self._require_project_document(document.project.id),
                previous.output_version_id,
            )[1]
            if previous_version.review_notes:
                prompt += (
                    "\n\n上一候选未通过制片检查。仅修复以下问题，不改变资产身份："
                    + previous_version.review_notes
                )
        descriptor = next(
            (
                version.descriptor
                for version in asset.versions
                if version.media_type == AssetMediaType.TEXT_REFERENCE
            ),
            asset.versions[-1].descriptor,
        )
        try:
            return self.asset_workbench.enqueue_image_task(
                document.project.id,
                asset.id,
                ImageTaskCreateRequest(
                    operation=ImageOperation.GENERATE,
                    prompt=prompt,
                    descriptor=descriptor,
                ),
                attempt=(previous.attempt + 1 if previous is not None else 1),
                retry_of_task_id=previous.id if previous is not None else None,
            )
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(
                "请先在资产页配置 GPT Image 2 的 URL 和 API Key，再继续真人自动草稿。"
            ) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def _advance_shot_asset_preparation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        document = self._require_project_document(project_id)
        for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
            if decision.readiness != H3ShotRouteReadiness.NEEDS_ASSET:
                continue
            shot = self._require_shot_in_document(document, decision.shot_id)
            for preparation in decision.asset_preparations:
                if preparation.approved_version_id is not None:
                    continue
                if preparation.asset_id is None:
                    self._create_keyframe_asset(document, plan, decision, preparation)
                    return True, False
                tasks = self.asset_workbench.list_image_tasks(
                    project_id,
                    preparation.asset_id,
                )
                latest = max(tasks, key=lambda item: item.created_at, default=None)
                if latest is None:
                    task = self._enqueue_keyframe_image(
                        document,
                        shot,
                        preparation,
                        None,
                    )
                    preparation.image_task_ids.append(task.id)
                    preparation.status = H3RouteAssetPreparationStatus.GENERATING
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_plan(plan)
                    return True, False
                if latest.status in {ImageTaskStatus.QUEUED, ImageTaskStatus.SUBMITTING}:
                    self.advance_image_task(latest.id)
                    return True, False
                if latest.status == ImageTaskStatus.OUTPUT_MISSING:
                    self.recover_image_task_output(latest.id)
                    return True, False
                if latest.status == ImageTaskStatus.SUBMIT_UNKNOWN:
                    raise WorkflowConflictError(
                        f"Keyframe task {latest.id} has an unknown submission result"
                    )
                if latest.status == ImageTaskStatus.SUCCEEDED:
                    version = self._find_asset_version(
                        self._require_project_document(project_id),
                        latest.output_version_id,
                    )[1]
                    if version.approval_status == ApprovalStatus.DRAFT:
                        review, approved_by_agent = self._review_generated_asset(
                            project_id=project_id,
                            asset_id=preparation.asset_id,
                            version_id=version.id,
                            shot=shot,
                            preparation=preparation,
                        )
                        preparation.review_summary = review.summary
                        if approved_by_agent:
                            preparation.status = H3RouteAssetPreparationStatus.READY
                            preparation.approved_version_id = version.id
                            media_input = self._h3_image_input(
                                self._require_project_document(project_id),
                                version.id,
                                preparation.role,
                            )
                            decision.media_inputs = [
                                item
                                for item in decision.media_inputs
                                if item.role != preparation.role
                            ] + [media_input]
                            decision.missing_inputs = [
                                item
                                for item in decision.missing_inputs
                                if item != preparation.role.value
                            ]
                            decision.warnings = [
                                item
                                for item in decision.warnings
                                if "needs an approved start frame" not in item
                            ]
                        else:
                            preparation.status = H3RouteAssetPreparationStatus.REJECTED
                        plan.updated_at = utc_now()
                        self.repository.save_h3_route_plan(plan)
                        return True, False
                    if version.approval_status == ApprovalStatus.REJECTED:
                        if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                            if self._fallback_failed_keyframe_preparation(
                                document=self._require_project_document(project_id),
                                plan=plan,
                                decision=decision,
                                failed_preparation=preparation,
                                reason=(
                                    f"Automatic keyframe review rejected {latest.attempt} "
                                    "candidate(s)."
                                ),
                            ):
                                return True, False
                            raise WorkflowConflictError(
                                f"Automatic keyframe retries exhausted for shot {shot.sequence}"
                            )
                        task = self._enqueue_keyframe_image(
                            document,
                            shot,
                            preparation,
                            latest,
                        )
                        preparation.image_task_ids.append(task.id)
                        preparation.status = H3RouteAssetPreparationStatus.GENERATING
                        plan.updated_at = utc_now()
                        self.repository.save_h3_route_plan(plan)
                        return True, False
                if latest.status in {ImageTaskStatus.FAILED, ImageTaskStatus.CANCELLED}:
                    if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                        if self._fallback_failed_keyframe_preparation(
                            document=self._require_project_document(project_id),
                            plan=plan,
                            decision=decision,
                            failed_preparation=preparation,
                            reason=(
                                f"Keyframe provider task failed or was cancelled "
                                f"{latest.attempt} time(s)."
                            ),
                        ):
                            return True, False
                        raise WorkflowConflictError(
                            f"Keyframe task retries exhausted for shot {shot.sequence}"
                        )
                    task = self._enqueue_keyframe_image(
                        document,
                        shot,
                        preparation,
                        latest,
                    )
                    preparation.image_task_ids.append(task.id)
                    preparation.status = H3RouteAssetPreparationStatus.GENERATING
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_plan(plan)
                    return True, False
            if all(
                item.approved_version_id is not None
                for item in decision.asset_preparations
            ):
                decision.readiness = (
                    H3ShotRouteReadiness.READY_WITH_WARNINGS
                    if decision.warnings
                    else H3ShotRouteReadiness.READY
                )
                plan.updated_at = utc_now()
                self.repository.save_h3_route_plan(plan)
                return True, False
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.PREPARE_SHOT_ASSETS,
            next_step=AutonomousRunStep.GENERATE,
        )
        plan.status = H3RoutePlanStatus.READY
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)
        return True, False

    def _fallback_failed_keyframe_preparation(
        self,
        *,
        document: ProjectDocument,
        plan: H3ProductionRoutePlan,
        decision: H3ShotRoutePlan,
        failed_preparation: H3RouteAssetPreparation,
        reason: str,
    ) -> bool:
        reference_plan = self._require_reference_plan(document, decision.shot_id)
        bound = _filter_generation_reference_versions(
            self._approved_bound_image_versions(document, decision.shot_id),
            reference_plan,
        )
        media_inputs = [
            self._h3_image_input(
                document,
                version.id,
                H3MediaRole.REFERENCE_IMAGE,
            )
            for _asset, version in bound[:9]
        ]
        if not media_inputs:
            return False
        try:
            validate_h3_probe(
                route=VideoGenerationRoute.REFERENCE,
                prompt=decision.prompt,
                duration_seconds=decision.duration_seconds,
                media_inputs=media_inputs,
            )
        except H3CapabilityError:
            return False

        failed_preparation.status = H3RouteAssetPreparationStatus.REJECTED
        fallback_warning = (
            f"{reason} The advisory keyframe route was replaced with approved visual "
            "references so AUTO_DRAFT can continue; the user may override this route."
        )
        failed_preparation.review_summary = " ".join(
            item
            for item in [failed_preparation.review_summary, fallback_warning]
            if item
        )
        decision.route = VideoGenerationRoute.REFERENCE
        decision.media_inputs = media_inputs
        decision.missing_inputs = []
        decision.readiness = H3ShotRouteReadiness.READY_WITH_WARNINGS
        decision.rationale = (
            "Shot-specific keyframe preparation did not converge within the bounded automatic "
            "retries. Continue with approved references for subjects the reference plan expects "
            "to be visible, while retaining stricter continuity QA; this is an overridable "
            "Director fallback."
        )
        decision.warnings = list(
            dict.fromkeys(
                [
                    *(
                        warning
                        for warning in decision.warnings
                        if "needs an approved start frame" not in warning.lower()
                        and "起始帧" not in warning
                    ),
                    fallback_warning,
                ]
            )
        )
        plan.status = (
            H3RoutePlanStatus.READY
            if all(
                item.readiness
                in {
                    H3ShotRouteReadiness.READY,
                    H3ShotRouteReadiness.READY_WITH_WARNINGS,
                }
                for item in plan.decisions
            )
            else H3RoutePlanStatus.PARTIAL
        )
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)
        return True

    def _create_keyframe_asset(
        self,
        document: ProjectDocument,
        plan: H3ProductionRoutePlan,
        decision: H3ShotRoutePlan,
        preparation: H3RouteAssetPreparation,
    ) -> None:
        reference_plan = self._require_reference_plan(document, decision.shot_id)
        bound = _filter_generation_reference_versions(
            self._approved_bound_image_versions(document, decision.shot_id),
            reference_plan,
        )
        if not bound:
            raise WorkflowConflictError(
                f"Shot {decision.shot_sequence} has no approved visual base for keyframe editing"
            )
        ordered = _order_keyframe_reference_versions(
            bound,
            reference_plan,
            preparation.input_version_ids,
        )
        primary_asset, primary_version = ordered[0]
        preparation.input_version_ids = [
            version.id for _asset, version in ordered[:16]
        ]
        created = self.create_asset(
            document.project.id,
            AssetCreateRequest(
                kind=AssetKind.REFERENCE,
                name=preparation.name,
                descriptor=preparation.descriptor,
            ),
        )
        if primary_version.local_path is not None:
            _seeded = self.import_local_asset_version(
                document.project.id,
                created.asset.id,
                AssetImportLocalRequest(
                    local_path=primary_version.local_path,
                    descriptor=(
                        f"镜头关键帧构图父图，来源于 {primary_asset.name} 的已批准版本"
                    ),
                ),
            )
        elif primary_version.external_url is not None:
            _seeded = self.import_url_asset_version(
                document.project.id,
                created.asset.id,
                AssetImportUrlRequest(
                    external_url=primary_version.external_url,
                    descriptor=(
                        f"镜头关键帧构图父图，来源于 {primary_asset.name} 的已批准版本"
                    ),
                    media_type=AssetMediaType.IMAGE,
                ),
            )
        else:
            raise WorkflowConflictError("Approved keyframe base has no usable media")
        preparation.asset_id = created.asset.id
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)

    def _enqueue_keyframe_image(
        self,
        document: ProjectDocument,
        shot: ShotPlan,
        preparation: H3RouteAssetPreparation,
        previous: ImageGenerationTask | None,
    ) -> ImageGenerationTask:
        if preparation.asset_id is None:
            raise WorkflowConflictError("Keyframe preparation has no target asset")
        refreshed = self._require_project_document(document.project.id)
        target = self._require_asset_in_document(refreshed, preparation.asset_id)
        parent = next(
            (
                item
                for item in reversed(target.versions)
                if item.media_type == AssetMediaType.IMAGE
                and item.image_task_id is None
            ),
            None,
        )
        if parent is None:
            raise WorkflowConflictError("Keyframe asset has no visual parent")
        reference_plan = self._require_reference_plan(refreshed, shot.id)
        bound = _filter_generation_reference_versions(
            self._approved_bound_image_versions(refreshed, shot.id),
            reference_plan,
        )
        ordered = _order_keyframe_reference_versions(
            bound,
            reference_plan,
            preparation.input_version_ids,
        )
        if not ordered:
            raise WorkflowConflictError("Keyframe asset has no approved visual references")
        primary_asset, primary_version = ordered[0]
        input_ids = list(
            dict.fromkeys(
                [
                    parent.id,
                    *(
                        version.id
                        for _asset, version in ordered
                        if version.id != primary_version.id
                    ),
                ]
            )
        )[:16]
        reference_lines = [
            (
                "Image 1: PRIMARY EDIT INPUT copied exactly from the approved "
                f"{primary_asset.kind.value} reference '{primary_asset.name}'. "
                + self._keyframe_primary_reference_lock(primary_asset.kind)
            ),
            *[
                (
                    f"Image {index}: approved {asset.kind.value} reference "
                    f"'{asset.name}'; use it only to preserve that subject's visible "
                    "identity, appearance, geometry, or topology where requested by the shot."
                )
                for index, (asset, _version) in enumerate(
                    (
                        item
                        for item in ordered
                        if item[1].id != primary_version.id
                    ),
                    start=2,
                )
            ],
        ]
        prompt = (
            build_keyframe_correction_prompt(
                preparation,
                preparation.review_summary if previous is not None else None,
            )
            + "\n\nACTUAL EDIT INPUT MAP:\n"
            + "\n".join(reference_lines)
            + "\nDo not blend different actors or replace the primary subject with a face "
            "from another reference. Reference-sheet or turnaround layouts must be transformed "
            "into the single cinematic frame requested above, never reproduced as a collage."
        )
        try:
            return self.asset_workbench.enqueue_image_task(
                refreshed.project.id,
                target.id,
                ImageTaskCreateRequest(
                    operation=ImageOperation.EDIT,
                    prompt=prompt,
                    descriptor=preparation.descriptor,
                    parent_version_id=parent.id,
                    input_version_ids=input_ids,
                    primary_input_version_id=parent.id,
                ),
                attempt=(previous.attempt + 1 if previous is not None else 1),
                retry_of_task_id=previous.id if previous is not None else None,
            )
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(
                "请先在资产页配置 GPT Image 2 的 URL 和 API Key，再继续关键帧准备。"
            ) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def _review_generated_asset(
        self,
        *,
        project_id: str,
        asset_id: str,
        version_id: str,
        shot: ShotPlan | None = None,
        preparation: H3RouteAssetPreparation | None = None,
    ) -> tuple[SemanticAssetReview, bool]:
        document = self._require_project_document(project_id)
        asset = self._require_asset_in_document(document, asset_id)
        version = self._find_asset_version(document, version_id)[1]
        client = self._require_language_client()
        canonical_references: list[tuple[Asset, AssetVersion]] = []
        if shot is not None:
            canonical_references = _order_keyframe_reference_versions(
                self._approved_bound_image_versions(document, shot.id),
                self._require_reference_plan(document, shot.id),
                preparation.input_version_ids if preparation is not None else [],
            )
        try:
            review = inspect_asset_image(
                document=document,
                asset=asset,
                version=version,
                client=client,
                shot=shot,
                preparation=preparation,
                canonical_references=canonical_references,
            )
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        severe = any(
            issue.severity.upper() in {"ERROR", "HIGH", "MAJOR", "CRITICAL", "FATAL"}
            for issue in review.issues
        )
        approved = review.recommended and review.score >= 3.5 and not severe
        issue_text = "; ".join(
            f"{item.code}:{item.message}" for item in review.issues
        )
        notes = (
            f"AUTO_DRAFT GPT-5.6-sol visual review score={review.score:.1f}; "
            f"{review.summary}"
            + (f"; issues={issue_text}" if issue_text else "")
        )
        self.review_asset_version(
            project_id,
            asset_id,
            version_id,
            AssetReviewRequest(
                decision=(
                    AssetReviewDecision.APPROVED
                    if approved
                    else AssetReviewDecision.REJECTED
                ),
                notes=notes,
            ),
        )
        return review, approved

    @staticmethod
    def _keyframe_primary_reference_lock(kind: AssetKind) -> str:
        if kind == AssetKind.CHARACTER:
            return (
                "Preserve the exact face identity, hair, wardrobe, and body appearance; "
                "change only pose, crop, camera, and scene composition required by the shot."
            )
        if kind == AssetKind.LOCATION:
            return (
                "Preserve the location's topology, fixed anchors, materials, and lighting logic; "
                "change only the camera framing required by the shot."
            )
        if kind == AssetKind.PROP:
            return (
                "Preserve the prop's exact shape, color, component placement, and material; "
                "change only pose and placement required by the shot."
            )
        return "Preserve its approved visual facts while applying only the requested shot change."

    def _advance_real_video_generation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        has_unexecuted = any(
            decision.execution_status == H3ShotRouteExecutionStatus.PLANNED
            for decision in plan.decisions
        )
        if has_unexecuted:
            self.execute_h3_production_route_plan(
                project_id,
                plan.id,
                H3ProductionRouteExecuteRequest(
                    reuse_passed_takes=True,
                    enqueue_new_generations=True,
                    inspect_reused_takes=False,
                    semantic_review=False,
                    rebuild_manifest=False,
                    render_export=False,
                ),
            )
            return True, False
        plan = self.repository.get_h3_route_plan(plan.id) or plan
        all_project_tasks = self.repository.list_tasks(project_id)
        retry_children: dict[str, list[GenerationTask]] = {}
        for task in all_project_tasks:
            if task.retry_of_task_id is not None:
                retry_children.setdefault(task.retry_of_task_id, []).append(task)
        plan_changed = False
        for decision in plan.decisions:
            planned_ids = set(decision.task_ids)
            for parent_id in list(decision.task_ids):
                children = retry_children.get(parent_id, [])
                if not children:
                    continue
                earliest = min(children, key=lambda item: (item.created_at, item.id))
                if earliest.id not in planned_ids:
                    decision.task_ids.append(earliest.id)
                    planned_ids.add(earliest.id)
                    plan_changed = True
        if plan_changed:
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        task_ids = [task_id for item in plan.decisions for task_id in item.task_ids]
        tasks = [self._require_task(task_id) for task_id in task_ids]
        pending_submission = next(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.QUEUED, TaskStatus.SUBMITTING}
            ),
            None,
        )
        if pending_submission is not None:
            self.advance_task(pending_submission.id)
            return True, False

        missing_output = next(
            (task for task in tasks if task.status == TaskStatus.OUTPUT_MISSING),
            None,
        )
        if missing_output is not None:
            self.recover_task_output(missing_output.id)
            return True, False

        unknown_submission = next(
            (task for task in tasks if task.status == TaskStatus.SUBMIT_UNKNOWN),
            None,
        )
        if unknown_submission is not None:
            raise WorkflowConflictError(
                f"H3 task {unknown_submission.id} submission is unknown; reconcile it before continuing"
            )

        failed = next(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                and task.id not in retry_children
            ),
            None,
        )
        if failed is not None:
            if failed.attempt >= 2:
                raise WorkflowConflictError(
                    f"H3 retries exhausted for shot {failed.shot_id}: "
                    f"{failed.error_message or failed.status.value}"
                )
            retry = self.retry_task(failed.id)
            decision = next(
                item for item in plan.decisions if item.shot_id == failed.shot_id
            )
            decision.task_ids.append(retry.id)
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        running = min(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.ACKNOWLEDGED, TaskStatus.RUNNING}
            ),
            key=lambda item: (item.updated_at, item.created_at, item.id),
            default=None,
        )
        if running is not None:
            advanced = self.advance_task(running.id)
            waiting = advanced.status in {
                TaskStatus.ACKNOWLEDGED,
                TaskStatus.RUNNING,
            }
            if waiting:
                self._set_autonomous_waiting(
                    project_id,
                    run_id,
                    "MiniMax-H3 tasks are still running; the desktop will continue polling.",
                )
            return True, waiting
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.GENERATE,
            next_step=AutonomousRunStep.QA_AND_REPAIR,
        )
        return True, False

    def _advance_real_qa_and_repair(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        project = self.get_project(project_id)
        task_ids = {task_id for item in plan.decisions for task_id in item.task_ids}
        pending = sorted(
            (
                take
                for take in project.takes
                if take.task_id in task_ids
                and take.freshness == Freshness.CURRENT
                and take.qa_status == QaStatus.PENDING
            ),
            key=lambda item: next(
                shot.sequence
                for shot in project.document.shots
                if shot.id == item.shot_id
            ),
        )
        if pending:
            take = pending[0]
            decision = next(item for item in plan.decisions if item.shot_id == take.shot_id)
            existing = next(
                (
                    report
                    for report in reversed(project.video_inspections)
                    if report.take_id == take.id
                    and report.semantic_status == SemanticReviewStatus.COMPLETE
                ),
                None,
            )
            if existing is None:
                response = self.inspect_take_video(
                    take.id,
                    VideoInspectionRequest(
                        include_previous_boundary=True,
                        semantic_review=True,
                        max_frames=decision.inspection.max_frames,
                        review_focus=decision.inspection.review_focus,
                    ),
                )
                report = response.report
            else:
                report = existing
            if report.semantic_status != SemanticReviewStatus.COMPLETE:
                refreshed = self.get_project(project_id)
                failed_attempts = sum(
                    1
                    for item in refreshed.video_inspections
                    if item.take_id == take.id
                    and item.semantic_status
                    in {
                        SemanticReviewStatus.FAILED,
                        SemanticReviewStatus.UNAVAILABLE,
                    }
                )
                if failed_attempts < _MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS:
                    self._set_autonomous_waiting(
                        project_id,
                        run_id,
                        (
                            f"Semantic review for Take {take.id} was temporarily unavailable; "
                            f"AUTO_DRAFT will retry ({failed_attempts}/"
                            f"{_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS})."
                        ),
                    )
                    return True, True
                raise WorkflowConflictError(
                    f"Semantic video review remained unavailable for Take {take.id} "
                    f"after {_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS} attempts"
                )
            self._apply_automatic_h3_review(take, report)
            self._select_best_current_takes(project_id)
            decision.execution_status = H3ShotRouteExecutionStatus.GENERATED
            if report.id not in decision.inspection_ids:
                decision.inspection_ids.append(report.id)
            decision.inspection_issue_codes = list(
                dict.fromkeys(
                    [
                        *decision.inspection_issue_codes,
                        *(item.code for item in report.issues),
                    ]
                )
            )
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        self._select_best_current_takes(project_id)
        aggregate = self.get_project(project_id)
        missing = [
            shot.id
            for shot in aggregate.document.shots
            if not any(
                take.shot_id == shot.id
                and take.is_selected
                and take.freshness == Freshness.CURRENT
                and take.qa_status == QaStatus.PASSED
                for take in aggregate.takes
            )
        ]
        if not missing:
            plan.status = H3RoutePlanStatus.COMPLETE
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            self._advance_autonomous_run(
                project_id,
                run_id,
                completed_step=AutonomousRunStep.QA_AND_REPAIR,
                next_step=AutonomousRunStep.TIMELINE,
            )
            return True, False
        if run.repair_round >= run.max_repair_rounds:
            raise WorkflowConflictError(
                "REAL_H3 AUTO_DRAFT exhausted Director repair rounds for: "
                + ", ".join(missing)
            )
        repaired = self._run_autonomous_director_repairs(project_id, missing)
        if not repaired:
            raise WorkflowConflictError(
                "REAL_H3 AUTO_DRAFT needs an executable Director repair for: "
                + ", ".join(missing)
            )
        with self._lock:
            plan.status = H3RoutePlanStatus.PARTIAL
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            document = self._require_project_document(project_id)
            persisted = next(item for item in document.autonomous_runs if item.id == run_id)
            persisted.repair_round += 1
            persisted.route_plan_id = None
            persisted.current_step = AutonomousRunStep.PLAN_ROUTES
            persisted.waiting_reason = None
            persisted.updated_at = utc_now()
            self.repository.save_project(document)
        return True, False

    def _apply_automatic_h3_review(
        self,
        take: Take,
        report: VideoInspectionReport,
    ) -> None:
        evidence = self.repository.get_route_evidence_for_take(take.id)
        if evidence is None:
            raise WorkflowConflictError(f"Route evidence for Take {take.id} is missing")
        severe = [
            issue
            for issue in report.issues
            if issue.severity.upper()
            in {"ERROR", "HIGH", "MAJOR", "CRITICAL", "FATAL"}
        ]
        passed = evidence.technical_passed is True and not severe
        medium_count = sum(
            issue.severity.upper() in {"WARN", "WARNING", "MEDIUM", "MODERATE"}
            for issue in report.issues
        )
        low_count = sum(
            issue.severity.upper() in {"LOW", "MINOR"} for issue in report.issues
        )
        score = max(
            0.0,
            min(
                5.0,
                4.6 - len(severe) * 1.2 - medium_count * 0.45 - low_count * 0.2,
            ),
        )
        issues = list(report.issues)
        if not passed and not issues:
            issues.append(
                QaIssue(
                    code="AUTOMATED_TECHNICAL_GATE_FAILED",
                    severity="ERROR",
                    message="Provider media did not pass the stored technical gate.",
                    repair_layer="PROVIDER_RETRY",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
                )
            )
        notes = (
            f"AUTO_DRAFT visual candidate judge: {report.summary} "
            "Audio-stream presence was checked; transcript, speaker identity, and cross-shot "
            "voice identity remain outside this automated visual decision and require final "
            "episode review or a future STT/voice tool. User override remains allowed."
        )
        self.review_h3_take(
            take.id,
            H3TakeReviewRequest(
                hard_gate_passed=passed,
                score=score,
                notes=notes,
                issues=issues,
            ),
        )

    def _set_autonomous_waiting(
        self,
        project_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next(item for item in document.autonomous_runs if item.id == run_id)
            run.status = AutonomousRunStatus.RUNNING
            run.waiting_reason = reason
            run.updated_at = utc_now()
            self.repository.save_project(document)

    def _require_language_client(self) -> LanguageProviderClient:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError(
                "GPT-5.6-sol is required for autonomous visual asset and video review"
            )
        return self.language_provider_factory(api_key, base_url)

    @staticmethod
    def _approved_image_version(asset: Asset) -> AssetVersion | None:
        if asset.approved_version_id is None:
            return None
        return next(
            (
                item
                for item in asset.versions
                if item.id == asset.approved_version_id
                and item.approval_status == ApprovalStatus.APPROVED
                and item.media_type == AssetMediaType.IMAGE
                and (item.local_path is not None or item.external_url is not None)
            ),
            None,
        )

    @staticmethod
    def _rebind_asset_references(
        document: ProjectDocument,
        asset: Asset,
        approved: AssetVersion,
    ) -> bool:
        version_ids = {item.id for item in asset.versions}
        changed = False
        for plan in document.reference_plan:
            for binding in plan.bindings:
                if binding.target_id in version_ids and binding.target_id != approved.id:
                    binding.target_id = approved.id
                    changed = True
        return changed

    def _approved_bound_image_versions(
        self,
        document: ProjectDocument,
        shot_id: str,
    ) -> list[tuple[Asset, AssetVersion]]:
        plan = self._require_reference_plan(document, shot_id)
        result: list[tuple[Asset, AssetVersion]] = []
        seen: set[str] = set()
        for binding in plan.bindings:
            try:
                asset, version = self._find_asset_version(document, binding.target_id)
            except ArtifactNotFoundError:
                continue
            if (
                version.id in seen
                or version.approval_status != ApprovalStatus.APPROVED
                or version.media_type != AssetMediaType.IMAGE
                or (version.local_path is None and version.external_url is None)
            ):
                continue
            seen.add(version.id)
            result.append((asset, version))
        if result:
            return result
        for asset in document.assets:
            version = self._approved_image_version(asset)
            if version is not None and version.id not in seen:
                seen.add(version.id)
                result.append((asset, version))
        return result

    def _h3_image_input(
        self,
        document: ProjectDocument,
        version_id: str,
        role: H3MediaRole,
    ) -> H3MediaInput:
        _asset, version = self._find_asset_version(document, version_id)
        if version.local_path is not None:
            source = H3MediaSource(local_path=version.local_path)
        elif version.external_url is not None:
            source = H3MediaSource(external_url=version.external_url)
        else:
            raise WorkflowConflictError(f"AssetVersion {version.id} has no usable media")
        return H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=role,
            source=source,
            asset_version_id=version.id,
        )

    def _prepared_h3_media_inputs(
        self,
        document: ProjectDocument,
        plans: list[H3ProductionRoutePlan],
    ) -> dict[str, list[H3MediaInput]]:
        shot_revisions = {item.id: item.revision for item in document.shots}
        result: dict[str, list[H3MediaInput]] = {}
        claimed: set[tuple[str, H3MediaRole]] = set()
        for plan in plans:
            for decision in plan.decisions:
                if shot_revisions.get(decision.shot_id) != decision.input_shot_revision:
                    continue
                for preparation in decision.asset_preparations:
                    key = (decision.shot_id, preparation.role)
                    if (
                        key in claimed
                        or preparation.status != H3RouteAssetPreparationStatus.READY
                        or preparation.approved_version_id is None
                    ):
                        continue
                    try:
                        media = self._h3_image_input(
                            document,
                            preparation.approved_version_id,
                            preparation.role,
                        )
                    except (ArtifactNotFoundError, WorkflowConflictError):
                        continue
                    result.setdefault(decision.shot_id, []).append(media)
                    claimed.add(key)
        return result

    def delete_project(self, project_id: str) -> bool:
        return self.repository.delete_project(project_id)

    def get_project(self, project_id: str) -> ProjectAggregate:
        document = self._require_project_document(project_id)
        return self._aggregate(document)

    def get_h3_config(self) -> H3ProviderConfigResponse:
        return self.h3_config.response()

    def get_image_provider_config(self) -> ImageProviderConfigResponse:
        return self.asset_workbench.get_config()

    def update_image_provider_config(
        self,
        request: ImageProviderConfigUpdate,
    ) -> ImageProviderConfigResponse:
        return self.asset_workbench.update_config(request)

    def validate_image_provider_credentials(self) -> ImageProviderConfigResponse:
        try:
            return self.asset_workbench.validate_credentials()
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except ImageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc

    def get_language_provider_config(self) -> LanguageProviderConfigResponse:
        return self.language_config.response()

    def update_language_provider_config(
        self,
        request: LanguageProviderConfigUpdate,
    ) -> LanguageProviderConfigResponse:
        return self.language_config.update(request)

    def validate_language_provider_credentials(self) -> LanguageProviderConfigResponse:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        try:
            client = self.language_provider_factory(
                api_key,
                base_url,
            )
            client.validate_credentials()
        except LanguageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return self.language_config.response()

    def compile_h3_prompt_with_language_model(
        self,
        project_id: str,
        request: LanguagePromptCompileRequest,
    ) -> LanguagePromptCompileResponse:
        document = self._require_project_document(project_id)
        shot = next((item for item in document.shots if item.id == request.shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(request.shot_id)
        duration = request.duration_seconds or math.ceil(shot.duration_seconds)
        source_prompt = (
            request.source_prompt.strip()
            if request.source_prompt is not None
            else build_h3_prompt(document, shot)
        )
        if not source_prompt:
            raise WorkflowConflictError("H3 source prompt is empty")
        media_inputs = self._resolve_approved_asset_inputs(document, request.media_inputs)
        if media_inputs:
            try:
                validate_h3_probe(
                    route=request.route,
                    prompt=source_prompt,
                    duration_seconds=duration,
                    media_inputs=media_inputs,
                )
            except H3CapabilityError as exc:
                raise WorkflowConflictError(str(exc)) from exc

        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        instructions, input_text = build_language_prompt_input(
            document,
            shot,
            route=request.route,
            duration_seconds=duration,
            source_prompt=source_prompt,
            media_inputs=media_inputs,
        )
        try:
            client = self.language_provider_factory(api_key, base_url)
            compiled_prompt = client.generate_text(
                instructions=instructions,
                input_text=input_text,
            )
            validation_notes = validate_language_prompt(
                compiled_prompt,
                document,
                shot,
                duration_seconds=duration,
            )
        except (LanguageProviderError, LanguagePromptValidationError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        return LanguagePromptCompileResponse(
            shot_id=shot.id,
            route=request.route,
            duration_seconds=duration,
            source_prompt=source_prompt,
            compiled_prompt=compiled_prompt,
            validation_notes=validation_notes,
        )

    def add_reference_pack_entry(
        self,
        project_id: str,
        request: ReferencePackEntryCreateRequest,
    ) -> ReferencePackEntryMutationResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            subject = self._require_asset_in_document(document, request.subject_asset_id)
            if subject.kind not in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}:
                raise WorkflowConflictError(
                    "Reference packs must belong to a character, location, or prop asset"
                )
            if not role_is_compatible(subject.kind, request.role):
                raise WorkflowConflictError(
                    f"Reference role {request.role.value} is incompatible with {subject.kind.value}"
                )
            _source_asset, source_version = self._find_asset_version(
                document,
                request.asset_version_id,
            )
            if (
                _source_asset.approved_version_id != source_version.id
                or source_version.approval_status != ApprovalStatus.APPROVED
            ):
                raise WorkflowConflictError(
                    "Only an asset's current approved formal version can enter a reference pack"
                )
            if source_version.media_type != AssetMediaType.IMAGE:
                raise WorkflowConflictError("Reference pack entries currently require image media")

            now = utc_now()
            pack_id = f"reference-pack-{subject.id}"
            pack = next(
                (item for item in document.reference_packs if item.id == pack_id),
                None,
            )
            if pack is None:
                pack = ReferencePack(
                    id=pack_id,
                    project_id=project_id,
                    subject_asset_id=subject.id,
                    name=f"{subject.name}参考包",
                    created_at=now,
                    updated_at=now,
                )
                document.reference_packs.append(pack)

            normalized_tags = list(
                dict.fromkeys(tag.strip() for tag in request.tags if tag.strip())
            )
            existing = next(
                (
                    item
                    for item in pack.entries
                    if item.asset_version_id == source_version.id
                    and item.role == request.role
                    and item.angle == request.angle
                    and item.framing == request.framing
                ),
                None,
            )
            if existing is None:
                entry = ReferencePackEntry(
                    id=f"reference-entry-{uuid4().hex[:12]}",
                    asset_version_id=source_version.id,
                    role=request.role,
                    angle=request.angle,
                    framing=request.framing,
                    tags=normalized_tags,
                    description=request.description.strip(),
                    priority=request.priority,
                )
                pack.entries.append(entry)
            else:
                existing.tags = normalized_tags
                existing.description = request.description.strip()
                existing.priority = request.priority
                entry = existing
            pack.updated_at = now
            document.project.updated_at = now
            self.repository.save_project(document)
            return ReferencePackEntryMutationResponse(
                pack=pack,
                entry=entry,
                project=self._aggregate(document),
            )

    def update_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            shot = self._require_shot_in_document(document, shot_id)
            plan = self._require_reference_plan(document, shot_id)
            before = plan.model_dump(mode="json")
            self._apply_reference_plan_update(document, shot_id, request)
            now = utc_now()
            if plan.model_dump(mode="json") != before:
                shot.revision += 1
                self.repository.mark_takes_stale(project_id, [shot_id])
                self._invalidate_current_manifests(document)
                document.project.stage = ProjectStage.PLANNED
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def preview_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ReferencePlanPreviewRequest,
    ) -> ReferencePlanPreviewResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            return preview_reference_plan(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def compile_keyframe_brief(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeBriefCompileRequest,
    ) -> KeyframeBriefCompileResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            return compile_keyframe_brief(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def get_director_advice(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorAdviceResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            preview = preview_reference_plan(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

        shot_takes = self.repository.list_takes_for_shot(project_id, shot_id)
        if request.take_id is not None:
            selected_take = next(
                (take for take in shot_takes if take.id == request.take_id),
                None,
            )
            if selected_take is None:
                raise ArtifactNotFoundError(request.take_id)
            qa_takes = [selected_take]
        else:
            qa_takes = [
                take for take in shot_takes if take.qa_status == QaStatus.REJECTED
            ][-5:]
        qa_issues = [
            issue.model_copy(deep=True)
            for take in qa_takes
            for issue in take.qa_report.issues
        ]
        reviewer_notes = [
            f"{take.id}: {take.qa_report.reviewer_notes}"
            for take in qa_takes
            if take.qa_report.reviewer_notes
        ]
        if request.inspection_id is not None:
            inspection = self.repository.get_video_inspection(request.inspection_id)
            if (
                inspection is None
                or inspection.project_id != project_id
                or inspection.shot_id != shot_id
            ):
                raise ArtifactNotFoundError(request.inspection_id)
            if request.take_id is not None and inspection.take_id != request.take_id:
                raise WorkflowConflictError(
                    "Director advice Take and video inspection do not refer to the same output"
                )
            qa_issues.extend(issue.model_copy(deep=True) for issue in inspection.issues)
            reviewer_notes.append(
                f"{inspection.id}: {inspection.summary}"
            )
        capability_profile = build_h3_capability_profile(
            self.repository.list_takes(project_id),
            project_id=project_id,
        )

        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        instructions, input_text = build_director_advice_input(
            document,
            shot,
            plan,
            preview,
            request,
            qa_issues,
            reviewer_notes,
            capability_profile,
        )
        try:
            client = self.language_provider_factory(api_key, base_url)
            output_text = client.generate_text(
                instructions=instructions,
                input_text=input_text,
            )
        except LanguageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        try:
            payload = parse_director_advice(output_text)
        except ValueError as first_error:
            repair_instructions, repair_input = build_director_advice_repair_input(
                original_input=input_text,
                invalid_output=output_text,
                validation_error=first_error,
            )
            try:
                repaired_output = client.generate_text(
                    instructions=repair_instructions,
                    input_text=repair_input,
                )
                payload = parse_director_advice(repaired_output)
            except (LanguageProviderError, ValueError) as repair_error:
                raise ProviderOperationError(
                    "Director Agent JSON and one automatic repair were invalid: "
                    f"first={first_error}; repair={repair_error}"
                ) from repair_error
        return DirectorAdviceResponse(
            shot_id=shot_id,
            advisories=payload.advisories,
            repair_options=payload.repair_options,
            recommended_strategy=payload.recommended_strategy,
            proposed_repair=payload.proposed_repair,
            capability_profile_version=capability_profile.version,
            qa_issue_codes=list(dict.fromkeys(issue.code for issue in qa_issues)),
        )

    def get_h3_capability_profile(
        self,
        project_id: str,
    ) -> H3CapabilityProfileResponse:
        self._require_project_document(project_id)
        return build_h3_capability_profile(
            self.repository.list_takes(project_id),
            project_id=project_id,
        )

    def compile_h3_production_route_plan(
        self,
        project_id: str,
        request: H3ProductionRoutePlanRequest,
    ) -> H3ProductionRoutePlanResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            if refresh_lightweight_reference_plans(document):
                document.project.updated_at = utc_now()
                self.repository.save_project(document)
        language_client = None
        if request.use_language_agent:
            api_key = self.language_config.api_key()
            base_url = self.language_config.base_url()
            if api_key or is_loopback_openai_url(base_url):
                language_client = self.language_provider_factory(api_key, base_url)
        existing = self.repository.list_h3_route_plans(project_id)
        try:
            plan = build_h3_production_route_plan(
                document=document,
                tasks=self.repository.list_tasks(project_id),
                takes=self.repository.list_takes(project_id),
                evidence=self.repository.list_route_evidence(project_id),
                provider_runs=self.repository.list_provider_runs(project_id),
                inspections=self.repository.list_video_inspections(project_id),
                pricing=self.h3_config.pricing_snapshot(),
                version=max((item.version for item in existing), default=0) + 1,
                created_at=utc_now(),
                shot_ids=request.shot_ids,
                candidate_count=request.candidate_count,
                language_client=language_client,
                prepared_media_inputs=self._prepared_h3_media_inputs(
                    document,
                    existing,
                ),
            )
        except RoutePlanningError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self.repository.save_h3_route_plan(plan)
        return H3ProductionRoutePlanResponse(plan=plan)

    def list_h3_production_route_plans(
        self,
        project_id: str,
    ) -> H3ProductionRoutePlanListResponse:
        self._require_project_document(project_id)
        return H3ProductionRoutePlanListResponse(
            plans=self.repository.list_h3_route_plans(project_id)
        )

    def execute_h3_production_route_plan(
        self,
        project_id: str,
        plan_id: str,
        request: H3ProductionRouteExecuteRequest,
    ) -> H3ProductionRouteExecuteResponse:
        inspection_take_ids: list[str] = []
        queued_tasks: list[GenerationTask] = []
        with self._lock:
            document = self._require_project_document(project_id)
            plan = self.repository.get_h3_route_plan(plan_id)
            if plan is None or plan.project_id != project_id:
                raise ArtifactNotFoundError(plan_id)
            try:
                validate_route_plan_freshness(plan, document)
            except RoutePlanningError as exc:
                plan.status = H3RoutePlanStatus.STALE
                plan.updated_at = utc_now()
                self.repository.save_h3_route_plan(plan)
                raise WorkflowConflictError(str(exc)) from exc

            needs_provider_submission = any(
                item.action == H3ShotRouteAction.GENERATE
                and item.readiness
                not in {
                    H3ShotRouteReadiness.NEEDS_ASSET,
                    H3ShotRouteReadiness.UNSUPPORTED,
                }
                for item in plan.decisions
            )
            if (
                request.enqueue_new_generations
                and needs_provider_submission
                and not self.h3_config.api_key()
            ):
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing production H3 tasks"
                )
            for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
                if decision.action == H3ShotRouteAction.REUSE_TAKE:
                    if not request.reuse_passed_takes or decision.reuse_take_id is None:
                        decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                        continue
                    self.select_take(decision.reuse_take_id)
                    decision.execution_status = H3ShotRouteExecutionStatus.REUSED
                    if request.inspect_reused_takes:
                        inspection_take_ids.append(decision.reuse_take_id)
                    continue

                if (
                    decision.readiness
                    in {H3ShotRouteReadiness.NEEDS_ASSET, H3ShotRouteReadiness.UNSUPPORTED}
                    or not request.enqueue_new_generations
                ):
                    decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                    continue
                shot = self._require_shot_in_document(document, decision.shot_id)
                media_inputs = self._resolve_approved_asset_inputs(
                    document,
                    decision.media_inputs,
                )
                try:
                    validate_h3_probe(
                        route=decision.route,
                        prompt=decision.prompt,
                        duration_seconds=decision.duration_seconds,
                        media_inputs=media_inputs,
                    )
                except H3CapabilityError as exc:
                    raise WorkflowConflictError(str(exc)) from exc
                existing_tasks = self.repository.list_tasks_for_shot(project_id, shot.id)
                next_candidate = max(
                    (item.candidate_index for item in existing_tasks),
                    default=0,
                ) + 1
                task_ids: list[str] = []
                for offset in range(decision.candidate_count):
                    task, run, route_evidence = self._new_h3_task_and_run(
                        document=document,
                        shot_id=shot.id,
                        route=decision.route,
                        duration_seconds=decision.duration_seconds,
                        prompt=decision.prompt,
                        media_inputs=media_inputs,
                        purpose=EvidencePurpose.PRODUCTION,
                        learning_objective=None,
                        regression_case_id=None,
                        candidate_index=next_candidate + offset,
                        attempt=1,
                        retry_of_task_id=None,
                    )
                    self.repository.save_task(task)
                    self.repository.save_provider_run(run)
                    self.repository.save_route_evidence(route_evidence)
                    queued_tasks.append(task)
                    task_ids.append(task.id)
                decision.task_ids = task_ids
                decision.execution_status = H3ShotRouteExecutionStatus.QUEUED

            if queued_tasks:
                document = self._require_project_document(project_id)
                document.project.stage = ProjectStage.GENERATING
                document.project.updated_at = utc_now()
                self.repository.save_project(document)
                plan.status = H3RoutePlanStatus.EXECUTING
            elif all(
                item.execution_status == H3ShotRouteExecutionStatus.REUSED
                for item in plan.decisions
            ):
                plan.status = H3RoutePlanStatus.COMPLETE
            else:
                plan.status = H3RoutePlanStatus.PARTIAL
            plan.updated_at = utc_now()
            plan.executed_at = plan.updated_at
            self.repository.save_h3_route_plan(plan)

        inspections: list[VideoInspectionReport] = []
        persisted_by_take: dict[str, VideoInspectionReport] = {}
        for report in self.repository.list_video_inspections(project_id):
            persisted_by_take[report.take_id] = report
        for take_id in inspection_take_ids:
            decision = next(
                item for item in plan.decisions if item.reuse_take_id == take_id
            )
            existing_report = persisted_by_take.get(take_id)
            if existing_report is not None and (
                not request.semantic_review
                or existing_report.semantic_status == SemanticReviewStatus.COMPLETE
            ):
                inspections.append(existing_report)
                if existing_report.id not in decision.inspection_ids:
                    decision.inspection_ids.append(existing_report.id)
                decision.inspection_issue_codes = list(
                    dict.fromkeys(
                        [
                            *decision.inspection_issue_codes,
                            *(item.code for item in existing_report.issues),
                        ]
                    )
                )
                continue
            response = self.inspect_take_video(
                take_id,
                VideoInspectionRequest(
                    include_previous_boundary=True,
                    semantic_review=request.semantic_review,
                    max_frames=decision.inspection.max_frames,
                    review_focus=decision.inspection.review_focus,
                ),
            )
            inspections.append(response.report)
            decision.inspection_ids.append(response.report.id)
            decision.inspection_issue_codes = list(
                dict.fromkeys(
                    [
                        *decision.inspection_issue_codes,
                        *(item.code for item in response.report.issues),
                    ]
                )
            )

        manifest = None
        export = None
        if request.rebuild_manifest:
            aggregate = self.get_project(project_id)
            if aggregate.progress.shots_with_selected_take == aggregate.progress.total_shots:
                manifest = self.rebuild_manifest(project_id)
        if request.render_export and manifest is not None:
            export = self.export_project(project_id, rebuild_manifest=False)

        plan.updated_at = utc_now()
        if not queued_tasks and all(
            item.execution_status == H3ShotRouteExecutionStatus.REUSED
            for item in plan.decisions
        ):
            plan.status = H3RoutePlanStatus.COMPLETE
        self.repository.save_h3_route_plan(plan)
        return H3ProductionRouteExecuteResponse(
            plan=plan,
            project=self.get_project(project_id),
            queued_tasks=queued_tasks,
            inspections=inspections,
            manifest=manifest,
            export=export,
        )

    def decide_director_repair(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorRepairDecisionRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            shot = self._require_shot_in_document(document, shot_id)
            if request.source_take_id is not None:
                source_take = self.repository.get_take(request.source_take_id)
                if (
                    source_take is None
                    or source_take.project_id != project_id
                    or source_take.shot_id != shot_id
                ):
                    raise ArtifactNotFoundError(request.source_take_id)

            now = utc_now()
            affected_shot_ids = [shot_id]
            if request.decision == DirectorRepairDecision.APPLIED:
                if request.strategy == DirectorRepairStrategy.SPLIT_SHOT:
                    affected_shot_ids = self._apply_shot_split(
                        document,
                        shot,
                        request,
                    )
                else:
                    patch = request.shot_patch
                    if patch is not None:
                        if patch.dialogue_line_ids is not None:
                            self._validate_dialogue_line_ids(
                                document,
                                patch.dialogue_line_ids,
                            )
                        updates = patch.model_dump(exclude_none=True)
                        for field_name, value in updates.items():
                            setattr(shot, field_name, value)
                    if request.reference_plan is not None:
                        self._apply_reference_plan_update(
                            document,
                            shot_id,
                            request.reference_plan,
                        )
                    shot.revision += 1
                    self.repository.mark_takes_stale(project_id, [shot_id])
                self._invalidate_current_manifests(document)
                document.project.stage = ProjectStage.PLANNED

            document.repair_history.append(
                DirectorRepairRecord(
                    id=f"director-repair-{uuid4().hex}",
                    source_shot_id=shot_id,
                    source_take_id=request.source_take_id,
                    strategy=request.strategy,
                    decision=request.decision,
                    reason=request.reason.strip(),
                    issue_codes=list(dict.fromkeys(request.issue_codes)),
                    affected_shot_ids=affected_shot_ids,
                    created_at=now,
                )
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def run_director_loop(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorLoopResponse:
        advice = self.get_director_advice(project_id, shot_id, request)
        project = self.get_project(project_id)
        if project.document.project.run_mode != RunMode.AUTO_DRAFT:
            return DirectorLoopResponse(
                advice=advice,
                repair_applied=False,
                next_action="AWAIT_USER",
                project=project,
            )
        if advice.proposed_repair is None:
            return DirectorLoopResponse(
                advice=advice,
                repair_applied=False,
                next_action="NO_CHANGE",
                project=project,
            )
        proposed = advice.proposed_repair.model_copy(
            deep=True,
            update={
                "decision": DirectorRepairDecision.APPLIED,
                "source_take_id": request.take_id,
                "issue_codes": advice.qa_issue_codes,
            },
        )
        updated = self.decide_director_repair(project_id, shot_id, proposed)
        return DirectorLoopResponse(
            advice=advice,
            repair_applied=True,
            next_action="REGENERATE_SHOT",
            project=updated,
        )

    def create_asset(
        self,
        project_id: str,
        request: AssetCreateRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.create_asset(project_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ProjectNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_local_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportLocalRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.import_local(project_id, asset_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_url_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportUrlRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.import_url(project_id, asset_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def review_asset_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetReviewRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.review_version(
                project_id,
                asset_id,
                version_id,
                request,
            )
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def enqueue_image_task(
        self,
        project_id: str,
        asset_id: str,
        request: ImageTaskCreateRequest,
    ) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.enqueue_image_task(project_id, asset_id, request)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(project_id))

    def list_image_tasks(
        self,
        project_id: str,
        asset_id: str | None = None,
    ) -> ImageTaskListResponse:
        try:
            tasks = self.asset_workbench.list_image_tasks(project_id, asset_id)
        except AssetWorkbenchNotFound as exc:
            raise ProjectNotFoundError(str(exc)) from exc
        return ImageTaskListResponse(tasks=tasks)

    def advance_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.advance_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except ImageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def cancel_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.cancel_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def retry_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.retry_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def recover_image_task_output(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.recover_image_output(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

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
        _document, _shot, prompt, duration, media_inputs = self._resolve_h3_probe(
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
                media_inputs=media_inputs,
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
            document, shot, prompt, duration, media_inputs = self._resolve_h3_probe(
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
                media_inputs=media_inputs,
                purpose=request.purpose,
                learning_objective=request.learning_objective,
                regression_case_id=request.regression_case_id,
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
                    learning_objective=run.learning_objective,
                    regression_case_id=run.regression_case_id,
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

    def resolve_h3_not_submitted(
        self,
        task_id: str,
        request: H3NotSubmittedResolutionRequest,
    ) -> H3TaskActionResponse:
        try:
            task = self.h3_worker.resolve_not_submitted(task_id, request.reason)
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except InvalidTaskTransition as exc:
            raise WorkflowConflictError(str(exc)) from exc
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

    def inspect_take_video(
        self,
        take_id: str,
        request: VideoInspectionRequest,
    ) -> VideoInspectionResponse:
        take = self.repository.get_take(take_id)
        if take is None:
            raise ArtifactNotFoundError(take_id)
        document = self._require_project_document(take.project_id)
        shot = self._require_shot_in_document(document, take.shot_id)

        previous_take: Take | None = None
        previous_shot: ShotPlan | None = None
        if request.previous_take_id is not None:
            candidate = self.repository.get_take(request.previous_take_id)
            if candidate is None or candidate.project_id != take.project_id:
                raise ArtifactNotFoundError(request.previous_take_id)
            previous_take = candidate
            previous_shot = self._require_shot_in_document(document, candidate.shot_id)
        elif request.include_previous_boundary:
            previous_shot = max(
                (item for item in document.shots if item.sequence < shot.sequence),
                key=lambda item: item.sequence,
                default=None,
            )
            if previous_shot is not None:
                previous_take = next(
                    (
                        item
                        for item in reversed(
                            self.repository.list_takes_for_shot(
                                take.project_id,
                                previous_shot.id,
                            )
                        )
                        if item.is_selected and item.freshness == Freshness.CURRENT
                    ),
                    None,
                )
                if previous_take is None:
                    previous_shot = None

        language_client = None
        if request.semantic_review:
            api_key = self.language_config.api_key()
            base_url = self.language_config.base_url()
            if api_key or is_loopback_openai_url(base_url):
                language_client = self.language_provider_factory(api_key, base_url)
        report = self.video_inspector.inspect_video(
            document=document,
            shot=shot,
            take=take,
            previous_shot=previous_shot,
            previous_take=previous_take,
            request=request,
            language_client=language_client,
            created_at=utc_now(),
        )
        self.repository.save_video_inspection(report)
        return VideoInspectionResponse(
            report=report,
            project=self.get_project(take.project_id),
        )

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
        document = self._require_project_document(project_id)
        resumable = next(
            (
                run
                for run in reversed(document.autonomous_runs)
                if run.status
                in {AutonomousRunStatus.RUNNING, AutonomousRunStatus.NEEDS_ATTENTION}
            ),
            None,
        )
        run_id = resumable.id if resumable is not None else None
        try:
            return self._execute_auto_draft(
                project_id,
                render_export=render_export,
                autonomous_run_id=run_id,
            )
        except (WorkflowConflictError, ProviderOperationError, MediaGenerationError) as exc:
            if run_id is not None:
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.NEEDS_ATTENTION,
                    error_message=str(exc),
                )
            raise

    def _execute_auto_draft(
        self,
        project_id: str,
        *,
        render_export: bool,
        autonomous_run_id: str | None,
    ) -> AutoDraftResponse:
        repair_round = 0
        max_repair_rounds = 2
        while True:
            self.enqueue(project_id, EnqueueRequest())
            drain_result = self.drain(project_id, max_steps=5000)
            if drain_result.remaining_runnable > 0:
                raise WorkflowConflictError("Worker did not reach an idle state")
            aggregate = self.get_project(project_id)
            if aggregate.progress.unresolved_tasks > 0:
                raise WorkflowConflictError(
                    "AUTO_DRAFT stopped at actionable task failures; reconcile, recover, or retry them first"
                )
            if autonomous_run_id is not None:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.GENERATE,
                    next_step=AutonomousRunStep.QA_AND_REPAIR,
                )

            self._select_best_current_takes(project_id)
            aggregate = self.get_project(project_id)
            missing_selected = [
                shot.id
                for shot in aggregate.document.shots
                if not any(
                    take.shot_id == shot.id
                    and take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and take.qa_status == QaStatus.PASSED
                    for take in aggregate.takes
                )
            ]
            if not missing_selected:
                break
            if repair_round >= max_repair_rounds:
                raise WorkflowConflictError(
                    "AUTO_DRAFT exhausted Director repair rounds for: "
                    + ", ".join(missing_selected)
                )
            repaired = self._run_autonomous_director_repairs(
                project_id,
                missing_selected,
            )
            if not repaired:
                raise WorkflowConflictError(
                    "AUTO_DRAFT needs a passed Take or an executable Director repair for: "
                    + ", ".join(missing_selected)
                )
            repair_round += 1
            if autonomous_run_id is not None:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.QA_AND_REPAIR,
                    next_step=AutonomousRunStep.GENERATE,
                )
        if autonomous_run_id is not None:
            self._advance_autonomous_run(
                project_id,
                autonomous_run_id,
                completed_step=AutonomousRunStep.QA_AND_REPAIR,
                next_step=AutonomousRunStep.TIMELINE,
            )

        self.rebuild_manifest(project_id)
        export: ExportRecord | None = None
        if render_export:
            if autonomous_run_id is not None:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.TIMELINE,
                    next_step=AutonomousRunStep.EXPORT,
                )
            export = self.export_project(project_id, rebuild_manifest=False)
        if autonomous_run_id is not None:
            completed_steps = [AutonomousRunStep.TIMELINE]
            if export is not None:
                completed_steps.append(AutonomousRunStep.EXPORT)
            self._finish_autonomous_run(
                project_id,
                autonomous_run_id,
                status=AutonomousRunStatus.COMPLETE,
                export_id=export.id if export is not None else None,
                completed_steps=completed_steps,
            )
        return AutoDraftResponse(project=self.get_project(project_id), export=export)

    def _run_autonomous_director_repairs(
        self,
        project_id: str,
        shot_ids: list[str],
    ) -> bool:
        project = self.get_project(project_id)
        if project.document.project.run_mode != RunMode.AUTO_DRAFT:
            return False
        reports = project.video_inspections
        applied = False
        for shot_id in shot_ids:
            candidates = [
                take
                for take in project.takes
                if take.shot_id == shot_id and take.freshness == Freshness.CURRENT
            ]
            if not candidates:
                continue
            source_take = max(
                candidates,
                key=lambda take: (take.qa_report.score, take.created_at, take.id),
            )
            inspection = next(
                (
                    report
                    for report in reversed(reports)
                    if report.take_id == source_take.id
                ),
                None,
            )
            result = self.run_director_loop(
                project_id,
                shot_id,
                DirectorAdviceRequest(
                    take_id=source_take.id,
                    inspection_id=inspection.id if inspection is not None else None,
                    user_direction=(
                        "Continue the autonomous draft. Preserve the narrative purpose and only "
                        "apply a repair that addresses observed QA evidence."
                    ),
                ),
            )
            applied = result.repair_applied or applied
        return applied

    def _select_best_current_takes(self, project_id: str) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            takes = self.repository.list_takes(project_id)
            for shot in document.shots:
                candidates = [
                    take
                    for take in takes
                    if take.shot_id == shot.id
                    and take.freshness == Freshness.CURRENT
                    and take.qa_status == QaStatus.PASSED
                ]
                if not candidates:
                    continue
                best = max(
                    candidates,
                    key=lambda take: (take.qa_report.score, -take.candidate_index),
                )
                if not best.is_selected:
                    self.repository.select_take(best.id)

    def _resolve_h3_probe(
        self,
        *,
        project_id: str,
        request: H3ProbeRequest,
    ) -> tuple[ProjectDocument, ShotPlan, str, int, list[H3MediaInput]]:
        document = self._require_project_document(project_id)
        shot = next((item for item in document.shots if item.id == request.shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(request.shot_id)
        if (
            request.regression_case_id is not None
            and known_regression_case(request.regression_case_id) is None
        ):
            raise ArtifactNotFoundError(request.regression_case_id)
        prompt = (
            request.prompt_override.strip()
            if request.prompt_override is not None
            else build_h3_prompt(document, shot)
        )
        duration = request.duration_seconds or math.ceil(shot.duration_seconds)
        media_inputs = self._resolve_approved_asset_inputs(
            document,
            request.media_inputs,
        )
        try:
            validate_h3_probe(
                route=request.route,
                prompt=prompt,
                duration_seconds=duration,
                media_inputs=media_inputs,
            )
        except H3CapabilityError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return document, shot, prompt, duration, media_inputs

    def _resolve_approved_asset_inputs(
        self,
        document: ProjectDocument,
        media_inputs: list[H3MediaInput],
    ) -> list[H3MediaInput]:
        approved_versions: dict[str, AssetVersion] = {}
        for asset in document.assets:
            approved_id = asset.approved_version_id
            if approved_id is None:
                continue
            approved = next(
                (version for version in asset.versions if version.id == approved_id),
                None,
            )
            if approved is not None and approved.approval_status == ApprovalStatus.APPROVED:
                approved_versions[approved.id] = approved

        expected_kinds = {
            AssetMediaType.IMAGE: H3MediaKind.IMAGE,
            AssetMediaType.VIDEO: H3MediaKind.VIDEO,
            AssetMediaType.AUDIO: H3MediaKind.AUDIO,
        }
        resolved: list[H3MediaInput] = []
        for media in media_inputs:
            if media.asset_version_id is None:
                resolved.append(media)
                continue
            version = approved_versions.get(media.asset_version_id)
            if version is None:
                raise WorkflowConflictError(
                    f"Asset version {media.asset_version_id} is not the asset's approved formal version"
                )
            expected_kind = expected_kinds.get(version.media_type)
            if expected_kind is None or expected_kind != media.kind:
                raise WorkflowConflictError(
                    f"Approved asset version {version.id} is not compatible with {media.kind.value}"
                )
            if version.local_path is not None:
                source = H3MediaSource(local_path=version.local_path)
            elif version.external_url is not None:
                source = H3MediaSource(external_url=version.external_url)
            else:
                raise WorkflowConflictError(
                    f"Approved asset version {version.id} has no usable media source"
                )
            resolved.append(media.model_copy(update={"source": source}))
        return resolved

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
        learning_objective: str | None,
        regression_case_id: str | None,
        candidate_index: int,
        attempt: int,
        retry_of_task_id: str | None,
    ) -> tuple[GenerationTask, H3ProviderRun, RouteEvidence]:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        run_id = f"h3-run-{uuid4().hex}"
        shot = self._require_shot_in_document(document, shot_id)
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
            "shot_revision": shot.revision,
            "learning_objective": learning_objective,
            "regression_case_id": regression_case_id,
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
            input_shot_revision=shot.revision,
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
            learning_objective=(
                learning_objective.strip() if learning_objective is not None else None
            ),
            regression_case_id=regression_case_id,
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
        shot = self._require_shot_in_document(document, shot_id)
        normalized_request = {
            "project_id": document.project.id,
            "shot_id": shot_id,
            "operation": operation.value,
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "shot_revision": shot.revision,
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
            input_shot_revision=shot.revision,
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
            video_inspections=self.repository.list_video_inspections(document.project.id),
            progress=progress,
        )

    def _apply_reference_plan_update(
        self,
        document: ProjectDocument,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> None:
        for requirement in request.requirements:
            self._require_asset_in_document(document, requirement.subject_asset_id)
        for beat in request.action_beats:
            self._require_asset_in_document(document, beat.actor_asset_id)
        for interaction in request.interactions:
            self._require_asset_in_document(document, interaction.actor_asset_id)
            self._require_asset_in_document(document, interaction.target_asset_id)

        plan = self._require_reference_plan(document, shot_id)
        plan.requirements = [item.model_copy(deep=True) for item in request.requirements]
        plan.action_beats = [
            item.model_copy(deep=True)
            for item in sorted(request.action_beats, key=lambda item: item.order)
        ]
        plan.interactions = [item.model_copy(deep=True) for item in request.interactions]
        plan.max_reference_images = request.max_reference_images
        plan.planning_notes = (
            request.planning_notes.strip() if request.planning_notes else None
        )
        plan.validation_notes = [
            "Reference requirements are selected from approved semantic packs at preview time",
            "Spatial interaction constraints are provider-neutral and apply to keyframe and video planning",
            "Missing references, topology gaps, and split suggestions are non-blocking Director Agent advisories",
        ]

    def _apply_shot_split(
        self,
        document: ProjectDocument,
        source_shot: ShotPlan,
        request: DirectorRepairDecisionRequest,
    ) -> list[str]:
        shot_index = document.shots.index(source_shot)
        execution_index = next(
            (
                index
                for index, item in enumerate(document.execution_plan)
                if item.shot_id == source_shot.id
            ),
            None,
        )
        reference_index = next(
            (
                index
                for index, item in enumerate(document.reference_plan)
                if item.shot_id == source_shot.id
            ),
            None,
        )
        if execution_index is None or reference_index is None:
            raise WorkflowConflictError(
                f"Shot {source_shot.id} is missing execution or reference dependencies"
            )
        source_execution = document.execution_plan[execution_index]
        source_reference = document.reference_plan[reference_index]
        existing_ids = {item.id for item in document.shots if item.id != source_shot.id}
        replacement_ids: list[str] = []
        replacement_shots: list[ShotPlan] = []
        replacement_execution: list[ExecutionPlanEntry] = []
        replacement_references: list[ReferencePlanEntry] = []

        for draft in request.split_shots:
            replacement_id = draft.id or f"{source_shot.id}-split-{uuid4().hex[:10]}"
            if replacement_id in existing_ids or replacement_id in replacement_ids:
                raise WorkflowConflictError(f"Duplicate replacement shot ID: {replacement_id}")
            self._validate_dialogue_line_ids(document, draft.dialogue_line_ids)
            replacement_ids.append(replacement_id)
            replacement_shots.append(
                ShotPlan(
                    id=replacement_id,
                    project_id=document.project.id,
                    sequence=source_shot.sequence,
                    duration_seconds=draft.duration_seconds,
                    narrative_purpose=draft.narrative_purpose,
                    visual=draft.visual,
                    framing=draft.framing,
                    camera=draft.camera,
                    audio_intent=draft.audio_intent,
                    dialogue_line_ids=list(draft.dialogue_line_ids),
                    continuity=draft.continuity.model_copy(deep=True),
                    transition_out=draft.transition_out,
                    merge_candidate=draft.merge_candidate,
                    local_rework_risk=draft.local_rework_risk,
                    approval_status=ApprovalStatus.APPROVED,
                    freshness=Freshness.CURRENT,
                    revision=(source_shot.revision + 1 if replacement_id == source_shot.id else 1),
                )
            )
            replacement_execution.append(
                source_execution.model_copy(
                    deep=True,
                    update={
                        "shot_id": replacement_id,
                        "rationale": (
                            f"{source_execution.rationale} | Director repair: "
                            f"{request.reason.strip()}"
                        ),
                    },
                )
            )
            replacement_references.append(
                source_reference.model_copy(
                    deep=True,
                    update={"shot_id": replacement_id},
                )
            )

        document.shots[shot_index : shot_index + 1] = replacement_shots
        document.execution_plan[
            execution_index : execution_index + 1
        ] = replacement_execution
        document.reference_plan[
            reference_index : reference_index + 1
        ] = replacement_references
        for draft, replacement_id in zip(
            request.split_shots,
            replacement_ids,
            strict=True,
        ):
            if draft.reference_plan is not None:
                self._apply_reference_plan_update(
                    document,
                    replacement_id,
                    draft.reference_plan,
                )
        for sequence, shot in enumerate(document.shots, start=1):
            shot.sequence = sequence

        dependency_target = next(
            (
                replacement_id
                for draft, replacement_id in zip(
                    request.split_shots,
                    replacement_ids,
                    strict=True,
                )
                if draft.inherits_source_dependencies
            ),
            replacement_ids[-1],
        )
        bible = next(
            (
                item
                for item in document.bible_versions
                if item.id == document.project.current_bible_version_id
            ),
            None,
        )
        if bible is None:
            raise WorkflowConflictError("Project current ProjectBibleVersion is missing")
        for prop in bible.props:
            for state in prop.states:
                if state.shot_id == source_shot.id:
                    state.shot_id = dependency_target

        self.repository.mark_takes_stale(document.project.id, [source_shot.id])
        return replacement_ids

    def _validate_dialogue_line_ids(
        self,
        document: ProjectDocument,
        line_ids: list[str],
    ) -> None:
        if not line_ids:
            return
        script = self._current_script(document)
        known = {line.id for line in script.lines}
        unknown = sorted(set(line_ids) - known)
        if unknown:
            raise ArtifactNotFoundError(", ".join(unknown))

    def _asset_mutation_response(
        self,
        project_id: str,
        version_id: str,
    ) -> AssetMutationResponse:
        project = self.get_project(project_id)
        for asset in project.document.assets:
            version: AssetVersion | None = next(
                (item for item in asset.versions if item.id == version_id),
                None,
            )
            if version is not None:
                return AssetMutationResponse(
                    asset=asset,
                    version=version,
                    project=project,
                )
        raise ArtifactNotFoundError(version_id)

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

    @staticmethod
    def _require_asset_in_document(document: ProjectDocument, asset_id: str) -> Asset:
        asset = next((item for item in document.assets if item.id == asset_id), None)
        if asset is None:
            raise ArtifactNotFoundError(asset_id)
        return asset

    @staticmethod
    def _find_asset_version(
        document: ProjectDocument,
        version_id: str,
    ) -> tuple[Asset, AssetVersion]:
        for asset in document.assets:
            version = next((item for item in asset.versions if item.id == version_id), None)
            if version is not None:
                return asset, version
        raise ArtifactNotFoundError(version_id)

    @staticmethod
    def _require_shot_in_document(document: ProjectDocument, shot_id: str) -> ShotPlan:
        shot = next((item for item in document.shots if item.id == shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(shot_id)
        return shot

    @staticmethod
    def _require_reference_plan(
        document: ProjectDocument,
        shot_id: str,
    ) -> ReferencePlanEntry:
        plan = next((item for item in document.reference_plan if item.shot_id == shot_id), None)
        if plan is None:
            raise ArtifactNotFoundError(f"reference-plan:{shot_id}")
        return plan

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
