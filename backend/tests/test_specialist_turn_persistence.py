"""Recoverable, one-step-at-a-time execution for delegated specialist Agents."""

from __future__ import annotations

import json
from pathlib import Path

from short_drama.agent_runtime import (
    ActionKind,
    AgentResultStatus,
    AgentRunStatus,
    DelegationStatus,
    PlanStep,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService


class SequencedLanguageClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.inputs: list[str] = []

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
        del instructions, image_urls, max_output_tokens
        self.inputs.append(input_text)
        if not self.responses:
            raise AssertionError("The specialist made an unexpected model call")
        return self.responses.pop(0)


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
    assert len(final_input["tool_history"]) == 1
    assert final_input["tool_history"][0]["result"]["status"] == "SUCCEEDED"
    assert completed.result.evidence_refs[0].artifact_id == project.id

    settled = reopened_again.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED


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
        model_input["tool_history"][0]["result"]["error"]["kind"]
        == "ToolExecutionInterrupted"
    )
