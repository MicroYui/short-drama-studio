from __future__ import annotations

from pathlib import Path

import pytest

from validate_agent_storyboard_run import (
    ApiHttpError,
    DEFAULT_EXPECTED_SCRIPT_VERSION_ID,
    DEFAULT_OBJECTIVE,
    DEFAULT_PROJECT_ID,
    ProbeFailure,
    _completed_specialist_tool_events,
    _extract_canonical_storyboard,
    _extract_storyboard_draft,
    _is_h3_clip_group_duration,
    _latest_storyboard_draft_ref_from_board,
    _missing_ordered_tool_stages,
    _parse_args,
    _progress_fingerprint,
    _resumable_agent_conflict,
    _seen_specialist_turn_ids,
    _successful_trace_tool_events,
    _trace_tool_names,
    _validate_args,
    _validate_storyboard_only_result,
    _waiting_blocker,
    _work_order_update_key,
    _write_storyboard_markdown,
)


def test_default_objective_locks_confirmed_script_during_restoryboarding() -> None:
    assert "Leader 必须选择 STORYBOARD_PLAN" in DEFAULT_OBJECTIVE
    assert "不得调用 planning.start_script_revision 改写台词或剧情" in (
        DEFAULT_OBJECTIVE
    )
    assert "后续 ledger、Verifier 或 mismatch_resolution" in DEFAULT_OBJECTIVE
    assert "不修改锁定的 ScriptVersion" in DEFAULT_OBJECTIVE


def _run_view() -> dict[str, object]:
    return {
        "snapshot": {
            "run": {
                "id": "agent-run-storyboard",
                "project_id": "project-1",
                "status": "COMPLETED",
            },
            "actions": [],
            "current_plan": None,
        }
    }


def _waiting_run_view(
    *,
    actions: list[dict[str, object]] | None = None,
    with_plan: bool = True,
) -> dict[str, object]:
    return {
        "snapshot": {
            "run": {
                "id": "agent-run-storyboard",
                "project_id": "project-1",
                "status": "WAITING",
            },
            "actions": actions or [],
            "current_plan": (
                {
                    "id": "plan-1",
                    "revision": 1,
                    "goal_complete": False,
                }
                if with_plan
                else None
            ),
        }
    }


def _waiting_board(
    *,
    work_orders: list[dict[str, object]] | None = None,
    step_status: str = "BLOCKED",
    blockers: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> dict[str, object]:
    return {
        "steps": [{"id": "step-1", "status": step_status}],
        "work_orders": work_orders or [],
        "historical_work_orders": [],
        "blockers": blockers or [],
        "open_questions": open_questions or [],
        "next_actions": [],
        "artifact_refs": [],
    }


def _project(*, with_storyboard: bool) -> dict[str, object]:
    production_documents: list[dict[str, object]] = []
    production_spec_document_id: str | None = None
    storyboard_document_id: str | None = None
    if with_storyboard:
        production_spec_document_id = "production-spec-1"
        storyboard_document_id = "storyboard-1"
        production_documents = [
            {
                "id": production_spec_document_id,
                "kind": "production.spec",
            },
            {
                "id": storyboard_document_id,
                "kind": "storyboard",
            },
        ]
    return {
        "document": {
            "project": {
                "id": "project-1",
                "current_script_version_id": "script-4",
                "current_bible_version_id": "bible-2",
                "current_manifest_id": None,
            },
            "script_versions": [
                {
                    "id": "script-4",
                    "lines": [
                        {
                            "id": "line-1",
                            "speaker": "林一",
                            "spoken_text": "十四元。",
                        },
                        {
                            "id": "line-2",
                            "speaker": "秦校长",
                            "spoken_text": "请你留下。",
                        },
                    ],
                    "structured_scenes": [
                        {
                            "id": "scene-1",
                            "heading": "食堂窗口",
                            "summary": "林一算出菜价。",
                            "target_duration_seconds": 8.0,
                            "line_ids": ["line-1"],
                        },
                        {
                            "id": "scene-2",
                            "heading": "校长赶到",
                            "summary": "校长当众邀请。",
                            "target_duration_seconds": 8.0,
                            "line_ids": ["line-2"],
                        },
                    ],
                }
            ],
            "assets": [],
            "shots": [],
            "manifests": [],
            "production_documents": production_documents,
            "production_spec_document_id": production_spec_document_id,
            "storyboard_document_id": storyboard_document_id,
            "generation_plan_document_id": None,
            "audio_structure_document_id": None,
            "master_audio_document_id": None,
            "timed_text_document_id": None,
        },
        "tasks": [],
        "takes": [],
    }


def _storyboard_response() -> dict[str, object]:
    shots = [
        {
            "id": "shot-1",
            "project_id": "project-1",
            "sequence": 1,
            "scene_id": "scene-1",
            "duration_seconds": 8.0,
            "narrative_purpose": "公开验证口算。",
            "visual": "计算器显示 14，林一平静站在窗口。",
            "framing": "中近景",
            "camera": "缓慢推近后转焦计算器。",
            "audio_intent": "食堂底噪骤然降低。",
            "dialogue_line_ids": ["line-1"],
            "timeline_beats": [
                {
                    "start_seconds": 0.0,
                    "end_seconds": 8.0,
                    "visual": "林一报出答案，计算器完成验证。",
                    "framing": "中近景",
                    "camera": "轻推近",
                    "action": "林一报价，阿姨按下等号。",
                    "dialogue_line_ids": ["line-1"],
                    "sound": "按键声。",
                    "references": [],
                }
            ],
            "continuity": {},
            "transition_out": "校长从人群后方入画。",
        },
        {
            "id": "shot-2",
            "project_id": "project-1",
            "sequence": 2,
            "scene_id": "scene-2",
            "duration_seconds": 8.0,
            "narrative_purpose": "兑现地位跃升。",
            "visual": "校长穿过人群向林一开口。",
            "framing": "中景",
            "camera": "跟随校长入画后固定。",
            "audio_intent": "人群倒吸凉气。",
            "dialogue_line_ids": ["line-2"],
            "timeline_beats": [],
            "continuity": {},
            "transition_out": "切黑。",
        },
    ]
    storyboard = {
        "project_id": "project-1",
        "title": "数学倒退一百倍·完整分镜",
        "script_version_id": "script-4",
        "bible_version_id": "bible-2",
        "production_spec_document_id": "production-spec-1",
        "production_spec_version_id": "production-spec-1-v1",
        "script_line_ids": ["line-1", "line-2"],
        "scene_nodes": [
            {
                "id": "scene-node-1",
                "scene_id": "scene-1",
                "sequence": 1,
                "heading": "食堂窗口",
                "summary": "林一算出菜价。",
                "source_line_ids": ["line-1"],
                "clip_group_ids": ["clip-group-1"],
                "shot_ids": ["shot-1"],
            },
            {
                "id": "scene-node-2",
                "scene_id": "scene-2",
                "sequence": 2,
                "heading": "校长赶到",
                "summary": "校长当众邀请。",
                "source_line_ids": ["line-2"],
                "clip_group_ids": ["clip-group-2"],
                "shot_ids": ["shot-2"],
            },
        ],
        "shots": shots,
        "clip_groups": [
            {
                "id": "clip-group-1",
                "title": "十四元验证",
                "shot_ids": ["shot-1"],
                "duration_seconds": 8.0,
                "generation_intent": "完成口算与计算器公开验证。",
                "continuity_in": ["林一在打饭窗口"],
                "visible_changes": ["计算器显示 14"],
                "continuity_out": ["人群安静"],
                "reference_notes": [],
            },
            {
                "id": "clip-group-2",
                "title": "校长赶到",
                "shot_ids": ["shot-2"],
                "duration_seconds": 8.0,
                "generation_intent": "校长穿过人群当众挽留林一。",
                "continuity_in": ["人群安静"],
                "visible_changes": ["校长抵达窗口"],
                "continuity_out": ["林一成为全场焦点"],
                "reference_notes": [],
            },
        ],
        "computed_duration_seconds": 16.0,
        "timing_notes": [],
        "notes": [],
    }
    return {
        "storyboard": storyboard,
        "document": {"id": "storyboard-1"},
        "version": {
            "id": "storyboard-1-v1",
            "version": 1,
            "source_artifact_refs": [
                {
                    "project_id": "project-1",
                    "artifact_type": "production_document_version",
                    "artifact_id": "storyboard-draft-1-v5",
                    "version": 5,
                }
            ],
        },
        "output_slot": {
            "id": "storyboard.current",
            "current_artifact_ref": {
                "artifact_id": "storyboard-1",
                "version": 1,
            },
        },
    }


def _storyboard_draft_response() -> dict[str, object]:
    storyboard = _storyboard_response()["storyboard"]
    assert isinstance(storyboard, dict)
    shots = storyboard["shots"]
    groups = storyboard["clip_groups"]
    assert isinstance(shots, list)
    assert isinstance(groups, list)
    first_shot = shots[0]
    second_shot = shots[1]
    first_group = groups[0]
    second_group = groups[1]
    assert isinstance(first_shot, dict)
    assert isinstance(second_shot, dict)
    assert isinstance(first_group, dict)
    assert isinstance(second_group, dict)

    first_source = {
        "id": "source-range-1",
        "scene_ids": ["scene-1"],
        "script_line_ids": ["line-1"],
        "description": "林一报出十四元并由计算器验证。",
    }
    second_source = {
        "id": "source-range-2",
        "scene_ids": ["scene-2"],
        "script_line_ids": ["line-2"],
        "description": "校长穿过人群当众挽留林一。",
    }
    payload = {
        "phase": "GROUP_AUTHORING",
        "project_id": "project-1",
        "title": "数学倒退一百倍·完整分镜",
        "script_version_id": "script-4",
        "bible_version_id": "bible-2",
        "production_spec_document_id": "production-spec-1",
        "production_spec_version_id": "production-spec-1-v1",
        "base_storyboard_version_id": None,
        "script_line_ids": ["line-1", "line-2"],
        "scene_nodes": [
            {
                "id": "scene-node-1",
                "scene_id": "scene-1",
                "sequence": 1,
                "heading": "食堂窗口",
                "summary": "林一算出菜价。",
                "source_line_ids": ["line-1"],
                "clip_group_ids": [],
                "shot_ids": [],
            },
            {
                "id": "scene-node-2",
                "scene_id": "scene-2",
                "sequence": 2,
                "heading": "校长赶到",
                "summary": "校长当众邀请。",
                "source_line_ids": ["line-2"],
                "clip_group_ids": [],
                "shot_ids": [],
            },
        ],
        "timing_ledger": [
            {
                "id": "timing-1",
                "sequence": 1,
                "scene_node_id": "scene-node-1",
                "source_range": first_source,
                "source_text": "林一：十四元。",
                "dialogue_units": 4,
                "speech_rate_units_per_second": 2.0,
                "minimum_spoken_duration_seconds": 2.0,
                "non_speech_reserve_seconds": 3.0,
                "allocated_duration_seconds": 8.0,
                "completes_scene": True,
                "natural_cut": "计算器显示十四，食堂骤静。",
                "continuity_in": ["林一在打饭窗口"],
                "visible_changes": ["计算器显示 14"],
                "continuity_out": ["人群安静"],
                "completes_scene": True,
                "planned_clip_group_id": "clip-group-1",
                "planned_shot_id": "shot-1",
            },
            {
                "id": "timing-2",
                "sequence": 2,
                "scene_node_id": "scene-node-2",
                "source_range": second_source,
                "source_text": "秦校长：请你留下。",
                "dialogue_units": 5,
                "speech_rate_units_per_second": 2.5,
                "minimum_spoken_duration_seconds": 2.0,
                "non_speech_reserve_seconds": 3.0,
                "allocated_duration_seconds": 8.0,
                "completes_scene": True,
                "natural_cut": "校长完成邀请，林一成为焦点。",
                "continuity_in": ["人群安静"],
                "visible_changes": ["校长抵达窗口"],
                "continuity_out": ["林一成为全场焦点"],
                "completes_scene": True,
                "planned_clip_group_id": "clip-group-2",
                "planned_shot_id": "shot-2",
            },
        ],
        "planned_clip_groups": [
            {
                "id": "clip-group-1",
                "title": "十四元验证",
                "scene_node_id": "scene-node-1",
                "scene_group_sequence": 1,
                "source_range": first_source,
                "timing_entry_ids": ["timing-1"],
                "duration_seconds": 8.0,
                "objective": "完成口算与计算器公开验证。",
                "continuity_in": ["林一在打饭窗口"],
                "visible_changes": ["计算器显示 14"],
                "continuity_out": ["人群安静"],
            },
            {
                "id": "clip-group-2",
                "title": "校长赶到",
                "scene_node_id": "scene-node-2",
                "scene_group_sequence": 1,
                "source_range": second_source,
                "timing_entry_ids": ["timing-2"],
                "duration_seconds": 8.0,
                "objective": "校长穿过人群当众挽留林一。",
                "continuity_in": ["人群安静"],
                "visible_changes": ["校长抵达窗口"],
                "continuity_out": ["林一成为全场焦点"],
            },
        ],
        "timing_audit": {
            "source_declared_duration_s": 16.0,
            "target_duration_s": 16.0,
            "dialogue_units_total": 9,
            "minimum_spoken_duration_s": 4.0,
            "non_speech_reserve_s": 6.0,
            "computed_episode_duration_s": 16.0,
            "clip_group_count": 2,
            "overloaded_shot_ids": [],
            "overloaded_group_ids": [],
            "timing_audit_passed": True,
            "notes": [],
        },
        "committed_groups": [
            {
                "work_unit_id": "work-unit-1",
                "scene_node_id": "scene-node-1",
                "scene_group_sequence": 1,
                "source_range": first_source,
                "shots": [first_shot],
                "clip_group": first_group,
                "handoff_notes": [],
            },
            {
                "work_unit_id": "work-unit-2",
                "scene_node_id": "scene-node-2",
                "scene_group_sequence": 1,
                "source_range": second_source,
                "shots": [second_shot],
                "clip_group": second_group,
                "handoff_notes": [],
            },
        ],
        "verification": {
            "status": "READY",
            "summary": "完整分镜可进入后续资产规划。",
            "checked_clip_group_ids": ["clip-group-1", "clip-group-2"],
            "findings": [],
            "continuity_assessment": ["相邻组末态和初态一致。"],
            "timing_assessment": ["全部组位于 H3 单次生成容量内。"],
        },
        "timing_notes": [],
        "notes": [],
    }
    return {
        "document": {
            "id": "storyboard-draft-1",
            "project_id": "project-1",
            "kind": "storyboard.draft",
            "title": "Storyboard Draft",
            "current_version_id": "storyboard-draft-1-v5",
            "versions": [],
        },
        "version": {
            "id": "storyboard-draft-1-v5",
            "version": 5,
            "parent_version_id": "storyboard-draft-1-v4",
            "payload": payload,
            "source_artifact_refs": [],
            "origin": "AGENT",
            "created_at": "2026-09-05T00:00:00Z",
        },
    }


def _storyboard_trace(
    *,
    extra_tool_names: list[str] | None = None,
    detailed_group_tool: str = "shot.append_storyboard_group",
) -> dict[str, object]:
    names = [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        detailed_group_tool,
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
        *(extra_tool_names or []),
    ]
    return {
        "phases": [
            {
                "new_actions": [
                    {
                        "id": f"action-{index}",
                        "tool_name": name,
                        "status": "SUCCEEDED",
                        "work_order_id": (
                            "work-order-planner"
                            if name
                            in {
                                "shot.begin_storyboard_draft",
                                "shot.append_storyboard_ledger_segment",
                                "shot.complete_storyboard_ledger",
                                "shot.append_storyboard_outline_segment",
                                "shot.complete_storyboard_outline",
                            }
                            else f"work-order-{index}"
                        ),
                    }
                    for index, name in enumerate(names, 1)
                ],
                "work_order_updates": [],
            }
        ]
    }


def _storyboard_draft_ref() -> dict[str, object]:
    return {
        "project_id": "project-1",
        "artifact_type": "storyboard_draft",
        "artifact_id": "storyboard-draft-1",
        "version": 5,
    }


def test_parser_defaults_to_current_accepted_script_v4(tmp_path: Path) -> None:
    args = _parse_args(["--output-dir", str(tmp_path)])

    assert args.project_id == DEFAULT_PROJECT_ID
    assert args.expected_script_version_id == DEFAULT_EXPECTED_SCRIPT_VERSION_ID
    assert args.advance_max_actions == 1
    assert args.request_timeout_seconds is None
    assert "canonical Storyboard" in args.objective
    assert "不编译 GenerationPlan" in args.objective
    assert "在首次封结 ledger 前" in args.objective
    assert "每组分配时长之和精确为 4–15 秒整数" in args.objective
    assert "不得先封存账本" in args.objective
    assert "同一 WorkOrder 内重新 begin 一个草稿分支" in args.objective
    assert "不得仅为重启草稿返回 Leader" in args.objective
    assert "任何旧 storyboard_draft" in args.objective
    assert "不得直接 finalize" in args.objective


def test_argument_validation_keeps_heartbeat_separate_from_deadline(
    tmp_path: Path,
) -> None:
    args = _parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--progress-heartbeat-seconds",
            "0",
        ]
    )

    with pytest.raises(ProbeFailure, match="heartbeat"):
        _validate_args(args)


@pytest.mark.parametrize(
    ("status", "next_owner"),
    [
        ("PENDING", "WORKER_DECISION"),
        ("IN_PROGRESS", "WORKER_DECISION"),
        ("IN_PROGRESS", "WORKER_TOOL"),
        ("NEEDS_ATTENTION", "WORKER_TOOL"),
        ("NEEDS_ATTENTION", "LEADER_REPLAN"),
    ],
)
def test_waiting_handoff_remains_resumable_for_local_agent_team(
    status: str,
    next_owner: str,
) -> None:
    board = _waiting_board(
        work_orders=[
            {
                "id": "work-1",
                "status": status,
                "next_owner": next_owner,
            }
        ]
    )

    assert _waiting_blocker(_waiting_run_view(), board) is None


def test_waiting_ready_leader_step_remains_resumable() -> None:
    assert _waiting_blocker(
        _waiting_run_view(),
        _waiting_board(step_status="READY"),
    ) is None


def test_waiting_user_input_is_a_real_unattended_blocker() -> None:
    run_view = _waiting_run_view(
        actions=[
            {
                "id": "action-input",
                "kind": "USER_INPUT",
                "status": "RUNNING",
                "input_summary": "请确认分镜",
            }
        ]
    )

    assert "requested user input" in str(
        _waiting_blocker(run_view, _waiting_board())
    )


def test_superseded_user_input_does_not_block_the_current_plan() -> None:
    run_view = _waiting_run_view(
        actions=[
            {
                "id": "action-old-input",
                "kind": "USER_INPUT",
                "status": "RUNNING",
                "plan_revision_id": "plan-superseded",
                "input_summary": "读取旧 Verifier 证据",
            }
        ]
    )

    assert _waiting_blocker(
        run_view,
        _waiting_board(
            work_orders=[
                {
                    "id": "work-current",
                    "status": "PENDING",
                    "next_owner": "WORKER_DECISION",
                }
            ],
            open_questions=["读取旧 Verifier 证据"],
        ),
    ) is None


def test_waiting_without_ready_owner_is_a_real_stall() -> None:
    blocker = _waiting_blocker(
        _waiting_run_view(),
        _waiting_board(blockers=["没有可继续的工作"]),
    )

    assert blocker is not None
    assert "没有可继续的工作" in blocker


def test_visible_worker_conflict_can_continue_from_persisted_leader_handoff() -> None:
    error = ApiHttpError(
        method="POST",
        path="/agent-runs/run-1/advance",
        status_code=409,
        body={
            "code": "SHORT_DRAMA_AGENT_CONFLICT",
            "message": "Local Agent Team batch stopped after a visible worker failure",
        },
    )
    board = _waiting_board(
        work_orders=[
            {
                "id": "work-1",
                "status": "NEEDS_ATTENTION",
                "next_owner": "LEADER_REPLAN",
            }
        ]
    )

    note = _resumable_agent_conflict(error, _waiting_run_view(), board)

    assert note is not None
    assert "autonomous recovery" in note
    assert error.status_code == 409
    assert error.body["code"] == "SHORT_DRAMA_AGENT_CONFLICT"


def test_unknown_or_user_owned_conflict_remains_a_probe_failure() -> None:
    unknown = ApiHttpError(
        method="POST",
        path="/agent-runs/run-1/advance",
        status_code=409,
        body={"code": "SOME_OTHER_CONFLICT", "message": "Do not retry"},
    )
    user_owned = ApiHttpError(
        method="POST",
        path="/agent-runs/run-1/advance",
        status_code=409,
        body={"code": "SHORT_DRAMA_AGENT_CONFLICT", "message": "Needs input"},
    )

    assert _resumable_agent_conflict(
        unknown,
        _waiting_run_view(),
        _waiting_board(
            work_orders=[
                {
                    "id": "work-1",
                    "status": "NEEDS_ATTENTION",
                    "next_owner": "LEADER_REPLAN",
                }
            ]
        ),
    ) is None
    assert _resumable_agent_conflict(
        user_owned,
        _waiting_run_view(),
        _waiting_board(
            work_orders=[
                {
                    "id": "work-1",
                    "status": "NEEDS_ATTENTION",
                    "next_owner": "USER_INPUT",
                }
            ]
        ),
    ) is None


def test_progress_fingerprint_changes_when_worker_owner_advances() -> None:
    run_view = _waiting_run_view()
    before = _waiting_board(
        work_orders=[
            {
                "id": "work-1",
                "status": "PENDING",
                "next_owner": "WORKER_DECISION",
            }
        ]
    )
    after = _waiting_board(
        work_orders=[
            {
                "id": "work-1",
                "status": "NEEDS_ATTENTION",
                "next_owner": "WORKER_TOOL",
                "pending_tool_name": "shot.begin_storyboard_draft",
            }
        ]
    )

    assert _progress_fingerprint(run_view, before) != _progress_fingerprint(
        run_view,
        after,
    )


def test_work_order_update_key_tracks_each_persisted_draft_version() -> None:
    base = {
        "id": "work-1",
        "historical": False,
        "status": "NEEDS_ATTENTION",
        "next_owner": "WORKER_DECISION",
        "pending_tool_name": None,
        "result_status": None,
        "result_summary": "已提交一个 ClipGroup",
        "progress_entries": [],
    }
    version_two = {
        **base,
        "artifact_refs": [
            {
                "project_id": "project-1",
                "artifact_type": "storyboard_draft",
                "artifact_id": "draft-1",
                "version": 2,
            }
        ],
    }
    version_three = {
        **base,
        "artifact_refs": [
            {
                "project_id": "project-1",
                "artifact_type": "storyboard_draft",
                "artifact_id": "draft-1",
                "version": 3,
            }
        ],
    }

    assert _work_order_update_key(version_two) != _work_order_update_key(
        version_three
    )


def test_incremental_adapter_reads_only_published_canonical_storyboard() -> None:
    response = _storyboard_response()

    assert _extract_canonical_storyboard(response)["title"] == (
        "数学倒退一百倍·完整分镜"
    )
    with pytest.raises(ProbeFailure, match="canonical storyboard"):
        _extract_canonical_storyboard({"draft_fragments": []})


def test_staged_trace_requires_semantic_order_without_fixed_segment_count() -> None:
    required = (
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    )
    valid = [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.append_storyboard_ledger_segment",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    ]
    out_of_order = [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_outline_segment",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    ]

    assert _missing_ordered_tool_stages(valid, required) == []
    assert _missing_ordered_tool_stages(out_of_order, required) == [
        "shot.append_storyboard_outline_segment"
    ]


def test_completed_specialist_turns_are_structured_deduplicated_events() -> None:
    work_order_view = {
        "work_order": {
            "id": "work-order-planner",
            "specialist_turns": [
                {
                    "id": "turn-2",
                    "sequence": 2,
                    "tool_name": "shot.append_storyboard_ledger_segment",
                    "status": "COMPLETED",
                    "result": {
                        "tool_id": "shot.append_storyboard_ledger_segment",
                        "status": "SUCCEEDED",
                        "artifact_refs": [
                            {
                                "project_id": "project-1",
                                "artifact_type": "storyboard_draft",
                                "artifact_id": "draft-1",
                                "version": 2,
                            }
                        ],
                    },
                },
                {
                    "id": "turn-1",
                    "sequence": 1,
                    "tool_name": "shot.begin_storyboard_draft",
                    "status": "COMPLETED",
                    "result": {
                        "tool_id": "shot.begin_storyboard_draft",
                        "status": "FAILED",
                        "artifact_refs": [],
                    },
                },
                {
                    "id": "turn-3",
                    "sequence": 3,
                    "tool_name": "shot.complete_storyboard_ledger",
                    "status": "REQUESTED",
                    "result": None,
                },
            ],
        }
    }
    seen: set[str] = set()

    events = _completed_specialist_tool_events(work_order_view, seen)

    assert [event["turn_id"] for event in events if isinstance(event, dict)] == [
        "turn-1",
        "turn-2",
    ]
    assert [event["tool_status"] for event in events if isinstance(event, dict)] == [
        "FAILED",
        "SUCCEEDED",
    ]
    assert seen == {"turn-1", "turn-2"}
    assert _completed_specialist_tool_events(work_order_view, seen) == []


def test_group_segment_trace_exposes_committed_groups_and_checkpoints() -> None:
    work_order_view = {
        "work_order": {
            "id": "work-order-director",
            "specialist_turns": [
                {
                    "id": "turn-groups",
                    "sequence": 1,
                    "tool_name": "shot.append_storyboard_groups",
                    "status": "COMPLETED",
                    "result": {
                        "tool_id": "shot.append_storyboard_groups",
                        "status": "SUCCEEDED",
                        "artifact_refs": [],
                        "output": {
                            "checkpoints": [
                                {
                                    "clip_group_id": "clip-1",
                                    "draft_version_id": "draft-v6",
                                },
                                {
                                    "clip_group_id": "clip-2",
                                    "draft_version_id": "draft-v7",
                                },
                            ]
                        },
                    },
                }
            ],
        }
    }

    events = _completed_specialist_tool_events(work_order_view, set())

    assert events[0]["committed_group_ids"] == ["clip-1", "clip-2"]
    assert events[0]["checkpoint_version_ids"] == ["draft-v6", "draft-v7"]


def test_structured_specialist_events_merge_with_leader_tool_trace() -> None:
    planning_names = [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
    ]
    post_names = [
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
    ]
    specialist_events = [
        {
            "turn_id": f"turn-{index}",
            "work_order_id": (
                "work-order-planner"
                if name in planning_names
                else f"work-order-worker-{index}"
            ),
            "sequence": index,
            "tool_name": name,
            "turn_status": "COMPLETED",
            "tool_status": "SUCCEEDED",
            "artifact_refs": [],
        }
        for index, name in enumerate([*planning_names, *post_names], 1)
    ]
    trace = {
        "phases": [
            {
                "new_actions": [
                    {
                        "id": "action-finalize",
                        "tool_name": "shot.finalize_storyboard",
                        "status": "SUCCEEDED",
                        "work_order_id": None,
                    }
                ],
                "specialist_tool_events": specialist_events,
                "work_order_updates": [],
            }
        ]
    }

    successful = _successful_trace_tool_events(trace)

    assert [name for name, _owner in successful] == [
        "shot.finalize_storyboard",
        *planning_names,
        *post_names,
    ]
    assert set(_trace_tool_names(trace)) == {
        "shot.finalize_storyboard",
        *planning_names,
        *post_names,
    }


def test_backfilled_specialist_events_sort_before_later_leader_finalize() -> None:
    ordered_names = [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.record_storyboard_draft_verification",
    ]
    trace = {
        "phases": [
            {
                "recorded_at": "2026-09-05T12:10:00Z",
                "new_actions": [
                    {
                        "id": "action-finalize",
                        "tool_name": "shot.finalize_storyboard",
                        "status": "SUCCEEDED",
                        "work_order_id": None,
                    }
                ],
                "specialist_tool_events": [],
                "work_order_updates": [],
            },
            {
                # A resumed probe discovers historical Worker turns only after
                # the run has completed. Their persisted completion timestamps,
                # not the reconciliation phase timestamp, determine chronology.
                "recorded_at": "2026-09-05T12:20:00Z",
                "new_actions": [],
                "specialist_tool_events": [
                    {
                        "turn_id": f"turn-{index}",
                        "work_order_id": (
                            "work-order-planner" if index <= 5 else f"worker-{index}"
                        ),
                        "sequence": index,
                        "tool_name": name,
                        "turn_status": "COMPLETED",
                        "tool_status": "SUCCEEDED",
                        "artifact_refs": [],
                        "completed_at": f"2026-09-05T12:0{index}:00Z",
                    }
                    for index, name in enumerate(ordered_names, 1)
                ],
                "work_order_updates": [],
            },
        ]
    }

    successful = _successful_trace_tool_events(trace)
    successful_names = [name for name, _owner in successful]

    assert successful_names == [*ordered_names, "shot.finalize_storyboard"]
    assert _missing_ordered_tool_stages(
        successful_names,
        (*ordered_names, "shot.finalize_storyboard"),
    ) == []


def test_resume_seen_turn_ids_only_backfills_missing_completed_turns() -> None:
    trace = {
        "phases": [
            {
                "specialist_tool_events": [
                    {
                        "turn_id": "turn-1",
                        "work_order_id": "work-order-planner",
                        "sequence": 1,
                        "tool_name": "shot.begin_storyboard_draft",
                        "turn_status": "COMPLETED",
                        "tool_status": "SUCCEEDED",
                        "artifact_refs": [],
                    }
                ]
            }
        ]
    }
    work_order_view = {
        "work_order": {
            "id": "work-order-planner",
            "specialist_turns": [
                {
                    "id": "turn-1",
                    "sequence": 1,
                    "tool_name": "shot.begin_storyboard_draft",
                    "status": "COMPLETED",
                    "result": {"status": "SUCCEEDED", "artifact_refs": []},
                },
                {
                    "id": "turn-2",
                    "sequence": 2,
                    "tool_name": "shot.append_storyboard_ledger_segment",
                    "status": "COMPLETED",
                    "result": {"status": "SUCCEEDED", "artifact_refs": []},
                },
            ],
        }
    }
    seen = _seen_specialist_turn_ids(trace)

    events = _completed_specialist_tool_events(work_order_view, seen)

    assert [event["turn_id"] for event in events if isinstance(event, dict)] == [
        "turn-2"
    ]
    assert seen == {"turn-1", "turn-2"}


def test_h3_clip_group_duration_is_integer_four_to_fifteen_seconds() -> None:
    assert _is_h3_clip_group_duration(4.0) is True
    assert _is_h3_clip_group_duration(15.0) is True
    assert _is_h3_clip_group_duration(3.0) is False
    assert _is_h3_clip_group_duration(4.5) is False


def test_final_board_resolves_exact_canonical_storyboard_draft_branch() -> None:
    board = _waiting_board(
        work_orders=[
            {
                "id": "abandoned-work",
                "artifact_refs": [
                    {
                        "project_id": "project-1",
                        "artifact_type": "storyboard_draft",
                        "artifact_id": "abandoned-storyboard-draft",
                        "version": 12,
                    }
                ],
            },
            {
                "id": "final-work",
                "artifact_refs": [
                    {
                        "project_id": "project-1",
                        "artifact_type": "storyboard_draft",
                        "artifact_id": "storyboard-draft-1",
                        "version": 5,
                    }
                ],
            },
        ]
    )
    project = _project(with_storyboard=True)
    project_document = project["document"]
    assert isinstance(project_document, dict)
    production_documents = project_document["production_documents"]
    assert isinstance(production_documents, list)
    production_documents.extend(
        [
            {
                "id": "abandoned-storyboard-draft",
                "kind": "storyboard.draft",
                "versions": [
                    {
                        "id": "abandoned-storyboard-draft-v12",
                        "version": 12,
                    }
                ],
            },
            {
                "id": "storyboard-draft-1",
                "kind": "storyboard.draft",
                "versions": [
                    {
                        "id": "storyboard-draft-1-v5",
                        "version": 5,
                    }
                ],
            },
        ]
    )

    resolved = _latest_storyboard_draft_ref_from_board(
        board,
        storyboard_response=_storyboard_response(),
        project=project,
    )

    assert resolved == {
        "project_id": "project-1",
        "artifact_type": "storyboard_draft",
        "artifact_id": "storyboard-draft-1",
        "version": 5,
        "version_id": "storyboard-draft-1-v5",
    }
    assert _extract_storyboard_draft(_storyboard_draft_response())["verification"] == {
        "status": "READY",
        "summary": "完整分镜可进入后续资产规划。",
        "checked_clip_group_ids": ["clip-group-1", "clip-group-2"],
        "findings": [],
        "continuity_assessment": ["相邻组末态和初态一致。"],
        "timing_assessment": ["全部组位于 H3 单次生成容量内。"],
    }


def test_final_board_refuses_ambiguous_restarted_draft_fallback() -> None:
    storyboard_response = _storyboard_response()
    project = _project(with_storyboard=True)
    board = _waiting_board(
        work_orders=[
            {
                "id": "work-1",
                "artifact_refs": [
                    {
                        "project_id": "project-1",
                        "artifact_type": "storyboard_draft",
                        "artifact_id": "storyboard-draft-a",
                        "version": 5,
                    },
                    {
                        "project_id": "project-1",
                        "artifact_type": "storyboard_draft",
                        "artifact_id": "storyboard-draft-b",
                        "version": 5,
                    },
                ],
            }
        ]
    )

    with pytest.raises(ProbeFailure, match="multiple restarted draft branches"):
        _latest_storyboard_draft_ref_from_board(
            board,
            storyboard_response=storyboard_response,
            project=project,
        )


def test_storyboard_only_validation_accepts_scene_group_shot_hierarchy() -> None:
    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert errors == []


def test_storyboard_only_validation_accepts_group_segment_tool() -> None:
    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(
            detailed_group_tool="shot.append_storyboard_groups"
        ),
        expected_script_version_id="script-4",
    )

    assert errors == []


def test_storyboard_only_validation_observes_draft_audit_and_verification() -> None:
    draft_response = _storyboard_draft_response()
    version = draft_response["version"]
    assert isinstance(version, dict)
    payload = version["payload"]
    assert isinstance(payload, dict)
    timing_audit = payload["timing_audit"]
    verification = payload["verification"]
    assert isinstance(timing_audit, dict)
    assert isinstance(verification, dict)
    timing_audit["timing_audit_passed"] = False
    verification["status"] = "NEEDS_REVISION"
    verification["findings"] = [
        {
            "id": "finding-1",
            "summary": "连续性仍需修订。",
            "affected_clip_group_ids": ["clip-group-2"],
        }
    ]
    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=draft_response,
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert "Storyboard timing audit did not pass" in errors
    assert "Latest storyboard draft verification is not READY" in errors


def test_storyboard_continuity_uses_semantic_verification_not_string_equality() -> None:
    response = _storyboard_response()
    storyboard = response["storyboard"]
    assert isinstance(storyboard, dict)
    canonical_groups = storyboard["clip_groups"]
    assert isinstance(canonical_groups, list)
    second_canonical = canonical_groups[1]
    assert isinstance(second_canonical, dict)

    draft_response = _storyboard_draft_response()
    version = draft_response["version"]
    assert isinstance(version, dict)
    draft = version["payload"]
    assert isinstance(draft, dict)
    planned_groups = draft["planned_clip_groups"]
    committed_groups = draft["committed_groups"]
    timing_ledger = draft["timing_ledger"]
    assert isinstance(planned_groups, list)
    assert isinstance(committed_groups, list)
    assert isinstance(timing_ledger, list)
    second_planned = planned_groups[1]
    second_committed = committed_groups[1]
    second_timing = timing_ledger[1]
    assert isinstance(second_planned, dict)
    assert isinstance(second_committed, dict)
    assert isinstance(second_timing, dict)
    committed_group = second_committed["clip_group"]
    assert isinstance(committed_group, dict)

    entering_state = ["食堂忽然安静，校长从门口穿过人群走向窗口"]
    second_timing["continuity_in"] = entering_state
    second_planned["continuity_in"] = entering_state
    committed_group["continuity_in"] = entering_state
    second_canonical["continuity_in"] = entering_state

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=response,
        storyboard_draft_response=draft_response,
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert errors == []


def test_storyboard_validation_requires_scene_completion_marker() -> None:
    draft_response = _storyboard_draft_response()
    version = draft_response["version"]
    assert isinstance(version, dict)
    payload = version["payload"]
    assert isinstance(payload, dict)
    timing_ledger = payload["timing_ledger"]
    assert isinstance(timing_ledger, list)
    first_entry = timing_ledger[0]
    assert isinstance(first_entry, dict)
    first_entry["completes_scene"] = False

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=draft_response,
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert any("completes_scene=true" in error for error in errors)


def test_storyboard_validation_requires_scene_local_integer_ledger_partition() -> None:
    draft_response = _storyboard_draft_response()
    version = draft_response["version"]
    assert isinstance(version, dict)
    payload = version["payload"]
    assert isinstance(payload, dict)
    planned_groups = payload["planned_clip_groups"]
    assert isinstance(planned_groups, list)
    first_group = planned_groups[0]
    second_group = planned_groups[1]
    assert isinstance(first_group, dict)
    assert isinstance(second_group, dict)
    first_group["timing_entry_ids"] = ["timing-1", "timing-2"]
    first_group["duration_seconds"] = 16
    second_group["timing_entry_ids"] = []
    second_group["duration_seconds"] = 0

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=draft_response,
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert any(
        "Planned ClipGroup clip-group-1 crosses or loses its timing ledger Scene"
        in error
        for error in errors
    )
    assert any("outside H3 integer 4–15s" in error for error in errors)


def test_storyboard_validation_requires_one_planner_work_order() -> None:
    trace = _storyboard_trace()
    phases = trace["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    actions = phase["new_actions"]
    assert isinstance(actions, list)
    ledger_action = next(
        action
        for action in actions
        if isinstance(action, dict)
        and action.get("tool_name") == "shot.complete_storyboard_ledger"
    )
    assert isinstance(ledger_action, dict)
    ledger_action["work_order_id"] = "work-order-second-planner"

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=trace,
        expected_script_version_id="script-4",
    )

    assert any("multiple Planner WorkOrders" in error for error in errors)


def test_storyboard_validation_uses_final_successful_planning_branch() -> None:
    trace = _storyboard_trace()
    phases = trace["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    actions = phase["new_actions"]
    assert isinstance(actions, list)
    actions[:0] = [
        {
            "id": "abandoned-begin",
            "tool_name": "shot.begin_storyboard_draft",
            "work_order_id": "work-order-abandoned",
            "status": "SUCCEEDED",
        },
        {
            "id": "abandoned-ledger",
            "tool_name": "shot.append_storyboard_ledger_segment",
            "work_order_id": "work-order-abandoned",
            "status": "FAILED",
        },
    ]

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=trace,
        expected_script_version_id="script-4",
    )

    assert errors == []


def test_storyboard_validation_allows_same_planner_to_restart_draft_branch() -> None:
    trace = _storyboard_trace()
    phases = trace["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    actions = phase["new_actions"]
    assert isinstance(actions, list)
    actions[:0] = [
        {
            "id": "superseded-begin",
            "tool_name": "shot.begin_storyboard_draft",
            "work_order_id": "work-order-planner",
            "status": "SUCCEEDED",
        },
        {
            "id": "superseded-ledger",
            "tool_name": "shot.append_storyboard_ledger_segment",
            "work_order_id": "work-order-planner",
            "status": "SUCCEEDED",
        },
    ]

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=trace,
        expected_script_version_id="script-4",
    )

    assert errors == []


def test_storyboard_only_validation_rejects_media_or_legacy_trace_tools() -> None:
    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=_storyboard_response(),
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(
            extra_tool_names=["shot.create_storyboard", "video.enqueue"]
        ),
        expected_script_version_id="script-4",
    )

    assert any("legacy canonical/media Tools" in error for error in errors)


def test_storyboard_only_validation_rejects_cross_scene_clip_group() -> None:
    response = _storyboard_response()
    storyboard = response["storyboard"]
    assert isinstance(storyboard, dict)
    groups = storyboard["clip_groups"]
    assert isinstance(groups, list)
    first = groups[0]
    assert isinstance(first, dict)
    first["shot_ids"] = ["shot-1", "shot-2"]
    groups.pop()

    errors = _validate_storyboard_only_result(
        run_view=_run_view(),
        before=_project(with_storyboard=False),
        after=_project(with_storyboard=True),
        storyboard_response=response,
        storyboard_draft_response=_storyboard_draft_response(),
        storyboard_draft_ref=_storyboard_draft_ref(),
        trace=_storyboard_trace(),
        expected_script_version_id="script-4",
    )

    assert any("crosses or loses its script Scene" in error for error in errors)


def test_storyboard_markdown_renders_canvas_compatible_hierarchy(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "storyboard.md"

    _write_storyboard_markdown(
        _storyboard_response(),
        _project(with_storyboard=True),
        output_path,
    )

    markdown = output_path.read_text(encoding="utf-8")
    assert "## MiniMax Design / ComfyUI 投影" in markdown
    assert "完整 Storyboard 对应一个「分镜列表」语义文档节点" in markdown
    assert "## 场次 1：食堂窗口" in markdown
    assert "### ClipGroup 1.1：十四元验证" in markdown
    assert "#### Shot 1：`shot-1`" in markdown
    assert "林一：十四元。" in markdown
    assert "[0.0-8.0s]" in markdown
