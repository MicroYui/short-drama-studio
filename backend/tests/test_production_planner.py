"""Planning regressions for structured locked-screenplay input."""

from __future__ import annotations

import json

import pytest

from short_drama.hierarchical_script import compose_scene_screenplay
from short_drama.models import (
    AudioKind,
    CreateProjectRequest,
    ProductionProfile,
    ProjectDocument,
    ReferenceRequirement,
    ReferenceRole,
    ReviewRepairOwner,
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
    StoryLockedDialogueLine,
    StorySourceContract,
    StoryRevisionViolation,
    StoryRevisionVerification,
)
from short_drama.production_planner import (
    PlannedCharacter,
    PlannedLocation,
    PlannedProp,
    PlannedReferenceNeed,
    PlannedShot,
    PlannedShotTimelineBeat,
    ProductionPlan,
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
    _fallback_plan,
    _final_cold_read_prompt,
    _compact_error,
    _canonical_scene_screenplay,
    _outline_prompt,
    _normalize_review_report,
    _retime_shots,
    _scene_director_prompt,
    _story_critic_prompt,
    _story_repair_plan_prompt,
    _story_revision_verifier_prompt,
    _validate_directed_scene_plan,
    _validate_story_revision_verification,
    build_project_document_from_plan,
    create_production_plan,
    direct_story_scene,
    normalize_story_repair_plan_issue_ids,
    normalize_story_revision_verification_issue_ids,
    orient_story,
    refresh_lightweight_reference_plans,
    story_revision_review,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_method_skill_catalog,
    production_skill,
    production_skill_catalog,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderError
from short_drama.script_documents import EpisodeScenePatch, scene_dialogue_lines


LOCKED_SCRIPT = """
《别相信我》
类型：真人都市悬疑短剧；普通话；雨夜便利店；约 45–60 秒。
角色：林夏，28 岁，短黑发，米白风衣，克制但警觉。周衡，32 岁，深灰西装，左眉有浅疤，表面平静。
核心道具：一支磨损红壳、无天线、无品牌文字的旧录音笔。

雨夜，便利店内。林夏与周衡隔着柜台对峙。
林夏：“你说他十点离开，可监控里，十点零三分他还在这里。”
周衡右手握着红色录音笔：“监控会骗人。”
林夏：“人也会。”
周衡把录音笔放到柜台，指示灯亮。录音笔中传出林夏的声音：“如果你听到这段话，别相信我。”
林夏听见自己的声音，神情动摇。
周衡：“现在，你还信自己吗？”
林夏左手拿起录音笔，转身离开便利店，走进雨夜。

要求：保留以上五句台词原文和说话人；规划为 8–10 个最终剪辑镜头。
""".strip()

LOCKED_DIALOGUE = [
    StoryLockedDialogueLine(
        speaker="林夏",
        text="你说他十点离开，可监控里，十点零三分他还在这里。",
    ),
    StoryLockedDialogueLine(speaker="周衡", text="监控会骗人。"),
    StoryLockedDialogueLine(speaker="林夏", text="人也会。"),
    StoryLockedDialogueLine(
        speaker="录音笔中的林夏",
        text="如果你听到这段话，别相信我。",
        audio_kind=AudioKind.VOICEOVER,
    ),
    StoryLockedDialogueLine(speaker="周衡", text="现在，你还信自己吗？"),
]


def test_planned_shot_dialogue_line_can_span_adjacent_timeline_beats() -> None:
    shot = PlannedShot(
        scene_sequence=1,
        duration_seconds=4.0,
        narrative_purpose="Deliver one two-part instruction.",
        visual="The principal stops the calculator and questions the witnesses.",
        framing="Medium close-up",
        camera="Locked, then a small pan to the witnesses.",
        audio_intent="One continuous spoken line with a short pause.",
        dialogue_line_indices=[1],
        timeline_beats=[
            PlannedShotTimelineBeat(
                start_seconds=0.0,
                end_seconds=1.8,
                visual="The principal raises one hand beside the calculator.",
                framing="Close-up",
                camera="Locked",
                action="He stops anyone from touching the calculator.",
                dialogue_line_indices=[1],
                sound="First sentence of the line.",
            ),
            PlannedShotTimelineBeat(
                start_seconds=1.8,
                end_seconds=4.0,
                visual="He turns from the calculator toward the witnesses.",
                framing="Relationship close-up",
                camera="Small pan",
                action="He asks who saw the calculation.",
                dialogue_line_indices=[1],
                sound="Second sentence of the same line.",
            ),
        ],
        character_positions="Principal at the counter; witnesses opposite him.",
        eyeline="Principal moves his gaze from calculator to witnesses.",
        prop_state_in="Calculator remains visible.",
        prop_state_out="Calculator remains untouched.",
        action_in="The principal has just arrived.",
        action_out="A witness is ready to answer.",
        transition_out="Cut to the witness response.",
        local_rework_risk="Keep the calculator reading stable.",
    )

    assert [beat.dialogue_line_indices for beat in shot.timeline_beats] == [[1], [1]]


def test_planned_characters_do_not_have_an_adult_only_content_gate() -> None:
    child = PlannedCharacter(
        name="小满",
        age=8,
        appearance="短发，神情机灵",
        wardrobe="黄色雨衣",
        performance="反应直接自然",
        voice_identity="child-voice",
    )
    elder = PlannedCharacter(
        name="周奶奶",
        age=103,
        appearance="银白短发，精神清醒",
        wardrobe="深蓝棉布外套",
        performance="沉稳有力",
        voice_identity="elder-voice",
    )

    assert child.age == 8
    assert elder.age == 103


def test_planned_asset_text_normalizes_outer_whitespace() -> None:
    character = PlannedCharacter(
        name=" 林夏 ",
        age=18,
        appearance=" 黑色碎发 ",
        wardrobe=" 白色校服 ",
        performance=" 清爽专注 ",
        voice_identity=" 青年男声 ",
    )
    location = PlannedLocation(
        name=" 食堂 ",
        layout=" 打饭窗口与就餐区 ",
        lighting=" 暖黄顶灯 ",
        palette=" 暖色 ",
        key_views=[" 食堂全景 ", " 食堂窗口 "],
    )

    assert character.name == "林夏"
    assert character.appearance == "黑色碎发"
    assert location.name == "食堂"
    assert location.key_views == ["食堂全景", "食堂窗口"]


def test_canonical_screenplay_preserves_semantically_written_scene() -> None:
    scene = _fallback_episode_outline(
        CreateProjectRequest(
            source_mode=SourceMode.IDEA_EXPANSION,
            production_profile=ProductionProfile.AI_SHORT_DRAMA,
            input_text="用可见行动表现一个人第一次公开赢得认可。",
            target_duration_seconds=30,
        )
    ).scenes[0].model_copy(
        update={
            "playable_actions": ["主角完成挑战，旁观者公开改变判断"],
            "screenplay_text": (
                "【教室 日 内】\n"
                "粉笔落下最后一笔。后排原本窃笑的人慢慢站直，"
                "把最前排的位置让了出来。"
            ),
        }
    )

    screenplay = _canonical_scene_screenplay(scene, [])

    assert screenplay == scene.screenplay_text
    assert scene.playable_actions[0] not in screenplay


def test_story_revision_review_follows_the_critics_semantic_revision_decision() -> None:
    review = ScriptReviewReport(
        summary="一项阻断问题和一项可选润色。",
        issues=[
            ScriptReviewIssue(
                category="CAUSAL_LOGIC",
                severity=ScriptReviewSeverity.HIGH,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="核心因果尚未闭合。",
                recommendation="修复因果闭环。",
            ),
            ScriptReviewIssue(
                category="DIALOGUE_POLISH",
                severity=ScriptReviewSeverity.MEDIUM,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[2],
                observation="一句台词可更精简。",
                recommendation="可选润色。",
            ),
        ],
        requires_revision=True,
    )
    disposition = ScriptReviewDisposition(
        action=ScriptReviewDispositionAction.REVISE_STORY,
        issue_routes=[
            ScriptReviewIssueRoute(
                issue_index=1,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                rationale="需要修改剧本因果。",
            ),
            ScriptReviewIssueRoute(
                issue_index=2,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                rationale="若用户需要可由编剧润色。",
            ),
        ],
        rationale="Critic 判断这一轮需要聚焦修订。",
    )

    blocking_review = story_revision_review(review, disposition)

    assert [issue.category for issue in blocking_review.issues] == [
        "CAUSAL_LOGIC",
        "DIALOGUE_POLISH",
    ]
    assert blocking_review.requires_revision is True


def test_review_report_retries_when_revision_decision_has_no_story_issue() -> None:
    contradictory = ScriptReviewReport(
        summary="剧本需要修改，但没有说明任何剧本文本问题。",
        issues=[],
        requires_revision=True,
    )

    with pytest.raises(
        ValueError,
        match="requires_revision=true without a STORY_ARCHITECT issue",
    ):
        _normalize_review_report(contradictory)


def test_review_report_accepts_missing_optional_timing_assessment() -> None:
    missing_timing_evidence = ScriptReviewReport(
        summary="剧本整体成立。",
        issues=[],
        requires_revision=False,
    )

    normalized = _normalize_review_report(missing_timing_evidence)

    assert normalized.performance_timing_assessment is None


class RepairingLanguageClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        self.request = request
        self.calls = 0

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
        del instructions, input_text, image_urls, max_output_tokens
        self.calls += 1
        payload = _fallback_plan(self.request).model_dump(mode="json")
        if self.calls == 1:
            payload["shots"][0]["dialogue_line_indices"] = [0]
        return json.dumps(payload, ensure_ascii=False)


class MultiAgentLanguageClient:
    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        fail_scene: int | None = None,
        critic_revision_rounds_required: int = 0,
        invalid_first_revision: bool = False,
    ) -> None:
        self.request = request
        self.fail_scene = fail_scene
        self.critic_revision_rounds_required = critic_revision_rounds_required
        self.invalid_first_revision = invalid_first_revision
        self.outline = _fallback_episode_outline(request)
        # Multi-Agent communication tests need an explicit multi-scene model result;
        # production fallback intentionally no longer invents that segmentation.
        outline_payload = self.outline.model_dump(mode="python")
        scene_template = self.outline.scenes[0]
        scene_duration = request.resolved_target_duration_seconds() / 3
        outline_payload["scenes"] = [
            scene_template.model_copy(
                deep=True,
                update={
                    "sequence": sequence,
                    "heading": f"Agent 规划场景 {sequence}",
                    "target_duration_seconds": scene_duration,
                    "dialogue_line_indices": [],
                },
            ).model_dump(mode="python")
            for sequence in range(1, 4)
        ]
        self.outline = type(self.outline).model_validate(outline_payload)
        blueprint_payload = self.outline.model_dump(mode="python")
        if request.source_mode != SourceMode.SCRIPT_LOCKED:
            blueprint_payload["lines"] = []
            for scene in blueprint_payload["scenes"]:
                scene["dialogue_line_indices"] = []
        self.blueprint = type(self.outline).model_validate(blueprint_payload)
        self.scene_inputs: list[dict[str, object]] = []
        self.calls = 0
        self.critic_calls = 0
        self.revision_calls = 0
        self.repair_plan_calls = 0
        self.revision_verifier_calls = 0
        self.repair_plan_inputs: list[dict[str, object]] = []
        self.revision_inputs: list[dict[str, object]] = []
        self.revision_verifier_inputs: list[dict[str, object]] = []
        self.scene_plans = {}
        handoff = self.outline.scenes[0].continuity_in.model_copy(deep=True)
        for scene in self.outline.scenes:
            plan = _fallback_directed_scene_plan(request, self.outline, scene, handoff)
            plan.shots[0].narrative_purpose = (
                f"Agent scene {scene.sequence}: " + plan.shots[0].narrative_purpose
            )
            plan.continuity_out.known_facts.append(f"agent-scene-{scene.sequence}-complete")
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
        self.calls += 1
        if "Script Verifier Agent" in instructions:
            self.critic_calls += 1
            requires_revision = self.critic_calls <= self.critic_revision_rounds_required
            return ScriptReviewReport(
                summary=(
                    "存在一处因果链缺口，需要 Story Architect 修订。"
                    if requires_revision
                    else "因果、动机和场次回收均可继续进入逐场规划。"
                ),
                strengths=["核心悬念沿场次连续推进"],
                issues=(
                    [
                        ScriptReviewIssue(
                            issue_id=f"CAUSAL-LOGIC-{self.critic_calls}",
                            category="CAUSAL_LOGIC",
                            severity=ScriptReviewSeverity.HIGH,
                            owner=ReviewRepairOwner.STORY_ARCHITECT,
                            scene_sequences=[1, len(self.outline.scenes)],
                            observation="结尾选择尚未完整解释开场事件如何成立。",
                            recommendation="明确世界规则或调整结尾选择，使因果闭合。",
                        )
                    ]
                    if requires_revision
                    else []
                ),
                performance_timing_assessment=(
                    "自然通读完整文本后，时长与本测试目标相称。"
                ),
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
        if "Leader 的返修分派步骤" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issues = payload["routed_story_review"]["issues"]
            return StoryRepairPlan(
                summary="分清来源概念缺口与当前大纲缺陷后再修订。",
                source_promise="保留开场异常、选择和结尾回收。",
                invariants=["核心悬念沿场次连续推进"],
                actions=[
                    StoryRepairAction(
                        issue_index=index,
                        issue_id=str(issue["issue_id"]),
                        diagnosis=StoryRepairDiagnosis.OUTLINE_DEFECT,
                        should_revise=True,
                        rationale="当前大纲未闭合该因果。",
                        affected_scene_sequences=list(issue["scene_sequences"]),
                        recommended_change="替换底层因果，而不是增加解释。",
                        remove_or_simplify=["重复证明"],
                        preserve=["核心悬念"],
                        validation_questions=["开场异常是否被结尾直接回收？"],
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
            return StoryRevisionVerification(
                accepted=True,
                summary="Writer 已落实 Repair Plan 且没有引入新矛盾。",
                satisfied_issue_ids=required_ids,
                satisfied_issue_indices=required_indices,
                violations=[],
            ).model_dump_json()
        if "revising an episode" in instructions:
            self.revision_calls += 1
            self.revision_inputs.append(json.loads(input_text))
            payload = self.outline.model_dump(mode="python")
            payload["story_summary"] += " 修订后明确了开场事件与结尾选择的因果关系。"
            valid = json.dumps(payload, ensure_ascii=False)
            if self.invalid_first_revision and self.revision_calls == 1:
                return valid[:-1] + ",}"
            return valid
        if "Scene Writer Agent" in instructions:
            payload = json.loads(input_text)
            sequence = int(payload["current_scene_node"]["sequence"])
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
        if "story-architect Agent" in instructions:
            return self.blueprint.model_dump_json()
        payload = json.loads(input_text)
        self.scene_inputs.append(payload)
        sequence = int(payload["scene_contract"]["sequence"])
        if sequence == self.fail_scene:
            raise LanguageProviderError(f"scene {sequence} unavailable")
        return self.scene_plans[sequence].model_dump_json()


class DownstreamRoutingClient(MultiAgentLanguageClient):
    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "You are the Review Router Agent inside" in instructions:
            payload = json.loads(input_text)
            issues = payload["script_review"]["issues"]
            return ScriptReviewDisposition(
                action=ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF,
                issue_routes=[
                    ScriptReviewIssueRoute(
                        issue_index=index,
                        owner=ReviewRepairOwner.SCENE_DIRECTOR,
                        rationale="问题属于场面调度，不应增加剧本解释。",
                    )
                    for index in range(1, len(issues) + 1)
                ],
                rationale="保留剧本并下放到导演层。",
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class SceneReferenceRepairClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        self.request = request
        self.outline = _fallback_episode_outline(request)
        self.calls = 0
        self.inputs: list[dict[str, object]] = []

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
        del instructions, image_urls, max_output_tokens
        self.calls += 1
        self.inputs.append(json.loads(input_text))
        scene = self.outline.scenes[0]
        plan = _fallback_directed_scene_plan(
            self.request,
            self.outline,
            scene,
            scene.continuity_in,
        )
        if self.calls == 1:
            plan.shots[0].reference_needs.append(
                PlannedReferenceNeed(
                    subject_name="未声明的临时道具",
                    acceptable_roles=[ReferenceRole.PROP_PRODUCT],
                    purpose="首次无效输出",
                )
            )
        return plan.model_dump_json()


class StoryOrientationClient:
    def __init__(
        self,
        *,
        skill_id: str | None = None,
        method_id: str | None = None,
    ) -> None:
        self.skill_id = skill_id
        self.method_id = method_id
        self.instructions = ""
        self.input_text = ""

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
        self.instructions = instructions
        self.input_text = input_text
        return json.dumps(
            {
                "source_contract": {
                    "explicit_commitments": ["所有人的数学水平倒退100倍，只有主角不变"],
                    "explicit_prohibitions": [],
                    "open_creative_choices": ["人物、地点和具体升级方式"],
                    "authoritative_user_overrides": [],
                    "continuation_preference": "AGENT_DECIDES",
                    "user_overridable": True,
                },
                "story_intent": {
                    "audience_experience": "快速感受主角因简单心算不断获得夸张认可的爽感。",
                    "dramatic_engine": "常识能力在新世界里变成稀缺天赋，并逐级放大公开反应。",
                    "progression_strategy": "从日常小事推进到更高影响，但每段都先给可见兑现。",
                    "tone_and_plausibility": "接受类型化夸张和无厘头反应，不要求现实制度解释。",
                    "protected_elements": ["主角数学水平不变"],
                    "drift_risks": ["不要写成严肃制度推演"],
                },
                "selected_narrative_skills": (
                    []
                    if self.skill_id is None
                    else [
                        {
                            "skill_id": self.skill_id,
                            "rationale": "测试未知叙事 Skill 的拒绝路径。",
                        }
                    ]
                ),
                "narrative_skill_decision": (
                    "当前 Narrative Skill Catalog 为空；"
                    "不使用本地自创替代。"
                ),
                "selected_method_skills": (
                    []
                    if self.method_id is None
                    else [
                        {
                            "skill_id": self.method_id,
                            "rationale": "项目需要该制片方法组织跨角色交接。",
                        }
                    ]
                ),
                "method_selection_rationale": (
                    "不额外加载制片方法。"
                    if self.method_id is None
                    else "当前项目需要该制片方法。"
                ),
            },
            ensure_ascii=False,
        )


class LockedStoryOrientationClient:
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
        del instructions, input_text, image_urls, max_output_tokens
        return json.dumps(
            {
                "source_contract": {
                    "explicit_commitments": ["五句台词及说话人保持不变"],
                    "explicit_prohibitions": [],
                    "open_creative_choices": ["场景调度与镜头设计"],
                    "authoritative_user_overrides": [],
                    "locked_dialogue": [
                        line.model_dump(mode="json") for line in LOCKED_DIALOGUE
                    ],
                    "continuation_preference": "AGENT_DECIDES",
                    "user_overridable": True,
                },
                "story_intent": {
                    "audience_experience": "让观众感到克制的现实悬疑。",
                    "dramatic_engine": "录音内容与人物当下选择形成信息差。",
                    "progression_strategy": "线索和不信任逐步加深。",
                    "tone_and_plausibility": "保持现实行为逻辑。",
                    "protected_elements": ["五句原始台词"],
                    "drift_risks": ["不要解释过度"],
                },
                "selected_narrative_skills": [],
                "narrative_skill_decision": (
                    "锁定悬疑文本已经给出明确发动机，当前不需要额外爽感或荒诞技能。"
                ),
            },
            ensure_ascii=False,
        )


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=50,
        input_text=LOCKED_SCRIPT,
        locked_dialogue=LOCKED_DIALOGUE,
    )


def test_story_orientation_exposes_only_source_verifiable_skill_catalogs() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        input_text="所有人的数学水平倒退100倍，只有我不变的三分钟爽文短剧。",
        target_duration_seconds=180,
    )
    client = StoryOrientationClient()

    result = orient_story(request, client)

    assert result.used
    assert result.orientation is not None
    assert result.orientation.selected_narrative_skills == []
    assert "MiniMax Design 的 drama-series" in client.instructions
    assert "MiniMax-H3 方法提供可组合的任务级表达方法" in client.instructions
    assert "没有通用真人短剧" not in client.instructions
    payload = json.loads(client.input_text)
    assert payload["available_narrative_skills"] == []
    assert {
        item["id"] for item in payload["available_production_methods"]
    } == {descriptor.id for descriptor in production_method_skill_catalog()}


def test_story_orientation_structures_plain_locked_dialogue_semantically() -> None:
    request = CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=50,
        input_text=LOCKED_SCRIPT,
    )

    result = orient_story(request, LockedStoryOrientationClient())

    assert result.used
    assert result.orientation is not None
    assert result.orientation.source_contract.locked_dialogue == LOCKED_DIALOGUE


def test_story_orientation_rejects_unknown_skill_without_keyword_fallback() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        input_text="写一个爽剧。",
    )
    invalid = orient_story(
        request,
        StoryOrientationClient(skill_id="narrative.keyword_selected"),
    )
    invalid_method = orient_story(
        request,
        StoryOrientationClient(method_id="method.keyword_selected"),
    )
    offline = orient_story(request, None)

    assert not invalid.used
    assert invalid.orientation is None
    assert "unknown narrative Skills" in invalid.warnings[0]
    assert not invalid_method.used
    assert invalid_method.orientation is None
    assert "unknown Production Method Skills" in invalid_method.warnings[0]
    assert offline.orientation is not None
    assert offline.orientation.selected_narrative_skills == []


def test_production_skills_are_advisory_and_share_independent_tools() -> None:
    catalog = production_skill_catalog()
    assert {skill.profile for skill in catalog} == set(ProductionProfile)
    assert all(skill.user_overridable for skill in catalog)
    assert all(skill.recommended_playbook for skill in catalog)

    douyin = production_skill(ProductionProfile.AI_SHORT_DRAMA)
    general = production_skill(ProductionProfile.GENERAL_VIDEO)
    digital_human = production_skill(ProductionProfile.DIGITAL_HUMAN)
    anime = production_skill(ProductionProfile.ANIME)
    assert douyin.profile == ProductionProfile.AI_SHORT_DRAMA
    assert general.profile == ProductionProfile.GENERAL_VIDEO
    assert digital_human.profile == ProductionProfile.DIGITAL_HUMAN
    assert anime.profile == ProductionProfile.ANIME
    assert len({skill.recommended_playbook for skill in catalog}) == len(catalog)
    assert douyin.writer != general.writer
    assert douyin.constitution != general.constitution
    assert douyin.asset_reviewer != general.asset_reviewer
    assert douyin.route_planner != general.route_planner
    assert douyin.video_prompt_compiler != general.video_prompt_compiler
    assert douyin.episode_reviewer != general.episode_reviewer
    assert "口型同步" in digital_human.constitution
    assert "角色造型基准" in anime.constitution
    assert digital_human.director != douyin.director
    assert anime.director != douyin.director


def test_production_skills_inject_distinct_policies_without_fixed_shot_count() -> None:
    general_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    douyin_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
    )

    general_instructions, general_input = _outline_prompt(general_request)
    douyin_instructions, douyin_input = _outline_prompt(douyin_request)
    general_payload = json.loads(general_input)
    douyin_payload = json.loads(douyin_input)

    assert "留白、静默" in general_instructions
    assert "主导类型、主角行动路线与核心情绪" in douyin_instructions
    assert "办公室、文件、录音" not in douyin_instructions
    assert "借用签名" not in douyin_instructions
    assert "用户已经确定的作品承诺" in douyin_instructions
    assert production_skill(ProductionProfile.AI_SHORT_DRAMA).writer == ""
    assert production_skill(ProductionProfile.AI_SHORT_DRAMA).critic == ""
    assert "不是 MiniMax 官方 Skill" in douyin_instructions
    assert "Leader → Worker → Verifier" in douyin_instructions
    assert "不得为提高密度而机械增加镜头" not in douyin_instructions
    assert len(douyin_instructions) < 20_000
    assert "真实皮肤" not in douyin_instructions
    assert general_payload["production_profile"] == "GENERAL_VIDEO"
    assert douyin_payload["production_profile"] == "AI_SHORT_DRAMA"
    assert general_payload["target_duration_seconds"] == 60
    assert douyin_payload["target_duration_seconds"] == 300
    assert "而不是完整剧本" in general_instructions
    assert "不要写镜头、机位、关键帧或 Provider 提示词" in general_instructions
    assert general_payload["handoff_contract"]["scene_director_owns"] == (
        "shot execution inside one scene"
    )
    assert "6-10" not in general_instructions + general_input
    assert "6-10" not in douyin_instructions + douyin_input


def test_douyin_long_form_prompts_leave_pacing_to_semantic_reading() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        input_text="一把不该出现的红伞打断融资签约。",
        target_duration_seconds=180,
    )
    outline = _fallback_episode_outline(request)

    architect_instructions, _ = _outline_prompt(request)
    critic_instructions, critic_input = _story_critic_prompt(request, outline)
    final_read_instructions, _ = _final_cold_read_prompt(request, outline)
    scene_instructions, scene_input = _scene_director_prompt(
        request,
        outline,
        outline.scenes[0],
        outline.scenes[0].continuity_in,
    )
    critic_payload = json.loads(critic_input)
    scene_payload = json.loads(scene_input)

    assert "AI 短剧（本地格式适配）" in architect_instructions
    assert "不再自建一套爆款、爽剧、喜剧" in architect_instructions
    assert "时长算术本身不代表内容成立" in architect_instructions
    assert "主导类型、主角行动路线与核心情绪" in architect_instructions
    assert "爽点、情感释放或反转都应从前面的铺垫" in architect_instructions
    assert "[grounded-institutional-causality]" not in architect_instructions
    assert "[separate-consequence-domains]" not in architect_instructions
    assert "整稿只聚焦五件事" in critic_instructions
    assert "主角的处境、目标和行动路线" in critic_instructions
    assert "对立力量的反应、受挫或反噬" in critic_instructions
    assert "结尾要看实际可读剧本" in critic_instructions
    assert "第一判断依据是 StoryIntent" in critic_instructions
    assert "performance_timing_assessment 中简要说明主要压力点" in critic_instructions
    assert "不要逐场建立语速、动作和停顿账本" in critic_instructions
    assert "performance_timing_assessment" not in critic_payload["output_schema"][
        "required"
    ]
    assert "pacing_evidence" not in critic_payload
    assert "各场可信范围、全剧范围" in final_read_instructions
    assert "本场究竟改变了什么" in final_read_instructions
    assert "不触发代码阈值" in final_read_instructions
    assert "观望、等待、行走或重复证明" in scene_instructions
    assert "导演的通用职责" in scene_instructions
    assert "拆成可生成、可检查、可剪辑的单元" in scene_instructions
    assert "scene_pacing_evidence" not in scene_payload


def test_general_video_critic_does_not_treat_quiet_time_as_a_density_failure() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=180,
    )
    outline = _fallback_episode_outline(request)

    instructions, payload_json = _story_critic_prompt(request, outline)
    payload = json.loads(payload_json)

    assert "留白和无厘头并不天然是缺陷" in instructions
    assert "pacing_evidence" not in payload


def test_story_revision_prompts_treat_script_review_as_separate_delivery() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        input_text="请生成完整剧本及其内容审校。",
        target_duration_seconds=180,
    )
    outline = _fallback_episode_outline(request)
    review = ScriptReviewReport(
        summary="内容审校已作为独立报告交付。",
        strengths=["主线清晰"],
        issues=[
            ScriptReviewIssue(
                issue_id="CAUSAL-LOGIC-1",
                category="CAUSAL_LOGIC",
                severity=ScriptReviewSeverity.HIGH,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="开场事件尚未在结尾回收。",
                recommendation="在第 1 场补足因果回收。",
            )
        ],
        requires_revision=True,
    )
    source_contract = StorySourceContract(
        explicit_commitments=["完整剧本及其内容审校"],
        explicit_prohibitions=[],
        open_creative_choices=["未指定的剧情细节"],
    )
    repair_plan = StoryRepairPlan(
        summary="只修复剧情因果。",
        source_promise="保留完整剧本交付。",
        invariants=["主线清晰"],
        actions=[
            StoryRepairAction(
                issue_index=1,
                issue_id="CAUSAL-LOGIC-1",
                diagnosis=StoryRepairDiagnosis.OUTLINE_DEFECT,
                should_revise=True,
                rationale="剧情因果尚未闭环。",
                affected_scene_sequences=[1],
                recommended_change="回收开场事件。",
            )
        ],
    )

    repair_instructions, repair_input = _story_repair_plan_prompt(
        request,
        outline,
        review,
        source_contract,
        None,
    )
    critic_instructions, critic_input = _story_critic_prompt(request, outline)
    verifier_instructions, verifier_input = _story_revision_verifier_prompt(
        request,
        outline,
        outline,
        review,
        repair_plan,
        source_contract,
    )

    assert "ScriptReviewReport 本身就是独立交付" in critic_instructions
    assert "不要求把审校附录写进场景" in critic_instructions
    assert "语义判断" in critic_instructions
    assert "不触发代码阈值" in critic_instructions
    assert "你已经完成了整稿冷读" in critic_instructions
    assert "不再把同一份稿件交给另一个独立审校角色" in critic_instructions
    assert "旧运行记录" not in critic_instructions
    assert "审校报告是独立工件" in repair_instructions
    assert "CRITIC_OVERREACH" in repair_instructions
    assert "编剧的通用职责" not in repair_instructions
    assert "内容审校的通用职责" not in repair_instructions
    assert "不得因审校附录没有写进场景" in verifier_instructions
    assert "你是唯一的 Script Verifier" in verifier_instructions
    assert "在同一次阅读中先核对已分派的修复" in verifier_instructions
    assert "只使用 audience_cold_read" in verifier_instructions
    assert "不用 outline summary" in verifier_instructions
    assert "整稿只聚焦五项" in verifier_instructions
    assert "铺垫是否释放成爽点" in verifier_instructions
    assert "对立力量的受挫、反应或反噬" in verifier_instructions
    assert "才以逻辑理由否决" in verifier_instructions
    assert "不逐场建立表演时长账本" in verifier_instructions
    assert "recommended_followup=FINALIZE" in verifier_instructions
    assert "不再把稿件转交给另一个独立审校角色" in verifier_instructions
    assert "旧运行记录" not in verifier_instructions
    assert "不得因" in verifier_instructions
    assert "修订验收的通用职责" in verifier_instructions
    assert "内容审校的通用职责" not in verifier_instructions
    assert json.loads(critic_input)["readable_screenplay"][0]["screenplay_text"] == (
        outline.scenes[0].screenplay_text
    )
    assert json.loads(repair_input)["routed_story_review"] == review.model_dump(
        mode="json"
    )
    verifier_payload = json.loads(verifier_input)
    assert verifier_payload["routed_story_review"] == review.model_dump(mode="json")
    assert "original_outline" not in verifier_payload
    assert "revised_outline" not in verifier_payload
    assert verifier_payload["audience_cold_read"] == {
        "user_brief": request.input_text.strip(),
        "source_contract": source_contract.model_dump(mode="json"),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "ending_contract": (
            outline.ending_contract.model_dump(mode="json")
            if outline.ending_contract is not None
            else None
        ),
        "readable_final_screenplay": [
            {
                "scene_sequence": outline.scenes[0].sequence,
                "heading": outline.scenes[0].heading,
                "target_duration_seconds": outline.scenes[0].target_duration_seconds,
                "screenplay_text": outline.scenes[0].screenplay_text,
            }
        ],
    }
    assert verifier_payload["repair_verification_context"][
        "revised_readable_screenplay"
    ] == verifier_payload["audience_cold_read"]["readable_final_screenplay"]
    repair_schema = json.loads(repair_input)["output_schema"]
    assert "issue_id" in repair_schema["$defs"]["StoryRepairAction"]["required"]
    verifier_schema = json.loads(verifier_input)["output_schema"]
    assert "satisfied_issue_ids" in verifier_schema["required"]
    assert (
        "issue_id"
        in verifier_schema["$defs"]["StoryRevisionViolation"]["required"]
    )


def test_legacy_revision_issue_indices_load_and_upgrade_to_stable_ids() -> None:
    review = ScriptReviewReport(
        summary="旧修订记录兼容测试。",
        issues=[
            ScriptReviewIssue(
                issue_id="STABLE-ISSUE-1",
                category="CAUSAL_LOGIC",
                severity=ScriptReviewSeverity.HIGH,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="因果尚未闭合。",
                recommendation="回收因果。",
            )
        ],
        requires_revision=True,
    )
    legacy_plan = StoryRepairPlan.model_validate(
        {
            "summary": "旧计划。",
            "source_promise": "保留主线。",
            "actions": [
                {
                    "issue_index": 1,
                    "diagnosis": "OUTLINE_DEFECT",
                    "should_revise": True,
                    "rationale": "旧数据只有索引。",
                    "affected_scene_sequences": [1],
                    "recommended_change": "聚焦修复。",
                }
            ],
        }
    )
    upgraded_plan = normalize_story_repair_plan_issue_ids(review, legacy_plan)
    assert upgraded_plan.actions[0].issue_id == "STABLE-ISSUE-1"

    legacy_acceptance = StoryRevisionVerification.model_validate(
        {
            "accepted": True,
            "summary": "旧 Verifier 使用索引记录通过项。",
            "satisfied_issue_indices": [1],
            "violations": [],
        }
    )
    upgraded_acceptance = normalize_story_revision_verification_issue_ids(
        upgraded_plan,
        legacy_acceptance,
    )
    assert upgraded_acceptance.satisfied_issue_ids == ["STABLE-ISSUE-1"]

    legacy_rejection = StoryRevisionVerification.model_validate(
        {
            "accepted": False,
            "summary": "旧 Verifier 的 violation 没有 ID。",
            "violations": [
                {
                    "category": "LEGACY_REGRESSION",
                    "scene_sequences": [1],
                    "observation": "旧回归。",
                    "repair_instruction": "保留旧证据。",
                }
            ],
        }
    )
    assert legacy_rejection.violations[0].issue_id is None


def test_story_revision_verifier_rejects_an_unscoped_scene_violation() -> None:
    request = _request()
    outline = _fallback_episode_outline(request)
    repair_plan = StoryRepairPlan(
        summary="只修复剧情因果。",
        source_promise="保留完整剧本交付。",
        actions=[
            StoryRepairAction(
                issue_index=1,
                issue_id="CAUSAL-LOGIC-1",
                diagnosis=StoryRepairDiagnosis.OUTLINE_DEFECT,
                should_revise=True,
                rationale="剧情因果尚未闭环。",
                affected_scene_sequences=[1],
                recommended_change="回收开场事件。",
            )
        ],
    )
    verification = StoryRevisionVerification(
        accepted=False,
        summary="错把独立审校交付当成了场次内容。",
        satisfied_issue_ids=["CAUSAL-LOGIC-1"],
        violations=[
            StoryRevisionViolation(
                issue_id="DELIVERY-SCOPE-1",
                category="明确交付承诺被删除",
                scene_sequences=[],
                observation="剧本场次中没有内容审校附录。",
                repair_instruction="把内容审校加回剧本。",
            )
        ],
    )

    with pytest.raises(ValueError, match="at least one repairable scene"):
        _validate_story_revision_verification(
            outline,
            repair_plan,
            verification,
        )


@pytest.mark.parametrize("target_seconds", [180, 300, 430, 600, 900])
def test_ai_short_drama_scaffold_preserves_multi_minute_duration_contract(
    target_seconds: int,
) -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=target_seconds,
    )

    plan = _fallback_plan(request)

    assert plan.target_duration_seconds == target_seconds
    assert sum(scene.target_duration_seconds for scene in plan.scenes) == pytest.approx(
        target_seconds
    )
    assert sum(shot.duration_seconds for shot in plan.shots) == pytest.approx(target_seconds)
    assert [scene.sequence for scene in plan.scenes] == list(
        range(1, len(plan.scenes) + 1)
    )
    assert all(shot.duration_seconds > 0 for shot in plan.shots)
    assert {shot.scene_sequence for shot in plan.shots} == {
        scene.sequence for scene in plan.scenes
    }


def test_language_backed_planning_requires_agent_owned_content_job() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request)

    with pytest.raises(ValueError, match="AgentRun-owned Content Planning Job"):
        create_production_plan(request, client)


def test_scene_director_rejects_reference_subjects_outside_episode_asset_contract() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    outline = _fallback_episode_outline(request)
    scene = outline.scenes[0]
    plan = _fallback_directed_scene_plan(
        request,
        outline,
        scene,
        scene.continuity_in,
    )
    plan.shots[0].reference_needs.append(
        PlannedReferenceNeed(
            subject_name="未声明的临时道具",
            acceptable_roles=[ReferenceRole.PROP_PRODUCT],
            purpose="测试单场资产契约校验",
        )
    )

    with pytest.raises(ValueError, match="outside the episode asset contract"):
        _validate_directed_scene_plan(outline, scene, plan)


def test_scene_director_retries_unknown_reference_subject_on_next_explicit_call() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    client = SceneReferenceRepairClient(request)
    scene = client.outline.scenes[0]

    first = direct_story_scene(
        request,
        client.outline,
        scene,
        scene.continuity_in,
        client,
    )

    assert first.retry_required is True
    assert first.retry_feedback is not None
    assert first.scene is None
    assert first.repaired is False
    assert first.used_fallback is False
    assert client.calls == 1
    assert client.inputs[0]["previous_retry_feedback"] is None

    second = direct_story_scene(
        request,
        client.outline,
        scene,
        scene.continuity_in,
        client,
        retry_feedback=first.retry_feedback,
    )

    assert second.retry_required is False
    assert second.retry_feedback is None
    assert second.repaired is False
    assert second.used_fallback is False
    assert client.calls == 2
    assert client.inputs[1]["previous_retry_feedback"] == first.retry_feedback
    assert all(
        need.subject_name != "未声明的临时道具"
        for shot in second.scene.shots
        for need in shot.reference_needs
    )


def test_provider_html_error_is_compacted_before_persisting_warning() -> None:
    error = LanguageProviderError(
        "<!DOCTYPE html><html><head><title>Unicorn! · Provider</title></head>"
        + "<body>failure</body></html>"
        + ("x" * 5000)
    )

    summary = _compact_error(error)

    assert summary == "Provider returned an HTML error page: Unicorn! · Provider"
    assert "<!DOCTYPE" not in summary


def test_multi_scene_plan_maps_scene_contracts_and_shots_into_project_document() -> None:
    request = CreateProjectRequest(
        name="五分钟自动短剧",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    planning = create_production_plan(request, None)

    document = build_project_document_from_plan(
        project_id="project-five-minute",
        request=request,
        planning=planning,
        created_at="2026-08-21T00:00:00Z",
    )

    script = document.script_versions[0]
    scene_ids = {scene.id for scene in script.structured_scenes}
    assert document.project.target_duration_seconds == 300
    assert len(script.structured_scenes) == len(planning.plan.scenes)
    assert all(scene.entry_state is not None for scene in script.structured_scenes)
    assert all(scene.exit_state is not None for scene in script.structured_scenes)
    assert all(shot.scene_id in scene_ids for shot in document.shots)


def test_general_profile_accepts_silent_long_take_plan_without_density_gate() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    fallback = _fallback_plan(request)
    assert len(fallback.shots) == 1
    assert fallback.lines == []
    assert fallback.style.genre == "未指定"
    payload = fallback.model_dump(mode="json")
    payload["lines"] = []
    payload["shots"] = [payload["shots"][0] for _ in range(3)]
    for shot in payload["shots"]:
        shot["duration_seconds"] = 20.0
        shot["dialogue_line_indices"] = []

    plan = ProductionPlan.model_validate(payload)

    assert len(plan.shots) == 3
    assert sum(shot.duration_seconds for shot in plan.shots) == 60.0
    assert plan.lines == []


def test_production_subject_names_are_unique_across_asset_kinds() -> None:
    payload = _fallback_plan(_request()).model_dump(mode="json")
    payload["locations"][0]["name"] = payload["characters"][0]["name"]

    with pytest.raises(ValueError, match="must be globally unique"):
        ProductionPlan.model_validate(payload)


def test_local_scaffold_does_not_choose_style_or_profile_specific_rhythm() -> None:
    common = {
        "source_mode": SourceMode.AUTO_ORIGINAL,
        "input_text": "一段尚未定稿的用户想法",
        "target_duration_seconds": 60,
    }
    ai_plan = _fallback_plan(
        CreateProjectRequest(
            **common,
            production_profile=ProductionProfile.AI_SHORT_DRAMA,
        )
    )
    general_plan = _fallback_plan(
        CreateProjectRequest(
            **common,
            production_profile=ProductionProfile.GENERAL_VIDEO,
        )
    )

    assert len(ai_plan.scenes) == len(general_plan.scenes)
    assert len(ai_plan.shots) == len(general_plan.shots)
    assert ai_plan.style == general_plan.style
    assert ai_plan.style.medium == "未指定"
    assert ai_plan.style.forbidden == []
    serialized = ai_plan.model_dump_json()
    for hardcoded_choice in ("都市", "悬疑", "真人", "电影感", "真实皮肤", "冷白", "蓝黑"):
        assert hardcoded_choice not in serialized


def test_editorial_retiming_does_not_split_at_h3_request_boundary() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    templates = _fallback_plan(request).shots[:3]

    shots = _retime_shots(
        templates,
        target_duration_seconds=75.0,
        scene_sequence=1,
        preferred_count=3,
    )

    assert [shot.duration_seconds for shot in shots] == [25.0, 25.0, 25.0]


def test_empty_reference_needs_remain_explicitly_empty() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    for shot in planning.plan.shots:
        shot.reference_needs = []

    document = build_project_document_from_plan(
        project_id="project-explicit-empty-references",
        request=request,
        planning=planning,
        created_at="2026-09-03T00:00:00Z",
    )

    assert all(plan.requirements == [] for plan in document.reference_plan)
    assert all(plan.bindings == [] for plan in document.reference_plan)
    assert all(
        plan.planning_notes == "asset-manager:agent-explicit"
        for plan in document.reference_plan
    )


def test_legacy_project_document_defaults_to_douyin_profile() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-profile-backward-compatible",
        request=request,
        planning=planning,
        created_at="2026-08-21T00:00:00Z",
    )
    payload = document.model_dump(mode="json")
    del payload["project"]["production_profile"]

    restored = ProjectDocument.model_validate(payload)

    assert restored.project.production_profile == ProductionProfile.AI_SHORT_DRAMA


def test_plain_locked_script_waits_for_agent_semantic_intake() -> None:
    request = CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=50,
        input_text=LOCKED_SCRIPT,
    )

    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-locked-awaiting-intake",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )

    assert planning.plan.lines == []
    assert document.script_versions[0].original_text == LOCKED_SCRIPT


def test_structured_locked_dialogue_must_be_verbatim_and_in_source_order() -> None:
    with pytest.raises(ValueError, match="appear verbatim"):
        CreateProjectRequest(
            name="坏顺序",
            source_mode=SourceMode.SCRIPT_LOCKED,
            target_duration_seconds=50,
            input_text=LOCKED_SCRIPT,
            locked_dialogue=list(reversed(LOCKED_DIALOGUE)),
        )


def test_locked_fallback_does_not_turn_metadata_or_voiceover_into_characters() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-locked-parser",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )

    assert [character.name for character in planning.plan.characters] == ["林夏", "周衡"]
    assert [line.speaker for line in planning.plan.lines] == [
        "林夏",
        "周衡",
        "林夏",
        "录音笔中的林夏",
        "周衡",
    ]
    assert [
        asset.name
        for asset in document.assets
        if asset.kind.value == "CHARACTER"
    ] == ["林夏定妆", "周衡定妆"]


def test_reference_refresh_does_not_infer_assets_from_shot_text() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    planning.plan.props.append(
        PlannedProp(
            name="红色录音笔",
            appearance="锁定剧本声明的红色录音笔",
        )
    )
    planned_shot = planning.plan.shots[0]
    planned_shot.visual += "；红色录音笔在画面中承担证据作用"
    planned_shot.prop_state_in = "红色录音笔可见"
    planned_shot.prop_state_out = "红色录音笔仍可见"
    document = build_project_document_from_plan(
        project_id="project-lightweight-reference",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )
    shot = document.shots[0]
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    prop = next(item for item in document.assets if item.kind.value == "PROP")

    changed = refresh_lightweight_reference_plans(document)

    assert changed is False
    assert plan.requirements == []
    assert all(item.target_id != prop.current_version_id for item in plan.bindings)


def test_lightweight_reference_plans_exclude_unrelated_project_locations() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    planning.plan.style.texture = "干净低噪点的电影质感"
    planning.plan.locations.append(
        PlannedLocation(
            name="地下车库",
            layout="开阔停车区与独立步行入口",
            lighting="冷灰顶灯",
            palette="水泥灰与低饱和绿色",
            key_views=["步行入口", "停车区全景"],
        )
    )

    document = build_project_document_from_plan(
        project_id="project-location-scope",
        request=request,
        planning=planning,
        created_at="2026-08-24T00:00:00Z",
    )

    unrelated = next(
        item for item in document.assets if item.name == "地下车库"
    )
    assert unrelated.brief is not None
    assert unrelated.brief.named_views_or_zones == ["步行入口", "停车区全景"]
    assert unrelated.brief.texture_constraints == ["干净低噪点的电影质感"]
    assert all(
        unrelated.current_version_id
        not in {binding.target_id for binding in plan.bindings}
        for plan in document.reference_plan
    )


def test_location_name_ending_in_character_suffix_maps_to_its_asset() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    location_name = "摄影棚定妆"
    planning.plan.locations.append(
        PlannedLocation(
            name=location_name,
            layout="单层摄影棚与中央表演区",
            lighting="均匀顶光",
            palette="中性灰",
            key_views=["摄影棚全景", "中央表演区"],
        )
    )
    planning.plan.shots[0].reference_needs = [
        PlannedReferenceNeed(
            subject_name=location_name,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="锁定该摄影棚的空间关系",
        )
    ]

    document = build_project_document_from_plan(
        project_id="project-location-suffix",
        request=request,
        planning=planning,
        created_at="2026-09-02T00:00:00Z",
    )
    location = next(item for item in document.assets if item.name == location_name)
    reference_plan = document.reference_plan[0]

    assert reference_plan.requirements[0].subject_asset_id == location.id


def test_reference_refresh_uses_only_explicit_subject_asset_ids() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    planning.plan.locations.append(
        PlannedLocation(
            name="地下车库",
            layout="开阔停车区与独立步行入口",
            lighting="冷灰顶灯",
            palette="水泥灰与低饱和绿色",
            key_views=["步行入口", "停车区全景"],
        )
    )
    document = build_project_document_from_plan(
        project_id="project-reference-refresh-scope",
        request=request,
        planning=planning,
        created_at="2026-08-24T00:00:00Z",
    )
    locations = [
        item for item in document.assets if item.kind.value == "LOCATION"
    ]
    unrequested = next(item for item in locations if item.name != "地下车库")
    requested = next(item for item in locations if item.name == "地下车库")
    shot = document.shots[0]
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    plan.planning_notes = "legacy-note-must-not-route"
    plan.requirements = [
        ReferenceRequirement(
            id=f"{shot.id}-reference-01",
            subject_asset_id=requested.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="Agent 明确选择该地点作为本镜空间锚点",
        )
    ]

    changed = refresh_lightweight_reference_plans(document)

    subject_ids = {item.subject_asset_id for item in plan.requirements}
    bound_version_ids = {item.target_id for item in plan.bindings}
    assert changed is True
    assert subject_ids == {requested.id}
    assert bound_version_ids == {requested.current_version_id}
    assert unrequested.current_version_id not in bound_version_ids
