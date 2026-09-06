"""HTTP integration for the derived production board and local team executor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes._errors import HTTPError
from _routes.short_drama import router as short_drama_router
from short_drama.agent_runtime import (
    ActionKind,
    ActionStatus,
    AgentRunStatus,
    DelegationStatus,
    PlanStep,
    SpecialistTurnStatus,
    WorkOrderNextOwner,
)
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService


class CompletingSpecialistLanguageClient:
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
        del instructions, input_text, image_urls, max_output_tokens
        return """{
          "status": "COMPLETED",
          "concise_summary": "The assigned production question is resolved.",
          "created_artifacts": [],
          "updated_artifacts": [],
          "findings": ["The bounded specialist review is complete."],
          "decisions": [],
          "assumptions": [],
          "unresolved_questions": [],
          "evidence_refs": [],
          "recommended_next_actions": []
        }"""


class ToolUsingSpecialistLanguageClient:
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
        payload = cast(dict[str, object], json.loads(input_text))
        tool_history = cast(list[object], payload["tool_history"])
        if not tool_history:
            context = cast(dict[str, object], payload["context"])
            project_id = cast(str, context["project_id"])
            return json.dumps(
                {
                    "tool_name": "artifact.list_production_documents",
                    "arguments": {"project_id": project_id},
                    "reason": "Inspect the durable handoff documents first.",
                }
            )
        return json.dumps(
            {
                "status": "COMPLETED",
                "concise_summary": "Inspected the persisted Tool result and finished.",
                "created_artifacts": [],
                "updated_artifacts": [],
                "findings": ["The production-document inventory is available."],
                "decisions": [],
                "assumptions": [],
                "unresolved_questions": [],
                "evidence_refs": [],
                "recommended_next_actions": [],
            }
        )


class PartialThenPlanningLanguageClient:
    def __init__(self) -> None:
        self.specialist_calls = 0
        self.planner_calls = 0

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
        payload = cast(dict[str, object], json.loads(input_text))
        if "work_order" in payload:
            self.specialist_calls += 1
            return json.dumps(
                {
                    "status": "PARTIAL",
                    "concise_summary": "The bounded review found a plan-level conflict.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": ["The Leader must revise the next production step."],
                    "decisions": [],
                    "assumptions": [],
                    "unresolved_questions": ["Which alternate step should replace it?"],
                    "evidence_refs": [],
                    "recommended_next_actions": ["Let the Leader revise the plan."],
                    "next_owner": "LEADER_REPLAN",
                }
            )
        self.planner_calls += 1
        return json.dumps(
            {
                "summary": "The Leader accepted the partial evidence and concluded.",
                "steps": [],
                "goal_complete": True,
            }
        )


class FailingSpecialistLanguageClient:
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
        del instructions, input_text, image_urls, max_output_tokens
        raise RuntimeError("specialist provider stopped")


class ProtocolCorrectingSpecialistLanguageClient:
    def __init__(self) -> None:
        self.specialist_calls = 0
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
        payload = cast(dict[str, object], json.loads(input_text))
        if "work_order" not in payload:
            raise AssertionError("Protocol correction must not invoke the Leader")
        self.specialist_calls += 1
        result = json.dumps(
            {
                "status": "COMPLETED",
                "concise_summary": "The same Worker completed the bounded review.",
                "created_artifacts": [],
                "updated_artifacts": [],
                "findings": ["The staging remains readable."],
                "decisions": [],
                "assumptions": [],
                "unresolved_questions": [],
                "evidence_refs": [],
                "recommended_next_actions": [],
            }
        )
        if self.specialist_calls == 1:
            return result + "\n" + json.dumps({"extra": True})
        return result


class ProtocolRepeatingSpecialistLanguageClient:
    def __init__(self) -> None:
        self.specialist_calls = 0
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
        payload = cast(dict[str, object], json.loads(input_text))
        if "work_order" not in payload:
            raise AssertionError("Protocol no-progress must return control to the Leader")
        self.specialist_calls += 1
        if self.specialist_calls > 2:
            raise AssertionError(
                "The same semantic protocol failure must not be replayed indefinitely"
            )
        result = json.dumps(
            {
                "status": "COMPLETED",
                "concise_summary": "The same Worker completed the bounded review.",
                "created_artifacts": [],
                "updated_artifacts": [],
                "findings": ["The staging remains readable."],
                "decisions": [],
                "assumptions": [],
                "unresolved_questions": [],
                "evidence_refs": [],
                "recommended_next_actions": [],
            }
        )
        return result + "\n" + json.dumps({"extra": True})


def test_board_and_team_advance_are_available_through_the_http_api(
    tmp_path: Path,
) -> None:
    language_client = CompletingSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Resolve two independent production questions.",
            initial_plan=AgentPlanProposal(
                summary="Delegate two independent bounded reviews.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="review-story",
                        title="Review story shape",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="story-reviewer",
                        production_skill_role=ProductionSkillRole.WRITER,
                        instructions="Review the story shape.",
                    ),
                    PlanStep(
                        id="review-direction",
                        title="Review visual direction",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="visual-reviewer",
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        instructions="Review the visual direction.",
                    ),
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id
    waiting = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)

    board_before = client.get(f"/api/short-drama/agent-runs/{run_id}/board")
    assert board_before.status_code == 200, board_before.text
    assert {item["id"] for item in board_before.json()["work_orders"]} == set(
        waiting.created_work_order_ids
    )
    assert {step["status"] for step in board_before.json()["steps"]} == {
        "IN_PROGRESS"
    }

    advanced = client.post(
        f"/api/short-drama/agent-runs/{run_id}/team/advance"
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["selected_work_order_ids"] == waiting.created_work_order_ids
    assert {item["work_order_status"] for item in advanced.json()["advances"]} == {
        "COMPLETED"
    }

    board_after = client.get(f"/api/short-drama/agent-runs/{run_id}/board")
    assert board_after.status_code == 200, board_after.text
    assert {step["status"] for step in board_after.json()["steps"]} == {
        "COMPLETED"
    }
    assert board_after.json()["run_status"] == "RUNNING"

    leader_advance = client.post(
        f"/api/short-drama/agent-runs/{run_id}/advance",
        json={"max_actions": 1},
    )
    assert leader_advance.status_code == 200, leader_advance.text
    assert leader_advance.json()["run"]["snapshot"]["run"]["status"] == "COMPLETED"


def test_unattended_advance_owns_worker_tool_restart_and_leader_completion(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app-data" / "short-drama.sqlite3"
    workspace_root = tmp_path / "workspace"
    language_client = ToolUsingSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=database_path,
        workspace_root=workspace_root,
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect one durable artifact inventory and finish.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            initial_plan=AgentPlanProposal(
                summary="Delegate one bounded artifact review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="artifact-review",
                        title="Review durable artifacts",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="artifact-reviewer",
                        production_skill_role=ProductionSkillRole.PLANNER,
                        instructions="Inspect the production-document inventory.",
                    )
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id

    delegated = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(max_actions=8),
    )
    assert len(delegated.created_work_order_ids) == 1
    work_order_id = delegated.created_work_order_ids[0]
    delegated_order = service.get_agent_work_order(work_order_id).work_order
    assert delegated_order.next_owner == WorkOrderNextOwner.WORKER_DECISION

    completed_worker = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(max_actions=8),
    )
    assert completed_worker.advanced_work_order_ids == [work_order_id]
    completed_order = service.get_agent_work_order(work_order_id).work_order
    assert completed_order.status == DelegationStatus.COMPLETED
    assert completed_order.next_owner is None
    assert len(completed_order.specialist_turns) == 1
    completed_turn = completed_order.specialist_turns[0]
    assert completed_turn.status == SpecialistTurnStatus.COMPLETED
    assert completed_turn.result is not None
    assert completed_turn.result["status"] == "SUCCEEDED"
    assert completed_worker.run.snapshot.run.status == AgentRunStatus.RUNNING

    restarted = ShortDramaService(
        database_path=database_path,
        workspace_root=workspace_root,
        language_provider_factory=lambda _key, _url: language_client,
    )
    restored_order = restarted.get_agent_work_order(work_order_id).work_order
    assert restored_order.id == work_order_id
    assert restored_order.status == DelegationStatus.COMPLETED
    assert restored_order.next_owner is None
    assert len(restored_order.specialist_turns) == 1
    restored_turn = restored_order.specialist_turns[0]
    assert restored_turn.status == SpecialistTurnStatus.COMPLETED
    assert restored_turn.result == completed_turn.result

    completed_leader = restarted.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(max_actions=8),
    )
    assert completed_leader.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_partial_worker_result_returns_control_to_leader_on_next_advance(
    tmp_path: Path,
) -> None:
    language_client = PartialThenPlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Resolve a bounded review and let the Leader decide the next step.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            initial_plan=AgentPlanProposal(
                summary="Ask a specialist, then reconsider the plan.",
                goal_complete=False,
                steps=[
                    PlanStep(
                        id="bounded-review",
                        title="Review the bounded issue",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="story-reviewer",
                        production_skill_role=ProductionSkillRole.WRITER,
                        instructions="Return control if the plan itself must change.",
                    )
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id
    delegated = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    work_order_id = delegated.created_work_order_ids[0]

    partial = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    assert partial.advanced_work_order_ids == [work_order_id]
    assert language_client.specialist_calls == 1
    assert language_client.planner_calls == 0
    partial_order = service.get_agent_work_order(work_order_id).work_order
    assert partial_order.status == DelegationStatus.NEEDS_ATTENTION
    assert partial_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    board = service.get_agent_production_board(run_id)
    board_order = next(item for item in board.work_orders if item.id == work_order_id)
    assert board_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN

    leader = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=8))
    assert leader.advanced_work_order_ids == []
    assert language_client.specialist_calls == 1
    assert language_client.planner_calls == 1
    assert leader.run.snapshot.run.status.value == "COMPLETED"


def test_unattended_worker_failure_is_visible_and_not_replayed(
    tmp_path: Path,
) -> None:
    language_client = FailingSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Expose a worker failure without replaying it.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            initial_plan=AgentPlanProposal(
                summary="Delegate one review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="failing-review",
                        title="Run the failing review",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="story-reviewer",
                        production_skill_role=ProductionSkillRole.WRITER,
                        instructions="Review once.",
                    )
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id
    delegated = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    work_order_id = delegated.created_work_order_ids[0]

    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with pytest.raises(HTTPError) as exc_info:
        TestClient(app).post(
            f"/api/short-drama/agent-runs/{run_id}/advance",
            json={"max_actions": 8},
        )

    assert exc_info.value.status_code == 409
    assert "Local Agent Team batch stopped" in exc_info.value.detail
    stopped = service.get_agent_work_order(work_order_id).work_order
    assert stopped.status == DelegationStatus.NEEDS_ATTENTION
    assert stopped.next_owner == WorkOrderNextOwner.LEADER_REPLAN


def test_unattended_protocol_correction_keeps_same_work_order_and_plan(
    tmp_path: Path,
) -> None:
    language_client = ProtocolCorrectingSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Correct one malformed Worker handoff in place.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            initial_plan=AgentPlanProposal(
                summary="Delegate one bounded review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="protocol-review",
                        title="Review staging",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="story-reviewer",
                        production_skill_role=ProductionSkillRole.WRITER,
                        instructions="Return one bounded staging review.",
                    )
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id
    original_plan_id = created.snapshot.run.current_plan_revision_id
    delegated = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    work_order_id = delegated.created_work_order_ids[0]

    corrected = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(),
    )

    assert corrected.advanced_work_order_ids == [work_order_id]
    completed_order = service.get_agent_work_order(work_order_id).work_order
    assert completed_order.status == DelegationStatus.COMPLETED
    assert completed_order.pending_protocol_feedback is None
    assert corrected.run.snapshot.actions[0].status == ActionStatus.SUCCEEDED
    assert corrected.run.snapshot.run.current_plan_revision_id == original_plan_id
    assert corrected.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert language_client.specialist_calls == 2
    second_payload = cast(dict[str, object], json.loads(language_client.inputs[1]))
    assert second_payload["response_protocol_feedback"] is not None

    finished = service.advance_agent_run(run_id, AgentRunAdvanceRequest())

    assert finished.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_unattended_repeated_equivalent_protocol_failure_returns_to_leader(
    tmp_path: Path,
) -> None:
    language_client = ProtocolRepeatingSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: language_client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Return repeated protocol no-progress to the Leader.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            initial_plan=AgentPlanProposal(
                summary="Delegate one bounded review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="repeated-protocol-review",
                        title="Review staging",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="story-reviewer",
                        production_skill_role=ProductionSkillRole.WRITER,
                        instructions="Return one bounded staging review.",
                    )
                ],
            ),
        ),
    )
    run_id = created.snapshot.run.id
    original_plan_id = created.snapshot.run.current_plan_revision_id
    delegated = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    work_order_id = delegated.created_work_order_ids[0]

    stopped = service.advance_agent_run(run_id, AgentRunAdvanceRequest())

    assert stopped.advanced_work_order_ids == [work_order_id]
    assert language_client.specialist_calls == 2
    second_payload = cast(dict[str, object], json.loads(language_client.inputs[1]))
    assert second_payload["response_protocol_feedback"] is not None
    work_order = service.get_agent_work_order(work_order_id).work_order
    assert work_order.status == DelegationStatus.NEEDS_ATTENTION
    assert work_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert work_order.pending_protocol_feedback is not None
    assert stopped.run.snapshot.actions[0].status == ActionStatus.FAILED
    assert stopped.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert stopped.run.snapshot.run.current_plan_revision_id == original_plan_id


def test_team_advance_rejects_an_unknown_run(tmp_path: Path) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with pytest.raises(HTTPError) as exc_info:
        TestClient(app).post(
            "/api/short-drama/agent-runs/missing-run/team/advance"
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SHORT_DRAMA_AGENT_CONFLICT"
