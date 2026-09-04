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

from pydantic import Field, JsonValue, model_validator

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
    ContextPack,
    DelegationStatus,
    PlanGraph,
    PlanStep,
    ProjectMemory,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrder,
)
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver,
    project_artifact_refs as _project_refs,
    project_summary as _project_summary,
)
from short_drama.agent_runtime.models import utc_now
from short_drama.image_models import ImageGenerationTask
from short_drama.models import (
    ContentPlanningOutput,
    ProductionExecutionTarget,
    CreateProjectRequest,
    DomainModel,
    GenerationTask,
    PlanningRunRecord,
    PlanningRunStep,
    PlanningRunStatus,
    ProductionProfile,
    ProjectDocument,
    ProjectStage,
    RevisionJudgeRecommendation,
    RunMode,
    SceneTimingAssessmentRecord,
    ScriptReviewDisposition,
    ScriptReviewReport,
    StoryIntent,
    StoryIssueLedgerEntry,
    StoryMetadataRevisionPatch,
    StoryRepairPlan,
    StoryRevisionVerification,
    StorySourceContract,
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
        "route.list_h3_reconciliation",
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


class AgentPlanProposal(DomainModel):
    """A complete, replaceable plan authored by an Agent or supplied by a caller."""

    summary: str = Field(min_length=1, max_length=4000)
    steps: list[PlanStep] = Field(default_factory=list[PlanStep], max_length=48)
    goal_complete: bool = False

    @model_validator(mode="after")
    def validate_progress_path(self) -> AgentPlanProposal:
        if not self.goal_complete and not self.steps:
            raise ValueError(
                "An incomplete Agent plan must contain at least one next step"
            )
        return self


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
    execution_target: ProductionExecutionTarget
    render_export: bool
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


class ContentPlanningTraceView(DomainModel):
    """Structured creative evidence exposed for human-readable Agent traces.

    This projection deliberately excludes retry counters and orchestration plan
    revisions.  It reports the semantic work produced by the creative Agents;
    callers must not treat it as a second source of canonical story content.
    """

    planning_run_id: str
    status: PlanningRunStatus
    current_step: PlanningRunStep
    completed_steps: list[PlanningRunStep]
    source_contract: StorySourceContract | None = None
    story_intent: StoryIntent | None = None
    selected_narrative_skill_ids: list[str] = Field(default_factory=list[str])
    skill_selection_reasons: list[str] = Field(default_factory=list[str])
    narrative_skill_decision: str | None = None
    outline_source: str | None = None
    outline_json: str | None = None
    scene_draft_json: list[str] = Field(default_factory=list[str])
    scene_timing_assessments: list[SceneTimingAssessmentRecord] = Field(
        default_factory=list[SceneTimingAssessmentRecord]
    )
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    issue_ledger: list[StoryIssueLedgerEntry] = Field(
        default_factory=list[StoryIssueLedgerEntry]
    )
    latest_repair_plan: StoryRepairPlan | None = None
    latest_story_metadata_patch: StoryMetadataRevisionPatch | None = None
    latest_revision_verification: StoryRevisionVerification | None = None
    revision_judge: RevisionJudgeRecommendation | None = None
    revision_round: int = Field(ge=0)
    revision_scene_sequences: list[int] = Field(default_factory=list[int])
    revision_changed_scene_sequences: list[int] = Field(default_factory=list[int])
    latest_revision_target_scene_sequences: list[int] = Field(
        default_factory=list[int]
    )
    latest_revision_changed_scene_sequences: list[int] = Field(
        default_factory=list[int]
    )
    critic_used: bool = False
    final_cold_read_used: bool = False
    final_cold_read_report: ScriptReviewReport | None = None
    warnings: list[str] = Field(default_factory=list[str])
    completed_at: str | None = None


class AgentRunView(DomainModel):
    snapshot: AgentRunSnapshot
    ready_steps: list[PlanStep]
    context: ContextPack
    content_planning_trace: ContentPlanningTraceView | None = None


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


def _content_planning_trace_view(
    run: PlanningRunRecord,
) -> ContentPlanningTraceView:
    return ContentPlanningTraceView(
        planning_run_id=run.id,
        status=run.status,
        current_step=run.current_step,
        completed_steps=list(run.completed_steps),
        source_contract=(
            run.source_contract.model_copy(deep=True)
            if run.source_contract is not None
            else None
        ),
        story_intent=(
            run.story_intent.model_copy(deep=True)
            if run.story_intent is not None
            else None
        ),
        selected_narrative_skill_ids=list(run.selected_narrative_skill_ids),
        skill_selection_reasons=list(run.skill_selection_reasons),
        narrative_skill_decision=run.narrative_skill_decision,
        outline_source=run.outline_source,
        outline_json=run.outline_json,
        scene_draft_json=list(run.scene_script_json),
        scene_timing_assessments=[
            assessment.model_copy(deep=True)
            for assessment in run.scene_timing_assessments
        ],
        review_report=(
            run.review_report.model_copy(deep=True)
            if run.review_report is not None
            else None
        ),
        review_disposition=(
            run.review_disposition.model_copy(deep=True)
            if run.review_disposition is not None
            else None
        ),
        issue_ledger=[entry.model_copy(deep=True) for entry in run.issue_ledger],
        latest_repair_plan=(
            run.latest_repair_plan.model_copy(deep=True)
            if run.latest_repair_plan is not None
            else None
        ),
        latest_story_metadata_patch=(
            run.latest_story_metadata_patch.model_copy(deep=True)
            if run.latest_story_metadata_patch is not None
            else None
        ),
        latest_revision_verification=(
            run.latest_revision_verification.model_copy(deep=True)
            if run.latest_revision_verification is not None
            else None
        ),
        revision_judge=(
            run.revision_judge.model_copy(deep=True)
            if run.revision_judge is not None
            else None
        ),
        revision_round=run.revision_round,
        revision_scene_sequences=list(run.revision_scene_sequences),
        revision_changed_scene_sequences=list(
            run.revision_changed_scene_sequences
        ),
        latest_revision_target_scene_sequences=list(
            run.latest_revision_target_scene_sequences
        ),
        latest_revision_changed_scene_sequences=list(
            run.latest_revision_changed_scene_sequences
        ),
        critic_used=run.critic_used,
        final_cold_read_used=run.final_cold_read_used,
        final_cold_read_report=(
            run.final_cold_read_report.model_copy(deep=True)
            if run.final_cold_read_report is not None
            else None
        ),
        warnings=list(run.warnings),
        completed_at=run.completed_at,
    )


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

    def execute(
        self,
        work_order: AgentWorkOrderView,
    ) -> AgentWorkOrderResultRequest | SpecialistToolRequest: ...


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
        role_values = "、".join(role.value for role in ProductionSkillRole)
        instructions = (
            "你是以 Agent 为核心的视频创作系统中的 Production Manager。请依据给定上下文，"
            "为当前目标提出最小且有用的下一版计划。你可以选择、跳过、重排、并行或替换步骤，"
            "不要照抄固定工作流。TOOL_CALL 或 ARTIFACT_PATCH 只能使用可用的 Tool id；"
            "当专业 Agent 应依据当前 Production Skill 完成任务时使用 DELEGATION；"
            "只有缺少会实质改变结果的用户决定时才使用 USER_INPUT。步骤依赖必须构成无环图。"
            "只返回 JSON，顶层键为 summary、steps、goal_complete。仅当最新上下文已经证明"
            "本次运行目标达成时设置 goal_complete=true；否则返回下一组有边界的有效步骤，"
            "并设置 goal_complete=false。每个步骤包含 id、title、kind、assigned_agent、"
            "可选的 tool_name 与 production_skill_role，以及 instructions、arguments、"
            "depends_on、artifact_refs。assigned_agent 只是执行者身份，不得从它的名称或"
            "用户文本猜测专业职责。每个 DELEGATION 都必须由你根据完整目标语义，显式选择"
            f"一个 production_skill_role：{role_values}。"
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
    """Recognize terminal lifecycle facts when no semantic planner is configured.

    This fallback deliberately does not author creative or provider routes. A caller
    must supply an explicit plan, or configure a language planner, for non-terminal
    production work.
    """

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
        del skill_prompt, tools, reason
        document = self._projects.get_project(context.project_id)
        if document is None:
            raise ProductionManagerError(
                "The local planner cannot continue before project.create succeeds"
            )
        if (
            document.project.run_mode == RunMode.GUIDED
            and current_plan is not None
            and any(step.tool_name == "project.create" for step in current_plan.steps)
        ):
            return AgentPlanProposal(
                summary="The guided project is created and ready for user-directed work.",
                goal_complete=True,
            )

        stage = document.project.stage
        if stage == ProjectStage.EXPORTED:
            return AgentPlanProposal(
                summary="The local fallback confirms that the requested export exists.",
                goal_complete=True,
            )
        if stage == ProjectStage.TIMELINE_READY and context.render_export is False:
            return AgentPlanProposal(
                summary="The requested timeline is ready and export was not requested.",
                goal_complete=True,
            )

        raise ProductionManagerError(
            "No semantic Production Manager is configured for this non-terminal "
            "objective; configure the language planner or supply initial_plan."
        )


class SpecialistToolRequest(DomainModel):
    """One registry invocation requested by the built-in specialist Agent."""

    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    reason: str = Field(default="Gather the evidence needed for the WorkOrder", max_length=1000)


def _specialist_tool_history(work_order: WorkOrder) -> list[dict[str, object]]:
    """Project persisted specialist turns back into the model's compact history."""

    history: list[dict[str, object]] = []
    for turn in work_order.specialist_turns:
        item: dict[str, object] = {
            "request": {
                "tool_name": turn.tool_name,
                "arguments": turn.arguments,
                "reason": turn.reason,
            }
        }
        if turn.result is not None:
            item["result"] = turn.result
        history.append(item)
    return history


def _specialist_evidence_refs(work_order: WorkOrder) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for turn in work_order.specialist_turns:
        if turn.result is None:
            continue
        raw_refs = turn.result.get("artifact_refs")
        if not isinstance(raw_refs, list):
            continue
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict):
                continue
            try:
                refs.append(ArtifactRef.model_validate(raw_ref))
            except ValueError:
                continue
    return _unique_artifact_refs(refs)


def _interrupted_tool_result(turn: SpecialistTurnRecord) -> dict[str, JsonValue]:
    return {
        "tool_id": turn.tool_name,
        "status": ToolCallStatus.FAILED.value,
        "output": None,
        "artifact_refs": [],
        "warnings": [],
        "error": {
            "kind": "ToolExecutionInterrupted",
            "message": (
                "Tool execution was interrupted before its result was persisted. "
                "Inspect canonical state and decide semantically whether to reconcile or retry."
            ),
        },
    }


class LanguageSpecialistAgent:
    """Make one specialist decision from a persisted WorkOrder checkpoint."""

    def __init__(
        self,
        client: LanguageProviderClient,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._client = client
        self._tools = tools
        self._tool_descriptors: list[ToolDescriptor] | None = None

    def bind_tools(
        self,
        tools: ToolRegistry,
        *,
        descriptors: Sequence[ToolDescriptor] | None = None,
    ) -> None:
        """Attach the shared Tool catalog without executing a Tool in this model turn."""

        self._tools = tools
        self._tool_descriptors = None if descriptors is None else list(descriptors)

    def execute(
        self,
        work_order: AgentWorkOrderView,
    ) -> AgentWorkOrderResultRequest | SpecialistToolRequest:
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
        may_call_tool = bool(descriptors) and self._tools is not None
        instructions = (
            "你是当前 WorkOrder 指定的专业 Agent。请依据下方对应角色的 Production Skill，"
            "处理已经限定范围的目标和所提供的工件摘要，不要扩大任务范围。"
            "你与 Production Manager 共用同一套 Tool Catalog。"
            + (
                "如果还需要补充证据或执行范围内的修改，只返回包含 tool_name、arguments、"
                "reason 的 JSON，本轮只提出一次 Tool 请求。请求会先持久化，由后续独立"
                "推进执行；否则直接返回最终结果。"
                if may_call_tool
                else "当前没有可用 Tool，请直接返回最终结果。"
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
                    "work_order": work_order.work_order.model_dump(
                        mode="json",
                        exclude={"specialist_turns"},
                    ),
                    "context": work_order.context.model_dump(mode="json"),
                    "available_tools": tool_catalog,
                    "tool_history": _specialist_tool_history(work_order.work_order),
                },
                ensure_ascii=False,
            ),
            max_output_tokens=5000,
        )
        payload = _json_block(raw)
        try:
            result = AgentWorkOrderResultRequest.model_validate_json(payload)
        except ValueError as result_error:
            if not may_call_tool:
                raise ProductionManagerError(
                    "Specialist Agent returned an invalid AgentResult payload"
                ) from result_error
            try:
                return SpecialistToolRequest.model_validate_json(payload)
            except ValueError as tool_error:
                raise ProductionManagerError(
                    "Specialist Agent returned neither a Tool call nor an AgentResult"
                ) from tool_error

        evidence_refs = _specialist_evidence_refs(work_order.work_order)
        if evidence_refs:
            result = result.model_copy(
                update={
                    "evidence_refs": _unique_artifact_refs(
                        [*result.evidence_refs, *evidence_refs]
                    )
                }
            )
        return result


def default_specialist_agent_factory(
    client: LanguageProviderClient,
) -> SpecialistAgent:
    return LanguageSpecialistAgent(client)


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
            render_export=request.render_export,
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
            render_export=request.render_export,
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
        context = self.runtime.context(
            run_id,
            requested_artifact_refs=requested_refs,
        )
        planning_trace: ContentPlanningTraceView | None = None
        planning_refs = [
            *requested_refs,
            *[
                artifact.ref
                for artifact in context.artifacts
                if artifact.ref.artifact_type == "content_planning_job"
            ],
        ]
        seen_planning_ids: set[str] = set()
        for ref in planning_refs:
            if (
                ref.artifact_type != "content_planning_job"
                or ref.artifact_id in seen_planning_ids
            ):
                continue
            seen_planning_ids.add(ref.artifact_id)
            planning = self.projects.get_planning_run(ref.artifact_id)
            if planning is None or planning.owner_agent_run_id != run_id:
                continue
            planning_trace = _content_planning_trace_view(planning)
            break
        return AgentRunView(
            snapshot=snapshot,
            ready_steps=self.runtime.ready_steps(run_id),
            context=context,
            content_planning_trace=planning_trace,
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
    ) -> AgentPlanProposal | None:
        """Keep persisted long-form planning ahead of downstream production work."""

        planning = document.planning
        if (
            document.project.content_planning_output
            == ContentPlanningOutput.SCRIPT_ONLY
            and document.project.stage == ProjectStage.SCRIPT_READY
            and (planning is None or planning.status == PlanningRunStatus.COMPLETE)
        ):
            return AgentPlanProposal(
                summary=(
                    "The requested screenplay is complete; stop before shot, asset, image, "
                    "video, or timeline production."
                ),
                steps=[],
                goal_complete=True,
            )
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
            artifact_type="content_planning_job",
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
        }:
            # A failed content unit is context for the Production Manager Agent, not
            # an automatic user-approval gate.  Returning no fixed precondition lets
            # the planner choose whether to retry, change route, or request genuinely
            # missing user input from the persisted ContextPack.
            return None

        if not self.tools.contains("planning.advance"):
            raise ProductionManagerError(
                "The shared Tool Registry has no planning.advance Tool"
            )
        return AgentPlanProposal(
            summary="Advance the persisted content plan by one bounded unit.",
            steps=[
                PlanStep(
                    id="advance-content-planning",
                    title="Advance content planning",
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
                        "owner_agent_run_id": agent_run_id,
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
        planning_progress_boundary_reached = False

        while True:
            current = self.runtime.snapshot(run_id)
            plan = current.current_plan
            if plan is None:
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                break

            failed_actions = self._failed_actions_for_plan(current, plan.id)
            if failed_actions:
                reason = "Revise the plan after failed actions: " + "; ".join(
                    action.error_message or action.input_summary
                    for action in failed_actions[-4:]
                )
                if not self._automatic_replan_unlocked(run_id, reason, notes):
                    break
                continue

            ready = self.runtime.ready_steps(run_id)
            if ready and planning_progress_boundary_reached:
                self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                break
            if ready and remaining_actions > 0:
                batch = ready[:remaining_actions]
                self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                for step in batch:
                    completed_planning_action = False
                    action, work_order, note = self._execute_step(
                        run_id,
                        plan.id,
                        step,
                    )
                    executed.append(action.id)
                    remaining_actions -= 1
                    if action.status == ActionStatus.SUCCEEDED:
                        if step.tool_name == "planning.advance":
                            planning_progress_boundary_reached = True
                            completed_planning_action = True
                    if work_order is not None:
                        work_orders.append(work_order.id)
                    if note:
                        notes.append(note)
                    if completed_planning_action:
                        break
                continue

            if ready:
                self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                break

            if PlanGraph(plan).is_complete(current.actions):
                if plan.goal_complete:
                    self.runtime.set_run_status(run_id, AgentRunStatus.COMPLETED)
                    notes.append("The Agent confirmed that the run objective is complete")
                    break
                if not self._automatic_replan_unlocked(
                    run_id,
                    "The bounded plan completed; inspect the latest ContextPack and continue "
                    "toward the run objective.",
                    notes,
                ):
                    break
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
        active_blockers = [
            action.error_message
            for action in view.snapshot.actions
            if action.status == ActionStatus.FAILED
            and action.plan_revision_id == active_plan_id
            and action.error_message
        ][-4:]
        if not active_blockers and view.snapshot.run.status == AgentRunStatus.WAITING:
            active_blockers = view.snapshot.memory.blockers
        next_actions = [step.title for step in view.ready_steps]
        if not next_actions and view.snapshot.run.status == AgentRunStatus.WAITING:
            next_actions = view.snapshot.memory.next_actions
        self.runtime.checkpoint(
            run_id,
            current_focus=(
                view.snapshot.current_plan.summary
                if view.snapshot.current_plan is not None
                else view.snapshot.run.objective
            ),
            blockers=active_blockers,
            open_questions=[
                action.input_summary
                for action in view.snapshot.actions
                if action.kind == ActionKind.USER_INPUT
                and action.status == ActionStatus.RUNNING
            ],
            next_actions=next_actions,
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
        replanned = self.runtime.snapshot(run_id)
        replanned_plan = replanned.current_plan
        if (
            replanned_plan is None
            or (
                not replanned_plan.goal_complete
                and not self.runtime.ready_steps(run_id)
            )
        ):
            self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
            self.runtime.checkpoint(
                run_id,
                current_focus=reason,
                blockers=[
                    *blockers,
                    "Automatic replanning produced no actionable plan",
                ][-4:],
                next_actions=["Revise the plan from the latest ContextPack"],
            )
            notes.append("Automatic replanning produced no actionable or terminal plan")
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
        role = work_order.production_skill_role
        if role is None:
            raise ProductionManagerError(
                "WorkOrder 缺少 Agent 显式选择的 production_skill_role；"
                "不能从 recipient_role 名称推断专业职责"
            )
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
            pending_turn = next(
                (
                    turn
                    for turn in reversed(current.specialist_turns)
                    if turn.status == SpecialistTurnStatus.REQUESTED
                ),
                None,
            )
        if pending_turn is not None:
            return self._execute_pending_specialist_tool(
                work_order_id,
                pending_turn.id,
            )

        client = self._language_client_resolver()
        if client is None:
            raise ProductionManagerError(
                "No language provider is configured for the specialist Agent"
            )
        with self._run_lock(work_order.parent_run_id):
            self.runtime.begin_work_order(work_order_id)
            view = self.get_work_order(work_order_id)
            parent_run = self.runtime.snapshot(work_order.parent_run_id).run
        try:
            agent = self._specialist_agent_factory(client)
            if isinstance(agent, LanguageSpecialistAgent):
                agent.bind_tools(
                    self.tools,
                    descriptors=_tools_for_execution_target(
                        self.tools.list(),
                        parent_run.execution_target,
                    ),
                )
            decision = agent.execute(view)
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
        if isinstance(decision, SpecialistToolRequest):
            with self._run_lock(work_order.parent_run_id):
                current = self.runtime.store.get_work_order(work_order_id)
                if current is None:
                    raise ProductionManagerError(
                        f"Unknown work order: {work_order_id}"
                    )
                next_sequence = max(
                    (turn.sequence for turn in current.specialist_turns),
                    default=0,
                ) + 1
                current.specialist_turns.append(
                    SpecialistTurnRecord(
                        id=f"specialist-turn-{uuid4().hex}",
                        sequence=next_sequence,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        reason=decision.reason,
                    )
                )
                current.status = DelegationStatus.NEEDS_ATTENTION
                current.updated_at = utc_now()
                self.runtime.store.save_work_order(current)
            return self.submit_work_order_result(
                work_order_id,
                AgentWorkOrderResultRequest(
                    status=AgentResultStatus.PARTIAL,
                    concise_summary=(
                        f"专业 Agent 已请求 Tool {decision.tool_name}，"
                        "请求已持久化，尚未执行。"
                    ),
                    recommended_next_actions=[
                        "执行已持久化的 Tool 请求，然后让专业 Agent 继续判断。"
                    ],
                ),
            )
        return self.submit_work_order_result(work_order_id, decision)

    def _execute_pending_specialist_tool(
        self,
        work_order_id: str,
        turn_id: str,
    ) -> AgentWorkOrderResultResponse:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        with self._run_lock(work_order.parent_run_id):
            current = self.runtime.store.get_work_order(work_order_id)
            if current is None:
                raise ProductionManagerError(f"Unknown work order: {work_order_id}")
            turn = next(
                (item for item in current.specialist_turns if item.id == turn_id),
                None,
            )
            if turn is None or turn.status != SpecialistTurnStatus.REQUESTED:
                raise ProductionManagerError(
                    "The persisted specialist Tool request is no longer pending"
                )
            turn.status = SpecialistTurnStatus.IN_PROGRESS
            turn.updated_at = utc_now()
            current.status = DelegationStatus.IN_PROGRESS
            current.updated_at = turn.updated_at
            self.runtime.store.save_work_order(current)
            project_id = self.runtime.snapshot(current.parent_run_id).run.project_id
            parent_run_id = current.parent_run_id
            tool_name = turn.tool_name
            arguments = cast(Mapping[str, object], turn.arguments)

        try:
            tool_result = self._invoke_scoped_tool(
                project_id,
                tool_name,
                arguments,
                agent_run_id=parent_run_id,
            )
        except Exception as exc:
            with self._run_lock(parent_run_id):
                current = self.runtime.store.get_work_order(work_order_id)
                if current is not None:
                    turn = next(
                        (item for item in current.specialist_turns if item.id == turn_id),
                        None,
                    )
                    if turn is not None:
                        turn.status = SpecialistTurnStatus.COMPLETED
                        turn.result = {
                            "tool_id": turn.tool_name,
                            "status": ToolCallStatus.FAILED.value,
                            "output": None,
                            "artifact_refs": [],
                            "warnings": [],
                            "error": {
                                "kind": type(exc).__name__,
                                "message": str(exc),
                            },
                        }
                        turn.updated_at = utc_now()
                    current.status = DelegationStatus.NEEDS_ATTENTION
                    current.updated_at = utc_now()
                    self.runtime.store.save_work_order(current)
            if isinstance(exc, ProductionManagerError):
                raise
            raise ProductionManagerError(
                f"Specialist Tool execution failed: {exc}"
            ) from exc

        serialized_result = cast(
            dict[str, JsonValue],
            tool_result.model_dump(mode="json"),
        )
        with self._run_lock(parent_run_id):
            current = self.runtime.store.get_work_order(work_order_id)
            if current is None:
                raise ProductionManagerError(f"Unknown work order: {work_order_id}")
            turn = next(
                (item for item in current.specialist_turns if item.id == turn_id),
                None,
            )
            if turn is None:
                raise ProductionManagerError(
                    "The persisted specialist Tool turn disappeared during execution"
                )
            turn.status = SpecialistTurnStatus.COMPLETED
            turn.result = serialized_result
            turn.updated_at = utc_now()
            current.status = DelegationStatus.NEEDS_ATTENTION
            current.updated_at = turn.updated_at
            self.runtime.store.save_work_order(current)

        findings: list[str] = []
        if tool_result.error is not None:
            findings.append(
                f"Tool {tool_name} returned {tool_result.status.value}: "
                f"{tool_result.error.message}"
            )
        return self.submit_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.PARTIAL,
                concise_summary=(
                    f"Tool {tool_name} 已执行并持久化结果："
                    f"{tool_result.status.value}。"
                ),
                findings=findings,
                evidence_refs=tool_result.artifact_refs,
                recommended_next_actions=[
                    "让专业 Agent 读取已持久化的 Tool 结果并继续判断。"
                ],
            ),
        )

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
        self.runtime.complete_action(action.id, output_summary=request.answer)
        if request.remember_as_decision:
            self.runtime.remember_decision(
                project_id=run.project_id,
                run_id=run.id,
                statement=request.answer,
                rationale=f"User response to: {action.input_summary}",
                artifact_refs=action.artifact_refs,
            )
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
        while True:
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
                        error_kind="MissingToolName",
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
                        error_kind="UnknownAgentRun",
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
                        error_kind=type(exc).__name__,
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
                        error_kind=(
                            result.error.kind
                            if result.error is not None
                            else result.status.value
                        ),
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
                if step.tool_name == "planning.advance":
                    planning_run_id = step.arguments.get("run_id")
                    if isinstance(planning_run_id, str):
                        self._synchronize_planning_narrative_context(
                            agent_run_id=run.id,
                            document=document,
                            planning_run_id=planning_run_id,
                        )
                self.refresh_project_memory(document)
            return completed, None, None
        if step.kind == ActionKind.DELEGATION:
            if step.production_skill_role is None:
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=(
                            "DELEGATION step is missing the Agent-selected "
                            "production_skill_role"
                        ),
                        error_kind="MissingProductionSkillRole",
                    ),
                    None,
                    "The Agent plan must explicitly select the delegation's production role",
                )
            arguments = step.arguments
            work_order = self.runtime.create_work_order(
                run_id,
                sender="production-manager",
                recipient_role=step.assigned_agent,
                production_skill_role=step.production_skill_role,
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

    def _synchronize_planning_narrative_context(
        self,
        *,
        agent_run_id: str,
        document: ProjectDocument,
        planning_run_id: str,
    ) -> None:
        """Expose Agent-selected story direction to the owning AgentRun."""

        planning = self.projects.get_planning_run(planning_run_id)
        intent = None if planning is None else planning.story_intent
        if planning is None or intent is None:
            return

        self.runtime.set_run_active_skills(
            agent_run_id,
            [
                production_skill_id(document.project.production_profile),
                *planning.selected_narrative_skill_ids,
            ],
        )

        existing_statements = {
            decision.statement
            for decision in self.runtime.store.list_decisions(
                document.project.id,
                run_id=agent_run_id,
            )
            if decision.run_id == agent_run_id
        }
        planning_ref = ArtifactRef(
            project_id=document.project.id,
            artifact_type="content_planning_job",
            artifact_id=planning.id,
        )
        intent_parts = [
            f"观众体验：{intent.audience_experience}",
            f"戏剧引擎：{intent.dramatic_engine}",
            f"推进策略：{intent.progression_strategy}",
            f"调性与可信度：{intent.tone_and_plausibility}",
        ]
        if intent.protected_elements:
            intent_parts.append("需保护的元素：" + "；".join(intent.protected_elements))
        if intent.drift_risks:
            intent_parts.append("需避免的偏移：" + "；".join(intent.drift_risks))
        decisions = [
            (
                "当前故事创作意图：" + "；".join(intent_parts),
                (
                    f"内容规划任务 {planning.id} 形成的可覆盖创作方向，"
                    "供后续 Agent 理解观众体验与故事取舍，不作为质量门禁。"
                ),
            )
        ]
        if planning.narrative_skill_decision:
            decisions.append(
                (
                    "本轮叙事技能选择判断：" + planning.narrative_skill_decision,
                    (
                        f"内容规划任务 {planning.id} 基于完整用户语义形成；"
                        "包括不装载额外技能时的理由，不作为关键词路由或质量门禁。"
                    ),
                )
            )
        for index, skill_id in enumerate(planning.selected_narrative_skill_ids):
            normalized_skill_id = skill_id.strip()
            if not normalized_skill_id:
                continue
            reason = (
                planning.skill_selection_reasons[index].strip()
                if index < len(planning.skill_selection_reasons)
                else ""
            )
            selection_statement = f"当前故事选用叙事技能 {normalized_skill_id}"
            if reason:
                selection_statement += f"；选择理由：{reason}"
            else:
                selection_statement += "；内容规划记录未附单独理由"
            decisions.append(
                (
                    selection_statement,
                    (
                        f"内容规划任务 {planning.id} 根据当前故事创作意图"
                        "自主选择，后续 Agent 或用户可以调整。"
                    ),
                )
            )

        for statement, rationale in decisions:
            if statement in existing_statements:
                continue
            self.runtime.remember_decision(
                project_id=document.project.id,
                run_id=agent_run_id,
                statement=statement,
                rationale=rationale,
                artifact_refs=[planning_ref],
            )
            existing_statements.add(statement)

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
            requested_owner_agent_run_id = arguments.get("owner_agent_run_id")
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
            if requested_owner_agent_run_id != agent_run_id:
                raise ProductionManagerError(
                    "planning.advance owner_agent_run_id must match the current Agent run"
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
                for turn in work_order.specialist_turns:
                    if turn.status != SpecialistTurnStatus.IN_PROGRESS:
                        continue
                    turn.status = SpecialistTurnStatus.INTERRUPTED
                    turn.result = _interrupted_tool_result(turn)
                    turn.updated_at = utc_now()
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
                                    artifact_type="content_planning_job",
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
                                        artifact_type="content_planning_job",
                                        artifact_id=planning_run.id,
                                    )
                                ],
                            )
                            self._synchronize_planning_narrative_context(
                                agent_run_id=run.id,
                                document=document,
                                planning_run_id=planning_run.id,
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
