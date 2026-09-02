"""Agent control-plane coverage for persisted long-form planning."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from short_drama.agent_runtime import ActionKind, ActionStatus, AgentRunStatus
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ProductionExecutionTarget,
    CreateProjectRequest,
    PlanningRunStatus,
    PlanningRunStep,
    ProductionProfile,
    ProjectStage,
    RunMode,
    SourceMode,
)
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentProjectCreateRequest,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunReplanRequest,
    AgentUserInputRequest,
)
from short_drama.production_planner import EpisodeOutline, _fallback_episode_outline
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCategory


class StoryArchitectClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        outline_payload = _fallback_episode_outline(request).model_dump(mode="python")
        outline_payload["lines"] = []
        for scene in outline_payload["scenes"]:
            scene["dialogue_line_indices"] = []
        self.outline = EpisodeOutline.model_validate(outline_payload)
        self.calls = 0
        self.fail_with_runtime_error = False

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
        self.calls += 1
        if self.fail_with_runtime_error:
            raise RuntimeError("temporary planning provider failure")
        return self.outline.model_dump_json()


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="Agent-owned long planning",
        source_mode=SourceMode.AUTO_ORIGINAL,
        input_text="Two siblings trace an impossible message across one night.",
        run_mode=RunMode.AUTO_DRAFT,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        target_duration_seconds=180,
    )


def _service(
    root: Path,
    client: StoryArchitectClient,
) -> ShortDramaService:
    service = ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
        language_provider_factory=lambda _api_key, _base_url: client,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    return service


def _create_long_agent_project(
    service: ShortDramaService,
    request: CreateProjectRequest,
):
    return service.create_agent_project(
        AgentProjectCreateRequest(
            project=request,
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        )
    )


def test_long_planning_is_exposed_as_one_bounded_agent_tool_action(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)

    created = _create_long_agent_project(service, request)
    aggregate = service.get_project(created.project_id)
    planning = aggregate.document.planning

    assert service.tool_registry.describe("planning.advance").category == (
        ToolCategory.PLANNING
    )
    assert aggregate.document.project.stage == ProjectStage.INPUT
    assert planning is not None
    assert planning.status == PlanningRunStatus.RUNNING
    assert planning.owner_agent_run_id == created.run.snapshot.run.id
    assert planning.run_id is not None
    assert client.calls == 0
    assert created.run.ready_steps[0].tool_name == "planning.advance"

    advanced = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )
    persisted = service.get_planning_run(planning.run_id).run

    assert client.calls == 1
    assert persisted.current_step == PlanningRunStep.SCENE_WRITER
    assert advanced.run.ready_steps[0].tool_name == "planning.advance"
    assert advanced.run.snapshot.actions[-1].status == ActionStatus.SUCCEEDED
    planning_artifact = next(
        artifact
        for artifact in advanced.run.context.artifacts
        if artifact.ref.artifact_type == "planning_run"
    )
    assert '"current_step":"SCENE_WRITER"' in planning_artifact.summary
    assert advanced.run.context.project_summary is not None
    assert "Planning RUNNING" in advanced.run.context.project_summary
    assert "multi-agent:" in advanced.run.context.project_summary


def test_agent_owned_planning_run_has_no_legacy_http_advance(
    tmp_path: Path,
    client: TestClient,
) -> None:
    request = _request()
    language_client = StoryArchitectClient(request)
    service = _service(tmp_path, language_client)
    created = _create_long_agent_project(service, request)
    planning = service.get_project(created.project_id).document.planning
    assert planning is not None and planning.run_id is not None

    client.app.dependency_overrides[get_short_drama_service] = lambda: service
    response = client.post(
        f"/api/short-drama/planning-runs/{planning.run_id}/advance"
    )

    assert response.status_code == 404
    assert language_client.calls == 0

    internally_advanced = service.advance_planning_run(planning.run_id)
    assert internally_advanced.run.current_step == PlanningRunStep.SCENE_WRITER
    assert language_client.calls == 1


def test_initial_plan_cannot_bypass_an_incomplete_owned_planning_run(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)

    secondary = service.create_agent_run(
        created.project_id,
        AgentRunCreateRequest(
            objective="Skip directly to completion.",
            initial_plan=AgentPlanProposal(
                summary="Claim completion without doing the persisted planning work.",
                steps=[],
                goal_complete=True,
            ),
        ),
    )

    assert secondary.snapshot.current_plan is not None
    assert secondary.snapshot.current_plan.goal_complete is False
    assert secondary.snapshot.current_plan.steps[0].id == "wait-for-owner-planning-run"
    assert secondary.ready_steps[0].kind == ActionKind.USER_INPUT
    assert client.calls == 0


def test_proposed_plan_cannot_bypass_the_owner_planning_precondition(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)

    replanned = service.replan_agent_run(
        created.run.snapshot.run.id,
        AgentRunReplanRequest(
            reason="Try to replace the persisted planning prerequisite.",
            proposed_plan=AgentPlanProposal(
                summary="Claim completion without advancing the planning cursor.",
                steps=[],
                goal_complete=True,
            ),
        ),
    )

    assert replanned.snapshot.current_plan is not None
    assert replanned.snapshot.current_plan.goal_complete is False
    assert replanned.ready_steps[0].tool_name == "planning.advance"
    assert client.calls == 0


def test_planning_attention_waits_for_explicit_retry_instead_of_looping(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    client.fail_with_runtime_error = True
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)

    waiting = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=8),
    ).run

    assert client.calls == 1
    assert waiting.snapshot.run.status == AgentRunStatus.WAITING
    assert waiting.snapshot.current_plan is not None
    assert waiting.snapshot.current_plan.steps[0].id == "repair-planning-run"
    pending_input = next(
        action
        for action in waiting.snapshot.actions
        if action.plan_step_id == "repair-planning-run"
    )
    assert pending_input.kind == ActionKind.USER_INPUT
    assert pending_input.status == ActionStatus.RUNNING
    assert any(
        "temporary planning provider failure" in artifact.summary
        for artifact in waiting.context.artifacts
        if artifact.ref.artifact_type == "planning_run"
    )

    client.fail_with_runtime_error = False
    retry_ready = service.submit_agent_user_input(
        created.run.snapshot.run.id,
        AgentUserInputRequest(
            action_id=pending_input.id,
            answer="The provider is available again; retry the persisted unit.",
        ),
    )

    assert client.calls == 1
    assert retry_ready.ready_steps[0].tool_name == "planning.advance"


def test_failed_planning_run_resumes_after_explicit_user_confirmation(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)
    aggregate = service.get_project(created.project_id)
    planning = aggregate.document.planning
    assert planning is not None and planning.run_id is not None

    persisted = service.repository.get_planning_run(planning.run_id)
    assert persisted is not None
    persisted.status = PlanningRunStatus.FAILED
    persisted.error_message = "The previous process could not continue."
    aggregate.document.planning = planning.model_copy(
        update={"status": PlanningRunStatus.FAILED}
    )
    service.repository.save_planning_run_with_project(
        persisted,
        aggregate.document,
    )

    failed = service.replan_agent_run(
        created.run.snapshot.run.id,
        AgentRunReplanRequest(reason="Inspect the failed persisted planning cursor."),
    )
    assert failed.snapshot.current_plan is not None
    assert failed.snapshot.current_plan.steps[0].id == "resolve-failed-planning-run"

    waiting = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    ).run
    pending_input = next(
        action
        for action in waiting.snapshot.actions
        if action.plan_step_id == "resolve-failed-planning-run"
    )
    assert pending_input.status == ActionStatus.RUNNING

    retry_ready = service.submit_agent_user_input(
        created.run.snapshot.run.id,
        AgentUserInputRequest(
            action_id=pending_input.id,
            answer="The provider and inputs are repaired; retry the persisted unit.",
        ),
    )

    assert retry_ready.snapshot.current_plan is not None
    assert retry_ready.snapshot.current_plan.steps[0].id == "advance-content-planning"
    assert retry_ready.ready_steps[0].tool_name == "planning.advance"
    assert client.calls == 0


def test_restart_reconciles_to_the_persisted_planning_cursor(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)
    run_id = created.run.snapshot.run.id
    planning = service.get_project(created.project_id).document.planning
    assert planning is not None and planning.run_id is not None
    assert created.run.snapshot.current_plan is not None
    step = created.run.ready_steps[0]

    interrupted = service.agent_runtime.begin_action(
        run_id,
        kind=ActionKind.TOOL_CALL,
        agent_id="production-manager",
        input_summary=step.instructions,
        plan_revision_id=created.run.snapshot.current_plan.id,
        plan_step_id=step.id,
        tool_name=step.tool_name,
        artifact_refs=step.artifact_refs,
    )
    persisted = service.advance_planning_run(planning.run_id).run
    assert persisted.current_step == PlanningRunStep.SCENE_WRITER
    assert client.calls == 1

    reopened = _service(tmp_path, client)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted.id)
    recovered = reopened.get_agent_run(run_id)

    assert client.calls == 1
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.SUCCEEDED
    assert "SCENE_WRITER" in (recovered_action.output_summary or "")
    assert reopened.get_planning_run(planning.run_id).run.current_step == (
        PlanningRunStep.SCENE_WRITER
    )
    assert recovered.snapshot.run.status == AgentRunStatus.RUNNING
    assert recovered.ready_steps[0].tool_name == "planning.advance"
