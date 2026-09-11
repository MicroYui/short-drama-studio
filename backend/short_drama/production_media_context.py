"""Read selected media and timestamped observations without inventing continuity."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.models import ProjectDocument, SemanticReviewStatus, Take, VideoInspectionReport
from short_drama.production_contracts import StoryboardContent
from short_drama.studio_editorial import EditorClip, EditorProject
from short_drama.video_inspection import inspection_shots, inspection_source_context


def previous_adopted_media_boundary(
    document: ProjectDocument,
    *,
    target_shot_id: str,
    editorial_project: dict[str, JsonValue],
    takes: list[Take],
    inspections: list[VideoInspectionReport],
) -> dict[str, object] | None:
    """Locate an unambiguous previous current usage, preserving trim/speed/reverse.

    A storyboard predecessor is used only to locate an already selected media
    usage when the next shot has not been placed yet. It never supplies an
    observed state. Layered/duplicate ambiguous uses remain explicit unknowns.
    """

    editor = EditorProject.model_validate(editorial_project)
    timeline = next(item for item in editor.timelines if item.id == editor.activeTimelineId)
    by_take = {take.id: take for take in takes}
    by_asset = {asset.id: asset for asset in editor.assets}

    def take_for(clip: EditorClip) -> Take | None:
        identity = (clip.model_extra or {}).get("sourceTakeId") or clip.assetId
        take = by_take.get(identity) if isinstance(identity, str) else None
        asset = by_asset.get(clip.assetId) if clip.assetId else clip.asset
        if take is not None and asset is not None and Path(asset.path).expanduser().resolve() != Path(take.media_path).expanduser().resolve():
            return None
        return take

    def shot_for(clip: EditorClip) -> str | None:
        identity = (clip.model_extra or {}).get("sourceShotId")
        return identity if isinstance(identity, str) else None

    usable = [clip for clip in timeline.clips
              if clip.type == "video" and clip.opacity > 0 and take_for(clip) is not None
              and (clip.trackIndex >= len(timeline.tracks) or timeline.tracks[clip.trackIndex].enabled)]
    targets = [clip for clip in usable if shot_for(clip) == target_shot_id]
    relationship = "CURRENT_EDITORIAL_PREDECESSOR"
    if len(targets) > 1:
        return {"status": "AMBIGUOUS_CURRENT_USES", "target_clip_ids": [clip.id for clip in targets],
                "observation_status": "UNKNOWN", "audio_observation_status": "UNKNOWN"}
    if targets:
        target = targets[0]
        candidates = [clip for clip in usable if clip.id != target.id and clip.trackIndex == target.trackIndex
                      and clip.startTime + clip.duration <= target.startTime + 0.001]
        if not candidates:
            return None
        end = max(clip.startTime + clip.duration for clip in candidates)
        candidates = [clip for clip in candidates if abs(clip.startTime + clip.duration - end) < 0.001]
    else:
        current_shot = next(shot for shot in document.shots if shot.id == target_shot_id)
        predecessor = max((shot for shot in document.shots if shot.sequence < current_shot.sequence),
                          key=lambda shot: shot.sequence, default=None)
        if predecessor is None:
            return None
        candidates = [clip for clip in usable if shot_for(clip) == predecessor.id]
        relationship = "SELECTED_MEDIA_FOR_PLANNED_PREDECESSOR"
    if len(candidates) != 1:
        return {"status": "AMBIGUOUS_PREVIOUS_USES" if candidates else "NO_ADOPTED_PREVIOUS_MEDIA",
                "candidate_clip_ids": [clip.id for clip in candidates], "observation_status": "UNKNOWN",
                "audio_observation_status": "UNKNOWN"}
    clip = candidates[0]
    take = take_for(clip)
    assert take is not None
    source_start = clip.trimStart
    source_end = clip.trimStart + clip.duration * clip.speed
    endpoint = source_start if clip.reversed else source_end
    boundary: dict[str, object] = {
        "relationship": relationship, "status": "ADOPTED_MEDIA",
        "clip_id": clip.id, "shot_id": shot_for(clip), "take_id": take.id,
        "media_path": take.media_path, "source_in_seconds": source_start,
        "source_out_seconds": source_end, "speed": clip.speed, "reversed": clip.reversed,
        "playback_end_source_seconds": endpoint, "observation_status": "UNKNOWN",
        "audio_observation_status": "UNKNOWN", "observations": [], "frame_samples": [],
    }
    if not Path(take.media_path).is_file():
        boundary["status"] = "ADOPTED_MEDIA_FILE_MISSING"
        return boundary
    for report in reversed(inspections):
        if report.take_id != take.id or report.semantic_status != SemanticReviewStatus.COMPLETE:
            continue
        observed_end = report.observed_source_out_seconds
        if observed_end is None or report.observed_source_in_seconds > endpoint or observed_end + 0.05 < endpoint:
            continue
        try:
            report_shots = inspection_shots(document, take, report.observed_source_in_seconds, observed_end)
            previous_take = by_take.get(report.previous_take_id) if report.previous_take_id else None
            current_source = inspection_source_context(document, take, report_shots, previous_take)
        except ValueError:
            # Historical reports may refer to shots removed by a later split.
            # Such evidence is unavailable, not a failure of the next compile.
            continue
        if not report.source_context or report.source_context != current_source:
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
