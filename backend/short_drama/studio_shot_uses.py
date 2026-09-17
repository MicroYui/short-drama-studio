"""Conservative source-window views of an explicitly retained whole H3 group.

These are metadata coordinates, not detected picture cuts. Ambiguous legacy
edits retain their explicit shot ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import cast

from pydantic import JsonValue

from short_drama.models import GenerationUnitShotWindow, Take
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, EditorTimeline, clip_media_source


@dataclass(frozen=True)
class ShotClipUse:
    shot_id: str
    clip: EditorClip
    start: float
    end: float
    shared: bool = False


def _explicit_interval(clip: EditorClip) -> bool:
    extra = clip.model_extra or {}
    begin, end = extra.get("sourceWindowStart"), extra.get("sourceWindowEnd")
    return (isinstance(begin, (int, float)) and not isinstance(begin, bool)
            and isinstance(end, (int, float)) and not isinstance(end, bool)
            and isfinite(begin) and isfinite(end) and begin >= 0 and end > begin)


def _group_source(clip: EditorClip, assets: dict[str, EditorAsset],
                  takes: dict[str, Take]) -> tuple[Take, list[GenerationUnitShotWindow]]:
    extra = clip.model_extra or {}
    identity = extra.get("sourceTakeId")
    take = takes.get(identity) if isinstance(identity, str) else None
    asset = assets.get(clip.assetId or "")
    if clip.type != "video" or take is None or asset is None or len(take.shot_windows) < 2:
        raise ValueError("必须绑定当前项目中有多个镜头源窗口的视频 Take。")
    path, _ = clip_media_source(asset, clip)
    if path != take.media_path:
        raise ValueError("实际素材路径与 sourceTakeId 的媒体不一致。")
    windows = sorted(take.shot_windows, key=lambda item: item.source_in_seconds)
    if len({item.shot_id for item in windows}) != len(windows):
        raise ValueError("Take 的镜头源窗口标识重复。")
    source_end = clip.trimStart + clip.duration * clip.speed
    if (not all(isfinite(value) for value in (clip.trimStart, source_end, take.duration_seconds))
            or source_end > take.duration_seconds + .04):
        raise ValueError("片段源区间超出 Take 的实际时长。")
    previous_end = 0.0
    for window in windows:
        begin, end = window.source_in_seconds, window.source_out_seconds
        if (not all(isfinite(value) for value in (begin, end, window.duration_seconds))
                or begin < 0 or end <= begin or abs(end - begin - window.duration_seconds) > .001
                or begin < previous_end - .001 or end > take.duration_seconds + .001):
            raise ValueError("Take 的镜头源窗口无效或重叠。")
        previous_end = end
    return take, windows


def declared_group_windows(clip: EditorClip, assets: dict[str, EditorAsset],
                           takes: dict[str, Take]) -> dict[str, tuple[float, float]]:
    """Validate an explicit multi-shot relation; trimming only changes its use.

    A materialized single-shot source interval has precedence. The saved Take
    windows describe source coordinates, never detected visual boundaries.
    """
    extra = clip.model_extra or {}
    value = extra.get("sourceShotIds")
    if value is None or any(extra.get(key) is not None for key in ("sourceWindowStart", "sourceWindowEnd")):
        return {}
    if not isinstance(value, list):
        raise ValueError("sourceShotIds 必须是至少两个非空镜头 ID。")
    values = cast(list[object], value)
    if len(values) < 2 or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError("sourceShotIds 必须是至少两个非空镜头 ID。")
    ids = cast(list[str], values)
    if len(set(ids)) != len(ids) or extra.get("sourceShotId") not in ids:
        raise ValueError("sourceShotIds 不能重复，且必须包含 sourceShotId。")
    take, windows = _group_source(clip, assets, takes)
    if not set(ids).issubset(item.shot_id for item in windows):
        raise ValueError("sourceShotIds 包含该 Take 源窗口中不存在的镜头。")
    result = {window.shot_id: (window.source_in_seconds,
              take.duration_seconds if index == len(windows) - 1 else window.source_out_seconds)
              for index, window in enumerate(windows) if window.shot_id in ids}
    source_end = clip.trimStart + clip.duration * clip.speed
    if not any(min(end, source_end) > max(begin, clip.trimStart) for begin, end in result.values()):
        raise ValueError("片段源区间与 sourceShotIds 声明的镜头没有交集。")
    return result


def declared_relation_is_saved(clip: EditorClip, windows: dict[str, tuple[float, float]],
                               saved: EditorProject, takes: dict[str, Take]) -> bool:
    """Recognize an existing relation independently of an occurrence's clip ID.

    Replacing another occurrence can change Take.selected_shot_ids. Ordinary
    trim, split and copy still use the exact saved Take/path/window relation.
    """
    source_take = (clip.model_extra or {}).get("sourceTakeId")
    assets = {item.id: item for item in saved.assets}
    for timeline in saved.timelines:
        for previous in timeline.clips:
            if (previous.model_extra or {}).get("sourceTakeId") != source_take:
                continue
            try:
                prior_windows = declared_group_windows(previous, assets, takes)
            except ValueError:
                continue
            if windows and all(prior_windows.get(sid) == interval for sid, interval in windows.items()):
                return True
    return False


def shared_group_windows(clip: EditorClip, timeline: EditorTimeline,
                         assets: dict[str, EditorAsset], takes: dict[str, Take]) -> dict[str, tuple[float, float]]:
    """Use declared groups; only infer intact selected groups for legacy clips."""
    extra = clip.model_extra or {}
    if clip.type != "video" or any(extra.get(key) is not None for key in ("sourceWindowStart", "sourceWindowEnd")):
        return {}
    try:
        if extra.get("sourceShotIds") is not None:
            return declared_group_windows(clip, assets, takes)
        take, windows = _group_source(clip, assets, takes)
    except ValueError:
        # Keep old saved content readable. New explicit declarations are
        # validated before saving; a broken binding must not invent uses.
        return {}
    ids = [item.shot_id for item in windows]
    if extra.get("sourceShotId") != ids[0] or not set(ids).issubset(take.selected_shot_ids):
        return {}
    if any(other.type == "video" and (other.model_extra or {}).get("sourceShotId") in ids[1:]
           and not _explicit_interval(other) for other in timeline.clips):
        return {}
    source_end = clip.trimStart + clip.duration * clip.speed
    if any(clip.trimStart > window.source_in_seconds + .001 or source_end < window.source_out_seconds - .001
           for window in windows):
        return {}
    return {window.shot_id: (window.source_in_seconds,
            take.duration_seconds if index == len(windows) - 1 else window.source_out_seconds)
            for index, window in enumerate(windows)}


def source_interval_use(clip: EditorClip, shot_id: str, begin: float, end: float) -> ShotClipUse | None:
    source_end = clip.trimStart + clip.duration * clip.speed
    begin, end = max(begin, clip.trimStart), min(end, source_end)
    if end <= begin:
        return None
    if clip.reversed:
        start = clip.startTime + (source_end - end) / clip.speed
        finish = clip.startTime + (source_end - begin) / clip.speed
    else:
        start = clip.startTime + (begin - clip.trimStart) / clip.speed
        finish = clip.startTime + (end - clip.trimStart) / clip.speed
    return ShotClipUse(shot_id, clip, start, finish, shared=True)


def shot_clip_uses(timeline: EditorTimeline, assets: list[EditorAsset], takes: list[Take],
                   shot_id: str) -> list[ShotClipUse]:
    by_asset, by_take = {item.id: item for item in assets}, {item.id: item for item in takes}
    result: list[ShotClipUse] = []
    for clip in timeline.clips:
        if clip.type != "video":
            continue
        windows = shared_group_windows(clip, timeline, by_asset, by_take)
        if windows:
            interval = windows.get(shot_id)
            use = source_interval_use(clip, shot_id, *interval) if interval else None
            if use:
                result.append(use)
        elif (clip.model_extra or {}).get("sourceShotId") == shot_id:
            result.append(ShotClipUse(shot_id, clip, clip.startTime, clip.startTime + clip.duration))
    return sorted(result, key=lambda item: item.start)


def slice_clip(clip: EditorClip, begin: float, end: float) -> EditorClip:
    """Retain source samples and the original time-based sound envelope."""
    result = clip.model_copy(deep=True)
    offset = begin - clip.startTime
    result.startTime, result.duration = begin, end - begin
    for edge in ("transitionIn", "transitionOut"):
        transition = (clip.model_extra or {}).get(edge)
        if not isinstance(transition, dict):
            continue
        value = cast(dict[str, JsonValue], transition)
        if value.get("type", "none") == "none":
            continue
        duration = value.get("duration")
        if not isinstance(duration, (int, float)):
            continue
        inside = (clip.startTime < begin < clip.startTime + duration - .000001
                  or clip.startTime < end < clip.startTime + duration - .000001) if edge == "transitionIn" else (
            clip.startTime + clip.duration - duration + .000001 < begin < clip.startTime + clip.duration
            or clip.startTime + clip.duration - duration + .000001 < end < clip.startTime + clip.duration)
        if inside:
            raise ValueError(f"片段 {clip.id} 的镜头来源窗口切点落在 {edge} 转场内部；请在成片中先调整转场或明确拆分，不能在局部剪辑中重复应用转场。")
        internal = begin > clip.startTime + .000001 if edge == "transitionIn" else end < clip.startTime + clip.duration - .000001
        if internal and result.model_extra is not None:
            result.model_extra.pop(edge, None)
    if clip.type in {"video", "audio"}:
        result.trimStart = clip.trimStart + (clip.startTime + clip.duration - end if clip.reversed else offset) * clip.speed
        result.trimEnd = clip.trimEnd + (offset if clip.reversed else clip.startTime + clip.duration - end) * clip.speed
        if abs(offset) > .000001 or abs(result.duration - clip.duration) > .000001:
            result.audioEnvelopeOffsetSeconds = (clip.audioEnvelopeOffsetSeconds or 0) + offset
            result.audioEnvelopeDurationSeconds = clip.audioEnvelopeDurationSeconds or clip.duration
    return result
