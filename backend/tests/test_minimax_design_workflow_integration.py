"""MiniMax Design workflow vendoring and thin role-adapter coverage."""

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
        assert "交互按以下优先级解释上游步骤" in role_prompt
        assert "用户已明确给出的值直接采用，不重复询问" in role_prompt
        assert "用户明确要求不交互或交由 Agent 决定时" in role_prompt
        assert "用户明确要求在某个工件或阶段确认时" in role_prompt
        assert "<official_method_reference" not in role_prompt
        assert "source=\"" not in role_prompt
        assert "MiniMax-H3 上游 Markdown" not in role_prompt


def test_official_method_prompts_end_with_local_execution_precedence() -> None:
    for method in load_production_method_skills():
        for role in method.role_prompts:
            role_prompt = compile_production_method_skill_prompt([method.id], roles=[role])
            footer_index = role_prompt.rfind("上游原文提供成熟的制片经验")

            assert footer_index >= 0
            assert "创作数值和步骤数量" in role_prompt[footer_index:]
            assert "只是可选发想起点" in role_prompt[footer_index:]
            assert "H3 单个生成组的 4–15 秒能力" in role_prompt[footer_index:]
            assert role_prompt.rstrip().endswith("不得使用关键词、正则、固定次数或额外审批门代替这个判断。")


def test_design_methods_load_only_role_relevant_original_sections() -> None:
    drama = production_method_skill("method.drama_series")
    assert "DurationContract.rhythm_shape" in drama.constitution
    assert "可根据当前题材、单篇时长" in drama.constitution
    assert "不把原文的秒数、百分比、集数或连续次数变成剧本配额" in (
        drama.constitution
    )
    assert "单篇完结的“尾”可以是已兑现的收束按钮" in (
        drama.constitution
    )
    script_role_sections = {
        ProductionSkillRole.CONCEPT: {
            "## 创作大纲",
            "## 题材（Genre）",
            "## 开篇",
            "## 爽感",
            "## 合规",
        },
        ProductionSkillRole.WRITER: {
            "## 剧本落画布",
            "## 题材（Genre）",
            "## 开篇",
            "## 爽感",
            "## 合规",
        },
    }
    downstream_or_series_sections = {
        "## 系列节奏骨架",
        "## 钩子和悬念结尾",
        "## 反派",
        "## storyboard-confirm 前分组检查清单",
        "## 宫格定稿检查",
        "## 精剪自检",
    }
    for role, expected_sections in script_role_sections.items():
        role_prompt = drama.role_prompts[role]
        for section in expected_sections:
            assert section in role_prompt
        for section in downstream_or_series_sections:
            assert section not in role_prompt

    planner_prompt = drama.role_prompts[ProductionSkillRole.PLANNER]
    assert "## 镜头组：拆分整集" in planner_prompt

    director_prompt = drama.role_prompts[ProductionSkillRole.DIRECTOR]
    assert "## 镜头组：拆分整集" in director_prompt
    assert "## 对白：语速、负载和拆分" in director_prompt
    assert "一个镜头组就是未来一次视频生成" in director_prompt
    assert "required_group_seconds" in director_prompt

    verifier_prompt = drama.role_prompts[
        ProductionSkillRole.REVISION_VERIFIER
    ]
    for section in {
        "## 剧本落画布",
        "## 题材（Genre）",
        "## 开篇",
        "## 爽感",
        "## 合规",
        "## 唯一编译路径：剧本 → 时长账本 → 完整分镜",
        "## storyboard-confirm 前分组检查清单",
        "## 跨镜头空间连续性",
        "## 状态连续性",
    }:
        assert section in verifier_prompt
    assert "每句必须完整出现一次并落到明确镜头" in verifier_prompt
    assert "下一组起始快照逐项继承上一组末态" in verifier_prompt
    assert "Clip N 的末态快照逐项成为 Clip N+1 的起始快照" in (
        verifier_prompt
    )

    assert "## 角色 Profile 维度" in drama.role_prompts[
        ProductionSkillRole.ASSET_REVIEWER
    ]
    assert "## 建卡启发式规则" in (
        drama.prompt_for(
            ProductionSkillRole.ASSET_PLANNER,
            stage=ProductionPromptStage.ASSETS,
        )
        or ""
    )
    assert "## 角色设定图 Prompt 模板" in (
        drama.prompt_for(
            ProductionSkillRole.ASSET_BUILDER,
            stage=ProductionPromptStage.ASSETS,
        )
        or ""
    )
    assert "## 站位质量" in (
        drama.prompt_for(
            ProductionSkillRole.ASSET_VERIFIER,
            stage=ProductionPromptStage.ASSETS,
        )
        or ""
    )
    assert "## H3 唯一可执行填空骨架" in drama.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "## 角色 Profile 维度" not in drama.role_prompts[
        ProductionSkillRole.WRITER
    ]

    ad_tvc = production_method_skill("method.advertising")
    assert "## 高信息量问询" in ad_tvc.role_prompts[
        ProductionSkillRole.CONCEPT
    ]
    assert "## Prompt Skeleton" in ad_tvc.role_prompts[
        ProductionSkillRole.ASSET_REVIEWER
    ]
    assert "## Prompt Skeleton" in ad_tvc.role_prompts[
        ProductionSkillRole.ASSET_BUILDER
    ]
    assert "## Unit 编译" in ad_tvc.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "## Unit 编译" not in ad_tvc.role_prompts[
        ProductionSkillRole.CONCEPT
    ]

    mv = production_method_skill("method.music_video")
    assert "# Script Writing — 故事与剧本写作" in mv.role_prompts[
        ProductionSkillRole.WRITER
    ]
    assert "## 字体运动机制" in mv.role_prompts[
        ProductionSkillRole.DIRECTOR
    ]
    assert "## 固定六段骨架" in mv.role_prompts[
        ProductionSkillRole.VIDEO_PROMPT_COMPILER
    ]
    assert "## 固定六段骨架" not in mv.role_prompts[
        ProductionSkillRole.CONCEPT
    ]


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
