"""Planning regressions for structured locked-screenplay input."""

from __future__ import annotations

import json

import pytest

from short_drama.models import (
    CreateProjectRequest,
    ProductionProfile,
    ProjectDocument,
    ReferenceFraming,
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
    StorySourceContract,
    StoryRevisionVerification,
)
from short_drama.production_planner import (
    PlannedCharacter,
    PlannedLocation,
    PlannedProp,
    PlannedReferenceNeed,
    ProductionPlan,
    _default_reference_needs,
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
    _fallback_plan,
    _compact_error,
    _character_name_covered,
    _locked_dialogue,
    _outline_prompt,
    _planner_prompt,
    _retime_shots,
    _scene_director_prompt,
    _story_critic_prompt,
    _story_revision_prompt,
    _validate_directed_scene_plan,
    build_project_document_from_plan,
    create_production_plan,
    direct_story_scene,
    refresh_lightweight_reference_plans,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
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


def test_group_character_covers_a_singular_dialogue_label() -> None:
    assert _character_name_covered("受骗买家", {"受骗买家群"})
    assert _character_name_covered("工人", {"工人们"})
    assert not _character_name_covered("林夏", {"林夏朋友"})


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
        if "independent script-critic Agent" in instructions:
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
                            category="CAUSAL_LOGIC",
                            severity=ScriptReviewSeverity.HIGH,
                            scene_sequences=[1, len(self.outline.scenes)],
                            observation="结尾选择尚未完整解释开场事件如何成立。",
                            recommendation="明确世界规则或调整结尾选择，使因果闭合。",
                        )
                    ]
                    if requires_revision
                    else []
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
        if "story-repair planner Agent" in instructions:
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
            required = [
                action["issue_index"]
                for action in payload["story_repair_plan"]["actions"]
                if action["should_revise"]
            ]
            return StoryRevisionVerification(
                accepted=True,
                summary="Writer 已落实 Repair Plan 且没有引入新矛盾。",
                satisfied_issue_indices=required,
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


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=50,
        input_text=LOCKED_SCRIPT,
    )


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

    general_instructions, general_input = _planner_prompt(general_request)
    douyin_instructions, douyin_input = _planner_prompt(douyin_request)
    general_payload = json.loads(general_input)
    douyin_payload = json.loads(douyin_input)

    assert "留白、静默" in general_instructions
    assert "不要仅为模仿短剧公式" in general_instructions
    assert "好奇缺口" in douyin_instructions
    assert "比较若干个实质不同的构想" in douyin_instructions
    assert "办公室、文件、录音" not in douyin_instructions
    assert "借用签名" not in douyin_instructions
    assert "本故事独有的承诺" in douyin_instructions
    assert "日常语序、代词、语气词" in douyin_instructions
    assert "日常语序、代词、语气词" not in general_instructions
    assert "而不是把对白压缩得不自然" in douyin_instructions
    assert "不要求每句台词都新增事实" in douyin_instructions
    assert "朗读检查" in production_skill(
        ProductionProfile.AI_SHORT_DRAMA
    ).critic
    assert "电报式" in production_skill(
        ProductionProfile.AI_SHORT_DRAMA
    ).critic
    assert "不得为提高密度而机械增加镜头" in douyin_instructions
    assert "ProductionProfile supplies production guidance" in douyin_instructions
    assert "do not require human anatomy" in douyin_instructions
    assert "真实皮肤" not in douyin_instructions
    assert general_payload["production_profile"] == "GENERAL_VIDEO"
    assert douyin_payload["production_profile"] == "AI_SHORT_DRAMA"
    assert general_payload["target_duration_seconds"] == 60
    assert douyin_payload["target_duration_seconds"] == 300
    assert general_payload["requirements"]["shots"] == (
        "choose the smallest coherent executable set; no fixed count"
    )
    assert general_payload["requirements"]["medium"].startswith(
        "derive the project's concrete visual medium"
    )
    assert "Editorial shots may be shorter than 4 seconds or longer than 15 seconds" in (
        general_instructions
    )
    assert "integer 4-15 seconds" in general_payload["requirements"][
        "shot_duration_seconds"
    ]
    assert "6-10" not in general_instructions + general_input
    assert "6-10" not in douyin_instructions + douyin_input


def test_douyin_long_form_prompts_audit_effective_story_duration_without_a_hard_gate() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
        input_text="一把不该出现的红伞打断融资签约。",
        target_duration_seconds=180,
    )
    outline = _fallback_episode_outline(request)

    architect_instructions, _ = _outline_prompt(request)
    critic_instructions, critic_input = _story_critic_prompt(request, outline)
    scene_instructions, scene_input = _scene_director_prompt(
        request,
        outline,
        outline.scenes[0],
        outline.scenes[0].continuity_in,
    )
    review = ScriptReviewReport(
        summary="有效剧情时长不足。",
        issues=[
            ScriptReviewIssue(
                category="EFFECTIVE_STORY_DURATION",
                severity=ScriptReviewSeverity.HIGH,
                scene_sequences=[1],
                observation="场景时长主要依赖等待和重复证明。",
                recommendation="用新的选择与后果替换空转时间。",
            )
        ],
        requires_revision=True,
    )
    revision_instructions, revision_input = _story_revision_prompt(
        request,
        outline,
        review,
    )
    critic_payload = json.loads(critic_input)
    scene_payload = json.loads(scene_input)
    revision_payload = json.loads(revision_input)

    assert "单独生成、检查、替换和重新剪辑" in architect_instructions
    assert "而不是把对白压缩得不自然" in architect_instructions
    assert "scene-duration arithmetic" in architect_instructions
    assert "[grounded-institutional-causality]" not in architect_instructions
    assert "[separate-consequence-domains]" not in architect_instructions
    assert "Do not approve an episode merely because" in critic_instructions
    assert "对同一结论的重复证明" in critic_instructions
    assert "当前行动、因果和人物后果是否可见可懂" in critic_instructions
    assert "审核实际作品，不按预设答案找错" in critic_instructions
    assert critic_payload["pacing_evidence"]["target_duration_seconds"] == 180
    assert critic_payload["pacing_evidence"]["purpose"].startswith(
        "Rough descriptive evidence"
    )
    assert len(critic_payload["pacing_evidence"]["scenes"]) == len(outline.scenes)
    assert "generic looking, waiting, walking" in scene_instructions
    assert "导演职责准则" in scene_instructions
    assert "重新生成、更换关键帧、拆分镜头" in scene_instructions
    assert scene_payload["scene_pacing_evidence"]["scene_sequence"] == 1
    assert "replacing empty atmosphere" in revision_instructions
    assert "collapse it to the smallest plain-language mechanism" in revision_instructions
    assert "[grounded-institutional-causality]" not in revision_instructions
    assert "[separate-consequence-domains]" not in revision_instructions
    assert revision_payload["current_pacing_evidence"][
        "target_duration_seconds"
    ] == 180
    assert "fixed beat counts" in revision_instructions


def test_general_video_critic_does_not_treat_quiet_time_as_a_density_failure() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
        target_duration_seconds=180,
    )
    outline = _fallback_episode_outline(request)

    instructions, payload_json = _story_critic_prompt(request, outline)
    payload = json.loads(payload_json)

    assert "安静内容不会仅因为安静就构成缺陷" in instructions
    assert payload["pacing_evidence"]["target_duration_seconds"] == 180


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


def test_multi_agent_planner_passes_compact_actual_handoff_between_scene_agents() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request)

    planning = create_production_plan(request, client)

    assert planning.planner.startswith("multi-agent:gpt-5.6-sol story architect")
    assert "+ script critic" in planning.planner
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is False
    assert len(client.scene_inputs) == len(planning.plan.scenes)
    assert client.calls == len(planning.plan.scenes) * 2 + 2
    second_input = client.scene_inputs[1]
    assert second_input["previous_actual_handoff"]["known_facts"][-1] == (
        "agent-scene-1-complete"
    )
    assert "invalid_scene_plan_json" not in second_input


def test_multi_agent_planner_falls_back_only_the_failed_scene() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request, fail_scene=2)

    planning = create_production_plan(request, client)

    shots_by_scene = {
        sequence: [
            shot
            for shot in planning.plan.shots
            if shot.scene_sequence == sequence
        ]
        for sequence in range(1, len(planning.plan.scenes) + 1)
    }
    assert "mixed-fallback" in planning.planner
    assert shots_by_scene[1][0].narrative_purpose.startswith("Agent scene 1")
    assert not shots_by_scene[2][0].narrative_purpose.startswith("Agent scene 2")
    assert shots_by_scene[3][0].narrative_purpose.startswith("Agent scene 3")
    assert any("场景 02" in warning and "仅该场" in warning for warning in planning.warnings)


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


def test_scene_director_locally_repairs_unknown_reference_subject_before_finalize() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
    )
    client = SceneReferenceRepairClient(request)
    scene = client.outline.scenes[0]

    result = direct_story_scene(
        request,
        client.outline,
        scene,
        scene.continuity_in,
        client,
    )

    assert result.repaired is True
    assert result.used_fallback is False
    assert client.calls == 2
    assert all(
        need.subject_name != "未声明的临时道具"
        for shot in result.scene.shots
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


def test_script_critic_can_trigger_one_story_architect_revision_before_scene_planning() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="一个预告未来的录音迫使女主重新审视自己的选择。",
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request, critic_revision_rounds_required=1)

    planning = create_production_plan(request, client)

    assert client.critic_calls == 2
    assert client.revision_calls == 1
    assert "+ script critic revised" in planning.planner
    assert "修订后明确了" in planning.plan.scene_summary
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is False
    assert any("已自动修订" in warning for warning in planning.warnings)
    assert not any("仍保留" in warning for warning in planning.warnings)
    document = build_project_document_from_plan(
        project_id="project-script-reviewed",
        request=request,
        planning=planning,
        created_at="2026-08-21T00:00:00Z",
    )
    assert document.script_versions[0].review_report == planning.review_report
    assert document.bible_versions[0].style.medium == planning.plan.style.medium


def test_story_revision_repairs_invalid_json_before_returning_to_critic() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="一个视觉线索迫使女主做出有代价的选择。",
        target_duration_seconds=180,
    )
    client = MultiAgentLanguageClient(
        request,
        critic_revision_rounds_required=1,
        invalid_first_revision=True,
    )

    planning = create_production_plan(request, client)

    assert client.revision_calls == 2
    assert client.critic_calls == 2
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is False
    assert any(
        "首次输出未通过结构校验" in warning and "已自动修复" in warning
        for warning in planning.warnings
    )


def test_script_critic_revision_loop_is_bounded_and_remaining_advice_is_non_blocking() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="一个选择会改变未来的都市悬疑故事。",
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request, critic_revision_rounds_required=99)

    planning = create_production_plan(request, client)

    assert client.revision_calls == 3
    assert client.critic_calls == 4
    assert "revised-3x" in planning.planner
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is True
    assert any("仍保留" in warning and "不会阻断" in warning for warning in planning.warnings)
    assert planning.plan.shots


def test_review_router_can_handoff_critic_issue_without_rewriting_story() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="人物在复杂空间里完成一次需要清楚调度的行动。",
        target_duration_seconds=180,
    )
    client = DownstreamRoutingClient(
        request,
        critic_revision_rounds_required=1,
    )

    planning = create_production_plan(request, client)

    assert client.critic_calls == 1
    assert client.revision_calls == 0
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is True
    assert planning.review_disposition is not None
    assert (
        planning.review_disposition.action
        == ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF
    )
    assert planning.review_disposition.issue_routes[0].owner == ReviewRepairOwner.SCENE_DIRECTOR
    assert any(
        payload["routed_review_handoff"]
        for payload in client.scene_inputs
    )


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


def test_stationary_long_shot_does_not_force_full_body_reference() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.AI_SHORT_DRAMA,
    )
    plan = _fallback_plan(request)
    shot = plan.shots[0].model_copy(
        deep=True,
        update={
            "duration_seconds": 20.0,
            "visual": "一个稳定画面承载信息变化",
            "framing": "由当前 Style 决定",
            "camera": "保持稳定",
            "character_positions": plan.characters[0].name,
        },
    )

    needs = _default_reference_needs(
        plan=plan,
        shot=shot,
        line_speakers={plan.characters[0].name},
    )

    character_need = next(item for item in needs if item.subject_name == plan.characters[0].name)
    assert ReferenceFraming.FULL_BODY not in character_need.preferred_framings


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


def test_locked_dialogue_ignores_metadata_and_classifies_recorded_voice() -> None:
    lines = _locked_dialogue(LOCKED_SCRIPT)

    assert [(line.speaker, line.audio_kind.value) for line in lines] == [
        ("林夏", "DIALOGUE"),
        ("周衡", "DIALOGUE"),
        ("林夏", "DIALOGUE"),
        ("录音笔中的林夏", "VOICEOVER"),
        ("周衡", "DIALOGUE"),
    ]
    assert [line.text for line in lines] == [
        "你说他十点离开，可监控里，十点零三分他还在这里。",
        "监控会骗人。",
        "人也会。",
        "如果你听到这段话，别相信我。",
        "现在，你还信自己吗？",
    ]


def test_locked_dialogue_ignores_common_story_contract_headings() -> None:
    lines = _locked_dialogue(
        "\n".join(
            [
                "核心场景：仓库、酒店、停车场。",
                "关键规则：未来可以被改变。",
                "结尾明确：录音只代表可能结果。",
                "许宁：“下次先相信我。”",
            ]
        )
    )

    assert [(line.speaker, line.text) for line in lines] == [
        ("许宁", "下次先相信我。")
    ]


def test_locked_dialogue_uses_actor_at_action_start_and_voice_owner_at_source() -> None:
    lines = _locked_dialogue(
        "\n".join(
            [
                "角色：周衡，32 岁。林夏，28 岁。",
                "周衡看向林夏：“监控会骗人。”",
                "周衡按下录音笔，录音中传出林夏的声音：“别相信我。”",
            ]
        )
    )

    assert [(line.speaker, line.audio_kind.value) for line in lines] == [
        ("周衡", "DIALOGUE"),
        ("录音笔中的林夏", "VOICEOVER"),
    ]


def test_locked_dialogue_uses_latest_action_clause_and_nearest_voice_owner() -> None:
    lines = _locked_dialogue(
        "\n".join(
            [
                "角色：许宁，29岁。程野，31岁。顾岚，35岁。",
                "程野推门进来。许宁藏起录音笔，问：“你明晚九点在哪？”",
                "许宁把顾岚叫来。程野出现，看见录音笔，说：“你还是听了那段录音。”",
                "许宁、顾岚和程野当面对质。录音笔再次播放许宁的声音：“如果我没来这里，死的人才会是顾岚。”",
                "许宁关掉录音笔，对程野说：“下次先相信我。”",
            ]
        )
    )

    assert [(line.speaker, line.audio_kind.value) for line in lines] == [
        ("许宁", "DIALOGUE"),
        ("程野", "DIALOGUE"),
        ("录音笔中的许宁", "VOICEOVER"),
        ("许宁", "DIALOGUE"),
    ]


def test_locked_dialogue_strips_speech_actions_from_character_labels() -> None:
    lines = _locked_dialogue(
        "\n".join(
            [
                "角色：许宁，29岁。程野，31岁。",
                "许宁问：“你明晚九点在哪？”",
                "程野回答：“停车场。”",
                "许宁低声说：“别让他看见请柬。”",
            ]
        )
    )

    assert [line.speaker for line in lines] == ["许宁", "程野", "许宁"]


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


def test_invalid_language_plan_gets_one_structured_repair() -> None:
    request = _request()
    client = RepairingLanguageClient(request)

    planning = create_production_plan(request, client)

    assert client.calls == 2
    assert planning.planner == "gpt-5.6-sol-repaired"
    assert any("自动修复后采用" in warning for warning in planning.warnings)
    assert [character.name for character in planning.plan.characters] == ["林夏", "周衡"]
    assert all(
        index >= 1
        for shot in planning.plan.shots
        for index in shot.dialogue_line_indices
    )


def test_lightweight_reference_enrichment_uses_declared_shot_intent() -> None:
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
    prop_version_ids = {item.id for item in prop.versions}
    plan.bindings = [
        item for item in plan.bindings if item.target_id not in prop_version_ids
    ]
    plan.max_reference_images = 3

    changed = refresh_lightweight_reference_plans(document)

    assert changed is True
    assert any(item.target_id == prop.current_version_id for item in plan.bindings)
    assert plan.max_reference_images >= len(plan.bindings)


def test_lightweight_reference_plans_exclude_unrelated_project_locations() -> None:
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
        project_id="project-location-scope",
        request=request,
        planning=planning,
        created_at="2026-08-24T00:00:00Z",
    )

    unrelated = next(
        item for item in document.assets if item.name == "地下车库"
    )
    assert all(
        unrelated.current_version_id
        not in {binding.target_id for binding in plan.bindings}
        for plan in document.reference_plan
    )


def test_deterministic_reference_refresh_rebuilds_visible_subject_scope() -> None:
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
    visible = next(item for item in locations if item.name != "地下车库")
    offscreen = next(item for item in locations if item.name == "地下车库")
    shot = document.shots[0]
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    plan.planning_notes = "asset-manager:deterministic-fallback"
    plan.requirements = [
        ReferenceRequirement(
            id=f"{shot.id}-reference-01",
            subject_asset_id=offscreen.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="下一场地点只存在于连续性说明中",
        )
    ]

    changed = refresh_lightweight_reference_plans(document)

    subject_ids = {item.subject_asset_id for item in plan.requirements}
    assert changed is True
    assert visible.id in subject_ids
    assert offscreen.id not in subject_ids
