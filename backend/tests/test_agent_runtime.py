"""Agent runtime, memory, context, and delegation regressions."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.agent_runtime import (
    ActionKind,
    ActionRecord,
    ActionStatus,
    AgentResultContractError,
    AgentResultStatus,
    AgentRun,
    AgentRunStatus,
    AgentRuntime,
    AgentRuntimeStore,
    ArtifactRef,
    ArtifactSummary,
    ContextBoundary,
    ContextCompiler,
    ContextManifestResolutionError,
    ContextPack,
    DelegationStatus,
    ExperienceConfidence,
    PlanRevision,
    PlanStep,
    ProjectMemory,
    WorkOrderNextOwner,
)
from short_drama.agent_runtime.models import utc_now
from short_drama.models import ProductionExecutionTarget
from short_drama.observability import LOGGER_NAME


class FakeArtifactResolver:
    def __init__(self, summaries: list[ArtifactSummary]) -> None:
        self._summaries = {item.ref.identity(): item for item in summaries}
        self.requested: list[ArtifactRef] = []

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        self.requested.append(ref)
        return self._summaries.get(ref.identity())


def test_context_boundary_allows_guidance_without_neighboring_artifact() -> None:
    boundary = ContextBoundary(
        relationship="opening-state",
        guidance="The first shot begins before anyone has reacted.",
    )

    assert boundary.artifact_refs == []


def test_context_boundary_rejects_an_entirely_empty_handoff() -> None:
    with pytest.raises(ValidationError, match="artifact references or guidance"):
        ContextBoundary(relationship="opening-state")


def _runtime(
    database_path: Path,
    resolver: FakeArtifactResolver | None = None,
) -> tuple[AgentRuntimeStore, AgentRuntime, ContextCompiler | None]:
    store = AgentRuntimeStore(database_path)
    compiler = None if resolver is None else ContextCompiler(store, resolver)
    return store, AgentRuntime(store, compiler), compiler


def _context_content_characters(context: ContextPack) -> int:
    values: list[str] = [context.objective]
    values.extend(
        value
        for value in (context.project_summary, context.run_focus)
        if value is not None
    )
    values.extend(context.constraints)
    values.extend(context.blockers)
    values.extend(context.open_questions)
    values.extend(context.next_actions)
    for decision in context.decisions:
        values.extend((decision.statement, decision.rationale))
    for experience in context.experiences:
        values.extend(
            (experience.summary, experience.applicability, experience.evidence)
        )
    for artifact in context.artifacts:
        if artifact.title is not None:
            values.append(artifact.title)
        values.append(artifact.summary)
    for action in context.recent_actions:
        values.append(action.summary)
        if action.agent_result is None:
            continue
        values.append(action.agent_result.concise_summary)
        values.append(
            json.dumps(action.agent_result.deliverable, ensure_ascii=False)
        )
        values.extend(action.agent_result.findings)
        values.extend(
            ref.model_dump_json() for ref in action.agent_result.evidence_refs
        )
    return sum(len(value) for value in values)


def test_dynamic_plan_graph_exposes_only_ready_steps(tmp_path: Path) -> None:
    _store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-1",
        objective="改好第三场并只重算受影响镜头",
        root_agent="production-manager",
        active_skill_ids=["format.douyin-short-drama"],
    )
    scene_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="scene",
        artifact_id="scene-3",
        version=2,
    )
    plan = runtime.add_plan(
        run.id,
        summary="先生成局部补丁，再交给导演复核。",
        steps=[
            PlanStep(
                id="patch-scene",
                title="修改第三场",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="scene-writer",
                tool_name="script.patch-scenes",
                arguments={"scene_ids": ["scene-3"], "tone": "restrained"},
                instructions="仅修改第三场的表演与台词强度。",
                artifact_refs=[scene_ref],
            ),
            PlanStep(
                id="director-review",
                title="导演复核",
                kind=ActionKind.DELEGATION,
                assigned_agent="scene-director",
                instructions="检查与前后场连续性。",
                depends_on=["patch-scene"],
                artifact_refs=[scene_ref],
            ),
        ],
    )

    assert [step.id for step in runtime.ready_steps(run.id)] == ["patch-scene"]
    action = runtime.start_action(
        run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="scene-writer",
        input_summary="对第三场应用局部补丁",
        plan_revision_id=plan.id,
        plan_step_id="patch-scene",
        tool_name="script.patch-scenes",
        artifact_refs=[scene_ref],
    )
    assert runtime.ready_steps(run.id) == []

    runtime.complete_action(
        action.id,
        output_summary="第三场已生成新版本",
        artifact_refs=[scene_ref.model_copy(update={"version": 3})],
    )
    assert [step.id for step in runtime.ready_steps(run.id)] == ["director-review"]


def test_failed_step_waits_for_replan_instead_of_implicit_retry(tmp_path: Path) -> None:
    _store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-failed-step",
        objective="Try a provider action once, then let the Agent decide what follows.",
        root_agent="production-manager",
    )
    plan = runtime.add_plan(
        run.id,
        summary="One attempt",
        steps=[
            PlanStep(
                id="provider-attempt",
                title="Provider attempt",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="video-producer",
                tool_name="video.generate",
                instructions="Attempt the selected provider route.",
            )
        ],
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="video-producer",
        input_summary="Attempt provider route",
        plan_revision_id=plan.id,
        plan_step_id="provider-attempt",
        tool_name="video.generate",
    )
    runtime.fail_action(action.id, error_message="Provider request failed")

    assert runtime.ready_steps(run.id) == []


def test_agent_runtime_logs_structured_status_and_finished_action_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-observability",
        objective="secret objective text must not enter logs",
        root_agent="production-manager",
        unattended=True,
        execution_target=ProductionExecutionTarget.MOCK,
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="video-producer",
        input_summary="secret input and provider credential must not enter logs",
        tool_name="video.generate",
    )
    assert action.started_at is not None
    action.started_at -= timedelta(seconds=1)
    store.save_action(action)

    finished = runtime.finish_action(
        action.id,
        status=ActionStatus.FAILED,
        output_summary="secret provider output must not enter logs",
        error_message="secret provider failure body must not enter logs",
        error_kind="ProviderOperationError",
    )
    runtime.set_run_status(run.id, AgentRunStatus.WAITING)

    messages = [
        record.getMessage() for record in caplog.records if record.name == LOGGER_NAME
    ]
    events = [json.loads(message) for message in messages]
    assert [event["event"] for event in events] == [
        "agent.run_status",
        "agent.tool_finished",
        "agent.run_status",
    ]
    assert {
        key: events[0][key]
        for key in (
            "project_id",
            "run_id",
            "status",
            "previous_status",
            "root_agent",
            "unattended",
            "execution_target",
        )
    } == {
        "project_id": "project-observability",
        "run_id": run.id,
        "status": "RUNNING",
        "previous_status": None,
        "root_agent": "production-manager",
        "unattended": True,
        "execution_target": "MOCK",
    }
    assert events[1]["action_id"] == action.id
    assert events[1]["action_kind"] == "TOOL_CALL"
    assert events[1]["status"] == "FAILED"
    assert events[1]["tool_name"] == "video.generate"
    assert events[1]["error_kind"] == "ProviderOperationError"
    assert events[1]["duration_ms"] >= 1000
    assert events[2]["previous_status"] == "RUNNING"
    assert events[2]["status"] == "WAITING"
    assert finished.duration_ms is not None and finished.duration_ms >= 1000
    assert finished.error_kind == "ProviderOperationError"
    assert all("\n" not in message for message in messages)
    serialized_events = "\n".join(messages)
    assert "secret objective" not in serialized_events
    assert "secret input" not in serialized_events
    assert "secret provider output" not in serialized_events
    assert "secret provider failure" not in serialized_events


def test_plan_revisions_and_run_checkpoint_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    _store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-2",
        objective="完成第一镜",
        root_agent="production-manager",
        unattended=True,
        execution_target=ProductionExecutionTarget.MOCK,
        render_export=False,
    )
    initial = runtime.add_plan(
        run.id,
        summary="初始方案",
        steps=[
            PlanStep(
                id="generate",
                title="生成镜头",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="video-producer",
                tool_name="video.generate",
                arguments={"shot_id": "shot-1", "candidate_count": 2},
                instructions="生成两个候选。",
            )
        ],
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="video-producer",
        input_summary="生成镜头候选",
        plan_revision_id=initial.id,
        plan_step_id="generate",
        tool_name="video.generate",
    )
    runtime.complete_action(action.id, output_summary="生成了两个 Take")
    replacement = runtime.replace_plan(
        run.id,
        summary="根据已有 Take 直接进入比较",
        goal_complete=True,
        steps=[
            PlanStep(
                id="compare",
                title="比较 Take",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="video-reviewer",
                tool_name="take.compare",
                arguments={"shot_id": "shot-1"},
                instructions="比较可见质量并给出选择。",
            )
        ],
    )
    runtime.checkpoint(
        run.id,
        current_focus="比较两个 Take",
        next_actions=["Call take.compare"],
    )

    reopened_store, reopened_runtime, _compiler = _runtime(database_path)
    snapshot = reopened_runtime.snapshot(run.id)

    assert snapshot.current_plan is not None
    assert snapshot.current_plan.id == replacement.id
    assert snapshot.current_plan.revision == 2
    assert snapshot.current_plan.goal_complete is True
    assert snapshot.current_plan.steps[0].arguments == {"shot_id": "shot-1"}
    assert snapshot.memory.current_focus == "比较两个 Take"
    assert snapshot.memory.completed_action_ids == [action.id]
    assert snapshot.run.unattended is True
    assert snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert snapshot.run.render_export is False
    assert reopened_store.list_resumable_runs("project-2") == [snapshot.run]

    legacy_run_payload = snapshot.run.model_dump()
    legacy_run_payload.pop("unattended")
    legacy_run_payload.pop("execution_target")
    legacy_run_payload.pop("render_export")
    legacy_run = AgentRun.model_validate(legacy_run_payload)
    assert legacy_run.unattended is False
    assert legacy_run.execution_target is None
    assert legacy_run.render_export is None

    legacy_payload = initial.model_dump()
    legacy_payload.pop("goal_complete")
    assert PlanRevision.model_validate(legacy_payload).goal_complete is False

    legacy_action_payload = action.model_dump()
    legacy_action_payload.pop("duration_ms")
    legacy_action_payload.pop("error_kind")
    legacy_action = ActionRecord.model_validate(legacy_action_payload)
    assert legacy_action.duration_ms is None
    assert legacy_action.error_kind is None


def test_context_compiler_uses_artifact_refs_and_bounded_summaries(tmp_path: Path) -> None:
    scene_ref = ArtifactRef(
        project_id="project-3",
        artifact_type="scene",
        artifact_id="scene-3",
        version=4,
    )
    missing_ref = ArtifactRef(
        project_id="project-3",
        artifact_type="shot",
        artifact_id="shot-9",
    )
    resolver = FakeArtifactResolver(
        [
            ArtifactSummary(
                ref=scene_ref,
                title="第三场",
                summary="林夏压低情绪拒绝交出钥匙，下一场仍持有钥匙。",
            )
        ]
    )
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    run = runtime.create_run(
        project_id="project-3",
        objective="修改第三场",
        root_agent="production-manager",
        active_skill_ids=["format.douyin-short-drama"],
        unattended=True,
        execution_target=ProductionExecutionTarget.REAL_H3,
        render_export=False,
    )
    store.save_project_memory(
        ProjectMemory(
            project_id=run.project_id,
            summary="一部竖屏情感短剧，主线是林夏保护家人。",
            constraints=["保留人物关系", "不改其他场次"],
            artifact_refs=[scene_ref],
        )
    )
    runtime.checkpoint(
        run.id,
        current_focus="仅修改第三场",
        open_questions=["是否保留末句台词？"],
        next_actions=["生成 patch"],
        artifact_refs=[scene_ref, missing_ref],
    )
    runtime.remember_decision(
        project_id=run.project_id,
        run_id=run.id,
        statement="其他场次不变",
        rationale="用户明确要求局部修改",
        artifact_refs=[scene_ref],
    )
    runtime.remember_experience(
        summary="先修改上游场景，再重算受影响镜头",
        applicability="局部剧本修改",
        evidence="本项目上一次修改仅影响两个镜头",
        confidence=ExperienceConfidence.MEDIUM,
        tags=["partial-edit"],
        project_id=run.project_id,
        skill_id="format.douyin-short-drama",
        artifact_refs=[scene_ref],
    )

    context = runtime.context(
        run.id,
        requested_artifact_refs=[scene_ref],
        additional_constraints=["不新增角色"],
        experience_tags=["partial-edit"],
    )

    assert context.project_summary == "一部竖屏情感短剧，主线是林夏保护家人。"
    assert context.unattended is True
    assert context.execution_target == ProductionExecutionTarget.REAL_H3
    assert context.render_export is False
    assert context.run_focus == "仅修改第三场"
    assert [item.ref for item in context.artifacts] == [scene_ref]
    assert context.unresolved_artifact_refs == [missing_ref]
    assert len(resolver.requested) == 2
    assert context.constraints == ["保留人物关系", "不改其他场次", "不新增角色"]
    assert context.decisions[0].statement == "其他场次不变"
    assert context.experiences[0].applicability == "局部剧本修改"


def test_unresolved_artifact_refs_require_exact_canonical_identity(
    tmp_path: Path,
) -> None:
    canonical_ref = ArtifactRef(
        project_id="project-exact-artifact-ref",
        artifact_type="bible_version",
        artifact_id="project-exact-artifact-ref-bible-v2",
        version=2,
    )
    invented_alias = ArtifactRef(
        project_id=canonical_ref.project_id,
        artifact_type="project_bible_version",
        artifact_id=canonical_ref.artifact_id,
        version=canonical_ref.version,
    )
    resolver = FakeArtifactResolver(
        [
            ArtifactSummary(
                ref=canonical_ref,
                title="Project Bible v2",
                summary="Canonical project bible.",
            )
        ]
    )
    _store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    run = runtime.create_run(
        project_id=canonical_ref.project_id,
        objective="Validate exact artifact references",
        root_agent="production-manager",
    )

    unresolved = runtime.unresolved_artifact_refs(
        run.id,
        [canonical_ref, invented_alias, canonical_ref],
    )

    assert unresolved == [invented_alias]
    assert resolver.requested == [canonical_ref, invented_alias]


def test_context_recalls_project_decisions_with_current_run_first(
    tmp_path: Path,
) -> None:
    resolver = FakeArtifactResolver([])
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    previous_run = runtime.create_run(
        project_id="project-shared-decisions",
        objective="Previous edit",
        root_agent="production-manager",
    )
    current_run = runtime.create_run(
        project_id="project-shared-decisions",
        objective="Current edit",
        root_agent="production-manager",
    )
    current = runtime.remember_decision(
        project_id=current_run.project_id,
        run_id=current_run.id,
        statement="Keep the current ending",
        rationale="The user confirmed it in this run",
    )
    project_wide = runtime.remember_decision(
        project_id=current_run.project_id,
        statement="Use a vertical frame",
        rationale="Project-wide format decision",
    )
    previous = runtime.remember_decision(
        project_id=previous_run.project_id,
        run_id=previous_run.id,
        statement="Keep the protagonist reserved",
        rationale="Established during the previous edit",
    )

    context = runtime.context(current_run.id)

    assert [item.id for item in context.decisions] == [
        current.id,
        project_wide.id,
        previous.id,
    ]
    assert store.list_decisions(current_run.project_id) == [project_wide]


def test_context_character_budget_truncates_large_summaries_and_actions(
    tmp_path: Path,
) -> None:
    artifact_ref = ArtifactRef(
        project_id="project-context-budget",
        artifact_type="scene",
        artifact_id="scene-1",
    )
    resolver = FakeArtifactResolver(
        [
            ArtifactSummary(
                ref=artifact_ref,
                title="Opening scene",
                summary="artifact " * 1_000,
            )
        ]
    )
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    run = runtime.create_run(
        project_id=artifact_ref.project_id,
        objective="Review the opening scene",
        root_agent="production-manager",
    )
    store.save_project_memory(
        ProjectMemory(
            project_id=run.project_id,
            summary="project " * 1_000,
            artifact_refs=[artifact_ref],
        )
    )
    runtime.checkpoint(
        run.id,
        current_focus="Review only the opening",
        artifact_refs=[artifact_ref],
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.CHECKPOINT,
        agent_id="production-manager",
        input_summary="Inspect the opening",
    )
    runtime.complete_action(action.id, output_summary="action " * 1_000)

    context = runtime.context(run.id, max_characters=10_000)

    assert _context_content_characters(context) <= 10_000
    assert context.project_summary is not None
    assert context.project_summary.endswith("…")
    assert context.artifacts[0].summary.endswith("…")
    assert context.recent_actions[0].summary.endswith("…")
    with pytest.raises(ValueError, match="at least 256"):
        runtime.context(run.id, max_characters=255)


def test_context_budget_keeps_latest_actions_before_decisions_in_time_order(
    tmp_path: Path,
) -> None:
    _store, runtime, _compiler = _runtime(
        tmp_path / "runtime.sqlite3",
        FakeArtifactResolver([]),
    )
    run = runtime.create_run(
        project_id="project-latest-action-budget",
        objective="Determine whether the completed Storyboard needs another action.",
        root_agent="production-manager",
    )
    for index in range(3):
        runtime.remember_decision(
            project_id=run.project_id,
            run_id=run.id,
            statement=f"decision-{index} " * 200,
            rationale=f"rationale-{index} " * 200,
        )

    actions = []
    for index in range(4):
        action = runtime.start_action(
            run.id,
            kind=ActionKind.CHECKPOINT,
            agent_id="production-manager",
            input_summary=f"action-{index}",
        )
        artifact_refs = []
        if index == 3:
            artifact_refs = [
                ArtifactRef(
                    project_id=run.project_id,
                    artifact_type="storyboard",
                    artifact_id="production-document-storyboard",
                    version=1,
                )
            ]
        actions.append(
            runtime.complete_action(
                action.id,
                output_summary=f"completed-action-{index} " * 100,
                artifact_refs=artifact_refs,
            )
        )

    context = runtime.context(run.id, max_characters=2_600)
    retained_ids = [item.id for item in context.recent_actions]

    assert retained_ids == [
        item.id for item in actions if item.id in set(retained_ids)
    ]
    assert context.recent_actions[-1].id == actions[-1].id
    assert context.recent_actions[-1].status == ActionStatus.SUCCEEDED
    assert context.recent_actions[-1].artifact_refs == [
        ArtifactRef(
            project_id=run.project_id,
            artifact_type="storyboard",
            artifact_id="production-document-storyboard",
            version=1,
        )
    ]
    assert context.decisions == []
    assert _context_content_characters(context) <= 2_600


def test_recent_delegation_action_carries_bounded_agent_result_semantics(
    tmp_path: Path,
) -> None:
    store, runtime, _compiler = _runtime(
        tmp_path / "runtime.sqlite3",
        FakeArtifactResolver([]),
    )
    run = runtime.create_run(
        project_id="project-result-context",
        objective="Decide whether the storyboard draft is ready to publish.",
        root_agent="production-manager",
    )
    draft_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="storyboard_draft",
        artifact_id="storyboard-draft-1",
        version=4,
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="storyboard-verifier",
        objective="Read the exact draft and return its semantic conclusion.",
        why_now="All planned ClipGroups have been committed.",
        scope=[draft_ref],
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="storyboard-verifier",
        input_summary="Verify the complete storyboard draft.",
        work_order_id=order.id,
    )
    runtime.record_result(
        order.id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="The draft needs one continuity repair.",
        deliverable={
            "verification_status": "NEEDS_REVISION",
            "affected_clip_group_ids": ["clip-group-2"],
        },
        findings=["The tray changes hands without a visible pickup action."],
        evidence_refs=[draft_ref],
    )
    runtime.complete_action(
        action.id,
        output_summary="Verifier returned a semantic result.",
        artifact_refs=[draft_ref],
    )

    context = runtime.context(run.id, max_characters=6_000)

    assert len(context.recent_actions) == 1
    digest = context.recent_actions[0].agent_result
    assert digest is not None
    assert digest.status == AgentResultStatus.COMPLETED
    assert digest.concise_summary == "The draft needs one continuity repair."
    assert digest.deliverable == {
        "verification_status": "NEEDS_REVISION",
        "affected_clip_group_ids": ["clip-group-2"],
    }
    assert digest.findings == [
        "The tray changes hands without a visible pickup action."
    ]
    assert digest.evidence_refs == [draft_ref]
    assert _context_content_characters(context) <= 6_000
    assert store.get_agent_result(order.id) is not None


def test_recent_action_agent_result_respects_the_shared_character_budget(
    tmp_path: Path,
) -> None:
    _store, runtime, _compiler = _runtime(
        tmp_path / "runtime.sqlite3",
        FakeArtifactResolver([]),
    )
    run = runtime.create_run(
        project_id="project-large-result-context",
        objective="Read one large verifier result without overflowing context.",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="storyboard-verifier",
        objective="Return the semantic verification result.",
        why_now="The storyboard draft is complete.",
    )
    action = runtime.start_action(
        run.id,
        kind=ActionKind.DELEGATION,
        agent_id="storyboard-verifier",
        input_summary="Read the complete draft.",
        work_order_id=order.id,
    )
    runtime.record_result(
        order.id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="summary " * 1_000,
        deliverable={"report": "report " * 2_000},
        findings=["finding " * 1_000],
    )
    runtime.complete_action(
        action.id,
        output_summary="Verifier result persisted.",
    )

    context = runtime.context(run.id, max_characters=4_000)
    digest = context.recent_actions[0].agent_result

    assert digest is not None
    assert digest.concise_summary.endswith("…")
    assert isinstance(digest.deliverable["report"], str)
    assert digest.deliverable["report"].endswith("…")
    assert _context_content_characters(context) <= 4_000


def test_structured_delegation_round_trip_and_work_order_context(tmp_path: Path) -> None:
    shot_ref = ArtifactRef(
        project_id="project-4",
        artifact_type="shot",
        artifact_id="shot-2",
        version=1,
    )
    resolver = FakeArtifactResolver(
        [ArtifactSummary(ref=shot_ref, title="Shot 2", summary="两人室内对话中景。")]
    )
    store, runtime, compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    assert compiler is not None
    run = runtime.create_run(
        project_id="project-4",
        objective="完成第二镜导演规划",
        root_agent="production-manager",
    )
    order = runtime.delegate(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="为第二镜给出镜头调度",
        why_now="剧本和角色资产已就绪",
        scope=[shot_ref],
        constraints=["保留双人相对位置"],
        confirmed_decisions=["使用中景"],
        expected_output_schema={
            "type": "object",
            "required": ["decisions", "updated_artifacts"],
            "properties": {
                "decisions": {"type": "array", "minItems": 1},
                "updated_artifacts": {"type": "array", "minItems": 1},
            },
        },
        acceptance_criteria=["调度可被 shot.update 直接消费"],
    )
    runtime.begin_work_order(order.id)
    context = compiler.compile_for_work_order(order.id)
    assert context.objective == "为第二镜给出镜头调度"
    assert context.constraints == ["保留双人相对位置"]
    assert context.artifacts[0].ref == shot_ref

    result = runtime.record_result(
        order.id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="已给出先静后推的调度方案",
        updated_artifacts=[shot_ref.model_copy(update={"version": 2})],
        decisions=["台词前保持静止，拒绝时缓推"],
        evidence_refs=[shot_ref],
    )

    saved_order = store.get_work_order(order.id)
    assert saved_order is not None
    assert saved_order.status == DelegationStatus.COMPLETED
    assert store.get_agent_result(order.id) == result
    assert runtime.snapshot(run.id).pending_work_orders == []


def test_work_order_manifest_loads_only_explicit_artifacts_decisions_and_tags(
    tmp_path: Path,
) -> None:
    required_ref = ArtifactRef(
        project_id="project-manifest",
        artifact_type="scene",
        artifact_id="scene-2",
        version=3,
    )
    previous_ref = ArtifactRef(
        project_id="project-manifest",
        artifact_type="scene",
        artifact_id="scene-1",
        version=3,
    )
    ambient_ref = ArtifactRef(
        project_id="project-manifest",
        artifact_type="scene",
        artifact_id="scene-9",
        version=3,
    )
    resolver = FakeArtifactResolver(
        [
            ArtifactSummary(ref=required_ref, summary="The selected scene."),
            ArtifactSummary(ref=previous_ref, summary="The preceding scene."),
            ArtifactSummary(ref=ambient_ref, summary="Unrelated run memory."),
        ]
    )
    database_path = tmp_path / "runtime.sqlite3"
    store, runtime, compiler = _runtime(database_path, resolver)
    assert compiler is not None
    run = runtime.create_run(
        project_id=required_ref.project_id,
        objective="Review the whole project",
        root_agent="production-manager",
        active_skill_ids=["method.fast-payoff"],
        unattended=True,
    )
    runtime.checkpoint(
        run.id,
        current_focus="An unrelated run-level focus",
        artifact_refs=[ambient_ref],
    )
    inherited = runtime.remember_decision(
        project_id=run.project_id,
        run_id=run.id,
        statement="Keep the accepted reveal",
        rationale="The user accepted it",
        artifact_refs=[required_ref],
    )
    runtime.remember_decision(
        project_id=run.project_id,
        statement="Unrelated global decision",
        rationale="Must not leak into this handoff",
    )
    runtime.remember_experience(
        summary="Land the reaction immediately after the reveal",
        applicability="Fast payoff scenes",
        evidence="A prior scene review",
        confidence=ExperienceConfidence.MEDIUM,
        tags=["reaction-timing"],
        skill_id="method.fast-payoff",
        project_id=run.project_id,
    )
    runtime.remember_experience(
        summary="Unrelated camera lesson",
        applicability="Landscape montage",
        evidence="Another project unit",
        confidence=ExperienceConfidence.MEDIUM,
        tags=["landscape"],
        project_id=run.project_id,
    )

    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-writer",
        objective="Tighten scene 2 without changing its reveal",
        why_now="Scene 1 is accepted and scene 2 is ready for a local pass",
        scope=[required_ref],
        adjacent_boundaries=[
            ContextBoundary(
                relationship="previous-scene",
                artifact_refs=[previous_ref],
                guidance="Preserve the entry state inherited from scene 1",
            )
        ],
        inherited_decision_ids=[inherited.id],
        confirmed_decisions=[inherited.statement],
        experience_tags=["reaction-timing"],
        constraints=["Do not rewrite scene 1"],
        expected_output_schema={"type": "object"},
        acceptance_criteria=["Return a canonical scene artifact reference"],
    )
    runtime.set_run_active_skills(run.id, ["method.slow-burn"])
    context = compiler.compile_for_work_order(order.id)

    assert order.context_manifest is not None
    assert order.context_manifest.required_artifacts == [required_ref]
    assert order.context_manifest.inherited_decision_ids == [inherited.id]
    assert order.context_manifest.selected_skill_ids == ["method.fast-payoff"]
    assert resolver.requested == [required_ref, previous_ref]
    assert [artifact.ref for artifact in context.artifacts] == [
        required_ref,
        previous_ref,
    ]
    assert [decision.id for decision in context.decisions] == [inherited.id]
    assert [experience.summary for experience in context.experiences] == [
        "Land the reaction immediately after the reveal"
    ]
    assert context.active_skill_ids == ["method.fast-payoff"]
    assert context.unattended is True
    assert context.project_summary is None
    assert context.run_focus is None
    assert context.recent_actions == []
    assert context.work_order_id == order.id
    assert context.context_manifest_id == order.context_manifest.id

    reopened = AgentRuntimeStore(database_path).get_work_order(order.id)
    assert reopened is not None
    assert reopened.context_manifest == order.context_manifest


def test_manifest_preserves_boundary_semantics_while_loading_duplicate_ref_once(
    tmp_path: Path,
) -> None:
    scene_ref = ArtifactRef(
        project_id="project-shared-boundary",
        artifact_type="scene",
        artifact_id="scene-1",
        version=2,
    )
    resolver = FakeArtifactResolver(
        [ArtifactSummary(ref=scene_ref, summary="The selected cafeteria scene.")]
    )
    store, runtime, compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    assert compiler is not None
    run = runtime.create_run(
        project_id=scene_ref.project_id,
        objective="Storyboard the selected scene",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="storyboard-director",
        objective="Create the first clip group",
        why_now="The storyboard draft is ready",
        scope=[scene_ref],
        adjacent_boundaries=[
            ContextBoundary(
                relationship="opening-state",
                guidance="No prior clip exists; begin from the untouched lunch queue.",
            ),
            ContextBoundary(
                relationship="outgoing-state",
                artifact_refs=[scene_ref],
                guidance="End before the calculator reveals its result.",
            ),
        ],
    )

    context = compiler.compile_for_work_order(order.id)

    assert order.context_manifest is not None
    assert [item.relationship for item in order.context_manifest.adjacent_boundaries] == [
        "opening-state",
        "outgoing-state",
    ]
    assert resolver.requested == [scene_ref]
    assert [item.ref for item in context.artifacts] == [scene_ref]
    assert store.get_work_order(order.id) == order


@pytest.mark.parametrize(
    "artifact_type",
    ["shot", "h3_route_plan", "h3_route_asset_preparation"],
)
def test_exact_context_manifest_rejects_missing_or_stale_versions(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    stale_ref = ArtifactRef(
        project_id="project-exact-version",
        artifact_type=artifact_type,
        artifact_id="artifact-1",
        version=2,
    )
    resolver = FakeArtifactResolver([])
    _store, runtime, compiler = _runtime(tmp_path / "runtime.sqlite3", resolver)
    assert compiler is not None
    run = runtime.create_run(
        project_id=stale_ref.project_id,
        objective="Review one exact shot revision",
        root_agent="production-manager",
    )

    with pytest.raises(ValueError, match="explicit version"):
        runtime.create_work_order(
            run.id,
            sender="production-manager",
            recipient_role="scene-director",
            objective="Review shot 1",
            why_now="The shot changed",
            scope=[stale_ref.model_copy(update={"version": None})],
        )

    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Review shot 1",
        why_now="The shot changed",
        scope=[stale_ref],
    )
    with pytest.raises(ContextManifestResolutionError, match="unresolved artifact"):
        compiler.compile_for_work_order(order.id)


def test_work_order_progress_is_append_only_and_survives_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-progress",
        objective="Complete a delegated scene pass",
        root_agent="production-manager",
    )
    scene_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="scene",
        artifact_id="scene-1",
        version=1,
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-writer",
        objective="Revise scene 1",
        why_now="The critique is available",
        scope=[scene_ref],
    )

    second = runtime.append_work_order_progress(
        order.id,
        author="scene-writer",
        summary="Loaded the exact scene and inherited context.",
    )
    third = runtime.append_work_order_progress(
        order.id,
        author="scene-writer",
        summary="Drafted the local revision.",
        artifact_refs=[scene_ref],
    )

    entries = store.list_work_order_progress(order.id)
    assert [entry.sequence for entry in entries] == [1, 2, 3]
    assert entries[1:] == [second, third]
    reopened = AgentRuntimeStore(database_path)
    assert reopened.list_work_order_progress(order.id) == entries


def test_pre_manifest_work_order_records_remain_readable(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    _store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-legacy-order",
        objective="Resume a legacy delegation",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-writer",
        objective="Resume scene work",
        why_now="The application restarted",
    )
    legacy_json = order.model_dump_json(exclude={"context_manifest"})
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agent_work_orders SET record_json = ? WHERE id = ?",
            (legacy_json, order.id),
        )

    restored = AgentRuntimeStore(database_path).get_work_order(order.id)

    assert restored is not None
    assert restored.context_manifest is not None
    assert restored.context_manifest.work_order_id == order.id
    assert restored.context_manifest.objective == order.objective
    assert not restored.context_manifest.exact_artifact_versions


def test_completed_result_contract_checks_schema_and_completion_evidence(
    tmp_path: Path,
) -> None:
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-result-contract",
        objective="Collect a structured director finding",
        root_agent="production-manager",
    )
    schema_order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Return at least one finding",
        why_now="The scene draft is ready",
        expected_output_schema={
            "type": "object",
            "required": ["findings"],
            "properties": {
                "findings": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                }
            },
        },
    )
    with pytest.raises(AgentResultContractError, match="at least 1 items"):
        runtime.record_result(
            schema_order.id,
            status=AgentResultStatus.COMPLETED,
            concise_summary="Review complete",
        )
    assert store.get_agent_result(schema_order.id) is None

    criteria_order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Confirm continuity",
        why_now="The neighboring shots are available",
        acceptance_criteria=["Return a concrete continuity conclusion"],
    )
    with pytest.raises(AgentResultContractError, match="acceptance_criteria"):
        runtime.record_result(
            criteria_order.id,
            status=AgentResultStatus.COMPLETED,
            concise_summary="Done",
        )
    partial = runtime.record_result(
        criteria_order.id,
        status=AgentResultStatus.PARTIAL,
        concise_summary="One camera intention is still missing",
        unresolved_questions=["Should the camera remain static?"],
    )
    assert partial.status == AgentResultStatus.PARTIAL

    unresolved_order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Settle the camera intention",
        why_now="The user supplied the missing preference",
    )
    with pytest.raises(AgentResultContractError, match="unresolved_questions"):
        runtime.record_result(
            unresolved_order.id,
            status=AgentResultStatus.COMPLETED,
            concise_summary="Use a static composition",
            findings=["The static frame preserves tension"],
            unresolved_questions=["Should it move after the final line?"],
        )


def test_completed_result_can_return_a_task_specific_structured_deliverable(
    tmp_path: Path,
) -> None:
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-deliverable",
        objective="Collect one exact storyboard handoff.",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="storyboard-director",
        objective="Return the persisted ClipGroup identity.",
        why_now="The append Tool has completed.",
        expected_output_schema={
            "type": "object",
            "required": ["draft_version_id", "clip_group_id"],
            "properties": {
                "draft_version_id": {"type": "string"},
                "clip_group_id": {"type": "string"},
            },
        },
        acceptance_criteria=["Name the exact persisted group."],
    )

    result = runtime.record_result(
        order.id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="The ClipGroup was persisted.",
        deliverable={
            "draft_version_id": "draft-v2",
            "clip_group_id": "clip-group-1",
        },
    )

    assert result.deliverable["draft_version_id"] == "draft-v2"
    assert store.get_agent_result(order.id) == result


def test_legacy_partial_result_restores_leader_owner_from_persisted_result(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-legacy-partial",
        objective="Resume an old partial handoff.",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="story-reviewer",
        objective="Review one bounded story issue.",
        why_now="The prior process stopped after persisting a partial result.",
    )
    runtime.record_result(
        order.id,
        status=AgentResultStatus.PARTIAL,
        concise_summary="The active plan needs revision.",
        unresolved_questions=["Which replacement step should the Leader choose?"],
        next_owner=WorkOrderNextOwner.LEADER_REPLAN,
    )

    with sqlite3.connect(database_path) as connection:
        work_order_row = connection.execute(
            "SELECT record_json FROM agent_work_orders WHERE id = ?",
            (order.id,),
        ).fetchone()
        result_row = connection.execute(
            "SELECT record_json FROM agent_results WHERE work_order_id = ?",
            (order.id,),
        ).fetchone()
        assert work_order_row is not None
        assert result_row is not None
        work_order_payload = json.loads(str(work_order_row[0]))
        result_payload = json.loads(str(result_row[0]))
        del work_order_payload["next_owner"]
        del result_payload["next_owner"]
        connection.execute(
            "UPDATE agent_work_orders SET record_json = ? WHERE id = ?",
            (json.dumps(work_order_payload), order.id),
        )
        connection.execute(
            "UPDATE agent_results SET record_json = ? WHERE work_order_id = ?",
            (json.dumps(result_payload), order.id),
        )

    restored = AgentRuntimeStore(database_path).get_work_order(order.id)
    assert restored is not None
    assert restored.status == DelegationStatus.NEEDS_ATTENTION
    assert restored.next_owner == WorkOrderNextOwner.LEADER_REPLAN


def test_explicit_newer_work_order_owner_is_not_replaced_by_an_older_partial_result(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-worker-failure",
        objective="Return a failed worker decision to the Leader.",
        root_agent="production-manager",
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="storyboard-director",
        objective="Persist one bounded storyboard group.",
        why_now="The group is ready to author.",
    )
    runtime.record_result(
        order.id,
        status=AgentResultStatus.PARTIAL,
        concise_summary="The Tool completed and the Worker should inspect its result.",
        next_owner=WorkOrderNextOwner.WORKER_DECISION,
    )
    failed = store.get_work_order(order.id)
    assert failed is not None
    failed.next_owner = WorkOrderNextOwner.LEADER_REPLAN
    failed.updated_at = utc_now()
    store.save_work_order(failed)

    restored = AgentRuntimeStore(database_path).get_work_order(order.id)

    assert restored is not None
    assert restored.next_owner == WorkOrderNextOwner.LEADER_REPLAN


def test_completed_result_can_be_distilled_and_deduplicated_as_experience(
    tmp_path: Path,
) -> None:
    store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-experience",
        objective="Review the revised ending",
        root_agent="production-manager",
        active_skill_ids=["format.douyin-short-drama"],
    )
    scene_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="scene",
        artifact_id="scene-ending",
        version=2,
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Review the revised ending",
        why_now="The local script patch is complete",
        scope=[scene_ref],
    )
    runtime.record_result(
        order.id,
        status=AgentResultStatus.COMPLETED,
        concise_summary="A restrained final beat preserves the reversal",
        findings=["The pause lands before the reveal"],
        decisions=["Keep the final reaction silent"],
        updated_artifacts=[scene_ref],
    )

    first = runtime.distill_experience_from_result(
        order.id,
        applicability="Local ending revisions",
        confidence=ExperienceConfidence.MEDIUM,
        tags=["ending"],
        skill_id="format.douyin-short-drama",
    )
    merged = runtime.distill_experience_from_result(
        order.id,
        applicability="  local   ending revisions ",
        evidence="The director review confirmed the silent reaction",
        confidence=ExperienceConfidence.HIGH,
        tags=["director-review"],
        skill_id="format.douyin-short-drama",
    )

    assert merged.id == first.id
    assert merged.confidence == ExperienceConfidence.HIGH
    assert merged.tags == ["ending", "director-review"]
    assert "pause lands" in merged.evidence
    assert "director review" in merged.evidence
    assert merged.artifact_refs == [scene_ref]
    assert store.list_experiences_for_scope(
        project_id=run.project_id,
        skill_id="format.douyin-short-drama",
    ) == [merged]


def test_runtime_updates_agent_selected_skill_composition(tmp_path: Path) -> None:
    _store, runtime, _compiler = _runtime(tmp_path / "runtime.sqlite3")
    run = runtime.create_run(
        project_id="project-skills",
        objective="Write the requested story.",
        root_agent="production-manager",
        active_skill_ids=["format.ai-short-drama"],
    )

    updated = runtime.set_run_active_skills(
        run.id,
        [
            "format.ai-short-drama",
            "narrative.payoff-escalation",
            "narrative.payoff-escalation",
            " narrative.absurd-comedy ",
        ],
    )

    assert updated.active_skill_ids == [
        "format.ai-short-drama",
        "narrative.payoff-escalation",
        "narrative.absurd-comedy",
    ]
    assert runtime.snapshot(run.id).run.active_skill_ids == updated.active_skill_ids


def test_pending_work_order_survives_runtime_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    _store, runtime, _compiler = _runtime(database_path)
    run = runtime.create_run(
        project_id="project-restart",
        objective="Resume a delegated scene review after restart.",
        root_agent="production-manager",
    )
    shot_ref = ArtifactRef(
        project_id=run.project_id,
        artifact_type="shot",
        artifact_id="shot-1",
        version=1,
    )
    order = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="scene-director",
        objective="Review shot 1.",
        why_now="The upstream scene edit is complete.",
        scope=[shot_ref],
        constraints=["Preserve the accepted ending."],
    )

    _reopened_store, reopened, _reopened_compiler = _runtime(database_path)
    snapshot = reopened.snapshot(run.id)

    assert snapshot.pending_work_orders == [order]
    assert snapshot.pending_work_orders[0].scope == [shot_ref]


def test_plan_graph_rejects_missing_or_cyclic_dependencies() -> None:
    with pytest.raises(ValidationError, match="missing dependencies"):
        PlanRevision(
            id="plan-1",
            run_id="run-1",
            revision=1,
            summary="invalid",
            steps=[
                PlanStep(
                    id="a",
                    title="A",
                    kind=ActionKind.CHECKPOINT,
                    assigned_agent="manager",
                    instructions="checkpoint",
                    depends_on=["missing"],
                )
            ],
        )

    with pytest.raises(ValidationError, match="acyclic"):
        PlanRevision(
            id="plan-2",
            run_id="run-1",
            revision=1,
            summary="invalid",
            steps=[
                PlanStep(
                    id="a",
                    title="A",
                    kind=ActionKind.CHECKPOINT,
                    assigned_agent="manager",
                    instructions="checkpoint",
                    depends_on=["b"],
                ),
                PlanStep(
                    id="b",
                    title="B",
                    kind=ActionKind.CHECKPOINT,
                    assigned_agent="manager",
                    instructions="checkpoint",
                    depends_on=["a"],
                ),
            ],
        )
