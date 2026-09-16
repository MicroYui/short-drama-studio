"""Resolve and prepare one exact saved use; source video and editorial sound stay separate."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.models import ProjectDocument, Take, VideoEditorialUse
from short_drama.production_media_context import resolve_editorial_uses
from short_drama.studio_audio import prepare_edited_clip_audio
from short_drama.studio_audio_selection import clip_audio_delegated, clip_audio_path
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, EditorTimeline, EditorTrack, editorial_clip_enabled
from short_drama.video_edit_models import LipSyncRequest, VideoEditRun, VideoEditSourceFile


@dataclass
class LipSyncSelection:
    editor: EditorProject
    timeline: EditorTimeline
    use: VideoEditorialUse
    clip: EditorClip
    audio: EditorClip
    audio_asset: EditorAsset
    audio_path: str
    baseline: dict[str, JsonValue]


_SOURCE_FIELDS = {"id", "assetId", "type", "startTime", "duration", "trimStart", "trimEnd", "speed", "reversed",
    "trackIndex", "sourceShotId", "sourceTakeId", "sourceWindowStart", "sourceWindowEnd", "takeIndex", "linkedClipIds",
    "muted", "volume", "audioFadeInSeconds", "audioFadeOutSeconds", "audioEnvelopeOffsetSeconds", "audioEnvelopeDurationSeconds"}


def _clip_source(clip: EditorClip) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], clip.model_dump(mode="json", include=_SOURCE_FIELDS))


def _track_source(track: EditorTrack, timeline: EditorTimeline) -> dict[str, JsonValue]:
    return {"id": track.id, "enabled": track.enabled, "muted": track.muted, "solo": track.solo,
            "effectively_enabled": track.enabled and (not any(item.solo for item in timeline.tracks) or track.solo)}


def select_source(document: ProjectDocument, takes: list[Take], request: LipSyncRequest) -> LipSyncSelection:
    saved = document.editorial_timeline or {}
    raw = saved.get("project")
    if not isinstance(raw, dict):
        raise ValueError("请先保存当前剪辑，再选择要修复的画面使用位置。")
    project = cast(dict[str, JsonValue], raw)
    use, _ = resolve_editorial_uses(document, target_shot_id=request.shot_id, editorial_project=project,
        takes=takes, timeline_id=request.timeline_id, clip_id=request.clip_id, require_target=True, include_previous=False)
    assert use is not None
    if use.shot_id != request.shot_id:
        raise ValueError("所选使用位置不属于指定镜头。")
    if request.start_seconds < use.timeline_start_seconds - 1e-7 or request.end_seconds > use.timeline_end_seconds + 1e-7:
        raise ValueError("口型修复选区必须完整落在指定画面片段内，不能跨镜头。")
    editor = EditorProject.model_validate(project)
    timeline = next(item for item in editor.timelines if item.id == request.timeline_id)
    clip = next(item for item in timeline.clips if item.id == request.clip_id)
    assets = {item.id: item for item in editor.assets}
    if clip_audio_path(clip, assets) != use.media_path:
        raise ValueError("所选画面实际媒体与 Take 绑定不一致，请先保存正确的素材绑定后修复。")
    if request.audio_source is not None:
        audio = next((item for item in timeline.clips if item.id == request.audio_source.clip_id), None)
    elif clip_audio_delegated(clip, timeline.clips, assets):
        linked = (clip.model_extra or {}).get("linkedClipIds", [])
        candidates = [item for item in timeline.clips if item.type == "audio"
            and (item.id in linked or clip.id in (item.model_extra or {}).get("linkedClipIds", []))
            and clip_audio_path(item, assets) == use.media_path]
        if len(candidates) != 1:
            raise ValueError("原声有多个关联使用位置，请用 audio_source.clip_id 明确指定正确声音。")
        audio = candidates[0]
    else:
        audio = clip
    if audio is None or audio.type not in {"video", "audio"}:
        raise ValueError("未找到已采用的驱动声音，请指定同一时间线的声音片段。")
    audio_track = timeline.tracks[audio.trackIndex]
    if (not editorial_clip_enabled(timeline, audio) or audio_track.muted or audio.muted or audio.volume <= 0
            or clip_audio_delegated(audio, timeline.clips, assets)):
        raise ValueError("驱动声音当前被静音、禁用或交给关联音轨；请指定实际可听见的声音片段。")
    if audio.startTime > request.start_seconds + 1e-7 or audio.startTime + audio.duration < request.end_seconds - 1e-7:
        raise ValueError("驱动声音必须覆盖整个修复选区；本工具不补造未覆盖声音。")
    asset = assets.get(audio.assetId or "")
    if asset is None or asset.type not in {"audio", "video"}:
        raise ValueError("驱动声音缺少实际媒体绑定。")
    audio_path = clip_audio_path(audio, assets)
    if not audio_path:
        raise ValueError("驱动声音缺少可读媒体路径。")
    baseline: dict[str, JsonValue] = {"clip": _clip_source(clip), "track": _track_source(timeline.tracks[clip.trackIndex], timeline),
        "transitionIn": (clip.model_extra or {}).get("transitionIn"), "transitionOut": (clip.model_extra or {}).get("transitionOut"),
        "audio": _clip_source(audio), "audio_track": _track_source(audio_track, timeline),
        "source_media_path": use.media_path, "audio_media_path": audio_path}
    return LipSyncSelection(editor, timeline, use, clip, audio, asset, audio_path, baseline)


def source_file(path: str) -> VideoEditSourceFile:
    file = Path(path)
    stat = file.stat()
    if not file.is_file() or stat.st_size == 0:
        raise ValueError(f"媒体文件不可用：{path}")
    return VideoEditSourceFile(path=path, size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns)


def slice_clip(clip: EditorClip, start: float, end: float, *, identity: str | None = None) -> EditorClip:
    offset, duration = start - clip.startTime, end - start
    trim = clip.trimStart + ((clip.duration - offset - duration) if clip.reversed else offset) * clip.speed
    values = clip.model_dump(mode="json")
    values.update(id=identity or clip.id, startTime=start, duration=duration, trimStart=max(0.0, trim), trimEnd=0,
        audioEnvelopeOffsetSeconds=(clip.audioEnvelopeOffsetSeconds or 0) + offset,
        audioEnvelopeDurationSeconds=clip.audioEnvelopeDurationSeconds or clip.duration)
    if start > clip.startTime + 1e-7:
        values["transitionIn"] = {"type": "none", "duration": 0}
    if end < clip.startTime + clip.duration - 1e-7:
        values["transitionOut"] = {"type": "none", "duration": 0}
    return EditorClip.model_validate(values)


def prepared_inputs(run: VideoEditRun, folder: Path) -> VideoEditRun:
    for expected in run.source_files:
        if source_file(expected.path) != expected:
            raise ValueError("所选源媒体文件已变化，请重新读取当前使用位置后发起修复。")
    probe = probe_media_with_ffmpeg(Path(run.source_media_path))
    if probe["size"] == "unknown":
        raise MediaGenerationError("源视频缺少可解码画面。")
    width, height = (int(item) for item in probe["size"].split("x"))
    target_width, target_height = (1280, 720) if width >= height else (720, 1280)
    if abs((width / height) / (target_width / target_height) - 1) > .025:
        raise ValueError("首项口型修复只支持接近 16:9 或 9:16 的原视频；不能改变构图来满足供应商尺寸。")
    fps = float(probe["fps"])
    if not 12 <= fps <= 60:
        raise ValueError("口型修复源视频帧率须在 12–60 fps。")
    if run.source_out_seconds > float(probe["duration"]) + .01:
        raise ValueError("所选区间超出实际源视频时长，请重新读取可用媒体；不能用定格补造缺少画面。")
    duration = run.request.end_seconds - run.request.start_seconds
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "prepared-video.mp4"
    temporary = folder / "prepared-video.part.mp4"
    filters = [f"trim=start={run.source_in_seconds}:end={run.source_out_seconds}",
        f"setpts=(PTS-STARTPTS)/{run.source_speed}"]
    if run.source_reversed:
        filters.extend(["reverse", "setpts=PTS-STARTPTS"])
    filters.extend([f"tpad=stop_mode=clone:stop_duration={1 / fps}", f"fps={fps}", f"trim=duration={duration}",
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2", "setsar=1"])
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", run.source_media_path,
            "-vf", ",".join(filters), "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)], capture_output=True, text=True)
        if result.returncode:
            raise MediaGenerationError(result.stderr)
        if not 0 < temporary.stat().st_size <= 100 * 1024 * 1024:
            raise ValueError("规范化视频超出供应商 100 MB 上限。")
        temporary.replace(video)
    finally:
        temporary.unlink(missing_ok=True)
    audio_clip = slice_clip(EditorClip.model_validate(run.audio_clip), run.request.start_seconds, run.request.end_seconds)
    audio_path, _ = prepare_edited_clip_audio(audio_clip, Path(run.audio_media_path), folder, source_type=run.audio_asset_type)
    if audio_path == run.audio_media_path:
        raise ValueError("所选视频没有可听审的原声音轨，不能据此发起口型修复。")
    audio = folder / "prepared-audio.wav"
    Path(audio_path).replace(audio)
    if not 0 < audio.stat().st_size <= 5 * 1024 * 1024:
        raise ValueError("驱动声音超出供应商 5 MB 上限。")
    run.prepared_video_path, run.prepared_audio_path = str(video), str(audio)
    run.prepared_width, run.prepared_height = target_width, target_height
    run.prepared_duration_seconds, run.prepared_fps = duration, fps
    run.prepared_audio_sha256 = hashlib.sha256(audio.read_bytes()).hexdigest()
    return run
