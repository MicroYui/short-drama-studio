"""Application service for the provider-neutral short-drama workflow."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from short_drama.asset_workbench import (
    AssetProviderConfigurationError,
    AssetWorkbench,
    AssetWorkbenchConflict,
    AssetWorkbenchNotFound,
    ImageProviderFactory,
    default_image_provider_factory,
)
from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
    recommended_reusable_reference_views,
    search_asset_catalog,
)
from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextCompiler
from short_drama.audio_inspection import AudioInspector, AudioSemanticService
from short_drama.change_planner import (
    SemanticChangePlan,
    SemanticChangePlanRequest,
    plan_semantic_change,
)
from short_drama.change_executor import (
    ChangeExecutionError,
    SemanticChangeApplyRequest,
    SemanticChangeApplyResponse,
    prepare_semantic_change,
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
from short_drama.h3_video_guides import H3VideoGuideBuilder
from short_drama.episode_review import review_episode, review_scene
from short_drama.image_models import (
    AssetBriefUpdateRequest,
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReferenceSheetComposeRequest,
    AssetReviewDecision,
    AssetReviewRequest,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
    ImageGenerationTask,
    ImageNotSubmittedResolutionRequest,
    ImageTaskCreateRequest,
    ImageTaskListResponse,
    ImageTaskResponse,
)
from short_drama.language_models import (
    LanguagePromptCompileRequest,
    LanguagePromptCompileResponse,
    LanguageProviderConfigResponse,
    LanguageProviderConfigUpdate,
)
from short_drama.hierarchical_script import (
    SceneUpstreamReplanTarget,
    assemble_written_scenes,
    compact_script_retrieval_query,
    recommend_script_revision_context,
    revise_story_scene,
    write_story_scene,
)
from short_drama.media import MockMediaRenderer, TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    AudioContinuityAssessment,
    AudioInspectionRequest,
    AudioInspectionResponse,
    Asset,
    AssetKind,
    AssetVersion,
    AssetMediaType,
    CharacterProfile,
    ContentPlanningOutput,
    CreateProjectRequest,
    CreativeInputImage,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    EnqueueRequest,
    EpisodeReviewReport,
    EpisodeReviewRequest,
    EpisodeReviewResponse,
    EpisodeReviewStatus,
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
    PlanningOutlineSnapshot,
    PlanningReviewSnapshot,
    PlanningRunRecord,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanningRunStep,
    ProgressSummary,
    ProjectAggregate,
    ProjectDocument,
    ProjectListResponse,
    ProjectPlanningMetadata,
    ProjectStage,
    ReferencePack,
    ReferencePackEntry,
    ReferenceBinding,
    ReferencePlanEntry,
    ReviewRepairOwner,
    RouteEvidence,
    RunMode,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReviseScriptLineRequest,
    ReviseScriptLinesRequest,
    ScriptLineRevision,
    ScriptReviewDisposition,
    ScriptReviewDispositionAction,
    ScriptReviewReport,
    SceneTimingAssessmentRecord,
    StructuredScene,
    ScriptVersion,
    SemanticReviewStatus,
    ShotPlan,
    SourceMode,
    StoryIssueLedgerEntry,
    SubtitleCue,
    Take,
    TakeAvailability,
    TakeComparisonDecision,
    TakeComparisonRequest,
    TakeComparisonResponse,
    TaskStatus,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionRequest,
    VideoInspectionResponse,
    VoiceReferenceBinding,
    WorkerDrainResponse,
)
from short_drama.observability import log_provider_task_transition
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedSceneContract,
    ProductionPlanningResult,
    SceneShotPlan,
    assemble_multi_agent_planning_result,
    assemble_script_only_planning_result,
    story_revision_issue_indices,
    build_project_document_from_plan,
    compile_story_source_contract,
    create_production_plan,
    create_story_architecture,
    direct_story_scene,
    final_cold_read_story,
    judge_story_revisions,
    normalize_story_repair_plan_issue_ids,
    orient_story,
    plan_story_repair,
    refresh_lightweight_reference_plans,
    review_story_outline,
    revise_story_metadata,
    route_story_review,
    scene_review_handoff,
    story_revision_history_entry,
    story_revision_review,
    verify_story_revision,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_narrative_skill_prompt,
)
from short_drama.take_comparison import compare_take_candidates
from short_drama.visual_input_quality import (
    InputCompatibilityRequest,
    InputCompatibilityResponse,
    KeyframeComparisonRequest,
    KeyframeComparisonResponse,
    ReferenceVisualRerankRequest,
    ReferenceVisualRerankResponse,
    VisualInputContractError,
    VisualInputReportsResponse,
    check_input_compatibility,
    compare_keyframe_candidates,
    previous_shot_boundary_image_url,
    rerank_reference_visuals,
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
from short_drama.production_manager import (
    AgentAdvanceResponse,
    AgentCapabilityCatalog,
    AgentProjectCreateRequest,
    AgentProjectCreateResponse,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunListResponse,
    AgentRunReplanRequest,
    AgentRunView,
    AgentUserInputRequest,
    AgentWorkOrderResultRequest,
    AgentWorkOrderResultResponse,
    AgentWorkOrderView,
    ProductionManager,
    ProductionPlannerFactory,
    ProjectArtifactResolver,
    default_production_planner_factory,
)
from short_drama.repository import ShortDramaRepository
from short_drama.script_documents import (
    EpisodeOutlinePatch,
    EpisodeScenePatch,
    PlanningScriptSearchHit,
    SceneDurationRevisionConstraint,
    ScriptRevisionScope,
    apply_episode_outline_patch,
    build_planning_script_documents,
)
from short_drama.stock_assets import (
    RequestsStockAssetSearchClient,
    StockAssetImportRequest,
    StockAssetSearchClient,
    StockAssetSearchError,
    StockAssetSearchRequest,
    StockAssetSearchResponse,
    StockAssetProvider,
    StockProviderConfigListResponse,
    StockProviderConfigResponse,
    StockProviderConfigStore,
    StockProviderConfigUpdate,
)
from short_drama.tools import ToolRegistry, build_default_tool_registry
from short_drama.video_inspection import VideoInspector
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)
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
_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS = 7200
_CREATIVE_INPUT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_CREATIVE_INPUT_IMAGE_BYTES = 256 * 1024 * 1024


def _duration_milliseconds(value: float) -> int:
    return int(round(value * 1000))


def _take_is_timeline_eligible(take: Take) -> bool:
    """Keep technical availability separate from overridable creative QA."""

    return take.availability == TakeAvailability.AVAILABLE


def _timeline_clip_window(shot: ShotPlan, take: Take) -> tuple[float, float]:
    """Fit provider media to the Director's shot duration without freezing frames.

    H3 can return a longer clip than requested. Dialogue shots keep their opening so
    speech is not entered mid-line. Silent action shots whose continuity state changes
    keep the resolved tail, which is usually the edit-safe portion containing the
    declared action-out state.
    """

    if take.operation in {OperationKind.EXTEND, OperationKind.GAP_FILL}:
        return 0.0, take.duration_seconds

    clip_duration = min(shot.duration_seconds, take.duration_seconds)
    overrun = max(0.0, take.duration_seconds - clip_duration)
    has_resolved_action = (
        shot.continuity.action_in.strip() != shot.continuity.action_out.strip()
        or shot.continuity.prop_state_in.strip() != shot.continuity.prop_state_out.strip()
    )
    preserve_tail = not shot.dialogue_line_ids and has_resolved_action
    source_in_seconds = overrun if preserve_tail else 0.0
    return source_in_seconds, clip_duration


def _log_video_task_transition(
    previous: GenerationTask | None,
    task: GenerationTask,
    *,
    action: str,
    parent_task_id: str | None = None,
) -> None:
    log_provider_task_transition(
        task_kind="video",
        action=action,
        task_id=task.id,
        parent_task_id=parent_task_id,
        project_id=task.project_id,
        provider=task.provider,
        provider_task_id=task.provider_task_id,
        previous_status=(previous.status.value if previous is not None else None),
        status=task.status.value,
        error_kind=task.error_code,
    )


def _log_image_task_transition(
    previous: ImageGenerationTask | None,
    task: ImageGenerationTask,
    *,
    action: str,
    parent_task_id: str | None = None,
) -> None:
    log_provider_task_transition(
        task_kind="image",
        action=action,
        task_id=task.id,
        parent_task_id=parent_task_id,
        project_id=task.project_id,
        provider=task.provider,
        provider_task_id=task.provider_request_id,
        previous_status=(previous.status.value if previous is not None else None),
        status=task.status.value,
        error_kind=task.error_code,
    )


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
        stock_asset_client: StockAssetSearchClient | None = None,
        audio_semantic_service: AudioSemanticService | None = None,
        tool_registry: ToolRegistry | None = None,
        production_planner_factory: ProductionPlannerFactory = (
            default_production_planner_factory
        ),
    ) -> None:
        self.repository = ShortDramaRepository(database_path)
        self.workspace_root = workspace_root
        self.media_renderer = MockMediaRenderer(workspace_root)
        self.exporter = TimelineExporter(workspace_root)
        self.video_inspector = VideoInspector(workspace_root)
        self.h3_video_guide_builder = H3VideoGuideBuilder(workspace_root)
        self.audio_inspector = AudioInspector(workspace_root)
        self.audio_semantic_service = audio_semantic_service
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
        self.stock_config = StockProviderConfigStore(
            database_path.parent / "short-drama-stock-provider-settings.json"
        )
        self.stock_asset_client = stock_asset_client or RequestsStockAssetSearchClient(
            self.stock_config
        )
        self._lock = threading.RLock()
        self._planning_run_locks: dict[str, threading.Lock] = {}
        self.tool_registry = build_default_tool_registry(self, registry=tool_registry)
        self.agent_runtime_store = AgentRuntimeStore(database_path)
        self.agent_runtime = AgentRuntime(
            self.agent_runtime_store,
            ContextCompiler(
                self.agent_runtime_store,
                ProjectArtifactResolver(self.repository),
            ),
        )
        self.production_manager = ProductionManager(
            runtime=self.agent_runtime,
            tools=self.tool_registry,
            projects=self.repository,
            language_client_resolver=self._configured_language_client,
            planner_factory=production_planner_factory,
        )

    def list_projects(self) -> ProjectListResponse:
        return ProjectListResponse(
            projects=[document.project for document in self.repository.list_projects()]
        )

    def get_agent_capabilities(self) -> AgentCapabilityCatalog:
        return self.production_manager.capability_catalog()

    def create_agent_project(
        self,
        request: AgentProjectCreateRequest,
    ) -> AgentProjectCreateResponse:
        return self.production_manager.start_project_run(request)

    def create_agent_run(
        self,
        project_id: str,
        request: AgentRunCreateRequest,
    ) -> AgentRunView:
        return self.production_manager.start_run(project_id, request)

    def list_agent_runs(self, project_id: str) -> AgentRunListResponse:
        return self.production_manager.list_runs(project_id)

    def get_agent_run(self, run_id: str) -> AgentRunView:
        return self.production_manager.get_run(run_id)

    def replan_agent_run(
        self,
        run_id: str,
        request: AgentRunReplanRequest,
    ) -> AgentRunView:
        return self.production_manager.replan(run_id, request)

    def advance_agent_run(
        self,
        run_id: str,
        request: AgentRunAdvanceRequest,
    ) -> AgentAdvanceResponse:
        return self.production_manager.advance(run_id, request)

    def get_agent_work_order(self, work_order_id: str) -> AgentWorkOrderView:
        return self.production_manager.get_work_order(work_order_id)

    def execute_agent_work_order(
        self,
        work_order_id: str,
    ) -> AgentWorkOrderResultResponse:
        return self.production_manager.execute_work_order(work_order_id)

    def submit_agent_work_order_result(
        self,
        work_order_id: str,
        request: AgentWorkOrderResultRequest,
    ) -> AgentWorkOrderResultResponse:
        return self.production_manager.submit_work_order_result(work_order_id, request)

    def submit_agent_user_input(
        self,
        run_id: str,
        request: AgentUserInputRequest,
    ) -> AgentRunView:
        return self.production_manager.submit_user_input(run_id, request)

    def preview_semantic_change(
        self,
        project_id: str,
        request: SemanticChangePlanRequest,
    ) -> SemanticChangePlan:
        """Plan dependency-aware edits without mutating the project."""

        with self._lock:
            document = self._require_project_document(project_id)
            takes = self.repository.list_takes(project_id)
        return plan_semantic_change(
            document.model_copy(deep=True),
            [take.model_copy(deep=True) for take in takes],
            request,
            self._configured_language_client(),
        )

    def apply_semantic_change(
        self,
        project_id: str,
        request: SemanticChangeApplyRequest,
    ) -> SemanticChangeApplyResponse:
        """Apply an edited impact plan and invalidate its downstream dependency branch."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            try:
                prepared = prepare_semantic_change(document, request)
            except ChangeExecutionError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            affected = prepared.result.invalidated_shot_ids
            stale_take_ids = [
                take.id
                for take in self.repository.list_takes(project_id)
                if take.shot_id in affected and take.freshness == Freshness.CURRENT
            ]
            self.repository.mark_takes_stale(project_id, affected)
            prepared.result.stale_take_ids = stale_take_ids
            self.repository.save_project(prepared.document)
        return SemanticChangeApplyResponse(
            result=prepared.result,
            project=self.get_project(project_id),
        )

    def get_planning_run(self, run_id: str) -> PlanningRunResponse:
        return self._planning_run_response(self._require_planning_run(run_id))

    def get_content_planning_job(self, job_id: str) -> PlanningRunResponse:
        """Return the persisted Content Planning Job behind the compatibility name."""

        return self.get_planning_run(job_id)

    def advance_content_planning_job(
        self,
        job_id: str,
        *,
        owner_agent_run_id: str,
    ) -> PlanningRunResponse:
        """Advance one bounded planning unit on behalf of the owning AgentRun."""

        run_id = job_id
        lock = self._planning_run_lock(run_id)
        if not lock.acquire(blocking=False):
            raise WorkflowConflictError(
                f"Content planning job {run_id} is already advancing"
            )
        try:
            run = self._require_planning_run(run_id)
            if run.owner_agent_run_id != owner_agent_run_id:
                raise WorkflowConflictError(
                    "Content planning job does not belong to the requesting AgentRun"
                )
            if run.status == PlanningRunStatus.COMPLETE:
                return self._planning_run_response(run)
            try:
                run.status = PlanningRunStatus.RUNNING
                run.error_message = None
                self._advance_planning_run_once(run)
                run.updated_at = utc_now()
                self._save_planning_run_state(run)
                return self._planning_run_response(run)
            except WorkflowConflictError as exc:
                persisted = self.repository.get_planning_run(run_id)
                if persisted is None:
                    raise
                persisted.status = PlanningRunStatus.NEEDS_ATTENTION
                persisted.error_message = str(exc)
                persisted.updated_at = utc_now()
                self._save_planning_run_state(persisted)
                return self._planning_run_response(persisted)
            except Exception as exc:
                persisted = self.repository.get_planning_run(run_id)
                if persisted is None:
                    raise
                persisted.status = PlanningRunStatus.NEEDS_ATTENTION
                persisted.error_message = str(exc)
                persisted.updated_at = utc_now()
                self._save_planning_run_state(persisted)
                return self._planning_run_response(persisted)
        finally:
            lock.release()

    def advance_planning_run(self, run_id: str) -> PlanningRunResponse:
        """Compatibility helper for internal callers; product orchestration uses AgentRun."""

        run = self._require_planning_run(run_id)
        return self.advance_content_planning_job(
            run_id,
            owner_agent_run_id=run.owner_agent_run_id,
        )

    def _advance_planning_run_once(self, run: PlanningRunRecord) -> None:
        request = run.request
        client = self._configured_language_client()
        if run.current_step == PlanningRunStep.STORY_ORIENTATION:
            result = orient_story(
                request,
                client,
                retry_feedback=run.step_retry_feedback,
            )
            if client is not None and (not result.used or result.orientation is None):
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.STORY_ORIENTATION,
                    result.warnings,
                )
                return
            if result.orientation is None:
                raise WorkflowConflictError(
                    "Story Orientation did not return an orientation"
                )
            self._clear_planning_step_retry(run)
            orientation = result.orientation
            run.source_contract = orientation.source_contract.model_copy(deep=True)
            run.story_intent = orientation.story_intent.model_copy(deep=True)
            run.selected_narrative_skill_ids = [
                selection.skill_id
                for selection in orientation.selected_narrative_skills
            ]
            run.skill_selection_reasons = [
                selection.rationale
                for selection in orientation.selected_narrative_skills
            ]
            run.narrative_skill_decision = orientation.narrative_skill_decision
            run.warnings.extend(result.warnings)
            if orientation.selected_narrative_skills:
                run.warnings.append(
                    "Story Orientation Agent 已基于整段创作语义选择组合叙事技能："
                    + "；".join(
                        f"{selection.skill_id}（{selection.rationale}）"
                        for selection in orientation.selected_narrative_skills
                    )
                )
            else:
                run.warnings.append(
                    "Story Orientation Agent 的叙事技能判断："
                    + orientation.narrative_skill_decision
                )
            self._complete_planning_step(
                run,
                PlanningRunStep.STORY_ORIENTATION,
                PlanningRunStep.STORY_ARCHITECT,
            )
            return

        if run.current_step == PlanningRunStep.STORY_ARCHITECT:
            architecture = create_story_architecture(
                request,
                client,
                source_contract=run.source_contract,
                story_intent=run.story_intent,
                selected_narrative_skill_ids=tuple(
                    run.selected_narrative_skill_ids
                ),
                retry_feedback=run.step_retry_feedback,
            )
            if (
                client is not None
                and architecture.source == "deterministic structure scaffold"
            ):
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.STORY_ARCHITECT,
                    architecture.warnings,
                )
                return
            self._clear_planning_step_retry(run)
            run.outline_json = architecture.outline.model_dump_json()
            run.outline_source = architecture.source
            run.source_contract = architecture.source_contract
            run.story_intent = architecture.outline.story_intent
            run.total_scenes = len(architecture.outline.scenes)
            run.scene_script_json = []
            run.scene_timing_assessments = []
            run.next_script_scene_sequence = 1
            self._clear_scene_script_retry(run)
            run.warnings.extend(architecture.warnings)
            self._complete_planning_step(
                run,
                PlanningRunStep.STORY_ARCHITECT,
                PlanningRunStep.SCENE_WRITER,
            )
            return

        outline = self._planning_outline(run)
        if run.current_step == PlanningRunStep.SCENE_WRITER:
            written_scenes = [
                EpisodeScenePatch.model_validate_json(value)
                for value in run.scene_script_json
            ]
            if run.next_script_scene_sequence > len(outline.scenes):
                raise WorkflowConflictError(
                    "Scene Writer cursor is beyond an incomplete blueprint"
                )
            scene = outline.scenes[run.next_script_scene_sequence - 1]
            writing = write_story_scene(
                request,
                outline,
                scene,
                written_scenes,
                client,
                retry_feedback=run.scene_script_retry_feedback,
                story_intent=run.story_intent or outline.story_intent,
                source_contract=run.source_contract,
                selected_skill_prompt=compile_narrative_skill_prompt(
                    run.selected_narrative_skill_ids,
                    roles=(ProductionSkillRole.WRITER,),
                ),
            )
            if writing.retry_required:
                feedback = writing.retry_feedback or "Unknown Scene Writer failure"
                self._record_scene_script_retry(run, scene.sequence, feedback)
                return
            if writing.upstream_replan_request is not None:
                replan = writing.upstream_replan_request
                run.scene_script_json = []
                run.scene_timing_assessments = []
                run.next_script_scene_sequence = 1
                self._clear_scene_script_retry(run)
                run.step_retry_feedback = json.dumps(
                    {
                        "requested_by": "SCENE_WRITER",
                        "scene_sequence": scene.sequence,
                        "reason": replan.reason,
                        "evidence": replan.evidence,
                        "requested_change": replan.requested_change,
                        "preserve": replan.preserve,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                run.current_step = (
                    PlanningRunStep.STORY_ORIENTATION
                    if replan.target == SceneUpstreamReplanTarget.STORY_ORIENTATION
                    else PlanningRunStep.STORY_ARCHITECT
                )
                run.warnings.append(
                    f"第 {scene.sequence} 场 Scene Writer 请求重新进入 "
                    f"{replan.target.value}：{replan.reason}"
                )
                return
            self._clear_scene_script_retry(run)
            if writing.patch is None:
                raise WorkflowConflictError(
                    "Scene Writer returned neither a scene patch nor an upstream replan request"
                )
            run.scene_script_json.append(writing.patch.model_dump_json())
            timing_assessment = writing.patch.performance_timing_assessment
            if timing_assessment is not None:
                run.scene_timing_assessments.append(
                    SceneTimingAssessmentRecord(
                        scene_sequence=scene.sequence,
                        stage="INITIAL_WRITE",
                        revision_round=run.revision_round,
                        target_duration_seconds=scene.target_duration_seconds,
                        assessment=timing_assessment,
                    )
                )
            elif client is not None:
                run.warnings.append(
                    f"第 {scene.sequence} 场 Scene Writer 未留下表演时长自评；"
                    "该证据缺失已记录，但不会由代码决定创作结果。"
                )
            run.warnings.extend(writing.warnings)
            run.next_script_scene_sequence += 1
            if run.next_script_scene_sequence > len(outline.scenes):
                completed_outline = assemble_written_scenes(
                    outline,
                    [
                        EpisodeScenePatch.model_validate_json(value)
                        for value in run.scene_script_json
                    ],
                )
                run.outline_json = completed_outline.model_dump_json()
                run.total_scenes = len(completed_outline.scenes)
                created_at = utc_now()
                self.repository.save_planning_outline_snapshot(
                    run.id,
                    PlanningOutlineSnapshot(
                        revision_round=run.revision_round,
                        outline_json=run.outline_json,
                        source=run.outline_source or "Scene Writer assembly",
                        source_contract=run.source_contract,
                        story_intent=completed_outline.story_intent,
                        repair_plan=run.latest_repair_plan,
                        revision_verification=run.latest_revision_verification,
                        created_at=created_at,
                    ),
                )
                self._index_planning_outline(run, completed_outline)
                self._complete_planning_step(
                    run,
                    PlanningRunStep.SCENE_WRITER,
                    PlanningRunStep.SCRIPT_CRITIC,
                )
            return

        if run.current_step == PlanningRunStep.SCRIPT_CRITIC:
            critique = review_story_outline(
                request,
                outline,
                client,
                source_contract=run.source_contract,
                issue_ledger=run.issue_ledger,
                selected_narrative_skill_ids=tuple(
                    run.selected_narrative_skill_ids
                ),
                retry_feedback=run.step_retry_feedback,
            )
            if client is not None and (not critique.used or critique.report is None):
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.SCRIPT_CRITIC,
                    critique.warnings,
                )
                return
            self._clear_planning_step_retry(run)
            run.review_report = critique.report
            run.review_disposition = None
            run.final_cold_read_report = None
            run.critic_used = run.critic_used or critique.used
            run.warnings.extend(critique.warnings)
            if critique.report is not None:
                run.issue_ledger = self._merge_story_issue_ledger(
                    run.issue_ledger,
                    critique.report,
                    phase="首次完整剧本冷读",
                )
                next_step = self._route_current_story_review(
                    run,
                    request,
                    outline,
                    client,
                )
            else:
                next_step = self._post_script_planning_step(run)
            self._complete_planning_step(
                run,
                PlanningRunStep.SCRIPT_CRITIC,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.REVIEW_ROUTER:
            # Compatibility for jobs persisted before routing was folded into
            # the Critic unit. New jobs never stop on this step.
            if run.review_report is None:
                raise WorkflowConflictError(
                    "Planning run has no script review to route"
                )
            next_step = self._route_current_story_review(
                run,
                request,
                outline,
                client,
            )
            self._complete_planning_step(
                run,
                PlanningRunStep.REVIEW_ROUTER,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.SOURCE_CONTRACT:
            if run.source_contract is None:
                contract = compile_story_source_contract(request, client)
                if client is not None and not contract.used:
                    self._defer_planning_step_retry(
                        run,
                        PlanningRunStep.SOURCE_CONTRACT,
                        contract.warnings,
                    )
                    return
                self._clear_planning_step_retry(run)
                run.source_contract = contract.contract
                run.warnings.extend(contract.warnings)
            self._complete_planning_step(
                run,
                PlanningRunStep.SOURCE_CONTRACT,
                PlanningRunStep.STORY_REVISION,
            )
            return

        if run.current_step == PlanningRunStep.STORY_REVISION:
            if run.review_report is None or run.review_disposition is None:
                raise WorkflowConflictError(
                    "Planning run has no routed script review to revise"
                )
            if run.source_contract is None:
                # Compatibility for jobs persisted before SOURCE_CONTRACT became
                # an explicit, independently observable planning step.
                run.current_step = PlanningRunStep.SOURCE_CONTRACT
                return
            revision_review = story_revision_review(
                run.review_report,
                run.review_disposition,
            )
            planning = plan_story_repair(
                request,
                outline,
                revision_review,
                client,
                source_contract=run.source_contract,
                revision_history=self._planning_revision_history(run),
                selected_narrative_skill_ids=tuple(
                    run.selected_narrative_skill_ids
                ),
                retry_feedback=run.step_retry_feedback,
            )
            if client is not None and not planning.used:
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.STORY_REVISION,
                    planning.warnings,
                )
                return
            self._clear_planning_step_retry(run)
            run.warnings.extend(planning.warnings)
            run.source_contract = planning.source_contract
            run.latest_repair_plan = planning.plan
            run.latest_story_metadata_patch = None
            run.latest_revision_verification = None
            run.revision_base_outline_json = outline.model_dump_json()
            run.revision_scene_sequences = []
            run.next_revision_scene_index = 0
            self._clear_scene_revision_retry(run)
            run.revision_changed_scene_sequences = []
            run.latest_revision_target_scene_sequences = []
            run.latest_revision_changed_scene_sequences = []
            run.revision_repair_round = 0
            if not any(action.should_revise for action in planning.plan.actions):
                disposition = run.review_disposition
                run.review_disposition = ScriptReviewDisposition(
                    action=ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF,
                    issue_routes=[
                        (
                            route.model_copy(
                                update={
                                    "owner": ReviewRepairOwner.USER_OPTIONAL,
                                    "rationale": (
                                        "Source-contract-aware Repair Planner "
                                        "classified this routed story finding as "
                                        "non-actionable; no automatic story edit is required."
                                    ),
                                }
                            )
                            if route.owner == ReviewRepairOwner.STORY_ARCHITECT
                            else route.model_copy(deep=True)
                        )
                        for route in disposition.issue_routes
                    ],
                    rationale=(
                        "Source-contract-aware Repair Planner found no supported "
                        "story change; retain the findings as optional or downstream "
                        "handoff instead of forcing a rewrite."
                    ),
                )
                run.warnings.append(
                    "Story Repair Planner 判定当前 routed findings 均不应修订，"
                    "已降级为可选或下游交接，不强制改写剧本。"
                )
                self._append_remaining_review_warning(run)
                self._clear_revision_execution_state(run)
                self._complete_planning_step(
                    run,
                    PlanningRunStep.STORY_REVISION,
                    self._post_script_planning_step(run),
                )
                return
            next_step = PlanningRunStep.STORY_METADATA_REVISION
            self._complete_planning_step(
                run,
                PlanningRunStep.STORY_REVISION,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.STORY_METADATA_REVISION:
            self._advance_story_metadata_revision(run, request, outline, client)
            return

        if run.current_step == PlanningRunStep.REVISION_SCOPE:
            if not self._prepare_revision_scene_queue(run, outline, client):
                return
            self._clear_planning_step_retry(run)
            self._complete_planning_step(
                run,
                PlanningRunStep.REVISION_SCOPE,
                PlanningRunStep.SCENE_REVISION,
            )
            return

        if run.current_step == PlanningRunStep.SCENE_REVISION:
            self._advance_scene_revision(run, request, outline, client)
            return

        if run.current_step == PlanningRunStep.REVISION_VERIFIER:
            self._advance_revision_verifier(run, request, outline, client)
            return

        if run.current_step == PlanningRunStep.REVISION_JUDGE:
            self._run_revision_judge(run, request, client)
            self._complete_planning_step(
                run,
                PlanningRunStep.REVISION_JUDGE,
                self._post_script_planning_step(run),
            )
            return

        if run.current_step == PlanningRunStep.FINAL_COLD_READ:
            cold_read = final_cold_read_story(
                request,
                outline,
                client,
                source_contract=run.source_contract,
            )
            if client is not None and (not cold_read.used or cold_read.report is None):
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.FINAL_COLD_READ,
                    cold_read.warnings,
                )
                return
            self._clear_planning_step_retry(run)
            run.final_cold_read_used = run.final_cold_read_used or cold_read.used
            run.final_cold_read_report = cold_read.report
            run.warnings.extend(cold_read.warnings)
            if cold_read.report is not None:
                run.issue_ledger = self._merge_story_issue_ledger(
                    run.issue_ledger,
                    cold_read.report,
                    phase="最终冷读",
                )
                routing = route_story_review(
                    request,
                    outline,
                    cold_read.report,
                    None,
                )
                if (
                    routing.disposition.action
                    == ScriptReviewDispositionAction.REVISE_STORY
                ):
                    run.review_report = cold_read.report
                    run.review_disposition = routing.disposition
                    run.warnings.append(
                        "Final Cold Read 判断当前剧本仍需修订，"
                        "已按问题场次返回增量故事修订。"
                    )
                    run.current_step = PlanningRunStep.STORY_REVISION
                    return
            self._complete_planning_step(
                run,
                PlanningRunStep.FINAL_COLD_READ,
                self._post_cold_read_step(run),
            )
            return

        if run.current_step == PlanningRunStep.SCENE_DIRECTOR:
            directed_scenes = [
                SceneShotPlan.model_validate_json(value)
                for value in run.directed_scene_json
            ]
            if run.next_scene_sequence > len(outline.scenes):
                run.current_step = PlanningRunStep.FINALIZE
                return
            scene = outline.scenes[run.next_scene_sequence - 1]
            handoff = (
                directed_scenes[-1].continuity_out.model_copy(deep=True)
                if directed_scenes
                else outline.scenes[0].continuity_in.model_copy(deep=True)
            )
            direction = direct_story_scene(
                request,
                outline,
                scene,
                handoff,
                client,
                scene_review_handoff(
                    run.review_report,
                    run.review_disposition,
                    scene.sequence,
                ),
                retry_feedback=run.step_retry_feedback,
            )
            if direction.retry_required:
                feedback = (
                    direction.retry_feedback
                    or "Unknown Scene Director structural failure"
                )
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.SCENE_DIRECTOR,
                    [feedback],
                )
                return
            self._clear_planning_step_retry(run)
            if direction.scene is None:
                raise WorkflowConflictError(
                    "Scene Director completed without a directed scene"
                )
            run.directed_scene_json.append(direction.scene.model_dump_json())
            run.used_scene_repair = run.used_scene_repair or direction.repaired
            run.used_scene_fallback = (
                run.used_scene_fallback or direction.used_fallback
            )
            run.warnings.extend(direction.warnings)
            run.next_scene_sequence += 1
            if run.next_scene_sequence > len(outline.scenes):
                self._complete_planning_step(
                    run,
                    PlanningRunStep.SCENE_DIRECTOR,
                    PlanningRunStep.FINALIZE,
                )
            return

        if run.current_step == PlanningRunStep.FINALIZE:
            if (
                run.review_report is not None
                and run.review_report.issues
                and run.review_disposition is None
            ):
                warning = (
                    "FINALIZE 检测到尚未经 Review Router 归属的审校问题；"
                    "已返回路由步骤，未丢失剧本或审校报告。"
                )
                if warning not in run.warnings:
                    run.warnings.append(warning)
                self._complete_planning_step(
                    run,
                    PlanningRunStep.FINALIZE,
                    PlanningRunStep.REVIEW_ROUTER,
                )
                return
            if self._planning_run_has_unresolved_story_review(run):
                if run.review_disposition is None:
                    next_step = PlanningRunStep.REVIEW_ROUTER
                else:
                    next_step = (
                        PlanningRunStep.STORY_REVISION
                        if run.source_contract is not None
                        else PlanningRunStep.SOURCE_CONTRACT
                    )
                warning = (
                    "FINALIZE 检测到未完成的 REVISE_STORY 审校；"
                    "已恢复到增量故事修订，未写入已批准剧本。"
                )
                if warning not in run.warnings:
                    run.warnings.append(warning)
                self._complete_planning_step(
                    run,
                    PlanningRunStep.FINALIZE,
                    next_step,
                )
                return
            directed_scenes = [
                SceneShotPlan.model_validate_json(value)
                for value in run.directed_scene_json
            ]
            planning = (
                assemble_script_only_planning_result(
                    outline,
                    outline_source=run.outline_source or "unknown story architect",
                    critic_used=run.critic_used,
                    revision_rounds=run.revision_round,
                    warnings=run.warnings,
                    review_report=run.review_report,
                    review_disposition=run.review_disposition,
                    final_cold_read_report=run.final_cold_read_report,
                )
                if request.content_planning_output
                == ContentPlanningOutput.SCRIPT_ONLY
                else assemble_multi_agent_planning_result(
                    request,
                    outline,
                    directed_scenes,
                    source_contract=run.source_contract,
                    outline_source=run.outline_source or "unknown story architect",
                    critic_used=run.critic_used,
                    revision_rounds=run.revision_round,
                    used_scene_repair=run.used_scene_repair,
                    used_scene_fallback=run.used_scene_fallback,
                    warnings=run.warnings,
                    review_report=run.review_report,
                    review_disposition=run.review_disposition,
                    final_cold_read_report=run.final_cold_read_report,
                )
            )
            document = build_project_document_from_plan(
                project_id=run.project_id,
                request=request,
                planning=planning,
                created_at=utc_now(),
                planning_run_id=run.id,
                source_contract=run.source_contract,
                planning_status=PlanningRunStatus.RUNNING,
                owner_agent_run_id=run.owner_agent_run_id,
            )
            with self._lock:
                current = self.repository.get_project(run.project_id)
                if current is None:
                    raise WorkflowConflictError(
                        f"Owner project is missing: {run.project_id}"
                    )
                if not self._owner_planning_project_is_prepared(current, run):
                    self.repository.save_project(document)
            if request.content_planning_output != ContentPlanningOutput.SCRIPT_ONLY:
                self._ensure_creative_input_images_imported(run.project_id, request)
            self._complete_planning_step(
                run,
                PlanningRunStep.FINALIZE,
                PlanningRunStep.COMPLETE,
            )
            run.status = PlanningRunStatus.COMPLETE
            run.completed_at = utc_now()
            if PlanningRunStep.COMPLETE not in run.completed_steps:
                run.completed_steps.append(PlanningRunStep.COMPLETE)
            return

        if run.current_step == PlanningRunStep.COMPLETE:
            run.status = PlanningRunStatus.COMPLETE
            return
        raise WorkflowConflictError(
            f"Planning run cannot advance from {run.current_step.value}"
        )

    @staticmethod
    def _complete_planning_step(
        run: PlanningRunRecord,
        completed_step: PlanningRunStep,
        next_step: PlanningRunStep,
    ) -> None:
        if completed_step not in run.completed_steps:
            run.completed_steps.append(completed_step)
        run.current_step = next_step

    @staticmethod
    def _post_script_planning_step(run: PlanningRunRecord) -> PlanningRunStep:
        report = run.review_report
        if report is not None and not report.requires_revision:
            rationale = (
                report.followup_rationale
                or "当前完整 Critic 冷读已足以判断剧本可交付。"
            )
            warning = (
                "Script Critic 已完成整稿冷读，直接继续交付，"
                "不再自动转交独立 Final Cold Read："
                + rationale
            )
            if warning not in run.warnings:
                run.warnings.append(warning)
        return ShortDramaService._post_cold_read_step(run)

    def _route_current_story_review(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> PlanningRunStep:
        """Apply the Critic's semantic decision without a second serial Agent step."""

        if run.review_report is None:
            raise WorkflowConflictError("Planning run has no script review to route")
        routing = route_story_review(
            request,
            outline,
            run.review_report,
            client,
        )
        run.review_disposition = routing.disposition
        run.warnings.extend(routing.warnings)
        self.repository.save_planning_review_snapshot(
            run.id,
            PlanningReviewSnapshot(
                review_round=run.revision_round + 1,
                revision_round=run.revision_round,
                report=run.review_report,
                disposition=routing.disposition,
                created_at=utc_now(),
            ),
        )
        if routing.disposition.action == ScriptReviewDispositionAction.REVISE_STORY:
            return (
                PlanningRunStep.STORY_REVISION
                if run.source_contract is not None
                else PlanningRunStep.SOURCE_CONTRACT
            )
        self._append_remaining_review_warning(run)
        return self._post_script_planning_step(run)

    @staticmethod
    def _merge_story_issue_ledger(
        current: list[StoryIssueLedgerEntry],
        report: ScriptReviewReport,
        *,
        phase: str,
    ) -> list[StoryIssueLedgerEntry]:
        existing_by_id = {entry.issue_id: entry for entry in current}
        observed_ids: set[str] = set()
        merged: list[StoryIssueLedgerEntry] = []
        for issue in report.issues:
            issue_id = issue.issue_id
            if issue_id is None:
                raise WorkflowConflictError(
                    "Fresh story review issue is missing its Agent-authored semantic issue_id"
                )
            observed_ids.add(issue_id)
            previous = existing_by_id.get(issue_id)
            evidence = (
                f"{phase}；场次 {issue.scene_sequences or ['全局']}："
                f"{issue.observation}"
            )
            text_evidence = list(previous.text_evidence) if previous else []
            if evidence not in text_evidence:
                text_evidence.append(evidence)
            evolution_notes = list(previous.evolution_notes) if previous else []
            evolution = (
                f"{phase}沿用该语义问题并更新观察。"
                if previous is not None
                else f"{phase}首次记录该语义问题。"
            )
            if evolution not in evolution_notes:
                evolution_notes.append(evolution)
            merged.append(
                StoryIssueLedgerEntry(
                    issue_id=issue_id,
                    semantic_cluster=issue.category,
                    state=(
                        "当前剧本文本需要聚焦修订"
                        if issue.owner == ReviewRepairOwner.STORY_ARCHITECT
                        and report.requires_revision
                        else "作为创作取舍或下游制作建议保留"
                    ),
                    observation=issue.observation,
                    text_evidence=text_evidence,
                    fix_evidence=(
                        list(previous.fix_evidence) if previous else []
                    ),
                    evolution_notes=evolution_notes,
                    accepted_tradeoff=(
                        previous.accepted_tradeoff if previous else None
                    ),
                    handoff=(
                        previous.handoff
                        if previous is not None and previous.handoff is not None
                        else (
                            None
                            if issue.owner == ReviewRepairOwner.STORY_ARCHITECT
                            else f"交由 {issue.owner.value}：{issue.recommendation}"
                        )
                    ),
                )
            )
        for entry in current:
            if entry.issue_id in observed_ids:
                continue
            notes = list(entry.evolution_notes)
            note = f"{phase}未再次报告该问题；保留历史，不据此自动判定关闭。"
            if note not in notes:
                notes.append(note)
            merged.append(
                entry.model_copy(
                    deep=True,
                    update={
                        "state": "本轮未再次出现，等待后续证据或保留为已接受取舍",
                        "evolution_notes": notes,
                    },
                )
            )
        return merged

    @staticmethod
    def _record_revision_verification_in_issue_ledger(
        run: PlanningRunRecord,
    ) -> None:
        verification = run.latest_revision_verification
        if verification is None:
            return
        satisfied_ids = set(verification.satisfied_issue_ids)
        updated = [entry.model_copy(deep=True) for entry in run.issue_ledger]
        entry_index = {
            entry.issue_id: index for index, entry in enumerate(updated)
        }
        for issue_id in satisfied_ids:
            index = entry_index.get(issue_id)
            if index is None:
                continue
            entry = updated[index]
            fix_evidence = list(entry.fix_evidence)
            evidence = "Revision Verifier：" + verification.summary
            if evidence not in fix_evidence:
                fix_evidence.append(evidence)
            notes = list(entry.evolution_notes)
            note = (
                "Revision Verifier 已确认本轮修订满足对应修复范围，"
                "并完成终稿整体冷读。"
                if verification.accepted
                else (
                    "Revision Verifier 已确认该原问题的定向修复，"
                    "但整稿冷读又定位了需要聚焦返修的具体交付问题。"
                )
            )
            if note not in notes:
                notes.append(note)
            updated[index] = entry.model_copy(
                deep=True,
                update={
                    "state": (
                        "定向修复与终稿通读已获 Verifier 确认，"
                        "不再重复开启独立冷读"
                    )
                    if verification.accepted
                    else (
                        "原问题的定向修复已获确认，"
                        "终稿通读发现具体交付问题，继续聚焦返修"
                    ),
                    "fix_evidence": fix_evidence,
                    "evolution_notes": notes,
                },
            )

        for violation in verification.violations:
            issue_id = violation.issue_id
            if issue_id is None:
                continue
            scene_scope = violation.scene_sequences or ["全局"]
            evidence = (
                f"Revision Verifier；场次 {scene_scope}："
                f"{violation.observation}"
            )
            index = entry_index.get(issue_id)
            if index is None:
                updated.append(
                    StoryIssueLedgerEntry(
                        issue_id=issue_id,
                        semantic_cluster=violation.category,
                        state="Verifier 已定位，等待聚焦修订",
                        observation=violation.observation,
                        text_evidence=[evidence],
                        evolution_notes=[
                            "Revision Verifier 终稿通读首次记录该交付问题。"
                        ],
                        handoff=violation.repair_instruction,
                    )
                )
                entry_index[issue_id] = len(updated) - 1
                continue
            entry = updated[index]
            text_evidence = list(entry.text_evidence)
            if evidence not in text_evidence:
                text_evidence.append(evidence)
            notes = list(entry.evolution_notes)
            note = "Revision Verifier 沿用该 issue_id 确认问题仍需聚焦修订。"
            if note not in notes:
                notes.append(note)
            updated[index] = entry.model_copy(
                deep=True,
                update={
                    "semantic_cluster": violation.category,
                    "state": "Verifier 已定位，等待聚焦修订",
                    "observation": violation.observation,
                    "text_evidence": text_evidence,
                    "evolution_notes": notes,
                    "handoff": violation.repair_instruction,
                },
            )
        run.issue_ledger = updated

    @staticmethod
    def _post_cold_read_step(run: PlanningRunRecord) -> PlanningRunStep:
        if run.request.content_planning_output == ContentPlanningOutput.SCRIPT_ONLY:
            return PlanningRunStep.FINALIZE
        return PlanningRunStep.SCENE_DIRECTOR

    @staticmethod
    def _planning_run_has_unresolved_story_review(
        run: PlanningRunRecord,
    ) -> bool:
        if (
            run.latest_revision_verification is not None
            and not run.latest_revision_verification.accepted
        ):
            return True
        report = run.review_report
        disposition = run.review_disposition
        if (
            disposition is not None
            and disposition.action == ScriptReviewDispositionAction.REVISE_STORY
        ):
            return True
        if report is None:
            return False
        if disposition is None:
            return report.requires_revision
        return bool(story_revision_issue_indices(report, disposition))

    @staticmethod
    def _append_remaining_review_warning(run: PlanningRunRecord) -> None:
        report = run.review_report
        if report is None or not report.issues:
            return
        warning = (
            f"当前剧本审阅仍保留 {len(report.issues)} 项可选修改建议；"
            "已由当前 Production Skill 路由到对应制作层，"
            "用户可继续、编辑或忽略。"
        )
        if warning not in run.warnings:
            run.warnings.append(warning)

    def _run_revision_judge(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        client: LanguageProviderClient | None,
    ) -> None:
        reviews = self.repository.list_planning_review_snapshots(run.id)
        outlines = self.repository.list_planning_outline_snapshots(run.id)
        run.revision_judge = None
        if len(reviews) < 2:
            run.warnings.append(
                "Revision Judge 缺少两轮审稿证据，已保留最新版继续逐场规划。"
            )
            return
        older_review = reviews[-2]
        newer_review = reviews[-1]
        outlines_by_round = {
            snapshot.revision_round: snapshot for snapshot in outlines
        }
        older_snapshot = outlines_by_round.get(older_review.revision_round)
        newer_snapshot = outlines_by_round.get(newer_review.revision_round)
        if older_snapshot is None or newer_snapshot is None:
            run.warnings.append(
                "Revision Judge 缺少与最近两轮审稿对应的大纲快照，"
                "未替用户改选版本并继续使用最新版。"
            )
            return
        try:
            older_outline = EpisodeOutline.model_validate_json(
                older_snapshot.outline_json
            )
            newer_outline = EpisodeOutline.model_validate_json(
                newer_snapshot.outline_json
            )
        except ValueError as error:
            run.warnings.append(
                "Revision Judge 无法读取最近两版大纲，未替用户改选版本并继续使用最新版："
                + str(error)
            )
            return
        result = judge_story_revisions(
            request,
            older_revision_round=older_review.revision_round,
            older_outline=older_outline,
            older_review=older_review.report,
            newer_revision_round=newer_review.revision_round,
            newer_outline=newer_outline,
            newer_review=newer_review.report,
            client=client,
        )
        run.revision_judge = result.recommendation
        run.warnings.extend(result.warnings)
        if result.recommendation is None:
            return
        recommended = result.recommendation.recommended_revision_round
        label = "初稿" if recommended == 0 else f"第 {recommended} 轮修订稿"
        run.warnings.append(
            f"Revision Judge 建议优先比较并考虑 {label}"
            f"（{result.recommendation.confidence.value} 置信度）。"
            "当前流程未自动替换大纲，仍使用最新版继续；用户可忽略该建议。"
        )

    @staticmethod
    def _planning_outline(run: PlanningRunRecord) -> EpisodeOutline:
        if run.outline_json is None:
            raise WorkflowConflictError("Planning run has no Story Architect outline")
        return EpisodeOutline.model_validate_json(run.outline_json)

    def _index_planning_outline(
        self,
        run: PlanningRunRecord,
        outline: EpisodeOutline,
    ) -> None:
        created_at = utc_now()
        self.repository.replace_planning_script_documents(
            run.id,
            run.revision_round,
            build_planning_script_documents(
                run_id=run.id,
                revision_round=run.revision_round,
                outline=outline,
                created_at=created_at,
            ),
        )

    def _planning_revision_history(
        self,
        run: PlanningRunRecord,
    ) -> list[dict[str, object]]:
        """Return all completed pre-current review rounds for repair planning."""

        outlines = {
            snapshot.revision_round: snapshot
            for snapshot in self.repository.list_planning_outline_snapshots(run.id)
        }
        history: list[dict[str, object]] = []
        for snapshot in self.repository.list_planning_review_snapshots(run.id):
            if snapshot.revision_round >= run.revision_round:
                continue
            outline_snapshot = outlines.get(snapshot.revision_round)
            if outline_snapshot is None:
                continue
            try:
                outline = EpisodeOutline.model_validate_json(
                    outline_snapshot.outline_json
                )
            except ValueError:
                continue
            history.append(
                story_revision_history_entry(
                    revision_round=snapshot.revision_round,
                    outline=outline,
                    review=snapshot.report,
                )
            )
        return history

    @staticmethod
    def _clear_revision_execution_state(run: PlanningRunRecord) -> None:
        run.revision_base_outline_json = None
        run.revision_scene_sequences = []
        run.next_revision_scene_index = 0
        ShortDramaService._clear_scene_revision_retry(run)
        run.revision_changed_scene_sequences = []
        run.revision_repair_round = 0

    @staticmethod
    def _clear_scene_revision_retry(run: PlanningRunRecord) -> None:
        run.revision_scene_retry_feedback = None
        run.revision_scene_retry_count = 0

    @staticmethod
    def _record_scene_revision_retry(
        run: PlanningRunRecord,
        sequence: int,
        feedback: str,
    ) -> None:
        feedback = feedback[-2000:]
        run.revision_scene_retry_count += 1
        run.revision_scene_retry_feedback = feedback
        run.warnings.append(
            f"第 {run.revision_round + 1} 轮第 {sequence} 场局部修改第 "
            f"{run.revision_scene_retry_count} 次尝试失败；"
            "下次 planning.advance 将重试同一场："
            + feedback
        )

    @staticmethod
    def _duration_revision_scene_sequences(run: PlanningRunRecord) -> set[int]:
        verification = run.latest_revision_verification
        if (
            run.revision_repair_round > 0
            and verification is not None
            and not verification.accepted
        ):
            return {
                sequence
                for violation in verification.violations
                if violation.target_duration_change_required
                for sequence in violation.scene_sequences
            }
        repair_plan = run.latest_repair_plan
        if repair_plan is None:
            return set()
        return {
            sequence
            for action in repair_plan.actions
            if action.should_revise and action.target_duration_change_required
            for sequence in action.affected_scene_sequences
        }

    @classmethod
    def _scene_duration_revision_constraint(
        cls,
        run: PlanningRunRecord,
        outline: EpisodeOutline,
        scene: PlannedSceneContract,
    ) -> SceneDurationRevisionConstraint:
        allowed_sequences = cls._duration_revision_scene_sequences(run)
        allow_change = scene.sequence in allowed_sequences
        remaining_adjustable = [
            sequence
            for sequence in run.revision_scene_sequences[
                run.next_revision_scene_index + 1 :
            ]
            if sequence in allowed_sequences
        ]
        balance_here = allow_change and not remaining_adjustable
        scene_total_milliseconds = sum(
            _duration_milliseconds(item.target_duration_seconds)
            for item in outline.scenes
        )
        target_milliseconds = _duration_milliseconds(
            outline.target_duration_seconds
        )
        required_duration = (
            (
                _duration_milliseconds(scene.target_duration_seconds)
                + target_milliseconds
                - scene_total_milliseconds
            )
            / 1000
            if balance_here
            else None
        )
        return SceneDurationRevisionConstraint(
            allow_change=allow_change,
            balance_episode_on_this_scene=balance_here,
            episode_target_seconds=outline.target_duration_seconds,
            current_episode_scene_total_seconds=scene_total_milliseconds / 1000,
            required_scene_target_seconds=required_duration,
        )

    @classmethod
    def _scene_duration_candidate_error(
        cls,
        run: PlanningRunRecord,
        outline: EpisodeOutline,
        scene: PlannedSceneContract,
        patch: EpisodeScenePatch,
        constraint: SceneDurationRevisionConstraint,
    ) -> str | None:
        if not constraint.allow_change:
            return None
        target_milliseconds = _duration_milliseconds(
            constraint.episode_target_seconds
        )
        current_total_milliseconds = _duration_milliseconds(
            constraint.current_episode_scene_total_seconds
        )
        prospective_total_milliseconds = (
            current_total_milliseconds
            - _duration_milliseconds(scene.target_duration_seconds)
            + _duration_milliseconds(patch.target_duration_seconds)
        )
        remaining_adjustable = set(
            run.revision_scene_sequences[run.next_revision_scene_index + 1 :]
        ) & cls._duration_revision_scene_sequences(run)
        if not remaining_adjustable:
            if prospective_total_milliseconds == target_milliseconds:
                return None
            required_milliseconds = (
                _duration_milliseconds(scene.target_duration_seconds)
                + target_milliseconds
                - current_total_milliseconds
            )
            return (
                "Final scene-duration compensation is unbalanced: "
                f"episode_target={target_milliseconds / 1000:g}, "
                "prospective_scene_total="
                f"{prospective_total_milliseconds / 1000:g}, "
                f"scene_{scene.sequence}_target_duration_seconds must be "
                f"{required_milliseconds / 1000:g}"
            )
        remaining_current_milliseconds = sum(
            _duration_milliseconds(item.target_duration_seconds)
            for item in outline.scenes
            if item.sequence in remaining_adjustable
        )
        fixed_after_candidate_milliseconds = (
            prospective_total_milliseconds - remaining_current_milliseconds
        )
        remaining_budget_milliseconds = (
            target_milliseconds - fixed_after_candidate_milliseconds
        )
        if remaining_budget_milliseconds > 0:
            return None
        return (
            "Scene-duration reallocation leaves no positive total duration for remaining "
            f"adjustable scenes {sorted(remaining_adjustable)}: "
            f"remaining_total={remaining_budget_milliseconds / 1000:g}"
        )

    @staticmethod
    def _clear_scene_script_retry(run: PlanningRunRecord) -> None:
        run.scene_script_retry_feedback = None
        run.scene_script_retry_count = 0

    @staticmethod
    def _record_scene_script_retry(
        run: PlanningRunRecord,
        sequence: int,
        feedback: str,
    ) -> None:
        feedback = feedback[-2000:]
        run.scene_script_retry_count += 1
        run.scene_script_retry_feedback = feedback
        run.warnings.append(
            f"第 {sequence} 场 Scene Writer 第 {run.scene_script_retry_count} 次尝试失败；"
            "保留当前场次游标，下次 planning.advance 将重试同一场："
            + feedback
        )

    @staticmethod
    def _clear_planning_step_retry(run: PlanningRunRecord) -> None:
        run.step_retry_feedback = None
        run.step_retry_count = 0

    @staticmethod
    def _defer_planning_step_retry(
        run: PlanningRunRecord,
        step: PlanningRunStep,
        details: list[str],
    ) -> None:
        feedback = " ".join(item.strip() for item in details if item.strip())
        if not feedback:
            feedback = "Provider output was unavailable or failed validation."
        feedback = feedback[-2000:]
        run.step_retry_count += 1
        run.step_retry_feedback = feedback
        run.step_retry_events.append(
            f"{step.value} 第 {run.step_retry_count} 次尝试未完成；"
            f"保留当前游标，下次 planning.advance 重试同一步骤：{feedback}"
        )

    @staticmethod
    def _revision_instruction(run: PlanningRunRecord) -> str:
        repair_plan = run.latest_repair_plan
        source_contract = run.source_contract
        if repair_plan is None or source_contract is None:
            raise WorkflowConflictError(
                "Planning run has no persisted repair plan or source contract"
            )
        actions = [
            action for action in repair_plan.actions if action.should_revise
        ]
        original_instruction = {
            "repair_summary": repair_plan.summary,
            "source_promise": repair_plan.source_promise,
            "invariants": repair_plan.invariants,
            "authority_order": (
                "explicit_story_source_contract、StoryIntent、EndingContract "
                "与实际可读剧本优先；invariants 只保留不冲突的"
                "已成立内容，不得替剧本辩护观众看不到的因果。"
            ),
            "actions": [action.model_dump(mode="json") for action in actions],
            "explicit_story_source_contract": source_contract.model_dump(
                mode="json"
            ),
        }
        if (
            run.revision_repair_round == 0
            or run.latest_revision_verification is None
        ):
            return json.dumps(original_instruction, ensure_ascii=False)
        return json.dumps(
            {
                "original_repair_instruction": original_instruction,
                "revision_verification": (
                    run.latest_revision_verification.model_dump(mode="json")
                ),
                "instruction": (
                    "仅修复 Verifier 已定位的终稿问题，保留与该问题不冲突的"
                    "已成立局部修改；SourceContract、StoryIntent、EndingContract "
                    "与实际可读终稿优先于旧 repair invariant。"
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _normalize_active_repair_issue_ids(run: PlanningRunRecord) -> None:
        """Upgrade an old in-flight repair plan using its routed Critic review."""

        if (
            run.latest_repair_plan is None
            or run.review_report is None
            or run.review_disposition is None
        ):
            return
        revision_review = story_revision_review(
            run.review_report,
            run.review_disposition,
        )
        if not revision_review.issues:
            return
        run.latest_repair_plan = normalize_story_repair_plan_issue_ids(
            revision_review,
            run.latest_repair_plan,
        )

    def _revision_search_context(
        self,
        run: PlanningRunRecord,
        outline: EpisodeOutline,
    ) -> tuple[str, list[PlanningScriptSearchHit]]:
        repair_plan = run.latest_repair_plan
        if repair_plan is None:
            raise WorkflowConflictError(
                "Planning run has no persisted story repair plan"
            )
        actions = [
            action for action in repair_plan.actions if action.should_revise
        ]
        query = compact_script_retrieval_query(
            "\n".join(
                [
                    repair_plan.summary,
                    *[
                        " ".join(
                            [
                                action.recommended_change,
                                *action.remove_or_simplify,
                            ]
                        )
                        for action in actions
                    ],
                ]
            )
        )
        # Rebuild retrieval documents from the persisted run cursor before the
        # model call. If an earlier process stopped after a provider response but
        # before the run was committed, this removes any ahead-of-cursor index.
        self._index_planning_outline(run, outline)
        hits = self.repository.search_planning_script_documents(
            run.id,
            run.revision_round,
            query,
            limit=12,
            include_story_bible=True,
        )
        return query, hits

    def _prepare_revision_scene_queue(
        self,
        run: PlanningRunRecord,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> bool:
        if (
            run.latest_repair_plan is None
            or run.revision_base_outline_json is None
        ):
            raise WorkflowConflictError(
                "Planning run has no repair plan ready for scope selection"
            )
        all_sequences = [scene.sequence for scene in outline.scenes]
        candidate_sequences = sorted(
            {
                sequence
                for action in run.latest_repair_plan.actions
                if action.should_revise
                for sequence in action.affected_scene_sequences
            }
        )
        if not candidate_sequences:
            query, hits = self._revision_search_context(run, outline)
            scope_result = recommend_script_revision_context(
                self._revision_instruction(run),
                outline,
                hits,
                client,
                retrieval_query=query,
                candidate_scene_sequences=[],
            )
            if scope_result.plan is None:
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.REVISION_SCOPE,
                    [
                        scope_result.retry_feedback
                        or "Revision Scope Agent did not produce a semantic scene scope."
                    ],
                )
                return False
            run.warnings.extend(scope_result.warnings)
            scope_plan = scope_result.plan
            if scope_plan.scope == ScriptRevisionScope.FULL_OUTLINE:
                selected = all_sequences
                run.warnings.append(
                    "Scope Agent 建议 FULL_OUTLINE；"
                    "已转换为全场逐场修订队列，不会整集重写。"
                )
            else:
                selected = sorted(scope_plan.affected_scene_sequences)
        elif set(candidate_sequences) == set(all_sequences):
            selected = all_sequences
            run.warnings.append(
                "Story Repair Planner 已定位全部场次；"
                "已将它们按顺序排入逐场修订队列。"
            )
        else:
            query, hits = self._revision_search_context(run, outline)
            scope_plan = recommend_script_revision_context(
                self._revision_instruction(run),
                outline,
                hits,
                client,
                retrieval_query=query,
                candidate_scene_sequences=candidate_sequences,
            )
            if scope_plan.plan is None:
                self._defer_planning_step_retry(
                    run,
                    PlanningRunStep.REVISION_SCOPE,
                    [
                        scope_plan.retry_feedback
                        or "Revision Scope Agent did not produce a semantic scene scope."
                    ],
                )
                return False
            run.warnings.extend(scope_plan.warnings)
            resolved_scope_plan = scope_plan.plan
            if resolved_scope_plan.scope == ScriptRevisionScope.FULL_OUTLINE:
                selected = all_sequences
                run.warnings.append(
                    "Scope Agent 建议 FULL_OUTLINE；"
                    "已转换为全场逐场修订队列，不会整集重写。"
                )
            else:
                selected = sorted(resolved_scope_plan.affected_scene_sequences)
        duration_sequences = self._duration_revision_scene_sequences(run)
        if duration_sequences:
            # A scope recommendation may narrow ordinary content edits, but it
            # cannot omit a typed duration donor/recipient required to balance
            # the episode after sequential scene patches.
            selected = sorted(set(selected) | duration_sequences)
        known_sequences = set(all_sequences)
        unknown = sorted(set(selected) - known_sequences)
        if unknown:
            raise WorkflowConflictError(
                "Revision scope references unknown scenes: "
                + ", ".join(str(sequence) for sequence in unknown)
            )
        if not selected:
            raise WorkflowConflictError("Revision scope produced an empty scene queue")
        run.revision_scene_sequences = list(dict.fromkeys(selected))
        run.latest_revision_target_scene_sequences = list(
            dict.fromkeys(
                [
                    *run.latest_revision_target_scene_sequences,
                    *run.revision_scene_sequences,
                ]
            )
        )
        run.next_revision_scene_index = 0
        self._clear_scene_revision_retry(run)
        return True

    def _advance_story_metadata_revision(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> None:
        if (
            run.latest_repair_plan is None
            or run.source_contract is None
            or run.revision_base_outline_json is None
        ):
            raise WorkflowConflictError(
                "Planning run has no active repair plan for metadata revision"
            )
        self._normalize_active_repair_issue_ids(run)
        is_verifier_repair = run.revision_repair_round > 0
        if is_verifier_repair and not run.revision_scene_sequences:
            raise WorkflowConflictError(
                "Planning run has no persisted scene queue after verifier rejection"
            )
        result = revise_story_metadata(
            request,
            outline,
            run.latest_repair_plan,
            run.source_contract,
            client,
            verification=(
                run.latest_revision_verification
                if is_verifier_repair
                else None
            ),
            selected_narrative_skill_ids=tuple(
                run.selected_narrative_skill_ids
            ),
            retry_feedback=run.step_retry_feedback,
        )
        if client is not None and not result.used:
            self._defer_planning_step_retry(
                run,
                PlanningRunStep.STORY_METADATA_REVISION,
                result.warnings,
            )
            return
        self._clear_planning_step_retry(run)
        run.warnings.extend(result.warnings)
        run.latest_story_metadata_patch = result.patch
        if result.revised:
            run.outline_json = result.outline.model_dump_json()
            run.total_scenes = len(result.outline.scenes)
        self._complete_planning_step(
            run,
            PlanningRunStep.STORY_METADATA_REVISION,
            (
                PlanningRunStep.SCENE_REVISION
                if is_verifier_repair
                else PlanningRunStep.REVISION_SCOPE
            ),
        )

    def _advance_scene_revision(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> None:
        self._normalize_active_repair_issue_ids(run)
        repair_plan = run.latest_repair_plan
        if repair_plan is None or run.revision_base_outline_json is None:
            raise WorkflowConflictError(
                "Planning run has no active incremental story revision"
            )
        queue = run.revision_scene_sequences
        cursor = run.next_revision_scene_index
        if not queue or cursor >= len(queue):
            raise WorkflowConflictError(
                "Scene revision cursor is beyond an incomplete revision queue"
            )
        sequence = queue[cursor]
        scene = next(
            (item for item in outline.scenes if item.sequence == sequence),
            None,
        )
        if scene is None:
            raise WorkflowConflictError(
                f"Scene revision target no longer exists: {sequence}"
            )
        duration_constraint = self._scene_duration_revision_constraint(
            run,
            outline,
            scene,
        )
        _query, hits = self._revision_search_context(run, outline)
        result = revise_story_scene(
            request,
            outline,
            scene,
            self._revision_instruction(run),
            queue,
            hits,
            duration_constraint,
            client,
            retry_feedback=run.revision_scene_retry_feedback,
            story_intent=run.story_intent or outline.story_intent,
            selected_skill_prompt=compile_narrative_skill_prompt(
                run.selected_narrative_skill_ids,
                roles=(ProductionSkillRole.WRITER,),
            ),
            previous_verification=(
                run.latest_revision_verification
                if run.revision_repair_round > 0
                and run.latest_revision_verification is not None
                and not run.latest_revision_verification.accepted
                else None
            ),
        )
        if result.retry_required:
            feedback = result.retry_feedback or "Unknown scene revision failure"
            self._record_scene_revision_retry(run, sequence, feedback)
            return
        run.warnings.extend(result.warnings)
        timing_assessment = result.patch.performance_timing_assessment
        if timing_assessment is not None:
            run.scene_timing_assessments.append(
                SceneTimingAssessmentRecord(
                    scene_sequence=sequence,
                    stage="REVISION",
                    revision_round=run.revision_round + 1,
                    target_duration_seconds=result.patch.target_duration_seconds,
                    assessment=timing_assessment,
                )
            )
        elif client is not None:
            run.warnings.append(
                f"第 {sequence} 场局部 Writer 未留下修订后表演时长自评；"
                "该证据缺失已记录，但不会由代码决定创作结果。"
            )
        if result.revised:
            duration_error = self._scene_duration_candidate_error(
                run,
                outline,
                scene,
                result.patch,
                duration_constraint,
            )
            if duration_error is not None:
                self._record_scene_revision_retry(
                    run,
                    sequence,
                    duration_error,
                )
                return
            try:
                revised = apply_episode_outline_patch(
                    outline,
                    EpisodeOutlinePatch(
                        base_revision_round=run.revision_round,
                        rationale=repair_plan.summary,
                        scene_patches=[result.patch],
                        preserve=repair_plan.invariants,
                    ),
                )
            except ValueError as error:
                self._record_scene_revision_retry(run, sequence, str(error))
                return
            run.outline_json = revised.model_dump_json()
            run.total_scenes = len(revised.scenes)
            if sequence not in run.revision_changed_scene_sequences:
                run.revision_changed_scene_sequences.append(sequence)
            if sequence not in run.latest_revision_changed_scene_sequences:
                run.latest_revision_changed_scene_sequences.append(sequence)
        self._clear_scene_revision_retry(run)
        run.next_revision_scene_index += 1
        if run.next_revision_scene_index >= len(queue):
            self._complete_planning_step(
                run,
                PlanningRunStep.SCENE_REVISION,
                PlanningRunStep.REVISION_VERIFIER,
            )

    def _advance_revision_verifier(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> None:
        if (
            run.revision_base_outline_json is None
            or run.review_report is None
            or run.latest_repair_plan is None
            or run.source_contract is None
        ):
            raise WorkflowConflictError(
                "Planning run has incomplete state for revision verification"
            )
        self._normalize_active_repair_issue_ids(run)
        assert run.latest_repair_plan is not None
        original = EpisodeOutline.model_validate_json(
            run.revision_base_outline_json
        )
        previous_verification = (
            run.latest_revision_verification
            if run.latest_revision_verification is not None
            and not run.latest_revision_verification.accepted
            else None
        )
        revision_review = story_revision_review(
            run.review_report,
            run.review_disposition,
        ) if run.review_disposition is not None else run.review_report
        verification_result = verify_story_revision(
            request,
            original,
            outline,
            revision_review,
            run.latest_repair_plan,
            run.source_contract,
            client,
            selected_narrative_skill_ids=tuple(
                run.selected_narrative_skill_ids
            ),
            retry_feedback=run.step_retry_feedback,
            previous_verification=previous_verification,
            issue_ledger=run.issue_ledger,
        )
        if client is not None and not verification_result.used:
            self._defer_planning_step_retry(
                run,
                PlanningRunStep.REVISION_VERIFIER,
                verification_result.warnings,
            )
            return
        self._clear_planning_step_retry(run)
        run.warnings.extend(verification_result.warnings)
        run.latest_revision_verification = verification_result.verification
        self._record_revision_verification_in_issue_ledger(run)
        verification = verification_result.verification
        if verification is None:
            raise WorkflowConflictError(
                "Story Revision Verifier did not return an accepted result"
            )
        if not verification.accepted:
            violation_sequences = sorted(
                {
                    sequence
                    for violation in verification.violations
                    for sequence in violation.scene_sequences
                }
            )
            if not violation_sequences:
                violation_sequences = list(run.revision_scene_sequences)
            if not violation_sequences:
                violation_sequences = [scene.sequence for scene in outline.scenes]
            run.revision_scene_sequences = violation_sequences
            run.latest_revision_target_scene_sequences = list(
                dict.fromkeys(
                    [
                        *run.latest_revision_target_scene_sequences,
                        *violation_sequences,
                    ]
                )
            )
            run.next_revision_scene_index = 0
            self._clear_scene_revision_retry(run)
            run.revision_repair_round += 1
            run.warnings.append(
                f"Revision Verifier 已定位回归问题；第 "
                f"{run.revision_repair_round} 次定向修复将按场次 "
                f"{violation_sequences} 逐场执行。"
            )
            self._complete_planning_step(
                run,
                PlanningRunStep.REVISION_VERIFIER,
                PlanningRunStep.STORY_METADATA_REVISION,
            )
            return

        if run.review_disposition is not None:
            run.review_disposition = run.review_disposition.model_copy(
                deep=True,
                update={
                    "action": (
                        ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
                        if run.review_report.issues
                        else ScriptReviewDispositionAction.CONTINUE
                    ),
                    "issue_routes": [
                        (
                            route.model_copy(
                                deep=True,
                                update={
                                    "owner": ReviewRepairOwner.USER_OPTIONAL,
                                    "rationale": (
                                        "Revision Verifier 已确认对应剧本修复；"
                                        "保留为历史记录，不再自动重开同一问题。"
                                    ),
                                },
                            )
                            if route.owner == ReviewRepairOwner.STORY_ARCHITECT
                            else route.model_copy(deep=True)
                        )
                        for route in run.review_disposition.issue_routes
                    ],
                    "rationale": (
                        "Revision Verifier 已语义确认本轮剧本修复；"
                        "已解决的故事问题不再触发重复 Critic 或循环修订，"
                        "其余下游建议继续保留。"
                    ),
                },
            )

        run.latest_revision_target_scene_sequences = list(
            dict.fromkeys(
                [
                    *run.latest_revision_target_scene_sequences,
                    *run.revision_scene_sequences,
                ]
            )
        )
        run.latest_revision_changed_scene_sequences = list(
            dict.fromkeys(
                [
                    *run.latest_revision_changed_scene_sequences,
                    *run.revision_changed_scene_sequences,
                ]
            )
        )
        selected = list(run.revision_changed_scene_sequences)
        revised = outline != original
        repair_round = run.revision_repair_round
        if revised:
            run.revision_round += 1
            self.repository.save_planning_outline_snapshot(
                run.id,
                PlanningOutlineSnapshot(
                    revision_round=run.revision_round,
                    outline_json=outline.model_dump_json(),
                    source=f"Incremental scene revision {run.revision_round}",
                    source_contract=run.source_contract,
                    story_intent=outline.story_intent,
                    repair_plan=run.latest_repair_plan,
                    revision_verification=verification,
                    created_at=utc_now(),
                ),
            )
            self._index_planning_outline(run, outline)
            if repair_round:
                run.warnings.append(
                    f"第 {run.revision_round} 轮局部 Writer 已根据 "
                    f"Revision Verifier 定向修复 {repair_round} 次。"
                )
            run.warnings.append(
                f"第 {run.revision_round} 轮已逐场应用剧本 Patch；"
                f"产生修改的场次为 {selected}，未调用整集重写。"
            )
        else:
            run.warnings.append(
                f"第 {run.revision_round + 1} 轮逐场 Writer 未产生可采用 "
                "Patch；Revision Verifier 已接受当前修复范围。"
            )
        rationale = (
            verification.followup_rationale
            or "修复事项和从头到尾的自然冷读均已通过。"
        )
        run.warnings.append(
            "Revision Verifier 已同时完成定向修复验收与终稿整体冷读，"
            "直接继续交付，不再自动转交独立 Final Cold Read："
            + rationale
        )
        next_step = self._post_cold_read_step(run)
        self._clear_revision_execution_state(run)
        self._complete_planning_step(
            run,
            PlanningRunStep.REVISION_VERIFIER,
            next_step,
        )

    def _planning_run_response(self, run: PlanningRunRecord) -> PlanningRunResponse:
        document = self.repository.get_project(run.project_id)
        if document is None:
            raise WorkflowConflictError(f"Owner project is missing: {run.project_id}")
        return PlanningRunResponse(run=run, project=self._aggregate(document))

    def _save_planning_run_state(self, run: PlanningRunRecord) -> None:
        """Persist an owned run and its project-visible status as one DB commit."""

        with self._lock:
            document = self.repository.get_project(run.project_id)
            if document is None:
                raise WorkflowConflictError(
                    f"Owner project is missing: {run.project_id}"
                )
            existing = document.planning
            planner = existing.planner if existing is not None else None
            if planner is None and run.outline_source is not None:
                planner = f"multi-agent:{run.outline_source}"
            document.planning = ProjectPlanningMetadata(
                run_id=run.id,
                status=run.status,
                planner=planner,
                warnings=list(dict.fromkeys(run.warnings)),
                owner_agent_run_id=run.owner_agent_run_id,
                planning_job_kind="CONTENT_PLANNING",
            )
            document.project.updated_at = run.updated_at
            self.repository.save_planning_run_with_project(run, document)

    @staticmethod
    def _owner_planning_project_is_prepared(
        document: ProjectDocument,
        run: PlanningRunRecord,
    ) -> bool:
        """Recognize a FINALIZE payload left ready by an interrupted attempt."""

        expected_stage = (
            ProjectStage.SCRIPT_READY
            if run.request.content_planning_output
            == ContentPlanningOutput.SCRIPT_ONLY
            else ProjectStage.PLANNED
        )
        if document.project.stage != expected_stage:
            return False
        if document.planning is None or document.planning.run_id != run.id:
            return False
        return any(
            version.planning_run_id == run.id
            for version in document.script_versions
        )

    def _require_planning_run(self, run_id: str) -> PlanningRunRecord:
        run = self.repository.get_planning_run(run_id)
        if run is None:
            raise ArtifactNotFoundError(run_id)
        return run

    def _planning_run_lock(self, run_id: str) -> threading.Lock:
        with self._lock:
            lock = self._planning_run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._planning_run_locks[run_id] = lock
            return lock

    def search_asset_catalog(
        self,
        request: AssetCatalogSearchRequest,
        *,
        current_project_id: str | None = None,
    ) -> AssetCatalogSearchResponse:
        if current_project_id is not None:
            self._require_project_document(current_project_id)
        return search_asset_catalog(
            self.repository.list_projects(),
            current_project_id=current_project_id,
            request=request,
        )

    def get_stock_provider_configs(self) -> StockProviderConfigListResponse:
        return self.stock_config.list_response()

    def update_stock_provider_config(
        self,
        provider: StockAssetProvider,
        request: StockProviderConfigUpdate,
    ) -> StockProviderConfigResponse:
        return self.stock_config.update(provider, request)

    def search_stock_assets(
        self,
        request: StockAssetSearchRequest,
    ) -> StockAssetSearchResponse:
        try:
            return self.stock_asset_client.search(request)
        except StockAssetSearchError as exc:
            raise ProviderOperationError(str(exc)) from exc

    def inspect_asset_coverage(self, project_id: str) -> AssetCoverageResponse:
        document = self._require_project_document(project_id)
        missing = recommended_reusable_reference_views(document)
        return AssetCoverageResponse(
            project_id=project_id,
            ready=not missing,
            missing_views=missing,
        )

    def import_catalog_asset(
        self,
        project_id: str,
        request: AssetCatalogImportRequest,
    ) -> AssetMutationResponse:
        target_document = self._require_project_mutation_allowed(project_id)
        source_document = self._require_project_document(request.source_project_id)
        source_asset = self._require_asset_in_document(
            source_document,
            request.source_asset_id,
        )
        source_version = next(
            (
                item
                for item in source_asset.versions
                if item.id == request.source_version_id
            ),
            None,
        )
        if source_version is None:
            raise ArtifactNotFoundError(request.source_version_id)
        if (
            source_asset.approved_version_id != source_version.id
            or source_version.approval_status != ApprovalStatus.APPROVED
            or source_version.media_type != AssetMediaType.IMAGE
        ):
            raise WorkflowConflictError(
                "Only a current approved catalog image can be imported"
            )
        target_asset: Asset
        if request.target_asset_id is None:
            created = self.asset_workbench.create_asset(
                project_id,
                AssetCreateRequest(
                    kind=source_asset.kind,
                    name=request.name or source_asset.name,
                    descriptor=request.descriptor or source_version.descriptor,
                ),
            )
            target_document = self._require_project_document(project_id)
            target_asset = next(
                item
                for item in target_document.assets
                if item.current_version_id == created.id
            )
        else:
            target_asset = self._require_asset_in_document(
                target_document,
                request.target_asset_id,
            )
            if (
                target_asset.kind != AssetKind.REFERENCE
                and target_asset.kind != source_asset.kind
            ):
                raise WorkflowConflictError(
                    "Catalog source kind does not match the target asset"
                )
        version = self.asset_workbench.clone_catalog_image(
            project_id,
            target_asset.id,
            source_project_id=source_document.project.id,
            source_asset_id=source_asset.id,
            source_asset_name=source_asset.name,
            source_version=source_version,
            descriptor=request.descriptor or source_version.descriptor,
        )
        refreshed = self.get_project(project_id)
        asset = next(item for item in refreshed.document.assets if item.id == target_asset.id)
        return AssetMutationResponse(asset=asset, version=version, project=refreshed)

    def initialize_fixture(self) -> ProjectAggregate:
        with self._lock:
            fixture = build_dont_trust_me_fixture(utc_now())
            if not self.repository.project_exists(fixture.project.id):
                self.repository.save_project(fixture)
            return self.get_project(fixture.project.id)

    def create_project(
        self,
        request: CreateProjectRequest,
        *,
        project_id: str | None = None,
        owner_agent_run_id: str | None = None,
    ) -> ProjectAggregate:
        self._preflight_creative_input_images(request)
        if (
            owner_agent_run_id is not None
            and self._configured_language_client() is not None
        ):
            return self._create_agent_owned_planning_project(
                request,
                project_id=project_id,
                owner_agent_run_id=owner_agent_run_id,
            )
        project, _planning = self._create_planned_project(
            request,
            project_id=project_id,
            preflight=False,
        )
        created_project_id = project.document.project.id
        try:
            self._import_creative_input_images(created_project_id, request)
            return self.get_project(created_project_id)
        except Exception:
            self._discard_project_creation(created_project_id)
            raise

    def _create_planned_project(
        self,
        request: CreateProjectRequest,
        *,
        project_id: str | None = None,
        preflight: bool = True,
    ) -> tuple[ProjectAggregate, ProductionPlanningResult]:
        if preflight:
            self._preflight_creative_input_images(request)
        resolved_project_id = project_id or f"project-{uuid4().hex[:12]}"
        if project_id is not None and self.repository.project_exists(resolved_project_id):
            raise WorkflowConflictError(
                f"Project already exists: {resolved_project_id}"
            )
        # Language-authored project content is always owned by an AgentRun and
        # advanced through its persisted Content Planning Job.  The direct
        # compatibility path only creates a local scaffold; it must never hide
        # a multi-step generation or whole-script rewrite inside one call.
        planning = create_production_plan(request, None)
        created_at = utc_now()
        document = build_project_document_from_plan(
            project_id=resolved_project_id,
            request=request,
            planning=planning,
            created_at=created_at,
        )
        with self._lock:
            inserted = self.repository.insert_project(document)
        if not inserted:
            raise WorkflowConflictError(
                f"Project already exists: {resolved_project_id}"
            )
        try:
            return self._aggregate(document), planning
        except Exception:
            self._discard_project_creation(resolved_project_id)
            raise

    def _create_agent_owned_planning_project(
        self,
        request: CreateProjectRequest,
        *,
        project_id: str | None,
        owner_agent_run_id: str,
    ) -> ProjectAggregate:
        resolved_project_id = project_id or f"project-{uuid4().hex[:12]}"
        if project_id is not None and self.repository.project_exists(resolved_project_id):
            raise WorkflowConflictError(
                f"Project already exists: {resolved_project_id}"
            )
        run_id = f"planning-run-{uuid4().hex}"
        now = utc_now()
        run = PlanningRunRecord(
            id=run_id,
            status=PlanningRunStatus.RUNNING,
            current_step=PlanningRunStep.STORY_ORIENTATION,
            request=request,
            project_id=resolved_project_id,
            owner_agent_run_id=owner_agent_run_id,
            created_at=now,
            updated_at=now,
        )
        provisional = create_production_plan(request, None)
        document = build_project_document_from_plan(
            project_id=resolved_project_id,
            request=request,
            planning=provisional,
            created_at=now,
            planning_run_id=run.id,
            planning_status=PlanningRunStatus.RUNNING,
            owner_agent_run_id=owner_agent_run_id,
        )
        document.project.stage = ProjectStage.INPUT
        for version in document.script_versions:
            version.approval_status = ApprovalStatus.DRAFT
        for version in document.bible_versions:
            version.approval_status = ApprovalStatus.DRAFT
        document.planning = ProjectPlanningMetadata(
            run_id=run.id,
            status=PlanningRunStatus.RUNNING,
            owner_agent_run_id=owner_agent_run_id,
            planning_job_kind="CONTENT_PLANNING",
        )
        with self._lock:
            inserted = self.repository.insert_project_with_planning_run(document, run)
        if not inserted:
            raise WorkflowConflictError(
                f"Project already exists: {resolved_project_id}"
            )
        try:
            return self._aggregate(document)
        except Exception:
            self._discard_project_creation(
                resolved_project_id,
                planning_run_id=run.id,
            )
            raise

    def _configured_language_client(self) -> LanguageProviderClient | None:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        return (
            self.language_provider_factory(api_key, base_url)
            if api_key or is_loopback_openai_url(base_url)
            else None
        )

    @staticmethod
    def _preflight_creative_input_images(request: CreateProjectRequest) -> None:
        """Reject unusable local visual seeds before planning or persistence starts."""

        for image in request.input_images:
            if image.local_path is None:
                continue
            source_path = Path(image.local_path).expanduser().resolve()
            if not source_path.is_file():
                raise WorkflowConflictError(
                    f"Local asset does not exist (input image): {source_path}"
                )
            try:
                size_bytes = source_path.stat().st_size
            except OSError as exc:
                raise WorkflowConflictError(
                    f"Local input image cannot be read: {source_path}"
                ) from exc
            if size_bytes <= 0 or size_bytes > _MAX_CREATIVE_INPUT_IMAGE_BYTES:
                raise WorkflowConflictError(
                    "Local input image must be between 1 byte and 256 MB"
                )
            if source_path.suffix.lower() not in _CREATIVE_INPUT_IMAGE_EXTENSIONS:
                raise WorkflowConflictError(
                    f"Unsupported local input image type: {source_path.suffix.lower() or 'none'}"
                )
            try:
                with Image.open(source_path) as opened:
                    opened.verify()
            except (OSError, UnidentifiedImageError) as exc:
                raise WorkflowConflictError(
                    f"Invalid local input image: {source_path.name}"
                ) from exc

    def _discard_project_creation(
        self,
        project_id: str,
        *,
        planning_run_id: str | None = None,
    ) -> None:
        """Compensate a failed first-create without touching established projects."""

        self.repository.delete_project_creation(
            project_id,
            planning_run_id=planning_run_id,
        )
        workspace_root = self.workspace_root.resolve()
        project_root = (workspace_root / project_id).resolve()
        if project_root.parent == workspace_root and project_root.is_dir():
            shutil.rmtree(project_root)

    @staticmethod
    def _creative_input_asset_name(
        index: int,
        image: CreativeInputImage,
    ) -> str:
        source_name = (
            Path(image.local_path).stem
            if image.local_path is not None
            else f"网络参考图 {index}"
        )
        return f"输入视觉种子 {index} · {source_name}"[:120]

    @staticmethod
    def _creative_input_source_uri(image: CreativeInputImage) -> str:
        if image.local_path is not None:
            return str(Path(image.local_path).expanduser().resolve())
        assert image.external_url is not None
        return image.external_url.strip()

    def _ensure_creative_input_images_imported(
        self,
        project_id: str,
        request: CreateProjectRequest,
    ) -> None:
        """Reconcile an interrupted FINALIZE without duplicating visual seeds."""

        if not request.input_images:
            return
        document = self._require_project_document(project_id)
        expected_names = {
            self._creative_input_asset_name(index, image)
            for index, image in enumerate(request.input_images, start=1)
        }
        imported_versions: list[tuple[str, str]] = []
        for index, image in enumerate(request.input_images, start=1):
            expected_name = self._creative_input_asset_name(index, image)
            matches = [
                asset
                for asset in document.assets
                if asset.kind == AssetKind.REFERENCE and asset.name == expected_name
            ]
            if len(matches) != 1:
                break
            asset = matches[0]
            approved = next(
                (
                    version
                    for version in asset.versions
                    if version.id == asset.approved_version_id
                ),
                None,
            )
            if (
                approved is None
                or approved.approval_status != ApprovalStatus.APPROVED
                or approved.media_type != AssetMediaType.IMAGE
                or approved.source is None
                or approved.source.source_uri
                != self._creative_input_source_uri(image)
            ):
                break
            imported_versions.append((approved.id, image.descriptor))
        if len(imported_versions) == len(request.input_images):
            self._bind_creative_input_versions(
                project_id,
                request,
                imported_versions,
            )
            return

        stale_assets = [
            asset
            for asset in document.assets
            if asset.kind == AssetKind.REFERENCE and asset.name in expected_names
        ]
        if stale_assets:
            stale_asset_ids = {asset.id for asset in stale_assets}
            stale_version_ids = {
                version.id
                for asset in stale_assets
                for version in asset.versions
            }
            self._discard_asset_directories(project_id, stale_asset_ids)
            with self._lock:
                refreshed = self._require_project_document(project_id)
                refreshed.assets = [
                    asset
                    for asset in refreshed.assets
                    if asset.id not in stale_asset_ids
                ]
                for plan in refreshed.reference_plan:
                    plan.bindings = [
                        binding
                        for binding in plan.bindings
                        if binding.target_id not in stale_version_ids
                    ]
                refreshed.project.updated_at = utc_now()
                self.repository.save_project(refreshed)
        self._import_creative_input_images(project_id, request)

    def _import_creative_input_images(
        self,
        project_id: str,
        request: CreateProjectRequest,
    ) -> None:
        if not request.input_images:
            return
        baseline = self._require_project_document(project_id).model_copy(deep=True)
        baseline_asset_ids = {asset.id for asset in baseline.assets}
        try:
            self._import_creative_input_images_unchecked(project_id, request)
        except Exception:
            current = self.repository.get_project(project_id)
            created_asset_ids: set[str] = set()
            if current is not None:
                created_asset_ids = {
                    asset.id
                    for asset in current.assets
                    if asset.id not in baseline_asset_ids
                }
            self.repository.save_project(baseline)
            self._discard_asset_directories(project_id, created_asset_ids)
            raise

    def _discard_asset_directories(
        self,
        project_id: str,
        asset_ids: set[str],
    ) -> None:
        project_root = (self.workspace_root.resolve() / project_id).resolve()
        if project_root.parent != self.workspace_root.resolve():
            return
        assets_root = (project_root / "assets").resolve()
        for asset_id in asset_ids:
            asset_root = (assets_root / asset_id).resolve()
            if asset_root.parent == assets_root and asset_root.is_dir():
                shutil.rmtree(asset_root)

    def _import_creative_input_images_unchecked(
        self,
        project_id: str,
        request: CreateProjectRequest,
    ) -> None:
        imported_versions: list[tuple[str, str]] = []
        for index, image in enumerate(request.input_images, start=1):
            created = self._create_asset_unchecked(
                project_id,
                AssetCreateRequest(
                    kind=AssetKind.REFERENCE,
                    name=self._creative_input_asset_name(index, image),
                    descriptor=image.descriptor,
                ),
            )
            if image.local_path is not None:
                imported = self._import_local_asset_version_unchecked(
                    project_id,
                    created.asset.id,
                    AssetImportLocalRequest(
                        local_path=image.local_path,
                        descriptor=image.descriptor,
                    ),
                )
            else:
                assert image.external_url is not None
                imported = self._import_url_asset_version_unchecked(
                    project_id,
                    created.asset.id,
                    AssetImportUrlRequest(
                        external_url=image.external_url,
                        descriptor=image.descriptor,
                        media_type=AssetMediaType.IMAGE,
                    ),
                )
            self._review_asset_version_unchecked(
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
            self._bind_creative_input_versions(
                project_id,
                request,
                imported_versions,
            )

    def _bind_creative_input_versions(
        self,
        project_id: str,
        request: CreateProjectRequest,
        imported_versions: list[tuple[str, str]],
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            required = request.source_mode == SourceMode.IMAGE_SEEDED
            changed = False
            for plan in document.reference_plan:
                known_targets = {binding.target_id for binding in plan.bindings}
                for version_id, descriptor in imported_versions:
                    if version_id in known_targets:
                        continue
                    plan.bindings.append(
                        ReferenceBinding(
                            kind=AssetKind.REFERENCE,
                            target_id=version_id,
                            purpose=f"用户提供的跨镜视觉种子：{descriptor}",
                            required=required,
                        )
                    )
                    known_targets.add(version_id)
                    changed = True
            if changed:
                document.project.updated_at = utc_now()
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
    def _require_approved_voice_audio(asset: Asset) -> AssetVersion:
        if asset.kind != AssetKind.REFERENCE or asset.approved_version_id is None:
            raise WorkflowConflictError(
                "A character voice reference needs an approved REFERENCE audio asset"
            )
        version = next(
            (
                item
                for item in asset.versions
                if item.id == asset.approved_version_id
                and item.approval_status == ApprovalStatus.APPROVED
                and item.media_type == AssetMediaType.AUDIO
                and (item.local_path is not None or item.external_url is not None)
            ),
            None,
        )
        if version is None:
            raise WorkflowConflictError(
                "A character voice reference needs an approved audio version"
            )
        if version.local_path is not None:
            path = Path(version.local_path).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in {".wav", ".mp3"}:
                raise WorkflowConflictError(
                    "Local MiniMax-H3 voice references must be available WAV or MP3 files"
                )
            if path.stat().st_size > 15 * 1024 * 1024:
                raise WorkflowConflictError(
                    "MiniMax-H3 voice reference exceeds 15 MB"
                )
        return version

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
        media_inputs = self._resolve_approved_asset_inputs(document, request.media_inputs)
        source_prompt = (
            request.source_prompt.strip()
            if request.source_prompt is not None
            else build_h3_prompt(
                document,
                shot,
                route=request.route,
                duration_seconds=duration,
                media_inputs=media_inputs,
            )
        )
        if not source_prompt:
            raise WorkflowConflictError("H3 source prompt is empty")
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
                route=request.route,
                duration_seconds=duration,
                media_inputs=media_inputs,
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
            document = self._require_project_mutation_allowed(project_id)
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
            if request.lineage_root_version_id is not None:
                _root_asset, root_version = self._find_asset_version(
                    document,
                    request.lineage_root_version_id,
                )
                if (
                    root_version.media_type != AssetMediaType.IMAGE
                    or (
                        root_version.local_path is None
                        and root_version.external_url is None
                    )
                ):
                    raise WorkflowConflictError(
                        "Reference-pack lineage root must be a usable image"
                    )

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
                    lineage_root_version_id=request.lineage_root_version_id,
                    role=request.role,
                    angle=request.angle,
                    framing=request.framing,
                    tags=normalized_tags,
                    description=request.description.strip(),
                    priority=request.priority,
                )
                pack.entries.append(entry)
            else:
                existing.lineage_root_version_id = request.lineage_root_version_id
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
            document = self._require_project_mutation_allowed(project_id)
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

    def rerank_shot_reference_visuals(
        self,
        project_id: str,
        shot_id: str,
        request: ReferenceVisualRerankRequest,
    ) -> ReferenceVisualRerankResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        client = self._require_language_client()
        try:
            report = rerank_reference_visuals(
                document=document,
                shot=shot,
                plan=plan,
                request=request,
                client=client,
                created_at=utc_now(),
            )
        except VisualInputContractError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        self.repository.save_reference_visual_rerank(report)
        return ReferenceVisualRerankResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def compare_shot_keyframes(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeComparisonRequest,
    ) -> KeyframeComparisonResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        client = self._require_language_client()
        previous_boundary = (
            previous_shot_boundary_image_url(
                document=document,
                shot=shot,
                takes=self.repository.list_takes(project_id),
                inspections=self.repository.list_video_inspections(project_id),
            )
            if request.include_previous_boundary
            else None
        )
        try:
            report = compare_keyframe_candidates(
                document=document,
                shot=shot,
                plan=plan,
                request=request,
                client=client,
                previous_boundary_url=previous_boundary,
                created_at=utc_now(),
            )
        except VisualInputContractError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        self.repository.save_keyframe_comparison(report)
        return KeyframeComparisonResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def check_shot_input_compatibility(
        self,
        project_id: str,
        shot_id: str,
        request: InputCompatibilityRequest,
    ) -> InputCompatibilityResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        client = self._require_language_client()
        previous_boundary = (
            previous_shot_boundary_image_url(
                document=document,
                shot=shot,
                takes=self.repository.list_takes(project_id),
                inspections=self.repository.list_video_inspections(project_id),
            )
            if request.include_previous_boundary
            else None
        )
        try:
            report = check_input_compatibility(
                document=document,
                shot=shot,
                plan=plan,
                request=request,
                client=client,
                previous_boundary_url=previous_boundary,
                created_at=utc_now(),
            )
        except VisualInputContractError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        self.repository.save_input_compatibility_report(report)
        return InputCompatibilityResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def list_visual_input_reports(
        self,
        project_id: str,
    ) -> VisualInputReportsResponse:
        self._require_project_document(project_id)
        return VisualInputReportsResponse(
            reference_reranks=self.repository.list_reference_visual_reranks(project_id),
            keyframe_comparisons=self.repository.list_keyframe_comparisons(project_id),
            compatibility_checks=self.repository.list_input_compatibility_reports(project_id),
        )

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
        if request.audio_inspection_id is not None:
            audio_inspection = self.repository.get_audio_inspection(
                request.audio_inspection_id
            )
            if (
                audio_inspection is None
                or audio_inspection.project_id != project_id
            ):
                raise ArtifactNotFoundError(request.audio_inspection_id)
            shot_clip_inspections = [
                item
                for item in audio_inspection.clip_inspections
                if item.shot_id == shot_id
            ]
            shot_dialogue_checks = [
                item
                for item in audio_inspection.dialogue_checks
                if item.shot_id == shot_id
            ]
            if not shot_clip_inspections and not shot_dialogue_checks:
                raise WorkflowConflictError(
                    "Director advice audio inspection has no evidence for this shot"
                )
            if request.take_id is not None and not any(
                item.take_id == request.take_id
                for item in [*shot_clip_inspections, *shot_dialogue_checks]
            ):
                raise WorkflowConflictError(
                    "Director advice Take and audio inspection do not refer to the same output"
                )
            qa_issues.extend(
                issue.model_copy(deep=True)
                for item in shot_clip_inspections
                for issue in item.issues
            )
            qa_issues.extend(
                issue.model_copy(deep=True)
                for item in shot_dialogue_checks
                for issue in item.issues
            )
            relevant_line_ids = {
                item.script_line_id for item in shot_dialogue_checks
            }
            qa_issues.extend(
                QaIssue(
                    code="VOICE_CONTINUITY_REVIEW",
                    severity="UNKNOWN",
                    message=(
                        f"声线连续性证据涉及当前镜头；speaker={check.speaker!r}, "
                        f"script_line_ids={check.script_line_ids!r}, detail={check.detail!r}。"
                        "请导演 Agent 结合实际表演与可接受变化判断是否需要处理。"
                    ),
                    repair_layer="DIRECTOR_ROUTE_OR_REFERENCE_AUDIO",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
                for check in audio_inspection.speaker_checks
                if check.assessment == AudioContinuityAssessment.REVIEW
                and bool(set(check.script_line_ids) & relevant_line_ids)
            )
            reviewer_notes.append(
                f"{audio_inspection.id}: {audio_inspection.summary}"
            )
        if request.episode_review_id is not None:
            episode_review = self.repository.get_episode_review(
                request.episode_review_id
            )
            if (
                episode_review is None
                or episode_review.project_id != project_id
            ):
                raise ArtifactNotFoundError(request.episode_review_id)
            linked_findings = [
                item for item in episode_review.findings if shot_id in item.shot_ids
            ]
            if not linked_findings:
                raise WorkflowConflictError(
                    "Director advice episode review has no evidence for this shot"
                )
            qa_issues.extend(
                item.issue.model_copy(deep=True) for item in linked_findings
            )
            reviewer_notes.extend(
                (
                    f"{episode_review.id}/{item.id}: {item.evidence} "
                    f"Recommended action: {item.recommended_action}"
                )
                for item in linked_findings
            )
        capability_profile = build_h3_capability_profile(
            self.repository.list_takes(project_id),
            project_id=project_id,
        )
        prior_repairs = [
            record.model_copy(deep=True)
            for record in document.repair_history
            if record.decision == DirectorRepairDecision.APPLIED
            and (
                record.source_shot_id == shot_id
                or shot_id in record.affected_shot_ids
            )
        ]

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
            prior_repairs,
        )
        try:
            client = self.language_provider_factory(api_key, base_url)
            output_text = client.generate_text(
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS,
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
                    max_output_tokens=_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS,
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
            document = self._require_project_mutation_allowed(project_id)
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
        takes = self.repository.list_takes(project_id)
        created_at = utc_now()
        prepared_media_inputs = self._prepared_h3_media_inputs(
            document,
            existing,
        )
        video_guides = {}
        video_guide_warnings: list[str] = []
        directive_by_shot = {
            item.shot_id: item for item in request.shot_directives
        }
        if request.consider_video_guides:
            video_guides, video_guide_warnings = self.h3_video_guide_builder.prepare(
                document=document,
                takes=takes,
                shot_ids=request.shot_ids,
                created_at=created_at,
                prepared_media_inputs=prepared_media_inputs,
                camera_motion_by_shot={
                    shot_id: directive.camera_previs_motion
                    for shot_id, directive in directive_by_shot.items()
                    if directive.camera_previs_motion is not None
                },
            )
        try:
            plan = build_h3_production_route_plan(
                document=document,
                tasks=self.repository.list_tasks(project_id),
                takes=takes,
                evidence=self.repository.list_route_evidence(project_id),
                provider_runs=self.repository.list_provider_runs(project_id),
                inspections=self.repository.list_video_inspections(project_id),
                pricing=self.h3_config.pricing_snapshot(),
                version=max((item.version for item in existing), default=0) + 1,
                created_at=created_at,
                shot_ids=request.shot_ids,
                candidate_count=request.candidate_count,
                language_client=language_client,
                prepared_media_inputs=prepared_media_inputs,
                video_guides=video_guides,
                video_guide_warnings=video_guide_warnings,
                shot_directives=request.shot_directives,
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

    def _h3_route_plan_tasks(
        self,
        project_id: str,
        decision: H3ShotRoutePlan,
    ) -> list[GenerationTask]:
        if len(set(decision.task_ids)) != len(decision.task_ids):
            raise WorkflowConflictError(
                f"H3 route decision for shot {decision.shot_id} contains duplicate task IDs"
            )
        shot_tasks = self.repository.list_tasks_for_shot(project_id, decision.shot_id)
        by_id = {task.id: task for task in shot_tasks}
        planned_tasks: list[GenerationTask] = []
        for task_id in decision.task_ids:
            task = by_id.get(task_id)
            if task is None:
                raise WorkflowConflictError(
                    f"H3 route decision for shot {decision.shot_id} references missing "
                    f"task {task_id}"
                )
            evidence = self.repository.get_route_evidence_for_task(task.id)
            if (
                task.project_id != project_id
                or task.shot_id != decision.shot_id
                or task.provider != "minimax"
                or task.model != "MiniMax-H3"
                or task.operation != OperationKind.GENERATE
                or task.route != decision.route
                or evidence is None
                or evidence.purpose != EvidencePurpose.PRODUCTION
            ):
                raise WorkflowConflictError(
                    f"Task {task.id} does not belong to the production route decision "
                    f"for shot {decision.shot_id}"
                )
            planned_tasks.append(task)
        return planned_tasks

    def _h3_route_enqueue_count(
        self,
        decision: H3ShotRoutePlan,
        planned_tasks: list[GenerationTask],
    ) -> int:
        """Execute one persisted route decision as one explicit candidate batch."""

        return 0 if planned_tasks else decision.candidate_count

    def execute_h3_production_route_plan(
        self,
        project_id: str,
        plan_id: str,
        request: H3ProductionRouteExecuteRequest,
    ) -> H3ProductionRouteExecuteResponse:
        inspection_take_ids: list[str] = []
        queued_tasks: list[GenerationTask] = []
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
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

            execution_inputs: dict[
                int,
                tuple[
                    int,
                    ShotPlan | None,
                    list[H3MediaInput],
                ],
            ] = {}
            for decision in plan.decisions:
                planned_tasks: list[GenerationTask] = []
                enqueue_count = 0
                shot: ShotPlan | None = None
                media_inputs: list[H3MediaInput] = []
                if (
                    request.enqueue_new_generations
                    and decision.action == H3ShotRouteAction.GENERATE
                    and decision.readiness
                    not in {
                        H3ShotRouteReadiness.NEEDS_AGENT_DECISION,
                        H3ShotRouteReadiness.NEEDS_ASSET,
                        H3ShotRouteReadiness.UNSUPPORTED,
                    }
                ):
                    planned_tasks = self._h3_route_plan_tasks(
                        project_id,
                        decision,
                    )
                    enqueue_count = self._h3_route_enqueue_count(
                        decision,
                        planned_tasks,
                    )
                    if enqueue_count:
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
                execution_inputs[id(decision)] = (
                    enqueue_count,
                    shot,
                    media_inputs,
                )

            if (
                any(item[0] > 0 for item in execution_inputs.values())
                and not self.h3_config.api_key()
            ):
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing production H3 tasks"
                )
            for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
                if decision.readiness == H3ShotRouteReadiness.NEEDS_AGENT_DECISION:
                    decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                    continue
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
                    in {
                        H3ShotRouteReadiness.NEEDS_AGENT_DECISION,
                        H3ShotRouteReadiness.NEEDS_ASSET,
                        H3ShotRouteReadiness.UNSUPPORTED,
                    }
                    or not request.enqueue_new_generations
                ):
                    decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                    continue
                (
                    enqueue_count,
                    shot,
                    media_inputs,
                ) = execution_inputs[id(decision)]
                if decision.task_ids:
                    decision.execution_status = H3ShotRouteExecutionStatus.QUEUED
                if enqueue_count == 0:
                    continue
                assert shot is not None
                existing_tasks = self.repository.list_tasks_for_shot(project_id, shot.id)
                next_candidate = max(
                    (item.candidate_index for item in existing_tasks),
                    default=0,
                ) + 1
                decision.execution_status = H3ShotRouteExecutionStatus.QUEUED
                for offset in range(enqueue_count):
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
                    decision.task_ids.append(task.id)
                    plan.status = H3RoutePlanStatus.EXECUTING
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_task_bundle(
                        task=task,
                        run=run,
                        evidence=route_evidence,
                        plan=plan,
                    )
                    _log_video_task_transition(None, task, action="enqueue")
                    queued_tasks.append(task)

            if queued_tasks or any(
                item.action == H3ShotRouteAction.GENERATE and item.task_ids
                for item in plan.decisions
            ):
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
            document = self._require_project_mutation_allowed(project_id)
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
                        shot_payload = shot.model_dump(mode="python")
                        beat_invalidating_fields = {
                            "duration_seconds",
                            "visual",
                            "framing",
                            "camera",
                            "audio_intent",
                            "dialogue_line_ids",
                            "continuity",
                            "transition_out",
                        }
                        if (
                            "timeline_beats" not in patch.model_fields_set
                            and beat_invalidating_fields.intersection(
                                patch.model_fields_set
                            )
                        ):
                            shot_payload["timeline_beats"] = []
                        for field_name in patch.model_fields_set:
                            value = getattr(patch, field_name)
                            if value is not None:
                                shot_payload[field_name] = deepcopy(value)
                        shot_payload["revision"] = shot.revision + 1
                        try:
                            updated_shot = ShotPlan.model_validate(shot_payload)
                        except ValueError as exc:
                            raise WorkflowConflictError(
                                f"Invalid ShotPlan repair: {exc}"
                            ) from exc
                        document.shots[document.shots.index(shot)] = updated_shot
                        shot = updated_shot
                    if request.reference_plan is not None:
                        self._apply_reference_plan_update(
                            document,
                            shot_id,
                            request.reference_plan,
                        )
                    if patch is None:
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
        # Reject before get_director_advice can invoke the language provider.
        self._require_project_mutation_allowed(project_id)
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
        self._require_project_mutation_allowed(project_id)
        return self._create_asset_unchecked(project_id, request)

    def _create_asset_unchecked(
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

    def update_asset_brief(
        self,
        project_id: str,
        asset_id: str,
        request: AssetBriefUpdateRequest,
    ) -> AssetMutationResponse:
        self._require_project_mutation_allowed(project_id)
        try:
            version = self.asset_workbench.update_asset_brief(
                project_id,
                asset_id,
                request,
            )
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_local_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportLocalRequest,
    ) -> AssetMutationResponse:
        self._require_project_mutation_allowed(project_id)
        return self._import_local_asset_version_unchecked(
            project_id,
            asset_id,
            request,
        )

    def _import_local_asset_version_unchecked(
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
        self._require_project_mutation_allowed(project_id)
        return self._import_url_asset_version_unchecked(
            project_id,
            asset_id,
            request,
        )

    def _import_url_asset_version_unchecked(
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

    def compose_asset_reference_sheet(
        self,
        project_id: str,
        asset_id: str,
        request: AssetReferenceSheetComposeRequest,
    ) -> AssetMutationResponse:
        self._require_project_mutation_allowed(project_id)
        try:
            version = self.asset_workbench.compose_reference_sheet(
                project_id,
                asset_id,
                request,
            )
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_stock_asset(
        self,
        project_id: str,
        request: StockAssetImportRequest,
    ) -> AssetMutationResponse:
        self._require_project_mutation_allowed(project_id)
        try:
            version = self.asset_workbench.import_stock_candidate(project_id, request)
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
        self._require_project_mutation_allowed(project_id)
        return self._review_asset_version_unchecked(
            project_id,
            asset_id,
            version_id,
            request,
        )

    def _review_asset_version_unchecked(
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

    def update_character_voice_reference(
        self,
        project_id: str,
        character_id: str,
        request: VoiceReferenceUpdateRequest,
    ) -> VoiceReferenceMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            character = self._require_current_character(document, character_id)
            existing = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            now = utc_now()
            if request.asset_id is None:
                if existing is not None:
                    document.voice_references.remove(existing)
                    document.project.voice_reference_revision += 1
                    document.project.updated_at = now
                    self.repository.save_project(document)
                return self._voice_reference_mutation_response(
                    project_id,
                    character.id,
                )

            asset = self._require_asset_in_document(document, request.asset_id)
            self._require_approved_voice_audio(asset)
            changed = existing is None or existing.asset_id != asset.id
            if existing is None:
                document.voice_references.append(
                    VoiceReferenceBinding(
                        character_id=character.id,
                        asset_id=asset.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif changed:
                existing.asset_id = asset.id
                existing.updated_at = now
            if changed:
                document.project.voice_reference_revision += 1
                document.project.updated_at = now
                self.repository.save_project(document)
        return self._voice_reference_mutation_response(project_id, character_id)

    def upload_character_voice_reference(
        self,
        project_id: str,
        character_id: str,
        *,
        filename: str,
        descriptor: str,
        data: bytes,
    ) -> VoiceReferenceMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            character = self._require_current_character(document, character_id)
            existing = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            target_asset_id = existing.asset_id if existing is not None else None
            character_name = character.name
        try:
            version = self.asset_workbench.import_voice_reference_bytes(
                project_id,
                asset_id=target_asset_id,
                asset_name=f"{character_name}声线参考",
                descriptor=descriptor,
                filename=filename,
                data=data,
            )
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

        with self._lock:
            document = self._require_project_document(project_id)
            character = self._require_current_character(document, character_id)
            asset = next(
                (
                    candidate
                    for candidate in document.assets
                    if any(item.id == version.id for item in candidate.versions)
                ),
                None,
            )
            if asset is None:
                raise ArtifactNotFoundError(version.id)
            now = utc_now()
            binding = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            if binding is None:
                document.voice_references.append(
                    VoiceReferenceBinding(
                        character_id=character.id,
                        asset_id=asset.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                binding.asset_id = asset.id
                binding.updated_at = now
            document.project.voice_reference_revision += 1
            document.project.updated_at = now
            self.repository.save_project(document)
        return self._voice_reference_mutation_response(
            project_id,
            character_id,
            version_id=version.id,
        )

    def enqueue_image_task(
        self,
        project_id: str,
        asset_id: str,
        request: ImageTaskCreateRequest,
    ) -> ImageTaskResponse:
        self._require_project_mutation_allowed(project_id)
        try:
            task = self.asset_workbench.enqueue_image_task(project_id, asset_id, request)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        _log_image_task_transition(None, task, action="enqueue")
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
        previous = self.repository.get_image_task(task_id)
        try:
            task = self.asset_workbench.advance_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except ImageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        _log_image_task_transition(previous, task, action="advance")
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def cancel_image_task(self, task_id: str) -> ImageTaskResponse:
        previous = self.repository.get_image_task(task_id)
        try:
            task = self.asset_workbench.cancel_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        _log_image_task_transition(previous, task, action="cancel")
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def retry_image_task(self, task_id: str) -> ImageTaskResponse:
        previous = self.repository.get_image_task(task_id)
        try:
            task = self.asset_workbench.retry_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        _log_image_task_transition(
            None,
            task,
            action="retry",
            parent_task_id=(previous.id if previous is not None else task_id),
        )
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def resolve_image_not_submitted(
        self,
        task_id: str,
        request: ImageNotSubmittedResolutionRequest,
    ) -> ImageTaskResponse:
        previous = self.repository.get_image_task(task_id)
        try:
            task = self.asset_workbench.resolve_image_not_submitted(task_id, request)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        _log_image_task_transition(
            previous,
            task,
            action="resolve_not_submitted",
        )
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def recover_image_task_output(self, task_id: str) -> ImageTaskResponse:
        previous = self.repository.get_image_task(task_id)
        try:
            task = self.asset_workbench.recover_image_output(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        _log_image_task_transition(previous, task, action="recover_output")
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
            self._require_project_mutation_allowed(project_id)
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
            _log_video_task_transition(None, task, action="enqueue")
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

    def list_h3_reconciliation_candidates_for_task(
        self,
        project_id: str,
        task_id: str,
    ) -> H3ReconciliationCandidatesResponse:
        self._require_project_document(project_id)
        task = self._require_task(task_id)
        if task.project_id != project_id:
            raise ArtifactNotFoundError(task_id)
        try:
            tasks = self.h3_worker.list_reconciliation_candidates_for_task(task_id)
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return H3ReconciliationCandidatesResponse(tasks=tasks)

    def enqueue(self, project_id: str, request: EnqueueRequest) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
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
                    _log_video_task_transition(None, task, action="enqueue")
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
        _log_video_task_transition(persisted, task, action="advance")
        return task

    def drain(self, project_id: str, max_steps: int) -> WorkerDrainResponse:
        self._require_project_document(project_id)
        before = {
            task.id: task
            for task in self.repository.list_tasks(project_id)
            if task.provider == "mock"
        }
        processed_steps = self.worker.drain(project_id, max_steps)
        for task in self.repository.list_tasks(project_id):
            if task.provider != "mock":
                continue
            previous = before.get(task.id)
            if previous is None or (
                previous.status != task.status
                or previous.error_code != task.error_code
                or previous.provider_task_id != task.provider_task_id
            ):
                _log_video_task_transition(previous, task, action="drain")
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
        _log_video_task_transition(persisted, task, action="cancel")
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
                stale_reason = self.h3_worker.input_staleness_reason(task, run)
                if stale_reason is not None:
                    raise WorkflowConflictError(
                        f"{stale_reason}; compile a new H3 production route plan instead "
                        "of retrying stale inputs"
                    )
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
            _log_video_task_transition(
                None,
                retry,
                action="retry",
                parent_task_id=task.id,
            )
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
        self._refresh_project_stage(task.project_id)
        _log_video_task_transition(persisted, task, action="reconcile")
        return task

    def resolve_h3_submission(
        self,
        task_id: str,
        provider_task_id: str,
    ) -> H3TaskActionResponse:
        previous = self._require_task(task_id)
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
        _log_video_task_transition(previous, task, action="resolve_submission")
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
        previous = self._require_task(task_id)
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
        _log_video_task_transition(previous, task, action="resolve_not_submitted")
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
        _log_video_task_transition(persisted, task, action="recover_output")
        return task

    def select_take(self, take_id: str) -> ProjectAggregate:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            document = self._require_project_mutation_allowed(take.project_id)
            if take.freshness != Freshness.CURRENT:
                raise WorkflowConflictError("A stale Take cannot be selected for the current timeline")
            if take.availability != TakeAvailability.AVAILABLE:
                raise WorkflowConflictError("A missing Take cannot be selected")
            evidence = self.repository.get_route_evidence_for_take(take_id)
            if evidence is not None and evidence.purpose == EvidencePurpose.CONTRACT_SMOKE:
                raise WorkflowConflictError(
                    "A contract-smoke Take cannot be selected for the production timeline"
                )
            selected = self.repository.select_take(take_id)
            if selected is None:
                raise ArtifactNotFoundError(take_id)
            self._invalidate_current_manifests(document)
            document.project.stage = self._task_derived_project_stage(take.project_id)
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

    def compare_takes(
        self,
        project_id: str,
        request: TakeComparisonRequest,
    ) -> TakeComparisonResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, request.shot_id)
        if len(set(request.take_ids)) != len(request.take_ids):
            raise WorkflowConflictError("Take comparison candidates must be unique")

        if request.take_ids:
            takes: list[Take] = []
            for take_id in request.take_ids:
                take = self.repository.get_take(take_id)
                if take is None:
                    raise ArtifactNotFoundError(take_id)
                takes.append(take)
        else:
            takes = sorted(
                (
                    take
                    for take in self.repository.list_takes_for_shot(project_id, shot.id)
                    if take.freshness == Freshness.CURRENT
                    and take.availability == TakeAvailability.AVAILABLE
                ),
                key=lambda item: (item.candidate_index, item.created_at, item.id),
            )

        if len(takes) < 2:
            raise WorkflowConflictError(
                "Take comparison requires at least two current candidates"
            )
        for take in takes:
            if take.project_id != project_id or take.shot_id != shot.id:
                raise WorkflowConflictError(
                    "All compared Takes must belong to the requested project and shot"
                )
            if take.freshness != Freshness.CURRENT:
                raise WorkflowConflictError("A stale Take cannot enter current comparison")
            if take.availability != TakeAvailability.AVAILABLE:
                raise WorkflowConflictError("A missing Take cannot enter comparison")

        client = self._require_language_client()
        project_inspections = self.repository.list_video_inspections(project_id)
        inspections: list[VideoInspectionReport] = []
        for take in takes:
            inspection = next(
                (
                    item
                    for item in reversed(project_inspections)
                    if item.take_id == take.id
                    and item.semantic_status == SemanticReviewStatus.COMPLETE
                ),
                None,
            )
            if inspection is None:
                if not request.inspect_missing:
                    raise WorkflowConflictError(
                        f"Take {take.id} needs a complete semantic video inspection"
                    )
                inspection = self.inspect_take_video(
                    take.id,
                    VideoInspectionRequest(
                        include_previous_boundary=request.include_previous_boundary,
                        semantic_review=True,
                        review_focus=request.review_focus,
                    ),
                ).report
                project_inspections.append(inspection)
            if inspection.semantic_status != SemanticReviewStatus.COMPLETE:
                raise WorkflowConflictError(
                    f"Semantic video inspection is unavailable for Take {take.id}"
                )
            inspections.append(inspection)

        try:
            report = compare_take_candidates(
                document=document,
                shot=shot,
                takes=takes,
                inspections=inspections,
                client=client,
                review_focus=request.review_focus,
                created_at=utc_now(),
            )
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        self.repository.save_take_comparison(report)

        if request.apply_selection:
            if (
                report.decision == TakeComparisonDecision.SELECT_TAKE
                and report.selected_take_id is not None
            ):
                self.select_take(report.selected_take_id)
            elif report.decision == TakeComparisonDecision.NONE_ACCEPTABLE:
                self._clear_take_selection_for_shot(project_id, shot.id)

        return TakeComparisonResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def inspect_current_manifest_audio(
        self,
        project_id: str,
        request: AudioInspectionRequest,
    ) -> AudioInspectionResponse:
        document = self._require_project_document(project_id)
        manifest = self._current_manifest(document)
        report = self.audio_inspector.inspect_manifest(
            document=document,
            manifest=manifest,
            request=request,
            semantic_service=self.audio_semantic_service,
            created_at=utc_now(),
        )
        self.repository.save_audio_inspection(report)
        return AudioInspectionResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def review_current_episode(
        self,
        project_id: str,
        request: EpisodeReviewRequest,
    ) -> EpisodeReviewResponse:
        document = self._require_project_document(project_id)
        manifest = self._current_manifest(document)
        report = self._get_or_create_episode_review(
            document,
            manifest,
            force_new=request.force_new,
        )
        for _ in range(request.max_actions):
            if report.status != EpisodeReviewStatus.RUNNING:
                break
            report = self._advance_episode_review_report_once(report.id)
        return EpisodeReviewResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def _get_or_create_episode_review(
        self,
        document: ProjectDocument,
        manifest: TimelineManifest,
        *,
        force_new: bool,
    ) -> EpisodeReviewReport:
        existing = [
            item
            for item in self.repository.list_episode_reviews(document.project.id)
            if item.manifest_id == manifest.id
        ]
        if existing and not force_new:
            return existing[-1]
        now = utc_now()
        report = EpisodeReviewReport(
            id=f"episode-review-{uuid4().hex}",
            project_id=document.project.id,
            manifest_id=manifest.id,
            source_script_version_id=manifest.script_version_id,
            status=EpisodeReviewStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        self.repository.save_episode_review(report)
        return report

    def _advance_episode_review_report_once(
        self,
        report_id: str,
    ) -> EpisodeReviewReport:
        report = self.repository.get_episode_review(report_id)
        if report is None:
            raise ArtifactNotFoundError(report_id)
        if report.status != EpisodeReviewStatus.RUNNING:
            return report
        document = self._require_project_document(report.project_id)
        manifest = next(
            (item for item in document.manifests if item.id == report.manifest_id),
            None,
        )
        if manifest is None:
            raise ArtifactNotFoundError(report.manifest_id)
        client = self._configured_language_client()
        if client is None:
            report.status = EpisodeReviewStatus.UNAVAILABLE
            report.summary = (
                "GPT-5.6-sol 未配置；逐镜视频观察仍保留，整集语义审阅为 UNKNOWN。"
            )
            report.warnings.append(
                "Episode Reviewer was skipped because no language provider is configured."
            )
            report.repairs_complete = True
            report.updated_at = utc_now()
            self.repository.save_episode_review(report)
            return report

        scene_groups = self._episode_review_scene_groups(
            document,
            script_version_id=manifest.script_version_id,
        )
        if report.next_scene_index < len(scene_groups):
            scene_sequence, scene, shots = scene_groups[report.next_scene_index]
            stage_key = f"SCENE:{scene.id}:{scene_sequence}"
            try:
                takes = self.repository.list_takes(document.project.id)
                takes_by_id = {item.id: item for item in takes}
                latest_inspection_by_take: dict[str, VideoInspectionReport] = {}
                for inspection in self.repository.list_video_inspections(
                    document.project.id
                ):
                    latest_inspection_by_take[inspection.take_id] = inspection
                scene_review = review_scene(
                    document=document,
                    manifest=manifest,
                    scene=scene,
                    scene_sequence=scene_sequence,
                    shots=shots,
                    takes_by_id=takes_by_id,
                    latest_inspection_by_take=latest_inspection_by_take,
                    client=client,
                )
            except (LanguageProviderError, ValueError) as exc:
                return self._record_episode_review_failure(
                    report,
                    stage_key=stage_key,
                    error=exc,
                )
            report.scene_reviews.append(scene_review)
            report.next_scene_index += 1
            report.semantic_model = "gpt-5.6-sol"
            report.active_stage_key = None
            report.active_stage_attempts = 0
            report.summary = (
                f"整集审阅已完成 {report.next_scene_index}/{len(scene_groups)} 个场次。"
            )
            report.updated_at = utc_now()
            self.repository.save_episode_review(report)
            return report

        stage_key = "EPISODE"
        try:
            episode_findings, summary = review_episode(
                document=document,
                manifest=manifest,
                scene_reviews=report.scene_reviews,
                client=client,
            )
        except (LanguageProviderError, ValueError) as exc:
            return self._record_episode_review_failure(
                report,
                stage_key=stage_key,
                error=exc,
            )
        report.findings = [
            *(finding for scene in report.scene_reviews for finding in scene.findings),
            *episode_findings,
        ]
        report.summary = summary
        report.status = EpisodeReviewStatus.COMPLETE
        report.semantic_model = "gpt-5.6-sol"
        report.active_stage_key = None
        report.active_stage_attempts = 0
        report.updated_at = utc_now()
        self.repository.save_episode_review(report)
        return report

    def _record_episode_review_failure(
        self,
        report: EpisodeReviewReport,
        *,
        stage_key: str,
        error: Exception,
    ) -> EpisodeReviewReport:
        attempts = (
            report.active_stage_attempts + 1
            if report.active_stage_key == stage_key
            else 1
        )
        report.active_stage_key = stage_key
        report.active_stage_attempts = attempts
        report.warnings.append(
            f"{stage_key} review attempt {attempts} failed: {error}. "
            "The current cursor and collected evidence were retained so the Agent can "
            "retry, change route, or request input on a later advance."
        )
        report.summary = (
            f"整集 Reviewer 在 {stage_key} 本次未完成；"
            "已保留现有逐镜证据与当前游标。"
        )
        report.updated_at = utc_now()
        self.repository.save_episode_review(report)
        return report

    @staticmethod
    def _episode_review_scene_groups(
        document: ProjectDocument,
        *,
        script_version_id: str | None = None,
    ) -> list[tuple[int, StructuredScene, list[ShotPlan]]]:
        script = next(
            item
            for item in document.script_versions
            if item.id
            == (script_version_id or document.project.current_script_version_id)
        )
        groups: dict[str, list[ShotPlan]] = {}
        for shot in sorted(document.shots, key=lambda item: item.sequence):
            scene_key = shot.scene_id or "__UNSCOPED_SCENE__"
            groups.setdefault(scene_key, []).append(shot)
        ordered: list[tuple[StructuredScene, list[ShotPlan]]] = []
        for scene in script.structured_scenes:
            shots = groups.pop(scene.id, [])
            if shots:
                ordered.append((scene, shots))
        for scene_key, shots in groups.items():
            ordered.append(
                (
                    StructuredScene(
                        id=scene_key,
                        heading="未显式分场",
                        summary="按实际时间线连续审阅未归属到结构化场次的镜头。",
                        line_ids=list(
                            dict.fromkeys(
                                line_id
                                for shot in shots
                                for line_id in shot.dialogue_line_ids
                            )
                        ),
                    ),
                    shots,
                )
            )
        ordered.sort(key=lambda item: min(shot.sequence for shot in item[1]))
        return [
            (index, scene, shots)
            for index, (scene, shots) in enumerate(ordered, start=1)
        ]

    def review_h3_take(
        self,
        take_id: str,
        request: H3TakeReviewRequest,
    ) -> H3TakeReviewResponse:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            document = self._require_project_mutation_allowed(take.project_id)
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
            # A review request represents the current semantic judgment. Keep durable
            # media failures from the worker, but do not let superseded visual issues
            # accumulate forever across a reinspection of the same Take.
            merged_issues = {
                issue.code: issue
                for issue in take.qa_report.issues
                if issue.category == QaFailureCategory.TECHNICAL_MEDIA
            }
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

            document.project.stage = self._task_derived_project_stage(take.project_id)
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
        return self.revise_script_lines(
            project_id,
            ReviseScriptLinesRequest(
                revisions=[
                    ScriptLineRevision(
                        line_id=request.line_id,
                        spoken_text=request.spoken_text,
                        subtitle_text=request.subtitle_text,
                    )
                ],
                change_reason=request.change_reason,
            ),
        )

    def revise_script_lines(
        self,
        project_id: str,
        request: ReviseScriptLinesRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            current = self._current_script(document)
            revision_ids = [item.line_id for item in request.revisions]
            duplicate_ids = sorted(
                line_id
                for line_id in set(revision_ids)
                if revision_ids.count(line_id) > 1
            )
            if duplicate_ids:
                raise WorkflowConflictError(
                    "Duplicate script line revisions: " + ", ".join(duplicate_ids)
                )
            known_line_ids = {line.id for line in current.lines}
            unknown_line_ids = sorted(set(revision_ids) - known_line_ids)
            if unknown_line_ids:
                raise ArtifactNotFoundError(", ".join(unknown_line_ids))
            revisions = {item.line_id: item for item in request.revisions}
            current.approval_status = ApprovalStatus.SUPERSEDED
            current.freshness = Freshness.STALE
            next_version_number = max(version.version for version in document.script_versions) + 1
            next_lines = [line.model_copy(deep=True) for line in current.lines]
            for line in next_lines:
                revision = revisions.get(line.id)
                if revision is not None:
                    line.spoken_text = revision.spoken_text
                    line.subtitle_text = revision.subtitle_text
            now = utc_now()
            next_script = ScriptVersion(
                id=f"{project_id}-script-v{next_version_number}",
                project_id=project_id,
                version=next_version_number,
                source_mode=current.source_mode,
                original_text=current.original_text,
                screenplay_text=current.screenplay_text,
                spoken_text="\n".join(line.spoken_text for line in next_lines),
                subtitle_text="\n".join(line.subtitle_text for line in next_lines),
                lines=next_lines,
                structured_scenes=[scene.model_copy(deep=True) for scene in current.structured_scenes],
                review_report=(
                    current.review_report.model_copy(deep=True)
                    if current.review_report is not None
                    else None
                ),
                review_disposition=(
                    current.review_disposition.model_copy(deep=True)
                    if current.review_disposition is not None
                    else None
                ),
                final_cold_read_report=(
                    current.final_cold_read_report.model_copy(deep=True)
                    if current.final_cold_read_report is not None
                    else None
                ),
                ending_contract=(
                    current.ending_contract.model_copy(deep=True)
                    if current.ending_contract is not None
                    else None
                ),
                story_intent=(
                    current.story_intent.model_copy(deep=True)
                    if current.story_intent is not None
                    else None
                ),
                source_contract=(
                    current.source_contract.model_copy(deep=True)
                    if current.source_contract is not None
                    else None
                ),
                planning_run_id=current.planning_run_id,
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                parent_version_id=current.id,
                change_reason=request.change_reason,
                created_at=now,
            )
            document.script_versions.append(next_script)
            document.project.current_script_version_id = next_script.id
            affected_shot_ids = [
                shot.id
                for shot in document.shots
                if any(line_id in revisions for line_id in shot.dialogue_line_ids)
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
            document = self._require_project_mutation_allowed(project_id)
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
            _log_video_task_transition(None, task, action="enqueue")
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return task

    def rebuild_manifest(self, project_id: str) -> TimelineManifest:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            current_script = self._current_script(document)
            selected_by_shot: dict[str, Take] = {}
            for take in self.repository.list_takes(project_id):
                if (
                    take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and _take_is_timeline_eligible(take)
                ):
                    selected_by_shot[take.shot_id] = take
            missing = [shot.id for shot in document.shots if shot.id not in selected_by_shot]
            if missing:
                raise WorkflowConflictError(
                    "Every shot needs a current technically available selected Take before building the timeline: "
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
                source_in_seconds, clip_duration = _timeline_clip_window(shot, take)
                video_clips.append(
                    TimelineVideoClip(
                        id=f"video-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        media_path=take.media_path,
                        start_seconds=cursor,
                        source_in_seconds=source_in_seconds,
                        duration_seconds=clip_duration,
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
                        duration_seconds=clip_duration,
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
                    usable_duration = max(0.5, clip_duration - 1.0)
                    cue_duration = usable_duration / len(lines)
                    for index, line in enumerate(lines):
                        cue_start = cursor + 0.5 + cue_duration * index
                        cue_end = min(cursor + clip_duration - 0.25, cue_start + cue_duration)
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
                cursor += clip_duration
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
            document = self._require_project_mutation_allowed(project_id)
            if rebuild_manifest or document.project.current_manifest_id is None:
                manifest = self.rebuild_manifest(project_id)
            else:
                manifest = self._current_manifest(document)
            manifest_snapshot = manifest.model_copy(deep=True)

        # ffmpeg can take minutes for a long episode. Keep it outside the service lock so
        # progress reads and unrelated project work remain responsive during export.
        export = self.exporter.render(manifest_snapshot, utc_now())
        if not all(check.passed for check in export.checks):
            raise WorkflowConflictError("Technical export QA failed")

        with self._lock:
            document = self._require_project_document(project_id)
            if document.project.current_manifest_id != manifest_snapshot.id:
                raise WorkflowConflictError(
                    "Timeline changed while export was rendering; export the current timeline again"
                )
            self.repository.save_export(export)
            document.project.stage = ProjectStage.EXPORTED
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return export

    def _clear_take_selection_for_shot(
        self,
        project_id: str,
        shot_id: str,
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            changed = False
            for take in self.repository.list_takes_for_shot(project_id, shot_id):
                if not take.is_selected:
                    continue
                take.is_selected = False
                self.repository.save_take(take)
                changed = True
            if changed:
                self._invalidate_current_manifests(document)
                document.project.stage = self._task_derived_project_stage(project_id)
                document.project.updated_at = utc_now()
                self.repository.save_project(document)

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
                item.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for item in media_inputs
            ],
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "shot_revision": shot.revision,
            "voice_reference_revision": document.project.voice_reference_revision,
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
            input_voice_reference_revision=document.project.voice_reference_revision,
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
            "voice_reference_revision": document.project.voice_reference_revision,
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
            input_voice_reference_revision=document.project.voice_reference_revision,
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
            and _take_is_timeline_eligible(take)
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
            take_comparisons=self.repository.list_take_comparisons(document.project.id),
            audio_inspections=self.repository.list_audio_inspections(document.project.id),
            episode_reviews=self.repository.list_episode_reviews(document.project.id),
            progress=progress,
        )

    def _apply_reference_plan_update(
        self,
        document: ProjectDocument,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> None:
        requirements = [
            item.model_copy(
                deep=True,
                update={
                    "subject_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.subject_asset_id,
                    )
                },
            )
            for item in request.requirements
        ]
        action_beats = [
            item.model_copy(
                deep=True,
                update={
                    "actor_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.actor_asset_id,
                    )
                },
            )
            for item in request.action_beats
        ]
        interactions = [
            item.model_copy(
                deep=True,
                update={
                    "actor_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.actor_asset_id,
                    ),
                    "target_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.target_asset_id,
                    ),
                },
            )
            for item in request.interactions
        ]

        plan = self._require_reference_plan(document, shot_id)
        plan.requirements = requirements
        plan.action_beats = [
            item.model_copy(deep=True)
            for item in sorted(action_beats, key=lambda item: item.order)
        ]
        plan.interactions = interactions
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
                    scene_id=source_shot.scene_id,
                    duration_seconds=draft.duration_seconds,
                    narrative_purpose=draft.narrative_purpose,
                    visual=draft.visual,
                    framing=draft.framing,
                    camera=draft.camera,
                    audio_intent=draft.audio_intent,
                    dialogue_line_ids=list(draft.dialogue_line_ids),
                    timeline_beats=[
                        beat.model_copy(deep=True) for beat in draft.timeline_beats
                    ],
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

    def _voice_reference_mutation_response(
        self,
        project_id: str,
        character_id: str,
        *,
        version_id: str | None = None,
    ) -> VoiceReferenceMutationResponse:
        project = self.get_project(project_id)
        binding = next(
            (
                item
                for item in project.document.voice_references
                if item.character_id == character_id
            ),
            None,
        )
        if binding is None:
            return VoiceReferenceMutationResponse(project=project)
        asset = self._require_asset_in_document(project.document, binding.asset_id)
        version = next(
            (item for item in asset.versions if item.id == version_id),
            None,
        )
        if version is None and asset.approved_version_id is not None:
            version = next(
                (
                    item
                    for item in asset.versions
                    if item.id == asset.approved_version_id
                ),
                None,
            )
        return VoiceReferenceMutationResponse(
            binding=binding,
            asset=asset,
            version=version,
            project=project,
        )

    @staticmethod
    def _require_current_character(
        document: ProjectDocument,
        character_id: str,
    ) -> CharacterProfile:
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
        character = next(
            (item for item in bible.characters if item.id == character_id),
            None,
        )
        if character is None:
            raise ArtifactNotFoundError(character_id)
        return character

    def _refresh_project_stage(self, project_id: str) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            document.project.stage = self._task_derived_project_stage(project_id)
            document.project.updated_at = utc_now()
            self.repository.save_project(document)

    def _task_derived_project_stage(self, project_id: str) -> ProjectStage:
        tasks = self.repository.list_tasks(project_id)
        if any(task.status not in _TERMINAL_TASK_STATUSES for task in tasks):
            return ProjectStage.GENERATING
        if tasks and self.repository.list_takes(project_id):
            return ProjectStage.REVIEW
        return ProjectStage.GENERATING

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

    def _require_project_mutation_allowed(self, project_id: str) -> ProjectDocument:
        """Return the document unless its Agent-owned planning cursor is unfinished."""

        document = self._require_project_document(project_id)
        planning = document.planning
        if (
            planning is not None
            and planning.run_id is not None
            and planning.owner_agent_run_id is not None
            and planning.status != PlanningRunStatus.COMPLETE
        ):
            raise WorkflowConflictError(
                f"Project {project_id} has unfinished Content Planning Job "
                f"{planning.run_id} ({planning.status.value}); direct mutation is "
                "blocked until its AgentRun completes content planning"
            )
        return document

    @staticmethod
    def _require_asset_in_document(document: ProjectDocument, asset_id: str) -> Asset:
        asset = next((item for item in document.assets if item.id == asset_id), None)
        if asset is None:
            raise ArtifactNotFoundError(asset_id)
        return asset

    @staticmethod
    def _resolve_asset_or_version_id(
        document: ProjectDocument,
        candidate_id: str,
    ) -> str:
        """Accept an Agent-selected asset version anywhere an asset subject is required."""

        for asset in document.assets:
            if asset.id == candidate_id:
                return asset.id
            if any(version.id == candidate_id for version in asset.versions):
                return asset.id
        raise ArtifactNotFoundError(candidate_id)

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
