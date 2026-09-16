"""Human-readable production records and raw trace for a later presentation."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4
from pydantic import JsonValue
from short_drama.studio_trace import clean_trace_text
from short_drama.studio_package_progress import PackageProgress

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def shareable_trace(value: JsonValue) -> JsonValue:
    """Credentials are not production evidence; raw originals remain on this machine."""
    if isinstance(value, dict):
        secret_names = {"api_key", "apikey", "api-key", "authorization", "access_token", "refresh_token", "client_secret", "x-api-key"}
        return {key: "[credential omitted]" if key.lower() in secret_names else shareable_trace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shareable_trace(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[credential omitted]", clean_trace_text(value))
    return value


def _share_json(text: str) -> str:
    return json.dumps(shareable_trace(cast(JsonValue, json.loads(text))), ensure_ascii=False, indent=2)


class _TraceWriter:
    def __init__(self, archive: zipfile.ZipFile, prefix: str, progress: PackageProgress | None) -> None:
        self.archive, self.prefix, self.progress = archive, prefix, progress

    def writestr(self, name: str, text: str) -> None:
        if self.progress is None:
            self.archive.writestr(self.prefix + name, text)
            return
        self.progress.check()
        with self.archive.open(self.prefix + name, "w", force_zip64=True) as destination:
            for start in range(0, len(text), 256 * 1024):
                self.progress.check()
                data = text[start:start + 256 * 1024].encode("utf-8")
                destination.write(data)
                self.progress.advance(bytes_count=len(data))
        self.progress.advance(files=1)


def export_studio_trace(service: ShortDramaService, project_id: str) -> Path:
    folder = service.workspace_root / project_id / "trace-exports"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"production-trace-{uuid4().hex[:10]}.zip"
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            write_studio_trace(service, project_id, archive)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


def write_studio_trace(service: ShortDramaService, project_id: str, destination: zipfile.ZipFile,
                       *, prefix: str = "", progress: PackageProgress | None = None) -> None:
    """Write the same full Trace directly into a standalone or project archive."""
    project = service.get_project(project_id)
    store = service.agent_runtime.store
    archive = _TraceWriter(destination, prefix, progress)
    archive.writestr("README.md", "# 制作过程记录\n\nproject.json 是导出时当前内容；Agent 目录保留目标、计划、动作与交接；model-trace 保留实际模型输入、输出、用量与耗时。\n\n没有记录的 token 数、历史逐句资产引用或重试原因应保持未知，不能补写成已验证事实。用户补图与定向修改见 conversation.jsonl 和原始案例记录。原始媒体通过项目中本地路径定位，此过程包不重复复制大视频。\n")
    archive.writestr("project.json", _share_json(project.model_dump_json()))
    cursor = 0
    events: list[str] = []
    while True:
        batch = service.repository.list_studio_events(project_id, after=cursor, limit=1000)
        if not batch:
            break
        events.extend(item.model_dump_json() for item in batch)
        cursor = batch[-1].id
    archive.writestr("conversation.jsonl", "\n".join(json.dumps(shareable_trace(cast(JsonValue, json.loads(item))), ensure_ascii=False) for item in events))
    calls = [json.loads(item)["data"] for item in events if json.loads(item)["kind"] == "provider.language_generation"]
    summary: dict[str, JsonValue] = {"project_id": project_id, "name": project.document.project.name,
        "episode": project.document.project.episode_title, "shots": len(project.document.shots),
        "editorial_revision": (project.document.editorial_timeline or {}).get("revision", 0),
        "studio_exports": [item for item in project.document.studio_exports],
        "workflows": [{key: item.get(key) for key in ("id", "name", "status", "prompt_id", "output_asset_ids", "error", "created_at")} for item in project.document.comfy_workflows],
        "language_calls": [{key: call.get(key) for key in ("call_id", "model", "reasoning_effort", "input_tokens", "output_tokens", "total_tokens", "elapsed_seconds", "time_to_first_token_seconds")} for call in calls],
        "source_note": "本包记录本项目当前内容、实际动作与已关联的原始来源。用户补图、确认、开发修复和恢复以实际记录为准；缺失的历史用量不是 0，不补写未发生的制作过程。"}
    archive.writestr("production-summary.json", json.dumps(shareable_trace(summary), ensure_ascii=False, indent=2))
    media: list[JsonValue] = [{"asset_id": asset.id, "name": asset.name, "version_id": version.id, "description": version.descriptor,
        "path": version.local_path, "external_url": version.external_url, "type": version.media_type.value}
        for asset in project.document.assets for version in asset.versions if version.local_path or version.external_url]
    archive.writestr("media-index.json", json.dumps(shareable_trace(media), ensure_ascii=False, indent=2))
    # Real media requests also belong to the production record. These records
    # survive restarts and include the original prompt, input bindings and IDs.
    for media_task in service.repository.list_tasks(project_id):
        archive.writestr(f"providers/video/{media_task.id}/task.json", _share_json(media_task.model_dump_json()))
    for provider_run in service.repository.list_provider_runs(project_id):
        archive.writestr(f"providers/video/{provider_run.task_id}/run.json", _share_json(provider_run.model_dump_json()))
    for image_task in service.repository.list_image_tasks(project_id):
        archive.writestr(f"providers/image/{image_task.id}.json", _share_json(image_task.model_dump_json()))
    for video_edit in service.repository.list_video_edit_runs(project_id):
        archive.writestr(f"providers/video-edit/{video_edit.id}.json", _share_json(video_edit.model_dump_json()))
    for run in store.list_runs(project_id):
        prefix = f"agent/{run.id}"
        archive.writestr(f"{prefix}/snapshot.json", _share_json(service.agent_runtime.snapshot(run.id).model_dump_json()))
        archive.writestr(f"{prefix}/plans.json", json.dumps(shareable_trace([item.model_dump(mode="json") for item in store.list_plan_revisions(run.id)]), ensure_ascii=False, indent=2))
        for snapshot_id, payload in store.list_context_snapshots(run.id):
            archive.writestr(f"{prefix}/context/{snapshot_id}.json", json.dumps(shareable_trace(payload), ensure_ascii=False, indent=2))
        for work in store.list_work_orders(run_id=run.id):
            archive.writestr(f"{prefix}/workers/{work.id}.json", _share_json(work.model_dump_json()))
            result = store.get_agent_result(work.id)
            if result:
                archive.writestr(f"{prefix}/workers/{work.id}-result.json", _share_json(result.model_dump_json()))
    traces = service.workspace_root / project_id / "studio-trace"
    if traces.is_dir():
        for path in sorted(traces.rglob("*.json")):
            archive.writestr(f"model-trace/{path.relative_to(traces)}", _share_json(path.read_text(encoding="utf-8")))
    for workflow in project.document.comfy_workflows:
        prefix = f"workflows/{workflow['id']}"
        archive.writestr(f"{prefix}/record.json", json.dumps(shareable_trace(workflow), ensure_ascii=False, indent=2))
        input_path = workflow.get("input_path")
        if not isinstance(input_path, str):
            continue
        source = Path(input_path)
        for path in (source, source.with_name("result.json")):
            if path.is_file():
                archive.writestr(f"{prefix}/{path.name}", _share_json(path.read_text(encoding="utf-8")))
    source_index: list[dict[str, str | int]] = []
    for index, source in enumerate(project.document.trace_sources):
        base = Path(source)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")) if base.is_dir() else [base]:
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".txt", ".log", ".csv", ".svg"} and "settings" not in path.name:
                target = f"original-case/{index + 1}-{base.name}/{path.relative_to(base)}" if base.is_dir() else f"original-case/{index + 1}-{base.name}"
                raw = path.read_text(encoding="utf-8", errors="replace")
                try:
                    text = _share_json(raw) if path.suffix == ".json" else "\n".join(json.dumps(shareable_trace(cast(JsonValue, json.loads(line))), ensure_ascii=False) for line in raw.splitlines() if line.strip()) if path.suffix == ".jsonl" else str(shareable_trace(raw))
                except json.JSONDecodeError:
                    text = str(shareable_trace(raw))
                archive.writestr(target, text)
                source_index.append({"source": str(path), "archive_path": target, "original_bytes": path.stat().st_size})
    archive.writestr("original-sources.json", json.dumps(source_index, ensure_ascii=False, indent=2))
    archive.writestr("START_HERE.md", "# 制作记录入口\n\n先读 production-summary.json 查看本次项目和模型调用，再读 media-index.json 找到素材。conversation.jsonl 记录用户要求和实际动作；model-trace 是本次模型输入/原答，agent 保留计划与工作单。workflows 保留高级工作流的任务记录、精确输入绑定、实际提交图和已回收的服务结果。\n\noriginal-case 保留已关联的从剧本到成片的案例记录；original-sources.json 对照本机原始路径。API 凭据与重复内嵌媒体不写入分享包，原始文件仍在原目录。大图、音频和视频通过素材索引打开。\n")
