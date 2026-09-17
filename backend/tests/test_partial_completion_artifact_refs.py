"""Film 1: saved refs visible inside PARTIAL receipts must be valid plan inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import (
    ActionKind, AgentResultStatus, AgentRuntimeStore, ArtifactRef, ArtifactSummary,
    ContextPack, DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder,
)
from short_drama.agent_runtime.context import ContextCompiler
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.production_manager import (
    AgentPlanProposal, AgentWorkOrderResultRequest, LanguageProductionPlanner, ProductionManagerError,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from tests.test_agent_runtime import FakeArtifactResolver
from tests.test_harness_tool_context import ScriptedClient


def _partial_context(
    tmp_path: Path, *, select_saved: bool = True, cancel_source: bool = False,
) -> tuple[ShortDramaService, WorkOrder, ContextPack, list[ArtifactRef], ArtifactRef]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    project_id = document.project.id
    document.editorial_timeline = {"revision": 5, "project": {"timelines": [{"id": "edit-current"}]}}
    service.repository.save_project(document)
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=project_id, root_agent="leader", objective="Assemble saved ending; no new media.")
    old = ArtifactRef(project_id=project_id, artifact_type="clip_group", artifact_id="cg-dby-04", version=1)
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="ending-worker",
        production_skill_role=ProductionSkillRole.DIRECTOR, objective="Save the ending and hand off remaining editing.",
        why_now="The ending source is ready.", scope=[old])
    runtime.begin_action(run.id, kind=ActionKind.DELEGATION, agent_id="ending-worker",
        input_summary=work.objective, work_order_id=work.id, artifact_refs=[old])
    # Preserve the two incident ref IDs and version; only project ownership is
    # remapped to an isolated fixture. These are saved Tool records, not media QA.
    refs = [
        ArtifactRef(project_id=project_id, artifact_type="take", artifact_id="take-task-a7c79131e8fb405aaa2b5200711318c2"),
        ArtifactRef(project_id=project_id, artifact_type="shot", artifact_id="shot-dby-05", version=3),
    ]
    historical = ArtifactRef(project_id=project_id, artifact_type="editorial_timeline", artifact_id="edit-current", version=4)
    for sequence, tool_name, ref in [(60, "video.inspect_take", refs[0]), (67, "shot.patch_storyboard", refs[1]),
                                      (70, "timeline.read_editorial", historical)]:
        work.specialist_turns.append(SpecialistTurnRecord(
            id=f"saved-turn-{sequence}", sequence=sequence, tool_name=tool_name,
            arguments={"project_id": project_id}, reason="Preserve this successful saved source.",
            status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": tool_name, "status": "SUCCEEDED", "output": {},
                    "artifact_refs": [ref.model_dump(mode="json")], "warnings": [], "error": None},
        ))
    runtime.store.save_work_order(work)
    request = AgentWorkOrderResultRequest(
        status=AgentResultStatus.PARTIAL, concise_summary="Media and design saved; assembly remains.",
        saved_tool_result_sequences=[60, 67, 70] if select_saved else [],
        # A matching-looking object inside an Agent-authored body is not a receipt.
        deliverable={"claimed_refs": [ref.model_dump(mode="json") for ref in refs],
                     "completion_receipts": [{"kind": "saved_tool_result", "current_artifact_refs": [ref.model_dump(mode="json") for ref in refs]}]},
        unresolved_questions=["Complete the remaining assembly without claiming it already succeeded."],
    )
    result = service.submit_agent_work_order_result(work.id, request).result
    assert result.status == AgentResultStatus.PARTIAL
    if cancel_source:
        work = runtime.store.get_work_order(work.id)
        assert work is not None
        work.status = DelegationStatus.CANCELLED
        runtime.store.save_work_order(work)
    reopened = AgentRuntimeStore(tmp_path / "studio.sqlite3")
    # As in the incident, other bounded read views occupy the artifact inventory;
    # the exact new refs are visible in the recent PARTIAL result's receipts.
    resolver = FakeArtifactResolver([ArtifactSummary(ref=old, summary="Earlier group input")])
    context = ContextCompiler(reopened, resolver).compile(run.id, requested_artifact_refs=[old], max_artifacts=1)
    assert {item.ref.identity() for item in context.artifacts} == {old.identity()}
    assert all(ref not in action.artifact_refs for action in context.recent_actions for ref in refs)
    return service, work, context, refs, historical


def _plan(refs: list[ArtifactRef]) -> str:
    return json.dumps({"summary": "Use the saved ending for the remaining edit.", "steps": [{
        "id": "assemble", "title": "Assemble ending", "kind": "DELEGATION", "assigned_agent": "editor",
        "production_skill_role": "DIRECTOR", "production_prompt_stage": "POST",
        "instructions": "Use these exact sources; assembly and quality review remain pending.",
        "artifact_refs": [ref.model_dump(mode="json") for ref in refs],
    }]})


def _propose(service: ShortDramaService, context: ContextPack, client: ScriptedClient) -> AgentPlanProposal:
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(service.agent_runtime.store)
    return planner.propose_plan(context=context, skill_prompt="", method_skills=[], tools=[],
        current_plan=None, reason="Continue after the partial saved delivery.")


@pytest.mark.parametrize("cancel_source", [False, True])
def test_persisted_partial_receipts_are_referenceable_in_one_leader_call_after_restart(
    tmp_path: Path, cancel_source: bool,
) -> None:
    service, work, context, refs, _historical = _partial_context(tmp_path, cancel_source=cancel_source)
    store = service.agent_runtime.store
    before = store.get_agent_result(work.id)
    work_before = store.get_work_order(work.id)
    client = ScriptedClient([_plan(refs)])

    proposal = _propose(service, context, client)

    assert proposal.steps[0].artifact_refs == refs
    assert len(client.payloads) == 1
    assert "plan_validation_feedback" not in client.payloads[0]
    working_set = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
    assert working_set["completion_handoffs"] == []  # PARTIAL is not completed work.
    assert store.get_agent_result(work.id) == before
    assert before is not None and before.status == AgentResultStatus.PARTIAL
    assert store.get_work_order(work.id) == work_before
    for receipt in before.completion_receipts:
        if receipt.get("kind") == "saved_tool_result":
            marker = cast(dict[str, JsonValue], receipt["saved_result"])
            saved = store.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id)
            assert saved is not None


@pytest.mark.parametrize("change", ["invented_id", "foreign_project", "wrong_type", "future_version"])
def test_partial_receipt_does_not_authorize_unseen_identity_or_version(tmp_path: Path, change: str) -> None:
    service, _work, context, refs, _historical = _partial_context(tmp_path)
    ref = refs[1]
    field, value = {"invented_id": ("artifact_id", "invented-shot"), "foreign_project": ("project_id", "other-project"),
                    "wrong_type": ("artifact_type", "take"), "future_version": ("version", 4)}[change]
    invalid = ref.model_copy(update={field: value})
    client = ScriptedClient([_plan([invalid]), _plan([invalid])])
    with pytest.raises(ProductionManagerError, match="repeated unresolved context references"):
        _propose(service, context, client)
    assert len(client.payloads) == 2
    feedback = cast(dict[str, JsonValue], client.payloads[1]["plan_validation_feedback"])
    assert feedback["kind"] == "UNAVAILABLE_ARTIFACT_REF"


def test_historical_timeline_receipt_is_not_promoted_to_current_available_ref(tmp_path: Path) -> None:
    service, work, context, _refs, historical = _partial_context(tmp_path)
    result = service.agent_runtime.store.get_agent_result(work.id)
    assert result is not None
    receipt = next(row for row in result.completion_receipts if row.get("sequence") == 70)
    assert receipt["artifact_refs"] == [historical.model_dump(mode="json")]
    assert receipt["current_artifact_refs"] == []
    client = ScriptedClient([_plan([historical]), _plan([historical])])
    with pytest.raises(ProductionManagerError, match="repeated unresolved context references"):
        _propose(service, context, client)


def test_unselected_saved_tool_and_matching_deliverable_text_are_not_accepted(tmp_path: Path) -> None:
    service, work, context, refs, _historical = _partial_context(tmp_path, select_saved=False)
    result = service.agent_runtime.store.get_agent_result(work.id)
    assert result is not None and result.completion_receipts == []
    assert result.deliverable["claimed_refs"] == [ref.model_dump(mode="json") for ref in refs]
    client = ScriptedClient([_plan(refs), _plan(refs)])
    with pytest.raises(ProductionManagerError, match="repeated unresolved context references"):
        _propose(service, context, client)
