"""Optional ComfyUI/MiniMax plugin service; workflows and outputs belong to the project."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Thread
import time
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx
from pydantic import Field, JsonValue
from short_drama.models import AssetKind, DomainModel
from short_drama.studio_media import import_studio_media

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class ComfyConfigUpdate(DomainModel):
    base_url: str = Field(default="http://127.0.0.1:8188", max_length=2000)


class ComfyConfig(DomainModel):
    base_url: str
    connected: bool
    message: str


class ComfyInputBinding(DomainModel):
    node_id: str
    input_name: str
    asset_version_id: str


class ComfyRunRequest(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    workflow: dict[str, JsonValue]
    bindings: list[ComfyInputBinding] = Field(default_factory=list[ComfyInputBinding])


class ComfyRun(DomainModel):
    id: str
    project_id: str
    name: str
    base_url: str
    status: str = "queued"
    prompt_id: str | None = None
    output_asset_ids: list[str] = Field(default_factory=list[str])
    error: str | None = None
    input_path: str | None = None
    created_at: str


def _config_path(service: ShortDramaService) -> Path:
    return service.repository.database_path.parent / "short-drama-comfyui-settings.json"


def comfy_config(service: ShortDramaService, update: ComfyConfigUpdate | None = None) -> ComfyConfig:
    path = _config_path(service)
    value = update or (ComfyConfigUpdate.model_validate_json(path.read_text()) if path.exists() else ComfyConfigUpdate())
    address = value.base_url.strip().rstrip("/")
    parsed = urlparse(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("请填写有效的 ComfyUI HTTP 地址。")
    if update:
        path.write_text(ComfyConfigUpdate(base_url=address).model_dump_json(), encoding="utf-8")
    try:
        response = httpx.get(address + "/system_stats", timeout=4)
        response.raise_for_status()
        body: object = response.json()
        if not isinstance(body, dict) or "system" not in body:
            raise ValueError("该地址未返回 ComfyUI 系统信息。")
        return ComfyConfig(base_url=address, connected=True, message="已连接 ComfyUI，可执行高级工作流。")
    except (httpx.HTTPError, ValueError) as exc:
        return ComfyConfig(base_url=address, connected=False, message=f"未连接：{str(exc)[:240]}")


def _save(service: ShortDramaService, record: ComfyRun) -> None:
    with service.studio.lock:
        document = service.repository.get_project(record.project_id)
        if document is None:
            raise KeyError(record.project_id)
        value = cast(dict[str, JsonValue], record.model_dump(mode="json"))
        previous = next((i for i, item in enumerate(document.comfy_workflows) if item.get("id") == record.id), None)
        if previous is None:
            document.comfy_workflows.append(value)
        else:
            document.comfy_workflows[previous] = value
        service.repository.save_project(document)
        service.repository.append_studio_event(record.project_id, "workflow.updated", value)


def get_comfy_run(service: ShortDramaService, project_id: str, workflow_id: str) -> ComfyRun:
    value = next((item for item in service.get_project(project_id).document.comfy_workflows if item.get("id") == workflow_id), None)
    if value is None:
        raise KeyError(workflow_id)
    return ComfyRun.model_validate(value)


def run_comfy(service: ShortDramaService, project_id: str, request: ComfyRunRequest, *, background: bool = True) -> ComfyRun:
    service.get_project(project_id)
    if not request.workflow or any(not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict) for node in request.workflow.values()):
        raise ValueError("请在 ComfyUI 中导出 API 格式工作流 JSON，再导入这里。")
    config = comfy_config(service)
    if not config.connected:
        raise ValueError(config.message)
    record = ComfyRun(id=f"workflow-{uuid4().hex[:12]}", project_id=project_id, name=request.name,
        base_url=config.base_url, created_at=datetime.now(UTC).isoformat())
    _save(service, record)
    if background:
        Thread(target=_execute, args=(service, request, record), daemon=True, name=record.id).start()
    else:
        _execute(service, request, record)
    return record


def _execute(service: ShortDramaService, request: ComfyRunRequest, record: ComfyRun) -> None:
    folder = service.workspace_root / record.project_id / "workflows" / record.id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        graph = json.loads(json.dumps(request.workflow))
        document = service.get_project(record.project_id).document
        with httpx.Client(base_url=record.base_url, timeout=30) as client:
            if not record.prompt_id:
                for binding in request.bindings:
                    version = next((version for asset in document.assets for version in asset.versions if version.id == binding.asset_version_id), None)
                    if version is None or not version.local_path:
                        raise ValueError("工作流绑定的项目素材未找到。")
                    if binding.node_id not in graph or binding.input_name not in graph[binding.node_id]["inputs"]:
                        raise ValueError("工作流的输入位置不存在，请重新选择素材输入。")
                    source = Path(version.local_path)
                    with source.open("rb") as handle:
                        uploaded = client.post("/upload/image", files={"image": (f"{record.id}-{source.name}", handle)}, data={"overwrite": "true"})
                    uploaded.raise_for_status()
                    uploaded_data = cast(dict[str, JsonValue], uploaded.json())
                    filename = str(uploaded_data["name"])
                    subfolder = str(uploaded_data.get("subfolder", ""))
                    graph[binding.node_id]["inputs"][binding.input_name] = f"{subfolder}/{filename}" if subfolder else filename
                source_file = folder / "workflow.json"
                source_file.write_text(json.dumps({"request": request.model_dump(mode="json"), "submitted_graph": graph}, ensure_ascii=False, indent=2), encoding="utf-8")
                record.input_path = str(source_file)
                record.status = "submitting"
                _save(service, record)
                response = client.post("/prompt", json={"prompt": graph, "client_id": record.id})
                if response.status_code >= 400:
                    raise ValueError(f"ComfyUI 未接受工作流：{response.text[-1200:]}")
                submitted = cast(dict[str, JsonValue], response.json())
                record.prompt_id = str(submitted["prompt_id"])
                record.status = "running"
                _save(service, record)
            while True:
                response = client.get(f"/history/{quote(record.prompt_id, safe='')}")
                response.raise_for_status()
                history = cast(dict[str, JsonValue], response.json())
                finished = history.get(record.prompt_id)
                if isinstance(finished, dict):
                    (folder / "result.json").write_text(json.dumps(finished, ensure_ascii=False, indent=2), encoding="utf-8")
                    status = finished.get("status")
                    if isinstance(status, dict) and status.get("status_str") == "error":
                        raise ValueError(f"ComfyUI 执行失败：{str(status.get('messages', ''))[-1000:]}")
                    outputs = finished.get("outputs")
                    if isinstance(outputs, dict):
                        for node_id, output in outputs.items():
                            if not isinstance(output, dict):
                                continue
                            for output_kind in ("images", "gifs", "audio", "videos"):
                                files = output.get(output_kind)
                                if not isinstance(files, list):
                                    continue
                                for file in files:
                                    if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
                                        continue
                                    filename = str(file["filename"])
                                    content = client.get("/view", params={"filename": filename, "subfolder": str(file.get("subfolder", "")), "type": str(file.get("type", "output"))})
                                    content.raise_for_status()
                                    local = folder / f"{node_id}-{Path(filename).name}"
                                    local.write_bytes(content.content)
                                    before = {asset.id for asset in service.get_project(record.project_id).document.assets}
                                    updated = import_studio_media(service, record.project_id, local, name=f"{request.name} · {Path(filename).stem}", kind=AssetKind.REFERENCE, asset_id=None, provenance=f"ComfyUI 工作流 {record.id} 的实际输出；来源为 {record.base_url}，未进行模型内容审校。")
                                    record.output_asset_ids.extend(asset.id for asset in updated.document.assets if asset.id not in before)
                    if not record.output_asset_ids:
                        raise ValueError("工作流已结束，但没有可导入的图片、视频或音频输出。请添加对应保存节点。")
                    break
                time.sleep(1.5)
        record.status = "completed"
    except Exception as exc:
        record.status, record.error = "failed", str(exc)
    _save(service, record)


def recover_interrupted_workflows(service: ShortDramaService, *, background: bool = True) -> None:
    """Resume a known external job without submitting or uploading it a second time."""
    for document in service.repository.list_projects():
        for value in document.comfy_workflows:
            record = ComfyRun.model_validate(value)
            if record.status not in {"queued", "submitting", "running"}:
                continue
            if not record.prompt_id or not record.input_path:
                record.status = "failed"
                record.error = "工作台重启时尚未记录 ComfyUI 任务编号。请先在 ComfyUI 查看任务结果，再决定是否重新执行。"
                _save(service, record)
                continue
            try:
                request = ComfyRunRequest.model_validate(json.loads(Path(record.input_path).read_text())["request"])
            except (OSError, ValueError, KeyError) as exc:
                record.status, record.error = "failed", f"无法恢复工作流输入：{exc}"
                _save(service, record)
                continue
            if background:
                Thread(target=_execute, args=(service, request, record), daemon=True, name=record.id).start()
            else:
                _execute(service, request, record)
