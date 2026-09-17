"""Reuse a real saved ASR result while its video producer is still working."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.media_handoffs import observed_media_handoffs
from short_drama.agent_runtime.models import DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from tests.test_completed_worker_handoff import worker_input
from tests.test_harness_tool_context import runtime_work
from tests.test_leader_read_context import service_at
from tests.test_partial_media_handoff import leader_input


TAKE = "take-task-5ac81027515a4ebfaea0ef3047faa650"


def saved_transcription(store: AgentRuntimeStore, context: ContextPack) -> WorkOrder:
    fixture = json.loads((Path(__file__).parent / "fixtures/daily_film01_native_transcription.json").read_text())
    handoff = json.loads(json.dumps(fixture["handoff"], ensure_ascii=False).replace(
        fixture["source_project_id"], context.project_id))
    work = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="doorway_video",
        objective="Finish the ending; the doorway audio is already transcribed", why_now="Continue video production")
    work.specialist_turns = [SpecialistTurnRecord(
        id=handoff["attempt_ref"]["attempt_id"], sequence=handoff["sequence"],
        tool_name=handoff["request"]["tool_name"], arguments=handoff["request"]["arguments"],
        reason=handoff["request"]["reason"], status=SpecialistTurnStatus.COMPLETED, result=handoff["result"])]
    work.status = DelegationStatus.IN_PROGRESS
    store.save_work_order(work)
    return work


def test_real_transcript_reaches_leader_and_selected_worker_before_video_completes(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, receiver, context = runtime_work(path)
    source = saved_transcription(store, context)
    original = source.model_dump(mode="json")
    receiver.task_inputs = {"take_id": TAKE}
    store.save_work_order(receiver)
    expected = source.specialist_turns[0].result
    assert expected is not None
    for payload in (leader_input(AgentRuntimeStore(path), context), worker_input(AgentRuntimeStore(path), receiver, context)):
        rows = cast(list[dict[str, JsonValue]], payload["media_observations"])
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "native_transcription" and row["work_order_status"] == "IN_PROGRESS"
        assert row["take_ids"] == [TAKE] and row["shot_ids"] == ["shot-umbrella-04", "shot-umbrella-05", "shot-umbrella-06"]
        assert row["result"] == expected["output"]
        raw = json.dumps(row, ensure_ascii=False)
        assert "等等先拿着用吧" in raw and "TAKE_SOURCE" in raw and "4.32" in raw and "11.08" in raw
        assert "not speaker identification, lip-sync review or verified dialogue" in raw
    current = store.get_work_order(source.id)
    assert current is not None and current.model_dump(mode="json") == original


def test_precise_scope_and_failed_attempts_do_not_replace_saved_transcription(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_transcription(store, context)
    first = source.specialist_turns[0]
    second = first.model_copy(deep=True, update={"id": "different-audio-range", "sequence": 32})
    assert second.result is not None
    output = cast(dict[str, JsonValue], second.result["output"])
    output["report_path"] = "/saved/different-range/transcript.json"
    output["source_status"] = "HISTORICAL_OR_UNVERIFIED"
    cast(dict[str, JsonValue], second.arguments["request"])["source_start_seconds"] = 9.0
    cast(dict[str, JsonValue], output["source_context"])["source_start_seconds"] = 9.0
    window = cast(list[dict[str, JsonValue]], output["windows"])[0]
    window.update({"start_seconds": 9.0, "shot_ids": ["shot-umbrella-05", "shot-umbrella-06"],
        "transcript_text": "先拿着用吧"})
    window["characters"] = [character for character in cast(list[JsonValue], window["characters"])
        if isinstance(character, dict) and isinstance(start := character.get("start_seconds"), (int, float)) and start >= 9.0]
    # A different observed range remains independently readable, including its
    # historical limitation. A failed retry cannot overwrite either success.
    third = first.model_copy(deep=True, update={"id": "failed-retry", "sequence": 33,
        "status": SpecialistTurnStatus.COMPLETED, "result": {"status": "FAILED", "output": None}})
    source.specialist_turns.extend((second, third))
    source.status = DelegationStatus.CANCELLED
    store.save_work_order(source)
    rows, _ = observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id, selected_ids={TAKE})
    assert len(rows) == 2
    assert {cast(dict[str, JsonValue], row)["attempt_id"] for row in rows} == {first.id, second.id}
    assert "HISTORICAL_OR_UNVERIFIED" in json.dumps(rows)
    for selected in ({"another-take"}, {context.project_id}, {"whole-film-timeline"}):
        assert observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id, selected_ids=selected) == ([], [])
    receiver.task_inputs = {"shot_id": "shot-umbrella-05"}
    assert "media_observations" in worker_input(store, receiver, context)
    receiver.task_inputs = {"note": f"An unselected prose mention: {TAKE}"}
    assert "media_observations" not in worker_input(store, receiver, context)


def test_transcript_original_is_readable_via_existing_context_tool_after_restart_and_offload(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Reuse doorway speech for captions", root_agent="leader")
    context = ContextPack(run_id=run.id, project_id=project_id, objective=run.objective)
    source = saved_transcription(runtime.store, context)
    rows, _ = observed_media_handoffs(runtime.store, run_id=run.id, project_id=project_id,
        selected_ids={TAKE}, token_budget=0)
    assert len(rows) == 1
    row = cast(dict[str, JsonValue], rows[0])
    assert row["take_ids"] == [TAKE]
    assert "scope" not in row
    read = cast(dict[str, JsonValue], cast(dict[str, JsonValue], row["saved_observation"])["read"])
    args = cast(dict[str, JsonValue], read["arguments"])
    resumed = service_at(tmp_path)
    pieces: list[str] = []
    offset = 0
    while True:
        result = resumed.production_manager.tools.invoke("agent.read_context", {**args, "offset": offset, "limit": 1000})
        assert result.status.value == "SUCCEEDED" and isinstance(result.output, dict)
        pieces.append(cast(str, result.output["content"]))
        next_offset = result.output["next_offset"]
        if next_offset is None:
            break
        assert isinstance(next_offset, int)
        offset = next_offset
    turn = source.specialist_turns[0]
    assert json.loads("".join(pieces)) == {"arguments": turn.arguments, "result": turn.result}


def test_editorial_transcript_keeps_film_coordinates_and_captured_revision(tmp_path: Path) -> None:
    store, _receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_transcription(store, context)
    turn = source.specialist_turns[0]
    assert turn.result is not None
    output = deepcopy(cast(dict[str, JsonValue], turn.result["output"]))
    output.update({"observation_scope": "EDITORIAL", "time_space": "FILM", "source_editorial_revision": 16,
        "editorial_timeline_id": "whole-film-timeline", "source_context": {}, "source_status": None})
    windows = cast(list[dict[str, JsonValue]], output["windows"])
    windows[0]["clip_ids"] = ["current-doorway-clip"]
    turn.arguments["request"] = {"clip_ids": ["current-doorway-clip"], "expected_editorial_revision": 16}
    turn.result["output"] = output
    store.save_work_order(source)
    rows, _ = observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        selected_ids={"current-doorway-clip"})
    assert len(rows) == 1 and cast(dict[str, JsonValue], rows[0])["result"] == output
    assert observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        selected_ids={"whole-film-timeline"}) == ([], [])
