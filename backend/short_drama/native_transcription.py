"""Observe current native speech even when the film has no captions yet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import Field, JsonValue

from short_drama.audio_inspection import AudioTimingService, extract_audio_window
from short_drama.models import DomainModel, ProjectDocument
from short_drama.studio_audio import editorial_audio_input
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_subtitles import native_audio_snapshot, native_audio_sources, native_source_signature

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class NativeTranscriptionRequest(DomainModel):
    timeline_id: str | None = Field(default=None, min_length=1)
    expected_editorial_revision: int | None = Field(default=None, ge=0)
    clip_ids: list[str] = Field(default_factory=list[str], max_length=100)
    shot_ids: list[str] = Field(default_factory=list[str], max_length=100)


class TranscribedCharacter(DomainModel):
    text: str
    start_seconds: float
    end_seconds: float


class NativeTranscriptWindow(DomainModel):
    take_id: str
    clip_ids: list[str]
    shot_ids: list[str]
    start_seconds: float
    end_seconds: float
    transcript_text: str
    characters: list[TranscribedCharacter]


class NativeTranscriptionResponse(DomainModel):
    source_editorial_revision: int
    editorial_timeline_id: str
    time_space: str = "FILM"
    windows: list[NativeTranscriptWindow]
    report_path: str
    warnings: list[str]
    limitation: str = "Local ASR observations and approximate character times; not speaker identification, lip-sync review or verified dialogue. No captions were saved."


def transcribe_editorial_native(
    service: ShortDramaService, *, document: ProjectDocument,
    request: NativeTranscriptionRequest, timing_service: AudioTimingService,
) -> NativeTranscriptionResponse:
    from short_drama.service import WorkflowConflictError, utc_now

    # The initial editorial view is derived from actual adopted Takes. A first
    # transcription must not require a whole-project save or a fake caption.
    saved = read_editorial(service, document.project.id)
    if request.expected_editorial_revision is not None and request.expected_editorial_revision != saved.revision:
        raise WorkflowConflictError(f"Current editorial revision is {saved.revision}; read the selected audio before transcription")
    project = EditorProject.model_validate(saved.project)
    timeline_id = request.timeline_id or project.activeTimelineId
    timeline = next((item for item in project.timelines if item.id == timeline_id), None)
    if timeline is None:
        raise WorkflowConflictError(f"Timeline {timeline_id!r} does not exist")
    project.activeTimelineId = timeline.id
    sources = native_audio_sources(service, document, project, timeline)
    known_clips = {source.clip.id for source in sources}
    known_shots = {source.shot_id for source in sources}
    missing_clips = set(request.clip_ids) - known_clips
    missing_shots = set(request.shot_ids) - known_shots
    if missing_clips or missing_shots:
        raise WorkflowConflictError("Selected scope has no audible native Take audio: " + ", ".join(sorted(missing_clips | missing_shots)))
    selected = sorted([source for source in sources
        if (not request.clip_ids or source.clip.id in request.clip_ids)
        and (not request.shot_ids or source.shot_id in request.shot_ids)], key=lambda source: source.clip.startTime)
    if not selected:
        raise WorkflowConflictError("No audible native Take audio in this scope; adopt video with audio first. Music and separate narration are excluded")
    source_signature = native_source_signature(selected)
    snapshot = document.model_copy(deep=True)
    snapshot.editorial_timeline = {"revision": saved.revision, "project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}
    audio_document, clip_ids = native_audio_snapshot(snapshot, project, timeline, selected)
    prepared = editorial_audio_input(service, audio_document, utc_now(), native_only=True, clip_ids=clip_ids)
    assert prepared is not None
    manifest = prepared[0]
    report_dir = service.workspace_root / document.project.id / "inspections" / f"native-transcript-{uuid4().hex}"
    report_dir.mkdir(parents=True, exist_ok=True)
    windows: list[NativeTranscriptWindow] = []
    warnings: list[str] = []
    for source in manifest.audio_clips:
        if any(other.id != source.id and source.start_seconds < other.start_seconds + other.duration_seconds
               and other.start_seconds < source.start_seconds + source.duration_seconds for other in manifest.audio_clips):
            warnings.append(f"{source.id}: overlapping native audio is transcribed by source, not diarized or treated as the final mix")
        offset = 0.0
        while offset < source.duration_seconds - .001:
            # Whisper's ordinary timestamp path has a 30-second audio window.
            duration = min(30.0, source.duration_seconds - offset)
            start = source.start_seconds + offset
            end = start + duration
            path = report_dir / f"source-{len(windows) + 1:03d}.wav"
            extract_audio_window(source=Path(source.media_path), destination=path,
                start_seconds=source.source_in_seconds + offset, duration_seconds=duration)
            transcript = timing_service.transcribe_timed(audio_path=path, language=document.project.language)
            related = [item for item in selected if item.take_id == source.take_id
                and item.clip.startTime < end and item.clip.startTime + item.clip.duration > start]
            characters: list[TranscribedCharacter] = []
            for character in transcript.characters:
                first = min(duration, max(0.0, character.start_seconds))
                last = min(duration, max(first, character.end_seconds))
                characters.append(TranscribedCharacter(text=character.text,
                    start_seconds=round(start + first, 3), end_seconds=round(start + last, 3)))
            windows.append(NativeTranscriptWindow(take_id=source.take_id or "",
                clip_ids=[item.clip.id for item in related], shot_ids=list(dict.fromkeys(item.shot_id for item in related)),
                start_seconds=round(start, 3), end_seconds=round(end, 3),
                transcript_text=transcript.transcript_text, characters=characters))
            if source.duration_seconds > 30:
                warnings.append(f"{source.id}: long source split into at most 30-second observations; review words at window boundaries")
            offset += duration
    current = read_editorial(service, document.project.id)
    if current.revision != saved.revision:
        warnings.append(f"Editorial changed during transcription from r{saved.revision} to r{current.revision}; observations refer to the captured source, read current placement before saving captions")
    response = NativeTranscriptionResponse(source_editorial_revision=saved.revision,
        editorial_timeline_id=timeline.id, windows=windows, warnings=list(dict.fromkeys(warnings)),
        report_path=str((report_dir / "transcript.json").resolve()))
    Path(response.report_path).write_text(json.dumps({**response.model_dump(mode="json"),
        "source_audio": source_signature}, ensure_ascii=False, indent=2), encoding="utf-8")
    return response
