"""Locate uses and restore a moved source file without replacing creative content."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from PIL import Image
from pydantic import JsonValue
from short_drama.media import probe_media_with_ffmpeg
from short_drama.models import AssetMediaType, DomainModel, ProjectAggregate, ProjectDocument, Take, TakeAvailability
from short_drama.studio_editorial import EditorProject, project_editorial
from short_drama.studio_media import resolve_media_path
from short_drama.studio_references import shot_cues

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class StudioMediaUse(DomainModel):
    episode_id: str
    episode_title: str
    episode_number: int
    kind: Literal["shot", "editorial", "voice"]
    target_id: str
    label: str
    shot_id: str | None = None
    timeline_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None


class StudioMediaUsage(DomainModel):
    uses: list[StudioMediaUse]


class StudioMediaRelinkResult(DomainModel):
    project: ProjectAggregate
    affected_episode_ids: list[str]
    path: str
    notice: str


def media_usage(service: ShortDramaService, project_id: str, *, asset_id: str | None = None,
                asset_version_id: str | None = None, path: str | None = None) -> StudioMediaUsage:
    selected = service.studio.current_project(project_id).document
    if not any((asset_id, asset_version_id, path)):
        raise ValueError("请选择具体素材或文件。")
    asset_ids: set[str] = set()
    version_ids: set[str] = set()
    paths: set[Path] = set()
    if path:
        paths.add(resolve_media_path(service, path, project_id, require_exists=False))
    if asset_id:
        subject = next((item for item in selected.assets if item.id == asset_id), None)
        if subject is None:
            raise KeyError(asset_id)
        asset_ids = {item.id for item in selected.assets if item.id == asset_id or (
            subject.bible_entity_id and item.bible_entity_id == subject.bible_entity_id)}
        version_ids.update(entry.asset_version_id for pack in selected.reference_packs
                           if pack.subject_asset_id in asset_ids for entry in pack.entries)
    if asset_version_id:
        if not any(version.id == asset_version_id for asset in selected.assets for version in asset.versions):
            raise KeyError(asset_version_id)
        version_ids = {asset_version_id}
        asset_ids = set()
    for asset in selected.assets:
        for version in asset.versions:
            if asset.id in asset_ids or version.id in version_ids:
                version_ids.add(version.id)
                if version.local_path:
                    paths.add(Path(version.local_path).expanduser().resolve())
    # The same physical file can be registered on more than one object. Resolve
    # this after collecting all paths so asset ordering cannot hide a use.
    version_ids.update(version.id for asset in selected.assets for version in asset.versions
        if version.local_path and Path(version.local_path).expanduser().resolve() in paths)
    uses: list[StudioMediaUse] = []
    for episode in service.studio.episodes(project_id).episodes:
        aggregate = service.studio.current_project(episode.id)
        document = aggregate.document
        for shot in document.shots:
            matched = any(cue.asset_version_id in version_ids or cue.subject_asset_id in asset_ids for cue in shot_cues(shot))
            take_match = any(Path(take.media_path).expanduser().resolve() in paths
                and shot.id in take.selected_shot_ids for take in aggregate.takes)
            if matched or take_match:
                uses.append(StudioMediaUse(episode_id=episode.id, episode_title=episode.episode_title,
                    episode_number=episode.episode_number, kind="shot", target_id=shot.id, shot_id=shot.id,
                    label=f"镜头 {shot.sequence:02d} · {shot.narrative_purpose}"))
        saved = document.editorial_timeline
        editor_payload = saved["project"] if saved and isinstance(saved.get("project"), dict) else project_editorial(aggregate)
        if editor_payload:
            editor = EditorProject.model_validate(editor_payload)
            assets = {item.id: item for item in editor.assets}
            for timeline in editor.timelines:
                for clip in timeline.clips:
                    asset = assets.get(clip.assetId or "")
                    source_path = asset.path if asset else ""
                    extra = clip.model_extra or {}
                    take_index = extra.get("takeIndex")
                    takes: object = (asset.model_extra or {}).get("takes") if asset else None
                    if isinstance(take_index, int) and isinstance(takes, list):
                        take_values = cast(list[object], takes)
                        if 0 <= take_index < len(take_values) and isinstance(take_values[take_index], dict):
                            candidate = cast(dict[str, object], take_values[take_index]).get("path")
                            if isinstance(candidate, str):
                                source_path = candidate
                    if source_path and Path(source_path).expanduser().resolve() in paths:
                        shot_id = extra.get("sourceShotId")
                        track = timeline.tracks[clip.trackIndex] if 0 <= clip.trackIndex < len(timeline.tracks) else None
                        location = f"{timeline.name} · {track.name if track else '未知轨道'}"
                        if timeline.id != editor.activeTimelineId:
                            location += "（非当前时间线）"
                        if track is not None and not track.enabled:
                            location += "（轨道已停用）"
                        uses.append(StudioMediaUse(episode_id=episode.id, episode_title=episode.episode_title,
                            episode_number=episode.episode_number, kind="editorial", target_id=clip.id,
                            timeline_id=timeline.id,
                            shot_id=shot_id if isinstance(shot_id, str) else None,
                            label=location,
                            start_seconds=clip.startTime, end_seconds=clip.startTime + clip.duration))
        for binding in document.voice_references:
            asset = next((item for item in document.assets if item.id == binding.asset_id), None)
            if binding.asset_id in asset_ids or (asset and asset.current_version_id in version_ids):
                bible = next((item for item in document.bible_versions if item.id == document.project.current_bible_version_id), None)
                character = next((item for item in bible.characters if item.id == binding.character_id), None) if bible else None
                uses.append(StudioMediaUse(episode_id=episode.id, episode_title=episode.episode_title,
                    episode_number=episode.episode_number, kind="voice", target_id=binding.character_id,
                    label=f"{character.name if character else binding.character_id}的声音参考"))
    return StudioMediaUsage(uses=uses)


def _relocate(value: JsonValue, old: Path, new: str) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == "path" or key.endswith("_path") or key in {"bigThumbnailPath", "smallThumbnailPath"}) and item:
                if Path(item).expanduser().resolve() == old:
                    value[key] = new
                    changed = True
            elif isinstance(item, (dict, list)):
                changed = _relocate(item, old, new) or changed
    elif isinstance(value, list):
        for item in value:
            changed = _relocate(item, old, new) or changed
    return changed


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm"}


def _validate_relink_source(documents: Sequence[ProjectDocument], takes: Sequence[Take], old: Path, replacement: Path, *,
                           width: int, height: int, duration: float | None, has_audio: bool, file_size: int) -> None:
    expectations: set[tuple[int | None, int | None, float | None]] = set()
    media_types: set[AssetMediaType] = set()
    voice_reference = False
    for document in documents:
        for asset in document.assets:
            for version in asset.versions:
                if version.local_path and Path(version.local_path).expanduser().resolve() == old:
                    expectations.add((version.width, version.height, version.duration_seconds))
                    media_types.add(version.media_type)
                    voice_reference = voice_reference or (version.id == asset.current_version_id and any(
                        binding.asset_id == asset.id for binding in document.voice_references))
    for take in takes:
        if Path(take.media_path).expanduser().resolve() == old:
            expectations.add((take.width, take.height, take.duration_seconds))
            media_types.add(AssetMediaType.VIDEO)
    media_types.discard(AssetMediaType.TEXT_REFERENCE)
    if not media_types:
        media_types.add(AssetMediaType.IMAGE if old.suffix.lower() in _IMAGE_SUFFIXES else
                        AssetMediaType.VIDEO if old.suffix.lower() in _VIDEO_SUFFIXES else AssetMediaType.AUDIO)
    expected_suffixes = _IMAGE_SUFFIXES if AssetMediaType.IMAGE in media_types else (
        _VIDEO_SUFFIXES if AssetMediaType.VIDEO in media_types else _AUDIO_SUFFIXES)
    if replacement.suffix.lower() not in expected_suffixes:
        raise ValueError("所选文件类型与原素材不一致，请选择同一种媒体的原文件。")
    if AssetMediaType.VIDEO in media_types and (not width or not height):
        raise ValueError("原素材是视频，所选文件没有可读取的视频流。")
    if AssetMediaType.AUDIO in media_types and not has_audio:
        raise ValueError("原素材是声音，所选文件没有可读取的音轨。")
    if voice_reference and (replacement.suffix.lower() not in {".wav", ".mp3"} or file_size > 15 * 1024 * 1024):
        raise ValueError("该文件用于角色声音参考，请选择不超过 15 MB 的原 WAV 或 MP3 文件。")
    for expected_width, expected_height, expected_duration in expectations:
        if (expected_width and width != expected_width) or (expected_height and height != expected_height) or (
                expected_duration and (duration is None or abs(expected_duration - duration) > 0.12)):
            raise ValueError("所选文件与原素材的尺寸或时长不一致。重新定位应选择同一原文件；修改内容请使用导入替换。")


def relink_media(service: ShortDramaService, project_id: str, old_path: str, replacement: Path) -> StudioMediaRelinkResult:
    old = resolve_media_path(service, old_path, project_id, require_exists=False)
    if not replacement.is_file() or replacement.stat().st_size == 0:
        raise ValueError("所选文件为空或不可读取。")
    if old.suffix.lower() not in _IMAGE_SUFFIXES | _VIDEO_SUFFIXES | _AUDIO_SUFFIXES:
        raise ValueError("这个文件不支持重新定位，请在对应内容中重新导入。")
    # Decode before copying/writing any current project state.
    if replacement.suffix.lower() in _IMAGE_SUFFIXES:
        with Image.open(replacement) as picture:
            picture.load()
            width, height = picture.size
        duration: float | None = None
        has_audio = False
    else:
        metadata = probe_media_with_ffmpeg(replacement)
        size = metadata.get("size", "unknown").split("x")
        width, height = (int(size[0]), int(size[1])) if len(size) == 2 else (0, 0)
        duration = float(metadata.get("duration", "0"))
        has_audio = metadata.get("audio") == "present"
        if duration <= 0:
            raise ValueError("无法读取所选音视频的时长。")
    episodes = service.studio.episodes(project_id)
    aggregates = [service.get_project(episode.id) for episode in episodes.episodes]
    _validate_relink_source([aggregate.document for aggregate in aggregates],
        [take for aggregate in aggregates for take in aggregate.takes], old, replacement,
        width=width, height=height, duration=duration, has_audio=has_audio, file_size=replacement.stat().st_size)
    folder = service.workspace_root / episodes.project_id / "relinked-media"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{uuid4().hex}{replacement.suffix.lower() or old.suffix.lower()}"
    new_path = str(destination.resolve())
    committed = False
    try:
        shutil.copy2(replacement, destination)
        file_size = destination.stat().st_size

        def transform(documents: Sequence[ProjectDocument], takes: Sequence[Take]) -> list[str]:
            # The repository supplies the latest complete family after BEGIN
            # IMMEDIATE. This callback performs no copy or media probing.
            _validate_relink_source(documents, takes, old, destination,
                width=width, height=height, duration=duration, has_audio=has_audio, file_size=file_size)
            affected: set[str] = set()
            for document in documents:
                episode_id = document.project.id
                old_editorial = document.editorial_timeline
                payload = cast(dict[str, JsonValue], document.model_dump(mode="json"))
                changed = _relocate(payload, old, new_path)
                if changed:
                    # Keep the original loaded snapshot so repository field merging still protects concurrent edits.
                    updated = type(document).model_validate(payload)
                    for field in type(document).model_fields:
                        setattr(document, field, getattr(updated, field))
                    if document.editorial_timeline and document.editorial_timeline != old_editorial:
                        document.editorial_timeline["revision"] = int(cast(int, document.editorial_timeline.get("revision", 0))) + 1
                    for asset in document.assets:
                        for version in asset.versions:
                            if version.local_path == new_path:
                                version.file_size_bytes = file_size
                    document.project.updated_at = datetime.now(UTC).isoformat()
                shared = any(version.local_path == new_path for asset in document.assets for version in asset.versions)
                if changed or shared:
                    affected.add(episode_id)
            for take in takes:
                take_payload = cast(dict[str, JsonValue], take.model_dump(mode="json"))
                if _relocate(take_payload, old, new_path):
                    updated_take = type(take).model_validate(take_payload)
                    if updated_take.media_path == new_path:
                        updated_take.availability = TakeAvailability.AVAILABLE
                    for field in type(take).model_fields:
                        setattr(take, field, getattr(updated_take, field))
                    affected.add(take.project_id)
            return [document.project.id for document in documents if document.project.id in affected]

        with service.studio.lock:
            affected = service.repository.update_project_family_media(
                project_id, transform, event_data={"relinked_path": new_path})
            committed = True
    except Exception:
        # All references and events roll back together, so a failed attempt has
        # no database owner for its new copy.
        if not committed:
            destination.unlink(missing_ok=True)
        raise
    return StudioMediaRelinkResult(project=service.studio.current_project(project_id),
        affected_episode_ids=affected, path=new_path,
        notice=f"已重新定位文件，更新 {len(affected)} 集的相关引用。剪辑和已完成内容保留。")
