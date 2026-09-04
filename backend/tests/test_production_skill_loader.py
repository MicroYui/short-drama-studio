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
    DEFAULT_NARRATIVE_SKILLS_ROOT,
    DEFAULT_PROVIDER_SKILLS_ROOT,
    DEFAULT_PRODUCTION_SKILLS_ROOT,
    DEFAULT_SHARED_SKILL_ROOT,
    load_narrative_skills,
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
    assert "从第一场到最后一场自然冷读整份修订稿" in shared_role_prompt(
        ProductionSkillRole.REVISION_VERIFIER
    )
    assert "不再转交第二个独立冷读角色" in shared_role_prompt(
        ProductionSkillRole.REVISION_VERIFIER
    )
    assert "脱离前一轮审校" in shared_role_prompt(
        ProductionSkillRole.FINAL_COLD_READ
    )


def test_narrative_skill_catalog_is_open_file_backed_and_role_scoped() -> None:
    loaded = load_narrative_skills(DEFAULT_NARRATIVE_SKILLS_ROOT)
    catalog = narrative_skill_catalog()

    expected_ids = {
        "narrative.absurd_comedy",
        "narrative.payoff_escalation",
        "narrative.premise_amplification",
    }
    assert {skill.id for skill in loaded} == expected_ids
    assert {descriptor.id for descriptor in catalog} == expected_ids
    assert all(descriptor.description for descriptor in catalog)
    assert all(
        descriptor.supported_roles
        == (
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
            ProductionSkillRole.STORY_REPAIR,
            ProductionSkillRole.REVISION_VERIFIER,
        )
        for descriptor in catalog
    )
    premise = narrative_skill("narrative.premise_amplification")
    assert premise.supports(ProductionSkillRole.WRITER)
    assert not premise.supports(ProductionSkillRole.DIRECTOR)
    assert "昨日普通，今日奇迹" in premise.constitution
    payoff_writer = compile_narrative_skill_prompt(
        ("narrative.payoff_escalation",),
        roles=(ProductionSkillRole.WRITER,),
    )
    assert "下一场直接从这个新状态出发" in payoff_writer
    assert "界面跳绿、到账提示、权威认可和对手失语" in payoff_writer
    premise_critic = compile_narrative_skill_prompt(
        ("narrative.premise_amplification",),
        roles=(ProductionSkillRole.CRITIC,),
    )
    assert "主线与边角细节" in premise_critic
    absurd_verifier = compile_narrative_skill_prompt(
        ("narrative.absurd_comedy",),
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
    )
    assert "允许无厘头" in absurd_verifier


def test_shared_verifier_can_find_a_missed_delivery_issue_without_reopening_critic() -> None:
    verifier = shared_role_prompt(ProductionSkillRole.REVISION_VERIFIER)

    assert "此前漏检" in verifier
    assert "不等于只能检查新回归" in verifier
    assert "不扩张成一般性改稿清单" in verifier


def test_narrative_loader_requires_only_declared_role_files(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "narrative"
    shutil.copytree(DEFAULT_NARRATIVE_SKILLS_ROOT, copied_root)

    loaded = load_narrative_skills(copied_root)

    assert loaded
    assert not (
        copied_root / "premise_amplification" / "roles" / "director.md"
    ).exists()


def test_narrative_loader_reports_a_missing_declared_role_file(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "narrative"
    shutil.copytree(DEFAULT_NARRATIVE_SKILLS_ROOT, copied_root)
    missing_path = copied_root / "payoff_escalation" / "roles" / "writer.md"
    missing_path.unlink()

    with pytest.raises(ValueError, match=r"declares role WRITER.*writer\.md"):
        load_narrative_skills(copied_root)


def test_prompt_compiler_combines_format_and_agent_selected_narrative_skills() -> None:
    compiled = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT, ProductionSkillRole.WRITER),
        additional_skill_ids=(
            "narrative.premise_amplification",
            "narrative.payoff_escalation",
            "narrative.premise_amplification",
        ),
    )

    assert compiled.selected_skill_ids == (
        "narrative.premise_amplification",
        "narrative.payoff_escalation",
    )
    assert "概念策划的通用职责" in compiled.text
    assert "当前格式补充" in compiled.text
    assert "核心设定放大" in compiled.text
    assert "回报递进" in compiled.text
    assert "编剧的技能补充" in compiled.text


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
    with pytest.raises(ValueError, match="does not support any requested role"):
        compile_production_prompt(
            ProductionProfile.AI_SHORT_DRAMA,
            roles=(ProductionSkillRole.DIRECTOR,),
            additional_skill_ids=("narrative.absurd_comedy",),
        )


def test_narrative_prompt_helper_does_not_repeat_format_or_shared_layers() -> None:
    prompt = compile_narrative_skill_prompt(
        [
            "narrative.premise_amplification",
            "narrative.absurd_comedy",
        ],
        roles=[ProductionSkillRole.WRITER],
    )

    assert "核心设定放大" in prompt
    assert "荒诞喜剧逻辑" in prompt
    assert "编剧的技能补充" in prompt
    assert "当前制片技能" not in prompt
    assert "通用职责" not in prompt
    assert "当前格式补充" not in prompt
    assert compile_narrative_skill_prompt((), roles=()) == ""

    with pytest.raises(ValueError, match="does not support any requested role"):
        compile_narrative_skill_prompt(
            ("narrative.payoff_escalation",),
            roles=(ProductionSkillRole.DIRECTOR,),
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
    assert "本测试的选择性对白方法标记" not in douyin.critic
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
            'references: ["references/ai_native_forms.md", "references/dialogue_and_performance.md"]',
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


def test_story_review_roles_are_independent_and_keep_production_review_downstream() -> None:
    critic = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CRITIC,),
    )
    repair = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.STORY_REPAIR,),
        additional_skill_ids=("narrative.absurd_comedy",),
    )
    verifier = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.REVISION_VERIFIER,),
        additional_skill_ids=("narrative.absurd_comedy",),
    )
    cold_read = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.FINAL_COLD_READ,),
    )
    director = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )
    episode_reviewer = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.EPISODE_REVIEWER,),
    )

    assert "内容审校的通用职责" in critic.text
    assert "故事修复规划的通用职责" in repair.text
    assert "修订验收的通用职责" in verifier.text
    assert "最终冷读的通用职责" in cold_read.text
    assert "荒诞喜剧逻辑" in repair.text
    assert "荒诞喜剧逻辑" in verifier.text
    assert "生成单元" not in critic.text
    assert "进入生成前" in director.text
    assert "生成单元边界" in episode_reviewer.text


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


def test_ai_short_drama_skill_keeps_assets_storyboards_and_h3_bindings_advisory() -> None:
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
    assert "爆点" in skill.constitution
    assert "爽点" in skill.constitution
    assert "不默认要求爽点" in skill.constitution
    assert "具体四视图/群像轻量资产" in skill.constitution
    assert "次要群像、远景或一次性人物可只保留" in skill.playbook
    assert "完整剧本后按时间段拆解镜头" in skill.playbook
    assert "artifact.update_brief" in skill.playbook
    assert "image.compose_reference_sheet" in skill.playbook
    assert "@食堂全景" in planner.text
    assert "CHARACTER_STANDARD_VIEWS" in asset_reviewer.text
    assert "标签与请求媒体必须一一对应" in compiler.text
    assert "不把示例中的固定 15 秒、13 切" in compiler.text


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
