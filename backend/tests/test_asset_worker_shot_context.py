"""Two real asset-worker rereads reproduced with bounded durable Shot entities."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.tool_context import project_retained_read_results
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionSkillRole
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry


CASES: list[dict[str, Any]] = json.loads((Path(__file__).parent / "fixtures/asset_worker_shot_reads.json").read_text())["cases"]
READS = frozenset({"artifact.get_asset_work_context", "artifact.read_current", "project.get", "shot.preview_reference_plan"})
WRITES = frozenset({"artifact.add_reference_entry", "artifact.patch_asset_plan", "shot.save_current"})


def _setup(tmp_path: Path, case: dict[str, Any], count: int = 3) -> tuple[AgentRuntimeStore, WorkOrder, ContextPack]:
    store = AgentRuntimeStore(tmp_path / "agent.sqlite3")
    runtime = AgentRuntime(store)
    project_id = str(case["turns"][0]["arguments"]["project_id"])
    run = runtime.create_run(project_id=project_id, objective="Prepare only this character's selected reference.", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="asset-builder",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER, objective=run.objective,
        why_now="Current selected asset task.", task_inputs={"shot_ids": [case["shot_id"]]})
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in case["turns"][:count]]
    store.save_work_order(work)
    return store, work, ContextPack(run_id=run.id, project_id=project_id, objective=run.objective)


def _state(work: WorkOrder, context: ContextPack) -> dict[str, JsonValue]:
    return _specialist_working_set(work, [], project_id=context.project_id,
        read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode="json")


def _shots(value: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    return {str(entity["id"]): cast(dict[str, JsonValue], entity["state"])
        for entity in cast(list[dict[str, JsonValue]], value["entities"]) if entity["kind"] == "shot"}


def _other_read(context: ContextPack, sequence: int) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"other-{sequence}", sequence=sequence, tool_name="project.get",
        arguments={"project_id": context.project_id}, reason="Read an unrelated small fact.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"status": "available"}})


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["missing_shot_call"]))
@pytest.mark.parametrize("count", [2, 3])
def test_each_unrelated_write_retains_exact_real_shot_in_restarted_worker_input(
    tmp_path: Path, case: dict[str, Any], count: int,
) -> None:
    store, work, context = _setup(tmp_path, case, count)
    expected = case["turns"][3]["result"]["output"]["content"]["shot"]
    assert expected == case["turns"][0]["result"]["output"]["storyboard"]["shots"][0]
    tools = registry()
    for tool_name in sorted((READS | WRITES) - {"project.get", "artifact.read_current"}):
        tools.register(ToolSpec(id=tool_name, version=1,
            category=ToolCategory.SHOT if tool_name.startswith("shot.") else ToolCategory.ARTIFACT,
            description="Use the existing typed selected-object operation.",
            effect=ToolEffect.WRITE if tool_name in WRITES else ToolEffect.READ,
            idempotent=True, input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="available"))))
    reopened = AgentRuntimeStore(store.database_path)
    # The real work-context was large and had a durable read snapshot. This
    # reduced fixture omits unrelated bodies but retains that same read path.
    original_read = work.specialist_turns[0]
    assert original_read.result is not None
    reopened.save_context_snapshot(f"context-read-{work.id}-{original_read.sequence}", work.parent_run_id, original_read.result)
    saved = reopened.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
        "arguments": {"project_id": context.project_id}, "reason": "Continue with the retained selected Shot."})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Prepare this bounded image task."))
    working = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    state = _shots(working)[case["shot_id"]]
    body = state["shot"]
    if isinstance(body, dict) and "context_ref" in body:
        snapshot = reopened.get_context_snapshot(cast(str, body["context_ref"]), context.run_id)
        assert snapshot is not None
        assert pointer_value(snapshot, cast(str, body["pointer"])) == expected
    else:
        assert body == expected
    assert state["source"] == "ASSET_PLAN_STORYBOARD_SOURCE"
    assert state["storyboard_identity"] == case["turns"][0]["result"]["output"]["storyboard"]["identity"]
    ref = cast(dict[str, JsonValue], state["artifact_ref"])
    assert ref["version"] == expected["revision"] and ref["artifact_id"] == expected["id"]
    if count == 3:
        assert not any(read["tool_name"] == "artifact.get_asset_work_context"
            for read in cast(list[dict[str, JsonValue]], working["read_observations"]))
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.specialist_turns == work.specialist_turns


@pytest.mark.parametrize("known_revision", [True, False])
def test_shot_write_invalidates_only_its_body_and_old_plan_read_cannot_restore_it(tmp_path: Path, known_revision: bool) -> None:
    case = CASES[0]
    store, work, context = _setup(tmp_path, case)
    other = deepcopy(CASES[1]["turns"][0])
    other["sequence"] = 18
    work.specialist_turns.append(SpecialistTurnRecord.model_validate(other))
    old_shot = deepcopy(case["turns"][0]["result"]["output"]["storyboard"]["shots"][0])
    write = SpecialistTurnRecord(id="actual-shot-edit", sequence=19, tool_name="shot.save_current",
        arguments={"project_id": context.project_id, "shot_id": case["shot_id"]}, reason="Change this Shot's actual references.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"changed_shot_ids": [case["shot_id"]]},
            "artifact_refs": [{"project_id": context.project_id, "artifact_type": "shot", "artifact_id": case["shot_id"],
                               "version": 2 if known_revision else None}]})
    work.specialist_turns.append(write)
    after = _shots(_state(work, context))
    assert case["shot_id"] not in after and CASES[1]["shot_id"] in after
    reread = SpecialistTurnRecord.model_validate(case["turns"][0]).model_copy(update={"id": "old-source-reread", "sequence": 20})
    work.specialist_turns.extend([reread, _other_read(context, 21)])
    assert case["shot_id"] not in _shots(_state(work, context))
    refreshed = deepcopy(case["turns"][3])
    refreshed["id"], refreshed["sequence"] = "current-after-edit", 22
    refreshed["result"]["output"]["content"]["shot"]["revision"] = 2
    refreshed["result"]["output"]["content"]["shot"]["description"] = "The actual edited shot body."
    for ref in refreshed["result"]["artifact_refs"]:
        if ref["artifact_type"] == "shot":
            ref["version"] = 2
    work.specialist_turns.extend([SpecialistTurnRecord.model_validate(refreshed), _other_read(context, 23)])
    store.save_work_order(work)
    saved = AgentRuntimeStore(store.database_path).get_work_order(work.id)
    assert saved is not None
    latest = _shots(_state(saved, context))
    assert latest[case["shot_id"]]["shot"] == refreshed["result"]["output"]["content"]["shot"]
    assert latest[case["shot_id"]]["source"] == "CURRENT_SHOT_READ"
    assert latest[case["shot_id"]]["storyboard_identity"] is None
    assert latest[CASES[1]["shot_id"]]["shot"] == other["result"]["output"]["storyboard"]["shots"][0]
    assert old_shot != latest[case["shot_id"]]["shot"]


def test_visible_read_shares_only_equal_shot_body_and_keeps_exact_source(tmp_path: Path) -> None:
    case = CASES[0]
    store, work, context = _setup(tmp_path, case, 1)
    immediate = _state(work, context)
    assert not _shots(immediate)  # Full body already arrives once in latest_handoff.
    work.specialist_turns.append(_other_read(context, 15))
    working = _state(work, context)
    read = work.specialist_turns[0]
    assert read.result is not None
    snapshot_id = f"context-read-{work.id}-{read.sequence}"
    store.save_context_snapshot(snapshot_id, work.parent_run_id, read.result)
    original = deepcopy(working)
    projected = project_retained_read_results(working, store=store, project_id=context.project_id, work_order=work)
    state = _shots(projected)[case["shot_id"]]
    marker = cast(dict[str, JsonValue], state["shot"])
    assert marker["pointer"] == "/output/storyboard/shots/0"
    assert state["storyboard_identity"] == _shots(original)[case["shot_id"]]["storyboard_identity"]
    snapshot = store.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, cast(str, marker["pointer"])) == _shots(original)[case["shot_id"]]["shot"]
    assert working == original
    changed = deepcopy(original)
    cast(dict[str, JsonValue], _shots(changed)[case["shot_id"]]["shot"])["revision"] = 999
    different = project_retained_read_results(changed, store=store, project_id=context.project_id, work_order=work)
    assert _shots(different)[case["shot_id"]]["shot"] == _shots(changed)[case["shot_id"]]["shot"]


@pytest.mark.parametrize("current_first", [True, False])
def test_older_pinned_source_never_overwrites_a_newer_current_read(tmp_path: Path, current_first: bool) -> None:
    case = CASES[0]
    _store, work, context = _setup(tmp_path, case, 1)
    source = deepcopy(case["turns"][0])
    current = deepcopy(case["turns"][3])
    current["result"]["output"]["content"]["shot"]["revision"] = 2
    current["result"]["output"]["content"]["shot"]["description"] = "A newer user edit already read from current content."
    for ref in current["result"]["artifact_refs"]:
        if ref["artifact_type"] == "shot":
            ref["version"] = 2
    ordered = [current, source] if current_first else [source, current]
    for sequence, turn in enumerate(ordered, start=1):
        turn["sequence"] = sequence
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in ordered]
    work.specialist_turns.append(_other_read(context, 3))
    state = _shots(_state(work, context))[case["shot_id"]]
    assert state["shot"] == current["result"]["output"]["content"]["shot"]
    assert state["source"] == "CURRENT_SHOT_READ" and state["storyboard_identity"] is None


@pytest.mark.parametrize("returns_preview", [True, False])
def test_saved_shot_preview_replaces_old_one_without_reviving_shot_body(tmp_path: Path, returns_preview: bool) -> None:
    case = CASES[0]
    _store, work, context = _setup(tmp_path, case)
    old_preview = {"shot_id": case["shot_id"], "shot_revision": 1, "selected_media": ["old"]}
    new_preview = {"shot_id": case["shot_id"], "shot_revision": 2, "selected_media": ["actual-saved"]}
    work.specialist_turns.append(SpecialistTurnRecord(id="old-preview", sequence=18, tool_name="shot.preview_reference_plan",
        arguments={"project_id": context.project_id, "shot_id": case["shot_id"]}, reason="Read current reference selection.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": old_preview}))
    work.specialist_turns.append(SpecialistTurnRecord(id="save-with-preview", sequence=19, tool_name="shot.save_current",
        arguments={"project_id": context.project_id, "shot_id": case["shot_id"]}, reason="Save current selection.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED",
            "output": {"reference_plan_preview": new_preview if returns_preview else None,
                       "reference_plan_preview_error": None if returns_preview else "Missing selected media"},
            "artifact_refs": [{"project_id": context.project_id, "artifact_type": "shot", "artifact_id": case["shot_id"], "version": 2}]}))
    work.specialist_turns.append(_other_read(context, 20))
    working = _state(work, context)
    assert case["shot_id"] not in _shots(working)
    previews = [entity["state"] for entity in cast(list[dict[str, JsonValue]], working["entities"])
        if entity["kind"] == "reference_plan_preview"]
    assert previews == ([new_preview] if returns_preview else [])
