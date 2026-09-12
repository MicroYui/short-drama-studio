"""Agent-first production coordination over shared Skills and Tools.

The Production Manager is deliberately a thin harness.  Agents own the plan and
may replace it with a different dependency graph at any time.  This module only
provides bounded execution, persistence, focused context, Tool invocation, and
structured delegation; it does not encode a fixed video-production workflow.
"""

from __future__ import annotations

from short_drama.skill_ids import SkillIds, canonical_skill_ids

from short_drama.prompts import runtime_prompt

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
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
    AgentRun,
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
    RunMemory,
    SpecialistProtocolFeedback,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    ToolAttemptRef,
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
from short_drama.agent_runtime.completion import verify_completion_claims
from short_drama.agent_runtime.media_context import provider_media_model_view
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_boundary, execution_control
from short_drama.agent_runtime.model_input import ModelInputCompiler, generate_context_managed_text, json_text, pointer_value
from short_drama.agent_runtime.response_normalization import normalize_specialist_tool_payload
from short_drama.agent_runtime.asset_plan_context import asset_work_read_is_stale, fold_asset_plan_items
from short_drama.asset_plan_receipts import compatible_asset_plan_receipt
from short_drama.agent_runtime.leader_context import (
    BROAD_COMPLETION_REF_TYPES, completed_work_handoffs, exact_context_values, leader_read_working_set, persist_leader_tool_result,
)
from short_drama.agent_runtime.tool_context import initial_tool_schema_ids, normalized_read_arguments, project_retained_read_results, schema_request_feedback
from short_drama.studio_trace import record_trace, trace_context
from short_drama.image_models import ImageGenerationTask
from short_drama.models import (
    ContentPlanningOutput,
    DurationAlignment,
    DurationAuthority,
    DurationFitAssessment,
    ProductionExecutionTarget,
    CreateProjectRequest,
    DomainModel,
    GenerationTask,
    PlanningRunRecord,
    PlanningRunOperation,
    PlanningRunStep,
    PlanningRunStatus,
    ProductionDocumentArtifactRef,
    ProductionProfile,
    ProjectDocument,
    ProjectStage,
    RevisionJudgeRecommendation,
    RunMode,
    SceneTimingAssessmentRecord,
    ScriptRevisionOrigin,
    ScriptReviewDisposition,
    ScriptReviewReport,
    StoryIntent,
    StoryIssueLedgerEntry,
    StoryMetadataRevisionPatch,
    StoryRepairPlan,
    StoryRevisionVerification,
    StorySourceContract,
    StoryVerificationResolution,
    StoryVerificationResolutionDecision,
    Take,
)
from short_drama.production_profiles import (
    NarrativeSkillDescriptor,
    ProductionMethodSkillDescriptor,
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
    narrative_skill_catalog,
    production_method_skill_catalog,
    production_skill_catalog,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderTransportError,
    is_language_transport_error,
)
from short_drama.skills import SkillProvenanceKind
from short_drama.tools import (
    ToolCallStatus,
    ToolDescriptor,
    ToolEffect,
    ToolExecution,
    ToolRegistry,
    ToolResult,
)
from short_drama.tools.agent import (
    AgentGetToolAttemptInput,
    AgentGetToolAttemptOutput,
    AgentReadContextInput,
    AgentReadContextOutput,
    ToolAttemptPayload,
    register_context_tools,
    register_tool_attempt_tools,
)
from short_drama.tools.artifact import (
    AssetPlanAttemptPatch,
    AssetPlanAttemptPatchOperation,
    AssetPlanAttemptRetryInput,
    AssetPlanCreateInput,
    AssetPlanReplaceInput,
    register_asset_plan_attempt_tools,
)
from short_drama.production_contracts import AssetPlanMutationSummaryResponse


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


def _decode_json_pointer(path: str) -> list[str]:
    """Decode one non-root RFC 6901 JSON Pointer."""

    if not path.startswith("/"):
        raise ProductionManagerError(
            f"JSON Patch path must start with '/': {path!r}"
        )
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _json_pointer_list_index(
    token: str,
    *,
    length: int,
    allow_append: bool,
) -> int:
    if token == "-" and allow_append:
        return length
    try:
        index = int(token)
    except ValueError as exc:
        raise ProductionManagerError(
            f"JSON Patch list index is not an integer: {token!r}"
        ) from exc
    upper_bound = length if allow_append else length - 1
    if index < 0 or index > upper_bound:
        raise ProductionManagerError(
            f"JSON Patch list index {index} is outside 0..{upper_bound}"
        )
    return index


def _apply_json_patch_operation(
    target: dict[str, JsonValue],
    patch: AssetPlanAttemptPatch,
) -> None:
    """Apply the supported RFC 6902 subset to one selected mutable object."""

    tokens = _decode_json_pointer(patch.path)
    parent: dict[str, JsonValue] | list[JsonValue] = target
    for token in tokens[:-1]:
        if isinstance(parent, dict):
            child = parent.get(token)
        else:
            index = _json_pointer_list_index(
                token,
                length=len(parent),
                allow_append=False,
            )
            child = parent[index]
        if not isinstance(child, (dict, list)):
            raise ProductionManagerError(
                f"JSON Patch path does not resolve to a container: {patch.path!r}"
            )
        parent = child

    token = tokens[-1]
    if isinstance(parent, dict):
        exists = token in parent
        current = parent.get(token)
        if patch.op == AssetPlanAttemptPatchOperation.TEST:
            if not exists or current != patch.value:
                raise ProductionManagerError(
                    f"JSON Patch test failed at {patch.path!r}"
                )
            return
        if patch.op == AssetPlanAttemptPatchOperation.REMOVE:
            if not exists:
                raise ProductionManagerError(
                    f"JSON Patch remove target does not exist: {patch.path!r}"
                )
            del parent[token]
            return
        if patch.op == AssetPlanAttemptPatchOperation.REPLACE and not exists:
            raise ProductionManagerError(
                f"JSON Patch replace target does not exist: {patch.path!r}"
            )
        parent[token] = deepcopy(patch.value)
        return

    index = _json_pointer_list_index(
        token,
        length=len(parent),
        allow_append=patch.op == AssetPlanAttemptPatchOperation.ADD,
    )
    if patch.op == AssetPlanAttemptPatchOperation.TEST:
        if parent[index] != patch.value:
            raise ProductionManagerError(
                f"JSON Patch test failed at {patch.path!r}"
            )
    elif patch.op == AssetPlanAttemptPatchOperation.REMOVE:
        parent.pop(index)
    elif patch.op == AssetPlanAttemptPatchOperation.REPLACE:
        parent[index] = deepcopy(patch.value)
    else:
        parent.insert(index, deepcopy(patch.value))


def _asset_plan_attempt_patch_target(
    arguments: dict[str, JsonValue],
    patch: AssetPlanAttemptPatch,
) -> dict[str, JsonValue]:
    """Resolve a stable AssetPlan item/work-item selector inside Tool arguments."""

    request = arguments.get("request")
    if not isinstance(request, dict):
        raise ProductionManagerError("AssetPlan Tool attempt has no request object")
    asset_plan = request.get("asset_plan")
    if not isinstance(asset_plan, dict):
        raise ProductionManagerError("AssetPlan Tool attempt has no asset_plan object")
    if patch.item_id is None and patch.work_item_id is None:
        return asset_plan
    items = asset_plan.get("items")
    if not isinstance(items, list):
        raise ProductionManagerError("AssetPlan Tool attempt has no items array")
    if patch.item_id is None:
        matching_work_items: list[dict[str, JsonValue]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, JsonValue], raw_item)
            raw_work_items = item.get("work_items")
            if not isinstance(raw_work_items, list):
                continue
            for raw_work_item in raw_work_items:
                if not isinstance(raw_work_item, dict):
                    continue
                work_item = cast(dict[str, JsonValue], raw_work_item)
                if work_item.get("id") == patch.work_item_id:
                    matching_work_items.append(work_item)
        if len(matching_work_items) != 1:
            raise ProductionManagerError(
                "AssetPlan patch work-item selector must resolve exactly once: "
                f"{patch.work_item_id}"
            )
        return matching_work_items[0]
    matching_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("id") == patch.item_id
    ]
    if len(matching_items) != 1:
        raise ProductionManagerError(
            f"AssetPlan patch item selector must resolve exactly once: {patch.item_id}"
        )
    item = matching_items[0]
    if patch.work_item_id is None:
        return item
    work_items = item.get("work_items")
    if not isinstance(work_items, list):
        raise ProductionManagerError(
            f"AssetPlan item {patch.item_id} has no work_items array"
        )
    matching_work_items = [
        work_item
        for work_item in work_items
        if isinstance(work_item, dict)
        and work_item.get("id") == patch.work_item_id
    ]
    if len(matching_work_items) != 1:
        raise ProductionManagerError(
            "AssetPlan patch work-item selector must resolve exactly once: "
            f"{patch.item_id}/{patch.work_item_id}"
        )
    return matching_work_items[0]


def _apply_asset_plan_attempt_patches(
    arguments: Mapping[str, JsonValue],
    patches: Sequence[AssetPlanAttemptPatch],
) -> dict[str, JsonValue]:
    """Apply small stable-ID patches without returning the full candidate to the model."""

    patched = deepcopy(dict(arguments))
    for patch in patches:
        if not patch.path:
            _remove_asset_plan_attempt_entity(patched, patch)
            continue
        target = _asset_plan_attempt_patch_target(patched, patch)
        _apply_json_patch_operation(target, patch)
    return patched


def _remove_asset_plan_attempt_entity(
    arguments: dict[str, JsonValue],
    patch: AssetPlanAttemptPatch,
) -> None:
    """Remove one stable-ID item/work item without list-index patching."""

    request = arguments.get("request")
    if not isinstance(request, dict):
        raise ProductionManagerError("AssetPlan Tool attempt has no request object")
    asset_plan = request.get("asset_plan")
    if not isinstance(asset_plan, dict):
        raise ProductionManagerError("AssetPlan Tool attempt has no asset_plan object")
    items = asset_plan.get("items")
    if not isinstance(items, list):
        raise ProductionManagerError("AssetPlan Tool attempt has no items array")

    if patch.work_item_id is None:
        matching_item_indexes = [
            index
            for index, raw_item in enumerate(items)
            if isinstance(raw_item, dict) and raw_item.get("id") == patch.item_id
        ]
        if len(matching_item_indexes) != 1:
            raise ProductionManagerError(
                "AssetPlan removal item selector must resolve exactly once: "
                f"{patch.item_id}"
            )
        items.pop(matching_item_indexes[0])
        return

    matches: list[tuple[list[JsonValue], int]] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        if patch.item_id is not None and raw_item.get("id") != patch.item_id:
            continue
        work_items = raw_item.get("work_items")
        if not isinstance(work_items, list):
            continue
        for index, raw_work_item in enumerate(work_items):
            if (
                isinstance(raw_work_item, dict)
                and raw_work_item.get("id") == patch.work_item_id
            ):
                matches.append((work_items, index))
    if len(matches) != 1:
        raise ProductionManagerError(
            "AssetPlan removal work-item selector must resolve exactly once: "
            f"{patch.item_id or '*'}/{patch.work_item_id}"
        )
    work_items, index = matches[0]
    work_items.pop(index)


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
    # Appended plans retain earlier steps; their history must not hit the former
    # per-response limit. The language request still has its token/output budget.
    steps: list[PlanStep] = Field(default_factory=list[PlanStep])
    selected_method_skill_ids: SkillIds = Field(default_factory=list[str])
    method_selection_rationale: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
    )
    carried_work_order_ids: list[str] = Field(default_factory=list[str])
    goal_complete: bool = False
    goal_summary: str | None = Field(default=None, max_length=16000,
        description="结合原始目标和用户最新要求维护的当前交付目标与停止条件，不是阶段进度。")

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
        if not self.goal_complete and not self.steps and not self.carried_work_order_ids:
            raise ValueError(
                "An incomplete Agent plan must contain at least one next step or retain pending work"
            )
        return self


class CompletionPlanningInput(DomainModel):
    """One consumed completion event and its read-only planning snapshot."""

    run_id: str
    plan_revision_id: str
    completed_work_order_ids: list[str]
    latest_user_instructions: list[str]
    current_plan: AgentPlanProposal
    context: ContextPack
    profile: ProductionProfile
    execution_target: ProductionExecutionTarget | None
    local_plan_completed: bool = False
    failed_plan_state: dict[str, JsonValue] = Field(default_factory=dict)


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
    objective: str = Field(min_length=1, max_length=16000)
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
    objective: str | None = Field(default=None, min_length=1, max_length=16000)
    root_agent: str = Field(default="production-manager", min_length=1, max_length=120)


class AgentRunReplanRequest(DomainModel):
    reason: str = Field(min_length=1, max_length=4000)
    proposed_plan: AgentPlanProposal | None = None
    confirmed_decisions: list[str] = Field(
        default_factory=list[str],
        description=(
            "明确的用户补充决定；保存为本 Run 的 DecisionMemory，供后续重规划继承。"
            "reason 只解释本次重规划，不会自动转为长期决定。"
        ),
    )


class AgentRunAdvanceRequest(DomainModel):
    max_actions: int = Field(default=1, ge=1, le=8)
    review_blocked_step_ids: list[str] | None = Field(default=None, description="产品当前审阅所影响的步骤；非空值包括空列表都保留审阅完成边界。")


class AgentUserInputRequest(DomainModel):
    action_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, description="完整回答，可合并多条已接收消息及各自范围；由完整请求的上下文预算控制模型输入。")
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


def _specialist_work_order_result_schema() -> dict[str, JsonValue]:
    """Expose only Worker-owned handoff states in the Specialist JSON schema."""

    schema = cast(
        dict[str, JsonValue],
        deepcopy(AgentWorkOrderResultRequest.model_json_schema()),
    )
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ProductionManagerError(
            "AgentWorkOrderResultRequest schema has no properties object"
        )
    properties["next_owner"] = {
        "anyOf": [
            {
                "enum": [
                    WorkOrderNextOwner.LEADER_REPLAN.value,
                    WorkOrderNextOwner.USER_INPUT.value,
                ],
                "type": "string",
            },
            {"type": "null"},
        ],
        "default": None,
        "title": "Next Owner",
    }
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        definitions.pop("WorkOrderNextOwner", None)
    return schema


def _validate_specialist_result_ownership(
    result: AgentWorkOrderResultRequest,
) -> AgentWorkOrderResultRequest:
    """Validate Worker handoff semantics before committing the runtime result."""

    if result.status == AgentResultStatus.COMPLETED and result.unresolved_questions:
        raise ValueError(
            "A COMPLETED AgentResult cannot contain unresolved_questions. "
            "If the assigned observation is complete, preserve factual UNKNOWN "
            "limitations in findings or deliverable; if required work remains, "
            "return PARTIAL with its next owner. Do not erase unknown evidence."
        )

    if result.status == AgentResultStatus.PARTIAL and result.next_owner in {
        WorkOrderNextOwner.WORKER_DECISION,
        WorkOrderNextOwner.WORKER_TOOL,
    }:
        raise ValueError(
            "Specialist PARTIAL results may only hand off to LEADER_REPLAN or "
            "USER_INPUT; WORKER_DECISION and WORKER_TOOL are Manager-owned states"
        )
    return result


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
    selected_method_skill_ids: SkillIds = Field(default_factory=list[str])
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
    story_verification_resolutions: list[StoryVerificationResolution] = Field(
        default_factory=list[StoryVerificationResolution]
    )
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


class AgentWorkOrderSummary(DomainModel):
    """Public pending-WorkOrder state without its append-only execution trace."""

    id: str
    parent_run_id: str
    source_plan_revision_id: str | None = None
    source_action_id: str | None = None
    active_plan_revision_id: str | None = None
    superseded_by_plan_revision_id: str | None = None
    sender: str
    recipient_role: str
    production_skill_role: ProductionSkillRole | None = None
    production_prompt_stage: ProductionPromptStage | None = None
    objective: str
    why_now: str
    scope: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    constraints: list[str] = Field(default_factory=list[str])
    confirmed_decisions: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    expected_output_slot_ids: list[str] = Field(default_factory=list[str])
    acceptance_criteria: list[str] = Field(default_factory=list[str])
    context_manifest_id: str | None = None
    status: DelegationStatus
    next_owner: WorkOrderNextOwner | None = None
    specialist_turn_count: int = Field(ge=0)
    latest_specialist_tool_name: str | None = None
    latest_specialist_turn_status: SpecialistTurnStatus | None = None
    pending_protocol_feedback_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunPublicSnapshot(DomainModel):
    """API projection of AgentRunSnapshot used by normal polling responses."""

    run: AgentRun
    current_plan: PlanRevision | None
    actions: list[ActionRecord] = Field(default_factory=list[ActionRecord])
    memory: RunMemory
    pending_work_orders: list[AgentWorkOrderSummary] = Field(
        default_factory=list[AgentWorkOrderSummary]
    )


class AgentRunView(DomainModel):
    snapshot: AgentRunPublicSnapshot
    ready_steps: list[PlanStep]
    context: ContextPack
    content_planning_trace: ContentPlanningTraceView | None = None


def _public_work_order_summary(work_order: WorkOrder) -> AgentWorkOrderSummary:
    latest_turn = max(
        work_order.specialist_turns,
        key=lambda item: item.sequence,
        default=None,
    )
    return AgentWorkOrderSummary(
        id=work_order.id,
        parent_run_id=work_order.parent_run_id,
        source_plan_revision_id=work_order.source_plan_revision_id,
        source_action_id=work_order.source_action_id,
        active_plan_revision_id=work_order.active_plan_revision_id,
        superseded_by_plan_revision_id=work_order.superseded_by_plan_revision_id,
        sender=work_order.sender,
        recipient_role=work_order.recipient_role,
        production_skill_role=work_order.production_skill_role,
        production_prompt_stage=work_order.production_prompt_stage,
        objective=work_order.objective,
        why_now=work_order.why_now,
        scope=list(work_order.scope),
        constraints=list(work_order.constraints),
        confirmed_decisions=list(work_order.confirmed_decisions),
        open_questions=list(work_order.open_questions),
        expected_output_slot_ids=list(work_order.expected_output_slot_ids),
        acceptance_criteria=list(work_order.acceptance_criteria),
        context_manifest_id=(
            None
            if work_order.context_manifest is None
            else work_order.context_manifest.id
        ),
        status=work_order.status,
        next_owner=work_order.next_owner,
        specialist_turn_count=len(work_order.specialist_turns),
        latest_specialist_tool_name=(
            None if latest_turn is None else latest_turn.tool_name
        ),
        latest_specialist_turn_status=(
            None if latest_turn is None else latest_turn.status
        ),
        pending_protocol_feedback_id=(
            None
            if work_order.pending_protocol_feedback is None
            else work_order.pending_protocol_feedback.id
        ),
        created_at=work_order.created_at,
        updated_at=work_order.updated_at,
    )


def _public_agent_run_snapshot(snapshot: AgentRunSnapshot) -> AgentRunPublicSnapshot:
    return AgentRunPublicSnapshot(
        run=snapshot.run,
        current_plan=snapshot.current_plan,
        actions=list(snapshot.actions),
        memory=snapshot.memory,
        pending_work_orders=[
            _public_work_order_summary(work_order)
            for work_order in snapshot.pending_work_orders
        ],
    )


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
        story_verification_resolutions=[
            resolution.model_copy(deep=True)
            for resolution in run.story_verification_resolutions
        ],
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
    ProductionSkillRole.ASSET_PLANNER,
    ProductionSkillRole.ASSET_BUILDER,
    ProductionSkillRole.ASSET_VERIFIER,
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
        for skill_id in dict.fromkeys(canonical_skill_ids(skill_ids))
        if (descriptor := catalog.get(skill_id)) is not None
        and role in descriptor.supported_roles
    ]


def _selected_method_skill_ids(skill_ids: Sequence[str]) -> list[str]:
    known_ids = {descriptor.id for descriptor in production_method_skill_catalog()}
    return [
        skill_id for skill_id in dict.fromkeys(canonical_skill_ids(skill_ids))
        if skill_id in known_ids
    ]


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
    """Keep compatibility aligned with the current typed Worker Tool envelope."""

    return normalize_specialist_tool_payload(
        _json_block(text), tool_request_fields=SpecialistToolRequest.model_fields
    )


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


def _specialist_protocol_feedback_signature(
    feedback: SpecialistProtocolFeedback,
) -> str:
    """Identify whether a Worker made semantic protocol progress.

    Feedback IDs, timestamps, parser column numbers, and trailing invalid
    serialization are excluded.  A leading JSON value is canonicalized only
    for comparison; the malformed response remains rejected and is never run.
    """

    rejected = _json_block(feedback.rejected_response).strip()
    semantic_response: JsonValue
    try:
        decoded, _end = json.JSONDecoder().raw_decode(rejected)
    except json.JSONDecodeError:
        semantic_response = rejected
    else:
        semantic_response = cast(JsonValue, decoded)

    issue_projection: dict[str, JsonValue] = {}
    for contract_name, issues in sorted(feedback.validation_issues.items()):
        issue_projection[contract_name] = cast(
            JsonValue,
            [
                {
                    "type": issue.get("type"),
                    "loc": issue.get("loc", []),
                }
                for issue in issues
            ],
        )
    signature_payload: dict[str, JsonValue] = {
        "semantic_response": semantic_response,
        "validation_issues": cast(JsonValue, issue_projection),
        "loaded_tool_schema_ids": cast(
            JsonValue,
            sorted(feedback.loaded_tool_schema_ids),
        ),
    }
    return json.dumps(
        signature_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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


def _artifact_write_handoff_refs(refs: Sequence[ArtifactRef]) -> list[ArtifactRef]:
    """Keep Action/RunMemory focused on landed outputs and their read views."""

    handoff: list[ArtifactRef] = []
    for ref in refs:
        handoff.append(ref)
        if ref.artifact_type == "asset_plan":
            handoff.append(
                ref.model_copy(update={"artifact_type": "asset_plan_index"})
            )
    return _unique_artifact_refs(handoff)


def _visible_context_artifact_refs(context: ContextPack) -> list[ArtifactRef]:
    """Collect exact artifact identities that the Leader can actually see."""

    refs: list[ArtifactRef] = [artifact.ref for artifact in context.artifacts]
    for decision in context.decisions:
        refs.extend(decision.artifact_refs)
    for experience in context.experiences:
        refs.extend(experience.artifact_refs)
    for action in context.recent_actions:
        refs.extend(action.artifact_refs)
        if action.agent_result is not None:
            refs.extend(action.agent_result.evidence_refs)
    return _unique_artifact_refs(refs)


def _tool_attempt_ref_for_turn(
    project_id: str,
    work_order: WorkOrder,
    turn: SpecialistTurnRecord,
) -> ToolAttemptRef:
    outcome_status = None if turn.result is None else turn.result.get("status")
    return ToolAttemptRef(
        project_id=project_id,
        agent_run_id=work_order.parent_run_id,
        work_order_id=work_order.id,
        attempt_id=turn.id,
        tool_name=turn.tool_name,
        turn_status=turn.status,
        outcome_status=(
            outcome_status if isinstance(outcome_status, str) else None
        ),
        has_arguments=bool(turn.arguments),
        has_result=turn.result is not None,
    )


def _same_tool_attempt_locator(left: ToolAttemptRef, right: ToolAttemptRef) -> bool:
    """Compare immutable attempt identity; lifecycle fields are only a snapshot."""

    return left.identity() == right.identity() and left.tool_name == right.tool_name


def _tool_attempt_ref_matches_turn(
    project_id: str,
    work_order: WorkOrder,
    turn: SpecialistTurnRecord,
    ref: ToolAttemptRef,
) -> bool:
    return _same_tool_attempt_locator(
        _tool_attempt_ref_for_turn(project_id, work_order, turn),
        ref,
    )


def _work_order_owns_tool_attempt_ref(
    project_id: str,
    work_order: WorkOrder,
    ref: ToolAttemptRef,
) -> bool:
    """Validate a reference to an attempt produced by this same WorkOrder."""

    if (
        ref.project_id != project_id
        or ref.agent_run_id != work_order.parent_run_id
        or ref.work_order_id != work_order.id
    ):
        return False
    turn = next(
        (
            candidate
            for candidate in work_order.specialist_turns
            if candidate.id == ref.attempt_id
        ),
        None,
    )
    return turn is not None and _tool_attempt_ref_matches_turn(
        project_id,
        work_order,
        turn,
        ref,
    )


def _delegation_task_inputs(arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Preserve task data instead of silently dropping non-control arguments."""

    controls = {
        "why_now", "constraints", "confirmed_decisions", "inherited_decision_ids",
        "experience_tags", "adjacent_boundaries", "tool_attempt_refs", "open_questions",
        "acceptance_criteria", "expected_output_schema", "expected_output_slot_ids",
        "task_inputs",
    }
    explicit = arguments.get("task_inputs", {})
    if not isinstance(explicit, dict):
        raise ProductionManagerError("DELEGATION task_inputs must be an object")
    inputs = dict(explicit)
    for key, value in arguments.items():
        if key in controls:
            continue
        if key in inputs and inputs[key] != value:
            raise ProductionManagerError(f"DELEGATION task_inputs conflicts with {key!r}")
        inputs[key] = value
    return deepcopy(inputs)


def _tool_attempt_refs_argument(
    arguments: dict[str, JsonValue],
) -> list[ToolAttemptRef]:
    value = arguments.get("tool_attempt_refs")
    if not isinstance(value, list):
        return []
    refs: list[ToolAttemptRef] = []
    for item in value:
        if not isinstance(item, dict):
            raise ProductionManagerError(
                "DELEGATION tool_attempt_refs entries must be objects"
            )
        try:
            refs.append(ToolAttemptRef.model_validate(item))
        except ValueError as exc:
            raise ProductionManagerError(
                "DELEGATION contains an invalid Tool attempt reference"
            ) from exc
    return refs


def _plan_artifact_ref_entries(
    proposal: AgentPlanProposal,
) -> list[tuple[str, ArtifactRef]]:
    """Return all context-manifest refs authored by a proposed plan."""

    entries: list[tuple[str, ArtifactRef]] = []
    for step_index, step in enumerate(proposal.steps):
        for ref_index, ref in enumerate(step.artifact_refs):
            entries.append(
                (f"steps[{step_index}].artifact_refs[{ref_index}]", ref)
            )
        if step.kind != ActionKind.DELEGATION:
            continue
        for boundary_index, boundary in enumerate(
            _context_boundaries_argument(step.arguments)
        ):
            for ref_index, ref in enumerate(boundary.artifact_refs):
                entries.append(
                    (
                        "steps["
                        f"{step_index}].arguments.adjacent_boundaries["
                        f"{boundary_index}].artifact_refs[{ref_index}]",
                        ref,
                    )
                )
    return entries


def _plan_tool_attempt_ref_entries(
    proposal: AgentPlanProposal,
) -> list[tuple[str, ToolAttemptRef]]:
    entries: list[tuple[str, ToolAttemptRef]] = []
    for step_index, step in enumerate(proposal.steps):
        if step.kind != ActionKind.DELEGATION:
            continue
        for ref_index, ref in enumerate(_tool_attempt_refs_argument(step.arguments)):
            entries.append(
                (
                    f"steps[{step_index}].arguments.tool_attempt_refs[{ref_index}]",
                    ref,
                )
            )
    return entries


def _plan_inherited_decision_id_entries(
    proposal: AgentPlanProposal,
) -> list[tuple[str, str]]:
    """Return every Decision ID explicitly selected by a delegation step."""

    entries: list[tuple[str, str]] = []
    for step_index, step in enumerate(proposal.steps):
        if step.kind != ActionKind.DELEGATION:
            continue
        value = step.arguments.get("inherited_decision_ids")
        if not isinstance(value, list):
            continue
        for decision_index, decision_id in enumerate(value):
            if not isinstance(decision_id, str) or not decision_id.strip():
                continue
            entries.append(
                (
                    "steps["
                    f"{step_index}].arguments.inherited_decision_ids["
                    f"{decision_index}]",
                    decision_id,
                )
            )
    return entries


def _unavailable_plan_artifact_ref_feedback(
    proposal: AgentPlanProposal,
    context: ContextPack,
    *, observed_artifact_refs: Sequence[ArtifactRef] = (),
) -> dict[str, JsonValue] | None:
    """Describe invented context refs so the same Leader can correct its plan."""

    available_refs = _unique_artifact_refs([*_visible_context_artifact_refs(context), *observed_artifact_refs])
    available_identities = {ref.identity() for ref in available_refs}
    issues: list[JsonValue] = []
    for path, ref in _plan_artifact_ref_entries(proposal):
        if ref.identity() in available_identities:
            continue
        issues.append(
            {
                "path": path,
                "received": ref.model_dump(mode="json"),
            }
        )
    available_attempts = context.tool_attempt_refs
    available_attempt_by_identity = {
        ref.identity(): ref for ref in available_attempts
    }
    attempt_issues: list[JsonValue] = []
    for path, ref in _plan_tool_attempt_ref_entries(proposal):
        available = available_attempt_by_identity.get(ref.identity())
        if available is not None and _same_tool_attempt_locator(available, ref):
            continue
        attempt_issues.append(
            {
                "path": path,
                "received": ref.model_dump(mode="json"),
            }
        )
    issues.extend(attempt_issues)
    available_decision_ids = [decision.id for decision in context.decisions]
    available_decision_id_set = set(available_decision_ids)
    decision_issues: list[JsonValue] = []
    for path, decision_id in _plan_inherited_decision_id_entries(proposal):
        if decision_id in available_decision_id_set:
            continue
        decision_issues.append(
            {
                "path": path,
                "received": decision_id,
            }
        )
    issues.extend(decision_issues)
    if not issues:
        return None
    artifact_issue_count = len(issues) - len(attempt_issues) - len(decision_issues)
    issue_kinds = {
        kind
        for kind, count in (
            ("artifact", artifact_issue_count),
            ("tool_attempt", len(attempt_issues)),
            ("decision", len(decision_issues)),
        )
        if count > 0
    }
    return cast(
        dict[str, JsonValue],
        {
            "kind": (
                "UNAVAILABLE_ARTIFACT_REF"
                if issue_kinds == {"artifact"}
                else "UNAVAILABLE_TOOL_ATTEMPT_REF"
                if issue_kinds == {"tool_attempt"}
                else "UNAVAILABLE_DECISION_ID"
                if issue_kinds == {"decision"}
                else "UNAVAILABLE_CONTEXT_REF"
            ),
            "issues": issues,
            "available_artifact_refs": [
                ref.model_dump(mode="json") for ref in available_refs
            ],
            "available_tool_attempt_refs": [
                ref.model_dump(mode="json") for ref in available_attempts
            ],
            "available_decision_ids": available_decision_ids,
            "instruction": (
                "Copy complete ArtifactRefs exactly from the current ContextPack or the "
                "provided working_set.artifact_refs. Copy ToolAttemptRefs and inherited "
                "Decision IDs from the current ContextPack. Do not invent, concatenate, rename, "
                "alias, or infer its identity fields. If no exact current reference is "
                "available, end this plan before the dependent step and obtain it in a "
                "later replan."
            ),
        },
    )


def _production_manager_runtime_instructions(
    *, skill_prompt: str, role_values: str, prompt_stage_values: str,
) -> str:
    protocol = runtime_prompt("leader_protocol").replace("{{roles}}", role_values).replace("{{stages}}", prompt_stage_values)
    return "\n\n".join([runtime_prompt("common"), runtime_prompt("leader"), protocol, skill_prompt])


def _compact_tool_catalog_entry(
    descriptor: ToolDescriptor,
) -> dict[str, JsonValue]:
    """Expose discovery metadata only; exact contracts are loaded on demand."""

    description = descriptor.description.strip()
    for index, character in enumerate(description):
        if character in "。！？" or (character in ".!?" and (index + 1 == len(description) or description[index + 1].isspace())):
            description = description[:index + 1]
            break
    if len(description) > 64:
        description = description[:63] + "…"
    return cast(
        dict[str, JsonValue],
        {
            "id": descriptor.id,
            "description": description,
            "effect": descriptor.effect.value,
        },
    )


class LanguageProductionPlanner:
    """Ask the configured language model for a dynamic dependency-aware plan."""

    def __init__(self, client: LanguageProviderClient) -> None:
        self._client = client
        self.context_compiler = ModelInputCompiler()
        self._discovered_schema_ids: dict[str, list[str]] = {}

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
        available_method_ids = {item.id for item in method_skills}
        tool_catalog = [_compact_tool_catalog_entry(item) for item in tools]
        role_values = "、".join(
            role.value for role in _ACTIVE_PRODUCTION_SKILL_ROLES
        )
        prompt_stage_values = "、".join(
            stage.value for stage in ProductionPromptStage
        )
        instructions = _production_manager_runtime_instructions(
            skill_prompt=skill_prompt,
            role_values=role_values,
            prompt_stage_values=prompt_stage_values,
        )
        schema_ids = [*initial_tool_schema_ids(), *self._discovered_schema_ids.get(context.run_id, [])]
        if current_plan is not None:
            schema_ids.extend(step.tool_name for step in current_plan.steps if step.tool_name is not None)
        store = self.context_compiler.store
        if store is not None:
            run = store.get_run(context.run_id)
            if run is not None:
                schema_ids.extend(run.loaded_tool_schema_ids)
        loaded_schema_ids = list(dict.fromkeys(tool_id for tool_id in schema_ids if tool_id in tools_by_id))

        def remember_schemas() -> None:
            self._discovered_schema_ids[context.run_id] = list(loaded_schema_ids)
            if store is not None:
                store.remember_tool_schema_ids(context.run_id, loaded_schema_ids)

        remember_schemas()
        plan_validation_feedback: dict[str, JsonValue] | None = None
        schema_feedback: dict[str, JsonValue] | None = None
        rejected_schema_requests: set[tuple[str, ...]] = set()
        invalid_plan_signatures: set[str] = set()
        while True:
            execution_boundary()
            observed_artifact_refs: list[ArtifactRef] = []
            payload = {
                "reason": reason,
                "context": context.model_context_dump(),
                "available_tools": tool_catalog,
                "tool_schemas": {
                    tool_id: {
                        "description": tools_by_id[tool_id].description,
                        "input_schema": tools_by_id[tool_id].input_schema,
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
                "output_contracts": {
                    "plan": AgentPlanProposal.model_json_schema(),
                    "tool_schema_request": AgentToolSchemaRequest.model_json_schema(),
                },
                "current_plan": None
                if current_plan is None
                else current_plan.model_dump(mode="json"),
            }
            if schema_feedback is not None:
                payload["tool_schema_feedback"] = schema_feedback
            if plan_validation_feedback is not None:
                payload["plan_validation_feedback"] = plan_validation_feedback
            if self.context_compiler.store is not None:
                read_working_set = leader_read_working_set(
                    self.context_compiler.store, run_id=context.run_id,
                    project_id=context.project_id, tools=tools,
                    read_key=_specialist_read_observation_key,
                    project_result=_specialist_read_observation_result,
                )
                payload["working_set"] = read_working_set
                observed_artifact_refs = _artifact_refs_from_json(read_working_set.get("artifact_refs"))
                run = self.context_compiler.store.get_run(context.run_id)
                if run is not None:
                    payload["goal"] = {
                        "original_request": run.objective,
                        "current_goal": run.goal_summary,
                        "latest_user_instructions": run.latest_user_instructions,
                    }
            try:
                raw = generate_context_managed_text(
                    self._client, self.context_compiler,
                    run_id=context.run_id, project_id=context.project_id,
                    instructions=instructions,
                    payload=cast(dict[str, JsonValue], payload),
                    # Several legitimate delegations can exceed 7k output tokens.
                    # Leave JSON transport headroom without expanding the input
                    # context or prescribing a creative batching policy.
                    max_output_tokens=12000,
                )
            except Exception as exc:
                raise ProductionManagerError(
                    f"Production Manager planning failed: {exc}"
                ) from exc
            serialized = _json_block(raw)
            try:
                proposal = AgentPlanProposal.model_validate_json(serialized)
            except ValueError as plan_error:
                try:
                    schema_request = AgentToolSchemaRequest.model_validate_json(
                        serialized
                    )
                except ValueError as schema_error:
                    issues = _validation_issues(plan_error)
                    contract_feedback = cast(
                        dict[str, JsonValue],
                        {
                            "kind": "INVALID_PLAN_CONTRACT",
                            "issues": issues,
                            "instruction": (
                                "Return one complete JSON object matching "
                                "AgentPlanProposal. Correct the listed schema or JSON "
                                "issues; do not add prose, markdown, a second object, or "
                                "an invented top-level field."
                            ),
                        },
                    )
                    signature = json.dumps(
                        issues,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if signature in invalid_plan_signatures:
                        issue_summary = json.dumps(
                            issues,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        raise ProductionManagerError(
                            "Production Manager repeated the same invalid plan contract "
                            "after receiving typed validation feedback: "
                            + issue_summary[:4000]
                        ) from schema_error
                    invalid_plan_signatures.add(signature)
                    plan_validation_feedback = contract_feedback
                    continue
                unknown_ids = [
                    tool_id
                    for tool_id in schema_request.requested_tool_schema_ids
                    if tool_id not in tools_by_id
                ]
                new_ids = [tool_id for tool_id in schema_request.requested_tool_schema_ids
                           if tool_id in tools_by_id and tool_id not in loaded_schema_ids]
                loaded_schema_ids.extend(new_ids)
                remember_schemas()
                if unknown_ids or not new_ids:
                    schema_feedback = schema_request_feedback(schema_request.requested_tool_schema_ids, list(tools_by_id), loaded_schema_ids)
                    signature = tuple(schema_request.requested_tool_schema_ids)
                    if signature in rejected_schema_requests:
                        raise ProductionManagerError(
                            "Production Manager requested unknown Tool schemas or repeated loaded contracts after correction: "
                            + json_text(schema_feedback)
                        ) from plan_error
                    rejected_schema_requests.add(signature)
                else:
                    schema_feedback = None
                continue

            loaded_schema_ids.extend(step.tool_name for step in proposal.steps
                                     if step.tool_name in tools_by_id and step.tool_name not in loaded_schema_ids)
            remember_schemas()
            unknown_method_ids = [
                skill_id
                for skill_id in proposal.selected_method_skill_ids
                if skill_id not in available_method_ids
            ]
            if unknown_method_ids:
                feedback = cast(
                    dict[str, JsonValue],
                    {
                        "kind": "UNKNOWN_PRODUCTION_METHOD_SKILL",
                        "issues": [
                            {
                                "loc": ["selected_method_skill_ids"],
                                "msg": (
                                    "Every selected method must be copied exactly from "
                                    "available_production_methods; active format, profile, "
                                    "narrative, or role Skill IDs are not Production Method "
                                    "IDs."
                                ),
                                "input": proposal.selected_method_skill_ids,
                                "unknown_method_skill_ids": unknown_method_ids,
                            }
                        ],
                        "available_method_skill_ids": sorted(available_method_ids),
                        "instruction": (
                            "Return a corrected complete AgentPlanProposal. Remove the "
                            "unknown IDs or replace them only with exact IDs from "
                            "available_method_skill_ids; preserve the rest of the useful "
                            "plan."
                        ),
                    },
                )
                signature = json.dumps(
                    feedback,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if signature in invalid_plan_signatures:
                    raise ProductionManagerError(
                        "Production Manager repeated unknown Production Method Skills "
                        "after receiving typed validation feedback"
                    )
                invalid_plan_signatures.add(signature)
                plan_validation_feedback = feedback
                continue

            try:
                feedback = _unavailable_plan_artifact_ref_feedback(
                    proposal,
                    context,
                    observed_artifact_refs=observed_artifact_refs,
                )
            except ProductionManagerError as exc:
                issues = _validation_issues(exc)
                feedback = cast(
                    dict[str, JsonValue],
                    {
                        "kind": "INVALID_DELEGATION_CONTEXT",
                        "issues": issues,
                        "instruction": (
                            "Return a corrected complete AgentPlanProposal. Each "
                            "adjacent_boundaries entry must match ContextBoundary and "
                            "use only exact ArtifactRefs visible in the current "
                            "ContextPack."
                        ),
                    },
                )
            if feedback is None:
                return proposal
            signature = json.dumps(
                feedback["issues"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature in invalid_plan_signatures:
                issue_summary = json.dumps(
                    feedback["issues"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                raise ProductionManagerError(
                    "Production Manager repeated unresolved ArtifactRefs after receiving "
                    "typed plan validation feedback: " + issue_summary[:4000]
                )
            invalid_plan_signatures.add(signature)
            plan_validation_feedback = feedback


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
    working_notes: str | None = Field(default=None, max_length=8000,
        description="可选：替换本任务的当前工作笔记，保留判断、所需引用和下一步，不抄写工具历史。")


def specialist_tool_history_for_trace(
    work_order: WorkOrder,
) -> list[dict[str, object]]:
    """Project persisted specialist turns for trace/debug consumers.

    LanguageSpecialistAgent does not replay this append-only projection.  Its
    model input is rebuilt by ``_specialist_working_set`` so an old WorkOrder can
    retain a complete trace without paying for that trace on every decision.
    """

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


class SpecialistEntitySnapshot(DomainModel):
    """Latest known state for one durable domain entity."""

    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    source_tool: str = Field(min_length=1)
    source_sequence: int = Field(ge=1)
    state: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class SpecialistToolCheckpoint(DomainModel):
    """Latest control-plane outcome for one Tool, without its authored body."""

    tool_name: str = Field(min_length=1)
    source_sequence: int = Field(ge=1)
    status: JsonValue = None
    request_identifiers: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    error: JsonValue = None


class SpecialistToolRepairSnapshot(DomainModel):
    """Lightweight pointer to a caller-owned request that still needs repair.

    Exact arguments and results remain in the durable specialist turn.  The
    Worker can load them once through ``agent.get_tool_attempt`` when the error
    cannot be corrected from the identifiers and error summary alone.
    """

    tool_name: str = Field(min_length=1)
    source_sequence: int = Field(ge=1)
    attempt_ref: ToolAttemptRef
    request_identifiers: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    outcome_status: str | None = Field(default=None, min_length=1)
    error: JsonValue = None


class SpecialistReadObservation(DomainModel):
    """Latest successful observation of a read-only Tool's selected content.

    A stateless Worker often needs several independent facts at once (for
    example the current AssetPlan and the current image capability).  Keeping
    only the most recent Tool handoff makes those facts mutually exclusive and
    can cause the Worker to alternate between reads forever.  This whiteboard is
    bounded by Tool and document identity rather than turn count, while ``source_sequence``
    makes the age of each observation explicit after later writes.
    """

    tool_name: str = Field(min_length=1)
    source_sequence: int = Field(ge=1)
    request_identifiers: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    result: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class SpecialistToolAttemptObservation(DomainModel):
    """Exact on-demand payload explicitly selected for the current repair.

    A repair can depend on more than one persisted Tool attempt.  Keying these
    observations by ``ToolAttemptRef`` keeps every explicitly selected payload
    available together without replaying unrelated history or making the
    Worker alternate between mutually exclusive latest handoffs.
    """

    attempt_ref: ToolAttemptRef
    source_sequence: int = Field(ge=1)
    payload: str = Field(min_length=1)
    result: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class SpecialistProgressCheckpoint(DomainModel):
    """Bounded semantic progress channels rebuilt from the append-only trace."""

    total_events: int = Field(ge=0)
    lifecycle: WorkOrderProgressEntry | None = None
    leader_handoff: WorkOrderProgressEntry | None = None
    worker_handoff: WorkOrderProgressEntry | None = None
    external_handoff: WorkOrderProgressEntry | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class SpecialistWorkingSet(DomainModel):
    """Typed, reconstructable state supplied to one stateless specialist turn.

    ``latest_handoff`` retains exact content only for the immediately useful
    successful read. Failed requests and successful writes are represented by
    identifiers, compact outcomes and stable references. Independent successful
    read-only observations are retained once per Tool and selected scope because one
    decision may need several facts at the same time. Other earlier turns are
    folded by stable entity identity and Tool name; immutable content remains
    available through Artifact Tools and ``agent.get_tool_attempt``.
    """

    total_tool_turns: int = Field(ge=0)
    latest_handoff: dict[str, JsonValue] | None = None
    entities: list[SpecialistEntitySnapshot] = Field(
        default_factory=list[SpecialistEntitySnapshot]
    )
    tool_checkpoints: list[SpecialistToolCheckpoint] = Field(
        default_factory=list[SpecialistToolCheckpoint]
    )
    read_observations: list[SpecialistReadObservation] = Field(
        default_factory=list[SpecialistReadObservation]
    )
    tool_attempt_observations: list[SpecialistToolAttemptObservation] = Field(
        default_factory=list[SpecialistToolAttemptObservation]
    )
    pending_tool_repairs: list[SpecialistToolRepairSnapshot] = Field(
        default_factory=list[SpecialistToolRepairSnapshot]
    )
    repair_tool_schema_ids: list[str] = Field(default_factory=list[str])
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    progress: SpecialistProgressCheckpoint


_WORK_ORDER_LIFECYCLE_PROGRESS = frozenset(
    {
        "Specialist work started or resumed.",
    }
)


def _specialist_read_observation_key(
    turn: SpecialistTurnRecord, schema: Mapping[str, JsonValue] | None = None,
) -> str:
    """Keep independently selected documents together, replacing repeated reads.

    A document version is deliberately not part of the identity: reading a newer
    version replaces the earlier body without retaining a second full copy.
    Other reads use their complete selectors, including nested scopes and page
    positions. Tool-name-only keys lose one selected slice when another is read.
    """

    if turn.tool_name == "artifact.get_production_document":
        document_id = turn.arguments.get("production_document_id")
        if isinstance(document_id, str) and document_id:
            return json.dumps(
                [turn.tool_name, turn.arguments.get("project_id"), document_id],
                ensure_ascii=False,
            )
    if turn.tool_name == "artifact.read_current":
        return json.dumps(
            [
                turn.tool_name,
                turn.arguments.get("project_id"),
                turn.arguments.get("scene_id"),
                turn.arguments.get("shot_id"),
            ],
            ensure_ascii=False,
        )
    return json.dumps(
        [turn.tool_name, normalized_read_arguments(turn.arguments, schema)], ensure_ascii=False, sort_keys=True
    )


def _specialist_read_observation_result(
    turn: SpecialistTurnRecord,
) -> dict[str, JsonValue]:
    assert turn.result is not None
    result = _compact_specialist_tool_result(
        turn.tool_name, turn.result, retain_exact_payload=True
    )
    if turn.tool_name != "artifact.read_current" or not (
        turn.arguments.get("scene_id") or turn.arguments.get("shot_id")
    ):
        return result
    output = result.get("output")
    content = output.get("content") if isinstance(output, dict) else None
    if not isinstance(output, dict) or not isinstance(content, dict):
        return result
    # The latest handoff has the current shared project index. Older selected
    # scenes/shots retain their full content once, without repeating that index
    # and the Bible in every independently selected observation.
    return {
        **result,
        "output": {
            **output,
            "content": {
                key: value
                for key, value in content.items()
                if key in {
                    "project_id", "script_id", "episode_title", "editorial_revision",
                    "scene", "lines", "shot",
                }
            },
        },
    }


def _specialist_working_set(
    work_order: WorkOrder,
    progress_entries: Sequence[WorkOrderProgressEntry],
    *,
    project_id: str | None = None,
    read_tool_ids: frozenset[str] = frozenset(),
    write_tool_ids: frozenset[str] = frozenset(),
    read_schemas: Mapping[str, dict[str, JsonValue]] | None = None,
) -> SpecialistWorkingSet:
    """Fold durable history into the current decision state.

    This is deliberately identity-based rather than a last-N or character
    window. Repeated polling, retries and the same selected inspection scope
    replace the prior snapshot; distinct objects and review scopes stay visible.
    """

    read_schemas = {} if read_schemas is None else read_schemas
    # Route planning can consult a language provider (EXTERNAL), but its result
    # is a durable RoutePlan. A prompt compiler has no such saved artifact and
    # must continue handing off its exact result until the caller saves it.
    write_tool_ids = write_tool_ids | frozenset({"route.plan_h3"})
    ordered_turns = sorted(work_order.specialist_turns, key=lambda item: item.sequence)
    latest_turn = ordered_turns[-1] if ordered_turns else None
    latest_requires_repair = (
        False
        if latest_turn is None
        else _specialist_turn_requires_repair(
            latest_turn,
            write_tool_ids=write_tool_ids,
        )
    )
    has_repairable_turn = latest_requires_repair or any(
        _specialist_turn_requires_repair(
            turn,
            write_tool_ids=write_tool_ids,
        )
        for turn in ordered_turns
    )
    resolved_project_id = (
        project_id
        if project_id is not None
        else (
            _specialist_working_set_project_id(work_order, ordered_turns)
            if has_repairable_turn
            else ""
        )
    )
    latest_result_status = (
        None
        if latest_turn is None or latest_turn.result is None
        else latest_turn.result.get("status")
    )
    latest_is_successful_write = (
        latest_turn is not None
        and latest_turn.tool_name in write_tool_ids
        and latest_result_status == ToolCallStatus.SUCCEEDED.value
    )
    latest_is_compiled_prompt = (
        latest_turn is not None
        and latest_turn.tool_name == "route.compile_h3_prompt"
        and latest_result_status == ToolCallStatus.SUCCEEDED.value
    )
    latest_handoff = (
        None
        if latest_turn is None
        else _specialist_turn_handoff(
            latest_turn,
            retain_exact_payload=not (
                latest_requires_repair or latest_is_successful_write
            ),
            identifiers_only=(
                latest_requires_repair or latest_is_successful_write or latest_is_compiled_prompt
            ),
            compact_write_result=latest_is_successful_write,
            attempt_ref=(
                _selected_tool_attempt_ref(latest_turn)
                or (
                    _tool_attempt_ref_for_turn(
                        resolved_project_id,
                        work_order,
                        latest_turn,
                    )
                    if resolved_project_id
                    else None
                )
            ),
        )
    )

    entities: dict[tuple[str, str], SpecialistEntitySnapshot] = {}
    checkpoints: dict[str, SpecialistToolCheckpoint] = {}
    read_observations: dict[str, SpecialistReadObservation] = {}
    tool_attempt_observations: dict[
        tuple[str, str, str, str], SpecialistToolAttemptObservation
    ] = {}
    pending_tool_repairs: dict[str, SpecialistToolRepairSnapshot] = {}
    selected_repair_attempt_ids: set[str] = set()
    artifact_ref_slots: dict[tuple[str, str, str], ArtifactRef] = {}
    inspection_report_ids: dict[tuple[str, str], str] = {}
    shot_write_revisions: dict[str, int | None] = {}
    asset_plan_versions: dict[str, int] = {}
    turns_by_id = {turn.id: turn for turn in ordered_turns}
    for turn in ordered_turns:
        compact_result = (
            None
            if turn.result is None
            else _compact_specialist_tool_result(
                turn.tool_name,
                turn.result,
                retain_exact_payload=False,
            )
        )
        turn_refs = _artifact_refs_from_json(
            None if compact_result is None else compact_result.get("artifact_refs")
        )
        for ref in turn_refs:
            artifact_ref_slots[
                (ref.project_id, ref.artifact_type, ref.artifact_id)
            ] = ref
        report = _video_inspection_result_report(compact_result)
        if report is not None:
            key = (cast(str, report["project_id"]), cast(str, report["take_id"]))
            report_id = cast(str, report["id"])
            previous_id = inspection_report_ids.get(key)
            if previous_id is not None and previous_id != report_id:
                artifact_ref_slots.pop((key[0], "video_inspection", previous_id), None)
            inspection_report_ids[key] = report_id
        checkpoints[turn.tool_name] = SpecialistToolCheckpoint(
            tool_name=turn.tool_name,
            source_sequence=turn.sequence,
            status=(
                turn.status.value
                if compact_result is None
                else compact_result.get("status")
            ),
            request_identifiers=_specialist_request_identifiers(turn.arguments),
            artifact_refs=turn_refs,
            error=(None if compact_result is None else compact_result.get("error")),
        )
        result_status = (
            None if turn.result is None else turn.result.get("status")
        )
        fold_asset_plan_items(
            entities, turn,
            read_scope_key=_specialist_read_observation_key(turn, read_schemas.get(turn.tool_name)),
            read_scope=turn.arguments,
            known_versions=asset_plan_versions,
            entity_type=SpecialistEntitySnapshot,
        )
        if turn.tool_name == "artifact.retry_asset_plan_attempt":
            resolved_attempt_ids = _local_tool_attempt_lineage_ids(
                turn,
                work_order_id=work_order.id,
                turns_by_id=turns_by_id,
            )
            if resolved_attempt_ids:
                pending_tool_repairs = {
                    tool_name: repair
                    for tool_name, repair in pending_tool_repairs.items()
                    if repair.attempt_ref.attempt_id not in resolved_attempt_ids
                }
        consumed_attempt_ids = (
            selected_repair_attempt_ids
            if turn.tool_name in write_tool_ids
            else {
                observation.attempt_ref.attempt_id
                for observation in tool_attempt_observations.values()
                if observation.attempt_ref.tool_name == turn.tool_name
            }
        )
        if turn.tool_name in {"route.plan_h3", "route.compile_h3_prompt"}:
            # A video WorkOrder may load several independently compiled units.
            # Saving or recompiling one cannot consume the other units' output.
            independent_compilations = {
                observation.attempt_ref.attempt_id
                for observation in tool_attempt_observations.values()
                if observation.attempt_ref.tool_name == "route.compile_h3_prompt"
                and observation.attempt_ref.outcome_status == ToolCallStatus.SUCCEEDED.value
                and not _compiled_observation_matches_h3_target(observation, turn)
            }
            consumed_attempt_ids = consumed_attempt_ids - independent_compilations
        if consumed_attempt_ids:
            # An explicit write consumes the exact candidates the Worker loaded
            # to construct it.  If the write fails, that persisted write becomes
            # the new addressable repair base; replaying all of its ancestors
            # would duplicate payload and dilute the current error.
            pending_tool_repairs = {
                tool_name: repair
                for tool_name, repair in pending_tool_repairs.items()
                if repair.attempt_ref.attempt_id
                not in consumed_attempt_ids
            }
            selected_repair_attempt_ids = selected_repair_attempt_ids - consumed_attempt_ids
            tool_attempt_observations = {
                identity: observation
                for identity, observation in tool_attempt_observations.items()
                if observation.attempt_ref.attempt_id not in consumed_attempt_ids
            }
        if result_status == ToolCallStatus.SUCCEEDED.value:
            pending_tool_repairs.pop(turn.tool_name, None)
            selected_ref = _selected_tool_attempt_ref(turn)
            if selected_ref is not None:
                selected_repair_attempt_ids.add(selected_ref.attempt_id)
                if turn.result is not None:
                    payload_value = turn.arguments.get("payload")
                    tool_attempt_observations[selected_ref.identity()] = (
                        SpecialistToolAttemptObservation(
                            attempt_ref=selected_ref,
                            source_sequence=turn.sequence,
                            payload=(
                                payload_value
                                if isinstance(payload_value, str)
                                else "BOTH"
                            ),
                            result=_compact_specialist_tool_result(
                                turn.tool_name,
                                turn.result,
                                retain_exact_payload=True,
                            ),
                        )
                    )
            if turn.tool_name in write_tool_ids and turn_refs:
                for ref in turn_refs:
                    if ref.artifact_type == "shot":
                        entities.pop(("shot", ref.artifact_id), None)
                        entities.pop(("saved_shot", ref.artifact_id), None)
                        shot_write_revisions[ref.artifact_id] = ref.version
                        entities.pop(("shot_reference_plan", ref.artifact_id), None)
                        entities.pop(("reference_plan_preview", ref.artifact_id), None)
                    elif ref.artifact_type == "reference_pack":
                        entities.pop(("reference_pack", ref.artifact_id), None)
                    if ref.artifact_type in {"reference_pack", "asset_version", "asset"}:
                        for key in [key for key in entities if key[0] == "reference_plan_preview"]:
                            entities.pop(key, None)
                stale_read_tool_names = [
                    tool_name
                    for tool_name, observation in read_observations.items()
                    if _read_observation_is_superseded(
                        observation,
                        replacement_refs=turn_refs,
                    )
                ]
                for tool_name in stale_read_tool_names:
                    read_observations.pop(tool_name, None)
        elif _specialist_turn_requires_repair(
            turn,
            write_tool_ids=write_tool_ids,
        ):
            candidate = SpecialistToolRepairSnapshot(
                tool_name=turn.tool_name,
                source_sequence=turn.sequence,
                attempt_ref=_tool_attempt_ref_for_turn(
                    resolved_project_id,
                    work_order,
                    turn,
                ),
                request_identifiers=_specialist_request_identifiers(
                    turn.arguments
                ),
                outcome_status=(
                    result_status if isinstance(result_status, str) else None
                ),
                error=(None if turn.result is None else turn.result.get("error")),
            )
            current_repair = pending_tool_repairs.get(turn.tool_name)
            if _prefer_specialist_tool_repair(candidate, current_repair):
                pending_tool_repairs[turn.tool_name] = candidate
        if turn.tool_name in {
            "artifact.create_asset_plan",
            "artifact.replace_asset_plan",
            "artifact.retry_asset_plan_attempt",
        }:
            # Once a complete candidate exists in the server-side attempt
            # lineage, repair turns need only its stable reference and compact
            # diagnostics. Replaying the large planning-context body after
            # every patch duplicates the same upstream truth and dominated
            # real Worker inputs.
            for key in [
                key for key, observation in read_observations.items()
                if observation.tool_name == "artifact.get_asset_planning_context"
            ]:
                read_observations.pop(key, None)
        if result_status == ToolCallStatus.SUCCEEDED.value:
            request_value = turn.arguments.get("request")
            request = request_value if isinstance(request_value, dict) else {}
            if turn.tool_name == "route.get_generation_unit_context":
                for key, plan_read in list(read_observations.items()):
                    if plan_read.tool_name == "route.get_generation_plan" and (
                        plan_read.request_identifiers.get("version_id")
                        == request.get("generation_plan_version_id")
                    ):
                        # Selected units carry their own scoped facts; the full
                        # plan remains available through its artifact reference.
                        read_observations.pop(key, None)
            elif turn.tool_name == "route.plan_h3":
                for key, unit_read in list(read_observations.items()):
                    if unit_read.tool_name != "route.get_generation_unit_context":
                        continue
                    read_request = unit_read.request_identifiers.get("request")
                    saved_units = request.get("unit_ids")
                    if (
                        isinstance(read_request, dict)
                        and isinstance(saved_units, list)
                        and read_request.get("generation_unit_id") in saved_units
                        and read_request.get("generation_plan_version_id")
                        == request.get("generation_plan_version_id")
                    ):
                        read_observations.pop(key, None)
        if (
            turn.tool_name in read_tool_ids
            # This complete result is already retained per Shot in entities,
            # where subsequent Shot/media writes also invalidate it precisely.
            and turn.tool_name != "shot.preview_reference_plan"
            and turn.status == SpecialistTurnStatus.COMPLETED
            and turn.result is not None
            and turn.result.get("status") == ToolCallStatus.SUCCEEDED.value
        ):
            read_observations[_specialist_read_observation_key(turn, read_schemas.get(turn.tool_name))] = SpecialistReadObservation(
                tool_name=turn.tool_name,
                source_sequence=turn.sequence,
                request_identifiers=_specialist_request_identifiers(
                    turn.arguments
                ),
                result=_specialist_read_observation_result(turn),
            )
        if compact_result is not None:
            review_project_id = resolved_project_id or turn.arguments.get("project_id")
            _fold_specialist_entities(
                entities,
                tool_name=turn.tool_name,
                sequence=turn.sequence,
                arguments=turn.arguments,
                result=compact_result,
                shot_write_revisions=shot_write_revisions,
                attempt_ref=(
                    _tool_attempt_ref_for_turn(review_project_id, work_order, turn)
                    if turn.tool_name == "qa.inspect_asset_version"
                    and isinstance(review_project_id, str) and review_project_id
                    else None
                ),
            )

    progress = _specialist_progress_checkpoint(work_order, progress_entries)
    for ref in progress.artifact_refs:
        artifact_ref_slots[(ref.project_id, ref.artifact_type, ref.artifact_id)] = ref
    if latest_turn is not None:
        # The latest handoff already carries this exact payload.  Keep the
        # whiteboard free of duplicate copies while retaining every other
        # independent observation the Worker still needs for the decision.
        read_observations.pop(_specialist_read_observation_key(latest_turn, read_schemas.get(latest_turn.tool_name)), None)
        if latest_turn.tool_name in {"artifact.get_asset_work_context", "artifact.get_asset_planning_context", "artifact.read_current"}:
            # The immediate read already delivers this same Shot and source in
            # latest_handoff. Rebuild its entity on the following turn instead
            # of adding a second complete body to this request.
            for key, entity in list(entities.items()):
                if key[0] == "shot" and entity.source_sequence == latest_turn.sequence:
                    entities.pop(key)
        latest_selected_ref = _selected_tool_attempt_ref(latest_turn)
        if latest_selected_ref is not None:
            tool_attempt_observations.pop(latest_selected_ref.identity(), None)
    if latest_handoff is not None:
        latest_result = latest_handoff.get("result")
        receipt = latest_result.get("receipt") if isinstance(latest_result, dict) else None
        current_state = receipt.get("current_state") if isinstance(receipt, dict) else None
        saved_shot = current_state.get("saved_shot") if isinstance(current_state, dict) else None
        if isinstance(saved_shot, dict) and isinstance(saved_shot.get("shot_id"), str):
            key = ("saved_shot", cast(str, saved_shot["shot_id"]))
            saved_entity = entities.get(key)
            if saved_entity is not None and saved_entity.state == saved_shot:
                # The latest successful write already carries this exact summary
                # in its receipt. Rebuild it after the next Tool, not twice here.
                entities.pop(key)
        report = _video_inspection_result_report(
            cast(dict[str, JsonValue], latest_result) if isinstance(latest_result, dict) else None
        )
        if report is not None:
            # This report is already in latest_handoff; retain other Takes once.
            entities.pop(("video_inspection", cast(str, report["take_id"])), None)
    for key, observation in list(read_observations.items()):
        if asset_work_read_is_stale(
            observation.tool_name, observation.result, known_versions=asset_plan_versions,
        ):
            # A read-only index can reveal another writer's change too. Keep
            # exact historical snapshots addressable, not as current item facts.
            read_observations.pop(key)
    visible_plan_read_sequences = {
        observation.source_sequence for observation in read_observations.values()
        if observation.tool_name == "artifact.get_asset_work_context"
    }
    if latest_turn is not None and latest_turn.tool_name == "artifact.get_asset_work_context":
        visible_plan_read_sequences.add(latest_turn.sequence)
    for key, entity in list(entities.items()):
        if entity.kind == "asset_plan_item" and entity.source_sequence in visible_plan_read_sequences:
            # Its original scoped read already carries the same body (and its
            # exact snapshot). Materialize the item only if a later local write
            # invalidates that multi-object response while proving this item unchanged.
            entities.pop(key)
    repair_tool_schema_ids = sorted(pending_tool_repairs)
    if latest_turn is not None:
        latest_repair = pending_tool_repairs.get(latest_turn.tool_name)
        if (
            latest_repair is not None
            and latest_repair.source_sequence == latest_turn.sequence
        ):
            # latest_handoff already contains the same lightweight reference
            # and error summary. Avoid duplicating even the compact repair row.
            pending_tool_repairs.pop(latest_turn.tool_name, None)
    return SpecialistWorkingSet(
        total_tool_turns=len(ordered_turns),
        latest_handoff=latest_handoff,
        entities=sorted(entities.values(), key=lambda item: (item.kind, item.id)),
        tool_checkpoints=sorted(
            checkpoints.values(), key=lambda item: item.tool_name
        ),
        read_observations=sorted(
            read_observations.values(),
            key=lambda item: (item.tool_name, item.source_sequence),
        ),
        tool_attempt_observations=sorted(
            tool_attempt_observations.values(),
            key=lambda item: (
                item.attempt_ref.work_order_id,
                item.attempt_ref.attempt_id,
            ),
        ),
        pending_tool_repairs=sorted(
            pending_tool_repairs.values(), key=lambda item: item.tool_name
        ),
        repair_tool_schema_ids=repair_tool_schema_ids,
        artifact_refs=_unique_artifact_refs(list(artifact_ref_slots.values())),
        progress=progress,
    )


def _read_observation_is_superseded(
    observation: SpecialistReadObservation,
    *,
    replacement_refs: Sequence[ArtifactRef],
) -> bool:
    """Whether a successful write made an earlier exact read body stale.

    Artifact identity and version are the only signals used here.  Tool names,
    prompt wording, and artifact-type-specific routing do not participate, so
    the same rule applies to every versioned artifact exposed by the registry.
    The small read checkpoint and its exact refs remain elsewhere in the
    working set; only the potentially large stale observation body is removed.
    """

    observed_refs = _artifact_refs_from_json(
        observation.result.get("artifact_refs")
    )
    replacement_versions = {
        (ref.project_id, ref.artifact_type, ref.artifact_id): ref.version
        for ref in replacement_refs
        if ref.version is not None
    }
    return any(
        (replacement_version := replacement_versions.get(
            (
                observed_ref.project_id,
                observed_ref.artifact_type,
                observed_ref.artifact_id,
            )
        ))
        is not None
        and observed_ref.version != replacement_version
        for observed_ref in observed_refs
    )


def _prefer_specialist_tool_repair(
    candidate: SpecialistToolRepairSnapshot,
    current: SpecialistToolRepairSnapshot | None,
) -> bool:
    """Prefer a schema-valid repair base over a malformed follow-up.

    ``FAILED`` means the Tool input contract was accepted and the handler
    rejected a domain invariant.  That request is therefore a safer correction
    base than a later ``INVALID_ARGUMENTS`` attempt.  Among malformed attempts,
    retain the structurally richest payload instead of growing an attempt log.
    """

    if current is None:
        return True
    candidate_status = candidate.outcome_status
    current_status = current.outcome_status
    if candidate_status != current_status:
        return candidate_status == ToolCallStatus.FAILED.value
    return candidate.source_sequence > current.source_sequence


def _specialist_turn_requires_repair(
    turn: SpecialistTurnRecord,
    *,
    write_tool_ids: frozenset[str],
) -> bool:
    """Whether a persisted attempt should remain addressable for correction."""

    if turn.status != SpecialistTurnStatus.COMPLETED or turn.result is None:
        return False
    result_status = turn.result.get("status")
    return result_status == ToolCallStatus.INVALID_ARGUMENTS.value or (
        result_status == ToolCallStatus.FAILED.value
        and turn.tool_name in write_tool_ids
    )


def _compiled_observation_matches_h3_target(
    observation: SpecialistToolAttemptObservation,
    turn: SpecialistTurnRecord,
) -> bool:
    """Relate a loaded successful compilation to the explicit video target."""

    lookup_output = observation.result.get("output")
    if not isinstance(lookup_output, dict):
        return False
    attempt_result = lookup_output.get("result")
    if not isinstance(attempt_result, dict):
        return False
    compiled = attempt_result.get("output")
    request = turn.arguments.get("request")
    if not isinstance(compiled, dict) or not isinstance(request, dict):
        return False
    unit_id = compiled.get("generation_unit_id")
    if isinstance(unit_id, str):
        if compiled.get("generation_plan_version_id") != request.get("generation_plan_version_id"):
            return False
        target_id, single_key, multiple_key = unit_id, "generation_unit_id", "unit_ids"
    else:
        shot_id = compiled.get("shot_id")
        if not isinstance(shot_id, str):
            return False
        target_id, single_key, multiple_key = shot_id, "shot_id", "shot_ids"
    if turn.tool_name == "route.compile_h3_prompt":
        return request.get(single_key) == target_id
    selected_ids = request.get(multiple_key)
    if isinstance(selected_ids, list) and selected_ids:
        return target_id in selected_ids
    directives = request.get("shot_directives")
    return isinstance(directives, list) and any(
        isinstance(directive, dict) and directive.get(single_key) == target_id
        for directive in directives
    )


def _specialist_working_set_project_id(
    work_order: WorkOrder,
    turns: Sequence[SpecialistTurnRecord],
) -> str:
    """Resolve the project solely to construct self-owned ToolAttemptRefs.

    Production calls always pass the ContextPack project explicitly. The
    fallback keeps old persisted/test WorkOrders readable by deriving the same
    typed selector from their scope or Tool arguments.
    """

    if work_order.scope:
        return work_order.scope[0].project_id
    for turn in reversed(turns):
        candidate = turn.arguments.get("project_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        if turn.result is None:
            continue
        refs = _artifact_refs_from_json(turn.result.get("artifact_refs"))
        if refs:
            return refs[0].project_id
    raise ProductionManagerError(
        "Cannot construct a ToolAttemptRef without the current project id"
    )


def _selected_tool_attempt_ref(
    turn: SpecialistTurnRecord,
) -> ToolAttemptRef | None:
    """Return a self-selected repair reference from a successful on-demand read."""

    if (
        turn.tool_name != "agent.get_tool_attempt"
        or turn.result is None
        or turn.result.get("status") != ToolCallStatus.SUCCEEDED.value
    ):
        return None
    return _tool_attempt_ref_argument(turn)


def _tool_attempt_ref_argument(
    turn: SpecialistTurnRecord,
) -> ToolAttemptRef | None:
    """Parse an exact ToolAttemptRef argument without depending on turn outcome."""

    raw_ref = turn.arguments.get("attempt_ref")
    if not isinstance(raw_ref, dict):
        return None
    try:
        return ToolAttemptRef.model_validate(raw_ref)
    except ValueError:
        return None


def _local_tool_attempt_lineage_ids(
    turn: SpecialistTurnRecord,
    *,
    work_order_id: str,
    turns_by_id: Mapping[str, SpecialistTurnRecord],
) -> set[str]:
    """Resolve retry ancestors owned by this WorkingSet's WorkOrder.

    Retry arguments keep only a compact pointer to the prior exact candidate.
    Once the retry itself has produced an outcome, those local ancestors are
    superseded: a failed retry is the new repair base, while a successful retry
    resolves the whole lineage. References owned by another WorkOrder are not
    present in this local WorkingSet and are intentionally left to its handoff
    lifecycle.
    """

    ref = _tool_attempt_ref_argument(turn)
    lineage_ids: set[str] = set()
    while ref is not None and ref.work_order_id == work_order_id:
        if ref.attempt_id in lineage_ids:
            break
        lineage_ids.add(ref.attempt_id)
        source_turn = turns_by_id.get(ref.attempt_id)
        if (
            source_turn is None
            or source_turn.tool_name != "artifact.retry_asset_plan_attempt"
        ):
            break
        ref = _tool_attempt_ref_argument(source_turn)
    return lineage_ids


def _specialist_turn_handoff(
    turn: SpecialistTurnRecord,
    *,
    retain_exact_payload: bool,
    identifiers_only: bool = False,
    compact_write_result: bool = False,
    attempt_ref: ToolAttemptRef | None = None,
) -> dict[str, JsonValue]:
    request_arguments = (
        _specialist_request_identifiers(turn.arguments)
        if identifiers_only
        else _compact_specialist_tool_arguments(
            turn.tool_name,
            turn.arguments,
        )
    )
    handoff: dict[str, JsonValue] = {
        "sequence": turn.sequence,
        "request": {
            "tool_name": turn.tool_name,
            "arguments": request_arguments,
            "reason": turn.reason,
        },
        "turn_status": turn.status.value,
    }
    if attempt_ref is not None:
        handoff["attempt_ref"] = attempt_ref.model_dump(mode="json")
    if turn.result is not None:
        handoff["result"] = (
            {
                "tool_id": turn.result.get("tool_id"),
                "status": turn.result.get("status"),
                "artifact_refs": turn.result.get("artifact_refs"),
                "warnings": turn.result.get("warnings"),
                "error": turn.result.get("error"),
                "receipt": compatible_asset_plan_receipt(turn.tool_name, turn.result),
            }
            if compact_write_result or (attempt_ref is not None and not retain_exact_payload)
            else _compact_specialist_tool_result(
                turn.tool_name,
                turn.result,
                retain_exact_payload=retain_exact_payload,
            )
        )
    return handoff


def _specialist_request_identifiers(
    value: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Keep only typed selectors needed to relate a Tool checkpoint to state."""

    identifiers: dict[str, JsonValue] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            nested = _specialist_request_identifiers(
                cast(dict[str, JsonValue], item)
            )
            if nested:
                identifiers[key] = nested
            continue
        if key.endswith("_id") or key.endswith("_ids") or key in {
            "project_id",
            "operation",
        }:
            identifiers[key] = item
    return identifiers


def _artifact_refs_from_json(value: JsonValue | None) -> list[ArtifactRef]:
    if not isinstance(value, list):
        return []
    refs: list[ArtifactRef] = []
    for raw_ref in value:
        if not isinstance(raw_ref, dict):
            continue
        try:
            refs.append(ArtifactRef.model_validate(raw_ref))
        except ValueError:
            continue
    return refs


def _specialist_progress_checkpoint(
    work_order: WorkOrder,
    progress_entries: Sequence[WorkOrderProgressEntry],
) -> SpecialistProgressCheckpoint:
    ordered = sorted(progress_entries, key=lambda item: item.sequence)
    lifecycle: WorkOrderProgressEntry | None = None
    leader_handoff: WorkOrderProgressEntry | None = None
    worker_handoff: WorkOrderProgressEntry | None = None
    external_handoff: WorkOrderProgressEntry | None = None
    for entry in ordered:
        if entry.summary in _WORK_ORDER_LIFECYCLE_PROGRESS or entry.summary.startswith(
            "WorkOrder created:"
        ):
            lifecycle = entry
        elif entry.author == work_order.sender:
            leader_handoff = entry
        elif entry.author == work_order.recipient_role:
            worker_handoff = entry
        else:
            external_handoff = entry
    selected_entries = (
        lifecycle,
        leader_handoff,
        worker_handoff,
        external_handoff,
    )
    return SpecialistProgressCheckpoint(
        total_events=len(ordered),
        lifecycle=lifecycle,
        leader_handoff=leader_handoff,
        worker_handoff=worker_handoff,
        external_handoff=external_handoff,
        artifact_refs=_unique_artifact_refs(
            [
                ref
                for entry in selected_entries
                if entry is not None
                for ref in entry.artifact_refs
            ]
        ),
    )


def _video_inspection_result_report(
    result: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    if result is None or result.get("status") != ToolCallStatus.SUCCEEDED.value:
        return None
    output = result.get("output")
    report = output.get("report") if isinstance(output, dict) else None
    if (
        isinstance(report, dict)
        and "semantic_status" in report
        and "technical_checks" in report
        and all(isinstance(report.get(key), str) for key in ("id", "project_id", "take_id"))
    ):
        return cast(dict[str, JsonValue], report)
    return None


def _fold_specialist_take_states(
    entities: dict[tuple[str, str], SpecialistEntitySnapshot],
    *,
    tool_name: str,
    sequence: int,
    arguments: Mapping[str, JsonValue],
    output: Mapping[str, JsonValue],
) -> None:
    """Keep media identity and selection per Take, including legacy write results."""
    values: list[JsonValue] = []
    collection = output.get("takes")
    if isinstance(collection, list):
        values.extend(collection)
    single = output.get("take")
    if isinstance(single, dict):
        values.append(single)
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("task_id"), str):
            continue
        _put_specialist_entity(
            entities, kind="take", source_tool=tool_name, sequence=sequence,
            value=_select_json_fields(value, (
                "id", "project_id", "task_id", "shot_id", "generation_unit_id", "clip_group_id",
                "generation_plan_document_id", "generation_plan_version_id", "output_slot_id",
                "storyboard_document_id", "storyboard_version_id", "shot_windows",
                "media_path", "duration_seconds", "width", "height", "provider", "model",
                "availability", "freshness", "qa_status", "qa_report", "is_selected", "selected_shot_ids",
            )),
        )

    selection = output.get("take_selection")
    project = output.get("project")
    if not isinstance(selection, list) and isinstance(project, dict):
        selection = project.get("take_selection")
    if isinstance(selection, list):
        for item in selection:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            prior = entities.get(("take", cast(str, item["id"])))
            _put_specialist_entity(
                entities, kind="take", source_tool=tool_name, sequence=sequence,
                value={**(prior.state if prior else {}), **item},
            )
        return

    # Old persisted selection responses only contain a project summary. A
    # successful select_take selects all shots covered by the known Take; it
    # removes overlap from prior selections. Reconstruct that same domain effect
    # without changing the historical Tool result or re-executing its write.
    take_id = arguments.get("take_id")
    if tool_name != "video.select_take" or not isinstance(take_id, str):
        return
    prior = entities.get(("take", take_id))
    state = {**(prior.state if prior else {}), "id": take_id, "is_selected": True}
    windows = state.get("shot_windows")
    covered: list[JsonValue] = [
        item["shot_id"] for item in windows
        if isinstance(item, dict) and isinstance(item.get("shot_id"), str)
    ] if isinstance(windows, list) else []
    if not covered and isinstance(state.get("shot_id"), str):
        covered = [state["shot_id"]]
    if covered:
        state["selected_shot_ids"] = covered
        for key, other in list(entities.items()):
            selected = other.state.get("selected_shot_ids")
            if key[0] != "take" or key[1] == take_id or not isinstance(selected, list):
                continue
            remaining = [shot_id for shot_id in selected if shot_id not in covered]
            if remaining != selected:
                _put_specialist_entity(
                    entities, kind="take", source_tool=tool_name, sequence=sequence,
                    value={**other.state, "is_selected": bool(remaining), "selected_shot_ids": remaining},
                )
    _put_specialist_entity(entities, kind="take", source_tool=tool_name, sequence=sequence, value=state)


def _fold_specialist_entities(
    entities: dict[tuple[str, str], SpecialistEntitySnapshot],
    *,
    tool_name: str,
    sequence: int,
    arguments: Mapping[str, JsonValue],
    result: Mapping[str, JsonValue],
    shot_write_revisions: Mapping[str, int | None] | None = None,
    attempt_ref: ToolAttemptRef | None = None,
) -> None:
    output_value = result.get("output")
    if not isinstance(output_value, dict):
        return
    output = cast(dict[str, JsonValue], output_value)

    if result.get("status") == ToolCallStatus.SUCCEEDED.value:
        _fold_specialist_take_states(
            entities, tool_name=tool_name, sequence=sequence, arguments=arguments, output=output,
        )
        report = _video_inspection_result_report(result)
        if report is not None:
            _put_specialist_entity(
                entities,
                kind="video_inspection",
                source_tool=tool_name,
                sequence=sequence,
                value=report,
                identity_id=report["take_id"],
            )
        elif tool_name == "video.get_take_inspection" and output.get("report") is None:
            take = output.get("take")
            if isinstance(take, dict) and isinstance(take.get("id"), str):
                _put_specialist_entity(
                    entities, kind="video_inspection", source_tool=tool_name,
                    sequence=sequence, identity_id=take["id"],
                    value={"take_id": take["id"], "report": None, "inspection_available": False},
                )
        if tool_name == "shot.preview_reference_plan":
            _put_specialist_entity(
                entities,
                kind="reference_plan_preview",
                source_tool=tool_name,
                sequence=sequence,
                value=output,
                identity_id=output.get("shot_id"),
            )
        elif tool_name == "shot.save_current":
            saved_shot = output.get("saved_shot")
            if isinstance(saved_shot, dict):
                _put_specialist_entity(
                    entities, kind="saved_shot", source_tool=tool_name,
                    sequence=sequence, value=saved_shot, identity_id=saved_shot.get("shot_id"),
                )
            preview = output.get("reference_plan_preview")
            if isinstance(preview, dict):
                preview_scope = output.get("reference_plan_preview_scope")
                if preview_scope == "GENERATION_ONLY":
                    preview = {**preview, "scope": preview_scope}
                _put_specialist_entity(
                    entities, kind="reference_plan_preview", source_tool=tool_name,
                    sequence=sequence, value=preview, identity_id=preview.get("shot_id"),
                )
        # Preserve only the bounded Shots explicitly returned by these reads.
        # The asset work read uses the plan's pinned Storyboard source; keep
        # that identity instead of relabelling its snapshot as current content.
        shot_values: list[JsonValue] = []
        storyboard_identity: JsonValue = None
        current_shot_read = tool_name in {"artifact.read_current", "artifact.get_asset_planning_context"}
        if tool_name in {"artifact.get_asset_work_context", "artifact.get_asset_planning_context"}:
            storyboard = output.get("storyboard")
            storyboard_identity = (output.get("storyboard_identity")
                if tool_name == "artifact.get_asset_planning_context"
                else storyboard.get("identity") if isinstance(storyboard, dict) else None)
            shots = storyboard.get("shots") if isinstance(storyboard, dict) else None
            if isinstance(shots, list):
                shot_values = shots
        elif current_shot_read:
            content = output.get("content")
            shot = content.get("shot") if isinstance(content, dict) else None
            if isinstance(shot, dict):
                shot_values = [shot]
        for shot in shot_values:
            if not isinstance(shot, dict) or not isinstance(shot.get("id"), str):
                continue
            shot_id = cast(str, shot["id"])
            revision = shot.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool):
                continue
            known = entities.get(("shot", shot_id))
            known_shot = known.state.get("shot") if known is not None else None
            known_revision = known_shot.get("revision") if isinstance(known_shot, dict) else None
            if isinstance(known_revision, int) and not isinstance(known_revision, bool) and revision < known_revision:
                # A pinned source read must not overwrite a newer current read,
                # including changes made by the user or another Worker.
                continue
            if (known is not None and revision == known_revision and not current_shot_read
                and known.state.get("source") in ("CURRENT_SHOT_READ", "CURRENT_STORYBOARD_READ")):
                # A later historical work slice does not change which source
                # established the current body, even when this Shot is unchanged.
                continue
            if shot_write_revisions is not None and shot_id in shot_write_revisions:
                written_revision = shot_write_revisions[shot_id]
                if (written_revision is not None and revision < written_revision
                    or written_revision is None and not current_shot_read):
                    # A later reread of an old plan source must not resurrect
                    # the pre-edit body after an observed successful Shot write.
                    continue
            summary = entities.get(("saved_shot", shot_id))
            summary_revision = summary.state.get("revision") if summary is not None else None
            if isinstance(summary_revision, int) and revision >= summary_revision:
                # An exact full read now carries these same facts or a newer
                # revision. Do not keep an old saved summary beside current prose.
                entities.pop(("saved_shot", shot_id), None)
                if revision > summary_revision:
                    entities.pop(("reference_plan_preview", shot_id), None)
            refs = _artifact_refs_from_json(result.get("artifact_refs"))
            source_ref = next((ref for ref in refs if ref.artifact_type == "shot"
                and ref.artifact_id == shot_id and ref.version == revision), None)
            _put_specialist_entity(
                entities, kind="shot", source_tool=tool_name, sequence=sequence,
                identity_id=shot_id, value={
                    "shot": shot,
                    "source": ("CURRENT_STORYBOARD_READ" if tool_name == "artifact.get_asset_planning_context"
                        else "CURRENT_SHOT_READ" if current_shot_read else "ASSET_PLAN_STORYBOARD_SOURCE"),
                    "artifact_ref": None if source_ref is None else source_ref.model_dump(mode="json"),
                    "storyboard_identity": storyboard_identity,
                },
            )
        # A scoped read can contain several independent entities. A later Shot
        # write invalidates that read body, but does not change its packs or the
        # other Shots. Keep their latest known facts by identity so the Worker
        # need not guess metadata after each write. Exact write responses replace
        # the corresponding entity rather than retaining its old read snapshot.
        for collection_key, single_key, kind, identity_key in (
            ("reference_packs", "pack", "reference_pack", "id"),
            ("shot_reference_plans", "reference_plan", "shot_reference_plan", "shot_id"),
        ):
            values: list[JsonValue] = []
            collection = output.get(collection_key)
            if isinstance(collection, list):
                values.extend(collection)
            single = output.get(single_key)
            if isinstance(single, dict):
                values.append(single)
            for value in values:
                if isinstance(value, dict):
                    _put_specialist_entity(
                        entities,
                        kind=kind,
                        source_tool=tool_name,
                        sequence=sequence,
                        value=cast(dict[str, JsonValue], value),
                        identity_id=value.get(identity_key),
                    )

    task_value = output.get("task")
    if isinstance(task_value, dict):
        task = cast(dict[str, JsonValue], task_value)
        _put_specialist_entity(
            entities,
            kind="image_task" if tool_name.startswith("image.") else "task",
            source_tool=tool_name,
            sequence=sequence,
            value=_compact_image_task_state(task),
            identity_id=(
                cast(str, task["asset_id"])
                if isinstance(task.get("asset_id"), str)
                else None
            ),
        )
    tasks_value = output.get("tasks")
    if isinstance(tasks_value, list):
        for raw_task in tasks_value:
            if isinstance(raw_task, dict):
                task = cast(dict[str, JsonValue], raw_task)
                _put_specialist_entity(
                    entities,
                    kind=(
                        "image_task" if tool_name.startswith("image.") else "task"
                    ),
                    source_tool=tool_name,
                    sequence=sequence,
                    value=_compact_image_task_state(task),
                    identity_id=(
                        cast(str, task["asset_id"])
                        if isinstance(task.get("asset_id"), str)
                        else None
                    ),
                )

    asset_values = output.get("assets")
    assets = list(asset_values) if isinstance(asset_values, list) else []
    asset_value = output.get("asset")
    if isinstance(asset_value, dict):
        assets.append(asset_value)
    for asset_value in assets:
        if not isinstance(asset_value, dict) or not isinstance(asset_value.get("id"), str):
            continue
        asset_id = cast(str, asset_value["id"])
        previous = entities.get(("asset", asset_id))
        state = dict(previous.state) if previous else {}
        state.update({key: asset_value[key] for key in (
            "id", "project_id", "kind", "name", "bible_entity_id",
            "current_version_id", "approved_version_id", "brief",
        ) if key in asset_value})
        # Retain the current authored definition once per object. Successful
        # write handoffs keep only receipts; dropping this small brief forced a
        # Writer to read back its own just-saved clothes/appearance next turn.
        versions = asset_value.get("versions")
        current_version = next((item for item in versions if isinstance(item, dict)
            and item.get("id") == state.get("current_version_id")), None) if isinstance(versions, list) else None
        response_version = output.get("version")
        if current_version is None and isinstance(response_version, dict) and response_version.get("id") == state.get("current_version_id"):
            current_version = response_version
        if current_version is not None and "descriptor" in current_version:
            state["descriptor"] = current_version["descriptor"]
            state["descriptor_version_id"] = current_version.get("id")
        elif state.get("descriptor_version_id") != state.get("current_version_id"):
            state.pop("descriptor", None)
            state.pop("descriptor_version_id", None)
        _put_specialist_entity(entities, kind="asset", source_tool=tool_name, sequence=sequence, value=state)
        # Batch responses carry each initial version inside its own Asset,
        # avoiding a repeated ProjectAggregate or an ambiguous shared version.
        if tool_name == "artifact.create_assets" and isinstance(current_version, dict):
            _put_specialist_entity(entities, kind="asset_version", source_tool=tool_name, sequence=sequence,
                value=_compact_version_state(current_version, entity_kind="asset_version"))
    version_value = output.get("version")
    if isinstance(version_value, dict):
        version = cast(dict[str, JsonValue], version_value)
        entity_kind = (
            "production_document_version"
            if isinstance(output.get("document"), dict)
            else "asset_version"
        )
        _put_specialist_entity(
            entities,
            kind=entity_kind,
            source_tool=tool_name,
            sequence=sequence,
            value=_compact_version_state(version, entity_kind=entity_kind),
            # Views of one character share an Asset, but approval and media
            # facts belong to each exact version and must coexist in context.
            identity_id=version.get("id"),
        )
    document_value = output.get("document")
    if isinstance(document_value, dict):
        _put_specialist_entity(
            entities,
            kind="production_document",
            source_tool=tool_name,
            sequence=sequence,
            value=_select_json_fields(
                cast(dict[str, JsonValue], document_value),
                (
                    "id",
                    "project_id",
                    "kind",
                    "title",
                    "current_version_id",
                ),
            ),
        )

    progress_value = output.get("progress")
    if isinstance(progress_value, dict):
        progress = cast(dict[str, JsonValue], progress_value)
        draft_ref_value = progress.get("draft_ref")
        draft_ref = (
            cast(dict[str, JsonValue], draft_ref_value)
            if isinstance(draft_ref_value, dict)
            else {}
        )
        draft_id = draft_ref.get("artifact_id") or progress.get("draft_version_id")
        if isinstance(draft_id, str) and draft_id:
            _put_specialist_entity(
                entities,
                kind="storyboard_draft_progress",
                source_tool=tool_name,
                sequence=sequence,
                value={"id": draft_id, **progress},
            )

    if tool_name == "qa.inspect_asset_version" and result.get("status") == ToolCallStatus.SUCCEEDED.value:
        version_id = arguments.get("version_id")
        if isinstance(version_id, str) and version_id:
            request = arguments.get("request")
            # Compare the actual selected inspection scope, not inferred topic
            # names. Explicit defaults are equivalent; ordered references and
            # detail regions remain significant. Future request fields survive.
            scope: dict[str, JsonValue] = {
                "shot_id": None, "canonical_reference_version_ids": [],
                "review_focus": None, "regions": [],
                **(request if isinstance(request, dict) else {}),
            }
            key = ("asset_review", json.dumps([
                arguments.get("project_id"), arguments.get("asset_id"), version_id, scope,
            ], ensure_ascii=False, sort_keys=True))
            prior = entities.get(key)
            # Keep short display identities, while the private dictionary key
            # uses the full scope without hashes or model-visible duplicate text.
            identity = prior.id if prior is not None else (
                version_id if not any(item.kind == "asset_review" and item.id == version_id
                                      for item in entities.values())
                else f"{version_id}:review-{sequence}"
            )
            entities[key] = SpecialistEntitySnapshot(
                kind="asset_review",
                id=identity,
                source_tool=tool_name,
                source_sequence=sequence,
                state={
                    "id": version_id,
                    "asset_id": arguments.get("asset_id"),
                    "version_id": version_id,
                    "review_scope": scope,
                    **({"attempt_ref": attempt_ref.model_dump(mode="json")}
                       if attempt_ref is not None else {}),
                    "recommended": output.get("recommended"),
                    "score": output.get("score"),
                    "summary": output.get("summary"),
                    "issues": output.get("issues"),
                    "evidence_references": output.get("evidence_references"),
                    **{field: output[field] for field in ("candidate_output_layout", "region_evidence")
                       if field in output},
                    **({"crop_suggestion": output["crop_suggestion"]}
                       if output.get("crop_suggestion") is not None else {}),
                    **({"edit_guide_suggestion": output["edit_guide_suggestion"]}
                       if output.get("edit_guide_suggestion") is not None else {}),
                    **({"plane_composite_suggestion": output["plane_composite_suggestion"]}
                       if output.get("plane_composite_suggestion") is not None else {}),
                },
            )


def _put_specialist_entity(
    entities: dict[tuple[str, str], SpecialistEntitySnapshot],
    *,
    kind: str,
    source_tool: str,
    sequence: int,
    value: dict[str, JsonValue],
    identity_id: JsonValue | None = None,
) -> None:
    entity_id = identity_id if isinstance(identity_id, str) else value.get("id")
    if not isinstance(entity_id, str) or not entity_id:
        return
    entities[(kind, entity_id)] = SpecialistEntitySnapshot(
        kind=kind,
        id=entity_id,
        source_tool=source_tool,
        source_sequence=sequence,
        state=value,
    )


def _select_json_fields(
    value: Mapping[str, JsonValue],
    fields: Sequence[str],
) -> dict[str, JsonValue]:
    return {field: value.get(field) for field in fields}


def _compact_image_task_state(
    task: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _select_json_fields(
        task,
        (
            "id",
            "project_id",
            "asset_id",
            "output_version_id",
            "output_version_number",
            "parent_version_id",
            "operation",
            "status",
            "provider",
            "model",
            "attempt",
            "retry_of_task_id",
            "provider_request_id",
            "provider_output_path",
            "output_path",
            "output_mime_type",
            "usage",
            "cost",
            "error_code",
            "error_message",
            "submitted_at",
            "finished_at",
            "updated_at",
        ),
    )


def _compact_version_state(
    version: Mapping[str, JsonValue],
    *,
    entity_kind: str,
) -> dict[str, JsonValue]:
    common = (
        "id",
        "version",
        "parent_version_id",
        "origin",
        "origin_type",
        "media_type",
        "local_path",
        "external_url",
        "mime_type",
        "width",
        "height",
        "duration_seconds",
        "image_task_id",
        "approval_status",
        "review_notes",
        "freshness",
        "created_at",
    )
    state = _select_json_fields(version, common)
    state["entity_kind"] = entity_kind
    return state


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
        "shot.append_storyboard_groups",
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
                "alignment": audit.get("alignment"),
                "difference_seconds": audit.get("difference_seconds"),
                "assessment": audit.get("assessment"),
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

    if tool_name == "shot.append_storyboard_groups":
        work_unit_value = request.get("work_unit")
        work_unit = (
            cast(dict[str, JsonValue], work_unit_value)
            if isinstance(work_unit_value, dict)
            else {}
        )
        groups_value = request.get("groups")
        groups = (
            [
                cast(dict[str, JsonValue], item)
                for item in groups_value
                if isinstance(item, dict)
            ]
            if isinstance(groups_value, list)
            else []
        )
        compact_groups: list[JsonValue] = []
        for group in groups:
            clip_value = group.get("clip_group")
            clip_group = (
                cast(dict[str, JsonValue], clip_value)
                if isinstance(clip_value, dict)
                else {}
            )
            shots_value = group.get("shots")
            shots = (
                [
                    {
                        "id": shot.get("id"),
                        "sequence": shot.get("sequence"),
                        "scene_id": shot.get("scene_id"),
                        "duration_seconds": shot.get("duration_seconds"),
                        "dialogue_line_ids": shot.get("dialogue_line_ids"),
                        "narrative_purpose": shot.get("narrative_purpose"),
                    }
                    for item in shots_value
                    if isinstance(item, dict)
                    for shot in [cast(dict[str, JsonValue], item)]
                ]
                if isinstance(shots_value, list)
                else []
            )
            compact_groups.append(
                {
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
            "groups": compact_groups,
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
    """Project persisted Tool results into a compact model-facing handoff.

    This projection is used in the next language-model input, where artifact refs,
    exact domain results and compact project state are sufficient to resume work.
    Full ProjectAggregate values embedded in mutation and inspection responses may
    contain every prior production-document version; the authoritative project is
    already durable, so those convenience snapshots are summarized before both
    persistence and replay.  Exact authored documents are read through their
    explicit read Tools rather than being copied after every write.
    """

    # Provider transport bodies remain in the Tool trace and repository. Apply
    # this before Tool-specific projections, including nested attempt reads.
    result = cast(dict[str, JsonValue], provider_media_model_view(result))

    if tool_name == "agent.get_tool_attempt":
        output_value = result.get("output")
        if isinstance(output_value, dict):
            attempt_ref = output_value.get("attempt_ref")
            attempt_result = output_value.get("result")
            if (
                isinstance(attempt_ref, dict)
                and attempt_ref.get("tool_name") == "route.compile_h3_prompt"
                and isinstance(attempt_result, dict)
                and attempt_result.get("status") == ToolCallStatus.SUCCEEDED.value
            ):
                # Reading a successful compile by reference should have the
                # same model projection as receiving that compile directly.
                # Keep explicitly requested arguments and the raw trace intact.
                return {**result, "output": {
                    **output_value,
                    "result": _compact_specialist_tool_result(
                        "route.compile_h3_prompt", attempt_result,
                        retain_exact_payload=retain_exact_payload,
                    ),
                }}
        return result

    if tool_name == "route.list_h3_runs":
        output_value = result.get("output")
        raw_runs = output_value.get("runs") if isinstance(output_value, dict) else None
        if not isinstance(raw_runs, list):
            return result
        if isinstance(output_value, dict) and output_value.get("details_tool") == "route.get_h3_run":
            # v2 is already a typed, compact Tool result. Keep its local task /
            # Take handoff and counts instead of re-projecting them as zero.
            return result
        runs: list[JsonValue] = []
        for raw_run in raw_runs:
            if not isinstance(raw_run, dict):
                continue
            run: dict[str, JsonValue] = {key: raw_run.get(key) for key in (
                "id", "task_id", "project_id", "shot_id", "generation_unit_id", "shot_ids",
                "route", "downloaded_path", "last_error_code", "last_error_message",
                "created_at", "updated_at",
            )}
            remote = raw_run.get("latest_remote_task")
            if isinstance(remote, dict):
                run["remote"] = {key: remote.get(key) for key in ("id", "status", "error")}
            prompt, media = raw_run.get("prompt"), raw_run.get("media_inputs")
            run["prompt_characters"] = len(prompt) if isinstance(prompt, str) else 0
            run["media_input_count"] = len(media) if isinstance(media, list) else 0
            runs.append(run)
        return {**result, "output": {
            "runs": runs, "details_tool": "route.get_h3_run", "task_state_tool": "task.get",
        }}

    if tool_name == "route.list_h3_plans":
        output_value = result.get("output")
        if not isinstance(output_value, dict):
            return result
        raw_plans = output_value.get("plans")
        if not isinstance(raw_plans, list):
            return result
        plans: list[JsonValue] = []
        for raw_plan in raw_plans:
            if not isinstance(raw_plan, dict):
                continue
            plan = {key: value for key, value in raw_plan.items() if key != "decisions"}
            decisions: list[JsonValue] = []
            raw_decisions = raw_plan.get("decisions")
            for raw_decision in raw_decisions if isinstance(raw_decisions, list) else []:
                if not isinstance(raw_decision, dict):
                    continue
                decision = {key: value for key, value in raw_decision.items()
                            if key not in {"prompt", "media_inputs", "video_guides"}}
                prompt = raw_decision.get("prompt")
                media = raw_decision.get("media_inputs")
                decision["prompt_characters"] = len(prompt) if isinstance(prompt, str) else 0
                decision["ordered_asset_version_ids"] = [
                    item.get("asset_version_id") for item in media if isinstance(item, dict)
                ] if isinstance(media, list) else []
                decisions.append(decision)
            plan["decisions"] = decisions
            plans.append(plan)
        return {**result, "output": {"plans": plans, "details_tool": "route.get_h3_plan"}}

    if tool_name == "route.compile_h3_prompt":
        output_value = result.get("output")
        if isinstance(output_value, dict):
            # The successful response is the new draft. Its input draft remains
            # in the durable attempt, not duplicated in the next decision.
            return {
                **result,
                "output": {key: value for key, value in output_value.items() if key != "source_prompt"},
            }
        return result

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
        assets_value = document.get("assets")
        assets: list[JsonValue] = []
        if isinstance(assets_value, list):
            for raw_asset in assets_value:
                if not isinstance(raw_asset, dict):
                    continue
                asset = cast(dict[str, JsonValue], raw_asset)
                current_version_id = asset.get("current_version_id")
                approved_version_id = asset.get("approved_version_id")
                selected_version_ids = {
                    version_id
                    for version_id in (
                        current_version_id,
                        approved_version_id,
                    )
                    if isinstance(version_id, str)
                }
                versions_value = asset.get("versions")
                selected_versions: list[JsonValue] = []
                if isinstance(versions_value, list):
                    selected_versions = [
                        cast(dict[str, JsonValue], version)
                        for version in versions_value
                        if isinstance(version, dict)
                        and version.get("id") in selected_version_ids
                    ]
                assets.append(
                    {
                        "id": asset.get("id"),
                        "kind": asset.get("kind"),
                        "name": asset.get("name"),
                        "bible_entity_id": asset.get("bible_entity_id"),
                        "brief": asset.get("brief"),
                        "current_version_id": current_version_id,
                        "approved_version_id": approved_version_id,
                        # Keep the exact current/approved media records so an
                        # Agent can assign source-intake contributions and
                        # reference their AssetVersion IDs in AssetPlan.
                        "versions": selected_versions,
                    }
                )
        editorial_value = document.get("editorial_timeline")
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
            "asset_plan_document_id": document.get(
                "asset_plan_document_id"
            ),
            "generation_plan_document_id": document.get(
                "generation_plan_document_id"
            ),
            "output_slots": output_slots,
            "assets": assets,
            "studio_exports": document.get("studio_exports", []),
            "editorial_revision": editorial_value.get("revision", 0) if isinstance(editorial_value, dict) else 0,
        }
        compact_project["output"] = {
            "document": document_summary,
            "progress": output.get("progress"),
        }
        return compact_project

    if tool_name in {
        "artifact.get_production_document",
        "route.get_generation_plan",
        "route.compile_generation_plan",
    }:
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
        has_typed_plan = isinstance(output.get("generation_plan"), dict)
        if retain_exact_payload and not has_typed_plan:
            selected_version["payload"] = payload_value
        elif not retain_exact_payload and isinstance(payload_value, dict):
            payload = cast(dict[str, JsonValue], payload_value)
            selected_version["payload_summary"] = {
                "phase": payload.get("phase"),
                "script_version_id": payload.get("script_version_id"),
                "bible_version_id": payload.get("bible_version_id"),
            }
        selected_output: dict[str, JsonValue] = {
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
        if has_typed_plan and retain_exact_payload:
            # GenerationPlan responses repeat the same content in the typed
            # payload, version.payload, and every document.versions entry.
            selected_output["generation_plan"] = output["generation_plan"]
        if "output_slot" in output:
            selected_output["output_slot"] = output["output_slot"]
        compact_document["output"] = selected_output
        return compact_document

    if tool_name not in {
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
        "shot.append_storyboard_groups",
        "shot.replace_storyboard_draft_group",
        "shot.record_storyboard_draft_verification",
        "shot.finalize_storyboard",
    }:
        compact_result: dict[str, JsonValue] = {
            "tool_id": result.get("tool_id"),
            "status": result.get("status"),
            "artifact_refs": result.get("artifact_refs"),
            "warnings": result.get("warnings"),
            "error": result.get("error"),
        }
        output_value = result.get("output")
        if isinstance(output_value, dict):
            compact_result["output"] = _compact_project_aggregates_in_output(
                cast(dict[str, JsonValue], output_value)
            )
        else:
            compact_result["output"] = output_value
        return compact_result

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
        "shot.append_storyboard_groups",
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
        if tool_name == "shot.append_storyboard_groups":
            compact_output = cast(dict[str, JsonValue], compact["output"])
            compact_output["checkpoints"] = output.get("checkpoints")
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


def _compact_project_aggregates_in_output(
    output: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Replace direct ProjectAggregate values with a resumable state summary.

    ProjectAggregate is a service/API convenience envelope, not the specialist's
    artifact handoff.  A Tool's actual domain result (for example ``asset`` and
    ``version``) stays exact, while a direct embedded aggregate is reduced to
    current IDs, document pointers, output slots, progress, and collection counts.
    The same structural rule also covers Tools whose whole output is an aggregate,
    without maintaining a Tool-name allowlist.
    """

    if _looks_like_project_aggregate(output):
        return _compact_project_aggregate(output)

    compact: dict[str, JsonValue] = {}
    for key, value in output.items():
        if isinstance(value, dict) and _looks_like_project_aggregate(value):
            compact[key] = _compact_project_aggregate(
                cast(dict[str, JsonValue], value)
            )
        else:
            compact[key] = value
    return compact


def _specialist_tool_result_for_persistence(
    tool_name: str,
    result: dict[str, JsonValue],
    *,
    arguments: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Persist Tool facts without duplicating the complete project database.

    ``project.get`` is the explicit canonical read and remains lossless here; its
    model-facing projection is handled by ``_specialist_working_set``.  Other
    Tool responses keep their complete domain payload and envelope, while any
    direct ProjectAggregate convenience value is replaced by the same current-
    state summary used for the next model turn.  The authoritative project and
    versioned artifacts already live in ProjectDocumentStore.
    """

    if tool_name == "project.get":
        return result
    validation_feedback = _compact_asset_plan_input_validation_feedback(
        tool_name,
        arguments,
        result,
    )
    if validation_feedback is not None:
        result = validation_feedback
    output_value = result.get("output")
    if not isinstance(output_value, dict):
        return result
    compact_output = _compact_project_aggregates_in_output(
        cast(dict[str, JsonValue], output_value)
    )
    if compact_output is output_value or compact_output == output_value:
        return result
    return {
        **result,
        "output": compact_output,
    }


def _compact_asset_plan_input_validation_feedback(
    tool_name: str,
    arguments: Mapping[str, JsonValue] | None,
    result: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    """Replace opaque index-only Pydantic errors with stable repair identities.

    The complete rejected candidate remains persisted in the specialist turn for
    server-side retry.  The next model turn receives only the failing item/work
    item fields needed to write a small stable-ID patch.
    """

    if (
        arguments is None
        or result.get("status") != ToolCallStatus.INVALID_ARGUMENTS.value
        or tool_name
        not in {"artifact.create_asset_plan", "artifact.replace_asset_plan"}
    ):
        return None
    try:
        if tool_name == "artifact.create_asset_plan":
            AssetPlanCreateInput.model_validate(dict(arguments))
        else:
            AssetPlanReplaceInput.model_validate(dict(arguments))
    except ValidationError as exc:
        validation_error = exc
    else:
        return None

    request_value = arguments.get("request")
    if not isinstance(request_value, dict):
        return None
    request = cast(dict[str, JsonValue], request_value)
    plan_value = request.get("asset_plan")
    if not isinstance(plan_value, dict):
        return None
    plan = cast(dict[str, JsonValue], plan_value)
    items_value = plan.get("items")
    items = items_value if isinstance(items_value, list) else []

    issues: list[JsonValue] = []
    for raw_error in validation_error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        error = cast(dict[str, object], raw_error)
        raw_location = error.get("loc")
        location: list[JsonValue] = []
        if isinstance(raw_location, tuple):
            for raw_segment in cast(tuple[object, ...], raw_location):
                if isinstance(raw_segment, (str, int)):
                    location.append(raw_segment)
        item_index: int | None = None
        work_item_index: int | None = None
        for index, segment in enumerate(location[:-1]):
            next_segment = location[index + 1]
            if segment == "items" and isinstance(next_segment, int):
                item_index = next_segment
            if segment == "work_items" and isinstance(next_segment, int):
                work_item_index = next_segment

        item: dict[str, JsonValue] | None = None
        if (
            item_index is not None
            and 0 <= item_index < len(items)
            and isinstance(items[item_index], dict)
        ):
            item = cast(dict[str, JsonValue], items[item_index])
        work_item: dict[str, JsonValue] | None = None
        if item is not None and work_item_index is not None:
            work_items_value = item.get("work_items")
            if (
                isinstance(work_items_value, list)
                and 0 <= work_item_index < len(work_items_value)
                and isinstance(work_items_value[work_item_index], dict)
            ):
                work_item = cast(
                    dict[str, JsonValue],
                    work_items_value[work_item_index],
                )

        issue: dict[str, JsonValue] = {
            "loc": location,
            "type": str(error.get("type", "validation_error")),
            "msg": str(error.get("msg", "AssetPlan input is invalid")),
            "item_id": None if item is None else item.get("id"),
            "work_item_id": (
                None if work_item is None else work_item.get("id")
            ),
        }
        offending_node: dict[str, JsonValue] = {}
        if item is not None:
            brief_value = item.get("brief")
            offending_node["item"] = {
                "id": item.get("id"),
                "disposition": item.get("disposition"),
                "asset_kind": item.get("asset_kind"),
                "brief": (
                    None
                    if not isinstance(brief_value, dict)
                    else {
                        "presentation_mode": brief_value.get(
                            "presentation_mode"
                        ),
                        "required_visible_text": brief_value.get(
                            "required_visible_text"
                        ),
                    }
                ),
            }
        if work_item is not None:
            work_item_context: dict[str, JsonValue] = {
                "id": work_item.get("id")
            }
            for key in (
                "operation",
                "output_role",
                "review_purpose",
                "reference_pack_intent",
                "ordered_references",
                "text_plate_intent",
                "dependency_ids",
            ):
                work_item_context[key] = work_item.get(key)
            offending_node["work_item"] = work_item_context
        issue["offending_node"] = offending_node
        issues.append(issue)

    if not issues:
        return None
    error_value = result.get("error")
    error = (
        cast(dict[str, JsonValue], error_value)
        if isinstance(error_value, dict)
        else {}
    )
    return {
        **result,
        "error": {
            **error,
            "message": json.dumps(
                {
                    "kind": "ASSET_PLAN_INPUT_VALIDATION",
                    "instruction": (
                        "Keep the persisted candidate and repair only these "
                        "stable-ID nodes with artifact.retry_asset_plan_attempt."
                    ),
                    "issues": issues,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    }


def _looks_like_project_aggregate(value: dict[str, JsonValue]) -> bool:
    document = value.get("document")
    if not isinstance(document, dict):
        return False
    project = cast(dict[str, JsonValue], document).get("project")
    return (
        isinstance(project, dict)
        and isinstance(value.get("tasks"), list)
        and isinstance(value.get("takes"), list)
        and isinstance(value.get("exports"), list)
        and isinstance(value.get("progress"), dict)
    )


def _compact_project_aggregate(
    aggregate: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    document_value = aggregate.get("document")
    document = (
        cast(dict[str, JsonValue], document_value)
        if isinstance(document_value, dict)
        else {}
    )
    project_value = document.get("project")
    project = (
        cast(dict[str, JsonValue], project_value)
        if isinstance(project_value, dict)
        else {}
    )
    compact_project: dict[str, JsonValue] = {
        key: project.get(key)
        for key in (
            "id",
            "name",
            "source_mode",
            "run_mode",
            "production_profile",
            "content_planning_output",
            "language",
            "aspect_ratio",
            "target_duration_seconds",
            "voice_reference_revision",
            "stage",
            "current_script_version_id",
            "current_bible_version_id",
            "current_manifest_id",
            "updated_at",
        )
    }

    production_document_refs: list[JsonValue] = []
    production_documents_value = document.get("production_documents")
    if isinstance(production_documents_value, list):
        for raw_document in production_documents_value:
            if not isinstance(raw_document, dict):
                continue
            production_document = cast(dict[str, JsonValue], raw_document)
            production_document_refs.append(
                {
                    "id": production_document.get("id"),
                    "kind": production_document.get("kind"),
                    "current_version_id": production_document.get(
                        "current_version_id"
                    ),
                }
            )

    output_slots: list[JsonValue] = []
    output_slots_value = document.get("output_slots")
    if isinstance(output_slots_value, list):
        for raw_slot in output_slots_value:
            if not isinstance(raw_slot, dict):
                continue
            slot = cast(dict[str, JsonValue], raw_slot)
            output_slots.append(
                {
                    "id": slot.get("id"),
                    "revision": slot.get("revision"),
                    "current_artifact_ref": slot.get("current_artifact_ref"),
                    "invalidation_reason": slot.get("invalidation_reason"),
                }
            )

    collection_keys = (
        "assets",
        "shots",
        "reference_packs",
        "production_documents",
        "manifests",
    )
    document_counts: dict[str, JsonValue] = {
        key: len(value)
        for key in collection_keys
        if isinstance((value := document.get(key)), list)
    }
    aggregate_collection_keys = (
        "tasks",
        "takes",
        "exports",
        "route_evidence",
        "video_inspections",
        "take_comparisons",
        "audio_inspections",
        "episode_reviews",
    )
    aggregate_counts: dict[str, JsonValue] = {
        key: len(value)
        for key in aggregate_collection_keys
        if isinstance((value := aggregate.get(key)), list)
    }
    takes_value = aggregate.get("takes")
    takes = takes_value if isinstance(takes_value, list) else []

    return {
        "project": compact_project,
        "document_pointers": {
            key: document.get(key)
            for key in (
                "master_audio_document_id",
                "audio_structure_document_id",
                "timed_text_document_id",
                "production_spec_document_id",
                "storyboard_document_id",
                "asset_plan_document_id",
                "generation_plan_document_id",
            )
        },
        "production_document_refs": production_document_refs,
        "output_slots": output_slots,
        "take_selection": [
            _select_json_fields(take, ("id", "task_id", "shot_id", "generation_unit_id", "is_selected", "selected_shot_ids"))
            for take in takes
            if isinstance(take, dict)
        ],
        "counts": {**document_counts, **aggregate_counts},
        "progress": aggregate.get("progress"),
    }


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


def _specialist_runtime_instructions(*, may_call_tool: bool, system_prompt: str) -> str:
    tool_protocol = (
        "available_tools 是完整紧凑目录；tool_schemas 已含常用及本任务已发现的当前定义，可直接调用。"
        "只有尚未加载的复杂参数才请求 requested_tool_schema_ids 和 rationale，"
        "已加载 schema 不重复请求。每次返回一个包含 tool_name、reason、arguments 的 Tool 请求；"
        "可附 working_notes 更新简洁的当前工作笔记，不复制工具历史；"
        "结果持久化后继续同一 WorkOrder。"
        if may_call_tool else "当前没有可用 Tool，直接返回任务结果。"
    )
    protocol = runtime_prompt("worker_protocol").replace("{{tool_protocol}}", tool_protocol)
    return "\n\n".join([runtime_prompt("common"), runtime_prompt("worker"), protocol, system_prompt])


class LanguageSpecialistAgent:
    """Make one specialist decision from a persisted WorkOrder checkpoint."""

    def __init__(
        self,
        client: LanguageProviderClient,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._client = client
        self._tools = tools
        self.context_compiler = ModelInputCompiler()
        self._tool_descriptors: list[ToolDescriptor] | None = None
        self._discovered_schema_ids: dict[str, list[str]] = {}

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
                else [self._tools.describe(item.id) for item in self._tool_descriptors
                      if self._tools.contains(item.id)]
            )
        )
        tool_catalog = [
            _compact_tool_catalog_entry(item) for item in descriptors
        ]
        tools_by_id = {item.id: item for item in descriptors}
        may_call_tool = bool(descriptors) and self._tools is not None
        working_set = _specialist_working_set(
            work_order.work_order,
            work_order.progress_entries,
            project_id=work_order.context.project_id,
            read_tool_ids=frozenset(
                item.id
                for item in descriptors
                if item.effect == ToolEffect.READ
                and (item.id == "agent.read_context" or "on-demand-payload" not in item.tags)
            ),
            write_tool_ids=frozenset(
                item.id for item in descriptors if item.effect == ToolEffect.WRITE
            ),
            read_schemas={item.id: cast(dict[str, JsonValue], item.input_schema) for item in descriptors},
        )
        instructions = _specialist_runtime_instructions(
            may_call_tool=may_call_tool,
            system_prompt=work_order.system_prompt,
        )
        protocol_feedback = work_order.work_order.pending_protocol_feedback
        addressable_attempt_refs: dict[
            tuple[str, str, str, str], ToolAttemptRef
        ] = {
            ref.identity(): ref for ref in work_order.context.tool_attempt_refs
        }
        for repair in working_set.pending_tool_repairs:
            addressable_attempt_refs[repair.attempt_ref.identity()] = (
                repair.attempt_ref
            )
        for observation in working_set.tool_attempt_observations:
            addressable_attempt_refs[observation.attempt_ref.identity()] = (
                observation.attempt_ref
            )
        for entity in working_set.entities:
            ref_value = entity.state.get("attempt_ref")
            if entity.kind == "asset_review" and isinstance(ref_value, dict):
                ref = ToolAttemptRef.model_validate(ref_value)
                addressable_attempt_refs[ref.identity()] = ref
        latest_attempt_ref: ToolAttemptRef | None = None
        if working_set.latest_handoff is not None:
            raw_latest_ref = working_set.latest_handoff.get("attempt_ref")
            if not isinstance(raw_latest_ref, dict):
                latest_request = working_set.latest_handoff.get("request")
                if isinstance(latest_request, dict):
                    latest_arguments = latest_request.get("arguments")
                    if isinstance(latest_arguments, dict):
                        raw_latest_ref = latest_arguments.get("attempt_ref")
            if isinstance(raw_latest_ref, dict):
                try:
                    latest_attempt_ref = ToolAttemptRef.model_validate(
                        raw_latest_ref
                    )
                except ValueError:
                    latest_attempt_ref = None
        if latest_attempt_ref is not None:
            addressable_attempt_refs[latest_attempt_ref.identity()] = (
                latest_attempt_ref
            )
        has_addressable_tool_attempt = bool(addressable_attempt_refs)
        asset_plan_attempt_tool_ids = {
            "artifact.create_asset_plan",
            "artifact.replace_asset_plan",
            "artifact.retry_asset_plan_attempt",
        }
        has_addressable_asset_plan_attempt = any(
            ref.tool_name in asset_plan_attempt_tool_ids
            for ref in addressable_attempt_refs.values()
        )
        direct_attempt_tool_ids = sorted(
            {
                ref.tool_name
                for ref in addressable_attempt_refs.values()
                if ref.tool_name not in asset_plan_attempt_tool_ids
            }
        )
        has_addressable_non_asset_attempt = bool(direct_attempt_tool_ids)
        repair_schema_ids = [
            tool_id
            for tool_id in working_set.repair_tool_schema_ids
            if not (
                has_addressable_asset_plan_attempt
                and tool_id in asset_plan_attempt_tool_ids
            )
        ]
        schema_ids_to_load = [
            *initial_tool_schema_ids(work_order.work_order),
            *work_order.work_order.loaded_tool_schema_ids,
            *self._discovered_schema_ids.get(work_order.work_order.id, []),
            # Old persisted runs predate discovery state. Successful calls still
            # identify contracts this WorkOrder has already used.
            *[turn.tool_name for turn in work_order.work_order.specialist_turns],
            *(
                ["artifact.retry_asset_plan_attempt"]
                if has_addressable_asset_plan_attempt
                else []
            ),
            *(
                ["agent.get_tool_attempt"]
                if has_addressable_tool_attempt
                and has_addressable_non_asset_attempt
                else []
            ),
            *direct_attempt_tool_ids,
            *repair_schema_ids,
            *(
                []
                if protocol_feedback is None
                else protocol_feedback.loaded_tool_schema_ids
            ),
        ]
        store = self.context_compiler.store
        if store is not None:
            saved_work = store.get_work_order(work_order.work_order.id)
            if saved_work is not None:
                schema_ids_to_load.extend(saved_work.loaded_tool_schema_ids)
        loaded_schema_ids = list(dict.fromkeys(tool_id for tool_id in schema_ids_to_load if tool_id in tools_by_id))

        def remember_schemas() -> None:
            self._discovered_schema_ids[work_order.work_order.id] = list(loaded_schema_ids)
            work_order.work_order.loaded_tool_schema_ids = list(loaded_schema_ids)
            if store is not None:
                store.remember_tool_schema_ids(work_order.work_order.parent_run_id, loaded_schema_ids,
                                               work_order_id=work_order.work_order.id)

        remember_schemas()
        schema_feedback: dict[str, JsonValue] | None = None
        rejected_schema_requests: set[tuple[str, ...]] = set()
        working_set_payload = project_retained_read_results(
            working_set.model_dump(mode="json"), store=store,
            project_id=work_order.context.project_id, work_order=work_order.work_order,
        )
        if store is not None:
            selected_ids = exact_context_values(work_order.work_order.task_inputs)
            selected_ids.update(ref.artifact_id for ref in [
                *work_order.work_order.scope,
                *(artifact.ref for artifact in work_order.context.artifacts),
                *working_set.artifact_refs,
            ] if ref.artifact_type not in BROAD_COMPLETION_REF_TYPES)
            handoffs, handoff_refs = completed_work_handoffs(store,
                run_id=work_order.work_order.parent_run_id, project_id=work_order.context.project_id,
                selected_ids=selected_ids, exclude_work_order_id=work_order.work_order.id)
            if handoffs:
                working_set_payload["completion_handoffs"] = handoffs
                working_set_payload["completion_handoff_guidance"] = (
                    "These are persisted completed deliveries relevant to your selected objects/media. "
                    "Use their actual completion time and findings when older task wording says to wait for that work. "
                    "A completed review is an observation, not a new approval or proof that later edits remain valid."
                )
                known_refs = {ref.identity(): ref for ref in working_set.artifact_refs}
                known_refs.update({ref.identity(): ref for ref in handoff_refs})
                working_set_payload["artifact_refs"] = [ref.model_dump(mode="json") for ref in known_refs.values()]
        while True:
            execution_boundary()
            payload = {
                        "work_order": work_order.work_order.model_dump(
                            mode="json",
                            exclude={
                                "specialist_turns",
                                "pending_protocol_feedback",
                                "context_manifest",
                                "loaded_tool_schema_ids",
                            },
                        ),
                        "context": work_order.context.model_context_dump(),
                        "working_set": working_set_payload,
                        "available_tools": tool_catalog,
                        "tool_schemas": {
                            tool_id: {
                                "description": tools_by_id[tool_id].description,
                                "input_schema": tools_by_id[tool_id].input_schema,
                            }
                            for tool_id in loaded_schema_ids
                        },
                        "response_schemas": {
                            "final_result": (
                                _specialist_work_order_result_schema()
                            ),
                            "tool_request": SpecialistToolRequest.model_json_schema(),
                            "tool_schema_request": (
                                AgentToolSchemaRequest.model_json_schema()
                            ),
                        },
                        "tool_schema_feedback": schema_feedback,
                        "response_protocol_feedback": (
                            None
                            if protocol_feedback is None
                            else protocol_feedback.model_dump(mode="json")
                        ),
                    }
            if self.context_compiler.store is not None:
                run = self.context_compiler.store.get_run(work_order.work_order.parent_run_id)
                if run is not None:
                    payload["goal"] = {
                        "original_request": run.objective,
                        "current_goal": run.goal_summary,
                        "latest_user_instructions": run.latest_user_instructions,
                    }
            raw = generate_context_managed_text(
                self._client, self.context_compiler,
                run_id=work_order.work_order.parent_run_id, project_id=work_order.context.project_id,
                instructions=instructions,
                payload=cast(dict[str, JsonValue], payload),
                # A semantic segment may contain several adjacent detailed groups.
                # This is transport headroom so the Agent can finish valid JSON,
                # not a creative length target or a fixed batching requirement.
                max_output_tokens=20000,
            )
            payload = _normalize_specialist_response_payload(raw)
            try:
                result = _validate_specialist_result_ownership(
                    AgentWorkOrderResultRequest.model_validate_json(payload)
                )
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
                    requested_tool = SpecialistToolRequest.model_validate_json(payload)
                    if requested_tool.tool_name in tools_by_id and requested_tool.tool_name not in loaded_schema_ids:
                        loaded_schema_ids.append(requested_tool.tool_name)
                        remember_schemas()
                    return requested_tool
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
                    new_ids = [tool_id for tool_id in schema_request.requested_tool_schema_ids
                               if tool_id in tools_by_id and tool_id not in loaded_schema_ids]
                    loaded_schema_ids.extend(new_ids)
                    remember_schemas()
                    if unknown_ids or not new_ids:
                        schema_feedback = schema_request_feedback(schema_request.requested_tool_schema_ids, list(tools_by_id), loaded_schema_ids)
                        signature = tuple(schema_request.requested_tool_schema_ids)
                        if signature in rejected_schema_requests:
                            raise ProductionManagerError(
                                "Specialist Agent requested unknown Tool schemas or repeated loaded contracts after correction: "
                                + json_text(schema_feedback)
                            ) from tool_error
                        rejected_schema_requests.add(signature)
                    else:
                        schema_feedback = None

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
        self._project_tool_locks_guard = threading.RLock()
        self._project_tool_locks: dict[str, threading.Lock] = {}
        if not self.tools.contains("agent.get_tool_attempt"):
            register_tool_attempt_tools(self.tools, self)
        if not self.tools.contains("agent.read_context"):
            register_context_tools(self.tools, self)
        if not self.tools.contains("artifact.retry_asset_plan_attempt"):
            register_asset_plan_attempt_tools(self.tools, self)
        self._recover_interrupted_operations()

    def read_context(self, request: AgentReadContextInput) -> AgentReadContextOutput:
        run = self.runtime.store.get_run(request.run_id)
        if run is None or run.project_id != request.project_id:
            raise ProductionManagerError("Context snapshot does not belong to this project/run")
        snapshot = self.runtime.store.get_context_snapshot(request.snapshot_id, request.run_id)
        if snapshot is None:
            raise ProductionManagerError("Context snapshot was not found")
        value = pointer_value(snapshot, request.pointer)
        text = value if isinstance(value, str) else json_text(value)
        if request.offset > len(text):
            raise ProductionManagerError("Context page offset is outside the saved value")
        end = min(len(text), request.offset + request.limit)
        return AgentReadContextOutput(
            snapshot_id=request.snapshot_id, pointer=request.pointer,
            content=text[request.offset:end], offset=request.offset,
            total_characters=len(text), next_offset=end if end < len(text) else None,
            keys=list(value)[:100] if isinstance(value, dict) else [],
            item_count=len(value) if isinstance(value, (list, dict)) else None,
        )

    def _resolve_tool_attempt(
        self,
        ref: ToolAttemptRef,
    ) -> tuple[WorkOrder, SpecialistTurnRecord]:
        work_order = self.runtime.store.get_work_order(ref.work_order_id)
        if (
            work_order is None
            or work_order.parent_run_id != ref.agent_run_id
        ):
            raise ProductionManagerError(
                f"Unknown Tool attempt source WorkOrder: {ref.work_order_id}"
            )
        run = self.runtime.store.get_run(ref.agent_run_id)
        if run is None or run.project_id != ref.project_id:
            raise ProductionManagerError(
                f"Unknown Tool attempt AgentRun: {ref.agent_run_id}"
            )
        turn = next(
            (
                item
                for item in work_order.specialist_turns
                if item.id == ref.attempt_id
            ),
            None,
        )
        if (
            turn is None
            or not _tool_attempt_ref_matches_turn(
                run.project_id,
                work_order,
                turn,
                ref,
            )
        ):
            raise ProductionManagerError(
                f"Unknown or stale Tool attempt reference: {ref.attempt_id}"
            )
        return work_order, turn

    def get_tool_attempt(
        self,
        request: AgentGetToolAttemptInput,
    ) -> AgentGetToolAttemptOutput:
        """Resolve an explicitly-addressed persisted Tool attempt losslessly."""

        ref = request.attempt_ref
        work_order, turn = self._resolve_tool_attempt(ref)
        include_arguments = request.payload in {
            ToolAttemptPayload.ARGUMENTS,
            ToolAttemptPayload.BOTH,
        }
        include_result = request.payload in {
            ToolAttemptPayload.RESULT,
            ToolAttemptPayload.BOTH,
        }
        return AgentGetToolAttemptOutput(
            attempt_ref=_tool_attempt_ref_for_turn(
                ref.project_id,
                work_order,
                turn,
            ),
            reason=turn.reason,
            arguments=(dict(turn.arguments) if include_arguments else None),
            result=(
                None
                if not include_result or turn.result is None
                else dict(turn.result)
            ),
        )

    def _asset_plan_attempt_base(
        self,
        ref: ToolAttemptRef,
        *,
        seen: set[tuple[str, str, str, str]],
    ) -> tuple[str, dict[str, JsonValue]]:
        identity = ref.identity()
        if identity in seen:
            raise ProductionManagerError("AssetPlan retry attempt lineage contains a cycle")
        seen.add(identity)
        _, turn = self._resolve_tool_attempt(ref)
        result_status = None if turn.result is None else turn.result.get("status")
        if result_status not in {
            ToolCallStatus.FAILED.value,
            ToolCallStatus.INVALID_ARGUMENTS.value,
        }:
            raise ProductionManagerError(
                "AssetPlan retry requires a failed persisted Tool attempt"
            )
        if turn.tool_name in {
            "artifact.create_asset_plan",
            "artifact.replace_asset_plan",
        }:
            return turn.tool_name, deepcopy(dict(turn.arguments))
        if turn.tool_name != "artifact.retry_asset_plan_attempt":
            raise ProductionManagerError(
                "AssetPlan retry can only continue create/replace/retry attempts"
            )
        try:
            retry = AssetPlanAttemptRetryInput.model_validate(dict(turn.arguments))
        except ValueError as exc:
            raise ProductionManagerError(
                "Persisted AssetPlan retry arguments are invalid"
            ) from exc
        tool_name, arguments = self._asset_plan_attempt_base(
            retry.attempt_ref,
            seen=seen,
        )
        return tool_name, _apply_asset_plan_attempt_patches(
            arguments,
            retry.patches,
        )

    def retry_asset_plan_attempt(
        self,
        request: AssetPlanAttemptRetryInput,
    ) -> ToolExecution[AssetPlanMutationSummaryResponse]:
        """Patch and replay a persisted AssetPlan mutation without model retransmission."""

        if request.project_id != request.attempt_ref.project_id:
            raise ProductionManagerError(
                "AssetPlan retry project_id must match its ToolAttemptRef"
            )
        tool_name, arguments = self._asset_plan_attempt_base(
            request.attempt_ref,
            seen=set(),
        )
        patched_arguments = _apply_asset_plan_attempt_patches(
            arguments,
            request.patches,
        )
        result = self.tools.invoke(tool_name, patched_arguments)
        if result.status != ToolCallStatus.SUCCEEDED or result.output is None:
            compact_result = _specialist_tool_result_for_persistence(
                tool_name,
                cast(
                    dict[str, JsonValue],
                    result.model_dump(mode="json"),
                ),
                arguments=patched_arguments,
            )
            error_value = compact_result.get("error")
            error = (
                cast(dict[str, JsonValue], error_value)
                if isinstance(error_value, dict)
                else {}
            )
            message = error.get("message")
            raise ProductionManagerError(
                str(message or "Unknown AssetPlan retry failure")
            )
        return ToolExecution(
            output=AssetPlanMutationSummaryResponse.model_validate(result.output),
            artifact_refs=tuple(result.artifact_refs),
            warnings=tuple(result.warnings),
            receipt=result.receipt,
        )

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
            statement=(
                "Video execution target: "
                f"{request.execution_target.value}"
            ),
            rationale=(
                "Selects only the video-generation executor; image Provider "
                "availability is determined independently through image.capability."
            ),
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
                statement=(
                    "Video execution target: "
                    f"{request.execution_target.value}"
                ),
                rationale=(
                    "Selects only the video-generation executor; image Provider "
                    "availability is determined independently through image.capability."
                ),
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
            snapshot=_public_agent_run_snapshot(snapshot),
            ready_steps=self.runtime.ready_steps(run_id),
            context=context,
            content_planning_trace=planning_trace,
        )

    def prepare_completion_planning(
        self, run_id: str, *, include_local_plan_completion: bool = False,
        include_failed_plan: bool = False,
    ) -> CompletionPlanningInput | None:
        """Claim new completed work once, while unrelated work still continues."""
        with self._run_lock(run_id):
            if include_failed_plan:
                self._return_leader_owned_work_orders(run_id)
            snapshot = self.runtime.snapshot(run_id)
            plan = snapshot.current_plan
            if plan is None or plan.goal_complete or snapshot.run.status in {
                AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED,
            }:
                return None
            graph = PlanGraph(plan)
            failure_state = self._plan_failure_state(snapshot, plan)
            if failure_state and not include_failed_plan:
                return None
            owned_work_ids = {action.work_order_id for action in snapshot.actions if graph.owns_action(action)}
            workers = [work for work in self.runtime.store.list_work_orders(run_id)
                       if work.active_plan_revision_id == plan.id or work.id in owned_work_ids]
            unfinished = [work for work in workers if work.status in {
                DelegationStatus.PENDING, DelegationStatus.IN_PROGRESS, DelegationStatus.NEEDS_ATTENTION,
            }]
            if (not unfinished and not failure_state) or any(
                work.next_owner == WorkOrderNextOwner.USER_INPUT for work in unfinished
            ) or (not failure_state and any(work.next_owner == WorkOrderNextOwner.LEADER_REPLAN for work in unfinished)):
                return None
            # Workers selected by their current active-plan ownership may have
            # an original Action from before they were carried into this plan.
            completed_ids = {action.work_order_id for action in snapshot.actions
                             if action.status == ActionStatus.SUCCEEDED}
            new_ids = [work.id for work in workers if work.status == DelegationStatus.COMPLETED
                       and (work.id in completed_ids or work.id in plan.carried_work_order_ids)
                       and work.id not in snapshot.run.completion_planned_work_order_ids]
            local_complete = (include_local_plan_completion and bool(plan.steps)
                              and graph.is_complete(snapshot.actions))
            if not new_ids and not local_complete and not failure_state:
                return None
            document = self._require_project(snapshot.run.project_id)
            self.refresh_project_memory(document)
            prepared = CompletionPlanningInput(
                run_id=run_id, plan_revision_id=plan.id, completed_work_order_ids=new_ids,
                latest_user_instructions=list(snapshot.run.latest_user_instructions),
                current_plan=AgentPlanProposal(
                    summary=plan.summary, steps=plan.steps,
                    selected_method_skill_ids=plan.selected_method_skill_ids,
                    method_selection_rationale=plan.method_selection_rationale,
                    carried_work_order_ids=[work.id for work in unfinished
                        if work.next_owner != WorkOrderNextOwner.LEADER_REPLAN], goal_complete=False,
                ),
                context=self.get_run(run_id).context, profile=document.project.production_profile,
                execution_target=snapshot.run.execution_target,
                local_plan_completed=local_complete,
                failed_plan_state=failure_state,
            )
            # Claim before the slow call. A failed optional supplement is visible,
            # and normal completion/failure/user replanning remains available;
            # the runner must not retry this same result set on every tick/restart.
            current_run = self.runtime.store.get_run(run_id)
            assert current_run is not None
            current_run.completion_planned_work_order_ids = list(dict.fromkeys([
                *current_run.completion_planned_work_order_ids, *new_ids,
            ]))
            self.runtime.store.save_run(current_run)
            record_trace("agent.completion_planning", {
                "run_id": run_id, "status": "STARTED", "completed_work_order_ids": cast(list[JsonValue], new_ids),
                "plan_revision_id": plan.id,
                "trigger": "FAILED_PLAN" if failure_state else "LOCAL_PLAN_COMPLETED" if local_complete else "WORKER_COMPLETED",
            })
            return prepared

    def plan_after_completions(self, prepared: CompletionPlanningInput) -> AgentPlanProposal:
        """Perform only the language decision, without holding the run lock."""
        client = self._language_client_resolver()
        if client is None:
            if prepared.failed_plan_state:
                raise ProductionManagerError("Failed plan requires a Leader decision, but no language client is configured")
            return AgentPlanProposal(
                summary="Continue the existing work until its next result.", steps=[],
                carried_work_order_ids=prepared.current_plan.carried_work_order_ids,
            )
        compiled = compile_production_prompt(
            prepared.profile, roles=(ProductionSkillRole.PLANNER,),
            stage=ProductionPromptStage.ORCHESTRATION,
            method_skill_ids=_method_skill_ids_for_role(
                prepared.current_plan.selected_method_skill_ids, ProductionSkillRole.PLANNER,
            ),
        )
        planner = self._planner_factory(client)
        if isinstance(planner, LanguageProductionPlanner):
            planner.context_compiler = ModelInputCompiler(self.runtime.store)
        with trace_context(run_id=prepared.run_id, agent_role="leader", work_order_id=None):
            return planner.propose_plan(
                context=prepared.context, skill_prompt=compiled.text,
                method_skills=[_agent_method_skill_descriptor(descriptor)
                               for descriptor in production_method_skill_catalog()],
                tools=_tools_for_execution_target(self.tools.list(), prepared.execution_target),
                current_plan=prepared.current_plan,
                reason=(
                    "当前计划有实际失败，需要修订失败和未执行的部分："
                    + json.dumps(prepared.failed_plan_state, ensure_ascii=False)
                    + "。已成功及正在执行的步骤、仍在途的工单由系统保留，不能改写、重派或重复提交媒体。"
                    "失败步骤不会被继承，可修正后沿用其 step ID；尚未执行的步骤由本次计划决定是否保留。"
                    "新步骤可依赖已成功或在途的原步骤。只以当前持久结果为事实，按原目标继续。"
                    "若只能等待仍在途工单，返回 steps=[] 并保留 current_plan.carried_work_order_ids。"
                    if prepared.failed_plan_state else
                    "当前这一小段已计划工作全部执行完成，结果已经保存。结合当前上下文继续原目标，"
                    "可安排依赖这些结果的新工作，也可明确等待；其他工单仍在独立执行，必须保留其 ID，"
                    "不要重派、重提媒体或假定其已经完成。新步骤可依赖 current_plan 中已完成的步骤。"
                    "若没有独立工作，返回 steps=[] 并保留 current_plan.carried_work_order_ids。"
                    if prepared.local_plan_completed else
                    "这些 Worker 已完成并保存真实结果：" + ", ".join(prepared.completed_work_order_ids)
                    + "。其他工单仍独立继续。这次是补充规划：只在结果足以支持时追加新的就绪工作，"
                    "不要复制、替换或重新派发已有步骤，不要重提媒体任务。"
                    "已有全部步骤和在途工单由系统原样保留，新步骤可 depends_on 已有步骤 ID。"
                    "若没有实际可推进的独立工作，返回 steps=[] 并沿用 current_plan.carried_work_order_ids；"
                    "不要为了唤醒调度新增读取、检查点或角色。goal_complete=false。"
                    "只使用当前已经保存且明确可用的输入；不能把仍在制作的素材当作已完成。"
                ),
            )

    def apply_completion_plan(
        self, prepared: CompletionPlanningInput, proposal: AgentPlanProposal,
    ) -> bool:
        """Append to the unchanged plan; never cancel or replay existing work."""
        execution_boundary()
        with self._run_lock(prepared.run_id):
            self._return_leader_owned_work_orders(prepared.run_id)
            snapshot = self.runtime.snapshot(prepared.run_id)
            plan = snapshot.current_plan
            if (plan is None or plan.id != prepared.plan_revision_id
                or snapshot.run.latest_user_instructions != prepared.latest_user_instructions
                or snapshot.run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
                or any(work.active_plan_revision_id == prepared.plan_revision_id
                    and work.status in {DelegationStatus.PENDING, DelegationStatus.IN_PROGRESS, DelegationStatus.NEEDS_ATTENTION}
                    and work.next_owner == WorkOrderNextOwner.USER_INPUT
                    for work in self.runtime.store.list_work_orders(prepared.run_id))
                or self._plan_failure_state(snapshot, plan) != prepared.failed_plan_state):
                record_trace("agent.completion_planning", {"run_id": prepared.run_id,
                    "status": "SUPERSEDED", "plan_revision_id": prepared.plan_revision_id})
                return False
            protected_ids = {action.plan_step_id for action in snapshot.actions
                if PlanGraph(plan).owns_action(action) and action.status in {ActionStatus.SUCCEEDED, ActionStatus.RUNNING}}
            old_steps = {step.id: step for step in plan.steps
                if not prepared.failed_plan_state or step.id in protected_ids}
            added: list[PlanStep] = []
            for step in proposal.steps:
                if step.id in old_steps:
                    if step != old_steps[step.id]:
                        raise ProductionManagerError("Completion planning cannot rewrite an existing step: " + step.id)
                else:
                    added.append(step)
            if prepared.local_plan_completed or prepared.failed_plan_state:
                # This is the ordinary continuation after all local steps, not
                # an optional early supplement. Keep completed dependencies and
                # every still-running handoff; never replay the original steps.
                # A new empty waiting revision consumes this read-result event.
                self._replace_plan(prepared.run_id, proposal.model_copy(update={
                    "steps": added,
                    "selected_method_skill_ids": list(dict.fromkeys([
                        *plan.selected_method_skill_ids, *proposal.selected_method_skill_ids,
                    ])),
                }), append_to_plan=plan, completed_local_continuation=True,
                    failed_plan_continuation=bool(prepared.failed_plan_state))
                record_trace("agent.completion_planning", {"run_id": prepared.run_id,
                    "status": "APPLIED", "summary": proposal.summary,
                    "trigger": "FAILED_PLAN" if prepared.failed_plan_state else "LOCAL_PLAN_COMPLETED",
                    "added_step_ids": [step.id for step in added],
                    "completed_work_order_ids": cast(list[JsonValue], prepared.completed_work_order_ids)})
                return True
            if not added:
                record_trace("agent.completion_planning", {"run_id": prepared.run_id,
                    "status": "WAITING", "summary": proposal.summary,
                    "completed_work_order_ids": cast(list[JsonValue], prepared.completed_work_order_ids)})
                return False
            self._replace_plan(prepared.run_id, proposal.model_copy(update={
                "steps": added, "goal_complete": False,
                "selected_method_skill_ids": list(dict.fromkeys([
                    *plan.selected_method_skill_ids, *proposal.selected_method_skill_ids,
                ])),
            }), append_to_plan=plan)
            record_trace("agent.completion_planning", {"run_id": prepared.run_id,
                "status": "APPLIED", "summary": proposal.summary,
                "added_step_ids": [step.id for step in added],
                "completed_work_order_ids": cast(list[JsonValue], prepared.completed_work_order_ids)})
            return True

    def replan(
        self,
        run_id: str,
        request: AgentRunReplanRequest,
    ) -> AgentRunView:
        with self._run_lock(run_id):
            if request.confirmed_decisions:
                run = self.runtime.snapshot(run_id).run
                run.latest_user_instructions = list(request.confirmed_decisions)
                run.updated_at = utc_now()
                self.runtime.store.save_run(run)
                existing = {
                    decision.statement
                    for decision in self.runtime.store.list_decisions(
                        run.project_id, run_id=run_id
                    )
                    if decision.run_id == run_id
                }
                for raw_statement in request.confirmed_decisions:
                    statement = raw_statement.strip()
                    if statement and statement not in existing:
                        self.runtime.remember_decision(
                            project_id=run.project_id,
                            run_id=run_id,
                            statement=statement,
                            rationale="User-confirmed instruction supplied with external replanning.",
                        )
                        existing.add(statement)
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
                    carried_work_order_ids=snapshot.current_plan.carried_work_order_ids,
                    goal_complete=snapshot.current_plan.goal_complete,
                )
            )
            if semantic_planning_available:
                current = self._semantic_planning_current_plan(
                    agent_run_id=run_id,
                    document=document,
                    current=current,
                )
            profile = document.project.production_profile
            current_method_ids = (
                [] if current is None else current.selected_method_skill_ids
            )
            compiled = compile_production_prompt(
                profile,
                roles=(ProductionSkillRole.PLANNER,),
                stage=ProductionPromptStage.ORCHESTRATION,
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
            if isinstance(planner, LanguageProductionPlanner):
                planner.context_compiler = ModelInputCompiler(self.runtime.store)
            with trace_context(run_id=run_id, agent_role="leader", work_order_id=None):
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

    def _semantic_planning_current_plan(
        self,
        *,
        agent_run_id: str,
        document: ProjectDocument,
        current: AgentPlanProposal | None,
    ) -> AgentPlanProposal | None:
        """Give the Leader context without anchoring it to an internal wait step."""

        planning = document.planning
        planning_run = (
            None
            if planning is None or planning.run_id is None
            else self.projects.get_planning_run(planning.run_id)
        )
        if self._planning_run_awaits_story_verification_resolution(planning_run):
            # The exact Verifier payload is already present in ContextPack.  A
            # deterministic USER_INPUT placeholder here biases the semantic Leader
            # toward asking the user to perform its own planning duty.  Let the
            # Leader form the resolve_story_verification Tool call from that evidence.
            return None
        planning_stage = self._planning_precondition_plan(
            agent_run_id=agent_run_id,
            document=document,
            semantic_planning_available=False,
        )
        return planning_stage if planning_stage is not None else current

    def _planning_precondition_plan(
        self,
        *,
        agent_run_id: str,
        document: ProjectDocument,
        semantic_planning_available: bool = False,
    ) -> AgentPlanProposal | None:
        """Keep persisted long-form planning ahead of downstream production work."""

        planning = document.planning
        planning_run = (
            None
            if planning is None or planning.run_id is None
            else self.projects.get_planning_run(planning.run_id)
        )
        selected_method_skill_ids, method_selection_rationale = (
            self._planning_precondition_method_selection(
                agent_run_id=agent_run_id,
                document=document,
            )
        )
        if (
            planning is not None
            and planning.status == PlanningRunStatus.COMPLETE
            and planning.owner_agent_run_id == agent_run_id
            and planning_run is not None
            and planning_run.operation == PlanningRunOperation.INITIAL
            and document.project.content_planning_output
            == ContentPlanningOutput.SCRIPT_ONLY
            and document.project.stage == ProjectStage.SCRIPT_READY
        ):
            return AgentPlanProposal(
                summary=(
                    "The owned content-planning job completed the requested "
                    "script-only deliverable."
                ),
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

        planning_run = self.projects.get_planning_run(planning.run_id)
        if self._planning_run_awaits_story_verification_resolution(planning_run):
            if semantic_planning_available:
                return None
            return AgentPlanProposal(
                summary=(
                    "A persisted story verifier result requires an explicit semantic "
                    "decision from the owning AgentRun Leader."
                ),
                steps=[
                    PlanStep(
                        id="resolve-story-verification-handoff",
                        title="Resolve the story verifier handoff",
                        kind=ActionKind.USER_INPUT,
                        assigned_agent="production-manager",
                        instructions=(
                            "Read the exact content_planning_job verifier payload, then "
                            "submit a plan that calls planning.resolve_story_verification "
                            "with a reasoned decision. Do not advance the same verifier again."
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
    def _planning_duration_fit_requires_resolution(
        planning_run: PlanningRunRecord,
        assessment: DurationFitAssessment | None,
    ) -> bool:
        if assessment is None:
            return False
        if assessment.alignment == DurationAlignment.UNRESOLVED:
            return True
        if assessment.alignment != DurationAlignment.OUTSIDE_ACCEPTED_RANGE:
            return False
        story_intent = planning_run.story_intent
        contract = None if story_intent is None else story_intent.duration_contract
        return (
            contract is not None
            and contract.authority == DurationAuthority.TARGET_LED
        )

    @staticmethod
    def _planning_run_awaits_story_verification_resolution(
        planning_run: PlanningRunRecord | None,
    ) -> bool:
        if (
            planning_run is None
            or planning_run.status != PlanningRunStatus.NEEDS_ATTENTION
        ):
            return False
        if planning_run.current_step in {
            PlanningRunStep.SCRIPT_VERIFIER,
            PlanningRunStep.SCRIPT_CRITIC,
        }:
            report = planning_run.review_report
            return (
                report is not None
                and (
                    report.requires_revision
                    or ProductionManager._planning_duration_fit_requires_resolution(
                        planning_run,
                        report.duration_fit_assessment,
                    )
                )
            )
        verification = planning_run.latest_revision_verification
        return (
            planning_run.current_step == PlanningRunStep.REVISION_VERIFIER
            and verification is not None
            and (
                not verification.accepted
                or ProductionManager._planning_duration_fit_requires_resolution(
                    planning_run,
                    verification.duration_fit_assessment,
                )
            )
        )

    def _is_content_planning_plan(
        self,
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
        planning_run = self.projects.get_planning_run(planning.run_id)
        expected_tool = (
            "planning.resolve_story_verification"
            if self._planning_run_awaits_story_verification_resolution(planning_run)
            else "planning.advance"
        )
        for step in proposal.steps:
            if step.kind == ActionKind.USER_INPUT:
                continue
            if step.kind != ActionKind.TOOL_CALL or step.tool_name != expected_tool:
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
        *, allow_replanning: bool = True,
    ) -> AgentAdvanceResponse:
        with self._run_lock(run_id):
            return self._advance_unlocked(run_id, request, allow_replanning=allow_replanning)

    def _advance_unlocked(
        self,
        run_id: str,
        request: AgentRunAdvanceRequest,
        *, allow_replanning: bool = True,
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
            execution_boundary()
            current = self.runtime.snapshot(run_id)
            plan = current.current_plan
            if plan is None:
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                break

            failed_actions = self._failed_actions_for_plan(current, plan.id)
            if failed_actions:
                if not allow_replanning:
                    break
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
                if not allow_replanning:
                    break
                details = [
                    f"{work_order.id}={work_order.status.value}, next_owner={work_order.next_owner}"
                    for work_order in carried_failed
                ]
                details.extend(
                    f"{work_order_id}=missing-or-detached"
                    for work_order_id in invalid_carried_ids
                )
                reason = (
                    "Revise the plan after carried WorkOrders returned control or stopped: "
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
                if request.review_blocked_step_ids is not None and running_planning_stage[1].id in request.review_blocked_step_ids:
                    self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                    break
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
            if request.review_blocked_step_ids is not None:
                ready = [step for step in ready if step.id not in request.review_blocked_step_ids]
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

            if request.review_blocked_step_ids is not None:
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                notes.append("Affected work is waiting for the user's requested text review; independent work may continue")
                break

            local_plan_complete = PlanGraph(plan).is_complete(current.actions)
            if carried_waiting and (
                plan.goal_complete or not plan.steps or not local_plan_complete
            ):
                self.runtime.set_run_status(run_id, AgentRunStatus.WAITING)
                notes.append(
                    "Waiting for carried WorkOrders in the current plan: "
                    + ", ".join(work_order.id for work_order in carried_waiting)
                )
                break

            # New results from a nonempty local plan go back to the Leader even
            # while retained work continues. The Leader can add independent
            # work or explicitly wait with an empty plan that carries it.
            if local_plan_complete:
                if not allow_replanning:
                    break
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
                and PlanGraph(plan).owns_action(action)
                for action in current.actions
            ):
                notes.append("Waiting for delegated Agent or user input")
            else:
                notes.append("The current plan has no runnable step and needs revision")
            break

        view = self.get_run(run_id)
        active_blockers = [
            action.error_message
            for action in view.snapshot.actions
            if action.status == ActionStatus.FAILED
            and view.snapshot.current_plan is not None
            and PlanGraph(view.snapshot.current_plan).owns_action(action)
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
                and view.snapshot.current_plan is not None
                and PlanGraph(view.snapshot.current_plan).owns_action(action)
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
                not PlanGraph(plan).owns_action(action)
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
            if PlanGraph(plan).owns_action(action)
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
        plan = snapshot.current_plan
        return [
            action
            for action in snapshot.actions
            if (PlanGraph(plan).owns_action(action) if plan is not None and plan.id == plan_revision_id
                else action.plan_revision_id == plan_revision_id)
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
            if work_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN:
                # The action can belong to the previous plan revision. Its
                # explicit owner transfer must still wake the current Leader.
                failed.append(work_order)
            elif work_order.status in {
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

    def _plan_failure_state(self, snapshot: AgentRunSnapshot, plan: PlanRevision) -> dict[str, JsonValue]:
        """Capture only actionable failures; intermediate Worker progress is not a replan event."""
        actions = self._failed_actions_for_plan(snapshot, plan.id)
        _waiting, carried_failed, invalid_ids = self._carried_work_order_state(plan)
        if not actions and not carried_failed and not invalid_ids:
            return {}
        return {
            "actions": [{"id": action.id, "step_id": action.plan_step_id,
                "error_kind": action.error_kind, "error_message": action.error_message,
                "summary": action.output_summary} for action in actions],
            "carried_work_orders": [{"id": work.id, "status": work.status.value,
                "next_owner": work.next_owner.value if work.next_owner is not None else None}
                for work in carried_failed],
            "missing_carried_work_order_ids": list(invalid_ids),
        }

    def _automatic_replan_unlocked(
        self,
        run_id: str,
        reason: str,
        notes: list[str],
    ) -> bool:
        snapshot = self.runtime.snapshot(run_id)
        blockers = [
            action.error_message
            for action in snapshot.actions
            if action.status == ActionStatus.FAILED
            and snapshot.current_plan is not None
            and PlanGraph(snapshot.current_plan).owns_action(action)
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
            record_trace("agent.replan_failed", {"run_id": run_id, "error": str(exc)})
            self.runtime.checkpoint(
                run_id,
                current_focus=reason,
                blockers=[*blockers, f"Automatic replanning failed: {exc}"][-4:],
                next_actions=["Revise the plan or configure the planning Agent"],
            )
            notes.append(f"Automatic replanning failed: {exc}")
            if is_language_transport_error(exc):
                # Preserve the failure type for the product runner's resumable
                # language retry, including replanning between completed stages.
                raise
            return False
        replanned = self.runtime.snapshot(run_id)
        replanned_plan = replanned.current_plan
        if (
            replanned_plan is None
            or (
                not replanned_plan.goal_complete
                and not self.runtime.ready_steps(run_id)
                and not self._carried_work_order_state(replanned_plan)[0]
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
            stage=work_order.production_prompt_stage,
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
            completion_receipts = (
                verify_completion_claims(self.projects, project_id=run.project_id,
                    artifact_refs=[*request.created_artifacts, *request.updated_artifacts],
                    expected_output_slot_ids=work_order.expected_output_slot_ids,
                    deliverable=request.deliverable)
                if request.status == AgentResultStatus.COMPLETED else []
            )
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
                completion_receipts=completion_receipts,
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
        handoff_refs = _artifact_write_handoff_refs(
            [*request.created_artifacts, *request.updated_artifacts]
        )
        if action is not None:
            if request.status == AgentResultStatus.COMPLETED:
                self.runtime.complete_action(
                    action.id,
                    output_summary=request.concise_summary,
                    artifact_refs=handoff_refs,
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
                artifact_refs=handoff_refs,
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
                artifact_refs=handoff_refs,
            )
        elif request.status == AgentResultStatus.PARTIAL:
            next_owner = request.next_owner or WorkOrderNextOwner.LEADER_REPLAN
            self.runtime.checkpoint(
                run.id,
                current_focus=request.concise_summary,
                open_questions=request.unresolved_questions,
                next_actions=request.recommended_next_actions,
                artifact_refs=handoff_refs,
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
                artifact_refs=handoff_refs,
            )
        return AgentWorkOrderResultResponse(result=result, run=self.get_run(run.id))

    def execute_work_order(self, work_order_id: str) -> AgentWorkOrderResultResponse:
        execution_boundary()
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
        try:
            with self._run_lock(work_order.parent_run_id):
                self.runtime.begin_work_order(work_order_id)
                view = self.get_work_order(work_order_id)
                parent_run = self.runtime.snapshot(work_order.parent_run_id).run
            agent = self._specialist_agent_factory(client)
            if isinstance(agent, LanguageSpecialistAgent):
                agent.context_compiler = ModelInputCompiler(self.runtime.store)
                agent.bind_tools(
                    self.tools,
                    descriptors=_tools_for_execution_target(
                        self.tools.list(),
                        parent_run.execution_target,
                    ),
                )
            with trace_context(run_id=work_order.parent_run_id, work_order_id=work_order_id,
                               agent_role=current.recipient_role):
                decision = agent.execute(view)
        except ExecutionYielded:
            with self._run_lock(work_order.parent_run_id):
                interrupted = self.runtime.store.get_work_order(work_order_id)
                if interrupted is not None and interrupted.status == DelegationStatus.IN_PROGRESS:
                    interrupted.status = DelegationStatus.NEEDS_ATTENTION
                    interrupted.next_owner = WorkOrderNextOwner.WORKER_DECISION
                    interrupted.updated_at = utc_now()
                    self.runtime.store.save_work_order(interrupted)
            raise
        except SpecialistResponseProtocolError as exc:
            next_owner = self._return_specialist_protocol_failure_to_worker(
                work_order_id,
                exc,
            )
            repeated_without_progress = (
                next_owner == WorkOrderNextOwner.LEADER_REPLAN
            )
            return self.submit_work_order_result(
                work_order_id,
                AgentWorkOrderResultRequest(
                    status=AgentResultStatus.PARTIAL,
                    concise_summary=(
                        "专业 Agent 在收到类型化协议反馈后仍返回"
                        "语义等价的非法结构；已保留所有落盘内容并"
                        "交回 Leader 重新划分剩余修复范围。"
                        if repeated_without_progress
                        else (
                            "专业 Agent 的响应结构未通过协议校验，"
                            "已保留同一 WorkOrder 和已完成内容，由原 "
                            "Worker 依据类型化反馈修正交接结构。"
                        )
                    ),
                    findings=[
                        (
                            "同一 Worker 未对已给出的协议问题产生实质修正；"
                            "当前交付形式或语义范围需要由 Leader 重组。"
                            if repeated_without_progress
                            else (
                                "这是首次响应协议问题，不改变创意范围或"
                                "调度计划。"
                            )
                        )
                    ],
                    recommended_next_actions=[
                        (
                            "Leader 保留已持久化工件，按自然语义边界收缩"
                            "或重组剩余 WorkOrder，不要原样重发。"
                            if repeated_without_progress
                            else (
                                "让同一专业 Agent 修正已持久化的结构化响应。"
                            )
                        )
                    ],
                    next_owner=next_owner,
                ),
            )
        except Exception as exc:
            self._return_specialist_failure(work_order_id, exc)
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
                if decision.working_notes is not None:
                    current.working_notes = decision.working_notes
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
            self._return_specialist_failure(work_order_id, exc)
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
    ) -> WorkOrderNextOwner:
        """Persist feedback and return stalled protocol work to the Leader."""

        current = self.runtime.store.get_work_order(work_order_id)
        if current is None:
            return WorkOrderNextOwner.LEADER_REPLAN
        with self._run_lock(current.parent_run_id):
            failed = self.runtime.store.get_work_order(work_order_id)
            if failed is None or failed.status in {
                DelegationStatus.COMPLETED,
                DelegationStatus.FAILED,
                DelegationStatus.CANCELLED,
            }:
                return WorkOrderNextOwner.LEADER_REPLAN
            prior_feedback = failed.pending_protocol_feedback
            repeated_without_progress = (
                prior_feedback is not None
                and _specialist_protocol_feedback_signature(prior_feedback)
                == _specialist_protocol_feedback_signature(error.feedback)
            )
            next_owner = (
                WorkOrderNextOwner.LEADER_REPLAN
                if repeated_without_progress
                else WorkOrderNextOwner.WORKER_DECISION
            )
            failed.pending_protocol_feedback = error.feedback
            failed.status = DelegationStatus.NEEDS_ATTENTION
            failed.next_owner = next_owner
            failed.updated_at = utc_now()
            self.runtime.store.save_work_order(failed)
            self.runtime.append_work_order_progress(
                failed.id,
                author="specialist-response-validator",
                summary=(
                    "The same Worker repeated a semantically equivalent invalid "
                    "response after typed protocol feedback. Persisted artifacts "
                    "were preserved and the remaining work returned to the Leader "
                    "for a smaller or simpler semantic handoff."
                    if repeated_without_progress
                    else (
                        "The Worker response did not match a supported JSON contract. "
                        "Typed validation feedback was persisted for correction by the "
                        "same Worker; no creative scope or plan revision changed."
                    )
                ),
            )
            self.runtime.set_run_status(
                failed.parent_run_id,
                AgentRunStatus.WAITING,
            )
            self.runtime.checkpoint(
                failed.parent_run_id,
                current_focus=(
                    "The Worker made no semantic protocol progress; the Leader must "
                    "preserve landed artifacts and reshape the remaining WorkOrder."
                    if repeated_without_progress
                    else (
                        "The same specialist must correct its persisted response "
                        "protocol feedback."
                    )
                ),
                blockers=[],
                next_actions=[
                    "Let the Leader shrink or simplify the remaining semantic repair."
                    if repeated_without_progress
                    else "Let the same specialist correct the structured response."
                ],
            )
            return next_owner

    def _return_specialist_failure(
        self,
        work_order_id: str,
        error: Exception,
    ) -> None:
        """Keep interrupted inference with its Worker; return semantic failures."""

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
            transport_interrupted = isinstance(error, LanguageProviderTransportError)
            failed.status = DelegationStatus.NEEDS_ATTENTION
            failed.next_owner = (
                WorkOrderNextOwner.WORKER_DECISION
                if transport_interrupted
                else WorkOrderNextOwner.LEADER_REPLAN
            )
            failed.updated_at = utc_now()
            self.runtime.store.save_work_order(failed)
            self.runtime.append_work_order_progress(
                failed.id,
                author="production-manager",
                summary=(
                    (
                        "Specialist transport interrupted; resume the same WorkOrder "
                        "from its persisted Tool results without replaying completed Tools: "
                        if transport_interrupted
                        else "Specialist decision failed and returned to Leader: "
                    )
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
                next_actions=[
                    "Resume the same specialist decision from persisted Tool results."
                    if transport_interrupted
                    else "Let the Leader revise the active plan."
                ],
            )

    def _execute_pending_specialist_tool(
        self,
        work_order_id: str,
        turn_id: str,
    ) -> AgentWorkOrderResultResponse:
        # Yield while the persisted request is still REQUESTED. Once marked
        # IN_PROGRESS, this operation owns its completion and durable receipt.
        execution_boundary()
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
                work_order_id=work_order_id,
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
        persisted_result = _specialist_tool_result_for_persistence(
            tool_name,
            serialized_result,
            arguments=turn.arguments,
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
            turn.result = persisted_result
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
        run.latest_user_instructions = [request.answer]
        run.updated_at = utc_now()
        self.runtime.store.save_run(run)
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
                "User preferred production methods (empty means Agent decides): " + ", ".join(document.project.preferred_method_skill_ids),
                "User creative direction: " + document.project.creative_direction,
            ],
            artifact_refs=_project_refs(document),
        )
        self.runtime.store.save_project_memory(memory)
        return memory

    def _replace_plan(
        self, run_id: str, proposal: AgentPlanProposal, *, append_to_plan: PlanRevision | None = None,
        completed_local_continuation: bool = False,
        failed_plan_continuation: bool = False,
    ) -> None:
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
        try:
            plan_ref_entries = _plan_artifact_ref_entries(proposal)
            plan_attempt_ref_entries = _plan_tool_attempt_ref_entries(proposal)
        except ProductionManagerError as exc:
            raise ProductionManagerError(
                "Agent plan contains invalid delegation context references"
            ) from exc
        outside_project = [
            (path, ref)
            for path, ref in plan_ref_entries
            if ref.project_id != run.project_id
        ]
        if outside_project:
            path, _ref = outside_project[0]
            raise ProductionManagerError(
                f"Agent plan artifact at {path} belongs outside the run project"
            )
        # Bootstrap plans intentionally reference the preallocated project before
        # project.create has persisted it. Once the canonical project exists, every
        # context-manifest ref must resolve by its full identity before any plan,
        # Action, or WorkOrder is stored.
        if self.projects.get_project(run.project_id) is not None:
            unresolved_refs = self.runtime.unresolved_artifact_refs(
                run_id,
                [ref for _path, ref in plan_ref_entries],
            )
            if unresolved_refs:
                unresolved_identities = ", ".join(
                    f"{ref.artifact_type}:{ref.artifact_id}@{ref.version}"
                    for ref in unresolved_refs
                )
                raise ProductionManagerError(
                    "Agent plan contains unresolved exact ArtifactRefs: "
                    + unresolved_identities
                )
        for path, ref in plan_attempt_ref_entries:
            source_work_order = self.runtime.store.get_work_order(ref.work_order_id)
            source_turn = (
                None
                if source_work_order is None
                else next(
                    (
                        item
                        for item in source_work_order.specialist_turns
                        if item.id == ref.attempt_id
                    ),
                    None,
                )
            )
            if (
                source_work_order is None
                or source_work_order.parent_run_id != run.id
                or ref.agent_run_id != run.id
                or ref.project_id != run.project_id
                or source_turn is None
                or not _tool_attempt_ref_matches_turn(
                    run.project_id,
                    source_work_order,
                    source_turn,
                    ref,
                )
            ):
                raise ProductionManagerError(
                    f"Agent plan contains an unresolved exact ToolAttemptRef at {path}"
                )
        for step in proposal.steps:
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
        requested_carried_ids = (
            [work_order.id for work_order in unfinished]
            if append_to_plan is not None else proposal.carried_work_order_ids
        )
        unknown_carried = [
            work_order_id
            for work_order_id in requested_carried_ids
            if work_order_id not in unfinished_ids
        ]
        if unknown_carried:
            raise ProductionManagerError(
                "Plan can only carry unfinished WorkOrders from the active revision: "
                + ", ".join(unknown_carried)
            )
        leader_returned_ids = {
            work_order.id
            for work_order in unfinished
            if work_order.next_owner == WorkOrderNextOwner.LEADER_REPLAN
        }
        effective_carried_ids = [
            work_order_id
            for work_order_id in requested_carried_ids
            if work_order_id not in leader_returned_ids
        ]
        if append_to_plan is not None and leader_returned_ids and not failed_plan_continuation:
            raise ProductionManagerError("A Worker returned control while planning; preserve it for normal Leader replanning")
        retained_steps = [] if append_to_plan is None else append_to_plan.steps
        if failed_plan_continuation and append_to_plan is not None:
            protected_ids = {action.plan_step_id for action in actions
                if PlanGraph(append_to_plan).owns_action(action)
                and action.status in {ActionStatus.SUCCEEDED, ActionStatus.RUNNING}}
            retained_steps = [step for step in retained_steps if step.id in protected_ids]
        retain_old_steps = append_to_plan is not None and (
            bool(proposal.steps) or not completed_local_continuation
        )
        inherited_action_ids = (
            [action.id for action in actions if PlanGraph(append_to_plan).owns_action(action)
             and action.plan_step_id in {step.id for step in retained_steps}
             and (not failed_plan_continuation or action.status in {ActionStatus.SUCCEEDED, ActionStatus.RUNNING})]
            if append_to_plan is not None and retain_old_steps else []
        )
        # A carried Worker can finish after the Leader's input snapshot. Move
        # that still-unconsumed completion into the new plan in the same commit,
        # so an empty waiting proposal cannot strand its real result behind the
        # previous revision. Do not mark this unseen completion as consumed.
        unconsumed_completed = [
            work for work in self.runtime.store.list_work_orders(run_id)
            if append_to_plan is not None and work.active_plan_revision_id == previous_plan_id
            and work.status == DelegationStatus.COMPLETED
            and work.id not in run.completion_planned_work_order_ids
            and work.superseded_by_plan_revision_id is None
        ]
        if append_to_plan is None:
            # Ordinary planning also consumes the completed results it saw.
            # Later scheduler ticks must not ask about the same handoff again.
            run.completion_planned_work_order_ids = list(dict.fromkeys([
                *run.completion_planned_work_order_ids,
                *[work.id for work in self.runtime.store.list_work_orders(run_id)
                  if work.status == DelegationStatus.COMPLETED],
            ]))
            self.runtime.store.save_run(run)
        next_plan = self.runtime.replace_plan(
            run_id,
            summary=proposal.summary,
            steps=([*retained_steps, *proposal.steps]
                   if append_to_plan is not None and retain_old_steps else proposal.steps),
            selected_method_skill_ids=proposal.selected_method_skill_ids,
            method_selection_rationale=proposal.method_selection_rationale,
            carried_work_order_ids=effective_carried_ids,
            goal_complete=proposal.goal_complete,
            inherited_action_ids=inherited_action_ids,
            updated_work_orders=[*unfinished, *unconsumed_completed] if append_to_plan is not None else (),
        )
        if proposal.goal_summary is not None:
            updated_run = self.runtime.store.get_run(run_id)
            if updated_run is not None:
                updated_run.goal_summary = proposal.goal_summary
                self.runtime.store.save_run(updated_run)
        carried_ids = set(effective_carried_ids)
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

    def _canonical_work_order_refs(self, run: AgentRun, refs: Sequence[ArtifactRef]) -> list[ArtifactRef]:
        """Complete immutable version UUIDs, without selecting an asset's current media."""
        if not any(ref.artifact_type == "asset_version" for ref in refs):
            return list(refs)
        document = self._require_project(run.project_id)
        versions = {version.id: version.version for asset in document.assets for version in asset.versions}
        canonical = [ref.model_copy(update={"version": versions[ref.artifact_id]})
            if ref.project_id == run.project_id and ref.artifact_type == "asset_version"
            and ref.version is None and ref.artifact_id in versions else ref for ref in refs]
        unresolved = self.runtime.unresolved_artifact_refs(run.id,
            [ref for ref in canonical if ref.artifact_type == "asset_version"])
        if unresolved:
            raise ProductionManagerError("WorkOrder contains unresolved exact AssetVersion references: "
                + ", ".join(f"{ref.artifact_id}@{ref.version}" for ref in unresolved))
        return canonical

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
            persist_leader_tool_result(self.runtime.store, action,
                arguments=step.arguments, result=result.model_dump(mode="json"))
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
                        output_summary=json.dumps(result.error.model_dump(mode="json"), ensure_ascii=False) if result.error else message,
                        error_kind=(
                            result.error.kind
                            if result.error is not None
                            else result.status.value
                        ),
                    ),
                    None,
                    "Tool failure recorded; the Agent can revise the plan",
                )
            summary = json.dumps(result.receipt.model_dump(mode="json") if result.receipt else result.output or {}, ensure_ascii=False)
            selected_editor_read = step.tool_name == "timeline.read_editorial" and bool(
                step.arguments.get("subtitle_ids") or step.arguments.get("clip_ids"))
            # An exact read already excludes unrelated editor content. Preserve
            # the requested rows and coordinate mapping for the next decision.
            if len(summary) > 1600 and not selected_editor_read:
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
            production_prompt_stage = step.production_prompt_stage
            if production_prompt_stage is None and step.production_skill_role in {
                ProductionSkillRole.ASSET_PLANNER,
                ProductionSkillRole.ASSET_BUILDER,
                ProductionSkillRole.ASSET_VERIFIER,
                ProductionSkillRole.ASSET_REVIEWER,
            }:
                production_prompt_stage = ProductionPromptStage.ASSETS
            try:
                run = self.runtime.store.get_run(run_id)
                assert run is not None
                scope = self._canonical_work_order_refs(run, step.artifact_refs)
                boundaries = [boundary.model_copy(update={
                    "artifact_refs": self._canonical_work_order_refs(run, boundary.artifact_refs),
                }) for boundary in _context_boundaries_argument(arguments)]
                work_order = self.runtime.create_work_order(
                    run_id,
                    sender="production-manager",
                    recipient_role=step.assigned_agent,
                    production_skill_role=step.production_skill_role,
                    production_prompt_stage=production_prompt_stage,
                    objective=step.instructions,
                    why_now=_string_argument(arguments, "why_now", step.title),
                    task_inputs=_delegation_task_inputs(arguments),
                    scope=scope,
                    tool_attempt_refs=_tool_attempt_refs_argument(arguments),
                    constraints=_string_list_argument(arguments, "constraints"),
                    confirmed_decisions=_string_list_argument(
                        arguments, "confirmed_decisions"
                    ),
                    inherited_decision_ids=_string_list_argument(
                        arguments, "inherited_decision_ids"
                    ),
                    adjacent_boundaries=boundaries,
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
        work_order_id: str | None = None,
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

        if tool_name == "planning.resolve_story_verification":
            run_id = arguments.get("run_id")
            requested_owner_agent_run_id = arguments.get("owner_agent_run_id")
            rationale = arguments.get("rationale")
            document = self.projects.get_project(project_id)
            planning = None if document is None else document.planning
            planning_run = (
                self.projects.get_planning_run(run_id)
                if isinstance(run_id, str)
                else None
            )
            if (
                planning is None
                or planning.run_id != run_id
                or planning.owner_agent_run_id != agent_run_id
                or planning_run is None
                or planning_run.owner_agent_run_id != agent_run_id
                or not self._planning_run_awaits_story_verification_resolution(
                    planning_run
                )
            ):
                raise ProductionManagerError(
                    "planning.resolve_story_verification requires the current "
                    "project's pending verifier handoff"
                )
            if requested_owner_agent_run_id != agent_run_id:
                raise ProductionManagerError(
                    "planning.resolve_story_verification owner_agent_run_id must "
                    "match the current Agent run"
                )
            try:
                StoryVerificationResolutionDecision(arguments.get("decision"))
            except (TypeError, ValueError) as exc:
                raise ProductionManagerError(
                    "planning.resolve_story_verification requires a valid decision"
                ) from exc
            if not isinstance(rationale, str) or not rationale.strip():
                raise ProductionManagerError(
                    "planning.resolve_story_verification requires the Leader's "
                    "semantic rationale"
                )

        if tool_name == "planning.start_script_revision":
            document = self.projects.get_project(project_id)
            requested_owner_agent_run_id = arguments.get("owner_agent_run_id")
            base_script_version_id = arguments.get("base_script_version_id")
            instruction = arguments.get("instruction")
            raw_revision_origin = arguments.get(
                "revision_origin",
                ScriptRevisionOrigin.USER_FEEDBACK.value,
            )
            try:
                revision_origin = ScriptRevisionOrigin(raw_revision_origin)
            except (TypeError, ValueError) as exc:
                raise ProductionManagerError(
                    "planning.start_script_revision requires a valid revision_origin"
                ) from exc
            raw_evidence_refs = arguments.get("evidence_refs", [])
            if not isinstance(raw_evidence_refs, list):
                raise ProductionManagerError(
                    "planning.start_script_revision evidence_refs must be a list"
                )
            try:
                revision_evidence_refs = [
                    ProductionDocumentArtifactRef.model_validate(item)
                    for item in cast(list[object], raw_evidence_refs)
                ]
            except ValueError as exc:
                raise ProductionManagerError(
                    "planning.start_script_revision evidence_refs are invalid"
                ) from exc
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
                    "planning.start_script_revision requires the complete revision instruction"
                )
            resolver = ProjectArtifactResolver(self.projects)
            for evidence_ref in revision_evidence_refs:
                if evidence_ref.project_id != project_id:
                    raise ProductionManagerError(
                        "planning.start_script_revision evidence must belong to the current project"
                    )
                if evidence_ref.version is None:
                    raise ProductionManagerError(
                        "planning.start_script_revision evidence must pin exact versions"
                    )
                if resolver.summarize_artifact(
                    ArtifactRef.model_validate(evidence_ref.model_dump(mode="json"))
                ) is None:
                    raise ProductionManagerError(
                        "planning.start_script_revision evidence must resolve exactly"
                    )
            if (
                revision_origin == ScriptRevisionOrigin.PRODUCTION_CONSTRAINT
                and not revision_evidence_refs
            ):
                raise ProductionManagerError(
                    "A production-constraint script revision requires exact evidence_refs"
                )

        if tool_name == "agent.read_context":
            if arguments.get("project_id") != project_id or arguments.get("run_id") != agent_run_id:
                raise ProductionManagerError("agent.read_context must address the current project and run")
        if tool_name in {
            "agent.get_tool_attempt",
            "artifact.retry_asset_plan_attempt",
        }:
            try:
                attempt_ref = ToolAttemptRef.model_validate(
                    arguments.get("attempt_ref")
                )
            except ValueError as exc:
                raise ProductionManagerError(
                    f"{tool_name} requires one exact ToolAttemptRef"
                ) from exc
            ref = attempt_ref
            source_work_order = self.runtime.store.get_work_order(ref.work_order_id)
            leader_read = (
                work_order_id is None
                and tool_name == "agent.get_tool_attempt"
                and agent_run_id is not None
                and ref.project_id == project_id
                and ref.agent_run_id == agent_run_id
                and source_work_order is not None
                and source_work_order.parent_run_id == agent_run_id
                and _work_order_owns_tool_attempt_ref(project_id, source_work_order, ref)
            )
            current_work_order = (
                None
                if work_order_id is None
                else self.runtime.store.get_work_order(work_order_id)
            )
            self_owned_ref = (
                False
                if current_work_order is None
                else _work_order_owns_tool_attempt_ref(
                    project_id,
                    current_work_order,
                    ref,
                )
            )
            if not leader_read and (
                agent_run_id is None
                or current_work_order is None
                or current_work_order.parent_run_id != agent_run_id
                or ref.project_id != project_id
                or ref.agent_run_id != agent_run_id
                or (
                    not any(
                        _same_tool_attempt_locator(handed_ref, ref)
                        for handed_ref in current_work_order.tool_attempt_refs
                    )
                    and not self_owned_ref
                )
            ):
                raise ProductionManagerError(
                    f"{tool_name} can only use a ToolAttemptRef explicitly handed "
                    "to the current WorkOrder or produced by it"
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
        work_order_id: str | None = None,
    ) -> ToolResult:
        self._validate_tool_scope(
            project_id,
            tool_name,
            arguments,
            agent_run_id=agent_run_id,
            work_order_id=work_order_id,
        )
        descriptor = self.tools.describe(tool_name)
        # Pause/steering takes ownership between Tools. A composite Tool may
        # persist a partial operation before making a nested model call; yielding
        # inside it would strand IN_PROGRESS or tempt a duplicate external write.
        with execution_control(lambda: None):
            if descriptor.effect == ToolEffect.WRITE:
                # Slow external operations synchronize their own short saves.
                with self._project_tool_lock(project_id):
                    result = self.tools.invoke(tool_name, arguments)
            else:
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
                interrupted_tool = False
                for turn in work_order.specialist_turns:
                    if turn.status != SpecialistTurnStatus.IN_PROGRESS:
                        continue
                    interrupted_tool = True
                    turn.status = SpecialistTurnStatus.INTERRUPTED
                    turn.result = _interrupted_tool_result(turn)
                    turn.updated_at = utc_now()
                work_order.status = DelegationStatus.NEEDS_ATTENTION
                if work_order.next_owner not in {
                    WorkOrderNextOwner.LEADER_REPLAN, WorkOrderNextOwner.USER_INPUT,
                }:
                    work_order.next_owner = (
                        WorkOrderNextOwner.WORKER_TOOL
                        if not interrupted_tool and any(
                            turn.status == SpecialistTurnStatus.REQUESTED
                            for turn in work_order.specialist_turns
                        ) else WorkOrderNextOwner.WORKER_DECISION
                    )
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
                        try:
                            action_revision_origin = ScriptRevisionOrigin(
                                arguments.get(
                                    "revision_origin",
                                    ScriptRevisionOrigin.USER_FEEDBACK.value,
                                )
                            )
                        except (TypeError, ValueError):
                            action_revision_origin = None
                        raw_evidence_refs = arguments.get("evidence_refs", [])
                        action_evidence_valid = isinstance(raw_evidence_refs, list)
                        try:
                            action_evidence_refs = (
                                [
                                    ProductionDocumentArtifactRef.model_validate(item)
                                    for item in cast(list[object], raw_evidence_refs)
                                ]
                                if isinstance(raw_evidence_refs, list)
                                else []
                            )
                        except ValueError:
                            action_evidence_refs = []
                            action_evidence_valid = False
                        action_evidence_keys = {
                            (
                                item.project_id,
                                item.artifact_type,
                                item.artifact_id,
                                item.version,
                            )
                            for item in action_evidence_refs
                        }
                        persisted_evidence_keys = {
                            (
                                item.project_id,
                                item.artifact_type,
                                item.artifact_id,
                                item.version,
                            )
                            for item in (
                                []
                                if planning_run is None
                                else planning_run.revision_evidence_refs
                            )
                        }
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
                            and action_revision_origin
                            == planning_run.revision_origin
                            and action_evidence_valid
                            and action_evidence_keys == persisted_evidence_keys
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
                        action.tool_name == "planning.resolve_story_verification"
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
                            != "planning.resolve_story_verification"
                            else action_step.arguments
                        )
                        try:
                            action_decision = StoryVerificationResolutionDecision(
                                arguments.get("decision")
                            )
                        except (TypeError, ValueError):
                            action_decision = None
                        rationale = arguments.get("rationale")
                        latest_resolution = (
                            planning_run.story_verification_resolutions[-1]
                            if planning_run is not None
                            and planning_run.story_verification_resolutions
                            else None
                        )
                        if (
                            planning_run is not None
                            and planning_run.project_id == run.project_id
                            and planning_run.owner_agent_run_id == run.id
                            and arguments.get("project_id") == run.project_id
                            and arguments.get("run_id") == planning_run.id
                            and arguments.get("owner_agent_run_id") == run.id
                            and isinstance(rationale, str)
                            and latest_resolution is not None
                            and latest_resolution.owner_agent_run_id == run.id
                            and latest_resolution.decision == action_decision
                            and latest_resolution.rationale == rationale.strip()
                            and not self._planning_run_awaits_story_verification_resolution(
                                planning_run
                            )
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
                                    "Reconciled planning.resolve_story_verification "
                                    "with the persisted Leader decision after restart."
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

        recovery_started_at = utc_now()
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

        # save_agent_result updates the WorkOrder in the same transaction. Later
        # decisions, cancellation or interrupted-Tool normalization can advance
        # that state without another result. A receipt may finish its Action,
        # but must never replay its older status/owner over the current work.
        if linked.status == DelegationStatus.CANCELLED:
            self.runtime.finish_action(
                action.id,
                status=ActionStatus.CANCELLED,
                output_summary="The persisted WorkOrder was cancelled before restart.",
            )
            return None

        result = self.runtime.store.get_agent_result(linked.id)
        if result is not None and result.stale_handoff:
            run = self.runtime.store.get_run(action.run_id)
            if run is not None and self._work_order_stale_reason(
                linked, run.current_plan_revision_id,
            ) is not None:
                self.runtime.finish_action(
                    action.id,
                    status=ActionStatus.CANCELLED,
                    output_summary=(
                        "Recovered a stale historical handoff without merging it into "
                        "the active plan."
                    ),
                )
                return None
            result = None
        if result is not None:
            refs = [*result.created_artifacts, *result.updated_artifacts]
            if (linked.status == DelegationStatus.COMPLETED
                    and result.status == AgentResultStatus.COMPLETED):
                self.runtime.complete_action(
                    action.id,
                    output_summary=result.concise_summary,
                    artifact_refs=refs,
                )
                return None
            if (linked.status == DelegationStatus.FAILED
                    and result.status == AgentResultStatus.FAILED):
                self.runtime.fail_action(
                    action.id,
                    error_message=result.concise_summary,
                    output_summary=result.concise_summary,
                    error_kind="RecoveredAgentResultFailure",
                )
                return "A persisted delegated result failed and needs replanning."

        if linked.status == DelegationStatus.COMPLETED:
            self.runtime.fail_action(
                action.id,
                error_message=(
                    "WorkOrder was marked completed without a matching completed AgentResult; "
                    "manual reconciliation is required."
                ),
                error_kind="MissingDelegationResult",
            )
            return "A completed WorkOrder is missing its matching completed AgentResult."
        if linked.status == DelegationStatus.FAILED:
            self.runtime.fail_action(
                action.id,
                error_message="The persisted WorkOrder has no matching failed AgentResult.",
                error_kind="MissingDelegationResult",
            )
            return "A failed WorkOrder has no matching failed AgentResult."
        owner = linked.next_owner
        if owner is not None and owner in {
            WorkOrderNextOwner.LEADER_REPLAN, WorkOrderNextOwner.USER_INPUT,
        }:
            summary = f"{linked.recipient_role} returned control to {owner.value}."
            if result is not None and result.next_owner == owner:
                summary = result.concise_summary
            # Link repair above emits its own note. It is not the reason the
            # specialist handed back control before this restart.
            progress = [entry for entry in self.runtime.store.list_work_order_progress(linked.id)
                        if entry.created_at < recovery_started_at]
            if progress and (result is None or progress[-1].created_at > result.created_at):
                summary = progress[-1].summary
            self.runtime.fail_action(
                action.id,
                error_message=summary,
                output_summary=summary,
                error_kind=(
                    "RecoveredSpecialistNeedsUserInput"
                    if owner == WorkOrderNextOwner.USER_INPUT
                    else "RecoveredSpecialistNeedsLeaderReplan"
                ),
            )
            return "The current WorkOrder returned control to the Leader or user."
        return "A persisted WorkOrder is waiting; its specialist was not replayed."

    def _run_lock(self, run_id: str) -> threading.Lock:
        return self.runtime.run_lock(run_id)

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
