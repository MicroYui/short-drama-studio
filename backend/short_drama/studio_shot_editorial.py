"""A local-time view of one shot use, merged back into the current episode edit."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from short_drama.models import Take
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, EditorSubtitle, EditorTimeline, clip_media_source, project_editorial, read_editorial, validate_editorial
from short_drama.studio_models import StudioEditorial, StudioEditorialScope, StudioMutationResult, StudioShotOccurrence, StudioTimelineSave
from short_drama.studio_shot_uses import shared_group_windows, shot_clip_uses, slice_clip, source_interval_use

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


@dataclass
class ShotWindow:
    start: float
    end: float
    clips: list[EditorClip]
    shared_clip_ids: set[str] = field(default_factory=set[str])


def _timeline(project: EditorProject, timeline_id: str | None = None) -> EditorTimeline:
    target_id = timeline_id or project.activeTimelineId
    timeline = next((item for item in project.timelines if item.id == target_id), None)
    if timeline is None:
        raise ValueError(f"/timeline_id: {target_id!r} is not a current editorial timeline. Re-read the selected timeline.")
    return timeline


def _json(project: EditorProject) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], project.model_dump(mode="json"))


def _windows(timeline: EditorTimeline, shot_id: str, *, assets: list[EditorAsset] | None = None,
             takes: list[Take] | None = None) -> list[ShotWindow]:
    windows: list[ShotWindow] = []
    uses = shot_clip_uses(timeline, assets or [], takes or [], shot_id)
    for use in uses:
        clip = slice_clip(use.clip, use.start, use.end) if use.shared else use.clip
        end = clip.startTime + clip.duration
        if windows and clip.startTime <= windows[-1].end + 0.001:
            windows[-1].end = max(windows[-1].end, end)
            windows[-1].clips.append(clip)
        else:
            windows.append(ShotWindow(clip.startTime, end, [clip]))
        if use.shared:
            windows[-1].shared_clip_ids.add(clip.id)
    return windows


def _select_window(timeline: EditorTimeline, shot_id: str, anchor: str | None, *,
                   assets: list[EditorAsset] | None = None, takes: list[Take] | None = None) -> tuple[ShotWindow | None, list[ShotWindow]]:
    windows = _windows(timeline, shot_id, assets=assets, takes=takes)
    selected = next((window for window in windows if any(clip.id == anchor for clip in window.clips)), None) if anchor else next(iter(windows), None)
    if anchor and selected is None:
        raise ValueError("该镜头使用位置已变化，请重新打开镜头剪辑。")
    return selected, windows


def editorial_shot_windows(timeline: EditorTimeline, shot_id: str, *, assets: list[EditorAsset] | None = None,
                          takes: list[Take] | None = None) -> list[ShotWindow]:
    """Actual contiguous uses of a shot, shared by adoption and structural edits."""
    return _windows(timeline, shot_id, assets=assets, takes=takes)


def _owned(timeline: EditorTimeline, window: ShotWindow, shot_id: str) -> tuple[set[str], set[str]]:
    ids = {clip.id for clip in window.clips}
    for clip in timeline.clips:
        if (clip.model_extra or {}).get("sourceShotId") == shot_id and window.start <= clip.startTime < window.end:
            ids.add(clip.id)
    # Audio linked in either direction belongs to the same edit, including split clips.
    changed = True
    while changed:
        changed = False
        for clip in timeline.clips:
            if window.shared_clip_ids and (clip.startTime >= window.end or clip.startTime + clip.duration <= window.start):
                continue
            linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", [])) or []
            if clip.id in ids or ids.intersection(linked):
                additions = {clip.id, *linked} - ids
                if window.shared_clip_ids:
                    additions.intersection_update(item.id for item in timeline.clips
                        if item.startTime < window.end and item.startTime + item.duration > window.start
                        and (item.type != "video" or item.id in {video.id for video in window.clips}))
                ids.update(additions)
                changed = changed or bool(additions)
    # A subtitle can bridge a cut. Its visible interval, rather than a historical
    # sourceShotId, determines which local views must show the same current cue.
    cue_ids = {cue.id for cue in timeline.subtitles
               if cue.startTime < window.end and cue.endTime > window.start}
    return ids, cue_ids


def _local_clips(timeline: EditorTimeline, window: ShotWindow | None, ids: set[str]) -> list[EditorClip]:
    if window and window.shared_clip_ids:
        return [slice_clip(item, max(item.startTime, window.start), min(item.startTime + item.duration, window.end))
                for item in timeline.clips if item.id in ids
                and item.startTime < window.end and item.startTime + item.duration > window.start]
    return [item.model_copy(deep=True) for item in timeline.clips if item.id in ids]


def _clip_source(clip: EditorClip, assets: dict[str, EditorAsset]) -> tuple[str, float] | None:
    asset = assets.get(clip.assetId or "")
    return clip_media_source(asset, clip) if asset and clip.type in {"video", "audio"} else None


def _source_bounds(clips: list[EditorClip], assets: dict[str, EditorAsset], takes: list[Take], shot_id: str,
                   *, adopted: bool) -> dict[str, list[tuple[float, float]]]:
    """Rebuild allowed intervals from this server-side occurrence, never a request.

    A saved edit can deliberately use observed footage outside the generation
    window. Preserve those islands without opening the unused source between
    them, or borrowing ranges from another occurrence of the same shot.
    """
    sources = {clip.id: source for clip in clips if (source := _clip_source(clip, assets)) is not None}
    by_id = {take.id: take for take in takes}
    bounds: dict[str, list[tuple[float, float]]] = {}
    limits: dict[str, float] = {}
    for clip in clips:
        source = sources.get(clip.id)
        if source is None:
            continue
        path, duration = source
        if duration > 0:
            limits[path] = min(limits.get(path, duration), duration)
        extra = clip.model_extra or {}
        take = by_id.get(str(extra.get("sourceTakeId", "")))
        if take is not None and take.media_path != path:
            take = None
        if take is None and clip.type == "audio":
            linked = cast(list[str], extra.get("linkedClipIds", [])) or []
            video = next((item for item in clips if item.type == "video" and sources.get(item.id, (None,))[0] == path
                          and (item.id in linked or clip.id in (cast(list[str], (item.model_extra or {}).get("linkedClipIds", [])) or []))), None)
            take = by_id.get(str((video.model_extra or {}).get("sourceTakeId", ""))) if video else None
        nominal = next((item for item in take.shot_windows if item.shot_id == shot_id), None) if take else None
        if nominal and take:
            limits[path] = min(limits.get(path, take.duration_seconds), take.duration_seconds)
            begin, end = nominal.source_in_seconds, nominal.source_in_seconds + nominal.duration_seconds
            if not any(item.source_in_seconds + item.duration_seconds > end + 0.001
                       for item in take.shot_windows if item.shot_id != shot_id):
                end = take.duration_seconds
            end = min(end, take.duration_seconds)
        else:
            # Keep a persisted legacy bound when no Take window exists. These
            # values are read from the saved edit, not the submitted local JSON.
            begin_value, end_value = extra.get("sourceWindowStart"), extra.get("sourceWindowEnd")
            if not isinstance(begin_value, (float, int)) or not isinstance(end_value, (float, int)):
                continue
            begin, end = float(begin_value), float(end_value)
        if not isfinite(begin) or not isfinite(end):
            continue
        begin, end = max(0.0, begin), min(end, duration) if duration > 0 else end
        if end > begin:
            bounds.setdefault(path, []).append((begin, end))
    if adopted:
        for clip in clips:
            source = sources.get(clip.id)
            if source is not None and source[0] in bounds:
                end = clip.trimStart + clip.duration * clip.speed
                if source[0] in limits:
                    end = min(end, limits[source[0]])
                if end > clip.trimStart:
                    bounds[source[0]].append((clip.trimStart, end))
    for path, ranges in bounds.items():
        merged: list[tuple[float, float]] = []
        for begin, end in sorted(ranges):
            if merged and begin <= merged[-1][1] + 0.001:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((begin, end))
        bounds[path] = merged
    return bounds


def _within_source(clip: EditorClip, interval: tuple[float, float]) -> bool:
    return clip.trimStart >= interval[0] - 0.001 and clip.trimStart + clip.duration * clip.speed <= interval[1] + 0.04


def read_shot_editorial(service: ShortDramaService, project_id: str, shot_id: str, anchor_clip_id: str | None = None,
                        *, timeline_id: str | None = None) -> StudioEditorial:
    aggregate = service.get_project(project_id)
    from short_drama.generation_storyboard import generation_document
    aggregate = aggregate.model_copy(update={"document": generation_document(aggregate.document)})
    shot = next((item for item in aggregate.document.shots if item.id == shot_id), None)
    if shot is None:
        raise KeyError(shot_id)
    current = read_editorial(service, project_id)
    full = EditorProject.model_validate(current.project)
    timeline = _timeline(full, timeline_id)
    window, windows = _select_window(timeline, shot_id, anchor_clip_id, assets=full.assets, takes=aggregate.takes)
    insertion = window is None
    start = window.start if window else max((item.startTime + item.duration for item in timeline.clips if item.type == "video"), default=0.0)
    if window is None:
        # A new video, or a shot explicitly removed from the film, may be edited before adoption.
        projected = EditorProject.model_validate(project_editorial(aggregate, preview_shot_id=shot_id))
        source = _timeline(projected)
        window, _ = _select_window(source, shot_id, None)
        for following in sorted(aggregate.document.shots, key=lambda item: item.sequence):
            if following.sequence <= shot.sequence:
                continue
            next_window, _ = _select_window(timeline, following.id, None, assets=full.assets, takes=aggregate.takes)
            if next_window:
                start = next_window.start
                break
        if window:
            full.assets = projected.assets
            timeline = source
    local = full.model_copy(deep=True)
    local_timeline = timeline.model_copy(deep=True)
    ids, cue_ids = _owned(timeline, window, shot_id) if window else (set[str](), set[str]())
    origin = window.start if window else 0.0
    local_timeline.clips = _local_clips(timeline, window, ids)
    local_timeline.subtitles = [item.model_copy(deep=True) for item in timeline.subtitles if item.id in cue_ids]
    source_assets = {item.id: item for item in full.assets}
    bounds = _source_bounds(local_timeline.clips, source_assets, aggregate.takes, shot_id,
                            adopted=not insertion and current.revision > 0)
    for clip in local_timeline.clips:
        clip.startTime = max(0.0, clip.startTime - origin)
        if clip.model_extra is not None:
            clip.model_extra["sourceShotId"] = shot_id
            clip.model_extra.pop("sourceShotIds", None)
        source = _clip_source(clip, source_assets)
        ranges = bounds.get(source[0], []) if source else []
        interval = next((item for item in ranges if _within_source(clip, item)), next(iter(ranges), None))
        if interval and clip.model_extra is not None:
            clip.model_extra["sourceWindowStart"], clip.model_extra["sourceWindowEnd"] = interval
    for cue in local_timeline.subtitles:
        cue.startTime = max(0.0, cue.startTime - origin)
        cue.endTime = min(window.end, cue.endTime) - origin if window else cue.endTime - origin
    used_assets = {item.assetId for item in local_timeline.clips}
    local.assets = [item for item in local.assets if item.id in used_assets]
    for asset in local.assets:
        if asset.model_extra is not None:
            asset.model_extra["prompt"] = f"镜头 {shot.sequence:02d} · {'原声' if asset.type == 'audio' else '画面'}"
    assets_by_id = {item.id: item for item in local.assets}
    for clip in local_timeline.clips:
        clip.asset = assets_by_id.get(clip.assetId or "")
    local.timelines = [local_timeline]
    local.activeTimelineId = local_timeline.id
    if local.model_extra is not None:
        local.model_extra["studioShotScope"] = {"shotId": shot_id, "anchorClipId": None if insertion else window.clips[0].id if window else None}
    # Editor markers describe local time, never the full episode's in/out points.
    if local_timeline.model_extra is not None:
        local_timeline.model_extra.pop("markers", None)
    return StudioEditorial(revision=current.revision, project=_json(local),
        default_subtitle_font_family=current.default_subtitle_font_family, scope=StudioEditorialScope(
        shot_id=shot_id, anchor_clip_id=None if insertion else window.clips[0].id if window else None,
        film_start_seconds=start, duration_seconds=0 if insertion else window.end - window.start if window else 0,
        occurrences=[StudioShotOccurrence(anchor_clip_id=item.clips[0].id, start_seconds=item.start, duration_seconds=item.end - item.start) for item in windows]))


def _retime_subtitles(before: EditorTimeline, after: EditorTimeline, duration: float) -> list[EditorSubtitle]:
    """Follow retained source frames when a trim/split changes an unchanged subtitle."""
    originals = {item.id: item for item in before.subtitles}
    videos = [item for item in before.clips if item.type == "video"]
    result: list[EditorSubtitle] = []
    for cue in after.subtitles:
        original = originals.get(cue.id)
        candidates: list[EditorSubtitle] = []
        if original and (cue.startTime, cue.endTime) == (original.startTime, original.endTime):
            for video in videos:
                begin, end = max(original.startTime, video.startTime), min(original.endTime, video.startTime + video.duration)
                if end <= begin:
                    continue
                source_begin = video.trimStart + ((video.startTime + video.duration - end) if video.reversed else (begin - video.startTime)) * video.speed
                source_end = video.trimStart + ((video.startTime + video.duration - begin) if video.reversed else (end - video.startTime)) * video.speed
                for clip in after.clips:
                    if clip.type != "video" or clip.assetId != video.assetId:
                        continue
                    kept_begin, kept_end = max(source_begin, clip.trimStart), min(source_end, clip.trimStart + clip.duration * clip.speed)
                    if kept_end > kept_begin:
                        limit = clip.trimStart + clip.duration * clip.speed
                        candidates.append(cue.model_copy(deep=True, update={"startTime": clip.startTime + ((limit - kept_end) if clip.reversed else (kept_begin - clip.trimStart)) / clip.speed,
                            "endTime": clip.startTime + ((limit - kept_begin) if clip.reversed else (kept_end - clip.trimStart)) / clip.speed}))
        else:
            candidates = [cue.model_copy(deep=True)]
        for index, candidate in enumerate(candidates):
            candidate.startTime = max(0.0, candidate.startTime)
            candidate.endTime = min(duration, candidate.endTime)
            if candidate.endTime <= candidate.startTime:
                continue
            if index:
                candidate.id = f"{cue.id}-part-{index}"
            result.append(candidate)
    return result


def _merge_local_subtitles(
    before: EditorTimeline, after: EditorTimeline, global_timeline: EditorTimeline,
    *, start: float, old_duration: float, duration: float,
    explicit_fields: dict[str, set[str]] | None,
) -> list[EditorSubtitle]:
    """Restore unseen cue edges, while following source edits in this window.

    The server recomputes the baseline from the requested editorial revision;
    neither a browser-only extra field nor a second subtitle record is needed.
    """
    local_originals = {cue.id: cue for cue in before.subtitles}
    global_originals = {cue.id: cue for cue in global_timeline.subtitles}
    reserved_ids = {*global_originals, *(cue.id for cue in after.subtitles)}
    old_end, delta = start + old_duration, duration - old_duration

    def geometry(timeline: EditorTimeline) -> list[tuple[str, str | None, float, float, float, float, bool]]:
        return sorted((clip.id, clip.assetId, clip.startTime, clip.duration, clip.trimStart, clip.speed, clip.reversed)
                      for clip in timeline.clips if clip.type == "video")

    same_frames = geometry(before) == geometry(after)
    result: list[EditorSubtitle] = []
    for cue in after.subtitles:
        local_original, original = local_originals.get(cue.id), global_originals.get(cue.id)
        fields = (explicit_fields or {}).get(cue.id, set[str]())
        start_changed = "start_seconds" in fields or local_original is None or cue.startTime != local_original.startTime
        end_changed = "end_seconds" in fields or local_original is None or cue.endTime != local_original.endTime
        keep_prefix = original is not None and original.startTime < start and not start_changed
        keep_suffix = original is not None and original.endTime > old_end and not end_changed
        single = after.model_copy(update={"subtitles": [cue]})
        # Unchanged geometry also covers an existing multi-clip shot: opening
        # and saving must not split one cue into several identical captions.
        if same_frames:
            candidates = [cue.model_copy(deep=True, update={"startTime": max(0.0, cue.startTime), "endTime": min(duration, cue.endTime)})]
        else:
            baseline = before.model_copy(update={"subtitles": []}) if start_changed or end_changed else before
            candidates = _retime_subtitles(baseline, single, duration)
        if not candidates and (keep_prefix or keep_suffix):
            # The selected frames may all have been removed, while part of this
            # cue still accompanies the material before/after the removed use.
            candidates = [cue.model_copy(deep=True, update={"startTime": 0.0, "endTime": duration})]
        joined: list[EditorSubtitle] = []
        for candidate in sorted(candidates, key=lambda item: item.startTime):
            if joined and candidate.startTime <= joined[-1].endTime + 0.001:
                joined[-1].endTime = max(joined[-1].endTime, candidate.endTime)
            else:
                candidate.id = cue.id
                if joined:
                    part = len(joined)
                    while f"{cue.id}-part-{part}" in reserved_ids:
                        part += 1
                    candidate.id = f"{cue.id}-part-{part}"
                    reserved_ids.add(candidate.id)
                joined.append(candidate)
        candidates = joined
        for index, candidate in enumerate(candidates):
            candidate.startTime += start
            candidate.endTime += start
            if original is not None:
                merged = original.model_copy(deep=True)
                merged.id, merged.text, merged.trackIndex = candidate.id, candidate.text, candidate.trackIndex
                merged.style = {**original.style, **candidate.style}
                merged.startTime, merged.endTime = candidate.startTime, candidate.endTime
                candidate = merged
                if index == 0 and keep_prefix:
                    candidate.startTime = original.startTime
                if index == len(candidates) - 1 and keep_suffix:
                    candidate.endTime = original.endTime + delta
            if candidate.endTime > candidate.startTime:
                result.append(candidate)
    return result


def _retained_shared_clips(timeline: EditorTimeline, window: ShotWindow, owned: set[str],
                           assets: list[EditorAsset], takes: list[Take], source: EditorTimeline,
                           *, projection: EditorTimeline | None = None) -> list[EditorClip]:
    """Split saved containers only on save; keep samples outside the local use.

    The edited part retains its original ID. Remaining pieces get distinct IDs
    and explicit bounds so later opens cannot infer a new whole-group use.
    """
    by_asset, by_take = {item.id: item for item in assets}, {item.id: item for item in takes}
    original = projection or timeline
    uses = [use for clip in original.clips if clip.id in window.shared_clip_ids
            for shot_id, interval in shared_group_windows(clip, original, by_asset, by_take).items()
            if (use := source_interval_use(clip, shot_id, *interval)) is not None]
    boundaries = {window.start, window.end, *(value for use in uses for value in (use.start, use.end))}
    reserved = {item.id for item in timeline.clips} | {item.id for item in source.clips}
    retained: list[EditorClip] = []
    pieces: dict[str, list[tuple[str, float, float]]] = {}
    original_links: dict[str, list[str]] = {}
    for clip in timeline.clips:
        if clip.id not in owned:
            continue
        original_links[clip.id] = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", [])) or []
        points = sorted({clip.startTime, clip.startTime + clip.duration,
                         *(value for value in boundaries if clip.startTime < value < clip.startTime + clip.duration)})
        for begin, end in zip(points, points[1:]):
            if begin >= window.start - .000001 and end <= window.end + .000001:
                continue
            part = slice_clip(clip, begin, end)
            index = 1
            while f"{clip.id}-retained-{index}" in reserved:
                index += 1
            part.id = f"{clip.id}-retained-{index}"
            reserved.add(part.id)
            if part.model_extra is not None:
                matching = [use for use in uses if begin >= use.start - .000001 and end <= use.end + .000001
                            and (use.clip.id == clip.id or use.clip.id in original_links[clip.id]
                                 or clip.id in (cast(list[str], (use.clip.model_extra or {}).get("linkedClipIds", [])) or []))]
                if len({use.shot_id for use in matching}) == 1:
                    part.model_extra["sourceShotId"] = matching[0].shot_id
                if part.type in {"video", "audio"}:
                    part.model_extra.pop("sourceShotIds", None)
                    part.model_extra["sourceWindowStart"] = part.trimStart
                    part.model_extra["sourceWindowEnd"] = part.trimStart + part.duration * part.speed
            retained.append(part)
            pieces.setdefault(clip.id, []).append((part.id, begin, end))
    for clip in source.clips:
        if clip.id in owned:
            pieces.setdefault(clip.id, []).append((clip.id, clip.startTime + window.start,
                                                  clip.startTime + window.start + clip.duration))
    for original_id, parts in pieces.items():
        for part_id, begin, end in parts:
            part = next((item for item in retained if item.id == part_id), None)
            if part is None or part.model_extra is None:
                continue
            links: list[str] = []
            for linked_id in original_links.get(original_id, []):
                if linked_id not in owned:
                    if any(item.id == linked_id and item.startTime < end - .000001
                           and item.startTime + item.duration > begin + .000001 for item in timeline.clips):
                        links.append(linked_id)
                else:
                    links.extend(identity for identity, linked_begin, linked_end in pieces.get(linked_id, [])
                                 if linked_begin < end - .000001 and linked_end > begin + .000001)
            part.model_extra["linkedClipIds"] = list(dict.fromkeys(links))
    # A short linked sound can sit wholly outside the edited interval. Its
    # content stays untouched, but the original video ID now names the edited
    # part, so its backlink must follow the retained picture it still overlaps.
    for clip in timeline.clips:
        if clip.id in owned or clip.model_extra is None:
            continue
        old_links = cast(list[str], clip.model_extra.get("linkedClipIds", [])) or []
        if not owned.intersection(old_links):
            continue
        links: list[str] = []
        for identity in old_links:
            if identity not in owned:
                links.append(identity)
            else:
                links.extend(part_id for part_id, begin, end in pieces.get(identity, [])
                             if begin < clip.startTime + clip.duration - .000001 and end > clip.startTime + .000001)
        clip.model_extra["linkedClipIds"] = list(dict.fromkeys(links))
    for clip in source.clips:
        if clip.model_extra is None:
            continue
        links = cast(list[str], clip.model_extra.get("linkedClipIds", [])) or []
        clip.model_extra["linkedClipIds"] = [identity for identity in links
            if identity in owned or any(other.id == identity and other.startTime < window.start + clip.startTime + clip.duration - .000001
                                        and other.startTime + other.duration > window.start + clip.startTime + .000001
                                        for other in timeline.clips)]
    return retained


def materialize_shared_shot(timeline: EditorTimeline, assets: list[EditorAsset], takes: list[Take],
                            shot_id: str, *, clip_ids: set[str] | None = None) -> bool:
    """Give a write operation ordinary shot clips in its private editor copy.

    Nothing is persisted here. Caller retains its existing save/no-op contract.
    Unedited source samples and subtitles remain in the same film positions.
    """
    changed = False
    projection = timeline.model_copy(deep=True)
    for window in _windows(timeline, shot_id, assets=assets, takes=takes):
        if not window.shared_clip_ids or (clip_ids and not window.shared_clip_ids.intersection(clip_ids)):
            continue
        owned, _ = _owned(timeline, window, shot_id)
        source = timeline.model_copy(deep=True)
        source.clips = _local_clips(timeline, window, owned)
        for clip in source.clips:
            clip.startTime -= window.start
            if clip.model_extra is not None:
                clip.model_extra["sourceShotId"] = shot_id
                clip.model_extra.pop("sourceShotIds", None)
                clip.model_extra["sourceWindowStart"] = clip.trimStart
                clip.model_extra["sourceWindowEnd"] = clip.trimStart + clip.duration * clip.speed
        retained = _retained_shared_clips(timeline, window, owned, assets, takes, source, projection=projection)
        timeline.clips = [clip for clip in timeline.clips if clip.id not in owned] + retained
        for clip in source.clips:
            clip.startTime += window.start
        timeline.clips.extend(source.clips)
        changed = True
    return changed


def save_shot_editorial(service: ShortDramaService, project_id: str, shot_id: str, request: StudioTimelineSave,
                        *, explicit_subtitle_fields: dict[str, set[str]] | None = None,
                        timeline_id: str | None = None) -> StudioMutationResult:
    from short_drama.studio_service import StudioContentConflict
    with service.studio.lock:
        current = read_editorial(service, project_id)
        if current.revision != request.expected_revision:
            raise StudioContentConflict("剪辑已有更新；你的镜头剪辑仍保留，请合并后保存。")
        before = read_shot_editorial(service, project_id, shot_id, request.anchor_clip_id, timeline_id=timeline_id)
        assert before.scope is not None
        raw = request.timeline.get("project")
        if not isinstance(raw, dict):
            raise ValueError("请提交当前镜头的剪辑内容。")
        local = validate_editorial(service, project_id, cast(dict[str, JsonValue], raw))
        if len(local.timelines) != 1:
            raise ValueError("镜头剪辑只能包含一个局部时间线。")
        full = EditorProject.model_validate(current.project)
        target, source = _timeline(full, timeline_id), _timeline(local)
        if timeline_id is not None and source.id != target.id:
            raise ValueError(f"/timeline_id: local timeline {source.id!r} does not match selected timeline {target.id!r}.")
        old_local = _timeline(EditorProject.model_validate(before.project))
        takes = service.repository.list_takes(project_id)
        window, _ = _select_window(target, shot_id, request.anchor_clip_id, assets=full.assets, takes=takes)
        owned, owned_cues = _owned(target, window, shot_id) if window else (set[str](), set[str]())
        untouched = {item.id for item in target.clips if item.id not in owned}
        if untouched.intersection(item.id for item in source.clips):
            raise ValueError("局部剪辑包含其他镜头片段，请重新打开这个镜头。")
        if {item.id for item in target.subtitles if item.id not in owned_cues}.intersection(item.id for item in source.subtitles):
            raise ValueError("局部剪辑包含其他镜头字幕。")
        # Rebuild from the current saved occurrence. Client sourceWindow fields
        # and a different use of this same media cannot expand this set.
        current_clips = _local_clips(target, window, owned) if window else old_local.clips
        current_assets = full.assets if window else EditorProject.model_validate(before.project).assets
        bounds = _source_bounds(current_clips, {item.id: item for item in current_assets},
                                takes, shot_id,
                                adopted=window is not None and current.revision > 0)
        local_assets = {item.id: item for item in local.assets}
        for clip in source.clips:
            media = _clip_source(clip, local_assets)
            if media and media[0] in bounds and not any(_within_source(clip, interval) for interval in bounds[media[0]]):
                ranges = ", ".join(f"[{begin:g}, {end:g})" for begin, end in bounds[media[0]])
                raise ValueError(f"裁切超出了本镜头的素材范围；共享源文件中其他镜头的画面不属于这一镜。"
                                 f"片段 {clip.id} 可用连续源区间为 {ranges} 秒；请重新读取当前镜头剪辑，不能跨越区间间隙。")
        duration = max((item.startTime + item.duration for item in source.clips if item.type != "audio"), default=0.0)
        start = before.scope.film_start_seconds
        old_end = start + before.scope.duration_seconds
        delta = duration - before.scope.duration_seconds
        # A no-op view must not split an otherwise intact shared container.
        if window and window.shared_clip_ids and source == old_local:
            result = service.studio.save_timeline(project_id, StudioTimelineSave(
                expected_revision=request.expected_revision, timeline={"project": _json(full)}))
            result.notices = ["镜头剪辑没有变化；已保留成片中的共享片段。"]
            return result
        retained = _retained_shared_clips(target, window, owned, full.assets, takes, source) if window and window.shared_clip_ids else []
        source.subtitles = _merge_local_subtitles(old_local, source, target, start=start,
            old_duration=before.scope.duration_seconds, duration=duration, explicit_fields=explicit_subtitle_fields)
        track_ids = {item.id: index for index, item in enumerate(target.tracks)}
        for track in source.tracks:
            if track.id not in track_ids:
                track_ids[track.id] = len(target.tracks)
                target.tracks.append(track.model_copy(deep=True))
        target.clips = [item for item in target.clips if item.id not in owned]
        target.clips.extend(retained)
        target.subtitles = [item for item in target.subtitles if item.id not in owned_cues]
        spanning_audio = False
        for clip in target.clips:
            if clip.startTime >= old_end - 0.001:
                clip.startTime = max(0.0, clip.startTime + delta)
            elif clip.type == "audio" and clip.startTime + clip.duration > old_end:
                spanning_audio = True
        for cue in target.subtitles:
            if cue.startTime >= old_end - 0.001:
                cue.startTime = max(0.0, cue.startTime + delta)
                cue.endTime = max(cue.startTime + 0.001, cue.endTime + delta)
        for clip in source.clips:
            if clip.startTime >= duration:
                continue
            clip.duration = min(clip.duration, duration - clip.startTime)
            track = source.tracks[clip.trackIndex]
            clip.muted = clip.muted or track.muted or not track.enabled
            clip.trackIndex = track_ids[track.id]
            clip.startTime += start
            if clip.model_extra is not None:
                clip.model_extra["sourceShotId"] = shot_id
                clip.model_extra.pop("sourceShotIds", None)
                linked = cast(list[str], clip.model_extra.get("linkedClipIds", [])) or []
                allowed_links = {other.id for other in source.clips}
                if window and window.shared_clip_ids:
                    allowed_links.update(other.id for other in target.clips)
                clip.model_extra["linkedClipIds"] = [item for item in linked if item in allowed_links]
            target.clips.append(clip)
        for cue in source.subtitles:
            source_track = source.tracks[cue.trackIndex]
            track_style = (source_track.model_extra or {}).get("subtitleStyle")
            old_track = next((track for track in old_local.tracks if track.id == source_track.id), None)
            previous_style = (old_track.model_extra or {}).get("subtitleStyle") if old_track else None
            if isinstance(track_style, dict) and track_style != previous_style:
                cue.style = {**cast(dict[str, JsonValue], track_style), **cue.style}
            cue.trackIndex = track_ids[source_track.id]
            if cue.model_extra is not None and "sourceShotId" not in cue.model_extra and cue.id not in owned_cues:
                cue.model_extra["sourceShotId"] = shot_id
            target.subtitles.append(cue)
        asset_ids = {item.id for item in full.assets}
        full.assets.extend(item for item in local.assets if item.id not in asset_ids)
        result = service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=request.expected_revision, timeline={"project": _json(full)}))
        result.notices = ["镜头剪辑已保存并同步成片；后续镜头、关联声音与字幕已按长度变化调整。"]
        if spanning_audio and abs(delta) > 0.001:
            result.notices.append("已保留跨镜头声音的原始长度，请在成片中检查配乐节拍与结尾。")
        service.repository.append_studio_event(project_id, "content.updated", {"shot_id": shot_id, "editorial_scope": "shot", "duration_delta_seconds": delta})
        return result
