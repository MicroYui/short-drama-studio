"""Derived ProductionBoardView regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from short_drama.agent_runtime import (
    ActionKind,
    AgentRuntime,
    AgentRuntimeStore,
    ArtifactRef,
    PlanStep,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import (
    PlanningRunStatus,
    ProjectPlanningMetadata,
    StoryIssueLedgerEntry,
)
from short_drama.production_board import (
    ProductionBoardStepStatus,
    build_production_board,
)


class EmptyProjectStore:
    def get_project(self, _project_id: str):
        return None


class PlanningProjectStore:
    def __init__(self, owner_agent_run_id: str) -> None:
        self.document = build_dont_trust_me_fixture("2026-09-04T00:00:00Z")
        self.document.planning = ProjectPlanningMetadata(
            run_id="planning-board",
            status=PlanningRunStatus.RUNNING,
            owner_agent_run_id=owner_agent_run_id,
            planning_job_kind="CONTENT_PLANNING",
        )
        self.issue = StoryIssueLedgerEntry(
            issue_id="issue-payoff",
            semantic_cluster="payoff",
            state="OPEN",
            observation="The promised payoff still needs a visible consequence.",
        )

    def get_project(self, project_id: str):
        return self.document if project_id == self.document.project.id else None

    def get_planning_run(self, run_id: str):
        if run_id != "planning-board":
            return None
        return SimpleNamespace(issue_ledger=[self.issue])


def test_board_is_derived_from_plan_actions_work_orders_and_memory(
    tmp_path: Path,
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(
        project_id="project-board",
        objective="Prepare two independently reviewable assets.",
        root_agent="production-manager",
    )
    character_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="character",
        artifact_id="character-lead",
        version=1,
    )
    plan = runtime.add_plan(
        run.id,
        summary="Delegate the character first, then review it.",
        steps=[
            PlanStep(
                id="character-design",
                title="Design character reference",
                kind=ActionKind.DELEGATION,
                assigned_agent="character-designer",
                instructions="Produce a character reference proposal.",
                artifact_refs=[character_ref],
            ),
            PlanStep(
                id="character-review",
                title="Review character reference",
                kind=ActionKind.DELEGATION,
                assigned_agent="asset-reviewer",
                instructions="Review the proposed character reference.",
                depends_on=["character-design"],
                artifact_refs=[character_ref],
            ),
        ],
    )
    action = runtime.begin_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="character-designer",
        input_summary="Produce a character reference proposal.",
        plan_revision_id=plan.id,
        plan_step_id="character-design",
        artifact_refs=[character_ref],
    )
    work_order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="character-designer",
        objective="Produce a character reference proposal.",
        why_now="The downstream review depends on it.",
        scope=[character_ref],
    )
    action.work_order_id = work_order.id
    store.save_action(action)
    runtime.checkpoint(
        run.id,
        current_focus="Character reference work is delegated.",
        blockers=["Waiting for the specialist result."],
        next_actions=["Review character reference"],
        artifact_refs=[character_ref],
    )
    runtime.remember_decision(
        project_id=run.project_id,
        run_id=run.id,
        statement="Use one stable character identity.",
        rationale="The shots need visual continuity.",
        artifact_refs=[character_ref],
    )
    progress = runtime.append_work_order_progress(
        work_order.id,
        author="character-designer",
        summary="Character silhouette drafted; costume details remain.",
        artifact_refs=[character_ref],
    )

    board = build_production_board(runtime, EmptyProjectStore(), run.id)

    assert board.plan_revision_id == plan.id
    assert board.current_focus == "Character reference work is delegated."
    assert [step.status for step in board.steps] == [
        ProductionBoardStepStatus.IN_PROGRESS,
        ProductionBoardStepStatus.BLOCKED,
    ]
    assert board.steps[0].work_order_id == work_order.id
    assert board.work_orders[0].status.value == "PENDING"
    assert board.work_orders[0].progress_entries[-1] == progress
    assert board.work_orders[0].progress_entries[0].summary.startswith(
        "WorkOrder created:"
    )
    assert board.work_orders[0].artifact_refs == [character_ref]
    assert board.blockers == ["Waiting for the specialist result."]
    assert board.decisions[0].statement == "Use one stable character identity."
    assert character_ref in board.artifact_refs


def test_board_marks_unattempted_dependency_free_steps_ready(tmp_path: Path) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(
        project_id="project-ready-board",
        objective="Inspect the current project.",
        root_agent="production-manager",
    )
    runtime.add_plan(
        run.id,
        summary="Inspect only.",
        steps=[
            PlanStep(
                id="inspect",
                title="Inspect project",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="production-manager",
                tool_name="project.get",
                instructions="Read the project.",
            )
        ],
    )

    board = build_production_board(runtime, EmptyProjectStore(), run.id)

    assert board.steps[0].status == ProductionBoardStepStatus.READY
    assert board.next_actions == ["Inspect project"]


def test_board_projects_canonical_project_artifacts_and_story_issue_ledger(
    tmp_path: Path,
) -> None:
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = AgentRuntime(store)
    project_id = "project-dont-trust-me"
    run = runtime.create_run(
        project_id=project_id,
        objective="Inspect current production state.",
        root_agent="production-manager",
    )
    projects = PlanningProjectStore(run.id)

    board = build_production_board(runtime, projects, run.id)

    assert board.issue_ledger == [projects.issue]
    assert any(
        ref.artifact_type == "project" and ref.artifact_id == project_id
        for ref in board.artifact_refs
    )

    unrelated_run = runtime.create_run(
        project_id=project_id,
        objective="Inspect without adopting another run's planning cursor.",
        root_agent="production-manager",
    )
    unrelated_board = build_production_board(
        runtime,
        projects,
        unrelated_run.id,
    )
    assert unrelated_board.issue_ledger == []
