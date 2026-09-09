"""Tests for composable hierarchical screenplay tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.hierarchical_script import (
    SceneUpstreamReplanRequest,
    SceneUpstreamReplanTarget,
    _parse_scene_writer_output,
    _scene_writer_prompt,
    _validate_scene_patch,
    assemble_written_scenes,
    compose_scene_screenplay,
    recommend_script_revision_context,
    revise_story_scene,
    write_story_scene,
)
from short_drama.models import (
    CreateProjectRequest,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationRhythmSection,
    DurationTargetOrigin,
    RunMode,
    SourceMode,
    StoryIntent,
)
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedDialogueLine,
    PlannedProp,
    create_story_architecture,
)
from short_drama.repository import ShortDramaRepository
from short_drama.script_documents import (
    EpisodeOutlinePatch,
    EpisodeScenePatch,
    PlanningScriptDocumentKind,
    SceneDurationRevisionConstraint,
    apply_episode_outline_patch,
    build_planning_script_documents,
    scene_dialogue_lines,
)


class _StaticLanguageClient:
    def __init__(self, output: str) -> None:
        self.output = output

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
        return self.output


class _CapturingLanguageClient(_StaticLanguageClient):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.instructions: list[str] = []
        self.inputs: list[dict[str, object]] = []

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
        self.inputs.append(json.loads(input_text))
        return self.output


def _request(seconds: int) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=f"{seconds} 秒分层剧本",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=seconds,
        run_mode=RunMode.GUIDED,
    )


def _scene_patch(outline, sequence: int, suffix: str) -> EpisodeScenePatch:
    scene = outline.scenes[sequence - 1]
    first = outline.characters[0].name
    lines = [PlannedDialogueLine(speaker=first, text=f"第{sequence}场{suffix}")]
    return EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text=compose_scene_screenplay(
            heading=scene.heading,
            playable_actions=list(scene.playable_actions),
            lines=lines,
        ),
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        prop_ids=list(scene.prop_ids),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=lines,
    )


def _explicit_multi_scene_blueprint(
    request: CreateProjectRequest,
    *,
    scene_count: int,
) -> EpisodeOutline:
    """Build the explicit multi-scene model output required by these tests."""

    outline = create_story_architecture(request, None).outline
    template = outline.scenes[0]
    scene_duration = request.resolved_target_duration_seconds() / scene_count
    scenes = []
    for sequence in range(1, scene_count + 1):
        continuity_in = template.continuity_in.model_copy(
            deep=True,
            update={
                "known_facts": (
                    [] if sequence == 1 else [f"第 {sequence - 1} 场已完成"]
                ),
                "open_threads": [f"第 {sequence} 场待推进"],
            },
        )
        continuity_out = template.continuity_out.model_copy(
            deep=True,
            update={
                "known_facts": [f"第 {sequence} 场已完成"],
                "open_threads": (
                    [f"第 {sequence + 1} 场待推进"]
                    if sequence < scene_count
                    else []
                ),
            },
        )
        scenes.append(
            template.model_copy(
                deep=True,
                update={
                    "sequence": sequence,
                    "heading": f"第 {sequence} 场",
                    "summary": f"第 {sequence} 场因果链",
                    "narrative_purpose": f"推进第 {sequence} 场因果链",
                    "playable_actions": [f"完成第 {sequence} 场行动"],
                    "target_duration_seconds": scene_duration,
                    "dialogue_line_indices": [],
                    "continuity_in": continuity_in,
                    "continuity_out": continuity_out,
                },
            )
        )
    return EpisodeOutline.model_validate(
        {
            **outline.model_dump(mode="python"),
            "scenes": [scene.model_dump(mode="python") for scene in scenes],
        }
    )


def test_sub_four_second_scenes_through_sixty_minutes_share_scene_node_tools() -> None:
    brief_request = _request(4)
    short_request = _request(180)
    long_request = _request(3600)
    brief_blueprint = _explicit_multi_scene_blueprint(brief_request, scene_count=2)
    short_blueprint = _explicit_multi_scene_blueprint(short_request, scene_count=3)
    long_blueprint = _explicit_multi_scene_blueprint(long_request, scene_count=6)

    assert brief_blueprint.lines == []
    assert [scene.target_duration_seconds for scene in brief_blueprint.scenes] == [
        2.0,
        2.0,
    ]
    assert short_blueprint.lines == []
    assert long_blueprint.lines == []
    assert all(not scene.dialogue_line_indices for scene in brief_blueprint.scenes)
    assert all(not scene.dialogue_line_indices for scene in short_blueprint.scenes)
    assert all(not scene.dialogue_line_indices for scene in long_blueprint.scenes)
    assert len(brief_blueprint.scenes) <= len(short_blueprint.scenes) < len(long_blueprint.scenes)

    brief_first = write_story_scene(
        brief_request,
        brief_blueprint,
        brief_blueprint.scenes[0],
        [],
        None,
    ).patch
    short_patches = [
        write_story_scene(short_request, short_blueprint, scene, [], None).patch
        for scene in short_blueprint.scenes
    ]
    long_first = write_story_scene(
        long_request,
        long_blueprint,
        long_blueprint.scenes[0],
        [],
        None,
    ).patch

    short_script = assemble_written_scenes(short_blueprint, short_patches)
    assert short_script.lines
    assert len(short_script.scenes) == len(short_blueprint.scenes)
    assert brief_first.scene_sequence == 1
    assert len(brief_first.lines) == 1
    assert long_first.scene_sequence == 1
    assert len(long_first.lines) == 1
    assert all(len(patch.lines) == 1 for patch in short_patches)


def test_scene_duration_envelopes_do_not_require_exact_episode_arithmetic() -> None:
    outline = _explicit_multi_scene_blueprint(_request(180), scene_count=3)
    payload = outline.model_dump(mode="python")
    for scene, duration in zip(payload["scenes"], [200.0, 0.5, 3.0], strict=True):
        scene["target_duration_seconds"] = duration
    drifted_outline = EpisodeOutline.model_validate(payload)

    assert drifted_outline.target_duration_seconds == 180
    assert sum(
        scene.target_duration_seconds for scene in drifted_outline.scenes
    ) == pytest.approx(203.5)


def test_scene_writer_keeps_fast_pacing_out_of_telegraphic_dialogue() -> None:
    request = _request(180)
    blueprint = create_story_architecture(request, None).outline

    instructions, _ = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[0],
        [],
    )

    assert "把当前节点写成可读、可演的场景" in instructions
    assert "尽快进入当下目标与冲突" in instructions
    assert "不要为了显式说明而让人物朗读场景契约" in instructions
    assert "对白必须电报化或口号化" in instructions
    assert "不要求每句都增加新信息" in instructions
    assert "本场的铺垫走向清楚释放" in instructions
    assert "对立力量的受挫、改口或反噬" in instructions
    assert "完整行政或证明流程" in instructions
    assert "performance_timing_assessment 可记录自然演读" in instructions
    assert "不用字数公式、固定容差" in instructions
    assert "继续压缩会破坏用户承诺" in instructions


def test_scene_writer_treats_playable_actions_as_semantic_intent() -> None:
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=1)
    scene = blueprint.scenes[0]
    first_character = blueprint.characters[0].name
    first_character_id = blueprint.characters[0].character_id
    assert first_character_id is not None
    output = json.dumps(
        {
            "scene_sequence": scene.sequence,
            "blocks": [
                {
                    "kind": "ACTION",
                    "text": "他不解释，直接用结果让全场安静下来。",
                    "covers_action_indices": [],
                },
                {
                    "kind": "DIALOGUE",
                    "speaker": first_character,
                    "speaker_scope": "CORE",
                    "speaker_character_id": first_character_id,
                    "text": "答案已经在你们面前了。",
                    "covers_action_indices": [],
                },
            ],
        },
        ensure_ascii=False,
    )

    patch = _parse_scene_writer_output(request, blueprint, scene, output)

    assert patch.playable_actions == scene.playable_actions
    assert patch.performance_timing_assessment is None
    assert scene.playable_actions[0] not in patch.screenplay_text
    assert "他不解释，直接用结果让全场安静下来。" in patch.screenplay_text


def test_scene_writer_receives_story_intent_and_selected_skill_context() -> None:
    request = _request(180)
    blueprint = create_story_architecture(request, None).outline
    story_intent = StoryIntent(
        audience_experience="让观众迅速理解主角优势，并持续期待下一次升级。",
        dramatic_engine="主角的普通能力在失常世界中不断触发地位反转。",
        progression_strategy="每次展示都扩大围观层级和现实回报。",
        tone_and_plausibility="允许夸张和荒诞，以情绪兑现优先。",
        duration_contract=DurationContract(
            target_seconds=180,
            target_origin=DurationTargetOrigin.USER_DECLARED,
            authority=DurationAuthority.TARGET_LED,
            acceptable_range_seconds=None,
            source_fidelity_policy=(
                DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
            ),
            mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
            decision_basis="用户明确要求三分钟原创短剧。",
            rhythm_shape=[
                DurationRhythmSection(
                    label="首次稀缺性兑现",
                    narrative_function="让日常心算立即改变主角处境。",
                    relative_density="比前序更密，但留出全场反应的停顿。",
                    scene_sequences=[1],
                    payoff_or_handoff="将惊讶交给下一级人物。",
                )
            ],
        ),
        protected_elements=["主角能力没有倒退"],
        drift_risks=["写成严肃科普"],
    )

    instructions, input_text = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[0],
        [],
        story_intent=story_intent,
        selected_skill_prompt="优先使用可见的身份反转来兑现情绪。",
    )
    payload = json.loads(input_text)
    duration_contract = story_intent.duration_contract
    assert duration_contract is not None

    assert payload["story_intent"] == story_intent.model_dump(mode="json")
    assert payload["episode_context"]["duration_contract"] == (
        duration_contract.model_dump(mode="json")
    )
    assert payload["current_scene_node"]["duration_rhythm_sections"] == [
        duration_contract.rhythm_shape[0].model_dump(mode="json")
    ]
    assert "优先使用可见的身份反转来兑现情绪。" in instructions
    assert "不要把它改写成固定模板" in instructions


def test_scene_writer_uses_rolling_state_without_replaying_prior_screenplays() -> None:
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    previous_patches = [
        _scene_patch(blueprint, blueprint.scenes[0].sequence, "第一场完整可读剧本。"),
        _scene_patch(blueprint, blueprint.scenes[1].sequence, "第二场完整可读剧本。"),
    ]

    _, input_text = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[2],
        previous_patches,
    )
    payload = json.loads(input_text)

    assert "readable_episode_so_far" not in payload
    assert payload["previous_scene_boundary"] == {
        "scene_ref": "scene:2",
        "scene_sequence": 2,
        "outcome": previous_patches[1].summary,
        "final_rendered_line": previous_patches[1].screenplay_text.splitlines()[-1],
        "final_dialogue_line": {
            "line_ref": "scene:2:line:1",
            **previous_patches[1].lines[-1].model_dump(mode="json"),
        },
        "continuity_out": previous_patches[1].continuity_out.model_dump(mode="json"),
    }
    assert payload["story_state_before_current_scene"] == (
        blueprint.scenes[2].continuity_in.model_dump(mode="json")
    )
    assert payload["older_scene_rollup"] == {
        "through_scene_sequence": 1,
        "fulfilled_events": previous_patches[1].continuity_in.known_facts,
        "character_and_relationship_states": (
            previous_patches[1].continuity_in.character_states
        ),
        "prop_states": previous_patches[1].continuity_in.prop_states,
        "unresolved_promises": previous_patches[1].continuity_in.open_threads,
    }
    assert payload["prior_scene_archive"] == {
        "written_scene_range": {"start_sequence": 1, "end_sequence": 2},
        "scene_ref_format": "scene:{scene_sequence}",
        "line_ref_format": "scene:{scene_sequence}:line:{one_based_line_index}",
        "screenplay_bodies_included": False,
    }


def test_scene_writer_payload_does_not_scale_with_prior_screenplay_body_size() -> None:
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    previous_patches = [
        _scene_patch(blueprint, blueprint.scenes[0].sequence, "第一场。"),
        _scene_patch(blueprint, blueprint.scenes[1].sequence, "第二场。"),
    ]
    short_tail = "林一：这件事还没结束。"
    compact_patches = [
        patch.model_copy(update={"screenplay_text": f"场景正文\n{short_tail}"})
        for patch in previous_patches
    ]
    large_marker = "不应进入后续场输入的历史正文"
    expanded_patches = [
        patch.model_copy(
            update={
                "screenplay_text": (
                    f"{large_marker}\n" * 1000
                    + short_tail
                )
            }
        )
        for patch in previous_patches
    ]

    _, compact_input = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[2],
        compact_patches,
    )
    _, expanded_input = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[2],
        expanded_patches,
    )

    assert large_marker not in expanded_input
    assert expanded_input == compact_input


def test_scene_writers_receive_only_explicitly_related_props_with_state() -> None:
    request = _request(180)
    original = create_story_architecture(request, None).outline
    outline_payload = original.model_dump(mode="python")
    outline_payload["props"] = [
        PlannedProp(
            prop_id="prop-service-offer",
            name="服务包报价页",
            appearance="显示价格、内容与选择状态的报价页。",
        ).model_dump(mode="python"),
        PlannedProp(
            prop_id="prop-unrelated-cup",
            name="会议室水杯",
            appearance="透明玻璃水杯。",
        ).model_dump(mode="python"),
    ]
    outline_payload["scenes"][0]["prop_ids"] = ["prop-service-offer"]
    outline_payload["scenes"][0]["continuity_in"]["prop_states"] = [
        "服务包报价页尚未向客户展示。"
    ]
    outline_payload["scenes"][0]["continuity_out"]["prop_states"] = [
        "报价页已展示可选价格与成交状态。"
    ]
    outline = EpisodeOutline.model_validate(outline_payload)
    scene = outline.scenes[0]

    instructions, input_text = _scene_writer_prompt(
        request,
        outline,
        scene,
        [],
    )
    writer_payload = json.loads(input_text)
    assert writer_payload["current_scene_node"]["props"] == [
        {
            "prop_id": "prop-service-offer",
            "name": "服务包报价页",
            "appearance": "显示价格、内容与选择状态的报价页。",
        }
    ]
    assert writer_payload["current_scene_node"]["prop_state_context"] == {
        "continuity_in": ["服务包报价页尚未向客户展示。"],
        "continuity_out": ["报价页已展示可选价格与成交状态。"],
    }
    assert "prop-unrelated-cup" not in input_text
    assert "不能替代观众在 screenplay blocks 中" in instructions

    patch = _scene_patch(outline, scene.sequence, "展示报价")
    client = _CapturingLanguageClient(patch.model_dump_json())
    result = revise_story_scene(
        request,
        outline,
        scene,
        "让价格、选择和收益变化都在成稿中可见",
        [scene.sequence],
        [],
        SceneDurationRevisionConstraint(
            episode_target_seconds=outline.target_duration_seconds,
            current_episode_scene_total_seconds=sum(
                item.target_duration_seconds for item in outline.scenes
            ),
        ),
        client,
    )
    assert client.inputs[0]["duration_contract"] == (
        outline.story_intent.duration_contract.model_dump(mode="json")
        if outline.story_intent is not None
        and outline.story_intent.duration_contract is not None
        else None
    )
    assert result.retry_required is False
    revision_payload = client.inputs[0]
    episode_manifest = revision_payload["episode_manifest"]
    assert isinstance(episode_manifest, dict)
    assert episode_manifest["props"] == writer_payload["current_scene_node"]["props"]
    assert "prop-unrelated-cup" not in json.dumps(
        episode_manifest, ensure_ascii=False
    )
    assert "repair invariant" in client.instructions[0]


def test_scene_writer_can_semantically_request_upstream_replanning() -> None:
    request = _request(180)
    blueprint = create_story_architecture(request, None).outline
    scene = blueprint.scenes[0]
    output = json.dumps(
        {
            "scene_sequence": scene.sequence,
            "performance_timing_assessment": (
                "当前蓝图要求的内容无法在目标时长内自然完成，需要上游重规划。"
            ),
            "blocks": [],
            "upstream_replan_request": {
                "target": "STORY_ARCHITECT",
                "reason": "当前场景节点已把主角的核心优势改成了普通口才。",
                "evidence": ["本场 narrative_purpose 与 StoryIntent 的戏剧发动机相反"],
                "requested_change": "重写本场及后续节点，让核心设定持续产生可见结果。",
                "preserve": ["保留用户指定的主角能力"],
            },
        },
        ensure_ascii=False,
    )

    result = write_story_scene(
        request,
        blueprint,
        scene,
        [],
        _StaticLanguageClient(output),
    )

    assert result.patch is None
    assert isinstance(result.upstream_replan_request, SceneUpstreamReplanRequest)
    assert (
        result.upstream_replan_request.target
        == SceneUpstreamReplanTarget.STORY_ARCHITECT
    )
    assert result.retry_required is False


def test_normal_revision_does_not_require_verbatim_action_or_dialogue_coverage() -> None:
    request = _request(180)
    outline = create_story_architecture(request, None).outline
    scene = outline.scenes[0]
    speaker = outline.characters[0].name
    patch = EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text=f"【{scene.heading}】\n他扫过人群，把最终结果推到桌面中央。",
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=[PlannedDialogueLine(speaker=speaker, text="都看清楚了吗？")],
    )
    result = revise_story_scene(
        request,
        outline,
        scene,
        "让动作更自然",
        [scene.sequence],
        [],
        SceneDurationRevisionConstraint(
            episode_target_seconds=request.resolved_target_duration_seconds(),
            current_episode_scene_total_seconds=sum(
                item.target_duration_seconds for item in outline.scenes
            ),
        ),
        _StaticLanguageClient(patch.model_dump_json()),
    )

    assert result.retry_required is False
    assert result.patch == patch


def test_scene_revision_binds_core_speakers_by_id_not_display_name() -> None:
    request = _request(180)
    outline = create_story_architecture(request, None).outline
    scene = outline.scenes[0]
    character_id = outline.characters[0].character_id
    assert character_id is not None
    patch = EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text=f"【{scene.heading}】\n画面里的主角与临时店员各说一句。",
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=[
            PlannedDialogueLine(
                speaker="镜头中的少年",
                speaker_character_id=character_id,
                text="我来算。",
            ),
            PlannedDialogueLine(speaker="临时店员", text="请。"),
        ],
    )
    constraint = SceneDurationRevisionConstraint(
        episode_target_seconds=request.resolved_target_duration_seconds(),
        current_episode_scene_total_seconds=sum(
            item.target_duration_seconds for item in outline.scenes
        ),
    )

    accepted = revise_story_scene(
        request,
        outline,
        scene,
        "让临时店员回应主角",
        [scene.sequence],
        [],
        constraint,
        _StaticLanguageClient(patch.model_dump_json()),
    )
    invalid_patch = patch.model_copy(deep=True)
    invalid_patch.lines[0].speaker_character_id = "character-missing"
    rejected = revise_story_scene(
        request,
        outline,
        scene,
        "让临时店员回应主角",
        [scene.sequence],
        [],
        constraint,
        _StaticLanguageClient(invalid_patch.model_dump_json()),
    )

    assert accepted.retry_required is False
    assert accepted.patch.lines[0].speaker == "镜头中的少年"
    assert accepted.patch.lines[1].speaker_character_id is None
    assert rejected.retry_required is True
    assert rejected.retry_feedback is not None
    assert "character-missing" in rejected.retry_feedback


def test_script_locked_dialogue_still_requires_exact_text_in_screenplay() -> None:
    request = CreateProjectRequest(
        name="锁定剧本",
        source_mode=SourceMode.SCRIPT_LOCKED,
        target_duration_seconds=180,
        input_text="林夏：这句必须原样保留。",
        run_mode=RunMode.GUIDED,
    )
    blueprint = create_story_architecture(_request(180), None).outline
    scene = blueprint.scenes[0].model_copy(update={"dialogue_line_indices": [1]})
    locked_line = PlannedDialogueLine(speaker="林夏", text="这句必须原样保留。")
    locked_blueprint = blueprint.model_copy(
        update={"lines": [locked_line], "scenes": [scene, *blueprint.scenes[1:]]}
    )
    patch = EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text=f"【{scene.heading}】\n林夏表达了同样的意思，但没有说原句。",
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=[locked_line],
    )

    with pytest.raises(ValueError, match="omitted locked dialogue"):
        _validate_scene_patch(request, locked_blueprint, scene, patch)


def test_scope_recommendation_compacts_search_text_but_keeps_long_edit_valid() -> None:
    request = _request(180)
    outline = create_story_architecture(request, None).outline
    instruction = "局部修改指令" * 1000
    typed_sequence = outline.scenes[-1].sequence

    result = recommend_script_revision_context(
        instruction,
        outline,
        [],
        None,
        candidate_scene_sequences=[typed_sequence],
    )

    assert len(instruction) > 4000
    assert result.plan is not None
    assert result.used_typed_candidates is True
    assert result.plan.affected_scene_sequences == [typed_sequence]
    assert len(result.plan.retrieval_query) == 4000
    assert result.plan.user_overridable is True

    failed_agent_result = recommend_script_revision_context(
        instruction,
        outline,
        [],
        _StaticLanguageClient("not valid JSON"),
        candidate_scene_sequences=[typed_sequence],
    )
    assert failed_agent_result.plan is not None
    assert failed_agent_result.used_typed_candidates is True
    assert failed_agent_result.plan.affected_scene_sequences == [typed_sequence]


def test_scope_recommendation_waits_without_a_semantic_scene_scope() -> None:
    outline = create_story_architecture(_request(180), None).outline

    result = recommend_script_revision_context(
        "重新整理这段剧情",
        outline,
        [],
        None,
    )

    assert result.plan is None
    assert result.retry_required is True
    assert result.retry_feedback is not None


def test_scene_patch_merge_does_not_regenerate_unrelated_scenes() -> None:
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    initial = assemble_written_scenes(
        blueprint,
        [_scene_patch(blueprint, scene.sequence, "初稿") for scene in blueprint.scenes],
    )
    before_first = scene_dialogue_lines(initial, initial.scenes[0])
    replacement = _scene_patch(initial, 2, "局部修改")

    revised = apply_episode_outline_patch(
        initial,
        EpisodeOutlinePatch(
            base_revision_round=0,
            rationale="用户只修改第二场",
            scene_patches=[replacement],
        ),
    )

    assert scene_dialogue_lines(revised, revised.scenes[0]) == before_first
    assert scene_dialogue_lines(revised, revised.scenes[1])[0].text.endswith("局部修改")
    assert [scene.sequence for scene in revised.scenes] == [
        scene.sequence for scene in initial.scenes
    ]


def test_versioned_script_search_is_isolated_by_run_and_revision(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    outline = assemble_written_scenes(
        blueprint,
        [_scene_patch(blueprint, scene.sequence, "搜索文本") for scene in blueprint.scenes],
    )
    run_one_docs = build_planning_script_documents(
        run_id="run-one",
        revision_round=0,
        outline=outline,
        created_at="2026-08-25T00:00:00Z",
    )
    run_two_docs = build_planning_script_documents(
        run_id="run-two",
        revision_round=0,
        outline=outline.model_copy(update={"title": "另一个剧本"}),
        created_at="2026-08-25T00:00:00Z",
    )
    repository.replace_planning_script_documents("run-one", 0, run_one_docs)
    repository.replace_planning_script_documents("run-two", 0, run_two_docs)

    hits = repository.search_planning_script_documents(
        "run-one",
        0,
        "第 2 场因果链搜索文本",
        limit=8,
        include_story_bible=False,
    )

    assert hits
    assert all(hit.document.run_id == "run-one" for hit in hits)
    assert all(
        hit.document.kind == PlanningScriptDocumentKind.SCENE for hit in hits
    )
    assert any(hit.document.scene_sequence == 2 for hit in hits)
    assert json.loads(hits[0].document.model_dump_json())["revision_round"] == 0


def test_scene_search_metadata_uses_typed_character_and_location_bindings() -> None:
    outline = create_story_architecture(_request(180), None).outline.model_copy(
        deep=True,
        update={
            "props": [PlannedProp(name="旧计算器", appearance="灰色塑料外壳")],
        },
    )
    scene = outline.scenes[0]
    character = outline.characters[0]
    assert character.character_id is not None
    patch = _scene_patch(outline, scene.sequence, "结构化绑定").model_copy(
        deep=True,
        update={
            "screenplay_text": (
                f"【{scene.heading}】\n{character.name}站在旧计算器旁，"
                "屏幕上的昵称并不代表另一个角色。"
            ),
            "lines": [
                PlannedDialogueLine(
                    speaker="屏幕昵称",
                    speaker_character_id=character.character_id,
                    text="绑定的是稳定角色 ID。",
                ),
                PlannedDialogueLine(
                    speaker=character.name,
                    text="这句没有角色 ID。",
                ),
            ],
        },
    )
    revised = apply_episode_outline_patch(
        outline,
        EpisodeOutlinePatch(
            base_revision_round=0,
            rationale="验证修订检索元数据。",
            scene_patches=[patch],
        ),
    )

    documents = build_planning_script_documents(
        run_id="typed-index",
        revision_round=1,
        outline=revised,
        created_at="2026-09-03T00:00:00Z",
    )
    scene_document = next(
        document
        for document in documents
        if document.kind == PlanningScriptDocumentKind.SCENE
    )

    assert scene_document.character_names == [character.name]
    assert scene_document.location_names == scene.location_names
    assert scene_document.prop_names == []
