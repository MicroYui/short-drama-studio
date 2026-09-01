"""Agent-first production coordination over shared Skills and Tools.

The Production Manager is deliberately a thin harness.  Agents own the plan and
may replace it with a different dependency graph at any time.  This module only
provides bounded execution, persistence, focused context, Tool invocation, and
structured delegation; it does not encode a fixed video-production workflow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import threading
from typing import Protocol, cast
from uuid import uuid4

from pydantic import Field, JsonValue

from short_drama.agent_runtime import (
    ActionKind,
    ActionRecord,
    ActionStatus,
    AgentResult,
    AgentResultContractError,
    AgentResultStatus,
    AgentRunSnapshot,
    AgentRunStatus,
    AgentRuntime,
    ArtifactRef,
    ArtifactSummary,
    ContextPack,
    DelegationStatus,
    PlanGraph,
    PlanStep,
    ProjectMemory,
    WorkOrder,
)
from short_drama.agent_runtime.models import utc_now
from short_drama.image_models import ImageGenerationTask
from short_drama.models import (
    ProductionExecutionTarget,
    CreateProjectRequest,
    DomainModel,
    GenerationTask,
    PlanningRunRecord,
    PlanningRunStatus,
    ProductionProfile,
    ProjectDocument,
    ProjectStage,
    RunMode,
    TaskStatus,
    Take,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_skill_catalog,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.tools import ToolCallStatus, ToolDescriptor, ToolRegistry, ToolResult


class ProductionManagerError(ValueError):
    """Raised when an Agent run cannot be planned or advanced as requested."""


_MOCK_ONLY_TOOL_IDS = frozenset(
    {
        "video.enqueue",
        "video.retake",
        "video.extend",
        "video.gap_fill",
        "task.drain",
        "task.reconcile",
    }
)
_REAL_H3_ONLY_TOOL_IDS = frozenset(
    {
        "route.enqueue_h3_probe",
        "route.execute_h3_plan",
        "route.resolve_h3_submission",
        "route.resolve_h3_not_submitted",
    }
)
_PROVIDER_BOUND_TASK_TOOL_IDS = frozenset(
    {
        "task.advance",
        "task.retry",
        "task.recover_output",
    }
)


def _tools_for_execution_target(
    tools: Sequence[ToolDescriptor],
    target: ProductionExecutionTarget | None,
) -> list[ToolDescriptor]:
    """Hide only generation executors that contradict the selected run target."""

    blocked = (
        _MOCK_ONLY_TOOL_IDS | _REAL_H3_ONLY_TOOL_IDS
        if target is None
        else (
            _REAL_H3_ONLY_TOOL_IDS
            if target == ProductionExecutionTarget.MOCK
            else _MOCK_ONLY_TOOL_IDS
        )
    )
    return [tool for tool in tools if tool.id not in blocked]


class ProjectDocumentStore(Protocol):
    def get_project(self, project_id: str) -> ProjectDocument | None: ...

    def get_planning_run(self, run_id: str) -> PlanningRunRecord | None: ...

    def get_task(self, task_id: str) -> GenerationTask | None: ...

    def list_tasks(self, project_id: str) -> list[GenerationTask]: ...

    def get_image_task(self, task_id: str) -> ImageGenerationTask | None: ...

    def get_take(self, take_id: str) -> Take | None: ...


class ArtifactRecordLike(Protocol):
    id: str
    project_id: str

    def model_dump_json(self) -> str: ...


class ProjectArtifactRecordStore(Protocol):
    def list_exports(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_provider_runs(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_route_evidence(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def get_video_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_take_comparison(self, report_id: str) -> ArtifactRecordLike | None: ...

    def list_reference_visual_reranks(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_keyframe_comparisons(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_input_compatibility_reports(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def get_audio_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_episode_review(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_h3_route_plan(self, plan_id: str) -> ArtifactRecordLike | None: ...


class AgentPlanProposal(DomainModel):
    """A complete, replaceable plan authored by an Agent or supplied by a caller."""

    summary: str = Field(min_length=1, max_length=4000)
    steps: list[PlanStep] = Field(default_factory=list[PlanStep], max_length=48)
    goal_complete: bool = False


class AgentRunCreateRequest(DomainModel):
    objective: str = Field(min_length=1, max_length=12000)
    root_agent: str = Field(default="production-manager", min_length=1, max_length=120)
    auto_plan: bool = True
    initial_plan: AgentPlanProposal | None = None
    unattended: bool = False
    execution_target: ProductionExecutionTarget | None = None
    render_export: bool | None = None


class AgentProjectCreateRequest(DomainModel):
    """Bootstrap a production run before its canonical project exists."""

    project: CreateProjectRequest
    execution_target: ProductionExecutionTarget = ProductionExecutionTarget.MOCK
    render_export: bool = True
    objective: str | None = Field(default=None, min_length=1, max_length=12000)
    root_agent: str = Field(default="production-manager", min_length=1, max_length=120)


class AgentRunReplanRequest(DomainModel):
    reason: str = Field(min_length=1, max_length=4000)
    proposed_plan: AgentPlanProposal | None = None


class AgentRunAdvanceRequest(DomainModel):
    max_actions: int = Field(default=1, ge=1, le=8)


class AgentUserInputRequest(DomainModel):
    action_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=12000)
    remember_as_decision: bool = True


class AgentWorkOrderResultRequest(DomainModel):
    status: AgentResultStatus
    concise_summary: str = Field(min_length=1, max_length=4000)
    created_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    updated_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    findings: list[str] = Field(default_factory=list[str], max_length=40)
    decisions: list[str] = Field(default_factory=list[str], max_length=40)
    assumptions: list[str] = Field(default_factory=list[str], max_length=40)
    unresolved_questions: list[str] = Field(default_factory=list[str], max_length=40)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recommended_next_actions: list[str] = Field(default_factory=list[str], max_length=40)


class ProductionSkillDescriptor(DomainModel):
    id: str
    profile: ProductionProfile
    label: str
    medium: str
    recommended_playbook: list[str]
    role_names: list[str]


class AgentCapabilityCatalog(DomainModel):
    tools: list[ToolDescriptor]
    skills: list[ProductionSkillDescriptor]


class AgentRunView(DomainModel):
    snapshot: AgentRunSnapshot
    ready_steps: list[PlanStep]
    context: ContextPack


class AgentProjectCreateResponse(DomainModel):
    project_id: str
    run: AgentRunView


class AgentRunListResponse(DomainModel):
    runs: list[AgentRunView]


class AgentAdvanceResponse(DomainModel):
    run: AgentRunView
    executed_action_ids: list[str]
    created_work_order_ids: list[str]
    notes: list[str]


class AgentWorkOrderView(DomainModel):
    work_order: WorkOrder
    context: ContextPack
    system_prompt: str


class AgentWorkOrderResultResponse(DomainModel):
    result: AgentResult
    run: AgentRunView


class ProductionPlanner(Protocol):
    """Agent planning boundary; implementations may use any language model."""

    def propose_plan(
        self,
        *,
        context: ContextPack,
        skill_prompt: str,
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal: ...


ProductionPlannerFactory = Callable[[LanguageProviderClient], ProductionPlanner]
LanguageClientResolver = Callable[[], LanguageProviderClient | None]


class SpecialistAgent(Protocol):
    """Execute one bounded WorkOrder using its role-specific Skill context."""

    def execute(self, work_order: AgentWorkOrderView) -> AgentWorkOrderResultRequest: ...


SpecialistAgentFactory = Callable[[LanguageProviderClient], SpecialistAgent]


def production_skill_id(profile: ProductionProfile) -> str:
    return f"format.{profile.value.casefold().replace('_', '-')}"


def _json_block(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _unique_artifact_refs(refs: Sequence[ArtifactRef]) -> list[ArtifactRef]:
    result: list[ArtifactRef] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for ref in refs:
        identity = ref.identity()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(ref)
    return result


def _run_decision_suffix(context: ContextPack, prefix: str, default: str) -> str:
    for decision in context.decisions:
        if decision.run_id == context.run_id and decision.statement.startswith(prefix):
            value = decision.statement.removeprefix(prefix).strip()
            if value:
                return value
    return default


def _latest_artifact_ref(
    context: ContextPack,
    artifact_type: str,
) -> ArtifactRef | None:
    for action in reversed(context.recent_actions):
        for ref in reversed(action.artifact_refs):
            if ref.artifact_type == artifact_type:
                return ref
    return None


class LanguageProductionPlanner:
    """Ask the configured language model for a dynamic dependency-aware plan."""

    def __init__(self, client: LanguageProviderClient) -> None:
        self._client = client

    def propose_plan(
        self,
        *,
        context: ContextPack,
        skill_prompt: str,
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal:
        tool_catalog = [
            {
                "id": item.id,
                "description": item.description,
                "effect": item.effect.value,
                "input_schema": item.input_schema,
            }
            for item in tools
        ]
        payload = {
            "reason": reason,
            "context": context.model_dump(mode="json"),
            "available_tools": tool_catalog,
            "current_plan": None
            if current_plan is None
            else current_plan.model_dump(mode="json"),
        }
        instructions = (
            "你是以 Agent 为核心的视频创作系统中的 Production Manager。请依据给定上下文，"
            "为当前目标提出最小且有用的下一版计划。你可以选择、跳过、重排、并行或替换步骤，"
            "不要照抄固定工作流。TOOL_CALL 或 ARTIFACT_PATCH 只能使用可用的 Tool id；"
            "当专业 Agent 应依据当前 Production Skill 完成任务时使用 DELEGATION；"
            "只有缺少会实质改变结果的用户决定时才使用 USER_INPUT。步骤依赖必须构成无环图。"
            "只返回 JSON，顶层键为 summary、steps、goal_complete。仅当最新上下文已经证明"
            "本次运行目标达成时设置 goal_complete=true；否则返回下一组有边界的有效步骤，"
            "并设置 goal_complete=false。每个步骤包含 id、title、kind、assigned_agent、"
            "可选的 tool_name，以及 instructions、arguments、depends_on、artifact_refs。"
            "DELEGATION 的 arguments 可以包含 why_now、constraints、confirmed_decisions、"
            "open_questions、acceptance_criteria、expected_output_schema。\n\n"
            + skill_prompt
        )
        try:
            raw = self._client.generate_text(
                instructions=instructions,
                input_text=json.dumps(payload, ensure_ascii=False),
                max_output_tokens=7000,
            )
        except Exception as exc:
            raise ProductionManagerError(
                f"Production Manager planning failed: {exc}"
            ) from exc
        try:
            return AgentPlanProposal.model_validate_json(_json_block(raw))
        except ValueError as exc:
            raise ProductionManagerError(
                "Production Manager returned an invalid plan payload"
            ) from exc


def default_production_planner_factory(
    client: LanguageProviderClient,
) -> ProductionPlanner:
    return LanguageProductionPlanner(client)


class LocalProductionPlanner:
    """Small deterministic fallback used only when no language planner is configured."""

    def __init__(self, projects: ProjectDocumentStore) -> None:
        self._projects = projects

    def propose_plan(
        self,
        *,
        context: ContextPack,
        skill_prompt: str,
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal:
        del skill_prompt, reason
        document = self._projects.get_project(context.project_id)
        if document is None:
            raise ProductionManagerError(
                "The local planner cannot continue before project.create succeeds"
            )
        available = {item.id for item in tools}
        project_id = document.project.id
        if (
            document.project.run_mode == RunMode.GUIDED
            and current_plan is not None
            and any(step.tool_name == "project.create" for step in current_plan.steps)
        ):
            return AgentPlanProposal(
                summary="The guided project is created and ready for user-directed work.",
                goal_complete=True,
            )

        execution_target = _run_decision_suffix(
            context,
            "Execution target:",
            "",
        )
        if not execution_target:
            return AgentPlanProposal(
                summary=(
                    "The local fallback needs an explicit production target before it can "
                    "perform media-generation work for this Agent run."
                ),
                steps=[
                    PlanStep(
                        id="request-production-target",
                        title="Choose a production target",
                        kind=ActionKind.USER_INPUT,
                        assigned_agent="production-manager",
                        instructions=(
                            "Choose MOCK or REAL_H3 for this production continuation, or "
                            "configure the language provider for a different dynamic plan."
                        ),
                    )
                ],
            )
        render_export = (
            _run_decision_suffix(context, "Render export:", "yes").casefold() != "no"
        )
        stage = document.project.stage
        terminal_statuses = {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUBMIT_UNKNOWN,
            TaskStatus.OUTPUT_MISSING,
        }
        if stage == ProjectStage.EXPORTED:
            return AgentPlanProposal(
                summary="The local fallback confirms that the requested export exists.",
                goal_complete=True,
            )
        if stage == ProjectStage.TIMELINE_READY and not render_export:
            return AgentPlanProposal(
                summary="The requested timeline is ready and export was not requested.",
                goal_complete=True,
            )

        if execution_target == ProductionExecutionTarget.MOCK.value:
            if stage in {ProjectStage.INPUT, ProjectStage.PLANNED}:
                return self._one_tool_plan(
                    available,
                    summary="Queue deterministic candidate generation for the planned shots.",
                    step_id="enqueue-mock-video",
                    title="Queue candidate videos",
                    tool_name="video.enqueue",
                    arguments={"project_id": project_id, "request": {}},
                )
            if stage == ProjectStage.GENERATING and any(
                task.provider == "mock" and task.status not in terminal_statuses
                for task in self._projects.list_tasks(project_id)
            ):
                return self._one_tool_plan(
                    available,
                    summary="Advance the persisted deterministic generation tasks to idle.",
                    step_id="drain-mock-tasks",
                    title="Drain generation tasks",
                    tool_name="task.drain",
                    arguments={"project_id": project_id, "max_steps": 5000},
                )
            if stage == ProjectStage.REVIEW:
                return self._one_tool_plan(
                    available,
                    summary="Assemble the current selected Takes into a timeline.",
                    step_id="rebuild-timeline",
                    title="Build the timeline",
                    tool_name="timeline.rebuild",
                    arguments={"project_id": project_id},
                )
            if stage == ProjectStage.TIMELINE_READY:
                return self._one_tool_plan(
                    available,
                    summary="Render the requested delivery artifacts.",
                    step_id="export-timeline",
                    title="Export the finished timeline",
                    tool_name="timeline.export",
                    arguments={
                        "project_id": project_id,
                        "request": {"rebuild_manifest": False},
                    },
                )

        route_plan_ref = _latest_artifact_ref(context, "h3_route_plan")
        if stage in {ProjectStage.INPUT, ProjectStage.PLANNED} and route_plan_ref is None:
            return self._one_tool_plan(
                available,
                summary="Compile a provider route plan without a language-model planner.",
                step_id="plan-h3-route",
                title="Plan the H3 route",
                tool_name="route.plan_h3",
                arguments={
                    "project_id": project_id,
                    "request": {
                        "use_language_agent": False,
                        "candidate_count": 1,
                        "consider_video_guides": True,
                    },
                },
            )
        if stage in {ProjectStage.INPUT, ProjectStage.PLANNED} and route_plan_ref is not None:
            return self._one_tool_plan(
                available,
                summary="Start the prepared H3 route with explicit provider settings.",
                step_id="execute-h3-route",
                title="Execute the H3 route",
                tool_name="route.execute_h3_plan",
                arguments={
                    "project_id": project_id,
                    "plan_id": route_plan_ref.artifact_id,
                    "request": {
                        "reuse_passed_takes": True,
                        "enqueue_new_generations": True,
                        "adaptive_sequential": True,
                        "inspect_reused_takes": False,
                        "semantic_review": False,
                        "rebuild_manifest": False,
                        "render_export": False,
                    },
                },
            )
        if stage == ProjectStage.GENERATING:
            task_refs = [
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="generation_task",
                    artifact_id=task.id,
                )
                for task in self._projects.list_tasks(project_id)
                if task.provider == "minimax" and task.status not in terminal_statuses
            ][:48]
            if task_refs:
                return AgentPlanProposal(
                    summary="Advance the current H3 provider tasks by one bounded transition.",
                    steps=[
                        PlanStep(
                            id=f"advance-{index}",
                            title=f"Advance provider task {ref.artifact_id}",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="task.advance",
                            instructions="Advance this persisted provider task once.",
                            arguments={"task_id": ref.artifact_id},
                            artifact_refs=[ref],
                        )
                        for index, ref in enumerate(task_refs, start=1)
                    ],
                )

        return AgentPlanProposal(
            summary=(
                "The local fallback reached work that needs a user or configured specialist "
                "Agent to choose the next repair or review action."
            ),
            steps=[
                PlanStep(
                    id="request-next-production-decision",
                    title="Request the next production decision",
                    kind=ActionKind.USER_INPUT,
                    assigned_agent="production-manager",
                    instructions=(
                        "Choose the next review or repair action, or configure the language "
                        "provider so the Production Manager can continue autonomously."
                    ),
                )
            ],
        )

    @staticmethod
    def _one_tool_plan(
        available: set[str],
        *,
        summary: str,
        step_id: str,
        title: str,
        tool_name: str,
        arguments: dict[str, JsonValue],
    ) -> AgentPlanProposal:
        if tool_name not in available:
            raise ProductionManagerError(
                f"The local fallback requires unavailable Tool {tool_name!r}"
            )
        return AgentPlanProposal(
            summary=summary,
            steps=[
                PlanStep(
                    id=step_id,
                    title=title,
                    kind=ActionKind.TOOL_CALL,
                    assigned_agent="production-manager",
                    tool_name=tool_name,
                    instructions=summary,
                    arguments=arguments,
                )
            ],
        )


class SpecialistToolRequest(DomainModel):
    """One registry invocation requested by the built-in specialist Agent."""

    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    reason: str = Field(default="Gather the evidence needed for the WorkOrder", max_length=1000)


class LanguageSpecialistAgent:
    """Bounded specialist loop that may inspect or mutate through the shared Tool Registry."""

    def __init__(
        self,
        client: LanguageProviderClient,
        tools: ToolRegistry | None = None,
        *,
        max_tool_rounds: int = 4,
    ) -> None:
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds must be non-negative")
        self._client = client
        self._tools = tools
        self._tool_descriptors: list[ToolDescriptor] | None = None
        self._scoped_tool_invoker: (
            Callable[[str, str, Mapping[str, object]], ToolResult] | None
        ) = None
        self._max_tool_rounds = max_tool_rounds

    def bind_tools(
        self,
        tools: ToolRegistry,
        *,
        scoped_invoker: Callable[
            [str, str, Mapping[str, object]], ToolResult
        ] | None = None,
        descriptors: Sequence[ToolDescriptor] | None = None,
    ) -> None:
        """Attach the manager's shared registry without changing custom Agent factories."""

        self._tools = tools
        self._tool_descriptors = None if descriptors is None else list(descriptors)
        self._scoped_tool_invoker = scoped_invoker

    def execute(self, work_order: AgentWorkOrderView) -> AgentWorkOrderResultRequest:
        descriptors = (
            []
            if self._tools is None
            else (
                self._tools.list()
                if self._tool_descriptors is None
                else self._tool_descriptors
            )
        )
        tool_catalog = [
            {
                "id": item.id,
                "description": item.description,
                "effect": item.effect.value,
                "input_schema": item.input_schema,
            }
            for item in descriptors
        ]
        tool_history: list[dict[str, object]] = []
        evidence_refs: list[ArtifactRef] = []

        for round_index in range(self._max_tool_rounds + 1):
            may_call_tool = bool(descriptors) and round_index < self._max_tool_rounds
            instructions = (
                "你是当前 WorkOrder 指定的专业 Agent。请依据下方对应角色的 Production Skill，"
                "处理已经限定范围的目标和所提供的工件摘要，不要扩大任务范围。"
                "你与 Production Manager 共用同一套 Tool Catalog。"
                + (
                    "如果还需要补充证据或执行范围内的修改，只返回包含 tool_name、arguments、"
                    "reason 的 JSON，并且每轮只请求一次 Tool 调用；否则直接返回最终结果。"
                    if may_call_tool
                    else "Tool 调用轮次已用完，请立即返回当前能够给出的最佳最终结果。"
                )
                + "最终结果必须是符合 AgentWorkOrderResultRequest 的 JSON，字段包括 status、"
                "concise_summary、created_artifacts、updated_artifacts、findings、decisions、"
                "assumptions、unresolved_questions、evidence_refs、recommended_next_actions。"
                "请求的分析或决定已经完成时使用 COMPLETED；缺少实质输入时使用 PARTIAL；"
                "只有目标确实无法处理时才使用 FAILED。\n\n"
                + work_order.system_prompt
            )
            raw = self._client.generate_text(
                instructions=instructions,
                input_text=json.dumps(
                    {
                        "work_order": work_order.work_order.model_dump(mode="json"),
                        "context": work_order.context.model_dump(mode="json"),
                        "available_tools": tool_catalog,
                        "tool_history": tool_history,
                        "tool_calls_remaining": max(
                            0,
                            self._max_tool_rounds - round_index,
                        ),
                    },
                    ensure_ascii=False,
                ),
                max_output_tokens=5000,
            )
            payload = _json_block(raw)
            try:
                result = AgentWorkOrderResultRequest.model_validate_json(payload)
            except ValueError as result_error:
                if not may_call_tool or self._tools is None:
                    raise ProductionManagerError(
                        "Specialist Agent returned an invalid AgentResult payload"
                    ) from result_error
                try:
                    tool_request = SpecialistToolRequest.model_validate_json(payload)
                except ValueError as tool_error:
                    raise ProductionManagerError(
                        "Specialist Agent returned neither a Tool call nor an AgentResult"
                    ) from tool_error
                arguments = cast(Mapping[str, object], tool_request.arguments)
                scoped_invoker = self._scoped_tool_invoker
                tool_result = (
                    scoped_invoker(
                        work_order.context.project_id,
                        tool_request.tool_name,
                        arguments,
                    )
                    if scoped_invoker is not None
                    else self._tools.invoke(tool_request.tool_name, arguments)
                )
                evidence_refs.extend(tool_result.artifact_refs)
                tool_history.append(
                    {
                        "request": tool_request.model_dump(mode="json"),
                        "result": tool_result.model_dump(mode="json"),
                    }
                )
                continue

            if evidence_refs:
                merged_refs = _unique_artifact_refs(
                    [*result.evidence_refs, *evidence_refs]
                )
                result = result.model_copy(update={"evidence_refs": merged_refs})
            return result

        raise ProductionManagerError("Specialist Agent exhausted its bounded Tool loop")


def default_specialist_agent_factory(
    client: LanguageProviderClient,
) -> SpecialistAgent:
    return LanguageSpecialistAgent(client)


def _current_script(document: ProjectDocument):
    return next(
        (
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        ),
        None,
    )


def _current_bible(document: ProjectDocument):
    return next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )


class ProjectArtifactResolver:
    """Resolve focused summaries from the canonical ProjectDocument."""

    def __init__(self, projects: ProjectDocumentStore) -> None:
        self._projects = projects

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        document = self._projects.get_project(ref.project_id)
        if document is None:
            return None
        if ref.artifact_type == "project":
            return ArtifactSummary(
                ref=ref,
                title=document.project.name,
                summary=_project_summary(document),
            )
        if ref.artifact_type == "planning_run":
            planning_run = self._projects.get_planning_run(ref.artifact_id)
            if (
                planning_run is None
                or planning_run.id != ref.artifact_id
                or planning_run.project_id != ref.project_id
            ):
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Planning run {planning_run.current_step.value}",
                summary=planning_run.model_dump_json(),
            )
        if ref.artifact_type in {"script", "script_version"}:
            script = next(
                (
                    item
                    for item in document.script_versions
                    if item.id == ref.artifact_id
                    or (ref.version is not None and item.version == ref.version)
                ),
                None,
            )
            if script is None:
                return None
            scenes: list[str] = []
            line_by_id = {line.id: line for line in script.lines}
            for scene in script.structured_scenes:
                lines = [
                    f"{line_by_id[line_id].speaker}: {line_by_id[line_id].spoken_text}"
                    for line_id in scene.line_ids
                    if line_id in line_by_id
                ]
                scenes.append(
                    f"{scene.id} {scene.heading} | {scene.summary} | " + " / ".join(lines)
                )
            return ArtifactSummary(
                ref=ref,
                title=f"Script v{script.version}",
                summary="\n".join(scenes) or script.spoken_text,
            )
        if ref.artifact_type in {"bible", "bible_version"}:
            bible = next(
                (
                    item
                    for item in document.bible_versions
                    if item.id == ref.artifact_id
                    or (ref.version is not None and item.version == ref.version)
                ),
                None,
            )
            if bible is None:
                return None
            summary = {
                "characters": [item.model_dump(mode="json") for item in bible.characters],
                "locations": [item.model_dump(mode="json") for item in bible.locations],
                "props": [item.model_dump(mode="json") for item in bible.props],
                "style": bible.style.model_dump(mode="json"),
            }
            return ArtifactSummary(
                ref=ref,
                title=f"Project Bible v{bible.version}",
                summary=json.dumps(summary, ensure_ascii=False),
            )
        if ref.artifact_type == "scene":
            script = _current_script(document)
            if script is None:
                return None
            scene = next(
                (item for item in script.structured_scenes if item.id == ref.artifact_id),
                None,
            )
            if scene is None:
                return None
            line_by_id = {line.id: line for line in script.lines}
            lines = [
                line_by_id[line_id].model_dump(mode="json")
                for line_id in scene.line_ids
                if line_id in line_by_id
            ]
            return ArtifactSummary(
                ref=ref,
                title=scene.heading,
                summary=json.dumps(
                    {**scene.model_dump(mode="json"), "lines": lines},
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "shot":
            shot = next((item for item in document.shots if item.id == ref.artifact_id), None)
            if shot is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Shot {shot.sequence}",
                summary=shot.model_dump_json(),
            )
        if ref.artifact_type == "character":
            bible = _current_bible(document)
            character = None if bible is None else next(
                (item for item in bible.characters if item.id == ref.artifact_id),
                None,
            )
            if character is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=character.name,
                summary=character.model_dump_json(),
            )
        if ref.artifact_type in {"asset", "asset_version"}:
            asset = next(
                (
                    item
                    for item in document.assets
                    if item.id == ref.artifact_id
                    or any(version.id == ref.artifact_id for version in item.versions)
                ),
                None,
            )
            if asset is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=asset.name,
                summary=asset.model_dump_json(),
            )
        if ref.artifact_type in {"reference_pack", "reference_pack_entry"}:
            pack = next(
                (
                    item
                    for item in document.reference_packs
                    if item.id == ref.artifact_id
                    or any(entry.id == ref.artifact_id for entry in item.entries)
                ),
                None,
            )
            if pack is None:
                return None
            if ref.artifact_type == "reference_pack_entry":
                entry = next(
                    (item for item in pack.entries if item.id == ref.artifact_id),
                    None,
                )
                if entry is None:
                    return None
                return ArtifactSummary(
                    ref=ref,
                    title=f"Reference pack entry {entry.id}",
                    summary=entry.model_dump_json(),
                )
            return ArtifactSummary(
                ref=ref,
                title=pack.name,
                summary=pack.model_dump_json(),
            )
        if ref.artifact_type in {"timeline", "timeline_manifest"}:
            manifest = next(
                (item for item in document.manifests if item.id == ref.artifact_id),
                None,
            )
            if manifest is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Timeline v{manifest.version}",
                summary=manifest.model_dump_json(),
            )
        if ref.artifact_type == "generation_task":
            return _artifact_record_summary(ref, self._projects.get_task(ref.artifact_id))
        if ref.artifact_type == "image_task":
            return _artifact_record_summary(
                ref,
                self._projects.get_image_task(ref.artifact_id),
            )
        if ref.artifact_type == "take":
            return _artifact_record_summary(ref, self._projects.get_take(ref.artifact_id))

        records = cast(ProjectArtifactRecordStore, self._projects)
        if ref.artifact_type == "h3_route_plan":
            return _artifact_record_summary(
                ref,
                records.get_h3_route_plan(ref.artifact_id),
            )
        if ref.artifact_type == "video_inspection":
            return _artifact_record_summary(
                ref,
                records.get_video_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "take_comparison":
            return _artifact_record_summary(
                ref,
                records.get_take_comparison(ref.artifact_id),
            )
        if ref.artifact_type == "audio_inspection":
            return _artifact_record_summary(
                ref,
                records.get_audio_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "episode_review":
            return _artifact_record_summary(
                ref,
                records.get_episode_review(ref.artifact_id),
            )

        collection_loaders: dict[
            str,
            Callable[[str], Sequence[ArtifactRecordLike]],
        ] = {
            "export": records.list_exports,
            "h3_provider_run": records.list_provider_runs,
            "route_evidence": records.list_route_evidence,
            "reference_visual_rerank": records.list_reference_visual_reranks,
            "keyframe_comparison": records.list_keyframe_comparisons,
            "input_compatibility": records.list_input_compatibility_reports,
        }
        loader = collection_loaders.get(ref.artifact_type)
        if loader is not None:
            record = next(
                (item for item in loader(ref.project_id) if item.id == ref.artifact_id),
                None,
            )
            return _artifact_record_summary(ref, record)
        return None


def _artifact_record_summary(
    ref: ArtifactRef,
    record: ArtifactRecordLike | None,
) -> ArtifactSummary | None:
    if (
        record is None
        or record.id != ref.artifact_id
        or record.project_id != ref.project_id
    ):
        return None
    return ArtifactSummary(
        ref=ref,
        title=ref.artifact_type.replace("_", " ").title(),
        summary=record.model_dump_json(),
    )


def _project_refs(document: ProjectDocument) -> list[ArtifactRef]:
    refs = [
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id=document.project.id,
        )
    ]
    script = _current_script(document)
    if script is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="script_version",
                artifact_id=script.id,
                version=script.version,
            )
        )
    bible = _current_bible(document)
    if bible is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="bible_version",
                artifact_id=bible.id,
                version=bible.version,
            )
        )
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="scene",
            artifact_id=scene.id,
            version=None if script is None else script.version,
        )
        for scene in ([] if script is None else script.structured_scenes)
    )
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
        for shot in document.shots
    )
    return refs


def _project_summary(document: ProjectDocument) -> str:
    script = _current_script(document)
    bible = _current_bible(document)
    scene_summaries = [] if script is None else [
        f"{scene.id}:{scene.heading}:{scene.summary}" for scene in script.structured_scenes
    ]
    characters = [] if bible is None else [
        f"{item.id}:{item.name}:{item.performance}" for item in bible.characters
    ]
    shot_summaries = [
        f"{shot.id}#{shot.sequence}:{shot.narrative_purpose}:{shot.visual}"
        for shot in document.shots
    ]
    planning_summary = "Planning metadata unavailable"
    if document.planning is not None:
        planning = document.planning
        warning_summary = " | ".join(planning.warnings) or "none"
        planning_summary = (
            f"Planning {planning.status.value}; planner {planning.planner or 'pending'}; "
            f"run {planning.run_id or 'none'}; warnings {warning_summary}"
        )
    return "\n".join(
        [
            f"Project {document.project.name} ({document.project.id})",
            f"Profile {document.project.production_profile.value}; stage {document.project.stage.value}; "
            f"language {document.project.language}; aspect {document.project.aspect_ratio}; "
            f"target {document.project.target_duration_seconds}s",
            "Characters: " + " | ".join(characters),
            "Scenes: " + " | ".join(scene_summaries),
            "Shots: " + " | ".join(shot_summaries),
            f"Assets {len(document.assets)}; manifests {len(document.manifests)}; "
            f"current script {document.project.current_script_version_id}; "
            f"current bible {document.project.current_bible_version_id}",
            planning_summary,
        ]
    )


def _role_for_recipient(recipient: str) -> ProductionSkillRole:
    normalized = recipient.casefold().replace("_", "-")
    if "review-router" in normalized:
        return ProductionSkillRole.REVIEW_ROUTER
    if "director" in normalized:
        return ProductionSkillRole.DIRECTOR
    if "asset" in normalized or "design" in normalized:
        return ProductionSkillRole.ASSET_REVIEWER
    if "route" in normalized:
        return ProductionSkillRole.ROUTE_PLANNER
    if "video" in normalized or "prompt" in normalized:
        return ProductionSkillRole.VIDEO_PROMPT_COMPILER
    if "episode" in normalized or "final" in normalized:
        return ProductionSkillRole.EPISODE_REVIEWER
    if "critic" in normalized or "review" in normalized:
        return ProductionSkillRole.CRITIC
    if "concept" in normalized:
        return ProductionSkillRole.CONCEPT
    if "writer" in normalized or "story" in normalized or "script" in normalized:
        return ProductionSkillRole.WRITER
    return ProductionSkillRole.PLANNER


class ProductionManager:
    """Coordinate dynamic Agent plans without becoming a workflow engine."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        tools: ToolRegistry,
        projects: ProjectDocumentStore,
        language_client_resolver: LanguageClientResolver,
        planner_factory: ProductionPlannerFactory = default_production_planner_factory,
        specialist_agent_factory: SpecialistAgentFactory = (
            default_specialist_agent_factory
        ),
    ) -> None:
        self.runtime = runtime
        self.tools = tools
        self.projects = projects
        self._language_client_resolver = language_client_resolver
        self._planner_factory = planner_factory
        self._specialist_agent_factory = specialist_agent_factory
        self._run_locks_guard = threading.RLock()
        self._run_locks: dict[str, threading.Lock] = {}
        self._recover_interrupted_operations()

    def capability_catalog(self) -> AgentCapabilityCatalog:
        roles = [role.value for role in ProductionSkillRole]
        return AgentCapabilityCatalog(
            tools=self.tools.list(),
            skills=[
                ProductionSkillDescriptor(
                    id=production_skill_id(skill.profile),
                    profile=skill.profile,
                    label=skill.label,
                    medium=skill.medium,
                    recommended_playbook=list(skill.recommended_playbook),
                    role_names=roles,
                )
                for skill in production_skill_catalog()
            ],
        )

    def start_project_run(
        self,
        request: AgentProjectCreateRequest,
    ) -> AgentProjectCreateResponse:
        """Create a run first and let its initial Tool step create the project."""

        if not self.tools.contains("project.create"):
            raise ProductionManagerError("The shared Tool Registry has no project.create Tool")
        project_id = f"project-{uuid4().hex[:12]}"
        objective = request.objective or (
            f"Create and produce {request.project.production_profile.value} project "
            f"{project_id} from the supplied input; execution target is "
            f"{request.execution_target.value} and render_export="
            f"{'true' if request.render_export else 'false'}."
        )
        run = self.runtime.create_run(
            project_id=project_id,
            objective=objective,
            root_agent=request.root_agent,
            active_skill_ids=[production_skill_id(request.project.production_profile)],
            unattended=request.project.run_mode == RunMode.AUTO_DRAFT,
            execution_target=request.execution_target,
        )
        self.runtime.remember_decision(
            project_id=project_id,
            run_id=run.id,
            statement=f"Execution target: {request.execution_target.value}",
            rationale="Selected for this bootstrap production run.",
        )
        self.runtime.remember_decision(
            project_id=project_id,
            run_id=run.id,
            statement=f"Render export: {'yes' if request.render_export else 'no'}",
            rationale="Selected for this bootstrap production run.",
        )
        project_ref = ArtifactRef(
            project_id=project_id,
            artifact_type="project",
            artifact_id=project_id,
        )
        proposal = AgentPlanProposal(
            summary="Create the canonical project, then inspect its latest context and continue.",
            goal_complete=False,
            steps=[
                PlanStep(
                    id="create-project",
                    title="Create the canonical project",
                    kind=ActionKind.TOOL_CALL,
                    assigned_agent="production-manager",
                    tool_name="project.create",
                    instructions=(
                        "Create the project through the shared Tool Registry using the "
                        "preallocated project id."
                    ),
                    arguments={
                        "project_id": project_id,
                        "owner_agent_run_id": run.id,
                        "request": request.project.model_dump(mode="json"),
                    },
                    artifact_refs=[project_ref],
                )
            ],
        )
        self._replace_plan(run.id, proposal)
        self.runtime.checkpoint(
            run.id,
            current_focus=proposal.summary,
            next_actions=[proposal.steps[0].title],
            artifact_refs=[project_ref],
        )
        advanced = self._advance_unlocked(
            run.id,
            AgentRunAdvanceRequest(max_actions=1),
        )
        create_action = next(
            (
                action
                for action in self.runtime.store.list_actions(run.id)
                if action.plan_revision_id is not None
                and action.plan_step_id == "create-project"
            ),
            None,
        )
        if create_action is None or create_action.status != ActionStatus.SUCCEEDED:
            detail = (
                "project.create did not record a successful action"
                if create_action is None
                else create_action.error_message or "project.create failed"
            )
            self.runtime.set_run_status(run.id, AgentRunStatus.FAILED)
            self.runtime.checkpoint(
                run.id,
                current_focus="Create the canonical project before production can continue.",
                blockers=[detail],
                next_actions=["Correct the project input and start a new Agent project run."],
                artifact_refs=[project_ref],
            )
            raise ProductionManagerError(detail)
        if self.projects.get_project(project_id) is None:
            raise ProductionManagerError(
                "project.create did not produce the preallocated canonical project"
            )
        return AgentProjectCreateResponse(project_id=project_id, run=advanced.run)

    def start_run(
        self,
        project_id: str,
        request: AgentRunCreateRequest,
    ) -> AgentRunView:
        if request.unattended and request.execution_target is None:
            raise ProductionManagerError(
                "Unattended Agent runs require an explicit execution target"
            )
        document = self._require_project(project_id)
        self.refresh_project_memory(document)
        run = self.runtime.create_run(
            project_id=project_id,
            objective=request.objective,
            root_agent=request.root_agent,
            active_skill_ids=[production_skill_id(document.project.production_profile)],
            unattended=request.unattended,
            execution_target=request.execution_target,
        )
        if request.execution_target is not None:
            self.runtime.remember_decision(
                project_id=project_id,
                run_id=run.id,
                statement=f"Execution target: {request.execution_target.value}",
                rationale="Selected for this Agent run.",
            )
        if request.render_export is not None:
            self.runtime.remember_decision(
                project_id=project_id,
                run_id=run.id,
                statement=f"Render export: {'yes' if request.render_export else 'no'}",
                rationale="Selected for this Agent run.",
            )
        refs = _project_refs(document)
        self.runtime.checkpoint(
            run.id,
            current_focus=request.objective,
            next_actions=["Ask the Production Manager Agent to create a plan"],
            artifact_refs=refs,
        )
        if request.initial_plan is not None:
            proposal = self._planning_precondition_plan(
                agent_run_id=run.id,
                document=document,
            )
            self._replace_plan(
                run.id,
                request.initial_plan if proposal is None else proposal,
            )
        elif request.auto_plan:
            try:
                self.replan(
                    run.id,
                    AgentRunReplanRequest(reason="Create the initial plan for this objective"),
                )
            except ProductionManagerError as exc:
                self.runtime.set_run_status(run.id, AgentRunStatus.WAITING)
                self.runtime.checkpoint(
                    run.id,
                    current_focus=request.objective,
                    blockers=[str(exc)],
                    next_actions=["Configure a language planner or submit a proposed plan"],
                    artifact_refs=refs,
                )
        return self.get_run(run.id)

    def list_runs(self, project_id: str) -> AgentRunListResponse:
        self._require_project(project_id)
        return AgentRunListResponse(
            runs=[self.get_run(run.id) for run in self.runtime.store.list_runs(project_id)]
        )

    def get_run(self, run_id: str) -> AgentRunView:
        snapshot = self.runtime.snapshot(run_id)
        requested_refs: list[ArtifactRef] = []
        if snapshot.current_plan is not None:
            for step in snapshot.current_plan.steps:
                requested_refs.extend(step.artifact_refs)
        return AgentRunView(
            snapshot=snapshot,
            ready_steps=self.runtime.ready_steps(run_id),
            context=self.runtime.context(
                run_id,
                requested_artifact_refs=requested_refs,
            ),
        )

    def replan(
        self,
        run_id: str,
        request: AgentRunReplanRequest,
    ) -> AgentRunView:
        with self._run_lock(run_id):
            return self._replan_unlocked(run_id, request)

    def _replan_unlocked(
        self,
        run_id: str,
        request: AgentRunReplanRequest,
    ) -> AgentRunView:
        snapshot = self.runtime.snapshot(run_id)
        document = self._require_project(snapshot.run.project_id)
        self.refresh_project_memory(document)
        proposal = self._planning_precondition_plan(
            agent_run_id=run_id,
            document=document,
        )
        if proposal is None:
            proposal = request.proposed_plan
        if proposal is None:
            client = self._language_client_resolver()
            current = (
                None
                if snapshot.current_plan is None
                else AgentPlanProposal(
                    summary=snapshot.current_plan.summary,
                    steps=snapshot.current_plan.steps,
                    goal_complete=snapshot.current_plan.goal_complete,
                )
            )
            profile = document.project.production_profile
            compiled = compile_production_prompt(
                profile,
                roles=(
                    ProductionSkillRole.PLANNER,
                    ProductionSkillRole.REVIEW_ROUTER,
                    ProductionSkillRole.DIRECTOR,
                ),
            )
            planner: ProductionPlanner = (
                LocalProductionPlanner(self.projects)
                if client is None
                else self._planner_factory(client)
            )
            proposal = planner.propose_plan(
                context=self.get_run(run_id).context,
                skill_prompt=compiled.text,
                tools=_tools_for_execution_target(
                    self.tools.list(),
                    snapshot.run.execution_target,
                ),
                current_plan=current,
                reason=request.reason,
            )
        self._replace_plan(run_id, proposal)
        self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
        self.runtime.checkpoint(
            run_id,
            current_focus=proposal.summary,
            next_actions=[step.title for step in self.runtime.ready_steps(run_id)],
            artifact_refs=[ref for step in proposal.steps for ref in step.artifact_refs],
        )
        return self.get_run(run_id)

    def _planning_precondition_plan(
        self,
        *,
        agent_run_id: str,
        document: ProjectDocument,
        retry_after_user_confirmation: bool = False,
    ) -> AgentPlanProposal | None:
        """Keep persisted long-form planning ahead of downstream production work."""

        planning = document.planning
        if planning is None or planning.status == PlanningRunStatus.COMPLETE:
            return None

        if planning.run_id is None:
            return AgentPlanProposal(
                summary=(
                    "Content planning is incomplete but has no persisted planning cursor."
                ),
                steps=[
                    PlanStep(
                        id="resolve-missing-planning-cursor",
                        title="Resolve the missing planning cursor",
                        kind=ActionKind.USER_INPUT,
                        assigned_agent="production-manager",
                        instructions=(
                            "Inspect the project planning metadata and restart content "
                            "planning before production continues."
                        ),
                        artifact_refs=[
                            ArtifactRef(
                                project_id=document.project.id,
                                artifact_type="project",
                                artifact_id=document.project.id,
                            )
                        ],
                    )
                ],
            )

        planning_ref = ArtifactRef(
            project_id=document.project.id,
            artifact_type="planning_run",
            artifact_id=planning.run_id,
        )
        if planning.owner_agent_run_id != agent_run_id:
            return AgentPlanProposal(
                summary=(
                    "Content planning is still owned by another Agent run; wait for that "
                    "persisted planning cursor before starting production."
                ),
                steps=[
                    PlanStep(
                        id="wait-for-owner-planning-run",
                        title="Wait for the owning planning run",
                        kind=ActionKind.USER_INPUT,
                        assigned_agent="production-manager",
                        instructions=(
                            "Continue after the owning Agent run completes or repairs the "
                            "persisted content-planning run."
                        ),
                        artifact_refs=[planning_ref],
                    )
                ],
            )

        if planning.status in {
            PlanningRunStatus.FAILED,
            PlanningRunStatus.NEEDS_ATTENTION,
        } and not retry_after_user_confirmation:
            failed = planning.status == PlanningRunStatus.FAILED
            return AgentPlanProposal(
                summary=(
                    "The persisted content-planning run failed and needs a user decision "
                    "before production can continue."
                    if failed
                    else "The current content-planning unit needs attention before it is "
                    "retried."
                ),
                steps=[
                    PlanStep(
                        id=(
                            "resolve-failed-planning-run"
                            if failed
                            else "repair-planning-run"
                        ),
                        title=(
                            "Resolve the failed planning run"
                            if failed
                            else "Repair the current planning unit"
                        ),
                        kind=ActionKind.USER_INPUT,
                        assigned_agent="production-manager",
                        instructions=(
                            "Review the planning failure, correct the input or provider, then "
                            "resume from the persisted project context."
                            if failed
                            else "Correct the provider or project input, then explicitly "
                            "continue from the persisted planning cursor."
                        ),
                        artifact_refs=[planning_ref],
                    )
                ],
            )

        if not self.tools.contains("planning.advance"):
            raise ProductionManagerError(
                "The shared Tool Registry has no planning.advance Tool"
            )
        retrying = planning.status in {
            PlanningRunStatus.FAILED,
            PlanningRunStatus.NEEDS_ATTENTION,
        }
        return AgentPlanProposal(
            summary=(
                "Retry the current persisted content-planning unit."
                if retrying
                else "Advance the persisted content plan by one bounded unit."
            ),
            steps=[
                PlanStep(
                    id="advance-content-planning",
                    title=(
                        "Retry the current planning unit"
                        if retrying
                        else "Advance content planning"
                    ),
                    kind=ActionKind.TOOL_CALL,
                    assigned_agent="production-manager",
                    tool_name="planning.advance",
                    instructions=(
                        "Advance exactly one persisted planning unit and keep its returned "
                        "cursor as the source of truth."
                    ),
                    arguments={
                        "project_id": document.project.id,
                        "run_id": planning.run_id,
                    },
                    artifact_refs=[planning_ref],
                )
            ],
        )

    def advance(
        self,
        run_id: str,
        request: AgentRunAdvanceRequest,
    ) -> AgentAdvanceResponse:
        with self._run_lock(run_id):
            return self._advance_unlocked(run_id, request)

    def _advance_unlocked(
        self,
        run_id: str,
        request: AgentRunAdvanceRequest,
    ) -> AgentAdvanceResponse:
        snapshot = self.runtime.snapshot(run_id)
        if snapshot.run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            raise ProductionManagerError(
                f"Agent run is already {snapshot.run.status.value}"
            )
        if snapshot.current_plan is None:
            raise ProductionManagerError("Agent run has no active plan")

        self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
        executed: list[str] = []
        work_orders: list[str] = []
        notes: list[str] = []
        remaining_actions = request.max_actions
        automatic_replans = 0
        max_automatic_replans = 4

        while True:
            current = self.runtime.snapshot(run_id)
            plan = current.current_plan
            if plan is None:
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                break

            failed_actions = self._failed_actions_for_plan(current, plan.id)
            if failed_actions:
                if automatic_replans >= max_automatic_replans:
                    self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                    notes.append("Automatic replanning stopped at the bounded retry limit")
                    break
                reason = "Revise the plan after failed actions: " + "; ".join(
                    action.error_message or action.input_summary
                    for action in failed_actions[-4:]
                )
                if not self._automatic_replan_unlocked(run_id, reason, notes):
                    break
                automatic_replans += 1
                continue

            ready = self.runtime.ready_steps(run_id)
            if ready and remaining_actions > 0:
                batch = ready[:remaining_actions]
                made_progress = False
                self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                for step in batch:
                    action, work_order, note = self._execute_step(
                        run_id,
                        plan.id,
                        step,
                    )
                    executed.append(action.id)
                    remaining_actions -= 1
                    if action.status == ActionStatus.SUCCEEDED:
                        made_progress = True
                    if work_order is not None:
                        work_orders.append(work_order.id)
                    if note:
                        notes.append(note)
                # This limit protects against replanning without progress. A successful
                # action starts a fresh bounded segment; max_actions still caps the call.
                if made_progress:
                    automatic_replans = 0
                continue

            if ready:
                self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                break

            if PlanGraph(plan).is_complete(current.actions):
                if plan.goal_complete:
                    self.runtime.set_run_status(run_id, AgentRunStatus.COMPLETED)
                    notes.append("The Agent confirmed that the run objective is complete")
                    break
                if automatic_replans >= max_automatic_replans:
                    self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                    notes.append("Automatic replanning stopped at the bounded retry limit")
                    break
                if not self._automatic_replan_unlocked(
                    run_id,
                    "The bounded plan completed; inspect the latest ContextPack and continue "
                    "toward the run objective.",
                    notes,
                ):
                    break
                automatic_replans += 1
                continue

            self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
            if any(
                action.status == ActionStatus.RUNNING
                and action.plan_revision_id == plan.id
                for action in current.actions
            ):
                notes.append("Waiting for delegated Agent or user input")
            else:
                notes.append("The current plan has no runnable step and needs revision")
            break

        view = self.get_run(run_id)
        active_plan_id = (
            None
            if view.snapshot.current_plan is None
            else view.snapshot.current_plan.id
        )
        self.runtime.checkpoint(
            run_id,
            current_focus=(
                view.snapshot.current_plan.summary
                if view.snapshot.current_plan is not None
                else view.snapshot.run.objective
            ),
            blockers=[
                action.error_message
                for action in view.snapshot.actions
                if action.status == ActionStatus.FAILED
                and action.plan_revision_id == active_plan_id
                and action.error_message
            ][-4:],
            open_questions=[
                action.input_summary
                for action in view.snapshot.actions
                if action.kind == ActionKind.USER_INPUT
                and action.status == ActionStatus.RUNNING
            ],
            next_actions=[step.title for step in view.ready_steps],
            artifact_refs=[
                ref for action in view.snapshot.actions[-8:] for ref in action.artifact_refs
            ],
        )
        return AgentAdvanceResponse(
            run=self.get_run(run_id),
            executed_action_ids=executed,
            created_work_order_ids=work_orders,
            notes=notes,
        )

    @staticmethod
    def _failed_actions_for_plan(
        snapshot: AgentRunSnapshot,
        plan_revision_id: str,
    ) -> list[ActionRecord]:
        return [
            action
            for action in snapshot.actions
            if action.plan_revision_id == plan_revision_id
            and action.status == ActionStatus.FAILED
        ]

    def _automatic_replan_unlocked(
        self,
        run_id: str,
        reason: str,
        notes: list[str],
    ) -> bool:
        snapshot = self.runtime.snapshot(run_id)
        active_plan_id = (
            None if snapshot.current_plan is None else snapshot.current_plan.id
        )
        blockers = [
            action.error_message
            for action in snapshot.actions
            if action.status == ActionStatus.FAILED
            and action.plan_revision_id == active_plan_id
            and action.error_message
        ][-4:]
        self.runtime.checkpoint(
            run_id,
            current_focus=reason,
            blockers=blockers,
            artifact_refs=[
                ref for action in snapshot.actions[-8:] for ref in action.artifact_refs
            ],
        )
        try:
            self._replan_unlocked(
                run_id,
                AgentRunReplanRequest(reason=reason),
            )
        except Exception as exc:
            self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
            self.runtime.checkpoint(
                run_id,
                current_focus=reason,
                blockers=[*blockers, f"Automatic replanning failed: {exc}"][-4:],
                next_actions=["Revise the plan or configure the planning Agent"],
            )
            notes.append(f"Automatic replanning failed: {exc}")
            return False
        notes.append("The Production Manager revised the plan from the latest ContextPack")
        return True

    def get_work_order(self, work_order_id: str) -> AgentWorkOrderView:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        run = self.runtime.store.get_run(work_order.parent_run_id)
        if run is None:
            raise ProductionManagerError(f"Unknown agent run: {work_order.parent_run_id}")
        document = self._require_project(run.project_id)
        role = _role_for_recipient(work_order.recipient_role)
        prompt = compile_production_prompt(
            document.project.production_profile,
            roles=(role,),
        ).text
        return AgentWorkOrderView(
            work_order=work_order,
            context=self.runtime.work_order_context(work_order_id),
            system_prompt=prompt,
        )

    def submit_work_order_result(
        self,
        work_order_id: str,
        request: AgentWorkOrderResultRequest,
    ) -> AgentWorkOrderResultResponse:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        with self._run_lock(work_order.parent_run_id):
            return self._submit_work_order_result_unlocked(work_order_id, request)

    def _submit_work_order_result_unlocked(
        self,
        work_order_id: str,
        request: AgentWorkOrderResultRequest,
    ) -> AgentWorkOrderResultResponse:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        if work_order.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
        }:
            raise ProductionManagerError(
                f"Work order is already {work_order.status.value}"
            )
        run = self.runtime.store.get_run(work_order.parent_run_id)
        if run is None:
            raise ProductionManagerError(f"Unknown agent run: {work_order.parent_run_id}")
        result_refs = [
            *request.created_artifacts,
            *request.updated_artifacts,
            *request.evidence_refs,
        ]
        if any(ref.project_id != run.project_id for ref in result_refs):
            raise ProductionManagerError(
                "Agent result contains an artifact outside the current run project"
            )
        try:
            result = self.runtime.record_agent_result(
                work_order_id,
                status=request.status,
                concise_summary=request.concise_summary,
                created_artifacts=request.created_artifacts,
                updated_artifacts=request.updated_artifacts,
                findings=request.findings,
                decisions=request.decisions,
                assumptions=request.assumptions,
                unresolved_questions=request.unresolved_questions,
                evidence_refs=request.evidence_refs,
                recommended_next_actions=request.recommended_next_actions,
            )
        except AgentResultContractError as exc:
            raise ProductionManagerError(
                f"Agent result does not satisfy the WorkOrder contract: {exc}"
            ) from exc
        action = next(
            (
                item
                for item in self.runtime.store.list_actions(work_order.parent_run_id)
                if item.work_order_id == work_order_id and item.status == ActionStatus.RUNNING
            ),
            None,
        )
        refs = [*request.created_artifacts, *request.updated_artifacts]
        if action is not None:
            if request.status == AgentResultStatus.COMPLETED:
                self.runtime.complete_action(
                    action.id,
                    output_summary=request.concise_summary,
                    artifact_refs=refs,
                )
            elif request.status == AgentResultStatus.FAILED:
                self.runtime.fail_action(
                    action.id,
                    error_message=request.concise_summary,
                    output_summary=request.concise_summary,
                )
        for decision in request.decisions:
            self.runtime.remember_decision(
                project_id=run.project_id,
                run_id=run.id,
                statement=decision,
                rationale=f"Returned by {work_order.recipient_role}: {request.concise_summary}",
                artifact_refs=refs,
            )
        document = self._require_project(run.project_id)
        self.refresh_project_memory(document)
        if request.status == AgentResultStatus.COMPLETED:
            if request.findings or request.decisions:
                self.runtime.distill_experience_from_result(
                    work_order_id,
                    applicability=(
                        f"{work_order.recipient_role}: {work_order.objective}"
                    ),
                    tags=[
                        work_order.recipient_role,
                        document.project.production_profile.value,
                    ],
                )
            self._settle_run_after_external_action(
                run.id,
                reason=(
                    f"The {work_order.recipient_role} completed its WorkOrder; inspect "
                    "the returned evidence and continue toward the run objective."
                ),
            )
            ready_steps = self.runtime.ready_steps(run.id)
            current_plan = self.runtime.store.get_current_plan(run)
            self.runtime.checkpoint(
                run.id,
                current_focus=current_plan.summary if current_plan is not None else run.objective,
                next_actions=[step.title for step in ready_steps],
                artifact_refs=refs,
            )
        elif request.status == AgentResultStatus.PARTIAL:
            self.runtime.set_run_status(run.id, AgentRunStatus.WAITING)
            self.runtime.checkpoint(
                run.id,
                current_focus=request.concise_summary,
                open_questions=request.unresolved_questions,
                next_actions=request.recommended_next_actions,
                artifact_refs=refs,
            )
        else:
            self._settle_run_after_external_action(
                run.id,
                reason=(
                    f"The {work_order.recipient_role} could not complete its WorkOrder; "
                    "revise the plan from the returned findings."
                ),
            )
            self.runtime.checkpoint(
                run.id,
                current_focus=request.concise_summary,
                blockers=[request.concise_summary],
                next_actions=[step.title for step in self.runtime.ready_steps(run.id)],
                artifact_refs=refs,
            )
        return AgentWorkOrderResultResponse(result=result, run=self.get_run(run.id))

    def execute_work_order(self, work_order_id: str) -> AgentWorkOrderResultResponse:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        client = self._language_client_resolver()
        if client is None:
            raise ProductionManagerError(
                "No language provider is configured for the specialist Agent"
            )
        with self._run_lock(work_order.parent_run_id):
            current = self.runtime.store.get_work_order(work_order_id)
            if current is None:
                raise ProductionManagerError(f"Unknown work order: {work_order_id}")
            if current.status not in {
                DelegationStatus.PENDING,
                DelegationStatus.NEEDS_ATTENTION,
            }:
                raise ProductionManagerError(
                    f"Work order cannot be executed from {current.status.value}"
                )
            self.runtime.begin_work_order(work_order_id)
            view = self.get_work_order(work_order_id)
            parent_run = self.runtime.snapshot(work_order.parent_run_id).run
        try:
            agent = self._specialist_agent_factory(client)
            if isinstance(agent, LanguageSpecialistAgent):
                def invoke_for_parent_run(
                    project_id: str,
                    tool_name: str,
                    arguments: Mapping[str, object],
                ) -> ToolResult:
                    return self._invoke_scoped_tool(
                        project_id,
                        tool_name,
                        arguments,
                        agent_run_id=parent_run.id,
                    )

                agent.bind_tools(
                    self.tools,
                    scoped_invoker=invoke_for_parent_run,
                    descriptors=_tools_for_execution_target(
                        self.tools.list(),
                        parent_run.execution_target,
                    ),
                )
            result = agent.execute(view)
        except Exception as exc:
            with self._run_lock(work_order.parent_run_id):
                failed = self.runtime.store.get_work_order(work_order_id)
                if failed is not None and failed.status == DelegationStatus.IN_PROGRESS:
                    failed.status = DelegationStatus.NEEDS_ATTENTION
                    self.runtime.store.save_work_order(failed)
            if isinstance(exc, ProductionManagerError):
                raise
            raise ProductionManagerError(
                f"Specialist Agent execution failed: {exc}"
            ) from exc
        return self.submit_work_order_result(work_order_id, result)

    def submit_user_input(
        self,
        run_id: str,
        request: AgentUserInputRequest,
    ) -> AgentRunView:
        with self._run_lock(run_id):
            return self._submit_user_input_unlocked(run_id, request)

    def _submit_user_input_unlocked(
        self,
        run_id: str,
        request: AgentUserInputRequest,
    ) -> AgentRunView:
        action = self.runtime.store.get_action(request.action_id)
        if (
            action is None
            or action.run_id != run_id
            or action.kind != ActionKind.USER_INPUT
            or action.status != ActionStatus.RUNNING
        ):
            raise ProductionManagerError("No matching pending USER_INPUT action")
        run = self.runtime.store.get_run(run_id)
        if run is None:
            raise ProductionManagerError(f"Unknown agent run: {run_id}")
        decision_statement = request.answer
        if request.remember_as_decision and action.plan_step_id == "request-production-target":
            target = request.answer.strip().upper()
            if target not in {
                ProductionExecutionTarget.MOCK.value,
                ProductionExecutionTarget.REAL_H3.value,
            }:
                raise ProductionManagerError(
                    "Production target must be MOCK or REAL_H3"
                )
            self.runtime.set_run_execution_target(
                run.id,
                ProductionExecutionTarget(target),
            )
            decision_statement = f"Execution target: {target}"
        self.runtime.complete_action(action.id, output_summary=request.answer)
        if request.remember_as_decision:
            self.runtime.remember_decision(
                project_id=run.project_id,
                run_id=run.id,
                statement=decision_statement,
                rationale=f"User response to: {action.input_summary}",
                artifact_refs=action.artifact_refs,
            )
        if action.plan_step_id in {
            "repair-planning-run",
            "resolve-failed-planning-run",
        }:
            document = self._require_project(run.project_id)
            proposal = self._planning_precondition_plan(
                agent_run_id=run_id,
                document=document,
                retry_after_user_confirmation=True,
            )
            if proposal is None:
                raise ProductionManagerError(
                    "The planning run no longer needs an explicit retry"
                )
            self._replace_plan(run_id, proposal)
            self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
            self.runtime.checkpoint(
                run_id,
                current_focus=proposal.summary,
                next_actions=[step.title for step in self.runtime.ready_steps(run_id)],
                artifact_refs=[
                    ref for step in proposal.steps for ref in step.artifact_refs
                ],
            )
            return self.get_run(run_id)
        self._settle_run_after_external_action(
            run_id,
            reason=(
                "The user supplied the requested decision; inspect the latest ContextPack "
                "and continue toward the run objective."
            ),
        )
        ready_steps = self.runtime.ready_steps(run_id)
        self.runtime.checkpoint(
            run_id,
            current_focus=request.answer,
            next_actions=[step.title for step in ready_steps],
            artifact_refs=action.artifact_refs,
        )
        return self.get_run(run_id)

    def _settle_run_after_external_action(
        self,
        run_id: str,
        *,
        reason: str,
    ) -> AgentRunStatus:
        notes: list[str] = []
        for _index in range(4):
            snapshot = self.runtime.snapshot(run_id)
            plan = snapshot.current_plan
            if plan is None:
                status = AgentRunStatus.WAITING
                self.runtime.set_run_status(run_id, status)
                return status

            failed_actions = self._failed_actions_for_plan(snapshot, plan.id)
            if failed_actions:
                failure_reason = reason + " Failed action details: " + "; ".join(
                    action.error_message or action.input_summary
                    for action in failed_actions[-4:]
                )
                if not self._automatic_replan_unlocked(
                    run_id,
                    failure_reason,
                    notes,
                ):
                    return AgentRunStatus.WAITING
                continue

            if PlanGraph(plan).is_complete(snapshot.actions):
                if plan.goal_complete:
                    status = AgentRunStatus.COMPLETED
                    self.runtime.set_run_status(run_id, status)
                    return status
                if not self._automatic_replan_unlocked(run_id, reason, notes):
                    return AgentRunStatus.WAITING
                continue

            status = (
                AgentRunStatus.RUNNING
                if self.runtime.ready_steps(run_id)
                else AgentRunStatus.WAITING
            )
            self.runtime.set_run_status(run_id, status)
            return status

        self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
        return AgentRunStatus.WAITING

    def refresh_project_memory(self, document: ProjectDocument) -> ProjectMemory:
        memory = ProjectMemory(
            project_id=document.project.id,
            summary=_project_summary(document),
            constraints=[
                f"Use production profile {document.project.production_profile.value}",
                f"Preserve language {document.project.language} unless the user changes it",
                f"Preserve aspect ratio {document.project.aspect_ratio} unless the user changes it",
                "Modify only the artifact branch required by the objective",
            ],
            artifact_refs=_project_refs(document),
        )
        self.runtime.store.save_project_memory(memory)
        return memory

    def _replace_plan(self, run_id: str, proposal: AgentPlanProposal) -> None:
        run = self.runtime.store.get_run(run_id)
        if run is None:
            raise ProductionManagerError(f"Unknown agent run: {run_id}")
        for step in proposal.steps:
            if any(ref.project_id != run.project_id for ref in step.artifact_refs):
                raise ProductionManagerError(
                    f"Plan step {step.id!r} contains an artifact outside the run project"
                )
            if step.kind in {ActionKind.TOOL_CALL, ActionKind.ARTIFACT_PATCH}:
                if not step.tool_name:
                    raise ProductionManagerError(
                        f"{step.kind.value} step {step.id!r} requires tool_name"
                    )
                if not self.tools.contains(step.tool_name):
                    raise ProductionManagerError(
                        f"Plan step {step.id!r} references unknown tool {step.tool_name!r}"
                    )
                self._validate_tool_scope(
                    run.project_id,
                    step.tool_name,
                    step.arguments,
                    agent_run_id=run_id,
                )
        self.runtime.replace_plan(
            run_id,
            summary=proposal.summary,
            steps=proposal.steps,
            goal_complete=proposal.goal_complete,
        )

    def _execute_step(
        self,
        run_id: str,
        plan_revision_id: str,
        step: PlanStep,
    ) -> tuple[ActionRecord, WorkOrder | None, str | None]:
        action = self.runtime.begin_action(
            run_id,
            kind=step.kind,
            agent_id=step.assigned_agent,
            input_summary=step.instructions,
            plan_revision_id=plan_revision_id,
            plan_step_id=step.id,
            tool_name=step.tool_name,
            artifact_refs=step.artifact_refs,
        )
        if step.kind in {ActionKind.TOOL_CALL, ActionKind.ARTIFACT_PATCH}:
            if step.tool_name is None:
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=f"{step.kind.value} step is missing tool_name",
                    ),
                    None,
                    "The Agent plan must be revised",
                )
            run = self.runtime.store.get_run(run_id)
            if run is None:
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=f"Unknown agent run: {run_id}",
                    ),
                    None,
                    "The Agent plan must be revised",
                )
            try:
                result = self._invoke_scoped_tool(
                    run.project_id,
                    step.tool_name,
                    cast(Mapping[str, object], step.arguments),
                    agent_run_id=run_id,
                )
            except ProductionManagerError as exc:
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=str(exc),
                        output_summary=str(exc),
                    ),
                    None,
                    "Tool scope error recorded; the Agent can revise the plan",
                )
            if result.status != ToolCallStatus.SUCCEEDED:
                message = (
                    result.error.message
                    if result.error is not None
                    else f"Tool {step.tool_name} failed with {result.status.value}"
                )
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=message,
                        output_summary=message,
                    ),
                    None,
                    "Tool failure recorded; the Agent can revise the plan",
                )
            summary = json.dumps(result.output or {}, ensure_ascii=False)
            if len(summary) > 1600:
                summary = summary[:1597] + "..."
            completed = self.runtime.complete_action(
                action.id,
                output_summary=summary or f"{step.tool_name} completed",
                artifact_refs=result.artifact_refs,
            )
            document = self.projects.get_project(run.project_id)
            if document is not None:
                self.refresh_project_memory(document)
            return completed, None, None
        if step.kind == ActionKind.DELEGATION:
            arguments = step.arguments
            work_order = self.runtime.create_work_order(
                run_id,
                sender="production-manager",
                recipient_role=step.assigned_agent,
                objective=step.instructions,
                why_now=_string_argument(arguments, "why_now", step.title),
                scope=step.artifact_refs,
                constraints=_string_list_argument(arguments, "constraints"),
                confirmed_decisions=_string_list_argument(
                    arguments, "confirmed_decisions"
                ),
                open_questions=_string_list_argument(arguments, "open_questions"),
                expected_output_schema=_dict_argument(
                    arguments, "expected_output_schema"
                ),
                acceptance_criteria=_string_list_argument(
                    arguments, "acceptance_criteria"
                ),
            )
            action.work_order_id = work_order.id
            self.runtime.store.save_action(action)
            self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
            return action, work_order, "Waiting for the delegated Agent result"
        if step.kind == ActionKind.USER_INPUT:
            self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
            return action, None, "Waiting for user input"
        completed = self.runtime.complete_action(
            action.id,
            output_summary=step.instructions,
            artifact_refs=step.artifact_refs,
        )
        return completed, None, None

    def _require_project(self, project_id: str) -> ProjectDocument:
        document = self.projects.get_project(project_id)
        if document is None:
            raise ProductionManagerError(f"Unknown project: {project_id}")
        return document

    def _validate_tool_scope(
        self,
        project_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        agent_run_id: str | None = None,
    ) -> None:
        if tool_name == "project.create":
            explicit_project_id = arguments.get("project_id")
            if explicit_project_id != project_id:
                raise ProductionManagerError(
                    "project.create requires the preallocated current run project_id"
                )
            if self.projects.get_project(project_id) is not None:
                raise ProductionManagerError(
                    "project.create is only available before the Agent bootstrap project exists"
                )
            owner_agent_run_id = arguments.get("owner_agent_run_id")
            if (
                agent_run_id is not None
                and owner_agent_run_id != agent_run_id
            ):
                raise ProductionManagerError(
                    "project.create owner_agent_run_id must match the current Agent run"
                )

        if tool_name == "planning.advance":
            run_id = arguments.get("run_id")
            document = self.projects.get_project(project_id)
            planning = None if document is None else document.planning
            if not isinstance(run_id, str) or planning is None:
                raise ProductionManagerError(
                    "planning.advance requires the current project's persisted planning run"
                )
            if planning.run_id != run_id:
                raise ProductionManagerError(
                    "planning.advance must target the current project's planning run"
                )
            if planning.owner_agent_run_id is not None:
                if agent_run_id is None:
                    raise ProductionManagerError(
                        "planning.advance must be executed by its owning Production Manager run"
                    )
                if planning.owner_agent_run_id != agent_run_id:
                    raise ProductionManagerError(
                        "planning.advance must be executed by its owning Agent run"
                    )

        for key in ("project_id", "current_project_id"):
            explicit_project_id = arguments.get(key)
            if (
                isinstance(explicit_project_id, str)
                and explicit_project_id != project_id
            ):
                raise ProductionManagerError(
                    "Agent Tool calls must target the current run project"
                )

        task: GenerationTask | None = None
        task_id = arguments.get("task_id")
        if isinstance(task_id, str):
            task = self.projects.get_task(task_id)
            image_task = self.projects.get_image_task(task_id)
            owner_ids = {
                item.project_id for item in (task, image_task) if item is not None
            }
            if any(owner_id != project_id for owner_id in owner_ids):
                raise ProductionManagerError(
                    "Agent Tool task_id must belong to the current run project"
                )

        required_target: ProductionExecutionTarget | None = None
        if tool_name in _MOCK_ONLY_TOOL_IDS:
            required_target = ProductionExecutionTarget.MOCK
        elif tool_name in _REAL_H3_ONLY_TOOL_IDS:
            required_target = ProductionExecutionTarget.REAL_H3
        elif tool_name in _PROVIDER_BOUND_TASK_TOOL_IDS and task is not None:
            required_target = (
                ProductionExecutionTarget.REAL_H3
                if task.provider == "minimax"
                else ProductionExecutionTarget.MOCK
            )
        if required_target is not None and agent_run_id is not None:
            run = self.runtime.store.get_run(agent_run_id)
            if run is None:
                raise ProductionManagerError(f"Unknown agent run: {agent_run_id}")
            if run.execution_target is None:
                raise ProductionManagerError(
                    f"Tool {tool_name!r} requires execution target "
                    f"{required_target.value}, but this Agent run has no target"
                )
            if run.execution_target != required_target:
                raise ProductionManagerError(
                    f"Tool {tool_name!r} requires execution target "
                    f"{required_target.value}, not {run.execution_target.value}"
                )

        take_id = arguments.get("take_id")
        if isinstance(take_id, str):
            take = self.projects.get_take(take_id)
            if take is not None and take.project_id != project_id:
                raise ProductionManagerError(
                    "Agent Tool take_id must belong to the current run project"
                )

    def _invoke_scoped_tool(
        self,
        project_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        agent_run_id: str | None = None,
    ) -> ToolResult:
        self._validate_tool_scope(
            project_id,
            tool_name,
            arguments,
            agent_run_id=agent_run_id,
        )
        result = self.tools.invoke(tool_name, arguments)
        if any(ref.project_id != project_id for ref in result.artifact_refs):
            raise ProductionManagerError(
                "Tool result contains an artifact outside the current run project"
            )
        return result

    def _recover_interrupted_operations(self) -> None:
        """Normalize only states that cannot still be executing after restart."""

        for run in self.runtime.store.list_resumable_runs():
            recovered = False
            recovered_domain_action = False
            for work_order in self.runtime.store.list_work_orders(
                run.id,
                statuses=(DelegationStatus.IN_PROGRESS,),
            ):
                work_order.status = DelegationStatus.NEEDS_ATTENTION
                work_order.updated_at = utc_now()
                self.runtime.store.save_work_order(work_order)
                recovered = True

            for action in self.runtime.store.list_actions(run.id):
                if action.status != ActionStatus.RUNNING:
                    continue
                if action.kind in {ActionKind.TOOL_CALL, ActionKind.ARTIFACT_PATCH}:
                    document = self.projects.get_project(run.project_id)
                    if action.tool_name == "project.create" and document is not None:
                        refs = [
                            ArtifactRef(
                                project_id=run.project_id,
                                artifact_type="project",
                                artifact_id=run.project_id,
                            )
                        ]
                        if (
                            document.planning is not None
                            and document.planning.run_id is not None
                        ):
                            refs.append(
                                ArtifactRef(
                                    project_id=run.project_id,
                                    artifact_type="planning_run",
                                    artifact_id=document.planning.run_id,
                                )
                            )
                        self.runtime.complete_action(
                            action.id,
                            output_summary=(
                                "Reconciled project.create with the persisted canonical "
                                "project after restart."
                            ),
                            artifact_refs=refs,
                        )
                        self.refresh_project_memory(document)
                        recovered = True
                        recovered_domain_action = True
                        continue

                    if (
                        action.tool_name == "planning.advance"
                        and document is not None
                        and document.planning is not None
                        and document.planning.run_id is not None
                        and document.planning.owner_agent_run_id == run.id
                    ):
                        planning_run = self.projects.get_planning_run(
                            document.planning.run_id
                        )
                        if planning_run is not None:
                            self.runtime.complete_action(
                                action.id,
                                output_summary=(
                                    "Reconciled planning.advance with persisted cursor "
                                    f"{planning_run.current_step.value} "
                                    f"({planning_run.status.value}) after restart."
                                ),
                                artifact_refs=[
                                    ArtifactRef(
                                        project_id=run.project_id,
                                        artifact_type="planning_run",
                                        artifact_id=planning_run.id,
                                    )
                                ],
                            )
                            self.refresh_project_memory(document)
                            recovered = True
                            recovered_domain_action = True
                            continue

                    self.runtime.fail_action(
                        action.id,
                        error_message=(
                            "Execution was interrupted before Tool completion was recorded; "
                            "reconcile persisted domain state or revise the plan before retrying."
                        ),
                    )
                    recovered = True
                elif action.kind == ActionKind.CHECKPOINT:
                    self.runtime.fail_action(
                        action.id,
                        error_message="Checkpoint execution was interrupted before completion.",
                    )
                    recovered = True

            if recovered:
                document = self.projects.get_project(run.project_id)
                proposal = (
                    None
                    if not recovered_domain_action or document is None
                    else self._planning_precondition_plan(
                        agent_run_id=run.id,
                        document=document,
                    )
                )
                if proposal is not None:
                    self._replace_plan(run.id, proposal)
                    self.runtime.set_run_status(run.id, AgentRunStatus.RUNNING)
                    self.runtime.checkpoint(
                        run.id,
                        current_focus=proposal.summary,
                        next_actions=[
                            step.title for step in self.runtime.ready_steps(run.id)
                        ],
                        artifact_refs=[
                            ref for step in proposal.steps for ref in step.artifact_refs
                        ],
                    )
                    continue
                self.runtime.set_run_status(run.id, AgentRunStatus.WAITING)
                self.runtime.checkpoint(
                    run.id,
                    current_focus="Resume interrupted Agent work from persisted state.",
                    blockers=[
                        "An in-flight operation was interrupted and needs reconciliation or retry."
                    ],
                    next_actions=[
                        "Inspect the current plan, domain tasks, and WorkOrders before continuing."
                    ],
                )

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(run_id, threading.Lock())


def _string_argument(arguments: dict[str, JsonValue], key: str, default: str) -> str:
    value = arguments.get(key)
    return value if isinstance(value, str) and value.strip() else default


def _string_list_argument(arguments: dict[str, JsonValue], key: str) -> list[str]:
    value = arguments.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _dict_argument(arguments: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = arguments.get(key)
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, JsonValue], value)


__all__ = [
    "AgentAdvanceResponse",
    "AgentCapabilityCatalog",
    "AgentPlanProposal",
    "AgentProjectCreateRequest",
    "AgentProjectCreateResponse",
    "AgentRunAdvanceRequest",
    "AgentRunCreateRequest",
    "AgentRunListResponse",
    "AgentRunReplanRequest",
    "AgentRunView",
    "AgentUserInputRequest",
    "AgentWorkOrderResultRequest",
    "AgentWorkOrderResultResponse",
    "AgentWorkOrderView",
    "LanguageProductionPlanner",
    "LanguageSpecialistAgent",
    "LocalProductionPlanner",
    "ProductionManager",
    "ProductionManagerError",
    "ProductionPlanner",
    "ProductionSkillDescriptor",
    "ProjectArtifactResolver",
    "SpecialistAgent",
    "SpecialistToolRequest",
    "production_skill_id",
]
