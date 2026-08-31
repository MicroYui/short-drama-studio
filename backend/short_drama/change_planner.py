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
    AssetKind,
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
        "You are a Semantic Change Planner Agent in an AI-native video-production harness. "
        "Return JSON only and match SemanticChangeIntent exactly. Interpret the user's requested "
        "change and select only the semantic root targets from the supplied project index. Do not "
        "enumerate downstream shots merely because they depend on a character, location, prop, "
        "script line, or asset; the harness computes dependency impact. Use only exact target_id and "
        "kind pairs from the index. Preserve all unspecified identity, performance, story, staging, "
        "audio, and edit decisions. If the request is genuinely ambiguous, set "
        "needs_user_clarification=true instead of inventing a target. This is a user-editable plan, "
        "not a gate and not permission to mutate files. "
        f"Active Production Skill: {profile.label}."
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
    if not selected:
        instruction = request.instruction.casefold()
        preferred_kinds = {
            ChangeTargetKind.CHARACTER.value,
            ChangeTargetKind.LOCATION.value,
            ChangeTargetKind.PROP.value,
            ChangeTargetKind.SCENE.value,
            ChangeTargetKind.SHOT.value,
        }
        selected = [
            item
            for item in index
            if str(item.get("kind")) in preferred_kinds
            and len(str(item.get("name", ""))) >= 2
            and str(item.get("name", "")).casefold() in instruction
        ]
    if not selected and any(token in request.instruction for token in ("全片", "全部镜头")):
        selected = [
            item for item in index if item["kind"] == ChangeTargetKind.TIMELINE.value
        ]
    return SemanticChangeIntent(
        summary=(
            "已根据显式目标或项目名称匹配变更根对象。"
            if selected
            else "无法在未配置语义 Agent 时可靠确定变更目标。"
        ),
        requested_change=request.instruction,
        targets=[
            SemanticChangeTarget(
                kind=ChangeTargetKind(str(item["kind"])),
                target_id=str(item["target_id"]),
                reason="用户显式选择或指令中直接命中该项目对象。",
            )
            for item in selected
        ],
        preserve=["未被指令点名的内容保持不变"],
        needs_user_clarification=not selected,
    )


def _linked_asset_ids(
    document: ProjectDocument,
    target: SemanticChangeTarget,
    metadata: dict[str, object],
) -> set[str]:
    if target.kind == ChangeTargetKind.ASSET:
        return {target.target_id}
    name = str(metadata.get("name", ""))
    result: set[str] = set()
    for asset in document.assets:
        if target.kind == ChangeTargetKind.CHARACTER:
            if asset.kind == AssetKind.CHARACTER and asset.name.removesuffix("定妆") == name:
                result.add(asset.id)
        elif target.kind == ChangeTargetKind.LOCATION:
            if asset.kind == AssetKind.LOCATION and asset.name == name:
                result.add(asset.id)
        elif target.kind == ChangeTargetKind.PROP:
            if asset.kind == AssetKind.PROP and asset.name == name:
                result.add(asset.id)
    return result


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
    character_names: set[str] = set()
    prop_names: set[str] = set()
    location_names: set[str] = set()
    scene_scope_names: set[str] = set()

    for target in intent.targets:
        metadata = by_key[(target.kind, target.target_id)]
        asset_ids.update(_linked_asset_ids(document, target, metadata))
        name = str(metadata.get("name", ""))
        if target.kind == ChangeTargetKind.SCRIPT_LINE:
            line_ids.add(target.target_id)
        elif target.kind == ChangeTargetKind.SCENE:
            scene_ids.add(target.target_id)
            raw_line_ids = metadata.get("line_ids", [])
            if isinstance(raw_line_ids, list):
                line_ids.update(
                    str(value) for value in cast(list[object], raw_line_ids)
                )
        elif target.kind in {
            ChangeTargetKind.CHARACTER,
            ChangeTargetKind.LOCATION,
            ChangeTargetKind.PROP,
        }:
            if target.kind == ChangeTargetKind.CHARACTER:
                character_names.add(name)
                line_ids.update(
                    line.id for line in script.lines if line.speaker == name
                )
            elif target.kind == ChangeTargetKind.LOCATION:
                location_names.add(name)
                scene_scope_names.add(name)
            else:
                prop_names.add(name)
        elif target.kind == ChangeTargetKind.ASSET:
            asset_kind = str(metadata.get("asset_kind", ""))
            if asset_kind == AssetKind.CHARACTER.value:
                character_names.add(name.removesuffix("定妆"))
            elif asset_kind == AssetKind.LOCATION.value:
                location_names.add(name)
                scene_scope_names.add(name)
            elif asset_kind == AssetKind.PROP.value:
                prop_names.add(name)
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
        if scene.id in scene_ids or any(
            name and name in f"{scene.heading}\n{scene.summary}"
            for name in scene_scope_names
        ):
            scene_ids.add(scene.id)
            line_ids.update(scene.line_ids if scene.id in scene_ids else [])

    for shot in document.shots:
        prop_context = "\n".join(
            (
                shot.visual,
                shot.continuity.prop_state_in,
                shot.continuity.prop_state_out,
                shot.continuity.action_in,
                shot.continuity.action_out,
            )
        )
        if (
            shot.scene_id in scene_ids
            or bool(set(shot.dialogue_line_ids) & line_ids)
            or bool(reference_assets_by_shot.get(shot.id, set()) & asset_ids)
            or any(name and name in shot.visual for name in character_names)
            or any(name and name in prop_context for name in prop_names)
            or any(name and name in shot.visual for name in location_names)
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
            "语义变更 Agent 未配置，仅使用项目名称和显式目标匹配；计划仍可由用户修改。"
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
                "语义变更 Agent 不可用，已退回名称匹配且未修改任何资产："
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
