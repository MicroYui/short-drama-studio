"""Actual Chinese subtitle output remains readable without changing saved styles."""
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
import numpy as np
from pydantic import JsonValue

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorClip, EditorProject, EditorSubtitle, EditorTrack, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave


def test_default_and_explicit_chinese_subtitles_render_at_their_reference_size(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    current = read_editorial(service, pid)
    project = EditorProject.model_validate(current.project)
    timeline = project.timelines[0]
    # A real locally rendered black title provides a neutral measurement canvas.
    timeline.clips = [EditorClip.model_validate({"id": "black-background", "assetId": None,
        "type": "text", "startTime": 0, "duration": 4, "trimStart": 0, "trackIndex": 0,
        "textStyle": {"text": ".", "fontFamily": "Arial", "fontSize": 1, "fontWeight": "normal",
            "fontStyle": "normal", "color": "#000000", "backgroundColor": "transparent",
            "textAlign": "center", "positionX": 50, "positionY": 50, "strokeColor": "transparent",
            "strokeWidth": 0, "shadowColor": "transparent", "shadowBlur": 0, "shadowOffsetX": 0,
            "shadowOffsetY": 0, "letterSpacing": 0, "lineHeight": 1.2, "maxWidth": 0,
            "padding": 0, "borderRadius": 0, "opacity": 100}})]
    timeline.tracks.append(EditorTrack.model_validate({"id": "explicit-subtitle-track", "name": "已设置字幕",
        "type": "subtitle", "kind": "video", "subtitleStyle": {"fontSize": 48, "color": "#FFFF00"}}))
    explicit_track = len(timeline.tracks)-1
    text = "你不是说，今天不来了？"
    timeline.subtitles = [
        EditorSubtitle(id="default", text=text, startTime=0, endTime=1, trackIndex=3),
        EditorSubtitle(id="saved-small", text=text, startTime=1, endTime=2, trackIndex=3, style={"fontSize": 32}),
        EditorSubtitle(id="track-size", text=text, startTime=2, endTime=3, trackIndex=explicit_track),
        EditorSubtitle(id="cue-override", text=text, startTime=3, endTime=4, trackIndex=explicit_track,
            style={"fontSize": 72, "color": "#00FF00"}),
    ]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    saved = read_editorial(service, pid)
    result = start_export(service, pid, StudioExportRequest(expected_revision=saved.revision,
        width=1536, height=864, fps=24), background=False)
    assert result.status == "completed", result.error
    assert result.video_path and result.srt_path
    output = Path(result.video_path)
    # Decode actual encoded frames at four distinct cue times, not a filter string.
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(output),
        "-vf", "select=eq(n\\,12)+eq(n\\,36)+eq(n\\,60)+eq(n\\,84)", "-fps_mode", "passthrough",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], check=True, capture_output=True).stdout
    frames = np.frombuffer(decoded, dtype=np.uint8).reshape((4, 864, 1536, 3))
    bounds: list[tuple[int, int]] = []
    for frame in frames:
        rows, columns = np.where(np.max(frame, axis=2) > 100)
        assert rows.size and columns.size
        bounds.append((int(columns.max()-columns.min()+1), int(rows.max()-rows.min()+1)))
    default, small, track, override = bounds
    assert default[1] >= 27, bounds  # at least ~11px glyph height in a 640px player
    assert default[0] > small[0]*1.55, bounds
    assert small[0]*1.35 < track[0] < default[0], bounds
    assert override[0] > track[0]*1.4, bounds
    yellow = frames[2][np.max(frames[2], axis=2) > 100].mean(axis=0)
    green = frames[3][np.max(frames[3], axis=2) > 100].mean(axis=0)
    assert yellow[0] > 150 and yellow[1] > 150 and yellow[2] < 60
    assert green[1] > 150 and green[0] < 60 and green[2] < 60
    assert read_editorial(service, pid) == saved
    assert Path(result.srt_path).read_text().count(text) == 4
