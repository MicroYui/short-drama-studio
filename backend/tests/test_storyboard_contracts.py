"""Offline contract coverage for storyboard planning and stable handoffs."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.agent_runtime.models import WorkOrder
from short_drama.models import (
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationRange,
    DurationRhythmSection,
    DurationTargetOrigin,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ProjectDocument,
    ShotPlan,
)
from short_drama.production_contracts import (
    ClipGroup,
    OutputSlotBindRequest,
    OutputSlotInvalidateRequest,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardPatchRequest,
    StoryboardShotReplacement,
    apply_storyboard_patch,
    bind_project_output_slot,
    compile_generation_plan,
    invalidate_project_output_slot,
)
from short_drama.service import ShortDramaService


def _fixture_document(tmp_path: Path) -> ProjectDocument:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    return service.initialize_fixture().document


def _spec_and_storyboard(
    document: ProjectDocument,
) -> tuple[ProductionSpecContent, StoryboardContent]:
    project = document.project
    source_shots = document.shots[:2]
    shots: list[ShotPlan] = [
        source_shots[0].model_copy(update={"dialogue_line_ids": []}),
        source_shots[1].model_copy(
            update={"dialogue_line_ids": ["script-line-opening"]}
        ),
    ]
    duration = sum(item.duration_seconds for item in shots)
    spec = ProductionSpecContent(
        project_id=project.id,
        script_version_id=project.current_script_version_id,
        bible_version_id=project.current_bible_version_id,
        title="《别相信我》竖屏制片规格",
        production_scope="完成剧本、分镜、核心资产、视频和初步时间线。",
        production_mode="Agent 自主制片，保留稳定的编辑镜头边界。",
        genre_and_setting="雨夜便利店内发生的悬疑对峙。",
        language="zh-CN",
        aspect_ratio="9:16",
        target_duration_seconds=duration,
        duration_intent="在一个短生成单元内完成建立空间与抛出证据。",
        visual=ProductionVisualDirection(
            medium_lock="写实影像",
            style_brief=["竖屏近距离对峙", "快速进入冲突"],
            color_palette=["冷蓝雨夜", "便利店暖白灯"],
            lighting_intent="门外冷色雨光与店内顶灯形成对比。",
            forbidden_elements=["无关路人抢占前景"],
        ),
        audio=ProductionAudioDirection(
            dialogue_intent="台词清晰、克制。",
            ambience_intent="雨声和冰箱低频嗡鸣持续存在。",
            bgm_intent="证据被说出时才进入短促低频。",
            subtitle_intent="保留逐句可编辑字幕。",
        ),
        provider_preference=ProductionProviderPreference(
            provider="minimax",
            model="MiniMax-H3",
            resolution="768P",
        ),
        user_constraints=["不启用 LTX 模型"],
        agent_decisions=["前两个编辑镜头合并为一个生成单元"],
        notes=["这是离线合约测试数据"],
    )
    storyboard = StoryboardContent(
        project_id=project.id,
        title="开场完整分镜",
        script_version_id=project.current_script_version_id,
        bible_version_id=project.current_bible_version_id,
        production_spec_document_id="production-spec-document",
        production_spec_version_id="production-spec-version-1",
        script_line_ids=["script-line-opening"],
        shots=shots,
        clip_groups=[
            ClipGroup(
                id="clip-group-opening",
                title="雨夜推进与证据落点",
                shot_ids=[item.id for item in shots],
                duration_seconds=duration,
                generation_intent="先建立雨夜便利店，再落到林夏的证据台词。",
                transition_intent="保留从外景到林夏近景的硬切。",
                continuity_in=["林夏位于轴线左侧"],
                visible_changes=["镜头从店外进入店内"],
                continuity_out=["林夏视线锁定周衡"],
                reference_notes=["保持店内外光色对比"],
            )
        ],
        computed_duration_seconds=duration,
        timing_notes=["第二镜台词说完后留一个反应拍"],
        notes=["编辑镜头边界不因合并生成而丢失"],
    )
    return spec, storyboard


def test_project_document_loads_legacy_json_without_new_contract_fields(
    tmp_path: Path,
) -> None:
    legacy = _fixture_document(tmp_path).model_dump(mode="json")
    for field_name in (
        "production_spec_document_id",
        "storyboard_document_id",
        "generation_plan_document_id",
        "output_slots",
    ):
        legacy.pop(field_name)

    restored = ProjectDocument.model_validate(legacy)

    assert restored.production_spec_document_id is None
    assert restored.storyboard_document_id is None
    assert restored.generation_plan_document_id is None
    assert restored.output_slots == []


def test_complete_spec_and_storyboard_compile_two_shots_into_contiguous_windows(
    tmp_path: Path,
) -> None:
    spec, storyboard = _spec_and_storyboard(_fixture_document(tmp_path))

    restored_spec = ProductionSpecContent.model_validate(
        spec.model_dump(mode="json")
    )
    restored_storyboard = StoryboardContent.model_validate(
        storyboard.model_dump(mode="json")
    )
    plan = compile_generation_plan(
        storyboard=restored_storyboard,
        storyboard_document_id="storyboard-document",
        storyboard_version_id="storyboard-version-1",
        production_spec=restored_spec,
        production_spec_document_id="production-spec-document",
        production_spec_version_id="production-spec-version-1",
    )

    assert restored_spec.visual.medium_lock == "写实影像"
    assert restored_spec.audio.subtitle_intent == "保留逐句可编辑字幕。"
    assert len(restored_storyboard.shots) == 2
    assert len(plan.units) == 1
    unit = plan.units[0]
    first, second = unit.shot_windows
    assert unit.shot_ids == [item.id for item in storyboard.shots]
    assert first.source_in_seconds == 0.0
    assert first.source_out_seconds == storyboard.shots[0].duration_seconds
    assert second.source_in_seconds == first.source_out_seconds
    assert second.source_out_seconds == storyboard.computed_duration_seconds
    assert unit.requested_duration_seconds == storyboard.computed_duration_seconds
    assert unit.provider == "minimax"
    assert unit.model == "MiniMax-H3"


def test_duration_contract_is_structured_without_inventing_a_fixed_tolerance() -> None:
    contract = DurationContract(
        target_seconds=120.0,
        target_origin=DurationTargetOrigin.USER_DECLARED,
        authority=DurationAuthority.TARGET_LED,
        acceptable_range_seconds=DurationRange(
            minimum_seconds=110.0,
            maximum_seconds=130.0,
        ),
        source_fidelity_policy=(
            DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
        ),
        mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
        decision_basis="用户要求约两分钟，允许 Agent 依据自然表演解释可接受范围。",
        rhythm_shape=[
            DurationRhythmSection(
                label="第一次震惊",
                narrative_function="让观众立即理解主角优势。",
                relative_density="快，但保留饭勺落地的反应拍。",
                scene_sequences=[1],
                payoff_or_handoff="引出校长赶来。",
            )
        ],
    )

    restored = DurationContract.model_validate(contract.model_dump(mode="json"))

    assert restored == contract
    with pytest.raises(ValidationError, match="minimum cannot exceed maximum"):
        DurationRange(minimum_seconds=130.0, maximum_seconds=110.0)
    with pytest.raises(ValidationError, match="inside its acceptable range"):
        DurationContract.model_validate(
            {
                **contract.model_dump(mode="json"),
                "target_seconds": 150.0,
            }
        )


def test_production_spec_upgrades_legacy_duration_without_losing_intent(
    tmp_path: Path,
) -> None:
    spec, _ = _spec_and_storyboard(_fixture_document(tmp_path))
    legacy = spec.model_dump(mode="json")
    legacy.pop("duration_contract")

    restored = ProductionSpecContent.model_validate(legacy)

    assert restored.duration_contract.target_seconds == spec.target_duration_seconds
    assert (
        restored.duration_contract.target_origin
        == DurationTargetOrigin.LEGACY_UNKNOWN
    )
    assert restored.duration_contract.authority == DurationAuthority.SOURCE_LED
    assert restored.duration_contract.acceptable_range_seconds is None
    assert (
        restored.duration_contract.source_fidelity_policy
        == DurationFidelityPolicy.PRESERVE_VERBATIM
    )
    assert (
        restored.duration_contract.mismatch_resolution
        == DurationMismatchResolution.REPORT_NATURAL_RUNTIME
    )
    assert restored.duration_contract.decision_basis == spec.duration_intent

    assert restored.target_duration_seconds is not None
    mismatched_contract = restored.duration_contract.model_copy(
        update={"target_seconds": restored.target_duration_seconds + 1.0}
    )
    with pytest.raises(ValidationError, match="duration targets must match"):
        ProductionSpecContent.model_validate(
            {
                **restored.model_dump(mode="json"),
                "duration_contract": mismatched_contract.model_dump(mode="json"),
            }
        )


def test_storyboard_patch_only_revises_the_target_and_preserves_history(
    tmp_path: Path,
) -> None:
    _, storyboard = _spec_and_storyboard(_fixture_document(tmp_path))
    historical_snapshot = storyboard.model_dump(mode="json")
    target = storyboard.shots[1]
    replacement = target.model_copy(
        update={"visual": "林夏越过挂着雨滴的玻璃，正面锁定周衡"}
    )

    revised = apply_storyboard_patch(
        storyboard,
        StoryboardPatchRequest(
            base_version_id="storyboard-version-1",
            replacements=[
                StoryboardShotReplacement(
                    shot_id=target.id,
                    expected_revision=target.revision,
                    replacement=replacement,
                )
            ],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    assert revised.shots[0].revision == storyboard.shots[0].revision
    assert revised.shots[0].model_dump() == storyboard.shots[0].model_dump()
    assert revised.shots[1].revision == target.revision + 1
    assert revised.shots[1].visual == replacement.visual
    assert storyboard.model_dump(mode="json") == historical_snapshot
    assert storyboard.shots[1].visual != revised.shots[1].visual


def test_output_slot_bind_invalidate_and_rebind_retains_artifact_history() -> None:
    project_id = "project-contract-test"
    first_ref = ProductionDocumentArtifactRef(
        project_id=project_id,
        artifact_type="storyboard",
        artifact_id="storyboard-document",
        version=1,
    )
    second_ref = first_ref.model_copy(update={"version": 2})

    bound = bind_project_output_slot(
        project_id=project_id,
        current=None,
        request=OutputSlotBindRequest(
            slot_id="storyboard.current",
            artifact_ref=first_ref,
        ),
        updated_at="2026-09-04T12:00:00+08:00",
    )
    invalidated = invalidate_project_output_slot(
        project_id=project_id,
        current=bound,
        request=OutputSlotInvalidateRequest(
            slot_id=bound.id,
            reason="分镜已被局部修改",
            expected_revision=bound.revision,
        ),
        updated_at="2026-09-04T12:01:00+08:00",
    )
    rebound = bind_project_output_slot(
        project_id=project_id,
        current=invalidated,
        request=OutputSlotBindRequest(
            slot_id=bound.id,
            artifact_ref=second_ref,
            expected_revision=invalidated.revision,
        ),
        updated_at="2026-09-04T12:02:00+08:00",
    )

    assert bound.revision == 1
    assert invalidated.revision == 2
    assert invalidated.current_artifact_ref is None
    assert invalidated.previous_artifact_refs == [first_ref]
    assert [item.revision for item in invalidated.revision_history] == [1]
    assert invalidated.revision_history[0].current_artifact_ref == first_ref
    assert rebound.revision == 3
    assert rebound.current_artifact_ref == second_ref
    assert rebound.previous_artifact_refs == [first_ref]
    assert rebound.invalidation_reason is None
    assert [item.revision for item in rebound.revision_history] == [1, 2]
    assert rebound.revision_history[1].current_artifact_ref is None
    assert rebound.revision_history[1].invalidation_reason == "分镜已被局部修改"


def test_work_order_output_slots_round_trip_and_legacy_records_remain_readable() -> None:
    work_order = WorkOrder(
        id="work-order-storyboard",
        parent_run_id="agent-run-1",
        sender="production-manager",
        recipient_role="storyboard-artist",
        objective="完成可执行的结构化分镜",
        why_now="剧本和制片规格已稳定",
        expected_output_slot_ids=[
            "storyboard.current",
            "video.generation-plan.current",
        ],
        acceptance_criteria=["所有剧本台词按顺序归属且仅归属一个镜头"],
    )

    restored = WorkOrder.model_validate(work_order.model_dump(mode="json"))

    assert restored.expected_output_slot_ids == work_order.expected_output_slot_ids
    assert restored.context_manifest is not None
    assert (
        restored.context_manifest.expected_output_slot_ids
        == work_order.expected_output_slot_ids
    )

    legacy = work_order.model_dump(mode="json")
    legacy.pop("expected_output_slot_ids")
    context_manifest = legacy["context_manifest"]
    assert isinstance(context_manifest, dict)
    context_manifest.pop("expected_output_slot_ids")

    restored_legacy = WorkOrder.model_validate(legacy)

    assert restored_legacy.expected_output_slot_ids == []
    assert restored_legacy.context_manifest is not None
    assert restored_legacy.context_manifest.expected_output_slot_ids == []
