"""Replay the third clean run's lost plate metadata through actual Worker input."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore, WorkOrder
from short_drama.agent_runtime.asset_version_context import asset_construction_state
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import ArtifactRef, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from tests.test_harness_tool_context import ScriptedClient, runtime_work


REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/fresh3_asset_construction_context.json").read_text())
READS = frozenset({"project.get", "image.capability"})
WRITES = frozenset({"image.compose_text_plate", "image.composite_plane"})
PLATE_ID = "asset-version-b004f0cdde1e"


def _state(work: WorkOrder) -> dict[str, Any]:
    return _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode="json")


def _other(sequence: int) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"other-{sequence}", sequence=sequence, tool_name="image.capability", arguments={},
        reason="Read a separate capability", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"available": True}})


def _work(path: Path, sequences: tuple[int, ...]) -> tuple[AgentRuntimeStore, WorkOrder]:
    store, work, _context = runtime_work(path)
    work.production_skill_role = ProductionSkillRole.ASSET_BUILDER
    work.task_inputs = deepcopy(REAL["task_inputs"])
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in REAL["turns"] if turn["sequence"] in sequences]
    return store, work


@pytest.mark.parametrize("after_other", [False, True])
def test_real_plate_layout_and_qa_coordinates_survive_restart_in_actual_model_input(tmp_path: Path, after_other: bool) -> None:
    store, work = _work(tmp_path / "agent.sqlite3", (1, 2))
    if after_other:
        work.specialist_turns.append(_other(3))
    work.loaded_tool_schema_ids = ["image.capability"]
    store.save_work_order(work)
    original = work.model_dump(mode="json", exclude={"loaded_tool_schema_ids"})
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "image.capability", "arguments": {}, "reason": "Continue with known layout."})])
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "work")
    agent = LanguageSpecialistAgent(client, service.tool_registry)
    agent.context_compiler = ModelInputCompiler(reopened)
    _s, _w, context = runtime_work(tmp_path / "other.sqlite3")
    context.run_id = work.parent_run_id
    context.project_id = REAL["provenance"]["project_id"]
    agent.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Use saved local metadata."))
    actual = cast(dict[str, Any], client.payloads[0])["working_set"]
    version = next(e["state"] for e in actual["entities"] if e["kind"] == "asset_version" and e["id"] == PLATE_ID)
    expected = REAL["turns"][0]["result"]["output"]["version"]
    for field in ("descriptor", "text_plate", "derivation_references"):
        assert version[field] == expected[field]
    assert version["text_plate"]["text_blocks"][0]["font_size"] == 240
    assert version["text_plate"]["lines"][0]["start"] == [0.28, 0.78]
    qa = REAL["turns"][1]["result"]["output"]
    assert qa["summary"] in json.dumps(actual, ensure_ascii=False).replace('\\n', '\n').replace('\\"', '"')
    assert len(client.payloads) == 1
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == original


@pytest.mark.parametrize("selected_by", ["own_write", "scope", "task_input"])
@pytest.mark.parametrize("after_other", [False, True])
def test_real_project_read_keeps_selected_noncurrent_draft_once_without_other_history(
    tmp_path: Path, selected_by: str, after_other: bool,
) -> None:
    _store, work = _work(tmp_path / "agent.sqlite3", (1, 4) if selected_by == "own_write" else (4,))
    work.task_inputs = {}
    if selected_by == "scope":
        work = WorkOrder.model_validate({**work.model_dump(mode="json"), "context_manifest": None,
            "scope": [ArtifactRef(project_id=REAL["provenance"]["project_id"], artifact_type="asset_version", artifact_id=PLATE_ID, version=2).model_dump(mode="json")]})
    elif selected_by == "task_input":
        work.task_inputs = {"selected_media": [{"asset_version_id": PLATE_ID}]}
    read = work.specialist_turns[-1]
    assert read.result is not None
    doc = cast(dict[str, Any], read.result["output"])["document"]
    asset = doc["assets"][0]
    extra = deepcopy(asset["versions"][-1])
    extra["id"] = "unrelated-old-draft"
    extra["descriptor"] = "THIS HISTORY MUST NOT ENTER THE MODEL"
    asset["versions"].append(extra)
    before = work.model_dump(mode="json")
    if after_other:
        work.specialist_turns.append(_other(5))
    actual = _state(work)
    read_result = actual["read_observations"][0]["result"] if after_other else actual["latest_handoff"]["result"]
    visible = read_result["output"]["document"]["assets"][0]
    assert visible["current_version_id"] != PLATE_ID and visible["approved_version_id"] is None
    versions = {v["id"]: v for v in visible["versions"]}
    assert set(versions) == {asset["current_version_id"], PLATE_ID}
    assert versions[PLATE_ID]["text_plate"] == REAL["turns"][0]["result"]["output"]["version"]["text_plate"]
    assert versions[PLATE_ID]["approval_status"] == "DRAFT"
    assert not any(e["kind"] == "asset_version" and e["id"] == PLATE_ID for e in actual["entities"])
    assert "THIS HISTORY MUST NOT ENTER THE MODEL" not in json.dumps(actual)
    assert work.specialist_turns[:len(before["specialist_turns"])] == [SpecialistTurnRecord.model_validate(t) for t in before["specialist_turns"]]


def test_local_plane_retains_actual_recipe_and_derivation_without_external_prompt(tmp_path: Path) -> None:
    _store, work = _work(tmp_path / "agent.sqlite3", (1, 7))
    version = REAL["turns"][-1]["result"]["output"]["version"]
    state = next(e["state"] for e in _state(work)["entities"] if e["id"] == version["id"])
    assert state["local_operation"] == json.loads(version["source"]["prompt"])
    layer = state["local_operation"]["layers"][0]
    assert layer["source_quad"] == layer["target_quad"]
    assert state["derivation_references"] == version["derivation_references"]
    assert "prompt" not in state["source"]
    external = deepcopy(version)
    external["source"].update(source_type="GENERATED", provider="gpt-image-2")
    assert "local_operation" not in asset_construction_state(external)
    crop = {"source_asset_version_id": PLATE_ID, "source_width": 1680, "source_height": 2376, "box_pixels": [20, 30, 1000, 900]}
    sheet = {"columns": 2, "cells": [{"source_version_id": PLATE_ID, "label": "原始"}]}
    assert asset_construction_state({"image_crop": crop, "reference_sheet": sheet}) == {"image_crop": crop, "reference_sheet": sheet}


def test_construction_metadata_offloads_losslessly_under_existing_context_budget(tmp_path: Path) -> None:
    store, work = _work(tmp_path / "agent.sqlite3", (1, 7))
    payload: dict[str, JsonValue] = {"working_set": _state(work)}
    compiler = ModelInputCompiler(store, budget=ContextBudget(window_tokens=20000, compact_at_tokens=3500, target_tokens=2500))
    result = compiler.prepare(run_id=work.parent_run_id, project_id=REAL["provenance"]["project_id"],
        instructions="Keep the original goal and selected artifact identity.", payload=payload, max_output_tokens=1000, force_compact=True)
    assert result.offloaded_blocks > 0
    prepared = json.loads(result.input_text)
    marker = prepared["working_set"]["entities"]
    assert isinstance(marker, dict) and "context_ref" in marker
    snapshot = store.get_context_snapshot(marker["context_ref"], work.parent_run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, marker["pointer"]) == cast(dict[str, JsonValue], payload["working_set"])["entities"]
