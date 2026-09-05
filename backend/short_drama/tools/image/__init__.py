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
                "在所需视图已分别生成或导入且 Agent 明确选定源版本后，"
                "将 2–9 张本地图片确定性拼成角色、场景或轻量参考板。"
                "它不生成缺失视图、不判断一致性，输出仍是需验收的 DRAFT。"
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
            description=(
                "在 Asset/brief 和本次要生成的精确视图、状态或区域已明确后，"
                "为一个输出版本创建图像任务。GENERATE 不带输入图；从主图派生"
                "视角/换装/状态通常用 EDIT，并显式绑定父版本与参考图。"
                "本 Tool 只入队，之后用 image.advance 推进外部提交。"
            ),
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
            description=(
                "在继续、恢复或判断是否需要新建任务前，列出已持久化的图像"
                "任务，可按 Asset 过滤；它不会改变任务状态。"
            ),
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
            "对已入队图像任务只推进一次 Provider 状态转换；返回后由 Agent 根据新状态决定继续查询、恢复、检查输出或改路线。",
            service.advance_image_task,
            ToolEffect.EXTERNAL,
        ),
        (
            "image.cancel",
            "当任务已不再必要且尚未终态时取消；不删除历史任务或已有版本。",
            service.cancel_image_task,
            ToolEffect.WRITE,
        ),
        (
            "image.retry",
            "当图像任务明确失败、原输入仍是当前版本且 Agent 决定同路线再试时，建立保留系谱的重试转换；SUBMIT_UNKNOWN 不用此 Tool 盲目重提。",
            service.retry_image_task,
            ToolEffect.WRITE,
        ),
        (
            "image.recover_output",
            "当 Provider 输出已存在但本地下载/落盘转换中断时恢复它；不会重新创建远程任务。",
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
                "仅当对账已确认 SUBMIT_UNKNOWN 的远程任务实际未创建时，"
                "记录该事实，使 Agent 之后可显式发起保留系谱的新尝试；"
                "不能用来跳过对账。"
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
