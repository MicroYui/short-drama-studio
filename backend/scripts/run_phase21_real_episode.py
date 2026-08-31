"""Run or resume the Phase 21 real three-minute autonomous episode acceptance."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from short_drama.models import (
    AutonomousExecutionTarget,
    AutonomousRunAdvanceRequest,
    AutonomousRunRecord,
    AutonomousRunStatus,
    AutonomousRunStep,
    PlanningRunCreateRequest,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanningRunStep,
    ProgressSummary,
    ProductionProfile,
    RunMode,
    SourceMode,
)
from short_drama.production_planner import refresh_lightweight_reference_plans
from short_drama.service import ShortDramaService


DEFAULT_NAME = "极端热浪反转 Phase 21 最终版"
DEFAULT_PREMISE = (
    "闺蜜骗我说小冰河期马上到来，劝我替她接盘一批卖不掉的极寒生存舱；"
    "我表面答应，却转身租下所有人都嫌弃的废弃避暑山庄，在里面囤满工业制冷机、"
    "备用电池和饮用水。所有人笑我把末日方向搞反了，三天后城市却进入史无前例的极端热浪。"
    "闺蜜卖出的极寒舱因为只有保温层、没有有效制冷而变成闷罐。她带着被骗的买家闯进山庄，"
    "一边抢冷气，一边试图把售假责任推给我。女主必须先救人，再利用闺蜜自己的公开卖货证据、"
    "设备和选择完成反杀，不能为了复仇拒绝无辜者避险。"
    "这是约三分钟的真人现代轻末日爽剧第一集：开场立即展示荒诞而清楚的视觉反差，"
    "至少形成三次由人物行动推动、普通观众能立刻理解的可见反转；不要靠大段合同、设备参数、"
    "新闻解释或巧合推进。人物目标、资源变化和代价都要能拍出来。本集主要冲突应有结果，"
    "结尾再留下一个足以驱动下一集的强悬念。"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app-data-dir",
        type=Path,
        default=Path.home() / "Library/Application Support/ShortDramaStudio",
    )
    parser.add_argument(
        "--stage",
        choices=(
            "script",
            "script-revise",
            "plan",
            "preflight",
            "visuals",
            "produce",
            "all",
            "status",
        ),
        default="status",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Optional state file; relative paths are resolved under app-data-dir.",
    )
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--premise", default=DEFAULT_PREMISE)
    parser.add_argument("--target-seconds", type=int, default=180)
    parser.add_argument("--planning-run-id")
    parser.add_argument("--project-id")
    parser.add_argument("--autonomous-run-id")
    parser.add_argument("--planning-max-steps", type=int, default=80)
    parser.add_argument("--production-max-cycles", type=int, default=2000)
    parser.add_argument("--actions-per-cycle", type=int, default=24)
    parser.add_argument("--poll-seconds", type=float, default=8.0)
    return parser.parse_args()


def _state_path(app_data_dir: Path, configured: Path | None) -> Path:
    if configured is not None:
        return (
            configured.expanduser().resolve()
            if configured.is_absolute()
            else (app_data_dir / configured).resolve()
        )
    return app_data_dir / "phase21-real-episode-state.json"


def _load_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid Phase 21 state file: {path}")
    return cast(dict[str, object], payload)


def _save_state(path: Path, state: dict[str, object]) -> None:
    state["updated_at"] = _utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _print_event(event: str, **payload: object) -> None:
    print(
        json.dumps(
            {"at": _utc_now(), "event": event, **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def _service(app_data_dir: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=app_data_dir / "short-drama.sqlite3",
        workspace_root=app_data_dir / "outputs" / "short-drama-projects",
    )


def _planning_summary(response: PlanningRunResponse) -> dict[str, object]:
    run = response.run
    summary: dict[str, object] = {
        "planning_run_id": run.id,
        "status": run.status.value,
        "step": run.current_step.value,
        "revision_round": run.revision_round,
        "total_scenes": run.total_scenes,
        "next_scene_sequence": run.next_scene_sequence,
        "warnings": run.warnings,
        "error_message": run.error_message,
        "project_id": run.project_id,
        "autonomous_run_id": run.autonomous_run_id,
    }
    if response.project is not None:
        document = response.project.document
        generated_seconds = sum(
            math.ceil(shot.duration_seconds) for shot in document.shots
        )
        summary.update(
            {
                "project_name": document.project.name,
                "target_seconds": document.project.target_duration_seconds,
                "scenes": len(document.script_versions[-1].structured_scenes),
                "shots": len(document.shots),
                "selected_take_seconds": generated_seconds,
                "base_two_take_seconds": generated_seconds * 2,
            }
        )
    return summary


def _resolve_planning_run_id(
    service: ShortDramaService,
    state: dict[str, object],
    args: argparse.Namespace,
) -> str:
    explicit = args.planning_run_id
    saved = state.get("planning_run_id")
    if explicit:
        service.get_planning_run(explicit)
        return explicit
    if isinstance(saved, str) and saved:
        service.get_planning_run(saved)
        return saved
    request = PlanningRunCreateRequest(
        name=args.name,
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
        target_duration_seconds=float(args.target_seconds),
        input_text=args.premise,
        run_mode=RunMode.AUTO_DRAFT,
        execution_target=AutonomousExecutionTarget.REAL_H3,
        render_export=True,
    )
    response = service.create_planning_run(request)
    state.update(
        {
            "phase": 21,
            "name": args.name,
            "premise": args.premise,
            "target_seconds": args.target_seconds,
            "planning_run_id": response.run.id,
            "created_at": _utc_now(),
        }
    )
    _print_event("planning_created", planning_run_id=response.run.id)
    return response.run.id


def _run_planning(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> PlanningRunResponse:
    run_id = _resolve_planning_run_id(service, state, args)
    _save_state(state_path, state)
    response = service.get_planning_run(run_id)
    attention_retry_used = False
    for _ in range(args.planning_max_steps):
        _print_event("planning_status", **_planning_summary(response))
        if response.run.status == PlanningRunStatus.COMPLETE:
            break
        if response.run.status == PlanningRunStatus.FAILED:
            raise RuntimeError(
                response.run.error_message
                or f"Planning stopped with {response.run.status.value}"
            )
        if response.run.status == PlanningRunStatus.NEEDS_ATTENTION:
            if attention_retry_used:
                raise RuntimeError(
                    response.run.error_message
                    or "Planning still needs attention after one recovery retry"
                )
            attention_retry_used = True
        else:
            attention_retry_used = False
        response = service.advance_planning_run(run_id)
    else:
        raise RuntimeError("Planning did not complete within --planning-max-steps")

    if response.project is None:
        raise RuntimeError("Completed planning run has no project")
    run = response.run
    if run.project_id is None or run.autonomous_run_id is None:
        raise RuntimeError("Completed planning run has no autonomous production run")
    state.update(
        {
            "planning_run_id": run.id,
            "project_id": run.project_id,
            "autonomous_run_id": run.autonomous_run_id,
            "planning_summary": _planning_summary(response),
        }
    )
    _save_state(state_path, state)
    _print_event("planning_complete", **_planning_summary(response))
    return response


def _run_script_planning(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> PlanningRunResponse:
    """Advance only through the reviewed episode outline, before shot planning."""

    run_id = _resolve_planning_run_id(service, state, args)
    _save_state(state_path, state)
    response = service.get_planning_run(run_id)
    attention_retry_used = False
    for _ in range(args.planning_max_steps):
        _print_event("planning_status", **_planning_summary(response))
        if response.run.current_step == PlanningRunStep.SCENE_DIRECTOR:
            break
        if response.run.status == PlanningRunStatus.FAILED:
            raise RuntimeError(
                response.run.error_message
                or f"Planning stopped with {response.run.status.value}"
            )
        if response.run.status == PlanningRunStatus.NEEDS_ATTENTION:
            if attention_retry_used:
                raise RuntimeError(
                    response.run.error_message
                    or "Script planning still needs attention after one recovery retry"
                )
            attention_retry_used = True
        else:
            attention_retry_used = False
        if response.run.status == PlanningRunStatus.COMPLETE:
            break
        response = service.advance_planning_run(run_id)
    else:
        raise RuntimeError("Script planning did not complete within --planning-max-steps")

    state.update(
        {
            "planning_run_id": response.run.id,
            "script_ready": True,
            "script_revision_round": response.run.revision_round,
            "script_review": (
                response.run.review_report.model_dump(mode="json")
                if response.run.review_report is not None
                else None
            ),
        }
    )
    _save_state(state_path, state)
    _print_event("script_ready", **_planning_summary(response))
    return response


def _revise_script_from_latest_review(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> PlanningRunResponse:
    """Apply one review-driven language revision and stop before scene direction."""

    run_id = _resolve_planning_run_id(service, state, args)
    response = service.revise_planning_outline_from_review(run_id)
    attention_retry_used = False
    for _ in range(args.planning_max_steps):
        _print_event("planning_status", **_planning_summary(response))
        if response.run.current_step == PlanningRunStep.SCENE_DIRECTOR:
            break
        if response.run.status == PlanningRunStatus.FAILED:
            raise RuntimeError(
                response.run.error_message
                or f"Planning stopped with {response.run.status.value}"
            )
        if response.run.status == PlanningRunStatus.NEEDS_ATTENTION:
            if attention_retry_used:
                raise RuntimeError(
                    response.run.error_message
                    or "Script revision still needs attention after one recovery retry"
                )
            attention_retry_used = True
        else:
            attention_retry_used = False
        if response.run.status == PlanningRunStatus.COMPLETE:
            raise RuntimeError(
                "Review-driven script revision unexpectedly crossed project finalization"
            )
        response = service.advance_planning_run(run_id)
    else:
        raise RuntimeError(
            "Review-driven script revision did not return to SCENE_DIRECTOR"
        )

    state.update(
        {
            "planning_run_id": response.run.id,
            "script_ready": True,
            "script_revision_round": response.run.revision_round,
            "script_review": (
                response.run.review_report.model_dump(mode="json")
                if response.run.review_report is not None
                else None
            ),
        }
    )
    _save_state(state_path, state)
    _print_event("script_revised", **_planning_summary(response))
    return response


def _resolve_production_ids(
    service: ShortDramaService,
    state: dict[str, object],
    args: argparse.Namespace,
) -> tuple[str, str]:
    project_id = args.project_id or state.get("project_id")
    run_id = args.autonomous_run_id or state.get("autonomous_run_id")
    if not isinstance(project_id, str) or not project_id:
        raise RuntimeError("Phase 21 has no project_id; run --stage plan first")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("Phase 21 has no autonomous_run_id; run --stage plan first")
    aggregate = service.get_project(project_id)
    if not any(item.id == run_id for item in aggregate.document.autonomous_runs):
        raise RuntimeError(f"Autonomous run {run_id} does not belong to {project_id}")
    return project_id, run_id


def _production_summary(response) -> dict[str, object]:
    return _production_state_summary(response.run, response.project.progress)


def _production_state_summary(
    run: AutonomousRunRecord,
    progress: ProgressSummary,
) -> dict[str, object]:
    return {
        "project_id": run.project_id,
        "autonomous_run_id": run.id,
        "status": run.status.value,
        "step": run.current_step.value,
        "waiting_reason": run.waiting_reason,
        "error_message": run.error_message,
        "repair_round": run.repair_round,
        "total_shots": progress.total_shots,
        "selected_shots": progress.shots_with_selected_take,
        "total_tasks": progress.total_tasks,
        "terminal_tasks": progress.terminal_tasks,
        "failed_tasks": progress.failed_tasks,
        "manifest_ready": progress.manifest_ready,
        "export_ready": progress.export_ready,
        "export_id": run.export_id,
    }


def _run_production(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> None:
    project_id, run_id = _resolve_production_ids(service, state, args)
    state.update(
        {
            "project_id": project_id,
            "autonomous_run_id": run_id,
        }
    )
    _save_state(state_path, state)
    previous_signature: tuple[object, ...] | None = None
    for _ in range(args.production_max_cycles):
        response = service.advance_autonomous_run(
            project_id,
            run_id,
            AutonomousRunAdvanceRequest(max_actions=args.actions_per_cycle),
        )
        summary = _production_summary(response)
        signature = (
            summary["status"],
            summary["step"],
            summary["selected_shots"],
            summary["terminal_tasks"],
            summary["total_tasks"],
            summary["repair_round"],
            summary["waiting_reason"],
        )
        if signature != previous_signature:
            _print_event("production_status", **summary)
            previous_signature = signature
        state["production_summary"] = summary
        _save_state(state_path, state)
        if response.run.status == AutonomousRunStatus.COMPLETE:
            export = response.export
            if export is None:
                raise RuntimeError("Autonomous run completed without an export")
            state["export"] = export.model_dump(mode="json")
            _save_state(state_path, state)
            _print_event(
                "production_complete",
                **summary,
                clean_video_path=export.clean_video_path,
                subtitled_video_path=export.subtitled_video_path,
            )
            return
        if response.run.status in {
            AutonomousRunStatus.NEEDS_ATTENTION,
            AutonomousRunStatus.FAILED,
        }:
            raise RuntimeError(
                response.run.error_message
                or f"Production stopped with {response.run.status.value}"
            )
        time.sleep(args.poll_seconds)
    raise RuntimeError("Production did not complete within --production-max-cycles")


def _run_visual_preparation(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> None:
    """Prepare image assets and route plans, stopping before any H3 submission."""

    project_id, run_id = _resolve_production_ids(service, state, args)
    allowed_steps = {
        AutonomousRunStep.PREPARE_ASSETS,
        AutonomousRunStep.PLAN_ROUTES,
        AutonomousRunStep.PREPARE_SHOT_ASSETS,
    }
    previous_signature: tuple[object, ...] | None = None
    for _ in range(args.production_max_cycles):
        aggregate = service.get_project(project_id)
        run = next(
            item for item in aggregate.document.autonomous_runs if item.id == run_id
        )
        summary = _production_state_summary(run, aggregate.progress)
        signature = (
            summary["status"],
            summary["step"],
            summary["total_tasks"],
            summary["terminal_tasks"],
            summary["waiting_reason"],
        )
        if signature != previous_signature:
            _print_event("visual_preparation_status", **summary)
            previous_signature = signature
        state["production_summary"] = summary
        _save_state(state_path, state)

        if run.current_step == AutonomousRunStep.GENERATE:
            _print_event(
                "visual_preparation_complete",
                **summary,
                h3_submission_started=aggregate.progress.total_tasks > 0,
            )
            return
        if run.status in {
            AutonomousRunStatus.NEEDS_ATTENTION,
            AutonomousRunStatus.FAILED,
        }:
            raise RuntimeError(
                run.error_message
                or f"Visual preparation stopped with {run.status.value}"
            )
        if run.current_step not in allowed_steps:
            raise RuntimeError(
                "Visual preparation refuses to advance from "
                f"{run.current_step.value}; this stage never submits H3 video tasks"
            )

        response = service.advance_autonomous_run(
            project_id,
            run_id,
            AutonomousRunAdvanceRequest(max_actions=1),
        )
        if response.run.status in {
            AutonomousRunStatus.NEEDS_ATTENTION,
            AutonomousRunStatus.FAILED,
        }:
            raise RuntimeError(
                response.run.error_message
                or f"Visual preparation stopped with {response.run.status.value}"
            )
        if response.run.waiting_reason:
            time.sleep(args.poll_seconds)
    raise RuntimeError(
        "Visual preparation did not reach the pre-H3 boundary within "
        "--production-max-cycles"
    )


def _run_preflight(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
    args: argparse.Namespace,
) -> None:
    project_id, run_id = _resolve_production_ids(service, state, args)
    aggregate = service.get_project(project_id)
    document = aggregate.document.model_copy(deep=True)
    run = next(item for item in document.autonomous_runs if item.id == run_id)
    binding_count_before = sum(
        len(item.bindings) for item in document.reference_plan
    )
    reference_refresh_needed = refresh_lightweight_reference_plans(document)
    binding_count_after = sum(
        len(item.bindings) for item in document.reference_plan
    )
    image_tasks = service.list_image_tasks(project_id).tasks
    planning_run_id = next(
        (
            item.id
            for item in service.list_planning_runs().runs
            if item.project_id == project_id
        ),
        None,
    )
    planning_response = (
        service.get_planning_run(planning_run_id)
        if planning_run_id is not None
        else None
    )
    state.update(
        {
            "name": document.project.name,
            "target_seconds": int(document.project.target_duration_seconds),
            "project_id": project_id,
            "autonomous_run_id": run_id,
            "preflight_at": _utc_now(),
        }
    )
    if planning_response is not None:
        state["planning_run_id"] = planning_run_id
        state["premise"] = planning_response.run.request.input_text
        state["planning_summary"] = _planning_summary(planning_response)
    _save_state(state_path, state)
    payload = {
        "state_path": str(state_path),
        "project_id": project_id,
        "autonomous_run_id": run_id,
        "run_status": run.status.value,
        "run_step": run.current_step.value,
        "project_stage": document.project.stage.value,
        "target_seconds": document.project.target_duration_seconds,
        "shots": len(document.shots),
        "shot_seconds": sum(item.duration_seconds for item in document.shots),
        "base_two_take_seconds": sum(item.duration_seconds for item in document.shots) * 2,
        "assets": len(document.assets),
        "approved_image_assets": sum(
            item.approved_version_id is not None for item in document.assets
        ),
        "linked_asset_project_ids": document.project.linked_asset_project_ids,
        "image_tasks": len(image_tasks),
        "generation_tasks": aggregate.progress.total_tasks,
        "reference_refresh_needed": reference_refresh_needed,
        "reference_bindings_before": binding_count_before,
        "reference_bindings_after": binding_count_after,
        "provider_submission_started": bool(
            image_tasks or aggregate.progress.total_tasks
        ),
    }
    _print_event("production_preflight", **payload)


def _print_status(
    service: ShortDramaService,
    state_path: Path,
    state: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "state_path": str(state_path),
        "state": state,
    }
    planning_run_id = state.get("planning_run_id")
    if isinstance(planning_run_id, str) and planning_run_id:
        payload["planning"] = _planning_summary(
            service.get_planning_run(planning_run_id)
        )
    project_id = state.get("project_id")
    autonomous_run_id = state.get("autonomous_run_id")
    if (
        isinstance(project_id, str)
        and project_id
        and isinstance(autonomous_run_id, str)
        and autonomous_run_id
    ):
        aggregate = service.get_project(project_id)
        run = next(
            item
            for item in aggregate.document.autonomous_runs
            if item.id == autonomous_run_id
        )
        payload["production"] = {
            "project_id": project_id,
            "autonomous_run_id": run.id,
            "status": run.status.value,
            "step": run.current_step.value,
            "waiting_reason": run.waiting_reason,
            "error_message": run.error_message,
            "progress": aggregate.progress.model_dump(mode="json"),
            "export_id": run.export_id,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    args = _parse_args()
    app_data_dir = args.app_data_dir.expanduser().resolve()
    state_path = _state_path(app_data_dir, args.state_file)
    state = _load_state(state_path)
    service = _service(app_data_dir)

    if args.stage == "status":
        _print_status(service, state_path, state)
        return
    if args.stage == "script":
        _run_script_planning(service, state_path, state, args)
    if args.stage == "script-revise":
        _revise_script_from_latest_review(service, state_path, state, args)
    if args.stage in {"plan", "all"}:
        _run_planning(service, state_path, state, args)
    if args.stage == "preflight":
        _run_preflight(service, state_path, state, args)
    if args.stage == "visuals":
        _run_visual_preparation(service, state_path, state, args)
    if args.stage in {"produce", "all"}:
        _run_production(service, state_path, state, args)


if __name__ == "__main__":
    main()
