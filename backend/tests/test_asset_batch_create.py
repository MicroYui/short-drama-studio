"""Batch text definitions retain authored facts in the next real Worker input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import ContextPack, WorkOrder
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.models import Asset, CoreAssetBrief, ProjectDocument, ProductionProfile
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioEpisodeCreate
from short_drama.tools.contracts import ToolResult
from tests.test_harness_tool_context import ScriptedClient


class CountingRepository(ShortDramaRepository):
    saves: int = 0

    def save_project(self, document: ProjectDocument) -> None:
        self.saves += 1
        super().save_project(document)


@pytest.fixture
def project(tmp_path: Path) -> tuple[ShortDramaService, str, CountingRepository]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    counted = CountingRepository(service.repository.database_path)
    service.asset_workbench.repository = counted
    return service, pid, counted


def _definition(name: str, kind: str = "CHARACTER", **brief: JsonValue) -> dict[str, JsonValue]:
    return {"kind": kind, "name": name, "descriptor": f"{name}的已确定外形与当前用途，不代表已生成图片。",
        "brief": {"identity_anchor": name, "visual_attributes": ["黑色齐肩侧分直发"],
            "wardrobe_or_materials": ["浅米白无图案衬衫", "深灰直筒西裤"], **brief}}


def test_multiple_definitions_use_one_save_and_return_only_actual_new_objects(
    project: tuple[ShortDramaService, str, CountingRepository],
) -> None:
    service, pid, repo = project
    before = service.get_project(pid).document
    definitions = [_definition("许岚"), _definition("赵衡"), _definition("小会议室", "LOCATION")]
    result = service.tool_registry.invoke("artifact.create_assets", {"project_id": pid, "request": {"assets": definitions}})
    assert result.error is None, result.model_dump_json()
    assert repo.saves == 1 and result.output is not None
    assert set(result.output) == {"assets", "created_asset_ids"}
    assets = [Asset.model_validate(value) for value in cast(list[JsonValue], result.output["assets"])]
    assert [asset.name for asset in assets] == ["许岚", "赵衡", "小会议室"]
    assert result.output["created_asset_ids"] == [asset.id for asset in assets]
    after = service.get_project(pid).document
    assert after.assets[:len(before.assets)] == before.assets
    assert after.assets[len(before.assets):] == assets
    assert after.shots == before.shots and after.script_versions == before.script_versions
    for asset, definition in zip(assets, definitions, strict=True):
        assert asset.brief and asset.brief.wardrobe_or_materials == ["浅米白无图案衬衫", "深灰直筒西裤"]
        assert len(asset.versions) == 1 and asset.versions[0].descriptor == definition["descriptor"]
        assert asset.versions[0].media_type.value == "TEXT_REFERENCE" and asset.versions[0].local_path is None
    assert len(result.artifact_refs) == 6 and result.receipt
    assert result.receipt.changed_ids == {"created_asset_ids": [asset.id for asset in assets]}
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert reopened.get_project(pid).document.assets[-3:] == assets


def test_episode_batch_returns_actual_shared_asset_ownership(
    project: tuple[ShortDramaService, str, CountingRepository],
) -> None:
    service, pid, repo = project
    child = service.studio.create_episode(pid, StudioEpisodeCreate(title="下一集")).document.project.id
    result = service.tool_registry.invoke("artifact.create_assets", {"project_id": child,
        "request": {"assets": [_definition("本集新人")]}})
    assert result.error is None and result.output is not None, result.model_dump_json()
    asset = Asset.model_validate(cast(list[JsonValue], result.output["assets"])[0])
    assert repo.saves == 1 and asset.project_id == pid
    assert asset == service.get_project(child).document.assets[-1] == service.get_project(pid).document.assets[-1]
    assert result.artifact_refs[0].project_id == child


@pytest.mark.parametrize("bad,fragment", [
    ({"kind": "PROP", "name": "笔", "descriptor": "黑色笔", "brief": {"identity_anchor": "笔", "visual_attributes": ["黑色"], "reference_strategy": "CHARACTER_STANDARD_VIEWS"}}, "character asset"),
    ({"kind": "CHARACTER", "name": "", "descriptor": "未命名"}, "String should have at least 1 character"),
])
def test_batch_validation_prevents_partial_save(
    project: tuple[ShortDramaService, str, CountingRepository], bad: dict[str, JsonValue], fragment: str,
) -> None:
    service, pid, repo = project
    before = service.get_project(pid).document
    result = service.tool_registry.invoke("artifact.create_assets", {"project_id": pid, "request": {
        "assets": [_definition("先通过的对象"), bad]}})
    assert result.error and fragment in result.error.message
    assert repo.saves == 0 and service.get_project(pid).document == before


def _worker_input(service: ShortDramaService, pid: str, turns: list[tuple[str, dict[str, JsonValue], ToolResult]]) -> dict[str, JsonValue]:
    work = WorkOrder(id="work", parent_run_id="run", sender="leader", recipient_role="writer",
        objective="保存多个已确定对象后继续写场景，不重复读取自己刚写过的设定。", why_now="Current",
        production_skill_role=ProductionSkillRole.WRITER, production_prompt_stage=ProductionPromptStage.STORY)
    for sequence, (tool, args, result) in enumerate(turns, 1):
        work.specialist_turns.append(SpecialistTurnRecord(id=f"turn-{sequence}", sequence=sequence,
            tool_name=tool, arguments=args, reason="Save current definition", status=SpecialistTurnStatus.COMPLETED,
            result=cast(dict[str, JsonValue], result.model_dump(mode="json"))))
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue current scene",
        "arguments": {"project_id": pid}})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.execute(AgentWorkOrderView(work_order=work, context=ContextPack(run_id="run", project_id=pid, objective=work.objective),
        system_prompt="Continue using current saved facts."))
    assert len(client.payloads) == 1
    return cast(dict[str, JsonValue], client.payloads[0]["working_set"])


def test_new_writer_can_request_asset_creation_without_a_discovery_round(
    project: tuple[ShortDramaService, str, CountingRepository],
) -> None:
    service, pid, _repo = project
    args: dict[str, JsonValue] = {"project_id": pid, "request": {"assets": [_definition("许岚")]}}
    client = ScriptedClient([json.dumps({"tool_name": "artifact.create_assets", "arguments": args,
        "reason": "Save the character already defined for this story."})])
    work = WorkOrder(id="new-writer", parent_run_id="run", sender="leader", recipient_role="writer",
        objective="Write the story and save its named characters.", why_now="Current",
        production_skill_role=ProductionSkillRole.WRITER, production_prompt_stage=ProductionPromptStage.STORY)
    LanguageSpecialistAgent(client, service.tool_registry).execute(AgentWorkOrderView(
        work_order=work, context=ContextPack(run_id="run", project_id=pid, objective=work.objective),
        system_prompt="Write this story."))
    assert len(client.payloads) == 1
    schemas = cast(dict[str, dict[str, JsonValue]], client.payloads[0]["tool_schemas"])
    assert {"artifact.create_assets", "script.save_scene"} <= schemas.keys()
    assert schemas["artifact.create_assets"]["input_schema"] == service.tool_registry.describe("artifact.create_assets").input_schema
    assert "artifact.create_production_spec" not in schemas and "change.apply" not in schemas


@pytest.mark.parametrize("batch", [False, True])
def test_actual_worker_input_keeps_written_brief_and_descriptor_once_per_object(
    project: tuple[ShortDramaService, str, CountingRepository], batch: bool,
) -> None:
    service, pid, _repo = project
    definition = _definition("许岚")
    tool = "artifact.create_assets" if batch else "artifact.create_asset"
    args: dict[str, JsonValue] = {"project_id": pid, "request": {"assets": [definition, _definition("会议室", "LOCATION")]} if batch else definition}
    result = service.tool_registry.invoke(tool, args)
    assert result.error is None, result.model_dump_json()
    state = _worker_input(service, pid, [(tool, args, result)])
    entities = cast(list[dict[str, JsonValue]], state["entities"])
    assets = [cast(dict[str, JsonValue], item["state"]) for item in entities if item["kind"] == "asset"]
    assert len(assets) == (2 if batch else 1)
    saved = next(asset for asset in assets if asset["name"] == "许岚")
    assert saved["brief"] == CoreAssetBrief.model_validate(definition["brief"]).model_dump(mode="json")
    assert saved["descriptor"] == definition["descriptor"]
    assert saved["descriptor_version_id"] == saved["current_version_id"]
    assert "versions" not in saved and "project" not in saved
    handoff = cast(dict[str, JsonValue], state["latest_handoff"])
    assert "output" not in cast(dict[str, JsonValue], handoff["result"])
    assert json.dumps(state, ensure_ascii=False).count(str(definition["descriptor"])) == 1


def test_later_brief_update_replaces_only_that_current_object(
    project: tuple[ShortDramaService, str, CountingRepository],
) -> None:
    service, pid, _repo = project
    args: dict[str, JsonValue] = {"project_id": pid, "request": {"assets": [_definition("许岚"), _definition("赵衡")]}}
    result = service.tool_registry.invoke("artifact.create_assets", args)
    assert result.output and result.error is None
    ids = cast(list[str], result.output["created_asset_ids"])
    updated_args: dict[str, JsonValue] = {"project_id": pid, "asset_id": ids[0], "request": {"brief": {
        "identity_anchor": "许岚", "wardrobe_or_materials": ["当前改为蓝衬衫"], "visual_attributes": ["短发"]}}}
    updated = service.tool_registry.invoke("artifact.update_brief", updated_args)
    assert updated.error is None, updated.model_dump_json()
    state = _worker_input(service, pid, [("artifact.create_assets", args, result), ("artifact.update_brief", updated_args, updated)])
    assets = {item["id"]: cast(dict[str, JsonValue], item["state"]) for item in cast(list[dict[str, JsonValue]], state["entities"]) if item["kind"] == "asset"}
    assert len(assets) == 2
    assert cast(dict[str, JsonValue], assets[ids[0]]["brief"])["wardrobe_or_materials"] == ["当前改为蓝衬衫"]
    assert cast(dict[str, JsonValue], assets[ids[1]]["brief"])["wardrobe_or_materials"] == ["浅米白无图案衬衫", "深灰直筒西裤"]
    assert len([item for item in cast(list[dict[str, JsonValue]], state["entities"]) if item["kind"] == "asset_version"]) == 2


@pytest.mark.parametrize("role,stage", [(ProductionSkillRole.WRITER, ProductionPromptStage.STORY),
                                       (ProductionSkillRole.ASSET_PLANNER, ProductionPromptStage.ASSETS)])
def test_actual_role_text_exposes_specific_batch_tool(role: ProductionSkillRole, stage: ProductionPromptStage) -> None:
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(role,), stage=stage,
        method_skill_ids=("method.drama_series",))
    assert "artifact.create_assets" in prompt.text and "request.assets" in prompt.text
