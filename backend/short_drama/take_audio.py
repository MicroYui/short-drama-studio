"""Prepare original candidate sound for observation without changing project content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import JsonValue

from short_drama.models import ApprovalStatus, Freshness, NativeAudioObservationSource, OutputSpec, ProjectDocument, Take, TimelineAudioClip, TimelineManifest
from short_drama.studio_audio import prepare_edited_clip_audio
from short_drama.studio_editorial import EditorClip

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def take_audio_source_context(take: Take, start: float, end: float) -> dict[str, JsonValue]:
    path = Path(take.media_path).expanduser().resolve()
    media: dict[str, JsonValue] = {"path": str(path), "available": False}
    if path.is_file():
        stat = path.stat()
        media = {"path": str(path), "size": stat.st_size, "modified_ns": stat.st_mtime_ns}
    return {"kind": "TAKE", "project_id": take.project_id, "take_id": take.id,
        "shot_id": take.shot_id, "shot_windows": [item.model_dump(mode="json") for item in take.shot_windows],
        "media": media, "source_start_seconds": start, "source_end_seconds": end,
        "audio_projection": "STEREO_PCM_ENVELOPE_TO_MONO_1", "time_space": "TAKE_SOURCE"}


def take_audio_source_is_current(take: Take | None, source: dict[str, JsonValue]) -> bool:
    start, end = source.get("source_start_seconds"), source.get("source_end_seconds")
    if take is None or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return source == take_audio_source_context(take, start, end) and Path(take.media_path).is_file()


@dataclass(frozen=True)
class TakeAudioInput:
    take: Take
    manifest: TimelineManifest
    source_context: dict[str, JsonValue]
    start: float
    end: float


def prepare_take_audio(service: ShortDramaService, document: ProjectDocument,
                       request: NativeAudioObservationSource, created_at: str,
                       *, require_audio: bool = False) -> TakeAudioInput:
    from short_drama.service import ArtifactNotFoundError, WorkflowConflictError

    assert request.scope == "TAKE" and request.take_id is not None
    take = service.repository.get_take(request.take_id)
    if take is None or take.project_id != document.project.id:
        raise ArtifactNotFoundError(request.take_id)
    # Probe and conversion run outside the project/write lock. QA, selection,
    # current scene wording and existing editorial uses do not gate observation.
    path = Path(take.media_path).expanduser().resolve()
    probe = service.video_inspector.probe_video(path)
    start = request.source_start_seconds
    end = request.source_end_seconds if request.source_end_seconds is not None else probe.duration_seconds
    if start >= probe.duration_seconds or end > probe.duration_seconds + .001 or end <= start:
        raise WorkflowConflictError(f"Take audio range {start:g}–{end:g}s exceeds actual media duration {probe.duration_seconds:g}s")
    end = min(end, probe.duration_seconds)
    if require_audio and not probe.audio_present:
        raise WorkflowConflictError(f"Take {take.id} has no native audio stream; no speech was transcribed")
    source_context = take_audio_source_context(take, start, end)
    identity = f"take-audio-{uuid4().hex}"
    folder = service.workspace_root / document.project.id / "inspections" / identity
    clip = EditorClip(id=identity, type="video", startTime=start, duration=end-start, trimStart=start, trackIndex=0)
    prepared_path, source_in = prepare_edited_clip_audio(clip, path, folder, source_type="video", align_source_clock=True)
    manifest = TimelineManifest(id=identity, project_id=document.project.id, version=0,
        script_version_id="", bible_version_id="", video_clips=[], subtitle_cues=[],
        audio_clips=[TimelineAudioClip(id=identity, shot_id=take.shot_id, take_id=take.id,
            media_path=prepared_path, start_seconds=start, source_in_seconds=source_in,
            duration_seconds=end-start, gain_db=0, role="TAKE_SOURCE")],
        output_spec=OutputSpec(), duration_seconds=end,
        approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=created_at)
    return TakeAudioInput(take=take, manifest=manifest, source_context=source_context, start=start, end=end)
