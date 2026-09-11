"""Typed records for dynamic agent planning, memory, and delegation.

These models deliberately describe the agent control plane only.  Existing
project documents and provider task state machines remain the source of truth
for production data and external side effects.
"""

from __future__ import annotations

from short_drama.skill_ids import SkillIds

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from short_drama.models import ProductionExecutionTarget
from short_drama.skills.models import ProductionPromptStage, ProductionSkillRole


def utc_now() -> datetime:
    """Return a timezone-aware timestamp suitable for persisted records."""

    return datetime.now(UTC)


class RuntimeModel(BaseModel):
    """Strict base model for persisted agent-runtime records."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActionKind(StrEnum):
    TOOL_CALL = "TOOL_CALL"
    DELEGATION = "DELEGATION"
    ARTIFACT_PATCH = "ARTIFACT_PATCH"
    USER_INPUT = "USER_INPUT"
    CHECKPOINT = "CHECKPOINT"


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DelegationStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentResultStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class WorkOrderNextOwner(StrEnum):
    """The persisted actor responsible for the next WorkOrder transition."""

    WORKER_DECISION = "WORKER_DECISION"
    WORKER_TOOL = "WORKER_TOOL"
    LEADER_REPLAN = "LEADER_REPLAN"
    USER_INPUT = "USER_INPUT"


class SpecialistTurnStatus(StrEnum):
    """Persisted lifecycle of one Tool request made by a specialist Agent."""

    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"


class ExperienceConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ArtifactRef(RuntimeModel):
    """Stable pointer to a canonical project artifact, without duplicating it."""

    project_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)

    def identity(self) -> tuple[str, str, str, int | None]:
        return (self.project_id, self.artifact_type, self.artifact_id, self.version)

    def requires_explicit_version(self) -> bool:
        """Whether this content kind identifies a stored source revision."""

        return self.artifact_type in _VERSIONED_CONTEXT_ARTIFACT_TYPES


class ArtifactSummary(RuntimeModel):
    """Small context projection resolved from an :class:`ArtifactRef`."""

    ref: ArtifactRef
    title: str | None = None
    summary: str = Field(min_length=1)


_VERSIONED_CONTEXT_ARTIFACT_TYPES = frozenset(
    {
        "script",
        "script_version",
        "bible",
        "bible_version",
        "character",
        "scene",
        "shot",
        "asset_version",
        "production_document",
        "production_document_version",
        "production_spec",
        "storyboard",
        "storyboard_draft",
        "asset_plan",
        "asset_plan_index",
        "generation_plan",
        "generation_unit",
        "clip_group",
        "output_slot",
        "timeline",
        "timeline_manifest",
    }
)


class ContextBoundary(RuntimeModel):
    """Named neighboring context selected by the delegating Agent.

    A boundary records semantic entry/exit context for a WorkOrder.  It may
    anchor that guidance to exact canonical artifact pointers when such an
    artifact exists, but the first or last unit can legitimately have guidance
    without a neighboring artifact.  It never copies or owns project content.
    """

    relationship: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    guidance: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_boundary(self) -> Self:
        if not self.artifact_refs and self.guidance is None:
            raise ValueError(
                "Context boundary must include artifact references or guidance"
            )
        identities = [ref.identity() for ref in self.artifact_refs]
        if len(identities) != len(set(identities)):
            raise ValueError("Context boundary artifact references must be unique")
        return self


class ToolAttemptRef(RuntimeModel):
    """Stable, compact pointer to one persisted specialist Tool attempt.

    Tool arguments can contain a useful draft that failed contract validation.
    The reference lets a later WorkOrder recover that exact candidate on demand
    without copying the potentially large arguments into every Leader or Worker
    prompt.
    """

    project_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1, max_length=200)
    turn_status: SpecialistTurnStatus
    outcome_status: str | None = Field(default=None, min_length=1, max_length=100)
    has_arguments: bool = False
    has_result: bool = False

    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.project_id,
            self.agent_run_id,
            self.work_order_id,
            self.attempt_id,
        )


class ContextManifest(RuntimeModel):
    """Persisted, Agent-authored context selection for one WorkOrder.

    The manifest records why the delegation exists and which canonical records
    may be loaded. It is a control-plane contract, not a second project document.
    """

    id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    required_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    tool_attempt_refs: list[ToolAttemptRef] = Field(
        default_factory=list[ToolAttemptRef]
    )
    adjacent_boundaries: list[ContextBoundary] = Field(
        default_factory=list[ContextBoundary]
    )
    inherited_decision_ids: list[str] = Field(default_factory=list[str])
    inherited_decisions: list[str] = Field(default_factory=list[str])
    selected_skill_ids: SkillIds = Field(default_factory=list[str])
    production_prompt_stage: ProductionPromptStage | None = None
    experience_tags: list[str] = Field(default_factory=list[str])
    constraints: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    expected_output_schema: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    expected_output_slot_ids: list[str] = Field(default_factory=list[str])
    acceptance_criteria: list[str] = Field(default_factory=list[str])
    exact_artifact_versions: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        relationships = [boundary.relationship for boundary in self.adjacent_boundaries]
        if len(relationships) != len(set(relationships)):
            raise ValueError("Context boundary relationships must be unique")

        required_identities = [ref.identity() for ref in self.required_artifacts]
        if len(required_identities) != len(set(required_identities)):
            raise ValueError("Required context artifact references must be unique")

        attempt_identities = [ref.identity() for ref in self.tool_attempt_refs]
        if len(attempt_identities) != len(set(attempt_identities)):
            raise ValueError("Context manifest Tool attempt references must be unique")

        refs = [
            *self.required_artifacts,
            *[
                ref
                for boundary in self.adjacent_boundaries
                for ref in boundary.artifact_refs
            ],
        ]
        if self.exact_artifact_versions:
            missing_versions = [
                ref
                for ref in refs
                if ref.requires_explicit_version() and ref.version is None
            ]
            if missing_versions:
                first = missing_versions[0]
                raise ValueError(
                    "Context manifest requires an explicit version for "
                    f"{first.artifact_type}:{first.artifact_id}"
                )

        for field_name in (
            "inherited_decision_ids",
            "inherited_decisions",
            "selected_skill_ids",
            "experience_tags",
            "constraints",
            "open_questions",
            "expected_output_slot_ids",
            "acceptance_criteria",
        ):
            values = getattr(self, field_name)
            normalized = [value.strip() for value in values if value.strip()]
            if normalized != values or len(normalized) != len(set(normalized)):
                raise ValueError(
                    f"Context manifest {field_name} entries must be trimmed and unique"
                )
        return self


class AgentRun(RuntimeModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    goal_summary: str | None = Field(default=None, max_length=16000)
    latest_user_instructions: list[str] = Field(default_factory=list[str])
    loaded_tool_schema_ids: list[str] = Field(default_factory=list[str])
    root_agent: str = Field(min_length=1)
    active_skill_ids: SkillIds = Field(default_factory=list[str])
    unattended: bool = False
    execution_target: ProductionExecutionTarget | None = None
    render_export: bool | None = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    parent_run_id: str | None = None
    current_plan_revision_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class PlanStep(RuntimeModel):
    """One dynamically chosen unit in a dependency-aware agent plan."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: ActionKind
    assigned_agent: str = Field(min_length=1)
    production_skill_role: ProductionSkillRole | None = None
    production_prompt_stage: ProductionPromptStage | None = None
    tool_name: str | None = None
    instructions: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    depends_on: list[str] = Field(default_factory=list[str])
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        if self.kind == ActionKind.TOOL_CALL and not self.tool_name:
            raise ValueError("TOOL_CALL plan steps require tool_name")
        if self.id in self.depends_on:
            raise ValueError("A plan step cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Plan step dependencies must be unique")
        return self


class PlanRevision(RuntimeModel):
    """A replaceable plan graph proposed by the agent for one run."""

    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    summary: str = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list[PlanStep])
    selected_method_skill_ids: SkillIds = Field(default_factory=list[str])
    method_selection_rationale: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
    )
    carried_work_order_ids: list[str] = Field(default_factory=list[str])
    goal_complete: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        normalized_method_ids = [
            skill_id.strip()
            for skill_id in self.selected_method_skill_ids
            if skill_id.strip()
        ]
        if normalized_method_ids != self.selected_method_skill_ids or len(
            normalized_method_ids
        ) != len(set(normalized_method_ids)):
            raise ValueError(
                "Plan selected_method_skill_ids must be trimmed and unique"
            )
        normalized_carried_ids = [
            work_order_id.strip()
            for work_order_id in self.carried_work_order_ids
            if work_order_id.strip()
        ]
        if normalized_carried_ids != self.carried_work_order_ids or len(
            normalized_carried_ids
        ) != len(set(normalized_carried_ids)):
            raise ValueError("Plan carried_work_order_ids must be trimmed and unique")
        step_by_id = {step.id: step for step in self.steps}
        if len(step_by_id) != len(self.steps):
            raise ValueError("Plan step ids must be unique within a revision")

        for step in self.steps:
            missing = [dependency for dependency in step.depends_on if dependency not in step_by_id]
            if missing:
                raise ValueError(
                    f"Plan step {step.id!r} references missing dependencies: {missing}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Plan step dependencies must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in step_by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_by_id:
            visit(step_id)
        return self


class ActionRecord(RuntimeModel):
    """Current persisted state of one plan action; not an event log."""

    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    kind: ActionKind
    status: ActionStatus
    agent_id: str = Field(min_length=1)
    plan_revision_id: str | None = None
    plan_step_id: str | None = None
    tool_name: str | None = None
    work_order_id: str | None = None
    input_summary: str = Field(min_length=1)
    output_summary: str | None = None
    error_message: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    error_kind: str | None = Field(default=None, min_length=1, max_length=200)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectMemory(RuntimeModel):
    """Small project-level navigation summary, not a duplicate project document."""

    project_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list[str])
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    updated_at: datetime = Field(default_factory=utc_now)


class RunMemory(RuntimeModel):
    """Mutable working checkpoint used to resume an agent run."""

    run_id: str = Field(min_length=1)
    current_focus: str = ""
    completed_action_ids: list[str] = Field(default_factory=list[str])
    blockers: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    next_actions: list[str] = Field(default_factory=list[str])
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    updated_at: datetime = Field(default_factory=utc_now)


class DecisionMemory(RuntimeModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    run_id: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    created_at: datetime = Field(default_factory=utc_now)


class ExperienceMemory(RuntimeModel):
    """Reusable lesson whose applicability remains explicit."""

    id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    applicability: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    confidence: ExperienceConfidence
    tags: list[str] = Field(default_factory=list[str])
    project_id: str | None = None
    skill_id: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    created_at: datetime = Field(default_factory=utc_now)


class SpecialistTurnRecord(RuntimeModel):
    """Recoverable trace for one specialist decision followed by one Tool action."""

    id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    reason: str = Field(min_length=1, max_length=1000)
    status: SpecialistTurnStatus = SpecialistTurnStatus.REQUESTED
    result: dict[str, JsonValue] | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SpecialistProtocolFeedback(RuntimeModel):
    """Persisted feedback for correcting one malformed Worker response.

    This is control-plane state, not creative review.  Keeping the rejected
    response beside typed validation issues lets the same specialist repair its
    handoff without asking the Leader to decompose or rewrite the WorkOrder.
    """

    id: str = Field(min_length=1)
    rejected_response: str = Field(min_length=1)
    validation_issues: dict[str, list[dict[str, JsonValue]]] = Field(
        default_factory=dict[str, list[dict[str, JsonValue]]]
    )
    loaded_tool_schema_ids: list[str] = Field(default_factory=list[str])
    created_at: datetime = Field(default_factory=utc_now)


class WorkOrder(RuntimeModel):
    """Concise, self-contained contract for delegating work to another agent."""

    id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    source_plan_revision_id: str | None = Field(default=None, min_length=1)
    source_action_id: str | None = Field(default=None, min_length=1)
    active_plan_revision_id: str | None = Field(default=None, min_length=1)
    superseded_by_plan_revision_id: str | None = Field(default=None, min_length=1)
    sender: str = Field(min_length=1)
    recipient_role: str = Field(min_length=1)
    production_skill_role: ProductionSkillRole | None = None
    production_prompt_stage: ProductionPromptStage | None = None
    objective: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    task_inputs: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue],
        description="Leader-selected structured task data, excluding delegation control fields.",
    )
    working_notes: str | None = Field(default=None, max_length=8000)
    scope: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    tool_attempt_refs: list[ToolAttemptRef] = Field(
        default_factory=list[ToolAttemptRef]
    )
    constraints: list[str] = Field(default_factory=list[str])
    confirmed_decisions: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    expected_output_schema: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    expected_output_slot_ids: list[str] = Field(default_factory=list[str])
    acceptance_criteria: list[str] = Field(default_factory=list[str])
    context_manifest: ContextManifest | None = None
    specialist_turns: list[SpecialistTurnRecord] = Field(
        default_factory=list[SpecialistTurnRecord]
    )
    pending_protocol_feedback: SpecialistProtocolFeedback | None = None
    loaded_tool_schema_ids: list[str] = Field(default_factory=list[str])
    status: DelegationStatus = DelegationStatus.PENDING
    continuation_not_before: datetime | None = None
    next_owner: WorkOrderNextOwner | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_context_manifest(self) -> Self:
        manifest = self.context_manifest
        if manifest is None:
            # Records written before ContextManifest existed remain readable. New
            # delegations always supply an exact manifest from AgentRuntime.
            self.context_manifest = ContextManifest(
                id=f"context-manifest-{self.id}",
                work_order_id=self.id,
                objective=self.objective,
                why_now=self.why_now,
                required_artifacts=list(self.scope),
                tool_attempt_refs=list(self.tool_attempt_refs),
                production_prompt_stage=self.production_prompt_stage,
                inherited_decisions=list(self.confirmed_decisions),
                constraints=list(self.constraints),
                open_questions=list(self.open_questions),
                expected_output_schema=dict(self.expected_output_schema),
                expected_output_slot_ids=list(self.expected_output_slot_ids),
                acceptance_criteria=list(self.acceptance_criteria),
                exact_artifact_versions=False,
                created_at=self.created_at,
            )
            return self

        if manifest.work_order_id != self.id:
            raise ValueError("Context manifest must belong to its WorkOrder")
        mirrored_values = (
            ("objective", manifest.objective, self.objective),
            ("why_now", manifest.why_now, self.why_now),
            (
                "production_prompt_stage",
                manifest.production_prompt_stage,
                self.production_prompt_stage,
            ),
            ("required_artifacts", manifest.required_artifacts, self.scope),
            (
                "tool_attempt_refs",
                manifest.tool_attempt_refs,
                self.tool_attempt_refs,
            ),
            (
                "inherited_decisions",
                manifest.inherited_decisions,
                self.confirmed_decisions,
            ),
            ("constraints", manifest.constraints, self.constraints),
            ("open_questions", manifest.open_questions, self.open_questions),
            (
                "expected_output_schema",
                manifest.expected_output_schema,
                self.expected_output_schema,
            ),
            (
                "expected_output_slot_ids",
                manifest.expected_output_slot_ids,
                self.expected_output_slot_ids,
            ),
            (
                "acceptance_criteria",
                manifest.acceptance_criteria,
                self.acceptance_criteria,
            ),
        )
        mismatch = next(
            (
                name
                for name, manifest_value, value in mirrored_values
                if manifest_value != value
            ),
            None,
        )
        if mismatch is not None:
            raise ValueError(
                f"Context manifest {mismatch} must match the WorkOrder contract"
            )
        return self

    @model_validator(mode="after")
    def restore_legacy_next_owner(self) -> Self:
        """Infer the next actor for records written before this field existed."""

        if self.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.CANCELLED,
        }:
            object.__setattr__(self, "next_owner", None)
            return self
        if self.next_owner is not None:
            return self
        pending_tool = any(
            turn.status == SpecialistTurnStatus.REQUESTED
            for turn in self.specialist_turns
        )
        completed_tool = any(
            turn.status in {
                SpecialistTurnStatus.COMPLETED,
                SpecialistTurnStatus.INTERRUPTED,
            }
            for turn in self.specialist_turns
        )
        if pending_tool:
            object.__setattr__(
                self,
                "next_owner",
                WorkOrderNextOwner.WORKER_TOOL,
            )
        elif self.status in {
            DelegationStatus.PENDING,
            DelegationStatus.IN_PROGRESS,
        } or completed_tool:
            object.__setattr__(
                self,
                "next_owner",
                WorkOrderNextOwner.WORKER_DECISION,
            )
        else:
            object.__setattr__(
                self,
                "next_owner",
                WorkOrderNextOwner.LEADER_REPLAN,
            )
        return self


class WorkOrderProgressEntry(RuntimeModel):
    """One append-only progress note emitted while a WorkOrder is active."""

    id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    author: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    created_at: datetime = Field(default_factory=utc_now)


class AgentResult(RuntimeModel):
    """Structured response returned from a delegated specialist agent."""

    id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    status: AgentResultStatus
    concise_summary: str = Field(min_length=1)
    deliverable: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    created_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    completion_receipts: list[dict[str, JsonValue]] = Field(default_factory=list[dict[str, JsonValue]])
    updated_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    findings: list[str] = Field(default_factory=list[str])
    decisions: list[str] = Field(default_factory=list[str])
    assumptions: list[str] = Field(default_factory=list[str])
    unresolved_questions: list[str] = Field(default_factory=list[str])
    evidence_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recommended_next_actions: list[str] = Field(default_factory=list[str])
    next_owner: WorkOrderNextOwner | None = None
    stale_handoff: bool = False
    stale_reason: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_stale_handoff(self) -> Self:
        if self.stale_handoff != (self.stale_reason is not None):
            raise ValueError("A stale AgentResult requires one stale_reason")
        if self.status == AgentResultStatus.PARTIAL:
            if self.next_owner is None:
                object.__setattr__(
                    self,
                    "next_owner",
                    WorkOrderNextOwner.LEADER_REPLAN,
                )
        elif self.next_owner is not None:
            raise ValueError("Only a PARTIAL AgentResult can name a next owner")
        return self


class AgentResultDigest(RuntimeModel):
    """Bounded semantic handoff from one completed delegated WorkOrder.

    The full :class:`AgentResult` remains persisted in the runtime store.  This
    projection carries only the fields the Leader needs when choosing the next
    plan, without replaying the Worker's complete conversational context.
    """

    status: AgentResultStatus
    concise_summary: str = Field(min_length=1)
    deliverable: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    findings: list[str] = Field(default_factory=list[str])
    completion_receipts: list[dict[str, JsonValue]] = Field(default_factory=list[dict[str, JsonValue]])
    evidence_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class ActionDigest(RuntimeModel):
    id: str
    work_order_id: str | None = None
    kind: ActionKind
    status: ActionStatus
    summary: str
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    agent_result: AgentResultDigest | None = None


class ContextPack(RuntimeModel):
    """Bounded context assembled for one agent call.

    Runtime callers keep the complete typed memories so local validation can
    still work with exact :class:`ArtifactRef` objects.  The model-facing JSON
    projection serializes each unique reference once in ``artifact_index``;
    memories and action digests point to the compact index ids instead of
    replaying the same four-field object dozens of times.
    """

    run_id: str
    project_id: str
    objective: str
    work_order_id: str | None = None
    context_manifest_id: str | None = None
    active_skill_ids: SkillIds = Field(default_factory=list[str])
    unattended: bool = False
    execution_target: ProductionExecutionTarget | None = None
    render_export: bool | None = None
    project_summary: str | None = None
    run_focus: str | None = None
    constraints: list[str] = Field(default_factory=list[str])
    decisions: list[DecisionMemory] = Field(default_factory=list[DecisionMemory])
    experiences: list[ExperienceMemory] = Field(default_factory=list[ExperienceMemory])
    artifacts: list[ArtifactSummary] = Field(default_factory=list[ArtifactSummary])
    tool_attempt_refs: list[ToolAttemptRef] = Field(
        default_factory=list[ToolAttemptRef]
    )
    unresolved_artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recent_actions: list[ActionDigest] = Field(default_factory=list[ActionDigest])
    artifact_index: dict[str, ArtifactRef] = Field(
        default_factory=dict[str, ArtifactRef],
        exclude=True,
    )
    blockers: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    next_actions: list[str] = Field(default_factory=list[str])
    compiled_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def build_artifact_index(self) -> Self:
        refs: list[ArtifactRef] = [artifact.ref for artifact in self.artifacts]
        refs.extend(self.unresolved_artifact_refs)
        for decision in self.decisions:
            refs.extend(decision.artifact_refs)
        for experience in self.experiences:
            refs.extend(experience.artifact_refs)
        for action in self.recent_actions:
            refs.extend(action.artifact_refs)
            if action.agent_result is not None:
                refs.extend(action.agent_result.evidence_refs)

        unique_refs: list[ArtifactRef] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        for ref in refs:
            identity = ref.identity()
            if identity in seen:
                continue
            seen.add(identity)
            unique_refs.append(ref)
        canonical_index = {
            f"artifact-{index}": ref for index, ref in enumerate(unique_refs, start=1)
        }
        object.__setattr__(self, "artifact_index", canonical_index)
        return self

    def artifact_ref_id(self, ref: ArtifactRef) -> str:
        """Return the compact id for an exact reference visible in this pack."""

        for ref_id, indexed_ref in self.artifact_index.items():
            if indexed_ref.identity() == ref.identity():
                return ref_id
        raise KeyError(f"Artifact reference is not visible in ContextPack: {ref.identity()}")

    def resolve_artifact_ref(self, ref_id: str) -> ArtifactRef:
        """Recover the exact canonical reference named by a model projection."""

        try:
            return self.artifact_index[ref_id]
        except KeyError as exc:
            raise KeyError(f"Unknown ContextPack artifact reference id: {ref_id}") from exc

    def model_context_dump(self) -> dict[str, Any]:
        """Return the compact projection intended for a model request.

        Ordinary ``model_dump`` keeps its normal Pydantic meaning and therefore
        remains safe for diagnostics, APIs, or future persistence.  Model call
        sites opt into this projection explicitly.
        """

        payload = self.model_dump(mode="json")

        def ref_ids(refs: list[ArtifactRef]) -> list[str]:
            return [self.artifact_ref_id(ref) for ref in refs]

        projected = dict(payload)
        projected.pop("compiled_at", None)
        projected["video_execution_target"] = projected.pop(
            "execution_target",
            None,
        )
        projected["artifact_index"] = {
            ref_id: ref.model_dump(mode="json")
            for ref_id, ref in self.artifact_index.items()
        }

        projected_artifacts: list[dict[str, Any]] = []
        for artifact, serialized in zip(self.artifacts, payload["artifacts"], strict=True):
            item = dict(serialized)
            item.pop("ref", None)
            item["artifact_ref_id"] = self.artifact_ref_id(artifact.ref)
            projected_artifacts.append(item)
        projected["artifacts"] = projected_artifacts
        projected["unresolved_artifact_ref_ids"] = ref_ids(
            self.unresolved_artifact_refs
        )
        projected.pop("unresolved_artifact_refs", None)

        projected_decisions: list[dict[str, Any]] = []
        for decision, serialized in zip(
            self.decisions,
            payload["decisions"],
            strict=True,
        ):
            item = dict(serialized)
            item.pop("project_id", None)
            item.pop("run_id", None)
            item.pop("created_at", None)
            item.pop("artifact_refs", None)
            item["artifact_ref_ids"] = ref_ids(decision.artifact_refs)
            projected_decisions.append(item)
        projected["decisions"] = projected_decisions

        projected_experiences: list[dict[str, Any]] = []
        for experience, serialized in zip(
            self.experiences,
            payload["experiences"],
            strict=True,
        ):
            item = dict(serialized)
            item.pop("project_id", None)
            item.pop("created_at", None)
            item.pop("artifact_refs", None)
            item["artifact_ref_ids"] = ref_ids(experience.artifact_refs)
            projected_experiences.append(item)
        projected["experiences"] = projected_experiences

        projected_actions: list[dict[str, Any]] = []
        for action, serialized in zip(
            self.recent_actions,
            payload["recent_actions"],
            strict=True,
        ):
            item = dict(serialized)
            item.pop("artifact_refs", None)
            item["artifact_ref_ids"] = ref_ids(action.artifact_refs)
            if action.agent_result is not None:
                serialized_result = item.get("agent_result")
                if not isinstance(serialized_result, dict):
                    raise TypeError("AgentResultDigest serialization must be an object")
                result = dict(cast(dict[str, Any], serialized_result))
                result.pop("evidence_refs", None)
                result["evidence_ref_ids"] = ref_ids(
                    action.agent_result.evidence_refs
                )
                item["agent_result"] = result
            projected_actions.append(item)
        projected["recent_actions"] = projected_actions
        return projected


class AgentRunSnapshot(RuntimeModel):
    """Minimum recoverable state needed to continue an interrupted run."""

    run: AgentRun
    current_plan: PlanRevision | None
    actions: list[ActionRecord] = Field(default_factory=list[ActionRecord])
    memory: RunMemory
    pending_work_orders: list[WorkOrder] = Field(default_factory=list[WorkOrder])
