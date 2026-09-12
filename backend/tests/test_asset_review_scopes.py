"""Independent observations of one candidate survive real Worker reconstruction."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from short_drama.agent_runtime import ContextPack, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_edit_guide_suggestion import _setup
from tests.test_harness_tool_context import ScriptedClient


@pytest.fixture
def actual() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures/asset_review_scopes_actual.json").read_text())


def _reviews(working: dict[str, Any]) -> list[dict[str, Any]]:
    return [entity for entity in working["entities"] if entity["kind"] == "asset_review"]


def _work(turns: list[dict[str, Any]]) -> WorkOrder:
    return WorkOrder(id="work-scopes", parent_run_id="run-scopes", sender="leader",
        recipient_role="asset_builder", objective="Apply the independently observed local corrections.",
        why_now="The actual geometry is already available.",
        specialist_turns=[SpecialistTurnRecord.model_validate(turn) for turn in turns])


def _fold(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _reviews(_specialist_working_set(_work(turns), []).model_dump(mode="json"))


def _save_work(service: ShortDramaService, project_id: str, turns: list[dict[str, Any]]) -> tuple[WorkOrder, ContextPack]:
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Use the saved local geometry.", root_agent="leader")
    work = _work(turns).model_copy(update={"parent_run_id": run.id})
    runtime.store.save_work_order(work)
    return work, ContextPack(run_id=run.id, project_id=project_id, objective=work.objective)


def _worker_input(service: ShortDramaService, work: WorkOrder, context: ContextPack) -> dict[str, Any]:
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {
        "project_id": context.project_id}, "reason": "Continue using the saved inspection geometry."})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(service.production_manager.runtime.store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Prepare this local edit."))
    assert len(client.payloads) == 1
    assert "agent.get_tool_attempt" in client.payloads[0]["tool_schemas"]
    return client.payloads[0]


@pytest.mark.parametrize("after_sequence", [52, 53, 54, 55])
def test_actual_scopes_reach_resumed_worker_and_exact_attempt_tool(
    tmp_path: Path, actual: dict[str, Any], after_sequence: int,
) -> None:
    # These are the actual saved requests/results, including every polygon point.
    turns = [*actual["prior_turns"], *[
        turn for turn in actual["turns"] if turn["sequence"] <= after_sequence]]
    service = ShortDramaService(database_path=tmp_path / "runtime.sqlite3", workspace_root=tmp_path / "files")
    work, context = _save_work(service, actual["project_id"], turns)
    before = deepcopy(work.specialist_turns)
    resumed = ShortDramaService(database_path=service.repository.database_path, workspace_root=tmp_path / "files")
    saved = resumed.production_manager.runtime.store.get_work_order(work.id)
    assert saved is not None
    payload = _worker_input(resumed, saved, context)
    reviews = _reviews(payload["working_set"])
    assert len(reviews) == len(turns)
    by_sequence = {review["source_sequence"]: review for review in reviews}
    for turn in turns:
        state = by_sequence[turn["sequence"]]["state"]
        assert state["review_scope"] == {"regions": [], **turn["arguments"]["request"]}
        for field in ("edit_guide_suggestion", "issues", "summary", "recommended", "evidence_references"):
            assert state[field] == turn["result"]["output"][field]
        result = resumed.tool_registry.invoke("agent.get_tool_attempt", {
            "attempt_ref": state["attempt_ref"], "payload": "BOTH"})
        assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None
        assert result.output["arguments"] == turn["arguments"]
        assert result.output["result"] == turn["result"]
        assert state["attempt_ref"]["attempt_id"] == turn["id"]
    persisted = resumed.production_manager.runtime.store.get_work_order(work.id)
    assert persisted is not None and persisted.specialist_turns == before


@pytest.mark.parametrize("changed_field", ["review_focus", "shot_id", "canonical_reference_version_ids", "regions"])
def test_only_the_same_exact_selected_scope_replaces_its_previous_observation(
    actual: dict[str, Any], changed_field: str,
) -> None:
    first = deepcopy(actual["turns"][0])
    second = deepcopy(first)
    second.update(id="independent-review", sequence=60)
    replacements = {"review_focus": "Inspect another explicit purpose.", "shot_id": "another-shot",
        "canonical_reference_version_ids": ["different-exact-reference"],
        "regions": [{"region": {"x": .1, "y": .2, "width": .2, "height": .3}, "label": "detail"}]}
    second["arguments"]["request"][changed_field] = replacements[changed_field]
    refreshed = deepcopy(first)
    refreshed.update(id="same-scope-refresh", sequence=61)
    refreshed["arguments"]["request"]["regions"] = []  # Explicit default equals omission.
    refreshed["result"]["output"]["summary"] = "Latest result for the first precise scope."
    refreshed["result"]["output"]["edit_guide_suggestion"] = None
    reviews = _fold([first, second, refreshed])
    assert {review["source_sequence"] for review in reviews} == {60, 61}
    newest = next(review for review in reviews if review["source_sequence"] == 61)
    assert newest["state"]["summary"] == refreshed["result"]["output"]["summary"]
    assert "edit_guide_suggestion" not in newest["state"]  # No old geometry survives a new null.
    assert newest["state"]["attempt_ref"]["attempt_id"] == "same-scope-refresh"


def test_reference_order_defaults_and_failed_reviews_keep_their_actual_meaning(actual: dict[str, Any]) -> None:
    first = deepcopy(actual["turns"][0])
    first["arguments"]["request"] = {"canonical_reference_version_ids": ["room", "prop"]}
    reordered = deepcopy(first)
    reordered.update(id="different-reference-order", sequence=60)
    reordered["arguments"]["request"]["canonical_reference_version_ids"] = ["prop", "room"]
    failed = deepcopy(first)
    failed.update(id="failed-review", sequence=61, status="COMPLETED")
    failed["result"]["status"] = "FAILED"
    failed["result"]["output"] = {"summary": "Failure is not a new observed finding."}
    assert len(_fold([first, reordered, failed])) == 2
    first["arguments"].pop("request")
    repeated = deepcopy(first)
    repeated.update(id="explicit-defaults", sequence=60)
    repeated["arguments"]["request"] = {"shot_id": None, "review_focus": None,
        "canonical_reference_version_ids": [], "regions": []}
    assert len(_fold([first, repeated])) == 1


def test_actual_tool_results_with_two_scopes_survive_an_unrelated_tool_and_restart(tmp_path: Path) -> None:
    service, language, pid, aid, version = _setup(tmp_path)
    turns: list[dict[str, Any]] = []
    for sequence, focus in enumerate(("Check the visible partition geometry.", "Check the visible hand and pen geometry."), 1):
        arguments = {"project_id": pid, "asset_id": aid, "version_id": version.id,
            "request": {"review_focus": focus}}
        result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
        assert result.status == ToolCallStatus.SUCCEEDED
        turns.append(SpecialistTurnRecord(id=f"qa-{sequence}", sequence=sequence,
            tool_name="qa.inspect_asset_version", arguments=arguments, reason=focus,
            status=SpecialistTurnStatus.COMPLETED, result=result.model_dump(mode="json")).model_dump(mode="json"))
    assert language.calls == 2
    read_arguments = {"project_id": pid}
    read = service.tool_registry.invoke("artifact.read_current", read_arguments)
    assert read.status == ToolCallStatus.SUCCEEDED
    turns.append(SpecialistTurnRecord(id="unrelated-read", sequence=3, tool_name="artifact.read_current",
        arguments=read_arguments, reason="Read current content.", status=SpecialistTurnStatus.COMPLETED,
        result=read.model_dump(mode="json")).model_dump(mode="json"))
    work, context = _save_work(service, pid, turns)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=tmp_path / "workspace")
    saved = reopened.production_manager.runtime.store.get_work_order(work.id)
    assert saved is not None
    reviews = _reviews(_worker_input(reopened, saved, context)["working_set"])
    assert {review["state"]["review_scope"]["review_focus"] for review in reviews} == {
        turn["arguments"]["request"]["review_focus"] for turn in turns[:2]}
    assert all(review["state"]["edit_guide_suggestion"]["source_asset_version_id"] == version.id for review in reviews)
    assert language.calls == 2


def test_large_review_collection_uses_existing_exact_snapshot_pagination(tmp_path: Path, actual: dict[str, Any]) -> None:
    service = ShortDramaService(database_path=tmp_path / "runtime.sqlite3", workspace_root=tmp_path / "files")
    work, context = _save_work(service, actual["project_id"], actual["turns"])
    working = _specialist_working_set(work, [], project_id=context.project_id).model_dump(mode="json")
    compiler = ModelInputCompiler(service.production_manager.runtime.store,
        budget=ContextBudget(window_tokens=12000, compact_at_tokens=5000, target_tokens=3000, envelope_tokens=100))
    prepared = compiler.prepare(run_id=context.run_id, project_id=context.project_id,
        instructions="Keep the exact observed scope.", payload={"working_set": working}, max_output_tokens=500)
    payload = json.loads(prepared.input_text)
    marker = payload["working_set"]["entities"]
    assert prepared.offloaded_blocks > 0 and "context_ref" in marker
    output: list[str] = []
    offset = 0
    while True:
        page = service.tool_registry.invoke("agent.read_context", {"project_id": context.project_id,
            "run_id": context.run_id, "snapshot_id": marker["context_ref"], "pointer": marker["pointer"],
            "offset": offset, "limit": 12000})
        assert page.status == ToolCallStatus.SUCCEEDED and page.output is not None
        output.append(str(page.output["content"]))
        next_offset = page.output["next_offset"]
        if next_offset is None:
            break
        assert isinstance(next_offset, int)
        offset = next_offset
    restored = json.loads("".join(output))
    assert restored == working["entities"]
    assert len(restored) == 4
    for review in restored:
        exact = service.tool_registry.invoke("agent.get_tool_attempt", {
            "attempt_ref": review["state"]["attempt_ref"], "payload": "RESULT"})
        assert exact.status == ToolCallStatus.SUCCEEDED and exact.output is not None
        assert pointer_value(exact.output, "/result/output/edit_guide_suggestion") == review["state"]["edit_guide_suggestion"]
    assert prepared.input_tokens == count_text_tokens("Keep the exact observed scope.") + count_text_tokens(prepared.input_text)
