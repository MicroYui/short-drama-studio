"""Persisted generation-task lifecycle tool adapters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from short_drama.models import GenerationTask, Take, TaskStatus, WorkerDrainRequest, WorkerDrainResponse
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
    def get_generation_task(self, task_id: str) -> GenerationTask: ...

    def list_generation_task_takes(self, task_id: str) -> list[Take]: ...

    def advance_task(self, task_id: str) -> GenerationTask: ...

    def drain(self, project_id: str, max_steps: int) -> WorkerDrainResponse: ...

    def cancel_task(self, task_id: str) -> GenerationTask: ...

    def retry_task(self, task_id: str) -> GenerationTask: ...

    def reconcile_task(self, task_id: str) -> GenerationTask: ...

    def recover_task_output(self, task_id: str) -> GenerationTask: ...


class TaskActionInput(ToolModel):
    task_id: str


class TaskReadResponse(ToolModel):
    task: GenerationTask
    takes: list[Take]


def _task_refs(task: GenerationTask, takes: Sequence[Take]) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(project_id=task.project_id, artifact_type="generation_task", artifact_id=task.id),
        *(ArtifactRef(project_id=task.project_id, artifact_type="take", artifact_id=take.id) for take in takes),
    )


def _task_execution(task: GenerationTask, takes: Sequence[Take] = ()) -> ToolExecution[GenerationTask]:
    return ToolExecution(
        output=task,
        artifact_refs=_task_refs(task, takes),
    )


def register_tools(registry: ToolRegistry, service: TaskToolService) -> None:
    def get_task(request: TaskActionInput) -> ToolExecution[TaskReadResponse]:
        task = service.get_generation_task(request.task_id)
        takes = service.list_generation_task_takes(task.id)
        return ToolExecution(output=TaskReadResponse(task=task, takes=takes), artifact_refs=_task_refs(task, takes))

    registry.register(ToolSpec(
        id="task.get", version=1, category=ToolCategory.TASK,
        description=("Read a persisted video task and its produced Takes, including exact Take IDs and media paths. "
                     "A downloaded Take may await review and selection before an output slot is bound; "
                     "a missing output slot alone does not mean OUTPUT_MISSING. No provider calls."),
        effect=ToolEffect.READ, idempotent=True, input_model=TaskActionInput,
        output_model=TaskReadResponse, handler=get_task,
        tags=frozenset({"generation", "task", "lifecycle", "output"}),
    ))
    registry.register(
        ToolSpec(
            id="task.drain",
            version=1,
            category=ToolCategory.TASK,
            description=(
                "Advance runnable deterministic Mock tasks for one project within a step "
                "bound; real H3 tasks must use task.advance."
            ),
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
            tags=frozenset({"generation", "task", "worker", "mock"}),
        )
    )

    def register_action(
        *,
        tool_id: str,
        description: str,
        action: Callable[[str], GenerationTask],
        effect: ToolEffect,
        extra_tags: frozenset[str] = frozenset(),
    ) -> None:
        def invoke(request: TaskActionInput) -> ToolExecution[GenerationTask]:
            task = action(request.task_id)
            takes = service.list_generation_task_takes(task.id) if task.status == TaskStatus.SUCCEEDED else []
            return _task_execution(task, takes)

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
                tags=frozenset({"generation", "task", "lifecycle"}) | extra_tags,
            )
        )

    register_action(
        tool_id="task.advance",
        description="Advance one persisted generation task by a single transition; success also returns its produced Take references.",
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
        description="Reconcile an uncertain deterministic Mock generation task.",
        action=service.reconcile_task,
        effect=ToolEffect.EXTERNAL,
        extra_tags=frozenset({"mock"}),
    )
    register_action(
        tool_id="task.recover_output",
        description="Recover provider output for a completed but interrupted task.",
        action=service.recover_task_output,
        effect=ToolEffect.EXTERNAL,
    )


__all__ = ["TaskActionInput", "register_tools"]
