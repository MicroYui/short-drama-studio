"""Recoverable, one-step-at-a-time execution for delegated specialist Agents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.agent_runtime import (
    ActionKind,
    AgentResultStatus,
    AgentRunStatus,
    DelegationStatus,
    PlanStep,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrderNextOwner,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    ProductionManagerError,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderTransportError,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
)
from short_drama.service import ShortDramaService


class SequencedLanguageClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.inputs: list[str] = []
        self.instructions: list[str] = []

    def validate_credentials(self) -> None:
        return None

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        self.instructions.append(instructions)
        self.inputs.append(input_text)
        if not self.responses:
            raise AssertionError("The specialist made an unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _service(
    root: Path,
    client: SequencedLanguageClient,
) -> ShortDramaService:
    return ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )


def _delegated_work_order(
    service: ShortDramaService,
    project_id: str,
) -> tuple[str, str]:
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Ask a specialist to inspect canonical project evidence.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one evidence-backed decision.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        production_prompt_stage=ProductionPromptStage.STORYBOARD,
                        assigned_agent="scene-director",
                        instructions="Inspect the project and return one decision.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    return run.snapshot.run.id, waiting.created_work_order_ids[0]


def test_asset_specialist_does_not_receive_storyboard_execution_manual(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The asset scope is already sufficient.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": [],
                    "decisions": ["Keep the current approved asset."],
                    "assumptions": [],
                    "unresolved_questions": [],
                    "evidence_refs": [],
                    "recommended_next_actions": [],
                }
            )
        ]
    )
    service = _service(tmp_path, client)
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Review only the current core asset scope.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one asset decision.",
                selected_method_skill_ids=[
                    "method.drama_series"
                ],
                steps=[
                    PlanStep(
                        id="asset-review",
                        title="Review asset",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
                        production_prompt_stage=ProductionPromptStage.ASSETS,
                        assigned_agent="worker-with-neutral-name",
                        instructions="Review the current core asset scope.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    service.execute_agent_work_order(waiting.created_work_order_ids[0])

    assert len(client.instructions) == 1
    instructions = client.instructions[0]
    assert "当前制片阶段：ASSETS" in instructions
    assert "## 资产检查" in instructions
    assert "多角色参考分别比对主要识别点" in instructions
    assert "## 表演与物理动作" not in instructions
    assert "shot.append_storyboard_ledger_segment" not in instructions
    assert "storyboard-confirm 前分组检查清单" not in instructions


def test_specialist_model_and_tool_turns_resume_from_persisted_work_order(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "tool_name": "project.get",
                    "arguments": {},
                    "reason": "Inspect the canonical project before deciding.",
                }
            ),
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The specialist used the persisted evidence.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": ["The project is available."],
                    "decisions": ["Continue from the canonical project."],
                    "assumptions": [],
                    "unresolved_questions": [],
                    "evidence_refs": [],
                    "recommended_next_actions": [],
                }
            ),
        ]
    )
    service = _service(tmp_path, client)
    project = service.initialize_fixture().document.project
    client.responses[0] = json.dumps(
        {
            "tool_name": "project.get",
            "arguments": {"project_id": project.id},
            "reason": "Inspect the canonical project before deciding.",
        }
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, work_order_id = _delegated_work_order(service, project.id)

    requested = service.execute_agent_work_order(work_order_id)

    assert requested.result.status == AgentResultStatus.PARTIAL
    assert len(client.inputs) == 1
    persisted_request = service.agent_runtime_store.get_work_order(work_order_id)
    assert persisted_request is not None
    assert persisted_request.status == DelegationStatus.NEEDS_ATTENTION
    assert len(persisted_request.specialist_turns) == 1
    assert (
        persisted_request.specialist_turns[0].status
        == SpecialistTurnStatus.REQUESTED
    )
    assert persisted_request.specialist_turns[0].result is None

    reopened = _service(tmp_path, client)
    tool_step = reopened.execute_agent_work_order(work_order_id)

    assert tool_step.result.status == AgentResultStatus.PARTIAL
    assert len(client.inputs) == 1
    persisted_result = reopened.agent_runtime_store.get_work_order(work_order_id)
    assert persisted_result is not None
    assert (
        persisted_result.specialist_turns[0].status
        == SpecialistTurnStatus.COMPLETED
    )
    assert persisted_result.specialist_turns[0].result is not None
    assert persisted_result.specialist_turns[0].result["status"] == "SUCCEEDED"

    reopened_again = _service(tmp_path, client)
    completed = reopened_again.execute_agent_work_order(work_order_id)

    assert completed.result.status == AgentResultStatus.COMPLETED
    assert completed.run.snapshot.run.id == run_id
    assert completed.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert len(client.inputs) == 2
    final_input = json.loads(client.inputs[-1])
    assert "tool_history" not in final_input
    assert final_input["working_set"]["total_tool_turns"] == 1
    assert (
        final_input["working_set"]["latest_handoff"]["result"]["status"]
        == "SUCCEEDED"
    )
    assert completed.result.evidence_refs[0].artifact_id == project.id

    settled = reopened_again.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED


@pytest.mark.parametrize("explicit_inputs", [False, True])
def test_delegation_preserves_structured_task_inputs_through_restart(
    tmp_path: Path,
    explicit_inputs: bool,
) -> None:
    client = SequencedLanguageClient([json.dumps({
        "status": "COMPLETED",
        "concise_summary": "All candidate mappings reached this worker.",
        "findings": ["The structured task data is complete."],
    })])
    service = _service(tmp_path, client)
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    bindings = [[f"work-{i}", f"asset-{i}", f"version-{i}"] for i in range(17)]
    inputs = {"candidate_bindings": bindings}
    arguments = {"task_inputs": inputs} if explicit_inputs else dict(inputs)
    arguments["why_now"] = "Recover already-produced candidates."
    run = service.create_agent_run(project.id, AgentRunCreateRequest(
        objective="Pass the selected mapping to its worker.",
        initial_plan=AgentPlanProposal(
            summary="Delegate a structured mapping.", goal_complete=True,
            steps=[PlanStep(
                id="recover", title="Recover candidates", kind=ActionKind.DELEGATION,
                assigned_agent="candidate-recovery", production_skill_role=ProductionSkillRole.ASSET_BUILDER,
                production_prompt_stage=ProductionPromptStage.ASSETS,
                instructions="Inspect the supplied candidate mapping.", arguments=arguments,
            )],
        ),
    ))
    advanced = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_id = advanced.created_work_order_ids[0]
    reopened = _service(tmp_path, client)
    persisted = reopened.agent_runtime_store.get_work_order(work_id)
    assert persisted is not None
    assert persisted.task_inputs == inputs
    reopened.execute_agent_work_order(work_id)
    payload = json.loads(client.inputs[0])
    assert payload["work_order"]["task_inputs"] == inputs
    assert "why_now" not in payload["work_order"]["task_inputs"]
    assert "task_inputs" not in payload["work_order"]["task_inputs"]


def test_transport_interruption_resumes_same_worker_with_landed_asset(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = _service(tmp_path, client)
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, work_order_id = _delegated_work_order(service, project.id)
    before_assets = len(service.get_project(project.id).document.assets)
    plan_id = service.agent_runtime.snapshot(run_id).run.current_plan_revision_id
    client.responses.extend([
        json.dumps({
            "tool_name": "artifact.create_asset",
            "arguments": {
                "project_id": project.id,
                "request": {
                    "kind": "REFERENCE",
                    "name": "Recovery candidate",
                    "descriptor": "A persisted candidate must survive an interrupted decision.",
                },
            },
            "reason": "Create the requested asset once.",
        }),
        LanguageProviderTransportError("generation stream was interrupted"),
        json.dumps({
            "status": "COMPLETED",
            "concise_summary": "The candidate was already persisted before interruption.",
            "findings": ["Reuse the existing candidate; no new create call is needed."],
        }),
    ])
    service.execute_agent_work_order(work_order_id)
    service.execute_agent_work_order(work_order_id)
    landed = service.get_project(project.id).document.assets
    assert len(landed) == before_assets + 1
    candidate = next(asset for asset in landed if asset.name == "Recovery candidate")

    with pytest.raises(ProductionManagerError, match="stream was interrupted"):
        service.execute_agent_work_order(work_order_id)

    interrupted = service.agent_runtime_store.get_work_order(work_order_id)
    assert interrupted is not None
    assert interrupted.status == DelegationStatus.NEEDS_ATTENTION
    assert interrupted.next_owner == WorkOrderNextOwner.WORKER_DECISION
    assert len(interrupted.specialist_turns) == 1
    assert interrupted.specialist_turns[0].status == SpecialistTurnStatus.COMPLETED
    assert service.agent_runtime.snapshot(run_id).run.current_plan_revision_id == plan_id

    reopened = _service(tmp_path, client)
    completed = reopened.execute_agent_work_order(work_order_id)
    assert completed.result.status == AgentResultStatus.COMPLETED
    assert completed.result.work_order_id == work_order_id
    assert candidate.id in client.inputs[-1]
    assert len(reopened.get_project(project.id).document.assets) == before_assets + 1
    assert len(reopened.agent_runtime_store.list_work_orders(run_id)) == 1


def test_restart_marks_in_flight_specialist_tool_as_interrupted_without_replay(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The specialist handled the interruption.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": ["The previous Tool outcome is unknown."],
                    "decisions": ["Do not blindly replay the Tool."],
                    "assumptions": [],
                    "unresolved_questions": [],
                    "evidence_refs": [],
                    "recommended_next_actions": [],
                }
            )
        ]
    )
    service = _service(tmp_path, client)
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, work_order_id = _delegated_work_order(service, project.id)
    work_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert work_order is not None
    work_order.specialist_turns.append(
        SpecialistTurnRecord(
            id="specialist-turn-interrupted",
            sequence=1,
            tool_name="project.get",
            arguments={"project_id": project.id},
            reason="Inspect the project.",
            status=SpecialistTurnStatus.IN_PROGRESS,
        )
    )
    work_order.status = DelegationStatus.IN_PROGRESS
    service.agent_runtime_store.save_work_order(work_order)

    reopened = _service(tmp_path, client)
    recovered = reopened.agent_runtime_store.get_work_order(work_order_id)

    assert recovered is not None
    assert recovered.status == DelegationStatus.NEEDS_ATTENTION
    assert recovered.specialist_turns[0].status == SpecialistTurnStatus.INTERRUPTED
    assert recovered.specialist_turns[0].result is not None
    recovered_error = recovered.specialist_turns[0].result["error"]
    assert isinstance(recovered_error, dict)
    assert recovered_error["kind"] == "ToolExecutionInterrupted"

    completed = reopened.execute_agent_work_order(work_order_id)

    assert completed.result.status == AgentResultStatus.COMPLETED
    assert completed.run.snapshot.run.id == run_id
    assert len(client.inputs) == 1
    model_input = json.loads(client.inputs[0])
    assert (
        model_input["working_set"]["latest_handoff"]["result"]["error"]["kind"]
        == "ToolExecutionInterrupted"
    )
