"""Offline cache checks and explicit preparation failure/retry, without model downloads."""
from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
import pytest
from requests.exceptions import ConnectionError as RequestConnectionError, Timeout as RequestTimeout

from _routes.studio import router
from short_drama.audio_inspection import TransformersAudioSemanticService
from short_drama.audio_resources import AudioResourceManager, AudioResourcePrepare, HuggingFaceAudioResourceStore
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService


def cache_files(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "preprocessor_config.json", "tokenizer_config.json", "generation_config.json", "tokenizer.json"):
        (folder / name).write_text("{}")
    # A tiny fixture establishes file availability only, never real model inference.
    (folder / "model.safetensors").write_bytes(b"test-cache-file")


class ControlledResourceStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.started = Event()
        self.release = Event()
        self.calls = 0
        self.fail = True

    def locate(self, model: str) -> Path | None:
        path = self.folder / model.replace("/", "--")
        return path if path.is_dir() else None

    def prepare(self, model: str) -> Path:
        self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        if self.fail:
            raise PermissionError("Test preparation: cache directory is not writable")
        path = self.folder / model.replace("/", "--")
        cache_files(path)
        return path


def settled(manager: AudioResourceManager) -> str:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = manager.resource("transcription").status
        if status != "preparing":
            return status
        time.sleep(.01)
    raise AssertionError("Preparation did not finish")


def test_preparation_is_explicit_nonblocking_deduplicated_and_retryable(tmp_path: Path) -> None:
    store = ControlledResourceStore(tmp_path / "cache")
    manager = AudioResourceManager("org/asr", "org/speaker", store=store)
    assert [item.status for item in manager.status().resources] == ["not_prepared", "not_prepared"]
    assert store.calls == 0
    request = AudioResourcePrepare(resource="transcription")
    manager.prepare(request)
    assert store.started.wait(1)
    assert manager.resource("transcription").status == "preparing"
    manager.prepare(request)
    assert store.calls == 1
    # State reads and unrelated project saves complete while the slow preparation is blocked.
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    document.project.name = "准备期间的人工编辑"
    service.repository.save_project(document)
    assert service.get_project(document.project.id).document.project.name == document.project.name
    store.release.set()
    assert settled(manager) == "failed"
    assert "缓存目录的写入权限" in (manager.resource("transcription").error or "")
    store.fail = False
    manager.prepare(request)
    assert settled(manager) == "ready"
    assert store.calls == 2 and manager.resource("transcription").error is None
    assert manager.resource("speaker").status == "not_prepared"
    manager.prepare(request)
    assert store.calls == 2, "Ready resources must not re-download"
    reopened = AudioResourceManager("org/asr", None, store=store)
    assert reopened.resource("transcription").status == "ready"
    assert reopened.resource("speaker").status == "disabled"


def test_partial_local_cache_does_not_look_ready_or_attempt_download(tmp_path: Path) -> None:
    cache_files(tmp_path)
    (tmp_path / "model.safetensors").unlink()
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"layer": "model-1.safetensors"}}))
    manager = AudioResourceManager(str(tmp_path), None)
    state = manager.resource("transcription")
    assert state.status == "not_prepared" and "完整模型权重" in state.missing_files and not state.can_prepare
    with pytest.raises(ValueError, match="本地模型目录缺少"):
        manager.prepare(AudioResourcePrepare(resource="transcription"))
    (tmp_path / "model-1.safetensors").write_bytes(b"fixture-shard")
    assert manager.resource("transcription").status == "ready"
    assert manager.require_cached("transcription") == str(tmp_path)


def test_empty_hf_home_stays_offline_and_analysis_reports_required_resource(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key not in {
        "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"}}
    env.update(HF_HOME=str(tmp_path / "empty-hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run([sys.executable, "-c", """
import json
from short_drama.audio_inspection import TransformersAudioSemanticService
service = TransformersAudioSemanticService()
states = service.audio_resources.status()
assert all(item.status == 'not_prepared' for item in states.resources)
try:
    service._load_transcriber()
except ValueError as error:
    assert '工作台设置' in str(error) and 'whisper-small' in str(error)
else:
    raise AssertionError('Empty cache was accepted')
print(states.model_dump_json())
"""], env=env, capture_output=True, text=True, timeout=20, check=True)
    assert "not_prepared" in result.stdout
    assert not list((tmp_path / "empty-hf").rglob("*.incomplete"))


def test_resource_routes_expose_failure_retry_and_unsupported_service(tmp_path: Path) -> None:
    audio = TransformersAudioSemanticService(transcription_model="org/asr", speaker_model=None)
    store = ControlledResourceStore(tmp_path / "cache")
    audio.audio_resources = AudioResourceManager("org/asr", None, store=store)
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "media", audio_semantic_service=audio)
    app = FastAPI()
    app.include_router(router, prefix="/api/short-drama")
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        response = client.get("/api/short-drama/studio/audio-resources")
        assert response.status_code == 200 and response.json()["resources"][0]["status"] == "not_prepared"
        response = client.post("/api/short-drama/studio/audio-resources/prepare", json={"resource": "transcription"})
        assert response.status_code == 202 and response.json()["resources"][0]["status"] == "preparing"
        store.release.set()
        assert settled(audio.audio_resources) == "failed"
        assert client.get("/api/short-drama/studio/audio-resources").json()["resources"][0]["error"]
        store.fail = False
        assert client.post("/api/short-drama/studio/audio-resources/prepare", json={"resource": "transcription"}).status_code == 202
        assert settled(audio.audio_resources) == "ready"
        service.audio_semantic_service = None
        assert client.get("/api/short-drama/studio/audio-resources").json()["supported"] is False
        assert client.post("/api/short-drama/studio/audio-resources/prepare", json={"resource": "transcription"}).status_code == 422


def test_default_cache_check_is_read_only(tmp_path: Path) -> None:
    cache = tmp_path / "empty"
    store = HuggingFaceAudioResourceStore(cache)
    assert store.locate("openai/whisper-small") is None
    assert not cache.exists()


def wrapped_download_failure(cause: Exception) -> Exception:
    error = LocalEntryNotFoundError("Cannot find an appropriate cached snapshot; raw download failure")
    error.__cause__ = cause
    return error


class FailingResourceStore:
    def __init__(self, folder: Path, failure: Exception | None) -> None:
        self.folder = folder
        self.failure = failure

    def locate(self, model: str) -> Path | None:
        return self.folder if self.folder.is_dir() else None

    def prepare(self, model: str) -> Path:
        if self.failure is not None:
            raise self.failure
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "config.json").write_text("{}")
        return self.folder


@pytest.mark.parametrize(("failure", "expected"), [
    (wrapped_download_failure(OfflineModeIsEnabled("HF_HUB_OFFLINE is enabled")), "关闭离线模式后重新启动应用"),
    (wrapped_download_failure(RequestConnectionError("Failed to establish a new connection")), "检查网络与代理设置"),
    (RequestTimeout("Download timed out"), "连接已超时"),
    (PermissionError("Permission denied"), "缓存目录的写入权限"),
    (OSError(errno.ENOSPC, "No space left on device"), "释放磁盘空间"),
    (FileNotFoundError("Missing model file"), "所需文件未找到"),
    (RuntimeError("Unexpected remote response"), "打开日志查看具体原因"),
])
def test_preparation_errors_are_actionable_chinese_and_original_stays_in_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: Exception, expected: str,
) -> None:
    manager = AudioResourceManager("org/asr", None, store=FailingResourceStore(tmp_path / "cache", failure))
    manager.prepare(AudioResourcePrepare(resource="transcription"))
    assert settled(manager) == "failed"
    state = manager.resource("transcription")
    assert expected in (state.error or "")
    assert "播放、剪辑和导出仍可继续" in (state.error or "")
    assert str(failure) not in (state.error or "")
    assert state.missing_files and state.cache_path is None
    with pytest.raises(ValueError, match="尚未准备完整"):
        manager.require_cached("transcription")
    assert str(failure) in caplog.text
    records = [record for record in caplog.records if record.name == "short_drama.audio_resources"]
    assert len(records) == 1 and records[0].exc_info is not None
    if failure.__cause__ is not None:
        assert str(failure.__cause__) in caplog.text


def test_download_returning_partial_files_explains_missing_files_without_claiming_ready(tmp_path: Path) -> None:
    manager = AudioResourceManager("org/asr", None, store=FailingResourceStore(tmp_path / "cache", None))
    manager.prepare(AudioResourcePrepare(resource="transcription"))
    assert settled(manager) == "failed"
    state = manager.resource("transcription")
    assert state.error and "仍缺少" in state.error and "重试准备" in state.error
    assert all(name in state.error for name in state.missing_files)
    assert "config.json" not in state.missing_files
    with pytest.raises(ValueError, match="尚未准备完整"):
        manager.require_cached("transcription")
