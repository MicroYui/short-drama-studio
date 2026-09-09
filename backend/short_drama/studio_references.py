"""One binding from visible shot prose to the exact provider media input."""

from __future__ import annotations

from short_drama.models import (
    AssetMediaType, ProjectDocument, ReferenceBinding, ReferencePlanEntry,
    ShotPlan, ShotReferenceCue,
)
from short_drama.h3_models import H3MediaInput


def shot_cues(shot: ShotPlan) -> list[ShotReferenceCue]:
    return [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]


def project_legacy_references(document: ProjectDocument) -> None:
    """Enrich the returned view only; old files/trace do not acquire invented spans."""
    versions = {version.id: asset for asset in document.assets for version in asset.versions}
    for shot in document.shots:
        for index, cue in enumerate(shot_cues(shot)):
            cue.id = cue.id or f"{shot.id}-reference-{index}"
        plan = next((item for item in document.reference_plan if item.shot_id == shot.id), None)
        if plan is None:
            continue
        existing = {cue.asset_version_id for cue in shot_cues(shot)}
        for binding in plan.bindings:
            asset = versions.get(binding.target_id)
            if asset is None or binding.target_id in existing:
                continue
            version = next(item for item in asset.versions if item.id == binding.target_id)
            if version.media_type == AssetMediaType.TEXT_REFERENCE:
                continue
            subject_id = next((pack.subject_asset_id for pack in document.reference_packs
                               if any(entry.asset_version_id == version.id for entry in pack.entries)), asset.id)
            shot.references.append(ShotReferenceCue(
                id=f"{shot.id}-imported-{version.id}", subject_asset_id=subject_id,
                asset_version_id=version.id, alias=f"{asset.name}_{len(shot.references) + 1}",
                purpose=binding.purpose or "整镜参考",
            ))
            existing.add(version.id)


def sync_shot_references(document: ProjectDocument, shot: ShotPlan) -> None:
    """Validate occurrences and make explicit bindings authoritative for generation."""
    assets = {asset.id: asset for asset in document.assets}
    versions = {version.id: (asset, version) for asset in document.assets for version in asset.versions}
    bindings: list[ReferenceBinding] = []
    used_versions: set[str] = set()
    used_ids: set[str] = set()
    aliases: dict[str, tuple[str, str | None]] = {}
    owners = [("shot", shot, shot.references), *(
        (f"beat-{index}", beat, beat.references) for index, beat in enumerate(shot.timeline_beats)
    )]
    for scope, owner, cues in owners:
        spans: dict[str, list[tuple[int, int]]] = {}
        for index, cue in enumerate(cues):
            cue.id = cue.id or f"{shot.id}-{scope}-ref-{index}"
            if cue.id in used_ids:
                raise ValueError("每个文案引用位置必须有独立 id")
            used_ids.add(cue.id)
            subject = assets.get(cue.subject_asset_id)
            if subject is None:
                raise ValueError("引用对象不在本项目资产中")
            if cue.text_start is not None and cue.text_end is not None:
                text: object = getattr(owner, cue.field or "visual", "")
                if not isinstance(text, str) or cue.text_end > len(text):
                    raise ValueError("图片引用的位置已与正文不同，请在当前文案中重新选定位置")
                ranges = spans.setdefault(cue.field or "visual", [])
                if any(cue.text_start < end and cue.text_end > start for start, end in ranges):
                    raise ValueError("同一段文案不能叠加两个图片引用；请分别选定文字")
                ranges.append((cue.text_start, cue.text_end))
            allowed = {version.id for version in subject.versions}
            allowed.update(entry.asset_version_id for pack in document.reference_packs
                           if pack.subject_asset_id == subject.id for entry in pack.entries)
            if cue.asset_version_id is None:
                matching = [entry.asset_version_id for pack in document.reference_packs
                            if pack.subject_asset_id == subject.id for entry in pack.entries
                            if set(cue.required_tags).issubset(entry.tags)]
                candidates = [*matching, subject.approved_version_id, subject.current_version_id]
                cue.asset_version_id = next((version_id for version_id in candidates
                    if version_id in versions and versions[version_id][1].media_type != AssetMediaType.TEXT_REFERENCE), None)
            if cue.asset_version_id is None:
                # A planned object can remain without media; generation will report the missing input.
                continue
            if cue.asset_version_id not in allowed or cue.asset_version_id not in versions:
                raise ValueError("选择的图片不属于该对象或其参考图集")
            target = (subject.id, cue.asset_version_id)
            if cue.alias in aliases and aliases[cue.alias] != target:
                raise ValueError("同一镜头中不同图片需要不同引用名称")
            aliases[cue.alias] = target
            asset, _version = versions[cue.asset_version_id]
            if cue.asset_version_id not in used_versions:
                bindings.append(ReferenceBinding(kind=asset.kind, target_id=cue.asset_version_id, purpose=cue.purpose))
                used_versions.add(cue.asset_version_id)
            else:
                binding = next(item for item in bindings if item.target_id == cue.asset_version_id)
                if cue.purpose not in binding.purpose:
                    binding.purpose = f"{cue.purpose}；{binding.purpose}"[:500]
    if sum(versions[item.target_id][1].media_type == AssetMediaType.IMAGE for item in bindings) > 9:
        raise ValueError("当前 H3 单次最多支持 9 张图片，请减少引用或拆分镜头")
    plan = next((item for item in document.reference_plan if item.shot_id == shot.id), None)
    if plan is None:
        plan = ReferencePlanEntry(shot_id=shot.id, bindings=[], capability_valid=True, validation_notes=[])
        document.reference_plan.append(plan)
    plan.bindings = bindings
    # Exact choices take precedence over a planner picking a different view for the same subject.
    plan.requirements = []
    plan.max_reference_images = max(1, len(bindings))
    plan.planning_notes = "镜头正文引用为当前图片选择；未定位到文字的引用作用于整镜。"


def assert_current_script_structure(document: ProjectDocument, shot: ShotPlan) -> None:
    script = next((item for item in document.script_versions if item.id == document.project.current_script_version_id), None)
    if script and shot.scene_id in script.structure_stale_scene_ids:
        raise ValueError("SCRIPT_STRUCTURE_STALE: 剧本正文已修改，请先按当前正文同步此场次的台词和分镜，再生成视频。")


def assert_generation_references(document: ProjectDocument, shot: ShotPlan, media: list[H3MediaInput]) -> None:
    assert_current_script_structure(document, shot)
    available = {item.asset_version_id for item in media}
    missing = {cue.asset_version_id for cue in shot_cues(shot) if cue.asset_version_id and cue.asset_version_id not in available}
    if missing:
        raise ValueError("SHOT_REFERENCES_STALE: 当前文案绑定的图片没有全部进入生成请求，请按最新镜头引用重新编译。")
