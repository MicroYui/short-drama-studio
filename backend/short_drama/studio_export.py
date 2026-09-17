"""Export the saved LTX editor document using the shared local export runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import imageio_ffmpeg
import psutil
from pydantic import JsonValue
from short_drama.export_dimensions import source_output_dimensions
from short_drama.media import probe_media_with_ffmpeg
from short_drama.studio_editorial import EditorProject, EditorTimeline, editorial_clip_enabled, validate_editorial
from short_drama.studio_models import StudioExport, StudioExportRequest, StudioExports
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_records import cursor_for_record
from short_drama.studio_visual_export import validate_visual_export
from short_drama.studio_subtitle_fonts import default_subtitle_font_family, resolved_subtitle_family, wrap_subtitle_for_export

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class _ExportCancelled(Exception):
    pass


@dataclass
class _ExportControl:
    cancelled: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    process_lock: Lock = field(default_factory=Lock)
    stop_lock: Lock = field(default_factory=Lock)
    process: subprocess.Popen[str] | None = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise _ExportCancelled()


_ACTIVE_LOCK = Lock()
_ACTIVE: dict[tuple[str, str, str], _ExportControl] = {}
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _key(service: ShortDramaService, project_id: str, export_id: str) -> tuple[str, str, str]:
    return str(service.repository.database_path.resolve()), project_id, export_id


def _control(service: ShortDramaService, project_id: str, export_id: str) -> _ExportControl | None:
    with _ACTIVE_LOCK:
        return _ACTIVE.get(_key(service, project_id, export_id))


def _save(service: ShortDramaService, record: StudioExport) -> None:
    with service.studio.lock, service.repository.atomic():
        previous = service.repository.get_studio_export(record.project_id, record.id)
        if previous is not None:
            if previous.status in _TERMINAL:
                # A completed export and a confirmed cancellation cannot be
                # overwritten by a late progress event or competing finish.
                for name in StudioExport.model_fields:
                    setattr(record, name, getattr(previous, name))
                return
            if previous.status == "cancelling":
                if record.status == "running":
                    record.status, record.progress = "cancelling", "正在停止导出"
                elif record.status == "completed":
                    # All render processes have finished at this boundary, but
                    # cancellation acquired the content lock before completion.
                    record.status, record.progress, record.error = "cancelled", "导出已取消", None
                    record.video_path = record.clean_video_path = record.srt_path = None
        record.updated_at = datetime.now(UTC).isoformat()
        if record.status in _TERMINAL:
            record.finished_at = record.finished_at or record.updated_at
        value = cast(dict[str, JsonValue], record.model_dump(mode="json"))
        service.repository.save_studio_export(record)
        service.repository.append_studio_event(record.project_id, "export.updated", value)


def get_export(service: ShortDramaService, project_id: str, export_id: str) -> StudioExport:
    value = service.repository.get_studio_export(project_id, export_id)
    if value is None:
        raise KeyError(export_id)
    return value


def list_exports(service: ShortDramaService, project_id: str, *, limit: int = 20, before: str | None = None) -> StudioExports:
    size = max(1, min(limit, 100))
    records = service.repository.list_studio_exports(project_id, limit=size + 1, before=before)
    shown = records[:size]
    cursor = cursor_for_record(cast(dict[str, JsonValue], shown[-1].model_dump(mode="json"))) if len(records) > size else None
    return StudioExports(exports=shown, next_cursor=cursor)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    # Each subprocess starts in its own session. Include children such as the
    # runtime's FFmpeg; never signal the backend or another export's group.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    # Process objects retain identity checks if a PID was reused. A child that
    # ignores SIGTERM must also exit before cancellation is acknowledged.
    _gone, alive = psutil.wait_procs(children, timeout=2)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(alive, timeout=2)
    for child in alive:
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                raise RuntimeError("导出子进程尚未停止，请稍后重试取消。")
        except psutil.NoSuchProcess:
            pass


def _stop_process(control: _ExportControl) -> None:
    with control.stop_lock:
        with control.process_lock:
            process = control.process
        if process is not None:
            _terminate_process(process)


def cancel_export(service: ShortDramaService, project_id: str, export_id: str) -> StudioExport:
    with service.studio.lock:
        record = get_export(service, project_id, export_id)
        if record.status in _TERMINAL or (record.status == "cancelling" and not record.error):
            return record
        control = _control(service, project_id, export_id)
        if control is None:
            record.status, record.progress = "failed", "上次导出已中断"
            record.error = "未找到仍在执行的本地导出。当前剪辑已保存，可重新导出。"
            _save(service, record)
            return record
        control.cancelled.set()
        record.status, record.progress, record.error = "cancelling", "正在停止导出", None
        _save(service, record)
    # The render thread persists cancelled only after its process actually
    # exits. Process termination never holds the user's content lock.
    def stop() -> None:
        try:
            _stop_process(control)
        except Exception as exc:
            with service.studio.lock:
                current = get_export(service, project_id, export_id)
                if current.status == "cancelling":
                    current.error = f"停止导出遇到问题：{exc}"
                    _save(service, current)

    Thread(target=stop, daemon=True, name=f"cancel-{export_id}").start()
    return record


def _run_process(control: _ExportControl, args: list[str], *,
                 on_line: Callable[[str], None] | None = None,
                 env: dict[str, str] | None = None) -> str:
    with control.process_lock:
        control.check()
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, start_new_session=os.name == "posix")
        control.process = process
    logs: list[str] = []
    try:
        if process.stdout is None:
            raise RuntimeError("无法读取本地导出进度。")
        for line in process.stdout:
            logs.append(line)
            if on_line is not None:
                on_line(line)
        code = process.wait()
    finally:
        if control.cancelled.is_set() or process.poll() is None:
            _stop_process(control)
        with control.process_lock:
            if control.process is process:
                control.process = None
        if process.stdout is not None:
            process.stdout.close()
    control.check()
    output = "".join(logs)
    if code:
        raise ValueError(output[-1500:] or f"本地导出进程退出：{code}")
    return output


def _probe_export(control: _ExportControl, path: Path) -> dict[str, str]:
    output = _run_process(control, [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path), "-f", "null", "-"])
    size = re.search(r"Video:.*?\b(\d{2,5}x\d{2,5})\b", output)
    fps = re.search(r"\b(\d+(?:\.\d+)?) fps\b", output)
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", output)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else 0.0
    return {"size": size[1] if size else "unknown", "fps": fps[1] if fps else "0",
            "duration": f"{seconds:.3f}", "audio": "present" if "Audio:" in output else "missing"}


def inspect_export(service: ShortDramaService, project_id: str, export_id: str | None = None) -> dict[str, JsonValue]:
    """Read and decode the exact editorial export, independently of legacy manifests."""
    document = service.get_project(project_id).document
    if export_id is None:
        values = service.repository.list_studio_exports(project_id, limit=1, statuses=["completed"])
        if not values:
            raise ValueError("本集尚无已完成的剪辑导出。")
        export_id = values[0].id
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
        project = validate_editorial(service, project_id, cast(dict[str, JsonValue], saved["project"]), purpose="export")
        timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
        output_clips = [clip for clip in timeline.clips if editorial_clip_enabled(timeline, clip)]
        if not any(clip.type in {"video", "image", "text"} for clip in output_clips):
            raise ValueError("时间线尚无可导出的画面。")
        validate_visual_export(output_clips)
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
        for old in service.repository.list_studio_exports(project_id, statuses=["running", "cancelling"]):
            if old.status == "cancelling":
                raise StudioContentConflict("上一项导出正在停止，停止后可以重新导出。")
            if (old.editorial_revision == request.expected_revision
                    and old.width == width and old.height == height and old.fps == request.fps
                    and old.subtitles == request.subtitles):
                return old
            raise StudioContentConflict("另一份内容或尺寸的成片正在导出。完成后可以继续导出当前剪辑。")
        record = StudioExport(id=f"editorial-export-{uuid4().hex[:12]}", project_id=project_id,
            editorial_revision=request.expected_revision, width=width,
            height=height, fps=request.fps, subtitles=request.subtitles, created_at=datetime.now(UTC).isoformat())
        control = _ExportControl()
        with _ACTIVE_LOCK:
            _ACTIVE[_key(service, project_id, record.id)] = control
        _save(service, record)
    if background:
        Thread(target=_render, args=(service, project, request, record, control), daemon=True, name=record.id).start()
    else:
        _render(service, project, request, record, control)
    return record


def _timestamp(seconds: float, *, ass: bool = False) -> str:
    if ass:
        return f"{int(seconds // 3600)}:{int(seconds // 60 % 60):02}:{int(seconds % 60):02}.{int(seconds * 100) % 100:02}"
    millis = round(seconds * 1000)
    return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}"


def _write_subtitles(timeline: EditorTimeline, folder: Path, width: int, height: int) -> tuple[Path, Path]:
    srt, ass = folder / "subtitles.srt", folder / "subtitles.ass"
    # Same 1080px reference size as DEFAULT_SUBTITLE_STYLE in the monitor.
    default_font_size = 56
    confirmed_font = default_subtitle_font_family()
    default_font = resolved_subtitle_family(None, confirmed_font)
    has_solo_track = any(track.solo for track in timeline.tracks)
    visible_cues = [cue for cue in timeline.subtitles if timeline.tracks[cue.trackIndex].enabled
        and (not has_solo_track or timeline.tracks[cue.trackIndex].solo)]
    # SRT consumers expect chronological entries. Keep the saved/ASS order for
    # overlapping captions, whose render order may be intentional.
    srt_lines = [f"{index}\n{_timestamp(cue.startTime)} --> {_timestamp(cue.endTime)}\n{cue.text}\n"
        for index, cue in enumerate(sorted(visible_cues, key=lambda cue: cue.startTime), start=1)]
    ass_lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{default_font},{default_font_size * height / 1080},&H00FFFFFF,&H00FFFFFF,&H66000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,{width*.05:.0f},{width*.05:.0f},{height/36:.0f},1",
        f"Style: Box,{default_font},{default_font_size * height / 1080},&H00FFFFFF,&H00FFFFFF,&H66000000,&H00000000,0,0,0,0,100,100,0,0,3,4,0,2,{width*.05:.0f},{width*.05:.0f},{height/36:.0f},1",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for cue in visible_cues:
        track = timeline.tracks[cue.trackIndex]
        style = {**cast(dict[str, JsonValue], (track.model_extra or {}).get("subtitleStyle", {})), **cue.style}
        alignment = {"top": 8, "center": 5}.get(str(style.get("position")), 2)
        size_value = style.get("fontSize", default_font_size)
        size = float(size_value) if isinstance(size_value, (int, float)) else default_font_size
        color = str(style.get("color", "#FFFFFF")).removeprefix("#")[:6]
        color = color if len(color) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in color) else "FFFFFF"
        bgr = color[4:6] + color[2:4] + color[:2]
        font = resolved_subtitle_family(style.get("fontFamily"), confirmed_font)
        font = font.replace("{", "").replace("}", "").replace("\\", "")
        background = str(style.get("backgroundColor", "transparent")).removeprefix("#")
        box = len(background) in {6, 8} and all(ch in "0123456789abcdefABCDEF" for ch in background)
        extra = ""
        if box:
            bg = background[4:6] + background[2:4] + background[:2]
            alpha = 255 - int(background[6:8], 16) if len(background) == 8 else 0
            extra = f"\\3c&H{bg}&\\3a&H{alpha:02X}&\\bord{4*height/1080:.2f}"
        render_text = wrap_subtitle_for_export(cue.text, font, size * height / 1080, width * .9, bold=style.get("fontWeight") == "bold")
        text = render_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        tag = f"{{\\fn{font}{extra}\\an{alignment}\\fs{size * height / 1080:.2f}\\c&H{bgr}&\\b{1 if style.get('fontWeight') == 'bold' else 0}\\i{1 if style.get('italic') else 0}}}"
        style_name = "Box" if box else "Default"
        ass_lines.append(f"Dialogue: 0,{_timestamp(cue.startTime, ass=True)},{_timestamp(cue.endTime, ass=True)},{style_name},,0,0,0,,{tag}{text}")
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    ass.write_text("\n".join(ass_lines), encoding="utf-8")
    return srt, ass


def _render(service: ShortDramaService, project: EditorProject, request: StudioExportRequest,
            record: StudioExport, control: _ExportControl) -> None:
    folder = service.workspace_root / record.project_id / "editorial-exports" / record.id
    try:
        control.check()
        folder.mkdir(parents=True, exist_ok=True)
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
        logs: list[str] = []

        def on_line(line: str) -> None:
            logs.append(line)
            try:
                event: object = json.loads(line)
            except json.JSONDecodeError:
                return
            if isinstance(event, dict) and isinstance(cast(dict[str, object], event).get("progress"), str):
                record.progress = str(cast(dict[str, object], event)["progress"])
                _save(service, record)
        try:
            _run_process(control, [node, str(runtime), str(input_file)], on_line=on_line,
                env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"})
        finally:
            (folder / "export.log").write_text("".join(logs), encoding="utf-8")
        control.check()
        timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
        srt, ass = _write_subtitles(timeline, folder, record.width, record.height)
        final = clean
        if request.subtitles and timeline.subtitles:
            record.progress = "渲染字幕"
            _save(service, record)
            final = folder / "film-subtitled.mp4"
            escaped = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
            _run_process(control, [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(clean), "-vf", f"ass='{escaped}'", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(final)])
        has_solo = any(track.solo for track in timeline.tracks)
        record.duration_seconds = max((clip.startTime + clip.duration for clip in timeline.clips
            if timeline.tracks[clip.trackIndex].enabled and (not has_solo or timeline.tracks[clip.trackIndex].solo)), default=0)
        technical = _probe_export(control, final)
        checks = {"duration": abs(float(technical["duration"]) - record.duration_seconds) < max(0.15, 2 / record.fps),
                  "size": technical["size"] == f"{record.width}x{record.height}",
                  "fps": abs(float(technical["fps"]) - record.fps) < 0.02,
                  "audio_track": technical["audio"] == "present"}
        (folder / "technical-check.json").write_text(json.dumps({"expected_duration": record.duration_seconds, "media": technical, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not all(checks.values()):
            raise ValueError("导出文件检查未通过：" + ", ".join(name for name, passed in checks.items() if not passed))
        record.clean_video_path, record.video_path, record.srt_path = str(clean), str(final), str(srt)
        control.check()
        record.status, record.progress = "completed", "导出完成"
    except _ExportCancelled:
        record.status, record.progress, record.error = "cancelled", "导出已取消", None
        record.video_path = record.clean_video_path = record.srt_path = None
    except Exception as exc:
        record.status, record.progress, record.error = "failed", "导出未完成", str(exc)
    finally:
        try:
            _save(service, record)
        finally:
            control.done.set()
            with _ACTIVE_LOCK:
                key = _key(service, record.project_id, record.id)
                if _ACTIVE.get(key) is control:
                    _ACTIVE.pop(key)


def recover_interrupted_exports(service: ShortDramaService) -> None:
    """A stopped local render can be retried; never present it as still running."""
    for record in service.repository.list_studio_exports(None, statuses=["running", "cancelling"]):
        if _control(service, record.project_id, record.id) is not None:
            continue
        record.status, record.progress = "failed", "上次导出已中断"
        record.error = "应用在导出完成前退出。当前剪辑已保存，可重新导出。"
        _save(service, record)
