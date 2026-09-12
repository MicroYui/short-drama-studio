"""A compound receipt must not make a plan entity own old Shot or QA facts."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.asset_plan_context import fold_asset_plan_items
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.asset_plan_receipts import ASSET_PLAN_WRITE_TOOLS, compatible_asset_plan_receipt
from short_drama.production_manager import (
    AgentWorkOrderView, LanguageSpecialistAgent, SpecialistEntitySnapshot, _specialist_working_set,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.tools.contracts import ToolCallStatus, ToolWriteReceipt
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_shot_media_registration import Case, _invoke, _turn, case as case


def test_actual_compound_then_plain_save_reaches_restarted_worker_without_old_shot_in_plan(case: Case, tmp_path: Path) -> None:
    read_args: dict[str, JsonValue] = {"project_id": case.project_id, "scope": {
        "mode": "ITEM", "item_ids": ["asset-plan-character-linxia", "asset-plan-prop-recorder"]}}
    read = case.service.tool_registry.invoke("artifact.get_asset_work_context", read_args)
    combined = _invoke(case)
    assert read.status == combined.status == ToolCallStatus.SUCCEEDED
    assert combined.receipt and combined.output
    old_shot = deepcopy(combined.receipt.current_state["saved_shot"])
    selected = case.service.get_project(case.project_id).document.shots[0].model_copy(deep=True)
    selected.audio_intent = "A new explicitly saved performance intent, after media registration."
    plain_args: dict[str, JsonValue] = {"project_id": case.project_id, "shot_id": selected.id,
        "request": {"expected_revision": selected.revision, "shot": selected.model_dump(mode="json")}}
    plain = case.service.tool_registry.invoke("shot.save_current", plain_args)
    assert plain.status == ToolCallStatus.SUCCEEDED and plain.output

    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work.production_skill_role = ProductionSkillRole.ASSET_BUILDER
    context.project_id = case.project_id
    work.specialist_turns = [_turn("artifact.get_asset_work_context", read_args, read, 1),
        _turn("shot.save_current", case.arguments, combined, 2), _turn("shot.save_current", plain_args, plain, 3)]
    work.loaded_tool_schema_ids = ["artifact.read_current"]
    store.save_work_order(work)
    original = work.model_dump(mode="json")
    reopened = AgentRuntimeStore(store.database_path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
        "reason": "Continue with current content.", "arguments": {"project_id": case.project_id}})])
    worker = LanguageSpecialistAgent(client, case.service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Use actual current facts."))
    assert len(client.payloads) == 1
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    plan = next(entity["state"] for entity in working["entities"] if entity["kind"] == "asset_plan_state")
    assert "saved_shot" not in plan and "media_registration" not in plan
    assert "shot_revisions" not in plan and "reference_plan_preview_scope" not in plan
    for key in ("asset_plan_identity", "base_asset_plan_identity", "sources_changed", "execution_contract"):
        assert plan[key] == combined.receipt.current_state[key]
    latest = working["latest_handoff"]["result"]["receipt"]["current_state"]["saved_shot"]
    assert latest == plain.output["saved_shot"] and latest != old_shot
    items = [entity for entity in working["entities"] if entity["kind"] == "asset_plan_item"]
    assert [entity["id"] for entity in items] == ["asset-plan-prop-recorder"]
    assert items[0]["state"]["unchanged_through_asset_plan_identity"] == plan["asset_plan_identity"]
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None and persisted.specialist_turns[1].result == combined.model_dump(mode="json")
    assert persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == {
        key: value for key, value in original.items() if key != "loaded_tool_schema_ids"}


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


@pytest.mark.parametrize("tool_id", sorted(ASSET_PLAN_WRITE_TOOLS))
def test_all_plan_write_receipts_keep_exact_plan_and_legacy_fields(tool_id: str) -> None:
    fixture = _fixture("asset_plan_write_scope.json")
    ordinary = deepcopy(fixture["saved_receipt"]["current_state"])
    # These are the fields supported by the existing legacy acknowledgement
    # adapter, in addition to today's exact parent/delta/contract receipt.
    output = {**fixture["write"]["result"]["output"], "operation": "REPLACE",
        "item_count": 11, "work_item_count": 16, "notes_updated": False}
    compatible = compatible_asset_plan_receipt(tool_id, {"status": "SUCCEEDED", "output": output,
        "receipt": ToolWriteReceipt(current_state=ordinary).model_dump(mode="json")})
    assert isinstance(compatible, dict)
    expected = cast(dict[str, JsonValue], compatible["current_state"])
    identity = cast(dict[str, JsonValue], expected["asset_plan_identity"])
    document_id = cast(str, identity["document_id"])
    polluted = {**deepcopy(expected), "saved_shot": {"shot_id": "old", "revision": 1},
        "shot_revisions": {"old": 1}, "media_registration": {"asset_version_id": "old-image"},
        "reference_plan_preview_scope": "GENERATION_ONLY", "reference_plan_preview_error": None}
    prior = SpecialistEntitySnapshot(kind="asset_plan_state", id=document_id, source_tool="shot.save_current",
        source_sequence=1, state=polluted)
    entities = {("asset_plan_state", document_id): prior}
    turn = SpecialistTurnRecord(id="ack", sequence=2, tool_name=tool_id,
        arguments={}, reason="Observe the same current plan identity.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": output,
            "receipt": ToolWriteReceipt(current_state=polluted).model_dump(mode="json")})
    before = turn.model_dump(mode="json")
    fold_asset_plan_items(entities, turn, read_scope_key="scope", read_scope={},
        known_versions={document_id: cast(int, identity["version"])}, entity_type=SpecialistEntitySnapshot)
    assert entities[("asset_plan_state", document_id)].state == expected
    assert turn.model_dump(mode="json") == before
    assert prior.state == polluted


def test_independent_actual_qa_scopes_survive_compound_receipt_projection() -> None:
    qa = _fixture("asset_review_scopes_actual.json")
    plan = _fixture("asset_plan_write_scope.json")
    turns = [SpecialistTurnRecord.model_validate(turn) for turn in qa["turns"]]
    output = deepcopy(plan["write"]["result"]["output"])
    combined = deepcopy(plan["write"])
    combined.update(id="compound-after-qa", sequence=100, tool_name="shot.save_current")
    receipt = ToolWriteReceipt.model_validate(plan["saved_receipt"])
    receipt.current_state.update({"saved_shot": {"shot_id": "current-shot", "revision": 2},
        "media_registration": {"asset_version_id": qa["turns"][0]["arguments"]["version_id"]}})
    combined["result"].update(tool_id="shot.save_current", output=output, receipt=receipt.model_dump(mode="json"))
    turns.append(SpecialistTurnRecord.model_validate(combined))
    work = WorkOrder(id="qa-interleaving", parent_run_id="local-fold", sender="leader", recipient_role="asset_builder",
        objective="Keep independent actual inspections.", why_now="Combined local save completed.", specialist_turns=turns)
    original = deepcopy(work.specialist_turns)
    state = _specialist_working_set(work, [], project_id=qa["project_id"], write_tool_ids=frozenset({"shot.save_current"}))
    reviews = [entity for entity in state.entities if entity.kind == "asset_review"]
    assert len(reviews) == 4
    by_sequence = {entity.source_sequence: entity for entity in reviews}
    for turn in qa["turns"]:
        review = by_sequence[turn["sequence"]]
        assert review.state["review_scope"] == {"regions": [], **turn["arguments"]["request"]}
        assert review.state["edit_guide_suggestion"] == turn["result"]["output"]["edit_guide_suggestion"]
        assert cast(dict[str, JsonValue], review.state["attempt_ref"])["attempt_id"] == turn["id"]
    plan_state = next(entity.state for entity in state.entities if entity.kind == "asset_plan_state")
    assert "saved_shot" not in plan_state and "media_registration" not in plan_state
    assert work.specialist_turns == original
