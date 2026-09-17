"""Real local media, durable Fake provider and exact saved-use adoption checks."""
from __future__ import annotations

import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Literal, cast

import imageio_ffmpeg
import numpy as np
from pydantic import JsonValue
import pytest

from short_drama.h3_models import H3ProviderConfigUpdate
from short_drama.models import (Freshness, GenerationTask, OperationKind, QAReport, QaStatus, Take, TakeAvailability, TaskStatus)
from short_drama.providers.kling_lip_sync_client import KlingRemoteTask, KlingRequestRejected, KlingSubmission, KlingSubmissionUnknown, KlingTransientError
from short_drama.providers.kling_config import KlingProviderConfigUpdate
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorProject, EditorTrack, EditorSubtitle, read_editorial, validate_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from short_drama.video_edit_models import LipSyncRequest, LipSyncResponse, VideoEditRun
from short_drama.video_edit_worker import PersistentVideoEditWorker
from short_drama.worker import InvalidTaskTransition, utc_now


class FakeKling:
    upload_http_status: int | None = 200
    def __init__(self) -> None:
        self.uploads: list[Path] = []
        self.posts: list[dict[str, JsonValue]] = []
        self.queries: list[str] = []
        self.block: str | None = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.unknown = False
        self.download_fails = False
        self.http_status = 200
        self.candidate: Path | None = None
        self.remote_request: dict[str, JsonValue] | None = None
        self.receipt_usage: dict[str, JsonValue] = {}
        self.task_usage: dict[str, JsonValue] = {}

    def wait(self, operation: str) -> None:
        if self.block == operation:
            self.entered.set()
            assert self.release.wait(10)

    def upload_video(self, path: Path) -> str:
        self.uploads.append(path)
        self.wait("upload")
        return "https://files.example.test/prepared.mp4"

    def submit(self, *, video_url: str, audio_path: Path) -> KlingSubmission:
        self.posts.append({"video_url": video_url, "mode": "audio2video", "audio_type": "file",
            "audio_file": base64.b64encode(audio_path.read_bytes()).decode()})
        self.wait("submit")
        if self.unknown:
            raise KlingSubmissionUnknown("response lost after POST")
        return KlingSubmission(task_id="remote-original", http_status=self.http_status,
            response={"task_id": "remote-original", "success": self.http_status == 200, **self.receipt_usage})

    def query(self, task_id: str) -> KlingRemoteTask:
        self.queries.append(task_id)
        self.wait("query")
        return KlingRemoteTask.model_validate({"id": task_id, "request": self.remote_request or (self.posts[-1] if self.posts else {}),
            "response": {"state": "succeed", "video_url": "https://files.example.test/result.mp4"}, **self.task_usage})

    def download(self, url: str, destination: Path) -> None:
        assert url.endswith("result.mp4")
        self.wait("download")
        if self.download_fails:
            raise KlingTransientError("temporary download unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.candidate or self.uploads[-1], destination)


@pytest.fixture(scope="module")
def source_media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source = tmp_path_factory.mktemp("lip-sync-source") / "source.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
        check=True, capture_output=True)
    return source


def lip_sync_fixture(tmp_path: Path, source: Path, fake: FakeKling, *, reverse: bool = False,
                     speed: float = 1, linked: bool = False, selection_start: float = 1) -> tuple[ShortDramaService, LipSyncResponse]:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media",
        kling_client_factory=lambda _key, _upload: fake)
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-local-test-key"))
    aggregate = service.initialize_fixture()
    document = aggregate.document
    project_id, shot_id, now = document.project.id, document.shots[0].id, utc_now()
    task = GenerationTask(id="source-task", project_id=project_id, shot_id=shot_id, operation=OperationKind.GENERATE,
        candidate_index=1, status=TaskStatus.SUCCEEDED, provider="minimax", model="existing-fixture-video",
        request_fingerprint="existing-source", input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id, input_shot_revision=document.shots[0].revision,
        requested_duration_seconds=10, attempt=1, created_at=now, updated_at=now)
    qa = QAReport(id="source-qa", take_id="source-take", status=QaStatus.PASSED, hard_gate_passed=True,
        score=4, issues=[], checks=["real local media fixture"], created_at=now)
    take = Take(id="source-take", project_id=project_id, shot_id=shot_id, task_id=task.id, candidate_index=1,
        operation=OperationKind.GENERATE, provider="minimax", media_path=str(source), thumbnail_path=str(source),
        duration_seconds=10, width=640, height=360, availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.PASSED, qa_report=qa, is_selected=True, selected_shot_ids=[shot_id],
        freshness=Freshness.CURRENT, created_at=now)
    service.repository.save_task(task); service.repository.save_take(take)
    editor = EditorProject.model_validate(read_editorial(service, project_id).project)
    timeline = editor.timelines[0]
    timeline.tracks.append(EditorTrack(id="manual-captions", name="人工字幕", type="subtitle"))
    timeline.subtitles = [EditorSubtitle(id="keep-caption", text="人工字幕与画面修复独立", startTime=.5, endTime=3.5,
        trackIndex=len(timeline.tracks) - 1)]
    clip = timeline.clips[0]
    clip.startTime, clip.duration, clip.trimStart, clip.trimEnd = 0, 4, 1, 0
    clip.speed, clip.reversed = speed, reverse
    clip.opacity, clip.flipH = 73, True
    assert clip.model_extra is not None
    clip.model_extra["colorCorrection"] = {"brightness": 15, "contrast": 0, "saturation": 0, "temperature": 0,
        "tint": 0, "exposure": 0, "highlights": 0, "shadows": 0}
    clip.model_extra["colorLabel"] = "blue"
    clip.audioFadeInSeconds, clip.audioFadeOutSeconds, clip.volume = 1.5, 1.25, .6
    clip.model_extra.update(transitionIn={"type": "fade-to-black", "duration": .5},
        transitionOut={"type": "fade-to-black", "duration": .5}) if clip.model_extra is not None else None
    duplicate = clip.model_copy(deep=True, update={"id": "other-use", "startTime": 4, "muted": True})
    timeline.clips = [clip, duplicate]
    if linked:
        audio = clip.model_copy(deep=True, update={"id": "original-audio", "type": "audio"})
        assert audio.model_extra is not None and clip.model_extra is not None
        audio.model_extra["linkedClipIds"] = [clip.id]
        audio.model_extra["transitionIn"] = {"type": "none", "duration": 0}
        audio.model_extra["transitionOut"] = {"type": "none", "duration": 0}
        clip.model_extra["linkedClipIds"] = [audio.id]
        timeline.clips.append(audio)
    editor.timelines.append(timeline.model_copy(deep=True, update={"id": "other-timeline", "name": "人工备用"}))
    saved = service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=0,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    revision = cast(int, (saved.project.document.editorial_timeline or {})["revision"])
    response = service.enqueue_lip_sync(project_id, LipSyncRequest(shot_id=shot_id, timeline_id=timeline.id,
        clip_id=clip.id, start_seconds=selection_start, end_seconds=selection_start + 2, expected_editorial_revision=revision,
        intent="只修正说话时嘴形", preserve=["人物身份", "道具", "原声", "未选画面"]))
    return service, response


def complete(service: ShortDramaService, response: LipSyncResponse) -> LipSyncResponse:
    assert service.advance_task(response.task.id).status == TaskStatus.SUBMITTING
    assert service.advance_task(response.task.id).status == TaskStatus.ACKNOWLEDGED
    assert service.advance_task(response.task.id).status == TaskStatus.SUCCEEDED
    return service.get_lip_sync(response.run.project_id, response.run.id)


def test_default_service_keeps_history_but_cannot_execute_kling(
    tmp_path: Path, source_media: Path,
) -> None:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from _routes._errors import HTTPError
    from _routes.short_drama import router
    from short_drama.deps import get_short_drama_service

    fake = FakeKling()
    fixture, response = lip_sync_fixture(tmp_path, source_media, fake)
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    pid, rid, tid = response.run.project_id, response.run.id, response.task.id
    before = fixture.get_project(pid).model_dump_json()
    before_run = response.run.model_dump_json()
    assert service.video_edit_worker.client_factory is None
    assert service.get_lip_sync(pid, rid).run.model_dump_json() == before_run
    assert service.list_lip_sync(pid)[0].id == rid
    assert "历史" in service.get_lip_sync(pid, rid).warnings[0]

    operations: list[Callable[[], object]] = [
        lambda: service.enqueue_lip_sync(pid, response.run.request),
        lambda: service.advance_task(tid),
        lambda: service.retry_task(tid),
        lambda: service.recover_task_output(tid),
        lambda: service.reconcile_task(tid),
        lambda: service.retry_lip_sync_preparation(pid, rid),
        lambda: service.reconcile_lip_sync(pid, rid, "historical-remote-id"),
        lambda: service.adopt_lip_sync(pid, rid),
        lambda: service.update_kling_config(KlingProviderConfigUpdate(api_key="unused-local-test-value")),
    ]
    for operation in operations:
        with pytest.raises(WorkflowConflictError, match="历史 Kling 任务只可读取或取消"):
            operation()
    with pytest.raises(InvalidTaskTransition, match="历史 Kling"):
        service.video_edit_worker.advance(tid)

    app = FastAPI()

    async def http_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPError)
        return JSONResponse(status_code=exc.status_code, content=exc.response.model_dump())

    app.add_exception_handler(HTTPError, http_error)
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        for action in ("advance", "retry", "recover-output", "reconcile"):
            result = client.post(f"/api/short-drama/tasks/{tid}/{action}")
            assert result.status_code == 409
            assert "历史 Kling" in result.text
        assert service.get_project(pid).model_dump_json() == before
        assert service.get_lip_sync(pid, rid).run.model_dump_json() == before_run
        cancelled = client.post(f"/api/short-drama/tasks/{tid}/cancel")
        assert cancelled.status_code == 200
    assert service.get_generation_task(tid).status == TaskStatus.CANCELLED
    assert service.get_lip_sync(pid, rid).run.status == "CANCELLED"
    assert not service.kling_config.path.exists()
    assert fake.uploads == fake.posts == fake.queries == []


def test_default_service_can_pause_a_previously_submitted_kling_task(
    tmp_path: Path, source_media: Path,
) -> None:
    fixture, response = lip_sync_fixture(tmp_path, source_media, FakeKling())
    task, run = response.task, response.run
    task.status, task.provider_task_id = TaskStatus.ACKNOWLEDGED, "historical-remote-id"
    run.status, run.provider_task_id, run.submission_attempt_state = "RUNNING", task.provider_task_id, "ACKNOWLEDGED"
    fixture.repository.save_task(task)
    fixture.repository.save_video_edit_run(run)
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    service.cancel_task(task.id)
    historical = service.get_lip_sync(run.project_id, run.id)
    assert historical.run.status == "PAUSED"
    assert historical.run.provider_task_id == "historical-remote-id"
    assert historical.run.last_error_message is not None
    assert "不再恢复 Kling" in historical.run.last_error_message
    with pytest.raises(WorkflowConflictError, match="历史 Kling"):
        service.advance_task(task.id)


class UploadDeniedKling(FakeKling):
    denied = True

    def upload_video(self, path: Path) -> str:
        if self.denied:
            self.uploads.append(path)
            self.upload_http_status = 401
            raise KlingRequestRejected(401, "原视频上传被拒绝：Invalid authentication credentials；尚未提交口型生成。",
                details={"detail": "Invalid authentication credentials"})
        self.upload_http_status = 201
        return super().upload_video(path)


def test_upload_failure_records_stage_and_explicit_retry_reuses_original_run_and_new_config(
    tmp_path: Path, source_media: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("KLING_UPLOAD_TOKEN", raising=False)
    fake = UploadDeniedKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    project_id = response.run.project_id
    before = service.get_project(project_id).document.editorial_timeline
    failed = service.advance_task(response.task.id)
    run = service.get_lip_sync(project_id, response.run.id).run
    assert failed.status == TaskStatus.FAILED and failed.error_code == "KLING_UPLOAD_FAILED"
    assert run.last_error_stage == "upload" and run.upload_http_status == 401
    assert run.upload_error_detail == {"detail": "Invalid authentication credentials"}
    assert run.phase_seconds["upload"] >= 0 and "submit" not in run.phase_seconds
    assert run.submission_http_status is None and run.submission_attempt_state == "NOT_STARTED"
    assert run.provider_task_id is None and run.submitted_at is None and len(fake.posts) == 0
    service.advance_task(response.task.id)
    assert len(fake.uploads) == 1, "Failed preparation must not automatically loop"
    service.update_kling_config(KlingProviderConfigUpdate(api_key="generation-fixed", upload_token="upload-fixed"))
    keys: list[tuple[str, str]] = []
    def client(key: str, upload: str) -> FakeKling:
        keys.append((key, upload))
        return fake
    service.video_edit_worker.client_factory = client
    fake.denied = False
    retried = service.retry_task(response.task.id)
    assert retried.id == response.task.id and retried.status == TaskStatus.QUEUED
    assert len(fake.uploads) == 1 and keys == [], "Explicit retry only requeues; it does not itself call the Provider"
    result = complete(service, response)
    assert result.run.id == response.run.id and result.run.preparation_retry_count == 1
    assert result.run.upload_http_status == 201 and result.run.upload_error_detail == {}
    assert result.run.submission_http_status == 200 and result.run.submission_attempt_state == "ACKNOWLEDGED"
    assert len(service.list_lip_sync(project_id)) == 1 and len(fake.posts) == 1
    assert keys and all(value == ("generation-fixed", "upload-fixed") for value in keys)
    assert service.get_project(project_id).document.editorial_timeline == before
    candidate = service.repository.get_take(result.run.candidate_take_id or "")
    assert candidate is not None and not candidate.is_selected


@pytest.mark.parametrize("boundary", ["source-changed", "unknown", "known-id", "submitted-at"])
def test_preparation_retry_refuses_changed_or_possibly_submitted_input(
    tmp_path: Path, source_media: Path, boundary: str,
) -> None:
    fake = UploadDeniedKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    run = service.get_lip_sync(response.run.project_id, response.run.id).run
    if boundary == "source-changed":
        document = service.repository.get_project(run.project_id)
        assert document is not None and document.editorial_timeline is not None
        editor = EditorProject.model_validate(document.editorial_timeline["project"])
        editor.timelines[0].clips[0].volume = .2
        service.studio.save_timeline(run.project_id, StudioTimelineSave(expected_revision=1,
            timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    elif boundary == "unknown":
        run.submission_attempt_state = "UNKNOWN"
    elif boundary == "known-id":
        run.provider_task_id = "must-keep-original"
    else:
        run.submitted_at = utc_now()
    service.repository.save_video_edit_run(run)
    with pytest.raises(WorkflowConflictError):
        service.retry_lip_sync_preparation(run.project_id, run.id)
    with pytest.raises(WorkflowConflictError):
        service.retry_task(response.task.id)
    assert len(fake.uploads) == 1 and not fake.posts
    assert len(service.list_lip_sync(run.project_id)) == 1


def test_preparation_retry_is_not_exposed_by_product_api(tmp_path: Path, source_media: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from _routes.video_edit import router
    from short_drama.deps import get_short_drama_service
    fake = UploadDeniedKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    app = FastAPI()
    app.include_router(router, prefix="/api/short-drama")
    app.dependency_overrides[get_short_drama_service] = lambda: service
    endpoint = f"/api/short-drama/projects/{response.run.project_id}/video-edits/{response.run.id}/retry-preparation"
    with TestClient(app) as client:
        result = client.post(endpoint)
        assert result.status_code == 404
    assert service.get_lip_sync(response.run.project_id, response.run.id).run.status == "FAILED"
    assert len(fake.uploads) == 1 and fake.posts == []


class BlockingSourceCheckWorker(PersistentVideoEditWorker):
    def __init__(self, worker: PersistentVideoEditWorker) -> None:
        super().__init__(repository=worker.repository, workspace_root=worker.workspace_root,
            config_store=worker.config_store, client_factory=worker.client_factory)
        self.entered, self.release = threading.Event(), threading.Event()

    def source_changed(self, run: VideoEditRun) -> str | None:
        result = super().source_changed(run)
        self.entered.set()
        assert self.release.wait(10)
        return result


@pytest.mark.parametrize("concurrent_change", ["visual-edit", "audio-edit", "control-revision", "known-id"])
def test_preparation_retry_checks_files_without_blocking_writes_and_rechecks_latest_state(
    tmp_path: Path, source_media: Path, concurrent_change: str,
) -> None:
    fake = UploadDeniedKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    worker = BlockingSourceCheckWorker(service.video_edit_worker)
    service.video_edit_worker = worker

    def edit_while_files_are_checked() -> None:
        if concurrent_change in {"control-revision", "known-id"}:
            run = service.get_lip_sync(response.run.project_id, response.run.id).run
            if concurrent_change == "control-revision":
                run.control_revision += 1
            else:
                run.provider_task_id = "keep-this-original-task"
            service.repository.save_video_edit_run(run)
        else:
            current = read_editorial(service, response.run.project_id)
            editor = EditorProject.model_validate(current.project)
            if concurrent_change == "visual-edit":
                editor.timelines[0].clips[0].opacity = 65
            else:
                editor.timelines[0].clips[0].volume = .2
            service.studio.save_timeline(response.run.project_id, StudioTimelineSave(expected_revision=current.revision,
                timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.retry_lip_sync_preparation, response.run.project_id, response.run.id)
        try:
            assert worker.entered.wait(10)
            # A real SQLite write completes while the source check is paused.
            pool.submit(edit_while_files_are_checked).result(timeout=2)
        finally:
            worker.release.set()
        if concurrent_change == "visual-edit":
            assert pending.result(timeout=10).run.status == "QUEUED"
            editor = EditorProject.model_validate(read_editorial(service, response.run.project_id).project)
            assert editor.timelines[0].clips[0].opacity == 65
        else:
            with pytest.raises(WorkflowConflictError):
                pending.result(timeout=10)
            assert service.get_lip_sync(response.run.project_id, response.run.id).run.status == "FAILED"
    assert len(fake.uploads) == 1 and not fake.posts


@pytest.mark.parametrize("reverse,speed", [(False, 1), (False, 2), (True, 2)])
def test_prepare_exact_source_interval_and_candidate_is_unselected(tmp_path: Path, source_media: Path, reverse: bool, speed: float) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake, reverse=reverse, speed=speed, selection_start=.5)
    before = service.get_project(response.run.project_id).document.editorial_timeline
    result = complete(service, response)
    assert result.run.source_in_seconds == 1 + (1.5 if reverse else .5) * speed
    assert result.run.source_out_seconds == 1 + (3.5 if reverse else 2.5) * speed
    assert result.run.source_reversed == reverse
    assert result.run.prepared_width == 1280 and result.run.prepared_height == 720
    assert result.run.prepared_duration_seconds == 2
    assert result.run.quality_status == "AWAITING_REVIEW"
    assert len(fake.posts) == 1 and fake.queries == ["remote-original"]
    candidate = service.repository.get_take(result.run.candidate_take_id or "")
    assert candidate is not None and candidate.is_selected is False and candidate.qa_status == QaStatus.PENDING
    assert service.get_project(response.run.project_id).document.editorial_timeline == before
    assert "audio_file" not in result.run.latest_remote.get("request", {})
    assert result.run.usage_status == "NOT_PROVIDED" and result.run.actual_usage is None
    with pytest.raises(WorkflowConflictError, match="原选区"):
        service.select_take(candidate.id)
    def first_frame(path: str, start: float = 0) -> np.ndarray[tuple[int, ...], np.dtype[np.uint8]]:
        data = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", str(start), "-i", path,
            "-vf", "scale=640:360", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True).stdout
        return np.frombuffer(data, dtype=np.uint8)
    expected_time = result.run.source_out_seconds - 1 / 24 if reverse else result.run.source_in_seconds
    delta = np.abs(first_frame(result.run.prepared_video_path or "").astype(np.float32) - first_frame(str(source_media), expected_time).astype(np.float32))
    assert float(np.mean(delta)) < 5, "Preparation must map the source interval without baking opacity or horizontal flip"


@pytest.mark.parametrize("linked", [False, True])
def test_exact_adoption_preserves_other_uses_manual_changes_and_actual_audio(tmp_path: Path, source_media: Path, linked: bool) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake, reverse=True, speed=2, linked=linked)
    pid = response.run.project_id
    before_editor = EditorProject.model_validate(read_editorial(service, pid).project)
    before = start_export(service, pid, StudioExportRequest(expected_revision=1, width=640, height=360, fps=24, subtitles=False), background=False)
    assert before.status == "completed", before.error
    result = complete(service, response)
    document = service.repository.get_project(pid)
    assert document is not None
    document.shots[1].visual = "并行人工修改必须保留"
    document.project.name = "编辑期间的人工作品名"
    service.repository.save_project(document)
    adopted = service.adopt_lip_sync(pid, result.run.id)
    assert adopted.run.adoption_status == "ADOPTED"
    assert adopted.project.document.shots[1].visual == "并行人工修改必须保留"
    assert adopted.project.document.project.name == "编辑期间的人工作品名"
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    assert editor.timelines[1] == before_editor.timelines[1]
    assert editor.timelines[0].subtitles == before_editor.timelines[0].subtitles
    assert next(c for c in editor.timelines[0].clips if c.id == "other-use") == before_editor.timelines[0].clips[1]
    pieces = [c for c in editor.timelines[0].clips if c.id in adopted.run.adopted_clip_ids]
    assert [c.duration for c in pieces] == [1, 2, 1]
    assert [(c.trimStart, c.speed, c.reversed) for c in pieces] == [(7, 2, True), (0, 1, False), (1, 2, True)]
    assert pieces[1].muted and pieces[1].volume == 0
    assert all(c.opacity == 73 and c.flipH for c in pieces)
    assert all((c.model_extra or {}).get("colorCorrection") == (before_editor.timelines[0].clips[0].model_extra or {}).get("colorCorrection") for c in pieces)
    assert all((c.model_extra or {}).get("colorLabel") == "blue" for c in pieces)
    assert (pieces[0].model_extra or {})["transitionIn"] == {"type": "fade-to-black", "duration": .5}
    assert (pieces[-1].model_extra or {})["transitionOut"] == {"type": "fade-to-black", "duration": .5}
    assert all((c.model_extra or {}).get("transitionIn", {}).get("type") == "none" for c in pieces[1:])
    validate_editorial(service, pid, cast(dict[str, JsonValue], editor.model_dump(mode="json")), purpose="export")
    after = start_export(service, pid, StudioExportRequest(expected_revision=2, width=640, height=360, fps=24, subtitles=False), background=False)
    assert after.status == "completed", after.error
    def pcm(path: str | None) -> bytes:
        assert path
        return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path,
            "-f", "s16le", "-ac", "2", "-ar", "48000", "pipe:1"], check=True, capture_output=True).stdout
    assert pcm(before.video_path) == pcm(after.video_path), "All original sound and split envelope must be bit-identical"
    second = service.adopt_lip_sync(pid, result.run.id)
    assert second.project.document.editorial_timeline == adopted.project.document.editorial_timeline
    # The selected middle can differ; unselected source frames must retain their current edit.
    def frames(path: str | None) -> np.ndarray[tuple[int, ...], np.dtype[np.uint8]]:
        assert path
        data = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path,
            "-vf", "select='lt(t,0.9)+gte(t,3.1)'", "-fps_mode", "vfr", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], check=True, capture_output=True).stdout
        return np.frombuffer(data, dtype=np.uint8)
    delta = np.abs(frames(before.video_path).astype(np.float32) - frames(after.video_path).astype(np.float32))
    assert float(np.mean(delta)) < 2, "Unselected pixels must remain within independent H264 re-encoding error"


def test_changed_target_refuses_adoption_and_retains_candidate(tmp_path: Path, source_media: Path) -> None:
    service, response = lip_sync_fixture(tmp_path, source_media, FakeKling())
    result = complete(service, response)
    document = service.repository.get_project(result.run.project_id)
    assert document is not None and document.editorial_timeline is not None
    editor = EditorProject.model_validate(document.editorial_timeline["project"])
    editor.timelines[0].clips[0].trimStart += .25
    document.editorial_timeline["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json"))
    document.editorial_timeline["revision"] = 2
    service.repository.save_project(document)
    adopted = service.adopt_lip_sync(result.run.project_id, result.run.id)
    assert adopted.run.adoption_status == "SOURCE_CHANGED"
    assert adopted.project.document.editorial_timeline == document.editorial_timeline
    assert Path(adopted.run.candidate_media_path or "").is_file()


@pytest.mark.parametrize("phase", ["upload", "submit"])
def test_cancellation_races_slow_operation_without_locking_edits(tmp_path: Path, source_media: Path, phase: str) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    if phase == "submit":
        service.advance_task(response.task.id)
    fake.block = phase
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.advance_task, response.task.id)
        try:
            assert fake.entered.wait(10)
            pool.submit(service.cancel_task, response.task.id).result(timeout=2)
            document = service.repository.get_project(response.run.project_id)
            assert document is not None
            document.project.name = "取消时仍可保存"
            pool.submit(service.repository.save_project, document).result(timeout=2)
        finally:
            fake.release.set()
        pending.result(timeout=10)
    run = service.get_lip_sync(response.run.project_id, response.run.id).run
    if phase == "upload":
        assert run.status == "CANCELLED" and fake.posts == []
    else:
        assert run.status == "PAUSED" and run.provider_task_id == "remote-original"
    service.advance_task(response.task.id)
    assert len(fake.posts) == (0 if phase == "upload" else 1)
    assert fake.queries == []


def test_unknown_never_resubmits_and_requires_exact_original_id_request(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling(); fake.unknown = True
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    assert service.advance_task(response.task.id).status == TaskStatus.SUBMIT_UNKNOWN
    for _ in range(2): service.advance_task(response.task.id)
    assert len(fake.posts) == 1
    with pytest.raises(WorkflowConflictError, match="video.reconcile_lip_sync"):
        service.reconcile_task(response.task.id)
    with pytest.raises(WorkflowConflictError, match="明确的原任务 ID"):
        service.reconcile_lip_sync(response.run.project_id, response.run.id)
    fake.remote_request = {**fake.posts[0], "video_url": "https://different.example.test/not-ours.mp4"}
    with pytest.raises(WorkflowConflictError, match="精确匹配"):
        service.reconcile_lip_sync(response.run.project_id, response.run.id, "wrong-task")
    assert service.get_lip_sync(response.run.project_id, response.run.id).run.provider_task_id is None
    fake.remote_request = fake.posts[0]
    result = service.reconcile_lip_sync(response.run.project_id, response.run.id, "remote-original")
    assert result.run.status == "SUCCEEDED" and len(fake.posts) == 1


def test_error_receipt_and_output_recovery_keep_original_remote_id(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling(); fake.http_status = 500; fake.download_fails = True
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id); service.advance_task(response.task.id)
    assert service.advance_task(response.task.id).status == TaskStatus.OUTPUT_MISSING
    run = service.get_lip_sync(response.run.project_id, response.run.id).run
    assert run.submission_http_status == 500 and run.provider_task_id == "remote-original"
    fake.download_fails = False
    assert service.recover_task_output(response.task.id).status == TaskStatus.SUCCEEDED
    assert len(fake.posts) == 1 and fake.queries == ["remote-original", "remote-original"]


@pytest.mark.parametrize("state,known,paused,expected", [
    ("PREPARED", False, False, "PREPARED"), ("STARTED", False, False, "SUBMIT_UNKNOWN"),
    ("STARTED", True, False, "RUNNING"), ("STARTED", True, True, "PAUSED")])
def test_restart_preserves_submission_boundary(tmp_path: Path, source_media: Path, state: str, known: bool, paused: bool, expected: str) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    run = service.get_lip_sync(response.run.project_id, response.run.id).run
    run.submission_attempt_state = cast(Literal["PREPARED", "STARTED"], state)
    run.status = "PAUSED" if paused else "PREPARED" if state == "PREPARED" else "SUBMITTING"
    run.provider_task_id = "remote-original" if known else None
    task = service.get_generation_task(response.task.id)
    task.status, task.provider_task_id = TaskStatus.SUBMITTING, run.provider_task_id
    with service.repository.atomic():
        service.repository.save_task(task); service.repository.save_video_edit_run(run)
    restarted = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media", kling_client_factory=lambda _key, _upload: fake)
    recovered = restarted.get_lip_sync(run.project_id, run.id)
    assert recovered.run.status == expected
    assert not fake.posts and not fake.queries
    if known and not paused:
        restarted.advance_task(task.id)
        assert fake.queries == ["remote-original"] and not fake.posts
    if not known and state == "STARTED" or paused:
        restarted.advance_task(task.id)
        assert not fake.posts and not fake.queries


def test_visual_edit_during_generation_and_other_episode_are_preserved(tmp_path: Path, source_media: Path) -> None:
    from short_drama.studio_models import StudioEpisodeCreate
    service, response = lip_sync_fixture(tmp_path, source_media, FakeKling())
    complete(service, response)
    sibling = service.studio.create_episode(response.run.project_id, StudioEpisodeCreate(title="不相关第二集"))
    sibling_before = service.repository.get_project(sibling.document.project.id)
    document = service.repository.get_project(response.run.project_id)
    assert document is not None and document.editorial_timeline is not None
    editor = EditorProject.model_validate(document.editorial_timeline["project"])
    editor.timelines[0].clips[0].opacity = 62
    editor.timelines[0].clips[0].flipV = True
    assert editor.timelines[0].clips[0].model_extra is not None
    editor.timelines[0].clips[0].model_extra["colorCorrection"] = {"brightness": 20, "contrast": 5, "saturation": 0,
        "temperature": 0, "tint": 0, "exposure": 0, "highlights": 0, "shadows": 0}
    document.editorial_timeline["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json"))
    document.editorial_timeline["revision"] = 2
    service.repository.save_project(document)
    adopted = service.adopt_lip_sync(response.run.project_id, response.run.id)
    assert adopted.run.adoption_status == "ADOPTED"
    current = EditorProject.model_validate(read_editorial(service, response.run.project_id).project)
    pieces = [clip for clip in current.timelines[0].clips if clip.id in adopted.run.adopted_clip_ids]
    assert all(clip.opacity == 62 and clip.flipV for clip in pieces)
    assert all((clip.model_extra or {})["colorCorrection"] == (editor.timelines[0].clips[0].model_extra or {})["colorCorrection"] for clip in pieces)
    assert service.repository.get_project(sibling.document.project.id) == sibling_before


def test_success_reconcile_and_missing_adopted_output_do_not_undo_selection(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    result = complete(service, response)
    adopted = service.adopt_lip_sync(response.run.project_id, response.run.id)
    assert service.reconcile_lip_sync(response.run.project_id, response.run.id).task.status == TaskStatus.SUCCEEDED
    assert len(fake.queries) == 1
    Path(result.run.candidate_media_path or "").unlink()
    assert service.recover_task_output(response.task.id).status == TaskStatus.SUCCEEDED
    assert len(fake.posts) == 1 and len(fake.queries) == 2
    take = service.repository.get_take(result.run.candidate_take_id or "")
    assert take is not None and take.is_selected and take.selected_shot_ids == [response.run.shot_id]
    assert service.get_project(response.run.project_id).document.editorial_timeline == adopted.project.document.editorial_timeline


@pytest.mark.parametrize("already_paused", [False, True])
def test_cancel_wins_during_explicit_reconciliation(tmp_path: Path, source_media: Path, already_paused: bool) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id); service.advance_task(response.task.id)
    if already_paused:
        service.cancel_task(response.task.id)
    fake.block = "query"
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.reconcile_lip_sync, response.run.project_id, response.run.id)
        try:
            assert fake.entered.wait(10)
            pool.submit(service.cancel_task, response.task.id).result(timeout=2)
        finally:
            fake.release.set()
        pending.result(timeout=10)
    assert service.get_lip_sync(response.run.project_id, response.run.id).run.status == "PAUSED"
    assert not service.list_generation_task_takes(response.task.id)


def test_actual_usage_keeps_provider_units_and_absence(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling()
    fake.receipt_usage = {"usage": {"video_seconds": 2}}
    fake.task_usage = {"cost": {"amount": .125, "currency": "CNY"}}
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    assert response.run.actual_usage is None and response.run.usage_status == "NOT_PROVIDED"
    saved = complete(service, response).run
    assert saved.usage_status == "PROVIDED"
    assert saved.actual_usage == {"submission": {"usage": {"video_seconds": 2}},
        "query_task": {"cost": {"amount": .125, "currency": "CNY"}}}


def test_http_contract_keeps_history_without_write_routes(tmp_path: Path, source_media: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from _routes.short_drama import router
    from short_drama.deps import get_short_drama_service
    service, response = lip_sync_fixture(tmp_path, source_media, FakeKling())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    base = f"/api/short-drama/projects/{response.run.project_id}/video-edits"
    with TestClient(app) as client:
        duplicate = client.post(f"{base}/lip-sync", json=response.run.request.model_dump(mode="json"))
        assert duplicate.status_code == 405
        listed = client.get(base)
        assert listed.status_code == 200 and listed.json()[0]["id"] == response.run.id
        assert client.get(f"{base}/{response.run.id}").json()["run"]["status"] == "QUEUED"
        assert client.get(f"/api/short-drama/projects/unrelated/video-edits/{response.run.id}").status_code == 404
        assert client.post(f"{base}/{response.run.id}/adopt").status_code == 404
        assert client.post(f"{base}/{response.run.id}/reconcile", json={}).status_code == 404
        assert client.post(f"{base}/{response.run.id}/retry-preparation").status_code == 404
        routes = app.openapi()["paths"]
        assert "/api/short-drama/projects/{project_id}/video-edits/lip-sync" not in routes
        assert set(routes["/api/short-drama/projects/{project_id}/video-edits"]) == {"get"}
        assert set(routes["/api/short-drama/projects/{project_id}/video-edits/{run_id}"]) == {"get"}
    assert service.get_lip_sync(response.run.project_id, response.run.id).run.status == "QUEUED"


def test_explicit_retry_is_new_fingerprint_and_never_reuses_old_submission(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    service.cancel_task(response.task.id)
    retry = service.retry_task(response.task.id)
    assert retry.provider == "kling" and retry.id != response.task.id
    assert retry.request_fingerprint != response.task.request_fingerprint
    assert retry.retry_of_task_id == response.task.id and retry.attempt == 2
    service.advance_task(retry.id); service.advance_task(retry.id)
    assert len(fake.posts) == 1


def test_cancel_after_preparation_wins_before_post_boundary(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    entered, release = threading.Event(), threading.Event()
    def delayed_client(_key: str, _upload_token: str) -> FakeKling:
        entered.set()
        assert release.wait(10)
        return fake
    service.video_edit_worker.client_factory = delayed_client
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.advance_task, response.task.id)
        try:
            assert entered.wait(10)
            assert pool.submit(service.cancel_task, response.task.id).result(timeout=2).status == TaskStatus.CANCELLED
        finally:
            release.set()
        assert pending.result(timeout=10).status == TaskStatus.CANCELLED
    assert fake.posts == [] and service.get_lip_sync(response.run.project_id, response.run.id).run.submission_attempt_state == "PREPARED"


def test_relevant_edit_while_submit_factory_waits_is_rechecked_before_post(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    service.advance_task(response.task.id)
    entered, release = threading.Event(), threading.Event()
    def delayed_client(_key: str, _upload_token: str) -> FakeKling:
        entered.set()
        assert release.wait(10)
        return fake
    service.video_edit_worker.client_factory = delayed_client
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(service.advance_task, response.task.id)
        try:
            assert entered.wait(10)
            document = service.repository.get_project(response.run.project_id)
            assert document is not None and document.editorial_timeline is not None
            editor = EditorProject.model_validate(document.editorial_timeline["project"])
            editor.timelines[0].clips[0].trimStart += .25
            document.editorial_timeline["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json"))
            service.repository.save_project(document)
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result.status == TaskStatus.FAILED and result.error_code == "LIP_SYNC_SOURCE_CHANGED"
    assert fake.posts == []


def test_no_actual_audio_fails_before_upload_or_generation(tmp_path: Path, source_media: Path) -> None:
    silent = tmp_path / "without-audio.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(source_media),
        "-c:v", "copy", "-an", str(silent)], check=True, capture_output=True)
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, silent, fake)
    result = service.advance_task(response.task.id)
    assert result.status == TaskStatus.FAILED and "原声音轨" in (result.error_message or "")
    assert not fake.uploads and not fake.posts


def test_wrong_duration_candidate_is_retained_but_not_adoptable(tmp_path: Path, source_media: Path) -> None:
    fake = FakeKling(); fake.candidate = source_media
    service, response = lip_sync_fixture(tmp_path, source_media, fake)
    result = complete(service, response)
    assert result.run.quality_status == "TECHNICAL_FAILED"
    assert Path(result.run.candidate_media_path or "").is_file()
    with pytest.raises(ValueError, match="技术检查"):
        service.adopt_lip_sync(result.run.project_id, result.run.id)


def test_stale_take_duration_cannot_freeze_missing_source_frames(tmp_path: Path, source_media: Path) -> None:
    short_source = tmp_path / "short-source.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(source_media),
        "-t", "5", "-c", "copy", str(short_source)], check=True, capture_output=True)
    fake = FakeKling()
    service, response = lip_sync_fixture(tmp_path, short_source, fake, speed=2)
    result = service.advance_task(response.task.id)
    assert result.status == TaskStatus.FAILED and "实际源视频时长" in (result.error_message or "")
    assert not fake.uploads and not fake.posts
