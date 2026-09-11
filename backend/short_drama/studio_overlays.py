"""Place an existing graphic in the current editable timeline."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from pydantic import JsonValue

from short_drama.models import AssetMediaType
from short_drama.studio_editorial import EditorAsset, EditorClip, EditorProject, EditorTrack, read_editorial
from short_drama.studio_models import StudioAssetOverlay, StudioEditorial, StudioTimelineSave

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def add_asset_overlay(service: ShortDramaService, project_id: str, request: StudioAssetOverlay) -> StudioEditorial:
    from short_drama.studio_service import StudioContentConflict
    from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial

    current = (read_shot_editorial(service, project_id, request.shot_id, request.anchor_clip_id)
               if request.shot_id else read_editorial(service, project_id))
    project = EditorProject.model_validate(current.project)
    timeline = next(item for item in project.timelines if item.id == project.activeTimelineId)
    existing = next((clip for clip in timeline.clips if clip.id == request.overlay_id), None)
    if existing:
        if (existing.type == "image" and existing.assetId == request.asset_version_id
                and abs(existing.startTime - request.start_seconds) < 1e-6
                and abs(existing.duration - request.duration_seconds) < 1e-6):
            return current
        if existing.type != "image":
            raise ValueError("这个片段 ID 不属于图版，请使用图版自己的 ID。")
    if current.revision != request.expected_revision:
        raise StudioContentConflict("剪辑已更新，请读取当前修订后放入图版。")
    picture_end = max((clip.startTime + clip.duration for clip in timeline.clips
                       if clip.type in {"video", "image"}), default=0)
    if request.start_seconds + request.duration_seconds > picture_end + 1e-6:
        raise ValueError("图版时间超出了当前画面范围。")
    document = service.get_project(project_id).document
    media = next(((asset, version) for asset in document.assets for version in asset.versions
                  if version.id == request.asset_version_id), None)
    if media is None:
        raise ValueError("找不到指定的图版图片。")
    subject, version = media
    if version.media_type != AssetMediaType.IMAGE or not version.local_path:
        raise ValueError("图版需要本机可用的图片文件。")
    image = next((asset for asset in project.assets if asset.id == version.id), None)
    if image is None:
        image = EditorAsset.model_validate({"id": version.id, "path": version.local_path, "type": "image",
            "resolution": f"{version.width or 0}×{version.height or 0}",
            "sourceAssetVersionId": version.id, "prompt": subject.name,
            "width": version.width, "height": version.height,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000)})
        project.assets.append(image)
    track_index = existing.trackIndex if existing else next((index for index, track in enumerate(timeline.tracks)
                       if track.id.startswith("studio-graphics") and not track.locked
                       and track.enabled and not track.muted
                       and not any(clip.trackIndex == index
                                   and clip.startTime < request.start_seconds + request.duration_seconds
                                   and clip.startTime + clip.duration > request.start_seconds
                                   for clip in timeline.clips)), None)
    if track_index is None:
        track_index = len(timeline.tracks)
        timeline.tracks.append(EditorTrack(id=f"studio-graphics-{request.overlay_id}", name="图版", kind="video"))
    if existing:
        existing.assetId = image.id
        existing.asset = image
        existing.startTime = request.start_seconds
        existing.duration = request.duration_seconds
        existing.trimStart = 0
    else:
        clip = EditorClip.model_validate({"id": request.overlay_id, "assetId": image.id, "asset": image,
            "type": "image", "startTime": request.start_seconds, "duration": request.duration_seconds,
            "trimStart": 0, "trackIndex": track_index, "muted": True, "sourceShotId": request.shot_id})
        timeline.clips.append(clip)
    save = StudioTimelineSave(expected_revision=current.revision, anchor_clip_id=request.anchor_clip_id,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json", exclude_none=True))})
    if request.shot_id:
        save_shot_editorial(service, project_id, request.shot_id, save)
    else:
        service.studio.save_timeline(project_id, save)
    return read_editorial(service, project_id)
