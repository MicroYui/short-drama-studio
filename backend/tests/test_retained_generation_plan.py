"""Older plan directories keep selected criteria and exact unselected bodies."""
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime.model_input import json_text, pointer_value
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.tool_context import project_retained_read_results
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.production_manager import _specialist_working_set
from tests.test_harness_tool_context import runtime_work


def _read() -> dict[str, JsonValue]:
    return {"tool_name": "route.get_generation_plan", "source_sequence": 1,
        "request_identifiers": {"project_id": "project-test"}, "result": {
            "status": "SUCCEEDED", "artifact_refs": [], "output": {
                "version": {"id": "plan-v3", "version": 3},
                "generation_plan": {"project_id": "project-test", "shot_ids": ["opening", "ending"],
                    "units": [{"id": name, "clip_group_id": name, "shot_ids": [name],
                        "requested_duration_seconds": 8, "shot_windows": [{"shot_id": name, "start_seconds": 0, "end_seconds": 8}],
                        "generation_intent": name + " actual comparison criteria. " * 70,
                        "reference_notes": {"hearing": "UNKNOWN", "source_version": "source-v3"}}
                        for name in ("opening", "ending")]}}}}


def test_retained_plan_keeps_selected_body_and_resolves_other_after_reopen(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    work.task_inputs = {"generation_unit_id": "ending"}
    original: dict[str, JsonValue] = {"read_observations": [_read()],
        "latest_handoff": {"request": {"arguments": {"take_id": "ending-take"}},
            "result": {"status": "SUCCEEDED", "output": {"report": {"summary": "Current observation", "audio_semantics_observed": False}}}}}
    saved = deepcopy(original)
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    prefix = "/read_observations/0/result/output/generation_plan/units"
    assert pointer_value(projected, prefix + "/1") == pointer_value(original, prefix + "/1")
    omitted = cast(dict[str, JsonValue], pointer_value(projected, prefix + "/0"))
    assert omitted["id"] == "opening" and omitted["shot_windows"] == pointer_value(original, prefix + "/0/shot_windows")
    marker = cast(dict[str, JsonValue], omitted["saved_content"])
    reopened = AgentRuntimeStore(store.database_path)
    snapshot = reopened.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, cast(str, marker["pointer"])) == pointer_value(original, prefix + "/0")
    assert projected["latest_handoff"] == saved["latest_handoff"]
    assert pointer_value(projected, "/read_observations/0/result/output/version") == {"id": "plan-v3", "version": 3}
    assert count_text_tokens(json_text(projected)) < count_text_tokens(json_text(original))
    assert original == saved


@pytest.mark.parametrize("selected", [["opening", "ending"], ["different-unit"]])
def test_whole_plan_or_unresolved_selection_retains_comparison_prose(tmp_path: Path, selected: list[str]) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    work.task_inputs = {"shot_ids": cast(JsonValue, selected)}
    working: dict[str, JsonValue] = {"read_observations": [_read()]}
    assert project_retained_read_results(working, store=store, project_id=context.project_id, work_order=work) == working


@pytest.mark.parametrize("tool", ["route.prepare_h3_group", "route.compile_h3_prompt", "video.select_take"])
def test_picture_audio_binding_survives_checkpoint_and_latest_result(tool: str) -> None:
    args: dict[str, JsonValue] = {"project_id": "project", "source_in_seconds": 2, "source_out_seconds": 4}
    target: dict[str, JsonValue] = {"mode": "PICTURE_ONLY", "audio_policy": "KEEP_CURRENT_AUDIO", "timeline_id": "film",
        "clip_id": "current-picture", "expected_editorial_revision": 7}
    if tool == "video.select_take":
        args.update({"take_id": "candidate", "clip_ids": ["current-picture"], "audio_policy": "KEEP_CURRENT_AUDIO", "expected_editorial_revision": 7})
    else:
        args["picture_only"] = target
    work = WorkOrder(id="editor", parent_run_id="run", sender="leader", recipient_role="editor", objective="Replace picture", why_now="Saved audio stays")
    work.specialist_turns = [SpecialistTurnRecord(id="selection", sequence=1, tool_name=tool, arguments=args,
        reason="Use the bound operation", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": tool, "status": "SUCCEEDED", "output": {}, "receipt": {"current_state": {}}})]
    original = work.model_dump_json()
    current = _specialist_working_set(WorkOrder.model_validate_json(original), [], project_id="project",
        read_tool_ids=frozenset(), write_tool_ids=frozenset({tool}))
    checkpoint = next(item for item in current.tool_checkpoints if item.tool_name == tool)
    key = "audio_policy" if tool == "video.select_take" else "picture_only"
    assert checkpoint.request_identifiers[key] == args[key]
    assert current.latest_handoff is not None
    assert pointer_value(current.latest_handoff, "/request/arguments/" + key) == args[key]
    assert work.model_dump_json() == original
