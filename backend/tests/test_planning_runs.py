"""Recoverable long-form script planning integration tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from short_drama.models import (
    CreateProjectRequest,
    CreativeInputImage,
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
    SceneContinuitySummary,
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
    PlannedCharacter,
    PlannedDialogueLine,
    PlannedLocation,
    PlannedSceneContract,
    PlannedShot,
    PlannedStyle,
    SceneShotPlan,
)
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
)
from short_drama.service import ShortDramaService
from short_drama.script_documents import (
    EpisodeScenePatch,
    ScriptRevisionContextPlan,
    ScriptRevisionScope,
    scene_dialogue_lines,
)


def _staged_episode_outline(request: CreateProjectRequest) -> EpisodeOutline:
    """Build explicit creative test data without borrowing production fallbacks."""

    target_duration = request.resolved_target_duration_seconds()
    scene_duration = target_duration / 4
    dialogue = [
        ("林夏", "录音里的时间，比停电早了十分钟。"),
        ("周衡", "那就说明有人提前进过控制室。"),
        ("林夏", "门禁记录只留下了你的名字。"),
        ("周衡", "别看名字，看最后一段波形。"),
    ]
    lines = [
        PlannedDialogueLine(speaker=speaker, text=text)
        for speaker, text in dialogue
    ]
    scenes: list[PlannedSceneContract] = []
    for sequence in range(1, 5):
        duration = (
            scene_duration
            if sequence < 4
            else target_duration - scene_duration * 3
        )
        scenes.append(
            PlannedSceneContract(
                sequence=sequence,
                heading=f"测试控制室·场次 {sequence}",
                summary=f"林夏与周衡核对第 {sequence} 段证据。",
                narrative_purpose=f"推进第 {sequence} 段因果链。",
                playable_actions=[f"核对第 {sequence} 段记录"],
                target_duration_seconds=duration,
                dialogue_line_indices=[sequence],
                location_names=["测试控制室"],
                continuity_in=SceneContinuitySummary(
                    setting="测试控制室",
                    character_states=[f"两人准备核对第 {sequence} 段证据"],
                    prop_states=[f"第 {sequence} 段记录待查"],
                    known_facts=[f"前 {sequence - 1} 段证据已核对"],
                    open_threads=[f"第 {sequence} 段证据是否成立"],
                ),
                continuity_out=SceneContinuitySummary(
                    setting="测试控制室",
                    character_states=[f"两人已完成第 {sequence} 段核对"],
                    prop_states=[f"第 {sequence} 段记录已标记"],
                    known_facts=[f"第 {sequence} 段证据成立"],
                    open_threads=(
                        [f"继续核对第 {sequence + 1} 段证据"]
                        if sequence < 4
                        else []
                    ),
                ),
            )
        )
    return EpisodeOutline(
        title=request.name or "可恢复规划测试短剧",
        story_summary="两人通过四段记录拼出停电事件的真相。",
        target_duration_seconds=target_duration,
        lines=lines,
        characters=[
            PlannedCharacter(
                name="林夏",
                age=28,
                appearance="短发，表情专注",
                wardrobe="浅色外套",
                performance="先质疑，再逐步确认证据",
                voice_identity="S1",
            ),
            PlannedCharacter(
                name="周衡",
                age=32,
                appearance="短发，神情冷静",
                wardrobe="深色外套",
                performance="克制地提供关键信息",
                voice_identity="S2",
            ),
        ],
        locations=[
            PlannedLocation(
                name="测试控制室",
                layout="两人隔桌查看同一台终端",
                lighting="柔和顶光",
                palette="蓝灰与暖白",
                key_views=["终端与两人关系的建立画面"],
            )
        ],
        props=[],
        style=PlannedStyle(
            medium="二维图形化悬疑短剧",
            genre="悬疑",
            cinematography="竖屏构图，用画面层次突出证据关系",
            color="蓝灰主色，关键证据使用暖色提示",
            texture="清晰的平面图形与轻微纸张纹理",
            forbidden=["无动机的风格漂移"],
        ),
        scenes=scenes,
    )


def _staged_scene_plan(scene: PlannedSceneContract) -> SceneShotPlan:
    continuity_out = scene.continuity_out.model_copy(deep=True)
    continuity_out.known_facts.append(f"staged-scene-{scene.sequence}-complete")
    return SceneShotPlan(
        scene_sequence=scene.sequence,
        shots=[
            PlannedShot(
                scene_sequence=scene.sequence,
                duration_seconds=scene.target_duration_seconds,
                narrative_purpose=f"staged-scene-{scene.sequence}: {scene.narrative_purpose}",
                visual=f"林夏与周衡在终端上核对第 {scene.sequence} 段记录。",
                framing="能同时读取人物反应与证据的双人构图",
                camera="保持稳定，仅在证据揭示时轻微重构图",
                audio_intent="保留本场对白与必要的室内环境声",
                dialogue_line_indices=list(scene.dialogue_line_indices),
                character_positions="林夏与周衡分别位于终端两侧",
                eyeline="两人在终端与对方之间切换视线",
                prop_state_in=f"第 {scene.sequence} 段记录待查",
                prop_state_out=f"第 {scene.sequence} 段记录已标记",
                action_in="两人开始核对当前记录",
                action_out="两人确认当前证据并准备继续",
                transition_out="转入下一段记录",
                merge_candidate=False,
                local_rework_risk="低",
            )
        ],
        continuity_out=continuity_out,
    )


class StagedLanguageClient:
    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        revision_rounds_required: int = 0,
        fail_scene_once: int | None = None,
        review_issue_counts: list[int] | None = None,
        fail_judge: bool = False,
    ) -> None:
        self.request = request
        self.outline = _staged_episode_outline(request)
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
        self.scene_plans: dict[int, SceneShotPlan] = {}
        for scene in self.outline.scenes:
            plan = _staged_scene_plan(scene)
            self.scene_plans[scene.sequence] = plan

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
    def __init__(self, request: CreateProjectRequest) -> None:
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
    def __init__(self, request: CreateProjectRequest) -> None:
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


def _request(target_seconds: int = 300) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=f"可恢复 {target_seconds} 秒短剧",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=target_seconds,
        run_mode=RunMode.AUTO_DRAFT,
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


def _start_owned_planning(
    service: ShortDramaService,
    request: CreateProjectRequest,
):
    project = service.create_project(
        request,
        project_id=f"project-test-{uuid4().hex[:12]}",
        owner_agent_run_id=f"agent-run-test-{uuid4().hex[:12]}",
    )
    assert project.document.planning is not None
    assert project.document.planning.run_id is not None
    return service.get_planning_run(project.document.planning.run_id)


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

    started = _start_owned_planning(service, request)
    assert started.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert started.project is not None
    assert started.project.document.project.stage.value == "INPUT"

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
    assert "autonomous_runs" not in completed.project.document.model_dump()
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


def test_agent_owned_long_project_starts_as_scaffold_and_finalizes_without_legacy_run(
    tmp_path: Path,
) -> None:
    planning_request = CreateProjectRequest(
        name="Agent 可恢复长规划",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    language = StagedLanguageClient(planning_request)
    service = _service(tmp_path, language)
    project_id = "project-agent-owned-planning"
    owner_agent_run_id = "agent-run-owner"

    scaffold = service.create_project(
        planning_request,
        project_id=project_id,
        owner_agent_run_id=owner_agent_run_id,
    )

    assert scaffold.document.project.stage.value == "INPUT"
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.status == PlanningRunStatus.RUNNING
    assert scaffold.document.planning.owner_agent_run_id == owner_agent_run_id
    assert scaffold.document.planning.run_id is not None
    run = service.get_planning_run(scaffold.document.planning.run_id).run
    assert run.project_id == project_id
    assert run.owner_agent_run_id == owner_agent_run_id
    assert run.request == planning_request

    advanced = service.advance_planning_run(run.id)
    running_project = service.get_project(project_id).document
    assert advanced.run.status == PlanningRunStatus.RUNNING
    assert running_project.planning is not None
    assert running_project.planning.status == PlanningRunStatus.RUNNING
    assert running_project.planning.planner is not None
    assert running_project.planning.planner.startswith("multi-agent:")
    assert running_project.planning.warnings == advanced.run.warnings

    completed = _finish(service, run.id)

    assert completed.project is not None
    assert completed.project.document.project.stage.value == "PLANNED"
    assert completed.project.document.planning is not None
    assert completed.project.document.planning.status == PlanningRunStatus.COMPLETE
    assert completed.project.document.planning.run_id == run.id
    assert completed.project.document.planning.owner_agent_run_id == owner_agent_run_id
    assert "autonomous_runs" not in completed.project.document.model_dump()


def test_agent_owned_planning_failure_is_visible_on_the_project_scaffold(
    tmp_path: Path,
) -> None:
    class FailingStoryArchitectClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            if "story-architect Agent" in instructions:
                raise RuntimeError("story architect unavailable")
            return super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )

    planning_request = CreateProjectRequest(
        name="Agent 规划异常可见",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, FailingStoryArchitectClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-agent-planning-error",
        owner_agent_run_id="agent-run-error-owner",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None

    response = service.advance_planning_run(scaffold.document.planning.run_id)

    assert response.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert response.run.error_message == "story architect unavailable"
    assert response.project is not None
    assert response.project.document.planning is not None
    assert response.project.document.planning.status == PlanningRunStatus.NEEDS_ATTENTION
    assert response.project.document.planning.warnings == response.run.warnings


def test_owned_planning_run_and_project_metadata_update_roll_back_together(
    tmp_path: Path,
) -> None:
    planning_request = CreateProjectRequest(
        name="Agent 规划原子保存",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-atomic-planning-state",
        owner_agent_run_id="agent-run-atomic-owner",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None
    run_id = scaffold.document.planning.run_id
    run = service.repository.get_planning_run(run_id)
    document = service.repository.get_project(scaffold.document.project.id)
    assert run is not None
    assert document is not None and document.planning is not None

    run.status = PlanningRunStatus.NEEDS_ATTENTION
    run.error_message = "forced atomic save failure"
    document.planning = document.planning.model_copy(
        update={"status": PlanningRunStatus.NEEDS_ATTENTION}
    )
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_owned_project_status_update
            BEFORE UPDATE ON projects
            BEGIN
                SELECT RAISE(ABORT, 'forced project update failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced project update failure"):
        service.repository.save_planning_run_with_project(run, document)

    persisted_run = service.repository.get_planning_run(run_id)
    persisted_project = service.repository.get_project(scaffold.document.project.id)
    assert persisted_run is not None
    assert persisted_project is not None and persisted_project.planning is not None
    assert persisted_run.status == PlanningRunStatus.RUNNING
    assert persisted_run.error_message is None
    assert persisted_project.planning.status == PlanningRunStatus.RUNNING


def test_explicit_advance_resumes_a_failed_owned_planning_run(
    tmp_path: Path,
) -> None:
    planning_request = CreateProjectRequest(
        name="Agent 规划显式恢复",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-resume-failed-planning",
        owner_agent_run_id="agent-run-resume-owner",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None
    run = service.repository.get_planning_run(scaffold.document.planning.run_id)
    document = service.repository.get_project(scaffold.document.project.id)
    assert run is not None
    assert document is not None and document.planning is not None
    run.status = PlanningRunStatus.FAILED
    run.error_message = "provider was unavailable"
    document.planning = document.planning.model_copy(
        update={"status": PlanningRunStatus.FAILED}
    )
    service.repository.save_planning_run_with_project(run, document)

    resumed = service.advance_planning_run(run.id)

    assert resumed.run.status == PlanningRunStatus.RUNNING
    assert resumed.run.current_step == PlanningRunStep.SCENE_WRITER
    assert resumed.run.error_message is None
    assert resumed.project is not None
    assert resumed.project.document.planning is not None
    assert resumed.project.document.planning.status == PlanningRunStatus.RUNNING


def test_owner_finalize_retry_reuses_already_imported_input_image(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "owner-finalize-seed.png"
    Image.new("RGB", (36, 52), "#334455").save(image_path)
    planning_request = CreateProjectRequest(
        name="Agent FINALIZE 重入",
        source_mode=SourceMode.IMAGE_SEEDED,
        target_duration_seconds=120,
        input_images=[
            CreativeInputImage(
                local_path=str(image_path),
                descriptor="雨夜车站的冷蓝视觉种子",
            )
        ],
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-owner-finalize-retry",
        owner_agent_run_id="agent-run-finalize-owner",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None
    run_id = scaffold.document.planning.run_id
    _advance_to_step(service, run_id, PlanningRunStep.FINALIZE)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_owner_finalize_cursor_update
            BEFORE UPDATE ON planning_runs
            BEGIN
                SELECT RAISE(ABORT, 'forced planning update failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced planning update failure"):
        service.advance_planning_run(run_id)

    interrupted = service.get_project(scaffold.document.project.id).document
    seed_assets = [
        asset
        for asset in interrupted.assets
        if asset.name.startswith("输入视觉种子 1 ·")
    ]
    assert interrupted.project.stage.value == "PLANNED"
    assert interrupted.planning is not None
    assert interrupted.planning.status == PlanningRunStatus.RUNNING
    assert len(seed_assets) == 1
    imported_asset_id = seed_assets[0].id
    persisted_run = service.get_planning_run(run_id).run
    assert persisted_run.current_step == PlanningRunStep.FINALIZE
    assert persisted_run.status == PlanningRunStatus.RUNNING

    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("DROP TRIGGER reject_owner_finalize_cursor_update")

    completed = service.advance_planning_run(run_id)

    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.project is not None
    assert completed.project.document.planning is not None
    assert completed.project.document.planning.status == PlanningRunStatus.COMPLETE
    retried_seed_assets = [
        asset
        for asset in completed.project.document.assets
        if asset.name.startswith("输入视觉种子 1 ·")
    ]
    assert [asset.id for asset in retried_seed_assets] == [imported_asset_id]


def test_planning_run_persists_revision_and_scene_progress_across_restart(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, revision_rounds_required=1)
    first = _service(tmp_path, language)
    run = _start_owned_planning(first, request).run

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
    outlines = restarted.repository.list_planning_outline_snapshots(run.id)
    reviews = restarted.repository.list_planning_review_snapshots(run.id)
    assert [item.revision_round for item in outlines] == [0, 1]
    assert [item.review_round for item in reviews] == [1, 2]
    assert all(item.disposition is not None for item in reviews)
    assert [len(item.report.issues) for item in reviews] == [1, 0]
    assert completed.run.revision_judge is None
    assert language.judge_calls == 0


def test_story_revision_refreshes_persisted_scene_count(tmp_path: Path) -> None:
    request = _request()
    language = SceneCountRevisionClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run

    architect = service.advance_planning_run(run.id)
    original_count = architect.run.total_scenes
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_CRITIC)
    assert service.advance_planning_run(run.id).run.current_step == PlanningRunStep.STORY_REVISION
    revised = service.advance_planning_run(run.id)

    assert revised.run.total_scenes == original_count - 1
    assert revised.run.outline_json is not None
    assert len(EpisodeOutline.model_validate_json(revised.run.outline_json).scenes) == revised.run.total_scenes


def test_repair_planner_can_reject_unsupported_critic_findings_without_rewrite(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = CriticOverreachRepairClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
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
    run = _start_owned_planning(service, request).run
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
    request = CreateProjectRequest(
        name="来源承诺传播测试",
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text=source,
        target_duration_seconds=180,
        run_mode=RunMode.AUTO_DRAFT,
    )
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
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
    run = _start_owned_planning(service, request).run
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
    run = _start_owned_planning(service, request).run
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


def test_transient_scene_failure_keeps_the_same_local_retry_point(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, fail_scene_once=2)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
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
    run_id = _start_owned_planning(service, request).run.id
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

    reviews = restarted.repository.list_planning_review_snapshots(completed.run.id)
    assert [len(item.report.issues) for item in reviews] == [4, 2, 1, 3]
    assert completed.run.revision_judge is not None
    assert completed.run.revision_judge.recommended_revision_round == 2
    assert completed.run.revision_judge.alternative_revision_round == 3
    assert completed.run.revision_judge.advisory_only is True
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
        _start_owned_planning(service, request).run.id,
    )

    reviews = service.repository.list_planning_review_snapshots(completed.run.id)
    assert [len(item.report.issues) for item in reviews] == [4, 2, 1, 3]
    assert completed.run.revision_judge is None
    assert language.judge_calls == 1
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.outline_json is not None
    assert completed.run.revision_round == 3
    assert "（局部改稿）" in completed.run.outline_json
    assert any("Revision Judge 不可用" in item for item in completed.run.warnings)
