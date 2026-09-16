"""Supported visual controls reject missing render paths and export real titles."""

from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorClip, EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from short_drama.studio_visual_export import validate_visual_export


def _clip(identity: str, start: float = 0, **extra: object) -> EditorClip:
    return EditorClip.model_validate({"id": identity, "assetId": None, "type": "video", "startTime": start,
        "duration": 2, "trimStart": 0, "trackIndex": 0, **extra})


def _style() -> dict[str, JsonValue]:
    return {"text": "借路\n第二集", "fontFamily": "Arial", "fontSize": 108, "fontWeight": "bold", "fontStyle": "normal",
        "color": "#FFFF00", "backgroundColor": "transparent", "textAlign": "center", "positionX": 50, "positionY": 50,
        "strokeColor": "transparent", "strokeWidth": 0, "shadowColor": "transparent", "shadowBlur": 0,
        "shadowOffsetX": 0, "shadowOffsetY": 0, "letterSpacing": 0, "lineHeight": 1.2, "maxWidth": 0,
        "padding": 0, "borderRadius": 0, "opacity": 100}


def test_accepts_title_and_paired_basic_dissolve_without_changing_timing() -> None:
    first = _clip("first", transitionIn={"type": "fade-to-black", "duration": 0.5}, transitionOut={"type": "dissolve", "duration": 0.5})
    second = _clip("second", 2, transitionIn={"type": "dissolve", "duration": 0.5}, transitionOut={"type": "fade-to-black", "duration": 0.5})
    title = _clip("title", 1, type="text", trackIndex=1, textStyle=_style())
    validate_visual_export([first, second, title])
    assert [(clip.startTime, clip.duration) for clip in (first, second, title)] == [(0, 2), (2, 2), (1, 2)]


def test_rejects_unimplemented_effects_and_unmatched_or_overlapping_dissolves() -> None:
    with pytest.raises(ValueError, match="只支持"):
        validate_visual_export([_clip("wipe", transitionOut={"type": "wipe-left", "duration": 0.5})])
    with pytest.raises(ValueError, match="调整层"):
        validate_visual_export([_clip("adjustment", type="adjustment")])
    with pytest.raises(ValueError, match="样式"):
        validate_visual_export([_clip("title", type="text", textStyle={**_style(), "shadowBlur": 4})])
    with pytest.raises(ValueError, match="匹配"):
        validate_visual_export([_clip("first", transitionOut={"type": "dissolve", "duration": 0.5})])
    first = _clip("first", transitionOut={"type": "dissolve", "duration": 0.5})
    second = _clip("second", 2, transitionIn={"type": "dissolve", "duration": 0.5})
    with pytest.raises(ValueError, match="其他画面"):
        validate_visual_export([first, second, _clip("overlay", 1, trackIndex=1)])


def test_title_only_current_timeline_exports_with_existing_ffmpeg_runtime(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    project_id = service.initialize_fixture().document.project.id
    project = EditorProject.model_validate(read_editorial(service, project_id).project)
    project.timelines[0].clips = [_clip("title", type="text", textStyle=_style(), duration=1)]
    project.timelines[0].subtitles = []
    saved = service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=0,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    revision = cast(int, (saved.project.document.editorial_timeline or {})["revision"])
    result = start_export(service, project_id, StudioExportRequest(expected_revision=revision,
        width=320, height=320, fps=25, subtitles=False), background=False)
    assert result.status == "completed", result.error
    assert result.video_path and Path(result.video_path).stat().st_size > 500
    assert result.duration_seconds == 1
