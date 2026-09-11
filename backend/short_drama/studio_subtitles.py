"""Align selected current captions, merging only their observed time windows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from short_drama.audio_inspection import AudioTimingService
from short_drama.models import ProductionDocumentArtifactRef, ProductionDocumentMutationResponse, ProjectDocument
from short_drama.studio_audio import editorial_audio_input
from short_drama.studio_editorial import EditorClip, EditorProject, EditorSubtitle, EditorTimeline
from short_drama.subtitle_alignment import NativeSubtitleAlignmentRequest, NativeSubtitleAlignmentResponse, align_native_subtitles

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


@dataclass(frozen=True)
class NativeSource:
    clip: EditorClip
    take_id: str
    shot_id: str
    path: str


def _saved_project(document: ProjectDocument) -> tuple[EditorProject, EditorTimeline, int]:
    saved = document.editorial_timeline or {}
    project = EditorProject.model_validate(saved.get("project"))
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    return project, timeline, int(cast(int, saved.get("revision", 0)))


def _native_sources(service: ShortDramaService, document: ProjectDocument, project: EditorProject,
                    timeline: EditorTimeline) -> list[NativeSource]:
    assets = {asset.id: asset for asset in project.assets}
    clips = {clip.id: clip for clip in timeline.clips}
    takes = {take.id: take for take in service.repository.list_takes(document.project.id)}
    solo = any(track.solo for track in timeline.tracks)
    sources: list[NativeSource] = []
    for clip in timeline.clips:
        track = timeline.tracks[clip.trackIndex]
        if clip.type not in {"video", "audio"} or not track.enabled or (solo and not track.solo) or track.muted or clip.muted or clip.volume == 0:
            continue
        asset = assets.get(clip.assetId or "")
        if asset is None:
            continue
        extra = clip.model_extra or {}
        links = cast(list[str], extra.get("linkedClipIds", []))
        video = clip if clip.type == "video" else next((clips[item] for item in links if item in clips and clips[item].type == "video"), None)
        origin = (video.model_extra or {}) if video is not None else extra
        take_id, shot_id = str(origin.get("sourceTakeId") or ""), str(origin.get("sourceShotId") or "")
        take = takes.get(take_id)
        if take is not None and take.media_path == asset.path and shot_id:
            sources.append(NativeSource(clip, take_id, shot_id, asset.path))
    return sources


def _overlaps(start: float, end: float, clip: EditorClip) -> bool:
    return start < clip.startTime + clip.duration - 0.001 and end > clip.startTime + 0.001


def _adjacent(left: NativeSource, right: NativeSource) -> bool:
    a, b = left.clip, right.clip
    return (left.take_id == right.take_id and left.path == right.path
        and a.type == b.type and a.trackIndex == b.trackIndex
        and a.speed == b.speed and a.volume == b.volume and not a.reversed and not b.reversed
        and abs(a.startTime + a.duration - b.startTime) < 0.001
        and abs(a.trimStart + a.duration * a.speed - b.trimStart) < 0.001)


def _relevant_sources(sources: list[NativeSource], cues: list[EditorSubtitle]) -> list[NativeSource]:
    selected = {source.clip.id for source in sources if any(_overlaps(cue.startTime, cue.endTime, source.clip) for cue in cues)}
    ordered = sorted(sources, key=lambda item: item.clip.startTime)
    # Keep contiguous source audio when the actual line crosses a visual cut.
    changed = True
    while changed:
        changed = False
        for first, second in zip(ordered, ordered[1:]):
            if _adjacent(first, second) and bool({first.clip.id, second.clip.id} & selected):
                before = len(selected)
                selected.update((first.clip.id, second.clip.id))
                changed = changed or len(selected) != before
    return [source for source in ordered if source.clip.id in selected]


def _source_signature(sources: list[NativeSource]) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for source in sources:
        stat = Path(source.path).stat()
        clip = source.clip
        result.append({"clip_id": clip.id, "take_id": source.take_id, "shot_id": source.shot_id,
            "path": source.path, "file_size": stat.st_size, "file_mtime_ns": stat.st_mtime_ns,
            "start": clip.startTime, "duration": clip.duration, "trim_start": clip.trimStart,
            "speed": clip.speed, "reversed": clip.reversed, "volume": clip.volume})
    return result


def _cue_signature(cue: EditorSubtitle) -> tuple[str, float, float, int]:
    # Styling can be edited concurrently; only text and placement affect ASR alignment.
    return cue.text, cue.startTime, cue.endTime, cue.trackIndex


def _audio_snapshot(document: ProjectDocument, project: EditorProject, timeline: EditorTimeline,
                    sources: list[NativeSource]) -> tuple[ProjectDocument, set[str]]:
    """Merge contiguous native windows before speed/volume processing, for ASR only."""
    groups: list[list[NativeSource]] = []
    for source in sources:
        if groups and _adjacent(groups[-1][-1], source):
            groups[-1].append(source)
        else:
            groups.append([source])
    selected_ids = {source.clip.id for source in sources}
    selected_clips: list[EditorClip] = []
    for group in groups:
        clip = group[0].clip.model_copy(deep=True)
        clip.duration = sum(item.clip.duration for item in group)
        selected_clips.append(clip)
    snapshot = document.model_copy(deep=True)
    projected = project.model_copy(deep=True)
    selected_timeline = next(item for item in projected.timelines if item.id == timeline.id)
    selected_timeline.clips = [clip for clip in selected_timeline.clips if clip.id not in selected_ids] + selected_clips
    snapshot.editorial_timeline = {**(snapshot.editorial_timeline or {}), "project": cast(dict[str, JsonValue], projected.model_dump(mode="json"))}
    return snapshot, {clip.id for clip in selected_clips}


def align_editorial_native_subtitles(
    service: ShortDramaService, *, document: ProjectDocument,
    request: NativeSubtitleAlignmentRequest, timing_service: AudioTimingService,
    create_report: Callable[[ProjectDocument, str, dict[str, JsonValue], str], ProductionDocumentMutationResponse],
) -> NativeSubtitleAlignmentResponse:
    from short_drama.service import WorkflowConflictError, utc_now

    project, timeline, source_revision = _saved_project(document)
    if request.expected_editorial_revision is not None and request.expected_editorial_revision != source_revision:
        raise WorkflowConflictError(f"Current editorial revision is {source_revision}; read the selected captions before alignment")
    sources = _native_sources(service, document, project, timeline)
    solo = any(track.solo for track in timeline.tracks)
    usable = [cue for cue in timeline.subtitles if timeline.tracks[cue.trackIndex].enabled
              and (not solo or timeline.tracks[cue.trackIndex].solo)]
    known_ids = {cue.id for cue in usable}
    unknown_ids = set(request.cue_ids) - known_ids
    if unknown_ids:
        raise WorkflowConflictError("Selected captions are missing or disabled: " + ", ".join(sorted(unknown_ids)))
    unknown_shots = set(request.shot_ids) - {shot.id for shot in document.shots}
    if unknown_shots:
        raise WorkflowConflictError("Selected shots do not exist: " + ", ".join(sorted(unknown_shots)))
    targets = [cue for cue in usable if (not request.cue_ids or cue.id in request.cue_ids)
        and (not request.shot_ids or any(source.shot_id in request.shot_ids and _overlaps(cue.startTime, cue.endTime, source.clip) for source in sources))]
    if not targets:
        raise WorkflowConflictError("No enabled current captions match the requested native-audio scope")
    relevant = _relevant_sources(sources, targets)
    if not relevant:
        raise WorkflowConflictError("Selected captions have no audible native Take audio; narration and music use their own timed-text workflow")
    before_sources = _source_signature(relevant)
    audio_document, clip_ids = _audio_snapshot(document, project, timeline, relevant)
    prepared = editorial_audio_input(service, audio_document, utc_now(), native_only=True, clip_ids=clip_ids)
    assert prepared is not None
    manifest, _, _ = prepared
    selected_ids = {cue.id for cue in targets}
    candidate = align_native_subtitles(document=document, manifest=manifest, timing_service=timing_service,
        workspace_root=service.workspace_root, cue_ids=selected_ids, use_caption_text=True,
        cue_tracks={cue.id: cue.trackIndex for cue in timeline.subtitles})
    by_id = {cue.id: cue for cue in candidate.cues}
    target_signatures = {cue.id: _cue_signature(cue) for cue in targets}
    warnings = list(candidate.warnings)
    if request.manifest_id is not None:
        warnings.append("Used the saved Studio timeline; the legacy manifest_id was not the source or output of this alignment.")
    with service.studio.lock:
        current = service.repository.get_project(document.project.id)
        if current is None:
            raise KeyError(document.project.id)
        current_project, current_timeline, current_revision = _saved_project(current)
        current_cues = {cue.id: cue for cue in current_timeline.subtitles}
        current_solo = any(track.solo for track in current_timeline.tracks)
        enabled_ids = {cue.id for cue in current_timeline.subtitles
            if current_timeline.tracks[cue.trackIndex].enabled
            and (not current_solo or current_timeline.tracks[cue.trackIndex].solo)}
        if current_timeline.id != timeline.id or any(cue_id not in enabled_ids or _cue_signature(current_cues[cue_id]) != signature for cue_id, signature in target_signatures.items()):
            raise WorkflowConflictError("A selected caption changed during alignment; its current text and timing were preserved")
        current_sources = _relevant_sources(_native_sources(service, current, current_project, current_timeline), [current_cues[cue_id] for cue_id in selected_ids])
        if _source_signature(current_sources) != before_sources:
            raise WorkflowConflictError("Relevant native audio changed during alignment; the current timeline was preserved")
        # Unselected captions may have changed while ASR ran. Refuse a new overlap,
        # while allowing unrelated subtitle, style, graphics and media edits.
        for cue_id in selected_ids:
            aligned = by_id[cue_id]
            original = current_cues[cue_id]
            for other in current_timeline.subtitles:
                if other.id in selected_ids or other.trackIndex != original.trackIndex:
                    continue
                overlaps_now = aligned.start_seconds < other.endTime and aligned.end_seconds > other.startTime
                overlapped_before = original.startTime < other.endTime and original.endTime > other.startTime
                if overlaps_now and not overlapped_before:
                    raise WorkflowConflictError(f"Observed timing for {cue_id} overlaps unselected caption {other.id}; current captions were preserved")
        changed_ids: list[str] = []
        for cue_id in selected_ids:
            cue, aligned = current_cues[cue_id], by_id[cue_id]
            if (cue.startTime, cue.endTime) != (aligned.start_seconds, aligned.end_seconds):
                cue.startTime, cue.endTime = aligned.start_seconds, aligned.end_seconds
                changed_ids.append(cue_id)
        now = utc_now()
        revision = current_revision + bool(changed_ids)
        if changed_ids:
            current.editorial_timeline = {**(current.editorial_timeline or {}), "revision": revision,
                "project": cast(dict[str, JsonValue], current_project.model_dump(mode="json"))}
            current.project.updated_at = now
        changed_ids.sort()
        payload = {"source_editorial_revision": source_revision, "editorial_revision": revision,
            "editorial_timeline_id": timeline.id, "selected_cue_ids": sorted(selected_ids),
            "changed_cue_ids": changed_ids, "source_audio": before_sources,
            "subtitle_text_unchanged": True, "video_audio_and_styles_preserved": True,
            "adjustments": [item.model_dump(mode="json") for item in candidate.adjustments],
            "observations": [item.model_dump(mode="json") for item in candidate.observations],
            "warnings": warnings, "report_path": str(candidate.report_path.resolve()),
            "method": "Local ASR of current native audio after saved trim, speed, reverse and volume; aligned to current caption text.",
            "limitations": "Approximate word timing, not speaker identity or lip-sync verification."}
        report = create_report(current, f"当前剪辑字幕对齐 · r{revision}", cast(dict[str, JsonValue], payload), now)
        report_data = json.loads(candidate.report_path.read_text(encoding="utf-8"))
        candidate.report_path.write_text(json.dumps({**report_data, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
        service.repository.save_project(current)
    return NativeSubtitleAlignmentResponse(source_editorial_revision=source_revision,
        editorial_revision=revision, editorial_timeline_id=timeline.id, changed_cue_ids=changed_ids,
        adjustments=candidate.adjustments, observations=candidate.observations,
        alignment_document_ref=ProductionDocumentArtifactRef(project_id=document.project.id,
            artifact_type="production_document", artifact_id=report.document.id, version=report.version.version),
        alignment_version_ref=ProductionDocumentArtifactRef(project_id=document.project.id,
            artifact_type="production_document_version", artifact_id=report.version.id, version=report.version.version),
        report_path=str(candidate.report_path.resolve()), warnings=warnings)
