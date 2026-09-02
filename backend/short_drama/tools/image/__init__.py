"""Image task lifecycle tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.image_models import (
    ImageTaskCreateRequest,
    ImageTaskListResponse,
    ImageTaskResponse,
)
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class ImageToolService(Protocol):
    def enqueue_image_task(
        self,
        project_id: str,
        asset_id: str,
        request: ImageTaskCreateRequest,
    ) -> ImageTaskResponse: ...

    def list_image_tasks(
        self,
        project_id: str,
        asset_id: str | None = None,
    ) -> ImageTaskListResponse: ...

    def advance_image_task(self, task_id: str) -> ImageTaskResponse: ...

    def cancel_image_task(self, task_id: str) -> ImageTaskResponse: ...

    def retry_image_task(self, task_id: str) -> ImageTaskResponse: ...

    def recover_image_task_output(self, task_id: str) -> ImageTaskResponse: ...


class ImageEnqueueInput(ToolModel):
    project_id: str
    asset_id: str
    request: ImageTaskCreateRequest


class ImageListInput(ToolModel):
    project_id: str
    asset_id: str | None = None


class ImageTaskActionInput(ToolModel):
    task_id: str


def _task_execution(result: ImageTaskResponse) -> ToolExecution[ImageTaskResponse]:
    return ToolExecution(
        output=result,
        artifact_refs=(
            ArtifactRef(
                project_id=result.task.project_id,
                artifact_type="image_task",
                artifact_id=result.task.id,
            ),
            ArtifactRef(
                project_id=result.task.project_id,
                artifact_type="asset",
                artifact_id=result.task.asset_id,
            ),
            ArtifactRef(
                project_id=result.task.project_id,
                artifact_type="asset_version",
                artifact_id=result.task.output_version_id,
                version=result.task.output_version_number,
            ),
        ),
    )


def register_tools(registry: ToolRegistry, service: ImageToolService) -> None:
    def enqueue(request: ImageEnqueueInput) -> ToolExecution[ImageTaskResponse]:
        return _task_execution(
            service.enqueue_image_task(
                request.project_id,
                request.asset_id,
                request.request,
            )
        )

    registry.register(
        ToolSpec(
            id="image.enqueue",
            version=1,
            category=ToolCategory.IMAGE,
            description="Queue a provider-neutral image generation or edit task.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ImageEnqueueInput,
            output_model=ImageTaskResponse,
            handler=enqueue,
            tags=frozenset({"asset", "generation", "image"}),
        )
    )

    registry.register(
        ToolSpec(
            id="image.list",
            version=1,
            category=ToolCategory.IMAGE,
            description="List persisted image tasks, optionally filtered by asset.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ImageListInput,
            output_model=ImageTaskListResponse,
            handler=lambda request: ToolExecution(
                output=service.list_image_tasks(request.project_id, request.asset_id)
            ),
            tags=frozenset({"image", "task", "discovery"}),
        )
    )

    actions = (
        (
            "image.advance",
            "Advance one image task by a single provider-task transition.",
            service.advance_image_task,
            ToolEffect.EXTERNAL,
        ),
        (
            "image.cancel",
            "Cancel an image task that has not reached a terminal state.",
            service.cancel_image_task,
            ToolEffect.WRITE,
        ),
        (
            "image.retry",
            "Create the supported retry transition for a failed image task.",
            service.retry_image_task,
            ToolEffect.WRITE,
        ),
        (
            "image.recover_output",
            "Recover a persisted image output after an interrupted local transition.",
            service.recover_image_task_output,
            ToolEffect.EXTERNAL,
        ),
    )
    for tool_id, description, action, effect in actions:
        registry.register(
            ToolSpec(
                id=tool_id,
                version=1,
                category=ToolCategory.IMAGE,
                description=description,
                effect=effect,
                idempotent=False,
                input_model=ImageTaskActionInput,
                output_model=ImageTaskResponse,
                handler=lambda request, action=action: _task_execution(
                    action(request.task_id)
                ),
                tags=frozenset({"image", "task", "lifecycle"}),
            )
        )


__all__ = [
    "ImageEnqueueInput",
    "ImageListInput",
    "ImageTaskActionInput",
    "register_tools",
]
