"""AI-native semantic change planning over existing production tools.

The planner selects semantic roots. The harness resolves project dependencies and
returns an editable impact plan; it does not introduce a mandatory approval gate or
silently mutate production artifacts.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import cast

from pydantic import Field, model_validator

from short_drama.models import (
    Asset,
    DomainModel,
    Freshness,
    ProjectBibleVersion,
    ProjectDocument,
    ScriptVersion,
    Take,
)
from short_drama.production_profiles import production_skill
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)


class ChangeTargetKind(StrEnum):
    SCRIPT_LINE = "SCRIPT_LINE"
    SCENE = "SCENE"
    CHARACTER = "CHARACTER"
    LOCATION = "LOCATION"
    PROP = "PROP"
    ASSET = "ASSET"
    SHOT = "SHOT"
    TAKE = "TAKE"
    TIMELINE = "TIMELINE"


class ChangePlanTool(StrEnum):
    EDIT_SCRIPT = "script.edit_or_agent_revise"
    CREATE_ASSET_VERSION = "asset.create_or_edit_version"
    UPDATE_SHOT_PLAN = "shot.update_plan"
    UPDATE_REFERENCE_PLAN = "shot.update_reference_plan"
    REGENERATE_SHOTS = "video.regenerate_shots"
    REBUILD_TIMELINE = "timeline.rebuild"


class SemanticChangeTarget(DomainModel):
    kind: ChangeTargetKind
    target_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=600)


class SemanticChangeIntent(DomainModel):
    summary: str = Field(min_length=1, max_length=1200)
    requested_change: str = Field(min_length=1, max_length=2000)
    targets: list[SemanticChangeTarget] = Field(
        default_factory=list[SemanticChangeTarget],
        max_length=64,
    )
    preserve: list[str] = Field(default_factory=list[str], max_length=24)
    needs_user_clarification: bool = False


class SemanticChangePlanRequest(DomainModel):
    instruction: str = Field(min_length=1, max_length=8000)
    explicit_target_ids: list[str] = Field(default_factory=list[str], max_length=64)


class ChangeImpactStep(DomainModel):
    tool: ChangePlanTool
    target_ids: list[str] = Field(default_factory=list[str], max_length=500)
    reason: str = Field(min_length=1, max_length=1200)


class SemanticChangePlan(DomainModel):
    project_id: str
    instruction: str
    intent: SemanticChangeIntent
    affected_asset_ids: list[str] = Field(default_factory=list[str], max_length=200)
    affected_script_line_ids: list[str] = Field(default_factory=list[str], max_length=500)
    affected_scene_ids: list[str] = Field(default_factory=list[str], max_length=200)
    affected_shot_ids: list[str] = Field(default_factory=list[str], max_length=500)
    stale_take_ids: list[str] = Field(default_factory=list[str], max_length=2000)
    steps: list[ChangeImpactStep] = Field(default_factory=list[ChangeImpactStep], max_length=12)
    warnings: list[str] = Field(default_factory=list[str], max_length=24)
    user_editable: bool = True
    applies_changes: bool = False

    @model_validator(mode="after")
    def preserve_user_control(self) -> SemanticChangePlan:
        if not self.user_editable:
            raise ValueError("Semantic change plans must remain user-editable")
        if self.applies_changes:
            raise ValueError("Planning a semantic change must not mutate artifacts")
        return self


def _current_script(document: ProjectDocument) -> ScriptVersion:
    script = next(
        (
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        ),
        None,
    )
    if script is None:
        raise ValueError("Project current ScriptVersion is missing")
    return script


def _current_bible(document: ProjectDocument) -> ProjectBibleVersion:
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )
    if bible is None:
        raise ValueError("Project current ProjectBibleVersion is missing")
    return bible


def _asset_descriptor(asset: Asset) -> str:
    current = next(
        (item for item in asset.versions if item.id == asset.current_version_id),
        None,
    )
    return current.descriptor if current is not None else ""


def _project_index(
    document: ProjectDocument,
    takes: list[Take],
) -> tuple[list[dict[str, object]], dict[tuple[ChangeTargetKind, str], dict[str, object]]]:
    script = _current_script(document)
    bible = _current_bible(document)
    items: list[dict[str, object]] = []

    def add(kind: ChangeTargetKind, target_id: str, **metadata: object) -> None:
        items.append({"kind": kind.value, "target_id": target_id, **metadata})

    for line in script.lines:
        add(
            ChangeTargetKind.SCRIPT_LINE,
            line.id,
            name=f"{line.speaker}台词",
            speaker=line.speaker,
            speaker_character_id=line.speaker_character_id,
            text=line.spoken_text,
        )
    for scene in script.structured_scenes:
        add(
            ChangeTargetKind.SCENE,
            scene.id,
            name=scene.heading,
            summary=scene.summary,
            line_ids=scene.line_ids,
        )
    for character in bible.characters:
        add(
            ChangeTargetKind.CHARACTER,
            character.id,
            name=character.name,
            appearance=character.appearance,
            wardrobe=character.wardrobe,
            performance=character.performance,
        )
    for location in bible.locations:
        add(
            ChangeTargetKind.LOCATION,
            location.id,
            name=location.name,
            layout=location.layout,
        )
    for prop in bible.props:
        add(
            ChangeTargetKind.PROP,
            prop.id,
            name=prop.name,
            appearance=prop.appearance,
        )
    for asset in document.assets:
        add(
            ChangeTargetKind.ASSET,
            asset.id,
            name=asset.name,
            asset_kind=asset.kind.value,
            bible_entity_id=asset.bible_entity_id,
            descriptor=_asset_descriptor(asset),
            current_version_id=asset.current_version_id,
        )
    for shot in document.shots:
        add(
            ChangeTargetKind.SHOT,
            shot.id,
            name=f"镜头 {shot.sequence}",
            sequence=shot.sequence,
            scene_id=shot.scene_id,
            visual=shot.visual,
            dialogue_line_ids=shot.dialogue_line_ids,
        )
    for take in takes:
        add(
            ChangeTargetKind.TAKE,
            take.id,
            name=f"镜头 {take.shot_id} Take {take.candidate_index}",
            shot_id=take.shot_id,
            selected=take.is_selected,
            freshness=take.freshness.value,
        )
    add(
        ChangeTargetKind.TIMELINE,
        document.project.id,
        name="全片时间线",
        current_manifest_id=document.project.current_manifest_id,
    )
    by_key = {
        (ChangeTargetKind(item["kind"]), str(item["target_id"])): item
        for item in items
    }
    return items, by_key


def _intent_prompt(
    document: ProjectDocument,
    request: SemanticChangePlanRequest,
    index: list[dict[str, object]],
) -> tuple[str, str]:
    profile = production_skill(document.project.production_profile)
    instructions = (
        "你是 AI-native 视频制作 Harness 中的语义变更规划 Agent。只返回 JSON，"
        "并严格匹配 SemanticChangeIntent。完整理解用户要求的变更，只从项目索引中选择"
        "语义根目标。不要仅因为镜头依赖某个角色、地点、道具、台词或资产就穷举下游镜头；"
        "Harness 会根据结构化依赖计算影响范围。只能使用索引中精确的 target_id 与 kind 组合。"
        "保留用户未要求改变的身份、表演、故事、调度、声音和剪辑决定。若指令确实存在无法消解的歧义，"
        "将 needs_user_clarification 设为 true，不要臆造目标。这是可由用户编辑的计划，"
        "不是质量门禁，也不代表获得了修改文件的权限。"
        f"当前 Production Skill：{profile.label}。"
    )
    payload = {
        "instruction": request.instruction,
        "project": {
            "id": document.project.id,
            "name": document.project.name,
            "production_profile": document.project.production_profile.value,
        },
        "project_index": index,
        "output_schema": SemanticChangeIntent.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


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


def _validate_intent_targets(
    intent: SemanticChangeIntent,
    by_key: dict[tuple[ChangeTargetKind, str], dict[str, object]],
) -> None:
    keys = [(target.kind, target.target_id) for target in intent.targets]
    if len(keys) != len(set(keys)):
        raise ValueError("Semantic change intent contains duplicate targets")
    unknown = [key for key in keys if key not in by_key]
    if unknown:
        raise ValueError(f"Semantic change intent selected unknown targets: {unknown}")
    if not intent.targets and not intent.needs_user_clarification:
        raise ValueError("Semantic change intent must select a target or request clarification")


def _fallback_intent(
    request: SemanticChangePlanRequest,
    index: list[dict[str, object]],
) -> SemanticChangeIntent:
    by_id = {str(item["target_id"]): item for item in index}
    selected: list[dict[str, object]] = []
    for target_id in request.explicit_target_ids:
        item = by_id.get(target_id)
        if item is not None and item not in selected:
            selected.append(item)
    return SemanticChangeIntent(
        summary=(
            "已根据请求中的显式结构化目标建立变更意图。"
            if selected
            else "当前没有语义 Agent 结果或显式目标 ID，不从用户文本猜测项目对象。"
        ),
        requested_change=request.instruction,
        targets=[
            SemanticChangeTarget(
                kind=ChangeTargetKind(str(item["kind"])),
                target_id=str(item["target_id"]),
                reason="请求中显式提供了该项目对象的稳定 ID。",
            )
            for item in selected
        ],
        preserve=["未被指令点名的内容保持不变"],
        needs_user_clarification=not selected,
    )


def _linked_asset_ids(
    document: ProjectDocument,
    target: SemanticChangeTarget,
) -> set[str]:
    if target.kind == ChangeTargetKind.ASSET:
        return {target.target_id}
    if target.kind not in {
        ChangeTargetKind.CHARACTER,
        ChangeTargetKind.LOCATION,
        ChangeTargetKind.PROP,
    }:
        return set()
    return {
        asset.id
        for asset in document.assets
        if asset.bible_entity_id == target.target_id
    }


def _resolve_impact(
    document: ProjectDocument,
    takes: list[Take],
    intent: SemanticChangeIntent,
    by_key: dict[tuple[ChangeTargetKind, str], dict[str, object]],
) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    script = _current_script(document)
    version_to_asset = {
        version.id: asset.id
        for asset in document.assets
        for version in asset.versions
    }
    reference_assets_by_shot: dict[str, set[str]] = {}
    for entry in document.reference_plan:
        referenced = {
            requirement.subject_asset_id for requirement in entry.requirements
        }
        referenced.update(
            version_to_asset[binding.target_id]
            for binding in entry.bindings
            if binding.target_id in version_to_asset
        )
        referenced.update(beat.actor_asset_id for beat in entry.action_beats)
        for interaction in entry.interactions:
            referenced.add(interaction.actor_asset_id)
            referenced.add(interaction.target_asset_id)
        reference_assets_by_shot[entry.shot_id] = referenced

    asset_ids: set[str] = set()
    line_ids: set[str] = set()
    scene_ids: set[str] = set()
    shot_ids: set[str] = set()
    selected_take_ids: set[str] = set()
    for target in intent.targets:
        metadata = by_key[(target.kind, target.target_id)]
        asset_ids.update(_linked_asset_ids(document, target))
        if target.kind == ChangeTargetKind.SCRIPT_LINE:
            line_ids.add(target.target_id)
        elif target.kind == ChangeTargetKind.SCENE:
            scene_ids.add(target.target_id)
            raw_line_ids = metadata.get("line_ids", [])
            if isinstance(raw_line_ids, list):
                line_ids.update(
                    str(value) for value in cast(list[object], raw_line_ids)
                )
        elif target.kind == ChangeTargetKind.CHARACTER:
            line_ids.update(
                line.id
                for line in script.lines
                if line.speaker_character_id == target.target_id
            )
        elif target.kind == ChangeTargetKind.SHOT:
            shot_ids.add(target.target_id)
        elif target.kind == ChangeTargetKind.TAKE:
            selected_take_ids.add(target.target_id)
            shot_id = str(metadata.get("shot_id", ""))
            if shot_id:
                shot_ids.add(shot_id)
        elif target.kind == ChangeTargetKind.TIMELINE:
            shot_ids.update(shot.id for shot in document.shots)

    for scene in script.structured_scenes:
        if scene.id in scene_ids:
            line_ids.update(scene.line_ids)

    for shot in document.shots:
        if (
            shot.scene_id in scene_ids
            or bool(set(shot.dialogue_line_ids) & line_ids)
            or bool(reference_assets_by_shot.get(shot.id, set()) & asset_ids)
        ):
            shot_ids.add(shot.id)

    stale_take_ids = {
        take.id
        for take in takes
        if take.shot_id in shot_ids and take.freshness == Freshness.CURRENT
    }
    stale_take_ids.update(selected_take_ids)
    return asset_ids, line_ids, scene_ids, shot_ids, stale_take_ids


def _impact_steps(
    *,
    asset_ids: set[str],
    line_ids: set[str],
    scene_ids: set[str],
    shot_ids: set[str],
) -> list[ChangeImpactStep]:
    steps: list[ChangeImpactStep] = []
    if line_ids or scene_ids:
        steps.append(
            ChangeImpactStep(
                tool=ChangePlanTool.EDIT_SCRIPT,
                target_ids=sorted(line_ids | scene_ids),
                reason="用户可直接编辑，也可让 Script Agent 生成新的脚本版本。",
            )
        )
    if asset_ids:
        steps.append(
            ChangeImpactStep(
                tool=ChangePlanTool.CREATE_ASSET_VERSION,
                target_ids=sorted(asset_ids),
                reason="上传、素材库导入、生成或编辑都创建新的资产版本，不覆盖原图。",
            )
        )
        if shot_ids:
            steps.append(
                ChangeImpactStep(
                    tool=ChangePlanTool.UPDATE_REFERENCE_PLAN,
                    target_ids=sorted(shot_ids),
                    reason="让受影响镜头引用新的资产版本。",
                )
            )
    if shot_ids and (line_ids or scene_ids):
        steps.append(
            ChangeImpactStep(
                tool=ChangePlanTool.UPDATE_SHOT_PLAN,
                target_ids=sorted(shot_ids),
                reason="由导演 Agent 仅重算受脚本变化影响的镜头计划。",
            )
        )
    if shot_ids:
        steps.append(
            ChangeImpactStep(
                tool=ChangePlanTool.REGENERATE_SHOTS,
                target_ids=sorted(shot_ids),
                reason="仅重新生成依赖已变化内容的镜头；未受影响 take 保持可用。",
            )
        )
        steps.append(
            ChangeImpactStep(
                tool=ChangePlanTool.REBUILD_TIMELINE,
                target_ids=sorted(shot_ids),
                reason="替换受影响 take 后重建成片时间线与相关字幕。",
            )
        )
    return steps


def plan_semantic_change(
    document: ProjectDocument,
    takes: list[Take],
    request: SemanticChangePlanRequest,
    client: LanguageProviderClient | None,
) -> SemanticChangePlan:
    index, by_key = _project_index(document, takes)
    warnings: list[str] = []
    if request.explicit_target_ids:
        intent = _fallback_intent(request, index)
    elif client is None:
        intent = _fallback_intent(request, index)
        warnings.append(
            "语义变更 Agent 未配置，且请求未提供显式目标 ID；未根据文本猜测目标。"
        )
    else:
        instructions, input_text = _intent_prompt(document, request, index)
        try:
            output = client.generate_text(
                instructions=instructions,
                input_text=input_text,
                image_urls=[],
                max_output_tokens=5000,
            )
            intent = SemanticChangeIntent.model_validate_json(
                _strip_json_fence(output)
            )
            _validate_intent_targets(intent, by_key)
        except (LanguageProviderError, ValueError) as error:
            intent = _fallback_intent(request, index)
            warnings.append(
                "语义变更 Agent 不可用，未根据指令文本猜测项目对象，也未修改任何资产："
                + str(error)[:800]
            )

    _validate_intent_targets(intent, by_key)
    asset_ids, line_ids, scene_ids, shot_ids, stale_take_ids = _resolve_impact(
        document,
        takes,
        intent,
        by_key,
    )
    if intent.needs_user_clarification:
        warnings.append("当前指令未可靠命中项目对象；用户可补充描述或显式选择目标。")
    return SemanticChangePlan(
        project_id=document.project.id,
        instruction=request.instruction,
        intent=intent,
        affected_asset_ids=sorted(asset_ids),
        affected_script_line_ids=sorted(line_ids),
        affected_scene_ids=sorted(scene_ids),
        affected_shot_ids=sorted(
            shot_ids,
            key=lambda shot_id: next(
                (
                    shot.sequence
                    for shot in document.shots
                    if shot.id == shot_id
                ),
                10_000,
            ),
        ),
        stale_take_ids=sorted(stale_take_ids),
        steps=_impact_steps(
            asset_ids=asset_ids,
            line_ids=line_ids,
            scene_ids=scene_ids,
            shot_ids=shot_ids,
        ),
        warnings=warnings,
    )
