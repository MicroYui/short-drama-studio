"""GPT-assisted H3 prompt compilation with lightweight locked-fact checks."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import cast

from short_drama.h3_models import H3MediaInput, H3VideoRequest
from short_drama.models import AssetKind, ProjectDocument, ShotPlan, VideoGenerationRoute
from short_drama.production_contracts import GenerationUnit, ProductionSpecContent
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
)
from short_drama.skills.loader import load_provider_prompt_skills
from short_drama.providers.h3_prompt_skill import (
    prompt_schema_for_route,
    validate_h3_generation_unit_prompt_ir,
    validate_h3_prompt_ir,
)
from short_drama.providers.h3_media_bindings import is_h3_composition_reference, resolve_h3_media_bindings
from short_drama.providers.h3_output_format import h3_prompt_document, resolve_h3_output_format
from short_drama.providers.h3_dialogue_spans import (
    assert_complete_dialogue_request, dialogue_span_payload, generation_unit_dialogue_spans,
    request_dialogue_span_payload, request_speaker_ids,
)


_H3_FIELDS = (
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music",
)
_H3_FIELD_PATTERN = re.compile(
    r"(?m)^[ \t]*(" + "|".join(_H3_FIELDS) + r"):[ \t]*"
)
_DIALOGUE_POLICY = (
    "dialogue_spans 从当前分镜推导，每个 span 是一次实际发声，原文只写一个 <d>，"
    "放在起始镜头的 start_beat_index 时段并说明完整范围；continuation_beat_indices 及 continuing_dialogue 只延续，不重启或重复。"
    "beat 索引从0开始，span 起止是 beat 容纳范围；保留 sound/audio_intent 更细的发声时间，不强迫说满。"
    "同组相邻镜明确承载同ID时是一句跨切对白：按 request_dialogue_spans 的完整时间说一次，"
    "切镜两侧用 <scenetrans> 和相同声线的连续说明；不因视觉切换暂停、截短或重念，也不猜测切点具体词语。"
    "同镜连续阶段不使用 <scenetrans> 伪造切镜；不同 span 即使 line_id 或文字相同也不可去重。"
    "全部镜头使用 locked_dialogue.speaker_id 标注同一请求内的稳定声线，同时保留 speaker 名称。"
    "speaker_character_id 不同即使同名也保持不同声音ID；无绑定名称只在本请求内分配声线，不代表 Bible 实体。"
)


class LanguagePromptValidationError(ValueError):
    """The generated prompt changed or omitted a locked production fact."""


def _prompt_sections(prompt: str) -> tuple[str, dict[str, str]]:
    """Parse only the provider's top-level field syntax, never creative prose."""

    matches = list(_H3_FIELD_PATTERN.finditer(prompt))
    if not matches:
        raise LanguagePromptValidationError("H3 prompt has no recognizable top-level fields")
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        field = match.group(1)
        if field in sections:
            raise LanguagePromptValidationError(f"H3 prompt repeats top-level field {field}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        sections[field] = prompt[match.end():end].strip()
    return prompt[:matches[0].start()].strip(), sections


def _required_fields(route: VideoGenerationRoute) -> tuple[str, ...]:
    if route == VideoGenerationRoute.REFERENCE:
        return (
            "subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music",
        )
    return ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")


def _frame_alignment(
    route: VideoGenerationRoute, duration_seconds: int, *, generation_unit: bool,
) -> str:
    if route == VideoGenerationRoute.FRAME_FIRST:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    if route == VideoGenerationRoute.FRAME_LAST:
        source = "the final editorial shot" if generation_unit else "[Shot 1]"
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from {source}) aligns with the {duration_seconds:.2f}-second "
            "mark of the target video."
        )
    if route == VideoGenerationRoute.FRAME_FIRST_LAST:
        first = "[Shot 1]" if generation_unit else "Shot 1"
        last = "the final editorial shot" if generation_unit else "Shot 1"
        return (
            "How the reference pictures align with the target video — "
            f"Picture 1 (from {first}) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from {last}) aligns with the {duration_seconds:.2f}-second "
            "mark of the target video."
        )
    return ""


def normalize_language_prompt(
    prompt: str,
    document: ProjectDocument,
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
    generation_unit: bool = False,
) -> str:
    """Assemble known grammar and missing bindings without rewriting any facts.

    Creative sections must all exist. We retain every section body verbatim and
    reject conflicting aliases rather than silently changing which picture wins.
    Length and all locked-fact checks still run after this assembly.
    """

    stripped = prompt.strip()
    if "```" in stripped:
        raise LanguagePromptValidationError("H3 prompt contains Markdown fencing")
    prefix, sections = _prompt_sections(stripped)
    fields = _required_fields(route)
    if set(sections) != set(fields):
        missing = sorted(set(fields).difference(sections))
        unexpected = sorted(set(sections).difference(fields))
        raise LanguagePromptValidationError(
            f"H3 prompt fields do not match {route.value}; missing={missing}, unexpected={unexpected}"
        )
    expected_alignment = _frame_alignment(route, duration_seconds, generation_unit=generation_unit)
    if prefix and prefix != expected_alignment:
        # The preamble might contain a real creative fact or a conflicting frame
        # instruction. Never discard it under the guise of formatting repair.
        raise LanguagePromptValidationError("H3 prompt contains an unexpected preamble before its fields")
    if route == VideoGenerationRoute.REFERENCE:
        definitions = sections["subject_definitions"]
        additions: list[str] = []
        for binding in resolve_h3_media_bindings(document, media_inputs, []):
            required = f"@{binding.alias} = {binding.prompt_label}"
            assignments = re.findall(
                re.escape(f"@{binding.alias}") + r"\s*=\s*(<(?:Subject|Picture|Video|Audio) \d+>)",
                stripped,
            )
            if any(label != binding.prompt_label for label in assignments):
                raise LanguagePromptValidationError(f"H3 prompt has a conflicting binding for @{binding.alias}")
            if required not in definitions or binding.provider_label not in definitions:
                declaration = (
                    f"{required}; {binding.provider_label} is a soft composition reference: {binding.purpose}."
                    if is_h3_composition_reference(binding.media) else
                    f"{required}; {binding.prompt_label} refers to {binding.subject} from {binding.provider_label}."
                )
                if declaration not in additions:
                    additions.append(declaration)
        if additions:
            sections["subject_definitions"] = " ".join([definitions, *additions]).strip()
    body = "\n\n".join(f"{field}: {sections[field]}" for field in fields)
    return f"{expected_alignment}\n\n{body}" if expected_alignment else body


def build_language_prompt_section_repair_input(
    prompt: str,
    *,
    source_input_text: str,
    route: VideoGenerationRoute,
) -> tuple[str, str]:
    """Request a bounded edit of overlong prose; callers retain all other fields.

    This is an optional technical repair, not a second full compilation. The
    source is the exact first compiler input, so facts and dialogue stay anchored.
    """

    _prefix, sections = _prompt_sections(prompt)
    if len(prompt) <= 7000:
        raise LanguagePromptValidationError("H3 section repair requires an overlong prompt")
    fields = _required_fields(route)
    if set(sections) != set(fields):
        raise LanguagePromptValidationError("Repair requires a structurally complete H3 prompt")
    # Start with the largest prose sections. Their combined size must leave room
    # to remove the actual excess. References remain deterministic and intact.
    candidates = sorted(
        (field for field in fields if field != "subject_definitions"),
        key=lambda field: len(sections[field]), reverse=True,
    )
    selected: list[str] = []
    editable_characters = 0
    for field in candidates:
        selected.append(field)
        editable_characters += len(sections[field])
        if editable_characters > len(prompt) - 7000:
            break
    source = json.loads(source_input_text)
    source.pop("source_prompt", None)
    source.pop("required_prompt_schema", None)
    payload = {
        "operation": "replace_only_selected_h3_prose_sections",
        "required_reduction_characters": len(prompt) - 7000,
        "replacement_total_max_characters": editable_characters - (len(prompt) - 7000),
        "sections_to_replace": {field: sections[field] for field in selected},
        "retained_reference_definitions": sections.get("subject_definitions"),
        "locked_source": source,
    }
    return (
        "只修订 sections_to_replace 中的文本，输出 JSON 对象，键必须与其完全一致，值为替换正文。"
        "调用方会原样保留其他字段、标签与帧对齐。按给定字符上限消除重复修辞、重复身份和解释；"
        "保留每条不同事实、全部镜头/时段标记、原文对白及说话人、必要动作与声音。"
        "不得截断、改时长、删镜头或重写故事；不能忠实缩短时明确说明无法完成，不伪造成功。",
        json.dumps(payload, ensure_ascii=False),
    )


def apply_language_prompt_section_repair(
    prompt: str,
    replacement_json: str,
    *,
    repair_input_text: str,
) -> str:
    """Apply exact returned fields while preserving every untouched byte of prose."""

    _prefix, sections = _prompt_sections(prompt)
    request = json.loads(repair_input_text)
    decoded: object = json.loads(replacement_json)
    expected = request["sections_to_replace"]
    if not isinstance(decoded, dict):
        raise LanguagePromptValidationError("H3 section repair must return an object")
    replacements = cast(dict[str, object], decoded)
    if set(replacements) != set(expected):
        raise LanguagePromptValidationError("H3 section repair returned unexpected or missing fields")
    if not all(isinstance(value, str) and value.strip() for value in replacements.values()):
        raise LanguagePromptValidationError("H3 section repair must return nonempty text values")
    for field, value in cast(dict[str, str], replacements).items():
        if _H3_FIELD_PATTERN.search(value):
            raise LanguagePromptValidationError("H3 section repair embedded another top-level field")
        sections[field] = value.strip()
    matches = list(_H3_FIELD_PATTERN.finditer(prompt))
    result = prompt
    for index in range(len(matches) - 1, -1, -1):
        match = matches[index]
        field = match.group(1)
        if field not in replacements:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        old = prompt[match.end():end]
        trailing = old[len(old.rstrip()):]
        result = result[:match.end()] + sections[field] + trailing + result[end:]
    return result


@lru_cache(maxsize=1)
def _h3_prompt_skill_instructions() -> str:
    for skill in load_provider_prompt_skills():
        if skill.provider == "minimax_h3":
            return skill.instructions
    raise RuntimeError("MiniMax H3 Provider Prompt Skill is not configured")


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


def _explicit_shot_asset_ids(
    document: ProjectDocument,
    shot: ShotPlan,
    media_inputs: list[H3MediaInput],
) -> list[str]:
    """Return only asset ids selected by typed upstream fields."""

    selected: list[str] = []

    def add(asset_id: str | None) -> None:
        if asset_id is not None and asset_id not in selected:
            selected.append(asset_id)

    version_to_asset_id = {
        version.id: asset.id
        for asset in document.assets
        for version in asset.versions
    }
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is not None:
        for requirement in plan.requirements:
            add(requirement.subject_asset_id)
        for beat in plan.action_beats:
            add(beat.actor_asset_id)
        for interaction in plan.interactions:
            add(interaction.actor_asset_id)
            add(interaction.target_asset_id)
        for binding in plan.bindings:
            add(version_to_asset_id.get(binding.target_id))
    for beat in [shot, *shot.timeline_beats]:
        for cue in beat.references:
            add(cue.subject_asset_id)
    for media in media_inputs:
        if media.asset_version_id is not None:
            add(version_to_asset_id.get(media.asset_version_id))
    return selected


def _visible_bible_payload(
    document: ProjectDocument,
    shot: ShotPlan,
    media_inputs: list[H3MediaInput],
    *,
    related_shots: Sequence[ShotPlan] = (),
) -> tuple[list[str], dict[str, object]]:
    bible = next(
        version
        for version in document.bible_versions
        if version.id == document.project.current_bible_version_id
    )
    explicit_asset_ids = set(_explicit_shot_asset_ids(document, shot, media_inputs))
    for related in related_shots:
        explicit_asset_ids.update(_explicit_shot_asset_ids(document, related, []))
    visible_shot_ids = {shot.id, *(item.id for item in related_shots)}
    selected_assets = [
        asset for asset in document.assets if asset.id in explicit_asset_ids
    ]
    selected_character_ids = {
        asset.bible_entity_id
        for asset in selected_assets
        if asset.kind == AssetKind.CHARACTER and asset.bible_entity_id is not None
    }
    selected_location_ids = {
        asset.bible_entity_id
        for asset in selected_assets
        if asset.kind == AssetKind.LOCATION and asset.bible_entity_id is not None
    }
    selected_prop_ids = {
        asset.bible_entity_id
        for asset in selected_assets
        if asset.kind == AssetKind.PROP and asset.bible_entity_id is not None
    }
    visible_characters = [
        character for character in bible.characters if character.id in selected_character_ids
    ]
    visible_props = [
        prop for prop in bible.props if prop.id in selected_prop_ids
    ]
    visible_locations = [
        location for location in bible.locations if location.id in selected_location_ids
    ]
    bible_names: dict[tuple[AssetKind, str], str] = {}
    for entity in bible.characters:
        bible_names[(AssetKind.CHARACTER, entity.id)] = entity.name
    for entity in bible.locations:
        bible_names[(AssetKind.LOCATION, entity.id)] = entity.name
    for entity in bible.props:
        bible_names[(AssetKind.PROP, entity.id)] = entity.name
    visible_names: list[str] = []
    for asset in selected_assets:
        if asset.bible_entity_id is None:
            visible_names.append(asset.name)
        else:
            visible_names.append(
                bible_names.get((asset.kind, asset.bible_entity_id), asset.name)
            )
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
                    if state.shot_id in visible_shot_ids
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
    bindings: list[dict[str, object]] = []
    for resolved in resolve_h3_media_bindings(document, media_inputs, []):
        media = resolved.media
        binding: dict[str, object] = {
            "role": media.role.value,
            "kind": media.kind.value,
            "provider_label": resolved.provider_label,
            "prompt_label": resolved.prompt_label,
            "reference_alias": f"@{resolved.alias}",
            "required_binding": f"@{resolved.alias} = {resolved.prompt_label}",
            "subject": resolved.subject,
            "subject_asset_id": resolved.subject_asset_id,
            "reference_tags": list(resolved.tags),
            "image_reference_role": media.image_reference_role.value if media.image_reference_role else None,
            "purpose": resolved.purpose,
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


def _previous_shot_planned_terminal_state(
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
        "evidence_kind": "PLANNED_ONLY",
        "shot_id": previous.id,
        "sequence": previous.sequence,
        "visual": previous.visual,
        "framing": previous.framing,
        "action_out": previous.continuity.action_out,
        "prop_state_out": previous.continuity.prop_state_out,
        "character_positions": previous.continuity.character_positions,
        "eyeline": previous.continuity.eyeline,
        "transition_out": previous.transition_out,
    }


def _compiler_instructions(
    document: ProjectDocument,
    *,
    method_skill_ids: Sequence[str],
) -> str:
    skill = production_skill(document.project.production_profile)
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.VIDEO_PROMPT_COMPILER,),
        include_constitution=False,
        stage=ProductionPromptStage.VISUAL_GEN,
        method_skill_ids=method_skill_ids,
    )
    return (
        f"你是 MiniMax-H3 的模型专属 Prompt Compiler，当前制作类型为{skill.medium}。\n"
        "制作类型策略只用于忠实编译已经锁定的镜头，不得在此阶段改写导演意图："
        f"{skill_prompt.text}\n{_h3_prompt_skill_instructions()}"
        "\n当前 Provider API 的 required_prompt_schema 是最终输出格式。"
        "已选 Method 提供逐镜叙事、参考用途和声音连续性，项目提供风格，"
        "只有当前 Provider Skill 提供输出语法。"
        "\nmodel_lock 来自本次明确调用的 H3 适配器，是实际目标模型；"
        "GenerationUnit 中可空的 provider/model/resolution 是上游偏好，不替代该目标。"
        "aspect_ratio 是本次实际输出画幅，优先于旧风格备注中的画幅；"
        "adaptive 表示沿用所选画面比例，不得擅自写成竖屏或其他固定比例。"
        "operation_scope 是当前 Tool 调用范围：只编译文本，没有媒体上传、提交或生成。"
        "ProductionSpec 的 user_constraints 保留当时的记录：其中创作约束仍须遵守，"
        "而描述当时停在哪个工序的阶段备注不能替代当前 Tool 的调用范围。"
        "不得把准备最终 Prompt 误认为已经调用 H3。"
        "\n最终请求遵守 H3 的7000字符上限；不另设写作配额，不靠截断来达标。"
        "每条独有事实只表达一次：主体映射使用短定义，共用空间、摄影和声音只集中说明一次，"
        "逐镜段落只补充该镜的动作、变化、时间与原文对白。retention_analysis 用简短结论，"
        "不展开解释性推理、重复禁止事项或在每镜重写全部身份定义。"
        "官方字段是必要格式，不是要删除的解释包装：保留全部字段标题，压缩的是正文。"
        "英文叙述用直接、紧凑的句法，不用电影化修辞扩写已给事实。"
        "subject_definitions 保留短身份名、来源支持的主要可见区别、全部语义别名及对应图片；summary 一句；"
        "COMPOSITION/LAYOUT_GUIDE 的 prompt_label 为 Picture：它提供本镜构图、站位和当前物件状态，"
        "不是另一个 Subject，也不承诺硬首尾帧。明确它适用的镜头、时间和哪些人可见；"
        "分镜拼图只提供顺序与构图，不能照抄成分屏。人物图提取身份，构图图传递空间，两者冲突时"
        "如实指出冲突，不能把构图里的错误演员重新定义成主角。"
        "retention_analysis 集中说明保留与不复制的范围，不重复主体定义；"
        "overall_soundscape 只补充共享声音，不再逐字重复各镜已写的对白和动作声。"
        "每镜对白在对应时间窗内逐字保留，发话标注同时保留 locked_dialogue.speaker 原名；"
        "例如 Lin Yi（林一） (S1) says: <d>[Chinese] 原文对白</d>。"
        "按 production_rules.dialogue 使用 dialogue_spans 与 locked_dialogue.speaker_id，"
        "不按时段数量重复发声，不在每个镜头重置声音编号。"
        "保留各 beat 的视听动作与时间关系，镜头组内使用加过 source_window 起点的绝对时间。"
        "英文译名不能独自代替原说话人身份，尤其是媒体资产没有 Bible entity 关联时。"
        "若提供 source_prompt，它是本次待修订的完整草稿；结合 compiler_feedback 修改它，"
        "超长草稿本身不是最终 H3 请求，最终输出仍必须通过7000字符限制。"
        "compiler_feedback 是调用方针对上次结果的修订意见，应落实到表达方式；"
        "它不能覆盖锁定的事实、对白、镜头边界或有序媒体。"
        "\nprevious_*_planned_terminal_state 只是上一镜的设计意图。"
        "actual_previous_media_boundary 才是调用方提供的当前采用媒体边界；"
        "其中未观察的事实仍为未知，不得由计划、静帧或摘要冒充已发生动作或声音。"
        "仅承接当前镜头确实依赖的状态，不为补观察阻挡无依赖的编译。"
    )


def _h3_compilation_target(resolution: str = "768P") -> dict[str, object]:
    return {
        "model_lock": {
            "provider": "minimax",
            "model": H3VideoRequest.model_fields["model"].default,
            "resolution": resolution,
        },
        "operation_scope": {
            "current_tool": "route.compile_h3_prompt",
            "action": "Compile the supplied canonical facts into H3 prompt text only.",
            "language_compilation_requested": True,
            "h3_upload_or_submit": False,
            "media_generation": False,
            "upstream_content_mutation": False,
        },
    }


def build_language_prompt_input(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    source_prompt: str | None,
    media_inputs: list[H3MediaInput] | None = None,
    method_skill_ids: Sequence[str] = (),
    revision_instructions: str | None = None,
    actual_previous_media_boundary: dict[str, object] | None = None,
    resolution: str = "768P",
    aspect_ratio: str | None = None,
) -> tuple[str, str]:
    assert_complete_dialogue_request(document, [shot])
    resolution, _ratio = resolve_h3_output_format(route=route, resolution=resolution, aspect_ratio=aspect_ratio,
                                                default_aspect_ratio=document.project.aspect_ratio)
    document = h3_prompt_document(document, route=route, aspect_ratio=aspect_ratio)
    instructions = _compiler_instructions(document, method_skill_ids=method_skill_ids)
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
    speaker_ids = request_speaker_ids(document, [shot])
    scene = next(
        (item for item in script.structured_scenes if item.id == shot.scene_id),
        None,
    )
    route_guidance = {
        VideoGenerationRoute.DIRECT: "无图像锚点，提示词必须完整建立首帧人物、空间、构图和动作起点。",
        VideoGenerationRoute.FRAME_FIRST: "使用本次选中的首帧；从首帧精确起步，保持人物、服装、空间、构图和道具外观。",
        VideoGenerationRoute.FRAME_LAST: "使用本次选中的尾帧；根据锁定镜头推导相容起始状态，让动作、机位和物理状态逐步收敛到尾帧，不得把尾帧当作开场。",
        VideoGenerationRoute.FRAME_FIRST_LAST: "使用本次选中的首尾帧；连续完成中间动作，不改变两端已确定的身份、构图和道具状态。",
        VideoGenerationRoute.REFERENCE: "参考素材用于身份、空间或道具锚定；保持其外观，但不要把参考图理解成额外镜头或新增主体。",
    }[route]
    resolved_media_inputs = media_inputs or []
    visible_asset_names, visible_bible = _visible_bible_payload(
        document,
        shot,
        resolved_media_inputs,
    )
    route_media = _route_media_bindings(document, resolved_media_inputs)
    payload = {
        "task": "将当前镜头、原文对白和有序媒体忠实编译为 required_prompt_schema 指定的 MiniMax-H3 Prompt IR；仅在提供 source_prompt 时修订该草稿。",
        **_h3_compilation_target(resolution),
        "production_profile": document.project.production_profile.value,
        "route": route.value,
        "required_prompt_schema": prompt_schema_for_route(route),
        "route_guidance": route_guidance,
        "route_media_bindings": route_media,
        "duration_seconds": duration_seconds,
        "aspect_ratio": document.project.aspect_ratio,
        "locked_shot": {
            "sequence": shot.sequence,
            "narrative_purpose": shot.narrative_purpose,
            "visual": shot.visual,
            "framing": shot.framing,
            "camera": shot.camera,
            "audio_intent": shot.audio_intent,
            "timeline_beats": [
                beat.model_dump(mode="json") for beat in shot.timeline_beats
            ],
            "continuity": shot.continuity.model_dump(mode="json"),
            "transition_out": shot.transition_out,
        },
        "previous_shot_planned_terminal_state": _previous_shot_planned_terminal_state(document, shot),
        "actual_previous_media_boundary": actual_previous_media_boundary,
        "structured_scene_context": (
            scene.model_dump(mode="json") if scene is not None else None
        ),
        "structured_action_plan": _structured_action_plan(document, shot),
        "production_rules": {
            "route_selected_upstream": True,
            "action_density_policy": "按实际镜头上下文编译，不使用固定动作拍数阈值，也不擅自拆镜。",
            "timing_policy": "在不违背锁定事实时保留自然起止呼吸空间，不使用固定秒数作为硬规则。",
            "continuity_handoff": "有实际连续性依赖时使用已观察的采用媒体边界；未提供观察时只保留计划意图，不携入当前镜头未列出的可见资产。",
            "dialogue": _DIALOGUE_POLICY,
        },
        "locked_dialogue": [
            {
                "id": line.id,
                "speaker": line.speaker,
                "speaker_id": speaker_ids[line.id],
                "speaker_character_id": line.speaker_character_id,
                "spoken_text": line.spoken_text,
                "audio_kind": line.audio_kind.value,
            }
            for line in dialogue
        ],
        "dialogue_spans": dialogue_span_payload(shot),
        "visible_assets": visible_asset_names,
        "visible_asset_bible_for_disambiguation_only": visible_bible,
    }
    if revision_instructions is not None:
        payload["compiler_feedback"] = revision_instructions
    if source_prompt is not None:
        payload["source_prompt"] = source_prompt
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def build_generation_unit_language_prompt_input(
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    production_spec: ProductionSpecContent,
    media_inputs: list[H3MediaInput],
    method_skill_ids: Sequence[str] = (),
    source_prompt: str | None = None,
    revision_instructions: str | None = None,
    actual_previous_media_boundary: dict[str, object] | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
) -> tuple[str, str]:
    """Read one complete clip from canonical facts, without replaying other stages.

    Source facts are not a provider request and need not fit its 7000-character
    output limit. Each editorial window retains its dialogue and local roster.
    """

    resolution, _ratio = resolve_h3_output_format(route=route, resolution=resolution or unit.resolution or "768P",
        aspect_ratio=aspect_ratio, default_aspect_ratio=production_spec.aspect_ratio)
    document = h3_prompt_document(document, route=route, aspect_ratio=aspect_ratio)
    shot_by_id = {shot.id: shot for shot in shots}
    if set(shot_by_id) != set(unit.shot_ids):
        raise ValueError("Prompt compilation requires every shot in the generation unit")
    ordered_shots = [shot_by_id[window.shot_id] for window in unit.shot_windows]
    assert_complete_dialogue_request(document, ordered_shots)
    spans = generation_unit_dialogue_spans(unit, ordered_shots)
    speaker_ids = request_speaker_ids(document, ordered_shots)
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    visible_names, visible_bible = _visible_bible_payload(
        document, ordered_shots[0], media_inputs, related_shots=ordered_shots[1:]
    )
    locked_shots: list[dict[str, object]] = []
    for index, window in enumerate(unit.shot_windows, start=1):
        shot = shot_by_id[window.shot_id]
        locked_shots.append(
            {
                "required_timing_marker": (
                    f"[Shot {index}] [{window.source_in_seconds:.2f}-"
                    f"{window.source_out_seconds:.2f} seconds]"
                ),
                "source_window": window.model_dump(mode="json"),
                "locked_shot": shot.model_dump(
                    mode="json",
                    exclude={
                        "approval_status", "freshness", "merge_candidate", "local_rework_risk"
                    },
                ),
                "locked_dialogue": [
                    {**line_by_id[line_id].model_dump(
                        mode="json",
                        include={
                            "id", "speaker", "speaker_character_id", "spoken_text", "audio_kind"
                        },
                    ), "speaker_id": speaker_ids[line_id]}
                    for line_id in [span.line_id for span in spans if span.parts[0].shot_id == shot.id]
                    if line_id in line_by_id
                ],
                "dialogue_spans": [{
                    "utterance_id": f"utterance-{span_index + 1}", "line_id": span.line_id,
                    "start_seconds": span.start_seconds, "end_seconds": span.end_seconds,
                    "start_beat_index": span.parts[0].beat_indices[0] if span.parts[0].beat_indices else None,
                    "continuation_beat_indices": list(span.parts[0].beat_indices[1:]),
                } for span_index, span in enumerate(spans) if span.parts[0].shot_id == shot.id],
                "continuing_dialogue": [{
                    "utterance_id": f"utterance-{span_index + 1}", "line_id": span.line_id,
                    "speaker_id": speaker_ids.get(span.line_id), "finish_by_seconds": span.end_seconds,
                    "beat_indices": list(part.beat_indices),
                } for span_index, span in enumerate(spans) for part in span.parts[1:] if part.shot_id == shot.id],
                "visible_asset_ids": _explicit_shot_asset_ids(document, shot, []),
                "structured_action_plan": _structured_action_plan(document, shot),
            }
        )
    bindings = _route_media_bindings(document, media_inputs)
    for binding in bindings:
        binding.pop("review_notes", None)
    payload: dict[str, object] = {
        "task": "将当前完整镜头组忠实编译为一次 H3 音视频请求；每个 locked_shots 条目形成一个独立、按顺序承接的自然叙事段落。",
        **_h3_compilation_target(resolution),
        "route": route.value,
        "required_prompt_schema": prompt_schema_for_route(route),
        "duration_seconds": duration_seconds,
        "aspect_ratio": document.project.aspect_ratio,
        "production_direction": production_spec.model_dump(
            mode="json",
            include={"production_mode", "language", "visual", "audio", "user_constraints"},
        ),
        "locked_generation_unit": unit.model_dump(mode="json", exclude={"shot_windows"}),
        "locked_shots": locked_shots,
        "request_dialogue_spans": request_dialogue_span_payload(spans),
        "previous_clip_planned_terminal_state": _previous_shot_planned_terminal_state(document, ordered_shots[0]),
        "actual_previous_media_boundary": actual_previous_media_boundary,
        "route_media_bindings": bindings,
        "visible_assets": visible_names,
        "visible_asset_bible_for_disambiguation_only": visible_bible,
        "production_rules": {
            "timing": "每段原样保留 required_timing_marker；组内节拍加上 source_window 的起点后才是片段绝对时间。不得移动、合并、增删镜头或逐字台词。",
            "references": "按 route_media_bindings 的实际顺序绑定全部参考；每镜可见范围以该镜 visible_asset_ids 和 locked_shot 为准。当前组的完整参考包不表示所有主体每镜都在场。",
            "audio": "落实已声明的逐镜对白、声线、环境声和动作声；未准备独立音轨不代表静音。BGM、配音替换和字幕按制作方向与用户要求决定，不因有对白而自动另配音或烧字幕。",
            "dialogue": _DIALOGUE_POLICY,
            "output": "输出不超过7000字符的官方 H3 Prompt IR；删除重复表达但保留全部事实。无法忠实容纳时明确报告冲突，不截断台词或私自改分镜。",
        },
    }
    if source_prompt is not None:
        payload["source_prompt"] = source_prompt
    if revision_instructions is not None:
        payload["compiler_feedback"] = revision_instructions
    return (
        _compiler_instructions(document, method_skill_ids=method_skill_ids),
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def validate_language_generation_unit_prompt(
    prompt: str,
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
) -> list[str]:
    try:
        return validate_h3_generation_unit_prompt_ir(
            prompt,
            document,
            unit,
            shots,
            route=route,
            duration_seconds=duration_seconds,
            media_inputs=media_inputs,
        )
    except ValueError as exc:
        raise LanguagePromptValidationError(str(exc)) from exc


def validate_language_prompt(
    prompt: str,
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT,
    duration_seconds: int,
    media_inputs: list[H3MediaInput] | None = None,
) -> list[str]:
    try:
        return validate_h3_prompt_ir(
            prompt,
            document,
            shot,
            route=route,
            duration_seconds=duration_seconds,
            media_inputs=media_inputs,
        )
    except ValueError as exc:
        raise LanguagePromptValidationError(f"语言模型 {exc}") from exc
