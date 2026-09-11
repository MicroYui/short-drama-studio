"""Hierarchical script documents and scene-local outline patches.

Long-form script Agents should not receive or regenerate the complete episode for
every local change.  This module projects an ``EpisodeOutline`` into compact,
searchable documents and provides deterministic patch application.  Retrieval and
creative scope selection remain advisory Agent tools; the merge itself is ordinary
typed product logic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from short_drama.models import DomainModel, SceneContinuitySummary
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedDialogueLine,
    PlannedSceneContract,
)


class PlanningScriptDocumentKind(StrEnum):
    STORY_BIBLE = "STORY_BIBLE"
    SCENE = "SCENE"


class PlanningScriptDocument(DomainModel):
    """One versioned, retrieval-ready unit from a planning outline."""

    run_id: str = Field(min_length=1, max_length=160)
    revision_round: int = Field(ge=0)
    document_id: str = Field(min_length=1, max_length=240)
    kind: PlanningScriptDocumentKind
    scene_sequence: int | None = Field(default=None, ge=1, le=500)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=4000)
    character_names: list[str] = Field(default_factory=list[str], max_length=32)
    location_names: list[str] = Field(default_factory=list[str], max_length=32)
    prop_names: list[str] = Field(default_factory=list[str], max_length=32)
    search_metadata: str = Field(default="", max_length=12000)
    body: str = Field(default="", max_length=60000)
    scene: PlannedSceneContract | None = None
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=500,
    )
    created_at: str

    @model_validator(mode="after")
    def validate_document_shape(self) -> PlanningScriptDocument:
        if self.kind == PlanningScriptDocumentKind.SCENE:
            if self.scene_sequence is None or self.scene is None:
                raise ValueError("Scene documents require scene_sequence and scene")
            if self.scene.sequence != self.scene_sequence:
                raise ValueError("Scene document sequence must match its scene payload")
        elif self.scene_sequence is not None or self.scene is not None or self.lines:
            raise ValueError("Story-bible documents cannot contain a scene payload")
        return self


class PlanningScriptSearchRequest(DomainModel):
    query: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=8, ge=1, le=24)
    include_story_bible: bool = True


class PlanningScriptSearchHit(DomainModel):
    document: PlanningScriptDocument
    bm25_score: float
    matched_by: list[str] = Field(default_factory=list[str], max_length=12)


class PlanningScriptSearchResponse(DomainModel):
    run_id: str
    revision_round: int = Field(ge=0)
    query: str
    hits: list[PlanningScriptSearchHit] = Field(
        default_factory=list[PlanningScriptSearchHit],
        max_length=24,
    )


class PlanningSceneRevisionRequest(DomainModel):
    """Natural-language local edit; explicit scenes override Agent scope selection."""

    instruction: str = Field(min_length=1, max_length=8000)
    scene_sequences: list[int] = Field(default_factory=list[int])

    @model_validator(mode="after")
    def validate_sequences(self) -> PlanningSceneRevisionRequest:
        if any(sequence < 1 or sequence > 500 for sequence in self.scene_sequences):
            raise ValueError("Scene sequences must be between 1 and 500")
        if len(self.scene_sequences) != len(set(self.scene_sequences)):
            raise ValueError("Scene sequences must be unique")
        return self


class ScriptRevisionScope(StrEnum):
    SCENE_PATCH = "SCENE_PATCH"
    FULL_OUTLINE = "FULL_OUTLINE"


class ScriptRevisionContextPlan(DomainModel):
    """Agent recommendation for the smallest coherent script-edit scope."""

    scope: ScriptRevisionScope
    affected_scene_sequences: list[int] = Field(default_factory=list[int])
    retrieval_query: str = Field(default="", max_length=4000)
    rationale: str = Field(min_length=1, max_length=1600)
    preserve: list[str] = Field(default_factory=list[str], max_length=24)
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_scope(self) -> ScriptRevisionContextPlan:
        if not self.user_overridable:
            raise ValueError("Script revision context plans must remain user-overridable")
        if self.scope == ScriptRevisionScope.SCENE_PATCH and not self.affected_scene_sequences:
            raise ValueError("SCENE_PATCH requires at least one affected scene")
        if len(self.affected_scene_sequences) != len(set(self.affected_scene_sequences)):
            raise ValueError("Affected scene sequences must be unique")
        return self


class SceneDurationRevisionConstraint(DomainModel):
    """Typed permission and advisory episode-duration context for one scene patch.

    The episode target is a creative planning target, not a millisecond-exact
    acceptance rule.  ``required_scene_target_seconds`` remains readable for
    older persisted runs, but new runs do not use it to force compensation in a
    particular scene.
    """

    allow_change: bool = False
    balance_episode_on_this_scene: bool = False
    episode_target_seconds: float = Field(ge=4.0)
    current_episode_scene_total_seconds: float = Field(ge=4.0)
    required_scene_target_seconds: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_balance_contract(self) -> SceneDurationRevisionConstraint:
        if self.balance_episode_on_this_scene and not self.allow_change:
            raise ValueError("Episode duration balancing requires scene duration permission")
        if self.required_scene_target_seconds is not None and not self.allow_change:
            raise ValueError("A scene duration suggestion requires change permission")
        return self


class EpisodeScenePatch(DomainModel):
    """Replacement content for one scene; global dialogue indices are rebuilt.

    ``performance_timing_assessment`` is Agent-authored evidence for trace and
    later review.  It never decides whether the patch is accepted.
    """

    scene_sequence: int = Field(ge=1, le=500)
    heading: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1600)
    narrative_purpose: str = Field(min_length=1, max_length=800)
    playable_actions: list[str] = Field(default_factory=list[str], max_length=24)
    screenplay_text: str = Field(default="", max_length=60000)
    target_duration_seconds: float = Field(gt=0.0)
    location_names: list[str] = Field(min_length=1, max_length=6)
    prop_ids: list[str] = Field(default_factory=list[str])
    continuity_in: SceneContinuitySummary
    continuity_out: SceneContinuitySummary
    state_source: Literal["OUTLINE", "SCREENPLAY"] = Field(default="OUTLINE", description=(
        "Whether summary and continuity were copied from a plan or authored from the actual screenplay. "
        "Historical patches without this field remain OUTLINE and must not be treated as fulfilled events."
    ))
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=500,
    )
    performance_timing_assessment: str | None = Field(
        default=None,
        min_length=1,
        max_length=1600,
    )


class EpisodeOutlinePatch(DomainModel):
    """A model-produced edit proposal that cannot mutate unrelated scenes."""

    base_revision_round: int = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=1600)
    scene_patches: list[EpisodeScenePatch] = Field(min_length=1)
    preserve: list[str] = Field(default_factory=list[str], max_length=24)
    user_overridable: bool = True

    @model_validator(mode="after")
    def validate_patch(self) -> EpisodeOutlinePatch:
        sequences = [patch.scene_sequence for patch in self.scene_patches]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Each scene may be patched only once")
        if not self.user_overridable:
            raise ValueError("Episode outline patches must remain user-overridable")
        return self


def scene_dialogue_lines(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
) -> list[PlannedDialogueLine]:
    """Resolve a scene's 1-based dialogue indices into independent line values."""

    return [
        outline.lines[index - 1].model_copy(deep=True)
        for index in scene.dialogue_line_indices
        if 1 <= index <= len(outline.lines)
    ]


def build_episode_manifest(outline: EpisodeOutline) -> dict[str, Any]:
    """Return global and scene metadata without embedding the complete dialogue."""

    return {
        "title": outline.title,
        "story_summary": outline.story_summary,
        "target_duration_seconds": outline.target_duration_seconds,
        "characters": [character.model_dump(mode="json") for character in outline.characters],
        "locations": [location.model_dump(mode="json") for location in outline.locations],
        "props": [prop.model_dump(mode="json") for prop in outline.props],
        "style": outline.style.model_dump(mode="json"),
        "ending_contract": (
            outline.ending_contract.model_dump(mode="json")
            if outline.ending_contract is not None
            else None
        ),
        "story_intent": (
            outline.story_intent.model_dump(mode="json")
            if outline.story_intent is not None
            else None
        ),
        "scenes": [
            {
                "sequence": scene.sequence,
                "heading": scene.heading,
                "summary": scene.summary,
                "narrative_purpose": scene.narrative_purpose,
                "playable_actions": list(scene.playable_actions),
                "target_duration_seconds": scene.target_duration_seconds,
                "location_names": list(scene.location_names),
                "prop_ids": list(scene.prop_ids),
                "dialogue_line_count": len(scene.dialogue_line_indices),
                "continuity_in": scene.continuity_in.model_dump(mode="json"),
                "continuity_out": scene.continuity_out.model_dump(mode="json"),
            }
            for scene in outline.scenes
        ],
    }


def build_planning_script_documents(
    *,
    run_id: str,
    revision_round: int,
    outline: EpisodeOutline,
    created_at: str,
) -> list[PlanningScriptDocument]:
    """Project an outline into a story Bible and one document per scene."""

    all_character_names = [character.name for character in outline.characters]
    all_location_names = [location.name for location in outline.locations]
    all_prop_names = [prop.name for prop in outline.props]
    character_name_by_id = {
        character.character_id: character.name
        for character in outline.characters
        if character.character_id is not None
    }
    intent = outline.story_intent
    intent_lines = (
        []
        if intent is None
        else [
            f"观众体验：{intent.audience_experience}",
            f"戏剧引擎：{intent.dramatic_engine}",
            f"推进策略：{intent.progression_strategy}",
            f"调性与可信度：{intent.tone_and_plausibility}",
            "需保护的元素：" + "；".join(intent.protected_elements),
            "需避免的偏移：" + "；".join(intent.drift_risks),
        ]
    )
    bible_body = "\n".join(
        [
            f"标题：{outline.title}",
            f"故事摘要：{outline.story_summary}",
            *intent_lines,
            "角色："
            + "；".join(
                f"{character.name}，{character.appearance}，{character.wardrobe}，"
                f"{character.performance}"
                for character in outline.characters
            ),
            "地点："
            + "；".join(
                f"{location.name}，{location.layout}，{location.lighting}"
                for location in outline.locations
            ),
            "道具："
            + "；".join(f"{prop.name}，{prop.appearance}" for prop in outline.props),
            f"风格：{outline.style.genre}；{outline.style.cinematography}；"
            f"{outline.style.color}；{outline.style.texture}",
            (
                "结尾合同："
                f"{outline.ending_contract.resolved_mode.value}；"
                f"本集回报={outline.ending_contract.episode_payoff}；"
                f"最后落点={outline.ending_contract.final_button}"
                if outline.ending_contract is not None
                else "结尾合同：旧版本未记录"
            ),
        ]
    )
    documents = [
        PlanningScriptDocument(
            run_id=run_id,
            revision_round=revision_round,
            document_id=f"{run_id}:r{revision_round}:bible",
            kind=PlanningScriptDocumentKind.STORY_BIBLE,
            title=outline.title,
            summary=outline.story_summary,
            character_names=all_character_names,
            location_names=all_location_names,
            prop_names=all_prop_names,
            search_metadata=(
                "全剧 设定 故事 人物 地点 道具 风格 创作意图 观众体验 戏剧引擎 "
                + " ".join(all_character_names + all_location_names + all_prop_names)
                + (
                    ""
                    if intent is None
                    else " "
                    + " ".join(
                        [
                            intent.audience_experience,
                            intent.dramatic_engine,
                            intent.progression_strategy,
                            intent.tone_and_plausibility,
                            *intent.protected_elements,
                            *intent.drift_risks,
                        ]
                    )
                )
            ),
            body=bible_body,
            created_at=created_at,
        )
    ]
    for scene in outline.scenes:
        lines = scene_dialogue_lines(outline, scene)
        dialogue_text = "\n".join(f"{line.speaker}：{line.text}" for line in lines)
        continuity_text = "\n".join(
            [
                f"进入场景：{scene.continuity_in.setting}",
                "进入人物状态：" + "；".join(scene.continuity_in.character_states),
                "进入道具状态：" + "；".join(scene.continuity_in.prop_states),
                "已知信息：" + "；".join(scene.continuity_in.known_facts),
                "待解决线索：" + "；".join(scene.continuity_in.open_threads),
                f"离开场景：{scene.continuity_out.setting}",
                "离开人物状态：" + "；".join(scene.continuity_out.character_states),
                "离开道具状态：" + "；".join(scene.continuity_out.prop_states),
                "新增信息：" + "；".join(scene.continuity_out.known_facts),
                "遗留线索：" + "；".join(scene.continuity_out.open_threads),
            ]
        )
        scene_text = "\n".join(
            [
                scene.heading,
                scene.summary,
                scene.narrative_purpose,
                "可执行动作：" + "；".join(scene.playable_actions),
                continuity_text,
                scene.screenplay_text or dialogue_text,
            ]
        )
        character_names = list(
            dict.fromkeys(
                character_name_by_id[line.speaker_character_id]
                for line in lines
                if line.speaker_character_id in character_name_by_id
            )
        )
        prop_names: list[str] = []
        documents.append(
            PlanningScriptDocument(
                run_id=run_id,
                revision_round=revision_round,
                document_id=f"{run_id}:r{revision_round}:scene:{scene.sequence:03d}",
                kind=PlanningScriptDocumentKind.SCENE,
                scene_sequence=scene.sequence,
                title=scene.heading,
                summary=scene.summary,
                character_names=character_names,
                location_names=list(scene.location_names),
                prop_names=prop_names,
                search_metadata=" ".join(
                    [
                        f"第{scene.sequence}场",
                        scene.heading,
                        scene.narrative_purpose,
                        *character_names,
                        *scene.location_names,
                        *prop_names,
                        *scene.continuity_in.open_threads,
                        *scene.continuity_out.open_threads,
                    ]
                ),
                body=scene_text,
                scene=scene.model_copy(deep=True),
                lines=lines,
                created_at=created_at,
            )
        )
    return documents


def apply_episode_outline_patch(
    outline: EpisodeOutline,
    patch: EpisodeOutlinePatch,
) -> EpisodeOutline:
    """Apply scene replacements and deterministically rebuild global line indices."""

    existing_sequences = {scene.sequence for scene in outline.scenes}
    patch_by_sequence = {
        scene_patch.scene_sequence: scene_patch for scene_patch in patch.scene_patches
    }
    unknown = sorted(set(patch_by_sequence) - existing_sequences)
    if unknown:
        raise ValueError(f"Patch references unknown scene sequences: {unknown}")

    old_lines_by_sequence = {
        scene.sequence: scene_dialogue_lines(outline, scene) for scene in outline.scenes
    }
    rebuilt_lines: list[PlannedDialogueLine] = []
    rebuilt_scenes: list[PlannedSceneContract] = []
    for scene in outline.scenes:
        scene_patch = patch_by_sequence.get(scene.sequence)
        if scene_patch is None:
            next_scene = scene.model_copy(deep=True)
            next_lines = old_lines_by_sequence[scene.sequence]
        else:
            next_scene = PlannedSceneContract(
                sequence=scene_patch.scene_sequence,
                heading=scene_patch.heading,
                summary=scene_patch.summary,
                narrative_purpose=scene_patch.narrative_purpose,
                playable_actions=list(scene_patch.playable_actions),
                screenplay_text=scene_patch.screenplay_text,
                target_duration_seconds=scene_patch.target_duration_seconds,
                dialogue_line_indices=[],
                location_names=list(scene_patch.location_names),
                prop_ids=list(scene_patch.prop_ids),
                continuity_in=scene_patch.continuity_in.model_copy(deep=True),
                continuity_out=scene_patch.continuity_out.model_copy(deep=True),
            )
            next_lines = [line.model_copy(deep=True) for line in scene_patch.lines]
        first_index = len(rebuilt_lines) + 1
        rebuilt_lines.extend(next_lines)
        next_scene.dialogue_line_indices = list(
            range(first_index, first_index + len(next_lines))
        )
        rebuilt_scenes.append(next_scene)

    payload = outline.model_dump(mode="python")
    payload["lines"] = [line.model_dump(mode="python") for line in rebuilt_lines]
    payload["scenes"] = [scene.model_dump(mode="python") for scene in rebuilt_scenes]
    return EpisodeOutline.model_validate(payload)
