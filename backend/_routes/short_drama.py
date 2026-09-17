"""Product API for the provider-neutral short-drama workflow."""

from __future__ import annotations

from _routes.studio import router as studio_router
from _routes.video_edit import router as video_edit_router
from short_drama.studio_models import StudioTakeSelectRequest

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from _routes._errors import HTTPError
from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
)
from short_drama.change_planner import (
    SemanticChangePlan,
    SemanticChangePlanRequest,
)
from short_drama.change_executor import (
    SemanticChangeApplyRequest,
    SemanticChangeApplyResponse,
)
from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3NotSubmittedResolutionRequest,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProductionRouteExecuteRequest,
    H3ProductionRouteExecuteResponse,
    H3ProductionRoutePlanListResponse,
    H3ProductionRoutePlanRequest,
    H3ProductionRoutePlanResponse,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
    H3ProviderRunsResponse,
    H3ReconcileRequest,
    H3ReconciliationCandidatesResponse,
    H3RouteAssetPreparationUpdateRequest,
    H3RouteAssetPreparationUpdateResponse,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReviewRequest,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
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
from short_drama.media import MediaGenerationError
from short_drama.media_frame_asset import TakeFrameAssetRequest, TakeFrameAssetResponse
from short_drama.models import (
    AudioInspectionRequest,
    AudioInspectionResponse,
    EnqueueRequest,
    EpisodeReviewRequest,
    EpisodeReviewResponse,
    ExportRecord,
    ExportRequest,
    LocalReworkRequest,
    MasterAudioBindingResponse,
    MasterAudioBindRequest,
    MusicAnalysisRequest,
    MusicAnalysisResponse,
    OperationKind,
    ProductionDocumentCreateRequest,
    ProductionDocumentListResponse,
    ProductionDocumentMutationResponse,
    ProductionDocumentReadResponse,
    ProductionDocumentReviseRequest,
    ProjectAggregate,
    ProjectListResponse,
    ReviseScriptLineRequest,
    ReviseScriptLinesRequest,
    StatusMessage,
    TakeComparisonRequest,
    TakeComparisonResponse,
    TaskActionResponse,
    TimelineManifest,
    TimedTextLockResponse,
    TimedTextLockUpdateRequest,
    VideoInspectionRequest,
    VideoInspectionResponse,
    WorkerDrainRequest,
    WorkerDrainResponse,
)
from short_drama.quality_learning import H3CapabilityProfileResponse
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
    ProductionManagerError,
)
from short_drama.production_contracts import (
    GenerationPlanCompileRequest,
    GenerationPlanMutationResponse,
    GenerationPlanReadResponse,
    OutputSlotBindRequest,
    OutputSlotListResponse,
    OutputSlotMutationResponse,
    OutputSlotReadResponse,
    ProductionSpecCreateRequest,
    ProductionSpecMutationResponse,
    ProductionSpecReadResponse,
    ProductionSpecReplaceRequest,
    StoryboardCreateRequest,
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupReplaceRequest,
    StoryboardDraftLedgerCompleteRequest,
    StoryboardDraftLedgerSegmentAppendRequest,
    StoryboardDraftMutationResponse,
    StoryboardDraftOutlineCompleteRequest,
    StoryboardDraftOutlineSegmentAppendRequest,
    StoryboardDraftVerificationRequest,
    StoryboardMutationResponse,
    StoryboardPatchRequest,
    StoryboardReadResponse,
    StoryboardReplaceRequest,
)
from short_drama.agent_runtime import WorkOrderProgressEntry
from short_drama.production_board import ProductionBoardView
from short_drama.reference_planner import (
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
)
from short_drama.team_executor import TeamExecutionBatch
from short_drama.service import (
    ArtifactNotFoundError,
    ProviderOperationError,
    ProjectNotFoundError,
    WorkflowConflictError,
)
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService
from short_drama.stock_assets import (
    StockAssetImportRequest,
    StockAssetProvider,
    StockAssetSearchRequest,
    StockAssetSearchResponse,
    StockProviderConfigListResponse,
    StockProviderConfigResponse,
    StockProviderConfigUpdate,
)
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)
from short_drama.visual_input_quality import (
    InputCompatibilityRequest,
    InputCompatibilityResponse,
    KeyframeComparisonRequest,
    KeyframeComparisonResponse,
    ReferenceVisualRerankRequest,
    ReferenceVisualRerankResponse,
    VisualInputReportsResponse,
)


router = APIRouter(prefix="/api/short-drama", tags=["short-drama"])


def _translate_error(error: Exception) -> HTTPError:
    if isinstance(error, (ProjectNotFoundError, ArtifactNotFoundError)):
        return HTTPError(404, str(error.args[0]), code="SHORT_DRAMA_NOT_FOUND")
    if isinstance(error, WorkflowConflictError):
        return HTTPError(409, str(error), code="SHORT_DRAMA_STATE_CONFLICT")
    if isinstance(error, ProductionManagerError):
        return HTTPError(409, str(error), code="SHORT_DRAMA_AGENT_CONFLICT")
    if isinstance(error, ProviderOperationError):
        return HTTPError(502, str(error), code="SHORT_DRAMA_PROVIDER_FAILED")
    if isinstance(error, MediaGenerationError):
        return HTTPError(500, str(error), code="SHORT_DRAMA_MEDIA_FAILED")
    return HTTPError(500, str(error), code="SHORT_DRAMA_INTERNAL_ERROR")


@router.get(
    "/agent-runtime/capabilities",
    response_model=AgentCapabilityCatalog,
)
def get_agent_capabilities(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentCapabilityCatalog:
    return service.get_agent_capabilities()


@router.post(
    "/agent-projects",
    response_model=AgentProjectCreateResponse,
)
def create_agent_project(
    request: AgentProjectCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentProjectCreateResponse:
    try:
        return service.create_agent_project(request)
    except (ProductionManagerError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/agent-runs",
    response_model=AgentRunView,
)
def create_agent_run(
    project_id: str,
    request: AgentRunCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentRunView:
    try:
        return service.create_agent_run(project_id, request)
    except ProductionManagerError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/agent-runs",
    response_model=AgentRunListResponse,
)
def list_agent_runs(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentRunListResponse:
    try:
        return service.list_agent_runs(project_id)
    except ProductionManagerError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/agent-runs/{run_id}",
    response_model=AgentRunView,
)
def get_agent_run(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentRunView:
    try:
        return service.get_agent_run(run_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.get(
    "/agent-runs/{run_id}/board",
    response_model=ProductionBoardView,
)
def get_agent_production_board(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionBoardView:
    try:
        return service.get_agent_production_board(run_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-runs/{run_id}/replan",
    response_model=AgentRunView,
)
def replan_agent_run(
    run_id: str,
    request: AgentRunReplanRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentRunView:
    try:
        return service.replan_agent_run(run_id, request)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-runs/{run_id}/advance",
    response_model=AgentAdvanceResponse,
)
def advance_agent_run(
    run_id: str,
    request: AgentRunAdvanceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentAdvanceResponse:
    try:
        return service.advance_agent_run(run_id, request)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-runs/{run_id}/team/advance",
    response_model=TeamExecutionBatch,
)
def advance_agent_team(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TeamExecutionBatch:
    try:
        return service.advance_agent_team(run_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-runs/{run_id}/user-input",
    response_model=AgentRunView,
)
def submit_agent_user_input(
    run_id: str,
    request: AgentUserInputRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentRunView:
    try:
        return service.submit_agent_user_input(run_id, request)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.get(
    "/agent-work-orders/{work_order_id}",
    response_model=AgentWorkOrderView,
)
def get_agent_work_order(
    work_order_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentWorkOrderView:
    try:
        return service.get_agent_work_order(work_order_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-work-orders/{work_order_id}/progress",
    response_model=WorkOrderProgressEntry,
)
def append_agent_work_order_progress(
    work_order_id: str,
    request: AgentWorkOrderProgressRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> WorkOrderProgressEntry:
    try:
        return service.append_agent_work_order_progress(work_order_id, request)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.get(
    "/agent-work-orders/{work_order_id}/progress",
    response_model=AgentWorkOrderProgressListResponse,
)
def list_agent_work_order_progress(
    work_order_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentWorkOrderProgressListResponse:
    try:
        return service.list_agent_work_order_progress(work_order_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-work-orders/{work_order_id}/execute",
    response_model=AgentWorkOrderResultResponse,
)
def execute_agent_work_order(
    work_order_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentWorkOrderResultResponse:
    try:
        return service.execute_agent_work_order(work_order_id)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/agent-work-orders/{work_order_id}/result",
    response_model=AgentWorkOrderResultResponse,
)
def submit_agent_work_order_result(
    work_order_id: str,
    request: AgentWorkOrderResultRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AgentWorkOrderResultResponse:
    try:
        return service.submit_agent_work_order_result(work_order_id, request)
    except (ProductionManagerError, KeyError) as exc:
        raise _translate_error(ProductionManagerError(str(exc))) from exc


@router.post(
    "/asset-agent/tools/search-assets",
    response_model=AssetCatalogSearchResponse,
)
def search_asset_catalog(
    request: AssetCatalogSearchRequest,
    current_project_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetCatalogSearchResponse:
    try:
        return service.search_asset_catalog(
            request,
            current_project_id=current_project_id,
        )
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/asset-agent/tools/inspect-coverage",
    response_model=AssetCoverageResponse,
)
def inspect_asset_coverage(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetCoverageResponse:
    try:
        return service.inspect_asset_coverage(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/asset-agent/tools/import-asset",
    response_model=AssetMutationResponse,
)
def import_catalog_asset(
    project_id: str,
    request: AssetCatalogImportRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.import_catalog_asset(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/providers/stock-assets",
    response_model=StockProviderConfigListResponse,
)
def get_stock_provider_configs(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StockProviderConfigListResponse:
    return service.get_stock_provider_configs()


@router.post(
    "/providers/stock-assets/{provider}",
    response_model=StockProviderConfigResponse,
)
def update_stock_provider_config(
    provider: StockAssetProvider,
    request: StockProviderConfigUpdate,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StockProviderConfigResponse:
    return service.update_stock_provider_config(provider, request)


@router.post(
    "/asset-agent/tools/search-stock",
    response_model=StockAssetSearchResponse,
)
def search_stock_assets(
    request: StockAssetSearchRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StockAssetSearchResponse:
    try:
        return service.search_stock_assets(request)
    except ProviderOperationError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/asset-agent/tools/import-stock",
    response_model=AssetMutationResponse,
)
def import_stock_asset(
    project_id: str,
    request: StockAssetImportRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.import_stock_asset(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/providers/gpt-image", response_model=ImageProviderConfigResponse)
def get_image_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageProviderConfigResponse:
    return service.get_image_provider_config()


@router.post("/providers/gpt-image", response_model=ImageProviderConfigResponse)
def update_image_provider_config(
    request: ImageProviderConfigUpdate,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageProviderConfigResponse:
    return service.update_image_provider_config(request)


@router.post("/providers/gpt-image/validate", response_model=ImageProviderConfigResponse)
def validate_image_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageProviderConfigResponse:
    try:
        return service.validate_image_provider_credentials()
    except (WorkflowConflictError, ProviderOperationError) as exc:
        raise _translate_error(exc) from exc


@router.get("/providers/gpt-sol", response_model=LanguageProviderConfigResponse)
def get_language_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> LanguageProviderConfigResponse:
    return service.get_language_provider_config()


@router.post("/providers/gpt-sol", response_model=LanguageProviderConfigResponse)
def update_language_provider_config(
    request: LanguageProviderConfigUpdate,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> LanguageProviderConfigResponse:
    return service.update_language_provider_config(request)


@router.post(
    "/providers/gpt-sol/validate",
    response_model=LanguageProviderConfigResponse,
)
def validate_language_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> LanguageProviderConfigResponse:
    try:
        return service.validate_language_provider_credentials()
    except (WorkflowConflictError, ProviderOperationError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/providers/minimax-h3/capability-profile",
    response_model=H3CapabilityProfileResponse,
)
def get_h3_capability_profile(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3CapabilityProfileResponse:
    try:
        return service.get_h3_capability_profile(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/h3/prompt/compile",
    response_model=LanguagePromptCompileResponse,
)
def compile_h3_prompt_with_language_model(
    project_id: str,
    request: LanguagePromptCompileRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> LanguagePromptCompileResponse:
    try:
        return service.compile_h3_prompt_with_language_model(project_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/reference-packs/entries",
    response_model=ReferencePackEntryMutationResponse,
)
def add_reference_pack_entry(
    project_id: str,
    request: ReferencePackEntryCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ReferencePackEntryMutationResponse:
    try:
        return service.add_reference_pack_entry(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.put(
    "/projects/{project_id}/shots/{shot_id}/reference-plan",
    response_model=ProjectAggregate,
)
def update_shot_reference_plan(
    project_id: str,
    shot_id: str,
    request: ShotReferencePlanUpdateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.update_shot_reference_plan(project_id, shot_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/reference-plan/preview",
    response_model=ReferencePlanPreviewResponse,
)
def preview_shot_reference_plan(
    project_id: str,
    shot_id: str,
    request: ReferencePlanPreviewRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ReferencePlanPreviewResponse:
    try:
        return service.preview_shot_reference_plan(project_id, shot_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/keyframe-brief/compile",
    response_model=KeyframeBriefCompileResponse,
)
def compile_keyframe_brief(
    project_id: str,
    shot_id: str,
    request: KeyframeBriefCompileRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> KeyframeBriefCompileResponse:
    try:
        return service.compile_keyframe_brief(project_id, shot_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/agent-tools/rerank-references",
    response_model=ReferenceVisualRerankResponse,
)
def rerank_shot_reference_visuals(
    project_id: str,
    shot_id: str,
    request: ReferenceVisualRerankRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ReferenceVisualRerankResponse:
    try:
        return service.rerank_shot_reference_visuals(project_id, shot_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/agent-tools/compare-keyframes",
    response_model=KeyframeComparisonResponse,
)
def compare_shot_keyframes(
    project_id: str,
    shot_id: str,
    request: KeyframeComparisonRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> KeyframeComparisonResponse:
    try:
        return service.compare_shot_keyframes(project_id, shot_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/agent-tools/check-input-compatibility",
    response_model=InputCompatibilityResponse,
)
def check_shot_input_compatibility(
    project_id: str,
    shot_id: str,
    request: InputCompatibilityRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> InputCompatibilityResponse:
    try:
        return service.check_shot_input_compatibility(project_id, shot_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/agent-tools/visual-input-reports",
    response_model=VisualInputReportsResponse,
)
def list_visual_input_reports(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> VisualInputReportsResponse:
    try:
        return service.list_visual_input_reports(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/director-advice",
    response_model=DirectorAdviceResponse,
)
def get_director_advice(
    project_id: str,
    shot_id: str,
    request: DirectorAdviceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> DirectorAdviceResponse:
    try:
        return service.get_director_advice(project_id, shot_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/director-loop",
    response_model=DirectorLoopResponse,
)
def run_director_loop(
    project_id: str,
    shot_id: str,
    request: DirectorAdviceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> DirectorLoopResponse:
    try:
        return service.run_director_loop(project_id, shot_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.get("/providers/minimax-h3", response_model=H3ProviderConfigResponse)
def get_h3_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProviderConfigResponse:
    return service.get_h3_config()


@router.post("/providers/minimax-h3", response_model=H3ProviderConfigResponse)
def update_h3_provider_config(
    request: H3ProviderConfigUpdate,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProviderConfigResponse:
    return service.update_h3_config(request)


@router.post("/providers/minimax-h3/validate", response_model=H3ProviderConfigResponse)
def validate_h3_provider_config(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProviderConfigResponse:
    try:
        return service.validate_h3_credentials()
    except (WorkflowConflictError, ProviderOperationError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/providers/minimax-h3/reconciliation-candidates",
    response_model=H3ReconciliationCandidatesResponse,
)
def list_h3_reconciliation_candidates(
    offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=100),
    max_pages: int = Query(default=10, ge=1, le=10),
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ReconciliationCandidatesResponse:
    try:
        return service.list_h3_reconciliation_candidates(offset=offset, page_size=page_size, max_pages=max_pages)
    except (WorkflowConflictError, ProviderOperationError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/h3/preview",
    response_model=H3CompilePreviewResponse,
)
def preview_h3_probe(
    project_id: str,
    request: H3ProbeRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3CompilePreviewResponse:
    try:
        return service.preview_h3_probe(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/h3/production-route-plans",
    response_model=H3ProductionRoutePlanResponse,
)
def compile_h3_production_route_plan(
    project_id: str,
    request: H3ProductionRoutePlanRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProductionRoutePlanResponse:
    try:
        return service.compile_h3_production_route_plan(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/h3/production-route-plans",
    response_model=H3ProductionRoutePlanListResponse,
)
def list_h3_production_route_plans(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProductionRoutePlanListResponse:
    try:
        return service.list_h3_production_route_plans(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/h3/production-route-plans/{plan_id}/"
    "asset-preparations/{preparation_id}",
    response_model=H3RouteAssetPreparationUpdateResponse,
)
def update_h3_route_asset_preparation(
    project_id: str,
    plan_id: str,
    preparation_id: str,
    request: H3RouteAssetPreparationUpdateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3RouteAssetPreparationUpdateResponse:
    try:
        return service.update_h3_route_asset_preparation(
            project_id,
            plan_id,
            preparation_id,
            request,
        )
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/h3/production-route-plans/{plan_id}/execute",
    response_model=H3ProductionRouteExecuteResponse,
)
def execute_h3_production_route_plan(
    project_id: str,
    plan_id: str,
    request: H3ProductionRouteExecuteRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProductionRouteExecuteResponse:
    try:
        return service.execute_h3_production_route_plan(project_id, plan_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/director-repair",
    response_model=ProjectAggregate,
)
def decide_director_repair(
    project_id: str,
    shot_id: str,
    request: DirectorRepairDecisionRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.decide_director_repair(project_id, shot_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/h3/probes", response_model=H3ProbeResponse)
def enqueue_h3_probe(
    project_id: str,
    request: H3ProbeRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProbeResponse:
    try:
        return service.enqueue_h3_probe(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/projects/{project_id}/h3/runs", response_model=H3ProviderRunsResponse)
def list_h3_provider_runs(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ProviderRunsResponse:
    try:
        return service.list_h3_provider_runs(project_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/tasks/{task_id}/h3/resolve-submit",
    response_model=H3TaskActionResponse,
)
def resolve_h3_submission(
    task_id: str,
    request: H3ReconcileRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3TaskActionResponse:
    try:
        return service.resolve_h3_submission(task_id, request.provider_task_id)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/tasks/{task_id}/h3/resolve-not-submitted",
    response_model=H3TaskActionResponse,
)
def resolve_h3_not_submitted(
    task_id: str,
    request: H3NotSubmittedResolutionRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3TaskActionResponse:
    try:
        return service.resolve_h3_not_submitted(task_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/takes/{take_id}/h3/review", response_model=H3TakeReviewResponse)
def review_h3_take(
    take_id: str,
    request: H3TakeReviewRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3TakeReviewResponse:
    try:
        return service.review_h3_take(take_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(service: ShortDramaService = Depends(get_short_drama_service)) -> ProjectListResponse:
    return service.list_projects()


@router.post(
    "/projects/{project_id}/agent-tools/plan-change",
    response_model=SemanticChangePlan,
)
def preview_semantic_change(
    project_id: str,
    request: SemanticChangePlanRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> SemanticChangePlan:
    try:
        return service.preview_semantic_change(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/agent-tools/apply-change",
    response_model=SemanticChangeApplyResponse,
)
def apply_semantic_change(
    project_id: str,
    request: SemanticChangeApplyRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> SemanticChangeApplyResponse:
    try:
        return service.apply_semantic_change(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/assets", response_model=AssetMutationResponse)
def create_asset(
    project_id: str,
    request: AssetCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.create_asset(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/versions/import-local",
    response_model=AssetMutationResponse,
)
def import_local_asset_version(
    project_id: str,
    asset_id: str,
    request: AssetImportLocalRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.import_local_asset_version(project_id, asset_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/versions/import-url",
    response_model=AssetMutationResponse,
)
def import_url_asset_version(
    project_id: str,
    asset_id: str,
    request: AssetImportUrlRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.import_url_asset_version(project_id, asset_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.put(
    "/projects/{project_id}/characters/{character_id}/voice-reference",
    response_model=VoiceReferenceMutationResponse,
)
def update_character_voice_reference(
    project_id: str,
    character_id: str,
    request: VoiceReferenceUpdateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> VoiceReferenceMutationResponse:
    try:
        return service.update_character_voice_reference(
            project_id,
            character_id,
            request,
        )
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/characters/{character_id}/voice-reference/upload",
    response_model=VoiceReferenceMutationResponse,
)
async def upload_character_voice_reference(
    project_id: str,
    character_id: str,
    file: UploadFile = File(...),
    descriptor: str = Form(...),
    service: ShortDramaService = Depends(get_short_drama_service),
) -> VoiceReferenceMutationResponse:
    try:
        return service.upload_character_voice_reference(
            project_id,
            character_id,
            filename=file.filename or "voice-reference.webm",
            descriptor=descriptor,
            data=await file.read(),
        )
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/versions/{version_id}/review",
    response_model=AssetMutationResponse,
)
def review_asset_version(
    project_id: str,
    asset_id: str,
    version_id: str,
    request: AssetReviewRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AssetMutationResponse:
    try:
        return service.review_asset_version(
            project_id,
            asset_id,
            version_id,
            request,
        )
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/image-tasks",
    response_model=ImageTaskResponse,
)
def enqueue_image_task(
    project_id: str,
    asset_id: str,
    request: ImageTaskCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.enqueue_image_task(project_id, asset_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/image-tasks",
    response_model=ImageTaskListResponse,
)
def list_image_tasks(
    project_id: str,
    asset_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskListResponse:
    try:
        return service.list_image_tasks(project_id, asset_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post("/image-tasks/{task_id}/advance", response_model=ImageTaskResponse)
def advance_image_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.advance_image_task(task_id)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/image-tasks/{task_id}/cancel", response_model=ImageTaskResponse)
def cancel_image_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.cancel_image_task(task_id)
    except (ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/image-tasks/{task_id}/retry", response_model=ImageTaskResponse)
def retry_image_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.retry_image_task(task_id)
    except (ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/image-tasks/{task_id}/resolve-not-submitted",
    response_model=ImageTaskResponse,
)
def resolve_image_not_submitted(
    task_id: str,
    request: ImageNotSubmittedResolutionRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.resolve_image_not_submitted(task_id, request)
    except (ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/image-tasks/{task_id}/recover-output", response_model=ImageTaskResponse)
def recover_image_task_output(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ImageTaskResponse:
    try:
        return service.recover_image_task_output(task_id)
    except (ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/fixtures/dont-trust-me", response_model=ProjectAggregate)
def initialize_dont_trust_me_fixture(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    return service.initialize_fixture()


@router.get("/projects/{project_id}", response_model=ProjectAggregate)
def get_project(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.get_project(project_id)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-spec",
    response_model=ProductionSpecMutationResponse,
)
def create_production_spec(
    project_id: str,
    request: ProductionSpecCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionSpecMutationResponse:
    try:
        return service.create_production_spec(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/production-spec",
    response_model=ProductionSpecReadResponse,
)
def get_production_spec(
    project_id: str,
    version_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionSpecReadResponse:
    try:
        return service.get_production_spec(project_id, version_id=version_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-spec/replacements",
    response_model=ProductionSpecMutationResponse,
)
def replace_production_spec(
    project_id: str,
    request: ProductionSpecReplaceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionSpecMutationResponse:
    try:
        return service.replace_production_spec(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts",
    response_model=StoryboardDraftMutationResponse,
)
def begin_storyboard_draft(
    project_id: str,
    request: StoryboardDraftBeginRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        return service.begin_storyboard_draft(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/ledger/segments",
    response_model=StoryboardDraftMutationResponse,
)
def append_storyboard_ledger_segment(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftLedgerSegmentAppendRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.append_storyboard_ledger_segment(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/ledger/complete",
    response_model=StoryboardDraftMutationResponse,
)
def complete_storyboard_ledger(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftLedgerCompleteRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.complete_storyboard_ledger(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/outline/segments",
    response_model=StoryboardDraftMutationResponse,
)
def append_storyboard_outline_segment(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftOutlineSegmentAppendRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.append_storyboard_outline_segment(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/outline/complete",
    response_model=StoryboardDraftMutationResponse,
)
def complete_storyboard_outline(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftOutlineCompleteRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.complete_storyboard_outline(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/groups",
    response_model=StoryboardDraftMutationResponse,
)
def append_storyboard_group(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftAppendRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.append_storyboard_group(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/groups/{clip_group_id}/replacement",
    response_model=StoryboardDraftMutationResponse,
)
def replace_storyboard_draft_group(
    project_id: str,
    draft_document_id: str,
    clip_group_id: str,
    request: StoryboardDraftGroupReplaceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        if clip_group_id != request.replaced_clip_group_id:
            raise WorkflowConflictError(
                "Storyboard ClipGroup ID in the path does not match the request body"
            )
        return service.replace_storyboard_draft_group(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/verification",
    response_model=StoryboardDraftMutationResponse,
)
def record_storyboard_draft_verification(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftVerificationRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardDraftMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.record_storyboard_draft_verification(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard-drafts/{draft_document_id}/finalize",
    response_model=StoryboardMutationResponse,
)
def finalize_storyboard(
    project_id: str,
    draft_document_id: str,
    request: StoryboardDraftFinalizeRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardMutationResponse:
    try:
        if draft_document_id != request.draft_document_id:
            raise WorkflowConflictError(
                "Storyboard draft document ID in the path does not match the request body"
            )
        return service.finalize_storyboard(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard",
    response_model=StoryboardMutationResponse,
)
def create_storyboard(
    project_id: str,
    request: StoryboardCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardMutationResponse:
    try:
        return service.create_storyboard(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/storyboard",
    response_model=StoryboardReadResponse,
)
def get_storyboard(
    project_id: str,
    version_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardReadResponse:
    try:
        return service.get_storyboard(project_id, version_id=version_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard/replacements",
    response_model=StoryboardMutationResponse,
)
def replace_storyboard(
    project_id: str,
    request: StoryboardReplaceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardMutationResponse:
    try:
        return service.replace_storyboard(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/storyboard/patches",
    response_model=StoryboardMutationResponse,
)
def patch_storyboard(
    project_id: str,
    request: StoryboardPatchRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StoryboardMutationResponse:
    try:
        return service.patch_storyboard(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/generation-plan/compile",
    response_model=GenerationPlanMutationResponse,
)
def compile_generation_plan(
    project_id: str,
    request: GenerationPlanCompileRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> GenerationPlanMutationResponse:
    try:
        return service.compile_generation_plan(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/generation-plan",
    response_model=GenerationPlanReadResponse,
)
def get_generation_plan(
    project_id: str,
    version_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> GenerationPlanReadResponse:
    try:
        return service.get_generation_plan(project_id, version_id=version_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/output-slots",
    response_model=OutputSlotListResponse,
)
def list_output_slots(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> OutputSlotListResponse:
    try:
        return service.list_output_slots(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/output-slots/{slot_id}",
    response_model=OutputSlotReadResponse,
)
def get_output_slot(
    project_id: str,
    slot_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> OutputSlotReadResponse:
    try:
        return service.get_output_slot(project_id, slot_id)
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/output-slots/bind",
    response_model=OutputSlotMutationResponse,
)
def bind_output_slot(
    project_id: str,
    request: OutputSlotBindRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> OutputSlotMutationResponse:
    try:
        return service.bind_output_slot(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-documents",
    response_model=ProductionDocumentMutationResponse,
)
def create_production_document(
    project_id: str,
    request: ProductionDocumentCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionDocumentMutationResponse:
    try:
        return service.create_production_document(project_id, request)
    except (ProjectNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/production-documents",
    response_model=ProductionDocumentListResponse,
)
def list_production_documents(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionDocumentListResponse:
    try:
        return service.list_production_documents(project_id)
    except ProjectNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/projects/{project_id}/production-documents/{production_document_id}",
    response_model=ProductionDocumentReadResponse,
)
def get_production_document(
    project_id: str,
    production_document_id: str,
    version_id: str | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionDocumentReadResponse:
    try:
        return service.get_production_document(
            project_id,
            production_document_id,
            version_id=version_id,
        )
    except (ProjectNotFoundError, ArtifactNotFoundError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-documents/{production_document_id}/revisions",
    response_model=ProductionDocumentMutationResponse,
)
def revise_production_document(
    project_id: str,
    production_document_id: str,
    request: ProductionDocumentReviseRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProductionDocumentMutationResponse:
    try:
        return service.revise_production_document(
            project_id,
            production_document_id,
            request,
        )
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/audio/master-binding",
    response_model=MasterAudioBindingResponse,
)
def bind_master_audio(
    project_id: str,
    request: MasterAudioBindRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> MasterAudioBindingResponse:
    try:
        return service.bind_master_audio(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/audio/timed-text-lock",
    response_model=TimedTextLockResponse,
)
def update_timed_text_lock(
    project_id: str,
    request: TimedTextLockUpdateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TimedTextLockResponse:
    try:
        return service.update_timed_text_lock(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/audio/analyze-music",
    response_model=MusicAnalysisResponse,
)
def analyze_music(
    project_id: str,
    request: MusicAnalysisRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> MusicAnalysisResponse:
    try:
        return service.analyze_music(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.delete("/projects/{project_id}", response_model=StatusMessage)
def delete_project(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> StatusMessage:
    if not service.delete_project(project_id):
        raise HTTPError(404, project_id, code="SHORT_DRAMA_NOT_FOUND")
    return StatusMessage(status="ok", message="Project metadata deleted")


@router.post("/projects/{project_id}/enqueue", response_model=ProjectAggregate)
def enqueue_generation(
    project_id: str,
    request: EnqueueRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.enqueue(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/worker/drain", response_model=WorkerDrainResponse)
def drain_worker(
    request: WorkerDrainRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> WorkerDrainResponse:
    try:
        return service.drain(request.project_id, request.max_steps)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/tasks/{task_id}/advance", response_model=TaskActionResponse)
def advance_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    try:
        task = service.advance_task(task_id)
        return TaskActionResponse(task=task, project=service.get_project(task.project_id))
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/tasks/{task_id}/cancel", response_model=TaskActionResponse)
def cancel_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    try:
        task = service.cancel_task(task_id)
        return TaskActionResponse(task=task, project=service.get_project(task.project_id))
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/tasks/{task_id}/retry", response_model=TaskActionResponse)
def retry_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    try:
        task = service.retry_task(task_id)
        return TaskActionResponse(task=task, project=service.get_project(task.project_id))
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/tasks/{task_id}/reconcile", response_model=TaskActionResponse)
def reconcile_task(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    try:
        task = service.reconcile_task(task_id)
        return TaskActionResponse(task=task, project=service.get_project(task.project_id))
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/tasks/{task_id}/recover-output", response_model=TaskActionResponse)
def recover_task_output(
    task_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    try:
        task = service.recover_task_output(task_id)
        return TaskActionResponse(task=task, project=service.get_project(task.project_id))
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/takes/{take_id}/select", response_model=ProjectAggregate)
def select_take(
    take_id: str,
    request: StudioTakeSelectRequest | None = None,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        selection = request or StudioTakeSelectRequest()
        return service.select_take(take_id, shot_ids=selection.shot_ids, clip_ids=selection.clip_ids,
            expected_editorial_revision=selection.expected_editorial_revision,
            source_in_seconds=selection.source_in_seconds, source_out_seconds=selection.source_out_seconds, audio_policy=selection.audio_policy)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/agent-tools/extract-frame", response_model=TakeFrameAssetResponse)
def extract_take_frame(
    project_id: str,
    request: TakeFrameAssetRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TakeFrameAssetResponse:
    try:
        return service.extract_take_frame(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError, MediaGenerationError) as exc:
        raise _translate_error(exc) from exc


@router.post("/takes/{take_id}/inspect-video", response_model=VideoInspectionResponse)
def inspect_take_video(
    take_id: str,
    request: VideoInspectionRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> VideoInspectionResponse:
    try:
        return service.inspect_take_video(take_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/agent-tools/compare-takes",
    response_model=TakeComparisonResponse,
)
def compare_takes(
    project_id: str,
    request: TakeComparisonRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TakeComparisonResponse:
    try:
        return service.compare_takes(project_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/inspect-audio",
    response_model=AudioInspectionResponse,
)
def inspect_project_audio(
    project_id: str,
    request: AudioInspectionRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AudioInspectionResponse:
    try:
        return service.inspect_current_manifest_audio(project_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/review-episode",
    response_model=EpisodeReviewResponse,
)
def review_project_episode(
    project_id: str,
    request: EpisodeReviewRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> EpisodeReviewResponse:
    try:
        return service.review_current_episode(project_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/script/revise-line", response_model=ProjectAggregate)
def revise_script_line(
    project_id: str,
    request: ReviseScriptLineRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.revise_script_line(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/script/revise-lines", response_model=ProjectAggregate)
def revise_script_lines(
    project_id: str,
    request: ReviseScriptLinesRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.revise_script_lines(project_id, request)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


def _enqueue_rework(
    *,
    project_id: str,
    shot_id: str,
    operation: OperationKind,
    request: LocalReworkRequest,
    service: ShortDramaService,
) -> TaskActionResponse:
    try:
        task = service.enqueue_local_rework(
            project_id,
            shot_id,
            operation,
            request,
        )
        return TaskActionResponse(task=task, project=service.get_project(project_id))
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/shots/{shot_id}/retake", response_model=TaskActionResponse)
def enqueue_retake(
    project_id: str,
    shot_id: str,
    request: LocalReworkRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    return _enqueue_rework(
        project_id=project_id,
        shot_id=shot_id,
        operation=OperationKind.RETAKE,
        request=request,
        service=service,
    )


@router.post("/projects/{project_id}/shots/{shot_id}/extend", response_model=TaskActionResponse)
def enqueue_extend(
    project_id: str,
    shot_id: str,
    request: LocalReworkRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    return _enqueue_rework(
        project_id=project_id,
        shot_id=shot_id,
        operation=OperationKind.EXTEND,
        request=request,
        service=service,
    )


@router.post("/projects/{project_id}/shots/{shot_id}/gap-fill", response_model=TaskActionResponse)
def enqueue_gap_fill(
    project_id: str,
    shot_id: str,
    request: LocalReworkRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TaskActionResponse:
    return _enqueue_rework(
        project_id=project_id,
        shot_id=shot_id,
        operation=OperationKind.GAP_FILL,
        request=request,
        service=service,
    )


@router.post("/projects/{project_id}/manifest/rebuild", response_model=TimelineManifest)
def rebuild_manifest(
    project_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> TimelineManifest:
    try:
        return service.rebuild_manifest(project_id)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/export", response_model=ExportRecord)
def export_project(
    project_id: str,
    request: ExportRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ExportRecord:
    try:
        return service.export_project(project_id, request.rebuild_manifest)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError, MediaGenerationError) as exc:
        raise _translate_error(exc) from exc


router.include_router(studio_router)
router.include_router(video_edit_router)
