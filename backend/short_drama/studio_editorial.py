"""LTX editor projection. Only the current ProjectDocument owns saved edits."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from short_drama.models import ProjectAggregate, ProjectDocument, Take, TakeAvailability
from short_drama.studio_models import StudioEditorial, StudioEditorialEdit
from short_drama.studio_subtitle_fonts import default_subtitle_font_family

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
    assetId: str | None = None
    type: Literal["video", "image", "audio", "text", "adjustment"]
    startTime: float = Field(ge=0)
    duration: float = Field(gt=0)
    trimStart: float = Field(ge=0)
    trimEnd: float = Field(default=0, ge=0)
    speed: float = Field(default=1, ge=0.1, le=16)
    reversed: bool = False
    muted: bool = False
    volume: float = Field(default=1, ge=0, le=1)
    audioFadeInSeconds: float = Field(default=0, ge=0)
    audioFadeOutSeconds: float = Field(default=0, ge=0)
    audioEnvelopeOffsetSeconds: float | None = Field(default=None, ge=0)
    audioEnvelopeDurationSeconds: float | None = Field(default=None, gt=0)
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


def require_unambiguous_editorial_ids(project: EditorProject, *, clip_ids: list[str], subtitle_ids: list[str]) -> None:
    """Legacy active-timeline defaults cannot choose between equal object IDs."""
    ambiguous: list[str] = []
    for field, ids in (("clips", set(clip_ids)), ("subtitles", set(subtitle_ids))):
        for identity in sorted(ids):
            matches = [item.id for item in project.timelines
                       if any(row.id == identity for row in (item.clips if field == "clips" else item.subtitles))]
            if len(matches) > 1:
                ambiguous.append(f"{field} id={identity!r} in {matches!r}")
    if ambiguous:
        raise ValueError("/timeline_id is required for ambiguous object IDs: " + "; ".join(ambiguous)
                         + ". Copy the exact timeline_id from editor_selection/read_editorial.")


def editorial_clip_enabled(timeline: EditorTimeline, clip: EditorClip) -> bool:
    track = timeline.tracks[clip.trackIndex]
    return track.enabled and (not any(item.solo for item in timeline.tracks) or track.solo)


def clip_media_source(asset: EditorAsset, clip: EditorClip) -> tuple[str, float]:
    """Use the clip's concrete Take, matching the shared export adapter."""
    take_index = (clip.model_extra or {}).get("takeIndex")
    if take_index is None:
        return asset.path, asset.duration
    takes: object = (asset.model_extra or {}).get("takes")
    if not isinstance(takes, list):
        raise ValueError("片段所选 Take 不存在。")
    take_values = cast(list[object], takes)
    if (not isinstance(take_index, int) or isinstance(take_index, bool)
            or not 0 <= take_index < len(take_values)):
        raise ValueError("片段所选 Take 不存在。")
    take = take_values[take_index]
    if not isinstance(take, dict):
        raise ValueError("片段所选 Take 无效。")
    source = cast(dict[str, object], take)
    path = source.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("片段所选 Take 缺少媒体路径。")
    duration = source.get("duration", asset.duration)
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0:
        raise ValueError("片段所选 Take 的素材时长无效。")
    return path, float(duration)


def validate_editorial(service: ShortDramaService, project_id: str, value: dict[str, JsonValue],
                       *, purpose: Literal["save", "export"] = "save") -> EditorProject:
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
            # A library entry may be offline without making unrelated edits
            # unusable. It must still belong to this project.
            try:
                resolve_media_path(service, asset.path, project_id, require_exists=False)
            except ValueError as exc:
                raise ValueError(f"素材 {asset.id}：{exc}") from exc
    for timeline in project.timelines:
        if len({clip.id for clip in timeline.clips}) != len(timeline.clips):
            raise ValueError("同一时间线的片段标识重复。")
        if len({cue.id for cue in timeline.subtitles}) != len(timeline.subtitles):
            raise ValueError("同一时间线的字幕标识重复，请为不同字幕保留独立标识。")
        for clip in timeline.clips:
            if clip.trackIndex >= len(timeline.tracks):
                raise ValueError("片段的轨道不存在。")
            if clip.type in {"video", "image", "audio"}:
                asset = assets.get(clip.assetId or "")
                # LTX keeps a video's separated original sound bound to the
                # same video asset. It is an audio view of that exact source.
                if asset is None or (asset.type != clip.type and not (clip.type == "audio" and asset.type == "video")):
                    raise ValueError(f"时间线 {timeline.id} / 片段 {clip.id} / 素材 {clip.assetId}：片段没有绑定有效素材。")
                track = timeline.tracks[clip.trackIndex]
                location = (f"时间线「{timeline.name}」({timeline.id}) / 轨道「{track.name}」({track.id}) / "
                            f"片段 {clip.id}（{clip.startTime:g}–{clip.startTime + clip.duration:g} 秒）/ 素材 {asset.id}")
                # Keep embedded LTX playback data equal to its exact asset binding.
                clip.asset = asset
                try:
                    path, source_duration = clip_media_source(asset, clip)
                    resolve_media_path(service, path, project_id, require_exists=False)
                except ValueError as exc:
                    raise ValueError(f"{location}：{exc}") from exc
                if source_duration and clip.type != "image" and clip.trimStart + clip.duration * clip.speed > source_duration + 0.1:
                    raise ValueError(f"{location}：片段裁切超出了原素材时长。")
                # The current renderer decodes enabled visual clips even at
                # zero opacity, so their files still belong to its input set.
                visual = clip.type in {"video", "image"}
                audible = clip.type in {"video", "audio"} and not clip.muted and not track.muted and clip.volume > 0
                needed = editorial_clip_enabled(timeline, clip) and (visual or audible)
                if needed and (purpose == "save" or timeline.id == project.activeTimelineId):
                    try:
                        resolve_media_path(service, path, project_id)
                    except FileNotFoundError as exc:
                        raise FileNotFoundError(f"{location}：媒体文件未找到（{path}）。请导入替换该素材。") from exc
        for subtitle in timeline.subtitles:
            if subtitle.endTime <= subtitle.startTime or subtitle.trackIndex >= len(timeline.tracks):
                raise ValueError("字幕的时间或轨道无效。")
    return project


def read_editorial(service: ShortDramaService, project_id: str) -> StudioEditorial:
    aggregate = service.get_project(project_id)
    saved = aggregate.document.editorial_timeline
    if saved and isinstance(saved.get("project"), dict):
        return StudioEditorial(revision=int(cast(int, saved.get("revision", 0))), project=cast(dict[str, JsonValue], saved["project"]),
            default_subtitle_font_family=default_subtitle_font_family())
    return StudioEditorial(revision=0, project=project_editorial(aggregate), default_subtitle_font_family=default_subtitle_font_family())


def edit_editorial(service: ShortDramaService, project_id: str, request: "StudioEditorialEdit",
                   *, changed_clip_ids: set[str] | None = None) -> StudioEditorial:
    from short_drama.studio_models import StudioTimelineSave
    from short_drama.studio_service import StudioContentConflict
    from short_drama.studio_shot_editorial import read_shot_editorial, save_shot_editorial
    from short_drama.studio_visual_edits import add_titles, change_title, change_transitions
    current = read_editorial(service, project_id)
    if current.revision != request.expected_revision:
        raise StudioContentConflict("剪辑已更新，请读取当前片段后再次修改。")
    project = EditorProject.model_validate(current.project)
    original_project = project
    if request.timeline_id is None:
        require_unambiguous_editorial_ids(project, clip_ids=[change.id for change in request.clips],
                                         subtitle_ids=[change.id for change in request.subtitles])
    if request.shot_id:
        current = read_shot_editorial(service, project_id, request.shot_id, request.anchor_clip_id,
            timeline_id=request.timeline_id)
        if current.revision != request.expected_revision:
            raise StudioContentConflict("剪辑已更新，请读取当前片段后再次修改。")
        project = EditorProject.model_validate(current.project)
    target_id = request.timeline_id or project.activeTimelineId
    timeline = next((item for item in project.timelines if item.id == target_id), None)
    if timeline is None:
        raise ValueError(f"/timeline_id: {target_id!r} is not a current editorial timeline. Re-read the selected timeline.")
    before_clips = {clip.id: clip.model_copy(deep=True) for item in original_project.timelines if item.id == target_id for clip in item.clips}
    before_visual = timeline.model_copy(deep=True)
    removed: set[str] = set()
    for change in request.clips:
        clip = next((item for item in timeline.clips if item.id == change.id), None)
        if clip is None:
            raise KeyError(f"Clip {change.id!r} is not in timeline_id={target_id!r}, shot_id={request.shot_id!r}, anchor_clip_id={request.anchor_clip_id!r}.")
        linked = cast(list[str], (clip.model_extra or {}).get("linkedClipIds", [])) if request.keep_linked_audio else []
        ids = {clip.id, *linked}
        if change.remove:
            removed.update(ids)
            continue
        change_title(clip, change)
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
    change_transitions(timeline, before_visual, request.clips, removed)
    if request.shot_id:
        visual_end = max((clip.startTime + clip.duration for clip in timeline.clips if clip.type in {"video", "image"}), default=0.0)
        changed_titles = {change.id for change in request.clips if not change.remove}
        for clip in timeline.clips:
            if clip.type == "text" and clip.id in changed_titles and clip.startTime + clip.duration > visual_end + 1e-7:
                raise ValueError(f"/clips/{clip.id}: title exceeds SHOT_LOCAL duration {visual_end:g}s; use FILM scope for a crossing title.")
    add_titles(timeline, request.add_titles, shot_id=request.shot_id)
    explicit_subtitle_fields: dict[str, set[str]] = {}
    for change in request.subtitles:
        cue = next((item for item in timeline.subtitles if item.id == change.id), None)
        if cue is None:
            raise KeyError(f"Subtitle {change.id!r} is not in timeline_id={target_id!r}, shot_id={request.shot_id!r}, anchor_clip_id={request.anchor_clip_id!r}.")
        explicit_subtitle_fields.setdefault(change.id, set()).update(
            name for name in change.model_fields_set if getattr(change, name) is not None)
        if change.remove:
            timeline.subtitles = [item for item in timeline.subtitles if item.id != change.id]
            continue
        if change.start_seconds is not None: cue.startTime = change.start_seconds
        if change.end_seconds is not None: cue.endTime = change.end_seconds
        if change.text is not None: cue.text = change.text
        if change.style is not None: cue.style.update(change.style)
    local_duration = max((clip.startTime + clip.duration for clip in timeline.clips if clip.type != "audio"), default=0.0)
    for cue in request.add_subtitles:
        if any(item.id == cue.id for item in timeline.subtitles):
            raise ValueError(f"/add_subtitles: {cue.id!r} already exists in timeline_id={target_id!r}; use subtitles to edit it.")
        if request.shot_id and cue.end_seconds > local_duration:
            raise ValueError(f"/add_subtitles: {cue.id!r} exceeds the edited SHOT_LOCAL duration {local_duration:g}s; use FILM coordinates without shot_id for a crossing caption.")
        track_index = cue.track_index
        if track_index is None:
            track_index = next((index for index, track in enumerate(timeline.tracks)
                                if track.type == "subtitle" and track.enabled and not track.locked), None)
            if track_index is None:
                track_id, suffix = "subtitles", 1
                while any(track.id == track_id for track in timeline.tracks):
                    track_id, suffix = f"subtitles-{suffix}", suffix + 1
                track_index = len(timeline.tracks)
                timeline.tracks.append(EditorTrack(id=track_id, name="字幕", type="subtitle"))
        elif track_index >= len(timeline.tracks) or timeline.tracks[track_index].type != "subtitle":
            raise ValueError(f"/add_subtitles: track_index={track_index} is not a current subtitle track in timeline_id={target_id!r}.")
        timeline.subtitles.append(EditorSubtitle(id=cue.id, text=cue.text, startTime=cue.start_seconds,
            endTime=cue.end_seconds, trackIndex=track_index, style=cue.style,
            **({"sourceShotId": request.shot_id} if request.shot_id else {})))
    save = StudioTimelineSave(expected_revision=request.expected_revision, anchor_clip_id=request.anchor_clip_id,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))})
    if request.shot_id:
        mutation = save_shot_editorial(service, project_id, request.shot_id, save,
            explicit_subtitle_fields=explicit_subtitle_fields, timeline_id=request.timeline_id)
    else:
        mutation = service.studio.save_timeline(project_id, save)
    # Use this save's snapshot for its receipt. A fresh read after releasing
    # the save lock could include another edit and claim those changes too.
    stored = mutation.project.document.editorial_timeline
    assert stored is not None and isinstance(stored.get("project"), dict)
    saved = StudioEditorial(revision=int(cast(int, stored["revision"])),
        project=cast(dict[str, JsonValue], stored["project"]), default_subtitle_font_family=default_subtitle_font_family())
    if changed_clip_ids is not None:
        after = EditorProject.model_validate(saved.project)
        after_clips = {clip.id: clip for item in after.timelines if item.id == target_id for clip in item.clips}
        changed_clip_ids.update(identity for identity in before_clips.keys() | after_clips.keys()
                                if before_clips.get(identity) != after_clips.get(identity))
    return saved


def project_editorial(aggregate: ProjectAggregate, *, preview_shot_id: str | None = None) -> dict[str, JsonValue]:
    return project_editorial_from_document(aggregate.document, aggregate.takes, preview_shot_id=preview_shot_id)


def project_editorial_from_document(document: ProjectDocument, takes: list[Take], *,
                                    preview_shot_id: str | None = None) -> dict[str, JsonValue]:
    """Project existing Take windows once; subsequent opens use saved editor content."""
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
        take = next((item for item in takes if item.is_selected and shot.id in item.selected_shot_ids), None)
        if take is None and old_clip:
            take = next((item for item in takes if item.id == old_clip.take_id), None)
        if take is None and shot.id == preview_shot_id:
            # Opening a local editor may preview a returned video before adoption.
            # This projection does not select the Take or write the episode timeline.
            take = next((item for item in reversed(takes)
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
                "resolution": "", "duration": next((take.duration_seconds for take in takes if take.id == clip.take_id), clip.source_in_seconds + clip.duration_seconds), "createdAt": now}
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
