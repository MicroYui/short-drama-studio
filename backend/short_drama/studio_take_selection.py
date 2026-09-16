"""Adopt generated media into the user's current edit without rebuilding it."""
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from short_drama.models import ProjectDocument, Take
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject
from short_drama.studio_shot_editorial import editorial_shot_windows


def adopt_take_in_editorial(
    document: ProjectDocument, takes: list[Take], take: Take, shot_ids: list[str],
    *, clip_ids: list[str] | None = None,
    source_in_seconds: float | None = None, source_out_seconds: float | None = None,
) -> list[str]:
    explicit_source = source_in_seconds is not None and source_out_seconds is not None
    if explicit_source and source_out_seconds is not None and source_out_seconds > take.duration_seconds + 0.001:
        raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，所选源区间终点 {source_out_seconds:.3f}s 超出了原素材时长。")
    saved = document.editorial_timeline
    if not saved or not isinstance(saved.get("project"), dict):
        if clip_ids:
            raise ValueError("当前没有已保存的采用位置，请读取当前剪辑或省略 clip_ids 后采用镜头。")
        uncovered = [window.shot_id for window in take.shot_windows if window.shot_id in shot_ids
                     and window.source_out_seconds > take.duration_seconds + 0.04]
        if uncovered:
            raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，不能完整覆盖镜头 {', '.join(uncovered)} 的采用区间；先在局部剪辑中裁切可用片段或选择完整覆盖的候选。")
        # Initial adoption is projected from selected Take windows when opened.
        return list(shot_ids)
    editor = EditorProject.model_validate(saved["project"])
    timeline = next(item for item in editor.timelines if item.id == editor.activeTimelineId)
    by_id = {item.id: item for item in takes}
    selected_clips = set(clip_ids or [])
    if selected_clips - {item.id for item in timeline.clips if item.type == "video" and (item.model_extra or {}).get("sourceShotId") in shot_ids}:
        raise ValueError("采用位置不属于这些镜头，请读取当前剪辑并传入对应 clip_ids。")
    assets: dict[str, EditorAsset] = {}
    adopted_shot_ids: list[str] = []
    updated_clip_ids: set[str] = set()

    def asset(media_type: str) -> EditorAsset:
        asset_id = take.id if media_type == "video" else f"{take.id}-native-audio"
        if asset_id not in assets:
            assets[asset_id] = EditorAsset.model_validate({"id": asset_id, "type": media_type,
                "path": take.media_path, "duration": take.duration_seconds,
                "width": take.width, "height": take.height,
                "smallThumbnailPath": take.thumbnail_path, "bigThumbnailPath": take.thumbnail_path,
                "resolution": f"{take.width}×{take.height}", "prompt": "已采用视频" if media_type == "video" else "已采用视频 · 原声"})
        return assets[asset_id]

    for shot_id in shot_ids:
        shot = next(item for item in document.shots if item.id == shot_id)
        windows = editorial_shot_windows(timeline, shot_id)
        if len(windows) > 1 and not selected_clips:
            options = ", ".join(item.clips[0].id for item in windows)
            raise ValueError(f"镜头 {shot_id} 有多处采用，请用 clip_ids 明确这次更换的位置：{options}")
        if selected_clips:
            windows = [item for item in windows if any(clip.id in selected_clips for clip in item.clips)]
            if not windows:
                continue
        adopted_shot_ids.append(shot_id)
        new_window = next((item for item in take.shot_windows if item.shot_id == shot_id), None)
        new_in = new_window.source_in_seconds if new_window else 0.0
        planned_duration = new_window.duration_seconds if new_window else shot.duration_seconds
        new_duration = min(planned_duration, max(0.0, take.duration_seconds - new_in))
        if source_in_seconds is not None and source_out_seconds is not None:
            new_in, new_duration = source_in_seconds, source_out_seconds - source_in_seconds
        if not windows:
            if new_duration <= 0:
                raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，镜头 {shot_id} 从 {new_in:.3f}s 开始，已无可用画面。")
            if selected_clips:
                continue
            following_ids = {item.id for item in document.shots if item.sequence > shot.sequence}
            start = min((item.startTime for item in timeline.clips if item.type == "video" and (item.model_extra or {}).get("sourceShotId") in following_ids),
                        default=max((item.startTime + item.duration for item in timeline.clips if item.type == "video"), default=0.0))
            for item in timeline.clips:
                if item.startTime >= start - 0.001:
                    item.startTime += new_duration
            for cue in timeline.subtitles:
                if cue.startTime >= start - 0.001:
                    cue.startTime += new_duration
                    cue.endTime += new_duration
            media = asset("video")
            timeline.clips.append(EditorClip.model_validate({"id": f"clip-{shot_id}-{take.id}",
                "assetId": media.id, "asset": media.model_dump(), "type": "video", "sourceShotId": shot_id,
                "sourceTakeId": take.id, "startTime": start, "duration": new_duration, "trimStart": new_in,
                "trimEnd": max(0.0, take.duration_seconds - new_in - new_duration), "trackIndex": 0}))
            continue
        for window in windows:
            videos = [clip for clip in window.clips if not selected_clips or clip.id in selected_clips]
            origins = {(str((clip.model_extra or {}).get("sourceTakeId", "")),
                        clip.asset.path if clip.asset else clip.assetId) for clip in videos}
            if len(origins) > 1:
                raise ValueError(f"镜头 {shot_id} 的位置混用了多个源素材，不能自动把裁切映射到候选 {take.id}；请先明确该位置要保留的源区间。")
            for video in videos:
                if explicit_source and abs(new_duration - video.duration * video.speed) > 0.001:
                    raise ValueError(f"所选源区间长 {new_duration:.3f}s，必须等于片段 {video.id} 的采用时长 {video.duration:.3f}s × 速度 {video.speed:g}；本次采用不会改变成片剪点。")
                source_shift = new_in - video.trimStart
                old_take = by_id.get(str((video.model_extra or {}).get("sourceTakeId", "")))
                existing_asset = video.asset or next((item for item in editor.assets if item.id == video.assetId), None)
                same_source = old_take is not None and old_take.id == take.id and existing_asset is not None and existing_asset.path == take.media_path
                old_window = next((item for item in old_take.shot_windows if item.shot_id == shot_id), None) if old_take else None
                old_in = float((video.model_extra or {}).get("sourceWindowStart",
                    old_window.source_in_seconds if old_window else 0.0))
                linked = set(cast(list[str], (video.model_extra or {}).get("linkedClipIds", [])))
                for clip in timeline.clips:
                    if clip.id in updated_clip_ids:
                        continue
                    linked_back = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))
                    clip_asset = clip.asset or next((item for item in editor.assets if item.id == clip.assetId), None)
                    native_audio = clip.type == "audio" and (clip.id in linked or video.id in linked_back) and old_take is not None and clip_asset is not None and clip_asset.path == old_take.media_path
                    if clip.id != video.id and not native_audio:
                        continue
                    if native_audio and selected_clips and (not same_source or (explicit_source and abs(source_shift) > 0.000001)):
                        other_uses = [item.id for item in timeline.clips if item.type == "video" and item.id not in selected_clips
                            and (item.id in linked_back or clip.id in cast(list[str], (item.model_extra or {}).get("linkedClipIds", [])))]
                        if other_uses or clip.startTime < video.startTime - 0.001 or clip.startTime + clip.duration > video.startTime + video.duration + 0.001:
                            raise ValueError(f"关联原声 {clip.id} 同时覆盖未选画面，不能随这一处采用更换；请先明确该原声的局部使用位置。")
                    if same_source and not explicit_source:
                        # Reconfirming this exact source is not a replacement.
                        # Actual edit points can legitimately extend beyond the
                        # generation plan's original per-shot boundaries.
                        if clip.trimStart < -0.01 or clip.trimStart + clip.duration * clip.speed > take.duration_seconds + 0.04:
                            raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，无法覆盖片段 {clip.id} 已有源区间；先调整该片段或选择完整覆盖的候选。")
                        updated_clip_ids.add(clip.id)
                        continue
                    if new_duration <= 0:
                        raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，镜头 {shot_id} 从 {new_in:.3f}s 开始，已无可用画面。")
                    if explicit_source:
                        # An observed replacement maps from the actual old edit,
                        # never from potentially stale generation-plan metadata.
                        selected_in = clip.trimStart + source_shift
                        if selected_in < -0.001 or selected_in + clip.duration * clip.speed > take.duration_seconds + 0.001:
                            raise ValueError(f"候选 {take.id} 实际媒体仅 {take.duration_seconds:.3f}s，无法覆盖片段 {clip.id} 对应的所选源区间。")
                        if same_source and abs(selected_in - clip.trimStart) < 0.000001:
                            updated_clip_ids.add(clip.id)
                            continue
                    else:
                        relative_in = clip.trimStart - old_in
                        if relative_in < -0.01 or relative_in + clip.duration * clip.speed > new_duration + 0.04:
                            raise ValueError(f"候选 {take.id} 在镜头 {shot_id} 的可用区间 {new_in:.3f}–{new_in + new_duration:.3f}s 无法覆盖片段 {clip.id} 已有裁切；先调整该片段或选更长候选。")
                        selected_in = new_in + max(0.0, relative_in)
                    media = asset(clip.type)
                    clip.assetId, clip.asset = media.id, media
                    clip.trimStart = max(0.0, selected_in)
                    clip.trimEnd = max(0.0, take.duration_seconds - clip.trimStart - clip.duration * clip.speed)
                    assert clip.model_extra is not None
                    clip.model_extra["sourceTakeId"] = take.id
                    clip.model_extra["sourceWindowStart"] = new_in
                    clip.model_extra["sourceWindowEnd"] = new_in + new_duration
                    updated_clip_ids.add(clip.id)
    for media in assets.values():
        editor.assets = [item for item in editor.assets if item.id != media.id]
        editor.assets.append(media)
    if assets:
        document.editorial_timeline = {**saved, "project": cast(dict[str, JsonValue], editor.model_dump(mode="json")),
            "revision": int(cast(int, saved.get("revision", 0))) + 1}
    return adopted_shot_ids
