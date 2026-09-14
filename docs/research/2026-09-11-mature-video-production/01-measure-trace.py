#!/usr/bin/env python3
"""Read one persisted Studio case; emit aggregates, never full prompts or payloads.

The database is opened as a read-only SQLite URI. No project mutation or provider
call is possible in this script. Model classification reads only the small
*-model.output.json files in the two exact job directories, not model inputs,
the exported ZIP, app bundles, or the entire output tree.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics


def seconds(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return round(sum(end - start for start, end in merged), 3)


def most_common(counter: Counter, limit: int = 20) -> list[dict]:
    return [{"name": key, "count": count} for key, count in counter.most_common(limit)]


def language_metrics(values: list[dict]) -> dict:
    fields = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
    return {
        "calls": len(values),
        "success": sum(value.get("success") is True for value in values),
        "reported_usage": {field: sum(value[field] for value in values if isinstance(value.get(field), int)) for field in fields},
        "usage_field_calls": {field: sum(isinstance(value.get(field), int) for value in values) for field in fields},
        "elapsed_sum_seconds": round(sum(value.get("elapsed_seconds", 0) for value in values), 3),
        "elapsed_median_seconds": round(statistics.median(value.get("elapsed_seconds", 0) for value in values), 3) if values else None,
        "input_median_tokens": statistics.median(value["input_tokens"] for value in values if isinstance(value.get("input_tokens"), int)) if values else None,
        "input_max_tokens": max((value.get("input_tokens", 0) for value in values), default=0),
    }


def measure(runtime: Path, project_id: str) -> dict:
    db = runtime / "short-drama.sqlite3"
    connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    kinds = ("model.started", "provider.language_generation", "tool.started", "tool.finished", "job.updated", "chat.user")
    query = "SELECT id, kind, created_at, data_json FROM studio_events WHERE project_id = ? AND kind IN (" + ",".join("?" for _ in kinds) + ") ORDER BY id"
    events = [(event_id, kind, created, json.loads(raw)) for event_id, kind, created, raw in connection.execute(query, (project_id, *kinds))]
    starts = [(event_id, created, data) for event_id, kind, created, data in events if kind == "model.started"]
    completions = [(event_id, created, data) for event_id, kind, created, data in events if kind == "provider.language_generation"]
    start_by_id = {data["call_id"]: (event_id, created, data) for event_id, created, data in starts}
    completion_by_id = {data["call_id"]: (event_id, created, data) for event_id, created, data in completions}
    jobs = [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM studio_jobs WHERE project_id = ? ORDER BY updated_at", (project_id,))]
    result: dict = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "database": str(db),
        "method": {
            "database_mode": "SQLite mode=ro and PRAGMA query_only=ON",
            "model_unit": "unique call_id; provider.language_generation is a completion record including unsuccessful completed requests",
            "source_files": "Only exact job directories and *-model.output.json are read for response classification; no model inputs or app bundles scanned.",
            "time": "Language elapsed sums overlap under concurrency. Interval unions use matching model.started and provider.language_generation timestamps. Job states integrate consecutive job.updated events.",
            "usage": "Cached input is included in input, reasoning is included in output. No price inferred. A started request without completion has unknown usage.",
        },
        "model_identity_check": {
            "started_records": len(starts), "unique_started_ids": len(start_by_id),
            "completion_records": len(completions), "unique_completion_ids": len(completion_by_id),
            "completion_ids_without_start": sorted(set(completion_by_id) - set(start_by_id)),
            "started_without_completion": [{"call_id": call_id, "event_id": item[0], "created_at": item[1], "agent_role": item[2].get("agent_role")} for call_id, item in start_by_id.items() if call_id not in completion_by_id],
        },
        "language": language_metrics([data for _, _, data in completions]),
        "configurations": most_common(Counter(data.get("model", "") + "/" + str(data.get("reasoning_effort")) for _, _, data in completions)),
    }
    result["language"]["failures"] = [{"event_id": event_id, "call_id": data["call_id"], "created_at": created, "agent_role": data.get("agent_role"), "error_kind": data.get("error_kind"), "completion_type": data.get("completion_type")} for event_id, created, data in completions if data.get("success") is not True]
    job_stats = []
    for job in jobs:
        values = [data for _, _, data in completions if data.get("job_id") == job["id"]]
        states = [(seconds(created), data["status"]) for _, kind, created, data in events if kind == "job.updated" and data.get("job_id") == job["id"]]
        duration = Counter()
        for (start, state), (end, _) in zip(states, states[1:]):
            duration[state] += end - start
        intervals = [(seconds(start_by_id[data["call_id"]][1]), seconds(created)) for _, created, data in completions if data.get("job_id") == job["id"] and data["call_id"] in start_by_id]
        job_stats.append({"job_id": job["id"], "run_id": job.get("run_id"), "status": job["status"], "created_at": job.get("created_at"), "updated_at": job.get("updated_at"), "language": language_metrics(values), "language_interval_union_seconds": union_seconds(intervals), "state_seconds": {key: round(value, 3) for key, value in duration.items()}, "state_event_counts": dict(Counter(state for _, state in states))})
    result["jobs"] = job_stats
    by_role = defaultdict(list)
    for _, _, data in completions:
        by_role[data.get("agent_role", "unknown")].append(data)
    result["roles"] = sorted([{"role": role, **language_metrics(values)} for role, values in by_role.items()], key=lambda item: -item["reported_usage"]["input_tokens"])
    finished_tools = [data for _, kind, _, data in events if kind == "tool.finished"]
    result["tools"] = {
        "started_records": sum(kind == "tool.started" for _, kind, _, _ in events),
        "finished_records": len(finished_tools),
        "unique_finished_tool_call_ids": len({value.get("tool_call_id") for value in finished_tools}),
        "finished_statuses": dict(Counter(data["status"] for data in finished_tools)),
        "calls_by_tool": most_common(Counter(data["tool_name"] for data in finished_tools), 200),
        "failures_by_tool": most_common(Counter(data["tool_name"] for data in finished_tools if data["status"] == "FAILED"), 200),
        "top_tools_by_role": {role: most_common(Counter(data["tool_name"] for data in finished_tools if data.get("agent_role") == role), 10) for role in ["opening_video_compiler", "director_media_binding", "EDITORIAL_FINISH", "ENDING_VIDEO", "asset_planning_recovery"]},
    }
    result["chat_user_records"] = sum(kind == "chat.user" for _, kind, _, _ in events)
    work_orders = [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM agent_work_orders WHERE parent_run_id IN (SELECT id FROM agent_runs WHERE project_id = ?)", (project_id,))]
    result["work_orders"] = {"count": len(work_orders), "statuses": dict(Counter(work["status"] for work in work_orders))}
    video = [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM generation_tasks WHERE project_id = ?", (project_id,))]
    intervals = [(seconds(task["submitted_at"]), seconds(task["finished_at"])) for task in video if task["status"] == "SUCCEEDED" and task.get("submitted_at") and task.get("finished_at")]
    result["video_tasks"] = {"count": len(video), "statuses": dict(Counter(task["status"] for task in video)), "successful_observed_turnaround_seconds": [round(end - start, 3) for start, end in intervals], "successful_interval_union_seconds": union_seconds(intervals), "note": "Submitted-to-finished includes polling and local collection; unfinished extra candidate excluded, not treated as zero time."}
    image_tasks = [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM image_tasks WHERE project_id = ?", (project_id,))]
    result["image_tasks"] = {"count": len(image_tasks), "statuses": dict(Counter(task["status"] for task in image_tasks))}
    connection.close()

    # These direct job directories are the only filesystem enumeration below.
    outputs = []
    output_type_counts = Counter()
    output_file_count = 0
    trace = runtime / "outputs" / "short-drama-projects" / project_id / "studio-trace"
    for job in jobs:
        for path in (trace / job["id"]).glob("*-model.output.json"):
            output_file_count += 1
            wrapper = json.loads(path.read_text())
            data = wrapper["data"]
            try:
                parsed = json.loads(data["text"])
            except (ValueError, TypeError):
                output_type_counts["not_plain_json"] += 1
                continue
            if not isinstance(parsed, dict):
                output_type_counts["not_object"] += 1
                continue
            if "requested_tool_schema_ids" in parsed:
                output_type_counts["schema_request"] += 1
                outputs.append((wrapper["created_at"], data, list(dict.fromkeys(parsed["requested_tool_schema_ids"]))))
            elif "tool_name" in parsed:
                output_type_counts["tool_request"] += 1
            elif "goal_complete" in parsed:
                output_type_counts["leader_plan"] += 1
            elif "concise_summary" in parsed:
                output_type_counts["worker_result"] += 1
            else:
                output_type_counts["observation_or_other"] += 1
    schema_seen: set[tuple[str, str]] = set()
    uninterrupted_seen: set[tuple[str, int, str]] = set()
    schema_pairs = Counter()
    repeated_pairs = Counter()
    response_classes = Counter()
    class_metrics = defaultdict(list)
    uninterrupted_classes = Counter()
    uninterrupted_metrics = defaultdict(list)
    resets = defaultdict(list)
    for _, kind, created, data in events:
        if kind == "job.updated" and data.get("status") in {"queued", "paused", "failed", "waiting_user"}:
            resets[data["job_id"]].append(created)
    repeat_samples = []
    for created, data, tool_ids in sorted(outputs, key=lambda item: item[0]):
        work_id = data.get("work_order_id")
        matched = completion_by_id.get(data["call_id"])
        if not work_id:
            response_class = "no_work_order"
            uninterrupted_class = "no_work_order"
        else:
            prior = [tool_id for tool_id in tool_ids if (work_id, tool_id) in schema_seen]
            fresh = [tool_id for tool_id in tool_ids if (work_id, tool_id) not in schema_seen]
            response_class = "all_previously_discovered" if prior and not fresh else "mixed_new_and_repeated" if prior else "all_first_discovery"
            for tool_id in tool_ids:
                schema_pairs[tool_id] += 1
                if (work_id, tool_id) in schema_seen:
                    repeated_pairs[tool_id] += 1
                schema_seen.add((work_id, tool_id))
            if response_class == "all_previously_discovered" and len(repeat_samples) < 8:
                repeat_samples.append({"call_id": data["call_id"], "created_at": created, "work_order_id": work_id, "agent_role": data.get("agent_role"), "tool_ids": tool_ids})
            job_id = matched[2].get("job_id") if matched else None
            epoch = bisect_right(resets.get(job_id, []), created)
            prior_in_epoch = [tool_id for tool_id in tool_ids if (work_id, epoch, tool_id) in uninterrupted_seen]
            fresh_in_epoch = [tool_id for tool_id in tool_ids if (work_id, epoch, tool_id) not in uninterrupted_seen]
            uninterrupted_class = "all_previously_discovered" if prior_in_epoch and not fresh_in_epoch else "mixed_new_and_repeated" if prior_in_epoch else "all_first_discovery"
            uninterrupted_seen.update((work_id, epoch, tool_id) for tool_id in tool_ids)
        response_classes[response_class] += 1
        uninterrupted_classes[uninterrupted_class] += 1
        if matched:
            class_metrics[response_class].append(matched[2])
            uninterrupted_metrics[uninterrupted_class].append(matched[2])
    result["model_output_classification"] = {"targeted_files_read": output_file_count, "counts": dict(output_type_counts), "note": "Plain JSON only; unparsed output retained as not_plain_json rather than guessed."}
    result["schema_discovery"] = {
        "response_count": len(outputs), "responses_by_class": dict(response_classes),
        "tool_id_occurrences": sum(schema_pairs.values()), "unique_work_order_tool_pairs": len(schema_seen),
        "repeated_work_order_tool_occurrences": sum(repeated_pairs.values()),
        "tool_ids_requested": most_common(schema_pairs, 30), "tool_ids_repeated_in_work_order": most_common(repeated_pairs, 30),
        "completion_metrics_by_class": {key: language_metrics(values) for key, values in class_metrics.items()},
        "all_repeat_samples": repeat_samples,
        "uninterrupted_job_segment": {
            "reset_states": ["queued", "paused", "failed", "waiting_user"],
            "responses_by_class": dict(uninterrupted_classes),
            "completion_metrics_by_class": {key: language_metrics(values) for key, values in uninterrupted_metrics.items()},
            "meaning": "Discovery memory is deliberately cleared at every listed job-state event, excluding repetitions across repair/restart/pause/review boundaries. This is stricter than whole-WorkOrder counting. Schema content hashes were not read from large input payloads.",
        },
        "interpretation": "All-previously-discovered responses repeat Tool IDs within a WorkOrder; some schemas may have changed during case repair/repackaging. Do not claim all 802 or all same-ID repeats are guaranteed removable. Retained schema must refresh on actual schema changes. Actual savings need protocol replay and cannot equal summed request elapsed under concurrency.",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Directory containing the source short-drama.sqlite3")
    parser.add_argument("--project-id", default="project-974ca837e45e")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("01-current-metrics.json"))
    args = parser.parse_args()
    data = measure(args.runtime, args.project_id)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    summary = {"output": str(args.output), "language_calls": data["language"]["calls"], "schema": data["schema_discovery"]["responses_by_class"], "schema_tool_pairs": {key: data["schema_discovery"][key] for key in ("tool_id_occurrences", "unique_work_order_tool_pairs", "repeated_work_order_tool_occurrences")}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
