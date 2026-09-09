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
from short_drama.skill_ids import canonical_method_skill_id, canonical_skill_ids
from short_drama.skills.loader import (
    load_narrative_skills,
    load_production_method_skills,
    load_production_skills,
    load_production_workflow,
    load_shared_role_prompts,
)
from short_drama.skills.models import (
    ComposableProductionSkill,
    CompiledProductionPrompt,
    NarrativeSkillDescriptor,
    ProductionMethodSkillDescriptor,
    ProductionPromptStage,
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
    ProductionSkillRole.ASSET_PLANNER: "资产规划",
    ProductionSkillRole.ASSET_BUILDER: "资产制作",
    ProductionSkillRole.ASSET_VERIFIER: "资产验收",
    ProductionSkillRole.ASSET_REVIEWER: "旧版资产职责",
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
def _production_method_skill_library() -> tuple[ComposableProductionSkill, ...]:
    return load_production_method_skills()


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


def production_method_skill(skill_id: str) -> ComposableProductionSkill:
    """Resolve one Agent-selected production method by its open, file-backed id."""

    skill_id = canonical_method_skill_id(skill_id)
    for skill in _production_method_skill_library():
        if skill.id == skill_id:
            return skill
    raise KeyError(skill_id)


def production_method_skill_catalog() -> tuple[ProductionMethodSkillDescriptor, ...]:
    """Return compact method metadata for semantic selection by an Agent."""

    return tuple(
        ProductionMethodSkillDescriptor(
            id=skill.id,
            label=skill.label,
            description=skill.description,
            supported_roles=tuple(skill.role_prompts),
            provenance=skill.provenance,
            upstream_sources=skill.upstream_sources,
            user_overridable=skill.user_overridable,
        )
        for skill in _production_method_skill_library()
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


def _resolve_production_method_skills(
    skill_ids: Sequence[str],
    *,
    roles: Sequence[ProductionSkillRole],
) -> tuple[tuple[str, ...], tuple[ComposableProductionSkill, ...]]:
    resolved_roles = tuple(dict.fromkeys(roles))
    resolved_skill_ids = tuple(dict.fromkeys(canonical_skill_ids(skill_ids)))
    selected_skills: list[ComposableProductionSkill] = []
    for skill_id in resolved_skill_ids:
        try:
            selected_skill = production_method_skill(skill_id)
        except KeyError as exc:
            raise ValueError(f"Unknown Production Method Skill: {skill_id}") from exc
        if any(selected_skill.supports(role) for role in resolved_roles):
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


def _render_production_method_skill_prompt(
    selected_skills: Sequence[ComposableProductionSkill],
    *,
    roles: Sequence[ProductionSkillRole],
    stage: ProductionPromptStage | None = None,
) -> str:
    resolved_roles = tuple(dict.fromkeys(roles))
    parts: list[str] = []
    shared_prefixes: dict[str, str] = {}
    shared_suffixes: dict[str, str] = {}
    for selected_skill in selected_skills:
        if selected_skill.shared_prefix is not None:
            shared_prefixes.setdefault(*selected_skill.shared_prefix)
        if selected_skill.shared_suffix is not None:
            shared_suffixes.setdefault(*selected_skill.shared_suffix)
        sections = [
            f"Agent 已根据当前项目完整语义选择制片方法技能“{selected_skill.label}”"
            f"（{selected_skill.id}）：{selected_skill.description}"
        ]
        sections.extend(
            f"{_ROLE_LABELS[role]}的制片方法补充：{role_prompt}"
            for role in resolved_roles
            if (role_prompt := selected_skill.prompt_for(role, stage=stage))
            is not None
        )
        parts.append("\n".join(sections))
    return "\n\n".join([*shared_prefixes.values(), *parts, *shared_suffixes.values()])


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


def compile_production_method_skill_prompt(
    skill_ids: Sequence[str],
    *,
    roles: Sequence[ProductionSkillRole],
    stage: ProductionPromptStage | None = None,
) -> str:
    """Compile only Agent-selected production methods for requested roles."""

    _resolved_ids, selected_skills = _resolve_production_method_skills(
        skill_ids,
        roles=roles,
    )
    return _render_production_method_skill_prompt(
        selected_skills,
        roles=roles,
        stage=stage,
    )


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
    method_skill_ids: Sequence[str] = (),
    stage: ProductionPromptStage | None = None,
) -> CompiledProductionPrompt:
    """Compose shared roles, one format, and Agent-selected production methods.

    This compiler validates and loads explicit ids. It deliberately does not infer
    narrative or production methods from words, profiles, categories, or keywords.
    """

    skill = production_skill(profile)
    resolved_roles = tuple(dict.fromkeys(roles))
    resolved_skill_ids, selected_skills = _resolve_narrative_skills(
        additional_skill_ids,
        roles=resolved_roles,
    )
    resolved_method_skill_ids, selected_method_skills = (
        _resolve_production_method_skills(
            method_skill_ids,
            roles=resolved_roles,
        )
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
        ProductionSkillRole.ASSET_PLANNER: skill.asset_planner,
        ProductionSkillRole.ASSET_BUILDER: skill.asset_builder,
        ProductionSkillRole.ASSET_VERIFIER: skill.asset_verifier,
        ProductionSkillRole.ASSET_REVIEWER: skill.asset_reviewer,
        ProductionSkillRole.ROUTE_PLANNER: skill.route_planner,
        ProductionSkillRole.VIDEO_PROMPT_COMPILER: skill.video_prompt_compiler,
        ProductionSkillRole.EPISODE_REVIEWER: skill.episode_reviewer,
    }
    parts = [f"当前制片技能：{skill.label}。"]
    if stage is not None:
        parts.append(f"当前制片阶段：{stage.value}。只装载该阶段与当前角色需要的方法。")
    parts.extend(
        f"{_ROLE_LABELS[role]}的通用职责：{shared_role_prompt(role)}"
        for role in resolved_roles
    )
    if include_constitution:
        parts.append(f"类型创作原则：{skill.constitution}")
    if ProductionSkillRole.PLANNER in resolved_roles:
        parts.append(
            "制作规划只安排当前目标与依赖；完整端到端文档与类型 "
            "playbook 仅在需要跨阶段重规划时按需读取，不进入每个 Worker "
            "prompt。精简可用阶段线索："
            + " → ".join(skill.recommended_playbook)
        )
    parts.extend(
        f"{_ROLE_LABELS[role]}的当前格式补充：{policy_by_role[role]}"
        for role in resolved_roles
        if policy_by_role[role].strip()
    )
    method_prompt = _render_production_method_skill_prompt(
        selected_method_skills,
        roles=resolved_roles,
        stage=stage,
    )
    if method_prompt:
        parts.append(method_prompt)
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
        selected_method_skill_ids=resolved_method_skill_ids,
        stage=stage,
    )


__all__ = [
    "CompiledProductionPrompt",
    "NarrativeSkillDescriptor",
    "ProductionMethodSkillDescriptor",
    "ProductionPromptStage",
    "ProductionSkill",
    "ProductionSkillRole",
    "compile_narrative_skill_prompt",
    "compile_production_method_skill_prompt",
    "compile_production_prompt",
    "narrative_skill",
    "narrative_skill_catalog",
    "production_method_skill",
    "production_method_skill_catalog",
    "production_skill",
    "production_skill_catalog",
    "production_workflow",
    "shared_role_prompt",
]
