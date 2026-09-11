"""Real local output decoding and exact saved-request aspect checks, no Provider IO."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import subprocess

import imageio_ffmpeg
from PIL import Image
import pytest

from short_drama.h3_models import (
    H3ContentItem, H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource,
    H3RemoteStatus, H3RemoteTask, H3TaskContent, H3UrlValue, H3VideoRequest,
)
from short_drama.h3_output_validation import expected_output_aspect
from short_drama.h3_worker import PersistentH3Worker
from short_drama.models import TaskStatus
from tests.test_h3_generation_unit_worker import _harness


def _request(ratio: str | None, resolution: str = "768P") -> H3VideoRequest:
    return H3VideoRequest(
        content=[H3ContentItem(type="text", text="A static room.")],
        resolution=resolution, duration=4, ratio=ratio,
    )


def _data_image(size: tuple[int, int]) -> str:
    image = Image.new("RGB", size, "blue")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


@pytest.mark.parametrize("size,resolution,ratio,passed", [
    ((2560, 1440), "2K", "16:9", True),
    ((1792, 768), "768P", "21:9", True),
    ((1440, 2560), "2K", "9:16", True),
    ((768, 768), "768P", "9:16", False),
])
def test_download_checks_actual_requested_aspect_without_guessed_resolution_pixels(
    tmp_path: Path, size: tuple[int, int], resolution: str, ratio: str, passed: bool,
) -> None:
    harness = _harness(tmp_path)
    task, run = harness.task, harness.run
    task.resolution = resolution
    task.requested_duration_seconds = 4
    task.shot_windows = []
    run.shot_windows = []
    run.request = _request(ratio, resolution)
    harness.repository.save_task(task)
    harness.repository.save_provider_run(run)
    path = tmp_path / "source.mp4"
    rendered = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s={size[0]}x{size[1]}:r=12:d=4",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    harness.client.output_source = path
    assert task.provider_task_id is not None
    harness.client.remote_tasks[task.provider_task_id] = H3RemoteTask(
        id=task.provider_task_id, status=H3RemoteStatus.SUCCEEDED,
        content=H3TaskContent(url="fake://source.mp4"), resolution=resolution,
        ratio=ratio, duration=4,
    )
    assert harness.worker.advance(task.id).status == TaskStatus.SUCCEEDED
    take = harness.repository.get_take(f"take-{task.id}")
    evidence = harness.repository.get_route_evidence_for_task(task.id)
    assert take is not None and evidence is not None
    assert (take.width, take.height) == size
    assert evidence.technical_passed is passed
    assert any(f"resolution={resolution}; exact pixel mapping=UNVERIFIED" in note for note in take.qa_report.checks)
    assert {issue.code for issue in take.qa_report.issues} == (set() if passed else {"H3_ASPECT_RATIO"})
    saved_run = harness.repository.get_provider_run(task.id)
    assert saved_run is not None
    if resolution != run.pricing.billing_resolution:
        assert saved_run.estimated_cost_usd is None
        assert saved_run.estimated_cost_credits is None
        assert saved_run.latest_remote_task is not None
    else:
        assert saved_run.estimated_cost_credits is not None


def test_adaptive_prefers_exact_uploaded_pixels_over_local_source(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    source = tmp_path / "local.png"
    Image.new("RGB", (100, 300)).save(source)
    harness.run.media_inputs = [H3MediaInput(
        kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(local_path=str(source)),
    )]
    harness.run.request = _request("adaptive")
    harness.run.request.content.append(H3ContentItem(
        type="image_url", role=H3MediaRole.FIRST_FRAME,
        image_url=H3UrlValue(url=_data_image((600, 400))),
    ))
    value, note = expected_output_aspect(harness.run)
    assert value == 1.5
    assert "600x400" in note


def test_adaptive_local_boundary_uses_visible_exif_orientation(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (300, 200))
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)
    harness.run.media_inputs = [H3MediaInput(
        kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(local_path=str(source)),
    )]
    harness.run.request = _request(None)
    harness.run.request.content.append(H3ContentItem(
        type="image_url", role=H3MediaRole.FIRST_FRAME,
        image_url=H3UrlValue(url="mm_file://existing-frame"),
    ))
    value, note = expected_output_aspect(harness.run)
    assert value == 2 / 3
    assert "200x300" in note


@pytest.mark.parametrize("ratio", [None, "adaptive"])
def test_adaptive_unknown_boundary_is_not_guessed_as_portrait(tmp_path: Path, ratio: str | None) -> None:
    harness = _harness(tmp_path)
    harness.run.request = _request(ratio)
    harness.run.request.content.append(H3ContentItem(
        type="image_url", role=H3MediaRole.FIRST_FRAME,
        image_url=H3UrlValue(url="https://example.invalid/not-fetched.png"),
    ))
    expected, note = expected_output_aspect(harness.run)
    assert expected is None
    assert "UNVERIFIED" in note
    remote = H3RemoteTask(id="candidate", status=H3RemoteStatus.QUEUED,
                          model=harness.task.model, resolution=harness.task.resolution, ratio="16:9")
    assert PersistentH3Worker._reconciliation_mismatch_reason(harness.task, harness.run, remote) is None


def test_reconciliation_rejects_known_different_ratio_but_accepts_adaptive_echo(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    harness.run.request = _request("21:9")
    remote = H3RemoteTask(id="candidate", status=H3RemoteStatus.QUEUED,
                          model=harness.task.model, resolution=harness.task.resolution, ratio="9:16")
    assert PersistentH3Worker._reconciliation_mismatch_reason(harness.task, harness.run, remote) is not None
    remote.ratio = "adaptive"
    assert PersistentH3Worker._reconciliation_mismatch_reason(harness.task, harness.run, remote) is None


def test_adaptive_conflicting_boundaries_do_not_invent_output_ratio(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    harness.run.request = _request("adaptive")
    for role, size in [(H3MediaRole.FIRST_FRAME, (600, 400)), (H3MediaRole.LAST_FRAME, (400, 600))]:
        harness.run.request.content.append(H3ContentItem(type="image_url", role=role, image_url=H3UrlValue(url=_data_image(size))))
    value, note = expected_output_aspect(harness.run)
    assert value is None
    assert "different aspect ratios" in note
