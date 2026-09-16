"""Read-only applicability of existing observations; no new reviews or decisions."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.models import AudioInspectionReport, EpisodeReviewReport, ProjectDocument, Take, TimelineManifest, VideoEditorialUse, VideoInspectionReport
from short_drama.studio_editorial import EditorProject


_AUDIO_CLIP_FIELDS = {"id", "assetId", "type", "startTime", "duration", "trimStart", "speed", "reversed",
    "muted", "volume", "audioFadeInSeconds", "audioFadeOutSeconds",
    "audioEnvelopeOffsetSeconds", "audioEnvelopeDurationSeconds"}
_AUDIO_SUBTITLE_FIELDS = {"id", "text", "startTime", "endTime"}


def _audio_entry_source(value: JsonValue) -> JsonValue:
    """Also narrow older snapshots that retained visual and editor UI fields."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    clip = value.get("clip")
    if isinstance(clip, dict):
        result["clip"] = {key: field for key, field in clip.items() if key in _AUDIO_CLIP_FIELDS}
    subtitles = value.get("subtitles")
    if isinstance(subtitles, list):
        result["subtitles"] = [{key: field for key, field in cue.items() if key in _AUDIO_SUBTITLE_FIELDS}
                               for cue in subtitles if isinstance(cue, dict)]
    return result


def _media(path_value: str) -> dict[str, JsonValue]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        return {"path": str(path), "available": False}
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "modified_ns": stat.st_mtime_ns}


def video_report_source_status(document: ProjectDocument, take: Take, report: VideoInspectionReport,
                               takes: dict[str, Take]) -> tuple[str, list[str]]:
    from short_drama.video_inspection import inspection_shots, inspection_source_context

    if report.take_id != take.id or report.observed_source_out_seconds is None or not report.source_context:
        return "HISTORICAL_OR_UNVERIFIED", ["Missing exact observation source/range"]
    previous = takes.get(report.previous_take_id or "")
    try:
        shots = inspection_shots(document, take, report.observed_source_in_seconds, report.observed_source_out_seconds)
        current = inspection_source_context(document, take, shots, previous)
        if current != report.source_context:
            return "HISTORICAL_OR_UNVERIFIED", ["Observed media, shots or references changed"]
        if report.observation_scope == "EDITORIAL":
            from short_drama.production_media_context import resolve_editorial_uses
            from short_drama.studio_editorial import project_editorial_from_document

            context = report.editorial_context
            if context is None:
                return "HISTORICAL_OR_UNVERIFIED", ["Missing editorial use identity"]
            saved = (document.editorial_timeline or {}).get("project")
            editorial = cast(dict[str, JsonValue], saved) if isinstance(saved, dict) else project_editorial_from_document(document, list(takes.values()))
            use, before = resolve_editorial_uses(document, target_shot_id=context.current.shot_id,
                editorial_project=editorial, takes=list(takes.values()), timeline_id=context.current.timeline_id,
                clip_id=context.current.clip_id, require_target=True, include_previous=context.include_previous_boundary)
            if use != context.current or (context.include_previous_boundary and before != context.previous):
                return "HISTORICAL_OR_UNVERIFIED", ["Observed editorial use or preceding cut changed"]
    except (ValueError, StopIteration):
        return "HISTORICAL_OR_UNVERIFIED", ["Observed source no longer resolves"]
    return "CURRENT", []


def video_evidence_for_use(document: ProjectDocument, take: Take, reports: list[VideoInspectionReport],
                           takes: dict[str, Take], start: float, end: float,
                           editorial_use: VideoEditorialUse | None = None) -> dict[str, JsonValue]:
    evidence: list[JsonValue] = []
    coverage: list[tuple[float, float]] = []
    for report in reports:
        if report.take_id != take.id:
            continue
        status, reasons = video_report_source_status(document, take, report, takes)
        stop = report.observed_source_out_seconds
        overlaps = stop is not None and report.observed_source_in_seconds < end and stop > start
        if status == "CURRENT" and not overlaps:
            status, reasons = "OUTSIDE_REQUESTED_RANGE", ["Observation does not overlap this use"]
        if status == "CURRENT" and report.observation_scope == "EDITORIAL" and editorial_use is not None and (
                report.editorial_context is None or report.editorial_context.current != editorial_use):
            status, reasons = "OUTSIDE_REQUESTED_USE", ["Editorial observation belongs to a different clip use"]
        item: dict[str, JsonValue] = {
            "id": report.id, "source_status": status, "source_reasons": cast(JsonValue, reasons),
            "semantic_status": report.semantic_status.value, "review_focus": report.review_focus,
            "observed_source_in_seconds": report.observed_source_in_seconds,
            "observed_source_out_seconds": stop, "covered_shot_ids": cast(JsonValue, report.covered_shot_ids),
            "observation_scope": report.observation_scope,
            "editorial_context": report.editorial_context.model_dump(mode="json") if report.editorial_context else None,
        }
        if status == "CURRENT":
            item.update({"observations": [value.model_dump(mode="json") for value in report.observations],
                "issues": [value.model_dump(mode="json") for value in report.issues], "summary": report.summary,
                "contact_sheet_path": report.contact_sheet_path,
                "audio_semantics_observed": report.audio_semantics_observed})
            # A focused question never establishes coverage of other qualities.
            if report.semantic_status.value == "COMPLETE" and report.review_focus is None and stop is not None:
                coverage.append((max(start, report.observed_source_in_seconds), min(end, stop)))
        evidence.append(item)
    cursor = start
    missing: list[JsonValue] = []
    for left, right in sorted(coverage):
        if left > cursor + .001:
            missing.append({"source_in_seconds": cursor, "source_out_seconds": left})
        cursor = max(cursor, right)
    if cursor < end - .001:
        missing.append({"source_in_seconds": cursor, "source_out_seconds": end})
    return {"requested_source_in_seconds": start, "requested_source_out_seconds": end,
        "reports": evidence, "unobserved_general_review_ranges": missing,
        "coverage_note": "Range coverage describes sampled visual evidence, not continuous motion, audio or a quality approval."}


def audio_source_context(document: ProjectDocument) -> dict[str, JsonValue]:
    from short_drama.studio_audio_selection import clip_audio_delegated, clip_audio_origin, clip_audio_path
    saved = document.editorial_timeline or {}
    value = saved.get("project")
    script = next((item for item in document.script_versions if item.id == document.project.current_script_version_id), None)
    if not isinstance(value, dict):
        manifest = next((item for item in document.manifests if item.id == document.project.current_manifest_id), None)
        return {"kind": "MANIFEST", "manifest_id": manifest.id if manifest else None,
            "manifest": manifest.model_dump(mode="json") if manifest else None,
            "script": script.model_dump(mode="json") if script else None,
            "media": [_media(clip.media_path) for clip in manifest.audio_clips] if manifest else []}
    project = EditorProject.model_validate(value)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    assets = {item.id: item for item in project.assets}
    solo = any(track.solo for track in timeline.tracks)
    clips: dict[str, JsonValue] = {}
    for clip in timeline.clips:
        track = timeline.tracks[clip.trackIndex]
        if clip.type not in {"audio", "video"} or not track.enabled or (solo and not track.solo) or track.muted or clip.muted or clip.volume == 0:
            continue
        if clip_audio_delegated(clip, timeline.clips, assets):
            continue
        origin = clip_audio_origin(clip, timeline.clips, assets)
        origin_extra = origin.model_extra or {}
        source_path = clip_audio_path(clip, assets)
        cues = [cue for cue in timeline.subtitles if cue.startTime < clip.startTime + clip.duration
                and cue.endTime > clip.startTime and timeline.tracks[cue.trackIndex].enabled
                and (not solo or timeline.tracks[cue.trackIndex].solo)]
        texts = {cue.text for cue in cues}
        shot = next((item for item in document.shots if item.id == origin_extra.get("sourceShotId")), None)
        line_ids: set[str] = set(shot.dialogue_line_ids) if shot else set()
        clips[clip.id] = {"shot_id": cast(JsonValue, origin_extra.get("sourceShotId")),
            "take_id": cast(JsonValue, origin_extra.get("sourceTakeId")),
            # Effective enabled/solo/muted track state determines membership
            # above. Names, ordering and purely visual parameters do not alter audio.
            "clip": clip.model_dump(mode="json", include=_AUDIO_CLIP_FIELDS), "media": _media(source_path) if source_path else None,
            "subtitles": [cue.model_dump(mode="json", include=_AUDIO_SUBTITLE_FIELDS) for cue in cues],
            "lines": [line.model_dump(mode="json") for line in script.lines
                      if line.id in line_ids or line.subtitle_text in texts] if script else []}
    return {"kind": "EDITORIAL", "timeline_id": timeline.id,
        "audio_projection": "STEREO_PCM_ENVELOPE_TO_MONO_1", "clips": clips}


def audio_report_evidence(document: ProjectDocument, report: AudioInspectionReport, *,
                          shot_id: str | None = None, take_id: str | None = None,
                          timeline_start: float | None = None, timeline_end: float | None = None,
                          source_take: Take | None = None) -> dict[str, JsonValue]:
    if report.observation_scope == "TAKE":
        from short_drama.take_audio import take_audio_source_is_current

        # A source interval is not a FILM interval, even for the same Take.
        # Editorial consumers must inspect the actual use; no implicit mapping.
        film_scope = timeline_start is not None or timeline_end is not None
        current = (source_take is not None and source_take.project_id == document.project.id
                   and take_audio_source_is_current(source_take, report.source_context))
        entries = [item for item in report.clip_inspections if (take_id is None or item.take_id == take_id)
                   and (shot_id is None or item.shot_id == shot_id)]
        status = "OUTSIDE_REQUESTED_USE" if film_scope else "CURRENT" if current and entries else "HISTORICAL_OR_UNVERIFIED"
        return {"id": report.id, "source_status": status, "observation_scope": "TAKE", "time_space": "TAKE_SOURCE",
            "source_reasons": ["Original Take observations do not establish edited FILM coverage"] if film_scope else [] if current and entries
                else ["Original Take source is missing, changed or not verified"],
            "semantic_status": report.semantic_status.value, "source_take_id": report.source_take_id,
            "observed_source_start_seconds": report.observed_source_start_seconds,
            "observed_source_end_seconds": report.observed_source_end_seconds,
            "clip_inspections": [item.model_dump(mode="json") for item in entries] if status == "CURRENT" else [],
            "dialogue_checks": [], "speaker_checks": [],
            "historical_clip_ids": [item.clip_id for item in entries] if status != "CURRENT" else [],
            "limitations": "Original candidate signal evidence only; no assumed dialogue, current editorial coverage, speaker identity, lip sync or adoption."}
    current = audio_source_context(document)
    source = report.source_context
    entries = [item for item in report.clip_inspections if (shot_id is None or item.shot_id == shot_id)
               and (take_id is None or item.take_id == take_id)
               and (timeline_start is None or item.timeline_start_seconds + item.expected_duration_seconds > timeline_start)
               and (timeline_end is None or item.timeline_start_seconds < timeline_end)]
    latest_clips = current.get("clips")
    source_clips = source.get("clips")
    same_timeline = source.get("timeline_id") == current.get("timeline_id")
    same_projection = source.get("audio_projection") == current.get("audio_projection")
    if source and source.get("kind") == "EDITORIAL" and isinstance(latest_clips, dict) and isinstance(source_clips, dict):
        all_valid = [item for item in report.clip_inspections if same_timeline and same_projection and item.clip_id in source_clips
                 and _audio_entry_source(source_clips[item.clip_id]) == _audio_entry_source(latest_clips.get(item.clip_id))]
        valid = [item for item in entries if item in all_valid]
    elif source:
        all_valid = report.clip_inspections if source == current else []
        valid = [item for item in entries if item in all_valid]
    else:
        exact_manifest = (current.get("kind") == "MANIFEST" and report.manifest_id == current.get("manifest_id")
                          and report.source_script_version_id == document.project.current_script_version_id)
        all_valid = report.clip_inspections if exact_manifest else []
        valid = [item for item in entries if item in all_valid]
    dialogue = [item for item in report.dialogue_checks if any(
        clip.shot_id == item.shot_id and clip.take_id == item.take_id
        and item.cue_start_seconds >= clip.timeline_start_seconds - .001
        and item.cue_end_seconds <= clip.timeline_start_seconds + clip.expected_duration_seconds + .001 for clip in valid)
        and (timeline_start is None or item.cue_end_seconds > timeline_start)
        and (timeline_end is None or item.cue_start_seconds < timeline_end)]
    # A speaker comparison also depends on its other samples, not only this cut.
    line_ids = {item.script_line_id for item in dialogue}
    all_current_lines = {item.script_line_id for item in report.dialogue_checks if any(
        clip.shot_id == item.shot_id and clip.take_id == item.take_id
        and item.cue_start_seconds >= clip.timeline_start_seconds - .001
        and item.cue_end_seconds <= clip.timeline_start_seconds + clip.expected_duration_seconds + .001 for clip in all_valid)}
    speaker = [item for item in report.speaker_checks if line_ids.intersection(item.script_line_ids)
               and set(item.script_line_ids).issubset(all_current_lines)]
    status = "CURRENT" if valid and len(valid) == len(entries) else "PARTIAL" if valid else "HISTORICAL_OR_UNVERIFIED"
    reasons: list[JsonValue] = [] if status == "CURRENT" else [
        "Observation predates the current editorial audio channel/envelope projection; inspect again for current signal evidence"
        if current.get("kind") == "EDITORIAL" and not same_projection
        else "Some observed clips no longer match current audio, subtitles or dialogue sources"]
    return {"id": report.id, "source_status": status,
        "source_reasons": reasons,
        "semantic_status": report.semantic_status.value,
        "source_editorial_revision": report.source_editorial_revision,
        "source_editorial_timeline_id": report.source_editorial_timeline_id,
        "clip_inspections": [item.model_dump(mode="json") for item in valid],
        "dialogue_checks": [item.model_dump(mode="json") for item in dialogue],
        "speaker_checks": [item.model_dump(mode="json") for item in speaker],
        "historical_clip_ids": [item.clip_id for item in entries if item not in valid],
        "limitations": "Signal measurements, ASR and acoustic similarity are separate evidence; none establishes lip sync or unheard performance. Editorial signal evidence measures each clip as mono after export channel conversion, not the complete mixed soundtrack or master attenuation."}


def boundary_audio_evidence(document: ProjectDocument, use: VideoEditorialUse,
                             reports: list[AudioInspectionReport]) -> dict[str, object]:
    for report in reversed(reports):
        if report.source_editorial_timeline_id != use.timeline_id:
            continue
        evidence = audio_report_evidence(document, report, shot_id=use.shot_id, take_id=use.take_id,
            timeline_start=use.timeline_start_seconds, timeline_end=use.timeline_end_seconds)
        if evidence["clip_inspections"]:
            return {"audio_observation_status": "CURRENT_AUDIO_ANALYSIS", "audio_evidence": evidence,
                    "audio_evidence_scope": "Only the listed current clip/ASR intervals; no inferred sound or lip-sync verdict."}
    return {"audio_observation_status": "UNKNOWN"}


def episode_source_context(document: ProjectDocument, manifest: TimelineManifest, takes: list[Take],
                           editorial_project: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
    from short_drama.video_inspection import inspection_source_context
    from short_drama.studio_episode_review import editorial_scene_context

    by_take = {item.id: item for item in takes}
    script = next((item for item in document.script_versions if item.id == manifest.script_version_id), None)
    scenes = {item.id: item for item in script.structured_scenes} if script else {}
    used = {clip.shot_id for clip in manifest.video_clips}
    contexts: dict[str, JsonValue] = {}
    for shot in document.shots:
        if shot.id not in used:
            continue
        clips = [clip for clip in manifest.video_clips if clip.shot_id == shot.id]
        contexts[shot.id] = {"shot": shot.model_dump(mode="json"),
            "scene": scenes[shot.scene_id].model_dump(mode="json") if shot.scene_id in scenes else None,
            "lines": [line.model_dump(mode="json") for line in script.lines if line.id in shot.dialogue_line_ids] if script else [],
            "clips": [clip.model_dump(mode="json") for clip in clips],
            "media": [inspection_source_context(document, by_take[clip.take_id], [shot])
                      if clip.take_id in by_take else {"missing_take_id": clip.take_id} for clip in clips],
            "editorial": editorial_scene_context(editorial_project, [shot.id]) if editorial_project else None}
    return {"shots": contexts, "episode": {
        "name": document.project.name,
        "production_profile": document.project.production_profile.value,
        "target_duration_seconds": document.project.target_duration_seconds,
        "script_lines": [item.model_dump(mode="json") for item in script.lines] if script else [],
        "scenes": [item.model_dump(mode="json") for item in script.structured_scenes] if script else [],
    }}


def episode_report_is_current(document: ProjectDocument, report: EpisodeReviewReport, takes: list[Take],
                              *, shot_id: str | None = None) -> bool:
    from short_drama.studio_episode_review import editorial_episode_input

    try:
        editorial = editorial_episode_input(document, report.created_at)
        manifest = editorial[0] if editorial else next((item for item in document.manifests if item.id == document.project.current_manifest_id), None)
        if manifest is None or not report.source_context:
            return False
        current = episode_source_context(document, manifest, takes, editorial[2] if editorial else None)
        if shot_id is None:
            return current == report.source_context
        current_shots, observed_shots = current.get("shots"), report.source_context.get("shots")
        return (isinstance(current_shots, dict) and isinstance(observed_shots, dict)
                and shot_id in observed_shots and observed_shots[shot_id] == current_shots.get(shot_id))
    except ValueError:
        return False
