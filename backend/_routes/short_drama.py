"""Product API for the provider-neutral short-drama workflow."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from _routes._errors import HTTPError
from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
    H3ProviderRunsResponse,
    H3ReconcileRequest,
    H3ReconciliationCandidatesResponse,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.media import MediaGenerationError
from short_drama.models import (
    AutoDraftRequest,
    AutoDraftResponse,
    CreateProjectRequest,
    EnqueueRequest,
    ExportRecord,
    ExportRequest,
    LocalReworkRequest,
    OperationKind,
    ProjectAggregate,
    ProjectListResponse,
    ReviseScriptLineRequest,
    StatusMessage,
    TaskActionResponse,
    TimelineManifest,
    WorkerDrainRequest,
    WorkerDrainResponse,
)
from short_drama.service import (
    ArtifactNotFoundError,
    ProviderOperationError,
    ProjectNotFoundError,
    WorkflowConflictError,
)
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService


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


@router.post("/projects", response_model=ProjectAggregate)
def create_project(
    request: CreateProjectRequest,
    service: ShortDramaService = Depends(get_short_drama_service),
) -> ProjectAggregate:
    return service.create_project(request)


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
