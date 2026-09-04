"""Workflow-neutral coordinator for dynamic agent runs."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.context import (
    DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ContextCompiler,
)
from short_drama.agent_runtime.contract_validation import validate_agent_result_contract
from short_drama.agent_runtime.models import (
    ActionKind,
    ActionRecord,
    ActionStatus,
    AgentResult,
    AgentResultStatus,
    AgentRun,
    AgentRunSnapshot,
    AgentRunStatus,
    ArtifactRef,
    ContextPack,
    DecisionMemory,
    DelegationStatus,
    ExperienceConfidence,
    ExperienceMemory,
    PlanRevision,
    PlanStep,
    RunMemory,
    WorkOrder,
    utc_now,
)
from short_drama.skills.models import ProductionSkillRole
from short_drama.agent_runtime.plan_graph import PlanGraph
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.models import ProductionExecutionTarget
from short_drama.observability import log_event


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _normalized_memory_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_artifact_refs(values: Sequence[ArtifactRef]) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for ref in values:
        identity = ref.identity()
        if identity in seen:
            continue
        seen.add(identity)
        refs.append(ref)
    return refs


_CONFIDENCE_RANK = {
    ExperienceConfidence.LOW: 0,
    ExperienceConfidence.MEDIUM: 1,
    ExperienceConfidence.HIGH: 2,
}


class AgentRuntime:
    """Persist plans and progress while leaving creative choices to agents.

    This class intentionally does not prescribe a production workflow.  A
    manager agent may replace its plan at any point, while the harness retains
    only ordinary persistence and dependency bookkeeping.
    """

    def __init__(
        self,
        store: AgentRuntimeStore,
        context_compiler: ContextCompiler | None = None,
    ) -> None:
        self.store = store
        self._context_compiler = context_compiler

    def create_run(
        self,
        *,
        project_id: str,
        objective: str,
        root_agent: str,
        active_skill_ids: Sequence[str] = (),
        parent_run_id: str | None = None,
        unattended: bool = False,
        execution_target: ProductionExecutionTarget | None = None,
        render_export: bool | None = None,
    ) -> AgentRun:
        run = AgentRun(
            id=_new_id("agent-run"),
            project_id=project_id,
            objective=objective,
            root_agent=root_agent,
            active_skill_ids=list(active_skill_ids),
            parent_run_id=parent_run_id,
            unattended=unattended,
            execution_target=execution_target,
            render_export=render_export,
        )
        self.store.create_run(
            run,
            RunMemory(run_id=run.id, current_focus=objective, next_actions=["Create a plan"]),
        )
        self._log_run_status(run, previous_status=None)
        return run

    def start_run(
        self,
        *,
        project_id: str,
        objective: str,
        root_agent: str,
        active_skill_ids: Sequence[str] = (),
        parent_run_id: str | None = None,
        unattended: bool = False,
        execution_target: ProductionExecutionTarget | None = None,
        render_export: bool | None = None,
    ) -> AgentRun:
        """Compatibility alias for callers that describe creation as start."""

        return self.create_run(
            project_id=project_id,
            objective=objective,
            root_agent=root_agent,
            active_skill_ids=active_skill_ids,
            parent_run_id=parent_run_id,
            unattended=unattended,
            execution_target=execution_target,
            render_export=render_export,
        )

    def revise_plan(
        self,
        run_id: str,
        *,
        summary: str,
        steps: Sequence[PlanStep],
        goal_complete: bool = False,
    ) -> PlanRevision:
        run = self._require_run(run_id)
        next_revision = len(self.store.list_plan_revisions(run_id)) + 1
        revision = PlanRevision(
            id=_new_id("plan"),
            run_id=run_id,
            revision=next_revision,
            summary=summary,
            steps=list(steps),
            goal_complete=goal_complete,
        )
        run.current_plan_revision_id = revision.id
        run.updated_at = utc_now()
        self.store.append_plan_revision(revision, run)
        return revision

    def add_plan(
        self,
        run_id: str,
        *,
        summary: str,
        steps: Sequence[PlanStep],
        goal_complete: bool = False,
    ) -> PlanRevision:
        return self.revise_plan(
            run_id,
            summary=summary,
            steps=steps,
            goal_complete=goal_complete,
        )

    def replace_plan(
        self,
        run_id: str,
        *,
        summary: str,
        steps: Sequence[PlanStep],
        goal_complete: bool = False,
    ) -> PlanRevision:
        """Replace the active plan by appending a recoverable new revision."""

        return self.revise_plan(
            run_id,
            summary=summary,
            steps=steps,
            goal_complete=goal_complete,
        )

    def ready_steps(self, run_id: str) -> list[PlanStep]:
        run = self._require_run(run_id)
        plan = self.store.get_current_plan(run)
        if plan is None:
            return []
        return PlanGraph(plan).ready_steps(self.store.list_actions(run_id))

    def begin_action(
        self,
        run_id: str,
        *,
        kind: ActionKind,
        agent_id: str,
        input_summary: str,
        plan_revision_id: str | None = None,
        plan_step_id: str | None = None,
        tool_name: str | None = None,
        work_order_id: str | None = None,
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> ActionRecord:
        self._require_run(run_id)
        now = utc_now()
        action = ActionRecord(
            id=_new_id("action"),
            run_id=run_id,
            kind=kind,
            status=ActionStatus.RUNNING,
            agent_id=agent_id,
            plan_revision_id=plan_revision_id,
            plan_step_id=plan_step_id,
            tool_name=tool_name,
            work_order_id=work_order_id,
            input_summary=input_summary,
            artifact_refs=list(artifact_refs),
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        self.store.save_action(action)
        return action

    def start_action(
        self,
        run_id: str,
        *,
        kind: ActionKind,
        agent_id: str,
        input_summary: str,
        plan_revision_id: str | None = None,
        plan_step_id: str | None = None,
        tool_name: str | None = None,
        work_order_id: str | None = None,
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> ActionRecord:
        return self.begin_action(
            run_id,
            kind=kind,
            agent_id=agent_id,
            input_summary=input_summary,
            plan_revision_id=plan_revision_id,
            plan_step_id=plan_step_id,
            tool_name=tool_name,
            work_order_id=work_order_id,
            artifact_refs=artifact_refs,
        )

    def finish_action(
        self,
        action_id: str,
        *,
        status: ActionStatus,
        output_summary: str | None = None,
        error_message: str | None = None,
        error_kind: str | None = None,
        artifact_refs: Sequence[ArtifactRef] | None = None,
    ) -> ActionRecord:
        if status not in (
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.CANCELLED,
        ):
            raise ValueError("finish_action requires a terminal action status")
        action = self.store.get_action(action_id)
        if action is None:
            raise KeyError(f"Unknown action: {action_id}")
        action.status = status
        action.output_summary = output_summary
        action.error_message = error_message
        action.error_kind = error_kind
        if artifact_refs is not None:
            action.artifact_refs = list(artifact_refs)
        action.completed_at = utc_now()
        action.updated_at = action.completed_at
        if action.started_at is not None:
            action.duration_ms = max(
                0,
                int((action.completed_at - action.started_at).total_seconds() * 1000),
            )
        self.store.save_action(action)
        run = self._require_run(action.run_id)
        log_event(
            "agent.tool_finished" if action.tool_name is not None else "agent.action_finished",
            run_id=action.run_id,
            project_id=run.project_id,
            action_id=action.id,
            action_kind=action.kind.value,
            status=action.status.value,
            agent_id=action.agent_id,
            plan_revision_id=action.plan_revision_id,
            plan_step_id=action.plan_step_id,
            tool_name=action.tool_name,
            work_order_id=action.work_order_id,
            duration_ms=action.duration_ms,
            error_kind=action.error_kind,
            artifact_count=len(action.artifact_refs),
        )
        return action

    def complete_action(
        self,
        action_id: str,
        *,
        output_summary: str,
        artifact_refs: Sequence[ArtifactRef] | None = None,
    ) -> ActionRecord:
        return self.finish_action(
            action_id,
            status=ActionStatus.SUCCEEDED,
            output_summary=output_summary,
            artifact_refs=artifact_refs,
        )

    def fail_action(
        self,
        action_id: str,
        *,
        error_message: str,
        output_summary: str | None = None,
        error_kind: str | None = None,
    ) -> ActionRecord:
        return self.finish_action(
            action_id,
            status=ActionStatus.FAILED,
            output_summary=output_summary,
            error_message=error_message,
            error_kind=error_kind,
        )

    def checkpoint(
        self,
        run_id: str,
        *,
        current_focus: str,
        blockers: Sequence[str] = (),
        open_questions: Sequence[str] = (),
        next_actions: Sequence[str] = (),
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> RunMemory:
        self._require_run(run_id)
        actions = self.store.list_actions(run_id)
        memory = RunMemory(
            run_id=run_id,
            current_focus=current_focus,
            completed_action_ids=[
                action.id for action in actions if action.status == ActionStatus.SUCCEEDED
            ],
            blockers=list(blockers),
            open_questions=list(open_questions),
            next_actions=list(next_actions),
            artifact_refs=list(artifact_refs),
        )
        self.store.save_run_memory(memory)
        return memory

    def set_run_status(self, run_id: str, status: AgentRunStatus) -> AgentRun:
        run = self._require_run(run_id)
        previous_status = run.status
        run.status = status
        run.updated_at = utc_now()
        run.completed_at = (
            run.updated_at
            if status in (
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            )
            else None
        )
        self.store.save_run(run)
        self._log_run_status(run, previous_status=previous_status)
        return run

    def set_run_execution_target(
        self,
        run_id: str,
        execution_target: ProductionExecutionTarget,
    ) -> AgentRun:
        run = self._require_run(run_id)
        run.execution_target = execution_target
        run.updated_at = utc_now()
        self.store.save_run(run)
        return run

    def set_run_render_export(
        self,
        run_id: str,
        render_export: bool,
    ) -> AgentRun:
        run = self._require_run(run_id)
        run.render_export = render_export
        run.updated_at = utc_now()
        self.store.save_run(run)
        return run

    def set_run_active_skills(
        self,
        run_id: str,
        skill_ids: Sequence[str],
    ) -> AgentRun:
        """Persist an Agent-selected Skill composition for later context assembly.

        The runtime deliberately does not decide which Skills apply. It only
        normalizes identifiers already selected and validated by the owning Agent.
        """

        run = self._require_run(run_id)
        run.active_skill_ids = list(
            dict.fromkeys(value.strip() for value in skill_ids if value.strip())
        )
        run.updated_at = utc_now()
        self.store.save_run(run)
        return run

    def remember_decision(
        self,
        *,
        project_id: str,
        statement: str,
        rationale: str,
        run_id: str | None = None,
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> DecisionMemory:
        if run_id is not None:
            run = self._require_run(run_id)
            if run.project_id != project_id:
                raise ValueError("Decision project must match its run")
        decision = DecisionMemory(
            id=_new_id("decision"),
            project_id=project_id,
            run_id=run_id,
            statement=statement,
            rationale=rationale,
            artifact_refs=list(artifact_refs),
        )
        self.store.save_decision(decision)
        return decision

    def remember_experience(
        self,
        *,
        summary: str,
        applicability: str,
        evidence: str,
        confidence: ExperienceConfidence,
        tags: Sequence[str] = (),
        project_id: str | None = None,
        skill_id: str | None = None,
        artifact_refs: Sequence[ArtifactRef] = (),
        deduplicate: bool = True,
    ) -> ExperienceMemory:
        if deduplicate:
            key = (
                _normalized_memory_text(summary),
                _normalized_memory_text(applicability),
            )
            for existing in self.store.list_experiences_for_scope(
                project_id=project_id,
                skill_id=skill_id,
            ):
                existing_key = (
                    _normalized_memory_text(existing.summary),
                    _normalized_memory_text(existing.applicability),
                )
                if existing_key != key:
                    continue
                if _normalized_memory_text(evidence) not in {
                    _normalized_memory_text(item)
                    for item in existing.evidence.split("\n\n")
                }:
                    existing.evidence = f"{existing.evidence}\n\n{evidence}"
                if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[existing.confidence]:
                    existing.confidence = confidence
                existing.tags = _unique_strings([*existing.tags, *tags])
                existing.artifact_refs = _unique_artifact_refs(
                    [*existing.artifact_refs, *artifact_refs]
                )
                self.store.save_experience(existing)
                return existing
        experience = ExperienceMemory(
            id=_new_id("experience"),
            summary=summary,
            applicability=applicability,
            evidence=evidence,
            confidence=confidence,
            tags=list(tags),
            project_id=project_id,
            skill_id=skill_id,
            artifact_refs=list(artifact_refs),
        )
        self.store.save_experience(experience)
        return experience

    def distill_experience_from_result(
        self,
        work_order_id: str,
        *,
        applicability: str,
        evidence: str | None = None,
        confidence: ExperienceConfidence = ExperienceConfidence.MEDIUM,
        tags: Sequence[str] = (),
        skill_id: str | None = None,
        deduplicate: bool = True,
    ) -> ExperienceMemory:
        """Derive one reusable lesson from a completed delegated result.

        Callers choose which successful results are worth retaining; the method
        derives their project, primary Skill, evidence, and artifact references,
        then applies the normal deterministic deduplication policy.
        """

        work_order = self._require_work_order(work_order_id)
        result = self.store.get_agent_result(work_order_id)
        if result is None:
            raise ValueError("Cannot distill experience before an AgentResult exists")
        if result.status != AgentResultStatus.COMPLETED:
            raise ValueError("Only a COMPLETED AgentResult can become experience memory")
        run = self._require_run(work_order.parent_run_id)
        selected_skill_id = skill_id
        if selected_skill_id is None and run.active_skill_ids:
            selected_skill_id = run.active_skill_ids[0]
        evidence_text = evidence
        if evidence_text is None:
            evidence_parts = _unique_strings([*result.findings, *result.decisions])
            evidence_text = "\n".join(evidence_parts) or result.concise_summary
        refs = _unique_artifact_refs(
            [
                *work_order.scope,
                *result.created_artifacts,
                *result.updated_artifacts,
                *result.evidence_refs,
            ]
        )
        return self.remember_experience(
            summary=result.concise_summary,
            applicability=applicability,
            evidence=evidence_text,
            confidence=confidence,
            tags=tags,
            project_id=run.project_id,
            skill_id=selected_skill_id,
            artifact_refs=refs,
            deduplicate=deduplicate,
        )

    def create_work_order(
        self,
        run_id: str,
        *,
        sender: str,
        recipient_role: str,
        production_skill_role: ProductionSkillRole | None = None,
        objective: str,
        why_now: str,
        scope: Sequence[ArtifactRef] = (),
        constraints: Sequence[str] = (),
        confirmed_decisions: Sequence[str] = (),
        open_questions: Sequence[str] = (),
        expected_output_schema: dict[str, JsonValue] | None = None,
        acceptance_criteria: Sequence[str] = (),
    ) -> WorkOrder:
        self._require_run(run_id)
        work_order = WorkOrder(
            id=_new_id("work-order"),
            parent_run_id=run_id,
            sender=sender,
            recipient_role=recipient_role,
            production_skill_role=production_skill_role,
            objective=objective,
            why_now=why_now,
            scope=list(scope),
            constraints=list(constraints),
            confirmed_decisions=list(confirmed_decisions),
            open_questions=list(open_questions),
            expected_output_schema=expected_output_schema or {},
            acceptance_criteria=list(acceptance_criteria),
        )
        self.store.save_work_order(work_order)
        return work_order

    def delegate(
        self,
        run_id: str,
        *,
        sender: str,
        recipient_role: str,
        production_skill_role: ProductionSkillRole | None = None,
        objective: str,
        why_now: str,
        scope: Sequence[ArtifactRef] = (),
        constraints: Sequence[str] = (),
        confirmed_decisions: Sequence[str] = (),
        open_questions: Sequence[str] = (),
        expected_output_schema: dict[str, JsonValue] | None = None,
        acceptance_criteria: Sequence[str] = (),
    ) -> WorkOrder:
        return self.create_work_order(
            run_id,
            sender=sender,
            recipient_role=recipient_role,
            production_skill_role=production_skill_role,
            objective=objective,
            why_now=why_now,
            scope=scope,
            constraints=constraints,
            confirmed_decisions=confirmed_decisions,
            open_questions=open_questions,
            expected_output_schema=expected_output_schema,
            acceptance_criteria=acceptance_criteria,
        )

    def begin_work_order(self, work_order_id: str) -> WorkOrder:
        work_order = self._require_work_order(work_order_id)
        work_order.status = DelegationStatus.IN_PROGRESS
        work_order.updated_at = utc_now()
        self.store.save_work_order(work_order)
        return work_order

    def record_agent_result(
        self,
        work_order_id: str,
        *,
        status: AgentResultStatus,
        concise_summary: str,
        created_artifacts: Sequence[ArtifactRef] = (),
        updated_artifacts: Sequence[ArtifactRef] = (),
        findings: Sequence[str] = (),
        decisions: Sequence[str] = (),
        assumptions: Sequence[str] = (),
        unresolved_questions: Sequence[str] = (),
        evidence_refs: Sequence[ArtifactRef] = (),
        recommended_next_actions: Sequence[str] = (),
    ) -> AgentResult:
        work_order = self._require_work_order(work_order_id)
        result = AgentResult(
            id=_new_id("agent-result"),
            work_order_id=work_order_id,
            status=status,
            concise_summary=concise_summary,
            created_artifacts=list(created_artifacts),
            updated_artifacts=list(updated_artifacts),
            findings=list(findings),
            decisions=list(decisions),
            assumptions=list(assumptions),
            unresolved_questions=list(unresolved_questions),
            evidence_refs=list(evidence_refs),
            recommended_next_actions=list(recommended_next_actions),
        )
        validate_agent_result_contract(work_order, result)
        self.store.save_agent_result(result)
        return result

    def record_result(
        self,
        work_order_id: str,
        *,
        status: AgentResultStatus,
        concise_summary: str,
        created_artifacts: Sequence[ArtifactRef] = (),
        updated_artifacts: Sequence[ArtifactRef] = (),
        findings: Sequence[str] = (),
        decisions: Sequence[str] = (),
        assumptions: Sequence[str] = (),
        unresolved_questions: Sequence[str] = (),
        evidence_refs: Sequence[ArtifactRef] = (),
        recommended_next_actions: Sequence[str] = (),
    ) -> AgentResult:
        return self.record_agent_result(
            work_order_id,
            status=status,
            concise_summary=concise_summary,
            created_artifacts=created_artifacts,
            updated_artifacts=updated_artifacts,
            findings=findings,
            decisions=decisions,
            assumptions=assumptions,
            unresolved_questions=unresolved_questions,
            evidence_refs=evidence_refs,
            recommended_next_actions=recommended_next_actions,
        )

    def resume(self, run_id: str) -> AgentRunSnapshot:
        run = self._require_run(run_id)
        actions = self.store.list_actions(run_id)
        memory = self.store.get_run_memory(run_id) or RunMemory(run_id=run_id)
        completed = [
            action.id for action in actions if action.status == ActionStatus.SUCCEEDED
        ]
        memory.completed_action_ids = list(
            dict.fromkeys([*memory.completed_action_ids, *completed])
        )
        pending_work_orders = self.store.list_work_orders(
            run_id,
            statuses=(
                DelegationStatus.PENDING,
                DelegationStatus.IN_PROGRESS,
                DelegationStatus.NEEDS_ATTENTION,
            ),
        )
        return AgentRunSnapshot(
            run=run,
            current_plan=self.store.get_current_plan(run),
            actions=actions,
            memory=memory,
            pending_work_orders=pending_work_orders,
        )

    def snapshot(self, run_id: str) -> AgentRunSnapshot:
        return self.resume(run_id)

    def context(
        self,
        run_id: str,
        *,
        requested_artifact_refs: Sequence[ArtifactRef] = (),
        additional_constraints: Sequence[str] = (),
        experience_tags: Sequence[str] = (),
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ) -> ContextPack:
        if self._context_compiler is None:
            raise RuntimeError("AgentRuntime was created without a ContextCompiler")
        return self._context_compiler.compile(
            run_id,
            requested_artifact_refs=requested_artifact_refs,
            additional_constraints=additional_constraints,
            experience_tags=experience_tags,
            max_characters=max_characters,
        )

    def work_order_context(
        self,
        work_order_id: str,
        *,
        experience_tags: Sequence[str] = (),
        max_characters: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    ) -> ContextPack:
        if self._context_compiler is None:
            raise RuntimeError("AgentRuntime was created without a ContextCompiler")
        return self._context_compiler.compile_for_work_order(
            work_order_id,
            experience_tags=experience_tags,
            max_characters=max_characters,
        )

    def _require_run(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        return run

    def _require_work_order(self, work_order_id: str) -> WorkOrder:
        work_order = self.store.get_work_order(work_order_id)
        if work_order is None:
            raise KeyError(f"Unknown work order: {work_order_id}")
        return work_order

    @staticmethod
    def _log_run_status(
        run: AgentRun,
        *,
        previous_status: AgentRunStatus | None,
    ) -> None:
        log_event(
            "agent.run_status",
            run_id=run.id,
            project_id=run.project_id,
            status=run.status.value,
            previous_status=(
                previous_status.value if previous_status is not None else None
            ),
            root_agent=run.root_agent,
            unattended=run.unattended,
            execution_target=(
                run.execution_target.value if run.execution_target is not None else None
            ),
        )
