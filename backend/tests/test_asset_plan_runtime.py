"""Versioned Agent-authored AssetPlan runtime and Tool coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.agent_runtime import (
    ArtifactRef,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    ToolAttemptRef,
)
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver,
    project_artifact_refs,
    project_summary,
)
from short_drama.image_models import (
    AssetCreateRequest,
    AssetBriefUpdateRequest,
    AssetImportUrlRequest,
    AssetReviewPurpose,
    AssetReviewDecision,
    AssetReviewRequest,
    ImageProviderConfigUpdate,
    ImageTaskCreateRequest,
)
from short_drama.models import (
    AssetKind,
    AssetLayoutProfile,
    AssetPresentationMode,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetStandardView,
    CoreAssetBrief,
    CreateProjectRequest,
    CreativeInputImage,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationTargetOrigin,
    ImageReferenceAuthority,
    ImageReferenceRole,
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
    AssetPlanFieldPatch,
    AssetPlanFieldPatchOperation,
    AssetPlanPatchRequest,
    AssetPlanReference,
    AssetPlanReferencePackIntent,
    AssetPlanReplaceRequest,
    AssetPlanTextPlateIntent,
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
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
)
from short_drama.production_manager import (
    _apply_asset_plan_attempt_patches,
    _specialist_working_set,
)
from short_drama.reference_planner import (
    ReferencePackEntryCreateRequest,
    ShotReferencePlanUpdateRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from short_drama.tools.artifact import AssetPlanAttemptPatch


def _service(tmp_path: Path, name: str = "asset-plan") -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / name / "short-drama.sqlite3",
        workspace_root=tmp_path / name / "workspace",
    )


def test_asset_plan_tool_schema_exposes_canonical_reference_role_contract(
    tmp_path: Path,
) -> None:
    create_descriptor = _service(tmp_path).tool_registry.describe(
        "artifact.create_asset_plan"
    )
    schema_text = str(create_descriptor.input_schema)

    assert "若同时提供 reference_pack_intent" in schema_text
    assert "可以不提供，但若提供则 role 必须为 CANONICAL_MASTER" in schema_text
    assert "LOCATION_MASTER、PROP_PRODUCT" in schema_text
    assert "真正使用该资产的 Storyboard Shot ID" in schema_text
    assert "至少包含每个 serves_shot_ids 所属的生成组" in schema_text
    assert create_descriptor.version == 2
    assert "不回传完整 AssetPlan" in create_descriptor.description
    assert "asset_plan" not in create_descriptor.output_schema["properties"]
    assert "asset_plan_identity" in create_descriptor.output_schema["properties"]

    replace_descriptor = _service(tmp_path, "replace-schema").tool_registry.describe(
        "artifact.replace_asset_plan"
    )
    replace_schema_text = str(replace_descriptor.input_schema)
    assert "完整替换快照中有意移除" in replace_schema_text
    assert "从仍保留的 item 里有意移除" in replace_schema_text
    assert "完整快照替换，不是单条 item 的 patch" in (
        replace_descriptor.description
    )
    assert replace_descriptor.version == 2
    assert "不回传完整" in replace_descriptor.description
    assert "asset_plan" not in replace_descriptor.output_schema["properties"]
    assert "asset_plan_identity" in replace_descriptor.output_schema["properties"]
    patch_descriptor = _service(tmp_path, "patch-schema").tool_registry.describe(
        "artifact.patch_asset_plan"
    )
    assert patch_descriptor.version == 2
    assert "按稳定 item/work-item ID" in patch_descriptor.description
    assert "field_patches" in patch_descriptor.description
    assert "合并成完整计划后执行" in patch_descriptor.description
    assert "field_patches" in patch_descriptor.input_schema["$defs"][
        "AssetPlanPatchRequest"
    ]["properties"]
    assert "不回传完整 AssetPlan" in patch_descriptor.description
    assert "asset_plan" not in patch_descriptor.output_schema["properties"]
    assert "asset_plan_identity" in patch_descriptor.output_schema["properties"]
    field_patch_descriptor = _service(
        tmp_path,
        "field-patch-schema",
    ).tool_registry.describe("artifact.patch_asset_plan_fields")
    assert field_patch_descriptor.version == 1
    assert "add /items/-" in field_patch_descriptor.description
    assert "remove 和空 path" in field_patch_descriptor.description
    assert set(field_patch_descriptor.input_schema["properties"]) == {
        "project_id",
        "base_version_id",
        "patches",
        "notes",
        "source_artifact_refs",
        "origin",
    }
    assert "AssetPlanItem" not in json.dumps(
        field_patch_descriptor.input_schema,
        ensure_ascii=False,
    )
    slice_descriptor = _service(tmp_path, "slice-schema").tool_registry.describe(
        "artifact.get_asset_work_context"
    )
    assert slice_descriptor.version == 2
    assert "所选工作项及依赖" in slice_descriptor.description
    assert "避免 project.get" in slice_descriptor.description
    assert "顶层旧式 selector 或混合 scope" in slice_descriptor.description
    assert set(slice_descriptor.input_schema["properties"]) == {
        "project_id",
        "scope",
        "shot_ids",
        "clip_group_ids",
        "version_id",
    }
    index_descriptor = _service(tmp_path, "index-schema").tool_registry.describe(
        "artifact.get_asset_plan_index"
    )
    assert "轻量读取 AssetPlan" in index_descriptor.description
    assert "不返回 brief、final prompt、有序参考" in index_descriptor.description
    assert "Worker 再调用 artifact.get_asset_work_context" in (
        index_descriptor.description
    )
    planning_descriptor = _service(
        tmp_path,
        "planning-context-schema",
    ).tool_registry.describe("artifact.get_asset_planning_context")
    assert "当前 ScriptVersion" in planning_descriptor.description
    assert "source intake 的当前/已批准精确版本" in (
        planning_descriptor.description
    )
    assert "不包含 ProjectDocument" in planning_descriptor.description

    retry_descriptor = _service(
        tmp_path,
        "asset-plan-attempt-retry-schema",
    ).tool_registry.describe("artifact.retry_asset_plan_attempt")
    assert retry_descriptor.version == 2
    assert retry_descriptor.effect.value == "WRITE"
    assert "ToolAttemptRef" in retry_descriptor.description
    assert "RFC 6902" in retry_descriptor.description
    assert "不回传完整正文" in retry_descriptor.description
    assert retry_descriptor.input_schema["required"] == [
        "project_id",
        "attempt_ref",
        "patches",
    ]
    retry_properties = retry_descriptor.input_schema["properties"]
    assert set(retry_properties) == {"project_id", "attempt_ref", "patches"}
    patch_ref = retry_properties["patches"]["items"]["$ref"]
    patch_schema = retry_descriptor.input_schema["$defs"][
        patch_ref.rsplit("/", maxsplit=1)[-1]
    ]
    assert set(patch_schema["properties"]) == {
        "item_id",
        "work_item_id",
        "op",
        "path",
        "value",
    }
    assert "asset_plan" not in retry_descriptor.output_schema["properties"]
    assert "asset_plan_identity" in retry_descriptor.output_schema["properties"]


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
    approve_sources: bool = True,
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
    if approve_sources:
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
    if approve_sources:
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
                        operation=AssetPlanOperation.REFERENCE_GENERATE,
                        output_role="character_master",
                        final_prompt="生成林夏稳定角色主图。",
                        size="1344x768",
                        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                        ordered_references=[
                            AssetPlanReference(
                                role="prop_continuity",
                                purpose="保持红色录音笔外观。",
                                reference_role=ImageReferenceRole.PROP_IDENTITY,
                                reference_authority=(
                                    ImageReferenceAuthority.AUTHORITATIVE
                                ),
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


def _persist_failed_asset_plan_attempt(
    service: ShortDramaService,
    *,
    project_id: str,
    arguments: dict[str, object],
    turn_id: str,
) -> tuple[str, str, ToolAttemptRef]:
    """Persist one real failed create call and hand its compact ref to a repair Worker."""

    runtime = service.agent_runtime
    run = runtime.create_run(
        project_id=project_id,
        objective="Repair the exact failed AssetPlan candidate by stable IDs.",
        root_agent="production-manager",
    )
    source = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-planner",
        production_skill_role=ProductionSkillRole.ASSET_PLANNER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Create the complete AssetPlan candidate.",
        why_now="The storyboard and production specification are ready.",
    )
    failed = service.tool_registry.invoke(
        "artifact.create_asset_plan",
        arguments,
    )
    assert failed.status == ToolCallStatus.INVALID_ARGUMENTS
    source.specialist_turns.append(
        SpecialistTurnRecord(
            id=turn_id,
            sequence=1,
            tool_name="artifact.create_asset_plan",
            arguments=arguments,
            reason="Persist the complete candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result=failed.model_dump(mode="json"),
        )
    )
    runtime.store.save_work_order(source)
    ref = ToolAttemptRef(
        project_id=project_id,
        agent_run_id=run.id,
        work_order_id=source.id,
        attempt_id=turn_id,
        tool_name="artifact.create_asset_plan",
        turn_status=SpecialistTurnStatus.COMPLETED,
        outcome_status=failed.status.value,
        has_arguments=True,
        has_result=True,
    )
    repair = runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-plan-repairer",
        production_skill_role=ProductionSkillRole.ASSET_PLANNER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Patch only the rejected fields and retry the exact candidate.",
        why_now="The exact failed candidate is available by ToolAttemptRef.",
        tool_attempt_refs=[ref],
    )
    return run.id, repair.id, ref


def test_retry_asset_plan_attempt_patches_failed_create_without_full_retransmission(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "retry-failed-create")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    candidate_marker = "exact-large-candidate-" + ("x" * 20_000)
    plan_payload = plan.model_dump(mode="json")
    plan_payload["notes"] = [candidate_marker]
    work_item = plan_payload["items"][0]["work_items"][0]
    work_item["final_prompt"] = ""
    source_arguments: dict[str, object] = {
        "project_id": project_id,
        "request": {
            "asset_plan": plan_payload,
            "origin": ProductionDocumentOrigin.AGENT.value,
        },
    }
    run_id, repair_id, source_ref = _persist_failed_asset_plan_attempt(
        service,
        project_id=project_id,
        arguments=source_arguments,
        turn_id="specialist-turn-invalid-prompt",
    )
    malformed_path = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        {
            "project_id": project_id,
            "attempt_ref": source_ref.model_dump(mode="json"),
            "patches": [
                {
                    "work_item_id": "work-linxia-master",
                    "op": "replace",
                    "path": "final_prompt",
                    "value": "invalid pointer",
                }
            ],
        },
        agent_run_id=run_id,
        work_order_id=repair_id,
    )
    assert malformed_path.status == ToolCallStatus.INVALID_ARGUMENTS
    assert malformed_path.error is not None
    assert "RFC 6901 pointer" in malformed_path.error.message

    missing_value = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        {
            "project_id": project_id,
            "attempt_ref": source_ref.model_dump(mode="json"),
            "patches": [
                {
                    "work_item_id": "work-linxia-master",
                    "op": "replace",
                    "path": "/final_prompt",
                }
            ],
        },
        agent_run_id=run_id,
        work_order_id=repair_id,
    )
    assert missing_value.status == ToolCallStatus.INVALID_ARGUMENTS
    assert missing_value.error is not None
    assert "requires value" in missing_value.error.message

    retry_arguments = {
        "project_id": project_id,
        "attempt_ref": source_ref.model_dump(mode="json"),
        "patches": [
            {
                "work_item_id": "work-linxia-master",
                "op": "add",
                "path": "/final_prompt",
                "value": "修正后的林夏稳定角色主图提示词。",
            }
        ],
    }

    assert candidate_marker not in json.dumps(retry_arguments, ensure_ascii=False)
    retried = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        retry_arguments,
        agent_run_id=run_id,
        work_order_id=repair_id,
    )

    assert retried.status == ToolCallStatus.SUCCEEDED
    assert retried.output is not None
    assert retried.output["operation"] == "CREATE"
    assert "asset_plan" not in retried.output
    persisted = service.get_asset_plan(project_id).asset_plan
    assert persisted.notes == [candidate_marker]
    assert (
        persisted.items[0].work_items[0].final_prompt
        == "修正后的林夏稳定角色主图提示词。"
    )


def test_asset_plan_attempt_patch_removes_stable_work_item_without_list_index(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "remove-stable-work-item")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    arguments = {
        "project_id": project_id,
        "request": {
            "asset_plan": plan.model_dump(mode="json"),
            "origin": ProductionDocumentOrigin.AGENT.value,
        },
    }
    removed_id = plan.items[0].work_items[1].id

    patched = _apply_asset_plan_attempt_patches(
        arguments,
        [
            AssetPlanAttemptPatch(
                item_id=plan.items[0].id,
                work_item_id=removed_id,
                op="remove",
                path="",
            )
        ],
    )

    remaining_ids = [
        item["id"]
        for item in patched["request"]["asset_plan"]["items"][0]["work_items"]
    ]
    assert removed_id not in remaining_ids
    assert len(remaining_ids) == len(plan.items[0].work_items) - 1


def test_retry_asset_plan_attempt_reconstructs_chained_failed_retry_lineage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "retry-chain")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    plan_payload = plan.model_dump(mode="json")
    work_item = plan_payload["items"][0]["work_items"][0]
    work_item["final_prompt"] = ""
    work_item["size"] = ""
    full_candidate_marker = "CHAINED_RETRY_FULL_PLAN_MUST_STAY_SERVER_SIDE_" + (
        "x" * 8_000
    )
    plan_payload["notes"] = [full_candidate_marker]
    first_item = plan.items[0]
    plan_payload["items"].append(
        AssetPlanItem(
            id="asset-plan-text-plate-chain",
            disposition=AssetPlanDisposition.ANCHOR,
            asset_kind=AssetKind.REFERENCE,
            name="链式重试上屏文字",
            serves_shot_ids=list(first_item.serves_shot_ids),
            serves_clip_group_ids=list(first_item.serves_clip_group_ids),
            brief=CoreAssetBrief(
                identity_anchor="一张独立上屏文字板。",
                visual_attributes=["清晰排版"],
                presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
                required_visible_text=["错误旧文案"],
            ),
            rationale="精确文字由本地确定性排版生成。",
            work_items=[
                AssetPlanWorkItem(
                    id="work-text-plate-chain",
                    operation=AssetPlanOperation.TEXT_PLATE,
                    output_role="exact_screen_text",
                    final_prompt="按照已批准文案确定性排版。",
                    size="1344x768",
                    review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                    text_plate_intent=AssetPlanTextPlateIntent(
                        background_color="#FFFFFF",
                        foreground_color="#111111",
                        exact_lines=["正确新文案"],
                        layout_description="文字居中。",
                    ),
                )
            ],
        ).model_dump(mode="json")
    )
    raw_text_plate_brief = plan_payload["items"][-1]["brief"]
    raw_text_plate_brief.pop("presentation_mode")
    raw_text_plate_brief.pop("required_visible_text")
    source_arguments: dict[str, object] = {
        "project_id": project_id,
        "request": {
            "asset_plan": plan_payload,
            "origin": ProductionDocumentOrigin.AGENT.value,
        },
    }
    run_id, repair_id, source_ref = _persist_failed_asset_plan_attempt(
        service,
        project_id=project_id,
        arguments=source_arguments,
        turn_id="specialist-turn-two-invalid-fields",
    )
    first_retry_arguments = {
        "project_id": project_id,
        "attempt_ref": source_ref.model_dump(mode="json"),
        "patches": [
            {
                "item_id": "asset-plan-character-linxia",
                "work_item_id": "work-linxia-master",
                "op": "add",
                "path": "/final_prompt",
                "value": "第一次重试已修正提示词。",
            }
        ],
    }
    first_retry = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        first_retry_arguments,
        agent_run_id=run_id,
        work_order_id=repair_id,
    )
    assert first_retry.status == ToolCallStatus.FAILED
    assert first_retry.error is not None
    assert full_candidate_marker not in first_retry.error.message
    first_feedback = json.loads(first_retry.error.message)
    assert first_feedback["kind"] == "ASSET_PLAN_INPUT_VALIDATION"
    first_issue = first_feedback["issues"][0]
    assert first_issue["item_id"] == "asset-plan-character-linxia"
    assert first_issue["work_item_id"] == "work-linxia-master"
    assert first_issue["loc"][-1] == "size"
    assert first_issue["offending_node"]["work_item"]["id"] == (
        "work-linxia-master"
    )
    assert "final_prompt" not in first_issue["offending_node"]["work_item"]

    repair = service.agent_runtime.store.get_work_order(repair_id)
    assert repair is not None
    retry_turn_id = "specialist-turn-failed-retry"
    repair.specialist_turns.append(
        SpecialistTurnRecord(
            id=retry_turn_id,
            sequence=1,
            tool_name="artifact.retry_asset_plan_attempt",
            arguments=first_retry_arguments,
            reason="Repair the first invalid field.",
            status=SpecialistTurnStatus.COMPLETED,
            result=first_retry.model_dump(mode="json"),
        )
    )
    service.agent_runtime.store.save_work_order(repair)
    next_worker_set = _specialist_working_set(
        repair,
        [],
        project_id=project_id,
        write_tool_ids=frozenset({"artifact.retry_asset_plan_attempt"}),
    ).model_dump(mode="json")
    next_worker_json = json.dumps(next_worker_set, ensure_ascii=False)
    assert full_candidate_marker not in next_worker_json
    next_error = next_worker_set["latest_handoff"]["result"]["error"]  # type: ignore[index]
    assert isinstance(next_error, dict)
    next_feedback = json.loads(str(next_error["message"]))
    assert next_feedback["issues"][0]["item_id"] == (
        "asset-plan-character-linxia"
    )
    assert next_feedback["issues"][0]["work_item_id"] == (
        "work-linxia-master"
    )
    assert next_worker_set["repair_tool_schema_ids"] == [
        "artifact.retry_asset_plan_attempt"
    ]
    retry_ref = ToolAttemptRef(
        project_id=project_id,
        agent_run_id=run_id,
        work_order_id=repair_id,
        attempt_id=retry_turn_id,
        tool_name="artifact.retry_asset_plan_attempt",
        turn_status=SpecialistTurnStatus.COMPLETED,
        outcome_status=first_retry.status.value,
        has_arguments=True,
        has_result=True,
    )

    second_retry_arguments = {
        "project_id": project_id,
        "attempt_ref": retry_ref.model_dump(mode="json"),
        "patches": [
            {
                "item_id": "asset-plan-character-linxia",
                "work_item_id": "work-linxia-master",
                "op": "add",
                "path": "/size",
                "value": "1344x768",
            }
        ],
    }
    second_retry = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        second_retry_arguments,
        agent_run_id=run_id,
        work_order_id=repair_id,
    )

    assert second_retry.status == ToolCallStatus.FAILED
    assert second_retry.error is not None
    assert full_candidate_marker not in second_retry.error.message
    prefix = (
        "TEXT_PLATE AssetPlan work items do not match their text-plate contracts: "
    )
    assert second_retry.error.message.startswith(prefix)
    mismatches = json.loads(second_retry.error.message.removeprefix(prefix))
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch["item_id"] == "asset-plan-text-plate-chain"
    assert mismatch["work_item_id"] == "work-text-plate-chain"
    assert mismatch["field"] == "/text_plate_intent"
    assert "suggested_patches" not in mismatch
    # This fixture has deliberately removed its new item's brief fields.
    # The caller supplies that missing authoring intent; an error must not
    # suggest rewriting an existing carrier's brief to fit a new state.
    author_patches = [
        {"item_id": "asset-plan-text-plate-chain", "op": "add",
         "path": "/brief/presentation_mode", "value": "SCREEN_CONTENT_PLATE"},
        {"item_id": "asset-plan-text-plate-chain", "op": "add",
         "path": "/brief/required_visible_text", "value": ["正确新文案"]},
    ]

    repair = service.agent_runtime.store.get_work_order(repair_id)
    assert repair is not None
    second_retry_turn_id = "specialist-turn-failed-second-retry"
    repair.specialist_turns.append(
        SpecialistTurnRecord(
            id=second_retry_turn_id,
            sequence=2,
            tool_name="artifact.retry_asset_plan_attempt",
            arguments=second_retry_arguments,
            reason="Repair the second invalid field.",
            status=SpecialistTurnStatus.COMPLETED,
            result=second_retry.model_dump(mode="json"),
        )
    )
    service.agent_runtime.store.save_work_order(repair)
    second_retry_ref = ToolAttemptRef(
        project_id=project_id,
        agent_run_id=run_id,
        work_order_id=repair_id,
        attempt_id=second_retry_turn_id,
        tool_name="artifact.retry_asset_plan_attempt",
        turn_status=SpecialistTurnStatus.COMPLETED,
        outcome_status=second_retry.status.value,
        has_arguments=True,
        has_result=True,
    )

    third_retry = service.production_manager._invoke_scoped_tool(
        project_id,
        "artifact.retry_asset_plan_attempt",
        {
            "project_id": project_id,
            "attempt_ref": second_retry_ref.model_dump(mode="json"),
            "patches": author_patches,
        },
        agent_run_id=run_id,
        work_order_id=repair_id,
    )

    assert third_retry.status == ToolCallStatus.SUCCEEDED
    persisted_work = service.get_asset_plan(project_id).asset_plan.items[0].work_items[0]
    assert persisted_work.final_prompt == "第一次重试已修正提示词。"
    assert persisted_work.size == "1344x768"
    persisted_text_item = next(
        item
        for item in service.get_asset_plan(project_id).asset_plan.items
        if item.id == "asset-plan-text-plate-chain"
    )
    assert persisted_text_item.brief is not None
    assert persisted_text_item.brief.required_visible_text == ["正确新文案"]


def test_asset_plan_contract_rejects_cyclic_or_undeclared_work_refs() -> None:
    with pytest.raises(ValidationError, match="dependency cycle"):
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
                            dependency_ids=["derived"],
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

    structured_reference = AssetPlanReference(
        role="user_identity",
        purpose="The user declared this exact photo to be the protagonist.",
        reference_role=ImageReferenceRole.SUBJECT_IDENTITY,
        reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
        asset_id="uploaded-reference",
        asset_version_id="uploaded-reference-v1",
    )
    reference_generated = AssetPlanWorkItem(
        id="reference-generate-character-master",
        operation=AssetPlanOperation.REFERENCE_GENERATE,
        output_role="character_master",
        final_prompt="Derive the first character master from the user's identity photo.",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        ordered_references=[structured_reference],
    )
    assert reference_generated.ordered_references[0].role == "user_identity"
    assert (
        reference_generated.ordered_references[0].reference_role
        == ImageReferenceRole.SUBJECT_IDENTITY
    )

    with pytest.raises(ValidationError, match="GENERATE.*cannot declare image references"):
        AssetPlanWorkItem(
            id="plain-generate-with-reference",
            operation=AssetPlanOperation.GENERATE,
            output_role="character_master",
            final_prompt="Generate a new character.",
            size="1344x768",
            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
            ordered_references=[structured_reference],
        )
    with pytest.raises(ValidationError, match="REFERENCE_GENERATE.*requires"):
        AssetPlanWorkItem(
            id="reference-generate-without-reference",
            operation=AssetPlanOperation.REFERENCE_GENERATE,
            output_role="character_master",
            final_prompt="Derive a character.",
            size="1344x768",
            review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        )

    text_plate = AssetPlanWorkItem(
        id="price-list-text-plate",
        operation=AssetPlanOperation.TEXT_PLATE,
        output_role="exact_price_text",
        final_prompt="Deterministically typeset the exact approved menu text.",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        text_plate_intent=AssetPlanTextPlateIntent(
            background_color="#FFFFFF",
            foreground_color="#111111",
            title="今日菜单",
            exact_lines=["番茄炒蛋 5元", "酸辣土豆丝 3元"],
            layout_description="标题居中，价格行左对齐。",
        ),
    )
    assert text_plate.operation == AssetPlanOperation.TEXT_PLATE

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
    created = service.get_asset_plan(project_id)
    assert created_call.output == {
        "project_id": project_id,
        "operation": "CREATE",
        "asset_plan_identity": {
            "document_id": created.document.id,
            "version_id": created.version.id,
            "version": 1,
        },
        "base_version_id": None,
        "item_count": len(plan.items),
        "work_item_count": sum(len(item.work_items) for item in plan.items),
    }
    assert "asset_plan" not in created_call.output
    assert "document" not in created_call.output
    assert created_call.artifact_refs[0].artifact_type == "asset_plan"
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
    assert replaced_call.output is not None
    assert replaced_call.output["operation"] == "REPLACE"
    assert replaced_call.output["base_version_id"] == created.version.id
    assert replaced_call.output["asset_plan_identity"]["version"] == 2
    assert "asset_plan" not in replaced_call.output
    assert "document" not in replaced_call.output
    replaced = service.get_asset_plan(project_id)
    assert replaced.version.version == 2
    assert replaced.asset_plan.notes == ["保留 Agent 的资产取舍理由。"]
    read_call = service.tool_registry.invoke(
        "artifact.get_asset_plan",
        {"project_id": project_id, "version_id": replaced.version.id},
    )
    assert read_call.status == ToolCallStatus.SUCCEEDED
    assert read_call.output is not None
    read_json = json.dumps(read_call.output, ensure_ascii=False)
    assert read_json.count("生成林夏稳定角色主图。") == 1
    assert "versions" not in read_call.output["document"]
    assert "payload" not in read_call.output["version"]
    assert _slot(
        service,
        project_id,
        GENERATION_PLAN_OUTPUT_SLOT_ID,
    ).current_artifact_ref is not None

    document = service.get_project(project_id).document
    refs = project_artifact_refs(document)
    typed_refs = [item.artifact_type for item in refs]
    assert "asset_plan_index" in typed_refs
    assert "asset_plan" not in typed_refs
    assert typed_refs.index("asset_plan_index") < typed_refs.index("output_slot")
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


def test_replace_asset_plan_rejects_undeclared_item_omissions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "replace-plan-item-omission")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(
        service,
        project_id,
        spec=spec,
        storyboard=storyboard,
    )
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    partial_plan = plan.model_copy(
        update={"items": [plan.items[1].model_copy(deep=True)]},
        deep=True,
    )

    rejected = service.tool_registry.invoke(
        "artifact.replace_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": partial_plan.model_dump(mode="json"),
                "base_version_id": created.version.id,
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None
    assert rejected.error.kind == "WorkflowConflictError"
    assert "complete plan snapshot" in rejected.error.message
    assert "not a local item patch" in rejected.error.message
    assert "asset-plan-character-linxia" in rejected.error.message
    assert "asset-plan-background-crowd" in rejected.error.message
    unchanged = service.get_asset_plan(project_id)
    assert unchanged.version.id == created.version.id
    assert unchanged.asset_plan == plan

    accepted = service.tool_registry.invoke(
        "artifact.replace_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": partial_plan.model_dump(mode="json"),
                "base_version_id": created.version.id,
                "removed_item_ids": [
                    "asset-plan-character-linxia",
                    "asset-plan-background-crowd",
                ],
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert accepted.status == ToolCallStatus.SUCCEEDED
    replaced = service.get_asset_plan(project_id)
    assert replaced.version.version == 2
    assert [item.id for item in replaced.asset_plan.items] == [
        "asset-plan-prop-recorder"
    ]


def test_replace_asset_plan_rejects_undeclared_work_item_omissions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "replace-plan-work-item-omission")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(
        service,
        project_id,
        spec=spec,
        storyboard=storyboard,
    )
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    retained_character = plan.items[0].model_copy(
        update={"work_items": [plan.items[0].work_items[0].model_copy(deep=True)]},
        deep=True,
    )
    replacement = plan.model_copy(
        update={
            "items": [
                retained_character,
                *[item.model_copy(deep=True) for item in plan.items[1:]],
            ]
        },
        deep=True,
    )

    rejected = service.tool_registry.invoke(
        "artifact.replace_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": replacement.model_dump(mode="json"),
                "base_version_id": created.version.id,
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None
    assert rejected.error.kind == "WorkflowConflictError"
    assert "omitted work items from retained items" in rejected.error.message
    assert "work-linxia-derived" in rejected.error.message
    assert service.get_asset_plan(project_id).version.id == created.version.id

    accepted = service.tool_registry.invoke(
        "artifact.replace_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "asset_plan": replacement.model_dump(mode="json"),
                "base_version_id": created.version.id,
                "removed_work_item_ids": ["work-linxia-derived"],
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert accepted.status == ToolCallStatus.SUCCEEDED
    assert [
        item.id
        for item in service.get_asset_plan(project_id).asset_plan.items[0].work_items
    ] == ["work-linxia-master"]


def test_asset_plan_can_be_saved_in_batches_and_resumed_without_rewriting(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "incremental-plan")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan.model_copy(update={"items": []}),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    first = service.patch_asset_plan(
        project_id,
        AssetPlanPatchRequest(
            base_version_id=created.version.id,
            upsert_items=[plan.items[0]],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    index = service.get_asset_plan_index(project_id)
    assert [item.id for item in index.items] == [plan.items[0].id]

    resumed = _service(tmp_path, "incremental-plan")
    continued = resumed.patch_asset_plan(
        project_id,
        AssetPlanPatchRequest(
            base_version_id=first.version.id,
            upsert_items=plan.items[1:],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    assert continued.document.id == created.document.id
    assert continued.asset_plan == plan
    assert resumed.get_asset_plan(project_id).asset_plan.items[0] == plan.items[0]


@pytest.mark.parametrize("invalid_target", [False, True])
def test_append_assets_and_patch_existing_fields_in_one_atomic_save(
    tmp_path: Path, invalid_target: bool,
) -> None:
    service = _service(tmp_path, "mixed-plan-patch")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    initial = plan.model_copy(update={"items": [plan.items[0]]})
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(asset_plan=initial, origin=ProductionDocumentOrigin.AGENT),
    )
    added = plan.items[1]
    call = service.tool_registry.invoke("artifact.patch_asset_plan", {
        "project_id": project_id,
        "request": {
            "base_version_id": created.version.id,
            "upsert_items": [added.model_dump(mode="json")],
            "field_patches": [
                {"item_id": plan.items[0].id, "op": "replace", "path": "/rationale",
                 "value": "保留已采用角色图片，补充本次使用说明。"},
                {"item_id": "missing-item" if invalid_target else added.id,
                 "op": "replace", "path": "/rationale", "value": "新增道具与已有角色共同服务本集。"},
            ],
            "origin": ProductionDocumentOrigin.AGENT.value,
        },
    })
    current = service.get_asset_plan(project_id)
    if invalid_target:
        assert call.status == ToolCallStatus.FAILED
        assert current.version.id == created.version.id
        assert current.asset_plan == initial
    else:
        assert call.status == ToolCallStatus.SUCCEEDED
        assert [item.id for item in current.asset_plan.items] == [plan.items[0].id, added.id]
        assert current.asset_plan.items[0].rationale == "保留已采用角色图片，补充本次使用说明。"
        assert current.asset_plan.items[1].rationale == "新增道具与已有角色共同服务本集。"
        assert current.asset_plan.items[0].work_items == plan.items[0].work_items
        assert current.asset_plan.items[0].completed_asset_version_id == plan.items[0].completed_asset_version_id
        assert current.version.version == created.version.version + 1


def test_parallel_candidate_saves_merge_unrelated_work_without_global_version_conflict(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "parallel-candidates")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    requests: list[AssetPlanPatchRequest] = []
    expected: dict[str, tuple[str, str]] = {}
    for item, work in [(plan.items[0], work) for work in plan.items[0].work_items]:
        asset = service.create_asset(project_id, AssetCreateRequest(
            kind=item.asset_kind, name=item.name + "候选", descriptor="已生成的参考图",
        )).asset
        version = service.import_url_asset_version(project_id, asset.id, AssetImportUrlRequest(
            external_url=f"https://example.test/{work.id}.png", descriptor="已保存候选", media_type="IMAGE",
        )).version
        expected[work.id] = (asset.id, version.id)
        requests.append(AssetPlanPatchRequest(
            base_version_id=created.version.id,
            field_patches=[AssetPlanFieldPatch(
                work_item_id=work.id, op=AssetPlanFieldPatchOperation.ADD, path=path, value=value,
            ) for path, value in [("/candidate_asset_id", asset.id), ("/candidate_asset_version_id", version.id)]],
            origin=ProductionDocumentOrigin.AGENT,
        ))
    service.patch_asset_plan(project_id, requests[0])
    saved = service.patch_asset_plan(project_id, requests[1])
    actual = {work.id: (work.candidate_asset_id, work.candidate_asset_version_id)
              for item in saved.asset_plan.items for work in item.work_items if work.id in expected}
    assert actual == expected
    assert saved.asset_plan.items[0].completed_asset_version_id == plan.items[0].completed_asset_version_id
    # The same completed request can be delivered again without losing the other result.
    repeated = service.patch_asset_plan(project_id, requests[0])
    assert repeated.asset_plan == saved.asset_plan


def test_parallel_field_edits_preserve_sibling_work_and_reject_changed_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "parallel-work-fields")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    def field_edit(work_id: str, text: str) -> AssetPlanPatchRequest:
        return AssetPlanPatchRequest(
            base_version_id=created.version.id,
            field_patches=[AssetPlanFieldPatch(work_item_id=work_id,
                op=AssetPlanFieldPatchOperation.REPLACE, path="/final_prompt", value=text)],
            origin=ProductionDocumentOrigin.AGENT,
        )
    first, second = plan.items[0].work_items[:2]
    service.patch_asset_plan(project_id, field_edit(first.id, "保留用户刚保存的主图修改。"))
    saved = service.patch_asset_plan(project_id, field_edit(second.id, "只调整另外一个补充视角。"))
    assert [work.final_prompt for work in saved.asset_plan.items[0].work_items] == [
        "保留用户刚保存的主图修改。", "只调整另外一个补充视角。",
    ]
    with pytest.raises(WorkflowConflictError, match="selected content changed"):
        service.patch_asset_plan(project_id, field_edit(first.id, "过时任务试图覆盖主图。"))
    with pytest.raises(WorkflowConflictError, match="selector must resolve exactly once"):
        service.patch_asset_plan(project_id, field_edit("unknown-work", "不能猜测对象。"))
    assert service.get_asset_plan(project_id).version.id == saved.version.id
    assert service.get_asset_plan(project_id).asset_plan == saved.asset_plan


def test_patch_asset_plan_merges_local_work_changes_and_versions_full_truth(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "patch-plan")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    revised_master = plan.items[0].work_items[0].model_copy(
        update={"final_prompt": "仅修正林夏主图提示词，保留其他资产计划。"},
        deep=True,
    )

    patched_call = service.tool_registry.invoke(
        "artifact.patch_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "work_item_patches": [
                    {
                        "item_id": "asset-plan-character-linxia",
                        "upsert_work_items": [
                            revised_master.model_dump(mode="json")
                        ],
                        "removed_work_item_ids": ["work-linxia-derived"],
                    }
                ],
                "notes": ["局部修正角色主图工作项。"],
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert patched_call.status == ToolCallStatus.SUCCEEDED
    assert patched_call.output is not None
    assert patched_call.output == {
        "project_id": project_id,
        "asset_plan_identity": {
            "document_id": created.document.id,
            "version_id": patched_call.output["asset_plan_identity"]["version_id"],
            "version": 2,
        },
        "base_version_id": created.version.id,
        "upserted_item_ids": [],
        "work_item_patch_item_ids": ["asset-plan-character-linxia"],
        "upserted_work_item_ids": ["work-linxia-master"],
        "removed_item_ids": [],
        "removed_work_item_ids": ["work-linxia-derived"],
        "notes_updated": True,
    }
    assert "asset_plan" not in patched_call.output
    assert "document" not in patched_call.output
    current = service.get_asset_plan(project_id)
    assert current.version.version == 2
    assert current.version.parent_version_id == created.version.id
    assert current.asset_plan.notes == ["局部修正角色主图工作项。"]
    assert [item.id for item in current.asset_plan.items] == [
        "asset-plan-character-linxia",
        "asset-plan-prop-recorder",
        "asset-plan-background-crowd",
    ]
    character = current.asset_plan.items[0]
    assert [item.id for item in character.work_items] == ["work-linxia-master"]
    assert character.work_items[0].final_prompt == revised_master.final_prompt


def test_patch_asset_plan_applies_compact_fields_across_items_atomically(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "patch-plan-fields")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    patched_call = service.tool_registry.invoke(
        "artifact.patch_asset_plan_fields",
        {
            "project_id": project_id,
            "base_version_id": created.version.id,
            "patches": [
                {
                    "item_id": "asset-plan-character-linxia",
                    "op": "replace",
                    "path": "/rationale",
                    "value": "角色保持稳定身份锚点，并按镜头需要派生视图。",
                },
                {
                    "item_id": "asset-plan-character-linxia",
                    "work_item_id": "work-linxia-master",
                    "op": "replace",
                    "path": "/final_prompt",
                    "value": "生成林夏稳定身份主图，背景干净，五官与服装清晰。",
                },
                {
                    "item_id": "asset-plan-prop-recorder",
                    "op": "replace",
                    "path": "/rationale",
                    "value": "录音机作为跨镜头连续出现的关键道具。",
                },
                {
                    "item_id": "asset-plan-character-linxia",
                    "work_item_id": "work-linxia-derived",
                    "op": "remove",
                    "path": "",
                },
            ],
            "notes": ["跨多项的稳定 ID 字段修订。"],
            "origin": ProductionDocumentOrigin.AGENT.value,
        },
    )

    assert patched_call.status == ToolCallStatus.SUCCEEDED
    assert patched_call.output is not None
    assert "asset_plan" not in patched_call.output
    current = service.get_asset_plan(project_id)
    assert current.version.version == 2
    assert current.asset_plan.notes == ["跨多项的稳定 ID 字段修订。"]
    character = current.asset_plan.items[0]
    assert character.rationale == "角色保持稳定身份锚点，并按镜头需要派生视图。"
    assert character.work_items[0].final_prompt == (
        "生成林夏稳定身份主图，背景干净，五官与服装清晰。"
    )
    assert [item.id for item in character.work_items] == ["work-linxia-master"]
    assert current.asset_plan.items[1].rationale == (
        "录音机作为跨镜头连续出现的关键道具。"
    )


def test_patch_asset_plan_accepts_later_cross_asset_dependency_and_keeps_order(
    tmp_path: Path,
) -> None:
    """A character coverage image may depend on a later listed prop master."""
    name = "cross-asset-dependency"
    service = _service(tmp_path, name)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    prop = plan.items[1]
    prop.work_items = [AssetPlanWorkItem(
        id="work-prop-master",
        operation=AssetPlanOperation.GENERATE,
        output_role="prop_master",
        final_prompt="已有录音笔的精确主图。",
        size="1344x768",
        review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
        completed_asset_id=prop.completed_asset_id,
        completed_asset_version_id=prop.completed_asset_version_id,
    )]
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    derived = plan.items[0].work_items[1].model_copy(deep=True)
    derived.dependency_ids.append("work-prop-master")
    derived.ordered_references.append(AssetPlanReference(
        role="prop_identity",
        purpose="镜头中保持同一录音笔。",
        source_work_item_id="work-prop-master",
    ))
    result = service.tool_registry.invoke("artifact.patch_asset_plan", {
        "project_id": project_id,
        "request": {
            "base_version_id": created.version.id,
            "work_item_patches": [{
                "item_id": plan.items[0].id,
                "upsert_work_items": [derived.model_dump(mode="json")],
            }],
            "origin": "AGENT",
        },
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    reopened = _service(tmp_path, name)
    persisted = reopened.get_asset_plan(project_id).asset_plan
    assert [item.id for item in persisted.items] == [item.id for item in plan.items]
    assert [work.id for item in persisted.items for work in item.work_items] == [
        "work-linxia-master", "work-linxia-derived", "work-prop-master",
    ]
    index = reopened.get_asset_plan_index(project_id)
    assert index.items[0].work_items[1].status.value == "READY"
    assert not index.execution_contract.needs_reconciliation
    context = reopened.tool_registry.invoke("artifact.get_asset_work_context", {
        "project_id": project_id,
        "scope": {"mode": "WORK_ITEM", "work_item_ids": [derived.id]},
    })
    assert context.status == ToolCallStatus.SUCCEEDED
    assert context.output is not None
    assert set(context.output["dependency_work_item_ids"]) == {
        "work-linxia-master", "work-prop-master",
    }
    assert {ref.artifact_id for ref in context.artifact_refs
            if ref.artifact_type == "asset_version"} >= {
        prop.completed_asset_version_id, plan.items[0].completed_asset_version_id,
    }
    assert reopened.repository.list_tasks(project_id) == []
    assert reopened.repository.list_image_tasks(project_id) == []


@pytest.mark.parametrize("defect", ["self_cycle", "cross_cycle", "unknown", "undeclared"])
def test_patch_asset_plan_rejects_invalid_dependency_without_partial_write(
    tmp_path: Path, defect: str,
) -> None:
    service = _service(tmp_path, defect)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    derived = plan.items[0].work_items[1].model_copy(deep=True)
    if defect == "self_cycle":
        derived.dependency_ids.append(derived.id)
    elif defect == "cross_cycle":
        derived = plan.items[0].work_items[0].model_copy(deep=True)
        derived.dependency_ids = ["work-linxia-derived"]
    elif defect == "unknown":
        derived.dependency_ids.append("missing-work")
    else:
        derived.dependency_ids = []
    rejected = service.tool_registry.invoke("artifact.patch_asset_plan", {
        "project_id": project_id,
        "request": {
            "base_version_id": created.version.id,
            "work_item_patches": [{
                "item_id": plan.items[0].id,
                "upsert_work_items": [derived.model_dump(mode="json")],
            }],
            "origin": "AGENT",
        },
    })
    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None
    expected = {"self_cycle": "dependency cycle", "cross_cycle": "dependency cycle",
                "unknown": "unknown dependency", "undeclared": "as a dependency"}
    assert expected[defect] in rejected.error.message
    current = service.get_asset_plan(project_id)
    assert current.version.id == created.version.id
    assert current.asset_plan == plan


def test_patch_asset_plan_revalidates_merged_dependency_graph_atomically(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "patch-invalid-dependency")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    rejected = service.tool_registry.invoke(
        "artifact.patch_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "work_item_patches": [
                    {
                        "item_id": "asset-plan-character-linxia",
                        "removed_work_item_ids": ["work-linxia-master"],
                    }
                ],
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None
    assert rejected.error.kind == "WorkflowConflictError"
    assert "structurally invalid" in rejected.error.message
    unchanged = service.get_asset_plan(project_id)
    assert unchanged.version.id == created.version.id
    assert unchanged.asset_plan == plan


def test_patch_asset_plan_aggregates_all_missing_served_clip_groups(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "patch-missing-groups")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    character = plan.items[0].model_copy(
        update={"serves_clip_group_ids": []},
        deep=True,
    )
    prop = plan.items[1].model_copy(
        update={"serves_clip_group_ids": []},
        deep=True,
    )

    rejected = service.tool_registry.invoke(
        "artifact.patch_asset_plan",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "upsert_items": [
                    character.model_dump(mode="json"),
                    prop.model_dump(mode="json"),
                ],
                "origin": ProductionDocumentOrigin.AGENT.value,
            },
        },
    )

    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None
    assert "AssetPlan omits ClipGroups for its served Shots" in (
        rejected.error.message
    )
    assert "asset-plan-character-linxia ->" in rejected.error.message
    assert "asset-plan-prop-recorder ->" in rejected.error.message
    assert plan.items[0].serves_clip_group_ids[0] in rejected.error.message
    unchanged = service.get_asset_plan(project_id)
    assert unchanged.version.id == created.version.id
    assert unchanged.asset_plan == plan


def test_asset_planning_context_tool_works_before_spec_or_storyboard(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-context-before-storyboard")
    project_id = service.initialize_fixture().document.project.id

    call = service.tool_registry.invoke(
        "artifact.get_asset_planning_context", {"project_id": project_id}
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    assert call.output["production_spec"] is None
    assert call.output["storyboard"] is None
    assert call.output["existing_asset_plan_index"] is None
    assert {ref.artifact_type for ref in call.artifact_refs} >= {
        "project", "script_version", "bible_version"
    }
    assert not any(ref.artifact_type in {"production_spec", "storyboard"} for ref in call.artifact_refs)


def test_asset_planning_context_returns_each_current_truth_once_without_histories(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-planning-context")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    call = service.tool_registry.invoke(
        "artifact.get_asset_planning_context",
        {"project_id": project_id},
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    output = call.output
    document = service.get_project(project_id).document
    assert output["project_id"] == project_id
    assert output["original_user_brief"] == document.project.input_text
    assert output["script"]["id"] == document.project.current_script_version_id
    assert output["bible"]["id"] == document.project.current_bible_version_id
    assert output["production_spec_identity"]["version_id"] == spec.version.id
    assert output["storyboard_identity"]["version_id"] == storyboard.version.id
    assert output["production_spec"] == spec.spec.model_dump(mode="json")
    assert output["storyboard"] == storyboard.storyboard.model_dump(mode="json")
    assert output["existing_asset_plan_index"] is not None
    assert output["existing_asset_plan_index"]["asset_plan_identity"] == {
        "document_id": created.document.id,
        "version_id": created.version.id,
        "version": created.version.version,
    }
    assert "asset_plan" not in output
    assert "project" not in output
    assert "document" not in output
    assert "version" not in output
    serialized = json.dumps(output, ensure_ascii=False)
    for forbidden in (
        '"script_versions"',
        '"bible_versions"',
        '"production_documents"',
        '"output_slots"',
    ):
        assert forbidden not in serialized
    assert all(
        set(asset) == {
            "id",
            "name",
            "asset_kind",
            "bible_entity_id",
            "current_version_id",
            "current_version",
            "approved_version_id",
            "approved_version",
            "is_source_intake",
        }
        for asset in output["existing_assets"]
    )
    plan_index_json = json.dumps(
        output["existing_asset_plan_index"],
        ensure_ascii=False,
    )
    assert "final_prompt" not in plan_index_json
    assert "ordered_references" not in plan_index_json
    assert "identity_anchor" not in plan_index_json
    assert {ref.artifact_type for ref in call.artifact_refs} >= {
        "project",
        "script_version",
        "bible_version",
        "production_spec",
        "storyboard",
        "production_document",
        "production_document_version",
        "shot",
        "clip_group",
        "asset",
        "asset_version",
        "asset_plan",
    }


def test_asset_planning_context_keeps_source_current_and_approved_version_once(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-planning-source-intake")
    project = service.create_project(
        CreateProjectRequest(
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="男高中生在学校食堂展示心算天赋。",
            target_duration_seconds=60.0,
            input_images=[
                CreativeInputImage(
                    external_url="https://example.test/hero-source.png",
                    descriptor="用户指定的男主身份参考",
                )
            ],
        ),
        project_id="asset-planning-source-project",
    )
    project_id = project.document.project.id
    document = service.get_project(project_id).document
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    assert script.story_intent is not None
    spec_content = _production_spec(service, project_id).model_copy(
        update={
            "duration_contract": script.story_intent.duration_contract.model_copy(
                deep=True
            )
        }
    )
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=spec_content,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    service.create_storyboard(
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

    call = service.tool_registry.invoke(
        "artifact.get_asset_planning_context",
        {"project_id": project_id},
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    source = call.output["source_intake"][0]
    assert source["current_version_id"] == source["approved_version_id"]
    assert [version["id"] for version in source["exact_versions"]] == [
        source["current_version_id"]
    ]
    assert len({version["id"] for version in source["exact_versions"]}) == 1
    indexed = next(
        asset
        for asset in call.output["existing_assets"]
        if asset["id"] == source["asset_id"]
    )
    assert indexed["is_source_intake"] is True
    assert "versions" not in indexed
    assert "brief" not in indexed


def test_asset_work_context_returns_selected_work_and_dependency_slice(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-work-slice")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    sibling_pack_intent = plan.items[0].work_items[1].reference_pack_intent
    assert sibling_pack_intent is not None
    sibling = plan.items[0].work_items[1].model_copy(
        update={
            "id": "work-linxia-sibling",
            "output_role": "character_back_coverage",
            "reference_pack_intent": sibling_pack_intent.model_copy(
                update={
                    "role": ReferenceRole.CHARACTER_BACK,
                    "angle": ReferenceAngle.BACK,
                    "tags": ["identity", "back"],
                }
            ),
        },
        deep=True,
    )
    plan.items[0].work_items.append(sibling)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    call = service.tool_registry.invoke(
        "artifact.get_asset_work_context",
        {
            "project_id": project_id,
            "scope": {
                "mode": "WORK_ITEM",
                "work_item_ids": ["work-linxia-derived"],
            },
        },
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    assert call.output["asset_plan_identity"] == {
        "document_id": created.document.id,
        "version_id": created.version.id,
        "version": created.version.version,
    }
    assert call.output["requested_work_item_ids"] == ["work-linxia-derived"]
    assert call.output["dependency_work_item_ids"] == ["work-linxia-master"]
    assert [item["id"] for item in call.output["asset_plan_items"]] == [
        "asset-plan-character-linxia"
    ]
    sliced_item = call.output["asset_plan_items"][0]
    assert [item["id"] for item in sliced_item["work_items"]] == [
        "work-linxia-master",
        "work-linxia-derived",
    ]
    assert sliced_item["omitted_work_item_ids"] == ["work-linxia-sibling"]
    assert call.output["storyboard"]["shots"] == []
    assert call.output["storyboard"]["clip_groups"] == []
    assert call.output["production_spec"]["project_id"] == project_id
    assert call.output["source_intake"] == []
    context_ref_types = {ref.artifact_type for ref in call.artifact_refs}
    assert "asset_plan_index" in context_ref_types
    assert "asset" in context_ref_types
    assert "asset_version" in context_ref_types
    assert context_ref_types.isdisjoint(
        {"asset_plan", "production_spec", "storyboard"}
    )

    scoped_call = service.tool_registry.invoke(
        "artifact.get_asset_work_context",
        {
            "project_id": project_id,
            "scope": {
                "mode": "WORK_ITEM",
                "work_item_ids": ["work-linxia-derived"],
            },
            "shot_ids": [plan.items[0].serves_shot_ids[0]],
            "clip_group_ids": [plan.items[0].serves_clip_group_ids[0]],
        },
    )
    assert scoped_call.status == ToolCallStatus.SUCCEEDED
    assert scoped_call.output is not None
    assert [
        work["id"]
        for work in scoped_call.output["asset_plan_items"][0]["work_items"]
    ] == ["work-linxia-master", "work-linxia-derived"]
    assert [shot["id"] for shot in scoped_call.output["storyboard"]["shots"]] == [
        plan.items[0].serves_shot_ids[0]
    ]
    assert [
        group["id"] for group in scoped_call.output["storyboard"]["clip_groups"]
    ] == [plan.items[0].serves_clip_group_ids[0]]


def test_asset_work_context_item_scope_returns_the_complete_selected_item(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-item-slice")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    call = service.tool_registry.invoke(
        "artifact.get_asset_work_context",
        {
            "project_id": project_id,
            "scope": {
                "mode": "ITEM",
                "item_ids": ["asset-plan-character-linxia"],
            },
        },
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    assert call.output["requested_item_ids"] == ["asset-plan-character-linxia"]
    assert call.output["requested_work_item_ids"] == []
    assert [item["id"] for item in call.output["asset_plan_items"]] == [
        "asset-plan-character-linxia"
    ]
    selected = call.output["asset_plan_items"][0]
    assert [work["id"] for work in selected["work_items"]] == [
        "work-linxia-master",
        "work-linxia-derived",
    ]
    assert selected["omitted_work_item_ids"] == []
    assert [shot["id"] for shot in call.output["storyboard"]["shots"]] == [
        plan.items[0].serves_shot_ids[0]
    ]
    assert [
        group["id"] for group in call.output["storyboard"]["clip_groups"]
    ] == [plan.items[0].serves_clip_group_ids[0]]


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "project_id": "project-old-selector",
            "item_ids": ["asset-plan-character-linxia"],
            "work_item_ids": [],
        },
        {
            "project_id": "project-mixed-scope",
            "scope": {
                "mode": "ITEM",
                "item_ids": ["asset-plan-character-linxia"],
                "work_item_ids": ["work-linxia-master"],
            },
        },
        {
            "project_id": "project-mixed-top-level",
            "scope": {
                "mode": "WORK_ITEM",
                "work_item_ids": ["work-linxia-master"],
            },
            "item_ids": ["asset-plan-character-linxia"],
        },
    ],
)
def test_asset_work_context_rejects_legacy_or_mixed_selectors(
    tmp_path: Path,
    arguments: dict[str, object],
) -> None:
    call = _service(tmp_path, "asset-invalid-scope").tool_registry.invoke(
        "artifact.get_asset_work_context",
        arguments,
    )

    assert call.status == ToolCallStatus.INVALID_ARGUMENTS
    assert call.error is not None
    assert call.error.kind == "ValidationError"


def test_asset_plan_requires_stable_canonical_lineage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-plan-canonical-first")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character = plan.items[0]
    canonical = character.work_items[0]
    supplemental = character.work_items[1].model_copy(
        update={
            "operation": AssetPlanOperation.GENERATE,
            "ordered_references": [],
            "dependency_ids": [],
            "reference_pack_intent": character.work_items[
                1
            ].reference_pack_intent.model_copy(update={"lineage_root": None}),
        },
        deep=True,
    )
    invalid_character = character.model_copy(
        update={
            "work_items": [supplemental, canonical],
            "completed_asset_id": None,
            "completed_asset_version_id": None,
        },
        deep=True,
    )
    invalid_plan = plan.model_copy(
        update={"items": [invalid_character, *plan.items[1:]]},
        deep=True,
    )

    with pytest.raises(
        WorkflowConflictError,
        match="must use canonical work item .* as its lineage root",
    ):
        service.create_asset_plan(
            project_id,
            AssetPlanCreateRequest(
                asset_plan=invalid_plan,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )


def test_asset_plan_item_card_layout_is_independent_from_basic_work_outputs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-plan-layout-size")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character = plan.items[0]
    assert character.brief is not None
    card_with_individual_outputs = character.model_copy(
        update={
            "brief": character.brief.model_copy(
                    update={
                        "reference_strategy": (
                            AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS
                        ),
                        "layout_profile": (
                            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9
                        ),
                        "requested_views": [
                            AssetStandardView.CANONICAL_MASTER,
                            AssetStandardView.CHARACTER_LEFT_PROFILE,
                        ],
                    },
                deep=True,
            ),
            "work_items": [
                work.model_copy(update={"size": "1024x1024"}, deep=True)
                for work in character.work_items
            ],
        },
        deep=True,
    )
    updated_plan = plan.model_copy(
        update={"items": [card_with_individual_outputs, *plan.items[1:]]},
        deep=True,
    )

    service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=updated_plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    persisted = service.get_asset_plan(project_id).asset_plan.items[0]
    assert persisted.brief is not None
    assert (
        persisted.brief.layout_profile
        == AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9
    )
    assert {
        work.resolved_output_layout_profile() for work in persisted.work_items
    } == {AssetLayoutProfile.LIGHTWEIGHT_SINGLE}


def test_real_scale_character_work_slice_and_enqueue_keep_sheet_layout_local_to_compose(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-plan-real-scale-layout")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    character = plan.items[0]
    assert character.brief is not None
    canonical = character.work_items[0].model_copy(
        update={
            "output_view": AssetStandardView.CANONICAL_MASTER,
            "size": "1024x1024",
        },
        deep=True,
    )

    def derived(
        work_id: str,
        view: AssetStandardView,
        role: ReferenceRole,
        angle: ReferenceAngle,
    ) -> AssetPlanWorkItem:
        return AssetPlanWorkItem(
            id=work_id,
            operation=AssetPlanOperation.EDIT,
            output_role=work_id,
            output_view=view,
            final_prompt=f"以母版派生 {view.value} 单张视图。",
            size="1024x1024",
            review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
            reference_pack_intent=AssetPlanReferencePackIntent(
                subject_item_id=character.id,
                role=role,
                angle=angle,
                framing=ReferenceFraming.FULL_BODY,
                tags=["identity", view.value.lower()],
                lineage_root=AssetPlanReference(
                    role="identity_lineage_root",
                    purpose="记录母版血缘。",
                    source_work_item_id=canonical.id,
                ),
            ),
            ordered_references=[
                AssetPlanReference(
                    role="identity_master",
                    purpose="锁定同一人物身份。",
                    source_work_item_id=canonical.id,
                )
            ],
            dependency_ids=[canonical.id],
        )

    front = derived(
        "work-linxia-front",
        AssetStandardView.CHARACTER_FRONT,
        ReferenceRole.CHARACTER_BODY,
        ReferenceAngle.FRONT,
    )
    left = derived(
        "work-linxia-left",
        AssetStandardView.CHARACTER_LEFT_PROFILE,
        ReferenceRole.CHARACTER_PROFILE,
        ReferenceAngle.LEFT_PROFILE,
    )
    right = derived(
        "work-linxia-right",
        AssetStandardView.CHARACTER_RIGHT_PROFILE,
        ReferenceRole.CHARACTER_PROFILE,
        ReferenceAngle.RIGHT_PROFILE,
    )
    back = derived(
        "work-linxia-back",
        AssetStandardView.CHARACTER_BACK,
        ReferenceRole.CHARACTER_BACK,
        ReferenceAngle.BACK,
    )
    sheet = AssetPlanWorkItem(
        id="work-linxia-sheet",
        operation=AssetPlanOperation.COMPOSE,
        output_role="character_reference_sheet",
        output_view=AssetStandardView.CHARACTER_SHEET,
        final_prompt="将已生成的四个视图确定性排成角色卡。",
        size="1536x864",
        review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
        reference_pack_intent=AssetPlanReferencePackIntent(
            subject_item_id=character.id,
            role=ReferenceRole.CHARACTER_REFERENCE_SHEET,
            angle=ReferenceAngle.NOT_APPLICABLE,
            framing=ReferenceFraming.FULL_BODY,
            tags=["identity", "reference-sheet"],
            lineage_root=AssetPlanReference(
                role="identity_lineage_root",
                purpose="角色卡归属同一母版。",
                source_work_item_id=canonical.id,
            ),
        ),
        dependency_ids=[canonical.id, front.id, left.id, back.id],
        compose_intent=AssetPlanComposeIntent(
            strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
            cells=[
                AssetPlanComposeCell(
                    source=AssetPlanReference(
                        role="canonical_face",
                        purpose="正脸身份基准。",
                        source_work_item_id=canonical.id,
                    ),
                    view=AssetStandardView.CANONICAL_MASTER,
                ),
                AssetPlanComposeCell(
                    source=AssetPlanReference(
                        role="front_body",
                        purpose="正面全身。",
                        source_work_item_id=front.id,
                    ),
                    view=AssetStandardView.CHARACTER_FRONT,
                ),
                AssetPlanComposeCell(
                    source=AssetPlanReference(
                        role="profile_body",
                        purpose="侧面全身。",
                        source_work_item_id=left.id,
                    ),
                    view=AssetStandardView.CHARACTER_LEFT_PROFILE,
                ),
                AssetPlanComposeCell(
                    source=AssetPlanReference(
                        role="back_body",
                        purpose="背面全身。",
                        source_work_item_id=back.id,
                    ),
                    view=AssetStandardView.CHARACTER_BACK,
                ),
            ],
            columns=2,
            layout_profile=AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            show_labels=True,
            background_color="#FFFFFF",
            target_size="1536x864",
        ),
    )
    character = character.model_copy(
        update={
            "brief": character.brief.model_copy(
                update={
                    "reference_strategy": (
                        AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS
                    ),
                    "layout_profile": (
                        AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9
                    ),
                    "requested_views": [
                        AssetStandardView.CANONICAL_MASTER,
                        AssetStandardView.CHARACTER_FRONT,
                        AssetStandardView.CHARACTER_LEFT_PROFILE,
                        AssetStandardView.CHARACTER_RIGHT_PROFILE,
                        AssetStandardView.CHARACTER_BACK,
                        AssetStandardView.CHARACTER_SHEET,
                    ],
                },
                deep=True,
            ),
            "work_items": [canonical, front, left, right, back, sheet],
        },
        deep=True,
    )
    plan = plan.model_copy(
        update={"items": [character, *plan.items[1:]]},
        deep=True,
    )
    service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    context = service.get_asset_work_context(
        project_id,
        item_ids=[],
        work_item_ids=[canonical.id, front.id],
        shot_ids=[],
        clip_group_ids=[],
    )
    item = context.asset_plan_items[0]
    assert item.brief is not None
    assert (
        item.brief.layout_profile
        == AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9
    )
    assert [work.id for work in item.work_items] == [canonical.id, front.id]
    assert len(item.omitted_work_item_ids) == 4
    assert all(
        work.resolved_output_layout_profile()
        == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
        for work in item.work_items
    )
    persisted_sheet = next(
        work for work in character.work_items if work.id == sheet.id
    )
    assert (
        persisted_sheet.resolved_output_layout_profile()
        == AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9
    )

    assert character.completed_asset_id is not None
    service.update_asset_brief(
        project_id,
        character.completed_asset_id,
        AssetBriefUpdateRequest(brief=character.brief),
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(api_key="test-key")
    )
    for work in (canonical, front):
        queued = service.enqueue_image_task(
            project_id,
            character.completed_asset_id,
            ImageTaskCreateRequest(
                prompt=work.final_prompt,
                descriptor=work.output_role,
                size=work.size,
                layout_profile=work.resolved_output_layout_profile(),
            ),
        )
        assert queued.task.request.size == "1024x1024"
        assert (
            queued.task.request.layout_profile
            == AssetLayoutProfile.LIGHTWEIGHT_SINGLE
        )


def test_asset_plan_index_is_payload_light_and_exposes_delegation_truth(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "asset-plan-index")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(
        project_id,
        AssetPlanCreateRequest(
            asset_plan=plan,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    call = service.tool_registry.invoke(
        "artifact.get_asset_plan_index",
        {"project_id": project_id},
    )

    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    assert call.output["asset_plan_identity"] == {
        "document_id": created.document.id,
        "version_id": created.version.id,
        "version": created.version.version,
    }
    assert call.output["production_spec_identity"]["version_id"] == (
        plan.production_spec_version_id
    )
    assert call.output["storyboard_identity"]["version_id"] == (
        plan.storyboard_version_id
    )
    assert call.output["execution_contract"] == {
        "needs_reconciliation": False,
        "issues": [],
    }
    character = call.output["items"][0]
    assert character["id"] == "asset-plan-character-linxia"
    assert character["name"] == plan.items[0].name
    assert character["asset_kind"] == "CHARACTER"
    assert character["disposition"] == "ANCHOR"
    assert character["layout_profile"] == "LIGHTWEIGHT_SINGLE"
    assert character["status"] == "IN_PROGRESS"
    assert character["serves_shot_ids"] == plan.items[0].serves_shot_ids
    assert [work["status"] for work in character["work_items"]] == [
        "COMPLETED",
        "READY",
    ]
    assert character["work_items"][1]["dependency_ids"] == [
        "work-linxia-master"
    ]
    assert set(character) == {
        "id",
        "name",
        "asset_kind",
        "disposition",
        "layout_profile",
        "status",
        "bible_entity_id",
        "serves_shot_ids",
        "serves_clip_group_ids",
        "completed_asset_id",
        "completed_asset_version_id",
        "work_items",
    }
    assert "brief" not in character
    assert "rationale" not in character
    assert "final_prompt" not in character["work_items"][0]
    assert "ordered_references" not in character["work_items"][0]
    assert {ref.artifact_type for ref in call.artifact_refs} == {
        "asset_plan_index"
    }
    summary = ProjectArtifactResolver(service.repository).summarize_artifact(
        call.artifact_refs[0]
    )
    assert summary is not None
    dispatch_index = json.loads(summary.summary)
    assert dispatch_index["columns"] == [
        "work_id",
        "status",
        "operation",
        "output_role",
        "output_view",
        "output_layout_profile",
        "size",
        "dependency_ids",
        "review_purpose",
        "reference_pack_subject_item_id",
        "completed_asset_id",
        "completed_asset_version_id",
        "candidate_asset_id",
        "candidate_asset_version_id",
        "candidate_approval_status",
        "candidate_freshness",
    ]
    assert dispatch_index["execution_contract"] == {
        "needs_reconciliation": False,
        "issues": [],
    }
    dispatch_character = dispatch_index["items"][0]
    assert dispatch_character["status"] == "IN_PROGRESS"
    assert dispatch_character["shot_count"] == len(plan.items[0].serves_shot_ids)
    assert dispatch_character["clip_group_count"] == len(
        plan.items[0].serves_clip_group_ids
    )
    assert dispatch_character["work"][1][2:5] == [
        "EDIT",
        "character_coverage",
        None,
    ]
    assert dispatch_character["work"][1][9] == plan.items[0].id


def test_draft_candidate_survives_restart_and_remains_distinct_from_completion(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "candidate-handoff")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    root = plan.items[0].work_items[0].model_copy(update={
        "completed_asset_id": None,
        "completed_asset_version_id": None,
    })
    character = plan.items[0].model_copy(update={
        "completed_asset_id": None,
        "completed_asset_version_id": None,
        "work_items": [root, plan.items[0].work_items[1]],
    })
    plan = plan.model_copy(update={"items": [character, *plan.items[1:]]})
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    candidate = service.create_asset(project_id, AssetCreateRequest(
        kind=AssetKind.CHARACTER, name="New candidate", descriptor="Unreviewed image",
    )).asset
    version = service.import_url_asset_version(project_id, candidate.id, AssetImportUrlRequest(
        external_url="https://example.test/new-candidate.png",
        descriptor="A draft candidate", media_type="IMAGE",
    )).version
    unrelated = service.import_url_asset_version(project_id, candidate.id, AssetImportUrlRequest(
        external_url="https://example.test/unselected-history.png",
        descriptor="UNRELATED_MEDIA_HISTORY", media_type="IMAGE",
    )).version
    assert version.approval_status.value == "DRAFT"
    patched = service.patch_asset_plan(project_id, AssetPlanPatchRequest(
        base_version_id=created.version.id,
        field_patches=[
            AssetPlanFieldPatch(
                item_id=character.id, work_item_id=root.id,
                op=AssetPlanFieldPatchOperation.ADD, path=path, value=value,
            )
            for path, value in [
                ("/candidate_asset_id", candidate.id),
                ("/candidate_asset_version_id", version.id),
            ]
        ],
        origin=ProductionDocumentOrigin.AGENT,
    ))
    reopened = _service(tmp_path, "candidate-handoff")
    index = reopened.get_asset_plan_index(project_id)
    assert index.items[0].status.value == "IN_PROGRESS"
    assert [work.status.value for work in index.items[0].work_items] == [
        "CANDIDATE_AVAILABLE", "BLOCKED",
    ]
    assert index.items[0].work_items[0].candidate_asset_version_id == version.id
    assert index.items[0].work_items[0].completed_asset_version_id is None
    summary = ProjectArtifactResolver(reopened.repository).summarize_artifact(ArtifactRef(
        project_id=project_id, artifact_type="asset_plan_index",
        artifact_id=created.document.id, version=patched.version.version,
    ))
    assert summary is not None
    compact = json.loads(summary.summary)
    assert compact["items"][0]["work"][0][1] == "CANDIDATE_AVAILABLE"
    assert compact["items"][0]["work"][0][-4:] == [
        candidate.id, version.id, "DRAFT", "CURRENT",
    ]
    assert compact["items"][0]["work"][1][1] == "BLOCKED"
    context = reopened.get_asset_work_context(
        project_id, item_ids=[], work_item_ids=[root.id], shot_ids=[], clip_group_ids=[],
    )
    assert context.asset_plan_items[0].work_items[0].candidate_asset_version_id == version.id
    assert len(context.asset_plan_items[0].work_items) == 1
    # Only the declared source and the selected candidate, not the newer
    # unselected version of the same Asset, belong to this task.
    assert len(context.media_versions) == 2
    selected_candidate = next(media for media in context.media_versions
                              if media.asset_version_id == version.id)
    assert selected_candidate.approval_status == "DRAFT"
    assert unrelated.id not in context.model_dump_json()
    assert "UNRELATED_MEDIA_HISTORY" not in context.model_dump_json()

    # Reviews are durable AssetVersion facts, visible without rewriting the plan
    # or depending on the Leader retaining older worker/action transcripts.
    for decision in [AssetReviewDecision.APPROVED, AssetReviewDecision.REJECTED]:
        reopened.review_asset_version(project_id, candidate.id, version.id,
                                      AssetReviewRequest(decision=decision,
                                                         purpose=AssetReviewPurpose.CANONICAL_MASTER,
                                                         notes="Existing review"))
        reopened = _service(tmp_path, "candidate-handoff")
        current_index = reopened.get_asset_plan_index(project_id)
        current_work = current_index.items[0].work_items[0]
        assert current_work.candidate_approval_status == decision
        assert current_work.candidate_freshness == "CURRENT"
        assert current_work.completed_asset_version_id is None
        assert current_index.items[0].work_items[1].status.value == "BLOCKED"
        current_summary = ProjectArtifactResolver(reopened.repository).summarize_artifact(
            ArtifactRef(project_id=project_id, artifact_type="asset_plan_index",
                        artifact_id=created.document.id, version=patched.version.version)
        )
        assert current_summary is not None
        assert json.loads(current_summary.summary)["items"][0]["work"][0][-2:] == [
            decision, "CURRENT",
        ]
        repair_context = reopened.tool_registry.invoke(
            "artifact.get_asset_work_context",
            {"project_id": project_id,
             "scope": {"mode": "WORK_ITEM", "work_item_ids": [root.id]}},
        )
        assert repair_context.status == ToolCallStatus.SUCCEEDED
        assert repair_context.output is not None
        media = repair_context.output["media_versions"]
        assert len(media) == 2
        candidate_media = next(item for item in media if item["asset_version_id"] == version.id)
        assert candidate_media["asset_id"] == candidate.id
        assert candidate_media["approval_status"] == decision
        assert candidate_media["review_notes"] == "Existing review"
        assert candidate_media["external_url"] == "https://example.test/new-candidate.png"
        assert candidate_media["freshness"] == "CURRENT"
        assert any(ref.artifact_type == "asset_version" and ref.artifact_id == version.id
                   for ref in repair_context.artifact_refs)

    for asset_id, version_id, expected_error in [
        (candidate.id, "missing-version", "unknown AssetVersion"),
        (candidate.id, candidate.current_version_id, "usable image media"),
    ]:
        invalid = patched.asset_plan.model_copy(deep=True)
        invalid.items[0].work_items[0].candidate_asset_id = asset_id
        invalid.items[0].work_items[0].candidate_asset_version_id = version_id
        with pytest.raises(WorkflowConflictError, match=expected_error):
            reopened._validate_asset_plan(reopened.get_project(project_id).document, invalid)

    reopened.review_asset_version(
        project_id, candidate.id, version.id,
        AssetReviewRequest(decision=AssetReviewDecision.APPROVED,
                           purpose=AssetReviewPurpose.CANONICAL_MASTER,
                           notes="Canonical master is ready for downstream work"),
    )
    canonical_patches = [
        AssetPlanFieldPatch(
            item_id=character.id, work_item_id=work_item_id,
            op=AssetPlanFieldPatchOperation.ADD, path=path, value=value,
        )
        for work_item_id in [None, root.id]
        for path, value in [
            ("/completed_asset_id", candidate.id),
            ("/completed_asset_version_id", version.id),
        ]
    ]
    reopened.patch_asset_plan(project_id, AssetPlanPatchRequest(
        base_version_id=patched.version.id, field_patches=canonical_patches,
        origin=ProductionDocumentOrigin.AGENT,
    ))
    completed_index = _service(tmp_path, "candidate-handoff").get_asset_plan_index(project_id)
    assert completed_index.items[0].completed_asset_id == candidate.id
    assert completed_index.items[0].status.value == "IN_PROGRESS"
    assert [work.status.value for work in completed_index.items[0].work_items] == [
        "COMPLETED", "READY",
    ]
    assert completed_index.items[0].work_items[1].completed_asset_version_id is None


def test_asset_work_context_exposes_usable_carrier_pack_for_selected_text_member(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "scoped-reference-packs")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)

    def approved_image(kind: AssetKind, name: str) -> tuple[str, str]:
        asset = service.create_asset(project_id, AssetCreateRequest(
            kind=kind, name=name, descriptor=name,
        )).asset
        version = service.import_url_asset_version(project_id, asset.id, AssetImportUrlRequest(
            external_url=f"https://example.test/{name}.png", media_type="IMAGE",
            descriptor=name,
        )).version
        service.review_asset_version(project_id, asset.id, version.id, AssetReviewRequest(
            decision=AssetReviewDecision.APPROVED,
            purpose=(AssetReviewPurpose.SUPPLEMENTAL_REFERENCE if kind == AssetKind.REFERENCE
                     else AssetReviewPurpose.CANONICAL_MASTER),
            notes="Usable reference image",
        ))
        return asset.id, version.id

    carrier_id, carrier_version_id = approved_image(AssetKind.PROP, "carrier")
    plate_id, plate_version_id = approved_image(AssetKind.REFERENCE, "exact-plate")
    rejected_id, rejected_version_id = approved_image(AssetKind.REFERENCE, "old-plate")
    unrelated_id, unrelated_version_id = approved_image(AssetKind.PROP, "unrelated-pack")
    for subject_id, version_id, role, tags in [
        (carrier_id, carrier_version_id, ReferenceRole.CANONICAL_MASTER, ["physical"]),
        (carrier_id, plate_version_id, ReferenceRole.PROP_STATE, ["exact-text", "CaseSensitive"]),
        (carrier_id, rejected_version_id, ReferenceRole.PROP_STATE, ["old-text"]),
        (unrelated_id, unrelated_version_id, ReferenceRole.CANONICAL_MASTER, ["unrelated"]),
    ]:
        service.add_reference_pack_entry(project_id, ReferencePackEntryCreateRequest(
            subject_asset_id=subject_id, asset_version_id=version_id, role=role,
            angle=ReferenceAngle.NOT_APPLICABLE, framing=ReferenceFraming.DETAIL,
            tags=tags, description="Actual saved entry metadata",
        ))
    service.review_asset_version(project_id, rejected_id, rejected_version_id, AssetReviewRequest(
        decision=AssetReviewDecision.REJECTED,
        purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
        notes="No longer a usable content layer",
    ))
    root = plan.items[0].work_items[0]
    root.ordered_references.append(AssetPlanReference(
        role="printed_content", purpose="Use the approved exact text layer",
        asset_id=plate_id, asset_version_id=plate_version_id,
    ))
    service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))

    reopened = _service(tmp_path, "scoped-reference-packs")
    shot_id = plan.items[0].serves_shot_ids[0]
    updated = reopened.tool_registry.invoke("shot.update_reference_plan", {
        "project_id": project_id,
        "shot_id": shot_id,
        "request": ShotReferencePlanUpdateRequest(
            max_reference_images=5, planning_notes="Current persisted reference plan",
        ).model_dump(mode="json"),
    })
    assert updated.status == ToolCallStatus.SUCCEEDED
    assert updated.output is not None
    assert set(updated.output) == {"project_id", "shot_id", "shot_revision", "reference_plan"}
    assert updated.output["shot_id"] == shot_id
    assert updated.output["reference_plan"]["max_reference_images"] == 5
    assert updated.output["reference_plan"]["planning_notes"] == "Current persisted reference plan"
    call = reopened.tool_registry.invoke("artifact.get_asset_work_context", {
        "project_id": project_id,
        "scope": {"mode": "WORK_ITEM", "work_item_ids": [root.id]},
        "shot_ids": [shot_id],
    })
    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.output is not None
    packs = call.output["reference_packs"]
    assert len(packs) == 1
    pack = packs[0]
    assert pack["subject_asset_id"] == carrier_id
    assert {entry["asset_version_id"] for entry in pack["entries"]} == {
        carrier_version_id, plate_version_id,
    }
    plate_entry = next(entry for entry in pack["entries"]
                       if entry["asset_version_id"] == plate_version_id)
    assert plate_entry["role"] == "PROP_STATE"
    assert plate_entry["tags"] == ["exact-text", "CaseSensitive"]
    assert plate_entry["angle"] == "NOT_APPLICABLE"
    assert plate_entry["framing"] == "DETAIL"
    assert unrelated_id not in json.dumps(packs)
    assert any(ref.artifact_type == "reference_pack" and ref.artifact_id == pack["id"]
               for ref in call.artifact_refs)
    shot_plans = call.output["shot_reference_plans"]
    assert len(shot_plans) == 1
    assert shot_plans[0]["shot_id"] == shot_id
    assert shot_plans[0]["max_reference_images"] == 5
    assert shot_plans[0]["planning_notes"] == "Current persisted reference plan"
    asset_only_context = reopened.get_asset_work_context(
        project_id, item_ids=[], work_item_ids=[root.id], shot_ids=[], clip_group_ids=[],
    )
    assert asset_only_context.shot_reference_plans == []
    # Filtering a read projection must not delete the saved rejected entry.
    saved_pack = next(pack for pack in reopened.get_project(project_id).document.reference_packs
                      if pack.subject_asset_id == carrier_id)
    assert len(saved_pack.entries) == 3


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


def test_asset_plan_service_aggregates_all_text_plate_contract_mismatches(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "text-plate-brief-mismatches")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    document = service.get_project(project_id).document

    def mismatched_text_plate_item(index: int) -> AssetPlanItem:
        return AssetPlanItem(
            id=f"asset-plan-text-plate-{index}",
            disposition=AssetPlanDisposition.ANCHOR,
            asset_kind=AssetKind.REFERENCE,
            name=f"上屏文字 {index}",
            brief=CoreAssetBrief(
                identity_anchor=f"第 {index} 张独立上屏文字板。",
                visual_attributes=["清晰排版"],
                presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
                required_visible_text=[f"需要文案 {index}"],
            ),
            rationale="精确文字由本地确定性排版生成。",
            work_items=[
                AssetPlanWorkItem(
                    id=f"work-text-plate-{index}",
                    operation=AssetPlanOperation.TEXT_PLATE,
                    output_role="exact_screen_text",
                    final_prompt="按照已批准文案确定性排版。",
                    size="1344x768",
                    review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                    text_plate_intent=AssetPlanTextPlateIntent(
                        background_color="#FFFFFF",
                        foreground_color="#111111",
                        exact_lines=[f"实际文案 {index}"],
                        layout_description="文字居中。",
                    ),
                )
            ],
        )

    mismatched_plan = plan.model_copy(
        update={
            "items": [
                *plan.items,
                mismatched_text_plate_item(1),
                mismatched_text_plate_item(2),
            ]
        }
    )

    with pytest.raises(WorkflowConflictError) as exc_info:
        service._validate_asset_plan(document, mismatched_plan)

    prefix = (
        "TEXT_PLATE AssetPlan work items do not match their text-plate contracts: "
    )
    message = str(exc_info.value)
    assert message.startswith(prefix)
    mismatches = json.loads(message.removeprefix(prefix))
    assert [item["item_id"] for item in mismatches] == [
        "asset-plan-text-plate-1",
        "asset-plan-text-plate-2",
    ]
    assert [item["work_item_id"] for item in mismatches] == [
        "work-text-plate-1",
        "work-text-plate-2",
    ]
    assert [item["actual_required_visible_text"] for item in mismatches] == [
        ["需要文案 1"], ["需要文案 2"],
    ]
    assert [item["rendered_visible_text"] for item in mismatches] == [
        ["实际文案 1"], ["实际文案 2"],
    ]
    for item in mismatches:
        assert item["field"] == "/text_plate_intent"
        assert "must exactly match brief.required_visible_text" in item["error"]
        assert "suggested_patches" not in item


def test_asset_plan_rejects_duplicate_text_plate_copy(tmp_path: Path) -> None:
    service = _service(tmp_path, "duplicate-text-plate-copy")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    duplicate_item = AssetPlanItem(
        id="asset-plan-duplicate-text",
        disposition=AssetPlanDisposition.ANCHOR,
        asset_kind=AssetKind.REFERENCE,
        name="重复文字板",
        brief=CoreAssetBrief(
            identity_anchor="一张独立文字板。",
            visual_attributes=["清晰排版"],
            presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
            required_visible_text=["数学天才"],
        ),
        rationale="本地确定性排版。",
        work_items=[
            AssetPlanWorkItem(
                id="work-duplicate-text",
                operation=AssetPlanOperation.TEXT_PLATE,
                output_role="exact_screen_text",
                final_prompt="按照精确文案排版。",
                size="1344x768",
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                text_plate_intent=AssetPlanTextPlateIntent(
                    background_color="#FFFFFF",
                    foreground_color="#111111",
                    title="数学天才",
                    exact_lines=["数学天才"],
                    layout_description="标题居中。",
                ),
            )
        ],
    )

    with pytest.raises(
        WorkflowConflictError,
        match="must exactly match brief.required_visible_text",
    ):
        service._validate_asset_plan(
            service.get_project(project_id).document,
            plan.model_copy(update={"items": [*plan.items, duplicate_item]}),
        )


def test_asset_plan_requires_served_shot_clip_groups(tmp_path: Path) -> None:
    service = _service(tmp_path, "served-shot-groups")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    first_item = plan.items[0].model_copy(update={"serves_clip_group_ids": []})

    with pytest.raises(WorkflowConflictError, match="omits ClipGroups for its served Shots"):
        service.create_asset_plan(
            project_id,
            AssetPlanCreateRequest(
                asset_plan=plan.model_copy(
                    update={"items": [first_item, *plan.items[1:]]}
                ),
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )


@pytest.mark.parametrize("adopt_latest_script", [False, True])
def test_local_asset_plan_patch_remains_editable_during_script_revision(
    tmp_path: Path, adopt_latest_script: bool,
) -> None:
    service = _service(tmp_path)
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard)
    created = service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    document = service.get_project(project_id).document
    script = next(value for value in document.script_versions
                  if value.id == document.project.current_script_version_id)
    line = script.lines[0]
    service.revise_script_line(project_id, ReviseScriptLineRequest(
        line_id=line.id, spoken_text=line.spoken_text + "！",
        subtitle_text=line.subtitle_text + "！", change_reason="用户修改一句对白。",
    ))
    before = service.get_project(project_id).document
    source_id = (before.project.current_script_version_id
                 if adopt_latest_script else plan.script_version_id)
    assert source_id is not None
    patched = service.patch_asset_plan(project_id, AssetPlanPatchRequest(
        base_version_id=created.version.id,
        field_patches=[
            AssetPlanFieldPatch(op=AssetPlanFieldPatchOperation.REPLACE,
                                path="/script_version_id", value=source_id),
            AssetPlanFieldPatch(item_id=plan.items[0].id,
                                op=AssetPlanFieldPatchOperation.REPLACE,
                                path="/rationale", value="只调整这一项的用途。"),
        ],
        origin=ProductionDocumentOrigin.AGENT,
    ))
    after = service.get_project(project_id).document
    assert patched.asset_plan.script_version_id == source_id
    assert patched.asset_plan.production_spec_version_id == spec.version.id
    assert patched.asset_plan.storyboard_version_id == storyboard.version.id
    assert patched.asset_plan.items[0].rationale == "只调整这一项的用途。"
    assert patched.asset_plan.items[1:] == plan.items[1:]
    assert after.script_versions == before.script_versions
    assert after.shots == before.shots
    assert after.assets == before.assets
    assert after.editorial_timeline == before.editorial_timeline
    script_refs = [ref for ref in patched.version.source_artifact_refs
                   if ref.artifact_type == "script_version"]
    assert [ref.artifact_id for ref in script_refs] == [source_id]


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


def test_explicit_draft_can_complete_plan_and_unblock_derived_work(tmp_path: Path) -> None:
    service = _service(tmp_path, "selected-draft-completion")
    project_id, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, project_id, spec=spec, storyboard=storyboard,
                       approve_sources=False)
    service.create_asset_plan(project_id, AssetPlanCreateRequest(
        asset_plan=plan, origin=ProductionDocumentOrigin.AGENT,
    ))
    reopened = _service(tmp_path, "selected-draft-completion")
    index = reopened.get_asset_plan_index(project_id)
    assert [work.status.value for work in index.items[0].work_items] == ["COMPLETED", "READY"]
    document = reopened.get_project(project_id).document
    for item in plan.items[:2]:
        asset = next(asset for asset in document.assets if asset.id == item.completed_asset_id)
        version = next(version for version in asset.versions if version.id == item.completed_asset_version_id)
        assert version.approval_status.value == "DRAFT"
