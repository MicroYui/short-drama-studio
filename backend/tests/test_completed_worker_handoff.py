"""Completed media decisions survive rolling action windows and scoped handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import ActionKind, AgentRuntime, AgentRuntimeStore, ArtifactRef, ContextPack, WorkOrder
from short_drama.agent_runtime.leader_context import completed_work_handoffs
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import AgentResult, AgentResultStatus, DelegationStatus
from short_drama.production_manager import AgentWorkOrderView, LanguageProductionPlanner, LanguageSpecialistAgent
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work
from tests.test_agent_runtime import FakeArtifactResolver


def completed(store: AgentRuntimeStore, run_id: str, project_id: str, name: str = "paper") -> tuple[WorkOrder, AgentResult]:
    work = AgentRuntime(store).create_work_order(run_id, sender="leader", recipient_role="asset_builder",
        objective=f"Prepare {name} physical references", why_now="Required media")
    result = AgentResult(id=f"result-{name}", work_order_id=work.id, status=AgentResultStatus.COMPLETED,
        concise_summary=f"{name}: blank and signed photographs were inspected and selected; remain usable DRAFT.",
        deliverable={"state_image_map": [
            {"asset_id": f"asset-{name}", "state": "blank", "asset_version_id": f"{name}-blank", "selection": "selected after visual inspection"},
            {"asset_id": f"asset-{name}", "state": "signed", "asset_version_id": f"{name}-signed", "selection": "selected after visual inspection"},
        ], "limitations": [{"message": "Static inspection does not prove dynamic handwriting."}]},
        findings=["The signed photograph contains the exact required name."],
        created_artifacts=[ArtifactRef(project_id=project_id, artifact_type="asset_version", artifact_id=f"{name}-{state}", version=i)
            for i, state in enumerate(("blank", "signed"), 1)])
    result.completion_receipts = [{"artifact_ref": ref.model_dump(mode="json"), "record_verified": True,
        "current_state": {"approval_status": "DRAFT", "freshness": "CURRENT"}} for ref in result.created_artifacts]
    store.save_agent_result(result)
    return work, result


def test_actual_planner_retains_completed_delivery_after_actions_replan_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, _new_worker, context = runtime_work(path)
    work, result = completed(store, context.run_id, context.project_id)
    runtime = AgentRuntime(store)
    delegation = runtime.begin_action(context.run_id, kind=ActionKind.DELEGATION, agent_id="leader",
        input_summary="Prepare physical paper states", work_order_id=work.id)
    runtime.complete_action(delegation.id, output_summary=result.concise_summary)
    assert result.concise_summary in json.dumps(ContextCompiler(store, FakeArtifactResolver([])).compile(context.run_id).model_context_dump())
    for i in range(12):
        action = runtime.begin_action(context.run_id, kind=ActionKind.CHECKPOINT, agent_id="leader", input_summary=f"Later action {i}")
        runtime.complete_action(action.id, output_summary="Another current action")
    runtime.revise_plan(context.run_id, summary="Prepare remaining composition", steps=[])
    reopened = AgentRuntimeStore(path)
    client = ScriptedClient([json.dumps({"summary": "Continue from actual completed deliveries.", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(reopened)
    planner.propose_plan(context=ContextCompiler(reopened, FakeArtifactResolver([])).compile(context.run_id), skill_prompt="", method_skills=[],
        tools=registry().list(), current_plan=None, reason="Continue after restart")
    payload = client.payloads[0]
    assert result.concise_summary not in json.dumps(payload["context"])
    working_set = cast(dict[str, JsonValue], payload["working_set"])
    handoffs = cast(list[dict[str, JsonValue]], working_set["completion_handoffs"])
    assert len(handoffs) == 1
    assert handoffs[0]["work_order_id"] == work.id and handoffs[0]["status"] == "COMPLETED"
    assert "state_image_map" in json.dumps(handoffs[0]["result"])
    assert "paper-signed" in json.dumps(working_set["artifact_refs"])
    assert len(client.payloads) == 1


def worker_input(store: AgentRuntimeStore, work: WorkOrder, context: ContextPack) -> dict[str, JsonValue]:
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Inspect required current content"})])
    agent = LanguageSpecialistAgent(client, registry())
    agent.context_compiler = ModelInputCompiler(store)
    agent.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Use completed media observations; do not generate video."))
    assert len(client.payloads) == 1
    return cast(dict[str, JsonValue], client.payloads[0]["working_set"])


def test_running_worker_refreshes_only_related_completed_media_without_new_message(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work.task_inputs = {"selected_media": ["paper-signed"]}
    store.save_work_order(work)
    assert "completion_handoffs" not in worker_input(store, work, context)
    completed(store, context.run_id, context.project_id)
    completed(store, context.run_id, context.project_id, "unrelated-room")
    payload = worker_input(store, work, context)
    handoffs = cast(list[dict[str, JsonValue]], payload["completion_handoffs"])
    assert len(handoffs) == 1
    view = json.dumps(handoffs[0]["result"])
    assert "paper-signed" in view and "paper-blank" not in view and "unrelated-room" not in view
    assert "Static inspection does not prove dynamic handwriting." in view
    assert "COMPLETED" in json.dumps(handoffs)
    assert "paper-signed" in json.dumps(payload["artifact_refs"])
    assert "paper-blank" not in json.dumps(payload["artifact_refs"])
    # A common plan or a prose mention must not expand a Worker's scope to all work.
    work.task_inputs = {"hint": "please consider paper-signed later"}
    work = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="director",
        objective="Read the shared plan", why_now="Another task", task_inputs=work.task_inputs,
        scope=[ArtifactRef(project_id=context.project_id, artifact_type="asset_plan", artifact_id="common-plan", version=1)])
    assert "completion_handoffs" not in worker_input(store, work, context)


def test_real_paper_completion_reaches_actual_receiving_worker_task_inputs(tmp_path: Path) -> None:
    source = json.loads((Path(__file__).parent / "fixtures" / "completed_paper_handoff.json").read_text())
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    paper = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="asset_builder",
        objective="Prepare the physical paper states", why_now="Incident replay")
    result = AgentResult.model_validate(source["result"])
    result.work_order_id = paper.id
    for ref in [*result.created_artifacts, *result.updated_artifacts]:
        ref.project_id = context.project_id
    store.save_agent_result(result)
    receiver.task_inputs = source["task_inputs"]
    before = receiver.model_dump(mode="json")
    payload = worker_input(store, receiver, context)
    rows = cast(list[dict[str, JsonValue]], payload["completion_handoffs"])
    assert len(rows) == 1 and rows[0]["status"] == "COMPLETED"
    delivery = cast(dict[str, JsonValue], cast(dict[str, JsonValue], rows[0]["result"])["deliverable"])
    assert delivery["state_image_map"] == result.deliverable["state_image_map"]
    assert rows[0]["concise_summary"] == result.concise_summary
    # Discovery metadata may be saved; source instructions and media selections are unchanged.
    assert receiver.task_inputs == before["task_inputs"]


def test_shared_plan_artifact_aliases_cannot_trigger_media_completion_handoff(tmp_path: Path) -> None:
    store, _worker, context = runtime_work(tmp_path / "agent.sqlite3")
    _paper, result = completed(store, context.run_id, context.project_id)
    aliases = [ArtifactRef(project_id=context.project_id, artifact_type=kind, artifact_id=identifier, version=2)
        for kind, identifier in [("asset_plan", "common-plan"), ("production_document", "common-plan"),
            ("production_document_version", "common-plan-v2"), ("bible_version", "common-bible-v1")]]
    result.updated_artifacts = aliases
    result.deliverable["saved_plan_refs"] = [ref.model_dump(mode="json") for ref in aliases]
    store.save_agent_result(result)
    receiver = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="director",
        objective="Prepare a different object's composition", why_now="Current work", scope=aliases,
        task_inputs={"selected_plan_refs": [ref.model_dump(mode="json") for ref in aliases]})
    assert "completion_handoffs" not in worker_input(store, receiver, context)
    rows, refs = completed_work_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        selected_ids={ref.artifact_id for ref in aliases})
    assert rows == [] and refs == []


def test_multiple_completion_bodies_remain_exactly_pageable_and_stable_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, _work, context = runtime_work(path)
    _paper, result = completed(store, context.run_id, context.project_id)
    completed(store, context.run_id, context.project_id, "room")
    rows, refs = completed_work_handoffs(store, run_id=context.run_id, project_id=context.project_id, token_budget=0)
    assert len(rows) == 2 and len(refs) == 4
    row = next(cast(dict[str, JsonValue], value) for value in rows if isinstance(value, dict) and value["result_id"] == result.id)
    marker = cast(dict[str, JsonValue], row["result"])
    read = cast(dict[str, JsonValue], marker["read"])
    assert read["tool_name"] == "agent.read_context"
    snapshot = cast(str, marker["context_ref"])
    reopened = AgentRuntimeStore(path)
    assert reopened.get_context_snapshot(snapshot, context.run_id) == {"result": result.model_dump(mode="json")}
    again, _ = completed_work_handoffs(reopened, run_id=context.run_id, project_id=context.project_id, token_budget=0)
    assert again == rows


@pytest.mark.parametrize("invalid", ["cancelled", "stale", "partial", "other-run"])
def test_completed_inventory_never_revives_noncurrent_or_unrelated_work(tmp_path: Path, invalid: str) -> None:
    store, _worker, context = runtime_work(tmp_path / "agent.sqlite3")
    work, result = completed(store, context.run_id, context.project_id)
    if invalid == "cancelled":
        work = store.get_work_order(work.id)
        assert work is not None
        work.status = DelegationStatus.CANCELLED
        store.save_work_order(work)
    elif invalid == "stale":
        result = result.model_copy(update={"stale_handoff": True, "stale_reason": "The source changed"})
        store.save_agent_result(result)
    elif invalid == "partial":
        store.save_agent_result(AgentResult(id="later-partial", work_order_id=work.id,
            status=AgentResultStatus.PARTIAL, concise_summary="More work is explicitly required."))
    else:
        context.run_id = AgentRuntime(store).create_run(project_id=context.project_id,
            objective="An independent task", root_agent="leader").id
    rows, refs = completed_work_handoffs(store, run_id=context.run_id, project_id=context.project_id)
    assert rows == [] and refs == []
