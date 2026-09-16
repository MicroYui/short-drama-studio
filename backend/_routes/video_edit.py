"""Read-only HTTP access to historical selected-use lip-sync records."""
from collections.abc import Callable
from typing import Annotated, TypeVar
from fastapi import APIRouter, Depends, HTTPException
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.video_edit_models import LipSyncResponse, VideoEditRun


router = APIRouter()
Service = Annotated[ShortDramaService, Depends(get_short_drama_service)]
T = TypeVar("T")


def _execute(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, f"未找到内容：{exc.args[0]}") from exc
    except WorkflowConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/video-edits", response_model=list[VideoEditRun])
def list_video_edits(project_id: str, service: Service) -> list[VideoEditRun]:
    return _execute(lambda: service.list_lip_sync(project_id))


@router.get("/projects/{project_id}/video-edits/{run_id}", response_model=LipSyncResponse)
def read_video_edit(project_id: str, run_id: str, service: Service) -> LipSyncResponse:
    return _execute(lambda: service.get_lip_sync(project_id, run_id))
