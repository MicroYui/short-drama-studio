"""Read selected media and timestamped observations without inventing continuity."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.models import AudioInspectionReport, ProjectDocument, SemanticReviewStatus, Take, VideoEditorialUse, VideoInspectionReport
from short_drama.production_contracts import StoryboardContent
from short_drama.studio_editorial import EditorClip, EditorProject


def resolve_editorial_uses(
    document: ProjectDocument, *, target_shot_id: str, editorial_project: dict[str, JsonValue],
    takes: list[Take], timeline_id: str | None = None, clip_id: str | None = None,
    require_target: bool = False, include_previous: bool = True,
) -> tuple[VideoEditorialUse | None, VideoEditorialUse | None]:
    """Resolve the actual visible use and predecessor once for observation and H3."""
    editor = EditorProject.model_validate(editorial_project)
    timeline = next((item for item in editor.timelines if item.id == (timeline_id or editor.activeTimelineId)), None)
    if timeline is None:
        raise ValueError(f"Unknown editorial timeline {timeline_id}")
    by_take = {take.id: take for take in takes}
    by_asset = {asset.id: asset for asset in editor.assets}
    solo = any(track.solo for track in timeline.tracks)
    usable = [clip for clip in timeline.clips if clip.type == "video" and clip.opacity > 0
              and clip.trackIndex < len(timeline.tracks) and timeline.tracks[clip.trackIndex].enabled
              and (not solo or timeline.tracks[clip.trackIndex].solo)]

    def use(clip: EditorClip) -> VideoEditorialUse:
        extra = clip.model_extra or {}
        identity = extra.get("sourceTakeId") or clip.assetId
        take = by_take.get(identity) if isinstance(identity, str) else None
        asset = by_asset.get(clip.assetId) if clip.assetId else clip.asset
        shot_id = extra.get("sourceShotId")
        if (take is None or asset is None or not isinstance(shot_id, str)
                or Path(asset.path).expanduser().resolve() != Path(take.media_path).expanduser().resolve()):
            raise ValueError(f"UNRESOLVED_MEDIA_BINDING: clip {clip.id}")
        return VideoEditorialUse(timeline_id=timeline.id, clip_id=clip.id, shot_id=shot_id, take_id=take.id,
            media_path=take.media_path, source_in_seconds=clip.trimStart,
            source_out_seconds=clip.trimStart + clip.duration * clip.speed,
            timeline_start_seconds=clip.startTime, timeline_end_seconds=clip.startTime + clip.duration,
            speed=clip.speed, reversed=clip.reversed)

    targets = [clip for clip in usable if clip.id == clip_id] if clip_id else [
        clip for clip in usable if (clip.model_extra or {}).get("sourceShotId") == target_shot_id]
    if len(targets) > 1:
        raise ValueError("AMBIGUOUS_CURRENT_USES: specify clip_id from " + ", ".join(clip.id for clip in targets))
    if not targets and (require_target or clip_id):
        raise ValueError("NO_CURRENT_USE: specify a visible saved video clip in the selected timeline")
    target = targets[0] if targets else None
    current = use(target) if target is not None else None
    if not include_previous:
        return current, None
    if target is not None:
        # Overlapping layers do not have one unambiguous preceding picture.
        overlaps = [clip for clip in usable if clip.id != target.id
                    and clip.startTime < target.startTime + .001
                    and clip.startTime + clip.duration > target.startTime + .001]
        if overlaps:
            raise ValueError("AMBIGUOUS_PREVIOUS_USES: overlapping clips " + ", ".join(clip.id for clip in overlaps))
        candidates = [clip for clip in usable if clip.id != target.id
                      and clip.startTime + clip.duration <= target.startTime + .001]
        if not candidates:
            return current, None
        end = max(clip.startTime + clip.duration for clip in candidates)
        candidates = [clip for clip in candidates if abs(clip.startTime + clip.duration - end) < .001]
    else:
        shot = next(item for item in document.shots if item.id == target_shot_id)
        predecessor = max((item for item in document.shots if item.sequence < shot.sequence),
                          key=lambda item: item.sequence, default=None)
        if predecessor is None:
            return None, None
        candidates = [clip for clip in usable if (clip.model_extra or {}).get("sourceShotId") == predecessor.id]
    if len(candidates) != 1:
        raise ValueError(("AMBIGUOUS_PREVIOUS_USES" if candidates else "NO_ADOPTED_PREVIOUS_MEDIA")
                         + ": " + ", ".join(clip.id for clip in candidates))
    return current, use(candidates[0])


def previous_adopted_media_boundary(
    document: ProjectDocument,
    *,
    target_shot_id: str,
    editorial_project: dict[str, JsonValue],
    takes: list[Take],
    inspections: list[VideoInspectionReport],
    audio_inspections: list[AudioInspectionReport] | None = None,
) -> dict[str, object] | None:
    """Locate an unambiguous previous current usage, preserving trim/speed/reverse.

    A storyboard predecessor is used only to locate an already selected media
    usage when the next shot has not been placed yet. It never supplies an
    observed state. Layered/duplicate ambiguous uses remain explicit unknowns.
    """

    by_take = {take.id: take for take in takes}
    try:
        current, previous = resolve_editorial_uses(document, target_shot_id=target_shot_id,
            editorial_project=editorial_project, takes=takes)
    except ValueError as exc:
        return {"status": str(exc).split(":", 1)[0], "reason": str(exc),
                "observation_status": "UNKNOWN", "audio_observation_status": "UNKNOWN"}
    if previous is None:
        return None
    take = by_take[previous.take_id]
    source_start, source_end = previous.source_in_seconds, previous.source_out_seconds
    endpoint = previous.playback_end_source_seconds
    boundary: dict[str, object] = {
        **previous.model_dump(mode="json"),
        "relationship": "CURRENT_EDITORIAL_PREDECESSOR" if current else "SELECTED_MEDIA_FOR_PLANNED_PREDECESSOR",
        "status": "ADOPTED_MEDIA",
        "playback_end_source_seconds": endpoint, "observation_status": "UNKNOWN",
        "audio_observation_status": "UNKNOWN", "observations": [], "frame_samples": [],
    }
    if not Path(take.media_path).is_file():
        boundary["status"] = "ADOPTED_MEDIA_FILE_MISSING"
        return boundary
    if audio_inspections:
        from short_drama.observation_sources import boundary_audio_evidence
        boundary.update(boundary_audio_evidence(document, previous, audio_inspections))
    for report in reversed(inspections):
        if report.take_id != take.id or report.semantic_status != SemanticReviewStatus.COMPLETE:
            continue
        observed_end = report.observed_source_out_seconds
        if observed_end is None or report.observed_source_in_seconds > endpoint or observed_end + 0.05 < endpoint:
            continue
        from short_drama.observation_sources import video_report_source_status
        status, _reasons = video_report_source_status(document, take, report, by_take)
        if status != "CURRENT":
            continue
        # A short endpoint interval selects existing evidence; it does not claim
        # that every motion or sound within it has been inspected.
        tolerance = max(0.25, 1 / report.fps)
        frames = [sample for sample in report.samples if sample.source_take_id == take.id
                  and source_start <= sample.timestamp_seconds <= source_end
                  and abs(sample.timestamp_seconds - endpoint) <= tolerance
                  and Path(sample.image_path).is_file()]
        observations = [item for item in report.observations
                        if item.start_seconds is not None and item.end_seconds is not None
                        and item.start_seconds >= max(source_start, endpoint - tolerance)
                        and item.end_seconds <= min(source_end, endpoint + tolerance)
                        and item.end_seconds >= endpoint - tolerance]
        if not frames and not observations:
            continue
        boundary.update({
            "inspection_id": report.id, "review_focus": report.review_focus,
            "observation_status": "TIMESTAMPED_VISUAL_EVIDENCE" if observations else "FRAMES_AVAILABLE_NOT_INTERPRETED",
            "observations": [item.model_dump(mode="json") for item in observations],
            "frame_samples": [sample.model_dump(mode="json") for sample in frames],
            "evidence_scope": "Only listed timestamps and descriptions; no audio or unobserved motion verdict.",
        })
        break
    return boundary


def current_group_context(document: ProjectDocument, shot_id: str, take: Take | None) -> dict[str, JsonValue]:
    """Expose actual source windows plus current neighboring shot facts once."""

    ids = take.covered_shot_ids() if take is not None else [shot_id]
    planned_group: dict[str, JsonValue] | None = None
    if take is None and document.storyboard_document_id:
        board_document = next((item for item in document.production_documents if item.id == document.storyboard_document_id), None)
        if board_document is not None:
            version = next(item for item in board_document.versions if item.id == board_document.current_version_id)
            board = StoryboardContent.model_validate(version.payload)
            group = next((item for item in board.clip_groups if shot_id in item.shot_ids), None)
            if group is not None:
                ids = list(group.shot_ids)
                planned_group = group.model_dump(mode="json")
    return {
        "source": "TAKE_SOURCE_WINDOWS" if take is not None else "CURRENT_STORYBOARD_GROUP" if planned_group else "CURRENT_SHOT",
        "planned_group": planned_group,
        "take_id": take.id if take is not None else None,
        "generation_unit_id": take.generation_unit_id if take is not None else None,
        "clip_group_id": take.clip_group_id if take is not None else None,
        "ordered_shot_ids": cast(JsonValue, ids),
        "shot_windows": [window.model_dump(mode="json") for window in take.shot_windows] if take is not None else [],
        "other_current_shots": [shot.model_dump(mode="json") for shot in document.shots if shot.id in ids and shot.id != shot_id],
        "other_reference_plans": [plan.model_dump(mode="json") for plan in document.reference_plan if plan.shot_id in ids and plan.shot_id != shot_id],
    }
