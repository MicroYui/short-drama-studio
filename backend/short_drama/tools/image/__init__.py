"""Image task lifecycle tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.image_capabilities import ImageProviderCapabilityResponse
from short_drama.image_edit_guides import AssetImageEditGuideRequest, AssetImageEditGuideResponse
from short_drama.image_edit_results import AssetImageEditResultRequest
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest
from short_drama.image_models import (
    AgentImageTaskCreateRequest,
    AssetMutationResponse,
    AssetImageCropRequest,
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
    ToolWriteReceipt,
)
from short_drama.tools.registry import ToolRegistry


class ImageToolService(Protocol):
    def get_image_provider_capability(self) -> ImageProviderCapabilityResponse: ...

    def crop_asset_image(
        self, project_id: str, asset_id: str, request: AssetImageCropRequest,
    ) -> AssetMutationResponse: ...

    def create_asset_edit_guide(
        self, project_id: str, request: AssetImageEditGuideRequest,
    ) -> AssetImageEditGuideResponse: ...

    def composite_asset_plane(
        self, project_id: str, request: AssetImagePlaneCompositeRequest,
    ) -> AssetMutationResponse: ...

    def derive_asset_edit_result(
        self, project_id: str, request: AssetImageEditResultRequest,
    ) -> AssetMutationResponse: ...

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


class ImageCropInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetImageCropRequest


class ImageEditGuideInput(ToolModel):
    project_id: str
    request: AssetImageEditGuideRequest


class ImagePlaneCompositeInput(ToolModel):
    project_id: str
    request: AssetImagePlaneCompositeRequest


class ImageEditResultInput(ToolModel):
    project_id: str
    request: AssetImageEditResultRequest


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

    def crop_image(request: ImageCropInput) -> ToolExecution[AssetMutationResponse]:
        result = service.crop_asset_image(request.project_id, request.asset_id, request.request)
        return ToolExecution(output=result, artifact_refs=(
            ArtifactRef(project_id=request.project_id, artifact_type="asset", artifact_id=result.asset.id),
            ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=result.version.id,
                        version=result.version.version),
        ))

    registry.register(ToolSpec(
        id="image.crop", version=1, category=ToolCategory.IMAGE,
        description=(
            "从已选本地图片裁出清晰脸部、单个视图、场景区域或道具，直接登记目标 Asset 的新版本。"
            "region 是图片按 EXIF 转正后的归一化 x/y/width/height；使用真实看过的区域，不猜测五官位置。"
            "保留原始像素与透明度，不调用 Provider、不放大、不重绘、不改变原图或当前母版。"
            "返回 image_crop 的真实源尺寸和像素范围。低分辨率原脸不会因裁切增加细节；"
            "需要时用 qa.inspect_asset_version 检查局部并将可用结果加入 ReferencePack。"
        ),
        effect=ToolEffect.WRITE, idempotent=False, input_model=ImageCropInput,
        output_model=AssetMutationResponse, handler=crop_image,
        tags=frozenset({"image", "asset", "crop", "local", "identity", "reference"}),
    ))

    def create_edit_guide(request: ImageEditGuideInput) -> ToolExecution[AssetImageEditGuideResponse]:
        result = service.create_asset_edit_guide(request.project_id, request.request)
        refs = tuple(ArtifactRef(project_id=request.project_id, artifact_type="asset_version",
            artifact_id=version.id, version=version.version)
            for version in (result.guide_version, result.mask_version))
        return ToolExecution(output=result, artifact_refs=refs, receipt=ToolWriteReceipt(
            artifact_refs=list(refs), current_state={"project_id": result.project_id, "asset_id": result.asset_id,
                "source_asset_version_id": result.source_asset_version_id,
                "guide_asset_version_id": result.guide_version.id,
                "mask_asset_version_id": result.mask_version.id,
                "width": result.guide_version.width, "height": result.guide_version.height,
                "guide_reference_role": "LAYOUT_GUIDE",
                "mask_semantics": "transparent pixels are editable; opaque pixels preserve the original"},
            changed_ids={"created_asset_version_ids": [version.id for version in (result.guide_version, result.mask_version)]}))

    registry.register(ToolSpec(
        id="image.create_edit_guide", version=1, category=ToolCategory.IMAGE,
        description=(
            "为看过的实际候选制作本地局部编辑图示和独立PNG透明遮罩，不调用Provider。"
            "按归一化图像坐标圈出真正需要重画的区域，可加箭头与具体目标文字，"
            "例如标题边应落在画面哪一侧、哪只手需要移动。区域包含物体原位置和目标位置；"
            "要保留的手指、脸、门窗不要圈入。仅世界方位词不能代替可见位置。"
            "返回两个同尺寸精确版本并保留current/canonical。后续image.enqueue用EDIT，"
            "原图仍为parent及第一张primary参考，图示作为额外LAYOUT_GUIDE参考，"
            "mask_version_id单独使用返回遮罩，不放进reference_inputs；size与原图一致。"
            "注明红框、箭头、标签只是修改说明，不出现在结果中。output_format用png时，现有EDIT合成保留mask外原像素；mask_feather_pixels为0直接替换圈内，非零仅柔化圈内边缘。"
            "圈内是否改对仍需观察。涉及整个人物站位或全景机位变化时不要用狭小mask限制修改。"
            "图示和遮罩不是修好的照片，不登记为镜头已完成。源像素需已转正；"
            "带EXIF旋转的导入图先用image.crop取完整图像得到转正版本。"
            "原图尺寸也须符合image.enqueue的边长、像素总数和16倍数限制；不符合时用可用尺寸原图，"
            "或不用mask进行整图EDIT，不能仅改请求size而让遮罩错位。"
        ),
        effect=ToolEffect.WRITE, idempotent=False, input_model=ImageEditGuideInput,
        output_model=AssetImageEditGuideResponse, handler=create_edit_guide,
        tags=frozenset({"image", "asset", "edit", "mask", "local", "layout", "reference"}),
    ))

    def derive_edit_result(request: ImageEditResultInput) -> ToolExecution[AssetMutationResponse]:
        result = service.derive_asset_edit_result(request.project_id, request.request)
        refs = (
            ArtifactRef(project_id=request.project_id, artifact_type="asset", artifact_id=result.asset.id),
            ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=result.version.id,
                version=result.version.version),
        )
        return ToolExecution(output=result, artifact_refs=refs, receipt=ToolWriteReceipt(
            artifact_refs=list(refs), current_state={
                "project_id": request.project_id, "asset_id": result.asset.id,
                "image_task_id": request.request.image_task_id,
                "mode": str(request.request.mode), "mask_version_id": request.request.mask_version_id,
                "mask_feather_pixels": request.request.mask_feather_pixels,
                "version.id": result.version.id, "version.version": result.version.version,
                "version.local_path": result.version.local_path, "version.width": result.version.width,
                "version.height": result.version.height,
                "asset.current_version_id": result.asset.current_version_id,
                "provider_called": False, "visual_acceptance": "NOT_ASSESSED",
            }, changed_ids={"created_asset_version_ids": [result.version.id]}))

    registry.register(ToolSpec(
        id="image.derive_edit_result", version=1, category=ToolCategory.IMAGE,
        description=(
            "使用已有masked EDIT任务保留的供应商原始像素，取出供检查或调整遮罩后本地重新合成，保存新的本地DRAFT候选，不调用Provider；原结果保留其编码，遮罩重合成为PNG。"
            "先确认image_task_id对应已完成任务且provider_output_path实际可用；历史任务未留存原图时不能恢复，也不自动重新生成。"
            "PROVIDER_OUTPUT保存遮罩合成前的整张供应商结果供实际看图，圈外也可能变化，不表示可直接采用；该模式不传mask或非零feather。"
            "MASKED_COMPOSITE使用原任务实际primary、原始供应商结果及明确的新mask_version_id；新mask尺寸与原图相同，透明区用新像素，圈外保留原图。"
            "可用image.create_edit_guide在原任务primary上重新定义实际边界；mask_feather_pixels只柔化圈内边缘，不扩大区域，不能补出原结果没有的内容。"
            "适用于已生成内容被局部边界截断、旧轮廓残留等情形。先根据原结果和实际遮罩判断，不能只重写说明而沿用已证明不合适的坐标。"
            "原任务的使用量、最终输出及当前/批准媒体和镜头绑定不变；候选仍需检查完整结构、背景和接缝后明确选用。"
        ),
        effect=ToolEffect.WRITE, idempotent=False, input_model=ImageEditResultInput,
        output_model=AssetMutationResponse, handler=derive_edit_result,
        tags=frozenset({"image", "asset", "local", "edit", "mask", "composite", "reuse"}),
    ))

    def composite_plane(request: ImagePlaneCompositeInput) -> ToolExecution[AssetMutationResponse]:
        result = service.composite_asset_plane(request.project_id, request.request)
        refs = (
            ArtifactRef(project_id=request.project_id, artifact_type="asset", artifact_id=result.asset.id),
            ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=result.version.id,
                version=result.version.version),
        )
        return ToolExecution(output=result, artifact_refs=refs, receipt=ToolWriteReceipt(
            artifact_refs=list(refs), current_state={
                "project_id": request.project_id, "asset_id": result.asset.id,
                "base_asset_version_id": request.request.base_asset_version_id,
                "source_asset_version_ids": list(dict.fromkeys(layer.source_asset_version_id for layer in request.request.layers)),
                "version.id": result.version.id, "version.version": result.version.version,
                "version.local_path": result.version.local_path, "version.width": result.version.width,
                "version.height": result.version.height,
                "asset.current_version_id": result.asset.current_version_id,
                "composition_completed": True, "visual_acceptance": "NOT_ASSESSED",
            }, changed_ids={"created_asset_version_ids": [result.version.id]}))

    registry.register(ToolSpec(
        id="image.composite_plane", version=1, category=ToolCategory.IMAGE,
        description=(
            "把已有准确图文或摄影纸面按四角透视映射到实际照片的平面上，保存本地PNG候选，不调用Provider。"
            "适用于可观察的纸面、屏幕、标牌等平面，整页字符与布局一起变换，不重新排字或生成姓名。"
            "base和各source均用本项目精确本地图片版本；先观察所选可见共面区域的四角，source_quad省略时取整张源图。"
            "target_quad的每一点对应同序source角，旋转90度通过这种对应表达；不能把两组角分别按画面上下重新排序。"
            "摄影源仅圈纸面，不带桌面和灰背景。replace覆盖平面，multiply保留底面纹理但也保留旧墨，"
            "仅在实际确认目标是空白或透明图形适用时使用multiply；源白色不改变底图，灰色会压暗，不能清除已有错字。"
            "摄影纸存在光照/灰底内框时，可用paper_ink给同一源纸的空白blank_sample及实际像素背景窗口；"
            "程序测量纸纹残差、除去局部纸光后multiply原墨迹，不重绘字，不用反复看图猜白点。"
            "需替换目标旧细印字时可显式给target_paper_cleanup.regions，仅在paper_ink平面、指定区减保护区内局部恢复旧字像素；"
            "不适合人物、纸边、接触影、复杂纹理或不可恢复的背景，目标已空白则省略清除。"
            "paper_ink须用multiply且不同时设source_white_point；省略新选项不改变原行为。"
            "已有可靠空白纸面RGB测量时仍可用source_white_point，不把视觉估计称为测量，不预设固定值。"
            "它保持原手写与红改的几何形状，但浅墨、细灰线和浅色可能减弱或截白，须据本次实图检查。"
            "用protected_regions保留真实可见的手、笔等前景遮挡。目标平面外和保护区保持base像素。"
            "若目标范围外仍有需移除的旧字、旧纸或错误纸边，应先修好底图；这个工具不擦除旧物体、移动手势、补阴影或改变机位。"
            "输出仍需观察朝向、文字、透视、光照、边缘和遮挡；未改变current/canonical、镜头绑定或宣告制作项完成。"
            "坐标对应EXIF转正后的可见像素；保持底图尺寸，局部合成不提高源内容的实际细节。"
        ),
        effect=ToolEffect.WRITE, idempotent=False, input_model=ImagePlaneCompositeInput,
        output_model=AssetMutationResponse, handler=composite_plane,
        tags=frozenset({"image", "asset", "local", "perspective", "composite", "text", "edit"}),
    ))

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
                "普通 LIGHTWEIGHT 拼板允许不同对象采用相同视图，按真实图片标注即可。"
                "普通网格的 layout_profile 留空（null），用 columns 指定列数；"
                "LIGHTWEIGHT_SINGLE 只用于单格。"
                "它适合最终参考卡需要像素级分区、固定语义槽、可重复裁切"
                "或统一背景时使用，避免让概率生成器为微小几何偏差反复返修。"
                "它不生成缺失视图、不判断一致性。输出保存为 DRAFT，"
                "Agent 可以明确选用，并按实际疑问决定是否检查。"
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
                *(ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=reference.asset_version_id)
                    for reference in result.version.derivation_references),
            ),
        )

    registry.register(
        ToolSpec(
            id="image.compose_text_plate",
            version=1,
            category=ToolCategory.IMAGE,
            description=(
                "为 SCREEN_CONTENT_PLATE 或已有 PHYSICAL_MASTER PROP 的准确表面内容在本地"
                "确定性排版菜价、金额、姓名、标题或文件条款。文案须保持 "
                "brief.required_visible_text 的词句、数字与顺序；空行、行末标签冒号和尾随装饰横线不要求改 brief，"
                "若同一资产的后续剧情状态需新增姓名等文字，显式提供 content_state.name 和该状态完整的 "
                "required_visible_text：保留 brief 每项固定文字为独立条目并按原顺序排列，新增文字另列条目。"
                "实际渲染逐字匹配该状态；状态只保存在本候选 AssetTextPlate，不修改全局 brief 或旧候选。"
                "已有字版仅新增姓名等文字时，传 base_version_id 与 append_text_blocks："
                "复制这份真实可用 PNG，只画新增块；完整 content_state.required_visible_text 包含原文字和新增文字。"
                "该模式继承实际尺寸、颜色、旧文字和横线，size等旧布局参数不覆盖基图；不要重传 title/exact_lines/text_blocks/lines。"
                "原样保留追加区域之外的像素，支持旧式流式字版；不能擦除或移动原内容，不把摄影当字版。"
                "parent_version_id 仍仅表示来源关系，不会自动复制版式；返回的 derivation_references 指向实际复制的 base。"
                "正文内部标点、标识符、金额小数、正负号、百分号与姓名仍校验。title/exact_lines 是简易流式排版，"
                "exact_lines 的空字符串保留空行。精确版式改用 text_blocks（不要同时给 title/exact_lines）："
                "每块给 text、归一化 content_box=[x,y,width,height]、实际像素 font_size 与对齐，"
                "无隐含边距，越界/放不下会明确报块索引，不自动截字。lines 的归一化 start/end、像素 width、"
                "可选 color 绘制签字线或分隔线，不把下划线长度当剧情文字。不同文案可以分别保存为 TEXT_PLATE，"
                "本 Tool 不调用图像 Provider，输出 PNG DRAFT。物理道具的字版保存为同Asset下 "
                "text_plate.purpose=PHYSICAL_SURFACE_CONTENT 候选，不改变母资产brief/current/canonical，"
                "不冒充摄影母图；使用返回的精确version引用即可，不必临时改成SCREEN_CONTENT_PLATE或另建假载体。"
                "background_color=#00000000 保留透明通道；content_box 明确指定"
                "归一化 x/y/宽/高文字区域，text_align、vertical_align 和描边参数"
                "控制实际排版，layout_description 只记录说明。透明图版可通过"
                " timeline.add_asset_overlay 直接叠加到当前视频，不需要 ComfyUI。"
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
                "为一个输出版本创建图像任务。GENERATE 不带输入图或父版本；"
                "REFERENCE_GENERATE 使用有序参考图且省略 parent_version_id，"
                "可给已有 Asset 创建新候选；显式指定目标 parent_version_id 时"
                "必须用 EDIT，并保留实际参考图。计划 operation 表达制作意图，"
                "实际选择目标父图后按此合同映射为 EDIT；依赖、身份参考、"
                "primary_input 或已有 Asset 不会自动成为 target parent。"
                "request.layout_profile 只描述本次输出；单张脸部、正面、"
                "侧面、背面素材使用 LIGHTWEIGHT_SINGLE，不继承 Asset.brief "
                "中最终参考卡的多区布局。"
                "参考图只使用有序 reference_inputs，其版本字段名是 "
                "asset_version_id；不要传 legacy input_version_ids。"
                "一图生成标准卡时，已登记的布局 guide 必须作为真实参考"
                "传入才能在 Prompt 中声称“图N是布局参考”；没有实际"
                "LAYOUT_GUIDE reference_input 就不执行或冒充官方标准一图路线。"
                "已有图像提供可用身份时，定向修视图或局部状态"
                "优先用 EDIT，并将该图作为 parent 和 identity reference；不用无参考"
                "GENERATE 冒充“保持原人物”。已有图只需裁脸/裁单格时用本地 image.crop；像素级排版改用"
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
