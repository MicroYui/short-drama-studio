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
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextvars import copy_context
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from short_drama.agent_runtime.models import (
    AgentResultStatus,
    AgentRun,
    AgentRunStatus,
    DelegationStatus,
    RuntimeModel,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrder,
    WorkOrderNextOwner,
    utc_now,
)
from short_drama.agent_runtime.runtime import AgentRuntime
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary
from short_drama.studio_trace import record_trace, trace_context
from short_drama.providers.openai_compatible_language_client import is_language_transport_error
from short_drama.image_models import ImageTaskStatus
from short_drama.models import TaskStatus
from short_drama.tools import (
    ToolCallStatus,
    ToolEffect,
    ToolNotFoundError,
    ToolRegistry,
)


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
    YIELDED = "YIELDED"


class TeamWorkAdvance(RuntimeModel):
    work_order_id: str
    status: TeamWorkAdvanceStatus
    work_order_status: DelegationStatus | None = None
    result_status: AgentResultStatus | None = None
    serialized_project_write: bool = False
    advance_count: int = Field(default=0, ge=0)
    deterministic_continuation_count: int = Field(default=0, ge=0)
    error_kind: str | None = None
    retryable_language_error: bool = False
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
        video_poll_interval_seconds: float = 15.0,
    ) -> None:
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be at least 1 when provided")
        if video_poll_interval_seconds < 0:
            raise ValueError("video_poll_interval_seconds cannot be negative")
        self._manager = manager
        self._max_workers = max_workers
        self._video_poll_interval_seconds = video_poll_interval_seconds
        self._claims_lock = threading.RLock()
        self._claimed_work_order_ids: set[str] = set()
        self._project_locks_guard = threading.RLock()
        self._project_locks: dict[str, threading.Lock] = {}

    def run_ready_work(self, run_id: str) -> TeamExecutionBatch:
        """Advance each currently ready WorkOrder once, without hidden retries."""

        return self._run_ready_work(run_id, drain_worker_ownership=False)

    def drain_ready_work_until_handoff(self, run_id: str) -> TeamExecutionBatch:
        """Continue each ready WorkOrder while ownership remains with its Worker.

        Every model decision and Tool turn is still persisted by
        ``ProductionManager.execute_work_order``.  This only removes redundant
        outer HTTP advances between those persisted turns; it never crosses into
        Leader replanning or user input and never replays a failed call.
        """

        return self._run_ready_work(run_id, drain_worker_ownership=True)

    def _run_ready_work(
        self,
        run_id: str,
        *,
        drain_worker_ownership: bool,
    ) -> TeamExecutionBatch:

        if self._manager.runtime.store.get_run(run_id) is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        selected = self._select_and_reserve(run_id)
        if not selected:
            return TeamExecutionBatch(run_id=run_id)

        advances_by_id: dict[str, TeamWorkAdvance] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures: dict[Future[TeamWorkAdvance], str] = {
                pool.submit(
                    copy_context().run, self._advance_reserved,
                    work_order,
                    drain_worker_ownership=drain_worker_ownership,
                ): work_order.id
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
        execution_boundary()
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
            if self._max_workers is not None:
                selected = selected[:max(0, self._max_workers - len(self._claimed_work_order_ids))]
            self._claimed_work_order_ids.update(work_order.id for work_order in selected)
        return selected

    def _advance_reserved(
        self, work_order: WorkOrder, *, drain_worker_ownership: bool,
    ) -> TeamWorkAdvance:
        with trace_context(run_id=work_order.parent_run_id, work_order_id=work_order.id,
                           agent_role=work_order.recipient_role):
            return self._advance_reserved_in_context(work_order, drain_worker_ownership=drain_worker_ownership)

    def _advance_reserved_in_context(
        self,
        work_order: WorkOrder,
        *,
        drain_worker_ownership: bool,
    ) -> TeamWorkAdvance:
        serialized_project_write = False
        advance_count = 0
        deterministic_continuation_count = 0
        try:
            execution_boundary()
            record_trace("worker.updated", {"status": "IN_PROGRESS", "objective": work_order.objective,
                         "next_owner": None if work_order.next_owner is None else work_order.next_owner.value})
            # A restart may land immediately after a completed lifecycle Tool.
            # Resume the same mechanical transition before another model call.
            if drain_worker_ownership and self._queue_deterministic_continuation(work_order.id):
                deterministic_continuation_count += 1
            while True:
                execution_boundary()
                current = self._manager.runtime.store.get_work_order(work_order.id)
                if current is None:
                    raise KeyError(f"Unknown work order: {work_order.id}")
                run = self._manager.runtime.store.get_run(current.parent_run_id)
                if run is None:
                    raise KeyError(f"Unknown agent run: {current.parent_run_id}")
                if not self._worker_still_owns(current, run):
                    break

                # A long specialist model call should be visible as active work,
                # rather than leaving the Board at the preceding WAITING handoff.
                if run.status == AgentRunStatus.WAITING:
                    self._manager.runtime.set_run_status(run.id, AgentRunStatus.RUNNING)

                needs_project_write = self._needs_project_write_lane(current)
                serialized_project_write = (
                    serialized_project_write or needs_project_write
                )
                if needs_project_write:
                    with self._project_lock(run.project_id):
                        execution_boundary()
                        self._manager.execute_work_order(current.id)
                else:
                    self._manager.execute_work_order(work_order.id)
                advance_count += 1
                if drain_worker_ownership and self._queue_deterministic_continuation(
                    work_order.id
                ):
                    deterministic_continuation_count += 1
                if not drain_worker_ownership:
                    break

            current = self._manager.runtime.store.get_work_order(work_order.id)
            result = self._manager.runtime.store.get_agent_result(work_order.id)
            return TeamWorkAdvance(
                work_order_id=work_order.id,
                status=TeamWorkAdvanceStatus.ADVANCED,
                work_order_status=None if current is None else current.status,
                result_status=None if result is None else result.status,
                serialized_project_write=serialized_project_write,
                advance_count=advance_count,
                deterministic_continuation_count=deterministic_continuation_count,
            )
        except ExecutionYielded:
            record_trace("worker.yielded", {"status": "YIELDED", "objective": work_order.objective})
            return TeamWorkAdvance(work_order_id=work_order.id, status=TeamWorkAdvanceStatus.YIELDED,
                                   advance_count=advance_count)
        except Exception as exc:
            current = self._manager.runtime.store.get_work_order(work_order.id)
            result = self._manager.runtime.store.get_agent_result(work_order.id)
            return TeamWorkAdvance(
                work_order_id=work_order.id,
                status=TeamWorkAdvanceStatus.FAILED,
                work_order_status=None if current is None else current.status,
                result_status=None if result is None else result.status,
                serialized_project_write=serialized_project_write,
                advance_count=advance_count,
                deterministic_continuation_count=deterministic_continuation_count,
                error_kind=type(exc).__name__,
                error_message=str(exc),
                retryable_language_error=is_language_transport_error(exc),
            )
        finally:
            latest = self._manager.runtime.store.get_work_order(work_order.id)
            if latest is not None:
                result = self._manager.runtime.store.get_agent_result(work_order.id)
                record_trace("worker.updated", {"status": latest.status.value,
                    "objective": latest.objective, "next_owner": None if latest.next_owner is None else latest.next_owner.value,
                    "summary": None if result is None else result.concise_summary,
                    "tool_turns": len(latest.specialist_turns)})
            with self._claims_lock:
                self._claimed_work_order_ids.discard(work_order.id)

    def _queue_deterministic_continuation(self, work_order_id: str) -> bool:
        """Persist the next mechanical Tool step without asking the Worker again.

        A Worker still makes every creative and recovery decision.  The executor
        only continues a provider lifecycle when the just-landed typed Tool result
        proves that the same media task has one unambiguous next transition. The
        new request is appended to ``specialist_turns`` before execution, so a
        restart sees the same recoverable trace as a Worker-authored Tool request.

        Image preparation may continue to submission. For H3, only an already
        acknowledged remote task may continue polling; no H3 submission is
        originated here. Failed, uncertain, transient-error and terminal states
        return to the Worker for review, recovery, reconciliation or retry.
        """

        current = self._manager.runtime.store.get_work_order(work_order_id)
        if current is None:
            return False
        run = self._manager.runtime.store.get_run(current.parent_run_id)
        if run is None or not self._worker_still_owns(current, run):
            return False
        if current.next_owner != WorkOrderNextOwner.WORKER_DECISION:
            return False
        if any(
            turn.status
            in {SpecialistTurnStatus.REQUESTED, SpecialistTurnStatus.IN_PROGRESS}
            for turn in current.specialist_turns
        ):
            return False

        completed_turn = next(
            (
                turn
                for turn in reversed(current.specialist_turns)
                if turn.status == SpecialistTurnStatus.COMPLETED
            ),
            None,
        )
        if completed_turn is None or completed_turn.tool_name not in {
            "image.enqueue",
            "image.advance",
            "task.advance",
        }:
            return False
        result = completed_turn.result
        if (
            result is None
            or result.get("tool_id") != completed_turn.tool_name
            or result.get("status") != ToolCallStatus.SUCCEEDED.value
        ):
            return False
        output = result.get("output")
        if not isinstance(output, dict):
            return False
        is_h3_poll = completed_turn.tool_name == "task.advance"
        task = output if is_h3_poll else output.get("task")
        if not isinstance(task, dict):
            return False
        task_id = task.get("id")
        task_status = task.get("status")
        task_project_id = task.get("project_id")
        allowed_statuses = (
            {TaskStatus.ACKNOWLEDGED.value, TaskStatus.RUNNING.value}
            if is_h3_poll else {ImageTaskStatus.QUEUED.value, ImageTaskStatus.SUBMITTING.value}
        )
        if (
            not isinstance(task_id, str)
            or task_status not in allowed_statuses
            or task_project_id != run.project_id
        ):
            return False
        if is_h3_poll and (
            task.get("provider") != "minimax"
            or task.get("model") != "MiniMax-H3"
            or not isinstance(task.get("provider_task_id"), str)
            or not task.get("provider_task_id")
            or task.get("error_code") is not None
        ):
            return False
        if (
            completed_turn.tool_name in {"image.advance", "task.advance"}
            and completed_turn.arguments.get("task_id") != task_id
        ):
            return False

        if is_h3_poll and self._video_poll_interval_seconds:
            # No project write lock is held while waiting for the provider.
            time.sleep(self._video_poll_interval_seconds)
            refreshed = self._manager.runtime.store.get_work_order(work_order_id)
            refreshed_run = self._manager.runtime.store.get_run(run.id)
            if (
                refreshed is None or refreshed_run is None
                or not self._worker_still_owns(refreshed, refreshed_run)
                or refreshed.next_owner != WorkOrderNextOwner.WORKER_DECISION
                or not refreshed.specialist_turns
                or refreshed.specialist_turns[-1].id != completed_turn.id
            ):
                return False
            current = refreshed

        media_label = "h3" if is_h3_poll else "image"
        tool_name = "task.advance" if is_h3_poll else "image.advance"

        next_sequence = max(
            (turn.sequence for turn in current.specialist_turns),
            default=0,
        ) + 1
        current.specialist_turns.append(
            SpecialistTurnRecord(
                id=f"executor-{media_label}-lifecycle-{work_order_id}-{next_sequence}",
                sequence=next_sequence,
                tool_name=tool_name,
                arguments={"task_id": task_id},
                reason=(
                    "视频任务已受理，正在等待并查询结果；返回后继续制作。"
                    if is_h3_poll
                    else "图片任务已保存，继续准备并提交当前素材。"
                ),
            )
        )
        current.status = DelegationStatus.NEEDS_ATTENTION
        current.next_owner = WorkOrderNextOwner.WORKER_TOOL
        current.updated_at = utc_now()
        self._manager.runtime.store.save_work_order(current)
        self._manager.runtime.append_work_order_progress(
            current.id,
            author="local-team-executor",
            summary=(
                f"The executor persisted the next deterministic {media_label} lifecycle "
                f"transition for {task_id} from {task_status}; no creative or "
                "recovery decision was made."
            ),
        )
        return True

    @staticmethod
    def _worker_still_owns(work_order: WorkOrder, run: AgentRun) -> bool:
        if run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            return False
        return (
            work_order.status
            in {DelegationStatus.PENDING, DelegationStatus.NEEDS_ATTENTION}
            and work_order.next_owner
            in {
                WorkOrderNextOwner.WORKER_DECISION,
                WorkOrderNextOwner.WORKER_TOOL,
            }
            and (
                work_order.source_plan_revision_id is None
                or work_order.active_plan_revision_id
                == run.current_plan_revision_id
            )
        )

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
        # Provider I/O is not a project-wide write. External services guard
        # their own short saves and task transitions after the slow operation.
        return descriptor.effect == ToolEffect.WRITE

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
