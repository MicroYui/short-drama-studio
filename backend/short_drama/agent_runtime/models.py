"""Typed records for dynamic agent planning, memory, and delegation.

These models deliberately describe the agent control plane only.  Existing
project documents and provider task state machines remain the source of truth
for production data and external side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from short_drama.models import ProductionExecutionTarget
from short_drama.skills.models import ProductionSkillRole


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


class ArtifactSummary(RuntimeModel):
    """Small context projection resolved from an :class:`ArtifactRef`."""

    ref: ArtifactRef
    title: str | None = None
    summary: str = Field(min_length=1)


class AgentRun(RuntimeModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    root_agent: str = Field(min_length=1)
    active_skill_ids: list[str] = Field(default_factory=list[str])
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
    goal_complete: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
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


class WorkOrder(RuntimeModel):
    """Concise, self-contained contract for delegating work to another agent."""

    id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    recipient_role: str = Field(min_length=1)
    production_skill_role: ProductionSkillRole | None = None
    objective: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    scope: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    constraints: list[str] = Field(default_factory=list[str])
    confirmed_decisions: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    expected_output_schema: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    acceptance_criteria: list[str] = Field(default_factory=list[str])
    specialist_turns: list[SpecialistTurnRecord] = Field(
        default_factory=list[SpecialistTurnRecord]
    )
    status: DelegationStatus = DelegationStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentResult(RuntimeModel):
    """Structured response returned from a delegated specialist agent."""

    id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    status: AgentResultStatus
    concise_summary: str = Field(min_length=1)
    created_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    updated_artifacts: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    findings: list[str] = Field(default_factory=list[str])
    decisions: list[str] = Field(default_factory=list[str])
    assumptions: list[str] = Field(default_factory=list[str])
    unresolved_questions: list[str] = Field(default_factory=list[str])
    evidence_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recommended_next_actions: list[str] = Field(default_factory=list[str])
    created_at: datetime = Field(default_factory=utc_now)


class ActionDigest(RuntimeModel):
    id: str
    kind: ActionKind
    status: ActionStatus
    summary: str
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class ContextPack(RuntimeModel):
    """Bounded context assembled for one agent call."""

    run_id: str
    project_id: str
    objective: str
    active_skill_ids: list[str] = Field(default_factory=list[str])
    execution_target: ProductionExecutionTarget | None = None
    render_export: bool | None = None
    project_summary: str | None = None
    run_focus: str | None = None
    constraints: list[str] = Field(default_factory=list[str])
    decisions: list[DecisionMemory] = Field(default_factory=list[DecisionMemory])
    experiences: list[ExperienceMemory] = Field(default_factory=list[ExperienceMemory])
    artifacts: list[ArtifactSummary] = Field(default_factory=list[ArtifactSummary])
    unresolved_artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    recent_actions: list[ActionDigest] = Field(default_factory=list[ActionDigest])
    blockers: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    next_actions: list[str] = Field(default_factory=list[str])
    compiled_at: datetime = Field(default_factory=utc_now)


class AgentRunSnapshot(RuntimeModel):
    """Minimum recoverable state needed to continue an interrupted run."""

    run: AgentRun
    current_plan: PlanRevision | None
    actions: list[ActionRecord] = Field(default_factory=list[ActionRecord])
    memory: RunMemory
    pending_work_orders: list[WorkOrder] = Field(default_factory=list[WorkOrder])
