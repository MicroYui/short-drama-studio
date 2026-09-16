"""Exact current editor objects, with film times and actual shot-use mappings."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from short_drama.studio_editorial import EditorProject, read_editorial, require_unambiguous_editorial_ids
from short_drama.studio_shot_editorial import editorial_shot_windows
from short_drama.studio_visual_edits import clip_visual_fields

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def read_editorial_selection(
    service: ShortDramaService, project_id: str, *, timeline_id: str | None,
    subtitle_ids: list[str], clip_ids: list[str], shot_id: str | None = None,
    anchor_clip_id: str | None = None,
) -> dict[str, JsonValue]:
    """Read canonical rows, never a clipped local copy of a crossing subtitle."""
    current = read_editorial(service, project_id)
    editor = EditorProject.model_validate(current.project)
    if timeline_id is None:
        require_unambiguous_editorial_ids(editor, clip_ids=clip_ids, subtitle_ids=subtitle_ids)
    target_id = timeline_id or editor.activeTimelineId
    timeline = next((item for item in editor.timelines if item.id == target_id), None)
    if timeline is None:
        raise ValueError(f"/timeline_id: {target_id!r} is not a current editorial timeline. Re-select the timeline.")
    cues = {item.id: item for item in timeline.subtitles}
    clips = {item.id: item for item in timeline.clips}
    missing = [f"/{field}/{index}: {identity!r}" for field, selected, available in (
        ("subtitle_ids", subtitle_ids, cues), ("clip_ids", clip_ids, clips),
    ) for index, identity in enumerate(selected) if identity not in available]
    if missing:
        raise ValueError(f"Unknown current objects in timeline {target_id!r}: " + "; ".join(missing)
                         + ". Use current editor selection IDs; no other timeline or first shot use was substituted.")
    selected_cues = [cues[identity] for identity in dict.fromkeys(subtitle_ids)]
    selected_clips = [clips[identity] for identity in dict.fromkeys(clip_ids)]
    shot_ids = list(dict.fromkeys(identity for clip in timeline.clips
        if clip.type == "video" and isinstance(identity := (clip.model_extra or {}).get("sourceShotId"), str)))
    windows = [(identity, window) for identity in shot_ids
               for window in editorial_shot_windows(timeline, identity)]
    if shot_id is not None:
        windows = [(identity, window) for identity, window in windows if identity == shot_id]
        if not windows:
            raise ValueError(f"/shot_id: {shot_id!r} has no current film use in timeline {target_id!r}. Read the selected objects without shot_id for film coordinates.")
    if anchor_clip_id is not None:
        windows = [(identity, window) for identity, window in windows
                   if any(clip.id == anchor_clip_id for clip in window.clips)]
        if not windows:
            raise ValueError(f"/anchor_clip_id: {anchor_clip_id!r} is not a current use of shot {shot_id!r} in timeline {target_id!r}. Re-select the use position.")
    mappings: list[JsonValue] = []
    mapped: set[tuple[str, str]] = set()
    for identity, window in windows:
        local_cues: list[JsonValue] = []
        local_clips: list[JsonValue] = []
        for field, rows, target in (("subtitle_ids", [(cue.id, cue.startTime, cue.endTime) for cue in selected_cues], local_cues),
                                    ("clip_ids", [(clip.id, clip.startTime, clip.startTime + clip.duration) for clip in selected_clips], local_clips)):
            for object_id, start, end in rows:
                begin, finish = max(start, window.start), min(end, window.end)
                if finish > begin:
                    target.append({"id": object_id, "start_seconds": round(begin - window.start, 9),
                                   "end_seconds": round(finish - window.start, 9)})
                    mapped.add((field, object_id))
        if local_cues or local_clips:
            mappings.append({"shot_id": identity, "anchor_clip_id": window.clips[0].id,
                "film_start_seconds": window.start, "film_end_seconds": window.end,
                "subtitles": local_cues, "clips": local_clips})
    if shot_id is not None or anchor_clip_id is not None:
        outside = [f"{field}={identity!r}" for field, selected in (("subtitle_ids", subtitle_ids), ("clip_ids", clip_ids))
                   for identity in selected if (field, identity) not in mapped]
        if outside:
            raise ValueError(f"Selected IDs {outside!r} do not intersect shot_id={shot_id!r}, anchor_clip_id={anchor_clip_id!r} in timeline {target_id!r}. Correct that scope or omit shot_id/anchor_clip_id to read exact film objects.")
    content: dict[str, JsonValue] = {
        "project_id": project_id, "editorial_revision": current.revision,
        "timeline_id": target_id, "coordinate_space": "FILM", "selection_only": True,
        "active_timeline_id": editor.activeTimelineId,
        "is_saved": current.revision > 0,
        "subtitles": [{"id": cue.id, "text": cue.text, "start_seconds": cue.startTime,
                       "end_seconds": cue.endTime, "track_index": cue.trackIndex,
                       "style": cue.style} for cue in selected_cues],
        "clips": [{"id": clip.id, "asset_id": clip.assetId, "type": clip.type,
            "start_seconds": clip.startTime, "end_seconds": clip.startTime + clip.duration,
            "source_in_seconds": clip.trimStart, "speed": clip.speed, "reversed": clip.reversed,
            "volume": clip.volume, "muted": clip.muted, "track_index": clip.trackIndex,
            "source_shot_id": cast(JsonValue, (clip.model_extra or {}).get("sourceShotId")),
            "source_take_id": cast(JsonValue, (clip.model_extra or {}).get("sourceTakeId")),
            "source_window_start_seconds": cast(JsonValue, (clip.model_extra or {}).get("sourceWindowStart")),
            "source_window_end_seconds": cast(JsonValue, (clip.model_extra or {}).get("sourceWindowEnd")),
            **clip_visual_fields(clip)}
            for clip in selected_clips],
        "shot_local_mappings": mappings,
        "mapping_coordinates": "SHOT_LOCAL intersections; add film_start_seconds. Top-level rows retain full FILM boundaries.",
    }
    content["edit_arguments"] = {"timeline_id": target_id, "expected_revision": current.revision}
    return content
