"""MiniMax-H3 route planning, execution, review, and recovery adapters."""

from __future__ import annotations

from typing import Protocol

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
    H3ProviderRunsResponse,
    H3ReconcileRequest,
    H3ReconciliationCandidatesResponse,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.language_models import (
    LanguagePromptCompileRequest,
    LanguagePromptCompileResponse,
)
from short_drama.quality_learning import H3CapabilityProfileResponse
from short_drama.tools.common import EmptyToolInput
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class RouteToolService(Protocol):
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

    def list_h3_provider_runs(self, project_id: str) -> H3ProviderRunsResponse: ...

    def list_h3_reconciliation_candidates(
        self,
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


class RoutePromptCompileInput(ToolModel):
    project_id: str
    request: LanguagePromptCompileRequest


class RouteProbeInput(ToolModel):
    project_id: str
    request: H3ProbeRequest


class RouteResolveSubmissionInput(ToolModel):
    task_id: str
    request: H3ReconcileRequest


class RouteResolveNotSubmittedInput(ToolModel):
    task_id: str
    request: H3NotSubmittedResolutionRequest


class RoutePlanInput(ToolModel):
    project_id: str
    request: H3ProductionRoutePlanRequest


class RouteExecuteInput(ToolModel):
    project_id: str
    plan_id: str
    request: H3ProductionRouteExecuteRequest


class RouteReviewTakeInput(ToolModel):
    take_id: str
    request: H3TakeReviewRequest


def register_tools(registry: ToolRegistry, service: RouteToolService) -> None:
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
            description="Compile one provider-ready H3 prompt from canonical shot context.",
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
            description="Queue a bounded H3 probe using an explicit typed request.",
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
            version=1,
            category=ToolCategory.ROUTE,
            description="List persisted MiniMax-H3 provider runs for a project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProjectInput,
            output_model=H3ProviderRunsResponse,
            handler=lambda request: ToolExecution(
                output=service.list_h3_provider_runs(request.project_id)
            ),
            tags=frozenset({"h3", "provider-run", "route"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.list_h3_reconciliation",
            version=1,
            category=ToolCategory.ROUTE,
            description="List H3 tasks whose remote submission state needs reconciliation.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=EmptyToolInput,
            output_model=H3ReconciliationCandidatesResponse,
            handler=lambda _request: ToolExecution(
                output=service.list_h3_reconciliation_candidates()
            ),
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
                    version=result.plan.version,
                ),
            ),
            warnings=tuple(result.plan.warnings),
        )

    registry.register(
        ToolSpec(
            id="route.plan_h3",
            version=1,
            category=ToolCategory.ROUTE,
            description="Compile a shot-level MiniMax-H3 production route plan.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=RoutePlanInput,
            output_model=H3ProductionRoutePlanResponse,
            handler=plan,
            tags=frozenset({"h3", "planning", "route"}),
        )
    )

    registry.register(
        ToolSpec(
            id="route.list_h3_plans",
            version=1,
            category=ToolCategory.ROUTE,
            description="List persisted H3 production route plan revisions for a project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=RouteProjectInput,
            output_model=H3ProductionRoutePlanListResponse,
            handler=lambda request: ToolExecution(
                output=service.list_h3_production_route_plans(request.project_id)
            ),
            tags=frozenset({"h3", "planning", "route"}),
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
                version=result.plan.version,
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
            description="Execute a persisted H3 route plan using its explicit execution options.",
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
    "RouteExecuteInput",
    "RoutePlanInput",
    "RouteProbeInput",
    "RouteProjectInput",
    "RoutePromptCompileInput",
    "RouteResolveNotSubmittedInput",
    "RouteResolveSubmissionInput",
    "RouteReviewTakeInput",
    "register_tools",
]
