"""Historical receipt facts survive the actual model view and durable sources."""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime.completion_receipt_view import project_completion_receipt_view
from short_drama.agent_runtime.context_markers import context_read_contract
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.leader_context import completed_work_handoffs
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.production_manager import LanguageProductionPlanner
from tests.test_agent_runtime import FakeArtifactResolver
from tests.test_completed_worker_handoff import completed, worker_input
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work


def _receipts(project_id: str = "project-test", run_id: str = "run") -> list[JsonValue]:
    ref: dict[str, JsonValue] = {"project_id": project_id, "artifact_type": "editorial_timeline",
        "artifact_id": "film", "version": 4}
    return [{"kind": "saved_result_read", "read": context_read_contract(project_id=project_id, run_id=run_id)},
        {"kind": "saved_tool_result", "sequence": 3, "tool_name": "video.select_take", "status": "SUCCEEDED",
         "observed_at": "2026-09-17T00:00:00Z", "artifact_refs": [deepcopy(ref)],
         "current_artifact_refs": [deepcopy(ref)],
         "artifact_currentness": [{"artifact_ref": deepcopy(ref), "source_revision": 4, "current_revision": 4,
             "source_status": "CURRENT", "current_content_verified": True}],
         "saved_result": {"context_ref": "exact-source", "pointer": "/result"},
         "saved_arguments": {"context_ref": "exact-source", "pointer": "/arguments"}},
        {"kind": "saved_media_metadata", "asset_version_id": "v1", "metadata": {
            "freshness": "CURRENT", "source": {"source_uri": "/original/source.png"}},
         "note": "File availability is not image quality."},
        {"kind": "saved_media_observation", "asset_version_id": "v1", "scope": {"start": 3, "end": 5.5},
         "result": {"issues": ["continuity remains UNKNOWN"], "audio_semantics_observed": False},
         "saved_result": {"context_ref": "exact-source", "pointer": "/result/output/observation"},
         "saved_arguments": {"context_ref": "exact-source", "pointer": "/arguments"}}]


def _restore_refs(value: JsonValue, project_id: str) -> JsonValue:
    if isinstance(value, dict):
        keys = {"artifact_type", "artifact_id", "version"}
        if set(value) == keys:
            return {**value, "project_id": project_id}
        return {key: _restore_refs(item, project_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_refs(item, project_id) for item in value]
    return value


def restore_receipts(rows: JsonValue, shared: dict[str, JsonValue]) -> JsonValue:
    """Test oracle implements the model's visible contract, with no hidden map."""
    restored = _restore_refs(deepcopy(rows), cast(str, shared["project_id"]))
    assert isinstance(restored, list)
    for row in restored:
        if not isinstance(row, dict):
            continue
        if row.pop("shared_read", False):
            row["read"] = deepcopy(shared["read"])
        source = row.pop("saved_source", None)
        if isinstance(source, dict):
            row["saved_result"] = {"context_ref": source["context_ref"], "pointer": source["result_pointer"]}
            row["saved_arguments"] = {"context_ref": source["context_ref"], "pointer": source["arguments_pointer"]}
        if row.pop("current_refs_match_saved", False):
            row["current_artifact_refs"] = deepcopy(row["artifact_refs"])
    return restored


def _working(receipts: list[JsonValue]) -> dict[str, JsonValue]:
    return {"completion_handoffs": [{"result": {"completion_receipts": receipts,
        "deliverable": {"clip_id": "clip03", "source": [3.0, 5.541666667], "audio_policy": "KEEP_CURRENT_AUDIO"},
        "findings": ["Static frames; hearing UNKNOWN."]}}],
        "latest_handoff": {"result": {"receipt": {"current_state": {
            "clips": [{"id": "clip03", "sourceStart": 3.0, "sourceEnd": 5.541666667}],
            "audio_clips": [{"id": "dialogue", "muted": False, "volume": 0.8}]}}}}}


def test_visible_contract_restores_all_receipts_without_an_external_mapping() -> None:
    original = _working(_receipts())
    original["completion_handoffs"] = cast(list[JsonValue], original["completion_handoffs"]) * 2
    before = deepcopy(original)
    projected = project_completion_receipt_view(original, project_id="project-test")
    shared = cast(dict[str, JsonValue], projected["completion_receipt_view"])
    rows = cast(list[JsonValue], projected["completion_handoffs"])
    for row in rows:
        assert isinstance(row, dict) and isinstance(row["result"], dict)
        assert restore_receipts(row["result"]["completion_receipts"], shared) == _receipts()
    assert rows[0] == rows[1]  # Both remain expanded, no cross-handoff dependency.
    assert pointer_value(projected, "/completion_handoffs/0/result/deliverable") == pointer_value(before, "/completion_handoffs/0/result/deliverable")
    assert projected["latest_handoff"] == original["latest_handoff"]
    assert project_completion_receipt_view(projected, project_id="project-test") == projected
    assert original == before


@pytest.mark.parametrize("variation", ["historical", "unknown_version", "missing_current", "different_source", "missing_source", "other_project"])
def test_historical_missing_and_different_sources_are_not_promoted(variation: str) -> None:
    receipts = _receipts()
    row = cast(dict[str, JsonValue], receipts[1])
    if variation == "historical":
        row["current_artifact_refs"] = []
        row["artifact_currentness"] = [{"source_status": "HISTORICAL_REVISION", "source_revision": 4,
            "current_revision": 6, "current_content_verified": False}]
    elif variation == "unknown_version":
        row["artifact_refs"] = [{"project_id": "project-test", "artifact_type": "editorial_timeline", "artifact_id": "film", "version": None}]
        row["current_artifact_refs"] = []
    elif variation == "missing_current":
        del row["current_artifact_refs"]
    elif variation == "different_source":
        row["saved_arguments"] = {"context_ref": "different-source", "pointer": "/arguments"}
    elif variation == "missing_source":
        del row["saved_arguments"]
    else:
        row["artifact_refs"] = [{"project_id": "other-project", "artifact_type": "take", "artifact_id": "take", "version": None}]
    projected = project_completion_receipt_view(_working(receipts), project_id="project-test")
    shared = cast(dict[str, JsonValue], projected["completion_receipt_view"])
    assert restore_receipts(pointer_value(projected, "/completion_handoffs/0/result/completion_receipts"), shared) == receipts
    view = cast(dict[str, JsonValue], pointer_value(projected, "/completion_handoffs/0/result/completion_receipts/1"))
    if variation in {"historical", "unknown_version", "missing_current", "other_project"}:
        assert "current_refs_match_saved" not in view
    if variation in {"different_source", "missing_source"}:
        assert "saved_source" not in view


def test_unknown_pointer_only_and_different_read_contracts_are_preserved() -> None:
    unknown: dict[str, JsonValue] = {"kind": {"future": "receipt"}, "artifact_refs": [{"project_id": "project-test"}]}
    assert project_completion_receipt_view(_working([unknown]), project_id="project-test") == _working([unknown])
    assert project_completion_receipt_view({"completion_handoffs": [{"result": {"context_ref": "saved", "pointer": "/result"}}]}, project_id="project-test") == {"completion_handoffs": [{"result": {"context_ref": "saved", "pointer": "/result"}}]}
    rows = _receipts()
    different = {"kind": "saved_result_read", "read": context_read_contract(project_id="project-test", run_id="other-run")}
    rows.append(different)
    projected = project_completion_receipt_view(_working(rows), project_id="project-test")
    assert pointer_value(projected, "/completion_handoffs/0/result/completion_receipts/4") == different


def test_actual_leader_and_worker_views_leave_saved_completion_and_sources_intact(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    work, result = completed(store, context.run_id, context.project_id)
    result.completion_receipts = cast(list[dict[str, JsonValue]], _receipts(context.project_id, context.run_id))
    store.save_agent_result(result)
    source: dict[str, JsonValue] = {"arguments": {"clip_ids": ["clip03"], "source_in_seconds": 3.0,
        "source_out_seconds": 5.541666667, "audio_policy": "KEEP_CURRENT_AUDIO", "expected_editorial_revision": 4},
        "result": {"receipt": {"current_state": {"clips": [{"id": "clip03", "trimStart": 3.0}],
            "audio_clips": [{"id": "dialogue", "volume": 0.8}]}}, "hearing": "UNKNOWN"}}
    store.save_context_snapshot("exact-source", context.run_id, source)
    receiver.task_inputs = {"result_id": result.id}
    store.save_work_order(receiver)
    before = result.model_dump(mode="json")
    raw, _ = completed_work_handoffs(store, run_id=context.run_id, project_id=context.project_id)
    assert pointer_value(raw, "/0/result/completion_receipts") == result.completion_receipts
    client = ScriptedClient([json.dumps({"summary": "Use saved conclusions", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(store)
    planner.propose_plan(context=ContextCompiler(store, FakeArtifactResolver([])).compile(context.run_id),
        skill_prompt="", method_skills=[], tools=registry().list(), current_plan=None, reason="Continue")
    leader = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    worker = worker_input(AgentRuntimeStore(store.database_path), receiver, context)
    for view in [leader, worker]:
        shared = cast(dict[str, JsonValue], view["completion_receipt_view"])
        rows = pointer_value(view, "/completion_handoffs/0/result/completion_receipts")
        assert restore_receipts(rows, shared) == result.completion_receipts
        saved_source = cast(dict[str, JsonValue], pointer_value(rows, "/1/saved_source"))
        reopened = AgentRuntimeStore(store.database_path)
        saved = reopened.get_context_snapshot(cast(str, saved_source["context_ref"]), context.run_id)
        assert saved == source
        assert pointer_value(saved, cast(str, saved_source["arguments_pointer"])) == source["arguments"]
        assert pointer_value(saved, cast(str, saved_source["result_pointer"])) == source["result"]
    saved_result = store.get_agent_result(work.id)
    assert saved_result is not None and saved_result.model_dump(mode="json") == before
    saved_completion = store.get_context_snapshot(f"context-completion-{result.id}", context.run_id)
    assert saved_completion == {"result": before}


def test_shared_contract_survives_normal_model_input_overflow(tmp_path: Path) -> None:
    store, _work, context = runtime_work(tmp_path / "agent.sqlite3")
    working = _working(_receipts(context.project_id, context.run_id))
    body = cast(dict[str, JsonValue], pointer_value(working, "/completion_handoffs/0/result"))
    body["findings"] = ["An exact saved finding with hearing UNKNOWN. " * 1000]
    projected = project_completion_receipt_view(working, project_id=context.project_id)
    compiler = ModelInputCompiler(store, budget=ContextBudget(window_tokens=12000, compact_at_tokens=5000, target_tokens=3000))
    prepared = compiler.prepare(run_id=context.run_id, project_id=context.project_id, instructions="Keep facts.",
        payload={"working_set": projected}, max_output_tokens=500)
    payload = cast(dict[str, JsonValue], json.loads(prepared.input_text))
    assert prepared.offloaded_blocks > 0
    assert pointer_value(payload, "/working_set/completion_receipt_view") == projected["completion_receipt_view"]
    marker = cast(dict[str, JsonValue], pointer_value(payload, "/working_set/completion_handoffs/0/result"))
    saved = store.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert saved is not None
    historical = cast(dict[str, JsonValue], pointer_value(saved, cast(str, marker["pointer"])))
    assert restore_receipts(historical["completion_receipts"], cast(dict[str, JsonValue], projected["completion_receipt_view"])) == _receipts(context.project_id, context.run_id)
