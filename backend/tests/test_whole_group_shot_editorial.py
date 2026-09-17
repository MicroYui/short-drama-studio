"""A saved whole H3 group remains one media use when its shot view is edited."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.models import Freshness, GenerationTask, Take
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorAsset, EditorProject, EditorSubtitle, EditorTimeline, read_editorial
from short_drama.studio_models import StudioTimelineSave
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
from short_drama.studio_shot_uses import shot_clip_uses, slice_clip


FIXTURE = Path(__file__).parent / "fixtures/studio-whole-group-uses.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_shared_frontend_backend_fixture(case: dict[str, Any]) -> None:
    assets = [EditorAsset.model_validate(item) for item in case["assets"]]
    takes = [Take.model_validate(item) for item in case["takes"]]
    timeline = EditorTimeline.model_validate(case["timeline"])
    before = timeline.model_dump()
    for shot_id, expected in case["expected"].items():
        uses = shot_clip_uses(timeline, assets, takes, shot_id)
        assert [{"anchor_clip_id": use.clip.id, "start_seconds": use.start,
                 "duration_seconds": use.end - use.start} for use in uses] == expected
    assert timeline.model_dump() == before


def _json(editor: EditorProject) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], editor.model_dump(mode="json"))


@pytest.fixture
def whole_group(tmp_path: Path) -> tuple[ShortDramaService, str]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    pid = document.project.id
    media = tmp_path / "whole-group.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=160x90:r=24:d=16", "-f", "lavfi", "-i", "sine=frequency=440:duration=16",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(media)], check=True, capture_output=True)
    case = CASES[0]
    document.shots = [document.shots[0].model_copy(deep=True, update={
        "id": identity, "sequence": index + 1,
    }) for index, identity in enumerate(case["expected"])]
    document.manifests = []
    document.project.current_manifest_id = None
    service.repository.save_project(document)
    for row in case["takes"]:
        take = Take.model_validate({**row, "project_id": pid, "media_path": str(media)})
        service.repository.save_task(GenerationTask.model_validate({
            "id": take.task_id, "project_id": pid, "shot_id": take.shot_id, "operation": take.operation,
            "candidate_index": take.candidate_index, "status": "SUCCEEDED", "provider": "mock", "model": "local-fixture",
            "request_fingerprint": "local-whole-group", "input_script_version_id": document.script_versions[-1].id,
            "input_bible_version_id": document.bible_versions[-1].id, "requested_duration_seconds": take.duration_seconds,
            "attempt": 1, "created_at": take.created_at, "updated_at": take.created_at,
        }))
        service.repository.save_take(take)
    editor = EditorProject.model_validate({"version": 2, "id": pid, "name": "Whole group local test",
        "assets": [{**asset, "path": str(media)} for asset in case["assets"]],
        "timelines": [case["timeline"]], "activeTimelineId": case["timeline"]["id"]})
    editor.timelines[0].subtitles = [EditorSubtitle(id="crossing", text="保留跨界字幕", startTime=6, endTime=8,
        trackIndex=0), EditorSubtitle(id="later", text="保留后续字幕", startTime=20, endTime=22, trackIndex=0)]
    service.studio.save_timeline(pid, StudioTimelineSave(timeline={"project": _json(editor)}))
    return service, pid


@pytest.mark.parametrize(("shot_id", "start", "duration"), [
    ("shot-yqzyd-02", 6.5, 5.75), ("shot-yqzyd-06", 37.625, 4),
])
def test_read_and_noop_save_use_current_whole_group_without_insertion(
    whole_group: tuple[ShortDramaService, str], shot_id: str, start: float, duration: float,
) -> None:
    service, pid = whole_group
    before = read_editorial(service, pid)
    local = read_shot_editorial(service, pid, shot_id)
    assert local.scope and local.scope.anchor_clip_id
    assert (local.scope.film_start_seconds, local.scope.duration_seconds) == (start, duration)
    assert read_editorial(service, pid) == before  # Opening is read-only.
    result = save_shot_editorial(service, pid, shot_id, StudioTimelineSave(
        expected_revision=local.revision, anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": local.project}))
    after = read_editorial(service, pid)
    assert after.project == before.project
    assert len(EditorProject.model_validate(after.project).timelines[0].clips) == 4
    assert result.project.document.shots == service.get_project(pid).document.shots


def test_sound_edit_then_second_save_preserve_other_group_parts_and_crossing_subtitle(
    whole_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = whole_group
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    local = read_shot_editorial(service, pid, "shot-yqzyd-02")
    assert local.scope and local.scope.anchor_clip_id
    anchor = local.scope.anchor_clip_id
    editor = EditorProject.model_validate(local.project)
    editor.timelines[0].clips[0].volume = .4
    save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=anchor, timeline={"project": _json(editor)}))
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    target = next(clip for clip in after.clips if clip.id == anchor)
    assert (target.startTime, target.duration, target.trimStart, target.volume) == (6.5, 5.75, 6.5, .4)
    prefix = next(clip for clip in after.clips if (clip.model_extra or {}).get("sourceShotId") == "shot-yqzyd-01")
    assert (prefix.startTime, prefix.duration, prefix.trimStart, prefix.volume) == (0, 6.5, 0, 1)
    assert {cue.id: cue for cue in after.subtitles} == {cue.id: cue for cue in before.timelines[0].subtitles}
    for old in before.timelines[0].clips[1:]:
        assert next(clip for clip in after.clips if clip.id == old.id) == old
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    reopened = read_shot_editorial(restarted, pid, "shot-yqzyd-02", anchor)
    assert reopened.scope and reopened.scope.anchor_clip_id == anchor
    second = EditorProject.model_validate(reopened.project)
    second.timelines[0].clips[0].muted = True
    save_shot_editorial(restarted, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=reopened.revision,
        anchor_clip_id=anchor, timeline={"project": _json(second)}))
    final = EditorProject.model_validate(read_editorial(restarted, pid).project).timelines[0]
    assert len(final.clips) == 5
    assert max(clip.startTime + clip.duration for clip in final.clips) == 41.625
    assert next(clip for clip in final.clips if clip.id == anchor).muted


def test_shortening_moves_later_parts_once_and_preserves_unseen_subtitle_edges(
    whole_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = whole_group
    local = read_shot_editorial(service, pid, "shot-yqzyd-02")
    assert local.scope
    editor = EditorProject.model_validate(local.project)
    editor.timelines[0].clips[0].duration -= 1
    save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": _json(editor)}))
    final = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    assert max(clip.startTime + clip.duration for clip in final.clips) == 40.625
    assert next(clip for clip in final.clips if clip.id == "clip-shot-yqzyd-03").startTime == 11.25
    assert [(cue.id, cue.startTime, cue.endTime) for cue in final.subtitles] == [("later", 19, 21), ("crossing", 6, 8)]


@pytest.mark.parametrize("reversed_playback", [False, True])
def test_speed_reverse_and_linked_sound_keep_source_and_envelope(
    whole_group: tuple[ShortDramaService, str], reversed_playback: bool,
) -> None:
    service, pid = whole_group
    current = read_editorial(service, pid)
    full = EditorProject.model_validate(current.project)
    video = full.timelines[0].clips[0]
    video.duration, video.speed, video.reversed = 6.125, 2, reversed_playback
    video.volume, video.audioFadeInSeconds, video.audioFadeOutSeconds = .6, .2, .3
    video.audioEnvelopeOffsetSeconds, video.audioEnvelopeDurationSeconds = .4, 7
    assert video.model_extra is not None
    video.model_extra["linkedClipIds"] = ["linked-audio"]
    audio = video.model_copy(deep=True, update={"id": "linked-audio", "type": "audio"})
    assert audio.model_extra is not None
    audio.model_extra["linkedClipIds"] = [video.id]
    full.timelines[0].clips.append(audio)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": _json(full)}))
    local = read_shot_editorial(service, pid, "shot-yqzyd-02")
    assert local.scope
    start = 0 if reversed_playback else 3.25
    assert (local.scope.film_start_seconds, local.scope.duration_seconds) == (start, 2.875)
    edited = EditorProject.model_validate(local.project)
    for clip in edited.timelines[0].clips:
        assert (clip.trimStart, clip.duration, clip.speed, clip.reversed) == (6.5, 2.875, 2, reversed_playback)
        assert clip.audioEnvelopeOffsetSeconds == pytest.approx(.4 + start)
        assert clip.audioEnvelopeDurationSeconds == 7
        clip.volume = .5
    save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": _json(edited)}))
    final = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    for kind in ["video", "audio"]:
        pieces = [clip for clip in final.clips if clip.assetId == video.assetId and clip.type == kind]
        assert sorted((clip.trimStart, clip.trimStart + clip.duration * clip.speed) for clip in pieces) == [(0, 6.5), (6.5, 12.25)]
        assert sum(clip.duration for clip in pieces) == 6.125
        for clip in pieces:
            links = (clip.model_extra or {}).get("linkedClipIds")
            assert isinstance(links, list) and len(links) == 1
            pair = next(item for item in final.clips if item.id == links[0])
            assert (pair.startTime, pair.duration, pair.trimStart) == (clip.startTime, clip.duration, clip.trimStart)
            assert clip.id in ((pair.model_extra or {}).get("linkedClipIds") or [])
            assert (clip.audioFadeInSeconds, clip.audioFadeOutSeconds, clip.audioEnvelopeDurationSeconds) == (.2, .3, 7)


def test_short_unedited_linked_sound_follows_retained_prefix(
    whole_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = whole_group
    current = read_editorial(service, pid)
    full = EditorProject.model_validate(current.project)
    video = full.timelines[0].clips[0]
    assert video.model_extra is not None
    video.model_extra["linkedClipIds"] = ["short-audio"]
    audio = video.model_copy(deep=True, update={"id": "short-audio", "type": "audio", "duration": 2})
    assert audio.model_extra is not None
    audio.model_extra["linkedClipIds"] = [video.id]
    full.timelines[0].clips.append(audio)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": _json(full)}))
    local = read_shot_editorial(service, pid, "shot-yqzyd-02")
    assert local.scope
    edit = EditorProject.model_validate(local.project)
    assert len(edit.timelines[0].clips) == 1
    edit.timelines[0].clips[0].volume = .5
    save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": _json(edit)}))
    final = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    saved_audio = next(clip for clip in final.clips if clip.id == audio.id)
    prefix = next(clip for clip in final.clips if clip.type == "video" and clip.startTime == 0)
    target = next(clip for clip in final.clips if clip.id == video.id)
    assert (saved_audio.startTime, saved_audio.duration, saved_audio.trimStart, saved_audio.volume) == (0, 2, 0, 1)
    assert (saved_audio.model_extra or {}).get("linkedClipIds") == [prefix.id]
    assert (prefix.model_extra or {}).get("linkedClipIds") == [audio.id]
    assert not (target.model_extra or {}).get("linkedClipIds")


def test_whole_group_transition_edges_are_kept_only_at_original_edges() -> None:
    timeline = EditorTimeline.model_validate(CASES[0]["timeline"])
    clip = timeline.clips[0]
    assert clip.model_extra is not None
    clip.model_extra.update(transitionIn={"type": "fade-to-black", "duration": .5},
                            transitionOut={"type": "dissolve", "duration": .5})
    prefix, target = slice_clip(clip, 0, 6.5), slice_clip(clip, 6.5, 12.25)
    assert (prefix.model_extra or {}).get("transitionIn") == {"type": "fade-to-black", "duration": .5}
    assert (target.model_extra or {}).get("transitionOut") == {"type": "dissolve", "duration": .5}
    assert not (prefix.model_extra or {}).get("transitionOut")
    assert not (target.model_extra or {}).get("transitionIn")
    with pytest.raises(ValueError, match="transitionIn.*转场内部"):
        slice_clip(clip, .25, 6.5)
    with pytest.raises(ValueError, match="transitionOut.*转场内部"):
        slice_clip(clip, 6.5, 12)


def test_reconfirm_original_take_does_not_persist_a_partition(whole_group: tuple[ShortDramaService, str]) -> None:
    service, pid = whole_group
    take = next(item for item in service.repository.list_takes(pid) if "shot-yqzyd-02" in item.selected_shot_ids)
    before = read_editorial(service, pid)
    service.select_take(take.id, shot_ids=["shot-yqzyd-02"], clip_ids=["clip-shot-yqzyd-01"],
                        expected_editorial_revision=before.revision)
    assert read_editorial(service, pid) == before


def test_candidate_replaces_secondary_shot_only_and_keeps_linked_prefix(whole_group: tuple[ShortDramaService, str]) -> None:
    service, pid = whole_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    video = editor.timelines[0].clips[0]
    assert video.model_extra is not None
    video.model_extra["linkedClipIds"] = ["original-audio"]
    audio = video.model_copy(deep=True, update={"id": "original-audio", "type": "audio"})
    assert audio.model_extra is not None
    audio.model_extra["linkedClipIds"] = [video.id]
    editor.timelines[0].clips.append(audio)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": _json(editor)}))
    old = next(item for item in service.repository.list_takes(pid) if "shot-yqzyd-02" in item.selected_shot_ids)
    candidate = old.model_copy(deep=True, update={"id": "candidate-for-shot2", "is_selected": False,
        "selected_shot_ids": [], "candidate_index": 2, "freshness": Freshness.CURRENT})
    service.repository.save_take(candidate)
    before = read_editorial(service, pid)
    original_takes = service.repository.list_takes(pid)
    result = service.tool_registry.invoke("video.select_take", {"take_id": candidate.id,
        "shot_ids": ["shot-yqzyd-02"], "clip_ids": [video.id], "expected_editorial_revision": before.revision,
        "source_in_seconds": 5, "source_out_seconds": 10.75})
    assert result.status.value == "SUCCEEDED", result.error
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    for kind in ("video", "audio"):
        target = next(clip for clip in after.clips if clip.id == (video.id if kind == "video" else audio.id))
        assert (target.startTime, target.duration, target.trimStart) == (6.5, 5.75, 5)
        assert (target.model_extra or {}).get("sourceTakeId") == candidate.id
        prefix = next(clip for clip in after.clips if clip.type == kind and clip.startTime == 0)
        assert (prefix.duration, prefix.trimStart) == (6.5, 0)
        assert (prefix.model_extra or {}).get("sourceTakeId") == old.id
    for untouched in editor.timelines[0].clips[1:-1]:
        assert next(clip for clip in after.clips if clip.id == untouched.id) == untouched
    assert after.subtitles == editor.timelines[0].subtitles
    assert max(clip.startTime + clip.duration for clip in after.clips) == 41.625
    # Failed mapping after the private partition cannot partially update SQLite.
    saved = read_editorial(service, pid)
    with pytest.raises(ValueError, match="必须等于"):
        service.select_take(candidate.id, shot_ids=["shot-yqzyd-02"], clip_ids=[video.id],
            expected_editorial_revision=saved.revision, source_in_seconds=5, source_out_seconds=11)
    assert read_editorial(service, pid) == saved
    assert len(service.repository.list_takes(pid)) == len(original_takes)


def test_director_split_secondary_shot_keeps_the_shared_prefix(whole_group: tuple[ShortDramaService, str]) -> None:
    from short_drama.director_content import remap_split_editorial
    service, pid = whole_group
    document = service.get_project(pid).document
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    source = next(shot for shot in document.shots if shot.id == "shot-yqzyd-02")
    replacements = [source.model_copy(deep=True, update={"id": f"shot2-part-{index}", "duration_seconds": 2.875}) for index in (1, 2)]
    remap_split_editorial(document, source, replacements, takes=service.repository.list_takes(pid))
    assert document.editorial_timeline
    after = EditorProject.model_validate(document.editorial_timeline["project"]).timelines[0]
    pieces = [clip for clip in after.clips if clip.assetId == before.timelines[0].clips[0].assetId]
    assert sorted((clip.startTime, clip.duration, clip.trimStart) for clip in pieces) == [(0, 6.5, 0), (6.5, 2.875, 6.5), (9.375, 2.875, 9.375)]
    for untouched in before.timelines[0].clips[1:]:
        assert next(clip for clip in after.clips if clip.id == untouched.id) == untouched
    assert [(cue.id, cue.text, cue.startTime, cue.endTime, cue.style) for cue in after.subtitles] == [
        (cue.id, cue.text, cue.startTime, cue.endTime, cue.style) for cue in before.timelines[0].subtitles]
    # Mapping a private copy did not persist an editorial or rewrite a Take.
    assert read_editorial(service, pid).project == _json(before)


def test_repeated_group_use_targets_only_the_explicit_occurrence(whole_group: tuple[ShortDramaService, str]) -> None:
    service, pid = whole_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    original = editor.timelines[0].clips[0]
    repeated = original.model_copy(deep=True, update={"id": "second-whole-group", "startTime": 43})
    editor.timelines[0].clips.append(repeated)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": _json(editor)}))
    local = read_shot_editorial(service, pid, "shot-yqzyd-02", repeated.id)
    assert local.scope and len(local.scope.occurrences) == 2
    assert (local.scope.film_start_seconds, local.scope.duration_seconds) == (49.5, 5.75)
    edited = EditorProject.model_validate(local.project)
    edited.timelines[0].clips[0].muted = True
    save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=repeated.id, timeline={"project": _json(edited)}))
    result = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    assert next(clip for clip in result.clips if clip.id == original.id) == original
    assert next(clip for clip in result.clips if clip.id == repeated.id).muted
    assert max(clip.startTime + clip.duration for clip in result.clips) == 55.25
    reopened = read_shot_editorial(service, pid, "shot-yqzyd-02", original.id)
    assert reopened.scope and len(reopened.scope.occurrences) == 2
    assert (reopened.scope.anchor_clip_id, reopened.scope.film_start_seconds, reopened.scope.duration_seconds) == (original.id, 6.5, 5.75)


def test_shared_window_rejects_expansion_into_other_shot_without_writes(whole_group: tuple[ShortDramaService, str]) -> None:
    service, pid = whole_group
    before = read_editorial(service, pid)
    takes = service.repository.list_takes(pid)
    local = read_shot_editorial(service, pid, "shot-yqzyd-02")
    assert local.scope
    editor = EditorProject.model_validate(local.project)
    clip = editor.timelines[0].clips[0]
    clip.trimStart = 0
    assert clip.model_extra is not None
    clip.model_extra.update(sourceWindowStart=0, sourceWindowEnd=12.25)
    with pytest.raises(ValueError, match="可用连续源区间"):
        save_shot_editorial(service, pid, "shot-yqzyd-02", StudioTimelineSave(expected_revision=local.revision,
            anchor_clip_id=local.scope.anchor_clip_id, timeline={"project": _json(editor)}))
    assert read_editorial(service, pid) == before
    assert service.repository.list_takes(pid) == takes


def test_take_index_uses_actual_path_for_reconfirm_and_replacement(whole_group: tuple[ShortDramaService, str]) -> None:
    service, pid = whole_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    video = editor.timelines[0].clips[0]
    asset = next(item for item in editor.assets if item.id == video.assetId)
    actual_path = asset.path
    assert asset.model_extra is not None and video.model_extra is not None
    asset.path = str(Path(actual_path).with_name("unused-base.mp4"))
    asset.model_extra["takes"] = [{"path": actual_path, "duration": 12.25}]
    video.model_extra["takeIndex"] = 0
    old = next(item for item in service.repository.list_takes(pid) if "shot-yqzyd-02" in item.selected_shot_ids)
    service.repository.save_take(old.model_copy(deep=True, update={"id": "registered-base", "media_path": asset.path,
        "is_selected": False, "selected_shot_ids": [], "candidate_index": 3}))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision, timeline={"project": _json(editor)}))
    before = read_editorial(service, pid)
    service.select_take(old.id, shot_ids=["shot-yqzyd-02"], clip_ids=[video.id], expected_editorial_revision=before.revision)
    assert read_editorial(service, pid) == before
    candidate = old.model_copy(deep=True, update={"id": "take-index-candidate", "is_selected": False,
        "selected_shot_ids": [], "candidate_index": 2, "freshness": Freshness.CURRENT})
    service.repository.save_take(candidate)
    service.select_take(candidate.id, shot_ids=["shot-yqzyd-02"], clip_ids=[video.id],
        expected_editorial_revision=before.revision, source_in_seconds=5, source_out_seconds=10.75)
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    target = next(clip for clip in after.clips if clip.id == video.id)
    assert (target.model_extra or {}).get("takeIndex") is None
    assert target.asset and target.asset.path == actual_path
    prefix = next(clip for clip in after.clips if clip.type == "video" and clip.startTime == 0)
    assert (prefix.model_extra or {}).get("takeIndex") == 0
