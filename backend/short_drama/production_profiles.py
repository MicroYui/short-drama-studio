"""Public facade for file-backed Production Skills.

Human-authored constitutions, role prompts, and playbooks live under
``short_drama/skills/formats`` as Markdown and compile into typed runtime objects.

Production Skills advise Agents. They do not own provider calls, Tool schemas,
task state, or fixed workflow transitions.
"""

from __future__ import annotations

from functools import lru_cache

from short_drama.models import ProductionProfile
from short_drama.skills.loader import (
    load_production_skills,
    load_production_workflow,
)
from short_drama.skills.models import (
    CompiledProductionPrompt,
    ProductionSkill,
    ProductionSkillRole,
)


@lru_cache(maxsize=1)
def _production_skill_library() -> tuple[ProductionSkill, ...]:
    return load_production_skills()


def production_skill(profile: ProductionProfile) -> ProductionSkill:
    """Return one compiled Skill without restricting the global Tool catalog."""

    for skill in _production_skill_library():
        if skill.profile == profile:
            return skill
    raise KeyError(profile)


def production_skill_catalog() -> tuple[ProductionSkill, ...]:
    """Expose the complete file-backed catalog in enum declaration order."""

    by_profile = {
        skill.profile: skill for skill in _production_skill_library()
    }
    return tuple(by_profile[profile] for profile in ProductionProfile)


@lru_cache(maxsize=1)
def production_workflow() -> str:
    """Return the shared end-to-end production guidance loaded from Markdown."""

    return load_production_workflow()


def compile_production_prompt(
    profile: ProductionProfile,
    *,
    roles: tuple[ProductionSkillRole, ...],
    include_constitution: bool = True,
) -> CompiledProductionPrompt:
    """Compile only the Skill layers needed by one Agent call."""

    skill = production_skill(profile)
    resolved_roles = tuple(dict.fromkeys(roles))
    policy_by_role = {
        ProductionSkillRole.CONCEPT: skill.concept,
        ProductionSkillRole.WRITER: skill.writer,
        ProductionSkillRole.PLANNER: skill.planner,
        ProductionSkillRole.CRITIC: skill.critic,
        ProductionSkillRole.REVIEW_ROUTER: skill.review_router,
        ProductionSkillRole.DIRECTOR: skill.director,
        ProductionSkillRole.ASSET_REVIEWER: skill.asset_reviewer,
        ProductionSkillRole.ROUTE_PLANNER: skill.route_planner,
        ProductionSkillRole.VIDEO_PROMPT_COMPILER: skill.video_prompt_compiler,
        ProductionSkillRole.EPISODE_REVIEWER: skill.episode_reviewer,
    }
    role_labels = {
        ProductionSkillRole.CONCEPT: "概念策划",
        ProductionSkillRole.WRITER: "编剧",
        ProductionSkillRole.PLANNER: "镜头规划",
        ProductionSkillRole.CRITIC: "内容审校",
        ProductionSkillRole.REVIEW_ROUTER: "审核问题分派",
        ProductionSkillRole.DIRECTOR: "导演",
        ProductionSkillRole.ASSET_REVIEWER: "资产审核",
        ProductionSkillRole.ROUTE_PLANNER: "生成路线规划",
        ProductionSkillRole.VIDEO_PROMPT_COMPILER: "视频提示词编译",
        ProductionSkillRole.EPISODE_REVIEWER: "成片审核",
    }
    parts = [f"当前制片技能：{skill.label}。"]
    if include_constitution:
        parts.append(f"类型创作原则：{skill.constitution}")
    if ProductionSkillRole.PLANNER in resolved_roles:
        parts.append(
            "共享端到端制片流程（根据当前目标、已有工件与证据跳过、合并、重排或并行，"
            "不得将其视为固定状态机）：" + production_workflow()
        )
        parts.append(
            "当前类型在共享流程上的详细制作方法（可按任务需要取用，不是第二套固定流程）：\n"
            + skill.playbook
        )
    parts.extend(
        f"{role_labels[role]}职责准则：{policy_by_role[role]}"
        for role in resolved_roles
    )
    return CompiledProductionPrompt(
        profile=profile,
        roles=resolved_roles,
        text=" ".join(parts),
    )


__all__ = [
    "CompiledProductionPrompt",
    "ProductionSkill",
    "ProductionSkillRole",
    "compile_production_prompt",
    "production_skill",
    "production_skill_catalog",
    "production_workflow",
]
