"""Agent-first Production Manager integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.agent_runtime import (
    ActionKind,
    ActionStatus,
    AgentResultStatus,
    AgentRunStatus,
    ArtifactRef,
    DelegationStatus,
    PlanStep,
)
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ProductionExecutionTarget,
    CreativeInputImage,
    CreateProjectRequest,
    EnqueueRequest,
    ProductionProfile,
    RunMode,
    SourceMode,
    TaskStatus,
)
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentProjectCreateRequest,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunReplanRequest,
    AgentUserInputRequest,
    AgentWorkOrderResultRequest,
    ProductionManagerError,
)
from short_drama.service import ShortDramaService


class FakePlanningLanguageClient:
    def __init__(self) -> None:
        self.instructions: list[str] = []
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
        del image_urls, max_output_tokens
        self.instructions.append(instructions)
        self.inputs.append(input_text)
        project_id = json.loads(input_text)["context"]["project_id"]
        return json.dumps(
            {
                "summary": (
                    "The Agent chose a direct inspection instead of a fixed pipeline."
                ),
                "steps": [
                    {
                        "id": "inspect-project",
                        "title": "Inspect current project",
                        "kind": "TOOL_CALL",
                        "assigned_agent": "production-manager",
                        "tool_name": "project.get",
                        "instructions": "Read the canonical project.",
                        "arguments": {"project_id": project_id},
                        "depends_on": [],
                        "artifact_refs": [],
                    }
                ],
            }
        )


class FakeSpecialistLanguageClient(FakePlanningLanguageClient):
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
        return """{
          "status": "COMPLETED",
          "concise_summary": "The director recommends preserving the eyeline.",
          "created_artifacts": [],
          "updated_artifacts": [],
          "findings": ["The staging is readable."],
          "decisions": ["Preserve the eyeline."],
          "assumptions": [],
          "unresolved_questions": [],
          "evidence_refs": [],
          "recommended_next_actions": ["Continue with the current shot plan."]
        }"""


class SequencedLanguageClient(FakePlanningLanguageClient):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)

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
            raise AssertionError("The test language client has no response left")
        return self.responses.pop(0)


def _service(root: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
    )


def test_capability_catalog_shares_tools_across_file_backed_skills(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    catalog = service.get_agent_capabilities()

    assert {skill.profile for skill in catalog.skills} == set(ProductionProfile)
    tool_ids = {tool.id for tool in catalog.tools}
    assert {"project.get", "change.preview", "change.apply"} <= tool_ids
    assert all(skill.role_names for skill in catalog.skills)


def test_configured_language_agent_authors_the_initial_dynamic_plan(
    tmp_path: Path,
) -> None:
    client = FakePlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(objective="Inspect only what this objective needs."),
    )

    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.steps[0].tool_name == "project.get"
    assert run.snapshot.run.unattended is False
    assert run.snapshot.run.execution_target is None
    assert "你可以选择、跳过、重排、并行或替换步骤" in client.instructions[0]
    assert "当前制片技能：AI 原生短剧" in client.instructions[0]
    assert "共享端到端制片流程" in client.instructions[0]
    assert project.id in client.inputs[0]


def test_agent_authored_plan_executes_tools_and_survives_service_restart(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Read the current project, then checkpoint what should change.",
            initial_plan=AgentPlanProposal(
                summary="Inspect first, then checkpoint.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="read-project",
                        title="Read the canonical project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Load the current project before making a decision.",
                        arguments={"project_id": project.id},
                    ),
                    PlanStep(
                        id="checkpoint",
                        title="Record the next focus",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="The project was read from the canonical store.",
                        depends_on=["read-project"],
                    ),
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        created.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(advanced.executed_action_ids) == 2
    assert advanced.run.snapshot.actions[0].tool_name == "project.get"
    assert advanced.run.snapshot.actions[0].artifact_refs[0].artifact_id == project.id

    reopened = _service(tmp_path)
    resumed = reopened.get_agent_run(created.snapshot.run.id)
    assert resumed.snapshot.run.status == AgentRunStatus.COMPLETED
    assert resumed.snapshot.current_plan is not None
    assert resumed.snapshot.current_plan.steps[0].id == "read-project"


def test_configured_specialist_agent_completes_work_order_round_trip(
    tmp_path: Path,
) -> None:
    client = FakeSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get a scene-director decision.",
            initial_plan=AgentPlanProposal(
                summary="Delegate the bounded decision.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate-director",
                        title="Ask scene director",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review the first shot's staging.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    response = service.execute_agent_work_order(waiting.created_work_order_ids[0])

    assert response.result.status == AgentResultStatus.COMPLETED
    assert response.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert response.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert response.run.context.decisions[0].statement == "Preserve the eyeline."
    assert "对应角色的 Production Skill" in client.instructions[0]
    assert "scene-director" in client.inputs[0]


def test_plan_can_be_replaced_with_a_new_parallel_graph(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Revise only the selected scene.",
            initial_plan=AgentPlanProposal(
                summary="Initial idea",
                steps=[
                    PlanStep(
                        id="old-step",
                        title="Old step",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="This plan will be replaced.",
                    )
                ],
            ),
        ),
    )

    replaced = service.replan_agent_run(
        run.snapshot.run.id,
        AgentRunReplanRequest(
            reason="The Agent found two independent inspections.",
            proposed_plan=AgentPlanProposal(
                summary="Inspect story and staging in parallel.",
                steps=[
                    PlanStep(
                        id="story-check",
                        title="Check story impact",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="story-architect",
                        instructions="Review the story branch.",
                    ),
                    PlanStep(
                        id="staging-check",
                        title="Check staging impact",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="scene-director",
                        instructions="Review the staging branch.",
                    ),
                ],
            ),
        ),
    )

    assert replaced.snapshot.current_plan is not None
    assert replaced.snapshot.current_plan.revision == 2
    assert {step.id for step in replaced.ready_steps} == {
        "story-check",
        "staging-check",
    }


def test_agent_plan_cannot_target_a_different_project(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(
        ProductionManagerError,
        match="must target the current run project",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Inspect only the current project.",
                initial_plan=AgentPlanProposal(
                    summary="Invalid cross-project Tool call.",
                    steps=[
                        PlanStep(
                            id="inspect-other-project",
                            title="Inspect another project",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="project.get",
                            instructions="This should be rejected before execution.",
                            arguments={"project_id": "project-outside-this-run"},
                        )
                    ],
                ),
            ),
        )


def test_delegation_uses_compact_work_order_and_profile_specific_role_prompt(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    shot = aggregate.document.shots[0]
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Ask the scene director to review one shot.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one bounded review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="director-review",
                        title="Director review",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review this shot's staging without changing other shots.",
                        arguments={
                            "why_now": "The shot is the only affected branch.",
                            "constraints": ["Preserve the accepted ending."],
                            "acceptance_criteria": ["Return one concise finding."],
                        },
                        artifact_refs=[
                            {
                                "project_id": project_id,
                                "artifact_type": "shot",
                                "artifact_id": shot.id,
                                "version": shot.revision,
                            }
                        ],
                    )
                ],
            ),
        ),
    )
    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = advanced.created_work_order_ids[0]
    work_order = service.get_agent_work_order(work_order_id)

    assert work_order.work_order.scope[0].artifact_id == shot.id
    assert work_order.work_order.constraints == ["Preserve the accepted ending."]
    assert "当前制片技能：AI 原生短剧" in work_order.system_prompt
    assert "导演职责准则" in work_order.system_prompt
    assert work_order.context.artifacts[0].ref.artifact_id == shot.id

    returned = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="Staging is clear; preserve the current eyeline.",
            decisions=["Preserve the current eyeline."],
            updated_artifacts=[work_order.work_order.scope[0]],
        ),
    )
    assert returned.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert returned.run.context.decisions[0].statement == "Preserve the current eyeline."
    assert returned.run.snapshot.run.status == AgentRunStatus.COMPLETED

    digital = aggregate.document.model_copy(deep=True)
    digital.project.id = "project-digital-human-test"
    digital.project.name = "Presenter test"
    digital.project.production_profile = ProductionProfile.DIGITAL_HUMAN
    digital.project.current_script_version_id = digital.script_versions[0].id
    digital.project.current_bible_version_id = digital.bible_versions[0].id
    for script_version in digital.script_versions:
        script_version.project_id = digital.project.id
    for bible_version in digital.bible_versions:
        bible_version.project_id = digital.project.id
    for asset in digital.assets:
        asset.project_id = digital.project.id
    for item in digital.shots:
        item.project_id = digital.project.id
    digital.reference_packs = []
    digital.manifests = []
    service.repository.save_project(digital)
    digital_run = service.create_agent_run(
        digital.project.id,
        AgentRunCreateRequest(
            objective="Review presenter performance.",
            initial_plan=AgentPlanProposal(
                summary="Delegate presenter direction.",
                steps=[
                    PlanStep(
                        id="presenter-direction",
                        title="Presenter direction",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review eyeline and gesture range.",
                    )
                ],
            ),
        ),
    )
    digital_advanced = service.advance_agent_run(
        digital_run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    digital_order = service.get_agent_work_order(
        digital_advanced.created_work_order_ids[0]
    )

    assert "当前制片技能：数字人口播视频" in digital_order.system_prompt
    assert "主播的视线" in digital_order.system_prompt
    assert digital_order.system_prompt != work_order.system_prompt


def test_partial_work_order_result_can_be_completed_without_orphaning_the_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get one bounded specialist answer.",
            initial_plan=AgentPlanProposal(
                summary="Delegate once.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review the scene.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = waiting.created_work_order_ids[0]

    partial = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.PARTIAL,
            concise_summary="The camera intention is still missing.",
            unresolved_questions=["Should the camera remain static?"],
            recommended_next_actions=["Answer the camera question."],
        ),
    )

    assert partial.run.snapshot.run.status == AgentRunStatus.WAITING
    assert partial.run.snapshot.actions[0].status.value == "RUNNING"
    assert partial.run.snapshot.pending_work_orders[0].status.value == "NEEDS_ATTENTION"
    assert partial.run.context.open_questions == ["Should the camera remain static?"]

    completed = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="Use a restrained static composition.",
        ),
    )

    assert completed.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert completed.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert completed.run.snapshot.pending_work_orders == []
    with pytest.raises(ValueError, match="already COMPLETED"):
        service.submit_agent_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="Duplicate completion must not overwrite the result.",
            ),
        )


def test_agent_runtime_http_endpoints_are_inspectable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)

    capabilities = client.get("/api/short-drama/agent-runtime/capabilities")
    assert capabilities.status_code == 200
    assert len(capabilities.json()["skills"]) == 4

    created = client.post(
        f"/api/short-drama/projects/{project_id}/agent-runs",
        json={
            "objective": "Create one inspectable checkpoint.",
            "initial_plan": {
                "summary": "One dynamic step",
                "goal_complete": True,
                "steps": [
                    {
                        "id": "checkpoint",
                        "title": "Checkpoint",
                        "kind": "CHECKPOINT",
                        "assigned_agent": "production-manager",
                        "instructions": "Record the current focus.",
                    }
                ],
            },
        },
    )
    assert created.status_code == 200
    run_id = created.json()["snapshot"]["run"]["id"]

    advanced = client.post(
        f"/api/short-drama/agent-runs/{run_id}/advance",
        json={"max_actions": 1},
    )
    assert advanced.status_code == 200
    assert advanced.json()["run"]["snapshot"]["run"]["status"] == "COMPLETED"

    listed = client.get(f"/api/short-drama/projects/{project_id}/agent-runs")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["snapshot"]["run"]["id"] == run_id

    bootstrapped = client.post(
        "/api/short-drama/agent-projects",
        json={
            "project": {
                "name": "HTTP bootstrap",
                "source_mode": "IDEA_EXPANSION",
                "input_text": "A concise presenter-led production test.",
                "run_mode": "GUIDED",
            },
            "execution_target": "MOCK",
            "render_export": False,
        },
    )
    assert bootstrapped.status_code == 200
    bootstrapped_project_id = bootstrapped.json()["project_id"]
    fetched = client.get(f"/api/short-drama/projects/{bootstrapped_project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["document"]["project"]["id"] == bootstrapped_project_id


def test_advance_executes_the_current_ready_batch_before_waiting(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Run all independent ready work, including one delegation.",
            initial_plan=AgentPlanProposal(
                summary="Three independent ready steps.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate a bounded review",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review the current staging.",
                    ),
                    PlanStep(
                        id="checkpoint",
                        title="Record the current focus",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Record the current production focus.",
                    ),
                    PlanStep(
                        id="inspect",
                        title="Inspect the canonical project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the canonical project.",
                        arguments={"project_id": project.id},
                    ),
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=3),
    )

    assert len(advanced.executed_action_ids) == 3
    assert len(advanced.created_work_order_ids) == 1
    assert [action.status.value for action in advanced.run.snapshot.actions] == [
        "RUNNING",
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert advanced.run.snapshot.run.status == AgentRunStatus.WAITING

    completed = service.submit_agent_work_order_result(
        advanced.created_work_order_ids[0],
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The staging review is complete.",
        ),
    )
    assert completed.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_completed_bounded_plan_replans_from_the_latest_context(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            """{
              "summary": "The latest project read is enough for a final checkpoint.",
              "goal_complete": true,
              "steps": [{
                "id": "finish",
                "title": "Finish from latest context",
                "kind": "CHECKPOINT",
                "assigned_agent": "production-manager",
                "instructions": "Confirm the inspected project satisfies this bounded objective."
              }]
            }"""
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the project and decide whether the bounded goal is complete.",
            initial_plan=AgentPlanProposal(
                summary="Inspect first, then let the Agent observe the result.",
                steps=[
                    PlanStep(
                        id="inspect",
                        title="Inspect project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the canonical project.",
                        arguments={"project_id": project.id},
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.revision == 2
    assert len(advanced.executed_action_ids) == 2
    planner_payload = json.loads(client.inputs[0])
    recent = planner_payload["context"]["recent_actions"]
    assert recent[-1]["status"] == "SUCCEEDED"
    assert recent[-1]["artifact_refs"][0]["artifact_id"] == project.id
    assert any("latest ContextPack" in note for note in advanced.notes)


def test_successful_actions_reset_the_no_progress_replan_bound(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": f"Continue bounded step {index}.",
                    "goal_complete": index == 6,
                    "steps": [
                        {
                            "id": f"checkpoint-{index}",
                            "title": f"Checkpoint {index}",
                            "kind": "CHECKPOINT",
                            "assigned_agent": "production-manager",
                            "instructions": f"Record successful step {index}.",
                        }
                    ],
                }
            )
            for index in range(1, 7)
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Complete seven successful bounded checkpoints.",
            initial_plan=AgentPlanProposal(
                summary="Start the bounded sequence.",
                steps=[
                    PlanStep(
                        id="checkpoint-0",
                        title="Checkpoint 0",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Record successful step 0.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=7),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(advanced.executed_action_ids) == 7
    assert len(client.inputs) == 6
    assert not any("bounded retry limit" in note for note in advanced.notes)


def test_tool_failure_can_replan_and_continue_within_the_same_advance(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            """{
              "summary": "Recover from the invalid Tool arguments.",
              "goal_complete": true,
              "steps": [{
                "id": "recovered",
                "title": "Record the recovery",
                "kind": "CHECKPOINT",
                "assigned_agent": "production-manager",
                "instructions": "Record that the failed inspection was safely replanned."
              }]
            }"""
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Recover from an invalid inspection action.",
            initial_plan=AgentPlanProposal(
                summary="This Tool call intentionally omits its required argument.",
                steps=[
                    PlanStep(
                        id="invalid-inspection",
                        title="Invalid project inspection",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Attempt the bounded inspection.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert [action.status.value for action in advanced.run.snapshot.actions] == [
        "FAILED",
        "SUCCEEDED",
    ]
    planner_payload = json.loads(client.inputs[0])
    assert planner_payload["context"]["recent_actions"][-1]["status"] == "FAILED"
    assert "project_id" in planner_payload["context"]["recent_actions"][-1]["summary"]


def test_builtin_specialist_can_call_shared_tools_before_returning_result(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    client.responses = [
            json.dumps(
                {
                    "tool_name": "project.get",
                    "arguments": {"project_id": project.id},
                    "reason": "Inspect the canonical project before directing the scene.",
                }
            ),
            """{
              "status": "COMPLETED",
              "concise_summary": "The scene direction is grounded in the canonical project.",
              "created_artifacts": [],
              "updated_artifacts": [],
              "findings": ["The current staging is readable."],
              "decisions": ["Preserve the current staging."],
              "assumptions": [],
              "unresolved_questions": [],
              "evidence_refs": [],
              "recommended_next_actions": []
            }""",
    ]
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get a grounded scene-direction decision.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one grounded review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate scene direction",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Inspect the project and return one staging decision.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    response = service.execute_agent_work_order(waiting.created_work_order_ids[0])

    assert response.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(client.inputs) == 2
    first_payload = json.loads(client.inputs[0])
    assert "project.get" in {
        tool["id"] for tool in first_payload["available_tools"]
    }
    second_payload = json.loads(client.inputs[1])
    assert second_payload["tool_history"][0]["result"]["status"] == "SUCCEEDED"
    assert response.result.evidence_refs[0].artifact_id == project.id
    experiences = service.agent_runtime_store.list_experiences(project.id)
    assert experiences[0].summary == response.result.concise_summary
    assert "scene-director" in experiences[0].tags
    assert project.production_profile.value in experiences[0].tags


def test_completed_work_order_contract_errors_are_wrapped(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Require one evidenced specialist finding.",
            initial_plan=AgentPlanProposal(
                summary="Delegate with a simple acceptance criterion.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate evidenced review",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Return one evidenced finding.",
                        arguments={
                            "acceptance_criteria": ["Return one concise finding."]
                        },
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    with pytest.raises(
        ProductionManagerError,
        match="does not satisfy the WorkOrder contract",
    ):
        service.submit_agent_work_order_result(
            waiting.created_work_order_ids[0],
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="A prose-only completion is insufficient.",
            ),
        )


def test_agent_project_bootstrap_creates_the_project_before_returning(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    guided = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Guided bootstrap",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="A presenter explains one practical idea.",
                run_mode=RunMode.GUIDED,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        )
    )

    guided_project = service.get_project(guided.project_id)
    assert guided_project.document.project.id == guided.project_id
    assert guided.run.snapshot.actions[0].tool_name == "project.create"
    assert guided.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert guided.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert guided.run.snapshot.run.unattended is False
    assert (
        guided.run.snapshot.run.execution_target
        == ProductionExecutionTarget.MOCK
    )
    assert guided.run.snapshot.current_plan is not None
    assert guided.run.snapshot.current_plan.goal_complete is True

    automatic = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Automatic bootstrap",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="Two characters discover a surprising package at the door.",
                run_mode=RunMode.AUTO_DRAFT,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        )
    )

    automatic_project = service.get_project(automatic.project_id)
    assert automatic_project.document.project.id == automatic.project_id
    assert automatic.run.snapshot.current_plan is not None
    assert automatic.run.snapshot.current_plan.revision == 2
    assert automatic.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert automatic.run.snapshot.run.unattended is True
    assert (
        automatic.run.snapshot.run.execution_target
        == ProductionExecutionTarget.MOCK
    )
    assert automatic.run.ready_steps[0].tool_name == "video.enqueue"


def test_default_mock_agent_project_reaches_export_without_legacy_run(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    created = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Agent-first default path",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="Two characters discover a surprising package at the door.",
                run_mode=RunMode.AUTO_DRAFT,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        )
    )

    current = created.run
    for _ in range(8):
        if current.snapshot.run.status == AgentRunStatus.COMPLETED:
            break
        current = service.advance_agent_run(
            current.snapshot.run.id,
            AgentRunAdvanceRequest(max_actions=8),
        ).run

    aggregate = service.get_project(created.project_id)
    assert current.snapshot.run.status == AgentRunStatus.COMPLETED
    assert aggregate.document.project.stage.value == "EXPORTED"
    assert len(aggregate.exports) == 1
    assert "autonomous_runs" not in aggregate.document.model_dump()


def test_existing_guided_project_can_start_an_explicit_mock_agent_run(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Guided continuation",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A presenter explains one practical idea.",
            run_mode=RunMode.GUIDED,
        )
    ).document.project

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Produce a complete Mock draft from this guided project.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        ),
    )

    assert run.snapshot.run.status == AgentRunStatus.RUNNING
    assert run.snapshot.run.unattended is True
    assert run.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.goal_complete is False
    assert run.ready_steps[0].tool_name == "video.enqueue"


def test_current_run_target_does_not_inherit_an_older_run_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Target isolation",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters find an unexpected parcel.",
            run_mode=RunMode.AUTO_DRAFT,
        )
    ).document.project
    older = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Prepare a real H3 production.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )
    assert older.ready_steps[0].tool_name == "route.plan_h3"

    current = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Produce a deterministic Mock draft.",
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        ),
    )

    assert current.ready_steps[0].tool_name == "video.enqueue"
    assert current.context.decisions[0].run_id == current.snapshot.run.id
    assert current.context.decisions[0].statement == "Render export: yes"
    assert any(
        decision.statement == "Execution target: MOCK"
        and decision.run_id == current.snapshot.run.id
        for decision in current.context.decisions
    )


def test_local_planner_requests_and_remembers_a_missing_production_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Target prompt",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator demonstrates a small workflow.",
            run_mode=RunMode.AUTO_DRAFT,
        )
    ).document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(objective="Continue production."),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    ).run
    action = waiting.snapshot.actions[-1]
    assert action.kind == ActionKind.USER_INPUT
    assert action.status == ActionStatus.RUNNING

    with pytest.raises(ProductionManagerError, match="must be MOCK or REAL_H3"):
        service.submit_agent_user_input(
            run.snapshot.run.id,
            AgentUserInputRequest(
                action_id=action.id,
                answer="something else",
                remember_as_decision=True,
            ),
        )
    pending_action = service.agent_runtime_store.get_action(action.id)
    assert pending_action is not None
    assert pending_action.status == ActionStatus.RUNNING

    continued = service.submit_agent_user_input(
        run.snapshot.run.id,
        AgentUserInputRequest(
            action_id=action.id,
            answer="mock",
            remember_as_decision=True,
        ),
    )
    assert continued.ready_steps[0].tool_name == "video.enqueue"
    assert continued.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert any(
        decision.statement == "Execution target: MOCK"
        for decision in continued.context.decisions
    )


def test_id_only_tool_arguments_cannot_mutate_another_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_a = service.create_project(
        CreateProjectRequest(
            name="Project A",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator explains a useful shortcut.",
        )
    ).document.project
    project_b = service.create_project(
        CreateProjectRequest(
            name="Project B",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters discover a package.",
        )
    ).document.project
    queued = service.enqueue(project_b.id, EnqueueRequest(candidate_count=1))
    foreign_task = queued.tasks[0]
    assert foreign_task.status == TaskStatus.QUEUED

    with pytest.raises(ProductionManagerError, match="task_id must belong"):
        service.create_agent_run(
            project_a.id,
            AgentRunCreateRequest(
                objective="Cancel a task.",
                initial_plan=AgentPlanProposal(
                    summary="Attempt a foreign task mutation.",
                    steps=[
                        PlanStep(
                            id="cancel-task",
                            title="Cancel task",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="task.cancel",
                            instructions="Cancel the selected task.",
                            arguments={"task_id": foreign_task.id},
                        )
                    ],
                ),
            ),
        )

    unchanged = service.repository.get_task(foreign_task.id)
    assert unchanged is not None
    assert unchanged.status == TaskStatus.QUEUED


def test_existing_run_cannot_use_project_create_without_its_bootstrap_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Existing project",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator explains a useful shortcut.",
        )
    ).document.project
    before_ids = {item.id for item in service.list_projects().projects}

    with pytest.raises(ProductionManagerError, match="preallocated current run project_id"):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Create another project.",
                initial_plan=AgentPlanProposal(
                    summary="Attempt an unscoped project creation.",
                    steps=[
                        PlanStep(
                            id="create-project",
                            title="Create project",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="project.create",
                            instructions="Create an unrelated project.",
                            arguments={
                                "request": {
                                    "name": "Unexpected project",
                                    "source_mode": "IDEA_EXPANSION",
                                    "input_text": "This must not be created.",
                                    "run_mode": "AUTO_DRAFT",
                                }
                            },
                        )
                    ],
                ),
            ),
        )

    assert {item.id for item in service.list_projects().projects} == before_ids


def test_agent_result_artifacts_must_match_the_parent_run_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_a = service.create_project(
        CreateProjectRequest(
            name="Result A",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A presenter explains a practical tip.",
        )
    ).document.project
    project_b = service.create_project(
        CreateProjectRequest(
            name="Result B",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters meet unexpectedly.",
        )
    ).document.project
    run = service.create_agent_run(
        project_a.id,
        AgentRunCreateRequest(
            objective="Delegate a bounded review.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one review.",
                steps=[
                    PlanStep(
                        id="delegate-review",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review the current project.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_order_id = waiting.created_work_order_ids[0]

    with pytest.raises(ProductionManagerError, match="outside the current run project"):
        service.submit_agent_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="Reviewed, but returned the wrong artifact.",
                created_artifacts=[
                    ArtifactRef(
                        project_id=project_b.id,
                        artifact_type="project",
                        artifact_id=project_b.id,
                    )
                ],
            ),
        )

    assert service.agent_runtime_store.get_agent_result(work_order_id) is None
    unchanged_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert unchanged_order is not None
    assert unchanged_order.status == DelegationStatus.PENDING


def test_restart_normalizes_interrupted_tool_and_specialist_work(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    tool_run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the project.",
            initial_plan=AgentPlanProposal(
                summary="Inspect once.",
                steps=[
                    PlanStep(
                        id="inspect",
                        title="Inspect",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the project.",
                        arguments={"project_id": project.id},
                    )
                ],
            ),
        ),
    )
    assert tool_run.snapshot.current_plan is not None
    interrupted_action = service.agent_runtime.begin_action(
        tool_run.snapshot.run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="production-manager",
        input_summary="Read the project.",
        plan_revision_id=tool_run.snapshot.current_plan.id,
        plan_step_id="inspect",
        tool_name="project.get",
    )

    delegated_run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Ask a director.",
            initial_plan=AgentPlanProposal(
                summary="Delegate once.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review one scene.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        delegated_run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = waiting.created_work_order_ids[0]
    service.agent_runtime.begin_work_order(work_order_id)

    reopened = _service(tmp_path)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted_action.id)
    recovered_order = reopened.agent_runtime_store.get_work_order(work_order_id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.FAILED
    assert "interrupted" in (recovered_action.error_message or "").lower()
    assert recovered_order is not None
    assert recovered_order.status == DelegationStatus.NEEDS_ATTENTION
    assert reopened.get_agent_run(tool_run.snapshot.run.id).snapshot.run.status == AgentRunStatus.WAITING


def test_agent_project_bootstrap_does_not_report_partial_project_creation_as_success(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ProductionManagerError, match="Local asset does not exist"):
        service.create_agent_project(
            AgentProjectCreateRequest(
                project=CreateProjectRequest(
                    name="Broken visual seed",
                    source_mode=SourceMode.IMAGE_SEEDED,
                    input_images=[
                        CreativeInputImage(
                            local_path=str(tmp_path / "missing-seed.png"),
                            descriptor="A missing visual seed.",
                        )
                    ],
                    run_mode=RunMode.AUTO_DRAFT,
                ),
                execution_target=ProductionExecutionTarget.MOCK,
                render_export=True,
            )
        )

    runs = service.agent_runtime_store.list_runs()
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED
    actions = service.agent_runtime_store.list_actions(runs[0].id)
    assert actions[0].tool_name == "project.create"
    assert actions[0].status == ActionStatus.FAILED


def test_work_order_context_resolves_a_persisted_take_reference(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    service.enqueue(project.id, EnqueueRequest(candidate_count=1))
    drained = service.drain(project.id, 500)
    take = drained.project.takes[0]
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Review one generated take.",
            initial_plan=AgentPlanProposal(
                summary="Delegate the take review.",
                steps=[
                    PlanStep(
                        id="review-take",
                        title="Review take",
                        kind=ActionKind.DELEGATION,
                        assigned_agent="scene-director",
                        instructions="Review only this generated take.",
                        artifact_refs=[
                            ArtifactRef(
                                project_id=project.id,
                                artifact_type="take",
                                artifact_id=take.id,
                            )
                        ],
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_order = service.get_agent_work_order(waiting.created_work_order_ids[0])

    assert take.id in {
        item.ref.artifact_id for item in work_order.context.artifacts
    }
    assert take.id not in {
        item.artifact_id for item in work_order.context.unresolved_artifact_refs
    }


def test_local_h3_planner_reads_all_active_tasks_from_the_repository(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    queued = service.enqueue(project.id, EnqueueRequest(candidate_count=2))
    assert len(queued.tasks) > 8

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Continue all active generation tasks.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )

    assert len(run.ready_steps) == len(queued.tasks)
    assert {step.arguments["task_id"] for step in run.ready_steps} == {
        task.id for task in queued.tasks
    }
    assert all(step.tool_name == "task.advance" for step in run.ready_steps)
