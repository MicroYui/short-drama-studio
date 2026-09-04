"""Agent-first Production Manager integration tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

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
    AgentWorkOrderResultRequest,
    ProductionManagerError,
)
from short_drama.production_profiles import ProductionSkillRole
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


def _single_tool_plan(
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> AgentPlanProposal:
    return AgentPlanProposal(
        summary=f"Invoke {tool_name}.",
        steps=[
            PlanStep(
                id=tool_name.replace(".", "-"),
                title=f"Invoke {tool_name}",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="production-manager",
                tool_name=tool_name,
                instructions=f"Invoke {tool_name}.",
                arguments=arguments,
            )
        ],
    )


def test_incomplete_agent_plan_requires_a_next_step() -> None:
    with pytest.raises(
        ValueError,
        match="incomplete Agent plan must contain at least one next step",
    ):
        AgentPlanProposal(summary="No next step", goal_complete=False)


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
        AgentRunCreateRequest(
            objective="Inspect only what this objective needs.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )

    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.steps[0].tool_name == "project.get"
    assert run.snapshot.run.unattended is False
    assert run.snapshot.run.execution_target == ProductionExecutionTarget.REAL_H3
    assert run.snapshot.run.render_export is False
    planner_context = json.loads(client.inputs[0])["context"]
    assert planner_context["execution_target"] == "REAL_H3"
    assert planner_context["render_export"] is False
    assert "你可以选择、跳过、重排、并行或替换步骤" in client.instructions[0]
    assert "STORY_REPAIR" in client.instructions[0]
    assert "REVISION_VERIFIER" in client.instructions[0]
    assert "FINAL_COLD_READ" in client.instructions[0]
    assert "当前制片技能：AI 原生短剧" in client.instructions[0]
    assert "共享端到端制片流程" in client.instructions[0]
    assert project.id in client.inputs[0]


@pytest.mark.parametrize(
    ("target", "present", "absent"),
    [
        (
            ProductionExecutionTarget.MOCK,
            {"video.enqueue", "task.drain", "task.reconcile"},
            {
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
                "route.resolve_h3_submission",
                "route.resolve_h3_not_submitted",
            },
        ),
        (
            ProductionExecutionTarget.REAL_H3,
            {
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
            },
            {
                "video.enqueue",
                "video.retake",
                "video.extend",
                "video.gap_fill",
                "task.drain",
                "task.reconcile",
            },
        ),
        (
            None,
            {"project.get", "video.inspect_take", "route.plan_h3"},
            {
                "video.enqueue",
                "video.retake",
                "video.extend",
                "video.gap_fill",
                "task.drain",
                "task.reconcile",
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
                "route.resolve_h3_submission",
                "route.resolve_h3_not_submitted",
            },
        ),
    ],
)
def test_language_planner_catalog_filters_incompatible_generation_tools(
    tmp_path: Path,
    target: ProductionExecutionTarget | None,
    present: set[str],
    absent: set[str],
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "Stop after catalog inspection.",
                    "goal_complete": True,
                    "steps": [
                        {
                            "id": "checkpoint",
                            "title": "Checkpoint",
                            "kind": "CHECKPOINT",
                            "assigned_agent": "production-manager",
                            "instructions": "Record the bounded inspection.",
                        }
                    ],
                }
            )
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

    service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the tools available for this target.",
            execution_target=target,
        ),
    )

    tool_ids = {
        tool["id"]
        for tool in json.loads(client.inputs[0])["available_tools"]
    }
    assert present <= tool_ids
    assert tool_ids.isdisjoint(absent)
    assert {"project.get", "video.inspect_take", "route.plan_h3", "task.advance"} <= (
        tool_ids
    )


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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="specialist-alpha",
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
    assert work_order.work_order.recipient_role == "specialist-alpha"
    assert (
        work_order.work_order.production_skill_role
        == ProductionSkillRole.DIRECTOR
    )
    assert work_order.work_order.constraints == ["Preserve the accepted ending."]
    assert "当前制片技能：AI 原生短剧" in work_order.system_prompt
    assert "导演的通用职责" in work_order.system_prompt
    assert "把已批准的文本意图转成可见的表演、调度、构图、动作、声音与时间设计" in (
        work_order.system_prompt
    )
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
    assert all(
        action.tool_name != "planning.advance"
        for action in advanced.run.snapshot.actions
    )
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


def test_failed_actions_can_replan_beyond_the_legacy_fixed_count(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": (
                        "Record recovery after repeated failed plans."
                        if index == 5
                        else f"Retry invalid inspection plan {index}."
                    ),
                    "goal_complete": index == 5,
                    "steps": [
                        (
                            {
                                "id": "recovered",
                                "title": "Record recovery",
                                "kind": "CHECKPOINT",
                                "assigned_agent": "production-manager",
                                "instructions": "Record the recovered plan.",
                            }
                            if index == 5
                            else {
                                "id": f"invalid-inspection-{index}",
                                "title": f"Invalid inspection {index}",
                                "kind": "TOOL_CALL",
                                "assigned_agent": "production-manager",
                                "tool_name": "project.get",
                                "instructions": "Attempt an inspection without its required input.",
                                "arguments": {},
                            }
                        )
                    ],
                }
            )
            for index in range(1, 6)
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
            objective="Recover after more than four consecutive failed plans.",
            initial_plan=AgentPlanProposal(
                summary="Start with an invalid inspection.",
                steps=[
                    PlanStep(
                        id="invalid-inspection-0",
                        title="Invalid inspection 0",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Attempt an inspection without its required input.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=6),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert [action.status for action in advanced.run.snapshot.actions] == [
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.SUCCEEDED,
    ]
    assert len(advanced.executed_action_ids) == 6
    assert len(client.inputs) == 5
    assert not any("bounded retry limit" in note for note in advanced.notes)


def test_completed_plan_waits_when_replan_has_no_actionable_or_terminal_path(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "No justified next action is available yet.",
                    "goal_complete": False,
                    "steps": [],
                }
            )
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
            objective="Stop naturally when replanning has no supported next action.",
            initial_plan=AgentPlanProposal(
                summary="Record the current state before reconsidering the plan.",
                steps=[
                    PlanStep(
                        id="checkpoint",
                        title="Record current state",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Persist the current state.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.WAITING
    assert len(advanced.executed_action_ids) == 1
    assert len(client.inputs) == 1
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.revision == 1
    assert advanced.run.ready_steps == []
    assert any("invalid plan payload" in note for note in advanced.notes)


def test_external_action_settlement_waits_after_one_invalid_replan(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "No justified next action is available yet.",
                    "goal_complete": False,
                    "steps": [],
                }
            )
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
            objective="Complete a delegated review and reconsider the next plan.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one review before replanning.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Return one concise staging review.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    completed = service.submit_agent_work_order_result(
        waiting.created_work_order_ids[0],
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The staging review is complete.",
        ),
    )

    assert completed.run.snapshot.run.status == AgentRunStatus.WAITING
    assert len(client.inputs) == 1
    assert completed.run.snapshot.current_plan is not None
    assert completed.run.snapshot.current_plan.revision == 1
    assert completed.run.ready_steps == []


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
        *[
            json.dumps(
                {
                    "tool_name": "project.get",
                    "arguments": {"project_id": project.id},
                    "reason": f"Inspect canonical evidence item {index} before deciding.",
                }
            )
            for index in range(1, 7)
        ],
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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

    work_order_id = waiting.created_work_order_ids[0]
    partial_results = []
    while True:
        response = service.execute_agent_work_order(work_order_id)
        if response.result.status == AgentResultStatus.COMPLETED:
            break
        partial_results.append(response.result)

    assert response.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(partial_results) == 12
    assert all(item.status == AgentResultStatus.PARTIAL for item in partial_results)
    assert len(client.inputs) == 7
    first_payload = json.loads(client.inputs[0])
    assert "project.get" in {
        tool["id"] for tool in first_payload["available_tools"]
    }
    final_payload = json.loads(client.inputs[-1])
    assert len(final_payload["tool_history"]) == 6
    assert [
        len(json.loads(value)["tool_history"]) for value in client.inputs
    ] == list(range(7))
    assert all(
        item["result"]["status"] == "SUCCEEDED"
        for item in final_payload["tool_history"]
    )
    assert all(
        "tool_calls_remaining" not in json.loads(value) for value in client.inputs
    )
    assert all("轮次已用完" not in value for value in client.instructions)
    assert len(response.result.evidence_refs) == 1
    assert response.result.evidence_refs[0].artifact_id == project.id
    experiences = service.agent_runtime_store.list_experiences(project.id)
    assert experiences[0].summary == response.result.concise_summary
    assert "scene-director" in experiences[0].tags
    assert project.production_profile.value in experiences[0].tags


def test_specialist_cannot_bypass_parent_execution_target(tmp_path: Path) -> None:
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
                "tool_name": "video.enqueue",
                "arguments": {"project_id": project.id, "request": {}},
                "reason": "Attempt a Mock generation from a REAL_H3 specialist.",
            }
        )
    ]
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Delegate one bounded production review.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            initial_plan=AgentPlanProposal(
                summary="Delegate the review.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Inspect the project and act only if compatible.",
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

    requested = service.execute_agent_work_order(work_order_id)

    assert requested.result.status == AgentResultStatus.PARTIAL
    assert service.repository.list_tasks(project.id) == []

    with pytest.raises(
        ProductionManagerError,
        match=r"video\.enqueue.*MOCK.*REAL_H3",
    ):
        service.execute_agent_work_order(work_order_id)

    tool_ids = {
        tool["id"]
        for tool in json.loads(client.inputs[0])["available_tools"]
    }
    assert "video.enqueue" not in tool_ids
    assert "route.execute_h3_plan" in tool_ids
    assert "project.get" in tool_ids
    assert service.repository.list_tasks(project.id) == []
    work_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert work_order is not None
    assert work_order.status == DelegationStatus.NEEDS_ATTENTION
    assert len(work_order.specialist_turns) == 1
    assert work_order.specialist_turns[0].result is not None
    assert work_order.specialist_turns[0].result["status"] == "FAILED"


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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
    assert guided.run.snapshot.run.render_export is False
    assert guided.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert guided.run.context.render_export is False
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
    assert automatic.run.snapshot.current_plan.revision == 1
    assert automatic.run.snapshot.run.status == AgentRunStatus.WAITING
    assert automatic.run.snapshot.run.unattended is True
    assert (
        automatic.run.snapshot.run.execution_target
        == ProductionExecutionTarget.MOCK
    )
    assert automatic.run.snapshot.run.render_export is False
    assert automatic.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert automatic.run.context.render_export is False
    assert automatic.run.ready_steps == []
    assert any(
        "semantic Production Manager" in blocker
        for blocker in automatic.run.snapshot.memory.blockers
    )


def test_local_fallback_does_not_author_a_mock_production_route(
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

    aggregate = service.get_project(created.project_id)
    assert created.run.snapshot.run.status == AgentRunStatus.WAITING
    assert aggregate.document.project.stage.value != "EXPORTED"
    assert aggregate.exports == []
    assert created.run.ready_steps == []
    assert all(
        action.tool_name not in {"video.enqueue", "task.drain", "timeline.export"}
        for action in created.run.snapshot.actions
    )
    assert created.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert created.run.context.render_export is True
    assert "autonomous_runs" not in aggregate.document.model_dump()


def test_existing_project_without_semantic_planner_waits_for_an_explicit_plan(
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

    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.snapshot.run.unattended is True
    assert run.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert run.snapshot.run.render_export is True
    assert run.context.execution_target == ProductionExecutionTarget.MOCK
    assert run.context.render_export is True
    assert run.snapshot.current_plan is None
    assert run.ready_steps == []
    assert any(
        "supply initial_plan" in blocker for blocker in run.snapshot.memory.blockers
    )


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
    assert older.snapshot.run.status == AgentRunStatus.WAITING
    assert older.ready_steps == []

    current = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Produce a deterministic Mock draft.",
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        ),
    )

    assert current.snapshot.run.status == AgentRunStatus.WAITING
    assert current.ready_steps == []
    assert older.context.execution_target == ProductionExecutionTarget.REAL_H3
    assert older.context.render_export is False
    assert current.context.execution_target == ProductionExecutionTarget.MOCK
    assert current.context.render_export is True
    assert current.context.decisions[0].run_id == current.snapshot.run.id
    assert current.context.decisions[0].statement == "Render export: yes"
    assert any(
        decision.statement == "Execution target: MOCK"
        and decision.run_id == current.snapshot.run.id
        for decision in current.context.decisions
    )


def test_context_options_are_not_reconstructed_from_decision_text(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Keep explicit runtime options typed.",
            auto_plan=False,
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        ),
    )
    service.agent_runtime.remember_decision(
        project_id=project.id,
        run_id=run.snapshot.run.id,
        statement="Execution target: REAL_H3",
        rationale="A deliberately contradictory readable trace entry.",
    )
    service.agent_runtime.remember_decision(
        project_id=project.id,
        run_id=run.snapshot.run.id,
        statement="Render export: yes",
        rationale="A deliberately contradictory readable trace entry.",
    )

    current = service.get_agent_run(run.snapshot.run.id)

    assert current.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert current.snapshot.run.render_export is False
    assert current.context.execution_target == ProductionExecutionTarget.MOCK
    assert current.context.render_export is False
    assert {decision.statement for decision in current.context.decisions} >= {
        "Execution target: REAL_H3",
        "Render export: yes",
    }


@pytest.mark.parametrize(
    ("target", "tool_name", "required"),
    [
        (ProductionExecutionTarget.REAL_H3, "video.enqueue", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.retake", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.extend", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.gap_fill", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "task.drain", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "task.reconcile", "MOCK"),
        (ProductionExecutionTarget.MOCK, "route.enqueue_h3_probe", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.execute_h3_plan", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.list_h3_reconciliation", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.resolve_h3_submission", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.resolve_h3_not_submitted", "REAL_H3"),
    ],
)
def test_agent_plan_rejects_incompatible_execution_tool(
    tmp_path: Path,
    target: ProductionExecutionTarget,
    tool_name: str,
    required: str,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(
        ProductionManagerError,
        match=rf"{re.escape(tool_name)}.*{required}",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Attempt an incompatible generation action.",
                execution_target=target,
                initial_plan=_single_tool_plan(
                    tool_name,
                    {"project_id": project.id},
                ),
            ),
        )

    assert service.repository.list_tasks(project.id) == []


def test_provider_execution_tool_requires_an_explicit_run_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(ProductionManagerError, match="has no target"):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Attempt generation without choosing a provider target.",
                initial_plan=_single_tool_plan(
                    "video.enqueue",
                    {"project_id": project.id, "request": {}},
                ),
            ),
        )


def test_real_h3_run_cannot_advance_a_mock_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    task = service.enqueue(
        project.id,
        EnqueueRequest(candidate_count=1),
    ).tasks[0]

    with pytest.raises(
        ProductionManagerError,
        match=r"task\.advance.*MOCK.*REAL_H3",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Advance only tasks for the selected provider.",
                execution_target=ProductionExecutionTarget.REAL_H3,
                initial_plan=_single_tool_plan(
                    "task.advance",
                    {"task_id": task.id},
                ),
            ),
        )

    persisted = service.repository.get_task(task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.QUEUED


def test_local_planner_requires_a_semantic_planner_or_caller_plan(
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
    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.snapshot.current_plan is None
    assert run.ready_steps == []
    assert any(
        "configure the language planner or supply initial_plan" in blocker
        for blocker in run.snapshot.memory.blockers
    )

    caller_planned = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the canonical project.",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project.id},
            ),
        ),
    )
    assert caller_planned.snapshot.run.status == AgentRunStatus.RUNNING
    assert caller_planned.ready_steps[0].tool_name == "project.get"


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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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
                        production_skill_role=ProductionSkillRole.DIRECTOR,
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


def test_local_fallback_never_authors_a_provider_task_route(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    queued = service.enqueue(project.id, EnqueueRequest(candidate_count=2))
    assert len(queued.tasks) > 8
    assert all(task.provider == "mock" for task in queued.tasks)
    mock_task_ids = {task.id for task in queued.tasks}

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Continue all active generation tasks.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )

    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.ready_steps == []
    assert service.repository.list_tasks(project.id) == queued.tasks
    assert mock_task_ids == {task.id for task in queued.tasks}
