"""Compile the existing linked-clip relationship into one original audio source."""
from __future__ import annotations

from short_drama.studio_editorial import EditorAsset, EditorClip, clip_media_source


def clip_audio_path(clip: EditorClip, assets: dict[str, EditorAsset]) -> str | None:
    asset = assets.get(clip.assetId or "")
    return clip_media_source(asset, clip)[0] if asset else None


def _linked(first: EditorClip, second: EditorClip) -> bool:
    left = (first.model_extra or {}).get("linkedClipIds", [])
    right = (second.model_extra or {}).get("linkedClipIds", [])
    return (isinstance(left, list) and second.id in left) or (isinstance(right, list) and first.id in right)


def clip_audio_delegated(clip: EditorClip, all_clips: list[EditorClip], assets: dict[str, EditorAsset]) -> bool:
    if clip.type != "video":
        return False
    path = clip_audio_path(clip, assets)
    # Match frontend/lib/clip-audio-source.ts. Do not filter the linked clip by
    # audibility: muting/disabling its track must not revive the video's sound.
    return bool(path) and any(candidate.type == "audio" and _linked(clip, candidate)
        and clip_audio_path(candidate, assets) == path for candidate in all_clips)


def clip_audio_origin(clip: EditorClip, all_clips: list[EditorClip], assets: dict[str, EditorAsset]) -> EditorClip:
    if clip.type != "audio":
        return clip
    path = clip_audio_path(clip, assets)
    return next((candidate for candidate in all_clips if candidate.type == "video" and _linked(clip, candidate)
        and clip_audio_path(candidate, assets) == path), clip)
