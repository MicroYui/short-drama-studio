"""Observed H3 intervals replace one actual use without moving the film edit."""
from __future__ import annotations

from array import array
from math import cos, pi, sin
from pathlib import Path
import sqlite3
import subprocess
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import imageio_ffmpeg
from pydantic import JsonValue, ValidationError
import pytest

from _routes.short_drama import router
from _routes._errors import HTTPError
from short_drama.deps import get_short_drama_service
from short_drama.models import GenerationUnitShotWindow, QaStatus, Take
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import EditorialTitleCreate, StudioExportRequest, StudioTakeSelectRequest, StudioTimelineSave
from short_drama.studio_visual_edits import add_titles
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def candidate_use(episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path) -> tuple[ShortDramaService, str, Take, str]:
    service, pid, shot_ids = episode
    old = next(item for item in service.get_project(pid).takes if item.shot_id == shot_ids[1])
    for name, duration, color, frequency in (("old", 9.42, "blue", 440), ("candidate", 10.13, "red", 880)):
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=320x180:r=24:d={duration}", "-f", "lavfi", "-i",
            f"sine=frequency={frequency}:duration={duration}", "-c:v", "libx264", "-c:a", "aac", "-shortest",
            str(tmp_path / f"{name}.mp4")], check=True, capture_output=True)
    old.media_path, old.duration_seconds = str(tmp_path / "old.mp4"), 9.42
    old.generation_unit_id = "original-h3-group"
    old.shot_windows = [GenerationUnitShotWindow(shot_id=old.shot_id, shot_revision=1,
        source_in_seconds=4, source_out_seconds=8.5, duration_seconds=4.5)]
    service.repository.save_take(old)
    candidate = old.model_copy(deep=True, update={"id": "observed-h3-candidate", "candidate_index": 99,
        "media_path": str(tmp_path / "candidate.mp4"), "duration_seconds": 10.13,
        "is_selected": False, "selected_shot_ids": [], "qa_status": QaStatus.PENDING,
        "shot_windows": [GenerationUnitShotWindow(shot_id=old.shot_id, shot_revision=2,
            source_in_seconds=4, source_out_seconds=10, duration_seconds=6)]})
    service.repository.save_take(candidate)
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    video = next(clip for clip in timeline.clips if clip.type == "video" and (clip.model_extra or {}).get("sourceShotId") == old.shot_id)
    for clip in timeline.clips:
        if (clip.model_extra or {}).get("sourceShotId") == old.shot_id:
            clip.startTime, clip.duration, clip.trimStart, clip.trimEnd = 3.875, 5.545, 3.875, 0
            clip.volume, clip.audioFadeInSeconds, clip.audioFadeOutSeconds = 0.65, 0.25, 0.1
            assert clip.model_extra is not None and clip.asset is not None
            clip.model_extra.update(sourceWindowStart=4.0, sourceWindowEnd=8.5)
            clip.asset = clip.asset.model_copy(update={"path": old.media_path, "duration": old.duration_seconds})
            editor.assets = [clip.asset if asset.id == clip.assetId else asset for asset in editor.assets]
        elif clip.startTime == 0:
            clip.duration = 3.875
        else:
            clip.startTime = 10.42
    # An adjacent use of the SAME shot/Take is one shot window. clip_ids must
    # still protect it, including its separately linked native sound.
    linked = next(clip for clip in timeline.clips if clip.type == "audio" and (clip.model_extra or {}).get("sourceShotId") == old.shot_id)
    other_video = video.model_copy(deep=True, update={"id": "other-use", "startTime": 9.42,
        "duration": 1.0, "trimStart": 1.0, "linkedClipIds": ["other-sound"]})
    other_audio = linked.model_copy(deep=True, update={"id": "other-sound", "startTime": 9.42,
        "duration": 1.0, "trimStart": 1.0, "linkedClipIds": ["other-use"]})
    timeline.clips.extend([other_video, other_audio])
    add_titles(timeline, [EditorialTitleCreate(id="human-title", text="保留人工标题",
        start_seconds=0, duration_seconds=14.42, track_index=1)], shot_id=None)
    for cue in timeline.subtitles:
        if cue.id == "cue-1":
            cue.text, cue.startTime, cue.endTime = "我答应过你，就不会食言。", 6.82, 9.2
            cue.style = {"fontSize": 58, "color": "#ffeecc"}
        elif cue.id == "cue-2":
            cue.startTime, cue.endTime = 11.17, 13.92
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    return service, pid, candidate, video.id


@pytest.mark.parametrize("linked_native_audio", [True, False])
def test_tool_adopts_exact_observed_interval_preserving_other_uses_and_reopens(
    candidate_use: tuple[ShortDramaService, str, Take, str], linked_native_audio: bool,
) -> None:
    service, pid, candidate, clip_id = candidate_use
    if not linked_native_audio:
        current = read_editorial(service, pid)
        editor = EditorProject.model_validate(current.project)
        video = next(clip for clip in editor.timelines[0].clips if clip.id == clip_id)
        assert video.model_extra is not None
        separate_ids = cast(list[str], video.model_extra["linkedClipIds"])
        editor.timelines[0].clips = [clip for clip in editor.timelines[0].clips if clip.id not in separate_ids]
        video.model_extra["linkedClipIds"] = []
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": as_json(editor)}))
    before = read_editorial(service, pid)
    original = EditorProject.model_validate(before.project)
    shots_before = service.get_project(pid).document.shots
    result = service.tool_registry.invoke("video.select_take", {"take_id": candidate.id,
        "clip_ids": [clip_id], "expected_editorial_revision": before.revision,
        "source_in_seconds": 4.5, "source_out_seconds": 10.045})
    assert result.status.value == "SUCCEEDED", result.error
    saved = read_editorial(service, pid)
    assert saved.revision == before.revision + 1
    after = EditorProject.model_validate(saved.project)
    updated = {clip.id: clip for clip in after.timelines[0].clips}
    selected = next(clip for clip in original.timelines[0].clips if clip.id == clip_id)
    linked_ids = cast(list[str], (selected.model_extra or {})["linkedClipIds"])
    for old_clip in original.timelines[0].clips:
        clip = updated[old_clip.id]
        if clip.id not in {clip_id, *linked_ids}:
            assert clip == old_clip
            continue
        expected = old_clip.model_copy(deep=True)
        expected.assetId, expected.asset = clip.assetId, clip.asset
        expected.trimStart, expected.trimEnd = 4.5, candidate.duration_seconds - 4.5 - old_clip.duration * old_clip.speed
        assert expected.model_extra is not None
        expected.model_extra.update(sourceTakeId=candidate.id, sourceWindowStart=4.5, sourceWindowEnd=10.045)
        assert clip.model_dump() == expected.model_dump()
        assert clip.trimEnd == pytest.approx(0.085)
        assert clip.asset is not None and clip.asset.path == candidate.media_path
        assert (clip.startTime, clip.duration, clip.speed) == (3.875, 5.545, 1)
    assert after.timelines[0].tracks == original.timelines[0].tracks
    assert after.timelines[0].subtitles == original.timelines[0].subtitles
    assert service.get_project(pid).document.shots == shots_before
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert read_editorial(reopened, pid) == saved
    # Repeating the exact adoption is idempotent, including user controls.
    service.select_take(candidate.id, clip_ids=[clip_id], expected_editorial_revision=saved.revision,
        source_in_seconds=4.5, source_out_seconds=10.045)
    assert read_editorial(service, pid) == saved
    exported = start_export(service, pid, StudioExportRequest(expected_revision=saved.revision,
        width=480, height=270, fps=24), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == pytest.approx(14.42)
    assert exported.video_path is not None
    def pixel(time: float) -> tuple[int, int, int]:
        raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", str(time), "-i", exported.video_path or "",
            "-frames:v", "1", "-vf", "crop=2:2:0:0,scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            check=True, capture_output=True).stdout
        return raw[0], raw[1], raw[2]
    assert pixel(5)[0] > 200 and pixel(5)[2] < 30
    assert pixel(10)[2] > 200 and pixel(10)[0] < 30
    def tone_energy(time: float, frequency: int) -> float:
        raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", str(time), "-i", exported.video_path or "",
            "-t", "0.2", "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"], check=True, capture_output=True).stdout
        samples = array("f", raw)
        real = sum(value * cos(2 * pi * frequency * index / 8000) for index, value in enumerate(samples))
        imaginary = sum(value * sin(2 * pi * frequency * index / 8000) for index, value in enumerate(samples))
        return real * real + imaginary * imaginary
    assert tone_energy(5, 880) > tone_energy(5, 440) * 50
    assert tone_energy(10, 440) > tone_energy(10, 880) * 50


@pytest.mark.parametrize(("source_in", "source_out", "message"), [
    (4.5, 10.2, "超出了原素材时长"),
    (4.5, 10, "必须等于片段"),
    (float("nan"), 10.045, "finite"),
    (4.5, None, "supplied together"),
    (4.5, 4.5, "must follow"),
])
def test_invalid_interval_keeps_sqlite_edit_and_selection(
    candidate_use: tuple[ShortDramaService, str, Take, str], source_in: float, source_out: float | None, message: str,
) -> None:
    service, pid, candidate, clip_id = candidate_use
    before = service.get_project(pid)
    revision = read_editorial(service, pid).revision
    with pytest.raises(WorkflowConflictError, match=message):
        service.select_take(candidate.id, clip_ids=[clip_id], expected_editorial_revision=revision,
            source_in_seconds=source_in, source_out_seconds=source_out)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    after = reopened.get_project(pid)
    assert after.document == before.document and after.takes == before.takes


@pytest.mark.parametrize("change", [
    {"clip_ids": []}, {"clip_ids": ["one", "two"]}, {"expected_editorial_revision": None},
])
def test_source_contract_requires_a_single_current_use(change: dict[str, JsonValue]) -> None:
    arguments: dict[str, JsonValue] = {"clip_ids": ["one"], "expected_editorial_revision": 2,
        "source_in_seconds": 4.5, "source_out_seconds": 10.045, **change}
    with pytest.raises(ValidationError):
        StudioTakeSelectRequest.model_validate(arguments)


def test_http_exact_adoption_and_old_no_body_request_remain_supported(
    candidate_use: tuple[ShortDramaService, str, Take, str],
) -> None:
    service, pid, candidate, clip_id = candidate_use
    app = FastAPI()
    @app.exception_handler(HTTPError)
    async def handle_error(_request: Request, exc: HTTPError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(status_code=exc.status_code, content=exc.response.model_dump())
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)
    before = read_editorial(service, pid)
    request = {"clip_ids": [clip_id], "expected_editorial_revision": before.revision,
        "source_in_seconds": 4.5, "source_out_seconds": 10.045}
    response = client.post(f"/api/short-drama/takes/{candidate.id}/select", json=request)
    assert response.status_code == 200, response.text
    assert read_editorial(service, pid).revision == before.revision + 1
    conflict = client.post(f"/api/short-drama/takes/{candidate.id}/select", json=request)
    assert conflict.status_code == 409
    # No-body legacy requests still parse and reach selection (the newly mixed
    # source shot requires a precise use rather than silently replacing both).
    legacy = client.post(f"/api/short-drama/takes/{candidate.id}/select")
    assert legacy.status_code == 409 and "混用了多个源素材" in legacy.text


def test_explicit_adoption_rejects_audio_shared_with_unselected_use(
    candidate_use: tuple[ShortDramaService, str, Take, str],
) -> None:
    service, pid, candidate, clip_id = candidate_use
    saved = read_editorial(service, pid)
    editor = EditorProject.model_validate(saved.project)
    selected = next(clip for clip in editor.timelines[0].clips if clip.id == clip_id)
    other = next(clip for clip in editor.timelines[0].clips if clip.id == "other-use")
    assert other.model_extra is not None
    other.model_extra["linkedClipIds"] = (selected.model_extra or {})["linkedClipIds"]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision, timeline={"project": as_json(editor)}))
    before = service.get_project(pid)
    with pytest.raises(WorkflowConflictError, match="关联原声.*未选画面"):
        service.select_take(candidate.id, clip_ids=[clip_id], expected_editorial_revision=saved.revision + 1,
            source_in_seconds=4.5, source_out_seconds=10.045)
    assert service.get_project(pid).document == before.document
    assert service.get_project(pid).takes == before.takes


def test_explicit_source_span_uses_existing_speed(candidate_use: tuple[ShortDramaService, str, Take, str]) -> None:
    service, pid, candidate, clip_id = candidate_use
    saved = read_editorial(service, pid)
    editor = EditorProject.model_validate(saved.project)
    selected = next(clip for clip in editor.timelines[0].clips if clip.id == clip_id)
    linked = cast(list[str], (selected.model_extra or {})["linkedClipIds"])
    for clip in editor.timelines[0].clips:
        if clip.id in {clip_id, *linked}:
            clip.speed, clip.duration = 2, 2.7725
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision, timeline={"project": as_json(editor)}))
    service.select_take(candidate.id, clip_ids=[clip_id], expected_editorial_revision=saved.revision + 1,
        source_in_seconds=4.5, source_out_seconds=10.045)
    after = EditorProject.model_validate(read_editorial(service, pid).project)
    video = next(clip for clip in after.timelines[0].clips if clip.id == clip_id)
    assert (video.startTime, video.duration, video.speed, video.trimStart) == (3.875, 2.7725, 2, 4.5)


def test_final_project_save_failure_rolls_back_take_selection_and_editorial(
    candidate_use: tuple[ShortDramaService, str, Take, str],
) -> None:
    service, pid, candidate, clip_id = candidate_use
    before = service.get_project(pid)
    revision = read_editorial(service, pid).revision
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_adoption_project_save BEFORE UPDATE ON projects
            BEGIN
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM takes WHERE id = 'observed-h3-candidate'
                    AND json_extract(record_json, '$.is_selected') = 1
                ) THEN RAISE(ABORT, 'Take selection was not reached') END;
                SELECT RAISE(ABORT, 'forced final project save failure');
            END;
        """)
    with pytest.raises(sqlite3.IntegrityError, match="forced final project save failure"):
        service.select_take(candidate.id, clip_ids=[clip_id], expected_editorial_revision=revision,
            source_in_seconds=4.5, source_out_seconds=10.045)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    after = reopened.get_project(pid)
    assert after.document == before.document
    assert after.takes == before.takes
