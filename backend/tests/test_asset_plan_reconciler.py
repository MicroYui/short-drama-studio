"""Regression coverage for the semantic AssetPlan execution reconciler."""

from __future__ import annotations

import json
from pathlib import Path

from short_drama.asset_plan_reconciler import (
    assess_asset_plan_execution_contract,
    reconcile_asset_plan_execution_contract,
)
from short_drama.image_models import AssetReviewPurpose
from short_drama.models import (
    AssetKind,
    AssetLayoutProfile,
    AssetPresentationMode,
    AssetReferenceStrategy,
    AssetStandardView,
    CoreAssetBrief,
    ImageReferenceAuthority,
    ImageReferenceRole,
    ProductionDocumentOrigin,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRole,
)
from short_drama.production_contracts import (
    AssetPlanComposeCell,
    AssetPlanComposeIntent,
    AssetPlanContent,
    AssetPlanCreateRequest,
    AssetPlanDisposition,
    AssetPlanExecutionReconcileRequest,
    AssetPlanItem,
    AssetPlanOperation,
    AssetPlanPhysicalMasterCreateIntent,
    AssetPlanReference,
    AssetPlanReferencePackIntent,
    AssetPlanSubjectReconcileIntent,
    AssetPlanTextPlateIntent,
    AssetPlanTextPlateSpec,
    AssetPlanTextSurfaceIntent,
    AssetPlanWorkExecutionOverride,
    AssetPlanWorkItem,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_plan_runtime import _setup_story_truth


def _legacy_plan() -> AssetPlanContent:
    character_item_id = "character-linyi"
    face_work_id = "work-linyi-face"
    profile_work_id = "work-linyi-profile"
    sheet_work_id = "work-linyi-sheet"
    carrier_item_id = "prop-admission-letter"
    text_work_id = "work-admission-letter-text"
    return AssetPlanContent(
        project_id="project-legacy",
        script_version_id="script-legacy",
        bible_version_id="bible-legacy",
        production_spec_document_id="spec-document-legacy",
        production_spec_version_id="spec-version-legacy",
        storyboard_document_id="storyboard-document-legacy",
        storyboard_version_id="storyboard-version-legacy",
        items=[
            AssetPlanItem(
                id=character_item_id,
                disposition=AssetPlanDisposition.ANCHOR,
                asset_kind=AssetKind.CHARACTER,
                name="林一角色资产",
                brief=CoreAssetBrief(
                    identity_anchor="林一的五官、黑色碎发与高中生体态。",
                    visual_attributes=["十八岁男高中生", "五官清秀"],
                    continuity_anchors=["发型、脸型与校服全程一致"],
                    reference_strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
                    layout_profile=AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2,
                    requested_views=[
                        AssetStandardView.CHARACTER_FRONT,
                        AssetStandardView.CHARACTER_LEFT_PROFILE,
                        AssetStandardView.CHARACTER_SHEET,
                    ],
                    strategy_rationale="旧计划把最终角色卡当成 canonical。",
                ),
                rationale="主角需要跨镜头稳定身份。",
                work_items=[
                    AssetPlanWorkItem(
                        id=face_work_id,
                        operation=AssetPlanOperation.GENERATE,
                        output_role="character_face",
                        output_view=AssetStandardView.CHARACTER_FRONT,
                        final_prompt="生成林一正脸标准像。",
                        size="1024x1024",
                        review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                        reference_pack_intent=AssetPlanReferencePackIntent(
                            subject_item_id=character_item_id,
                            role=ReferenceRole.CHARACTER_FACE,
                            angle=ReferenceAngle.FRONT,
                            framing=ReferenceFraming.FACE_CLOSE_UP,
                            tags=["identity", "face"],
                        ),
                    ),
                    AssetPlanWorkItem(
                        id=profile_work_id,
                        operation=AssetPlanOperation.GENERATE,
                        output_role="character_left_profile",
                        output_view=AssetStandardView.CHARACTER_LEFT_PROFILE,
                        final_prompt="生成林一左侧身标准像。",
                        size="1024x1024",
                        review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                        reference_pack_intent=AssetPlanReferencePackIntent(
                            subject_item_id=character_item_id,
                            role=ReferenceRole.CHARACTER_PROFILE,
                            angle=ReferenceAngle.LEFT_PROFILE,
                            framing=ReferenceFraming.FULL_BODY,
                            tags=["identity", "profile"],
                        ),
                    ),
                    AssetPlanWorkItem(
                        id=sheet_work_id,
                        operation=AssetPlanOperation.COMPOSE,
                        output_role="final_character_card",
                        output_view=AssetStandardView.CANONICAL_MASTER,
                        final_prompt="把正脸与侧身拼成最终角色卡。",
                        size="1536x1024",
                        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        reference_pack_intent=AssetPlanReferencePackIntent(
                            subject_item_id=character_item_id,
                            role=ReferenceRole.CANONICAL_MASTER,
                            angle=ReferenceAngle.NOT_APPLICABLE,
                            framing=ReferenceFraming.WIDE,
                            tags=["legacy", "final-card"],
                        ),
                        compose_intent=AssetPlanComposeIntent(
                            strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
                            cells=[
                                AssetPlanComposeCell(
                                    source=AssetPlanReference(
                                        role="face_cell",
                                        purpose="放入正脸格。",
                                        source_work_item_id=face_work_id,
                                    ),
                                    view=AssetStandardView.CHARACTER_FRONT,
                                    label="正面",
                                ),
                                AssetPlanComposeCell(
                                    source=AssetPlanReference(
                                        role="profile_cell",
                                        purpose="放入左侧身格。",
                                        source_work_item_id=profile_work_id,
                                    ),
                                    view=AssetStandardView.CHARACTER_LEFT_PROFILE,
                                    label="左侧身",
                                ),
                            ],
                            columns=2,
                            show_labels=True,
                            background_color="#FFFFFF",
                            target_size="1536x1024",
                        ),
                        dependency_ids=[face_work_id, profile_work_id],
                    ),
                ],
            ),
            AssetPlanItem(
                id=carrier_item_id,
                disposition=AssetPlanDisposition.ANCHOR,
                asset_kind=AssetKind.PROP,
                name="录取通知书",
                brief=CoreAssetBrief(
                    identity_anchor="一张带有精确标题的纸质录取通知书。",
                    visual_attributes=["厚实纸张", "红色烫金边框"],
                    continuity_anchors=["纸张尺寸与边框不变"],
                    presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
                    required_visible_text=["录取通知书"],
                    reference_strategy=AssetReferenceStrategy.LIGHTWEIGHT,
                    layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
                    strategy_rationale="旧计划将物理载体与文字合并。",
                ),
                rationale="高考爽点需要可读的录取标题。",
                work_items=[
                    AssetPlanWorkItem(
                        id=text_work_id,
                        operation=AssetPlanOperation.TEXT_PLATE,
                        output_role="admission_letter_with_title",
                        final_prompt="本地排版录取通知书标题。",
                        size="1024x1024",
                        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        text_plate_intent=AssetPlanTextPlateIntent(
                            background_color="#FFFDF7",
                            foreground_color="#B20D0D",
                            title="录取通知书",
                            exact_lines=[],
                            layout_description="标题水平居中。",
                        ),
                    )
                ],
            ),
            AssetPlanItem(
                id="reference-untouched",
                disposition=AssetPlanDisposition.TEXT_ONLY,
                asset_kind=AssetKind.REFERENCE,
                name="食堂远景人群",
                rationale="仅作一次性环境氛围。",
            ),
        ],
        notes=["保留这条旧计划备注。"],
    )


def _reconcile_request(base_version_id: str) -> AssetPlanExecutionReconcileRequest:
    return AssetPlanExecutionReconcileRequest(
        base_version_id=base_version_id,
        subjects=[
            AssetPlanSubjectReconcileIntent(
                item_id="character-linyi",
                canonical_work_item_id="work-linyi-face",
                supplemental_work_item_ids=[
                    "work-linyi-profile",
                    "work-linyi-sheet",
                ],
            )
        ],
        text_surfaces=[
            AssetPlanTextSurfaceIntent(
                carrier_item_id="prop-admission-letter",
                create_carrier_master=AssetPlanPhysicalMasterCreateIntent(
                    work_item_id="work-admission-letter-physical",
                    output_role="admission_letter_physical_master",
                    output_view=AssetStandardView.PROP_PRODUCT,
                    final_prompt="生成无字的纸质录取通知书物理母版。",
                    size="1024x1024",
                ),
                remove_text_work_item_ids=["work-admission-letter-text"],
                plates=[
                    AssetPlanTextPlateSpec(
                        item_id="reference-admission-letter-title",
                        work_item_id="work-admission-letter-text",
                        name="录取通知书标题图版",
                        rationale="精确文字与物理纸张分层。",
                        output_role="admission_letter_title_plate",
                        size="1024x1024",
                        title="录取通知书",
                        exact_lines=[],
                        layout_description="标题水平居中，保持透明留白。",
                        background_color="#FFFDF7",
                        foreground_color="#B20D0D",
                    )
                ],
            )
        ],
        origin=ProductionDocumentOrigin.AGENT,
    )


def test_compiler_migrates_legacy_character_and_text_surface_contracts() -> None:
    legacy = _legacy_plan()
    legacy_payload = legacy.model_dump(mode="json")
    untouched_payload = legacy.items[2].model_dump(mode="json")

    first = reconcile_asset_plan_execution_contract(
        legacy,
        _reconcile_request("version-legacy"),
    )
    second = reconcile_asset_plan_execution_contract(
        legacy,
        _reconcile_request("version-legacy"),
    )

    assert legacy.model_dump(mode="json") == legacy_payload
    assert first.asset_plan.model_dump(mode="json") == second.asset_plan.model_dump(
        mode="json"
    )
    assert first.canonicalized_item_ids == ["character-linyi"]
    assert first.canonical_work_item_ids == ["work-linyi-face"]
    assert first.supplemental_work_item_ids == [
        "work-linyi-profile",
        "work-linyi-sheet",
    ]
    assert first.created_text_plate_item_ids == [
        "reference-admission-letter-title"
    ]
    assert first.removed_text_work_item_ids == ["work-admission-letter-text"]
    assert first.created_physical_master_work_item_ids == [
        "work-admission-letter-physical"
    ]

    character = first.asset_plan.items[0]
    assert character.brief is not None
    assert (
        character.brief.layout_profile
        == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2
    )
    assert [work.id for work in character.work_items] == [
        "work-linyi-face",
        "work-linyi-profile",
        "work-linyi-sheet",
    ]
    face, profile, sheet = character.work_items
    assert (
        face.resolved_output_layout_profile()
        == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
    )
    assert face.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
    assert face.reference_pack_intent is not None
    assert face.reference_pack_intent.role == ReferenceRole.CANONICAL_MASTER
    assert face.reference_pack_intent.lineage_root is None

    assert profile.operation == AssetPlanOperation.REFERENCE_GENERATE
    assert (
        profile.resolved_output_layout_profile()
        == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
    )
    assert profile.review_purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
    assert profile.dependency_ids[0] == face.id
    assert profile.ordered_references[0].source_work_item_id == face.id
    assert (
        profile.ordered_references[0].reference_role
        == ImageReferenceRole.SUBJECT_IDENTITY
    )
    assert (
        profile.ordered_references[0].reference_authority
        == ImageReferenceAuthority.AUTHORITATIVE
    )
    assert profile.reference_pack_intent is not None
    assert profile.reference_pack_intent.lineage_root is not None
    assert profile.reference_pack_intent.lineage_root.source_work_item_id == face.id
    assert (
        profile.reference_pack_intent.lineage_root.reference_authority
        == ImageReferenceAuthority.AUTHORITATIVE
    )

    assert sheet.output_view == AssetStandardView.CHARACTER_SHEET
    assert sheet.review_purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
    assert sheet.dependency_ids[0] == face.id
    assert sheet.reference_pack_intent is not None
    assert (
        sheet.reference_pack_intent.role
        == ReferenceRole.CHARACTER_REFERENCE_SHEET
    )
    assert sheet.reference_pack_intent.lineage_root is not None
    assert sheet.reference_pack_intent.lineage_root.source_work_item_id == face.id

    carrier = first.asset_plan.items[1]
    assert carrier.asset_kind == AssetKind.PROP
    assert carrier.brief is not None
    assert carrier.brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER
    assert carrier.brief.required_visible_text == []
    assert carrier.brief.layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE
    assert [work.id for work in carrier.work_items] == [
        "work-admission-letter-physical"
    ]
    assert carrier.work_items[0].review_purpose == AssetReviewPurpose.CANONICAL_MASTER

    plate = first.asset_plan.items[3]
    assert plate.asset_kind == AssetKind.REFERENCE
    assert plate.brief is not None
    assert plate.brief.presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE
    assert plate.brief.required_visible_text == ["录取通知书"]
    assert len(plate.work_items) == 1
    assert plate.work_items[0].operation == AssetPlanOperation.TEXT_PLATE
    assert plate.work_items[0].text_plate_intent is not None
    assert plate.work_items[0].text_plate_intent.title == "录取通知书"
    assert plate.work_items[0].text_plate_intent.exact_lines == []
    assert first.asset_plan.items[2].model_dump(mode="json") == untouched_payload


def test_execution_contract_review_exposes_legacy_semantics_without_migrating() -> None:
    legacy = _legacy_plan()

    review = assess_asset_plan_execution_contract(legacy)

    assert review.needs_reconciliation is True
    issues_by_code = {issue.code.value: issue for issue in review.issues}
    assert set(issues_by_code) == {
        "CANONICAL_ROOT_REQUIRES_RECONCILIATION",
    }
    assert issues_by_code[
        "CANONICAL_ROOT_REQUIRES_RECONCILIATION"
    ].item_id == "character-linyi"
    assert set(
        issues_by_code[
            "CANONICAL_ROOT_REQUIRES_RECONCILIATION"
        ].work_item_ids
    ) == {
        "work-linyi-face",
        "work-linyi-profile",
        "work-linyi-sheet",
    }
    reconciled = reconcile_asset_plan_execution_contract(
        legacy,
        _reconcile_request("version-legacy"),
    ).asset_plan
    aligned = assess_asset_plan_execution_contract(reconciled)
    assert aligned.needs_reconciliation is False
    assert aligned.issues == []


def test_execution_contract_review_accepts_pure_screen_content_prop_but_flags_mixed_carrier() -> None:
    legacy = _legacy_plan()
    pure_plate = legacy.items[1]

    pure_review = assess_asset_plan_execution_contract(
        legacy.model_copy(update={"items": [pure_plate]}, deep=True)
    )
    assert pure_review.needs_reconciliation is False
    assert pure_review.issues == []

    physical_work = AssetPlanWorkItem(
        id="work-admission-letter-physical",
        operation=AssetPlanOperation.GENERATE,
        output_role="admission_letter_physical_master",
        output_view=AssetStandardView.PROP_PRODUCT,
        final_prompt="生成无字录取通知书物理母版。",
        size="1024x1024",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )
    mixed = pure_plate.model_copy(
        update={"work_items": [physical_work, *pure_plate.work_items]},
        deep=True,
    )
    mixed_review = assess_asset_plan_execution_contract(
        legacy.model_copy(update={"items": [mixed]}, deep=True)
    )
    assert mixed_review.needs_reconciliation is True
    assert [issue.code.value for issue in mixed_review.issues] == [
        "TEXT_SURFACE_REQUIRES_SEPARATION"
    ]
    assert mixed_review.issues[0].work_item_ids == [
        "work-admission-letter-physical",
        "work-admission-letter-text",
    ]


def test_execution_overrides_update_base_work_and_synchronize_compose_intent() -> None:
    legacy = _legacy_plan()
    character = legacy.items[0]
    carrier = legacy.items[1]
    untouched = legacy.items[2]
    location = AssetPlanItem(
        id="location-canteen",
        disposition=AssetPlanDisposition.ANCHOR,
        asset_kind=AssetKind.LOCATION,
        name="学校食堂场景母图",
        rationale="食堂内多镜头共用场景连续性。",
        work_items=[
            AssetPlanWorkItem(
                id="work-canteen-master",
                operation=AssetPlanOperation.GENERATE,
                output_role="canteen_scene_master",
                output_view=AssetStandardView.SCENE_MASTER,
                output_layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
                final_prompt="生成现代中学食堂全景母图。",
                size="1920x1080",
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
            ),
            AssetPlanWorkItem(
                id="work-canteen-window",
                operation=AssetPlanOperation.GENERATE,
                output_role="canteen_window_reference",
                output_view=AssetStandardView.SCENE_MASTER,
                final_prompt="生成食堂打饭窗口参考图。",
                size="1536x1024",
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
            ),
        ],
    )
    plan = legacy.model_copy(
        update={"items": [character, carrier, untouched, location]},
        deep=True,
    )

    result = reconcile_asset_plan_execution_contract(
        plan,
        AssetPlanExecutionReconcileRequest(
            base_version_id="version-legacy",
            execution_overrides=[
                AssetPlanWorkExecutionOverride(
                    item_id="location-canteen",
                    work_item_id="work-canteen-master",
                    size="1536x864",
                    output_layout_profile=(
                        AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2
                    ),
                ),
                AssetPlanWorkExecutionOverride(
                    item_id="character-linyi",
                    work_item_id="work-linyi-sheet",
                    size="1536x864",
                    output_layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
                ),
            ],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    reconciled_character = result.asset_plan.items[0]
    reconciled_sheet = next(
        work for work in reconciled_character.work_items if work.id == "work-linyi-sheet"
    )
    assert reconciled_sheet.size == "1536x864"
    assert reconciled_sheet.compose_intent is not None
    assert reconciled_sheet.compose_intent.target_size == "1536x864"
    reconciled_location = result.asset_plan.items[3]
    reconciled_location_master = reconciled_location.work_items[0]
    assert (
        reconciled_location_master.output_layout_profile
        == AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2
    )
    assert (
        reconciled_sheet.compose_intent.layout_profile
        == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
    )

    reconciled_location = result.asset_plan.items[3]
    master, sibling = reconciled_location.work_items
    assert master.size == "1536x864"
    assert sibling.size == "1536x1024"
    assert [item.work_item_id for item in result.applied_execution_overrides] == [
        "work-canteen-master",
        "work-linyi-sheet",
    ]
    assert result.applied_execution_overrides[0].size == "1536x864"
    assert result.applied_execution_overrides[1].compose_target_size == "1536x864"


def _provider_must_not_be_created(_api_key: str, _base_url: str):
    raise AssertionError("AssetPlan reconciliation must not create an image Provider")


def _integration_plan(
    service: ShortDramaService,
    project_id: str,
    *,
    spec: object,
    storyboard: object,
) -> AssetPlanContent:
    document = service.get_project(project_id).document
    first_shot = document.shots[0]
    return AssetPlanContent(
        project_id=project_id,
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=spec.document.id,  # type: ignore[attr-defined]
        production_spec_version_id=spec.version.id,  # type: ignore[attr-defined]
        storyboard_document_id=storyboard.document.id,  # type: ignore[attr-defined]
        storyboard_version_id=storyboard.version.id,  # type: ignore[attr-defined]
        items=[
            AssetPlanItem(
                id="prop-score-board",
                disposition=AssetPlanDisposition.ANCHOR,
                asset_kind=AssetKind.PROP,
                name="食堂计价牌",
                serves_shot_ids=[first_shot.id],
                serves_clip_group_ids=[f"clip-{first_shot.id}"],
                brief=CoreAssetBrief(
                    identity_anchor="带有精确价格文字的食堂计价牌。",
                    visual_attributes=["白色硬质标牌", "黑色印刷字"],
                    continuity_anchors=["标牌轮廓与尺寸不变"],
                    presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
                    required_visible_text=["今日菜价"],
                    reference_strategy=AssetReferenceStrategy.LIGHTWEIGHT,
                    layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
                    strategy_rationale="尚未把物理载体与精确文字分层。",
                ),
                rationale="价格牌是主角口算爽点的视觉依据。",
                work_items=[
                    AssetPlanWorkItem(
                        id="work-score-board-text",
                        operation=AssetPlanOperation.TEXT_PLATE,
                        output_role="score_board_with_title",
                        final_prompt="本地排版食堂价格牌标题。",
                        size="1024x1024",
                        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        text_plate_intent=AssetPlanTextPlateIntent(
                            background_color="#FFFFFF",
                            foreground_color="#111111",
                            title="今日菜价",
                            exact_lines=[],
                            layout_description="标题居中。",
                        ),
                    )
                ],
            ),
            AssetPlanItem(
                id="reference-crowd-untouched",
                disposition=AssetPlanDisposition.TEXT_ONLY,
                asset_kind=AssetKind.REFERENCE,
                name="同学群像氛围",
                rationale="仅需留在镜头描述中。",
            ),
        ],
    )


def _asset_plan_version_count(service: ShortDramaService, project_id: str) -> int:
    document = service.get_project(project_id).document
    assert document.asset_plan_document_id is not None
    production_document = next(
        item
        for item in document.production_documents
        if item.id == document.asset_plan_document_id
    )
    return len(production_document.versions)


def _tool_split_arguments(project_id: str, base_version_id: str) -> dict[str, object]:
    return {
        "project_id": project_id,
        "request": {
            "base_version_id": base_version_id,
            "text_surfaces": [
                {
                    "carrier_item_id": "prop-score-board",
                    "create_carrier_master": {
                        "work_item_id": "work-score-board-physical",
                        "output_role": "score_board_physical_master",
                        "output_view": "PROP_PRODUCT",
                        "final_prompt": "生成无字食堂硬质价格牌母版。",
                        "size": "1024x1024",
                    },
                    "remove_text_work_item_ids": ["work-score-board-text"],
                    "plates": [
                        {
                            "item_id": "reference-score-board-title",
                            "work_item_id": "work-score-board-text",
                            "name": "今日菜价标题图版",
                            "rationale": "精确标题与物理价格牌分层。",
                            "serves_shot_ids": [],
                            "serves_clip_group_ids": [],
                            "output_role": "score_board_title_plate",
                            "size": "1024x1024",
                            "title": "今日菜价",
                            "exact_lines": [],
                            "layout_description": "标题居中。",
                            "background_color": "#FFFFFF",
                            "foreground_color": "#111111",
                        }
                    ],
                }
            ],
            "origin": "AGENT",
        },
    }


def test_reconcile_tool_is_compact_atomic_and_provider_free(tmp_path: Path) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "reconciler" / "short-drama.sqlite3",
        workspace_root=tmp_path / "reconciler" / "workspace",
        image_provider_factory=_provider_must_not_be_created,
    )
    project_id, spec, storyboard = _setup_story_truth(service)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=_integration_plan(
                service,
                project_id,
                spec=spec,
                storyboard=storyboard,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert _asset_plan_version_count(service, project_id) == 1

    invalid = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "subjects": [
                    {
                        "item_id": "missing-character",
                        "canonical_work_item_id": "missing-work",
                    }
                ],
                "origin": "AGENT",
            },
        },
    )
    assert invalid.status == ToolCallStatus.FAILED
    assert _asset_plan_version_count(service, project_id) == 1

    notes_only = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "notes": ["将某个 work 改为 1536x864"],
                "origin": "AGENT",
            },
        },
    )
    assert notes_only.status == ToolCallStatus.INVALID_ARGUMENTS
    assert _asset_plan_version_count(service, project_id) == 1

    result = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        _tool_split_arguments(project_id, created.version.id),
    )
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert set(result.output) == {
        "project_id",
        "base_version_id",
        "asset_plan_identity",
        "canonicalized_item_ids",
        "canonical_work_item_ids",
        "supplemental_work_item_ids",
        "created_text_plate_item_ids",
        "removed_text_work_item_ids",
        "created_physical_master_work_item_ids",
        "normalized_layout_item_ids",
        "applied_execution_overrides",
    }
    assert "asset_plan" not in result.output
    assert "items" not in result.output
    assert len(json.dumps(result.output, ensure_ascii=False)) < 2_000
    assert result.output["created_text_plate_item_ids"] == [
        "reference-score-board-title"
    ]
    assert result.output["created_physical_master_work_item_ids"] == [
        "work-score-board-physical"
    ]
    assert [
        (ref.artifact_type, ref.version) for ref in result.artifact_refs
    ] == [
        ("asset_plan", 2),
        ("asset_plan_index", 2),
    ]
    assert _asset_plan_version_count(service, project_id) == 2

    current = service.get_asset_plan(project_id).asset_plan
    untouched = next(item for item in current.items if item.id == "reference-crowd-untouched")
    assert untouched == _integration_plan(
        service,
        project_id,
        spec=spec,
        storyboard=storyboard,
    ).items[1]
    carrier = next(item for item in current.items if item.id == "prop-score-board")
    assert carrier.brief is not None
    assert carrier.brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER
    assert [work.id for work in carrier.work_items] == ["work-score-board-physical"]
    plate = next(
        item for item in current.items if item.id == "reference-score-board-title"
    )
    assert plate.asset_kind == AssetKind.REFERENCE
    assert plate.brief is not None
    assert plate.brief.presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE
    assert plate.brief.required_visible_text == ["今日菜价"]
    assert plate.work_items[0].text_plate_intent is not None
    assert plate.work_items[0].text_plate_intent.exact_lines == []

    stale = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        _tool_split_arguments(project_id, created.version.id),
    )
    assert stale.status == ToolCallStatus.FAILED
    assert _asset_plan_version_count(service, project_id) == 2

    malformed = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        {"project_id": project_id, "request": {}},
    )
    assert malformed.status == ToolCallStatus.INVALID_ARGUMENTS
    assert _asset_plan_version_count(service, project_id) == 2
    assert service.list_image_tasks(project_id).tasks == []
    assert service.repository.list_provider_runs(project_id) == []


def test_reconcile_tool_persists_exact_base_work_override_in_all_projections(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "execution-override" / "short-drama.sqlite3",
        workspace_root=tmp_path / "execution-override" / "workspace",
        image_provider_factory=_provider_must_not_be_created,
    )
    project_id, spec, storyboard = _setup_story_truth(service)
    base_plan = _integration_plan(
        service,
        project_id,
        spec=spec,
        storyboard=storyboard,
    )
    location_item = AssetPlanItem(
        id="location-canteen",
        disposition=AssetPlanDisposition.ANCHOR,
        asset_kind=AssetKind.LOCATION,
        name="学校食堂场景母图",
        rationale="食堂内多镜头共用场景连续性。",
        work_items=[
            AssetPlanWorkItem(
                id="work-canteen-master",
                operation=AssetPlanOperation.GENERATE,
                output_role="canteen_scene_master",
                output_view=AssetStandardView.SCENE_MASTER,
                final_prompt="生成现代中学食堂全景母图。",
                size="1920x1080",
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
            ),
        ],
    )
    plan = base_plan.model_copy(
        update={"items": [*base_plan.items, location_item]},
        deep=True,
    )
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    result = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "execution_overrides": [
                    {
                        "item_id": "location-canteen",
                        "work_item_id": "work-canteen-master",
                        "size": "1536x864",
                    }
                ],
                "origin": "AGENT",
            },
        },
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert result.output["applied_execution_overrides"] == [
        {
            "item_id": "location-canteen",
            "work_item_id": "work-canteen-master",
            "size": "1536x864",
            "output_layout_profile": "LIGHTWEIGHT_SINGLE",
            "compose_target_size": None,
            "compose_layout_profile": None,
        }
    ]
    index = service.get_asset_plan_index(project_id)
    indexed_location = next(item for item in index.items if item.id == "location-canteen")
    indexed_master = indexed_location.work_items[0]
    assert indexed_master.size == "1536x864"
    context = service.get_asset_work_context(
        project_id,
        item_ids=[],
        work_item_ids=["work-canteen-master"],
        shot_ids=[],
        clip_group_ids=[],
    )
    assert len(context.asset_plan_items) == 1
    assert [work.id for work in context.asset_plan_items[0].work_items] == [
        "work-canteen-master"
    ]
    assert context.asset_plan_items[0].work_items[0].size == "1536x864"

    version_count = _asset_plan_version_count(service, project_id)
    wrong_owner = service.tool_registry.invoke(
        "artifact.reconcile_asset_plan_execution_contract",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": index.asset_plan_identity.version_id,
                "execution_overrides": [
                    {
                        "item_id": "reference-crowd-untouched",
                        "work_item_id": "work-canteen-master",
                        "size": "2048x1152",
                    }
                ],
                "origin": "AGENT",
            },
        },
    )
    assert wrong_owner.status == ToolCallStatus.FAILED
    assert _asset_plan_version_count(service, project_id) == version_count
