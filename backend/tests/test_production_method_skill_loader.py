"""Regression coverage for Agent-selected MiniMax Production Methods."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionPromptStage,
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
    "method.advertising",
    "method.drama_series",
    "method.music_video",
    "method.animation_short",
    "method.brand_promo",
    "method.co_op_game_intro",
    "method.handdrawn_live",
    "method.minimalist_product_ad",
    "method.lyric_video",
    "method.paper_collage_explainer",
    "method.papercraft_stop_motion",
}

EXPECTED_METHOD_PROVENANCE = {
    "method.advertising": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.drama_series": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.music_video": (
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
    ),
    "method.animation_short": (
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER
    ),
    "method.brand_promo": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.co_op_game_intro": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER
    ),
    "method.handdrawn_live": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.minimalist_product_ad": (
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER
    ),
    "method.lyric_video": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.paper_collage_explainer": (
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
    ),
    "method.papercraft_stop_motion": (
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
        / "storyboard-guidelines.zh_cn.md"
    ).is_file()
    assert (
        DEFAULT_MINIMAX_H3_SKILL_TRANSLATIONS_ROOT
        / "co-op-game-intro-generator"
        / "references"
        / "h3-video-prompt-template.zh_cn.md"
    ).is_file()

    game_intro = production_method_skill("method.co_op_game_intro")
    animation = production_method_skill("method.animation_short")

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
        method_skill_ids=("method.animation_short",),
    )

    assert composed.selected_method_skill_ids == (
        "method.animation_short",
    )
    assert "AI 短剧（本地格式适配）" in composed.text
    assert "## 全局视觉风格锁" in composed.text
    assert "皮克斯感 3D 卡通渲染" in composed.text
    assert "# 3D动画短片生成器" not in composed.text


def test_method_loader_reads_adapter_markdown_without_python_catalog_edits(
    tmp_path: Path,
) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    skill_path = copied_root / "animation_short" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8")
        + "\n\n本测试指定的官方 3D 方法适配补充。\n",
        encoding="utf-8",
    )

    methods = load_production_method_skills(copied_root)
    animation = next(
        skill
        for skill in methods
        if skill.id == "method.animation_short"
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
            "method.brand_promo",
            "method.animation_short",
            "method.brand_promo",
        ),
    )

    assert compiled.selected_method_skill_ids == (
        "method.brand_promo",
        "method.animation_short",
    )
    assert compiled.selected_skill_ids == ()
    assert "品牌宣传片" in compiled.text
    assert "3D 动画短片" in compiled.text


def test_method_loader_validates_id_but_silently_skips_unsupported_roles() -> None:
    with pytest.raises(ValueError, match="Unknown Production Method Skill"):
        compile_production_method_skill_prompt(
            ("method.not_found",),
            roles=(ProductionSkillRole.DIRECTOR,),
        )

    unsupported_prompt = compile_production_method_skill_prompt(
        ("method.animation_short",),
        roles=(ProductionSkillRole.WRITER,),
    )
    assert unsupported_prompt == ""

    compiled = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.WRITER,),
        method_skill_ids=("method.animation_short",),
    )
    assert compiled.selected_method_skill_ids == (
        "method.animation_short",
    )
    assert "3D 动画短片" not in compiled.text
    assert compile_production_method_skill_prompt((), roles=()) == ""


def test_official_method_uses_declared_roles_without_local_role_files() -> None:
    animation = production_method_skill("method.animation_short")
    source_directory = Path(animation.source_path).parent

    assert animation.supports(ProductionSkillRole.CONCEPT)
    assert not animation.supports(ProductionSkillRole.WRITER)
    assert not (source_directory / "roles").exists()
    assert "## 全局视觉风格锁" in animation.role_prompts[
        ProductionSkillRole.CONCEPT
    ]


def test_drama_method_role_sources_include_storyboard_execution_and_verification() -> None:
    drama = production_method_skill("method.drama_series")

    asset_prompt = drama.role_prompts[ProductionSkillRole.ASSET_REVIEWER]
    assert "# 用户源图片的登记与人物锚定" in asset_prompt
    assert "权威 identity reference" in asset_prompt
    assert "完整角色卡" in asset_prompt
    assert "不默认将它绑定所有镜头" in asset_prompt
    assert "# 官方角色卡布局参考的本地映射" in asset_prompt
    assert "reference_role=LAYOUT_GUIDE" in asset_prompt
    assert "drama-character-sheet-three-view-20260701.png" in asset_prompt
    assert "不得只在计划中备注“没有模板”就继续" in asset_prompt
    assert "路线 A：布局 guide 引导的一图成卡" in asset_prompt
    assert "路线 B：分视图生成/导入后本地确定性拼板" in asset_prompt
    assert "不扮演像素级测量门禁" in asset_prompt
    assert "image.compose_reference_sheet" in asset_prompt
    assert "旧版 AssetPlan 或中断恢复时都重新语义核对" in asset_prompt
    assert "转成真实 `reference_inputs`" in asset_prompt
    assert "不得仍把无参考 `GENERATE` 称为官方标准一图成卡" in (
        asset_prompt
    )
    assert "只有左右存在可识别的非对称身份特征" in asset_prompt
    assert "不仅因 `requested_views` 中使用了 LEFT/RIGHT 枚举名" in (
        asset_prompt
    )
    assert "定向修复保留已有身份" in asset_prompt
    assert "作为 `parent_version_id` 和精确 identity `reference_input`" in (
        asset_prompt
    )
    assert "一件可干净裁用的无字物理母版" in asset_prompt
    assert "各使用处引用同一精确版本" in asset_prompt
    assert "多件像素级一致" in asset_prompt
    assert "轻微尺寸或透视差不是全量返修理由" in asset_prompt

    planner_prompt = drama.prompt_for(
        ProductionSkillRole.ASSET_PLANNER,
        stage=ProductionPromptStage.ASSETS,
    )
    builder_prompt = drama.prompt_for(
        ProductionSkillRole.ASSET_BUILDER,
        stage=ProductionPromptStage.ASSETS,
    )
    asset_verifier_prompt = drama.prompt_for(
        ProductionSkillRole.ASSET_VERIFIER,
        stage=ProductionPromptStage.ASSETS,
    )
    assert planner_prompt is not None
    assert builder_prompt is not None
    assert asset_verifier_prompt is not None
    assert "## 建卡启发式规则" in planner_prompt
    assert "## Base-First Dependency Chain" in planner_prompt
    assert "## 角色设定图 Prompt 模板" in builder_prompt
    assert "## 站位质量" not in builder_prompt
    assert "## 站位质量" in asset_verifier_prompt
    assert "## 角色设定图 Prompt 模板" not in asset_verifier_prompt
    assert "# 用户源图片的登记与人物锚定" in planner_prompt
    assert "# 用户源图片的登记与人物锚定" in builder_prompt
    assert "# 用户源图片的登记与人物锚定" in asset_verifier_prompt
    assert len(builder_prompt) < len(asset_prompt)
    assert len(asset_verifier_prompt) < len(asset_prompt)

    animation = production_method_skill("method.animation_short")
    for role in (
        ProductionSkillRole.ASSET_PLANNER,
        ProductionSkillRole.ASSET_BUILDER,
        ProductionSkillRole.ASSET_VERIFIER,
    ):
        assert animation.role_prompts[role] == animation.role_prompts[
            ProductionSkillRole.ASSET_REVIEWER
        ]

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


def test_drama_method_compiles_only_the_explicit_stage_and_role_sections() -> None:
    method_ids = ("method.drama_series",)
    asset_planner = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.ASSET_PLANNER,),
        include_constitution=False,
        method_skill_ids=method_ids,
        stage=ProductionPromptStage.ASSETS,
    )
    storyboard_verifier = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
        include_constitution=False,
        method_skill_ids=method_ids,
        stage=ProductionPromptStage.STORYBOARD,
    )
    script_verifier = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
        include_constitution=False,
        method_skill_ids=method_ids,
        stage=ProductionPromptStage.STORY,
    )

    assert asset_planner.stage == ProductionPromptStage.ASSETS
    assert "## 角色 Profile 维度" in asset_planner.text
    assert "## 唯一编译路径：剧本 → 时长账本 → 完整分镜" not in (
        asset_planner.text
    )
    assert "## 剧本落画布" not in asset_planner.text
    assert "## storyboard-confirm 前分组检查清单" in (
        storyboard_verifier.text
    )
    assert "## 剧本落画布" not in storyboard_verifier.text
    assert "## 剧本落画布" in script_verifier.text
    assert "## storyboard-confirm 前分组检查清单" not in (
        script_verifier.text
    )
    assert len(asset_planner.text) < 30_000


def test_method_loader_rejects_duplicate_ids_and_missing_source_sections(
    tmp_path: Path,
) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    duplicate_path = copied_root / "co_op_game_intro" / "manifest.json"
    duplicate_path.write_text(
        duplicate_path.read_text(encoding="utf-8").replace(
            "method.co_op_game_intro",
            "method.animation_short",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate Production Method Skill id"):
        load_production_method_skills(copied_root)

    shutil.rmtree(copied_root.parent)
    copied_root = _copy_skill_tree(tmp_path)
    invalid_section_path = (
        copied_root / "animation_short" / "manifest.json"
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


def test_shared_method_guidance_is_once_across_methods_and_roles() -> None:
    text = compile_production_method_skill_prompt(
        ["method.drama_series", "method.brand_promo"],
        roles=[ProductionSkillRole.CONCEPT, ProductionSkillRole.PLANNER],
    )
    assert text.count("这里同时服务两层来源方法") == 1
    assert text.count("上游原文提供成熟的制片经验") == 1
    assert "## 创作大纲" in text
    assert "上游原文提供成熟的制片经验" in text[text.index("## 创作大纲"):]


def test_method_manifest_round_trip_preserves_existing_ids(tmp_path: Path) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    methods = load_production_method_skills(copied_root)
    assert {method.id for method in methods} == OFFICIAL_MINIMAX_METHOD_IDS
    source = copied_root / "drama_series" / "SKILL.md"
    assert max(map(len, source.read_text().splitlines()[:6])) < 200
    # A user's ordinary Markdown metadata override remains readable.
    source.write_text(source.read_text().replace("manifest: manifest.json", "label: 本地短剧方法\nmanifest: manifest.json"))
    updated = load_production_method_skills(copied_root)
    assert next(item for item in updated if item.id == "method.drama_series").label == "本地短剧方法"
