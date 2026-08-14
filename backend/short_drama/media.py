"""Deterministic placeholder media generation and TimelineManifest export."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from short_drama.models import (
    ExportRecord,
    GenerationTask,
    ScriptLine,
    ShotPlan,
    TechnicalCheck,
    TimelineManifest,
)


class MediaGenerationError(RuntimeError):
    """Raised when deterministic media creation or export fails."""


@dataclass(frozen=True)
class RenderedTakeMedia:
    media_path: Path
    thumbnail_path: Path
    width: int
    height: int


def _run_ffmpeg(args: list[str]) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [executable, "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown ffmpeg failure"
        raise MediaGenerationError(detail)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _palette(sequence: int, candidate_index: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256(f"{sequence}:{candidate_index}".encode()).digest()
    base = (10 + digest[0] % 24, 22 + digest[1] % 34, 38 + digest[2] % 42)
    accent = (42 + digest[3] % 38, 92 + digest[4] % 60, 130 + digest[5] % 80)
    return base, accent


class MockMediaRenderer:
    """Creates reproducible 9:16 audiovisual placeholders for every Mock take."""

    width = 432
    height = 768
    fps = 30

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        path = self.workspace_root / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def render_take(
        self,
        *,
        task: GenerationTask,
        shot: ShotPlan,
        script_lines: list[ScriptLine],
    ) -> RenderedTakeMedia:
        take_dir = self._project_dir(task.project_id) / "takes" / shot.id
        take_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{task.operation.value.lower()}-c{task.candidate_index}-a{task.attempt}-{task.id[-8:]}"
        frame_path = take_dir / f"{stem}.png"
        thumbnail_path = take_dir / f"{stem}-thumb.jpg"
        media_path = take_dir / f"{stem}.mp4"

        self._render_frame(
            frame_path=frame_path,
            thumbnail_path=thumbnail_path,
            shot=shot,
            task=task,
            script_lines=script_lines,
        )
        frequency = 185 + shot.sequence * 23 + task.candidate_index * 11
        duration = f"{shot.duration_seconds:.3f}"
        audio_source = (
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}"
        )
        _run_ffmpeg(
            [
                "-y",
                "-loop",
                "1",
                "-framerate",
                str(self.fps),
                "-i",
                str(frame_path),
                "-f",
                "lavfi",
                "-i",
                audio_source,
                "-t",
                duration,
                "-vf",
                f"scale={self.width}:{self.height}:flags=lanczos,format=yuv420p",
                "-r",
                str(self.fps),
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-preset",
                "ultrafast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-b:a",
                "128k",
                "-shortest",
                str(media_path),
            ]
        )
        if not media_path.exists() or media_path.stat().st_size == 0:
            raise MediaGenerationError(f"Mock take was not created: {media_path}")
        return RenderedTakeMedia(
            media_path=media_path,
            thumbnail_path=thumbnail_path,
            width=self.width,
            height=self.height,
        )

    def _render_frame(
        self,
        *,
        frame_path: Path,
        thumbnail_path: Path,
        shot: ShotPlan,
        task: GenerationTask,
        script_lines: list[ScriptLine],
    ) -> None:
        base, accent = _palette(shot.sequence, task.candidate_index)
        image = Image.new("RGB", (self.width, self.height), base)
        draw = ImageDraw.Draw(image)

        for y in range(self.height):
            mix = y / max(1, self.height - 1)
            color = tuple(
                int(base[channel] * (1.0 - mix) + accent[channel] * mix)
                for channel in range(3)
            )
            draw.line((0, y, self.width, y), fill=color)

        # Convenience-store geometry and wet glass highlights.
        draw.rectangle((28, 88, self.width - 28, self.height - 84), outline=(126, 166, 190), width=3)
        draw.line((self.width * 0.63, 88, self.width * 0.63, self.height - 84), fill=(82, 133, 170), width=3)
        draw.rectangle((42, 510, 252, 612), fill=(35, 40, 48), outline=(112, 120, 130), width=2)
        for offset in (0, 74, 148):
            x = 54 + offset + task.candidate_index * 2
            draw.line((x, 100, x - 48, 480), fill=(70, 128, 175), width=2)

        # Two stable character silhouettes on the dialogue axis.
        draw.ellipse((72, 250, 155, 333), fill=(208, 199, 180))
        draw.polygon(((57, 510), (172, 510), (155, 326), (78, 326)), fill=(213, 205, 183))
        draw.ellipse((275, 245, 354, 324), fill=(174, 166, 160))
        draw.polygon(((260, 505), (370, 505), (351, 317), (282, 317)), fill=(61, 64, 70))

        # The red recorder moves according to the locked continuity plan.
        if shot.sequence <= 4:
            recorder_box = (332, 394, 355, 448)
        elif shot.sequence <= 7:
            recorder_box = (194, 522, 222, 575)
        else:
            recorder_box = (91, 433, 117, 486)
        draw.rounded_rectangle(recorder_box, radius=4, fill=(173, 28, 35), outline=(246, 92, 92), width=2)
        if shot.sequence >= 5:
            draw.ellipse((recorder_box[2] - 7, recorder_box[1] + 5, recorder_box[2] - 3, recorder_box[1] + 9), fill=(255, 90, 70))

        header_font = _font(30)
        label_font = _font(18)
        small_font = _font(14)
        draw.rounded_rectangle((24, 24, self.width - 24, 76), radius=12, fill=(7, 11, 20))
        draw.text((42, 34), f"SHOT {shot.sequence:02d}  TAKE {task.candidate_index}", font=header_font, fill=(240, 245, 250))
        draw.rounded_rectangle((24, 634, self.width - 24, 738), radius=12, fill=(7, 11, 20))
        draw.text((40, 650), shot.framing.upper(), font=label_font, fill=(128, 205, 255))
        draw.text((40, 680), f"{shot.duration_seconds:.0f}s  {task.operation.value}", font=small_font, fill=(216, 224, 232))
        line_count = sum(1 for line in script_lines if line.id in shot.dialogue_line_ids)
        draw.text((40, 704), f"AUDIO PLACEHOLDER  LINES {line_count}", font=small_font, fill=(216, 224, 232))

        image.save(frame_path, format="PNG")
        thumbnail = image.copy()
        thumbnail.thumbnail((216, 384))
        thumbnail.save(thumbnail_path, format="JPEG", quality=88)


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


class TimelineExporter:
    """Renders all delivery artifacts from one persisted TimelineManifest."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def render(self, manifest: TimelineManifest, created_at: str) -> ExportRecord:
        if not manifest.video_clips:
            raise MediaGenerationError("TimelineManifest has no video clips")
        export_dir = self.workspace_root / manifest.project_id / "exports" / manifest.id
        export_dir.mkdir(parents=True, exist_ok=True)
        concat_path = export_dir / "selected-takes.txt"
        clean_path = export_dir / "final-clean.mp4"
        subtitled_path = export_dir / "final-subtitled.mp4"
        srt_path = export_dir / "subtitles.srt"
        ass_path = export_dir / "subtitles.ass"
        manifest_path = export_dir / "timeline-manifest.json"
        qa_path = export_dir / "technical-qa.json"

        concat_lines = [f"file '{clip.media_path.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for clip in manifest.video_clips]
        concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        srt_path.write_text(self._build_srt(manifest), encoding="utf-8")
        ass_path.write_text(self._build_ass(manifest), encoding="utf-8")
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")

        spec = manifest.output_spec
        _run_ffmpeg(
            [
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-vf",
                f"scale={spec.width}:{spec.height}:flags=lanczos,setsar=1",
                "-r",
                str(spec.fps),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-profile:v",
                spec.profile,
                "-preset",
                "ultrafast",
                "-crf",
                str(spec.crf),
                "-pix_fmt",
                spec.pixel_format,
                "-color_primaries",
                spec.color_space,
                "-color_trc",
                spec.color_space,
                "-colorspace",
                spec.color_space,
                "-c:a",
                spec.audio_codec,
                "-ar",
                str(spec.audio_sample_rate),
                "-ac",
                "2",
                "-b:a",
                f"{spec.audio_bitrate_kbps}k",
                "-movflags",
                "+faststart",
                str(clean_path),
            ]
        )

        if manifest.subtitle_cues:
            _run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(clean_path),
                    "-vf",
                    f"ass='{_filter_path(ass_path)}'",
                    "-c:v",
                    "libx264",
                    "-profile:v",
                    spec.profile,
                    "-preset",
                    "ultrafast",
                    "-crf",
                    str(spec.crf),
                    "-pix_fmt",
                    spec.pixel_format,
                    "-color_primaries",
                    spec.color_space,
                    "-color_trc",
                    spec.color_space,
                    "-colorspace",
                    spec.color_space,
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(subtitled_path),
                ]
            )
        else:
            shutil.copy2(clean_path, subtitled_path)

        checks = self._technical_checks(manifest, clean_path, subtitled_path)
        qa_path.write_text(
            json.dumps(
                {"manifest_id": manifest.id, "checks": [check.model_dump() for check in checks]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return ExportRecord(
            id=f"export-{manifest.id}",
            project_id=manifest.project_id,
            manifest_id=manifest.id,
            clean_video_path=str(clean_path.resolve()),
            subtitled_video_path=str(subtitled_path.resolve()),
            srt_path=str(srt_path.resolve()),
            ass_path=str(ass_path.resolve()),
            manifest_path=str(manifest_path.resolve()),
            qa_report_path=str(qa_path.resolve()),
            checks=checks,
            created_at=created_at,
        )

    def _build_srt(self, manifest: TimelineManifest) -> str:
        blocks = [
            "\n".join(
                (
                    str(index),
                    f"{_format_srt_time(cue.start_seconds)} --> {_format_srt_time(cue.end_seconds)}",
                    cue.text,
                )
            )
            for index, cue in enumerate(manifest.subtitle_cues, start=1)
        ]
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def _build_ass(self, manifest: TimelineManifest) -> str:
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,48,&H00FFFFFF,&H000000FF,&H00101010,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,90,170,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = [
            f"Dialogue: 0,{_format_ass_time(cue.start_seconds)},{_format_ass_time(cue.end_seconds)},Default,{cue.speaker},0,0,0,,{_escape_ass_text(cue.text)}"
            for cue in manifest.subtitle_cues
        ]
        return header + "\n".join(events) + ("\n" if events else "")

    def _technical_checks(
        self,
        manifest: TimelineManifest,
        clean_path: Path,
        subtitled_path: Path,
    ) -> list[TechnicalCheck]:
        checks: list[TechnicalCheck] = []
        for label, path in (("clean", clean_path), ("subtitled", subtitled_path)):
            exists = path.exists() and path.stat().st_size > 0
            checks.append(TechnicalCheck(name=f"{label}_file", passed=exists, detail=str(path)))
            if not exists:
                continue
            metadata = self._probe_with_ffmpeg(path)
            expected_size = f"{manifest.output_spec.width}x{manifest.output_spec.height}"
            checks.extend(
                [
                    TechnicalCheck(
                        name=f"{label}_resolution",
                        passed=metadata["size"] == expected_size,
                        detail=f"expected {expected_size}, got {metadata['size']}",
                    ),
                    TechnicalCheck(
                        name=f"{label}_fps",
                        passed=abs(float(metadata["fps"]) - manifest.output_spec.fps) < 0.2,
                        detail=f"expected {manifest.output_spec.fps}, got {metadata['fps']}",
                    ),
                    TechnicalCheck(
                        name=f"{label}_audio",
                        passed=metadata["audio"] == "present",
                        detail=f"audio {metadata['audio']}",
                    ),
                    TechnicalCheck(
                        name=f"{label}_duration",
                        passed=abs(float(metadata["duration"]) - manifest.duration_seconds) < 0.35,
                        detail=f"expected {manifest.duration_seconds:.3f}, got {metadata['duration']}",
                    ),
                ]
            )
        checks.append(
            TechnicalCheck(
                name="manifest_clip_clock",
                passed=abs(
                    sum(clip.duration_seconds for clip in manifest.video_clips)
                    - manifest.duration_seconds
                )
                < 0.001,
                detail="video clip durations equal TimelineManifest duration",
            )
        )
        return checks

    def _probe_with_ffmpeg(self, path: Path) -> dict[str, str]:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [executable, "-hide_banner", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stderr
        if result.returncode != 0:
            raise MediaGenerationError(output.strip() or f"Could not probe {path}")
        size_match = re.search(r"Video:.*?\b(\d{2,5}x\d{2,5})\b", output)
        fps_match = re.search(r"\b(\d+(?:\.\d+)?) fps\b", output)
        duration_match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", output)
        duration = 0.0
        if duration_match is not None:
            duration = (
                int(duration_match.group(1)) * 3600
                + int(duration_match.group(2)) * 60
                + float(duration_match.group(3))
            )
        return {
            "size": size_match.group(1) if size_match is not None else "unknown",
            "fps": fps_match.group(1) if fps_match is not None else "0",
            "duration": f"{duration:.3f}",
            "audio": "present" if "Audio:" in output else "missing",
        }
