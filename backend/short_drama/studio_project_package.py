"""Portable current content, local media and readable production records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import TYPE_CHECKING, cast
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from pydantic import Field, JsonValue

from short_drama.models import DomainModel, GenerationTask, ProjectDocument, Take, TakeAvailability, TaskStatus
from short_drama.video_edit_models import VideoEditRun
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_trace_export import write_studio_trace
from short_drama.studio_package_progress import PackageProgress
from short_drama.studio_package_models import (
    PackageMedia, PackageEpisode, ProjectPackageManifest, StudioProjectPackage,
    StudioProjectPackageImport, StudioProjectImportSummary,
)

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class _EpisodeContent(DomainModel):
    document: ProjectDocument
    takes: list[Take]
    take_tasks: list[GenerationTask]
    video_edit_runs: list[VideoEditRun] = Field(default_factory=list[VideoEditRun])


def _map_paths(value: JsonValue, replace: Callable[[str], str], *, path_list: bool = False) -> JsonValue:
    if isinstance(value, dict):
        return {key: replace(item) if isinstance(item, str) and item and (
            key == "path" or key.endswith("_path") or key in {"bigThumbnailPath", "smallThumbnailPath"})
            else _map_paths(item, replace, path_list=key.endswith("_paths")) for key, item in value.items()}
    if isinstance(value, list):
        return [replace(item) if path_list and isinstance(item, str) and item else _map_paths(item, replace) for item in value]
    return value


def _entry(value: str) -> str:
    path = PurePosixPath(value)
    if not value or not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in path.parts[0]:
        raise ValueError("项目包包含无效的相对文件路径。")
    return str(path)


def project_package_path(service: ShortDramaService, project_id: str, package_id: str) -> Path:
    root_id = service.studio.episodes(project_id).project_id
    if re.fullmatch(r"project-package-[a-f0-9]{12}", package_id) is None:
        raise ValueError("项目包名称无效。")
    path = service.workspace_root / root_id / "project-packages" / f"{package_id}.zip"
    if not path.is_file():
        raise FileNotFoundError("项目包未找到，请重新收集。")
    return path


def create_project_package(service: ShortDramaService, project_id: str, *,
                           progress: PackageProgress | None = None, package_id: str | None = None,
                           on_complete: Callable[[StudioProjectPackage], None] | None = None) -> StudioProjectPackage:
    # Keep one current content snapshot. ZIP/media/Trace IO runs after releasing
    # the content lock, so collecting a film never prevents editing it.
    progress = progress or PackageProgress()
    progress.begin("snapshot", "正在读取已保存的项目内容")
    with service.studio.lock:
        family = service.studio.episodes(project_id)
        contents: list[_EpisodeContent] = []
        entries: list[PackageEpisode] = []
        for index, episode in enumerate(family.episodes):
            document = service.studio.current_project(episode.id).document
            takes = service.repository.list_takes(episode.id)
            task_ids = {take.task_id for take in takes}
            video_edit_runs = service.repository.list_video_edit_runs(episode.id)
            task_ids.update(run.task_id for run in video_edit_runs)
            tasks = [task for task in service.repository.list_tasks(episode.id) if task.id in task_ids]
            document.trace_sources = []  # The complete source records have their own portable directory.
            contents.append(_EpisodeContent(document=document, takes=takes, take_tasks=tasks, video_edit_runs=video_edit_runs))
            entries.append(PackageEpisode(project_id=episode.id, title=episode.episode_title,
                document_path=f"projects/{index}.json", trace_path=f"traces/{index}"))
    package_id = package_id or f"project-package-{uuid4().hex[:12]}"
    folder = service.workspace_root / family.project_id / "project-packages"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{package_id}.zip"
    temporary = folder / f"{package_id}.tmp"
    manifest = ProjectPackageManifest(root_project_id=family.project_id,
        created_at=datetime.now(UTC).isoformat(), episodes=entries)
    by_source: dict[str, PackageMedia] = {}

    def relative(source: str) -> str:
        if "://" in source:
            return source  # Remote sources stay explicit; packaging never downloads media.
        path = Path(source).expanduser().resolve()
        key = str(path)
        if key not in by_source:
            suffix = path.suffix if len(path.suffix) < 16 else ""
            media = PackageMedia(source_path=key, archive_path=f"media/{len(by_source):05d}-{uuid4().hex[:8]}{suffix}")
            by_source[key] = media
            manifest.media.append(media)
        return by_source[key].archive_path

    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for entry, content in zip(entries, contents, strict=True):
                progress.check()
                portable = _map_paths(cast(JsonValue, content.model_dump(mode="json")), relative)
                archive.writestr(entry.document_path, json.dumps(portable, ensure_ascii=False, indent=2))
                progress.check()
            total_bytes = 0
            for media in manifest.media:
                try:
                    total_bytes += Path(media.source_path).stat().st_size if Path(media.source_path).is_file() else 0
                except OSError:
                    pass
            progress.begin("media", "正在收集本地媒体", total_files=len(manifest.media), total_bytes=total_bytes)
            for media in manifest.media:
                progress.check()
                source = Path(media.source_path)
                try:
                    if not source.is_file():
                        raise FileNotFoundError("本地文件未找到")
                    before = source.stat()
                    info = ZipInfo(media.archive_path)
                    info.compress_type = ZIP_STORED
                    with source.open("rb") as origin, archive.open(info, "w", force_zip64=True) as destination:
                        progress.copy(origin, destination)
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("收集期间素材文件发生变化，请重新收集项目。")
                    media.bytes = after.st_size
                except OSError as exc:
                    media.status, media.reason = "missing", str(exc)
                progress.advance(files=1)
            for entry in entries:
                progress.begin("trace", f"正在收集制作记录：{entry.title}")
                write_studio_trace(service, entry.project_id, archive, prefix=f"{entry.trace_path}/", progress=progress)
            archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
            missing = [media for media in manifest.media if media.status == "missing"]
            archive.writestr("START_HERE.md", "# Short Drama Studio 项目包\n\n"
                "在 Studio 中选择“导入项目包”，恢复当前多集内容、素材与剪辑。媒体使用包内相对路径，换电脑后仍可打开。\n\n"
                "projects 保留收集时的内容快照和 Take 来源；media 是实际本地文件；traces 按集保留完整制作记录、模型输入输出与工作单。"
                "导入不会继续原电脑上的 Agent、供应商任务或本地导出。已存在的同 ID 项目不会被覆盖。\n\n"
                f"共 {len(entries)} 集，收集 {len(manifest.media) - len(missing)} 个本地文件，缺失 {len(missing)} 个。"
                "缺失项及原始路径详见 manifest.json；远程 URL 保持原引用，不代表媒体已离线保存。\n")
        result = StudioProjectPackage(package_id=package_id, path=str(output), filename=output.name,
            download_url=f"/api/short-drama/studio/projects/{family.project_id}/package/{package_id}", episode_count=len(entries),
            media_count=sum(media.status == "included" for media in manifest.media),
            missing_media=[media for media in manifest.media if media.status == "missing"])
        with service.studio.lock:
            progress.begin("committing", "正在完成项目包，完成后可下载")
            temporary.replace(output)
            if on_complete is not None:
                on_complete(result)
    except Exception:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise
    return result


def import_project_package(service: ShortDramaService, path: Path, *, progress: PackageProgress | None = None,
                           destination_folder: Path | None = None,
                           on_complete: Callable[[StudioProjectImportSummary], None] | None = None) -> StudioProjectPackageImport:
    progress = progress or PackageProgress()
    progress.begin("validating", "正在检查项目包内容和媒体清单")
    folder = destination_folder or service.workspace_root / "imported-projects" / f"import-{uuid4().hex[:12]}"
    committed = False
    try:
        with ZipFile(path) as archive:
            names = [_entry(info.filename) for info in archive.infolist() if not info.is_dir()]
            if len(names) != len(set(names)):
                raise ValueError("项目包包含重复文件名。")
            manifest = ProjectPackageManifest.model_validate_json(archive.read("manifest.json"))
            if not manifest.episodes:
                raise ValueError("项目包没有可导入的内容。")
            ids = [entry.project_id for entry in manifest.episodes]
            if len(ids) != len(set(ids)) or manifest.root_project_id not in ids:
                raise ValueError("项目包的项目与集数信息不一致。")
            if any(service.repository.get_project(identity) is not None for identity in ids):
                raise StudioContentConflict("本机已有同 ID 的项目或集；请在另一工作区导入，原内容不会覆盖。")
            by_path = {media.archive_path: media for media in manifest.media}
            if len(by_path) != len(manifest.media) or any(not _entry(name).startswith("media/") for name in by_path):
                raise ValueError("项目包的媒体清单无效。")

            def absolute(source: str) -> str:
                if "://" in source:
                    return source
                if source not in by_path:
                    raise ValueError(f"项目包中的文件没有登记在媒体清单：{source}")
                return str((folder / _entry(source)).resolve())

            contents: list[_EpisodeContent] = []
            for entry in manifest.episodes:
                progress.check()
                value = cast(JsonValue, json.loads(archive.read(_entry(entry.document_path))))
                content = _EpisodeContent.model_validate(_map_paths(value, absolute))
                if content.document.project.id != entry.project_id:
                    raise ValueError("项目包的集内容与清单不一致。")
                expected_parent = None if entry.project_id == manifest.root_project_id else manifest.root_project_id
                if content.document.project.parent_project_id != expected_parent:
                    raise ValueError("项目包中的多集关系无效。")
                if any(task.project_id != entry.project_id for task in content.take_tasks) or any(take.project_id != entry.project_id for take in content.takes):
                    raise ValueError("项目包的 Take 来源属于其他项目。")
                tasks = {task.id: task for task in content.take_tasks}
                if any(take.task_id not in tasks for take in content.takes):
                    raise ValueError("项目包缺少 Take 的来源记录。")
                if any(run.project_id != entry.project_id or run.task_id not in tasks for run in content.video_edit_runs):
                    raise ValueError("项目包的视频编辑记录缺少同集任务来源。")
                trace_path = _entry(entry.trace_path)
                if not trace_path.startswith("traces/") or not any(name.startswith(f"{trace_path}/") for name in names):
                    raise ValueError("项目包缺少该集的制作记录。")
                content.document.trace_sources = [str((folder / trace_path).resolve())]
                now = datetime.now(UTC).isoformat()
                for task in content.take_tasks:
                    if task.status in {TaskStatus.QUEUED, TaskStatus.SUBMITTING, TaskStatus.ACKNOWLEDGED, TaskStatus.RUNNING, TaskStatus.SUBMIT_UNKNOWN}:
                        original = task.status.value
                        task.status, task.error_code = TaskStatus.CANCELLED, "PROJECT_IMPORTED"
                        task.error_message = f"导入内容时未恢复原运行（原状态 {original}）；原制作过程保存在 Trace。"
                        task.finished_at = task.updated_at = now
                    task.provider_run_id = task.provider_task_id = None
                for record in content.document.studio_exports:
                    if record.get("status") in {"running", "cancelling"}:
                        record.update(status="failed", progress="原导出未在本机继续", error="当前剪辑已导入，可重新导出。", finished_at=now, updated_at=now)
                for workflow in content.document.comfy_workflows:
                    if workflow.get("status") in {"running", "submitted", "queued"}:
                        workflow.update(status="failed", error="迁移后未恢复原工作流；原记录保存在 Trace。")
                for run in content.video_edit_runs:
                    if run.status not in {"SUCCEEDED", "FAILED", "CANCELLED", "OUTPUT_MISSING"}:
                        run.status = "PAUSED"
                        run.last_error_code, run.last_error_message = "PROJECT_IMPORTED", "迁移后暂停；原任务不会自动提交或查询。原制作记录保留在 Trace。"
                    run.provider_task_id = None
                    run.next_poll_at = None
                    run.updated_at = now
                contents.append(content)
            folder.mkdir(parents=True, exist_ok=False)
            allowed_media = {media.archive_path for media in manifest.media if media.status == "included"}
            missing_entries = allowed_media.difference(names)
            if missing_entries:
                raise ValueError("项目包缺少已声明收集的媒体：" + ", ".join(sorted(missing_entries)))
            extract_names = [name for name in names if name in allowed_media or name.startswith("traces/") or name in {"manifest.json", "START_HERE.md"}]
            progress.begin("extracting", "正在导入媒体和完整制作记录", total_files=len(extract_names),
                total_bytes=sum(archive.getinfo(name).file_size for name in extract_names))
            for name in extract_names:
                progress.check()
                destination = folder / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, destination.open("wb") as target:
                    progress.copy(source, target)
                if name in allowed_media and destination.stat().st_size != by_path[name].bytes:
                    raise ValueError("项目包媒体大小不一致：" + name)
                progress.advance(files=1)
            for content in contents:
                for take in content.takes:
                    if not Path(take.media_path).is_file():
                        take.availability = TakeAvailability.MISSING
                for run in content.video_edit_runs:
                    for source in run.source_files:
                        local = Path(source.path)
                        if local.is_file():
                            stat = local.stat()
                            source.size_bytes, source.modified_ns = stat.st_size, stat.st_mtime_ns
            ordered = sorted(contents, key=lambda content: content.document.project.id != manifest.root_project_id)
            summary = StudioProjectImportSummary(project_id=manifest.root_project_id, episode_ids=ids,
                media_count=len(allowed_media), missing_media=[media for media in manifest.media if media.status == "missing"],
                trace_path=str(folder / "traces"),
                notice="已导入当前内容、媒体和剪辑；完整制作过程保存在 Trace。原 Agent、供应商任务与本地导出未继续执行。")
            with service.studio.lock:
                progress.begin("committing", "正在保存项目，提交完成后即可打开")
                with service.repository.atomic():
                    inserted = service.repository.insert_project_media_bundle(
                        [content.document for content in ordered],
                        [task for content in ordered for task in content.take_tasks],
                        [take for content in ordered for take in content.takes],
                        [run for content in ordered for run in content.video_edit_runs])
                    if not inserted:
                        raise StudioContentConflict("本机已有同 ID 的项目、集或 Take 来源；原内容不会覆盖。")
                    if on_complete is not None:
                        on_complete(summary)
                committed = True
        return StudioProjectPackageImport(project=service.studio.current_project(manifest.root_project_id),
            episode_ids=ids, media_count=len(allowed_media),
            missing_media=[media for media in manifest.media if media.status == "missing"], trace_path=str(folder / "traces"),
            notice="已导入当前内容、媒体和剪辑；完整制作过程保存在 Trace。原 Agent、供应商任务与本地导出未继续执行。")
    except (BadZipFile, KeyError) as exc:
        raise ValueError("无法读取项目包，文件格式不完整或已损坏。") from exc
    finally:
        if not committed and folder.exists():
            shutil.rmtree(folder)
