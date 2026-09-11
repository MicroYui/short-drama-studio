"""Regression tests for Skills vendored from the official MiniMax-H3 repository."""

from __future__ import annotations

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_method_skill_prompt,
    compile_production_prompt,
    production_method_skill,
)
from short_drama.skills.loader import DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT
from short_drama.skills.models import SkillProvenanceKind


UPSTREAM_COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"

METHOD_DIRECTORIES = {
    "method.animation_short": "3d-animation-short-generator",
    "method.brand_promo": "brand-promo-video-generator",
    "method.co_op_game_intro": "co-op-game-intro-generator",
    "method.handdrawn_live": "handdrawn-live-video-generator",
    "method.minimalist_product_ad": "minimalist-product-ad-generator",
    "method.lyric_video": "music-video-subtitle-generator",
    "method.paper_collage_explainer": "paper-collage-explainer-generator",
    "method.papercraft_stop_motion": (
        "papercraft-stop-motion-explainer"
    ),
}

EXPECTED_UPSTREAM_ATTRIBUTION = {
    "3d-animation-short-generator": (
        "community",
        "MiniMax Hub User",
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER,
    ),
    "brand-promo-video-generator": (
        "official-featured",
        "MiniMax Hub",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER,
    ),
    "co-op-game-intro-generator": (
        "official",
        "MiniMax Hub",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER,
    ),
    "handdrawn-live-video-generator": (
        "official-featured",
        "MiniMax Hub User",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER,
    ),
    "minimalist-product-ad-generator": (
        "community",
        "User",
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER,
    ),
    "music-video-subtitle-generator": (
        "official-featured",
        "MiniMax Hub User",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER,
    ),
    "paper-collage-explainer-generator": (
        "official-featured",
        "MiniMax Hub",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER,
    ),
    "papercraft-stop-motion-explainer": (
        "official",
        "MiniMax Hub",
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER,
    ),
}

RETAINED_SOURCE_FILE_PATHS = {
    "3d-animation-short-generator/meta.yaml",
    "3d-animation-short-generator/SKILL.cn.md",
    "3d-animation-short-generator/references/qc-checklist.md",
    "3d-animation-short-generator/references/storyboard-guidelines.md",
    "brand-promo-video-generator/meta.yaml",
    "brand-promo-video-generator/SKILL.cn.md",
    "co-op-game-intro-generator/meta.yaml",
    "co-op-game-intro-generator/SKILL.cn.md",
    "co-op-game-intro-generator/references/h3-confirmation-image-template.md",
    "co-op-game-intro-generator/references/h3-video-prompt-template.md",
    "handdrawn-live-video-generator/meta.yaml",
    "handdrawn-live-video-generator/SKILL.cn.md",
    "minimalist-product-ad-generator/meta.yaml",
    "minimalist-product-ad-generator/SKILL.cn.md",
    "music-video-subtitle-generator/meta.yaml",
    "music-video-subtitle-generator/SKILL.cn.md",
    "paper-collage-explainer-generator/meta.yaml",
    "paper-collage-explainer-generator/SKILL.cn.md",
    "papercraft-stop-motion-explainer/meta.yaml",
    "papercraft-stop-motion-explainer/SKILL.cn.md",
}


def _top_level_meta_values(source_directory: str) -> dict[str, str]:
    values: dict[str, str] = {}
    path = DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT / source_directory / "meta.yaml"
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw_value = line.partition(":")
        if separator and key in {"source", "author-en"}:
            values[key] = raw_value.strip().strip('"')
    return values


def test_retained_sources_keep_their_upstream_inventory_and_attribution() -> None:
    actual_files = {
        path.relative_to(DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT).as_posix()
        for path in DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT.rglob("*")
        if path.is_file()
    }

    assert actual_files == RETAINED_SOURCE_FILE_PATHS

    for method_id, upstream_directory in METHOD_DIRECTORIES.items():
        method = production_method_skill(method_id)
        source, author, expected_provenance = EXPECTED_UPSTREAM_ATTRIBUTION[
            upstream_directory
        ]
        assert _top_level_meta_values(upstream_directory) == {
            "source": source,
            "author-en": author,
        }
        assert method.provenance == expected_provenance
        assert any(
            UPSTREAM_COMMIT in upstream_source
            and f"skills/{upstream_directory}/SKILL.cn.md" in upstream_source
            for upstream_source in method.upstream_sources
        )
        assert any(
            UPSTREAM_COMMIT in upstream_source
            and f"skills/{upstream_directory}/meta.yaml" in upstream_source
            and f"source={source}" in upstream_source
            and f"author-en={author}" in upstream_source
            for upstream_source in method.upstream_sources
        )


@pytest.mark.parametrize(
    ("method_id", "source_directory", "upstream_directive", "selected_heading"),
    (
        (
            "method.minimalist_product_ad",
            "minimalist-product-ad-generator",
            "启动问询是强制 Gate",
            "## 产品事实与叙事",
        ),
        (
            "method.animation_short",
            "3d-animation-short-generator",
            "同一镜连续 3 次失败后",
            "## 视觉设定",
        ),
        (
            "method.lyric_video",
            "music-video-subtitle-generator",
            "必须写入 Hub 画布文本节点",
            "## 创意与音乐依据",
        ),
    ),
)
def test_upstream_product_directives_stay_on_disk_but_out_of_compiled_prompts(
    method_id: str,
    source_directory: str,
    upstream_directive: str,
    selected_heading: str,
) -> None:
    source = (
        DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT
        / source_directory
        / "SKILL.cn.md"
    ).read_text(encoding="utf-8")
    prompt = compile_production_method_skill_prompt(
        (method_id,),
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert upstream_directive in source
    assert upstream_directive not in prompt
    assert selected_heading in prompt
    assert "<official_method_reference" not in prompt
    assert "source=\"" not in prompt
    assert "不伪造目录中不存在的调用" in prompt


def test_upstream_tool_declarations_remain_source_evidence_not_harness_tools() -> None:
    source_path = (
        DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT
        / "brand-promo-video-generator"
        / "SKILL.cn.md"
    )
    source = source_path.read_text(encoding="utf-8")
    prompt = compile_production_method_skill_prompt(
        ("method.brand_promo",),
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert "hub_image_search" in source
    assert "hub_image_search" not in prompt
    assert "hub_canvas_get_node" not in prompt
    assert "不伪造目录中不存在的调用" in prompt
    assert "不伪造目录中不存在的调用" in prompt


def test_official_3d_loads_only_explicit_sections_for_each_supported_role() -> None:
    prompts = {
        role: compile_production_method_skill_prompt(
            ("method.animation_short",),
            roles=(role,),
        )
        for role in (
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.PLANNER,
            ProductionSkillRole.CRITIC,
            ProductionSkillRole.STORY_REPAIR,
            ProductionSkillRole.REVISION_VERIFIER,
            ProductionSkillRole.VIDEO_PROMPT_COMPILER,
            ProductionSkillRole.EPISODE_REVIEWER,
        )
    }

    concept = prompts[ProductionSkillRole.CONCEPT]
    assert "<official_method_reference" not in concept
    assert concept.count("## 视觉设定") == 1
    assert "## 视觉设定" in concept
    assert "## 镜头规划" not in concept

    planner = prompts[ProductionSkillRole.PLANNER]
    assert planner.count("## 镜头规划") == 1
    assert "## 镜头规划" in planner
    assert "## STEP 5.5：镜头表自检门（强制）" not in planner

    assert prompts[ProductionSkillRole.CRITIC] == ""
    assert prompts[ProductionSkillRole.STORY_REPAIR] == ""

    verifier = prompts[ProductionSkillRole.REVISION_VERIFIER]
    assert "<official_method_reference" not in verifier
    assert "## 当前范围验收" in verifier
    assert "最小处理方式" in verifier
    assert "## STEP 9：最终复盘" not in verifier
    assert "## 步骤 9：最终审核" not in verifier

    compiler = prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]
    assert "<official_method_reference" not in compiler
    assert "## 视频编译" in compiler
    assert "挤压拉伸、跟随、节奏停顿和轮廓对比" in compiler
    assert "### 模型相关 prompt 形态" not in compiler
    assert "# 视频模型选择与提示词塑形" not in compiler
    assert "# 生成失败与漂移回退策略" not in compiler

    episode_reviewer = prompts[ProductionSkillRole.EPISODE_REVIEWER]
    assert "## 后期判断" in episode_reviewer
    assert "预备动作是否保留" in episode_reviewer
    assert "## STEP 9：最终复盘" not in episode_reviewer
    assert "## 步骤 9：最终审核" not in episode_reviewer

    assert all(
        "# 3D动画短片生成器" not in prompt
        for prompt in prompts.values()
    )


def test_ai_short_drama_and_official_3d_are_composable_but_not_coupled() -> None:
    unselected = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert unselected.selected_method_skill_ids == ()
    assert "AI 短剧（本地格式适配）" in unselected.text
    assert "`AI_SHORT_DRAMA` 数据边界" in unselected.text
    assert "Leader 依据用户目标安排分工" in unselected.text
    assert "独立检查按实际需要安排，也可以由制作者自查" in unselected.text
    assert "Leader → Worker → Verifier" not in unselected.text
    assert "## 视觉设定" not in unselected.text
    assert "次表面散射" not in unselected.text

    composed = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
        method_skill_ids=("method.animation_short",),
    )

    assert composed.selected_method_skill_ids == (
        "method.animation_short",
    )
    assert "AI 短剧（本地格式适配）" in composed.text
    assert "## 视觉设定" in composed.text
    assert "次表面散射" in composed.text
    assert "# 3D动画短片生成器" not in composed.text
