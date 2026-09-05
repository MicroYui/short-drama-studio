"""SQLite persistence for the agent control plane.

The tables are intentionally independent from the existing production tables,
so the runtime can be introduced incrementally and can share the same SQLite
file without changing current project or provider-task migrations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import cast

from short_drama.agent_runtime.models import (
    ActionRecord,
    AgentResult,
    AgentResultStatus,
    AgentRun,
    AgentRunStatus,
    ArtifactRef,
    DecisionMemory,
    DelegationStatus,
    ExperienceMemory,
    PlanRevision,
    ProjectMemory,
    RunMemory,
    WorkOrder,
    WorkOrderProgressEntry,
    utc_now,
)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


class AgentRuntimeStore:
    """Small repository for plans, checkpoints, memories, and delegations."""

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
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_project_updated
                    ON agent_runs(project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated
                    ON agent_runs(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_plan_revisions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE(run_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_plan_revisions_run
                    ON agent_plan_revisions(run_id, revision DESC);

                CREATE TABLE IF NOT EXISTS agent_actions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    plan_revision_id TEXT,
                    plan_step_id TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_actions_run_updated
                    ON agent_actions(run_id, updated_at ASC, id ASC);
                CREATE INDEX IF NOT EXISTS idx_agent_actions_plan_step
                    ON agent_actions(plan_revision_id, plan_step_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_project_memory (
                    project_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_run_memory (
                    run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_decisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_decisions_project_created
                    ON agent_decisions(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_decisions_run_created
                    ON agent_decisions(run_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_experiences (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    skill_id TEXT,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_experiences_project_created
                    ON agent_experiences(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_experiences_skill_created
                    ON agent_experiences(skill_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_work_orders (
                    id TEXT PRIMARY KEY,
                    parent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_work_orders_run_status
                    ON agent_work_orders(parent_run_id, status, updated_at ASC);

                CREATE TABLE IF NOT EXISTS agent_results (
                    id TEXT PRIMARY KEY,
                    work_order_id TEXT NOT NULL UNIQUE
                        REFERENCES agent_work_orders(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_work_order_progress (
                    id TEXT PRIMARY KEY,
                    work_order_id TEXT NOT NULL
                        REFERENCES agent_work_orders(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE(work_order_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_work_order_progress_order
                    ON agent_work_order_progress(work_order_id, sequence ASC);
                """
            )

    def create_run(self, run: AgentRun, memory: RunMemory) -> None:
        if memory.run_id != run.id:
            raise ValueError("Run memory must belong to the created run")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs(id, project_id, status, updated_at, record_json)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.project_id,
                    run.status.value,
                    _timestamp(run.updated_at),
                    run.model_dump_json(),
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_run_memory(run_id, updated_at, record_json)
                VALUES(?, ?, ?)
                """,
                (memory.run_id, _timestamp(memory.updated_at), memory.model_dump_json()),
            )

    def save_run(self, run: AgentRun) -> None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_runs
                SET project_id = ?, status = ?, updated_at = ?, record_json = ?
                WHERE id = ?
                """,
                (
                    run.project_id,
                    run.status.value,
                    _timestamp(run.updated_at),
                    run.model_dump_json(),
                    run.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown agent run: {run.id}")

    def get_run(self, run_id: str) -> AgentRun | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return AgentRun.model_validate_json(cast(str, row["record_json"]))

    def list_resumable_runs(self, project_id: str | None = None) -> list[AgentRun]:
        statuses = (AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING.value)
        with self._lock, self._connection() as connection:
            if project_id is None:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_runs
                    WHERE status IN (?, ?)
                    ORDER BY updated_at ASC, id ASC
                    """,
                    statuses,
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_runs
                    WHERE project_id = ? AND status IN (?, ?)
                    ORDER BY updated_at ASC, id ASC
                    """,
                    (project_id, *statuses),
                ).fetchall()
        return [AgentRun.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def list_runs(self, project_id: str | None = None) -> list[AgentRun]:
        """List all runs for inspection, including completed history."""

        with self._lock, self._connection() as connection:
            if project_id is None:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_runs
                    ORDER BY updated_at DESC, id DESC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_runs
                    WHERE project_id = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (project_id,),
                ).fetchall()
        return [AgentRun.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def append_plan_revision(self, revision: PlanRevision, updated_run: AgentRun) -> None:
        """Persist a new plan and update the run's current-plan pointer atomically."""

        if revision.run_id != updated_run.id:
            raise ValueError("Plan revision and run ids do not match")
        if updated_run.current_plan_revision_id != revision.id:
            raise ValueError("Run must point to the plan revision being appended")

        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT MAX(revision) AS latest FROM agent_plan_revisions WHERE run_id = ?",
                (revision.run_id,),
            ).fetchone()
            latest_value = None if row is None else row["latest"]
            latest = 0 if latest_value is None else cast(int, latest_value)
            if revision.revision != latest + 1:
                raise ValueError(
                    f"Expected plan revision {latest + 1}, got {revision.revision}"
                )

            connection.execute(
                """
                INSERT INTO agent_plan_revisions(
                    id, run_id, revision, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    revision.id,
                    revision.run_id,
                    revision.revision,
                    _timestamp(revision.created_at),
                    revision.model_dump_json(),
                ),
            )
            cursor = connection.execute(
                """
                UPDATE agent_runs
                SET status = ?, updated_at = ?, record_json = ?
                WHERE id = ?
                """,
                (
                    updated_run.status.value,
                    _timestamp(updated_run.updated_at),
                    updated_run.model_dump_json(),
                    updated_run.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown agent run: {updated_run.id}")

    def list_plan_revisions(self, run_id: str) -> list[PlanRevision]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM agent_plan_revisions
                WHERE run_id = ? ORDER BY revision ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            PlanRevision.model_validate_json(cast(str, row["record_json"])) for row in rows
        ]

    def get_current_plan(self, run: AgentRun) -> PlanRevision | None:
        if run.current_plan_revision_id is None:
            return None
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_plan_revisions WHERE id = ?",
                (run.current_plan_revision_id,),
            ).fetchone()
        if row is None:
            return None
        return PlanRevision.model_validate_json(cast(str, row["record_json"]))

    def save_action(self, action: ActionRecord) -> None:
        if self.get_run(action.run_id) is None:
            raise KeyError(f"Unknown agent run: {action.run_id}")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_actions(
                    id, run_id, plan_revision_id, plan_step_id,
                    status, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    action.id,
                    action.run_id,
                    action.plan_revision_id,
                    action.plan_step_id,
                    action.status.value,
                    _timestamp(action.updated_at),
                    action.model_dump_json(),
                ),
            )

    def get_action(self, action_id: str) -> ActionRecord | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_actions WHERE id = ?", (action_id,)
            ).fetchone()
        if row is None:
            return None
        return ActionRecord.model_validate_json(cast(str, row["record_json"]))

    def list_actions(self, run_id: str) -> list[ActionRecord]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM agent_actions
                WHERE run_id = ? ORDER BY updated_at ASC, id ASC
                """,
                (run_id,),
            ).fetchall()
        return [ActionRecord.model_validate_json(cast(str, row["record_json"])) for row in rows]

    def save_project_memory(self, memory: ProjectMemory) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_project_memory(project_id, updated_at, record_json)
                VALUES(?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    memory.project_id,
                    _timestamp(memory.updated_at),
                    memory.model_dump_json(),
                ),
            )

    def get_project_memory(self, project_id: str) -> ProjectMemory | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_project_memory WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ProjectMemory.model_validate_json(cast(str, row["record_json"]))

    def save_run_memory(self, memory: RunMemory) -> None:
        memory.updated_at = utc_now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_run_memory SET updated_at = ?, record_json = ?
                WHERE run_id = ?
                """,
                (
                    _timestamp(memory.updated_at),
                    memory.model_dump_json(),
                    memory.run_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown agent run memory: {memory.run_id}")

    def get_run_memory(self, run_id: str) -> RunMemory | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_run_memory WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunMemory.model_validate_json(cast(str, row["record_json"]))

    def save_decision(self, decision: DecisionMemory) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_decisions(id, project_id, run_id, created_at, record_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json
                """,
                (
                    decision.id,
                    decision.project_id,
                    decision.run_id,
                    _timestamp(decision.created_at),
                    decision.model_dump_json(),
                ),
            )

    def get_decision(self, decision_id: str) -> DecisionMemory | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return DecisionMemory.model_validate_json(cast(str, row["record_json"]))

    def list_decisions(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        limit: int = 20,
    ) -> list[DecisionMemory]:
        with self._lock, self._connection() as connection:
            if run_id is None:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_decisions
                    WHERE project_id = ? AND run_id IS NULL
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (project_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record_json FROM agent_decisions
                    WHERE project_id = ?
                    ORDER BY
                        CASE
                            WHEN run_id = ? THEN 0
                            WHEN run_id IS NULL THEN 1
                            ELSE 2
                        END,
                        created_at DESC,
                        id DESC
                    LIMIT ?
                    """,
                    (project_id, run_id, limit),
                ).fetchall()
        return [
            DecisionMemory.model_validate_json(cast(str, row["record_json"])) for row in rows
        ]

    def save_experience(self, experience: ExperienceMemory) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_experiences(
                    id, project_id, skill_id, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    skill_id = excluded.skill_id,
                    record_json = excluded.record_json
                """,
                (
                    experience.id,
                    experience.project_id,
                    experience.skill_id,
                    _timestamp(experience.created_at),
                    experience.model_dump_json(),
                ),
            )

    def list_experiences(
        self,
        project_id: str,
        *,
        skill_ids: Sequence[str] = (),
        tags: Sequence[str] = (),
        limit: int = 12,
    ) -> list[ExperienceMemory]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM agent_experiences
                WHERE project_id IS NULL OR project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        candidates = [
            ExperienceMemory.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]
        skill_set = set(skill_ids)
        tag_set = set(tags)
        relevant = [
            item
            for item in candidates
            if (not skill_set or item.skill_id is None or item.skill_id in skill_set)
            and (not tag_set or bool(tag_set.intersection(item.tags)))
        ]
        return relevant[:limit]

    def list_experiences_for_scope(
        self,
        *,
        project_id: str | None,
        skill_id: str | None,
    ) -> list[ExperienceMemory]:
        """Return experiences in one exact scope for deterministic deduplication."""

        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM agent_experiences
                WHERE project_id IS ? AND skill_id IS ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id, skill_id),
            ).fetchall()
        return [
            ExperienceMemory.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]

    def save_work_order(self, work_order: WorkOrder) -> None:
        if self.get_run(work_order.parent_run_id) is None:
            raise KeyError(f"Unknown agent run: {work_order.parent_run_id}")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_work_orders(
                    id, parent_run_id, status, updated_at, record_json
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    work_order.id,
                    work_order.parent_run_id,
                    work_order.status.value,
                    _timestamp(work_order.updated_at),
                    work_order.model_dump_json(),
                ),
            )

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_work_orders WHERE id = ?",
                (work_order_id,),
            ).fetchone()
        if row is None:
            return None
        return self._load_work_order(cast(str, row["record_json"]))

    def list_work_orders(
        self,
        run_id: str,
        *,
        statuses: Sequence[DelegationStatus] = (),
    ) -> list[WorkOrder]:
        values: list[object] = [run_id]
        status_clause = ""
        if statuses:
            placeholders = ", ".join("?" for _status in statuses)
            status_clause = f" AND status IN ({placeholders})"
            values.extend(status.value for status in statuses)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT record_json FROM agent_work_orders
                WHERE parent_run_id = ? {status_clause}
                ORDER BY updated_at ASC, id ASC
                """,
                values,
            ).fetchall()
        return [self._load_work_order(cast(str, row["record_json"])) for row in rows]

    def _load_work_order(self, serialized: str) -> WorkOrder:
        payload = json.loads(serialized)
        legacy_next_owner_missing = (
            isinstance(payload, dict) and "next_owner" not in payload
        )
        return self._restore_result_next_owner(
            WorkOrder.model_validate(payload),
            legacy_next_owner_missing=legacy_next_owner_missing,
        )

    def _restore_result_next_owner(
        self,
        work_order: WorkOrder,
        *,
        legacy_next_owner_missing: bool,
    ) -> WorkOrder:
        """Prefer the persisted partial result when migrating legacy WorkOrders."""

        if (
            not legacy_next_owner_missing
            or work_order.status != DelegationStatus.NEEDS_ATTENTION
        ):
            return work_order
        result = self.get_agent_result(work_order.id)
        if result is not None and result.status == AgentResultStatus.PARTIAL:
            work_order.next_owner = result.next_owner
        return work_order

    def save_agent_result(self, result: AgentResult) -> WorkOrder:
        """Save a specialist result and update its work order in one transaction."""

        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_work_orders WHERE id = ?",
                (result.work_order_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown work order: {result.work_order_id}")
            work_order = WorkOrder.model_validate_json(cast(str, row["record_json"]))
            if not result.stale_handoff:
                if result.status == AgentResultStatus.COMPLETED:
                    work_order.status = DelegationStatus.COMPLETED
                elif result.status == AgentResultStatus.PARTIAL:
                    work_order.status = DelegationStatus.NEEDS_ATTENTION
                    work_order.next_owner = result.next_owner
                else:
                    work_order.status = DelegationStatus.FAILED
                if result.status != AgentResultStatus.PARTIAL:
                    work_order.next_owner = None
                work_order.updated_at = utc_now()

            connection.execute(
                """
                INSERT INTO agent_results(id, work_order_id, created_at, record_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(work_order_id) DO UPDATE SET
                    id = excluded.id,
                    created_at = excluded.created_at,
                    record_json = excluded.record_json
                """,
                (
                    result.id,
                    result.work_order_id,
                    _timestamp(result.created_at),
                    result.model_dump_json(),
                ),
            )
            connection.execute(
                """
                UPDATE agent_work_orders
                SET status = ?, updated_at = ?, record_json = ?
                WHERE id = ?
                """,
                (
                    work_order.status.value,
                    _timestamp(work_order.updated_at),
                    work_order.model_dump_json(),
                    work_order.id,
                ),
            )
        return work_order

    def get_agent_result(self, work_order_id: str) -> AgentResult | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM agent_results WHERE work_order_id = ?",
                (work_order_id,),
            ).fetchone()
        if row is None:
            return None
        return AgentResult.model_validate_json(cast(str, row["record_json"]))

    def append_work_order_progress(
        self,
        *,
        entry_id: str,
        work_order_id: str,
        author: str,
        summary: str,
        artifact_refs: Sequence[ArtifactRef],
        created_at: datetime,
    ) -> WorkOrderProgressEntry:
        """Append one progress event and assign its per-WorkOrder sequence."""

        with self._lock, self._connection() as connection:
            work_order = connection.execute(
                "SELECT 1 FROM agent_work_orders WHERE id = ?",
                (work_order_id,),
            ).fetchone()
            if work_order is None:
                raise KeyError(f"Unknown work order: {work_order_id}")
            row = connection.execute(
                """
                SELECT MAX(sequence) AS latest
                FROM agent_work_order_progress
                WHERE work_order_id = ?
                """,
                (work_order_id,),
            ).fetchone()
            latest_value = None if row is None else row["latest"]
            latest = 0 if latest_value is None else cast(int, latest_value)
            entry = WorkOrderProgressEntry(
                id=entry_id,
                work_order_id=work_order_id,
                sequence=latest + 1,
                author=author,
                summary=summary,
                artifact_refs=list(artifact_refs),
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO agent_work_order_progress(
                    id, work_order_id, sequence, created_at, record_json
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.work_order_id,
                    entry.sequence,
                    _timestamp(entry.created_at),
                    entry.model_dump_json(),
                ),
            )
        return entry

    def list_work_order_progress(
        self,
        work_order_id: str,
    ) -> list[WorkOrderProgressEntry]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM agent_work_order_progress
                WHERE work_order_id = ? ORDER BY sequence ASC
                """,
                (work_order_id,),
            ).fetchall()
        return [
            WorkOrderProgressEntry.model_validate_json(cast(str, row["record_json"]))
            for row in rows
        ]
