"""Product API for the provider-neutral short-drama workflow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from _routes._errors import HTTPError
from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
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
from short_drama.models import (
    AudioInspectionRequest,
    AudioInspectionResponse,
    AutoDraftRequest,
    AutoDraftResponse,
    AutonomousDraftCreateRequest,
    AutonomousRepeatCreateRequest,
    AutonomousDraftResponse,
    AutonomousRunAdvanceRequest,
    CreateProjectRequest,
    EnqueueRequest,
    ExportRecord,
    ExportRequest,
    LocalReworkRequest,
    OperationKind,
    PlanningRunCreateRequest,
    PlanningRunListResponse,
    PlanningRunResponse,
    ScriptQualityCatalogResponse,
    ScriptQualityHistoryResponse,
    ProjectAggregate,
    ProjectListResponse,
    ReviseScriptLineRequest,
    StatusMessage,
    TaskActionResponse,
    TimelineManifest,
    VideoInspectionRequest,
    VideoInspectionResponse,
    WorkerDrainRequest,
    WorkerDrainResponse,
)
from short_drama.quality_learning import H3CapabilityProfileResponse
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
from short_drama.service import (
    ArtifactNotFoundError,
    ProviderOperationError,
    ProjectNotFoundError,
    WorkflowConflictError,
)
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)


router = APIRouter(prefix="/api/short-drama", tags=["short-drama"])


def _translate_error(error: Exception) -> HTTPError:
    if isinstance(error, (ProjectNotFoundError, ArtifactNotFoundError)):
        return HTTPError(404, str(error.args[0]), code="SHORT_DRAMA_NOT_FOUND")
    if isinstance(error, WorkflowConflictError):
        return HTTPError(409, str(error), code="SHORT_DRAMA_STATE_CONFLICT")
    if isinstance(error, ProviderOperationError):
        return HTTPError(502, str(error), code="SHORT_DRAMA_PROVIDER_FAILED")
    if isinstance(error, MediaGenerationError):
        return HTTPError(500, str(error), code="SHORT_DRAMA_MEDIA_FAILED")
    return HTTPError(500, str(error), code="SHORT_DRAMA_INTERNAL_ERROR")


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
    service: ShortDramaService = Depends(get_short_drama_service),
) -> H3ReconciliationCandidatesResponse:
    try:
        return service.list_h3_reconciliation_candidates()
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


@router.get("/planning-runs", response_model=PlanningRunListResponse)
def list_planning_runs(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> PlanningRunListResponse:
    return service.list_planning_runs()


@router.post("/planning-runs", response_model=PlanningRunResponse)
def create_planning_run(
    request: PlanningRunCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> PlanningRunResponse:
    return service.create_planning_run(request)


@router.get("/planning-runs/{run_id}", response_model=PlanningRunResponse)
def get_planning_run(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> PlanningRunResponse:
    try:
        return service.get_planning_run(run_id)
    except ArtifactNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post("/planning-runs/{run_id}/advance", response_model=PlanningRunResponse)
def advance_planning_run(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> PlanningRunResponse:
    try:
        return service.advance_planning_run(run_id)
    except (ArtifactNotFoundError, WorkflowConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/planning-runs/{run_id}/script-quality",
    response_model=ScriptQualityHistoryResponse,
)
def get_planning_run_script_quality(
    run_id: str,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ScriptQualityHistoryResponse:
    try:
        return service.get_planning_run_script_quality(run_id)
    except ArtifactNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.get("/script-quality", response_model=ScriptQualityCatalogResponse)
def list_script_quality_evaluations(
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ScriptQualityCatalogResponse:
    return service.list_script_quality_evaluations()


@router.post("/projects", response_model=ProjectAggregate)
def create_project(
    request: CreateProjectRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    return service.create_project(request)


@router.post("/projects/autonomous-draft", response_model=AutonomousDraftResponse)
def create_autonomous_draft(
    request: AutonomousDraftCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AutonomousDraftResponse:
    try:
        return service.create_autonomous_draft(request)
    except (WorkflowConflictError, ProviderOperationError, MediaGenerationError) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{source_project_id}/autonomous-repeats",
    response_model=AutonomousDraftResponse,
)
def create_autonomous_repeat(
    source_project_id: str,
    request: AutonomousRepeatCreateRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AutonomousDraftResponse:
    try:
        return service.create_autonomous_repeat(source_project_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/projects/{project_id}/autonomous-runs/{run_id}/advance",
    response_model=AutonomousDraftResponse,
)
def advance_autonomous_run(
    project_id: str,
    run_id: str,
    request: AutonomousRunAdvanceRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AutonomousDraftResponse:
    try:
        return service.advance_autonomous_run(project_id, run_id, request)
    except (
        ProjectNotFoundError,
        ArtifactNotFoundError,
        WorkflowConflictError,
        ProviderOperationError,
        MediaGenerationError,
    ) as exc:
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
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    try:
        return service.select_take(take_id)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError) as exc:
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


@router.post("/projects/{project_id}/auto-draft", response_model=AutoDraftResponse)
def auto_draft(
    project_id: str,
    request: AutoDraftRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> AutoDraftResponse:
    try:
        return service.auto_draft(project_id, request.render_export)
    except (ProjectNotFoundError, ArtifactNotFoundError, WorkflowConflictError, MediaGenerationError) as exc:
        raise _translate_error(exc) from exc
