"""Actual paired QA -> durable Worker, shared observation and completion handoff."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from short_drama.agent_runtime import AgentRuntimeStore, ArtifactRef, ContextPack
from short_drama.agent_runtime.media_handoffs import asset_inspection_scope, observed_media_handoffs
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import AgentResultStatus, DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.production_manager import AgentWorkOrderResultRequest, AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection_pair import setup_pair
from tests.test_harness_tool_context import ScriptedClient


def paired_work(tmp_path: Path):
    service, provider, client, asset, _base, _mask, task = setup_pair(tmp_path)
    # Synthetic coordinate wiring, not a new visual judgement of the real S11.
    quad = [{"x": .1, "y": .1}, {"x": .9, "y": .1}, {"x": .9, "y": .9}, {"x": .1, "y": .9}]
    for report in (client.response, client.response["provider_output_review"]):
        report["edit_guide_suggestion"] = {"regions": [{"polygon": quad, "label": "Observed connected part"}],
                                            "descriptor": "Synthetic observed coordinates for handoff"}
    client.response["provider_output_review"].update(recommended=True, score=4.8, issues=[],
        summary="Synthetic raw positive judgement for checking independent verdict retention.")
    shot = service.get_project(asset.project_id).document.shots[0]
    args = {"project_id": asset.project_id, "asset_id": asset.id, "version_id": task.output_version_id,
        "request": {"compare_provider_output": True, "shot_id": shot.id,
                    "intended_reference_role": "COMPOSITION", "review_focus": "Check the visible connection."}}
    result = service.tool_registry.invoke("qa.inspect_asset_version", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=asset.project_id, objective="Observe exact saved images, no video.", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="asset_builder",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER,
        objective="Inspect both saved candidates, retaining limitations; do not adopt either.", why_now="Offline same-call QA replay")
    work.status = DelegationStatus.IN_PROGRESS
    work.specialist_turns = [SpecialistTurnRecord(id="paired-qa", sequence=1, tool_name="qa.inspect_asset_version",
        arguments=cast(dict[str, Any], args), reason="Compare the actual saved edit outputs.",
        status=SpecialistTurnStatus.COMPLETED, result=result.model_dump(mode="json"))]
    runtime.store.save_work_order(work)
    return service, provider, client, task, work, result


def test_actual_pair_survives_restart_worker_and_exact_attempt_without_merging_verdicts(tmp_path: Path) -> None:
    service, provider, qa, task, work, result = paired_work(tmp_path)
    assert result.output is not None
    before = service.repository.get_project(task.project_id)
    original = deepcopy(work.specialist_turns)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    store = AgentRuntimeStore(reopened.agent_runtime_store.database_path)
    saved = store.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": task.project_id},
        "reason": "Synthetic next decision; the Tool is not executed."})])
    worker = LanguageSpecialistAgent(client, reopened.tool_registry)
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=saved, context=ContextPack(run_id=work.parent_run_id,
        project_id=task.project_id, objective=work.objective), system_prompt="Use the exact observed result."))
    rows = [row for row in client.payloads[0]["working_set"]["entities"] if row["kind"] == "asset_review"]
    assert len(rows) == 2
    expected = {task.output_version_id: result.output, task.provider_output_version_id: result.output["provider_output_review"]}
    for row in rows:
        state = row["state"]
        report = expected[state["version_id"]]
        for field in ("recommended", "score", "summary", "issues", "shot_revision", "edit_guide_suggestion"):
            assert state[field] == report[field]
        assert state["edit_guide_suggestion"]["source_asset_version_id"] == state["version_id"]
        assert state["review_scope"]["intended_reference_role"] == "COMPOSITION"
        exact = reopened.tool_registry.invoke("agent.get_tool_attempt", {"attempt_ref": state["attempt_ref"], "payload": "RESULT"})
        assert exact.status == ToolCallStatus.SUCCEEDED and exact.output is not None
        assert pointer_value(exact.output["result"], state["source_output_pointer"]) == report
    assert {row["state"]["recommended"] for row in rows} == {True, False}
    restored = store.get_work_order(work.id)
    assert restored is not None and restored.specialist_turns == original
    assert reopened.repository.get_project(task.project_id) == before
    assert qa.calls == 1 and len(provider.calls) == 1


def test_actual_pair_shared_pages_and_saved_sequences_keep_two_exact_observations(tmp_path: Path) -> None:
    service, _provider, qa, task, work, result = paired_work(tmp_path)
    assert result.output is not None
    store = AgentRuntimeStore(service.agent_runtime_store.database_path)
    rows, refs = observed_media_handoffs(store, run_id=work.parent_run_id, project_id=task.project_id,
        selected_ids={cast(str, task.provider_output_version_id)}, token_budget=0)
    assert len(rows) == 1
    raw = cast(dict[str, Any], rows[0])
    assert raw["asset_version_id"] == task.provider_output_version_id
    assert raw["source_output_pointer"] == "/output/provider_output_review"
    page = service.tool_registry.invoke("agent.read_context", raw["saved_observation"]["read"]["arguments"])
    assert page.status == ToolCallStatus.SUCCEEDED and page.output is not None
    # The real source may span pages; the existing full snapshot remains exact.
    source = store.get_context_snapshot(raw["saved_observation"]["context_ref"], work.parent_run_id)
    assert source == {"arguments": work.specialist_turns[0].arguments, "result": work.specialist_turns[0].result}
    assert pointer_value(source, "/result" + raw["source_output_pointer"]) == result.output["provider_output_review"]
    assert {task.output_version_id, task.provider_output_version_id} <= {ref.artifact_id for ref in refs}
    receiver = service.agent_runtime.create_work_order(work.parent_run_id, sender="leader", recipient_role="asset_builder",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER,
        objective="Use the selected raw observation", why_now="Independent receiving scope",
        scope=[ArtifactRef(project_id=task.project_id, artifact_type="asset_version",
                           artifact_id=cast(str, task.provider_output_version_id), version=task.provider_output_version_number)])
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {"project_id": task.project_id},
        "reason": "Synthetic receiving decision only."})])
    agent = LanguageSpecialistAgent(client, service.tool_registry)
    agent.context_compiler = ModelInputCompiler(store)
    agent.execute(service.get_agent_work_order(receiver.id))
    visible = client.payloads[0]["working_set"]["media_observations"]
    assert len(visible) == 1 and visible[0]["result"]["score"] == 4.8
    complete = AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="Two observations saved; neither candidate was adopted.", saved_tool_result_sequences=[1])
    finished = service.submit_agent_work_order_result(work.id, complete).result
    saved = [row for row in finished.completion_receipts if row.get("observation_kind") == "image_inspection"]
    assert len(saved) == 2
    by_id = {row["asset_version_id"]: row for row in saved}
    assert by_id[task.output_version_id]["result"]["recommended"] is False
    assert by_id[task.provider_output_version_id]["result"]["recommended"] is True
    assert {row["asset_version_id"] for row in saved} == {task.output_version_id, task.provider_output_version_id}
    assert qa.calls == 1


@pytest.mark.parametrize("field,value", [("compare_provider_output", False), ("intended_reference_role", None)])
def test_new_scope_defaults_equal_omission_without_changing_request(field: str, value: object) -> None:
    request = cast(dict[str, Any], {field: value})
    prior = deepcopy(request)
    assert asset_inspection_scope(request) == asset_inspection_scope({})
    assert request == prior


@pytest.mark.parametrize("change", ["role", "same_scope", "failed", "no_child"])
def test_pair_scope_updates_only_actual_reports(tmp_path: Path, change: str) -> None:
    _service, _provider, _qa, task, work, _result = paired_work(tmp_path)
    turn = work.specialist_turns[0].model_copy(deep=True)
    turn.id, turn.sequence = "next-inspection", 2
    assert turn.result is not None
    output = cast(dict[str, Any], turn.result["output"])
    if change == "role":
        cast(dict[str, Any], turn.arguments["request"])["intended_reference_role"] = "FIRST_FRAME"
    elif change == "failed":
        turn.result["status"] = "FAILED"
    elif change == "no_child":
        output["provider_output_review"] = None
        work.specialist_turns = []  # Legacy/non-comparison result does not invent a raw report.
    else:
        output["provider_output_review"]["edit_guide_suggestion"] = None
    work.specialist_turns.append(turn)
    reviews = [row for row in _specialist_working_set(work, []).entities if row.kind == "asset_review"]
    assert len(reviews) == (4 if change == "role" else 1 if change == "no_child" else 2)
    if change == "same_scope":
        raw = next(row for row in reviews if row.state["version_id"] == task.provider_output_version_id)
        assert raw.source_sequence == 2 and "edit_guide_suggestion" not in raw.state
    if change == "failed":
        assert all(row.source_sequence == 1 for row in reviews)
