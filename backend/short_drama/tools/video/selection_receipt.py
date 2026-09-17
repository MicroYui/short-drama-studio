"""Bounded adoption facts from the returned saved edit, not request intent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue

from short_drama.models import ProjectAggregate
from short_drama.studio_editorial import EditorClip, EditorProject, clip_media_source
from short_drama.studio_models import StudioTakeSelectRequest
from short_drama.tools.contracts import ArtifactRef, ToolWriteReceipt


def take_selection_receipt(
    result: ProjectAggregate, take_id: str, request: StudioTakeSelectRequest,
    refs: Sequence[ArtifactRef],
) -> ToolWriteReceipt:
    take = next(item for item in result.takes if item.id == take_id)
    saved = result.document.editorial_timeline or {}
    state: dict[str, JsonValue] = {
        "project_id": result.document.project.id, "take_id": take.id,
        "take_selected_shot_ids": list(take.selected_shot_ids),
        "editorial_saved": isinstance(saved.get("project"), dict),
        "editorial_revision": saved.get("revision", 0),
    }
    if not isinstance(saved.get("project"), dict):
        # Initial shot adoption can precede a saved timeline. Do not describe
        # its later projection as clips already written to the editorial.
        return ToolWriteReceipt(artifact_refs=list(refs), current_state=state)
    editor = EditorProject.model_validate(saved["project"])
    timeline = next(item for item in editor.timelines if item.id == editor.activeTimelineId)
    assets = {item.id: item for item in editor.assets}
    videos = [clip for clip in timeline.clips if clip.type == "video"
        and (clip.model_extra or {}).get("sourceTakeId") == take.id
        and (not request.clip_ids or clip.id in request.clip_ids)
        and (request.shot_ids is None or (clip.model_extra or {}).get("sourceShotId") in request.shot_ids)]
    video_ids = {clip.id for clip in videos}
    linked_ids = {identity for clip in videos
        for identity in cast(list[str], (clip.model_extra or {}).get("linkedClipIds", []))}

    def clip_state(clip: EditorClip) -> dict[str, JsonValue]:
        asset = assets.get(clip.assetId or "") or clip.asset
        path = clip_media_source(asset, clip)[0] if asset is not None else None
        track = timeline.tracks[clip.trackIndex]
        return {"clip_id": clip.id, "type": clip.type, "asset_id": clip.assetId,
            "source_take_id": (clip.model_extra or {}).get("sourceTakeId"),
            "source_shot_id": (clip.model_extra or {}).get("sourceShotId"), "media_path": path,
            "film_start_seconds": clip.startTime, "film_end_seconds": clip.startTime + clip.duration,
            "duration_seconds": clip.duration, "source_in_seconds": clip.trimStart,
            "source_out_seconds": clip.trimStart + clip.duration * clip.speed,
            "speed": clip.speed, "reversed": clip.reversed, "muted": clip.muted, "volume": clip.volume,
            "audio_fade_in_seconds": clip.audioFadeInSeconds, "audio_fade_out_seconds": clip.audioFadeOutSeconds,
            "track_id": track.id, "track_muted": track.muted, "track_enabled": track.enabled}

    sounds: list[JsonValue] = []
    for clip in timeline.clips:
        if clip.type != "audio" or not (clip.id in linked_ids or video_ids.intersection(
                cast(list[str], (clip.model_extra or {}).get("linkedClipIds", [])))):
            continue
        actual = clip_state(clip)
        if actual["media_path"] == take.media_path:
            sounds.append(actual)
    state.update(timeline_id=timeline.id, adopted_clips=[clip_state(clip) for clip in videos],
        linked_native_audio_clips=sounds)
    if request.audio_policy == "KEEP_CURRENT_AUDIO":
        from short_drama.studio_audio_selection import clip_audio_delegated
        solo = any(track.solo for track in timeline.tracks)
        retained: list[JsonValue] = []
        for clip in timeline.clips:
            track = timeline.tracks[clip.trackIndex]
            if (clip.type not in {"audio", "video"} or clip.muted or clip.volume == 0 or not track.enabled
                    or track.muted or solo and not track.solo or clip_audio_delegated(clip, timeline.clips, assets)):
                continue
            if any(clip.startTime < video.startTime + video.duration and clip.startTime + clip.duration > video.startTime for video in videos):
                retained.append(clip_state(clip))
        state["audio_policy"] = request.audio_policy
        state["current_audio_clips_in_target"] = retained
    # These are actual current uses, not a changed-ID claim: a repeated
    # adoption can legitimately return the same revision without writing.
    return ToolWriteReceipt(artifact_refs=list(refs), current_state=state)
