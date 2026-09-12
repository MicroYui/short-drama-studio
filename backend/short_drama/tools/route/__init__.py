"""MiniMax-H3 route planning, execution, review, and recovery adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.h3_group_operation import H3GroupOperationRequest, H3GroupOperationResponse

from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3NotSubmittedResolutionRequest,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProductionRouteExecuteRequest,
    H3ProductionRouteExecuteResponse,
    H3ProductionRoutePlanListResponse,
    H3ProductionRoutePlanRequest,
    H3ProductionRoutePlanResponse,
    H3ProviderRun,
    H3ProviderRunIndexResponse,
    H3ReconcileRequest,
    H3ReconciliationCandidatesResponse,
    H3RouteAssetPreparationUpdateRequest,
    H3RouteAssetPreparationUpdateResponse,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.language_models import (
    H3GenerationUnitContextRequest,
    H3GenerationUnitContextResponse,
    LanguagePromptCompileRequest,
    LanguagePromptCompileResponse,
)
from short_drama.quality_learning import H3CapabilityProfileResponse
from short_drama.production_contracts import (
    GenerationPlanCompileRequest,
    GenerationPlanMutationResponse,
    GenerationPlanReadResponse,
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


class RouteToolService(Protocol):
    def prepare_h3_group(self, project_id: str, request: H3GroupOperationRequest) -> H3GroupOperationResponse: ...

    def get_h3_group_operation(self, project_id: str, operation_id: str) -> H3GroupOperationResponse: ...

    def get_h3_production_route_plan(
        self, project_id: str, plan_id: str,
    ) -> H3ProductionRoutePlanResponse: ...

    def get_h3_generation_unit_context(
        self, project_id: str, request: H3GenerationUnitContextRequest,
    ) -> H3GenerationUnitContextResponse: ...

    def compile_generation_plan(
        self,
        project_id: str,
        request: GenerationPlanCompileRequest,
    ) -> GenerationPlanMutationResponse: ...

    def get_generation_plan(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> GenerationPlanReadResponse: ...

    def get_h3_capability_profile(
        self,
        project_id: str,
    ) -> H3CapabilityProfileResponse: ...

    def compile_h3_prompt_with_language_model(
        self,
        project_id: str,
        request: LanguagePromptCompileRequest,
    ) -> LanguagePromptCompileResponse: ...

    def preview_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3CompilePreviewResponse: ...

    def enqueue_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3ProbeResponse: ...

    def list_h3_provider_run_index(self, project_id: str) -> H3ProviderRunIndexResponse: ...

    def get_h3_provider_run(self, task_id: str) -> H3ProviderRun: ...

    def list_h3_reconciliation_candidates_for_task(
        self,
        project_id: str,
        task_id: str,
    ) -> H3ReconciliationCandidatesResponse: ...

    def resolve_h3_submission(
        self,
        task_id: str,
        provider_task_id: str,
    ) -> H3TaskActionResponse: ...

    def resolve_h3_not_submitted(
        self,
        task_id: str,
        request: H3NotSubmittedResolutionRequest,
    ) -> H3TaskActionResponse: ...

    def compile_h3_production_route_plan(
        self,
        project_id: str,
        request: H3ProductionRoutePlanRequest,
    ) -> H3ProductionRoutePlanResponse: ...

    def list_h3_production_route_plans(
        self,
        project_id: str,
    ) -> H3ProductionRoutePlanListResponse: ...

    def update_h3_route_asset_preparation(
        self,
        project_id: str,
        plan_id: str,
        preparation_id: str,
        request: H3RouteAssetPreparationUpdateRequest,
    ) -> H3RouteAssetPreparationUpdateResponse: ...

    def execute_h3_production_route_plan(
        self,
        project_id: str,
        plan_id: str,
        request: H3ProductionRouteExecuteRequest,
    ) -> H3ProductionRouteExecuteResponse: ...

    def review_h3_take(
        self,
        take_id: str,
        request: H3TakeReviewRequest,
    ) -> H3TakeReviewResponse: ...


class RouteProjectInput(ToolModel):
    project_id: str


class RoutePlanGetInput(RouteProjectInput):
    plan_id: str


class RouteReconciliationInput(ToolModel):
    project_id: str
    task_id: str


class RoutePromptCompileInput(ToolModel):
    project_id: str
    request: LanguagePromptCompileRequest


class GenerationUnitContextInput(ToolModel):
    project_id: str
    request: H3GenerationUnitContextRequest


class RouteProbeInput(ToolModel):
    project_id: str
    request: H3ProbeRequest


class RouteTaskInput(ToolModel):
    task_id: str


class RouteResolveSubmissionInput(ToolModel):
    task_id: str
    request: H3ReconcileRequest


class RouteResolveNotSubmittedInput(ToolModel):
    task_id: str
    request: H3NotSubmittedResolutionRequest


class RoutePlanInput(ToolModel):
    project_id: str
    request: H3ProductionRoutePlanRequest


class RouteAssetPreparationUpdateInput(ToolModel):
    project_id: str
    plan_id: str
    preparation_id: str
    request: H3RouteAssetPreparationUpdateRequest


class RouteExecuteInput(ToolModel):
    project_id: str
    plan_id: str
    request: H3ProductionRouteExecuteRequest


class RouteReviewTakeInput(ToolModel):
    take_id: str
    request: H3TakeReviewRequest


class H3GroupOperationInput(ToolModel):
    project_id: str
    request: H3GroupOperationRequest


class H3GroupOperationGetInput(ToolModel):
    project_id: str
    operation_id: str


class GenerationPlanCompileInput(ToolModel):
    project_id: str
    request: GenerationPlanCompileRequest


class GenerationPlanGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


def _generation_plan_refs(
    project_id: str,
    result: GenerationPlanReadResponse,
) -> tuple[ArtifactRef, ...]:
    refs = [
        ArtifactRef(
            project_id=project_id,
            artifact_type="generation_plan",
            artifact_id=result.document.id,
            version=result.version.version,
        )
    ]
    refs.extend(
        ArtifactRef(
            project_id=project_id,
            artifact_type="generation_unit",
            artifact_id=unit.id,
            version=result.version.version,
        )
        for unit in result.generation_plan.units
    )
    return tuple(refs)


def register_tools(registry: ToolRegistry, service: RouteToolService) -> None:
    def group_operation_refs(result: H3GroupOperationResponse) -> tuple[ArtifactRef, ...]:
        refs = [ArtifactRef(project_id=result.project_id, artifact_type="h3_group_operation", artifact_id=result.operation_id)]
        for kind, identifier, version in (
            ("generation_plan", result.generation_plan_document_id, result.generation_plan_version),
            ("generation_unit", result.generation_unit_id, result.generation_plan_version),
            ("h3_route_plan", result.route_plan_id, result.route_plan_state_revision),
        ):
            if identifier is not None:
                refs.append(ArtifactRef(project_id=result.project_id, artifact_type=kind, artifact_id=identifier, version=version))
        refs.extend(ArtifactRef(project_id=result.project_id, artifact_type="generation_task", artifact_id=task_id) for task_id in result.task_ids)
        return tuple(refs)

    def prepare_group(request: H3GroupOperationInput) -> ToolExecution[H3GroupOperationResponse]:
        result = service.prepare_h3_group(request.project_id, request.request)
        refs = group_operation_refs(result)
        return ToolExecution(output=result, artifact_refs=refs, warnings=tuple(result.warnings),
            receipt=ToolWriteReceipt(
                artifact_refs=list(refs), current_state=result.model_dump(mode="json")))

    registry.register(ToolSpec(
        id="route.prepare_h3_group", version=1, category=ToolCategory.ROUTE,
        description=("Prepare one current shot/group through current plan, selected inputs, one H3 prompt compilation and saved route. "
            "Use shot_id or clip_group_id; no manual plan IDs or prompt transfer. Defaults to preparation only; enqueue=true "
            "explicitly queues real H3 generation. Repeat/resume by operation_id reuses completed prompt/route/tasks. "
            "When shot_id belongs to an existing ClipGroup, preparation covers that whole group; "
            "a subset-only work order must hand off to the group owner instead of changing its scope. "
            "Existing selected bindings determine media unless an ordered media_inputs choice is supplied. "
            "This preserves the chosen input strategy; it does not evaluate whether identity cards alone "
            "can establish the planned space/action. Mark staged-frame/sheet references COMPOSITION/LAYOUT_GUIDE "
            "in current shot cues or media_inputs. These are soft anchors in REFERENCE mode. "
            "Choose FRAME_FIRST explicitly with a complete prepared first_frame image when a hard start "
            "is needed; hard frame roles cannot mix with reference roles. Read get_generation_unit_context "
            "or use image/video tools to prepare an appropriate frame when needed, without a mandatory extra LM step. "
            "For rework identify source_take_id/source_repair_id; this creates a whole affected-group candidate, never video inpainting. "
            "Blocked returns preserve exact phase and completed refs; retry_failed_step requests one retry."),
        effect=ToolEffect.EXTERNAL, idempotent=True, input_model=H3GroupOperationInput,
        output_model=H3GroupOperationResponse, handler=prepare_group,
        tags=frozenset({"h3", "generation-group", "planning", "execution", "resume"}),
    ))

    def get_group_operation(request: H3GroupOperationGetInput) -> ToolExecution[H3GroupOperationResponse]:
        result = service.get_h3_group_operation(request.project_id, request.operation_id)
        return ToolExecution(output=result, artifact_refs=group_operation_refs(result))

    registry.register(ToolSpec(
        id="route.get_h3_group_operation", version=1, category=ToolCategory.ROUTE,
        description="Read a saved H3 group operation and current task states. No language or video provider calls; no implicit retry.",
        effect=ToolEffect.READ, idempotent=True, input_model=H3GroupOperationGetInput,
        output_model=H3GroupOperationResponse, handler=get_group_operation,
        tags=frozenset({"h3", "generation-group", "resume", "on-demand-payload"}),
    ))

    def get_generation_unit_context(
        request: GenerationUnitContextInput,
    ) -> ToolExecution[H3GenerationUnitContextResponse]:
        result = service.get_h3_generation_unit_context(request.project_id, request.request)
        return ToolExecution(output=result, artifact_refs=(
            ArtifactRef(project_id=result.project_id, artifact_type="generation_plan",
                        artifact_id=result.generation_plan_document_id, version=result.generation_plan_version),
            ArtifactRef(project_id=result.project_id, artifact_type="generation_unit",
                        artifact_id=result.generation_unit_id, version=result.generation_plan_version),
        ))

    registry.register(ToolSpec(
        id="route.get_generation_unit_context", version=1, category=ToolCategory.ROUTE,
        description=(
            "Read one exact GenerationUnit's complete canonical shots, dialogue, timing, "
            "production direction and visible Bible, plus current usable media candidates "
            "with real local_path/external_url, AssetVersion, subject, role, purpose and tags. "
            "Current available DRAFT images can be selected directly; approval status is not a prerequisite. "
            "Use this before compile_h3_prompt; it replaces full Storyboard/AssetPlan reads "
            "for video preparation. Select the route and ordered media subset yourself. "
            "Read-only; no model, upload or H3 call."
        ),
        effect=ToolEffect.READ, idempotent=True,
        input_model=GenerationUnitContextInput, output_model=H3GenerationUnitContextResponse,
        handler=get_generation_unit_context,
        tags=frozenset({"generation-unit", "context-slice", "media-source", "read"}),
    ))

    def compile_generation_plan(
        request: GenerationPlanCompileInput,
    ) -> ToolExecution[GenerationPlanMutationResponse]:
        result = service.compile_generation_plan(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_generation_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="route.compile_generation_plan",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "把指定版本的 Storyboard ClipGroup 编译为 Provider-neutral"
                " GenerationUnit，并保留每个 Shot 在生成视频中的源时间窗。"
                "该工具只形成可执行计划，不调用 H3 或其他真实 Provider。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=GenerationPlanCompileInput,
            output_model=GenerationPlanMutationResponse,
            handler=compile_generation_plan,
            tags=frozenset({"generation-plan", "generation-unit", "handoff"}),
        )
    )

    def get_generation_plan(
        request: GenerationPlanGetInput,
    ) -> ToolExecution[GenerationPlanReadResponse]:
        result = service.get_generation_plan(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_generation_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="route.get_generation_plan",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "读取当前或指定历史版本的 GenerationPlan 和 GenerationUnit，"
                "供 H3 路由 Agent 与后续执行 Worker 精确交接。"
                "尚未编译时返回具体缺项；不自动创建计划。完整当前组准备可用"
                " route.prepare_h3_group（enqueue=false），无需先手工创建计划。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=GenerationPlanGetInput,
            output_model=GenerationPlanReadResponse,
            handler=get_generation_plan,
            tags=frozenset({"generation-plan", "generation-unit", "read"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.h3_capability_profile",
            version=1,
            category=ToolCategory.ROUTE,
            description="Read the current evidence-backed MiniMax-H3 capability profile.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProjectInput,
            output_model=H3CapabilityProfileResponse,
            handler=lambda request: ToolExecution(
                output=service.get_h3_capability_profile(request.project_id)
            ),
            tags=frozenset({"h3", "route", "capability"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.compile_h3_prompt",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "Compile an H3 prompt from one canonical shot or an exact generation-plan "
                "document/version/unit. Pass Agent-selected method_skill_ids and ordered "
                "media_inputs from route.get_generation_unit_context; for a unit, send the result to route.plan_h3 as its explicit "
                "prompt_override with the same media order. A single-shot response has no generation-unit candidate "
                "and cannot supply authored prompt/media to plan_h3. For full current-group preparation use "
                "prepare_h3_group with enqueue=false; for a subset hand off the exact result to its group owner. "
                "Does not submit video."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=RoutePromptCompileInput,
            output_model=LanguagePromptCompileResponse,
            handler=lambda request: ToolExecution(
                output=service.compile_h3_prompt_with_language_model(
                    request.project_id,
                    request.request,
                )
            ),
            tags=frozenset({"h3", "prompt", "route"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.preview_h3_probe",
            version=1,
            category=ToolCategory.ROUTE,
            description="Compile an H3 request preview without submitting it to the provider.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProbeInput,
            output_model=H3CompilePreviewResponse,
            handler=lambda request: ToolExecution(
                output=service.preview_h3_probe(request.project_id, request.request)
            ),
            tags=frozenset({"h3", "preview", "route"}),
        )
    )

    def enqueue_probe(request: RouteProbeInput) -> ToolExecution[H3ProbeResponse]:
        result = service.enqueue_h3_probe(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="generation_task",
                    artifact_id=result.task.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="h3_provider_run",
                    artifact_id=result.provider_run.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.enqueue_h3_probe",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "Queue a bounded real MiniMax-H3 probe using an explicit typed request."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=RouteProbeInput,
            output_model=H3ProbeResponse,
            handler=enqueue_probe,
            tags=frozenset({"h3", "probe", "route"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.list_h3_runs",
            version=2,
            category=ToolCategory.ROUTE,
            description=(
                "List compact MiniMax-H3 run metadata, task status and produced Take IDs for a project. "
                "Read exact prompt and media details with route.get_h3_run(task_id); "
                "read local task state and produced Takes with task.get(task_id)."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProjectInput,
            output_model=H3ProviderRunIndexResponse,
            handler=lambda request: ToolExecution(
                output=service.list_h3_provider_run_index(request.project_id)
            ),
            tags=frozenset({"h3", "provider-run", "route"}),
        )
    )

    def get_run(request: RouteTaskInput) -> ToolExecution[H3ProviderRun]:
        run = service.get_h3_provider_run(request.task_id)
        return ToolExecution(output=run, artifact_refs=(
            ArtifactRef(project_id=run.project_id, artifact_type="h3_provider_run", artifact_id=run.id),
            ArtifactRef(project_id=run.project_id, artifact_type="generation_task", artifact_id=run.task_id),
        ))

    registry.register(ToolSpec(
        id="route.get_h3_run", version=1, category=ToolCategory.ROUTE,
        description="Read one task's exact persisted H3 run, prompt, ordered references and current remote result.",
        effect=ToolEffect.READ, idempotent=True,
        input_model=RouteTaskInput, output_model=H3ProviderRun,
        handler=get_run, tags=frozenset({"h3", "provider-run", "read"}),
    ))

    def list_reconciliation_candidates(
        request: RouteReconciliationInput,
    ) -> ToolExecution[H3ReconciliationCandidatesResponse]:
        result = service.list_h3_reconciliation_candidates_for_task(
            request.project_id,
            request.task_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="generation_task",
                    artifact_id=request.task_id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.list_h3_reconciliation",
            version=2,
            category=ToolCategory.ROUTE,
            description=(
                "List remote H3 candidates compatible with one local SUBMIT_UNKNOWN "
                "task; do not bind automatically when multiple candidates remain."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=True,
            input_model=RouteReconciliationInput,
            output_model=H3ReconciliationCandidatesResponse,
            handler=list_reconciliation_candidates,
            tags=frozenset({"h3", "reconciliation", "route"}),
        )
    )

    def resolve_submission(
        request: RouteResolveSubmissionInput,
    ) -> ToolExecution[H3TaskActionResponse]:
        result = service.resolve_h3_submission(
            request.task_id,
            request.request.provider_task_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=result.task.project_id,
                    artifact_type="generation_task",
                    artifact_id=result.task.id,
                ),
                ArtifactRef(
                    project_id=result.task.project_id,
                    artifact_type="h3_provider_run",
                    artifact_id=result.provider_run.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.resolve_h3_submission",
            version=1,
            category=ToolCategory.ROUTE,
            description="Attach a known provider task id to an uncertain H3 submission.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=RouteResolveSubmissionInput,
            output_model=H3TaskActionResponse,
            handler=resolve_submission,
            tags=frozenset({"h3", "reconciliation", "route"}),
        )
    )

    def resolve_not_submitted(
        request: RouteResolveNotSubmittedInput,
    ) -> ToolExecution[H3TaskActionResponse]:
        result = service.resolve_h3_not_submitted(request.task_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=result.task.project_id,
                    artifact_type="generation_task",
                    artifact_id=result.task.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.resolve_h3_not_submitted",
            version=1,
            category=ToolCategory.ROUTE,
            description="Record that an uncertain H3 task was not submitted and can proceed locally.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=RouteResolveNotSubmittedInput,
            output_model=H3TaskActionResponse,
            handler=resolve_not_submitted,
            tags=frozenset({"h3", "reconciliation", "route"}),
        )
    )

    def plan(request: RoutePlanInput) -> ToolExecution[H3ProductionRoutePlanResponse]:
        result = service.compile_h3_production_route_plan(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="h3_route_plan",
                    artifact_id=result.plan.id,
                    version=result.plan.state_revision,
                ),
            ),
            warnings=tuple(result.plan.warnings),
        )

    registry.register(
        ToolSpec(
            id="route.plan_h3",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "Save a MiniMax-H3 route plan for shots or generation units. For units use "
                "generation_plan_document_id/version_id and unit_ids without shot_ids; "
                "each directive targets generation_unit_id without shot_id. Preserve the "
                "compiled prompt and media order. Does not submit video."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=RoutePlanInput,
            output_model=H3ProductionRoutePlanResponse,
            handler=plan,
            tags=frozenset({"h3", "planning", "route"}),
        )
    )

    def list_plans(request: RouteProjectInput) -> ToolExecution[H3ProductionRoutePlanListResponse]:
        result = service.list_h3_production_route_plans(request.project_id)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(project_id=request.project_id, artifact_type="h3_route_plan_index",
                            artifact_id=request.project_id),
                *(ArtifactRef(project_id=request.project_id, artifact_type="h3_route_plan",
                              artifact_id=item.id, version=item.state_revision) for item in result.plans),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.list_h3_plans",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "List persisted H3 route plans for coverage and readiness. Model context "
                "contains compact plan summaries; use route.get_h3_plan for one complete "
                "saved prompt, media order, and execution parameters."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProjectInput,
            output_model=H3ProductionRoutePlanListResponse,
            handler=list_plans,
            tags=frozenset({"h3", "planning", "route"}),
        )
    )

    def get_plan(request: RoutePlanGetInput) -> ToolExecution[H3ProductionRoutePlanResponse]:
        result = service.get_h3_production_route_plan(request.project_id, request.plan_id)
        return ToolExecution(
            output=result,
            artifact_refs=(ArtifactRef(
                project_id=request.project_id, artifact_type="h3_route_plan",
                artifact_id=result.plan.id, version=result.plan.state_revision,
            ),),
        )

    registry.register(ToolSpec(
        id="route.get_h3_plan", version=1, category=ToolCategory.ROUTE,
        description="Read one complete persisted H3 route plan, including its exact prompt and ordered media. No provider calls.",
        effect=ToolEffect.READ, idempotent=True, input_model=RoutePlanGetInput,
        output_model=H3ProductionRoutePlanResponse, handler=get_plan,
        tags=frozenset({"h3", "planning", "route", "on-demand-payload"}),
    ))

    def update_asset_preparation(
        request: RouteAssetPreparationUpdateInput,
    ) -> ToolExecution[H3RouteAssetPreparationUpdateResponse]:
        result = service.update_h3_route_asset_preparation(
            request.project_id,
            request.plan_id,
            request.preparation_id,
            request.request,
        )
        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="h3_route_plan",
                artifact_id=result.plan.id,
                version=result.plan.state_revision,
            ),
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="h3_route_asset_preparation",
                artifact_id=result.preparation.id,
                version=result.plan.state_revision,
            ),
        ]
        if result.preparation.asset_id is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset",
                    artifact_id=result.preparation.asset_id,
                )
            )
        if result.preparation.approved_version_id is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset_version",
                    artifact_id=result.preparation.approved_version_id,
                    version=result.approved_version_number,
                )
            )
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="route.update_h3_asset_preparation",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "当图像 Worker 已开始、已产出并批准、或已拒绝某个精确 H3 "
                "关键帧准备时，回写 GENERATING/READY/REJECTED 交接。"
                "READY 只完成交接，不会改写原 NEEDS_ASSET 路线；随后必须再调用 "
                "route.plan_h3，让新路线消费已批准版本。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=RouteAssetPreparationUpdateInput,
            output_model=H3RouteAssetPreparationUpdateResponse,
            handler=update_asset_preparation,
            tags=frozenset({"asset", "handoff", "h3", "route"}),
        )
    )

    def execute(
        request: RouteExecuteInput,
    ) -> ToolExecution[H3ProductionRouteExecuteResponse]:
        result = service.execute_h3_production_route_plan(
            request.project_id,
            request.plan_id,
            request.request,
        )
        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="h3_route_plan",
                artifact_id=result.plan.id,
                version=result.plan.state_revision,
            )
        ]
        refs.extend(
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="generation_task",
                artifact_id=task.id,
            )
            for task in result.queued_tasks
        )
        if result.manifest is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="timeline_manifest",
                    artifact_id=result.manifest.id,
                    version=result.manifest.version,
                )
            )
        if result.export is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="export",
                    artifact_id=result.export.id,
                )
            )
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="route.execute_h3_plan",
            version=1,
            category=ToolCategory.ROUTE,
            description=(
                "Execute a persisted real MiniMax-H3 route plan using its explicit "
                "execution options."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=RouteExecuteInput,
            output_model=H3ProductionRouteExecuteResponse,
            handler=execute,
            tags=frozenset({"execution", "h3", "route"}),
        )
    )

    def review_take(
        request: RouteReviewTakeInput,
    ) -> ToolExecution[H3TakeReviewResponse]:
        result = service.review_h3_take(request.take_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=result.take.project_id,
                    artifact_type="take",
                    artifact_id=result.take.id,
                ),
                ArtifactRef(
                    project_id=result.take.project_id,
                    artifact_type="route_evidence",
                    artifact_id=result.evidence.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="route.review_h3_take",
            version=1,
            category=ToolCategory.ROUTE,
            description="Record the typed quality review for one generated H3 Take.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=RouteReviewTakeInput,
            output_model=H3TakeReviewResponse,
            handler=review_take,
            tags=frozenset({"h3", "review", "route"}),
        )
    )


__all__ = [
    "GenerationPlanCompileInput",
    "GenerationPlanGetInput",
    "RouteAssetPreparationUpdateInput",
    "RouteExecuteInput",
    "RoutePlanInput",
    "RouteProbeInput",
    "RouteProjectInput",
    "RoutePromptCompileInput",
    "RouteReconciliationInput",
    "RouteResolveNotSubmittedInput",
    "RouteResolveSubmissionInput",
    "RouteReviewTakeInput",
    "register_tools",
]
