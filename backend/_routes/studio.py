"""Thin product workspace endpoints; content remains in ProjectDocument."""

from collections.abc import Callable
from collections.abc import AsyncIterator
import asyncio
from pathlib import Path
from uuid import uuid4
import shutil
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
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
    StudioEditorial, StudioExport, StudioExportRequest,
)
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_comfy import ComfyConfig, ComfyConfigUpdate, ComfyRun, ComfyRunRequest, comfy_config, run_comfy, get_comfy_run

router = APIRouter(prefix="/studio", tags=["studio"])
StudioService = Annotated[ShortDramaService, Depends(get_short_drama_service)]
T = TypeVar("T")


@router.get("/comfyui", response_model=ComfyConfig)
def read_comfy_config(service: StudioService) -> ComfyConfig:
    return _execute(lambda: comfy_config(service))


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


@router.post("/projects", response_model=ProjectAggregate)
def create_project(request: StudioProjectCreate, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: service.studio.create_project(request))


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


@router.post("/projects/{project_id}/shots/{shot_id}/media", response_model=ProjectAggregate)
def select_shot_media(project_id: str, shot_id: str, request: StudioShotMediaSelect, service: StudioService) -> ProjectAggregate:
    return _execute(lambda: select_imported_video(service, project_id, shot_id, request))
