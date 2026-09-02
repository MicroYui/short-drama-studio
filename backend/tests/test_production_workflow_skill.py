"""Regression coverage for the shared Markdown production process."""

from __future__ import annotations

import re

from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_workflow,
)


def test_shared_end_to_end_workflow_is_loaded_into_planner_prompts() -> None:
    workflow = production_workflow()

    assert "形成内容方案" in workflow
    assert "准备制作资产" in workflow
    assert "设计场次与分镜" in workflow
    assert "完成成片验收与导出" in workflow

    planner = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.PLANNER,),
    )
    director = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
    )

    assert workflow in planner.text
    assert workflow not in director.text
    assert "共享端到端制片流程" in planner.text
    assert "当前类型在共享流程上的详细制作方法" in planner.text
    assert "每次规划前先回答的六个问题" in planner.text
    assert "route.compile_h3_prompt" in planner.text
    assert "change.preview" in planner.text
    assert "竖屏导演重点确保核心动作" in planner.text
    assert "当前类型在共享流程上的详细制作方法" not in director.text


def test_shared_workflow_keeps_all_ten_reviewable_stages() -> None:
    stages = re.findall(
        r"^## 阶段([一二三四五六七八九十]+)：",
        production_workflow(),
        flags=re.MULTILINE,
    )

    assert stages == ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
