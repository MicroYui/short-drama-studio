"""Project a saved editorial document into the existing advisory review input."""
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from short_drama.models import (
    ApprovalStatus, Freshness, OutputSpec, ProjectDocument, SubtitleCue,
    TimelineManifest, TimelineVideoClip,
)
from short_drama.studio_editorial import EditorProject


def editorial_episode_input(
    document: ProjectDocument, created_at: str,
) -> tuple[TimelineManifest, int, dict[str, JsonValue]] | None:
    saved = document.editorial_timeline or {}
    value = saved.get("project")
    if not isinstance(value, dict):
        return None
    project = EditorProject.model_validate(value)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    revision = int(cast(int, saved.get("revision", 0)))
    assets = {asset.id: asset for asset in project.assets}
    solo = any(track.solo for track in timeline.tracks)
    clips = [clip for clip in timeline.clips if timeline.tracks[clip.trackIndex].enabled
             and (not solo or timeline.tracks[clip.trackIndex].solo)]
    videos: list[TimelineVideoClip] = []
    for clip in sorted(clips, key=lambda item: (item.startTime, item.trackIndex)):
        # Fully transparent padding contributes time, but has no visible Take.
        if clip.type != "video" or clip.opacity == 0:
            continue
        extra = clip.model_extra or {}
        shot_id, take_id = extra.get("sourceShotId"), extra.get("sourceTakeId")
        asset = assets.get(clip.assetId or "")
        if not isinstance(shot_id, str) or not isinstance(take_id, str) or asset is None:
            raise ValueError(f"Saved video clip {clip.id} has no shot, Take or media binding")
        videos.append(TimelineVideoClip(
            id=clip.id, shot_id=shot_id, take_id=take_id, media_path=asset.path,
            start_seconds=clip.startTime, source_in_seconds=clip.trimStart,
            duration_seconds=clip.duration, transition_in="cut", transition_out="cut",
        ))
    subtitles = [SubtitleCue(id=cue.id, timed_text_id=cue.id, text=cue.text,
        start_seconds=cue.startTime, end_seconds=cue.endTime)
        for cue in timeline.subtitles if timeline.tracks[cue.trackIndex].enabled
        and (not solo or timeline.tracks[cue.trackIndex].solo)]
    manifest = TimelineManifest(
        id=f"editorial-review-{timeline.id}-r{revision}-{document.project.current_script_version_id}",
        project_id=document.project.id, version=revision,
        script_version_id=document.project.current_script_version_id or "",
        bible_version_id=document.project.current_bible_version_id or "",
        video_clips=videos, audio_clips=[], subtitle_cues=subtitles,
        output_spec=OutputSpec(), duration_seconds=max(
            (clip.startTime + clip.duration for clip in clips), default=0),
        approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=created_at,
    )
    return manifest, revision, cast(dict[str, JsonValue], project.model_dump(mode="json"))


def editorial_scene_context(
    value: dict[str, JsonValue], shot_ids: list[str],
) -> dict[str, JsonValue]:
    project = EditorProject.model_validate(value)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    solo = any(track.solo for track in timeline.tracks)
    visible = [clip for clip in timeline.clips if timeline.tracks[clip.trackIndex].enabled
               and (not solo or timeline.tracks[clip.trackIndex].solo)]
    ranges = [(clip.startTime, clip.startTime + clip.duration) for clip in visible
              if clip.type == "video" and (clip.model_extra or {}).get("sourceShotId") in shot_ids]
    def overlaps(start: float, end: float) -> bool:
        return any(start < stop and end > begin for begin, stop in ranges)
    clips = [clip for clip in visible if overlaps(clip.startTime, clip.startTime + clip.duration)]
    asset_ids = {clip.assetId for clip in clips}
    return {"timeline_id": timeline.id,
        "clips": [cast(JsonValue, clip.model_dump(mode="json")) for clip in clips],
        "assets": [cast(JsonValue, asset.model_dump(mode="json"))
                   for asset in project.assets if asset.id in asset_ids],
        "tracks": [cast(JsonValue, track.model_dump(mode="json")) for track in timeline.tracks],
        "subtitles": [cast(JsonValue, cue.model_dump(mode="json")) for cue in timeline.subtitles
                      if overlaps(cue.startTime, cue.endTime)
                      and timeline.tracks[cue.trackIndex].enabled
                      and (not solo or timeline.tracks[cue.trackIndex].solo)]}
