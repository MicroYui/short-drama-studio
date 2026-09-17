"""Complete subtitle coverage does not identify one of two mixed sources."""
from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.audio_inspection import AudioInspector, _clips_for_cue, _cue_has_timeline_audio
from short_drama.models import AudioInspectionRequest, SemanticReviewStatus, TimelineAudioClip
from tests.test_native_subtitle_alignment import _fixture


def _clip(path: Path) -> TimelineAudioClip:
    return TimelineAudioClip(id="return-picture-native", shot_id="shot03", take_id="take-return",
        media_path=str(path), start_seconds=9.42, source_in_seconds=0,
        duration_seconds=5.17, gain_db=0, role="EDITORIAL_AUDIO")


def test_real_jcut_geometry_does_not_select_the_shorter_complete_source(tmp_path: Path) -> None:
    """The saved '等等。' cue has both G2 native and independent G3 J-cut audio."""
    shorter = _clip(tmp_path / "return.wav")
    jcut = shorter.model_copy(update={"id": "doorway-native-jcut", "shot_id": "shot04",
        "take_id": "take-doorway", "media_path": str(tmp_path / "jcut.wav"),
        "start_seconds": 13.715, "duration_seconds": 11.08})
    clips = [shorter, jcut]
    before = [clip.model_dump(mode="json") for clip in clips]
    assert _cue_has_timeline_audio(clips, 13.935, 14.415)
    assert _clips_for_cue(clips, 13.935, 14.415) == []
    assert _clips_for_cue(list(reversed(clips)), 13.935, 14.415) == []
    assert [clip.model_dump(mode="json") for clip in clips] == before


@pytest.mark.parametrize("other_start", [13.715, 9.0])
def test_cutting_another_complete_source_does_not_remove_its_ambiguity(
    tmp_path: Path, other_start: float,
) -> None:
    first = _clip(tmp_path / "return.wav")
    # The other source crosses the cue continuously. Its start can be before
    # or after the uncut source, so global FILM sorting must not break the join.
    other = first.model_copy(update={"id": "jcut-left", "take_id": "take-doorway",
        "media_path": str(tmp_path / "jcut.wav"), "start_seconds": other_start,
        "source_in_seconds": 0, "duration_seconds": 14.1 - other_start})
    tail = other.model_copy(update={"id": "jcut-right", "start_seconds": 14.1,
        "source_in_seconds": other.duration_seconds, "duration_seconds": 24.795 - 14.1})
    clips = [first, other, tail]
    before = [clip.model_dump(mode="json") for clip in clips]
    assert _cue_has_timeline_audio(clips, 13.935, 14.415)
    assert _clips_for_cue(clips, 13.935, 14.415) == []
    assert _clips_for_cue(list(reversed(clips)), 13.935, 14.415) == []
    # The identical physical relation, expressed as one clip plus its split,
    # is not another sound source. Per-clip signal checks remain separate.
    uncut = other.model_copy(update={"id": "same-source-uncut", "duration_seconds": 24.795 - other_start})
    assert len(_clips_for_cue([uncut, other, tail], 13.935, 14.415)) == 1
    assert [clip.model_dump(mode="json") for clip in clips] == before


@pytest.mark.parametrize("difference", ["none", "path_alias", "source_offset", "file", "gain", "binding"])
def test_duplicate_complete_source_requires_the_same_physical_mapping(
    tmp_path: Path, difference: str,
) -> None:
    original = _clip(tmp_path / "native.wav")
    # A second representation may have a different outer trim/ID/shot while
    # addressing precisely the same file samples at this FILM position.
    duplicate = original.model_copy(update={"id": "shorter-view", "shot_id": "shot04",
        "start_seconds": 13.42, "source_in_seconds": 4, "duration_seconds": 1.17})
    if difference == "path_alias":
        duplicate.media_path = str(tmp_path / "subdir" / ".." / "native.wav")
    elif difference == "source_offset":
        duplicate.source_in_seconds += .1
    elif difference == "file":
        duplicate.media_path = str(tmp_path / "other-projection.wav")
    elif difference == "gain":
        duplicate.gain_db = -6
    elif difference == "binding":
        duplicate.take_id = "other-take"
    clips = [original, duplicate]
    before = [clip.model_dump(mode="json") for clip in clips]
    selected = _clips_for_cue(clips, 13.935, 14.415)
    if difference in {"none", "path_alias"}:
        assert len(selected) == 1
        assert selected[0].source_in_seconds + 13.935 - selected[0].start_seconds == pytest.approx(4.515)
    else:
        assert selected == []
    assert [clip.model_dump(mode="json") for clip in clips] == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_inspector_keeps_file_checks_but_does_not_transcribe_an_ambiguous_cue(
    tmp_path: Path, duplicate: bool,
) -> None:
    service, audio, original = _fixture(tmp_path)
    document = service.get_project(original.project_id).document
    manifest = original.model_copy(deep=True)
    first = manifest.audio_clips[0]
    other_path = tmp_path / "other-native.wav"
    other_path.write_bytes(Path(first.media_path).read_bytes())
    # Byte equality alone is not source identity: separate rendered files can
    # have independent provenance and no saved relation between them.
    other = first.model_copy(update={"id": "simultaneous-native",
        "media_path": first.media_path if duplicate else str(other_path),
        "take_id": first.take_id if duplicate else "other-take",
        "duration_seconds": 6, "source_out_seconds": 6})
    manifest.audio_clips = [first, other]
    manifest.subtitle_cues = [manifest.subtitle_cues[0].model_copy(update={
        "start_seconds": 2, "end_seconds": 4.5})]
    document.manifests[-1] = manifest
    service.repository.save_project(document)
    before = document.model_dump(mode="json")
    report = AudioInspector(tmp_path / "observed").inspect_manifest(document=document, manifest=manifest,
        request=AudioInspectionRequest(semantic_review=True, speaker_continuity=False, dialogue_padding_seconds=0),
        semantic_service=audio, created_at=document.project.created_at)
    assert service.repository.get_project(document.project.id).model_dump(mode="json") == before
    assert document.model_dump(mode="json") == before
    assert len(report.clip_inspections) == 2
    assert all(clip.metrics is not None for clip in report.clip_inspections)
    assert next(check for check in report.technical_checks if check.name == "subtitle-cues-map-to-audio").passed
    check = report.dialogue_checks[0]
    if duplicate:
        assert report.semantic_status == SemanticReviewStatus.COMPLETE
        assert check.exact_match is True
        assert check.extracted_audio_path is not None
    else:
        assert report.semantic_status == SemanticReviewStatus.UNAVAILABLE
        assert check.shot_id == check.take_id == "UNKNOWN"
        assert check.transcript_text is check.extracted_audio_path is check.exact_match is None
        assert check.transcription_status == SemanticReviewStatus.UNAVAILABLE
        assert [(issue.code, issue.severity) for issue in check.issues] == [("AUDIO_CUE_SOURCE_UNRESOLVED", "UNKNOWN")]
        assert not any(issue.code in {"AUDIO_CUE_OUTSIDE_CLIP", "DIALOGUE_WINDOW_MOSTLY_SILENT",
            "DIALOGUE_TRANSCRIPT_MISMATCH"} for issue in report.issues)
