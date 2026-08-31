"""SQLite persistence for projects, recoverable tasks, takes, and exports."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from short_drama.h3_models import H3ProductionRoutePlan, H3ProviderRun, H3UploadedInput
from short_drama.image_models import ImageGenerationTask, ImageTaskStatus
from short_drama.models import (
    AudioInspectionReport,
    EpisodeReviewReport,
    EvidenceStatus,
    ExportRecord,
    Freshness,
    GenerationTask,
    PlanningOutlineSnapshot,
    PlanningReviewSnapshot,
    PlanningRunRecord,
    ProjectDocument,
    RouteEvidence,
    Take,
    TakeComparisonReport,
    TaskStatus,
    VideoInspectionReport,
)
from short_drama.script_documents import (
    PlanningScriptDocument,
    PlanningScriptDocumentKind,
    PlanningScriptSearchHit,
)
from short_drama.visual_input_quality import (
    InputCompatibilityReport,
    KeyframeComparisonReport,
    ReferenceVisualRerankReport,
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
                    provider TEXT NOT NULL DEFAULT 'mock',
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

                CREATE TABLE IF NOT EXISTS provider_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE REFERENCES generation_tasks(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_runs_project
                    ON provider_runs(project_id, provider, updated_at);

                CREATE TABLE IF NOT EXISTS provider_uploads (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS route_evidence (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES generation_tasks(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_route_evidence_project_shot
                    ON route_evidence(project_id, shot_id, created_at);

                CREATE TABLE IF NOT EXISTS image_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_image_tasks_project_asset
                    ON image_tasks(project_id, asset_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_image_tasks_project_status
                    ON image_tasks(project_id, status, created_at);

                CREATE TABLE IF NOT EXISTS video_inspections (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    take_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_video_inspections_project_shot
                    ON video_inspections(project_id, shot_id, created_at);

                CREATE TABLE IF NOT EXISTS take_comparisons (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_take_comparisons_project_shot
                    ON take_comparisons(project_id, shot_id, created_at);

                CREATE TABLE IF NOT EXISTS visual_input_reports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visual_input_reports_project_shot
                    ON visual_input_reports(project_id, shot_id, kind, created_at);

                CREATE TABLE IF NOT EXISTS audio_inspections (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    manifest_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audio_inspections_project_manifest
                    ON audio_inspections(project_id, manifest_id, created_at);

                CREATE TABLE IF NOT EXISTS episode_reviews (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    manifest_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_episode_reviews_project_manifest
                    ON episode_reviews(project_id, manifest_id, updated_at);

                CREATE TABLE IF NOT EXISTS h3_route_plans (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_h3_route_plans_project_version
                    ON h3_route_plans(project_id, version);
                CREATE INDEX IF NOT EXISTS idx_h3_route_plans_project_updated
                    ON h3_route_plans(project_id, updated_at);

                CREATE TABLE IF NOT EXISTS planning_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_planning_runs_status_updated
                    ON planning_runs(status, updated_at);

                CREATE TABLE IF NOT EXISTS planning_run_artifacts (
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, kind, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_planning_run_artifacts_run_kind
                    ON planning_run_artifacts(run_id, kind, sequence);

                CREATE TABLE IF NOT EXISTS planning_script_documents (
                    run_id TEXT NOT NULL,
                    revision_round INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scene_sequence INTEGER,
                    created_at TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, revision_round, document_id)
                );
                CREATE INDEX IF NOT EXISTS idx_planning_script_documents_revision
                    ON planning_script_documents(run_id, revision_round, kind, scene_sequence);
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS planning_script_documents_fts
                    USING fts5(
                        run_id UNINDEXED,
                        revision_round UNINDEXED,
                        document_id UNINDEXED,
                        title,
                        metadata,
                        body,
                        tokenize='trigram'
                    )
                    """
                )
            except sqlite3.OperationalError:
                # Older SQLite builds may omit the trigram tokenizer.  FTS5 itself
                # is still useful for whitespace-delimited metadata in that case.
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS planning_script_documents_fts
                    USING fts5(
                        run_id UNINDEXED,
                        revision_round UNINDEXED,
                        document_id UNINDEXED,
                        title,
                        metadata,
                        body,
                        tokenize='unicode61'
                    )
                    """
                )
            task_columns = {
                cast(str, row["name"])
                for row in connection.execute("PRAGMA table_info(generation_tasks)").fetchall()
            }
            if "provider" not in task_columns:
                connection.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN provider TEXT NOT NULL DEFAULT 'mock'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_tasks_project_provider_status "
                "ON generation_tasks(project_id, provider, status, created_at)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(3, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(4, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(5, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(6, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(7, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(8, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(9, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(10, datetime('now'))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(11, datetime('now'))"
            )

    def save_planning_run(self, run: PlanningRunRecord) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO planning_runs(id, status, updated_at, record_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (run.id, run.status.value, run.updated_at, run.model_dump_json()),
            )

    def get_planning_run(self, run_id: str) -> PlanningRunRecord | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM planning_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return PlanningRunRecord.model_validate_json(cast(str, row["record_json"]))

    def list_planning_runs(self) -> list[PlanningRunRecord]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT record_json FROM planning_runs ORDER BY updated_at DESC, id ASC"
            ).fetchall()
        return [
            PlanningRunRecord.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_planning_outline_snapshot(
        self,
        run_id: str,
        snapshot: PlanningOutlineSnapshot,
    ) -> None:
        self._save_planning_artifact(
            run_id,
            "OUTLINE",
            snapshot.revision_round,
            snapshot.created_at,
            snapshot.model_dump_json(),
        )

    def list_planning_outline_snapshots(
        self,
        run_id: str,
    ) -> list[PlanningOutlineSnapshot]:
        return [
            PlanningOutlineSnapshot.model_validate_json(value)
            for value in self._list_planning_artifacts(run_id, "OUTLINE")
        ]

    def save_planning_review_snapshot(
        self,
        run_id: str,
        snapshot: PlanningReviewSnapshot,
    ) -> None:
        self._save_planning_artifact(
            run_id,
            "REVIEW",
            snapshot.review_round,
            snapshot.created_at,
            snapshot.model_dump_json(),
        )

    def list_planning_review_snapshots(
        self,
        run_id: str,
    ) -> list[PlanningReviewSnapshot]:
        return [
            PlanningReviewSnapshot.model_validate_json(value)
            for value in self._list_planning_artifacts(run_id, "REVIEW")
        ]

    def replace_planning_script_documents(
        self,
        run_id: str,
        revision_round: int,
        documents: Sequence[PlanningScriptDocument],
    ) -> None:
        """Replace one outline revision's retrieval projection atomically."""

        for document in documents:
            if document.run_id != run_id or document.revision_round != revision_round:
                raise ValueError("Planning script document scope does not match replacement")
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM planning_script_documents WHERE run_id = ? AND revision_round = ?",
                (run_id, revision_round),
            )
            connection.execute(
                "DELETE FROM planning_script_documents_fts WHERE run_id = ? AND revision_round = ?",
                (run_id, str(revision_round)),
            )
            for document in documents:
                connection.execute(
                    """
                    INSERT INTO planning_script_documents(
                        run_id, revision_round, document_id, kind, scene_sequence,
                        created_at, document_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.run_id,
                        document.revision_round,
                        document.document_id,
                        document.kind.value,
                        document.scene_sequence,
                        document.created_at,
                        document.model_dump_json(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO planning_script_documents_fts(
                        run_id, revision_round, document_id, title, metadata, body
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.run_id,
                        str(document.revision_round),
                        document.document_id,
                        document.title,
                        document.search_metadata,
                        document.body,
                    ),
                )

    def list_planning_script_documents(
        self,
        run_id: str,
        revision_round: int,
    ) -> list[PlanningScriptDocument]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT document_json
                FROM planning_script_documents
                WHERE run_id = ? AND revision_round = ?
                ORDER BY CASE kind WHEN 'STORY_BIBLE' THEN 0 ELSE 1 END,
                         scene_sequence ASC,
                         document_id ASC
                """,
                (run_id, revision_round),
            ).fetchall()
        return [
            PlanningScriptDocument.model_validate_json(
                cast(str, row["document_json"])
            )
            for row in rows
        ]

    @staticmethod
    def _planning_fts_query(value: str) -> str:
        """Build a quoted trigram OR query from Chinese or word-like text."""

        segments = re.findall(r"[\u3400-\u9fff]{3,}|[A-Za-z0-9_]{3,}", value.casefold())
        terms: list[str] = []
        for segment in segments:
            candidates = (
                [segment[index : index + 3] for index in range(len(segment) - 2)]
                if re.fullmatch(r"[\u3400-\u9fff]+", segment)
                else [segment]
            )
            for candidate in candidates:
                escaped = candidate.replace('"', '""')
                if escaped not in terms:
                    terms.append(escaped)
                if len(terms) >= 32:
                    break
            if len(terms) >= 32:
                break
        return " OR ".join(f'"{term}"' for term in terms)

    def search_planning_script_documents(
        self,
        run_id: str,
        revision_round: int,
        query: str,
        *,
        limit: int = 8,
        include_story_bible: bool = True,
    ) -> list[PlanningScriptSearchHit]:
        """Rank documents within one script revision using SQLite FTS5 BM25."""

        fts_query = self._planning_fts_query(query)
        with self._lock, self._connection() as connection:
            if fts_query:
                kind_filter = "" if include_story_bible else "AND d.kind = ?"
                values: list[object] = [
                    fts_query,
                    run_id,
                    str(revision_round),
                ]
                if not include_story_bible:
                    values.append(PlanningScriptDocumentKind.SCENE.value)
                values.append(limit)
                rows = connection.execute(
                    f"""
                    SELECT d.document_json,
                           bm25(
                               planning_script_documents_fts,
                               0.0, 0.0, 0.0, 8.0, 4.0, 1.0
                           ) AS score
                    FROM planning_script_documents_fts
                    JOIN planning_script_documents AS d
                      ON d.run_id = planning_script_documents_fts.run_id
                     AND d.revision_round = CAST(
                         planning_script_documents_fts.revision_round AS INTEGER
                     )
                     AND d.document_id = planning_script_documents_fts.document_id
                    WHERE planning_script_documents_fts MATCH ?
                      AND planning_script_documents_fts.run_id = ?
                      AND planning_script_documents_fts.revision_round = ?
                      {kind_filter}
                    ORDER BY score ASC, d.scene_sequence ASC, d.document_id ASC
                    LIMIT ?
                    """,
                    values,
                ).fetchall()
            else:
                kind_filter = "" if include_story_bible else "AND kind = ?"
                values = [run_id, revision_round]
                if not include_story_bible:
                    values.append(PlanningScriptDocumentKind.SCENE.value)
                values.extend([f"%{query.strip()}%", limit])
                rows = connection.execute(
                    f"""
                    SELECT document_json, 0.0 AS score
                    FROM planning_script_documents
                    WHERE run_id = ? AND revision_round = ?
                      {kind_filter}
                      AND document_json LIKE ?
                    ORDER BY scene_sequence ASC, document_id ASC
                    LIMIT ?
                    """,
                    values,
                ).fetchall()
        return [
            PlanningScriptSearchHit(
                document=PlanningScriptDocument.model_validate_json(
                    cast(str, row["document_json"])
                ),
                bm25_score=float(row["score"]),
                matched_by=["FTS5_BM25" if fts_query else "METADATA_SUBSTRING"],
            )
            for row in rows
        ]

    def _save_planning_artifact(
        self,
        run_id: str,
        kind: str,
        sequence: int,
        created_at: str,
        artifact_json: str,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO planning_run_artifacts(
                    run_id, kind, sequence, created_at, artifact_json
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(run_id, kind, sequence) DO UPDATE SET
                    created_at = excluded.created_at,
                    artifact_json = excluded.artifact_json
                """,
                (run_id, kind, sequence, created_at, artifact_json),
            )

    def _list_planning_artifacts(self, run_id: str, kind: str) -> list[str]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT artifact_json
                FROM planning_run_artifacts
                WHERE run_id = ? AND kind = ?
                ORDER BY sequence ASC
                """,
                (run_id, kind),
            ).fetchall()
        return [cast(str, row["artifact_json"]) for row in rows]

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
                    id, project_id, shot_id, provider, status, candidate_index,
                    request_fingerprint, created_at, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider = excluded.provider,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    task.id,
                    task.project_id,
                    task.shot_id,
                    task.provider,
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

    def next_runnable_task(
        self,
        project_id: str,
        provider: str | None = None,
    ) -> GenerationTask | None:
        placeholders = ", ".join("?" for _status in _RUNNABLE_STATUSES)
        values: list[str] = [project_id, *[status.value for status in _RUNNABLE_STATUSES]]
        provider_clause = ""
        if provider is not None:
            provider_clause = " AND provider = ?"
            values.append(provider)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT record_json FROM generation_tasks
                WHERE project_id = ? AND status IN ({placeholders})
                {provider_clause}
                ORDER BY created_at ASC, candidate_index ASC, id ASC LIMIT 1
                """,
                values,
            ).fetchone()
        if row is None:
            return None
        return GenerationTask.model_validate_json(cast(str, row["record_json"]))

    def count_runnable_tasks(self, project_id: str, provider: str | None = None) -> int:
        placeholders = ", ".join("?" for _status in _RUNNABLE_STATUSES)
        values: list[str] = [project_id, *[status.value for status in _RUNNABLE_STATUSES]]
        provider_clause = ""
        if provider is not None:
            provider_clause = " AND provider = ?"
            values.append(provider)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count FROM generation_tasks
                WHERE project_id = ? AND status IN ({placeholders})
                {provider_clause}
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
                if task.provider == "minimax":
                    evidence = self.get_route_evidence_for_task(task.id)
                    if evidence is not None:
                        evidence.status = EvidenceStatus.SUBMIT_UNKNOWN
                        self.save_route_evidence(evidence)
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

    def save_provider_run(self, run: H3ProviderRun) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO provider_runs(
                    id, task_id, project_id, provider, updated_at, record_json
                ) VALUES(?, ?, ?, 'minimax', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    run.id,
                    run.task_id,
                    run.project_id,
                    run.updated_at,
                    run.model_dump_json(),
                ),
            )

    def get_provider_run(self, task_id: str) -> H3ProviderRun | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM provider_runs WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return H3ProviderRun.model_validate_json(cast(str, row["record_json"]))

    def list_provider_runs(self, project_id: str) -> list[H3ProviderRun]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM provider_runs
                WHERE project_id = ? ORDER BY updated_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [H3ProviderRun.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def save_provider_upload(
        self,
        *,
        cache_key: str,
        provider: str,
        record: H3UploadedInput,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO provider_uploads(cache_key, provider, expires_at, record_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    expires_at = excluded.expires_at,
                    record_json = excluded.record_json
                """,
                (cache_key, provider, record.expires_at, record.model_dump_json()),
            )

    def get_provider_upload(self, cache_key: str, now: str) -> H3UploadedInput | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT record_json FROM provider_uploads
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now),
            ).fetchone()
        if row is None:
            return None
        return H3UploadedInput.model_validate_json(cast(str, row["record_json"]))

    def save_route_evidence(self, evidence: RouteEvidence) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO route_evidence(
                    id, project_id, shot_id, task_id, status, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    record_json = excluded.record_json
                """,
                (
                    evidence.id,
                    evidence.project_id,
                    evidence.shot_id,
                    evidence.task_id,
                    evidence.status.value,
                    evidence.created_at,
                    evidence.model_dump_json(),
                ),
            )

    def get_route_evidence_for_take(self, take_id: str) -> RouteEvidence | None:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT record_json FROM route_evidence").fetchall()
        for row in rows:
            evidence = RouteEvidence.model_validate_json(cast(str, row["record_json"]))
            if evidence.take_id == take_id:
                return evidence
        return None

    def get_route_evidence_for_task(self, task_id: str) -> RouteEvidence | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM route_evidence WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return RouteEvidence.model_validate_json(cast(str, row["record_json"]))

    def list_route_evidence(self, project_id: str) -> list[RouteEvidence]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM route_evidence
                WHERE project_id = ? ORDER BY created_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [RouteEvidence.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def save_video_inspection(self, report: VideoInspectionReport) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO video_inspections(
                    id, project_id, shot_id, take_id, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json
                """,
                (
                    report.id,
                    report.project_id,
                    report.shot_id,
                    report.take_id,
                    report.created_at,
                    report.model_dump_json(),
                ),
            )

    def get_video_inspection(self, report_id: str) -> VideoInspectionReport | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM video_inspections WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return VideoInspectionReport.model_validate_json(cast(str, row["record_json"]))

    def list_video_inspections(self, project_id: str) -> list[VideoInspectionReport]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM video_inspections
                WHERE project_id = ? ORDER BY created_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [
            VideoInspectionReport.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_take_comparison(self, report: TakeComparisonReport) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO take_comparisons(
                    id, project_id, shot_id, decision, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    decision = excluded.decision,
                    record_json = excluded.record_json
                """,
                (
                    report.id,
                    report.project_id,
                    report.shot_id,
                    report.decision.value,
                    report.created_at,
                    report.model_dump_json(),
                ),
            )

    def get_take_comparison(self, report_id: str) -> TakeComparisonReport | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM take_comparisons WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return TakeComparisonReport.model_validate_json(cast(str, row["record_json"]))

    def list_take_comparisons(self, project_id: str) -> list[TakeComparisonReport]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM take_comparisons
                WHERE project_id = ? ORDER BY created_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [
            TakeComparisonReport.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_reference_visual_rerank(self, report: ReferenceVisualRerankReport) -> None:
        self._save_visual_input_report(
            report_id=report.id,
            project_id=report.project_id,
            shot_id=report.shot_id,
            kind="REFERENCE_RERANK",
            created_at=report.created_at,
            record_json=report.model_dump_json(),
        )

    def list_reference_visual_reranks(
        self,
        project_id: str,
    ) -> list[ReferenceVisualRerankReport]:
        return [
            ReferenceVisualRerankReport.model_validate_json(record)
            for record in self._list_visual_input_report_json(
                project_id,
                kind="REFERENCE_RERANK",
            )
        ]

    def save_keyframe_comparison(self, report: KeyframeComparisonReport) -> None:
        self._save_visual_input_report(
            report_id=report.id,
            project_id=report.project_id,
            shot_id=report.shot_id,
            kind="KEYFRAME_COMPARISON",
            created_at=report.created_at,
            record_json=report.model_dump_json(),
        )

    def list_keyframe_comparisons(
        self,
        project_id: str,
    ) -> list[KeyframeComparisonReport]:
        return [
            KeyframeComparisonReport.model_validate_json(record)
            for record in self._list_visual_input_report_json(
                project_id,
                kind="KEYFRAME_COMPARISON",
            )
        ]

    def save_input_compatibility_report(self, report: InputCompatibilityReport) -> None:
        self._save_visual_input_report(
            report_id=report.id,
            project_id=report.project_id,
            shot_id=report.shot_id,
            kind="INPUT_COMPATIBILITY",
            created_at=report.created_at,
            record_json=report.model_dump_json(),
        )

    def list_input_compatibility_reports(
        self,
        project_id: str,
    ) -> list[InputCompatibilityReport]:
        return [
            InputCompatibilityReport.model_validate_json(record)
            for record in self._list_visual_input_report_json(
                project_id,
                kind="INPUT_COMPATIBILITY",
            )
        ]

    def _save_visual_input_report(
        self,
        *,
        report_id: str,
        project_id: str,
        shot_id: str,
        kind: str,
        created_at: str,
        record_json: str,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO visual_input_reports(
                    id, project_id, shot_id, kind, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    record_json = excluded.record_json
                """,
                (report_id, project_id, shot_id, kind, created_at, record_json),
            )

    def _list_visual_input_report_json(
        self,
        project_id: str,
        *,
        kind: str,
    ) -> list[str]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM visual_input_reports
                WHERE project_id = ? AND kind = ?
                ORDER BY created_at ASC, id ASC
                """,
                (project_id, kind),
            ).fetchall()
        return [cast(str, row["record_json"]) for row in rows]

    def save_audio_inspection(self, report: AudioInspectionReport) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audio_inspections(
                    id, project_id, manifest_id, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json
                """,
                (
                    report.id,
                    report.project_id,
                    report.manifest_id,
                    report.created_at,
                    report.model_dump_json(),
                ),
            )

    def get_audio_inspection(self, report_id: str) -> AudioInspectionReport | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM audio_inspections WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return AudioInspectionReport.model_validate_json(cast(str, row["record_json"]))

    def list_audio_inspections(self, project_id: str) -> list[AudioInspectionReport]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM audio_inspections
                WHERE project_id = ? ORDER BY created_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [
            AudioInspectionReport.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_episode_review(self, report: EpisodeReviewReport) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO episode_reviews(
                    id, project_id, manifest_id, status, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    report.id,
                    report.project_id,
                    report.manifest_id,
                    report.status.value,
                    report.updated_at,
                    report.model_dump_json(),
                ),
            )

    def get_episode_review(self, report_id: str) -> EpisodeReviewReport | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM episode_reviews WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return EpisodeReviewReport.model_validate_json(cast(str, row["record_json"]))

    def list_episode_reviews(self, project_id: str) -> list[EpisodeReviewReport]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM episode_reviews
                WHERE project_id = ? ORDER BY updated_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [
            EpisodeReviewReport.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_h3_route_plan(self, plan: H3ProductionRoutePlan) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO h3_route_plans(
                    id, project_id, version, status, created_at, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    plan.id,
                    plan.project_id,
                    plan.version,
                    plan.status.value,
                    plan.created_at,
                    plan.updated_at,
                    plan.model_dump_json(),
                ),
            )

    def get_h3_route_plan(self, plan_id: str) -> H3ProductionRoutePlan | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM h3_route_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return H3ProductionRoutePlan.model_validate_json(cast(str, row["record_json"]))

    def list_h3_route_plans(self, project_id: str) -> list[H3ProductionRoutePlan]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM h3_route_plans
                WHERE project_id = ? ORDER BY version DESC, updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            H3ProductionRoutePlan.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_image_task(self, task: ImageGenerationTask) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO image_tasks(
                    id, project_id, asset_id, provider, operation, status,
                    created_at, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    task.id,
                    task.project_id,
                    task.asset_id,
                    task.provider,
                    task.operation.value,
                    task.status.value,
                    task.created_at,
                    task.updated_at,
                    task.model_dump_json(),
                ),
            )

    def get_image_task(self, task_id: str) -> ImageGenerationTask | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM image_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return ImageGenerationTask.model_validate_json(cast(str, row["record_json"]))

    def list_image_tasks(
        self,
        project_id: str,
        asset_id: str | None = None,
    ) -> list[ImageGenerationTask]:
        query = "SELECT record_json FROM image_tasks WHERE project_id = ?"
        values: list[str] = [project_id]
        if asset_id is not None:
            query += " AND asset_id = ?"
            values.append(asset_id)
        query += " ORDER BY created_at DESC, id DESC"
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            ImageGenerationTask.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def recover_interrupted_image_tasks(self) -> int:
        recovered = 0
        with self._lock:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT record_json FROM image_tasks WHERE status = ?",
                    (ImageTaskStatus.SUBMITTING.value,),
                ).fetchall()
            for row in rows:
                task = ImageGenerationTask.model_validate_json(cast(str, row["record_json"]))
                task.status = ImageTaskStatus.SUBMIT_UNKNOWN
                task.error_code = "PROCESS_INTERRUPTED_DURING_SUBMIT"
                task.error_message = (
                    "图像提交阶段进程中断；OpenAI Image API 无远端任务查询，"
                    "再次提交可能产生重复费用。"
                )
                self.save_image_task(task)
                recovered += 1
        return recovered
