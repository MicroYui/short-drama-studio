"""Fresh4 completion is visible once, with its exact durable source intact."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import ActionKind, AgentRuntime, AgentRuntimeStore
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.leader_context import completed_work_handoffs, project_leader_completion_actions
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text, pointer_value
from short_drama.agent_runtime.models import AgentResult
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.production_manager import LanguageProductionPlanner
from tests.test_agent_runtime import FakeArtifactResolver
from tests.test_harness_tool_context import ScriptedClient, registry


FIXTURE = Path(__file__).parent / "fixtures" / "fresh4_director_context.json"


def replay(tmp_path: Path) -> tuple[AgentRuntimeStore, str, dict[str, JsonValue], dict[str, JsonValue]]:
    fixture = json.loads(FIXTURE.read_text())
    result = AgentResult.model_validate(fixture["result"])
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project-06116be431fa", objective="Prepare references, no video", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="director", objective="Complete the storyboard", why_now="Needed directory")
    # Only local run/work linkage changes; all authored content/refs remain real.
    result.work_order_id = work.id
    store.save_agent_result(result)
    original_action = fixture["action"]
    action = runtime.begin_action(run.id, kind=ActionKind.DELEGATION, agent_id="leader",
        input_summary=original_action["summary"], work_order_id=work.id)
    runtime.complete_action(action.id, output_summary=original_action["summary"])
    # Generate the new identity through the real compiler from the saved result;
    # never annotate an old model input and call it naturally deduplicated.
    context = cast(dict[str, JsonValue], ContextCompiler(store, FakeArtifactResolver([])).compile(run.id).model_context_dump())
    digest = cast(dict[str, JsonValue], pointer_value(context, "/recent_actions/0/agent_result"))
    for key in ("deliverable", "findings", "completion_receipts"):
        assert digest[key] == original_action["agent_result"][key]
    rows, refs = completed_work_handoffs(store, run_id=run.id, project_id=run.project_id)
    return store, run.id, context, {
        "completion_handoffs": rows, "artifact_refs": [ref.model_dump(mode="json") for ref in refs]}


def test_real_completed_fields_are_equivalent_and_not_copied_twice(tmp_path: Path) -> None:
    store, run_id, context, working = replay(tmp_path)
    original = deepcopy(context)
    projected = project_leader_completion_actions(context, working, store=store, run_id=run_id)
    digest = pointer_value(projected, "/recent_actions/0/agent_result")
    assert isinstance(digest, dict)
    assert digest["shared_completion_fields"] == ["deliverable", "completion_receipts", "findings"]
    assert digest["status"] == "COMPLETED"
    marker = cast(dict[str, JsonValue], digest["saved_result"])
    reopened = AgentRuntimeStore(store.database_path)
    source = reopened.get_context_snapshot(cast(str, marker["context_ref"]), run_id)
    assert source is not None
    for field in cast(list[str], digest["shared_completion_fields"]):
        assert field not in digest
        assert pointer_value(source, "/result/" + field) == pointer_value(original, "/recent_actions/0/agent_result/" + field)
    assert context == original
    before = count_text_tokens(json_text({"context": original, "working_set": working}))
    after = count_text_tokens(json_text({"context": projected, "working_set": working}))
    assert 2800 < before - after < 3000  # Exact local text estimate, not Provider usage/time.


@pytest.mark.parametrize("case", ["legacy", "other-result", "other-work", "partial", "scoped", "missing-source", "ambiguous-source"])
def test_unknown_or_different_completion_source_is_not_deduplicated(tmp_path: Path, case: str) -> None:
    store, run_id, context, working = replay(tmp_path)
    digest = cast(dict[str, JsonValue], pointer_value(context, "/recent_actions/0/agent_result"))
    row = cast(dict[str, JsonValue], pointer_value(working, "/completion_handoffs/0"))
    if case == "legacy":
        digest.pop("result_id")
    elif case == "other-result":
        row["result_id"] = "different-result-with-identical-content"
    elif case == "other-work":
        row["work_order_id"] = "different-work"
    elif case == "partial":
        digest["status"] = "PARTIAL"
    elif case == "scoped":
        row["matched_artifact_ids"] = ["shot-qiantai-01"]
    elif case == "missing-source":
        cast(dict[str, JsonValue], row["saved_result"])["context_ref"] = "missing"
    else:
        cast(list[JsonValue], working["completion_handoffs"]).append(deepcopy(row))
    original = deepcopy(context)
    assert project_leader_completion_actions(context, working, store=store, run_id=run_id) == original


def test_only_identical_fields_are_shared_and_offloaded_handoff_still_reads_original(tmp_path: Path) -> None:
    store, run_id, context, working = replay(tmp_path)
    digest = cast(dict[str, JsonValue], pointer_value(context, "/recent_actions/0/agent_result"))
    digest["findings"] = ["A distinct later observation must remain visible."]
    projected = project_leader_completion_actions(context, working, store=store, run_id=run_id)
    assert pointer_value(projected, "/recent_actions/0/agent_result/findings") == digest["findings"]
    compiler = ModelInputCompiler(store, budget=ContextBudget(window_tokens=20000, compact_at_tokens=4500, target_tokens=2000))
    prepared = compiler.prepare(run_id=run_id, project_id="project-06116be431fa", instructions="Use actual saved facts.",
        payload={"context": projected, "working_set": working}, max_output_tokens=500)
    assert prepared.offloaded_blocks > 0
    after = json.loads(prepared.input_text)
    marker = after["context"]["recent_actions"][0]["agent_result"]["saved_result"]
    saved = AgentRuntimeStore(store.database_path).get_context_snapshot(marker["context_ref"], run_id)
    assert saved is not None
    assert pointer_value(saved, "/result/deliverable") == digest["deliverable"]
    # Existing completion-specific budget may have already externalized the body.
    rows, _ = completed_work_handoffs(store, run_id=run_id, project_id="project-06116be431fa", token_budget=0)
    working["completion_handoffs"] = rows
    again = project_leader_completion_actions(context, working, store=store, run_id=run_id)
    assert again == projected


def test_actual_planner_projects_exact_result_identity_after_restart(tmp_path: Path) -> None:
    store, run_id, _context, _working = replay(tmp_path)
    reopened = AgentRuntimeStore(store.database_path)
    context_pack = ContextCompiler(reopened, FakeArtifactResolver([])).compile(run_id)
    original = context_pack.model_dump(mode="json")
    client = ScriptedClient([json.dumps({"summary": "Prepare the next scope", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(reopened)
    planner.propose_plan(context=context_pack, skill_prompt="", method_skills=[], tools=registry().list(), current_plan=None, reason="Continue")
    digest = pointer_value(client.payloads[0], "/context/recent_actions/0/agent_result")
    assert isinstance(digest, dict) and "completion_receipts" not in digest
    assert digest["result_id"] == "agent-result-2bb45468c4094c33921e83341b4ba15b"
    assert pointer_value(client.payloads[0], "/working_set/completion_handoffs/0/result/deliverable") == json.loads(FIXTURE.read_text())["result"]["deliverable"]
    assert context_pack.model_dump(mode="json") == original
    assert len(client.payloads) == 1
