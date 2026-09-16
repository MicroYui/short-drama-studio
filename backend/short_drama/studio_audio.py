"""Read the saved editor timeline as an audio-inspection input, without rebuilding it."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import imageio_ffmpeg

from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.models import (
    ApprovalStatus, Freshness, OutputSpec, ProjectDocument, SubtitleCue,
    TimelineAudioClip, TimelineManifest,
)
from short_drama.studio_editorial import EditorClip, EditorProject
from short_drama.studio_audio_selection import clip_audio_delegated, clip_audio_origin, clip_audio_path

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def _edited_audio(clip: EditorClip, path: Path, folder: Path, *, source_type: str | None = None,
                  align_source_clock: bool = False) -> tuple[str, float]:
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"clip-{uuid4().hex[:12]}.wav"
    filters = [f"atrim=start={clip.trimStart}:end={clip.trimStart + clip.duration * clip.speed}",
               "asetpts=PTS-STARTPTS"]
    if align_source_clock:
        # Original Take coordinates include silence before a late audio stream,
        # matching video_detail.extract_source_audio's source clock.
        filters.insert(0, "aresample=async=1:first_pts=0")
    rate = clip.speed
    while rate > 2:
        filters.append("atempo=2")
        rate /= 2
    while rate < .5:
        filters.append("atempo=0.5")
        rate *= 2
    if rate != 1:
        filters.append(f"atempo={rate}")
    if clip.reversed:
        filters.append("areverse")
    # Match frontend/lib/audio-envelope.ts and export/audio-mix.ts: apply
    # the minimum of both linear fades in playback time, after speed/reverse.
    # Split pieces retain the original envelope duration and time offset.
    duration = clip.audioEnvelopeDurationSeconds or clip.duration
    time = f"(t+{clip.audioEnvelopeOffsetSeconds or 0})"
    fade_in = min(duration, clip.audioFadeInSeconds)
    fade_out = min(duration, clip.audioFadeOutSeconds)
    attack = f"min(1,max(0,{time}/{fade_in}))" if fade_in else "1"
    release = f"min(1,max(0,({duration}-{time})/{fade_out}))" if fade_out else "1"
    gain = f"{clip.volume}*min({attack},{release})"
    # Export extracts stereo/48 kHz Int16 before gain, then rounds the result
    # to that same PCM format. Preserve this order before inspection's mono
    # downmix, including the exporter's mono-to-stereo channel gain.
    stereo_pcm = "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"
    filters.extend([stereo_pcm,
        f"aeval=exprs='val(0)*{gain}|val(1)*{gain}'", stereo_pcm,
        f"apad=whole_dur={clip.duration}", f"atrim=end={clip.duration}"])
    process = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
        "-i", str(path), "-vn", "-af", ",".join(filters), "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(destination)], capture_output=True, text=True)
    if process.returncode:
        if (clip.type == "video" or source_type == "video") and probe_media_with_ffmpeg(path)["audio"] == "missing":
            # Keep the original evidence so AudioInspector can report the
            # absent stream; never manufacture a silent sound track.
            return str(path), clip.trimStart
        raise MediaGenerationError(process.stderr.strip())
    return str(destination), 0.0


def prepare_edited_clip_audio(clip: EditorClip, path: Path, folder: Path, *, source_type: str | None = None,
                               align_source_clock: bool = False) -> tuple[str, float]:
    """Share the exact adopted sound preparation with local video edits."""
    return _edited_audio(clip, path, folder, source_type=source_type, align_source_clock=align_source_clock)


def editorial_audio_input(
    service: ShortDramaService, document: ProjectDocument, created_at: str,
    *, native_only: bool = False, clip_ids: set[str] | None = None,
) -> tuple[TimelineManifest, int, str] | None:
    """Project the exact saved clips, original audio and subtitles for this inspection."""
    saved = document.editorial_timeline or {}
    value = saved.get("project")
    if not isinstance(value, dict):
        return None
    project = EditorProject.model_validate(value)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    revision = int(cast(int, saved.get("revision", 0)))
    assets = {asset.id: asset for asset in project.assets}
    takes = {take.id: take for take in service.repository.list_takes(document.project.id)}
    versions = {version.local_path: (asset.id, version.id)
                for asset in document.assets for version in asset.versions if version.local_path}
    solo = any(track.solo for track in timeline.tracks)
    folder = service.workspace_root / document.project.id / "inspections" / f"editorial-input-{uuid4().hex}"
    audio: list[TimelineAudioClip] = []
    for clip in timeline.clips:
        if clip_ids is not None and clip.id not in clip_ids:
            continue
        track = timeline.tracks[clip.trackIndex]
        if (clip.type not in {"video", "audio"} or not track.enabled or solo and not track.solo
                or track.muted or clip.muted or clip.volume == 0):
            continue
        if clip_audio_delegated(clip, timeline.clips, assets):
            continue
        asset = assets.get(clip.assetId or "")
        if asset is None:
            raise ValueError(f"Saved audio clip {clip.id} has no bound media")
        origin = clip_audio_origin(clip, timeline.clips, assets).model_extra or {}
        source_path = clip_audio_path(clip, assets)
        assert source_path is not None
        take_id = str(origin.get("sourceTakeId") or "")
        shot_id = str(origin.get("sourceShotId") or "")
        take = takes.get(take_id)
        asset_id, version_id = versions.get(source_path, (None, None))
        if take is not None and take.media_path == source_path and shot_id:
            audio_shot_id, audio_take_id = shot_id, take_id
            asset_id, version_id = None, None
        elif native_only:
            continue
        elif version_id is not None:
            audio_shot_id, audio_take_id = None, None
        else:
            raise ValueError(f"Saved audio clip {clip.id} has no current Take or asset binding")
        path, source_in = _edited_audio(clip, Path(source_path), folder, source_type=asset.type)
        audio.append(TimelineAudioClip(id=clip.id, shot_id=audio_shot_id, take_id=audio_take_id,
            asset_id=asset_id, asset_version_id=version_id, media_path=path,
            start_seconds=clip.startTime, source_in_seconds=source_in,
            duration_seconds=clip.duration, gain_db=0, role="EDITORIAL_AUDIO"))

    script = next((item for item in document.script_versions
                   if item.id == document.project.current_script_version_id), None)
    lines = {line.id: line for line in script.lines} if script else {}
    old_cues = {cue.id: cue for manifest in document.manifests for cue in manifest.subtitle_cues}
    subtitles: list[SubtitleCue] = []
    for cue in timeline.subtitles:
        track = timeline.tracks[cue.trackIndex]
        if not track.enabled or solo and not track.solo:
            continue
        original = old_cues.get(cue.id)
        line_id = original.script_line_id if original else None
        line = lines.get(line_id or "")
        if line is None:
            matching = [item for item in lines.values() if item.subtitle_text == cue.text]
            line = matching[0] if len(matching) == 1 else None
        subtitles.append(SubtitleCue(id=cue.id, script_line_id=line.id if line else None,
            timed_text_id=None if line else cue.id, text=cue.text,
            start_seconds=cue.startTime, end_seconds=cue.endTime,
            speaker=line.speaker if line else None, language="zh-CN" if line else None))
    manifest = TimelineManifest(id=f"editorial-{timeline.id}-r{revision}",
        project_id=document.project.id, version=revision,
        script_version_id=document.project.current_script_version_id or "",
        bible_version_id=document.project.current_bible_version_id or "",
        video_clips=[], audio_clips=audio, subtitle_cues=subtitles,
        output_spec=OutputSpec(), duration_seconds=max(
            (clip.startTime + clip.duration for clip in timeline.clips), default=0),
        approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=created_at)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.json").write_text(json.dumps({"editorial_revision": revision,
        "timeline_id": timeline.id, "project": project.model_dump(mode="json"),
        "audio_input": manifest.model_dump(mode="json")}, ensure_ascii=False), encoding="utf-8")
    return manifest, revision, timeline.id
