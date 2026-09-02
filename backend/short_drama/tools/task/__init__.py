"""Persisted generation-task lifecycle tool adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from short_drama.models import GenerationTask, WorkerDrainRequest, WorkerDrainResponse
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class TaskToolService(Protocol):
    def advance_task(self, task_id: str) -> GenerationTask: ...

    def drain(self, project_id: str, max_steps: int) -> WorkerDrainResponse: ...

    def cancel_task(self, task_id: str) -> GenerationTask: ...

    def retry_task(self, task_id: str) -> GenerationTask: ...

    def reconcile_task(self, task_id: str) -> GenerationTask: ...

    def recover_task_output(self, task_id: str) -> GenerationTask: ...


class TaskActionInput(ToolModel):
    task_id: str


def _task_execution(task: GenerationTask) -> ToolExecution[GenerationTask]:
    return ToolExecution(
        output=task,
        artifact_refs=(
            ArtifactRef(
                project_id=task.project_id,
                artifact_type="generation_task",
                artifact_id=task.id,
            ),
        ),
    )


def register_tools(registry: ToolRegistry, service: TaskToolService) -> None:
    registry.register(
        ToolSpec(
            id="task.drain",
            version=1,
            category=ToolCategory.TASK,
            description="Advance runnable generation tasks for one project within a step bound.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=WorkerDrainRequest,
            output_model=WorkerDrainResponse,
            handler=lambda request: ToolExecution(
                output=service.drain(request.project_id, request.max_steps),
                artifact_refs=(
                    ArtifactRef(
                        project_id=request.project_id,
                        artifact_type="project",
                        artifact_id=request.project_id,
                    ),
                ),
            ),
            tags=frozenset({"generation", "task", "worker"}),
        )
    )

    def register_action(
        *,
        tool_id: str,
        description: str,
        action: Callable[[str], GenerationTask],
        effect: ToolEffect,
    ) -> None:
        def invoke(request: TaskActionInput) -> ToolExecution[GenerationTask]:
            return _task_execution(action(request.task_id))

        registry.register(
            ToolSpec(
                id=tool_id,
                version=1,
                category=ToolCategory.TASK,
                description=description,
                effect=effect,
                idempotent=False,
                input_model=TaskActionInput,
                output_model=GenerationTask,
                handler=invoke,
                tags=frozenset({"generation", "task", "lifecycle"}),
            )
        )

    register_action(
        tool_id="task.advance",
        description="Advance one persisted generation task by a single transition.",
        action=service.advance_task,
        effect=ToolEffect.EXTERNAL,
    )
    register_action(
        tool_id="task.cancel",
        description="Cancel one eligible persisted generation task.",
        action=service.cancel_task,
        effect=ToolEffect.WRITE,
    )
    register_action(
        tool_id="task.retry",
        description="Retry one failed persisted generation task.",
        action=service.retry_task,
        effect=ToolEffect.WRITE,
    )
    register_action(
        tool_id="task.reconcile",
        description="Reconcile the current provider state of an uncertain generation task.",
        action=service.reconcile_task,
        effect=ToolEffect.EXTERNAL,
    )
    register_action(
        tool_id="task.recover_output",
        description="Recover provider output for a completed but interrupted task.",
        action=service.recover_task_output,
        effect=ToolEffect.EXTERNAL,
    )


__all__ = ["TaskActionInput", "register_tools"]
