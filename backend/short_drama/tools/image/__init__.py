"""Image task lifecycle tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.image_capabilities import ImageProviderCapabilityResponse
from short_drama.image_models import (
    AgentImageTaskCreateRequest,
    AssetMutationResponse,
    AssetReferenceSheetComposeRequest,
    AssetTextPlateComposeRequest,
    ImageNotSubmittedResolutionRequest,
    ImageTaskCreateRequest,
    ImageTaskListResponse,
    ImageTaskResponse,
    ImageTaskStatus,
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
    def get_image_provider_capability(self) -> ImageProviderCapabilityResponse: ...

    def compose_asset_reference_sheet(
        self,
        project_id: str,
        asset_id: str,
        request: AssetReferenceSheetComposeRequest,
    ) -> AssetMutationResponse: ...

    def compose_asset_text_plate(
        self,
        project_id: str,
        asset_id: str,
        request: AssetTextPlateComposeRequest,
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
    request: AgentImageTaskCreateRequest


class ImageCapabilityInput(ToolModel):
    pass


class ImageReferenceSheetComposeInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetReferenceSheetComposeRequest


class ImageTextPlateComposeInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetTextPlateComposeRequest


class ImageListInput(ToolModel):
    project_id: str
    asset_id: str | None = None


class ImageTaskActionInput(ToolModel):
    task_id: str


class ImageNotSubmittedInput(ToolModel):
    task_id: str
    request: ImageNotSubmittedResolutionRequest


def _task_execution(result: ImageTaskResponse) -> ToolExecution[ImageTaskResponse]:
    refs = [
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
    ]
    if result.task.status == ImageTaskStatus.SUCCEEDED:
        refs.append(
            ArtifactRef(
                project_id=result.task.project_id,
                artifact_type="asset_version",
                artifact_id=result.task.output_version_id,
                version=result.task.output_version_number,
            )
        )
    return ToolExecution(
        output=result,
        artifact_refs=tuple(refs),
    )


def register_tools(registry: ToolRegistry, service: ImageToolService) -> None:
    registry.register(
        ToolSpec(
            id="image.capability",
            version=1,
            category=ToolCategory.IMAGE,
            description=(
                "在规划或执行资产前，读取已配置 GPT Image 适配器的"
                "无密钥本地能力快照。它不发起网络请求，返回凭证就绪"
                "状态、Azure 路由、原创/参考生成/编辑路线、多参考与高"
                "输入保真、本地尺寸约束、透明背景行为及当前价格快照。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ImageCapabilityInput,
            output_model=ImageProviderCapabilityResponse,
            handler=lambda _request: ToolExecution(
                output=service.get_image_provider_capability()
            ),
            tags=frozenset({"image", "capability", "provider", "discovery"}),
        )
    )

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
                "它适合最终参考卡需要像素级分区、固定语义槽、可重复裁切"
                "或统一背景时使用，避免让概率生成器为微小几何偏差反复返修。"
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

    def compose_text_plate(
        request: ImageTextPlateComposeInput,
    ) -> ToolExecution[AssetMutationResponse]:
        result = service.compose_asset_text_plate(
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
            id="image.compose_text_plate",
            version=1,
            category=ToolCategory.IMAGE,
            description=(
                "为 presentation_mode=SCREEN_CONTENT_PLATE 的资产在本地"
                "确定性排版菜价、金额、姓名、标题或文件条款。title "
                "与 exact_lines 必须逐项完整匹配 brief.required_visible_text；"
                "同一物理母版上的不同文案分别保存为独立 TEXT_PLATE，"
                "本 Tool 不调用图像 Provider，输出可追溯的 PNG DRAFT。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ImageTextPlateComposeInput,
            output_model=AssetMutationResponse,
            handler=compose_text_plate,
            tags=frozenset({"asset", "image", "text-plate", "local"}),
        )
    )

    def enqueue(request: ImageEnqueueInput) -> ToolExecution[ImageTaskResponse]:
        return _task_execution(
            service.enqueue_image_task(
                request.project_id,
                request.asset_id,
                request.request.to_service_request(),
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
                "request.layout_profile 只描述本次输出；单张脸部、正面、"
                "侧面、背面素材使用 LIGHTWEIGHT_SINGLE，不继承 Asset.brief "
                "中最终参考卡的多区布局。"
                "参考图只使用有序 reference_inputs，其版本字段名是 "
                "asset_version_id；不要传 legacy input_version_ids。"
                "一图生成标准卡时，已登记的布局 guide 必须作为真实参考"
                "传入才能在 Prompt 中声称“图N是布局参考”；没有实际"
                "LAYOUT_GUIDE reference_input 就不执行或冒充官方标准一图路线。"
                "已有图像提供可用身份时，定向修视图、裁切、排版或局部状态"
                "优先用 EDIT，并将该图作为 parent 和 identity reference；不用无参考"
                "GENERATE 冒充“保持原人物”。像素级几何改用"
                "分视图后调用 image.compose_reference_sheet。"
                "对只有后续文字不同的同规格重复载体，不要求 Provider 一次"
                "生成多件像素级一致的物件；先生成单件干净物理母版，"
                "各处引用同一版本并将文字交给本地 TEXT_PLATE。"
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
            "仅当失败任务的 retry_disposition=RETRY_SAME_REQUEST 时，复制原请求并建立保留系谱的新任务。该结构化分类只由 Provider 状态与提交事实产生；moderation/content policy、参数、引用、输出尺寸或其他请求语义错误不允许原样付费重提，应根据错误语义修订 prompt/引用/operation/参数后调用新的 image.enqueue。SUBMIT_UNKNOWN 先对账，OUTPUT_MISSING 先恢复输出。",
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
    "ImageCapabilityInput",
    "ImageEnqueueInput",
    "ImageListInput",
    "ImageNotSubmittedInput",
    "ImageReferenceSheetComposeInput",
    "ImageTaskActionInput",
    "register_tools",
]
