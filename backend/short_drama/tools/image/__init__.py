"""Image task lifecycle tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.image_models import (
    AssetMutationResponse,
    AssetReferenceSheetComposeRequest,
    ImageNotSubmittedResolutionRequest,
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
    def compose_asset_reference_sheet(
        self,
        project_id: str,
        asset_id: str,
        request: AssetReferenceSheetComposeRequest,
    ) -> AssetMutationResponse: ...

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

    def resolve_image_not_submitted(
        self,
        task_id: str,
        request: ImageNotSubmittedResolutionRequest,
    ) -> ImageTaskResponse: ...

    def recover_image_task_output(self, task_id: str) -> ImageTaskResponse: ...


class ImageEnqueueInput(ToolModel):
    project_id: str
    asset_id: str
    request: ImageTaskCreateRequest


class ImageReferenceSheetComposeInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetReferenceSheetComposeRequest


class ImageListInput(ToolModel):
    project_id: str
    asset_id: str | None = None


class ImageTaskActionInput(ToolModel):
    task_id: str


class ImageNotSubmittedInput(ToolModel):
    task_id: str
    request: ImageNotSubmittedResolutionRequest


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
    def compose_reference_sheet(
        request: ImageReferenceSheetComposeInput,
    ) -> ToolExecution[AssetMutationResponse]:
        result = service.compose_asset_reference_sheet(
            request.project_id,
            request.asset_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset",
                    artifact_id=result.asset.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset_version",
                    artifact_id=result.version.id,
                    version=result.version.version,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="image.compose_reference_sheet",
            version=1,
            category=ToolCategory.IMAGE,
            description=(
                "Deterministically compose explicitly selected local AssetVersions "
                "into one labeled character, scene, or lightweight reference sheet."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ImageReferenceSheetComposeInput,
            output_model=AssetMutationResponse,
            handler=compose_reference_sheet,
            tags=frozenset({"asset", "image", "reference-sheet", "local"}),
        )
    )

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

    registry.register(
        ToolSpec(
            id="image.resolve_not_submitted",
            version=1,
            category=ToolCategory.IMAGE,
            description=(
                "Record that an uncertain image submission was not created so an "
                "explicit lineage-preserving retry can be made."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ImageNotSubmittedInput,
            output_model=ImageTaskResponse,
            handler=lambda request: _task_execution(
                service.resolve_image_not_submitted(
                    request.task_id,
                    request.request,
                )
            ),
            tags=frozenset({"image", "reconciliation", "task"}),
        )
    )


__all__ = [
    "ImageEnqueueInput",
    "ImageListInput",
    "ImageNotSubmittedInput",
    "ImageReferenceSheetComposeInput",
    "ImageTaskActionInput",
    "register_tools",
]
