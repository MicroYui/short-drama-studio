"""Regressions for durable discovery and scoped normal model working sets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import AgentResult, AgentResultStatus, DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.tool_context import normalized_read_arguments, project_retained_read_results
from short_drama.production_manager import AgentWorkOrderView, LanguageProductionPlanner, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolSpec
from short_drama.tools.agent import AgentReadContextInput, AgentReadContextOutput, register_context_tools
from short_drama.tools.registry import ToolRegistry
from short_drama.tools.studio import CurrentReadOutput, TimelineReadInput


class ScriptedClient:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = iter(outputs)
        self.payloads: list[dict[str, JsonValue]] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        self.payloads.append(json.loads(input_text))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return value


class ReadInput(ToolModel):
    project_id: str


class ReadOutput(ToolModel):
    status: str


def registry(version: int = 1) -> ToolRegistry:
    tools = ToolRegistry()
    for tool_id in ("project.get", "artifact.read_current", "agent.read_context"):
        tools.register(ToolSpec(
            id=tool_id, version=version, category=ToolCategory(tool_id.split(".")[0]),
            description=f"Current contract {version}", effect=ToolEffect.READ,
            idempotent=True, input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="available")),
        ))
    return tools


def runtime_work(path: Path) -> tuple[AgentRuntimeStore, WorkOrder, ContextPack]:
    store = AgentRuntimeStore(path)
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id="project-test", objective="Review the selected shot.", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="director",
        objective="Review the selected shot.", why_now="Current task",
        production_skill_role=ProductionSkillRole.DIRECTOR,
        production_prompt_stage=ProductionPromptStage.STORYBOARD)
    return store, work, ContextPack(run_id=run.id, project_id=run.project_id, objective=work.objective)


def test_discovery_merge_survives_stale_lifecycle_saves_and_other_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, work, context = runtime_work(path)
    stale_run = store.get_run(context.run_id)
    assert stale_run is not None
    other = AgentRuntimeStore(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(owner.remember_tool_schema_ids, context.run_id, [tool_id], work_order_id=work.id)
                   for owner, tool_id in ((store, "project.get"), (other, "video.inspect_take"))]
        for future in pending:
            future.result()
    store.remember_tool_schema_ids(context.run_id, ["project.get"])
    work.working_notes = "Retain the user's edit."
    store.save_work_order(work)  # This view predates both discoveries.
    stale_run.goal_summary = "Finish the updated shot."
    store.save_run(stale_run)
    resumed = other.get_work_order(work.id)
    resumed_run = other.get_run(context.run_id)
    assert resumed is not None and resumed_run is not None
    assert set(resumed.loaded_tool_schema_ids) == {"project.get", "video.inspect_take"}
    assert resumed.working_notes == work.working_notes
    assert resumed_run.loaded_tool_schema_ids == ["project.get"]
    assert resumed_run.goal_summary == stale_run.goal_summary


def test_result_save_preserves_schema_discovered_by_another_store(tmp_path: Path) -> None:
    class InterleavingStore(AgentRuntimeStore):
        before_result_insert: Callable[[], None] | None = None
        fail_fast = False

        @contextmanager
        def _connection(self) -> Iterator[sqlite3.Connection]:
            with super()._connection() as connection:
                if self.fail_fast:
                    connection.execute("PRAGMA busy_timeout = 0")

                def trace(statement: str) -> None:
                    if "INSERT INTO agent_results" in statement and self.before_result_insert is not None:
                        callback = self.before_result_insert
                        self.before_result_insert = None
                        callback()

                connection.set_trace_callback(trace)
                yield connection

    path = tmp_path / "agent.sqlite3"
    _store, work, context = runtime_work(path)
    saver, discovery = InterleavingStore(path), InterleavingStore(path)
    discovery.fail_fast = True
    outcomes: list[str] = []

    def discover() -> None:
        try:
            discovery.remember_tool_schema_ids(context.run_id, ["timeline.read_export"], work_order_id=work.id)
            outcomes.append("saved")
        except sqlite3.OperationalError as error:
            outcomes.append("blocked" if "locked" in str(error) else str(error))

    # Attempt a second connection's write after the result saver has read its
    # WorkOrder. A zero busy timeout makes this ordering deterministic: it either
    # writes before the stale save, or waits for the enclosing transaction.
    saver.before_result_insert = discover
    saver.save_agent_result(AgentResult(
        id="finished", work_order_id=work.id, status=AgentResultStatus.COMPLETED,
        concise_summary="Current result saved.",
    ))
    assert len(outcomes) == 1
    if outcomes == ["blocked"]:
        discover()  # The blocked writer resumes after the result transaction.
    assert outcomes[-1] == "saved"
    current = discovery.get_work_order(work.id)
    assert current is not None
    assert current.status == DelegationStatus.COMPLETED
    assert current.loaded_tool_schema_ids == ["timeline.read_export"]


def test_worker_discovery_survives_success_and_process_recreation_with_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "agent.sqlite3"
    store, work, context = runtime_work(path)
    request = json.dumps({"tool_name": "project.get", "arguments": {"project_id": context.project_id}, "reason": "Inspect"})
    client = ScriptedClient([json.dumps({"requested_tool_schema_ids": ["project.get"], "rationale": "Inspect"}), request])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Review only."))
    assert len(client.payloads) == 2
    assert set(cast(dict[str, JsonValue], client.payloads[0]["tool_schemas"])) == {"artifact.read_current", "agent.read_context"}
    reopened = AgentRuntimeStore(path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None and "project.get" in saved.loaded_tool_schema_ids
    resumed_client = ScriptedClient([request])
    resumed = LanguageSpecialistAgent(resumed_client, registry(2))
    resumed.context_compiler = ModelInputCompiler(reopened)
    resumed.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Review only."))
    assert len(resumed_client.payloads) == 1
    schemas = cast(dict[str, dict[str, JsonValue]], resumed_client.payloads[0]["tool_schemas"])
    assert schemas["project.get"]["description"] == "Current contract 2"
    assert "output_schema" not in schemas["project.get"]


def test_worker_persists_discovery_before_provider_failure(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    client = ScriptedClient([json.dumps({"requested_tool_schema_ids": ["project.get"], "rationale": "Inspect"}), RuntimeError("transport disconnected")])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(store)
    with pytest.raises(RuntimeError, match="transport disconnected"):
        worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Review only."))
    saved = store.get_work_order(work.id)
    assert saved is not None and "project.get" in saved.loaded_tool_schema_ids


def test_leader_discovery_survives_replan_with_current_definitions(tmp_path: Path) -> None:
    store, _work, context = runtime_work(tmp_path / "agent.sqlite3")
    plan = json.dumps({"summary": "Read current project", "steps": [{"id": "read", "title": "Read", "kind": "TOOL_CALL", "assigned_agent": "leader", "tool_name": "project.get", "instructions": "Read", "arguments": {}}]})
    client = ScriptedClient([json.dumps({"requested_tool_schema_ids": ["project.get"], "rationale": "Inspect"}), plan])
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(store)
    proposal = planner.propose_plan(context=context, skill_prompt="", method_skills=[], tools=registry().list(), current_plan=None, reason="Initial")
    resumed_client = ScriptedClient([plan])
    resumed = LanguageProductionPlanner(resumed_client)
    resumed.context_compiler = ModelInputCompiler(AgentRuntimeStore(store.database_path))
    resumed.propose_plan(context=context, skill_prompt="", method_skills=[], tools=registry(2).list(), current_plan=proposal, reason="Continue")
    schemas = cast(dict[str, dict[str, JsonValue]], resumed_client.payloads[0]["tool_schemas"])
    assert schemas["project.get"]["description"] == "Current contract 2"
    assert len(resumed_client.payloads) == 1


def test_read_identity_normalizes_defaults_but_retains_view_object_and_page() -> None:
    schema = cast(dict[str, JsonValue], TimelineReadInput.model_json_schema())
    base: dict[str, JsonValue] = {"project_id": "project-test", "shot_id": "shot-a"}
    assert normalized_read_arguments(base, schema) == normalized_read_arguments({**base, "include_project": False, "anchor_clip_id": None}, schema)
    assert normalized_read_arguments(base, schema) != normalized_read_arguments({**base, "include_project": True}, schema)
    work = WorkOrder(id="work", parent_run_id="run", sender="leader", recipient_role="editor", objective="Edit related shots", why_now="Current")
    for index, args in enumerate((base, {**base, "include_project": False}, {**base, "include_project": True}, {**base, "shot_id": "shot-b"}), 1):
        work.specialist_turns.append(SpecialistTurnRecord(id=f"read-{index}", sequence=index, tool_name="timeline.read_editorial", arguments=args, reason="Read", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"marker": f"read-{index}"}}))
    state = _specialist_working_set(work, [], read_tool_ids=frozenset({"timeline.read_editorial"}), read_schemas={"timeline.read_editorial": schema})
    assert [entry.source_sequence for entry in state.read_observations] == [2, 3]
    assert "read-1" not in state.model_dump_json()
    nested_schema: dict[str, JsonValue] = {"type": "object", "properties": {"request": {"$ref": "#/$defs/Page"}}, "$defs": {"Page": {"type": "object", "properties": {"offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 6000}}}}}
    assert normalized_read_arguments({"request": {"offset": 0, "limit": 6000}}, nested_schema) == {"request": {}}
    assert normalized_read_arguments({"request": {"offset": 6000}}, nested_schema) != {"request": {}}


def test_worker_retains_actual_context_tool_pages_after_subsequent_tools(tmp_path: Path) -> None:
    class ContextReader:
        def read_context(self, request: AgentReadContextInput) -> AgentReadContextOutput:
            raise AssertionError(f"Only the persisted result should be read: {request.snapshot_id}")

    tools = ToolRegistry()
    register_context_tools(tools, ContextReader())
    tools.register(ToolSpec(
        id="artifact.read_current", version=1, category=ToolCategory.ARTIFACT,
        description="Inspect current content", effect=ToolEffect.READ,
        idempotent=True, input_model=ReadInput, output_model=ReadOutput,
        handler=lambda _request: ToolExecution(output=ReadOutput(status="available")),
    ))
    assert "on-demand-payload" in tools.describe("agent.read_context").tags
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    arguments: dict[str, JsonValue] = {
        "project_id": context.project_id, "run_id": context.run_id,
        "snapshot_id": "saved-draft", "pointer": "/scene/dialogue",
    }
    pages: list[tuple[dict[str, JsonValue], str]] = [
        (arguments, "Original first page"),
        ({**arguments, "offset": 6000}, "Second page"),
        ({**arguments, "pointer": "/scene/action"}, "Another field"),
        ({**arguments, "offset": 0, "limit": 6000}, "Updated first page"),
    ]
    for sequence, (page_arguments, content) in enumerate(pages, 1):
        work.specialist_turns.append(SpecialistTurnRecord(
            id=f"page-{sequence}", sequence=sequence, tool_name="agent.read_context",
            arguments=page_arguments, reason="Read exact saved content",
            status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"content": content}},
        ))
    work.specialist_turns.append(SpecialistTurnRecord(
        id="read-current", sequence=5, tool_name="artifact.read_current",
        arguments={"project_id": context.project_id}, reason="Inspect current state",
        status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"status": "available"}},
    ))
    request = json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Continue"})
    client = ScriptedClient([request])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Review only."))
    state = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    observations = cast(list[dict[str, JsonValue]], state["read_observations"])
    assert {item["source_sequence"] for item in observations} == {2, 3, 4}
    encoded = json.dumps(observations)
    assert all(content in encoded for _args, content in pages[1:])
    assert "Original first page" not in encoded
    assert cast(dict[str, JsonValue], state["latest_handoff"])["sequence"] == 5


def test_large_retained_read_keeps_selected_shot_and_exact_other_fields_readable(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work.task_inputs = {"shot_ids": ["shot-3"]}
    shots: list[JsonValue] = [{"id": f"shot-{index}", "project_id": context.project_id,
        "title": f"Shot {index}", "body": f"Unabridged dramatic detail {index}. " * 800} for index in range(15)]
    working: dict[str, JsonValue] = {"read_observations": [{"tool_name": "shot.get_storyboard", "source_sequence": 1,
        "result": {"status": "SUCCEEDED", "output": {"storyboard": {"shots": shots}}}}],
        "latest_handoff": {"request": {"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id, "shot_id": "shot-3"}}, "result": {"current": "Already inspected"}}}
    raw = json.dumps(working)
    projected = project_retained_read_results(working, store=store, project_id=context.project_id, work_order=work)
    assert json.dumps(working) == raw
    assert len(json.dumps(projected)) < len(raw) // 3
    obs = cast(list[dict[str, JsonValue]], projected["read_observations"])[0]
    shot_list = pointer_value(obs, "/result/output/storyboard/shots")
    assert isinstance(shot_list, dict)
    assert shot_list["item_count"] == 15
    items = cast(list[dict[str, JsonValue]], shot_list["items"])
    assert [item["id"] for item in items] == [f"shot-{index}" for index in range(15)]
    # The selected shot retains its field structure. A long field is addressable,
    # while unrelated shots remain index entries instead of 15 copied bodies.
    assert "body" in items[3] and "saved_content" in items[2]
    marker = cast(dict[str, JsonValue], items[2]["saved_content"])
    saved = store.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert saved is not None
    assert pointer_value(saved, cast(str, marker["pointer"])) == shots[2]
    again = project_retained_read_results(working, store=store, project_id=context.project_id, work_order=work)
    assert again == projected
    assert len(store.list_context_snapshots(context.run_id)) == 1


def test_retained_editor_project_uses_exact_ref_and_keeps_clip_subtitle_facts(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    tools = registry()
    tools.register(ToolSpec(
        id="timeline.read_editorial", version=1, category=ToolCategory.TIMELINE,
        description="Read current editorial summary and optional editor project",
        effect=ToolEffect.READ, idempotent=True, input_model=TimelineReadInput,
        output_model=CurrentReadOutput,
        handler=lambda _request: ToolExecution(output=CurrentReadOutput(content={})),
    ))
    clips: list[JsonValue] = [{"id": "clip-selected", "source_shot_id": "shot-selected",
        "start_seconds": 2.0, "duration_seconds": 4.0, "volume": 0.8}]
    subtitles: list[JsonValue] = [{"id": "cue-selected", "startTime": 2.75,
        "endTime": 5.5, "text": "Keep this exact wording.", "style": {"fontSize": 32}}]
    editor: dict[str, JsonValue] = {"id": "editor-current", "name": "Current film", "activeTimelineId": "main",
        "assets": [{"id": f"asset-{index}", "prompt": f"Detailed original source {index}. " * 300} for index in range(24)],
        "timelines": [{"id": "main", "clips": clips, "subtitles": subtitles}]}
    content: dict[str, JsonValue] = {"project_id": context.project_id, "editorial_revision": 7,
        "timeline_id": "main", "is_saved": True, "clips": clips, "subtitles": subtitles, "project": editor}
    work.specialist_turns.append(SpecialistTurnRecord(
        id="read-editor", sequence=1, tool_name="timeline.read_editorial",
        arguments={"project_id": context.project_id, "include_project": True}, reason="Inspect full editor",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"content": content}},
    ))
    request = json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Continue"})
    client = ScriptedClient([request, request])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(store)
    view = AgentWorkOrderView(work_order=work, context=context, system_prompt="Edit the selected clip and cue.")
    worker.execute(view)
    first = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    assert pointer_value(first, "/latest_handoff/result/output/content/project") == editor
    work.specialist_turns.append(SpecialistTurnRecord(
        id="read-current", sequence=2, tool_name="artifact.read_current",
        arguments={"project_id": context.project_id}, reason="Read related current facts",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"status": "available"}},
    ))
    worker.execute(view)
    second = cast(dict[str, JsonValue], client.payloads[1]["working_set"])
    retained = cast(dict[str, JsonValue], pointer_value(second, "/read_observations/0/result/output/content"))
    assert retained["clips"] == clips and retained["subtitles"] == subtitles
    assert retained["editorial_revision"] == 7
    summary = cast(dict[str, JsonValue], retained["project"])
    assert summary["asset_count"] == 24 and summary["timeline_count"] == 1
    assert "assets" not in summary and "timelines" not in summary
    marker = cast(dict[str, JsonValue], summary["saved_content"])
    assert marker["pointer"] == "/output/content/project"
    saved = store.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert saved is not None and pointer_value(saved, cast(str, marker["pointer"])) == editor
    assert len(json.dumps(retained)) < len(json.dumps(content)) // 10


@pytest.mark.parametrize("tool_id, feedback_kind", [("artifact.read_current", "TOOL_SCHEMA_ALREADY_LOADED"), ("project.gte", "UNKNOWN_TOOL_SCHEMA")])
def test_worker_repairs_schema_discovery_without_replanning(tmp_path: Path, tool_id: str, feedback_kind: str) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    request = json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Inspect"})
    client = ScriptedClient([json.dumps({"requested_tool_schema_ids": [tool_id], "rationale": "Inspect"}), request])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Review only."))
    assert len(client.payloads) == 2
    feedback = cast(dict[str, JsonValue], client.payloads[1]["tool_schema_feedback"])
    assert feedback["kind"] == feedback_kind
    if feedback_kind == "UNKNOWN_TOOL_SCHEMA":
        assert "project.get" in cast(dict[str, list[str]], feedback["suggestions"])[tool_id]
