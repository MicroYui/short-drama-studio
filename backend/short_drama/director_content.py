"""Apply director changes to the same current storyboard used by generation."""
from __future__ import annotations

from decimal import Decimal
from typing import cast

from pydantic import JsonValue

from short_drama.dialogue_sequence import ordered_dialogue_line_ids
from short_drama.models import (
    ProductionDocument, ProductionDocumentVersion, ProjectDocument, ShotPlan, ShotReferenceCue,
)
from short_drama.production_contracts import StoryboardContent, StoryboardDraftContent
from short_drama.studio_storyboard import current_draft


def preserve_repair_references(source: ShotPlan, target: ShotPlan, *, explicit: bool) -> None:
    """Keep selected media; retain prose spans only when their label still matches."""
    if explicit:
        return
    target.references = [item.model_copy(deep=True) for item in source.references]
    for cue in target.references:
        if cue.text_start is None or cue.text_end is None:
            continue
        before: object = getattr(source, cue.field or "visual", "")
        after: object = getattr(target, cue.field or "visual", "")
        label = before[cue.text_start:cue.text_end] if isinstance(before, str) else ""
        if isinstance(after, str) and after == before:
            continue
        if label and isinstance(after, str) and after.count(label) == 1:
            cue.text_start = after.index(label)
            cue.text_end = cue.text_start + len(label)
        else:
            cue.text_start = cue.text_end = None
            cue.field = None
    # A rewritten beat layout must not silently lose previously selected media.
    # New explicit beat bindings take precedence; omitted ones remain whole-shot
    # references until rebound. Historical cues can legitimately lack IDs.
    def cue_key(cue: ShotReferenceCue) -> str:
        return f"id:{cue.id}" if cue.id else "value:" + cue.model_dump_json(exclude={"field", "text_start", "text_end"})

    used = {cue_key(item) for item in target.references}
    used.update(cue_key(item) for beat in target.timeline_beats for item in beat.references)
    for beat in source.timeline_beats:
        for original in beat.references:
            key = cue_key(original)
            if key in used:
                continue
            cue = original.model_copy(deep=True)
            cue.text_start = cue.text_end = None
            cue.field = None
            target.references.append(cue)
            used.add(key)

def prepare_repaired_storyboard(
    document: ProjectDocument, source_shot_id: str, replacement_ids: list[str],
) -> tuple[ProductionDocument, ProductionDocumentVersion, dict[str, JsonValue]] | None:
    """Caller holds the project lock and saves the entire mutation once.

    Keep existing execution and reference choices: projecting the storyboard
    through the generic replace path would reset them on a revision change.
    """
    by_id = {shot.id: shot for shot in document.shots}

    def remap(ids: list[str]) -> list[str]:
        return [new_id for old_id in ids
                for new_id in (replacement_ids if old_id == source_shot_id else [old_id])]

    def shots(ids: list[str]) -> list[ShotPlan]:
        return [by_id[item].model_copy(deep=True) for item in ids]

    draft_value = current_draft(document)
    if document.storyboard_document_id:
        source = next(item for item in document.production_documents
                      if item.id == document.storyboard_document_id)
        version = next(item for item in source.versions if item.id == source.current_version_id)
        storyboard = StoryboardContent.model_validate(version.payload)
        storyboard.shots = shots(remap([item.id for item in storyboard.shots]))
        for group in storyboard.clip_groups:
            if source_shot_id in group.shot_ids:
                group.shot_ids = remap(group.shot_ids)
                group.duration_seconds = float(sum(Decimal(str(by_id[item].duration_seconds)) for item in group.shot_ids))
        for scene in storyboard.scene_nodes:
            scene.shot_ids = remap(scene.shot_ids)
        storyboard.computed_duration_seconds = float(sum(Decimal(str(item.duration_seconds)) for item in storyboard.shots))
        storyboard = StoryboardContent.model_validate(storyboard.model_dump())
        payload = cast(dict[str, JsonValue], storyboard.model_dump(mode="json"))
    elif draft_value:
        source, version, draft = draft_value
        for group in draft.committed_groups:
            if source_shot_id not in group.clip_group.shot_ids:
                continue
            group.clip_group.shot_ids = remap(group.clip_group.shot_ids)
            group.shots = shots(group.clip_group.shot_ids)
            group.clip_group.duration_seconds = float(sum(Decimal(str(item.duration_seconds)) for item in group.shots))
            group.source_range.script_line_ids = ordered_dialogue_line_ids(
                group.shots, clip_groups=[group.clip_group])
        for scene in draft.scene_nodes:
            scene.shot_ids = remap(scene.shot_ids)
        draft.verification = None
        draft = StoryboardDraftContent.model_validate(draft.model_dump())
        payload = cast(dict[str, JsonValue], draft.model_dump(mode="json"))
    else:
        return None
    return source, version, payload


def remap_split_editorial(document: ProjectDocument, source: ShotPlan, replacements: list[ShotPlan]) -> None:
    """Keep every adopted frame and sound while giving split shots local views.

    A design change cannot stretch existing media. Divide each actual use by
    the new design proportions, leaving timeline duration and user styling intact.
    """
    from short_drama.studio_editorial import EditorClip, EditorProject
    from short_drama.studio_shot_editorial import editorial_shot_windows

    saved = document.editorial_timeline
    if not saved or not isinstance(saved.get("project"), dict):
        return
    editor = EditorProject.model_validate(saved["project"])
    total = sum(item.duration_seconds for item in replacements)
    changed = False
    for timeline in editor.timelines:
        for window in editorial_shot_windows(timeline, source.id):
            cursor = window.start
            intervals: list[tuple[str, float, float]] = []
            for index, shot in enumerate(replacements):
                end = window.end if index == len(replacements) - 1 else cursor + (window.end - window.start) * shot.duration_seconds / total
                intervals.append((shot.id, cursor, end))
                cursor = end
            video_ids = {item.id for item in window.clips}
            linked_ids = {str(value) for clip in window.clips
                          for value in cast(list[object], (clip.model_extra or {}).get("linkedClipIds", []))}
            old_to_new: dict[str, dict[str, str]] = {}
            revised: list[EditorClip] = []
            for clip in timeline.clips:
                linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))
                belongs = clip.id in video_ids or clip.id in linked_ids or bool(video_ids.intersection(linked))
                if not belongs:
                    revised.append(clip)
                    continue
                mapping: dict[str, str] = {}
                for shot_id, begin, end in intervals:
                    left, right = max(clip.startTime, begin), min(clip.startTime + clip.duration, end)
                    if right <= left:
                        continue
                    part = clip.model_copy(deep=True)
                    part.id = clip.id if not mapping else f"{clip.id}-split-{shot_id}"
                    offset = (clip.startTime + clip.duration - right) if clip.reversed else (left - clip.startTime)
                    part.trimStart = clip.trimStart + offset * clip.speed
                    part.startTime, part.duration = left, right - left
                    if part.asset and part.asset.duration:
                        part.trimEnd = max(0.0, part.asset.duration - part.trimStart - part.duration * part.speed)
                    assert part.model_extra is not None
                    part.model_extra["sourceShotId"] = shot_id
                    # The replacement shot did not exist in the original Take.
                    # Persist its actual source interval so later candidate
                    # adoption can retain local cuts relative to this portion.
                    part.model_extra["sourceWindowStart"] = part.trimStart
                    part.model_extra["sourceWindowEnd"] = part.trimStart + part.duration * part.speed
                    mapping[shot_id] = part.id
                    revised.append(part)
                old_to_new[clip.id] = mapping
            for clip in revised:
                extra = clip.model_extra or {}
                shot_id = str(extra.get("sourceShotId", ""))
                linked = cast(list[str], extra.get("linkedClipIds", []))
                if clip.model_extra is not None and shot_id in {item.id for item in replacements}:
                    clip.model_extra["linkedClipIds"] = [old_to_new.get(item, {}).get(shot_id, item) for item in linked]
            # Every cut belonging to this replacement use shares one source
            # origin. Resetting each cut separately repeats the beginning of a
            # newly adopted candidate and hides gaps in the retained source.
            for shot_id, begin, end in intervals:
                videos = [clip for clip in revised if clip.type == "video"
                          and (clip.model_extra or {}).get("sourceShotId") == shot_id
                          and begin <= clip.startTime < end]
                for video in videos:
                    origin = (video.model_extra or {}).get("sourceTakeId")
                    path = video.asset.path if video.asset else video.assetId
                    related = [clip for clip in videos
                               if (clip.model_extra or {}).get("sourceTakeId") == origin
                               and (clip.asset.path if clip.asset else clip.assetId) == path]
                    source_begin = min(clip.trimStart for clip in related)
                    source_end = max(clip.trimStart + clip.duration * clip.speed for clip in related)
                    linked_ids = cast(list[str], (video.model_extra or {}).get("linkedClipIds", []))
                    for clip in revised:
                        linked_back = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))
                        native = clip.type == "audio" and clip.asset is not None and clip.asset.path == path
                        if clip.id == video.id or native and (clip.id in linked_ids or video.id in linked_back):
                            assert clip.model_extra is not None
                            clip.model_extra["sourceWindowStart"] = source_begin
                            clip.model_extra["sourceWindowEnd"] = source_end
            timeline.clips = revised
            for cue in timeline.subtitles:
                if (cue.model_extra or {}).get("sourceShotId", source.id) != source.id:
                    continue
                if cue.endTime <= window.start or cue.startTime >= window.end:
                    continue
                shot_id = max(intervals, key=lambda entry: max(0.0, min(cue.endTime, entry[2]) - max(cue.startTime, entry[1])))[0]
                assert cue.model_extra is not None
                cue.model_extra["sourceShotId"] = shot_id
            changed = True
    if changed:
        document.editorial_timeline = {**saved, "project": cast(dict[str, JsonValue], editor.model_dump(mode="json")),
            "revision": int(cast(int, saved.get("revision", 0))) + 1}
