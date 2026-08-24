"""Run the Phase 18 H3 continuity-reference comparison on persisted project media."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from short_drama.h3_models import (
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3ProbeRequest,
    H3VideoGuide,
    H3VideoGuideKind,
)
from short_drama.h3_video_guides import append_h3_video_guide_guidance
from short_drama.models import (
    EvidencePurpose,
    Freshness,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
    VideoInspectionRequest,
)
from short_drama.providers.minimax_h3_compiler import build_h3_prompt
from short_drama.service import ShortDramaService
from short_drama.worker import utc_now


_TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.SUBMIT_UNKNOWN,
    TaskStatus.OUTPUT_MISSING,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--app-data-dir",
        type=Path,
        default=Path.home() / "Library/Application Support/ShortDramaStudio",
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--source-sequence", type=int, required=True)
    parser.add_argument("--target-sequence", type=int, required=True)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=[item.value for item in H3VideoGuideKind],
        default=[item.value for item in H3VideoGuideKind],
    )
    parser.add_argument("--submit", action="store_true")
    parser.add_argument(
        "--resume-task",
        action="append",
        default=[],
        metavar="KIND=TASK_ID",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-wait-seconds", type=float, default=1800.0)
    return parser.parse_args()


def _selected_take(takes: list[Take], shot_id: str) -> Take:
    candidates = [
        item
        for item in takes
        if item.shot_id == shot_id
        and item.is_selected
        and item.freshness == Freshness.CURRENT
        and item.availability == TakeAvailability.AVAILABLE
        and item.qa_status != QaStatus.PENDING
        and Path(item.media_path).is_file()
    ]
    if not candidates:
        raise RuntimeError(f"Shot {shot_id} has no selected current Take")
    return max(candidates, key=lambda item: (item.created_at, item.id))


def _inspection_payload(report) -> dict[str, object]:
    return {
        "id": report.id,
        "take_id": report.take_id,
        "previous_take_id": report.previous_take_id,
        "semantic_status": report.semantic_status.value,
        "summary": report.summary,
        "issue_codes": [item.code for item in report.issues],
        "contact_sheet_path": report.contact_sheet_path,
        "boundary_contact_sheet_path": report.boundary_contact_sheet_path,
    }


def _inspect(
    service: ShortDramaService,
    *,
    take: Take,
    source_take: Take,
) -> dict[str, object]:
    existing = next(
        (
            item
            for item in reversed(service.repository.list_video_inspections(take.project_id))
            if item.take_id == take.id
            and item.previous_take_id == source_take.id
            and item.semantic_status.value == "COMPLETE"
        ),
        None,
    )
    if existing is not None:
        return _inspection_payload(existing)
    response = service.inspect_take_video(
        take.id,
        VideoInspectionRequest(
            previous_take_id=source_take.id,
            include_previous_boundary=True,
            semantic_review=True,
            max_frames=18,
            review_focus=(
                "Phase 18 comparison: assess identity and wardrobe continuity, location geometry, "
                "boundary pose and screen direction, camera path, accidental repetition of the "
                "preceding shot, source-error inheritance, composition quality, and natural motion. "
                "Report visible evidence only; the reference route remains advisory."
            ),
        ),
    )
    return _inspection_payload(response.report)


def main() -> None:
    args = _parse_args()
    app_data_dir = args.app_data_dir.expanduser().resolve()
    service = ShortDramaService(
        database_path=app_data_dir / "short-drama.sqlite3",
        workspace_root=app_data_dir / "outputs" / "short-drama-projects",
    )
    aggregate = service.get_project(args.project_id)
    shots_by_sequence = {
        item.sequence: item for item in aggregate.document.shots
    }
    source_shot = shots_by_sequence.get(args.source_sequence)
    target_shot = shots_by_sequence.get(args.target_sequence)
    if source_shot is None or target_shot is None:
        raise RuntimeError("Source or target shot sequence does not exist")
    if target_shot.sequence != source_shot.sequence + 1:
        raise RuntimeError("Phase 18 compares immediately adjacent shots")
    if source_shot.scene_id != target_shot.scene_id:
        raise RuntimeError("Source and target shots must belong to the same scene")

    source_take = _selected_take(aggregate.takes, source_shot.id)
    baseline_take = _selected_take(aggregate.takes, target_shot.id)
    existing_route_plans = service.repository.list_h3_route_plans(args.project_id)
    prepared_media_inputs = service._prepared_h3_media_inputs(
        aggregate.document,
        existing_route_plans,
    )
    guides_by_shot, warnings = service.h3_video_guide_builder.prepare(
        document=aggregate.document,
        takes=[source_take],
        shot_ids=[target_shot.id],
        created_at=utc_now(),
        prepared_media_inputs=prepared_media_inputs,
    )
    available = {item.kind: item for item in guides_by_shot.get(target_shot.id, [])}
    requested = [H3VideoGuideKind(item) for item in args.variants]
    guides = [available[item] for item in requested if item in available]
    missing = [item.value for item in requested if item not in available]
    if missing:
        raise RuntimeError("Could not prepare guide variants: " + ", ".join(missing))

    base_prompt = build_h3_prompt(aggregate.document, target_shot)
    probe_requests: dict[H3VideoGuideKind, H3ProbeRequest] = {
        guide.kind: H3ProbeRequest(
            shot_id=target_shot.id,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=math.ceil(target_shot.duration_seconds),
            prompt_override=append_h3_video_guide_guidance(base_prompt, guide),
            media_inputs=[
                H3MediaInput(
                    kind=H3MediaKind.VIDEO,
                    role=H3MediaRole.REFERENCE_VIDEO,
                    source=H3MediaSource(local_path=guide.media_path),
                    video_guide_id=guide.id,
                )
            ],
            purpose=EvidencePurpose.QUALITY_PROBE,
            learning_objective=(
                "Compare the same target shot against its accepted frame-controlled baseline, "
                "using one visual-only continuity guide. Measure boundary continuity, camera "
                "path, error inheritance, composition quality, repetition, and rework scope."
            ),
            regression_case_id="same-scene-boundary-video-guide-v1",
        )
        for guide in guides
    }
    previews = {
        kind.value: service.preview_h3_probe(args.project_id, request).request.model_dump(
            mode="json"
        )
        for kind, request in probe_requests.items()
    }
    summary: dict[str, object] = {
        "project_id": args.project_id,
        "source_shot_id": source_shot.id,
        "source_take_id": source_take.id,
        "target_shot_id": target_shot.id,
        "baseline_take_id": baseline_take.id,
        "baseline_media_path": baseline_take.media_path,
        "warnings": warnings,
        "guides": [item.model_dump(mode="json") for item in guides],
        "previews": previews,
        "submitted": args.submit,
        "variants": {},
    }
    if not args.submit:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    baseline_inspection = _inspect(
        service,
        take=baseline_take,
        source_take=source_take,
    )
    summary["baseline_inspection"] = baseline_inspection

    task_ids: dict[H3VideoGuideKind, str] = {}
    for raw in args.resume_task:
        kind_text, separator, task_id = raw.partition("=")
        if not separator or not task_id.strip():
            raise RuntimeError("--resume-task must use KIND=TASK_ID")
        kind = H3VideoGuideKind(kind_text)
        if kind not in requested:
            raise RuntimeError(f"Resume task variant was not requested: {kind.value}")
        persisted = service.repository.get_task(task_id.strip())
        if persisted is None or persisted.project_id != args.project_id:
            raise RuntimeError(f"Resume task does not exist in this project: {task_id}")
        task_ids[kind] = persisted.id
    for kind, request in probe_requests.items():
        if kind in task_ids:
            print(json.dumps({"event": "resumed", "variant": kind.value, "task_id": task_ids[kind]}))
            continue
        response = service.enqueue_h3_probe(args.project_id, request)
        task_ids[kind] = response.task.id
        print(json.dumps({"event": "queued", "variant": kind.value, "task_id": response.task.id}))

    deadline = time.monotonic() + args.max_wait_seconds
    last_status: dict[str, str] = {}
    while time.monotonic() < deadline:
        all_terminal = True
        for kind, task_id in task_ids.items():
            task = service.repository.get_task(task_id)
            if task is None:
                raise RuntimeError(f"Task disappeared: {task_id}")
            if task.status not in _TERMINAL:
                task = service.advance_task(task_id)
            status = task.status.value
            if last_status.get(task_id) != status:
                print(
                    json.dumps(
                        {"event": "status", "variant": kind.value, "task_id": task_id, "status": status}
                    )
                )
                last_status[task_id] = status
            if task.status not in _TERMINAL:
                all_terminal = False
        if all_terminal:
            break
        time.sleep(max(0.25, args.poll_seconds))
    else:
        raise RuntimeError("Timed out waiting for Phase 18 H3 probes")

    refreshed_takes = service.repository.list_takes(args.project_id)
    variants: dict[str, object] = {}
    for kind, task_id in task_ids.items():
        task = service.repository.get_task(task_id)
        assert task is not None
        record: dict[str, object] = {
            "task_id": task_id,
            "status": task.status.value,
            "error_code": task.error_code,
            "error_message": task.error_message,
        }
        take = next((item for item in refreshed_takes if item.task_id == task_id), None)
        if take is not None:
            record.update(
                {
                    "take_id": take.id,
                    "media_path": take.media_path,
                    "duration_seconds": take.duration_seconds,
                    "inspection": _inspect(
                        service,
                        take=take,
                        source_take=source_take,
                    ),
                }
            )
        variants[kind.value] = record
    summary["variants"] = variants

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        service.workspace_root
        / args.project_id
        / "experiments"
        / "phase18-video-guides"
        / timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "manifest.json"
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"event": "complete", "manifest": str(output_path.resolve())}))


if __name__ == "__main__":
    main()
