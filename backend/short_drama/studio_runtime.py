"""Reconnectable product sessions over the persisted AgentRun and Team engine."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.agent_runtime.models import ActionKind, ActionStatus, AgentRunStatus, DelegationStatus, WorkOrderNextOwner
from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.models import ProductionExecutionTarget
from short_drama.production_manager import (
    AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest, AgentUserInputRequest,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderTransportError, is_language_transport_error
from short_drama.studio_models import StudioActivity, StudioJob, StudioJobControl, StudioMessage, StudioWorker
from short_drama.studio_trace import production_trace, record_trace, trace_context

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
        self.retry_delays = retry_delays

    def activity(self, project_id: str, after: int = 0) -> StudioActivity:
        jobs = self.service.repository.list_studio_jobs(project_id)
        workers: list[StudioWorker] = []
        run_id = jobs[0].run_id if jobs else None
        if run_id is not None:
            for work in self.service.agent_runtime.store.list_work_orders(run_id):
                result = self.service.agent_runtime.store.get_agent_result(work.id)
                workers.append(StudioWorker(id=work.id, run_id=run_id, role=work.recipient_role,
                    objective=work.objective, status=work.status.value,
                    next_owner=None if work.next_owner is None else work.next_owner.value,
                    summary=None if result is None else result.concise_summary, tool_turns=len(work.specialist_turns)))
        return StudioActivity(jobs=jobs, workers=workers,
            context_window_tokens=ContextBudget().window_tokens,
            events=self.service.repository.list_studio_events(project_id, after=after, limit=2000))

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
            self._launch(job.id)
            return job

    def resume_after_restart(self) -> None:
        for job in self.service.repository.list_studio_jobs():
            if job.status in {"queued", "running", "retrying", "pausing"}:
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
            if len(job.pending_messages) > self._processing_messages.get(job_id, 0):
                return "User input is pending"
        return None

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
            with production_trace(directory, emit), execution_control(lambda: self._yield_reason(job_id)):
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
                            if current.status == "pausing":
                                self._save_state(job_id, "paused", error=str(exc))
                                return
                            if is_language_transport_error(exc) and current.retry_attempt < len(self.retry_delays):
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
                        self._save_state(job_id, "failed", error=str(exc))
                        record_trace("chat.assistant", {"text": f"这一步未完成：{exc}。已保存的内容和任务仍在，可以修改要求后继续。", "failed": True})
                        return
        finally:
            with self.lock:
                self.threads.pop(job_id, None)
                self._processing_messages.pop(job_id, None)
                current = self._job(job_id)
                if current.pending_messages and current.status in {"queued", "running", "retrying"}:
                    self._launch(job_id)

    def _apply_messages(self, job_id: str, run_id: str) -> None:
        with self.lock:
            messages = list(self._job(job_id).pending_messages)
            if not messages:
                return
            self._processing_messages[job_id] = len(messages)
        try:
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
        if job.status == "pausing":
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
        self._save_state(job_id, "running")
        with trace_context(run_id=run_id, agent_role="leader", work_order_id=None):
            self._advance_until_handoff(job_id, run_id)

    def _advance_until_handoff(self, job_id: str, run_id: str) -> None:
        published_actions: dict[str, str] = {}
        while True:
            if self._job(job_id).status == "pausing":
                self._save_state(job_id, "paused")
                return
            self._apply_messages(job_id, run_id)
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
            if waiting:
                self._save_state(job_id, "waiting_user")
                record_trace("chat.assistant", {"text": waiting.input_summary, "needs_input": True, "run_id": run_id})
                return
            # Materialize independent siblings before running their decisions.
            # The Agent-authored dependency graph still decides what is ready.
            if view.ready_steps and view.ready_steps[0].kind == ActionKind.DELEGATION:
                self.service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
                continue
            batch = self.service.advance_agent_team(run_id)
            failures = [item for item in batch.advances if item.error_message]
            for failure in failures:
                record_trace("worker.error", {"work_order_id": failure.work_order_id,
                    "error": failure.error_message, "error_kind": failure.error_kind})
                if failure.retryable_language_error:
                    raise LanguageProviderTransportError(failure.error_message)
                worker = self.service.agent_runtime.store.get_work_order(failure.work_order_id)
                if worker is None or worker.next_owner != WorkOrderNextOwner.LEADER_REPLAN:
                    raise ValueError(failure.error_message)
            if failures or not batch.selected_work_order_ids:
                advanced = self.service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
                snapshot = advanced.run.snapshot
                workers = self.service.agent_runtime.store.list_work_orders(run_id)
                worker_can_continue = any(work.status == DelegationStatus.IN_PROGRESS or
                    (work.status in {DelegationStatus.PENDING, DelegationStatus.NEEDS_ATTENTION} and
                     work.next_owner in {WorkOrderNextOwner.WORKER_DECISION, WorkOrderNextOwner.WORKER_TOOL}) for work in workers)
                waiting_input = any(action.kind == ActionKind.USER_INPUT and action.status == ActionStatus.RUNNING for action in snapshot.actions)
                if (snapshot.run.status == AgentRunStatus.WAITING and not advanced.run.ready_steps
                    and not worker_can_continue and not waiting_input and not advanced.executed_action_ids
                    and snapshot.run.current_plan_revision_id == view.snapshot.run.current_plan_revision_id):
                    reason = "; ".join(snapshot.memory.blockers) or snapshot.memory.current_focus or "当前计划没有可执行的下一步"
                    raise ValueError(f"制作任务暂时无法推进：{reason}")
            # Event-driven wakeup makes pause/new input responsive between operations.
            wakeup = self._wakeups[job_id]
            wakeup.wait(0.15)
            wakeup.clear()
