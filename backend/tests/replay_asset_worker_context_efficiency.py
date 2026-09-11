"""Offline replay of three saved closeup inputs; only temporary stores are written.

Run from backend:
  .venv/bin/python -m tests.replay_asset_worker_context_efficiency
Requires the original local debug Trace/DB; no provider or product service runs.
"""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text, pointer_value
from short_drama.agent_runtime.models import AgentRun, RunMemory, WorkOrder
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.agent_runtime.tool_context import initial_tool_schema_ids, project_retained_read_results
from short_drama.production_manager import _compact_tool_catalog_entry
from short_drama.service import ShortDramaService
from short_drama.tools.artifact import register_tools
from short_drama.tools.contracts import ToolDescriptor
from short_drama.tools.registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "output/quality-e2e-2026-09-12/runtime"
TRACE = RUNTIME / "outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171"
FILES = (
    "20260911T204509308576-2b305de2-model.input.json",
    "20260911T204516025138-e1b623e8-model.input.json",
    "20260911T204518348595-29046806-model.input.json",
)


def replay() -> list[dict[str, JsonValue]]:
    # Registration constructs descriptions/closures only. The absent service
    # makes any accidental Tool execution fail before touching product state.
    tools = ToolRegistry()
    register_tools(tools, cast(ShortDramaService, None))
    descriptor = tools.describe("artifact.patch_asset_plan_fields")
    field_schema = cast(JsonValue, {"description": descriptor.description, "input_schema": descriptor.input_schema})
    new_paragraph = next(line for line in (ROOT / "backend/short_drama/skills/shared/roles/asset_builder.md").read_text().splitlines()
                         if line.startswith("- 每个 work 的候选媒体成功落盘后"))
    provider_events: dict[str, dict[str, JsonValue]] = {}
    for path in TRACE.glob("*provider.language_generation.json"):
        event = cast(dict[str, JsonValue], json.loads(path.read_text())["data"])
        provider_events[cast(str, event["call_id"])] = event
    results: list[dict[str, JsonValue]] = []
    with sqlite3.connect((RUNTIME / "short-drama.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as source:
        for name in FILES:
            data = cast(dict[str, JsonValue], json.loads((TRACE / name).read_text())["data"])
            payload = cast(dict[str, JsonValue], json.loads(cast(str, data["input_text"])))
            instructions = cast(str, data["instructions"])
            assert json_text(payload) == data["input_text"]
            original = deepcopy(payload)
            work = WorkOrder.model_validate(payload["work_order"])
            project_id = cast(str, pointer_value(payload, "/context/project_id"))
            old_paragraph = next(line for line in instructions.splitlines() if line.startswith("- 每个 work 的候选媒体成功落盘后"))
            revised_instructions = instructions.replace(old_paragraph, new_paragraph)
            baseline = count_text_tokens(instructions) + count_text_tokens(json_text(payload))
            with TemporaryDirectory(prefix="asset-context-replay-") as temporary:
                store = AgentRuntimeStore(Path(temporary) / "runtime.sqlite3")
                store.create_run(AgentRun(id=work.parent_run_id, project_id=project_id, objective=work.objective, root_agent="leader"),
                                 RunMemory(run_id=work.parent_run_id))
                working = cast(dict[str, JsonValue], payload["working_set"])
                for observation in cast(list[dict[str, JsonValue]], working["read_observations"]):
                    snapshot_id = f"context-read-{work.id}-{observation['source_sequence']}"
                    row = source.execute("SELECT record_json FROM agent_context_snapshots WHERE id=? AND run_id=?",
                                         (snapshot_id, work.parent_run_id)).fetchone()
                    assert row is not None, f"Original retained snapshot missing: {snapshot_id}"
                    store.save_context_snapshot(snapshot_id, work.parent_run_id, json.loads(row[0]))
                projected = project_retained_read_results(working, store=store, project_id=project_id, work_order=work)
                assert projected["read_observations"] == working["read_observations"]
                assert payload == original
                body_only = {**payload, "working_set": projected}
                body_saved = baseline - count_text_tokens(instructions) - count_text_tokens(json_text(body_only))
                payload["working_set"] = projected
                old_catalogue = cast(list[dict[str, JsonValue]], payload["available_tools"])
                payload["available_tools"] = [_compact_tool_catalog_entry(ToolDescriptor.model_validate({
                    **entry, "input_schema": {}, "output_schema": {}})) for entry in old_catalogue]
                catalogue_saved = count_text_tokens(json_text(body_only)) - count_text_tokens(json_text(payload))
                schemas = cast(dict[str, JsonValue], payload["tool_schemas"])
                new_schemas = {("artifact.patch_asset_plan_fields" if key == "artifact.patch_asset_plan" else key):
                               (field_schema if key == "artifact.patch_asset_plan" else value) for key, value in schemas.items()}
                resumed_schemas = {key: field_schema if key == "artifact.patch_asset_plan_fields" else schemas[key]
                    for key in dict.fromkeys([*initial_tool_schema_ids(work), *schemas])
                    if key == "artifact.patch_asset_plan_fields" or key in schemas}
                estimates: dict[str, JsonValue] = {}
                for label, contracts in (("default_schema_replacement", new_schemas), ("existing_loaded_schema_retained", resumed_schemas)):
                    prepared = ModelInputCompiler(store).prepare(run_id=work.parent_run_id, project_id=project_id,
                        instructions=revised_instructions, payload={**payload, "tool_schemas": contracts},
                        max_output_tokens=cast(int, data["max_output_tokens"]))
                    assert prepared.input_tokens == count_text_tokens(revised_instructions) + count_text_tokens(prepared.input_text)
                    assert prepared.offloaded_blocks == 0
                    estimates[label] = {"estimated_input_tokens": prepared.input_tokens, "saved_vs_original": baseline - prepared.input_tokens}
                shared = [entity for entity in cast(list[dict[str, JsonValue]], projected["entities"])
                          if "context_ref" in cast(dict[str, JsonValue], entity["state"])]
                for entity in shared:
                    marker = cast(dict[str, JsonValue], entity["state"])
                    snapshot = store.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id)
                    assert snapshot is not None
                    assert isinstance(pointer_value(snapshot, cast(str, marker["pointer"])), dict)
                event = provider_events[cast(str, data["call_id"])]
                results.append({"call_id": data["call_id"], "trace_file": name,
                    "original_estimated_input_tokens": baseline,
                    "original_provider_input_tokens": event["input_tokens"],
                    "original_provider_output_tokens": event["output_tokens"],
                    "original_provider_elapsed_seconds": event["elapsed_seconds"],
                    "catalogue_tokens_saved": catalogue_saved, "shared_body_tokens_saved": body_saved,
                    "shared_entities": [entity["id"] for entity in shared],
                    "instruction_tokens_added": count_text_tokens(revised_instructions) - count_text_tokens(instructions),
                    "offline_estimates": estimates})
    return results


if __name__ == "__main__":
    print(json.dumps(replay(), ensure_ascii=False, indent=2))
