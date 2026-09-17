"""Picture-only H3 responsibility and current soundtrack bindings, without providers."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.h3_models import H3ProviderConfigUpdate
from short_drama.h3_picture_source import assert_picture_binding_current, picture_only_binding, picture_prompt_projection, verify_retained_audio_files
from short_drama.language_models import LanguagePromptCompileRequest
from short_drama.models import AssetKind, H3PictureOnlyTarget, ProductionDocumentOrigin, QaStatus, TaskStatus
from short_drama.production_contracts import StoryboardPatchRequest, StoryboardShotSplice
from short_drama.providers.minimax_h3_compiler import build_h3_prompt
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave
from tests.test_h3_generation_unit_language import FakePromptCompiler, _fixture
from tests.test_h3_cross_shot_dialogue import _spoken_shot


@pytest.fixture
def picture_case(tmp_path: Path) -> tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget]:
    service, fake, document, _generation, _unit, _request = _fixture(tmp_path)
    pid = document.project.id
    board = service.get_storyboard(pid)
    originals = board.storyboard.shots[:2]
    line_id = originals[1].dialogue_line_ids[0]
    first = _spoken_shot(originals[0], originals[0].id, 1, [line_id], 2.5)
    second = _spoken_shot(originals[1], originals[1].id, 2, [line_id], 1.5)
    group = board.storyboard.clip_groups[0].model_copy(update={"duration_seconds": 4})
    service.patch_storyboard(pid, StoryboardPatchRequest(base_version_id=board.version.id,
        origin=ProductionDocumentOrigin.AGENT, shot_splices=[StoryboardShotSplice(shot_ids=[first.id, second.id], shots=[first, second])],
        clip_groups=[group]))
    movie, wav = tmp_path / "old.mp4", tmp_path / "original.wav"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=blue:s=320x180:r=24:d=6.58", "-f", "lavfi", "-i", "sine=frequency=440:duration=6.58",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(movie)], capture_output=True, check=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(movie), "-t", "3",
        "-vn", "-c:a", "pcm_s16le", str(wav)], capture_output=True, check=True)
    imported = import_studio_media(service, pid, movie, name="原组02+03", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    for shot in document.shots[:2]:
        current_shot = next(s for s in service.get_project(pid).document.shots if s.id == shot.id)
        select_imported_video(service, pid, shot.id, StudioShotMediaSelect(asset_version_id=version.id, expected_revision=current_shot.revision))
    imported_sound = import_studio_media(service, pid, wav, name="保留整句PCM", kind=AssetKind.REFERENCE, asset_id=None)
    sound = imported_sound.document.assets[-1].versions[-1]
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    timeline = editor.timelines[0]
    videos = sorted(timeline.clips, key=lambda c: c.startTime)
    assert len(videos) == 2
    videos[0].startTime, videos[0].duration, videos[0].trimStart = 6.166667, 1.958333, 0
    videos[1].startTime, videos[1].duration, videos[1].trimStart = 8.125, 2.541667, 3
    for clip in videos:
        clip.muted, clip.trimEnd = True, 0
    audio = EditorAsset(id=sound.id, type="audio", path=sound.local_path or "", duration=3)
    editor.assets = [a for a in editor.assets if a.id != audio.id] + [audio]
    timeline.clips.append(EditorClip(id="kept-pcm", assetId=audio.id, asset=audio, type="audio",
        startTime=6.166667, duration=3, trimStart=0, trackIndex=2))
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    document = service.get_project(pid).document
    current = read_editorial(service, pid)
    target = H3PictureOnlyTarget(mode="PICTURE_ONLY", audio_policy="KEEP_CURRENT_AUDIO", timeline_id=timeline.id,
        clip_id=videos[1].id, expected_editorial_revision=current.revision)
    projection, shot = picture_prompt_projection(document, next(s for s in document.shots if s.id == second.id), 4)
    fake.output_text = build_h3_prompt(projection, shot, duration_seconds=4)
    fake.calls.clear()
    return service, fake, pid, second.id, target


def test_picture_prepare_is_single_shot_and_reopens_without_new_group_or_dialogue(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget]) -> None:
    service, fake, pid, sid, target = picture_case
    before = service.get_project(pid).document
    with pytest.raises(ValueError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
        service.compile_h3_prompt_with_language_model(pid, LanguagePromptCompileRequest(shot_id=sid))
    prepared = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[]))
    assert prepared.status == "PREPARED", prepared.error
    assert prepared.shot_ids == [sid] and prepared.generation_unit_id is None
    assert len(fake.calls) == 1
    payload = json.loads(fake.calls[0][1])
    assert payload["locked_dialogue"] == [] and payload["dialogue_spans"] == []
    assert payload["picture_only"]["original_shot_context"]["dialogue_line_ids"]
    assert payload["picture_only"]["generated_dialogue"] == []
    assert "<d>" not in fake.output_text
    assert service.get_project(pid).document == before
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-only"))
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: fake)
    queued = restarted.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    assert len(queued.task_ids) == 1 and len(fake.calls) == 1
    task = restarted.repository.get_task(queued.task_ids[0])
    assert task is not None and task.picture_only is not None and task.covered_shot_ids() == [sid]
    assert task.requested_duration_seconds == 4
    assert restarted.h3_worker.input_staleness_reason(task) is None
    again = restarted.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert again.task_ids == queued.task_ids and len(fake.calls) == 1
    latest = restarted.get_project(pid).document
    assert latest.shots == before.shots and latest.production_documents == before.production_documents
    assert latest.editorial_timeline == before.editorial_timeline


@pytest.mark.parametrize("change", ["clip", "audio", "wrong_shot", "wrong_revision", "file_missing", "range"])
def test_picture_requires_actual_current_audio_and_target(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget], change: str) -> None:
    service, fake, pid, sid, target = picture_case
    document = service.get_project(pid).document
    editor = EditorProject.model_validate((document.editorial_timeline or {})["project"])
    if change == "wrong_shot":
        sid = document.shots[0].id
    elif change == "wrong_revision":
        target = target.model_copy(update={"expected_editorial_revision": 999})
    elif change == "file_missing":
        audio = next(a for a in editor.assets if a.id == next(c for c in editor.timelines[0].clips if c.id == "kept-pcm").assetId)
        Path(audio.path).unlink()
    else:
        for clip in editor.timelines[0].clips:
            if change == "clip" and clip.id == target.clip_id:
                assert clip.model_extra is not None
                clip.model_extra["sourceTakeId"] = "other-project-take"
            if clip.id == "kept-pcm":
                if change == "audio":
                    clip.muted = True
                elif change == "range":
                    clip.trimStart = 100
        document.editorial_timeline = {**(document.editorial_timeline or {}), "project": editor.model_dump(mode="json")}
        service.repository.save_project(document)
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[]))
    assert result.status == "BLOCKED" and result.error
    assert fake.calls == [] and not any(t.provider == "minimax" for t in service.repository.list_tasks(pid))


def test_changed_retained_pcm_blocks_saved_operation_before_enqueue(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget]) -> None:
    service, _fake, pid, sid, target = picture_case
    result = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[]))
    assert result.status == "PREPARED", result.error
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    next(c for c in editor.timelines[0].clips if c.id == "kept-pcm").volume = .5
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    changed = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=result.operation_id, enqueue=True))
    assert changed.status == "INPUTS_CHANGED" and changed.task_ids == []


def test_registered_binding_checks_real_audio_file_range(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget]) -> None:
    service, _fake, pid, sid, target = picture_case
    document = service.get_project(pid).document
    binding = picture_only_binding(document, sid, target, store=service.repository)
    verify_retained_audio_files(binding)
    row = binding.audio_sources[0]["clip"]
    assert isinstance(row, dict)
    row["trimStart"] = 30
    with pytest.raises(ValueError, match="container duration"):
        verify_retained_audio_files(binding)


def test_picture_binding_survives_pure_film_shift_and_retry_keeps_responsibility(
    picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget],
) -> None:
    service, fake, pid, sid, target = picture_case
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-only"))
    prepared = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[]))
    assert prepared.status == "PREPARED", prepared.error
    original = service.repository.get_h3_group_operation(prepared.operation_id)
    assert original is not None
    binding = picture_only_binding(service.get_project(pid).document, sid, target, store=service.repository)
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    for clip in editor.timelines[0].clips:
        clip.startTime += 2
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    assert_picture_binding_current(service.get_project(pid).document, binding, store=service.repository)
    queued = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    assert len(fake.calls) == 1 and len(queued.task_ids) == 1
    task = service.repository.get_task(queued.task_ids[0])
    assert task is not None and task.picture_only is not None
    assert service.h3_worker.input_staleness_reason(task) is None
    task.status = TaskStatus.FAILED
    service.repository.save_task(task)
    retried = service.retry_task(task.id)
    assert retried.picture_only == task.picture_only
    assert retried.covered_shot_ids() == [sid]


def test_picture_context_ir_is_explicitly_unsupported(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget]) -> None:
    from short_drama.providers.h3_context_ir import compile_h3_context_ir
    service, fake, pid, sid, target = picture_case
    with pytest.raises(ValueError, match="LANGUAGE compiler"):
        compile_h3_context_ir(service, pid, LanguagePromptCompileRequest(shot_id=sid, picture_only=target))
    assert fake.calls == []


@pytest.mark.parametrize("retry", [False, True])
def test_saved_preceding_cut_source_change_invalidates_picture_and_retries(
    picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget], retry: bool,
) -> None:
    service, fake, pid, sid, target = picture_case
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-only"))
    queued = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[], enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    operation = service.repository.get_h3_group_operation(queued.operation_id)
    assert operation is not None
    context_boundary = json.loads(fake.calls[0][1])["actual_previous_media_boundary"]
    stored_boundary = operation.input_context["actual_previous_media_boundary"]
    assert isinstance(stored_boundary, dict)
    assert stored_boundary["source_in_seconds"] == context_boundary["source_in_seconds"] == 0
    task = service.repository.get_task(queued.task_ids[0])
    assert task is not None
    if retry:
        for _ in range(2):
            task.status = TaskStatus.FAILED
            service.repository.save_task(task)
            task = service.retry_task(task.id)
        original_run = service.repository.get_provider_run(task.id)
        assert original_run is not None
        changed_run = original_run.model_copy(deep=True, update={"prompt": original_run.prompt + " changed"})
        mismatch = service.h3_worker.input_staleness_reason(task, changed_run)
        assert mismatch is not None and "compiled inputs" in mismatch
    assert service.h3_worker.input_staleness_reason(task) is None
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    previous = min((clip for clip in editor.timelines[0].clips if clip.type == "video"), key=lambda clip: clip.startTime)
    previous.trimStart += .25
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    reason = service.h3_worker.input_staleness_reason(task)
    assert reason is not None and "reference source changed" in reason
    resumed = service.prepare_h3_group(pid, H3GroupOperationRequest(operation_id=queued.operation_id, enqueue=True))
    assert resumed.status == "INPUTS_CHANGED"


def test_silent_video_is_allowed_without_claiming_a_dialogue_binding(
    picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget], tmp_path: Path,
) -> None:
    service, _fake, pid, sid, target = picture_case
    binding = picture_only_binding(service.get_project(pid).document, sid, target, store=service.repository)
    silent = tmp_path / "silent.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
        "color=blue:s=64x64:r=24:d=4", "-an", str(silent)], capture_output=True, check=True)
    row = binding.audio_sources[0]
    source, clip = row["source"], row["clip"]
    assert isinstance(source, dict) and isinstance(clip, dict)
    source["source_path"], clip["type"] = str(silent), "video"
    verify_retained_audio_files(binding, require_audio=False)
    with pytest.raises(ValueError, match="silent video"):
        verify_retained_audio_files(binding)


def test_generated_picture_candidate_adopts_only_father03_and_keeps_old02_pcm(picture_case: tuple[ShortDramaService, FakePromptCompiler, str, str, H3PictureOnlyTarget], tmp_path: Path) -> None:
    service, _fake, pid, sid, target = picture_case
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-only"))
    queued = service.prepare_h3_group(pid, H3GroupOperationRequest(shot_id=sid, picture_only=target, media_inputs=[], enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    old = next(t for t in service.repository.list_takes(pid) if t.shot_id == sid)
    path = tmp_path / "picture-candidate.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:s=320x180:r=24:d=4", "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], capture_output=True, check=True)
    take = old.model_copy(deep=True, update={"id": "picture-candidate", "task_id": queued.task_ids[0],
        "media_path": str(path), "duration_seconds": 4.0, "is_selected": False, "selected_shot_ids": [],
        "qa_status": QaStatus.PENDING})
    service.repository.save_take(take)
    before = read_editorial(service, pid)
    old_editor = EditorProject.model_validate(before.project)
    wrong = service.tool_registry.invoke("video.select_take", {"take_id": take.id, "clip_ids": [target.clip_id],
        "expected_editorial_revision": before.revision, "source_in_seconds": .5, "source_out_seconds": 3.041667})
    assert wrong.status.value == "FAILED" and wrong.error is not None and "KEEP_CURRENT_AUDIO" in wrong.error.message
    assert read_editorial(service, pid) == before
    result = service.tool_registry.invoke("video.select_take", {"take_id": take.id, "clip_ids": [target.clip_id],
        "expected_editorial_revision": before.revision, "source_in_seconds": .5, "source_out_seconds": 3.041667,
        "audio_policy": "KEEP_CURRENT_AUDIO"})
    assert result.status.value == "SUCCEEDED", result.error
    after = read_editorial(service, pid)
    updated = EditorProject.model_validate(after.project)
    for clip in old_editor.timelines[0].clips:
        new = next(c for c in updated.timelines[0].clips if c.id == clip.id)
        if clip.id == target.clip_id:
            assert (new.startTime, new.duration, new.trimStart, new.muted) == (8.125, 2.541667, .5, True)
            assert new.asset is not None and new.asset.path == str(path)
        else:
            assert new == clip
    assert updated.timelines[0].tracks == old_editor.timelines[0].tracks
    assert updated.timelines[0].subtitles == old_editor.timelines[0].subtitles
    service.select_take(take.id, clip_ids=[target.clip_id], expected_editorial_revision=after.revision,
        source_in_seconds=.5, source_out_seconds=3.041667, audio_policy="KEEP_CURRENT_AUDIO")
    assert read_editorial(service, pid) == after
