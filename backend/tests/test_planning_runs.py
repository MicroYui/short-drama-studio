"""Recoverable long-form script planning integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.models import (
    AutonomousExecutionTarget,
    AutonomousRunRecord,
    AutonomousRunStatus,
    AutonomousRunStep,
    PlanningRunCreateRequest,
    PlanningOutlineAgentRevisionRequest,
    PlanningOutlineDirectEditRequest,
    PlanningRunStatus,
    PlanningRunStep,
    ReviewRepairOwner,
    RevisionJudgeConfidence,
    RevisionJudgeRecommendation,
    ReviseScriptLineRequest,
    ReviseScriptLinesRequest,
    RunMode,
    ScriptLineRevision,
    ScriptReviewDisposition,
    ScriptReviewDispositionAction,
    ScriptReviewIssueRoute,
    ScriptReviewIssue,
    ScriptReviewReport,
    ScriptReviewSeverity,
    SourceMode,
    StoryRepairAction,
    StoryRepairDiagnosis,
    StoryRepairPlan,
    StorySourceContract,
    StoryRevisionVerification,
    StoryRevisionViolation,
)
from short_drama.production_planner import (
    EpisodeOutline,
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
)
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
)
from short_drama.service import ShortDramaService
from short_drama.script_documents import (
    EpisodeScenePatch,
    PlanningSceneRevisionRequest,
    ScriptRevisionContextPlan,
    ScriptRevisionScope,
    scene_dialogue_lines,
)


class StagedLanguageClient:
    def __init__(
        self,
        request: PlanningRunCreateRequest,
        *,
        revision_rounds_required: int = 0,
        fail_scene_once: int | None = None,
        review_issue_counts: list[int] | None = None,
        fail_judge: bool = False,
    ) -> None:
        self.request = request
        self.outline = _fallback_episode_outline(request)
        blueprint_payload = self.outline.model_dump(mode="python")
        if request.source_mode != SourceMode.SCRIPT_LOCKED:
            blueprint_payload["lines"] = []
            for scene in blueprint_payload["scenes"]:
                scene["dialogue_line_indices"] = []
        self.blueprint = EpisodeOutline.model_validate(blueprint_payload)
        self.revision_rounds_required = revision_rounds_required
        self.fail_scene_once = fail_scene_once
        self.review_issue_counts = review_issue_counts
        self.fail_judge = fail_judge
        self.critic_calls = 0
        self.revision_calls = 0
        self.repair_plan_calls = 0
        self.revision_verifier_calls = 0
        self.repair_plan_inputs: list[dict[str, object]] = []
        self.revision_inputs: list[dict[str, object]] = []
        self.revision_verifier_inputs: list[dict[str, object]] = []
        self.judge_calls = 0
        self.scene_calls: list[int] = []
        self.scene_inputs: list[dict[str, object]] = []
        self.script_scene_calls: list[int] = []
        self.script_scene_inputs: list[dict[str, object]] = []
        self.local_revision_calls: list[int] = []
        self.local_revision_inputs: list[dict[str, object]] = []
        self._failed_scene = False
        self.scene_plans = {}
        handoff = self.outline.scenes[0].continuity_in.model_copy(deep=True)
        for scene in self.outline.scenes:
            plan = _fallback_directed_scene_plan(request, self.outline, scene, handoff)
            plan.shots[0].narrative_purpose = (
                f"staged-scene-{scene.sequence}: " + plan.shots[0].narrative_purpose
            )
            plan.continuity_out.known_facts.append(
                f"staged-scene-{scene.sequence}-complete"
            )
            self.scene_plans[scene.sequence] = plan
            handoff = plan.continuity_out.model_copy(deep=True)

    def validate_credentials(self) -> None:
        pass

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        if "revision-judge Agent" in instructions:
            self.judge_calls += 1
            if self.fail_judge:
                raise LanguageProviderError("transient revision judge failure")
            payload = json.loads(input_text)
            candidates = payload["candidates"]
            older_round = int(candidates[0]["revision_round"])
            newer_round = int(candidates[1]["revision_round"])
            return RevisionJudgeRecommendation(
                recommended_revision_round=older_round,
                alternative_revision_round=newer_round,
                confidence=RevisionJudgeConfidence.HIGH,
                summary="较早版本闭合了核心因果，较新版本引入了新的解释冲突。",
                evidence=["开场异常与结尾机制在较早版本中直接对应。"],
                tradeoffs=["较新版本补充了信息，但削弱了因果清晰度。"],
            ).model_dump_json()
        if "independent script-critic Agent" in instructions:
            self.critic_calls += 1
            issue_count = (
                self.review_issue_counts[
                    min(self.critic_calls - 1, len(self.review_issue_counts) - 1)
                ]
                if self.review_issue_counts
                else int(self.critic_calls <= self.revision_rounds_required)
            )
            requires_revision = (
                issue_count > 0
                if self.review_issue_counts is not None
                else self.critic_calls <= self.revision_rounds_required
            )
            return ScriptReviewReport(
                summary=(
                    "因果链需要一次修订。"
                    if requires_revision
                    else "剧本可继续进入逐场规划。"
                ),
                strengths=["悬念线持续推进"],
                issues=[
                    ScriptReviewIssue(
                        category=f"CAUSAL_LOGIC_{index + 1}",
                        severity=(
                            ScriptReviewSeverity.HIGH
                            if index == 0
                            else ScriptReviewSeverity.MEDIUM
                        ),
                        scene_sequences=[1, len(self.outline.scenes)],
                        observation=f"第 {index + 1} 项因果尚未闭合。",
                        recommendation="明确规则并回收开场事件。",
                    )
                    for index in range(issue_count)
                ],
                requires_revision=requires_revision,
            ).model_dump_json()
        if "You are the Review Router Agent inside" in instructions:
            payload = json.loads(input_text)
            issues = payload["script_review"]["issues"]
            return ScriptReviewDisposition(
                action=(
                    ScriptReviewDispositionAction.REVISE_STORY
                    if issues
                    else ScriptReviewDispositionAction.CONTINUE
                ),
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=index,
                        owner=ReviewRepairOwner.STORY_ARCHITECT,
                        rationale="测试问题需要修改剧本因果。",
                    )
                    for index in range(1, len(issues) + 1)
                ],
                rationale="测试路由。",
            ).model_dump_json()
        if "source-contract Agent" in instructions:
            source = self.request.input_text.strip() or "生成完整测试短剧"
            return StorySourceContract(
                explicit_commitments=[source],
                explicit_prohibitions=[],
                open_creative_choices=["未由用户明确规定的剧情细节可调整"],
            ).model_dump_json()
        if "story-repair planner Agent" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issues = payload["routed_story_review"]["issues"]
            return StoryRepairPlan(
                summary="先诊断问题来源，再以最小改动修复剧本。",
                source_promise="保留核心异常、选择与结尾回收。",
                invariants=["保留已经成立的悬念推进"],
                actions=[
                    StoryRepairAction(
                        issue_index=index,
                        diagnosis=(
                            StoryRepairDiagnosis.SOURCE_CONCEPT_GAP
                            if index == 1
                            else StoryRepairDiagnosis.OUTLINE_DEFECT
                        ),
                        should_revise=True,
                        rationale="问题在来源桥接或当前大纲中有直接依据。",
                        affected_scene_sequences=list(issue["scene_sequences"]),
                        recommended_change="替换底层因果，不累加解释。",
                        remove_or_simplify=["重复证据"],
                        preserve=["核心悬念"],
                        validation_questions=["关键选择是否无需额外说明即可理解？"],
                    )
                    for index, issue in enumerate(issues, start=1)
                ],
            ).model_dump_json()
        if "story-revision verifier Agent" in instructions:
            self.revision_verifier_calls += 1
            payload = json.loads(input_text)
            self.revision_verifier_inputs.append(payload)
            required = [
                action["issue_index"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            return StoryRevisionVerification(
                accepted=True,
                summary="Writer 已落实 Repair Plan 且未引入回退。",
                satisfied_issue_indices=required,
                violations=[],
            ).model_dump_json()
        if "revising an episode" in instructions:
            self.revision_calls += 1
            self.revision_inputs.append(json.loads(input_text))
            payload = self.outline.model_dump(mode="python")
            payload["story_summary"] += (
                f" 第 {self.revision_calls} 轮已补全开场与结尾的因果闭环。"
            )
            return json.dumps(payload, ensure_ascii=False)
        if "Scene Writer Agent" in instructions:
            payload = json.loads(input_text)
            self.script_scene_inputs.append(payload)
            sequence = int(payload["current_scene_node"]["sequence"])
            self.script_scene_calls.append(sequence)
            node = self.blueprint.scenes[sequence - 1]
            source_scene = self.outline.scenes[sequence - 1]
            return EpisodeScenePatch(
                scene_sequence=sequence,
                heading=node.heading,
                summary=node.summary,
                narrative_purpose=node.narrative_purpose,
                playable_actions=list(node.playable_actions),
                target_duration_seconds=node.target_duration_seconds,
                location_names=list(node.location_names),
                continuity_in=node.continuity_in.model_copy(deep=True),
                continuity_out=node.continuity_out.model_copy(deep=True),
                lines=scene_dialogue_lines(self.outline, source_scene),
            ).model_dump_json()
        if "script-change scope Agent" in instructions:
            payload = json.loads(input_text)
            candidates = [
                item["scene_sequence"]
                for item in payload["retrieved_candidates"]
                if item["scene_sequence"] is not None
            ]
            return ScriptRevisionContextPlan(
                scope=ScriptRevisionScope.SCENE_PATCH,
                affected_scene_sequences=list(dict.fromkeys(candidates))[:2] or [1],
                retrieval_query=payload["user_instruction"],
                rationale="测试用最小场次范围。",
                preserve=["未相关场次"],
            ).model_dump_json()
        if "scene-local screenplay revision Agent" in instructions:
            payload = json.loads(input_text)
            self.local_revision_inputs.append(payload)
            patch = EpisodeScenePatch.model_validate(payload["current_scene"])
            self.local_revision_calls.append(patch.scene_sequence)
            patch.lines[0].text += "（局部改稿）"
            return patch.model_dump_json()
        if "story-architect Agent" in instructions:
            return self.blueprint.model_dump_json()
        payload = json.loads(input_text)
        self.scene_inputs.append(payload)
        sequence = int(payload["scene_contract"]["sequence"])
        self.scene_calls.append(sequence)
        if (
            sequence == self.fail_scene_once
            and not self._failed_scene
        ):
            self._failed_scene = True
            raise RuntimeError(f"transient scene {sequence} failure")
        return self.scene_plans[sequence].model_dump_json()


class SceneCountRevisionClient(StagedLanguageClient):
    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "script-change scope Agent" in instructions:
            return ScriptRevisionContextPlan(
                scope=ScriptRevisionScope.FULL_OUTLINE,
                affected_scene_sequences=[],
                retrieval_query="合并结尾场次",
                rationale="测试修订会改变全局场次结构。",
                preserve=["总时长", "核心因果"],
            ).model_dump_json()
        if "revising an episode" in instructions:
            self.revision_calls += 1
            payload = self.outline.model_dump(mode="python")
            scenes = payload["scenes"]
            previous = scenes[-2]
            removed = scenes[-1]
            previous["summary"] += " " + removed["summary"]
            previous["narrative_purpose"] += "；" + removed["narrative_purpose"]
            previous["target_duration_seconds"] += removed["target_duration_seconds"]
            previous["dialogue_line_indices"].extend(removed["dialogue_line_indices"])
            previous["location_names"] = list(
                dict.fromkeys(previous["location_names"] + removed["location_names"])
            )
            previous["continuity_out"] = removed["continuity_out"]
            payload["scenes"] = scenes[:-1]
            self.outline = EpisodeOutline.model_validate(payload)
            return self.outline.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class UserDirectedRevisionClient(StagedLanguageClient):
    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Story Architect editing the current episode" in instructions:
            payload = json.loads(input_text)
            outline = EpisodeOutline.model_validate(payload["current_outline"])
            revised = outline.model_copy(
                deep=True,
                update={
                    "title": "用户定向修改后的标题",
                    "story_summary": outline.story_summary + " 已按用户意见精简因果。",
                },
            )
            self.outline = revised
            return revised.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class CriticOverreachRepairClient(StagedLanguageClient):
    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-repair planner Agent" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issues = payload["routed_story_review"]["issues"]
            return StoryRepairPlan(
                summary="Critic 的观察不由当前大纲文本支持。",
                source_promise="保留原有核心因果。",
                invariants=["不为无依据的观察改写剧本"],
                actions=[
                    StoryRepairAction(
                        issue_index=index,
                        diagnosis=StoryRepairDiagnosis.CRITIC_OVERREACH,
                        should_revise=False,
                        rationale="当前大纲没有出现 Critic 所称的事实。",
                        affected_scene_sequences=list(issue["scene_sequences"]),
                        recommended_change="不修改剧本，保留为用户可选意见。",
                        preserve=["当前大纲"],
                        validation_questions=["该观察是否能在文本中直接定位？"],
                    )
                    for index, issue in enumerate(issues, start=1)
                ],
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class MalformedCriticJsonClient(StagedLanguageClient):
    def __init__(self, request: PlanningRunCreateRequest) -> None:
        super().__init__(request)
        self.invalid_critic_sent = False

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Repair only the invalid JSON or schema structure" in instructions:
            return ScriptReviewReport(
                summary="修复后的审阅仍保留原结论。",
                strengths=["核心因果可继续"],
                issues=[],
                requires_revision=False,
            ).model_dump_json()
        if "independent script-critic Agent" in instructions and not self.invalid_critic_sent:
            self.invalid_critic_sent = True
            valid = ScriptReviewReport(
                summary="原审阅内容正确，但 JSON 有尾逗号。",
                strengths=["核心因果可继续"],
                issues=[],
                requires_revision=False,
            ).model_dump_json()
            return valid[:-1] + ",}"
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class RevisionRegressionClient(StagedLanguageClient):
    def __init__(self, request: PlanningRunCreateRequest) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.targeted_repair_calls = 0

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "performing one bounded correction" in instructions:
            self.targeted_repair_calls += 1
            payload = json.loads(input_text)
            outline = EpisodeOutline.model_validate(payload["current_revised_outline"])
            outline.story_summary += " 已移除新引入的供电状态矛盾。"
            return outline.model_dump_json()
        if "story-revision verifier Agent" in instructions:
            self.revision_verifier_calls += 1
            payload = json.loads(input_text)
            required = [
                action["issue_index"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            if self.revision_verifier_calls == 1:
                return StoryRevisionVerification(
                    accepted=False,
                    summary="Writer 新增了与既有供电状态冲突的描述。",
                    satisfied_issue_indices=[],
                    violations=[
                        StoryRevisionViolation(
                            category="RESOURCE_STATE_REGRESSION",
                            scene_sequences=[1],
                            observation="同一线路既在供电又被写成早已断开。",
                            repair_instruction="统一断线发生时间并保留已成立的供电链。",
                        )
                    ],
                ).model_dump_json()
            return StoryRevisionVerification(
                accepted=True,
                summary="定向修复后供电状态一致。",
                satisfied_issue_indices=required,
                violations=[],
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


def _request(target_seconds: int = 300) -> PlanningRunCreateRequest:
    return PlanningRunCreateRequest(
        name=f"可恢复 {target_seconds} 秒短剧",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=target_seconds,
        run_mode=RunMode.GUIDED,
    )


def _service(
    root: Path,
    language: StagedLanguageClient,
) -> ShortDramaService:
    service = ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
        language_provider_factory=lambda _api_key, _base_url: language,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    return service


def _finish(service: ShortDramaService, run_id: str, *, max_steps: int = 30):
    response = service.get_planning_run(run_id)
    for _ in range(max_steps):
        if response.run.status == PlanningRunStatus.COMPLETE:
            return response
        response = service.advance_planning_run(run_id)
    raise AssertionError("planning run did not finish within the test bound")


def _advance_to_step(
    service: ShortDramaService,
    run_id: str,
    step: PlanningRunStep,
    *,
    max_steps: int = 40,
):
    response = service.get_planning_run(run_id)
    for _ in range(max_steps):
        if response.run.current_step == step:
            return response
        response = service.advance_planning_run(run_id)
    raise AssertionError(f"planning run did not reach {step} within the test bound")


def test_long_form_planning_advances_one_agent_stage_or_scene_at_a_time(
    tmp_path: Path,
) -> None:
    request = _request(430)
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)

    started = service.create_planning_run(request)
    assert started.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert started.project is None

    after_architect = service.advance_planning_run(started.run.id)
    assert after_architect.run.current_step == PlanningRunStep.SCENE_WRITER
    assert after_architect.run.total_scenes == len(language.outline.scenes)
    assert after_architect.run.directed_scene_json == []

    after_writer = _advance_to_step(
        service,
        started.run.id,
        PlanningRunStep.SCRIPT_CRITIC,
    )
    assert after_writer.run.next_script_scene_sequence == len(language.outline.scenes) + 1
    assert language.script_scene_calls == list(
        range(1, len(language.outline.scenes) + 1)
    )
    assert len(language.script_scene_inputs[-1]["recent_full_scenes"]) <= 2

    after_critic = service.advance_planning_run(started.run.id)
    assert after_critic.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert after_critic.run.review_report is not None
    assert after_critic.run.review_disposition is not None
    assert (
        after_critic.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE
    )

    first_scene = service.advance_planning_run(started.run.id)
    assert len(first_scene.run.directed_scene_json) == 1
    assert first_scene.run.next_scene_sequence == 2
    assert language.scene_calls == [1]

    completed = _finish(service, started.run.id)
    assert completed.project is not None
    assert completed.project.document.project.target_duration_seconds == 430
    assert completed.project.document.autonomous_runs == []
    assert len(completed.project.document.script_versions[0].structured_scenes) == len(
        language.outline.scenes
    )
    assert language.scene_calls == list(range(1, len(language.outline.scenes) + 1))
    assert service.repository.list_image_tasks(completed.project.document.project.id) == []
    assert service.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []
    assert [
        snapshot.revision_round
        for snapshot in service.repository.list_planning_outline_snapshots(
            started.run.id
        )
    ] == [0]
    assert [
        snapshot.review_round
        for snapshot in service.repository.list_planning_review_snapshots(
            started.run.id
        )
    ] == [1]
    assert completed.project.document.script_versions[0].planning_run_id == started.run.id
    assert (
        completed.project.document.script_versions[0].review_disposition
        == completed.run.review_disposition
    )
    first_line = completed.project.document.script_versions[0].lines[0]
    revised_project = service.revise_script_line(
        completed.project.document.project.id,
        ReviseScriptLineRequest(
            line_id=first_line.id,
            spoken_text=first_line.spoken_text,
            subtitle_text=first_line.subtitle_text,
            change_reason="确认手动版本仍可追溯规划审稿轨迹",
        ),
    )
    revised_script = revised_project.document.script_versions[-1]
    assert revised_script.planning_run_id == started.run.id
    assert revised_script.review_report == completed.project.document.script_versions[0].review_report
    assert (
        revised_script.review_disposition
        == completed.project.document.script_versions[0].review_disposition
    )

    second_line = revised_script.lines[1]
    third_line = revised_script.lines[2]
    batch_revised = service.revise_script_lines(
        completed.project.document.project.id,
        ReviseScriptLinesRequest(
            revisions=[
                ScriptLineRevision(
                    line_id=second_line.id,
                    spoken_text=second_line.spoken_text + "，你先听我说。",
                    subtitle_text=second_line.subtitle_text + "，你先听我说。",
                ),
                ScriptLineRevision(
                    line_id=third_line.id,
                    spoken_text=third_line.spoken_text + "，别急。",
                    subtitle_text=third_line.subtitle_text + "，别急。",
                ),
            ],
            change_reason="一次提交两句自然口语调整",
        ),
    )
    assert len(batch_revised.document.script_versions) == 3
    latest_script = batch_revised.document.script_versions[-1]
    assert latest_script.parent_version_id == revised_script.id
    assert latest_script.lines[1].spoken_text.endswith("你先听我说。")
    assert latest_script.lines[2].spoken_text.endswith("别急。")


def test_batch_dialogue_revision_rebuilds_active_h3_route_before_generation(
    tmp_path: Path,
) -> None:
    request = _request(180)
    service = _service(tmp_path, StagedLanguageClient(request))
    project = service.initialize_fixture()
    document = project.document
    document.autonomous_runs.append(
        AutonomousRunRecord(
            id="active-dialogue-revision-run",
            project_id=document.project.id,
            status=AutonomousRunStatus.RUNNING,
            current_step=AutonomousRunStep.GENERATE,
            completed_steps=[
                AutonomousRunStep.NORMALIZE_INPUT,
                AutonomousRunStep.PLAN,
                AutonomousRunStep.PREPARE_ASSETS,
                AutonomousRunStep.PLAN_ROUTES,
            ],
            planner="test",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            route_plan_id="old-dialogue-route-plan",
            render_export=True,
            created_at="2026-08-27T00:00:00Z",
            updated_at="2026-08-27T00:00:00Z",
        )
    )
    service.repository.save_project(document)
    line = document.script_versions[-1].lines[0]

    revised = service.revise_script_lines(
        document.project.id,
        ReviseScriptLinesRequest(
            revisions=[
                ScriptLineRevision(
                    line_id=line.id,
                    spoken_text="你先听我把话说完。",
                    subtitle_text="你先听我把话说完。",
                )
            ],
            change_reason="视频生成前调整真人口语",
        ),
    )

    run = revised.document.autonomous_runs[-1]
    assert run.status == AutonomousRunStatus.RUNNING
    assert run.current_step == AutonomousRunStep.PLAN_ROUTES
    assert run.route_plan_id is None
    assert AutonomousRunStep.PLAN_ROUTES not in run.completed_steps
    assert service.repository.list_tasks(document.project.id) == []


def test_planning_run_persists_revision_and_scene_progress_across_restart(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, revision_rounds_required=1)
    first = _service(tmp_path, language)
    run = first.create_planning_run(request).run

    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.SCENE_WRITER
    first_script_scene = first.advance_planning_run(run.id)
    assert len(first_script_scene.run.scene_script_json) == 1
    restarted_writer = _service(tmp_path, language)
    restored_writer = restarted_writer.get_planning_run(run.id)
    assert len(restored_writer.run.scene_script_json) == 1
    _advance_to_step(restarted_writer, run.id, PlanningRunStep.SCRIPT_CRITIC)
    first = restarted_writer
    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.STORY_REVISION
    revised = first.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert revised.run.revision_round == 1
    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.SCENE_DIRECTOR
    first_scene = first.advance_planning_run(run.id)
    assert len(first_scene.run.directed_scene_json) == 1

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(run.id)
    assert restored.run.revision_round == 1
    assert len(restored.run.directed_scene_json) == 1
    assert restored.run.review_disposition is not None

    completed = _finish(restarted, run.id)
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.project_id is not None
    assert language.revision_calls == 0
    assert language.local_revision_calls == [1, len(language.outline.scenes)]
    assert language.critic_calls == 2
    assert language.scene_inputs[1]["previous_actual_handoff"]["known_facts"][-1] == (
        "staged-scene-1-complete"
    )
    history = restarted.get_planning_run_script_quality(run.id)
    assert [item.revision_round for item in history.outline_snapshots] == [0, 1]
    assert [item.review_round for item in history.review_snapshots] == [1, 2]
    assert all(item.disposition is not None for item in history.review_snapshots)
    assert [item.total_issues for item in history.comparison.rounds] == [1, 0]
    assert history.comparison.trend.value == "FEWER_REPORTED_ISSUES"
    assert history.comparison.total_issue_delta == -1
    assert history.comparison.high_issue_delta == -1
    assert history.revision_judge is None
    assert language.judge_calls == 0


def test_story_revision_refreshes_persisted_scene_count(tmp_path: Path) -> None:
    request = _request()
    language = SceneCountRevisionClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run

    architect = service.advance_planning_run(run.id)
    original_count = architect.run.total_scenes
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    assert service.advance_planning_run(run.id).run.current_step == PlanningRunStep.STORY_REVISION
    revised = service.advance_planning_run(run.id)

    assert revised.run.total_scenes == original_count - 1
    assert revised.run.outline_json is not None
    assert len(EpisodeOutline.model_validate_json(revised.run.outline_json).scenes) == revised.run.total_scenes


def test_user_can_replace_outline_and_only_downstream_planning_is_invalidated(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    architect = service.advance_planning_run(run.id)
    assert architect.run.outline_json is not None
    edited = EpisodeOutline.model_validate_json(architect.run.outline_json).model_copy(
        deep=True,
        update={"title": "用户直接编辑后的标题"},
    )

    response = service.replace_planning_outline(
        run.id,
        PlanningOutlineDirectEditRequest(
            outline_json=edited.model_dump_json(),
            change_reason="调整标题并保留其余剧本",
        ),
    )

    assert response.run.revision_round == 1
    assert response.run.current_step == PlanningRunStep.SCENE_WRITER
    assert response.run.completed_steps == [PlanningRunStep.STORY_ARCHITECT]
    assert response.run.directed_scene_json == []
    assert response.run.review_report is None
    assert response.run.latest_repair_plan is None
    assert response.run.source_contract is not None
    assert any(
        "调整标题并保留其余剧本" in item
        for item in response.run.source_contract.authoritative_user_overrides
    )
    assert EpisodeOutline.model_validate_json(response.run.outline_json or "").title == (
        "用户直接编辑后的标题"
    )
    assert service.repository.list_planning_outline_snapshots(run.id) == []
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert [item.revision_round for item in snapshots] == [1]
    assert snapshots[-1].source == "User direct outline edit"
    assert snapshots[-1].source_contract is not None
    assert snapshots[-1].source_contract.authoritative_user_overrides


def test_agent_directed_outline_edit_clears_stale_scene_work_and_returns_to_review(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = UserDirectedRevisionClient(request)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCENE_DIRECTOR)
    with_scene = service.advance_planning_run(run.id)
    assert with_scene.run.directed_scene_json

    response = service.revise_planning_outline_with_agent(
        run.id,
        PlanningOutlineAgentRevisionRequest(
            instruction="精简因果，但保留题材、人物和总时长。",
        ),
    )

    assert response.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert response.run.revision_round == 1
    assert response.run.directed_scene_json == []
    assert response.run.next_scene_sequence == 1
    assert response.run.review_report is None
    assert response.run.review_disposition is None
    assert response.run.source_contract is not None
    assert any(
        "精简因果，但保留题材、人物和总时长" in item
        for item in response.run.source_contract.authoritative_user_overrides
    )
    assert EpisodeOutline.model_validate_json(response.run.outline_json or "").title == (
        "用户定向修改后的标题"
    )


def test_scene_revision_tool_patches_only_user_selected_scene(tmp_path: Path) -> None:
    request = _request(180)
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    original = EpisodeOutline.model_validate_json(ready.run.outline_json or "")
    first_before = scene_dialogue_lines(original, original.scenes[0])
    second_before = scene_dialogue_lines(original, original.scenes[1])

    response = service.revise_planning_scenes_with_agent(
        run.id,
        PlanningSceneRevisionRequest(
            instruction="只把第二场的第一句改得更直接。",
            scene_sequences=[2],
        ),
    )

    revised = EpisodeOutline.model_validate_json(response.run.outline_json or "")
    assert response.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert response.run.revision_round == 1
    assert scene_dialogue_lines(revised, revised.scenes[0]) == first_before
    second_after = scene_dialogue_lines(revised, revised.scenes[1])
    assert second_after[0].text == second_before[0].text + "（局部改稿）"
    assert all(
        after == before
        for after, before in zip(second_after[1:], second_before[1:], strict=True)
    )
    assert any("局部修改范围：[2]" in warning for warning in response.run.warnings)
    assert service.repository.list_planning_script_documents(run.id, 0)
    assert service.repository.list_planning_script_documents(run.id, 1)


def test_repair_planner_can_reject_unsupported_critic_findings_without_rewrite(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = CriticOverreachRepairClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    routed = service.advance_planning_run(run.id)
    assert routed.run.current_step == PlanningRunStep.STORY_REVISION

    diagnosed = service.advance_planning_run(run.id)

    assert diagnosed.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert language.repair_plan_calls == 1
    assert language.revision_calls == 0
    assert diagnosed.run.latest_repair_plan is not None
    assert diagnosed.run.latest_repair_plan.actions[0].diagnosis == (
        StoryRepairDiagnosis.CRITIC_OVERREACH
    )
    assert any("均不应触发剧本重写" in item for item in diagnosed.run.warnings)


def test_repair_planner_receives_prior_round_context_and_writer_receives_plan(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = StagedLanguageClient(request, revision_rounds_required=2)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    for _ in range(20):
        response = service.advance_planning_run(run.id)
        if (
            response.run.revision_round == 2
            and response.run.current_step == PlanningRunStep.SCRIPT_CRITIC
        ):
            break

    assert response.run.revision_round == 2
    assert language.repair_plan_calls == 2
    assert language.repair_plan_inputs[0]["prior_revision_history"] == []
    prior = language.repair_plan_inputs[1]["prior_revision_history"]
    assert isinstance(prior, list)
    assert len(prior) == 1
    assert prior[0]["revision_round"] == 0
    local_instruction = json.loads(
        language.local_revision_inputs[-1]["user_instruction"]
    )
    assert local_instruction["invariants"] == [
        "保留已经成立的悬念推进"
    ]
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert snapshots[-1].repair_plan is not None


def test_source_contract_is_compiled_once_and_reaches_repair_writer_and_verifier(
    tmp_path: Path,
) -> None:
    source = "乔曼带着多名被骗买家闯入避暑山庄，并争抢最后的冷气。"
    request = PlanningRunCreateRequest(
        name="来源承诺传播测试",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text=source,
        target_duration_seconds=180,
        run_mode=RunMode.GUIDED,
    )
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)

    revised = service.advance_planning_run(run.id)

    assert revised.run.source_contract is not None
    assert revised.run.source_contract.explicit_commitments == [source]
    repair_contract = language.repair_plan_inputs[0][
        "explicit_story_source_contract"
    ]
    local_contract = json.loads(
        language.local_revision_inputs[0]["user_instruction"]
    )["explicit_story_source_contract"]
    verifier_contract = language.revision_verifier_inputs[0][
        "explicit_story_source_contract"
    ]
    assert repair_contract["explicit_commitments"] == [source]
    assert local_contract["explicit_commitments"] == [source]
    assert verifier_contract["explicit_commitments"] == [source]
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert snapshots[-1].source_contract is not None
    assert snapshots[-1].source_contract.explicit_commitments == [source]


def test_critic_json_structure_is_repaired_without_dropping_the_review(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = MalformedCriticJsonClient(request)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)

    reviewed = service.advance_planning_run(run.id)

    assert reviewed.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert reviewed.run.review_report is not None
    assert reviewed.run.review_report.summary == "修复后的审阅仍保留原结论。"
    assert any("已保留原审阅内容并修复 JSON" in item for item in reviewed.run.warnings)


def test_revision_verifier_triggers_one_targeted_writer_repair_before_acceptance(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = RevisionRegressionClient(request)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)

    revised = service.advance_planning_run(run.id)

    assert revised.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert language.revision_verifier_calls == 2
    assert language.targeted_repair_calls == 0
    assert language.local_revision_calls.count(1) == 2
    assert revised.run.latest_revision_verification is not None
    assert revised.run.latest_revision_verification.accepted is True
    assert any("定向修复 1 次" in item for item in revised.run.warnings)
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert snapshots[-1].revision_verification is not None
    assert snapshots[-1].revision_verification.accepted is True


def test_supervising_agent_can_revise_the_latest_routed_review_as_a_tool(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    reviewed = service.advance_planning_run(run.id)
    assert reviewed.run.current_step == PlanningRunStep.STORY_REVISION

    revised = service.revise_planning_outline_from_review(run.id)

    assert revised.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert revised.run.revision_round == 1
    assert revised.run.latest_repair_plan is not None
    assert revised.run.latest_revision_verification is not None
    assert revised.run.latest_revision_verification.accepted is True
    assert revised.run.review_report is None
    assert revised.run.review_disposition is None


def test_transient_scene_failure_keeps_the_same_local_retry_point(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, fail_scene_once=2)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCENE_DIRECTOR)
    service.advance_planning_run(run.id)

    failed = service.advance_planning_run(run.id)
    assert failed.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert failed.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert failed.run.next_scene_sequence == 2
    assert len(failed.run.directed_scene_json) == 1

    retried = service.advance_planning_run(run.id)
    assert retried.run.status == PlanningRunStatus.RUNNING
    assert retried.run.next_scene_sequence == 3
    assert len(retried.run.directed_scene_json) == 2
    assert language.scene_calls.count(2) == 2


def test_latest_review_regression_is_advisory_and_does_not_block(
    tmp_path: Path,
) -> None:
    request = _request(260)
    language = StagedLanguageClient(
        request,
        revision_rounds_required=3,
        review_issue_counts=[4, 2, 1, 3],
    )
    service = _service(tmp_path, language)
    run_id = service.create_planning_run(request).run.id
    response = _advance_to_step(
        service,
        run_id,
        PlanningRunStep.REVISION_JUDGE,
    )
    assert response.run.current_step == PlanningRunStep.REVISION_JUDGE
    assert response.run.directed_scene_json == []

    judged = service.advance_planning_run(run_id)
    assert judged.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert judged.run.revision_judge is not None

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(run_id)
    assert restored.run.revision_judge == judged.run.revision_judge
    completed = _finish(restarted, run_id)

    history = restarted.get_planning_run_script_quality(completed.run.id)
    assert [item.total_issues for item in history.comparison.rounds] == [4, 2, 1, 3]
    assert history.comparison.total_issue_delta == -1
    assert history.comparison.latest_total_issue_delta == 2
    assert history.comparison.latest_high_issue_delta == 0
    assert history.comparison.latest_report_regression_risk is True
    assert history.revision_judge is not None
    assert history.revision_judge.recommended_revision_round == 2
    assert history.revision_judge.alternative_revision_round == 3
    assert history.revision_judge.advisory_only is True
    assert language.judge_calls == 1
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert PlanningRunStep.REVISION_JUDGE in completed.run.completed_steps
    assert completed.run.outline_json is not None
    assert completed.run.revision_round == 3
    assert "（局部改稿）" in completed.run.outline_json
    assert any("第 3 轮已仅对场次" in item for item in completed.run.warnings)
    assert any("建议优先比较并考虑" in item for item in completed.run.warnings)
    assert any("未自动替换大纲" in item for item in completed.run.warnings)
    assert completed.project is not None
    assert restarted.repository.list_image_tasks(
        completed.project.document.project.id
    ) == []
    assert restarted.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []


def test_revision_judge_provider_failure_does_not_block_latest_outline(
    tmp_path: Path,
) -> None:
    request = _request(260)
    language = StagedLanguageClient(
        request,
        revision_rounds_required=3,
        review_issue_counts=[4, 2, 1, 3],
        fail_judge=True,
    )
    service = _service(tmp_path, language)
    completed = _finish(
        service,
        service.create_planning_run(request).run.id,
    )

    history = service.get_planning_run_script_quality(completed.run.id)
    assert history.comparison.latest_report_regression_risk is True
    assert history.revision_judge is None
    assert language.judge_calls == 1
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.outline_json is not None
    assert completed.run.revision_round == 3
    assert "（局部改稿）" in completed.run.outline_json
    assert any("Revision Judge 不可用" in item for item in completed.run.warnings)


def test_planning_run_api_can_finish_with_local_script_only_fallback(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        created = client.post(
            "/api/short-drama/planning-runs",
            json=_request(300).model_dump(mode="json"),
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run"]["id"]
        for _ in range(30):
            advanced = client.post(
                f"/api/short-drama/planning-runs/{run_id}/advance"
            )
            assert advanced.status_code == 200, advanced.text
            if advanced.json()["run"]["status"] == "COMPLETE":
                break
        else:
            raise AssertionError("local planning API did not complete")

        listed = client.get("/api/short-drama/planning-runs")
        restored = client.get(f"/api/short-drama/planning-runs/{run_id}")
        quality = client.get(
            f"/api/short-drama/planning-runs/{run_id}/script-quality"
        )
        search = client.post(
            f"/api/short-drama/planning-runs/{run_id}/agent-tools/search-script",
            json={"query": "因果链", "limit": 4, "include_story_bible": False},
        )

    assert listed.status_code == 200
    assert listed.json()["runs"][0]["id"] == run_id
    assert restored.status_code == 200
    assert restored.json()["project"]["document"]["project"][
        "target_duration_seconds"
    ] == 300
    assert any(
        "未配置" in warning
        for warning in restored.json()["run"]["warnings"]
    )
    assert quality.status_code == 200
    assert quality.json()["comparison"]["rounds"] == []
    assert quality.json()["comparison"]["trend"] == "NOT_COMPARABLE"
    assert search.status_code == 200
    assert search.json()["run_id"] == run_id
    assert search.json()["hits"]


def test_auto_mode_planning_stops_before_image_or_h3_generation(
    tmp_path: Path,
) -> None:
    request = PlanningRunCreateRequest(
        name="先完成剧本再进入制片",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
        run_mode=RunMode.AUTO_DRAFT,
        execution_target=AutonomousExecutionTarget.REAL_H3,
    )
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)

    completed = _finish(
        service,
        service.create_planning_run(request).run.id,
    )

    assert completed.project is not None
    autonomous = completed.project.document.autonomous_runs[-1]
    assert autonomous.current_step == AutonomousRunStep.PREPARE_ASSETS
    assert completed.run.autonomous_run_id == autonomous.id
    assert service.repository.list_image_tasks(
        completed.project.document.project.id
    ) == []
    assert service.repository.list_tasks(completed.project.document.project.id) == []
    assert service.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []


def test_script_quality_api_compares_multiple_custom_duration_runs(
    tmp_path: Path,
) -> None:
    requests = [
        PlanningRunCreateRequest(
            name="180 秒都市误会",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="婚礼当天，女主发现新郎只是替别人出席。",
            target_duration_seconds=180,
            run_mode=RunMode.GUIDED,
        ),
        PlanningRunCreateRequest(
            name="430 秒职场悬疑",
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=430,
            run_mode=RunMode.GUIDED,
        ),
        PlanningRunCreateRequest(
            name="900 秒家庭反转",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="父亲的遗嘱要求三个子女在旧屋共住一夜才能继承遗产。",
            target_duration_seconds=900,
            run_mode=RunMode.GUIDED,
        ),
    ]
    run_ids: list[str] = []
    for request in requests:
        language = StagedLanguageClient(request, revision_rounds_required=1)
        service = _service(tmp_path, language)
        run = service.create_planning_run(request).run
        completed = _finish(service, run.id)
        assert completed.project is not None
        run_ids.append(run.id)

    service = _service(tmp_path, StagedLanguageClient(requests[-1]))
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        catalog = client.get("/api/short-drama/script-quality")
        detail = client.get(
            f"/api/short-drama/planning-runs/{run_ids[1]}/script-quality"
        )

    assert catalog.status_code == 200, catalog.text
    evaluations = catalog.json()["evaluations"]
    assert {item["target_duration_seconds"] for item in evaluations} == {
        180.0,
        430.0,
        900.0,
    }
    assert all(item["trend"] == "FEWER_REPORTED_ISSUES" for item in evaluations)
    assert detail.status_code == 200, detail.text
    assert detail.json()["comparison"]["target_duration_seconds"] == 430.0
    assert [
        item["revision_round"] for item in detail.json()["outline_snapshots"]
    ] == [0, 1]
    assert [
        item["review_round"] for item in detail.json()["review_snapshots"]
    ] == [1, 2]
