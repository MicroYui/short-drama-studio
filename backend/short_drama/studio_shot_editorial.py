"""A local-time view of one shot use, merged back into the current episode edit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from short_drama.studio_editorial import EditorClip, EditorProject, EditorSubtitle, EditorTimeline, project_editorial, read_editorial, validate_editorial
from short_drama.studio_models import StudioEditorial, StudioEditorialScope, StudioMutationResult, StudioShotOccurrence, StudioTimelineSave

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


@dataclass
class ShotWindow:
    start: float
    end: float
    clips: list[EditorClip]


def _timeline(project: EditorProject) -> EditorTimeline:
    return next(item for item in project.timelines if item.id == project.activeTimelineId)


def _json(project: EditorProject) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], project.model_dump(mode="json"))


def _windows(timeline: EditorTimeline, shot_id: str) -> list[ShotWindow]:
    windows: list[ShotWindow] = []
    for clip in sorted((item for item in timeline.clips if item.type == "video" and (item.model_extra or {}).get("sourceShotId") == shot_id), key=lambda item: item.startTime):
        end = clip.startTime + clip.duration
        if windows and clip.startTime <= windows[-1].end + 0.001:
            windows[-1].end = max(windows[-1].end, end)
            windows[-1].clips.append(clip)
        else:
            windows.append(ShotWindow(clip.startTime, end, [clip]))
    return windows


def _select_window(timeline: EditorTimeline, shot_id: str, anchor: str | None) -> tuple[ShotWindow | None, list[ShotWindow]]:
    windows = _windows(timeline, shot_id)
    selected = next((window for window in windows if any(clip.id == anchor for clip in window.clips)), None) if anchor else next(iter(windows), None)
    if anchor and selected is None:
        raise ValueError("该镜头使用位置已变化，请重新打开镜头剪辑。")
    return selected, windows


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
            linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))
            if clip.id in ids or ids.intersection(linked):
                additions = {clip.id, *linked} - ids
                ids.update(additions)
                changed = changed or bool(additions)
    cue_ids = {cue.id for cue in timeline.subtitles if cue.startTime >= window.start - 0.001 and cue.endTime <= window.end + 0.001
        and (cue.model_extra or {}).get("sourceShotId", shot_id) == shot_id}
    return ids, cue_ids


def read_shot_editorial(service: ShortDramaService, project_id: str, shot_id: str, anchor_clip_id: str | None = None) -> StudioEditorial:
    aggregate = service.get_project(project_id)
    shot = next((item for item in aggregate.document.shots if item.id == shot_id), None)
    if shot is None:
        raise KeyError(shot_id)
    current = read_editorial(service, project_id)
    full = EditorProject.model_validate(current.project)
    timeline = _timeline(full)
    window, windows = _select_window(timeline, shot_id, anchor_clip_id)
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
            next_window, _ = _select_window(timeline, following.id, None)
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
    local_timeline.clips = [item.model_copy(deep=True) for item in timeline.clips if item.id in ids]
    local_timeline.subtitles = [item.model_copy(deep=True) for item in timeline.subtitles if item.id in cue_ids]
    take_by_id = {item.id: item for item in aggregate.takes}
    for clip in local_timeline.clips:
        clip.startTime = max(0.0, clip.startTime - origin)
        if clip.model_extra is not None:
            clip.model_extra["sourceShotId"] = shot_id
        take = take_by_id.get(str((clip.model_extra or {}).get("sourceTakeId", "")))
        if take is None and clip.type == "audio":
            linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))
            video = next((item for item in local_timeline.clips if item.id in linked and item.type == "video"), None)
            candidate = take_by_id.get(str((video.model_extra or {}).get("sourceTakeId", ""))) if video else None
            if candidate and clip.asset and clip.asset.path == candidate.media_path:
                take = candidate
        source_window = next((item for item in take.shot_windows if item.shot_id == shot_id), None) if take else None
        if source_window and clip.model_extra is not None:
            source_end = source_window.source_in_seconds + source_window.duration_seconds
            # A provider can return extra footage after the final assigned shot.
            # Keep it available for editing without crossing another shot's range.
            if take and not any(item.source_in_seconds + item.duration_seconds > source_end + 0.001
                                for item in take.shot_windows if item.shot_id != shot_id):
                source_end = take.duration_seconds
            clip.model_extra["sourceWindowStart"] = source_window.source_in_seconds
            clip.model_extra["sourceWindowEnd"] = source_end
    for cue in local_timeline.subtitles:
        cue.startTime = max(0.0, cue.startTime - origin)
        cue.endTime -= origin
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
    return StudioEditorial(revision=current.revision, project=_json(local), scope=StudioEditorialScope(
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
                source_begin = video.trimStart + (begin - video.startTime) * video.speed
                source_end = video.trimStart + (end - video.startTime) * video.speed
                for clip in after.clips:
                    if clip.type != "video" or clip.assetId != video.assetId:
                        continue
                    kept_begin, kept_end = max(source_begin, clip.trimStart), min(source_end, clip.trimStart + clip.duration * clip.speed)
                    if kept_end > kept_begin:
                        candidates.append(cue.model_copy(deep=True, update={"startTime": clip.startTime + (kept_begin - clip.trimStart) / clip.speed,
                            "endTime": clip.startTime + (kept_end - clip.trimStart) / clip.speed}))
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


def save_shot_editorial(service: ShortDramaService, project_id: str, shot_id: str, request: StudioTimelineSave) -> StudioMutationResult:
    from short_drama.studio_service import StudioContentConflict
    with service.studio.lock:
        current = read_editorial(service, project_id)
        if current.revision != request.expected_revision:
            raise StudioContentConflict("剪辑已有更新；你的镜头剪辑仍保留，请合并后保存。")
        before = read_shot_editorial(service, project_id, shot_id, request.anchor_clip_id)
        assert before.scope is not None
        raw = request.timeline.get("project")
        if not isinstance(raw, dict):
            raise ValueError("请提交当前镜头的剪辑内容。")
        local = validate_editorial(service, project_id, cast(dict[str, JsonValue], raw))
        if len(local.timelines) != 1:
            raise ValueError("镜头剪辑只能包含一个局部时间线。")
        full = EditorProject.model_validate(current.project)
        target, source = _timeline(full), _timeline(local)
        old_local = _timeline(EditorProject.model_validate(before.project))
        window, _ = _select_window(target, shot_id, request.anchor_clip_id)
        owned, owned_cues = _owned(target, window, shot_id) if window else (set[str](), set[str]())
        untouched = {item.id for item in target.clips if item.id not in owned}
        if untouched.intersection(item.id for item in source.clips):
            raise ValueError("局部剪辑包含其他镜头片段，请重新打开这个镜头。")
        if {item.id for item in target.subtitles if item.id not in owned_cues}.intersection(item.id for item in source.subtitles):
            raise ValueError("局部剪辑包含其他镜头字幕。")
        bounds: dict[str | None, tuple[float, float]] = {}
        for clip in old_local.clips:
            extra = clip.model_extra or {}
            if "sourceWindowStart" in extra and "sourceWindowEnd" in extra:
                bounds[clip.assetId] = (float(extra["sourceWindowStart"]), float(extra["sourceWindowEnd"]))
        for clip in source.clips:
            if clip.type in {"video", "audio"} and clip.assetId in bounds:
                begin, end = bounds[clip.assetId]
                if clip.trimStart < begin - 0.001 or clip.trimStart + clip.duration * clip.speed > end + 0.04:
                    raise ValueError("裁切超出了本镜头的素材范围；共享源文件中其他镜头的画面不属于这一镜。")
        duration = max((item.startTime + item.duration for item in source.clips if item.type != "audio"), default=0.0)
        start = before.scope.film_start_seconds
        old_end = start + before.scope.duration_seconds
        delta = duration - before.scope.duration_seconds
        source.subtitles = _retime_subtitles(old_local, source, duration)
        track_ids = {item.id: index for index, item in enumerate(target.tracks)}
        for track in source.tracks:
            if track.id not in track_ids:
                track_ids[track.id] = len(target.tracks)
                target.tracks.append(track.model_copy(deep=True))
        target.clips = [item for item in target.clips if item.id not in owned]
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
                linked = cast(list[str], clip.model_extra.get("linkedClipIds", []))
                clip.model_extra["linkedClipIds"] = [item for item in linked if any(other.id == item for other in source.clips)]
            target.clips.append(clip)
        for cue in source.subtitles:
            track_style = (source.tracks[cue.trackIndex].model_extra or {}).get("subtitleStyle")
            if isinstance(track_style, dict):
                cue.style = {**cast(dict[str, JsonValue], track_style), **cue.style}
            cue.trackIndex = track_ids[source.tracks[cue.trackIndex].id]
            cue.startTime += start
            cue.endTime += start
            if cue.model_extra is not None:
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
