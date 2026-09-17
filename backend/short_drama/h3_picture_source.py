"""Resolve one saved picture retake and the sound that stays in the local edit."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
import re
import subprocess
from typing import Protocol

from pydantic import JsonValue
import imageio_ffmpeg

from short_drama.models import H3PictureOnlyBinding, H3PictureOnlyTarget, ProjectDocument, ShotPlan, Take, VideoInspectionReport
from short_drama.studio_audio_excerpt import inspection_audio_source
from short_drama.studio_audio_selection import clip_audio_delegated, clip_audio_origin, clip_audio_path
from short_drama.studio_editorial import EditorProject, clip_media_source
from short_drama.studio_shot_uses import shot_clip_uses, slice_clip


class PictureSourceStore(Protocol):
    def list_takes(self, project_id: str) -> list[Take]: ...
    def list_video_inspections(self, project_id: str) -> list[VideoInspectionReport]: ...


def picture_only_binding(document: ProjectDocument, shot_id: str, target: H3PictureOnlyTarget, *,
                         store: PictureSourceStore, check_revision: bool = True) -> H3PictureOnlyBinding:
    saved = document.editorial_timeline or {}
    if check_revision and saved.get("revision", 0) != target.expected_editorial_revision:
        raise ValueError("PICTURE_ONLY requires the current editorial revision; read the saved timeline again")
    editor = EditorProject.model_validate(saved.get("project"))
    if editor.activeTimelineId != target.timeline_id:
        raise ValueError("PICTURE_ONLY must target the current active timeline")
    timeline = next(t for t in editor.timelines if t.id == target.timeline_id)
    takes = store.list_takes(document.project.id)
    uses = [use for use in shot_clip_uses(timeline, editor.assets, takes, shot_id) if use.clip.id == target.clip_id]
    if len(uses) != 1:
        raise ValueError("PICTURE_ONLY clip must be a current saved use of this shot")
    use = uses[0]
    clip = slice_clip(use.clip, use.start, use.end) if use.shared else use.clip
    if not all(math.isfinite(value) for value in (clip.startTime, clip.duration, clip.trimStart, clip.speed)):
        raise ValueError("PICTURE_ONLY target timing must be finite")
    assets = {asset.id: asset for asset in editor.assets}
    asset = assets.get(clip.assetId or "")
    if asset is None:
        raise ValueError("PICTURE_ONLY target has no current source asset")
    path, _ = clip_media_source(asset, clip)
    take_id = (clip.model_extra or {}).get("sourceTakeId")
    source_take = next((take for take in takes if take.id == take_id and take.project_id == document.project.id
        and take.media_path == path and shot_id in take.covered_shot_ids()), None)
    if source_take is None or not Path(path).is_file():
        raise ValueError("PICTURE_ONLY target must retain an existing exact Take source")
    if clip.trimStart + clip.duration * clip.speed > source_take.duration_seconds + .04:
        raise ValueError("PICTURE_ONLY target exceeds its current source Take")
    shot = next(s for s in document.shots if s.id == shot_id)
    by_take = {take.id: take for take in takes}
    versions = {version.local_path: version for owner in document.assets for version in owner.versions if version.local_path}
    audio: list[dict[str, JsonValue]] = []
    solo = any(track.solo for track in timeline.tracks)
    for sound in timeline.clips:
        if sound.type not in {"audio", "video"} or sound.startTime >= clip.startTime + clip.duration or sound.startTime + sound.duration <= clip.startTime:
            continue
        track = timeline.tracks[sound.trackIndex]
        if not track.enabled or track.muted or solo and not track.solo or sound.muted or sound.volume == 0:
            continue
        if clip_audio_delegated(sound, timeline.clips, assets):
            continue
        audio_path = clip_audio_path(sound, assets)
        if audio_path is None or not Path(audio_path).is_file():
            raise ValueError(f"PICTURE_ONLY retained audio {sound.id} has no readable file")
        origin = clip_audio_origin(sound, timeline.clips, assets).model_extra or {}
        take = by_take.get(str(origin.get("sourceTakeId") or ""))
        version = versions.get(audio_path)
        provenance: dict[str, JsonValue]
        if take is not None and take.project_id == document.project.id and take.media_path == audio_path:
            source_duration = take.duration_seconds
            provenance = {"take_id": take.id, "source_path": take.media_path}
        elif version is not None:
            source_duration = assets[sound.assetId or ""].duration
            provenance = {"asset_version_id": version.id, "source_path": audio_path}
        else:
            excerpt = inspection_audio_source(project_id=document.project.id, clip=sound, path=audio_path,
                take=take, shot_id=str(origin.get("sourceShotId") or ""), reports=store.list_video_inspections(document.project.id))
            if excerpt is None:
                raise ValueError(f"PICTURE_ONLY retained audio {sound.id} has no registered current source")
            source_duration = sound.trimStart + sound.duration * sound.speed
            provenance = {key: value for key, value in asdict(excerpt).items()}
        end = sound.trimStart + sound.duration * sound.speed
        if (not math.isfinite(source_duration) or not math.isfinite(end)
                or sound.trimStart < 0 or end > source_duration + .04):
            raise ValueError(f"PICTURE_ONLY retained audio {sound.id} exceeds its source")
        stat = Path(audio_path).stat()
        audio.append({"clip": sound.model_dump(mode="json"), "track": track.model_dump(mode="json"),
            "source": provenance, "size": stat.st_size, "modified_ns": stat.st_mtime_ns})
    if shot.dialogue_line_ids and not audio:
        raise ValueError("PICTURE_ONLY with dialogue requires actual registered current audio in the target interval; mute flags or prose are not an audio binding")
    stat = Path(path).stat()
    return H3PictureOnlyBinding(project_id=document.project.id, shot_id=shot_id, target=target,
        picture={"clip": clip.model_dump(mode="json"), "container": use.clip.model_dump(mode="json"), "track": timeline.tracks[clip.trackIndex].model_dump(mode="json"),
            "source_path": path, "size": stat.st_size, "modified_ns": stat.st_mtime_ns}, audio_sources=audio)


def assert_picture_binding_current(document: ProjectDocument, binding: H3PictureOnlyBinding, *, store: PictureSourceStore) -> None:
    def relative(value: H3PictureOnlyBinding) -> dict[str, JsonValue]:
        result = value.model_dump(mode="json")
        origin = value.picture["clip"]
        assert isinstance(origin, dict) and isinstance(origin["startTime"], (int, float))
        start = origin["startTime"]
        picture = result["picture"]
        for clip in [picture["clip"], picture["container"], *(row["clip"] for row in result["audio_sources"])]:
            clip["startTime"] = round(clip["startTime"] - start, 6)
        return result

    if binding.project_id != document.project.id or relative(picture_only_binding(document, binding.shot_id, binding.target,
            store=store, check_revision=False)) != relative(binding):
        raise ValueError("PICTURE_ONLY saved picture/audio binding changed; read the current edit and prepare that exact target")


def verify_retained_audio_files(binding: H3PictureOnlyBinding, *, require_audio: bool = True) -> None:
    """Read stream headers and container bounds once; this is not sample coverage or hearing."""
    observed: dict[str, tuple[float, bool]] = {}
    readable_audio = False
    for row in binding.audio_sources:
        source, clip = row["source"], row["clip"]
        assert isinstance(source, dict) and isinstance(clip, dict)
        path = source.get("source_path") or source.get("audio_path")
        assert isinstance(path, str)
        if path not in observed:
            try:
                process = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path,
                    "-t", "0", "-map", "0:a:0", "-f", "null", "-"], capture_output=True, text=True, timeout=20)
            except subprocess.TimeoutExpired as error:
                raise ValueError(f"PICTURE_ONLY retained source header read timed out: {path}") from error
            match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", process.stderr)
            silent_video = ("Video:" in process.stderr and "matches no streams" in process.stderr)
            if match is None or process.returncode and not silent_video:
                raise ValueError(f"PICTURE_ONLY retained source {path} has no readable audio stream/duration")
            observed[path] = (int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]), not silent_video)
        container_duration, has_audio = observed[path]
        if not has_audio and clip["type"] != "video":
            raise ValueError(f"PICTURE_ONLY retained audio clip {clip['id']} has no audio stream")
        readable_audio = readable_audio or has_audio
        start, duration, speed = clip["trimStart"], clip["duration"], clip["speed"]
        assert isinstance(start, (int, float)) and isinstance(duration, (int, float)) and isinstance(speed, (int, float))
        if start + duration * speed > container_duration + .04:
            raise ValueError(f"PICTURE_ONLY retained clip {clip['id']} exceeds its media container duration")
    if require_audio and not readable_audio:
        raise ValueError("PICTURE_ONLY dialogue requires a readable existing audio stream; a silent video is not a sound binding")


def picture_prompt_projection(document: ProjectDocument, shot: ShotPlan, duration: int) -> tuple[ProjectDocument, ShotPlan]:
    """Request-only generation responsibility; never save this projection."""
    scale = duration / shot.duration_seconds
    picture = shot.model_copy(update={"duration_seconds": float(duration), "dialogue_line_ids": [],
        "audio_intent": "Picture only. The current editorial soundtrack is retained locally; generate no speech.",
        "timeline_beats": [beat.model_copy(update={"start_seconds": beat.start_seconds * scale,
            "end_seconds": beat.end_seconds * scale, "dialogue_line_ids": []}) for beat in shot.timeline_beats]})
    projected = document.model_copy(update={"shots": [picture if item.id == shot.id else item for item in document.shots]})
    return projected, picture
