"""Run the real production Agent through persisted H3 preparation, without H3 IO.

Only the normal Agent create/replan/advance APIs originate production changes.
The recorder saves exact model text, streamed responses, returned token usage,
and persisted Agent plans/actions/Tool turns. Media provider factories stop at
the user's requested execution boundary; they never create fake media.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Never, cast

from requests import Response

from short_drama.agent_runtime.models import AgentRunStatus
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import (
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunReplanRequest,
)
from short_drama.providers.openai_compatible_language_client import (
    OpenAICompatibleLanguageClient,
    _StreamGenerationResult,
)
from short_drama.service import ShortDramaService


DEFAULT_OBJECTIVE = """
继续当前校园短剧，在当前已确认的 Script、Bible、ProductionSpec、canonical
Storyboard、AssetPlan 和已批准媒体上，完成视频生成前的全部准备，然后结束本 Run。
本阶段已经获准使用真实语言模型。停止点是实际 H3 调用之前：不向 H3 上传、
提交、轮询，不创建视频 Task/Take，不生成音频，不建立时间线或导出。

已有核心资产与逐镜引用已经完成。复用当前批准版本；本阶段不重做资产制作，
不修改已确认剧本、分镜、制作规格和资产审核决定。若发现真正缺失的必要输入，
报告精确缺口。不要把此前资产 Run 的阶段终点当成本 Run 的终点。

由 Agent 自主安排准备工作、选择当前制作方法、参考子集和视频路线。先把当前
完整 canonical Storyboard 编译为版本化 GenerationPlan，再根据每组镜头的
连续动作、可见主体、空间、对白与声音方向，准备最小充分的有序媒体输入。
ReferencePack 是可选素材库，不等于每次调用都全部上传；使用真实保存的
AssetVersion、reference role、purpose 和 subject 映射，不猜 ID 或现有标签。

route.compile_h3_prompt 已支持完整 GenerationPlan document/version/unit 目标
和 method_skill_ids。按当前组的完整来源编译，保留原文对白、时间窗、可见名单
与引用绑定；把返回的最终 compiled_prompt 和 media_inputs 交给 route.plan_h3 的
generation-unit directive（prompt_override、media_inputs 和明确 candidate_id）。
最终提示词受 H3 长度约束，完整来源不受这个输出限制；不能截断事实或私自改分镜。
明确选定的提示词与引用在保存路线时原样沿用，不重复请求模型改写。

全体 GenerationUnit 都有可执行的持久化 H3RoutePlan 后，核对当前版本和覆盖范围，
返回实际工件及尚未提交 H3 的状态并结束。不得调用 route.execute_h3_plan 或
task.advance 来试生成视频；也不使用 MOCK 视频冒充完成。需要独立配音、字幕
或配乐与否由既定制作意图决定，当前准备应保留要求的原生对白、环境声和动作声。
任务的拆分和依赖由语义决定；不设置固定工作项数量、创作确认或审核循环。
""".strip()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_object(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class TraceRecorder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._call_sequence = max(
            (int(path.name) for path in (root / "model_calls").glob("[0-9]*")),
            default=0,
        )

    def event(self, kind: str, **values: Any) -> None:
        record = {"at": utc_now(), "event": kind, **values}
        with self._lock, (self.root / "events.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def begin_call(self, request: dict[str, Any], instructions: str, input_text: str) -> Path:
        with self._lock:
            self._call_sequence += 1
            call = self.root / "model_calls" / f"{self._call_sequence:05d}"
            call.mkdir(parents=True)
        (call / "instructions.md").write_text(instructions, encoding="utf-8")
        (call / "input.txt").write_text(input_text, encoding="utf-8")
        try:
            payload = json_object(json.loads(input_text))
        except json.JSONDecodeError:
            payload = {}
        context = json_object(payload.get("context"))
        order = json_object(payload.get("work_order"))
        request.update({
            "started_at": utc_now(),
            "thread": threading.current_thread().name,
            "instruction_characters": len(instructions),
            "input_characters": len(input_text),
            "total_text_characters": len(instructions) + len(input_text),
            "work_order_id": order.get("id"),
            "input_top_level_keys": list(payload),
            "context_top_level_keys": list(context),
        })
        write_json(call / "request.json", request)
        self.event("model_start", call=call.name, **request)
        return call

    def block_media(self, provider: str) -> Never:
        self.event("media_boundary_attempt", provider=provider, network_called=False)
        raise RuntimeError(f"This run stops before {provider} provider IO, as requested by the user")


class TappedResponse:
    """Observe the same stream consumed by the production parser, without buffering it."""

    def __init__(self, response: Response, call: Path) -> None:
        self.response = response
        self.call = call

    def close(self) -> None:
        self.response.close()

    def iter_lines(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        with (self.call / "provider-stream.txt").open("a", encoding="utf-8") as target:
            for line in self.response.iter_lines(*args, **kwargs):
                text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                target.write(str(text) + "\n")
                target.flush()
                if isinstance(text, str) and text.startswith("data: "):
                    try:
                        event = json_object(json.loads(text[6:]))
                    except json.JSONDecodeError:
                        event = {}
                    response = json_object(event.get("response"))
                    if response.get("usage") is not None:
                        write_json(self.call / "usage.json", {
                            "response_id": response.get("id"),
                            "model": response.get("model"),
                            "status": response.get("status"),
                            "usage": response["usage"],
                        })
                yield line


class RecordingLanguageClient(OpenAICompatibleLanguageClient):
    def __init__(self, recorder: TraceRecorder, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.recorder = recorder
        self._current_call: ContextVar[Path | None] = ContextVar("current_model_call", default=None)

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        call = self.recorder.begin_call({
            "model": self.model,
            "base_url": self.base_url,
            "reasoning_effort": "low",
            "max_output_tokens": max_output_tokens,
            "generation_timeout_seconds": self.generation_timeout_seconds,
            "image_count": len(image_urls or []),
            "images": [
                {"data_url_header": value.split(",", 1)[0], "characters": len(value)}
                if value.startswith("data:") else {"url": value}
                for value in (image_urls or [])
            ],
        }, instructions, input_text)
        token = self._current_call.set(call)
        started = time.monotonic()
        result: dict[str, Any] = {"success": False}
        try:
            text = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            (call / "response.txt").write_text(text, encoding="utf-8")
            result = {"success": True, "output_characters": len(text)}
            return text
        except BaseException as exc:
            result = {"success": False, "error_kind": type(exc).__name__, "error": str(exc)}
            raise
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            result["finished_at"] = utc_now()
            write_json(call / "result.json", result)
            self.recorder.event("model_end", call=call.name, **result)
            self._current_call.reset(token)

    def _extract_stream_output_text(
        self,
        response: Response,
        *,
        first_text_delta_observer: Callable[[float], None],
        response_observer: Callable[[dict[str, object]], None],
        text_delta_observer: Callable[[str], None],
    ) -> _StreamGenerationResult:
        call = self._current_call.get()
        if call is not None:
            response = cast(Response, TappedResponse(response, call))
        return super()._extract_stream_output_text(
            response, first_text_delta_observer=first_text_delta_observer,
            response_observer=response_observer, text_delta_observer=text_delta_observer,
        )


_snapshot_lock = threading.RLock()


def snapshot(service: ShortDramaService, run_id: str, root: Path) -> dict[str, Any]:
    with _snapshot_lock:
        return _snapshot(service, run_id, root)


def _snapshot(service: ShortDramaService, run_id: str, root: Path) -> dict[str, Any]:
    store = service.agent_runtime.store
    view = service.get_agent_run(run_id)
    write_json(root / "run.json", view.model_dump(mode="json"))
    write_json(root / "board.json", service.get_agent_production_board(run_id).model_dump(mode="json"))
    for plan in store.list_plan_revisions(run_id):
        write_json(root / "plans" / f"{plan.id}.json", plan.model_dump(mode="json"))
    for action in store.list_actions(run_id):
        write_json(root / "actions" / f"{action.id}.json", action.model_dump(mode="json"))
    orders = store.list_work_orders(run_id)
    for order in orders:
        write_json(root / "work_orders" / f"{order.id}.json", order.model_dump(mode="json"))
        for turn in order.specialist_turns:
            write_json(root / "tool_turns" / f"{turn.id}.json", turn.model_dump(mode="json"))
    project_id = view.snapshot.run.project_id
    routes = service.repository.list_h3_route_plans(project_id)
    for route in routes:
        write_json(root / "route_plans" / f"{route.id}.json", route.model_dump(mode="json"))
    summary = {
        "observed_at": utc_now(), "run_id": run_id,
        "status": view.snapshot.run.status.value,
        "plan_revision": view.snapshot.current_plan.revision if view.snapshot.current_plan else None,
        "goal_complete": bool(view.snapshot.current_plan and view.snapshot.current_plan.goal_complete),
        "work_orders": [{
            "id": item.id, "role": item.recipient_role, "status": item.status.value,
            "next_owner": item.next_owner.value if item.next_owner else None,
            "tool_turns": len(item.specialist_turns),
            "latest_tool": item.specialist_turns[-1].tool_name if item.specialist_turns else None,
        } for item in orders],
        "route_plan_count": len(routes),
        "prepared_unit_ids": sorted({
            decision.generation_unit_id for route in routes for decision in route.decisions
            if decision.generation_unit_id is not None
        }),
        "video_tasks": len(service.repository.list_tasks(project_id)),
        "h3_provider_runs": len(service.repository.list_provider_runs(project_id)),
    }
    write_json(root / "latest.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", default="project-11b4f7a637c3")
    parser.add_argument("--run-id")
    parser.add_argument("--objective-file", type=Path)
    parser.add_argument("--replan-reason", help="Resume the same AgentRun with observed bug-fix feedback")
    parser.add_argument("--max-advances", type=int, help="Optional observation batch; not an Agent creative rule")
    args = parser.parse_args(argv)
    root = args.output_dir.resolve()
    recorder = TraceRecorder(root)
    database = args.database.resolve()
    backup = root / "database-before.sqlite3"
    if not backup.exists():
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)

    def block_image(*_args: object) -> Never:
        return recorder.block_media("image")

    service = ShortDramaService(
        database_path=database,
        workspace_root=database.parent / "outputs" / "short-drama-projects",
        language_provider_factory=lambda key, url: RecordingLanguageClient(
            recorder, api_key=key, base_url=url, generation_timeout_seconds=180.0
        ),
        h3_client_factory=lambda _key: recorder.block_media("H3"),
        image_provider_factory=block_image,
    )
    project_id: str = args.project_id
    session_path = root / "session.json"
    session: dict[str, Any] = json.loads(session_path.read_text()) if session_path.exists() else {}
    run_id: str | None = args.run_id or session.get("run_id")
    if session and session.get("database") != str(database):
        raise ValueError("This trace directory belongs to a different database")
    if session and session.get("project_id") != project_id:
        raise ValueError("This trace directory belongs to a different project")
    if run_id is None:
        before = service.get_project(project_id)
        write_json(root / "project-before.json", before.model_dump(mode="json"))
        objective = args.objective_file.read_text() if args.objective_file else DEFAULT_OBJECTIVE
        view = service.create_agent_run(project_id, AgentRunCreateRequest(
            objective=objective, root_agent="video-preparation-agent", auto_plan=False,
            unattended=True, execution_target=ProductionExecutionTarget.REAL_H3, render_export=False,
        ))
        run_id = view.snapshot.run.id
        session = {
            "started_at": utc_now(), "database": str(database), "project_id": project_id,
            "run_id": run_id, "objective": objective, "h3_io": "disabled",
            "language_model": "gpt-5.6-sol",
        }
        write_json(session_path, session)
    recorder.event("run_attached", run_id=run_id, project_id=project_id)
    stop_monitor = threading.Event()

    def observe() -> None:
        while not stop_monitor.wait(10):
            try:
                snapshot(service, run_id, root)
            except Exception as exc:
                recorder.event("snapshot_error", error=str(exc))

    monitor = threading.Thread(target=observe, name="trace-observer", daemon=True)
    monitor.start()
    exit_code = 0
    try:
        view = service.get_agent_run(run_id)
        if args.replan_reason:
            recorder.event("operator_replan", reason=args.replan_reason)
            service.replan_agent_run(run_id, AgentRunReplanRequest(reason=args.replan_reason))
        elif view.snapshot.current_plan is None:
            service.replan_agent_run(run_id, AgentRunReplanRequest(reason="Begin the authorized pre-H3 preparation stage."))
        advances = 0
        while True:
            summary = snapshot(service, run_id, root)
            recorder.event("agent_progress", **summary)
            if summary["status"] in {
                AgentRunStatus.COMPLETED.value, AgentRunStatus.FAILED.value, AgentRunStatus.CANCELLED.value
            }:
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
        snapshot(service, run_id, root)
        write_json(root / "project.json", service.get_project(project_id).model_dump(mode="json"))
        recorder.event("process_finished", exit_code=exit_code, run_id=run_id)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
