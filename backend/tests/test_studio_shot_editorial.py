"""Shot-only editing uses local time and preserves the rest of the real episode."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorAsset, EditorProject, EditorSubtitle, edit_editorial, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import EditorialClipChange, StudioEditorialEdit, StudioShotMediaSelect, StudioTimelineSave, StudioExportRequest
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial


def as_json(editor: EditorProject) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], editor.model_dump(mode="json"))


@pytest.fixture
def episode(tmp_path: Path) -> tuple[ShortDramaService, str, list[str]]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    path = tmp_path / "video.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=8", "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)
    imported = import_studio_media(service, pid, path, name="本地剪辑测试", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    ids = [item.id for item in document.shots[:3]]
    assert len(ids) == 3
    for shot in document.shots[:3]:
        select_imported_video(service, pid, shot.id, StudioShotMediaSelect(asset_version_id=version.id, expected_revision=shot.revision))
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    timeline = editor.timelines[0]
    for index, clip in enumerate(list(timeline.clips)):
        clip.startTime, clip.duration, clip.trimStart = index * 4.0, 4.0, 0.0
        audio = EditorAsset.model_validate({"id": f"audio-{index}", "path": str(version.local_path), "type": "audio", "duration": 8, "prompt": "本镜原声", "resolution": "", "createdAt": 1})
        editor.assets.append(audio)
        linked = clip.model_copy(deep=True, update={"id": f"linked-{index}", "assetId": audio.id, "type": "audio", "asset": audio, "trackIndex": 2, "linkedClipIds": [clip.id]})
        assert clip.model_extra is not None
        clip.model_extra["linkedClipIds"] = [linked.id]
        timeline.clips.append(linked)
        timeline.subtitles.append(EditorSubtitle(id=f"cue-{index}", text=f"镜头 {index + 1} 的字幕", startTime=index * 4.0 + 0.75, endTime=index * 4.0 + 3.5, trackIndex=3))
    service.studio.save_timeline(pid, StudioTimelineSave(timeline={"project": as_json(editor)}))
    return service, pid, ids


def test_local_view_trim_syncs_film_audio_subtitles_and_keeps_design(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    design = service.get_project(pid).document.shots
    baseline = EditorProject.model_validate(read_editorial(service, pid).project)
    local = read_shot_editorial(service, pid, ids[1])
    assert local.scope and local.scope.film_start_seconds == 4 and local.scope.duration_seconds == 4
    editor = EditorProject.model_validate(local.project)
    timeline = editor.timelines[0]
    assert len(timeline.clips) == 2
    assert {item.startTime for item in timeline.clips} == {0}
    assert [item.id for item in timeline.subtitles] == ["cue-1"]
    assert len(editor.assets) == 2
    for item in timeline.clips:
        item.trimStart, item.duration = 0.5, 3
    result = save_shot_editorial(service, pid, ids[1], StudioTimelineSave(expected_revision=1, anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": as_json(editor)}))
    assert result.project.document.shots == design
    full = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    clips = {item.id: item for item in full.clips}
    for item in baseline.timelines[0].clips:
        changed = clips[item.id]
        if item.startTime == 0:
            assert changed == item
        elif item.startTime == 4:
            assert (changed.startTime, changed.trimStart, changed.duration) == (4, 0.5, 3)
        else:
            assert changed.startTime == 7 and changed.duration == item.duration
    cues = {item.id: item for item in full.subtitles}
    assert (cues["cue-1"].startTime, cues["cue-1"].endTime) == (4.25, 7)
    assert (cues["cue-2"].startTime, cues["cue-2"].endTime) == (7.75, 10.5)
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    reopened = read_shot_editorial(restarted, pid, ids[1])
    assert reopened.scope and reopened.scope.duration_seconds == 3
    assert read_editorial(restarted, pid).revision == 2
    exported = start_export(restarted, pid, StudioExportRequest(expected_revision=2, width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == 11 and exported.video_path


def test_agent_edits_the_same_shot_scope_and_http_rejects_stale_or_foreign_content(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    local = read_shot_editorial(service, pid, ids[0])
    clip = EditorProject.model_validate(local.project).timelines[0].clips[0]
    edit_editorial(service, pid, StudioEditorialEdit(expected_revision=1, shot_id=ids[0], clips=[EditorialClipChange(id=clip.id, duration_seconds=3)]))
    following = read_shot_editorial(service, pid, ids[1])
    assert following.scope and following.scope.film_start_seconds == 3
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    base = f"/api/short-drama/studio/projects/{pid}/shots/{ids[0]}/editorial"
    with TestClient(app) as client:
        response = client.get(base)
        assert response.status_code == 200
        assert response.json()["scope"]["duration_seconds"] == 3
        stale = client.put(base, json={"expected_revision": 1, "timeline": {"project": local.project}})
        assert stale.status_code == 409
        full = read_editorial(service, pid)
        foreign = client.put(base, json={"expected_revision": 2, "timeline": {"project": full.project}})
        assert foreign.status_code == 422, foreign.text
    assert read_editorial(service, pid).revision == 2
    with pytest.raises(ValueError, match="局部剪辑"):
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=2, timeline={"project": read_shot_editorial(service, pid, ids[0]).project}))
    assert read_editorial(service, pid).revision == 2


def test_repeated_use_is_selected_explicitly_and_other_use_survives(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    source = next(item for item in editor.timelines[0].clips if item.type == "video")
    repeated = source.model_copy(deep=True, update={"id": "second-use", "startTime": 12.0, "linkedClipIds": []})
    editor.timelines[0].clips.append(repeated)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": as_json(editor)}))
    local = read_shot_editorial(service, pid, ids[0], "second-use")
    assert local.scope and len(local.scope.occurrences) == 2
    assert local.scope.film_start_seconds == 12
    edit_editorial(service, pid, StudioEditorialEdit(expected_revision=2, shot_id=ids[0], anchor_clip_id="second-use", clips=[EditorialClipChange(id="second-use", duration_seconds=2)]))
    full = EditorProject.model_validate(read_editorial(service, pid).project)
    assert next(item for item in full.timelines[0].clips if item.id == source.id) == source
    assert next(item for item in full.timelines[0].clips if item.id == "second-use").duration == 2


def test_shot_without_video_has_readable_empty_editor_and_no_fake_duration(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    value = read_shot_editorial(service, document.project.id, document.shots[0].id)
    assert value.scope and value.scope.duration_seconds == 0 and not value.scope.occurrences
    assert EditorProject.model_validate(value.project).timelines[0].clips == []
    assert service.get_project(document.project.id).document.shots == document.shots


def test_agent_speed_keeps_source_span_and_linked_audio(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    local = read_shot_editorial(service, pid, ids[0])
    video = next(item for item in EditorProject.model_validate(local.project).timelines[0].clips if item.type == "video")
    edit_editorial(service, pid, StudioEditorialEdit(expected_revision=1, shot_id=ids[0], clips=[EditorialClipChange(id=video.id, speed=2)]))
    after = read_shot_editorial(service, pid, ids[0])
    clips = EditorProject.model_validate(after.project).timelines[0].clips
    assert [(item.speed, item.duration, item.trimStart) for item in clips] == [(2, 2, 0), (2, 2, 0)]
    assert after.scope and after.scope.duration_seconds == 2
    following = read_shot_editorial(service, pid, ids[1])
    assert following.scope and following.scope.film_start_seconds == 2
