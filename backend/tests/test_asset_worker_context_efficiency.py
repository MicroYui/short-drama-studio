"""Asset worker discovery and exact shared read bodies, with real request paths."""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.agent_runtime.tool_context import initial_tool_schema_ids, project_retained_read_results
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _compact_tool_catalog_entry
from short_drama.production_profiles import ProductionSkillRole
from short_drama.skills.loader import load_shared_role_prompts
from short_drama.tools.artifact import AssetPlanFieldPatchInput, AssetPlanPatchInput
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry, runtime_work


def _working_set() -> dict[str, JsonValue]:
    pack: dict[str, JsonValue] = {"id": "pack-identity", "project_id": "project-test", "subject_asset_id": "actor",
        "entries": [{"id": "identity", "version_id": "face-v1", "role": "FACE_IDENTITY", "tags": ["face", "front"],
                     "purpose": "Use this saved face and outfit. " * 45}], "available": True}
    plan: dict[str, JsonValue] = {"shot_id": "shot-closeup", "bindings": [
        {"entry_id": "identity", "version_id": "face-v1", "purpose": "Keep this exact selected identity. " * 45}],
        "capability_valid": True}
    output: dict[str, JsonValue] = {"reference_packs": [pack], "shot_reference_plans": [plan],
        "storyboard": {"shots": [{"id": "shot-closeup", "body": "Saved shot action. " * 2100}]}}
    result: dict[str, JsonValue] = {"tool_id": "artifact.get_asset_work_context", "status": "SUCCEEDED", "output": output}
    return {"entities": [
        {"kind": "reference_pack", "id": "pack-identity", "source_tool": "artifact.get_asset_work_context", "source_sequence": 1, "state": deepcopy(pack)},
        {"kind": "shot_reference_plan", "id": "shot-closeup", "source_tool": "artifact.get_asset_work_context", "source_sequence": 1, "state": deepcopy(plan)}],
        "read_observations": [{"tool_name": "artifact.get_asset_work_context", "source_sequence": 1,
            "request_identifiers": {"scope": {"work_item_ids": ["work-closeup"]}, "shot_ids": ["shot-closeup"]}, "result": result}],
        "latest_handoff": {"result": {"output": {"capability": "available"}}},
        "user_json": deepcopy(pack)}


@pytest.mark.parametrize("has_suggestion", [True, False])
def test_crop_suggestion_survives_qa_then_other_read_and_worker_restart(tmp_path: Path, has_suggestion: bool) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    suggestion: dict[str, JsonValue] = {"region": {"x": 0, "y": 0, "width": 0.5, "height": 1},
        "reason": "Keep the candidate's left panel for this shot.",
        "source": {"source_asset_version_id": "candidate-v3", "source_width": 400,
                   "source_height": 200, "box_pixels": [0, 0, 200, 200]}}
    output: dict[str, JsonValue] = {"recommended": False, "summary": "Current candidate needs a local crop."}
    if has_suggestion:
        output["crop_suggestion"] = suggestion
    work.specialist_turns = [SpecialistTurnRecord(id="qa-candidate", sequence=1,
        tool_name="qa.inspect_asset_version", arguments={"project_id": context.project_id,
            "asset_id": "actor", "version_id": "candidate-v3"}, reason="Inspect this actual candidate.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": output}),
        SpecialistTurnRecord(id="other-read", sequence=2, tool_name="artifact.read_current",
            arguments={"project_id": context.project_id}, reason="Read current instructions.",
            status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"status": "available"}})]
    store.save_work_order(work)
    tools = registry()
    tools.register(ToolSpec(id="qa.inspect_asset_version", version=1, category=ToolCategory.QA,
        description="Inspect actual pixels.", effect=ToolEffect.EXTERNAL, idempotent=False,
        input_model=ReadInput, output_model=ReadOutput,
        handler=lambda _request: ToolExecution(output=ReadOutput(status="available"))))
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
        "arguments": {"project_id": context.project_id}, "reason": "Continue with the saved crop finding."})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Make the selected image."))
    working = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    entities = cast(list[dict[str, JsonValue]], working["entities"])
    review = next(entity for entity in entities if entity["kind"] == "asset_review")
    state = cast(dict[str, JsonValue], review["state"])
    assert state["version_id"] == "candidate-v3" and state["asset_id"] == "actor"
    if has_suggestion:
        assert state["crop_suggestion"] == suggestion
    else:
        assert "crop_suggestion" not in state
    assert all(read["tool_name"] != "qa.inspect_asset_version"
        for read in cast(list[dict[str, JsonValue]], working["read_observations"]))
    saved = reopened.get_work_order(work.id)
    assert saved is not None and saved.specialist_turns == work.specialist_turns


def test_new_builder_uses_field_contract_and_can_still_discover_full_patch(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    work.production_skill_role = ProductionSkillRole.ASSET_BUILDER
    tools = registry()
    for tool_id, model in (("artifact.patch_asset_plan_fields", AssetPlanFieldPatchInput), ("artifact.patch_asset_plan", AssetPlanPatchInput)):
        tools.register(ToolSpec(id=tool_id, version=3, category=ToolCategory.ARTIFACT,
            description="Save selected plan fields. Complete typed details follow.", effect=ToolEffect.WRITE,
            idempotent=False, input_model=model, output_model=ReadOutput,
            tags=frozenset({"asset-plan", "field-patch"}),
            handler=lambda _request: ToolExecution(output=ReadOutput(status="saved"))))
    request = json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Continue"})
    client = ScriptedClient([json.dumps({"requested_tool_schema_ids": ["artifact.patch_asset_plan"], "rationale": "Need to add a whole item"}), request])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Make the selected image."))
    first = cast(dict[str, JsonValue], client.payloads[0]["tool_schemas"])
    second = cast(dict[str, JsonValue], client.payloads[1]["tool_schemas"])
    assert "artifact.patch_asset_plan_fields" in first and "artifact.patch_asset_plan" not in first
    assert "artifact.patch_asset_plan" in second
    catalogue = cast(list[dict[str, JsonValue]], client.payloads[0]["available_tools"])
    for entry in catalogue:
        listed_tool = tools.describe(cast(str, entry["id"]))
        expected_keys = {"id", "description", "effect"}
        if listed_tool.effect == ToolEffect.READ:
            expected_keys.add("idempotent")
            assert entry["idempotent"] is listed_tool.idempotent
        assert set(entry) == expected_keys
    assert {entry["id"] for entry in catalogue} == {tool.id for tool in tools.list()}
    descriptor = tools.describe("artifact.patch_asset_plan")
    assert descriptor.version == 3 and set(descriptor.tags) == {"asset-plan", "field-patch"}
    assert _compact_tool_catalog_entry(descriptor)["description"] == "Save selected plan fields."
    resumed = AgentRuntimeStore(store.database_path).get_work_order(work.id)
    assert resumed is not None and "artifact.patch_asset_plan" in resumed.loaded_tool_schema_ids
    resumed_client = ScriptedClient([request])
    resumed_worker = LanguageSpecialistAgent(resumed_client, tools)
    resumed_worker.context_compiler = ModelInputCompiler(store)
    resumed_worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Make the selected image."))
    assert {"artifact.patch_asset_plan_fields", "artifact.patch_asset_plan"} <= set(cast(dict[str, JsonValue], resumed_client.payloads[0]["tool_schemas"]))
    work.production_skill_role = ProductionSkillRole.ASSET_PLANNER
    assert "artifact.patch_asset_plan" in initial_tool_schema_ids(work)
    role = load_shared_role_prompts()[ProductionSkillRole.ASSET_BUILDER].replace(chr(96), "")
    assert "artifact.patch_asset_plan_fields 的顶层 project_id、base_version_id 和 patches" in role


def test_equal_bodies_share_durable_read_without_changing_scope_source_or_trace(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    untouched = deepcopy(original)
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    entities = cast(list[dict[str, JsonValue]], projected["entities"])
    for index, collection in enumerate(("reference_packs", "shot_reference_plans")):
        marker = cast(dict[str, JsonValue], entities[index]["state"])
        snapshot = store.get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
        assert snapshot is not None
        expected = pointer_value(original, f"/read_observations/0/result/output/{collection}/0")
        assert pointer_value(snapshot, cast(str, marker["pointer"])) == expected
        assert pointer_value(projected, f"/read_observations/0/result/output/{collection}/0") == expected
        before = cast(dict[str, JsonValue], cast(list[JsonValue], original["entities"])[index])
        assert {key: value for key, value in entities[index].items() if key != "state"} == {key: value for key, value in before.items() if key != "state"}
    assert pointer_value(projected, "/read_observations/0/request_identifiers") == pointer_value(original, "/read_observations/0/request_identifiers")
    assert projected["latest_handoff"] == original["latest_handoff"]
    assert projected["user_json"] == original["user_json"]
    assert original == untouched
    assert len(store.list_context_snapshots(context.run_id)) == 1
    assert project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work) == projected
    assert count_text_tokens(json_text(projected)) < count_text_tokens(json_text(original))


@pytest.mark.parametrize("change", ["version", "source", "order", "scalar_type"])
def test_different_current_entity_is_never_replaced_with_old_read(tmp_path: Path, change: str) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    state = cast(dict[str, JsonValue], pointer_value(original, "/entities/0/state"))
    if change == "version":
        cast(dict[str, JsonValue], pointer_value(state, "/entries/0"))["version_id"] = "face-v2"
    elif change == "source":
        state["source"] = "latest explicit selection"
    elif change == "order":
        cast(dict[str, JsonValue], pointer_value(state, "/entries/0"))["tags"] = ["front", "face"]
    else:
        state["available"] = 1
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    assert pointer_value(projected, "/entities/0/state") == state
    assert "context_ref" not in cast(dict[str, JsonValue], pointer_value(projected, "/entities/0/state"))


def test_distinct_read_scopes_remain_and_newest_matching_body_is_used(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    observations = cast(list[JsonValue], original["read_observations"])
    later = deepcopy(cast(dict[str, JsonValue], observations[0]))
    later["source_sequence"] = 4
    later["request_identifiers"] = {"scope": {"work_item_ids": ["another-work"]}, "shot_ids": ["shot-closeup"], "offset": 20}
    observations.append(later)
    # Object key order is not a different body; ordered arrays still are.
    state = cast(dict[str, JsonValue], pointer_value(original, "/entities/0/state"))
    cast(dict[str, JsonValue], pointer_value(original, "/entities/0"))["state"] = dict(reversed(list(state.items())))
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    assert len(cast(list[JsonValue], projected["read_observations"])) == 2
    assert cast(str, pointer_value(projected, "/entities/0/state/context_ref")).endswith("-4")
    assert pointer_value(projected, "/read_observations/1/request_identifiers") == later["request_identifiers"]


def test_small_bodies_or_mismatching_saved_content_stay_inline(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    saved = deepcopy(cast(dict[str, JsonValue], pointer_value(original, "/read_observations/0/result")))
    cast(dict[str, JsonValue], pointer_value(saved, "/output/reference_packs/0"))["project_id"] = "another-project"
    store.save_context_snapshot(f"context-read-{work.id}-1", context.run_id, saved)
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    assert pointer_value(projected, "/entities/0/state") == pointer_value(original, "/entities/0/state")
    short = _working_set()
    tiny: dict[str, JsonValue] = {"shot_id": "shot-closeup", "bindings": []}
    cast(dict[str, JsonValue], pointer_value(short, "/entities/1"))["state"] = tiny
    cast(dict[str, JsonValue], pointer_value(short, "/read_observations/0/result/output"))["shot_reference_plans"] = [tiny]
    projected_short = project_retained_read_results(short, store=store, project_id=context.project_id, work_order=work)
    assert pointer_value(projected_short, "/entities/1/state") == tiny
    assert project_retained_read_results(original, store=None, project_id=context.project_id, work_order=work) == original


def test_small_read_needs_no_new_snapshot_just_for_sharing(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    cast(dict[str, JsonValue], pointer_value(original, "/read_observations/0/result/output"))["storyboard"] = {"shots": []}
    projected = project_retained_read_results(original, store=store, project_id=context.project_id, work_order=work)
    assert projected == original
    assert store.list_context_snapshots(context.run_id) == []


def test_restarted_worker_retains_body_once_and_overflow_refs_resolve(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3")
    original = _working_set()
    work.specialist_turns = [SpecialistTurnRecord(id="read-slice", sequence=1, tool_name="artifact.get_asset_work_context",
        arguments={"project_id": context.project_id, "scope": {"work_item_ids": ["work-closeup"]}}, reason="Read the selected work",
        status=SpecialistTurnStatus.COMPLETED, result=cast(dict[str, JsonValue], pointer_value(original, "/read_observations/0/result"))),
        SpecialistTurnRecord(id="read-capability", sequence=2, tool_name="image.capability", arguments={"project_id": context.project_id},
            reason="Check the image capability", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"capability": "available"}})]
    store.save_work_order(work)
    tools = registry()
    for tool_id, category in (("artifact.get_asset_work_context", ToolCategory.ARTIFACT), ("image.capability", ToolCategory.IMAGE)):
        tools.register(ToolSpec(id=tool_id, version=1, category=category, description="Read current selected facts", effect=ToolEffect.READ,
            idempotent=True, input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="available"))))
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Continue"})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Make this closeup."))
    working = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    entities = cast(list[dict[str, JsonValue]], working["entities"])
    assert len(entities) == 2
    for entity in entities:
        assert "context_ref" in cast(dict[str, JsonValue], entity["state"])
    assert pointer_value(working, "/read_observations/0/result/output/reference_packs/0") == pointer_value(original, "/entities/0/state")
    compiler = ModelInputCompiler(reopened, budget=ContextBudget(window_tokens=2300, compact_at_tokens=1500, target_tokens=1000, envelope_tokens=100))
    prepared = compiler.prepare(run_id=context.run_id, project_id=context.project_id, instructions="Keep the exact selected image.",
        payload={"working_set": working, "context_read": client.payloads[0]["context_read"]},
        max_output_tokens=100, force_compact=True)
    compacted = cast(dict[str, JsonValue], json.loads(prepared.input_text))
    assert prepared.offloaded_blocks > 0
    reads = pointer_value(compacted, "/working_set/read_observations")
    marker = cast(dict[str, JsonValue], reads if isinstance(reads, dict)
                  else pointer_value(compacted, "/working_set/read_observations/0/result"))
    assert "context_ref" in marker
    for entity in entities:
        shared = cast(dict[str, JsonValue], entity["state"])
        snapshot = AgentRuntimeStore(store.database_path).get_context_snapshot(cast(str, shared["context_ref"]), context.run_id)
        assert snapshot is not None
        assert isinstance(pointer_value(snapshot, cast(str, shared["pointer"])), dict)
    assert prepared.input_tokens == count_text_tokens("Keep the exact selected image.") + count_text_tokens(prepared.input_text)
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.specialist_turns == work.specialist_turns
