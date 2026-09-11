"""Lossless cross-WorkOrder handoff for failed Tool candidates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.agent_runtime import (
    ActionKind,
    AgentResultStatus,
    DelegationStatus,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    ToolAttemptRef,
    PlanStep,
    WorkOrderNextOwner,
)
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentRunAdvanceRequest,
    AgentWorkOrderResultRequest,
    ProductionManagerError,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus


@pytest.mark.parametrize("status", [DelegationStatus.NEEDS_ATTENTION, DelegationStatus.COMPLETED, DelegationStatus.CANCELLED])
def test_successful_unsaved_compilation_survives_handoff_and_supersedes_failed_refs(tmp_path: Path, status: DelegationStatus) -> None:
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    project_id = service.initialize_fixture().document.project.id
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=project_id, objective="Save the accepted prompt.", root_agent="leader")
    source = runtime.create_work_order(run.id, sender="leader", recipient_role="compiler",
        objective="Compile one unit.", why_now="Its source is ready.")
    target = {"generation_plan_version_id": "gp-v3", "generation_unit_id": "unit-3"}
    for index, outcome in enumerate(["FAILED", "SUCCEEDED"], start=1):
        source.specialist_turns.append(SpecialistTurnRecord(
            id=f"compile-{index}", sequence=index, tool_name="route.compile_h3_prompt",
            arguments={"project_id": project_id, "request": target}, reason="Compile the unit.",
            status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "route.compile_h3_prompt", "status": outcome,
                    "output": {"compiled_prompt": "ACCEPTED_EXACT_PROMPT", "media_inputs": [], **target} if outcome == "SUCCEEDED" else None,
                    "error": {"message": "The first draft exceeded the limit."} if outcome == "FAILED" else None,
                    "artifact_refs": [], "warnings": []},
        ))
    source.status = status
    source.next_owner = WorkOrderNextOwner.LEADER_REPLAN if status == DelegationStatus.NEEDS_ATTENTION else None
    runtime.store.save_work_order(source)
    runtime.begin_action(run.id, kind=ActionKind.DELEGATION, agent_id="compiler", input_summary="Compile the unit.", work_order_id=source.id)
    context = runtime.context(run.id)
    assert [(ref.attempt_id, ref.outcome_status) for ref in context.tool_attempt_refs] == [("compile-2", "SUCCEEDED")]
    assert "ACCEPTED_EXACT_PROMPT" not in json.dumps(context.model_context_dump())
    recovered = service.production_manager._invoke_scoped_tool(project_id, "agent.get_tool_attempt", {
        "attempt_ref": context.tool_attempt_refs[0].model_dump(mode="json"), "payload": "RESULT",
    }, agent_run_id=run.id)
    assert recovered.status == ToolCallStatus.SUCCEEDED
    assert "ACCEPTED_EXACT_PROMPT" in json.dumps(recovered.output)
    source.specialist_turns.append(SpecialistTurnRecord(
        id="save-route", sequence=3, tool_name="route.plan_h3", arguments={"project_id": project_id},
        reason="Save the accepted result.", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "route.plan_h3", "status": "SUCCEEDED", "output": {"plan": {
            "input_generation_plan_version_id": "gp-v3", "decisions": [{"generation_unit_id": "unit-3"}],
        }}, "artifact_refs": [], "warnings": [], "error": None},
    ))
    runtime.store.save_work_order(source)
    assert runtime.context(run.id).tool_attempt_refs == []


def test_failed_tool_candidate_is_addressable_without_default_payload_replay(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    project_id = service.initialize_fixture().document.project.id
    runtime = service.agent_runtime
    run = runtime.create_run(
        project_id=project_id,
        objective="Repair the failed asset-plan candidate without rebuilding it.",
        root_agent="production-manager",
    )
    source = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-planner",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Write the complete asset plan.",
        why_now="The storyboard is ready.",
    )
    candidate_marker = "exact-failed-candidate-"
    candidate = {
        "project_id": project_id,
        "request": {
            "content": {
                "items": [
                    {
                        "id": "asset-item-hero",
                        "description": candidate_marker + ("x" * 100_000),
                    }
                ]
            }
        },
    }
    source.specialist_turns.append(
        SpecialistTurnRecord(
            id="specialist-turn-failed-asset-plan",
            sequence=1,
            tool_name="artifact.replace_asset_plan",
            arguments=candidate,
            reason="Persist the complete candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": ToolCallStatus.FAILED.value,
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {
                    "kind": "AssetPlanValidationError",
                    "message": "One item needs a local repair.",
                },
            },
        )
    )
    source.status = DelegationStatus.NEEDS_ATTENTION
    source.next_owner = WorkOrderNextOwner.LEADER_REPLAN
    runtime.store.save_work_order(source)
    action = runtime.begin_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="asset-planner",
        input_summary="Write the complete asset plan.",
        work_order_id=source.id,
    )
    runtime.fail_action(
        action.id,
        error_message="The candidate needs a targeted repair.",
    )

    leader_context = runtime.context(run.id)
    assert len(leader_context.tool_attempt_refs) == 1
    attempt_ref = leader_context.tool_attempt_refs[0]
    assert attempt_ref.attempt_id == "specialist-turn-failed-asset-plan"
    serialized_context = json.dumps(
        leader_context.model_context_dump(),
        ensure_ascii=False,
    )
    assert candidate_marker not in serialized_context
    assert len(serialized_context) < 24_000

    stale_attempt_ref = attempt_ref.model_copy(
        update={
            "turn_status": SpecialistTurnStatus.REQUESTED,
            "outcome_status": ToolCallStatus.INVALID_ARGUMENTS.value,
            "has_arguments": False,
            "has_result": False,
        }
    )

    service.production_manager._replace_plan(
        run.id,
        AgentPlanProposal(
            summary="Repair the persisted candidate by reference.",
            steps=[
                PlanStep(
                    id="repair-failed-asset-plan",
                    title="Repair the failed asset plan",
                    kind=ActionKind.DELEGATION,
                    assigned_agent="asset-plan-repairer",
                    production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
                    production_prompt_stage=ProductionPromptStage.ASSETS,
                    instructions="Repair only the rejected asset-plan fields.",
                    arguments={
                        "why_now": (
                            "The failed exact candidate is available by reference."
                        ),
                        "tool_attempt_refs": [
                            stale_attempt_ref.model_dump(mode="json")
                        ],
                    },
                )
            ],
        ),
    )
    advanced = service.production_manager.advance(
        run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )
    assert len(advanced.created_work_order_ids) == 1
    repair = runtime.store.get_work_order(advanced.created_work_order_ids[0])
    assert repair is not None
    repair_context = runtime.work_order_context(repair.id)
    assert repair_context.tool_attempt_refs == [attempt_ref]
    assert candidate_marker not in json.dumps(
        repair_context.model_context_dump(),
        ensure_ascii=False,
    )

    result = service.production_manager._invoke_scoped_tool(
        project_id,
        "agent.get_tool_attempt",
        {
            "attempt_ref": stale_attempt_ref.model_dump(mode="json"),
            "payload": "ARGUMENTS",
        },
        agent_run_id=run.id,
        work_order_id=repair.id,
    )
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert result.output["attempt_ref"] == attempt_ref.model_dump(mode="json")
    assert result.output["arguments"] == candidate
    assert result.output["result"] is None

    self_result = service.production_manager._invoke_scoped_tool(
        project_id,
        "agent.get_tool_attempt",
        {
            "attempt_ref": stale_attempt_ref.model_dump(mode="json"),
            "payload": "BOTH",
        },
        agent_run_id=run.id,
        work_order_id=source.id,
    )
    assert self_result.status == ToolCallStatus.SUCCEEDED
    assert self_result.output is not None
    assert self_result.output["attempt_ref"] == attempt_ref.model_dump(mode="json")
    assert self_result.output["arguments"] == candidate
    self_attempt_result = self_result.output["result"]
    assert isinstance(self_attempt_result, dict)
    assert self_attempt_result["status"] == ToolCallStatus.FAILED.value

    unrelated = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="unrelated-worker",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Do unrelated work.",
        why_now="This Worker was not handed the failed candidate.",
    )
    with pytest.raises(
        ProductionManagerError,
        match="explicitly handed to the current WorkOrder",
    ):
        service.production_manager._invoke_scoped_tool(
            project_id,
            "agent.get_tool_attempt",
            {
                "attempt_ref": attempt_ref.model_dump(mode="json"),
                "payload": "ARGUMENTS",
            },
            agent_run_id=run.id,
            work_order_id=unrelated.id,
        )

    wrong_tool_ref = stale_attempt_ref.model_copy(
        update={"tool_name": "artifact.create_asset_plan"}
    )
    with pytest.raises(
        ProductionManagerError,
        match="explicitly handed to the current WorkOrder",
    ):
        service.production_manager._invoke_scoped_tool(
            project_id,
            "agent.get_tool_attempt",
            {
                "attempt_ref": wrong_tool_ref.model_dump(mode="json"),
                "payload": "ARGUMENTS",
            },
            agent_run_id=run.id,
            work_order_id=source.id,
        )

    with pytest.raises(
        ProductionManagerError,
        match="explicitly handed to the current WorkOrder",
    ):
        service.production_manager._invoke_scoped_tool(
            project_id,
            "artifact.retry_asset_plan_attempt",
            {
                "project_id": project_id,
                "attempt_ref": attempt_ref.model_dump(mode="json"),
                "patches": [
                    {
                        "item_id": "asset-item-hero",
                        "op": "replace",
                        "path": "/description",
                        "value": "repaired",
                    }
                ],
            },
            agent_run_id=run.id,
            work_order_id=unrelated.id,
        )


def test_work_order_rejects_tool_attempt_ref_from_another_run(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    project_id = service.initialize_fixture().document.project.id
    first = service.agent_runtime.create_run(
        project_id=project_id,
        objective="First run.",
        root_agent="production-manager",
    )
    second = service.agent_runtime.create_run(
        project_id=project_id,
        objective="Second run.",
        root_agent="production-manager",
    )
    source = service.agent_runtime.create_work_order(
        first.id,
        sender="production-manager",
        recipient_role="worker",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Create a candidate.",
        why_now="Needed by the first run.",
    )
    source.specialist_turns.append(
        SpecialistTurnRecord(
            id="specialist-turn-first-run",
            sequence=1,
            tool_name="artifact.replace_asset_plan",
            arguments={"project_id": project_id},
            reason="Try the candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": ToolCallStatus.FAILED.value,
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {"kind": "Error", "message": "Rejected"},
            },
        )
    )
    source.status = DelegationStatus.NEEDS_ATTENTION
    source.next_owner = WorkOrderNextOwner.LEADER_REPLAN
    service.agent_runtime.store.save_work_order(source)
    attempt_ref = service.agent_runtime.context(first.id).tool_attempt_refs
    # No action points at the source yet, so the ordinary Leader context exposes
    # no handoff. Build the exact typed reference through the persisted contract.
    cross_run_ref = ToolAttemptRef(
        project_id=project_id,
        agent_run_id=first.id,
        work_order_id=source.id,
        attempt_id="specialist-turn-first-run",
        tool_name="artifact.replace_asset_plan",
        turn_status=SpecialistTurnStatus.COMPLETED,
        outcome_status=ToolCallStatus.FAILED.value,
        has_arguments=True,
        has_result=True,
    )
    assert attempt_ref == []
    with pytest.raises(ValueError, match="must belong to the AgentRun"):
        service.agent_runtime.create_work_order(
            second.id,
            sender="production-manager",
            recipient_role="worker",
            production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
            production_prompt_stage=ProductionPromptStage.ASSETS,
            objective="Improperly inherit another run's candidate.",
            why_now="This must be rejected.",
            tool_attempt_refs=[cross_run_ref],
        )


def test_repair_replan_preserves_source_attempt_after_successful_lookup(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    project_id = service.initialize_fixture().document.project.id
    runtime = service.agent_runtime
    run = runtime.create_run(
        project_id=project_id,
        objective="Repair the prior candidate without asking the user.",
        root_agent="production-manager",
        unattended=True,
    )
    source = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-planner",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Persist the original asset-plan candidate.",
        why_now="The current asset plan needs one repair.",
    )
    source.specialist_turns.append(
        SpecialistTurnRecord(
            id="specialist-turn-original-failure",
            sequence=1,
            tool_name="artifact.replace_asset_plan",
            arguments={
                "project_id": project_id,
                "request": {"content": {"items": [{"id": "asset-hero"}]}},
            },
            reason="Persist the candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": ToolCallStatus.FAILED.value,
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {
                    "kind": "AssetPlanValidationError",
                    "message": "The candidate needs a scoped repair.",
                },
            },
        )
    )
    source.status = DelegationStatus.NEEDS_ATTENTION
    source.next_owner = WorkOrderNextOwner.LEADER_REPLAN
    runtime.store.save_work_order(source)
    source_action = runtime.begin_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="asset-planner",
        input_summary=source.objective,
        work_order_id=source.id,
    )
    runtime.fail_action(
        source_action.id,
        error_message="The original candidate needs a repair.",
    )

    source_ref = runtime.context(run.id).tool_attempt_refs[0]
    repair = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-plan-repairer",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Inspect the old candidate and decide the next repair scope.",
        why_now="The exact failed candidate is addressable by reference.",
        tool_attempt_refs=[source_ref],
    )
    runtime.begin_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="asset-plan-repairer",
        input_summary=repair.objective,
        work_order_id=repair.id,
    )
    repair.specialist_turns.append(
        SpecialistTurnRecord(
            id="specialist-turn-read-original-failure",
            sequence=1,
            tool_name="agent.get_tool_attempt",
            arguments={
                "attempt_ref": source_ref.model_dump(mode="json"),
                "payload": "ARGUMENTS",
            },
            reason="Load the exact rejected candidate once.",
        )
    )
    repair.status = DelegationStatus.NEEDS_ATTENTION
    repair.next_owner = WorkOrderNextOwner.WORKER_TOOL
    runtime.store.save_work_order(repair)

    tool_step = service.production_manager.execute_work_order(repair.id)
    assert tool_step.result.status == AgentResultStatus.PARTIAL
    persisted_repair = runtime.store.get_work_order(repair.id)
    assert persisted_repair is not None
    assert len(persisted_repair.specialist_turns) == 1
    lookup_turn = persisted_repair.specialist_turns[0]
    assert lookup_turn.tool_name == "agent.get_tool_attempt"
    assert lookup_turn.result is not None
    assert lookup_turn.result["status"] == ToolCallStatus.SUCCEEDED.value

    returned = service.production_manager.submit_work_order_result(
        repair.id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.PARTIAL,
            concise_summary="The Leader must narrow the remaining repair scope.",
            recommended_next_actions=[
                "Replan from the exact candidate already loaded by this repair Worker."
            ],
            next_owner=WorkOrderNextOwner.LEADER_REPLAN,
        ),
    )

    assert returned.run.snapshot.run.unattended is True
    leader_context = runtime.context(run.id)
    assert leader_context.unattended is True
    assert leader_context.tool_attempt_refs == [source_ref]
    assert all(
        action.kind != ActionKind.USER_INPUT
        for action in runtime.store.list_actions(run.id)
    )
