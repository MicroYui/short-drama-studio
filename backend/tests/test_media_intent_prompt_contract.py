"""Check the actual Leader/Worker requests, not simulated model judgement."""

from pathlib import Path

import pytest

from short_drama.agent_runtime.models import ActionKind, PlanStep
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
)
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole
from short_drama.service import ShortDramaService
from tests.test_production_manager import FakePlanningLanguageClient, FakeSpecialistLanguageClient


def _assert_media_intent_contract(instructions: str) -> None:
    assert instructions.count("核对所用条件的来源、对象和适用时段") == 1
    assert "自定细节已保存也不自动成为用户硬要求" in instructions
    assert "局部动作条件不能无依据扩展为全过程限制" in instructions
    assert "不影响原目标或实际连续性的结果，应保留使用" in instructions
    assert "不能靠静默省略约束绕过来源校验" in instructions
    assert "身份、对白、关键动作先后、可见物理关系和实际衔接" in instructions
    assert "未观察或未听审保持未知" in instructions
    assert "audio.transcribe_native" in instructions and "audio.inspect_manifest" in instructions
    assert 'scope="TAKE"' in instructions and "source_start_seconds" in instructions and "source_end_seconds" in instructions
    assert "无需为审听建立或保存正式时间线" in instructions
    assert "不能直接当作 `FILM` 字幕时码或当前剪辑覆盖" in instructions
    assert "未实际听审的表现仍保持未知" in instructions


def test_actual_leader_request_checks_constraint_source_before_rework(tmp_path: Path) -> None:
    client = FakePlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    service.create_agent_run(project.id, AgentRunCreateRequest(
        objective="核对现有候选是否完成指定手的核心交接，保留能满足目标的结果。",
        render_export=False,
    ))

    assert len(client.instructions) == 1
    _assert_media_intent_contract(client.instructions[0])
    assert "goal.original_request 是完整原始要求" in client.instructions[0]


@pytest.mark.parametrize(("role", "stage", "role_phrase"), [
    (ProductionSkillRole.VIDEO_PROMPT_COMPILER, ProductionPromptStage.VISUAL_GEN,
     "视频提示词编译的通用职责"),
    (ProductionSkillRole.DIRECTOR, ProductionPromptStage.STORYBOARD, "导演的通用职责"),
    (ProductionSkillRole.DIRECTOR, ProductionPromptStage.POST,
     "使用当前已选视频、实际媒体时间和本集编辑时间线"),
])
def test_actual_media_worker_receives_source_rule_in_its_current_stage(
    tmp_path: Path, role: ProductionSkillRole, stage: ProductionPromptStage, role_phrase: str,
) -> None:
    client = FakeSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(project.id, AgentRunCreateRequest(
        objective="许宁用左手完成核心交接，随后由她独自持有同一信封。",
        initial_plan=AgentPlanProposal(summary="核对当前交接结果。", steps=[PlanStep(
            id="review-existing-media", title="核对现有候选", kind=ActionKind.DELEGATION,
            assigned_agent="handoff-media-worker", production_skill_role=role,
            production_prompt_stage=stage, instructions="观察已保存的候选并说明本次可用范围。",
        )]),
    ))
    waiting = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    service.execute_agent_work_order(waiting.created_work_order_ids[0])

    assert len(client.instructions) == 1
    instructions = client.instructions[0]
    _assert_media_intent_contract(instructions)
    assert role_phrase in instructions
    if stage == ProductionPromptStage.STORYBOARD:
        assert "另一只手自然辅助不自动构成失败" in instructions
        assert "用户明确要求全过程单手时则按该要求检查" in instructions
    if role == ProductionSkillRole.VIDEO_PROMPT_COMPILER:
        assert "忠实编译为目标 Provider 可理解的输入" in instructions
        assert "不伪造已上传的媒体" in instructions
