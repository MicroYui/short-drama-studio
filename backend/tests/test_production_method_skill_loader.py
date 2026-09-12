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


def test_method_catalog_keeps_existing_adapters_and_loads_added_methods() -> None:
    loaded = load_production_method_skills()
    catalog = production_method_skill_catalog()

    assert OFFICIAL_MINIMAX_METHOD_IDS <= {skill.id for skill in loaded}
    assert {descriptor.id for descriptor in catalog} == {skill.id for skill in loaded}
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
        if descriptor.id in OFFICIAL_MINIMAX_METHOD_IDS
    } == EXPECTED_METHOD_PROVENANCE
    assert all(descriptor.upstream_sources for descriptor in catalog)
    assert all(descriptor.user_overridable for descriptor in catalog)
    skill_paths = tuple(DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT.glob("*/SKILL.md"))
    assert len(skill_paths) == len(catalog)
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
    assert "## 创意与玩家信息" in game_intro.role_prompts[
        ProductionSkillRole.CONCEPT
    ]
    assert "## 视频编译" in game_intro.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "身份、昵称、位置与装备对应" in game_intro.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "## 镜头规划" in animation.role_prompts[
        ProductionSkillRole.PLANNER
    ]
    assert "## 镜头规划" not in animation.role_prompts[
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
    assert "Leader → Worker → Verifier" not in format_only.text
    assert "无需先获得 Verifier 的通过标记" in format_only.text
    assert "## 视觉设定" not in format_only.text
    assert "次表面散射" not in format_only.text

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
    assert "## 视觉设定" in animation.role_prompts[
        ProductionSkillRole.CONCEPT
    ]


def test_drama_method_role_sources_include_storyboard_execution_and_verification() -> None:
    drama = production_method_skill("method.drama_series")
    planner = drama.prompt_for(ProductionSkillRole.ASSET_PLANNER, stage=ProductionPromptStage.ASSETS)
    builder = drama.prompt_for(ProductionSkillRole.ASSET_BUILDER, stage=ProductionPromptStage.ASSETS)
    verifier = drama.prompt_for(ProductionSkillRole.ASSET_VERIFIER, stage=ProductionPromptStage.ASSETS)
    assert planner is not None and builder is not None and verifier is not None
    assert "## 资产规划" in planner
    assert "完整/破损、空/已装物、封闭/打开是剧情状态" in planner
    assert "缺少必要可见证据才从可用版本派生" in planner
    assert "# 用户源图片的登记与人物锚定" in planner
    assert "单张脸照或单角度人像也可以足够" in planner
    assert "## 资产制作" in builder
    assert "# 可选角色卡与布局参考" in builder
    assert "LAYOUT_GUIDE/GUIDE_ONLY" in builder
    assert "SUBJECT_IDENTITY/AUTHORITATIVE" in builder
    assert "drama-character-sheet-three-view-20260701.png" in builder
    assert "只有确实发送的 guide 才能" in builder
    assert "没有规定每个人物都要双区卡" in builder
    assert "image.compose_reference_sheet" in builder
    assert "不能仅因 LEFT/RIGHT 枚举名" in builder
    assert "保留精确当前版本作为身份输入" in builder
    assert "独立 TEXT_PLATE" in builder
    assert "不得只在计划中备注“没有模板”就继续" not in builder
    assert "## 资产检查" in verifier
    assert "多角色参考分别比对主要识别点" in verifier
    assert "# 可选角色卡与布局参考" not in verifier
    assert "## 资产规划" not in builder
    assert "## 资产制作" not in verifier

    animation = production_method_skill("method.animation_short")
    for role, heading in (
        (ProductionSkillRole.ASSET_PLANNER, "## 资产规划"),
        (ProductionSkillRole.ASSET_BUILDER, "## 资产制作"),
        (ProductionSkillRole.ASSET_VERIFIER, "## 资产检查"),
    ):
        assert heading in animation.role_prompts[role]
    assert len({animation.role_prompts[role] for role in (
        ProductionSkillRole.ASSET_PLANNER,
        ProductionSkillRole.ASSET_BUILDER,
        ProductionSkillRole.ASSET_VERIFIER,
    )}) == 3

    director = drama.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "## 镜头语言与情绪" in director
    assert "## 表演与物理动作" in director
    assert "## 空间与状态连续性" in director
    verifier = drama.role_prompts[ProductionSkillRole.REVISION_VERIFIER]
    assert "## 当前范围验收" in verifier
    assert "## 渐进分镜与可选检查" in verifier
    assert "## 空间与状态连续性" in verifier
    assert "## 唯一编译路径：剧本 → 时长账本 → 完整分镜" not in verifier


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
    assert "## 资产规划" in asset_planner.text
    assert "## 唯一编译路径：剧本 → 时长账本 → 完整分镜" not in (
        asset_planner.text
    )
    assert "## 剧本写入当前项目" not in asset_planner.text
    assert "## 渐进分镜与可选检查" in storyboard_verifier.text
    assert "## storyboard-confirm 前分组检查清单" not in storyboard_verifier.text
    assert "## 剧本写入当前项目" not in storyboard_verifier.text
    assert "## 剧本写入当前项目" in script_verifier.text
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
            "::视觉设定",
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
    assert text.count("按当前角色与阶段读取相关方法") == 1
    assert text.count("方法提供可选择的制片经验") == 1
    assert "## 创作大纲" in text
    assert "方法提供可选择的制片经验" in text[text.index("## 创作大纲"):]


def test_method_manifest_round_trip_preserves_existing_ids(tmp_path: Path) -> None:
    copied_root = _copy_skill_tree(tmp_path)
    methods = load_production_method_skills(copied_root)
    assert {method.id for method in methods} == {item.id for item in production_method_skill_catalog()}
    source = copied_root / "drama_series" / "SKILL.md"
    assert max(map(len, source.read_text().splitlines()[:6])) < 200
    # A user's ordinary Markdown metadata override remains readable.
    source.write_text(source.read_text().replace("manifest: manifest.json", "label: 本地短剧方法\nmanifest: manifest.json"))
    updated = load_production_method_skills(copied_root)
    assert next(item for item in updated if item.id == "method.drama_series").label == "本地短剧方法"
