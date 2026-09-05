"""Read-only production board derived from canonical runtime and project state.

The board deliberately owns no persistence.  It is a compact projection for a
Leader, UI, or trace consumer and can always be rebuilt from the Agent runtime,
the canonical :class:`ProjectDocument`, and its owned content-planning cursor.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from short_drama.agent_runtime.artifact_resolver import project_artifact_refs
from short_drama.agent_runtime.models import (
    ActionRecord,
    ActionStatus,
    AgentResultStatus,
    AgentRunStatus,
    ArtifactRef,
    DecisionMemory,
    DelegationStatus,
    RuntimeModel,
    SpecialistTurnStatus,
    WorkOrder,
    WorkOrderNextOwner,
    WorkOrderProgressEntry,
    utc_now,
)
from short_drama.agent_runtime.runtime import AgentRuntime
from short_drama.models import PlanningRunRecord, ProjectDocument, StoryIssueLedgerEntry


class ProductionBoardProjectStore(Protocol):
    """Minimum canonical-project reads needed to derive a board."""

    def get_project(self, project_id: str) -> ProjectDocument | None: ...

    def get_planning_run(self, run_id: str) -> PlanningRunRecord | None: ...


class ProductionBoardStepStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductionBoardStep(RuntimeModel):
    id: str
    title: str
    assigned_agent: str
    status: ProductionBoardStepStatus
    depends_on: list[str] = Field(default_factory=list[str])
    action_id: str | None = None
    work_order_id: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class ProductionBoardWorkOrder(RuntimeModel):
    id: str
    recipient_role: str
    objective: str
    status: DelegationStatus
    next_owner: WorkOrderNextOwner | None = None
    active: bool = True
    source_plan_revision_id: str | None = None
    superseded_by_plan_revision_id: str | None = None
    context_manifest_id: str | None = None
    pending_tool_name: str | None = None
    result_status: AgentResultStatus | None = None
    result_summary: str | None = None
    progress_entries: list[WorkOrderProgressEntry] = Field(
        default_factory=list[WorkOrderProgressEntry]
    )
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])


class ProductionBoardView(RuntimeModel):
    """Ephemeral shared-board projection; never a second writable truth."""

    run_id: str
    project_id: str
    objective: str
    run_status: AgentRunStatus
    plan_revision_id: str | None = None
    plan_summary: str | None = None
    selected_method_skill_ids: list[str] = Field(default_factory=list[str])
    method_selection_rationale: str | None = None
    content_planning_status: str | None = None
    content_planning_step: str | None = None
    current_focus: str = ""
    steps: list[ProductionBoardStep] = Field(default_factory=list[ProductionBoardStep])
    work_orders: list[ProductionBoardWorkOrder] = Field(
        default_factory=list[ProductionBoardWorkOrder]
    )
    historical_work_orders: list[ProductionBoardWorkOrder] = Field(
        default_factory=list[ProductionBoardWorkOrder]
    )
    blockers: list[str] = Field(default_factory=list[str])
    open_questions: list[str] = Field(default_factory=list[str])
    next_actions: list[str] = Field(default_factory=list[str])
    decisions: list[DecisionMemory] = Field(default_factory=list[DecisionMemory])
    issue_ledger: list[StoryIssueLedgerEntry] = Field(
        default_factory=list[StoryIssueLedgerEntry]
    )
    artifact_refs: list[ArtifactRef] = Field(default_factory=list[ArtifactRef])
    generated_at: datetime = Field(default_factory=utc_now)


def _latest_actions_by_step(
    actions: list[ActionRecord],
    plan_revision_id: str,
) -> dict[str, ActionRecord]:
    latest: dict[str, ActionRecord] = {}
    for action in actions:
        if (
            action.plan_revision_id == plan_revision_id
            and action.plan_step_id is not None
        ):
            latest[action.plan_step_id] = action
    return latest


def _step_status(
    action: ActionRecord | None,
    *,
    ready: bool,
) -> ProductionBoardStepStatus:
    if action is None:
        return (
            ProductionBoardStepStatus.READY
            if ready
            else ProductionBoardStepStatus.BLOCKED
        )
    return {
        ActionStatus.PENDING: ProductionBoardStepStatus.BLOCKED,
        ActionStatus.RUNNING: ProductionBoardStepStatus.IN_PROGRESS,
        ActionStatus.SUCCEEDED: ProductionBoardStepStatus.COMPLETED,
        ActionStatus.FAILED: ProductionBoardStepStatus.FAILED,
        ActionStatus.CANCELLED: ProductionBoardStepStatus.CANCELLED,
    }[action.status]


def _pending_tool_name(work_order: WorkOrder) -> str | None:
    for turn in reversed(work_order.specialist_turns):
        if turn.status in {
            SpecialistTurnStatus.REQUESTED,
            SpecialistTurnStatus.IN_PROGRESS,
        }:
            return turn.tool_name
    return None


def _unique_refs(refs: list[ArtifactRef]) -> list[ArtifactRef]:
    result: list[ArtifactRef] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for ref in refs:
        if ref.identity() in seen:
            continue
        seen.add(ref.identity())
        result.append(ref)
    return result


def build_production_board(
    runtime: AgentRuntime,
    projects: ProductionBoardProjectStore,
    run_id: str,
) -> ProductionBoardView:
    """Rebuild one shared board from existing sources of truth."""

    snapshot = runtime.snapshot(run_id)
    plan = snapshot.current_plan
    ready_ids = {step.id for step in runtime.ready_steps(run_id)}
    actions_by_step = (
        {}
        if plan is None
        else _latest_actions_by_step(snapshot.actions, plan.id)
    )
    steps = (
        []
        if plan is None
        else [
            ProductionBoardStep(
                id=step.id,
                title=step.title,
                assigned_agent=step.assigned_agent,
                status=_step_status(
                    actions_by_step.get(step.id),
                    ready=step.id in ready_ids,
                ),
                depends_on=list(step.depends_on),
                action_id=(
                    None
                    if actions_by_step.get(step.id) is None
                    else actions_by_step[step.id].id
                ),
                work_order_id=(
                    None
                    if actions_by_step.get(step.id) is None
                    else actions_by_step[step.id].work_order_id
                ),
                artifact_refs=list(step.artifact_refs),
            )
            for step in plan.steps
        ]
    )

    work_orders = runtime.store.list_work_orders(run_id)
    board_work_orders: list[ProductionBoardWorkOrder] = []
    historical_work_orders: list[ProductionBoardWorkOrder] = []
    accumulated_refs: list[ArtifactRef] = []
    for work_order in work_orders:
        result = runtime.store.get_agent_result(work_order.id)
        progress_entries = runtime.store.list_work_order_progress(work_order.id)
        refs = list(work_order.scope)
        refs.extend(
            ref for entry in progress_entries for ref in entry.artifact_refs
        )
        if result is not None:
            refs.extend(result.created_artifacts)
            refs.extend(result.updated_artifacts)
            refs.extend(result.evidence_refs)
        accumulated_refs.extend(refs)
        active = (
            work_order.source_plan_revision_id is None
            or work_order.active_plan_revision_id == (None if plan is None else plan.id)
        ) and work_order.status != DelegationStatus.CANCELLED
        projected = ProductionBoardWorkOrder(
            id=work_order.id,
            recipient_role=work_order.recipient_role,
            objective=work_order.objective,
            status=work_order.status,
            next_owner=work_order.next_owner,
            active=active,
            source_plan_revision_id=work_order.source_plan_revision_id,
            superseded_by_plan_revision_id=work_order.superseded_by_plan_revision_id,
            context_manifest_id=(
                None
                if work_order.context_manifest is None
                else work_order.context_manifest.id
            ),
            pending_tool_name=_pending_tool_name(work_order),
            result_status=None if result is None else result.status,
            result_summary=None if result is None else result.concise_summary,
            progress_entries=progress_entries,
            artifact_refs=_unique_refs(refs),
        )
        if active:
            board_work_orders.append(projected)
        else:
            historical_work_orders.append(projected)

    document = projects.get_project(snapshot.run.project_id)
    planning_run = None
    if (
        document is not None
        and document.planning is not None
        and document.planning.run_id is not None
        and document.planning.owner_agent_run_id == run_id
    ):
        planning_run = projects.get_planning_run(document.planning.run_id)

    refs = list(snapshot.memory.artifact_refs)
    refs.extend(
        ref
        for step in ([] if plan is None else plan.steps)
        for ref in step.artifact_refs
    )
    refs.extend(ref for action in snapshot.actions for ref in action.artifact_refs)
    refs.extend(accumulated_refs)
    if document is not None:
        refs.extend(project_artifact_refs(document))

    decisions = [
        decision
        for decision in runtime.store.list_decisions(
            snapshot.run.project_id,
            run_id=run_id,
        )
        if decision.run_id in {None, run_id}
    ]
    return ProductionBoardView(
        run_id=run_id,
        project_id=snapshot.run.project_id,
        objective=snapshot.run.objective,
        run_status=snapshot.run.status,
        plan_revision_id=None if plan is None else plan.id,
        plan_summary=None if plan is None else plan.summary,
        selected_method_skill_ids=(
            [] if plan is None else list(plan.selected_method_skill_ids)
        ),
        method_selection_rationale=(
            None if plan is None else plan.method_selection_rationale
        ),
        content_planning_status=(
            None
            if planning_run is None or not hasattr(planning_run, "status")
            else planning_run.status.value
        ),
        content_planning_step=(
            None
            if planning_run is None or not hasattr(planning_run, "current_step")
            else planning_run.current_step.value
        ),
        current_focus=snapshot.memory.current_focus,
        steps=steps,
        work_orders=board_work_orders,
        historical_work_orders=historical_work_orders,
        blockers=list(snapshot.memory.blockers),
        open_questions=list(snapshot.memory.open_questions),
        next_actions=(
            [
                step.title
                for step in ([] if plan is None else plan.steps)
                if step.id in ready_ids
            ]
            or list(snapshot.memory.next_actions)
        ),
        decisions=decisions,
        issue_ledger=(
            [] if planning_run is None else list(planning_run.issue_ledger)
        ),
        artifact_refs=_unique_refs(refs),
    )


__all__ = [
    "ProductionBoardProjectStore",
    "ProductionBoardStep",
    "ProductionBoardStepStatus",
    "ProductionBoardView",
    "ProductionBoardWorkOrder",
    "build_production_board",
]
