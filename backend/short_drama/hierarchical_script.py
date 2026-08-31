"""Scene-local screenplay writing for every supported episode duration.

The global Story Architect owns a compact blueprint.  This module expands one
scene node at a time with a bounded context window, so a three-minute episode and
a thirty-minute episode use the same mechanism and differ only in node count.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from short_drama.models import AudioKind, CreateProjectRequest, SourceMode
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
    ScriptRevisionContextPlan,
    ScriptRevisionScope,
    apply_episode_outline_patch,
    build_episode_manifest,
)


@dataclass(frozen=True)
class SceneWritingResult:
    patch: EpisodeScenePatch
    used_fallback: bool
    repaired: bool
    warnings: list[str]


@dataclass(frozen=True)
class SceneRevisionResult:
    patch: EpisodeScenePatch
    revised: bool
    warnings: list[str]


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
    return re.sub(r"\s+", " ", str(error)).strip()[:max_chars]


def compact_script_retrieval_query(value: str, *, max_chars: int = 4000) -> str:
    """Bound search text without truncating the Agent's complete edit instruction."""

    compact = re.sub(r"\s+", " ", value).strip()
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
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
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
    names = [character.name for character in outline.characters]
    first = names[0]
    second = names[min(1, len(names) - 1)]
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
            speaker=templates[index % len(templates)][0],
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
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER,),
        context="\n".join(
            (
                request.input_text,
                scene.model_dump_json(),
                *(patch.model_dump_json() for patch in previous_patches[-2:]),
            )
        ),
    )
    previous_full = previous_patches[-2:]
    older_summaries = [
        {
            "scene_sequence": patch.scene_sequence,
            "summary": patch.summary,
            "continuity_out": patch.continuity_out.model_dump(mode="json"),
        }
        for patch in previous_patches[:-2]
    ]
    next_nodes = [
        item.model_dump(mode="json")
        for item in blueprint.scenes[scene.sequence : scene.sequence + 2]
    ]
    instructions = (
        "You are the Scene Writer Agent for a Mandarin live-action vertical production. Return "
        "JSON only and match EpisodeScenePatch exactly. Expand only the supplied scene node into "
        "finished dialogue while preserving its sequence, heading, summary, narrative_purpose, "
        "playable_actions, duration, locations, and continuity contract verbatim. Do not rewrite "
        "the global blueprint, other scenes, cast, locations, props, or style. Do not add shots, "
        "camera moves, keyframes, editing, or provider prompts. Let declared actions, reactions, and "
        "blocking carry state changes directly; dialogue is only what these particular people would "
        "naturally say to one another in the immediate situation. Never verbalize a scene contract, "
        "continuity summary, relationship-state label, or production instruction merely to make it "
        "explicit. Fast audiovisual pacing must not be translated into compressed or slogan-like speech. "
        "Dialogue volume must plausibly fit the scene duration but there is no fixed line quota and not "
        "every line needs to add a new fact. Avoid exposition that merely repeats the summary. Carry "
        "forward established facts and open threads from the compact context, and "
        "do not resolve future nodes early. For SCRIPT_LOCKED, copy the supplied locked scene lines "
        "verbatim and do not invent dialogue. "
        f"{skill_prompt.text} "
        "The result is a scene-local patch, not a whole-episode rewrite. Do not add fields outside "
        "the schema."
    )
    locked_lines = _scene_lines_from_outline(blueprint, scene)
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "source_text": request.input_text.strip(),
        "episode_manifest": build_episode_manifest(blueprint),
        "current_scene_node": scene.model_dump(mode="json"),
        "locked_scene_lines": [line.model_dump(mode="json") for line in locked_lines],
        "recent_full_scenes": [patch.model_dump(mode="json") for patch in previous_full],
        "older_scene_summaries": older_summaries[-8:],
        "next_scene_nodes": next_nodes,
        "output_schema": EpisodeScenePatch.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


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
        ("continuity_in", patch.continuity_in, scene.continuity_in),
        ("continuity_out", patch.continuity_out, scene.continuity_out),
    )
    changed = [name for name, actual, expected in immutable_pairs if actual != expected]
    if changed:
        raise ValueError(
            "Scene Writer changed blueprint-owned fields: " + ", ".join(changed)
        )
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        expected = _scene_lines_from_outline(blueprint, scene)
        if patch.lines != expected:
            raise ValueError("Scene Writer changed locked dialogue")
    known_characters = {character.name for character in blueprint.characters}
    invalid_speakers = sorted(
        {
            line.speaker
            for line in patch.lines
            if line.audio_kind == AudioKind.DIALOGUE
            and line.speaker not in known_characters
        }
    )
    if invalid_speakers:
        raise ValueError(
            "Scene Writer used undeclared speaking characters: "
            + ", ".join(invalid_speakers)
        )


def write_story_scene(
    request: CreateProjectRequest,
    blueprint: EpisodeOutline,
    scene: PlannedSceneContract,
    previous_patches: list[EpisodeScenePatch],
    client: LanguageProviderClient | None,
) -> SceneWritingResult:
    """Write one recoverable scene using recent detail and older summaries."""

    fallback = _fallback_scene_patch(blueprint, scene)
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return SceneWritingResult(
            patch=fallback,
            used_fallback=False,
            repaired=False,
            warnings=[],
        )
    if client is None:
        return SceneWritingResult(
            patch=fallback,
            used_fallback=True,
            repaired=False,
            warnings=[
                f"GPT-5.6-sol 未配置，第 {scene.sequence} 场已使用本地可恢复剧本补全。"
            ],
        )
    instructions, input_text = _scene_writer_prompt(
        request,
        blueprint,
        scene,
        previous_patches,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=7000,
        )
        repaired = False
        repair_warning: str | None = None
        try:
            patch = EpisodeScenePatch.model_validate_json(_strip_json_fence(output))
            _validate_scene_patch(request, blueprint, scene, patch)
        except ValueError as first_error:
            payload = json.loads(input_text)
            payload["invalid_scene_patch_json"] = output
            payload["validation_error"] = str(first_error)
            repaired_output = client.generate_text(
                instructions=(
                    instructions
                    + " Repair only the invalid JSON or blueprint-contract violation. Return one "
                    "complete corrected EpisodeScenePatch JSON object."
                ),
                input_text=json.dumps(payload, ensure_ascii=False, indent=2),
                image_urls=[],
                max_output_tokens=7000,
            )
            patch = EpisodeScenePatch.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_scene_patch(request, blueprint, scene, patch)
            repaired = True
            repair_warning = (
                f"第 {scene.sequence} 场 Scene Writer 首次输出未通过局部结构校验，"
                "已修复后采用："
                + _compact_error(first_error)
            )
        return SceneWritingResult(
            patch=patch,
            used_fallback=False,
            repaired=repaired,
            warnings=[repair_warning] if repair_warning is not None else [],
        )
    except (LanguageProviderError, ValueError) as error:
        return SceneWritingResult(
            patch=fallback,
            used_fallback=True,
            repaired=False,
            warnings=[
                f"第 {scene.sequence} 场 Scene Writer 不可用，仅该场降级为本地补全："
                + _compact_error(error)
            ],
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
) -> ScriptRevisionContextPlan:
    """Recommend an editable local scope; this is advice, never a hard gate."""

    candidate_sequences = list(
        dict.fromkeys(
            hit.document.scene_sequence
            for hit in search_hits
            if hit.document.scene_sequence is not None
        )
    )
    fallback_sequences = candidate_sequences[:6]
    if not fallback_sequences:
        fallback_sequences = [scene.sequence for scene in outline.scenes[:3]]
    bounded_retrieval_query = compact_script_retrieval_query(
        retrieval_query or instruction
    )
    fallback = ScriptRevisionContextPlan(
        scope=ScriptRevisionScope.SCENE_PATCH,
        affected_scene_sequences=fallback_sequences,
        retrieval_query=bounded_retrieval_query,
        rationale="按剧本文档检索结果选择最相关场次；用户可直接指定其他场次覆盖。",
        preserve=["未选中场次", "全局资产契约", "目标总时长"],
    )
    if client is None:
        return fallback
    instructions = (
        "You are a script-change scope Agent. Return JSON only and match "
        "ScriptRevisionContextPlan exactly. Recommend the smallest coherent set of existing "
        "scene sequences needed for the user's edit. Use SCENE_PATCH whenever scene-local patches "
        "can express the change. Use FULL_OUTLINE only when the title, central premise, global cast "
        "or location contract, total duration, or most of the causal spine must change. This is an "
        "advisory recommendation and user_overridable must remain true. Do not invent scene IDs. "
        "Keep retrieval_query at 4000 characters or fewer; it is a compact search expression, "
        "not a copy of the complete revision instruction."
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
            input_text=json.dumps(payload, ensure_ascii=False, indent=2),
            image_urls=[],
            max_output_tokens=3000,
        )
        plan = ScriptRevisionContextPlan.model_validate_json(_strip_json_fence(output))
        known = {scene.sequence for scene in outline.scenes}
        unknown = sorted(set(plan.affected_scene_sequences) - known)
        if unknown:
            raise ValueError(f"Scope Agent selected unknown scenes: {unknown}")
        return plan
    except (LanguageProviderError, ValueError):
        return fallback


def revise_story_scene(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    instruction: str,
    selected_scene_sequences: list[int],
    related_hits: list[PlanningScriptSearchHit],
    client: LanguageProviderClient | None,
) -> SceneRevisionResult:
    """Create one independently applicable scene patch for a user-directed edit."""

    original = _scene_patch_from_contract(
        scene,
        _scene_lines_from_outline(outline, scene),
    )
    if client is None:
        return SceneRevisionResult(
            patch=original,
            revised=False,
            warnings=[
                f"第 {scene.sequence} 场局部修改需要语言模型，已保留原稿。"
            ],
        )
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER,),
        context="\n".join(
            (
                request.input_text,
                instruction,
                original.model_dump_json(),
                *(hit.document.body for hit in related_hits[:8]),
            )
        ),
    )
    instructions = (
        "You are a scene-local screenplay revision Agent. Return JSON only and match "
        "EpisodeScenePatch exactly. Apply the user's instruction only to the current scene while "
        "coordinating with the supplied selected-scene context. Preserve scene_sequence and "
        "target_duration_seconds. Use only globally declared locations and dialogue characters. "
        "You may revise this scene's heading, summary, narrative_purpose, playable_actions, "
        "continuity handoff, and dialogue when the instruction requires it. Do not mutate the "
        "episode title, global cast/assets/style, unrelated scenes, shots, media, or timeline. "
        "Do not add explanation to solve a downstream staging or generation problem. "
        f"{skill_prompt.text}"
    )
    payload = {
        "user_instruction": instruction,
        "episode_manifest": build_episode_manifest(outline),
        "selected_scene_sequences": selected_scene_sequences,
        "current_scene": original.model_dump(mode="json"),
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
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=json.dumps(payload, ensure_ascii=False, indent=2),
            image_urls=[],
            max_output_tokens=7000,
        )
        patch = EpisodeScenePatch.model_validate_json(_strip_json_fence(output))
        if patch.scene_sequence != scene.sequence:
            raise ValueError("Scene revision changed scene_sequence")
        if patch.target_duration_seconds != scene.target_duration_seconds:
            raise ValueError("Scene revision changed target_duration_seconds")
        known_locations = {location.name for location in outline.locations}
        if not set(patch.location_names) <= known_locations:
            raise ValueError("Scene revision used undeclared locations")
        known_characters = {character.name for character in outline.characters} | {
            line.speaker for line in original.lines
        }
        invalid_speakers = sorted(
            {
                line.speaker
                for line in patch.lines
                if line.audio_kind == AudioKind.DIALOGUE
                and line.speaker not in known_characters
            }
        )
        if invalid_speakers:
            raise ValueError(
                "Scene revision used undeclared speaking characters: "
                + ", ".join(invalid_speakers)
            )
        if request.source_mode == SourceMode.SCRIPT_LOCKED and patch.lines != original.lines:
            raise ValueError("Scene revision changed locked dialogue")
        return SceneRevisionResult(patch=patch, revised=patch != original, warnings=[])
    except (LanguageProviderError, ValueError) as error:
        return SceneRevisionResult(
            patch=original,
            revised=False,
            warnings=[
                f"第 {scene.sequence} 场局部修改未采用，已保留原稿："
                + _compact_error(error)
            ],
        )
