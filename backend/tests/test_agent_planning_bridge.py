"""Agent control-plane coverage for persisted long-form planning."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from short_drama.agent_runtime import ActionKind, ActionStatus, AgentRunStatus
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ContentPlanningOutput,
    ProductionExecutionTarget,
    CreateProjectRequest,
    PlanningRunStatus,
    PlanningRunStep,
    ProductionProfile,
    ProjectStage,
    RunMode,
    ScriptReviewDisposition,
    ScriptReviewDispositionAction,
    ScriptReviewReport,
    SourceMode,
    StoryIntent,
    StorySourceContract,
)
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentProjectCreateRequest,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunReplanRequest,
    PlanStep,
    production_skill_id,
)
from short_drama.production_planner import (
    EpisodeOutline,
    NarrativeSkillSelection,
    PlannedDialogueLine,
    StoryOrientation,
    _fallback_ending_contract,
    _fallback_episode_outline,
)
from short_drama.script_documents import EpisodeScenePatch
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus, ToolCategory


def _test_story_intent() -> StoryIntent:
    return StoryIntent(
        audience_experience="让观众清楚理解核心命题，并看到它如何改变人物的处境。",
        dramatic_engine="围绕用户给出的异常事件持续产生可见的行动与后果。",
        progression_strategy="每个场景都改变人物关系、处境或观众对事件的理解。",
        tone_and_plausibility="服从本剧建立的调性和世界规则，不默认改成现实主义。",
        protected_elements=["用户给出的核心命题"],
        drift_risks=["不要用通用类型套路取代具体故事"],
    )


def _story_orientation_response(
    input_text: str,
    *,
    story_intent: StoryIntent,
    selected_narrative_skills: list[NarrativeSkillSelection],
) -> str:
    payload = json.loads(input_text)
    return StoryOrientation(
        source_contract=StorySourceContract.model_validate(
            payload["lossless_source_contract"]
        ),
        story_intent=story_intent,
        selected_narrative_skills=selected_narrative_skills,
        narrative_skill_decision=(
            "选择了与当前故事目标直接相关的组合技能。"
            if selected_narrative_skills
            else "当前故事使用通用角色与格式能力即可，不加载额外叙事技能。"
        ),
    ).model_dump_json()


class StoryArchitectClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        outline_payload = _fallback_episode_outline(request).model_dump(mode="python")
        outline_payload["ending_contract"] = _fallback_ending_contract(
            request
        ).model_dump(mode="python")
        outline_payload["lines"] = []
        for scene in outline_payload["scenes"]:
            scene["dialogue_line_indices"] = []
        self.outline = EpisodeOutline.model_validate(outline_payload)
        self.story_intent = _test_story_intent()
        self.selected_narrative_skills: list[NarrativeSkillSelection] = []
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
        del image_urls, max_output_tokens
        self.calls += 1
        if self.fail_with_runtime_error:
            raise RuntimeError("temporary planning provider failure")
        payload = json.loads(input_text)
        if "lossless_source_contract" in payload:
            return _story_orientation_response(
                input_text,
                story_intent=self.story_intent,
                selected_narrative_skills=self.selected_narrative_skills,
            )
        if "context" in payload and "available_tools" in payload:
            current_plan = AgentPlanProposal.model_validate(payload["current_plan"])
            current_step = current_plan.steps[0]
            return AgentPlanProposal(
                summary=(
                    "Retry the persisted planning unit now that the latest context "
                    "contains no missing user decision."
                ),
                steps=[
                    PlanStep(
                        id="retry-content-planning",
                        title="Retry content planning",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="planning.advance",
                        instructions=(
                            "Retry the persisted unit and retain its returned cursor."
                        ),
                        arguments=dict(current_step.arguments),
                        artifact_refs=list(current_step.artifact_refs),
                    )
                ],
            ).model_dump_json()
        return self.outline.model_dump_json()


class ScriptOnlyLanguageClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        outline_payload = _fallback_episode_outline(request).model_dump(mode="python")
        outline_payload["ending_contract"] = _fallback_ending_contract(
            request
        ).model_dump(mode="python")
        outline_payload["title"] = "数学退化一百倍，唯独我不变"
        outline_payload["story_summary"] = (
            "所有人的数学能力突然退化一百倍，我用仍然正常的计算能力破局。"
        )
        outline_payload["lines"] = []
        scene = outline_payload["scenes"][0]
        scene["heading"] = "内景·重点高中数学公开课·日"
        scene["summary"] = "全校陷入简单计算恐慌，我当场揭开错题并扭转局面。"
        scene["narrative_purpose"] = "建立全员退化的异常与主角唯一优势。"
        scene["playable_actions"] = [
            "数学老师对着五加三的算式反复擦汗",
            "我走上讲台写下答案八，全场瞬间安静",
        ]
        scene["dialogue_line_indices"] = []
        scene["screenplay_text"] = ""
        self.outline = EpisodeOutline.model_validate(outline_payload)
        self.story_intent = _test_story_intent()
        self.selected_narrative_skills: list[NarrativeSkillSelection] = []
        self.scene_director_calls = 0
        self.final_cold_read_calls = 0

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
        payload = json.loads(input_text)
        if "lossless_source_contract" in payload:
            return _story_orientation_response(
                input_text,
                story_intent=self.story_intent,
                selected_narrative_skills=self.selected_narrative_skills,
            )
        if "story-architect Agent" in instructions:
            return self.outline.model_dump_json()
        if "Scene Writer Agent" in instructions:
            scene = self.outline.scenes[int(payload["current_scene_node"]["sequence"]) - 1]
            first_character = self.outline.characters[0].name
            second_character = self.outline.characters[
                min(1, len(self.outline.characters) - 1)
            ].name
            lines = [
                PlannedDialogueLine(
                    speaker=first_character,
                    text="五加三……到底等于多少？",
                ),
                PlannedDialogueLine(
                    speaker=second_character,
                    text="不用猜了，答案是八。",
                ),
            ]
            screenplay_text = "\n".join(
                [
                    f"【{scene.heading}】",
                    scene.playable_actions[0],
                    f"{lines[0].speaker}（嗓音发抖）：{lines[0].text}",
                    scene.playable_actions[1],
                    f"{lines[1].speaker}（平静）：{lines[1].text}",
                ]
            )
            return EpisodeScenePatch(
                scene_sequence=scene.sequence,
                heading=scene.heading,
                summary=scene.summary,
                narrative_purpose=scene.narrative_purpose,
                playable_actions=list(scene.playable_actions),
                screenplay_text=screenplay_text,
                target_duration_seconds=scene.target_duration_seconds,
                location_names=list(scene.location_names),
                continuity_in=scene.continuity_in.model_copy(deep=True),
                continuity_out=scene.continuity_out.model_copy(deep=True),
                lines=lines,
            ).model_dump_json()
        if "final cold-read Agent（最终冷读编辑）" in instructions:
            self.final_cold_read_calls += 1
            return ScriptReviewReport(
                summary="异常、主角优势和首次爽点已清晰建立。",
                strengths=["动作、反应和台词在同一时间线中推进"],
                issues=[],
                performance_timing_assessment=(
                    "自然通读对白与动作后，实际文本可由目标时长承载。"
                ),
                requires_revision=False,
            ).model_dump_json()
        if "script-critic Agent（剧本冷读 Agent）" in instructions:
            return ScriptReviewReport(
                summary="异常、主角优势和首次爽点已清晰建立。",
                strengths=["动作、反应和台词在同一时间线中推进"],
                issues=[],
                performance_timing_assessment=(
                    "自然通读对白与动作后，实际文本可由目标时长承载。"
                ),
                requires_revision=False,
            ).model_dump_json()
        if "Review Router Agent" in instructions:
            return ScriptReviewDisposition(
                action=ScriptReviewDispositionAction.CONTINUE,
                issue_routes=[],
                rationale="无需剧本修订。",
            ).model_dump_json()
        if "你是场景导演 Agent" in instructions:
            self.scene_director_calls += 1
            raise AssertionError("SCRIPT_ONLY must not invoke the Scene Director")
        raise AssertionError(f"Unexpected language call: {instructions[:120]}")


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
    client: StoryArchitectClient | ScriptOnlyLanguageClient,
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


def test_guided_agent_owned_project_uses_the_same_incremental_planning_job(
    tmp_path: Path,
) -> None:
    request = _request().model_copy(update={"run_mode": RunMode.GUIDED})
    language_client = StoryArchitectClient(request)
    service = _service(tmp_path, language_client)

    project = service.create_project(
        request,
        project_id="project-guided-incremental-planning",
        owner_agent_run_id="agent-run-guided-incremental-planning",
    )

    planning = project.document.planning
    assert planning is not None and planning.run_id is not None
    assert project.document.project.stage == ProjectStage.INPUT
    assert language_client.calls == 0
    run = service.get_planning_run(planning.run_id).run
    assert run.current_step == PlanningRunStep.STORY_ORIENTATION
    assert run.owner_agent_run_id == "agent-run-guided-incremental-planning"


def test_unowned_compat_project_creation_never_hides_language_generation(
    tmp_path: Path,
) -> None:
    request = _request()
    language_client = StoryArchitectClient(request)
    service = _service(tmp_path, language_client)

    project = service.create_project(
        request,
        project_id="project-unowned-compat-scaffold",
    )

    assert language_client.calls == 0
    assert project.document.planning is not None
    assert project.document.planning.owner_agent_run_id is None
    assert project.document.planning.run_id is None


def test_script_only_agent_run_stops_with_a_complete_readable_screenplay(
    tmp_path: Path,
) -> None:
    request = CreateProjectRequest(
        name="数学退化一百倍，唯独我不变",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="所有人的数学水平退化100倍，只有我不变的5分钟爽文AI短剧。",
        run_mode=RunMode.AUTO_DRAFT,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        content_planning_output=ContentPlanningOutput.SCRIPT_ONLY,
        target_duration_seconds=300,
    )
    language_client = ScriptOnlyLanguageClient(request)
    service = _service(tmp_path, language_client)
    created = _create_long_agent_project(service, request)

    current = created.run
    planning_action_ids: list[str] = []
    for _ in range(12):
        if current.snapshot.run.status == AgentRunStatus.COMPLETED:
            break
        response = service.advance_agent_run(
            created.run.snapshot.run.id,
            AgentRunAdvanceRequest(max_actions=8),
        )
        current = response.run
        assert len(response.executed_action_ids) <= 1
        if response.executed_action_ids:
            action_id = response.executed_action_ids[0]
            action = next(
                item for item in current.snapshot.actions if item.id == action_id
            )
            assert action.tool_name == "planning.advance"
            assert action.status == ActionStatus.SUCCEEDED
            planning_action_ids.append(action_id)
    completed = current
    aggregate = service.get_project(created.project_id)
    document = aggregate.document
    planning = document.planning
    assert planning is not None and planning.run_id is not None
    job = service.get_planning_run(planning.run_id).run

    assert completed.snapshot.run.status == AgentRunStatus.COMPLETED
    assert completed.snapshot.current_plan is not None
    assert completed.snapshot.current_plan.goal_complete is True
    assert completed.snapshot.current_plan.steps == []
    assert completed.ready_steps == []
    assert document.project.content_planning_output == ContentPlanningOutput.SCRIPT_ONLY
    assert document.project.stage == ProjectStage.SCRIPT_READY
    assert planning.status == PlanningRunStatus.COMPLETE
    assert job.status == PlanningRunStatus.COMPLETE
    assert PlanningRunStep.FINALIZE in job.completed_steps
    assert PlanningRunStep.FINAL_COLD_READ not in job.completed_steps
    assert PlanningRunStep.SCENE_DIRECTOR not in job.completed_steps
    assert job.directed_scene_json == []
    assert language_client.scene_director_calls == 0
    assert language_client.final_cold_read_calls == 0

    assert len(document.script_versions) == 1
    assert len(document.bible_versions) == 1
    assert document.assets == []
    assert document.shots == []
    assert document.execution_plan == []
    assert document.reference_plan == []
    assert document.manifests == []
    script = document.script_versions[0]
    assert script.original_text == request.input_text
    for scene in document.script_versions[0].structured_scenes:
        assert scene.screenplay_text
        assert scene.screenplay_text in script.screenplay_text
    outline = EpisodeOutline.model_validate_json(job.outline_json or "")
    for scene in outline.scenes:
        for action in scene.playable_actions:
            assert action in script.screenplay_text
    for line in script.lines:
        assert line.original_text in script.screenplay_text

    tool_names = [
        action.tool_name
        for action in completed.snapshot.actions
        if action.tool_name is not None
    ]
    assert tool_names[0] == "project.create"
    assert set(tool_names) <= {"project.create", "planning.advance"}
    assert planning_action_ids
    assert len(planning_action_ids) == tool_names.count("planning.advance")


def test_http_advance_exposes_each_planning_progress_unit_separately(
    tmp_path: Path,
    client: TestClient,
) -> None:
    request = CreateProjectRequest(
        name="HTTP 逐步剧本规划",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="用两个可观察步骤验证剧本规划进度。",
        run_mode=RunMode.AUTO_DRAFT,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        content_planning_output=ContentPlanningOutput.SCRIPT_ONLY,
        target_duration_seconds=120,
    )
    language_client = ScriptOnlyLanguageClient(request)
    service = _service(tmp_path, language_client)
    created = _create_long_agent_project(service, request)
    run_id = created.run.snapshot.run.id
    planning = service.get_project(created.project_id).document.planning
    assert planning is not None and planning.run_id is not None
    client.app.dependency_overrides[get_short_drama_service] = lambda: service

    first = client.post(
        f"/api/short-drama/agent-runs/{run_id}/advance",
        json={"max_actions": 8},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["executed_action_ids"]) == 1
    first_action_id = first_payload["executed_action_ids"][0]
    first_action = next(
        item
        for item in first_payload["run"]["snapshot"]["actions"]
        if item["id"] == first_action_id
    )
    assert first_action["tool_name"] == "planning.advance"
    assert first_action["status"] == "SUCCEEDED"
    assert service.get_planning_run(planning.run_id).run.current_step == (
        PlanningRunStep.STORY_ARCHITECT
    )
    assert first_payload["run"]["ready_steps"][0]["tool_name"] == (
        "planning.advance"
    )

    second = client.post(
        f"/api/short-drama/agent-runs/{run_id}/advance",
        json={"max_actions": 8},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["executed_action_ids"]) == 1
    second_action_id = second_payload["executed_action_ids"][0]
    second_action = next(
        item
        for item in second_payload["run"]["snapshot"]["actions"]
        if item["id"] == second_action_id
    )
    assert second_action["tool_name"] == "planning.advance"
    assert second_action["status"] == "SUCCEEDED"
    assert second_action_id != first_action_id
    assert service.get_planning_run(planning.run_id).run.current_step == (
        PlanningRunStep.SCENE_WRITER
    )
    planning_actions = [
        item
        for item in second_payload["run"]["snapshot"]["actions"]
        if item["tool_name"] == "planning.advance"
    ]
    assert len(planning_actions) == 2


def test_short_auto_draft_agent_project_uses_owned_content_planning_job(
    tmp_path: Path,
    client: TestClient,
) -> None:
    request = CreateProjectRequest(
        name="60 秒 Agent 逐步规划",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="一名学生发现全校只有自己还能正常计算。",
        run_mode=RunMode.AUTO_DRAFT,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        target_duration_seconds=60,
    )
    language_client = StoryArchitectClient(request)
    service = _service(tmp_path, language_client)

    created = _create_long_agent_project(service, request)
    aggregate = service.get_project(created.project_id)
    planning = aggregate.document.planning

    assert language_client.calls == 0
    assert aggregate.document.project.stage == ProjectStage.INPUT
    assert planning is not None
    assert planning.status == PlanningRunStatus.RUNNING
    assert planning.owner_agent_run_id == created.run.snapshot.run.id
    assert planning.run_id is not None
    assert created.run.ready_steps[0].tool_name == "planning.advance"
    persisted = service.get_planning_run(planning.run_id).run
    assert persisted.owner_agent_run_id == created.run.snapshot.run.id
    assert persisted.current_step == PlanningRunStep.STORY_ORIENTATION

    client.app.dependency_overrides[get_short_drama_service] = lambda: service
    advanced = client.post(
        f"/api/short-drama/agent-runs/{created.run.snapshot.run.id}/advance",
        json={"max_actions": 8},
    )

    assert advanced.status_code == 200
    payload = advanced.json()
    assert len(payload["executed_action_ids"]) == 1
    assert language_client.calls == 1
    persisted = service.get_planning_run(planning.run_id).run
    assert persisted.current_step == PlanningRunStep.STORY_ARCHITECT
    assert payload["run"]["ready_steps"][0]["tool_name"] == "planning.advance"


def test_default_content_planning_output_still_builds_the_full_plan(
    tmp_path: Path,
) -> None:
    request = CreateProjectRequest(
        name="默认完整制片",
        source_mode=SourceMode.AUTO_ORIGINAL,
        input_text="一个人在雨夜发现了一条不可能的留言。",
        target_duration_seconds=60,
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )

    aggregate = service.create_project(request)
    document = aggregate.document

    assert request.content_planning_output == ContentPlanningOutput.SCRIPT_AND_SHOTS
    assert document.project.content_planning_output == (
        ContentPlanningOutput.SCRIPT_AND_SHOTS
    )
    assert document.project.stage == ProjectStage.PLANNED
    assert document.assets
    assert document.shots
    assert document.execution_plan
    assert document.reference_plan


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
    assert [
        ref.artifact_type for ref in created.run.ready_steps[0].artifact_refs
    ] == ["content_planning_job"]
    listed_runs = service.list_agent_runs(created.project_id).runs
    assert [item.snapshot.run.id for item in listed_runs] == [
        created.run.snapshot.run.id
    ]

    advanced = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )
    persisted = service.get_planning_run(planning.run_id).run

    assert client.calls == 1
    assert persisted.current_step == PlanningRunStep.STORY_ARCHITECT
    assert advanced.run.ready_steps[0].tool_name == "planning.advance"
    assert advanced.run.snapshot.actions[-1].status == ActionStatus.SUCCEEDED
    assert advanced.run.snapshot.actions[-1].kind == ActionKind.TOOL_CALL
    assert advanced.run.snapshot.actions[-1].tool_name == "planning.advance"
    planning_artifact = next(
        artifact
        for artifact in advanced.run.context.artifacts
        if artifact.ref.artifact_type == "content_planning_job"
    )
    assert '"current_step":"STORY_ARCHITECT"' in planning_artifact.summary
    assert advanced.run.content_planning_trace is not None
    assert advanced.run.content_planning_trace.planning_run_id == planning.run_id
    assert advanced.run.content_planning_trace.current_step == (
        PlanningRunStep.STORY_ARCHITECT
    )
    assert advanced.run.content_planning_trace.source_contract is not None
    assert advanced.run.content_planning_trace.story_intent is not None
    assert advanced.run.context.project_summary is not None
    assert "Content planning job RUNNING" in advanced.run.context.project_summary
    assert "Story Orientation Agent" in advanced.run.context.project_summary


def test_agent_trace_keeps_latest_revision_history_separate_from_active_cursor(
    tmp_path: Path,
) -> None:
    request = _request()
    service = _service(tmp_path, StoryArchitectClient(request))
    created = _create_long_agent_project(service, request)
    aggregate = service.get_project(created.project_id)
    planning = aggregate.document.planning

    assert planning is not None and planning.run_id is not None
    persisted = service.get_planning_run(planning.run_id).run.model_copy(
        update={
            "revision_scene_sequences": [],
            "revision_changed_scene_sequences": [],
            "latest_revision_target_scene_sequences": [1, 2],
            "latest_revision_changed_scene_sequences": [2],
        }
    )
    service.repository.save_planning_run_with_project(
        persisted,
        aggregate.document,
    )

    run_view = service.get_agent_run(created.run.snapshot.run.id)
    trace = run_view.content_planning_trace

    assert trace is not None
    assert trace.revision_scene_sequences == []
    assert trace.revision_changed_scene_sequences == []
    assert trace.latest_revision_target_scene_sequences == [1, 2]
    assert trace.latest_revision_changed_scene_sequences == [2]


def test_planning_narrative_context_is_exposed_to_the_owning_agent_run(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    intent = StoryIntent(
        audience_experience="让观众迅速看懂主角的稀缺优势，并持续获得公开翻盘的爽感。",
        dramatic_engine="把原本普通的能力变成全社会稀缺能力，不断改变主角地位。",
        progression_strategy="从身边人震惊逐步扩大到权威人物主动争取主角。",
        tone_and_plausibility="校园荒诞爽剧，允许夸张反应和戏剧化压缩。",
        protected_elements=["主角松弛、围观者失态的反差"],
        drift_risks=["不要漂移成程序审计剧"],
    )
    client.story_intent = intent
    client.selected_narrative_skills = [
        NarrativeSkillSelection(
            skill_id="narrative.payoff_escalation",
            rationale="用公开反应和地位变化连续兑现爽感。",
        ),
        NarrativeSkillSelection(
            skill_id="narrative.absurd_comedy",
            rationale="用荒诞扩大保留设定的喜剧性。",
        ),
    ]
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)
    project = service.get_project(created.project_id).document
    assert project.planning is not None and project.planning.run_id is not None

    advanced = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )
    planning = service.get_planning_run(project.planning.run_id).run

    assert planning.current_step == PlanningRunStep.STORY_ARCHITECT
    assert planning.story_intent == intent
    assert advanced.run.snapshot.run.active_skill_ids == [
        production_skill_id(request.production_profile),
        "narrative.payoff_escalation",
        "narrative.absurd_comedy",
    ]
    run_decisions = [
        decision
        for decision in service.agent_runtime_store.list_decisions(
            created.project_id,
            run_id=created.run.snapshot.run.id,
        )
        if decision.run_id == created.run.snapshot.run.id
    ]
    statements = [decision.statement for decision in run_decisions]
    story_statements = [
        statement for statement in statements if statement.startswith("当前故事")
    ]
    assert len(story_statements) == 3
    assert any(
        statement.startswith("当前故事创作意图：观众体验：")
        and "不要漂移成程序审计剧" in statement
        for statement in statements
    )
    assert (
        "当前故事选用叙事技能 narrative.payoff_escalation；"
        "选择理由：用公开反应和地位变化连续兑现爽感。"
    ) in statements
    assert all(
        decision.artifact_refs
        and decision.artifact_refs[0].artifact_id == planning.id
        for decision in run_decisions
        if decision.statement.startswith("当前故事")
    )

    service.production_manager._synchronize_planning_narrative_context(
        agent_run_id=created.run.snapshot.run.id,
        document=service.get_project(created.project_id).document,
        planning_run_id=planning.id,
    )
    repeated_statements = [
        decision.statement
        for decision in service.agent_runtime_store.list_decisions(
            created.project_id,
            run_id=created.run.snapshot.run.id,
        )
        if decision.run_id == created.run.snapshot.run.id
        and decision.statement.startswith("当前故事")
    ]
    assert repeated_statements == story_statements


def test_planning_tool_requires_the_owner_and_rejects_a_different_agent_run(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)
    owner_agent_run_id = created.run.snapshot.run.id
    planning = service.get_project(created.project_id).document.planning
    assert planning is not None and planning.run_id is not None
    initial = service.get_planning_run(planning.run_id).run

    missing_owner = service.tool_registry.invoke(
        "planning.advance",
        {
            "project_id": created.project_id,
            "run_id": planning.run_id,
        },
    )
    assert missing_owner.status == ToolCallStatus.INVALID_ARGUMENTS

    wrong_owner = service.tool_registry.invoke(
        "planning.advance",
        {
            "project_id": created.project_id,
            "run_id": planning.run_id,
            "owner_agent_run_id": "agent-run-not-the-owner",
        },
    )
    assert wrong_owner.status == ToolCallStatus.FAILED
    assert wrong_owner.error is not None
    assert "requesting AgentRun" in wrong_owner.error.message
    unchanged = service.get_planning_run(planning.run_id).run
    assert unchanged.current_step == initial.current_step
    assert unchanged.completed_steps == initial.completed_steps
    assert unchanged.updated_at == initial.updated_at
    assert client.calls == 0

    advanced = service.tool_registry.invoke(
        "planning.advance",
        {
            "project_id": created.project_id,
            "run_id": planning.run_id,
            "owner_agent_run_id": owner_agent_run_id,
        },
    )
    assert advanced.status == ToolCallStatus.SUCCEEDED
    assert [ref.artifact_type for ref in advanced.artifact_refs] == [
        "content_planning_job",
        "project",
    ]
    persisted = service.get_planning_run(planning.run_id).run
    assert persisted.current_step == PlanningRunStep.STORY_ARCHITECT
    assert client.calls == 1


def test_project_document_only_projects_content_planning_job_state(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)
    planning = service.get_project(created.project_id).document.planning
    assert planning is not None and planning.run_id is not None

    service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )
    job = service.get_planning_run(planning.run_id).run
    projection = service.get_project(created.project_id).document.planning
    assert projection is not None
    assert projection.run_id == job.id
    assert projection.owner_agent_run_id == job.owner_agent_run_id
    assert projection.planning_job_kind == job.job_kind == "CONTENT_PLANNING"
    assert projection.status == job.status
    assert projection.warnings == job.warnings

    projection_payload = projection.model_dump(mode="python")
    assert {
        "current_step",
        "completed_steps",
        "request",
        "outline_json",
        "scene_script_json",
        "directed_scene_json",
        "next_script_scene_sequence",
        "next_scene_sequence",
        "review_report",
        "error_message",
    }.isdisjoint(projection_payload)


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
    assert internally_advanced.run.current_step == PlanningRunStep.STORY_ARCHITECT
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


def test_planning_attention_returns_to_the_agent_without_a_user_gate(
    tmp_path: Path,
) -> None:
    request = _request()
    client = StoryArchitectClient(request)
    client.fail_with_runtime_error = True
    service = _service(tmp_path, client)
    created = _create_long_agent_project(service, request)

    progress = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=8),
    )

    assert client.calls == 2
    assert len(progress.executed_action_ids) == 1
    assert progress.run.snapshot.run.status == AgentRunStatus.WAITING
    assert not any(
        action.kind == ActionKind.USER_INPUT
        and action.status == ActionStatus.RUNNING
        for action in progress.run.snapshot.actions
    )
    assert any(
        "temporary planning provider failure" in artifact.summary
        for artifact in progress.run.context.artifacts
        if artifact.ref.artifact_type == "content_planning_job"
    )

    client.fail_with_runtime_error = False
    resumed = service.advance_agent_run(
        created.run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=8),
    )

    assert client.calls == 4
    assert resumed.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert len(resumed.executed_action_ids) == 1
    assert not any(
        action.kind == ActionKind.USER_INPUT
        and action.status == ActionStatus.RUNNING
        for action in resumed.run.snapshot.actions
    )


def test_failed_planning_run_is_semantically_replanned_without_user_confirmation(
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
    assert failed.snapshot.current_plan.steps[0].id == "retry-content-planning"
    assert failed.ready_steps[0].tool_name == "planning.advance"
    assert failed.ready_steps[0].kind == ActionKind.TOOL_CALL
    assert not any(
        action.kind == ActionKind.USER_INPUT
        and action.status == ActionStatus.RUNNING
        for action in failed.snapshot.actions
    )
    assert client.calls == 1


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
    assert persisted.current_step == PlanningRunStep.STORY_ARCHITECT
    assert client.calls == 1

    reopened = _service(tmp_path, client)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted.id)
    recovered = reopened.get_agent_run(run_id)

    assert client.calls == 1
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.SUCCEEDED
    assert "STORY_ARCHITECT" in (recovered_action.output_summary or "")
    assert reopened.get_planning_run(planning.run_id).run.current_step == (
        PlanningRunStep.STORY_ARCHITECT
    )
    assert recovered.snapshot.run.status == AgentRunStatus.RUNNING
    assert recovered.ready_steps[0].tool_name == "planning.advance"
