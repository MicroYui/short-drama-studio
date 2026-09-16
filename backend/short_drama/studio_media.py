"""Project-owned media delivery and current-asset imports."""

from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime
from uuid import uuid4
import subprocess
import imageio_ffmpeg
from typing import TYPE_CHECKING, cast
from threading import RLock
from weakref import WeakKeyDictionary
from pydantic import JsonValue
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest, AssetReviewRequest, AssetReviewDecision, AssetReviewPurpose
from short_drama.models import (AssetKind, AssetMediaType, ProjectAggregate, GenerationTask, TaskStatus, OperationKind,
    Take, TakeAvailability, QaStatus, QAReport, Freshness)
from short_drama.studio_models import StudioShotMediaSelect

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService
    from short_drama.models import ProjectDocument

_index_lock = RLock()
_path_indexes: WeakKeyDictionary[ShortDramaService, dict[str | None, set[Path]]] = WeakKeyDictionary()


def referenced_media_paths(value: JsonValue) -> set[Path]:
    paths: set[Path] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key.endswith("_path") or key == "path") and item:
                paths.add(Path(item).expanduser().resolve())
            elif isinstance(item, (dict, list)):
                paths.update(referenced_media_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(referenced_media_paths(item))
    return paths


def resolve_media_path(service: ShortDramaService, path: str, project_id: str | None = None,
                       *, require_exists: bool = True) -> Path:
    candidate = Path(path).expanduser().resolve()
    # Index once for a grid of thumbnails; refresh on a newly imported path.
    # Existing files remain registered in project history even after replacement.
    with _index_lock:
        cache = _path_indexes.setdefault(service, {})
        if candidate not in cache.get(project_id, set()):
            paths: set[Path] = set()
            project_ids = [project_id] if project_id else [document.project.id for document in service.repository.list_projects()]
            for selected in project_ids:
                aggregate = service.get_project(selected)
                paths.update(referenced_media_paths(cast(dict[str, JsonValue], aggregate.model_dump(mode="json"))))
            cache[project_id] = paths
        if candidate not in cache[project_id]:
            raise ValueError("文件未登记到项目媒体中，请先导入。")
    if require_exists and not candidate.is_file():
        raise FileNotFoundError("项目记录中的媒体文件未找到，请重新导入。")
    return candidate


def import_studio_media(service: ShortDramaService, project_id: str, path: Path, *, name: str, kind: AssetKind, asset_id: str | None, replace_version_id: str | None = None, provenance: str = "用户直接导入并选用的素材。") -> ProjectAggregate:
    document = service.studio.current_project(project_id).document
    root_id = document.project.parent_project_id or project_id
    if replace_version_id and not any(asset.id == asset_id and any(version.id == replace_version_id for version in asset.versions) for asset in document.assets):
        raise ValueError("要替换的文件不属于当前素材，请重新选择。")
    # Imports remain available while another episode is being authored.
    if asset_id is None:
        initial = service.asset_workbench.create_asset(root_id, AssetCreateRequest(kind=kind, name=name, descriptor=name))
        owner = service.studio.current_project(root_id).document
        asset_id = next(asset.id for asset in owner.assets if any(version.id == initial.id for version in asset.versions))
    version = service.asset_workbench.import_local(root_id, asset_id, AssetImportLocalRequest(local_path=str(path), descriptor=name))
    service.asset_workbench.review_version(root_id, asset_id, version.id, AssetReviewRequest(
        decision=AssetReviewDecision.APPROVED, purpose=AssetReviewPurpose.CANONICAL_MASTER, notes=provenance,
    ))
    if replace_version_id:
        _replace_references(service, root_id, replace_version_id, version.id)
    else:
        for episode in service.studio.episodes(root_id).episodes:
            service.repository.append_studio_event(episode.id, "assets.updated", {"asset_id": asset_id, "asset_version_id": version.id})
    return service.studio.current_project(project_id)


def _replace_references(service: ShortDramaService, root_id: str, old: str, new: str) -> None:
    """An explicit file replacement follows its occurrences across shared episodes."""
    from short_drama.studio_references import shot_cues, sync_shot_references
    with service.studio.lock:
        root = service.studio.current_project(root_id).document
        for pack in root.reference_packs:
            for entry in pack.entries:
                if entry.asset_version_id == old:
                    entry.asset_version_id = new
        service.repository.save_project(root)
        for episode in service.studio.episodes(root_id).episodes:
            document = service.studio.current_project(episode.id).document
            affected: list[str] = []
            for shot in document.shots:
                occurrences = [cue for cue in shot_cues(shot) if cue.asset_version_id == old]
                if not occurrences:
                    continue
                for cue in occurrences:
                    cue.asset_version_id = new
                from short_drama.studio_models import StudioShotSave
                sync_shot_references(document, shot)
                service.studio.save_shot(episode.id, shot.id, StudioShotSave(shot=shot, expected_revision=shot.revision))
                affected.append(shot.id)
            service.repository.append_studio_event(episode.id, "assets.updated", {"replaced_version_id": old, "asset_version_id": new, "shot_ids": [item for item in affected]})


def select_imported_video(service: ShortDramaService, project_id: str, shot_id: str, request: StudioShotMediaSelect) -> ProjectAggregate:
    project = service.studio.current_project(project_id)
    document = project.document
    shot = next((item for item in document.shots if item.id == shot_id), None)
    if shot is None:
        raise KeyError(shot_id)
    if shot.revision != request.expected_revision:
        raise ValueError("镜头已更新，请在最新镜头上选择视频。")
    version = next((version for asset in document.assets for version in asset.versions if version.id == request.asset_version_id), None)
    if version is None or version.media_type != AssetMediaType.VIDEO or not version.local_path or not version.duration_seconds:
        raise ValueError("请选择已导入本项目的视频文件。")
    thumbnail = service.workspace_root / project_id / "imports" / f"{version.id}-poster.jpg"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-i', version.local_path, '-frames:v', '1', '-vf', 'scale=480:-2', str(thumbnail)], check=True, capture_output=True, timeout=30)
    from short_drama.studio_service import StudioContentConflict
    with service.studio.lock:
        project = service.studio.current_project(project_id)
        document = project.document
        shot = next((item for item in document.shots if item.id == shot_id), None)
        if shot is None or shot.revision != request.expected_revision:
            raise StudioContentConflict("导入期间镜头已更新，请读取最新镜头后再选择视频。")
        now = datetime.now(UTC).isoformat()
        task_id = f"import-{uuid4().hex[:12]}"
        take_id = f"take-{uuid4().hex[:12]}"
        index = max((item.candidate_index for item in project.takes if item.shot_id == shot_id), default=0) + 1
        _replace_editorial_take(document, project.takes, shot_id, take_id, version.id, version.local_path, str(thumbnail), version.duration_seconds)
        service.repository.save_task(GenerationTask(id=task_id, project_id=project_id, shot_id=shot_id, operation=OperationKind.GENERATE,
            candidate_index=index, status=TaskStatus.SUCCEEDED, provider="user-import", model="original-media",
            request_fingerprint=task_id, input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id, input_shot_revision=shot.revision,
            requested_duration_seconds=version.duration_seconds, attempt=1, created_at=now, updated_at=now, finished_at=now))
        service.repository.save_take(Take(id=take_id, project_id=project_id, shot_id=shot_id, task_id=task_id, candidate_index=index,
            operation=OperationKind.GENERATE, provider="user-import", model="original-media", media_path=version.local_path,
            thumbnail_path=str(thumbnail), duration_seconds=version.duration_seconds, width=version.width or 0, height=version.height or 0,
            availability=TakeAvailability.AVAILABLE, qa_status=QaStatus.PASSED, is_selected=False, freshness=Freshness.CURRENT,
            qa_report=QAReport(id=f"import-review-{take_id}", take_id=take_id, status=QaStatus.PASSED, hard_gate_passed=True, score=0,
                issues=[], checks=["用户直接选用的导入文件", "文件元数据可读取"], reviewer_notes="用户选用；未进行模型内容审校。", created_at=now), created_at=now))
        service.repository.select_take(take_id)
        document.project.current_manifest_id = None
        document.project.updated_at = now
        service.repository.save_project(document)
        service.repository.append_studio_event(project_id, "content.updated", {"shot_id": shot_id, "take_id": take_id, "asset_version_id": version.id})
        return service.studio.current_project(project_id)


def _replace_editorial_take(document: "ProjectDocument", takes: list[Take], shot_id: str, take_id: str, version_id: str, path: str, thumbnail: str, duration: float) -> None:
    from short_drama.studio_editorial import EditorProject, EditorAsset
    saved = document.editorial_timeline
    if not saved or not isinstance(saved.get("project"), dict):
        return
    editor = EditorProject.model_validate(saved["project"])
    changed = False
    for timeline in editor.timelines:
        for clip in timeline.clips:
            metadata = clip.model_extra or {}
            if clip.type != "video" or metadata.get("sourceShotId") != shot_id:
                continue
            old_take_id = metadata.get("sourceTakeId")
            old_take = next((item for item in takes if item.id == old_take_id), None)
            window = next((item for item in old_take.shot_windows if item.shot_id == shot_id), None) if old_take else None
            old_window = next((item for manifest in reversed(document.manifests) for item in manifest.video_clips if item.shot_id == shot_id and item.take_id == old_take_id), None)
            original_in = old_window.source_in_seconds if old_window else window.source_in_seconds if window else 0
            source_in = clip.trimStart - original_in
            if source_in < -0.01 or source_in + clip.duration * clip.speed > duration + 0.05:
                raise ValueError("新视频无法覆盖这个镜头已有的裁切范围。文件已加入资产库，请先调整剪辑的素材入点或时长，再替换。")
            linked = cast(list[str], metadata.get("linkedClipIds", []))
            for item in timeline.clips:
                if item.id != clip.id and not (item.id in linked and item.type == "audio"):
                    continue
                media_id = take_id if item.type == "video" else f"{take_id}-audio"
                asset = EditorAsset.model_validate({"id": media_id, "type": item.type, "path": path, "duration": duration,
                    "sourceAssetVersionId": version_id, "smallThumbnailPath": thumbnail, "bigThumbnailPath": thumbnail,
                    "prompt": "替换视频" if item.type == "video" else "替换视频 · 原声", "createdAt": int(datetime.now(UTC).timestamp() * 1000)})
                if not any(existing.id == media_id for existing in editor.assets): editor.assets.append(asset)
                item.assetId, item.asset, item.trimStart = media_id, asset, max(0, source_in)
                item.trimEnd = max(0, duration - item.trimStart - item.duration * item.speed)
                if item.model_extra is not None: item.model_extra["sourceTakeId"] = take_id
            changed = True
    if changed:
        document.editorial_timeline = {**saved, "project": cast(dict[str, JsonValue], editor.model_dump(mode="json")), "revision": int(cast(int, saved.get("revision", 0))) + 1}
