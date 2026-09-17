"""A caption crossing retained native audio must use both saved local clocks."""
from __future__ import annotations

from pathlib import Path
import wave

import numpy as np
import pytest

from short_drama.audio_inspection import AudioInspector
from short_drama.models import AudioInspectionRequest, SemanticReviewStatus, SubtitleCue
from short_drama.service import ShortDramaService
from tests.test_native_subtitle_alignment import _fixture


def _pcm(path: Path, samples: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples)


@pytest.mark.parametrize("script_linked", [True, False])
@pytest.mark.parametrize("case", ["retained_tail", "gap", "overlap", "other_take", "ambiguous_tail", "extra_overlap", "background", "short_file", "missing_file", "silent"])
def test_cue_uses_exact_audio_across_picture_cut_and_pcm_tail(
    tmp_path: Path, case: str, script_linked: bool,
) -> None:
    service, _audio, original = _fixture(tmp_path)
    document = service.get_project(original.project_id).document
    manifest = original.model_copy(deep=True)
    with wave.open(manifest.audio_clips[0].media_path, "rb") as wav:
        source = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    main_path, tail_path = tmp_path / "picture-source.wav", tmp_path / "retained-tail.wav"
    _pcm(main_path, source[:72000].tobytes())  # The first file really ends at 4.5s.
    _pcm(tail_path, np.concatenate([np.zeros(4800, dtype="<i2"), source[72000:96000]]).tobytes())
    first = manifest.audio_clips[0].model_copy(update={"media_path": str(main_path),
        "duration_seconds": 4.5, "source_out_seconds": 4.5})
    tail = manifest.audio_clips[1].model_copy(update={"media_path": str(tail_path),
        "start_seconds": 4.5, "source_in_seconds": .3, "source_out_seconds": 1.8, "duration_seconds": 1.5})
    if case == "gap":
        tail.start_seconds += .2
    elif case == "overlap":
        tail.start_seconds -= .2
    elif case == "other_take":
        tail.take_id = "unrelated-take"
    elif case == "short_file":
        _pcm(tail_path, bytes(16000))
    elif case == "missing_file":
        tail_path.unlink()
    elif case == "silent":
        _pcm(main_path, bytes(72000 * 2))
        _pcm(tail_path, bytes(28800 * 2))
    manifest.audio_clips = [first, tail]
    if case == "ambiguous_tail":
        manifest.audio_clips.append(tail.model_copy(update={"id": "other-tail"}))
    elif case in {"extra_overlap", "background"}:
        manifest.audio_clips.append(first.model_copy(update={"id": "overlapping-use",
            "start_seconds": 4, "duration_seconds": 1, "source_out_seconds": 1,
            "take_id": "background-take" if case == "background" else first.take_id}))
    line = document.script_versions[-1].lines[0]
    manifest.subtitle_cues = [SubtitleCue(id="across-cut", script_line_id=line.id if script_linked else None,
        timed_text_id=None if script_linked else "timed-text", text=line.subtitle_text, speaker=line.speaker,
        start_seconds=2, end_seconds=5.6)]
    before = document.model_dump(mode="json")
    manifest_before = manifest.model_dump(mode="json")
    report = AudioInspector(tmp_path / "observed").inspect_manifest(document=document, manifest=manifest,
        request=AudioInspectionRequest(semantic_review=False, speaker_continuity=False, dialogue_padding_seconds=0),
        semantic_service=None, created_at=document.project.created_at)
    assert document.model_dump(mode="json") == before
    assert manifest.model_dump(mode="json") == manifest_before
    outside = {"AUDIO_CUE_OUTSIDE_CLIP", "AUDIO_TIMED_TEXT_OUTSIDE_CLIP"}
    if case == "gap":
        assert outside.intersection(issue.code for issue in report.issues)
        return
    assert not outside.intersection(issue.code for issue in report.issues)
    if not script_linked:
        assert report.dialogue_checks == []
        return
    check = report.dialogue_checks[0]
    assert check.exact_match is None  # A mapped window is not a semantic verdict.
    if case in {"overlap", "other_take", "ambiguous_tail", "extra_overlap"}:
        assert check.transcription_status == SemanticReviewStatus.UNAVAILABLE
        assert check.take_id == check.shot_id == "UNKNOWN"
        assert check.extracted_audio_path is None
        issue = next(issue for issue in report.issues if issue.code == "AUDIO_CUE_SOURCE_UNRESOLVED")
        assert issue.severity == "UNKNOWN"
        assert ("overlap" in issue.message) == (case != "other_take")
        return
    if case in {"short_file", "missing_file"}:
        assert any(issue.code == "DIALOGUE_WINDOW_UNAVAILABLE" for issue in report.issues)
        assert any(issue.code == ("AUDIO_DURATION_MISMATCH" if case == "short_file" else "AUDIO_STREAM_UNAVAILABLE")
            for issue in report.issues)
        return
    if case == "silent":
        assert any(issue.code == "DIALOGUE_WINDOW_MOSTLY_SILENT" for issue in report.issues)
        return
    assert check.extracted_audio_path is not None
    with wave.open(check.extracted_audio_path, "rb") as wav:
        actual = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    np.testing.assert_array_equal(actual, source[32000:89600])
    assert check.take_id == first.take_id


@pytest.mark.parametrize("semantic_review", [False, True])
def test_imported_asset_tail_and_concurrent_pcm_keep_coverage_without_inventing_take(
    tmp_path: Path, semantic_review: bool,
) -> None:
    """Film5 line02's actual saved times, after import lost inspection provenance."""
    service, audio, original = _fixture(tmp_path)
    document = service.get_project(original.project_id).document
    manifest = original.model_copy(deep=True)
    first = original.audio_clips[0].model_copy(update={
        "id": "opening-native", "start_seconds": 0, "duration_seconds": 6.166667,
        "source_in_seconds": 0, "source_out_seconds": 6.166667, "role": "EDITORIAL_AUDIO",
    })
    tail_path, next_path = tmp_path / "imported-line02-tail.wav", tmp_path / "imported-line03.wav"
    with wave.open(first.media_path, "rb") as wav:
        source = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    _pcm(tail_path, source[round(4 * 16000):round((4 + .413333) * 16000)].tobytes())
    _pcm(next_path, source[round(2 * 16000):round(5 * 16000)].tobytes())
    tail = first.model_copy(update={
        "id": "audio-line02-tail-continuous", "shot_id": None, "take_id": None,
        "asset_id": "imported-tail", "asset_version_id": "imported-tail-version",
        "media_path": str(tail_path), "start_seconds": 6.166667, "duration_seconds": .413333,
        "source_in_seconds": 0, "source_out_seconds": .413333,
    })
    other = tail.model_copy(update={
        "id": "audio-line03-continuous", "asset_id": "imported-next", "asset_version_id": "imported-next-version",
        "media_path": str(next_path), "duration_seconds": 3, "source_out_seconds": 3,
    })
    manifest.audio_clips = [other, first, tail]  # Same order as the real imported manifest.
    line, second_line = document.script_versions[-1].lines[:2]
    manifest.subtitle_cues = [SubtitleCue(id="subtitle-breakfast-line-02", script_line_id=line.id,
        text=line.subtitle_text, speaker=line.speaker, start_seconds=2.5, end_seconds=6.56),
        SubtitleCue(id="independent-mapped-cue", script_line_id=second_line.id,
            text=second_line.subtitle_text, speaker=second_line.speaker, start_seconds=7, end_seconds=8)]
    document.manifests[-1] = manifest
    service.repository.save_project(document)
    reloaded = ShortDramaService(database_path=tmp_path / "app.sqlite3", workspace_root=tmp_path / "media")
    saved = reloaded.get_project(document.project.id).document
    assert reloaded.repository.list_video_inspections(document.project.id) == []
    before = saved.model_dump(mode="json")
    report = AudioInspector(tmp_path / "observed").inspect_manifest(document=saved, manifest=saved.manifests[-1],
        request=AudioInspectionRequest(semantic_review=semantic_review, speaker_continuity=False, dialogue_padding_seconds=0),
        semantic_service=audio if semantic_review else None, created_at=document.project.created_at)
    assert reloaded.repository.get_project(document.project.id).model_dump(mode="json") == before
    assert saved.model_dump(mode="json") == before
    assert not any(issue.code == "AUDIO_CUE_OUTSIDE_CLIP" for issue in report.issues)
    assert next(check for check in report.technical_checks if check.name == "subtitle-cues-map-to-audio").passed
    check = report.dialogue_checks[0]
    assert check.take_id == check.shot_id == "UNKNOWN"
    assert check.transcription_status == SemanticReviewStatus.UNAVAILABLE
    assert check.extracted_audio_path is check.transcript_text is check.exact_match is None
    assert check.issues[0].code == "AUDIO_CUE_SOURCE_UNRESOLVED"
    assert check.issues[0].severity == "UNKNOWN"
    assert "overlap" in check.issues[0].message
    assert all(clip.id in check.issues[0].message for clip in (first, tail, other))
    if semantic_review:
        assert report.dialogue_checks[1].exact_match is True
        assert report.semantic_status == SemanticReviewStatus.UNAVAILABLE
    else:
        assert report.semantic_status == SemanticReviewStatus.NOT_REQUESTED
