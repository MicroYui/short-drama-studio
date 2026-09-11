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
from short_drama.models import AssetKind, GenerationUnitShotWindow
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


def test_returned_video_can_be_trimmed_before_adoption_without_changing_the_film_on_open(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, ids = episode
    aggregate = service.get_project(pid)
    aggregate.document.editorial_timeline = None
    aggregate.document.manifests = []
    aggregate.document.project.current_manifest_id = None
    service.repository.save_project(aggregate.document)
    for take in aggregate.takes:
        take.is_selected = False
        take.selected_shot_ids = []
        service.repository.save_take(take)

    local = read_shot_editorial(service, pid, ids[1])
    assert local.scope and local.scope.duration_seconds == 0
    editor = EditorProject.model_validate(local.project)
    clips = editor.timelines[0].clips
    assert len(clips) == 1
    assert (clips[0].model_extra or {}).get("sourceShotId") == ids[1]
    assert clips[0].startTime == 0 and not clips[0].muted
    assert len(editor.assets) == 1
    assert EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].clips == []
    assert service.get_project(pid).document.editorial_timeline is None
    assert all(not take.is_selected for take in service.get_project(pid).takes)

    clips[0].duration = 2
    save_shot_editorial(service, pid, ids[1], StudioTimelineSave(
        expected_revision=0, timeline={"project": as_json(editor)}))
    full = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    assert len(full.clips) == 1 and full.clips[0].duration == 2
    assert (full.clips[0].model_extra or {}).get("sourceShotId") == ids[1]
    assert service.get_project(pid).document.shots == aggregate.document.shots


def test_select_group_candidate_changes_only_current_requested_shot(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    aggregate = service.get_project(pid)
    old = next(item for item in aggregate.takes if item.shot_id == ids[1])
    candidate = old.model_copy(deep=True, update={"id": "new-group-candidate", "shot_id": ids[0], "candidate_index": 20, "generation_unit_id": "candidate-unit",
        "is_selected": False, "selected_shot_ids": [], "shot_windows": [
            GenerationUnitShotWindow(shot_id=ids[0], shot_revision=1, duration_seconds=4, source_in_seconds=0, source_out_seconds=4),
            GenerationUnitShotWindow(shot_id=ids[1], shot_revision=1, duration_seconds=4, source_in_seconds=4, source_out_seconds=8)]})
    service.repository.save_take(candidate)
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    service.select_take(candidate.id, shot_ids=[ids[1]], expected_editorial_revision=1)
    current = read_editorial(service, pid)
    assert current.revision == 2
    after = EditorProject.model_validate(current.project)
    for clip in before.timelines[0].clips:
        changed = next(item for item in after.timelines[0].clips if item.id == clip.id)
        if (clip.model_extra or {}).get("sourceShotId") == ids[1]:
            assert (changed.model_extra or {}).get("sourceTakeId") == candidate.id
            assert changed.trimStart == 4 and changed.startTime == 4 and changed.duration == 4
        else:
            assert changed == clip
    assert after.timelines[0].subtitles == before.timelines[0].subtitles
    exported = start_export(service, pid, StudioExportRequest(expected_revision=2, width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == 12


def test_select_candidate_requires_position_for_multiple_uses(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    from short_drama.service import WorkflowConflictError
    service, pid, ids = episode
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    first = next(item for item in editor.timelines[0].clips if item.type == "video")
    editor.timelines[0].clips.append(first.model_copy(deep=True, update={"id": "repeat-use", "startTime": 12, "linkedClipIds": []}))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": as_json(editor)}))
    old = next(item for item in service.get_project(pid).takes if item.id == (first.model_extra or {}).get("sourceTakeId"))
    candidate = old.model_copy(deep=True, update={"id": "candidate-repeat", "candidate_index": 21, "is_selected": False, "selected_shot_ids": []})
    service.repository.save_take(candidate)
    with pytest.raises(WorkflowConflictError, match="clip_ids"):
        service.select_take(candidate.id)
    assert read_editorial(service, pid).revision == 2
    service.select_take(candidate.id, clip_ids=["repeat-use"], expected_editorial_revision=2)
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    assert next(item for item in after.clips if item.id == first.id) == first
    assert (next(item for item in after.clips if item.id == "repeat-use").model_extra or {}).get("sourceTakeId") == candidate.id


def test_design_split_keeps_adopted_frames_audio_and_human_subtitles(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    from short_drama.models import DirectorRepairDecision, DirectorRepairStrategy
    from short_drama.reference_planner import DirectorRepairDecisionRequest, ReplacementShotDraft
    service, pid, ids = episode
    shot = next(item for item in service.get_project(pid).document.shots if item.id == ids[1])
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    parts = [ReplacementShotDraft(id=f"{shot.id}-{index}", duration_seconds=shot.duration_seconds / 2,
        narrative_purpose=shot.narrative_purpose, visual=shot.visual, framing=shot.framing, camera=shot.camera,
        audio_intent=shot.audio_intent, dialogue_line_ids=shot.dialogue_line_ids if index == 1 else [],
        continuity=shot.continuity, transition_out=shot.transition_out, local_rework_risk=shot.local_rework_risk) for index in (1, 2)]
    service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(decision=DirectorRepairDecision.APPLIED,
        strategy=DirectorRepairStrategy.SPLIT_SHOT, reason="分别呈现动作和反应", split_shots=parts))
    current = read_editorial(service, pid)
    after = EditorProject.model_validate(current.project).timelines[0]
    for source in before.timelines[0].clips:
        if (source.model_extra or {}).get("sourceShotId") != ids[1]:
            assert next(item for item in after.clips if item.id == source.id) == source
    clips = [item for item in after.clips if (item.model_extra or {}).get("sourceShotId") in {part.id for part in parts}]
    assert len(clips) == 4
    assert sorted((item.startTime, item.trimStart, item.duration) for item in clips) == [(4, 0, 2), (4, 0, 2), (6, 2, 2), (6, 2, 2)]
    assert [(item.text, item.startTime, item.endTime, item.style) for item in after.subtitles] == [(item.text, item.startTime, item.endTime, item.style) for item in before.timelines[0].subtitles]
    # A later regenerated replacement starts its own source at zero. The old
    # second half's offset must not be carried across as a new two-second crop.
    from short_drama.models import Freshness
    original = next(item for item in service.get_project(pid).takes if item.shot_id == ids[1])
    replacement = original.model_copy(deep=True, update={"id": "split-reaction-candidate",
        "shot_id": parts[1].id, "candidate_index": 30, "freshness": Freshness.CURRENT,
        "is_selected": False, "selected_shot_ids": [], "shot_windows": []})
    service.repository.save_take(replacement)
    service.select_take(replacement.id, expected_editorial_revision=current.revision)
    current = read_editorial(service, pid)
    adopted = EditorProject.model_validate(current.project).timelines[0]
    assert [(clip.startTime, clip.duration, clip.trimStart) for clip in adopted.clips
            if (clip.model_extra or {}).get("sourceShotId") == parts[1].id] == [(6, 2, 0), (6, 2, 0)]
    exported = start_export(service, pid, StudioExportRequest(expected_revision=current.revision, width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == 12


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


def test_direct_local_editor_project_can_be_saved(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    local = read_shot_editorial(service, pid, ids[1])
    editor = EditorProject.model_validate(local.project)
    for clip in editor.timelines[0].clips:
        clip.duration = 3
    save_shot_editorial(service, pid, ids[1], StudioTimelineSave(
        expected_revision=1, timeline=as_json(editor)))
    after = read_shot_editorial(service, pid, ids[1])
    assert after.scope and after.scope.duration_seconds == 3
    following = read_shot_editorial(service, pid, ids[2])
    assert following.scope and following.scope.film_start_seconds == 7


@pytest.mark.parametrize("following_source_shot", [False, True])
def test_unused_source_tail_is_editable_without_entering_another_shot(
    episode: tuple[ShortDramaService, str, list[str]], following_source_shot: bool,
) -> None:
    service, pid, ids = episode
    aggregate = service.get_project(pid)
    take = next(item for item in aggregate.takes if item.shot_id == ids[1])
    take.shot_id = ids[0]
    take.generation_unit_id = "shared-source-unit"
    take.shot_windows = [GenerationUnitShotWindow(
        shot_id=shot_id, shot_revision=1, source_in_seconds=start,
        source_out_seconds=end, duration_seconds=end-start,
    ) for shot_id, start, end in [(ids[0], 0, 2), (ids[1], 2, 6)]]
    if following_source_shot:
        take.shot_windows.append(GenerationUnitShotWindow(
            shot_id=ids[2], shot_revision=1, source_in_seconds=6,
            source_out_seconds=8, duration_seconds=2))
    service.repository.save_take(take)
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    for clip in editor.timelines[0].clips:
        if (clip.model_extra or {}).get("sourceShotId") == ids[1]:
            clip.trimStart = 2
    service.studio.save_timeline(pid, StudioTimelineSave(
        expected_revision=1, timeline={"project": as_json(editor)}))
    baseline = read_editorial(service, pid)
    local = read_shot_editorial(service, pid, ids[1])
    local_clips = EditorProject.model_validate(local.project).timelines[0].clips
    assert {(item.model_extra or {}).get("sourceWindowEnd") for item in local_clips} == {
        6 if following_source_shot else 8}
    video = next(item for item in local_clips if item.type == "video")
    change = StudioEditorialEdit(expected_revision=2, shot_id=ids[1],
        clips=[EditorialClipChange(id=video.id, duration_seconds=5.5)])
    if following_source_shot:
        with pytest.raises(ValueError, match="本镜头的素材范围"):
            edit_editorial(service, pid, change)
        assert read_editorial(service, pid) == baseline
        return
    edit_editorial(service, pid, change)
    full = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    for clip in full.clips:
        shot_id = (clip.model_extra or {}).get("sourceShotId")
        if shot_id == ids[0]:
            assert clip.startTime == 0 and clip.duration == 4
        elif shot_id == ids[1]:
            assert (clip.startTime, clip.trimStart, clip.duration) == (4, 2, 5.5)
        elif shot_id == ids[2]:
            assert clip.startTime == 9.5 and clip.duration == 4
    assert next(cue for cue in full.subtitles if cue.id == "cue-2").startTime == 10.25
    assert service.get_project(pid).document.shots == aggregate.document.shots


def test_tool_patches_subtitle_and_volume_together_without_rewriting_other_content(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, ids = episode
    before = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    video = next(item for item in before.clips if item.type == "video" and (item.model_extra or {}).get("sourceShotId") == ids[1])
    result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": 1, "clips": [{"id": video.id, "volume": 0.8}],
        "subtitles": [{"id": "cue-1", "start_seconds": 4.85, "end_seconds": 7.6}]}})
    assert result.error is None
    assert result.output and result.output["content"]["editorial_revision"] == 2
    assert result.output["content"]["changed_subtitles"][0]["id"] == "cue-1"
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    linked = set((video.model_extra or {}).get("linkedClipIds", []))
    for clip in before.clips:
        current = next(item for item in after.clips if item.id == clip.id)
        assert current == (clip.model_copy(update={"volume": 0.8}) if clip.id in linked else clip)
    for cue in before.subtitles:
        current = next(item for item in after.subtitles if item.id == cue.id)
        assert current == (cue.model_copy(update={"startTime": 4.85, "endTime": 7.6}) if cue.id == "cue-1" else cue)
    # A bad cue must not commit the otherwise valid clip mutation.
    failed = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "expected_revision": 2, "clips": [{"id": video.id, "volume": 0.2}],
        "subtitles": [{"id": "missing-cue", "text": "cannot save"}]}})
    assert failed.error is not None
    assert read_editorial(service, pid).revision == 2
    assert EditorProject.model_validate(read_editorial(service, pid).project).timelines[0] == after
