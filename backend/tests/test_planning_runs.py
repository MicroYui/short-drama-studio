"""Recoverable long-form script planning integration tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from short_drama.models import (
    ApprovalStatus,
    ContentPlanningOutput,
    ContinuationPreference,
    CreateProjectRequest,
    CreativeInputImage,
    DurationAlignment,
    DurationAuthority,
    DurationFitAssessment,
    DurationMismatchResolution,
    DurationRange,
    DurationTargetOrigin,
    EndingContract,
    PlanningRunRecord,
    PlanningRunStatus,
    PlanningRunStep,
    ProjectStage,
    ResolvedEndingMode,
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
    StoryIntent,
    StoryCharacterMetadataPatch,
    StoryMetadataRevisionPatch,
    StoryReviewFollowup,
    StorySourceContract,
    StoryRevisionVerification,
    StoryRevisionViolation,
    StoryVerificationResolutionDecision,
    StoryVerificationSource,
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
    StoryOrientation,
    ProductionMethodSelection,
    _fallback_duration_contract,
)
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderError,
)
from short_drama.production_profiles import production_method_skill_catalog
from short_drama.service import ShortDramaService
from short_drama.script_documents import (
    EpisodeScenePatch,
    ScriptRevisionContextPlan,
    ScriptRevisionScope,
    scene_dialogue_lines,
)
from short_drama.hierarchical_script import compose_scene_screenplay


def _aligned_duration_fit_assessment(
    request: CreateProjectRequest,
) -> DurationFitAssessment:
    target = request.resolved_target_duration_seconds()
    return DurationFitAssessment(
        estimated_range_seconds=DurationRange(
            minimum_seconds=target,
            maximum_seconds=target,
        ),
        alignment=DurationAlignment.ALIGNED,
        pressure_points=[],
        concurrent_performance=[],
        resolution=_fallback_duration_contract(request).mismatch_resolution,
        rationale="测试 Verifier 已语义确认当前剧本尺度与时长意图对齐。",
    )


def _outside_duration_fit_assessment(
    request: CreateProjectRequest,
) -> DurationFitAssessment:
    target = request.resolved_target_duration_seconds()
    return DurationFitAssessment(
        estimated_range_seconds=DurationRange(
            minimum_seconds=target + 20,
            maximum_seconds=target + 40,
        ),
        alignment=DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
        pressure_points=["中段两次规则解释挤压了动作节奏"],
        concurrent_performance=["群演反应可与主角动作并行"],
        resolution=DurationMismatchResolution.REVISE_SCRIPT,
        rationale="完整冷读后，自然表演时长超出当前契约范围。",
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
        ending_contract=EndingContract(
            requested_preference=ContinuationPreference.AGENT_DECIDES,
            resolved_mode=ResolvedEndingMode.STANDALONE,
            episode_payoff="林夏与周衡在本集内拼出停电事件的真相。",
            required_closures=["四段证据形成完整因果链"],
            allowed_open_threads=[],
            continuation_hook=None,
            final_button="两人确认最后一段证据，停电事件真相落定。",
            decision_basis="测试剧本在本集完成主要因果。",
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
        self.story_orientation_calls = 0
        self.story_orientation_inputs: list[dict[str, object]] = []
        self.selected_method_skills: list[ProductionMethodSelection] = []
        self.method_selection_rationale: str | None = None
        self.story_architect_instructions: list[str] = []
        self.scene_writer_instructions: list[str] = []
        self.critic_calls = 0
        self.final_cold_read_calls = 0
        self.review_router_calls = 0
        self.source_contract_calls = 0
        self.revision_calls = 0
        self.repair_plan_calls = 0
        self.metadata_revision_calls = 0
        self.revision_scope_calls = 0
        self.revision_verifier_calls = 0
        self.repair_plan_inputs: list[dict[str, object]] = []
        self.metadata_revision_inputs: list[dict[str, object]] = []
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
        if instructions.startswith("你是 Story Orientation Agent"):
            self.story_orientation_calls += 1
            payload = json.loads(input_text)
            self.story_orientation_inputs.append(payload)
            source = self.request.input_text.strip() or "生成完整测试短剧"
            source_contract = StorySourceContract(
                explicit_commitments=[source],
                explicit_prohibitions=[],
                open_creative_choices=["未由用户明确规定的剧情细节可调整"],
            )
            return StoryOrientation(
                source_contract=source_contract,
                story_intent=StoryIntent(
                    audience_experience="清楚跟随四段证据推进并获得完整真相回收。",
                    dramatic_engine="每段新证据改变人物判断并推动下一次核对。",
                    progression_strategy="按四个场景逐步收紧因果链。",
                    tone_and_plausibility="保持图形化悬疑调性，服从剧内证据规则。",
                    protected_elements=list(source_contract.explicit_commitments),
                    drift_risks=["避免把证据核对写成无关说明"],
                    duration_contract=_fallback_duration_contract(self.request),
                ),
                selected_narrative_skills=[],
                narrative_skill_decision=(
                    "当前测试故事依靠通用悬疑职责即可，不额外装载爽感或荒诞升级技能。"
                ),
                selected_method_skills=self.selected_method_skills,
                method_selection_rationale=self.method_selection_rationale,
            ).model_dump_json()
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
        if "final cold-read Agent" in instructions:
            self.final_cold_read_calls += 1
            return ScriptReviewReport(
                summary="最终剧本的本集回报与结尾落点均已成立。",
                strengths=["结尾动作自然完成本集回报"],
                issues=[],
                performance_timing_assessment=(
                    "自然通读对白、动作与停顿后，目标时长可承载当前文本。"
                ),
                requires_revision=False,
            ).model_dump_json()
        if "Script Verifier Agent" in instructions:
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
                        issue_id=f"CAUSAL-LOGIC-{self.critic_calls}-{index + 1}",
                        category=f"CAUSAL_LOGIC_{index + 1}",
                        severity=(
                            ScriptReviewSeverity.HIGH
                            if index == 0
                            else ScriptReviewSeverity.MEDIUM
                        ),
                        owner=ReviewRepairOwner.STORY_ARCHITECT,
                        scene_sequences=[1, len(self.outline.scenes)],
                        observation=f"第 {index + 1} 项因果尚未闭合。",
                        recommendation="明确规则并回收开场事件。",
                    )
                    for index in range(issue_count)
                ],
                performance_timing_assessment=(
                    "自然通读对白、动作与停顿后，目标时长可承载当前文本。"
                ),
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
                requires_revision=requires_revision,
            ).model_dump_json()
        if "You are the Review Router Agent inside" in instructions:
            self.review_router_calls += 1
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
            self.source_contract_calls += 1
            source = self.request.input_text.strip() or "生成完整测试短剧"
            return StorySourceContract(
                explicit_commitments=[source],
                explicit_prohibitions=[],
                open_creative_choices=["未由用户明确规定的剧情细节可调整"],
            ).model_dump_json()
        if "Leader 的返修分派步骤" in instructions:
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
                        issue_id=str(issue["issue_id"]),
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
            required_indices = [
                action["issue_index"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            required_ids = [
                action["issue_id"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            previous_verification = payload["previous_revision_verification"]
            if previous_verification is not None:
                required_ids.extend(
                    violation["issue_id"]
                    for violation in previous_verification["violations"]
                    if violation["issue_id"] not in required_ids
                )
            return StoryRevisionVerification(
                accepted=True,
                summary=(
                    "Writer 已落实 Repair Plan；从第一场到最后一场"
                    "自然冷读后，整稿未引入回退。"
                ),
                satisfied_issue_ids=required_ids,
                satisfied_issue_indices=required_indices,
                violations=[],
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
                followup_rationale=(
                    "各场对白、动作、停顿与反应按顺序演读后，"
                    "全剧可在目标时长中自然完成。"
                ),
            ).model_dump_json()
        if "story-metadata revision Agent" in instructions:
            self.metadata_revision_calls += 1
            self.metadata_revision_inputs.append(json.loads(input_text))
            return StoryMetadataRevisionPatch().model_dump_json()
        if "revising an episode" in instructions:
            self.revision_calls += 1
            self.revision_inputs.append(json.loads(input_text))
            payload = self.outline.model_dump(mode="python")
            payload["story_summary"] += (
                f" 第 {self.revision_calls} 轮已补全开场与结尾的因果闭环。"
            )
            return json.dumps(payload, ensure_ascii=False)
        if "Scene Writer Agent" in instructions:
            self.scene_writer_instructions.append(instructions)
            payload = json.loads(input_text)
            self.script_scene_inputs.append(payload)
            sequence = int(payload["current_scene_node"]["sequence"])
            self.script_scene_calls.append(sequence)
            node = self.blueprint.scenes[sequence - 1]
            source_scene = self.outline.scenes[sequence - 1]
            lines = scene_dialogue_lines(self.outline, source_scene)
            return EpisodeScenePatch(
                scene_sequence=sequence,
                heading=node.heading,
                summary=node.summary,
                narrative_purpose=node.narrative_purpose,
                playable_actions=list(node.playable_actions),
                screenplay_text=compose_scene_screenplay(
                    heading=node.heading,
                    playable_actions=list(node.playable_actions),
                    lines=lines,
                ),
                target_duration_seconds=node.target_duration_seconds,
                location_names=list(node.location_names),
                continuity_in=node.continuity_in.model_copy(deep=True),
                continuity_out=node.continuity_out.model_copy(deep=True),
                lines=lines,
            ).model_dump_json()
        if "script-change scope Agent" in instructions:
            self.revision_scope_calls += 1
            payload = json.loads(input_text)
            del payload
            return ScriptRevisionContextPlan(
                scope=ScriptRevisionScope.SCENE_PATCH,
                affected_scene_sequences=[1, len(self.outline.scenes)],
                retrieval_query="修复开场与结尾的因果闭环",
                rationale="测试用最小场次范围。",
                preserve=["未相关场次"],
            ).model_dump_json()
        if "scene-local screenplay revision Agent" in instructions:
            payload = json.loads(input_text)
            self.local_revision_inputs.append(payload)
            patch = EpisodeScenePatch.model_validate(payload["current_scene"])
            self.local_revision_calls.append(patch.scene_sequence)
            patch.lines[0].text += "（局部改稿）"
            patch.screenplay_text = compose_scene_screenplay(
                heading=patch.heading,
                playable_actions=patch.playable_actions,
                lines=patch.lines,
            )
            return patch.model_dump_json()
        if "story-architect Agent" in instructions:
            self.story_architect_instructions.append(instructions)
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


class CriticRecommendedFinalColdReadClient(StagedLanguageClient):
    """Model a Critic that has a concrete reason to request a second read."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        if "Script Verifier Agent" not in instructions:
            return output
        report = ScriptReviewReport.model_validate_json(output)
        if report.requires_revision:
            return output
        return report.model_copy(
            update={
                "recommended_followup": StoryReviewFollowup.FINAL_COLD_READ,
                "followup_rationale": (
                    "结尾的整体观看效果需要脱离大纲元数据再通读一次。"
                ),
            }
        ).model_dump_json()


class SceneWriterRequestsReplanClient(StagedLanguageClient):
    """Ask the persisted Story Architect to reconsider one incompatible node."""

    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request)
        self.requested_replan = False

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Scene Writer Agent" in instructions and not self.requested_replan:
            self.requested_replan = True
            payload = json.loads(input_text)
            sequence = int(payload["current_scene_node"]["sequence"])
            return json.dumps(
                {
                    "scene_sequence": sequence,
                    "performance_timing_assessment": (
                        "当前蓝图冲突无法通过赶词解决，需要上游重规划。"
                    ),
                    "blocks": [],
                    "upstream_replan_request": {
                        "target": "STORY_ARCHITECT",
                        "reason": "当前节点与已持久化的 StoryIntent 发生实质冲突。",
                        "evidence": ["节点把核心设定改成了无关支线"],
                        "requested_change": "重新组织场景节点，让核心设定持续驱动可见结果。",
                        "preserve": ["用户的核心命题"],
                    },
                },
                ensure_ascii=False,
            )
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class LegacyVerifierFollowupClient(StagedLanguageClient):
    """Return a legacy follow-up value so new orchestration can ignore it."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        if "story-revision verifier Agent" not in instructions:
            return output
        verification = StoryRevisionVerification.model_validate_json(output)
        if not verification.accepted:
            return output
        return verification.model_copy(
            update={
                "recommended_followup": StoryReviewFollowup.FINAL_COLD_READ,
                "followup_rationale": (
                    "修订触及首尾呼应，需要脱离 Repair Plan 检查整体观看效果。"
                ),
            }
        ).model_dump_json()


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
            self.revision_scope_calls += 1
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


class EmptyAffectedRevisionClient(StagedLanguageClient):
    scope_failed_once = False

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            return StoryRepairPlan(
                summary="问题涉及整集因果，未提前指定局部场次。",
                source_promise="保留核心异常与结尾回收。",
                invariants=["保留核心悬念"],
                actions=[
                    StoryRepairAction(
                        issue_index=1,
                        issue_id=str(
                            payload["routed_story_review"]["issues"][0]["issue_id"]
                        ),
                        diagnosis=StoryRepairDiagnosis.OUTLINE_DEFECT,
                        should_revise=True,
                        rationale="需要逐场检查整集因果。",
                        affected_scene_sequences=[],
                        recommended_change="逐场修复，不整集重写。",
                        preserve=["核心悬念"],
                        validation_questions=["每场是否维持前后因果？"],
                    )
                ],
            ).model_dump_json()
        if "script-change scope Agent" in instructions:
            self.revision_scope_calls += 1
            if not self.scope_failed_once:
                self.scope_failed_once = True
                raise LanguageProviderError("transient revision scope failure")
            return ScriptRevisionContextPlan(
                scope=ScriptRevisionScope.SCENE_PATCH,
                affected_scene_sequences=[len(self.outline.scenes)],
                retrieval_query="修复结尾的因果回收",
                rationale="语义判断只需修订结尾场。",
                preserve=["未相关场次"],
            ).model_dump_json()
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
        if "Leader 的返修分派步骤" in instructions:
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
                        issue_id=str(issue["issue_id"]),
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


class HandoffRegressionClient(StagedLanguageClient):
    """Route the final regressed review downstream to exercise Revision Judge."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        if (
            "Script Verifier Agent" in instructions
            and "final cold-read Agent" not in instructions
            and self.critic_calls >= 4
        ):
            report = ScriptReviewReport.model_validate_json(output)
            return report.model_copy(
                deep=True,
                update={
                    "issues": [
                        issue.model_copy(
                            update={"owner": ReviewRepairOwner.SCENE_DIRECTOR}
                        )
                        for issue in report.issues
                    ]
                },
            ).model_dump_json()
        return output


class AdvisoryStoryReviewClient(StagedLanguageClient):
    """Exercise a Router that incorrectly asks to rewrite advisory findings."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if (
            "Script Verifier Agent" in instructions
            and "final cold-read Agent" not in instructions
        ):
            self.critic_calls += 1
            return ScriptReviewReport(
                summary="剧本已经成立，仍有两项可选润色建议。",
                strengths=["核心因果和公开反转均已闭合"],
                issues=[
                    ScriptReviewIssue(
                        issue_id="DIALOGUE-POLISH-1",
                        category="DIALOGUE_POLISH",
                        severity=ScriptReviewSeverity.MEDIUM,
                        owner=ReviewRepairOwner.SCENE_DIRECTOR,
                        scene_sequences=[2],
                        observation="一句台词还可以更精简。",
                        recommendation="如有需要可在后续局部润色。",
                    ),
                    ScriptReviewIssue(
                        issue_id="ENDING-TASTE-1",
                        category="ENDING_TASTE",
                        severity=ScriptReviewSeverity.LOW,
                        owner=ReviewRepairOwner.SCENE_DIRECTOR,
                        scene_sequences=[len(self.outline.scenes)],
                        observation="结尾语气可有其他风格选择。",
                        recommendation="作为用户可选风格建议保留。",
                    ),
                ],
                performance_timing_assessment=(
                    "自然通读完整剧本后，现有内容无需依靠赶词完成。"
                ),
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
                requires_revision=False,
            ).model_dump_json()
        if "You are the Review Router Agent inside" in instructions:
            self.review_router_calls += 1
            payload = json.loads(input_text)
            issues = payload["script_review"]["issues"]
            return ScriptReviewDisposition(
                action=ScriptReviewDispositionAction.REVISE_STORY,
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=index,
                        owner=ReviewRepairOwner.SCENE_DIRECTOR,
                        rationale="该建议可在后续导演或表演阶段处理。",
                    )
                    for index in range(1, len(issues) + 1)
                ],
                rationale="模型试图对中低级建议继续改稿。",
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
        if "Script Verifier Agent" in instructions and not self.invalid_critic_sent:
            self.invalid_critic_sent = True
            valid = ScriptReviewReport(
                summary="原审阅内容正确，但 JSON 有尾逗号。",
                strengths=["核心因果可继续"],
                issues=[],
                performance_timing_assessment=(
                    "自然通读后，文本可在目标时长内完成。"
                ),
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
                    satisfied_issue_ids=[
                        action["issue_id"]
                        for action in payload["story_repair_plan"]["actions"]
                        if action["should_revise"]
                    ],
                    satisfied_issue_indices=required,
                    violations=[
                        StoryRevisionViolation(
                            issue_id="RESOURCE-STATE-REGRESSION",
                            category="RESOURCE_STATE_REGRESSION",
                            scene_sequences=[1],
                            observation="同一线路既在供电又被写成早已断开。",
                            repair_instruction="统一断线发生时间并保留已成立的供电链。",
                        )
                    ],
                    duration_fit_assessment=_aligned_duration_fit_assessment(
                        self.request
                    ),
                ).model_dump_json()
            return StoryRevisionVerification(
                accepted=True,
                summary="定向修复后供电状态一致。",
                satisfied_issue_ids=list(
                    dict.fromkeys(
                        [
                            action["issue_id"]
                            for action in payload["story_repair_plan"]["actions"]
                            if action["should_revise"]
                        ]
                        + ["RESOURCE-STATE-REGRESSION"]
                    )
                ),
                satisfied_issue_indices=required,
                violations=[],
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class RepeatedRevisionRegressionClient(StagedLanguageClient):
    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        rejections_before_acceptance: int = 3,
    ) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.rejections_before_acceptance = rejections_before_acceptance

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-revision verifier Agent" in instructions:
            self.revision_verifier_calls += 1
            payload = json.loads(input_text)
            self.revision_verifier_inputs.append(payload)
            required = [
                action["issue_index"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            if self.revision_verifier_calls <= self.rejections_before_acceptance:
                satisfied_ids = [
                    action["issue_id"]
                    for action in payload["story_repair_plan"]["actions"]
                    if action["should_revise"]
                    and action["issue_id"] != "RESOURCE-STATE-REGRESSION"
                ]
                satisfied_indices = [
                    action["issue_index"]
                    for action in payload["story_repair_plan"]["actions"]
                    if action["should_revise"]
                    and action["issue_id"] != "RESOURCE-STATE-REGRESSION"
                ]
                return StoryRevisionVerification(
                    accepted=False,
                    summary="Writer 修订仍有一处供电时序回归。",
                    satisfied_issue_ids=satisfied_ids,
                    satisfied_issue_indices=satisfied_indices,
                    violations=[
                        StoryRevisionViolation(
                            issue_id="RESOURCE-STATE-REGRESSION",
                            category="RESOURCE_STATE_REGRESSION",
                            scene_sequences=[1],
                            observation="第 1 场的供电状态仍与已成立事实冲突。",
                            repair_instruction="继续只修正第 1 场的时序描述。",
                        )
                    ],
                    duration_fit_assessment=_aligned_duration_fit_assessment(
                        self.request
                    ),
                ).model_dump_json()
            return StoryRevisionVerification(
                accepted=True,
                summary="多轮定向修复后供电时序一致。",
                satisfied_issue_ids=list(
                    dict.fromkeys(
                        [
                            action["issue_id"]
                            for action in payload["story_repair_plan"]["actions"]
                            if action["should_revise"]
                        ]
                        + ["RESOURCE-STATE-REGRESSION"]
                    )
                ),
                satisfied_issue_indices=required,
                violations=[],
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class DurationReallocationClient(StagedLanguageClient):
    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        fail_final_balance_once: bool = False,
        first_scene_shift_seconds: float = 6.0,
    ) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.fail_final_balance_once = fail_final_balance_once
        self.first_scene_shift_seconds = first_scene_shift_seconds
        self.failed_final_balance = False
        self.duration_constraints: list[dict[str, object]] = []

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            plan = StoryRepairPlan.model_validate_json(output)
            plan.actions = [
                action.model_copy(
                    update={
                        "affected_scene_sequences": [1, len(self.outline.scenes)],
                        "target_duration_change_required": True,
                    }
                )
                for action in plan.actions
            ]
            return plan.model_dump_json()
        if "scene-local screenplay revision Agent" in instructions:
            payload = json.loads(input_text)
            constraint = payload["duration_constraint"]
            assert isinstance(constraint, dict)
            self.duration_constraints.append(constraint)
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            patch = EpisodeScenePatch.model_validate_json(output)
            if patch.scene_sequence == 1:
                patch.target_duration_seconds -= self.first_scene_shift_seconds
            elif self.fail_final_balance_once and not self.failed_final_balance:
                self.failed_final_balance = True
                patch.target_duration_seconds -= 2.0
            return patch.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class VerifierDurationReallocationClient(StagedLanguageClient):
    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.rejected_duration_once = False
        self.duration_constraints: list[dict[str, object]] = []

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            plan = StoryRepairPlan.model_validate_json(output)
            if any(
                action.issue_id == "SCENE-DURATION-REALLOCATION"
                for action in plan.actions
            ):
                plan.actions = [
                    action.model_copy(
                        update={
                            "affected_scene_sequences": [
                                1,
                                len(self.outline.scenes),
                            ],
                            "target_duration_change_required": True,
                        }
                    )
                    for action in plan.actions
                ]
            return plan.model_dump_json()
        if "story-revision verifier Agent" in instructions:
            if not self.rejected_duration_once:
                self.rejected_duration_once = True
                self.revision_verifier_calls += 1
                payload = json.loads(input_text)
                self.revision_verifier_inputs.append(payload)
                return StoryRevisionVerification(
                    accepted=False,
                    summary="需要把开场的 6 秒重分配给结尾。",
                    satisfied_issue_ids=[
                        action["issue_id"]
                        for action in payload["story_repair_plan"]["actions"]
                        if action["should_revise"]
                    ],
                    satisfied_issue_indices=[
                        action["issue_index"]
                        for action in payload["story_repair_plan"]["actions"]
                        if action["should_revise"]
                    ],
                    violations=[
                        StoryRevisionViolation(
                            issue_id="SCENE-DURATION-REALLOCATION",
                            category="SCENE_DURATION_REALLOCATION",
                            scene_sequences=[1, len(self.outline.scenes)],
                            target_duration_change_required=True,
                            observation="开场偏长，结尾的行动后果偏短。",
                            repair_instruction=(
                                "将第 1 场减少 6 秒，并将 6 秒增加到最后一场。"
                            ),
                        )
                    ],
                    duration_fit_assessment=_aligned_duration_fit_assessment(
                        self.request
                    ),
                ).model_dump_json()
            return super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
        if "scene-local screenplay revision Agent" in instructions:
            payload = json.loads(input_text)
            constraint = payload["duration_constraint"]
            assert isinstance(constraint, dict)
            if constraint["allow_change"] is True:
                self.duration_constraints.append(constraint)
                output = super().generate_text(
                    instructions=instructions,
                    input_text=input_text,
                    image_urls=image_urls,
                    max_output_tokens=max_output_tokens,
                )
                patch = EpisodeScenePatch.model_validate_json(output)
                if patch.scene_sequence == 1:
                    patch.target_duration_seconds -= 6.0
                return patch.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class MetadataReconciliationClient(StagedLanguageClient):
    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.reconciled_summary = (
            "两人按同一条明确计量口径核对证据，场景正文与故事摘要保持一致。"
        )
        self.reconciled_appearance = "短发，眼神清醒，左眉下有一道稳定的浅痣"

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-metadata revision Agent" in instructions:
            self.metadata_revision_calls += 1
            payload = json.loads(input_text)
            self.metadata_revision_inputs.append(payload)
            character_id = payload["current_global_metadata"]["characters"][0][
                "character_id"
            ]
            return StoryMetadataRevisionPatch(
                story_summary=self.reconciled_summary,
                character_patches=[
                    StoryCharacterMetadataPatch(
                        character_id=character_id,
                        appearance=self.reconciled_appearance,
                    )
                ],
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class RetryOnceSceneContentClient(StagedLanguageClient):
    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        writer_failure: str | None = None,
        revision_failure: str | None = None,
    ) -> None:
        super().__init__(request, revision_rounds_required=1)
        if writer_failure not in {None, "timeout", "structure"}:
            raise ValueError(f"Unknown writer failure: {writer_failure}")
        if revision_failure not in {None, "timeout", "structure"}:
            raise ValueError(f"Unknown revision failure: {revision_failure}")
        self.writer_failure = writer_failure
        self.revision_failure = revision_failure
        self.writer_failure_responses = 0
        self.revision_failure_responses = 0

    @staticmethod
    def _invalid_scene_sequence_patch(value: str) -> str:
        patch = EpisodeScenePatch.model_validate_json(value)
        patch.scene_sequence += 1
        return patch.model_dump_json()

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Scene Writer Agent" in instructions and self.writer_failure is not None:
            payload = json.loads(input_text)
            sequence = int(payload["current_scene_node"]["sequence"])
            if sequence == 1:
                if self.writer_failure == "timeout" and self.writer_failure_responses == 0:
                    self.writer_failure_responses += 1
                    self.script_scene_inputs.append(payload)
                    self.script_scene_calls.append(sequence)
                    raise LanguageProviderError("The operation timed out.")
                if (
                    self.writer_failure == "structure"
                    and self.writer_failure_responses == 0
                ):
                    self.writer_failure_responses += 1
                    valid = super().generate_text(
                        instructions=instructions,
                        input_text=input_text,
                        image_urls=image_urls,
                        max_output_tokens=max_output_tokens,
                    )
                    return self._invalid_scene_sequence_patch(valid)
        if (
            "scene-local screenplay revision Agent" in instructions
            and self.revision_failure is not None
        ):
            payload = json.loads(input_text)
            sequence = int(payload["current_scene"]["scene_sequence"])
            if sequence == 1 and self.revision_failure_responses == 0:
                self.revision_failure_responses += 1
                if self.revision_failure == "timeout":
                    self.local_revision_inputs.append(payload)
                    self.local_revision_calls.append(sequence)
                    raise LanguageProviderError("The operation timed out.")
                valid = super().generate_text(
                    instructions=instructions,
                    input_text=input_text,
                    image_urls=image_urls,
                    max_output_tokens=max_output_tokens,
                )
                return self._invalid_scene_sequence_patch(valid)
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class RetryOncePlanningStageClient(StagedLanguageClient):
    def __init__(self, request: CreateProjectRequest, *, marker: str) -> None:
        super().__init__(request, revision_rounds_required=1)
        self.marker = marker
        self.failed = False

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if self.marker in instructions and not self.failed:
            self.failed = True
            raise LanguageProviderError("transient planning-stage failure")
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class LegacyCriticFollowupClient(RetryOncePlanningStageClient):
    """Return the legacy Critic follow-up value for compatibility coverage."""

    def __init__(self, request: CreateProjectRequest, *, marker: str) -> None:
        super().__init__(request, marker=marker)
        self.revision_rounds_required = 0

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        if "Script Verifier Agent" not in instructions:
            return output
        report = ScriptReviewReport.model_validate_json(output)
        if report.requires_revision:
            return output
        return report.model_copy(
            update={
                "recommended_followup": StoryReviewFollowup.FINAL_COLD_READ,
                "followup_rationale": (
                    "终场的独立可读性仍需一次不依赖大纲解释的复核。"
                ),
            }
        ).model_dump_json()


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


def _continue_verifier_handoff_for_test(
    service: ShortDramaService,
    response,
):
    """Act as the owning Leader in tests that are exercising later stages."""

    run = response.run
    if run.status != PlanningRunStatus.NEEDS_ATTENTION:
        return response
    if run.current_step not in {
        PlanningRunStep.SCRIPT_VERIFIER,
        PlanningRunStep.SCRIPT_CRITIC,
        PlanningRunStep.REVISION_VERIFIER,
    }:
        return response
    return service.resolve_story_verification(
        run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION,
        rationale=(
            "测试正在验证后续修订链路；模拟所属 AgentRun Leader "
            "阅读精确 Verifier 结果后选择聚焦修订。"
        ),
    )


def _finish(service: ShortDramaService, run_id: str, *, max_steps: int = 80):
    response = service.get_planning_run(run_id)
    for _ in range(max_steps):
        if response.run.status == PlanningRunStatus.COMPLETE:
            return response
        continued = _continue_verifier_handoff_for_test(service, response)
        if continued is not response:
            response = continued
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
        continued = _continue_verifier_handoff_for_test(service, response)
        if continued is not response:
            response = continued
            if response.run.current_step == step:
                return response
        response = service.advance_planning_run(run_id)
    raise AssertionError(f"planning run did not reach {step} within the test bound")


def _replace_duration_authority_for_test(
    service: ShortDramaService,
    response,
    authority: DurationAuthority,
):
    assert response.project is not None
    run = response.run.model_copy(deep=True)
    assert run.story_intent is not None
    contract = run.story_intent.duration_contract
    assert contract is not None
    contract_updates: dict[str, object] = {"authority": authority}
    if authority == DurationAuthority.SOURCE_LED:
        contract_updates.update(
            {
                "target_origin": DurationTargetOrigin.SOURCE_DECLARED,
                "mismatch_resolution": (
                    DurationMismatchResolution.REPORT_NATURAL_RUNTIME
                ),
            }
        )
    elif authority == DurationAuthority.AUDIO_LED:
        contract_updates.update(
            {
                "target_origin": DurationTargetOrigin.MASTER_AUDIO,
                "mismatch_resolution": DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
            }
        )
    revised_intent = run.story_intent.model_copy(
        deep=True,
        update={
            "duration_contract": contract.model_copy(
                deep=True,
                update=contract_updates,
            )
        },
    )
    outline = EpisodeOutline.model_validate_json(run.outline_json or "")
    run.story_intent = revised_intent
    run.outline_json = outline.model_copy(
        deep=True,
        update={"story_intent": revised_intent},
    ).model_dump_json()
    service.repository.save_planning_run_with_project(
        run,
        response.project.document,
    )
    return service.get_planning_run(run.id)


def test_legacy_critic_final_cold_read_followup_is_not_dispatched(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = LegacyCriticFollowupClient(request, marker="unused-marker")
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)

    reviewed = service.advance_planning_run(run.id)

    assert reviewed.run.current_step == PlanningRunStep.FINALIZE
    assert reviewed.run.review_report is not None
    assert (
        reviewed.run.review_report.recommended_followup
        == StoryReviewFollowup.FINAL_COLD_READ
    )
    assert language.final_cold_read_calls == 0
    assert any(
        "不再自动转交独立 Final Cold Read" in warning
        for warning in reviewed.run.warnings
    )


def test_legacy_verifier_final_cold_read_followup_is_not_dispatched(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = LegacyVerifierFollowupClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)

    verified = service.advance_planning_run(run.id)

    assert verified.run.current_step == PlanningRunStep.FINALIZE
    assert verified.run.latest_revision_verification is not None
    assert (
        verified.run.latest_revision_verification.recommended_followup
        == StoryReviewFollowup.FINAL_COLD_READ
    )
    assert language.final_cold_read_calls == 0
    assert any(
        "不再自动转交独立 Final Cold Read" in warning
        for warning in verified.run.warnings
    )


def test_long_form_planning_advances_one_agent_stage_or_scene_at_a_time(
    tmp_path: Path,
) -> None:
    request = _request(430)
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)

    started = _start_owned_planning(service, request)
    assert started.run.current_step == PlanningRunStep.STORY_ORIENTATION
    assert started.run.source_contract is None
    assert started.run.story_intent is None
    assert language.story_orientation_calls == 0
    assert started.project is not None
    assert started.project.document.project.stage.value == "INPUT"

    after_orientation = service.advance_planning_run(started.run.id)
    assert after_orientation.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert after_orientation.run.source_contract is not None
    assert after_orientation.run.story_intent is not None
    assert after_orientation.run.selected_narrative_skill_ids == []
    assert language.story_orientation_calls == 1

    after_architect = service.advance_planning_run(started.run.id)
    assert after_architect.run.current_step == PlanningRunStep.SCENE_WRITER
    assert after_architect.run.total_scenes == len(language.outline.scenes)
    assert after_architect.run.directed_scene_json == []

    after_writer = _advance_to_step(
        service,
        started.run.id,
        PlanningRunStep.SCRIPT_VERIFIER,
    )
    assert after_writer.run.next_script_scene_sequence == len(language.outline.scenes) + 1
    assert language.script_scene_calls == list(
        range(1, len(language.outline.scenes) + 1)
    )
    assert "episode_manifest" not in language.script_scene_inputs[-1]
    assert language.script_scene_inputs[-1]["previous_scene_boundary"] is not None

    after_critic = service.advance_planning_run(started.run.id)
    assert after_critic.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert after_critic.run.review_report is not None
    assert after_critic.run.review_disposition is not None
    assert language.critic_calls == 1
    assert language.review_router_calls == 0
    assert language.final_cold_read_calls == 0
    assert (
        after_critic.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE
    )
    assert after_critic.run.final_cold_read_used is False
    assert PlanningRunStep.FINAL_COLD_READ not in after_critic.run.completed_steps

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


def test_story_orientation_method_selection_survives_restart_and_reaches_agents(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={
            "name": "新品牌商业短片",
            "input_text": (
                "为已提供的新产品做一支品牌商业片，只使用简报中的事实，"
                "用真实使用动作证明核心价值。"
            ),
        }
    )
    language = StagedLanguageClient(request)
    language.selected_method_skills = [
        ProductionMethodSelection(
            skill_id="method.brand_promo",
            rationale="需要把品牌事实、核心主张与可见产品证据对齐。",
        )
    ]
    language.method_selection_rationale = (
        "这个项目需要商业片方法；具体 Style 仍由项目资产决定。"
    )
    service = _service(tmp_path, language)
    started = _start_owned_planning(service, request)

    oriented = service.advance_planning_run(started.run.id)

    assert oriented.run.selected_method_skill_ids == [
        "method.brand_promo"
    ]
    assert oriented.run.method_selection_reasons == [
        "需要把品牌事实、核心主张与可见产品证据对齐。"
    ]
    assert oriented.run.method_selection_rationale == (
        "这个项目需要商业片方法；具体 Style 仍由项目资产决定。"
    )
    assert {
        item["id"]
        for item in language.story_orientation_inputs[0][
            "available_production_methods"
        ]
    } == {descriptor.id for descriptor in production_method_skill_catalog()}

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(started.run.id).run
    assert restored.selected_method_skill_ids == [
        "method.brand_promo"
    ]

    after_architect = restarted.advance_planning_run(started.run.id)
    assert after_architect.run.current_step == PlanningRunStep.SCENE_WRITER
    assert "品牌宣传片" in (
        language.story_architect_instructions[-1]
    )
    assert "## 品牌事实与创意" in language.story_architect_instructions[-1]
    assert "区分已证实事实、用户主张和概念创作" in language.story_architect_instructions[-1]

    restarted.advance_planning_run(started.run.id)
    assert "品牌宣传片" in language.scene_writer_instructions[-1]
    assert "## 文案与故事结构" in language.scene_writer_instructions[-1]
    assert "用产品专属细节建立可信度" in language.scene_writer_instructions[-1]


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
    assert run.current_step == PlanningRunStep.STORY_ORIENTATION

    oriented = service.advance_planning_run(run.id)
    assert oriented.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert oriented.run.source_contract is not None
    assert oriented.run.story_intent is not None

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


def test_content_planning_job_requires_project_and_owner_agent_run(
    tmp_path: Path,
) -> None:
    planning_request = CreateProjectRequest(
        name="Agent 归属不可缺失",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    started = _start_owned_planning(service, planning_request).run
    payload = started.model_dump(mode="python")

    without_project = dict(payload)
    without_project.pop("project_id")
    with pytest.raises(ValidationError, match="project_id"):
        PlanningRunRecord.model_validate(without_project)

    without_owner = dict(payload)
    without_owner.pop("owner_agent_run_id")
    with pytest.raises(ValidationError, match="owner_agent_run_id"):
        PlanningRunRecord.model_validate(without_owner)

    with pytest.raises(ValidationError, match="project_id"):
        PlanningRunRecord.model_validate({**payload, "project_id": None})
    with pytest.raises(ValidationError, match="owner_agent_run_id"):
        PlanningRunRecord.model_validate({**payload, "owner_agent_run_id": None})


def test_deleting_project_removes_its_active_content_planning_job(
    tmp_path: Path,
) -> None:
    planning_request = CreateProjectRequest(
        name="删除项目同步清理内容规划 Job",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    started = _start_owned_planning(service, planning_request).run
    project_id = started.project_id
    run_id = started.id

    advanced = _advance_to_step(
        service,
        run_id,
        PlanningRunStep.SCRIPT_VERIFIER,
    ).run
    assert advanced.status == PlanningRunStatus.RUNNING
    assert service.repository.list_planning_outline_snapshots(run_id)
    assert service.repository.list_planning_script_documents(
        run_id,
        advanced.revision_round,
    )

    assert service.delete_project(project_id) is True
    assert service.repository.get_project(project_id) is None
    assert service.repository.get_planning_run(run_id) is None
    assert service.repository.list_planning_outline_snapshots(run_id) == []
    assert service.repository.list_planning_review_snapshots(run_id) == []
    assert service.repository.list_planning_script_documents(
        run_id,
        advanced.revision_round,
    ) == []


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

    oriented = service.advance_planning_run(scaffold.document.planning.run_id)
    assert oriented.run.current_step == PlanningRunStep.STORY_ARCHITECT

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
    assert resumed.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert resumed.run.source_contract is not None
    assert resumed.run.story_intent is not None
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


def test_script_only_owner_finalize_retry_recognizes_script_ready_document(
    tmp_path: Path,
) -> None:
    planning_request = _request(120).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-script-only-finalize-retry",
        owner_agent_run_id="agent-run-script-only-finalize-owner",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None
    run_id = scaffold.document.planning.run_id
    _advance_to_step(service, run_id, PlanningRunStep.FINALIZE)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_script_only_finalize_cursor_update
            BEFORE UPDATE ON planning_runs
            BEGIN
                SELECT RAISE(ABORT, 'forced script-only planning update failure');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced script-only planning update failure",
    ):
        service.advance_planning_run(run_id)

    interrupted = service.get_project(scaffold.document.project.id).document
    assert interrupted.project.stage == ProjectStage.SCRIPT_READY
    assert interrupted.planning is not None
    assert interrupted.planning.status == PlanningRunStatus.RUNNING
    assert len(interrupted.script_versions) == 1
    assert interrupted.assets == []
    assert interrupted.shots == []
    persisted_run = service.get_planning_run(run_id).run
    assert persisted_run.current_step == PlanningRunStep.FINALIZE

    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("DROP TRIGGER reject_script_only_finalize_cursor_update")

    completed = service.advance_planning_run(run_id)

    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY
    assert completed.project.document.planning is not None
    assert completed.project.document.planning.status == PlanningRunStatus.COMPLETE
    assert len(completed.project.document.script_versions) == 1
    assert completed.run.review_report is not None
    assert completed.run.review_report.requires_revision is False
    assert completed.run.review_disposition is not None
    assert (
        completed.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE
    )
    assert (
        completed.project.document.script_versions[0].approval_status
        == ApprovalStatus.APPROVED
    )
    assert completed.project.document.assets == []
    assert completed.project.document.shots == []


def test_script_only_finalize_registers_user_image_before_later_asset_planning(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "script-only-hero.png"
    Image.new("RGB", (48, 64), "#91745d").save(image_path)
    planning_request = _request(120).model_copy(
        update={
            "source_mode": SourceMode.IMAGE_SEEDED,
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "这张照片就是男主角，先完成剧本。",
            "input_images": [
                CreativeInputImage(
                    local_path=str(image_path),
                    descriptor="用户指定的男主角身份照片",
                )
            ],
        }
    )
    service = _service(tmp_path, StagedLanguageClient(planning_request))
    scaffold = service.create_project(
        planning_request,
        project_id="project-script-only-source-intake",
        owner_agent_run_id="agent-run-script-only-source-intake",
    )
    assert scaffold.document.planning is not None
    assert scaffold.document.planning.run_id is not None
    run_id = scaffold.document.planning.run_id
    _advance_to_step(service, run_id, PlanningRunStep.FINALIZE)

    completed = service.advance_planning_run(run_id)

    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY
    assert completed.project.document.shots == []
    assert len(completed.project.document.assets) == 1
    source_asset = completed.project.document.assets[0]
    assert source_asset.kind.value == "REFERENCE"
    assert source_asset.approved_version_id is not None
    source_version = next(
        version
        for version in source_asset.versions
        if version.id == source_asset.approved_version_id
    )
    assert source_version.local_path is not None
    assert Path(source_version.local_path).is_file()
    assert source_version.review_notes is not None
    assert "source intake" in source_version.review_notes
    assert completed.project.document.reference_plan == []


def test_scene_writer_upstream_replan_is_persisted_as_the_next_creative_unit(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = SceneWriterRequestsReplanClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.SCENE_WRITER)
    assert ready.run.story_intent is not None

    replanning = service.advance_planning_run(run.id)

    assert replanning.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert replanning.run.scene_script_json == []
    assert replanning.run.next_script_scene_sequence == 1
    assert replanning.run.step_retry_count == 0
    assert replanning.run.step_retry_feedback is not None
    feedback = json.loads(replanning.run.step_retry_feedback)
    assert feedback["requested_by"] == "SCENE_WRITER"
    assert feedback["scene_sequence"] == 1
    assert "StoryIntent" in feedback["reason"]
    assert any(
        "Scene Writer 请求重新进入 STORY_ARCHITECT" in item
        for item in replanning.run.warnings
    )

    restarted = _service(tmp_path, language)
    rearchitected = restarted.advance_planning_run(run.id)

    assert rearchitected.run.current_step == PlanningRunStep.SCENE_WRITER
    assert rearchitected.run.step_retry_feedback is None


def test_planning_run_persists_revision_and_scene_progress_across_restart(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, revision_rounds_required=1)
    first = _service(tmp_path, language)
    run = _start_owned_planning(first, request).run

    ready_to_write = _advance_to_step(first, run.id, PlanningRunStep.SCENE_WRITER)
    assert ready_to_write.run.source_contract is not None
    assert ready_to_write.run.story_intent is not None
    first_script_scene = first.advance_planning_run(run.id)
    assert len(first_script_scene.run.scene_script_json) == 1
    restarted_writer = _service(tmp_path, language)
    restored_writer = restarted_writer.get_planning_run(run.id)
    assert len(restored_writer.run.scene_script_json) == 1
    _advance_to_step(restarted_writer, run.id, PlanningRunStep.SCRIPT_VERIFIER)
    after_critic = restarted_writer.advance_planning_run(run.id)
    assert after_critic.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert after_critic.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert after_critic.run.review_report is not None
    assert after_critic.run.review_report.requires_revision is True
    assert after_critic.run.review_disposition is None
    assert after_critic.run.source_contract is not None
    assert language.review_router_calls == 0

    restarted_after_critic = _service(tmp_path, language)
    restored_critic = restarted_after_critic.get_planning_run(run.id).run
    assert restored_critic.status == PlanningRunStatus.NEEDS_ATTENTION
    assert restored_critic.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert restored_critic.review_report == after_critic.run.review_report
    assert restored_critic.review_disposition is None
    resolved = restarted_after_critic.resolve_story_verification(
        after_critic.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION,
        rationale="精确因果问题适合由 Repair Leader 重新规划最小修订范围。",
    )
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION
    assert resolved.run.review_disposition is not None
    review_snapshots = (
        restarted_after_critic.repository.list_planning_review_snapshots(run.id)
    )
    assert len(review_snapshots) == 1
    assert review_snapshots[0].report == resolved.run.review_report
    assert review_snapshots[0].disposition == resolved.run.review_disposition
    assert language.review_router_calls == 0

    after_repair_plan = restarted_after_critic.advance_planning_run(run.id)
    expected_revision_scenes = [1, len(language.outline.scenes)]
    assert after_repair_plan.run.current_step == PlanningRunStep.SCENE_REVISION
    assert after_repair_plan.run.latest_repair_plan is not None
    assert after_repair_plan.run.revision_base_outline_json is not None
    assert (
        after_repair_plan.run.revision_scene_sequences
        == expected_revision_scenes
    )
    assert after_repair_plan.run.next_revision_scene_index == 0
    assert language.repair_plan_calls == 1
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.local_revision_calls == []

    restarted_before_revision = _service(tmp_path, language)
    restored_repair_plan = restarted_before_revision.get_planning_run(run.id).run
    assert restored_repair_plan.current_step == PlanningRunStep.SCENE_REVISION
    assert restored_repair_plan.latest_repair_plan == after_repair_plan.run.latest_repair_plan
    assert restored_repair_plan.revision_base_outline_json == (
        after_repair_plan.run.revision_base_outline_json
    )
    assert restored_repair_plan.revision_changed_scene_sequences == []
    assert (
        restored_repair_plan.latest_revision_target_scene_sequences
        == expected_revision_scenes
    )
    assert restored_repair_plan.latest_revision_changed_scene_sequences == []
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.local_revision_calls == []
    assert language.revision_verifier_calls == 0

    first_patch = restarted_before_revision.advance_planning_run(run.id)
    assert first_patch.run.current_step == PlanningRunStep.SCENE_REVISION
    assert first_patch.run.next_revision_scene_index == 1
    assert first_patch.run.revision_changed_scene_sequences == [1]
    assert first_patch.run.latest_revision_changed_scene_sequences == [1]
    assert language.local_revision_calls == [1]
    assert language.revision_verifier_calls == 0
    partially_revised = EpisodeOutline.model_validate_json(
        first_patch.run.outline_json or ""
    )
    assert "（局部改稿）" in scene_dialogue_lines(
        partially_revised,
        partially_revised.scenes[0],
    )[0].text
    assert "（局部改稿）" not in scene_dialogue_lines(
        partially_revised,
        partially_revised.scenes[-1],
    )[0].text

    restarted_mid_revision = _service(tmp_path, language)
    restored_patch = restarted_mid_revision.get_planning_run(run.id).run
    assert restored_patch.outline_json == first_patch.run.outline_json
    assert restored_patch.next_revision_scene_index == 1
    assert restored_patch.revision_changed_scene_sequences == [1]

    second_patch = restarted_mid_revision.advance_planning_run(run.id)
    assert second_patch.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert second_patch.run.next_revision_scene_index == 2
    assert second_patch.run.revision_changed_scene_sequences == expected_revision_scenes
    assert language.local_revision_calls == expected_revision_scenes
    assert language.revision_verifier_calls == 0

    restarted_before_verifier = _service(tmp_path, language)
    restored_verifier = restarted_before_verifier.get_planning_run(run.id).run
    assert restored_verifier.current_step == PlanningRunStep.REVISION_VERIFIER
    assert restored_verifier.outline_json == second_patch.run.outline_json
    assert language.revision_verifier_calls == 0
    assert restored_verifier.project_id is not None
    verifier_project = restarted_before_verifier.get_project(
        restored_verifier.project_id
    )
    restarted_before_verifier.repository.save_planning_run_with_project(
        restored_verifier.model_copy(
            update={
                # Simulate an in-flight record written before terminal revision
                # trace history was persisted separately from the active cursor.
                "latest_revision_target_scene_sequences": [],
                "latest_revision_changed_scene_sequences": [],
            }
        ),
        verifier_project.document,
    )

    revised = restarted_before_verifier.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert revised.run.revision_round == 1
    assert revised.run.revision_scene_sequences == []
    assert revised.run.revision_changed_scene_sequences == []
    assert (
        revised.run.latest_revision_target_scene_sequences
        == expected_revision_scenes
    )
    assert (
        revised.run.latest_revision_changed_scene_sequences
        == expected_revision_scenes
    )
    assert language.revision_verifier_calls == 1
    assert language.revision_calls == 0
    assert language.final_cold_read_calls == 0

    first_scene = restarted_before_verifier.advance_planning_run(run.id)
    assert len(first_scene.run.directed_scene_json) == 1

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(run.id)
    assert restored.run.revision_round == 1
    assert restored.run.revision_scene_sequences == []
    assert restored.run.revision_changed_scene_sequences == []
    assert (
        restored.run.latest_revision_target_scene_sequences
        == expected_revision_scenes
    )
    assert (
        restored.run.latest_revision_changed_scene_sequences
        == expected_revision_scenes
    )
    assert len(restored.run.directed_scene_json) == 1
    assert restored.run.review_disposition is not None

    completed = _finish(restarted, run.id)
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.project_id is not None
    assert language.revision_calls == 0
    assert language.local_revision_calls == expected_revision_scenes
    assert language.critic_calls == 1
    assert language.review_router_calls == 0
    assert language.source_contract_calls == 0
    assert language.repair_plan_calls == 1
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.revision_verifier_calls == 1
    assert language.scene_inputs[1]["previous_actual_handoff"]["known_facts"][-1] == (
        "staged-scene-1-complete"
    )
    outlines = restarted.repository.list_planning_outline_snapshots(run.id)
    reviews = restarted.repository.list_planning_review_snapshots(run.id)
    assert [item.revision_round for item in outlines] == [0, 1]
    assert [item.review_round for item in reviews] == [1]
    assert all(item.disposition is not None for item in reviews)
    assert [len(item.report.issues) for item in reviews] == [1]
    assert completed.run.revision_judge is None
    assert (
        completed.run.latest_revision_target_scene_sequences
        == expected_revision_scenes
    )
    assert (
        completed.run.latest_revision_changed_scene_sequences
        == expected_revision_scenes
    )
    assert language.judge_calls == 0


def test_revision_writer_timing_uses_pending_user_visible_round(
    tmp_path: Path,
) -> None:
    class RevisionTimingClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            if "scene-local screenplay revision Agent" not in instructions:
                return output
            patch = EpisodeScenePatch.model_validate_json(output)
            return patch.model_copy(
                update={
                    "performance_timing_assessment": (
                        "按本次修订后的动作与对白自然演读，目标时长可承载。"
                    )
                }
            ).model_dump_json()

    request = _request()
    language = RevisionTimingClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    queued = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)

    assert queued.run.revision_round == 0
    first_patch = service.advance_planning_run(run.id)
    revision_timing = [
        assessment
        for assessment in first_patch.run.scene_timing_assessments
        if assessment.stage == "REVISION"
    ]

    assert len(revision_timing) == 1
    assert revision_timing[0].revision_round == 1


@pytest.mark.parametrize("failure_mode", ["timeout", "structure"])
def test_scene_writer_failure_retries_the_same_scene_before_advancing(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    request = _request()
    language = RetryOnceSceneContentClient(
        request,
        writer_failure=failure_mode,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.SCENE_WRITER)
    assert ready.run.current_step == PlanningRunStep.SCENE_WRITER
    assert ready.run.next_script_scene_sequence == 1

    failed = service.advance_planning_run(run.id)

    assert failed.run.status == PlanningRunStatus.RUNNING
    assert failed.run.current_step == PlanningRunStep.SCENE_WRITER
    assert failed.run.next_script_scene_sequence == 1
    assert failed.run.scene_script_json == []
    assert failed.run.scene_script_retry_count == 1
    assert failed.run.scene_script_retry_feedback is not None
    feedback = failed.run.scene_script_retry_feedback
    assert any(
        "下次 planning.advance 将重试同一场" in warning
        for warning in failed.run.warnings
    )

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.next_script_scene_sequence == 1
    assert persisted.scene_script_retry_feedback == feedback
    assert persisted.scene_script_retry_count == 1

    succeeded = restarted.advance_planning_run(run.id)

    assert succeeded.run.current_step == PlanningRunStep.SCENE_WRITER
    assert succeeded.run.next_script_scene_sequence == 2
    assert len(succeeded.run.scene_script_json) == 1
    assert succeeded.run.scene_script_retry_feedback is None
    assert succeeded.run.scene_script_retry_count == 0
    assert language.script_scene_inputs[-1]["previous_retry_feedback"] == feedback
    assert EpisodeScenePatch.model_validate_json(
        succeeded.run.scene_script_json[0]
    ).scene_sequence == 1


@pytest.mark.parametrize(
    ("step", "instruction_marker"),
    [
        (PlanningRunStep.STORY_ORIENTATION, "Story Orientation Agent"),
        (PlanningRunStep.STORY_ARCHITECT, "story-architect Agent"),
        (PlanningRunStep.SCRIPT_VERIFIER, "Script Verifier Agent"),
        (PlanningRunStep.SCRIPT_CRITIC, "Script Verifier Agent"),
        (PlanningRunStep.STORY_REVISION, "Leader 的返修分派步骤"),
        (PlanningRunStep.STORY_METADATA_REVISION, "story-metadata revision Agent"),
        (PlanningRunStep.REVISION_VERIFIER, "story-revision verifier Agent"),
        (PlanningRunStep.FINAL_COLD_READ, "final cold-read Agent"),
    ],
)
def test_provider_backed_planning_stage_failure_retries_the_same_cursor(
    tmp_path: Path,
    step: PlanningRunStep,
    instruction_marker: str,
) -> None:
    request = _request(300).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = RetryOncePlanningStageClient(
        request,
        marker=instruction_marker,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    if step in {
        PlanningRunStep.SCRIPT_CRITIC,
        PlanningRunStep.STORY_METADATA_REVISION,
        PlanningRunStep.FINAL_COLD_READ,
    }:
        # 这些步骤不再由新流程自动进入，但已持久化的旧游标
        # 仍可恢复和重试。
        preparation_step = {
            PlanningRunStep.SCRIPT_CRITIC: PlanningRunStep.SCRIPT_VERIFIER,
            PlanningRunStep.STORY_METADATA_REVISION: PlanningRunStep.SCENE_REVISION,
            PlanningRunStep.FINAL_COLD_READ: PlanningRunStep.SCRIPT_VERIFIER,
        }[step]
        before_legacy_step = _advance_to_step(
            service,
            run.id,
            preparation_step,
            max_steps=80,
        )
        assert before_legacy_step.project is not None
        legacy_run = before_legacy_step.run.model_copy(
            deep=True,
            update={"current_step": step},
        )
        service.repository.save_planning_run_with_project(
            legacy_run,
            before_legacy_step.project.document,
        )
        ready = service.get_planning_run(run.id)
    else:
        ready = _advance_to_step(service, run.id, step, max_steps=80)

    failed = service.advance_planning_run(run.id)

    assert failed.run.status == PlanningRunStatus.RUNNING
    assert failed.run.current_step == step
    assert failed.run.step_retry_count == 1
    assert failed.run.step_retry_feedback is not None
    assert "transient planning-stage failure" in failed.run.step_retry_feedback
    assert len(failed.run.step_retry_events) == 1
    assert step.value in failed.run.step_retry_events[0]
    assert failed.run.outline_json == ready.run.outline_json

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.current_step == step
    assert persisted.step_retry_count == 1
    assert persisted.step_retry_events == failed.run.step_retry_events

    succeeded = restarted.advance_planning_run(run.id)

    assert succeeded.run.step_retry_count == 0
    assert succeeded.run.step_retry_feedback is None
    assert succeeded.run.step_retry_events == failed.run.step_retry_events
    if step in {
        PlanningRunStep.SCRIPT_VERIFIER,
        PlanningRunStep.SCRIPT_CRITIC,
    }:
        assert succeeded.run.status == PlanningRunStatus.NEEDS_ATTENTION
        assert succeeded.run.current_step == step
        assert succeeded.run.review_report is not None
        assert succeeded.run.review_report.requires_revision is True
        assert step not in succeeded.run.completed_steps
    else:
        assert succeeded.run.status == PlanningRunStatus.RUNNING
        assert succeeded.run.current_step != step


@pytest.mark.parametrize("failure_mode", ["timeout", "structure"])
def test_scene_revision_failure_retries_the_same_scene_before_advancing(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    request = _request()
    language = RetryOnceSceneContentClient(
        request,
        revision_failure=failure_mode,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    queued = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    assert queued.run.next_revision_scene_index == 0
    assert queued.run.revision_scene_sequences[0] == 1
    outline_before = queued.run.outline_json

    failed = service.advance_planning_run(run.id)

    assert failed.run.status == PlanningRunStatus.RUNNING
    assert failed.run.current_step == PlanningRunStep.SCENE_REVISION
    assert failed.run.next_revision_scene_index == 0
    assert failed.run.revision_changed_scene_sequences == []
    assert failed.run.outline_json == outline_before
    assert failed.run.revision_scene_retry_count == 1
    assert failed.run.revision_scene_retry_feedback is not None
    feedback = failed.run.revision_scene_retry_feedback
    assert any(
        "下次 planning.advance 将重试同一场" in warning
        for warning in failed.run.warnings
    )

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.next_revision_scene_index == 0
    assert persisted.revision_scene_retry_feedback == feedback
    assert persisted.revision_scene_retry_count == 1

    succeeded = restarted.advance_planning_run(run.id)

    assert succeeded.run.current_step == PlanningRunStep.SCENE_REVISION
    assert succeeded.run.next_revision_scene_index == 1
    assert succeeded.run.revision_changed_scene_sequences == [1]
    assert succeeded.run.revision_scene_retry_feedback is None
    assert succeeded.run.revision_scene_retry_count == 0
    assert language.local_revision_inputs[-1]["previous_retry_feedback"] == feedback


def test_scene_duration_reallocation_keeps_agent_selected_approximate_targets(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = DurationReallocationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    queued = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    assert queued.run.revision_scene_sequences == [1, len(language.outline.scenes)]
    original = EpisodeOutline.model_validate_json(queued.run.outline_json or "")

    decreased = service.advance_planning_run(run.id)

    assert decreased.run.current_step == PlanningRunStep.SCENE_REVISION
    assert decreased.run.next_revision_scene_index == 1
    after_decrease = EpisodeOutline.model_validate_json(
        decreased.run.outline_json or ""
    )
    assert after_decrease.scenes[0].target_duration_seconds == (
        original.scenes[0].target_duration_seconds - 6.0
    )
    assert sum(
        round(scene.target_duration_seconds * 1000)
        for scene in after_decrease.scenes
    ) == round(original.target_duration_seconds * 1000) - 6000
    first_constraint = language.duration_constraints[0]
    assert first_constraint["allow_change"] is True
    assert first_constraint["balance_episode_on_this_scene"] is False
    assert first_constraint["required_scene_target_seconds"] is None

    revised_second_scene = service.advance_planning_run(run.id)

    assert revised_second_scene.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert revised_second_scene.run.next_revision_scene_index == 2
    final_outline = EpisodeOutline.model_validate_json(
        revised_second_scene.run.outline_json or ""
    )
    assert final_outline.scenes[-1].target_duration_seconds == (
        original.scenes[-1].target_duration_seconds
    )
    assert sum(
        round(scene.target_duration_seconds * 1000)
        for scene in final_outline.scenes
    ) == round(final_outline.target_duration_seconds * 1000) - 6000
    final_constraint = language.duration_constraints[-1]
    assert final_constraint["allow_change"] is True
    assert final_constraint["balance_episode_on_this_scene"] is False
    assert final_constraint["required_scene_target_seconds"] is None
    assert any(
        "供 Verifier 判断" in warning
        for warning in revised_second_scene.run.warnings
    )


def test_large_intermediate_duration_drift_persists_and_revision_continues(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = DurationReallocationClient(
        request,
        # 40 seconds exceeds the removed 20% outline tolerance while keeping
        # the first scene's target positive (45 -> 5 seconds).
        first_scene_shift_seconds=40.0,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    queued = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    original = EpisodeOutline.model_validate_json(queued.run.outline_json or "")

    partially_revised = service.advance_planning_run(run.id)

    assert partially_revised.run.current_step == PlanningRunStep.SCENE_REVISION
    assert partially_revised.run.next_revision_scene_index == 1
    assert partially_revised.run.revision_scene_retry_count == 0
    intermediate = EpisodeOutline.model_validate_json(
        partially_revised.run.outline_json or ""
    )
    assert intermediate.scenes[0].target_duration_seconds == 5.0
    assert sum(scene.target_duration_seconds for scene in intermediate.scenes) == 140.0
    assert any("供 Verifier 判断" in warning for warning in partially_revised.run.warnings)

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.outline_json == partially_revised.run.outline_json
    assert persisted.next_revision_scene_index == 1

    continued = restarted.advance_planning_run(run.id)

    assert continued.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert continued.run.next_revision_scene_index == 2
    assert continued.run.revision_scene_retry_count == 0
    assert language.local_revision_calls == [1, len(original.scenes)]


def test_duration_drift_is_recorded_for_verifier_without_retrying_scene(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = DurationReallocationClient(
        request,
        fail_final_balance_once=True,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    decreased = service.advance_planning_run(run.id)
    accepted = service.advance_planning_run(run.id)

    assert accepted.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert accepted.run.next_revision_scene_index == 2
    assert accepted.run.revision_changed_scene_sequences == [
        1,
        len(language.outline.scenes),
    ]
    assert accepted.run.revision_scene_retry_count == 0
    assert accepted.run.revision_scene_retry_feedback is None
    assert any("供 Verifier 判断" in warning for warning in accepted.run.warnings)

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.current_step == PlanningRunStep.REVISION_VERIFIER
    assert persisted.next_revision_scene_index == 2
    assert persisted.revision_scene_retry_count == 0
    assert persisted.revision_scene_retry_feedback is None
    assert language.local_revision_calls == [1, len(language.outline.scenes)]
    final_outline = EpisodeOutline.model_validate_json(
        persisted.outline_json or ""
    )
    assert sum(
        round(scene.target_duration_seconds * 1000)
        for scene in final_outline.scenes
    ) == round(final_outline.target_duration_seconds * 1000) - 8000


def test_verifier_duration_reallocation_permission_survives_restart(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = VerifierDurationReallocationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)
    initial_outline = EpisodeOutline.model_validate_json(ready.run.outline_json or "")
    prior_queue = list(ready.run.revision_scene_sequences)
    prior_cursor = ready.run.next_revision_scene_index

    rejected = service.advance_planning_run(run.id)

    assert rejected.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert rejected.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert rejected.run.latest_revision_verification is not None
    assert rejected.run.latest_revision_verification.accepted is False
    assert rejected.run.latest_revision_verification.violations[
        0
    ].target_duration_change_required is True
    assert rejected.run.revision_scene_sequences == prior_queue
    assert rejected.run.next_revision_scene_index == prior_cursor

    blocked = service.advance_planning_run(run.id)
    assert blocked.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert blocked.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert language.revision_verifier_calls == 1

    resolved = service.resolve_story_verification(
        rejected.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=(
            StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION
        ),
        rationale="时长冲突有精确场次证据，交由 Repair Leader 重新规划。",
    )
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION
    assert resolved.run.story_verification_resolutions[-1].revision_verification == (
        rejected.run.latest_revision_verification
    )

    restarted = _service(tmp_path, language)
    repair_planned = restarted.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert repair_planned.run.revision_scene_sequences == [
        1,
        len(language.outline.scenes),
    ]

    first_repair = restarted.advance_planning_run(run.id)
    assert first_repair.run.current_step == PlanningRunStep.SCENE_REVISION
    assert first_repair.run.next_revision_scene_index == 1

    decreased = first_repair
    decreased_outline = EpisodeOutline.model_validate_json(
        decreased.run.outline_json or ""
    )
    assert decreased_outline.scenes[0].target_duration_seconds == (
        initial_outline.scenes[0].target_duration_seconds - 6.0
    )

    restarted = _service(tmp_path, language)
    revised_second_scene = restarted.advance_planning_run(run.id)

    assert revised_second_scene.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert revised_second_scene.run.next_revision_scene_index == 2
    assert len(language.duration_constraints) == 2
    assert language.duration_constraints[-1]["balance_episode_on_this_scene"] is False
    assert language.duration_constraints[-1]["required_scene_target_seconds"] is None
    final_outline = EpisodeOutline.model_validate_json(
        revised_second_scene.run.outline_json or ""
    )
    assert sum(
        round(scene.target_duration_seconds * 1000)
        for scene in final_outline.scenes
    ) == round(final_outline.target_duration_seconds * 1000) - 6000


def test_leader_selected_revision_scenes_are_applied_one_per_advance(
    tmp_path: Path,
) -> None:
    request = _request()
    language = SceneCountRevisionClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run

    architect = _advance_to_step(service, run.id, PlanningRunStep.SCENE_WRITER)
    original_count = architect.run.total_scenes
    _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)

    repair_planned = service.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert language.revision_calls == 0
    assert language.local_revision_calls == []
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0

    queued = repair_planned
    expected_sequences = [1, original_count]
    assert queued.run.current_step == PlanningRunStep.SCENE_REVISION
    assert queued.run.revision_scene_sequences == expected_sequences
    assert queued.run.next_revision_scene_index == 0
    assert language.revision_scope_calls == 0
    assert language.revision_calls == 0

    current = queued
    for completed_count, expected_sequence in enumerate(expected_sequences, start=1):
        prior_call_count = len(language.local_revision_calls)
        current = service.advance_planning_run(run.id)
        assert len(language.local_revision_calls) == prior_call_count + 1
        assert language.local_revision_calls[-1] == expected_sequence
        assert current.run.next_revision_scene_index == completed_count
        assert current.run.revision_changed_scene_sequences == expected_sequences[
            :completed_count
        ]
        assert language.revision_calls == 0
        assert language.revision_verifier_calls == 0
        partially_revised = EpisodeOutline.model_validate_json(
            current.run.outline_json or ""
        )
        patched_sequences = [
            scene.sequence
            for scene in partially_revised.scenes
            if any(
                "（局部改稿）" in line.text
                for line in scene_dialogue_lines(partially_revised, scene)
            )
        ]
        assert patched_sequences == expected_sequences[:completed_count]
        expected_step = (
            PlanningRunStep.REVISION_VERIFIER
            if completed_count == len(expected_sequences)
            else PlanningRunStep.SCENE_REVISION
        )
        assert current.run.current_step == expected_step

    revised = service.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert revised.run.total_scenes == original_count
    assert len(EpisodeOutline.model_validate_json(revised.run.outline_json or "").scenes) == (
        original_count
    )
    assert language.revision_verifier_calls == 1
    assert language.revision_calls == 0
    assert language.final_cold_read_calls == 0


def test_legacy_story_metadata_revision_persists_without_rewriting_any_scene(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = MetadataReconciliationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    active_revision = _advance_to_step(
        service,
        run.id,
        PlanningRunStep.SCENE_REVISION,
    )
    assert active_revision.project is not None
    legacy_run = active_revision.run.model_copy(
        deep=True,
        update={"current_step": PlanningRunStep.STORY_METADATA_REVISION},
    )
    service.repository.save_planning_run_with_project(
        legacy_run,
        active_revision.project.document,
    )
    ready = service.get_planning_run(run.id)
    before = EpisodeOutline.model_validate_json(ready.run.outline_json or "")

    aligned = service.advance_planning_run(run.id)

    assert aligned.run.current_step == PlanningRunStep.REVISION_SCOPE
    assert language.metadata_revision_calls == 1
    assert aligned.run.latest_story_metadata_patch is not None
    after = EpisodeOutline.model_validate_json(aligned.run.outline_json or "")
    assert after.story_summary == language.reconciled_summary
    assert after.characters[0].appearance == language.reconciled_appearance
    assert after.characters[0].character_id == before.characters[0].character_id
    assert after.lines == before.lines
    assert after.scenes == before.scenes
    assert after.target_duration_seconds == before.target_duration_seconds

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.current_step == PlanningRunStep.REVISION_SCOPE
    assert persisted.outline_json == aligned.run.outline_json
    assert persisted.latest_story_metadata_patch == (
        aligned.run.latest_story_metadata_patch
    )


def test_empty_repair_scope_returns_to_story_architect_without_scope_agent(
    tmp_path: Path,
) -> None:
    request = _request()
    language = EmptyAffectedRevisionClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)

    repair_planned = service.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert repair_planned.run.revision_scene_sequences == []
    assert repair_planned.run.step_retry_feedback is not None
    feedback = json.loads(repair_planned.run.step_retry_feedback)
    assert feedback["requested_by"] == "STORY_REPAIR_LEADER"
    assert feedback["structural_actions"][0]["affected_scene_sequences"] == []
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.revision_calls == 0
    assert language.local_revision_calls == []

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.current_step == PlanningRunStep.STORY_ARCHITECT
    assert persisted.revision_scene_sequences == []
    assert persisted.step_retry_feedback == repair_planned.run.step_retry_feedback

    rearchitected = restarted.advance_planning_run(run.id)
    assert rearchitected.run.current_step == PlanningRunStep.SCENE_WRITER
    assert rearchitected.run.step_retry_feedback is None
    assert rearchitected.run.scene_script_json == []
    assert rearchitected.run.next_script_scene_sequence == 1
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0


def test_structural_story_handoff_larger_than_retry_text_survives_restart(
    tmp_path: Path,
) -> None:
    request = _request()
    language = EmptyAffectedRevisionClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)

    repair_planned = service.advance_planning_run(run.id)
    assert repair_planned.project is not None
    assert repair_planned.run.step_retry_feedback is not None
    feedback = json.loads(repair_planned.run.step_retry_feedback)
    feedback["architect_context"] = "需要保留的结构语义。" * 220
    expanded_feedback = json.dumps(
        feedback,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(expanded_feedback) > 2000
    repair_planned.run.step_retry_feedback = expanded_feedback
    service.repository.save_planning_run_with_project(
        repair_planned.run,
        repair_planned.project.document,
    )

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id).run
    assert persisted.step_retry_feedback == expanded_feedback

    rearchitected = restarted.advance_planning_run(run.id)
    assert rearchitected.run.current_step == PlanningRunStep.SCENE_WRITER
    assert rearchitected.run.step_retry_feedback is None


def test_repair_planner_can_downgrade_unsupported_router_story_findings(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = CriticOverreachRepairClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    routed = _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)
    assert routed.run.source_contract is not None

    diagnosed = service.advance_planning_run(run.id)

    assert diagnosed.run.current_step == PlanningRunStep.FINALIZE
    assert language.repair_plan_calls == 1
    assert language.revision_calls == 0
    assert diagnosed.run.latest_repair_plan is not None
    assert all(
        not action.should_revise
        for action in diagnosed.run.latest_repair_plan.actions
    )
    assert diagnosed.run.review_disposition is not None
    assert (
        diagnosed.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
    )
    assert all(
        route.owner == ReviewRepairOwner.USER_OPTIONAL
        for route in diagnosed.run.review_disposition.issue_routes
    )
    assert any(
        "不强制改写剧本" in item
        for item in diagnosed.run.warnings
    )

    restarted_service = _service(tmp_path, language)
    restarted = restarted_service.get_planning_run(run.id)
    assert restarted.run.current_step == PlanningRunStep.FINALIZE
    assert restarted.run.review_disposition is not None
    assert (
        restarted.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
    )

    assert language.final_cold_read_calls == 0
    completed = restarted_service.advance_planning_run(run.id)
    assert completed.run.current_step == PlanningRunStep.COMPLETE
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.project is not None
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY


def test_legacy_max_revision_rounds_does_not_restore_a_count_based_critic_loop(
    tmp_path: Path,
) -> None:
    request = _request(260).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request, review_issue_counts=[1])
    service = _service(tmp_path, language)
    started = _start_owned_planning(service, request)
    assert started.run.max_revision_rounds is None
    assert started.project is not None
    assert started.project.document.project.stage == ProjectStage.INPUT
    assert all(
        version.approval_status == ApprovalStatus.DRAFT
        for version in started.project.document.script_versions
    )

    legacy_run = started.run.model_copy(update={"max_revision_rounds": 3})
    service.repository.save_planning_run_with_project(
        legacy_run,
        started.project.document,
    )
    completed = _finish(service, legacy_run.id, max_steps=140)

    reviews = service.repository.list_planning_review_snapshots(legacy_run.id)
    assert [len(snapshot.report.issues) for snapshot in reviews] == [1]
    assert completed.run.max_revision_rounds == 3
    assert completed.run.revision_round == 1
    assert language.critic_calls == 1
    assert language.repair_plan_calls == 1
    assert language.revision_verifier_calls == 1
    assert completed.run.latest_revision_verification is not None
    assert completed.run.latest_revision_verification.accepted is True
    assert completed.project is not None
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY
    assert (
        completed.project.document.script_versions[0].approval_status
        == ApprovalStatus.APPROVED
    )


def test_medium_and_low_story_findings_are_advisory_and_do_not_rewrite(
    tmp_path: Path,
) -> None:
    request = _request(300).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = AdvisoryStoryReviewClient(request)
    service = _service(tmp_path, language)

    completed = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert completed.run.review_report is not None
    assert [
        issue.severity for issue in completed.run.review_report.issues
    ] == [ScriptReviewSeverity.MEDIUM, ScriptReviewSeverity.LOW]
    assert completed.run.review_disposition is not None
    assert (
        completed.run.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
    )
    assert all(
        route.owner == ReviewRepairOwner.SCENE_DIRECTOR
        for route in completed.run.review_disposition.issue_routes
    )
    assert language.review_router_calls == 0
    assert language.source_contract_calls == 0
    assert language.repair_plan_calls == 0
    assert language.metadata_revision_calls == 0
    assert language.local_revision_calls == []
    assert any("可选修改建议" in warning for warning in completed.run.warnings)
    assert completed.project is not None
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY
    final_version = completed.project.document.script_versions[0]
    assert final_version.review_report == completed.run.review_report
    assert final_version.review_disposition == completed.run.review_disposition
    assert final_version.final_cold_read_report == completed.run.final_cold_read_report


def test_agent_revision_decision_is_not_overridden_by_severity_after_restart(
    tmp_path: Path,
) -> None:
    request = _request(300).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = AdvisoryStoryReviewClient(request)
    service = _service(tmp_path, language)
    ready = _advance_to_step(
        service,
        _start_owned_planning(service, request).run.id,
        PlanningRunStep.SCRIPT_VERIFIER,
    )
    assert ready.project is not None
    report = ScriptReviewReport(
        summary="Critic 认为这项台词问题会破坏当前场景。",
        issues=[
            ScriptReviewIssue(
                category="DIALOGUE_POLISH",
                severity=ScriptReviewSeverity.MEDIUM,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="台词可再精简。",
                recommendation="做一次局部台词修订。",
            )
        ],
        requires_revision=True,
    )
    legacy = ready.run.model_copy(
        update={
            "current_step": PlanningRunStep.STORY_REVISION,
            "review_report": report,
            "review_disposition": ScriptReviewDisposition(
                action=ScriptReviewDispositionAction.REVISE_STORY,
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=1,
                        owner=ReviewRepairOwner.STORY_ARCHITECT,
                        rationale="Critic 明确请求编剧层修订。",
                    )
                ],
                rationale="Agent 的语义修订决定。",
            ),
            "source_contract": StorySourceContract(
                explicit_commitments=[request.input_text or "生成测试短剧"],
            ),
        }
    )
    service.repository.save_planning_run_with_project(
        legacy,
        ready.project.document,
    )

    restarted = _service(tmp_path, language)
    repair_planned = restarted.advance_planning_run(legacy.id)

    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert repair_planned.run.review_disposition is not None
    assert (
        repair_planned.run.review_disposition.action
        == ScriptReviewDispositionAction.REVISE_STORY
    )
    assert repair_planned.run.latest_repair_plan is not None
    assert language.repair_plan_calls == 1


def test_planning_run_record_keeps_legacy_cap_but_allows_unbounded_counters(
    tmp_path: Path,
) -> None:
    request = _request(180)
    service = _service(tmp_path, StagedLanguageClient(request))
    started = _start_owned_planning(service, request)
    payload = started.run.model_dump(mode="python")
    payload.update(
        {
            "revision_round": 21,
            "revision_repair_round": 3,
            "max_revision_rounds": 3,
        }
    )

    restored_legacy = PlanningRunRecord.model_validate(payload)

    assert restored_legacy.revision_round == 21
    assert restored_legacy.revision_repair_round == 3
    assert restored_legacy.max_revision_rounds == 3
    payload.pop("max_revision_rounds")
    payload.pop("latest_revision_target_scene_sequences")
    payload.pop("latest_revision_changed_scene_sequences")
    restored_without_trace_history = PlanningRunRecord.model_validate(payload)
    assert restored_without_trace_history.max_revision_rounds is None
    assert restored_without_trace_history.latest_revision_target_scene_sequences == []
    assert restored_without_trace_history.latest_revision_changed_scene_sequences == []


def test_unresolved_story_review_cannot_finalize_an_approved_script(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    revising = _advance_to_step(service, run.id, PlanningRunStep.STORY_REVISION)
    assert revising.project is not None
    assert revising.run.review_report is not None
    forced_finalize = revising.run.model_copy(
        update={
            "current_step": PlanningRunStep.FINALIZE,
            # The explicit Router action remains authoritative even if a stale
            # persisted report flag says no revision is required.
            "review_report": revising.run.review_report.model_copy(
                update={"requires_revision": False}
            ),
        }
    )
    service.repository.save_planning_run_with_project(
        forced_finalize,
        revising.project.document,
    )

    guarded = service.advance_planning_run(run.id)

    assert guarded.run.status == PlanningRunStatus.RUNNING
    assert guarded.run.current_step == PlanningRunStep.STORY_REVISION
    assert guarded.project is not None
    assert guarded.project.document.project.stage == ProjectStage.INPUT
    assert all(
        version.approval_status == ApprovalStatus.DRAFT
        for version in guarded.project.document.script_versions
    )
    assert any(
        "FINALIZE 检测到未完成的 REVISE_STORY 审校" in warning
        for warning in guarded.run.warnings
    )


@pytest.mark.parametrize(
    "severity",
    [ScriptReviewSeverity.HIGH, ScriptReviewSeverity.MEDIUM],
)
def test_finalize_with_unrouted_review_returns_to_router(
    tmp_path: Path,
    severity: ScriptReviewSeverity,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)
    ready = _advance_to_step(
        service,
        _start_owned_planning(service, request).run.id,
        PlanningRunStep.SCRIPT_VERIFIER,
    )
    assert ready.project is not None
    high_review = ScriptReviewReport(
        summary="定稿前仍有一项未路由的阻断问题。",
        issues=[
            ScriptReviewIssue(
                category="CAUSAL_LOGIC",
                severity=severity,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="开场与结尾因果未闭合。",
                recommendation="由 Router 确认最小修复层。",
            )
        ],
        requires_revision=True,
    )
    forced_finalize = ready.run.model_copy(
        update={
            "current_step": PlanningRunStep.FINALIZE,
            "review_report": high_review,
            "review_disposition": None,
        }
    )
    service.repository.save_planning_run_with_project(
        forced_finalize,
        ready.project.document,
    )

    guarded = service.advance_planning_run(forced_finalize.id)

    assert guarded.run.current_step == PlanningRunStep.REVIEW_ROUTER
    assert guarded.run.status == PlanningRunStatus.RUNNING
    restored = _service(tmp_path, language).get_planning_run(forced_finalize.id)
    assert restored.run.current_step == PlanningRunStep.REVIEW_ROUTER
    assert restored.project is not None
    assert restored.project.document.project.stage == ProjectStage.INPUT

    routed = _service(tmp_path, language).advance_planning_run(forced_finalize.id)
    assert routed.run.current_step == PlanningRunStep.STORY_REVISION
    assert routed.run.review_disposition is not None
    assert (
        routed.run.review_disposition.action
        == ScriptReviewDispositionAction.REVISE_STORY
    )
    assert language.review_router_calls == 0
    snapshots = service.repository.list_planning_review_snapshots(
        forced_finalize.id
    )
    assert len(snapshots) == 1
    assert snapshots[0].report == high_review
    assert snapshots[0].disposition == routed.run.review_disposition


def test_enhanced_verifier_drives_focused_repair_without_final_cold_read(
    tmp_path: Path,
) -> None:
    class VerifierFindsOneResidualIssueClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            if "story-revision verifier Agent" in instructions:
                payload = json.loads(input_text)
                screenplay = "\n".join(
                    str(scene["screenplay_text"])
                    for scene in payload["audience_cold_read"][
                        "readable_final_screenplay"
                    ]
                )
                if "（局部改稿）（局部改稿）" not in screenplay:
                    accepted = StoryRevisionVerification.model_validate_json(output)
                    return accepted.model_copy(
                        update={
                            "accepted": False,
                            "summary": (
                                "Repair Plan 原问题已修复，但从头到尾自然冷读"
                                "发现末场的可见回收仍未落实。"
                            ),
                            "violations": [
                                StoryRevisionViolation(
                                    issue_id="ENDING-PAYOFF-RESIDUAL",
                                    category="ENDING_PAYOFF",
                                    scene_sequences=[len(self.outline.scenes)],
                                    observation=(
                                        "末场还没有用可见行动完成"
                                        "最后一步回收。"
                                    ),
                                    repair_instruction=(
                                        "保留已有因果和已成立的局部修改，"
                                        "只在末场补齐可见回收动作。"
                                    ),
                                )
                            ],
                            "followup_rationale": (
                                "全剧表演时长可由目标时长承载；"
                                "只需聚焦返修末场回收。"
                            ),
                        }
                    ).model_dump_json()
            return output

    request = _request(180)
    language = VerifierFindsOneResidualIssueClient(
        request,
        revision_rounds_required=1,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)
    prior_queue = list(ready.run.revision_scene_sequences)
    prior_cursor = ready.run.next_revision_scene_index

    rejected = service.advance_planning_run(run.id)

    assert rejected.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert rejected.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert rejected.run.latest_revision_verification is not None
    assert rejected.run.latest_revision_verification.accepted is False
    assert rejected.run.revision_scene_sequences == prior_queue
    assert rejected.run.next_revision_scene_index == prior_cursor
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    rejected_verification = rejected.run.latest_revision_verification.model_dump(
        mode="json"
    )
    residual_entry = next(
        entry
        for entry in rejected.run.issue_ledger
        if entry.issue_id == "ENDING-PAYOFF-RESIDUAL"
    )
    assert residual_entry.state == "Verifier 已定位，等待聚焦修订"
    assert "末场还没有用可见行动" in residual_entry.observation
    assert any(
        entry.state
        == "原问题的定向修复已获确认，终稿通读发现具体交付问题，继续聚焦返修"
        for entry in rejected.run.issue_ledger
    )

    resolved = service.resolve_story_verification(
        rejected.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=(
            StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION
        ),
        rationale="末场回收是可定位的剧本问题，交回 Repair Leader 选择最小范围。",
    )
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION

    response = resolved
    for _ in range(80):
        response = service.advance_planning_run(run.id)
        if (
            response.run.revision_round == 1
            and response.run.current_step == PlanningRunStep.SCENE_DIRECTOR
        ):
            break
    else:
        raise AssertionError("planning run did not complete focused verifier repair")

    assert response.run.latest_revision_verification is not None
    assert response.run.latest_revision_verification.accepted is True
    assert "ENDING-PAYOFF-RESIDUAL" in (
        response.run.latest_revision_verification.satisfied_issue_ids
    )
    assert response.run.latest_revision_verification.followup_rationale
    assert language.critic_calls == 1
    assert language.revision_verifier_calls == 2
    assert language.final_cold_read_calls == 0
    assert language.repair_plan_calls == 2
    assert language.repair_plan_inputs[0]["prior_revision_history"] == []
    local_instruction = json.loads(
        language.local_revision_inputs[-1]["user_instruction"]
    )
    assert local_instruction["invariants"] == [
        "保留已经成立的悬念推进"
    ]
    assert "不得替剧本辩护观众看不到的因果" in local_instruction[
        "authority_order"
    ]
    leader_handoff = json.loads(
        language.repair_plan_inputs[-1]["previous_structural_feedback"]
    )
    assert leader_handoff["story_verification_resolution"][
        "revision_verification"
    ] == rejected_verification
    verifier_ledger = language.revision_verifier_inputs[-1]["issue_ledger"]
    assert any(
        entry["issue_id"] == "ENDING-PAYOFF-RESIDUAL"
        for entry in verifier_ledger
    )
    resolved_residual = next(
        entry
        for entry in response.run.issue_ledger
        if entry.issue_id == "ENDING-PAYOFF-RESIDUAL"
    )
    assert "Verifier 确认" in resolved_residual.state
    assert resolved_residual.fix_evidence
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert snapshots[-1].repair_plan is not None


def test_filtered_story_repair_keeps_critic_issue_id_through_verifier_and_ledger(
    tmp_path: Path,
) -> None:
    class MixedOwnerReviewClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            if "Script Verifier Agent" in instructions:
                self.critic_calls += 1
                return ScriptReviewReport(
                    summary="一项下游建议和一项剧本问题。",
                    strengths=["主线清晰"],
                    issues=[
                        ScriptReviewIssue(
                            issue_id="DOWNSTREAM-1",
                            category="SHOT_CLARITY",
                            severity=ScriptReviewSeverity.MEDIUM,
                            owner=ReviewRepairOwner.SCENE_DIRECTOR,
                            scene_sequences=[1],
                            observation="下游可进一步明确空间调度。",
                            recommendation="在场景导演阶段处理。",
                        ),
                        ScriptReviewIssue(
                            issue_id="STORY-2",
                            category="CAUSAL_LOGIC",
                            severity=ScriptReviewSeverity.HIGH,
                            owner=ReviewRepairOwner.STORY_ARCHITECT,
                            scene_sequences=[1, len(self.outline.scenes)],
                            observation="开场因果尚未在结尾回收。",
                            recommendation="聚焦修复开场与结尾的因果。",
                        ),
                    ],
                    performance_timing_assessment="当前时长可承载聚焦修订。",
                    duration_fit_assessment=_aligned_duration_fit_assessment(
                        self.request
                    ),
                    requires_revision=True,
                ).model_dump_json()
            return super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )

    request = _request(180)
    language = MixedOwnerReviewClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)

    assert ready.run.latest_repair_plan is not None
    assert [
        (action.issue_id, action.issue_index)
        for action in ready.run.latest_repair_plan.actions
    ] == [("STORY-2", 1)]

    accepted = service.advance_planning_run(run.id)

    assert accepted.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    verifier_review = language.revision_verifier_inputs[-1][
        "routed_story_review"
    ]
    assert [issue["issue_id"] for issue in verifier_review["issues"]] == [
        "STORY-2"
    ]
    story_entry = next(
        entry for entry in accepted.run.issue_ledger if entry.issue_id == "STORY-2"
    )
    downstream_entry = next(
        entry
        for entry in accepted.run.issue_ledger
        if entry.issue_id == "DOWNSTREAM-1"
    )
    assert "Verifier 确认" in story_entry.state
    assert "Verifier" not in downstream_entry.state


def test_source_contract_is_precompiled_and_reaches_repair_writer_and_verifier(
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
    ready_for_repair = _advance_to_step(
        service,
        run.id,
        PlanningRunStep.STORY_REVISION,
    )
    assert ready_for_repair.run.source_contract is not None
    assert ready_for_repair.run.source_contract.explicit_commitments == [source]
    assert language.source_contract_calls == 0
    assert language.repair_plan_calls == 0

    repair_planned = service.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert language.repair_plan_calls == 1
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    queued = repair_planned
    assert language.local_revision_calls == []
    assert language.revision_verifier_calls == 0

    current = queued
    while current.run.current_step == PlanningRunStep.SCENE_REVISION:
        prior_calls = len(language.local_revision_calls)
        current = service.advance_planning_run(run.id)
        assert len(language.local_revision_calls) == prior_calls + 1
        assert language.revision_verifier_calls == 0
    assert current.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert language.revision_verifier_calls == 0

    revised = service.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert language.revision_verifier_calls == 1
    assert language.final_cold_read_calls == 0
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


def test_critic_json_structure_retries_the_same_semantic_step_without_losing_state(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = MalformedCriticJsonClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)

    invalid = service.advance_planning_run(run.id)

    assert invalid.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert invalid.run.review_report is None
    assert invalid.run.review_disposition is None
    assert invalid.run.step_retry_count == 1
    assert invalid.run.step_retry_feedback is not None
    assert any(
        "保留当前游标，下次 planning.advance 重试同一步骤" in item
        for item in invalid.run.step_retry_events
    )

    reviewed = service.advance_planning_run(run.id)
    assert reviewed.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert reviewed.run.review_report is not None
    assert reviewed.run.review_disposition is not None
    assert reviewed.run.step_retry_count == 0
    assert reviewed.run.step_retry_feedback is None
    assert language.critic_calls == 1
    assert language.review_router_calls == 0
    assert language.final_cold_read_calls == 0


def test_initial_script_verifier_revision_waits_for_agent_run_leader(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)

    handed_back = service.advance_planning_run(run.id)

    assert handed_back.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert handed_back.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert handed_back.run.review_report is not None
    assert handed_back.run.review_report.requires_revision is True
    exact_report = handed_back.run.review_report.model_copy(deep=True)
    assert handed_back.run.review_disposition is None
    assert language.critic_calls == 1
    assert language.repair_plan_calls == 0
    assert language.local_revision_calls == []

    blocked = service.advance_planning_run(run.id)
    assert blocked.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert blocked.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert blocked.run.review_report == exact_report
    assert language.critic_calls == 1
    assert language.repair_plan_calls == 0

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run.id)
    assert persisted.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert persisted.run.review_report == exact_report

    resolved = restarted.resolve_story_verification(
        handed_back.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION,
        rationale=(
            "首轮 Verifier 定位了首尾因果闭环，"
            "交给 Story Repair Leader 重新判断最小修订范围。"
        ),
    )

    assert resolved.run.status == PlanningRunStatus.RUNNING
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION
    assert resolved.run.review_disposition is not None
    resolution = resolved.run.story_verification_resolutions[-1]
    assert resolution.source == StoryVerificationSource.SCRIPT_VERIFIER
    assert resolution.script_review_report == exact_report
    assert resolution.revision_verification is None
    assert language.repair_plan_calls == 0
    assert language.local_revision_calls == []

    repair_planned = restarted.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert language.repair_plan_calls == 1


def test_initial_script_verifier_can_return_to_story_architect_without_stale_block(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request, revision_rounds_required=1)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)
    handed_back = service.advance_planning_run(run.id)
    exact_report = handed_back.run.review_report
    assert exact_report is not None

    resolved = service.resolve_story_verification(
        handed_back.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.RETURN_TO_STORY_ARCHITECT,
        rationale="该因果缺口来自整体结构，需要 Story Architect 重新组织场次。",
    )

    assert resolved.run.status == PlanningRunStatus.RUNNING
    assert resolved.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert resolved.run.story_verification_resolutions[-1].script_review_report == (
        exact_report
    )
    rearchitected = service.advance_planning_run(run.id)
    assert rearchitected.run.status == PlanningRunStatus.RUNNING
    assert rearchitected.run.current_step == PlanningRunStep.SCENE_WRITER
    assert rearchitected.run.error_message is None


def test_target_led_outside_duration_from_initial_verifier_waits_for_leader(
    tmp_path: Path,
) -> None:
    class OutsideDurationClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            if "Script Verifier Agent" in instructions:
                self.critic_calls += 1
                return ScriptReviewReport(
                    summary="剧情成立，但自然表演时长超出目标契约范围。",
                    strengths=["核心因果和回报完整"],
                    issues=[],
                    performance_timing_assessment="慢点来自中段规则解释。",
                    duration_fit_assessment=_outside_duration_fit_assessment(
                        self.request
                    ),
                    requires_revision=False,
                ).model_dump_json()
            return super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )

    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = OutsideDurationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)

    handed_back = service.advance_planning_run(run.id)

    assert handed_back.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert handed_back.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert handed_back.run.review_report is not None
    assert handed_back.run.review_report.requires_revision is False
    assert (
        handed_back.run.review_report.duration_fit_assessment is not None
        and handed_back.run.review_report.duration_fit_assessment.alignment
        == DurationAlignment.OUTSIDE_ACCEPTED_RANGE
    )
    exact_report = handed_back.run.review_report.model_copy(deep=True)
    assert language.critic_calls == 1
    blocked = service.advance_planning_run(run.id)
    assert blocked.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert language.critic_calls == 1

    resolved = service.resolve_story_verification(
        handed_back.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.ACCEPT_TRADEOFF,
        rationale="当前剧情回报优先，明确接受这次目标时长取舍。",
    )
    assert resolved.run.current_step == PlanningRunStep.FINALIZE
    resolution = resolved.run.story_verification_resolutions[-1]
    assert resolution.source == StoryVerificationSource.SCRIPT_VERIFIER
    assert resolution.script_review_report == exact_report
    assert any(
        entry.semantic_cluster == "duration-contract-outside-accepted-range"
        and entry.accepted_tradeoff is not None
        for entry in resolved.run.issue_ledger
    )


@pytest.mark.parametrize(
    ("authority", "resolution"),
    [
        (
            DurationAuthority.SOURCE_LED,
            DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
        ),
        (
            DurationAuthority.AUDIO_LED,
            DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
        ),
    ],
)
def test_non_target_led_outside_duration_does_not_force_initial_leader_handoff(
    tmp_path: Path,
    authority: DurationAuthority,
    resolution: DurationMismatchResolution,
) -> None:
    class OutsideDurationClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            if "Script Verifier Agent" in instructions:
                self.critic_calls += 1
                assessment = _outside_duration_fit_assessment(self.request).model_copy(
                    update={"resolution": resolution}
                )
                return ScriptReviewReport(
                    summary="剧情可交付，自然表演时长仅作契约对照。",
                    strengths=["来源或主音频权威已明确"],
                    issues=[],
                    performance_timing_assessment="保留当前自然表演。",
                    duration_fit_assessment=assessment,
                    requires_revision=False,
                ).model_dump_json()
            return super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )

    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = OutsideDurationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)
    ready = _replace_duration_authority_for_test(service, ready, authority)

    reviewed = service.advance_planning_run(run.id)

    assert reviewed.run.status == PlanningRunStatus.RUNNING
    assert reviewed.run.current_step == PlanningRunStep.FINALIZE
    assert reviewed.run.review_report is not None
    assert reviewed.run.review_report.requires_revision is False
    assert reviewed.run.story_verification_resolutions == []


def test_missing_script_verifier_duration_assessment_waits_for_leader_resolution(
    tmp_path: Path,
) -> None:
    class MissingDurationAssessmentClient(StagedLanguageClient):
        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            if "Script Verifier Agent" in instructions:
                report = ScriptReviewReport.model_validate_json(output)
                return report.model_copy(
                    update={"duration_fit_assessment": None}
                ).model_dump_json()
            return output

    request = _request(120).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = MissingDurationAssessmentClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    _advance_to_step(service, run.id, PlanningRunStep.SCRIPT_VERIFIER)

    handed_back = service.advance_planning_run(run.id)

    assert handed_back.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert handed_back.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert handed_back.run.review_report is not None
    assessment = handed_back.run.review_report.duration_fit_assessment
    assert assessment is not None
    assert assessment.alignment == DurationAlignment.UNRESOLVED
    critic_calls = language.critic_calls

    blocked = service.advance_planning_run(run.id)
    assert blocked.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert blocked.run.current_step == PlanningRunStep.SCRIPT_VERIFIER
    assert language.critic_calls == critic_calls

    resolved = service.resolve_story_verification(
        handed_back.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.ACCEPT_TRADEOFF,
        rationale="当前剧本质量可交付，保留未解析的时长判断供后续重新评估。",
    )

    assert resolved.run.current_step == PlanningRunStep.FINALIZE
    assert resolved.run.review_report is not None
    assert resolved.run.review_report.duration_fit_assessment is not None
    assert (
        resolved.run.review_report.duration_fit_assessment.alignment
        == DurationAlignment.UNRESOLVED
    )
    resolution = resolved.run.story_verification_resolutions[-1]
    assert resolution.script_review_report == handed_back.run.review_report
    assert resolution.decision == StoryVerificationResolutionDecision.ACCEPT_TRADEOFF
    assert any(entry.accepted_tradeoff for entry in resolved.run.issue_ledger)
    assert _finish(service, run.id, max_steps=3).run.status == PlanningRunStatus.COMPLETE


def test_leader_can_accept_rejected_revision_tradeoff_without_faking_acceptance(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = RevisionRegressionClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)
    prior_writer_calls = list(language.local_revision_calls)

    rejected = service.advance_planning_run(run.id)
    assert rejected.run.latest_revision_verification is not None
    assert rejected.run.latest_revision_verification.accepted is False

    resolved = service.resolve_story_verification(
        rejected.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.ACCEPT_TRADEOFF,
        rationale="当前运行允许保留这一可见状态取舍，后续仍可从记录重开修订。",
    )

    assert resolved.run.current_step == PlanningRunStep.FINALIZE
    assert resolved.run.latest_revision_verification is not None
    assert resolved.run.latest_revision_verification.accepted is False
    resolution = resolved.run.story_verification_resolutions[-1]
    assert resolution.revision_verification == rejected.run.latest_revision_verification
    assert resolution.decision == StoryVerificationResolutionDecision.ACCEPT_TRADEOFF
    violation_entry = next(
        entry
        for entry in resolved.run.issue_ledger
        if entry.issue_id == "RESOURCE-STATE-REGRESSION"
    )
    assert violation_entry.accepted_tradeoff is not None
    assert language.local_revision_calls == prior_writer_calls
    completed = _finish(service, ready.run.id, max_steps=3)
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.latest_revision_verification is not None
    assert completed.run.latest_revision_verification.accepted is False


def test_target_led_outside_duration_from_accepted_revision_verifier_waits_for_leader(
    tmp_path: Path,
) -> None:
    class AcceptedOutsideDurationClient(StagedLanguageClient):
        def __init__(self, request: CreateProjectRequest) -> None:
            super().__init__(request, revision_rounds_required=1)

        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            if "story-revision verifier Agent" not in instructions:
                return output
            verification = StoryRevisionVerification.model_validate_json(output)
            return verification.model_copy(
                update={
                    "duration_fit_assessment": _outside_duration_fit_assessment(
                        self.request
                    )
                }
            ).model_dump_json()

    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = AcceptedOutsideDurationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)

    handed_back = service.advance_planning_run(run.id)

    assert handed_back.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert handed_back.run.current_step == PlanningRunStep.REVISION_VERIFIER
    verification = handed_back.run.latest_revision_verification
    assert verification is not None
    assert verification.accepted is True
    assert (
        verification.duration_fit_assessment is not None
        and verification.duration_fit_assessment.alignment
        == DurationAlignment.OUTSIDE_ACCEPTED_RANGE
    )
    exact_verification = verification.model_copy(deep=True)

    resolved = service.resolve_story_verification(
        handed_back.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=StoryVerificationResolutionDecision.ACCEPT_TRADEOFF,
        rationale="终稿故事回报成立，明确接受这次 TARGET_LED 时长取舍。",
    )
    assert resolved.run.current_step == PlanningRunStep.FINALIZE
    assert resolved.run.latest_revision_verification == exact_verification
    resolution = resolved.run.story_verification_resolutions[-1]
    assert resolution.source == StoryVerificationSource.REVISION_VERIFIER
    assert resolution.revision_verification == exact_verification


@pytest.mark.parametrize(
    ("authority", "resolution"),
    [
        (
            DurationAuthority.SOURCE_LED,
            DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
        ),
        (
            DurationAuthority.AUDIO_LED,
            DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
        ),
    ],
)
def test_non_target_led_outside_duration_does_not_reject_revision_verifier(
    tmp_path: Path,
    authority: DurationAuthority,
    resolution: DurationMismatchResolution,
) -> None:
    class AcceptedOutsideDurationClient(StagedLanguageClient):
        def __init__(self, request: CreateProjectRequest) -> None:
            super().__init__(request, revision_rounds_required=1)

        def generate_text(
            self,
            *,
            instructions: str,
            input_text: str,
            image_urls: list[str] | None = None,
            max_output_tokens: int = 2200,
        ) -> str:
            output = super().generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=image_urls,
                max_output_tokens=max_output_tokens,
            )
            if "story-revision verifier Agent" not in instructions:
                return output
            verification = StoryRevisionVerification.model_validate_json(output)
            assessment = _outside_duration_fit_assessment(self.request).model_copy(
                update={"resolution": resolution}
            )
            return verification.model_copy(
                update={"duration_fit_assessment": assessment}
            ).model_dump_json()

    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = AcceptedOutsideDurationClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    ready = _advance_to_step(service, run.id, PlanningRunStep.REVISION_VERIFIER)
    ready = _replace_duration_authority_for_test(service, ready, authority)

    verified = service.advance_planning_run(run.id)

    assert verified.run.status == PlanningRunStatus.RUNNING
    assert verified.run.current_step == PlanningRunStep.FINALIZE
    assert verified.run.latest_revision_verification is not None
    assert verified.run.latest_revision_verification.accepted is True


def test_revision_verifier_triggers_one_targeted_writer_repair_before_acceptance(
    tmp_path: Path,
) -> None:
    request = _request(180)
    language = RevisionRegressionClient(request)
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    queued = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    assert queued.run.next_revision_scene_index == 0
    assert language.revision_verifier_calls == 0

    current = queued
    while current.run.current_step == PlanningRunStep.SCENE_REVISION:
        prior_calls = len(language.local_revision_calls)
        current = service.advance_planning_run(run.id)
        assert len(language.local_revision_calls) == prior_calls + 1
        assert language.revision_verifier_calls == 0
    assert current.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert language.local_revision_calls == [1, len(language.outline.scenes)]
    prior_queue = list(current.run.revision_scene_sequences)
    prior_cursor = current.run.next_revision_scene_index

    rejected = service.advance_planning_run(run.id)
    assert rejected.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert rejected.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert rejected.run.revision_scene_sequences == prior_queue
    assert rejected.run.next_revision_scene_index == prior_cursor
    assert language.revision_verifier_calls == 1
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.local_revision_calls == [1, len(language.outline.scenes)]

    resolved = service.resolve_story_verification(
        rejected.run.project_id,
        run_id=run.id,
        owner_agent_run_id=run.owner_agent_run_id,
        decision=(
            StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION
        ),
        rationale="供电状态回归可定位于第 1 场，让 Repair Leader 重新定界。",
    )
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION

    repair_planned = service.advance_planning_run(run.id)
    assert repair_planned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert repair_planned.run.revision_scene_sequences == [1]

    repaired = service.advance_planning_run(run.id)
    assert repaired.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert repaired.run.next_revision_scene_index == 1
    assert language.local_revision_calls == [1, len(language.outline.scenes), 1]
    assert language.revision_verifier_calls == 1

    revised = service.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert language.revision_verifier_calls == 2
    assert language.targeted_repair_calls == 0
    assert language.local_revision_calls.count(1) == 2
    assert revised.run.latest_revision_verification is not None
    assert revised.run.latest_revision_verification.accepted is True
    assert (
        revised.run.latest_revision_verification.recommended_followup
        == StoryReviewFollowup.FINALIZE
    )
    assert language.final_cold_read_calls == 0
    assert len(
        [
            resolution
            for resolution in revised.run.story_verification_resolutions
            if resolution.source == StoryVerificationSource.REVISION_VERIFIER
        ]
    ) == 1
    snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert snapshots[-1].revision_verification is not None
    assert snapshots[-1].revision_verification.accepted is True


def test_revision_verifier_rejections_persist_and_continue_until_accepted(
    tmp_path: Path,
) -> None:
    request = _request(180).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = RepeatedRevisionRegressionClient(
        request,
        rejections_before_acceptance=3,
    )
    service = _service(tmp_path, language)
    run = _start_owned_planning(service, request).run
    current = _advance_to_step(service, run.id, PlanningRunStep.SCENE_REVISION)
    while current.run.current_step == PlanningRunStep.SCENE_REVISION:
        current = service.advance_planning_run(run.id)
    assert current.run.current_step == PlanningRunStep.REVISION_VERIFIER

    for repair_round in range(1, 4):
        prior_queue = list(current.run.revision_scene_sequences)
        prior_cursor = current.run.next_revision_scene_index
        rejected = service.advance_planning_run(run.id)
        assert rejected.run.status == PlanningRunStatus.NEEDS_ATTENTION
        assert rejected.run.current_step == PlanningRunStep.REVISION_VERIFIER
        assert rejected.run.revision_scene_sequences == prior_queue
        assert rejected.run.next_revision_scene_index == prior_cursor
        assert rejected.run.latest_revision_verification is not None
        assert rejected.run.latest_revision_verification.accepted is False
        assert rejected.project is not None
        assert rejected.project.document.project.stage == ProjectStage.INPUT
        assert all(
            version.approval_status == ApprovalStatus.DRAFT
            for version in rejected.project.document.script_versions
        )

        service = _service(tmp_path, language)
        persisted = service.get_planning_run(run.id)
        assert persisted.run.status == PlanningRunStatus.NEEDS_ATTENTION
        assert persisted.run.current_step == PlanningRunStep.REVISION_VERIFIER
        assert persisted.run.latest_revision_verification is not None
        assert persisted.run.latest_revision_verification.accepted is False

        resolved = service.resolve_story_verification(
            rejected.run.project_id,
            run_id=run.id,
            owner_agent_run_id=run.owner_agent_run_id,
            decision=(
                StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION
            ),
            rationale=(
                f"第 {repair_round} 次 Verifier handoff 仍是第 1 场的精确时序问题，"
                "继续交给 Repair Leader 语义决定。"
            ),
        )
        assert resolved.run.current_step == PlanningRunStep.STORY_REVISION
        planned = service.advance_planning_run(run.id)
        assert planned.run.current_step == PlanningRunStep.SCENE_REVISION
        assert planned.run.revision_scene_sequences == [1]

        repaired_scene = service.advance_planning_run(run.id)
        assert repaired_scene.run.current_step == PlanningRunStep.REVISION_VERIFIER
        current = repaired_scene

    accepted = service.advance_planning_run(run.id)
    assert accepted.run.current_step == PlanningRunStep.FINALIZE
    assert accepted.run.latest_revision_verification is not None
    assert accepted.run.latest_revision_verification.accepted is True
    assert (
        accepted.run.latest_revision_verification.recommended_followup
        == StoryReviewFollowup.FINALIZE
    )
    assert language.revision_verifier_calls == 4
    assert language.metadata_revision_calls == 0
    assert language.revision_scope_calls == 0
    assert language.final_cold_read_calls == 0
    assert len(
        [
            resolution
            for resolution in accepted.run.story_verification_resolutions
            if resolution.source == StoryVerificationSource.REVISION_VERIFIER
        ]
    ) == 3
    assert not any("回退原稿" in warning for warning in accepted.run.warnings)
    accepted_snapshots = service.repository.list_planning_outline_snapshots(run.id)
    assert accepted_snapshots[-1].revision_verification is not None
    assert accepted_snapshots[-1].revision_verification.accepted is True
    assert accepted.project is not None
    assert accepted.project.document.project.stage == ProjectStage.INPUT
    assert all(
        version.approval_status == ApprovalStatus.DRAFT
        for version in accepted.project.document.script_versions
    )

    completed = _finish(service, run.id)
    assert completed.project is not None
    assert completed.project.document.project.stage == ProjectStage.SCRIPT_READY
    assert (
        completed.project.document.script_versions[0].approval_status
        == ApprovalStatus.APPROVED
    )


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


def test_issue_count_regression_does_not_insert_a_count_based_judge(
    tmp_path: Path,
) -> None:
    request = _request(260)
    language = HandoffRegressionClient(
        request,
        revision_rounds_required=3,
        review_issue_counts=[4, 2, 1, 3],
    )
    service = _service(tmp_path, language)
    run_id = _start_owned_planning(service, request).run.id
    response = _advance_to_step(
        service,
        run_id,
        PlanningRunStep.SCENE_DIRECTOR,
    )
    assert response.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert response.run.directed_scene_json == []
    assert response.run.revision_judge is None
    assert language.judge_calls == 0
    assert language.final_cold_read_calls == 0

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(run_id)
    assert restored.run.revision_judge is None
    completed = _finish(restarted, run_id)

    reviews = restarted.repository.list_planning_review_snapshots(completed.run.id)
    assert [len(item.report.issues) for item in reviews] == [4]
    assert completed.run.revision_judge is None
    assert language.judge_calls == 0
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert PlanningRunStep.REVISION_JUDGE not in completed.run.completed_steps
    assert completed.run.outline_json is not None
    assert completed.run.revision_round == 1
    assert "（局部改稿）" in completed.run.outline_json
    assert any(
        "第 1 轮已逐场应用剧本 Patch" in item
        and "未调用整集重写" in item
        for item in completed.run.warnings
    )
    assert completed.project is not None
    assert restarted.repository.list_image_tasks(
        completed.project.document.project.id
    ) == []
    assert restarted.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []


def test_legacy_revision_judge_cursor_without_two_reviews_skips_provider(
    tmp_path: Path,
) -> None:
    request = _request(260)
    language = HandoffRegressionClient(
        request,
        revision_rounds_required=3,
        review_issue_counts=[4, 2, 1, 3],
        fail_judge=True,
    )
    service = _service(tmp_path, language)
    run_id = _start_owned_planning(service, request).run.id
    ready = _advance_to_step(
        service,
        run_id,
        PlanningRunStep.SCENE_DIRECTOR,
    )
    assert ready.project is not None
    legacy = ready.run.model_copy(
        update={"current_step": PlanningRunStep.REVISION_JUDGE}
    )
    service.repository.save_planning_run_with_project(
        legacy,
        ready.project.document,
    )

    judged = service.advance_planning_run(run_id)
    assert judged.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert judged.run.revision_judge is None
    assert language.judge_calls == 0
    assert language.final_cold_read_calls == 0
    assert any("缺少两轮审稿证据" in item for item in judged.run.warnings)

    completed = _finish(service, run_id)

    reviews = service.repository.list_planning_review_snapshots(completed.run.id)
    assert [len(item.report.issues) for item in reviews] == [4]
    assert completed.run.revision_judge is None
    assert language.judge_calls == 0
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.outline_json is not None
    assert completed.run.revision_round == 1
    assert "（局部改稿）" in completed.run.outline_json
    assert any("缺少两轮审稿证据" in item for item in completed.run.warnings)
