"""Real local decoding, exact adopted-use coordinates and reusable image handoff."""

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from PIL import Image

from _routes.short_drama import router
from _routes._errors import HTTPError
from short_drama.deps import get_short_drama_service
from short_drama.media_frame_asset import TakeFrameAssetRequest, TakeFrameAssetResponse
from short_drama.models import ApprovalStatus, EnqueueRequest, GenerationUnitShotWindow, ImageReferenceRole, ProjectDocument, Take
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools.contracts import ToolCallStatus
from tests.test_video_inspection import _render_video

Source = tuple[ShortDramaService, ProjectDocument, Take]


@pytest.fixture
def source(tmp_path: Path) -> Source:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = service.drain(document.project.id, 50).project.takes[0]
    video = tmp_path / "source.mp4"
    _render_video(video, 4)
    take.media_path, take.duration_seconds, take.generation_unit_id = str(video), 4, "two-shot-group"
    take.shot_windows = [GenerationUnitShotWindow(shot_id=shot.id, shot_revision=shot.revision,
        source_in_seconds=i * 2, source_out_seconds=(i + 1) * 2, duration_seconds=2)
        for i, shot in enumerate(document.shots[:2])]
    service.repository.save_take(take)
    return service, document, take


def test_tool_preserves_pixels_actual_pts_and_reference_handoff_without_adoption(source: Source) -> None:
    service, document, take = source
    before = service.get_project(document.project.id).document
    args = {"project_id": document.project.id, "request": {"take_id": take.id,
        "time_seconds": .71, "description": "Counter blocking and tray state for the next shot"}}
    result = service.tool_registry.invoke("video.extract_frame", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = TakeFrameAssetResponse.model_validate(result.output)
    assert (response.width, response.height) == (432, 768)
    assert .71 <= response.source_time_seconds < .71 + 1 / 24
    assert response.coordinate_space == "SOURCE" and response.review_status == "NOT_REVIEWED"
    assert response.image_reference_input.reference_role == ImageReferenceRole.COMPOSITION
    assert response.h3_media_input.image_reference_role == ImageReferenceRole.COMPOSITION
    assert response.h3_media_input.asset_version_id == response.image_reference_input.asset_version_id
    with Image.open(response.local_path) as pixels:
        assert pixels.size == (432, 768) and pixels.format == "PNG"
    after = service.get_project(document.project.id).document
    saved = next(a for a in after.assets if a.id == response.asset_id)
    assert saved.versions[0].approval_status == ApprovalStatus.DRAFT
    assert saved.versions[0].source and saved.versions[0].source.provider_asset_id == take.id
    assert after.shots == before.shots and after.editorial_timeline == before.editorial_timeline
    assert service.repository.get_take(take.id) == take
    repeated = service.tool_registry.invoke("video.extract_frame", args)
    cached = TakeFrameAssetResponse.model_validate(repeated.output)
    assert cached.reused and cached.asset_version_id == response.asset_version_id
    assert len(service.get_project(document.project.id).document.assets) == len(after.assets)


def test_shot_local_uses_covered_window_and_endpoint_stays_inside(source: Source) -> None:
    service, document, take = source
    request = TakeFrameAssetRequest(take_id=take.id, shot_id=document.shots[1].id,
        time_seconds=0, description="The actual start of the group's second shot")
    response = service.extract_take_frame(document.project.id, request)
    assert response.coordinate_space == "SHOT_LOCAL"
    assert response.source_window_start_seconds == 2 and response.source_time_seconds == 2
    end = service.extract_take_frame(document.project.id, request.model_copy(update={"time_seconds": 2}))
    assert 4 - 1 / 24 - .00001 <= end.source_time_seconds < 4
    with pytest.raises(WorkflowConflictError, match="outside"):
        service.extract_take_frame(document.project.id, request.model_copy(update={"time_seconds": 2.1}))
    with pytest.raises(WorkflowConflictError, match="not covered"):
        service.extract_take_frame(document.project.id, request.model_copy(update={"shot_id": document.shots[2].id}))


def test_exact_clip_maps_speed_reverse_and_rejects_ambiguous_timeline(source: Source) -> None:
    service, document, take = source
    clip = {"id": "second-use", "assetId": take.id, "type": "video", "sourceTakeId": take.id,
        "startTime": 12, "duration": 1, "trimStart": 2, "speed": 2, "reversed": True, "trackIndex": 0}
    document.editorial_timeline = {"revision": 1, "project": {"id": document.project.id, "version": 2,
        "name": "Two film uses", "activeTimelineId": "film-a",
        "assets": [{"id": take.id, "type": "video", "path": take.media_path}],
        "timelines": [{"id": identity, "name": identity, "tracks": [], "clips": [clip], "subtitles": []}
                      for identity in ["film-a", "film-b"]]}}
    service.repository.save_project(document)
    request = TakeFrameAssetRequest(take_id=take.id, clip_id="second-use", time_seconds=.5,
        description="Exact reversed second film-use midpoint")
    with pytest.raises(WorkflowConflictError, match="timeline_id is required"):
        service.extract_take_frame(document.project.id, request)
    response = service.extract_take_frame(document.project.id, request.model_copy(update={"timeline_id": "film-b"}))
    assert response.coordinate_space == "CLIP_LOCAL" and response.timeline_id == "film-b"
    assert response.source_time_seconds == 3
    assert response.source_window_start_seconds == 2 and response.source_window_end_seconds == 4


def test_http_extract_rejects_other_project_take_and_returns_created_asset(source: Source) -> None:
    service, document, take = source
    app = FastAPI()
    @app.exception_handler(HTTPError)
    async def handle_error(_request: Request, exc: HTTPError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(status_code=exc.status_code, content=exc.response.model_dump())
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(f"/api/short-drama/projects/{document.project.id}/agent-tools/extract-frame",
            json={"take_id": take.id, "time_seconds": 1, "description": "Frame for composition editing"})
        assert response.status_code == 200, response.text
        assert response.json()["source_time_seconds"] == 1
        rejected = client.post(f"/api/short-drama/projects/{document.project.id}/agent-tools/extract-frame",
            json={"take_id": "absent-take", "time_seconds": 1, "description": "Missing source"})
        assert rejected.status_code == 409, rejected.text
