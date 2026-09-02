"""Regression coverage for Markdown-first Production Skills."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from short_drama.models import CreateProjectRequest, ProductionProfile, SourceMode
from short_drama.production_planner import _planner_prompt
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
    production_skill_catalog,
)
from short_drama.skills.loader import (
    DEFAULT_PROVIDER_SKILLS_ROOT,
    DEFAULT_PRODUCTION_SKILLS_ROOT,
    load_provider_prompt_skills,
    load_production_skills,
)


def test_every_profile_is_compiled_from_reviewable_markdown() -> None:
    catalog = production_skill_catalog()

    assert tuple(skill.profile for skill in catalog) == tuple(ProductionProfile)
    for skill in catalog:
        directory = DEFAULT_PRODUCTION_SKILLS_ROOT / skill.profile.value.casefold()
        assert (directory / "SKILL.md").is_file()
        assert (directory / "playbooks" / "default.md").is_file()
        for role in ProductionSkillRole:
            assert (directory / "roles" / f"{role.value.casefold()}.md").is_file()


def test_packaged_backend_includes_runtime_markdown_skills() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    builder_config = (repository_root / "electron-builder.yml").read_text(
        encoding="utf-8"
    )

    assert '"short_drama/skills/**/*.md"' in builder_config


def test_loader_reads_changed_markdown_without_python_profile_edits(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "formats"
    shutil.copytree(DEFAULT_PRODUCTION_SKILLS_ROOT, copied_root)
    writer_path = copied_root / "digital_human" / "roles" / "writer.md"
    writer_path.write_text(
        "# 编剧\n\n使用本测试指定的主播措辞。\n",
        encoding="utf-8",
    )

    library = load_production_skills(copied_root)
    by_profile = {skill.profile: skill for skill in library}

    assert by_profile[ProductionProfile.DIGITAL_HUMAN].writer == (
        "使用本测试指定的主播措辞。"
    )


def test_role_references_are_loaded_only_for_roles_that_request_them(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "formats"
    shutil.copytree(DEFAULT_PRODUCTION_SKILLS_ROOT, copied_root)
    reference_path = (
        copied_root
        / "ai_short_drama"
        / "references"
        / "dialogue_and_performance.md"
    )
    reference_path.write_text(
        reference_path.read_text(encoding="utf-8")
        + "\n\n本测试的选择性对白方法标记。\n",
        encoding="utf-8",
    )

    library = load_production_skills(copied_root)
    douyin = next(
        skill
        for skill in library
        if skill.profile == ProductionProfile.AI_SHORT_DRAMA
    )

    assert "本测试的选择性对白方法标记" in douyin.writer
    assert "本测试的选择性对白方法标记" in douyin.critic
    assert "本测试的选择性对白方法标记" not in douyin.concept
    assert "三种文本要分开" in douyin.writer


def test_provider_prompt_skill_is_file_backed_and_reads_references(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "providers"
    shutil.copytree(DEFAULT_PROVIDER_SKILLS_ROOT, copied_root)
    reference_path = (
        copied_root / "minimax_h3" / "references" / "common_compilation.md"
    )
    reference_path.write_text(
        reference_path.read_text(encoding="utf-8")
        + "\n\n本测试的 H3 编译方法标记。\n",
        encoding="utf-8",
    )

    skills = load_provider_prompt_skills(copied_root)

    assert len(skills) == 1
    assert skills[0].provider == "minimax_h3"
    assert skills[0].label == "MiniMax H3 提示词编译技能"
    assert "本测试的 H3 编译方法标记" in skills[0].instructions
    assert "subject_definitions" in skills[0].instructions
    assert "本文件供维护者和路线规划 Agent 审阅" not in skills[0].instructions
    assert skills[0].reference_paths == (
        "references/common_compilation.md",
        "references/base_and_keyframe_modes.md",
        "references/reference_mode.md",
    )


def test_missing_role_or_provider_reference_reports_the_source_file(
    tmp_path: Path,
) -> None:
    copied_formats = tmp_path / "formats"
    copied_providers = tmp_path / "providers"
    shutil.copytree(DEFAULT_PRODUCTION_SKILLS_ROOT, copied_formats)
    shutil.copytree(DEFAULT_PROVIDER_SKILLS_ROOT, copied_providers)

    writer_path = copied_formats / "ai_short_drama" / "roles" / "writer.md"
    writer_path.write_text(
        writer_path.read_text(encoding="utf-8").replace(
            'references: ["references/ai_native_forms.md", "references/narrative_and_retention.md", "references/dialogue_and_performance.md"]',
            'references: ["references/not-found.md"]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"Role reference.*writer\.md"):
        load_production_skills(copied_formats)

    provider_path = copied_providers / "minimax_h3" / "SKILL.md"
    provider_path.write_text(
        provider_path.read_text(encoding="utf-8").replace(
            "references/common_compilation.md",
            "references/not-found.md",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"Provider prompt reference.*SKILL\.md"):
        load_provider_prompt_skills(copied_providers)


def test_format_skills_change_role_behavior_without_changing_prompt_api() -> None:
    general = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO,
        roles=(ProductionSkillRole.DIRECTOR,),
    )
    douyin = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )
    digital_human = compile_production_prompt(
        ProductionProfile.DIGITAL_HUMAN,
        roles=(ProductionSkillRole.DIRECTOR,),
    )
    anime = compile_production_prompt(
        ProductionProfile.ANIME,
        roles=(ProductionSkillRole.DIRECTOR,),
    )

    assert "竖屏可读性" in douyin.text
    assert "视线" in digital_human.text
    assert "轮廓" in anime.text
    assert "留白、静默" in general.text
    assert len({general.text, douyin.text, digital_human.text, anime.text}) == 4


def test_douyin_behavior_and_rhythm_method_is_selectively_injected() -> None:
    marker = "本方法把短视频观看行为研究转成创作与审看问题"
    concept = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
    )
    director = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )
    episode_reviewer = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.EPISODE_REVIEWER,),
    )
    asset_reviewer = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
    )
    general_concept = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO,
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert marker in concept.text
    assert marker in director.text
    assert marker in episode_reviewer.text
    assert marker not in asset_reviewer.text
    assert marker not in general_concept.text


def test_douyin_skill_keeps_behavior_metrics_diagnostic_and_contextual() -> None:
    skill = production_skill(ProductionProfile.AI_SHORT_DRAMA)

    assert "快速滑走、延迟跳出、中段退出" in skill.playbook
    assert "流量来源和曝光阶段" in skill.playbook
    assert "切点优先跟随" in skill.playbook
    assert "不用单一绝对阈值判定剧本失败" in skill.playbook
    assert "没有数据时只输出观看风险预判" in skill.constitution
    assert "不伪造留存结论" in skill.constitution


def test_ai_short_drama_keeps_style_open_and_separate_from_anime() -> None:
    ai_short_drama = production_skill(ProductionProfile.AI_SHORT_DRAMA)
    anime = production_skill(ProductionProfile.ANIME)
    director = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )

    assert ai_short_drama.label == "AI 原生短剧"
    assert "不是 anime 的同义词" in ai_short_drama.constitution
    assert "不预先要求某种人物质感" in ai_short_drama.constitution
    assert "Style 是可修改的项目工件，不是代码枚举" in director.text
    assert "AI 原生短剧即使采用动画" in anime.constitution


def test_new_format_skills_keep_shared_runtime_contracts_advisory() -> None:
    digital_human = production_skill(ProductionProfile.DIGITAL_HUMAN)
    anime = production_skill(ProductionProfile.ANIME)

    assert digital_human.user_overridable
    assert anime.user_overridable
    assert "先确认语音内容" in " ".join(
        digital_human.recommended_playbook
    )
    assert "生成与修复时持续保护角色造型" in " ".join(
        anime.recommended_playbook
    )
    assert digital_human.label == "数字人口播视频"
    assert anime.label == "动画叙事视频"
    assert len(digital_human.recommended_playbook) == 10
    assert len(anime.recommended_playbook) == 10
    assert "检查与返工" in digital_human.playbook
    assert "检查与返工" in anime.playbook


def test_recommended_playbook_is_plain_display_text() -> None:
    for skill in production_skill_catalog():
        assert len(skill.recommended_playbook) == 10
        assert all("**" not in step for step in skill.recommended_playbook)


def test_new_formats_inject_medium_through_skill_instead_of_base_prompt() -> None:
    for profile in (ProductionProfile.DIGITAL_HUMAN, ProductionProfile.ANIME):
        instructions, payload = _planner_prompt(
            CreateProjectRequest(
                source_mode=SourceMode.AUTO_ORIGINAL,
                production_profile=profile,
            )
        )

        assert production_skill(profile).label in instructions
        assert "Mandarin live-action vertical" not in instructions
        assert "follow the source contract and active Production Skill" in payload
