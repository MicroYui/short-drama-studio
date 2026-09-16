"""Real local frame timestamps, detail pixels and aligned source audio excerpts."""
from __future__ import annotations

import array
import json
from pathlib import Path
import subprocess
import wave

import imageio_ffmpeg
from PIL import Image, ImageChops
from pydantic import ValidationError
import pytest

from short_drama.models import EnqueueRequest, VideoInspectionRequest
from short_drama.media import MediaGenerationError
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService
from short_drama.studio_media import resolve_media_path
from short_drama.video_detail import extract_detail_frames, extract_source_audio, sample_dense_window
from short_drama.video_inspection import inspection_matches_request
from tests.test_video_inspection import FakeInspectionLanguageProvider


def render_source(path: Path, *, fps: int = 24, audio_offset: float = 0) -> bytes:
    samples = array.array("h", [0] * 4 * 48000 * 2)
    for start, channel in ((1.37, 0), (1.87, 1)):
        for frame in range(round(start * 48000), round(start * 48000) + 480):
            samples[frame * 2 + channel] = 12000
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s=768x432:r={fps}:d=4",
        "-itsoffset", str(audio_offset),
        "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(path)],
        input=samples.tobytes(), capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return samples.tobytes()


def test_audio_stream_start_offset_does_not_shift_source_time(tmp_path: Path) -> None:
    source = tmp_path / "offset.mov"
    expected = render_source(source, audio_offset=0.4)
    result = extract_source_audio(source=source, destination=tmp_path / "excerpt.wav", start=0.13, end=2.13)
    with wave.open(result.audio_path, "rb") as excerpt:
        actual = excerpt.readframes(excerpt.getnframes())
    padded = bytes(round(0.4 * 48000) * 4) + expected
    assert actual == padded[round(0.13 * 48000) * 4:round(2.13 * 48000) * 4]
    assert result.source_in_seconds == 0.13 and result.source_out_seconds == pytest.approx(2.13)


def test_dense_frames_record_actual_pts_without_duplicates_and_detail_preserves_pixels(tmp_path: Path) -> None:
    source = tmp_path / "source.mov"
    render_source(source)
    frames = sample_dense_window(source=source, take_id="take", output_dir=tmp_path / "dense", start=1.13, end=2.13, fps=12)
    assert len(frames) == 12
    assert len({frame.timestamp_seconds for frame in frames}) == 12
    for index, frame in enumerate(frames):
        requested = 1.13 + index / 12
        actual = frame.timestamp_seconds
        assert frame.timestamp_is_source_pts
        assert frame.requested_timestamp_seconds == pytest.approx(requested)
        assert requested <= actual < requested + 1 / 24 + 0.00001
        assert actual * 24 == pytest.approx(round(actual * 24), abs=0.0002)
        assert frame.width == 360 and frame.height is not None and frame.height < 360
    details = extract_detail_frames(source=source, take_id="take", output_dir=tmp_path / "details",
        times=[1.131, 2.125], start=1.13, end=2.13, source_fps=24, max_dimension=None)
    assert [(frame.width, frame.height) for frame in details] == [(768, 432), (768, 432)]
    assert details[0].timestamp_seconds == pytest.approx(28 / 24, abs=0.00001)
    assert details[1].timestamp_seconds < 2.13
    reference = tmp_path / "reference.png"
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(source), "-vf", "select=eq(n\\,28)",
        "-frames:v", "1", str(reference)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    with Image.open(reference) as expected, Image.open(details[0].image_path) as actual_image:
        assert ImageChops.difference(expected.convert("RGB"), actual_image.convert("RGB")).getbbox() is None
    resized = extract_detail_frames(source=source, take_id="take", output_dir=tmp_path / "resized", times=[1.131],
        start=1.13, end=2.13, source_fps=24, max_dimension=640)
    assert (resized[0].width, resized[0].height) == (640, 360)


def test_dense_sampling_caps_at_36_and_does_not_invent_frames_for_slow_sources(tmp_path: Path) -> None:
    source = tmp_path / "fast.mov"
    render_source(source)
    frames = sample_dense_window(source=source, take_id="take", output_dir=tmp_path / "fast", start=0, end=3, fps=12)
    assert len(frames) == 36
    slow = tmp_path / "slow.mov"
    render_source(slow, fps=2)
    frames = sample_dense_window(source=slow, take_id="slow-take", output_dir=tmp_path / "slow", start=0, end=3, fps=12)
    assert [frame.timestamp_seconds for frame in frames] == [0, 0.5, 1, 1.5, 2, 2.5]


def render_tail_source(path: Path) -> None:
    # 243 real video frames end at PTS 242/24; audio extends the container to
    # 10.13 seconds. The requested 10.125 point has no following video frame.
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=768x432:r=24:d=10.125",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10.13",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(path)],
        capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_inspection_container_tail_uses_actual_last_frame_and_preserves_complete_request(tmp_path: Path) -> None:
    fake = FakeInspectionLanguageProvider(['{"observations":[],"issues":[],"summary":"Visible frames only."}'])
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: fake)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    document = service.initialize_fixture().document
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = service.drain(document.project.id, 50).project.takes[0]
    source = tmp_path / "tail.mov"
    render_tail_source(source)
    take.media_path, take.duration_seconds = str(source), 10.13
    service.repository.save_take(take)
    request = VideoInspectionRequest(source_in_seconds=7.2, source_out_seconds=10.13, dense_sample_fps=12,
        keyframe_times_seconds=[9.583333, 9.916667, 10.041667, 10.125], keyframe_max_dimension=None,
        include_audio_excerpt=True, include_previous_boundary=False)
    report = service.inspect_take_video(take.id, request).report
    assert report.duration_seconds == 10.13 and len(report.samples) == 35 and len(report.detail_frames) == 4
    assert report.observed_source_in_seconds == 7.2 and report.observed_source_out_seconds == 10.13
    final_frame = report.detail_frames[-1]
    assert final_frame.requested_timestamp_seconds == 10.125
    assert final_frame.timestamp_seconds == pytest.approx(242 / 24, abs=1e-9)
    assert final_frame.timestamp_is_source_pts and final_frame.reasons == ["DETAIL_KEYFRAME_LAST_AVAILABLE"]
    reference = tmp_path / "last-real-frame.png"
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(source),
        "-vf", "select=eq(n\\,242)", "-frames:v", "1", str(reference)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    with Image.open(reference) as expected, Image.open(final_frame.image_path) as actual:
        assert ImageChops.difference(expected.convert("RGB"), actual.convert("RGB")).getbbox() is None
    assert report.audio_excerpt is not None and report.audio_excerpt.source_out_seconds == pytest.approx(10.13)
    assert not report.audio_semantics_observed and not report.full_take_coverage
    assert len(fake.generations) == 1
    assert service.get_take_video_inspection(take.id).report == report
    # An actual source-range overrun remains an error before any model call.
    with pytest.raises(ValueError, match="within the available Take"):
        service.inspect_take_video(take.id, request.model_copy(update={"source_out_seconds": 10.3}))
    assert len(fake.generations) == 1


def test_detail_tail_fallback_does_not_invent_a_frame_in_empty_or_invalid_scope(tmp_path: Path) -> None:
    source = tmp_path / "tail.mov"
    render_tail_source(source)
    with pytest.raises(MediaGenerationError, match="No timestamped source frame"):
        extract_detail_frames(source=source, take_id="take", output_dir=tmp_path / "empty",
            times=[10.125], start=10.09, end=10.13, source_fps=24, max_dimension=360)
    assert not list((tmp_path / "empty").glob("*.png"))
    for start, end, point in [(7.2, 10.13, 10.13), (7.2, 10.13, 7.19), (10.13, 10.13, 10.13)]:
        with pytest.raises(ValueError, match="inside the actual source interval"):
            extract_detail_frames(source=source, take_id="take", output_dir=tmp_path / "invalid",
                times=[point], start=start, end=end, source_fps=24, max_dimension=360)


def test_inspection_persists_detail_and_exact_audio_scope_without_claiming_listening(tmp_path: Path) -> None:
    fake = FakeInspectionLanguageProvider(['{"observations":[],"issues":[],"summary":"Only visible details observed."}'])
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: fake)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    document = service.initialize_fixture().document
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[document.shots[0].id], candidate_count=1))
    take = service.drain(document.project.id, 50).project.takes[0]
    source = tmp_path / "source.mov"
    expected = render_source(source)
    take.media_path, take.duration_seconds = str(source), 4
    service.repository.save_take(take)
    request = VideoInspectionRequest(source_in_seconds=1.13, source_out_seconds=2.13,
        dense_sample_fps=12, keyframe_times_seconds=[1.131], keyframe_max_dimension=None,
        include_audio_excerpt=True, include_previous_boundary=False)
    report = service.inspect_take_video(take.id, request).report
    assert not report.audio_semantics_observed and not report.full_take_coverage
    assert report.dense_sample_fps == 12 and len(report.samples) == 12
    assert report.audio_excerpt is not None
    audio = report.audio_excerpt
    assert audio.source_in_seconds == 1.13 and audio.source_out_seconds == pytest.approx(2.13)
    assert audio.duration_seconds == 1 and audio.sample_rate == 48000 and audio.channels == 2
    with wave.open(audio.audio_path, "rb") as extracted:
        actual = extracted.readframes(extracted.getnframes())
    assert actual == expected[round(1.13 * 48000) * 4:round(2.13 * 48000) * 4]
    assert resolve_media_path(service, audio.audio_path, document.project.id) == Path(audio.audio_path)
    reopened = service.get_take_video_inspection(take.id).report
    assert reopened is not None and reopened.audio_excerpt == audio
    instructions, text, images, _budget = fake.generations[0]
    payload = json.loads(text)
    assert "当前输入只有静态图像" in instructions
    assert payload["detail_frames"][0]["timestamp_is_source_pts"] is True
    assert payload["image_map"][-1]["role"] == "DETAIL_KEYFRAME"
    assert images is not None and images[-1].startswith("data:image/png;base64,")
    assert inspection_matches_request(report, document=document, take=take, request=request)
    assert not inspection_matches_request(report, document=document, take=take,
        request=request.model_copy(update={"dense_sample_fps": 6}))
    Path(report.detail_frames[0].image_path).unlink()
    assert not inspection_matches_request(report, document=document, take=take, request=request)


@pytest.mark.parametrize("values", [
    {"dense_sample_fps": 12},
    {"dense_sample_fps": 12, "source_out_seconds": 3.1},
    {"dense_sample_fps": 13, "source_out_seconds": 1},
    {"include_audio_excerpt": True, "source_out_seconds": 4},
    {"source_in_seconds": 1, "source_out_seconds": 2, "keyframe_times_seconds": [0.5]},
    {"source_out_seconds": 2, "keyframe_times_seconds": [2]},
])
def test_detail_request_rejects_ambiguous_or_out_of_range_sampling(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VideoInspectionRequest.model_validate(values)
