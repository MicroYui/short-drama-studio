"""Shot planning and director-loop tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import ProjectAggregate
from short_drama.production_contracts import (
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupReplaceRequest,
    StoryboardDraftLedgerCompleteRequest,
    StoryboardDraftLedgerSegmentAppendRequest,
    StoryboardDraftMutationResponse,
    StoryboardDraftOutlineCompleteRequest,
    StoryboardDraftOutlineSegmentAppendRequest,
    StoryboardDraftVerificationRequest,
    StoryboardCreateRequest,
    StoryboardMutationResponse,
    StoryboardPatchRequest,
    StoryboardReadResponse,
    StoryboardReplaceRequest,
)
from short_drama.reference_planner import (
    DirectorAdviceRequest,
    DirectorAdviceResponse,
    DirectorLoopResponse,
    DirectorRepairDecisionRequest,
    KeyframeBriefCompileRequest,
    KeyframeBriefCompileResponse,
    ReferencePlanPreviewRequest,
    ReferencePlanPreviewResponse,
    ShotReferencePlanUpdateRequest,
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


class ShotToolService(Protocol):
    def begin_storyboard_draft(
        self,
        project_id: str,
        request: StoryboardDraftBeginRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def append_storyboard_group(
        self,
        project_id: str,
        request: StoryboardDraftAppendRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def append_storyboard_ledger_segment(
        self,
        project_id: str,
        request: StoryboardDraftLedgerSegmentAppendRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def complete_storyboard_ledger(
        self,
        project_id: str,
        request: StoryboardDraftLedgerCompleteRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def append_storyboard_outline_segment(
        self,
        project_id: str,
        request: StoryboardDraftOutlineSegmentAppendRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def complete_storyboard_outline(
        self,
        project_id: str,
        request: StoryboardDraftOutlineCompleteRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def replace_storyboard_draft_group(
        self,
        project_id: str,
        request: StoryboardDraftGroupReplaceRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def record_storyboard_draft_verification(
        self,
        project_id: str,
        request: StoryboardDraftVerificationRequest,
    ) -> StoryboardDraftMutationResponse: ...

    def finalize_storyboard(
        self,
        project_id: str,
        request: StoryboardDraftFinalizeRequest,
    ) -> StoryboardMutationResponse: ...

    def create_storyboard(
        self,
        project_id: str,
        request: StoryboardCreateRequest,
    ) -> StoryboardMutationResponse: ...

    def replace_storyboard(
        self,
        project_id: str,
        request: StoryboardReplaceRequest,
    ) -> StoryboardMutationResponse: ...

    def patch_storyboard(
        self,
        project_id: str,
        request: StoryboardPatchRequest,
    ) -> StoryboardMutationResponse: ...

    def get_storyboard(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> StoryboardReadResponse: ...

    def update_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> ProjectAggregate: ...

    def preview_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ReferencePlanPreviewRequest,
    ) -> ReferencePlanPreviewResponse: ...

    def compile_keyframe_brief(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeBriefCompileRequest,
    ) -> KeyframeBriefCompileResponse: ...

    def get_director_advice(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorAdviceResponse: ...

    def decide_director_repair(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorRepairDecisionRequest,
    ) -> ProjectAggregate: ...

    def run_director_loop(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorLoopResponse: ...


class ShotReferencePlanUpdateInput(ToolModel):
    project_id: str
    shot_id: str
    request: ShotReferencePlanUpdateRequest


class ShotReferencePlanPreviewInput(ToolModel):
    project_id: str
    shot_id: str
    request: ReferencePlanPreviewRequest


class ShotKeyframeBriefCompileInput(ToolModel):
    project_id: str
    shot_id: str
    request: KeyframeBriefCompileRequest


class ShotDirectorAdviceInput(ToolModel):
    project_id: str
    shot_id: str
    request: DirectorAdviceRequest


class ShotDirectorRepairInput(ToolModel):
    project_id: str
    shot_id: str
    request: DirectorRepairDecisionRequest


class StoryboardCreateInput(ToolModel):
    project_id: str
    request: StoryboardCreateRequest


class StoryboardDraftBeginInput(ToolModel):
    project_id: str
    request: StoryboardDraftBeginRequest


class StoryboardDraftAppendInput(ToolModel):
    project_id: str
    request: StoryboardDraftAppendRequest


class StoryboardDraftLedgerSegmentAppendInput(ToolModel):
    project_id: str
    request: StoryboardDraftLedgerSegmentAppendRequest


class StoryboardDraftLedgerCompleteInput(ToolModel):
    project_id: str
    request: StoryboardDraftLedgerCompleteRequest


class StoryboardDraftOutlineSegmentAppendInput(ToolModel):
    project_id: str
    request: StoryboardDraftOutlineSegmentAppendRequest


class StoryboardDraftOutlineCompleteInput(ToolModel):
    project_id: str
    request: StoryboardDraftOutlineCompleteRequest


class StoryboardDraftGroupReplaceInput(ToolModel):
    project_id: str
    request: StoryboardDraftGroupReplaceRequest


class StoryboardDraftVerificationInput(ToolModel):
    project_id: str
    request: StoryboardDraftVerificationRequest


class StoryboardDraftFinalizeInput(ToolModel):
    project_id: str
    request: StoryboardDraftFinalizeRequest


class StoryboardReplaceInput(ToolModel):
    project_id: str
    request: StoryboardReplaceRequest


class StoryboardPatchInput(ToolModel):
    project_id: str
    request: StoryboardPatchRequest


class StoryboardGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


def _shot_ref(project_id: str, shot_id: str, revision: int | None = None) -> ArtifactRef:
    return ArtifactRef(
        project_id=project_id,
        artifact_type="shot",
        artifact_id=shot_id,
        version=revision,
    )


def _storyboard_refs(
    project_id: str,
    result: StoryboardReadResponse,
) -> tuple[ArtifactRef, ...]:
    refs = [
        ArtifactRef(
            project_id=project_id,
            artifact_type="storyboard",
            artifact_id=result.document.id,
            version=result.version.version,
        )
    ]
    refs.extend(
        _shot_ref(project_id, shot.id, shot.revision)
        for shot in result.storyboard.shots
    )
    refs.extend(
        ArtifactRef(
            project_id=project_id,
            artifact_type="clip_group",
            artifact_id=group.id,
            version=result.version.version,
        )
        for group in result.storyboard.clip_groups
    )
    return tuple(refs)


def _storyboard_draft_refs(
    project_id: str,
    result: StoryboardDraftMutationResponse,
) -> tuple[ArtifactRef, ...]:
    progress = result.progress
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="storyboard_draft",
            artifact_id=progress.draft_ref.artifact_id,
            version=progress.draft_ref.version,
        ),
    )


def register_tools(registry: ToolRegistry, service: ShotToolService) -> None:
    def begin_storyboard_draft(
        request: StoryboardDraftBeginInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.begin_storyboard_draft(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.begin_storyboard_draft",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "为已确认的剧本和 ProductionSpec 建立可恢复的分镜草稿。"
                "输入只包含源版本锁定、标题和场次连续性等轻量 seed；场次标题、"
                "摘要和台词归属会从当前 ScriptVersion 确定性回填。此时不要"
                "一次输出整集时长账本或 outline；后续在同一 WorkOrder 内按语义"
                "连续范围增量持久化，无固定片段数或次数。不会生成视频。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftBeginInput,
            output_model=StoryboardDraftMutationResponse,
            handler=begin_storyboard_draft,
            tags=frozenset(
                {"storyboard", "draft", "scene-node", "incremental", "handoff"}
            ),
        )
    )

    def append_storyboard_ledger_segment(
        request: StoryboardDraftLedgerSegmentAppendInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.append_storyboard_ledger_segment(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.append_storyboard_ledger_segment",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "把已经稳定的一段私有时长账本追加到分镜草稿。每段按剧本"
                "的语义连续范围选择，按对白与可见动作事实实算时长；不设"
                "固定秒数、条目数、片段数或调用次数。片段边界只是可恢复的"
                "运行检查点，不是 Scene、ClipGroup、Shot 或 ComfyUI 节点边界。"
                "completes_scene 默认为 false；只有一个场次最后的 timing entry "
                "显式设为 true，才表示该场次完整并把游标推进到下一场。"
                "在封账前，Agent 还要从全场连续性检查 allocations 能否无遗漏、"
                "不重叠地完整分区为各自精确 4–15 秒整数的 ClipGroup。如果不能，"
                "可基于真实镜头需要调整可分割的动作、反应或停顿预留，但不得修改"
                "台词内容，也不得用目标总时长倒推分配。"
                "同一 WorkOrder 可连续追加多段。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftLedgerSegmentAppendInput,
            output_model=StoryboardDraftMutationResponse,
            handler=append_storyboard_ledger_segment,
            tags=frozenset(
                {"storyboard", "draft", "timing-ledger", "incremental"}
            ),
        )
    )

    def complete_storyboard_ledger(
        request: StoryboardDraftLedgerCompleteInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.complete_storyboard_ledger(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.complete_storyboard_ledger",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "在时长账本已完整覆盖源剧本后封存账本，转入 ClipGroup outline"
                " 规划阶段。这是确定性阶段转换，不是用户审批门、Critic 循环或"
                "固定调用次数限制。封账前必须确认每场 allocations 能按连续顺序"
                "完整分区为精确 4–15 秒整数组；只能依据可分割的动作、反应和停顿"
                "需要调整预留，不改台词，不用目标总时长倒推。未覆盖的源内容"
                "不会被默认补齐。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftLedgerCompleteInput,
            output_model=StoryboardDraftMutationResponse,
            handler=complete_storyboard_ledger,
            tags=frozenset(
                {"storyboard", "draft", "timing-ledger", "phase-transition"}
            ),
        )
    )

    def append_storyboard_outline_segment(
        request: StoryboardDraftOutlineSegmentAppendInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.append_storyboard_outline_segment(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.append_storyboard_outline_segment",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "基于已封存的完整时长账本，按账本顺序追加一段已稳定的"
                "ClipGroup outline。Agent 先保持整集语义与连续性，再自主选择可恢复"
                "的持久化边界；不设固定片段数、ClipGroup 数或调用次数。"
                "每个 ClipGroup 是未来 H3 生成单元，时长必须是 4–15 秒整数。"
                "片段边界不投影为用户可见节点。同一 WorkOrder 可连续追加多段。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftOutlineSegmentAppendInput,
            output_model=StoryboardDraftMutationResponse,
            handler=append_storyboard_outline_segment,
            tags=frozenset(
                {"storyboard", "draft", "clip-group-outline", "incremental"}
            ),
        )
    )

    def complete_storyboard_outline(
        request: StoryboardDraftOutlineCompleteInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.complete_storyboard_outline(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.complete_storyboard_outline",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "在 outline 完整消费已封存账本后，写入整体 TimingAudit 并封存"
                "ClipGroup 边界，转入逐组详细分镜阶段。这是确定性编译阶段"
                "转换，不是用户审批门或 Critic 循环；不会强制固定分组数。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftOutlineCompleteInput,
            output_model=StoryboardDraftMutationResponse,
            handler=complete_storyboard_outline,
            tags=frozenset(
                {"storyboard", "draft", "clip-group-outline", "phase-transition"}
            ),
        )
    )

    def append_storyboard_group(
        request: StoryboardDraftAppendInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.append_storyboard_group(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.append_storyboard_group",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "把一个语义 WorkOrder 已完成的 ClipGroup 和其中多个可编辑 Shot 增量写入"
                "分镜草稿。WorkOrder 可以小于或大于场次，也可以连续提交多个 ClipGroup；"
                "WorkOrder 仍按内容语义规划；只在已封存完整 outline 后，单用户"
                "本地流程才按 outline 顺序提交下一个"
                "计划组。ClipGroup 保持 H3 要求的 4–15 秒整数时长；内部 Shot "
                "可以使用小数秒，但 Shot 时长之和必须等于组时长。来源映射和"
                "时长用结构校验；continuity 与 visible_changes 是自然语言事实，"
                "由语义 Verifier 判断是否一致，不要为通过字符串逐字相等而复制措辞。"
                "成功组会立即形成可追踪"
                "进度，其他组无需重写。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftAppendInput,
            output_model=StoryboardDraftMutationResponse,
            handler=append_storyboard_group,
            tags=frozenset(
                {"storyboard", "draft", "shot", "clip-group", "incremental"}
            ),
        )
    )

    def replace_storyboard_draft_group(
        request: StoryboardDraftGroupReplaceInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.replace_storyboard_draft_group(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.replace_storyboard_draft_group",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "在 finalize 前只替换已经提交的一个计划 ClipGroup。计划中的组 ID、"
                "场次、场内顺序、来源台词和实算时长保持不变，Shot、镜头语言、表演"
                "细节可定向修订；不能借此修改时长账本、组边界或计划连续性事实。"
                "自然语言连续性可以改写，其事实是否保持由语义 Verifier 判断；相关变化"
                "需要重新建立完整草稿。任何旧 Verifier 结论会自动失效。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftGroupReplaceInput,
            output_model=StoryboardDraftMutationResponse,
            handler=replace_storyboard_draft_group,
            tags=frozenset(
                {"storyboard", "draft", "clip-group", "partial-edit"}
            ),
        )
    )

    def record_storyboard_draft_verification(
        request: StoryboardDraftVerificationInput,
    ) -> ToolExecution[StoryboardDraftMutationResponse]:
        result = service.record_storyboard_draft_verification(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.record_storyboard_draft_verification",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "把 Verifier 对当前完整草稿的 READY 或 NEEDS_REVISION 语义报告写入"
                "版本化草稿，供 Agent 决定继续定向修订还是完成装配。这不是用户审批"
                "门，也不会生成媒体。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftVerificationInput,
            output_model=StoryboardDraftMutationResponse,
            handler=record_storyboard_draft_verification,
            tags=frozenset(
                {"storyboard", "draft", "verification", "handoff"}
            ),
        )
    )

    def finalize_storyboard(
        request: StoryboardDraftFinalizeInput,
    ) -> ToolExecution[StoryboardMutationResponse]:
        result = service.finalize_storyboard(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.finalize_storyboard",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "在完整计划中的每个 ClipGroup 都已提交后进行确定性装配和结构校验，"
                "产出唯一的版本化 canonical Storyboard，并绑定场次内的 ClipGroup 与"
                " Shot。装配要求私有时长账本、Shot、ClipGroup、outline 与 TimingAudit"
                "秒数一致且无超载；自然语言连续性与可见变化由 Verifier 做语义"
                "判断，不做逐字等值匹配。Verifier 报告供 Agent 语义决策，不作为用户审批门；"
                "不会调用 H3 或生成视频。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftFinalizeInput,
            output_model=StoryboardMutationResponse,
            handler=finalize_storyboard,
            tags=frozenset(
                {"storyboard", "finalize", "canonical", "clip-group", "handoff"}
            ),
        )
    )

    def create_storyboard(
        request: StoryboardCreateInput,
    ) -> ToolExecution[StoryboardMutationResponse]:
        result = service.create_storyboard(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.create_storyboard",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "兼容旧调用或导入已经完整存在的结构化分镜，一次写入镜头、"
                "ClipGroup 和连续性信息；不会生成视频。新的 Agent 自主制片"
                "默认使用轻量 begin → 增量账本 → 增量 outline → 逐组详细分镜 "
                "→ finalize 的可恢复链路。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardCreateInput,
            output_model=StoryboardMutationResponse,
            handler=create_storyboard,
            tags=frozenset(
                {"storyboard", "shot", "clip-group", "compatibility", "import"}
            ),
        )
    )

    def replace_storyboard(
        request: StoryboardReplaceInput,
    ) -> ToolExecution[StoryboardMutationResponse]:
        result = service.replace_storyboard(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.replace_storyboard",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "当分镜结构需要整体重构时，基于明确的当前版本写入一份新版本。"
                "旧分镜与旧镜头引用继续可读，供 Agent 追溯和比较。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardReplaceInput,
            output_model=StoryboardMutationResponse,
            handler=replace_storyboard,
            tags=frozenset({"storyboard", "replace", "versioned"}),
        )
    )

    def patch_storyboard(
        request: StoryboardPatchInput,
    ) -> ToolExecution[StoryboardMutationResponse]:
        result = service.patch_storyboard(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.patch_storyboard",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "只修改 Agent 明确选中的镜头或 ClipGroup，并通过 expected_revision"
                " 保护其他已确认镜头；未修改镜头保持原修订号。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardPatchInput,
            output_model=StoryboardMutationResponse,
            handler=patch_storyboard,
            tags=frozenset({"storyboard", "partial-edit", "versioned"}),
        )
    )

    def get_storyboard(
        request: StoryboardGetInput,
    ) -> ToolExecution[StoryboardReadResponse]:
        result = service.get_storyboard(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.get_storyboard",
            version=1,
            category=ToolCategory.SHOT,
            description=(
                "读取当前或指定历史版本的完整分镜及 ClipGroup，供局部修改、资产规划"
                "和生成计划编译使用。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=StoryboardGetInput,
            output_model=StoryboardReadResponse,
            handler=get_storyboard,
            tags=frozenset({"storyboard", "read", "clip-group"}),
        )
    )

    def update_plan(
        request: ShotReferencePlanUpdateInput,
    ) -> ToolExecution[ProjectAggregate]:
        result = service.update_shot_reference_plan(
            request.project_id,
            request.shot_id,
            request.request,
        )
        shot = next(item for item in result.document.shots if item.id == request.shot_id)
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, shot.id, shot.revision),),
        )

    registry.register(
        ToolSpec(
            id="shot.update_reference_plan",
            version=1,
            category=ToolCategory.SHOT,
            description="Update one shot's reference, action, and interaction plan.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=ShotReferencePlanUpdateInput,
            output_model=ProjectAggregate,
            handler=update_plan,
            tags=frozenset({"shot", "reference-plan", "partial-edit"}),
        )
    )

    def preview_plan(
        request: ShotReferencePlanPreviewInput,
    ) -> ToolExecution[ReferencePlanPreviewResponse]:
        result = service.preview_shot_reference_plan(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
            warnings=tuple(result.warnings),
        )

    registry.register(
        ToolSpec(
            id="shot.preview_reference_plan",
            version=1,
            category=ToolCategory.SHOT,
            description="Preview resolved shot references without mutating the project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ShotReferencePlanPreviewInput,
            output_model=ReferencePlanPreviewResponse,
            handler=preview_plan,
            tags=frozenset({"shot", "reference-plan", "preview"}),
        )
    )

    def compile_keyframe(
        request: ShotKeyframeBriefCompileInput,
    ) -> ToolExecution[KeyframeBriefCompileResponse]:
        result = service.compile_keyframe_brief(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
            warnings=tuple(result.warnings),
        )

    registry.register(
        ToolSpec(
            id="shot.compile_keyframe_brief",
            version=1,
            category=ToolCategory.SHOT,
            description="Compile a topology, start, or end keyframe brief for one shot.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ShotKeyframeBriefCompileInput,
            output_model=KeyframeBriefCompileResponse,
            handler=compile_keyframe,
            tags=frozenset({"shot", "keyframe", "planning"}),
        )
    )

    def director_advice(
        request: ShotDirectorAdviceInput,
    ) -> ToolExecution[DirectorAdviceResponse]:
        result = service.get_director_advice(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.director_advice",
            version=1,
            category=ToolCategory.SHOT,
            description="Ask the active format's director role for shot-specific advice.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=ShotDirectorAdviceInput,
            output_model=DirectorAdviceResponse,
            handler=director_advice,
            tags=frozenset({"shot", "director", "advice"}),
        )
    )

    def apply_director_repair(
        request: ShotDirectorRepairInput,
    ) -> ToolExecution[ProjectAggregate]:
        result = service.decide_director_repair(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.apply_director_repair",
            version=1,
            category=ToolCategory.SHOT,
            description="Apply or record a typed director repair decision for one shot.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ShotDirectorRepairInput,
            output_model=ProjectAggregate,
            handler=apply_director_repair,
            tags=frozenset({"shot", "director", "repair"}),
        )
    )

    def director_loop(
        request: ShotDirectorAdviceInput,
    ) -> ToolExecution[DirectorLoopResponse]:
        result = service.run_director_loop(
            request.project_id,
            request.shot_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(_shot_ref(request.project_id, request.shot_id),),
        )

    registry.register(
        ToolSpec(
            id="shot.run_director_loop",
            version=1,
            category=ToolCategory.SHOT,
            description="Run director advice and apply its proposed repair when available.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=ShotDirectorAdviceInput,
            output_model=DirectorLoopResponse,
            handler=director_loop,
            tags=frozenset({"shot", "director", "repair"}),
        )
    )


__all__ = [
    "ShotDirectorAdviceInput",
    "ShotDirectorRepairInput",
    "ShotKeyframeBriefCompileInput",
    "ShotReferencePlanPreviewInput",
    "ShotReferencePlanUpdateInput",
    "StoryboardCreateInput",
    "StoryboardDraftAppendInput",
    "StoryboardDraftBeginInput",
    "StoryboardDraftFinalizeInput",
    "StoryboardDraftGroupReplaceInput",
    "StoryboardDraftLedgerCompleteInput",
    "StoryboardDraftLedgerSegmentAppendInput",
    "StoryboardDraftOutlineCompleteInput",
    "StoryboardDraftOutlineSegmentAppendInput",
    "StoryboardDraftVerificationInput",
    "StoryboardGetInput",
    "StoryboardPatchInput",
    "StoryboardReplaceInput",
    "register_tools",
]
