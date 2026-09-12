"""Replay saved partial deliveries through real Leader/Worker model inputs."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.media_handoffs import observed_media_handoffs
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.agent_runtime.models import DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.production_manager import LanguageProductionPlanner
from tests.test_agent_runtime import FakeArtifactResolver
from tests.test_completed_worker_handoff import worker_input
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work
from tests.test_leader_read_context import service_at


ROOM = "asset-version-4ef62d521b85"
FOLDER = "asset-version-abe658eaa803"
PEN = "asset-version-517b1b4894ce"


def saved_partial(store: AgentRuntimeStore, context: ContextPack) -> WorkOrder:
    fixture = json.loads((Path(__file__).parent / "fixtures/fresh2_partial_media_observations.json").read_text())
    work = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="asset_builder",
        objective="Prepare the room and remaining open-folder states", why_now="Independent image work")
    # Only project ownership is remapped. The real media, review scopes,
    # coordinates and persisted entry values remain unchanged.
    raw = json.dumps(fixture["turns"], ensure_ascii=False).replace(fixture["source_project_id"], context.project_id)
    work.specialist_turns = [SpecialistTurnRecord.model_validate(turn) for turn in json.loads(raw)]
    work.status = DelegationStatus.IN_PROGRESS
    store.save_work_order(work)
    return work


def leader_input(store: AgentRuntimeStore, context: ContextPack) -> dict[str, JsonValue]:
    client = ScriptedClient([json.dumps({"summary": "Model-input replay only", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(store)
    planner.propose_plan(context=ContextCompiler(store, FakeArtifactResolver([])).compile(context.run_id),
        skill_prompt="", method_skills=[], tools=registry().list(), current_plan=None,
        reason="A different Worker completed; check available partial deliveries")
    assert len(client.payloads) == 1
    return cast(dict[str, JsonValue], client.payloads[0]["working_set"])


def test_real_partial_qa_and_registration_reach_leader_before_work_completes_and_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, _receiver, context = runtime_work(path)
    source = saved_partial(store, context)
    original = source.model_dump(mode="json")
    payload = leader_input(AgentRuntimeStore(path), context)
    rows = cast(list[dict[str, JsonValue]], payload["media_observations"])
    assert len(rows) == 6
    assert {row["asset_version_id"] for row in rows} == {ROOM, FOLDER, PEN}
    assert all(row["work_order_status"] == "IN_PROGRESS" for row in rows)
    for row in rows:
        turn = next(turn for turn in source.specialist_turns if turn.id == row["attempt_id"])
        assert turn.result is not None
        if row["kind"] == "image_inspection":
            assert row["result"] == turn.result["output"]
            assert cast(dict[str, JsonValue], row["result"])["recommended"] is True
    assert "实际成人拉椅、转身净距不能由单张静帧精确验证" in json.dumps(rows, ensure_ascii=False)
    assert {ROOM, FOLDER, PEN} <= {cast(str, ref["artifact_id"]) for ref in cast(list[dict[str, JsonValue]], payload["artifact_refs"])}
    persisted = store.get_work_order(source.id)
    assert persisted is not None and persisted.model_dump(mode="json") == original
    assert leader_input(AgentRuntimeStore(path), context)["media_observations"] == rows


def test_receiving_worker_only_gets_related_media_and_exact_report_page_after_source_cancelled(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_partial(store, context)
    source.status = DelegationStatus.CANCELLED
    store.save_work_order(source)
    receiver.task_inputs = {"selected_media": [ROOM]}
    store.save_work_order(receiver)
    payload = worker_input(store, receiver, context)
    rows = cast(list[dict[str, JsonValue]], payload["media_observations"])
    assert len(rows) == 2
    assert FOLDER not in json.dumps(rows) and PEN not in json.dumps(rows)
    assert all(row["work_order_status"] == "CANCELLED" for row in rows)
    inspection = next(row for row in rows if row["kind"] == "image_inspection")
    marker = cast(dict[str, JsonValue], inspection["saved_observation"])
    read = cast(dict[str, JsonValue], marker["read"])
    assert read["tool_name"] == "agent.read_context"
    args = cast(dict[str, JsonValue], read["arguments"])
    assert args["project_id"] == context.project_id and args["run_id"] == context.run_id
    turn = next(turn for turn in source.specialist_turns if turn.id == inspection["attempt_id"])
    assert store.get_context_snapshot(cast(str, args["snapshot_id"]), context.run_id) == {
        "arguments": turn.arguments, "result": turn.result,
    }
    # A common project/plan and a prose mention do not select all saved media.
    receiver.task_inputs = {"plan_id": "common-plan", "note": f"Maybe consider {ROOM} later"}
    assert "media_observations" not in worker_input(store, receiver, context)


def test_inflight_inspection_is_visible_without_result_and_scopes_remain_independent(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_partial(store, context)
    first = source.specialist_turns[0]
    args = deepcopy(first.arguments)
    args["request"] = {"shot_id": "shot-eight", "canonical_reference_version_ids": ["lin-face"],
        "review_focus": "Chest-up including bag upper edge"}
    newer = first.model_copy(update={"id": "new-inspection", "sequence": 20, "arguments": args,
        "result": None, "status": SpecialistTurnStatus.IN_PROGRESS,
        "updated_at": first.updated_at + timedelta(minutes=15)})
    source.specialist_turns.append(newer)
    store.save_work_order(source)
    receiver.task_inputs = {"selected_media": [ROOM]}
    before = worker_input(store, receiver, context)
    rows = cast(list[dict[str, JsonValue]], before["media_observations"])
    pending = next(row for row in rows if row["attempt_id"] == newer.id)
    assert pending["status"] == "IN_PROGRESS" and pending["result"] is None
    assert "saved_observation" not in pending
    newer.status = SpecialistTurnStatus.COMPLETED
    newer.result = {"status": "SUCCEEDED", "output": {"recommended": False,
        "summary": "Identity is consistent; this framing cannot show the bag upper edge.",
        "issues": [{"code": "REQUIRED_PROP_DETAIL_NOT_VISIBLE"}],
        "crop_suggestion": {"box": [115, 184, 957, 1680]}}}
    store.save_work_order(source)
    after = worker_input(AgentRuntimeStore(tmp_path / "agent.sqlite3"), receiver, context)
    reviews = [row for row in cast(list[dict[str, JsonValue]], after["media_observations"]) if row["kind"] == "image_inspection"]
    assert len(reviews) == 2
    assert {cast(dict[str, JsonValue], row["result"])["recommended"] for row in reviews} == {True, False}
    assert any(cast(dict[str, JsonValue], row["result"]).get("crop_suggestion") == {"box": [115, 184, 957, 1680]} for row in reviews)


def test_latest_same_scope_updates_without_losing_other_scope_and_budget_keeps_exact_reads(tmp_path: Path) -> None:
    store, _receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_partial(store, context)
    first = source.specialist_turns[0]
    assert first.result is not None
    newer = first.model_copy(deep=True, update={"id": "updated-room-review", "sequence": 20,
        "updated_at": first.updated_at + timedelta(minutes=20)})
    source.specialist_turns.append(newer)
    store.save_work_order(source)
    rows, _refs = observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        selected_ids={ROOM}, token_budget=0)
    assert len(rows) == 2
    assert first.id not in json.dumps(rows)
    row = next(cast(dict[str, JsonValue], row) for row in rows if isinstance(row, dict) and row["attempt_id"] == newer.id)
    result = cast(dict[str, JsonValue], row["result"])
    snapshot = store.get_context_snapshot(cast(str, result["context_ref"]), context.run_id)
    assert snapshot == {"arguments": newer.arguments, "result": newer.result}
    assert observed_media_handoffs(store, run_id=context.run_id, project_id="another-project") == ([], [])
    receiver = AgentRuntime(store).create_run(project_id=context.project_id, objective="Independent run", root_agent="leader")
    assert observed_media_handoffs(store, run_id=receiver.id, project_id=context.project_id) == ([], [])


def test_failed_tool_and_self_observation_do_not_become_shared_success(tmp_path: Path) -> None:
    store, _receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    source = saved_partial(store, context)
    for turn in source.specialist_turns:
        assert turn.result is not None
        turn.result["status"] = "FAILED"
    store.save_work_order(source)
    assert observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id) == ([], [])
    source = saved_partial(store, context)
    assert observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        exclude_work_order_id=source.id) == ([], [])


def test_saved_observation_read_executes_through_existing_tool_after_restart(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Read an already inspected image", root_agent="leader")
    context = ContextPack(run_id=run.id, project_id=project_id, objective=run.objective)
    source = saved_partial(runtime.store, context)
    rows, _ = observed_media_handoffs(runtime.store, run_id=run.id, project_id=project_id,
        selected_ids={ROOM}, token_budget=0)
    row = next(cast(dict[str, JsonValue], item) for item in rows if isinstance(item, dict) and item["kind"] == "image_inspection")
    marker = cast(dict[str, JsonValue], row["saved_observation"])
    read = cast(dict[str, JsonValue], marker["read"])
    args = cast(dict[str, JsonValue], read["arguments"])
    resumed = service_at(tmp_path)
    # Read small real pages to prove the handoff's locator is executable, not
    # merely a tool name or an inaccessible cross-Worker ToolAttemptRef.
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
    turn = next(turn for turn in source.specialist_turns if turn.id == row["attempt_id"])
    assert json.loads("".join(pieces)) == {"arguments": turn.arguments, "result": turn.result}


def test_many_small_observations_remain_recoverable_with_whole_request_budget(tmp_path: Path) -> None:
    store, _receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    observations: list[JsonValue] = [{"kind": "image_inspection", "asset_version_id": f"image-{i}",
        "status": "IN_PROGRESS", "scope": {"review_focus": "这个画面是否能支持当前用途" * 5}}
        for i in range(400)]
    payload: dict[str, JsonValue] = {"goal": {"original_request": "完成视频前准备"},
        "working_set": {"media_observations": observations}}
    prepared = ModelInputCompiler(store, budget=ContextBudget(window_tokens=9000,
        compact_at_tokens=7000, target_tokens=4500, envelope_tokens=200)).prepare(
            run_id=context.run_id, project_id=context.project_id, instructions="根据实图判断",
            payload=payload, max_output_tokens=1000)
    actual = json.loads(prepared.input_text)
    assert prepared.input_tokens < 4500 and prepared.offloaded_blocks > 0
    assert actual["goal"] == payload["goal"]
    marker = actual["working_set"]["media_observations"]
    assert isinstance(marker, dict)
    assert store.get_context_snapshot(marker["context_ref"], context.run_id) == payload
