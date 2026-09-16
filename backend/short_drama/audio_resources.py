"""Explicit, optional preparation of local audio models; status never uses the network."""
from __future__ import annotations

import json
import logging
import errno
from pathlib import Path
from threading import Lock, Thread
from typing import Literal, Protocol, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)
AudioResourceId = Literal["transcription", "speaker"]


class AudioResource(BaseModel):
    id: AudioResourceId
    label: str
    model: str | None
    status: Literal["ready", "not_prepared", "preparing", "failed", "disabled"]
    cache_path: str | None = None
    missing_files: list[str] = []
    error: str | None = None
    can_prepare: bool = True


class AudioResources(BaseModel):
    supported: bool = True
    resources: list[AudioResource] = []
    message: str = "声音资源用于对白听审、字幕对齐和音色比较。播放、剪辑、导出无需准备。缓存可用只表示所需文件已齐备，识别效果在实际使用时验证。"


class AudioResourcePrepare(BaseModel):
    resource: AudioResourceId


class AudioResourceStore(Protocol):
    def locate(self, model: str) -> Path | None: ...
    def prepare(self, model: str) -> Path: ...


def _local_model(model: str) -> bool:
    return Path(model).expanduser().is_dir() or model.startswith(("/", "~", "./", "../"))


def _has_weights(folder: Path) -> bool:
    for name in ("model.safetensors", "pytorch_model.bin"):
        file = folder / name
        if file.is_file() and file.stat().st_size > 0:
            return True
    for name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        try:
            value = cast(dict[str, object], json.loads((folder / name).read_text()))
            weights = value.get("weight_map")
            if not isinstance(weights, dict) or not weights:
                continue
            shards = cast(dict[str, object], weights).values()
            if all(isinstance(shard, str) and (folder / shard).is_file()
                   and (folder / shard).stat().st_size > 0 for shard in shards):
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def missing_audio_files(folder: Path | None, resource: AudioResourceId) -> list[str]:
    required = ["config.json", "preprocessor_config.json"]
    if resource == "transcription":
        required.extend(("tokenizer_config.json", "generation_config.json"))
    missing = [name for name in required if folder is None or not (folder / name).is_file()]
    if resource == "transcription" and (folder is None or not (
        (folder / "tokenizer.json").is_file()
        or ((folder / "vocab.json").is_file() and (folder / "merges.txt").is_file())
    )):
        missing.append("tokenizer.json 或 vocab.json + merges.txt")
    if folder is None or not _has_weights(folder):
        missing.append("完整模型权重")
    return missing


def _preparation_error_message(error: Exception) -> str:
    from huggingface_hub.errors import OfflineModeIsEnabled
    from requests.exceptions import ConnectionError as RequestConnectionError, Timeout as RequestTimeout

    # snapshot_download wraps offline/connection errors in LocalEntryNotFoundError.
    # Classify the original cause instead of showing the wrapper's English message.
    causes: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in causes:
        causes.append(current)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    if any(isinstance(item, OfflineModeIsEnabled) for item in causes):
        message = "当前处于离线模式，且本机尚无完整声音资源。请联网并关闭离线模式后重新启动应用，再重试准备。"
    elif any(isinstance(item, (ConnectionError, TimeoutError, RequestConnectionError, RequestTimeout)) for item in causes):
        message = "无法连接声音资源下载服务，或连接已超时。请检查网络与代理设置后重试；如仍失败，请打开日志查看原因。"
    elif any(isinstance(item, PermissionError) for item in causes):
        message = "无法写入声音资源缓存。请检查缓存目录的写入权限后重试；具体目录可在日志中查看。"
    elif any(isinstance(item, OSError) and item.errno == errno.ENOSPC for item in causes):
        message = "存储空间不足，无法保存声音资源。请释放磁盘空间后重试。"
    elif any(isinstance(item, FileNotFoundError) for item in causes):
        message = "声音资源所需文件未找到，本机缓存尚不完整。请检查网络后重试准备；如仍失败，请打开日志查看缺失内容。"
    else:
        message = "声音资源准备失败。请重试准备；如仍失败，请打开日志查看具体原因。"
    return message + "播放、剪辑和导出仍可继续。"


class HuggingFaceAudioResourceStore:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir

    def locate(self, model: str) -> Path | None:
        if _local_model(model):
            folder = Path(model).expanduser()
            return folder if folder.is_dir() else None
        from huggingface_hub import try_to_load_from_cache

        config = try_to_load_from_cache(model, "config.json", cache_dir=self.cache_dir)
        return Path(config).parent if isinstance(config, str) else None

    def prepare(self, model: str) -> Path:
        if _local_model(model):
            return Path(model).expanduser()
        from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

        # Prefer one weight format, without downloading unused TF/Flax/ONNX copies.
        folder = Path(snapshot_download(repo_id=model, cache_dir=self.cache_dir,
            allow_patterns=["*.json", "*.txt", "*.model", "model*.safetensors"]))
        if not _has_weights(folder):
            snapshot_download(repo_id=model, cache_dir=self.cache_dir, revision=folder.name,
                allow_patterns=["pytorch_model*.bin"])
        return folder


class AudioResourceManager:
    def __init__(self, transcription_model: str, speaker_model: str | None,
                 *, store: AudioResourceStore | None = None) -> None:
        self._models: dict[AudioResourceId, str | None] = {
            "transcription": transcription_model, "speaker": speaker_model,
        }
        self._store = store or HuggingFaceAudioResourceStore()
        self._lock = Lock()
        self._preparing: set[AudioResourceId] = set()
        self._errors: dict[AudioResourceId, str] = {}

    def resource(self, resource: AudioResourceId) -> AudioResource:
        model = self._models[resource]
        label = "对白识别与字幕对齐" if resource == "transcription" else "音色比较"
        if model is None:
            return AudioResource(id=resource, label=label, model=None, status="disabled", can_prepare=False)
        # Cache metadata and file reads stay outside the state lock.
        folder = self._store.locate(model)
        missing = missing_audio_files(folder, resource)
        with self._lock:
            preparing, error = resource in self._preparing, self._errors.get(resource)
        status = "preparing" if preparing else "failed" if error else "not_prepared" if missing else "ready"
        return AudioResource(id=resource, label=label, model=model, status=status,
            cache_path=str(folder) if folder else None, missing_files=missing, error=error,
            can_prepare=not _local_model(model))

    def status(self) -> AudioResources:
        return AudioResources(resources=[self.resource("transcription"), self.resource("speaker")])

    def require_cached(self, resource: AudioResourceId) -> str:
        state = self.resource(resource)
        if state.missing_files or state.status == "disabled" or state.cache_path is None:
            detail = "正在准备，请稍后重试" if state.status == "preparing" else "尚未准备完整"
            raise ValueError(f"{state.label}资源{detail}（{state.model}）。请在工作台设置 → 模型与连接 → 声音分析资源中准备或重试。"
                             + (f" 本地目录缺少：{'、'.join(state.missing_files)}" if not state.can_prepare else ""))
        return state.cache_path

    def prepare(self, request: AudioResourcePrepare) -> AudioResources:
        resource = request.resource
        current = self.resource(resource)
        if current.model is None:
            raise ValueError("当前未配置此声音资源")
        if not current.can_prepare and current.missing_files:
            raise ValueError(f"本地模型目录缺少：{'、'.join(current.missing_files)}。请补齐目录后刷新。")
        with self._lock:
            if resource in self._preparing:
                start = False
            else:
                self._errors.pop(resource, None)
                start = bool(current.missing_files)
                if start:
                    self._preparing.add(resource)
        if start:
            Thread(target=self._prepare, args=(resource, current.model), daemon=True,
                   name=f"studio-audio-{resource}").start()
        return self.status()

    def _prepare(self, resource: AudioResourceId, model: str) -> None:
        error: str | None = None
        try:
            folder = self._store.prepare(model)
            missing = missing_audio_files(folder, resource)
            if missing:
                error = (f"声音资源尚未准备完整，仍缺少：{'、'.join(missing)}。请重试准备；"
                         "如仍失败，请打开日志查看原因。播放、剪辑和导出仍可继续。")
                logger.error("Audio resource preparation incomplete: %s (%s), missing=%s", resource, model, missing)
        except Exception as exc:
            error = _preparation_error_message(exc)
            logger.exception("Audio resource preparation failed: %s (%s)", resource, model)
        finally:
            with self._lock:
                self._preparing.discard(resource)
                if error:
                    self._errors[resource] = error


def audio_resource_manager(semantic_service: object) -> AudioResourceManager | None:
    manager = getattr(semantic_service, "audio_resources", None)
    return manager if isinstance(manager, AudioResourceManager) else None
