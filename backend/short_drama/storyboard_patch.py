"""Local, atomic edits of the canonical storyboard, addressed by stable IDs."""

from __future__ import annotations

from decimal import Decimal

from short_drama.models import ShotPlan
from short_drama.production_contracts import (
    ClipGroup,
    StoryboardContent,
    StoryboardPatchRequest,
    populate_storyboard_scene_nodes,
)


def _range(ids: list[str], selected: list[str], after: str | None) -> tuple[int, int]:
    if selected:
        if selected[0] not in ids:
            raise ValueError(f"Storyboard edit references unknown ID: {selected[0]}")
        start = ids.index(selected[0])
        if ids[start:start + len(selected)] != selected:
            raise ValueError("Storyboard edit source IDs must be an unchanged contiguous range in order")
        return start, start + len(selected)
    if after is not None and after not in ids:
        raise ValueError(f"Storyboard insertion references unknown anchor: {after}")
    start = 0 if after is None else ids.index(after) + 1
    return start, start


def _splice_ids(
    ids: list[str], edits: list[tuple[list[str], str | None, list[str]]],
) -> list[str]:
    """All addresses refer to the same original document, never shifted offsets."""
    ranges: list[tuple[int, int, list[str]]] = []
    removed = {item for selected, _, _ in edits for item in selected}
    for selected, after, replacement in edits:
        start, end = _range(ids, selected, after)
        if after is not None and after in removed:
            raise ValueError("An insertion anchor cannot be removed by another edit")
        if any(item in ids and item not in selected for item in replacement):
            raise ValueError("A local edit cannot take an existing ID from outside its source range")
        ranges.append((start, end, replacement))
    ranges.sort(key=lambda item: (item[0], item[1]))
    cursor = 0
    previous_start = -1
    result: list[str] = []
    for start, end, replacement in ranges:
        if start < cursor or start == previous_start:
            raise ValueError("Storyboard edit ranges must not overlap or share an insertion boundary")
        result.extend(ids[cursor:start])
        result.extend(replacement)
        cursor = end
        previous_start = start
    result.extend(ids[cursor:])
    if len(result) != len(set(result)):
        raise ValueError("Storyboard edit output IDs must be unique")
    return result


def _shot_content(shot: ShotPlan) -> dict[str, object]:
    # Sequence is an index derived from the edited list, not a new creative revision.
    return shot.model_dump(mode="json", exclude={"sequence", "revision"})


def validate_storyboard_patch_base(
    base: StoryboardContent, current: StoryboardContent, request: StoryboardPatchRequest,
) -> None:
    """Merge unrelated changes; reject changes to read content or placement anchors."""
    base_shots = {shot.id: shot for shot in base.shots}
    current_shots = {shot.id: shot for shot in current.shots}
    base_ids = list(base_shots)
    current_ids = list(current_shots)
    touched = {item.shot_id for item in request.replacements}
    for edit in request.shot_splices:
        base_start, base_end = _range(base_ids, edit.shot_ids, edit.after_shot_id)
        current_start, current_end = _range(current_ids, edit.shot_ids, edit.after_shot_id)
        touched.update(edit.shot_ids)
        if not edit.shot_ids:
            # The two adjacent IDs identify the exact original insertion gap.
            if (base_ids[max(0, base_start - 1):base_end + 1]
                    != current_ids[max(0, current_start - 1):current_end + 1]):
                raise ValueError("Storyboard insertion boundary changed; read the current adjacent shots")
            if edit.after_shot_id is not None:
                touched.add(edit.after_shot_id)
    for shot_id in touched:
        old, now = base_shots.get(shot_id), current_shots.get(shot_id)
        if old is None or now is None or old.revision != now.revision or _shot_content(old) != _shot_content(now):
            raise ValueError(f"Storyboard shot {shot_id} changed since the read version; read its current revision")
    base_groups = {group.id: group for group in base.clip_groups}
    current_groups = {group.id: group for group in current.clip_groups}
    group_ids = {group.id for group in request.clip_groups or []}
    for edit in request.clip_group_splices:
        _range(list(base_groups), edit.clip_group_ids, None)
        _range(list(current_groups), edit.clip_group_ids, None)
        group_ids.update(edit.clip_group_ids)
    if request.shot_splices:
        group_ids.update(group.id for group in base.clip_groups if touched.intersection(group.shot_ids))
        if any(not edit.shot_ids and edit.after_shot_id is None for edit in request.shot_splices):
            group_ids.add(base.clip_groups[0].id)
    for group_id in group_ids:
        if group_id not in base_groups or base_groups[group_id] != current_groups.get(group_id):
            raise ValueError(f"Storyboard clip group {group_id} changed since the read version")
    for field in ("title", "script_version_id", "bible_version_id", "production_spec_document_id",
                  "production_spec_version_id", "timing_notes", "notes"):
        if getattr(request, field) is not None and getattr(base, field) != getattr(current, field):
            raise ValueError(f"Storyboard {field} changed since the read version")
    base_scenes = {scene.id: scene for scene in base.scene_nodes}
    current_scenes = {scene.id: scene for scene in current.scene_nodes}
    for scene in request.scene_nodes or []:
        if scene.id not in base_scenes or base_scenes[scene.id] != current_scenes.get(scene.id):
            raise ValueError(f"Storyboard scene {scene.id} changed since the read version")


def apply_local_storyboard_patch(current: StoryboardContent, request: StoryboardPatchRequest) -> StoryboardContent:
    prior_shots = {shot.id: shot for shot in current.shots}
    shot_values = dict(prior_shots)
    replaced_ids = {item.shot_id for item in request.replacements}
    splice_source_ids = {shot_id for edit in request.shot_splices for shot_id in edit.shot_ids}
    if replaced_ids.intersection(splice_source_ids):
        raise ValueError("A shot cannot appear in both replacements and shot_splices")
    for item in request.replacements:
        old = prior_shots.get(item.shot_id)
        if old is None:
            raise ValueError(f"Storyboard patch references unknown shots: {item.shot_id}")
        if old.revision != item.expected_revision:
            raise ValueError(f"Storyboard shot {old.id} revision changed from {item.expected_revision} to {old.revision}")
        shot_values[item.shot_id] = item.replacement
    shot_edits = [(edit.shot_ids, edit.after_shot_id, [shot.id for shot in edit.shots]) for edit in request.shot_splices]
    shot_ids = _splice_ids(list(prior_shots), shot_edits)
    for edit in request.shot_splices:
        shot_values.update((shot.id, shot) for shot in edit.shots)
    next_shots: list[ShotPlan] = []
    for sequence, shot_id in enumerate(shot_ids, start=1):
        candidate = shot_values[shot_id]
        old = prior_shots.get(shot_id)
        changed = old is not None and _shot_content(candidate) != _shot_content(old)
        revision = 1 if old is None else old.revision + int(changed)
        next_shots.append(candidate.model_copy(update={
            "revision": revision,
            "sequence": sequence if request.shot_splices else candidate.sequence,
        }, deep=True))

    group_by_shot = {shot_id: group.id for group in current.clip_groups for shot_id in group.shot_ids}
    group_edits: list[tuple[list[str], str | None, list[str]]] = [
        (edit.clip_group_ids, None, [group.id for group in edit.clip_groups]) for edit in request.clip_group_splices
    ]
    group_order = _splice_ids([group.id for group in current.clip_groups], group_edits)
    explicit_group_sources = {group_id for edit in request.clip_group_splices for group_id in edit.clip_group_ids}
    auto_edits: dict[str, list[tuple[list[str], str | None, list[str]]]] = {}
    for selected, after, outputs in shot_edits:
        involved = {group_by_shot[shot_id] for shot_id in selected}
        if not involved:
            involved = {group_by_shot[after] if after is not None else current.clip_groups[0].id}
        if len(involved) > 1:
            if not any(involved.issubset(set(edit.clip_group_ids)) for edit in request.clip_group_splices):
                raise ValueError("A shot splice crossing groups requires clip_group_splices for those affected groups")
        else:
            group_id = next(iter(involved))
            if group_id not in explicit_group_sources:
                auto_edits.setdefault(group_id, []).append((selected, after, outputs))
    group_values: dict[str, ClipGroup] = {}
    for group in current.clip_groups:
        members = _splice_ids(group.shot_ids, auto_edits.get(group.id, []))
        if members:
            group_values[group.id] = group.model_copy(update={"shot_ids": members}, deep=True)
    for edit in request.clip_group_splices:
        group_values.update((group.id, group) for group in edit.clip_groups)
    for group in request.clip_groups or []:
        if group.id not in {item.id for item in current.clip_groups}:
            raise ValueError(f"Storyboard patch references unknown clip groups: {group.id}")
        if group.id in explicit_group_sources:
            raise ValueError("A clip group cannot appear in both clip_groups and clip_group_splices")
        group_values[group.id] = group
    structural = bool(request.shot_splices or request.clip_group_splices)
    next_by_id = {shot.id: shot for shot in next_shots}
    next_groups: list[ClipGroup] = []
    for group_id in group_order:
        group = group_values.get(group_id)
        if group is None:  # Deleting the last shot also removes its now-empty group.
            continue
        if any(shot_id not in next_by_id for shot_id in group.shot_ids):
            raise ValueError(f"Clip group {group.id} references a removed or unknown shot")
        if structural:
            duration = float(sum((Decimal(str(next_by_id[shot_id].duration_seconds)) for shot_id in group.shot_ids), Decimal(0)))
            group = group.model_copy(update={"duration_seconds": duration}, deep=True)
        next_groups.append(group)
    scene_replacements = {scene.id: scene for scene in request.scene_nodes or []}
    if set(scene_replacements) - {scene.id for scene in current.scene_nodes}:
        raise ValueError("Storyboard patch references unknown scenes")
    scenes = [scene_replacements.get(scene.id, scene) for scene in current.scene_nodes]
    payload = current.model_dump(mode="json")
    payload.update(shots=[shot.model_dump(mode="json") for shot in next_shots],
                   clip_groups=[group.model_dump(mode="json") for group in next_groups],
                   computed_duration_seconds=sum(shot.duration_seconds for shot in next_shots),
                   scene_nodes=[scene.model_dump(mode="json") for scene in populate_storyboard_scene_nodes(scenes, next_shots, next_groups)] if scenes else [])
    for field in ("title", "script_version_id", "bible_version_id", "production_spec_document_id",
                  "production_spec_version_id", "timing_notes", "notes"):
        if getattr(request, field) is not None:
            payload[field] = getattr(request, field)
    return StoryboardContent.model_validate(payload)
