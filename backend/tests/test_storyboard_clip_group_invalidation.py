"""Focused invalidation coverage for ClipGroup-only storyboard revisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.models import (
    EnqueueRequest,
    Freshness,
    GenerationTask,
    OperationKind,
    ProductionDocumentOrigin,
    QAReport,
    QaStatus,
    Take,
    TakeAvailability,
    TaskStatus,
)
from short_drama.production_contracts import (
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    ClipGroup,
    GenerationPlanCompileRequest,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardReplaceRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _selected_take(
    project_id: str,
    shot_id: str,
    task_id: str,
    duration: float,
    *,
    storyboard_document_id: str,
    storyboard_version_id: str,
    generation_plan_document_id: str,
    generation_plan_version_id: str,
) -> Take:
    take_id = f"take-{shot_id}"
    return Take(
        id=take_id,
        project_id=project_id,
        shot_id=shot_id,
        task_id=task_id,
        candidate_index=1,
        operation=OperationKind.GENERATE,
        provider="minimax",
        model="MiniMax-H3",
        storyboard_document_id=storyboard_document_id,
        storyboard_version_id=storyboard_version_id,
        generation_plan_document_id=generation_plan_document_id,
        generation_plan_version_id=generation_plan_version_id,
        media_path=f"/tmp/{take_id}.mp4",
        thumbnail_path=f"/tmp/{take_id}.jpg",
        duration_seconds=duration,
        width=768,
        height=1366,
        availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.PASSED,
        is_selected=True,
        freshness=Freshness.CURRENT,
        qa_report=QAReport(
            id=f"qa-{take_id}",
            take_id=take_id,
            status=QaStatus.PASSED,
            hard_gate_passed=True,
            score=5.0,
            issues=[],
            checks=["focused-test"],
            created_at="2026-09-04T12:00:00+08:00",
        ),
        created_at="2026-09-04T12:00:00+08:00",
    )


def test_timeline_rejects_legacy_take_from_previous_script_version(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    service.enqueue(project_id, EnqueueRequest(candidate_count=1))
    assert service.drain(project_id, 500).remaining_runnable == 0
    assert service.rebuild_manifest(project_id).script_version_id == (
        document.project.current_script_version_id
    )

    document = service.get_project(project_id).document
    current_script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    changed_line = current_script.lines[0].model_copy(
        update={
            "spoken_text": "这句新台词没有经过正常的分镜与 Take 失效传播。",
            "subtitle_text": "这句新台词没有经过正常的分镜与 Take 失效传播。",
        }
    )
    replacement = current_script.model_copy(
        deep=True,
        update={
            "id": f"{project_id}-script-v2",
            "version": current_script.version + 1,
            "lines": [changed_line, *current_script.lines[1:]],
            "parent_version_id": current_script.id,
            "change_reason": "验证时间线不得把旧 Take 冒充为新剧本产物。",
        },
    )
    document.script_versions.append(replacement)
    document.project.current_script_version_id = replacement.id
    service.repository.save_project(document)

    with pytest.raises(WorkflowConflictError, match="outdated ScriptVersion"):
        service.rebuild_manifest(project_id)


def test_clip_group_only_revision_stales_exactly_affected_takes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shots = [item.model_copy(deep=True) for item in document.shots]

    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="ClipGroup invalidation spec",
                production_scope="Validate storyboard grouping changes.",
                production_mode="Agent-managed production.",
                genre_and_setting="Rainy-night convenience store thriller.",
                language="zh-CN",
                aspect_ratio="9:16",
                target_duration_seconds=sum(item.duration_seconds for item in shots),
                visual=ProductionVisualDirection(medium_lock="Live action"),
                audio=ProductionAudioDirection(dialogue_intent="Clear dialogue"),
                provider_preference=ProductionProviderPreference(
                    provider="minimax",
                    model="MiniMax-H3",
                    resolution="768P",
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    original_groups = [
        ClipGroup(
            id=f"clip-group-{shot.sequence:02d}",
            title=f"Shot {shot.sequence}",
            shot_ids=[shot.id],
            duration_seconds=shot.duration_seconds,
            generation_intent=f"Generate shot {shot.sequence} independently.",
        )
        for shot in shots
    ]
    created = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=StoryboardContent(
                project_id=project_id,
                title="ClipGroup invalidation storyboard",
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
                script_line_ids=[item.id for item in script.lines],
                shots=shots,
                clip_groups=original_groups,
                computed_duration_seconds=sum(
                    item.duration_seconds for item in shots
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    generation_plan = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=created.document.id,
            storyboard_version_id=created.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    for shot in shots:
        task = GenerationTask(
            id=f"task-{shot.id}",
            project_id=project_id,
            shot_id=shot.id,
            operation=OperationKind.GENERATE,
            candidate_index=1,
            status=TaskStatus.SUCCEEDED,
            provider="minimax",
            model="MiniMax-H3",
            request_fingerprint=f"fingerprint-{shot.id}",
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            input_shot_revision=shot.revision,
            storyboard_document_id=created.document.id,
            storyboard_version_id=created.version.id,
            generation_plan_document_id=generation_plan.document.id,
            generation_plan_version_id=generation_plan.version.id,
            requested_duration_seconds=shot.duration_seconds,
            attempt=1,
            finished_at="2026-09-04T12:00:00+08:00",
            created_at="2026-09-04T12:00:00+08:00",
            updated_at="2026-09-04T12:00:00+08:00",
        )
        service.repository.save_task(task)
        service.repository.save_take(
            _selected_take(
                project_id,
                shot.id,
                task.id,
                shot.duration_seconds,
                storyboard_document_id=created.document.id,
                storyboard_version_id=created.version.id,
                generation_plan_document_id=generation_plan.document.id,
                generation_plan_version_id=generation_plan.version.id,
            )
        )
    manifest = service.rebuild_manifest(project_id)

    revised_groups = [
        ClipGroup(
            id="clip-group-merged-opening",
            title="Merged opening",
            shot_ids=[shots[0].id, shots[1].id],
            duration_seconds=shots[0].duration_seconds + shots[1].duration_seconds,
            generation_intent="Generate the opening shots as one continuous clip.",
        ),
        original_groups[2].model_copy(
            update={
                "generation_intent": "Generate shot 3 with a different performance intent."
            },
            deep=True,
        ),
        *[item.model_copy(deep=True) for item in original_groups[3:]],
    ]
    service.replace_storyboard(
        project_id,
        StoryboardReplaceRequest(
            storyboard=created.storyboard.model_copy(
                update={"clip_groups": revised_groups},
                deep=True,
            ),
            base_version_id=created.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    affected_ids = {item.id for item in shots[:3]}
    for shot in shots:
        take = service.repository.get_take(f"take-{shot.id}")
        assert take is not None
        if shot.id in affected_ids:
            assert take.freshness == Freshness.STALE
            assert take.selected_shot_ids == []
        else:
            assert take.freshness == Freshness.CURRENT
            assert take.selected_shot_ids == [shot.id]

    revised_document = service.get_project(project_id).document
    persisted_manifest = next(
        item for item in revised_document.manifests if item.id == manifest.id
    )
    assert persisted_manifest.freshness == Freshness.STALE
    assert revised_document.project.current_manifest_id is None
    generation_slot = service.get_output_slot(
        project_id,
        GENERATION_PLAN_OUTPUT_SLOT_ID,
    ).output_slot
    assert generation_slot.current_artifact_ref is None
    assert generation_slot.previous_artifact_refs[-1].artifact_id == (
        generation_plan.document.id
    )
