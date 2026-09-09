"""Black-box validation for an Agent-owned core-asset production stage.

The probe is deliberately narrower than an asset integration test.  It may
create and advance an ``AgentRun`` and may read the resulting project, board,
work orders, and versioned AssetPlan.  It never invokes an asset, image,
reference-pack, shot-reference, video, audio, timeline, or export endpoint
itself.  All production mutations therefore have to originate from the Agent's
own Tool calls.

``execution_target=MOCK`` currently selects deterministic video executors; it
does not install a fake image provider.  The result consequently reports two
separate closures:

* plan closure: a source-locked AssetPlan with stable work items and explicit
  anchor/text-only/reuse decisions exists;
* media closure: the planned anchors also have approved image versions,
  reference packs, and shot reference bindings.

This separation keeps offline runs honest while allowing a configured local or
fake image provider to satisfy the stronger closure without changing the
probe.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
from time import monotonic
from typing import Sequence, cast
from urllib.parse import quote

from validate_agent_storyboard_run import (
    AgentStoryboardApiClient,
    ApiHttpError,
    HttpResult,
    JsonObject,
    JsonValue,
    ProbeFailure,
    RawResponseRecorder,
    _as_list,
    _as_object,
    _board_work_order_ids,
    _collect_specialist_tool_events,
    _json_strings,
    _progress_fingerprint,
    _resumable_agent_conflict,
    _run_object,
    _run_status,
    _running_actions,
    _seen_action_ids,
    _seen_specialist_turn_ids,
    _seen_work_order_keys,
    _string,
    _trace_phase,
    _trace_tool_names,
    _utc_now,
    _waiting_blocker,
)


HttpRequestRecord = tuple[str, str]

DEFAULT_PROJECT_ID = "project-11b4f7a637c3"
DEFAULT_OBJECTIVE = """
以当前 ProjectDocument 中已确认的 ScriptVersion、ProjectBibleVersion、
ProductionSpec 和 canonical Storyboard 为唯一内容来源，由 Agent 独立
完成本项目的核心资产阶段，然后立即停止。不改写剧本、Bible、
ProductionSpec 或分镜，不请求用户确认。

先通读完整剧本、Bible、ProductionSpec 和 Storyboard，调用版本化
AssetPlan 能力建立 `asset.plan.current`。AssetPlan 必须记录每个候选资产
是生成视觉锚点、复用已有精确版本，还是仅在分镜和 Prompt 中保留
文字；说清判断理由、服务哪些 Shot/ClipGroup、结构化 brief、稳定
work-item ID、最终可执行 Prompt、有序参考和真实依赖。这是 Agent 的
语义取舍，不使用关键词路由、固定资产数量、审批门或重复 Critic 循环。

先调用 `image.capability`，并据当前实现重新检查已有 AssetPlan 是否仍可执行。
Provider 图像尺寸必须采用 capability 返回的合法值；16:9 角色卡和场景卡可用
`1536x864` 或 `2048x1152`，不要继续使用不满足 16 像素对齐的
`1920x1080`。如果当前 AssetPlan 的 operation、尺寸、精确文字处理或参考职责
已经过时，由 ASSET_PLANNER 读取当前精确计划并做语义判断：跨多个 item 同步
canonical、supplemental、lineage、权威参考，或拆分物理载体与文字图版时，使用
`artifact.reconcile_asset_plan_execution_contract` 一次提交语义决定，由 Tool 编译
联动合同；其中 work-item 的尺寸或输出布局变更必须写入结构化
`execution_overrides`，不得只写进 notes。Tool 成功后以返回的
`applied_execution_overrides` 为实际落盘事实。只有盘点、顺序或依赖网络整体变化时
才使用完整替换。不在执行时偷偷修参数，也不使用低层字段 patch 手写大规模迁移。

对当前校园食堂短剧，结合已装载的 MiniMax Design
drama-series 方法自主判断资产深度。如果主要角色确实需要跨镜
身份锁定，可选 16:9 双区角色卡；如果食堂的多个机位确实需要
空间连续性，可选 16:9 2x2 四向环境卡。也可根据已有媒体、
镜头职责和复用范围做出其他取舍，但必须在 AssetPlan 中说明理由。
道具、群像和一次性元素同样由 Agent 结合复用、特写、状态变化和
连续性需求判断，不为了满足预设数量而制卡或 text-only。

项目若存在用户 source intake，从 `artifact.get_asset_planning_context` 或精确的
`artifact.get_asset_work_context` 取得其当前/已批准 AssetVersion，不再叠加
`project.get` 的完整项目聚合。
当实际画面覆盖需要判断时调用 `qa.analyze_source_image`；把从用户原话和完整
项目语义确定的目标资产、reference role/authority、用途和可选 layout 作为
结构化 assignment 传入。用户明确指定的人物照片是权威身份来源；若只覆盖
单脸或单角度，使用 `REFERENCE_GENERATE` 与高保真 identity reference 派生
标准卡，不得换人；若图片本身已是满足当前需求的完整角色卡，使用 `IMPORT`
精确复用，不为走流程重绘。未分配图片可以保持未使用，不能自动绑定全片。

菜价、姓名、金额、标题、协议条款、通知书正文等精确可见文字不要交给图像
Provider 碰运气。需要精确平面内容时，将其计划为带
`required_visible_text` 的 `SCREEN_CONTENT_PLATE`，用 `TEXT_PLATE` work item
和 `image.compose_text_plate` 确定性生成；物理外壳或纸张需要单独母版时再建
`PHYSICAL_MASTER`。不要让一张道具图同时承担互不兼容的封面、内页和签名状态。

若当前运行环境已配置可用的图像 Provider 或本地假执行器，继续按
AssetPlan 执行就绪的 work items：建立/更新资产和 brief，生成、导入或
组合图像，对精确版本进行语义审核，批准可用的角色和场景锚点，
建立 ReferencePack，并仅为确实需要的 Shot 更新 reference plan。
一个资产可保留多张有意义的参考，但每个后续 Provider 调用只选择最小
充分子集；每张参考都说清 role/purpose，不把丰富的 ReferencePack 等同于
全部塞给模型。Base-first 衍生项等待真实依赖，相互独立的角色、
场景或道具可并行。

如果 MOCK 执行目标不提供图像生成能力，不得伪造图片、审核、
ReferencePack 或 Shot 绑定，也不得调用视频 MOCK 冒充资产。在 AssetPlan
中保留未完成 work item 和阻塞原因，完成“计划闭环”后停止，由黑盒
探针如实报告媒体闭环未成立。

不编译 GenerationPlan，不生成视频或音频，不建立 Provider 视频 Task/Take，
不建立时间线，不导出媒体。版本化 AssetPlan 已完成，且在环境允许时
资产媒体也已闭环后，立即结束 AgentRun。
""".strip()


@dataclass(frozen=True)
class AssetClosureAssessment:
    """Structural validation result with an honest media-closure distinction."""

    errors: list[str]
    media_closed: bool
    media_gaps: list[str]
    anchor_item_ids: list[str]
    completed_anchor_item_ids: list[str]


class AgentAssetApiClient(AgentStoryboardApiClient):
    """Public Agent API client with no direct production mutation methods."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float | None,
        recorder: RawResponseRecorder,
    ) -> None:
        self._request_records: list[HttpRequestRecord] = []
        super().__init__(
            base_url,
            timeout_seconds=timeout_seconds,
            recorder=recorder,
        )

    def create_agent_run(self, project_id: str, objective: str) -> JsonObject:
        return self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/agent-runs",
            label="create_agent_asset_run",
            payload={
                "objective": objective,
                "auto_plan": True,
                "unattended": True,
                "execution_target": "MOCK",
                "render_export": False,
            },
        ).body

    def advance_agent_run(self, run_id: str, max_actions: int) -> JsonObject:
        return self._request(
            "POST",
            f"/agent-runs/{quote(run_id, safe='')}/advance",
            label="advance_agent_asset_run",
            payload={"max_actions": max_actions},
        ).body

    def _request(
        self,
        method: str,
        path: str,
        *,
        label: str,
        payload: JsonObject | None,
    ) -> HttpResult:
        self._request_records.append((method, path))
        return super()._request(method, path, label=label, payload=payload)

    def request_records(self) -> list[HttpRequestRecord]:
        return list(self._request_records)


def _project_document(project: JsonObject) -> JsonObject:
    return _as_object(project.get("document"), "project.document")


def _project_record(project: JsonObject) -> JsonObject:
    return _as_object(_project_document(project).get("project"), "project record")


def _identity_list(value: JsonValue | None, label: str) -> list[str]:
    result: list[str] = []
    for item_value in _as_list(value, label):
        item = _as_object(item_value, f"{label} item")
        result.append(_string(item.get("id"), f"{label} item id"))
    return result


def _baseline(project: JsonObject) -> JsonObject:
    """Capture story truth and downstream media state the asset stage may not alter."""

    document = _project_document(project)
    record = _project_record(project)
    return {
        "current_script_version_id": record.get("current_script_version_id"),
        "current_bible_version_id": record.get("current_bible_version_id"),
        "production_spec_document_id": document.get("production_spec_document_id"),
        "storyboard_document_id": document.get("storyboard_document_id"),
        "generation_plan_document_id": document.get("generation_plan_document_id"),
        "audio_structure_document_id": document.get("audio_structure_document_id"),
        "master_audio_document_id": document.get("master_audio_document_id"),
        "timed_text_document_id": document.get("timed_text_document_id"),
        "current_manifest_id": record.get("current_manifest_id"),
        "task_ids": _json_strings(_identity_list(project.get("tasks"), "tasks")),
        "take_ids": _json_strings(_identity_list(project.get("takes"), "takes")),
        "export_ids": _json_strings(_identity_list(project.get("exports"), "exports")),
        "manifest_ids": _json_strings(
            _identity_list(document.get("manifests"), "manifests")
        ),
    }


def _production_document_version_id(
    project: JsonObject,
    document_id: JsonValue | None,
    expected_kind: str,
) -> str | None:
    if not isinstance(document_id, str) or not document_id:
        return None
    for value in _as_list(
        _project_document(project).get("production_documents"),
        "production_documents",
    ):
        document = _as_object(value, "production document")
        if document.get("id") != document_id or document.get("kind") != expected_kind:
            continue
        current_version_id = document.get("current_version_id")
        return current_version_id if isinstance(current_version_id, str) else None
    return None


def _asset_plan_payload(response: JsonObject) -> JsonObject:
    """Accept the managed AssetPlan response or its generic document read shape."""

    direct = response.get("asset_plan")
    if isinstance(direct, dict):
        return direct
    version = _as_object(response.get("version"), "asset plan version")
    return _as_object(version.get("payload"), "asset plan version payload")


def _asset_plan_version(response: JsonObject) -> JsonObject:
    return _as_object(response.get("version"), "asset plan version")


def _asset_plan_document(response: JsonObject) -> JsonObject:
    return _as_object(response.get("document"), "asset plan document")


def _asset_by_id(document: JsonObject) -> dict[str, JsonObject]:
    return {
        _string(asset.get("id"), "asset id"): asset
        for value in _as_list(document.get("assets"), "assets")
        for asset in [_as_object(value, "asset")]
    }


def _asset_version(asset: JsonObject, version_id: str) -> JsonObject | None:
    for value in _as_list(asset.get("versions"), "asset versions"):
        version = _as_object(value, "asset version")
        if version.get("id") == version_id:
            return version
    return None


def _approved_image_version_ids(document: JsonObject) -> set[str]:
    result: set[str] = set()
    for asset_value in _as_list(document.get("assets"), "assets"):
        asset = _as_object(asset_value, "asset")
        for version_value in _as_list(asset.get("versions"), "asset versions"):
            version = _as_object(version_value, "asset version")
            version_id = version.get("id")
            if (
                isinstance(version_id, str)
                and version.get("media_type") == "IMAGE"
                and version.get("approval_status") == "APPROVED"
                and version.get("freshness") == "CURRENT"
            ):
                result.add(version_id)
    return result


def _reference_pack_subjects(document: JsonObject) -> set[str]:
    """Include approved member assets, such as text plates in a prop's pack."""

    result: set[str] = set()
    assets = _asset_by_id(document)
    version_owners = {
        _string(version.get("id"), "asset version id"): asset_id
        for asset_id, asset in assets.items()
        for version_value in _as_list(asset.get("versions"), "asset versions")
        for version in [_as_object(version_value, "asset version")]
    }
    approved_version_ids = _approved_image_version_ids(document)
    for value in _as_list(document.get("reference_packs"), "reference_packs"):
        pack = _as_object(value, "reference pack")
        subject_id = pack.get("subject_asset_id")
        if not isinstance(subject_id, str) or subject_id not in assets:
            continue
        canonical_id = assets[subject_id].get("approved_version_id")
        entries = [
            _as_object(entry_value, "reference pack entry")
            for entry_value in _as_list(
                pack.get("entries"),
                "reference pack entries",
            )
        ]
        for entry in entries:
            version_id = entry.get("asset_version_id")
            if not isinstance(version_id, str) or version_id not in approved_version_ids:
                continue
            lineage_root = entry.get("lineage_root_version_id")
            if lineage_root is not None and lineage_root != canonical_id:
                continue
            result.add(subject_id)
            owner_id = version_owners.get(version_id)
            if owner_id is not None:
                result.add(owner_id)
    return result


def _resolved_shot_reference_subjects(document: JsonObject) -> dict[str, set[str]]:
    """Resolve exact bindings and satisfiable pack requirements to Asset IDs."""

    result: dict[str, set[str]] = {}
    assets = _asset_by_id(document)
    version_owners = {
        _string(version.get("id"), "asset version id"): asset_id
        for asset_id, asset in assets.items()
        for version_value in _as_list(asset.get("versions"), "asset versions")
        for version in [_as_object(version_value, "asset version")]
    }
    approved_version_ids = _approved_image_version_ids(document)
    valid_entries: dict[str, list[JsonObject]] = {}
    for pack_value in _as_list(document.get("reference_packs"), "reference_packs"):
        pack = _as_object(pack_value, "reference pack")
        subject_id = pack.get("subject_asset_id")
        if not isinstance(subject_id, str) or subject_id not in assets:
            continue
        canonical_id = assets[subject_id].get("approved_version_id")
        for entry_value in _as_list(pack.get("entries"), "reference pack entries"):
            entry = _as_object(entry_value, "reference pack entry")
            if entry.get("asset_version_id") not in approved_version_ids:
                continue
            lineage_root = entry.get("lineage_root_version_id")
            if lineage_root is not None and lineage_root != canonical_id:
                continue
            valid_entries.setdefault(subject_id, []).append(entry)

    for value in _as_list(document.get("reference_plan"), "reference_plan"):
        plan = _as_object(value, "reference plan")
        shot_id = plan.get("shot_id")
        if not isinstance(shot_id, str) or not shot_id:
            continue
        subjects = result.setdefault(shot_id, set())
        for binding_value in _as_list(plan.get("bindings"), "reference bindings"):
            binding = _as_object(binding_value, "reference binding")
            target_id = binding.get("target_id")
            if isinstance(target_id, str) and target_id in approved_version_ids:
                owner = version_owners.get(target_id)
                if owner is not None:
                    subjects.add(owner)
        for requirement_value in _as_list(
            plan.get("requirements"),
            "reference requirements",
        ):
            requirement = _as_object(requirement_value, "reference requirement")
            subject_id = requirement.get("subject_asset_id")
            if not isinstance(subject_id, str) or not subject_id:
                continue
            acceptable_roles = {
                str(item)
                for item in _as_list(
                    requirement.get("acceptable_roles"),
                    "acceptable_roles",
                )
            }
            required_tags = {
                str(item)
                for item in _as_list(requirement.get("required_tags"), "required_tags")
            }
            # Angle and framing are ranking preferences in ReferencePlanner,
            # not eligibility filters. Role and required tags must still match.
            for entry in valid_entries.get(subject_id, []):
                if not (
                    entry.get("role") in acceptable_roles
                    and required_tags.issubset(
                        {
                            str(item)
                            for item in _as_list(entry.get("tags"), "reference tags")
                        }
                    )
                ):
                    continue
                subjects.add(subject_id)
                version_id = entry.get("asset_version_id")
                if isinstance(version_id, str):
                    owner_id = version_owners.get(version_id)
                    if owner_id is not None:
                        subjects.add(owner_id)
    return result


def _output_slot(document: JsonObject, slot_id: str) -> JsonObject | None:
    for value in _as_list(document.get("output_slots"), "output_slots"):
        slot = _as_object(value, "output slot")
        if slot.get("id") == slot_id:
            return slot
    return None


def _completed_asset_pair(item: JsonObject) -> tuple[str, str] | None:
    asset_id = item.get("completed_asset_id")
    version_id = item.get("completed_asset_version_id")
    if isinstance(asset_id, str) and isinstance(version_id, str):
        return asset_id, version_id
    completed_work: list[tuple[str, str]] = []
    for value in _as_list(item.get("work_items"), "asset plan work items"):
        work = _as_object(value, "asset plan work item")
        work_asset_id = work.get("completed_asset_id")
        work_version_id = work.get("completed_asset_version_id")
        if isinstance(work_asset_id, str) and isinstance(work_version_id, str):
            completed_work.append((work_asset_id, work_version_id))
    return completed_work[-1] if completed_work else None


def _collection_ids(project: JsonObject, field: str) -> list[str]:
    if field == "manifests":
        return _identity_list(_project_document(project).get(field), field)
    return _identity_list(project.get(field), field)


def _validate_asset_stage_result(
    *,
    run_view: JsonObject,
    before: JsonObject,
    after: JsonObject,
    asset_plan_response: JsonObject,
    trace: JsonObject,
) -> AssetClosureAssessment:
    """Validate Agent-authored plan closure, then independently assess media closure."""

    errors: list[str] = []
    media_gaps: list[str] = []
    if _run_status(run_view) != "COMPLETED":
        errors.append("AgentRun did not complete")

    baseline = _baseline(before)
    after_document = _project_document(after)
    after_record = _project_record(after)
    for field in (
        "current_script_version_id",
        "current_bible_version_id",
        "production_spec_document_id",
        "storyboard_document_id",
        "generation_plan_document_id",
        "audio_structure_document_id",
        "master_audio_document_id",
        "timed_text_document_id",
        "current_manifest_id",
    ):
        owner = after_record if field.startswith("current_") else after_document
        if owner.get(field) != baseline.get(field):
            errors.append(f"Asset-only run changed {field}")
    for field in ("tasks", "takes", "exports", "manifests"):
        if _collection_ids(after, field) != [
            str(value) for value in _as_list(baseline.get(f"{field[:-1]}_ids"), field)
        ]:
            errors.append(f"Asset-only run changed {field}")

    document_id = after_document.get("asset_plan_document_id")
    plan_document = _asset_plan_document(asset_plan_response)
    plan_version = _asset_plan_version(asset_plan_response)
    plan = _asset_plan_payload(asset_plan_response)
    if not isinstance(document_id, str) or not document_id:
        errors.append("Project has no asset_plan_document_id")
    elif plan_document.get("id") != document_id:
        errors.append("AssetPlan response does not match project.asset_plan_document_id")
    if plan_document.get("kind") != "asset.plan":
        errors.append("AssetPlan production document kind is not asset.plan")

    slot = _output_slot(after_document, "asset.plan.current")
    if slot is None:
        errors.append("Project has no asset.plan.current output slot")
    else:
        current_ref = slot.get("current_artifact_ref")
        if not isinstance(current_ref, dict):
            errors.append("asset.plan.current has no current artifact reference")
        else:
            if current_ref.get("artifact_id") != plan_document.get("id"):
                errors.append("asset.plan.current points at another document")
            expected_version = plan_version.get("version")
            if current_ref.get("version") != expected_version:
                errors.append("asset.plan.current points at another plan version")

    production_spec_version_id = _production_document_version_id(
        before,
        baseline.get("production_spec_document_id"),
        "production.spec",
    )
    storyboard_version_id = _production_document_version_id(
        before,
        baseline.get("storyboard_document_id"),
        "storyboard",
    )
    expected_source_values = {
        "script_version": baseline.get("current_script_version_id"),
        "bible_version": baseline.get("current_bible_version_id"),
        "production_spec": baseline.get("production_spec_document_id"),
        "storyboard": baseline.get("storyboard_document_id"),
    }
    source_refs = {
        ref.get("artifact_type"): ref
        for value in _as_list(
            plan_version.get("source_artifact_refs"),
            "asset plan source refs",
        )
        for ref in [_as_object(value, "asset plan source ref")]
    }
    for artifact_type, expected_id in expected_source_values.items():
        ref = source_refs.get(artifact_type)
        if ref is None:
            errors.append(f"AssetPlan is missing {artifact_type} source reference")
        elif expected_id is not None and ref.get("artifact_id") != expected_id:
            errors.append(f"AssetPlan {artifact_type} source is not the exact current version")

    plan_identity_fields = {
        "project_id": after_record.get("id"),
        "script_version_id": baseline.get("current_script_version_id"),
        "bible_version_id": baseline.get("current_bible_version_id"),
        "production_spec_document_id": baseline.get("production_spec_document_id"),
        "production_spec_version_id": production_spec_version_id,
        "storyboard_document_id": baseline.get("storyboard_document_id"),
        "storyboard_version_id": storyboard_version_id,
    }
    for field, expected in plan_identity_fields.items():
        if expected is not None and plan.get(field) != expected:
            errors.append(f"AssetPlan {field} does not match current source truth")

    items = [
        _as_object(value, "asset plan item")
        for value in _as_list(plan.get("items"), "asset plan items")
    ]
    item_ids = [item.get("id") for item in items]
    if not items:
        errors.append("AssetPlan has no items")
    if any(not isinstance(value, str) or not value for value in item_ids):
        errors.append("Every AssetPlan item needs a stable non-empty ID")
    if len(item_ids) != len(set(str(value) for value in item_ids)):
        errors.append("AssetPlan item IDs are not unique")

    anchors = [item for item in items if item.get("disposition") == "ANCHOR"]
    work_items: list[JsonObject] = []
    for item in items:
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"AssetPlan item {item.get('id')} has no rationale")
        item_work = [
            _as_object(value, "asset plan work item")
            for value in _as_list(item.get("work_items"), "asset plan work items")
        ]
        disposition = item.get("disposition")
        if disposition == "ANCHOR":
            if not isinstance(item.get("brief"), dict):
                errors.append(f"Anchor item {item.get('id')} has no structured brief")
            if not item_work:
                errors.append(f"Anchor item {item.get('id')} has no executable work item")
        elif disposition == "TEXT_ONLY" and (
            item_work or _completed_asset_pair(item) is not None
        ):
            errors.append(f"Text-only item {item.get('id')} contains media work")
        elif disposition == "REUSE_EXISTING" and _completed_asset_pair(item) is None:
            errors.append(f"Reuse item {item.get('id')} has no exact completed asset")
        work_items.extend(item_work)
    work_ids = [work.get("id") for work in work_items]
    known_work_ids = {
        value for value in work_ids if isinstance(value, str) and value.strip()
    }
    if len(work_ids) != len(known_work_ids):
        errors.append("AssetPlan work-item IDs must be non-empty and globally unique")
    preceding: set[str] = set()
    for work in work_items:
        work_id = work.get("id")
        prompt = work.get("final_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"AssetPlan work item {work_id} has no final_prompt")
        dependencies = [
            str(value)
            for value in _as_list(work.get("dependency_ids"), "dependency_ids")
        ]
        for dependency in dependencies:
            if dependency not in known_work_ids:
                errors.append(f"Work item {work_id} has unknown dependency {dependency}")
            elif dependency not in preceding:
                errors.append(f"Work item {work_id} has a forward dependency {dependency}")
        for reference_value in _as_list(
            work.get("ordered_references"),
            "ordered_references",
        ):
            reference = _as_object(reference_value, "asset plan reference")
            if not isinstance(reference.get("role"), str) or not isinstance(
                reference.get("purpose"), str
            ):
                errors.append(f"Work item {work_id} has an untyped ordered reference")
            source_work_id = reference.get("source_work_item_id")
            if isinstance(source_work_id, str) and source_work_id not in dependencies:
                errors.append(
                    f"Work item {work_id} references {source_work_id} without depending on it"
                )
        if isinstance(work_id, str) and work_id:
            preceding.add(work_id)

    assets = _asset_by_id(after_document)
    pack_subjects = _reference_pack_subjects(after_document)
    shot_subjects = _resolved_shot_reference_subjects(after_document)
    anchor_item_ids: list[str] = []
    completed_anchor_item_ids: list[str] = []
    for item in anchors:
        item_id = str(item.get("id") or "<missing>")
        anchor_item_ids.append(item_id)
        completed_pair = _completed_asset_pair(item)
        if completed_pair is None:
            media_gaps.append(f"{item_id}: no completed asset/version")
            continue
        asset_id, version_id = completed_pair
        asset = assets.get(asset_id)
        if asset is None:
            media_gaps.append(f"{item_id}: completed asset {asset_id} is missing")
            continue
        version = _asset_version(asset, version_id)
        if version is None:
            media_gaps.append(f"{item_id}: completed version {version_id} is missing")
            continue
        if asset.get("brief") is None:
            media_gaps.append(f"{item_id}: completed asset has no structured brief")
            continue
        if version.get("media_type") != "IMAGE":
            media_gaps.append(f"{item_id}: completed version is not an image")
            continue
        if version.get("approval_status") != "APPROVED":
            media_gaps.append(f"{item_id}: completed image is not approved")
            continue
        if asset_id not in pack_subjects:
            media_gaps.append(f"{item_id}: no non-empty ReferencePack")
            continue
        served_shots = [
            str(value)
            for value in _as_list(item.get("serves_shot_ids"), "serves_shot_ids")
        ]
        missing_shots = [
            shot_id for shot_id in served_shots if asset_id not in shot_subjects.get(shot_id, set())
        ]
        if missing_shots:
            media_gaps.append(
                f"{item_id}: missing shot reference bindings for {', '.join(missing_shots)}"
            )
            continue
        completed_anchor_item_ids.append(item_id)

    forbidden_tools = {
        "route.compile_generation_plan",
        "route.get_generation_plan",
        "route.compile_h3_prompt",
        "route.enqueue_h3_probe",
        "route.execute_h3_plan",
        "route.list_h3_plans",
        "route.list_h3_reconciliation",
        "route.list_h3_runs",
        "route.plan_h3",
        "route.preview_h3_probe",
        "route.resolve_h3_not_submitted",
        "route.resolve_h3_submission",
        "route.review_h3_take",
        "route.update_h3_asset_preparation",
        "audio.bind_master",
        "audio.update_timed_text_lock",
        "audio.analyze_music",
        "audio.inspect_manifest",
        "audio.update_voice_reference",
        "timeline.rebuild",
        "timeline.export",
        "video.enqueue",
        "video.retake",
        "video.extend",
        "video.gap_fill",
        "video.compare_takes",
        "video.inspect_take",
        "video.select_take",
        "task.advance",
        "task.cancel",
        "task.drain",
        "task.reconcile",
        "task.recover_output",
        "task.retry",
    }
    used_forbidden = sorted(set(_trace_tool_names(trace)) & forbidden_tools)
    if used_forbidden:
        errors.append("Asset-only run used forbidden downstream tools: " + ", ".join(used_forbidden))

    return AssetClosureAssessment(
        errors=errors,
        media_closed=bool(anchors)
        and len(completed_anchor_item_ids) == len(anchors),
        media_gaps=media_gaps,
        anchor_item_ids=anchor_item_ids,
        completed_anchor_item_ids=completed_anchor_item_ids,
    )


def _probe_http_boundary_errors(records: Sequence[HttpRequestRecord]) -> list[str]:
    """Ensure this process only mutates state through AgentRun public APIs."""

    errors: list[str] = []
    for method, path in records:
        if method == "GET":
            continue
        is_create = method == "POST" and path.startswith("/projects/") and path.endswith(
            "/agent-runs"
        )
        is_advance = method == "POST" and path.startswith("/agent-runs/") and path.endswith(
            "/advance"
        )
        if not (is_create or is_advance):
            errors.append(f"Probe issued forbidden direct mutation {method} {path}")
    return errors


def _write_asset_plan_markdown(
    response: JsonObject,
    assessment: AssetClosureAssessment,
    output_path: Path,
) -> None:
    plan = _asset_plan_payload(response)
    lines = [
        "# Agent 核心资产计划",
        "",
        f"- 计划文档：`{_asset_plan_document(response).get('id')}`",
        f"- 计划版本：`{_asset_plan_version(response).get('id')}`",
        f"- 计划闭环：{'PASS' if not assessment.errors else 'FAIL'}",
        f"- 媒体闭环：{'PASS' if assessment.media_closed else 'NOT AVAILABLE'}",
        "",
        "## 资产取舍",
        "",
    ]
    for value in _as_list(plan.get("items"), "asset plan items"):
        item = _as_object(value, "asset plan item")
        lines.extend(
            [
                f"### {item.get('name') or item.get('id')}",
                "",
                f"- ID：`{item.get('id')}`",
                f"- 类型：{item.get('asset_kind')}",
                f"- 决定：{item.get('disposition')}",
                f"- 理由：{item.get('rationale') or '-'}",
            ]
        )
        brief = item.get("brief")
        if isinstance(brief, dict):
            lines.append(f"- 版式：{brief.get('layout_profile') or '-'}")
        work_items = _as_list(item.get("work_items"), "asset plan work items")
        if work_items:
            lines.append("- Work items：")
            for work_value in work_items:
                work = _as_object(work_value, "asset plan work item")
                lines.append(
                    "  - `"
                    + str(work.get("id"))
                    + "` · "
                    + str(work.get("operation"))
                    + " → "
                    + str(work.get("output_role"))
                )
        lines.append("")
    if assessment.media_gaps:
        lines.extend(["## 媒体闭环缺口", ""])
        lines.extend(f"- {gap}" for gap in assessment.media_gaps)
        lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_trace_markdown(trace: JsonObject, output_path: Path) -> None:
    validation = _as_object(trace.get("validation"), "trace.validation")
    lines = [
        "# Agent asset validation trace",
        "",
        f"- Started: {trace.get('started_at')}",
        f"- Finished: {trace.get('finished_at')}",
        f"- Project: {trace.get('project_id')}",
        f"- AgentRun: {trace.get('run_id')}",
        f"- Outcome: {trace.get('outcome')}",
        f"- Elapsed: {trace.get('elapsed_seconds')} seconds",
        "",
        "## 执行轨迹",
        "",
    ]
    for index, phase_value in enumerate(_as_list(trace.get("phases"), "trace.phases"), 1):
        phase = _as_object(phase_value, "trace phase")
        lines.extend(
            [
                f"### {index}. {phase.get('operation')}",
                "",
                f"- Run 状态：{phase.get('run_status')}",
                f"- 当前聚焦：{phase.get('current_focus') or '-'}",
                f"- 计划摘要：{phase.get('plan_summary') or '-'}",
            ]
        )
        methods = _as_list(
            phase.get("selected_method_skill_ids"),
            "selected_method_skill_ids",
        )
        if methods:
            lines.append("- 方法 Skills：" + "、".join(str(value) for value in methods))
        tool_events = _as_list(
            phase.get("specialist_tool_events"),
            "specialist_tool_events",
        )
        if tool_events:
            lines.append("- Specialist Tool 事件：")
            for event_value in tool_events:
                event = _as_object(event_value, "specialist tool event")
                lines.append(
                    "  - `"
                    + str(event.get("tool_name") or "tool")
                    + "` → "
                    + str(event.get("tool_status") or event.get("turn_status") or "-")
                )
        updates = _as_list(phase.get("work_order_updates"), "work_order_updates")
        if updates:
            lines.append("- WorkOrder 进展：")
            for update_value in updates:
                update = _as_object(update_value, "work order update")
                lines.append(
                    "  - `"
                    + str(update.get("id"))
                    + "` · "
                    + str(update.get("recipient_role") or "-")
                    + " → "
                    + str(update.get("status") or "-")
                )
        lines.append("")
    errors = _as_list(validation.get("errors"), "validation.errors")
    lines.extend(["## 验收", ""])
    if errors:
        lines.extend(f"- FAIL：{value}" for value in errors)
    else:
        lines.append("- PASS：AssetPlan 已由 Agent 通过公共 AgentRun API 完成。")
        if validation.get("media_closed") is True:
            lines.append("- PASS：角色/场景锚点、审核版本、ReferencePack 与 Shot 绑定已闭环。")
        else:
            lines.append("- NOT AVAILABLE：当前执行环境没有完成真实图像媒体闭环，未伪造成功。")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _persist_trace(
    trace: JsonObject,
    *,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> None:
    trace["updated_at"] = _utc_now()
    trace["raw_response_files"] = _json_strings(recorder.files())
    temporary = output_dir / "trace.json.tmp"
    temporary.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output_dir / "trace.json")
    _write_trace_markdown(trace, output_dir / "trace.md")


def _advance_with_heartbeats(
    client: AgentAssetApiClient,
    run_id: str,
    *,
    max_actions: int,
    heartbeat_seconds: float,
    trace: JsonObject,
    recorder: RawResponseRecorder,
    output_dir: Path,
) -> JsonObject:
    outcomes: Queue[tuple[str, object]] = Queue(maxsize=1)

    def advance() -> None:
        try:
            outcomes.put(("response", client.advance_agent_run(run_id, max_actions)))
        except Exception as exc:
            outcomes.put(("error", exc))

    Thread(target=advance, name="agent-asset-advance", daemon=True).start()
    started = monotonic()
    observations = 0
    while True:
        try:
            outcome, value = outcomes.get(timeout=heartbeat_seconds)
        except Empty:
            observations += 1
            try:
                observed = client.get_agent_run(run_id)
                board = client.get_production_board(run_id)
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observations,
                    "run_status": _run_status(observed),
                    "current_focus": board.get("current_focus"),
                    "running_actions": _running_actions(observed),
                    "work_orders": board.get("work_orders", []),
                }
            except ProbeFailure as exc:
                trace["in_flight"] = {
                    "observed_at": _utc_now(),
                    "elapsed_seconds": round(monotonic() - started, 1),
                    "observation_count": observations,
                    "observation_error": str(exc),
                }
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            print(
                f"[{_utc_now()}] Agent asset action still running after "
                f"{monotonic() - started:.0f}s",
                flush=True,
            )
            continue
        trace.pop("in_flight", None)
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if outcome == "response":
            return _as_object(cast(JsonValue, value), "advance response")
        if isinstance(value, Exception):
            raise value
        raise ProbeFailure("Agent advance failed without an exception")


def _load_or_initialize_trace(
    output_dir: Path,
    *,
    base_url: str,
    project_id: str,
    run_id: str | None,
) -> JsonObject:
    trace_path = output_dir / "trace.json"
    if trace_path.exists():
        if run_id is None:
            raise ProbeFailure(
                "Output directory already contains a trace; pass --run-id to resume "
                "or choose a new directory"
            )
        try:
            trace = _as_object(
                cast(JsonValue, json.loads(trace_path.read_text(encoding="utf-8"))),
                "existing trace",
            )
        except json.JSONDecodeError as exc:
            raise ProbeFailure("Existing trace.json is not valid JSON") from exc
        if trace.get("project_id") != project_id or trace.get("run_id") != run_id:
            raise ProbeFailure("Existing trace belongs to another project or AgentRun")
        trace["outcome"] = "RUNNING"
        trace["finished_at"] = None
        trace["resumed_at"] = _utc_now()
        trace["validation"] = {"errors": []}
        return trace
    if run_id is not None:
        raise ProbeFailure(
            "Resuming requires the original output directory with trace.json and "
            "project_before.json"
        )
    return {
        "probe": "agent-asset-stage-black-box",
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "finished_at": None,
        "base_url": base_url,
        "project_id": project_id,
        "run_id": None,
        "outcome": "RUNNING",
        "phases": [],
        "validation": {"errors": []},
        "raw_response_files": [],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an Agent-owned core-asset validation on an existing "
            "script/Bible/spec/storyboard-ready project."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend origin or full /api/short-drama base URL.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE)
    parser.add_argument("--run-id", help="Resume using the original output directory.")
    parser.add_argument(
        "--advance-max-actions",
        type=int,
        default=1,
        help="Actions requested per advance call; this is a trace batch, not a creative limit.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=None,
        help="Optional transport timeout for one HTTP request, not an Agent deadline.",
    )
    parser.add_argument(
        "--progress-heartbeat-seconds",
        type=float,
        default=30.0,
        help="Trace refresh interval while one Agent action is running.",
    )
    parser.add_argument(
        "--require-media-closure",
        action="store_true",
        help="Fail when plan closure succeeds but no configured image executor closes media.",
    )
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if not str(args.project_id).strip():
        raise ProbeFailure("--project-id must not be empty")
    if not str(args.objective).strip():
        raise ProbeFailure("--objective must not be empty")
    if int(args.advance_max_actions) < 1 or int(args.advance_max_actions) > 8:
        raise ProbeFailure("--advance-max-actions must be between 1 and 8")
    if args.request_timeout_seconds is not None and float(
        args.request_timeout_seconds
    ) <= 0:
        raise ProbeFailure("--request-timeout-seconds must be positive")
    if float(args.progress_heartbeat_seconds) <= 0:
        raise ProbeFailure("--progress-heartbeat-seconds must be positive")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _validate_args(args)
    except ProbeFailure as exc:
        print(str(exc), file=sys.stderr)
        return 2

    project_id = str(args.project_id).strip()
    run_id_arg = str(args.run_id).strip() if args.run_id else None
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = RawResponseRecorder(output_dir)
    started = monotonic()
    try:
        trace = _load_or_initialize_trace(
            output_dir,
            base_url=str(args.base_url),
            project_id=project_id,
            run_id=run_id_arg,
        )
    except ProbeFailure as exc:
        print(f"Asset probe could not start: {exc}", file=sys.stderr)
        return 2

    exit_code = 2
    after: JsonObject | None = None
    asset_plan_response: JsonObject | None = None
    client: AgentAssetApiClient | None = None
    try:
        client = AgentAssetApiClient(
            str(args.base_url),
            timeout_seconds=(
                float(args.request_timeout_seconds)
                if args.request_timeout_seconds is not None
                else None
            ),
            recorder=recorder,
        )
        before_path = output_dir / "project_before.json"
        if run_id_arg is None:
            before = client.get_project(project_id)
            before_path.write_text(
                json.dumps(before, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            before_document = _project_document(before)
            if before_document.get("production_spec_document_id") is None:
                raise ProbeFailure("Project has no confirmed ProductionSpec")
            if before_document.get("storyboard_document_id") is None:
                raise ProbeFailure("Project has no canonical Storyboard")
            run_view = client.create_agent_run(project_id, str(args.objective).strip())
            run_id = _string(_run_object(run_view).get("id"), "created AgentRun id")
            trace["run_id"] = run_id
            operation = "create_agent_run"
        else:
            if not before_path.exists():
                raise ProbeFailure("Resume output is missing project_before.json")
            before = _as_object(
                cast(JsonValue, json.loads(before_path.read_text(encoding="utf-8"))),
                "project_before.json",
            )
            run_id = run_id_arg
            run_view = client.get_agent_run(run_id)
            if _run_object(run_view).get("project_id") != project_id:
                raise ProbeFailure("Resumed AgentRun belongs to another project")
            operation = "resume_agent_run"

        board = client.get_production_board(run_id)
        phases = _as_list(trace.get("phases"), "trace.phases")
        seen_actions = _seen_action_ids(trace)
        seen_work_orders = _seen_work_order_keys(trace)
        seen_specialist_turns = _seen_specialist_turn_ids(trace)
        phases.append(
            _trace_phase(
                operation=operation,
                run_view=run_view,
                board=board,
                seen_action_ids=seen_actions,
                seen_work_order_keys=seen_work_orders,
                specialist_tool_events=_collect_specialist_tool_events(
                    client,
                    _board_work_order_ids(board),
                    seen_specialist_turns,
                ),
            )
        )
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)

        while True:
            status = _run_status(run_view)
            if status == "COMPLETED":
                break
            if status in {"FAILED", "CANCELLED"}:
                raise ProbeFailure(f"AgentRun reached terminal status {status}")
            if status == "WAITING":
                blocker = _waiting_blocker(run_view, board)
                if blocker is not None:
                    raise ProbeFailure(blocker)
            progress_before = _progress_fingerprint(run_view, board)
            try:
                advanced = _advance_with_heartbeats(
                    client,
                    run_id,
                    max_actions=int(args.advance_max_actions),
                    heartbeat_seconds=float(args.progress_heartbeat_seconds),
                    trace=trace,
                    recorder=recorder,
                    output_dir=output_dir,
                )
            except ApiHttpError as exc:
                observed_run = client.get_agent_run(run_id)
                observed_board = client.get_production_board(run_id)
                recovery_note = _resumable_agent_conflict(
                    exc,
                    observed_run,
                    observed_board,
                )
                if recovery_note is None:
                    raise
                run_view = observed_run
                board = observed_board
                phases.append(
                    _trace_phase(
                        operation="advance_agent_run(recoverable_worker_failure)",
                        run_view=run_view,
                        board=board,
                        seen_action_ids=seen_actions,
                        seen_work_order_keys=seen_work_orders,
                        specialist_tool_events=_collect_specialist_tool_events(
                            client,
                            _board_work_order_ids(board),
                            seen_specialist_turns,
                        ),
                        notes=[recovery_note],
                    )
                )
                _persist_trace(trace, recorder=recorder, output_dir=output_dir)
                if _progress_fingerprint(run_view, board) == progress_before:
                    raise ProbeFailure(
                        "Recoverable Worker failure did not persist a semantic handoff"
                    ) from exc
                continue
            run_view = _as_object(advanced.get("run"), "advance response run")
            board = client.get_production_board(run_id)
            advanced_work_order_ids = _as_list(
                advanced.get("advanced_work_order_ids"),
                "advanced_work_order_ids",
            )
            phases.append(
                _trace_phase(
                    operation=f"advance_agent_run(max_actions={args.advance_max_actions})",
                    run_view=run_view,
                    board=board,
                    seen_action_ids=seen_actions,
                    seen_work_order_keys=seen_work_orders,
                    executed_action_ids=_as_list(
                        advanced.get("executed_action_ids"),
                        "executed_action_ids",
                    ),
                    created_work_order_ids=_as_list(
                        advanced.get("created_work_order_ids"),
                        "created_work_order_ids",
                    ),
                    advanced_work_order_ids=advanced_work_order_ids,
                    specialist_tool_events=_collect_specialist_tool_events(
                        client,
                        [
                            _string(value, "advanced work order id")
                            for value in advanced_work_order_ids
                        ],
                        seen_specialist_turns,
                    ),
                    notes=_as_list(advanced.get("notes"), "advance notes"),
                )
            )
            _persist_trace(trace, recorder=recorder, output_dir=output_dir)
            if (
                status == "WAITING"
                and _run_status(run_view) == "WAITING"
                and _progress_fingerprint(run_view, board) == progress_before
            ):
                phase = _as_object(phases[-1], "latest trace phase")
                notes = _as_list(phase.get("notes"), "trace phase notes")
                notes.append(
                    "Public Agent advance returned before the next semantic state "
                    "was persisted; the probe will keep driving the AgentRun."
                )
                phase["notes"] = notes
                _persist_trace(trace, recorder=recorder, output_dir=output_dir)
                continue

        run_view = client.get_agent_run(run_id)
        after = client.get_project(project_id)
        asset_plan_document_id = _string(
            _project_document(after).get("asset_plan_document_id"),
            "asset_plan_document_id",
        )
        asset_plan_response = client.get_production_document(
            project_id,
            asset_plan_document_id,
        )
        (output_dir / "project.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "asset_plan.json").write_text(
            json.dumps(asset_plan_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        assessment = _validate_asset_stage_result(
            run_view=run_view,
            before=before,
            after=after,
            asset_plan_response=asset_plan_response,
            trace=trace,
        )
        boundary_errors = _probe_http_boundary_errors(client.request_records())
        errors = [*assessment.errors, *boundary_errors]
        trace["validation"] = {
            "errors": _json_strings(errors),
            "plan_closed": not errors,
            "media_closed": assessment.media_closed,
            "media_gaps": _json_strings(assessment.media_gaps),
            "anchor_item_ids": _json_strings(assessment.anchor_item_ids),
            "completed_anchor_item_ids": _json_strings(
                assessment.completed_anchor_item_ids
            ),
            "asset_plan_document_id": asset_plan_document_id,
            "asset_plan_version_id": _asset_plan_version(asset_plan_response).get("id"),
            "direct_http_requests": [
                {"method": method, "path": path}
                for method, path in client.request_records()
            ],
        }
        _write_asset_plan_markdown(
            asset_plan_response,
            assessment,
            output_dir / "asset_plan.md",
        )
        if errors:
            trace["outcome"] = "FAILED_VALIDATION"
            exit_code = 1
        elif assessment.media_closed:
            trace["outcome"] = "PASSED_MEDIA_CLOSURE"
            exit_code = 0
        elif bool(args.require_media_closure):
            trace["outcome"] = "FAILED_MEDIA_CLOSURE"
            trace_validation = _as_object(trace["validation"], "trace.validation")
            trace_validation["errors"] = [
                "Media closure was required but no approved, packed, shot-bound "
                "image closure was produced"
            ]
            exit_code = 1
        else:
            trace["outcome"] = "PASSED_PLAN_ONLY"
            exit_code = 0
    except ProbeFailure as exc:
        trace["outcome"] = "FAILED_PROBE"
        trace["validation"] = {"errors": [str(exc)]}
        print(f"Asset validation probe failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        trace["outcome"] = "INTERRUPTED"
        trace["validation"] = {
            "errors": ["Probe interrupted; resume with the recorded run and output directory"]
        }
        exit_code = 130
    except Exception as exc:
        trace["outcome"] = "FAILED_PROBE"
        trace["validation"] = {
            "errors": [f"Unexpected {type(exc).__name__}: {exc}"]
        }
        print(f"Asset validation probe hit an unexpected error: {exc}", file=sys.stderr)
    finally:
        trace["finished_at"] = _utc_now()
        trace["elapsed_seconds"] = round(monotonic() - started, 3)
        _persist_trace(trace, recorder=recorder, output_dir=output_dir)
        if after is not None and not (output_dir / "project.json").exists():
            (output_dir / "project.json").write_text(
                json.dumps(after, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if asset_plan_response is not None and not (
            output_dir / "asset_plan.json"
        ).exists():
            (output_dir / "asset_plan.json").write_text(
                json.dumps(asset_plan_response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
