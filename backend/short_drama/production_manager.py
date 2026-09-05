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

from pydantic import Field, JsonValue, ValidationError, model_validator

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
    ContextBoundary,
    ContextManifestResolutionError,
    ContextPack,
    DelegationStatus,
    PlanGraph,
    PlanRevision,
    PlanStep,
    ProjectMemory,
    SpecialistProtocolFeedback,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrder,
    WorkOrderNextOwner,
    WorkOrderProgressEntry,
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
    PlanningRunOperation,
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
    NarrativeSkillDescriptor,
    ProductionMethodSkillDescriptor,
    ProductionSkillRole,
    compile_production_prompt,
    narrative_skill_catalog,
    production_method_skill_catalog,
    production_skill_catalog,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.skills import SkillProvenanceKind
from short_drama.tools import (
    ToolCallStatus,
    ToolDescriptor,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)


class ProductionManagerError(ValueError):
    """Raised when an Agent run cannot be planned or advanced as requested."""


class SpecialistResponseProtocolError(ProductionManagerError):
    """A Worker response needs protocol correction by that same Worker."""

    def __init__(self, feedback: SpecialistProtocolFeedback) -> None:
        self.feedback = feedback
        issue_summary = json.dumps(
            feedback.validation_issues,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        super().__init__(
            "Specialist response did not match any supported response contract: "
            + issue_summary[:4000]
        )


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

_AGENT_INTERACTION_PRIORITY = (
    "交互决策按以下优先级执行：用户已明确给出的值、事实和选择直接采用，"
    "不得换个说法重复确认；用户明确要求在某个工件或内容节点确认、审阅或选择时，"
    "只在该语义节点暂停，即使其余流程无人值守；除此之外，用户要求不交互、"
    "由 Agent 决定，或 context.unattended=true 时，对可合理推断的事项自主选择，"
    "在决策或假设中留痕并继续；"
    "其他情况只有在未解决决策会实质改变结果，且无法从当前输入、已确认决策或工件中"
    "可靠推断时，才请求用户输入。这些规则不代表固定确认次数或阶段门禁。"
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
    selected_method_skill_ids: list[str] = Field(default_factory=list[str])
    method_selection_rationale: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
    )
    carried_work_order_ids: list[str] = Field(default_factory=list[str])
    goal_complete: bool = False

    @model_validator(mode="after")
    def validate_progress_path(self) -> AgentPlanProposal:
        normalized_method_ids = [
            skill_id.strip()
            for skill_id in self.selected_method_skill_ids
            if skill_id.strip()
        ]
        if normalized_method_ids != self.selected_method_skill_ids or len(
            normalized_method_ids
        ) != len(set(normalized_method_ids)):
            raise ValueError(
                "selected_method_skill_ids must be trimmed and unique"
            )
        normalized_carried_ids = [
            work_order_id.strip()
            for work_order_id in self.carried_work_order_ids
            if work_order_id.strip()
        ]
        if normalized_carried_ids != self.carried_work_order_ids or len(
            normalized_carried_ids
        ) != len(set(normalized_carried_ids)):
            raise ValueError("carried_work_order_ids must be trimmed and unique")
        if not self.goal_complete and not self.steps:
            raise ValueError(
                "An incomplete Agent plan must contain at least one next step"
            )
        return self


class AgentToolSchemaRequest(DomainModel):
    """Agent-authored request for the exact schemas needed to finish planning."""

    requested_tool_schema_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_requested_ids(self) -> AgentToolSchemaRequest:
        normalized = [tool_id.strip() for tool_id in self.requested_tool_schema_ids]
        if normalized != self.requested_tool_schema_ids or len(normalized) != len(
            set(normalized)
        ):
            raise ValueError("requested_tool_schema_ids must be trimmed and unique")
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
    deliverable: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    created_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    updated_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    findings: list[str] = Field(default_factory=list[str], max_length=40)
    decisions: list[str] = Field(default_factory=list[str], max_length=40)
    assumptions: list[str] = Field(default_factory=list[str], max_length=40)
    unresolved_questions: list[str] = Field(default_factory=list[str], max_length=40)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recommended_next_actions: list[str] = Field(default_factory=list[str], max_length=40)
    next_owner: WorkOrderNextOwner | None = None

    @model_validator(mode="after")
    def normalize_next_owner(self) -> AgentWorkOrderResultRequest:
        if self.status == AgentResultStatus.PARTIAL:
            if self.next_owner is None:
                self.next_owner = WorkOrderNextOwner.LEADER_REPLAN
        elif self.next_owner is not None:
            raise ValueError("Only a PARTIAL AgentResult can name a next owner")
        return self


class ProductionSkillDescriptor(DomainModel):
    id: str
    profile: ProductionProfile
    label: str
    medium: str
    recommended_playbook: list[str]
    role_names: list[str]


class AgentProductionMethodSkillDescriptor(DomainModel):
    id: str
    label: str
    description: str
    supported_roles: list[ProductionSkillRole]
    provenance: SkillProvenanceKind
    upstream_sources: list[str]
    user_overridable: bool = True


class AgentCapabilityCatalog(DomainModel):
    tools: list[ToolDescriptor]
    skills: list[ProductionSkillDescriptor]
    production_methods: list[AgentProductionMethodSkillDescriptor]


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
    selected_method_skill_ids: list[str] = Field(default_factory=list[str])
    method_selection_reasons: list[str] = Field(default_factory=list[str])
    method_selection_rationale: str | None = None
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
    advanced_work_order_ids: list[str] = Field(default_factory=list[str])
    notes: list[str]


class AgentWorkOrderView(DomainModel):
    work_order: WorkOrder
    context: ContextPack
    system_prompt: str
    progress_entries: list[WorkOrderProgressEntry] = Field(
        default_factory=list[WorkOrderProgressEntry]
    )


class AgentWorkOrderProgressRequest(DomainModel):
    author: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class AgentWorkOrderProgressListResponse(DomainModel):
    entries: list[WorkOrderProgressEntry]


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
        selected_method_skill_ids=list(run.selected_method_skill_ids),
        method_selection_reasons=list(run.method_selection_reasons),
        method_selection_rationale=run.method_selection_rationale,
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
        method_skills: Sequence[AgentProductionMethodSkillDescriptor],
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal: ...


ProductionPlannerFactory = Callable[[LanguageProviderClient], ProductionPlanner]
LanguageClientResolver = Callable[[], LanguageProviderClient | None]


# These are the responsibilities available to newly authored Agent plans.
# Legacy review roles remain in the enum so stored runs can still be read, but
# they are not advertised as separate Agents in the current team topology.
_ACTIVE_PRODUCTION_SKILL_ROLES = (
    ProductionSkillRole.CONCEPT,
    ProductionSkillRole.WRITER,
    ProductionSkillRole.PLANNER,
    ProductionSkillRole.REVISION_VERIFIER,
    ProductionSkillRole.DIRECTOR,
    ProductionSkillRole.ASSET_REVIEWER,
    ProductionSkillRole.ROUTE_PLANNER,
    ProductionSkillRole.VIDEO_PROMPT_COMPILER,
    ProductionSkillRole.EPISODE_REVIEWER,
)


class SpecialistAgent(Protocol):
    """Execute one bounded WorkOrder using its role-specific Skill context."""

    def execute(
        self,
        work_order: AgentWorkOrderView,
    ) -> AgentWorkOrderResultRequest | SpecialistToolRequest: ...


SpecialistAgentFactory = Callable[[LanguageProviderClient], SpecialistAgent]


def production_skill_id(profile: ProductionProfile) -> str:
    return f"format.{profile.value.casefold().replace('_', '-')}"


def _agent_method_skill_descriptor(
    descriptor: ProductionMethodSkillDescriptor,
) -> AgentProductionMethodSkillDescriptor:
    return AgentProductionMethodSkillDescriptor(
        id=descriptor.id,
        label=descriptor.label,
        description=descriptor.description,
        supported_roles=list(descriptor.supported_roles),
        provenance=descriptor.provenance,
        upstream_sources=list(descriptor.upstream_sources),
        user_overridable=descriptor.user_overridable,
    )


def _narrative_skill_ids_for_role(
    skill_ids: Sequence[str],
    role: ProductionSkillRole,
) -> list[str]:
    catalog: dict[str, NarrativeSkillDescriptor] = {
        descriptor.id: descriptor for descriptor in narrative_skill_catalog()
    }
    return [
        skill_id
        for skill_id in skill_ids
        if (descriptor := catalog.get(skill_id)) is not None
        and role in descriptor.supported_roles
    ]


def _selected_narrative_skill_ids(skill_ids: Sequence[str]) -> list[str]:
    known_ids = {descriptor.id for descriptor in narrative_skill_catalog()}
    return [skill_id for skill_id in skill_ids if skill_id in known_ids]


def _method_skill_ids_for_role(
    skill_ids: Sequence[str],
    role: ProductionSkillRole,
) -> list[str]:
    catalog: dict[str, ProductionMethodSkillDescriptor] = {
        descriptor.id: descriptor for descriptor in production_method_skill_catalog()
    }
    return [
        skill_id
        for skill_id in skill_ids
        if (descriptor := catalog.get(skill_id)) is not None
        and role in descriptor.supported_roles
    ]


def _selected_method_skill_ids(skill_ids: Sequence[str]) -> list[str]:
    known_ids = {descriptor.id for descriptor in production_method_skill_catalog()}
    return [skill_id for skill_id in skill_ids if skill_id in known_ids]


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


def _normalize_specialist_response_payload(text: str) -> str:
    """Recover only a dangling ``reason`` field on a Tool request.

    Some language providers occasionally emit a structurally complete object,
    close it one brace too early after the large ``arguments`` value, and then
    continue with the non-executing ``reason`` field.  This is a transport-level
    serialization slip, not a second response.  Accept only that observed
    SpecialistToolRequest shape.  Separate JSON objects, prose, truncated
    payloads, unknown suffixes, and field conflicts remain invalid and continue
    through typed protocol feedback.
    """

    payload = _json_block(text)
    try:
        json.loads(payload)
    except json.JSONDecodeError:
        pass
    else:
        return payload

    try:
        decoded, end = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError:
        return payload
    if not isinstance(decoded, dict):
        return payload
    decoded_object = cast(dict[str, JsonValue], decoded)
    if set(decoded_object) != {"tool_name", "arguments"}:
        return payload

    remainder = payload[end:].strip()
    if not remainder.startswith(","):
        return payload
    try:
        continuation = json.loads("{" + remainder[1:])
    except json.JSONDecodeError:
        return payload
    if not isinstance(continuation, dict):
        return payload
    continuation_object = cast(dict[str, JsonValue], continuation)
    if set(continuation_object) != {"reason"}:
        return payload

    decoded_object.update(continuation_object)
    return json.dumps(decoded_object, ensure_ascii=False)


def _validation_issues(error: ValueError) -> list[dict[str, JsonValue]]:
    """Normalize Pydantic issues into durable JSON feedback for the Worker."""

    raw_issues: object
    if isinstance(error, ValidationError):
        raw_issues = error.errors(include_url=False, include_input=False)
    else:
        fallback_issue: dict[str, JsonValue] = {
            "type": type(error).__name__,
            "loc": [],
            "msg": str(error),
        }
        raw_issues = [fallback_issue]
    normalized = json.loads(
        json.dumps(raw_issues, ensure_ascii=False, default=str)
    )
    return cast(list[dict[str, JsonValue]], normalized)


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
        method_skills: Sequence[AgentProductionMethodSkillDescriptor],
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal:
        tools_by_id = {item.id: item for item in tools}
        tool_catalog = [
            {
                "id": item.id,
                "version": item.version,
                "category": item.category.value,
                "description": item.description,
                "effect": item.effect.value,
                "idempotent": item.idempotent,
                "tags": item.tags,
            }
            for item in tools
        ]
        role_values = "、".join(
            role.value for role in _ACTIVE_PRODUCTION_SKILL_ROLES
        )
        instructions = (
            "你是以 Agent 为核心的视频创作系统中的 Production Manager。请依据给定上下文，"
            "为当前目标提出最小且有用的下一版计划。你可以选择、跳过、重排、并行或替换步骤，"
            "不要照抄固定工作流。TOOL_CALL 或 ARTIFACT_PATCH 只能使用可用的 Tool id；"
            "当专业 Agent 应依据当前 Production Skill 完成任务时使用 DELEGATION；"
            f"{_AGENT_INTERACTION_PRIORITY} USER_INPUT 必须符合这一优先级。"
            "若当前存在属于本 AgentRun 且尚未完成的 content_planning_job，"
            "本轮只能编排 planning.advance Script Stage 和必要的 USER_INPUT 停靠点；"
            "用户要求当前规划单元后确认时，先安排 planning.advance，再安排一个"
            " depends_on 该步骤的 USER_INPUT，使停靠发生在指定语义边界。"
            "不得进入下游制作，也不得声称总目标已完成。步骤依赖必须构成无环图。"
            "当已有完整剧本且用户提出创意性修改时，可选择 planning.start_script_revision；"
            "该 Tool 会继承原始要求和当前版本，并由剧本 Leader 自主决定局部逐场修改、"
            "全场逐场修改或结构重构。不要用关键词判断范围，也不要用 script.revise_lines "
            "承担场景、人物、节奏或因果层面的改写。"
            "当目标是从已确认剧本制作分镜时，对外最终结果仍是一份绑定到 "
            "storyboard.current 的完整 Storyboard，不按场次创建多份正式分镜文档。"
            "这是 MiniMax Design「剧本 → 时长账本 → 完整分镜」的本地执行适配："
            "Leader 应先委派一个负责全片编译计划的 Planner WorkOrder。该 "
            "WorkOrder 先检查当前 Tool history；若其中没有当前精确 "
            "ScriptVersion 与 ProjectBibleVersion 正文，必须先请求 project.get。"
            "ArtifactRef、进度摘要或 Leader 转述不等于通读原文。获得精确"
            "内容后再用 shot.begin_storyboard_draft 只锁定"
            "源版本、场次身份、顺序与连续性意图；场次标题、摘要、全局 "
            "script_line_ids 与逐场 source_line_ids 由服务端确定性回填。begin "
            "是可恢复的源锁定检查点，不要在一次模型回答中输出全片账本或分组。"
            "同一 Planner WorkOrder 随后按剧情语义和自然动作链选择连续范围，用 "
            "shot.append_storyboard_ledger_segment 立即持久化已稳定的时长账本片段，"
            "每次都从最新 draft_version_id 与精确源游标继续。片段边界只是运行检查点，"
            "不是 Scene、ClipGroup、Shot、ComfyUI 节点或固定字数；不设片段数量、大小或"
            "重试次数。"
            "每个场次只有最后一个 timing entry 显式设置 completes_scene=true，"
            "其他场内条目为 false。纯动作场次或对白后仍有可见动作时，由最后的"
            "动作条目标记场次完成；不把分段持久化边界当成场次结束。"
            "在首次封结 ledger 之前，Planner 必须基于已精确分配的时长前瞻确认："
            "每个场次的有序 timing entries 都存在一个连续、完整、不跨场的分区，"
            "且每组 allocated_duration_seconds 之和精确为 4–15 秒整数。"
            "若不可分，先在账本中拆分或调整确实可分的动作、反应和停顿预留，"
            "但不改台词、事件或自然表演总意图，也不从用户的 120 秒等目标总时长倒推分配。"
            "不得先封存账本，再在 outline 阶段才发现无法装箱。"
            "只在账本已按源顺序完整覆盖剧本且该分区可行时调用 "
            "shot.complete_storyboard_ledger，不得边看台词边临时发明最终镜头组。"
            "账本完成后，该 WorkOrder 必须用 artifact.get_production_document "
            "读取最新 storyboard_draft 的精确版本，不能凭进度摘要重建账本；"
            "再基于这份完整账本二次重装箱，用 "
            "shot.append_storyboard_outline_segment 持久化已稳定的有序大纲前缀。"
            "一个 ClipGroup 是未来一次 H3 生成的 4–15 秒整数执行边界，"
            "不跨场次，"
            "可以包含多个可编辑 Shot。全部大纲片段落盘后，再读取最新精确 draft，"
            "在全局检查来源覆盖、连续性、分组和实算时长后，"
            "再用 shot.complete_storyboard_outline 写入 timing_audit 并进入详细分镜阶段。"
            "用户或剧本声明的目标时长只作并列对比，不得倒推镜头秒数、平均分配或"
            "静默删改剧本。Planner 在 begin、多次 ledger append、ledger complete、多次 "
            "outline append 与 outline complete 之间持续使用同一 WorkOrder；每个 Tool "
            "结果会回到同一 Specialist，它可直接取得真实 draft document/version ID 并继续请求"
            "下一个 Tool。不得仅为获取 begin 生成的 ID 就结束 WorkOrder、交回 Leader "
            "重规划，也不为每个片段重建 Leader 计划。若 Planner 自己发现当前草稿"
            "无法形成合法分区，这仍属于同一编译职责：可在同一 WorkOrder 内重新 "
            "begin 一个草稿分支并完整重编译，不得仅为重启草稿返回 Leader。"
            "完整 outline 锁定后，再由一个或多个按剧情语义划分的有序 WorkOrder "
            "通过 shot.append_storyboard_group 逐组持久化，最后使用 "
            "shot.finalize_storyboard 确定性装配；shot.create_storyboard 只用于导入已完整存在的旧分镜。"
            "WorkOrder 的范围由完整剧情、自然动作链、对白与连续性判断，可小于一个场次、"
            "覆盖一个场次，也可在必要时覆盖相邻场次；不要把场次、一次模型调用、一个 Shot "
            "和一个 ClipGroup 强行等同。多个 WorkOrder 写同一草稿时必须通过依赖保持有序，"
            "并把最新 storyboard_draft 明确交给后续工作。每次 append 只提交一个完整 "
            "ClipGroup；ClipGroup 是未来一次 H3 生成的 4–15 秒整数执行边界，内部可以包含多个 "
            "可编辑 Shot，Shot 的剪辑时长可以为小数。一个语义范围内有多个 planned ClipGroup 时，默认交给同一个"
            "Worker 在同一 WorkOrder 中逐组 append；工具调用仍一次一组，但不为每组重建"
            "Leader 计划。只有语义范围真的需要变化时才返回 Leader 重规划。下一组必须"
            "从草稿中读取上一组真实 continuity_out 作为事实基础，不由 Leader "
            "凭摘要重写，也不为了字符串相等而复制措辞。同场次边界要在人物、"
            "道具、站位、动作和声音上语义可承接；跨场次边界要分别写清上一场的"
            "退场/结束状态、下一场的入场/起始状态以及时间或空间转换。"
            "Director 创作详细组之前、Verifier 审阅之前，都必须通过 "
            "artifact.get_production_document 读取对应的最新精确 draft version。"
            "全部 planned ClipGroup 落盘后，先派一个选择 "
            "REVISION_VERIFIER 职责的 Verifier 通读精确草稿，按已选方法的时长账本、"
            "对白覆盖、重复膨胀和末态检查项，并语义通读每个组间边界："
            "同场次核对可见状态与动作是否自然承接，跨场次核对上一场退场、下一场入场和"
            "时空转换是否清楚。不用 continuity 文本逐字相等代替内容判断。结论写入"
            "shot.record_storyboard_draft_verification。若报告只涉及组内镜头语言、表演或"
            "连续性实现，Leader 定向安排 shot.replace_storyboard_draft_group 修复受影响组；"
            "若问题涉及账本、时长、来源范围或组边界，则从当前剧本重新完成整份账本并 begin "
            "一个新的完整草稿，不能用局部替换偷改编译计划。修复后再依当前语义判断是否需要"
            "Verifier 重读；不使用固定轮次或 Critic 循环。Verifier 认为完整可用后才 finalize。"
            "场次索引用于组织一份 Storyboard 内的章节和连续性，不代表 ComfyUI 的节点、"
            "workflow 或视觉 group；未来 Comfy 投影以每个 ClipGroup 的 visual-gen work item "
            "为稳定衔接点。"
            "计划执行器不会替步骤解析未来输出或做跨步骤模板插值。每个步骤的 arguments "
            "和 artifact_refs 只能使用当前 ContextPack 中已经存在且可精确引用的值。若本轮"
            "某一步会创建后续步骤必需的 document ID、version ID 或其他工件引用，就让本版"
            "计划在该创建步骤后结束；待结果落盘并进入下一次重规划，再从最新上下文读取真实"
            "引用继续。不得在计划中编造占位符、变量路径或推测未来 ID。"
            "available_tools 首先只提供紧凑目录。如果完成当前规划确实需要某些 Tool "
            "的精确输入/输出合同，只返回 JSON：requested_tool_schema_ids 和 rationale；"
            "运行时会将这些显式选中的 schema 放入 tool_schemas 后让你继续规划。"
            "已提供的 schema 不得重复请求，也不得请求目录外 ID。"
            "无需更多 schema 时，只返回计划 JSON，顶层键为 summary、steps、selected_method_skill_ids、"
            "method_selection_rationale、carried_work_order_ids、goal_complete。"
            "重规划时，只有仍应在新计划后台继续的未完成 WorkOrder 才写入 "
            "carried_work_order_ids；其他旧 WorkOrder 会作为历史交接停止自动执行。"
            "先阅读 available_production_methods，"
            "根据完整目标、已有工件与当前阶段语义决定是否选择一个或多个制片方法；"
            "其中 MiniMax Design 的 drama-series、ad-tvc、mv 是项目级主 Workflow，"
            "MiniMax-H3 方法是可组合的任务级表达方法。可先判断是否需要一个项目 Workflow "
            "作为主骨架，再按实际任务组合任务方法，但这不是固定的一主多辅规则；"
            "选择只能使用目录中的 ID，也可以明确返回空列表。方法是可组合思考框架，不是"
            "视频类型门禁、固定模板或 Tool 白名单；不得用关键词匹配代替语义判断。"
            "method_selection_rationale 简要说明为什么当前组合适用，后续重规划可保留、替换"
            "或移除。仅当最新上下文已经证明"
            "本次运行目标达成时设置 goal_complete=true；否则返回下一组有边界的有效步骤，"
            "并设置 goal_complete=false。每个步骤包含 id、title、kind、assigned_agent、"
            "可选的 tool_name 与 production_skill_role，以及 instructions、arguments、"
            "depends_on、artifact_refs。assigned_agent 只是执行者身份，不得从它的名称或"
            "用户文本猜测专业职责。每个 DELEGATION 都必须由你根据完整目标语义，显式选择"
            f"一个 production_skill_role：{role_values}。"
            "DELEGATION 的 arguments 应显式包含 why_now、constraints、confirmed_decisions、"
            "inherited_decision_ids、experience_tags、adjacent_boundaries、open_questions、"
            "acceptance_criteria、expected_output_schema、expected_output_slot_ids。"
            "expected_output_schema 描述专业 Agent 最终 AgentResult.deliverable 的"
            "结构化交接对象，不是任意创造新的 AgentResult 顶层字段；需要传递"
            "draft_version_id、clip_group_id、continuity_out 等任务特有信息时，"
            "把它们定义为 deliverable 的属性。"
            "expected_output_slot_ids 用于声明工作结果应绑定的稳定逻辑槽位，"
            "不因 Plan revision 改名。artifact_refs 是必读工件；"
            "adjacent_boundaries 由 relationship、artifact_refs 和可选 guidance 组成。"
            "边界可以引用已在必读列表中的同一精确工件，用于表达其入场或出场语义；"
            "当开场或收尾没有可引用的相邻工件时，也可以只提供 guidance。"
            "不要为了避免工件引用重复而删除有价值的连续性边界。"
            "对 script、bible、character、scene、shot、asset_version、production_spec、"
            "storyboard、storyboard_draft、generation_plan、clip_group、output_slot 与 timeline 等版本化"
            "工件必须提供明确 version。这些选择由你根据完整语义显式"
            "给出；没有适用内容时显式使用空列表或空对象。Loader 只解析"
            "和校验这些 ID，不会根据文本替你路由。\n\n"
            + skill_prompt
        )
        loaded_schema_ids: list[str] = []
        while True:
            payload = {
                "reason": reason,
                "context": context.model_dump(mode="json"),
                "available_tools": tool_catalog,
                "tool_schemas": {
                    tool_id: {
                        "input_schema": tools_by_id[tool_id].input_schema,
                        "output_schema": tools_by_id[tool_id].output_schema,
                    }
                    for tool_id in loaded_schema_ids
                },
                "available_production_methods": [
                    {
                        "id": item.id,
                        "label": item.label,
                        "description": item.description,
                        "supported_roles": [
                            role.value for role in item.supported_roles
                        ],
                        "user_overridable": item.user_overridable,
                    }
                    for item in method_skills
                ],
                "current_plan": None
                if current_plan is None
                else current_plan.model_dump(mode="json"),
            }
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
            serialized = _json_block(raw)
            try:
                return AgentPlanProposal.model_validate_json(serialized)
            except ValueError as plan_error:
                try:
                    schema_request = AgentToolSchemaRequest.model_validate_json(
                        serialized
                    )
                except ValueError as schema_error:
                    raise ProductionManagerError(
                        "Production Manager returned an invalid plan payload and not a "
                        "Tool schema request"
                    ) from schema_error
                unknown_ids = [
                    tool_id
                    for tool_id in schema_request.requested_tool_schema_ids
                    if tool_id not in tools_by_id
                ]
                if unknown_ids:
                    raise ProductionManagerError(
                        "Production Manager requested unknown Tool schemas: "
                        + ", ".join(unknown_ids)
                    ) from plan_error
                new_ids = [
                    tool_id
                    for tool_id in schema_request.requested_tool_schema_ids
                    if tool_id not in loaded_schema_ids
                ]
                if not new_ids:
                    raise ProductionManagerError(
                        "Production Manager repeated a Tool schema request without "
                        "selecting any new Tool"
                    ) from plan_error
                loaded_schema_ids.extend(new_ids)


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
        method_skills: Sequence[AgentProductionMethodSkillDescriptor],
        tools: Sequence[ToolDescriptor],
        current_plan: AgentPlanProposal | None,
        reason: str,
    ) -> AgentPlanProposal:
        del skill_prompt, method_skills, tools, reason
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
    reason: str = Field(default="Gather the evidence needed for the WorkOrder", max_length=1000)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


def _specialist_tool_history(work_order: WorkOrder) -> list[dict[str, object]]:
    """Project persisted specialist turns back into the model's compact history."""

    latest_exact_result_index: dict[str, int] = {}
    for index, turn in enumerate(work_order.specialist_turns):
        if turn.result is not None and turn.tool_name in {
            "project.get",
            "artifact.get_production_document",
        }:
            latest_exact_result_index[turn.tool_name] = index

    history: list[dict[str, object]] = []
    for index, turn in enumerate(work_order.specialist_turns):
        arguments = _compact_specialist_tool_arguments(
            turn.tool_name,
            turn.arguments,
        )
        item: dict[str, object] = {
            "request": {
                "tool_name": turn.tool_name,
                "arguments": arguments,
                "reason": turn.reason,
            }
        }
        if turn.result is not None:
            item["result"] = _compact_specialist_tool_result(
                turn.tool_name,
                turn.result,
                retain_exact_payload=(
                    latest_exact_result_index.get(turn.tool_name) == index
                ),
            )
        history.append(item)
    return history


def _compact_specialist_tool_arguments(
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Keep incremental storyboard history resumable without replaying prose."""

    storyboard_tools = {
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.replace_storyboard_draft_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    }
    if tool_name not in storyboard_tools:
        return arguments
    request_value = arguments.get("request")
    if not isinstance(request_value, dict):
        return arguments
    request = cast(dict[str, JsonValue], request_value)
    compact: dict[str, JsonValue] = {
        "project_id": arguments.get("project_id"),
    }
    if tool_name == "shot.begin_storyboard_draft":
        seed_value = request.get("seed")
        if not isinstance(seed_value, dict):
            compact["request"] = request
            return compact
        seed = cast(dict[str, JsonValue], seed_value)
        nodes_value = seed.get("scene_nodes")
        nodes: list[JsonValue] = []
        if isinstance(nodes_value, list):
            for raw_node in nodes_value:
                if not isinstance(raw_node, dict):
                    continue
                node = cast(dict[str, JsonValue], raw_node)
                nodes.append(
                    {
                        "id": node.get("id"),
                        "scene_id": node.get("scene_id"),
                        "sequence": node.get("sequence"),
                        "continuity_in": node.get("continuity_in"),
                        "continuity_out": node.get("continuity_out"),
                    }
                )
        compact["request"] = {
            "seed": {
                "project_id": seed.get("project_id"),
                "title": seed.get("title"),
                "script_version_id": seed.get("script_version_id"),
                "bible_version_id": seed.get("bible_version_id"),
                "production_spec_document_id": seed.get(
                    "production_spec_document_id"
                ),
                "production_spec_version_id": seed.get(
                    "production_spec_version_id"
                ),
                "base_storyboard_version_id": seed.get(
                    "base_storyboard_version_id"
                ),
                "scene_nodes": nodes,
            },
            "source_artifact_refs": request.get("source_artifact_refs"),
            "origin": request.get("origin"),
        }
        return compact

    if tool_name == "shot.append_storyboard_ledger_segment":
        entries_value = request.get("entries")
        entries = (
            [cast(dict[str, JsonValue], item) for item in entries_value if isinstance(item, dict)]
            if isinstance(entries_value, list)
            else []
        )
        source_line_ids: list[JsonValue] = []
        scene_node_ids: list[JsonValue] = []
        duration_seconds = 0.0
        for entry in entries:
            scene_node_id = entry.get("scene_node_id")
            if (
                isinstance(scene_node_id, str)
                and scene_node_id not in scene_node_ids
            ):
                scene_node_ids.append(scene_node_id)
            source_range = entry.get("source_range")
            if isinstance(source_range, dict):
                line_ids = source_range.get("script_line_ids")
                if isinstance(line_ids, list):
                    source_line_ids.extend(line_ids)
            duration = entry.get("allocated_duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                duration_seconds += float(duration)
        segment: dict[str, JsonValue] = {
            "entry_count": len(entries),
            "entry_ids": [entry.get("id") for entry in entries],
            "first_sequence": entries[0].get("sequence") if entries else None,
            "last_sequence": entries[-1].get("sequence") if entries else None,
            "scene_node_ids": scene_node_ids,
            "source_line_ids": source_line_ids,
            "allocated_duration_seconds": duration_seconds,
            "latest_continuity_out": (
                entries[-1].get("continuity_out") if entries else []
            ),
        }
        compact_request: dict[str, JsonValue] = {
            "draft_document_id": request.get("draft_document_id"),
            "base_version_id": request.get("base_version_id"),
            "segment": segment,
            "source_artifact_refs": request.get("source_artifact_refs"),
            "origin": request.get("origin"),
        }
        compact["request"] = compact_request
        return compact

    if tool_name == "shot.complete_storyboard_ledger":
        compact["request"] = {
            "draft_document_id": request.get("draft_document_id"),
            "base_version_id": request.get("base_version_id"),
            "source_artifact_refs": request.get("source_artifact_refs"),
            "origin": request.get("origin"),
        }
        return compact

    if tool_name == "shot.append_storyboard_outline_segment":
        groups_value = request.get("groups")
        groups = (
            [cast(dict[str, JsonValue], item) for item in groups_value if isinstance(item, dict)]
            if isinstance(groups_value, list)
            else []
        )
        compact_groups: list[JsonValue] = []
        for group in groups:
            source_range_value = group.get("source_range")
            source_range = (
                cast(dict[str, JsonValue], source_range_value)
                if isinstance(source_range_value, dict)
                else {}
            )
            compact_groups.append(
                {
                    "id": group.get("id"),
                    "scene_node_id": group.get("scene_node_id"),
                    "scene_group_sequence": group.get("scene_group_sequence"),
                    "source_range_id": source_range.get("id"),
                    "source_line_ids": source_range.get("script_line_ids"),
                    "timing_entry_ids": group.get("timing_entry_ids"),
                    "duration_seconds": group.get("duration_seconds"),
                    "continuity_out": group.get("continuity_out"),
                }
            )
        compact["request"] = {
            "draft_document_id": request.get("draft_document_id"),
            "base_version_id": request.get("base_version_id"),
            "groups": compact_groups,
            "source_artifact_refs": request.get("source_artifact_refs"),
            "origin": request.get("origin"),
        }
        return compact

    if tool_name == "shot.complete_storyboard_outline":
        audit_value = request.get("timing_audit")
        audit = (
            cast(dict[str, JsonValue], audit_value)
            if isinstance(audit_value, dict)
            else {}
        )
        compact["request"] = {
            "draft_document_id": request.get("draft_document_id"),
            "base_version_id": request.get("base_version_id"),
            "timing_audit": {
                "source_declared_duration_s": audit.get(
                    "source_declared_duration_s"
                ),
                "target_duration_s": audit.get("target_duration_s"),
                "dialogue_units_total": audit.get("dialogue_units_total"),
                "minimum_spoken_duration_s": audit.get(
                    "minimum_spoken_duration_s"
                ),
                "non_speech_reserve_s": audit.get("non_speech_reserve_s"),
                "computed_episode_duration_s": audit.get(
                    "computed_episode_duration_s"
                ),
                "clip_group_count": audit.get("clip_group_count"),
                "overloaded_shot_ids": audit.get("overloaded_shot_ids"),
                "overloaded_group_ids": audit.get("overloaded_group_ids"),
                "timing_audit_passed": audit.get("timing_audit_passed"),
            },
            "source_artifact_refs": request.get("source_artifact_refs"),
            "origin": request.get("origin"),
        }
        return compact
    if tool_name == "shot.finalize_storyboard":
        compact["request"] = {
            "draft_document_id": request.get("draft_document_id"),
            "draft_version_id": request.get("draft_version_id"),
            "origin": request.get("origin"),
        }
        return compact

    if tool_name == "shot.record_storyboard_draft_verification":
        compact["request"] = {
            "draft_document_id": request.get("draft_document_id"),
            "base_version_id": request.get("base_version_id"),
            "report": request.get("report"),
            "origin": request.get("origin"),
        }
        return compact

    work_unit_value = request.get("work_unit")
    group_value = request.get("group")
    work_unit = (
        cast(dict[str, JsonValue], work_unit_value)
        if isinstance(work_unit_value, dict)
        else {}
    )
    group = (
        cast(dict[str, JsonValue], group_value)
        if isinstance(group_value, dict)
        else {}
    )
    clip_value = group.get("clip_group")
    clip_group = (
        cast(dict[str, JsonValue], clip_value)
        if isinstance(clip_value, dict)
        else {}
    )
    shots_value = group.get("shots")
    shots: list[JsonValue] = []
    if isinstance(shots_value, list):
        for raw_shot in shots_value:
            if not isinstance(raw_shot, dict):
                continue
            shot = cast(dict[str, JsonValue], raw_shot)
            shots.append(
                {
                    "id": shot.get("id"),
                    "sequence": shot.get("sequence"),
                    "scene_id": shot.get("scene_id"),
                    "duration_seconds": shot.get("duration_seconds"),
                    "dialogue_line_ids": shot.get("dialogue_line_ids"),
                    "narrative_purpose": shot.get("narrative_purpose"),
                }
            )
    compact["request"] = {
        "draft_document_id": request.get("draft_document_id"),
        "base_version_id": request.get("base_version_id"),
        "work_unit": {
            "id": work_unit.get("id"),
            "title": work_unit.get("title"),
            "source_range": work_unit.get("source_range"),
            "objective": work_unit.get("objective"),
        },
        "group": {
            "work_unit_id": group.get("work_unit_id"),
            "scene_node_id": group.get("scene_node_id"),
            "scene_group_sequence": group.get("scene_group_sequence"),
            "source_range": group.get("source_range"),
            "shots": shots,
            "clip_group": {
                "id": clip_group.get("id"),
                "title": clip_group.get("title"),
                "shot_ids": clip_group.get("shot_ids"),
                "duration_seconds": clip_group.get("duration_seconds"),
                "continuity_out": clip_group.get("continuity_out"),
            },
            "handoff_notes": group.get("handoff_notes"),
        },
        "origin": request.get("origin"),
    }
    if tool_name == "shot.replace_storyboard_draft_group":
        compact_request = cast(dict[str, JsonValue], compact["request"])
        compact_request["replaced_clip_group_id"] = request.get(
            "replaced_clip_group_id"
        )
    return compact


def _compact_specialist_tool_result(
    tool_name: str,
    result: dict[str, JsonValue],
    *,
    retain_exact_payload: bool = True,
) -> dict[str, JsonValue]:
    """Keep exact storyboard handoffs without replaying the authored document.

    The persisted SpecialistTurn remains lossless.  This projection is used only
    in the next language-model input, where artifact refs and compact progress are
    sufficient to resume work.  In particular, a finalized Storyboard may contain
    many long camera and performance descriptions that should be resolved from its
    exact versioned artifact instead of copied into Tool history again.
    """

    if tool_name == "project.get":
        compact_project: dict[str, JsonValue] = {
            "tool_id": result.get("tool_id"),
            "status": result.get("status"),
            "artifact_refs": result.get("artifact_refs"),
            "warnings": result.get("warnings"),
            "error": result.get("error"),
        }
        output_value = result.get("output")
        if not isinstance(output_value, dict):
            compact_project["output"] = output_value
            return compact_project
        output = cast(dict[str, JsonValue], output_value)
        document_value = output.get("document")
        if not isinstance(document_value, dict):
            compact_project["output"] = output
            return compact_project
        document = cast(dict[str, JsonValue], document_value)
        project_value = document.get("project")
        project = (
            cast(dict[str, JsonValue], project_value)
            if isinstance(project_value, dict)
            else {}
        )
        current_script_id = project.get("current_script_version_id")
        current_bible_id = project.get("current_bible_version_id")
        script_versions_value = document.get("script_versions")
        current_scripts: list[JsonValue] = (
            [
                cast(dict[str, JsonValue], item)
                for item in script_versions_value
                if isinstance(item, dict) and item.get("id") == current_script_id
            ]
            if isinstance(script_versions_value, list)
            and retain_exact_payload
            else []
        )
        bible_versions_value = document.get("bible_versions")
        current_bibles: list[JsonValue] = (
            [
                cast(dict[str, JsonValue], item)
                for item in bible_versions_value
                if isinstance(item, dict) and item.get("id") == current_bible_id
            ]
            if isinstance(bible_versions_value, list)
            and retain_exact_payload
            else []
        )
        production_documents_value = document.get("production_documents")
        production_document_refs: list[JsonValue] = []
        if isinstance(production_documents_value, list):
            for raw_document in production_documents_value:
                if not isinstance(raw_document, dict):
                    continue
                production_document = cast(
                    dict[str, JsonValue], raw_document
                )
                versions_value = production_document.get("versions")
                current_version_id = production_document.get(
                    "current_version_id"
                )
                current_version: dict[str, JsonValue] | None = None
                if isinstance(versions_value, list):
                    raw_version = next(
                        (
                            item
                            for item in versions_value
                            if isinstance(item, dict)
                            and item.get("id") == current_version_id
                        ),
                        None,
                    )
                    if isinstance(raw_version, dict):
                        version = cast(dict[str, JsonValue], raw_version)
                        current_version = {
                            "id": version.get("id"),
                            "version": version.get("version"),
                            "origin": version.get("origin"),
                            "created_at": version.get("created_at"),
                        }
                production_document_refs.append(
                    {
                        "id": production_document.get("id"),
                        "kind": production_document.get("kind"),
                        "title": production_document.get("title"),
                        "current_version_id": current_version_id,
                        "current_version": current_version,
                    }
                )
        output_slots_value = document.get("output_slots")
        output_slots: list[JsonValue] = []
        if isinstance(output_slots_value, list):
            for raw_slot in output_slots_value:
                if not isinstance(raw_slot, dict):
                    continue
                slot = cast(dict[str, JsonValue], raw_slot)
                output_slots.append(
                    {
                        "id": slot.get("id"),
                        "revision": slot.get("revision"),
                        "current_artifact_ref": slot.get(
                            "current_artifact_ref"
                        ),
                        "invalidation_reason": slot.get(
                            "invalidation_reason"
                        ),
                    }
                )
        document_summary: dict[str, JsonValue] = {
            "project": project,
            "planning": document.get("planning"),
            # Keep the exact current screenplay, including per-scene action
            # text, while removing historical Script/Bible versions.
            "script_versions": current_scripts,
            "bible_versions": current_bibles,
            "production_documents": production_document_refs,
            "production_spec_document_id": document.get(
                "production_spec_document_id"
            ),
            "storyboard_document_id": document.get(
                "storyboard_document_id"
            ),
            "generation_plan_document_id": document.get(
                "generation_plan_document_id"
            ),
            "output_slots": output_slots,
        }
        compact_project["output"] = {
            "document": document_summary,
            "progress": output.get("progress"),
        }
        return compact_project

    if tool_name == "artifact.get_production_document":
        compact_document: dict[str, JsonValue] = {
            "tool_id": result.get("tool_id"),
            "status": result.get("status"),
            "artifact_refs": result.get("artifact_refs"),
            "warnings": result.get("warnings"),
            "error": result.get("error"),
        }
        output_value = result.get("output")
        if not isinstance(output_value, dict):
            compact_document["output"] = output_value
            return compact_document
        output = cast(dict[str, JsonValue], output_value)
        document_value = output.get("document")
        version_value = output.get("version")
        document = (
            cast(dict[str, JsonValue], document_value)
            if isinstance(document_value, dict)
            else {}
        )
        version = (
            cast(dict[str, JsonValue], version_value)
            if isinstance(version_value, dict)
            else {}
        )
        selected_version: dict[str, JsonValue] = {
            "id": version.get("id"),
            "version": version.get("version"),
            "parent_version_id": version.get("parent_version_id"),
            "source_artifact_refs": version.get("source_artifact_refs"),
            "origin": version.get("origin"),
            "created_at": version.get("created_at"),
        }
        payload_value = version.get("payload")
        if retain_exact_payload:
            selected_version["payload"] = payload_value
        elif isinstance(payload_value, dict):
            payload = cast(dict[str, JsonValue], payload_value)
            selected_version["payload_summary"] = {
                "phase": payload.get("phase"),
                "script_version_id": payload.get("script_version_id"),
                "bible_version_id": payload.get("bible_version_id"),
            }
        compact_document["output"] = {
            "document": {
                "id": document.get("id"),
                "project_id": document.get("project_id"),
                "kind": document.get("kind"),
                "title": document.get("title"),
                "current_version_id": document.get("current_version_id"),
            },
            # The selected immutable version is the only exact payload needed
            # for the next decision. document.versions is intentionally omitted.
            "version": selected_version,
        }
        return compact_document

    if tool_name not in {
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.replace_storyboard_draft_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    }:
        return result

    compact: dict[str, JsonValue] = {
        "tool_id": result.get("tool_id"),
        "status": result.get("status"),
        "artifact_refs": result.get("artifact_refs"),
        "warnings": result.get("warnings"),
        "error": result.get("error"),
    }
    output_value = result.get("output")
    if output_value is None:
        compact["output"] = None
        return compact
    if not isinstance(output_value, dict):
        compact["output"] = output_value
        return compact
    output = cast(dict[str, JsonValue], output_value)

    if tool_name in {
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.replace_storyboard_draft_group",
        "shot.record_storyboard_draft_verification",
    }:
        progress_value = output.get("progress")
        if not isinstance(progress_value, dict):
            compact["output"] = output
            return compact
        progress = cast(dict[str, JsonValue], progress_value)
        compact["output"] = {
            "progress": {
                "draft_ref": progress.get("draft_ref"),
                "draft_version_id": progress.get("draft_version_id"),
                "phase": progress.get("phase"),
                "scene_node_count": progress.get("scene_node_count"),
                "timing_ledger_entry_count": progress.get(
                    "timing_ledger_entry_count"
                ),
                "ledger_covered_script_line_count": progress.get(
                    "ledger_covered_script_line_count"
                ),
                "outlined_timing_entry_count": progress.get(
                    "outlined_timing_entry_count"
                ),
                "outlined_script_line_count": progress.get(
                    "outlined_script_line_count"
                ),
                "planned_clip_group_count": progress.get(
                    "planned_clip_group_count"
                ),
                "committed_clip_group_count": progress.get(
                    "committed_clip_group_count"
                ),
                "committed_shot_count": progress.get("committed_shot_count"),
                "total_planned_duration_seconds": progress.get(
                    "total_planned_duration_seconds"
                ),
                "committed_duration_seconds": progress.get(
                    "committed_duration_seconds"
                ),
                "remaining_duration_seconds": progress.get(
                    "remaining_duration_seconds"
                ),
                "covered_script_line_count": progress.get(
                    "covered_script_line_count"
                ),
                "remaining_script_line_count": progress.get(
                    "remaining_script_line_count"
                ),
                "scene_progress": progress.get("scene_progress"),
                "next_unplanned_scene_node_id": progress.get(
                    "next_unplanned_scene_node_id"
                ),
                "next_unplanned_script_line_id": progress.get(
                    "next_unplanned_script_line_id"
                ),
                "latest_planned_continuity_out": progress.get(
                    "latest_planned_continuity_out"
                ),
                "latest_scene_node_id": progress.get("latest_scene_node_id"),
                "latest_clip_group_id": progress.get("latest_clip_group_id"),
                "latest_shot_ids": progress.get("latest_shot_ids"),
                "latest_continuity_out": progress.get(
                    "latest_continuity_out"
                ),
                "verification_status": progress.get("verification_status"),
            }
        }
        return compact

    storyboard_value = output.get("storyboard")
    document_value = output.get("document")
    version_value = output.get("version")
    output_slot_value = output.get("output_slot")
    storyboard = (
        cast(dict[str, JsonValue], storyboard_value)
        if isinstance(storyboard_value, dict)
        else {}
    )
    document = (
        cast(dict[str, JsonValue], document_value)
        if isinstance(document_value, dict)
        else {}
    )
    version = (
        cast(dict[str, JsonValue], version_value)
        if isinstance(version_value, dict)
        else {}
    )
    output_slot = (
        cast(dict[str, JsonValue], output_slot_value)
        if isinstance(output_slot_value, dict)
        else {}
    )
    scene_nodes = storyboard.get("scene_nodes")
    shots = storyboard.get("shots")
    clip_groups = storyboard.get("clip_groups")
    script_line_ids = storyboard.get("script_line_ids")
    compact["output"] = {
        "canonical_storyboard_ref": output_slot.get("current_artifact_ref"),
        "storyboard_summary": {
            "project_id": storyboard.get("project_id"),
            "title": storyboard.get("title"),
            "script_version_id": storyboard.get("script_version_id"),
            "bible_version_id": storyboard.get("bible_version_id"),
            "production_spec_document_id": storyboard.get(
                "production_spec_document_id"
            ),
            "production_spec_version_id": storyboard.get(
                "production_spec_version_id"
            ),
            "computed_duration_seconds": storyboard.get(
                "computed_duration_seconds"
            ),
            "scene_count": len(scene_nodes) if isinstance(scene_nodes, list) else 0,
            "clip_group_count": (
                len(clip_groups) if isinstance(clip_groups, list) else 0
            ),
            "shot_count": len(shots) if isinstance(shots, list) else 0,
            "script_line_count": (
                len(script_line_ids) if isinstance(script_line_ids, list) else 0
            ),
        },
        "document": {
            "id": document.get("id"),
            "project_id": document.get("project_id"),
            "kind": document.get("kind"),
            "title": document.get("title"),
            "current_version_id": document.get("current_version_id"),
        },
        "version": {
            "id": version.get("id"),
            "version": version.get("version"),
            "parent_version_id": version.get("parent_version_id"),
            "origin": version.get("origin"),
            "created_at": version.get("created_at"),
        },
        "output_slot": {
            "id": output_slot.get("id"),
            "revision": output_slot.get("revision"),
            "current_artifact_ref": output_slot.get("current_artifact_ref"),
            "producer_agent_run_id": output_slot.get("producer_agent_run_id"),
            "producer_work_order_id": output_slot.get("producer_work_order_id"),
            "updated_at": output_slot.get("updated_at"),
        },
    }
    return compact


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
                "version": item.version,
                "category": item.category.value,
                "description": item.description,
                "effect": item.effect.value,
                "idempotent": item.idempotent,
                "tags": item.tags,
            }
            for item in descriptors
        ]
        tools_by_id = {item.id: item for item in descriptors}
        may_call_tool = bool(descriptors) and self._tools is not None
        instructions = (
            "你是当前 WorkOrder 指定的专业 Agent。请依据下方对应角色的 Production Skill，"
            "处理已经限定范围的目标和所提供的工件摘要，不要扩大任务范围。"
            "你与 Production Manager 共用同一套 Tool Catalog。"
            f"{_AGENT_INTERACTION_PRIORITY} 当可以自主继续时，把选择写入 decisions 或 assumptions；"
            "只有确实需要暂停时才返回 PARTIAL 并将 next_owner 设为 USER_INPUT。"
            + (
                "available_tools 只是紧凑目录。如果判断确实需要某些 Tool 的精确"
                "合同，先只返回 requested_tool_schema_ids 和 rationale 的 JSON；"
                "运行时会仅补充这些 schema 到 tool_schemas，不得重复请求已加载 ID。"
                "如果还需要补充证据或执行范围内的修改，返回包含 tool_name、reason、"
                "arguments 的 JSON，只提出一个当前 Tool 请求。请求会先持久化，由后续独立"
                "推进执行；否则直接返回最终结果。"
                if may_call_tool
                else "当前没有可用 Tool，请直接返回最终结果。"
            )
            + "如果 WorkOrder 要编写分镜，对外最终仍是一份完整 Storyboard。"
            "如果当前 WorkOrder 的语义责任是编译全片时长账本与分组大纲，"
            "先检查当前 Tool history 是否已有当前精确 ScriptVersion 和 "
            "ProjectBibleVersion 正文；如果没有，第一个 Tool 请求必须是 project.get。"
            "不能只根据 ArtifactRef、局部摘要、Leader 转述或当前一句开始分组。"
            "先用 shot.begin_storyboard_draft 仅建立锁定源版本和场次连续性意图的轻量草稿。"
            "然后由你根据剧情语义自主选择连续范围，用 "
            "shot.append_storyboard_ledger_segment 持久化已稳定的账本片段；"
            "每次成功后读取新的 draft_version_id、下一条源游标和精确连续性，"
            "仍在本 WorkOrder 内继续。片段只是可恢复检查点，不要设固定字数、条数、"
            "场次数或调用次数，也不把它当成 ClipGroup 或 ComfyUI 节点。"
            "每个场次只有最后一个 timing entry 显式设置 completes_scene=true；"
            "场内其他条目必须为 false。即使该场没有台词或台词已结束，也要以最后的"
            "可见动作条目标记完成，让游标基于场次完成事实推进；不要因为一次片段"
            "边界就标记场次完成。"
            "在首次封结 ledger 前，必须用账本中的精确 allocations 前瞻确认："
            "每个场次的有序 timing entries 都能分成连续、完整、不跨场的组，"
            "且每组 allocated_duration_seconds 之和精确为 4–15 秒整数。"
            "如果当前条目无法合法分组，先拆分或调整确实可分的动作、反应和停顿预留，"
            "但不改台词、事件或自然表演总意图，也不从 120 秒等用户目标总时长倒推分配。"
            "不得先封存账本，再在 outline 阶段发现无法装箱。"
            "只在账本完整覆盖剧本源顺序且上述分区可行时，调用 "
            "shot.complete_storyboard_ledger。"
            "调用完成后必须先用 artifact.get_production_document 读取刚封结的"
            "storyboard_draft 最新精确版本，从其完整 timing_ledger 进行全局重装箱；"
            "不能凭 Tool 进度摘要、Leader 转述或自己的上轮文本重建账本。"
            "随后用 shot.append_storyboard_outline_segment "
            "持久化已稳定的大纲前缀，仍由剧情和连续性决定每次范围。"
            "全部 ledger 条目已按顺序被 4–15 秒整数、不跨场次的 ClipGroup 大纲覆盖后，"
            "再用 artifact.get_production_document 读取最新精确草稿，对完整 ledger "
            "与 planned_clip_groups 做全局核对，然后用 "
            "shot.complete_storyboard_outline 写入 timing_audit。"
            "begin、ledger 增量、ledger 完成、outline 增量和 outline 完成是同一 Planner "
            "WorkOrder 的连续工作。每次 Tool 结果会回到你手上，其中的真实 draft "
            "document/version ID 可直接用于下一次请求；不要仅因为 begin 产生了新 ID "
            "或进入下一片段就结束 WorkOrder、返回 LEADER_REPLAN。"
            "如果你在精确分配后仍发现当前草稿无法形成合法分区，这仍是同一编译职责："
            "可以在本 WorkOrder 内再请求一次 shot.begin_storyboard_draft 开启新草稿分支，"
            "并从账本到 outline 完整重编译；不得仅为重启草稿返回 Leader。"
            "如果 WorkOrder 的语义责任是创作详细镜头组，Director 必须先用 "
            "artifact.get_production_document 读取 storyboard_draft 最新精确版本中已完成的全片 "
            "planned_clip_groups、timing_audit 和最新持久化进度，严格沿计划范围"
            "逐组创作，不临时增加填充反应、摄影装饰或未规划镜头组来扩张时长。"
            "每次只用 shot.append_storyboard_group 提交一个已经完整设计好的 "
            "ClipGroup；一个 ClipGroup 是 4–15 秒整数的未来 H3 生成单元，可以包含多个 "
            "Shot，Shot 保留景别、机位、动作、逐字台词、表演、情绪、声音和衔接，其剪辑时长可为小数。"
            "同一 WorkOrder 覆盖多个 planned ClipGroup 时，append 成功后读取新的 "
            "draft_version_id 并在本 WorkOrder 内继续下一组；不要仅因为需要下一组就"
            "返回 LEADER_REPLAN，也不要重写已完成组。只有语义范围确实需要改变时"
            "才交回 Leader。下一组必须以草稿最新 ClipGroup 的真实 continuity_out "
            "为事实基础，但无需逐字复制。同场次要语义承接人物、道具、站位、动作和声音；"
            "跨场次要明确上一场退场/结束状态、下一场入场/起始状态和时间或空间转换。"
            "人物、道具或站位发生变化时，本组必须包含观众可见的过渡动作，不能让 Leader "
            "摘要替代工件末态。组内镜头语言、表演或连续性"
            "返修使用 shot.replace_storyboard_draft_group，并保持计划组的 ID、范围、"
            "顺序和时长；若发现账本、时长、来源范围或组边界错误，应把结论交回 Leader，"
            "由 Leader 从当前剧本重新编译并 begin 新的完整草稿，不能用 replace 偷改计划。"
            "如果 WorkOrder 的职责是 REVISION_VERIFIER，必须先用 "
            "artifact.get_production_document 读取当前最新精确版本，通读完整草稿并依据"
            "已选方法的完整分镜检查项审阅。对每个组间边界做内容判断："
            "同场次查可见状态与动作是否自然承接，跨场次查上一场退场、下一场入场和"
            "时空转换是否清楚；不用 continuity 文本逐字相等代替语义判断。然后调用 "
            "shot.record_storyboard_draft_verification，写入 READY 或 "
            "NEEDS_REVISION；审阅任务本身随后以 COMPLETED 交回 Leader，由 Leader "
            "决定 finalize 或定向修订，不用 Critic 循环或固定轮次。"
            "只有完整覆盖剧本的对白、可见动作、反应和状态变化且语义核对接受后，"
            "才调用 shot.finalize_storyboard。场次索引是单份 Storyboard 内的结构，"
            "不是 ComfyUI 节点或单独文档。"
            + "最终结果必须是符合 AgentWorkOrderResultRequest 的 JSON，字段包括 status、"
            "concise_summary、deliverable、created_artifacts、updated_artifacts、findings、decisions、"
            "assumptions、unresolved_questions、evidence_refs、recommended_next_actions、"
            "next_owner。只有 PARTIAL 需要指定下一责任方：需要主 Agent 改计划时用 "
            "LEADER_REPLAN，确实缺少用户决定时用 USER_INPUT。Tool 请求不要伪装成"
            "最终 PARTIAL，继续返回独立 Tool 请求结构。"
            "如果 WorkOrder.expected_output_schema 非空，COMPLETED 结果必须把与该"
            "schema 匹配的任务特有结构写入 deliverable；不要把这些字段塞进"
            "ArtifactRef 或虚构为 AgentResult 顶层字段。"
            "请求的分析或决定已经完成时使用 COMPLETED；缺少实质输入时使用 PARTIAL；"
            "只有目标确实无法处理时才使用 FAILED。\n\n"
            "如果 response_protocol_feedback 非空，它是你在同一 WorkOrder "
            "上一轮响应的结构协议校验反馈，不是剧情、创意或任务拆分意见。"
            "保留 rejected_response 中已经完成的专业判断和内容，仅依据 "
            "validation_issues 修正交接结构；只返回一个符合 response_schemas "
            "之一的 JSON 对象，不要追加说明、第二个 JSON 或请求 Leader "
            "重新规划。\n\n"
            + work_order.system_prompt
        )
        protocol_feedback = work_order.work_order.pending_protocol_feedback
        loaded_schema_ids = list(
            dict.fromkeys(
                tool_id
                for tool_id in (
                    []
                    if protocol_feedback is None
                    else protocol_feedback.loaded_tool_schema_ids
                )
                if tool_id in tools_by_id
            )
        )
        while True:
            raw = self._client.generate_text(
                instructions=instructions,
                input_text=json.dumps(
                    {
                        "work_order": work_order.work_order.model_dump(
                            mode="json",
                            exclude={
                                "specialist_turns",
                                "pending_protocol_feedback",
                            },
                        ),
                        "context": work_order.context.model_dump(mode="json"),
                        "progress_entries": [
                            entry.model_dump(mode="json")
                            for entry in work_order.progress_entries
                        ],
                        "available_tools": tool_catalog,
                        "tool_schemas": {
                            tool_id: {
                                "input_schema": tools_by_id[tool_id].input_schema,
                                "output_schema": tools_by_id[tool_id].output_schema,
                            }
                            for tool_id in loaded_schema_ids
                        },
                        "response_schemas": {
                            "final_result": (
                                AgentWorkOrderResultRequest.model_json_schema()
                            ),
                            "tool_request": SpecialistToolRequest.model_json_schema(),
                            "tool_schema_request": (
                                AgentToolSchemaRequest.model_json_schema()
                            ),
                        },
                        "response_protocol_feedback": (
                            None
                            if protocol_feedback is None
                            else protocol_feedback.model_dump(mode="json")
                        ),
                        "tool_history": _specialist_tool_history(
                            work_order.work_order
                        ),
                    },
                    ensure_ascii=False,
                ),
                # A bounded ClipGroup Tool request can still exceed 5k tokens once
                # detailed shots and the model's reasoning share the completion
                # budget. This is transport headroom, not a creative length target.
                max_output_tokens=10000,
            )
            payload = _normalize_specialist_response_payload(raw)
            try:
                result = AgentWorkOrderResultRequest.model_validate_json(payload)
                break
            except ValueError as result_error:
                if not may_call_tool:
                    raise SpecialistResponseProtocolError(
                        SpecialistProtocolFeedback(
                            id=f"specialist-protocol-feedback-{uuid4().hex}",
                            rejected_response=payload,
                            validation_issues={
                                "agent_result": _validation_issues(result_error)
                            },
                            loaded_tool_schema_ids=list(loaded_schema_ids),
                        )
                    ) from result_error
                try:
                    return SpecialistToolRequest.model_validate_json(payload)
                except ValueError as tool_error:
                    try:
                        schema_request = AgentToolSchemaRequest.model_validate_json(
                            payload
                        )
                    except ValueError as schema_error:
                        raise SpecialistResponseProtocolError(
                            SpecialistProtocolFeedback(
                                id=f"specialist-protocol-feedback-{uuid4().hex}",
                                rejected_response=payload,
                                validation_issues={
                                    "agent_result": _validation_issues(result_error),
                                    "tool_request": _validation_issues(tool_error),
                                    "tool_schema_request": _validation_issues(
                                        schema_error
                                    ),
                                },
                                loaded_tool_schema_ids=list(loaded_schema_ids),
                            )
                        ) from schema_error
                    unknown_ids = [
                        tool_id
                        for tool_id in schema_request.requested_tool_schema_ids
                        if tool_id not in tools_by_id
                    ]
                    if unknown_ids:
                        raise ProductionManagerError(
                            "Specialist Agent requested unknown Tool schemas: "
                            + ", ".join(unknown_ids)
                        ) from tool_error
                    new_ids = [
                        tool_id
                        for tool_id in schema_request.requested_tool_schema_ids
                        if tool_id not in loaded_schema_ids
                    ]
                    if not new_ids:
                        raise ProductionManagerError(
                            "Specialist Agent repeated a Tool schema request without "
                            "selecting any new Tool"
                        ) from tool_error
                    loaded_schema_ids.extend(new_ids)

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
        self._project_tool_locks_guard = threading.RLock()
        self._project_tool_locks: dict[str, threading.Lock] = {}
        self._recover_interrupted_operations()

    def capability_catalog(self) -> AgentCapabilityCatalog:
        roles = [role.value for role in _ACTIVE_PRODUCTION_SKILL_ROLES]
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
            production_methods=[
                _agent_method_skill_descriptor(descriptor)
                for descriptor in production_method_skill_catalog()
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
        client = (
            None
            if request.proposed_plan is not None
            else self._language_client_resolver()
        )
        planning = document.planning
        semantic_planning_available = (
            client is not None
            and planning is not None
            and planning.run_id is not None
            and planning.owner_agent_run_id == run_id
            and planning.status != PlanningRunStatus.COMPLETE
        )
        proposal = self._planning_precondition_plan(
            agent_run_id=run_id,
            document=document,
            semantic_planning_available=semantic_planning_available,
        )
        if proposal is None:
            proposal = request.proposed_plan
        if proposal is None:
            current = (
                None
                if snapshot.current_plan is None
                else AgentPlanProposal(
                    summary=snapshot.current_plan.summary,
                    steps=snapshot.current_plan.steps,
                    selected_method_skill_ids=(
                        snapshot.current_plan.selected_method_skill_ids
                    ),
                    method_selection_rationale=(
                        snapshot.current_plan.method_selection_rationale
                    ),
                    goal_complete=snapshot.current_plan.goal_complete,
                )
            )
            if semantic_planning_available:
                planning_stage = self._planning_precondition_plan(
                    agent_run_id=run_id,
                    document=document,
                    semantic_planning_available=False,
                )
                if planning_stage is not None:
                    current = planning_stage
            profile = document.project.production_profile
            current_method_ids = (
                [] if current is None else current.selected_method_skill_ids
            )
            compiled = compile_production_prompt(
                profile,
                roles=(
                    ProductionSkillRole.PLANNER,
                    ProductionSkillRole.DIRECTOR,
                ),
                method_skill_ids=_method_skill_ids_for_role(
                    current_method_ids,
                    ProductionSkillRole.PLANNER,
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
                method_skills=[
                    _agent_method_skill_descriptor(descriptor)
                    for descriptor in production_method_skill_catalog()
                ],
                tools=_tools_for_execution_target(
                    self.tools.list(),
                    snapshot.run.execution_target,
                ),
                current_plan=current,
                reason=request.reason,
            )
            if semantic_planning_available:
                selected_ids, selection_rationale = (
                    self._planning_precondition_method_selection(
                        agent_run_id=run_id,
                        document=document,
                    )
                )
                if (
                    selected_ids
                    and not proposal.selected_method_skill_ids
                    and proposal.method_selection_rationale is None
                ):
                    proposal = proposal.model_copy(
                        update={
                            "selected_method_skill_ids": selected_ids,
                            "method_selection_rationale": selection_rationale,
                        }
                    )
            if semantic_planning_available and not self._is_content_planning_plan(
                proposal,
                agent_run_id=run_id,
                document=document,
            ):
                fallback = self._planning_precondition_plan(
                    agent_run_id=run_id,
                    document=document,
                    semantic_planning_available=False,
                )
                if fallback is None:
                    raise ProductionManagerError(
                        "Content planning is incomplete, but the semantic planner "
                        "returned downstream work and no safe planning continuation exists"
                    )
                proposal = fallback
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
        semantic_planning_available: bool = False,
    ) -> AgentPlanProposal | None:
        """Keep persisted long-form planning ahead of downstream production work."""

        planning = document.planning
        selected_method_skill_ids, method_selection_rationale = (
            self._planning_precondition_method_selection(
                agent_run_id=agent_run_id,
                document=document,
            )
        )
        if (
            document.project.content_planning_output
            == ContentPlanningOutput.SCRIPT_ONLY
            and document.project.stage == ProjectStage.SCRIPT_READY
            and planning is not None
            and planning.status == PlanningRunStatus.COMPLETE
            and planning.owner_agent_run_id == agent_run_id
        ):
            return AgentPlanProposal(
                summary=(
                    "The requested screenplay is complete; stop before shot, asset, image, "
                    "video, or timeline production."
                ),
                steps=[],
                selected_method_skill_ids=selected_method_skill_ids,
                method_selection_rationale=method_selection_rationale,
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
                selected_method_skill_ids=selected_method_skill_ids,
                method_selection_rationale=method_selection_rationale,
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
                selected_method_skill_ids=selected_method_skill_ids,
                method_selection_rationale=method_selection_rationale,
            )

        if planning.status in {
            PlanningRunStatus.FAILED,
            PlanningRunStatus.NEEDS_ATTENTION,
        }:
            # A failed content unit is context for the semantic Production Manager,
            # not an automatic user-approval gate.  Without that Agent, the bounded
            # planning Tool remains the deterministic recovery path.
            if semantic_planning_available:
                return None

        if semantic_planning_available:
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
            selected_method_skill_ids=selected_method_skill_ids,
            method_selection_rationale=method_selection_rationale,
        )

    @staticmethod
    def _is_content_planning_plan(
        proposal: AgentPlanProposal,
        *,
        agent_run_id: str,
        document: ProjectDocument,
    ) -> bool:
        """Keep an unfinished PlanningRun ahead of every downstream action."""

        if proposal.goal_complete or not proposal.steps:
            return False
        planning = document.planning
        if (
            planning is None
            or planning.run_id is None
            or planning.owner_agent_run_id != agent_run_id
            or planning.status == PlanningRunStatus.COMPLETE
        ):
            return False
        for step in proposal.steps:
            if step.kind == ActionKind.USER_INPUT:
                continue
            if step.kind != ActionKind.TOOL_CALL or step.tool_name != "planning.advance":
                return False
            if (
                step.arguments.get("project_id") != document.project.id
                or step.arguments.get("run_id") != planning.run_id
                or step.arguments.get("owner_agent_run_id") != agent_run_id
            ):
                return False
        return True

    def _planning_precondition_method_selection(
        self,
        *,
        agent_run_id: str,
        document: ProjectDocument,
    ) -> tuple[list[str], str | None]:
        """Keep semantic method selection across generated planning snapshots."""

        snapshot = self.runtime.snapshot(agent_run_id)
        current_plan = snapshot.current_plan
        selected_ids = (
            list(current_plan.selected_method_skill_ids)
            if current_plan is not None
            else _selected_method_skill_ids(snapshot.run.active_skill_ids)
        )
        rationale = (
            current_plan.method_selection_rationale
            if current_plan is not None
            else None
        )
        planning = document.planning
        if planning is None or planning.run_id is None:
            return selected_ids, rationale
        planning_run = self.projects.get_planning_run(planning.run_id)
        if (
            planning_run is None
            or planning_run.owner_agent_run_id != agent_run_id
            or PlanningRunStep.STORY_ORIENTATION not in planning_run.completed_steps
        ):
            return selected_ids, rationale
        return (
            list(planning_run.selected_method_skill_ids),
            planning_run.method_selection_rationale,
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

        self._return_leader_owned_work_orders(run_id)
        self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
        executed: list[str] = []
        work_orders: list[str] = []
        notes: list[str] = []
        remaining_actions = request.max_actions
        planning_progress_boundary_reached = False
        continue_owned_planning_cursor = False

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

            carried_waiting, carried_failed, invalid_carried_ids = (
                self._carried_work_order_state(plan)
            )
            if carried_failed or invalid_carried_ids:
                details = [
                    f"{work_order.id}={work_order.status.value}"
                    for work_order in carried_failed
                ]
                details.extend(
                    f"{work_order_id}=missing-or-detached"
                    for work_order_id in invalid_carried_ids
                )
                reason = (
                    "Revise the plan after carried WorkOrders stopped without a "
                    "successful handoff: "
                    + "; ".join(details)
                )
                if not self._automatic_replan_unlocked(run_id, reason, notes):
                    break
                continue

            running_planning_stage = self._running_content_planning_stage(
                current,
                plan,
            )
            if running_planning_stage is not None:
                if planning_progress_boundary_reached or remaining_actions <= 0:
                    self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
                    break
                stage_action, stage_step = running_planning_stage
                action, _work_order, note = self._execute_step(
                    run_id,
                    plan.id,
                    stage_step,
                    existing_action=stage_action,
                    persist_planning_stage=True,
                )
                executed.append(action.id)
                remaining_actions -= 1
                planning_progress_boundary_reached = True
                if note:
                    notes.append(note)
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
                    planning_cursor_before = self._planning_cursor_state_for_step(
                        run_id,
                        step,
                    )
                    action, work_order, note = self._execute_step(
                        run_id,
                        plan.id,
                        step,
                        persist_planning_stage=(
                            step.tool_name == "planning.advance"
                            and not self._planning_step_has_user_input_boundary(
                                plan,
                                step.id,
                            )
                        ),
                    )
                    executed.append(action.id)
                    remaining_actions -= 1
                    if step.tool_name == "planning.advance" and action.status in {
                        ActionStatus.RUNNING,
                        ActionStatus.SUCCEEDED,
                    }:
                        planning_progress_boundary_reached = True
                        completed_planning_action = True
                        if action.status == ActionStatus.SUCCEEDED:
                            planning_cursor_after = (
                                self._planning_cursor_state_for_step(run_id, step)
                            )
                            continue_owned_planning_cursor = (
                                planning_cursor_before is not None
                                and planning_cursor_after is not None
                                and planning_cursor_before[0]
                                == PlanningRunStatus.RUNNING
                                and planning_cursor_after[0]
                                == PlanningRunStatus.RUNNING
                            )
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

            if carried_waiting:
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                notes.append(
                    "Waiting for carried WorkOrders in the current plan: "
                    + ", ".join(work_order.id for work_order in carried_waiting)
                )
                break

            if PlanGraph(plan).is_complete(current.actions):
                if plan.goal_complete:
                    self.runtime.set_run_status(run_id, AgentRunStatus.COMPLETED)
                    notes.append("The Agent confirmed that the run objective is complete")
                    break
                if continue_owned_planning_cursor:
                    if self._continue_owned_planning_cursor_unlocked(run_id, notes):
                        continue
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

    def _planning_cursor_state_for_step(
        self,
        agent_run_id: str,
        step: PlanStep,
    ) -> tuple[PlanningRunStatus, PlanningRunStep] | None:
        """Read the owned child-job cursor without interpreting its content."""

        if step.kind != ActionKind.TOOL_CALL or step.tool_name != "planning.advance":
            return None
        planning_run_id = step.arguments.get("run_id")
        if not isinstance(planning_run_id, str):
            return None
        planning_run = self.projects.get_planning_run(planning_run_id)
        if (
            planning_run is None
            or planning_run.owner_agent_run_id != agent_run_id
        ):
            return None
        return planning_run.status, planning_run.current_step

    @staticmethod
    def _running_content_planning_stage(
        snapshot: AgentRunSnapshot,
        plan: PlanRevision,
    ) -> tuple[ActionRecord, PlanStep] | None:
        """Return the one active Script Stage action, if this plan owns it."""

        steps_by_id = {step.id: step for step in plan.steps}
        matches: list[tuple[ActionRecord, PlanStep]] = []
        for action in snapshot.actions:
            if (
                action.plan_revision_id != plan.id
                or action.status != ActionStatus.RUNNING
                or action.tool_name != "planning.advance"
                or action.plan_step_id is None
            ):
                continue
            step = steps_by_id.get(action.plan_step_id)
            if (
                step is not None
                and step.kind == ActionKind.TOOL_CALL
                and step.tool_name == "planning.advance"
            ):
                matches.append((action, step))
        if len(matches) > 1:
            raise ProductionManagerError(
                "The current plan has multiple running content-planning stages"
            )
        return matches[0] if matches else None

    @staticmethod
    def _planning_step_has_user_input_boundary(
        plan: PlanRevision,
        planning_step_id: str,
    ) -> bool:
        """Whether this bounded planning unit unlocks an explicit user stop."""

        dependencies = {step.id: set(step.depends_on) for step in plan.steps}

        def depends_on_planning(step_id: str, seen: set[str]) -> bool:
            if step_id in seen:
                return False
            seen.add(step_id)
            direct = dependencies.get(step_id, set())
            if planning_step_id in direct:
                return True
            return any(
                depends_on_planning(dependency_id, seen)
                for dependency_id in direct
            )

        return any(
            step.kind == ActionKind.USER_INPUT
            and depends_on_planning(step.id, set())
            for step in plan.steps
        )

    def _continue_owned_planning_cursor_unlocked(
        self,
        run_id: str,
        notes: list[str],
    ) -> bool:
        """Continue one Script Stage unit without another Leader call.

        ``PlanningRunStep`` is the persistent cursor inside the owned content
        planning job.  A step transition is progress inside the same Script
        Stage, not a new production-planning decision.  The Leader regains
        control only after the child job completes or needs attention.
        """

        snapshot = self.runtime.snapshot(run_id)
        document = self._require_project(snapshot.run.project_id)
        proposal = self._planning_precondition_plan(
            agent_run_id=run_id,
            document=document,
            semantic_planning_available=False,
        )
        if proposal is None or not self._is_content_planning_plan(
            proposal,
            agent_run_id=run_id,
            document=document,
        ):
            return False
        self._replace_plan(run_id, proposal)
        self.runtime.set_run_status(run_id, AgentRunStatus.RUNNING)
        self.runtime.checkpoint(
            run_id,
            current_focus=proposal.summary,
            next_actions=[step.title for step in self.runtime.ready_steps(run_id)],
            artifact_refs=[ref for step in proposal.steps for ref in step.artifact_refs],
        )
        notes.append(
            "The owned Script Stage is still running; continue its persisted "
            "content-planning cursor without another Leader planning call"
        )
        return True

    def _return_leader_owned_work_orders(self, run_id: str) -> None:
        """Turn an explicit specialist handoff back into a Leader replan signal."""

        snapshot = self.runtime.snapshot(run_id)
        plan = snapshot.current_plan
        if plan is None:
            return
        actions_by_work_order_id = {
            action.work_order_id: action
            for action in snapshot.actions
            if action.plan_revision_id == plan.id
            and action.work_order_id is not None
            and action.status == ActionStatus.RUNNING
        }
        for work_order in snapshot.pending_work_orders:
            if work_order.next_owner not in {
                WorkOrderNextOwner.LEADER_REPLAN,
                WorkOrderNextOwner.USER_INPUT,
            }:
                continue
            action = actions_by_work_order_id.get(work_order.id)
            if action is None:
                continue
            result = self.runtime.store.get_agent_result(work_order.id)
            summary = (
                result.concise_summary
                if result is not None
                else f"{work_order.recipient_role} returned control to the Leader."
            )
            self.runtime.fail_action(
                action.id,
                error_message=summary,
                output_summary=summary,
                error_kind=(
                    "SpecialistNeedsUserInput"
                    if work_order.next_owner == WorkOrderNextOwner.USER_INPUT
                    else "SpecialistNeedsLeaderReplan"
                ),
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

    def _carried_work_order_state(
        self,
        plan: PlanRevision,
    ) -> tuple[list[WorkOrder], list[WorkOrder], list[str]]:
        waiting: list[WorkOrder] = []
        failed: list[WorkOrder] = []
        invalid_ids: list[str] = []
        for work_order_id in plan.carried_work_order_ids:
            work_order = self.runtime.store.get_work_order(work_order_id)
            if (
                work_order is None
                or work_order.parent_run_id != plan.run_id
                or work_order.active_plan_revision_id != plan.id
            ):
                invalid_ids.append(work_order_id)
                continue
            if work_order.status in {
                DelegationStatus.PENDING,
                DelegationStatus.IN_PROGRESS,
                DelegationStatus.NEEDS_ATTENTION,
            }:
                waiting.append(work_order)
            elif work_order.status in {
                DelegationStatus.FAILED,
                DelegationStatus.CANCELLED,
            }:
                failed.append(work_order)
        return waiting, failed, invalid_ids

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
        try:
            context = self.runtime.work_order_context(work_order_id)
        except ContextManifestResolutionError as exc:
            raise ProductionManagerError(
                f"WorkOrder ContextManifest cannot be resolved: {exc}"
            ) from exc
        prompt = compile_production_prompt(
            document.project.production_profile,
            roles=(role,),
            additional_skill_ids=_narrative_skill_ids_for_role(
                context.active_skill_ids,
                role,
            ),
            method_skill_ids=_method_skill_ids_for_role(
                context.active_skill_ids,
                role,
            ),
        ).text
        return AgentWorkOrderView(
            work_order=work_order,
            context=context,
            system_prompt=prompt,
            progress_entries=self.runtime.work_order_progress(work_order_id),
        )

    def append_work_order_progress(
        self,
        work_order_id: str,
        request: AgentWorkOrderProgressRequest,
    ) -> WorkOrderProgressEntry:
        work_order = self.runtime.store.get_work_order(work_order_id)
        if work_order is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        with self._run_lock(work_order.parent_run_id):
            try:
                return self.runtime.append_work_order_progress(
                    work_order_id,
                    author=request.author,
                    summary=request.summary,
                    artifact_refs=request.artifact_refs,
                )
            except ValueError as exc:
                raise ProductionManagerError(str(exc)) from exc

    def list_work_order_progress(
        self,
        work_order_id: str,
    ) -> AgentWorkOrderProgressListResponse:
        if self.runtime.store.get_work_order(work_order_id) is None:
            raise ProductionManagerError(f"Unknown work order: {work_order_id}")
        return AgentWorkOrderProgressListResponse(
            entries=self.runtime.work_order_progress(work_order_id)
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
        stale_reason = self._work_order_stale_reason(
            work_order,
            run.current_plan_revision_id,
        )
        if stale_reason is not None:
            result = self.runtime.record_agent_result(
                work_order_id,
                status=request.status,
                concise_summary=request.concise_summary,
                deliverable=request.deliverable,
                created_artifacts=request.created_artifacts,
                updated_artifacts=request.updated_artifacts,
                findings=request.findings,
                decisions=request.decisions,
                assumptions=request.assumptions,
                unresolved_questions=request.unresolved_questions,
                evidence_refs=request.evidence_refs,
                recommended_next_actions=request.recommended_next_actions,
                next_owner=request.next_owner,
                stale_handoff=True,
                stale_reason=stale_reason,
            )
            return AgentWorkOrderResultResponse(result=result, run=self.get_run(run.id))
        if work_order.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
        }:
            raise ProductionManagerError(
                f"Work order is already {work_order.status.value}"
            )
        try:
            result = self.runtime.record_agent_result(
                work_order_id,
                status=request.status,
                concise_summary=request.concise_summary,
                deliverable=request.deliverable,
                created_artifacts=request.created_artifacts,
                updated_artifacts=request.updated_artifacts,
                findings=request.findings,
                decisions=request.decisions,
                assumptions=request.assumptions,
                unresolved_questions=request.unresolved_questions,
                evidence_refs=request.evidence_refs,
                recommended_next_actions=request.recommended_next_actions,
                next_owner=request.next_owner,
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
                manifest_skill_ids = (
                    []
                    if work_order.context_manifest is None
                    else work_order.context_manifest.selected_skill_ids
                )
                method_ids = (
                    []
                    if work_order.production_skill_role is None
                    else _method_skill_ids_for_role(
                        manifest_skill_ids,
                        work_order.production_skill_role,
                    )
                )
                for skill_id in method_ids or [None]:
                    self.runtime.distill_experience_from_result(
                        work_order_id,
                        applicability=(
                            f"{work_order.recipient_role}: {work_order.objective}"
                        ),
                        tags=[
                            work_order.recipient_role,
                            document.project.production_profile.value,
                        ],
                        skill_id=skill_id,
                    )
            # Persisting the specialist result is one worker-owned semantic unit.
            # The next unified advance lets the Leader inspect the completed plan,
            # continue, or declare the objective complete without hiding a replan
            # inside this worker batch.
            self.runtime.set_run_status(run.id, AgentRunStatus.RUNNING)
            ready_steps = self.runtime.ready_steps(run.id)
            current_plan = self.runtime.store.get_current_plan(run)
            self.runtime.checkpoint(
                run.id,
                current_focus=current_plan.summary if current_plan is not None else run.objective,
                next_actions=[step.title for step in ready_steps],
                artifact_refs=refs,
            )
        elif request.status == AgentResultStatus.PARTIAL:
            next_owner = request.next_owner or WorkOrderNextOwner.LEADER_REPLAN
            self.runtime.checkpoint(
                run.id,
                current_focus=request.concise_summary,
                open_questions=request.unresolved_questions,
                next_actions=request.recommended_next_actions,
                artifact_refs=refs,
            )
            if next_owner in {
                WorkOrderNextOwner.WORKER_DECISION,
                WorkOrderNextOwner.WORKER_TOOL,
            }:
                self.runtime.set_run_status(run.id, AgentRunStatus.WAITING)
            else:
                if action is not None:
                    self.runtime.fail_action(
                        action.id,
                        error_message=request.concise_summary,
                        output_summary=request.concise_summary,
                        error_kind=(
                            "SpecialistNeedsUserInput"
                            if next_owner == WorkOrderNextOwner.USER_INPUT
                            else "SpecialistNeedsLeaderReplan"
                        ),
                    )
                self.runtime.set_run_status(
                    run.id,
                    AgentRunStatus.WAITING
                    if next_owner == WorkOrderNextOwner.USER_INPUT
                    else AgentRunStatus.RUNNING,
                )
        else:
            # A failed worker result is visible and persisted. Leader replanning is
            # deliberately deferred to the next unified advance.
            self.runtime.set_run_status(run.id, AgentRunStatus.RUNNING)
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
            run = self.runtime.store.get_run(current.parent_run_id)
            if run is None:
                raise ProductionManagerError(
                    f"Unknown agent run: {current.parent_run_id}"
                )
            stale_reason = self._work_order_stale_reason(
                current,
                run.current_plan_revision_id,
            )
            if stale_reason is not None:
                raise ProductionManagerError(stale_reason)
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
        except SpecialistResponseProtocolError as exc:
            self._return_specialist_protocol_failure_to_worker(
                work_order_id,
                exc,
            )
            return self.submit_work_order_result(
                work_order_id,
                AgentWorkOrderResultRequest(
                    status=AgentResultStatus.PARTIAL,
                    concise_summary=(
                        "专业 Agent 的响应结构未通过协议校验，"
                        "已保留同一 WorkOrder 和已完成内容，由原 Worker "
                        "依据类型化反馈修正交接结构。"
                    ),
                    findings=[
                        "这是响应协议问题，不需要改变剧情范围或调度计划。"
                    ],
                    recommended_next_actions=[
                        "让同一专业 Agent 修正已持久化的结构化响应。"
                    ],
                    next_owner=WorkOrderNextOwner.WORKER_DECISION,
                ),
            )
        except Exception as exc:
            self._return_specialist_failure_to_leader(work_order_id, exc)
            if isinstance(exc, ProductionManagerError):
                raise
            raise ProductionManagerError(
                f"Specialist Agent execution failed: {exc}"
            ) from exc
        self._clear_specialist_protocol_feedback(work_order_id)
        if isinstance(decision, SpecialistToolRequest):
            with self._run_lock(work_order.parent_run_id):
                current = self.runtime.store.get_work_order(work_order_id)
                if current is None:
                    raise ProductionManagerError(
                        f"Unknown work order: {work_order_id}"
                    )
                current_run = self.runtime.store.get_run(current.parent_run_id)
                if current_run is None:
                    raise ProductionManagerError(
                        f"Unknown agent run: {current.parent_run_id}"
                    )
                stale_reason = self._work_order_stale_reason(
                    current,
                    current_run.current_plan_revision_id,
                )
                if stale_reason is not None:
                    return self._submit_work_order_result_unlocked(
                        work_order_id,
                        AgentWorkOrderResultRequest(
                            status=AgentResultStatus.PARTIAL,
                            concise_summary=(
                                f"Superseded specialist requested Tool "
                                f"{decision.tool_name}; request was not executed."
                            ),
                            unresolved_questions=[decision.reason],
                            next_owner=WorkOrderNextOwner.LEADER_REPLAN,
                        ),
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
                current.next_owner = WorkOrderNextOwner.WORKER_TOOL
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
                    next_owner=WorkOrderNextOwner.WORKER_TOOL,
                ),
            )
        try:
            return self.submit_work_order_result(work_order_id, decision)
        except Exception as exc:
            self._return_specialist_failure_to_leader(work_order_id, exc)
            if isinstance(exc, ProductionManagerError):
                raise
            raise ProductionManagerError(
                f"Specialist Agent result handoff failed: {exc}"
            ) from exc

    def _clear_specialist_protocol_feedback(self, work_order_id: str) -> None:
        """Clear pending protocol feedback after one valid Worker decision."""

        current = self.runtime.store.get_work_order(work_order_id)
        if current is None or current.pending_protocol_feedback is None:
            return
        with self._run_lock(current.parent_run_id):
            corrected = self.runtime.store.get_work_order(work_order_id)
            if corrected is None or corrected.pending_protocol_feedback is None:
                return
            corrected.pending_protocol_feedback = None
            corrected.updated_at = utc_now()
            self.runtime.store.save_work_order(corrected)
            self.runtime.append_work_order_progress(
                corrected.id,
                author="specialist-response-validator",
                summary=(
                    "The same Worker corrected its response protocol; the WorkOrder "
                    "scope and active plan were preserved."
                ),
            )

    def _return_specialist_protocol_failure_to_worker(
        self,
        work_order_id: str,
        error: SpecialistResponseProtocolError,
    ) -> None:
        """Persist typed response feedback without turning it into a Leader replan."""

        current = self.runtime.store.get_work_order(work_order_id)
        if current is None:
            return
        with self._run_lock(current.parent_run_id):
            failed = self.runtime.store.get_work_order(work_order_id)
            if failed is None or failed.status in {
                DelegationStatus.COMPLETED,
                DelegationStatus.FAILED,
                DelegationStatus.CANCELLED,
            }:
                return
            failed.pending_protocol_feedback = error.feedback
            failed.status = DelegationStatus.NEEDS_ATTENTION
            failed.next_owner = WorkOrderNextOwner.WORKER_DECISION
            failed.updated_at = utc_now()
            self.runtime.store.save_work_order(failed)
            self.runtime.append_work_order_progress(
                failed.id,
                author="specialist-response-validator",
                summary=(
                    "The Worker response did not match a supported JSON contract. "
                    "Typed validation feedback was persisted for correction by the "
                    "same Worker; no creative scope or plan revision changed."
                ),
            )
            self.runtime.set_run_status(
                failed.parent_run_id,
                AgentRunStatus.WAITING,
            )
            self.runtime.checkpoint(
                failed.parent_run_id,
                current_focus=(
                    "The same specialist must correct its persisted response "
                    "protocol feedback."
                ),
                blockers=[],
                next_actions=[
                    "Let the same specialist correct the structured response."
                ],
            )

    def _return_specialist_failure_to_leader(
        self,
        work_order_id: str,
        error: Exception,
    ) -> None:
        """Persist a failed Worker decision as one semantic Leader handoff."""

        current = self.runtime.store.get_work_order(work_order_id)
        if current is None:
            return
        with self._run_lock(current.parent_run_id):
            failed = self.runtime.store.get_work_order(work_order_id)
            if failed is None or failed.status in {
                DelegationStatus.COMPLETED,
                DelegationStatus.FAILED,
                DelegationStatus.CANCELLED,
            }:
                return
            failed.status = DelegationStatus.NEEDS_ATTENTION
            failed.next_owner = WorkOrderNextOwner.LEADER_REPLAN
            failed.updated_at = utc_now()
            self.runtime.store.save_work_order(failed)
            self.runtime.append_work_order_progress(
                failed.id,
                author="production-manager",
                summary=(
                    "Specialist decision failed and returned to Leader: "
                    + str(error)
                ),
            )
            self.runtime.set_run_status(
                failed.parent_run_id,
                AgentRunStatus.WAITING,
            )
            self.runtime.checkpoint(
                failed.parent_run_id,
                current_focus=str(error),
                blockers=[str(error)],
                next_actions=["Let the Leader revise the active plan."],
            )

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
            current_run = self.runtime.store.get_run(current.parent_run_id)
            if current_run is None:
                raise ProductionManagerError(
                    f"Unknown agent run: {current.parent_run_id}"
                )
            stale_reason = self._work_order_stale_reason(
                current,
                current_run.current_plan_revision_id,
            )
            if stale_reason is not None:
                raise ProductionManagerError(stale_reason)
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
                    if current.status != DelegationStatus.CANCELLED:
                        current.status = DelegationStatus.NEEDS_ATTENTION
                        current.next_owner = WorkOrderNextOwner.WORKER_DECISION
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
            if current.status != DelegationStatus.CANCELLED:
                current.status = DelegationStatus.NEEDS_ATTENTION
                current.next_owner = WorkOrderNextOwner.WORKER_DECISION
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
                next_owner=WorkOrderNextOwner.WORKER_DECISION,
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

            carried_waiting, carried_failed, invalid_carried_ids = (
                self._carried_work_order_state(plan)
            )
            if carried_failed or invalid_carried_ids:
                details = [
                    f"{work_order.id}={work_order.status.value}"
                    for work_order in carried_failed
                ]
                details.extend(
                    f"{work_order_id}=missing-or-detached"
                    for work_order_id in invalid_carried_ids
                )
                failure_reason = (
                    reason
                    + " Carried WorkOrder details: "
                    + "; ".join(details)
                )
                if not self._automatic_replan_unlocked(
                    run_id,
                    failure_reason,
                    notes,
                ):
                    return AgentRunStatus.WAITING
                continue

            if carried_waiting:
                status = AgentRunStatus.WAITING
                self.runtime.set_run_status(run_id, status)
                return status

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
        known_method_ids = {
            descriptor.id for descriptor in production_method_skill_catalog()
        }
        unknown_method_ids = [
            skill_id
            for skill_id in proposal.selected_method_skill_ids
            if skill_id not in known_method_ids
        ]
        if unknown_method_ids:
            raise ProductionManagerError(
                "Agent plan selected unknown Production Method Skills: "
                + ", ".join(unknown_method_ids)
            )
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
        previous_plan_id = run.current_plan_revision_id
        actions = self.runtime.store.list_actions(run_id)
        action_by_work_order_id = {
            action.work_order_id: action
            for action in actions
            if action.work_order_id is not None
        }
        unfinished = [
            work_order
            for work_order in self.runtime.store.list_work_orders(run_id)
            if work_order.status
            in {
                DelegationStatus.PENDING,
                DelegationStatus.IN_PROGRESS,
                DelegationStatus.NEEDS_ATTENTION,
            }
            and (
                work_order.active_plan_revision_id == previous_plan_id
                or (
                    work_order.source_plan_revision_id is None
                    and action_by_work_order_id.get(work_order.id) is not None
                    and action_by_work_order_id[work_order.id].plan_revision_id
                    == previous_plan_id
                )
            )
        ]
        unfinished_ids = {work_order.id for work_order in unfinished}
        unknown_carried = [
            work_order_id
            for work_order_id in proposal.carried_work_order_ids
            if work_order_id not in unfinished_ids
        ]
        if unknown_carried:
            raise ProductionManagerError(
                "Plan can only carry unfinished WorkOrders from the active revision: "
                + ", ".join(unknown_carried)
            )
        next_plan = self.runtime.replace_plan(
            run_id,
            summary=proposal.summary,
            steps=proposal.steps,
            selected_method_skill_ids=proposal.selected_method_skill_ids,
            method_selection_rationale=proposal.method_selection_rationale,
            carried_work_order_ids=proposal.carried_work_order_ids,
            goal_complete=proposal.goal_complete,
        )
        carried_ids = set(proposal.carried_work_order_ids)
        for work_order in unfinished:
            if work_order.id in carried_ids:
                work_order.active_plan_revision_id = next_plan.id
                work_order.updated_at = utc_now()
                self.runtime.store.save_work_order(work_order)
                self.runtime.append_work_order_progress(
                    work_order.id,
                    author=run.root_agent,
                    summary=(
                        f"Leader carried this WorkOrder into plan revision "
                        f"{next_plan.revision}."
                    ),
                )
                continue
            work_order.status = DelegationStatus.CANCELLED
            work_order.superseded_by_plan_revision_id = next_plan.id
            work_order.updated_at = utc_now()
            self.runtime.store.save_work_order(work_order)
            self.runtime.append_work_order_progress(
                work_order.id,
                author=run.root_agent,
                summary=(
                    f"Leader superseded this WorkOrder with plan revision "
                    f"{next_plan.revision}; later output remains a historical handoff."
                ),
            )
            action = action_by_work_order_id.get(work_order.id)
            if action is not None and action.status == ActionStatus.RUNNING:
                self.runtime.finish_action(
                    action.id,
                    status=ActionStatus.CANCELLED,
                    output_summary=(
                        f"Superseded by plan revision {next_plan.revision}."
                    ),
                )
        narrative_ids = _selected_narrative_skill_ids(run.active_skill_ids)
        method_ids = set(_selected_method_skill_ids(run.active_skill_ids))
        narrative_id_set = set(narrative_ids)
        base_skill_ids = [
            skill_id
            for skill_id in run.active_skill_ids
            if skill_id not in method_ids and skill_id not in narrative_id_set
        ]
        self.runtime.set_run_active_skills(
            run_id,
            [
                *base_skill_ids,
                *proposal.selected_method_skill_ids,
                *narrative_ids,
            ],
        )

    def _execute_step(
        self,
        run_id: str,
        plan_revision_id: str,
        step: PlanStep,
        *,
        existing_action: ActionRecord | None = None,
        persist_planning_stage: bool = False,
    ) -> tuple[ActionRecord, WorkOrder | None, str | None]:
        action = existing_action or self.runtime.begin_action(
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
            if step.tool_name == "planning.advance":
                planning_run_id = step.arguments.get("run_id")
                planning_run = (
                    self.projects.get_planning_run(planning_run_id)
                    if isinstance(planning_run_id, str)
                    else None
                )
                if (
                    persist_planning_stage
                    and planning_run is not None
                    and planning_run.owner_agent_run_id == run_id
                    and planning_run.status == PlanningRunStatus.RUNNING
                ):
                    action.output_summary = summary or "planning.advance progressed"
                    action.artifact_refs = list(result.artifact_refs)
                    action.updated_at = utc_now()
                    self.runtime.store.save_action(action)
                    document = self.projects.get_project(run.project_id)
                    if document is not None:
                        self._synchronize_planning_narrative_context(
                            agent_run_id=run.id,
                            document=document,
                            planning_run_id=planning_run.id,
                        )
                        self.refresh_project_memory(document)
                    return (
                        action,
                        None,
                        "The Script Stage persisted one bounded progress unit",
                    )
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
            try:
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
                    inherited_decision_ids=_string_list_argument(
                        arguments, "inherited_decision_ids"
                    ),
                    adjacent_boundaries=_context_boundaries_argument(arguments),
                    experience_tags=_string_list_argument(
                        arguments, "experience_tags"
                    ),
                    open_questions=_string_list_argument(arguments, "open_questions"),
                    expected_output_schema=_dict_argument(
                        arguments, "expected_output_schema"
                    ),
                    expected_output_slot_ids=_string_list_argument(
                        arguments, "expected_output_slot_ids"
                    ),
                    acceptance_criteria=_string_list_argument(
                        arguments, "acceptance_criteria"
                    ),
                    source_plan_revision_id=plan_revision_id,
                    source_action_id=action.id,
                )
            except (ProductionManagerError, ValueError) as exc:
                return (
                    self.runtime.fail_action(
                        action.id,
                        error_message=str(exc),
                        error_kind="InvalidContextManifest",
                    ),
                    None,
                    "The delegation ContextManifest is invalid; the Agent can revise it",
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
        """Expose Agent-selected story direction and methods to the owning AgentRun."""

        planning = self.projects.get_planning_run(planning_run_id)
        intent = None if planning is None else planning.story_intent
        if planning is None or intent is None:
            return

        agent_run = self.runtime.store.get_run(agent_run_id)
        if agent_run is None:
            raise ProductionManagerError(f"Unknown agent run: {agent_run_id}")

        self.runtime.set_run_active_skills(
            agent_run_id,
            [
                production_skill_id(document.project.production_profile),
                *planning.selected_method_skill_ids,
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
        if planning.method_selection_rationale:
            decisions.append(
                (
                    "本轮制片方法选择判断：" + planning.method_selection_rationale,
                    (
                        f"内容规划任务 {planning.id} 基于完整项目语义形成；"
                        "制片方法是可调整的 Agent 能力，不是关键词路由或固定流程。"
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
        for index, skill_id in enumerate(planning.selected_method_skill_ids):
            normalized_skill_id = skill_id.strip()
            if not normalized_skill_id:
                continue
            reason = (
                planning.method_selection_reasons[index].strip()
                if index < len(planning.method_selection_reasons)
                else ""
            )
            selection_statement = f"当前项目选用制片方法 {normalized_skill_id}"
            if reason:
                selection_statement += f"；选择理由：{reason}"
            else:
                selection_statement += "；内容规划记录未附单独理由"
            decisions.append(
                (
                    selection_statement,
                    (
                        f"内容规划任务 {planning.id} 根据当前项目语义"
                        "自主选择，后续 Production Manager Agent 或用户可以调整。"
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

        if tool_name == "planning.start_script_revision":
            document = self.projects.get_project(project_id)
            requested_owner_agent_run_id = arguments.get("owner_agent_run_id")
            base_script_version_id = arguments.get("base_script_version_id")
            instruction = arguments.get("instruction")
            if document is None:
                raise ProductionManagerError(
                    "planning.start_script_revision requires an existing project"
                )
            if requested_owner_agent_run_id != agent_run_id:
                raise ProductionManagerError(
                    "planning.start_script_revision owner_agent_run_id must match "
                    "the current Agent run"
                )
            if base_script_version_id != document.project.current_script_version_id:
                raise ProductionManagerError(
                    "planning.start_script_revision must target the current ScriptVersion"
                )
            if not isinstance(instruction, str) or not instruction.strip():
                raise ProductionManagerError(
                    "planning.start_script_revision requires the complete user feedback"
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
        descriptor = self.tools.describe(tool_name)
        if descriptor.effect == ToolEffect.READ:
            result = self.tools.invoke(tool_name, arguments)
        else:
            # A desktop project has one mutable content truth. Read-only Tools may
            # run concurrently, while writes and external side effects share one
            # short project lane whether invoked by the leader or a worker.
            with self._project_tool_lock(project_id):
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
            retained_running_planning_stage = False
            recovery_notes: list[str] = []
            work_orders = self.runtime.store.list_work_orders(run.id)
            for work_order in work_orders:
                if work_order.status != DelegationStatus.IN_PROGRESS:
                    continue
                for turn in work_order.specialist_turns:
                    if turn.status != SpecialistTurnStatus.IN_PROGRESS:
                        continue
                    turn.status = SpecialistTurnStatus.INTERRUPTED
                    turn.result = _interrupted_tool_result(turn)
                    turn.updated_at = utc_now()
                work_order.status = DelegationStatus.NEEDS_ATTENTION
                work_order.next_owner = WorkOrderNextOwner.WORKER_DECISION
                work_order.updated_at = utc_now()
                self.runtime.store.save_work_order(work_order)
                recovered = True

            work_orders = self.runtime.store.list_work_orders(run.id)
            work_orders_by_id = {work_order.id: work_order for work_order in work_orders}
            work_orders_by_source_action: dict[str, list[WorkOrder]] = {}
            for work_order in work_orders:
                if work_order.source_action_id is None:
                    continue
                work_orders_by_source_action.setdefault(
                    work_order.source_action_id,
                    [],
                ).append(work_order)

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
                        action.tool_name == "planning.start_script_revision"
                        and document is not None
                        and document.planning is not None
                        and document.planning.run_id is not None
                        and document.planning.owner_agent_run_id == run.id
                    ):
                        planning_run = self.projects.get_planning_run(
                            document.planning.run_id
                        )
                        action_plan = next(
                            (
                                plan
                                for plan in self.runtime.store.list_plan_revisions(
                                    run.id
                                )
                                if plan.id == action.plan_revision_id
                            ),
                            None,
                        )
                        action_step = (
                            None
                            if action_plan is None
                            else next(
                                (
                                    step
                                    for step in action_plan.steps
                                    if step.id == action.plan_step_id
                                ),
                                None,
                            )
                        )
                        arguments = (
                            {}
                            if action_step is None
                            or action_step.tool_name
                            != "planning.start_script_revision"
                            else action_step.arguments
                        )
                        instruction = arguments.get("instruction")
                        if (
                            planning_run is not None
                            and planning_run.operation
                            == PlanningRunOperation.SCRIPT_REVISION
                            and planning_run.project_id == run.project_id
                            and planning_run.owner_agent_run_id == run.id
                            and arguments.get("project_id") == run.project_id
                            and arguments.get("owner_agent_run_id") == run.id
                            and arguments.get("base_script_version_id")
                            == planning_run.base_script_version_id
                            and isinstance(instruction, str)
                            and instruction.strip()
                            == planning_run.revision_instruction
                        ):
                            planning_refs = [
                                ArtifactRef(
                                    project_id=run.project_id,
                                    artifact_type="content_planning_job",
                                    artifact_id=planning_run.id,
                                ),
                                ArtifactRef(
                                    project_id=run.project_id,
                                    artifact_type="project",
                                    artifact_id=run.project_id,
                                ),
                            ]
                            self.runtime.complete_action(
                                action.id,
                                output_summary=(
                                    "Reconciled planning.start_script_revision with "
                                    "the persisted screenplay revision job "
                                    f"{planning_run.id} after restart."
                                ),
                                artifact_refs=planning_refs,
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
                            planning_refs = [
                                ArtifactRef(
                                    project_id=run.project_id,
                                    artifact_type="content_planning_job",
                                    artifact_id=planning_run.id,
                                )
                            ]
                            summary = (
                                "Reconciled the Script Stage with persisted cursor "
                                f"{planning_run.current_step.value} "
                                f"({planning_run.status.value}) after restart."
                            )
                            current_plan = self.runtime.store.get_current_plan(run)
                            stage_is_persistent = (
                                current_plan is not None
                                and current_plan.id == action.plan_revision_id
                                and action.plan_step_id is not None
                                and not self._planning_step_has_user_input_boundary(
                                    current_plan,
                                    action.plan_step_id,
                                )
                            )
                            if (
                                planning_run.status == PlanningRunStatus.RUNNING
                                and stage_is_persistent
                            ):
                                action.output_summary = summary
                                action.artifact_refs = planning_refs
                                action.updated_at = utc_now()
                                self.runtime.store.save_action(action)
                                retained_running_planning_stage = True
                            else:
                                self.runtime.complete_action(
                                    action.id,
                                    output_summary=summary,
                                    artifact_refs=planning_refs,
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
                elif action.kind == ActionKind.DELEGATION:
                    recovery_note = self._reconcile_delegation_action_after_restart(
                        action,
                        work_orders_by_id=work_orders_by_id,
                        work_orders_by_source_action=work_orders_by_source_action,
                    )
                    if recovery_note is not None:
                        recovery_notes.append(recovery_note)
                    recovered = True
                elif action.kind == ActionKind.CHECKPOINT:
                    self.runtime.fail_action(
                        action.id,
                        error_message="Checkpoint execution was interrupted before completion.",
                    )
                    recovered = True

            if recovered:
                if retained_running_planning_stage:
                    self.runtime.set_run_status(run.id, AgentRunStatus.RUNNING)
                    self.runtime.checkpoint(
                        run.id,
                        current_focus=(
                            "Resume the persisted Script Stage from its internal cursor."
                        ),
                        next_actions=["Continue the Script Stage"],
                    )
                    continue
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
                recovered_snapshot = self.runtime.snapshot(run.id)
                recovered_plan = recovered_snapshot.current_plan
                carried_waiting: list[WorkOrder] = []
                carried_failed: list[WorkOrder] = []
                invalid_carried_ids: list[str] = []
                if recovered_plan is not None:
                    carried_waiting, carried_failed, invalid_carried_ids = (
                        self._carried_work_order_state(recovered_plan)
                    )
                recovered_complete = (
                    recovered_plan is not None
                    and recovered_plan.goal_complete
                    and not carried_waiting
                    and not carried_failed
                    and not invalid_carried_ids
                    and not self._failed_actions_for_plan(
                        recovered_snapshot,
                        recovered_plan.id,
                    )
                    and PlanGraph(recovered_plan).is_complete(
                        recovered_snapshot.actions
                    )
                )
                recovered_status = (
                    AgentRunStatus.COMPLETED
                    if recovered_complete
                    else AgentRunStatus.WAITING
                )
                self.runtime.set_run_status(run.id, recovered_status)
                self.runtime.checkpoint(
                    run.id,
                    current_focus="Resume interrupted Agent work from persisted state.",
                    blockers=(
                        []
                        if recovered_complete
                        else recovery_notes
                        or [
                            "An in-flight operation was interrupted and needs "
                            "reconciliation or retry."
                        ]
                    ),
                    next_actions=(
                        []
                        if recovered_complete
                        else [
                            "Inspect the current plan, domain tasks, and WorkOrders "
                            "before continuing."
                        ]
                    ),
                )

    def _reconcile_delegation_action_after_restart(
        self,
        action: ActionRecord,
        *,
        work_orders_by_id: Mapping[str, WorkOrder],
        work_orders_by_source_action: Mapping[str, list[WorkOrder]],
    ) -> str | None:
        """Reconcile one interrupted delegation without replaying its specialist."""

        source_matches = work_orders_by_source_action.get(action.id, [])
        linked = (
            None
            if action.work_order_id is None
            else work_orders_by_id.get(action.work_order_id)
        )
        if linked is None:
            if len(source_matches) == 1:
                linked = source_matches[0]
                action.work_order_id = linked.id
                action.updated_at = utc_now()
                self.runtime.store.save_action(action)
                self.runtime.append_work_order_progress(
                    linked.id,
                    author="production-manager",
                    summary=(
                        "Recovered the persisted WorkOrder linkage after restart; "
                        "the specialist was not replayed."
                    ),
                )
            elif not source_matches:
                self.runtime.fail_action(
                    action.id,
                    error_message=(
                        "Delegation was interrupted before its WorkOrder was persisted; "
                        "revise the plan instead of replaying the specialist implicitly."
                    ),
                    error_kind="MissingDelegationWorkOrder",
                )
                return "A delegation action had no persisted WorkOrder and needs replanning."
            else:
                self.runtime.fail_action(
                    action.id,
                    error_message=(
                        "Delegation recovery found multiple WorkOrders for one action; "
                        "manual reconciliation is required."
                    ),
                    error_kind="AmbiguousDelegationWorkOrder",
                )
                for work_order in source_matches:
                    if work_order.status in {
                        DelegationStatus.PENDING,
                        DelegationStatus.IN_PROGRESS,
                    }:
                        work_order.status = DelegationStatus.NEEDS_ATTENTION
                        work_order.next_owner = WorkOrderNextOwner.LEADER_REPLAN
                        work_order.updated_at = utc_now()
                        self.runtime.store.save_work_order(work_order)
                return "A delegation action has ambiguous persisted WorkOrders."

        if linked.parent_run_id != action.run_id:
            self.runtime.fail_action(
                action.id,
                error_message=(
                    "Delegation action points to a WorkOrder from another Agent run; "
                    "manual reconciliation is required."
                ),
                error_kind="CrossRunDelegationWorkOrder",
            )
            return "A delegation action points outside its Agent run."
        if linked.source_action_id is None:
            linked.source_action_id = action.id
            linked.updated_at = utc_now()
            self.runtime.store.save_work_order(linked)
        elif linked.source_action_id != action.id:
            self.runtime.fail_action(
                action.id,
                error_message=(
                    "Delegation action and WorkOrder disagree about their persisted "
                    "linkage; manual reconciliation is required."
                ),
                error_kind="MismatchedDelegationWorkOrder",
            )
            if linked.status in {
                DelegationStatus.PENDING,
                DelegationStatus.IN_PROGRESS,
            }:
                linked.status = DelegationStatus.NEEDS_ATTENTION
                linked.next_owner = WorkOrderNextOwner.LEADER_REPLAN
                linked.updated_at = utc_now()
                self.runtime.store.save_work_order(linked)
            return "A delegation action and WorkOrder have mismatched linkage."

        result = self.runtime.store.get_agent_result(linked.id)
        if result is not None:
            refs = [*result.created_artifacts, *result.updated_artifacts]
            if result.stale_handoff:
                self.runtime.finish_action(
                    action.id,
                    status=ActionStatus.CANCELLED,
                    output_summary=(
                        "Recovered a stale historical handoff without merging it into "
                        "the active plan."
                    ),
                )
                return None
            if result.status == AgentResultStatus.COMPLETED:
                self.runtime.complete_action(
                    action.id,
                    output_summary=result.concise_summary,
                    artifact_refs=refs,
                )
                return None
            if result.status == AgentResultStatus.FAILED:
                self.runtime.fail_action(
                    action.id,
                    error_message=result.concise_summary,
                    output_summary=result.concise_summary,
                    error_kind="RecoveredAgentResultFailure",
                )
                return "A persisted delegated result failed and needs replanning."
            if linked.status != DelegationStatus.NEEDS_ATTENTION:
                linked.status = DelegationStatus.NEEDS_ATTENTION
            linked.next_owner = result.next_owner
            linked.updated_at = utc_now()
            self.runtime.store.save_work_order(linked)
            if result.next_owner in {
                WorkOrderNextOwner.LEADER_REPLAN,
                WorkOrderNextOwner.USER_INPUT,
            }:
                self.runtime.fail_action(
                    action.id,
                    error_message=result.concise_summary,
                    output_summary=result.concise_summary,
                    error_kind=(
                        "RecoveredSpecialistNeedsUserInput"
                        if result.next_owner == WorkOrderNextOwner.USER_INPUT
                        else "RecoveredSpecialistNeedsLeaderReplan"
                    ),
                )
                return "A partial delegated result returned control to the Leader."
            return "A partial delegated result is persisted and ready for the specialist."

        if linked.status == DelegationStatus.COMPLETED:
            self.runtime.fail_action(
                action.id,
                error_message=(
                    "WorkOrder was marked completed without a persisted AgentResult; "
                    "manual reconciliation is required."
                ),
                error_kind="MissingDelegationResult",
            )
            return "A completed WorkOrder is missing its persisted AgentResult."
        if linked.status == DelegationStatus.FAILED:
            self.runtime.fail_action(
                action.id,
                error_message="The persisted WorkOrder failed before an AgentResult was saved.",
                error_kind="MissingDelegationResult",
            )
            return "A failed WorkOrder has no persisted AgentResult."
        if linked.status == DelegationStatus.CANCELLED:
            self.runtime.finish_action(
                action.id,
                status=ActionStatus.CANCELLED,
                output_summary="The persisted WorkOrder was cancelled before restart.",
            )
            return None
        return "A persisted WorkOrder is waiting; its specialist was not replayed."

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(run_id, threading.Lock())

    @staticmethod
    def _work_order_stale_reason(
        work_order: WorkOrder,
        current_plan_revision_id: str | None,
    ) -> str | None:
        if work_order.source_plan_revision_id is None:
            return None
        if (
            work_order.status == DelegationStatus.CANCELLED
            or work_order.active_plan_revision_id != current_plan_revision_id
        ):
            return (
                "This WorkOrder belongs to a superseded plan revision. Its result was "
                "saved as a historical handoff and was not merged into the active plan."
            )
        return None

    def _project_tool_lock(self, project_id: str) -> threading.Lock:
        with self._project_tool_locks_guard:
            return self._project_tool_locks.setdefault(project_id, threading.Lock())


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


def _context_boundaries_argument(
    arguments: dict[str, JsonValue],
) -> list[ContextBoundary]:
    value = arguments.get("adjacent_boundaries")
    if not isinstance(value, list):
        return []
    boundaries: list[ContextBoundary] = []
    for item in value:
        if not isinstance(item, dict):
            raise ProductionManagerError(
                "DELEGATION adjacent_boundaries entries must be objects"
            )
        try:
            boundaries.append(ContextBoundary.model_validate(item))
        except ValueError as exc:
            raise ProductionManagerError(
                "DELEGATION contains an invalid adjacent context boundary"
            ) from exc
    return boundaries


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
    "AgentWorkOrderProgressListResponse",
    "AgentWorkOrderProgressRequest",
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
