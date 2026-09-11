"""Export the saved LTX editor document using the shared local export runtime."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Thread
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import imageio_ffmpeg
from pydantic import JsonValue
from short_drama.export_dimensions import source_output_dimensions
from short_drama.media import probe_media_with_ffmpeg
from short_drama.studio_editorial import EditorProject, EditorTimeline, validate_editorial
from short_drama.studio_models import StudioExport, StudioExportRequest
from short_drama.studio_service import StudioContentConflict

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def _save(service: ShortDramaService, record: StudioExport) -> None:
    with service.studio.lock:
        document = service.repository.get_project(record.project_id)
        if document is None:
            raise KeyError(record.project_id)
        value = cast(dict[str, JsonValue], record.model_dump(mode="json"))
        index = next((i for i, item in enumerate(document.studio_exports) if item.get("id") == record.id), None)
        if index is None:
            document.studio_exports.append(value)
        else:
            document.studio_exports[index] = value
        service.repository.save_project(document)
        service.repository.append_studio_event(record.project_id, "export.updated", value)


def get_export(service: ShortDramaService, project_id: str, export_id: str) -> StudioExport:
    document = service.get_project(project_id).document
    value = next((item for item in document.studio_exports if item.get("id") == export_id), None)
    if value is None:
        raise KeyError(export_id)
    return StudioExport.model_validate(value)


def inspect_export(service: ShortDramaService, project_id: str, export_id: str | None = None) -> dict[str, JsonValue]:
    """Read and decode the exact editorial export, independently of legacy manifests."""
    document = service.get_project(project_id).document
    if export_id is None:
        value = next((item for item in reversed(document.studio_exports) if item.get("status") == "completed"), None)
        if value is None:
            raise ValueError("本集尚无已完成的剪辑导出。")
        export_id = str(value["id"])
    record = get_export(service, project_id, export_id)
    files: list[JsonValue] = []
    for label, path in (("subtitled", record.video_path), ("clean", record.clean_video_path)):
        if not path:
            continue
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"导出记录中的 {label} 视频未找到。")
        probe = probe_media_with_ffmpeg(source)
        files.append({"kind": label, "path": path, "bytes": source.stat().st_size, "decoded": True,
            "duration_seconds": float(probe["duration"]), "size": probe["size"], "fps": float(probe["fps"]), "audio": probe["audio"]})
    checks_path = Path(record.input_path).with_name("technical-check.json") if record.input_path else None
    checks = cast(JsonValue, json.loads(checks_path.read_text())) if checks_path and checks_path.is_file() else None
    return {"export": cast(dict[str, JsonValue], record.model_dump(mode="json")), "files": files,
        "current_editorial_revision": (document.editorial_timeline or {}).get("revision", 0), "technical_check": checks,
        "review_scope": "实际解码、尺寸、时长、帧率与音轨检查；不等于模型审美或剧情审校。"}


def _selected_video_dimensions(project: EditorProject) -> list[tuple[int, int]]:
    """Probe the same active source selection consumed by studio-export-cli."""
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    has_solo = any(track.solo for track in timeline.tracks)
    assets = {asset.id: asset for asset in project.assets}
    sizes_by_path: dict[str, tuple[int, int]] = {}
    for clip in timeline.clips:
        track = timeline.tracks[clip.trackIndex]
        if clip.type != "video" or clip.opacity <= 0 or not track.enabled or (has_solo and not track.solo):
            continue
        asset = assets[clip.assetId or ""]
        source = cast(dict[str, JsonValue], asset.model_dump(mode="json"))
        take_index = (clip.model_extra or {}).get("takeIndex")
        takes = source.get("takes")
        if (isinstance(take_index, int) and not isinstance(take_index, bool)
                and isinstance(takes, list) and 0 <= take_index < len(takes)):
            selected = takes[take_index]
            if isinstance(selected, dict):
                source = selected
        path = source.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"片段 {clip.id} 的实际视频来源缺少路径。")
        if path not in sizes_by_path:
            # Decode just the first frame for geometry. Full-stream validation
            # already belongs to ingestion and the completed export check.
            result = subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path,
                "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-",
            ], capture_output=True, text=True, check=False)
            dimensions = re.findall(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", result.stderr)
            if result.returncode != 0 or not dimensions:
                raise ValueError(f"片段 {clip.id} 的实际视频尺寸不可读，无法确定跟随素材的导出尺寸。")
            # The output stream also accounts for FFmpeg's display rotation.
            width, height = (int(value) for value in dimensions[-1])
            if width <= 0 or height <= 0:
                raise ValueError(f"片段 {clip.id} 的视频尺寸无效。")
            sizes_by_path[path] = (width, height)
    return list(sizes_by_path.values())


def start_export(service: ShortDramaService, project_id: str, request: StudioExportRequest, *, background: bool = True) -> StudioExport:
    with service.studio.lock:
        document = service.get_project(project_id).document
        saved = document.editorial_timeline or {}
        if saved.get("revision") != request.expected_revision or not isinstance(saved.get("project"), dict):
            raise StudioContentConflict("请先保存当前剪辑，再导出同一份内容。")
        project = validate_editorial(service, project_id, cast(dict[str, JsonValue], saved["project"]))
        timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
        if not any(clip.type in {"video", "image"} for clip in timeline.clips):
            raise ValueError("时间线尚无可导出的画面。")
        for clip in timeline.clips:
            extra = clip.model_extra or {}
            transitions = [extra.get(key, {}) for key in ("transitionIn", "transitionOut")]
            if clip.type in {"text", "adjustment"} or any(isinstance(item, dict) and cast(dict[str, object], item).get("type") not in (None, "none") for item in transitions):
                raise ValueError("当前导出支持镜头、多轨、透明度、PNG图层、裁切、变速、翻转、声音和字幕。请移除尚未接通的文字层、调整层或转场；画面文字可使用字幕或PNG图版。")
        aspect_ratio = document.project.aspect_ratio

    # Actual file inspection can take time; don't hold the user's content lock.
    if request.width is not None and request.height is not None:
        width, height = request.width // 2 * 2, request.height // 2 * 2
    else:
        width, height = source_output_dimensions(_selected_video_dimensions(project), aspect_ratio)

    with service.studio.lock:
        document = service.get_project(project_id).document
        if (document.editorial_timeline or {}).get("revision") != request.expected_revision:
            raise StudioContentConflict("确认素材尺寸期间剪辑已改变，请导出当前保存的剪辑。")
        if request.width is None and document.project.aspect_ratio != aspect_ratio:
            raise StudioContentConflict("确认素材尺寸期间项目画幅已改变，请按当前画幅重新导出。")
        for old in document.studio_exports:
            if old.get("status") == "running":
                if (old.get("editorial_revision") == request.expected_revision
                        and old.get("width") == width and old.get("height") == height and old.get("fps") == request.fps):
                    return StudioExport.model_validate(old)
                raise StudioContentConflict("另一份内容或尺寸的成片正在导出。完成后可以继续导出当前剪辑。")
        record = StudioExport(id=f"editorial-export-{uuid4().hex[:12]}", project_id=project_id,
            editorial_revision=request.expected_revision, width=width,
            height=height, fps=request.fps, created_at=datetime.now(UTC).isoformat())
        _save(service, record)
    if background:
        Thread(target=_render, args=(service, project, request, record), daemon=True, name=record.id).start()
    else:
        _render(service, project, request, record)
    return record


def _timestamp(seconds: float, *, ass: bool = False) -> str:
    if ass:
        return f"{int(seconds // 3600)}:{int(seconds // 60 % 60):02}:{int(seconds % 60):02}.{int(seconds * 100) % 100:02}"
    millis = round(seconds * 1000)
    return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}"


def _write_subtitles(timeline: EditorTimeline, folder: Path, width: int, height: int) -> tuple[Path, Path]:
    srt, ass = folder / "subtitles.srt", folder / "subtitles.ass"
    srt_lines: list[str] = []
    ass_lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{32 * height / 1080},&H00FFFFFF,&H00FFFFFF,&H66000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,{width*.05:.0f},{width*.05:.0f},{height/36:.0f},1",
        f"Style: Box,Arial,{32 * height / 1080},&H00FFFFFF,&H00FFFFFF,&H66000000,&H00000000,0,0,0,0,100,100,0,0,3,4,0,2,{width*.05:.0f},{width*.05:.0f},{height/36:.0f},1",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for cue in timeline.subtitles:
        track = timeline.tracks[cue.trackIndex]
        if not track.enabled or (any(item.solo for item in timeline.tracks) and not track.solo):
            continue
        srt_lines.append(f"{len(srt_lines)+1}\n{_timestamp(cue.startTime)} --> {_timestamp(cue.endTime)}\n{cue.text}\n")
        style = {**cast(dict[str, JsonValue], (track.model_extra or {}).get("subtitleStyle", {})), **cue.style}
        alignment = {"top": 8, "center": 5}.get(str(style.get("position")), 2)
        size_value = style.get("fontSize", 32)
        size = float(size_value) if isinstance(size_value, (int, float)) else 32
        color = str(style.get("color", "#FFFFFF")).removeprefix("#")[:6]
        color = color if len(color) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in color) else "FFFFFF"
        bgr = color[4:6] + color[2:4] + color[:2]
        font = str(style.get("fontFamily", "Arial")).split(",")[0].strip("\"'")
        if font in {"sans-serif", "system-ui"}: font = "Arial"
        font = font.replace("{", "").replace("}", "").replace("\\", "")
        background = str(style.get("backgroundColor", "transparent")).removeprefix("#")
        box = len(background) in {6, 8} and all(ch in "0123456789abcdefABCDEF" for ch in background)
        extra = ""
        if box:
            bg = background[4:6] + background[2:4] + background[:2]
            alpha = 255 - int(background[6:8], 16) if len(background) == 8 else 0
            extra = f"\\3c&H{bg}&\\3a&H{alpha:02X}&\\bord{4*height/1080:.2f}"
        text = cue.text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        tag = f"{{\\fn{font}{extra}\\an{alignment}\\fs{size * height / 1080:.2f}\\c&H{bgr}&\\b{1 if style.get('fontWeight') == 'bold' else 0}\\i{1 if style.get('italic') else 0}}}"
        style_name = "Box" if box else "Default"
        ass_lines.append(f"Dialogue: 0,{_timestamp(cue.startTime, ass=True)},{_timestamp(cue.endTime, ass=True)},{style_name},,0,0,0,,{tag}{text}")
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    ass.write_text("\n".join(ass_lines), encoding="utf-8")
    return srt, ass


def _render(service: ShortDramaService, project: EditorProject, request: StudioExportRequest, record: StudioExport) -> None:
    folder = service.workspace_root / record.project_id / "editorial-exports" / record.id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        node = os.environ.get("STUDIO_NODE_EXECUTABLE") or shutil.which("node")
        runtime = Path(__file__).parent / "runtime" / "studio-export.cjs"
        if not node or not runtime.exists():
            raise ValueError("剪辑导出运行环境缺失：请运行本地构建脚本 build-studio-runtime.mjs。")
        clean = folder / "film-clean.mp4"
        input_file = folder / "editorial-input.json"
        input_file.write_text(json.dumps({"project": project.model_dump(mode="json"), "editorial_revision": record.editorial_revision,
            "ffmpegPath": imageio_ffmpeg.get_ffmpeg_exe(), "width": record.width, "height": record.height,
            "fps": record.fps, "outputPath": str(clean)}, ensure_ascii=False, indent=2), encoding="utf-8")
        record.input_path = str(input_file)
        _save(service, record)
        process = subprocess.Popen([node, str(runtime), str(input_file)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"})
        logs: list[str] = []
        if process.stdout is None:
            raise RuntimeError("无法读取本地导出进度。")
        for line in process.stdout:
            logs.append(line)
            try:
                event: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(cast(dict[str, object], event).get("progress"), str):
                record.progress = str(cast(dict[str, object], event)["progress"])
                _save(service, record)
        code = process.wait()
        (folder / "export.log").write_text("".join(logs), encoding="utf-8")
        if code:
            raise ValueError("".join(logs)[-1500:])
        timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
        srt, ass = _write_subtitles(timeline, folder, record.width, record.height)
        final = clean
        if request.subtitles and timeline.subtitles:
            record.progress = "渲染字幕"
            _save(service, record)
            final = folder / "film-subtitled.mp4"
            escaped = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
            result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(clean), "-vf", f"ass='{escaped}'", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(final)], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr[-1500:])
        has_solo = any(track.solo for track in timeline.tracks)
        record.duration_seconds = max((clip.startTime + clip.duration for clip in timeline.clips
            if timeline.tracks[clip.trackIndex].enabled and (not has_solo or timeline.tracks[clip.trackIndex].solo)), default=0)
        technical = probe_media_with_ffmpeg(final)
        checks = {"duration": abs(float(technical["duration"]) - record.duration_seconds) < max(0.15, 2 / record.fps),
                  "size": technical["size"] == f"{record.width}x{record.height}",
                  "fps": abs(float(technical["fps"]) - record.fps) < 0.02,
                  "audio_track": technical["audio"] == "present"}
        (folder / "technical-check.json").write_text(json.dumps({"expected_duration": record.duration_seconds, "media": technical, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not all(checks.values()):
            raise ValueError("导出文件检查未通过：" + ", ".join(name for name, passed in checks.items() if not passed))
        record.clean_video_path, record.video_path, record.srt_path = str(clean), str(final), str(srt)
        record.status, record.progress = "completed", "导出完成"
    except Exception as exc:
        record.status, record.progress, record.error = "failed", "导出未完成", str(exc)
    _save(service, record)


def recover_interrupted_exports(service: ShortDramaService) -> None:
    """A stopped local render can be retried; never present it as still running."""
    for document in service.repository.list_projects():
        for value in document.studio_exports:
            if value.get("status") == "running":
                record = StudioExport.model_validate(value)
                record.status, record.progress = "failed", "上次导出已中断"
                record.error = "应用在导出完成前退出。当前剪辑已保存，可重新导出。"
                _save(service, record)
