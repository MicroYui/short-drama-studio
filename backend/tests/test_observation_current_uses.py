"""Actual local frames/audio with deterministic semantic replies; no remote Provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.episode_review import review_scene
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import AudioContinuityAssessment, AudioInspectionRequest, EnqueueRequest, EpisodeReviewDimension, EpisodeReviewFinding, EpisodeReviewRequest, ProjectDocument, QaIssue, SemanticReviewStatus, SpeakerVoiceContinuityCheck, Take, VideoInspectionRequest
from short_drama.observation_sources import audio_report_evidence, episode_report_is_current, video_evidence_for_use, video_report_source_status
from short_drama.production_media_context import previous_adopted_media_boundary
from short_drama.reference_planner import DirectorAdviceRequest
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorClip, EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_episode_review import editorial_episode_input
from short_drama.studio_models import StudioTimelineSave
from tests.test_audio_inspection import FakeAudioSemanticService, FakeDirectorLanguageProvider
from tests.test_video_inspection import FakeInspectionLanguageProvider


def _project(path: Path, count: int = 2) -> tuple[ShortDramaService, ProjectDocument, EditorProject, list[Take]]:
    service = ShortDramaService(database_path=path / "state.sqlite3", workspace_root=path / "media")
    document = service.initialize_fixture().document
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[shot.id for shot in document.shots[:count]], candidate_count=1))
    result = service.drain(document.project.id, 100).project
    editor = EditorProject.model_validate(read_editorial(service, document.project.id).project)
    _save(service, result.document, editor)
    return service, service.get_project(document.project.id).document, editor, result.takes


def _save(service: ShortDramaService, document: ProjectDocument, editor: EditorProject) -> None:
    service.studio.save_timeline(document.project.id, StudioTimelineSave(
        expected_revision=read_editorial(service, document.project.id).revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))


def _clips(editor: EditorProject) -> list[EditorClip]:
    return sorted((item for item in editor.timelines[0].clips if item.type == "video"), key=lambda item: item.startTime)


@pytest.mark.parametrize("trim,duration,speed,reversed_", [(0., 1., 1., False), (1., 1., 2., False), (1., 1., 2., True)])
def test_editorial_boundary_uses_actual_cut_and_playback_direction(
    tmp_path: Path, trim: float, duration: float, speed: float, reversed_: bool,
) -> None:
    service, document, editor, takes = _project(tmp_path)
    first, second = _clips(editor)
    first.trimStart, first.duration, first.speed, first.reversed = trim, duration, speed, reversed_
    second.startTime, second.trimStart, second.duration, second.speed, second.reversed = duration, 1., 1., 2., True
    _save(service, document, editor)
    by_shot = {take.shot_id: take for take in takes}
    previous, current = by_shot[document.shots[0].id], by_shot[document.shots[1].id]
    report = service.inspect_take_video(current.id, VideoInspectionRequest(scope="EDITORIAL",
        timeline_id=editor.timelines[0].id, clip_id=second.id, semantic_review=False)).report
    assert report.observation_scope == "EDITORIAL" and report.editorial_context is not None
    assert report.observed_source_in_seconds == 1 and report.observed_source_out_seconds == 3
    before = [item for item in report.samples if "PREVIOUS_SHOT_END" in item.reasons]
    after = [item for item in report.samples if "CURRENT_SHOT_START" in item.reasons]
    assert len(before) == len(after) == 2
    assert all(item.source_take_id == previous.id and trim <= item.timestamp_seconds <= trim + duration * speed for item in before)
    assert (before[0].timestamp_seconds > before[1].timestamp_seconds) == reversed_
    endpoint = trim if reversed_ else trim + duration * speed
    assert abs(before[-1].timestamp_seconds - endpoint) < .2
    assert after[0].timestamp_seconds > after[1].timestamp_seconds and 2 < after[-1].timestamp_seconds < 3
    assert all(Path(item.image_path).is_file() for item in before + after)
    original = service.inspect_take_video(current.id, VideoInspectionRequest(semantic_review=False)).report
    assert original.observation_scope == "TAKE" and original.editorial_context is None
    assert max(item.timestamp_seconds for item in original.samples if item.source_take_id == previous.id) > 4.8


def test_editorial_reorder_duplicate_uses_and_explicit_nonactive_timeline(tmp_path: Path) -> None:
    service, document, editor, takes = _project(tmp_path, 3)
    first, second, third = _clips(editor)
    first.duration = second.duration = third.duration = 1
    third.startTime, first.startTime, second.startTime = 0, 1, 2
    duplicate = first.model_copy(deep=True, update={"id": "repeated-use", "startTime": 4.})
    editor.timelines[0].clips.append(duplicate)
    other = editor.timelines[0].model_copy(deep=True, update={"id": "other-timeline"})
    editor.timelines.append(other)
    _save(service, document, editor)
    take = next(item for item in takes if item.shot_id == document.shots[0].id)
    with pytest.raises(WorkflowConflictError, match="multiple uses"):
        service.inspect_take_video(take.id, VideoInspectionRequest(scope="EDITORIAL", semantic_review=False))
    request = VideoInspectionRequest(scope="EDITORIAL", timeline_id=other.id, clip_id=first.id, semantic_review=False)
    report = service.inspect_take_video(take.id, request).report
    assert report.editorial_context is not None and report.editorial_context.previous is not None
    assert report.editorial_context.current.timeline_id == other.id
    assert report.editorial_context.previous.shot_id == document.shots[2].id
    assert read_editorial(service, document.project.id).project["activeTimelineId"] == editor.activeTimelineId
    current = service.get_project(document.project.id).document
    assert video_report_source_status(current, take, report, {item.id: item for item in takes})[0] == "CURRENT"
    own = report.editorial_context.current
    other_use = own.model_copy(update={"clip_id": duplicate.id, "timeline_start_seconds": 4., "timeline_end_seconds": 5.})
    evidence = video_evidence_for_use(current, take, [report], {item.id: item for item in takes},
        own.source_in_seconds, own.source_out_seconds, other_use)
    assert cast(list[dict[str, JsonValue]], evidence["reports"])[0]["source_status"] == "OUTSIDE_REQUESTED_USE"
    next(item for item in other.clips if item.id == third.id).trimStart = .5
    _save(service, document, editor)
    changed = service.get_project(document.project.id).document
    assert video_report_source_status(changed, take, report, {item.id: item for item in takes})[0] == "HISTORICAL_OR_UNVERIFIED"


def test_editorial_hidden_or_overlapping_predecessor_is_not_guessed(tmp_path: Path) -> None:
    service, document, editor, takes = _project(tmp_path)
    first, second = _clips(editor)
    first.duration, second.startTime, second.duration = 2, 1, 1
    _save(service, document, editor)
    take = next(item for item in takes if item.shot_id == document.shots[1].id)
    with pytest.raises(WorkflowConflictError, match="AMBIGUOUS_PREVIOUS_USES"):
        service.inspect_take_video(take.id, VideoInspectionRequest(scope="EDITORIAL", clip_id=second.id, semantic_review=False))
    isolated = service.inspect_take_video(take.id, VideoInspectionRequest(scope="EDITORIAL", clip_id=second.id,
        include_previous_boundary=False, semantic_review=False)).report
    assert isolated.previous_take_id is None
    with pytest.raises(ValueError, match="resolves source times"):
        VideoInspectionRequest(scope="EDITORIAL", clip_id=second.id, source_out_seconds=1)


def test_scene_review_keeps_full_and_focused_reports_and_discards_stale_sources(tmp_path: Path) -> None:
    service, document, editor, takes = _project(tmp_path)
    first_take = next(item for item in takes if item.shot_id == document.shots[0].id)
    full = service.inspect_take_video(first_take.id, VideoInspectionRequest(semantic_review=False)).report.model_copy(
        update={"semantic_status": SemanticReviewStatus.COMPLETE})
    focused = service.inspect_take_video(first_take.id, VideoInspectionRequest(semantic_review=False,
        source_in_seconds=2, source_out_seconds=3, review_focus="Only the prop")).report.model_copy(
        update={"semantic_status": SemanticReviewStatus.COMPLETE})
    lookup = {item.id: item for item in takes}
    evidence = video_evidence_for_use(document, first_take, [full, focused], lookup, 0, first_take.duration_seconds)
    assert len(cast(list[JsonValue], evidence["reports"])) == 2
    assert evidence["unobserved_general_review_ranges"] == []
    only_local = video_evidence_for_use(document, first_take, [focused], lookup, 0, first_take.duration_seconds)
    assert only_local["unobserved_general_review_ranges"] == [{"source_in_seconds": 0, "source_out_seconds": first_take.duration_seconds}]
    manifest_input = editorial_episode_input(document, "2026-09-16T00:00:00Z")
    assert manifest_input is not None
    manifest, _revision, value = manifest_input
    fake = FakeInspectionLanguageProvider(['{"observations":[],"findings":[],"summary":"Scoped evidence."}'] * 2)
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    scene = script.structured_scenes[0]
    review_scene(document=document, manifest=manifest, scene=scene, scene_sequence=1, shots=[document.shots[0]],
        takes_by_id=lookup, latest_inspection_by_take={first_take.id: focused}, client=fake,
        editorial_project=value, video_inspections=[full, focused])
    payload = json.loads(fake.generations[0][1])
    received = payload["shot_evidence"][0]["video_inspections"]["reports"]
    assert [item["id"] for item in received] == [full.id, focused.id]
    assert received[1]["observed_source_in_seconds"] == 2 and received[1]["review_focus"] == "Only the prop"
    document.shots[2].revision += 1
    assert video_report_source_status(document, first_take, full, lookup)[0] == "CURRENT"
    document.shots[0].revision += 1
    review_scene(document=document, manifest=manifest, scene=scene, scene_sequence=1, shots=[document.shots[0]],
        takes_by_id=lookup, latest_inspection_by_take={first_take.id: focused}, client=fake,
        editorial_project=value, video_inspections=[full, focused])
    stale_payload = json.loads(fake.generations[1][1])
    assert not stale_payload["image_map"]
    assert all(item["source_status"] == "HISTORICAL_OR_UNVERIFIED" and "issues" not in item
               for item in stale_payload["shot_evidence"][0]["video_inspections"]["reports"])


def test_episode_review_cache_and_director_sources_follow_current_used_content(tmp_path: Path) -> None:
    service, document, _editor, takes = _project(tmp_path)
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    for shot in document.shots[:2]:
        shot.scene_id = script.structured_scenes[0].id
    service.repository.save_project(document)
    fake = FakeInspectionLanguageProvider(['{"observations":[],"findings":[],"summary":"Current scene."}'] * 2)
    service.language_provider_factory = lambda _key, _url: fake
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    first = service.review_current_episode(document.project.id, EpisodeReviewRequest(max_actions=1)).report
    assert episode_report_is_current(document, first, takes)
    document.shots[-1].revision += 1
    service.repository.save_project(document)
    assert episode_report_is_current(document, first, takes)
    document.shots[0].visual += " Changed prop state."
    document.shots[0].revision += 1
    service.repository.save_project(document)
    assert not episode_report_is_current(document, first, takes)
    assert episode_report_is_current(document, first, takes, shot_id=document.shots[1].id)
    second = service.review_current_episode(document.project.id, EpisodeReviewRequest(max_actions=1)).report
    assert second.id != first.id and len(fake.generations) == 2
    current = service.get_project(document.project.id).document
    second.findings = [EpisodeReviewFinding(id="paired-state", dimension=EpisodeReviewDimension.CHARACTER_PROP_STATE,
        issue=QaIssue(code="PAIRED_STATE", severity="HIGH", message="Historical pair conclusion.", repair_layer="EPISODE_DIRECTOR"),
        evidence="Depends on both shots.", shot_ids=[item.id for item in current.shots[:2]],
        recommended_action="Review the two cuts together.")]
    service.repository.save_episode_review(second)
    current.shots[0].revision += 1
    service.repository.save_project(current)
    director = FakeDirectorLanguageProvider()
    service.language_provider_factory = lambda _key, _url: director
    service.get_director_advice(current.project.id, current.shots[1].id, DirectorAdviceRequest(episode_review_id=second.id))
    payload = json.loads(director.inputs[0])
    assert payload["media_evidence"]["episode_review"]["historical_finding_ids"] == ["paired-state"]
    assert "Historical pair conclusion." not in director.inputs[0]
    current.shots[0].revision -= 1
    script = next(item for item in current.script_versions if item.id == current.project.current_script_version_id)
    scene = next(item for item in script.structured_scenes if item.id == current.shots[0].scene_id)
    scene.summary += " The prop now belongs to a different character."
    assert not episode_report_is_current(current, second, takes)
    assert not episode_report_is_current(current, second, takes, shot_id=current.shots[0].id)


def test_audio_evidence_preserves_unrelated_edits_and_rejects_changed_adoption(tmp_path: Path) -> None:
    service, document, editor, takes = _project(tmp_path, 3)
    service.audio_semantic_service = FakeAudioSemanticService()
    unrelated, first, _target = _clips(editor)
    protected_shot, target_shot = document.shots[1:3]
    first_take = next(item for item in takes if item.shot_id == protected_shot.id)
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    line = next(item for item in script.lines if item.id in protected_shot.dialogue_line_ids)
    editor.timelines[0].subtitles = [EditorSubtitle(id="observed-dialogue", text=line.subtitle_text,
        startTime=first.startTime + .2, endTime=first.startTime + first.duration - .2, trackIndex=3)]
    _save(service, document, editor)
    document = service.get_project(document.project.id).document
    report = service.inspect_current_manifest_audio(document.project.id, AudioInspectionRequest()).report
    report.speaker_checks = [SpeakerVoiceContinuityCheck(speaker=line.speaker, script_line_ids=ids,
        comparison_count=1, assessment=AudioContinuityAssessment.REVIEW, detail=detail)
        for ids, detail in [([line.id], "Current observed speaker sample."),
                            ([line.id, "unobserved-line"], "Historical speaker comparison must not leak.")]]
    service.repository.save_audio_inspection(report)
    evidence = audio_report_evidence(document, report, shot_id=protected_shot.id, take_id=first_take.id)
    assert evidence["source_status"] == "CURRENT" and evidence["dialogue_checks"]
    boundary = previous_adopted_media_boundary(document, target_shot_id=target_shot.id,
        editorial_project=editor.model_dump(mode="json"), takes=takes, inspections=[], audio_inspections=[report])
    assert boundary is not None and boundary["audio_observation_status"] == "CURRENT_AUDIO_ANALYSIS"
    unrelated.volume = .5
    _save(service, document, editor)
    current = service.get_project(document.project.id).document
    assert audio_report_evidence(current, report, shot_id=protected_shot.id, take_id=first_take.id)["source_status"] == "CURRENT"
    after = previous_adopted_media_boundary(current, target_shot_id=target_shot.id,
        editorial_project=editor.model_dump(mode="json"), takes=takes, inspections=[], audio_inspections=[report])
    assert after == boundary
    fake = FakeDirectorLanguageProvider()
    service.language_provider_factory = lambda _key, _url: fake
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    service.get_director_advice(document.project.id, protected_shot.id,
        DirectorAdviceRequest(take_id=first_take.id, audio_inspection_id=report.id))
    assert "Current observed speaker sample." in fake.inputs[0]
    assert "Historical speaker comparison must not leak." not in fake.inputs[0]
    first.trimStart, first.duration = 1, first.duration - 1
    for clip in editor.timelines[0].clips:
        if clip.type == "audio" and (clip.model_extra or {}).get("sourceTakeId") == first_take.id:
            clip.trimStart, clip.duration = 1, clip.duration - 1
    _save(service, document, editor)
    current = service.get_project(document.project.id).document
    assert audio_report_evidence(current, report, shot_id=protected_shot.id, take_id=first_take.id)["source_status"] == "HISTORICAL_OR_UNVERIFIED"
    service.get_director_advice(document.project.id, protected_shot.id,
        DirectorAdviceRequest(take_id=first_take.id, audio_inspection_id=report.id))
    payload = json.loads(fake.inputs[-1])
    audio = payload["media_evidence"]["audio_inspection"]
    assert audio["source_status"] == "HISTORICAL_OR_UNVERIFIED" and audio["clip_inspections"] == []


def test_audio_sources_ignore_same_clip_visual_edits_but_keep_sound_dependencies(tmp_path: Path) -> None:
    service, document, editor, takes = _project(tmp_path)
    protected = _clips(editor)[1]
    take = next(item for item in takes if item.shot_id == document.shots[1].id)
    timeline = editor.timelines[0]
    timeline.subtitles = [EditorSubtitle(id="sound-cue", text="对白", startTime=protected.startTime,
        endTime=protected.startTime + protected.duration, trackIndex=3)]
    _save(service, document, editor)
    report = service.inspect_current_manifest_audio(document.project.id,
        AudioInspectionRequest(semantic_review=False, speaker_continuity=False)).report
    observed = next(item for item in report.clip_inspections if item.take_id == take.id)
    clip = next(item for item in timeline.clips if item.id == observed.clip_id)
    # Compatibility: reports saved before narrowing contained these complete models.
    source_clips = cast(dict[str, dict[str, JsonValue]], report.source_context["clips"])
    source_clips[clip.id]["clip"] = clip.model_dump(mode="json")
    source_clips[clip.id]["subtitles"] = [cue.model_dump(mode="json") for cue in timeline.subtitles]
    clip.opacity, clip.flipH, clip.flipV = 25, True, True
    assert clip.model_extra is not None
    clip.model_extra.update({"transitionIn": {"type": "fade-to-black", "duration": .2},
        "transitionOut": {"type": "dissolve", "duration": .2}, "selected": True})
    timeline.subtitles[0].style = {"fontSize": 72, "color": "red"}
    timeline.tracks[clip.trackIndex].name = "Renamed in editor"
    _save(service, document, editor)
    current = service.get_project(document.project.id).document
    assert audio_report_evidence(current, report, shot_id=take.shot_id, take_id=take.id)["source_status"] == "CURRENT"
    previous_projection = report.model_copy(deep=True)
    previous_projection.source_context.pop("audio_projection")
    historical = audio_report_evidence(current, previous_projection, shot_id=take.shot_id, take_id=take.id)
    assert historical["source_status"] == "HISTORICAL_OR_UNVERIFIED"
    assert "channel/envelope projection" in str(historical["source_reasons"])

    for change in ({"volume": .4}, {"audioFadeInSeconds": .5}, {"audioFadeOutSeconds": .5},
                   {"audioEnvelopeOffsetSeconds": .2}, {"audioEnvelopeDurationSeconds": 10},
                   {"trimStart": .1}, {"speed": 1.1}, {"reversed": True}, {"muted": True}):
        changed = editor.model_copy(deep=True)
        index = next(index for index, item in enumerate(changed.timelines[0].clips) if item.id == clip.id)
        changed.timelines[0].clips[index] = clip.model_copy(update=change)
        candidate = current.model_copy(deep=True)
        assert candidate.editorial_timeline is not None
        candidate.editorial_timeline["project"] = changed.model_dump(mode="json")
        assert audio_report_evidence(candidate, report, shot_id=take.shot_id, take_id=take.id)["source_status"] == "HISTORICAL_OR_UNVERIFIED", change
    for change in ({"muted": True}, {"enabled": False}):
        changed = editor.model_copy(deep=True)
        changed.timelines[0].tracks[clip.trackIndex] = timeline.tracks[clip.trackIndex].model_copy(update=change)
        candidate = current.model_copy(deep=True)
        assert candidate.editorial_timeline is not None
        candidate.editorial_timeline["project"] = changed.model_dump(mode="json")
        assert audio_report_evidence(candidate, report, shot_id=take.shot_id, take_id=take.id)["source_status"] == "HISTORICAL_OR_UNVERIFIED", change
