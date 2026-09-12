"""Actual WRITE catalog, not the old READ-only test registry, drives handoff."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from short_drama.agent_runtime import AgentRuntimeStore, ContextPack, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient
from tests.test_partial_storyboard_handoff import running_plan, saved_prefix
from tests.test_storyboard_local_edits import _request


def _worker_input(service: ShortDramaService, work: WorkOrder) -> dict[str, Any]:
    store = AgentRuntimeStore(service.agent_runtime_store.database_path)
    store.save_work_order(work)
    restored = store.get_work_order(work.id)
    assert restored is not None
    original = deepcopy(restored.specialist_turns)
    run = store.get_run(work.parent_run_id)
    assert run is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
        "arguments": {"project_id": run.project_id}, "reason": "Synthetic next decision; not executed."})])
    agent = LanguageSpecialistAgent(client, service.tool_registry)
    agent.context_compiler = ModelInputCompiler(store)
    agent.execute(AgentWorkOrderView(work_order=restored, context=ContextPack(
        run_id=run.id, project_id=run.project_id, objective=work.objective), system_prompt="Use the saved directory."))
    latest = store.get_work_order(work.id)
    assert latest is not None and latest.specialist_turns == original
    return cast(dict[str, Any], client.payloads[0])


def _read(service: ShortDramaService, pid: str, sequence: int) -> SpecialistTurnRecord:
    args = {"project_id": pid}
    call = service.tool_registry.invoke("artifact.read_current", args)
    assert call.status == ToolCallStatus.SUCCEEDED
    return SpecialistTurnRecord(id=f"read-{sequence}", sequence=sequence, tool_name="artifact.read_current",
        arguments=args, reason="A separate lightweight index read.", status=SpecialistTurnStatus.COMPLETED,
        result=call.model_dump(mode="json"))


@pytest.mark.parametrize("after_read", [False, True])
def test_fresh5_actual_finalize_directory_reaches_next_worker_after_restart(tmp_path: Path, after_read: bool) -> None:
    service, turns, _ = saved_prefix(tmp_path, through=8)
    _, work = running_plan(service, turns)
    receipt = cast(dict[str, Any], turns[-1].result)
    pid = cast(str, turns[-1].arguments["project_id"])
    if after_read:
        work.specialist_turns.append(_read(service, pid, 9))
    payload = _worker_input(service, work)
    state = payload["working_set"]
    if after_read:
        directory = next(row["state"] for row in state["entities"] if row["kind"] == "storyboard_directory")
    else:
        directory = state["latest_handoff"]["result"]["output"]
        assert not any(row["kind"] == "storyboard_directory" for row in state["entities"])
    for key in ("shot_directory", "clip_group_directory"):
        assert directory["storyboard_summary"][key] == receipt["output"]["storyboard"][key]
    assert directory["version"]["id"] == receipt["output"]["version"]["id"]
    assert len(directory["storyboard_summary"]["shot_directory"]) == 16
    assert len(directory["storyboard_summary"]["clip_group_directory"]) == 6
    assert "timeline_beats" not in json.dumps(directory)


def test_actual_local_shot_write_retires_old_directory_instead_of_relabelling_it(tmp_path: Path) -> None:
    service, turns, _ = saved_prefix(tmp_path, through=8)
    _, work = running_plan(service, turns)
    pid = cast(str, turns[-1].arguments["project_id"])
    document = service.repository.get_project(pid)
    assert document is not None
    shot = document.shots[0]
    changed = shot.model_copy(update={"camera": shot.camera + " Keep the saved spatial axis."})
    args = {"project_id": pid, "shot_id": shot.id,
            "request": {"expected_revision": shot.revision, "shot": changed.model_dump(mode="json")}}
    call = service.tool_registry.invoke("shot.save_current", args)
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    work.specialist_turns.extend([SpecialistTurnRecord(id="local-save", sequence=9, tool_name="shot.save_current",
        arguments=args, reason="Actual selected Shot edit", status=SpecialistTurnStatus.COMPLETED,
        result=call.model_dump(mode="json")), _read(service, pid, 10)])
    payload = _worker_input(service, work)
    assert not any(row["kind"] == "storyboard_directory" for row in payload["working_set"]["entities"])
    saved = next(row for row in payload["working_set"]["entities"] if row["kind"] == "saved_shot")
    assert saved["id"] == shot.id
    assert cast(dict[str, Any], work.specialist_turns[4].result)["output"]["version"]["version"] == 1


@pytest.mark.parametrize("case", ["failed_write", "other_shot", "newer_read", "legacy"])
def test_saved_directory_retirement_is_scoped_and_legacy_does_not_invent_fields(tmp_path: Path, case: str) -> None:
    service, turns, _ = saved_prefix(tmp_path, through=8)
    _, work = running_plan(service, turns)
    pid = cast(str, turns[-1].arguments["project_id"])
    raw = cast(dict[str, Any], deepcopy(turns[-1].result))
    if case == "legacy":
        raw["output"]["storyboard"].pop("shot_directory")
        raw["output"]["storyboard"].pop("clip_group_directory")
        work.specialist_turns[-1].result = raw
    else:
        if case == "newer_read":
            result = {"status": "SUCCEEDED", "output": {"document": raw["output"]["document"],
                "version": {"id": "observed-newer-board", "version": 2}}}
            name, arguments = "shot.get_storyboard", {"project_id": pid}
        else:
            result = {"status": "FAILED" if case == "failed_write" else "SUCCEEDED", "artifact_refs": [
                {"project_id": pid, "artifact_type": "shot", "artifact_id": "other-shot", "version": 2}]}
            name, arguments = "shot.save_current", {"project_id": pid, "shot_id": "other-shot"}
        work.specialist_turns.append(SpecialistTurnRecord(id=case, sequence=9, tool_name=name,
            arguments=arguments, reason="Adjacent projection boundary", status=SpecialistTurnStatus.COMPLETED,
            result=cast(dict[str, Any], result)))
    work.specialist_turns.append(_read(service, pid, 10))
    state = _worker_input(service, work)["working_set"]
    directories = [row for row in state["entities"] if row["kind"] == "storyboard_directory"]
    assert bool(directories) == (case in {"failed_write", "other_shot"})


def test_actual_split_write_replaces_directory_and_keeps_unchanged_group_members(tmp_path: Path) -> None:
    service, turns, _ = saved_prefix(tmp_path, through=8)
    _, work = running_plan(service, turns)
    pid = cast(str, turns[-1].arguments["project_id"])
    prior = service.get_storyboard(pid)
    args = {"project_id": pid, "request": _request(prior).model_dump(mode="json")}
    call = service.tool_registry.invoke("shot.patch_storyboard", args)
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    work.specialist_turns.extend([SpecialistTurnRecord(id="actual-split", sequence=9,
        tool_name="shot.patch_storyboard", arguments=args, reason="Split one existing Shot into two.",
        status=SpecialistTurnStatus.COMPLETED, result=call.model_dump(mode="json")), _read(service, pid, 10)])
    payload = _worker_input(service, work)
    directories = [row for row in payload["working_set"]["entities"] if row["kind"] == "storyboard_directory"]
    assert len(directories) == 1
    state = directories[0]["state"]
    actual = cast(dict[str, Any], call.output)
    assert state["version"]["id"] == actual["version"]["id"] != prior.version.id
    assert state["storyboard_summary"]["shot_directory"] == actual["storyboard"]["shot_directory"]
    assert len(state["storyboard_summary"]["shot_directory"]) == 17
    assert state["storyboard_summary"]["clip_group_directory"] == actual["storyboard"]["clip_group_directory"]
    assert [row["shot_ids"] for row in state["storyboard_summary"]["clip_group_directory"]][1:] == [
        group.shot_ids for group in prior.storyboard.clip_groups[1:]]
