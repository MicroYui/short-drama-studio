"""Service-level coverage for recoverable incremental storyboard authoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.models import (
    ContentPlanningOutput,
    ProductionDocumentOrigin,
    ProjectStage,
)
from short_drama.production_contracts import (
    ClipGroup,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionVisualDirection,
    StoryboardClipGroupOutline,
    StoryboardClipGroupDraft,
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftContent,
    StoryboardDraftFinalizeRequest,
    StoryboardDraftGroupReplaceRequest,
    StoryboardDraftLedgerCompleteRequest,
    StoryboardDraftLedgerSegmentAppendRequest,
    StoryboardDraftOutlineCompleteRequest,
    StoryboardDraftOutlineSegmentAppendRequest,
    StoryboardDraftPhase,
    StoryboardDraftSceneSeed,
    StoryboardDraftSeed,
    StoryboardDraftVerificationFinding,
    StoryboardDraftVerificationReport,
    StoryboardDraftVerificationRequest,
    StoryboardDraftVerificationStatus,
    StoryboardSceneNode,
    StoryboardSourceRange,
    StoryboardTimingAudit,
    StoryboardTimingLedgerEntry,
    StoryboardWorkUnitContract,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _prepare_script_only_project(service: ShortDramaService):
    document = service.initialize_fixture().document
    document.project.content_planning_output = ContentPlanningOutput.SCRIPT_ONLY
    document.project.stage = ProjectStage.SCRIPT_READY
    source_shots = [item.model_copy(deep=True) for item in document.shots]
    document.shots = []
    document.execution_plan = []
    document.reference_plan = []
    service.repository.save_project(document)
    project_id = document.project.id
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=ProductionSpecContent(
                project_id=project_id,
                script_version_id=document.project.current_script_version_id,
                bible_version_id=document.project.current_bible_version_id,
                title="Incremental storyboard service spec",
                production_scope="Persist bounded storyboard groups before publishing.",
                production_mode="Agent-managed incremental storyboard authoring.",
                genre_and_setting="Rainy-night convenience store thriller.",
                language="zh-CN",
                aspect_ratio="9:16",
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
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    dialogue_shots = [item for item in source_shots if item.dialogue_line_ids]
    return document, script, spec, dialogue_shots


def _draft(
    document,
    script,
    spec,
    *,
    dialogue_shots,
    base_storyboard_version_id=None,
):
    scene = script.structured_scenes[0]
    timing_ledger = [
        StoryboardTimingLedgerEntry(
            id=f"timing-{line.id}",
            sequence=sequence,
            scene_node_id=f"scene-node-{scene.id}",
            source_range=StoryboardSourceRange(
                id=f"timing-range-{line.id}",
                scene_ids=[scene.id],
                script_line_ids=[line.id],
                description=f"Timing source for {line.id}.",
            ),
            source_text=line.spoken_text,
            dialogue_units=1,
            speech_rate_units_per_second=1.0,
            minimum_spoken_duration_seconds=1.0,
            non_speech_reserve_seconds=source_shot.duration_seconds - 1.0,
            allocated_duration_seconds=source_shot.duration_seconds,
            natural_cut=f"Natural pause after {line.id}.",
            continuity_in=(
                []
                if sequence == 1
                else [f"after-{script.lines[sequence - 2].id}"]
            ),
            visible_changes=[f"{line.id} completes its visible action."],
            continuity_out=[f"after-{line.id}"],
            completes_scene=sequence == len(script.lines),
            planned_clip_group_id=f"clip-{line.id}",
            planned_shot_id=f"incremental-{line.id}",
        )
        for sequence, (line, source_shot) in enumerate(
            zip(script.lines, dialogue_shots, strict=True),
            start=1,
        )
    ]
    planned_clip_groups = [
        StoryboardClipGroupOutline(
            id=f"clip-{line.id}",
            title=f"Clip for {line.id}",
            scene_node_id=f"scene-node-{scene.id}",
            scene_group_sequence=sequence,
            source_range=StoryboardSourceRange(
                id=f"range-{line.id}",
                scene_ids=[scene.id],
                script_line_ids=[line.id],
                description=f"Complete the action and reaction around {line.id}.",
            ),
            timing_entry_ids=[f"timing-{line.id}"],
            duration_seconds=source_shot.duration_seconds,
            objective="Render this dialogue beat as one continuous H3 clip.",
            continuity_in=(
                []
                if sequence == 1
                else [f"after-{script.lines[sequence - 2].id}"]
            ),
            visible_changes=[f"{line.id} completes its visible action."],
            continuity_out=[f"after-{line.id}"],
        )
        for sequence, (line, source_shot) in enumerate(
            zip(script.lines, dialogue_shots, strict=True),
            start=1,
        )
    ]
    computed_duration = sum(item.duration_seconds for item in planned_clip_groups)
    return StoryboardDraftContent(
        phase=StoryboardDraftPhase.GROUP_AUTHORING,
        project_id=document.project.id,
        title="Incremental storyboard",
        script_version_id=script.id,
        bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=spec.document.id,
        production_spec_version_id=spec.version.id,
        base_storyboard_version_id=base_storyboard_version_id,
        script_line_ids=[item.id for item in script.lines],
        scene_nodes=[
            StoryboardSceneNode(
                id=f"scene-node-{scene.id}",
                scene_id=scene.id,
                sequence=1,
                heading=scene.heading,
                summary=scene.summary,
                source_line_ids=list(scene.line_ids),
            )
        ],
        timing_ledger=timing_ledger,
        planned_clip_groups=planned_clip_groups,
        timing_audit=StoryboardTimingAudit(
            target_duration_s=document.project.target_duration_seconds,
            dialogue_units_total=len(script.lines),
            minimum_spoken_duration_s=float(len(script.lines)),
            non_speech_reserve_s=sum(
                item.duration_seconds for item in dialogue_shots
            )
            - len(script.lines),
            computed_episode_duration_s=computed_duration,
            clip_group_count=len(planned_clip_groups),
            timing_audit_passed=True,
        ),
    )


def _seed_from_draft(draft: StoryboardDraftContent) -> StoryboardDraftSeed:
    return StoryboardDraftSeed(
        project_id=draft.project_id,
        title=draft.title,
        script_version_id=draft.script_version_id,
        bible_version_id=draft.bible_version_id,
        production_spec_document_id=draft.production_spec_document_id,
        production_spec_version_id=draft.production_spec_version_id,
        base_storyboard_version_id=draft.base_storyboard_version_id,
        scene_nodes=[
            StoryboardDraftSceneSeed(
                id=node.id,
                scene_id=node.scene_id,
                sequence=node.sequence,
                continuity_in=node.continuity_in,
                continuity_out=node.continuity_out,
                notes=node.notes,
            )
            for node in draft.scene_nodes
        ],
        timing_notes=draft.timing_notes,
        notes=draft.notes,
    )


def _begin_and_plan_draft(
    service: ShortDramaService,
    *,
    project_id: str,
    draft: StoryboardDraftContent,
):
    assert draft.timing_audit is not None
    begun = service.begin_storyboard_draft(
        project_id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(draft),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    return _persist_plan(
        service,
        project_id=project_id,
        draft=draft,
        begun=begun,
    )


def _persist_plan(
    service: ShortDramaService,
    *,
    project_id: str,
    draft: StoryboardDraftContent,
    begun,
):
    assert draft.timing_audit is not None
    ledger = service.append_storyboard_ledger_segment(
        project_id,
        StoryboardDraftLedgerSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=begun.draft_version_id,
            entries=draft.timing_ledger,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    ledger_complete = service.complete_storyboard_ledger(
        project_id,
        StoryboardDraftLedgerCompleteRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=ledger.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    outline = service.append_storyboard_outline_segment(
        project_id,
        StoryboardDraftOutlineSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=ledger_complete.draft_version_id,
            groups=draft.planned_clip_groups,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    return service.complete_storyboard_outline(
        project_id,
        StoryboardDraftOutlineCompleteRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=outline.draft_version_id,
            timing_audit=draft.timing_audit,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress


def _append_all_groups(
    service: ShortDramaService,
    *,
    document,
    script,
    dialogue_shots,
    draft_document_id: str,
    base_version_id: str,
    changed_line_id: str | None = None,
    starting_sequence: int = 1,
    starting_continuity_in: list[str] | None = None,
):
    scene = script.structured_scenes[0]
    current_version_id = base_version_id
    last_progress = None
    for offset, (line, source_shot) in enumerate(
        zip(script.lines, dialogue_shots, strict=True)
    ):
        sequence = starting_sequence + offset
        source_range = StoryboardSourceRange(
            id=f"range-{line.id}",
            scene_ids=[scene.id],
            script_line_ids=[line.id],
            description=f"Complete the action and reaction around {line.id}.",
        )
        work_unit = StoryboardWorkUnitContract(
            id=f"work-{line.id}",
            title=f"Author {line.id}",
            source_range=source_range,
            objective="Create one complete, bounded generated clip.",
            completion_criteria=["Dialogue, action, and continuity are explicit."],
        )
        visual = source_shot.visual
        if line.id == changed_line_id:
            visual = f"{visual} A deliberate close-up emphasizes the changed beat."
        shot = source_shot.model_copy(
            update={
                "id": f"incremental-{line.id}",
                "sequence": sequence,
                "scene_id": scene.id,
                "visual": visual,
            },
            deep=True,
        )
        group = StoryboardClipGroupDraft(
            work_unit_id=work_unit.id,
            scene_node_id=f"scene-node-{scene.id}",
            scene_group_sequence=sequence,
            source_range=source_range,
            shots=[shot],
            clip_group=ClipGroup(
                id=f"clip-{line.id}",
                title=f"Clip for {line.id}",
                shot_ids=[shot.id],
                duration_seconds=shot.duration_seconds,
                generation_intent="Render this dialogue beat as one continuous H3 clip.",
                continuity_in=(
                    list(starting_continuity_in or [])
                    if offset == 0
                    else [f"after-{script.lines[offset - 1].id}"]
                ),
                visible_changes=[f"{line.id} completes its visible action."],
                continuity_out=[f"after-{line.id}"],
            ),
        )
        response = service.append_storyboard_group(
            document.project.id,
            StoryboardDraftAppendRequest(
                draft_document_id=draft_document_id,
                base_version_id=current_version_id,
                work_unit=work_unit,
                group=group,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
        last_progress = response.progress
        current_version_id = response.progress.draft_version_id
    assert last_progress is not None
    return last_progress


def test_incremental_draft_persists_progress_without_publishing_until_finalize(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    draft = _draft(
        document,
        script,
        spec,
        dialogue_shots=dialogue_shots,
    )
    begun = service.begin_storyboard_draft(
        project_id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(draft),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress

    after_begin = service.get_project(project_id).document
    assert after_begin.storyboard_document_id is None
    assert after_begin.shots == []
    assert after_begin.project.stage == ProjectStage.SCRIPT_READY
    assert begun.phase == StoryboardDraftPhase.LEDGER_BUILDING
    assert begun.timing_ledger_entry_count == 0
    assert begun.planned_clip_group_count == 0
    assert begun.committed_clip_group_count == 0
    assert begun.total_planned_duration_seconds is None
    assert begun.committed_duration_seconds == 0.0
    assert begun.remaining_duration_seconds is None
    assert begun.covered_script_line_count == 0
    assert begun.remaining_script_line_count == len(script.lines)
    assert begun.scene_progress[0].planned_clip_group_count == 0

    planned = _persist_plan(
        service,
        project_id=project_id,
        draft=draft,
        begun=begun,
    )
    assert planned.phase == StoryboardDraftPhase.GROUP_AUTHORING
    assert planned.planned_clip_group_count == len(script.lines)
    assert planned.total_planned_duration_seconds == sum(
        item.duration_seconds for item in dialogue_shots
    )

    first_source = draft.planned_clip_groups[0].source_range.model_copy(deep=True)
    first_work = StoryboardWorkUnitContract(
        id=f"work-{script.lines[0].id}",
        title="First beat",
        source_range=first_source,
        objective="Persist the first beat.",
        completion_criteria=["The first line is covered."],
    )
    first_shot = dialogue_shots[0].model_copy(
        update={
            "id": f"incremental-{script.lines[0].id}",
            "sequence": 1,
            "scene_id": script.structured_scenes[0].id,
        },
        deep=True,
    )
    first_group = StoryboardClipGroupDraft(
        work_unit_id=first_work.id,
        scene_node_id=f"scene-node-{script.structured_scenes[0].id}",
        scene_group_sequence=1,
        source_range=first_source,
        shots=[first_shot],
        clip_group=ClipGroup(
            id=f"clip-{script.lines[0].id}",
            title="First clip",
            shot_ids=[first_shot.id],
            duration_seconds=first_shot.duration_seconds,
            generation_intent="Render the first beat.",
            continuity_in=[],
            visible_changes=[
                f"{script.lines[0].id} completes its visible action."
            ],
            continuity_out=[f"after-{script.lines[0].id}"],
        ),
    )
    first_append = service.append_storyboard_group(
        project_id,
        StoryboardDraftAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=planned.draft_version_id,
            work_unit=first_work,
            group=first_group,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    with pytest.raises(WorkflowConflictError, match="stale version"):
        service.append_storyboard_group(
            project_id,
            StoryboardDraftAppendRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=planned.draft_version_id,
                work_unit=first_work,
                group=first_group,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    partial_verification = service.record_storyboard_draft_verification(
        project_id,
        StoryboardDraftVerificationRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=first_append.draft_version_id,
            report=StoryboardDraftVerificationReport(
                status=StoryboardDraftVerificationStatus.NEEDS_REVISION,
                summary="The rest of the planned episode has not been authored yet.",
                checked_clip_group_ids=[f"clip-{script.lines[0].id}"],
                findings=[
                    StoryboardDraftVerificationFinding(
                        id="remaining-groups",
                        summary="Continue authoring the remaining planned groups.",
                    )
                ],
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert (
        partial_verification.verification_status
        == StoryboardDraftVerificationStatus.NEEDS_REVISION
    )

    progress = _append_all_groups(
        service,
        document=document,
        script=script.model_copy(update={"lines": script.lines[1:]}),
        dialogue_shots=dialogue_shots[1:],
        draft_document_id=begun.draft_ref.artifact_id,
        base_version_id=partial_verification.draft_version_id,
        starting_sequence=2,
        starting_continuity_in=[f"after-{script.lines[0].id}"],
    )
    # The helper's reduced ScriptVersion only selects the remaining lines; all
    # service validation still resolves the authoritative complete ScriptVersion.
    assert progress.committed_clip_group_count == len(script.lines)
    assert progress.committed_duration_seconds == progress.total_planned_duration_seconds
    assert progress.remaining_duration_seconds == 0.0
    assert progress.covered_script_line_count == len(script.lines)
    assert progress.remaining_script_line_count == 0
    assert progress.latest_continuity_out == [
        f"after-{script.lines[-1].id}"
    ]
    assert progress.verification_status is None
    assert service.get_project(project_id).document.storyboard_document_id is None

    finalized = service.finalize_storyboard(
        project_id,
        StoryboardDraftFinalizeRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            draft_version_id=progress.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    published = service.get_project(project_id).document
    assert published.storyboard_document_id == finalized.document.id
    assert published.project.stage == ProjectStage.PLANNED
    assert [item.id for item in published.shots] == [
        f"incremental-{line.id}" for line in script.lines
    ]
    assert finalized.storyboard.scene_nodes[0].clip_group_ids == [
        f"clip-{line.id}" for line in script.lines
    ]
    assert finalized.version.source_artifact_refs[-1].artifact_type == (
        "production_document_version"
    )
    assert finalized.version.source_artifact_refs[-1].artifact_id == (
        progress.draft_version_id
    )


def test_begin_draft_resolves_scene_display_metadata_from_current_script(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, _dialogue_shots = _prepare_script_only_project(service)
    draft = _draft(
        document,
        script,
        spec,
        dialogue_shots=_dialogue_shots,
    )

    begun = service.begin_storyboard_draft(
        document.project.id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(draft),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    persisted = service.get_production_document(
        document.project.id,
        begun.draft_ref.artifact_id,
        version_id=begun.draft_version_id,
    )
    content = StoryboardDraftContent.model_validate(persisted.version.payload)

    assert [node.heading for node in content.scene_nodes] == [
        scene.heading for scene in script.structured_scenes
    ]
    assert [node.summary for node in content.scene_nodes] == [
        scene.summary for scene in script.structured_scenes
    ]
    assert content.script_line_ids == [line.id for line in script.lines]
    assert [node.source_line_ids for node in content.scene_nodes] == [
        scene.line_ids for scene in script.structured_scenes
    ]


def test_finalizing_revision_preserves_unchanged_shot_revisions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id

    first_begin = _begin_and_plan_draft(
        service,
        project_id=project_id,
        draft=_draft(
            document,
            script,
            spec,
            dialogue_shots=dialogue_shots,
        ),
    )
    first_progress = _append_all_groups(
        service,
        document=document,
        script=script,
        dialogue_shots=dialogue_shots,
        draft_document_id=first_begin.draft_ref.artifact_id,
        base_version_id=first_begin.draft_version_id,
    )
    first = service.finalize_storyboard(
        project_id,
        StoryboardDraftFinalizeRequest(
            draft_document_id=first_begin.draft_ref.artifact_id,
            draft_version_id=first_progress.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    second_begin = _begin_and_plan_draft(
        service,
        project_id=project_id,
        draft=_draft(
            document,
            script,
            spec,
            dialogue_shots=dialogue_shots,
            base_storyboard_version_id=first.version.id,
        ),
    )
    changed_line_id = script.lines[2].id
    second_progress = _append_all_groups(
        service,
        document=document,
        script=script,
        dialogue_shots=dialogue_shots,
        draft_document_id=second_begin.draft_ref.artifact_id,
        base_version_id=second_begin.draft_version_id,
        changed_line_id=changed_line_id,
    )
    second = service.finalize_storyboard(
        project_id,
        StoryboardDraftFinalizeRequest(
            draft_document_id=second_begin.draft_ref.artifact_id,
            draft_version_id=second_progress.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    previous_revisions = {item.id: item.revision for item in first.storyboard.shots}
    current_revisions = {item.id: item.revision for item in second.storyboard.shots}
    changed_shot_id = f"incremental-{changed_line_id}"
    assert current_revisions[changed_shot_id] == previous_revisions[changed_shot_id] + 1
    assert all(
        current_revisions[shot_id] == revision
        for shot_id, revision in previous_revisions.items()
        if shot_id != changed_shot_id
    )


def test_append_must_land_on_the_exact_planned_clip_group(tmp_path: Path) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    begun = _begin_and_plan_draft(
        service,
        project_id=project_id,
        draft=_draft(
            document,
            script,
            spec,
            dialogue_shots=dialogue_shots,
        ),
    )

    second_line = script.lines[1]
    second_source_range = StoryboardSourceRange(
        id=f"range-{second_line.id}",
        scene_ids=[script.structured_scenes[0].id],
        script_line_ids=[second_line.id],
        description="The second planned beat.",
    )
    second_work_unit = StoryboardWorkUnitContract(
        id="dynamic-work-order-for-second-beat",
        title="Author second planned beat",
        source_range=second_source_range,
        objective="Author the planned beat without changing its ledger slot.",
        completion_criteria=["The planned line is covered."],
    )
    second_shot = dialogue_shots[1].model_copy(
        update={
            "id": f"incremental-{second_line.id}",
            "sequence": 2,
            "scene_id": script.structured_scenes[0].id,
        },
        deep=True,
    )
    with pytest.raises(WorkflowConflictError, match="next planned ClipGroup"):
        service.append_storyboard_group(
            project_id,
            StoryboardDraftAppendRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=begun.draft_version_id,
                work_unit=second_work_unit,
                group=StoryboardClipGroupDraft(
                    work_unit_id=second_work_unit.id,
                    scene_node_id=(
                        f"scene-node-{script.structured_scenes[0].id}"
                    ),
                    scene_group_sequence=2,
                    source_range=second_source_range,
                    shots=[second_shot],
                    clip_group=ClipGroup(
                        id=f"clip-{second_line.id}",
                        title=f"Clip for {second_line.id}",
                        shot_ids=[second_shot.id],
                        duration_seconds=second_shot.duration_seconds,
                        generation_intent="Render the second beat.",
                        continuity_in=[f"after-{script.lines[0].id}"],
                        visible_changes=[
                            f"{second_line.id} completes its visible action."
                        ],
                        continuity_out=[f"after-{second_line.id}"],
                    ),
                ),
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    line = script.lines[0]
    scene = script.structured_scenes[0]
    source_range = StoryboardSourceRange(
        id=f"range-{line.id}",
        scene_ids=[scene.id],
        script_line_ids=[line.id],
        description="The first planned beat.",
    )
    work_unit = StoryboardWorkUnitContract(
        id="dynamic-work-order-for-first-beat",
        title="Author first planned beat",
        source_range=source_range,
        objective="Author the planned beat without changing its ledger slot.",
        completion_criteria=["The planned line is covered."],
    )
    planned_duration = dialogue_shots[0].duration_seconds
    changed_duration = planned_duration + 1.0
    if changed_duration > 15.0:
        changed_duration = planned_duration - 1.0
    changed_shot = dialogue_shots[0].model_copy(
        update={
            "id": f"incremental-{line.id}",
            "sequence": 1,
            "scene_id": scene.id,
            "duration_seconds": changed_duration,
        },
        deep=True,
    )
    with pytest.raises(WorkflowConflictError, match="match its planned ClipGroup"):
        service.append_storyboard_group(
            project_id,
            StoryboardDraftAppendRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=begun.draft_version_id,
                work_unit=work_unit,
                group=StoryboardClipGroupDraft(
                    work_unit_id=work_unit.id,
                    scene_node_id=f"scene-node-{scene.id}",
                    scene_group_sequence=1,
                    source_range=source_range,
                    shots=[changed_shot],
                    clip_group=ClipGroup(
                        id=f"clip-{line.id}",
                        title="First clip",
                        shot_ids=[changed_shot.id],
                        duration_seconds=changed_duration,
                        generation_intent="Render the first beat.",
                    ),
                ),
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )


def test_verification_persists_and_targeted_replacement_invalidates_it(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    begun = _begin_and_plan_draft(
        service,
        project_id=project_id,
        draft=_draft(
            document,
            script,
            spec,
            dialogue_shots=dialogue_shots,
        ),
    )
    authored = _append_all_groups(
        service,
        document=document,
        script=script,
        dialogue_shots=dialogue_shots,
        draft_document_id=begun.draft_ref.artifact_id,
        base_version_id=begun.draft_version_id,
    )
    clip_group_ids = [f"clip-{line.id}" for line in script.lines]
    needs_revision = service.record_storyboard_draft_verification(
        project_id,
        StoryboardDraftVerificationRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=authored.draft_version_id,
            report=StoryboardDraftVerificationReport(
                status=StoryboardDraftVerificationStatus.NEEDS_REVISION,
                summary="The ending continuity should use a more explicit eyeline handoff.",
                checked_clip_group_ids=clip_group_ids,
                findings=[
                    StoryboardDraftVerificationFinding(
                        id="ending-eyeline",
                        summary="The final reaction does not visibly hand off the eyeline.",
                        affected_clip_group_ids=[clip_group_ids[-1]],
                        suggested_revision="Clarify the final eyeline and held reaction.",
                    )
                ],
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert (
        needs_revision.verification_status
        == StoryboardDraftVerificationStatus.NEEDS_REVISION
    )

    line = script.lines[-1]
    scene = script.structured_scenes[0]
    source_range = StoryboardSourceRange(
        id=f"range-{line.id}",
        scene_ids=[scene.id],
        script_line_ids=[line.id],
        description=f"Complete the action and reaction around {line.id}.",
    )
    repair_work = StoryboardWorkUnitContract(
        id=f"repair-{line.id}",
        title="Repair only the ending eyeline",
        source_range=source_range,
        objective="Revise one committed group while retaining its ledger slot.",
        completion_criteria=["The final eyeline handoff is explicit."],
    )
    replacement_shot = dialogue_shots[-1].model_copy(
        update={
            "id": f"incremental-{line.id}",
            "sequence": len(script.lines),
            "scene_id": scene.id,
            "visual": (
                f"{dialogue_shots[-1].visual} The held eyeline clearly hands off "
                "to the next visual focus."
            ),
        },
        deep=True,
    )
    replaced = service.replace_storyboard_draft_group(
        project_id,
        StoryboardDraftGroupReplaceRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=needs_revision.draft_version_id,
            replaced_clip_group_id=clip_group_ids[-1],
            work_unit=repair_work,
            group=StoryboardClipGroupDraft(
                work_unit_id=repair_work.id,
                scene_node_id=f"scene-node-{scene.id}",
                scene_group_sequence=len(script.lines),
                source_range=source_range,
                shots=[replacement_shot],
                clip_group=ClipGroup(
                    id=clip_group_ids[-1],
                    title=f"Clip for {line.id}",
                    shot_ids=[replacement_shot.id],
                    duration_seconds=replacement_shot.duration_seconds,
                    generation_intent="Render the repaired ending beat.",
                    continuity_in=[f"after-{script.lines[-2].id}"],
                    visible_changes=[
                        f"{line.id} completes its visible action."
                    ],
                    continuity_out=[f"after-{line.id}"],
                ),
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert replaced.verification_status is None
    assert replaced.committed_clip_group_count == len(script.lines)
    assert replaced.remaining_duration_seconds == 0.0
    assert replaced.latest_continuity_out == [
        f"after-{line.id}"
    ]

    ready = service.record_storyboard_draft_verification(
        project_id,
        StoryboardDraftVerificationRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=replaced.draft_version_id,
            report=StoryboardDraftVerificationReport(
                status=StoryboardDraftVerificationStatus.READY,
                summary="All planned groups, timing, and continuity now read cleanly.",
                checked_clip_group_ids=clip_group_ids,
                continuity_assessment=["The ending eyeline handoff is explicit."],
                timing_assessment=["All groups retain their audited durations."],
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert ready.verification_status == StoryboardDraftVerificationStatus.READY
    persisted = service.get_production_document(
        project_id,
        begun.draft_ref.artifact_id,
        version_id=ready.draft_version_id,
    )
    content = StoryboardDraftContent.model_validate(persisted.version.payload)
    assert content.verification is not None
    assert content.verification.status == StoryboardDraftVerificationStatus.READY

    finalized = service.finalize_storyboard(
        project_id,
        StoryboardDraftFinalizeRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            draft_version_id=ready.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert finalized.storyboard.clip_groups[-1].continuity_out == [
        f"after-{line.id}"
    ]


def test_staged_planning_resumes_and_preserves_exact_segment_boundaries(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    full = _draft(
        document,
        script,
        spec,
        dialogue_shots=dialogue_shots,
    )
    assert full.timing_audit is not None
    begun = service.begin_storyboard_draft(
        project_id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(full),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    first_ledger = service.append_storyboard_ledger_segment(
        project_id,
        StoryboardDraftLedgerSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=begun.draft_version_id,
            entries=full.timing_ledger[:1],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert first_ledger.phase == StoryboardDraftPhase.LEDGER_BUILDING
    assert first_ledger.ledger_covered_script_line_count == 1
    assert first_ledger.next_unplanned_script_line_id == script.lines[1].id

    with pytest.raises(WorkflowConflictError, match="cover all source lines"):
        service.complete_storyboard_ledger(
            project_id,
            StoryboardDraftLedgerCompleteRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=first_ledger.draft_version_id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    with pytest.raises(WorkflowConflictError, match="stale version"):
        service.append_storyboard_ledger_segment(
            project_id,
            StoryboardDraftLedgerSegmentAppendRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=begun.draft_version_id,
                entries=full.timing_ledger[1:],
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    mismatched_boundary = full.timing_ledger[1].model_copy(
        update={"continuity_in": ["unrelated-state"]},
        deep=True,
    )
    complete_ledger_segment = service.append_storyboard_ledger_segment(
        project_id,
        StoryboardDraftLedgerSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=first_ledger.draft_version_id,
            entries=[mismatched_boundary, *full.timing_ledger[2:]],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert complete_ledger_segment.timing_ledger_entry_count == len(
        full.timing_ledger
    )

    # Re-open the SQLite-backed service to prove the cursor is persisted rather
    # than retained only in the original Python object. Natural-language
    # continuity wording is assessed by the semantic Verifier, not string equality.
    service = _service(tmp_path)
    outline_open = service.complete_storyboard_ledger(
        project_id,
        StoryboardDraftLedgerCompleteRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=complete_ledger_segment.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert outline_open.phase == StoryboardDraftPhase.OUTLINE_BUILDING
    with pytest.raises(WorkflowConflictError, match="LEDGER_BUILDING"):
        service.append_storyboard_ledger_segment(
            project_id,
            StoryboardDraftLedgerSegmentAppendRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=outline_open.draft_version_id,
                entries=full.timing_ledger[:1],
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    first_outline = service.append_storyboard_outline_segment(
        project_id,
        StoryboardDraftOutlineSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=outline_open.draft_version_id,
            groups=full.planned_clip_groups[:1],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    with pytest.raises(WorkflowConflictError, match="full timing ledger"):
        service.complete_storyboard_outline(
            project_id,
            StoryboardDraftOutlineCompleteRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=first_outline.draft_version_id,
                timing_audit=full.timing_audit,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    service = _service(tmp_path)
    full_outline = service.append_storyboard_outline_segment(
        project_id,
        StoryboardDraftOutlineSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=first_outline.draft_version_id,
            groups=full.planned_clip_groups[1:],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    authoring = service.complete_storyboard_outline(
        project_id,
        StoryboardDraftOutlineCompleteRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=full_outline.draft_version_id,
            timing_audit=full.timing_audit,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    assert authoring.phase == StoryboardDraftPhase.GROUP_AUTHORING
    assert authoring.outlined_timing_entry_count == len(full.timing_ledger)
    assert authoring.total_planned_duration_seconds == (
        full.timing_audit.computed_episode_duration_s
    )


def test_ledger_completion_accepts_a_contiguous_integer_partition_per_scene(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    full = _draft(
        document,
        script,
        spec,
        dialogue_shots=dialogue_shots,
    )
    fractional_prefix = [
        full.timing_ledger[0].model_copy(
            update={
                "allocated_duration_seconds": 2.1,
                "non_speech_reserve_seconds": 1.1,
                "planned_clip_group_id": "clip-combined-prefix",
            },
            deep=True,
        ),
        full.timing_ledger[1].model_copy(
            update={
                "allocated_duration_seconds": 2.9,
                "non_speech_reserve_seconds": 1.9,
                "planned_clip_group_id": "clip-combined-prefix",
            },
            deep=True,
        ),
    ]
    begun = service.begin_storyboard_draft(
        project_id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(full),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    ledger = service.append_storyboard_ledger_segment(
        project_id,
        StoryboardDraftLedgerSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=begun.draft_version_id,
            entries=[*fractional_prefix, *full.timing_ledger[2:]],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress

    completed = service.complete_storyboard_ledger(
        project_id,
        StoryboardDraftLedgerCompleteRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=ledger.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress

    assert completed.phase == StoryboardDraftPhase.OUTLINE_BUILDING


def test_ledger_completion_rejects_an_unpartitionable_scene_without_sealing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    project_id = document.project.id
    full = _draft(
        document,
        script,
        spec,
        dialogue_shots=dialogue_shots,
    )
    unpartitionable_first_entry = full.timing_ledger[0].model_copy(
        update={
            "allocated_duration_seconds": 3.5,
            "non_speech_reserve_seconds": 2.5,
        },
        deep=True,
    )
    begun = service.begin_storyboard_draft(
        project_id,
        StoryboardDraftBeginRequest(
            seed=_seed_from_draft(full),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress
    ledger = service.append_storyboard_ledger_segment(
        project_id,
        StoryboardDraftLedgerSegmentAppendRequest(
            draft_document_id=begun.draft_ref.artifact_id,
            base_version_id=begun.draft_version_id,
            entries=[unpartitionable_first_entry, *full.timing_ledger[1:]],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    ).progress

    with pytest.raises(WorkflowConflictError, match="cannot be partitioned"):
        service.complete_storyboard_ledger(
            project_id,
            StoryboardDraftLedgerCompleteRequest(
                draft_document_id=begun.draft_ref.artifact_id,
                base_version_id=ledger.draft_version_id,
                notes=["This failed completion note must not be persisted."],
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    persisted = service.get_production_document(
        project_id,
        begun.draft_ref.artifact_id,
    )
    assert persisted.document.current_version_id == ledger.draft_version_id
    content = StoryboardDraftContent.model_validate(persisted.version.payload)
    assert content.phase == StoryboardDraftPhase.LEDGER_BUILDING
    assert "This failed completion note must not be persisted." not in content.timing_notes
