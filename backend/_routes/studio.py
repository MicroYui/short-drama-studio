"""Thin product workspace endpoints; content remains in ProjectDocument."""

from collections.abc import Callable
from collections.abc import AsyncIterator
import asyncio
from pathlib import Path
from uuid import uuid4
import shutil
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse, FileResponse

from short_drama.deps import get_short_drama_service
from short_drama.models import ProjectAggregate, AssetKind
from short_drama.studio_media import resolve_media_path, import_studio_media, select_imported_video
from short_drama.studio_trace_export import export_studio_trace
from short_drama.service import ShortDramaService
from short_drama.studio_models import (
    StudioEpisodeCreate, StudioEpisodes, StudioMutationResult,
    StudioRename, StudioSceneSave, StudioShotSave, StudioTimelineSave,
    StudioProjectCreate, StudioMessage, StudioJob, StudioJobControl, StudioActivity,
    StudioShotMediaSelect,
    StudioPreferences,
    StudioEditorial, StudioExport, StudioExportRequest, StudioExports,
    StudioChangeSet, StudioChangeDecision, StudioChangeResult, StudioChanges,
)
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_comfy import ComfyConfig, ComfyConfigUpdate, ComfyRun, ComfyRunRequest, comfy_config, run_comfy, get_comfy_run
from short_drama.studio_methods import StudioMethodDetail, method_detail
from short_drama.audio_resources import AudioResources, AudioResourcePrepare, audio_resource_manager
from short_drama.studio_media_recovery import StudioMediaUsage, StudioMediaRelinkResult, media_usage, relink_media
from short_drama.studio_project_package import (
    StudioProjectPackage, StudioProjectPackageImport, create_project_package, import_project_package, project_package_path,
)
from short_drama.studio_package_models import StudioProjectTransfer, StudioProjectTransfers
from short_drama.studio_project_transfers import (
    start_project_collection, start_project_import, get_project_transfer, list_project_transfers, cancel_project_transfer,
)

router = APIRouter(prefix="/studio", tags=["studio"])
StudioService = Annotated[ShortDramaService, Depends(get_short_drama_service)]
T = TypeVar("T")


@router.get("/methods/{skill_id}", response_model=StudioMethodDetail)
def read_method(skill_id: str) -> StudioMethodDetail:
    return _execute(lambda: method_detail(skill_id))


@router.get("/comfyui", response_model=ComfyConfig)
def read_comfy_config(service: StudioService) -> ComfyConfig:
    return _execute(lambda: comfy_config(service))


@router.get("/audio-resources", response_model=AudioResources)
def read_audio_resources(service: StudioService) -> AudioResources:
    manager = audio_resource_manager(service.audio_semantic_service)
    return _execute(manager.status) if manager else AudioResources(
        supported=False, message="当前服务未配置本地声音分析。播放、剪辑和导出可继续使用。")


@router.post("/audio-resources/prepare", response_model=AudioResources, status_code=202)
def prepare_audio_resource(request: AudioResourcePrepare, service: StudioService) -> AudioResources:
    manager = audio_resource_manager(service.audio_semantic_service)
    if manager is None:
        raise HTTPException(422, "当前服务未配置可准备的本地声音资源")
    return _execute(lambda: manager.prepare(request))


@router.put("/comfyui", response_model=ComfyConfig)
def update_comfy_config(request: ComfyConfigUpdate, service: StudioService) -> ComfyConfig:
    return _execute(lambda: comfy_config(service, request))


@router.post("/projects/{project_id}/workflows", response_model=ComfyRun)
def start_comfy_workflow(project_id: str, request: ComfyRunRequest, service: StudioService) -> ComfyRun:
    return _execute(lambda: run_comfy(service, project_id, request))


@router.get("/projects/{project_id}/workflows/{workflow_id}", response_model=ComfyRun)
def read_comfy_workflow(project_id: str, workflow_id: str, service: StudioService) -> ComfyRun:
    return _execute(lambda: get_comfy_run(service, project_id, workflow_id))


def _execute(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, f"未找到内容：{exc.args[0]}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except StudioContentConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}", response_model=ProjectAggregate)
def current_project(project_id: str, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.current_project(project_id))


@router.get("/projects/{project_id}/changes", response_model=list[StudioChangeSet])
def read_changes(project_id: str, service: StudioService) -> list[StudioChangeSet]:
    from short_drama.studio_changes import read_changes as read_records
    return _execute(lambda: read_records(service, project_id))


@router.get("/projects/{project_id}/changes/page", response_model=StudioChanges)
def read_change_page(project_id: str, service: StudioService, limit: int = Query(default=20, ge=1, le=100),
                     before: str | None = Query(default=None, max_length=1000), pending_only: bool = False) -> StudioChanges:
    from short_drama.studio_changes import page_changes
    return _execute(lambda: page_changes(service, project_id, limit=limit, before=before, pending_only=pending_only))


@router.post("/projects/{project_id}/changes/{change_id}/items/{item_id}", response_model=StudioChangeResult)
def decide_text_change(project_id: str, change_id: str, item_id: str, request: StudioChangeDecision, service: StudioService) -> StudioChangeResult:
    from short_drama.studio_changes import decide_change
    return _execute(lambda: decide_change(service, project_id, change_id, item_id, request))


@router.post("/projects", response_model=ProjectAggregate)
def create_project(request: StudioProjectCreate, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.create_project(request))


@router.post("/projects/import-package", response_model=StudioProjectPackageImport)
def import_portable_project(service: StudioService, file: UploadFile = File(...)) -> StudioProjectPackageImport:
    folder = service.workspace_root / "package-uploads"
    folder.mkdir(parents=True, exist_ok=True)
    uploaded = folder / f"{uuid4().hex}.zip"
    try:
        with uploaded.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        return _execute(lambda: import_project_package(service, uploaded))
    finally:
        uploaded.unlink(missing_ok=True)


@router.post("/projects/import-package-jobs", response_model=StudioProjectTransfer, status_code=202)
def start_portable_project_import(service: StudioService, file: UploadFile = File(...)) -> StudioProjectTransfer:
    # The upload must finish first. The background task then owns this file,
    # independent of the page or connection that initiated it.
    folder = service.workspace_root / "package-uploads"
    folder.mkdir(parents=True, exist_ok=True)
    uploaded = folder / f"{uuid4().hex}.zip"
    try:
        with uploaded.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        return _execute(lambda: start_project_import(service, uploaded, file.filename or "项目包.zip"))
    except Exception:
        uploaded.unlink(missing_ok=True)
        raise


@router.post("/projects/{project_id}/package-jobs", response_model=StudioProjectTransfer, status_code=202)
def start_portable_project_collection(project_id: str, service: StudioService) -> StudioProjectTransfer:
    return _execute(lambda: start_project_collection(service, project_id))


@router.get("/project-transfers", response_model=StudioProjectTransfers)
def read_project_transfers(service: StudioService, limit: int = Query(default=20, ge=1, le=100),
                           before: str | None = Query(default=None, max_length=1000)) -> StudioProjectTransfers:
    return _execute(lambda: list_project_transfers(service, limit=limit, before=before))


@router.get("/project-transfers/{transfer_id}", response_model=StudioProjectTransfer)
def read_project_transfer(transfer_id: str, service: StudioService) -> StudioProjectTransfer:
    return _execute(lambda: get_project_transfer(service, transfer_id))


@router.post("/project-transfers/{transfer_id}/cancel", response_model=StudioProjectTransfer)
def cancel_portable_project_transfer(transfer_id: str, service: StudioService) -> StudioProjectTransfer:
    return _execute(lambda: cancel_project_transfer(service, transfer_id))


@router.post("/projects/{project_id}/package", response_model=StudioProjectPackage)
def collect_portable_project(project_id: str, service: StudioService) -> StudioProjectPackage:
    return _execute(lambda: create_project_package(service, project_id))


@router.get("/projects/{project_id}/package/{package_id}")
def download_portable_project(project_id: str, package_id: str, service: StudioService) -> FileResponse:
    path = _execute(lambda: project_package_path(service, project_id, package_id))
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.get("/projects/{project_id}/activity", response_model=StudioActivity)
def activity(project_id: str, service: StudioService, after: int = 0) -> StudioActivity:
    return _execute(lambda: service.studio_runtime.activity(project_id, after))


@router.post("/projects/{project_id}/messages", response_model=StudioJob)
def send_message(project_id: str, request: StudioMessage, service: StudioService) -> StudioJob:
    return _execute(lambda: service.studio_runtime.send(project_id, request))


@router.post("/jobs/{job_id}/control", response_model=StudioJob)
def control_job(job_id: str, request: StudioJobControl, service: StudioService) -> StudioJob:
    return _execute(lambda: service.studio_runtime.control(job_id, request))


@router.get("/projects/{project_id}/events")
async def events(project_id: str, request: Request, service: StudioService, after: int = 0) -> StreamingResponse:
    _execute(lambda: service.studio.episodes(project_id))

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            entries = await asyncio.to_thread(service.repository.list_studio_events, project_id, after=cursor)
            for entry in entries:
                yield f"id: {entry.id}\ndata: {entry.model_dump_json()}\n\n"
                cursor = entry.id
            if not entries:
                yield ": connected\n\n"
                await asyncio.sleep(1.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/media")
def media(path: str, service: StudioService, project_id: str | None = None) -> FileResponse:
    resolved = _execute(lambda: resolve_media_path(service, path, project_id))
    return FileResponse(resolved, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/projects/{project_id}/trace")
def trace_export(project_id: str, service: StudioService) -> FileResponse:
    path = _execute(lambda: export_studio_trace(service, project_id))
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/projects/{project_id}/media", response_model=ProjectAggregate)
def upload_media(project_id: str, service: StudioService,
                 file: Annotated[UploadFile, File()], name: Annotated[str, Form()] = "导入素材",
                 kind: Annotated[AssetKind, Form()] = AssetKind.REFERENCE,
                 asset_id: Annotated[str | None, Form()] = None,
                 replace_version_id: Annotated[str | None, Form()] = None) -> ProjectAggregate:
    _execute(lambda: service.studio.episodes(project_id))
    folder = service.workspace_root / project_id / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    uploaded = folder / f"{uuid4().hex}{Path(file.filename or '').suffix.lower()}"
    try:
        with uploaded.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        return _execute(lambda: import_studio_media(service, project_id, uploaded, name=name, kind=kind, asset_id=asset_id, replace_version_id=replace_version_id))
    finally:
        uploaded.unlink(missing_ok=True)


@router.get("/projects/{project_id}/media/usage", response_model=StudioMediaUsage)
def read_media_usage(project_id: str, service: StudioService, asset_id: str | None = None,
                     asset_version_id: str | None = None, path: str | None = None) -> StudioMediaUsage:
    return _execute(lambda: media_usage(service, project_id, asset_id=asset_id, asset_version_id=asset_version_id, path=path))


@router.post("/projects/{project_id}/media/relink", response_model=StudioMediaRelinkResult)
def restore_media(project_id: str, service: StudioService, file: Annotated[UploadFile, File()],
                  old_path: Annotated[str, Form()]) -> StudioMediaRelinkResult:
    _execute(lambda: service.studio.episodes(project_id))
    folder = service.workspace_root / project_id / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    uploaded = folder / f"{uuid4().hex}{Path(file.filename or '').suffix.lower()}"
    try:
        with uploaded.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        return _execute(lambda: relink_media(service, project_id, old_path, uploaded))
    finally:
        uploaded.unlink(missing_ok=True)


@router.get("/projects/{project_id}/episodes", response_model=StudioEpisodes)
def list_episodes(project_id: str, service: StudioService) -> StudioEpisodes:
    return _execute(lambda: service.studio.episodes(project_id))


@router.post("/projects/{project_id}/episodes", response_model=ProjectAggregate)
def create_episode(project_id: str, request: StudioEpisodeCreate, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.create_episode(project_id, request))


@router.patch("/projects/{project_id}/name", response_model=ProjectAggregate)
def rename_project(project_id: str, request: StudioRename, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.rename(project_id, request))


@router.put("/projects/{project_id}/preferences", response_model=ProjectAggregate)
def preferences(project_id: str, request: StudioPreferences, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.preferences(project_id, request))


@router.put("/projects/{project_id}/scene", response_model=StudioMutationResult)
def save_scene(project_id: str, request: StudioSceneSave, service: StudioService) -> StudioMutationResult:
    return _execute(lambda: service.studio.save_scene(project_id, request))


@router.put("/projects/{project_id}/shots/{shot_id}", response_model=StudioMutationResult)
def save_shot(project_id: str, shot_id: str, request: StudioShotSave, service: StudioService) -> StudioMutationResult:
    return _execute(lambda: service.studio.save_shot(project_id, shot_id, request))


@router.put("/projects/{project_id}/editorial", response_model=StudioMutationResult)
def save_timeline(project_id: str, request: StudioTimelineSave, service: StudioService) -> StudioMutationResult:
    return _execute(lambda: service.studio.save_timeline(project_id, request))


@router.get("/projects/{project_id}/editorial", response_model=StudioEditorial)
def read_editorial(project_id: str, service: StudioService) -> StudioEditorial:
    from short_drama.studio_editorial import read_editorial as read
    return _execute(lambda: read(service, project_id))


@router.get("/projects/{project_id}/shots/{shot_id}/editorial", response_model=StudioEditorial)
def read_shot_editorial(project_id: str, shot_id: str, service: StudioService, anchor_clip_id: str | None = None) -> StudioEditorial:
    from short_drama.studio_shot_editorial import read_shot_editorial as read
    return _execute(lambda: read(service, project_id, shot_id, anchor_clip_id))


@router.put("/projects/{project_id}/shots/{shot_id}/editorial", response_model=StudioMutationResult)
def save_shot_editorial(project_id: str, shot_id: str, request: StudioTimelineSave, service: StudioService) -> StudioMutationResult:
    from short_drama.studio_shot_editorial import save_shot_editorial as save
    return _execute(lambda: save(service, project_id, shot_id, request))


@router.post("/projects/{project_id}/exports", response_model=StudioExport)
def export_editorial(project_id: str, request: StudioExportRequest, service: StudioService) -> StudioExport:
    from short_drama.studio_export import start_export
    return _execute(lambda: start_export(service, project_id, request))


@router.get("/projects/{project_id}/exports/{export_id}", response_model=StudioExport)
def read_export(project_id: str, export_id: str, service: StudioService) -> StudioExport:
    from short_drama.studio_export import get_export
    return _execute(lambda: get_export(service, project_id, export_id))


@router.get("/projects/{project_id}/exports", response_model=StudioExports)
def read_exports(project_id: str, service: StudioService, limit: int = Query(default=20, ge=1, le=100),
                 before: str | None = Query(default=None, max_length=1000)) -> StudioExports:
    from short_drama.studio_export import list_exports
    return _execute(lambda: list_exports(service, project_id, limit=limit, before=before))


@router.post("/projects/{project_id}/exports/{export_id}/cancel", response_model=StudioExport)
def cancel_editorial_export(project_id: str, export_id: str, service: StudioService) -> StudioExport:
    from short_drama.studio_export import cancel_export
    return _execute(lambda: cancel_export(service, project_id, export_id))


@router.post("/projects/{project_id}/shots/{shot_id}/media", response_model=ProjectAggregate)
def select_shot_media(project_id: str, shot_id: str, request: StudioShotMediaSelect, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: select_imported_video(service, project_id, shot_id, request))
