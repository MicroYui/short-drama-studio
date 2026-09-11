"""Rebuild the saved 125k planning request using read-only history and temp stores.

Run: .venv/bin/python -m tests.replay_asset_planner_context (from backend).
No Provider, live service, or production database writes occur.
"""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text
from short_drama.agent_runtime.models import AgentRun, RunMemory, WorkOrder
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.agent_runtime.tool_context import project_retained_read_results
from short_drama.production_manager import _specialist_working_set


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "output/quality-e2e-2026-09-12/runtime"
TRACE = RUNTIME / "outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171"
INPUT = "20260911T214133535572-320f4ba1-model.input.json"
USAGE = "20260911T214147599914-ec32646f-provider.language_generation.json"


def replay() -> dict[str, JsonValue]:
    data = json.loads((TRACE / INPUT).read_text())["data"]
    payload = cast(dict[str, JsonValue], json.loads(data["input_text"]))
    original = deepcopy(payload)
    work = WorkOrder.model_validate(payload["work_order"])
    context = cast(dict[str, JsonValue], payload["context"])
    project_id = cast(str, context["project_id"])
    old_working = cast(dict[str, JsonValue], payload["working_set"])
    with sqlite3.connect((RUNTIME / "short-drama.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as source:
        row = source.execute("SELECT record_json FROM agent_work_orders WHERE id=?", (work.id,)).fetchone()
        assert row is not None
        saved = WorkOrder.model_validate_json(row[0])
        work.specialist_turns = [turn for turn in saved.specialist_turns if turn.sequence <= 9]
        schemas = cast(dict[str, dict[str, JsonValue]], payload["tool_schemas"])
        working = _specialist_working_set(work, [], project_id=project_id,
            read_tool_ids=frozenset(turn.tool_name for turn in work.specialist_turns if turn.sequence < 9),
            write_tool_ids=frozenset({"artifact.patch_asset_plan"}),
            read_schemas={key: cast(dict[str, JsonValue], schema["input_schema"]) for key, schema in schemas.items()}).model_dump(mode="json")
        # Preserve the progress actually visible at this request's time, not
        # later progress from the still-live job.
        working["progress"] = deepcopy(old_working["progress"])
        with TemporaryDirectory(prefix="planner-context-replay-") as temporary:
            store = AgentRuntimeStore(Path(temporary) / "agent.sqlite3")
            store.create_run(AgentRun(id=work.parent_run_id, project_id=project_id, objective=work.objective, root_agent="leader"),
                             RunMemory(run_id=work.parent_run_id))
            for turn in work.specialist_turns:
                snapshot_id = f"context-read-{work.id}-{turn.sequence}"
                snapshot = source.execute("SELECT record_json FROM agent_context_snapshots WHERE id=? AND run_id=?",
                                          (snapshot_id, work.parent_run_id)).fetchone()
                if snapshot is not None:
                    store.save_context_snapshot(snapshot_id, work.parent_run_id, json.loads(snapshot[0]))
            projected = project_retained_read_results(working, store=store, project_id=project_id, work_order=work)
            pinned_read = next(read for read in cast(list[dict[str, JsonValue]], projected["read_observations"])
                               if read["tool_name"] == "artifact.get_asset_work_context")
            pinned_result = cast(dict[str, JsonValue], pinned_read["result"])
            pinned_output = cast(dict[str, JsonValue], pinned_result["output"])
            pinned_storyboard = cast(dict[str, JsonValue], pinned_output["storyboard"])
            pinned_shots = cast(dict[str, JsonValue], pinned_storyboard["shots"])
            assert "context_ref" in pinned_shots
            assert all("timeline_beats" not in item for item in cast(list[dict[str, JsonValue]], pinned_shots["items"]))
            payload["working_set"] = projected
            prepared = ModelInputCompiler(store).prepare(run_id=work.parent_run_id, project_id=project_id,
                instructions=data["instructions"], payload=payload, max_output_tokens=data["max_output_tokens"])
            actual = cast(dict[str, JsonValue], json.loads(prepared.input_text))
            assert prepared.offloaded_blocks == 0
            assert prepared.input_tokens == count_text_tokens(data["instructions"]) + count_text_tokens(prepared.input_text)
            old_shots = [entity for entity in cast(list[dict[str, JsonValue]], old_working["entities"]) if entity["kind"] == "shot"]
            new_shots = [entity for entity in cast(list[dict[str, JsonValue]], projected["entities"]) if entity["kind"] == "shot"]
            current = work.specialist_turns[0].result
            assert current is not None
            expected = cast(dict[str, JsonValue], current["output"])
            expected_shots = cast(dict[str, JsonValue], expected["storyboard"])["shots"]
            assert [cast(dict[str, JsonValue], entity["state"])["shot"] for entity in new_shots] == sorted(
                cast(list[dict[str, JsonValue]], expected_shots), key=lambda shot: cast(str, shot["id"]))
            old_tokens = count_text_tokens(data["instructions"]) + count_text_tokens(data["input_text"])
            event = json.loads((TRACE / USAGE).read_text())["data"]
            assert original["working_set"] == old_working
            return {
                "call_id": data["call_id"], "source_trace": INPUT,
                "original_estimated_input_tokens": old_tokens,
                "original_provider_input_tokens": event["input_tokens"],
                "original_provider_output_tokens": event["output_tokens"],
                "original_provider_elapsed_seconds": event["elapsed_seconds"],
                "new_offline_estimated_input_tokens": prepared.input_tokens,
                "new_input_characters": len(prepared.input_text),
                "estimated_tokens_saved": old_tokens - prepared.input_tokens,
                "old_shot_entities_tokens": count_text_tokens(json_text(cast(JsonValue, old_shots))),
                "current_shot_entities_tokens": count_text_tokens(json_text(cast(JsonValue, new_shots))),
                "new_top_level_tokens": {key: count_text_tokens(json_text(value)) for key, value in actual.items()},
                "preserved_current_shots": len(new_shots), "offloaded_blocks": prepared.offloaded_blocks,
                "retained_pinned_shot_index_tokens": count_text_tokens(json_text(pinned_shots)),
                "retained_pinned_full_shot_bodies": 0,
                "new_provider_usage": None,
            }


if __name__ == "__main__":
    print(json.dumps(replay(), ensure_ascii=False, indent=2))
