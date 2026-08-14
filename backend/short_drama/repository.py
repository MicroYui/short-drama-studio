"""SQLite persistence for projects, recoverable tasks, takes, and exports."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from short_drama.models import (
    ExportRecord,
    Freshness,
    GenerationTask,
    ProjectDocument,
    Take,
    TaskStatus,
)


_RUNNABLE_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.QUEUED,
    TaskStatus.SUBMITTING,
    TaskStatus.ACKNOWLEDGED,
    TaskStatus.RUNNING,
)


class ShortDramaRepository:
    """Small SQLite repository with JSON domain documents and indexed lifecycle rows."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    document_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generation_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_generation_tasks_project_status
                    ON generation_tasks(project_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_generation_tasks_fingerprint
                    ON generation_tasks(request_fingerprint);

                CREATE TABLE IF NOT EXISTS provider_submissions (
                    request_fingerprint TEXT PRIMARY KEY,
                    provider_task_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    submit_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS takes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES generation_tasks(id),
                    candidate_index INTEGER NOT NULL,
                    is_selected INTEGER NOT NULL,
                    freshness TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_takes_project_shot
                    ON takes(project_id, shot_id, created_at);

                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    manifest_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_exports_project_manifest
                    ON exports(project_id, manifest_id, created_at);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, datetime('now'))"
            )

    def save_project(self, document: ProjectDocument) -> None:
        project = document.project
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, stage, updated_at, document_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    stage = excluded.stage,
                    updated_at = excluded.updated_at,
                    document_json = excluded.document_json
                """,
                (
                    project.id,
                    project.name,
                    project.stage.value,
                    project.updated_at,
                    document.model_dump_json(),
                ),
            )

    def project_exists(self, project_id: str) -> bool:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row is not None

    def get_project(self, project_id: str) -> ProjectDocument | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        return ProjectDocument.model_validate_json(cast(str, row["document_json"]))

    def list_projects(self) -> list[ProjectDocument]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM projects ORDER BY updated_at DESC, id ASC"
            ).fetchall()
        return [
            ProjectDocument.model_validate_json(cast(str, row["document_json"]))
            for row in rows
        ]

    def delete_project(self, project_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            deleted = cursor.rowcount > 0
        return deleted

    def save_task(self, task: GenerationTask) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO generation_tasks(
                    id, project_id, shot_id, status, candidate_index,
                    request_fingerprint, created_at, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    task.id,
                    task.project_id,
                    task.shot_id,
                    task.status.value,
                    task.candidate_index,
                    task.request_fingerprint,
                    task.created_at,
                    task.updated_at,
                    task.model_dump_json(),
                ),
            )

    def get_task(self, task_id: str) -> GenerationTask | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM generation_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return GenerationTask.model_validate_json(cast(str, row["record_json"]))

    def list_tasks(self, project_id: str) -> list[GenerationTask]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM generation_tasks
                WHERE project_id = ? ORDER BY created_at ASC, candidate_index ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [GenerationTask.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def list_tasks_for_shot(self, project_id: str, shot_id: str) -> list[GenerationTask]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM generation_tasks
                WHERE project_id = ? AND shot_id = ?
                ORDER BY created_at ASC, candidate_index ASC, id ASC
                """,
                (project_id, shot_id),
            ).fetchall()
        return [GenerationTask.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def next_runnable_task(self, project_id: str) -> GenerationTask | None:
        placeholders = ", ".join("?" for _status in _RUNNABLE_STATUSES)
        values: list[str] = [project_id, *[status.value for status in _RUNNABLE_STATUSES]]
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT record_json FROM generation_tasks
                WHERE project_id = ? AND status IN ({placeholders})
                ORDER BY created_at ASC, candidate_index ASC, id ASC LIMIT 1
                """,
                values,
            ).fetchone()
        if row is None:
            return None
        return GenerationTask.model_validate_json(cast(str, row["record_json"]))

    def count_runnable_tasks(self, project_id: str) -> int:
        placeholders = ", ".join("?" for _status in _RUNNABLE_STATUSES)
        values: list[str] = [project_id, *[status.value for status in _RUNNABLE_STATUSES]]
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count FROM generation_tasks
                WHERE project_id = ? AND status IN ({placeholders})
                """,
                values,
            ).fetchone()
        return 0 if row is None else cast(int, row["count"])

    def recover_interrupted_submissions(self) -> int:
        """Convert pre-acknowledgement submissions to the explicit reconciliation state."""

        recovered = 0
        with self._lock:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT record_json FROM generation_tasks WHERE status = ?",
                    (TaskStatus.SUBMITTING.value,),
                ).fetchall()
            for row in rows:
                task = GenerationTask.model_validate_json(cast(str, row["record_json"]))
                task.status = TaskStatus.SUBMIT_UNKNOWN
                task.error_code = "PROCESS_INTERRUPTED_DURING_SUBMIT"
                task.error_message = "提交阶段进程中断，需要先按请求指纹对账。"
                self.save_task(task)
                recovered += 1
        return recovered

    def record_provider_submission(
        self,
        *,
        request_fingerprint: str,
        provider_task_id: str,
        provider: str,
        created_at: str,
    ) -> tuple[str, bool]:
        """Record an acknowledged provider submit exactly once per request fingerprint."""

        with self._lock, self._connection() as connection:
            existing = connection.execute(
                """
                SELECT provider_task_id FROM provider_submissions
                WHERE request_fingerprint = ?
                """,
                (request_fingerprint,),
            ).fetchone()
            if existing is not None:
                return cast(str, existing["provider_task_id"]), False
            connection.execute(
                """
                INSERT INTO provider_submissions(
                    request_fingerprint, provider_task_id, provider, submit_count, created_at
                ) VALUES(?, ?, ?, 1, ?)
                """,
                (request_fingerprint, provider_task_id, provider, created_at),
            )
        return provider_task_id, True

    def find_provider_submission(self, request_fingerprint: str) -> str | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT provider_task_id FROM provider_submissions
                WHERE request_fingerprint = ?
                """,
                (request_fingerprint,),
            ).fetchone()
        return None if row is None else cast(str, row["provider_task_id"])

    def provider_submission_count(self, request_fingerprint: str) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT submit_count FROM provider_submissions
                WHERE request_fingerprint = ?
                """,
                (request_fingerprint,),
            ).fetchone()
        return 0 if row is None else cast(int, row["submit_count"])

    def save_take(self, take: Take) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO takes(
                    id, project_id, shot_id, task_id, candidate_index,
                    is_selected, freshness, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    is_selected = excluded.is_selected,
                    freshness = excluded.freshness,
                    record_json = excluded.record_json
                """,
                (
                    take.id,
                    take.project_id,
                    take.shot_id,
                    take.task_id,
                    take.candidate_index,
                    1 if take.is_selected else 0,
                    take.freshness.value,
                    take.created_at,
                    take.model_dump_json(),
                ),
            )

    def get_take(self, take_id: str) -> Take | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM takes WHERE id = ?", (take_id,)
            ).fetchone()
        if row is None:
            return None
        return Take.model_validate_json(cast(str, row["record_json"]))

    def list_takes(self, project_id: str) -> list[Take]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM takes WHERE project_id = ?
                ORDER BY shot_id ASC, created_at ASC, candidate_index ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [Take.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def list_takes_for_shot(self, project_id: str, shot_id: str) -> list[Take]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM takes
                WHERE project_id = ? AND shot_id = ?
                ORDER BY created_at ASC, candidate_index ASC, id ASC
                """,
                (project_id, shot_id),
            ).fetchall()
        return [Take.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def select_take(self, take_id: str) -> Take | None:
        with self._lock:
            take = self.get_take(take_id)
            if take is None:
                return None
            for existing in self.list_takes_for_shot(take.project_id, take.shot_id):
                next_selected = existing.id == take_id
                if existing.is_selected != next_selected:
                    existing.is_selected = next_selected
                    self.save_take(existing)
            take.is_selected = True
            return take

    def mark_takes_stale(self, project_id: str, shot_ids: Sequence[str]) -> int:
        shot_id_set = set(shot_ids)
        changed = 0
        with self._lock:
            for take in self.list_takes(project_id):
                if take.shot_id not in shot_id_set or take.freshness == Freshness.STALE:
                    continue
                take.freshness = Freshness.STALE
                take.is_selected = False
                self.save_take(take)
                changed += 1
        return changed

    def save_export(self, export: ExportRecord) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO exports(id, project_id, manifest_id, created_at, record_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json
                """,
                (
                    export.id,
                    export.project_id,
                    export.manifest_id,
                    export.created_at,
                    export.model_dump_json(),
                ),
            )

    def list_exports(self, project_id: str) -> list[ExportRecord]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM exports WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [ExportRecord.model_validate_json(cast(str, row["record_json"])) for row in rows]
