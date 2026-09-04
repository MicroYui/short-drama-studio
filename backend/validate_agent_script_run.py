"""Black-box validation for an Agent-authored screenplay-only project run.

The probe intentionally uses only the public Agent project/run API.  It does not
call Tool, provider, image, video, or H3 endpoints.  Every Agent advance is
bounded to one action so the output directory contains a readable phase trace.
There is no end-to-end deadline or advance-count limit.
"""

from __future__ import annotations

import argparse
import json
from queue import Empty, Queue
import sys
from threading import Lock, Thread
from time import monotonic
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

DEFAULT_INPUT_TEXT = """
创作一集约 3 分钟的中文竖屏 AI 爽剧完整剧本。主题是：所有人的数学水平突然倒退 100 倍，只有主角“我”完全不变。让观众尽快通过可见行动看懂异常，随后用连续升级的公开震惊、利益变化和身份翻盘兑现爽感。

允许荒诞、无厘头和适度逻辑跳跃，好看、节奏和情绪回报优先于现实主义论证。可以从日常小事暴露优势，再把影响扩大，但不要照搬固定桥段。人物、场景、冲突方式、叙事方法以及是否留下续集动力，都由 Agent 根据整体效果自主决定；本集需要形成完整且可见的情绪回报。对白自然可演，动作和反应共同推进剧情。

只生成完整剧本及内容审校，完成后停下，不生成分镜、资产、图片、视频、时间线或导出。
""".strip()
INTERNAL_PLAN_REBUILD_NOTE = (
    "The Production Manager revised the plan from the latest ContextPack"
)


class ProbeFailure(RuntimeError):
    """Expected validation or transport failure with a concise user message."""


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: JsonObject


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _as_object(value: JsonValue, label: str) -> JsonObject:
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
    if not isinstance(value, str) or not value:
        raise ProbeFailure(f"{label} must be a non-empty string")
    return value


def _json_string_list(values: list[str]) -> list[JsonValue]:
    result: list[JsonValue] = []
    result.extend(values)
    return result


class RawResponseRecorder:
    def __init__(self, output_dir: Path) -> None:
        self._lock = Lock()
        self.raw_dir = output_dir / "raw_responses"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.raw_dir.glob("[0-9][0-9][0-9]_*.json"))
        self._sequence = max(
            (int(path.name.split("_", 1)[0]) for path in existing),
            default=0,
        )
        self.files: list[str] = [
            f"raw_responses/{path.name}" for path in existing
        ]

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
            self.files.append(f"raw_responses/{filename}")

    def file_snapshot(self) -> list[str]:
        with self._lock:
            return list(self.files)


class AgentApiClient:
    """Narrow client exposing only the four public calls allowed by this probe."""

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

    def create_agent_project(self, payload: JsonObject) -> JsonObject:
        return self._request(
            "POST",
            "/agent-projects",
            label="create_agent_project",
            payload=payload,
        ).body

    def advance_agent_run(self, run_id: str) -> JsonObject:
        return self._request(
            "POST",
            f"/agent-runs/{quote(run_id, safe='')}/advance",
            label="advance_agent_run",
            payload={"max_actions": 1},
        ).body

    def get_agent_run(self, run_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/agent-runs/{quote(run_id, safe='')}",
            label="get_agent_run",
            payload=None,
        ).body

    def get_project(self, project_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}",
            label="get_project",
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
        data = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{self._api_root}{path}",
            data=data,
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
            raise ProbeFailure(
                f"{method} {path} returned HTTP {status_code}; see raw responses"
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


def _run_object(run_view: JsonObject) -> JsonObject:
    snapshot = _as_object(run_view.get("snapshot"), "run.snapshot")
    return _as_object(snapshot.get("run"), "run.snapshot.run")


def _run_status(run_view: JsonObject) -> str:
    return _string(_run_object(run_view).get("status"), "run status")


def _summarize_action(action: JsonObject) -> JsonObject:
    return {
        "id": action.get("id"),
        "plan_step_id": action.get("plan_step_id"),
        "kind": action.get("kind"),
        "tool_name": action.get("tool_name"),
        "status": action.get("status"),
        "input_summary": action.get("input_summary"),
        "output_summary": action.get("output_summary"),
        "error_message": action.get("error_message"),
        "duration_ms": action.get("duration_ms"),
        "artifact_refs": action.get("artifact_refs", []),
    }


def _content_planning_step(run_view: JsonObject) -> str | None:
    context_value = run_view.get("context")
    if context_value is None:
        return None
    context = _as_object(context_value, "run.context")
    for value in _as_list(context.get("artifacts"), "run.context.artifacts"):
        artifact = _as_object(value, "context artifact")
        ref = _as_object(artifact.get("ref"), "context artifact ref")
        if ref.get("artifact_type") != "content_planning_job":
            continue
        title = artifact.get("title")
        return title if isinstance(title, str) and title else None
    return None


def _content_planning_record(run_view: JsonObject) -> JsonObject | None:
    """Read public creative trace evidence, with artifact fallback for old servers."""

    trace_value = run_view.get("content_planning_trace")
    if trace_value is not None:
        return _as_object(trace_value, "run.content_planning_trace")

    context_value = run_view.get("context")
    if context_value is None:
        return None
    context = _as_object(context_value, "run.context")
    for value in _as_list(context.get("artifacts"), "run.context.artifacts"):
        artifact = _as_object(value, "context artifact")
        ref = _as_object(artifact.get("ref"), "context artifact ref")
        if ref.get("artifact_type") != "content_planning_job":
            continue
        summary = artifact.get("summary")
        if not isinstance(summary, str):
            return None
        json_start = summary.find("{")
        if json_start < 0:
            return None
        try:
            return _as_object(
                cast(JsonValue, json.loads(summary[json_start:])),
                "content planning artifact payload",
            )
        except json.JSONDecodeError:
            return None
    return None


def _timing_assessment_key(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _new_scene_timing_assessments(
    run_view: JsonObject,
    seen_keys: set[str],
) -> list[JsonValue]:
    planning = _content_planning_record(run_view)
    if planning is None:
        return []
    result: list[JsonValue] = []
    for value in _as_list(
        planning.get("scene_timing_assessments"),
        "planning scene_timing_assessments",
    ):
        assessment = _as_object(value, "scene timing assessment")
        key = _timing_assessment_key(assessment)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        result.append(assessment)
    return result


def _blueprint_projection(outline_json: str) -> JsonObject | None:
    try:
        outline = _as_object(
            cast(JsonValue, json.loads(outline_json)),
            "content planning outline",
        )
    except (json.JSONDecodeError, ProbeFailure):
        return None
    scenes: list[JsonValue] = []
    for value in _as_list(outline.get("scenes"), "content planning outline scenes"):
        scene = _as_object(value, "content planning outline scene")
        scenes.append(
            {
                "sequence": scene.get("sequence"),
                "heading": scene.get("heading"),
                "summary": scene.get("summary"),
                "narrative_purpose": scene.get("narrative_purpose"),
                "playable_actions": scene.get("playable_actions", []),
                "target_duration_seconds": scene.get("target_duration_seconds"),
            }
        )
    return {
        "title": outline.get("title"),
        "story_summary": outline.get("story_summary"),
        "target_duration_seconds": outline.get("target_duration_seconds"),
        "ending_contract": outline.get("ending_contract"),
        "scenes": scenes,
    }


def _semantic_event_key(kind: str, payload: JsonObject) -> str:
    return kind + ":" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _refresh_content_planning_trace(
    trace: JsonObject,
    run_view: JsonObject,
) -> None:
    planning = _content_planning_record(run_view)
    if planning is None:
        return
    trace["content_planning_trace"] = planning

    if trace.get("story_blueprint") is None:
        outline_json = planning.get("outline_json")
        if isinstance(outline_json, str):
            blueprint = _blueprint_projection(outline_json)
            if blueprint is not None:
                trace["story_blueprint"] = blueprint

    events_value = trace.get("semantic_events")
    events = _as_list(events_value, "trace.semantic_events")
    if events_value is None:
        trace["semantic_events"] = events
    seen = {
        _semantic_event_key(
            _string(event.get("kind"), "semantic event kind"),
            _as_object(event.get("payload"), "semantic event payload"),
        )
        for value in events
        for event in [_as_object(value, "semantic event")]
    }
    event_fields = (
        ("SCRIPT_CRITIC", "review_report"),
        ("REPAIR_PLAN", "latest_repair_plan"),
        ("REVISION_VERIFIER", "latest_revision_verification"),
        ("FINAL_COLD_READ_COMPATIBILITY", "final_cold_read_report"),
    )
    for kind, field_name in event_fields:
        payload_value = planning.get(field_name)
        if payload_value is None:
            continue
        payload = _as_object(payload_value, f"planning {field_name}")
        key = _semantic_event_key(kind, payload)
        if key in seen:
            continue
        seen.add(key)
        events.append(
            {
                "kind": kind,
                "recorded_at": _utc_now(),
                "revision_round": planning.get("revision_round"),
                "payload": payload,
            }
        )


def _trace_phase(
    *,
    index: int,
    operation: str,
    run_view: JsonObject,
    seen_action_ids: set[str],
    seen_timing_assessment_keys: set[str],
    executed_action_ids: list[JsonValue] | None = None,
    notes: list[JsonValue] | None = None,
    content_planning_step_before: str | None = None,
) -> JsonObject:
    snapshot = _as_object(run_view.get("snapshot"), "run.snapshot")
    actions = _as_list(snapshot.get("actions"), "run.snapshot.actions")
    new_actions: list[JsonValue] = []
    for value in actions:
        action = _as_object(value, "run action")
        action_id = _string(action.get("id"), "run action id")
        if action_id in seen_action_ids:
            continue
        seen_action_ids.add(action_id)
        new_actions.append(_summarize_action(action))

    current_plan_value = snapshot.get("current_plan")
    current_plan = (
        _as_object(current_plan_value, "run.snapshot.current_plan")
        if current_plan_value is not None
        else None
    )
    ready_steps = _as_list(run_view.get("ready_steps"), "run.ready_steps")
    content_planning_step_after = _content_planning_step(run_view)
    return {
        "phase": index,
        "recorded_at": _utc_now(),
        "operation": operation,
        "run_status": _run_status(run_view),
        # Keep the original field for old trace readers.  New human traces use
        # the explicit before/after pair so action duration is not attributed
        # to the next planning cursor.
        "content_planning_step": content_planning_step_after,
        "content_planning_step_before": content_planning_step_before,
        "content_planning_step_after": content_planning_step_after,
        "plan_revision": current_plan.get("revision") if current_plan else None,
        "plan_summary": current_plan.get("summary") if current_plan else None,
        "plan_goal_complete": current_plan.get("goal_complete") if current_plan else None,
        "ready_steps": [
            {
                "id": _as_object(step, "ready step").get("id"),
                "title": _as_object(step, "ready step").get("title"),
                "kind": _as_object(step, "ready step").get("kind"),
                "tool_name": _as_object(step, "ready step").get("tool_name"),
            }
            for step in ready_steps
        ],
        "executed_action_ids": executed_action_ids or [],
        "new_actions": new_actions,
        "scene_timing_assessments": _new_scene_timing_assessments(
            run_view,
            seen_timing_assessment_keys,
        ),
        "notes": notes or [],
    }


def _markdown_excerpt(value: JsonValue | None, *, limit: int = 360) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _warning_candidates(
    run_view: JsonObject,
    project: JsonObject,
    response_notes: list[str],
) -> list[str]:
    candidates = list(response_notes)
    snapshot = _as_object(run_view.get("snapshot"), "run.snapshot")
    for value in _as_list(snapshot.get("actions"), "run.snapshot.actions"):
        action = _as_object(value, "run action")
        text = action.get("error_message")
        if isinstance(text, str):
            candidates.append(text)

    context_value = run_view.get("context")
    if context_value is not None:
        context = _as_object(context_value, "run.context")
        for key in ("blockers", "constraints"):
            for item in _as_list(context.get(key), f"run.context.{key}"):
                if isinstance(item, str):
                    candidates.append(item)

    document = _as_object(project.get("document"), "project.document")
    planning_value = document.get("planning")
    if planning_value is not None:
        planning = _as_object(planning_value, "project.document.planning")
        for item in _as_list(planning.get("warnings"), "project planning warnings"):
            if isinstance(item, str):
                candidates.append(item)
    return candidates


def _duration_label(seconds: float) -> str:
    minutes = seconds / 60.0
    if minutes.is_integer():
        return f"{minutes:g} 分钟"
    return f"{seconds:g} 秒"


def _build_agent_project_payload(
    project_name: str,
    input_text: str,
    target_duration_seconds: float,
) -> JsonObject:
    duration_label = _duration_label(target_duration_seconds)
    return {
        "project": {
            "name": project_name,
            "source_mode": "IDEA_EXPANSION",
            "production_profile": "AI_SHORT_DRAMA",
            "target_duration_seconds": target_duration_seconds,
            "input_text": input_text,
            "run_mode": "AUTO_DRAFT",
            "content_planning_output": "SCRIPT_ONLY",
        },
        "execution_target": "MOCK",
        "render_export": False,
        "objective": (
            f"由 Agent 独立完成约 {duration_label}的可评审完整剧本；"
            "完成剧本后立即停止，"
            "不生成分镜、资产、生成任务、take 或导出。"
        ),
    }


def _current_screenplay(project: JsonObject) -> tuple[JsonObject, str]:
    document = _as_object(project.get("document"), "project.document")
    project_record = _as_object(document.get("project"), "project.document.project")
    current_id = _string(
        project_record.get("current_script_version_id"),
        "current_script_version_id",
    )
    versions = _as_list(document.get("script_versions"), "project.document.script_versions")
    selected: JsonObject | None = None
    for value in versions:
        version = _as_object(value, "script version")
        if version.get("id") == current_id:
            selected = version
            break
    if selected is None:
        raise ProbeFailure(f"Current script version {current_id!r} was not returned")
    screenplay = _string(selected.get("screenplay_text"), "script screenplay_text").strip()
    if not screenplay:
        raise ProbeFailure("Current script screenplay_text is empty")
    return selected, screenplay


def _validate_script_only_result(
    run_view: JsonObject,
    project: JsonObject,
    response_notes: list[str],
) -> tuple[list[str], JsonObject, str]:
    errors: list[str] = []
    status = _run_status(run_view)
    if status != "COMPLETED":
        errors.append(f"AgentRun ended with status {status}, expected COMPLETED")

    document = _as_object(project.get("document"), "project.document")
    prohibited_collections = {
        "assets": document.get("assets"),
        "shots": document.get("shots"),
        "tasks": project.get("tasks"),
        "takes": project.get("takes"),
    }
    for name, value in prohibited_collections.items():
        count = len(_as_list(value, name))
        if count:
            errors.append(f"SCRIPT_ONLY run unexpectedly generated {count} {name}")

    script_version, screenplay = _current_screenplay(project)
    return errors, script_version, screenplay


def _append_text_list(
    lines: list[str],
    label: str,
    values: JsonValue | None,
) -> None:
    items = [str(value) for value in _as_list(values, label) if str(value).strip()]
    if items:
        lines.append(f"- {label}：" + "；".join(items))


def _append_story_orientation(lines: list[str], planning: JsonObject) -> None:
    lines.extend(["## 创作定向", ""])
    source_value = planning.get("source_contract")
    if source_value is not None:
        source = _as_object(source_value, "planning source contract")
        _append_text_list(lines, "用户明确要求", source.get("explicit_commitments"))
        _append_text_list(lines, "用户明确禁止", source.get("explicit_prohibitions"))
        _append_text_list(lines, "留给 Agent 的选择", source.get("open_creative_choices"))
        lines.append(
            "- 续集偏好："
            + str(source.get("continuation_preference") or "未记录")
        )

    skill_ids = _as_list(
        planning.get("selected_narrative_skill_ids"),
        "planning selected narrative skills",
    )
    skill_reasons = _as_list(
        planning.get("skill_selection_reasons"),
        "planning skill selection reasons",
    )
    if skill_ids:
        lines.append("- Narrative Skills：")
        for index, skill_id in enumerate(skill_ids):
            reason = skill_reasons[index] if index < len(skill_reasons) else "未记录理由"
            lines.append(f"  - `{skill_id}`：{reason}")
    else:
        lines.append("- Narrative Skills：未选择额外叙事 Skill")
    decision = planning.get("narrative_skill_decision")
    if isinstance(decision, str) and decision.strip():
        lines.append("- Skill 决策说明：" + decision)

    intent_value = planning.get("story_intent")
    if intent_value is not None:
        intent = _as_object(intent_value, "planning story intent")
        lines.extend(
            [
                "- StoryIntent：",
                "  - 观众体验：" + str(intent.get("audience_experience") or "未记录"),
                "  - 戏剧引擎：" + str(intent.get("dramatic_engine") or "未记录"),
                "  - 推进策略：" + str(intent.get("progression_strategy") or "未记录"),
                "  - 调性与可信度："
                + str(intent.get("tone_and_plausibility") or "未记录"),
            ]
        )
        protected = _as_list(intent.get("protected_elements"), "protected elements")
        risks = _as_list(intent.get("drift_risks"), "drift risks")
        if protected:
            lines.append("  - 需保护：" + "；".join(str(value) for value in protected))
        if risks:
            lines.append("  - 漂移风险：" + "；".join(str(value) for value in risks))
    lines.append("")


def _append_story_blueprint(lines: list[str], trace: JsonObject) -> None:
    blueprint_value = trace.get("story_blueprint")
    if blueprint_value is None:
        return
    blueprint = _as_object(blueprint_value, "trace story blueprint")
    lines.extend(
        [
            "## 初始故事蓝图",
            "",
            "- 标题：" + str(blueprint.get("title") or "未记录"),
            "- 故事概述：" + str(blueprint.get("story_summary") or "未记录"),
            "- 目标时长："
            + str(blueprint.get("target_duration_seconds") or "未记录")
            + " 秒",
        ]
    )
    scenes = _as_list(blueprint.get("scenes"), "trace blueprint scenes")
    if scenes:
        lines.append("- 场次：")
        for value in scenes:
            scene = _as_object(value, "trace blueprint scene")
            lines.append(
                "  - 第 "
                + str(scene.get("sequence"))
                + " 场 · "
                + str(scene.get("heading") or "未命名")
                + "（"
                + str(scene.get("target_duration_seconds") or "?")
                + " 秒）："
                + str(scene.get("narrative_purpose") or scene.get("summary") or "未记录")
            )
    lines.append("")


def _append_scene_outputs(lines: list[str], planning: JsonObject) -> None:
    outline_json = planning.get("outline_json")
    if not isinstance(outline_json, str):
        return
    try:
        outline = _as_object(
            cast(JsonValue, json.loads(outline_json)),
            "current content planning outline",
        )
    except (json.JSONDecodeError, ProbeFailure):
        return
    scenes = _as_list(outline.get("scenes"), "current content planning scenes")
    rendered: list[tuple[JsonObject, str]] = []
    for value in scenes:
        scene = _as_object(value, "current content planning scene")
        screenplay = scene.get("screenplay_text")
        if isinstance(screenplay, str) and screenplay.strip():
            rendered.append((scene, screenplay))
    if not rendered:
        return
    lines.extend(["## 当前场次产物", ""])
    for scene, screenplay in rendered:
        lines.append(
            "- 第 "
            + str(scene.get("sequence"))
            + " 场 · "
            + str(scene.get("heading") or "未命名")
            + "："
            + _markdown_excerpt(screenplay, limit=520)
        )
    lines.append("")


def _append_review_report(lines: list[str], report: JsonObject) -> None:
    lines.append("  - 结论：" + str(report.get("summary") or "未记录"))
    timing = report.get("performance_timing_assessment")
    if isinstance(timing, str) and timing.strip():
        lines.append("  - 自然表演时长判断：" + timing)
    lines.append(
        "  - 是否建议修订：" + ("是" if report.get("requires_revision") else "否")
    )
    for value in _as_list(report.get("issues"), "review report issues"):
        issue = _as_object(value, "review report issue")
        lines.append(
            "  - 问题 "
            + str(issue.get("issue_id") or issue.get("category") or "未命名")
            + "："
            + str(issue.get("observation") or "未记录")
            + "；建议："
            + str(issue.get("recommendation") or "未记录")
        )


def _append_semantic_review_history(lines: list[str], trace: JsonObject) -> None:
    events = _as_list(trace.get("semantic_events"), "trace semantic events")
    planning_value = trace.get("content_planning_trace")
    planning = (
        _as_object(planning_value, "trace content planning")
        if planning_value is not None
        else None
    )
    if not events and planning is None:
        return
    lines.extend(["## Critic、修订与验证", ""])
    repair_index = 0
    verifier_index = 0
    for value in events:
        event = _as_object(value, "semantic event")
        kind = event.get("kind")
        payload = _as_object(event.get("payload"), "semantic event payload")
        if kind == "SCRIPT_CRITIC":
            lines.append("- 完整剧本 Critic：")
            _append_review_report(lines, payload)
        elif kind == "REPAIR_PLAN":
            repair_index += 1
            lines.append(f"- 修订方案 {repair_index}：")
            lines.append("  - 总结：" + str(payload.get("summary") or "未记录"))
            for action_value in _as_list(payload.get("actions"), "repair actions"):
                action = _as_object(action_value, "repair action")
                if not action.get("should_revise"):
                    continue
                lines.append(
                    "  - 影响场次 "
                    + str(action.get("affected_scene_sequences") or [])
                    + "："
                    + str(action.get("recommended_change") or action.get("rationale") or "未记录")
                )
        elif kind == "REVISION_VERIFIER":
            verifier_index += 1
            lines.append(f"- 增强 Verifier {verifier_index}：")
            lines.append("  - 结论：" + str(payload.get("summary") or "未记录"))
            lines.append("  - 接受当前稿：" + ("是" if payload.get("accepted") else "否"))
            rationale = payload.get("followup_rationale")
            if isinstance(rationale, str) and rationale.strip():
                lines.append("  - 后续判断：" + rationale)
            for violation_value in _as_list(
                payload.get("violations"), "revision verifier violations"
            ):
                violation = _as_object(violation_value, "revision verifier violation")
                lines.append(
                    "  - 未解决："
                    + str(violation.get("observation") or "未记录")
                    + "；下一步："
                    + str(violation.get("repair_instruction") or "未记录")
                )
        elif kind == "FINAL_COLD_READ_COMPATIBILITY":
            lines.append("- 独立 Final Cold Read（兼容旧运行）：")
            _append_review_report(lines, payload)

    if planning is not None:
        ledger = _as_list(planning.get("issue_ledger"), "planning issue ledger")
        if ledger:
            lines.append("- 当前 Issue Ledger：")
            for value in ledger:
                issue = _as_object(value, "issue ledger entry")
                lines.append(
                    "  - "
                    + str(issue.get("issue_id") or issue.get("semantic_cluster") or "未命名")
                    + " ["
                    + str(issue.get("state") or "未记录状态")
                    + "]："
                    + str(issue.get("observation") or "未记录")
                )
    lines.append("")


def _append_completion_basis(lines: list[str], trace: JsonObject) -> None:
    planning_value = trace.get("content_planning_trace")
    if planning_value is None:
        return
    planning = _as_object(planning_value, "trace content planning")
    lines.extend(
        [
            "## Agent 停止依据",
            "",
            "- Content Planning Job："
            + str(planning.get("status") or "未记录")
            + " / "
            + str(planning.get("current_step") or "未记录"),
            "- 用户可见剧本修订轮次："
            + str(planning.get("revision_round") or 0),
            "- 独立 Final Cold Read："
            + ("已执行" if planning.get("final_cold_read_used") else "未执行"),
        ]
    )
    verifier_value = planning.get("latest_revision_verification")
    review_value = planning.get("review_report")
    if verifier_value is not None:
        verifier = _as_object(verifier_value, "latest revision verification")
        lines.append("- 最终语义判断：" + str(verifier.get("summary") or "未记录"))
        rationale = verifier.get("followup_rationale")
        if isinstance(rationale, str) and rationale.strip():
            lines.append("- 停止/继续理由：" + rationale)
    elif review_value is not None:
        review = _as_object(review_value, "latest review report")
        lines.append("- 最终语义判断：" + str(review.get("summary") or "未记录"))
        rationale = review.get("followup_rationale")
        if isinstance(rationale, str) and rationale.strip():
            lines.append("- 停止/继续理由：" + rationale)
    else:
        lines.append("- 最终语义判断：尚未记录")
    lines.append("")


def _write_trace_markdown(trace: JsonObject, output_path: Path) -> None:
    lines = [
        "# Agent screenplay validation trace",
        "",
        f"- Started: {trace.get('started_at')}",
        f"- Finished: {trace.get('finished_at')}",
        f"- Project: {trace.get('project_id')}",
        f"- AgentRun: {trace.get('run_id')}",
        f"- Outcome: {trace.get('outcome')}",
        "",
    ]
    planning: JsonObject | None = None
    planning_value = trace.get("content_planning_trace")
    if planning_value is not None:
        planning = _as_object(planning_value, "trace content planning")
        _append_story_orientation(lines, planning)
    _append_story_blueprint(lines, trace)
    if planning is not None:
        _append_scene_outputs(lines, planning)
    lines.extend(["## 执行进度", ""])
    in_flight_value = trace.get("in_flight")
    if in_flight_value is not None:
        in_flight = _as_object(in_flight_value, "trace.in_flight")
        lines.extend(
            [
                "## Currently running",
                "",
                f"- Observed: {in_flight.get('observed_at')}",
                f"- Elapsed: {in_flight.get('elapsed_seconds')} seconds",
                (
                    "- Content planning step: "
                    f"{in_flight.get('content_planning_step') or '-'}"
                ),
                f"- Run status: {in_flight.get('run_status') or '-'}",
                "",
            ]
        )
    for phase_value in _as_list(trace.get("phases"), "trace.phases"):
        phase = _as_object(phase_value, "trace phase")
        step_before = phase.get("content_planning_step_before")
        step_after = (
            phase.get("content_planning_step_after")
            or phase.get("content_planning_step")
        )
        transition_line = (
            f"- Content planning transition: {step_before} → {step_after or '-'}"
            if isinstance(step_before, str) and step_before
            else f"- Content planning step: {step_after or '-'}"
        )
        lines.extend(
            [
                f"### {phase.get('phase')}. {phase.get('operation')}",
                "",
                f"- Run status: {phase.get('run_status')}",
                transition_line,
            ]
        )
        actions = _as_list(phase.get("new_actions"), "trace phase actions")
        if actions:
            lines.append("- New actions:")
            for action_value in actions:
                action = _as_object(action_value, "trace action")
                label = action.get("tool_name") or action.get("kind") or "action"
                summary = _markdown_excerpt(
                    action.get("output_summary") or action.get("error_message")
                )
                if action.get("tool_name") == "planning.advance":
                    summary = ""
                duration = action.get("duration_ms")
                lines.append(
                    f"  - `{label}` → {action.get('status')}"
                    + (f" ({duration} ms)" if isinstance(duration, int) else "")
                    + (f": {summary}" if summary else "")
                )
        timing_assessments = _as_list(
            phase.get("scene_timing_assessments"),
            "trace phase scene timing assessments",
        )
        if timing_assessments:
            lines.append("- Writer 自然表演时长证据：")
            for value in timing_assessments:
                assessment = _as_object(value, "trace timing assessment")
                lines.append(
                    "  - "
                    f"{assessment.get('stage')} scene {assessment.get('scene_sequence')} "
                    f"(target {assessment.get('target_duration_seconds')}s): "
                    + _markdown_excerpt(assessment.get("assessment"), limit=520)
                )
        notes = [
            note
            for note in _as_list(phase.get("notes"), "trace phase notes")
            if note != INTERNAL_PLAN_REBUILD_NOTE
        ]
        if notes:
            lines.append("- Notes: " + "; ".join(str(note) for note in notes))
        lines.append("")

    _append_semantic_review_history(lines, trace)
    _append_completion_basis(lines, trace)

    validation = _as_object(trace.get("validation"), "trace.validation")
    lines.extend(["## 工程结构验证", ""])
    errors = _as_list(validation.get("errors"), "trace.validation.errors")
    if errors:
        lines.extend(f"- FAIL: {error}" for error in errors)
    elif trace.get("outcome") == "PASSED":
        lines.append(
            "- PASS：AgentRun 已完成，且未生成资产、分镜、任务或 Take。"
        )
        lines.append("- 此 PASS 只表示工程结构闭环；创作质量需由独立盲评判断。")
    else:
        lines.append(
            f"- Pending: probe outcome is `{trace.get('outcome')}`; "
            "final screenplay validation has not passed yet."
        )
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _persist_trace(
    trace: JsonObject,
    *,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> None:
    """Atomically refresh both trace views after every observable Agent step."""

    trace["updated_at"] = _utc_now()
    trace["raw_response_files"] = _json_string_list(recorder.file_snapshot())
    json_path = output_dir / "trace.json"
    temporary_path = output_dir / "trace.json.tmp"
    temporary_path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(json_path)
    _write_trace_markdown(trace, output_dir / "trace.md")


def _phase_index(phases: list[JsonValue]) -> int:
    highest = -1
    for phase_value in phases:
        phase = _as_object(phase_value, "trace phase")
        index = phase.get("phase")
        if isinstance(index, int):
            highest = max(highest, index)
    return highest + 1


def _seen_action_ids(phases: list[JsonValue]) -> set[str]:
    result: set[str] = set()
    for phase_value in phases:
        phase = _as_object(phase_value, "trace phase")
        for action_value in _as_list(phase.get("new_actions"), "trace phase actions"):
            action = _as_object(action_value, "trace action")
            action_id = action.get("id")
            if isinstance(action_id, str):
                result.add(action_id)
    return result


def _seen_timing_assessment_keys(phases: list[JsonValue]) -> set[str]:
    result: set[str] = set()
    for phase_value in phases:
        phase = _as_object(phase_value, "trace phase")
        for value in _as_list(
            phase.get("scene_timing_assessments"),
            "trace phase scene timing assessments",
        ):
            result.add(_timing_assessment_key(_as_object(value, "timing assessment")))
    return result


def _prior_response_notes(phases: list[JsonValue]) -> list[str]:
    result: list[str] = []
    for phase_value in phases:
        phase = _as_object(phase_value, "trace phase")
        result.extend(
            note
            for note in _as_list(phase.get("notes"), "trace phase notes")
            if isinstance(note, str)
        )
    return result


def _print_phase_progress(phase: JsonObject) -> None:
    step_before = phase.get("content_planning_step_before")
    step_after = (
        phase.get("content_planning_step_after")
        or phase.get("content_planning_step")
        or "Agent planning"
    )
    step = (
        f"{step_before} -> {step_after}"
        if isinstance(step_before, str) and step_before
        else str(step_after)
    )
    print(
        f"[{phase.get('recorded_at')}] phase {phase.get('phase')}: "
        f"{step} -> {phase.get('run_status')}",
        flush=True,
    )
    for action_value in _as_list(phase.get("new_actions"), "trace phase actions"):
        action = _as_object(action_value, "trace action")
        label = action.get("tool_name") or action.get("kind") or "action"
        summary = _markdown_excerpt(
            action.get("output_summary") or action.get("error_message"),
            limit=180,
        )
        if action.get("tool_name") == "planning.advance":
            summary = ""
        print(
            f"  action {label}: {action.get('status')}"
            + (f" - {summary}" if summary else ""),
            flush=True,
        )


def _running_actions(run_view: JsonObject) -> list[JsonValue]:
    snapshot = _as_object(run_view.get("snapshot"), "run.snapshot")
    result: list[JsonValue] = []
    for value in _as_list(snapshot.get("actions"), "run.snapshot.actions"):
        action = _as_object(value, "run action")
        if action.get("status") == "RUNNING":
            result.append(_summarize_action(action))
    return result


def _advance_agent_run_with_heartbeats(
    client: AgentApiClient,
    run_id: str,
    *,
    heartbeat_seconds: float,
    trace: JsonObject,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> JsonObject:
    """Advance once while keeping the trace fresh without imposing a deadline."""

    outcomes: Queue[tuple[str, object]] = Queue(maxsize=1)

    def advance() -> None:
        try:
            outcomes.put(("response", client.advance_agent_run(run_id)))
        except Exception as exc:
            outcomes.put(("error", exc))

    Thread(target=advance, name="agent-run-advance", daemon=True).start()
    started = monotonic()
    observation_count = 0
    while True:
        try:
            outcome, value = outcomes.get(timeout=heartbeat_seconds)
        except Empty:
            observation_count += 1
            try:
                observed = client.get_agent_run(run_id)
                _refresh_content_planning_trace(trace, observed)
                step = _content_planning_step(observed) or "Agent planning"
                running_actions = _running_actions(observed)
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observation_count,
                    "run_status": _run_status(observed),
                    "content_planning_step": step,
                    "running_actions": running_actions,
                }
                print(
                    f"[{_utc_now()}] still running after "
                    f"{monotonic() - started:.0f}s: {step}",
                    flush=True,
                )
            except ProbeFailure as exc:
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observation_count,
                    "observation_error": str(exc),
                }
                print(
                    f"[{_utc_now()}] Agent action is still running; "
                    f"progress observation failed: {exc}",
                    flush=True,
                )
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            continue

        trace.pop("in_flight", None)
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if outcome == "response":
            return _as_object(cast(JsonValue, value), "advance response")
        if isinstance(value, Exception):
            raise value
        raise ProbeFailure("Agent advance failed without an exception")


def _load_trace_for_resume(
    output_dir: Path,
    *,
    base_url: str,
    project_id: str | None,
    run_id: str | None,
) -> JsonObject:
    trace_path = output_dir / "trace.json"
    if trace_path.exists():
        if project_id is None or run_id is None:
            raise ProbeFailure(
                "Output directory already contains a trace; resume it with "
                "--project-id and --run-id or choose a new directory"
            )
        try:
            parsed = cast(JsonValue, json.loads(trace_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ProbeFailure("Existing trace.json is not valid JSON") from exc
        trace = _as_object(parsed, "existing trace")
        if trace.get("project_id") != project_id or trace.get("run_id") != run_id:
            raise ProbeFailure(
                "Existing trace belongs to a different project or AgentRun"
            )
        trace["outcome"] = "RUNNING"
        trace["finished_at"] = None
        trace["resumed_at"] = _utc_now()
        trace["validation"] = {"errors": []}
        return trace
    return {
        "probe": "agent-script-only-black-box",
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "finished_at": None,
        "base_url": base_url,
        "project_id": project_id,
        "run_id": run_id,
        "outcome": "RUNNING",
        "phases": [],
        "raw_response_files": [],
        "validation": {"errors": []},
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a public-API-only Agent screenplay validation and preserve a one-action "
            "phase trace."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend origin or full /api/short-drama base URL.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--project-name",
        default="数学倒退一百倍",
        help="Project name used when creating a fresh validation run.",
    )
    parser.add_argument(
        "--input-text",
        default=DEFAULT_INPUT_TEXT,
        help="Creative request passed to the Agent project (defaults to the math premise).",
    )
    parser.add_argument(
        "--target-duration-seconds",
        type=float,
        default=180.0,
        help=(
            "Creative target duration stored on the project. This guides the Agent; "
            "it is not a runtime timeout or generation limit."
        ),
    )
    parser.add_argument(
        "--project-id",
        help="Resume an existing Agent project instead of creating a new one.",
    )
    parser.add_argument(
        "--run-id",
        help="Resume an existing AgentRun; must be supplied with --project-id.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        "--timeout-seconds",
        dest="request_timeout_seconds",
        type=float,
        default=None,
        help=(
            "Optional client-side fault boundary for each individual HTTP request. "
            "By default the probe adds no request deadline; only an explicitly "
            "configured backend/provider boundary still applies. This never acts as a screenplay or AgentRun "
            "deadline. The legacy name --timeout-seconds remains accepted."
        ),
    )
    parser.add_argument(
        "--progress-heartbeat-seconds",
        type=float,
        default=30.0,
        help=(
            "How often to refresh trace files while one Agent action is still running. "
            "This is an observation interval, not a timeout or generation limit."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.target_duration_seconds <= 0:
        print("--target-duration-seconds must be positive", file=sys.stderr)
        return 2
    if (
        args.request_timeout_seconds is not None
        and args.request_timeout_seconds <= 0
    ):
        print("--request-timeout-seconds must be positive", file=sys.stderr)
        return 2
    if args.progress_heartbeat_seconds <= 0:
        print("--progress-heartbeat-seconds must be positive", file=sys.stderr)
        return 2
    if bool(args.project_id) != bool(args.run_id):
        print(
            "--project-id and --run-id must be supplied together",
            file=sys.stderr,
        )
        return 2
    input_text = str(args.input_text).strip()
    if not input_text:
        print("--input-text must not be empty", file=sys.stderr)
        return 2
    project_name = str(args.project_name).strip()
    if not project_name:
        print("--project-name must not be empty", file=sys.stderr)
        return 2
    target_duration_seconds = float(args.target_duration_seconds)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = RawResponseRecorder(output_dir)
    try:
        trace = _load_trace_for_resume(
            output_dir,
            base_url=str(args.base_url),
            project_id=str(args.project_id) if args.project_id else None,
            run_id=str(args.run_id) if args.run_id else None,
        )
    except ProbeFailure as exc:
        print(f"Validation probe could not start: {exc}", file=sys.stderr)
        return 2
    exit_code = 2
    project: JsonObject | None = None
    screenplay: str | None = None
    try:
        client = AgentApiClient(
            str(args.base_url),
            timeout_seconds=(
                float(args.request_timeout_seconds)
                if args.request_timeout_seconds is not None
                else None
            ),
            recorder=recorder,
        )
        if args.project_id and args.run_id:
            project_id = str(args.project_id)
            run_id = str(args.run_id)
            run_view = client.get_agent_run(run_id)
            returned_project_id = _string(
                _run_object(run_view).get("project_id"),
                "resumed run project_id",
            )
            if returned_project_id != project_id:
                raise ProbeFailure(
                    "Resumed AgentRun belongs to a different project: "
                    f"{returned_project_id}"
                )
            initial_operation = "resume_agent_run"
        else:
            payload = _build_agent_project_payload(
                project_name,
                input_text,
                target_duration_seconds,
            )
            created = client.create_agent_project(payload)
            project_id = _string(created.get("project_id"), "created project_id")
            run_view = _as_object(created.get("run"), "created run")
            run_id = _string(_run_object(run_view).get("id"), "created run id")
            initial_operation = "create_agent_project"
        trace["project_id"] = project_id
        trace["run_id"] = run_id
        trace["target_duration_seconds"] = target_duration_seconds
        phases = _as_list(trace.get("phases"), "trace.phases")
        seen_action_ids = _seen_action_ids(phases)
        seen_timing_keys = _seen_timing_assessment_keys(phases)
        response_notes = _prior_response_notes(phases)
        _refresh_content_planning_trace(trace, run_view)
        phase = _trace_phase(
            index=_phase_index(phases),
            operation=initial_operation,
            run_view=run_view,
            seen_action_ids=seen_action_ids,
            seen_timing_assessment_keys=seen_timing_keys,
            content_planning_step_before=(
                "AgentRun resume" if args.project_id else "Project creation"
            ),
        )
        phases.append(phase)
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        _print_phase_progress(phase)

        while True:
            status = _run_status(run_view)
            if status == "COMPLETED":
                break
            if status in {"FAILED", "CANCELLED"}:
                raise ProbeFailure(f"AgentRun reached terminal status {status}")
            if status == "WAITING":
                raise ProbeFailure("AgentRun is waiting for input or intervention")
            timeout_label = (
                f"{float(args.request_timeout_seconds):g}s"
                if args.request_timeout_seconds is not None
                else "backend/provider policy"
            )
            print(
                f"Requesting next Agent action for run {run_id} "
                f"(request boundary: {timeout_label})...",
                flush=True,
            )
            content_planning_step_before = _content_planning_step(run_view)
            advanced = _advance_agent_run_with_heartbeats(
                client,
                run_id,
                heartbeat_seconds=float(args.progress_heartbeat_seconds),
                trace=trace,
                recorder=recorder,
                output_dir=output_dir,
            )
            run_view = _as_object(advanced.get("run"), "advance response run")
            _refresh_content_planning_trace(trace, run_view)
            advance_notes = _as_list(advanced.get("notes"), "advance notes")
            response_notes.extend(
                note for note in advance_notes if isinstance(note, str)
            )
            phase = _trace_phase(
                index=_phase_index(phases),
                operation="advance_agent_run(max_actions=1)",
                run_view=run_view,
                seen_action_ids=seen_action_ids,
                seen_timing_assessment_keys=seen_timing_keys,
                executed_action_ids=_as_list(
                    advanced.get("executed_action_ids"),
                    "executed_action_ids",
                ),
                notes=advance_notes,
                content_planning_step_before=content_planning_step_before,
            )
            phases.append(phase)
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            _print_phase_progress(phase)

        if _run_status(run_view) != "COMPLETED":
            raise ProbeFailure("AgentRun stopped without reaching COMPLETED")

        run_view = client.get_agent_run(run_id)
        _refresh_content_planning_trace(trace, run_view)
        project = client.get_project(project_id)
        (output_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        errors, script_version, screenplay = _validate_script_only_result(
            run_view,
            project,
            response_notes,
        )
        completed_planning_record = _content_planning_record(run_view)
        completed_timing_assessments = (
            _as_list(
                completed_planning_record.get("scene_timing_assessments"),
                "completed planning scene_timing_assessments",
            )
            if completed_planning_record is not None
            else []
        )
        trace["validation"] = {
            "errors": _json_string_list(errors),
            "runtime_observations": _json_string_list(
                sorted(
                    {
                        candidate.strip()
                        for candidate in _warning_candidates(
                            run_view,
                            project,
                            response_notes,
                        )
                        if candidate.strip()
                    }
                )
            ),
            "script_version_id": script_version.get("id"),
            "screenplay_characters": len(screenplay),
            "scene_timing_assessments": completed_timing_assessments,
            "asset_count": len(
                _as_list(
                    _as_object(project.get("document"), "project.document").get("assets"),
                    "assets",
                )
            ),
            "shot_count": len(
                _as_list(
                    _as_object(project.get("document"), "project.document").get("shots"),
                    "shots",
                )
            ),
            "task_count": len(_as_list(project.get("tasks"), "tasks")),
            "take_count": len(_as_list(project.get("takes"), "takes")),
        }
        (output_dir / "screenplay.md").write_text(
            screenplay + "\n",
            encoding="utf-8",
        )
        if errors:
            trace["outcome"] = "FAILED_VALIDATION"
            exit_code = 1
        else:
            trace["outcome"] = "PASSED"
            exit_code = 0
    except ProbeFailure as exc:
        trace["outcome"] = "FAILED_PROBE"
        trace["validation"] = {"errors": [str(exc)]}
        print(f"Validation probe failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        trace["outcome"] = "INTERRUPTED"
        trace["validation"] = {
            "errors": [
                "Probe interrupted; resume with the recorded project_id and run_id"
            ]
        }
        exit_code = 130
        print(
            "Validation probe interrupted; trace and resumable IDs were preserved.",
            file=sys.stderr,
        )
    finally:
        trace["finished_at"] = _utc_now()
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if project is not None and not (output_dir / "project.json").exists():
            (output_dir / "project.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if screenplay is not None and not (output_dir / "screenplay.md").exists():
            (output_dir / "screenplay.md").write_text(
                screenplay + "\n",
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
                "screenplay": (
                    str(output_dir / "screenplay.md") if screenplay is not None else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
