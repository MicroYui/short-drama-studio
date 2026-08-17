"""GPT-assisted H3 prompt compilation with lightweight locked-fact checks."""

from __future__ import annotations

import json
import re

from short_drama.h3_models import H3MediaInput, H3MediaRole
from short_drama.models import ProjectDocument, ShotPlan, VideoGenerationRoute


class LanguagePromptValidationError(ValueError):
    """The generated prompt changed or omitted a locked production fact."""


_SYSTEM_INSTRUCTIONS = """你是 MiniMax-H3 真人竖屏短剧的镜头提示词编译器。
只输出一段可直接提交给视频模型的中文提示词，不要标题、解释、Markdown 或代码块。
锁定镜头事实、对白、说话人、左右手、角色身份、服装、道具状态、场景布局、时长和画幅都不可改写。
route 已由上游规划器选定，不得在提示词编译阶段改换生成路线。
visible_assets 是本镜头可见资产的权威清单；项目 Bible 只是这些资产的消歧资料。未列入 visible_assets 的人物或道具不得入画，即使其在对白、相邻镜头或项目 Bible 中出现。
route_media_bindings 描述本次路线实际绑定的媒体。first_frame 是不可改写的开场画面，last_frame 是不可改写的结束画面，reference media 只锚定其明确描述的身份、空间、道具或声音；提示词不得与这些媒体的描述冲突。
previous_shot_terminal_state 只用于承接上一镜的物理终态；不得把上一镜独有的人物、道具或事件带入当前镜头。
structured_action_plan 是导演 Agent 为当前镜头声明的动作顺序和物理交互关系。按顺序执行已声明动作；当 contact_persists 为 true 时，保持接触到最终帧或后续动作节拍明确改变该状态。刚性附件始终属于声明的物体表面并随主体同步运动，不得消失、换面、复制或漂移。不得据此增加未声明动作。
把镜头写成单个连续镜头，按时间顺序明确起始状态、动作节拍、摄影机运动和结束状态。优先提高真人感、物理动作连续性、身份与道具稳定性、构图和运镜可执行性。
动作和运镜不得超出 locked_shot 与 source_prompt 已明确规定的范围；不得自行添加开关门、人物进出场、镜头穿越门窗、道具交接或场景转换等大动作。
根据镜头意图、时长和已声明动作安排自然节奏；不要套用固定动作拍数阈值。若信息密度较高，仍忠实编译用户已经选择的镜头方案，不得擅自删动作或拆镜。除非锁定事实明确要求立即开始或压尾结束，否则给起止留出自然呼吸空间。
对白必须逐字保留并明确普通话说话人；画外音时画面人物不得误开口。禁止字幕、水印、画面内可读文字、无关对白、切镜、瞬移和无动机越轴。
最终提示词必须包含 9:16、精确时长，并控制在 7000 个字符以内。"""


def _structured_action_plan(
    document: ProjectDocument,
    shot: ShotPlan,
) -> dict[str, object] | None:
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is None or (not plan.action_beats and not plan.interactions):
        return None
    asset_names = {asset.id: asset.name for asset in document.assets}
    return {
        "action_beats": [
            {
                "order": beat.order,
                "actor": asset_names.get(beat.actor_asset_id, beat.actor_asset_id),
                "action": beat.action,
                "target_anchor": beat.target_anchor,
                "required_end_state": beat.required_end_state,
            }
            for beat in sorted(plan.action_beats, key=lambda item: item.order)
        ],
        "spatial_interactions": [
            {
                "actor": asset_names.get(
                    interaction.actor_asset_id,
                    interaction.actor_asset_id,
                ),
                "effector": interaction.effector,
                "action": interaction.action,
                "target": asset_names.get(
                    interaction.target_asset_id,
                    interaction.target_asset_id,
                ),
                "target_anchor": interaction.target_anchor,
                "start_state": interaction.start_state,
                "end_state": interaction.end_state,
                "unchanged_anchors": interaction.unchanged_anchors,
                "contact_anchor": interaction.contact_anchor,
                "contact_surface": interaction.contact_surface,
                "contact_persists": interaction.contact_persists,
                "rigid_attachments": [
                    attachment.model_dump(mode="json")
                    for attachment in interaction.rigid_attachments
                ],
            }
            for interaction in plan.interactions
        ],
        "planning_notes": plan.planning_notes,
    }


def _contains_name_or_core(text: str, name: str) -> bool:
    if name in text:
        return True
    compact = re.sub(r"[\s·\-_]", "", name)
    return any(
        compact[start : start + width] in text
        for width in range(min(4, len(compact)), 2, -1)
        for start in range(0, len(compact) - width + 1)
    )


def _visible_bible_payload(
    document: ProjectDocument,
    shot: ShotPlan,
) -> tuple[list[str], dict[str, object]]:
    bible = next(
        version
        for version in document.bible_versions
        if version.id == document.project.current_bible_version_id
    )
    visual_facts = "。".join(
        (
            shot.narrative_purpose,
            shot.visual,
            shot.continuity.action_in,
            shot.continuity.action_out,
            shot.continuity.prop_state_in,
            shot.continuity.prop_state_out,
        )
    )
    visual_subject = re.split(r"[，,。；;]", shot.visual, maxsplit=1)[0]
    character_facts = "。".join((shot.narrative_purpose, visual_subject))
    visible_characters = [
        character for character in bible.characters if character.name in character_facts
    ]
    if not visible_characters and any(
        marker in visual_facts for marker in ("两人", "二人", "双方", "对峙")
    ):
        visible_characters = list(bible.characters)

    visible_props = [
        prop for prop in bible.props if _contains_name_or_core(visual_facts, prop.name)
    ]
    matching_locations = [
        location
        for location in bible.locations
        if _contains_name_or_core(visual_facts, location.name)
    ]
    visible_locations = (
        matching_locations
        if matching_locations
        else list(bible.locations) if len(bible.locations) == 1 else []
    )
    visible_names = [
        *(character.name for character in visible_characters),
        *(location.name for location in visible_locations),
        *(prop.name for prop in visible_props),
    ]
    return visible_names, {
        "characters": [
            {
                "name": character.name,
                "appearance": character.appearance,
                "wardrobe": character.wardrobe,
                "performance": character.performance,
            }
            for character in visible_characters
        ],
        "locations": [
            location.model_dump(mode="json") for location in visible_locations
        ],
        "props": [
            {
                **prop.model_dump(mode="json", exclude={"states"}),
                "states": [
                    state.model_dump(mode="json")
                    for state in prop.states
                    if state.shot_id == shot.id
                ],
            }
            for prop in visible_props
        ],
        "style": bible.style.model_dump(mode="json"),
    }


def _route_media_bindings(
    document: ProjectDocument,
    media_inputs: list[H3MediaInput],
) -> list[dict[str, object]]:
    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    role_purpose = {
        H3MediaRole.FIRST_FRAME: "不可改写的镜头开场画面与构图",
        H3MediaRole.LAST_FRAME: "不可改写的镜头结束画面与构图",
        H3MediaRole.REFERENCE_IMAGE: "只锚定描述中明确对应的视觉元素",
        H3MediaRole.REFERENCE_VIDEO: "只锚定描述中明确对应的动态或视觉元素",
        H3MediaRole.REFERENCE_AUDIO: "只锚定描述中明确对应的声音元素",
    }
    bindings: list[dict[str, object]] = []
    for media in media_inputs:
        binding: dict[str, object] = {
            "role": media.role.value,
            "kind": media.kind.value,
            "purpose": role_purpose[media.role],
        }
        if media.asset_version_id is not None:
            binding["asset_version_id"] = media.asset_version_id
            matched = version_index.get(media.asset_version_id)
            if matched is not None:
                asset, version = matched
                binding.update(
                    {
                        "asset_name": asset.name,
                        "asset_kind": asset.kind.value,
                        "version": version.version,
                        "descriptor": version.descriptor,
                        "review_notes": version.review_notes,
                        "width": version.width,
                        "height": version.height,
                    }
                )
        bindings.append(binding)
    return bindings


def _previous_shot_terminal_state(
    document: ProjectDocument,
    shot: ShotPlan,
) -> dict[str, object] | None:
    previous = max(
        (candidate for candidate in document.shots if candidate.sequence < shot.sequence),
        key=lambda candidate: candidate.sequence,
        default=None,
    )
    if previous is None:
        return None
    return {
        "sequence": previous.sequence,
        "visual": previous.visual,
        "framing": previous.framing,
        "action_out": previous.continuity.action_out,
        "prop_state_out": previous.continuity.prop_state_out,
        "character_positions": previous.continuity.character_positions,
        "eyeline": previous.continuity.eyeline,
        "transition_out": previous.transition_out,
    }


def build_language_prompt_input(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    source_prompt: str,
    media_inputs: list[H3MediaInput] | None = None,
) -> tuple[str, str]:
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    dialogue = [
        line_by_id[line_id]
        for line_id in shot.dialogue_line_ids
        if line_id in line_by_id
    ]
    route_guidance = {
        VideoGenerationRoute.DIRECT: "无图像锚点，提示词必须完整建立首帧人物、空间、构图和动作起点。",
        VideoGenerationRoute.FRAME_FIRST: "首帧图已批准；从首帧精确起步，保持人物、服装、空间、构图和道具外观。",
        VideoGenerationRoute.FRAME_FIRST_LAST: "首尾帧均已批准；连续完成中间动作，不改变两端已确定的身份、构图和道具状态。",
        VideoGenerationRoute.REFERENCE: "参考素材用于身份、空间或道具锚定；保持其外观，但不要把参考图理解成额外镜头或新增主体。",
    }[route]
    visible_asset_names, visible_bible = _visible_bible_payload(document, shot)
    route_media = _route_media_bindings(document, media_inputs or [])
    payload = {
        "task": "在不改变任何锁定事实的前提下，增强 source_prompt 的时序、动作、运镜与视觉可执行性。",
        "route": route.value,
        "route_guidance": route_guidance,
        "route_media_bindings": route_media,
        "duration_seconds": duration_seconds,
        "aspect_ratio": "9:16",
        "source_prompt": source_prompt,
        "locked_shot": {
            "sequence": shot.sequence,
            "narrative_purpose": shot.narrative_purpose,
            "visual": shot.visual,
            "framing": shot.framing,
            "camera": shot.camera,
            "audio_intent": shot.audio_intent,
            "continuity": shot.continuity.model_dump(mode="json"),
            "transition_out": shot.transition_out,
        },
        "previous_shot_terminal_state": _previous_shot_terminal_state(document, shot),
        "structured_action_plan": _structured_action_plan(document, shot),
        "production_rules": {
            "route_selected_upstream": True,
            "action_density_policy": "按实际镜头上下文编译，不使用固定动作拍数阈值，也不擅自拆镜。",
            "timing_policy": "在不违背锁定事实时保留自然起止呼吸空间，不使用固定秒数作为硬规则。",
            "continuity_handoff": "从上一镜终态自然承接，但不得携入当前镜头未列出的可见资产。",
        },
        "locked_dialogue": [
            {
                "speaker": line.speaker,
                "spoken_text": line.spoken_text,
                "audio_kind": line.audio_kind.value,
            }
            for line in dialogue
        ],
        "visible_assets": visible_asset_names,
        "visible_asset_bible_for_disambiguation_only": visible_bible,
    }
    return _SYSTEM_INSTRUCTIONS, json.dumps(payload, ensure_ascii=False, indent=2)


def validate_language_prompt(
    prompt: str,
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    duration_seconds: int,
) -> list[str]:
    stripped = prompt.strip()
    if not stripped:
        raise LanguagePromptValidationError("GPT-5.6-sol returned an empty H3 prompt")
    if len(stripped) > 7000:
        raise LanguagePromptValidationError("GPT-5.6-sol H3 prompt exceeds 7000 characters")
    if "```" in stripped:
        raise LanguagePromptValidationError("GPT-5.6-sol H3 prompt contains Markdown fencing")
    if "9:16" not in stripped:
        raise LanguagePromptValidationError("GPT-5.6-sol H3 prompt omitted the locked 9:16 ratio")
    duration_pattern = rf"{duration_seconds}\s*秒"
    if re.search(duration_pattern, stripped) is None:
        raise LanguagePromptValidationError(
            f"GPT-5.6-sol H3 prompt omitted the locked {duration_seconds}-second duration"
        )

    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    locked_lines = [
        line_by_id[line_id]
        for line_id in shot.dialogue_line_ids
        if line_id in line_by_id
    ]
    for line in locked_lines:
        if line.spoken_text not in stripped:
            raise LanguagePromptValidationError(
                f"GPT-5.6-sol H3 prompt changed or omitted locked dialogue for {line.speaker}"
            )
        speaker_preserved = line.speaker in stripped
        if not speaker_preserved and line.audio_kind.value == "VOICEOVER":
            source, separator, speaker = line.speaker.partition("中的")
            speaker_preserved = bool(
                separator
                and source.strip() in stripped
                and speaker.strip() in stripped
            )
        if not speaker_preserved:
            raise LanguagePromptValidationError(
                f"GPT-5.6-sol H3 prompt changed or omitted locked speaker {line.speaker}"
            )

    notes = [
        "non-empty prompt within MiniMax-H3 7000-character limit",
        f"locked 9:16 ratio and {duration_seconds}-second duration preserved",
    ]
    if locked_lines:
        notes.append(f"{len(locked_lines)} locked dialogue line(s) preserved verbatim")
    else:
        notes.append("shot contains no locked dialogue")
    return notes
