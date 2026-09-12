"""Loaded role methods and a real, still-constrained repair handoff; no Provider."""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore, ContextPack, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import AgentRun, RunMemory, WorkOrderNextOwner
from short_drama.models import ProductionProfile
from short_drama.production_manager import (
    AgentWorkOrderResultRequest, AgentWorkOrderView, LanguageSpecialistAgent,
    _production_manager_runtime_instructions,
)
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt
from short_drama.skills.loader import load_shared_role_prompts
from tests.test_harness_tool_context import registry
from tests.test_specialist_turn_persistence import SequencedLanguageClient


@pytest.mark.parametrize(("role", "stage", "decision"), [
    (ProductionSkillRole.DIRECTOR, ProductionPromptStage.STORYBOARD, "Agent 自定的手别"),
    (ProductionSkillRole.ASSET_BUILDER, ProductionPromptStage.ASSETS, "next_owner=LEADER_REPLAN"),
    (ProductionSkillRole.ASSET_VERIFIER, ProductionPromptStage.ASSETS, "哪项用途未满足"),
])
def test_loaded_methods_distinguish_adjustable_design_without_losing_quality(
    role: ProductionSkillRole, stage: ProductionPromptStage, decision: str,
) -> None:
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(role,),
        stage=stage, method_skill_ids=("method.drama_series",)).text
    assert load_shared_role_prompts(stage=stage)[role] in prompt
    assert decision in prompt
    assert "COMPOSITION" in prompt and "FIRST_FRAME / LAST_FRAME" in prompt
    assert "用户明确要求" in prompt and "因果" in prompt
    assert "StageCapsule" not in prompt and "submitMainAgent" not in prompt
    if role == ProductionSkillRole.DIRECTOR:
        assert "## 表演与物理动作" in prompt and "## 资产检查" not in prompt
    else:
        assert "## 表演与物理动作" not in prompt


def test_leader_receives_scope_ownership_and_rework_choice_in_real_assembly() -> None:
    skill = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.PLANNER,), stage=ProductionPromptStage.ORCHESTRATION,
        method_skill_ids=("method.drama_series",)).text
    instructions = _production_manager_runtime_instructions(skill_prompt=skill,
        role_values="DIRECTOR, ASSET_BUILDER", prompt_stage_values="STORYBOARD, ASSETS")
    assert "不要把自行选择的左右手" in instructions
    assert "Worker 返回 PARTIAL / LEADER_REPLAN" in instructions
    assert "不把降低验收标准当作解决" in instructions
    assert "保留用户已生效的编辑" in instructions
    assert "enqueue=false" in instructions


def test_saved_real_repair_scope_reaches_resumed_worker_and_can_return_partial(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/efficient_production_work_order.json").read_text())
    work = WorkOrder.model_validate(fixture["work_order"])
    original = deepcopy(work)
    project_id = fixture["source"]["project_id"]
    store = AgentRuntimeStore(tmp_path / "runtime.sqlite3")
    store.create_run(AgentRun(id=work.parent_run_id, project_id=project_id,
        objective=work.objective, root_agent="leader"), RunMemory(run_id=work.parent_run_id))
    store.save_work_order(work)
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    evidence = cast(dict[str, JsonValue], work.specialist_turns[0].result)
    refs = cast(list[JsonValue], evidence["artifact_refs"])
    # Scripted response verifies the protocol, not a change in real LM choices.
    response = {"status": "PARTIAL", "next_owner": "LEADER_REPLAN",
        "concise_summary": "当前配准用途未满足；请决定局部修复还是调整镜头覆盖。",
        "findings": ["shot-last-version-10 / asset-version-293ec22a0807：双手与挂包状态正确，电脑底座和钥匙落点有可见漂移；不把当前用途改称通过。"],
        "recommended_next_actions": ["保留精确候选，由导演比较当前动作覆盖与必要配准；本工单禁止改分镜，未自行改动。"],
        "evidence_refs": refs}
    client = SequencedLanguageClient([json.dumps(response, ensure_ascii=False)])
    worker = LanguageSpecialistAgent(client, registry())
    worker.context_compiler = ModelInputCompiler(reopened)
    system_prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.ASSET_BUILDER,), stage=ProductionPromptStage.ASSETS,
        method_skill_ids=("method.drama_series",)).text
    result = worker.execute(AgentWorkOrderView(work_order=resumed,
        context=ContextPack(run_id=work.parent_run_id, project_id=project_id,
            objective=work.objective, constraints=work.constraints), system_prompt=system_prompt))
    assert isinstance(result, AgentWorkOrderResultRequest)
    assert result.next_owner == WorkOrderNextOwner.LEADER_REPLAN
    assert len(client.inputs) == 1
    payload = json.loads(client.inputs[0])
    assert payload["work_order"]["constraints"] == original.constraints
    assert payload["work_order"]["task_inputs"] == original.task_inputs
    assert "禁止创建、入队、提交或执行视频" in json.dumps(payload, ensure_ascii=False)
    assert "PROP_BASE_POSITION_DRIFT" in client.inputs[0]
    assert "本次明确要求的底座原位不动" in client.inputs[0]
    assert "recommended_next_actions 提出保留因果" in client.instructions[0]
    assert payload["response_schemas"]["final_result"]["properties"]["next_owner"]["anyOf"][0]["enum"] == ["LEADER_REPLAN", "USER_INPUT"]
    unchanged = reopened.get_work_order(work.id)
    assert unchanged is not None
    assert unchanged.constraints == original.constraints
    assert unchanged.specialist_turns == original.specialist_turns
    assert any(ref.artifact_id == "asset-version-293ec22a0807" and ref.version == 5 for ref in result.evidence_refs)
