"""Observe real product-Agent video execution using previously saved H3 plans.

The runner owns recording and process recovery. Only normal Agent APIs originate
production changes; provider requests and persisted task transitions remain in
the existing H3 worker. No images, replacement takes or mocked media are created.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import base64
import mimetypes
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Never
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import Image
from requests import PreparedRequest, Response, Session

from short_drama.agent_runtime.models import AgentRunStatus
from short_drama.audio_inspection import TransformersAudioSemanticService
from short_drama.h3_models import H3MediaKind
from short_drama.h3_route_planner import validate_route_plan_freshness
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.minimax_h3_client import MiniMaxH3Client, h3_image_transport_size
from short_drama.service import ShortDramaService
from validate_agent_video_preparation_run import RecordingLanguageClient, TraceRecorder, snapshot, utc_now, write_json


DEFAULT_OBJECTIVE = """
继续当前已完成视频准备的校园短剧。本 Run 的具体工作是执行第 001 个
GenerationUnit（clip-group-independent-v7-001，13 秒，3 个镜头）的已有
H3RoutePlan，回收真实视频，并进行实际媒体检查，为继续其余 11 组提供结果。
用户已授权从准备阶段继续视频生成，真实 H3 和语言模型调用均获准。

完整 Script、Bible、ProductionSpec、canonical Storyboard、AssetPlan 和已批准
媒体都是当前已确认输入，GenerationPlan v3 和每组完整 H3RoutePlan 已保存。
先获取路线索引，再读取本组当前持久化状态，直接执行该方案，沿用原 Prompt、
有序媒体、时长和 candidate_count=1；不重新编译计划/提示词或制作资产。
route.execute_h3_plan 只入队，后续用 task.advance 推进真实持久化任务。
本组完成前不执行其他组，不增加候选或新付费重试。

不要把前一阶段“停在 H3 前”的阶段目标当作当前目标，也不要使用 MOCK。
当前只做真实视频回收和必要检查，不生成独立配音、字幕、配乐或整集导出。
视频请求应保留已有原生对白、动作声和环境声要求。检查下载结果的实际媒体
属性及画面；技术通过不等于语义和对白全部通过，没有实际观察的结论保留未知。

对 SUBMIT_UNKNOWN 先使用对应 H3 reconciliation 工具查询，不直接 retry 或
猜测没提交；OUTPUT_MISSING 使用原任务 recover_output，不重新付费生成。
明确内容过滤或其他 Provider 拒绝应保留完整错误并向 Leader 返回准确状态，
不反复修改提示词尝试。普通网络查询/下载问题可在原任务上恢复。

由 Agent 自主选择必要的工具、角色和检查安排。若成功回收到首组视频并保存
检查结果，或者出现需要明确告知用户的 Provider 阻塞，给出具体任务、工件和
检查依据后结束本 Run。不要因为只是首组而宣称 131 秒整片已完成。
""".strip()


class VideoTraceRecorder(TraceRecorder):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._http_sequence = max(
            (int(path.name) for path in (root / "provider_calls").glob("[0-9]*")), default=0,
        )

    def begin_http(self, request: PreparedRequest, timeout: Any) -> Path:
        with self._lock:
            self._http_sequence += 1
            call = self.root / "provider_calls" / f"{self._http_sequence:05d}"
            call.mkdir(parents=True)
        body = request.body
        raw = body.encode("utf-8") if isinstance(body, str) else body if isinstance(body, bytes) else b""
        if raw:
            (call / "request-body.json").write_bytes(raw)
        url = urlsplit(request.url or "")
        metadata = {
            "at": utc_now(), "method": request.method,
            "url": request.url, "body_bytes": len(raw), "timeout_seconds": timeout,
            "content_type": request.headers.get("Content-Type"),
        }
        # Authorization headers are intentionally absent from both trace and events.
        write_json(call / "request.json", metadata)
        self.event("h3_http_start", call=call.name, method=request.method,
                   endpoint=f"{url.scheme}://{url.netloc}{url.path}", body_bytes=len(raw), timeout=timeout)
        return call


class RecordingH3Session(Session):
    def __init__(self, recorder: VideoTraceRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def send(self, request: PreparedRequest, **kwargs: Any) -> Response:
        call = self.recorder.begin_http(request, kwargs.get("timeout"))
        started = time.monotonic()
        result: dict[str, Any] = {"success": False}
        try:
            response = super().send(request, **kwargs)
            result = {"success": response.ok, "status_code": response.status_code,
                      "content_type": response.headers.get("Content-Type")}
            if not kwargs.get("stream"):
                (call / "response-body.txt").write_bytes(response.content)
                result["response_bytes"] = len(response.content)
            return response
        except BaseException as exc:
            result.update(error_kind=type(exc).__name__, error=str(exc))
            raise
        finally:
            result.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic() - started, 3))
            write_json(call / "result.json", result)
            self.recorder.event("h3_http_end", call=call.name, **result)


class RecordingH3Client(MiniMaxH3Client):
    def __init__(self, recorder: VideoTraceRecorder, api_key: str) -> None:
        super().__init__(api_key=api_key, session=RecordingH3Session(recorder))
        self.recorder = recorder

    def upload_file(self, path: Path) -> str:
        reference = super().upload_file(path)
        geometry: dict[str, Any] | None = None
        if (mimetypes.guess_type(path.name)[0] or "").startswith("image/"):
            with Image.open(path) as source:
                transport_size = h3_image_transport_size(source.width, source.height)
                geometry = {"source_size": source.size, "transport_size": transport_size,
                            "padded": transport_size != source.size}
            if geometry["padded"]:
                encoded_path = self.recorder.root / "provider_inputs" / f"{uuid4().hex}.png"
                encoded_path.parent.mkdir(parents=True, exist_ok=True)
                encoded_path.write_bytes(base64.b64decode(reference.split(",", 1)[1]))
                geometry["encoded_path"] = str(encoded_path)
        self.recorder.event("h3_local_input_encoded", path=str(path), source_bytes=path.stat().st_size,
                            encoded_characters=len(reference), image_transport=geometry, network_called=False)
        return reference

    def download(self, url: str, destination: Path) -> None:
        super().download(url, destination)
        self.recorder.event("h3_output_downloaded", path=str(destination), bytes=destination.stat().st_size)


class VideoSnapshots:
    def __init__(self, service: ShortDramaService, run_id: str, root: Path) -> None:
        self.service, self.run_id, self.root = service, run_id, root
        self._lock = threading.RLock()
        self._saved: dict[tuple[str, str], str] = {}

    def capture(self) -> dict[str, Any]:
        with self._lock:
            summary = snapshot(self.service, self.run_id, self.root)
            project_id = self.service.get_agent_run(self.run_id).snapshot.run.project_id
            repository = self.service.repository
            tasks = repository.list_tasks(project_id)
            collections = {
                "tasks": tasks, "provider_runs": repository.list_provider_runs(project_id),
                "takes": repository.list_takes(project_id),
                "video_inspections": repository.list_video_inspections(project_id),
                "audio_inspections": repository.list_audio_inspections(project_id),
                "episode_reviews": repository.list_episode_reviews(project_id),
                "exports": repository.list_exports(project_id),
                "manifests": self.service.get_project(project_id).document.manifests,
            }
            for category, records in collections.items():
                for record in records:
                    payload = record.model_dump(mode="json")
                    # A task poll can change a Run independently of Agent actions.
                    revision = str(payload.get("updated_at") or json.dumps(payload, ensure_ascii=False))
                    key = (category, record.id)
                    if self._saved.get(key) != revision:
                        write_json(self.root / category / f"{record.id}.json", payload)
                        self._saved[key] = revision
            summary["tasks"] = [{
                "id": task.id, "unit_id": task.generation_unit_id, "status": task.status.value,
                "provider_task_id": task.provider_task_id, "error_code": task.error_code,
                "error_message": task.error_message,
            } for task in tasks]
            summary["take_count"] = len(collections["takes"])
            summary["video_inspection_count"] = len(collections["video_inspections"])
            summary["selected_take_count"] = sum(take.is_selected for take in repository.list_takes(project_id))
            summary["audio_inspection_count"] = len(collections["audio_inspections"])
            summary["episode_review_count"] = len(collections["episode_reviews"])
            summary["export_count"] = len(collections["exports"])
            write_json(self.root / "latest.json", summary)
            return summary


def preflight(service: ShortDramaService, project_id: str, root: Path) -> None:
    document = service.get_project(project_id).document
    plans = service.repository.list_h3_route_plans(project_id)
    entries: list[dict[str, Any]] = []
    for plan in plans:
        validate_route_plan_freshness(plan, document)
        for decision in plan.decisions:
            media: list[dict[str, Any]] = []
            for item in decision.media_inputs:
                path = Path(item.source.local_path) if item.source.local_path else None
                if path is not None and not path.is_file():
                    raise FileNotFoundError(str(path))
                details: dict[str, Any] = {
                    "asset_version_id": item.asset_version_id, "kind": item.kind.value,
                    "path": str(path) if path else None,
                    "source_bytes": path.stat().st_size if path else None,
                }
                if path is not None and item.kind == H3MediaKind.IMAGE:
                    with Image.open(path) as source:
                        transport_size = h3_image_transport_size(source.width, source.height)
                        details["image_transport"] = {
                            "source_size": source.size, "transport_size": transport_size,
                            "padded": transport_size != source.size,
                        }
                media.append(details)
            entries.append({"plan_id": plan.id, "state_revision": plan.state_revision,
                            "unit_id": decision.generation_unit_id, "duration_seconds": decision.duration_seconds,
                            "candidate_count": decision.candidate_count, "prompt_characters": len(decision.prompt),
                            "media": media, "readiness": decision.readiness.value})
    write_json(root / "preflight.json", {"at": utc_now(), "plans": entries,
                                        "existing_tasks": len(service.repository.list_tasks(project_id))})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", default="project-11b4f7a637c3")
    parser.add_argument("--h3-settings", type=Path, default=Path.home() / "Library/Application Support/ShortDramaStudio/short-drama-provider-settings.json")
    parser.add_argument("--objective-file", type=Path)
    parser.add_argument("--replan-reason")
    parser.add_argument("--confirmed-decision", action="append", default=[])
    parser.add_argument("--max-advances", type=int)
    parser.add_argument("--render-export", action="store_true", help="Authorize the new Run to finish and export its timeline.")
    parser.add_argument("--audio-transcription-model", type=Path, help="Use this existing local Whisper snapshot for the product audio Tool.")
    args = parser.parse_args(argv)
    root, database = args.output_dir.resolve(), args.database.resolve()
    recorder = VideoTraceRecorder(root)
    key = H3ProviderConfigStore(args.h3_settings).api_key()
    if not key:
        raise ValueError("Configure the existing desktop H3 provider before running real video generation")
    os.environ["ACEDATACLOUD_API_KEY"] = key
    backup = root / "database-before.sqlite3"
    if not backup.exists():
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)

    def block_image(*_args: object) -> Never:
        return recorder.block_media("image")

    audio_service = None
    if args.audio_transcription_model is not None:
        model_path = args.audio_transcription_model.expanduser().resolve()
        if not model_path.is_dir():
            raise FileNotFoundError(str(model_path))
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch

        torch.set_num_threads(4)
        audio_service = TransformersAudioSemanticService(
            transcription_model=str(model_path), speaker_model=None, device="cpu",
        )
        recorder.event("local_audio_service", model_path=str(model_path), device="cpu", threads=4,
                       speaker_model=None, offline=True)

    service = ShortDramaService(
        database_path=database, workspace_root=database.parent / "outputs/short-drama-projects",
        language_provider_factory=lambda key, url: RecordingLanguageClient(
            recorder, api_key=key, base_url=url, generation_timeout_seconds=180.0,
        ),
        h3_client_factory=lambda key: RecordingH3Client(recorder, key),
        image_provider_factory=block_image,
        audio_semantic_service=audio_service,
    )
    preflight(service, args.project_id, root)
    service.h3_worker.validate_credentials()  # Read-only query; no generation task is created.
    session_path = root / "session.json"
    session: dict[str, Any] = json.loads(session_path.read_text()) if session_path.exists() else {}
    if session and (session.get("database") != str(database) or session.get("project_id") != args.project_id):
        raise ValueError("This trace directory belongs to a different project/database")
    run_id: str | None = session.get("run_id")
    if run_id is None:
        write_json(root / "project-before.json", service.get_project(args.project_id).model_dump(mode="json"))
        objective = args.objective_file.read_text() if args.objective_file else DEFAULT_OBJECTIVE
        view = service.create_agent_run(args.project_id, AgentRunCreateRequest(
            objective=objective, root_agent="video-finishing-agent" if args.render_export else "video-generation-agent", auto_plan=False,
            unattended=True, execution_target=ProductionExecutionTarget.REAL_H3, render_export=args.render_export,
        ))
        run_id = view.snapshot.run.id
        session = {"started_at": utc_now(), "database": str(database), "project_id": args.project_id,
                   "run_id": run_id, "objective": objective, "h3_io": "enabled",
                   "h3_settings_path": str(args.h3_settings), "language_model": "gpt-5.6-sol",
                   "render_export": args.render_export,
                   "audio_transcription_model": str(args.audio_transcription_model) if args.audio_transcription_model else None}
        write_json(session_path, session)
    recorder.event("run_attached", run_id=run_id, project_id=args.project_id)
    snapshots = VideoSnapshots(service, run_id, root)
    stop_monitor = threading.Event()

    def observe() -> None:
        while not stop_monitor.wait(10):
            try:
                snapshots.capture()
            except Exception as exc:
                recorder.event("snapshot_error", error=str(exc))

    monitor = threading.Thread(target=observe, name="video-trace-observer", daemon=True)
    monitor.start()
    exit_code = 0
    try:
        view = service.get_agent_run(run_id)
        if args.replan_reason or view.snapshot.current_plan is None:
            reason = args.replan_reason or (
                "Finish the authorized complete video using the existing real Takes."
                if args.render_export else "Continue the authorized real H3 execution stage using saved inputs."
            )
            recorder.event("operator_replan", reason=reason, confirmed_decisions=args.confirmed_decision)
            service.replan_agent_run(run_id, AgentRunReplanRequest(
                reason=reason, confirmed_decisions=args.confirmed_decision,
            ))
        advances = 0
        while True:
            summary = snapshots.capture()
            recorder.event("agent_progress", **summary)
            if summary["status"] in {AgentRunStatus.COMPLETED.value, AgentRunStatus.FAILED.value, AgentRunStatus.CANCELLED.value}:
                break
            view = service.get_agent_run(run_id)
            if any(item.kind.value == "USER_INPUT" and item.status.value == "RUNNING" for item in view.snapshot.actions):
                recorder.event("needs_input", run_id=run_id)
                exit_code = 2
                break
            if args.max_advances is not None and advances >= args.max_advances:
                recorder.event("observation_batch_complete", advances=advances)
                break
            service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
            advances += 1
    except (Exception, KeyboardInterrupt) as exc:
        recorder.event("run_interrupted", error_kind=type(exc).__name__, error=str(exc))
        exit_code = 1
    finally:
        stop_monitor.set()
        monitor.join(timeout=15)
        snapshots.capture()
        write_json(root / "project.json", service.get_project(args.project_id).model_dump(mode="json"))
        recorder.event("process_finished", exit_code=exit_code, run_id=run_id)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
