"""Linked original sound selection agrees across listening, snapshots and subtitle sources."""
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.models import AssetKind, AudioInspectionRequest
from short_drama.observation_sources import audio_source_context
from short_drama.service import ShortDramaService
from short_drama.studio_audio import editorial_audio_input
from short_drama.studio_editorial import EditorClip, EditorProject, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave
from short_drama.studio_subtitles import native_audio_sources


@pytest.mark.parametrize("has_audio", [True, False])
def test_linked_native_audio_is_inspected_once_and_disabled_sound_stays_absent(tmp_path: Path, has_audio: bool) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    video = tmp_path / "source.mp4"
    args = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x36:r=24:d=1"]
    if has_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    else:
        args += ["-an"]
    subprocess.run(args + ["-c:v", "libx264", "-shortest", str(video)], check=True)
    imported = import_studio_media(service, pid, video, name="关联原声", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, pid, document.shots[0].id,
        StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    saved = read_editorial(service, pid)
    project = EditorProject.model_validate(saved.project)
    timeline = project.timelines[0]
    visual = next(clip for clip in timeline.clips if clip.type == "video")
    sound = EditorClip.model_validate({**visual.model_dump(mode="json"), "id": "native-audio", "type": "audio",
        "trackIndex": 2, "linkedClipIds": [visual.id]})
    assert visual.model_extra is not None
    visual.model_extra["linkedClipIds"] = [sound.id]
    timeline.clips = [visual, sound]
    timeline.subtitles = []
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    document = service.get_project(pid).document
    snapshot = audio_source_context(document)
    assert set(cast(dict[str, JsonValue], snapshot["clips"])) == {sound.id}
    sources = native_audio_sources(service, document, project, timeline)
    assert [source.clip.id for source in sources] == [sound.id]
    report = service.inspect_current_manifest_audio(pid, AudioInspectionRequest(semantic_review=False, speaker_continuity=False)).report
    assert [item.clip_id for item in report.clip_inspections] == [sound.id]
    inspected = report.clip_inspections[0]
    if has_audio:
        assert inspected.metrics is not None
    else:
        assert inspected.metrics is None
        assert any(not check.passed for check in inspected.technical_checks)
    for mode in ("muted", "disabled", "clip-muted", "solo-video"):
        edited = project.model_copy(deep=True)
        active = edited.timelines[0]
        if mode == "muted":
            active.tracks[2].muted = True
        elif mode == "disabled":
            active.tracks[2].enabled = False
        elif mode == "clip-muted":
            active.clips[1].muted = True
        else:
            active.tracks[visual.trackIndex].solo = True
        current = document.model_copy(deep=True)
        current.editorial_timeline = {"revision": 1, "project": cast(dict[str, JsonValue], edited.model_dump(mode="json"))}
        prepared = editorial_audio_input(service, current, "2026-09-16T00:00:00Z")
        assert prepared is not None and prepared[0].audio_clips == [], mode
        assert audio_source_context(current)["clips"] == {}, mode
        assert native_audio_sources(service, current, edited, active) == [], mode
