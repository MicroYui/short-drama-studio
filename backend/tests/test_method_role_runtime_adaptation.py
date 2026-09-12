"""Exercise the actual local method bodies, including stage overrides."""

from __future__ import annotations

import json

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
    production_method_skill,
    production_method_skill_catalog,
)
from short_drama.skills.loader import DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT


METHODS = (
    "advertising", "animation_short", "brand_promo", "minimalist_product_ad",
    "music_video", "drama_series",
)

OBSOLETE_INSTRUCTIONS = (
    "hub_analyse_media", "hub_canvas_write_node", "hub_canvas_get_node",
    "Compact Downstream Capsule", "StageCapsule", "selection_status",
    "execution_locks", "runtime_refs", "review.before_execution",
    "generation_authorization_unit", "不允许换分析工具", "启动问询是强制 Gate",
    "Gate 2", "必须两轮轻交互", "target_episode_scope", "恰好 3 个",
    "五区模板；必须", "必须传入已登记的官方 layout guide",
    "Clip N 的末态快照逐项成为 Clip N+1 的起始快照",
    "## STEP ", "## Step ", "## Prompt Skeleton", "## Base-First Dependency Chain",
    "固定六段骨架", "H3 唯一可执行填空骨架", "prompt_section_titles",
)


@pytest.mark.parametrize("method_name", METHODS)
def test_all_live_roles_and_stages_use_executable_method_sections(method_name: str) -> None:
    method_id = f"method.{method_name}"
    method = production_method_skill(method_id)
    for role in ProductionSkillRole:
        for stage in (None, *ProductionPromptStage):
            body = method.prompt_for(role, stage=stage)
            if body is None:
                continue
            assert "正常失败例" in body, (method_name, role, stage)
            prompt = compile_production_prompt(
                ProductionProfile.AI_SHORT_DRAMA, roles=(role,),
                method_skill_ids=(method_id,), stage=stage, include_constitution=False,
            ).text
            assert body in prompt
            assert method.constitution not in prompt
            assert "按当前角色与阶段读取相关方法" in prompt
            assert "方法提供可选择的制片经验" in prompt
            for obsolete in OBSOLETE_INSTRUCTIONS:
                assert obsolete not in prompt, (method_name, role, stage, obsolete)


@pytest.mark.parametrize("method_name", METHODS)
def test_asset_responsibilities_are_explicit_and_do_not_inherit_a_whole_workflow(method_name: str) -> None:
    method = production_method_skill(f"method.{method_name}")
    bodies: set[str] = set()
    for role, heading in (
        (ProductionSkillRole.ASSET_PLANNER, "资产规划"),
        (ProductionSkillRole.ASSET_BUILDER, "资产制作"),
        (ProductionSkillRole.ASSET_VERIFIER, "资产检查"),
    ):
        body = method.prompt_for(role, stage=ProductionPromptStage.ASSETS)
        assert body is not None
        assert f"## {heading}" in body
        bodies.add(body)
    assert len(bodies) == 3


@pytest.mark.parametrize(
    ("method_name", "role", "craft"),
    (
        ("advertising", ProductionSkillRole.CONCEPT, ("观众价值—可见动作—产品反应—结果", "概念片")),
        ("advertising", ProductionSkillRole.DIRECTOR, ("匹配切", "材质", "文字", "触发")),
        ("animation_short", ProductionSkillRole.CONCEPT, ("次表面散射", "轮廓", "重心转移")),
        ("brand_promo", ProductionSkillRole.PLANNER, ("产品证明", "实际项目帧率", "主要动作")),
        ("minimalist_product_ad", ProductionSkillRole.ASSET_PLANNER, ("不同用途", "主推款", "DRAFT")),
        ("music_video", ProductionSkillRole.PLANNER, ("实际主音轨", "歌词句界", "全局音乐窗口", "口型同步")),
        ("music_video", ProductionSkillRole.DIRECTOR, ("互补", "揭示—附着—跟随—遮挡—传递—落稳", "透视")),
        ("drama_series", ProductionSkillRole.DIRECTOR, ("互补的屏幕视线", "重心转移", "AU4", "设计终态冒充已拍事实")),
        ("drama_series", ProductionSkillRole.VIDEO_PROMPT_COMPILER, ("可见区别", "实际扮演关系", "不默认添加更多图片")),
    ),
)
def test_method_knowledge_stays_specific(
    method_name: str, role: ProductionSkillRole, craft: tuple[str, ...],
) -> None:
    body = production_method_skill(f"method.{method_name}").role_prompts[role]
    for detail in craft:
        assert detail in body


def test_every_catalog_method_keeps_sources_separate_from_live_local_chapters() -> None:
    for descriptor in production_method_skill_catalog():
        name = descriptor.id.removeprefix("method.")
        manifest = json.loads(
            (DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT / name / "manifest.json").read_text()
        )
        assert manifest["upstream_sources"]
        assert manifest["user_overridable"] is True
        for key in ("role_source_sections", "stage_role_source_sections", "role_references"):
            for references in manifest.get(key, {}).values():
                for reference in references:
                    assert "sources/" not in reference, (name, key, reference)
                    assert reference.startswith(("references/", "../../shared/"))
