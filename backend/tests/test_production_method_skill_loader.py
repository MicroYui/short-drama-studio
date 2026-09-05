"""Regression coverage for Agent-selected MiniMax Production Methods."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_method_skill_prompt,
    compile_production_prompt,
    production_method_skill,
    production_method_skill_catalog,
)
from short_drama.skills.loader import (
    DEFAULT_MINIMAX_H3_SKILL_TRANSLATIONS_ROOT,
    DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT,
    DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT,
    load_production_method_skills,
)
from short_drama.skills.models import SkillProvenanceKind


OFFICIAL_MINIMAX_METHOD_IDS = {
    "method.minimax_design.ad_tvc",
    "method.minimax_design.drama_series",
    "method.minimax_design.mv",
    "method.minimax_h3.3d_animation_short",
    "method.minimax_h3.brand_promo_video",
    "method.minimax_h3.co_op_game_intro",
    "method.minimax_h3.handdrawn_live_video",
    "method.minimax_h3.minimalist_product_ad",
    "method.minimax_h3.music_video_subtitle",
    "method.minimax_h3.paper_collage_explainer",
    "method.minimax_h3.papercraft_stop_motion_explainer",
}

EXPECTED_METHOD_PROVENANCE = {
    "method.minimax_design.ad_tvc": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.minimax_design.drama_series": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.minimax_design.mv": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.minimax_h3.3d_animation_short": (
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER
    ),
    "method.minimax_h3.brand_promo_video": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.minimax_h3.co_op_game_intro": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER
    ),
    "method.minimax_h3.handdrawn_live_video": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.minimax_h3.minimalist_product_ad": (
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER
    ),
    "method.minimax_h3.music_video_subtitle": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.minimax_h3.paper_collage_explainer": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.minimax_h3.papercraft_stop_motion_explainer": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER
    ),
}


def _copy_skill_tree(tmp_path: Path) -> Path:
    copied_root = tmp_path / "skills"
    shutil.copytree(DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT.parent, copied_root)
    return copied_root / "methods"


def test_method_catalog_contains_only_the_upstream_role_scoped_adapters() -> None:
    loaded = load_production_method_skills()
    catalog = production_method_skill_catalog()

    assert {skill.id for skill in loaded} == OFFICIAL_MINIMAX_METHOD_IDS
    assert {descriptor.id for descriptor in catalog} == OFFICIAL_MINIMAX_METHOD_IDS
    assert all(descriptor.description for descriptor in catalog)
    assert all(descriptor.supported_roles for descriptor in catalog)
    assert all(
        not any(
            marker in f"{descriptor.label}\n{descriptor.description}"
            for marker in ("官方发布", "官方精选", "社区收录")
        )
        for descriptor in catalog
    )
    assert {
        descriptor.id: descriptor.provenance for descriptor in catalog
    } == EXPECTED_METHOD_PROVENANCE
    assert all(descriptor.upstream_sources for descriptor in catalog)
    assert all(descriptor.user_overridable for descriptor in catalog)
    skill_paths = tuple(DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT.glob("*/SKILL.md"))
    assert len(skill_paths) == len(OFFICIAL_MINIMAX_METHOD_IDS)
    for skill_path in skill_paths:
        assert "trigger-words" not in skill_path.read_text(encoding="utf-8")


def test_minimax_methods_load_selected_chinese_source_sections() -> None:
    expected_directories = {
        "3d-animation-short-generator",
        "brand-promo-video-generator",
        "co-op-game-intro-generator",
        "handdrawn-live-video-generator",
        "minimalist-product-ad-generator",
        "music-video-subtitle-generator",
        "paper-collage-explainer-generator",
        "papercraft-stop-motion-explainer",
    }

    assert {
        path.name
        for path in DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT.iterdir()
        if path.is_dir()
    } == expected_directories
    assert all(
        (DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT / directory / "SKILL.cn.md").is_file()
        for directory in expected_directories
    )
    assert (
        DEFAULT_MINIMAX_H3_SKILL_TRANSLATIONS_ROOT
        / "3d-animation-short-generator"
        / "references"
        / "shot-table-spec.zh_cn.md"
    ).is_file()
    assert (
        DEFAULT_MINIMAX_H3_SKILL_TRANSLATIONS_ROOT
        / "co-op-game-intro-generator"
        / "references"
        / "h3-video-prompt-template.zh_cn.md"
    ).is_file()

    game_intro = production_method_skill("method.minimax_h3.co_op_game_intro")
    animation = production_method_skill("method.minimax_h3.3d_animation_short")

    assert game_intro.provenance == SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER
    assert (
        animation.provenance
        == SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER
    )
    assert "# 双人游戏开场视频生成器" not in game_intro.constitution
    assert "## STEP 1：询问视觉风格" in game_intro.role_prompts[
        ProductionSkillRole.CONCEPT
    ]
    assert "## 提示词原则" in game_intro.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "## STEP 5：六列标准镜头信息表" in animation.role_prompts[
        ProductionSkillRole.PLANNER
    ]
    assert "## STEP 5：六列标准镜头信息表" not in animation.role_prompts[
        ProductionSkillRole.CONCEPT
    ]


def test_ai_short_drama_schema_adapter_composes_with_official_method() -> None:
    format_only = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert format_only.selected_method_skill_ids == ()
    assert "AI 短剧（本地格式适配）" in format_only.text
    assert "`AI_SHORT_DRAMA` 数据边界" in format_only.text
    assert "Leader → Worker → Verifier" in format_only.text
    assert "## 全局视觉风格锁" not in format_only.text
    assert "皮克斯感 3D 卡通渲染" not in format_only.text

    composed = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
        method_skill_ids=("method.minimax_h3.3d_animation_short",),
    )

    assert composed.selected_method_skill_ids == (
        "method.minimax_h3.3d_animation_short",
    )
    assert "AI 短剧（本地格式适配）" in composed.text
    assert "## 全局视觉风格锁" in composed.text
    assert "皮克斯感 3D 卡通渲染" in composed.text
    assert "# 3D动画短片生成器" not in composed.text


def test_method_loader_reads_adapter_markdown_without_python_catalog_edits(
    tmp_path: Path,
) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    skill_path = copied_root / "minimax_h3_3d_animation_short" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8")
        + "\n\n本测试指定的官方 3D 方法适配补充。\n",
        encoding="utf-8",
    )

    methods = load_production_method_skills(copied_root)
    animation = next(
        skill
        for skill in methods
        if skill.id == "method.minimax_h3.3d_animation_short"
    )

    assert "本测试指定的官方 3D 方法适配补充。" in animation.constitution


def test_method_selection_is_explicit_and_deduplicated() -> None:
    unselected = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO,
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert unselected.selected_method_skill_ids == ()
    assert "Agent 已根据当前项目完整语义选择制片方法技能" not in unselected.text

    compiled = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO,
        roles=(ProductionSkillRole.CONCEPT,),
        method_skill_ids=(
            "method.minimax_h3.brand_promo_video",
            "method.minimax_h3.3d_animation_short",
            "method.minimax_h3.brand_promo_video",
        ),
    )

    assert compiled.selected_method_skill_ids == (
        "method.minimax_h3.brand_promo_video",
        "method.minimax_h3.3d_animation_short",
    )
    assert compiled.selected_skill_ids == ()
    assert "MiniMax-H3：品牌宣传短片" in compiled.text
    assert "MiniMax-H3：3D 动画短片" in compiled.text


def test_method_loader_validates_id_but_silently_skips_unsupported_roles() -> None:
    with pytest.raises(ValueError, match="Unknown Production Method Skill"):
        compile_production_method_skill_prompt(
            ("method.not_found",),
            roles=(ProductionSkillRole.DIRECTOR,),
        )

    unsupported_prompt = compile_production_method_skill_prompt(
        ("method.minimax_h3.3d_animation_short",),
        roles=(ProductionSkillRole.WRITER,),
    )
    assert unsupported_prompt == ""

    compiled = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.WRITER,),
        method_skill_ids=("method.minimax_h3.3d_animation_short",),
    )
    assert compiled.selected_method_skill_ids == (
        "method.minimax_h3.3d_animation_short",
    )
    assert "MiniMax-H3：3D 动画短片" not in compiled.text
    assert compile_production_method_skill_prompt((), roles=()) == ""


def test_official_method_uses_declared_roles_without_local_role_files() -> None:
    animation = production_method_skill("method.minimax_h3.3d_animation_short")
    source_directory = Path(animation.source_path).parent

    assert animation.supports(ProductionSkillRole.CONCEPT)
    assert not animation.supports(ProductionSkillRole.WRITER)
    assert not (source_directory / "roles").exists()
    assert "## 全局视觉风格锁" in animation.role_prompts[
        ProductionSkillRole.CONCEPT
    ]


def test_drama_method_role_sources_include_storyboard_execution_and_verification() -> None:
    drama = production_method_skill("method.minimax_design.drama_series")

    director_prompt = drama.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "## 镜头组：拆分整集" in director_prompt
    assert "## 对白：语速、负载和拆分" in director_prompt

    verifier_prompt = drama.role_prompts[
        ProductionSkillRole.REVISION_VERIFIER
    ]
    assert "## 唯一编译路径：剧本 → 时长账本 → 完整分镜" in (
        verifier_prompt
    )
    assert "## storyboard-confirm 前分组检查清单" in verifier_prompt
    assert "## 跨镜头空间连续性" in verifier_prompt
    assert "## 状态连续性" in verifier_prompt


def test_method_loader_rejects_duplicate_ids_and_missing_source_sections(
    tmp_path: Path,
) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    duplicate_path = copied_root / "minimax_h3_co_op_game_intro" / "SKILL.md"
    duplicate_path.write_text(
        duplicate_path.read_text(encoding="utf-8").replace(
            "method.minimax_h3.co_op_game_intro",
            "method.minimax_h3.3d_animation_short",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate Production Method Skill id"):
        load_production_method_skills(copied_root)

    shutil.rmtree(copied_root.parent)
    copied_root = _copy_skill_tree(tmp_path)
    invalid_section_path = (
        copied_root / "minimax_h3_3d_animation_short" / "SKILL.md"
    )
    invalid_section_path.write_text(
        invalid_section_path.read_text(encoding="utf-8").replace(
            "::全局视觉风格锁",
            "::不存在的官方章节",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must appear exactly once"):
        load_production_method_skills(copied_root)
