"""Opt-in short-window inspection with actual frame PTS and separate source audio."""
from __future__ import annotations

import math
from pathlib import Path
import re
import subprocess
import wave

import imageio_ffmpeg
from PIL import Image

from short_drama.media import MediaGenerationError
from short_drama.models import VideoAudioExcerpt, VideoFrameSample


def _decode_frames(source: Path, destination: Path, *, selection: str, max_frames: int,
                   max_dimension: int | None, allow_empty: bool = False) -> list[tuple[Path, float]]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"select={selection}", "showinfo"]
    if max_dimension is not None:
        # Bound either orientation without enlarging small source frames.
        filters.append(f"scale=w='min(iw,{max_dimension})':h='min(ih,{max_dimension})':force_original_aspect_ratio=decrease")
    result = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y",
        "-i", str(source), "-an", "-vf", ",".join(filters), "-fps_mode", "vfr",
        "-frames:v", str(max_frames), str(destination),
    ], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise MediaGenerationError(result.stderr[-2000:] or "Could not decode inspection frames")
    # showinfo can log a look-ahead frame after the requested output count.
    points = [float(value) for value in re.findall(r"\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+-]+)", result.stderr)]
    files = sorted(destination.parent.glob(destination.name.replace("%03d", "*")))
    if not files and allow_empty:
        return []
    if not files or len(points) < len(files):
        raise MediaGenerationError("No timestamped source frame in the requested interval")
    return list(zip(files, points[:len(files)], strict=True))


def _decode_last_frame(source: Path, destination: Path, *, start: float, end: float,
                       max_dimension: int | None) -> tuple[Path, float]:
    # Only used when no frame exists at/after the normal target. Decode the
    # selected source interval, keeping original PTS and a single output image.
    # Container duration and average FPS cannot identify a real final frame.
    filters = [f"select=gte(t\\,{start:.9f})*lt(t\\,{end:.9f})", "showinfo"]
    if max_dimension is not None:
        filters.append(f"scale=w='min(iw,{max_dimension})':h='min(ih,{max_dimension})':force_original_aspect_ratio=decrease")
    result = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y",
        "-ss", f"{start:.9f}", "-t", f"{end - start:.9f}", "-copyts", "-i", str(source),
        "-an", "-vf", ",".join(filters), "-fps_mode", "vfr", "-update", "1", str(destination),
    ], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise MediaGenerationError(result.stderr[-2000:] or "Could not decode inspection frames")
    time_base = re.search(r"config in time_base:\s*(\d+)/(\d+)", result.stderr)
    points = re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)", result.stderr)
    if not destination.is_file() or not points or time_base is None:
        raise MediaGenerationError("No timestamped source frame in the requested interval")
    actual = int(points[-1]) * int(time_base[1]) / int(time_base[2])
    return destination, actual


def _sample(take_id: str, path: Path, actual: float, requested: float, reason: str) -> VideoFrameSample:
    with Image.open(path) as frame:
        width, height = frame.size
    return VideoFrameSample(source_take_id=take_id, timestamp_seconds=actual,
        requested_timestamp_seconds=requested, timestamp_is_source_pts=True, width=width, height=height,
        image_path=str(path.resolve()), reasons=[reason])


def sample_dense_window(*, source: Path, take_id: str, output_dir: Path,
                        start: float, end: float, fps: float) -> list[VideoFrameSample]:
    # Keep the first decoded source frame in each requested time bucket. No fps
    # filter invents replacement timestamps or duplicate frames for slow sources.
    bucket = f"floor((t-{start:.9f})*{fps:.9f}+0.000001)"
    previous = f"floor((prev_selected_t-{start:.9f})*{fps:.9f}+0.000001)"
    select = f"gte(t\\,{start:.9f})*lt(t\\,{end:.9f})*if(isnan(prev_selected_t)\\,1\\,gt({bucket}\\,{previous}))"
    frames = _decode_frames(source, output_dir / "dense-%03d.png", selection=select,
                            max_frames=min(36, math.ceil((end - start) * fps - 1e-8)), max_dimension=360)
    return [_sample(take_id, path, actual, start + max(0, math.floor((actual - start) * fps + 1e-5)) / fps,
                    "DENSE_SOURCE_WINDOW") for path, actual in frames]


def extract_detail_frames(*, source: Path, take_id: str, output_dir: Path, times: list[float],
                          start: float, end: float, source_fps: float,
                          max_dimension: int | None) -> list[VideoFrameSample]:
    samples: list[VideoFrameSample] = []
    for index, requested in enumerate(dict.fromkeys(times)):
        if not start <= requested < end:
            raise ValueError("Detail keyframe times must lie inside the actual source interval")
        # Preserve normal selection; the duration-based target can still lie
        # after the last real PTS when audio/container duration extends further.
        target = max(start, min(requested, end - 1 / source_fps))
        select = f"gte(t\\,{target:.9f})*lt(t\\,{end:.9f})"
        frames = _decode_frames(source, output_dir / f"detail-{index}-%03d.png", selection=select,
                                max_frames=1, max_dimension=max_dimension, allow_empty=True)
        if frames:
            path, actual = frames[0]
            reason = "DETAIL_KEYFRAME"
        else:
            path, actual = _decode_last_frame(source, output_dir / f"detail-{index}-tail.png",
                start=start, end=end, max_dimension=max_dimension)
            reason = "DETAIL_KEYFRAME_LAST_AVAILABLE"
        samples.append(_sample(take_id, path, actual, requested, reason))
    return samples


def extract_source_audio(*, source: Path, destination: Path, start: float, end: float) -> VideoAudioExcerpt:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        # Preserve video/source time even when an audio stream begins later:
        # material before its first sample is silence, not an earlier sound.
        "-vn", "-af", f"aresample=async=1:first_pts=0,atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS",
        "-c:a", "pcm_s16le", str(destination),
    ], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise MediaGenerationError(result.stderr[-2000:] or "Could not extract source audio")
    with wave.open(str(destination), "rb") as audio:
        duration, rate, channels = audio.getnframes() / audio.getframerate(), audio.getframerate(), audio.getnchannels()
    if duration <= 0:
        raise MediaGenerationError("The requested interval contains no readable audio samples")
    return VideoAudioExcerpt(audio_path=str(destination.resolve()), source_in_seconds=start,
        source_out_seconds=start + duration, duration_seconds=duration, sample_rate=rate, channels=channels)
