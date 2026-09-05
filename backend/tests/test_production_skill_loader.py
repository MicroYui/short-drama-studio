"""Regression coverage for Markdown-first Production Skills."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from short_drama.models import CreateProjectRequest, ProductionProfile, SourceMode
from short_drama.production_planner import _outline_prompt
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_narrative_skill_prompt,
    compile_production_prompt,
    narrative_skill,
    narrative_skill_catalog,
    production_skill,
    production_skill_catalog,
    shared_role_prompt,
)
from short_drama.skills.loader import (
    DEFAULT_PROVIDER_SKILLS_ROOT,
    DEFAULT_PRODUCTION_SKILLS_ROOT,
    DEFAULT_SHARED_SKILL_ROOT,
    load_provider_prompt_skills,
    load_production_skills,
    load_shared_role_prompts,
)


def test_every_profile_is_compiled_from_reviewable_markdown() -> None:
    catalog = production_skill_catalog()

    assert tuple(skill.profile for skill in catalog) == tuple(ProductionProfile)
    for skill in catalog:
        directory = DEFAULT_PRODUCTION_SKILLS_ROOT / skill.profile.value.casefold()
        assert (directory / "SKILL.md").is_file()
        assert (directory / "playbooks" / "default.md").is_file()
        role_paths = {
            role: directory / "roles" / f"{role.value.casefold()}.md"
            for role in ProductionSkillRole
        }
        if skill.profile == ProductionProfile.AI_SHORT_DRAMA:
            assert not any(path.exists() for path in role_paths.values())
        else:
            assert all(path.is_file() for path in role_paths.values())


def test_packaged_backend_includes_runtime_markdown_skills() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    builder_config = (repository_root / "electron-builder.yml").read_text(
        encoding="utf-8"
    )

    assert '"short_drama/skills/**/*.md"' in builder_config
    assert '"short_drama/skills/**/*.txt"' in builder_config
    assert '"short_drama/skills/**/*.yaml"' in builder_config


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


def test_shared_role_prompts_are_file_backed_chinese_responsibilities(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "shared"
    shutil.copytree(DEFAULT_SHARED_SKILL_ROOT, copied_root)
    concept_path = copied_root / "roles" / "concept.md"
    concept_path.write_text(
        "# 故事架构\n\n本测试的通用故事架构职责。\n",
        encoding="utf-8",
    )

    prompts = load_shared_role_prompts(copied_root)

    assert set(prompts) == set(ProductionSkillRole)
    assert prompts[ProductionSkillRole.CONCEPT] == "本测试的通用故事架构职责。"
    assert "目标体验" in shared_role_prompt(ProductionSkillRole.CRITIC)
    assert "审校意见是证据线索" in shared_role_prompt(
        ProductionSkillRole.STORY_REPAIR
    )
    assert "负责收敛一次完整语义检查的 Verifier" in shared_role_prompt(
        ProductionSkillRole.REVISION_VERIFIER
    )
    assert "不另起 Critic、评分或冷读循环" in shared_role_prompt(
        ProductionSkillRole.REVISION_VERIFIER
    )
    assert "脱离前一轮审校" in shared_role_prompt(
        ProductionSkillRole.FINAL_COLD_READ
    )


def test_retired_local_narrative_skills_are_not_in_the_active_catalog() -> None:
    assert narrative_skill_catalog() == ()
    with pytest.raises(KeyError):
        narrative_skill("narrative.absurd_comedy")


def test_shared_verifier_supports_script_and_storyboard_semantic_handoffs() -> None:
    verifier = shared_role_prompt(ProductionSkillRole.REVISION_VERIFIER)

    assert "若工件是剧本" in verifier
    assert "若工件是分镜草稿" in verifier
    assert "夸张、荒诞、无厘头和类型化跳跃不是天然缺陷" in verifier
    assert "把 READY 或 NEEDS_REVISION" in verifier
    assert "验收 WorkOrder 本身均以 COMPLETED" in verifier


def test_ai_short_drama_composes_with_an_explicit_official_method() -> None:
    format_only = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
    )
    composed = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
        method_skill_ids=("method.minimax_h3.3d_animation_short",),
    )

    assert format_only.selected_method_skill_ids == ()
    assert "MiniMax-H3：3D 动画短片" not in format_only.text
    assert "皮克斯感 3D 卡通渲染" not in format_only.text
    assert composed.selected_method_skill_ids == (
        "method.minimax_h3.3d_animation_short",
    )
    assert "MiniMax-H3：3D 动画短片" in composed.text
    assert "全局视觉风格锁" in composed.text
    assert "皮克斯感 3D 卡通渲染" in composed.text

    for compiled in (format_only, composed):
        assert compiled.selected_skill_ids == ()
        assert "概念策划的通用职责" in compiled.text
        assert "Leader → Worker → Verifier" in compiled.text
        assert "当前格式补充" not in compiled.text


def test_prompt_compiler_only_validates_explicit_skill_selection() -> None:
    unselected = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,),
    )

    assert unselected.selected_skill_ids == ()
    assert "Agent 已根据当前任务语义选择叙事技能" not in unselected.text
    with pytest.raises(ValueError, match="Unknown narrative Production Skill"):
        compile_production_prompt(
            ProductionProfile.AI_SHORT_DRAMA,
            roles=(ProductionSkillRole.CONCEPT,),
            additional_skill_ids=("narrative.not_found",),
        )
    with pytest.raises(ValueError, match="Unknown narrative Production Skill"):
        compile_production_prompt(
            ProductionProfile.AI_SHORT_DRAMA,
            roles=(ProductionSkillRole.DIRECTOR,),
            additional_skill_ids=("narrative.absurd_comedy",),
        )


def test_retired_narrative_prompt_ids_are_rejected() -> None:
    assert compile_narrative_skill_prompt((), roles=()) == ""

    with pytest.raises(ValueError, match="Unknown narrative Production Skill"):
        compile_narrative_skill_prompt(
            ("narrative.premise_amplification",),
            roles=(ProductionSkillRole.WRITER,),
        )


def test_role_references_are_loaded_only_for_roles_that_request_them(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "formats"
    shutil.copytree(DEFAULT_PRODUCTION_SKILLS_ROOT, copied_root)
    format_directory = copied_root / "digital_human"
    reference_path = format_directory / "writer-reference.md"
    reference_path.write_text(
        "# 口播写作参考\n\n本测试的选择性口播方法标记。\n",
        encoding="utf-8",
    )
    writer_path = format_directory / "roles" / "writer.md"
    writer_path.write_text(
        '---\nreferences: ["writer-reference.md"]\n---\n'
        + writer_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    library = load_production_skills(copied_root)
    digital_human = next(
        skill
        for skill in library
        if skill.profile == ProductionProfile.DIGITAL_HUMAN
    )

    assert "本测试的选择性口播方法标记" in digital_human.writer
    assert "本测试的选择性口播方法标记" not in digital_human.critic
    assert "本测试的选择性口播方法标记" not in digital_human.concept


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
    assert skills[0].provenance == "OFFICIAL_TRANSLATION_WITH_ADAPTER"
    assert any(
        "h3-prompt-writing/SKILL.md" in source
        for source in skills[0].upstream_sources
    )
    assert "## H3 提示词编写" in skills[0].instructions
    assert "本测试的 H3 编译方法标记" in skills[0].instructions
    assert "subject_definitions" in skills[0].instructions
    assert "本文件供维护者和路线规划 Agent 审阅" not in skills[0].instructions
    assert skills[0].reference_paths == (
        "references/official_h3_prompt_writing_zh_cn.md",
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

    writer_path = copied_formats / "digital_human" / "roles" / "writer.md"
    writer_path.write_text(
        '---\nreferences: ["../not-found.md"]\n---\n'
        + writer_path.read_text(encoding="utf-8"),
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

    assert "Leader → Worker → Verifier" in douyin.text
    assert "视线" in digital_human.text
    assert "轮廓" in anime.text
    assert "留白、静默" in general.text
    assert len({general.text, douyin.text, digital_human.text, anime.text}) == 4


def test_ai_short_drama_has_no_format_role_supplements() -> None:
    compiled = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=tuple(ProductionSkillRole),
    )

    assert "当前格式补充" not in compiled.text


def test_ai_short_drama_is_only_a_local_schema_adapter() -> None:
    skill = production_skill(ProductionProfile.AI_SHORT_DRAMA)

    assert skill.label == "AI 短剧（本地格式适配）"
    assert "不是 MiniMax 官方 Skill" in skill.constitution
    assert "MiniMax Design `drama-series` 作为通用短剧/漫剧项目主骨架" in (
        skill.constitution
    )
    assert "Leader → Worker → Verifier" in skill.constitution
    assert "职责重叠的多套审校与返修 Agent" in skill.constitution
    assert "Critic" not in skill.constitution
    assert "Final Cold Read" not in skill.constitution
    assert skill.recommended_playbook == (
        "Leader 对齐目标与工件",
        "Worker 按上游 Skill 产出",
        "Verifier 验收并退回原 Worker",
    )


def test_ai_short_drama_keeps_style_open_and_separate_from_anime() -> None:
    ai_short_drama = production_skill(ProductionProfile.AI_SHORT_DRAMA)
    anime = production_skill(ProductionProfile.ANIME)
    director = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )

    assert ai_short_drama.label == "AI 短剧（本地格式适配）"
    assert "按项目和已选上游 Skill 声明" in ai_short_drama.medium
    assert "具体题材、节奏、爽点、幽默与媒介仍服从" in (
        ai_short_drama.constitution
    )
    assert "导演的通用职责" in director.text
    assert "当前格式补充" not in director.text
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
        expected_count = (
            3 if skill.profile == ProductionProfile.AI_SHORT_DRAMA else 10
        )
        assert len(skill.recommended_playbook) == expected_count
        assert all("**" not in step for step in skill.recommended_playbook)


def test_ai_short_drama_does_not_duplicate_worker_or_verifier_prompts() -> None:
    skill = production_skill(ProductionProfile.AI_SHORT_DRAMA)
    planner = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.PLANNER,),
    )
    asset_reviewer = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
    )
    compiler = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.VIDEO_PROMPT_COMPILER,),
    )

    assert skill.user_overridable
    assert skill.planner == ""
    assert skill.asset_reviewer == ""
    assert skill.video_prompt_compiler == ""
    assert "共享端到端制片流程" in planner.text
    assert "资产审核的通用职责" in asset_reviewer.text
    assert "artifact.update_brief" in asset_reviewer.text
    assert "image.enqueue" in asset_reviewer.text
    assert "qa.inspect_asset_version" in asset_reviewer.text
    assert "artifact.review_version" in asset_reviewer.text
    assert "route.plan_h3" in asset_reviewer.text
    assert "视频提示词编译的通用职责" in compiler.text
    assert "当前格式补充" not in planner.text
    assert "当前格式补充" not in asset_reviewer.text
    assert "当前格式补充" not in compiler.text


def test_new_formats_inject_medium_through_skill_instead_of_base_prompt() -> None:
    for profile in (ProductionProfile.DIGITAL_HUMAN, ProductionProfile.ANIME):
        instructions, payload = _outline_prompt(
            CreateProjectRequest(
                source_mode=SourceMode.AUTO_ORIGINAL,
                production_profile=profile,
            )
        )

        assert production_skill(profile).label in instructions
        assert "Mandarin live-action vertical" not in instructions
        assert '"output_schema"' in payload
