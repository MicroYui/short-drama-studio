"""Old projects and resumable runs keep their selected production knowledge."""

from short_drama.agent_runtime.models import AgentRun, ContextManifest
from short_drama.models import ProductionProfile
from short_drama.production_manager import (
    AgentPlanProposal,
    _method_skill_ids_for_role,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
    production_method_skill,
    production_method_skill_catalog,
)


def test_old_method_names_resolve_into_one_current_catalog_entry() -> None:
    catalog = production_method_skill_catalog()
    assert {"method.drama_series", "method.brand_promo", "method.music_video"} <= {item.id for item in catalog}
    assert all("minimax" not in item.id.casefold() for item in catalog)
    assert all("minimax" not in item.label.casefold() for item in catalog)

    legacy = production_method_skill("method.minimax_h3.brand_promo_video")
    current = production_method_skill("method.brand_promo")
    assert legacy is current
    assert current.label == "品牌宣传片"
    assert current.upstream_sources

    prompt = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO,
        roles=(ProductionSkillRole.CONCEPT,),
        method_skill_ids=("method.minimax_h3.brand_promo_video", "method.brand_promo"),
    )
    assert prompt.selected_method_skill_ids == ("method.brand_promo",)
    assert prompt.text.count("（method.brand_promo）") == 1
    assert prompt.text.count("## 品牌事实与创意") == 1
    assert "区分已证实事实、用户主张和概念创作" in prompt.text
    assert current.role_prompts[ProductionSkillRole.CONCEPT] in prompt.text


def test_old_run_and_work_order_select_the_same_storyboard_sections() -> None:
    legacy_id = "method.minimax_design.drama_series"
    run = AgentRun(
        id="run-old-name", project_id="project-old-name", objective="完成分镜",
        root_agent="leader", active_skill_ids=[legacy_id],
    )
    manifest = ContextManifest(
        id="context-old-name", work_order_id="work-old-name", objective="完成分镜",
        why_now="剧本已完成", selected_skill_ids=[legacy_id],
        production_prompt_stage=ProductionPromptStage.STORYBOARD,
    )
    proposal = AgentPlanProposal(
        summary="已完成当前交付", goal_complete=True,
        selected_method_skill_ids=[legacy_id],
    )
    assert run.active_skill_ids == ["method.drama_series"]
    assert manifest.selected_skill_ids == run.active_skill_ids
    assert proposal.selected_method_skill_ids == run.active_skill_ids
    methods = _method_skill_ids_for_role([legacy_id], ProductionSkillRole.PLANNER)
    assert methods == ["method.drama_series"]
    prompt = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA, roles=(ProductionSkillRole.PLANNER,),
        method_skill_ids=methods, stage=manifest.production_prompt_stage,
    )
    assert "## 渐进分镜与可选检查" in prompt.text
    assert "时长账本、全片总纲和独立审校是可选帮助" in prompt.text
    assert "## 唯一编译路径：剧本 → 时长账本 → 完整分镜" not in prompt.text
    assert "## 角色 Profile 维度" not in prompt.text
