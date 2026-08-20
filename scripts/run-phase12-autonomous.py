#!/usr/bin/env python3
"""Create or resume the Phase 12 real autonomous short-drama run."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from short_drama.models import (  # noqa: E402
    AutonomousDraftCreateRequest,
    AutonomousDraftResponse,
    AutonomousExecutionTarget,
    AutonomousRunAdvanceRequest,
    AutonomousRunStatus,
    RunMode,
    SourceMode,
)
from short_drama.service import ShortDramaService  # noqa: E402


LOCKED_SCRIPT = """
片名：《明天别来了》
类型：真人都市职场轻反转，普通话，9:16，目标 55–60 秒。

角色：
1. 许知夏，25 岁，实习设计师，肩长黑发，深绿色针织上衣、炭灰长裤、白色运动鞋，克制但倔强。
2. 陈屿，36 岁，项目总监，短发，藏蓝衬衫、灰色长裤，平静直接。

场景：
深夜创意公司办公区；电梯厅、直走廊和走廊尽头的玻璃会议室是同一连续空间。走廊冷白光，会议室暖光。避免可读 Logo、招牌和工牌文字。

道具：
员工工牌。开始为白卡蓝挂绳，被取下后由陈屿持有；揭示时会议桌上有深色挂绳的新工牌，不展示可读字。

锁定台词，必须保留说话人、原文和顺序：
1. 陈屿：“工牌给我，明天别来这个工位了。”
2. 许知夏：“方案是我做的，至少让我讲完。”
3. 陈屿：“谁说不让你讲？”
4. 陈屿：“客户点名，让你来带这个项目。”
5. 许知夏：“所以，我不是被辞退？”
6. 陈屿：“是实习期结束了。”

导演与剪辑目标：
- 目标 7 镜、58 秒：S01 5s，S02 15s，S03 7s，S04 6s，S05 10s，S06 8s，S07 7s。
- S01 在电梯厅用索要工牌建立 Hook。
- S02 是一个 15 秒走廊跟拍长镜头：人物沿简单直线路径行走和对话，不叠加复杂手物交互。
- S03 用停步反打回应“谁说不让你讲”。
- S04 进入暖光玻璃会议室，以新工牌和安静的项目成员做无台词揭示。
- S05–S07 完成客户点名、许知夏确认与“实习期结束”反转。
- 全片要求真人写实、克制表演、稳定身份与服装、同一办公区的空间连续、工牌状态连续，不使用依赖可读文字的叙事。
""".strip()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app-data",
        type=Path,
        default=Path.home() / "Library/Application Support/ShortDramaStudio",
    )
    parser.add_argument("--project-id")
    parser.add_argument("--run-id")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--max-actions", type=int, default=40)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def _summary(response: AutonomousDraftResponse) -> dict[str, object]:
    project = response.project
    run = response.run
    export = response.export
    return {
        "project_id": project.document.project.id,
        "project_name": project.document.project.name,
        "run_id": run.id,
        "status": run.status.value,
        "step": run.current_step.value,
        "waiting_reason": run.waiting_reason,
        "error_message": run.error_message,
        "warnings": run.warnings,
        "shots": project.progress.total_shots,
        "selected_takes": project.progress.shots_with_selected_take,
        "tasks": project.progress.total_tasks,
        "terminal_tasks": project.progress.terminal_tasks,
        "failed_tasks": project.progress.failed_tasks,
        "reference_packs": len(project.document.reference_packs),
        "reference_views": sum(
            len(pack.entries) for pack in project.document.reference_packs
        ),
        "manifest_id": project.document.project.current_manifest_id,
        "export_id": export.id if export is not None else None,
        "clean_video_path": export.clean_video_path if export is not None else None,
        "subtitled_video_path": (
            export.subtitled_video_path if export is not None else None
        ),
    }


def main() -> int:
    args = _arguments()
    app_data = args.app_data.expanduser().resolve()
    service = ShortDramaService(
        database_path=app_data / "short-drama.sqlite3",
        workspace_root=app_data / "outputs/short-drama-projects",
    )

    if not args.no_validate:
        providers = {
            "gpt_image": service.validate_image_provider_credentials().model_dump(),
            "gpt_sol": service.validate_language_provider_credentials().model_dump(),
            "minimax_h3": service.validate_h3_credentials().model_dump(),
        }
        print(json.dumps({"providers": providers}, ensure_ascii=False, indent=2))
        if args.validate_only:
            return 0

    if args.validate_only:
        raise SystemExit("--validate-only cannot be combined with --no-validate")

    if bool(args.project_id) != bool(args.run_id):
        raise SystemExit("--project-id and --run-id must be supplied together")

    if args.project_id and args.run_id:
        response = service.advance_autonomous_run(
            args.project_id,
            args.run_id,
            AutonomousRunAdvanceRequest(max_actions=args.max_actions),
        )
    else:
        response = service.create_autonomous_draft(
            AutonomousDraftCreateRequest(
                name="明天别来了 Phase 12",
                source_mode=SourceMode.SCRIPT_LOCKED,
                input_text=LOCKED_SCRIPT,
                run_mode=RunMode.AUTO_DRAFT,
                render_export=True,
                execution_target=AutonomousExecutionTarget.REAL_H3,
            )
        )

    while True:
        summary = _summary(response)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if response.run.status != AutonomousRunStatus.RUNNING:
            return 0 if response.run.status == AutonomousRunStatus.COMPLETE else 2
        time.sleep(max(1.0, args.poll_seconds))
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=args.max_actions),
        )


if __name__ == "__main__":
    raise SystemExit(main())
