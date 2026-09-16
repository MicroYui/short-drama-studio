"""Persisted, findable local project package jobs with cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import shutil
from threading import Event, Lock, Thread
import time
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue
from short_drama.studio_package_models import (
    StudioProjectImportSummary, StudioProjectPackage, StudioProjectTransfer, StudioProjectTransfers,
)
from short_drama.studio_package_progress import PackageCancelled, PackageProgress
from short_drama.studio_project_package import create_project_package, import_project_package
from short_drama.studio_records import cursor_for_record, decode_record_cursor

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_ACTIVE_LOCK = Lock()


@dataclass
class _TransferControl:
    cancelled: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    io_slot: Lock = field(default_factory=Lock)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise PackageCancelled()


_ACTIVE: dict[tuple[str, str], _TransferControl] = {}


def _key(service: ShortDramaService, identity: str) -> tuple[str, str]:
    return str(service.repository.database_path.resolve()), identity


def _save(service: ShortDramaService, record: StudioProjectTransfer) -> None:
    with service.studio.lock, service.repository.atomic():
        previous = service.repository.get_project_transfer(record.id)
        if previous is not None:
            if previous.status in _TERMINAL:
                # A delayed progress update cannot erase a completed result.
                for name in StudioProjectTransfer.model_fields:
                    setattr(record, name, getattr(previous, name))
                return
            if previous.status == "cancelling" and record.status == "running":
                record.status, record.cancellable = "cancelling", False
                record.message = previous.message
        record.updated_at = datetime.now(UTC).isoformat()
        if record.status in _TERMINAL:
            record.finished_at = record.updated_at
            record.cancellable = False
        service.repository.save_project_transfer(record)


def get_project_transfer(service: ShortDramaService, identity: str) -> StudioProjectTransfer:
    record = service.repository.get_project_transfer(identity)
    if record is None:
        raise KeyError(identity)
    return record


def list_project_transfers(service: ShortDramaService, *, limit: int = 20,
                           before: str | None = None) -> StudioProjectTransfers:
    size = max(1, min(limit, 100))
    records = service.repository.list_project_transfers(limit=size + 1,
        before=decode_record_cursor(before) if before else None)
    shown = records[:size]
    cursor = cursor_for_record(cast(dict[str, JsonValue], shown[-1].model_dump(mode="json"))) if len(records) > size else None
    return StudioProjectTransfers(transfers=shown, next_cursor=cursor)


def _cleanup(record: StudioProjectTransfer, *, include_result: bool) -> None:
    if record.input_path:
        Path(record.input_path).unlink(missing_ok=True)
    if include_result and record.working_path:
        working = Path(record.working_path)
        if record.operation == "collect":
            working.with_suffix(".tmp").unlink(missing_ok=True)
            working.unlink(missing_ok=True)
        elif working.exists():
            shutil.rmtree(working)


def _run(service: ShortDramaService, record: StudioProjectTransfer, control: _TransferControl,
         package_id: str | None) -> None:
    last_write, last_stage = 0.0, ""

    def update(progress: PackageProgress) -> None:
        nonlocal last_write, last_stage
        now = time.monotonic()
        if progress.stage == last_stage and now - last_write < 0.25:
            return
        record.stage, record.message = progress.stage, progress.message
        record.processed_files, record.total_files = progress.processed_files, progress.total_files
        record.processed_bytes, record.total_bytes = progress.processed_bytes, progress.total_bytes
        record.cancellable = progress.stage != "committing"
        _save(service, record)
        last_write, last_stage = now, progress.stage

    progress = PackageProgress(on_update=update, check_cancelled=control.check)
    acquired = False

    def complete() -> None:
        record.status, record.stage, record.message = "completed", "completed", "项目包已收集，可下载保存" if record.operation == "collect" else "项目已导入，可打开继续编辑"
        _save(service, record)

    def collected(result: StudioProjectPackage) -> None:
        record.package = result
        complete()

    def imported(result: StudioProjectImportSummary) -> None:
        record.imported, record.project_id = result, result.project_id
        complete()

    try:
        control.check()
        # A full history package can be large. Serialize local transfer IO per
        # workspace while queued jobs remain visible and independently cancellable.
        while not control.io_slot.acquire(timeout=0.2):
            control.check()
        acquired = True
        control.check()
        record.status = "running"
        _save(service, record)
        if record.operation == "collect":
            if record.project_id is None:
                raise ValueError("收集任务缺少项目")
            create_project_package(service, record.project_id, progress=progress, package_id=package_id, on_complete=collected)
        else:
            if record.input_path is None or record.working_path is None:
                raise ValueError("导入任务缺少上传文件")
            import_project_package(service, Path(record.input_path), progress=progress,
                destination_folder=Path(record.working_path), on_complete=imported)
    except Exception as exc:
        # A completed import and its result record share one SQLite commit. Do
        # not remove committed media if an unrelated final cleanup fails.
        current = get_project_transfer(service, record.id)
        if current.status != "completed":
            try:
                _cleanup(record, include_result=True)
            except OSError as cleanup_error:
                record.status, record.error = "failed", f"清理临时文件失败：{cleanup_error}；原错误：{exc}"
                record.message = "任务已停止，临时文件未能全部清理"
            else:
                record.status = "cancelled" if isinstance(exc, PackageCancelled) else "failed"
                record.error = None if isinstance(exc, PackageCancelled) else str(exc)
                record.message = "已取消，未写入项目或保留不完整项目包" if isinstance(exc, PackageCancelled) else "收集或导入失败，原内容未覆盖"
            record.stage = record.status
            _save(service, record)
    finally:
        try:
            _cleanup(record, include_result=False)
        finally:
            if acquired:
                control.io_slot.release()
            with _ACTIVE_LOCK:
                _ACTIVE.pop(_key(service, record.id), None)
            control.done.set()


def _start(service: ShortDramaService, record: StudioProjectTransfer, *, package_id: str | None = None,
           background: bool = True) -> StudioProjectTransfer:
    control = _TransferControl()
    with service.studio.lock:
        _save(service, record)
        with _ACTIVE_LOCK:
            database = _key(service, record.id)[0]
            existing = next((value for key, value in _ACTIVE.items() if key[0] == database), None)
            if existing is not None:
                control.io_slot = existing.io_slot
            _ACTIVE[_key(service, record.id)] = control
        # Return an immutable snapshot even if a small package completes before
        # FastAPI serializes the accepted response.
        accepted = record.model_copy(deep=True)
    if background:
        try:
            Thread(target=_run, args=(service, record, control, package_id), daemon=True, name=record.id).start()
        except RuntimeError as exc:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(_key(service, record.id), None)
            record.status, record.error, record.message = "failed", str(exc), "后台任务未能启动"
            _cleanup(record, include_result=True)
            _save(service, record)
            return record
    else:
        _run(service, record, control, package_id)
        return get_project_transfer(service, record.id)
    return accepted


def start_project_collection(service: ShortDramaService, project_id: str, *, background: bool = True) -> StudioProjectTransfer:
    document = service.repository.get_project(project_id)
    if document is None:
        raise KeyError(project_id)
    root_id = document.project.parent_project_id or project_id
    package_id = f"project-package-{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    record = StudioProjectTransfer(id=f"project-transfer-{uuid4().hex[:12]}", operation="collect",
        project_id=root_id, project_name=document.project.name, created_at=now, updated_at=now,
        working_path=str(service.workspace_root / root_id / "project-packages" / f"{package_id}.zip"))
    return _start(service, record, package_id=package_id, background=background)


def start_project_import(service: ShortDramaService, uploaded: Path, filename: str, *, background: bool = True) -> StudioProjectTransfer:
    if not uploaded.is_file():
        raise FileNotFoundError("上传的项目包未找到")
    now = datetime.now(UTC).isoformat()
    record = StudioProjectTransfer(id=f"project-transfer-{uuid4().hex[:12]}", operation="import",
        project_name=Path(filename).name or "项目包", created_at=now, updated_at=now, input_path=str(uploaded),
        working_path=str(service.workspace_root / "imported-projects" / f"import-{uuid4().hex[:12]}"))
    return _start(service, record, background=background)


def cancel_project_transfer(service: ShortDramaService, identity: str) -> StudioProjectTransfer:
    with service.studio.lock:
        record = get_project_transfer(service, identity)
        if record.status in _TERMINAL or not record.cancellable:
            return record
        with _ACTIVE_LOCK:
            control = _ACTIVE.get(_key(service, identity))
        if control is not None:
            control.cancelled.set()
            record.status, record.cancellable = "cancelling", False
            outcome = "不生成项目包" if record.operation == "collect" else "不提交导入内容"
            record.message = f"正在取消；当前内容处理结束后清理临时文件，{outcome}"
            _save(service, record)
            return record
    # Orphan cleanup can involve many files; do it outside the content lock.
    _recover_one(service, record)
    return get_project_transfer(service, identity)


def _recover_one(service: ShortDramaService, record: StudioProjectTransfer) -> None:
    with service.studio.lock:
        # The list may predate a just-completed worker. Re-read before touching
        # files so startup/list recovery never deletes a valid final result.
        record = get_project_transfer(service, record.id)
        if record.status in _TERMINAL:
            return
        with _ACTIVE_LOCK:
            if _key(service, record.id) in _ACTIVE:
                return
    try:
        _cleanup(record, include_result=True)
    except OSError as exc:
        record.error = f"上次任务中断；临时文件清理失败：{exc}"
    else:
        record.error = "应用退出中断了本地收集或导入。原项目未覆盖，可重新开始。"
    record.status, record.stage, record.message = "failed", "interrupted", "上次任务已中断"
    _save(service, record)


def recover_interrupted_project_transfers(service: ShortDramaService) -> None:
    before: tuple[str, str] | None = None
    while records := service.repository.list_project_transfers(limit=100, before=before,
            statuses=["queued", "running", "cancelling"]):
        for record in records:
            with _ACTIVE_LOCK:
                active = _key(service, record.id) in _ACTIVE
            if not active:
                _recover_one(service, record)
        before = records[-1].created_at, records[-1].id
