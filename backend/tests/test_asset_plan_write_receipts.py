"""Actual AssetPlan writes, scoped retention, and the next restarted Worker request."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.asset_plan_reconciler import assess_asset_plan_execution_contract
from short_drama.production_contracts import AssetPlanCreateRequest, AssetPlanFieldPatch, AssetPlanPatchRequest
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionSkillRole
from short_drama.models import ProductionDocumentOrigin
from short_drama.tools.contracts import ToolCallStatus, ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_asset_plan_runtime import _asset_plan, _persist_failed_asset_plan_attempt, _service, _setup_story_truth
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry, runtime_work


FIXTURE: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/asset_plan_write_scope.json").read_text())
READS = frozenset({"artifact.get_asset_work_context", "artifact.read_current"})
WRITES = frozenset({"artifact.patch_asset_plan", "artifact.patch_asset_plan_fields", "artifact.replace_asset_plan"})


def _fixture_work(tmp_path: Path, *, legacy: bool = False):
    store, work, context = runtime_work(tmp_path / "runtime.sqlite3",
        project_id=str(FIXTURE["read"]["arguments"]["project_id"]))
    work.production_skill_role = ProductionSkillRole.ASSET_PLANNER
    read, write = deepcopy(FIXTURE["read"]), deepcopy(FIXTURE["write"])
    if not legacy:
        write["result"]["receipt"] = deepcopy(FIXTURE["saved_receipt"])
    work.specialist_turns = [SpecialistTurnRecord.model_validate(read), SpecialistTurnRecord.model_validate(write)]
    work.task_inputs = {"item_ids": deepcopy(read["arguments"]["scope"]["item_ids"])}
    store.save_work_order(work)
    return store, work, context


def _state(work):
    return _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES)


def test_real_five_item_write_preserves_six_exact_scopes_in_restarted_worker(tmp_path: Path) -> None:
    store, work, context = _fixture_work(tmp_path)
    original = work.model_dump(mode="json")
    restarted = AgentRuntimeStore(store.database_path)
    saved = restarted.get_work_order(work.id)
    assert saved is not None
    tools = registry()
    tools.register(ToolSpec(id="artifact.patch_asset_plan", version=1, category=ToolCategory.ARTIFACT,
        description="Save plan fields.", effect=ToolEffect.WRITE, idempotent=False,
        input_model=ReadInput, output_model=ReadOutput,
        handler=lambda _input: ToolExecution(output=ReadOutput(status="saved"))))
    tools.register(ToolSpec(id="artifact.get_asset_work_context", version=1, category=ToolCategory.ARTIFACT,
        description="Read exact scope.", effect=ToolEffect.READ, idempotent=True,
        input_model=ReadInput, output_model=ReadOutput,
        handler=lambda _input: ToolExecution(output=ReadOutput(status="available"))))
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": context.project_id}, "reason": "Continue"})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Plan the remaining images."))
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    receipt = working["latest_handoff"]["result"]["receipt"]
    assert receipt["current_state"]["asset_plan_identity"] == FIXTURE["saved_receipt"]["current_state"]["asset_plan_identity"]
    assert receipt["current_state"]["execution_contract"] == FIXTURE["saved_receipt"]["current_state"]["execution_contract"]
    items = {item["id"]: item["state"] for item in working["entities"] if item["kind"] == "asset_plan_item"}
    changed = set(FIXTURE["saved_receipt"]["changed_ids"]["changed_item_ids"])
    expected = {item["id"]: item for item in FIXTURE["read"]["result"]["output"]["asset_plan_items"] if item["id"] not in changed}
    assert len(items) == len(expected) == 6
    for key, body in expected.items():
        assert items[key]["item"] == body
        assert items[key]["asset_plan_identity"] == FIXTURE["read"]["result"]["output"]["asset_plan_identity"]
        assert items[key]["unchanged_through_asset_plan_identity"] == receipt["current_state"]["asset_plan_identity"]
        assert items[key]["read_scope"] == FIXTURE["read"]["arguments"]
    assert not any(read["tool_name"] == "artifact.get_asset_work_context" for read in working["read_observations"])
    persisted = restarted.get_work_order(work.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == {
        key: value for key, value in original.items() if key != "loaded_tool_schema_ids"}


def test_old_success_restores_exact_uuid_without_fabricating_delta_or_rewriting_trace(tmp_path: Path) -> None:
    store, work, _context = _fixture_work(tmp_path, legacy=True)
    before = deepcopy(work.specialist_turns[-1].result)
    state = _state(work)
    assert state.latest_handoff is not None
    receipt = pointer_value(state.latest_handoff, "/result/receipt")
    assert isinstance(receipt, dict)
    assert pointer_value(receipt, "/current_state/asset_plan_identity/version_id") == FIXTURE["write"]["result"]["output"]["asset_plan_identity"]["version_id"]
    assert "changed_item_ids" not in cast(dict[str, JsonValue], receipt["changed_ids"])
    assert "execution_contract" not in cast(dict[str, JsonValue], receipt["current_state"])
    assert not any(entity.kind == "asset_plan_item" for entity in state.entities)
    assert store.get_work_order(work.id).specialist_turns[-1].result == before  # type: ignore[union-attr]


def test_actual_nonempty_contract_diagnostics_survive_another_tool(tmp_path: Path) -> None:
    _store, work, _context = _fixture_work(tmp_path)
    write = deepcopy(FIXTURE["diagnostic_write"])
    write["result"]["receipt"] = deepcopy(FIXTURE["diagnostic_saved_receipt"])
    work.specialist_turns = [SpecialistTurnRecord.model_validate(write), SpecialistTurnRecord(
        id="later-read", sequence=15, tool_name="artifact.read_current", arguments={"project_id": "project-test"},
        reason="Read another object.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"content": {}}})]
    state = _state(work)
    plan = next(entity.state for entity in state.entities if entity.kind == "asset_plan_state")
    report = cast(dict[str, Any], plan["execution_contract"])
    assert report == FIXTURE["diagnostic_saved_receipt"]["current_state"]["execution_contract"]
    assert report["needs_reconciliation"] and len(report["issues"]) == 2
    assert {issue["item_id"] for issue in report["issues"]} == {
        "item-yqq-external-statement-review-signed", "item-yqq-external-statement-both-signed"}


def test_retained_items_still_read_back_exactly_after_final_budget_offload(tmp_path: Path) -> None:
    store, work, context = _fixture_work(tmp_path)
    working = _state(work).model_dump(mode="json")
    original = deepcopy(working)
    compiler = ModelInputCompiler(store, budget=ContextBudget(window_tokens=5000,
        compact_at_tokens=4000, target_tokens=2500, envelope_tokens=100))
    prepared = compiler.prepare(run_id=context.run_id, project_id=context.project_id,
        instructions="Continue with the selected saved items.", payload={"working_set": working},
        max_output_tokens=100, force_compact=True)
    assert prepared.offloaded_blocks > 0
    actual = json.loads(prepared.input_text)
    marker = pointer_value(actual, "/working_set/entities")
    assert isinstance(marker, dict) and "context_ref" in marker
    snapshot = AgentRuntimeStore(store.database_path).get_context_snapshot(cast(str, marker["context_ref"]), context.run_id)
    assert snapshot is not None
    assert pointer_value(snapshot, cast(str, marker["pointer"])) == original["entities"]
    assert prepared.input_tokens == count_text_tokens("Continue with the selected saved items.") + count_text_tokens(prepared.input_text)
    assert working == original


@pytest.mark.parametrize("cause", ["source", "replace", "retry_replace", "unknown_parent", "later_old_read"])
def test_unproven_or_changed_sources_never_restore_old_target_items(tmp_path: Path, cause: str) -> None:
    _store, work, _context = _fixture_work(tmp_path)
    write = work.specialist_turns[-1]
    assert write.result is not None
    receipt = cast(dict[str, Any], write.result["receipt"])
    if cause == "source":
        receipt["current_state"]["sources_changed"] = True
    elif cause == "replace":
        write.tool_name = "artifact.replace_asset_plan"
    elif cause == "retry_replace":
        write.tool_name = "artifact.retry_asset_plan_attempt"
    elif cause == "unknown_parent":
        receipt["current_state"]["base_asset_plan_identity"]["version_id"] = "unobserved-concurrent-version"
    else:
        old = work.specialist_turns[0].model_copy(deep=True, update={"sequence": 25})
        work.specialist_turns.append(old)
        work.specialist_turns.append(SpecialistTurnRecord(id="other-read", sequence=26, tool_name="artifact.read_current",
            arguments={"project_id": "project-test"}, reason="Continue", status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "artifact.read_current", "status": "SUCCEEDED", "output": {"content": {}}}))
    items = [entity for entity in _state(work).entities if entity.kind == "asset_plan_item"]
    if cause == "later_old_read":
        assert len(items) == 6
        assert not {item.id for item in items} & set(receipt["changed_ids"]["changed_item_ids"])
    else:
        assert not items


def test_distinct_read_scopes_stay_distinct_without_duplicate_visible_bodies(tmp_path: Path) -> None:
    _store, work, _context = _fixture_work(tmp_path)
    read = work.specialist_turns[0].model_copy(deep=True, update={"sequence": 22})
    assert read.result is not None
    read.arguments["scope"] = {"mode": "WORK_ITEM", "work_item_ids": ["wi-yqq-reuse-room"]}
    output = cast(dict[str, Any], read.result["output"])
    output["asset_plan_items"] = [output["asset_plan_items"][0]]
    output["requested_item_ids"] = []
    output["requested_work_item_ids"] = ["wi-yqq-reuse-room"]
    work.specialist_turns.insert(0, read)
    items = [entity for entity in _state(work).entities if entity.kind == "asset_plan_item"]
    room = [item for item in items if item.id == "item-yqq-room"]
    assert len(room) == 2 and room[0].state["read_scope"] != room[1].state["read_scope"]
    work.specialist_turns = work.specialist_turns[:2]
    assert not any(entity.kind == "asset_plan_item" for entity in _state(work).entities)


@pytest.mark.parametrize("mode", ["fields", "bulk"])
def test_actual_registry_write_receipt_reaches_worker_and_next_save_without_index(tmp_path: Path, mode: str) -> None:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    plan = _asset_plan(service, pid, spec=spec, storyboard=board)
    created = service.tool_registry.invoke("artifact.create_asset_plan", {"project_id": pid,
        "request": {"asset_plan": plan.model_dump(mode="json"), "origin": "AGENT"}})
    assert created.status == ToolCallStatus.SUCCEEDED and created.receipt is not None
    base = cast(dict[str, Any], created.receipt.current_state["asset_plan_identity"])["version_id"]
    changes = [{"work_item_id": plan.items[0].work_items[0].id, "op": "replace", "path": "/final_prompt", "value": "Keep the actual character identity and use the agreed framing."}]
    replacement = plan.items[0].model_copy(deep=True)
    replacement.work_items[0].final_prompt = changes[0]["value"]
    tool = "artifact.patch_asset_plan_fields" if mode == "fields" else "artifact.patch_asset_plan"
    arguments = {"project_id": pid, "base_version_id": base, "patches": changes, "origin": "AGENT"} if mode == "fields" else {
        "project_id": pid, "request": {"base_version_id": base, "upsert_items": [replacement.model_dump(mode="json")], "origin": "AGENT"}}
    call = service.tool_registry.invoke(tool, arguments)
    assert call.status == ToolCallStatus.SUCCEEDED and call.receipt is not None, call.error
    saved = service.get_asset_plan(pid)
    assert call.receipt.current_state["asset_plan_identity"] == {"document_id": saved.document.id, "version_id": saved.version.id, "version": saved.version.version}
    assert call.receipt.changed_ids["changed_item_ids"] == [plan.items[0].id]
    assert call.receipt.changed_ids["changed_work_item_ids"] == [plan.items[0].work_items[0].id]
    review = assess_asset_plan_execution_contract(saved.asset_plan)
    assert call.receipt.current_state["execution_contract"] == review.model_dump(mode="json")
    assert call.output is not None and "asset_plan" not in call.output
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = pid
    work.production_skill_role = ProductionSkillRole.ASSET_PLANNER
    work.specialist_turns = [SpecialistTurnRecord(id="actual-write", sequence=1, tool_name=tool,
        arguments=cast(dict[str, JsonValue], arguments), reason="Save work size.", status=SpecialistTurnStatus.COMPLETED,
        result=call.model_dump(mode="json"))]
    work.loaded_tool_schema_ids = ["artifact.patch_asset_plan_fields"]
    store.save_work_order(work)

    class ContinueClient(ScriptedClient):
        def generate_text(self, *, instructions: str, input_text: str,
                          image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
            payload = json.loads(input_text)
            self.payloads.append(payload)
            actual = payload["working_set"]["latest_handoff"]["result"]["receipt"]["current_state"]
            assert actual["execution_contract"] == review.model_dump(mode="json")
            return json.dumps({"tool_name": "artifact.patch_asset_plan_fields", "reason": "Continue from the actual saved version and contract facts.",
                "arguments": {"project_id": pid, "base_version_id": actual["asset_plan_identity"]["version_id"],
                    "patches": [{"work_item_id": plan.items[0].work_items[0].id, "op": "replace", "path": "/final_prompt", "value": "Keep the saved identity, with the selected framing and wardrobe."}], "origin": "AGENT"}})

    client = ContinueClient([])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(AgentRuntimeStore(store.database_path))
    resumed = store.get_work_order(work.id)
    assert resumed is not None
    response = worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Prepare the remaining images."))
    assert len(client.payloads) == 1
    followup = service.tool_registry.invoke(response.tool_name, response.arguments)  # type: ignore[union-attr]
    assert followup.status == ToolCallStatus.SUCCEEDED


def test_stale_base_merge_receipt_reports_actual_parent_and_only_actual_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    plan = _asset_plan(service, pid, spec=spec, storyboard=board)
    created = service.create_asset_plan(pid, AssetPlanCreateRequest(asset_plan=plan, origin=ProductionDocumentOrigin.AGENT))
    first = service.patch_asset_plan(pid, AssetPlanPatchRequest(base_version_id=created.version.id,
        field_patches=[AssetPlanFieldPatch(item_id=plan.items[0].id, op="replace", path="/rationale", value="Concurrent item A edit.")], origin=ProductionDocumentOrigin.AGENT))
    call = service.tool_registry.invoke("artifact.patch_asset_plan_fields", {"project_id": pid,
        "base_version_id": created.version.id, "patches": [{"item_id": plan.items[1].id, "op": "replace", "path": "/rationale", "value": "Item B edit."}], "origin": "AGENT"})
    assert call.status == ToolCallStatus.SUCCEEDED and call.receipt is not None
    assert cast(dict[str, Any], call.receipt.current_state["base_asset_plan_identity"])["version_id"] == first.version.id
    assert call.receipt.changed_ids["changed_item_ids"] == [plan.items[1].id]
    assert service.get_asset_plan(pid).asset_plan.items[0].rationale == "Concurrent item A edit."


def test_newer_index_invalidates_unproven_item_slices_and_old_reads_cannot_revive_them(tmp_path: Path) -> None:
    _store, work, _context = _fixture_work(tmp_path)
    newer = deepcopy(FIXTURE["saved_receipt"]["current_state"]["asset_plan_identity"])
    newer.update(version_id="another-writer-version", version=newer["version"] + 1)
    work.specialist_turns.append(SpecialistTurnRecord(id="new-index", sequence=25,
        tool_name="artifact.get_asset_plan_index", arguments={"project_id": "project-test"},
        reason="Read the current index.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"asset_plan_identity": newer}}))
    assert not any(entity.kind == "asset_plan_item" for entity in _state(work).entities)
    work.specialist_turns.append(work.specialist_turns[0].model_copy(deep=True, update={"sequence": 26}))
    assert not any(entity.kind == "asset_plan_item" for entity in _state(work).entities)


@pytest.mark.parametrize("repeat_old_read", [False, True])
def test_read_only_new_index_removes_old_scope_from_restarted_worker(tmp_path: Path, repeat_old_read: bool) -> None:
    store, work, context = _fixture_work(tmp_path)
    read = work.specialist_turns[0]
    assert read.result is not None
    context.project_id = cast(str, read.arguments["project_id"])
    newer = deepcopy(FIXTURE["read"]["result"]["output"]["asset_plan_identity"])
    newer.update(version_id="new-other-writer-version", version=newer["version"] + 1)
    index = SpecialistTurnRecord(id="new-index", sequence=24, tool_name="artifact.get_asset_plan_index",
        arguments={"project_id": context.project_id}, reason="Observe another writer's current plan.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED",
            "output": {"asset_plan_identity": newer}, "artifact_refs": [{"project_id": context.project_id,
                "artifact_type": "asset_plan", "artifact_id": newer["document_id"], "version": newer["version"]}]})
    # No t24 write from this Worker: the newer index alone invalidates the old scope.
    work.specialist_turns = [read, index]
    if repeat_old_read:
        work.specialist_turns.extend([read.model_copy(deep=True, update={"id": "explicit-old-read", "sequence": 25}),
            SpecialistTurnRecord(id="later-read", sequence=26, tool_name="artifact.read_current",
                arguments={"project_id": context.project_id}, reason="Continue after the explicit historical read.",
                status=SpecialistTurnStatus.COMPLETED,
                result={"status": "SUCCEEDED", "output": {"content": {"project_id": context.project_id}}})])
    store.save_work_order(work)
    old_snapshot_id = f"context-read-{work.id}-{read.sequence}"
    store.save_context_snapshot(old_snapshot_id, work.parent_run_id, read.result)
    original = work.model_dump(mode="json", exclude={"loaded_tool_schema_ids"})
    restarted = AgentRuntimeStore(store.database_path)
    resumed = restarted.get_work_order(work.id)
    assert resumed is not None
    tools = registry()
    for tool_id in ("artifact.get_asset_work_context", "artifact.get_asset_plan_index"):
        tools.register(ToolSpec(id=tool_id, version=1, category=ToolCategory.ARTIFACT,
            description="Read a selected plan scope.", effect=ToolEffect.READ, idempotent=True,
            input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="available"))))
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue from current facts.",
        "arguments": {"project_id": context.project_id}})])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Use the current plan."))
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    assert not any(item["tool_name"] == "artifact.get_asset_work_context" for item in working["read_observations"])
    assert not any(item["kind"] == "asset_plan_item" for item in working["entities"])
    state = next(item["state"] for item in working["entities"] if item["kind"] == "asset_plan_state")
    assert state["asset_plan_identity"] == newer
    assert '"asset_plan_items"' not in json.dumps(client.payloads[0])
    assert restarted.get_context_snapshot(old_snapshot_id, work.parent_run_id) == read.result
    persisted = restarted.get_work_order(work.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == original


@pytest.mark.parametrize("observed", ["NEWER", "SAME", "FAILED"])
def test_index_scope_invalidation_preserves_other_documents_and_unconfirmed_reads(tmp_path: Path, observed: str) -> None:
    _store, work, _context = _fixture_work(tmp_path)
    read = work.specialist_turns[0]
    other = read.model_copy(deep=True, update={"id": "another-plan-scope", "sequence": 24})
    assert other.result is not None
    other_output = cast(dict[str, Any], other.result["output"])
    other_output["asset_plan_identity"]["document_id"] = "different-plan-document"
    other.arguments["asset_plan_version_id"] = "different-plan-version"
    current = deepcopy(FIXTURE["read"]["result"]["output"]["asset_plan_identity"])
    if observed != "SAME":
        current.update(version_id="new-other-writer-version", version=current["version"] + 1)
    index = SpecialistTurnRecord(id="new-index", sequence=25, tool_name="artifact.get_asset_plan_index",
        arguments=deepcopy(read.arguments), reason="Read the selected current plan.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "FAILED" if observed == "FAILED" else "SUCCEEDED", "output": {"asset_plan_identity": current}})
    unrelated = SpecialistTurnRecord(id="unrelated-read", sequence=22, tool_name="artifact.read_current",
        arguments={"project_id": read.arguments["project_id"], "scene_id": "independent-scene"},
        reason="Read the selected scene.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"content": {"scene": {
            "id": "independent-scene", "summary": "This unrelated scene body remains available."}}}})
    work.specialist_turns = [unrelated, read, other, index]
    state = _state(work)
    identities = [cast(dict[str, Any], item.result["output"])["asset_plan_identity"]
        for item in state.read_observations if item.tool_name == "artifact.get_asset_work_context"]
    assert any(item["document_id"] == "different-plan-document" for item in identities)
    old_id = FIXTURE["read"]["result"]["output"]["asset_plan_identity"]["document_id"]
    assert any(item["document_id"] == old_id for item in identities) is (observed != "NEWER")
    preserved = next(item for item in state.read_observations if item.tool_name == "artifact.read_current")
    assert unrelated.result is not None
    assert preserved.result["output"] == unrelated.result["output"]
    assert preserved.result["status"] == unrelated.result["status"]
    assert preserved.request_identifiers["scene_id"] == "independent-scene"


def test_actual_retry_keeps_the_inner_write_receipt(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pid, spec, board = _setup_story_truth(service)
    plan = _asset_plan(service, pid, spec=spec, storyboard=board)
    payload = plan.model_dump(mode="json")
    payload["items"][0]["work_items"][0]["final_prompt"] = ""
    run_id, repair_id, ref = _persist_failed_asset_plan_attempt(service, project_id=pid,
        arguments={"project_id": pid, "request": {"asset_plan": payload, "origin": "AGENT"}},
        turn_id="failed-create")
    retried = service.production_manager._invoke_scoped_tool(pid, "artifact.retry_asset_plan_attempt", {
        "project_id": pid, "attempt_ref": ref.model_dump(mode="json"),
        "patches": [{"work_item_id": plan.items[0].work_items[0].id, "op": "replace",
            "path": "/final_prompt", "value": "Use the agreed character appearance and wardrobe."}]},
        agent_run_id=run_id, work_order_id=repair_id)
    assert retried.status == ToolCallStatus.SUCCEEDED and retried.receipt is not None
    saved = service.get_asset_plan(pid)
    assert retried.receipt.current_state["asset_plan_identity"] == {
        "document_id": saved.document.id, "version_id": saved.version.id, "version": saved.version.version}
    assert retried.receipt.current_state["execution_contract"] == assess_asset_plan_execution_contract(saved.asset_plan).model_dump(mode="json")
