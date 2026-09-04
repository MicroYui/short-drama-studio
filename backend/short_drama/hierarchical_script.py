"""Scene-local screenplay writing for every supported episode duration.

The global Story Architect owns a compact blueprint.  This module expands one
scene node at a time with a bounded context window, so a three-minute episode and
a thirty-minute episode use the same mechanism and differ only in node count.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json

from pydantic import Field, model_validator

from short_drama.models import (
    AudioKind,
    CreateProjectRequest,
    DomainModel,
    SourceMode,
    StoryIntent,
    StoryRevisionVerification,
    StorySourceContract,
)
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedDialogueLine,
    PlannedSceneContract,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)
from short_drama.script_documents import (
    EpisodeOutlinePatch,
    EpisodeScenePatch,
    PlanningScriptSearchHit,
    SceneDurationRevisionConstraint,
    ScriptRevisionContextPlan,
    ScriptRevisionScope,
    apply_episode_outline_patch,
    build_episode_manifest,
)


def _duration_milliseconds(value: float) -> int:
    return int(round(value * 1000))


@dataclass(frozen=True)
class SceneWritingResult:
    patch: EpisodeScenePatch | None
    upstream_replan_request: SceneUpstreamReplanRequest | None
    used_fallback: bool
    repaired: bool
    retry_required: bool
    retry_feedback: str | None
    warnings: list[str]


@dataclass(frozen=True)
class SceneRevisionResult:
    patch: EpisodeScenePatch
    revised: bool
    retry_required: bool
    retry_feedback: str | None
    warnings: list[str]


@dataclass(frozen=True)
class ScriptRevisionContextResult:
    plan: ScriptRevisionContextPlan | None
    used_typed_candidates: bool
    retry_required: bool
    retry_feedback: str | None
    warnings: list[str]


class SceneBlockKind(StrEnum):
    ACTION = "ACTION"
    DIALOGUE = "DIALOGUE"
    VOICEOVER = "VOICEOVER"


class SceneSpeakerScope(StrEnum):
    CORE = "CORE"
    SCENE_EXTRA = "SCENE_EXTRA"


class SceneUpstreamReplanTarget(StrEnum):
    """The earlier creative decision the Scene Writer asks to reconsider."""

    STORY_ORIENTATION = "STORY_ORIENTATION"
    STORY_ARCHITECT = "STORY_ARCHITECT"


class SceneUpstreamReplanRequest(DomainModel):
    """A semantic handoff when expanding the current blueprint would be wrong."""

    target: SceneUpstreamReplanTarget
    reason: str = Field(min_length=1, max_length=1600)
    evidence: list[str] = Field(min_length=1, max_length=12)
    requested_change: str = Field(min_length=1, max_length=1600)
    preserve: list[str] = Field(default_factory=list[str], max_length=12)


class SceneScriptBlock(DomainModel):
    """One chronological screenplay block owned by the Scene Writer."""

    kind: SceneBlockKind
    text: str = Field(min_length=1, max_length=1600)
    speaker: str | None = Field(default=None, min_length=1, max_length=80)
    speaker_scope: SceneSpeakerScope | None = None
    speaker_character_id: str | None = Field(default=None, max_length=200)
    covers_action_indices: list[int] = Field(
        default_factory=list[int],
        max_length=24,
        description=(
            "Optional trace from a finished screenplay block back to planning intents; "
            "it is not an exhaustive coverage checklist."
        ),
    )

    @model_validator(mode="after")
    def validate_block(self) -> SceneScriptBlock:
        if self.kind == SceneBlockKind.ACTION:
            if (
                self.speaker is not None
                or self.speaker_scope is not None
                or self.speaker_character_id is not None
            ):
                raise ValueError("Action blocks cannot declare a speaker")
        elif self.speaker is None:
            raise ValueError("Dialogue and voiceover blocks require a speaker")
        elif self.speaker_scope is None:
            raise ValueError("Spoken blocks require speaker_scope")
        elif (
            self.speaker_scope == SceneSpeakerScope.CORE
            and self.speaker_character_id is None
        ):
            raise ValueError("Core speakers require an explicit speaker_character_id")
        elif (
            self.speaker_scope == SceneSpeakerScope.SCENE_EXTRA
            and self.speaker_character_id is not None
        ):
            raise ValueError("Scene extras cannot bind a global character ID")
        if len(self.covers_action_indices) != len(set(self.covers_action_indices)):
            raise ValueError("A screenplay block cannot repeat an action index")
        return self


class SceneCreativeDraft(DomainModel):
    """Minimal creative output; blueprint-owned fields are merged by code."""

    scene_sequence: int = Field(ge=1, le=500)
    blocks: list[SceneScriptBlock] = Field(
        default_factory=list[SceneScriptBlock],
        max_length=500,
    )
    performance_timing_assessment: str = Field(min_length=1, max_length=1200)
    upstream_replan_request: SceneUpstreamReplanRequest | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> SceneCreativeDraft:
        has_blocks = bool(self.blocks)
        has_replan = self.upstream_replan_request is not None
        if has_blocks == has_replan:
            raise ValueError(
                "Scene Writer must return either screenplay blocks or one upstream "
                "replan request"
            )
        return self


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _compact_error(error: BaseException, *, max_chars: int = 600) -> str:
    return " ".join(str(error).split())[:max_chars]


def compact_script_retrieval_query(value: str, *, max_chars: int = 4000) -> str:
    """Bound search text without truncating the Agent's complete edit instruction."""

    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    separator = " … "
    head_chars = (max_chars - len(separator)) * 2 // 3
    tail_chars = max_chars - len(separator) - head_chars
    return compact[:head_chars] + separator + compact[-tail_chars:]


def _scene_lines_from_outline(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
) -> list[PlannedDialogueLine]:
    return [
        outline.lines[index - 1].model_copy(deep=True)
        for index in scene.dialogue_line_indices
        if 1 <= index <= len(outline.lines)
    ]


def _scene_relevant_props(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
) -> list[dict[str, object]]:
    """Resolve only props explicitly assigned to this scene by Story Architect."""

    props_by_id = {
        prop.prop_id: prop
        for prop in outline.props
        if prop.prop_id is not None
    }
    return [
        {
            "prop_id": prop.prop_id,
            "name": prop.name,
            "appearance": prop.appearance,
        }
        for prop_id in scene.prop_ids
        if (prop := props_by_id.get(prop_id)) is not None
    ]


def compose_scene_screenplay(
    *,
    heading: str,
    playable_actions: list[str],
    lines: list[PlannedDialogueLine],
) -> str:
    """Build a readable chronological fallback preserving every declared beat."""

    blocks = [f"【{heading}】"]
    for index in range(max(len(playable_actions), len(lines))):
        if index < len(playable_actions):
            blocks.append(f"动作/反应/情绪：{playable_actions[index]}")
        if index < len(lines):
            line = lines[index]
            blocks.append(f"{line.speaker}：{line.text}")
    return "\n".join(blocks)


def _scene_patch_from_creative_draft(
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    draft: SceneCreativeDraft,
) -> EpisodeScenePatch:
    if draft.scene_sequence != scene.sequence:
        raise ValueError("Scene Writer returned a draft for another scene")
    valid_action_indices = set(range(1, len(scene.playable_actions) + 1))
    covered_action_indices = {
        action_index
        for block in draft.blocks
        for action_index in block.covers_action_indices
    }
    invalid_action_indices = sorted(covered_action_indices - valid_action_indices)
    if invalid_action_indices:
        raise ValueError(
            "Scene Writer referenced unknown playable action indices: "
            f"invalid={invalid_action_indices}"
        )
    known_character_ids = {
        character.character_id
        for character in blueprint.characters
        if character.character_id is not None
    }
    invalid_core_character_ids = sorted(
        {
            block.speaker_character_id
            for block in draft.blocks
            if block.kind != SceneBlockKind.ACTION
            and block.speaker_scope == SceneSpeakerScope.CORE
            and block.speaker_character_id not in known_character_ids
            and block.speaker_character_id is not None
        }
    )
    if invalid_core_character_ids:
        raise ValueError(
            "Scene Writer referenced undeclared core character IDs: "
            + ", ".join(invalid_core_character_ids)
        )
    screenplay_blocks = [f"【{scene.heading}】"]
    lines: list[PlannedDialogueLine] = []
    for block in draft.blocks:
        if block.kind == SceneBlockKind.ACTION:
            screenplay_blocks.append(block.text)
            continue
        assert block.speaker is not None
        audio_kind = (
            AudioKind.VOICEOVER
            if block.kind == SceneBlockKind.VOICEOVER
            else AudioKind.DIALOGUE
        )
        lines.append(
            PlannedDialogueLine(
                speaker=block.speaker,
                speaker_character_id=block.speaker_character_id,
                text=block.text,
                audio_kind=audio_kind,
            )
        )
        screenplay_blocks.append(f"{block.speaker}：{block.text}")
    return EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text="\n".join(screenplay_blocks),
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        prop_ids=list(scene.prop_ids),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=lines,
        performance_timing_assessment=draft.performance_timing_assessment,
    )


def _scene_patch_from_contract(
    scene: PlannedSceneContract,
    lines: list[PlannedDialogueLine],
) -> EpisodeScenePatch:
    return EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        screenplay_text=(
            scene.screenplay_text
            or compose_scene_screenplay(
                heading=scene.heading,
                playable_actions=list(scene.playable_actions),
                lines=lines,
            )
        ),
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        prop_ids=list(scene.prop_ids),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=[line.model_copy(deep=True) for line in lines],
    )


def _fallback_scene_patch(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
) -> EpisodeScenePatch:
    existing = _scene_lines_from_outline(outline, scene)
    if existing or not outline.characters:
        return _scene_patch_from_contract(scene, existing)
    first = outline.characters[0]
    second = outline.characters[min(1, len(outline.characters) - 1)]
    desired = max(1, min(120, round(scene.target_duration_seconds / 8)))
    templates = (
        (first, f"这一步已经证明，{scene.narrative_purpose}不能再拖。"),
        (second, "你现在看到的，只是结果，不是他们动手的原因。"),
        (first, "那就把真正能改变结果的东西拿出来。"),
        (second, "可以，但你做出选择以后，就没有回头路了。"),
        (first, "我怕的不是代价，是继续按他们的规则输下去。"),
        (second, "那就让他们亲眼看见，这个后果是怎么发生的。"),
    )
    lines = [
        PlannedDialogueLine(
            speaker=templates[index % len(templates)][0].name,
            speaker_character_id=(
                templates[index % len(templates)][0].character_id
            ),
            text=(
                templates[index % len(templates)][1]
                if index < len(templates)
                else f"第 {scene.sequence} 场的下一个选择已经被逼到眼前。"
            ),
        )
        for index in range(desired)
    ]
    return _scene_patch_from_contract(scene, lines)


def _scene_writer_prompt(
    request: CreateProjectRequest,
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    previous_patches: list[EpisodeScenePatch],
    retry_feedback: str | None = None,
    *,
    story_intent: StoryIntent | None = None,
    source_contract: StorySourceContract | None = None,
    selected_skill_prompt: str = "",
) -> tuple[str, str]:
    resolved_story_intent = (
        story_intent if story_intent is not None else blueprint.story_intent
    )
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER,),
    )
    older_summaries = [
        {
            "scene_sequence": patch.scene_sequence,
            "outcome": patch.summary,
            "known_facts": patch.continuity_out.known_facts,
            "open_threads": patch.continuity_out.open_threads,
        }
        for patch in previous_patches[-5:-1]
    ]
    next_nodes = [
        {
            "sequence": item.sequence,
            "summary": item.summary,
            "narrative_purpose": item.narrative_purpose,
        }
        for item in blueprint.scenes[scene.sequence : scene.sequence + 2]
    ]
    instructions = (
        "你是面向当前视频制作媒介的场景编剧 Agent（Scene Writer Agent）。"
        "只返回 JSON，并严格匹配 SceneCreativeDraft；只把当前场景节点扩写成按演出顺序排列的 "
        "ACTION、DIALOGUE、VOICEOVER blocks。heading、summary、duration、locations 与 continuity "
        "由代码从蓝图合并，不要在输出中重复，也不要改写全局蓝图、其他场次、角色、地点、道具或风格。"
        "本阶段不写镜头、运镜、关键帧、剪辑或供应商提示词。让可见动作、反应、调度、情绪变化和停顿"
        "直接承担状态变化；对白只说当下这些人物自然会对彼此说的话。不要为了显式说明而把场景契约、"
        "连续性摘要、关系状态标签或制作指令念成对白。视听节奏快不等于对白必须电报化或口号化；"
        "对白量应与场景时长大致相称，但没有固定行数，也不要求每句都增加新信息。避免复述 summary 的说明性对白。"
        "返回 performance_timing_assessment：先在脑中按成稿顺序，以自然语速和真实动作完整演一遍当前场。"
        "除非 blocks 本身明确写成同时发生、蒙太奇或可连续掠过的画面，否则把对白、辨认、动作、反应和停顿"
        "视为依次占用表演时间，不要替成稿假设尚未写出的快速剪辑。给出可信的表演时长范围，指出最耗时的"
        "顺序节拍，以及你已删掉、合并或改为同时发生的内容；若成稿仍与目标时长明显冲突，应实际继续取舍"
        "或请求上游重规划，而不是只在自评中宣称可以装入。不得只复述秒数标签，也不得假设演员持续赶词。这个判断用于"
        "下一位 Agent 理解创作取舍，不由代码按字数、语速或真假值设门禁。"
        "延续精简上下文中的已知事实、关系位置和未结线索，不要提前解决后续场景。先确认入场时已经成立的"
        "群众态度、人物关系、资源与权限，再只写本场真正新增的变化；若一次认可、让位、授予或交接只是在"
        "换措辞重演既有状态，应压成过渡或让它产生新的实际后果。blocks 必须是最终演出顺序，"
        "自然交错动作、反应、情绪转折、停顿与对白。playable_actions 是 Story Architect 给出的语义意图，"
        "不是必须逐字写进 screenplay 的文本清单；可以合并、压缩、重排，或用等价的可见行为完成，"
        "但要守住本场 narrative_purpose 与 continuity_out。covers_action_indices 只是可选的意图来源标注："
        "能明确对应时填写，不确定时可留空，不要求逐项全覆盖。已声明的常驻角色标为 CORE，"
        "并且用 speaker_character_id 显式引用 episode_context.characters 中的稳定 ID；"
        "确实只在本场出现的群众、店员等小角色可标为 SCENE_EXTRA，避免无意升级成全局重资产。"
        "current_scene_node.props 只包含 Story Architect 明确分配给本场的道具，"
        "prop_id 是稳定身份，appearance 只解释稳定外观，当前用法与状态以 "
        "prop_state_context 和本场动作为准。这些元数据不能替代观众在 screenplay blocks 中"
        "实际看到的关键动作、条件与因果。"
        "如果问题只是当前场难写，仍应优先通过取舍和合并完成当前场；但如果你发现 StoryIntent 本身"
        "误解了用户，或当前全局蓝图已经与 StoryIntent、用户承诺、自然表演所需的目标时长发生实质冲突，"
        "继续扩写只会固化错误方向，则不要硬写。"
        "此时返回 upstream_replan_request，并用 target 明确请求重新做 STORY_ORIENTATION 或 "
        "STORY_ARCHITECT，提供当前文本证据、希望改变的内容和应保留内容；不要同时返回 blocks。"
        "只有 source_mode=SCRIPT_LOCKED 时，locked_scene_lines 中用户锁定的对白或文字才必须原样保留；"
        "普通创作模式不做逐字复述。始终保护 explicit_story_source_contract 中的用户承诺、禁止项与"
        "authoritative_user_overrides；开放创作选择不是必须照抄的文字。"
        f"{skill_prompt.text} "
        "最后一场要先兑现 ending_contract 的 episode_payoff 与 final_button；只有 SERIES 才能在此后表达"
        "有依据的 continuation_hook。结果是当前场的创作草稿，不是整集重写；不要添加 schema 之外的字段。"
    )
    if selected_skill_prompt.strip():
        instructions += (
            " 本次由调度 Agent 按当前创作目标选择的组合技能如下；把它作为创作方法使用，不要把它改写成固定模板："
            + selected_skill_prompt.strip()
        )
    if retry_feedback:
        instructions += (
            " 当前场景的上一次尝试失败了。先修复 previous_retry_feedback 指出的具体问题，"
            "再做其他必要调整；不要跳过或替换这一场。"
        )
    locked_lines = _scene_lines_from_outline(blueprint, scene)
    previous_scene = previous_patches[-1] if previous_patches else None
    payload: dict[str, object] = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "explicit_story_source_contract": (
            source_contract.model_dump(mode="json")
            if source_contract is not None
            else None
        ),
        "episode_context": {
            "title": blueprint.title,
            "story_promise": blueprint.story_summary,
            "characters": [
                {
                    "character_id": character.character_id,
                    "name": character.name,
                    "performance": character.performance,
                    "voice_identity": character.voice_identity,
                }
                for character in blueprint.characters
            ],
            "style": blueprint.style.model_dump(mode="json"),
            "ending_contract": (
                blueprint.ending_contract.model_dump(mode="json")
                if blueprint.ending_contract is not None
                else None
            ),
        },
        "current_scene_node": {
            "sequence": scene.sequence,
            "heading": scene.heading,
            "summary": scene.summary,
            "narrative_purpose": scene.narrative_purpose,
            "target_duration_seconds": scene.target_duration_seconds,
            "location_names": scene.location_names,
            "props": _scene_relevant_props(blueprint, scene),
            "prop_state_context": {
                "continuity_in": list(scene.continuity_in.prop_states),
                "continuity_out": list(scene.continuity_out.prop_states),
            },
            "playable_actions": [
                {"index": index, "intent": action}
                for index, action in enumerate(scene.playable_actions, start=1)
            ],
            "continuity_in": scene.continuity_in.model_dump(mode="json"),
            "continuity_out": scene.continuity_out.model_dump(mode="json"),
        },
        "locked_scene_lines": [line.model_dump(mode="json") for line in locked_lines],
        "previous_scene_boundary": (
            {
                "scene_sequence": previous_scene.scene_sequence,
                "screenplay_tail": previous_scene.screenplay_text[-3000:],
                "continuity_out": previous_scene.continuity_out.model_dump(mode="json"),
            }
            if previous_scene is not None
            else None
        ),
        "older_scene_outcomes": older_summaries,
        "next_scene_nodes": next_nodes,
        "output_schema": SceneCreativeDraft.model_json_schema(),
    }
    if retry_feedback:
        payload["previous_retry_feedback"] = retry_feedback
    if resolved_story_intent is not None:
        payload["story_intent"] = resolved_story_intent.model_dump(mode="json")
    return instructions, json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _validate_scene_patch(
    request: CreateProjectRequest,
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    patch: EpisodeScenePatch,
) -> None:
    immutable_pairs = (
        ("scene_sequence", patch.scene_sequence, scene.sequence),
        ("heading", patch.heading, scene.heading),
        ("summary", patch.summary, scene.summary),
        ("narrative_purpose", patch.narrative_purpose, scene.narrative_purpose),
        ("playable_actions", patch.playable_actions, scene.playable_actions),
        ("target_duration_seconds", patch.target_duration_seconds, scene.target_duration_seconds),
        ("location_names", patch.location_names, scene.location_names),
        ("prop_ids", patch.prop_ids, scene.prop_ids),
        ("continuity_in", patch.continuity_in, scene.continuity_in),
        ("continuity_out", patch.continuity_out, scene.continuity_out),
    )
    changed = [name for name, actual, expected in immutable_pairs if actual != expected]
    if changed:
        raise ValueError(
            "Scene Writer changed blueprint-owned fields: " + ", ".join(changed)
        )
    screenplay = patch.screenplay_text.strip()
    if not screenplay:
        raise ValueError("Scene Writer omitted screenplay_text")
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        expected = _scene_lines_from_outline(blueprint, scene)
        if patch.lines != expected:
            raise ValueError("Scene Writer changed locked dialogue")
        missing_locked_text = [line.text for line in expected if line.text not in screenplay]
        if missing_locked_text:
            raise ValueError(
                "Scene Writer omitted locked dialogue from screenplay_text: "
                f"missing_locked_text={missing_locked_text}"
            )


def _parse_scene_writer_output(
    request: CreateProjectRequest,
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    output: str,
) -> EpisodeScenePatch | SceneUpstreamReplanRequest:
    raw = _strip_json_fence(output)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Scene Writer output must be one JSON object")
    if "blocks" in payload or "upstream_replan_request" in payload:
        draft = SceneCreativeDraft.model_validate(payload)
        if draft.upstream_replan_request is not None:
            return draft.upstream_replan_request
        patch = _scene_patch_from_creative_draft(blueprint, scene, draft)
    else:
        # Compatibility for persisted tests and in-flight providers that still
        # return the former wide patch. New prompts request SceneCreativeDraft.
        patch = EpisodeScenePatch.model_validate(payload)
    _validate_scene_patch(request, blueprint, scene, patch)
    return patch


def write_story_scene(
    request: CreateProjectRequest,
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    previous_patches: list[EpisodeScenePatch],
    client: LanguageProviderClient | None,
    retry_feedback: str | None = None,
    *,
    story_intent: StoryIntent | None = None,
    source_contract: StorySourceContract | None = None,
    selected_skill_prompt: str = "",
) -> SceneWritingResult:
    """Write one recoverable scene using recent detail and older summaries."""

    fallback = _fallback_scene_patch(blueprint, scene)
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return SceneWritingResult(
            patch=fallback,
            upstream_replan_request=None,
            used_fallback=False,
            repaired=False,
            retry_required=False,
            retry_feedback=None,
            warnings=[],
        )
    if client is None:
        return SceneWritingResult(
            patch=fallback,
            upstream_replan_request=None,
            used_fallback=True,
            repaired=False,
            retry_required=False,
            retry_feedback=None,
            warnings=[
                f"GPT-5.6-sol 未配置，第 {scene.sequence} 场已使用本地可恢复剧本补全。"
            ],
        )
    instructions, input_text = _scene_writer_prompt(
        request,
        blueprint,
        scene,
        previous_patches,
        retry_feedback,
        story_intent=story_intent,
        source_contract=source_contract,
        selected_skill_prompt=selected_skill_prompt,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=7000,
        )
        decision = _parse_scene_writer_output(
            request,
            blueprint,
            scene,
            output,
        )
        patch = decision if isinstance(decision, EpisodeScenePatch) else None
        upstream_replan_request = (
            decision if isinstance(decision, SceneUpstreamReplanRequest) else None
        )
        return SceneWritingResult(
            patch=patch,
            upstream_replan_request=upstream_replan_request,
            used_fallback=False,
            repaired=False,
            retry_required=False,
            retry_feedback=None,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return SceneWritingResult(
            patch=fallback,
            upstream_replan_request=None,
            used_fallback=False,
            repaired=False,
            retry_required=True,
            retry_feedback=_compact_error(error),
            warnings=[],
        )


def assemble_written_scenes(
    blueprint: EpisodeOutline,
    patches: list[EpisodeScenePatch],
) -> EpisodeOutline:
    """Assemble a complete screenplay after every scene node has one patch."""

    expected = [scene.sequence for scene in blueprint.scenes]
    actual = [patch.scene_sequence for patch in patches]
    if actual != expected:
        raise ValueError(
            f"Scene-writing patches do not cover the blueprint in order: {actual} != {expected}"
        )
    return apply_episode_outline_patch(
        blueprint,
        EpisodeOutlinePatch(
            base_revision_round=0,
            rationale="Assemble scene-local screenplay drafts from the shared blueprint",
            scene_patches=patches,
            preserve=["global blueprint", "scene order", "duration allocation"],
        ),
    )


def recommend_script_revision_context(
    instruction: str,
    outline: EpisodeOutline,
    search_hits: list[PlanningScriptSearchHit],
    client: LanguageProviderClient | None,
    *,
    retrieval_query: str | None = None,
    candidate_scene_sequences: list[int] | None = None,
) -> ScriptRevisionContextResult:
    """Recommend an editable local scope; this is advice, never a hard gate."""

    typed_candidates = list(dict.fromkeys(candidate_scene_sequences or []))
    bounded_retrieval_query = compact_script_retrieval_query(
        retrieval_query or instruction
    )
    typed_plan = (
        ScriptRevisionContextPlan(
            scope=ScriptRevisionScope.SCENE_PATCH,
            affected_scene_sequences=typed_candidates,
            retrieval_query=bounded_retrieval_query,
            rationale=(
                "沿用 Story Repair Planner 已声明的结构化场次范围；"
                "未从自由文本检索结果推测新的修订路线。"
            ),
            preserve=["未选中场次", "全局资产契约", "目标总时长"],
        )
        if typed_candidates
        else None
    )
    if client is None:
        if typed_plan is not None:
            return ScriptRevisionContextResult(
                plan=typed_plan,
                used_typed_candidates=True,
                retry_required=False,
                retry_feedback=None,
                warnings=[
                    "Revision Scope Agent 未配置；已保留 Story Repair Planner "
                    "明确声明的场次范围。"
                ],
            )
        return ScriptRevisionContextResult(
            plan=None,
            used_typed_candidates=False,
            retry_required=True,
            retry_feedback=(
                "Revision Scope Agent 未配置，且 Story Repair Planner "
                "未声明可执行的场次范围。"
            ),
            warnings=[],
        )
    instructions = (
        "你是剧本修改范围 Agent（script-change scope Agent）。只返回 JSON，并严格匹配 "
        "ScriptRevisionContextPlan。根据用户修改意图，推荐能够完整表达这次修改的最小现有场次集合。"
        "只要通过局部场景 patch 可以完成，就使用 SCENE_PATCH；只有标题、核心前提、全局角色或地点契约、"
        "总时长，或大部分因果主线都必须改变时，才使用 FULL_OUTLINE。此结果只是建议，"
        "user_overridable 必须保持 true。不得虚构 scene ID。retrieval_query 不得超过 4000 字符；"
        "它是精简检索表达式，不是完整复制用户修改指令。"
    )
    payload = {
        "user_instruction": instruction,
        "retrieval_query": bounded_retrieval_query,
        "episode_manifest": build_episode_manifest(outline),
        "retrieved_candidates": [
            {
                "scene_sequence": hit.document.scene_sequence,
                "title": hit.document.title,
                "summary": hit.document.summary,
                "matched_by": hit.matched_by,
            }
            for hit in search_hits
        ],
        "output_schema": ScriptRevisionContextPlan.model_json_schema(),
    }
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            image_urls=[],
            max_output_tokens=3000,
        )
        plan = ScriptRevisionContextPlan.model_validate_json(_strip_json_fence(output))
        known = {scene.sequence for scene in outline.scenes}
        unknown = sorted(set(plan.affected_scene_sequences) - known)
        if unknown:
            raise ValueError(f"Scope Agent selected unknown scenes: {unknown}")
        return ScriptRevisionContextResult(
            plan=plan,
            used_typed_candidates=False,
            retry_required=False,
            retry_feedback=None,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        feedback = _compact_error(error)
        if typed_plan is not None:
            return ScriptRevisionContextResult(
                plan=typed_plan,
                used_typed_candidates=True,
                retry_required=False,
                retry_feedback=None,
                warnings=[
                    "Revision Scope Agent 未完成；已保留 Story Repair Planner "
                    f"明确声明的场次范围。{feedback}"
                ],
            )
        return ScriptRevisionContextResult(
            plan=None,
            used_typed_candidates=False,
            retry_required=True,
            retry_feedback=feedback,
            warnings=[],
        )


def revise_story_scene(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    instruction: str,
    selected_scene_sequences: list[int],
    related_hits: list[PlanningScriptSearchHit],
    duration_constraint: SceneDurationRevisionConstraint,
    client: LanguageProviderClient | None,
    retry_feedback: str | None = None,
    *,
    story_intent: StoryIntent | None = None,
    selected_skill_prompt: str = "",
    previous_verification: StoryRevisionVerification | None = None,
) -> SceneRevisionResult:
    """Create one independently applicable scene patch for a user-directed edit."""

    resolved_story_intent = (
        story_intent if story_intent is not None else outline.story_intent
    )
    original = _scene_patch_from_contract(
        scene,
        _scene_lines_from_outline(outline, scene),
    )
    if client is None:
        return SceneRevisionResult(
            patch=original,
            revised=False,
            retry_required=True,
            retry_feedback="Language provider is not configured",
            warnings=[],
        )
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER,),
    )
    duration_instruction = (
        "严格保持 target_duration_seconds 不变。"
        if not duration_constraint.allow_change
        else (
            "只有为了执行已明确授权的场景时长重分配，才可以改变 target_duration_seconds。"
            + (
                "这是最后一个可调整场景：把 target_duration_seconds 精确设为 "
                f"{duration_constraint.required_scene_target_seconds:g}，使所有场景目标时长之和"
                "等于整集目标时长。"
                if duration_constraint.required_scene_target_seconds is not None
                else (
                    "后续已授权场景会共同吸收互补时长；为它们留下正的总时长空间，"
                    "不要改变无关场景的目标时长。"
                )
            )
        )
    )
    instructions = (
        "你是场景局部剧本修订 Agent（scene-local screenplay revision Agent）。只返回 JSON，并严格匹配 "
        "EpisodeScenePatch。结合所给的已选场景上下文，只在当前场景落实用户修改指令；"
        "保持 scene_sequence 不变。"
        f"{duration_instruction} 地点只能使用全局已声明的地点。"
        "保持 prop_ids 不变；episode_manifest.props 只提供本场已关联道具的稳定 ID 和外观，"
        "当前状态在 current_scene.continuity_in/out 中。道具简介、场景 summary、continuity "
        "或 repair invariant 只是创作上下文，不能替代观众在 screenplay_text 中实际看到的因果。"
        "已声明的核心角色必须用 speaker_character_id 引用 episode_manifest.characters "
        "中的稳定 ID；确实只属于本场的群众、店员或其他临时说话者可不绑定全局 ID。"
        "当用户指令确有需要时，可以修改本场的 heading、summary、narrative_purpose、playable_actions、"
        "连续性衔接、对白与 screenplay_text。screenplay_text 应按演出顺序自然呈现。"
        "playable_actions 是场景语义意图，不要求逐字出现在 screenplay_text 中；可以通过合并、压缩、"
        "重排或等价的可见动作来落实。若修复要求合并重复证明、反应或交接，不能只把原动作缩句、并排写成"
        "蒙太奇或宣称同时发生；先判断它们是否在证明同一状态，保留最有力的一次，其余真正删除，或转化为"
        "新的选择、代价和后果。按修订后成稿顺序自然演读；未明确写成同时发生的动作、辨认、反应和停顿都"
        "依次占用时间，不用假想快切替文本完成时长修复。普通创作模式下，对白也不以字符串逐字覆盖作为质量判断；"
        "由你保持 lines 与 screenplay_text 的语义一致。只有 source_mode=SCRIPT_LOCKED 时，"
        "用户锁定的对白或文字必须原样保留。不要改变整集标题、全局角色/资产/风格、无关场景、"
        "镜头、媒体或时间线，也不要为了处理下游调度或生成问题而补写解释。"
        "必须填写 performance_timing_assessment，只评价本次实际返回的 screenplay_text：说明你实际删除或"
        "合并了什么、哪些动作必须依次发生、哪些确实能同时发生，并给出自然表演范围。若仍装不下，应在本次"
        "patch 中继续取舍，而不是描述未来还需删除的内容。该字段只进入 trace 和后续语义判断，不触发代码阈值。"
        "若提供 previous_revision_verification，只处理其中明确落在当前场次的 violation；"
        "把 violation.issue_id 当作连续语义身份，保留它已确认成立的内容，不扩大返修范围。"
        f"{skill_prompt.text}"
    )
    if selected_skill_prompt.strip():
        instructions += (
            " 本次由调度 Agent 按当前创作目标选择的组合技能如下；把它作为创作方法使用，不要套成固定模板："
            + selected_skill_prompt.strip()
        )
    if retry_feedback:
        instructions += (
            " 当前场景的上一次修订失败了。先修复 previous_retry_feedback 指出的具体问题，"
            "再做其他必要调整；不要跳过或替换这一场。"
        )
    episode_manifest = build_episode_manifest(outline)
    episode_manifest["props"] = _scene_relevant_props(outline, scene)
    payload: dict[str, object] = {
        "source_mode": request.source_mode.value,
        "user_instruction": instruction,
        "episode_manifest": episode_manifest,
        "selected_scene_sequences": selected_scene_sequences,
        "current_scene": original.model_dump(mode="json"),
        "duration_constraint": duration_constraint.model_dump(mode="json"),
        "previous_revision_verification": (
            previous_verification.model_dump(mode="json")
            if previous_verification is not None
            else None
        ),
        "related_script_documents": [
            {
                "scene_sequence": hit.document.scene_sequence,
                "title": hit.document.title,
                "summary": hit.document.summary,
                "body": hit.document.body,
            }
            for hit in related_hits[:8]
        ],
        "output_schema": EpisodeScenePatch.model_json_schema(),
    }
    if retry_feedback:
        payload["previous_retry_feedback"] = retry_feedback
    if resolved_story_intent is not None:
        payload["story_intent"] = resolved_story_intent.model_dump(mode="json")
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            image_urls=[],
            max_output_tokens=7000,
        )
        patch = EpisodeScenePatch.model_validate_json(_strip_json_fence(output))
        if patch.scene_sequence != scene.sequence:
            raise ValueError("Scene revision changed scene_sequence")
        if (
            not duration_constraint.allow_change
            and patch.target_duration_seconds != scene.target_duration_seconds
        ):
            raise ValueError("Scene revision changed target_duration_seconds")
        required_duration = duration_constraint.required_scene_target_seconds
        if required_duration is not None and _duration_milliseconds(
            patch.target_duration_seconds
        ) != _duration_milliseconds(required_duration):
            raise ValueError(
                "Final duration compensation requires target_duration_seconds="
                f"{required_duration:g}; received={patch.target_duration_seconds:g}; "
                f"episode_target={duration_constraint.episode_target_seconds:g}"
            )
        known_locations = {location.name for location in outline.locations}
        if not set(patch.location_names) <= known_locations:
            raise ValueError("Scene revision used undeclared locations")
        if patch.prop_ids != scene.prop_ids:
            raise ValueError("Scene revision changed prop_ids")
        known_character_ids = {
            character.character_id
            for character in outline.characters
            if character.character_id is not None
        }
        invalid_speaker_character_ids = sorted(
            {
                line.speaker_character_id
                for line in patch.lines
                if line.speaker_character_id is not None
                and line.speaker_character_id not in known_character_ids
            }
        )
        if invalid_speaker_character_ids:
            raise ValueError(
                "Scene revision used undeclared core character IDs: "
                + ", ".join(invalid_speaker_character_ids)
            )
        screenplay = patch.screenplay_text.strip()
        if not screenplay:
            raise ValueError("Scene revision omitted screenplay_text")
        if request.source_mode == SourceMode.SCRIPT_LOCKED:
            if patch.lines != original.lines:
                raise ValueError("Scene revision changed locked dialogue")
            missing_locked_text = [
                line.text for line in original.lines if line.text not in screenplay
            ]
            if missing_locked_text:
                raise ValueError(
                    "Scene revision omitted locked dialogue from screenplay_text: "
                    f"missing_locked_text={missing_locked_text}"
                )
        comparable_patch = patch.model_copy(
            deep=True,
            update={"performance_timing_assessment": None},
        )
        return SceneRevisionResult(
            patch=patch,
            revised=comparable_patch != original,
            retry_required=False,
            retry_feedback=None,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return SceneRevisionResult(
            patch=original,
            revised=False,
            retry_required=True,
            retry_feedback=_compact_error(error),
            warnings=[],
        )
