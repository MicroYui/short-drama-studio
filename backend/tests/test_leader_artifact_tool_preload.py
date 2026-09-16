"""Artifact-related discovery is schema availability, not execution or a fixed workflow."""
import json
from pathlib import Path

import pytest

from short_drama.agent_runtime import ActionKind, ActionStatus, AgentRuntimeStore, ArtifactRef, ArtifactSummary, ContextPack, WorkOrder, AgentResultStatus
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.tool_context import initial_tool_schema_ids
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest, LanguageProductionPlanner, _compact_specialist_tool_result
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from tests.test_harness_tool_context import ScriptedClient
from tests.test_storyboard_local_edits import _fixture

BOARD = {"shot.get_storyboard"}
GENERATION = {"route.get_generation_unit_context"}
H3 = {"route.list_h3_plans", "route.get_h3_plan"}
DOMAIN_WRITES = {"shot.patch_storyboard", "change.apply", "route.compile_generation_plan", "route.prepare_h3_group", "route.plan_h3"}


def plan(service, context, store=None, tools=None):
    client = ScriptedClient([json.dumps({"summary": "Inspect current saved inputs", "steps": [], "goal_complete": True})])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(store)
    planner.projects = service.repository
    planner.propose_plan(context=context, skill_prompt="", method_skills=[],
        tools=service.tool_registry.list() if tools is None else tools, current_plan=None, reason="Natural completion")
    assert len(client.payloads) == 1
    return client.payloads[0]


@pytest.mark.parametrize("kind,expected", [
    ("storyboard", BOARD), ("shot", BOARD), ("clip_group", BOARD),
    ("generation_plan", GENERATION), ("generation_unit", GENERATION),
    ("h3_route_plan", H3), ("h3_group_operation", H3),
    ("storyboard_draft", set()), ("asset_version", set()),
])
def test_actual_catalog_preloads_only_observed_artifact_domain(tmp_path, kind, expected):
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    ref = ArtifactRef(project_id=pid, artifact_type=kind, artifact_id="saved-domain-ref", version=1)
    context = ContextPack(run_id="read-only-planner", project_id=pid,
        objective="Unknown genre; no tool-name or workflow keywords", artifacts=[ArtifactSummary(ref=ref, title="Saved source", summary="Saved artifact summary")])
    payload = plan(service, context)
    schemas = payload["tool_schemas"]
    assert set(schemas) == set(initial_tool_schema_ids()) | expected
    catalog = {tool.id: tool for tool in service.tool_registry.list()}
    assert DOMAIN_WRITES <= {tool["id"] for tool in payload["available_tools"]}
    assert DOMAIN_WRITES.isdisjoint(schemas)
    for tool_id in expected:
        assert schemas[tool_id] == {"description": catalog[tool_id].description, "input_schema": catalog[tool_id].input_schema}
    assert service.agent_runtime_store.list_runs(project_id=pid) == []


def test_completed_saved_refs_join_context_index_and_survive_restart_with_current_contract(tmp_path):
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=pid, root_agent="leader", objective="Finish the user's preparation goal")
    board = ArtifactRef(project_id=pid, artifact_type="storyboard", artifact_id="persisted-board", version=2)
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="director", objective="Save board", why_now="Current task")
    runtime.record_agent_result(work.id, status=AgentResultStatus.COMPLETED, concise_summary="Saved storyboard address", updated_artifacts=[board])
    # Fresh6 had H3/generation refs in ContextPack but the formal storyboard only
    # in the observed working-set refs. Neither source alone includes all domains.
    context = ContextPack(run_id=run.id, project_id=pid, objective=run.objective, artifacts=[ArtifactSummary(ref=ArtifactRef(project_id=pid, artifact_type=kind, artifact_id=f"saved-{kind}", version=1), title="Saved domain", summary="Saved source") for kind in ("generation_unit", "h3_route_plan")])
    store = AgentRuntimeStore(tmp_path / "studio.sqlite3")
    payload = plan(service, context, store)
    expected = set(initial_tool_schema_ids()) | BOARD | GENERATION | H3
    assert set(payload["tool_schemas"]) == expected
    assert set(store.get_run(run.id).loaded_tool_schema_ids) == expected
    # Reopened run remembers IDs, while definitions come from the actual current catalog.
    descriptors = [tool.model_copy(update={"description": "Current changed definition"}) if tool.id == "route.get_h3_plan" else tool
                   for tool in service.tool_registry.list()]
    again = plan(service, context, AgentRuntimeStore(tmp_path / "studio.sqlite3"), descriptors)
    assert again["tool_schemas"]["route.get_h3_plan"]["description"] == "Current changed definition"


@pytest.mark.parametrize("case", ["foreign", "unresolved", "catalog_missing"])
def test_preload_does_not_invent_scope_or_unavailable_contracts(tmp_path, case):
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    ref = ArtifactRef(project_id="another-project" if case == "foreign" else pid,
        artifact_type="storyboard", artifact_id="saved-board", version=1)
    context = ContextPack(run_id="run", project_id=pid, objective="prepare H3, storyboard and generation plan",
        artifacts=[ArtifactSummary(ref=ref, title="Source", summary="Saved source")] if case != "unresolved" else [], unresolved_artifact_refs=[ref] if case == "unresolved" else [])
    tools = [tool for tool in service.tool_registry.list() if case != "catalog_missing" or tool.id not in BOARD]
    assert set(plan(service, context, tools=tools)["tool_schemas"]) == set(initial_tool_schema_ids())


@pytest.mark.parametrize("role", [ProductionSkillRole.ASSET_BUILDER, ProductionSkillRole.ASSET_VERIFIER])
def test_visual_roles_start_with_actual_image_view_schema(tmp_path, role):
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    work = WorkOrder(id="work", parent_run_id="run", sender="leader", recipient_role=role.value,
        production_skill_role=role, objective="Inspect relevant saved pixels", why_now="Current input")
    assert "image.view" in initial_tool_schema_ids(work)
    assert "image.view" in {tool.id for tool in service.tool_registry.list()}


def test_h3_current_source_checks_survive_list_and_get_projection():
    checks = [{"route_plan_id": "route", "state_revision": 2, "generation_unit_id": "unit", "shot_id": "shot",
        "checked_at": "2026-09-12T12:00:00Z", "status": "STALE", "scope": "SAVED_OPERATION_SOURCE", "reason": "Current shot changed"}]
    for tool in ("route.list_h3_plans", "route.get_h3_plan"):
        raw = {"status": "SUCCEEDED", "output": {"plans": [{"id": "route", "status": "READY", "decisions": []}], "source_checks": checks}}
        actual = _compact_specialist_tool_result(tool, raw)
        assert actual["output"]["source_checks"] == checks
        assert actual["output"]["plans"][0]["status"] == "READY"


def test_saved_storyboard_can_be_read_then_written_after_discovery_and_restart(tmp_path: Path) -> None:
    original, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    before = original.get_project(pid).document
    read_plan = {"summary": "Read the saved storyboard before the requested title edit", "steps": [{
        "id": "read-board", "title": "Read saved board", "kind": "TOOL_CALL",
        "assigned_agent": "production-manager", "tool_name": "shot.get_storyboard",
        "instructions": "Read this current storyboard before editing it.",
        "arguments": {"project_id": pid, "version_id": board.version.id},
    }]}
    title = "用户指定的新分镜标题"
    write_plan = {"summary": "Apply the requested title and preserve the shots", "goal_complete": True, "steps": [{
        "id": "write-title", "title": "Save requested title", "kind": "TOOL_CALL",
        "assigned_agent": "production-manager", "tool_name": "shot.patch_storyboard",
        "instructions": "Change only the title of the previously read storyboard.",
        "arguments": {"project_id": pid, "request": {
            "base_version_id": board.version.id, "title": title, "origin": "AGENT",
        }},
    }]}
    client = ScriptedClient([json.dumps(read_plan), json.dumps({
        "requested_tool_schema_ids": ["shot.patch_storyboard"],
        "rationale": "The current saved board has been read; the user requested a title change.",
    }), json.dumps(write_plan)])
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "work",
        language_provider_factory=lambda _key, _url: client)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    run = service.create_agent_run(pid, AgentRunCreateRequest(objective=f"Read the current storyboard, then change only its title to {title}."))
    first = client.payloads[0]
    assert "shot.get_storyboard" in first["tool_schemas"]
    assert DOMAIN_WRITES.isdisjoint(first["tool_schemas"])
    assert DOMAIN_WRITES <= {tool["id"] for tool in first["available_tools"]}
    read = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest(), allow_replanning=False)
    assert read.run.snapshot.actions[-1].status == ActionStatus.SUCCEEDED
    assert read.run.snapshot.actions[-1].tool_name == "shot.get_storyboard"
    service.replan_agent_run(run.snapshot.run.id, AgentRunReplanRequest(reason="The read completed; apply the user's title edit."))
    assert len(client.payloads) == 3
    assert "shot.patch_storyboard" not in client.payloads[1]["tool_schemas"]
    assert "shot.patch_storyboard" in client.payloads[2]["tool_schemas"]
    assert client.payloads[1]["available_tools"] == client.payloads[2]["available_tools"]

    # A fresh Service/Planner uses the remembered contract without another
    # discovery decision, then executes the real typed tool into the same DB.
    resumed_client = ScriptedClient([json.dumps(write_plan)])
    reopened = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "work",
        language_provider_factory=lambda _key, _url: resumed_client)
    reopened.replan_agent_run(run.snapshot.run.id, AgentRunReplanRequest(reason="Continue the prepared title edit after restart."))
    assert len(resumed_client.payloads) == 1
    assert "shot.patch_storyboard" in resumed_client.payloads[0]["tool_schemas"]
    saved = reopened.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest(), allow_replanning=False)
    assert saved.run.snapshot.actions[-1].status == ActionStatus.SUCCEEDED
    assert saved.run.snapshot.actions[-1].tool_name == "shot.patch_storyboard"
    assert reopened.get_storyboard(pid).storyboard.title == title
    after = reopened.get_project(pid).document
    assert after.shots == before.shots
    assert after.assets == before.assets
    assert after.editorial_timeline == before.editorial_timeline
