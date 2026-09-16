"""Rendered-file pixels reach Worker decisions without touching project content."""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from PIL import Image
import pytest

from short_drama.agent_runtime.visual_input import prepare_worker_visual_input
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioExport
from short_drama.tools import ToolCallStatus
from tests.test_worker_visual_observations import _save_and_reopen


def export_fixture(tmp_path: Path) -> tuple[ShortDramaService, StudioExport]:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    for variant in ["clean", "subtitled"]:
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=24:d=1"]
        if variant == "subtitled":
            command += ["-vf", "drawbox=x=40:y=160:w=240:h=50:color=red:t=fill"]
        subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / f"{variant}.mp4")],
            check=True, capture_output=True)
    record = StudioExport(id="export-pixels", project_id=document.project.id, editorial_revision=1, status="completed",
        video_path=str(tmp_path / "subtitled.mp4"), clean_video_path=str(tmp_path / "clean.mp4"),
        width=320, height=240, fps=24, duration_seconds=1, created_at="2026-09-16T00:00:00+00:00")
    service.repository.save_studio_export(record)
    return service, record


def test_exact_export_variant_pixels_and_timestamps_reach_worker_after_reopen(tmp_path: Path) -> None:
    service, record = export_fixture(tmp_path)
    original = service.get_project(record.project_id).document.model_dump(mode="json")
    before_files = {path: Path(path).read_bytes() for path in [record.video_path, record.clean_video_path] if path}
    arguments = {"project_id": record.project_id, "export_id": record.id, "frame_times_seconds": [0.125, 0.99]}
    result = service.tool_registry.invoke("timeline.read_export", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    content = result.output["content"]
    assert isinstance(content, dict)
    rendered = content["rendered_frames"]
    assert isinstance(rendered, dict)
    assert rendered["time_space"] == "EXPORT_FILM" and rendered["export_id"] == record.id
    assert rendered["editorial_revision"] == 1 and not rendered["matches_editorial_revision_at_read"]
    assert not rendered["audio_semantics_observed"] and rendered["variant"] == "subtitled"
    assert rendered["frames"][0]["actual_pts_seconds"] == pytest.approx(0.125)
    assert rendered["frames"][1]["actual_pts_seconds"] == pytest.approx(23 / 24, abs=1e-5)
    assert len(result.visual_attachments) == 2
    reopened, view = _save_and_reopen(service, record.project_id, result, arguments)
    prepared = prepare_worker_visual_input(view.work_order, project_id=record.project_id)
    assert prepared is not None and len(prepared.image_urls) == 2
    for url in prepared.image_urls:
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as pixels:
            r, _g, b = cast(tuple[int, int, int], pixels.convert("RGB").getpixel((100, 180)))
            assert r > 200 and b < 20  # Actual composited file, not its blue clean/source image.
    clean = reopened.tool_registry.invoke("timeline.read_export", {**arguments, "frame_times_seconds": [0.125], "frame_variant": "clean"})
    assert clean.status == ToolCallStatus.SUCCEEDED, clean.error
    assert clean.visual_attachments[0].local_path is not None
    with Image.open(clean.visual_attachments[0].local_path) as pixels:
        r, _g, b = cast(tuple[int, int, int], pixels.convert("RGB").getpixel((100, 180)))
        assert b > 200 and r < 20
    assert reopened.get_project(record.project_id).document.model_dump(mode="json") == original
    assert {path: Path(path).read_bytes() for path in before_files} == before_files
    metadata_only = reopened.tool_registry.invoke("timeline.read_export", {"project_id": record.project_id, "export_id": record.id})
    assert metadata_only.status == ToolCallStatus.SUCCEEDED and metadata_only.visual_attachments == []


def test_export_frame_selection_rejects_invalid_times_and_never_falls_back_to_another_export(tmp_path: Path) -> None:
    service, record = export_fixture(tmp_path)
    base = {"project_id": record.project_id, "export_id": record.id}
    for times, expected in [([-0.1], ToolCallStatus.INVALID_ARGUMENTS), ([1], ToolCallStatus.FAILED),
        ([1.1], ToolCallStatus.FAILED), ([float("nan")], ToolCallStatus.INVALID_ARGUMENTS),
        ([0, 0.1, 0.2, 0.3, 0.4], ToolCallStatus.INVALID_ARGUMENTS)]:
        result = service.tool_registry.invoke("timeline.read_export", {**base, "frame_times_seconds": times})
        assert result.status == expected
        assert not result.visual_attachments
    assert not list(service.workspace_root.glob("*/inspections/export-frames-*"))
    missing = service.tool_registry.invoke("timeline.read_export", {**base, "export_id": "missing", "frame_times_seconds": [0]})
    assert missing.status == ToolCallStatus.FAILED and not missing.visual_attachments
    assert record.video_path is not None
    Path(record.video_path).unlink()
    missing_file = service.tool_registry.invoke("timeline.read_export", {**base, "frame_times_seconds": [0]})
    assert missing_file.status == ToolCallStatus.FAILED and not missing_file.visual_attachments


def test_frame_selection_uses_actual_file_duration_when_editorial_end_is_between_frames(tmp_path: Path) -> None:
    service, record = export_fixture(tmp_path)
    base = {"project_id": record.project_id, "export_id": record.id}
    service.repository.save_studio_export(record.model_copy(update={"duration_seconds": 0.98}))
    available_tail = service.tool_registry.invoke("timeline.read_export", {**base, "frame_times_seconds": [0.99]})
    assert available_tail.status == ToolCallStatus.SUCCEEDED, available_tail.error
    assert available_tail.visual_attachments
    service.repository.save_studio_export(record.model_copy(update={"duration_seconds": 1.04}))
    beyond_file = service.tool_registry.invoke("timeline.read_export", {**base, "frame_times_seconds": [1.01]})
    assert beyond_file.status == ToolCallStatus.FAILED and not beyond_file.visual_attachments
