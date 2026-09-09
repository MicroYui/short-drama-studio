"""Focused coverage for the extracted project artifact resolver."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver as RuntimeProjectArtifactResolver,
    _storyboard_draft_summary,
    project_artifact_refs,
    project_summary,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import (
    CreateProjectRequest,
    DurationAlignment,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationFitAssessment,
    DurationMismatchResolution,
    DurationRange,
    DurationTargetOrigin,
    PlanningRunRecord,
    PlanningRunStatus,
    PlanningRunStep,
    ProductionDocument,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ProductionDocumentVersion,
    ProjectPlanningMetadata,
    ProjectOutputSlot,
    ReviewRepairOwner,
    ScriptReviewIssue,
    ScriptReviewReport,
    ScriptReviewSeverity,
    StoryIntent,
    StoryRevisionVerification,
    StoryRevisionViolation,
)
from short_drama.production_contracts import (
    ClipGroup,
    PRODUCTION_SPEC_DOCUMENT_KIND,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    STORYBOARD_DRAFT_DOCUMENT_KIND,
    ProductionSpecContent,
    StoryboardClipGroupDraft,
    StoryboardClipGroupOutline,
    StoryboardDraftContent,
    StoryboardDraftPhase,
    StoryboardDraftVerificationFinding,
    StoryboardDraftVerificationReport,
    StoryboardDraftVerificationStatus,
    StoryboardSceneNode,
    StoryboardSourceRange,
    StoryboardTimingAudit,
    StoryboardTimingLedgerEntry,
)
from short_drama.production_manager import (
    ProjectArtifactResolver,
    _compact_specialist_tool_result,
)
from short_drama.repository import ShortDramaRepository


def _fixture_resolver(
    tmp_path: Path,
) -> tuple[RuntimeProjectArtifactResolver, ShortDramaRepository]:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    repository.save_project(
        build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    )
    return RuntimeProjectArtifactResolver(repository), repository


def test_production_manager_reexports_extracted_resolver(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]

    assert ProjectArtifactResolver is RuntimeProjectArtifactResolver

    summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id=document.project.id,
        )
    )

    assert summary is not None
    assert summary.title == document.project.name
    assert summary.summary == project_summary(document)
    assert f"Project {document.project.name} ({document.project.id})" in summary.summary
    assert "Current script DurationContract: none" in summary.summary


def test_resolver_preserves_focused_document_summaries(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shot = document.shots[0]

    script_summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="script_version",
            artifact_id=script.id,
            version=script.version,
        )
    )
    shot_summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
    )

    assert script_summary is not None
    assert script_summary.title == f"Script v{script.version}"
    script_payload = json.loads(script_summary.summary)
    assert script_payload["screenplay_text"] == script.screenplay_text
    assert script_payload["structured_scenes"][0]["heading"] == (
        script.structured_scenes[0].heading
    )
    assert script_payload["structured_scenes"][0]["screenplay_text"] == (
        script.structured_scenes[0].screenplay_text
    )
    assert script_payload["story_intent"] is None
    assert script_payload["ending_contract"] is None
    assert script_payload["source_contract"] is None
    assert shot_summary is not None
    assert shot_summary.title == f"Shot {shot.sequence}"
    assert json.loads(shot_summary.summary)["id"] == shot.id


def test_long_script_context_prioritizes_duration_contract_with_null_or_range(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "duration-contract-context.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    script.original_text = "原始长正文" * 2000
    script.screenplay_text = "分镜长正文" * 2000
    script.spoken_text = "口播长正文" * 2000
    script.subtitle_text = "字幕长正文" * 2000

    accepted_ranges = (
        None,
        DurationRange(minimum_seconds=108.0, maximum_seconds=132.0),
    )
    for accepted_range in accepted_ranges:
        contract = DurationContract(
            target_seconds=120.0,
            target_origin=DurationTargetOrigin.USER_DECLARED,
            authority=DurationAuthority.TARGET_LED,
            acceptable_range_seconds=accepted_range,
            source_fidelity_policy=(
                DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
            ),
            mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
            decision_basis="用户明确要求约两分钟的完整故事。",
        )
        script.story_intent = StoryIntent(
            audience_experience="观众体验" * 400,
            dramatic_engine="戏剧引擎" * 400,
            progression_strategy="推进策略" * 400,
            tone_and_plausibility="调性可信" * 400,
            duration_contract=contract,
        )
        repository.save_project(document)
        resolver = RuntimeProjectArtifactResolver(repository)

        summary = resolver.summarize_artifact(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="script_version",
                artifact_id=script.id,
                version=script.version,
            )
        )

        assert summary is not None
        prefix = summary.summary[:3000]
        assert '"story_intent":' in prefix
        assert '"duration_contract":' in prefix
        assert summary.summary.index('"duration_contract":') < summary.summary.index(
            '"audience_experience":'
        )
        assert summary.summary.index('"story_intent":') < summary.summary.index(
            '"original_text":'
        )
        expected_contract = contract.model_dump(mode="json")
        assert json.loads(summary.summary)["story_intent"]["duration_contract"] == (
            expected_contract
        )
        if accepted_range is None:
            assert '"acceptable_range_seconds":null' in prefix
        else:
            assert (
                '"acceptable_range_seconds":{"minimum_seconds":108.0,'
                '"maximum_seconds":132.0}'
            ) in prefix

        compact_contract = json.dumps(
            expected_contract,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        project_context = project_summary(document)
        assert f"Current script DurationContract: {compact_contract}" in project_context
        assert project_context.index("Current script DurationContract:") < (
            project_context.index("Original user brief:")
        )


def test_project_get_compact_keeps_exact_current_duration_contract() -> None:
    accepted_ranges = (
        None,
        DurationRange(minimum_seconds=108.0, maximum_seconds=132.0),
    )
    for accepted_range in accepted_ranges:
        contract = DurationContract(
            target_seconds=120.0,
            target_origin=DurationTargetOrigin.USER_DECLARED,
            authority=DurationAuthority.TARGET_LED,
            acceptable_range_seconds=accepted_range,
            source_fidelity_policy=(
                DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
            ),
            mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
            decision_basis="用户明确要求约两分钟的完整故事。",
        )
        expected_contract = contract.model_dump(mode="json")
        result: dict[str, JsonValue] = {
            "tool_id": "project.get",
            "status": "SUCCEEDED",
            "output": {
                "document": {
                    "project": {
                        "id": "project-duration-context",
                        "current_script_version_id": "script-current",
                        "current_bible_version_id": "bible-current",
                    },
                    "script_versions": [
                        {
                            "id": "script-old",
                            "story_intent": {"duration_contract": None},
                        },
                        {
                            "id": "script-current",
                            "story_intent": {
                                "duration_contract": expected_contract,
                            },
                        },
                    ],
                    "bible_versions": [{"id": "bible-current"}],
                    "production_documents": [],
                    "output_slots": [],
                }
            },
            "artifact_refs": [],
            "warnings": [],
            "error": None,
        }

        compact = _compact_specialist_tool_result("project.get", result)
        output = compact["output"]
        assert isinstance(output, dict)
        compact_document = output["document"]
        assert isinstance(compact_document, dict)
        scripts = compact_document["script_versions"]
        assert isinstance(scripts, list)
        assert len(scripts) == 1
        current_script = scripts[0]
        assert isinstance(current_script, dict)
        story_intent = current_script["story_intent"]
        assert isinstance(story_intent, dict)
        assert story_intent["duration_contract"] == expected_contract


def test_project_get_compact_keeps_exact_current_and_approved_asset_versions() -> None:
    source_version: dict[str, JsonValue] = {
        "id": "asset-version-source-intake",
        "version": 1,
        "origin_type": "USER_PROVIDED",
        "descriptor": "用户指定的男主角身份照片",
        "media_type": "IMAGE",
        "local_path": "/tmp/hero.png",
        "external_url": None,
        "approval_status": "APPROVED",
        "review_notes": "已登记为 source intake。",
    }
    stale_version: dict[str, JsonValue] = {
        **source_version,
        "id": "asset-version-stale",
        "version": 0,
    }
    project_record: dict[str, JsonValue] = {
        "id": "project-source-intake",
        "current_script_version_id": "script-current",
        "current_bible_version_id": "bible-current",
        "input_images": [
            {
                "local_path": "/tmp/hero.png",
                "external_url": None,
                "descriptor": "这就是男主角",
            }
        ],
    }
    result: dict[str, JsonValue] = {
        "tool_id": "project.get",
        "status": "SUCCEEDED",
        "output": {
            "document": {
                "project": project_record,
                "script_versions": [{"id": "script-current"}],
                "bible_versions": [{"id": "bible-current"}],
                "production_documents": [],
                "output_slots": [],
                "assets": [
                    {
                        "id": "asset-source-intake",
                        "kind": "REFERENCE",
                        "name": "用户输入图 1",
                        "bible_entity_id": None,
                        "brief": None,
                        "current_version_id": source_version["id"],
                        "approved_version_id": source_version["id"],
                        "versions": [stale_version, source_version],
                    }
                ],
            }
        },
        "artifact_refs": [],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result("project.get", result)

    output = compact["output"]
    assert isinstance(output, dict)
    compact_document = output["document"]
    assert isinstance(compact_document, dict)
    assert compact_document["project"] == project_record
    assert compact_document["assets"] == [
        {
            "id": "asset-source-intake",
            "kind": "REFERENCE",
            "name": "用户输入图 1",
            "bible_entity_id": None,
            "brief": None,
            "current_version_id": source_version["id"],
            "approved_version_id": source_version["id"],
            "versions": [source_version],
        }
    ]


def test_storyboard_draft_summary_exposes_resume_ledger_without_shot_prose() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    scene = document.script_versions[0].structured_scenes[0]
    source_shots = [item for item in document.shots if item.dialogue_line_ids][:2]
    line_ids = [item.dialogue_line_ids[0] for item in source_shots]
    scene_node_id = f"scene-node-{scene.id}"
    ranges = [
        StoryboardSourceRange(
            id=f"range-{line_id}",
            scene_ids=[scene.id],
            script_line_ids=[line_id],
            description=f"Cover {line_id}.",
        )
        for line_id in line_ids
    ]
    timing_entries = [
        StoryboardTimingLedgerEntry(
            id=f"timing-{line_id}",
            sequence=sequence,
            scene_node_id=scene_node_id,
            source_range=source_range,
            source_text=f"Source beat {sequence}",
            dialogue_units=2,
            speech_rate_units_per_second=2.0,
            minimum_spoken_duration_seconds=1.0,
            non_speech_reserve_seconds=1.0,
            allocated_duration_seconds=source_shot.duration_seconds,
            natural_cut=f"Cut after source beat {sequence} lands.",
            continuity_in=(
                []
                if sequence == 1
                else ["The tray remains on the counter beside the protagonist."]
            ),
            visible_changes=[f"Source beat {sequence} completes visibly."],
            continuity_out=[
                "The tray remains on the counter beside the protagonist."
                if sequence == 1
                else "The protagonist now holds the tray."
            ],
            completes_scene=sequence == len(line_ids),
            planned_clip_group_id=f"clip-{line_id}",
            planned_shot_id=f"draft-shot-{sequence}",
        )
        for sequence, (line_id, source_range, source_shot) in enumerate(
            zip(line_ids, ranges, source_shots, strict=True),
            start=1,
        )
    ]
    plans = [
        StoryboardClipGroupOutline(
            id=f"clip-{line_id}",
            title=f"Clip {sequence}",
            scene_node_id=scene_node_id,
            scene_group_sequence=sequence,
            source_range=source_range,
            timing_entry_ids=[timing_entry.id],
            duration_seconds=source_shot.duration_seconds,
            objective=f"Stage source beat {sequence} without adding filler.",
            continuity_in=list(timing_entry.continuity_in),
            visible_changes=list(timing_entry.visible_changes),
            continuity_out=list(timing_entry.continuity_out),
        )
        for sequence, (line_id, source_range, source_shot, timing_entry) in enumerate(
            zip(line_ids, ranges, source_shots, timing_entries, strict=True),
            start=1,
        )
    ]
    first_shot = source_shots[0].model_copy(
        update={
            "id": "draft-shot-1",
            "project_id": document.project.id,
            "sequence": 1,
            "scene_id": scene.id,
            "visual": "LONG_CAMERA_TEXT_SHOULD_NOT_APPEAR",
        },
        deep=True,
    )
    committed = StoryboardClipGroupDraft(
        work_unit_id="work-unit-1",
        scene_node_id=scene_node_id,
        scene_group_sequence=1,
        source_range=ranges[0],
        shots=[first_shot],
        clip_group=ClipGroup(
            id=plans[0].id,
            title=plans[0].title,
            shot_ids=[first_shot.id],
            duration_seconds=first_shot.duration_seconds,
            generation_intent="Generate the first beat.",
            visible_changes=list(plans[0].visible_changes),
            continuity_out=["The tray remains on the counter beside the protagonist."],
        ),
    )
    computed_duration = sum(item.duration_seconds for item in plans)
    draft = StoryboardDraftContent(
        project_id=document.project.id,
        title="Resume-safe storyboard draft",
        script_version_id=document.script_versions[0].id,
        bible_version_id=document.bible_versions[0].id,
        production_spec_document_id="production-spec-1",
        production_spec_version_id="production-spec-version-1",
        script_line_ids=line_ids,
        scene_nodes=[
            StoryboardSceneNode(
                id=scene_node_id,
                scene_id=scene.id,
                sequence=1,
                heading=scene.heading,
                summary=scene.summary,
                source_line_ids=line_ids,
            )
        ],
        timing_ledger=timing_entries,
        planned_clip_groups=plans,
        timing_audit=StoryboardTimingAudit(
            target_duration_s=computed_duration,
            dialogue_units_total=4,
            minimum_spoken_duration_s=2.0,
            non_speech_reserve_s=2.0,
            computed_episode_duration_s=computed_duration,
            clip_group_count=2,
            alignment=DurationAlignment.ALIGNED,
            difference_seconds=0.0,
            assessment=DurationFitAssessment(
                estimated_range_seconds=DurationRange(
                    minimum_seconds=computed_duration,
                    maximum_seconds=computed_duration,
                ),
                alignment=DurationAlignment.ALIGNED,
                pressure_points=["Dialogue and visible actions fit the sealed outline."],
                concurrent_performance=[
                    "The reaction can play under the final spoken beat."
                ],
                resolution=DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
                rationale="The natural runtime matches the pinned target.",
            ),
            timing_audit_passed=True,
        ),
        committed_groups=[committed],
        verification=StoryboardDraftVerificationReport(
            status=StoryboardDraftVerificationStatus.NEEDS_REVISION,
            summary="The committed range needs one visible continuity repair.",
            checked_clip_group_ids=[plans[0].id],
            findings=[
                StoryboardDraftVerificationFinding(
                    id="finding-tray-state",
                    summary="The next group must show how the tray changes position.",
                    affected_clip_group_ids=[plans[0].id],
                    suggested_revision="Add the visible pickup before the next action.",
                )
            ],
        ),
    )

    summary = _storyboard_draft_summary(
        "storyboard-draft-1",
        "storyboard-draft-version-4",
        4,
        draft,
    )
    payload = json.loads(summary)

    assert [item["clip_group_id"] for item in payload["planned_clip_groups"]] == [
        plans[0].id,
        plans[1].id,
    ]
    assert payload["timing_audit"]["clip_group_count"] == 2
    assert payload["timing_audit"]["alignment"] == "ALIGNED"
    assert payload["timing_audit"]["difference_seconds"] == 0.0
    assert payload["timing_audit"]["assessment"]["resolution"] == (
        "REPORT_NATURAL_RUNTIME"
    )
    assert payload["timing_audit"]["assessment"]["rationale"] == (
        "The natural runtime matches the pinned target."
    )
    assert payload["committed_clip_group_ids"] == [plans[0].id]
    assert payload["next_uncommitted_group"]["clip_group_id"] == plans[1].id
    assert payload["latest_continuity_out"] == [
        "The tray remains on the counter beside the protagonist."
    ]
    assert payload["verification"]["status"] == "NEEDS_REVISION"
    assert payload["verification"]["findings"][0]["id"] == "finding-tray-state"
    assert payload["verification"]["findings"][0]["repair_scope"] == "CLIP_GROUP"
    assert payload["verification"]["findings"][0]["affected_scene_sequences"] == []
    assert "LONG_CAMERA_TEXT_SHOULD_NOT_APPEAR" not in summary


def test_storyboard_draft_summary_supports_partial_planning_phases() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    script = document.script_versions[0]
    scene = script.structured_scenes[0]
    line_ids = scene.line_ids[:2]
    scene_node_id = f"scene-node-{scene.id}"
    first_range = StoryboardSourceRange(
        id="range-first-line",
        scene_ids=[scene.id],
        script_line_ids=[line_ids[0]],
        description="Cover the first visible beat.",
    )
    second_range = StoryboardSourceRange(
        id="range-second-line",
        scene_ids=[scene.id],
        script_line_ids=[line_ids[1]],
        description="Cover the second visible beat.",
    )
    entries = [
        StoryboardTimingLedgerEntry(
            id="timing-first",
            sequence=1,
            scene_node_id=scene_node_id,
            source_range=first_range,
            source_text="First exact source beat.",
            dialogue_units=2,
            speech_rate_units_per_second=2.0,
            minimum_spoken_duration_seconds=1.0,
            non_speech_reserve_seconds=2.0,
            allocated_duration_seconds=6.0,
            natural_cut="First beat lands.",
            continuity_out=["The tray remains on the counter."],
        ),
        StoryboardTimingLedgerEntry(
            id="timing-second",
            sequence=2,
            scene_node_id=scene_node_id,
            source_range=second_range,
            source_text="Second exact source beat.",
            dialogue_units=2,
            speech_rate_units_per_second=2.0,
            minimum_spoken_duration_seconds=1.0,
            non_speech_reserve_seconds=2.0,
            allocated_duration_seconds=6.0,
            natural_cut="Second beat lands.",
            continuity_in=["The tray remains on the counter."],
            continuity_out=["The protagonist now holds the tray."],
            completes_scene=True,
        ),
    ]
    common = {
        "project_id": document.project.id,
        "title": "Partial storyboard plan",
        "script_version_id": script.id,
        "bible_version_id": document.bible_versions[0].id,
        "production_spec_document_id": "production-spec-1",
        "production_spec_version_id": "production-spec-1-v1",
        "script_line_ids": line_ids,
        "scene_nodes": [
            StoryboardSceneNode(
                id=scene_node_id,
                scene_id=scene.id,
                sequence=1,
                heading=scene.heading,
                summary=scene.summary,
                source_line_ids=line_ids,
            )
        ],
    }
    ledger_building = StoryboardDraftContent(
        phase=StoryboardDraftPhase.LEDGER_BUILDING,
        timing_ledger=[entries[0]],
        **common,
    )

    ledger_payload = json.loads(
        _storyboard_draft_summary("draft-1", "draft-1-v2", 2, ledger_building)
    )

    assert ledger_payload["phase"] == "LEDGER_BUILDING"
    assert ledger_payload["timing_audit"] is None
    assert ledger_payload["progress"]["timing_ledger_entry_count"] == 1
    assert ledger_payload["progress"]["planned_clip_group_count"] == 0
    assert ledger_payload["next_unplanned_source"] == {
        "scene_node_id": scene_node_id,
        "script_line_id": line_ids[1],
        "timing_entry_id": None,
    }
    assert ledger_payload["latest_planned_continuity_out"] == [
        "The tray remains on the counter."
    ]

    first_plan = StoryboardClipGroupOutline(
        id="clip-first",
        title="First beat",
        scene_node_id=scene_node_id,
        scene_group_sequence=1,
        source_range=first_range,
        timing_entry_ids=[entries[0].id],
        duration_seconds=6.0,
        objective="Stage the first beat.",
        continuity_out=list(entries[0].continuity_out),
    )
    outline_building = StoryboardDraftContent(
        phase=StoryboardDraftPhase.OUTLINE_BUILDING,
        timing_ledger=entries,
        planned_clip_groups=[first_plan],
        **common,
    )
    outline_payload = json.loads(
        _storyboard_draft_summary("draft-1", "draft-1-v5", 5, outline_building)
    )

    assert outline_payload["phase"] == "OUTLINE_BUILDING"
    assert len(outline_payload["timing_ledger"]) == 2
    assert outline_payload["progress"]["outlined_timing_entry_count"] == 1
    assert outline_payload["next_unplanned_source"]["timing_entry_id"] == (
        "timing-second"
    )
    assert outline_payload["latest_planned_continuity_out"] == [
        "The tray remains on the counter."
    ]


def test_duration_reconciliation_prioritizes_only_the_active_story_branch(
    tmp_path: Path,
) -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    pinned_script = document.script_versions[0]
    current_script = pinned_script.model_copy(
        update={
            "id": f"{pinned_script.id}-current",
            "version": pinned_script.version + 1,
            "parent_version_id": pinned_script.id,
            "change_reason": "A later unrelated screenplay revision.",
        },
        deep=True,
    )
    document.script_versions.append(current_script)
    document.project.current_script_version_id = current_script.id

    pinned_contract = DurationContract(
        target_seconds=30.0,
        target_origin=DurationTargetOrigin.USER_DECLARED,
        authority=DurationAuthority.TARGET_LED,
        acceptable_range_seconds=DurationRange(
            minimum_seconds=28.0,
            maximum_seconds=32.0,
        ),
        source_fidelity_policy=DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS,
        mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
        decision_basis="The user explicitly requested a 30-second episode.",
    )
    pinned_spec = ProductionSpecContent(
        project_id=document.project.id,
        script_version_id=pinned_script.id,
        bible_version_id=document.bible_versions[0].id,
        title="Pinned duration spec",
        production_scope="Compile the complete screenplay into one episode.",
        production_mode="Agent-led storyboard production.",
        genre_and_setting="A rainy-night convenience-store confrontation.",
        language="zh-CN",
        aspect_ratio="9:16",
        target_duration_seconds=30.0,
        duration_intent="Keep the requested short-form runtime explicit.",
        duration_contract=pinned_contract,
    )
    current_spec = pinned_spec.model_copy(
        update={
            "script_version_id": current_script.id,
            "target_duration_seconds": 45.0,
            "duration_contract": pinned_contract.model_copy(
                update={
                    "target_seconds": 45.0,
                    "acceptable_range_seconds": DurationRange(
                        minimum_seconds=42.0,
                        maximum_seconds=48.0,
                    ),
                    "decision_basis": "A later unrelated production-spec revision.",
                }
            ),
        },
        deep=True,
    )
    pinned_spec_version = ProductionDocumentVersion(
        id="production-spec-version-pinned",
        version=1,
        payload=pinned_spec.model_dump(mode="json"),
        origin=ProductionDocumentOrigin.AGENT,
        created_at="2026-09-02T00:00:01+00:00",
    )
    current_spec_version = ProductionDocumentVersion(
        id="production-spec-version-current",
        version=2,
        parent_version_id=pinned_spec_version.id,
        payload=current_spec.model_dump(mode="json"),
        origin=ProductionDocumentOrigin.AGENT,
        created_at="2026-09-02T00:00:02+00:00",
    )
    spec_document = ProductionDocument(
        id="production-spec-duration-context",
        project_id=document.project.id,
        kind=PRODUCTION_SPEC_DOCUMENT_KIND,
        title="Duration context spec",
        current_version_id=current_spec_version.id,
        versions=[pinned_spec_version, current_spec_version],
    )

    scene = pinned_script.structured_scenes[0]
    line_id = scene.line_ids[0]
    scene_node_id = f"scene-node-{scene.id}"
    source_range = StoryboardSourceRange(
        id="range-duration-reconciliation",
        scene_ids=[scene.id],
        script_line_ids=[line_id],
        description="Cover the first complete source beat.",
    )
    timing_entry = StoryboardTimingLedgerEntry(
        id="timing-duration-reconciliation",
        sequence=1,
        scene_node_id=scene_node_id,
        source_range=source_range,
        source_text="The complete source beat plays without filler.",
        dialogue_units=2,
        speech_rate_units_per_second=2.0,
        minimum_spoken_duration_seconds=1.0,
        non_speech_reserve_seconds=1.0,
        allocated_duration_seconds=12.0,
        natural_cut="The source beat resolves.",
        completes_scene=True,
    )
    plan = StoryboardClipGroupOutline(
        id="clip-duration-reconciliation",
        title="Complete source beat",
        scene_node_id=scene_node_id,
        scene_group_sequence=1,
        source_range=source_range,
        timing_entry_ids=[timing_entry.id],
        duration_seconds=12.0,
        objective="Preserve the complete source beat.",
    )
    assessment = DurationFitAssessment(
        estimated_range_seconds=DurationRange(
            minimum_seconds=11.0,
            maximum_seconds=13.0,
        ),
        alignment=DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
        pressure_points=[
            "Extending the beat to 28 seconds would add unsupported dramatic material."
        ],
        concurrent_performance=[
            "The visible reaction already overlaps the final spoken beat."
        ],
        resolution=DurationMismatchResolution.REVISE_SCRIPT,
        rationale=(
            "The sealed outline runs 18 seconds shorter than the pinned target and "
            "cannot reach its accepted range without a story-level decision."
        ),
    )
    draft = StoryboardDraftContent(
        phase=StoryboardDraftPhase.DURATION_RECONCILIATION,
        project_id=document.project.id,
        title="Duration reconciliation draft",
        script_version_id=pinned_script.id,
        bible_version_id=document.bible_versions[0].id,
        production_spec_document_id=spec_document.id,
        production_spec_version_id=pinned_spec_version.id,
        script_line_ids=[line_id],
        scene_nodes=[
            StoryboardSceneNode(
                id=scene_node_id,
                scene_id=scene.id,
                sequence=1,
                heading=scene.heading,
                summary=scene.summary,
                source_line_ids=[line_id],
            )
        ],
        timing_ledger=[timing_entry],
        planned_clip_groups=[plan],
        timing_audit=StoryboardTimingAudit(
            target_duration_s=30.0,
            dialogue_units_total=2,
            minimum_spoken_duration_s=1.0,
            non_speech_reserve_s=1.0,
            computed_episode_duration_s=12.0,
            clip_group_count=1,
            alignment=DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
            difference_seconds=-18.0,
            assessment=assessment,
            timing_audit_passed=True,
        ),
    )
    draft_version = ProductionDocumentVersion(
        id="storyboard-draft-version-reconciliation",
        version=4,
        payload=draft.model_dump(mode="json"),
        origin=ProductionDocumentOrigin.AGENT,
        created_at="2026-09-02T00:00:03+00:00",
    )
    draft_document = ProductionDocument(
        id="storyboard-draft-duration-context",
        project_id=document.project.id,
        kind=STORYBOARD_DRAFT_DOCUMENT_KIND,
        title="Duration reconciliation draft",
        current_version_id=draft_version.id,
        versions=[draft_version],
    )
    document.production_documents.extend([spec_document, draft_document])
    document.production_spec_document_id = spec_document.id
    document.output_slots.append(
        ProjectOutputSlot(
            id=PRODUCTION_SPEC_OUTPUT_SLOT_ID,
            revision=1,
            current_artifact_ref=ProductionDocumentArtifactRef(
                project_id=document.project.id,
                artifact_type="production_spec",
                artifact_id=spec_document.id,
                version=current_spec_version.version,
            ),
            updated_at="2026-09-02T00:00:02+00:00",
        )
    )

    pinned_script_ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="script_version",
        artifact_id=pinned_script.id,
        version=pinned_script.version,
    )
    pinned_spec_ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="production_spec",
        artifact_id=spec_document.id,
        version=pinned_spec_version.version,
    )
    current_spec_ref = pinned_spec_ref.model_copy(
        update={"version": current_spec_version.version}
    )
    current_script_ref = pinned_script_ref.model_copy(
        update={
            "artifact_id": current_script.id,
            "version": current_script.version,
        }
    )
    draft_ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="storyboard_draft",
        artifact_id=draft_document.id,
        version=draft_version.version,
    )
    draft_document_ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="production_document",
        artifact_id=draft_document.id,
        version=draft_version.version,
    )
    draft_version_ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="production_document_version",
        artifact_id=draft_version.id,
        version=draft_version.version,
    )
    refs = project_artifact_refs(document)

    assert refs[1] == current_script_ref
    assert pinned_script_ref not in refs
    assert pinned_spec_ref not in refs
    assert draft_ref not in refs
    assert draft_document_ref not in refs
    assert draft_version_ref not in refs

    current_script.story_intent = StoryIntent(
        audience_experience="Follow the current screenplay's exact duration promise.",
        dramatic_engine="The current visible source beat drives the episode.",
        progression_strategy="Keep the current production branch coherent.",
        tone_and_plausibility="Preserve the established current-story semantics.",
        duration_contract=current_spec.duration_contract.model_copy(deep=True),
    )
    document.project.target_duration_seconds = 45.0
    assert draft.timing_audit is not None
    active_draft = draft.model_copy(
        deep=True,
        update={
            "script_version_id": current_script.id,
            "production_spec_version_id": current_spec_version.id,
            "timing_audit": draft.timing_audit.model_copy(
                deep=True,
                update={
                    "target_duration_s": 45.0,
                    "difference_seconds": -33.0,
                },
            ),
        },
    )
    active_draft_version = ProductionDocumentVersion(
        id="storyboard-draft-version-active-reconciliation",
        version=5,
        parent_version_id=draft_version.id,
        payload=active_draft.model_dump(mode="json"),
        origin=ProductionDocumentOrigin.AGENT,
        created_at="2026-09-02T00:00:04+00:00",
    )
    draft_document.versions.append(active_draft_version)
    draft_document.current_version_id = active_draft_version.id
    active_draft_ref = draft_ref.model_copy(update={"version": 5})
    active_draft_document_ref = draft_document_ref.model_copy(
        update={"version": 5}
    )
    active_draft_version_ref = draft_version_ref.model_copy(
        update={
            "artifact_id": active_draft_version.id,
            "version": 5,
        }
    )
    active_refs = project_artifact_refs(document)

    assert active_refs[1:4] == [
        current_script_ref,
        current_spec_ref,
        active_draft_ref,
    ]
    assert pinned_script_ref not in active_refs
    assert pinned_spec_ref not in active_refs
    assert active_draft_document_ref in active_refs
    assert active_draft_version_ref in active_refs

    active_story_intent = current_script.story_intent
    assert active_story_intent is not None
    current_script.story_intent = active_story_intent.model_copy(
        deep=True,
        update={
            "duration_contract": current_spec.duration_contract.model_copy(
                deep=True,
                update={"target_seconds": 44.0},
            )
        },
    )
    mismatched_refs = project_artifact_refs(document)
    assert mismatched_refs[1] == current_script_ref
    assert current_spec_ref not in mismatched_refs
    assert active_draft_ref not in mismatched_refs
    assert active_draft_document_ref not in mismatched_refs
    assert active_draft_version_ref not in mismatched_refs
    current_script.story_intent = active_story_intent

    repository = ShortDramaRepository(tmp_path / "duration-context.sqlite3")
    repository.save_project(document)
    resolver = RuntimeProjectArtifactResolver(repository)
    spec_summary = resolver.summarize_artifact(pinned_spec_ref)
    draft_summary = resolver.summarize_artifact(draft_ref)

    assert spec_summary is not None
    spec_payload = json.loads(spec_summary.summary)
    assert spec_payload["production_spec_version_id"] == pinned_spec_version.id
    assert spec_payload["production_spec_version"] == pinned_spec_version.version
    assert spec_payload["script_version_id"] == pinned_script.id
    assert spec_payload["duration_contract"]["target_seconds"] == 30.0

    assert draft_summary is not None
    draft_payload = json.loads(draft_summary.summary)
    assert draft_payload["draft_version_id"] == draft_version.id
    assert draft_payload["script_version_id"] == pinned_script.id
    assert draft_payload["production_spec_version_id"] == pinned_spec_version.id
    assert draft_payload["timing_audit"]["alignment"] == (
        "OUTSIDE_ACCEPTED_RANGE"
    )
    assert draft_payload["timing_audit"]["difference_seconds"] == -18.0
    assert draft_payload["timing_audit"]["assessment"]["resolution"] == (
        "REVISE_SCRIPT"
    )
    assert '"alignment":"OUTSIDE_ACCEPTED_RANGE"' in draft_summary.summary[:3000]


def test_resolver_rejects_stale_or_mismatched_versioned_refs(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shot = document.shots[0]

    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id="another-project",
        )
    ) is None
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="script_version",
            artifact_id="another-script",
            version=script.version,
        )
    ) is None
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="script_version",
            artifact_id=script.id,
            version=script.version + 1,
        )
    ) is None
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="scene",
            artifact_id=script.structured_scenes[0].id,
            version=script.version + 1,
        )
    ) is None
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision + 1,
        )
    ) is None


def test_project_refs_and_missing_artifacts_remain_stable(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]

    refs = project_artifact_refs(document)
    ref_types = [ref.artifact_type for ref in refs]

    assert ref_types[0] == "project"
    assert "script_version" in ref_types
    assert "bible_version" in ref_types
    assert ref_types.count("scene") == len(document.script_versions[0].structured_scenes)
    assert ref_types.count("shot") == len(document.shots)
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id="missing-shot",
        )
    ) is None


def test_content_planning_job_resolver_accepts_legacy_artifact_ref(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    run = PlanningRunRecord(
        id="planning-run-resolver-compat",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.STORY_ARCHITECT,
        request=CreateProjectRequest(
            name=document.project.name,
            source_mode=document.project.source_mode,
            target_duration_seconds=document.project.target_duration_seconds,
            input_text=document.project.input_text,
            run_mode=document.project.run_mode,
        ),
        project_id=document.project.id,
        owner_agent_run_id="agent-run-resolver-compat",
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    document.planning = ProjectPlanningMetadata(
        run_id=run.id,
        status=run.status,
        owner_agent_run_id=run.owner_agent_run_id,
        planning_job_kind=run.job_kind,
    )
    assert repository.insert_project_with_planning_run(document, run)
    resolver = RuntimeProjectArtifactResolver(repository)

    canonical = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="content_planning_job",
            artifact_id=run.id,
        )
    )
    legacy = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="planning_run",
            artifact_id=run.id,
        )
    )

    assert canonical is not None
    assert legacy is not None
    assert canonical.title == legacy.title == "Content planning job STORY_ARCHITECT"
    assert "canonical script, Bible, shots, and assets remain in ProjectDocument" in (
        canonical.summary
    )


def test_content_planning_job_frontloads_pending_story_verifier_handoffs(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    duration_contract = DurationContract(
        target_seconds=120.0,
        target_origin=DurationTargetOrigin.USER_DECLARED,
        authority=DurationAuthority.TARGET_LED,
        acceptable_range_seconds=DurationRange(
            minimum_seconds=108.0,
            maximum_seconds=132.0,
        ),
        source_fidelity_policy=DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS,
        mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
        decision_basis="用户明确要求约两分钟，保持完整故事但压缩表演密度。",
    )
    run = PlanningRunRecord(
        id="planning-run-long-verifier-handoff",
        status=PlanningRunStatus.NEEDS_ATTENTION,
        current_step=PlanningRunStep.SCRIPT_VERIFIER,
        request=CreateProjectRequest(
            name=document.project.name,
            source_mode=document.project.source_mode,
            target_duration_seconds=document.project.target_duration_seconds,
            input_text=document.project.input_text,
            run_mode=document.project.run_mode,
        ),
        outline_json="LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD" * 1000,
        story_intent=StoryIntent(
            audience_experience="纯爽、快速升级。",
            dramatic_engine="主角的普通能力成为全场奇迹。",
            progression_strategy="从食堂震惊升级到校长挽留。",
            tone_and_plausibility="接受夸张逻辑，优先爽点。",
            duration_contract=duration_contract,
        ),
        review_report=ScriptReviewReport(
            summary="初次冷读要求围绕时长做一次聚焦修订。",
            issues=[
                ScriptReviewIssue(
                    issue_id="DURATION-1",
                    category="DURATION_FIT",
                    severity=ScriptReviewSeverity.HIGH,
                    owner=ReviewRepairOwner.STORY_ARCHITECT,
                    scene_sequences=[1],
                    observation="自然表演超出用户声明的两分钟目标。",
                    recommendation="保留事件，只压缩重复反应。",
                )
            ],
            duration_fit_assessment=DurationFitAssessment(
                estimated_range_seconds=DurationRange(
                    minimum_seconds=190.0,
                    maximum_seconds=210.0,
                ),
                alignment=DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
                resolution=DurationMismatchResolution.REVISE_SCRIPT,
                rationale="SCRIPT_DURATION_RATIONALE_SENTINEL",
            ),
            requires_revision=True,
        ),
        project_id=document.project.id,
        owner_agent_run_id="agent-run-long-verifier-handoff",
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    document.planning = ProjectPlanningMetadata(
        run_id=run.id,
        status=run.status,
        owner_agent_run_id=run.owner_agent_run_id,
        planning_job_kind=run.job_kind,
    )
    assert repository.insert_project_with_planning_run(document, run)
    resolver = RuntimeProjectArtifactResolver(repository)
    ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="content_planning_job",
        artifact_id=run.id,
    )

    script_verifier = resolver.summarize_artifact(ref)

    assert script_verifier is not None
    script_prefix = script_verifier.summary[:3000]
    assert '"source":"SCRIPT_VERIFIER"' in script_prefix
    assert '"requires_revision":true' in script_prefix
    assert '"alignment":"OUTSIDE_ACCEPTED_RANGE"' in script_prefix
    assert '"target_seconds":120.0' in script_prefix
    assert "SCRIPT_DURATION_RATIONALE_SENTINEL" in script_prefix
    assert script_verifier.summary.index("SCRIPT_DURATION_RATIONALE_SENTINEL") < (
        script_verifier.summary.index("LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD")
    )

    run.current_step = PlanningRunStep.REVISION_VERIFIER
    run.latest_revision_verification = StoryRevisionVerification(
        accepted=False,
        summary="修订稿仍未解决时长偏差。",
        violations=[
            StoryRevisionViolation(
                issue_id="DURATION-1",
                category="DURATION_FIT",
                scene_sequences=[1],
                observation="重复震惊反应仍然过长。",
                repair_instruction=(
                    "REVISION_REPAIR_INSTRUCTION_SENTINEL：只压缩重复反应，"
                    "不改变事件链。"
                ),
            )
        ],
        duration_fit_assessment=DurationFitAssessment(
            estimated_range_seconds=DurationRange(
                minimum_seconds=175.0,
                maximum_seconds=190.0,
            ),
            alignment=DurationAlignment.UNRESOLVED,
            resolution=DurationMismatchResolution.REVISE_SCRIPT,
            rationale="REVISION_DURATION_RATIONALE_SENTINEL",
        ),
    )
    run.updated_at = "2026-09-02T00:00:01+00:00"
    repository.save_planning_run_with_project(run, document)

    revision_verifier = resolver.summarize_artifact(ref)

    assert revision_verifier is not None
    revision_prefix = revision_verifier.summary[:3000]
    assert '"source":"REVISION_VERIFIER"' in revision_prefix
    assert '"accepted":false' in revision_prefix
    assert '"alignment":"UNRESOLVED"' in revision_prefix
    assert '"target_seconds":120.0' in revision_prefix
    assert "REVISION_DURATION_RATIONALE_SENTINEL" in revision_prefix
    assert "REVISION_REPAIR_INSTRUCTION_SENTINEL" in revision_prefix
    assert revision_verifier.summary.index("REVISION_DURATION_RATIONALE_SENTINEL") < (
        revision_verifier.summary.index("LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD")
    )


def test_scene_revision_artifact_title_exposes_persisted_queue_progress(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    run = PlanningRunRecord(
        id="planning-run-scene-revision-progress",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.SCENE_REVISION,
        request=CreateProjectRequest(
            name=document.project.name,
            source_mode=document.project.source_mode,
            target_duration_seconds=document.project.target_duration_seconds,
            input_text=document.project.input_text,
            run_mode=document.project.run_mode,
        ),
        revision_scene_sequences=[1, 4],
        next_revision_scene_index=0,
        project_id=document.project.id,
        owner_agent_run_id="agent-run-scene-revision-progress",
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    document.planning = ProjectPlanningMetadata(
        run_id=run.id,
        status=run.status,
        owner_agent_run_id=run.owner_agent_run_id,
        planning_job_kind=run.job_kind,
    )
    assert repository.insert_project_with_planning_run(document, run)
    resolver = RuntimeProjectArtifactResolver(repository)
    ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="content_planning_job",
        artifact_id=run.id,
    )

    first = resolver.summarize_artifact(ref)

    assert first is not None
    assert first.title == "Content planning job SCENE_REVISION 1/2"

    run.next_revision_scene_index = 1
    run.updated_at = "2026-09-02T00:00:01+00:00"
    repository.save_planning_run_with_project(run, document)
    second = resolver.summarize_artifact(ref)

    assert second is not None
    assert second.title == "Content planning job SCENE_REVISION 2/2"
