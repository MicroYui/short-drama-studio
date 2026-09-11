"""Leader reads survive real Tool execution, replanning and process recreation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import ActionKind, AgentRuntime, AgentRuntimeStore, ArtifactRef, PlanStep, ProjectMemory
from short_drama.agent_runtime.leader_context import leader_read_working_set, persist_leader_tool_result
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.models import ProductionDocumentCreateRequest, ProductionDocumentOrigin
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest, LanguageProductionPlanner,
    _specialist_read_observation_key, _specialist_read_observation_result,
)
from short_drama.service import ShortDramaService
from short_drama.tools.agent import AgentReadContextInput
from tests.test_harness_tool_context import ScriptedClient


def service_at(root: Path) -> ShortDramaService:
    return ShortDramaService(database_path=root / "db.sqlite3", workspace_root=root / "workspace")


def read_step(name: str, tool: str, arguments: dict[str, JsonValue]) -> PlanStep:
    return PlanStep(id=name, title=name, kind=ActionKind.TOOL_CALL,
        assigned_agent="production-manager", tool_name=tool, arguments=arguments,
        instructions="Read the selected current content.")


def working(service: ShortDramaService, run_id: str, project_id: str, *, budget: int = 32000) -> dict[str, JsonValue]:
    return leader_read_working_set(service.production_manager.runtime.store,
        run_id=run_id, project_id=project_id, tools=service.production_manager.tools.list(),
        read_key=_specialist_read_observation_key, project_result=_specialist_read_observation_result,
        token_budget=budget)


def test_real_registry_leader_scene_and_document_survive_capability_replan_and_restart(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    scene = script.structured_scenes[0]
    # Match the observed incident: long ambient input and metadata came before
    # the selected scene/payload, beyond both old 1600/1000-character cuts.
    document.project.input_text = "A long original whole-film brief. " * 150
    scene.screenplay_text = "A shootable scene with cause and effect. " * 90 + "SCENE_END_SIGN_TOGETHER"
    service.repository.save_project(document)
    old_refs: list[ArtifactRef] = []
    for index in range(20):
        item = service.create_production_document(project_id, ProductionDocumentCreateRequest(
            kind="notes", title=f"Old document {index}", payload={"note": "Old context"},
            origin=ProductionDocumentOrigin.AGENT))
        old_refs.append(ArtifactRef(project_id=project_id, artifact_type="production_document",
            artifact_id=item.document.id))
    board = service.create_production_document(project_id, ProductionDocumentCreateRequest(
        kind="continuity_notes", title="Current board", payload={"shots": [
            {"shot_id": f"shot-{i}", "description": "Show the exact document before the action. " * 15,
             "end_state": f"SHOT_{i}_EXACT_END_STATE"} for i in range(14)]},
        origin=ProductionDocumentOrigin.AGENT))
    view = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective="Review current scene and board before video generation.", initial_plan=AgentPlanProposal(
            summary="Read the current facts.", steps=[
                read_step("scene", "artifact.read_current", {"project_id": project_id, "scene_id": scene.id}),
                read_step("board", "artifact.get_production_document", {"project_id": project_id,
                    "production_document_id": board.document.id}),
            ])))
    run_id = view.snapshot.run.id
    runtime = service.production_manager.runtime
    runtime.store.save_project_memory(ProjectMemory(project_id=project_id, summary="Old index", artifact_refs=old_refs))
    service.production_manager.advance(run_id, AgentRunAdvanceRequest(max_actions=2), allow_replanning=False)
    actions = runtime.store.list_actions(run_id)
    assert all(action.tool_result_context_ref for action in actions)
    assert all("SCENE_END_SIGN_TOGETHER" not in (action.output_summary or "") for action in actions)
    runtime.revise_plan(run_id, summary="Inspect actual capability.", steps=[
        read_step("capability", "route.h3_capability_profile", {"project_id": project_id})])
    service.production_manager.advance(run_id, AgentRunAdvanceRequest(), allow_replanning=False)
    # Later non-read actions must not turn read retention into a last-N window.
    for index in range(10):
        action = runtime.begin_action(run_id, kind=ActionKind.CHECKPOINT, agent_id="leader", input_summary=f"Checkpoint {index}")
        runtime.complete_action(action.id, output_summary="Progress recorded")
    resumed = service_at(tmp_path)
    resumed_runtime = resumed.production_manager.runtime
    context = resumed_runtime.context(run_id)
    assert board.document.id in {artifact.ref.artifact_id for artifact in context.artifacts}
    client = ScriptedClient([json.dumps({"summary": "Known current facts are sufficient.", "goal_complete": True, "steps": []})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(resumed_runtime.store)
    planner.propose_plan(context=context, skill_prompt="", method_skills=[], tools=resumed.production_manager.tools.list(),
        current_plan=None, reason="Continue after a process restart.")
    payload = client.payloads[0]
    observations = cast(list[dict[str, JsonValue]], cast(dict[str, JsonValue], payload["working_set"])["read_observations"])
    assert len(observations) == 3
    scene_read = next(row for row in observations if row["tool_name"] == "artifact.read_current")
    board_read = next(row for row in observations if row["tool_name"] == "artifact.get_production_document")
    assert "SCENE_END_SIGN_TOGETHER" in json.dumps(scene_read)
    assert "SHOT_13_EXACT_END_STATE" in json.dumps(board_read)
    assert "A long original whole-film brief" not in json.dumps(scene_read)
    assert len(client.payloads) == 1  # No second discovery/read round to get either body.
    ref = cast(dict[str, JsonValue], board_read["saved_result"])
    snapshot_id = cast(str, ref["context_ref"])
    # Existing agent.read_context exposes the original unprojected Tool result.
    raw = resumed.production_manager.read_context(AgentReadContextInput(project_id=project_id, run_id=run_id,
        snapshot_id=snapshot_id, pointer="/result/output/version/payload/shots/13/end_state"))
    assert raw.content == "SHOT_13_EXACT_END_STATE"


def test_read_scope_replaces_only_same_object_and_retains_pages_and_views(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Compare selected content", root_agent="leader")
    reads: list[tuple[str, dict[str, JsonValue], str]] = [
        ("artifact.read_current", {"scene_id": "scene-a"}, "old-a"),
        ("artifact.read_current", {"scene_id": "scene-b"}, "keep-b"),
        ("artifact.read_current", {"scene_id": "scene-a", "shot_id": None}, "new-a"),
        ("artifact.get_production_document", {"production_document_id": "doc-a", "version_id": "old"}, "old-doc"),
        ("artifact.get_production_document", {"production_document_id": "doc-a"}, "new-doc"),
        ("timeline.read_editorial", {"shot_id": "shot-a"}, "old-default-view"),
        ("timeline.read_editorial", {"shot_id": "shot-a", "include_project": False}, "default-view"),
        ("timeline.read_editorial", {"shot_id": "shot-a", "include_project": True}, "full-view"),
        ("agent.read_context", {"run_id": run.id, "snapshot_id": "source", "pointer": "/body"}, "old-page-one"),
        ("agent.read_context", {"run_id": run.id, "snapshot_id": "source", "pointer": "/body", "offset": 0, "limit": 6000}, "page-one"),
        ("agent.read_context", {"run_id": run.id, "snapshot_id": "source", "pointer": "/body", "offset": 6000}, "page-two"),
    ]
    for tool, args, marker in reads:
        action = runtime.begin_action(run.id, kind=ActionKind.TOOL_CALL, agent_id="leader", tool_name=tool, input_summary="Read")
        output: dict[str, JsonValue] = ({"document": {"id": "doc-a"},
            "version": {"id": marker, "payload": {"marker": marker}}}
            if tool == "artifact.get_production_document" else {"marker": marker})
        persist_leader_tool_result(runtime.store, action, arguments={"project_id": project_id, **args},
            result={"status": "SUCCEEDED", "output": output})
        runtime.complete_action(action.id, output_summary="Read completed")
    observed = json.dumps(working(service, run.id, project_id))
    for marker in ("old-a", "old-doc", "old-default-view", "old-page-one"):
        assert marker not in observed
    for marker in ("keep-b", "new-a", "new-doc", "default-view", "full-view", "page-one", "page-two"):
        assert marker in observed


def test_budget_prefers_recent_read_bodies_and_every_older_body_remains_exactly_pageable(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Compare selected content", root_agent="leader")
    body = "完整原文与不同字段。" * 2000
    for index, content in enumerate((body, "RECENT_SELECTED_SCENE")):
        action = runtime.begin_action(run.id, kind=ActionKind.TOOL_CALL, agent_id="leader",
            tool_name="artifact.read_current", input_summary="Read")
        persist_leader_tool_result(runtime.store, action, arguments={"project_id": project_id, "scene_id": f"scene-{index}"},
            result={"status": "SUCCEEDED", "output": {"content": {"scene": {"screenplay_text": content}}}})
        runtime.complete_action(action.id, output_summary="Shortened read summary")
    observation = cast(list[dict[str, JsonValue]], working(service, run.id, project_id, budget=200)["read_observations"])
    assert "RECENT_SELECTED_SCENE" in json.dumps(observation[0]["result"])
    old = cast(dict[str, JsonValue], observation[1]["result"])
    assert "context_ref" in old and body not in json.dumps(old)
    # Reopen before reading both pages through the actual registered Tool.
    resumed = service_at(tmp_path)
    chunks: list[str] = []
    offset = 0
    while True:
        page = resumed.production_manager.read_context(AgentReadContextInput(project_id=project_id, run_id=run.id,
            snapshot_id=cast(str, old["context_ref"]), pointer="/result/output/content/scene/screenplay_text",
            offset=offset, limit=6000))
        chunks.append(page.content)
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert "".join(chunks) == body
    context = resumed.production_manager.runtime.context(run.id)
    assert all(action.tool_result_ref is not None for action in context.recent_actions)
    assert "saved_result" in json.dumps(working(resumed, run.id, project_id))


def test_historical_actions_do_not_claim_truncated_text_is_recoverable(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = AgentRuntime(AgentRuntimeStore(service.repository.database_path))
    run = runtime.create_run(project_id=project_id, objective="Resume historical task", root_agent="leader")
    action = runtime.begin_action(run.id, kind=ActionKind.TOOL_CALL, agent_id="leader", tool_name="artifact.read_current", input_summary="Read")
    runtime.complete_action(action.id, output_summary='{"content": "historical truncated...')
    assert working(service, run.id, project_id)["read_observations"] == []


def test_visible_read_reference_outside_sixteen_summaries_can_be_delegated_without_reread(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Delegate one previously inspected document", root_agent="leader")
    refs: list[ArtifactRef] = []
    for index in range(17):
        document = service.create_production_document(project_id, ProductionDocumentCreateRequest(
            kind="notes", title=f"Selected document {index}", payload={"finding": f"READ_{index}"},
            origin=ProductionDocumentOrigin.AGENT))
        ref = ArtifactRef(project_id=project_id, artifact_type="production_document", artifact_id=document.document.id,
            version=document.version.version)
        refs.append(ref)
        action = runtime.begin_action(run.id, kind=ActionKind.TOOL_CALL, agent_id="leader",
            tool_name="artifact.get_production_document", input_summary="Read", artifact_refs=[ref])
        persist_leader_tool_result(runtime.store, action,
            arguments={"project_id": project_id, "production_document_id": document.document.id},
            result={"status": "SUCCEEDED", "artifact_refs": [ref.model_dump(mode="json")], "output": {
                "document": {"id": document.document.id}, "version": {"payload": {"finding": f"READ_{index}"}}}})
        runtime.complete_action(action.id, output_summary="Read completed")
    for index in range(9):
        action = runtime.begin_action(run.id, kind=ActionKind.CHECKPOINT, agent_id="leader", input_summary=f"Progress {index}")
        runtime.complete_action(action.id, output_summary="Progress recorded")
    context = runtime.context(run.id)
    assert refs[0].artifact_id not in {item.ref.artifact_id for item in context.artifacts}
    client = ScriptedClient([json.dumps({"summary": "Delegate the earlier inspected document", "steps": [{
        "id": "review", "title": "Review selected content", "kind": "DELEGATION",
        "assigned_agent": "director", "production_skill_role": "DIRECTOR",
        "instructions": "Review the known selected content.", "artifact_refs": [refs[0].model_dump(mode="json")]}]})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(runtime.store)
    result = planner.propose_plan(context=context, skill_prompt="", method_skills=[],
        tools=service.production_manager.tools.list(), current_plan=None, reason="Delegate known facts")
    assert result.steps[0].artifact_refs == [refs[0]]
    assert len(client.payloads) == 1
    # The actual Worker handoff still resolves against the canonical repository.
    order = runtime.create_work_order(run.id, sender="leader", recipient_role="director", objective="Review",
        why_now="Known read content", scope=[refs[0]])
    assert runtime.work_order_context(order.id).artifacts[0].ref == refs[0]
