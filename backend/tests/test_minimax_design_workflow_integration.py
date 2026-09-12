"""MiniMax Design source retention and local role/stage knowledge coverage."""

from __future__ import annotations

from pathlib import Path

from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    production_method_skill,
    compile_production_method_skill_prompt,
)
from short_drama.skills.loader import (
    DEFAULT_OFFICIAL_MINIMAX_DESIGN_WORKFLOWS_ROOT,
    DEFAULT_SHARED_SKILL_ROOT,
    load_production_method_skills,
)
from short_drama.skills.models import SkillProvenanceKind


INSTALLED_MINIMAX_DESIGN_WORKFLOWS_ROOT = Path(
    "/Applications/MiniMax Design.app/Contents/Resources/agent-profiles/"
    "v2/config/workflows"
)

DESIGN_METHOD_ROLES = {
    "method.drama_series": ProductionSkillRole.CONCEPT,
    "method.advertising": ProductionSkillRole.CONCEPT,
    "method.music_video": ProductionSkillRole.CONCEPT,
}


def _relative_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
    }


def _active_upstream_files(root: Path) -> set[Path]:
    files = {Path("README.md"), Path("AUTHORING.md"), Path("workflow.md")}
    for directory in ("_shared", "drama-series", "ad-tvc", "mv"):
        files.update(
            path.relative_to(root)
            for path in (root / directory).rglob("*")
            if path.is_file()
        )
    return files


def test_minimax_design_active_workflows_are_vendored_as_original_markdown() -> None:
    root = DEFAULT_OFFICIAL_MINIMAX_DESIGN_WORKFLOWS_ROOT

    assert {path.name for path in root.iterdir()} == {
        "README.md",
        "AUTHORING.md",
        "workflow.md",
        "_shared",
        "drama-series",
        "ad-tvc",
        "mv",
    }
    assert len(_relative_files(root)) == 80
    assert {
        directory: len(_relative_files(root / directory))
        for directory in ("_shared", "drama-series", "ad-tvc", "mv")
    } == {
        "_shared": 5,
        "drama-series": 10,
        "ad-tvc": 52,
        "mv": 10,
    }
    assert not any("_disabled" in path.parts for path in _relative_files(root))
    assert root.joinpath("README.md").read_text(encoding="utf-8").startswith(
        "# Workflows — authoring and directory guide\n"
    )
    assert root.joinpath("workflow.md").read_text(encoding="utf-8").startswith(
        "# Workflows — runtime routing index\n"
    )

    if INSTALLED_MINIMAX_DESIGN_WORKFLOWS_ROOT.is_dir():
        upstream_files = _active_upstream_files(
            INSTALLED_MINIMAX_DESIGN_WORKFLOWS_ROOT
        )
        assert _relative_files(root) == upstream_files
        for relative_path in upstream_files:
            assert root.joinpath(relative_path).read_bytes() == (
                INSTALLED_MINIMAX_DESIGN_WORKFLOWS_ROOT
                / relative_path
            ).read_bytes()


def test_design_methods_share_the_local_interaction_precedence_preamble() -> None:
    for method_id, role in DESIGN_METHOD_ROLES.items():
        method = production_method_skill(method_id)
        role_prompt = compile_production_method_skill_prompt([method_id], roles=[role])

        assert method.provenance == SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER
        assert role_prompt.count("根据完整用户目标、当前对象与已有效结果选择方法") == 1
        assert "已给信息直接采用" in role_prompt
        assert "交给 Agent 决定的内容自主完成" in role_prompt
        assert "用户指定确认点时只在该处停靠" in role_prompt
        assert "<official_method_reference" not in role_prompt
        assert "source=\"" not in role_prompt
        assert "MiniMax-H3 上游 Markdown" not in role_prompt


def test_official_method_prompts_end_with_local_execution_precedence() -> None:
    footer = (DEFAULT_SHARED_SKILL_ROOT / "official_task_method_footer.md").read_text(
        encoding="utf-8"
    ).strip()
    for method in load_production_method_skills():
        for role in method.role_prompts:
            role_prompt = compile_production_method_skill_prompt([method.id], roles=[role])
            footer_index = role_prompt.rfind("方法提供可选择的制片经验")

            assert footer_index >= 0
            assert "创作数值、方案数量、节奏比例、全局语速、重试次数是可选参考" in role_prompt[footer_index:]
            assert "不设置冻结、预算门槛或必须由另一名 Agent 批准的顺序" in role_prompt[footer_index:]
            assert "明确选中且可用的媒体不因 DRAFT 标记而另起批准流程" in role_prompt[footer_index:]
            assert "工具返回的具体范围决定检查结论覆盖哪里" in role_prompt[footer_index:]
            assert role_prompt.rstrip().endswith(footer.rsplit("\n\n", 1)[-1])


def test_design_methods_load_only_role_relevant_adapted_sections() -> None:
    drama = production_method_skill("method.drama_series")
    assert "制作时只读取与当前任务有关的知识和素材" in drama.constitution
    assert "时长、场次和反转次数随故事调整" in drama.constitution
    for role, headings in (
        (ProductionSkillRole.CONCEPT, ("创作大纲", "题材与开篇", "情绪与因果", "本次制作范围")),
        (ProductionSkillRole.WRITER, ("剧本写入当前项目", "题材与开篇", "情绪与因果")),
        (ProductionSkillRole.PLANNER, ("本次制作范围", "渐进分镜与可选检查")),
        (ProductionSkillRole.DIRECTOR, ("镜头语言与情绪", "表演与物理动作", "空间与状态连续性")),
        (ProductionSkillRole.REVISION_VERIFIER, ("当前范围验收", "空间与状态连续性")),
    ):
        body = drama.role_prompts[role]
        for heading in headings:
            assert f"## {heading}" in body
        assert "两轮轻交互" not in body
        assert "target_episode_scope" not in body
        assert "## storyboard-confirm 前分组检查清单" not in body
    assert "不能用设计终态冒充已拍事实" in drama.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "互补的屏幕视线" in drama.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "每组写完立即持久化并在内容页显示" in drama.role_prompts[ProductionSkillRole.PLANNER]
    assert "对白覆盖以及时长合计" in drama.role_prompts[ProductionSkillRole.REVISION_VERIFIER]
    for role, heading in (
        (ProductionSkillRole.ASSET_PLANNER, "资产规划"),
        (ProductionSkillRole.ASSET_BUILDER, "资产制作"),
        (ProductionSkillRole.ASSET_VERIFIER, "资产检查"),
    ):
        assert f"## {heading}" in (drama.prompt_for(role, stage=ProductionPromptStage.ASSETS) or "")
    assert "## 连续表演与角色声音签名" in drama.role_prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]
    assert "只精简当前超长字段的重复修辞" in drama.role_prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]
    assert "## 资产规划" not in drama.role_prompts[ProductionSkillRole.WRITER]

    advertising = production_method_skill("method.advertising")
    assert "## 简报与传播证明" in advertising.role_prompts[ProductionSkillRole.CONCEPT]
    assert "观众价值—可见动作—产品反应—结果" in advertising.role_prompts[ProductionSkillRole.CONCEPT]
    assert "## 镜头与视觉技法" in advertising.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "## 资产检查" in advertising.role_prompts[ProductionSkillRole.ASSET_REVIEWER]
    assert "## 资产制作" in advertising.role_prompts[ProductionSkillRole.ASSET_BUILDER]
    assert "## Prompt Skeleton" not in advertising.role_prompts[ProductionSkillRole.ASSET_BUILDER]
    assert "产品动作、卖点因果" in advertising.role_prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]

    mv = production_method_skill("method.music_video")
    assert "## 歌词与内容写作" in mv.role_prompts[ProductionSkillRole.WRITER]
    assert "## 字体与空间包装" in mv.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "揭示—附着—跟随—遮挡—传递—落稳" in mv.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "主音乐绝对时间与本组局部时间建立对应" in mv.role_prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]
    for method in (drama, advertising, mv):
        compiler = method.role_prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER]
        post = compile_production_method_skill_prompt(
            [method.id], roles=[ProductionSkillRole.DIRECTOR], stage=ProductionPromptStage.POST,
        )
        assert "## 当前作品与最小修复" in post
        assert method.role_prompts[ProductionSkillRole.DIRECTOR] not in post
        assert method.role_prompts[ProductionSkillRole.CONCEPT] not in post
        for prompt in (compiler, post):
            for old_contract in (
                "H3 唯一可执行填空骨架", "固定六段骨架", "hub_canvas_write_node",
                "execution_locks", "prompt_section_titles", "review.before_execution",
            ):
                assert old_contract not in prompt


def test_root_routing_index_is_snapshot_only_not_a_method_prompt_source() -> None:
    root_source_label = (
        'source="../../sources/minimax_design/3.0.11/workflows/workflow.md"'
    )
    for method_id in DESIGN_METHOD_ROLES:
        method = production_method_skill(method_id)
        combined_role_prompts = "\n".join(method.role_prompts.values())

        assert "# Workflows — runtime routing index" not in combined_role_prompts
        assert root_source_label not in combined_role_prompts
        assert "workflow_match for project workflow units" not in combined_role_prompts
