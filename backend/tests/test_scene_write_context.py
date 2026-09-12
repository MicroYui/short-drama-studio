"""Replay the clean Writer's actual save/read gap without a model or runtime write."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.production_manager import (
    AgentWorkOrderView, LanguageSpecialistAgent, _specialist_tool_result_for_persistence, _specialist_working_set,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolCallStatus, ToolResult
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_studio_scene_semantic_revision import story, _revision  # noqa: F401


REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/current_scene_write_context.json").read_text())
READS = frozenset({"artifact.read_current", "project.get", "agent.read_context"})
WRITES = frozenset({"script.save_scene", "script.revise_lines", "shot.save_current"})


def _turn(tool: str, arguments: dict[str, JsonValue], result: ToolResult, sequence: int) -> SpecialistTurnRecord:
    serialized = result.model_dump(mode="json")
    persisted = _specialist_tool_result_for_persistence(tool, serialized, arguments=arguments)
    assert persisted == serialized  # Same persistence adapter as actual Worker Tool execution.
    return SpecialistTurnRecord(id=f"actual-{sequence}", sequence=sequence, tool_name=tool,
        arguments=arguments, reason="Continue from the actual saved current content.",
        status=SpecialistTurnStatus.COMPLETED, result=persisted)


def _other(sequence: int) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"other-{sequence}", sequence=sequence, tool_name="project.get",
        arguments={"project_id": "test"}, reason="Read an unrelated fact.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"status": "available"}})


def _working(work: WorkOrder) -> dict[str, Any]:
    return _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode="json")


def _scenes(state: dict[str, Any]) -> dict[str, Any]:
    return {item["id"]: item["state"] for item in state["entities"] if item["kind"] == "scene"}


@pytest.mark.parametrize("after_other_tool", [False, True])
def test_real_writer_save_now_reaches_restarted_worker_without_read(tmp_path: Path, after_other_tool: bool) -> None:
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "work")
    project_id = service.initialize_fixture().document.project.id
    args = deepcopy(REAL["turns"][0]["arguments"])
    args["project_id"] = project_id  # Only the temporary project's address changes.
    result = service.tool_registry.invoke("script.save_scene", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    saved = cast(dict[str, Any], result.output)["saved_scenes"][0]
    expected = REAL["turns"][1]["result"]["output"]["content"]
    assert {k: v for k, v in saved["scene"].items() if k != "id"} == {k: v for k, v in expected["scene"].items() if k != "id"}
    assert saved["lines"] == expected["lines"] and len(saved["lines"]) == 9
    assert len(saved["scene"]["screenplay_text"]) == 623
    script = service.get_project(project_id).document.script_versions[-1]
    assert result.output["script_ref"] == {"project_id": project_id, "artifact_type": "script_version", "artifact_id": script.id, "version": script.version}
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = project_id
    work.production_skill_role = ProductionSkillRole.WRITER
    work.specialist_turns = [_turn("script.save_scene", args, result, 2)]
    if after_other_tool:
        work.specialist_turns.append(_other(3))
    work.loaded_tool_schema_ids = ["project.get"]
    store.save_work_order(work)
    original = work.model_dump(mode="json", exclude={"loaded_tool_schema_ids"})
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "project.get", "arguments": {"project_id": project_id},
        "reason": "The complete saved scene is already available; continue the task."})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Write the requested episode."))
    payload = cast(dict[str, Any], client.payloads[0])
    state = payload["working_set"]
    assert _scenes(state) == {saved["scene"]["id"]: {**saved, "script_ref": result.output["script_ref"]}}
    assert json.dumps(payload, ensure_ascii=False).count(json.dumps(saved["scene"]["screenplay_text"], ensure_ascii=False)) == 1
    assert not state["read_observations"]
    assert len(client.payloads) == 1
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == original


def test_actual_related_save_replaces_only_selected_scenes_and_preserves_saved_lines(story: tuple[ShortDramaService, str], tmp_path: Path) -> None:
    service, project_id = story
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    for seq, scene_id in enumerate(("setup", "reaction", "payoff"), 1):
        args: dict[str, JsonValue] = {"project_id": project_id, "scene_id": scene_id}
        work.specialist_turns.append(_turn("artifact.read_current", args, service.tool_registry.invoke("artifact.read_current", args), seq))
    before = deepcopy(work.specialist_turns)
    args = {"project_id": project_id, "request": _revision(service, project_id).model_dump(mode="json", exclude_unset=True)}
    result = service.tool_registry.invoke("script.save_scene", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    work.specialist_turns.extend([_turn("script.save_scene", args, result, 4), _other(5)])
    state = _working(work)
    assert set(_scenes(state)) == {"setup", "payoff"}
    assert _scenes(state)["payoff"]["lines"][0]["spoken_text"] == "谢谢。"  # lines were omitted, not erased.
    assert _scenes(state)["payoff"]["structure_stale"] is True
    observations = [item for item in state["read_observations"] if item["tool_name"] == "artifact.read_current"]
    assert [item["request_identifiers"]["scene_id"] for item in observations] == ["reaction"]
    assert observations[0]["result"]["output"]["content"]["scene"] == before[1].result["output"]["content"]["scene"]
    assert work.specialist_turns[:3] == before
    # A fresh current read becomes the single selected body; the write does not linger beside it.
    readargs: dict[str, JsonValue] = {"project_id": project_id, "scene_id": "payoff"}
    work.specialist_turns.extend([_turn("artifact.read_current", readargs, service.tool_registry.invoke("artifact.read_current", readargs), 6), _other(7)])
    assert "payoff" not in _scenes(_working(work))


def test_concurrent_failure_preserves_last_observed_fact_then_current_read_replaces_it(story: tuple[ShortDramaService, str], tmp_path: Path) -> None:
    service, project_id = story
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    args: dict[str, JsonValue] = {"project_id": project_id, "request": _revision(service, project_id).model_dump(mode="json", exclude_unset=True)}
    result = service.tool_registry.invoke("script.save_scene", args)
    work.specialist_turns = [_turn("script.save_scene", args, result, 1), _other(2)]
    original = deepcopy(_scenes(_working(work)))
    current = original["payoff"]["scene"]
    concurrent: dict[str, JsonValue] = {"project_id": project_id, "request": {"scene_id": "payoff", "heading": current["heading"],
        "screenplay_text": "用户改成另一段正文。", "lines": [], "affected_shot_ids": []}}
    assert service.tool_registry.invoke("script.save_scene", concurrent).status == ToolCallStatus.SUCCEEDED
    before = service.get_project(project_id).document
    stale = deepcopy(concurrent)
    stale["request"]["expected_text"] = current["screenplay_text"]
    stale["request"]["screenplay_text"] = "旧工作依据不得覆盖用户。"
    failed = service.tool_registry.invoke("script.save_scene", stale)
    assert failed.status == ToolCallStatus.FAILED
    assert service.get_project(project_id).document == before
    work.specialist_turns.extend([_turn("script.save_scene", stale, failed, 3), _other(4)])
    assert _scenes(_working(work)) == original
    readargs: dict[str, JsonValue] = {"project_id": project_id, "scene_id": "payoff"}
    read = service.tool_registry.invoke("artifact.read_current", readargs)
    work.specialist_turns.extend([_turn("artifact.read_current", readargs, read, 5), _other(6)])
    state = _working(work)
    assert set(_scenes(state)) == {"setup"}
    observation = next(item for item in state["read_observations"] if item["tool_name"] == "artifact.read_current")
    assert observation["result"]["output"]["content"]["scene"]["screenplay_text"] == "用户改成另一段正文。"


@pytest.mark.parametrize("legacy", [False, True])
def test_subsequent_scene_save_retires_previous_body_even_when_old_output_lacks_saved_content(story: tuple[ShortDramaService, str], tmp_path: Path, legacy: bool) -> None:
    service, project_id = story
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    args: dict[str, JsonValue] = {"project_id": project_id, "request": _revision(service, project_id).model_dump(mode="json", exclude_unset=True)}
    first = service.tool_registry.invoke("script.save_scene", args)
    work.specialist_turns = [_turn("script.save_scene", args, first, 1)]
    changed: dict[str, JsonValue] = {"project_id": project_id, "request": {"scene_id": "payoff", "heading": "食堂窗口",
        "screenplay_text": "下一次实际修改。", "lines": [], "affected_shot_ids": []}}
    latest = service.tool_registry.invoke("script.save_scene", changed)
    if legacy:
        latest.output.pop("saved_scenes", None)
    work.specialist_turns.extend([_turn("script.save_scene", changed, latest, 2), _other(3)])
    scenes = _scenes(_working(work))
    assert "setup" in scenes
    if legacy:
        assert "payoff" not in scenes
    else:
        assert scenes["payoff"]["scene"]["screenplay_text"] == "下一次实际修改。"


def test_large_saved_scene_is_exactly_page_readable_after_budget_offload_and_restart(story: tuple[ShortDramaService, str], tmp_path: Path) -> None:
    service, project_id = story
    runtime = service.agent_runtime
    store = runtime.store
    run = runtime.create_run(project_id=project_id, objective="Continue the saved scene.", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="writer",
        objective=run.objective, why_now="Actual saved content.")
    text = "按保存的真实场次逐段延续，不引入旧项目。\n" * 1100
    args: dict[str, JsonValue] = {"project_id": project_id, "request": {"scene_id": "setup", "heading": "食堂", "screenplay_text": text, "lines": [], "affected_shot_ids": []}}
    result = service.tool_registry.invoke("script.save_scene", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    work.specialist_turns = [_turn("script.save_scene", args, result, 1)]
    store.save_work_order(work)
    state = _working(work)
    prepared = ModelInputCompiler(store, budget=ContextBudget(window_tokens=8000, compact_at_tokens=4500,
        target_tokens=3000, envelope_tokens=100)).prepare(run_id=run.id, project_id=project_id,
        instructions="Use the saved scene.", payload={"working_set": state}, max_output_tokens=100)
    payload = json.loads(prepared.input_text)
    marker = payload["working_set"]["entities"]
    assert "context_ref" in marker and "context_read" in payload
    reopened = AgentRuntimeStore(store.database_path)
    snapshot = reopened.get_context_snapshot(marker["context_ref"], run.id)
    assert snapshot is not None
    entities = pointer_value(snapshot, marker["pointer"])
    assert entities == state["entities"]
    # Exercise the actual paginated Tool, not a reconstructed/truncated body.
    chunks: list[str] = []
    pointer = marker["pointer"] + "/0/state/scene/screenplay_text"
    offset: int | None = 0
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    while offset is not None:
        response = restarted.tool_registry.invoke("agent.read_context", {"project_id": project_id, "run_id": run.id,
            "snapshot_id": marker["context_ref"], "pointer": pointer, "offset": offset, "limit": 3000})
        assert response.status == ToolCallStatus.SUCCEEDED, response.error
        chunks.append(cast(str, response.output["content"]))
        offset = cast(int | None, response.output["next_offset"])
    assert "".join(chunks) == text and len(chunks) > 1
    assert prepared.input_tokens == count_text_tokens("Use the saved scene.") + count_text_tokens(prepared.input_text)
    assert cast(list[dict[str, Any]], entities)[0]["state"]["scene"]["screenplay_text"] == text


def test_original_write_trace_is_not_repaired_from_request_intent(tmp_path: Path) -> None:
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    work.specialist_turns = [SpecialistTurnRecord.model_validate(REAL["turns"][0]), _other(3)]
    assert not _scenes(_working(work))


def test_unchanged_save_returns_only_actual_selected_scene_without_new_version(story: tuple[ShortDramaService, str]) -> None:
    service, project_id = story
    before = service.get_project(project_id).document
    scene = before.script_versions[-1].structured_scenes[1]
    result = service.tool_registry.invoke("script.save_scene", {"project_id": project_id, "request": {
        "scene_id": scene.id, "heading": scene.heading, "screenplay_text": scene.screenplay_text, "expected_text": scene.screenplay_text}})
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output["changed_scene_ids"] == []
    assert result.output["saved_scenes"] == [{"scene": scene.model_dump(mode="json"), "lines": [], "structure_stale": False}]
    assert service.get_project(project_id).document == before


def test_unknown_whole_script_write_retires_current_scenes_but_keeps_explicit_snapshot_pages(story: tuple[ShortDramaService, str], tmp_path: Path) -> None:
    service, project_id = story
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    args: dict[str, JsonValue] = {"project_id": project_id, "request": _revision(service, project_id).model_dump(mode="json", exclude_unset=True)}
    result = service.tool_registry.invoke("script.save_scene", args)
    readargs: dict[str, JsonValue] = {"project_id": project_id, "scene_id": "reaction"}
    work.specialist_turns = [_turn("script.save_scene", args, result, 1),
        _turn("artifact.read_current", readargs, service.tool_registry.invoke("artifact.read_current", readargs), 2)]
    for seq, offset in ((3, 0), (4, 30)):
        work.specialist_turns.append(SpecialistTurnRecord(id=f"page-{seq}", sequence=seq, tool_name="agent.read_context",
            arguments={"project_id": project_id, "run_id": work.parent_run_id, "snapshot_id": "explicit-history", "pointer": "/scene", "offset": offset, "limit": 30},
            reason="Read the specifically requested historical snapshot page.", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"content": f"page-{seq}", "pointer": "/scene", "offset": offset}}))
    before_pages = deepcopy(work.specialist_turns[2:])
    work.specialist_turns.extend([SpecialistTurnRecord(id="whole-script", sequence=5, tool_name="script.revise_lines",
        arguments={"project_id": project_id}, reason="Replace the script without a selected scene delta.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {}, "artifact_refs": [{"project_id": project_id, "artifact_type": "script_version",
            "artifact_id": "actual-revised-script", "version": 99}]}), _other(6)])
    state = _working(work)
    assert not _scenes(state)
    assert [item["tool_name"] for item in state["read_observations"]] == ["agent.read_context", "agent.read_context"]
    assert [item["result"]["output"] for item in state["read_observations"]] == [item.result["output"] for item in before_pages]
    assert work.specialist_turns[2:4] == before_pages
