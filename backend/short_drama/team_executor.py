"""Lightweight in-process executor for independent persisted WorkOrders.

This is intentionally a single-desktop-process coordinator, not a distributed
queue.  Existing ``ProductionManager.execute_work_order`` remains responsible
for claiming a WorkOrder under the run lock, performing language work outside
that lock, and committing the result under the run lock.

The executor adds two missing pieces:

* it can advance several already-created WorkOrders concurrently;
* it serializes a pending non-read Tool turn per project because current Tool
  handlers may read and replace the canonical ProjectDocument in one opaque
  call.  Read-only Tool turns and specialist language calls can still overlap.

It never advances a PlanningRun or invents WorkOrders, so the initial screenplay
chain remains the existing sequential Content Planning Job.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from short_drama.agent_runtime.models import (
    AgentResultStatus,
    DelegationStatus,
    RuntimeModel,
    SpecialistTurnStatus,
    WorkOrder,
    WorkOrderNextOwner,
)
from short_drama.agent_runtime.runtime import AgentRuntime
from short_drama.tools import ToolEffect, ToolNotFoundError, ToolRegistry


class TeamExecutionManager(Protocol):
    """Production Manager surface needed by the local executor."""

    @property
    def runtime(self) -> AgentRuntime: ...

    @property
    def tools(self) -> ToolRegistry: ...

    def execute_work_order(self, work_order_id: str) -> object: ...


class TeamWorkAdvanceStatus(StrEnum):
    ADVANCED = "ADVANCED"
    FAILED = "FAILED"


class TeamWorkAdvance(RuntimeModel):
    work_order_id: str
    status: TeamWorkAdvanceStatus
    work_order_status: DelegationStatus | None = None
    result_status: AgentResultStatus | None = None
    serialized_project_write: bool = False
    error_kind: str | None = None
    error_message: str | None = None


class TeamExecutionBatch(RuntimeModel):
    run_id: str
    selected_work_order_ids: list[str] = Field(default_factory=list[str])
    advances: list[TeamWorkAdvance] = Field(default_factory=list[TeamWorkAdvance])


class LocalTeamExecutor:
    """Advance ready WorkOrders concurrently within one trusted local process."""

    def __init__(
        self,
        manager: TeamExecutionManager,
        *,
        max_workers: int | None = 4,
    ) -> None:
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be at least 1 when provided")
        self._manager = manager
        self._max_workers = max_workers
        self._claims_lock = threading.RLock()
        self._claimed_work_order_ids: set[str] = set()
        self._project_locks_guard = threading.RLock()
        self._project_locks: dict[str, threading.Lock] = {}

    def run_ready_work(self, run_id: str) -> TeamExecutionBatch:
        """Advance each currently ready WorkOrder once, without hidden retries."""

        if self._manager.runtime.store.get_run(run_id) is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        selected = self._select_and_reserve(run_id)
        if not selected:
            return TeamExecutionBatch(run_id=run_id)

        advances_by_id: dict[str, TeamWorkAdvance] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures: dict[Future[TeamWorkAdvance], str] = {
                pool.submit(self._advance_reserved, work_order): work_order.id
                for work_order in selected
            }
            for future in as_completed(futures):
                work_order_id = futures[future]
                try:
                    advances_by_id[work_order_id] = future.result()
                except Exception as exc:  # defensive boundary for one worker thread
                    advances_by_id[work_order_id] = TeamWorkAdvance(
                        work_order_id=work_order_id,
                        status=TeamWorkAdvanceStatus.FAILED,
                        error_kind=type(exc).__name__,
                        error_message=str(exc),
                    )

        selected_ids = [work_order.id for work_order in selected]
        return TeamExecutionBatch(
            run_id=run_id,
            selected_work_order_ids=selected_ids,
            advances=[advances_by_id[work_order_id] for work_order_id in selected_ids],
        )

    def _select_and_reserve(self, run_id: str) -> list[WorkOrder]:
        run = self._manager.runtime.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        active_plan_id = run.current_plan_revision_id
        candidates = self._manager.runtime.store.list_work_orders(
            run_id,
            statuses=(
                DelegationStatus.PENDING,
                DelegationStatus.NEEDS_ATTENTION,
            ),
        )
        with self._claims_lock:
            selected = [
                work_order
                for work_order in candidates
                if work_order.id not in self._claimed_work_order_ids
                and work_order.next_owner in {
                    WorkOrderNextOwner.WORKER_DECISION,
                    WorkOrderNextOwner.WORKER_TOOL,
                }
                and (
                    work_order.source_plan_revision_id is None
                    or work_order.active_plan_revision_id == active_plan_id
                )
            ]
            self._claimed_work_order_ids.update(work_order.id for work_order in selected)
        return selected

    def _advance_reserved(self, work_order: WorkOrder) -> TeamWorkAdvance:
        serialized_project_write = self._needs_project_write_lane(work_order)
        try:
            run = self._manager.runtime.store.get_run(work_order.parent_run_id)
            if run is None:
                raise KeyError(f"Unknown agent run: {work_order.parent_run_id}")
            if serialized_project_write:
                with self._project_lock(run.project_id):
                    self._manager.execute_work_order(work_order.id)
            else:
                self._manager.execute_work_order(work_order.id)

            current = self._manager.runtime.store.get_work_order(work_order.id)
            result = self._manager.runtime.store.get_agent_result(work_order.id)
            return TeamWorkAdvance(
                work_order_id=work_order.id,
                status=TeamWorkAdvanceStatus.ADVANCED,
                work_order_status=None if current is None else current.status,
                result_status=None if result is None else result.status,
                serialized_project_write=serialized_project_write,
            )
        except Exception as exc:
            current = self._manager.runtime.store.get_work_order(work_order.id)
            result = self._manager.runtime.store.get_agent_result(work_order.id)
            return TeamWorkAdvance(
                work_order_id=work_order.id,
                status=TeamWorkAdvanceStatus.FAILED,
                work_order_status=None if current is None else current.status,
                result_status=None if result is None else result.status,
                serialized_project_write=serialized_project_write,
                error_kind=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            with self._claims_lock:
                self._claimed_work_order_ids.discard(work_order.id)

    def _needs_project_write_lane(self, work_order: WorkOrder) -> bool:
        pending_tool_name = next(
            (
                turn.tool_name
                for turn in reversed(work_order.specialist_turns)
                if turn.status in {
                    SpecialistTurnStatus.REQUESTED,
                    SpecialistTurnStatus.IN_PROGRESS,
                }
            ),
            None,
        )
        if pending_tool_name is None:
            return False
        try:
            descriptor = self._manager.tools.describe(pending_tool_name)
        except ToolNotFoundError:
            # Unknown calls remain serial until the normal Tool boundary reports
            # the missing registration; this avoids guessing that they are safe.
            return True
        return descriptor.effect != ToolEffect.READ

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._project_locks_guard:
            return self._project_locks.setdefault(project_id, threading.Lock())


__all__ = [
    "LocalTeamExecutor",
    "TeamExecutionBatch",
    "TeamExecutionManager",
    "TeamWorkAdvance",
    "TeamWorkAdvanceStatus",
]
