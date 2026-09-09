"""Coverage checks for the agent-callable production tool catalog."""

from __future__ import annotations

from pathlib import Path

from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus, ToolCategory, ToolEffect


REQUIRED_PRODUCTION_TOOLS = {
    "project": {
        "project.bind_output_slot",
        "project.create",
        "project.get",
        "project.get_output_slot",
        "project.list",
        "project.list_output_slots",
    },
    "audio": {
        "audio.analyze_music",
        "audio.bind_master",
        "audio.inspect_manifest",
        "audio.update_timed_text_lock",
        "audio.update_voice_reference",
    },
    "artifact": {
        "artifact.add_reference_entry",
        "artifact.create_asset",
        "artifact.create_asset_plan",
        "artifact.create_production_document",
        "artifact.create_production_spec",
        "artifact.get_production_document",
        "artifact.get_production_spec",
        "artifact.get_asset_plan",
        "artifact.get_asset_plan_index",
        "artifact.get_asset_planning_context",
        "artifact.get_asset_work_context",
        "artifact.import_catalog",
        "artifact.import_local_version",
        "artifact.import_stock",
        "artifact.import_url_version",
        "artifact.inspect_coverage",
        "artifact.list_production_documents",
        "artifact.review_version",
        "artifact.replace_production_spec",
        "artifact.replace_asset_plan",
        "artifact.patch_asset_plan",
        "artifact.reconcile_asset_plan_execution_contract",
        "artifact.retry_asset_plan_attempt",
        "artifact.revise_production_document",
        "artifact.search_catalog",
        "artifact.search_stock",
        "artifact.update_brief",
    },
    "image": {
        "image.advance",
        "image.capability",
        "image.cancel",
        "image.compose_reference_sheet",
        "image.compose_text_plate",
        "image.enqueue",
        "image.list",
        "image.recover_output",
        "image.resolve_not_submitted",
        "image.retry",
    },
    "planning": {
        "planning.advance",
        "planning.resolve_story_verification",
        "planning.start_script_revision",
    },
    "shot": {
        "shot.apply_director_repair",
        "shot.append_storyboard_group",
        "shot.append_storyboard_groups",
        "shot.append_storyboard_ledger_segment",
        "shot.append_storyboard_outline_segment",
        "shot.begin_storyboard_draft",
        "shot.compile_keyframe_brief",
        "shot.complete_storyboard_ledger",
        "shot.complete_storyboard_outline",
        "shot.create_storyboard",
        "shot.director_advice",
        "shot.finalize_storyboard",
        "shot.preview_reference_plan",
        "shot.get_storyboard",
        "shot.patch_storyboard",
        "shot.record_storyboard_draft_verification",
        "shot.replace_storyboard_draft_group",
        "shot.replace_storyboard",
        "shot.run_director_loop",
        "shot.update_reference_plan",
    },
    "video": {
        "video.compare_takes",
        "video.enqueue",
        "video.extend",
        "video.gap_fill",
        "video.inspect_take",
        "video.get_take_inspection",
        "video.retake",
        "video.select_take",
    },
    "timeline": {"timeline.export", "timeline.rebuild"},
    "task": {
        "task.advance",
        "task.cancel",
        "task.drain",
        "task.reconcile",
        "task.recover_output",
        "task.retry",
    },
    "qa": {
        "qa.analyze_source_image",
        "qa.check_input_compatibility",
        "qa.compare_keyframes",
        "qa.inspect_asset_version",
        "qa.list_visual_input_reports",
        "qa.rerank_reference_visuals",
        "qa.review_episode",
    },
    "route": {
        "route.compile_generation_plan",
        "route.compile_h3_prompt",
        "route.enqueue_h3_probe",
        "route.execute_h3_plan",
        "route.h3_capability_profile",
        "route.get_generation_plan",
        "route.list_h3_plans",
        "route.list_h3_reconciliation",
        "route.list_h3_runs",
        "route.get_h3_run",
        "route.plan_h3",
        "route.preview_h3_probe",
        "route.resolve_h3_not_submitted",
        "route.resolve_h3_submission",
        "route.review_h3_take",
        "route.update_h3_asset_preparation",
    },
}


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def test_default_registry_covers_the_existing_production_loop(tmp_path: Path) -> None:
    service = _service(tmp_path)
    descriptors = {item.id: item for item in service.tool_registry.list()}

    for category, expected_ids in REQUIRED_PRODUCTION_TOOLS.items():
        assert expected_ids <= descriptors.keys()
        assert {
            descriptor.id
            for descriptor in service.tool_registry.list(
                category=ToolCategory(category)
            )
        } >= expected_ids

    assert "change.preview" in descriptors
    assert "change.apply" in descriptors
    assert "agent.delegate" not in descriptors

    for descriptor in descriptors.values():
        assert descriptor.input_schema["type"] == "object"
        assert descriptor.output_schema["type"] == "object"

    reconciliation = descriptors["route.list_h3_reconciliation"]
    assert reconciliation.version == 2
    assert reconciliation.effect == ToolEffect.EXTERNAL
    assert reconciliation.idempotent is True
    assert reconciliation.input_schema["required"] == ["project_id", "task_id"]

    image_enqueue = descriptors["image.enqueue"]
    request_ref = image_enqueue.input_schema["properties"]["request"]["$ref"]
    request_name = request_ref.rsplit("/", maxsplit=1)[-1]
    request_schema = image_enqueue.input_schema["$defs"][request_name]
    assert "reference_inputs" in request_schema["properties"]
    assert "input_version_ids" not in request_schema["properties"]
    reference_ref = request_schema["properties"]["reference_inputs"]["items"][
        "$ref"
    ]
    reference_name = reference_ref.rsplit("/", maxsplit=1)[-1]
    reference_schema = image_enqueue.input_schema["$defs"][reference_name]
    assert "asset_version_id" in reference_schema["properties"]
    assert "version_id" not in reference_schema["properties"]

    assert "ProjectBible.style" in descriptors["artifact.create_asset"].description
    assert "需要锚点" in descriptors["artifact.create_asset_plan"].description
    assert "仅保留文字" in descriptors["artifact.create_asset_plan"].description
    assert descriptors["artifact.create_asset_plan"].version == 2
    assert "不回传完整 AssetPlan" in descriptors[
        "artifact.create_asset_plan"
    ].description
    assert "asset_plan" not in descriptors[
        "artifact.create_asset_plan"
    ].output_schema["properties"]
    assert "共享一份单件干净物理母版" in descriptors[
        "artifact.create_asset_plan"
    ].description
    assert "调整少数资产或整体计划" in descriptors[
        "artifact.replace_asset_plan"
    ].description
    assert "四视图、轻量参考等创意路由" in descriptors[
        "artifact.replace_asset_plan"
    ].description
    assert "局部补全未提交工作" in descriptors[
        "artifact.replace_asset_plan"
    ].description
    assert descriptors["artifact.replace_asset_plan"].version == 2
    assert "不回传完整" in descriptors[
        "artifact.replace_asset_plan"
    ].description
    assert "asset_plan" not in descriptors[
        "artifact.replace_asset_plan"
    ].output_schema["properties"]
    assert "局部语义修改" in descriptors[
        "artifact.patch_asset_plan"
    ].description
    assert "依赖、当前上游版本" in descriptors[
        "artifact.patch_asset_plan"
    ].description
    assert descriptors["artifact.patch_asset_plan"].version == 2
    assert "不回传完整 AssetPlan" in descriptors[
        "artifact.patch_asset_plan"
    ].description
    assert "asset_plan" not in descriptors[
        "artifact.patch_asset_plan"
    ].output_schema["properties"]
    reconcile = descriptors["artifact.reconcile_asset_plan_execution_contract"]
    assert reconcile.version == 2
    assert "Agent 已经做出的资产执行语义决定" in reconcile.description
    assert "不调用图片 Provider" in reconcile.description
    assert "execution_overrides" in reconcile.description
    assert "成功摘要来自实际" in reconcile.description
    assert "applied_execution_overrides" in reconcile.output_schema["properties"]
    assert "asset_plan" not in reconcile.output_schema["properties"]
    assert "不携带完整文档 payload" in descriptors[
        "artifact.get_asset_work_context"
    ].description
    assert "不返回 brief、final prompt、有序参考" in descriptors[
        "artifact.get_asset_plan_index"
    ].description
    assert "去重后的当前制片真相" in descriptors[
        "artifact.get_asset_planning_context"
    ].description
    assert "不附完整计划" in descriptors[
        "artifact.get_asset_planning_context"
    ].description
    assert "Leader 应先用本索引" in descriptors[
        "artifact.get_asset_plan_index"
    ].description
    assert "后续镜头输入编排" in descriptors[
        "artifact.get_asset_plan"
    ].description
    assert "不会自动看图" in descriptors["artifact.review_version"].description
    assert "不把一图生成卡的近似分区" in descriptors[
        "artifact.review_version"
    ].description
    assert "轻微尺寸或透视差" in descriptors[
        "artifact.review_version"
    ].description
    assert "artifact.review_version" in descriptors["qa.inspect_asset_version"].description
    assert "不应单独成为拒绝理由" in descriptors[
        "qa.inspect_asset_version"
    ].description
    assert "像素级分区" in descriptors[
        "image.compose_reference_sheet"
    ].description
    assert "布局 guide 必须作为真实参考" in descriptors[
        "image.enqueue"
    ].description
    assert "不用无参考GENERATE 冒充“保持原人物”" in descriptors[
        "image.enqueue"
    ].description
    assert "不从 LEFT/RIGHT 枚举名自动推导" in descriptors[
        "qa.inspect_asset_version"
    ].description
    assert "能独立裁用的单件" in descriptors[
        "qa.inspect_asset_version"
    ].description
    assert "结构、材质、组件或可裁用性" in descriptors[
        "qa.inspect_asset_version"
    ].description
    assert "不要求 Provider 一次生成多件像素级一致" in descriptors[
        "image.enqueue"
    ].description
    assert "不同文案分别保存为独立 TEXT_PLATE" in descriptors[
        "image.compose_text_plate"
    ].description
    assert "再调用 route.plan_h3" in descriptors[
        "route.update_h3_asset_preparation"
    ].description
    assert "不会触发任何真实生成" in descriptors[
        "artifact.create_production_spec"
    ].description
    for tool_id in (
        "artifact.create_production_spec",
        "artifact.replace_production_spec",
        "artifact.get_production_spec",
        "artifact.create_production_document",
        "artifact.revise_production_document",
        "artifact.get_production_document",
        "artifact.list_production_documents",
        "artifact.get_asset_plan",
        "shot.create_storyboard",
        "shot.replace_storyboard",
        "shot.patch_storyboard",
        "shot.finalize_storyboard",
        "shot.get_storyboard",
    ):
        assert descriptors[tool_id].version == 2
    for tool_id in (
        "artifact.create_production_spec",
        "artifact.replace_production_spec",
        "artifact.create_production_document",
        "artifact.revise_production_document",
        "artifact.list_production_documents",
        "shot.create_storyboard",
        "shot.replace_storyboard",
        "shot.patch_storyboard",
        "shot.finalize_storyboard",
    ):
        schema = descriptors[tool_id].output_schema
        assert "versions" not in schema.get("properties", {})
        assert all(
            "versions" not in definition.get("properties", {})
            for definition in schema.get("$defs", {}).values()
        )
    assert "不会生成视频" in descriptors["shot.create_storyboard"].description
    assert "轻量 seed" in descriptors[
        "shot.begin_storyboard_draft"
    ].description
    begin_schema = descriptors["shot.begin_storyboard_draft"].input_schema
    begin_request_schema = begin_schema["$defs"]["StoryboardDraftBeginRequest"]
    assert "seed" in begin_request_schema["properties"]
    assert "draft" not in begin_request_schema["properties"]
    assert "不设固定秒数" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    assert "不是 Scene、ClipGroup、Shot 或 ComfyUI 节点边界" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    assert "completes_scene 默认为 false" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    assert "完整分区为各自精确 4–15 秒整数" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    assert "不得修改台词内容" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    assert "不得用目标总时长倒推分配" in descriptors[
        "shot.append_storyboard_ledger_segment"
    ].description
    ledger_schema = descriptors[
        "shot.append_storyboard_ledger_segment"
    ].input_schema["$defs"]["StoryboardTimingLedgerEntry"]
    assert ledger_schema["properties"]["completes_scene"] == {
        "default": False,
        "title": "Completes Scene",
        "type": "boolean",
    }
    assert "不是用户审批门" in descriptors[
        "shot.complete_storyboard_ledger"
    ].description
    assert "每场 allocations 能按连续顺序完整分区" in descriptors[
        "shot.complete_storyboard_ledger"
    ].description
    assert "不改台词，不用目标总时长倒推" in descriptors[
        "shot.complete_storyboard_ledger"
    ].description
    assert "同一 WorkOrder 可连续追加多段" in descriptors[
        "shot.append_storyboard_outline_segment"
    ].description
    assert "4–15 秒整数" in descriptors[
        "shot.append_storyboard_outline_segment"
    ].description
    outline_schema = descriptors[
        "shot.append_storyboard_outline_segment"
    ].input_schema["$defs"]["StoryboardClipGroupOutline"]
    assert outline_schema["properties"]["duration_seconds"] == {
        "maximum": 15,
        "minimum": 4,
        "title": "Duration Seconds",
        "type": "integer",
    }
    assert "不会强制固定分组数" in descriptors[
        "shot.complete_storyboard_outline"
    ].description
    assert "WorkOrder 可以小于或大于场次" in descriptors[
        "shot.append_storyboard_group"
    ].description
    assert "Shot 时长之和必须等于组时长" in descriptors[
        "shot.append_storyboard_group"
    ].description
    assert "不要为通过字符串逐字相等而复制措辞" in descriptors[
        "shot.append_storyboard_group"
    ].description
    assert "服务端先整体校验" in descriptors[
        "shot.append_storyboard_groups"
    ].description
    assert "按组逐个创建" in descriptors[
        "shot.append_storyboard_groups"
    ].description
    # Authored timing supports decimals; provider request limits belong to its adapter.
    group_schema = descriptors["shot.append_storyboard_group"].input_schema
    assert group_schema["$defs"]["ClipGroup"]["properties"][
        "duration_seconds"
    ]["type"] == "number"
    assert group_schema["$defs"]["ShotPlan"]["properties"][
        "duration_seconds"
    ]["type"] == "number"
    segment_schema = descriptors["shot.append_storyboard_groups"].input_schema
    assert segment_schema["$defs"]["StoryboardDraftGroupsAppendRequest"][
        "properties"
    ]["groups"]["minItems"] == 1
    assert "maxItems" not in segment_schema["$defs"][
        "StoryboardDraftGroupsAppendRequest"
    ]["properties"]["groups"]
    assert "旧 Verifier 结论会自动失效" in descriptors[
        "shot.replace_storyboard_draft_group"
    ].description
    assert "不是用户审批门" in descriptors[
        "shot.record_storyboard_draft_verification"
    ].description
    assert "canonical Storyboard" in descriptors[
        "shot.finalize_storyboard"
    ].description
    assert "不做逐字等值匹配" in descriptors[
        "shot.finalize_storyboard"
    ].description
    assert "兼容旧调用" in descriptors["shot.create_storyboard"].description
    assert descriptors["route.compile_generation_plan"].effect == ToolEffect.WRITE
    assert "不调用 H3" in descriptors[
        "route.compile_generation_plan"
    ].description


def test_read_adapters_use_the_real_service_without_bypassing_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id

    coverage = service.tool_registry.invoke(
        "artifact.inspect_coverage",
        {"project_id": project_id},
    )
    assert coverage.status == ToolCallStatus.SUCCEEDED
    assert coverage.output is not None
    assert coverage.output["project_id"] == project_id

    image_tasks = service.tool_registry.invoke(
        "image.list",
        {"project_id": project_id},
    )
    assert image_tasks.status == ToolCallStatus.SUCCEEDED
    assert image_tasks.output == {"tasks": []}

    route_plans = service.tool_registry.invoke(
        "route.list_h3_plans",
        {"project_id": project_id},
    )
    assert route_plans.status == ToolCallStatus.SUCCEEDED
    assert route_plans.output == {"plans": []}

    visual_reports = service.tool_registry.invoke(
        "qa.list_visual_input_reports",
        {"project_id": project_id},
    )
    assert visual_reports.status == ToolCallStatus.SUCCEEDED
    assert visual_reports.output == {
        "reference_reranks": [],
        "keyframe_comparisons": [],
        "compatibility_checks": [],
    }
