"""Align locked script captions against continuous native Take audio."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from short_drama.audio_inspection import (
    AudioSemanticError, AudioTimingService, TimedAudioTranscript,
    extract_audio_window, normalize_dialogue_text, measure_audio_signal, continuous_audio_groups,
)
from short_drama.models import (
    DomainModel, ProductionDocumentArtifactRef, ProjectDocument, SubtitleCue, TimelineManifest,
)


class NativeSubtitleAlignmentRequest(DomainModel):
    manifest_id: str = Field(min_length=1)


class SubtitleTimingAdjustment(DomainModel):
    cue_id: str
    script_line_id: str
    take_id: str
    previous_start_seconds: float
    previous_end_seconds: float
    start_seconds: float
    end_seconds: float
    matched_characters: int
    script_characters: int


class NativeSubtitleObservation(DomainModel):
    take_id: str
    start_seconds: float
    source_in_seconds: float
    duration_seconds: float
    cue_ids: list[str]
    transcript_text: str


class NativeSubtitleAlignmentResponse(DomainModel):
    manifest: TimelineManifest
    adjustments: list[SubtitleTimingAdjustment]
    observations: list[NativeSubtitleObservation]
    alignment_document_ref: ProductionDocumentArtifactRef
    alignment_version_ref: ProductionDocumentArtifactRef
    report_path: str
    warnings: list[str]


@dataclass(frozen=True)
class SubtitleAlignmentCandidate:
    cues: list[SubtitleCue]
    adjustments: list[SubtitleTimingAdjustment]
    observations: list[NativeSubtitleObservation]
    report_path: Path
    warnings: list[str]


def _character_correspondence(expected: str, actual: str) -> tuple[list[int | None], set[int]]:
    positions: list[int | None] = [None] * len(expected)
    matched: set[int] = set()
    for operation, a1, a2, b1, b2 in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes():
        if operation == "equal":
            for offset in range(a2 - a1):
                positions[a1 + offset] = b1 + offset
                matched.add(a1 + offset)
        elif operation == "replace" and b2 > b1:
            # ASR may spell digits or homophones differently. Retain the script
            # text and interpolate only its location inside the observed span.
            for offset in range(a2 - a1):
                positions[a1 + offset] = b1 + min(b2 - b1 - 1, offset * (b2 - b1) // (a2 - a1))
    return positions, matched


def align_native_subtitles(
    *, document: ProjectDocument, manifest: TimelineManifest,
    timing_service: AudioTimingService, workspace_root: Path,
) -> SubtitleAlignmentCandidate:
    if any(clip.take_id is None for clip in manifest.audio_clips):
        raise AudioSemanticError("Native subtitle alignment requires Take audio; Master Audio uses its timed-text workflow")
    script = next(script for script in document.script_versions if script.id == manifest.script_version_id)
    lines = {line.id: line for line in script.lines}
    report_dir = workspace_root / document.project.id / "inspections" / f"subtitle-alignment-{uuid4().hex}"
    report_dir.mkdir(parents=True, exist_ok=True)
    aligned = {cue.id: cue.model_copy(deep=True) for cue in manifest.subtitle_cues}
    adjustments: list[SubtitleTimingAdjustment] = []
    observations: list[NativeSubtitleObservation] = []
    warnings: list[str] = []
    observed_groups: list[dict[str, object]] = []
    mapped_ids: set[str] = set()
    for sequence, group in enumerate(continuous_audio_groups(manifest.audio_clips), start=1):
        cues = sorted([
            cue for cue in manifest.subtitle_cues
            if cue.start_seconds >= group.start_seconds - 0.001
            and cue.end_seconds <= group.start_seconds + group.duration_seconds + 0.001
        ], key=lambda cue: cue.start_seconds)
        if not cues:
            continue
        if any(cue.script_line_id not in lines for cue in cues):
            raise AudioSemanticError("Every native subtitle cue must reference a current script line")
        audio_path = report_dir / f"source-{sequence:03d}.wav"
        extract_audio_window(source=Path(group.media_path), destination=audio_path,
                       start_seconds=group.source_in_seconds, duration_seconds=group.duration_seconds)
        transcript: TimedAudioTranscript = timing_service.transcribe_timed(
            audio_path=audio_path, language=document.project.language,
        )
        metrics = measure_audio_signal(audio_path)
        observations.append(NativeSubtitleObservation(
            take_id=group.take_id or "", start_seconds=group.start_seconds,
            source_in_seconds=group.source_in_seconds, duration_seconds=group.duration_seconds,
            cue_ids=[cue.id for cue in cues], transcript_text=transcript.transcript_text,
        ))
        expected_parts = [normalize_dialogue_text(lines[cue.script_line_id or ""].spoken_text) for cue in cues]
        positions, matched = _character_correspondence(
            "".join(expected_parts), "".join(character.text for character in transcript.characters),
        )
        observed_groups.append({
            "take_id": group.take_id, "source_path": group.media_path, "audio_path": str(audio_path),
            "timeline_start_seconds": group.start_seconds, "source_in_seconds": group.source_in_seconds,
            "duration_seconds": group.duration_seconds, "transcript": asdict(transcript),
        })
        offset = 0
        for cue, text in zip(cues, expected_parts, strict=True):
            indices = [index for index in positions[offset:offset + len(text)] if index is not None]
            matched_count = sum(index in matched for index in range(offset, offset + len(text)))
            offset += len(text)
            mapped_ids.add(cue.id)
            if not indices or not matched_count:
                warnings.append(f"{cue.id}: no lexical timing anchor; original caption window retained for Agent review.")
                continue
            first = transcript.characters[min(indices)]
            last = transcript.characters[max(indices)]
            start, end = first.start_seconds, last.end_seconds
            activity = [interval for interval in metrics.speech_intervals
                        if interval.end_seconds > start and interval.start_seconds < end]
            if activity:
                # A continuous signal interval can contain several speakers.
                # Do not pull a later line back to the previous speaker's onset.
                start = min(start, max(start - 0.12, activity[0].start_seconds))
                end = min(end, activity[-1].end_seconds)
            start = max(group.start_seconds, group.start_seconds + start - 0.08)
            end = min(group.start_seconds + group.duration_seconds, group.start_seconds + end + 0.12)
            if end <= start:
                raise AudioSemanticError(f"Invalid observed timing for {cue.id}; original manifest was not changed")
            aligned[cue.id] = cue.model_copy(update={"start_seconds": round(start, 3), "end_seconds": round(end, 3)})
            adjustments.append(SubtitleTimingAdjustment(
                cue_id=cue.id, script_line_id=cue.script_line_id or "", take_id=group.take_id or "",
                previous_start_seconds=cue.start_seconds, previous_end_seconds=cue.end_seconds,
                start_seconds=round(start, 3), end_seconds=round(end, 3),
                matched_characters=matched_count, script_characters=len(text),
            ))
    if mapped_ids != set(aligned):
        raise AudioSemanticError("Some subtitle cues do not map to continuous native audio windows")
    cues = [aligned[cue.id] for cue in manifest.subtitle_cues]
    # Padding may overlap the next caption at a speaker cut. End that display at
    # the next observed onset; the underlying video/audio windows stay untouched.
    for current, following in zip(cues, cues[1:]):
        if current.end_seconds > following.start_seconds:
            if following.start_seconds <= current.start_seconds:
                raise AudioSemanticError("Observed caption order is ambiguous; original manifest was not changed")
            current.end_seconds = following.start_seconds
    cue_by_id = {cue.id: cue for cue in cues}
    for adjustment in adjustments:
        adjustment.end_seconds = cue_by_id[adjustment.cue_id].end_seconds
    report_path = report_dir / "alignment.json"
    report_path.write_text(json.dumps({
        "source_manifest_id": manifest.id,
        "method": "Local ASR attention token timestamps aligned to locked script text; signal edges refine padding.",
        "limitations": "Approximate subtitle timing; lexical differences and speaker/lip-sync judgments remain separate.",
        "groups": observed_groups, "adjustments": [item.model_dump(mode="json") for item in adjustments],
        "warnings": warnings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return SubtitleAlignmentCandidate(cues=cues, adjustments=adjustments, observations=observations,
                                      report_path=report_path, warnings=warnings)
