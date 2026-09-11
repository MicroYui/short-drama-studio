"""LTX editor projection. Only the current ProjectDocument owns saved edits."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from short_drama.models import ProjectAggregate, TakeAvailability
from short_drama.studio_models import StudioEditorial, StudioEditorialEdit

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class EditorAsset(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    path: str
    type: Literal["video", "image", "audio", "adjustment"]
    duration: float = 0
    # LTX's shared asset schema requires this string, including for audio and
    # replacement media whose dimensions have not been presented as a label.
    resolution: str = ""


class EditorTrack(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    muted: bool = False
    locked: bool = False
    enabled: bool = True
    solo: bool = False
    kind: Literal["video", "audio"] = "video"
    type: str = "default"


class EditorClip(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    assetId: str | None
    type: Literal["video", "image", "audio", "text", "adjustment"]
    startTime: float = Field(ge=0)
    duration: float = Field(gt=0)
    trimStart: float = Field(ge=0)
    trimEnd: float = Field(default=0, ge=0)
    speed: float = Field(default=1, ge=0.1, le=16)
    reversed: bool = False
    muted: bool = False
    volume: float = Field(default=1, ge=0, le=1)
    trackIndex: int = Field(ge=0)
    opacity: float = Field(default=100, ge=0, le=100)
    flipH: bool = False
    flipV: bool = False
    asset: EditorAsset | None = None


class EditorSubtitle(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    text: str
    startTime: float = Field(ge=0)
    endTime: float = Field(gt=0)
    trackIndex: int = Field(ge=0)
    style: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class EditorTimeline(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    tracks: list[EditorTrack]
    clips: list[EditorClip]
    subtitles: list[EditorSubtitle] = Field(default_factory=list[EditorSubtitle])


class EditorProject(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: Literal[2]
    id: str
    name: str
    assets: list[EditorAsset]
    timelines: list[EditorTimeline]
    activeTimelineId: str


def validate_editorial(service: ShortDramaService, project_id: str, value: dict[str, JsonValue]) -> EditorProject:
    from short_drama.studio_media import resolve_media_path
    project = EditorProject.model_validate(value)
    if project.id != project_id:
        raise ValueError("剪辑与当前集不匹配。")
    assets = {asset.id: asset for asset in project.assets}
    if len(assets) != len(project.assets):
        raise ValueError("素材标识重复。")
    if project.activeTimelineId not in {timeline.id for timeline in project.timelines}:
        raise ValueError("当前时间线不存在。")
    for asset in project.assets:
        if asset.type != "adjustment":
            resolve_media_path(service, asset.path, project_id)
    for timeline in project.timelines:
        if len({clip.id for clip in timeline.clips}) != len(timeline.clips):
            raise ValueError("同一时间线的片段标识重复。")
        for clip in timeline.clips:
            if clip.trackIndex >= len(timeline.tracks):
                raise ValueError("片段的轨道不存在。")
            if clip.type in {"video", "image", "audio"}:
                asset = assets.get(clip.assetId or "")
                if asset is None or asset.type != clip.type:
                    raise ValueError("片段没有绑定有效素材。")
                # Keep embedded LTX playback data equal to its exact asset binding.
                clip.asset = asset
                if asset.duration and clip.type != "image" and clip.trimStart + clip.duration * clip.speed > asset.duration + 0.1:
                    raise ValueError("片段裁切超出了原素材时长。")
        for subtitle in timeline.subtitles:
            if subtitle.endTime <= subtitle.startTime or subtitle.trackIndex >= len(timeline.tracks):
                raise ValueError("字幕的时间或轨道无效。")
    return project


def read_editorial(service: ShortDramaService, project_id: str) -> StudioEditorial:
    aggregate = service.get_project(project_id)
    saved = aggregate.document.editorial_timeline
    if saved and isinstance(saved.get("project"), dict):
        return StudioEditorial(revision=int(cast(int, saved.get("revision", 0))), project=cast(dict[str, JsonValue], saved["project"]))
    return StudioEditorial(revision=0, project=project_editorial(aggregate))


def edit_editorial(service: ShortDramaService, project_id: str, request: "StudioEditorialEdit") -> StudioEditorial:
    from short_drama.studio_models import StudioTimelineSave
    from short_drama.studio_service import StudioContentConflict
    from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
    current = read_shot_editorial(service, project_id, request.shot_id, request.anchor_clip_id) if request.shot_id else read_editorial(service, project_id)
    if current.revision != request.expected_revision:
        raise StudioContentConflict("剪辑已更新，请读取当前片段后再次修改。")
    project = EditorProject.model_validate(current.project)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    removed: set[str] = set()
    for change in request.clips:
        clip = next((item for item in timeline.clips if item.id == change.id), None)
        if clip is None:
            raise KeyError(change.id)
        linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", [])) if request.keep_linked_audio else []
        ids = {clip.id, *linked}
        if change.remove:
            removed.update(ids)
            continue
        audio = next((item for item in timeline.clips if item.id in ids and item.type == "audio"), clip)
        for item in timeline.clips:
            if item.id not in ids:
                continue
            if change.start_seconds is not None: item.startTime = change.start_seconds
            if change.source_in_seconds is not None: item.trimStart = change.source_in_seconds
            if change.speed is not None:
                if change.duration_seconds is None:
                    item.duration = item.duration * item.speed / change.speed
                item.speed = change.speed
            if change.duration_seconds is not None: item.duration = change.duration_seconds
            if item is audio:
                if change.volume is not None: item.volume = change.volume
                if change.muted is not None: item.muted = change.muted
    timeline.clips = [clip for clip in timeline.clips if clip.id not in removed]
    for clip in timeline.clips:
        if clip.model_extra and isinstance(clip.model_extra.get("linkedClipIds"), list):
            clip.model_extra["linkedClipIds"] = [item for item in clip.model_extra["linkedClipIds"] if item not in removed]
    save = StudioTimelineSave(expected_revision=request.expected_revision, anchor_clip_id=request.anchor_clip_id,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))})
    if request.shot_id:
        save_shot_editorial(service, project_id, request.shot_id, save)
    else:
        service.studio.save_timeline(project_id, save)
    return read_editorial(service, project_id)


def project_editorial(aggregate: ProjectAggregate, *, preview_shot_id: str | None = None) -> dict[str, JsonValue]:
    """Project existing Take windows once; subsequent opens use saved editor content."""
    document = aggregate.document
    now = int(datetime.now(UTC).timestamp() * 1000)
    manifest = next((item for item in document.manifests if item.id == document.project.current_manifest_id), None)
    if manifest is None and document.manifests:
        manifest = document.manifests[-1]
    assets: dict[str, dict[str, JsonValue]] = {}
    for subject in document.assets:
        for version in subject.versions:
            if not version.local_path or version.media_type.value.lower() not in {"video", "image", "audio"}:
                continue
            assets[version.id] = {"id": version.id, "sourceAssetVersionId": version.id, "path": version.local_path,
                "type": version.media_type.value.lower(), "prompt": subject.name, "resolution": "", "createdAt": now}
            if version.duration_seconds:
                assets[version.id]["duration"] = version.duration_seconds
    clips: list[dict[str, JsonValue]] = []
    tracks: list[dict[str, JsonValue]] = [
        {"id": "video-1", "name": "镜头", "kind": "video", "muted": False, "locked": False, "sourcePatched": True},
        {"id": "video-2", "name": "画面补充", "kind": "video", "muted": False, "locked": False},
        {"id": "audio-1", "name": "声音", "kind": "audio", "muted": False, "locked": False, "sourcePatched": True},
        {"id": "subtitles", "name": "字幕", "kind": "video", "type": "subtitle", "muted": False, "locked": False},
    ]
    cursor = 0.0
    for shot in sorted(document.shots, key=lambda item: item.sequence):
        old_clip = next((item for item in manifest.video_clips if item.shot_id == shot.id), None) if manifest else None
        take = next((item for item in aggregate.takes if item.is_selected and shot.id in item.selected_shot_ids), None)
        if take is None and old_clip:
            take = next((item for item in aggregate.takes if item.id == old_clip.take_id), None)
        if take is None and shot.id == preview_shot_id:
            # Opening a local editor may preview a returned video before adoption.
            # This projection does not select the Take or write the episode timeline.
            take = next((item for item in reversed(aggregate.takes)
                         if item.availability == TakeAvailability.AVAILABLE
                         and (item.shot_id == shot.id or any(window.shot_id == shot.id for window in item.shot_windows))), None)
        if take is None:
            cursor += shot.duration_seconds
            continue
        window = next((item for item in take.shot_windows if item.shot_id == shot.id), None)
        old_clip = old_clip if old_clip and old_clip.take_id == take.id else None
        trim = old_clip.source_in_seconds if old_clip else window.source_in_seconds if window else 0
        duration = old_clip.duration_seconds if old_clip else window.duration_seconds if window else min(shot.duration_seconds, take.duration_seconds)
        asset: dict[str, JsonValue] = {"id": take.id, "type": "video", "path": take.media_path,
            "duration": take.duration_seconds, "width": take.width, "height": take.height,
            "bigThumbnailPath": take.thumbnail_path, "smallThumbnailPath": take.thumbnail_path,
            "prompt": f"镜头 {shot.sequence:02d}", "resolution": f"{take.width}×{take.height}", "createdAt": now}
        assets[take.id] = asset
        clips.append({"id": f"clip-{shot.id}", "sourceShotId": shot.id, "sourceTakeId": take.id,
            "assetId": take.id, "asset": asset, "type": "video", "startTime": cursor,
            "duration": duration, "trimStart": trim, "trimEnd": max(0, take.duration_seconds - trim - duration),
            "trackIndex": 0, "muted": bool(manifest and any(item.role == "MASTER_AUDIO" or item.shot_id == shot.id and item.take_id == take.id for item in manifest.audio_clips))})
        cursor += duration
    if manifest:
        for clip in manifest.audio_clips:
            video = next((item for item in clips if item.get("sourceShotId") == clip.shot_id and item.get("sourceTakeId") == clip.take_id), None)
            if clip.shot_id and clip.take_id and video is None:
                continue
            shot = next((item for item in document.shots if item.id == clip.shot_id), None)
            label = f"镜头 {shot.sequence:02d} · 原声" if shot else "整集声音" if clip.role == "MASTER_AUDIO" else "声音素材"
            asset = {"id": clip.id, "path": clip.media_path, "type": "audio", "prompt": label,
                "resolution": "", "duration": next((take.duration_seconds for take in aggregate.takes if take.id == clip.take_id), clip.source_in_seconds + clip.duration_seconds), "createdAt": now}
            assets[clip.id] = asset
            audio_id = f"clip-{clip.id}"
            if video is not None:
                video["linkedClipIds"] = [audio_id]
            clips.append({"id": audio_id, "linkedClipIds": [video["id"]] if video else [], "assetId": clip.id, "asset": asset, "type": "audio",
                "startTime": clip.start_seconds, "duration": clip.duration_seconds, "trimStart": clip.source_in_seconds,
                "trimEnd": 0, "trackIndex": 2, "volume": min(1, 10 ** (clip.gain_db / 20))})
    subtitles: list[JsonValue] = [{"id": item.id, "text": item.text, "startTime": item.start_seconds,
        "endTime": item.end_seconds, "trackIndex": 3} for item in manifest.subtitle_cues] if manifest else []
    timeline: dict[str, JsonValue] = {"id": f"edit-{document.project.id}", "name": document.project.episode_title,
        "createdAt": now, "tracks": cast(list[JsonValue], tracks), "clips": cast(list[JsonValue], clips), "subtitles": subtitles}
    return {"version": 2, "id": document.project.id, "name": document.project.name, "createdAt": now,
        "updatedAt": now, "assets": cast(list[JsonValue], list(assets.values())), "bins": {},
        "timelines": [timeline], "activeTimelineId": timeline["id"]}
