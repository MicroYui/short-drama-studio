"""Shot planning and director-loop tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import ProjectAggregate, ReferencePlanEntry
from short_drama.production_contracts import (
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupReplaceRequest,
    StoryboardDraftGroupsAppendRequest,
    StoryboardDraftGroupsMutationResponse,
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
from short_drama.tools.artifact_views import (
    StoryboardCompactReadResponse,
    StoryboardMutationSummaryResponse,
    compact_storyboard_read,
    storyboard_mutation_summary,
)
from short_drama.tools.registry import ToolRegistry
from short_drama.storyboard_receipts import storyboard_write_context


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

    def append_storyboard_groups(
        self,
        project_id: str,
        request: StoryboardDraftGroupsAppendRequest,
    ) -> StoryboardDraftGroupsMutationResponse: ...

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
        *,
        current_content_edit: bool = False,
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


class ShotReferencePlanMutationResponse(ToolModel):
    project_id: str
    shot_id: str
    shot_revision: int
    reference_plan: ReferencePlanEntry


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


class StoryboardDraftGroupsAppendInput(ToolModel):
    project_id: str
    request: StoryboardDraftGroupsAppendRequest


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
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_document",
            artifact_id=progress.draft_ref.artifact_id,
            version=progress.draft_ref.version,
        ),
    )


def _storyboard_draft_group_segment_refs(
    project_id: str,
    result: StoryboardDraftGroupsMutationResponse,
) -> tuple[ArtifactRef, ...]:
    return tuple(
        ArtifactRef(
            project_id=project_id,
            artifact_type=artifact_type,
            artifact_id=item.draft_ref.artifact_id,
            version=item.draft_ref.version,
        )
        for item in result.checkpoints
        for artifact_type in ("storyboard_draft", "production_document")
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
            description='建立可逐组写入的分镜。seed 只需当前剧本、Bible、制作说明与场次 ID；正文由服务补全。随后直接 append_storyboard_group，页面会逐组显示。时长账本、总纲和独立审阅都是可选帮助，不是前置步骤。',
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
            description='可选：记录一段动作和对白的时间估计。用于复杂节奏的思考与回读，不限制后续镜头写入，也不要求估计已适配 Provider 时长。普通分镜可以直接逐组写入。',
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
            description='可选：完成当前时间笔记并转到总纲整理。只有实际需要独立时间笔记时才使用；不必先做此步骤才能写镜头。',
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
            description='可选：记录一段分镜总纲，保存动作和衔接想法。组时长是创作估计，实际 Provider 单元由后续路线编排确定。',
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
            description='可选：保存整体时间观察。检查报告用于判断节奏，不作为分镜、资产或视频制作的审批。',
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
            description='立即写入一组完整镜头并在产品中显示。包含场次、镜头设计、逐时段描述、对白与实际媒体引用。正文引用用 text_anchor.exact 复制所属 field 的原文，重复时补紧邻 prefix/suffix，无需计数。直接写入即可，不需要时长账本、预先确定全部组或检查通过。组时长应等于实际镜头时长之和；Provider 秒数限制在生成时处理。只返回当前进度及精确引用，后续按需要读取。',
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

    def append_storyboard_groups(
        request: StoryboardDraftGroupsAppendInput,
    ) -> ToolExecution[StoryboardDraftGroupsMutationResponse]:
        result = service.append_storyboard_groups(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_storyboard_draft_group_segment_refs(
                request.project_id,
                result,
            ),
        )

    registry.register(
        ToolSpec(
            id="shot.append_storyboard_groups",
            version=1,
            category=ToolCategory.SHOT,
            description='一次提交当前已完成的相邻镜头组，每组独立持久化。适合一起设计的一段表演，不需要先规划全片组数或时间账本。未完成的后续内容可继续逐组追加。',
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftGroupsAppendInput,
            output_model=StoryboardDraftGroupsMutationResponse,
            handler=append_storyboard_groups,
            tags=frozenset(
                {
                    "storyboard",
                    "draft",
                    "shot",
                    "clip-group",
                    "incremental",
                    "segment",
                }
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
            description='定向替换当前分镜中的一组，保留该组 ID 和其他组。允许修改镜头内容、时长和构图，不要求沿用旧时间估计。base_version_id 保护用户已经保存的更改；先读取相关当前内容再修改。旧检查观察会失效，Agent 自行决定是否需要重新检查。',
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
    ) -> ToolExecution[StoryboardMutationSummaryResponse]:
        result = service.finalize_storyboard(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=storyboard_mutation_summary(result),
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.finalize_storyboard",
            version=3,
            category=ToolCategory.SHOT,
            description='把当前镜头组装配为完整分镜，检查实际镜头、对白覆盖和引用一致性，不要求时长预算、账本、总纲或检查通过。不会生成媒体。成功返回分镜身份、实际逐镜设计时长与组成员目录；不含分镜全文，不代表动态或声音验收。视频由后续实际生成工具创建。',
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardDraftFinalizeInput,
            output_model=StoryboardMutationSummaryResponse,
            handler=finalize_storyboard,
            tags=frozenset(
                {"storyboard", "finalize", "canonical", "clip-group", "handoff"}
            ),
        )
    )

    def create_storyboard(
        request: StoryboardCreateInput,
    ) -> ToolExecution[StoryboardMutationSummaryResponse]:
        result = service.create_storyboard(request.project_id, request.request)
        return ToolExecution(
            output=storyboard_mutation_summary(result),
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.create_storyboard",
            version=2,
            category=ToolCategory.SHOT,
            description='一次写入已经完成的结构化分镜及镜头组，不会生成视频。也可以 begin_storyboard_draft 后逐组写入再 finalize，使长任务内容及时显示并持久化；两种方式都不需要独立审批。',
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardCreateInput,
            output_model=StoryboardMutationSummaryResponse,
            handler=create_storyboard,
            tags=frozenset(
                {"storyboard", "shot", "clip-group", "compatibility", "import"}
            ),
        )
    )

    def replace_storyboard(
        request: StoryboardReplaceInput,
    ) -> ToolExecution[StoryboardMutationSummaryResponse]:
        result = service.replace_storyboard(request.project_id, request.request)
        return ToolExecution(
            output=storyboard_mutation_summary(result),
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.replace_storyboard",
            version=2,
            category=ToolCategory.SHOT,
            description=(
                "整体重写全片分镜；局部拆镜、插入、删除或重分组使用shot.patch_storyboard，无需重输全片。"
                "确需整体重构时，基于明确的当前版本写入一份新版本。"
                "旧分镜与旧镜头引用继续可读，供 Agent 追溯和比较。"
                "成功时只返回新版本身份与结构摘要。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardReplaceInput,
            output_model=StoryboardMutationSummaryResponse,
            handler=replace_storyboard,
            tags=frozenset({"storyboard", "replace", "versioned"}),
        )
    )

    def patch_storyboard(
        request: StoryboardPatchInput,
    ) -> ToolExecution[StoryboardMutationSummaryResponse]:
        result = service.patch_storyboard(request.project_id, request.request, current_content_edit=True)
        summary = storyboard_mutation_summary(result)
        summary.write_context = storyboard_write_context(result)
        return ToolExecution(
            output=summary,
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.patch_storyboard",
            version=2,
            category=ToolCategory.SHOT,
            description=(
                "局部改镜头、拆镜/合并/插入/删除、重分组；只传选中内容，不重输全片。"
                "正文引用可用 text_anchor.exact 加必要 prefix/suffix 唯一定位；原人工范围保留。"
                "replacements改正文；shot_splices以原shot_ids替换为局部shots，空shots删除，"
                "空shot_ids用after_shot_id插入。原组自动更新成员/时长；"
                "需改变组边界时用clip_group_splices只替换相关旧组。镜号/新修订号由代码计算。"
                "base_version_id指向已读分镜；相关内容或插入边界已变时报冲突，无关人工修改保留。"
                "上游剧本正在修改时也可保存局部内容，并保留实际来源；生成前再同步所用输入。"
                "核对新剧本或制作说明后，可只更新对应的 script_version_id、bible_version_id、"
                "production_spec_document_id/version_id 和 scene_nodes；不必重写全片镜头。"
                "成功响应不重放完整 Storyboard；按需显式读取新版本。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=StoryboardPatchInput,
            output_model=StoryboardMutationSummaryResponse,
            handler=patch_storyboard,
            tags=frozenset({"storyboard", "partial-edit", "versioned"}),
        )
    )

    def get_storyboard(
        request: StoryboardGetInput,
    ) -> ToolExecution[StoryboardCompactReadResponse]:
        result = service.get_storyboard(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=compact_storyboard_read(result),
            artifact_refs=_storyboard_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="shot.get_storyboard",
            version=2,
            category=ToolCategory.SHOT,
            description=(
                "读取当前或指定历史版本的完整分镜及 ClipGroup，供局部修改、资产规划"
                "和生成计划编译使用。所选 Storyboard 正文只出现"
                "一次，不回传 document.versions 历史或 version.payload 副本。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=StoryboardGetInput,
            output_model=StoryboardCompactReadResponse,
            handler=get_storyboard,
            tags=frozenset({"storyboard", "read", "clip-group"}),
        )
    )

    def update_plan(
        request: ShotReferencePlanUpdateInput,
    ) -> ToolExecution[ShotReferencePlanMutationResponse]:
        result = service.update_shot_reference_plan(
            request.project_id,
            request.shot_id,
            request.request,
        )
        shot = next(item for item in result.document.shots if item.id == request.shot_id)
        plan = next(item for item in result.document.reference_plan
                    if item.shot_id == request.shot_id)
        return ToolExecution(
            output=ShotReferencePlanMutationResponse(
                project_id=request.project_id,
                shot_id=shot.id,
                shot_revision=shot.revision,
                reference_plan=plan,
            ),
            artifact_refs=(_shot_ref(request.project_id, shot.id, shot.revision),),
        )

    registry.register(
        ToolSpec(
            id="shot.update_reference_plan",
            version=2,
            category=ToolCategory.SHOT,
            description=(
                "Update one shot's reference, action, and interaction plan. "
                "Return the exact saved reference_plan and shot revision, without "
                "a full project snapshot."
            ),
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=ShotReferencePlanUpdateInput,
            output_model=ShotReferencePlanMutationResponse,
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
    "StoryboardDraftGroupsAppendInput",
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
