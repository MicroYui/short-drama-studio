"""Final-input references stay exact across restart and budget offloading."""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ArtifactRef
from short_drama.agent_runtime.input_references import project_visible_artifact_refs
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.studio_trace import production_trace
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry, runtime_work


def _refs(count: int = 12, version: int = 3) -> list[JsonValue]:
    return [cast(JsonValue, {"project_id": "project-test", "artifact_type": "shot",
        "artifact_id": f"shot-local-{index:03d}", "version": version}) for index in range(count)]


def _payload() -> dict[str, JsonValue]:
    refs = _refs()
    result = {"status": "SUCCEEDED", "artifact_refs": refs,
        "receipt": {"operation_completed": True, "artifact_refs": refs,
                    "current_state": {"version": 3}, "changed_ids": {"shots": ["shot-local-000"]}},
        "error": {"kind": "HISTORICAL_NOTE", "message": "Keep this exact diagnostic"}}
    return cast(dict[str, JsonValue], {"working_set": {
        "artifact_refs": refs,
        "latest_handoff": {"attempt_ref": {"work_order_id": "source-worker"}, "result": result},
        "tool_checkpoints": [{"tool_name": "shot.patch_storyboard", "source_sequence": 7, "artifact_refs": refs},
                             {"tool_name": "shot.finalize_storyboard", "source_sequence": 4, "artifact_refs": _refs(version=2)}],
        "progress": {"artifact_refs": refs, "worker_handoff": {"author": "director", "summary": "Saved local edits", "artifact_refs": refs}},
        "read_observations": [{"result": {"output": {"authored_json": result}}}],
    }, "work_order": {"task_inputs": {"user_json": result}}})


def _resolve(node: dict[str, JsonValue], payload: dict[str, JsonValue]) -> list[JsonValue]:
    if "artifact_refs" in node:
        return cast(list[JsonValue], node["artifact_refs"])
    lookup = cast(dict[str, JsonValue], node["artifact_refs_from"])
    table = pointer_value(payload, cast(str, lookup["pointer"]))
    assert isinstance(table, list)
    return [table[cast(int, index)] for index in cast(list[JsonValue], lookup["indices"])]


def test_final_projection_shares_exact_values_without_touching_bodies_or_history() -> None:
    original = _payload()
    saved = deepcopy(original)
    actual = project_visible_artifact_refs(original)
    work = cast(dict[str, JsonValue], actual["working_set"])
    latest = cast(dict[str, JsonValue], work["latest_handoff"])
    result = cast(dict[str, JsonValue], latest["result"])
    receipt = cast(dict[str, JsonValue], result["receipt"])
    checkpoints = cast(list[dict[str, JsonValue]], work["tool_checkpoints"])
    progress = cast(dict[str, JsonValue], work["progress"])
    for node in [result, receipt, checkpoints[0], progress, cast(dict[str, JsonValue], progress["worker_handoff"])]:
        assert "artifact_refs_from" in node
        assert _resolve(node, actual) == _refs()
    assert checkpoints[1]["artifact_refs"] == _refs(version=2)
    assert latest["attempt_ref"] == {"work_order_id": "source-worker"}
    assert receipt["current_state"] == {"version": 3}
    assert receipt["changed_ids"] == {"shots": ["shot-local-000"]}
    assert result["error"] == {"kind": "HISTORICAL_NOTE", "message": "Keep this exact diagnostic"}
    assert work["read_observations"] == cast(dict[str, JsonValue], saved["working_set"])["read_observations"]
    assert actual["work_order"] == saved["work_order"]
    assert original == saved
    assert project_visible_artifact_refs(actual) == actual


@pytest.mark.parametrize("field,value", [("version", 2), ("project_id", "other-project"),
    ("artifact_type", "asset_version"), ("source", "different-origin")])
def test_a_different_complete_reference_is_not_shared(field: str, value: JsonValue) -> None:
    payload = _payload()
    work = cast(dict[str, JsonValue], payload["working_set"])
    refs = _refs()
    cast(dict[str, JsonValue], refs[-1])[field] = value
    progress = cast(dict[str, JsonValue], work["progress"])
    progress["artifact_refs"] = refs
    actual = project_visible_artifact_refs(payload)
    projected = cast(dict[str, JsonValue], cast(dict[str, JsonValue], actual["working_set"])["progress"])
    assert projected["artifact_refs"] == refs
    assert "artifact_refs_from" not in projected


def test_shared_lookup_keeps_repeated_items_and_original_order() -> None:
    payload = _payload()
    working = cast(dict[str, JsonValue], payload["working_set"])
    progress = cast(dict[str, JsonValue], working["progress"])
    refs = _refs()
    selected = [refs[index] for index in (8, 3, 8, 1, 11, 0)]
    progress["artifact_refs"] = selected
    actual = project_visible_artifact_refs(payload)
    projected = cast(dict[str, JsonValue], cast(dict[str, JsonValue], actual["working_set"])["progress"])
    assert _resolve(projected, actual) == selected


def test_compiler_offloaded_table_cannot_leave_dangling_indices_and_preserves_snapshot(tmp_path: Path) -> None:
    store, _work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = cast(dict[str, JsonValue], {"working_set": {"artifact_refs": _refs(80),
        "latest_handoff": {"result": {"status": "SUCCEEDED", "artifact_refs": _refs(3),
            "receipt": {"artifact_refs": _refs(3), "operation_completed": True}}}},
        "goal": "Keep the current edit"})
    saved = deepcopy(original)
    events: list[tuple[str, dict[str, JsonValue]]] = []
    compiler = ModelInputCompiler(store, budget=ContextBudget(window_tokens=2500,
        compact_at_tokens=1800, target_tokens=1000, envelope_tokens=100))
    with production_trace(tmp_path / "trace", lambda kind, data: events.append((kind, data))):
        prepared = compiler.prepare(run_id=context.run_id, project_id=context.project_id,
            instructions="Use saved facts.", payload=original, max_output_tokens=100)
    actual = cast(dict[str, JsonValue], json.loads(prepared.input_text))
    work = cast(dict[str, JsonValue], actual["working_set"])
    marker = cast(dict[str, JsonValue], work["artifact_refs"])
    assert "context_ref" in marker
    result = cast(dict[str, JsonValue], cast(dict[str, JsonValue], work["latest_handoff"])["result"])
    assert result["artifact_refs"] == _refs(3)
    receipt = cast(dict[str, JsonValue], result["receipt"])
    assert _resolve(receipt, actual) == _refs(3)
    assert cast(dict[str, JsonValue], receipt["artifact_refs_from"])["pointer"] == "/working_set/latest_handoff/result/artifact_refs"
    reopened = AgentRuntimeStore(store.database_path)
    assert reopened.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id) == saved
    assert original == saved
    assert prepared.input_tokens == count_text_tokens("Use saved facts.") + count_text_tokens(prepared.input_text)
    stats = next(data for kind, data in events if kind == "context.prepared")
    assert stats["estimated_input_tokens"] == prepared.input_tokens


def test_restarted_worker_sends_resolvable_shared_refs_and_keeps_complete_tool_trace(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    refs = _refs()
    result = cast(dict[str, JsonValue], {"tool_id": "shot.patch_storyboard", "status": "SUCCEEDED",
        "artifact_refs": refs, "warnings": [], "error": None,
        "output": {"saved_authored_text": "The persisted original remains complete"},
        "receipt": {"operation_completed": True, "artifact_refs": refs,
                    "current_state": {"version": 3}, "changed_ids": {"shots": ["shot-local-000"]}}})
    work.specialist_turns = [SpecialistTurnRecord(id="saved-edit", sequence=1,
        tool_name="shot.patch_storyboard", arguments={"project_id": context.project_id},
        reason="Only the selected shot changed", status=SpecialistTurnStatus.COMPLETED, result=result)]
    store.save_work_order(work)
    AgentRuntime(store).append_work_order_progress(work.id, author=work.recipient_role,
        summary="Keep the new shot references", artifact_refs=[ArtifactRef.model_validate(ref) for ref in refs])
    before = store.get_work_order(work.id)
    assert before is not None
    tools = registry()
    tools.register(ToolSpec(id="shot.patch_storyboard", version=1, category=ToolCategory.SHOT,
        description="Local edit", effect=ToolEffect.WRITE, idempotent=False,
        input_model=ReadInput, output_model=ReadOutput,
        handler=lambda _request: ToolExecution(output=ReadOutput(status="saved"))))
    reopened = AgentRuntimeStore(store.database_path)
    resumed_work = reopened.get_work_order(work.id)
    assert resumed_work is not None
    client = ScriptedClient([json.dumps({"tool_name": "project.get",
        "arguments": {"project_id": context.project_id}, "reason": "Inspect the next current item"})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed_work, context=context,
        system_prompt="Keep saved work", progress_entries=reopened.list_work_order_progress(work.id)))
    actual = client.payloads[0]
    working = cast(dict[str, JsonValue], actual["working_set"])
    latest = cast(dict[str, JsonValue], working["latest_handoff"])
    actual_result = cast(dict[str, JsonValue], latest["result"])
    for node in [actual_result, cast(dict[str, JsonValue], actual_result["receipt"]),
                 cast(dict[str, JsonValue], working["progress"])]:
        assert "artifact_refs_from" in node
        assert _resolve(node, actual) == refs
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None
    assert persisted.specialist_turns == before.specialist_turns
    assert persisted.specialist_turns[0].result == result
    assert cast(dict[str, JsonValue], actual_result["receipt"])["current_state"] == {"version": 3}
    assert count_text_tokens(json_text(actual)) > 0
