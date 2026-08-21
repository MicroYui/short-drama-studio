"""Planning regressions for structured locked-screenplay input."""

from __future__ import annotations

import json

import pytest

from short_drama.models import (
    CreateProjectRequest,
    ProductionProfile,
    ProjectDocument,
    ScriptReviewIssue,
    ScriptReviewReport,
    ScriptReviewSeverity,
    SourceMode,
)
from short_drama.production_planner import (
    ProductionPlan,
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
    _fallback_plan,
    _locked_dialogue,
    _planner_prompt,
    build_project_document_from_plan,
    create_production_plan,
    refresh_lightweight_reference_plans,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderError


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
    ) -> None:
        self.request = request
        self.fail_scene = fail_scene
        self.critic_revision_rounds_required = critic_revision_rounds_required
        self.outline = _fallback_episode_outline(request)
        self.scene_inputs: list[dict[str, object]] = []
        self.calls = 0
        self.critic_calls = 0
        self.revision_calls = 0
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
        if "revising an episode" in instructions:
            self.revision_calls += 1
            payload = self.outline.model_dump(mode="python")
            payload["story_summary"] += " 修订后明确了开场事件与结尾选择的因果关系。"
            return json.dumps(payload, ensure_ascii=False)
        if "story-architect Agent" in instructions:
            return self.outline.model_dump_json()
        payload = json.loads(input_text)
        self.scene_inputs.append(payload)
        sequence = int(payload["scene_contract"]["sequence"])
        if sequence == self.fail_scene:
            raise LanguageProviderError(f"scene {sequence} unavailable")
        return self.scene_plans[sequence].model_dump_json()


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=50,
        input_text=LOCKED_SCRIPT,
    )


def test_production_profiles_inject_distinct_policies_without_fixed_shot_count() -> None:
    general_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    douyin_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
    )

    general_instructions, general_input = _planner_prompt(general_request)
    douyin_instructions, douyin_input = _planner_prompt(douyin_request)
    general_payload = json.loads(general_input)
    douyin_payload = json.loads(douyin_input)

    assert "Atmosphere, silence" in general_instructions
    assert "Do not invent conflict" in general_instructions
    assert "curiosity gap" in douyin_instructions
    assert "Never add shots" in douyin_instructions
    assert general_payload["production_profile"] == "GENERAL_VIDEO"
    assert douyin_payload["production_profile"] == "DOUYIN_SHORT_DRAMA"
    assert general_payload["target_duration_seconds"] == 60
    assert douyin_payload["target_duration_seconds"] == 300
    assert general_payload["requirements"]["shots"] == (
        "choose the smallest coherent executable set; no fixed count"
    )
    assert "6-10" not in general_instructions + general_input
    assert "6-10" not in douyin_instructions + douyin_input


@pytest.mark.parametrize("target_seconds", [180, 300, 430, 600, 900])
def test_douyin_fallback_scales_to_multi_minute_scene_contracts(
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
    assert all(4 <= shot.duration_seconds <= 15 for shot in plan.shots)
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
    assert client.calls == len(planning.plan.scenes) + 2
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


def test_script_critic_revision_loop_is_bounded_and_remaining_advice_is_non_blocking() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.IDEA_EXPANSION,
        input_text="一个选择会改变未来的都市悬疑故事。",
        target_duration_seconds=300,
    )
    client = MultiAgentLanguageClient(request, critic_revision_rounds_required=99)

    planning = create_production_plan(request, client)

    assert client.revision_calls == 2
    assert client.critic_calls == 3
    assert "revised-2x" in planning.planner
    assert planning.review_report is not None
    assert planning.review_report.requires_revision is True
    assert any("仍保留" in warning and "不会阻断" in warning for warning in planning.warnings)
    assert planning.plan.shots


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
    assert len(fallback.shots) == 4
    assert fallback.lines == []
    assert fallback.style.genre == "都市生活观察"
    payload = fallback.model_dump(mode="json")
    payload["lines"] = []
    payload["shots"] = payload["shots"][:3]
    for shot in payload["shots"]:
        shot["duration_seconds"] = 15.0
        shot["dialogue_line_indices"] = []

    plan = ProductionPlan.model_validate(payload)

    assert len(plan.shots) == 3
    assert sum(shot.duration_seconds for shot in plan.shots) == 45.0
    assert plan.lines == []


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

    assert restored.project.production_profile == ProductionProfile.DOUYIN_SHORT_DRAMA


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
    document = build_project_document_from_plan(
        project_id="project-lightweight-reference",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )
    shot = next(item for item in document.shots if item.sequence == 5)
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
