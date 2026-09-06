"""Versioned Agent-authored AssetPlan runtime and Tool coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver,
    project_artifact_refs,
    project_summary,
)
from short_drama.image_models import (
    AssetImportUrlRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import (
    AssetKind,
    AssetLayoutProfile,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    CoreAssetBrief,
    CreateProjectRequest,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationTargetOrigin,
    ProductionDocumentOrigin,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRole,
    ReviseScriptLineRequest,
    SourceMode,
)
from short_drama.production_contracts import (
    ASSET_PLAN_DOCUMENT_KIND,
    ASSET_PLAN_OUTPUT_SLOT_ID,
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    AssetPlanContent,
    AssetPlanComposeCell,
    AssetPlanComposeIntent,
    AssetPlanCreateRequest,
    AssetPlanDisposition,
    AssetPlanImportIntent,
    AssetPlanItem,
    AssetPlanOperation,
    AssetPlanReference,
    AssetPlanReferencePackIntent,
    AssetPlanReplaceRequest,
    AssetPlanWorkItem,
    ClipGroup,
    GenerationPlanCompileRequest,
    ProductionAudioDirection,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionSpecReplaceRequest,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardReplaceRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus


def _service(tmp_path: Path, name: str = "asset-plan") -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / name / "short-drama.sqlite3",
        workspace_root=tmp_path / name / "workspace",
    )


def _production_spec(
    service: ShortDramaService,
    project_id: str,
) -> ProductionSpecContent:
    document = service.get_project(project_id).document
    return ProductionSpecContent(
        project_id=project_id,
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        title="核心资产制片规格",
        production_scope="从完整剧本和分镜制作可复用的角色、场景和道具资产。",
        production_mode="Agent 根据连贯性需求自主决定资产深度。",
        genre_and_setting="雨夜便利店悬疑短剧。",
        language="zh-CN",
        aspect_ratio="9:16",
        target_duration_seconds=document.project.target_duration_seconds,
        duration_intent="保持已确定剧本和分镜时长。",
        duration_contract=DurationContract(
            target_seconds=None,
            target_origin=DurationTargetOrigin.LEGACY_UNKNOWN,
            authority=DurationAuthority.SOURCE_LED,
            source_fidelity_policy=DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS,
            mismatch_resolution=DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
            decision_basis="当前 fixture 未声明目标时长。",
        ),
        visual=ProductionVisualDirection(medium_lock="写实影像"),
        audio=ProductionAudioDirection(dialogue_intent="对白清晰。"),
    )


def _storyboard(
    service: ShortDramaService,
    project_id: str,
    *,
    production_spec_document_id: str,
    production_spec_version_id: str,
) -> StoryboardContent:
    document = service.get_project(project_id).document
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    return StoryboardContent(
        project_id=project_id,
        title="完整分镜",
        script_version_id=script.id,
        bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=production_spec_document_id,
        production_spec_version_id=production_spec_version_id,
        script_line_ids=[item.id for item in script.lines],
        shots=document.shots,
        clip_groups=[
            ClipGroup(
                id=f"clip-{shot.id}",
                title=f"镜头组 {index}",
                shot_ids=[shot.id],
                duration_seconds=int(shot.duration_seconds),
                generation_intent=shot.visual,
            )
            for index, shot in enumerate(document.shots, start=1)
        ],
        computed_duration_seconds=sum(item.duration_seconds for item in document.shots),
    )


def _setup_story_truth(
    service: ShortDramaService,
) -> tuple[str, object, object]:
    project_id = service.initialize_fixture().document.project.id
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=_production_spec(service, project_id),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    storyboard = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=_storyboard(
                service,
                project_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    return project_id, spec, storyboard


def _asset_plan(
    service: ShortDramaService,
    project_id: str,
    *,
    spec: object,
    storyboard: object,
) -> AssetPlanContent:
    document = service.get_project(project_id).document
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    character_version = service.import_url_asset_version(
        project_id,
        character.id,
        AssetImportUrlRequest(
            external_url="https://example.test/linxia.png",
            descriptor="current Linxia image master",
            media_type="IMAGE",
        ),
    ).version
    service.review_asset_version(
        project_id,
        character.id,
        character_version.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="approved character image master",
        ),
    )
    prop_version = service.import_url_asset_version(
        project_id,
        prop.id,
        AssetImportUrlRequest(
            external_url="https://example.test/recorder.png",
            descriptor="current recorder image master",
            media_type="IMAGE",
        ),
    ).version
    service.review_asset_version(
        project_id,
        prop.id,
        prop_version.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="approved prop image master",
        ),
    )
    document = service.get_project(project_id).document
    first_shot = document.shots[0]
    first_group_id = f"clip-{first_shot.id}"
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
                id="asset-plan-character-linxia",
                disposition=AssetPlanDisposition.ANCHOR,
                asset_kind=AssetKind.CHARACTER,
                name="林夏角色锚点",
                bible_entity_id=character.bible_entity_id,
                serves_shot_ids=[first_shot.id],
                serves_clip_group_ids=[first_group_id],
                brief=CoreAssetBrief(
                    identity_anchor="林夏的稳定面部、发型与身形。",
                    visual_attributes=["黑色短发", "五官清晰"],
                    strategy_rationale="多镜头反复出现，需要稳定身份。",
                ),
                rationale="主要人物跨多个镜头复用。",
                work_items=[
                    AssetPlanWorkItem(
                        id="work-linxia-master",
                        operation=AssetPlanOperation.GENERATE,
                        output_role="character_master",
                        final_prompt="生成林夏稳定角色主图。",
                        size="1344x768",
                        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        ordered_references=[
                            AssetPlanReference(
                                role="prop_continuity",
                                purpose="保持红色录音笔外观。",
                                asset_id=prop.id,
                                asset_version_id=prop_version.id,
                            )
                        ],
                        completed_asset_id=character.id,
                        completed_asset_version_id=character_version.id,
                    ),
                    AssetPlanWorkItem(
                        id="work-linxia-derived",
                        operation=AssetPlanOperation.EDIT,
                        output_role="character_coverage",
                        final_prompt="以主图的同一人物补充可复用视角。",
                        size="1344x768",
                        review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                        reference_pack_intent=AssetPlanReferencePackIntent(
                            subject_item_id="asset-plan-character-linxia",
                            role=ReferenceRole.CHARACTER_PROFILE,
                            angle=ReferenceAngle.LEFT_PROFILE,
                            framing=ReferenceFraming.MEDIUM,
                            tags=["identity", "profile"],
                            lineage_root=AssetPlanReference(
                                role="identity_lineage_root",
                                purpose="记录补充视角所属的母版血缘。",
                                source_work_item_id="work-linxia-master",
                            ),
                        ),
                        ordered_references=[
                            AssetPlanReference(
                                role="identity_master",
                                purpose="锁定同一人物身份。",
                                source_work_item_id="work-linxia-master",
                            )
                        ],
                        dependency_ids=["work-linxia-master"],
                    ),
                ],
                completed_asset_id=character.id,
                completed_asset_version_id=character_version.id,
            ),
            AssetPlanItem(
                id="asset-plan-prop-recorder",
                disposition=AssetPlanDisposition.REUSE_EXISTING,
                asset_kind=AssetKind.PROP,
                name="红色旧录音笔",
                bible_entity_id=prop.bible_entity_id,
                serves_shot_ids=[first_shot.id],
                serves_clip_group_ids=[first_group_id],
                rationale="项目中已有精确版本，直接复用。",
                completed_asset_id=prop.id,
                completed_asset_version_id=prop_version.id,
            ),
            AssetPlanItem(
                id="asset-plan-background-crowd",
                disposition=AssetPlanDisposition.TEXT_ONLY,
                asset_kind=AssetKind.REFERENCE,
                name="便利店远景人群",
                rationale="只是一次性环境氛围，留在镜头描述中。",
            ),
        ],
    )


def _slot(service: ShortDramaService, project_id: str, slot_id: str):
    return next(
        item
        for item in service.get_project(project_id).document.output_slots
        if item.id == slot_id
    )


def test_asset_plan_contract_rejects_forward_or_undeclared_work_refs() -> None:
    with pytest.raises(ValidationError, match="dependencies must precede"):
        AssetPlanContent(
            project_id="project",
            script_version_id="script",
            bible_version_id="bible",
            production_spec_document_id="spec",
            production_spec_version_id="spec-version",
            storyboard_document_id="storyboard",
            storyboard_version_id="storyboard-version",
            items=[
                AssetPlanItem(
                    id="item",
                    disposition=AssetPlanDisposition.ANCHOR,
                    asset_kind=AssetKind.CHARACTER,
                    name="character",
                    rationale="reused character",
                    work_items=[
                        AssetPlanWorkItem(
                            id="derived",
                            operation=AssetPlanOperation.EDIT,
                            output_role="derived",
                            final_prompt="derive",
                            size="1344x768",
                            review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                            reference_pack_intent=AssetPlanReferencePackIntent(
                                subject_item_id="item",
                                role=ReferenceRole.CHARACTER_PROFILE,
                                angle=ReferenceAngle.LEFT_PROFILE,
                                framing=ReferenceFraming.MEDIUM,
                                tags=[],
                                lineage_root=AssetPlanReference(
                                    role="identity_lineage_root",
                                    purpose="identity lineage",
                                    source_work_item_id="master",
                                ),
                            ),
                            ordered_references=[
                                AssetPlanReference(
                                    role="master",
                                    purpose="identity",
                                    source_work_item_id="master",
                                )
                            ],
                            dependency_ids=["master"],
                        ),
                        AssetPlanWorkItem(
                            id="master",
                            operation=AssetPlanOperation.GENERATE,
                            output_role="master",
                            final_prompt="generate",
                            size="1344x768",
                            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        ),
                    ],
                )
            ],
        )

    with pytest.raises(ValidationError, match="must match one completed work item"):
        AssetPlanContent(
            project_id="project",
            script_version_id="script",
            bible_version_id="bible",
            production_spec_document_id="spec",
            production_spec_version_id="spec-version",
            storyboard_document_id="storyboard",
            storyboard_version_id="storyboard-version",
            items=[
                AssetPlanItem(
                    id="item",
                    disposition=AssetPlanDisposition.ANCHOR,
                    asset_kind=AssetKind.CHARACTER,
                    name="character",
                    rationale="reused character",
                    completed_asset_id="asset",
                    completed_asset_version_id="version",
                    work_items=[
                        AssetPlanWorkItem(
                            id="master",
                            operation=AssetPlanOperation.GENERATE,
                            output_role="master",
                            final_prompt="generate",
                            size="1344x768",
                            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        )
                    ],
                )
            ],
        )

    with pytest.raises(ValidationError, match="text-only.*cannot contain work items"):
        AssetPlanItem(
            id="text-only",
            disposition=AssetPlanDisposition.TEXT_ONLY,
            asset_kind=AssetKind.REFERENCE,
            name="crowd",
            rationale="one-off atmosphere",
            work_items=[
                AssetPlanWorkItem(
                    id="contradictory-generation",
                    operation=AssetPlanOperation.GENERATE,
                    output_role="crowd_image",
                    final_prompt="generate a crowd",
                    size="1344x768",
                    review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                )
            ],
        )

    with pytest.raises(ValidationError, match="reuse-existing.*requires an exact"):
        AssetPlanItem(
            id="reuse-without-version",
            disposition=AssetPlanDisposition.REUSE_EXISTING,
            asset_kind=AssetKind.PROP,
            name="existing prop",
            rationale="Reuse should point to an exact usable version.",
        )

    with pytest.raises(ValidationError, match="supplemental.*cannot declare.*canonical"):
        AssetPlanWorkItem(
            id="conflicting-purpose",
            operation=AssetPlanOperation.EDIT,
            output_role="profile",
            final_prompt="derive a profile",
            size="1344x768",
            review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
            reference_pack_intent=AssetPlanReferencePackIntent(
                subject_item_id="character",
                role=ReferenceRole.CANONICAL_MASTER,
                angle=ReferenceAngle.LEFT_PROFILE,
                framing=ReferenceFraming.MEDIUM,
                tags=["profile"],
            ),
        )

    with pytest.raises(ValidationError, match="incompatible with subject item kind"):
        AssetPlanContent(
            project_id="project",
            script_version_id="script",
            bible_version_id="bible",
            production_spec_document_id="spec",
            production_spec_version_id="spec-version",
            storyboard_document_id="storyboard",
            storyboard_version_id="storyboard-version",
            items=[
                AssetPlanItem(
                    id="location",
                    disposition=AssetPlanDisposition.ANCHOR,
                    asset_kind=AssetKind.LOCATION,
                    name="canteen",
                    rationale="recurring location",
                    work_items=[
                        AssetPlanWorkItem(
                            id="wrong-role",
                            operation=AssetPlanOperation.GENERATE,
                            output_role="location_profile",
                            final_prompt="generate the canteen",
                            size="1344x768",
                            review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                            reference_pack_intent=AssetPlanReferencePackIntent(
                                subject_item_id="location",
                                role=ReferenceRole.CHARACTER_PROFILE,
                                angle=ReferenceAngle.LEFT_PROFILE,
                                framing=ReferenceFraming.WIDE,
                                tags=["location"],
                            ),
                        )
                    ],
                )
            ],
        )


def test_asset_plan_operations_require_complete_executable_payloads() -> None:
    with pytest.raises(ValidationError, match="IMPORT.*requires only import_intent"):
        AssetPlanWorkItem(
            id="import-without-source",
            operation=AssetPlanOperation.IMPORT,
            output_role="character_master",
            final_prompt="Import the selected exact catalog image.",
            size="1344x768",
            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        )

    imported = AssetPlanWorkItem(
        id="import-character-master",
        operation=AssetPlanOperation.IMPORT,
        output_role="character_master",
        final_prompt="Import the selected exact catalog image.",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        import_intent=AssetPlanImportIntent(
            source_project_id="source-project",
            source_asset_id="source-character",
            source_asset_version_id="source-character-v3",
        ),
    )
    assert imported.import_intent is not None
    assert imported.import_intent.source_project_id == "source-project"

    with pytest.raises(ValidationError, match="COMPOSE.*requires only compose_intent"):
        AssetPlanWorkItem(
            id="compose-without-layout",
            operation=AssetPlanOperation.COMPOSE,
            output_role="character_sheet",
            final_prompt="Compose the selected reference cells.",
            size="1344x768",
            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        )

    source = AssetPlanWorkItem(
        id="front-view",
        operation=AssetPlanOperation.GENERATE,
        output_role="character_front",
        output_view=AssetStandardView.CHARACTER_FRONT,
        final_prompt="Generate the character front view.",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
    )
    composed = AssetPlanWorkItem(
        id="character-sheet",
        operation=AssetPlanOperation.COMPOSE,
        output_role="character_sheet",
        final_prompt="Compose the selected reference cells.",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        compose_intent=AssetPlanComposeIntent(
            strategy=AssetReferenceStrategy.LIGHTWEIGHT,
            cells=[
                AssetPlanComposeCell(
                    source=AssetPlanReference(
                        role="front_view",
                        purpose="Use the generated front view as the sheet cell.",
                        source_work_item_id=source.id,
                    ),
                    view=AssetStandardView.CHARACTER_FRONT,
                    label="Front",
                )
            ],
            input_policy=AssetSheetInputPolicy.EXPLICIT_VERSIONS,
            columns=1,
            layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
            show_labels=True,
            background_color="#FFFFFF",
            target_size="1344x768",
        ),
        dependency_ids=[source.id],
    )
    plan = AssetPlanContent(
        project_id="project",
        script_version_id="script",
        bible_version_id="bible",
        production_spec_document_id="spec",
        production_spec_version_id="spec-version",
        storyboard_document_id="storyboard",
        storyboard_version_id="storyboard-version",
        items=[
            AssetPlanItem(
                id="character",
                disposition=AssetPlanDisposition.ANCHOR,
                asset_kind=AssetKind.CHARACTER,
                name="character",
                rationale="A recurring character needs a reusable sheet.",
                work_items=[source, composed],
            )
        ],
    )
    assert plan.items[0].work_items[1].compose_intent is not None


def test_asset_plan_tool_versions_sources_without_invalidating_generation_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id, spec, storyboard = _setup_story_truth(service)
    generation = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    plan = _asset_plan(
        service,
        project_id,
        spec=spec,
        storyboard=storyboard,
    )

    created_call = service.tool_registry.invoke(
        "artifact.create_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": plan.model_dump(mode="json"),
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert created_call.status == ToolCallStatus.SUCCEEDED
    assert created_call.output is not None
    assert created_call.output["document"]["kind"] == ASSET_PLAN_DOCUMENT_KIND
    assert created_call.artifact_refs[0].artifact_type == "asset_plan"
    created = service.get_asset_plan(project_id)
    assert created.asset_plan == plan
    assert [item.artifact_type for item in created.version.source_artifact_refs] == [
        "script_version",
        "bible_version",
        "production_spec",
        "storyboard",
    ]
    assert created.output_slot is not None
    assert created.output_slot.id == ASSET_PLAN_OUTPUT_SLOT_ID
    assert _slot(
        service,
        project_id,
        GENERATION_PLAN_OUTPUT_SLOT_ID,
    ).current_artifact_ref is not None

    service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            base_version_id=generation.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    revised_plan = plan.model_copy(update={"notes": ["保留 Agent 的资产取舍理由。"]})
    replaced_call = service.tool_registry.invoke(
        "artifact.replace_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": revised_plan.model_dump(mode="json"),
                "base_version_id": created.version.id,
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert replaced_call.status == ToolCallStatus.SUCCEEDED
    replaced = service.get_asset_plan(project_id)
    assert replaced.version.version == 2
    assert replaced.asset_plan.notes == ["保留 Agent 的资产取舍理由。"]
    assert _slot(
        service,
        project_id,
        GENERATION_PLAN_OUTPUT_SLOT_ID,
    ).current_artifact_ref is not None

    document = service.get_project(project_id).document
    refs = project_artifact_refs(document)
    typed_refs = [item.artifact_type for item in refs]
    assert "asset_plan" in typed_refs
    assert typed_refs.index("asset_plan") < typed_refs.index("output_slot")
    resolver = ProjectArtifactResolver(service.repository)
    summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan",
            artifact_id=replaced.document.id,
            version=replaced.version.version,
        )
    )
    assert summary is not None
    assert summary.title == "AssetPlan v2"
    assert "asset-plan-character-linxia" in summary.summary
    assert "asset_plan=" in project_summary(document)


def test_asset_plan_cross_project_import_is_exact_and_persists_source_identity(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "cross-project-import")
    source = service.create_project(
        CreateProjectRequest(
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="一名高中生在食堂展示数学天赋。",
            target_duration_seconds=60.0,
        ),
        project_id="source-catalog",
    )
    source_asset = next(
        item for item in source.document.assets if item.kind == AssetKind.CHARACTER
    )
    source_version = service.import_url_asset_version(
        source.document.project.id,
        source_asset.id,
        AssetImportUrlRequest(
            external_url="https://example.test/catalog-character.png",
            descriptor="approved catalog character image",
            media_type="IMAGE",
        ),
    ).version
    service.review_asset_version(
        source.document.project.id,
        source_asset.id,
        source_version.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="approved catalog master",
        ),
    )

    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character = plan.items[0]
    imported_master = character.work_items[0].model_copy(
        update={
            "operation": AssetPlanOperation.IMPORT,
            "import_intent": AssetPlanImportIntent(
                source_project_id=source.document.project.id,
                source_asset_id=source_asset.id,
                source_asset_version_id=source_version.id,
            ),
        }
    )
    imported_character = character.model_copy(
        update={
            "work_items": [imported_master, *character.work_items[1:]],
        }
    )
    imported_plan = plan.model_copy(
        update={"items": [imported_character, *plan.items[1:]]}
    )

    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=imported_plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    persisted_intent = created.asset_plan.items[0].work_items[0].import_intent
    assert persisted_intent is not None
    assert persisted_intent.source_project_id == source.document.project.id
    assert persisted_intent.source_asset_id == source_asset.id
    assert persisted_intent.source_asset_version_id == source_version.id


def test_asset_plan_service_rejects_bypassed_disposition_and_pack_subject_conflicts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "semantic-consistency")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    document = service.get_project(project_id).document
    text_only_with_work = plan.items[2].model_copy(
        update={
            "work_items": [
                plan.items[0].work_items[0].model_copy(
                    update={"id": "text-only-work"}
                )
            ]
        }
    )
    with pytest.raises(WorkflowConflictError, match="Text-only.*cannot contain media work"):
        service._validate_asset_plan(
            document,
            plan.model_copy(
                update={"items": [*plan.items[:2], text_only_with_work]}
            ),
        )

    derived = plan.items[0].work_items[1]
    assert derived.reference_pack_intent is not None
    incompatible_intent = derived.reference_pack_intent.model_copy(
        update={"subject_item_id": plan.items[1].id}
    )
    incompatible_work = derived.model_copy(
        update={"reference_pack_intent": incompatible_intent}
    )
    incompatible_character = plan.items[0].model_copy(
        update={"work_items": [plan.items[0].work_items[0], incompatible_work]}
    )
    with pytest.raises(WorkflowConflictError, match="incompatible with subject item"):
        service._validate_asset_plan(
            document,
            plan.model_copy(
                update={"items": [incompatible_character, *plan.items[1:]]}
            ),
        )


def test_asset_plan_rejects_stale_sources_and_unknown_completed_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    replaced_storyboard = service.replace_storyboard(
        project_id,
        StoryboardReplaceRequest(
            storyboard=storyboard.storyboard,
            base_version_id=storyboard.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    with pytest.raises(WorkflowConflictError, match="current Storyboard version"):
        service.create_asset_plan(
            project_id,
            AssetPlanCreateRequest(
                asset_plan=plan,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    current_plan = _asset_plan(
        service,
        project_id,
        spec=spec,
        storyboard=replaced_storyboard,
    )
    first_work_item = current_plan.items[0].work_items[0].model_copy(
        update={"completed_asset_version_id": "missing-version"}
    )
    first_item = current_plan.items[0].model_copy(
        update={
            "completed_asset_version_id": "missing-version",
            "work_items": [
                first_work_item,
                *current_plan.items[0].work_items[1:],
            ],
        }
    )
    with pytest.raises(WorkflowConflictError, match="unknown AssetVersion"):
        service.create_asset_plan(
            project_id,
            AssetPlanCreateRequest(
                asset_plan=current_plan.model_copy(
                    update={"items": [first_item, *current_plan.items[1:]]}
                ),
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )


def test_asset_plan_completed_visual_output_requires_usable_media(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "completed-visual")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character_item = plan.items[0]
    assert character_item.completed_asset_id is not None
    character = next(
        item
        for item in service.get_project(project_id).document.assets
        if item.id == character_item.completed_asset_id
    )
    unusable_version = next(
        item for item in character.versions if item.local_path is None and item.external_url is None
    )
    completed_work = character_item.work_items[0].model_copy(
        update={"completed_asset_version_id": unusable_version.id}
    )
    invalid_item = character_item.model_copy(
        update={
            "completed_asset_version_id": unusable_version.id,
            "work_items": [completed_work, *character_item.work_items[1:]],
        }
    )

    with pytest.raises(WorkflowConflictError, match="has no usable media"):
        service.create_asset_plan(
            project_id,
            AssetPlanCreateRequest(
                asset_plan=plan.model_copy(
                    update={"items": [invalid_item, *plan.items[1:]]}
                ),
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )


@pytest.mark.parametrize("review_action", ["reject", "replace"])
def test_asset_review_invalidates_only_asset_plan_when_completion_becomes_unusable(
    tmp_path: Path,
    review_action: str,
) -> None:
    service = _service(tmp_path, review_action)
    project_id, spec, storyboard = _setup_story_truth(service)
    generation = service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    completed_item = plan.items[0]
    assert completed_item.completed_asset_id is not None
    assert completed_item.completed_asset_version_id is not None

    if review_action == "reject":
        service.review_asset_version(
            project_id,
            completed_item.completed_asset_id,
            completed_item.completed_asset_version_id,
            AssetReviewRequest(
                decision="REJECTED",
                purpose=AssetReviewPurpose.CANONICAL_MASTER,
                notes="the completed master is no longer usable",
            ),
        )
    else:
        replacement = service.import_url_asset_version(
            project_id,
            completed_item.completed_asset_id,
            AssetImportUrlRequest(
                external_url="https://example.test/linxia-replacement.png",
                descriptor="replacement canonical image",
                media_type="IMAGE",
            ),
        )
        service.review_asset_version(
            project_id,
            completed_item.completed_asset_id,
            replacement.version.id,
            AssetReviewRequest(
                decision="APPROVED",
                purpose=AssetReviewPurpose.CANONICAL_MASTER,
                notes="replace the previous canonical master",
            ),
        )

    asset_slot = _slot(service, project_id, ASSET_PLAN_OUTPUT_SLOT_ID)
    assert asset_slot.current_artifact_ref is None
    assert asset_slot.previous_artifact_refs
    generation_slot = _slot(service, project_id, GENERATION_PLAN_OUTPUT_SLOT_ID)
    assert generation_slot.current_artifact_ref is not None
    assert generation_slot.current_artifact_ref.artifact_id == generation.document.id
    historical = service.get_asset_plan(project_id, version_id=created.version.id)
    assert historical.asset_plan == plan


@pytest.mark.parametrize("upstream", ["storyboard", "production_spec", "script"])
def test_asset_plan_slot_becomes_stale_when_upstream_truth_changes(
    tmp_path: Path,
    upstream: str,
) -> None:
    service = _service(tmp_path, upstream)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    if upstream == "storyboard":
        service.replace_storyboard(
            project_id,
            StoryboardReplaceRequest(
                storyboard=storyboard.storyboard,
                base_version_id=storyboard.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    elif upstream == "production_spec":
        service.replace_production_spec(
            project_id,
            ProductionSpecReplaceRequest(
                spec=spec.spec,
                base_version_id=spec.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    else:
        current_script = next(
            item
            for item in service.get_project(project_id).document.script_versions
            if item.id
            == service.get_project(project_id).document.project.current_script_version_id
        )
        first_line = current_script.lines[0]
        service.revise_script_line(
            project_id,
            ReviseScriptLineRequest(
                line_id=first_line.id,
                spoken_text=first_line.spoken_text + "！",
                subtitle_text=first_line.subtitle_text + "！",
                change_reason="验证上游变更会使资产计划失效。",
            ),
        )

    stale_slot = _slot(service, project_id, ASSET_PLAN_OUTPUT_SLOT_ID)
    assert stale_slot.current_artifact_ref is None
    assert stale_slot.invalidation_reason is not None
