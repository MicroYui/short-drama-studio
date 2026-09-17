"""A registered inspection WAV keeps its own clock and its original Take binding."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast
import wave

import numpy as np
from pydantic import JsonValue
import pytest

from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import AssetKind, AudioInspectionResponse, Take, VideoInspectionReport, VideoInspectionRequest
from short_drama.service import ShortDramaService
from short_drama.studio_audio import editorial_audio_input
from short_drama.studio_audio_excerpt import inspection_audio_source
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave
from short_drama.tools import ToolCallStatus
from tests.test_video_detail import render_source
from tests.test_video_inspection import FakeInspectionLanguageProvider


@dataclass
class ExcerptFixture:
    service: ShortDramaService
    take: Take
    report: VideoInspectionReport
    project: EditorProject
    clip: EditorClip
    fake: FakeInspectionLanguageProvider


def _fixture(tmp_path: Path, *, start: float = 1.13, end: float = 2.13,
             trim: float = .12, duration: float = .7, speed: float = 1, reverse: bool = False) -> ExcerptFixture:
    fake = FakeInspectionLanguageProvider(['{"observations":[],"issues":[],"summary":"Static frames only."}'])
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: fake)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    document = service.initialize_fixture().document
    pid, shot_id = document.project.id, document.shots[0].id
    source = tmp_path / "source.mov"
    render_source(source)
    imported = import_studio_media(service, pid, source, name="Original Take", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, pid, shot_id,
        StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    take = service.repository.list_takes(pid)[0]
    report = service.inspect_take_video(take.id, VideoInspectionRequest(source_in_seconds=start, source_out_seconds=end,
        include_audio_excerpt=True, include_previous_boundary=False, max_frames=6)).report
    excerpt = report.audio_excerpt
    assert excerpt is not None
    saved = read_editorial(service, pid)
    project = EditorProject.model_validate(saved.project)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    for original in timeline.clips:
        original.muted = True
    # The stored path may use an equivalent spelling; identity is normalized.
    wav_path = Path(excerpt.audio_path)
    asset = EditorAsset(id="registered-excerpt", path=f"{wav_path.parent}/./{wav_path.name}",
        type="audio", duration=end-start)
    clip = EditorClip(id="audio-line03-continuous", assetId=asset.id, type="audio", startTime=5.5,
        duration=duration, trimStart=trim, speed=speed, reversed=reverse, trackIndex=1,
        sourceTakeId=take.id, sourceShotId=shot_id, sourceWindowStart=0, sourceWindowEnd=end-start)
    project.assets.append(asset)
    timeline.clips.append(clip)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    return ExcerptFixture(service, take, report, project, clip, fake)


@pytest.mark.parametrize(("start", "end", "trim", "duration", "speed", "reverse", "pulse_starts"), [
    (0.0, 3.0, 0.0, 3.0, 1.0, False, [1.37, 1.87]),
    (1.13, 2.13, .12, .7, 1.0, False, [.12, .62]),
    (1.13, 2.13, .12, .35, 2.0, True, [.035, .285]),
])
def test_saved_excerpt_inspection_uses_local_pcm_clock_and_preserves_film_scope(
    tmp_path: Path, start: float, end: float, trim: float, duration: float,
    speed: float, reverse: bool, pulse_starts: list[float],
) -> None:
    fixture = _fixture(tmp_path, start=start, end=end, trim=trim, duration=duration, speed=speed, reverse=reverse)
    service, pid = fixture.service, fixture.take.project_id
    before = service.get_project(pid)
    result = service.tool_registry.invoke("audio.inspect_manifest", {"project_id": pid,
        "request": {"scope": "EDITORIAL", "semantic_review": False, "speaker_continuity": False}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    report = AudioInspectionResponse.model_validate(result.output).report
    assert report.observation_scope == "EDITORIAL" and report.time_space == "FILM"
    assert report.source_take_id is None and report.source_editorial_revision == read_editorial(service, pid).revision
    assert len(report.clip_inspections) == 1
    inspected = report.clip_inspections[0]
    assert inspected.take_id == fixture.take.id and inspected.shot_id == fixture.take.shot_id
    assert inspected.timeline_start_seconds == 5.5 and inspected.metrics is not None
    assert inspected.metrics.duration_seconds == pytest.approx(duration, abs=.001)
    assert inspected.extracted_audio_path is not None
    with wave.open(inspected.extracted_audio_path, "rb") as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        rate = wav.getframerate()
    # Original impulses at 1.37/1.87: excerpt offset is removed once, then local
    # trim, speed and reverse apply. Reapplying the Take offset loses both.
    loud = np.flatnonzero(abs(samples.astype(np.int32)) > 1000)
    assert len(loud) > 0
    groups = np.split(loud, np.flatnonzero(np.diff(loud) > round(.03 * rate)) + 1)
    assert len(groups) == 2
    assert [float(group[0]) / rate for group in groups] == pytest.approx(pulse_starts, abs=.025)
    source_files = list((service.workspace_root / pid / "inspections").glob("editorial-input-*/source.json"))
    assert len(source_files) == 1
    source = json.loads(source_files[0].read_text())
    assert source["audio_input"]["audio_clips"][0]["source_in_seconds"] == 0
    provenance = source["inspection_audio_sources"][0]
    assert provenance["inspection_id"] == fixture.report.id
    assert provenance["source_in_seconds"] == pytest.approx(start + trim)
    assert provenance["source_out_seconds"] == pytest.approx(start + trim + duration * speed)
    after = service.get_project(pid)
    assert before.document == after.document and before.takes == after.takes
    assert len(fixture.fake.generations) == 1  # Only the original Fake video inspection.
    reopened = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    assert reopened.repository.get_video_inspection(fixture.report.id) == fixture.report
    assert reopened.repository.get_audio_inspection(report.id) == report


@pytest.mark.parametrize("case", ["foreign_report", "wrong_take", "missing_take", "wrong_shot", "unknown_path", "unverified_source",
    "changed_take_path", "changed_take_file", "invalid_interval", "mismatched_observed_interval",
    "past_take_end", "missing_excerpt", "truncated_excerpt"])
def test_saved_excerpt_rejects_unknown_wrong_or_stale_bindings(tmp_path: Path, case: str) -> None:
    fixture = _fixture(tmp_path)
    service, take, report = fixture.service, fixture.take, fixture.report.model_copy(deep=True)
    assert report.audio_excerpt is not None
    excerpt_path = Path(report.audio_excerpt.audio_path)
    if case == "foreign_report":
        foreign = service.get_project(take.project_id).document.model_copy(deep=True)
        foreign.project.id = "foreign-project"
        service.repository.save_project(foreign)
        report.project_id = foreign.project.id
    elif case == "wrong_take":
        other = take.model_copy(deep=True, update={"id": "other-take"})
        service.repository.save_take(other)
        report.take_id = other.id
    elif case == "wrong_shot":
        fixture.clip.__pydantic_extra__ = {**(fixture.clip.model_extra or {}), "sourceShotId": "not-a-covered-shot"}
    elif case == "missing_take":
        fixture.clip.__pydantic_extra__ = {**(fixture.clip.model_extra or {}), "sourceTakeId": "missing-take"}
    elif case == "unknown_path":
        report.audio_excerpt.audio_path = str(excerpt_path.with_name("other.wav"))
    elif case == "unverified_source":
        report.source_context = {}
    elif case == "changed_take_path":
        other_path = excerpt_path.with_name("different-original.mov")
        other_path.write_bytes(Path(take.media_path).read_bytes())
        take.media_path = str(other_path)
        service.repository.save_take(take)
    elif case == "changed_take_file":
        with Path(take.media_path).open("ab") as stream:
            stream.write(b"changed")
    elif case == "invalid_interval":
        report.audio_excerpt.source_out_seconds += .25
    elif case == "mismatched_observed_interval":
        report.observed_source_in_seconds += .25
    elif case == "past_take_end":
        take.duration_seconds = 1.5
        service.repository.save_take(take)
    elif case == "missing_excerpt":
        excerpt_path.unlink()
    elif case == "truncated_excerpt":
        with wave.open(str(excerpt_path), "wb") as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
            wav.writeframes(bytes(4800 * 4))
    service.repository.save_video_inspection(report)
    document = service.get_project(take.project_id).document
    if case in {"wrong_shot", "missing_take"}:
        document.editorial_timeline = {**(document.editorial_timeline or {}),
            "project": cast(dict[str, JsonValue], fixture.project.model_dump(mode="json"))}
    before = service.get_project(take.project_id).document
    with pytest.raises(ValueError, match="Saved audio clip audio-line03-continuous"):
        editorial_audio_input(service, document, "2026-09-16T22:29:26Z")
    assert service.get_project(take.project_id).document == before
    assert len(fixture.fake.generations) == 1


@pytest.mark.parametrize("case", ["copied", "changed_copy", "unrelated_source"])
def test_imported_pcm_copy_retains_take_clock_only_when_it_matches_recorded_excerpt(tmp_path: Path, case: str) -> None:
    fixture = _fixture(tmp_path)
    service, pid = fixture.service, fixture.take.project_id
    assert fixture.report.audio_excerpt is not None
    imported = import_studio_media(service, pid, Path(fixture.report.audio_excerpt.audio_path),
        name="Retained native audio", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    assert version.local_path is not None and version.source is not None
    if case == "changed_copy":
        content = bytearray(Path(version.local_path).read_bytes())
        content[-1] ^= 1
        Path(version.local_path).write_bytes(content)
    elif case == "unrelated_source":
        version.source.source_uri = str(tmp_path / "unrelated.wav")
        service.repository.save_project(imported.document)
    project = fixture.project.model_copy(deep=True)
    clip = next(item for item in project.timelines[0].clips if item.id == fixture.clip.id)
    clip.assetId = version.id
    project.assets.append(EditorAsset(id=version.id, path=version.local_path, type="audio", duration=1.0))
    current = read_editorial(service, pid)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    document = service.get_project(pid).document
    prepared = editorial_audio_input(service, document, "2026-09-17T04:10:00Z")
    assert prepared is not None
    audio = prepared[0].audio_clips
    assert len(audio) == 1 and audio[0].source_in_seconds == 0
    if case == "copied":
        assert audio[0].take_id == fixture.take.id and audio[0].shot_id == fixture.take.shot_id
        assert audio[0].asset_version_id is None
    else:
        assert audio[0].take_id is None and audio[0].asset_version_id == version.id
    native = editorial_audio_input(service, document, "2026-09-17T04:10:01Z", native_only=True)
    assert native is not None
    assert len(native[0].audio_clips) == (1 if case == "copied" else 0)
    assert len(fixture.fake.generations) == 1


def test_excerpt_trim_cannot_exceed_registered_window_even_if_editor_asset_claims_more(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    document = fixture.service.get_project(fixture.take.project_id).document
    project = EditorProject.model_validate((document.editorial_timeline or {})["project"])
    next(asset for asset in project.assets if asset.id == "registered-excerpt").duration = 10
    clip = next(clip for clip in project.timelines[0].clips if clip.id == fixture.clip.id)
    clip.trimStart = .5
    document.editorial_timeline = {**(document.editorial_timeline or {}),
        "project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}
    with pytest.raises(ValueError, match="local duration"):
        editorial_audio_input(fixture.service, document, "2026-09-16T22:29:26Z")


def test_excerpt_does_not_require_current_story_revision(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    document = fixture.service.get_project(fixture.take.project_id).document
    document.shots[0].revision += 1
    result = editorial_audio_input(fixture.service, document, "2026-09-16T22:29:26Z", native_only=True)
    assert result is not None and result[0].audio_clips[0].take_id == fixture.take.id
    # A current-project report still cannot authorize a foreign Take object.
    foreign = fixture.take.model_copy(update={"project_id": "foreign-project"})
    assert fixture.report.audio_excerpt is not None
    with pytest.raises(ValueError, match="project, Take and shot binding"):
        inspection_audio_source(project_id=document.project.id, clip=fixture.clip,
            path=fixture.report.audio_excerpt.audio_path, take=foreign, shot_id=fixture.take.shot_id,
            reports=[fixture.report])
