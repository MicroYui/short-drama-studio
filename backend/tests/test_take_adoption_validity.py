"""Adoption uses current files and exact current shot/position source spans."""
from pathlib import Path

import pytest

from short_drama.models import DirectorRepairDecision, DirectorRepairStrategy, Freshness, GenerationUnitShotWindow, ShotReferenceCue, ShotTimelineBeat
from short_drama.reference_planner import DirectorRepairDecisionRequest, ReplacementShotDraft
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.mark.parametrize("file_state", ["missing", "empty", "directory"])
def test_adoption_checks_current_file_before_any_saved_change(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path, file_state: str,
) -> None:
    service, pid, ids = episode
    before = service.get_project(pid)
    original = next(take for take in before.takes if take.shot_id == ids[0])
    path = tmp_path / "unavailable-candidate.mp4"
    if file_state == "empty":
        path.touch()
    elif file_state == "directory":
        path.mkdir()
    candidate = original.model_copy(deep=True, update={"id": "candidate-invalid-file", "candidate_index": 40,
        "media_path": str(path), "is_selected": False, "selected_shot_ids": []})
    service.repository.save_take(candidate)
    with pytest.raises(WorkflowConflictError, match="candidate-invalid-file.*unavailable"):
        service.select_take(candidate.id)
    after = service.get_project(pid)
    assert after.document == before.document
    assert next(take for take in after.takes if take.id == original.id) == original
    assert next(take for take in after.takes if take.id == candidate.id).selected_shot_ids == []


def test_group_position_adoption_selects_only_the_shot_actually_replaced(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, ids = episode
    before = service.get_project(pid)
    original = next(take for take in before.takes if take.shot_id == ids[0])
    candidate = original.model_copy(deep=True, update={"id": "position-group-candidate", "candidate_index": 41,
        "generation_unit_id": "position-group-unit",
        "is_selected": False, "selected_shot_ids": [], "shot_windows": [
            GenerationUnitShotWindow(shot_id=shot_id, shot_revision=1, duration_seconds=4,
                source_in_seconds=index * 4, source_out_seconds=(index + 1) * 4)
            for index, shot_id in enumerate(ids[:2])]})
    service.repository.save_take(candidate)
    previous = EditorProject.model_validate(read_editorial(service, pid).project)
    clip = next(clip for clip in previous.timelines[0].clips if clip.type == "video"
        and (clip.model_extra or {}).get("sourceShotId") == ids[1])
    selected = service.select_take(candidate.id, clip_ids=[clip.id], expected_editorial_revision=1)
    assert next(take for take in selected.takes if take.id == candidate.id).selected_shot_ids == [ids[1]]
    assert next(take for take in selected.takes if take.id == original.id) == original
    after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    for old in previous.timelines[0].clips:
        if old.startTime == 4:
            continue
        assert next(clip for clip in after.clips if clip.id == old.id) == old


def test_adoption_rejects_position_ids_without_a_saved_timeline(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, ids = episode
    before = service.get_project(pid)
    before.document.editorial_timeline = None
    service.repository.save_project(before.document)
    take = next(take for take in before.takes if take.shot_id == ids[0])
    with pytest.raises(WorkflowConflictError, match="clip_ids"):
        service.select_take(take.id, clip_ids=[f"clip-{ids[0]}"])
    assert service.get_project(pid).document.editorial_timeline is None


@pytest.mark.parametrize("second_source_in", [2.0, 4.0])
def test_split_multiple_cuts_keep_one_source_origin_and_reject_uncovered_gaps(
    episode: tuple[ShortDramaService, str, list[str]], second_source_in: float,
) -> None:
    service, pid, ids = episode
    shot = service.get_project(pid).document.shots[0]
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    timeline = editor.timelines[0]
    video = next(clip for clip in timeline.clips if clip.type == "video"
        and (clip.model_extra or {}).get("sourceShotId") == ids[0])
    audio_id = (video.model_extra or {})["linkedClipIds"][0]
    audio = next(clip for clip in timeline.clips if clip.id == audio_id)
    for clip in (video, audio):
        clip.duration = 2
        tail = clip.model_copy(deep=True, update={"id": f"{clip.id}-tail", "startTime": 2,
            "trimStart": second_source_in, "linkedClipIds": [f"{audio.id if clip is video else video.id}-tail"]})
        timeline.clips.append(tail)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": as_json(editor)}))
    parts = [ReplacementShotDraft(id=f"{shot.id}-{index}", duration_seconds=duration,
        narrative_purpose=shot.narrative_purpose, visual=shot.visual, framing=shot.framing, camera=shot.camera,
        audio_intent=shot.audio_intent, dialogue_line_ids=shot.dialogue_line_ids if index == 0 else [],
        continuity=shot.continuity, transition_out=shot.transition_out, local_rework_risk=shot.local_rework_risk)
        for index, duration in enumerate((3, 1))]
    service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(
        decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.SPLIT_SHOT,
        reason="分开动作与反应并保留已剪好的素材", split_shots=parts))
    split = read_editorial(service, pid)
    divided = EditorProject.model_validate(split.project).timelines[0]
    clips = [clip for clip in divided.clips if (clip.model_extra or {}).get("sourceShotId") == parts[0].id]
    assert len(clips) == 4
    assert {(clip.model_extra or {}).get("sourceWindowStart") for clip in clips} == {0.0}
    original = next(take for take in service.get_project(pid).takes if take.shot_id == shot.id)
    candidate = original.model_copy(deep=True, update={"id": "split-multi-cut-candidate", "shot_id": parts[0].id,
        "candidate_index": 42, "freshness": Freshness.CURRENT, "is_selected": False, "selected_shot_ids": []})
    service.repository.save_take(candidate)
    if second_source_in == 4:
        with pytest.raises(WorkflowConflictError, match="无法覆盖"):
            service.select_take(candidate.id, expected_editorial_revision=split.revision)
        assert read_editorial(service, pid) == split
        assert service.repository.get_take(candidate.id) == candidate
    else:
        service.select_take(candidate.id, expected_editorial_revision=split.revision)
        after = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
        replaced = [clip for clip in after.clips if (clip.model_extra or {}).get("sourceShotId") == parts[0].id]
        assert sorted((clip.startTime, clip.trimStart, clip.duration) for clip in replaced) == [(0, 0, 2), (0, 0, 2), (2, 2, 1), (2, 2, 1)]


@pytest.mark.parametrize("explicit", [False, True])
def test_rewriting_beats_preserves_unmentioned_selected_media_unless_explicitly_replaced(
    tmp_path: Path, explicit: bool,
) -> None:
    from short_drama.director_content import preserve_repair_references
    service = ShortDramaService(database_path=tmp_path / "references.sqlite3", workspace_root=tmp_path / "media")
    source = service.initialize_fixture().document.shots[0]
    cues = [ShotReferenceCue(id=identifier, subject_asset_id=f"asset-{index}", asset_version_id=f"version-{index}",
        alias=f"参考{index}", purpose="已明确选用的参考", field="action", text_start=0, text_end=2,
        usage="EDITORIAL" if index == 2 else "GENERATION")
        for index, identifier in enumerate(("retained-new-beat", None, None))]
    source.references = []
    source.timeline_beats = [ShotTimelineBeat(start_seconds=0, end_seconds=source.duration_seconds,
        visual="人物走进来", framing="全景", camera="固定", action="人物走进来", references=cues)]
    target = source.model_copy(deep=True)
    target.references = []
    target.timeline_beats = [ShotTimelineBeat(start_seconds=0, end_seconds=source.duration_seconds,
        visual="人物停下", framing="近景", camera="固定", action="人物停下", references=[cues[0].model_copy(deep=True)])]
    preserve_repair_references(source, target, explicit=explicit)
    assert target.timeline_beats[0].references == [cues[0]]
    if explicit:
        assert target.references == []
    else:
        assert [cue.asset_version_id for cue in target.references] == ["version-1", "version-2"]
        assert [cue.usage for cue in target.references] == ["GENERATION", "EDITORIAL"]
        assert all(cue.field is None and cue.text_start is None and cue.text_end is None for cue in target.references)


@pytest.mark.parametrize("existing_duration", [None, 4.0, 2.0])
def test_truncated_candidate_uses_actual_available_window_and_allows_fitting_current_crops(
    episode: tuple[ShortDramaService, str, list[str]], existing_duration: float | None,
) -> None:
    service, pid, ids = episode
    before = service.get_project(pid)
    original = next(take for take in before.takes if take.shot_id == ids[0])
    candidate = original.model_copy(deep=True, update={"id": "short-group-candidate", "candidate_index": 43,
        "duration_seconds": 6.0, "generation_unit_id": "short-group", "is_selected": False, "selected_shot_ids": [],
        "shot_windows": [GenerationUnitShotWindow(shot_id=shot_id, shot_revision=1, duration_seconds=4,
            source_in_seconds=index * 4, source_out_seconds=(index + 1) * 4) for index, shot_id in enumerate(ids[:2])]})
    service.repository.save_take(candidate)
    if existing_duration is None:
        before.document.editorial_timeline = None
        service.repository.save_project(before.document)
    else:
        editor = EditorProject.model_validate(read_editorial(service, pid).project)
        for clip in editor.timelines[0].clips:
            if clip.startTime == 4:
                clip.duration = existing_duration
        service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": as_json(editor)}))
    previous = service.get_project(pid)
    if existing_duration != 2:
        with pytest.raises(WorkflowConflictError, match="覆盖"):
            service.select_take(candidate.id, shot_ids=[ids[1]])
        assert service.get_project(pid) == previous
        # The complete first window remains selectable even when another group
        # window is truncated; there is no blanket rejection of the whole Take.
        selected = service.select_take(candidate.id, shot_ids=[ids[0]])
        assert next(take for take in selected.takes if take.id == candidate.id).selected_shot_ids == [ids[0]]
    else:
        service.select_take(candidate.id, shot_ids=[ids[1]])
        editor = EditorProject.model_validate(read_editorial(service, pid).project)
        clips = [clip for clip in editor.timelines[0].clips if clip.startTime == 4]
        assert len(clips) == 2
        assert all(clip.trimStart == 4 and clip.duration == 2 and clip.trimEnd == 0 for clip in clips)


def test_shared_native_audio_is_rebased_once_when_multiple_video_cuts_reference_it(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, ids = episode
    aggregate = service.get_project(pid)
    old = next(take for take in aggregate.takes if take.shot_id == ids[0])
    candidate = old.model_copy(deep=True, update={"id": "shared-audio-group", "candidate_index": 44,
        "generation_unit_id": "shared-audio-unit", "is_selected": False, "selected_shot_ids": [],
        "shot_windows": [GenerationUnitShotWindow(shot_id=shot_id, shot_revision=1, duration_seconds=4,
            source_in_seconds=index * 4, source_out_seconds=(index + 1) * 4) for index, shot_id in enumerate(ids[:2])]})
    service.repository.save_take(candidate)
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    timeline = editor.timelines[0]
    video = next(clip for clip in timeline.clips if clip.type == "video"
        and (clip.model_extra or {}).get("sourceShotId") == ids[1])
    audio = next(clip for clip in timeline.clips if clip.id == (video.model_extra or {})["linkedClipIds"][0])
    video.duration = 2
    tail = video.model_copy(deep=True, update={"id": "tail-sharing-audio", "startTime": 6, "trimStart": 2})
    timeline.clips.append(tail)
    assert audio.model_extra is not None
    audio.model_extra["linkedClipIds"] = [video.id, tail.id]
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": as_json(editor)}))
    service.select_take(candidate.id, shot_ids=[ids[1]], expected_editorial_revision=2)
    clips = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0].clips
    assert next(clip for clip in clips if clip.id == audio.id).trimStart == 4
    assert next(clip for clip in clips if clip.id == video.id).trimStart == 4
    assert next(clip for clip in clips if clip.id == tail.id).trimStart == 6
