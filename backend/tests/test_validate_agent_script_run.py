from __future__ import annotations

import json
from pathlib import Path

from validate_agent_script_run import (
    INTERNAL_PLAN_REBUILD_NOTE,
    _build_agent_project_payload,
    _content_planning_record,
    _duration_label,
    _new_scene_timing_assessments,
    _refresh_content_planning_trace,
    _validate_script_only_result,
    _write_trace_markdown,
)


def test_build_agent_project_payload_uses_requested_duration() -> None:
    payload = _build_agent_project_payload("验收项目", "创作一个剧本", 180.0)

    project = payload["project"]
    assert project["name"] == "验收项目"
    assert isinstance(project, dict)
    assert project["target_duration_seconds"] == 180.0
    assert project["content_planning_output"] == "SCRIPT_ONLY"
    assert payload["execution_target"] == "MOCK"
    assert payload["render_export"] is False
    assert "3 分钟" in str(payload["objective"])
    assert "5 分钟" not in str(payload["objective"])


def test_runtime_warning_is_observation_not_a_semantic_failure_gate() -> None:
    run_view = {
        "snapshot": {
            "run": {"status": "COMPLETED"},
            "actions": [],
        },
        "context": {
            "blockers": [],
            "constraints": [],
        },
    }
    project = {
        "document": {
            "project": {"current_script_version_id": "script-1"},
            "script_versions": [
                {
                    "id": "script-1",
                    "screenplay_text": "第一场\n主角：我算出来了。",
                }
            ],
            "planning": {
                "warnings": [
                    "Language model unavailable; used local generation fallback."
                ]
            },
            "assets": [],
            "shots": [],
        },
        "tasks": [],
        "takes": [],
    }

    errors, script_version, screenplay = _validate_script_only_result(
        run_view,
        project,
        [],
    )

    assert errors == []
    assert script_version["id"] == "script-1"
    assert screenplay.endswith("我算出来了。")


def test_duration_label_prefers_whole_minutes() -> None:
    assert _duration_label(180.0) == "3 分钟"
    assert _duration_label(185.0) == "185 秒"


def test_trace_reads_writer_timing_evidence_from_public_planning_artifact() -> None:
    record = {
        "id": "planning-1",
        "scene_timing_assessments": [
            {
                "scene_sequence": 1,
                "stage": "INITIAL_WRITE",
                "revision_round": 0,
                "target_duration_seconds": 30.0,
                "assessment": "按成稿顺序自然演读约二十八至三十二秒。",
            }
        ],
    }
    run_view = {
        "context": {
            "artifacts": [
                {
                    "ref": {"artifact_type": "content_planning_job"},
                    "summary": (
                        "Execution cursor only; canonical content remains elsewhere. "
                        + json.dumps(record, ensure_ascii=False)
                    ),
                }
            ]
        }
    }

    assert _content_planning_record(run_view) == record
    seen: set[str] = set()
    first = _new_scene_timing_assessments(run_view, seen)
    second = _new_scene_timing_assessments(run_view, seen)

    assert first == record["scene_timing_assessments"]
    assert second == []


def test_trace_prefers_structured_agent_run_creative_evidence() -> None:
    record = {
        "planning_run_id": "planning-structured",
        "scene_timing_assessments": [],
    }
    run_view = {
        "content_planning_trace": record,
        "context": {
            "artifacts": [
                {
                    "ref": {"artifact_type": "content_planning_job"},
                    "summary": "truncated legacy artifact {",
                }
            ]
        },
    }

    assert _content_planning_record(run_view) == record


def test_human_trace_reports_semantic_work_without_plan_revision_noise(
    tmp_path: Path,
) -> None:
    planning = {
        "planning_run_id": "planning-1",
        "status": "COMPLETE",
        "current_step": "COMPLETE",
        "completed_steps": ["STORY_ORIENTATION", "STORY_ARCHITECT", "COMPLETE"],
        "source_contract": {
            "explicit_commitments": ["约三分钟爽剧"],
            "explicit_prohibitions": ["不生成分镜"],
            "open_creative_choices": ["人物与场景由 Agent 决定"],
            "continuation_preference": "AGENT_DECIDES",
        },
        "selected_narrative_skill_ids": ["narrative.payoff_escalation"],
        "skill_selection_reasons": ["需要连续可见的身份与利益回报。"],
        "narrative_skill_decision": "根据整段需求语义选择，而非关键词匹配。",
        "story_intent": {
            "audience_experience": "快速看懂设定并持续获得爽感。",
            "dramatic_engine": "普通计算能力不断改变主角选择权。",
            "progression_strategy": "从日常优势升级到公开身份翻盘。",
            "tone_and_plausibility": "允许荒诞压缩。",
            "protected_elements": ["主角松弛"],
            "drift_risks": ["不要写成理性程序剧"],
        },
        "outline_json": json.dumps(
            {
                "title": "数学倒退一百倍",
                "story_summary": "主角靠正常算术完成翻盘。",
                "target_duration_seconds": 180,
                "ending_contract": None,
                "scenes": [
                    {
                        "sequence": 1,
                        "heading": "食堂",
                        "summary": "主角先算出菜价。",
                        "narrative_purpose": "用可见行动揭示异常。",
                        "playable_actions": ["报出总价"],
                        "screenplay_text": "林默看一眼价签：八块。全场安静。",
                        "target_duration_seconds": 30,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "scene_timing_assessments": [
            {
                "scene_sequence": 1,
                "stage": "INITIAL_WRITE",
                "revision_round": 0,
                "target_duration_seconds": 30,
                "assessment": "按对白和动作顺序自然演读约三十秒。",
            }
        ],
        "review_report": {
            "summary": "设定清楚，但中段略满。",
            "strengths": ["开场直接"],
            "issues": [
                {
                    "issue_id": "pace-middle",
                    "category": "节奏",
                    "severity": "MAJOR",
                    "owner": "STORY_ARCHITECT",
                    "scene_sequences": [1],
                    "observation": "动作过密。",
                    "recommendation": "合并重复反应。",
                }
            ],
            "performance_timing_assessment": "自然演读偏长。",
            "requires_revision": True,
            "recommended_followup": "REVISE_STORY",
            "followup_rationale": "先压缩中段。",
        },
        "latest_repair_plan": {
            "summary": "保留最强反应并删除重复证明。",
            "source_promise": "兑现爽感。",
            "invariants": ["主角主动"],
            "actions": [
                {
                    "issue_index": 1,
                    "diagnosis": "VALID_STORY_ISSUE",
                    "should_revise": True,
                    "rationale": "重复反应拖慢节奏。",
                    "affected_scene_sequences": [1],
                    "target_duration_change_required": False,
                    "recommended_change": "删除第二次同义震惊。",
                    "remove_or_simplify": [],
                    "preserve": [],
                    "validation_questions": [],
                }
            ],
            "user_overridable": True,
        },
        "latest_revision_verification": {
            "accepted": True,
            "summary": "问题已解决，全稿自然冷读成立。",
            "satisfied_issue_indices": [1],
            "violations": [],
            "recommended_followup": "FINALIZE",
            "followup_rationale": "无需再增加独立冷读。",
            "user_overridable": True,
        },
        "issue_ledger": [],
        "revision_round": 1,
        "revision_scene_sequences": [1],
        "revision_changed_scene_sequences": [1],
        "critic_used": True,
        "final_cold_read_used": False,
        "final_cold_read_report": None,
        "warnings": [],
        "completed_at": "2026-09-03T00:00:00+00:00",
    }
    run_view = {"content_planning_trace": planning}
    trace = {
        "started_at": "start",
        "finished_at": "finish",
        "project_id": "project-1",
        "run_id": "agent-run-1",
        "outcome": "PASSED",
        "phases": [
            {
                "phase": 1,
                "operation": "advance_agent_run(max_actions=1)",
                "run_status": "RUNNING",
                "content_planning_step": "Content planning job STORY_ARCHITECT",
                "content_planning_step_before": (
                    "Content planning job STORY_ORIENTATION"
                ),
                "content_planning_step_after": (
                    "Content planning job STORY_ARCHITECT"
                ),
                "new_actions": [
                    {
                        "tool_name": "planning.advance",
                        "status": "SUCCEEDED",
                        "duration_ms": 36936,
                    }
                ],
                "scene_timing_assessments": [],
                "notes": [
                    INTERNAL_PLAN_REBUILD_NOTE,
                    "保留这条面向用户的阶段说明。",
                ],
            }
        ],
        "validation": {"errors": []},
    }

    _refresh_content_planning_trace(trace, run_view)
    _write_trace_markdown(trace, tmp_path / "trace.md")
    markdown = (tmp_path / "trace.md").read_text(encoding="utf-8")

    assert "## 创作定向" in markdown
    assert "narrative.payoff_escalation" in markdown
    assert "## 初始故事蓝图" in markdown
    assert "## 当前场次产物" in markdown
    assert "## Critic、修订与验证" in markdown
    assert "增强 Verifier" in markdown
    assert "无需再增加独立冷读" in markdown
    assert "工程结构闭环" in markdown
    assert (
        "Content planning transition: Content planning job STORY_ORIENTATION "
        "→ Content planning job STORY_ARCHITECT"
    ) in markdown
    assert "`planning.advance` → SUCCEEDED (36936 ms)" in markdown
    assert INTERNAL_PLAN_REBUILD_NOTE not in markdown
    assert "保留这条面向用户的阶段说明" in markdown
    assert "plan revision" not in markdown.lower()
