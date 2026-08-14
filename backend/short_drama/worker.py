"""Persistent task state machine and deterministic Mock provider worker."""

from __future__ import annotations

from datetime import UTC, datetime

from short_drama.media import MockMediaRenderer
from short_drama.models import (
    Freshness,
    GenerationTask,
    MockBehavior,
    QAReport,
    QaIssue,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
)
from short_drama.repository import ShortDramaRepository


class InvalidTaskTransition(ValueError):
    """Raised when an action does not apply to the task's persisted state."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class PersistentMockWorker:
    """Advances persisted tasks without relying on in-memory provider state."""

    def __init__(self, repository: ShortDramaRepository, media_renderer: MockMediaRenderer) -> None:
        self.repository = repository
        self.media_renderer = media_renderer
        self.recovered_submissions = self.repository.recover_interrupted_submissions()

    def advance(self, task_id: str) -> GenerationTask:
        task = self._require_task(task_id)
        now = utc_now()
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.SUBMITTING
            task.updated_at = now
            self.repository.save_task(task)
            return task

        if task.status == TaskStatus.SUBMITTING:
            provider_task_id = f"mock-{task.request_fingerprint[:20]}"
            recorded_id, _created = self.repository.record_provider_submission(
                request_fingerprint=task.request_fingerprint,
                provider_task_id=provider_task_id,
                provider=task.provider,
                created_at=now,
            )
            task.submitted_at = now
            task.updated_at = now
            if task.mock_behavior == MockBehavior.SUBMIT_UNKNOWN and task.attempt == 1:
                task.status = TaskStatus.SUBMIT_UNKNOWN
                task.error_code = "SUBMIT_RESPONSE_LOST"
                task.error_message = "Mock Provider 已接收请求，但本地未收到确认；需要对账。"
                self.repository.save_task(task)
                return task
            task.provider_task_id = recorded_id
            task.status = TaskStatus.ACKNOWLEDGED
            self.repository.save_task(task)
            return task

        if task.status == TaskStatus.ACKNOWLEDGED:
            task.status = TaskStatus.RUNNING
            task.started_at = now
            task.error_code = None
            task.error_message = None
            task.updated_at = now
            self.repository.save_task(task)
            return task

        if task.status == TaskStatus.RUNNING:
            if task.mock_behavior == MockBehavior.FAIL_ONCE and task.attempt == 1:
                task.status = TaskStatus.FAILED
                task.error_code = "INJECTED_PROVIDER_FAILURE"
                task.error_message = "用于验证局部失败与重试的可注入 Mock 故障。"
                task.finished_at = now
                task.updated_at = now
                self.repository.save_task(task)
                return task
            if task.mock_behavior == MockBehavior.OUTPUT_MISSING and task.attempt == 1:
                task.status = TaskStatus.OUTPUT_MISSING
                task.error_code = "OUTPUT_MISSING"
                task.error_message = "Provider 已完成，但本地媒体尚未下载。"
                task.finished_at = now
                task.updated_at = now
                self.repository.save_task(task)
                return task
            return self._materialize_success(task, now)

        raise InvalidTaskTransition(f"Task {task.id} cannot advance from {task.status.value}")

    def drain(self, project_id: str, max_steps: int) -> int:
        steps = 0
        while steps < max_steps:
            task = self.repository.next_runnable_task(project_id, provider="mock")
            if task is None:
                break
            self.advance(task.id)
            steps += 1
        return steps

    def cancel(self, task_id: str) -> GenerationTask:
        task = self._require_task(task_id)
        if task.status not in {
            TaskStatus.QUEUED,
            TaskStatus.SUBMITTING,
            TaskStatus.ACKNOWLEDGED,
            TaskStatus.RUNNING,
            TaskStatus.SUBMIT_UNKNOWN,
        }:
            raise InvalidTaskTransition(f"Task {task.id} cannot be cancelled from {task.status.value}")
        now = utc_now()
        task.status = TaskStatus.CANCELLED
        task.error_code = "CANCELLED_BY_USER"
        task.error_message = "任务已由用户取消。"
        task.finished_at = now
        task.updated_at = now
        self.repository.save_task(task)
        return task

    def reconcile(self, task_id: str) -> GenerationTask:
        task = self._require_task(task_id)
        if task.status != TaskStatus.SUBMIT_UNKNOWN:
            raise InvalidTaskTransition(f"Task {task.id} is not waiting for reconciliation")
        provider_task_id = self.repository.find_provider_submission(task.request_fingerprint)
        now = utc_now()
        if provider_task_id is None:
            task.status = TaskStatus.FAILED
            task.error_code = "SUBMISSION_NOT_FOUND"
            task.error_message = "对账确认 Provider 未创建任务，可以安全重试。"
            task.finished_at = now
        else:
            task.status = TaskStatus.ACKNOWLEDGED
            task.provider_task_id = provider_task_id
            task.error_code = None
            task.error_message = None
        task.updated_at = now
        self.repository.save_task(task)
        return task

    def recover_output(self, task_id: str) -> GenerationTask:
        task = self._require_task(task_id)
        if task.status != TaskStatus.OUTPUT_MISSING:
            raise InvalidTaskTransition(f"Task {task.id} has no missing output to recover")
        return self._materialize_success(task, utc_now())

    def _materialize_success(self, task: GenerationTask, now: str) -> GenerationTask:
        document = self.repository.get_project(task.project_id)
        if document is None:
            raise InvalidTaskTransition(f"Project {task.project_id} no longer exists")
        shot = next((item for item in document.shots if item.id == task.shot_id), None)
        if shot is None:
            raise InvalidTaskTransition(f"Shot {task.shot_id} no longer exists")
        script = next(
            (item for item in document.script_versions if item.id == task.input_script_version_id),
            None,
        )
        if script is None:
            raise InvalidTaskTransition(f"Script version {task.input_script_version_id} no longer exists")
        render_shot = shot.model_copy(update={"duration_seconds": task.requested_duration_seconds})
        media = self.media_renderer.render_take(task=task, shot=render_shot, script_lines=script.lines)
        take_id = f"take-{task.id}"
        score = 4.8 if task.candidate_index == 1 else 4.4
        issues = [] if task.candidate_index == 1 else [
            QaIssue(
                code="ALTERNATE_COMPOSITION",
                severity="INFO",
                message="第二候选使用轻微不同的占位构图，用于验证 Take 切换。",
                repair_layer="TAKE_SELECTION",
            )
        ]
        qa_report = QAReport(
            id=f"qa-{take_id}",
            take_id=take_id,
            status=QaStatus.PASSED,
            hard_gate_passed=True,
            score=score,
            issues=issues,
            checks=[
                "媒体文件可读取",
                "时长与 ShotPlan 一致",
                "9:16 画幅",
                "音轨存在",
                "锁定台词由 ScriptVersion 引用",
                "道具状态由连续性计划引用",
            ],
            created_at=now,
        )
        take = Take(
            id=take_id,
            project_id=task.project_id,
            shot_id=task.shot_id,
            task_id=task.id,
            candidate_index=task.candidate_index,
            operation=task.operation,
            media_path=str(media.media_path.resolve()),
            thumbnail_path=str(media.thumbnail_path.resolve()),
            duration_seconds=task.requested_duration_seconds,
            width=media.width,
            height=media.height,
            availability=TakeAvailability.AVAILABLE,
            qa_status=QaStatus.PASSED,
            is_selected=False,
            freshness=Freshness.CURRENT,
            qa_report=qa_report,
            created_at=now,
        )
        self.repository.save_take(take)
        existing_selected = next(
            (
                item
                for item in self.repository.list_takes_for_shot(task.project_id, task.shot_id)
                if item.is_selected and item.freshness == Freshness.CURRENT
            ),
            None,
        )
        if existing_selected is None:
            self.repository.select_take(take.id)

        task.status = TaskStatus.SUCCEEDED
        task.error_code = None
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        self.repository.save_task(task)
        return task

    def _require_task(self, task_id: str) -> GenerationTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task
