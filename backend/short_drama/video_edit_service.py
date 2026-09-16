"""Create and adopt a narrow lip-sync candidate without replacing a whole shot."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from pydantic import JsonValue
from short_drama.models import GenerationTask, OperationKind, ProjectStage, TaskStatus
from short_drama.studio_audio_selection import clip_audio_delegated
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorTrack
from short_drama.studio_trace import record_trace
from short_drama.video_edit_media import select_source, slice_clip, source_file
from short_drama.video_edit_models import LipSyncAdoptResponse, LipSyncRequest, LipSyncResponse, VideoEditRun
from short_drama.worker import utc_now

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def lip_sync_response(service: ShortDramaService, run_id: str, *, project_id: str | None = None) -> LipSyncResponse:
    run = service.repository.get_video_edit_run(run_id)
    if run is None or project_id is not None and run.project_id != project_id:
        raise KeyError(run_id)
    task = service.repository.get_task(run.task_id)
    if task is None:
        raise KeyError(run.task_id)
    warnings = [
        "历史口型修复记录只读保留；当前视频生成仅支持 MiniMax H3，不能继续执行或采用此任务。"
        if service.video_edit_worker.client_factory is None
        else "仅生成口型画面候选，原声和当前剪辑未自动替换；须实际检查人物、口型、动作与接缝后明确采用。"
    ]
    if run.status == "PAUSED":
        warnings.append("仅暂停本地等待，未确认停止供应商执行或计费。")
    return LipSyncResponse(task=task, run=run, warnings=warnings)


def enqueue_lip_sync(service: ShortDramaService, project_id: str, request: LipSyncRequest) -> LipSyncResponse:
    # All source/media checks precede the short transaction; no model/network I/O here.
    aggregate = service.get_project(project_id)
    document = aggregate.document
    if (document.editorial_timeline or {}).get("revision") != request.expected_editorial_revision:
        raise ValueError("当前剪辑已更新，请读取所选使用位置后重新发起修复。")
    selection = select_source(document, aggregate.takes, request)
    piece = slice_clip(selection.clip, request.start_seconds, request.end_seconds)
    # A partial fade cannot be reapplied independently without changing its ramp.
    for field in ("transitionIn", "transitionOut"):
        transition = cast(JsonValue, (selection.clip.model_extra or {}).get(field))
        if isinstance(transition, dict) and transition.get("type", "none") != "none":
            duration = transition.get("duration", 0)
            if isinstance(duration, (int, float)) and any(
                field == "transitionIn" and selection.clip.startTime < point < selection.clip.startTime + duration
                or field == "transitionOut" and selection.clip.startTime + selection.clip.duration - duration < point < selection.clip.startTime + selection.clip.duration
                for point in (request.start_seconds, request.end_seconds)):
                raise ValueError("选区边界不能切开已有转场；请保留完整外侧转场或将它包含在选区内。")
    files = [source_file(path) for path in dict.fromkeys([selection.use.media_path, selection.audio_path])]
    baseline_key = json.dumps({"request": request.model_dump(mode="json", exclude={"expected_editorial_revision"}),
        "baseline": selection.baseline, "files": [item.model_dump(mode="json") for item in files]}, sort_keys=True)
    with service.repository.atomic():
        current = service.repository.get_project(project_id)
        if current is None:
            raise KeyError(project_id)
        current_selection = select_source(current, service.repository.list_takes(project_id), request)
        if current_selection.baseline != selection.baseline:
            raise ValueError("准备请求期间所选使用位置已更新，请重新读取。")
        for prior in service.repository.list_video_edit_runs(project_id):
            task = service.repository.get_task(prior.task_id)
            prior_key = json.dumps({"request": prior.request.model_dump(mode="json", exclude={"expected_editorial_revision"}),
                "baseline": prior.baseline, "files": [item.model_dump(mode="json") for item in prior.source_files]}, sort_keys=True)
            if task and prior_key == baseline_key and prior.status not in {"FAILED", "CANCELLED"}:
                return lip_sync_response(service, prior.id)
        now = utc_now()
        task_id, run_id = f"task-{uuid4().hex}", f"video-edit-{uuid4().hex}"
        fingerprint = hashlib.sha256((baseline_key + task_id).encode()).hexdigest()
        shot = next(item for item in current.shots if item.id == request.shot_id)
        index = max((item.candidate_index for item in service.repository.list_tasks_for_shot(project_id, request.shot_id)), default=0) + 1
        task = GenerationTask(id=task_id, project_id=project_id, shot_id=request.shot_id, operation=OperationKind.RETAKE,
            candidate_index=index, status=TaskStatus.QUEUED, provider="kling", model="kling-lip-sync", provider_run_id=run_id,
            request_fingerprint=fingerprint, input_script_version_id=current.project.current_script_version_id,
            input_bible_version_id=current.project.current_bible_version_id, input_shot_revision=shot.revision,
            requested_duration_seconds=request.end_seconds - request.start_seconds, attempt=1, created_at=now, updated_at=now)
        run = VideoEditRun(id=run_id, task_id=task_id, project_id=project_id, shot_id=request.shot_id, request=request,
            source_take_id=selection.use.take_id, source_media_path=selection.use.media_path,
            source_in_seconds=piece.trimStart, source_out_seconds=piece.trimStart + piece.duration * piece.speed,
            source_speed=piece.speed, source_reversed=piece.reversed, source_files=files,
            target_clip=cast(dict[str, JsonValue], selection.clip.model_dump(mode="json")),
            target_track=cast(dict[str, JsonValue], selection.timeline.tracks[selection.clip.trackIndex].model_dump(mode="json")),
            audio_clip=cast(dict[str, JsonValue], selection.audio.model_dump(mode="json")),
            audio_track=cast(dict[str, JsonValue], selection.timeline.tracks[selection.audio.trackIndex].model_dump(mode="json")),
            audio_media_path=selection.audio_path, audio_asset_type=cast(Literal["video", "audio"], selection.audio_asset.type),
            baseline=selection.baseline, created_at=now, updated_at=now)
        service.repository.save_task(task)
        service.repository.save_video_edit_run(run)
        current.project.stage, current.project.updated_at = ProjectStage.GENERATING, now
        service.repository.save_project(current)
    record_trace("video.lip_sync.queued", {"project_id": project_id, "task_id": task_id, "run_id": run_id,
        "source_take_id": run.source_take_id, "timeline_id": request.timeline_id, "clip_id": request.clip_id,
        "start_seconds": request.start_seconds, "end_seconds": request.end_seconds, "intent": request.intent})
    return lip_sync_response(service, run_id)


def adopt_lip_sync(service: ShortDramaService, project_id: str, run_id: str) -> LipSyncAdoptResponse:
    response = lip_sync_response(service, run_id, project_id=project_id)
    run = response.run
    if run.adoption_status == "ADOPTED":
        return LipSyncAdoptResponse(run=run, project=service.get_project(project_id), notices=["此候选已采用；未重复切分。"])
    if run.status != "SUCCEEDED" or run.quality_status != "AWAITING_REVIEW" or not run.candidate_take_id:
        raise ValueError("需要已回收且通过技术检查的口型候选；未知、暂停或技术失败结果不能采用。")
    candidate = service.repository.get_take(run.candidate_take_id)
    if candidate is None or not Path(candidate.media_path).is_file():
        raise ValueError("候选媒体不可用，请先按原任务找回输出。")
    candidate_file = source_file(candidate.media_path)
    with service.repository.atomic():
        run = lip_sync_response(service, run_id, project_id=project_id).run
        if run.adoption_status == "ADOPTED":
            return LipSyncAdoptResponse(run=run, project=service.get_project(project_id), notices=["此候选已采用。"])
        if run.status != "SUCCEEDED" or run.quality_status != "AWAITING_REVIEW" or run.candidate_take_id != candidate.id:
            raise ValueError("候选状态已改变，请重新读取后决定是否采用。")
        changed = service.video_edit_worker.source_changed(run)
        if changed or source_file(candidate.media_path) != candidate_file:
            run.adoption_status = "SOURCE_CHANGED"
            run.adoption_notes = [changed or "候选文件已改变。", "已保留候选和当前人工内容；没有替换画面。"]
            run.updated_at = utc_now()
            service.repository.save_video_edit_run(run)
            return LipSyncAdoptResponse(run=run, project=service.get_project(project_id), notices=run.adoption_notes)
        document = service.repository.get_project(project_id)
        assert document is not None
        selected = select_source(document, service.repository.list_takes(project_id), run.request)
        editor, timeline, clip = selected.editor, selected.timeline, selected.clip
        start, end = run.request.start_seconds, run.request.end_seconds
        pieces: list[EditorClip] = []
        if start > clip.startTime + 1e-7:
            pieces.append(slice_clip(clip, clip.startTime, start, identity=f"{clip.id}-before-{run.id}"))
        repaired = slice_clip(clip, start, end)
        asset = EditorAsset.model_validate({"id": candidate.id, "path": candidate.media_path, "type": "video", "duration": candidate.duration_seconds,
            "resolution": f"{candidate.width}x{candidate.height}", "prompt": f"口型修复候选：{run.request.intent}",
            "createdAt": int(datetime_now_timestamp() * 1000)})
        values = repaired.model_dump(mode="json")
        values.update(assetId=candidate.id, asset=asset.model_dump(mode="json"), sourceTakeId=candidate.id,
            sourceWindowStart=0, sourceWindowEnd=end-start, trimStart=0, trimEnd=0, speed=1,
            reversed=False, muted=True, volume=0)
        values.pop("takeIndex", None)
        repaired = EditorClip.model_validate(values)
        pieces.append(repaired)
        if end < clip.startTime + clip.duration - 1e-7:
            pieces.append(slice_clip(clip, end, clip.startTime + clip.duration, identity=f"{clip.id}-after-{run.id}"))
        assets = {item.id: item for item in editor.assets}
        if not clip_audio_delegated(clip, timeline.clips, assets):
            # Preserve the *entire* original sound once, including unselected time,
            # gain, reverse/speed, envelope and original track audibility.
            track = timeline.tracks[clip.trackIndex]
            audio_id = f"{clip.id}-original-audio-{run.id}"
            audio_values = clip.model_dump(mode="json")
            audio_values.update(id=audio_id, type="audio", trackIndex=len(timeline.tracks),
                linkedClipIds=[item.id for item in pieces], transitionIn={"type": "none", "duration": 0},
                transitionOut={"type": "none", "duration": 0})
            audio = EditorClip.model_validate(audio_values)
            timeline.tracks.append(EditorTrack(id=f"original-audio-{run.id}", name="保留原声", kind="audio",
                enabled=track.enabled, muted=track.muted, solo=track.solo))
            timeline.clips.append(audio)
            for piece in pieces:
                extras = piece.model_extra
                assert extras is not None
                linked = extras.get("linkedClipIds", [])
                extras["linkedClipIds"] = [*linked, audio_id] if isinstance(linked, list) else [audio_id]
        editor.assets.append(asset)
        # Retain the original clip id on the repaired occurrence. Every other
        # timeline, subtitle, clip and original media object remains untouched.
        replaced: list[EditorClip] = []
        for current in timeline.clips:
            replaced.extend(pieces if current.id == clip.id else [current])
        timeline.clips = replaced
        saved = dict(document.editorial_timeline or {})
        saved["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json"))
        saved["revision"] = int(cast(int, saved.get("revision", 0))) + 1
        document.editorial_timeline = saved
        document.project.updated_at = utc_now()
        service.repository.save_project(document)
        candidate.selected_shot_ids = list(dict.fromkeys([*candidate.selected_shot_ids, run.shot_id]))
        candidate.is_selected = True
        service.repository.save_take(candidate)
        run.adoption_status, run.adopted_clip_ids = "ADOPTED", [item.id for item in pieces]
        run.adopted_at = run.updated_at = utc_now()
        run.adoption_notes = ["仅替换所选使用位置的画面区间；原音轨、字幕、未选画面和总时长保留。"]
        service.repository.save_video_edit_run(run)
    record_trace("video.lip_sync.adopted", {"project_id": project_id, "run_id": run_id,
        "candidate_take_id": candidate.id, "timeline_id": run.request.timeline_id,
        "clip_ids": cast(JsonValue, run.adopted_clip_ids), "start_seconds": start, "end_seconds": end})
    return LipSyncAdoptResponse(run=run, project=service.get_project(project_id), notices=run.adoption_notes)


def datetime_now_timestamp() -> float:
    from datetime import datetime, UTC
    return datetime.now(UTC).timestamp()
