"""Application service for the provider-neutral short-drama workflow."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue

from short_drama.asset_workbench import (
    AssetProviderConfigurationError,
    AssetWorkbench,
    AssetWorkbenchConflict,
    AssetWorkbenchNotFound,
    ImageProviderFactory,
    default_image_provider_factory,
)
from short_drama.asset_inspection import (
    AssetVersionInspectionRequest,
    SemanticAssetReview,
    inspect_asset_image,
)
from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
    asset_coverage_ready,
    catalog_semantic_asset_kinds,
    missing_canonical_assets,
    missing_reference_views,
    search_asset_catalog,
)
from short_drama.asset_reference_truth import (
    AssetReferenceTruthError,
    resolve_current_asset_media,
)
from short_drama.agent_runtime import (
    AgentRuntime,
    AgentRuntimeStore,
    ArtifactRef,
    ContextCompiler,
    WorkOrderProgressEntry,
)
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
    H3RouteAssetPreparation,
    H3RouteAssetPreparationStatus,
    H3RouteAssetPreparationUpdateRequest,
    H3RouteAssetPreparationUpdateResponse,
    H3RouteAssetPreparationUpdateStatus,
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
    AssetReviewPurpose,
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
from short_drama.media import (
    MediaGenerationError,
    MockMediaRenderer,
    TimelineExporter,
    probe_media_with_ffmpeg,
)
from short_drama.music_analysis import (
    LocalMusicAnalysisService,
    MusicAnalysisError,
    MusicAnalysisService,
)
from short_drama.models import (
    ApprovalStatus,
    AudioContinuityAssessment,
    AudioStructureMap,
    AudioInspectionRequest,
    AudioInspectionResponse,
    Asset,
    AssetKind,
    AssetVersion,
    AssetMediaType,
    CapabilitySnapshot,
    CharacterProfile,
    ContentPlanningOutput,
    CreateProjectRequest,
    CreativeInputImage,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    DurationAlignment,
    DurationAuthority,
    EnqueueRequest,
    EpisodeReviewReport,
    EpisodeReviewRequest,
    EpisodeReviewResponse,
    EpisodeReviewStatus,
    EvidencePurpose,
    EvidenceStatus,
    ExecutionPlanEntry,
    ExecutionStrategy,
    ExportRecord,
    Freshness,
    GenerationTask,
    GenerationUnitShotWindow,
    LocalReworkRequest,
    MasterAudioBinding,
    MasterAudioBindingResponse,
    MasterAudioBindRequest,
    MockBehavior,
    MusicAnalysisRequest,
    MusicAnalysisResponse,
    OperationKind,
    OutputSpec,
    PlanningOutlineSnapshot,
    PlanningReviewSnapshot,
    PlanningRunRecord,
    PlanningRunOperation,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanningRunStep,
    ProgressSummary,
    ProjectAggregate,
    ProjectBibleVersion,
    ProjectDocument,
    ProjectListResponse,
    ProjectPlanningMetadata,
    ProjectOutputSlot,
    ProjectStage,
    ProductionDocument,
    ProductionDocumentArtifactRef,
    ProductionDocumentCreateRequest,
    ProductionDocumentListResponse,
    ProductionDocumentMutationResponse,
    ProductionDocumentOrigin,
    ProductionDocumentReadResponse,
    ProductionDocumentReviseRequest,
    ProductionDocumentVersion,
    ReferencePack,
    ReferencePackEntry,
    ReferenceBinding,
    ReferencePlanEntry,
    ReferenceRole,
    ReviewRepairOwner,
    RouteEvidence,
    RunMode,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReviseScriptLineRequest,
    ReviseScriptLinesRequest,
    ScriptLineRevision,
    ScriptRevisionOrigin,
    ScriptReviewIssue,
    ScriptReviewIssueRoute,
    ScriptReviewSeverity,
    SceneContinuitySummary,
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
    StoryVerificationResolution,
    StoryVerificationResolutionDecision,
    StoryVerificationSource,
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
    TimedTextLockResponse,
    TimedTextLockSet,
    TimedTextLockUpdateRequest,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionRequest,
    VideoInspectionResponse,
    VoiceReferenceBinding,
    WorkerDrainResponse,
)
from short_drama.production_contracts import (
    ASSET_PLAN_DOCUMENT_KIND,
    ASSET_PLAN_OUTPUT_SLOT_ID,
    GENERATION_PLAN_DOCUMENT_KIND,
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    PRODUCTION_SPEC_DOCUMENT_KIND,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    STORYBOARD_DOCUMENT_KIND,
    STORYBOARD_DRAFT_DOCUMENT_KIND,
    STORYBOARD_OUTPUT_SLOT_ID,
    AssetPlanContent,
    AssetPlanCreateRequest,
    AssetPlanDisposition,
    AssetPlanMutationResponse,
    AssetPlanOperation,
    AssetPlanReadResponse,
    AssetPlanReplaceRequest,
    GenerationPlanCompileRequest,
    GenerationPlanContent,
    GenerationPlanMutationResponse,
    GenerationPlanReadResponse,
    OutputSlotBindRequest,
    OutputSlotInvalidateRequest,
    OutputSlotListResponse,
    OutputSlotMutationResponse,
    OutputSlotReadResponse,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionSpecMutationResponse,
    ProductionSpecReadResponse,
    ProductionSpecReplaceRequest,
    StoryboardClipGroupDraft,
    StoryboardClipGroupOutline,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftContent,
    StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupAppendCheckpoint,
    StoryboardDraftGroupReplaceRequest,
    StoryboardDraftGroupsAppendRequest,
    StoryboardDraftGroupsMutationResponse,
    StoryboardDraftLedgerCompleteRequest,
    StoryboardDraftLedgerSegmentAppendRequest,
    StoryboardDraftMutationResponse,
    StoryboardDraftOutlineCompleteRequest,
    StoryboardDraftOutlineSegmentAppendRequest,
    StoryboardDraftPhase,
    StoryboardDraftProgress,
    StoryboardDraftSceneProgress,
    StoryboardDraftSeed,
    StoryboardDraftVerificationRequest,
    StoryboardDraftVerificationStatus,
    StoryboardMutationResponse,
    StoryboardPatchRequest,
    StoryboardReadResponse,
    StoryboardReplaceRequest,
    StoryboardSceneNode,
    StoryboardSourceRange,
    StoryboardWorkUnitContract,
    assemble_storyboard_draft,
    apply_storyboard_patch,
    bind_project_output_slot,
    compile_generation_plan,
    invalidate_project_output_slot,
    production_document_payload,
)
from short_drama.observability import log_provider_task_transition
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedCharacter,
    PlannedDialogueLine,
    PlannedLocation,
    PlannedProp,
    PlannedSceneContract,
    PlannedStyle,
    ProductionPlanningResult,
    SceneShotPlan,
    assemble_multi_agent_planning_result,
    assemble_script_only_planning_result,
    story_revision_issue_indices,
    build_story_source_contract,
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
from short_drama.script_text import (
    ScriptTextSynchronizationError,
    synchronize_script_line_text,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_narrative_skill_prompt,
    compile_production_method_skill_prompt,
    production_method_skill_catalog,
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
    AgentWorkOrderProgressListResponse,
    AgentWorkOrderProgressRequest,
    AgentWorkOrderView,
    ProductionManager,
    ProductionManagerError,
    ProductionPlannerFactory,
    ProjectArtifactResolver,
    default_production_planner_factory,
)
from short_drama.production_board import ProductionBoardView, build_production_board
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
from short_drama.team_executor import (
    LocalTeamExecutor,
    TeamExecutionBatch,
    TeamWorkAdvanceStatus,
)
from short_drama.video_inspection import VideoInspector
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)
from short_drama.worker import InvalidTaskTransition, PersistentMockWorker, utc_now


def _method_skill_ids_for_role(
    skill_ids: list[str],
    role: ProductionSkillRole,
) -> list[str]:
    supported_roles = {
        descriptor.id: descriptor.supported_roles
        for descriptor in production_method_skill_catalog()
    }
    return [
        skill_id
        for skill_id in skill_ids
        if role in supported_roles.get(skill_id, ())
    ]


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
_MANAGED_PRODUCTION_DOCUMENT_KINDS = frozenset(
    {
        "audio.master",
        "audio.structure-map",
        "audio.timed-text-lock",
        PRODUCTION_SPEC_DOCUMENT_KIND,
        STORYBOARD_DRAFT_DOCUMENT_KIND,
        STORYBOARD_DOCUMENT_KIND,
        GENERATION_PLAN_DOCUMENT_KIND,
    }
)


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


def _fit_video_clips_to_duration(
    clips: list[TimelineVideoClip],
    duration_seconds: float,
) -> list[TimelineVideoClip]:
    """Trim or hold the visual track so it ends on the Master Audio clock.

    Master Audio establishes the delivery duration. Existing shot choices remain
    intact in order; a longer visual edit is trimmed, while a shorter edit holds
    its last selected frame. This is timeline normalization, not a creative
    approval rule.
    """

    fitted: list[TimelineVideoClip] = []
    for clip in clips:
        remaining = duration_seconds - clip.start_seconds
        if remaining <= 0:
            break
        fitted.append(
            clip.model_copy(
                deep=True,
                update={"duration_seconds": min(clip.duration_seconds, remaining)},
            )
        )
        if remaining <= clip.duration_seconds:
            break
    if not fitted:
        return fitted

    last = fitted[-1]
    visual_end = last.start_seconds + last.duration_seconds
    if visual_end < duration_seconds:
        fitted[-1] = last.model_copy(
            deep=True,
            update={
                "duration_seconds": last.duration_seconds
                + duration_seconds
                - visual_end
            },
        )
    return fitted


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
        music_analysis_service: MusicAnalysisService | None = None,
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
        self.music_analysis_service = (
            music_analysis_service or LocalMusicAnalysisService()
        )
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
        self.team_executor = LocalTeamExecutor(self.production_manager)

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

    def get_agent_production_board(self, run_id: str) -> ProductionBoardView:
        return build_production_board(
            self.agent_runtime,
            self.repository,
            run_id,
        )

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
        run = self.agent_runtime.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        if run.unattended:
            team_batch = self.team_executor.drain_ready_work_until_handoff(run_id)
            if team_batch.selected_work_order_ids:
                failed = [
                    item
                    for item in team_batch.advances
                    if item.status == TeamWorkAdvanceStatus.FAILED
                ]
                if failed:
                    details = "; ".join(
                        f"{item.work_order_id}: "
                        f"{item.error_message or item.error_kind or 'unknown failure'}"
                        for item in failed
                    )
                    raise ProductionManagerError(
                        "Local Agent Team batch stopped after a visible worker failure: "
                        + details
                    )
                return AgentAdvanceResponse(
                    run=self.production_manager.get_run(run_id),
                    executed_action_ids=[],
                    created_work_order_ids=[],
                    advanced_work_order_ids=team_batch.selected_work_order_ids,
                    notes=[
                        "Advanced the ready local Agent Team WorkOrders through all "
                        "persisted model/Tool turns still owned by their Workers; "
                        "Leader and user handoffs remain explicit."
                    ],
                )
            request = request.model_copy(update={"max_actions": 1})
        return self.production_manager.advance(run_id, request)

    def advance_agent_team(self, run_id: str) -> TeamExecutionBatch:
        return self.team_executor.run_ready_work(run_id)

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

    def append_agent_work_order_progress(
        self,
        work_order_id: str,
        request: AgentWorkOrderProgressRequest,
    ) -> WorkOrderProgressEntry:
        return self.production_manager.append_work_order_progress(
            work_order_id,
            request,
        )

    def list_agent_work_order_progress(
        self,
        work_order_id: str,
    ) -> AgentWorkOrderProgressListResponse:
        return self.production_manager.list_work_order_progress(work_order_id)

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

    def resolve_story_verification(
        self,
        project_id: str,
        *,
        run_id: str,
        owner_agent_run_id: str,
        decision: StoryVerificationResolutionDecision,
        rationale: str,
    ) -> PlanningRunResponse:
        """Persist the owning Leader's semantic decision over a verifier handoff."""

        normalized_rationale = rationale.strip()
        if not normalized_rationale:
            raise WorkflowConflictError(
                "Story verification resolution rationale cannot be empty"
            )
        lock = self._planning_run_lock(run_id)
        if not lock.acquire(blocking=False):
            raise WorkflowConflictError(
                f"Content planning job {run_id} is already advancing"
            )
        try:
            run = self._require_planning_run(run_id)
            if run.project_id != project_id:
                raise WorkflowConflictError(
                    "Story verification resolution targets another project"
                )
            if run.owner_agent_run_id != owner_agent_run_id:
                raise WorkflowConflictError(
                    "Story verification resolution must be made by the owning AgentRun"
                )
            if run.status != PlanningRunStatus.NEEDS_ATTENTION:
                raise WorkflowConflictError(
                    "Content planning job is not awaiting a Leader verification decision"
                )
            source = self._pending_story_verification_source(run)
            if source is None:
                raise WorkflowConflictError(
                    "Content planning job has no pending story-verifier handoff"
                )
            resolution = StoryVerificationResolution(
                source=source,
                decision=decision,
                rationale=normalized_rationale,
                owner_agent_run_id=owner_agent_run_id,
                script_review_report=(
                    run.review_report.model_copy(deep=True)
                    if source == StoryVerificationSource.SCRIPT_VERIFIER
                    and run.review_report is not None
                    else None
                ),
                revision_verification=(
                    run.latest_revision_verification.model_copy(deep=True)
                    if source == StoryVerificationSource.REVISION_VERIFIER
                    and run.latest_revision_verification is not None
                    else None
                ),
                created_at=utc_now(),
            )
            run.story_verification_resolutions.append(resolution)
            run.error_message = None
            run.status = PlanningRunStatus.RUNNING

            if decision == StoryVerificationResolutionDecision.ACCEPT_TRADEOFF:
                self._accept_story_verification_tradeoff(
                    run,
                    source=source,
                    rationale=normalized_rationale,
                )
                self._clear_revision_execution_state(run)
                self._complete_planning_step(
                    run,
                    run.current_step,
                    self._post_script_planning_step(run),
                )
                run.warnings.append(
                    "AgentRun Leader 明确接受当前 Story Verifier 取舍；"
                    "原始 rejected 或时长契约结果保持不变并已记入 issue ledger："
                    + normalized_rationale
                )
            elif (
                decision
                == StoryVerificationResolutionDecision.RETURN_TO_STORY_ARCHITECT
            ):
                run.step_retry_feedback = self._story_verification_handoff_json(
                    resolution
                )
                self._clear_revision_execution_state(run)
                self._complete_planning_step(
                    run,
                    run.current_step,
                    PlanningRunStep.STORY_ARCHITECT,
                )
                run.warnings.append(
                    "AgentRun Leader 已阅读精确 Verifier 结果并决定返回 Story Architect："
                    + normalized_rationale
                )
            else:
                focused_review = self._focused_story_verification_review(
                    run,
                    source=source,
                )
                run.review_report = focused_review
                run.review_disposition = ScriptReviewDisposition(
                    action=ScriptReviewDispositionAction.REVISE_STORY,
                    issue_routes=[
                        ScriptReviewIssueRoute(
                            issue_index=index,
                            owner=(
                                issue.owner
                                if source
                                == StoryVerificationSource.SCRIPT_VERIFIER
                                else ReviewRepairOwner.STORY_ARCHITECT
                            ),
                            rationale=(
                                "AgentRun Leader 选择继续聚焦处理；保留初次 "
                                "Script Verifier 已给出的专业 owner，修订项由 "
                                "Story Repair Leader 基于完整语义再决定范围。"
                            ),
                        )
                        for index, issue in enumerate(
                            focused_review.issues,
                            start=1,
                        )
                    ],
                    rationale=(
                        "保留 Verifier 的精确问题证据，先交给 Story Repair Leader "
                        "重新规划修复范围，不由 service 直接排入 Writer。"
                    ),
                )
                run.issue_ledger = self._merge_story_issue_ledger(
                    run.issue_ledger,
                    focused_review,
                    phase="AgentRun Leader 接管 Verifier handoff",
                )
                run.step_retry_feedback = self._story_verification_handoff_json(
                    resolution
                )
                self._clear_revision_execution_state(run)
                self._complete_planning_step(
                    run,
                    run.current_step,
                    PlanningRunStep.STORY_REVISION,
                )
                run.warnings.append(
                    "AgentRun Leader 已将 Verifier findings 转为聚焦 review/disposition；"
                    "下一步由 Story Repair Leader 重新判断修订范围："
                    + normalized_rationale
                )

            if (
                source == StoryVerificationSource.SCRIPT_VERIFIER
                and decision
                != StoryVerificationResolutionDecision.RETURN_TO_STORY_ARCHITECT
                and run.review_report is not None
                and run.review_disposition is not None
            ):
                self.repository.save_planning_review_snapshot(
                    run.id,
                    PlanningReviewSnapshot(
                        review_round=run.revision_round + 1,
                        revision_round=run.revision_round,
                        report=run.review_report.model_copy(deep=True),
                        disposition=run.review_disposition.model_copy(deep=True),
                        created_at=utc_now(),
                    ),
                )

            run.updated_at = utc_now()
            self._save_planning_run_state(run)
            return self._planning_run_response(run)
        finally:
            lock.release()

    def start_script_revision(
        self,
        project_id: str,
        *,
        owner_agent_run_id: str,
        instruction: str,
        base_script_version_id: str,
        revision_origin: ScriptRevisionOrigin = ScriptRevisionOrigin.USER_FEEDBACK,
        evidence_refs: list[ProductionDocumentArtifactRef] | None = None,
    ) -> PlanningRunResponse:
        """Start an Agent-owned screenplay revision on the current project truth."""

        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise WorkflowConflictError("Script revision instruction cannot be empty")
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            revision_evidence_refs = self._validate_script_revision_evidence_refs(
                document,
                list(evidence_refs or []),
                revision_origin=revision_origin,
                base_script_version_id=base_script_version_id,
            )
            current_script = self._current_script(document)
            if current_script.id != base_script_version_id:
                raise WorkflowConflictError(
                    "Script revision must start from the current ScriptVersion"
                )
            if (
                document.planning is not None
                and document.planning.status != PlanningRunStatus.COMPLETE
            ):
                raise WorkflowConflictError(
                    "The project already has an unfinished Content Planning Job"
                )
            current_bible = next(
                (
                    version
                    for version in document.bible_versions
                    if version.id == document.project.current_bible_version_id
                ),
                None,
            )
            if current_bible is None:
                raise WorkflowConflictError(
                    "Project current ProjectBibleVersion is missing"
                )
            request = self._script_revision_request(document, current_script)
            outline = self._script_revision_outline(
                document,
                current_script,
                current_bible,
            )
            rebuild_duration_envelope = outline is None
            source_contract = (
                current_script.source_contract.model_copy(deep=True)
                if current_script.source_contract is not None
                else build_story_source_contract(request)
            )
            if revision_origin == ScriptRevisionOrigin.USER_FEEDBACK:
                source_contract.authoritative_user_overrides = [
                    *source_contract.authoritative_user_overrides,
                    normalized_instruction,
                ]
                issue_prefix = "user-revision-"
                issue_category = "USER_REQUEST"
                issue_summary = "用户对当前完整剧本提出了新的权威修改意见。"
                issue_rationale = "这是用户对当前剧本的直接修改要求。"
                semantic_cluster = "user-requested-script-revision"
                evolution_note = "用户在当前 ScriptVersion 基础上提出新的修改意见。"
            else:
                issue_prefix = "duration-contract-"
                issue_category = "DURATION_CONTRACT"
                issue_summary = (
                    "已版本化的制片时长契约与当前剧本的自然分镜时长冲突。"
                )
                issue_rationale = (
                    "这是制片过程从精确 ProductionSpec 与 StoryboardDraft "
                    "证据中发现的剧本约束问题，不是新的用户要求。"
                )
                semantic_cluster = "duration-contract-reconciliation"
                evolution_note = (
                    "Agent 从精确 ProductionSpec、StoryboardDraft 和 ScriptVersion "
                    "证据中识别到时长契约冲突。"
                )
            issue_id = issue_prefix + hashlib.sha256(
                (
                    normalized_instruction
                    + "|"
                    + revision_origin.value
                    + "|"
                    + "|".join(
                        f"{item.artifact_type}:{item.artifact_id}:{item.version}"
                        for item in revision_evidence_refs
                    )
                ).encode("utf-8")
            ).hexdigest()[:16]
            review_report = ScriptReviewReport(
                summary=issue_summary,
                issues=[
                    ScriptReviewIssue(
                        issue_id=issue_id,
                        category=issue_category,
                        severity=ScriptReviewSeverity.MEDIUM,
                        owner=ReviewRepairOwner.STORY_ARCHITECT,
                        scene_sequences=[],
                        observation=normalized_instruction,
                        recommendation=normalized_instruction,
                    )
                ],
                requires_revision=True,
                followup_rationale=(
                    "由 Leader 根据原始要求、当前剧本和本轮意见决定最小连贯修改范围。"
                ),
            )
            review_disposition = ScriptReviewDisposition(
                action=ScriptReviewDispositionAction.REVISE_STORY,
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=1,
                        owner=ReviewRepairOwner.STORY_ARCHITECT,
                        rationale=issue_rationale,
                    )
                ],
                rationale=(
                    "交由 Leader 语义判断采用少数场 Patch、全场逐场修改，"
                    "或返回 Story Architect 重构蓝图。"
                ),
            )
            parent_planning_run = (
                self.repository.get_planning_run(current_script.planning_run_id)
                if current_script.planning_run_id is not None
                else None
            )
            now = utc_now()
            run = PlanningRunRecord(
                id=f"planning-run-{uuid4().hex}",
                operation=PlanningRunOperation.SCRIPT_REVISION,
                status=PlanningRunStatus.RUNNING,
                current_step=(
                    PlanningRunStep.STORY_ARCHITECT
                    if rebuild_duration_envelope
                    else PlanningRunStep.STORY_REVISION
                ),
                request=request,
                revision_instruction=normalized_instruction,
                revision_origin=revision_origin,
                revision_evidence_refs=revision_evidence_refs,
                base_script_version_id=current_script.id,
                base_bible_version_id=current_bible.id,
                parent_planning_run_id=current_script.planning_run_id,
                outline_json=(
                    None if outline is None else outline.model_dump_json()
                ),
                outline_source=(
                    None if outline is None else "current versioned screenplay"
                ),
                review_report=review_report,
                review_disposition=review_disposition,
                source_contract=source_contract,
                story_intent=(
                    current_script.story_intent.model_copy(deep=True)
                    if current_script.story_intent is not None
                    else (
                        None if outline is None else outline.story_intent
                    )
                ),
                selected_narrative_skill_ids=(
                    list(parent_planning_run.selected_narrative_skill_ids)
                    if parent_planning_run is not None
                    else []
                ),
                skill_selection_reasons=(
                    list(parent_planning_run.skill_selection_reasons)
                    if parent_planning_run is not None
                    else []
                ),
                narrative_skill_decision=(
                    parent_planning_run.narrative_skill_decision
                    if parent_planning_run is not None
                    else None
                ),
                selected_method_skill_ids=(
                    list(parent_planning_run.selected_method_skill_ids)
                    if parent_planning_run is not None
                    else []
                ),
                method_selection_reasons=(
                    list(parent_planning_run.method_selection_reasons)
                    if parent_planning_run is not None
                    else []
                ),
                method_selection_rationale=(
                    parent_planning_run.method_selection_rationale
                    if parent_planning_run is not None
                    else None
                ),
                issue_ledger=[
                    StoryIssueLedgerEntry(
                        issue_id=issue_id,
                        semantic_cluster=semantic_cluster,
                        state="OPEN",
                        observation=normalized_instruction,
                        evolution_notes=[evolution_note],
                    )
                ],
                step_retry_feedback=(
                    json.dumps(
                        {
                            "requested_by": "SCRIPT_REVISION_DURATION_ENVELOPE",
                            "reason": (
                                "The current ScriptVersion has scenes without a "
                                "versioned duration envelope, and no parent outline "
                                "can supply one. Rebuild the envelope semantically "
                                "from the exact screenplay and DurationContract; do "
                                "not divide a target evenly across scenes."
                            ),
                            "current_script": {
                                "id": current_script.id,
                                "version": current_script.version,
                                "screenplay_text": current_script.screenplay_text,
                                "structured_scenes": [
                                    scene.model_dump(mode="json")
                                    for scene in current_script.structured_scenes
                                ],
                                "story_intent": (
                                    current_script.story_intent.model_dump(mode="json")
                                    if current_script.story_intent is not None
                                    else None
                                ),
                            },
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if rebuild_duration_envelope
                    else None
                ),
                total_scenes=(
                    len(current_script.structured_scenes)
                    if outline is None
                    else len(outline.scenes)
                ),
                project_id=project_id,
                owner_agent_run_id=owner_agent_run_id,
                created_at=now,
                updated_at=now,
            )
            document.planning = ProjectPlanningMetadata(
                run_id=run.id,
                status=PlanningRunStatus.RUNNING,
                planner="agent-native screenplay revision",
                warnings=[
                    "已继承原始创作要求、当前 Script/Bible、StoryIntent、"
                    "SourceContract 与前序技能选择。"
                ],
                owner_agent_run_id=owner_agent_run_id,
                planning_job_kind="CONTENT_PLANNING",
            )
            document.project.updated_at = now
            self.repository.insert_planning_run_with_project(run, document)
        return self._planning_run_response(run)

    @staticmethod
    def _script_revision_request(
        document: ProjectDocument,
        current_script: ScriptVersion,
    ) -> CreateProjectRequest:
        project = document.project
        # SCRIPT_LOCKED describes the original intake contract.  A later,
        # explicit user revision is an editable working pass over the current
        # canonical version, so hard locked-dialogue validation must not reject
        # the new authoritative instruction.  The merge below restores the
        # ScriptVersion's original source provenance, while SourceContract and
        # the current outline tell the Agents what remains unchanged.
        revision_source_mode = (
            SourceMode.IDEA_EXPANSION
            if current_script.source_mode == SourceMode.SCRIPT_LOCKED
            else current_script.source_mode
        )
        return CreateProjectRequest(
            name=project.name,
            source_mode=revision_source_mode,
            production_profile=project.production_profile,
            content_planning_output=ContentPlanningOutput.SCRIPT_ONLY,
            target_duration_seconds=project.target_duration_seconds,
            input_text=project.input_text,
            input_images=[image.model_copy(deep=True) for image in project.input_images],
            linked_asset_project_ids=list(project.linked_asset_project_ids),
            run_mode=project.run_mode,
        )

    def _script_revision_outline(
        self,
        document: ProjectDocument,
        current_script: ScriptVersion,
        current_bible: ProjectBibleVersion,
    ) -> EpisodeOutline | None:
        bible = current_bible
        parent_outline: EpisodeOutline | None = None
        if current_script.planning_run_id is not None:
            parent_run = self.repository.get_planning_run(current_script.planning_run_id)
            if parent_run is not None and parent_run.outline_json is not None:
                try:
                    parent_outline = EpisodeOutline.model_validate_json(
                        parent_run.outline_json
                    )
                except ValueError:
                    parent_outline = None

        planned_lines = [
            PlannedDialogueLine(
                speaker=line.speaker,
                speaker_character_id=line.speaker_character_id,
                text=line.spoken_text,
                audio_kind=line.audio_kind,
            )
            for line in current_script.lines
        ]
        line_index_by_id = {
            line.id: index for index, line in enumerate(current_script.lines, start=1)
        }
        location_names = {location.name for location in bible.locations}
        default_location = (
            bible.locations[0].name if bible.locations else "未声明场景"
        )
        for index, scene in enumerate(current_script.structured_scenes, start=1):
            parent_scene = (
                parent_outline.scenes[index - 1]
                if parent_outline is not None and index <= len(parent_outline.scenes)
                else None
            )
            if (
                scene.target_duration_seconds is None
                and parent_scene is None
            ):
                return None
        scenes: list[PlannedSceneContract] = []
        for index, scene in enumerate(current_script.structured_scenes, start=1):
            parent_scene = (
                parent_outline.scenes[index - 1]
                if parent_outline is not None and index <= len(parent_outline.scenes)
                else None
            )
            inherited_locations = (
                [
                    name
                    for name in parent_scene.location_names
                    if name in location_names
                ]
                if parent_scene is not None
                else []
            )
            setting = (
                scene.entry_state.setting
                if scene.entry_state is not None
                else default_location
            )
            resolved_locations = inherited_locations or (
                [setting] if setting in location_names else [default_location]
            )
            resolved_duration = scene.target_duration_seconds
            if resolved_duration is None:
                if parent_scene is None:
                    raise WorkflowConflictError(
                        "Script revision duration envelope became unresolved"
                    )
                resolved_duration = parent_scene.target_duration_seconds
            scenes.append(
                PlannedSceneContract(
                    sequence=index,
                    heading=scene.heading,
                    summary=scene.summary,
                    narrative_purpose=(
                        scene.narrative_purpose
                        or (
                            parent_scene.narrative_purpose
                            if parent_scene is not None
                            else scene.summary
                        )
                    ),
                    playable_actions=(
                        list(parent_scene.playable_actions)
                        if parent_scene is not None
                        else [scene.summary]
                    ),
                    screenplay_text=scene.screenplay_text,
                    target_duration_seconds=resolved_duration,
                    dialogue_line_indices=[
                        line_index_by_id[line_id]
                        for line_id in scene.line_ids
                        if line_id in line_index_by_id
                    ],
                    location_names=resolved_locations,
                    prop_ids=(
                        [
                            prop_id
                            for prop_id in parent_scene.prop_ids
                            if any(prop.id == prop_id for prop in bible.props)
                        ]
                        if parent_scene is not None
                        else []
                    ),
                    continuity_in=(
                        scene.entry_state.model_copy(deep=True)
                        if scene.entry_state is not None
                        else (
                            parent_scene.continuity_in.model_copy(deep=True)
                            if parent_scene is not None
                            else SceneContinuitySummary(setting=resolved_locations[0])
                        )
                    ),
                    continuity_out=(
                        scene.exit_state.model_copy(deep=True)
                        if scene.exit_state is not None
                        else (
                            parent_scene.continuity_out.model_copy(deep=True)
                            if parent_scene is not None
                            else SceneContinuitySummary(setting=resolved_locations[0])
                        )
                    ),
                )
            )
        if not scenes:
            raise WorkflowConflictError(
                "Current ScriptVersion has no structured scenes to revise"
            )
        return EpisodeOutline(
            title=(
                parent_outline.title
                if parent_outline is not None
                else document.project.name
            ),
            story_summary=(
                parent_outline.story_summary
                if parent_outline is not None
                else "\n".join(scene.summary for scene in current_script.structured_scenes)
            ),
            target_duration_seconds=document.project.target_duration_seconds,
            lines=planned_lines,
            characters=[
                PlannedCharacter(
                    character_id=character.id,
                    name=character.name,
                    age=character.age,
                    appearance=character.appearance,
                    wardrobe=character.wardrobe,
                    performance=character.performance,
                    voice_identity=character.voice_identity,
                )
                for character in bible.characters
            ],
            locations=[
                PlannedLocation(
                    location_id=location.id,
                    name=location.name,
                    layout=location.layout,
                    lighting=location.lighting,
                    palette=location.palette,
                    key_views=list(location.key_views),
                )
                for location in bible.locations
            ],
            props=[
                PlannedProp(
                    prop_id=prop.id,
                    name=prop.name,
                    appearance=prop.appearance,
                )
                for prop in bible.props
            ],
            style=PlannedStyle(
                medium=bible.style.medium,
                genre=bible.style.genre,
                cinematography=bible.style.cinematography,
                color=bible.style.color,
                texture=bible.style.texture,
                forbidden=list(bible.style.forbidden),
            ),
            ending_contract=(
                current_script.ending_contract.model_copy(deep=True)
                if current_script.ending_contract is not None
                else (
                    parent_outline.ending_contract.model_copy(deep=True)
                    if parent_outline is not None
                    and parent_outline.ending_contract is not None
                    else None
                )
            ),
            story_intent=(
                current_script.story_intent.model_copy(deep=True)
                if current_script.story_intent is not None
                else (
                    parent_outline.story_intent.model_copy(deep=True)
                    if parent_outline is not None
                    and parent_outline.story_intent is not None
                    else None
                )
            ),
            scenes=scenes,
        )

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
                if self._pending_story_verification_source(run) is not None:
                    raise WorkflowConflictError(
                        "Story verification is awaiting an explicit AgentRun Leader "
                        "resolution; call planning.resolve_story_verification instead "
                        "of advancing the verifier again"
                    )
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
            run.selected_method_skill_ids = [
                selection.skill_id for selection in orientation.selected_method_skills
            ]
            run.method_selection_reasons = [
                selection.rationale for selection in orientation.selected_method_skills
            ]
            run.method_selection_rationale = orientation.method_selection_rationale
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
            if orientation.selected_method_skills:
                run.warnings.append(
                    "Story Orientation Agent 已基于完整项目语义选择制片方法："
                    + "；".join(
                        f"{selection.skill_id}（{selection.rationale}）"
                        for selection in orientation.selected_method_skills
                    )
                )
            elif orientation.method_selection_rationale:
                run.warnings.append(
                    "Story Orientation Agent 的制片方法判断："
                    + orientation.method_selection_rationale
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
                selected_method_skill_ids=tuple(run.selected_method_skill_ids),
                retry_feedback=run.step_retry_feedback,
                revision_instruction=(
                    run.revision_instruction
                    if run.operation == PlanningRunOperation.SCRIPT_REVISION
                    else None
                ),
                current_outline=(
                    self._planning_outline(run)
                    if run.operation == PlanningRunOperation.SCRIPT_REVISION
                    and run.outline_json is not None
                    else None
                ),
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
                selected_skill_prompt="\n\n".join(
                    prompt
                    for prompt in (
                        compile_narrative_skill_prompt(
                            run.selected_narrative_skill_ids,
                            roles=(ProductionSkillRole.WRITER,),
                        ),
                        compile_production_method_skill_prompt(
                            _method_skill_ids_for_role(
                                run.selected_method_skill_ids,
                                ProductionSkillRole.WRITER,
                            ),
                            roles=(ProductionSkillRole.WRITER,),
                        ),
                    )
                    if prompt
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
                    PlanningRunStep.SCRIPT_VERIFIER,
                )
            return

        if run.current_step in {
            PlanningRunStep.SCRIPT_VERIFIER,
            PlanningRunStep.SCRIPT_CRITIC,
        }:
            verifier_step = run.current_step
            critique = review_story_outline(
                request,
                outline,
                client,
                source_contract=run.source_contract,
                issue_ledger=run.issue_ledger,
                selected_narrative_skill_ids=tuple(
                    run.selected_narrative_skill_ids
                ),
                selected_method_skill_ids=tuple(run.selected_method_skill_ids),
                retry_feedback=run.step_retry_feedback,
            )
            if client is not None and (not critique.used or critique.report is None):
                self._defer_planning_step_retry(
                    run,
                    verifier_step,
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
                if (
                    critique.report.requires_revision
                    or self._duration_fit_requires_leader_resolution(
                        run,
                        report=critique.report,
                    )
                ):
                    run.status = PlanningRunStatus.NEEDS_ATTENTION
                    run.error_message = (
                        "Script Verifier 已交回 requires_revision 或 "
                        "需要 Leader 处理的 duration_fit_assessment 精确结果；"
                        "等待所属 AgentRun Leader 通过 "
                        "planning.resolve_story_verification 选择聚焦修订、"
                        "返回 Story Architect 或接受明确取舍。"
                    )
                    run.warnings.append(
                        "Script Verifier 的精确 review report 已持久化并交回 "
                        "AgentRun Leader；service 没有自动路由到 Writer，"
                        "也没有自动开启下一轮。"
                    )
                    return
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
                verifier_step,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.REVIEW_ROUTER:
            # Compatibility for jobs persisted before routing was folded into
            # the Verifier unit. New jobs never stop on this compatibility step.
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
                selected_method_skill_ids=tuple(run.selected_method_skill_ids),
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
                                        "Source-contract-aware Leader repair routing "
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
                        "Source-contract-aware Leader repair routing found no supported "
                        "story change; retain the findings as optional or downstream "
                        "handoff instead of forcing a rewrite."
                    ),
                )
                run.warnings.append(
                    "Leader 判定当前 Verifier findings 均不应修订，"
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
            self._normalize_active_repair_issue_ids(run)
            active_actions = [
                action
                for action in planning.plan.actions
                if action.should_revise
            ]
            structural_actions = [
                action
                for action in active_actions
                if not action.affected_scene_sequences
                and not action.metadata_targets
            ]
            if structural_actions:
                run.step_retry_feedback = json.dumps(
                    {
                        "requested_by": "STORY_REPAIR_LEADER",
                        "reason": (
                            "The semantic repair plan identified a structural story "
                            "problem that cannot be safely assigned to concrete scenes."
                        ),
                        "repair_summary": planning.plan.summary,
                        "structural_actions": [
                            action.model_dump(mode="json")
                            for action in structural_actions
                        ],
                        "preserve": planning.plan.invariants,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                self._clear_revision_execution_state(run)
                run.warnings.append(
                    "Leader 判定返修包含无法定位到具体场次的结构问题；"
                    "已携带语义返修计划回到 Story Architect，"
                    "不将全集问题伪装成逐场 Patch。"
                )
                next_step = PlanningRunStep.STORY_ARCHITECT
            else:
                scene_sequences = [
                    sequence
                    for action in active_actions
                    for sequence in action.affected_scene_sequences
                ]
                if scene_sequences:
                    self._set_scene_revision_queue(
                        run,
                        outline,
                        scene_sequences,
                        source="Leader 语义返修计划",
                    )
                metadata_targets = {
                    target
                    for action in active_actions
                    for target in action.metadata_targets
                }
                next_step = (
                    PlanningRunStep.STORY_METADATA_REVISION
                    if metadata_targets
                    else PlanningRunStep.SCENE_REVISION
                )
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
                selected_method_skill_ids=tuple(run.selected_method_skill_ids),
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
            if run.operation == PlanningRunOperation.SCRIPT_REVISION:
                with self._lock:
                    current = self.repository.get_project(run.project_id)
                    if current is None:
                        raise WorkflowConflictError(
                            f"Owner project is missing: {run.project_id}"
                        )
                    if not self._owner_planning_project_is_prepared(current, run):
                        self._merge_script_revision(
                            current,
                            run,
                            planning,
                        )
                        self.repository.save_project(current)
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
                or "当前 Script Verifier 冷读已足以判断剧本可交付。"
            )
            warning = (
                "Script Verifier 已完成整稿冷读，直接继续交付，"
                "不再自动转交独立 Final Cold Read："
                + rationale
            )
            if warning not in run.warnings:
                run.warnings.append(warning)
        return ShortDramaService._post_cold_read_step(run)

    @classmethod
    def _duration_fit_requires_leader_resolution(
        cls,
        run: PlanningRunRecord,
        report: ScriptReviewReport | None = None,
        *,
        verification: object | None = None,
    ) -> bool:
        assessment = (
            report.duration_fit_assessment
            if report is not None
            else getattr(verification, "duration_fit_assessment", None)
        )
        if assessment is None:
            return False
        if assessment.alignment == DurationAlignment.UNRESOLVED:
            return True
        if assessment.alignment != DurationAlignment.OUTSIDE_ACCEPTED_RANGE:
            return False
        story_intent = run.story_intent
        contract = None if story_intent is None else story_intent.duration_contract
        return (
            contract is not None
            and contract.authority == DurationAuthority.TARGET_LED
        )

    @classmethod
    def _pending_story_verification_source(
        cls,
        run: PlanningRunRecord,
    ) -> StoryVerificationSource | None:
        if run.status != PlanningRunStatus.NEEDS_ATTENTION:
            return None
        if run.current_step in {
            PlanningRunStep.SCRIPT_VERIFIER,
            PlanningRunStep.SCRIPT_CRITIC,
        }:
            report = run.review_report
            if report is not None and (
                report.requires_revision
                or cls._duration_fit_requires_leader_resolution(
                    run,
                    report=report,
                )
            ):
                return StoryVerificationSource.SCRIPT_VERIFIER
        verification = run.latest_revision_verification
        if (
            run.current_step == PlanningRunStep.REVISION_VERIFIER
            and verification is not None
            and (
                not verification.accepted
                or cls._duration_fit_requires_leader_resolution(
                    run,
                    verification=verification,
                )
            )
        ):
            return StoryVerificationSource.REVISION_VERIFIER
        return None

    @staticmethod
    def _story_verification_handoff_json(
        resolution: StoryVerificationResolution,
    ) -> str:
        return json.dumps(
            {
                "requested_by": "AGENT_RUN_LEADER",
                "story_verification_resolution": resolution.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _duration_resolution_issue_id(
        source: StoryVerificationSource,
        payload: str,
    ) -> str:
        return (
            "duration-assessment-"
            + hashlib.sha256((source.value + "|" + payload).encode("utf-8"))
            .hexdigest()[:16]
        )

    @classmethod
    def _focused_story_verification_review(
        cls,
        run: PlanningRunRecord,
        *,
        source: StoryVerificationSource,
    ) -> ScriptReviewReport:
        issues: list[ScriptReviewIssue] = []
        assessment = None
        performance_timing_assessment = None
        if source == StoryVerificationSource.SCRIPT_VERIFIER:
            report = run.review_report
            if report is None:
                raise WorkflowConflictError(
                    "Script Verifier handoff is missing its exact review report"
                )
            issues = [issue.model_copy(deep=True) for issue in report.issues]
            assessment = report.duration_fit_assessment
            performance_timing_assessment = report.performance_timing_assessment
            source_payload = report.model_dump_json()
            summary = (
                "AgentRun Leader 接管 Script Verifier 的问题与时长契约判断，"
                "交由 Story Repair Leader 重新确定最小修订范围。"
            )
        else:
            verification = run.latest_revision_verification
            if verification is None:
                raise WorkflowConflictError(
                    "Revision Verifier handoff is missing its exact verification"
                )
            issues = [
                ScriptReviewIssue(
                    issue_id=violation.issue_id,
                    category=violation.category,
                    severity=ScriptReviewSeverity.MEDIUM,
                    owner=ReviewRepairOwner.STORY_ARCHITECT,
                    scene_sequences=list(violation.scene_sequences),
                    observation=violation.observation,
                    recommendation=violation.repair_instruction,
                )
                for violation in verification.violations
            ]
            assessment = verification.duration_fit_assessment
            source_payload = verification.model_dump_json()
            summary = (
                "AgentRun Leader 接管 Revision Verifier 的精确 violations，"
                "交由 Story Repair Leader 重新判断结构、元数据或场次范围。"
            )

        duration_requires_resolution = cls._duration_fit_requires_leader_resolution(
            run,
            report=(
                run.review_report
                if source == StoryVerificationSource.SCRIPT_VERIFIER
                else None
            ),
            verification=(
                run.latest_revision_verification
                if source == StoryVerificationSource.REVISION_VERIFIER
                else None
            ),
        )
        duration_issue_category = (
            "DURATION_ASSESSMENT_UNRESOLVED"
            if assessment is not None
            and assessment.alignment == DurationAlignment.UNRESOLVED
            else "DURATION_CONTRACT_OUTSIDE_ACCEPTED_RANGE"
        )
        if duration_requires_resolution and not any(
            issue.category == duration_issue_category for issue in issues
        ):
            unresolved = (
                assessment is not None
                and assessment.alignment == DurationAlignment.UNRESOLVED
            )
            issues.append(
                ScriptReviewIssue(
                    issue_id=cls._duration_resolution_issue_id(
                        source,
                        source_payload,
                    ),
                    category=duration_issue_category,
                    severity=ScriptReviewSeverity.MEDIUM,
                    owner=ReviewRepairOwner.STORY_ARCHITECT,
                    scene_sequences=[],
                    observation=(
                        "Verifier 没有完成当前剧本相对 DurationContract 的时长判断；"
                        "UNRESOLVED 只表示证据不足，不表示剧情本身存在缺陷。"
                        if unresolved
                        else (
                            "Verifier 判断当前剧本的自然表演时长超出 "
                            "TARGET_LED DurationContract 的可接受范围；"
                            "这是需要 Leader 选择修订或接受的创作取舍。"
                        )
                    ),
                    recommendation=(
                        "结合完整剧本、DurationContract、压力点与可并发表演重新判断；"
                        "只有语义证据支持时才修改剧本，否则保留为明确取舍。"
                        if unresolved
                        else (
                            "由 Leader 结合剧情承诺、节奏形状和具体压力点，"
                            "选择聚焦修订、返回 Story Architect 或接受该时长取舍。"
                        )
                    ),
                )
            )
        if not issues:
            raise WorkflowConflictError(
                "Focused story revision requires verifier findings or a duration "
                "assessment that needs Leader resolution"
            )
        return ScriptReviewReport(
            summary=summary,
            strengths=[],
            issues=issues,
            performance_timing_assessment=performance_timing_assessment,
            duration_fit_assessment=assessment,
            requires_revision=True,
            followup_rationale=(
                "本报告是 AgentRun Leader 对精确 Verifier handoff 的聚焦投影；"
                "原始 Verifier 结论保存在 resolution history 中。"
            ),
        )

    @classmethod
    def _accept_story_verification_tradeoff(
        cls,
        run: PlanningRunRecord,
        *,
        source: StoryVerificationSource,
        rationale: str,
    ) -> None:
        issue_ids: set[str] = set()
        duration_payload: str | None = None
        if source == StoryVerificationSource.SCRIPT_VERIFIER:
            report = run.review_report
            if report is None:
                raise WorkflowConflictError(
                    "Script Verifier handoff is missing its exact review report"
                )
            issue_ids.update(
                issue.issue_id for issue in report.issues if issue.issue_id is not None
            )
            if cls._duration_fit_requires_leader_resolution(run, report=report):
                duration_payload = report.model_dump_json()
        else:
            verification = run.latest_revision_verification
            if verification is None:
                raise WorkflowConflictError(
                    "Revision Verifier handoff is missing its exact verification"
                )
            issue_ids.update(
                violation.issue_id
                for violation in verification.violations
                if violation.issue_id is not None
            )
            if cls._duration_fit_requires_leader_resolution(
                run,
                verification=verification,
            ):
                duration_payload = verification.model_dump_json()

        if duration_payload is not None:
            duration_issue_id = cls._duration_resolution_issue_id(
                source,
                duration_payload,
            )
            issue_ids.add(duration_issue_id)
            if not any(
                entry.issue_id == duration_issue_id for entry in run.issue_ledger
            ):
                assessment = (
                    run.review_report.duration_fit_assessment
                    if source == StoryVerificationSource.SCRIPT_VERIFIER
                    and run.review_report is not None
                    else (
                        run.latest_revision_verification.duration_fit_assessment
                        if run.latest_revision_verification is not None
                        else None
                    )
                )
                unresolved = (
                    assessment is not None
                    and assessment.alignment == DurationAlignment.UNRESOLVED
                )
                run.issue_ledger.append(
                    StoryIssueLedgerEntry(
                        issue_id=duration_issue_id,
                        semantic_cluster=(
                            "duration-assessment-unresolved"
                            if unresolved
                            else "duration-contract-outside-accepted-range"
                        ),
                        state=(
                            "Leader 明确接受未决时长判断"
                            if unresolved
                            else "Leader 明确接受 TARGET_LED 时长取舍"
                        ),
                        observation=(
                            "Verifier 的 duration_fit_assessment 为 UNRESOLVED；"
                            "该记录不被伪造成 ALIGNED。"
                            if unresolved
                            else (
                                "Verifier 判断当前自然表演时长为 "
                                "OUTSIDE_ACCEPTED_RANGE；该记录保持原始结论。"
                            )
                        ),
                        evolution_notes=[
                            "AgentRun Leader 阅读精确 Verifier 结果后接受当前取舍。"
                        ],
                        accepted_tradeoff=rationale,
                    )
                )

        updated_ledger: list[StoryIssueLedgerEntry] = []
        for entry in run.issue_ledger:
            if entry.issue_id not in issue_ids:
                updated_ledger.append(entry.model_copy(deep=True))
                continue
            notes = list(entry.evolution_notes)
            note = (
                "AgentRun Leader 显式接受该取舍；Verifier 原始结论保持不变，"
                "不视为已通过验收。"
            )
            if note not in notes:
                notes.append(note)
            updated_ledger.append(
                entry.model_copy(
                    deep=True,
                    update={
                        "state": "Leader 已接受明确取舍，保留 Verifier 原结论",
                        "accepted_tradeoff": rationale,
                        "evolution_notes": notes,
                    },
                )
            )
        run.issue_ledger = updated_ledger

        report = run.review_report
        if report is not None:
            run.review_disposition = ScriptReviewDisposition(
                action=(
                    ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
                    if report.issues
                    else ScriptReviewDispositionAction.CONTINUE
                ),
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=index,
                        owner=ReviewRepairOwner.USER_OPTIONAL,
                        rationale=(
                            "AgentRun Leader 已明确接受该取舍；保留原始 Verifier "
                            "记录，后续仍可由用户重新开启修订。"
                        ),
                    )
                    for index, _issue in enumerate(report.issues, start=1)
                ],
                rationale=(
                    "AgentRun Leader 接受当前交付取舍；这不会把 rejected 或 "
                    "UNRESOLVED Verifier 结果改写成 accepted。"
                ),
            )

    @staticmethod
    def _accepted_tradeoff_matches_current_verification(
        run: PlanningRunRecord,
    ) -> bool:
        if not run.story_verification_resolutions:
            return False
        resolution = run.story_verification_resolutions[-1]
        if (
            resolution.decision
            != StoryVerificationResolutionDecision.ACCEPT_TRADEOFF
        ):
            return False
        if resolution.source == StoryVerificationSource.SCRIPT_VERIFIER:
            return (
                resolution.script_review_report is not None
                and resolution.script_review_report == run.review_report
            )
        return (
            resolution.revision_verification is not None
            and resolution.revision_verification
            == run.latest_revision_verification
        )

    def _route_current_story_review(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        outline: EpisodeOutline,
        client: LanguageProviderClient | None,
    ) -> PlanningRunStep:
        """Apply the Verifier's semantic decision without a second review Agent."""

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
            and not ShortDramaService._accepted_tradeoff_matches_current_verification(
                run
            )
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
        scene_total_milliseconds = sum(
            _duration_milliseconds(item.target_duration_seconds)
            for item in outline.scenes
        )
        return SceneDurationRevisionConstraint(
            allow_change=allow_change,
            balance_episode_on_this_scene=False,
            episode_target_seconds=outline.target_duration_seconds,
            current_episode_scene_total_seconds=scene_total_milliseconds / 1000,
            required_scene_target_seconds=None,
        )

    @staticmethod
    def _scene_duration_candidate_observation(
        outline: EpisodeOutline,
        scene: PlannedSceneContract,
        patch: EpisodeScenePatch,
        constraint: SceneDurationRevisionConstraint,
    ) -> str | None:
        """Describe duration drift for the Verifier without rejecting the patch."""

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
        if prospective_total_milliseconds == target_milliseconds:
            return None
        return (
            f"第 {scene.sequence} 场修订后，各场目标时长合计约为 "
            f"{prospective_total_milliseconds / 1000:g} 秒，项目目标约为 "
            f"{target_milliseconds / 1000:g} 秒。这是供 Verifier 判断的创作证据，"
            "不是拒绝本次修订的程序门禁。"
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
            "latest_user_revision_instruction": run.revision_instruction,
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
        """Upgrade an old in-flight repair plan using its routed Verifier review."""

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
                "Leader 已定位全部返修场次；"
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

    @staticmethod
    def _set_scene_revision_queue(
        run: PlanningRunRecord,
        outline: EpisodeOutline,
        scene_sequences: list[int],
        *,
        source: str,
    ) -> None:
        """Persist an Agent-selected scene queue without another scope model call."""

        selected = list(dict.fromkeys(sorted(scene_sequences)))
        known_sequences = {scene.sequence for scene in outline.scenes}
        unknown = sorted(set(selected) - known_sequences)
        if unknown:
            raise WorkflowConflictError(
                f"{source} references unknown scenes: "
                + ", ".join(str(sequence) for sequence in unknown)
            )
        if not selected:
            raise WorkflowConflictError(
                f"{source} did not identify a concrete scene repair scope"
            )
        run.revision_scene_sequences = selected
        run.latest_revision_target_scene_sequences = list(
            dict.fromkeys(
                [
                    *run.latest_revision_target_scene_sequences,
                    *selected,
                ]
            )
        )
        run.next_revision_scene_index = 0
        ShortDramaService._clear_scene_revision_retry(run)

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
            selected_method_skill_ids=tuple(run.selected_method_skill_ids),
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
            run.story_intent = result.outline.story_intent
        has_typed_metadata_work = any(
            action.should_revise and action.metadata_targets
            for action in run.latest_repair_plan.actions
        ) or any(
            violation.metadata_targets
            for violation in (
                run.latest_revision_verification.violations
                if is_verifier_repair
                and run.latest_revision_verification is not None
                else []
            )
        )
        self._complete_planning_step(
            run,
            PlanningRunStep.STORY_METADATA_REVISION,
            (
                (
                    PlanningRunStep.SCENE_REVISION
                    if run.revision_scene_sequences
                    else PlanningRunStep.REVISION_VERIFIER
                )
                if has_typed_metadata_work
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
            story_intent=outline.story_intent or run.story_intent,
            selected_skill_prompt="\n\n".join(
                prompt
                for prompt in (
                    compile_narrative_skill_prompt(
                        run.selected_narrative_skill_ids,
                        roles=(ProductionSkillRole.WRITER,),
                    ),
                    compile_production_method_skill_prompt(
                        _method_skill_ids_for_role(
                            run.selected_method_skill_ids,
                            ProductionSkillRole.WRITER,
                        ),
                        roles=(ProductionSkillRole.WRITER,),
                    ),
                )
                if prompt
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
            duration_observation = self._scene_duration_candidate_observation(
                outline,
                scene,
                result.patch,
                duration_constraint,
            )
            if duration_observation is not None:
                run.warnings.append(duration_observation)
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
            selected_method_skill_ids=tuple(run.selected_method_skill_ids),
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
        if (
            not verification.accepted
            or self._duration_fit_requires_leader_resolution(
                run,
                verification=verification,
            )
        ):
            run.status = PlanningRunStatus.NEEDS_ATTENTION
            run.error_message = (
                "Story Revision Verifier 已交回需要 Leader 语义决策的"
                " rejected 或时长契约结果；请使用 "
                "planning.resolve_story_verification 选择聚焦修订、"
                "返回 Story Architect 或接受明确取舍。"
            )
            run.warnings.append(
                "Story Revision Verifier 的精确结果已持久化并交回 "
                "AgentRun Leader；service 未直接将 violation 排入 Writer，"
                "也未自动开启下一轮。"
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
                        "已解决的故事问题不再触发重复全稿审校或循环修订，"
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
            if selected:
                run.warnings.append(
                    f"第 {run.revision_round} 轮已逐场应用剧本 Patch；"
                    f"产生修改的场次为 {selected}，未调用整集重写。"
                )
            else:
                run.warnings.append(
                    f"第 {run.revision_round} 轮只对齐了全局故事元数据；"
                    "未调用 Story Architect 或场次 Writer。"
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

        if run.operation == PlanningRunOperation.SCRIPT_REVISION:
            return (
                document.planning is not None
                and document.planning.run_id == run.id
                and any(
                    version.planning_run_id == run.id
                    for version in document.script_versions
                )
            )
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

    def _merge_script_revision(
        self,
        document: ProjectDocument,
        run: PlanningRunRecord,
        planning: ProductionPlanningResult,
    ) -> None:
        """Append an accepted revision without replacing the project document."""

        current_script = self._current_script(document)
        if current_script.id != run.base_script_version_id:
            raise WorkflowConflictError(
                "Current ScriptVersion changed after this revision job started"
            )
        current_bible = next(
            (
                version
                for version in document.bible_versions
                if version.id == document.project.current_bible_version_id
            ),
            None,
        )
        if current_bible is None:
            raise WorkflowConflictError("Project current ProjectBibleVersion is missing")
        if current_bible.id != run.base_bible_version_id:
            raise WorkflowConflictError(
                "Current ProjectBibleVersion changed after this revision job started"
            )
        now = utc_now()
        materialized = build_project_document_from_plan(
            project_id=run.project_id,
            request=run.request,
            planning=planning,
            created_at=now,
            planning_run_id=run.id,
            source_contract=run.source_contract,
            planning_status=PlanningRunStatus.RUNNING,
            owner_agent_run_id=run.owner_agent_run_id,
        )
        candidate_script = materialized.script_versions[0]
        candidate_bible = materialized.bible_versions[0]
        next_script_number = max(
            version.version for version in document.script_versions
        ) + 1
        structural_rewrite = PlanningRunStep.STORY_ARCHITECT in run.completed_steps
        old_scenes = list(current_script.structured_scenes)
        old_scene_by_heading: dict[str, list[StructuredScene]] = {}
        for scene in old_scenes:
            old_scene_by_heading.setdefault(scene.heading, []).append(scene)
        matched_old_scene_by_candidate_id: dict[str, StructuredScene] = {}
        used_old_scene_ids: set[str] = set()
        for index, scene in enumerate(candidate_script.structured_scenes, start=1):
            matched: StructuredScene | None = None
            if not structural_rewrite and index <= len(old_scenes):
                matched = old_scenes[index - 1]
            elif structural_rewrite:
                matches = [
                    item
                    for item in old_scene_by_heading.get(scene.heading, [])
                    if item.id not in used_old_scene_ids
                ]
                if len(matches) == 1:
                    matched = matches[0]
            if matched is not None:
                scene.id = matched.id
                used_old_scene_ids.add(matched.id)
                matched_old_scene_by_candidate_id[scene.id] = matched
            else:
                scene.id = (
                    f"{run.project_id}-scene-v{next_script_number}-{index:02d}"
                )

        candidate_line_by_id = {line.id: line for line in candidate_script.lines}
        assigned_candidate_line_ids: set[str] = set()
        used_line_ids: set[str] = set()
        next_line_suffix = 1
        for scene in candidate_script.structured_scenes:
            matched = matched_old_scene_by_candidate_id.get(scene.id)
            old_line_ids = [] if matched is None else matched.line_ids
            remapped: list[str] = []
            for position, candidate_line_id in enumerate(scene.line_ids):
                line = candidate_line_by_id[candidate_line_id]
                if position < len(old_line_ids) and old_line_ids[position] not in used_line_ids:
                    resolved_line_id = old_line_ids[position]
                else:
                    while True:
                        resolved_line_id = (
                            f"{run.project_id}-line-v{next_script_number}-"
                            f"{next_line_suffix:04d}"
                        )
                        next_line_suffix += 1
                        if resolved_line_id not in used_line_ids:
                            break
                used_line_ids.add(resolved_line_id)
                assigned_candidate_line_ids.add(candidate_line_id)
                line.id = resolved_line_id
                remapped.append(resolved_line_id)
            scene.line_ids = remapped
        unassigned_lines = [
            line
            for original_id, line in candidate_line_by_id.items()
            if original_id not in assigned_candidate_line_ids
        ]
        for line in unassigned_lines:
            while True:
                resolved_line_id = (
                    f"{run.project_id}-line-v{next_script_number}-"
                    f"{next_line_suffix:04d}"
                )
                next_line_suffix += 1
                if resolved_line_id not in used_line_ids:
                    break
            used_line_ids.add(resolved_line_id)
            line.id = resolved_line_id

        candidate_script.id = f"{run.project_id}-script-v{next_script_number}"
        candidate_script.version = next_script_number
        candidate_script.source_mode = current_script.source_mode
        candidate_script.original_text = current_script.original_text
        candidate_script.parent_version_id = current_script.id
        candidate_script.change_reason = run.revision_instruction
        candidate_script.planning_run_id = run.id
        candidate_script.approval_status = ApprovalStatus.APPROVED
        candidate_script.freshness = Freshness.CURRENT
        candidate_script.created_at = now

        old_scene_by_id = {scene.id: scene for scene in old_scenes}
        new_scene_by_id = {
            scene.id: scene for scene in candidate_script.structured_scenes
        }
        changed_old_scene_ids = {
            scene_id
            for scene_id, old_scene in old_scene_by_id.items()
            if scene_id not in new_scene_by_id
            or new_scene_by_id[scene_id] != old_scene
        }
        affected_shot_ids = [
            shot.id
            for shot in document.shots
            if structural_rewrite or shot.scene_id in changed_old_scene_ids
        ]
        current_script.approval_status = ApprovalStatus.SUPERSEDED
        current_script.freshness = Freshness.STALE
        document.script_versions.append(candidate_script)
        document.project.current_script_version_id = candidate_script.id
        story_duration_target = (
            None
            if candidate_script.story_intent is None
            or candidate_script.story_intent.duration_contract is None
            else candidate_script.story_intent.duration_contract.target_seconds
        )
        if story_duration_target is not None:
            # Publish the ScriptVersion and its authoritative story-duration
            # projection atomically. The in-flight PlanningRun may explore a
            # revised contract, while ProjectRecord continues to describe the
            # current ScriptVersion until the revision is accepted here.
            document.project.target_duration_seconds = story_duration_target

        current_bible_content = current_bible.model_dump(
            exclude={
                "id",
                "version",
                "approval_status",
                "freshness",
                "parent_version_id",
                "created_at",
            }
        )
        candidate_bible_content = candidate_bible.model_dump(
            exclude={
                "id",
                "version",
                "approval_status",
                "freshness",
                "parent_version_id",
                "created_at",
            }
        )
        if candidate_bible_content != current_bible_content:
            next_bible_number = max(
                version.version for version in document.bible_versions
            ) + 1
            candidate_bible.id = f"{run.project_id}-bible-v{next_bible_number}"
            candidate_bible.version = next_bible_number
            candidate_bible.parent_version_id = current_bible.id
            candidate_bible.approval_status = ApprovalStatus.APPROVED
            candidate_bible.freshness = Freshness.CURRENT
            candidate_bible.created_at = now
            current_bible.approval_status = ApprovalStatus.SUPERSEDED
            current_bible.freshness = Freshness.STALE
            document.bible_versions.append(candidate_bible)
            document.project.current_bible_version_id = candidate_bible.id

        self.repository.mark_takes_stale(run.project_id, affected_shot_ids)
        self._invalidate_current_manifests(document)
        self._invalidate_output_slot_locked(
            document,
            PRODUCTION_SPEC_OUTPUT_SLOT_ID,
            "Script changed after the ProductionSpec was authored.",
            updated_at=now,
        )
        self._invalidate_output_slot_locked(
            document,
            STORYBOARD_OUTPUT_SLOT_ID,
            "Script changed after the Storyboard was authored.",
            updated_at=now,
        )
        self._invalidate_asset_plan_locked(
            document,
            "Script changed after the AssetPlan was authored.",
            updated_at=now,
        )
        self._invalidate_generation_plan_locked(
            document,
            "Script changed after the GenerationPlan was compiled.",
            updated_at=now,
        )
        document.project.stage = (
            ProjectStage.SCRIPT_READY
            if not document.shots
            else ProjectStage.PLANNED
        )
        document.project.updated_at = now

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
        missing_canonical = missing_canonical_assets(document)
        missing = missing_reference_views(document)
        return AssetCoverageResponse(
            project_id=project_id,
            ready=asset_coverage_ready(document),
            missing_canonical_assets=missing_canonical,
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
            source_version.approval_status != ApprovalStatus.APPROVED
            or source_version.freshness != Freshness.CURRENT
            or source_version.media_type != AssetMediaType.IMAGE
            or (
                source_version.local_path is None
                and source_version.external_url is None
            )
        ):
            raise WorkflowConflictError(
                "Only a usable current approved catalog image can be imported"
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
            compatible_kinds = {
                source_asset.kind,
                *catalog_semantic_asset_kinds(
                    source_document,
                    source_version.id,
                ),
            }
            if (
                target_asset.kind != AssetKind.REFERENCE
                and target_asset.kind not in compatible_kinds
            ):
                raise WorkflowConflictError(
                    "Catalog source is not advertised for the target asset kind"
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
                    purpose=AssetReviewPurpose.CANONICAL_MASTER,
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
        if (
            version.approval_status != ApprovalStatus.APPROVED
            or version.freshness != Freshness.CURRENT
            or version.media_type != AssetMediaType.IMAGE
        ):
            raise WorkflowConflictError(
                f"AssetVersion {version.id} is not a current approved image"
            )
        if version.local_path is not None:
            path = Path(version.local_path).expanduser().resolve()
            if not path.is_file():
                raise WorkflowConflictError(
                    f"Approved image AssetVersion {version.id} is missing its local media"
                )
            source = H3MediaSource(local_path=str(path))
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
            if plan.status == H3RoutePlanStatus.STALE:
                continue
            try:
                validate_route_plan_freshness(plan, document)
            except RoutePlanningError:
                continue
            for decision in plan.decisions:
                if shot_revisions.get(decision.shot_id) != decision.input_shot_revision:
                    continue
                for preparation in decision.asset_preparations:
                    key = (decision.shot_id, preparation.role)
                    if (
                        key in claimed
                        or preparation.status != H3RouteAssetPreparationStatus.READY
                        or preparation.approved_version_id is None
                        or not self._h3_preparation_inputs_are_current(
                            document,
                            preparation,
                        )
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

    @staticmethod
    def _h3_preparation_inputs_are_current(
        document: ProjectDocument,
        preparation: H3RouteAssetPreparation,
    ) -> bool:
        """Check that every source used to author a boundary frame is still approved."""

        versions = {
            version.id: (asset, version)
            for asset in document.assets
            for version in asset.versions
        }
        for version_id in preparation.input_version_ids:
            resolved = versions.get(version_id)
            if resolved is None:
                return False
            asset, version = resolved
            if (
                version.approval_status != ApprovalStatus.APPROVED
                or version.freshness != Freshness.CURRENT
                or (
                    version.media_type != AssetMediaType.IMAGE
                    and asset.approved_version_id != version.id
                )
            ):
                return False
        return True

    @staticmethod
    def _touch_h3_route_plan(plan: H3ProductionRoutePlan) -> None:
        """Advance the exact state identity after a persisted plan-state mutation."""

        plan.state_revision += 1
        plan.updated_at = utc_now()

    def _require_h3_route_plan_state(
        self,
        project_id: str,
        plan_id: str,
        expected_state_revision: int,
    ) -> H3ProductionRoutePlan:
        """Load one exact mutable plan state or reject a stale handoff."""

        plan = self.repository.get_h3_route_plan(plan_id)
        if plan is None or plan.project_id != project_id:
            raise ArtifactNotFoundError(plan_id)
        if plan.state_revision != expected_state_revision:
            raise WorkflowConflictError(
                f"H3 route plan {plan.id} state changed: expected revision "
                f"{expected_state_revision}, current revision {plan.state_revision}"
            )
        return plan

    @staticmethod
    def _h3_route_decision(
        plan: H3ProductionRoutePlan,
        shot_id: str,
    ) -> H3ShotRoutePlan:
        matches = [item for item in plan.decisions if item.shot_id == shot_id]
        if not matches:
            raise ArtifactNotFoundError(shot_id)
        if len(matches) != 1:
            raise WorkflowConflictError(
                f"H3 route plan {plan.id} contains duplicate shot decisions for {shot_id}"
            )
        return matches[0]

    def delete_project(self, project_id: str) -> bool:
        return self.repository.delete_project(project_id)

    def get_project(self, project_id: str) -> ProjectAggregate:
        document = self._require_project_document(project_id)
        return self._aggregate(document)

    def create_production_document(
        self,
        project_id: str,
        request: ProductionDocumentCreateRequest,
    ) -> ProductionDocumentMutationResponse:
        """Create one durable, method-neutral structured production document."""

        with self._lock:
            self._reject_managed_production_document_kind(request.kind)
            project_document = self._require_project_document(project_id)
            now = utc_now()
            result = self._create_production_document_locked(
                project_document,
                kind=request.kind,
                title=request.title,
                payload=request.payload,
                source_artifact_refs=request.source_artifact_refs,
                origin=request.origin,
                created_at=now,
            )
            project_document.project.updated_at = now
            self.repository.save_project(project_document)
            return result

    def revise_production_document(
        self,
        project_id: str,
        production_document_id: str,
        request: ProductionDocumentReviseRequest,
    ) -> ProductionDocumentMutationResponse:
        """Append a version when the caller still holds the current base version."""

        with self._lock:
            project_document = self._require_project_document(project_id)
            production_document = self._production_document(
                project_document,
                production_document_id,
            )
            self._reject_managed_production_document_kind(production_document.kind)
            now = utc_now()
            result = self._revise_production_document_locked(
                project_document,
                production_document_id=production_document_id,
                base_version_id=request.base_version_id,
                payload=request.payload,
                source_artifact_refs=request.source_artifact_refs,
                origin=request.origin,
                created_at=now,
            )
            project_document.project.updated_at = now
            self.repository.save_project(project_document)
            return result

    @staticmethod
    def _reject_managed_production_document_kind(kind: str) -> None:
        normalized_kind = kind.strip()
        if normalized_kind not in _MANAGED_PRODUCTION_DOCUMENT_KINDS:
            return
        raise WorkflowConflictError(
            f"Production document kind {normalized_kind!r} is managed by its "
            "dedicated audio operation and cannot be created or revised through "
            "the generic production-document API"
        )

    def get_production_document(
        self,
        project_id: str,
        production_document_id: str,
        *,
        version_id: str | None = None,
    ) -> ProductionDocumentReadResponse:
        with self._lock:
            project_document = self._require_project_document(project_id)
            production_document = next(
                (
                    item
                    for item in project_document.production_documents
                    if item.id == production_document_id
                ),
                None,
            )
            if production_document is None:
                raise ArtifactNotFoundError(production_document_id)
            resolved_version_id = version_id or production_document.current_version_id
            version = next(
                (
                    item
                    for item in production_document.versions
                    if item.id == resolved_version_id
                ),
                None,
            )
            if version is None:
                raise ArtifactNotFoundError(resolved_version_id)
            return ProductionDocumentReadResponse(
                document=production_document,
                version=version,
            )

    def list_production_documents(
        self,
        project_id: str,
    ) -> ProductionDocumentListResponse:
        with self._lock:
            project_document = self._require_project_document(project_id)
            return ProductionDocumentListResponse(
                documents=list(project_document.production_documents)
            )

    def get_production_spec(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> ProductionSpecReadResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            production_document = self._managed_project_document(
                document,
                document.production_spec_document_id,
                PRODUCTION_SPEC_DOCUMENT_KIND,
            )
            version = self._production_document_version(
                production_document,
                version_id,
            )
            return ProductionSpecReadResponse(
                spec=self._parse_production_spec(version),
                document=production_document,
                version=version,
                output_slot=self._project_output_slot(
                    document,
                    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
                ),
            )

    def create_production_spec(
        self,
        project_id: str,
        request: ProductionSpecCreateRequest,
    ) -> ProductionSpecMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            if document.production_spec_document_id is not None:
                raise WorkflowConflictError(
                    "The project already has a ProductionSpec; replace its current version"
                )
            self._validate_production_spec(document, request.spec)
            now = utc_now()
            result = self._create_production_document_locked(
                document,
                kind=PRODUCTION_SPEC_DOCUMENT_KIND,
                title="Production Spec",
                payload=production_document_payload(request.spec),
                source_artifact_refs=self._merge_production_document_refs(
                    self._production_spec_source_refs(document, request.spec),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            document.production_spec_document_id = result.document.id
            self._sync_project_from_production_spec(document, request.spec)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=PRODUCTION_SPEC_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="production_spec",
                        artifact_id=result.document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return ProductionSpecMutationResponse(
                spec=request.spec,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def replace_production_spec(
        self,
        project_id: str,
        request: ProductionSpecReplaceRequest,
    ) -> ProductionSpecMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            production_document = self._managed_project_document(
                document,
                document.production_spec_document_id,
                PRODUCTION_SPEC_DOCUMENT_KIND,
            )
            self._validate_production_spec(document, request.spec)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=production_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(request.spec),
                source_artifact_refs=self._merge_production_document_refs(
                    self._production_spec_source_refs(document, request.spec),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            self._sync_project_from_production_spec(document, request.spec)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=PRODUCTION_SPEC_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="production_spec",
                        artifact_id=production_document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            # Storyboards pin the exact ProductionSpec version, including its
            # provenance. Every explicit replacement therefore invalidates
            # downstream artifacts, even when the typed content compares equal.
            self._invalidate_output_slot_locked(
                document,
                STORYBOARD_OUTPUT_SLOT_ID,
                "ProductionSpec was replaced; the Storyboard must confirm the new production decisions.",
                updated_at=now,
            )
            self._invalidate_asset_plan_locked(
                document,
                "ProductionSpec was replaced after the AssetPlan was authored.",
                updated_at=now,
            )
            self._invalidate_generation_plan_locked(
                document,
                "ProductionSpec was replaced after the GenerationPlan was compiled.",
                updated_at=now,
            )
            self.repository.mark_takes_stale(
                project_id,
                [shot.id for shot in document.shots],
            )
            self._invalidate_current_manifests(document)
            document.project.updated_at = now
            self.repository.save_project(document)
            return ProductionSpecMutationResponse(
                spec=request.spec,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def get_storyboard(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> StoryboardReadResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            production_document = self._managed_project_document(
                document,
                document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            version = self._production_document_version(
                production_document,
                version_id,
            )
            return StoryboardReadResponse(
                storyboard=self._parse_storyboard(version),
                document=production_document,
                version=version,
                output_slot=self._project_output_slot(
                    document,
                    STORYBOARD_OUTPUT_SLOT_ID,
                ),
            )

    def begin_storyboard_draft(
        self,
        project_id: str,
        request: StoryboardDraftBeginRequest,
    ) -> StoryboardDraftMutationResponse:
        """Persist an empty, resumable storyboard draft against current story truth.

        A draft is authoring progress rather than the canonical Storyboard.  Creating
        it therefore does not change the storyboard output slot, projected Shots, or
        any generation/timeline state.
        """

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft = self._storyboard_draft_from_seed(document, request.seed)
            self._validate_storyboard_draft(document, draft)
            now = utc_now()
            result = self._create_production_document_locked(
                document,
                kind=STORYBOARD_DRAFT_DOCUMENT_KIND,
                title="Storyboard Draft",
                payload=production_document_payload(draft),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, draft),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    draft,
                )
            )

    def append_storyboard_ledger_segment(
        self,
        project_id: str,
        request: StoryboardDraftLedgerSegmentAppendRequest,
    ) -> StoryboardDraftMutationResponse:
        """Persist the next semantic timing-ledger segment."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document, current_version, current = (
                self._storyboard_draft_mutation_state(
                    document,
                    request.draft_document_id,
                    request.base_version_id,
                    operation="ledger append",
                )
            )
            if current.phase != StoryboardDraftPhase.LEDGER_BUILDING:
                raise WorkflowConflictError(
                    "Timing-ledger segments can only be appended while the draft "
                    "is in LEDGER_BUILDING"
                )
            for entry in request.entries:
                self._validate_storyboard_source_range(
                    document,
                    current,
                    entry.source_range,
                )
            try:
                revised = StoryboardDraftContent.model_validate(
                    {
                        **current.model_dump(mode="json"),
                        "timing_ledger": [
                            *[
                                item.model_dump(mode="json")
                                for item in current.timing_ledger
                            ],
                            *[
                                item.model_dump(mode="json")
                                for item in request.entries
                            ],
                        ],
                        "timing_notes": [*current.timing_notes, *request.notes],
                    }
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                )
            )

    def complete_storyboard_ledger(
        self,
        project_id: str,
        request: StoryboardDraftLedgerCompleteRequest,
    ) -> StoryboardDraftMutationResponse:
        """Seal full source coverage and enter outline planning."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document, current_version, current = (
                self._storyboard_draft_mutation_state(
                    document,
                    request.draft_document_id,
                    request.base_version_id,
                    operation="ledger completion",
                )
            )
            if current.phase != StoryboardDraftPhase.LEDGER_BUILDING:
                raise WorkflowConflictError(
                    "The storyboard ledger can only be completed from "
                    "LEDGER_BUILDING"
                )
            self._validate_storyboard_ledger_partition_feasibility(current)
            try:
                revised = StoryboardDraftContent.model_validate(
                    {
                        **current.model_dump(mode="json"),
                        "phase": StoryboardDraftPhase.OUTLINE_BUILDING,
                        "timing_notes": [*current.timing_notes, *request.notes],
                    }
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                )
            )

    def append_storyboard_outline_segment(
        self,
        project_id: str,
        request: StoryboardDraftOutlineSegmentAppendRequest,
    ) -> StoryboardDraftMutationResponse:
        """Persist the next stable ClipGroup-outline prefix."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document, current_version, current = (
                self._storyboard_draft_mutation_state(
                    document,
                    request.draft_document_id,
                    request.base_version_id,
                    operation="outline append",
                )
            )
            if current.phase != StoryboardDraftPhase.OUTLINE_BUILDING:
                raise WorkflowConflictError(
                    "Outline segments can only be appended while the draft is in "
                    "OUTLINE_BUILDING"
                )
            for group in request.groups:
                self._validate_storyboard_source_range(
                    document,
                    current,
                    group.source_range,
                )
            try:
                revised = StoryboardDraftContent.model_validate(
                    {
                        **current.model_dump(mode="json"),
                        "planned_clip_groups": [
                            *[
                                item.model_dump(mode="json")
                                for item in current.planned_clip_groups
                            ],
                            *[
                                item.model_dump(mode="json")
                                for item in request.groups
                            ],
                        ],
                        "notes": [*current.notes, *request.notes],
                    }
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                )
            )

    def complete_storyboard_outline(
        self,
        project_id: str,
        request: StoryboardDraftOutlineCompleteRequest,
    ) -> StoryboardDraftMutationResponse:
        """Seal the full outline and enter detailed ClipGroup authoring."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document, current_version, current = (
                self._storyboard_draft_mutation_state(
                    document,
                    request.draft_document_id,
                    request.base_version_id,
                    operation="outline completion",
                )
            )
            if current.phase != StoryboardDraftPhase.OUTLINE_BUILDING:
                raise WorkflowConflictError(
                    "The storyboard outline can only be completed from "
                    "OUTLINE_BUILDING"
                )
            production_spec_document = self._managed_project_document(
                document,
                current.production_spec_document_id,
                PRODUCTION_SPEC_DOCUMENT_KIND,
            )
            production_spec_version = self._production_document_version(
                production_spec_document,
                current.production_spec_version_id,
            )
            production_spec = self._parse_production_spec(production_spec_version)
            expected_alignment, expected_difference = (
                self._storyboard_duration_alignment(
                    production_spec,
                    request.timing_audit.computed_episode_duration_s,
                )
            )
            if request.timing_audit.alignment != expected_alignment:
                raise WorkflowConflictError(
                    "Timing-audit alignment does not match the bound "
                    "ProductionSpec DurationContract"
                )
            submitted_difference = request.timing_audit.difference_seconds
            if (
                (submitted_difference is None) != (expected_difference is None)
                or (
                    submitted_difference is not None
                    and expected_difference is not None
                    and not math.isclose(
                        submitted_difference,
                        expected_difference,
                        rel_tol=0.0,
                        abs_tol=1e-6,
                    )
                )
            ):
                raise WorkflowConflictError(
                    "Timing-audit difference_seconds does not match the bound "
                    "ProductionSpec DurationContract"
                )
            reconciled_audit = request.timing_audit.model_copy(
                update={
                    "target_duration_s": (
                        production_spec.duration_contract.target_seconds
                    ),
                    "alignment": expected_alignment,
                    "difference_seconds": expected_difference,
                },
                deep=True,
            )
            next_phase = (
                StoryboardDraftPhase.DURATION_RECONCILIATION
                if expected_alignment
                in {
                    DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
                    DurationAlignment.UNRESOLVED,
                }
                else StoryboardDraftPhase.GROUP_AUTHORING
            )
            try:
                revised = StoryboardDraftContent.model_validate(
                    {
                        **current.model_dump(mode="json"),
                        "phase": next_phase,
                        "timing_audit": reconciled_audit.model_dump(mode="json"),
                        "notes": [*current.notes, *request.notes],
                    }
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                )
            )

    def append_storyboard_group(
        self,
        project_id: str,
        request: StoryboardDraftAppendRequest,
    ) -> StoryboardDraftMutationResponse:
        """Append and immediately persist one complete provider-bounded ClipGroup."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document = self._production_document(
                document,
                request.draft_document_id,
            )
            if draft_document.kind != STORYBOARD_DRAFT_DOCUMENT_KIND:
                raise WorkflowConflictError(
                    f"Production document {draft_document.id} has kind "
                    f"{draft_document.kind!r}; expected "
                    f"{STORYBOARD_DRAFT_DOCUMENT_KIND!r}"
                )
            if request.base_version_id != draft_document.current_version_id:
                raise WorkflowConflictError(
                    "Storyboard draft append is based on a stale version: "
                    f"expected {draft_document.current_version_id}, "
                    f"received {request.base_version_id}"
                )
            current_version = self._production_document_version(draft_document)
            current = self._parse_storyboard_draft(current_version)
            self._validate_storyboard_draft(document, current)
            revised = self._validated_storyboard_group_append(
                project_id,
                document,
                current,
                request,
            )
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                    latest_scene_node_id=request.group.scene_node_id,
                    latest_clip_group_id=request.group.clip_group.id,
                    latest_shot_ids=[item.id for item in request.group.shots],
                    latest_continuity_out=list(
                        request.group.clip_group.continuity_out
                    ),
                )
            )

    def append_storyboard_groups(
        self,
        project_id: str,
        request: StoryboardDraftGroupsAppendRequest,
    ) -> StoryboardDraftGroupsMutationResponse:
        """Persist a validated adjacent group segment as per-group versions."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            _draft_document, _current_version, current = (
                self._storyboard_draft_mutation_state(
                    document,
                    request.draft_document_id,
                    request.base_version_id,
                    operation="group segment append",
                )
            )

            # Validate the whole requested prefix against an in-memory draft first.
            # A malformed later group therefore cannot leave a surprising partial
            # segment, while successful persistence still creates one immutable
            # ProductionDocumentVersion per ClipGroup below.
            simulated = current
            for group in request.groups:
                simulated = self._validated_storyboard_group_append(
                    project_id,
                    document,
                    simulated,
                    StoryboardDraftAppendRequest(
                        draft_document_id=request.draft_document_id,
                        base_version_id=request.base_version_id,
                        work_unit=request.work_unit,
                        group=group,
                        source_artifact_refs=request.source_artifact_refs,
                        origin=request.origin,
                    ),
                )

            base_version_id = request.base_version_id
            checkpoints: list[StoryboardDraftGroupAppendCheckpoint] = []
            final_progress: StoryboardDraftProgress | None = None
            for group in request.groups:
                response = self.append_storyboard_group(
                    project_id,
                    StoryboardDraftAppendRequest(
                        draft_document_id=request.draft_document_id,
                        base_version_id=base_version_id,
                        work_unit=request.work_unit,
                        group=group,
                        source_artifact_refs=request.source_artifact_refs,
                        origin=request.origin,
                    ),
                )
                final_progress = response.progress
                base_version_id = final_progress.draft_version_id
                checkpoints.append(
                    StoryboardDraftGroupAppendCheckpoint(
                        draft_ref=final_progress.draft_ref,
                        draft_version_id=final_progress.draft_version_id,
                        scene_node_id=group.scene_node_id,
                        clip_group_id=group.clip_group.id,
                        shot_ids=[shot.id for shot in group.shots],
                        continuity_out=list(group.clip_group.continuity_out),
                    )
                )
            if final_progress is None:  # guarded by the request model
                raise WorkflowConflictError(
                    "Storyboard group segment must contain at least one ClipGroup"
                )
            return StoryboardDraftGroupsMutationResponse(
                progress=final_progress,
                checkpoints=checkpoints,
            )

    def replace_storyboard_draft_group(
        self,
        project_id: str,
        request: StoryboardDraftGroupReplaceRequest,
    ) -> StoryboardDraftMutationResponse:
        """Replace one committed planned group while retaining its ledger slot."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document = self._production_document(
                document,
                request.draft_document_id,
            )
            if draft_document.kind != STORYBOARD_DRAFT_DOCUMENT_KIND:
                raise WorkflowConflictError(
                    f"Production document {draft_document.id} has kind "
                    f"{draft_document.kind!r}; expected "
                    f"{STORYBOARD_DRAFT_DOCUMENT_KIND!r}"
                )
            if request.base_version_id != draft_document.current_version_id:
                raise WorkflowConflictError(
                    "Storyboard draft replacement is based on a stale version: "
                    f"expected {draft_document.current_version_id}, "
                    f"received {request.base_version_id}"
                )
            current_version = self._production_document_version(draft_document)
            current = self._parse_storyboard_draft(current_version)
            self._validate_storyboard_draft(document, current)
            if current.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
                raise WorkflowConflictError(
                    "Storyboard verification cannot be recorded while the draft "
                    "requires duration reconciliation"
                )
            if current.phase != StoryboardDraftPhase.GROUP_AUTHORING:
                raise WorkflowConflictError(
                    "Storyboard groups can only be replaced during GROUP_AUTHORING"
                )
            self._validate_storyboard_work_unit(document, current, request.work_unit)
            self._validate_storyboard_source_range(
                document,
                current,
                request.group.source_range,
            )
            replaced_indexes = [
                index
                for index, item in enumerate(current.committed_groups)
                if item.clip_group.id == request.replaced_clip_group_id
            ]
            if not replaced_indexes:
                raise WorkflowConflictError(
                    "Storyboard draft replacement requires an already committed "
                    f"ClipGroup {request.replaced_clip_group_id}"
                )
            planned = next(
                (
                    item
                    for item in current.planned_clip_groups
                    if item.id == request.replaced_clip_group_id
                ),
                None,
            )
            if planned is None:
                raise WorkflowConflictError(
                    "Storyboard draft replacement has no sealed planned ClipGroup "
                    f"{request.replaced_clip_group_id}"
                )
            canonical_group = self._canonical_storyboard_group_for_planned_slot(
                request.group,
                planned,
            )
            if any(shot.project_id != project_id for shot in request.group.shots):
                raise WorkflowConflictError(
                    "Storyboard draft replacement Shots must belong to the "
                    "current project"
                )
            revised_groups = [
                item.model_copy(deep=True) for item in current.committed_groups
            ]
            revised_groups[replaced_indexes[0]] = canonical_group
            try:
                revised = StoryboardDraftContent.model_validate(
                    {
                        **current.model_dump(mode="json"),
                        "committed_groups": [
                            item.model_dump(mode="json") for item in revised_groups
                        ],
                        "verification": None,
                    }
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                    latest_scene_node_id=request.group.scene_node_id,
                    latest_clip_group_id=request.group.clip_group.id,
                    latest_shot_ids=[item.id for item in request.group.shots],
                    latest_continuity_out=list(
                        request.group.clip_group.continuity_out
                    ),
                )
            )

    def record_storyboard_draft_verification(
        self,
        project_id: str,
        request: StoryboardDraftVerificationRequest,
    ) -> StoryboardDraftMutationResponse:
        """Attach a semantic Verifier report to the current exact draft."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document = self._production_document(
                document,
                request.draft_document_id,
            )
            if draft_document.kind != STORYBOARD_DRAFT_DOCUMENT_KIND:
                raise WorkflowConflictError(
                    f"Production document {draft_document.id} has kind "
                    f"{draft_document.kind!r}; expected "
                    f"{STORYBOARD_DRAFT_DOCUMENT_KIND!r}"
                )
            if request.base_version_id != draft_document.current_version_id:
                raise WorkflowConflictError(
                    "Storyboard draft verification is based on a stale version: "
                    f"expected {draft_document.current_version_id}, "
                    f"received {request.base_version_id}"
                )
            current_version = self._production_document_version(draft_document)
            current = self._parse_storyboard_draft(current_version)
            self._validate_storyboard_draft(document, current)
            if current.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
                raise WorkflowConflictError(
                    "Storyboard verification cannot be recorded while the draft "
                    "requires duration reconciliation"
                )
            if current.phase != StoryboardDraftPhase.GROUP_AUTHORING:
                raise WorkflowConflictError(
                    "Storyboard verification requires a completed ledger and outline"
                )
            node_by_id = {item.id: item for item in current.scene_nodes}
            ordered_commits = sorted(
                current.committed_groups,
                key=lambda item: (
                    node_by_id[item.scene_node_id].sequence,
                    item.scene_group_sequence,
                ),
            )
            committed_ids = [item.clip_group.id for item in ordered_commits]
            if request.report.checked_clip_group_ids != committed_ids:
                raise WorkflowConflictError(
                    "Storyboard verification must check every currently committed "
                    "ClipGroup in planned order"
                )
            if (
                request.report.status == StoryboardDraftVerificationStatus.READY
                and len(committed_ids) != len(current.planned_clip_groups)
            ):
                raise WorkflowConflictError(
                    "A READY storyboard verification requires every planned "
                    "ClipGroup to be committed"
                )
            if (
                request.report.status == StoryboardDraftVerificationStatus.READY
                and current.timing_audit is not None
                and current.timing_audit.alignment
                == DurationAlignment.OUTSIDE_ACCEPTED_RANGE
            ):
                raise WorkflowConflictError(
                    "A storyboard outside its accepted duration range cannot be READY"
                )
            committed_id_set = set(committed_ids)
            unknown_finding_ids = [
                group_id
                for finding in request.report.findings
                for group_id in finding.affected_clip_group_ids
                if group_id not in committed_id_set
            ]
            if unknown_finding_ids:
                raise WorkflowConflictError(
                    "Storyboard verification findings reference uncommitted "
                    "ClipGroups: "
                    + ", ".join(unknown_finding_ids)
                )
            revised = current.model_copy(
                update={"verification": request.report.model_copy(deep=True)},
                deep=True,
            )
            self._validate_storyboard_draft(document, revised)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=draft_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(revised),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_draft_source_refs(document, revised),
                    [
                        *current_version.source_artifact_refs,
                        *request.source_artifact_refs,
                    ],
                ),
                origin=request.origin,
                created_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardDraftMutationResponse(
                progress=self._storyboard_draft_progress(
                    result.document,
                    result.version,
                    revised,
                )
            )

    def finalize_storyboard(
        self,
        project_id: str,
        request: StoryboardDraftFinalizeRequest,
    ) -> StoryboardMutationResponse:
        """Deterministically publish one exact draft as the canonical Storyboard."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            draft_document = self._production_document(
                document,
                request.draft_document_id,
            )
            if draft_document.kind != STORYBOARD_DRAFT_DOCUMENT_KIND:
                raise WorkflowConflictError(
                    f"Production document {draft_document.id} has kind "
                    f"{draft_document.kind!r}; expected "
                    f"{STORYBOARD_DRAFT_DOCUMENT_KIND!r}"
                )
            if request.draft_version_id != draft_document.current_version_id:
                raise WorkflowConflictError(
                    "Storyboard finalization requires the current exact draft "
                    f"version {draft_document.current_version_id}; received "
                    f"{request.draft_version_id}"
                )
            draft_version = self._production_document_version(
                draft_document,
                request.draft_version_id,
            )
            draft = self._parse_storyboard_draft(draft_version)
            self._validate_storyboard_draft(document, draft)
            if draft.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
                raise WorkflowConflictError(
                    "Storyboard finalization is unavailable while the draft requires "
                    "duration reconciliation"
                )

            storyboard_document: ProductionDocument | None = None
            current_storyboard_version: ProductionDocumentVersion | None = None
            previous: StoryboardContent | None = None
            if document.storyboard_document_id is None:
                if draft.base_storyboard_version_id is not None:
                    raise WorkflowConflictError(
                        "A first Storyboard draft cannot declare a base Storyboard version"
                    )
            else:
                storyboard_document = self._managed_project_document(
                    document,
                    document.storyboard_document_id,
                    STORYBOARD_DOCUMENT_KIND,
                )
                current_storyboard_version = self._production_document_version(
                    storyboard_document
                )
                if (
                    draft.base_storyboard_version_id
                    != current_storyboard_version.id
                ):
                    raise WorkflowConflictError(
                        "Storyboard draft is based on a stale canonical Storyboard: "
                        f"expected {current_storyboard_version.id}, received "
                        f"{draft.base_storyboard_version_id}"
                    )
                previous = self._parse_storyboard(current_storyboard_version)

            try:
                assembled = assemble_storyboard_draft(draft)
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            storyboard = self._normalize_storyboard_revisions(
                assembled,
                previous=previous,
            )
            self._validate_storyboard(document, storyboard)
            changed_shot_ids = (
                [item.id for item in storyboard.shots]
                if previous is None
                else self._changed_storyboard_shot_ids(previous, storyboard)
            )
            now = utc_now()
            draft_source_ref = ProductionDocumentArtifactRef(
                project_id=project_id,
                artifact_type="production_document_version",
                artifact_id=draft_version.id,
                version=draft_version.version,
            )
            source_refs = self._merge_production_document_refs(
                [
                    *self._storyboard_source_refs(document, storyboard),
                    draft_source_ref,
                ],
                request.source_artifact_refs,
            )
            if storyboard_document is None:
                result = self._create_production_document_locked(
                    document,
                    kind=STORYBOARD_DOCUMENT_KIND,
                    title="Storyboard",
                    payload=production_document_payload(storyboard),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
                document.storyboard_document_id = result.document.id
            else:
                assert current_storyboard_version is not None
                assert draft.base_storyboard_version_id is not None
                result = self._revise_production_document_locked(
                    document,
                    production_document_id=storyboard_document.id,
                    base_version_id=draft.base_storyboard_version_id,
                    payload=production_document_payload(storyboard),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
            self._sync_storyboard_projection(document, storyboard)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=STORYBOARD_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="storyboard",
                        artifact_id=result.document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            self._after_storyboard_revision(
                document,
                changed_shot_ids,
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardMutationResponse(
                storyboard=storyboard,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def create_storyboard(
        self,
        project_id: str,
        request: StoryboardCreateRequest,
    ) -> StoryboardMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            if document.storyboard_document_id is not None:
                raise WorkflowConflictError(
                    "The project already has a Storyboard; replace or patch its current version"
                )
            storyboard = self._normalize_storyboard_revisions(
                request.storyboard,
                previous=None,
            )
            self._validate_storyboard(document, storyboard)
            now = utc_now()
            result = self._create_production_document_locked(
                document,
                kind=STORYBOARD_DOCUMENT_KIND,
                title="Storyboard",
                payload=production_document_payload(storyboard),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_source_refs(document, storyboard),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            document.storyboard_document_id = result.document.id
            self._sync_storyboard_projection(document, storyboard)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=STORYBOARD_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="storyboard",
                        artifact_id=result.document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            self._invalidate_asset_plan_locked(
                document,
                "A Storyboard was created after the previous AssetPlan context.",
                updated_at=now,
            )
            self._invalidate_generation_plan_locked(
                document,
                "A Storyboard was created after the previous GenerationPlan context.",
                updated_at=now,
            )
            document.project.stage = ProjectStage.PLANNED
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardMutationResponse(
                storyboard=storyboard,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def replace_storyboard(
        self,
        project_id: str,
        request: StoryboardReplaceRequest,
    ) -> StoryboardMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            production_document = self._managed_project_document(
                document,
                document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            current_version = self._production_document_version(production_document)
            current = self._parse_storyboard(current_version)
            storyboard = self._normalize_storyboard_revisions(
                request.storyboard,
                previous=current,
            )
            self._validate_storyboard(document, storyboard)
            changed_shot_ids = self._changed_storyboard_shot_ids(current, storyboard)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=production_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(storyboard),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_source_refs(document, storyboard),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            self._sync_storyboard_projection(document, storyboard)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=STORYBOARD_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="storyboard",
                        artifact_id=production_document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            self._after_storyboard_revision(
                document,
                changed_shot_ids,
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardMutationResponse(
                storyboard=storyboard,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def patch_storyboard(
        self,
        project_id: str,
        request: StoryboardPatchRequest,
    ) -> StoryboardMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            production_document = self._managed_project_document(
                document,
                document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            current_version = self._production_document_version(production_document)
            if request.base_version_id != current_version.id:
                raise WorkflowConflictError(
                    "Storyboard patch is based on a stale ProductionDocument version"
                )
            current = self._parse_storyboard(current_version)
            try:
                storyboard = apply_storyboard_patch(current, request)
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            self._validate_storyboard(document, storyboard)
            changed_shot_ids = self._changed_storyboard_shot_ids(current, storyboard)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=production_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(storyboard),
                source_artifact_refs=self._merge_production_document_refs(
                    self._storyboard_source_refs(document, storyboard),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            self._sync_storyboard_projection(document, storyboard)
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=STORYBOARD_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="storyboard",
                        artifact_id=production_document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            self._after_storyboard_revision(
                document,
                changed_shot_ids,
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return StoryboardMutationResponse(
                storyboard=storyboard,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def get_asset_plan(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> AssetPlanReadResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            production_document = self._managed_project_document(
                document,
                document.asset_plan_document_id,
                ASSET_PLAN_DOCUMENT_KIND,
            )
            version = self._production_document_version(
                production_document,
                version_id,
            )
            return AssetPlanReadResponse(
                asset_plan=self._parse_asset_plan(version),
                document=production_document,
                version=version,
                output_slot=self._project_output_slot(
                    document,
                    ASSET_PLAN_OUTPUT_SLOT_ID,
                ),
            )

    def create_asset_plan(
        self,
        project_id: str,
        request: AssetPlanCreateRequest,
    ) -> AssetPlanMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            if document.asset_plan_document_id is not None:
                raise WorkflowConflictError(
                    "The project already has an AssetPlan; replace its current version"
                )
            self._validate_asset_plan(document, request.asset_plan)
            now = utc_now()
            result = self._create_production_document_locked(
                document,
                kind=ASSET_PLAN_DOCUMENT_KIND,
                title="Asset Plan",
                payload=production_document_payload(request.asset_plan),
                source_artifact_refs=self._merge_production_document_refs(
                    self._asset_plan_source_refs(document, request.asset_plan),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            document.asset_plan_document_id = result.document.id
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=ASSET_PLAN_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="asset_plan",
                        artifact_id=result.document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return AssetPlanMutationResponse(
                asset_plan=request.asset_plan,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def replace_asset_plan(
        self,
        project_id: str,
        request: AssetPlanReplaceRequest,
    ) -> AssetPlanMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            production_document = self._managed_project_document(
                document,
                document.asset_plan_document_id,
                ASSET_PLAN_DOCUMENT_KIND,
            )
            self._validate_asset_plan(document, request.asset_plan)
            now = utc_now()
            result = self._revise_production_document_locked(
                document,
                production_document_id=production_document.id,
                base_version_id=request.base_version_id,
                payload=production_document_payload(request.asset_plan),
                source_artifact_refs=self._merge_production_document_refs(
                    self._asset_plan_source_refs(document, request.asset_plan),
                    request.source_artifact_refs,
                ),
                origin=request.origin,
                created_at=now,
            )
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=ASSET_PLAN_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="asset_plan",
                        artifact_id=production_document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return AssetPlanMutationResponse(
                asset_plan=request.asset_plan,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def get_generation_plan(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> GenerationPlanReadResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            production_document = self._managed_project_document(
                document,
                document.generation_plan_document_id,
                GENERATION_PLAN_DOCUMENT_KIND,
            )
            version = self._production_document_version(
                production_document,
                version_id,
            )
            return GenerationPlanReadResponse(
                generation_plan=self._parse_generation_plan(version),
                document=production_document,
                version=version,
                output_slot=self._project_output_slot(
                    document,
                    GENERATION_PLAN_OUTPUT_SLOT_ID,
                ),
            )

    def compile_generation_plan(
        self,
        project_id: str,
        request: GenerationPlanCompileRequest,
    ) -> GenerationPlanMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            storyboard_document = self._managed_project_document(
                document,
                request.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            production_spec_document = self._managed_project_document(
                document,
                request.production_spec_document_id,
                PRODUCTION_SPEC_DOCUMENT_KIND,
            )
            if storyboard_document.id != document.storyboard_document_id:
                raise WorkflowConflictError("GenerationPlan must use the project Storyboard")
            if production_spec_document.id != document.production_spec_document_id:
                raise WorkflowConflictError("GenerationPlan must use the project ProductionSpec")
            storyboard_version = self._production_document_version(
                storyboard_document,
                request.storyboard_version_id,
            )
            spec_version = self._production_document_version(
                production_spec_document,
                request.production_spec_version_id,
            )
            if storyboard_version.id != storyboard_document.current_version_id:
                raise WorkflowConflictError("GenerationPlan requires the current Storyboard version")
            if spec_version.id != production_spec_document.current_version_id:
                raise WorkflowConflictError("GenerationPlan requires the current ProductionSpec version")
            storyboard = self._parse_storyboard(storyboard_version)
            spec = self._parse_production_spec(spec_version)
            # Output-slot invalidation is a useful projection, but the versioned
            # documents remain addressable.  Revalidate both exact inputs at the
            # consumption boundary so a stale audio-led spec/storyboard cannot be
            # compiled merely because its document/version is still marked current.
            self._validate_production_spec(document, spec)
            self._validate_storyboard(document, storyboard)
            try:
                generation_plan = compile_generation_plan(
                    storyboard=storyboard,
                    storyboard_document_id=storyboard_document.id,
                    storyboard_version_id=storyboard_version.id,
                    production_spec=spec,
                    production_spec_document_id=production_spec_document.id,
                    production_spec_version_id=spec_version.id,
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            now = utc_now()
            source_refs = self._merge_production_document_refs(
                [
                    ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="production_document_version",
                        artifact_id=storyboard_version.id,
                        version=storyboard_version.version,
                    ),
                    ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="production_document_version",
                        artifact_id=spec_version.id,
                        version=spec_version.version,
                    ),
                ],
                request.source_artifact_refs,
            )
            if document.generation_plan_document_id is None:
                if request.base_version_id is not None:
                    raise WorkflowConflictError(
                        "A first GenerationPlan compile cannot declare base_version_id"
                    )
                result = self._create_production_document_locked(
                    document,
                    kind=GENERATION_PLAN_DOCUMENT_KIND,
                    title="Video Generation Plan",
                    payload=production_document_payload(generation_plan),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
                document.generation_plan_document_id = result.document.id
            else:
                generation_document = self._managed_project_document(
                    document,
                    document.generation_plan_document_id,
                    GENERATION_PLAN_DOCUMENT_KIND,
                )
                if request.base_version_id is None:
                    raise WorkflowConflictError(
                        "Recompiling an existing GenerationPlan requires base_version_id"
                    )
                result = self._revise_production_document_locked(
                    document,
                    production_document_id=generation_document.id,
                    base_version_id=request.base_version_id,
                    payload=production_document_payload(generation_plan),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
            slot = self._bind_output_slot_locked(
                document,
                OutputSlotBindRequest(
                    slot_id=GENERATION_PLAN_OUTPUT_SLOT_ID,
                    artifact_ref=ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="generation_plan",
                        artifact_id=result.document.id,
                        version=result.version.version,
                    ),
                ),
                updated_at=now,
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return GenerationPlanMutationResponse(
                generation_plan=generation_plan,
                document=result.document,
                version=result.version,
                output_slot=slot,
            )

    def list_output_slots(self, project_id: str) -> OutputSlotListResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            return OutputSlotListResponse(output_slots=list(document.output_slots))

    def get_output_slot(
        self,
        project_id: str,
        slot_id: str,
    ) -> OutputSlotReadResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            slot = self._project_output_slot(document, slot_id)
            if slot is None:
                raise ArtifactNotFoundError(slot_id)
            return OutputSlotReadResponse(output_slot=slot)

    def bind_output_slot(
        self,
        project_id: str,
        request: OutputSlotBindRequest,
    ) -> OutputSlotMutationResponse:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            if request.artifact_ref.project_id != project_id:
                raise WorkflowConflictError("Output slot artifact belongs to another project")
            ref = ArtifactRef.model_validate(request.artifact_ref.model_dump(mode="json"))
            if ProjectArtifactResolver(self.repository).summarize_artifact(ref) is None:
                raise ArtifactNotFoundError(request.artifact_ref.artifact_id)
            now = utc_now()
            try:
                slot = self._bind_output_slot_locked(
                    document,
                    request,
                    updated_at=now,
                )
            except ValueError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            document.project.updated_at = now
            self.repository.save_project(document)
            return OutputSlotMutationResponse(output_slot=slot)

    @staticmethod
    def _production_document(
        project_document: ProjectDocument,
        production_document_id: str,
    ) -> ProductionDocument:
        production_document = next(
            (
                item
                for item in project_document.production_documents
                if item.id == production_document_id
            ),
            None,
        )
        if production_document is None:
            raise ArtifactNotFoundError(production_document_id)
        return production_document

    @staticmethod
    def _production_document_version(
        production_document: ProductionDocument,
        version_id: str | None = None,
    ) -> ProductionDocumentVersion:
        resolved_version_id = version_id or production_document.current_version_id
        version = next(
            (
                item
                for item in production_document.versions
                if item.id == resolved_version_id
            ),
            None,
        )
        if version is None:
            raise ArtifactNotFoundError(resolved_version_id)
        return version

    def _managed_project_document(
        self,
        project_document: ProjectDocument,
        production_document_id: str | None,
        expected_kind: str,
    ) -> ProductionDocument:
        if production_document_id is None:
            raise ArtifactNotFoundError(expected_kind)
        production_document = self._production_document(
            project_document,
            production_document_id,
        )
        if production_document.kind != expected_kind:
            raise WorkflowConflictError(
                f"Production document {production_document.id} has kind "
                f"{production_document.kind!r}; expected {expected_kind!r}"
            )
        return production_document

    @staticmethod
    def _parse_production_spec(
        version: ProductionDocumentVersion,
    ) -> ProductionSpecContent:
        try:
            return ProductionSpecContent.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                f"ProductionSpec version {version.id} is structurally invalid: {exc}"
            ) from exc

    @staticmethod
    def _parse_storyboard(
        version: ProductionDocumentVersion,
    ) -> StoryboardContent:
        try:
            return StoryboardContent.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                f"Storyboard version {version.id} is structurally invalid: {exc}"
            ) from exc

    @staticmethod
    def _parse_storyboard_draft(
        version: ProductionDocumentVersion,
    ) -> StoryboardDraftContent:
        try:
            return StoryboardDraftContent.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                f"Storyboard draft version {version.id} is structurally invalid: {exc}"
            ) from exc

    @staticmethod
    def _parse_asset_plan(
        version: ProductionDocumentVersion,
    ) -> AssetPlanContent:
        try:
            return AssetPlanContent.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                f"AssetPlan version {version.id} is structurally invalid: {exc}"
            ) from exc

    @staticmethod
    def _parse_generation_plan(
        version: ProductionDocumentVersion,
    ) -> GenerationPlanContent:
        try:
            return GenerationPlanContent.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                f"GenerationPlan version {version.id} is structurally invalid: {exc}"
            ) from exc

    @staticmethod
    def _project_output_slot(
        project_document: ProjectDocument,
        slot_id: str,
    ) -> ProjectOutputSlot | None:
        return next(
            (item for item in project_document.output_slots if item.id == slot_id),
            None,
        )

    @staticmethod
    def _replace_project_output_slot(
        project_document: ProjectDocument,
        output_slot: ProjectOutputSlot,
    ) -> None:
        for index, current in enumerate(project_document.output_slots):
            if current.id == output_slot.id:
                project_document.output_slots[index] = output_slot
                return
        project_document.output_slots.append(output_slot)

    def _bind_output_slot_locked(
        self,
        project_document: ProjectDocument,
        request: OutputSlotBindRequest,
        *,
        updated_at: str,
    ) -> ProjectOutputSlot:
        current = self._project_output_slot(project_document, request.slot_id)
        output_slot = bind_project_output_slot(
            project_id=project_document.project.id,
            current=current,
            request=request,
            updated_at=updated_at,
        )
        self._replace_project_output_slot(project_document, output_slot)
        return output_slot

    def _invalidate_output_slot_locked(
        self,
        project_document: ProjectDocument,
        slot_id: str,
        reason: str,
        *,
        updated_at: str,
    ) -> ProjectOutputSlot | None:
        current = self._project_output_slot(project_document, slot_id)
        if current is None or current.current_artifact_ref is None:
            return current
        output_slot = invalidate_project_output_slot(
            project_id=project_document.project.id,
            current=current,
            request=OutputSlotInvalidateRequest(
                slot_id=slot_id,
                reason=reason,
                expected_revision=current.revision,
            ),
            updated_at=updated_at,
        )
        self._replace_project_output_slot(project_document, output_slot)
        return output_slot

    def _invalidate_take_output_slot_if_current_locked(
        self,
        project_document: ProjectDocument,
        take: Take,
        reason: str,
        *,
        updated_at: str,
    ) -> None:
        if take.output_slot_id is None:
            return
        output_slot = self._project_output_slot(
            project_document,
            take.output_slot_id,
        )
        current_ref = (
            None if output_slot is None else output_slot.current_artifact_ref
        )
        if (
            current_ref is None
            or current_ref.artifact_type != "take"
            or current_ref.artifact_id != take.id
        ):
            return
        self._invalidate_output_slot_locked(
            project_document,
            take.output_slot_id,
            reason,
            updated_at=updated_at,
        )

    def _invalidate_generation_plan_locked(
        self,
        project_document: ProjectDocument,
        reason: str,
        *,
        updated_at: str,
    ) -> None:
        self._invalidate_output_slot_locked(
            project_document,
            GENERATION_PLAN_OUTPUT_SLOT_ID,
            reason,
            updated_at=updated_at,
        )

    def _invalidate_asset_plan_locked(
        self,
        project_document: ProjectDocument,
        reason: str,
        *,
        updated_at: str,
    ) -> None:
        self._invalidate_output_slot_locked(
            project_document,
            ASSET_PLAN_OUTPUT_SLOT_ID,
            reason,
            updated_at=updated_at,
        )

    def _invalidate_asset_plan_if_completion_became_unusable_locked(
        self,
        project_document: ProjectDocument,
        *,
        reason: str,
        updated_at: str,
    ) -> bool:
        """Clear only the live AssetPlan binding when its completion truth breaks."""

        slot = self._project_output_slot(
            project_document,
            ASSET_PLAN_OUTPUT_SLOT_ID,
        )
        current_ref = None if slot is None else slot.current_artifact_ref
        if (
            current_ref is None
            or current_ref.artifact_type != "asset_plan"
            or current_ref.version is None
        ):
            return False
        production_document = self._managed_project_document(
            project_document,
            current_ref.artifact_id,
            ASSET_PLAN_DOCUMENT_KIND,
        )
        version = next(
            (
                item
                for item in production_document.versions
                if item.version == current_ref.version
            ),
            None,
        )
        if version is None:
            self._invalidate_asset_plan_locked(
                project_document,
                reason,
                updated_at=updated_at,
            )
            return True
        try:
            self._validate_asset_plan(
                project_document,
                self._parse_asset_plan(version),
            )
        except WorkflowConflictError:
            self._invalidate_asset_plan_locked(
                project_document,
                reason,
                updated_at=updated_at,
            )
            return True
        return False

    def _invalidate_audio_led_production_after_master_audio_change_locked(
        self,
        project_document: ProjectDocument,
        *,
        master_audio_version_id: str,
        updated_at: str,
    ) -> None:
        """Invalidate only production truth whose timing authority was replaced."""

        if project_document.production_spec_document_id is None:
            return
        production_document = self._managed_project_document(
            project_document,
            project_document.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_version = self._production_document_version(production_document)
        production_spec = self._parse_production_spec(production_version)
        if production_spec.duration_contract.authority != DurationAuthority.AUDIO_LED:
            return
        if production_spec.master_audio_binding_version_id == master_audio_version_id:
            return
        self._invalidate_output_slot_locked(
            project_document,
            PRODUCTION_SPEC_OUTPUT_SLOT_ID,
            "Master Audio changed; the audio-led ProductionSpec must bind the new exact version.",
            updated_at=updated_at,
        )
        self._invalidate_output_slot_locked(
            project_document,
            STORYBOARD_OUTPUT_SLOT_ID,
            "Master Audio changed after the audio-led Storyboard was authored.",
            updated_at=updated_at,
        )
        self._invalidate_asset_plan_locked(
            project_document,
            "Master Audio changed after the audio-led AssetPlan was authored.",
            updated_at=updated_at,
        )
        self._invalidate_generation_plan_locked(
            project_document,
            "Master Audio changed after the audio-led GenerationPlan was compiled.",
            updated_at=updated_at,
        )
        self.repository.mark_takes_stale(
            project_document.project.id,
            [shot.id for shot in project_document.shots],
        )

    def _validate_production_spec(
        self,
        project_document: ProjectDocument,
        production_spec: ProductionSpecContent,
    ) -> None:
        if production_spec.project_id != project_document.project.id:
            raise WorkflowConflictError(
                "ProductionSpec must belong to the current project"
            )
        if (
            production_spec.script_version_id
            != project_document.project.current_script_version_id
        ):
            raise WorkflowConflictError(
                "ProductionSpec must reference the current ScriptVersion"
            )
        if (
            production_spec.bible_version_id
            != project_document.project.current_bible_version_id
        ):
            raise WorkflowConflictError(
                "ProductionSpec must reference the current ProjectBibleVersion"
            )
        current_script = self._current_script(project_document)
        story_duration_contract = (
            None
            if current_script.story_intent is None
            else current_script.story_intent.duration_contract
        )
        if (
            story_duration_contract is not None
            and production_spec.duration_contract != story_duration_contract
        ):
            raise WorkflowConflictError(
                "ProductionSpec DurationContract must match the current "
                "ScriptVersion StoryIntent DurationContract"
            )
        if production_spec.duration_contract.authority == DurationAuthority.AUDIO_LED:
            _master_document, master_version, binding = (
                self._current_master_audio_binding(project_document)
            )
            if production_spec.master_audio_binding_version_id != master_version.id:
                raise WorkflowConflictError(
                    "Audio-led ProductionSpec must reference the current exact "
                    "Master Audio binding version"
                )
            expected_duration = binding.time_origin_seconds + binding.duration_seconds
            target_duration = production_spec.duration_contract.target_seconds
            if target_duration is None or not math.isclose(
                target_duration,
                expected_duration,
                rel_tol=0.0,
                abs_tol=1e-6,
            ):
                raise WorkflowConflictError(
                    "Audio-led ProductionSpec duration must match the bound Master "
                    "Audio timeline duration"
                )

    @staticmethod
    def _storyboard_duration_alignment(
        production_spec: ProductionSpecContent,
        natural_duration_seconds: float,
    ) -> tuple[DurationAlignment, float | None]:
        """Compare the natural storyboard runtime with its pinned duration contract."""

        contract = production_spec.duration_contract
        difference = (
            None
            if contract.target_seconds is None
            else natural_duration_seconds - contract.target_seconds
        )
        if contract.authority != DurationAuthority.TARGET_LED:
            return DurationAlignment.NOT_APPLICABLE, difference
        if contract.target_seconds is None:
            return DurationAlignment.UNRESOLVED, None
        accepted = contract.acceptable_range_seconds
        if accepted is None:
            return DurationAlignment.UNRESOLVED, difference
        aligned = (
            accepted.minimum_seconds
            <= natural_duration_seconds
            <= accepted.maximum_seconds
        )
        return (
            DurationAlignment.ALIGNED
            if aligned
            else DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
            difference,
        )

    def _current_story_source_refs(
        self,
        project_document: ProjectDocument,
    ) -> list[ProductionDocumentArtifactRef]:
        script = self._current_script(project_document)
        bible = next(
            (
                item
                for item in project_document.bible_versions
                if item.id == project_document.project.current_bible_version_id
            ),
            None,
        )
        if bible is None:
            raise WorkflowConflictError(
                "Project current ProjectBibleVersion is missing"
            )
        return [
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="script_version",
                artifact_id=script.id,
                version=script.version,
            ),
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="bible_version",
                artifact_id=bible.id,
                version=bible.version,
            ),
        ]

    def _production_spec_source_refs(
        self,
        project_document: ProjectDocument,
        production_spec: ProductionSpecContent,
    ) -> list[ProductionDocumentArtifactRef]:
        refs = self._current_story_source_refs(project_document)
        if production_spec.duration_contract.authority != DurationAuthority.AUDIO_LED:
            return refs
        master_document, master_version, _binding = (
            self._current_master_audio_binding(project_document)
        )
        return self._merge_production_document_refs(
            refs,
            self._master_audio_source_refs(
                project_document.project.id,
                master_document,
                master_version,
            ),
        )

    def _validate_script_revision_evidence_refs(
        self,
        project_document: ProjectDocument,
        evidence_refs: list[ProductionDocumentArtifactRef],
        *,
        revision_origin: ScriptRevisionOrigin,
        base_script_version_id: str,
    ) -> list[ProductionDocumentArtifactRef]:
        """Resolve exact revision evidence before it becomes planning provenance."""

        resolved: list[ProductionDocumentArtifactRef] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        script_refs: list[ScriptVersion] = []
        production_spec_versions: list[
            tuple[
                ProductionDocument,
                ProductionDocumentVersion,
                ProductionSpecContent,
            ]
        ] = []
        storyboard_draft_versions: list[
            tuple[ProductionDocument, ProductionDocumentVersion, StoryboardDraftContent]
        ] = []
        for supplied in evidence_refs:
            ref = supplied.model_copy(deep=True)
            if ref.project_id != project_document.project.id:
                raise WorkflowConflictError(
                    "Script revision evidence must belong to the current project"
                )
            if ref.version is None:
                raise WorkflowConflictError(
                    "Script revision evidence must pin an exact artifact version"
                )
            key = (
                ref.project_id,
                ref.artifact_type,
                ref.artifact_id,
                ref.version,
            )
            if key in seen:
                continue
            seen.add(key)
            if ref.artifact_type in {"script", "script_version"}:
                script = next(
                    (
                        item
                        for item in project_document.script_versions
                        if item.id == ref.artifact_id
                        and item.version == ref.version
                    ),
                    None,
                )
                if script is None:
                    raise WorkflowConflictError(
                        "Script revision evidence references an unknown exact ScriptVersion"
                    )
                script_refs.append(script)
            elif ref.artifact_type in {
                "production_spec",
                "storyboard_draft",
                "production_document",
                "production_document_version",
            }:
                production_document = next(
                    (
                        item
                        for item in project_document.production_documents
                        if (
                            ref.artifact_type == "production_document_version"
                            and any(
                                version.id == ref.artifact_id
                                for version in item.versions
                            )
                        )
                        or (
                            ref.artifact_type != "production_document_version"
                            and item.id == ref.artifact_id
                        )
                    ),
                    None,
                )
                if production_document is None:
                    raise WorkflowConflictError(
                        "Script revision evidence references an unknown production document"
                    )
                version = next(
                    (
                        item
                        for item in production_document.versions
                        if item.version == ref.version
                        and (
                            ref.artifact_type != "production_document_version"
                            or item.id == ref.artifact_id
                        )
                    ),
                    None,
                )
                if version is None:
                    raise WorkflowConflictError(
                        "Script revision evidence references an unknown exact document version"
                )
                if production_document.kind == PRODUCTION_SPEC_DOCUMENT_KIND:
                    production_spec_versions.append(
                        (
                            production_document,
                            version,
                            self._parse_production_spec(version),
                        )
                    )
                elif production_document.kind == STORYBOARD_DRAFT_DOCUMENT_KIND:
                    storyboard_draft_versions.append(
                        (
                            production_document,
                            version,
                            self._parse_storyboard_draft(version),
                        )
                    )
                else:
                    raise WorkflowConflictError(
                        "Script revision evidence may only use ScriptVersion, "
                        "ProductionSpec, or StoryboardDraft artifacts"
                    )
            else:
                raise WorkflowConflictError(
                    "Script revision evidence contains an unsupported artifact type: "
                    f"{ref.artifact_type}"
                )
            resolved.append(ref)

        if revision_origin == ScriptRevisionOrigin.USER_FEEDBACK:
            return resolved
        if not resolved:
            raise WorkflowConflictError(
                "A production-constraint script revision requires exact evidence refs"
            )
        if not any(item.id == base_script_version_id for item in script_refs):
            raise WorkflowConflictError(
                "Production-constraint evidence must include the base ScriptVersion"
            )
        matching_pair = any(
            draft.script_version_id == base_script_version_id
            and draft.production_spec_document_id == spec_document.id
            and draft.production_spec_version_id == spec_version.id
            and draft.phase == StoryboardDraftPhase.DURATION_RECONCILIATION
            and draft.timing_audit is not None
            and draft.timing_audit.alignment
            in {
                DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
                DurationAlignment.UNRESOLVED,
            }
            and spec.duration_contract.authority == DurationAuthority.TARGET_LED
            for _draft_document, _draft_version, draft in storyboard_draft_versions
            for spec_document, spec_version, spec in production_spec_versions
        )
        if not matching_pair:
            raise WorkflowConflictError(
                "Production-constraint evidence must include a matching exact "
                "target-led ProductionSpec and unresolved or out-of-range "
                "StoryboardDraft reconciliation bound to the base ScriptVersion"
            )
        return resolved

    @staticmethod
    def _merge_production_document_refs(
        required: list[ProductionDocumentArtifactRef],
        supplied: list[ProductionDocumentArtifactRef],
    ) -> list[ProductionDocumentArtifactRef]:
        merged: dict[
            tuple[str, str, str, int | None],
            ProductionDocumentArtifactRef,
        ] = {}
        for item in [*required, *supplied]:
            key = (
                item.project_id,
                item.artifact_type,
                item.artifact_id,
                item.version,
            )
            merged.setdefault(key, item.model_copy(deep=True))
        return list(merged.values())

    @staticmethod
    def _sync_project_from_production_spec(
        project_document: ProjectDocument,
        production_spec: ProductionSpecContent,
    ) -> None:
        project_document.project.language = production_spec.language
        project_document.project.aspect_ratio = production_spec.aspect_ratio
        if production_spec.duration_contract.target_seconds is not None:
            project_document.project.target_duration_seconds = (
                production_spec.duration_contract.target_seconds
            )

    def _storyboard_draft_source_refs(
        self,
        project_document: ProjectDocument,
        draft: StoryboardDraftContent,
    ) -> list[ProductionDocumentArtifactRef]:
        production_spec_document = self._managed_project_document(
            project_document,
            draft.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            draft.production_spec_version_id,
        )
        return [
            *self._current_story_source_refs(project_document),
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="production_spec",
                artifact_id=production_spec_document.id,
                version=production_spec_version.version,
            ),
        ]

    def _storyboard_draft_mutation_state(
        self,
        project_document: ProjectDocument,
        draft_document_id: str,
        base_version_id: str,
        *,
        operation: str,
    ) -> tuple[
        ProductionDocument,
        ProductionDocumentVersion,
        StoryboardDraftContent,
    ]:
        draft_document = self._production_document(
            project_document,
            draft_document_id,
        )
        if draft_document.kind != STORYBOARD_DRAFT_DOCUMENT_KIND:
            raise WorkflowConflictError(
                f"Production document {draft_document.id} has kind "
                f"{draft_document.kind!r}; expected "
                f"{STORYBOARD_DRAFT_DOCUMENT_KIND!r}"
            )
        if base_version_id != draft_document.current_version_id:
            raise WorkflowConflictError(
                f"Storyboard draft {operation} is based on a stale version: "
                f"expected {draft_document.current_version_id}, "
                f"received {base_version_id}"
            )
        current_version = self._production_document_version(draft_document)
        current = self._parse_storyboard_draft(current_version)
        self._validate_storyboard_draft(project_document, current)
        return draft_document, current_version, current

    def _validated_storyboard_group_append(
        self,
        project_id: str,
        project_document: ProjectDocument,
        current: StoryboardDraftContent,
        request: StoryboardDraftAppendRequest,
    ) -> StoryboardDraftContent:
        """Validate one next planned group and return the simulated new draft."""

        if current.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
            raise WorkflowConflictError(
                "Detailed ClipGroups cannot be appended while the draft requires "
                "duration reconciliation"
            )
        if current.phase != StoryboardDraftPhase.GROUP_AUTHORING:
            raise WorkflowConflictError(
                "Detailed ClipGroups can only be authored after the ledger and "
                "outline are complete"
            )
        scene_order = {item.id: item.sequence for item in current.scene_nodes}
        ordered_plans = sorted(
            current.planned_clip_groups,
            key=lambda item: (
                scene_order[item.scene_node_id],
                item.scene_group_sequence,
            ),
        )
        if len(current.committed_groups) >= len(ordered_plans):
            raise WorkflowConflictError(
                "Storyboard draft already contains every planned ClipGroup"
            )
        next_planned = ordered_plans[len(current.committed_groups)]
        if request.group.clip_group.id != next_planned.id:
            raise WorkflowConflictError(
                "Single-user storyboard authoring must append the next planned "
                f"ClipGroup {next_planned.id}; received "
                f"{request.group.clip_group.id}"
            )
        canonical_group = self._canonical_storyboard_group_for_planned_slot(
            request.group,
            next_planned,
        )
        self._validate_storyboard_work_unit(
            project_document,
            current,
            request.work_unit,
        )
        self._validate_storyboard_source_range(
            project_document,
            current,
            request.group.source_range,
        )
        if any(shot.project_id != project_id for shot in request.group.shots):
            raise WorkflowConflictError(
                "Storyboard draft group Shots must belong to the current project"
            )
        try:
            revised = StoryboardDraftContent.model_validate(
                {
                    **current.model_dump(mode="json"),
                    "committed_groups": [
                        *[
                            item.model_dump(mode="json")
                            for item in current.committed_groups
                        ],
                        canonical_group.model_dump(mode="json"),
                    ],
                    "verification": None,
                }
            )
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self._validate_storyboard_draft(project_document, revised)
        return revised

    @staticmethod
    def _canonical_storyboard_group_for_planned_slot(
        group: StoryboardClipGroupDraft,
        planned: StoryboardClipGroupOutline,
    ) -> StoryboardClipGroupDraft:
        """Match stable slot identity, then restore sealed explanatory prose."""

        if (
            group.clip_group.id != planned.id
            or group.scene_node_id != planned.scene_node_id
            or group.scene_group_sequence != planned.scene_group_sequence
            or group.source_range.id != planned.source_range.id
            or group.source_range.scene_ids != planned.source_range.scene_ids
            or group.source_range.script_line_ids
            != planned.source_range.script_line_ids
            or group.clip_group.duration_seconds != planned.duration_seconds
        ):
            raise WorkflowConflictError(
                "Storyboard draft group must match its planned ClipGroup slot's "
                "stable identity, source span, and duration"
            )
        return group.model_copy(
            update={
                "source_range": planned.source_range.model_copy(deep=True),
            },
            deep=True,
        )

    @staticmethod
    def _storyboard_draft_progress(
        production_document: ProductionDocument,
        version: ProductionDocumentVersion,
        draft: StoryboardDraftContent,
        *,
        latest_scene_node_id: str | None = None,
        latest_clip_group_id: str | None = None,
        latest_shot_ids: list[str] | None = None,
        latest_continuity_out: list[str] | None = None,
    ) -> StoryboardDraftProgress:
        ledger_line_ids = [
            line_id
            for item in draft.timing_ledger
            for line_id in item.source_range.script_line_ids
        ]
        outlined_timing_entry_ids = [
            entry_id
            for item in draft.planned_clip_groups
            for entry_id in item.timing_entry_ids
        ]
        outlined_line_ids = [
            line_id
            for item in draft.planned_clip_groups
            for line_id in item.source_range.script_line_ids
        ]
        committed_duration = sum(
            item.clip_group.duration_seconds for item in draft.committed_groups
        )
        covered_line_ids = {
            line_id
            for item in draft.committed_groups
            for line_id in item.source_range.script_line_ids
        }
        plans_by_node = {
            node.id: [
                item
                for item in draft.planned_clip_groups
                if item.scene_node_id == node.id
            ]
            for node in draft.scene_nodes
        }
        commits_by_node = {
            node.id: [
                item
                for item in draft.committed_groups
                if item.scene_node_id == node.id
            ]
            for node in draft.scene_nodes
        }
        scene_progress: list[StoryboardDraftSceneProgress] = []
        for node in draft.scene_nodes:
            planned = plans_by_node[node.id]
            committed = commits_by_node[node.id]
            planned_duration = sum(item.duration_seconds for item in planned)
            scene_committed_duration = sum(
                item.clip_group.duration_seconds for item in committed
            )
            planned_line_ids = {
                line_id
                for item in planned
                for line_id in item.source_range.script_line_ids
            }
            scene_covered_line_ids = {
                line_id
                for item in committed
                for line_id in item.source_range.script_line_ids
            }
            scene_progress.append(
                StoryboardDraftSceneProgress(
                    scene_node_id=node.id,
                    scene_id=node.scene_id,
                    planned_clip_group_count=len(planned),
                    committed_clip_group_count=len(committed),
                    planned_duration_seconds=planned_duration,
                    committed_duration_seconds=scene_committed_duration,
                    remaining_duration_seconds=max(
                        0.0,
                        planned_duration - scene_committed_duration,
                    ),
                    planned_script_line_count=len(planned_line_ids),
                    covered_script_line_count=len(scene_covered_line_ids),
                    remaining_script_line_count=len(
                        planned_line_ids - scene_covered_line_ids
                    ),
                )
            )
        next_scene_node_id: str | None = None
        next_script_line_id: str | None = None
        if draft.phase == StoryboardDraftPhase.LEDGER_BUILDING:
            ledger_entries_by_node = {
                node.id: [
                    entry
                    for entry in draft.timing_ledger
                    if entry.scene_node_id == node.id
                ]
                for node in draft.scene_nodes
            }
            for node in draft.scene_nodes:
                node_entries = ledger_entries_by_node[node.id]
                if node_entries and node_entries[-1].completes_scene:
                    continue
                next_scene_node_id = node.id
                covered_node_line_ids = {
                    line_id
                    for entry in node_entries
                    for line_id in entry.source_range.script_line_ids
                }
                next_script_line_id = next(
                    (
                        line_id
                        for line_id in node.source_line_ids
                        if line_id not in covered_node_line_ids
                    ),
                    None,
                )
                break
        elif draft.phase == StoryboardDraftPhase.OUTLINE_BUILDING:
            if len(outlined_timing_entry_ids) < len(draft.timing_ledger):
                next_entry = draft.timing_ledger[len(outlined_timing_entry_ids)]
                next_scene_node_id = next_entry.scene_node_id
                next_script_line_id = next(
                    iter(next_entry.source_range.script_line_ids),
                    None,
                )
        total_planned_duration = (
            draft.timing_audit.computed_episode_duration_s
            if draft.timing_audit is not None
            else None
        )
        latest_planned_continuity = (
            list(draft.planned_clip_groups[-1].continuity_out)
            if draft.planned_clip_groups
            else (
                list(draft.timing_ledger[-1].continuity_out)
                if draft.timing_ledger
                else []
            )
        )
        return StoryboardDraftProgress(
            draft_ref=ProductionDocumentArtifactRef(
                project_id=production_document.project_id,
                artifact_type="storyboard_draft",
                artifact_id=production_document.id,
                version=version.version,
            ),
            draft_version_id=version.id,
            phase=draft.phase,
            scene_node_count=len(draft.scene_nodes),
            timing_ledger_entry_count=len(draft.timing_ledger),
            ledger_covered_script_line_count=len(ledger_line_ids),
            outlined_timing_entry_count=len(outlined_timing_entry_ids),
            outlined_script_line_count=len(outlined_line_ids),
            planned_clip_group_count=len(draft.planned_clip_groups),
            committed_clip_group_count=len(draft.committed_groups),
            committed_shot_count=sum(
                len(item.shots) for item in draft.committed_groups
            ),
            total_planned_duration_seconds=total_planned_duration,
            committed_duration_seconds=committed_duration,
            remaining_duration_seconds=(
                None
                if total_planned_duration is None
                else max(0.0, total_planned_duration - committed_duration)
            ),
            covered_script_line_count=len(covered_line_ids),
            remaining_script_line_count=len(
                set(draft.script_line_ids) - covered_line_ids
            ),
            scene_progress=scene_progress,
            next_unplanned_scene_node_id=next_scene_node_id,
            next_unplanned_script_line_id=next_script_line_id,
            latest_planned_continuity_out=latest_planned_continuity,
            latest_scene_node_id=latest_scene_node_id,
            latest_clip_group_id=latest_clip_group_id,
            latest_shot_ids=list(latest_shot_ids or []),
            latest_continuity_out=list(latest_continuity_out or []),
            verification_status=(
                draft.verification.status
                if draft.verification is not None
                else None
            ),
        )

    def _validate_storyboard_source_range(
        self,
        project_document: ProjectDocument,
        draft: StoryboardDraftContent,
        source_range: StoryboardSourceRange,
    ) -> None:
        script = self._current_script(project_document)
        scene_order = {
            scene.id: index for index, scene in enumerate(script.structured_scenes)
        }
        unknown_scene_ids = [
            scene_id
            for scene_id in source_range.scene_ids
            if scene_id not in scene_order
        ]
        if unknown_scene_ids:
            raise WorkflowConflictError(
                "Storyboard source range references unknown script scenes: "
                + ", ".join(unknown_scene_ids)
            )
        scene_positions = [scene_order[scene_id] for scene_id in source_range.scene_ids]
        if scene_positions != sorted(scene_positions):
            raise WorkflowConflictError(
                "Storyboard source-range scenes must preserve script order"
            )

        line_order = {line.id: index for index, line in enumerate(script.lines)}
        unknown_line_ids = [
            line_id
            for line_id in source_range.script_line_ids
            if line_id not in line_order
        ]
        if unknown_line_ids:
            raise WorkflowConflictError(
                "Storyboard source range references unknown script lines: "
                + ", ".join(unknown_line_ids)
            )
        line_positions = [line_order[line_id] for line_id in source_range.script_line_ids]
        if line_positions != sorted(line_positions):
            raise WorkflowConflictError(
                "Storyboard source-range lines must preserve script order"
            )
        lines_in_selected_scenes = {
            line_id
            for scene in script.structured_scenes
            if scene.id in source_range.scene_ids
            for line_id in scene.line_ids
        }
        outside_scene_line_ids = [
            line_id
            for line_id in source_range.script_line_ids
            if line_id not in lines_in_selected_scenes
        ]
        if outside_scene_line_ids:
            raise WorkflowConflictError(
                "Storyboard source-range lines must belong to its selected scenes: "
                + ", ".join(outside_scene_line_ids)
            )
        if any(
            line_id not in draft.script_line_ids
            for line_id in source_range.script_line_ids
        ):
            raise WorkflowConflictError(
                "Storyboard source range must stay inside the draft ScriptVersion"
            )

    def _validate_storyboard_work_unit(
        self,
        project_document: ProjectDocument,
        draft: StoryboardDraftContent,
        work_unit: StoryboardWorkUnitContract,
    ) -> None:
        self._validate_storyboard_source_range(
            project_document,
            draft,
            work_unit.source_range,
        )

    @staticmethod
    def _validate_storyboard_ledger_partition_feasibility(
        draft: StoryboardDraftContent,
    ) -> None:
        """Ensure every scene can become complete H3-sized ClipGroups.

        This check intentionally lives at the ledger-to-outline transition rather
        than in the persisted draft model.  Ledger-building drafts may be partial,
        and older drafts must remain readable while an Agent repairs their timing.
        """

        minimum_duration = Decimal("4")
        maximum_duration = Decimal("15")
        for node in draft.scene_nodes:
            entries = [
                entry
                for entry in draft.timing_ledger
                if entry.scene_node_id == node.id
            ]
            reachable_boundaries = [False] * (len(entries) + 1)
            reachable_boundaries[0] = True
            for start in range(len(entries)):
                if not reachable_boundaries[start]:
                    continue
                duration = Decimal("0")
                for end in range(start, len(entries)):
                    duration += Decimal(
                        str(entries[end].allocated_duration_seconds)
                    )
                    if duration > maximum_duration:
                        break
                    if (
                        duration >= minimum_duration
                        and duration == duration.to_integral_value()
                    ):
                        reachable_boundaries[end + 1] = True
            if not reachable_boundaries[-1]:
                allocations = ", ".join(
                    str(Decimal(str(entry.allocated_duration_seconds)))
                    for entry in entries
                )
                raise WorkflowConflictError(
                    "Storyboard timing ledger scene "
                    f"{node.id} cannot be partitioned contiguously and completely "
                    "without crossing scenes into ClipGroups with exact integer "
                    "durations from 4 to 15 seconds; revise its timing allocations "
                    f"before completing the ledger (allocations: [{allocations}])"
                )

    def _validate_storyboard_draft(
        self,
        project_document: ProjectDocument,
        draft: StoryboardDraftContent,
    ) -> None:
        if draft.project_id != project_document.project.id:
            raise WorkflowConflictError(
                "Storyboard draft must belong to the current project"
            )
        if draft.script_version_id != project_document.project.current_script_version_id:
            raise WorkflowConflictError(
                "Storyboard draft must reference the current ScriptVersion"
            )
        if draft.bible_version_id != project_document.project.current_bible_version_id:
            raise WorkflowConflictError(
                "Storyboard draft must reference the current ProjectBibleVersion"
            )
        production_spec_document = self._managed_project_document(
            project_document,
            draft.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            draft.production_spec_version_id,
        )
        if production_spec_document.id != project_document.production_spec_document_id:
            raise WorkflowConflictError(
                "Storyboard draft must reference the project's ProductionSpec"
            )
        if production_spec_version.id != production_spec_document.current_version_id:
            raise WorkflowConflictError(
                "Storyboard draft must reference the current ProductionSpec version"
            )
        production_spec = self._parse_production_spec(production_spec_version)
        self._validate_production_spec(project_document, production_spec)
        if (
            draft.script_version_id != production_spec.script_version_id
            or draft.bible_version_id != production_spec.bible_version_id
        ):
            raise WorkflowConflictError(
                "Storyboard draft and ProductionSpec must reference the same "
                "ScriptVersion and ProjectBibleVersion"
            )

        script = self._current_script(project_document)
        source_line_ids = [line.id for line in script.lines]
        if draft.script_line_ids != source_line_ids:
            raise WorkflowConflictError(
                "Storyboard draft must preserve every current script line in source order"
            )
        scenes = script.structured_scenes
        if len(draft.scene_nodes) != len(scenes):
            raise WorkflowConflictError(
                "Storyboard draft scene nodes must project every script scene exactly"
            )
        for expected_sequence, (node, scene) in enumerate(
            zip(draft.scene_nodes, scenes, strict=True),
            start=1,
        ):
            if (
                node.sequence != expected_sequence
                or node.scene_id != scene.id
                or node.heading != scene.heading
                or node.summary != scene.summary
                or node.source_line_ids != scene.line_ids
            ):
                raise WorkflowConflictError(
                    "Storyboard draft scene nodes must preserve each current script "
                    f"scene's order, identity, heading, summary, and line IDs; "
                    f"mismatch at scene {scene.id}"
                )

        if project_document.storyboard_document_id is None:
            if draft.base_storyboard_version_id is not None:
                raise WorkflowConflictError(
                    "Storyboard draft cannot declare a base version before a "
                    "canonical Storyboard exists"
                )
        else:
            storyboard_document = self._managed_project_document(
                project_document,
                project_document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            current_storyboard_version = self._production_document_version(
                storyboard_document
            )
            if draft.base_storyboard_version_id != current_storyboard_version.id:
                raise WorkflowConflictError(
                    "Storyboard draft must reference the current canonical "
                    f"Storyboard version {current_storyboard_version.id}"
                )

        for timing_entry in draft.timing_ledger:
            self._validate_storyboard_source_range(
                project_document,
                draft,
                timing_entry.source_range,
            )
        for planned in draft.planned_clip_groups:
            self._validate_storyboard_source_range(
                project_document,
                draft,
                planned.source_range,
            )
        for committed in draft.committed_groups:
            self._validate_storyboard_source_range(
                project_document,
                draft,
                committed.source_range,
            )
            if any(
                shot.project_id != project_document.project.id
                for shot in committed.shots
            ):
                raise WorkflowConflictError(
                    "Storyboard draft group Shots must belong to the current project"
                )

    def _storyboard_draft_from_seed(
        self,
        project_document: ProjectDocument,
        seed: StoryboardDraftSeed,
    ) -> StoryboardDraftContent:
        """Hydrate source-owned scene metadata into a small Agent seed."""

        script = self._current_script(project_document)
        if len(seed.scene_nodes) != len(script.structured_scenes):
            raise WorkflowConflictError(
                "Storyboard draft seed must identify every current script scene"
            )
        if [node.scene_id for node in seed.scene_nodes] != [
            scene.id for scene in script.structured_scenes
        ]:
            raise WorkflowConflictError(
                "Storyboard draft seed scenes must preserve current script order"
            )
        scene_nodes = [
            StoryboardSceneNode(
                id=node.id,
                scene_id=node.scene_id,
                sequence=node.sequence,
                heading=scene.heading,
                summary=scene.summary,
                source_target_duration_seconds=scene.target_duration_seconds,
                source_line_ids=list(scene.line_ids),
                continuity_in=list(node.continuity_in),
                continuity_out=list(node.continuity_out),
                notes=list(node.notes),
            )
            for node, scene in zip(
                seed.scene_nodes,
                script.structured_scenes,
                strict=True,
            )
        ]
        return StoryboardDraftContent(
            phase=StoryboardDraftPhase.LEDGER_BUILDING,
            project_id=seed.project_id,
            title=seed.title,
            script_version_id=seed.script_version_id,
            bible_version_id=seed.bible_version_id,
            production_spec_document_id=seed.production_spec_document_id,
            production_spec_version_id=seed.production_spec_version_id,
            base_storyboard_version_id=seed.base_storyboard_version_id,
            script_line_ids=[line.id for line in script.lines],
            scene_nodes=scene_nodes,
            timing_notes=list(seed.timing_notes),
            notes=list(seed.notes),
        )

    def _storyboard_source_refs(
        self,
        project_document: ProjectDocument,
        storyboard: StoryboardContent,
    ) -> list[ProductionDocumentArtifactRef]:
        production_spec_document = self._managed_project_document(
            project_document,
            storyboard.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            storyboard.production_spec_version_id,
        )
        return [
            *self._current_story_source_refs(project_document),
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="production_spec",
                artifact_id=production_spec_document.id,
                version=production_spec_version.version,
            ),
        ]

    def _validate_storyboard(
        self,
        project_document: ProjectDocument,
        storyboard: StoryboardContent,
    ) -> None:
        if storyboard.project_id != project_document.project.id:
            raise WorkflowConflictError(
                "Storyboard must belong to the current project"
            )
        if storyboard.script_version_id != project_document.project.current_script_version_id:
            raise WorkflowConflictError(
                "Storyboard must reference the current ScriptVersion"
            )
        if storyboard.bible_version_id != project_document.project.current_bible_version_id:
            raise WorkflowConflictError(
                "Storyboard must reference the current ProjectBibleVersion"
            )
        production_spec_document = self._managed_project_document(
            project_document,
            storyboard.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            storyboard.production_spec_version_id,
        )
        if production_spec_document.id != project_document.production_spec_document_id:
            raise WorkflowConflictError(
                "Storyboard must reference the project's ProductionSpec"
            )
        if production_spec_version.id != production_spec_document.current_version_id:
            raise WorkflowConflictError(
                "Storyboard must reference the current ProductionSpec version"
            )
        production_spec = self._parse_production_spec(production_spec_version)
        self._validate_production_spec(project_document, production_spec)
        if (
            storyboard.script_version_id != production_spec.script_version_id
            or storyboard.bible_version_id != production_spec.bible_version_id
        ):
            raise WorkflowConflictError(
                "Storyboard and ProductionSpec must reference the same "
                "ScriptVersion and ProjectBibleVersion"
            )

        script = self._current_script(project_document)
        source_line_ids = [line.id for line in script.lines]
        if storyboard.script_line_ids != source_line_ids:
            raise WorkflowConflictError(
                "Storyboard must preserve every current script line in source order"
            )
        scene_ids = {scene.id for scene in script.structured_scenes}
        unknown_scene_ids = sorted(
            {
                shot.scene_id
                for shot in storyboard.shots
                if shot.scene_id is not None and shot.scene_id not in scene_ids
            }
        )
        if unknown_scene_ids:
            raise WorkflowConflictError(
                "Storyboard references unknown script scenes: "
                + ", ".join(unknown_scene_ids)
            )
        asset_ids = {asset.id for asset in project_document.assets}
        unknown_asset_ids = sorted(
            {
                cue.subject_asset_id
                for shot in storyboard.shots
                for beat in shot.timeline_beats
                for cue in beat.references
                if cue.subject_asset_id not in asset_ids
            }
        )
        if unknown_asset_ids:
            raise WorkflowConflictError(
                "Storyboard references unknown core assets: "
                + ", ".join(unknown_asset_ids)
            )

    def _asset_plan_source_refs(
        self,
        project_document: ProjectDocument,
        asset_plan: AssetPlanContent,
    ) -> list[ProductionDocumentArtifactRef]:
        production_spec_document = self._managed_project_document(
            project_document,
            asset_plan.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            asset_plan.production_spec_version_id,
        )
        storyboard_document = self._managed_project_document(
            project_document,
            asset_plan.storyboard_document_id,
            STORYBOARD_DOCUMENT_KIND,
        )
        storyboard_version = self._production_document_version(
            storyboard_document,
            asset_plan.storyboard_version_id,
        )
        return [
            *self._current_story_source_refs(project_document),
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="production_spec",
                artifact_id=production_spec_document.id,
                version=production_spec_version.version,
            ),
            ProductionDocumentArtifactRef(
                project_id=project_document.project.id,
                artifact_type="storyboard",
                artifact_id=storyboard_document.id,
                version=storyboard_version.version,
            ),
        ]

    def _validate_asset_plan(
        self,
        project_document: ProjectDocument,
        asset_plan: AssetPlanContent,
    ) -> None:
        if asset_plan.project_id != project_document.project.id:
            raise WorkflowConflictError("AssetPlan must belong to the current project")
        if asset_plan.script_version_id != project_document.project.current_script_version_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the current ScriptVersion"
            )
        if asset_plan.bible_version_id != project_document.project.current_bible_version_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the current ProjectBibleVersion"
            )

        production_spec_document = self._managed_project_document(
            project_document,
            asset_plan.production_spec_document_id,
            PRODUCTION_SPEC_DOCUMENT_KIND,
        )
        production_spec_version = self._production_document_version(
            production_spec_document,
            asset_plan.production_spec_version_id,
        )
        if production_spec_document.id != project_document.production_spec_document_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the project's ProductionSpec"
            )
        if production_spec_version.id != production_spec_document.current_version_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the current ProductionSpec version"
            )
        production_spec = self._parse_production_spec(production_spec_version)
        self._validate_production_spec(project_document, production_spec)

        storyboard_document = self._managed_project_document(
            project_document,
            asset_plan.storyboard_document_id,
            STORYBOARD_DOCUMENT_KIND,
        )
        storyboard_version = self._production_document_version(
            storyboard_document,
            asset_plan.storyboard_version_id,
        )
        if storyboard_document.id != project_document.storyboard_document_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the project's Storyboard"
            )
        if storyboard_version.id != storyboard_document.current_version_id:
            raise WorkflowConflictError(
                "AssetPlan must reference the current Storyboard version"
            )
        storyboard = self._parse_storyboard(storyboard_version)
        self._validate_storyboard(project_document, storyboard)

        if (
            asset_plan.script_version_id != production_spec.script_version_id
            or asset_plan.bible_version_id != production_spec.bible_version_id
            or asset_plan.script_version_id != storyboard.script_version_id
            or asset_plan.bible_version_id != storyboard.bible_version_id
            or storyboard.production_spec_document_id != production_spec_document.id
            or storyboard.production_spec_version_id != production_spec_version.id
        ):
            raise WorkflowConflictError(
                "AssetPlan, Storyboard, and ProductionSpec must reference the same "
                "ScriptVersion and ProjectBibleVersion"
            )

        known_shot_ids = {item.id for item in storyboard.shots}
        known_clip_group_ids = {item.id for item in storyboard.clip_groups}
        unknown_shot_ids = sorted(
            {
                shot_id
                for item in asset_plan.items
                for shot_id in item.serves_shot_ids
                if shot_id not in known_shot_ids
            }
        )
        if unknown_shot_ids:
            raise WorkflowConflictError(
                "AssetPlan references unknown Storyboard shots: "
                + ", ".join(unknown_shot_ids)
            )
        unknown_clip_group_ids = sorted(
            {
                clip_group_id
                for item in asset_plan.items
                for clip_group_id in item.serves_clip_group_ids
                if clip_group_id not in known_clip_group_ids
            }
        )
        if unknown_clip_group_ids:
            raise WorkflowConflictError(
                "AssetPlan references unknown Storyboard ClipGroups: "
                + ", ".join(unknown_clip_group_ids)
            )
        clip_group_id_by_shot_id = {
            shot_id: clip_group.id
            for clip_group in storyboard.clip_groups
            for shot_id in clip_group.shot_ids
        }
        for item in asset_plan.items:
            missing_clip_group_ids = sorted(
                {
                    clip_group_id_by_shot_id[shot_id]
                    for shot_id in item.serves_shot_ids
                }
                - set(item.serves_clip_group_ids)
            )
            if missing_clip_group_ids:
                raise WorkflowConflictError(
                    f"AssetPlan item {item.id} omits ClipGroups for its served Shots: "
                    + ", ".join(missing_clip_group_ids)
                )

        bible = next(
            item
            for item in project_document.bible_versions
            if item.id == asset_plan.bible_version_id
        )
        bible_entity_ids: dict[AssetKind, set[str]] = {
            AssetKind.CHARACTER: {item.id for item in bible.characters},
            AssetKind.LOCATION: {item.id for item in bible.locations},
            AssetKind.PROP: {item.id for item in bible.props},
            AssetKind.REFERENCE: set(),
        }
        assets_by_id = {item.id: item for item in project_document.assets}

        def validate_asset_version(
            asset_id: str | None,
            asset_version_id: str | None,
            *,
            expected_kind: AssetKind | None,
            require_completed_media: bool = False,
            require_usable_image: bool = False,
            source_project_id: str | None = None,
        ) -> tuple[Asset, AssetVersion] | None:
            if asset_id is None or asset_version_id is None:
                return None
            source_document = project_document
            if source_project_id not in {None, project_document.project.id}:
                assert source_project_id is not None
                external_document = self.repository.get_project(source_project_id)
                if external_document is None:
                    raise WorkflowConflictError(
                        f"AssetPlan references unknown source project {source_project_id}"
                    )
                source_document = external_document
            source_assets_by_id = (
                assets_by_id
                if source_document is project_document
                else {item.id: item for item in source_document.assets}
            )
            asset = source_assets_by_id.get(asset_id)
            if asset is None:
                raise WorkflowConflictError(
                    f"AssetPlan references unknown Asset {asset_id} in project "
                    f"{source_document.project.id}"
                )
            if expected_kind is not None and asset.kind != expected_kind:
                raise WorkflowConflictError(
                    f"AssetPlan item kind {expected_kind.value} does not match Asset "
                    f"{asset_id} kind {asset.kind.value}"
                )
            version = next(
                (item for item in asset.versions if item.id == asset_version_id),
                None,
            )
            if version is None:
                raise WorkflowConflictError(
                    f"AssetPlan references unknown AssetVersion {asset_version_id} "
                    f"for Asset {asset_id}"
                )
            if require_completed_media:
                if (
                    version.approval_status != ApprovalStatus.APPROVED
                    or version.freshness != Freshness.CURRENT
                ):
                    raise WorkflowConflictError(
                        f"Completed AssetPlan version {version.id} must be current and approved"
                    )
                if version.local_path is None and version.external_url is None:
                    raise WorkflowConflictError(
                        f"Completed AssetPlan version {version.id} has no usable media"
                    )
                if (
                    asset.kind
                    in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
                    and version.media_type != AssetMediaType.IMAGE
                ):
                    raise WorkflowConflictError(
                        f"Completed visual AssetPlan version {version.id} must be an image"
                    )
            if require_usable_image and (
                version.media_type != AssetMediaType.IMAGE
                or (version.local_path is None and version.external_url is None)
            ):
                raise WorkflowConflictError(
                    f"AssetPlan image source {version.id} must have usable image media"
                )
            return asset, version

        work_items_by_id = {
            work_item.id: work_item
            for item in asset_plan.items
            for work_item in item.work_items
        }
        items_by_id = {item.id: item for item in asset_plan.items}

        for item in asset_plan.items:
            if item.disposition == AssetPlanDisposition.TEXT_ONLY and (
                item.work_items or item.completed_asset_id is not None
            ):
                raise WorkflowConflictError(
                    f"Text-only AssetPlan item {item.id} cannot contain media work or completion"
                )
            if (
                item.disposition == AssetPlanDisposition.REUSE_EXISTING
                and item.completed_asset_id is None
            ):
                raise WorkflowConflictError(
                    f"Reuse-existing AssetPlan item {item.id} requires an exact "
                    "completed asset version"
                )
            if (
                item.bible_entity_id is not None
                and item.asset_kind != AssetKind.REFERENCE
                and item.bible_entity_id not in bible_entity_ids[item.asset_kind]
            ):
                raise WorkflowConflictError(
                    f"AssetPlan item {item.id} references unknown "
                    f"{item.asset_kind.value} Bible entity {item.bible_entity_id}"
                )
            item_completed = validate_asset_version(
                item.completed_asset_id,
                item.completed_asset_version_id,
                expected_kind=item.asset_kind,
                require_completed_media=True,
            )
            if (
                item.disposition == AssetPlanDisposition.ANCHOR
                and item_completed is not None
                and not any(
                    work_item.completed_asset_id == item.completed_asset_id
                    and work_item.completed_asset_version_id
                    == item.completed_asset_version_id
                    for work_item in item.work_items
                )
            ):
                raise WorkflowConflictError(
                    f"Completed anchor item {item.id} must match one completed work item"
                )
            for work_item in item.work_items:
                if work_item.operation == AssetPlanOperation.IMPORT:
                    if work_item.import_intent is None or work_item.compose_intent is not None:
                        raise WorkflowConflictError(
                            f"IMPORT AssetPlan work item {work_item.id} lacks an exact import payload"
                        )
                    imported_source = validate_asset_version(
                        work_item.import_intent.source_asset_id,
                        work_item.import_intent.source_asset_version_id,
                        expected_kind=None,
                        require_completed_media=True,
                        require_usable_image=True,
                        source_project_id=work_item.import_intent.source_project_id,
                    )
                    assert imported_source is not None
                    source_document = self.repository.get_project(
                        work_item.import_intent.source_project_id
                    )
                    assert source_document is not None
                    compatible_kinds = {
                        imported_source[0].kind,
                        *catalog_semantic_asset_kinds(
                            source_document,
                            imported_source[1].id,
                        ),
                    }
                    if item.asset_kind not in compatible_kinds:
                        raise WorkflowConflictError(
                            f"IMPORT AssetPlan work item {work_item.id} source is not "
                            f"advertised for {item.asset_kind.value}"
                        )
                elif work_item.operation == AssetPlanOperation.COMPOSE:
                    if work_item.compose_intent is None or work_item.import_intent is not None:
                        raise WorkflowConflictError(
                            f"COMPOSE AssetPlan work item {work_item.id} lacks a compose payload"
                        )
                elif (
                    work_item.import_intent is not None
                    or work_item.compose_intent is not None
                ):
                    raise WorkflowConflictError(
                        f"AssetPlan work item {work_item.id} has a payload for another operation"
                    )
                completed = validate_asset_version(
                    work_item.completed_asset_id,
                    work_item.completed_asset_version_id,
                    expected_kind=item.asset_kind,
                    require_completed_media=True,
                )
                if (
                    completed is not None
                    and work_item.review_purpose
                    == AssetReviewPurpose.CANONICAL_MASTER
                    and completed[0].approved_version_id != completed[1].id
                ):
                    raise WorkflowConflictError(
                        f"Completed AssetPlan work item {work_item.id} declares a canonical "
                        "review purpose but does not reference the asset's canonical version"
                    )
                for reference in work_item.ordered_references:
                    validate_asset_version(
                        reference.asset_id,
                        reference.asset_version_id,
                        expected_kind=None,
                    )
                if work_item.compose_intent is not None:
                    for cell in work_item.compose_intent.cells:
                        source = cell.source
                        validate_asset_version(
                            source.asset_id,
                            source.asset_version_id,
                            expected_kind=None,
                            require_usable_image=True,
                        )
                        if completed is None or source.source_work_item_id is None:
                            continue
                        source_work_item = work_items_by_id[
                            source.source_work_item_id
                        ]
                        if (
                            source_work_item.completed_asset_id is None
                            or source_work_item.completed_asset_version_id is None
                        ):
                            raise WorkflowConflictError(
                                f"Completed COMPOSE work item {work_item.id} requires "
                                f"completed cell source {source_work_item.id}"
                            )
                        validate_asset_version(
                            source_work_item.completed_asset_id,
                            source_work_item.completed_asset_version_id,
                            expected_kind=None,
                            require_completed_media=True,
                            require_usable_image=True,
                        )
                pack_intent = work_item.reference_pack_intent
                if pack_intent is None:
                    continue
                if (
                    work_item.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
                    and pack_intent.role != ReferenceRole.CANONICAL_MASTER
                ) or (
                    work_item.review_purpose
                    == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
                    and pack_intent.role == ReferenceRole.CANONICAL_MASTER
                ):
                    raise WorkflowConflictError(
                        f"AssetPlan work item {work_item.id} review purpose conflicts "
                        "with its ReferencePack role"
                    )
                subject_item = items_by_id.get(pack_intent.subject_item_id)
                if subject_item is None:
                    raise WorkflowConflictError(
                        f"AssetPlan work item {work_item.id} references unknown "
                        f"ReferencePack subject item {pack_intent.subject_item_id}"
                    )
                if not role_is_compatible(subject_item.asset_kind, pack_intent.role):
                    raise WorkflowConflictError(
                        f"ReferencePack role {pack_intent.role.value} is incompatible "
                        f"with subject item {subject_item.id} kind "
                        f"{subject_item.asset_kind.value}"
                    )
                subject_completed = validate_asset_version(
                    subject_item.completed_asset_id,
                    subject_item.completed_asset_version_id,
                    expected_kind=subject_item.asset_kind,
                    require_completed_media=(completed is not None),
                )
                if completed is not None and subject_completed is None:
                    raise WorkflowConflictError(
                        f"Completed AssetPlan work item {work_item.id} requires completed "
                        f"ReferencePack subject item {subject_item.id}"
                    )
                if (
                    completed is not None
                    and pack_intent.role == ReferenceRole.CANONICAL_MASTER
                    and completed != subject_completed
                ):
                    raise WorkflowConflictError(
                        f"Canonical ReferencePack work item {work_item.id} must complete "
                        "the subject item's canonical AssetVersion"
                    )
                if pack_intent.lineage_root is None:
                    continue
                lineage_root = pack_intent.lineage_root
                lineage_completed = validate_asset_version(
                    lineage_root.asset_id,
                    lineage_root.asset_version_id,
                    expected_kind=subject_item.asset_kind,
                    require_completed_media=(completed is not None),
                )
                if (
                    completed is not None
                    and lineage_completed is not None
                    and subject_completed is not None
                    and lineage_completed[0].id != subject_completed[0].id
                ):
                    raise WorkflowConflictError(
                        f"AssetPlan work item {work_item.id} lineage root must belong "
                        "to its ReferencePack subject asset"
                    )
                if completed is None or lineage_root.source_work_item_id is None:
                    continue
                source_work_item = work_items_by_id[lineage_root.source_work_item_id]
                if (
                    source_work_item.completed_asset_id is None
                    or source_work_item.completed_asset_version_id is None
                ):
                    raise WorkflowConflictError(
                        f"Completed AssetPlan work item {work_item.id} requires its lineage-root "
                        f"work item {source_work_item.id} to be completed"
                    )
                source_completed = validate_asset_version(
                    source_work_item.completed_asset_id,
                    source_work_item.completed_asset_version_id,
                    expected_kind=subject_item.asset_kind,
                    require_completed_media=True,
                )
                if (
                    source_completed is not None
                    and subject_completed is not None
                    and source_completed[0].id != subject_completed[0].id
                ):
                    raise WorkflowConflictError(
                        f"AssetPlan work item {work_item.id} lineage-root work item must "
                        "complete the ReferencePack subject asset"
                    )

    @staticmethod
    def _normalize_storyboard_revisions(
        storyboard: StoryboardContent,
        *,
        previous: StoryboardContent | None,
    ) -> StoryboardContent:
        previous_by_id = (
            {} if previous is None else {item.id: item for item in previous.shots}
        )
        normalized_shots: list[ShotPlan] = []
        for shot in storyboard.shots:
            prior = previous_by_id.get(shot.id)
            if prior is None:
                revision = 1
            else:
                prior_payload = prior.model_dump(mode="json", exclude={"revision"})
                next_payload = shot.model_dump(mode="json", exclude={"revision"})
                revision = prior.revision if prior_payload == next_payload else prior.revision + 1
            normalized_shots.append(
                shot.model_copy(update={"revision": revision}, deep=True)
            )
        payload = storyboard.model_dump(mode="json")
        payload["shots"] = [
            item.model_dump(mode="json") for item in normalized_shots
        ]
        payload["computed_duration_seconds"] = sum(
            item.duration_seconds for item in normalized_shots
        )
        return StoryboardContent.model_validate(payload)

    @staticmethod
    def _changed_storyboard_shot_ids(
        previous: StoryboardContent,
        current: StoryboardContent,
    ) -> list[str]:
        previous_by_id = {item.id: item for item in previous.shots}
        current_by_id = {item.id: item for item in current.shots}
        affected_shot_ids = {
            shot_id
            for shot_id in set(previous_by_id) | set(current_by_id)
            if shot_id not in previous_by_id
            or shot_id not in current_by_id
            or previous_by_id[shot_id].revision != current_by_id[shot_id].revision
        }
        previous_groups = {item.id: item for item in previous.clip_groups}
        current_groups = {item.id: item for item in current.clip_groups}
        for group_id in set(previous_groups) | set(current_groups):
            previous_group = previous_groups.get(group_id)
            current_group = current_groups.get(group_id)
            if previous_group == current_group:
                continue
            if previous_group is not None:
                affected_shot_ids.update(previous_group.shot_ids)
            if current_group is not None:
                affected_shot_ids.update(current_group.shot_ids)
        return sorted(affected_shot_ids)

    @staticmethod
    def _default_storyboard_execution_entry(shot: ShotPlan) -> ExecutionPlanEntry:
        has_dialogue = bool(shot.dialogue_line_ids)
        return ExecutionPlanEntry(
            shot_id=shot.id,
            strategy=(
                ExecutionStrategy.AUDIO_DRIVEN
                if has_dialogue
                else ExecutionStrategy.DIRECT_AUDIOVISUAL
            ),
            rationale=(
                "镜头包含对白，先保留声音节奏再由生成执行补足画面。"
                if has_dialogue
                else "镜头可直接按完整视听意图生成。"
            ),
            skipped_optional_steps=[],
            provider="minimax",
            capability_snapshot=CapabilitySnapshot(
                provider="minimax",
                model="MiniMax-H3",
                resolution="768P",
                native_audio=True,
                supports_frame_reference=True,
                supports_asset_reference=True,
            ),
            candidate_count=1,
            fallback="由 Agent 根据生成检查结果决定重生或局部修复。",
        )

    @staticmethod
    def _default_storyboard_reference_entry(shot: ShotPlan) -> ReferencePlanEntry:
        return ReferencePlanEntry(
            shot_id=shot.id,
            bindings=[],
            capability_valid=True,
            validation_notes=[
                "当前尚无显式引用绑定；Agent 可按资产与镜头语义补充。"
            ],
            requirements=[],
            max_reference_images=9,
            planning_notes="storyboard-projection",
        )

    def _sync_storyboard_projection(
        self,
        project_document: ProjectDocument,
        storyboard: StoryboardContent,
    ) -> None:
        previous_shots = {item.id: item for item in project_document.shots}
        execution_by_shot = {
            item.shot_id: item for item in project_document.execution_plan
        }
        reference_by_shot = {
            item.shot_id: item for item in project_document.reference_plan
        }
        next_execution: list[ExecutionPlanEntry] = []
        next_references: list[ReferencePlanEntry] = []
        for shot in storyboard.shots:
            previous = previous_shots.get(shot.id)
            unchanged = previous is not None and previous.revision == shot.revision
            execution = execution_by_shot.get(shot.id) if unchanged else None
            reference = reference_by_shot.get(shot.id) if unchanged else None
            next_execution.append(
                execution.model_copy(deep=True)
                if execution is not None
                else self._default_storyboard_execution_entry(shot)
            )
            next_references.append(
                reference.model_copy(deep=True)
                if reference is not None
                else self._default_storyboard_reference_entry(shot)
            )
        project_document.shots = [
            item.model_copy(deep=True) for item in storyboard.shots
        ]
        project_document.execution_plan = next_execution
        project_document.reference_plan = next_references

    def _after_storyboard_revision(
        self,
        project_document: ProjectDocument,
        changed_shot_ids: list[str],
        *,
        updated_at: str,
    ) -> None:
        if changed_shot_ids:
            self.repository.mark_takes_stale(
                project_document.project.id,
                changed_shot_ids,
            )
            self._invalidate_current_manifests(project_document)
        self._invalidate_asset_plan_locked(
            project_document,
            "Storyboard changed after the AssetPlan was authored.",
            updated_at=updated_at,
        )
        self._invalidate_generation_plan_locked(
            project_document,
            "Storyboard changed after the GenerationPlan was compiled.",
            updated_at=updated_at,
        )
        project_document.project.stage = ProjectStage.PLANNED

    def _create_production_document_locked(
        self,
        project_document: ProjectDocument,
        *,
        kind: str,
        title: str,
        payload: dict[str, JsonValue],
        source_artifact_refs: list[ProductionDocumentArtifactRef],
        origin: ProductionDocumentOrigin,
        created_at: str,
    ) -> ProductionDocumentMutationResponse:
        self._validate_production_document_source_refs(
            project_document,
            source_artifact_refs,
        )
        version = ProductionDocumentVersion(
            id=f"production-document-version-{uuid4().hex[:12]}",
            version=1,
            payload=deepcopy(payload),
            source_artifact_refs=[
                item.model_copy(deep=True) for item in source_artifact_refs
            ],
            origin=origin,
            created_at=created_at,
        )
        production_document = ProductionDocument(
            id=f"production-document-{uuid4().hex[:12]}",
            project_id=project_document.project.id,
            kind=kind.strip(),
            title=title.strip(),
            current_version_id=version.id,
            versions=[version],
        )
        project_document.production_documents.append(production_document)
        return ProductionDocumentMutationResponse(
            document=production_document,
            version=version,
        )

    def _revise_production_document_locked(
        self,
        project_document: ProjectDocument,
        *,
        production_document_id: str,
        base_version_id: str,
        payload: dict[str, JsonValue],
        source_artifact_refs: list[ProductionDocumentArtifactRef],
        origin: ProductionDocumentOrigin,
        created_at: str,
    ) -> ProductionDocumentMutationResponse:
        production_document = self._production_document(
            project_document,
            production_document_id,
        )
        if base_version_id != production_document.current_version_id:
            raise WorkflowConflictError(
                "Production document revision is based on a stale version: "
                f"expected {production_document.current_version_id}, "
                f"received {base_version_id}"
            )
        current = self._production_document_version(production_document)
        self._validate_production_document_source_refs(
            project_document,
            source_artifact_refs,
        )
        version = ProductionDocumentVersion(
            id=f"production-document-version-{uuid4().hex[:12]}",
            version=max(item.version for item in production_document.versions) + 1,
            parent_version_id=current.id,
            payload=deepcopy(payload),
            source_artifact_refs=[
                item.model_copy(deep=True) for item in source_artifact_refs
            ],
            origin=origin,
            created_at=created_at,
        )
        production_document.versions.append(version)
        production_document.current_version_id = version.id
        return ProductionDocumentMutationResponse(
            document=production_document,
            version=version,
        )

    def _validate_production_document_source_refs(
        self,
        project_document: ProjectDocument,
        source_artifact_refs: list[ProductionDocumentArtifactRef],
    ) -> None:
        """Keep authoritative documents linked to real, project-local snapshots."""

        project_id = project_document.project.id
        resolver = ProjectArtifactResolver(self.repository)
        for source_ref in source_artifact_refs:
            if source_ref.project_id != project_id:
                raise WorkflowConflictError(
                    "Production document source artifacts must belong to the "
                    f"current project: {source_ref.artifact_type}:"
                    f"{source_ref.artifact_id} belongs to {source_ref.project_id}"
                )
            runtime_ref = ArtifactRef.model_validate(
                source_ref.model_dump(mode="json")
            )
            if runtime_ref.requires_explicit_version() and runtime_ref.version is None:
                raise WorkflowConflictError(
                    "Production document source artifact requires an explicit version: "
                    f"{runtime_ref.artifact_type}:{runtime_ref.artifact_id}"
                )
            if resolver.summarize_artifact(runtime_ref) is None:
                suffix = (
                    ""
                    if runtime_ref.version is None
                    else f"@{runtime_ref.version}"
                )
                raise WorkflowConflictError(
                    "Production document source artifact cannot be resolved in the "
                    f"current project: {runtime_ref.artifact_type}:"
                    f"{runtime_ref.artifact_id}{suffix}"
                )

    def bind_master_audio(
        self,
        project_id: str,
        request: MasterAudioBindRequest,
    ) -> MasterAudioBindingResponse:
        """Bind an approved local audio AssetVersion as the project time source."""

        with self._lock:
            project_document = self._require_project_document(project_id)
            asset, version, audio_path = self._require_current_approved_audio_version(
                project_document,
                request.asset_version_id,
            )
            asset_id = asset.id
            version_number = version.version

        try:
            metadata = probe_media_with_ffmpeg(audio_path)
        except (MediaGenerationError, OSError, ValueError) as exc:
            raise WorkflowConflictError(
                f"Master Audio could not be inspected: {exc}"
            ) from exc
        if metadata["audio"] != "present":
            raise WorkflowConflictError(
                "Master Audio AssetVersion contains no decodable audio stream"
            )
        media_duration = float(metadata["duration"])
        if media_duration <= 0.0:
            raise WorkflowConflictError(
                "Master Audio AssetVersion has no verified positive duration"
            )
        source_out = (
            request.source_out_seconds
            if request.source_out_seconds is not None
            else media_duration
        )
        if request.source_in_seconds >= source_out:
            raise WorkflowConflictError(
                "Master Audio source window must have positive duration"
            )
        if source_out > media_duration + 0.05:
            raise WorkflowConflictError(
                "Master Audio source window exceeds the verified media duration"
            )
        source_out = min(source_out, media_duration)
        binding = MasterAudioBinding(
            asset_id=asset_id,
            asset_version_id=request.asset_version_id,
            source_in_seconds=request.source_in_seconds,
            source_out_seconds=source_out,
            time_origin_seconds=request.time_origin_seconds,
            duration_seconds=source_out - request.source_in_seconds,
        )

        with self._lock:
            project_document = self._require_project_document(project_id)
            current_asset, current_version, current_path = (
                self._require_current_approved_audio_version(
                    project_document,
                    request.asset_version_id,
                )
            )
            if (
                current_asset.id != asset_id
                or current_version.version != version_number
                or current_path != audio_path
            ):
                raise WorkflowConflictError(
                    "Master Audio AssetVersion changed while it was being inspected"
                )
            now = utc_now()
            source_refs = [
                ProductionDocumentArtifactRef(
                    project_id=project_id,
                    artifact_type="asset_version",
                    artifact_id=current_version.id,
                    version=current_version.version,
                )
            ]
            if project_document.master_audio_document_id is None:
                if request.base_version_id is not None:
                    raise WorkflowConflictError(
                        "A base version cannot be supplied before Master Audio exists"
                    )
                result = self._create_production_document_locked(
                    project_document,
                    kind="audio.master",
                    title="Master Audio Binding",
                    payload=binding.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
                project_document.master_audio_document_id = result.document.id
            else:
                if request.base_version_id is None:
                    raise WorkflowConflictError(
                        "Rebinding Master Audio requires its current base_version_id"
                    )
                result = self._revise_production_document_locked(
                    project_document,
                    production_document_id=project_document.master_audio_document_id,
                    base_version_id=request.base_version_id,
                    payload=binding.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
            self._invalidate_audio_led_production_after_master_audio_change_locked(
                project_document,
                master_audio_version_id=result.version.id,
                updated_at=now,
            )
            # These documents remain in history but no longer describe the new source.
            project_document.audio_structure_document_id = None
            project_document.timed_text_document_id = None
            self._invalidate_current_manifests(project_document)
            project_document.project.updated_at = now
            self.repository.save_project(project_document)
            return MasterAudioBindingResponse(
                binding=binding,
                document=result.document,
                version=result.version,
            )

    def update_timed_text_lock(
        self,
        project_id: str,
        request: TimedTextLockUpdateRequest,
    ) -> TimedTextLockResponse:
        """Persist exact timed text against one current Master Audio version."""

        with self._lock:
            project_document = self._require_project_document(project_id)
            master_document, master_version, binding = (
                self._current_master_audio_binding(project_document)
            )
            if request.master_audio_version_id != master_version.id:
                raise WorkflowConflictError(
                    "Timed text is based on a stale Master Audio binding version"
                )
            lock = TimedTextLockSet(
                master_audio_version_id=master_version.id,
                entries=[item.model_copy(deep=True) for item in request.entries],
            )
            timeline_end = binding.time_origin_seconds + binding.duration_seconds
            outside = [
                item.id
                for item in lock.entries
                if item.start_seconds < binding.time_origin_seconds
                or item.end_seconds > timeline_end + 0.001
            ]
            if outside:
                raise WorkflowConflictError(
                    "Timed text entries fall outside the current Master Audio window: "
                    + ", ".join(outside)
                )
            now = utc_now()
            source_refs = self._master_audio_source_refs(
                project_id,
                master_document,
                master_version,
            )
            if project_document.timed_text_document_id is None:
                if request.base_version_id is not None:
                    raise WorkflowConflictError(
                        "A base version cannot be supplied before timed text exists"
                    )
                result = self._create_production_document_locked(
                    project_document,
                    kind="audio.timed-text-lock",
                    title="Timed Text Lock",
                    payload=lock.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
                project_document.timed_text_document_id = result.document.id
            else:
                if request.base_version_id is None:
                    raise WorkflowConflictError(
                        "Updating timed text requires its current base_version_id"
                    )
                result = self._revise_production_document_locked(
                    project_document,
                    production_document_id=project_document.timed_text_document_id,
                    base_version_id=request.base_version_id,
                    payload=lock.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=request.origin,
                    created_at=now,
                )
            self._invalidate_current_manifests(project_document)
            project_document.project.updated_at = now
            self.repository.save_project(project_document)
            return TimedTextLockResponse(
                lock=lock,
                document=result.document,
                version=result.version,
            )

    def analyze_music(
        self,
        project_id: str,
        request: MusicAnalysisRequest,
    ) -> MusicAnalysisResponse:
        """Measure the current Master Audio and persist the result as a document."""

        with self._lock:
            project_document = self._require_project_document(project_id)
            master_document, master_version, binding = (
                self._current_master_audio_binding(project_document)
            )
            if request.master_audio_version_id != master_version.id:
                raise WorkflowConflictError(
                    "Music analysis is based on a stale Master Audio binding version"
                )
            _, _, audio_path = self._require_current_approved_audio_version(
                project_document,
                binding.asset_version_id,
            )
            binding_snapshot = binding.model_copy(deep=True)
            analyzed_at = utc_now()

        try:
            structure = self.music_analysis_service.analyze(
                audio_path=audio_path,
                binding=binding_snapshot,
                created_at=analyzed_at,
            )
        except MusicAnalysisError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self._validate_audio_structure_binding(structure, binding_snapshot)

        with self._lock:
            project_document = self._require_project_document(project_id)
            current_master_document, current_master_version, current_binding = (
                self._current_master_audio_binding(project_document)
            )
            if (
                current_master_document.id != master_document.id
                or current_master_version.id != master_version.id
                or current_binding != binding_snapshot
            ):
                raise WorkflowConflictError(
                    "Master Audio binding changed while music analysis was running"
                )
            self._require_current_approved_audio_version(
                project_document,
                binding_snapshot.asset_version_id,
            )
            source_refs = self._master_audio_source_refs(
                project_id,
                current_master_document,
                current_master_version,
            )
            now = utc_now()
            if project_document.audio_structure_document_id is None:
                result = self._create_production_document_locked(
                    project_document,
                    kind="audio.structure-map",
                    title="Audio Structure Map",
                    payload=structure.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=ProductionDocumentOrigin.AGENT,
                    created_at=now,
                )
                project_document.audio_structure_document_id = result.document.id
            else:
                current_structure_document = self._production_document(
                    project_document,
                    project_document.audio_structure_document_id,
                )
                result = self._revise_production_document_locked(
                    project_document,
                    production_document_id=current_structure_document.id,
                    base_version_id=current_structure_document.current_version_id,
                    payload=structure.model_dump(mode="json"),
                    source_artifact_refs=source_refs,
                    origin=ProductionDocumentOrigin.AGENT,
                    created_at=now,
                )
            project_document.project.updated_at = now
            self.repository.save_project(project_document)
            return MusicAnalysisResponse(
                structure=structure,
                document=result.document,
                version=result.version,
            )

    @staticmethod
    def _master_audio_source_refs(
        project_id: str,
        document: ProductionDocument,
        version: ProductionDocumentVersion,
    ) -> list[ProductionDocumentArtifactRef]:
        return [
            ProductionDocumentArtifactRef(
                project_id=project_id,
                artifact_type="production_document",
                artifact_id=document.id,
                version=version.version,
            ),
            ProductionDocumentArtifactRef(
                project_id=project_id,
                artifact_type="production_document_version",
                artifact_id=version.id,
                version=version.version,
            ),
        ]

    @staticmethod
    def _validate_audio_structure_binding(
        structure: AudioStructureMap,
        binding: MasterAudioBinding,
    ) -> None:
        matching_identity = (
            structure.asset_id == binding.asset_id
            and structure.asset_version_id == binding.asset_version_id
        )
        matching_window = all(
            abs(left - right) <= 0.001
            for left, right in (
                (structure.source_in_seconds, binding.source_in_seconds),
                (structure.source_out_seconds, binding.source_out_seconds),
                (structure.time_origin_seconds, binding.time_origin_seconds),
                (structure.duration_seconds, binding.duration_seconds),
            )
        )
        if not matching_identity or not matching_window:
            raise WorkflowConflictError(
                "Music analysis result does not describe the requested Master Audio binding"
            )

    def _current_master_audio_binding(
        self,
        project_document: ProjectDocument,
    ) -> tuple[ProductionDocument, ProductionDocumentVersion, MasterAudioBinding]:
        if project_document.master_audio_document_id is None:
            raise WorkflowConflictError("Project has no current Master Audio binding")
        document = self._production_document(
            project_document,
            project_document.master_audio_document_id,
        )
        version = self._production_document_version(document)
        try:
            binding = MasterAudioBinding.model_validate(version.payload)
        except ValueError as exc:
            raise WorkflowConflictError(
                "Current Master Audio production document is invalid"
            ) from exc
        return document, version, binding

    @staticmethod
    def _require_current_approved_audio_version(
        project_document: ProjectDocument,
        asset_version_id: str,
    ) -> tuple[Asset, AssetVersion, Path]:
        asset, version = ShortDramaService._find_asset_version(
            project_document,
            asset_version_id,
        )
        if (
            asset.current_version_id != version.id
            or asset.approved_version_id != version.id
            or version.approval_status != ApprovalStatus.APPROVED
            or version.freshness != Freshness.CURRENT
            or version.media_type != AssetMediaType.AUDIO
        ):
            raise WorkflowConflictError(
                f"AssetVersion {version.id} is not its asset's current approved audio"
            )
        if version.local_path is None:
            raise WorkflowConflictError(
                "Master Audio currently requires a local approved audio AssetVersion"
            )
        path = Path(version.local_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise WorkflowConflictError(
                f"Approved audio AssetVersion {version.id} is missing its local media"
            )
        return asset, version, path

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
            source_asset, source_version = self._find_asset_version(
                document,
                request.asset_version_id,
            )
            if (
                source_version.approval_status != ApprovalStatus.APPROVED
                or source_version.freshness != Freshness.CURRENT
            ):
                raise WorkflowConflictError(
                    "Only a current approved formal version or supplemental image "
                    "can enter a reference pack"
                )
            if source_version.media_type != AssetMediaType.IMAGE:
                raise WorkflowConflictError("Reference pack entries currently require image media")
            if source_version.local_path is None and source_version.external_url is None:
                raise WorkflowConflictError(
                    "Reference pack entries require usable image media"
                )
            lineage_root_version_id = request.lineage_root_version_id
            if request.role == ReferenceRole.CANONICAL_MASTER:
                if (
                    source_asset.id != subject.id
                    or subject.approved_version_id != source_version.id
                ):
                    raise WorkflowConflictError(
                        "A CANONICAL_MASTER entry must use the subject asset's current "
                        "approved canonical image"
                    )
                if lineage_root_version_id not in {None, source_version.id}:
                    raise WorkflowConflictError(
                        "A CANONICAL_MASTER entry cannot declare another lineage root"
                    )
                lineage_root_version_id = source_version.id
            if lineage_root_version_id is not None:
                root_asset, root_version = self._find_asset_version(
                    document,
                    lineage_root_version_id,
                )
                if (
                    root_asset.id != subject.id
                    or subject.approved_version_id != root_version.id
                    or root_version.approval_status != ApprovalStatus.APPROVED
                    or root_version.freshness != Freshness.CURRENT
                    or root_version.media_type != AssetMediaType.IMAGE
                    or (
                        root_version.local_path is None
                        and root_version.external_url is None
                    )
                ):
                    raise WorkflowConflictError(
                        "Reference-pack lineage root must be the subject's current "
                        "approved canonical image"
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
                    lineage_root_version_id=lineage_root_version_id,
                    role=request.role,
                    angle=request.angle,
                    framing=request.framing,
                    tags=normalized_tags,
                    description=request.description.strip(),
                    priority=request.priority,
                )
                pack.entries.append(entry)
            else:
                existing.lineage_root_version_id = lineage_root_version_id
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

    def inspect_asset_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetVersionInspectionRequest,
    ) -> SemanticAssetReview:
        """Semantically inspect an actual image before recording a review decision."""

        document = self._require_project_document(project_id)
        asset = self._require_asset_in_document(document, asset_id)
        version = next(
            (item for item in asset.versions if item.id == version_id),
            None,
        )
        if version is None:
            raise ArtifactNotFoundError(version_id)
        if version.media_type != AssetMediaType.IMAGE or (
            version.local_path is None and version.external_url is None
        ):
            raise WorkflowConflictError(
                f"AssetVersion {version.id} is not a usable image version"
            )
        if version.local_path is not None:
            candidate_path = Path(version.local_path).expanduser().resolve()
            if not candidate_path.is_file():
                raise WorkflowConflictError(
                    f"AssetVersion image is missing: {candidate_path}"
                )

        shot = (
            self._require_shot_in_document(document, request.shot_id)
            if request.shot_id is not None
            else None
        )
        canonical_references: list[tuple[Asset, AssetVersion]] = []
        seen_reference_ids: set[str] = set()
        for reference_version_id in request.canonical_reference_version_ids:
            if reference_version_id in seen_reference_ids:
                continue
            reference_asset, reference_version = self._find_asset_version(
                document,
                reference_version_id,
            )
            if (
                reference_asset.approved_version_id != reference_version.id
                or reference_version.approval_status != ApprovalStatus.APPROVED
                or reference_version.media_type != AssetMediaType.IMAGE
                or (
                    reference_version.local_path is None
                    and reference_version.external_url is None
                )
            ):
                raise WorkflowConflictError(
                    "Canonical inspection references must be the current approved "
                    f"image version of their asset: {reference_version.id}"
                )
            if reference_version.local_path is not None:
                reference_path = Path(reference_version.local_path).expanduser().resolve()
                if not reference_path.is_file():
                    raise WorkflowConflictError(
                        f"Canonical reference image is missing: {reference_path}"
                    )
            canonical_references.append((reference_asset, reference_version))
            seen_reference_ids.add(reference_version_id)

        client = self._require_language_client()
        try:
            return inspect_asset_image(
                document=document,
                asset=asset,
                version=version,
                client=client,
                shot=shot,
                canonical_references=canonical_references,
            )
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc

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
        generation_plan: GenerationPlanContent | None = None
        selected_generation_units = None
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            if refresh_lightweight_reference_plans(document):
                document.project.updated_at = utc_now()
                self.repository.save_project(document)
            if request.generation_plan_document_id is not None:
                generation_document = self._managed_project_document(
                    document,
                    request.generation_plan_document_id,
                    GENERATION_PLAN_DOCUMENT_KIND,
                )
                if generation_document.id != document.generation_plan_document_id:
                    raise WorkflowConflictError(
                        "H3 route planning must use the project's GenerationPlan"
                    )
                generation_version = self._production_document_version(
                    generation_document,
                    request.generation_plan_version_id,
                )
                if generation_version.id != generation_document.current_version_id:
                    raise WorkflowConflictError(
                        "H3 route planning requires the current GenerationPlan version"
                    )
                generation_plan = self._parse_generation_plan(generation_version)
                generation_slot = self._project_output_slot(
                    document,
                    GENERATION_PLAN_OUTPUT_SLOT_ID,
                )
                if (
                    generation_slot is None
                    or generation_slot.current_artifact_ref is None
                    or generation_slot.current_artifact_ref.artifact_id
                    != generation_document.id
                    or generation_slot.current_artifact_ref.version
                    != generation_version.version
                ):
                    raise WorkflowConflictError(
                        "The GenerationPlan output slot is invalid; recompile it first"
                    )
                if (
                    generation_plan.script_version_id
                    != document.project.current_script_version_id
                    or generation_plan.bible_version_id
                    != document.project.current_bible_version_id
                ):
                    raise WorkflowConflictError(
                        "GenerationPlan references outdated story inputs"
                    )
                if (
                    generation_plan.storyboard_document_id
                    != document.storyboard_document_id
                ):
                    raise WorkflowConflictError(
                        "GenerationPlan references a different Storyboard"
                    )
                storyboard_document = self._managed_project_document(
                    document,
                    generation_plan.storyboard_document_id,
                    STORYBOARD_DOCUMENT_KIND,
                )
                storyboard_version = self._production_document_version(
                    storyboard_document,
                    generation_plan.storyboard_version_id,
                )
                if storyboard_version.id != storyboard_document.current_version_id:
                    raise WorkflowConflictError(
                        "GenerationPlan references an outdated Storyboard version"
                    )
                production_spec_document = self._managed_project_document(
                    document,
                    generation_plan.production_spec_document_id,
                    PRODUCTION_SPEC_DOCUMENT_KIND,
                )
                production_spec_version = self._production_document_version(
                    production_spec_document,
                    generation_plan.production_spec_version_id,
                )
                if (
                    production_spec_document.id != document.production_spec_document_id
                    or production_spec_version.id
                    != production_spec_document.current_version_id
                ):
                    raise WorkflowConflictError(
                        "GenerationPlan references an outdated ProductionSpec version"
                    )
                selected_ids = set(request.unit_ids)
                selected_generation_units = [
                    item
                    for item in generation_plan.units
                    if not selected_ids or item.id in selected_ids
                ]
                unknown_unit_ids = sorted(
                    selected_ids - {item.id for item in generation_plan.units}
                )
                if unknown_unit_ids:
                    raise ArtifactNotFoundError(", ".join(unknown_unit_ids))
                if not selected_generation_units:
                    raise WorkflowConflictError(
                        "H3 route planning requires at least one GenerationUnit"
                    )
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
            item.shot_id: item for item in request.shot_directives if item.shot_id
        }
        if request.consider_video_guides and generation_plan is None:
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
                generation_units=selected_generation_units,
                generation_plan_document_id=(
                    request.generation_plan_document_id
                    if generation_plan is not None
                    else None
                ),
                generation_plan_version_id=(
                    request.generation_plan_version_id
                    if generation_plan is not None
                    else None
                ),
                storyboard_document_id=(
                    generation_plan.storyboard_document_id
                    if generation_plan is not None
                    else None
                ),
                storyboard_version_id=(
                    generation_plan.storyboard_version_id
                    if generation_plan is not None
                    else None
                ),
            )
        except RoutePlanningError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self.repository.save_h3_route_plan(plan)
        return H3ProductionRoutePlanResponse(plan=plan)

    def update_h3_route_asset_preparation(
        self,
        project_id: str,
        plan_id: str,
        preparation_id: str,
        request: H3RouteAssetPreparationUpdateRequest,
    ) -> H3RouteAssetPreparationUpdateResponse:
        """Persist an image worker's exact handoff back to one H3 preparation."""

        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            plan = self._require_h3_route_plan_state(
                project_id,
                plan_id,
                request.expected_state_revision,
            )
            try:
                validate_route_plan_freshness(plan, document)
            except RoutePlanningError as exc:
                if plan.status != H3RoutePlanStatus.STALE:
                    plan.status = H3RoutePlanStatus.STALE
                    self._touch_h3_route_plan(plan)
                    self.repository.save_h3_route_plan(plan)
                raise WorkflowConflictError(str(exc)) from exc

            decision = self._h3_route_decision(plan, request.shot_id)
            matching_preparations = [
                item
                for item in decision.asset_preparations
                if item.id == preparation_id
            ]
            if not matching_preparations:
                raise ArtifactNotFoundError(preparation_id)
            if len(matching_preparations) != 1:
                raise WorkflowConflictError(
                    f"H3 route plan {plan.id} contains duplicate asset preparation "
                    f"{preparation_id}"
                )
            preparation = matching_preparations[0]
            if preparation.shot_id != decision.shot_id:
                raise WorkflowConflictError(
                    f"H3 asset preparation {preparation.id} has inconsistent shot ownership"
                )
            if preparation.role != request.role:
                raise WorkflowConflictError(
                    f"H3 asset preparation {preparation.id} expects role "
                    f"{preparation.role.value}, not {request.role.value}"
                )
            if (
                request.status == H3RouteAssetPreparationUpdateStatus.READY
                and not self._h3_preparation_inputs_are_current(document, preparation)
            ):
                raise WorkflowConflictError(
                    f"H3 asset preparation {preparation.id} uses source asset versions "
                    "that are no longer current approved inputs"
                )

            resolved_asset_id = request.asset_id
            if resolved_asset_id is None:
                resolved_asset_id = preparation.asset_id
            asset = (
                self._require_asset_in_document(document, resolved_asset_id)
                if resolved_asset_id is not None
                else None
            )
            effective_task_ids = (
                list(request.image_task_ids)
                if request.image_task_ids is not None
                else list(preparation.image_task_ids)
            )

            approved_version_id: str | None = None
            approved_version_number: int | None = None
            if request.approved_version_id is not None:
                if asset is None:
                    raise WorkflowConflictError(
                        "An approved H3 preparation version requires its owning asset"
                    )
                version_asset, version = self._find_asset_version(
                    document,
                    request.approved_version_id,
                )
                if version_asset.id != asset.id:
                    raise WorkflowConflictError(
                        f"AssetVersion {version.id} does not belong to asset {asset.id}"
                    )
                if (
                    version.approval_status != ApprovalStatus.APPROVED
                    or version.freshness != Freshness.CURRENT
                    or version.media_type != AssetMediaType.IMAGE
                    or (
                        version.local_path is None
                        and version.external_url is None
                    )
                ):
                    raise WorkflowConflictError(
                        f"AssetVersion {version.id} is not a current approved image"
                    )
                if version.local_path is not None and not Path(
                    version.local_path
                ).expanduser().resolve().is_file():
                    raise WorkflowConflictError(
                        f"Approved image AssetVersion {version.id} is missing its local media"
                    )
                approved_version_id = version.id
                approved_version_number = version.version
                if (
                    version.image_task_id is not None
                    and version.image_task_id not in effective_task_ids
                ):
                    effective_task_ids.append(version.image_task_id)

            if effective_task_ids and asset is None:
                raise WorkflowConflictError(
                    "H3 preparation image tasks require their owning asset"
                )
            for task_id in effective_task_ids:
                assert asset is not None
                task = self.repository.get_image_task(task_id)
                if task is None:
                    raise ArtifactNotFoundError(task_id)
                if task.project_id != project_id or task.asset_id != asset.id:
                    raise WorkflowConflictError(
                        f"Image task {task.id} does not belong to H3 preparation asset "
                        f"{asset.id} in project {project_id}"
                    )

            target_status = H3RouteAssetPreparationStatus(request.status.value)
            target_summary: str | None = None
            if request.review_summary is not None:
                target_summary = request.review_summary.strip() or None
            changed = any(
                (
                    preparation.status != target_status,
                    preparation.asset_id != resolved_asset_id,
                    preparation.image_task_ids != effective_task_ids,
                    preparation.approved_version_id != approved_version_id,
                    preparation.review_summary != target_summary,
                )
            )
            if changed:
                preparation.status = target_status
                preparation.asset_id = resolved_asset_id
                preparation.image_task_ids = effective_task_ids
                preparation.approved_version_id = approved_version_id
                preparation.review_summary = target_summary
                self._touch_h3_route_plan(plan)
                self.repository.save_h3_route_plan(plan)

            return H3RouteAssetPreparationUpdateResponse(
                plan=plan,
                preparation=preparation,
                approved_version_number=approved_version_number,
            )

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
                or task.generation_unit_id != decision.generation_unit_id
                or task.clip_group_id != decision.clip_group_id
                or task.output_slot_id != decision.output_slot_id
                or task.shot_windows != decision.shot_source_windows
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

    def _h3_route_candidate_reconciliation(
        self,
        document: ProjectDocument,
        plan: H3ProductionRoutePlan,
        decision: H3ShotRoutePlan,
        planned_tasks: list[GenerationTask],
        media_inputs: list[H3MediaInput],
    ) -> list[tuple[int, str, GenerationTask | None]]:
        """Resolve every declared candidate slot by its stable request fingerprint."""

        shot_tasks = self.repository.list_tasks_for_shot(
            document.project.id,
            decision.shot_id,
        )
        first_candidate_index = (
            min(task.candidate_index for task in planned_tasks)
            if planned_tasks
            else max((task.candidate_index for task in shot_tasks), default=0) + 1
        )
        expected_indices = range(
            first_candidate_index,
            first_candidate_index + decision.candidate_count,
        )
        expected_fingerprints = {
            candidate_index: self._h3_candidate_request_fingerprint(
                document=document,
                shot_id=decision.shot_id,
                route=decision.route,
                duration_seconds=decision.duration_seconds,
                prompt=decision.prompt,
                media_inputs=media_inputs,
                learning_objective=None,
                regression_case_id=None,
                candidate_index=candidate_index,
                attempt=1,
                storyboard_document_id=plan.input_storyboard_document_id,
                storyboard_version_id=plan.input_storyboard_version_id,
                generation_plan_document_id=plan.input_generation_plan_document_id,
                generation_plan_version_id=plan.input_generation_plan_version_id,
                generation_unit_id=decision.generation_unit_id,
                clip_group_id=decision.clip_group_id,
                output_slot_id=decision.output_slot_id,
                shot_windows=decision.shot_source_windows,
            )
            for candidate_index in expected_indices
        }
        expected_values = set(expected_fingerprints.values())
        invalid_owned = [
            task.id
            for task in planned_tasks
            if task.request_fingerprint not in expected_values
            or expected_fingerprints.get(task.candidate_index)
            != task.request_fingerprint
        ]
        if invalid_owned:
            raise WorkflowConflictError(
                f"H3 route decision for shot {decision.shot_id} owns tasks outside its "
                "declared candidate identities: " + ", ".join(invalid_owned)
            )

        tasks_by_fingerprint: dict[str, list[GenerationTask]] = {}
        for task in shot_tasks:
            tasks_by_fingerprint.setdefault(task.request_fingerprint, []).append(task)
        result: list[tuple[int, str, GenerationTask | None]] = []
        for candidate_index, fingerprint in expected_fingerprints.items():
            matches = tasks_by_fingerprint.get(fingerprint, [])
            if len(matches) > 1:
                raise WorkflowConflictError(
                    f"H3 candidate fingerprint {fingerprint} resolves to multiple tasks"
                )
            task = matches[0] if matches else None
            if task is not None:
                evidence = self.repository.get_route_evidence_for_task(task.id)
                if evidence is None or evidence.purpose != EvidencePurpose.PRODUCTION:
                    raise WorkflowConflictError(
                        f"H3 candidate task {task.id} lacks production route evidence"
                    )
            result.append((candidate_index, fingerprint, task))
        return result

    def execute_h3_production_route_plan(
        self,
        project_id: str,
        plan_id: str,
        request: H3ProductionRouteExecuteRequest,
    ) -> H3ProductionRouteExecuteResponse:
        inspection_targets: list[tuple[str, str, int, str | None]] = []
        queued_tasks: list[GenerationTask] = []
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            plan = self._require_h3_route_plan_state(
                project_id,
                plan_id,
                request.expected_state_revision,
            )
            try:
                validate_route_plan_freshness(plan, document)
            except RoutePlanningError as exc:
                if plan.status != H3RoutePlanStatus.STALE:
                    plan.status = H3RoutePlanStatus.STALE
                    self._touch_h3_route_plan(plan)
                    self.repository.save_h3_route_plan(plan)
                raise WorkflowConflictError(str(exc)) from exc

            execution_inputs: dict[
                str,
                tuple[
                    ShotPlan,
                    list[H3MediaInput],
                    list[tuple[int, str, GenerationTask | None]],
                ],
            ] = {}
            for decision in plan.decisions:
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
                    candidates = self._h3_route_candidate_reconciliation(
                        document,
                        plan,
                        decision,
                        planned_tasks,
                        media_inputs,
                    )
                    execution_inputs[decision.shot_id] = (
                        shot,
                        media_inputs,
                        candidates,
                    )

            if (
                any(
                    task is None
                    for _shot, _media_inputs, candidates in execution_inputs.values()
                    for _candidate_index, _fingerprint, task in candidates
                )
                and not self.h3_config.api_key()
            ):
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing production H3 tasks"
                )
            execution_status_by_shot: dict[str, H3ShotRouteExecutionStatus] = {}
            for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
                if decision.readiness == H3ShotRouteReadiness.NEEDS_AGENT_DECISION:
                    execution_status_by_shot[decision.shot_id] = (
                        H3ShotRouteExecutionStatus.SKIPPED
                    )
                    continue
                if decision.action == H3ShotRouteAction.REUSE_TAKE:
                    if not request.reuse_passed_takes or decision.reuse_take_id is None:
                        execution_status_by_shot[decision.shot_id] = (
                            H3ShotRouteExecutionStatus.SKIPPED
                        )
                        continue
                    self.select_take(decision.reuse_take_id)
                    execution_status_by_shot[decision.shot_id] = (
                        H3ShotRouteExecutionStatus.REUSED
                    )
                    if request.inspect_reused_takes:
                        inspection_targets.append(
                            (
                                decision.shot_id,
                                decision.reuse_take_id,
                                decision.inspection.max_frames,
                                decision.inspection.review_focus,
                            )
                        )
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
                    execution_status_by_shot[decision.shot_id] = (
                        H3ShotRouteExecutionStatus.SKIPPED
                    )
                    continue
                shot, media_inputs, candidates = execution_inputs[decision.shot_id]
                execution_status_by_shot[decision.shot_id] = (
                    H3ShotRouteExecutionStatus.QUEUED
                )
                for candidate_index, fingerprint, existing_task in candidates:
                    if existing_task is not None:
                        current_decision = self._h3_route_decision(plan, decision.shot_id)
                        if existing_task.id in current_decision.task_ids:
                            continue
                        plan = self._require_h3_route_plan_state(
                            project_id,
                            plan_id,
                            plan.state_revision,
                        )
                        current_decision = self._h3_route_decision(
                            plan,
                            decision.shot_id,
                        )
                        if existing_task.id not in current_decision.task_ids:
                            current_decision.task_ids.append(existing_task.id)
                            current_decision.execution_status = (
                                H3ShotRouteExecutionStatus.QUEUED
                            )
                            plan.status = H3RoutePlanStatus.EXECUTING
                            self._touch_h3_route_plan(plan)
                            self.repository.save_h3_route_plan(plan)
                        continue
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
                        candidate_index=candidate_index,
                        attempt=1,
                        retry_of_task_id=None,
                        storyboard_document_id=plan.input_storyboard_document_id,
                        storyboard_version_id=plan.input_storyboard_version_id,
                        generation_plan_document_id=(
                            plan.input_generation_plan_document_id
                        ),
                        generation_plan_version_id=(
                            plan.input_generation_plan_version_id
                        ),
                        generation_unit_id=decision.generation_unit_id,
                        clip_group_id=decision.clip_group_id,
                        output_slot_id=decision.output_slot_id,
                        shot_windows=decision.shot_source_windows,
                    )
                    if task.request_fingerprint != fingerprint:
                        raise WorkflowConflictError(
                            f"H3 candidate {candidate_index} changed identity before it "
                            "could be persisted"
                        )
                    plan = self._require_h3_route_plan_state(
                        project_id,
                        plan_id,
                        plan.state_revision,
                    )
                    current_decision = self._h3_route_decision(
                        plan,
                        decision.shot_id,
                    )
                    current_decision.task_ids.append(task.id)
                    current_decision.execution_status = H3ShotRouteExecutionStatus.QUEUED
                    plan.status = H3RoutePlanStatus.EXECUTING
                    self._touch_h3_route_plan(plan)
                    self.repository.save_h3_route_task_bundle(
                        task=task,
                        run=run,
                        evidence=route_evidence,
                        plan=plan,
                    )
                    _log_video_task_transition(None, task, action="enqueue")
                    queued_tasks.append(task)

            plan = self._require_h3_route_plan_state(
                project_id,
                plan_id,
                plan.state_revision,
            )
            for shot_id, execution_status in execution_status_by_shot.items():
                self._h3_route_decision(plan, shot_id).execution_status = execution_status
            has_generation_tasks = any(
                item.action == H3ShotRouteAction.GENERATE and item.task_ids
                for item in plan.decisions
            )
            if queued_tasks or has_generation_tasks:
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
            self._touch_h3_route_plan(plan)
            plan.executed_at = plan.updated_at
            self.repository.save_h3_route_plan(plan)

        inspections: list[VideoInspectionReport] = []
        inspection_updates: dict[str, tuple[list[str], list[str]]] = {}
        persisted_by_take: dict[str, VideoInspectionReport] = {}
        for report in self.repository.list_video_inspections(project_id):
            persisted_by_take[report.take_id] = report
        for shot_id, take_id, max_frames, review_focus in inspection_targets:
            existing_report = persisted_by_take.get(take_id)
            if existing_report is not None and (
                not request.semantic_review
                or existing_report.semantic_status == SemanticReviewStatus.COMPLETE
            ):
                report = existing_report
            else:
                response = self.inspect_take_video(
                    take_id,
                    VideoInspectionRequest(
                        include_previous_boundary=True,
                        semantic_review=request.semantic_review,
                        max_frames=max_frames,
                        review_focus=review_focus,
                    ),
                )
                report = response.report
                persisted_by_take[take_id] = report
            inspections.append(report)
            report_ids, issue_codes = inspection_updates.setdefault(
                shot_id,
                ([], []),
            )
            if report.id not in report_ids:
                report_ids.append(report.id)
            for issue in report.issues:
                if issue.code not in issue_codes:
                    issue_codes.append(issue.code)

        manifest = None
        export = None
        if request.rebuild_manifest:
            aggregate = self.get_project(project_id)
            if aggregate.progress.shots_with_selected_take == aggregate.progress.total_shots:
                manifest = self.rebuild_manifest(project_id)
        if request.render_export and manifest is not None:
            export = self.export_project(project_id, rebuild_manifest=False)

        with self._lock:
            plan = self._require_h3_route_plan_state(
                project_id,
                plan_id,
                plan.state_revision,
            )
            inspection_state_changed = False
            for shot_id, (report_ids, issue_codes) in inspection_updates.items():
                decision = self._h3_route_decision(plan, shot_id)
                next_report_ids = list(
                    dict.fromkeys([*decision.inspection_ids, *report_ids])
                )
                next_issue_codes = list(
                    dict.fromkeys([*decision.inspection_issue_codes, *issue_codes])
                )
                if decision.inspection_ids != next_report_ids:
                    decision.inspection_ids = next_report_ids
                    inspection_state_changed = True
                if decision.inspection_issue_codes != next_issue_codes:
                    decision.inspection_issue_codes = next_issue_codes
                    inspection_state_changed = True
            final_status_changed = False
            if not queued_tasks and all(
                item.execution_status == H3ShotRouteExecutionStatus.REUSED
                for item in plan.decisions
            ):
                if plan.status != H3RoutePlanStatus.COMPLETE:
                    plan.status = H3RoutePlanStatus.COMPLETE
                    final_status_changed = True
            if inspection_state_changed or final_status_changed:
                self._touch_h3_route_plan(plan)
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
            with self._lock:
                document = self._require_project_document(project_id)
                now = utc_now()
                if self._invalidate_asset_plan_if_completion_became_unusable_locked(
                    document,
                    reason=(
                        f"AssetVersion {version.id} review changed the truth of a "
                        "completed AssetPlan output."
                    ),
                    updated_at=now,
                ):
                    document.project.updated_at = now
                    self.repository.save_project(document)
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
                    storyboard_document_id=run.storyboard_document_id,
                    storyboard_version_id=run.storyboard_version_id,
                    generation_plan_document_id=run.generation_plan_document_id,
                    generation_plan_version_id=run.generation_plan_version_id,
                    generation_unit_id=run.generation_unit_id,
                    clip_group_id=run.clip_group_id,
                    output_slot_id=run.output_slot_id,
                    shot_windows=run.shot_windows,
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
            now = utc_now()
            if selected.output_slot_id is not None:
                self._bind_output_slot_locked(
                    document,
                    OutputSlotBindRequest(
                        slot_id=selected.output_slot_id,
                        artifact_ref=ProductionDocumentArtifactRef(
                            project_id=selected.project_id,
                            artifact_type="take",
                            artifact_id=selected.id,
                        ),
                    ),
                    updated_at=now,
                )
            document.project.stage = self._task_derived_project_stage(take.project_id)
            document.project.updated_at = now
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
            was_selected = bool(take.selected_shot_ids)
            if status != QaStatus.PASSED:
                take.selected_shot_ids = []
                take.is_selected = False
            self.repository.save_take(take)

            if status != QaStatus.PASSED and was_selected:
                self._invalidate_current_manifests(document)
                self._invalidate_take_output_slot_if_current_locked(
                    document,
                    take,
                    "The selected Take was rejected by the current H3 review.",
                    updated_at=now,
                )

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
            content_changed = any(
                (
                    current_line.spoken_text != revisions[current_line.id].spoken_text
                    or current_line.subtitle_text
                    != revisions[current_line.id].subtitle_text
                )
                for current_line in current.lines
                if current_line.id in revisions
            )
            current.approval_status = ApprovalStatus.SUPERSEDED
            current.freshness = Freshness.STALE
            next_version_number = max(version.version for version in document.script_versions) + 1
            next_lines = [line.model_copy(deep=True) for line in current.lines]
            for line in next_lines:
                revision = revisions.get(line.id)
                if revision is not None:
                    line.spoken_text = revision.spoken_text
                    line.subtitle_text = revision.subtitle_text
            try:
                next_scenes, next_screenplay_text = synchronize_script_line_text(
                    previous_lines=current.lines,
                    revised_lines=next_lines,
                    scenes=current.structured_scenes,
                )
            except ScriptTextSynchronizationError as exc:
                raise WorkflowConflictError(str(exc)) from exc
            now = utc_now()
            next_script = ScriptVersion(
                id=f"{project_id}-script-v{next_version_number}",
                project_id=project_id,
                version=next_version_number,
                source_mode=current.source_mode,
                original_text=current.original_text,
                screenplay_text=next_screenplay_text,
                spoken_text="\n".join(line.spoken_text for line in next_lines),
                subtitle_text="\n".join(line.subtitle_text for line in next_lines),
                lines=next_lines,
                structured_scenes=next_scenes,
                review_report=(
                    None
                    if content_changed
                    else (
                        current.review_report.model_copy(deep=True)
                        if current.review_report is not None
                        else None
                    )
                ),
                review_disposition=(
                    None
                    if content_changed
                    else (
                        current.review_disposition.model_copy(deep=True)
                        if current.review_disposition is not None
                        else None
                    )
                ),
                final_cold_read_report=(
                    None
                    if content_changed
                    else (
                        current.final_cold_read_report.model_copy(deep=True)
                        if current.final_cold_read_report is not None
                        else None
                    )
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
            self._invalidate_output_slot_locked(
                document,
                PRODUCTION_SPEC_OUTPUT_SLOT_ID,
                "Script changed after the ProductionSpec was authored.",
                updated_at=now,
            )
            self._invalidate_output_slot_locked(
                document,
                STORYBOARD_OUTPUT_SLOT_ID,
                "Script changed after the Storyboard was authored.",
                updated_at=now,
            )
            self._invalidate_asset_plan_locked(
                document,
                "Script changed after the AssetPlan was authored.",
                updated_at=now,
            )
            self._invalidate_generation_plan_locked(
                document,
                "Script changed after the GenerationPlan was compiled.",
                updated_at=now,
            )
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

    def _timeline_storyboard_truth(
        self,
        project_document: ProjectDocument,
    ) -> tuple[list[ShotPlan], str | None, str | None]:
        """Resolve the exact storyboard truth consumed by timeline assembly.

        Versioned Storyboards are authoritative once present.  The legacy Shot
        projection remains readable for older local projects, but only while it
        still covers the current ScriptVersion's lines and every projected Shot
        is current.  This prevents a compatibility projection from being stamped
        with a newer ScriptVersion merely because its IDs are still reachable.
        """

        if project_document.storyboard_document_id is not None:
            storyboard_document = self._managed_project_document(
                project_document,
                project_document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            storyboard_version = self._production_document_version(
                storyboard_document
            )
            storyboard = self._parse_storyboard(storyboard_version)
            self._validate_storyboard(project_document, storyboard)
            return (
                [item.model_copy(deep=True) for item in storyboard.shots],
                storyboard_document.id,
                storyboard_version.id,
            )

        shots = [item.model_copy(deep=True) for item in project_document.shots]
        if not shots:
            raise WorkflowConflictError(
                "Timeline rebuild requires a current Storyboard or legacy Shot projection"
            )
        if any(item.freshness != Freshness.CURRENT for item in shots):
            raise WorkflowConflictError(
                "Legacy Shot projection contains stale entries; author a current Storyboard"
            )
        script = self._current_script(project_document)
        projected_line_ids = [
            line_id for shot in shots for line_id in shot.dialogue_line_ids
        ]
        if projected_line_ids != [line.id for line in script.lines]:
            raise WorkflowConflictError(
                "Legacy Shot projection does not cover the current ScriptVersion; "
                "author a current Storyboard before rebuilding the timeline"
            )
        return shots, None, None

    def _validate_timeline_take_source(
        self,
        project_document: ProjectDocument,
        take: Take,
        shot: ShotPlan,
        *,
        storyboard_document_id: str | None,
        storyboard_version_id: str | None,
    ) -> GenerationTask:
        """Require every selected Take to prove its exact current story source."""

        task = self.repository.get_task(take.task_id)
        if task is None or task.project_id != project_document.project.id:
            raise WorkflowConflictError(
                f"Selected Take {take.id} has no generation task in the current project"
            )
        current_script = self._current_script(project_document)
        if task.input_script_version_id != current_script.id:
            script_by_id = {item.id: item for item in project_document.script_versions}
            source_script = script_by_id.get(task.input_script_version_id)
            ancestor_ids: set[str] = set()
            cursor: ScriptVersion | None = current_script
            while cursor is not None:
                ancestor_ids.add(cursor.id)
                cursor = (
                    None
                    if cursor.parent_version_id is None
                    else script_by_id.get(cursor.parent_version_id)
                )
            source_lines = (
                {} if source_script is None else {item.id: item for item in source_script.lines}
            )
            current_lines = {item.id: item for item in current_script.lines}
            dialogue_changed = any(
                line_id not in source_lines
                or line_id not in current_lines
                or source_lines[line_id] != current_lines[line_id]
                for line_id in shot.dialogue_line_ids
            )
            scene_changed = False
            if shot.scene_id is not None and source_script is not None:
                source_scene = next(
                    (
                        item
                        for item in source_script.structured_scenes
                        if item.id == shot.scene_id
                    ),
                    None,
                )
                current_scene = next(
                    (
                        item
                        for item in current_script.structured_scenes
                        if item.id == shot.scene_id
                    ),
                    None,
                )
                scene_changed = source_scene != current_scene
            if (
                source_script is None
                or source_script.id not in ancestor_ids
                or dialogue_changed
                or scene_changed
            ):
                raise WorkflowConflictError(
                    f"Selected Take {take.id} was generated from an outdated ScriptVersion"
                )
        if task.input_bible_version_id != project_document.project.current_bible_version_id:
            raise WorkflowConflictError(
                f"Selected Take {take.id} was generated from an outdated ProjectBibleVersion"
            )

        if task.input_shot_revisions:
            source_revision = task.input_shot_revisions.get(shot.id)
        elif task.shot_id == shot.id:
            source_revision = task.input_shot_revision
        else:
            source_revision = None
        if source_revision != shot.revision:
            raise WorkflowConflictError(
                f"Selected Take {take.id} was generated from an outdated Shot revision"
            )

        if storyboard_document_id is None or storyboard_version_id is None:
            if (
                take.storyboard_document_id is not None
                or take.storyboard_version_id is not None
                or task.storyboard_document_id is not None
                or task.storyboard_version_id is not None
            ):
                raise WorkflowConflictError(
                    f"Selected Take {take.id} references a Storyboard that is no longer current"
                )
            return task

        if (
            task.storyboard_document_id != storyboard_document_id
            or task.storyboard_version_id != storyboard_version_id
        ):
            raise WorkflowConflictError(
                f"Selected Take {take.id} must reference the current exact Storyboard version"
            )
        if (
            take.storyboard_document_id is not None
            or take.storyboard_version_id is not None
        ) and (
            take.storyboard_document_id != storyboard_document_id
            or take.storyboard_version_id != storyboard_version_id
        ):
            raise WorkflowConflictError(
                f"Selected Take {take.id} carries an outdated Storyboard version"
            )
        return task

    def rebuild_manifest(self, project_id: str) -> TimelineManifest:
        with self._lock:
            document = self._require_project_mutation_allowed(project_id)
            current_script = self._current_script(document)
            timeline_shots, storyboard_document_id, storyboard_version_id = (
                self._timeline_storyboard_truth(document)
            )
            production_spec: ProductionSpecContent | None = None
            if document.production_spec_document_id is not None:
                production_spec_document = self._managed_project_document(
                    document,
                    document.production_spec_document_id,
                    PRODUCTION_SPEC_DOCUMENT_KIND,
                )
                production_spec_version = self._production_document_version(
                    production_spec_document
                )
                production_spec = self._parse_production_spec(
                    production_spec_version
                )
                self._validate_production_spec(document, production_spec)
            master_audio: tuple[
                ProductionDocument,
                ProductionDocumentVersion,
                MasterAudioBinding,
                Path,
            ] | None = None
            timed_text: TimedTextLockSet | None = None
            if document.master_audio_document_id is not None:
                master_document, master_version, binding = (
                    self._current_master_audio_binding(document)
                )
                _, _, audio_path = self._require_current_approved_audio_version(
                    document,
                    binding.asset_version_id,
                )
                master_audio = (
                    master_document,
                    master_version,
                    binding,
                    audio_path,
                )
                if document.timed_text_document_id is not None:
                    timed_document = self._production_document(
                        document,
                        document.timed_text_document_id,
                    )
                    timed_version = self._production_document_version(timed_document)
                    try:
                        candidate = TimedTextLockSet.model_validate(
                            timed_version.payload
                        )
                    except ValueError as exc:
                        raise WorkflowConflictError(
                            "Current timed-text production document is invalid"
                        ) from exc
                    if candidate.master_audio_version_id == master_version.id:
                        timed_text = candidate
            master_audio_controls_duration = (
                master_audio is not None
                and production_spec is not None
                and production_spec.duration_contract.authority
                == DurationAuthority.AUDIO_LED
                and production_spec.master_audio_binding_version_id
                == master_audio[1].id
            )
            selected_by_shot: dict[str, Take] = {}
            for take in self.repository.list_takes(project_id):
                if (
                    take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and _take_is_timeline_eligible(take)
                ):
                    for selected_shot_id in take.selected_shot_ids:
                        existing = selected_by_shot.get(selected_shot_id)
                        if existing is not None and existing.id != take.id:
                            raise WorkflowConflictError(
                                "Multiple Takes are selected for shot "
                                f"{selected_shot_id}: {existing.id}, {take.id}"
                            )
                        selected_by_shot[selected_shot_id] = take
            missing = [
                shot.id for shot in timeline_shots if shot.id not in selected_by_shot
            ]
            if missing:
                raise WorkflowConflictError(
                    "Every shot needs a current technically available selected Take before building the timeline: "
                    + ", ".join(missing)
                )
            source_task_by_shot: dict[str, GenerationTask] = {}
            for shot in timeline_shots:
                source_task_by_shot[shot.id] = self._validate_timeline_take_source(
                    document,
                    selected_by_shot[shot.id],
                    shot,
                    storyboard_document_id=storyboard_document_id,
                    storyboard_version_id=storyboard_version_id,
                )
            self._invalidate_current_manifests(document)
            version = max((manifest.version for manifest in document.manifests), default=0) + 1
            now = utc_now()
            video_clips: list[TimelineVideoClip] = []
            audio_clips: list[TimelineAudioClip] = []
            subtitle_cues: list[SubtitleCue] = []
            line_by_id = {line.id: line for line in current_script.lines}
            cursor = 0.0
            for shot in sorted(timeline_shots, key=lambda item: item.sequence):
                take = selected_by_shot[shot.id]
                source_task = source_task_by_shot[shot.id]
                source_window = take.source_window_for_shot(shot.id)
                if source_window is None:
                    source_in_seconds, clip_duration = _timeline_clip_window(shot, take)
                else:
                    if source_window.source_out_seconds > take.duration_seconds + 0.05:
                        raise WorkflowConflictError(
                            f"Take {take.id} does not cover the source window for shot "
                            f"{shot.id}"
                        )
                    source_in_seconds = source_window.source_in_seconds
                    clip_duration = source_window.duration_seconds
                video_clips.append(
                    TimelineVideoClip(
                        id=f"video-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        source_script_version_id=source_task.input_script_version_id,
                        source_bible_version_id=source_task.input_bible_version_id,
                        source_storyboard_document_id=(
                            source_task.storyboard_document_id
                        ),
                        source_storyboard_version_id=(
                            source_task.storyboard_version_id
                        ),
                        media_path=take.media_path,
                        start_seconds=cursor,
                        source_in_seconds=source_in_seconds,
                        duration_seconds=clip_duration,
                        transition_in="none",
                        transition_out=shot.transition_out,
                    )
                )
                if master_audio is None:
                    audio_clips.append(
                        TimelineAudioClip(
                            id=f"audio-{shot.id}-{take.id}",
                            shot_id=shot.id,
                            take_id=take.id,
                            media_path=take.media_path,
                            start_seconds=cursor,
                            source_in_seconds=source_in_seconds,
                            duration_seconds=clip_duration,
                            gain_db=0.0,
                            role=(
                                "MODEL_NATIVE_H3"
                                if take.provider == "minimax"
                                else "MODEL_NATIVE_MOCK_PLACEHOLDER"
                            ),
                        )
                    )
                    lines = [
                        line_by_id[line_id]
                        for line_id in shot.dialogue_line_ids
                        if line_id in line_by_id
                    ]
                    if lines:
                        usable_duration = max(0.5, clip_duration - 1.0)
                        cue_duration = usable_duration / len(lines)
                        for index, line in enumerate(lines):
                            cue_start = cursor + 0.5 + cue_duration * index
                            cue_end = min(
                                cursor + clip_duration - 0.25,
                                cue_start + cue_duration,
                            )
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
            if master_audio is not None:
                _, master_version, binding, audio_path = master_audio
                if master_audio_controls_duration:
                    timeline_duration = (
                        binding.time_origin_seconds + binding.duration_seconds
                    )
                    video_clips = _fit_video_clips_to_duration(
                        video_clips,
                        timeline_duration,
                    )
                else:
                    timeline_duration = cursor
                audio_clips.append(
                    TimelineAudioClip(
                        id=f"master-audio-{master_version.id}",
                        asset_id=binding.asset_id,
                        asset_version_id=binding.asset_version_id,
                        media_path=str(audio_path),
                        start_seconds=binding.time_origin_seconds,
                        source_in_seconds=binding.source_in_seconds,
                        source_out_seconds=binding.source_out_seconds,
                        duration_seconds=binding.duration_seconds,
                        gain_db=0.0,
                        role="MASTER_AUDIO",
                    )
                )
                if timed_text is not None:
                    subtitle_cues.extend(
                        SubtitleCue(
                            id=f"subtitle-{item.id}",
                            timed_text_id=item.id,
                            start_seconds=item.start_seconds,
                            end_seconds=item.end_seconds,
                            text=item.text,
                            speaker=item.speaker,
                            language=item.language,
                        )
                        for item in timed_text.entries
                    )
            else:
                timeline_duration = cursor
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
                duration_seconds=timeline_duration,
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
            now = utc_now()
            changed = False
            for take in self.repository.list_takes_for_shot(project_id, shot_id):
                if shot_id not in take.selected_shot_ids:
                    continue
                take.selected_shot_ids = [
                    selected_shot_id
                    for selected_shot_id in take.selected_shot_ids
                    if selected_shot_id != shot_id
                ]
                take.is_selected = bool(take.selected_shot_ids)
                self.repository.save_take(take)
                if not take.selected_shot_ids:
                    self._invalidate_take_output_slot_if_current_locked(
                        document,
                        take,
                        "The Take is no longer selected for any covered Shot.",
                        updated_at=now,
                    )
                changed = True
            if changed:
                self._invalidate_current_manifests(document)
                document.project.stage = self._task_derived_project_stage(project_id)
                document.project.updated_at = now
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
        expected_kinds = {
            AssetMediaType.IMAGE: H3MediaKind.IMAGE,
            AssetMediaType.VIDEO: H3MediaKind.VIDEO,
            AssetMediaType.AUDIO: H3MediaKind.AUDIO,
        }
        resolved: list[H3MediaInput] = []
        for media in media_inputs:
            if media.asset_version_id is None:
                if media.subject_asset_id is not None:
                    raise WorkflowConflictError(
                        "A subject-linked H3 media input requires an AssetVersion"
                    )
                resolved.append(media)
                continue
            try:
                _asset, version = resolve_current_asset_media(
                    document,
                    asset_version_id=media.asset_version_id,
                    subject_asset_id=media.subject_asset_id,
                )
            except AssetReferenceTruthError as exc:
                raise WorkflowConflictError(
                    str(exc)
                ) from exc
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

    def _h3_candidate_request_fingerprint(
        self,
        *,
        document: ProjectDocument,
        shot_id: str,
        route: VideoGenerationRoute,
        duration_seconds: int,
        prompt: str,
        media_inputs: list[H3MediaInput],
        learning_objective: str | None,
        regression_case_id: str | None,
        candidate_index: int,
        attempt: int,
        storyboard_document_id: str | None = None,
        storyboard_version_id: str | None = None,
        generation_plan_document_id: str | None = None,
        generation_plan_version_id: str | None = None,
        generation_unit_id: str | None = None,
        clip_group_id: str | None = None,
        output_slot_id: str | None = None,
        shot_windows: list[GenerationUnitShotWindow] | None = None,
    ) -> str:
        """Return the stable identity of one exact H3 candidate request."""

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
        if generation_unit_id is not None:
            normalized_request.update(
                {
                    "storyboard_document_id": storyboard_document_id,
                    "storyboard_version_id": storyboard_version_id,
                    "generation_plan_document_id": generation_plan_document_id,
                    "generation_plan_version_id": generation_plan_version_id,
                    "generation_unit_id": generation_unit_id,
                    "clip_group_id": clip_group_id,
                    "output_slot_id": output_slot_id,
                    "shot_windows": [
                        item.model_dump(mode="json")
                        for item in (shot_windows or [])
                    ],
                }
            )
        return hashlib.sha256(
            json.dumps(
                normalized_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

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
        storyboard_document_id: str | None = None,
        storyboard_version_id: str | None = None,
        generation_plan_document_id: str | None = None,
        generation_plan_version_id: str | None = None,
        generation_unit_id: str | None = None,
        clip_group_id: str | None = None,
        output_slot_id: str | None = None,
        shot_windows: list[GenerationUnitShotWindow] | None = None,
    ) -> tuple[GenerationTask, H3ProviderRun, RouteEvidence]:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        run_id = f"h3-run-{uuid4().hex}"
        shot = self._require_shot_in_document(document, shot_id)
        fingerprint = self._h3_candidate_request_fingerprint(
            document=document,
            shot_id=shot_id,
            route=route,
            duration_seconds=duration_seconds,
            prompt=prompt,
            media_inputs=media_inputs,
            learning_objective=learning_objective,
            regression_case_id=regression_case_id,
            candidate_index=candidate_index,
            attempt=attempt,
            storyboard_document_id=storyboard_document_id,
            storyboard_version_id=storyboard_version_id,
            generation_plan_document_id=generation_plan_document_id,
            generation_plan_version_id=generation_plan_version_id,
            generation_unit_id=generation_unit_id,
            clip_group_id=clip_group_id,
            output_slot_id=output_slot_id,
            shot_windows=shot_windows,
        )
        copied_windows = [
            item.model_copy(deep=True) for item in (shot_windows or [])
        ]
        input_shot_revisions = {
            item.shot_id: item.shot_revision for item in copied_windows
        }
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
            input_shot_revisions=input_shot_revisions,
            storyboard_document_id=storyboard_document_id,
            storyboard_version_id=storyboard_version_id,
            generation_plan_document_id=generation_plan_document_id,
            generation_plan_version_id=generation_plan_version_id,
            generation_unit_id=generation_unit_id,
            clip_group_id=clip_group_id,
            output_slot_id=output_slot_id,
            shot_windows=copied_windows,
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
            generation_plan_document_id=generation_plan_document_id,
            generation_plan_version_id=generation_plan_version_id,
            storyboard_document_id=storyboard_document_id,
            storyboard_version_id=storyboard_version_id,
            generation_unit_id=generation_unit_id,
            clip_group_id=clip_group_id,
            output_slot_id=output_slot_id,
            shot_ids=[item.shot_id for item in copied_windows],
            shot_windows=[item.model_copy(deep=True) for item in copied_windows],
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
        storyboard_document_id: str | None = None
        storyboard_version_id: str | None = None
        if document.storyboard_document_id is not None:
            storyboard_document = self._managed_project_document(
                document,
                document.storyboard_document_id,
                STORYBOARD_DOCUMENT_KIND,
            )
            storyboard_version = self._production_document_version(
                storyboard_document
            )
            storyboard = self._parse_storyboard(storyboard_version)
            self._validate_storyboard(document, storyboard)
            storyboard_shot = next(
                (item for item in storyboard.shots if item.id == shot_id),
                None,
            )
            if storyboard_shot is None or storyboard_shot.revision != shot.revision:
                raise WorkflowConflictError(
                    "Generation requires the current exact Storyboard Shot revision"
                )
            storyboard_document_id = storyboard_document.id
            storyboard_version_id = storyboard_version.id
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
            "storyboard_document_id": storyboard_document_id,
            "storyboard_version_id": storyboard_version_id,
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
            storyboard_document_id=storyboard_document_id,
            storyboard_version_id=storyboard_version_id,
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
            shot_id
            for take in takes
            if take.is_selected
            and take.freshness == Freshness.CURRENT
            and _take_is_timeline_eligible(take)
            for shot_id in take.selected_shot_ids
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
