"""Offline acceptance for Storyboard -> GenerationUnit -> H3 Take -> timeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.h3_models import (
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
    H3ProviderConfigUpdate,
    H3RemoteStatus,
    H3ShotRouteDirective,
    H3TakeReviewRequest,
)
from short_drama.models import (
    ContentPlanningOutput,
    ProductionDocumentOrigin,
    ProjectStage,
    QaFailureCategory,
    QaIssue,
    TaskStatus,
)
from short_drama.production_contracts import (
    ClipGroup,
    GenerationPlanCompileRequest,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardCreateRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from tests.test_short_drama_h3 import (
    FakeH3Client,
    _fake_factory,
    _render_h3_output,
)


def _service(tmp_path: Path, fake: FakeH3Client) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        h3_client_factory=_fake_factory(fake),
    )
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-minimax-key"))
    return service


def _finish_h3_task(
    service: ShortDramaService,
    fake: FakeH3Client,
    task_id: str,
) -> None:
    assert service.advance_task(task_id).status == TaskStatus.SUBMITTING
    acknowledged = service.advance_task(task_id)
    assert acknowledged.status == TaskStatus.ACKNOWLEDGED
    assert acknowledged.provider_task_id is not None
    fake.set_status(acknowledged.provider_task_id, H3RemoteStatus.RUNNING)
    assert service.advance_task(task_id).status == TaskStatus.RUNNING
    fake.set_status(acknowledged.provider_task_id, H3RemoteStatus.SUCCEEDED)
    assert service.advance_task(task_id).status == TaskStatus.SUCCEEDED


@pytest.mark.parametrize("preparation_revision_offset", [0, 3])
def test_script_only_agent_pipeline_preserves_multi_shot_edit_boundaries(
    tmp_path: Path,
    preparation_revision_offset: int,
) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    initialized = service.initialize_fixture()
    project_id = initialized.document.project.id

    # Simulate the intended handoff: content planning stopped after the full script.
    document = initialized.document
    authored_shots = [item.model_copy(deep=True) for item in document.shots]
    document.project.content_planning_output = ContentPlanningOutput.SCRIPT_ONLY
    document.project.stage = ProjectStage.SCRIPT_READY
    document.shots = []
    document.execution_plan = []
    document.reference_plan = []
    service.repository.save_project(document)

    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="Agent 制片规格",
                production_scope="从完整剧本形成可生成、可编辑的视频单元。",
                production_mode="Agent 自主完成分镜、生成单元和稳定工件交接。",
                genre_and_setting="雨夜便利店悬疑对峙。",
                language="zh-CN",
                aspect_ratio="9:16",
                target_duration_seconds=document.project.target_duration_seconds,
                visual=ProductionVisualDirection(
                    medium_lock="写实竖屏影像",
                    style_brief=["快速进入冲突", "近距离人物对峙"],
                ),
                audio=ProductionAudioDirection(
                    dialogue_intent="对白清晰，保留环境声。"
                ),
                provider_preference=ProductionProviderPreference(
                    provider="minimax",
                    model="MiniMax-H3",
                    resolution="768P",
                ),
                user_constraints=["不启用 LTX 模型"],
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    first_group_shots = authored_shots[:2]
    clip_groups = [
        ClipGroup(
            id="clip-group-opening",
            title="进店与首次对峙",
            shot_ids=[item.id for item in first_group_shots],
            duration_seconds=sum(item.duration_seconds for item in first_group_shots),
            generation_intent="一次生成进店和首次对峙，但保留两个编辑镜头边界。",
        ),
        *[
            ClipGroup(
                id=f"clip-group-{shot.sequence:02d}",
                title=f"镜头 {shot.sequence}",
                shot_ids=[shot.id],
                duration_seconds=shot.duration_seconds,
                generation_intent=shot.narrative_purpose,
            )
            for shot in authored_shots[2:]
        ],
    ]
    storyboard = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=StoryboardContent(
                project_id=project_id,
                title="完整分镜",
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
                script_line_ids=[item.id for item in script.lines],
                shots=authored_shots,
                clip_groups=clip_groups,
                computed_duration_seconds=sum(
                    item.duration_seconds for item in authored_shots
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    if preparation_revision_offset:
        # Reference preparation advances the execution projection's revision
        # while the accepted Storyboard content and version remain unchanged.
        prepared = service.get_project(project_id).document
        for shot in prepared.shots:
            shot.revision += preparation_revision_offset + shot.sequence
        service.repository.save_project(prepared)
    generation_plan = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    units = generation_plan.generation_plan.units
    route = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            generation_plan_document_id=generation_plan.document.id,
            generation_plan_version_id=generation_plan.version.id,
            unit_ids=[item.id for item in units],
            use_language_agent=False,
            consider_video_guides=False,
            candidate_count=2,
            shot_directives=[
                H3ShotRouteDirective(
                    generation_unit_id=item.id,
                    candidate_id=f"direct:{item.id}",
                )
                for item in units
            ],
        ),
    ).plan
    executed = service.execute_h3_production_route_plan(
        project_id,
        route.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=route.state_revision,
            enqueue_new_generations=True,
        ),
    )
    assert len(executed.queued_tasks) == len(units) * 2

    output_path = tmp_path / "fake-h3-output.mp4"
    _render_h3_output(output_path, duration=15)
    fake.output_source = output_path
    selected_take_ids: dict[str, str] = {}
    for decision in executed.plan.decisions:
        tasks = sorted(
            (service.repository.get_task(task_id) for task_id in decision.task_ids),
            key=lambda item: item.candidate_index if item is not None else 0,
        )
        first_task = tasks[0]
        assert first_task is not None
        _finish_h3_task(service, fake, first_task.id)
        take = service.repository.list_takes_for_shot(
            project_id,
            decision.shot_id,
        )[0]
        service.select_take(take.id)
        assert decision.generation_unit_id is not None
        selected_take_ids[decision.generation_unit_id] = take.id

    # A failed sibling candidate and its retry must not replace the confirmed output.
    first_decision = executed.plan.decisions[0]
    sibling_task = service.repository.get_task(first_decision.task_ids[1])
    assert sibling_task is not None
    assert service.advance_task(sibling_task.id).status == TaskStatus.SUBMITTING
    acknowledged = service.advance_task(sibling_task.id)
    assert acknowledged.provider_task_id is not None
    fake.set_status(acknowledged.provider_task_id, H3RemoteStatus.FAILED)
    assert service.advance_task(sibling_task.id).status == TaskStatus.FAILED
    retry = service.retry_task(sibling_task.id)
    assert retry.status == TaskStatus.QUEUED
    confirmed_slot = service.get_output_slot(
        project_id,
        first_decision.output_slot_id or "",
    ).output_slot
    assert confirmed_slot.current_artifact_ref is not None
    assert confirmed_slot.current_artifact_ref.artifact_id == selected_take_ids[
        first_decision.generation_unit_id or ""
    ]

    manifest = service.rebuild_manifest(project_id)
    assert len(manifest.video_clips) == len(authored_shots)
    opening_clips = manifest.video_clips[:2]
    assert opening_clips[0].take_id == opening_clips[1].take_id
    assert opening_clips[0].source_in_seconds == 0.0
    assert opening_clips[0].duration_seconds == authored_shots[0].duration_seconds
    assert opening_clips[1].source_in_seconds == authored_shots[0].duration_seconds
    assert opening_clips[1].duration_seconds == authored_shots[1].duration_seconds
    assert sum(item.duration_seconds for item in manifest.video_clips) == 50.0

    if preparation_revision_offset:
        prepared = service.get_project(project_id).document
        original = prepared.model_copy(deep=True)
        prepared.shots[0].revision += 1
        service.repository.save_project(prepared)
        with pytest.raises(WorkflowConflictError, match="outdated Shot revision"):
            service.rebuild_manifest(project_id)
        prepared = original.model_copy(deep=True)
        prepared.shots[0].narrative_purpose += " A different authored action."
        service.repository.save_project(prepared)
        with pytest.raises(WorkflowConflictError, match="differs from the current Storyboard"):
            service.rebuild_manifest(project_id)
        service.repository.save_project(original)

    # Clearing one covered Shot keeps the shared Take and unit slot available for
    # its still-selected sibling. Rejecting the Take clears the remaining selection,
    # invalidates the derived timeline, and preserves the old slot binding as history.
    opening_take_id = opening_clips[0].take_id
    opening_slot_id = first_decision.output_slot_id or ""
    service._clear_take_selection_for_shot(project_id, authored_shots[0].id)
    partially_selected = service.repository.get_take(opening_take_id)
    assert partially_selected is not None
    assert partially_selected.selected_shot_ids == [authored_shots[1].id]
    partial_slot = service.get_output_slot(project_id, opening_slot_id).output_slot
    assert partial_slot.current_artifact_ref is not None
    assert partial_slot.current_artifact_ref.artifact_id == opening_take_id

    rejected = service.review_h3_take(
        opening_take_id,
        H3TakeReviewRequest(
            hard_gate_passed=False,
            score=2.0,
            notes="The selected generation unit is no longer acceptable.",
            issues=[
                QaIssue(
                    code="GENERATION_UNIT_REJECTED",
                    severity="HIGH",
                    message="The selected generation unit failed semantic review.",
                    repair_layer="DIRECTOR_AGENT",
                    category=QaFailureCategory.COMPOSITION_QUALITY,
                )
            ],
        ),
    )
    assert rejected.take.is_selected is False
    assert rejected.take.selected_shot_ids == []
    persisted_rejection = service.repository.get_take(opening_take_id)
    assert persisted_rejection is not None
    assert persisted_rejection.is_selected is False
    assert persisted_rejection.selected_shot_ids == []
    assert rejected.project.document.project.current_manifest_id is None
    rejected_slot = service.get_output_slot(project_id, opening_slot_id).output_slot
    assert rejected_slot.current_artifact_ref is None
    assert rejected_slot.previous_artifact_refs[-1].artifact_id == opening_take_id
