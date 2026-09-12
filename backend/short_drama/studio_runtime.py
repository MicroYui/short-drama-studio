"""Reconnectable product sessions over the persisted AgentRun and Team engine."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from contextvars import copy_context
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.agent_runtime.plan_graph import PlanGraph
from short_drama.agent_runtime.models import ActionKind, ActionStatus, AgentRunStatus, DelegationStatus, WorkOrderNextOwner
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import (
    AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest, AgentUserInputRequest,
    AgentPlanProposal, CompletionPlanningInput,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderTransportError, is_language_transport_error
from short_drama.studio_models import StudioActivity, StudioJob, StudioJobControl, StudioMessage, StudioChangeSet, StudioScope
from short_drama.studio_trace import current_trace_context, production_trace, record_trace, trace_context
from short_drama.studio_review_scope import ReviewScope, pending_review_scope

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StudioAgentRunner:
    """Schedule persisted operations; Agents own scope, plans and completion."""

    def __init__(self, service: ShortDramaService, *, retry_delays: tuple[float, ...] = (3.0, 8.0, 20.0)) -> None:
        self.service = service
        self.lock = threading.RLock()
        self.threads: dict[str, threading.Thread] = {}
        self._wakeups: dict[str, threading.Event] = {}
        self._processing_messages: dict[str, int] = {}
        self._completion_planning: dict[str, tuple[CompletionPlanningInput, Future[AgentPlanProposal]]] = {}
        self.retry_delays = retry_delays

    def activity(self, project_id: str, after: int = 0) -> StudioActivity:
        from short_drama.studio_activity import current_activity
        return current_activity(self.service, project_id, after)

    def send(self, project_id: str, message: StudioMessage) -> StudioJob:
        self.service.studio.current_project(project_id)
        with self.lock:
            jobs = self.service.repository.list_studio_jobs(project_id)
            # Continue the latest unfinished goal, including a failed connection.
            # Never revive an older abandoned task behind a completed newer goal.
            job = jobs[0] if jobs and jobs[0].status != "completed" else None
            if job is None:
                job = StudioJob(id=f"studio-job-{uuid4().hex[:12]}", project_id=project_id,
                                objective=message.text, scope=message.scope, created_at=_now(), updated_at=_now())
            else:
                job.pending_messages.append(message)
                if job.status in {"paused", "waiting_user", "failed", "retrying"}:
                    job.status = "queued"
                    job.error = None
                    job.retry_at = None
                    job.retry_attempt = 0
            job.updated_at = _now()
            self.service.repository.save_studio_job(job)
            self.service.repository.append_studio_event(project_id, "chat.user", {
                "job_id": job.id, **cast(dict[str, JsonValue], message.model_dump(mode="json"))})
            self._publish_job(job)
            self._wakeups.setdefault(job.id, threading.Event()).set()
            self._launch(job.id)
            return job

    def control(self, job_id: str, request: StudioJobControl) -> StudioJob:
        with self.lock:
            job = self._job(job_id)
            if request.action == "pause" and job.status in {"queued", "running", "retrying"}:
                job.status = "pausing"
            elif request.action == "resume" and job.status in {"paused", "waiting_user", "failed"}:
                job.status = "queued"
                job.error = None
                job.retry_attempt = 0
                job.retry_at = None
            job.updated_at = _now()
            self.service.repository.save_studio_job(job)
            self._publish_job(job)
            self._wakeups.setdefault(job.id, threading.Event()).set()
            if job.status in {"queued", "running", "retrying", "pausing"}:
                self._launch(job.id)
            return job

    def resume_after_restart(self) -> None:
        for job in self.service.repository.list_studio_jobs():
            if job.status in {"queued", "running", "retrying", "pausing"}:
                self._launch(job.id)

    def review_resolved(self, project_id: str, change: StudioChangeSet) -> None:
        """Deliver explicit review clicks back to the same goal, not as new chat."""
        if any(item.status == "pending" for item in change.items):
            return
        with self.lock:
            jobs = self.service.repository.list_studio_jobs(project_id)
            job = next((item for item in jobs if item.run_id == change.run_id), None)
            if job is None or job.status == "completed":
                return
            decisions = "；".join(f"{item.title}：{'已接受并保存' if item.status == 'accepted' else '已拒绝，保留原文'}" for item in change.items)
            job.pending_messages.append(StudioMessage(text=f"用户已逐项审阅修改提案 {change.id}。{decisions}。读取当前已生效内容，结合原目标继续；不要重做被拒绝的修改。", scope=job.scope))
            job.status = "queued"
            job.error = None
            job.updated_at = _now()
            self.service.repository.save_studio_job(job)
            self._publish_job(job)
            self._wakeups.setdefault(job.id, threading.Event()).set()
            self._launch(job.id)

    def _job(self, job_id: str) -> StudioJob:
        job = next((item for item in self.service.repository.list_studio_jobs() if item.id == job_id), None)
        if job is None:
            raise KeyError(job_id)
        return job

    def _launch(self, job_id: str) -> None:
        with self.lock:
            current = self.threads.get(job_id)
            if current is not None and current.is_alive():
                return
            self._wakeups.setdefault(job_id, threading.Event())
            thread = threading.Thread(target=self._run, args=(job_id,), name=job_id, daemon=True)
            self.threads[job_id] = thread
            thread.start()

    def _publish_job(self, job: StudioJob) -> None:
        self.service.repository.append_studio_event(job.project_id, "job.updated", {
            "job_id": job.id, "run_id": job.run_id, "status": job.status, "error": job.error,
            "retry_attempt": job.retry_attempt, "retry_at": job.retry_at,
        })

    def _save_state(self, job_id: str, status: str, *, run_id: str | None = None, error: str | None = None) -> StudioJob:
        with self.lock:
            job = self._job(job_id)
            if job.status != "pausing" or status == "paused":
                job = StudioJob.model_validate({**job.model_dump(), "status": status})
            if run_id:
                job.run_id = run_id
            job.error = error
            job.updated_at = _now()
            self.service.repository.save_studio_job(job)
            self._publish_job(job)
            return job

    def _yield_reason(self, job_id: str) -> str | None:
        with self.lock:
            job = self._job(job_id)
            if job.status == "pausing":
                return "User requested pause"
            if job.status in {"paused", "failed", "completed", "waiting_user"}:
                return f"Studio job is {job.status}"
            if len(job.pending_messages) > self._processing_messages.get(job_id, 0):
                return "User input is pending"
            work_id = current_trace_context().get("work_order_id")
            if job.run_id and isinstance(work_id, str) and work_id in self._review_scope(job).work_order_ids:
                return "User-requested text review is pending for this work"
        return None

    def _review_scope(self, job: StudioJob) -> ReviewScope:
        if job.run_id is None:
            return ReviewScope()
        document = self.service.repository.get_project(job.project_id)
        if document is None or not document.studio_changes:
            return ReviewScope()
        snapshot = self.service.agent_runtime.snapshot(job.run_id)
        return pending_review_scope(document, job.run_id,
            self.service.agent_runtime.store.list_work_orders(job.run_id),
            snapshot.current_plan.steps if snapshot.current_plan else [], snapshot.actions)

    def _wait_for_retry(self, job_id: str) -> None:
        while True:
            with self.lock:
                job = self._job(job_id)
                if job.status != "retrying" or job.retry_at is None:
                    return
                remaining = (datetime.fromisoformat(job.retry_at) - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    job.retry_at = None
                    self.service.repository.save_studio_job(job)
                    return
                wakeup = self._wakeups[job_id]
                wakeup.clear()
            wakeup.wait(min(remaining, 1.0))

    def _run(self, job_id: str) -> None:
        job = self._job(job_id)
        directory = self.service.workspace_root / job.project_id / "studio-trace" / job.id
        stopped = threading.Event()

        def emit(kind: str, data: dict[str, JsonValue]) -> None:
            self.service.repository.append_studio_event(job.project_id, kind, {"job_id": job_id, **data})
            # A completed language decision breaks a sequence of connection failures.
            if kind == "provider.language_generation" and data.get("success") is True:
                with self.lock:
                    current = self._job(job_id)
                    if current.retry_attempt:
                        current.retry_attempt = 0
                        current.retry_at = None
                        self.service.repository.save_studio_job(current)

        try:
            with production_trace(directory, emit), execution_control(
                lambda: "This execution has stopped" if stopped.is_set() else self._yield_reason(job_id)
            ):
                # Keep the original request and remove only an untouched legacy
                # blank-workspace scaffold before asking the Agent to continue.
                if self.service.studio.remember_initial_request(job.project_id, job.objective):
                    record_trace("content.updated", {"reason": "Initial workspace request and placeholder metadata reconciled."})
                while True:
                    self._wait_for_retry(job_id)
                    try:
                        self._execute(job_id)
                        return
                    except ExecutionYielded:
                        continue
                    except Exception as exc:
                        with self.lock:
                            current = self._job(job_id)
                            stopping = current.status in {"pausing", "paused", "failed", "completed", "waiting_user"}
                            if not stopping and is_language_transport_error(exc) and current.retry_attempt < len(self.retry_delays):
                                delay = self.retry_delays[current.retry_attempt]
                                current.retry_attempt += 1
                                current.retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
                                current.status = "retrying"
                                current.error = str(exc)
                                current.updated_at = _now()
                                self.service.repository.save_studio_job(current)
                                self._publish_job(current)
                                record_trace("agent.recovering", {"run_id": current.run_id,
                                    "attempt": current.retry_attempt, "delay_seconds": delay,
                                    "reason": str(exc), "summary": "连接暂时中断，保留已完成内容并继续当前任务。"})
                                continue
                        if stopping:
                            stopped.set()
                            self._drain_started_work(job_id)
                            if self._job(job_id).status == "pausing":
                                self._save_state(job_id, "paused", error=str(exc))
                            return
                        stopped.set()
                        self._fail_job(job_id, str(exc))
                        record_trace("chat.assistant", {"text": f"这一步未完成：{exc}。已保存的内容和任务仍在，可以修改要求后继续。", "failed": True})
                        return
        finally:
            with self.lock:
                self.threads.pop(job_id, None)
                self._processing_messages.pop(job_id, None)
                current = self._job(job_id)
                if current.status == "queued" or (current.pending_messages and current.status in {"running", "retrying"}):
                    self._launch(job_id)

    def _drain_started_work(self, job_id: str) -> None:
        """Collect already-started work without dispatching or applying a new plan."""
        job = self._job(job_id)
        while job.run_id is not None:
            batch = self.service.team_executor.collect_finished_work(job.run_id)
            for failure in batch.advances:
                if failure.error_message:
                    record_trace("worker.error", {"work_order_id": failure.work_order_id,
                        "error": failure.error_message, "error_kind": failure.error_kind})
            planning_active = self._collect_completion_plan(job_id, apply=False)
            if not batch.active_work_order_ids and not planning_active:
                return
            self._wakeups[job_id].wait(0.05)
            self._wakeups[job_id].clear()

    def _fail_job(self, job_id: str, error: str) -> None:
        # Close the shared boundary first. Started operations may still save
        # receipts; their result handoff must preserve this terminal Run state.
        job = self._save_state(job_id, "failed", error=error)
        if job.status == "failed" and job.run_id is not None:
            with self.service.agent_runtime.run_lock(job.run_id):
                self.service.agent_runtime.set_run_status(job.run_id, AgentRunStatus.FAILED)
        self._drain_started_work(job_id)
        if self._job(job_id).status == "pausing":
            self._save_state(job_id, "paused", error=error)

    def _apply_messages(self, job_id: str, run_id: str) -> None:
        # Input may arrive after the loop's yield check. Do not exempt it from
        # the shared callback while older Workers/Leader futures still exist.
        # Return to the normal drain before claiming the message for processing.
        active = self.service.team_executor.has_active_work(run_id) or job_id in self._completion_planning
        with self.lock:
            messages = list(self._job(job_id).pending_messages)
            if not messages:
                return
            if active:
                raise ExecutionYielded("Pending user input must wait for started work to be collected")
            self._processing_messages[job_id] = len(messages)
        try:
            for message in messages:
                self._remember_scope_tools(run_id, message.scope)
            view = self.service.get_agent_run(run_id)
            waiting = next((action for action in view.snapshot.actions
                if action.kind == ActionKind.USER_INPUT and action.status == ActionStatus.RUNNING), None)
            text = "\n\n".join(message.text + "\n作用范围：" + message.scope.model_dump_json() for message in messages)
            if waiting:
                self.service.submit_agent_user_input(run_id, AgentUserInputRequest(action_id=waiting.id, answer=text))
            else:
                self.service.replan_agent_run(run_id, AgentRunReplanRequest(
                    reason="用户在进行中发送了新消息。结合原目标判断补充、定向修改、提问或目标变更；最新用户要求优先。完整内容见 goal.latest_user_instructions。",
                    confirmed_decisions=[text],
                ))
            # Acknowledge only after the persisted plan/input handoff succeeds.
            # Concurrent sends append to the tail and remain pending.
            with self.lock:
                job = self._job(job_id)
                if job.pending_messages[:len(messages)] == messages:
                    job.pending_messages = job.pending_messages[len(messages):]
                    self.service.repository.save_studio_job(job)
                    record_trace("agent.input_applied", {"run_id": run_id, "message_count": len(messages)})
                    self._publish_job(job)
        finally:
            with self.lock:
                self._processing_messages.pop(job_id, None)

    def _execute(self, job_id: str) -> None:
        job = self._job(job_id)
        if job.status in {"paused", "failed", "completed", "waiting_user"}:
            self._drain_started_work(job_id)
            return
        if job.status == "pausing" and (
            job.run_id is None or not self.service.team_executor.has_active_work(job.run_id)
        ) and job_id not in self._completion_planning:
            self._save_state(job_id, "paused")
            return
        if job.run_id is None:
            recent = [item for item in self.service.repository.list_studio_jobs(job.project_id) if item.id != job.id][:2]
            background = "\n".join(f"此前要求（{item.status}）：{item.objective[:900]}" for item in recent)
            objective = job.objective + "\n\n用户选定范围：" + job.scope.model_dump_json()
            if background:
                objective += "\n\n近期对话背景，仅供理解指代；本次要求优先，当前内容从项目读取：\n" + background
            view = self.service.create_agent_run(job.project_id, AgentRunCreateRequest(
                objective=objective, auto_plan=False, unattended=False,
                execution_target=ProductionExecutionTarget.REAL_H3, render_export=True,
            ))
            job = self._save_state(job_id, "running", run_id=view.snapshot.run.id)
        if job.run_id is None or self.service.agent_runtime.store.get_run(job.run_id) is None:
            raise ValueError("原制作任务未找到")
        run_id = job.run_id
        self._remember_scope_tools(run_id, job.scope)
        self._save_state(job_id, "running")
        with trace_context(run_id=run_id, agent_role="leader", work_order_id=None):
            self._advance_until_handoff(job_id, run_id)

    def _remember_scope_tools(self, run_id: str, scope: StudioScope) -> None:
        # This is one starting read contract, not an execution route. The Agent
        # still decides whether to inspect, edit, answer, or finish this request.
        if scope.editor_selection is not None or scope.kind == "film":
            self.service.agent_runtime.store.remember_tool_schema_ids(run_id, ["timeline.read_editorial"])

    def _collect_completion_plan(self, job_id: str, *, apply: bool = True) -> bool:
        """Return whether a slow Leader supplement is still running."""
        pending = self._completion_planning.get(job_id)
        if pending is None:
            return False
        prepared, future = pending
        if not future.done():
            return True
        self._completion_planning.pop(job_id, None)
        try:
            proposal = future.result()
            if apply:
                self.service.production_manager.apply_completion_plan(prepared, proposal)
        except ExecutionYielded:
            if apply:
                raise
        except Exception as exc:
            # An optional early plan must not stop already-running production or
            # repeatedly request the same supplement. Normal handoffs still replan.
            record_trace("agent.completion_planning", {"run_id": prepared.run_id,
                "status": "FAILED", "error": str(exc),
                "completed_work_order_ids": cast(list[JsonValue], prepared.completed_work_order_ids),
                "trigger": "FAILED_PLAN" if prepared.failed_plan_state else "LOCAL_PLAN_COMPLETED" if prepared.local_plan_completed else "WORKER_COMPLETED"})
            if apply and (prepared.local_plan_completed or prepared.failed_plan_state):
                # Required continuation uses the existing bounded retry/error
                # path, not a new request on every scheduler tick.
                raise
        return False

    def _start_completion_plan(self, job_id: str, run_id: str) -> bool:
        if job_id in self._completion_planning:
            return True
        prepared = self.service.production_manager.prepare_completion_planning(
            run_id, include_local_plan_completion=True, include_failed_plan=True,
        )
        if prepared is None:
            return False
        future: Future[AgentPlanProposal] = Future()
        self._completion_planning[job_id] = (prepared, future)

        def decide() -> None:
            future.set_running_or_notify_cancel()
            try:
                future.set_result(self.service.production_manager.plan_after_completions(prepared))
            except ExecutionYielded as exc:
                # Cooperative control derives from BaseException. Complete the
                # Future so pause/input can drain it instead of waiting forever.
                future.set_exception(exc)
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self._wakeups[job_id].set()

        threading.Thread(target=copy_context().run, args=(decide,),
                         name=f"leader-supplement-{run_id}", daemon=True).start()
        return True

    def _advance_until_handoff(self, job_id: str, run_id: str) -> None:
        published_actions: dict[str, str] = {}
        while True:
            # Started operations finish and persist before pause/input is
            # acknowledged. Their next boundary sees the same control callback.
            if self._yield_reason(job_id) is not None and (
                self.service.team_executor.has_active_work(run_id) or job_id in self._completion_planning
            ):
                batch = self.service.team_executor.collect_finished_work(run_id)
                for failure in batch.advances:
                    if failure.error_message:
                        record_trace("worker.error", {"work_order_id": failure.work_order_id,
                            "error": failure.error_message, "error_kind": failure.error_kind})
                planning_active = self._collect_completion_plan(job_id, apply=False)
                if batch.active_work_order_ids or planning_active:
                    self._wakeups[job_id].wait(0.05)
                    self._wakeups[job_id].clear()
                    continue
            if self._job(job_id).status == "pausing":
                self._save_state(job_id, "paused")
                return
            if self._job(job_id).status in {"paused", "failed", "completed", "waiting_user"}:
                return
            self._apply_messages(job_id, run_id)
            planning_active = self._collect_completion_plan(job_id)
            review = self._review_scope(self._job(job_id))
            advance_request = AgentRunAdvanceRequest(max_actions=1,
                review_blocked_step_ids=sorted(review.step_ids) if review.pending_count else None)
            view = self.service.get_agent_run(run_id)
            if view.snapshot.current_plan is None or view.snapshot.run.status in {AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
                self.service.replan_agent_run(run_id, AgentRunReplanRequest(reason="根据本次用户目标继续工作，并按目标决定交付边界。"))
                view = self.service.get_agent_run(run_id)
            for action in view.snapshot.actions:
                state = action.status.value
                if published_actions.get(action.id) != state:
                    record_trace("agent.action", cast(dict[str, JsonValue], action.model_dump(mode="json")))
                    published_actions[action.id] = state
            if view.snapshot.run.status == AgentRunStatus.COMPLETED:
                if self.service.team_executor.has_active_work(run_id):
                    self.service.team_executor.collect_finished_work(run_id)
                    self._wakeups[job_id].wait(0.05)
                    continue
                if review.pending_count:
                    self._save_state(job_id, "waiting_user", error=f"{review.pending_count} 处修改等待审阅，其他可执行工作已完成。")
                    return
                with self.lock:
                    if self._job(job_id).pending_messages:
                        continue
                    if self._job(job_id).status == "pausing":
                        self._save_state(job_id, "paused")
                        return
                    self._save_state(job_id, "completed")
                text = view.snapshot.current_plan.summary if view.snapshot.current_plan else "已完成本次要求。"
                record_trace("chat.assistant", {"text": text, "run_id": run_id})
                return
            waiting = next((action for action in view.snapshot.actions
                if action.kind == ActionKind.USER_INPUT and action.status == ActionStatus.RUNNING), None)
            # Execute a bounded slice of already-authored steps, then give
            # retained Workers a dispatch/collection turn. Replanning inside
            # the last Tool could return another ready read indefinitely and
            # prevent the Worker scheduler below from ever being reached.
            failed_plan = view.snapshot.current_plan is not None and any(
                action.status == ActionStatus.FAILED and PlanGraph(view.snapshot.current_plan).owns_action(action)
                for action in view.snapshot.actions)
            if not failed_plan and any(step.id not in review.step_ids for step in view.ready_steps):
                self.service.advance_agent_run(run_id, advance_request, advance_workers=False,
                                               allow_replanning=False)
            # Each Worker continues its own persisted turns. A slow media
            # observation must not hold its siblings at every decision boundary.
            batch = self.service.advance_agent_team(run_id, continue_until_handoff=True, wait_for_completion=False,
                blocked_work_order_ids=review.work_order_ids)
            failures = [item for item in batch.advances if item.error_message]
            for failure in failures:
                record_trace("worker.error", {"work_order_id": failure.work_order_id,
                    "error": failure.error_message, "error_kind": failure.error_kind})
                if failure.retryable_language_error:
                    raise LanguageProviderTransportError(failure.error_message)
                worker = self.service.agent_runtime.store.get_work_order(failure.work_order_id)
                if worker is None or worker.next_owner != WorkOrderNextOwner.LEADER_REPLAN:
                    raise ValueError(failure.error_message)
            if (review.pending_count or waiting) and not (batch.selected_work_order_ids or batch.active_work_order_ids or batch.waiting_work_order_ids):
                reason = f"{review.pending_count} 处修改等待审阅，其他可执行工作已完成。" if review.pending_count else None
                self._save_state(job_id, "waiting_user", error=reason)
                if waiting:
                    record_trace("chat.assistant", {"text": waiting.input_summary, "needs_input": True, "run_id": run_id})
                return
            # Collect and dispatch independent Workers while the Leader thinks.
            # A saved Worker completion or completed nonempty local plan opens
            # planning. A Worker's intermediate Tool/poll alone does not.
            if not planning_active and not waiting and not review.pending_count:
                planning_active = self._start_completion_plan(job_id, run_id)
            if not planning_active and (failures or batch.advances or not (
                batch.selected_work_order_ids or batch.active_work_order_ids or batch.waiting_work_order_ids
            )):
                advanced = self.service.advance_agent_run(run_id, advance_request, advance_workers=False)
                snapshot = advanced.run.snapshot
                workers = self.service.agent_runtime.store.list_work_orders(run_id)
                worker_can_continue = any(work.status == DelegationStatus.IN_PROGRESS or
                    (work.status in {DelegationStatus.PENDING, DelegationStatus.NEEDS_ATTENTION} and
                     work.next_owner in {WorkOrderNextOwner.WORKER_DECISION, WorkOrderNextOwner.WORKER_TOOL}) for work in workers)
                waiting_input = any(action.kind == ActionKind.USER_INPUT and action.status == ActionStatus.RUNNING for action in snapshot.actions)
                if (snapshot.run.status == AgentRunStatus.WAITING and not advanced.run.ready_steps
                    and not worker_can_continue and not waiting_input and not advanced.executed_action_ids
                    and not self.service.team_executor.has_active_work(run_id)
                    and snapshot.run.current_plan_revision_id == view.snapshot.run.current_plan_revision_id):
                    replan_errors = [note for note in advanced.notes if note.startswith("Automatic replanning failed:")]
                    reason = "; ".join(replan_errors or snapshot.memory.blockers) or snapshot.memory.current_focus or "当前计划没有可执行的下一步"
                    raise ValueError(f"制作任务暂时无法推进：{reason}")
            # Event-driven wakeup makes pause/new input responsive between operations.
            wakeup = self._wakeups[job_id]
            wakeup.wait(0.15)
            wakeup.clear()
