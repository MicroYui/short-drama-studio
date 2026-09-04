"""Public facade for file-backed Production Skills.

Human-authored shared roles, format rules, narrative methods, and playbooks live
under ``short_drama/skills`` as Markdown and compile into typed runtime objects.

Production Skills advise Agents. They do not own provider calls, Tool schemas,
task state, or fixed workflow transitions.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from short_drama.models import ProductionProfile
from short_drama.skills.loader import (
    load_narrative_skills,
    load_production_skills,
    load_production_workflow,
    load_shared_role_prompts,
)
from short_drama.skills.models import (
    ComposableProductionSkill,
    CompiledProductionPrompt,
    NarrativeSkillDescriptor,
    ProductionSkill,
    ProductionSkillRole,
)


_ROLE_LABELS = {
    ProductionSkillRole.CONCEPT: "概念策划",
    ProductionSkillRole.WRITER: "编剧",
    ProductionSkillRole.PLANNER: "镜头规划",
    ProductionSkillRole.CRITIC: "内容审校",
    ProductionSkillRole.STORY_REPAIR: "故事修复规划",
    ProductionSkillRole.REVISION_VERIFIER: "修订验收",
    ProductionSkillRole.FINAL_COLD_READ: "最终冷读",
    ProductionSkillRole.REVIEW_ROUTER: "审核问题分派",
    ProductionSkillRole.DIRECTOR: "导演",
    ProductionSkillRole.ASSET_REVIEWER: "资产审核",
    ProductionSkillRole.ROUTE_PLANNER: "生成路线规划",
    ProductionSkillRole.VIDEO_PROMPT_COMPILER: "视频提示词编译",
    ProductionSkillRole.EPISODE_REVIEWER: "成片审核",
}


@lru_cache(maxsize=1)
def _production_skill_library() -> tuple[ProductionSkill, ...]:
    return load_production_skills()


@lru_cache(maxsize=1)
def _narrative_skill_library() -> tuple[ComposableProductionSkill, ...]:
    return load_narrative_skills()


@lru_cache(maxsize=1)
def _shared_role_library() -> dict[ProductionSkillRole, str]:
    return load_shared_role_prompts()


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


def narrative_skill(skill_id: str) -> ComposableProductionSkill:
    """Resolve one Agent-selected narrative method by its open, file-backed id."""

    for skill in _narrative_skill_library():
        if skill.id == skill_id:
            return skill
    raise KeyError(skill_id)


def narrative_skill_catalog() -> tuple[NarrativeSkillDescriptor, ...]:
    """Return compact metadata for semantic selection by an Agent."""

    return tuple(
        NarrativeSkillDescriptor(
            id=skill.id,
            label=skill.label,
            description=skill.description,
            supported_roles=tuple(skill.role_prompts),
            user_overridable=skill.user_overridable,
        )
        for skill in _narrative_skill_library()
    )


def shared_role_prompt(role: ProductionSkillRole) -> str:
    """Return the format-neutral Chinese responsibility for one Agent role."""

    return _shared_role_library()[role]


def _resolve_narrative_skills(
    skill_ids: Sequence[str],
    *,
    roles: Sequence[ProductionSkillRole],
) -> tuple[tuple[str, ...], tuple[ComposableProductionSkill, ...]]:
    resolved_roles = tuple(dict.fromkeys(roles))
    resolved_skill_ids = tuple(dict.fromkeys(skill_ids))
    selected_skills: list[ComposableProductionSkill] = []
    for skill_id in resolved_skill_ids:
        try:
            selected_skill = narrative_skill(skill_id)
        except KeyError as exc:
            raise ValueError(f"Unknown narrative Production Skill: {skill_id}") from exc
        if not any(selected_skill.supports(role) for role in resolved_roles):
            requested = ", ".join(role.value for role in resolved_roles) or "<none>"
            supported = ", ".join(
                role.value for role in selected_skill.role_prompts
            )
            raise ValueError(
                f"Narrative Production Skill '{skill_id}' does not support any "
                f"requested role ({requested}); supported roles: {supported}"
            )
        selected_skills.append(selected_skill)
    return resolved_skill_ids, tuple(selected_skills)


def _render_narrative_skill_prompt(
    selected_skills: Sequence[ComposableProductionSkill],
    *,
    roles: Sequence[ProductionSkillRole],
) -> str:
    resolved_roles = tuple(dict.fromkeys(roles))
    parts: list[str] = []
    for selected_skill in selected_skills:
        sections = [
            f"Agent 已根据当前任务语义选择叙事技能“{selected_skill.label}”"
            f"（{selected_skill.id}）：{selected_skill.constitution}"
        ]
        sections.extend(
            f"{_ROLE_LABELS[role]}的技能补充："
            f"{selected_skill.role_prompts[role]}"
            for role in resolved_roles
            if selected_skill.supports(role)
        )
        parts.append("\n".join(sections))
    return "\n\n".join(parts)


def compile_narrative_skill_prompt(
    skill_ids: Sequence[str],
    *,
    roles: Sequence[ProductionSkillRole],
) -> str:
    """Compile only Agent-selected narrative methods for the requested roles."""

    _resolved_ids, selected_skills = _resolve_narrative_skills(
        skill_ids,
        roles=roles,
    )
    return _render_narrative_skill_prompt(selected_skills, roles=roles)


@lru_cache(maxsize=1)
def production_workflow() -> str:
    """Return the shared end-to-end production guidance loaded from Markdown."""

    return load_production_workflow()


def compile_production_prompt(
    profile: ProductionProfile,
    *,
    roles: Sequence[ProductionSkillRole],
    include_constitution: bool = True,
    additional_skill_ids: Sequence[str] = (),
) -> CompiledProductionPrompt:
    """Compose shared roles, one format, and Agent-selected narrative methods.

    This compiler validates and loads explicit ids. It deliberately does not infer
    narrative methods from the user's words, the production profile, or keywords.
    """

    skill = production_skill(profile)
    resolved_roles = tuple(dict.fromkeys(roles))
    resolved_skill_ids, selected_skills = _resolve_narrative_skills(
        additional_skill_ids,
        roles=resolved_roles,
    )
    policy_by_role = {
        ProductionSkillRole.CONCEPT: skill.concept,
        ProductionSkillRole.WRITER: skill.writer,
        ProductionSkillRole.PLANNER: skill.planner,
        ProductionSkillRole.CRITIC: skill.critic,
        ProductionSkillRole.STORY_REPAIR: skill.story_repair,
        ProductionSkillRole.REVISION_VERIFIER: skill.revision_verifier,
        ProductionSkillRole.FINAL_COLD_READ: skill.final_cold_read,
        ProductionSkillRole.REVIEW_ROUTER: skill.review_router,
        ProductionSkillRole.DIRECTOR: skill.director,
        ProductionSkillRole.ASSET_REVIEWER: skill.asset_reviewer,
        ProductionSkillRole.ROUTE_PLANNER: skill.route_planner,
        ProductionSkillRole.VIDEO_PROMPT_COMPILER: skill.video_prompt_compiler,
        ProductionSkillRole.EPISODE_REVIEWER: skill.episode_reviewer,
    }
    parts = [f"当前制片技能：{skill.label}。"]
    parts.extend(
        f"{_ROLE_LABELS[role]}的通用职责：{shared_role_prompt(role)}"
        for role in resolved_roles
    )
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
        f"{_ROLE_LABELS[role]}的当前格式补充：{policy_by_role[role]}"
        for role in resolved_roles
    )
    narrative_prompt = _render_narrative_skill_prompt(
        selected_skills,
        roles=resolved_roles,
    )
    if narrative_prompt:
        parts.append(narrative_prompt)
    return CompiledProductionPrompt(
        profile=profile,
        roles=resolved_roles,
        text="\n\n".join(parts),
        selected_skill_ids=resolved_skill_ids,
    )


__all__ = [
    "CompiledProductionPrompt",
    "NarrativeSkillDescriptor",
    "ProductionSkill",
    "ProductionSkillRole",
    "compile_narrative_skill_prompt",
    "compile_production_prompt",
    "narrative_skill",
    "narrative_skill_catalog",
    "production_skill",
    "production_skill_catalog",
    "production_workflow",
    "shared_role_prompt",
]
