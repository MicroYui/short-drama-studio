"""GPT-assisted H3 prompt compilation with lightweight locked-fact checks."""

from __future__ import annotations

import json
from functools import lru_cache

from short_drama.h3_models import H3MediaInput
from short_drama.models import AssetKind, ProjectDocument, ShotPlan, VideoGenerationRoute
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
)
from short_drama.skills.loader import load_provider_prompt_skills
from short_drama.providers.h3_prompt_skill import (
    prompt_schema_for_route,
    validate_h3_prompt_ir,
)
from short_drama.providers.h3_media_bindings import resolve_h3_media_bindings


class LanguagePromptValidationError(ValueError):
    """The generated prompt changed or omitted a locked production fact."""


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
    for beat in shot.timeline_beats:
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
) -> tuple[list[str], dict[str, object]]:
    bible = next(
        version
        for version in document.bible_versions
        if version.id == document.project.current_bible_version_id
    )
    explicit_asset_ids = set(_explicit_shot_asset_ids(document, shot, media_inputs))
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
    bindings: list[dict[str, object]] = []
    for resolved in resolve_h3_media_bindings(document, media_inputs, []):
        media = resolved.media
        binding: dict[str, object] = {
            "role": media.role.value,
            "kind": media.kind.value,
            "provider_label": resolved.provider_label,
            "prompt_label": resolved.prompt_label,
            "reference_alias": f"@{resolved.alias}",
            "subject": resolved.subject,
            "subject_asset_id": resolved.subject_asset_id,
            "reference_tags": list(resolved.tags),
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
    skill = production_skill(document.project.production_profile)
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.VIDEO_PROMPT_COMPILER,),
        include_constitution=False,
    )
    instructions = (
        f"你是 MiniMax-H3 的模型专属 Prompt Compiler，当前制作类型为{skill.medium}。\n"
        "制作类型策略只用于忠实编译已经锁定的镜头，不得在此阶段改写导演意图："
        f"{skill_prompt.text}\n{_h3_prompt_skill_instructions()}"
    )
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
    scene = next(
        (item for item in script.structured_scenes if item.id == shot.scene_id),
        None,
    )
    route_guidance = {
        VideoGenerationRoute.DIRECT: "无图像锚点，提示词必须完整建立首帧人物、空间、构图和动作起点。",
        VideoGenerationRoute.FRAME_FIRST: "首帧图已批准；从首帧精确起步，保持人物、服装、空间、构图和道具外观。",
        VideoGenerationRoute.FRAME_LAST: "尾帧图已批准；根据锁定镜头推导相容起始状态，让动作、机位和物理状态逐步收敛到尾帧，不得把尾帧当作开场。",
        VideoGenerationRoute.FRAME_FIRST_LAST: "首尾帧均已批准；连续完成中间动作，不改变两端已确定的身份、构图和道具状态。",
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
        "task": "在不改变任何锁定事实的前提下，把 source_prompt 编译为 required_prompt_schema 指定的 MiniMax-H3 Prompt IR。",
        "production_profile": document.project.production_profile.value,
        "route": route.value,
        "required_prompt_schema": prompt_schema_for_route(route),
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
            "timeline_beats": [
                beat.model_dump(mode="json") for beat in shot.timeline_beats
            ],
            "continuity": shot.continuity.model_dump(mode="json"),
            "transition_out": shot.transition_out,
        },
        "previous_shot_terminal_state": _previous_shot_terminal_state(document, shot),
        "structured_scene_context": (
            scene.model_dump(mode="json") if scene is not None else None
        ),
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
                "speaker_character_id": line.speaker_character_id,
                "spoken_text": line.spoken_text,
                "audio_kind": line.audio_kind.value,
            }
            for line in dialogue
        ],
        "visible_assets": visible_asset_names,
        "visible_asset_bible_for_disambiguation_only": visible_bible,
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


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
        raise LanguagePromptValidationError(f"GPT-5.6-sol {exc}") from exc
