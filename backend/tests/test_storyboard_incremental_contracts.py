"""Offline coverage for incremental storyboard authoring contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from short_drama.models import (
    ApprovalStatus,
    ContinuityState,
    Freshness,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ShotPlan,
)
from short_drama.production_contracts import (
    ClipGroup,
    StoryboardClipGroupOutline,
    StoryboardClipGroupDraft,
    StoryboardDraftAppendRequest,
    StoryboardDraftBeginRequest,
    StoryboardDraftContent,
    StoryboardDraftPhase,
    StoryboardDraftSceneSeed,
    StoryboardDraftSeed,
    StoryboardSceneNode,
    StoryboardSourceRange,
    StoryboardTimingAudit,
    StoryboardTimingLedgerEntry,
    StoryboardWorkUnitContract,
    assemble_storyboard_draft,
)


def _shot(
    *,
    shot_id: str,
    scene_id: str,
    line_id: str,
    duration: float,
) -> ShotPlan:
    return ShotPlan(
        id=shot_id,
        project_id="project-incremental-storyboard",
        sequence=1,
        scene_id=scene_id,
        duration_seconds=duration,
        narrative_purpose=f"完成 {line_id} 对应的动作与反应",
        visual="人物在既定空间中完成动作，反应对象清晰可见。",
        framing="中近景",
        camera="稳定机位跟随表演重心",
        audio_intent="对白清晰并保留现场环境声",
        dialogue_line_ids=[line_id],
        continuity=ContinuityState(
            character_positions="主角在画面左侧",
            eyeline="主角看向画面右侧",
            prop_state_in="道具在桌面",
            prop_state_out="道具仍在桌面",
            action_in="动作开始前",
            action_out="动作完成后",
        ),
        transition_out="顺接下一反应",
        merge_candidate=True,
        local_rework_risk="低",
        approval_status=ApprovalStatus.DRAFT,
        freshness=Freshness.CURRENT,
    )


def _source_range(
    range_id: str,
    *,
    scene_ids: list[str],
    line_ids: list[str],
) -> StoryboardSourceRange:
    return StoryboardSourceRange(
        id=range_id,
        scene_ids=scene_ids,
        script_line_ids=line_ids,
        description="按完整动作、对白和反应形成语义连续的分镜范围。",
    )


def _group(
    *,
    work_unit_id: str,
    scene_node_id: str,
    scene_id: str,
    scene_group_sequence: int,
    line_id: str,
    duration: float,
) -> StoryboardClipGroupDraft:
    shot = _shot(
        shot_id=f"shot-{scene_id}",
        scene_id=scene_id,
        line_id=line_id,
        duration=duration,
    )
    return StoryboardClipGroupDraft(
        work_unit_id=work_unit_id,
        scene_node_id=scene_node_id,
        scene_group_sequence=scene_group_sequence,
        source_range=_source_range(
            f"range-{scene_id}",
            scene_ids=[scene_id],
            line_ids=[line_id],
        ),
        shots=[shot],
        clip_group=ClipGroup(
            id=f"clip-{scene_id}",
            title=f"{scene_id} 完整动作段",
            shot_ids=[shot.id],
            duration_seconds=duration,
            generation_intent="保持人物、空间和动作连续，完成这一段可生成画面。",
        ),
    )


def _draft(
    *,
    committed_groups: list[StoryboardClipGroupDraft] | None = None,
) -> StoryboardDraftContent:
    committed = committed_groups or []
    committed_by_id = {item.clip_group.id: item for item in committed}
    fallback_groups = [
        _group(
            work_unit_id=f"work-unit-{scene_sequence}",
            scene_node_id=f"scene-node-{scene_sequence}",
            scene_id=f"scene-{scene_sequence}",
            scene_group_sequence=1,
            line_id=f"line-{scene_sequence}",
            duration=duration,
        )
        for scene_sequence, duration in ((1, 5.0), (2, 6.0))
    ]
    ledger_groups = [
        committed_by_id.get(item.clip_group.id, item) for item in fallback_groups
    ]
    planned_clip_groups = [
        StoryboardClipGroupOutline(
            id=item.clip_group.id,
            title=item.clip_group.title,
            scene_node_id=item.scene_node_id,
            scene_group_sequence=item.scene_group_sequence,
            source_range=item.source_range,
            timing_entry_ids=[f"timing-{item.clip_group.id}"],
            duration_seconds=item.clip_group.duration_seconds,
            objective=item.clip_group.generation_intent,
            continuity_in=item.clip_group.continuity_in,
            visible_changes=item.clip_group.visible_changes,
            continuity_out=item.clip_group.continuity_out,
        )
        for item in ledger_groups
    ]
    timing_ledger = [
        StoryboardTimingLedgerEntry(
            id=f"timing-{item.clip_group.id}",
            sequence=sequence,
            scene_node_id=item.scene_node_id,
            source_range=item.source_range,
            source_text=f"Source beat for {item.clip_group.id}",
            dialogue_units=1,
            speech_rate_units_per_second=1.0,
            minimum_spoken_duration_seconds=1.0,
            non_speech_reserve_seconds=item.clip_group.duration_seconds - 1.0,
            allocated_duration_seconds=item.clip_group.duration_seconds,
            natural_cut=f"Natural cut after {item.clip_group.id}",
            continuity_in=item.clip_group.continuity_in,
            visible_changes=item.clip_group.visible_changes,
            continuity_out=item.clip_group.continuity_out,
            completes_scene=True,
            planned_clip_group_id=item.clip_group.id,
            planned_shot_id=item.shots[0].id,
        )
        for sequence, item in enumerate(ledger_groups, start=1)
    ]
    computed_duration = sum(item.duration_seconds for item in planned_clip_groups)
    return StoryboardDraftContent(
        phase=StoryboardDraftPhase.GROUP_AUTHORING,
        project_id="project-incremental-storyboard",
        title="增量分镜",
        script_version_id="script-v1",
        bible_version_id="bible-v1",
        production_spec_document_id="production-spec",
        production_spec_version_id="production-spec-v1",
        script_line_ids=["line-1", "line-2"],
        scene_nodes=[
            StoryboardSceneNode(
                id="scene-node-1",
                scene_id="scene-1",
                sequence=1,
                heading="内景 食堂 日",
                summary="主角完成计算，引发第一轮震惊。",
                source_line_ids=["line-1"],
            ),
            StoryboardSceneNode(
                id="scene-node-2",
                scene_id="scene-2",
                sequence=2,
                heading="内景 食堂 稍后",
                summary="校长到场，事件继续升级。",
                source_line_ids=["line-2"],
            ),
        ],
        timing_ledger=timing_ledger,
        planned_clip_groups=planned_clip_groups,
        timing_audit=StoryboardTimingAudit(
            target_duration_s=12.0,
            dialogue_units_total=2,
            minimum_spoken_duration_s=2.0,
            non_speech_reserve_s=computed_duration - 2.0,
            computed_episode_duration_s=computed_duration,
            clip_group_count=len(planned_clip_groups),
            timing_audit_passed=True,
        ),
        committed_groups=committed,
    )


def test_work_unit_may_cross_scenes_while_each_group_stays_in_one_scene() -> None:
    work_unit = StoryboardWorkUnitContract(
        id="work-unit-payoff-run",
        title="从第一次震惊推进到校长到场",
        source_range=_source_range(
            "range-payoff-run",
            scene_ids=["scene-1", "scene-2"],
            line_ids=["line-1", "line-2"],
        ),
        objective="保持空间连续地完成两段升级，不把 WorkOrder 当成镜头或生成组。",
        completion_criteria=["两场对白均有明确动作、反应和连续性承接"],
    )
    first_group = _group(
        work_unit_id=work_unit.id,
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=15.0,
    )
    second_group = _group(
        work_unit_id=work_unit.id,
        scene_node_id="scene-node-2",
        scene_id="scene-2",
        scene_group_sequence=1,
        line_id="line-2",
        duration=7.0,
    )

    first_append = StoryboardDraftAppendRequest(
        draft_document_id="storyboard-draft",
        base_version_id="storyboard-draft-v1",
        work_unit=work_unit,
        group=first_group,
        origin=ProductionDocumentOrigin.AGENT,
    )
    second_append = StoryboardDraftAppendRequest(
        draft_document_id="storyboard-draft",
        base_version_id="storyboard-draft-v2",
        work_unit=work_unit,
        group=second_group,
        origin=ProductionDocumentOrigin.AGENT,
    )

    assert first_append.group.scene_group_sequence == 1
    assert second_append.group.work_unit_id == first_append.work_unit.id
    # The WorkOrder spans both scenes by semantics. Only each executable H3
    # ClipGroup follows H3's integer 4–15 second generation contract.
    assert first_append.group.clip_group.duration_seconds == 15.0

    with pytest.raises(ValidationError, match="less than or equal to 15"):
        _group(
            work_unit_id=work_unit.id,
            scene_node_id="scene-node-1",
            scene_id="scene-1",
            scene_group_sequence=2,
            line_id="line-1",
            duration=22.0,
        )


def test_single_user_commits_follow_plan_order_before_canonical_assembly() -> None:
    first = _group(
        work_unit_id="work-unit-payoff-run",
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=5.0,
    )
    second = _group(
        work_unit_id="work-unit-payoff-run",
        scene_node_id="scene-node-2",
        scene_id="scene-2",
        scene_group_sequence=1,
        line_id="line-2",
        duration=6.0,
    )

    with pytest.raises(ValidationError, match="committed in planned order"):
        _draft(committed_groups=[second, first])

    storyboard = assemble_storyboard_draft(
        _draft(committed_groups=[first, second])
    )

    assert [item.id for item in storyboard.shots] == ["shot-scene-1", "shot-scene-2"]
    assert [item.sequence for item in storyboard.shots] == [1, 2]
    assert [item.id for item in storyboard.clip_groups] == [
        "clip-scene-1",
        "clip-scene-2",
    ]
    assert storyboard.script_line_ids == ["line-1", "line-2"]
    assert storyboard.computed_duration_seconds == 11.0
    assert storyboard.scene_nodes[0].clip_group_ids == ["clip-scene-1"]
    assert storyboard.scene_nodes[0].shot_ids == ["shot-scene-1"]
    assert storyboard.scene_nodes[1].clip_group_ids == ["clip-scene-2"]
    assert storyboard.scene_nodes[1].shot_ids == ["shot-scene-2"]


def test_begin_uses_a_lightweight_source_locked_seed() -> None:
    empty = _draft()
    request = StoryboardDraftBeginRequest(
        seed=StoryboardDraftSeed(
            project_id=empty.project_id,
            title=empty.title,
            script_version_id=empty.script_version_id,
            bible_version_id=empty.bible_version_id,
            production_spec_document_id=empty.production_spec_document_id,
            production_spec_version_id=empty.production_spec_version_id,
            scene_nodes=[
                StoryboardDraftSceneSeed(
                    id=node.id,
                    scene_id=node.scene_id,
                    sequence=node.sequence,
                )
                for node in empty.scene_nodes
            ],
        ),
        source_artifact_refs=[
            ProductionDocumentArtifactRef(
                project_id=empty.project_id,
                artifact_type="script",
                artifact_id=empty.script_version_id,
                version=1,
            )
        ],
        origin=ProductionDocumentOrigin.AGENT,
    )
    assert request.seed.scene_nodes[0].scene_id == "scene-1"
    assert "timing_ledger" not in request.seed.model_dump(mode="json")

    first = _group(
        work_unit_id="work-unit-1",
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=5.0,
    )
    with pytest.raises(ValueError, match="every planned ClipGroup"):
        assemble_storyboard_draft(_draft(committed_groups=[first]))


def test_staged_draft_accepts_prefixes_and_legacy_full_payload() -> None:
    complete = _draft()
    ledger_prefix = StoryboardDraftContent.model_validate(
        {
            **complete.model_dump(mode="json"),
            "phase": StoryboardDraftPhase.LEDGER_BUILDING,
            "timing_ledger": [complete.timing_ledger[0].model_dump(mode="json")],
            "planned_clip_groups": [],
            "timing_audit": None,
        }
    )
    assert ledger_prefix.phase == StoryboardDraftPhase.LEDGER_BUILDING
    assert ledger_prefix.timing_ledger[0].planned_clip_group_id == "clip-scene-1"

    outline_prefix = StoryboardDraftContent.model_validate(
        {
            **complete.model_dump(mode="json"),
            "phase": StoryboardDraftPhase.OUTLINE_BUILDING,
            "planned_clip_groups": [
                complete.planned_clip_groups[0].model_dump(mode="json")
            ],
            "timing_audit": None,
        }
    )
    assert outline_prefix.phase == StoryboardDraftPhase.OUTLINE_BUILDING

    legacy_payload = complete.model_dump(mode="json")
    legacy_payload.pop("phase")
    restored = StoryboardDraftContent.model_validate(legacy_payload)
    assert restored.phase == StoryboardDraftPhase.GROUP_AUTHORING

    staged_legacy_payload = complete.model_dump(mode="json")
    for item in staged_legacy_payload["timing_ledger"]:
        item.pop("completes_scene")
    staged_restored = StoryboardDraftContent.model_validate(staged_legacy_payload)
    assert all(item.completes_scene for item in staged_restored.timing_ledger)


def test_staged_draft_rejects_skipped_source_and_early_phase_transition() -> None:
    complete = _draft()
    with pytest.raises(ValidationError, match="advance through scene nodes in order"):
        StoryboardDraftContent.model_validate(
            {
                **complete.model_dump(mode="json"),
                "phase": StoryboardDraftPhase.LEDGER_BUILDING,
                "timing_ledger": [
                    complete.timing_ledger[1].model_copy(
                        update={"sequence": 1}
                    ).model_dump(mode="json")
                ],
                "planned_clip_groups": [],
                "timing_audit": None,
            }
        )
    with pytest.raises(ValidationError, match="cover all source lines"):
        StoryboardDraftContent.model_validate(
            {
                **complete.model_dump(mode="json"),
                "phase": StoryboardDraftPhase.OUTLINE_BUILDING,
                "timing_ledger": [complete.timing_ledger[0].model_dump(mode="json")],
                "planned_clip_groups": [],
                "timing_audit": None,
            }
        )


def test_append_rejects_a_group_outside_its_semantic_work_unit() -> None:
    work_unit = StoryboardWorkUnitContract(
        id="work-unit-scene-1",
        title="只处理第一场",
        source_range=_source_range(
            "range-scene-1",
            scene_ids=["scene-1"],
            line_ids=["line-1"],
        ),
        objective="完成第一场的动作与反应。",
        completion_criteria=["line-1 被完整覆盖"],
    )
    outside_group = _group(
        work_unit_id=work_unit.id,
        scene_node_id="scene-node-2",
        scene_id="scene-2",
        scene_group_sequence=1,
        line_id="line-2",
        duration=6.0,
    )

    with pytest.raises(ValidationError, match="source scenes"):
        StoryboardDraftAppendRequest(
            draft_document_id="storyboard-draft",
            base_version_id="storyboard-draft-v1",
            work_unit=work_unit,
            group=outside_group,
            origin=ProductionDocumentOrigin.AGENT,
        )


def test_action_only_timing_entry_has_no_speech_math() -> None:
    entry = StoryboardTimingLedgerEntry(
        id="timing-action-only",
        sequence=1,
        scene_node_id="scene-node-1",
        source_range=_source_range(
            "range-action-only",
            scene_ids=["scene-1"],
            line_ids=[],
        ),
        source_text="饭勺落地，全场同时转头。",
        dialogue_units=0,
        minimum_spoken_duration_seconds=0.0,
        non_speech_reserve_seconds=1.0,
        allocated_duration_seconds=1.0,
        natural_cut="饭勺停止弹跳后切入反应镜头。",
        completes_scene=True,
    )

    assert entry.speech_rate_units_per_second is None
    assert entry.planned_clip_group_id is None
    with pytest.raises(ValidationError, match="cannot declare a speech rate"):
        StoryboardTimingLedgerEntry.model_validate(
            {
                **entry.model_dump(mode="json"),
                "speech_rate_units_per_second": 3.5,
            }
        )


def test_clip_group_uses_integer_h3_seconds_and_exact_decimal_shot_sum() -> None:
    first = _shot(
        shot_id="shot-decimal-1",
        scene_id="scene-1",
        line_id="line-1",
        duration=1.25,
    )
    second = _shot(
        shot_id="shot-decimal-2",
        scene_id="scene-1",
        line_id="line-2",
        duration=2.75,
    )
    accepted = StoryboardClipGroupDraft(
        work_unit_id="work-decimal",
        scene_node_id="scene-node-1",
        scene_group_sequence=1,
        source_range=_source_range(
            "range-decimal",
            scene_ids=["scene-1"],
            line_ids=["line-1", "line-2"],
        ),
        shots=[first, second],
        clip_group=ClipGroup(
            id="clip-decimal",
            title="Decimal editorial shots",
            shot_ids=[first.id, second.id],
            duration_seconds=4,
            generation_intent="Generate both editorial shots as one H3 unit.",
        ),
    )
    assert accepted.clip_group.duration_seconds == 4

    with pytest.raises(ValidationError, match="valid integer"):
        ClipGroup(
            id="clip-fractional",
            title="Invalid fractional provider duration",
            shot_ids=[first.id],
            duration_seconds=4.5,
            generation_intent="This provider duration is not an integer.",
        )
    with pytest.raises(ValidationError, match="must equal its Shot durations"):
        StoryboardClipGroupDraft.model_validate(
            {
                **accepted.model_dump(mode="json"),
                "shots": [
                    first.model_dump(mode="json"),
                    second.model_copy(
                        update={"duration_seconds": 2.74}
                    ).model_dump(mode="json"),
                ],
            }
        )


def test_spoken_duration_is_a_lower_bound_and_allows_natural_rounding() -> None:
    rounded = StoryboardTimingLedgerEntry(
        id="timing-rounded-dialogue",
        sequence=1,
        scene_node_id="scene-node-1",
        source_range=_source_range(
            "range-rounded-dialogue",
            scene_ids=["scene-1"],
            line_ids=["line-1"],
        ),
        source_text="六个计时单位的对白。",
        dialogue_units=6,
        speech_rate_units_per_second=3.5,
        minimum_spoken_duration_seconds=1.8,
        non_speech_reserve_seconds=0.2,
        allocated_duration_seconds=2.0,
        natural_cut="对白落点后自然停顿。",
        completes_scene=True,
    )
    assert rounded.minimum_spoken_duration_seconds == 1.8

    with pytest.raises(ValidationError, match="cannot be shorter"):
        StoryboardTimingLedgerEntry.model_validate(
            {
                **rounded.model_dump(mode="json"),
                "minimum_spoken_duration_seconds": 1.6,
            }
        )


def test_scene_completion_covers_post_dialogue_and_action_only_scenes() -> None:
    dialogue = StoryboardTimingLedgerEntry(
        id="timing-dialogue",
        sequence=1,
        scene_node_id="scene-node-1",
        source_range=_source_range(
            "range-dialogue",
            scene_ids=["scene-1"],
            line_ids=["line-1"],
        ),
        source_text="主角说完台词。",
        dialogue_units=2,
        speech_rate_units_per_second=2.0,
        minimum_spoken_duration_seconds=1.0,
        non_speech_reserve_seconds=0.0,
        allocated_duration_seconds=1.0,
        natural_cut="对白落点仍保持镜头。",
    )
    post_dialogue_action = StoryboardTimingLedgerEntry(
        id="timing-post-dialogue-action",
        sequence=2,
        scene_node_id="scene-node-1",
        source_range=_source_range(
            "range-post-dialogue-action",
            scene_ids=["scene-1"],
            line_ids=[],
        ),
        source_text="全场先静止，再爆发惊呼。",
        dialogue_units=0,
        minimum_spoken_duration_seconds=0.0,
        non_speech_reserve_seconds=1.0,
        allocated_duration_seconds=1.0,
        natural_cut="群体反应完整落地。",
        completes_scene=True,
    )
    action_only_scene = StoryboardTimingLedgerEntry(
        id="timing-action-only-scene",
        sequence=3,
        scene_node_id="scene-node-2",
        source_range=_source_range(
            "range-action-only-scene",
            scene_ids=["scene-2"],
            line_ids=[],
        ),
        source_text="校门口横幅升起，主角走入阳光。",
        dialogue_units=0,
        minimum_spoken_duration_seconds=0.0,
        non_speech_reserve_seconds=2.0,
        allocated_duration_seconds=2.0,
        natural_cut="横幅与背影共同定格。",
        completes_scene=True,
    )
    common = {
        "project_id": "project-action-scenes",
        "title": "Action-aware ledger",
        "script_version_id": "script-v1",
        "bible_version_id": "bible-v1",
        "production_spec_document_id": "production-spec",
        "production_spec_version_id": "production-spec-v1",
        "script_line_ids": ["line-1"],
        "scene_nodes": [
            StoryboardSceneNode(
                id="scene-node-1",
                scene_id="scene-1",
                sequence=1,
                heading="内景 食堂 日",
                summary="对白之后还有完整群体反应。",
                source_line_ids=["line-1"],
            ),
            StoryboardSceneNode(
                id="scene-node-2",
                scene_id="scene-2",
                sequence=2,
                heading="外景 校门 日",
                summary="无对白的结尾动作场。",
                source_line_ids=[],
            ),
        ],
    }
    complete = StoryboardDraftContent(
        phase=StoryboardDraftPhase.OUTLINE_BUILDING,
        timing_ledger=[dialogue, post_dialogue_action, action_only_scene],
        **common,
    )
    assert complete.timing_ledger[-1].completes_scene is True

    with pytest.raises(ValidationError, match="explicitly complete every scene"):
        StoryboardDraftContent(
            phase=StoryboardDraftPhase.OUTLINE_BUILDING,
            timing_ledger=[dialogue],
            **common,
        )


def test_detailed_group_must_keep_the_full_planned_source_anchor() -> None:
    complete = _draft()
    first = _group(
        work_unit_id="work-unit-1",
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=5.0,
    )
    mismatched_anchor = first.model_copy(
        update={
            "source_range": first.source_range.model_copy(
                update={"ends_before": "另一个并不存在的动作锚点"}
            )
        },
        deep=True,
    )
    with pytest.raises(ValidationError, match="match its planned ClipGroup slot"):
        StoryboardDraftContent.model_validate(
            {
                **complete.model_dump(mode="json"),
                "committed_groups": [mismatched_anchor.model_dump(mode="json")],
            }
        )


def test_canonical_scene_continuity_is_derived_from_authored_groups() -> None:
    first = _group(
        work_unit_id="work-unit-1",
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=5.0,
    )
    first = first.model_copy(
        update={
            "clip_group": first.clip_group.model_copy(
                update={
                    "continuity_in": ["actual opening state"],
                    "continuity_out": ["actual ending state"],
                }
            )
        },
        deep=True,
    )
    second = _group(
        work_unit_id="work-unit-2",
        scene_node_id="scene-node-2",
        scene_id="scene-2",
        scene_group_sequence=1,
        line_id="line-2",
        duration=6.0,
    )
    draft = _draft(committed_groups=[first, second])
    seeded_nodes = [
        node.model_copy(
            update={
                "continuity_in": ["seed opening hint"],
                "continuity_out": ["seed ending hint"],
            }
        )
        for node in draft.scene_nodes
    ]
    draft = StoryboardDraftContent.model_validate(
        {**draft.model_dump(mode="json"), "scene_nodes": seeded_nodes}
    )

    storyboard = assemble_storyboard_draft(draft)

    assert storyboard.scene_nodes[0].continuity_in == ["actual opening state"]
    assert storyboard.scene_nodes[0].continuity_out == ["actual ending state"]


def test_assembly_requires_a_clean_timing_audit_but_not_verifier_approval() -> None:
    first = _group(
        work_unit_id="work-unit-1",
        scene_node_id="scene-node-1",
        scene_id="scene-1",
        scene_group_sequence=1,
        line_id="line-1",
        duration=5.0,
    )
    second = _group(
        work_unit_id="work-unit-2",
        scene_node_id="scene-node-2",
        scene_id="scene-2",
        scene_group_sequence=1,
        line_id="line-2",
        duration=6.0,
    )
    complete = _draft(committed_groups=[first, second])
    assert complete.verification is None
    assert assemble_storyboard_draft(complete).computed_duration_seconds == 11.0

    failed_audit = complete.model_copy(
        update={
            "timing_audit": complete.timing_audit.model_copy(
                update={"timing_audit_passed": False}
            )
        },
        deep=True,
    )
    with pytest.raises(ValueError, match="timing audit must pass"):
        assemble_storyboard_draft(failed_audit)
