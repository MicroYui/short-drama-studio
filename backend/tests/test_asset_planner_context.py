"""Actual current-v10 / pinned-v6 planning reads and compact snapshot locators."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.context_markers import context_read_contract
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.agent_runtime.tool_context import project_retained_read_results
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_asset_worker_shot_context import CASES, _other_read, _setup, _shots, READS, WRITES
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry


FIXTURE: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/asset_planner_storyboard_sources.json").read_text())
PLANNER_READS = READS | {"artifact.get_asset_planning_context"}


def _planning_work(tmp_path: Path, *, current_first: bool = True) -> tuple[AgentRuntimeStore, WorkOrder, ContextPack]:
    store, work, context = _setup(tmp_path, CASES[0], count=0)
    work.task_inputs = {"scope": "Remaining image dependencies for all five groups."}
    turns = deepcopy(FIXTURE["turns"])
    if not current_first:
        turns.reverse()
    for sequence, turn in enumerate(turns, start=1):
        turn["sequence"] = sequence
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in turns]
    work.specialist_turns.append(_other_read(context, 3))
    store.save_work_order(work)
    return store, work, context


def _working(work: WorkOrder, context: ContextPack) -> dict[str, JsonValue]:
    return _specialist_working_set(work, [], project_id=context.project_id,
        read_tool_ids=PLANNER_READS, write_tool_ids=WRITES).model_dump(mode="json")


@pytest.mark.parametrize("current_first", [True, False])
def test_current_planning_shots_win_in_both_read_orders_and_survive_restart(tmp_path: Path, current_first: bool) -> None:
    store, work, context = _planning_work(tmp_path, current_first=current_first)
    saved = AgentRuntimeStore(store.database_path).get_work_order(work.id)
    assert saved is not None
    state = _working(saved, context)
    current = FIXTURE["turns"][0]["result"]["output"]
    historical = FIXTURE["turns"][1]["result"]["output"]
    entities = _shots(state)
    assert len(entities) == len(current["storyboard"]["shots"]) == 20
    for shot in current["storyboard"]["shots"]:
        entity = entities[shot["id"]]
        assert entity["shot"] == shot
        assert entity["source"] == "CURRENT_STORYBOARD_READ"
        assert entity["storyboard_identity"] == current["storyboard_identity"]
    assert entities["shot-yqq-03"]["shot"] != historical["storyboard"]["shots"][2]
    observations = cast(list[dict[str, JsonValue]], state["read_observations"])
    old_read = next(read for read in observations if read["tool_name"] == "artifact.get_asset_work_context")
    assert pointer_value(old_read, "/result/output/storyboard") == historical["storyboard"]


def test_actual_restarted_worker_sees_current_dialogue_beats_and_compact_read_contract(tmp_path: Path) -> None:
    store, work, context = _planning_work(tmp_path)
    tools = registry()
    for tool_name in sorted(PLANNER_READS - {"project.get", "artifact.read_current"}):
        tools.register(ToolSpec(id=tool_name, version=1,
            category=ToolCategory.SHOT if tool_name.startswith("shot.") else ToolCategory.ARTIFACT,
            description="Read the requested planning source.", effect=ToolEffect.READ, idempotent=True,
            input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="available"))))
    reopened = AgentRuntimeStore(store.database_path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id},
        "reason": "Continue planning with current Shot bodies."})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Prepare only the remaining images."))
    payload = client.payloads[0]
    working = cast(dict[str, JsonValue], payload["working_set"])
    assert payload["context_read"] == context_read_contract(project_id=context.project_id, run_id=context.run_id)
    assert "context_read" not in working
    shots = _shots(working)
    for expected in FIXTURE["turns"][0]["result"]["output"]["storyboard"]["shots"]:
        # The five-group task needs these current facts inline, not a new read.
        assert shots[expected["id"]]["shot"] == expected
    old_read = next(read for read in cast(list[dict[str, JsonValue]], working["read_observations"])
                    if read["tool_name"] == "artifact.get_asset_work_context")
    marker = cast(dict[str, JsonValue], pointer_value(old_read, "/result/output/storyboard/shots"))
    assert "read" not in marker and "note" not in marker
    snapshot = reopened.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, cast(str, marker["pointer"])) == FIXTURE["turns"][1]["result"]["output"]["storyboard"]["shots"]
    assert reopened.get_work_order(work.id) == saved


@pytest.mark.parametrize("known_revision", [True, False])
def test_current_planning_read_respects_target_write_and_can_refresh_it(tmp_path: Path, known_revision: bool) -> None:
    _store, work, context = _planning_work(tmp_path)
    current = deepcopy(FIXTURE["turns"][0])
    shot = current["result"]["output"]["storyboard"]["shots"][2]
    shot_id = shot["id"]
    work.specialist_turns.append(SpecialistTurnRecord(id="edit-current", sequence=4, tool_name="shot.save_current",
        arguments={"project_id": context.project_id, "shot_id": shot_id}, reason="Change this current Shot.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "artifact_refs": [
            {"project_id": context.project_id, "artifact_type": "shot", "artifact_id": shot_id,
             "version": 4 if known_revision else None}]}))
    assert shot_id not in _shots(_working(work, context))
    historical = SpecialistTurnRecord.model_validate(FIXTURE["turns"][1]).model_copy(update={"sequence": 5})
    work.specialist_turns.extend([historical, _other_read(context, 6)])
    assert shot_id not in _shots(_working(work, context))
    current["sequence"] = 7
    shot["revision"] = 4
    shot["audio_intent"] = "Actual newly saved timing."
    work.specialist_turns.append(SpecialistTurnRecord.model_validate(current))
    assert not any(entity.source_sequence == 7 and entity.kind == "shot"
        for entity in _specialist_working_set(work, [], project_id=context.project_id, read_tool_ids=PLANNER_READS).entities)
    work.specialist_turns.append(_other_read(context, 8))
    assert _shots(_working(work, context))[shot_id]["shot"] == shot
    assert "shot-yqq-01" in _shots(_working(work, context))


def test_compact_markers_survive_final_budget_without_rewriting_user_json(tmp_path: Path) -> None:
    store, work, context = _planning_work(tmp_path)
    raw_working = _working(work, context)
    original = deepcopy(raw_working)
    projected = project_retained_read_results(raw_working, store=store, project_id=context.project_id, work_order=work)
    user_value: dict[str, JsonValue] = {"context_ref": "user-literal", "pointer": "/keep", "read": {"note": "User's own JSON"}}
    payload: dict[str, JsonValue] = {"working_set": projected, "goal": user_value}
    prepared = ModelInputCompiler(store, budget=ContextBudget(window_tokens=8000, compact_at_tokens=6000,
        target_tokens=4000, envelope_tokens=100)).prepare(run_id=context.run_id, project_id=context.project_id,
            instructions="Preserve the current target.", payload=payload, max_output_tokens=1000)
    actual = cast(dict[str, JsonValue], json.loads(prepared.input_text))
    assert prepared.offloaded_blocks > 0
    assert actual["goal"] == user_value
    assert actual["context_read"] == projected["context_read"]
    assert "context_read" not in cast(dict[str, JsonValue], actual["working_set"])
    marker = cast(dict[str, JsonValue], pointer_value(actual, "/working_set/entities"))
    assert "read" not in marker and "context_ref" in marker
    snapshot = AgentRuntimeStore(store.database_path).get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, cast(str, marker["pointer"])) == projected["entities"]
    assert prepared.input_tokens == count_text_tokens("Preserve the current target.") + count_text_tokens(prepared.input_text)
    assert raw_working == original and payload["goal"] == user_value
    assert len(json_text(projected)) < len(json_text(original))
