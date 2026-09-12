"""Fresh5 saved draft -> actual catalog, persisted state and natural Leader input."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import (
    ActionKind, ActionRecord, ActionStatus, AgentResult, AgentResultStatus, AgentRuntime,
    AgentRuntimeStore, ArtifactRef, ContextCompiler, DelegationStatus, PlanStep,
    SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder, WorkOrderNextOwner,
)
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.partial_storyboard_context import partial_storyboard_context
from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.models import ProjectDocument
from short_drama.production_manager import ProductionManager
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient

REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/fresh5_storyboard_handoff_context.json").read_text())


def _no_provider(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("No Provider is permitted in the saved storyboard replay")


def saved_prefix(tmp_path: Path, through: int = 3) -> tuple[ShortDramaService, list[SpecialistTurnRecord], dict[str, str]]:
    service = ShortDramaService(database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=_no_provider, image_provider_factory=_no_provider, h3_client_factory=_no_provider)
    service.repository.save_project(ProjectDocument.model_validate(REAL["setup"]))
    turns: list[SpecialistTurnRecord] = []
    mapping: dict[str, str] = {}
    for source in REAL["turns"]:
        if source["sequence"] <= through:
            turns.append(saved_turn(service, source, mapping))
    return service, turns, mapping


def saved_turn(service: ShortDramaService, source: dict[str, Any], mapping: dict[str, str]) -> SpecialistTurnRecord:
    arguments = deepcopy(source["arguments"])
    request = arguments["request"]
    for key in ("draft_document_id", "base_version_id", "draft_version_id"):
        if key in request:
            request[key] = mapping.get(request[key], request[key])
    call = service.tool_registry.invoke(source["tool_name"], arguments)
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    output = cast(dict[str, Any], call.output)
    progress = output.get("progress")
    if progress is not None:
        old = source["result"]["output"]["progress"]
        mapping[old["draft_ref"]["artifact_id"]] = progress["draft_ref"]["artifact_id"]
        mapping[old["draft_version_id"]] = progress["draft_version_id"]
    return SpecialistTurnRecord(id=f"actual-t{source['sequence']}", sequence=source["sequence"],
        tool_name=source["tool_name"], arguments=arguments, reason=source["reason"],
        status=SpecialistTurnStatus.COMPLETED, result=call.model_dump(mode="json"))


def running_plan(service: ShortDramaService, turns: list[SpecialistTurnRecord]) -> tuple[str, WorkOrder]:
    runtime, store = service.agent_runtime, service.agent_runtime_store
    pid = REAL["setup"]["project"]["id"]
    run = runtime.create_run(project_id=pid, root_agent="leader", objective=REAL["leader_goal"]["original_request"])
    run.goal_summary = REAL["leader_goal"]["current_goal"]
    store.save_run(run)
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="episode_director",
        objective="Continue the current script's saved draft.", why_now="Original saved Fresh5 prefix")
    work.specialist_turns = turns
    work.status = DelegationStatus.IN_PROGRESS
    work.next_owner = WorkOrderNextOwner.WORKER_DECISION
    store.save_work_order(work)
    other = runtime.create_work_order(run.id, sender="leader", recipient_role="offline_trigger",
        objective="Synthetic independent completion control, no media claims", why_now="Natural partial completion trigger")
    other = store.save_agent_result(AgentResult(id="offline-complete", work_order_id=other.id,
        status=AgentResultStatus.COMPLETED, concise_summary="Offline independent completion only"))
    runtime.revise_plan(run.id, summary=REAL["leader_plan"]["summary"],
        steps=[PlanStep.model_validate(row) for row in REAL["leader_plan"]["steps"]],
        carried_work_order_ids=[work.id, other.id], updated_work_orders=[work, other])
    store.save_action(ActionRecord(id="observed-ref-selection-control", run_id=run.id,
        kind=ActionKind.DELEGATION, status=ActionStatus.SUCCEEDED, agent_id="offline_trigger",
        work_order_id=other.id, input_summary="Synthetic checkpoint containing the actual 16 observed refs",
        artifact_refs=[ArtifactRef.model_validate(row) for row in REAL["observed_refs"]]))
    restored = store.get_work_order(work.id)
    assert restored is not None
    return run.id, restored


def test_real_registry_prefix_survives_restart_and_natural_completion_planning(tmp_path: Path) -> None:
    service, turns, _ = saved_prefix(tmp_path)
    run_id, work = running_plan(service, turns)
    store = AgentRuntimeStore(service.agent_runtime_store.database_path)
    runtime = AgentRuntime(store, ContextCompiler(store, ProjectArtifactResolver(service.repository)))
    client = ScriptedClient([])
    pm = ProductionManager(runtime=runtime, tools=service.tool_registry, projects=service.repository,
                           language_client_resolver=lambda: client)
    assert client.payloads == []  # Saving/reopening does not wake a Leader.
    prepared = pm.prepare_completion_planning(run_id)
    assert prepared is not None and not prepared.local_plan_completed and not prepared.failed_plan_state
    client.outputs = iter([json.dumps({"summary": "Wait for the remaining authoring", "steps": [],
                                      "carried_work_order_ids": [work.id], "goal_complete": False})])
    proposal = pm.plan_after_completions(prepared)
    assert not proposal.goal_complete and proposal.steps == []
    payload = cast(dict[str, Any], client.payloads[0])
    assert len(payload["context"]["artifact_index"]) == 16
    partial = payload["working_set"]["partial_storyboard"]
    assert len(partial) == 1
    index = partial[0]
    progress = cast(dict[str, Any], turns[-1].result)["output"]["progress"]
    assert index["draft_ref"] == progress["draft_ref"]
    assert index["draft_version_id"] == progress["draft_version_id"]
    assert index["status"] == "SAVED_PARTIAL_DRAFT" and index["finalized"] is False
    assert index["remaining_script_line_count"] == 3
    assert [row["shot_id"] for row in index["shot_directory"]] == [f"shot-andingao-{n:02d}" for n in range(1, 8)]
    assert [row["shot_ids"] for row in index["clip_group_directory"]] == [
        ["shot-andingao-01", "shot-andingao-02", "shot-andingao-03"],
        ["shot-andingao-04", "shot-andingao-05"], ["shot-andingao-06", "shot-andingao-07"]]
    assert sum(row["duration_seconds"] for row in index["shot_directory"]) == 23.8
    readback = service.tool_registry.invoke("agent.get_tool_attempt", {
        "attempt_ref": index["saved_attempt"], "payload": "BOTH"})
    assert readback.status == ToolCallStatus.SUCCEEDED
    assert cast(dict[str, Any], readback.output)["result"] == turns[-1].result
    restored = store.get_work_order(work.id)
    assert restored is not None and restored.specialist_turns == turns
    assert restored.status != DelegationStatus.COMPLETED
    assert len(client.payloads) == 1
    assert payload["goal"] == REAL["leader_goal"]


@pytest.mark.parametrize("case", ["cancelled", "completed", "other_run", "old_plan", "other_project", "other_episode",
                                  "changed_bible", "changed_spec", "unknown_version", "failed_only", "not_saved"])
def test_partial_index_excludes_unproven_or_out_of_scope_drafts(tmp_path: Path, case: str) -> None:
    service, turns, _ = saved_prefix(tmp_path)
    run_id, work = running_plan(service, turns)
    store = service.agent_runtime_store
    document = service.repository.get_project(REAL["setup"]["project"]["id"])
    assert document is not None
    if case in {"cancelled", "completed"}:
        work.status = DelegationStatus.CANCELLED if case == "cancelled" else DelegationStatus.COMPLETED
    elif case == "old_plan":
        work.active_plan_revision_id = "old-plan"
    elif case == "other_run":
        other = service.agent_runtime.create_run(project_id=document.project.id, objective="Other run", root_agent="leader")
        run_id = other.id
    elif case == "other_project":
        document.project.id = "another-project"
    elif case == "other_episode":
        document.project.current_script_version_id = "different-episode-script"
    elif case == "changed_bible":
        document.project.current_bible_version_id = "different-bible"
    elif case == "changed_spec":
        document.production_spec_document_id = None
    elif case == "unknown_version":
        source = next(item for item in document.production_documents if item.kind == "storyboard.draft")
        source.current_version_id = "unseen-other-writer-version"
    elif case == "failed_only":
        work.specialist_turns[-1].result = {"status": "FAILED"}
    elif case == "not_saved":
        work.specialist_turns = []
    store.save_work_order(work)
    assert partial_storyboard_context(store, run_id=run_id, document=document) == ([], [])


def test_actual_later_append_updates_index_and_finalize_retires_partial(tmp_path: Path) -> None:
    service, turns, mapping = saved_prefix(tmp_path)
    run_id, work = running_plan(service, turns)
    for source in REAL["turns"]:
        if source["sequence"] <= 3:
            continue
        turn = saved_turn(service, source, mapping)
        # Another writer's newer saved version cannot relabel our old scope.
        document = service.repository.get_project(REAL["setup"]["project"]["id"])
        assert document is not None
        assert partial_storyboard_context(service.agent_runtime_store, run_id=run_id, document=document) == ([], [])
        work.specialist_turns.append(turn)
        service.agent_runtime_store.save_work_order(work)
        entries, _ = partial_storyboard_context(service.agent_runtime_store, run_id=run_id, document=document)
        if source["sequence"] == 8:
            assert entries == [] and len(document.shots) == 16
        else:
            entry = cast(dict[str, Any], entries[0])
            progress = cast(dict[str, Any], turn.result)["output"]["progress"]
            assert len(entry["shot_directory"]) == progress["committed_shot_count"]
            assert entry["draft_version_id"] == progress["draft_version_id"]


def test_partial_directory_uses_existing_exact_snapshot_pagination(tmp_path: Path) -> None:
    service, turns, _ = saved_prefix(tmp_path)
    run_id, _ = running_plan(service, turns)
    document = service.repository.get_project(REAL["setup"]["project"]["id"])
    assert document is not None
    entries, _ = partial_storyboard_context(service.agent_runtime_store, run_id=run_id, document=document)
    compiler = ModelInputCompiler(service.agent_runtime_store, budget=ContextBudget(
        window_tokens=4000, compact_at_tokens=2500, target_tokens=1200))
    prepared = compiler.prepare(run_id=run_id, project_id=document.project.id, instructions="Inspect the saved directory",
        payload={"working_set": {"partial_storyboard": entries}}, max_output_tokens=200, force_compact=True)
    output = json.loads(prepared.input_text)
    assert prepared.offloaded_blocks > 0
    marker = output["working_set"]["partial_storyboard"]
    if isinstance(marker, list):
        marker = marker[0]
        expected: JsonValue = entries[0]
    else:
        expected = entries
    read = service.tool_registry.invoke("agent.read_context", {"project_id": document.project.id,
        "run_id": run_id, "snapshot_id": marker["context_ref"], "pointer": marker["pointer"], "limit": 12000})
    assert read.status == ToolCallStatus.SUCCEEDED, read.error
    assert json.loads(cast(dict[str, Any], read.output)["content"]) == expected


def test_actual_group_replacement_updates_saved_partial_members_without_old_ids(tmp_path: Path) -> None:
    service, turns, _ = saved_prefix(tmp_path)
    run_id, work = running_plan(service, turns)
    original_turns = deepcopy(turns)
    prior = cast(dict[str, Any], turns[-1].result)["output"]["progress"]
    request = cast(dict[str, Any], deepcopy(turns[-1].arguments["request"]))
    group = request.pop("groups")[0]
    old_id = group["shots"][0]["id"]
    group["shots"][0]["id"] = old_id + "-replaced"
    group["clip_group"]["shot_ids"][0] = old_id + "-replaced"
    request.update(base_version_id=prior["draft_version_id"], group=group,
                   replaced_clip_group_id=group["clip_group"]["id"])
    arguments = {"project_id": turns[-1].arguments["project_id"], "request": request}
    call = service.tool_registry.invoke("shot.replace_storyboard_draft_group", arguments)
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    work.specialist_turns.append(SpecialistTurnRecord(id="replace-group", sequence=4,
        tool_name="shot.replace_storyboard_draft_group", arguments=arguments,
        reason="Replace only the saved group's first Shot identity.", status=SpecialistTurnStatus.COMPLETED,
        result=call.model_dump(mode="json")))
    service.agent_runtime_store.save_work_order(work)
    document = service.repository.get_project(REAL["setup"]["project"]["id"])
    assert document is not None
    reopened = AgentRuntimeStore(service.agent_runtime_store.database_path)
    rows, _ = partial_storyboard_context(reopened, run_id=run_id, document=document)
    row = cast(dict[str, Any], rows[0])
    assert row["draft_version_id"] == cast(dict[str, Any], call.output)["progress"]["draft_version_id"]
    assert row["clip_group_directory"][0]["shot_ids"][0] == old_id + "-replaced"
    assert old_id not in {shot["shot_id"] for shot in row["shot_directory"]}
    assert len(row["shot_directory"]) == 7 and len(row["clip_group_directory"]) == 3
    assert work.specialist_turns[:2] == original_turns
