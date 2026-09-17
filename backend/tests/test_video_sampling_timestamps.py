"""Adaptive frames must describe the pixels at their recorded source PTS."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
from typing import cast

import imageio_ffmpeg
from PIL import Image, ImageChops
from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.media import MediaGenerationError
from short_drama.models import VideoInspectionReport
from short_drama.production_manager import (
    _compact_specialist_tool_result,
    _specialist_tool_result_for_persistence,
)
from short_drama.repository import ShortDramaRepository
from short_drama.video_inspection import VideoInspector, _sample_time_label


def _render_source(path: Path, *, fps: int, duration: float, cut_at: float | None = None) -> None:
    content = "testsrc2" if cut_at is None else "color=c=red"
    source = f"{content}:s=360x206:r={fps}:d={duration}" if cut_at is not None else f"{content}=s=360x206:r={fps}:d={duration}"
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source]
    if cut_at is not None:
        command += ["-vf", f"drawbox=c=blue:t=fill:enable='gte(t,{cut_at})'"]
    result = subprocess.run(command + ["-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "0", "-pix_fmt", "yuv420p", str(path)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_adaptive_cut_samples_use_frame_pts_instead_of_resampled_grid(tmp_path: Path) -> None:
    source = tmp_path / "cut.mp4"
    _render_source(source, fps=24, duration=9.42, cut_at=4.75)
    inspector = VideoInspector(tmp_path / "workspace")
    samples = inspector.sample_video(media_path=source, take_id="take-cut", probe=inspector.probe_video(source),
        output_dir=tmp_path / "samples", max_frames=24, source_out_seconds=9.42)
    assert len(samples) == 24 and all(frame.timestamp_is_source_pts for frame in samples)
    assert len({frame.timestamp_seconds for frame in samples}) == len(samples)
    assert samples[0].timestamp_seconds == 0 and samples[-1].timestamp_seconds > 9
    for frame in samples:
        with Image.open(frame.image_path) as picture:
            red, _green, blue = picture.convert("RGB").getpixel((180, 100))
        assert (blue > red) == (frame.timestamp_seconds >= 4.75)
    first_after_cut = next(frame for frame in samples if frame.timestamp_seconds >= 4.75)
    assert first_after_cut.timestamp_seconds == 4.75
    # The old fps/index implementation labelled this first blue sample 4.710s.
    assert first_after_cut.requested_timestamp_seconds == pytest.approx(4.71)
    assert "PTS" in _sample_time_label(first_after_cut)


@pytest.mark.parametrize("fps", [24, 25])
def test_nonzero_adaptive_window_pixels_match_each_recorded_source_frame(tmp_path: Path, fps: int) -> None:
    source = tmp_path / "source.mp4"
    _render_source(source, fps=fps, duration=4)
    inspector = VideoInspector(tmp_path / "workspace")
    samples = inspector.sample_video(media_path=source, take_id="take-offset", probe=inspector.probe_video(source),
        output_dir=tmp_path / "samples", max_frames=24, source_in_seconds=1.13, source_out_seconds=2.13)
    assert len(samples) == 8
    assert all(frame.timestamp_is_source_pts and 1.13 <= frame.timestamp_seconds < 2.13 for frame in samples)
    source_indices = [round(frame.timestamp_seconds * fps) for frame in samples]
    assert len(set(source_indices)) == len(samples)
    assert all(frame.timestamp_seconds == pytest.approx(index / fps, abs=0.000006)
        for frame, index in zip(samples, source_indices, strict=True))
    reference = tmp_path / "reference"
    reference.mkdir()
    selection = "+".join(f"eq(n\\,{index})" for index in source_indices)
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-an", "-vf", f"select={selection}", "-fps_mode", "vfr",
        str(reference / "frame-%03d.png")], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    for sample, expected_path in zip(samples, sorted(reference.glob("*.png")), strict=True):
        with Image.open(sample.image_path) as actual, Image.open(expected_path) as expected:
            assert ImageChops.difference(actual.convert("RGB"), expected.convert("RGB")).getbbox() is None


def test_adaptive_single_frame_and_empty_windows_do_not_invent_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "slow.mp4"
    _render_source(source, fps=1, duration=4)
    inspector = VideoInspector(tmp_path / "workspace")
    probe = inspector.probe_video(source)
    samples = inspector.sample_video(media_path=source, take_id="take-slow", probe=probe,
        output_dir=tmp_path / "single", max_frames=12, source_in_seconds=0.13, source_out_seconds=1.13)
    assert len(samples) == 1
    assert samples[0].timestamp_seconds == 1 and samples[0].timestamp_is_source_pts
    with pytest.raises(MediaGenerationError, match="No timestamped source frame"):
        inspector.sample_video(media_path=source, take_id="take-slow", probe=probe,
            output_dir=tmp_path / "empty", max_frames=12, source_in_seconds=0.13, source_out_seconds=0.8)
    assert not list((tmp_path / "empty").glob("*.png"))


def test_legacy_and_mixed_report_times_remain_typed_through_reopen_and_both_context_views(tmp_path: Path) -> None:
    repository = ShortDramaRepository(tmp_path / "state.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-16T00:00:00Z")
    repository.save_project(document)
    frame_path = tmp_path / "historical-frame.png"
    Image.new("RGB", (360, 206), "blue").save(frame_path)
    historical_sheet = tmp_path / "historical-sheet.jpg"
    Image.new("RGB", (252, 522), "white").save(historical_sheet)
    sheet_before = historical_sheet.read_bytes()
    report = VideoInspectionReport.model_validate({
        "id": "legacy-mixed-inspection", "project_id": document.project.id,
        "shot_id": document.shots[0].id, "take_id": "take-current", "previous_take_id": "take-previous",
        "scopes": ["INTRA_SHOT", "SHOT_BOUNDARY"], "duration_seconds": 9.42,
        "width": 360, "height": 206, "fps": 24, "audio_present": False,
        "samples": [
            {"source_take_id": "take-current", "timestamp_seconds": 4.71,
             "image_path": str(frame_path), "reasons": ["UNIFORM_COVERAGE"]},
            {"source_take_id": "take-current", "timestamp_seconds": 4.75,
             "timestamp_is_source_pts": True, "requested_timestamp_seconds": 4.71,
             "image_path": str(frame_path), "reasons": ["VISUAL_CHANGE"]},
            {"source_take_id": "take-previous", "timestamp_seconds": 8.999,
             "image_path": str(frame_path), "reasons": ["BOUNDARY_END"]},
        ],
        "contact_sheet_path": str(historical_sheet), "technical_checks": [],
        "semantic_status": "COMPLETE", "summary": "Historical evidence is still readable; audio unknown.",
        "created_at": "2026-09-16T00:00:00Z",
    })
    repository.save_video_inspection(report)
    legacy_payload = report.model_dump(mode="json")
    # Persist the actual older JSON shape; a read must not rewrite or promote it.
    del legacy_payload["samples"][0]["timestamp_is_source_pts"]
    del legacy_payload["samples"][2]["timestamp_is_source_pts"]
    original = json.dumps(legacy_payload)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE video_inspections SET record_json=? WHERE id=?", (original, report.id))
    reopened_repository = ShortDramaRepository(repository.database_path)
    reopened = reopened_repository.get_video_inspection(report.id)
    assert reopened is not None and reopened == report
    assert _sample_time_label(reopened.samples[0]) == "~4.710s nominal"
    assert _sample_time_label(reopened.samples[1]) == "4.750s PTS"
    result: dict[str, JsonValue] = {"tool_id": "video.get_take_inspection", "status": "SUCCEEDED",
        "output": {"report": cast(JsonValue, reopened.model_dump(mode="json"))}}
    persisted = _specialist_tool_result_for_persistence("video.get_take_inspection", result)
    worker = _compact_specialist_tool_result("video.get_take_inspection", persisted)
    worker_output = worker["output"]
    assert isinstance(worker_output, dict)
    worker_report = worker_output["report"]
    summary = ProjectArtifactResolver(reopened_repository).summarize_artifact(ArtifactRef(
        project_id=document.project.id, artifact_type="video_inspection", artifact_id=report.id))
    assert summary is not None
    for view in (worker_report, json.loads(summary.summary)):
        assert isinstance(view, dict)
        assert view["sample_timestamps_by_take"] == {"take-current": [4.71, 4.75], "take-previous": [8.999]}
        assert view["sample_timestamp_is_source_pts_by_take"] == {"take-current": [False, True], "take-previous": [False]}
        assert "not an exact source frame or cut time" in str(view["sample_timestamp_guidance"])
        assert "samples" not in view and view["semantic_status"] == "COMPLETE"
    assert persisted == result
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT record_json FROM video_inspections WHERE id=?", (report.id,)).fetchone()[0] == original
    assert historical_sheet.read_bytes() == sheet_before
    VideoInspector._build_contact_sheet(reopened.samples, tmp_path / "new-sheet.jpg", title="Mixed time types")
    assert (tmp_path / "new-sheet.jpg").is_file()
