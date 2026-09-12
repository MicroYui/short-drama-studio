"""Check the method text that reaches each role, including stage overrides."""

from __future__ import annotations

import json

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_method_skill_prompt,
    compile_production_prompt,
    production_method_skill,
)
from short_drama.skills.loader import DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT


METHODS = (
    "co_op_game_intro",
    "handdrawn_live",
    "lyric_video",
    "paper_collage_explainer",
    "papercraft_stop_motion",
)

# These are concrete previous live instructions, not words in the shared
# explanation of why external workflows are not local capabilities.
OBSOLETE_INSTRUCTIONS = (
    "hub_analyse_media",
    "hub_canvas_write_node",
    "StageCapsule",
    "Gate 2",
    "execution_locks",
    "runtime_refs",
    "提供简洁时长选项卡",
    "先用弹窗式问题",
    "用户明确确认 H3 生成前",
    "首图确认后",
    "制作方案确认后",
    "等待用户批准",
    "用户选定时长前",
    "表格后用选择卡片询问用户",
    "生成 3 到 5 个不同创意",
    "15 秒 16:9",
    "0-3 秒必须",
    "固定的 16:9",
    "画幅：16:9",
    "目标约 4 秒",
    "模型存在 15 秒单次生成上限",
    "五大衔接锁",
    "绝对只使用硬切",
    "绝无淡入淡出",
    "1/4 或 1/8 拍",
    "35mm Film Grain Overlay",
    "固定六段骨架",
    "H3 唯一可执行填空骨架",
)


@pytest.mark.parametrize("method_name", METHODS)
def test_every_live_role_and_stage_uses_adapted_method_body(method_name: str) -> None:
    method_id = f"method.{method_name}"
    method = production_method_skill(method_id)
    stages = (None, *ProductionPromptStage)

    for role in ProductionSkillRole:
        for stage in stages:
            body = method.prompt_for(role, stage=stage)
            method_prompt = compile_production_method_skill_prompt(
                (method_id,), roles=(role,), stage=stage,
            )
            if body is None:
                assert method_prompt == "", (method_id, role, stage)
                continue

            assert body in method_prompt
            assert "正常失败例" in body, (method_id, role, stage)
            assert "## STEP " not in body and "## Step " not in body
            # Also inspect the actual assembled input, so a local wrapper cannot
            # merely claim to override a contradictory method body underneath.
            actual = compile_production_prompt(
                ProductionProfile.AI_SHORT_DRAMA,
                roles=(role,), method_skill_ids=(method_id,), stage=stage,
                include_constitution=False,
            ).text
            assert body in actual
            for old_instruction in OBSOLETE_INSTRUCTIONS:
                assert old_instruction not in actual, (
                    method_id, role, stage, old_instruction,
                )


@pytest.mark.parametrize("method_name", METHODS)
def test_local_execution_sections_keep_upstream_provenance_separate(method_name: str) -> None:
    manifest = json.loads(
        (DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT / method_name / "manifest.json").read_text()
    )
    assert manifest["upstream_sources"]
    assert manifest["user_overridable"] is True
    for references in manifest["role_source_sections"].values():
        assert references
        assert all(reference.startswith("references/runtime-adaptation.md::") for reference in references)
    for references in manifest["stage_role_source_sections"].values():
        assert references
        assert all("sources/" not in reference for reference in references)


@pytest.mark.parametrize(
    ("method_name", "role", "knowledge"),
    (
        ("co_op_game_intro", ProductionSkillRole.ASSET_BUILDER, ("身份图", "主要动作", "接触阴影")),
        ("handdrawn_live", ProductionSkillRole.DIRECTOR, ("接触影", "可追踪的拖痕", "摄影者")),
        ("lyric_video", ProductionSkillRole.PLANNER, ("歌曲时间", "局部时间", "嘴型", "连续主音频")),
        ("lyric_video", ProductionSkillRole.WRITER, ("当前源文本", "实际音频", "人工文字")),
        ("paper_collage_explainer", ProductionSkillRole.ASSET_PLANNER, ("半调", "纤维", "焦橙", "墨绿")),
        ("papercraft_stop_motion", ProductionSkillRole.ASSET_VERIFIER, ("讲解职责", "关节节点", "投影", "所在层")),
    ),
)
def test_adapted_roles_retain_specific_craft(
    method_name: str, role: ProductionSkillRole, knowledge: tuple[str, ...],
) -> None:
    prompt = compile_production_method_skill_prompt(
        (f"method.{method_name}",), roles=(role,),
    )
    for fact in knowledge:
        assert fact in prompt


@pytest.mark.parametrize("method_name", METHODS)
def test_post_and_video_compilation_do_not_reload_development(method_name: str) -> None:
    method_id = f"method.{method_name}"
    method = production_method_skill(method_id)
    concept = method.role_prompts[ProductionSkillRole.CONCEPT]
    director = method.role_prompts[ProductionSkillRole.DIRECTOR]
    post = compile_production_method_skill_prompt(
        (method_id,), roles=(ProductionSkillRole.DIRECTOR,), stage=ProductionPromptStage.POST,
    )
    video = compile_production_method_skill_prompt(
        (method_id,), roles=(ProductionSkillRole.VIDEO_PROMPT_COMPILER,),
        stage=ProductionPromptStage.VISUAL_GEN,
    )
    assert "## 当前作品与最小修复" in post
    assert "## 后期判断" in post
    assert "## 视频编译" in video
    for prompt in (post, video):
        assert concept not in prompt
        assert director not in prompt


def test_method_combination_loads_current_role_once_without_overview() -> None:
    selected_ids = ("method.lyric_video", "method.paper_collage_explainer")
    prompt = compile_production_method_skill_prompt(
        (*selected_ids, selected_ids[0]), roles=(ProductionSkillRole.DIRECTOR,),
        stage=ProductionPromptStage.STORYBOARD,
    )
    assert prompt.count("按当前角色与阶段读取相关方法") == 1
    assert prompt.count("方法提供可选择的制片经验") == 1
    for method_id in selected_ids:
        method = production_method_skill(method_id)
        assert prompt.count(method.role_prompts[ProductionSkillRole.DIRECTOR]) == 1
        assert method.constitution not in prompt
        assert method.role_prompts[ProductionSkillRole.PLANNER] not in prompt
