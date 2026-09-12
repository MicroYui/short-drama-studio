"""The clean S03 saved revision must retire its original unreferenced read."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_scene_write_context import READS, WRITES, _other, _turn
from tests.test_studio_scene_semantic_revision import story  # noqa: F401


REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/current_shot_write_context.json").read_text())


@pytest.mark.parametrize("after_other_tool", [False, True])
def test_actual_s03_old_empty_cues_disappear_from_restarted_worker_input(tmp_path: Path, after_other_tool: bool) -> None:
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "work")
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = REAL["source"]["project_id"]
    work.specialist_turns = [SpecialistTurnRecord.model_validate(item) for item in REAL["turns"]]
    if after_other_tool:
        work.specialist_turns.append(_other(13))
    work.loaded_tool_schema_ids = ["project.get"]
    old_read = work.specialist_turns[2]
    assert old_read.result is not None
    assert not old_read.result["artifact_refs"]
    original = deepcopy(work.specialist_turns)
    store.save_work_order(work)
    store.save_context_snapshot("original-s03-read", work.parent_run_id, old_read.result)
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "project.get", "arguments": {"project_id": context.project_id},
        "reason": "Use the saved S03 and continue another scope."})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Prepare only the selected opening references."))
    state = cast(dict[str, Any], client.payloads[0]["working_set"])
    observations = [item for item in state["read_observations"] if item["tool_name"] == "artifact.read_current"]
    assert [item["source_sequence"] for item in observations] == [1, 2]
    for observation, original_read in zip(observations, REAL["turns"][:2], strict=True):
        assert observation["result"]["output"]["content"]["shot"] == original_read["result"]["output"]["content"]["shot"]
    expected = REAL["turns"][3]["result"]["output"]["saved_shot"]
    if after_other_tool:
        assert next(item["state"] for item in state["entities"] if item["kind"] == "saved_shot") == expected
    else:
        assert state["latest_handoff"]["result"]["receipt"]["current_state"]["saved_shot"] == expected
    assert expected["revision"] == 2
    assert not any(item["kind"] == "shot" and item["id"] == "shot-last-version-03" for item in state["entities"])
    assert reopened.get_context_snapshot("original-s03-read", work.parent_run_id) == old_read.result
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.specialist_turns == original


@pytest.mark.parametrize("failed", [False, True])
def test_real_failed_write_preserves_read_and_success_clears_only_target(tmp_path: Path, failed: bool) -> None:
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    turns = deepcopy(REAL["turns"])
    if failed:
        turns[-1]["result"]["status"] = "FAILED"
    work.specialist_turns = [SpecialistTurnRecord.model_validate(item) for item in turns] + [_other(13)]
    state = _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES)
    sequences = [item.source_sequence for item in state.read_observations if item.tool_name == "artifact.read_current"]
    assert sequences == ([1, 2, 3] if failed else [1, 2])


@pytest.mark.parametrize("write_kind", ["shot", "scene"])
def test_real_registry_combined_current_read_keeps_unrelated_selected_body(
    story: tuple[ShortDramaService, str], tmp_path: Path, write_kind: str,
) -> None:
    service, project_id = story
    document = service.get_project(project_id).document
    shot = document.shots[0]
    args: dict[str, JsonValue] = {"project_id": project_id, "scene_id": "reaction", "shot_id": shot.id}
    read = service.tool_registry.invoke("artifact.read_current", args)
    assert read.status == ToolCallStatus.SUCCEEDED
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    work.specialist_turns = [_turn("artifact.read_current", args, read, 1)]
    if write_kind == "shot":
        replacement = shot.model_copy(deep=True)
        replacement.camera = "The actual user's revised camera."
        writeargs: dict[str, JsonValue] = {"project_id": project_id, "shot_id": shot.id,
            "request": {"expected_revision": shot.revision, "shot": replacement.model_dump(mode="json")}}
        tool = "shot.save_current"
    else:
        writeargs = {"project_id": project_id, "request": {"scene_id": "reaction", "heading": "食堂队伍",
            "screenplay_text": "同学笑了。", "lines": [], "affected_shot_ids": []}}
        tool = "script.save_scene"
    saved = service.tool_registry.invoke(tool, writeargs)
    assert saved.status == ToolCallStatus.SUCCEEDED, saved.error
    work.specialist_turns.extend([_turn(tool, writeargs, saved, 2), _other(3)])
    state = _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES)
    observation = next(item for item in state.read_observations if item.tool_name == "artifact.read_current")
    content = cast(dict[str, Any], observation.result)["output"]["content"]
    expected_content = cast(dict[str, Any], read.output)["content"]
    retained = "scene" if write_kind == "shot" else "shot"
    removed = "shot" if write_kind == "shot" else "scene"
    assert content[retained] == expected_content[retained]
    assert removed not in content and f"{removed}_id" not in observation.request_identifiers
    assert work.specialist_turns[0].result == read.model_dump(mode="json")


def test_new_media_fields_keep_raw_and_final_candidate_ids_distinct() -> None:
    from short_drama.production_manager import _compact_image_task_state
    task: dict[str, JsonValue] = {"id": "task-1", "output_version_id": "masked-final", "output_version_number": 9,
        "provider_output_version_id": "provider-raw", "provider_output_version_number": 10}
    projected = _compact_image_task_state(task)
    assert {key: projected[key] for key in task} == task
