"""An explicitly associated, trimmed H3 group keeps its real saved uses."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.models import AssetKind, Freshness, GenerationTask, QaStatus, Take
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media
from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
from short_drama.studio_shot_uses import shot_clip_uses, slice_clip
from tests.test_whole_group_shot_editorial import whole_group as whole_group


CASES: list[dict[str, Any]] = json.loads((Path(__file__).parent / "fixtures/studio-whole-group-uses.json").read_text())["cases"]
CASE = next(item for item in CASES if item["name"] == "daily_umbrella_trimmed_declared")
GROUP = "clip-ready-doorway-04-06"
SHOT5, SHOT6 = "shot-umbrella-05", "shot-umbrella-06"


def as_json(editor: EditorProject) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], editor.model_dump(mode="json"))


@pytest.fixture
def trimmed_group(whole_group: tuple[ShortDramaService, str]) -> tuple[ShortDramaService, str]:
    service, pid = whole_group
    document = service.get_project(pid).document
    document.shots = [document.shots[0].model_copy(deep=True, update={"id": sid, "sequence": i + 1})
                       for i, sid in enumerate(CASE["expected"])]
    service.repository.save_project(document)
    media = service.repository.list_takes(pid)[0].media_path
    for row in CASE["takes"]:
        take = Take.model_validate({**row, "project_id": pid, "media_path": media})
        service.repository.save_task(GenerationTask.model_validate({
            "id": take.task_id, "project_id": pid, "shot_id": take.shot_id, "operation": take.operation,
            "candidate_index": take.candidate_index, "status": "SUCCEEDED", "provider": "mock", "model": "local-fixture",
            "request_fingerprint": "local-trimmed-group", "input_script_version_id": document.script_versions[-1].id,
            "input_bible_version_id": document.bible_versions[-1].id, "requested_duration_seconds": take.duration_seconds,
            "attempt": 1, "created_at": take.created_at, "updated_at": take.created_at,
        }))
        service.repository.save_take(take)
    editor = EditorProject.model_validate({"version": 2, "id": pid, "name": "Fixed umbrella geometry",
        "createdAt": 0, "updatedAt": 0, "bins": {},
        "assets": [{**row, "path": media} for row in CASE["assets"]],
        "timelines": [copy.deepcopy(CASE["timeline"])], "activeTimelineId": CASE["timeline"]["id"]})
    # Snapshot r1 has no subtitles yet. Exercise preservation with independent
    # and cross-boundary saved cues, without claiming they came from that film.
    editor.timelines[0].subtitles = [
        EditorSubtitle(id="before", text="前段字幕", startTime=10, endTime=11, trackIndex=3),
        EditorSubtitle(id="crossing", text="跨过两个镜头的字幕", startTime=14.6, endTime=21, trackIndex=3,
                       style={"color": "#ffcc00"}),
        EditorSubtitle(id="after", text="末段字幕", startTime=23, endTime=24, trackIndex=3),
    ]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=read_editorial(service, pid).revision,
                                                       timeline={"project": as_json(editor)}))
    return service, pid


@pytest.mark.parametrize(("shot_id", "start", "duration"), [(SHOT5, 14.715, 5.5), (SHOT6, 20.215, 4.58)])
def test_trimmed_group_read_noop_save_and_reopen_do_not_insert(
    trimmed_group: tuple[ShortDramaService, str], shot_id: str, start: float, duration: float,
) -> None:
    service, pid = trimmed_group
    before = read_editorial(service, pid)
    local = read_shot_editorial(service, pid, shot_id)
    assert local.scope and local.scope.anchor_clip_id == GROUP
    assert local.scope.film_start_seconds == pytest.approx(start)
    assert local.scope.duration_seconds == pytest.approx(duration)
    assert len(local.scope.occurrences) == 1
    assert read_editorial(service, pid) == before
    result = service.tool_registry.invoke("timeline.save_editorial", {"project_id": pid, "shot_id": shot_id,
        "request": {"expected_revision": local.revision, "anchor_clip_id": GROUP, "timeline": {"project": local.project}}})
    assert result.status.value == "SUCCEEDED", result.error
    assert read_editorial(service, pid).project == before.project
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    again = read_shot_editorial(reopened, pid, shot_id, GROUP)
    assert again.scope and again.scope.duration_seconds == pytest.approx(duration)
    assert read_editorial(reopened, pid).project == before.project


def test_picture_edit_preserves_other_source_samples_independent_pcm_and_subtitles(
    trimmed_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = trimmed_group
    before = read_editorial(service, pid)
    full = EditorProject.model_validate(before.project)
    baseline = start_export(service, pid, StudioExportRequest(expected_revision=before.revision, width=480, height=270, fps=24), background=False)
    assert baseline.status == "completed", baseline.error
    local = read_shot_editorial(service, pid, SHOT5)
    edit = EditorProject.model_validate(local.project)
    target = next(c for c in edit.timelines[0].clips if c.type == "video")
    target.flipH = True
    save_shot_editorial(service, pid, SHOT5, StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=GROUP, timeline={"project": as_json(edit)}))
    current = read_editorial(service, pid)
    after = EditorProject.model_validate(current.project)
    timeline = after.timelines[0]
    group = next(c for c in full.timelines[0].clips if c.id == GROUP)
    parts = sorted((c for c in timeline.clips if c.type == "video" and c.assetId == group.assetId), key=lambda c:c.startTime)
    assert [c.duration for c in parts] == pytest.approx([.125, 5.5, 4.58])
    assert [c.trimStart for c in parts] == [4.875, 5, 10.5]
    assert [c.flipH for c in parts] == [False, True, False]
    assert [c.startTime for c in parts] == pytest.approx([14.59, 14.715, 20.215])
    for part in parts:
        assert not (part.model_extra or {}).get("sourceShotIds")
        assert (part.model_extra or {}).get("sourceWindowStart") == part.trimStart
        assert part.muted == group.muted and part.volume == group.volume
    for old in full.timelines[0].clips:
        if old.id != GROUP:
            assert next(c for c in timeline.clips if c.id == old.id) == old
    assert {c.id:c for c in timeline.subtitles} == {c.id:c for c in full.timelines[0].subtitles}
    assert timeline.tracks == full.timelines[0].tracks
    assert max(c.startTime+c.duration for c in timeline.clips) == 24.795
    reopened = read_shot_editorial(service, pid, SHOT6)
    assert reopened.scope and reopened.scope.duration_seconds == pytest.approx(4.58)
    final = start_export(service, pid, StudioExportRequest(expected_revision=current.revision, width=480, height=270, fps=24), background=False)
    assert final.status == "completed", final.error
    def pcm(path: str | None) -> bytes:
        assert path
        return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path,
            "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
            capture_output=True, check=True, timeout=30).stdout
    assert pcm(baseline.video_path) == pcm(final.video_path)


@pytest.mark.parametrize("change", ["path", "take", "unknown_shot", "unselected", "duplicate", "outside", "non_video"])
def test_explicit_relation_save_rejects_wrong_binding_without_changing_saved_content(
    trimmed_group: tuple[ShortDramaService, str], change: str,
) -> None:
    service, pid = trimmed_group
    if change == "unselected":
        current = read_editorial(service, pid)
        unassociated = EditorProject.model_validate(current.project)
        original = next(c for c in unassociated.timelines[0].clips if c.id == GROUP)
        assert original.model_extra is not None
        original.model_extra.pop("sourceShotIds")
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
            timeline={"project": as_json(unassociated)}))
        take = service.repository.get_take(str(original.model_extra["sourceTakeId"]))
        assert take is not None
        take.selected_shot_ids = ["shot-umbrella-04"]
        service.repository.save_take(take)
    before = read_editorial(service, pid)
    editor = EditorProject.model_validate(before.project)
    clip = next(c for c in editor.timelines[0].clips if c.id == GROUP)
    assert clip.model_extra is not None
    if change == "path":
        asset = next(a for a in editor.assets if a.id == clip.assetId)
        imported = import_studio_media(service, pid, Path(asset.path), name="Different registered binding",
                                       kind=AssetKind.REFERENCE, asset_id=None)
        version = imported.document.assets[-1].versions[-1]
        assert version.local_path and version.local_path != asset.path
        asset.path = version.local_path
    elif change == "take":
        clip.model_extra["sourceTakeId"] = "unknown-take"
    elif change == "unknown_shot":
        clip.model_extra["sourceShotIds"] = ["shot-umbrella-04", "unknown-shot"]
    elif change == "unselected":
        clip.model_extra["sourceShotIds"] = ["shot-umbrella-04", SHOT5, SHOT6]
    elif change == "duplicate":
        clip.model_extra["sourceShotIds"] = ["shot-umbrella-04", "shot-umbrella-04"]
    elif change == "outside":
        clip.duration += 1
    else:
        clip.type = "audio"
    with pytest.raises(ValueError, match="sourceShotIds"):
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=before.revision,
                                                            timeline={"project": as_json(editor)}))
    assert read_editorial(service, pid) == before


@pytest.mark.parametrize("reversed_playback", [False, True])
def test_declared_film_split_retains_relation_and_maps_rate_reverse(reversed_playback: bool) -> None:
    case = copy.deepcopy(CASE)
    editor = EditorProject.model_validate({"version":2, "id":"fixture", "name":"fixture", "assets":case["assets"],
        "timelines":[case["timeline"]], "activeTimelineId":case["timeline"]["id"]})
    timeline = editor.timelines[0]
    clip = next(c for c in timeline.clips if c.id == GROUP)
    clip.speed, clip.duration, clip.reversed = 2, clip.duration/2, reversed_playback
    takes = [Take.model_validate(t) for t in case["takes"]]
    before = {sid:shot_clip_uses(timeline, editor.assets, takes, sid) for sid in (SHOT5, SHOT6)}
    cut = clip.startTime + 3
    first, second = slice_clip(clip, clip.startTime, cut), slice_clip(clip, cut, clip.startTime+clip.duration)
    second.id += "-second"
    timeline.clips = [c for c in timeline.clips if c.id != GROUP]+[first,second]
    for part in (first,second):
        assert (part.model_extra or {}).get("sourceShotIds") == (clip.model_extra or {}).get("sourceShotIds")
    for sid, original in before.items():
        uses = shot_clip_uses(timeline, editor.assets, takes, sid)
        assert min(u.start for u in uses) == pytest.approx(original[0].start)
        assert max(u.end for u in uses) == pytest.approx(original[0].end)
        assert sum(u.end-u.start for u in uses) == pytest.approx(original[0].end-original[0].start)


def test_explicit_single_shot_window_takes_precedence_over_group_metadata(
    trimmed_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = trimmed_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    clip = next(c for c in editor.timelines[0].clips if c.id == GROUP)
    assert clip.model_extra is not None
    clip.model_extra.update(sourceWindowStart=5, sourceWindowEnd=10.5)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
                                                       timeline={"project": as_json(editor)}))
    owner = read_shot_editorial(service, pid, "shot-umbrella-04")
    assert owner.scope and owner.scope.duration_seconds == pytest.approx(10.205)
    # A single-shot observed edit can deliberately exceed its nominal window.
    # The extra group list cannot make other shots own that saved occurrence.
    for sid in (SHOT5, SHOT6):
        local = read_shot_editorial(service, pid, sid)
        assert local.scope and not local.scope.occurrences


def test_editing_one_declared_group_use_preserves_another_use(
    trimmed_group: tuple[ShortDramaService, str],
) -> None:
    service, pid = trimmed_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    group = next(c for c in editor.timelines[0].clips if c.id == GROUP)
    other = group.model_copy(deep=True, update={"id":"other-declared-use", "startTime":40})
    editor.timelines[0].clips.append(other)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
                                                       timeline={"project":as_json(editor)}))
    local = read_shot_editorial(service, pid, SHOT5, GROUP)
    assert local.scope and len(local.scope.occurrences) == 2
    changed = EditorProject.model_validate(local.project)
    changed.timelines[0].clips[0].flipH = True
    save_shot_editorial(service, pid, SHOT5, StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=GROUP, timeline={"project":as_json(changed)}))
    current = EditorProject.model_validate(read_editorial(service, pid).project)
    assert next(c for c in current.timelines[0].clips if c.id == other.id) == other
    again = read_shot_editorial(service, pid, SHOT5, other.id)
    assert again.scope and len(again.scope.occurrences) == 2
    assert (again.scope.film_start_seconds, again.scope.duration_seconds) == (40.125, 5.5)


def test_replacing_one_use_keeps_other_declared_use_and_its_film_split(
    trimmed_group: tuple[ShortDramaService, str], tmp_path: Path,
) -> None:
    service, pid = trimmed_group
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    group = next(c for c in timeline.clips if c.id == GROUP)
    other = group.model_copy(deep=True, update={"id": "other-declared-use", "startTime": 40})
    timeline.clips.append(other)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": as_json(editor)}))
    before = read_editorial(service, pid)
    source = service.repository.get_take(str((group.model_extra or {})["sourceTakeId"]))
    assert source is not None
    media = tmp_path / "replacement.mp4"
    shutil.copyfile(source.media_path, media)
    candidate = source.model_copy(deep=True, update={"id": "replacement-take", "task_id": "replacement-task",
        "shot_id": SHOT5, "shot_ids": [SHOT5], "shot_windows": [], "media_path": str(media),
        "duration_seconds": 6.0, "selected_shot_ids": [], "is_selected": False, "output_slot_id": None,
        "qa_status": QaStatus.PENDING, "freshness": Freshness.CURRENT})
    source_task = service.repository.get_task(source.task_id)
    assert source_task is not None
    service.repository.save_task(source_task.model_copy(deep=True, update={"id": candidate.task_id,
        "shot_id": SHOT5, "shot_ids": [SHOT5], "requested_duration_seconds": 6.0}))
    service.repository.save_take(candidate)
    service.select_take(candidate.id, shot_ids=[SHOT5], clip_ids=[GROUP],
        expected_editorial_revision=before.revision, source_in_seconds=.25, source_out_seconds=5.75,
        audio_policy="KEEP_CURRENT_AUDIO")
    old_take = service.repository.get_take(source.id)
    assert old_take is not None and SHOT5 not in old_take.selected_shot_ids
    after = read_editorial(service, pid)
    adopted = EditorProject.model_validate(after.project)
    assert next(c for c in adopted.timelines[0].clips if c.id == other.id) == other
    for clip in timeline.clips:
        if clip.id != GROUP:
            assert next(c for c in adopted.timelines[0].clips if c.id == clip.id) == clip
    assert adopted.timelines[0].subtitles == timeline.subtitles
    assert adopted.timelines[0].tracks == timeline.tracks
    local = read_shot_editorial(service, pid, SHOT5, other.id)
    assert local.scope and len(local.scope.occurrences) == 2
    assert (local.scope.film_start_seconds, local.scope.duration_seconds) == (40.125, 5.5)
    save_shot_editorial(service, pid, SHOT5, StudioTimelineSave(expected_revision=local.revision,
        anchor_clip_id=other.id, timeline={"project": local.project}))
    assert read_editorial(service, pid).project == after.project

    # An ordinary split and copy carry the same saved relationship, even when
    # the new clip ID cannot match the original occurrence.
    cut = other.startTime + 3
    left, right = slice_clip(other, other.startTime, cut), slice_clip(other, cut, other.startTime + other.duration)
    right.id = "new-right-id"
    copied = left.model_copy(deep=True, update={"id": "new-copy-id", "startTime": 60})
    adopted.timelines[0].clips = [c for c in adopted.timelines[0].clips if c.id != other.id] + [left, right, copied]
    current = read_editorial(service, pid)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": as_json(adopted)}))
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    uses = read_shot_editorial(reopened, pid, SHOT5, right.id)
    assert uses.scope and uses.scope.film_start_seconds == 40.125
    assert uses.scope.duration_seconds == 5.5
    saved = EditorProject.model_validate(read_editorial(reopened, pid).project)
    for part in (left, right, copied):
        assert next(c for c in saved.timelines[0].clips if c.id == part.id) == part
