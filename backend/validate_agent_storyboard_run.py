"""Black-box validation for an Agent-authored storyboard-only run.

The probe starts a new ``AgentRun`` on an existing script-ready project and
then advances the Agent one action at a time.  It never calls a Tool directly
and never calls image, video, audio, H3, route, timeline, or export endpoints.

The only accepted terminal result is a canonical Storyboard bound by the
backend after the Agent has compiled a complete timing ledger and ClipGroup
outline, persisted each detailed group incrementally, semantically verified the
exact draft, and finalized it.  The probe reads that versioned draft only for
structural validation; it never assembles or repairs draft fragments itself.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Lock, Thread
from time import monotonic
from typing import Sequence, TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

DEFAULT_PROJECT_ID = "project-11b4f7a637c3"
DEFAULT_EXPECTED_SCRIPT_VERSION_ID = "project-11b4f7a637c3-script-v4"
DEFAULT_OBJECTIVE = """
以当前 ProjectDocument 中的 ScriptVersion 和 ProjectBibleVersion 为已确认内容，由
Agent 独立完成一份可交给后续制片的完整结构化分镜。不改写剧本事实、台词、
人物设定或场次顺序。
当前精确 ScriptVersion 是本次 AgentRun 已确认且锁定的内容源；一旦确认其
DurationContract 完整并开始新分镜草稿，本运行不得启动任何剧本内容修订。
若新草稿的账本超出接受区间，但同一 ScriptVersion 的当前 canonical Storyboard
已在契约区间内，这证明剧本具有可制作性：Leader 必须选择 STORYBOARD_PLAN，
从 canonical 基版再建一个全新草稿，重新检查可并发反应、停顿、道具动作、
运镜和组边界是否重复计时。旧 canonical 分镜只能作为时长可行性证据，
不得复制其镜头内容；不得调用 planning.start_script_revision 改写台词或剧情。

本次是对已确认剧本的全新分镜创作，不是把历史分镜草稿重新发布。
项目中任何旧 storyboard_draft，无论未完成还是已经 READY，都只能作为
历史参照：不得继续 append、不得再次 verify，也不得直接 finalize。
Planner 必须从当前 ScriptVersion、Bible 和可复用的 ProductionSpec 调用
shot.begin_storyboard_draft 创建新 document，并把当前 canonical Storyboard 的精确
version ID 写入 base_storyboard_version_id，使新草稿通过正常版本修订发布。

这是历史项目的重新分镜。若当前精确 ScriptVersion.story_intent.duration_contract
缺失，或 TARGET_LED 契约仍缺少使语义 alignment 可判定的
acceptable_range_seconds，不得将旧 ProductionSpec 的兼容默认值当成新的时长真相。用户原始需求已
明确要求约 120 秒，且现在再次确认剧本正文正常。Leader 应先通过
planning.start_script_revision 启动一次仅 STORY_INTENT 时长元数据的修订：剧本正文、
台词、结构化场次、故事承诺和 Bible 保持逐项不变，只建立来自用户明确时长的
TARGET_LED DurationContract。该元数据修订经单一 Verifier 接受后，再以新的精确
ScriptVersion 替换 ProductionSpec 和 Storyboard；不得续作历史未完成分镜草稿。
“约 120 秒”不是无限宽松，也不是必须精确到一帧；Agent 应根据这一集的快节奏类型、
完整台词和可演动作，在本次 StoryIntent 中明确一个具体、可解释的接受区间；
它是该项目的语义决定，不是全局固定容差或门禁。
这个例外只适用于新分镜草稿开始前，当前精确 ScriptVersion 确实缺失或未完整表达
DurationContract 时的 metadata-only 迁移。如果当前版本已有完整的 TARGET_LED target 和
acceptable range，后续 ledger、Verifier 或 mismatch_resolution 的结论都不得再触发 Script revision。

请先确认当前精确 ProductionSpec 是否已逐字投影当前 ScriptVersion 的
DurationContract 并满足本次分镜；若已满足就直接复用，不为重新生成分镜
无意义地创建新 Spec 版本。然后采用 MiniMax Design 的唯一编译路径：先通读
完整剧本和 Bible；若 Planner 当前 Tool history 没有两者的当前精确正文，
先用 project.get 取得，不以摘要或 ArtifactRef 代替原文。然后在同一 Planner
WorkOrder 内建立可恢复的源锁定草稿，按剧情语义
自主选择连续范围，逐段持久化对白和可见动作的时长账本。每个片段只是
运行检查点，不是 Scene、ClipGroup、Shot 或 ComfyUI 节点，不设固定大小或数量。
每个场次只有最后一个 timing entry 显式设置 completes_scene=true；纯动作场次
或对白后仍有动作时，由最后的可见动作条目完成该场。分段边界不代表场次完成。
在首次封结 ledger 前，Planner 必须基于精确 allocations 前瞻确认：每个场次
都存在连续、完整、不跨场的分区，每组分配时长之和精确为 4–15 秒整数。
时长账本必须落实 drama-series Workflow 的组级语义：台词、听者反应、道具
动作、运镜和环境变化可在同一 ClipGroup 并发。每条
non_speech_reserve_seconds 只表示并发后仍不可重叠的增量时间，不得把
组级的共享动作、反应、停顿或换镜预留复制给组内每条 entry。已选的
快节奏爽剧应逐句选择依然清晰自然的紧凑语速，不把整稿统一按慢速估算。
timing entry 是时间轴上的表演关键路径片段，不是一个 Shot 或一句原文。可并发的
台词、听者反应、道具动作和运镜必须共用同一时间片段；allocated_duration_seconds
可以是小数，只有最终 ClipGroup 总时长必须为 4–15 秒整数。
如果当前条目不可分，先拆分或调整确实可分的动作、反应和停顿预留，但不改台词、
事件或自然表演总意图，也不把 120 秒机械均摊到各场或各镜。但 TARGET_LED
必须对完整剧本的可演视听密度负责：若自然演读显示内容在契约尺度内可实现，应通过
紧凑动作、反应共时、对白与镜头并行等真实表演设计收敛；若精确账本证明无法实现，
必须带证据交回 Leader 在 STORYBOARD_PLAN 范围重编；若仍证明不可兼得，则报告约束冲突并停止，
不修改锁定的 ScriptVersion，也不能带着 OUTSIDE_ACCEPTED_RANGE 继续发布。
不得先封存账本，
再在 outline 阶段才发现无法装箱。账本完整覆盖剧本且分区可行后再完成该阶段；
基于完整账本二次重装箱，逐段持久化
planned ClipGroup outline，全局覆盖和连续性稳定后再写入 timing audit 完成大纲。
同一 Planner 在 begin、ledger append、ledger complete、outline append 和 outline complete
之间沿最新 draft version 继续；Tool 结果会回到同一 Specialist，可直接使用其中
真实 document/version ID，不得仅为获取 begin 产生的 ID 就结束 WorkOrder 并返回
Leader 重建计划。如果 Planner 仍发现当前草稿不可行，这仍属于同一编译职责：
可在同一 WorkOrder 内重新 begin 一个草稿分支并完整重编译，不得仅为重启草稿返回 Leader。
不能边看一句台词
边临时发明最终镜头组。用户目标时长是 TARGET_LED 的作品尺度，但不用于平均分配、
伪造账本或静默删改剧本。

随后按完整剧情语义自主规划 WorkOrder。一个 WorkOrder 可以覆盖同一自然动作链
中的多个 planned ClipGroup；Worker 优先使用 shot.append_storyboard_groups
把一次响应能够完整、清晰设计的相邻组前缀交给服务端，由服务端先整体
校验再按组逐版本持久化。当前只能可靠完成一个组时使用
shot.append_storyboard_group。批次范围由自然动作链、连续性和响应容量决定，
不为每组返回 Leader，也不为批量而截断 JSON 或降低镜头细节。长场次不
要求塞进一次模型响应，语义范围真正变化时才重规划。不使用固定镜头数、固定调用
次数或关键词路由替代 Agent 判断；相邻组必须从版本化草稿读取精确末态，状态变化
需要观众可见的过渡动作。

最终只发布一份 canonical Storyboard，对应 MiniMax Design 每个 episode/scope
的单一「分镜列表」canvas document node，不按场次拆成多份正式文档。文档内以原剧本
Scene 作为叙事与空间章节，每个章节包含按顺序排列的多个 ClipGroup，ClipGroup
内保留一个或多个可独立编辑的 Shot。每个 Shot 需要清楚表达镜头、动作、台词、情绪、
声音、时间段与连续性；每个 ClipGroup 需要对应一段 4–15 秒整数的未来 H3 视频生成单元，
组内 Shot 剪辑时长可为小数，
且不跨越剧本场次。分镜必须
按原顺序完整覆盖当前剧本台词行，不重复、不遗漏。同场次相邻组的可见状态与动作要在语义上承接，
但不要为字符串相等而复制措辞；跨场次边界要分别说清上一场的退场/结束、
下一场的入场/起始以及时间或空间转换。
所有计划组落盘后，由 REVISION_VERIFIER 一次通读精确完整草稿并写入语义报告；
若需要返修，Agent 自主定向替换受影响组，涉及时长账本或组边界时重新完整编译，
不采用 Critic 循环、固定审校次数或用户审批门。语义核对接受后再 finalize。

在 canonical Storyboard 结构完整、可读且可追溯后立即停止。不生成或上传核心资产，
不生成图片、视频或音频，不编译 GenerationPlan，不创建 Provider Task/Take，不创建
时间线，不导出媒体，不向用户请求创作确认。
""".strip()


class ProbeFailure(RuntimeError):
    """Expected validation or transport failure with a concise message."""


class ApiHttpError(ProbeFailure):
    """HTTP failure whose structured response remains available for recovery."""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        body: JsonValue,
    ) -> None:
        super().__init__(
            f"{method} {path} returned HTTP {status_code}; see raw responses"
        )
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: JsonObject


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _as_object(value: JsonValue | None, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ProbeFailure(f"{label} must be a JSON object")
    return value


def _as_list(value: JsonValue | None, label: str) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProbeFailure(f"{label} must be a JSON array")
    return value


def _string(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProbeFailure(f"{label} must be a non-empty string")
    return value


def _optional_string(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _number(value: JsonValue | None, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProbeFailure(f"{label} must be a number")
    return float(value)


def _json_strings(values: Sequence[str]) -> list[JsonValue]:
    return list(values)


class RawResponseRecorder:
    """Thread-safe raw HTTP envelope recorder for later diagnosis."""

    def __init__(self, output_dir: Path) -> None:
        self._lock = Lock()
        self.raw_dir = output_dir / "raw_responses"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.raw_dir.glob("[0-9][0-9][0-9]_*.json"))
        self._sequence = max(
            (int(path.name.split("_", 1)[0]) for path in existing),
            default=0,
        )
        self._files = [f"raw_responses/{path.name}" for path in existing]

    def record(
        self,
        *,
        label: str,
        method: str,
        path: str,
        request_body: JsonObject | None,
        status_code: int,
        response_body: JsonValue,
    ) -> None:
        with self._lock:
            self._sequence += 1
            filename = f"{self._sequence:03d}_{label}.json"
            envelope: JsonObject = {
                "recorded_at": _utc_now(),
                "method": method,
                "path": path,
                "request": request_body,
                "status_code": status_code,
                "response": response_body,
            }
            (self.raw_dir / filename).write_text(
                json.dumps(envelope, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._files.append(f"raw_responses/{filename}")

    def files(self) -> list[str]:
        with self._lock:
            return list(self._files)


class AgentStoryboardApiClient:
    """Narrow public API client; intentionally exposes no media mutations."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float | None,
        recorder: RawResponseRecorder,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProbeFailure("--base-url must be an absolute HTTP(S) URL")
        if not parsed.path.rstrip("/").endswith("/api/short-drama"):
            normalized = f"{normalized}/api/short-drama"
        self._api_root = normalized
        self._timeout_seconds = timeout_seconds
        self._recorder = recorder

    def create_agent_run(self, project_id: str, objective: str) -> JsonObject:
        return self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/agent-runs",
            label="create_agent_storyboard_run",
            payload={
                "objective": objective,
                "auto_plan": True,
                "unattended": True,
                "execution_target": "MOCK",
                "render_export": False,
            },
        ).body

    def advance_agent_run(self, run_id: str, max_actions: int) -> JsonObject:
        return self._request(
            "POST",
            f"/agent-runs/{quote(run_id, safe='')}/advance",
            label="advance_agent_storyboard_run",
            payload={"max_actions": max_actions},
        ).body

    def get_agent_run(self, run_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/agent-runs/{quote(run_id, safe='')}",
            label="get_agent_storyboard_run",
            payload=None,
        ).body

    def get_production_board(self, run_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/agent-runs/{quote(run_id, safe='')}/board",
            label="get_agent_storyboard_board",
            payload=None,
        ).body

    def get_work_order(self, work_order_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/agent-work-orders/{quote(work_order_id, safe='')}",
            label="get_agent_storyboard_work_order",
            payload=None,
        ).body

    def get_project(self, project_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}",
            label="get_agent_storyboard_project",
            payload=None,
        ).body

    def get_storyboard(self, project_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/storyboard",
            label="get_canonical_storyboard",
            payload=None,
        ).body

    def get_production_document(
        self,
        project_id: str,
        production_document_id: str,
        *,
        version_id: str | None = None,
    ) -> JsonObject:
        path = (
            "/projects/"
            + quote(project_id, safe="")
            + "/production-documents/"
            + quote(production_document_id, safe="")
        )
        if version_id is not None:
            path += "?version_id=" + quote(version_id, safe="")
        return self._request(
            "GET",
            path,
            label="get_storyboard_draft_document",
            payload=None,
        ).body

    def _request(
        self,
        method: str,
        path: str,
        *,
        label: str,
        payload: JsonObject | None,
    ) -> HttpResult:
        encoded = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{self._api_root}{path}",
            data=encoded,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            response_handle = (
                urlopen(request)
                if self._timeout_seconds is None
                else urlopen(request, timeout=self._timeout_seconds)
            )
            with response_handle as response:
                status_code = response.status
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            status_code = exc.code
            raw_body = exc.read().decode("utf-8", errors="replace")
            parsed_error = self._parse_json(raw_body)
            self._recorder.record(
                label=label,
                method=method,
                path=path,
                request_body=payload,
                status_code=status_code,
                response_body=parsed_error,
            )
            raise ApiHttpError(
                method=method,
                path=path,
                status_code=status_code,
                body=parsed_error,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ProbeFailure(f"{method} {path} failed: {exc}") from exc

        parsed_body = self._parse_json(raw_body)
        self._recorder.record(
            label=label,
            method=method,
            path=path,
            request_body=payload,
            status_code=status_code,
            response_body=parsed_body,
        )
        return HttpResult(
            status_code=status_code,
            body=_as_object(parsed_body, f"{method} {path} response"),
        )

    @staticmethod
    def _parse_json(raw_body: str) -> JsonValue:
        try:
            return cast(JsonValue, json.loads(raw_body))
        except json.JSONDecodeError as exc:
            raise ProbeFailure("API returned a non-JSON response") from exc


def _run_snapshot(run_view: JsonObject) -> JsonObject:
    return _as_object(run_view.get("snapshot"), "run.snapshot")


def _run_object(run_view: JsonObject) -> JsonObject:
    return _as_object(_run_snapshot(run_view).get("run"), "run.snapshot.run")


def _run_status(run_view: JsonObject) -> str:
    return _string(_run_object(run_view).get("status"), "run status")


def _waiting_blocker(run_view: JsonObject, board: JsonObject) -> str | None:
    """Return only a genuine external-input blocker for an unattended run.

    ``WAITING`` is also the normal persisted handoff state between the Leader
    and a local Worker.  The public ``advance`` endpoint owns that handoff, so
    the probe must keep advancing it instead of treating every WAITING snapshot
    as a failure.
    """

    snapshot = _run_snapshot(run_view)
    plan_value = snapshot.get("current_plan")
    current_plan_id = (
        _as_object(plan_value, "run.snapshot.current_plan").get("id")
        if plan_value is not None
        else None
    )
    for value in _as_list(snapshot.get("actions"), "run.snapshot.actions"):
        action = _as_object(value, "run action")
        if action.get("kind") == "USER_INPUT" and action.get("status") == "RUNNING":
            action_plan_id = action.get("plan_revision_id")
            if (
                isinstance(current_plan_id, str)
                and isinstance(action_plan_id, str)
                and action_plan_id != current_plan_id
            ):
                # Replanning supersedes the old semantic stop. The action remains
                # in history for traceability but no longer represents the active
                # plan's ownership boundary.
                continue
            return (
                "AgentRun requested user input during an unattended storyboard run: "
                + str(action.get("input_summary") or action.get("id") or "unknown input")
            )
    open_questions = [
        str(value)
        for value in _as_list(board.get("open_questions"), "board.open_questions")
        if str(value).strip()
    ]
    active_orders = [
        _as_object(value, "board.work_orders item")
        for value in _as_list(board.get("work_orders"), "board.work_orders")
    ]
    user_owned = [
        order
        for order in active_orders
        if order.get("next_owner") == "USER_INPUT"
    ]
    if user_owned:
        detail = "; ".join(open_questions) or ", ".join(
            str(order.get("id") or "unknown work order") for order in user_owned
        )
        return "AgentRun requested user input during an unattended run: " + detail

    ready_step = any(
        _as_object(value, "board.steps item").get("status") == "READY"
        for value in _as_list(board.get("steps"), "board.steps")
    )
    resumable_order = any(
        order.get("status") in {"PENDING", "NEEDS_ATTENTION"}
        and order.get("next_owner")
        in {"WORKER_DECISION", "WORKER_TOOL", "LEADER_REPLAN"}
        for order in active_orders
    )
    if ready_step or resumable_order:
        return None
    if open_questions:
        return (
            "AgentRun requested user input during an unattended run: "
            + "; ".join(open_questions)
        )

    blockers = [
        str(value)
        for value in _as_list(board.get("blockers"), "board.blockers")
        if str(value).strip()
    ]
    next_actions = [
        str(value)
        for value in _as_list(board.get("next_actions"), "board.next_actions")
        if str(value).strip()
    ]
    detail_parts = blockers or next_actions
    if not detail_parts and snapshot.get("current_plan") is None:
        detail_parts = ["the run has no active plan"]
    detail = "; ".join(detail_parts) or "no ready step or resumable WorkOrder"
    return "AgentRun cannot continue unattended: " + detail


def _resumable_agent_conflict(
    error: ApiHttpError,
    run_view: JsonObject,
    board: JsonObject,
) -> str | None:
    """Describe only a visible Worker failure already handed to a next owner."""

    if error.status_code != 409 or not isinstance(error.body, dict):
        return None
    if error.body.get("code") != "SHORT_DRAMA_AGENT_CONFLICT":
        return None
    if _run_status(run_view) != "WAITING":
        return None
    if _waiting_blocker(run_view, board) is not None:
        return None
    resumable_order = any(
        order.get("status") == "NEEDS_ATTENTION"
        and order.get("next_owner")
        in {"WORKER_DECISION", "WORKER_TOOL", "LEADER_REPLAN"}
        for value in _as_list(board.get("work_orders"), "board.work_orders")
        for order in [_as_object(value, "board.work_orders item")]
    )
    if not resumable_order:
        return None
    message = error.body.get("message")
    detail = message if isinstance(message, str) and message.strip() else str(error)
    return "Visible Worker failure was persisted for autonomous recovery: " + detail


def _progress_fingerprint(run_view: JsonObject, board: JsonObject) -> str:
    """Stable semantic progress fingerprint, excluding board timestamps."""

    snapshot = _run_snapshot(run_view)
    plan_value = snapshot.get("current_plan")
    plan = (
        _as_object(plan_value, "run.snapshot.current_plan")
        if plan_value is not None
        else None
    )
    actions = [
        {
            "id": action.get("id"),
            "status": action.get("status"),
            "output_summary": action.get("output_summary"),
            "error_message": action.get("error_message"),
            "artifact_refs": action.get("artifact_refs", []),
        }
        for value in _as_list(snapshot.get("actions"), "run.snapshot.actions")
        for action in [_as_object(value, "run action")]
    ]
    work_orders = [
        {
            "id": order.get("id"),
            "status": order.get("status"),
            "next_owner": order.get("next_owner"),
            "pending_tool_name": order.get("pending_tool_name"),
            "result_status": order.get("result_status"),
            "result_summary": order.get("result_summary"),
            "artifact_refs": order.get("artifact_refs", []),
            "progress_entries": order.get("progress_entries", []),
        }
        for field in ("work_orders", "historical_work_orders")
        for value in _as_list(board.get(field), f"board.{field}")
        for order in [_as_object(value, f"board.{field} item")]
    ]
    return json.dumps(
        {
            "status": _run_status(run_view),
            "plan_id": None if plan is None else plan.get("id"),
            "plan_revision": None if plan is None else plan.get("revision"),
            "plan_goal_complete": None if plan is None else plan.get("goal_complete"),
            "actions": actions,
            "work_orders": work_orders,
            "artifact_refs": board.get("artifact_refs", []),
            "blockers": board.get("blockers", []),
            "open_questions": board.get("open_questions", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _project_document(project: JsonObject) -> JsonObject:
    return _as_object(project.get("document"), "project.document")


def _project_record(project: JsonObject) -> JsonObject:
    return _as_object(_project_document(project).get("project"), "project record")


def _current_script(project: JsonObject) -> JsonObject:
    document = _project_document(project)
    current_id = _string(
        _project_record(project).get("current_script_version_id"),
        "current_script_version_id",
    )
    for value in _as_list(document.get("script_versions"), "script_versions"):
        version = _as_object(value, "script version")
        if version.get("id") == current_id:
            return version
    raise ProbeFailure(f"Current script version {current_id!r} was not returned")


def _script_story_content(script: JsonObject) -> JsonObject:
    """Project the authored story while excluding version and duration metadata."""

    story_intent_value = script.get("story_intent")
    story_intent = (
        {
            key: value
            for key, value in story_intent_value.items()
            if key != "duration_contract"
        }
        if isinstance(story_intent_value, dict)
        else story_intent_value
    )
    return {
        "source_mode": script.get("source_mode"),
        "original_text": script.get("original_text"),
        "screenplay_text": script.get("screenplay_text"),
        "spoken_text": script.get("spoken_text"),
        "subtitle_text": script.get("subtitle_text"),
        "lines": script.get("lines"),
        "structured_scenes": script.get("structured_scenes"),
        "ending_contract": script.get("ending_contract"),
        "story_intent_without_duration_contract": story_intent,
    }


def _script_duration_contract(script: JsonObject) -> JsonObject | None:
    story_intent = script.get("story_intent")
    if not isinstance(story_intent, dict):
        return None
    contract = story_intent.get("duration_contract")
    return contract if isinstance(contract, dict) else None


def _identity_list(value: JsonValue | None, label: str) -> list[str]:
    result: list[str] = []
    for item_value in _as_list(value, label):
        item = _as_object(item_value, f"{label} item")
        result.append(_string(item.get("id"), f"{label} item id"))
    return result


def _baseline(project: JsonObject) -> JsonObject:
    """Capture state that a storyboard-only run must leave untouched."""

    document = _project_document(project)
    record = _project_record(project)
    return {
        "current_script_version_id": record.get("current_script_version_id"),
        "current_bible_version_id": record.get("current_bible_version_id"),
        "asset_ids": _json_strings(_identity_list(document.get("assets"), "assets")),
        "task_ids": _json_strings(_identity_list(project.get("tasks"), "tasks")),
        "take_ids": _json_strings(_identity_list(project.get("takes"), "takes")),
        "export_ids": _json_strings(
            _identity_list(project.get("exports"), "exports")
        ),
        "route_evidence_ids": _json_strings(
            _identity_list(project.get("route_evidence"), "route_evidence")
        ),
        "video_inspection_ids": _json_strings(
            _identity_list(project.get("video_inspections"), "video_inspections")
        ),
        "take_comparison_ids": _json_strings(
            _identity_list(project.get("take_comparisons"), "take_comparisons")
        ),
        "audio_inspection_ids": _json_strings(
            _identity_list(project.get("audio_inspections"), "audio_inspections")
        ),
        "episode_review_ids": _json_strings(
            _identity_list(project.get("episode_reviews"), "episode_reviews")
        ),
        "manifest_ids": _json_strings(
            _identity_list(document.get("manifests"), "manifests")
        ),
        "generation_plan_document_id": document.get("generation_plan_document_id"),
        "audio_structure_document_id": document.get("audio_structure_document_id"),
        "master_audio_document_id": document.get("master_audio_document_id"),
        "timed_text_document_id": document.get("timed_text_document_id"),
        "current_manifest_id": record.get("current_manifest_id"),
        "production_document_ids": _json_strings(
            _identity_list(
                document.get("production_documents"),
                "production_documents",
            )
        ),
    }


def _summarize_action(action: JsonObject) -> JsonObject:
    return {
        "id": action.get("id"),
        "plan_step_id": action.get("plan_step_id"),
        "kind": action.get("kind"),
        "tool_name": action.get("tool_name"),
        "work_order_id": action.get("work_order_id"),
        "status": action.get("status"),
        "input_summary": action.get("input_summary"),
        "output_summary": action.get("output_summary"),
        "error_message": action.get("error_message"),
        "duration_ms": action.get("duration_ms"),
        "artifact_refs": action.get("artifact_refs", []),
        "created_at": action.get("created_at"),
        "completed_at": action.get("completed_at"),
    }


def _summarize_work_order(order: JsonObject, *, historical: bool) -> JsonObject:
    return {
        "id": order.get("id"),
        "historical": historical,
        "recipient_role": order.get("recipient_role"),
        "objective": order.get("objective"),
        "status": order.get("status"),
        "next_owner": order.get("next_owner"),
        "pending_tool_name": order.get("pending_tool_name"),
        "result_status": order.get("result_status"),
        "result_summary": order.get("result_summary"),
        "artifact_refs": order.get("artifact_refs", []),
        "progress_entries": order.get("progress_entries", []),
    }


def _board_work_order_ids(board: JsonObject) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for field in ("work_orders", "historical_work_orders"):
        for value in _as_list(board.get(field), f"board.{field}"):
            order = _as_object(value, f"board.{field} item")
            work_order_id = _string(order.get("id"), f"board.{field} item id")
            if work_order_id in seen:
                continue
            seen.add(work_order_id)
            result.append(work_order_id)
    return result


def _completed_specialist_tool_events(
    work_order_view: JsonObject,
    seen_turn_ids: set[str],
) -> list[JsonValue]:
    """Project completed specialist turns into concise structured trace events."""

    work_order = _as_object(
        work_order_view.get("work_order"),
        "agent work order response.work_order",
    )
    work_order_id = _string(work_order.get("id"), "agent work order id")
    events: list[JsonValue] = []
    turns: list[JsonObject] = [
        _as_object(value, "specialist turn")
        for value in _as_list(
            work_order.get("specialist_turns"),
            "work_order.specialist_turns",
        )
    ]

    def turn_sequence(turn: JsonObject) -> int:
        value = turn.get("sequence")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    turns.sort(key=turn_sequence)
    for turn in turns:
        if turn.get("status") != "COMPLETED":
            continue
        turn_id = _string(turn.get("id"), "specialist turn id")
        if turn_id in seen_turn_ids:
            continue
        sequence = turn.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise ProbeFailure("specialist turn sequence must be an integer")
        result_value = turn.get("result")
        result = (
            _as_object(result_value, "completed specialist turn result")
            if result_value is not None
            else None
        )
        artifact_refs = (
            []
            if result is None
            else _as_list(
                result.get("artifact_refs"),
                "specialist result artifact_refs",
            )
        )
        output_value = None if result is None else result.get("output")
        output = output_value if isinstance(output_value, dict) else {}
        checkpoint_values = output.get("checkpoints")
        checkpoints = checkpoint_values if isinstance(checkpoint_values, list) else []
        committed_group_ids = [
            checkpoint.get("clip_group_id")
            for checkpoint in checkpoints
            if isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("clip_group_id"), str)
        ]
        checkpoint_version_ids = [
            checkpoint.get("draft_version_id")
            for checkpoint in checkpoints
            if isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("draft_version_id"), str)
        ]
        events.append(
            {
                "turn_id": turn_id,
                "work_order_id": work_order_id,
                "sequence": sequence,
                "tool_name": _string(turn.get("tool_name"), "specialist tool name"),
                "turn_status": turn.get("status"),
                "tool_status": None if result is None else result.get("status"),
                "artifact_refs": artifact_refs,
                "committed_group_ids": committed_group_ids,
                "checkpoint_version_ids": checkpoint_version_ids,
                "completed_at": turn.get("updated_at"),
            }
        )
        seen_turn_ids.add(turn_id)
    return events


def _collect_specialist_tool_events(
    client: AgentStoryboardApiClient,
    work_order_ids: Sequence[str],
    seen_turn_ids: set[str],
) -> list[JsonValue]:
    events: list[JsonValue] = []
    for work_order_id in dict.fromkeys(work_order_ids):
        events.extend(
            _completed_specialist_tool_events(
                client.get_work_order(work_order_id),
                seen_turn_ids,
            )
        )
    return events


def _work_order_update_key(order: JsonObject) -> str:
    progress_ids = [
        _as_object(value, "work order progress").get("id")
        for value in _as_list(order.get("progress_entries"), "progress_entries")
    ]
    artifact_identities = [
        {
            "artifact_type": ref.get("artifact_type"),
            "artifact_id": ref.get("artifact_id"),
            "version": ref.get("version"),
        }
        for value in _as_list(order.get("artifact_refs"), "artifact_refs")
        for ref in [_as_object(value, "work order artifact ref")]
    ]
    return json.dumps(
        {
            "id": order.get("id"),
            "historical": order.get("historical"),
            "status": order.get("status"),
            "next_owner": order.get("next_owner"),
            "pending_tool_name": order.get("pending_tool_name"),
            "result_status": order.get("result_status"),
            "result_summary": order.get("result_summary"),
            "progress_ids": progress_ids,
            "artifact_refs": artifact_identities,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _latest_storyboard_draft_ref(order: JsonObject) -> JsonObject | None:
    refs = [
        ref
        for value in _as_list(order.get("artifact_refs"), "artifact_refs")
        for ref in [_as_object(value, "work order artifact ref")]
        if ref.get("artifact_type") == "storyboard_draft"
    ]
    return refs[-1] if refs else None


def _storyboard_draft_refs_from_board(board: JsonObject) -> list[JsonObject]:
    candidates: list[JsonObject] = []
    for value in _as_list(board.get("artifact_refs"), "board.artifact_refs"):
        ref = _as_object(value, "board artifact ref")
        if ref.get("artifact_type") == "storyboard_draft":
            candidates.append(ref)
    for field in ("work_orders", "historical_work_orders"):
        for value in _as_list(board.get(field), f"board.{field}"):
            order = _as_object(value, f"board.{field} item")
            for ref_value in _as_list(order.get("artifact_refs"), "artifact_refs"):
                ref = _as_object(ref_value, "work order artifact ref")
                if ref.get("artifact_type") == "storyboard_draft":
                    candidates.append(ref)
    unique: dict[tuple[str, int], JsonObject] = {}
    for ref in candidates:
        artifact_id = _string(ref.get("artifact_id"), "storyboard draft artifact id")
        version_value = ref.get("version")
        if not isinstance(version_value, int) or isinstance(version_value, bool):
            raise ProbeFailure("storyboard_draft refs must pin an integer version")
        unique[(artifact_id, version_value)] = ref
    return list(unique.values())


def _latest_storyboard_draft_ref_from_board(
    board: JsonObject,
    *,
    storyboard_response: JsonObject,
    project: JsonObject,
) -> JsonObject:
    """Resolve the exact draft version referenced by the canonical Storyboard.

    Draft version numbers are local to each production document. A restarted
    planning branch can therefore have a higher local version than the branch
    that was actually finalized. The canonical Storyboard version stores the
    finalized draft's immutable ``production_document_version`` reference; use
    that identity first and consult board refs only as a conservative fallback.
    """

    canonical_version = _as_object(
        storyboard_response.get("version"),
        "canonical storyboard version",
    )
    canonical_refs = [
        ref
        for value in _as_list(
            canonical_version.get("source_artifact_refs"),
            "canonical storyboard source_artifact_refs",
        )
        for ref in [_as_object(value, "canonical storyboard source artifact ref")]
        if ref.get("artifact_type") == "production_document_version"
    ]
    if not canonical_refs:
        raise ProbeFailure(
            "Canonical Storyboard version exposes no production_document_version "
            "source ref for its finalized draft"
        )

    project_document = _as_object(project.get("document"), "project document")
    draft_documents = [
        document
        for value in _as_list(
            project_document.get("production_documents"),
            "project production_documents",
        )
        for document in [_as_object(value, "project production document")]
        if document.get("kind") == "storyboard.draft"
    ]
    exact_matches: list[JsonObject] = []
    for document in draft_documents:
        document_id = _string(document.get("id"), "storyboard draft document id")
        for value in _as_list(
            document.get("versions"),
            "storyboard draft document versions",
        ):
            version = _as_object(value, "storyboard draft document version")
            version_id = _string(version.get("id"), "storyboard draft version id")
            version_number = version.get("version")
            if not isinstance(version_number, int) or isinstance(version_number, bool):
                raise ProbeFailure(
                    "storyboard draft document versions must pin an integer version"
                )
            matching_refs = [
                ref
                for ref in canonical_refs
                if ref.get("artifact_id") == version_id
                and ref.get("version") == version_number
            ]
            for source_ref in matching_refs:
                exact_matches.append(
                    {
                        "project_id": source_ref.get("project_id"),
                        "artifact_type": "storyboard_draft",
                        "artifact_id": document_id,
                        "version": version_number,
                        "version_id": version_id,
                    }
                )

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ProbeFailure(
            "Canonical Storyboard source refs resolve to multiple storyboard draft "
            "versions"
        )

    board_refs = _storyboard_draft_refs_from_board(board)
    if not board_refs:
        raise ProbeFailure("Final production board exposes no storyboard_draft ref")

    fallback_matches: list[JsonObject] = []
    for board_ref in board_refs:
        board_version = board_ref.get("version")
        matching_sources = [
            source_ref
            for source_ref in canonical_refs
            if source_ref.get("version") == board_version
        ]
        if len(matching_sources) != 1:
            continue
        source_ref = matching_sources[0]
        fallback_matches.append(
            {
                **board_ref,
                "version_id": source_ref.get("artifact_id"),
            }
        )
    if len(fallback_matches) == 1:
        return fallback_matches[0]
    if len(fallback_matches) > 1:
        raise ProbeFailure(
            "Canonical Storyboard draft source cannot be mapped safely because "
            "multiple restarted draft branches expose the same local version"
        )
    raise ProbeFailure(
        "Canonical Storyboard draft source cannot be mapped to a persisted "
        "storyboard_draft document"
    )


def _new_work_order_updates(
    board: JsonObject,
    seen_keys: set[str],
) -> list[JsonValue]:
    updates: list[JsonValue] = []
    for field, historical in (("work_orders", False), ("historical_work_orders", True)):
        for value in _as_list(board.get(field), f"board.{field}"):
            summary = _summarize_work_order(
                _as_object(value, f"board.{field} item"),
                historical=historical,
            )
            key = _work_order_update_key(summary)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            updates.append(summary)
    return updates


def _trace_phase(
    *,
    operation: str,
    run_view: JsonObject,
    board: JsonObject,
    seen_action_ids: set[str],
    seen_work_order_keys: set[str],
    executed_action_ids: list[JsonValue] | None = None,
    created_work_order_ids: list[JsonValue] | None = None,
    advanced_work_order_ids: list[JsonValue] | None = None,
    specialist_tool_events: list[JsonValue] | None = None,
    notes: list[JsonValue] | None = None,
) -> JsonObject:
    snapshot = _run_snapshot(run_view)
    actions: list[JsonValue] = []
    for value in _as_list(snapshot.get("actions"), "run.snapshot.actions"):
        action = _as_object(value, "run action")
        action_id = _string(action.get("id"), "run action id")
        if action_id in seen_action_ids:
            continue
        seen_action_ids.add(action_id)
        actions.append(_summarize_action(action))

    plan_value = snapshot.get("current_plan")
    plan = (
        _as_object(plan_value, "run.snapshot.current_plan")
        if plan_value is not None
        else None
    )
    return {
        "recorded_at": _utc_now(),
        "operation": operation,
        "run_status": _run_status(run_view),
        "plan_revision": plan.get("revision") if plan is not None else None,
        "plan_summary": plan.get("summary") if plan is not None else None,
        "plan_goal_complete": plan.get("goal_complete") if plan is not None else None,
        "selected_method_skill_ids": (
            plan.get("selected_method_skill_ids", []) if plan is not None else []
        ),
        "current_focus": board.get("current_focus"),
        "next_actions": board.get("next_actions", []),
        "executed_action_ids": executed_action_ids or [],
        "created_work_order_ids": created_work_order_ids or [],
        "advanced_work_order_ids": advanced_work_order_ids or [],
        "new_actions": actions,
        "specialist_tool_events": specialist_tool_events or [],
        "work_order_updates": _new_work_order_updates(board, seen_work_order_keys),
        "notes": notes or [],
    }


def _running_actions(run_view: JsonObject) -> list[JsonValue]:
    result: list[JsonValue] = []
    for value in _as_list(_run_snapshot(run_view).get("actions"), "run actions"):
        action = _as_object(value, "run action")
        if action.get("status") == "RUNNING":
            result.append(_summarize_action(action))
    return result


def _markdown_excerpt(value: JsonValue | None, *, limit: int = 300) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _write_trace_markdown(trace: JsonObject, output_path: Path) -> None:
    lines = [
        "# Agent storyboard validation trace",
        "",
        f"- Started: {trace.get('started_at')}",
        f"- Finished: {trace.get('finished_at')}",
        f"- Project: {trace.get('project_id')}",
        f"- AgentRun: {trace.get('run_id')}",
        f"- Outcome: {trace.get('outcome')}",
        "",
        "## 执行进展",
        "",
    ]
    in_flight_value = trace.get("in_flight")
    if in_flight_value is not None:
        in_flight = _as_object(in_flight_value, "trace.in_flight")
        lines.extend(
            [
                "### 当前仍在执行",
                "",
                f"- 已用时：{in_flight.get('elapsed_seconds')} 秒",
                f"- 当前聚焦：{in_flight.get('current_focus') or '-'}",
                f"- Run 状态：{in_flight.get('run_status') or '-'}",
                "",
            ]
        )
    for index, value in enumerate(_as_list(trace.get("phases"), "trace.phases"), 1):
        phase = _as_object(value, "trace phase")
        lines.extend(
            [
                f"### {index}. {phase.get('operation')}",
                "",
                f"- Run 状态：{phase.get('run_status')}",
                f"- 计划摘要：{phase.get('plan_summary') or '-'}",
                f"- 当前聚焦：{phase.get('current_focus') or '-'}",
            ]
        )
        selected = _as_list(
            phase.get("selected_method_skill_ids"),
            "selected_method_skill_ids",
        )
        if selected:
            lines.append("- 已选方法 Skills：" + "、".join(str(item) for item in selected))
        actions = _as_list(phase.get("new_actions"), "phase.new_actions")
        if actions:
            lines.append("- 新执行 Actions：")
            for action_value in actions:
                action = _as_object(action_value, "trace action")
                label = action.get("tool_name") or action.get("kind") or "action"
                summary = _markdown_excerpt(
                    action.get("output_summary") or action.get("error_message")
                )
                duration = action.get("duration_ms")
                work_order_id = _optional_string(action.get("work_order_id"))
                lines.append(
                    f"  - `{label}` → {action.get('status')}"
                    + (f" · WorkOrder `{work_order_id}`" if work_order_id else "")
                    + (f" ({duration} ms)" if isinstance(duration, int) else "")
                    + (f"：{summary}" if summary else "")
                )
        specialist_events = _as_list(
            phase.get("specialist_tool_events"),
            "phase.specialist_tool_events",
        )
        if specialist_events:
            lines.append("- Specialist Tool 事件：")
            for event_value in specialist_events:
                event = _as_object(event_value, "specialist tool event")
                committed_group_ids = _as_list(
                    event.get("committed_group_ids"),
                    "specialist committed_group_ids",
                )
                lines.append(
                    "  - `"
                    + str(event.get("tool_name") or "tool")
                    + "` → "
                    + str(event.get("tool_status") or event.get("turn_status") or "-")
                    + " · WorkOrder `"
                    + str(event.get("work_order_id") or "-")
                    + "` · turn "
                    + str(event.get("sequence") or "?")
                    + (
                        " · groups " + "、".join(str(item) for item in committed_group_ids)
                        if committed_group_ids
                        else ""
                    )
                )
        updates = _as_list(
            phase.get("work_order_updates"),
            "phase.work_order_updates",
        )
        if updates:
            lines.append("- WorkOrder 进展：")
            for update_value in updates:
                update = _as_object(update_value, "work order update")
                state = str(update.get("status") or "-")
                if update.get("result_status"):
                    state += f" / {update.get('result_status')}"
                lines.append(
                    "  - `"
                    + str(update.get("id"))
                    + "` · "
                    + str(update.get("recipient_role") or "-")
                    + " → "
                    + state
                    + "："
                    + _markdown_excerpt(
                        update.get("result_summary") or update.get("objective"),
                        limit=420,
                    )
                )
                draft_ref = _latest_storyboard_draft_ref(update)
                if draft_ref is not None:
                    lines.append(
                        "    - Storyboard draft：`"
                        + str(draft_ref.get("artifact_id") or "-")
                        + "` / v"
                        + str(draft_ref.get("version") or "?")
                    )
                for progress_value in _as_list(
                    update.get("progress_entries"),
                    "work order progress entries",
                ):
                    progress = _as_object(progress_value, "work order progress")
                    lines.append(
                        "    - 进展 "
                        + str(progress.get("sequence") or "?")
                        + "："
                        + str(progress.get("summary") or "-")
                    )
        notes = [
            str(item)
            for item in _as_list(phase.get("notes"), "phase.notes")
            if str(item).strip()
        ]
        if notes:
            lines.append("- Notes：" + "；".join(notes))
        lines.append("")

    validation = _as_object(trace.get("validation"), "trace.validation")
    lines.extend(["## 结构验证", ""])
    errors = _as_list(validation.get("errors"), "validation.errors")
    if errors:
        lines.extend(f"- FAIL：{error}" for error in errors)
    elif trace.get("outcome") == "PASSED":
        lines.append(
            "- PASS：Agent 已发布 canonical Storyboard，剧本与 Bible 版本未变，"
            "未进入资产、媒体、GenerationPlan、Task/Take 或时间线。"
        )
        lines.append(
            "- 场次、ClipGroup 和 Shot 的层级映射见 `storyboard.md`；"
            "完整时长账本、计划轮廓和语义核对见 `storyboard_draft.json`。"
        )
    else:
        lines.append("- Pending：运行尚未完成结构验收。")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _persist_trace(
    trace: JsonObject,
    *,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> None:
    trace["updated_at"] = _utc_now()
    trace["raw_response_files"] = _json_strings(recorder.files())
    temporary = output_dir / "trace.json.tmp"
    temporary.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output_dir / "trace.json")
    _write_trace_markdown(trace, output_dir / "trace.md")


def _advance_with_heartbeats(
    client: AgentStoryboardApiClient,
    run_id: str,
    *,
    max_actions: int,
    heartbeat_seconds: float,
    trace: JsonObject,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> JsonObject:
    outcomes: Queue[tuple[str, object]] = Queue(maxsize=1)

    def advance() -> None:
        try:
            outcomes.put(("response", client.advance_agent_run(run_id, max_actions)))
        except Exception as exc:
            outcomes.put(("error", exc))

    Thread(target=advance, name="agent-storyboard-advance", daemon=True).start()
    started = monotonic()
    observations = 0
    while True:
        try:
            outcome, value = outcomes.get(timeout=heartbeat_seconds)
        except Empty:
            observations += 1
            try:
                observed = client.get_agent_run(run_id)
                board = client.get_production_board(run_id)
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observations,
                    "run_status": _run_status(observed),
                    "current_focus": board.get("current_focus"),
                    "running_actions": _running_actions(observed),
                    "work_orders": board.get("work_orders", []),
                }
            except ProbeFailure as exc:
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observations,
                    "observation_error": str(exc),
                }
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            print(
                f"[{_utc_now()}] Agent action still running after "
                f"{monotonic() - started:.0f}s",
                flush=True,
            )
            continue

        trace.pop("in_flight", None)
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if outcome == "response":
            return _as_object(cast(JsonValue, value), "advance response")
        if isinstance(value, Exception):
            raise value
        raise ProbeFailure("Agent advance failed without an exception")


def _extract_canonical_storyboard(response: JsonObject) -> JsonObject:
    """Adapter seam between incremental drafting and the stable public result.

    TODO(incremental-storyboard): keep this adapter unchanged if the new draft
    Tools publish through the existing GET storyboard response.  If the public
    response is intentionally renamed, update only this function and its unit
    test; the runner must never assemble draft fragments itself.
    """

    return _as_object(response.get("storyboard"), "canonical storyboard")


def _storyboard_version(response: JsonObject) -> JsonObject:
    return _as_object(response.get("version"), "storyboard version")


def _extract_storyboard_draft(response: JsonObject) -> JsonObject:
    version = _as_object(response.get("version"), "storyboard draft version")
    return _as_object(version.get("payload"), "storyboard draft payload")


def _trace_tool_names(trace: JsonObject) -> list[str]:
    """Read structured Tool names recorded by Leader and Worker trace updates."""

    names: list[str] = []
    for phase_value in _as_list(trace.get("phases"), "trace.phases"):
        phase = _as_object(phase_value, "trace phase")
        for action_value in _as_list(phase.get("new_actions"), "phase.new_actions"):
            action = _as_object(action_value, "trace action")
            tool_name = _optional_string(action.get("tool_name"))
            if tool_name is not None:
                names.append(tool_name)
        for event_value in _as_list(
            phase.get("specialist_tool_events"),
            "phase.specialist_tool_events",
        ):
            event = _as_object(event_value, "specialist tool event")
            tool_name = _optional_string(event.get("tool_name"))
            if tool_name is not None:
                names.append(tool_name)
        for update_value in _as_list(
            phase.get("work_order_updates"),
            "phase.work_order_updates",
        ):
            update = _as_object(update_value, "work order update")
            tool_name = _optional_string(update.get("pending_tool_name"))
            if tool_name is not None:
                names.append(tool_name)
    return names


def _successful_trace_tool_events(
    trace: JsonObject,
) -> list[tuple[str, str | None]]:
    """Read completed Tool actions without treating pending/failed attempts as output."""

    ordered_events: list[
        tuple[datetime | None, int, str, str | None]
    ] = []
    ordinal = 0
    for phase_value in _as_list(trace.get("phases"), "trace.phases"):
        phase = _as_object(phase_value, "trace phase")
        phase_time = _trace_event_time(phase.get("recorded_at"))
        for action_value in _as_list(phase.get("new_actions"), "phase.new_actions"):
            action = _as_object(action_value, "trace action")
            if action.get("status") != "SUCCEEDED":
                continue
            tool_name = _optional_string(action.get("tool_name"))
            if tool_name is not None:
                ordinal += 1
                ordered_events.append(
                    (
                        _trace_event_time(action.get("completed_at")) or phase_time,
                        ordinal,
                        tool_name,
                        _optional_string(action.get("work_order_id")),
                    )
                )
        for event_value in _as_list(
            phase.get("specialist_tool_events"),
            "phase.specialist_tool_events",
        ):
            event = _as_object(event_value, "specialist tool event")
            if (
                event.get("turn_status") != "COMPLETED"
                or event.get("tool_status") != "SUCCEEDED"
            ):
                continue
            tool_name = _optional_string(event.get("tool_name"))
            if tool_name is not None:
                ordinal += 1
                ordered_events.append(
                    (
                        _trace_event_time(event.get("completed_at")) or phase_time,
                        ordinal,
                        tool_name,
                        _optional_string(event.get("work_order_id")),
                    )
                )
    ordered_events.sort(
        key=lambda item: (
            item[0] is not None,
            item[0] if item[0] is not None else datetime.min.replace(tzinfo=UTC),
            item[1],
        )
    )
    return [(name, owner) for _time, _ordinal, name, owner in ordered_events]


def _trace_event_time(value: JsonValue | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class _SuccessfulPlanningBranch:
    work_order_id: str
    start_index: int
    completion_index: int
    tool_names: tuple[str, ...]
    foreign_work_order_ids: frozenset[str]


def _final_successful_planning_branch(
    events: list[tuple[str, str | None]],
    planning_tool_names: set[str],
) -> _SuccessfulPlanningBranch | None:
    """Select the branch that produced the final successful planning outline.

    Earlier attempts remain visible in the trace, but a failed or superseded draft
    branch is not a formal Storyboard version.  A restarted branch is valid when it
    begins and completes under the same Planner WorkOrder.
    """

    completion_indices = [
        index
        for index, (tool_name, _work_order_id) in enumerate(events)
        if tool_name == "shot.complete_storyboard_outline"
    ]
    if not completion_indices:
        return None
    completion_index = completion_indices[-1]
    work_order_id = events[completion_index][1]
    if work_order_id is None:
        return None
    begin_indices = [
        index
        for index, (tool_name, owner) in enumerate(events[: completion_index + 1])
        if tool_name == "shot.begin_storyboard_draft" and owner == work_order_id
    ]
    if not begin_indices:
        return None
    start_index = begin_indices[-1]
    branch_events = events[start_index : completion_index + 1]
    return _SuccessfulPlanningBranch(
        work_order_id=work_order_id,
        start_index=start_index,
        completion_index=completion_index,
        tool_names=tuple(
            tool_name
            for tool_name, owner in branch_events
            if owner == work_order_id
        ),
        foreign_work_order_ids=frozenset(
            owner
            for tool_name, owner in branch_events
            if tool_name in planning_tool_names
            and owner is not None
            and owner != work_order_id
        ),
    )


def _missing_ordered_tool_stages(
    tool_names: list[str],
    required_stages: tuple[str, ...],
) -> list[str]:
    """Return stages that cannot be found in semantic order.

    The number of ledger, outline, group, replacement, and verification calls is
    deliberately unconstrained.  This only observes the phase transition order.
    """

    missing: list[str] = []
    cursor = 0
    for required in required_stages:
        try:
            index = tool_names.index(required, cursor)
        except ValueError:
            missing.append(required)
            continue
        cursor = index + 1
    return missing


def _duration_matches(left: float, right: float) -> bool:
    return abs(left - right) <= 0.05


def _is_h3_clip_group_duration(duration: float) -> bool:
    return 4.0 <= duration <= 15.0 and duration.is_integer()


def _artifact_ids(project: JsonObject, field: str) -> list[str]:
    document = _project_document(project)
    source = (
        project
        if field
        in {
            "tasks",
            "takes",
            "exports",
            "route_evidence",
            "video_inspections",
            "take_comparisons",
            "audio_inspections",
            "episode_reviews",
        }
        else document
    )
    return _identity_list(source.get(field), field)


def _baseline_strings(baseline: JsonObject, field: str) -> list[str]:
    return [
        _string(value, f"baseline.{field} item")
        for value in _as_list(baseline.get(field), f"baseline.{field}")
    ]


def _shot_scene_id(shot: JsonObject) -> str | None:
    return _optional_string(shot.get("scene_id"))


def _group_scene_id(group: JsonObject, shot_by_id: dict[str, JsonObject]) -> str | None:
    explicit = _optional_string(group.get("scene_id")) or _optional_string(
        group.get("source_scene_id")
    )
    inferred = {
        _shot_scene_id(shot_by_id[shot_id])
        for value in _as_list(group.get("shot_ids"), "clip group shot_ids")
        for shot_id in [_string(value, "clip group shot id")]
        if shot_id in shot_by_id
    }
    inferred.discard(None)
    if explicit is not None:
        return explicit
    if len(inferred) == 1:
        return next(iter(inferred))
    return None


def _validate_storyboard_only_result(
    *,
    run_view: JsonObject,
    before: JsonObject,
    after: JsonObject,
    storyboard_response: JsonObject,
    storyboard_draft_response: JsonObject,
    storyboard_draft_ref: JsonObject,
    trace: JsonObject,
    expected_script_version_id: str | None,
) -> list[str]:
    errors: list[str] = []
    if _run_status(run_view) != "COMPLETED":
        errors.append(
            f"AgentRun ended with {_run_status(run_view)}, expected COMPLETED"
        )

    baseline = _baseline(before)
    after_record = _project_record(after)
    after_document = _project_document(after)
    before_script = _current_script(before)
    after_script = _current_script(after)
    script_version_id = _string(
        after_record.get("current_script_version_id"),
        "after current_script_version_id",
    )
    script_version_changed = script_version_id != baseline.get(
        "current_script_version_id"
    )
    if script_version_changed:
        before_contract = _script_duration_contract(before_script)
        after_contract = _script_duration_contract(after_script)
        legacy_contract_needs_refinement = (
            before_contract is None
            or (
                before_contract.get("authority") == "TARGET_LED"
                and (
                    before_contract.get("target_seconds") is None
                    or before_contract.get("acceptable_range_seconds") is None
                )
            )
        )
        if not legacy_contract_needs_refinement:
            errors.append(
                "Storyboard run changed a ScriptVersion whose DurationContract "
                "was already complete"
            )
        if _script_story_content(before_script) != _script_story_content(after_script):
            errors.append(
                "Legacy duration migration changed confirmed screenplay content"
            )
        if (
            after_contract is None
            or after_contract.get("authority") != "TARGET_LED"
            or after_contract.get("target_origin") != "USER_DECLARED"
            or after_contract.get("acceptable_range_seconds") is None
        ):
            errors.append(
                "Legacy duration migration did not establish a user-declared "
                "TARGET_LED DurationContract with a semantic accepted range"
            )
    elif (
        expected_script_version_id is not None
        and script_version_id != expected_script_version_id
    ):
        errors.append(
            f"Current ScriptVersion is {script_version_id}, expected "
            f"{expected_script_version_id}"
        )
    if after_record.get("current_bible_version_id") != baseline.get(
        "current_bible_version_id"
    ):
        errors.append("Storyboard run changed the current ProjectBibleVersion")

    unchanged_collections = (
        ("assets", "asset_ids"),
        ("tasks", "task_ids"),
        ("takes", "take_ids"),
        ("manifests", "manifest_ids"),
        ("exports", "export_ids"),
        ("route_evidence", "route_evidence_ids"),
        ("video_inspections", "video_inspection_ids"),
        ("take_comparisons", "take_comparison_ids"),
        ("audio_inspections", "audio_inspection_ids"),
        ("episode_reviews", "episode_review_ids"),
    )
    for project_field, baseline_field in unchanged_collections:
        if _artifact_ids(after, project_field) != _baseline_strings(
            baseline, baseline_field
        ):
            errors.append(f"Storyboard-only run changed {project_field}")

    unchanged_pointers = (
        "generation_plan_document_id",
        "audio_structure_document_id",
        "master_audio_document_id",
        "timed_text_document_id",
    )
    for field in unchanged_pointers:
        if after_document.get(field) != baseline.get(field):
            errors.append(f"Storyboard-only run changed downstream pointer {field}")
    if after_record.get("current_manifest_id") != baseline.get("current_manifest_id"):
        errors.append("Storyboard-only run changed current_manifest_id")

    storyboard = _extract_canonical_storyboard(storyboard_response)
    if storyboard.get("project_id") != after_record.get("id"):
        errors.append("Canonical Storyboard belongs to a different project")
    if storyboard.get("script_version_id") != script_version_id:
        errors.append("Canonical Storyboard does not reference the current ScriptVersion")
    if storyboard.get("bible_version_id") != after_record.get(
        "current_bible_version_id"
    ):
        errors.append("Canonical Storyboard does not reference the current BibleVersion")
    if storyboard.get("production_spec_document_id") != after_document.get(
        "production_spec_document_id"
    ):
        errors.append("Canonical Storyboard does not reference the current ProductionSpec")
    if storyboard.get("computed_duration_seconds") in (None, 0, 0.0):
        errors.append("Canonical Storyboard has no positive computed duration")

    script = after_script
    expected_line_ids = [
        _string(_as_object(value, "script line").get("id"), "script line id")
        for value in _as_list(script.get("lines"), "script lines")
    ]
    draft_document = _as_object(
        storyboard_draft_response.get("document"),
        "storyboard draft document",
    )
    draft_version = _as_object(
        storyboard_draft_response.get("version"),
        "storyboard draft version",
    )
    if draft_document.get("kind") != "storyboard.draft":
        errors.append("Latest draft ref does not resolve to a storyboard.draft document")
    if draft_document.get("id") != storyboard_draft_ref.get("artifact_id"):
        errors.append("Fetched storyboard draft does not match the final board ref")
    if draft_version.get("version") != storyboard_draft_ref.get("version"):
        errors.append("Fetched storyboard draft is not the latest board-pinned version")

    draft = _extract_storyboard_draft(storyboard_draft_response)
    if draft.get("project_id") != after_record.get("id"):
        errors.append("Storyboard draft belongs to a different project")
    if draft.get("script_version_id") != script_version_id:
        errors.append("Storyboard draft does not reference the current ScriptVersion")
    if draft.get("bible_version_id") != after_record.get("current_bible_version_id"):
        errors.append("Storyboard draft does not reference the current BibleVersion")
    if draft.get("phase") != "GROUP_AUTHORING":
        errors.append("Final storyboard draft did not reach GROUP_AUTHORING phase")

    timing_ledger = [
        _as_object(value, "storyboard timing ledger entry")
        for value in _as_list(draft.get("timing_ledger"), "timing_ledger")
    ]
    planned_groups = [
        _as_object(value, "planned clip group")
        for value in _as_list(draft.get("planned_clip_groups"), "planned_clip_groups")
    ]
    committed_groups = [
        _as_object(value, "committed clip group")
        for value in _as_list(draft.get("committed_groups"), "committed_groups")
    ]
    timing_audit_value = draft.get("timing_audit")
    verification_value = draft.get("verification")
    if not timing_ledger:
        errors.append("Storyboard draft has no complete timing ledger")
    if not planned_groups:
        errors.append("Storyboard draft has no planned ClipGroup outline")
    if not committed_groups:
        errors.append("Storyboard draft has no committed ClipGroups")
    if timing_audit_value is None:
        errors.append("Storyboard draft has no timing audit")
    if verification_value is None:
        errors.append("Storyboard draft has no semantic verification report")
    timing_audit = (
        _as_object(timing_audit_value, "timing_audit")
        if timing_audit_value is not None
        else None
    )
    duration_contract = _script_duration_contract(script)
    if (
        duration_contract is not None
        and duration_contract.get("authority") == "TARGET_LED"
        and (
            timing_audit is None
            or timing_audit.get("alignment") != "ALIGNED"
        )
    ):
        errors.append(
            "TARGET_LED storyboard did not finish with ALIGNED timing"
        )

    ledger_ids = [
        _string(item.get("id"), "timing ledger entry id") for item in timing_ledger
    ]
    if len(ledger_ids) != len(set(ledger_ids)):
        errors.append("Storyboard timing ledger has duplicate entry IDs")
    if [item.get("sequence") for item in timing_ledger] != list(
        range(1, len(timing_ledger) + 1)
    ):
        errors.append("Storyboard timing ledger sequence is not contiguous")
    draft_scene_nodes = [
        _as_object(value, "storyboard draft scene node")
        for value in _as_list(draft.get("scene_nodes"), "draft scene_nodes")
    ]
    for scene_node in draft_scene_nodes:
        scene_node_id = _string(
            scene_node.get("id"),
            "storyboard draft scene node id",
        )
        scene_entries = [
            entry
            for entry in timing_ledger
            if entry.get("scene_node_id") == scene_node_id
        ]
        if not scene_entries:
            errors.append(f"Storyboard scene {scene_node_id} has no timing entry")
            continue
        if any(
            entry.get("completes_scene") is not False
            for entry in scene_entries[:-1]
        ) or scene_entries[-1].get("completes_scene") is not True:
            errors.append(
                "Storyboard timing ledger must mark only the final entry of "
                f"scene {scene_node_id} with completes_scene=true"
            )
    ledger_line_ids = [
        _string(value, "timing ledger script line id")
        for item in timing_ledger
        for value in _as_list(
            _as_object(item.get("source_range"), "timing ledger source range").get(
                "script_line_ids"
            ),
            "timing ledger source line ids",
        )
    ]
    if ledger_line_ids != expected_line_ids:
        errors.append("Storyboard timing ledger does not cover script lines in order")

    planned_ids = [
        _string(item.get("id"), "planned clip group id") for item in planned_groups
    ]
    planned_timing_ids = [
        _string(value, "planned timing entry id")
        for item in planned_groups
        for value in _as_list(item.get("timing_entry_ids"), "timing_entry_ids")
    ]
    if planned_timing_ids != ledger_ids:
        errors.append(
            "Planned ClipGroup outline does not consume the timing ledger in order"
        )
    planned_line_ids = [
        _string(value, "planned clip group script line id")
        for item in planned_groups
        for value in _as_list(
            _as_object(item.get("source_range"), "planned source range").get(
                "script_line_ids"
            ),
            "planned source line ids",
        )
    ]
    if planned_line_ids != expected_line_ids:
        errors.append("Planned ClipGroup outline does not cover script lines in order")

    ledger_by_id = dict(zip(ledger_ids, timing_ledger, strict=True))
    for planned in planned_groups:
        group_id = _string(planned.get("id"), "planned clip group id")
        entry_ids = [
            _string(value, "planned timing entry id")
            for value in _as_list(planned.get("timing_entry_ids"), "timing_entry_ids")
        ]
        if not entry_ids:
            errors.append(f"Planned ClipGroup {group_id} has no timing ledger rows")
        if any(entry_id not in ledger_by_id for entry_id in entry_ids):
            errors.append(f"Planned ClipGroup {group_id} references unknown ledger rows")
            continue
        planned_duration = _number(
            planned.get("duration_seconds"),
            "planned group duration",
        )
        if not _is_h3_clip_group_duration(planned_duration):
            errors.append(
                f"Planned ClipGroup {group_id} duration {planned_duration:g}s is "
                "outside H3 integer 4–15s"
            )
        entry_scene_node_ids = {
            _string(
                ledger_by_id[entry_id].get("scene_node_id"),
                "timing ledger scene node id",
            )
            for entry_id in entry_ids
        }
        planned_scene_node_id = _optional_string(planned.get("scene_node_id"))
        if (
            len(entry_scene_node_ids) != 1
            or planned_scene_node_id not in entry_scene_node_ids
        ):
            errors.append(
                f"Planned ClipGroup {group_id} crosses or loses its timing ledger Scene"
            )
        ledger_duration = sum(
            _number(
                ledger_by_id[entry_id].get("allocated_duration_seconds"),
                "timing ledger allocated duration",
            )
            for entry_id in entry_ids
        )
        if not _duration_matches(
            ledger_duration,
            planned_duration,
        ):
            errors.append(
                f"Timing ledger duration does not match planned ClipGroup {group_id}"
            )

    storyboard_line_ids = [
        _string(value, "storyboard script line id")
        for value in _as_list(storyboard.get("script_line_ids"), "script_line_ids")
    ]
    if storyboard_line_ids != expected_line_ids:
        errors.append("Canonical Storyboard does not cover script lines in source order")

    shots = [
        _as_object(value, "storyboard shot")
        for value in _as_list(storyboard.get("shots"), "storyboard shots")
    ]
    groups = [
        _as_object(value, "storyboard clip group")
        for value in _as_list(storyboard.get("clip_groups"), "storyboard clip_groups")
    ]
    if not shots:
        errors.append("Canonical Storyboard has no Shots")
    if not groups:
        errors.append("Canonical Storyboard has no ClipGroups")

    committed_ids = [
        _string(
            _as_object(item.get("clip_group"), "committed ClipGroup payload").get(
                "id"
            ),
            "committed clip group id",
        )
        for item in committed_groups
    ]
    canonical_group_ids = [
        _string(group.get("id"), "clip group id") for group in groups
    ]
    if planned_ids != committed_ids or committed_ids != canonical_group_ids:
        errors.append(
            "Planned, committed, and canonical ClipGroup IDs do not match in order"
        )
    for planned, committed, canonical in zip(
        planned_groups,
        [
            _as_object(item.get("clip_group"), "committed ClipGroup payload")
            for item in committed_groups
        ],
        groups,
    ):
        group_id = _string(planned.get("id"), "planned clip group id")
        durations = (
            _number(planned.get("duration_seconds"), "planned group duration"),
            _number(committed.get("duration_seconds"), "committed group duration"),
            _number(canonical.get("duration_seconds"), "canonical group duration"),
        )
        if not (
            _duration_matches(durations[0], durations[1])
            and _duration_matches(durations[1], durations[2])
        ):
            errors.append(
                f"Planned, committed, and canonical durations differ for {group_id}"
            )

    committed_line_ids = [
        _string(value, "committed clip group script line id")
        for item in committed_groups
        for value in _as_list(
            _as_object(item.get("source_range"), "committed source range").get(
                "script_line_ids"
            ),
            "committed source line ids",
        )
    ]
    if committed_line_ids != expected_line_ids:
        errors.append("Committed ClipGroups do not cover script lines in order")

    planned_duration = sum(
        _number(item.get("duration_seconds"), "planned group duration")
        for item in planned_groups
    )
    committed_duration = sum(
        _number(
            _as_object(item.get("clip_group"), "committed ClipGroup payload").get(
                "duration_seconds"
            ),
            "committed group duration",
        )
        for item in committed_groups
    )
    canonical_duration = sum(
        _number(group.get("duration_seconds"), "canonical group duration")
        for group in groups
    )
    ledger_duration = sum(
        _number(item.get("allocated_duration_seconds"), "ledger allocated duration")
        for item in timing_ledger
    )
    computed_duration = _number(
        storyboard.get("computed_duration_seconds"),
        "canonical computed duration",
    )
    duration_chain = (
        ledger_duration,
        planned_duration,
        committed_duration,
        canonical_duration,
        computed_duration,
    )
    if any(
        not _duration_matches(duration_chain[0], duration)
        for duration in duration_chain[1:]
    ):
        errors.append(
            "Timing ledger, outline, committed groups, and canonical durations disagree"
        )

    if timing_audit_value is not None:
        timing_audit = _as_object(timing_audit_value, "timing audit")
        if timing_audit.get("timing_audit_passed") is not True:
            errors.append("Storyboard timing audit did not pass")
        if _as_list(
            timing_audit.get("overloaded_shot_ids"),
            "overloaded_shot_ids",
        ):
            errors.append("Storyboard timing audit retains overloaded Shots")
        if _as_list(
            timing_audit.get("overloaded_group_ids"),
            "overloaded_group_ids",
        ):
            errors.append("Storyboard timing audit retains overloaded ClipGroups")
        if timing_audit.get("clip_group_count") != len(planned_groups):
            errors.append("Storyboard timing audit ClipGroup count is stale")
        if not _duration_matches(
            _number(
                timing_audit.get("computed_episode_duration_s"),
                "timing audit computed duration",
            ),
            computed_duration,
        ):
            errors.append("Storyboard timing audit duration disagrees with canonical")

    if verification_value is not None:
        verification = _as_object(verification_value, "storyboard verification")
        if verification.get("status") != "READY":
            errors.append("Latest storyboard draft verification is not READY")
        checked_ids = [
            _string(value, "verified clip group id")
            for value in _as_list(
                verification.get("checked_clip_group_ids"),
                "checked_clip_group_ids",
            )
        ]
        if checked_ids != canonical_group_ids:
            errors.append("Storyboard verification did not check every final ClipGroup")
        if _as_list(verification.get("findings"), "verification findings"):
            errors.append("READY storyboard verification retains unresolved findings")
        if not _as_list(
            verification.get("continuity_assessment"),
            "verification continuity_assessment",
        ):
            errors.append(
                "Storyboard verification has no semantic continuity assessment"
            )

    tool_names = _trace_tool_names(trace)
    planning_tool_stages = (
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
    )
    post_planning_tool_stages = (
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    )
    detailed_group_tools = {
        "shot.append_storyboard_group",
        "shot.append_storyboard_groups",
    }

    def normalized_group_tool(name: str) -> str:
        return (
            "shot.append_storyboard_group"
            if name in detailed_group_tools
            else name
        )

    planning_tool_names: set[str] = set(planning_tool_stages)
    successful_events = _successful_trace_tool_events(trace)
    final_planning_branch = _final_successful_planning_branch(
        successful_events,
        planning_tool_names,
    )
    if final_planning_branch is None:
        successful_tool_names = [
            normalized_group_tool(name) for name, _owner in successful_events
        ]
        missing_tools = _missing_ordered_tool_stages(
            successful_tool_names,
            planning_tool_stages + post_planning_tool_stages,
        )
        errors.append(
            "Storyboard trace does not expose one final successful planning "
            "branch owned by a Planner WorkOrder"
        )
    else:
        missing_tools = _missing_ordered_tool_stages(
            list(final_planning_branch.tool_names),
            planning_tool_stages,
        )
        post_planning_names = [
            normalized_group_tool(name)
            for name, _owner in successful_events[
                final_planning_branch.completion_index + 1 :
            ]
        ]
        missing_tools.extend(
            _missing_ordered_tool_stages(
                post_planning_names,
                post_planning_tool_stages,
            )
        )
        branch_events = successful_events[
            final_planning_branch.start_index :
            final_planning_branch.completion_index + 1
        ]
        missing_owner_tools = sorted(
            {
                name
                for name, owner in branch_events
                if name in planning_tool_names and owner is None
            }
        )
        if missing_owner_tools:
            errors.append(
                "Storyboard trace does not expose WorkOrder ownership for planning "
                "stages: " + ", ".join(missing_owner_tools)
            )
        if final_planning_branch.foreign_work_order_ids:
            errors.append(
                "Storyboard begin, ledger, and outline stages were split across "
                "multiple Planner WorkOrders"
            )
    if missing_tools:
        errors.append(
            "Storyboard trace is missing required ordered incremental stages: "
            + ", ".join(missing_tools)
        )
    forbidden_exact = {
        "shot.create_storyboard",
        "shot.replace_storyboard",
        "shot.patch_storyboard",
    }
    forbidden_prefixes = ("image.", "video.", "audio.", "route.", "timeline.")
    forbidden_tools = sorted(
        {
            name
            for name in tool_names
            if name in forbidden_exact or name.startswith(forbidden_prefixes)
        }
    )
    if forbidden_tools:
        errors.append(
            "Storyboard-only trace used legacy canonical/media Tools: "
            + ", ".join(forbidden_tools)
        )

    shot_ids = [_string(shot.get("id"), "shot id") for shot in shots]
    if len(shot_ids) != len(set(shot_ids)):
        errors.append("Canonical Storyboard has duplicate Shot IDs")
    if [shot.get("sequence") for shot in shots] != list(range(1, len(shots) + 1)):
        errors.append("Canonical Storyboard Shot sequence is not contiguous")

    assigned_line_ids = [
        _string(value, "shot dialogue line id")
        for shot in shots
        for value in _as_list(shot.get("dialogue_line_ids"), "shot dialogue_line_ids")
    ]
    if assigned_line_ids != expected_line_ids:
        errors.append("Shots do not assign every script line exactly once and in order")

    grouped_shot_ids = [
        _string(value, "clip group shot id")
        for group in groups
        for value in _as_list(group.get("shot_ids"), "clip group shot_ids")
    ]
    if grouped_shot_ids != shot_ids:
        errors.append("ClipGroups do not cover every Shot exactly once and in order")

    scene_ids = {
        _string(_as_object(value, "script scene").get("id"), "script scene id")
        for value in _as_list(script.get("structured_scenes"), "structured_scenes")
    }
    shot_by_id = dict(zip(shot_ids, shots, strict=True))
    for shot in shots:
        scene_id = _shot_scene_id(shot)
        if scene_id is None or scene_id not in scene_ids:
            errors.append(
                f"Shot {shot.get('id')} does not reference a current script Scene"
            )
    for group in groups:
        group_id = _string(group.get("id"), "clip group id")
        duration = _number(group.get("duration_seconds"), "clip group duration")
        if not _is_h3_clip_group_duration(duration):
            errors.append(
                f"ClipGroup {group_id} duration {duration:g}s is outside H3 "
                "integer 4–15s"
            )
        member_scene_ids = {
            _shot_scene_id(shot_by_id[shot_id])
            for value in _as_list(group.get("shot_ids"), "clip group shot_ids")
            for shot_id in [_string(value, "clip group shot id")]
            if shot_id in shot_by_id
        }
        if None in member_scene_ids or len(member_scene_ids) != 1:
            errors.append(f"ClipGroup {group_id} crosses or loses its script Scene")
        explicit_scene_id = _optional_string(group.get("scene_id")) or _optional_string(
            group.get("source_scene_id")
        )
        if explicit_scene_id is not None and explicit_scene_id not in member_scene_ids:
            errors.append(f"ClipGroup {group_id} scene identity disagrees with its Shots")

    script_scenes = [
        _as_object(value, "script scene")
        for value in _as_list(script.get("structured_scenes"), "structured_scenes")
    ]
    scene_nodes = [
        _as_object(value, "storyboard scene node")
        for value in _as_list(storyboard.get("scene_nodes"), "scene_nodes")
    ]
    expected_scene_ids = [
        _string(scene.get("id"), "script scene id") for scene in script_scenes
    ]
    if [node.get("scene_id") for node in scene_nodes] != expected_scene_ids:
        errors.append("Storyboard scene index does not match current script scenes")
    indexed_line_ids = [
        _string(value, "scene-node source line id")
        for node in scene_nodes
        for value in _as_list(node.get("source_line_ids"), "scene-node source_line_ids")
    ]
    if indexed_line_ids != expected_line_ids:
        errors.append("Storyboard scene index does not cover script lines in order")
    indexed_group_ids = [
        _string(value, "scene-node clip group id")
        for node in scene_nodes
        for value in _as_list(node.get("clip_group_ids"), "scene-node clip_group_ids")
    ]
    if indexed_group_ids != [
        _string(group.get("id"), "clip group id") for group in groups
    ]:
        errors.append("Storyboard scene index does not cover ClipGroups in order")
    indexed_shot_ids = [
        _string(value, "scene-node shot id")
        for node in scene_nodes
        for value in _as_list(node.get("shot_ids"), "scene-node shot_ids")
    ]
    if indexed_shot_ids != shot_ids:
        errors.append("Storyboard scene index does not cover Shots in order")

    slot_value = storyboard_response.get("output_slot")
    if slot_value is None:
        errors.append("Canonical Storyboard is not bound to an output slot")
    else:
        slot = _as_object(slot_value, "storyboard output_slot")
        if slot.get("id") != "storyboard.current":
            errors.append("Canonical Storyboard is not bound to storyboard.current")
        current_ref_value = slot.get("current_artifact_ref")
        if current_ref_value is None:
            errors.append("storyboard.current has no current artifact")
        else:
            current_ref = _as_object(current_ref_value, "storyboard current ref")
            version = _storyboard_version(storyboard_response)
            if current_ref.get("artifact_id") != _as_object(
                storyboard_response.get("document"), "storyboard document"
            ).get("id") or current_ref.get("version") != version.get("version"):
                errors.append("storyboard.current does not point at the returned version")
    return errors


def _scene_projection(
    storyboard: JsonObject,
    script: JsonObject,
) -> list[tuple[JsonObject, list[tuple[JsonObject, list[JsonObject]]]]]:
    """Project canonical content into Scene -> ClipGroup -> Shot hierarchy.

    This is a read-only view for the report and future semantic canvas.  It does
    not create another project state or rewrite the canonical Storyboard.
    """

    shots = [
        _as_object(value, "storyboard shot")
        for value in _as_list(storyboard.get("shots"), "storyboard shots")
    ]
    shot_by_id = {_string(shot.get("id"), "shot id"): shot for shot in shots}
    groups = [
        _as_object(value, "storyboard clip group")
        for value in _as_list(storyboard.get("clip_groups"), "storyboard clip_groups")
    ]
    grouped: dict[str, list[tuple[JsonObject, list[JsonObject]]]] = {}
    for group in groups:
        group_shots = [
            shot_by_id[shot_id]
            for value in _as_list(group.get("shot_ids"), "clip group shot_ids")
            for shot_id in [_string(value, "clip group shot id")]
            if shot_id in shot_by_id
        ]
        scene_id = _group_scene_id(group, shot_by_id) or "__unresolved__"
        grouped.setdefault(scene_id, []).append((group, group_shots))

    result: list[tuple[JsonObject, list[tuple[JsonObject, list[JsonObject]]]]] = []
    for value in _as_list(script.get("structured_scenes"), "structured_scenes"):
        scene = _as_object(value, "script scene")
        scene_id = _string(scene.get("id"), "script scene id")
        result.append((scene, grouped.pop(scene_id, [])))
    for scene_id, scene_groups in grouped.items():
        result.append(
            (
                {
                    "id": scene_id,
                    "heading": "未解析场次",
                    "summary": "分镜工件未能对应到当前剧本场次。",
                },
                scene_groups,
            )
        )
    return result


def _append_text_items(
    lines: list[str],
    label: str,
    value: JsonValue | None,
) -> None:
    items = [str(item) for item in _as_list(value, label) if str(item).strip()]
    if items:
        lines.append(f"- {label}：" + "；".join(items))


def _write_storyboard_markdown(
    storyboard_response: JsonObject,
    project: JsonObject,
    output_path: Path,
) -> None:
    storyboard = _extract_canonical_storyboard(storyboard_response)
    version = _storyboard_version(storyboard_response)
    script = _current_script(project)
    line_by_id = {
        _string(line.get("id"), "script line id"): line
        for value in _as_list(script.get("lines"), "script lines")
        for line in [_as_object(value, "script line")]
    }
    groups = _as_list(storyboard.get("clip_groups"), "clip_groups")
    shots = _as_list(storyboard.get("shots"), "shots")
    lines = [
        "# " + _string(storyboard.get("title"), "storyboard title"),
        "",
        "## 工件信息",
        "",
        f"- Storyboard document：`{_as_object(storyboard_response.get('document'), 'storyboard document').get('id')}`",
        f"- Storyboard version：`{version.get('id')}` / v{version.get('version')}",
        f"- ScriptVersion：`{storyboard.get('script_version_id')}`",
        f"- ProjectBibleVersion：`{storyboard.get('bible_version_id')}`",
        f"- ProductionSpec：`{storyboard.get('production_spec_document_id')}` / `{storyboard.get('production_spec_version_id')}`",
        f"- 总时长：{_number(storyboard.get('computed_duration_seconds'), 'computed duration'):g} 秒",
        f"- 场次 / ClipGroup / Shot：{len(_as_list(script.get('structured_scenes'), 'script scenes'))} / {len(groups)} / {len(shots)}",
        "",
        "## MiniMax Design / ComfyUI 投影",
        "",
        "完整 Storyboard 对应一个「分镜列表」语义文档节点；下方 Scene 只是该文档内的章节索引，"
        "不是独立 Canvas 文件或 ComfyUI 节点。每个 ClipGroup 稳定对应一个可选宫格 work item "
        "和一个 visual-gen work item，后续可投影为一个 H3 生成节点或子图；Comfy 的视觉 groups "
        "只用于排版。该投影不创建第二套项目状态。",
        "",
    ]
    for scene_index, (scene, scene_groups) in enumerate(
        _scene_projection(storyboard, script),
        1,
    ):
        lines.extend(
            [
                f"## 场次 {scene_index}：{scene.get('heading') or scene.get('id')}",
                "",
                f"- Scene ID：`{scene.get('id')}`",
                f"- 场次摘要：{scene.get('summary') or '-'}",
                f"- 场次目标时长：{scene.get('target_duration_seconds') or '-'} 秒",
                "",
            ]
        )
        if not scene_groups:
            lines.extend(["_本场次没有分镜工件。_", ""])
            continue
        for group_index, (group, group_shots) in enumerate(scene_groups, 1):
            lines.extend(
                [
                    f"### ClipGroup {scene_index}.{group_index}：{group.get('title') or group.get('id')}",
                    "",
                    f"- Group ID：`{group.get('id')}`",
                    f"- 时长：{group.get('duration_seconds')} 秒",
                    "- 后续执行投影：1 个 visual-gen work item → 1 个 H3 节点/子图",
                    f"- 生成意图：{group.get('generation_intent') or '-'}",
                    f"- 转场意图：{group.get('transition_intent') or '-'}",
                ]
            )
            _append_text_items(lines, "入场连续性", group.get("continuity_in"))
            _append_text_items(lines, "组内可见变化", group.get("visible_changes"))
            _append_text_items(lines, "出场连续性", group.get("continuity_out"))
            _append_text_items(lines, "引用说明", group.get("reference_notes"))
            lines.append("")
            for shot in group_shots:
                lines.extend(
                    [
                        f"#### Shot {shot.get('sequence')}：`{shot.get('id')}`",
                        "",
                        f"- 时长：{shot.get('duration_seconds')} 秒",
                        f"- 叙事目的：{shot.get('narrative_purpose') or '-'}",
                        f"- 画面：{shot.get('visual') or '-'}",
                        f"- 景别：{shot.get('framing') or '-'}",
                        f"- 镜头：{shot.get('camera') or '-'}",
                        f"- 声音：{shot.get('audio_intent') or '-'}",
                        f"- 出镜转场：{shot.get('transition_out') or '-'}",
                    ]
                )
                continuity_value = shot.get("continuity")
                if continuity_value is not None:
                    continuity = _as_object(continuity_value, "shot continuity")
                    lines.append(
                        "- 连续性："
                        + json.dumps(continuity, ensure_ascii=False, separators=(",", ":"))
                    )
                dialogue_ids = [
                    _string(value, "shot dialogue line id")
                    for value in _as_list(
                        shot.get("dialogue_line_ids"),
                        "shot dialogue_line_ids",
                    )
                ]
                if dialogue_ids:
                    lines.append("- 台词：")
                    for line_id in dialogue_ids:
                        script_line = line_by_id.get(line_id)
                        if script_line is None:
                            lines.append(f"  - `{line_id}`：未解析")
                            continue
                        lines.append(
                            "  - `"
                            + line_id
                            + "` "
                            + str(script_line.get("speaker") or "-")
                            + "："
                            + str(
                                script_line.get("spoken_text")
                                or script_line.get("original_text")
                                or "-"
                            )
                        )
                beats = _as_list(shot.get("timeline_beats"), "timeline_beats")
                if beats:
                    lines.append("- 时间线节拍：")
                    for beat_value in beats:
                        beat = _as_object(beat_value, "timeline beat")
                        lines.append(
                            "  - ["
                            + str(beat.get("start_seconds"))
                            + "-"
                            + str(beat.get("end_seconds"))
                            + "s] "
                            + str(beat.get("framing") or "-")
                            + " / "
                            + str(beat.get("camera") or "-")
                            + "："
                            + str(beat.get("visual") or "-")
                            + "；动作："
                            + str(beat.get("action") or "-")
                            + (
                                "；表演：" + str(beat.get("performance"))
                                if beat.get("performance")
                                else ""
                            )
                            + (
                                "；情绪：" + str(beat.get("emotion"))
                                if beat.get("emotion")
                                else ""
                            )
                            + (
                                "；声音：" + str(beat.get("sound"))
                                if beat.get("sound")
                                else ""
                            )
                        )
                lines.append("")
    if storyboard.get("timing_notes") or storyboard.get("notes"):
        lines.extend(["## 整体备注", ""])
        _append_text_items(lines, "时长备注", storyboard.get("timing_notes"))
        _append_text_items(lines, "其他备注", storyboard.get("notes"))
        lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _load_or_initialize_trace(
    output_dir: Path,
    *,
    base_url: str,
    project_id: str,
    run_id: str | None,
) -> JsonObject:
    trace_path = output_dir / "trace.json"
    if trace_path.exists():
        if run_id is None:
            raise ProbeFailure(
                "Output directory already contains a trace; pass --run-id to resume "
                "or choose a new directory"
            )
        try:
            trace = _as_object(
                cast(JsonValue, json.loads(trace_path.read_text(encoding="utf-8"))),
                "existing trace",
            )
        except json.JSONDecodeError as exc:
            raise ProbeFailure("Existing trace.json is not valid JSON") from exc
        if trace.get("project_id") != project_id or trace.get("run_id") != run_id:
            raise ProbeFailure("Existing trace belongs to another project or AgentRun")
        trace["outcome"] = "RUNNING"
        trace["finished_at"] = None
        trace["resumed_at"] = _utc_now()
        trace["validation"] = {"errors": []}
        return trace
    if run_id is not None:
        raise ProbeFailure(
            "Resuming requires the original output directory with trace.json and "
            "project_before.json"
        )
    return {
        "probe": "agent-storyboard-only-black-box",
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "finished_at": None,
        "base_url": base_url,
        "project_id": project_id,
        "run_id": None,
        "outcome": "RUNNING",
        "phases": [],
        "validation": {"errors": []},
        "raw_response_files": [],
    }


def _seen_action_ids(trace: JsonObject) -> set[str]:
    return {
        _string(action.get("id"), "trace action id")
        for phase_value in _as_list(trace.get("phases"), "trace.phases")
        for phase in [_as_object(phase_value, "trace phase")]
        for action_value in _as_list(phase.get("new_actions"), "trace new_actions")
        for action in [_as_object(action_value, "trace action")]
    }


def _seen_work_order_keys(trace: JsonObject) -> set[str]:
    return {
        _work_order_update_key(update)
        for phase_value in _as_list(trace.get("phases"), "trace.phases")
        for phase in [_as_object(phase_value, "trace phase")]
        for update_value in _as_list(
            phase.get("work_order_updates"),
            "trace work_order_updates",
        )
        for update in [_as_object(update_value, "trace work order update")]
    }


def _seen_specialist_turn_ids(trace: JsonObject) -> set[str]:
    return {
        _string(event.get("turn_id"), "trace specialist turn id")
        for phase_value in _as_list(trace.get("phases"), "trace.phases")
        for phase in [_as_object(phase_value, "trace phase")]
        for event_value in _as_list(
            phase.get("specialist_tool_events"),
            "trace specialist_tool_events",
        )
        for event in [_as_object(event_value, "trace specialist tool event")]
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an Agent-owned storyboard-only validation on an existing "
            "script-ready project."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend origin or full /api/short-drama base URL.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help="Existing script-ready project to storyboard.",
    )
    parser.add_argument(
        "--expected-script-version-id",
        default=DEFAULT_EXPECTED_SCRIPT_VERSION_ID,
        help=(
            "Optional exact ScriptVersion expected before and after the run. "
            "Pass an empty string to accept whichever version is current at start."
        ),
    )
    parser.add_argument(
        "--objective",
        default=DEFAULT_OBJECTIVE,
        help="Objective given to the Production Manager Agent.",
    )
    parser.add_argument(
        "--run-id",
        help="Resume an interrupted run using the original output directory.",
    )
    parser.add_argument(
        "--advance-max-actions",
        type=int,
        default=1,
        help="Actions requested per advance call; 1 keeps the trace easiest to inspect.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Optional transport fault boundary for one HTTP request. It is not an "
            "AgentRun, WorkOrder, or creative deadline."
        ),
    )
    parser.add_argument(
        "--progress-heartbeat-seconds",
        type=float,
        default=30.0,
        help="Trace refresh interval while one action is running; not a timeout.",
    )
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if not str(args.project_id).strip():
        raise ProbeFailure("--project-id must not be empty")
    if not str(args.objective).strip():
        raise ProbeFailure("--objective must not be empty")
    if int(args.advance_max_actions) < 1 or int(args.advance_max_actions) > 8:
        raise ProbeFailure("--advance-max-actions must be between 1 and 8")
    if (
        args.request_timeout_seconds is not None
        and float(args.request_timeout_seconds) <= 0
    ):
        raise ProbeFailure("--request-timeout-seconds must be positive")
    if float(args.progress_heartbeat_seconds) <= 0:
        raise ProbeFailure("--progress-heartbeat-seconds must be positive")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _validate_args(args)
    except ProbeFailure as exc:
        print(str(exc), file=sys.stderr)
        return 2

    project_id = str(args.project_id).strip()
    run_id_arg = str(args.run_id).strip() if args.run_id else None
    expected_script_version_id = (
        str(args.expected_script_version_id).strip()
        if str(args.expected_script_version_id).strip()
        else None
    )
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = RawResponseRecorder(output_dir)
    try:
        trace = _load_or_initialize_trace(
            output_dir,
            base_url=str(args.base_url),
            project_id=project_id,
            run_id=run_id_arg,
        )
    except ProbeFailure as exc:
        print(f"Storyboard probe could not start: {exc}", file=sys.stderr)
        return 2

    exit_code = 2
    before: JsonObject | None = None
    after: JsonObject | None = None
    storyboard_response: JsonObject | None = None
    storyboard_draft_response: JsonObject | None = None
    try:
        client = AgentStoryboardApiClient(
            str(args.base_url),
            timeout_seconds=(
                float(args.request_timeout_seconds)
                if args.request_timeout_seconds is not None
                else None
            ),
            recorder=recorder,
        )
        before_path = output_dir / "project_before.json"
        if run_id_arg is None:
            before = client.get_project(project_id)
            before_path.write_text(
                json.dumps(before, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            current_script_id = _string(
                _project_record(before).get("current_script_version_id"),
                "current script version id",
            )
            if (
                expected_script_version_id is not None
                and current_script_id != expected_script_version_id
            ):
                raise ProbeFailure(
                    f"Project currently uses {current_script_id}, expected "
                    f"{expected_script_version_id}"
                )
            run_view = client.create_agent_run(project_id, str(args.objective).strip())
            run_id = _string(_run_object(run_view).get("id"), "created AgentRun id")
            trace["run_id"] = run_id
            operation = "create_agent_run"
        else:
            if not before_path.exists():
                raise ProbeFailure("Resume output is missing project_before.json")
            try:
                before = _as_object(
                    cast(
                        JsonValue,
                        json.loads(before_path.read_text(encoding="utf-8")),
                    ),
                    "project_before.json",
                )
            except json.JSONDecodeError as exc:
                raise ProbeFailure("project_before.json is not valid JSON") from exc
            run_id = run_id_arg
            run_view = client.get_agent_run(run_id)
            if _run_object(run_view).get("project_id") != project_id:
                raise ProbeFailure("Resumed AgentRun belongs to another project")
            operation = "resume_agent_run"

        board = client.get_production_board(run_id)
        phases = _as_list(trace.get("phases"), "trace.phases")
        seen_actions = _seen_action_ids(trace)
        seen_work_orders = _seen_work_order_keys(trace)
        seen_specialist_turns = _seen_specialist_turn_ids(trace)
        initial_specialist_events = _collect_specialist_tool_events(
            client,
            _board_work_order_ids(board),
            seen_specialist_turns,
        )
        phases.append(
            _trace_phase(
                operation=operation,
                run_view=run_view,
                board=board,
                seen_action_ids=seen_actions,
                seen_work_order_keys=seen_work_orders,
                specialist_tool_events=initial_specialist_events,
            )
        )
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)

        while True:
            status = _run_status(run_view)
            if status == "COMPLETED":
                break
            if status in {"FAILED", "CANCELLED"}:
                raise ProbeFailure(f"AgentRun reached terminal status {status}")
            if status == "WAITING":
                waiting_blocker = _waiting_blocker(run_view, board)
                if waiting_blocker is not None:
                    raise ProbeFailure(waiting_blocker)
            progress_before = _progress_fingerprint(run_view, board)
            try:
                advanced = _advance_with_heartbeats(
                    client,
                    run_id,
                    max_actions=int(args.advance_max_actions),
                    heartbeat_seconds=float(args.progress_heartbeat_seconds),
                    trace=trace,
                    recorder=recorder,
                    output_dir=output_dir,
                )
            except ApiHttpError as exc:
                observed_run = client.get_agent_run(run_id)
                observed_board = client.get_production_board(run_id)
                recovery_note = _resumable_agent_conflict(
                    exc,
                    observed_run,
                    observed_board,
                )
                if recovery_note is None:
                    raise
                run_view = observed_run
                board = observed_board
                recovery_specialist_events = _collect_specialist_tool_events(
                    client,
                    _board_work_order_ids(board),
                    seen_specialist_turns,
                )
                phases.append(
                    _trace_phase(
                        operation="advance_agent_run(recoverable_worker_failure)",
                        run_view=run_view,
                        board=board,
                        seen_action_ids=seen_actions,
                        seen_work_order_keys=seen_work_orders,
                        specialist_tool_events=recovery_specialist_events,
                        notes=[recovery_note],
                    )
                )
                _persist_trace(trace, recorder=recorder, output_dir=output_dir)
                if _progress_fingerprint(run_view, board) == progress_before:
                    raise ProbeFailure(
                        "Recoverable Worker failure did not persist a semantic "
                        "handoff for the next owner"
                    ) from exc
                continue
            run_view = _as_object(advanced.get("run"), "advance response run")
            board = client.get_production_board(run_id)
            advanced_work_order_ids = _as_list(
                advanced.get("advanced_work_order_ids"),
                "advanced_work_order_ids",
            )
            specialist_events = _collect_specialist_tool_events(
                client,
                [
                    _string(value, "advanced work order id")
                    for value in advanced_work_order_ids
                ],
                seen_specialist_turns,
            )
            phases.append(
                _trace_phase(
                    operation=(
                        "advance_agent_run(max_actions="
                        + str(args.advance_max_actions)
                        + ")"
                    ),
                    run_view=run_view,
                    board=board,
                    seen_action_ids=seen_actions,
                    seen_work_order_keys=seen_work_orders,
                    executed_action_ids=_as_list(
                        advanced.get("executed_action_ids"),
                        "executed_action_ids",
                    ),
                    created_work_order_ids=_as_list(
                        advanced.get("created_work_order_ids"),
                        "created_work_order_ids",
                    ),
                    advanced_work_order_ids=advanced_work_order_ids,
                    specialist_tool_events=specialist_events,
                    notes=_as_list(advanced.get("notes"), "advance notes"),
                )
            )
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            if (
                status == "WAITING"
                and _run_status(run_view) == "WAITING"
                and _progress_fingerprint(run_view, board) == progress_before
            ):
                raise ProbeFailure(
                    "Public Agent advance returned without semantic progress from a "
                    "resumable WAITING handoff"
                )

        run_view = client.get_agent_run(run_id)
        board = client.get_production_board(run_id)
        after = client.get_project(project_id)
        storyboard_response = client.get_storyboard(project_id)
        storyboard_draft_ref = _latest_storyboard_draft_ref_from_board(
            board,
            storyboard_response=storyboard_response,
            project=after,
        )
        storyboard_draft_response = client.get_production_document(
            project_id,
            _string(
                storyboard_draft_ref.get("artifact_id"),
                "storyboard draft artifact id",
            ),
            version_id=_string(
                storyboard_draft_ref.get("version_id"),
                "storyboard draft version id",
            ),
        )
        (output_dir / "project.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "storyboard.json").write_text(
            json.dumps(storyboard_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "storyboard_draft.json").write_text(
            json.dumps(storyboard_draft_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_storyboard_markdown(
            storyboard_response,
            after,
            output_dir / "storyboard.md",
        )
        errors = _validate_storyboard_only_result(
            run_view=run_view,
            before=before,
            after=after,
            storyboard_response=storyboard_response,
            storyboard_draft_response=storyboard_draft_response,
            storyboard_draft_ref=storyboard_draft_ref,
            trace=trace,
            expected_script_version_id=expected_script_version_id,
        )
        storyboard = _extract_canonical_storyboard(storyboard_response)
        final_draft = _extract_storyboard_draft(storyboard_draft_response)
        final_verification = final_draft.get("verification")
        trace["validation"] = {
            "errors": _json_strings(errors),
            "storyboard_document_id": _as_object(
                storyboard_response.get("document"),
                "storyboard document",
            ).get("id"),
            "storyboard_version_id": _storyboard_version(storyboard_response).get("id"),
            "storyboard_draft_document_id": storyboard_draft_ref.get("artifact_id"),
            "storyboard_draft_version": storyboard_draft_ref.get("version"),
            "storyboard_draft_verification_status": (
                final_verification.get("status")
                if isinstance(final_verification, dict)
                else None
            ),
            "computed_duration_seconds": storyboard.get("computed_duration_seconds"),
            "scene_count": len(
                _as_list(_current_script(after).get("structured_scenes"), "script scenes")
            ),
            "clip_group_count": len(
                _as_list(storyboard.get("clip_groups"), "clip_groups")
            ),
            "shot_count": len(_as_list(storyboard.get("shots"), "shots")),
        }
        if errors:
            trace["outcome"] = "FAILED_VALIDATION"
            exit_code = 1
        else:
            trace["outcome"] = "PASSED"
            exit_code = 0
    except ProbeFailure as exc:
        trace["outcome"] = "FAILED_PROBE"
        trace["validation"] = {"errors": [str(exc)]}
        print(f"Storyboard validation probe failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        trace["outcome"] = "INTERRUPTED"
        trace["validation"] = {
            "errors": [
                "Probe interrupted; resume with the recorded project_id, run_id, "
                "and output directory"
            ]
        }
        exit_code = 130
        print("Storyboard probe interrupted; resumable IDs were preserved.", file=sys.stderr)
    except Exception as exc:
        # Preserve an inspectable trace for implementation bugs as well as
        # expected API conflicts.  The exception type remains visible so the
        # caller can distinguish a harness/backend bug from creative quality.
        trace["outcome"] = "FAILED_PROBE"
        trace["validation"] = {
            "errors": [f"Unexpected {type(exc).__name__}: {exc}"]
        }
        print(
            f"Storyboard validation probe hit an unexpected error: {exc}",
            file=sys.stderr,
        )
    finally:
        trace["finished_at"] = _utc_now()
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if after is not None and not (output_dir / "project.json").exists():
            (output_dir / "project.json").write_text(
                json.dumps(after, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if storyboard_response is not None and not (
            output_dir / "storyboard.json"
        ).exists():
            (output_dir / "storyboard.json").write_text(
                json.dumps(storyboard_response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if storyboard_draft_response is not None and not (
            output_dir / "storyboard_draft.json"
        ).exists():
            (output_dir / "storyboard_draft.json").write_text(
                json.dumps(
                    storyboard_draft_response,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    print(
        json.dumps(
            {
                "outcome": trace.get("outcome"),
                "project_id": trace.get("project_id"),
                "run_id": trace.get("run_id"),
                "output_dir": str(output_dir),
                "trace": str(output_dir / "trace.md"),
                "storyboard": (
                    str(output_dir / "storyboard.md")
                    if (output_dir / "storyboard.md").exists()
                    else None
                ),
                "storyboard_draft": (
                    str(output_dir / "storyboard_draft.json")
                    if (output_dir / "storyboard_draft.json").exists()
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
