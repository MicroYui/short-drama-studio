"""File-backed Production Skills used by the short-drama Agent runtime."""

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
from short_drama.skills.models import (
    ComposableProductionSkill,
    CompiledProductionPrompt,
    NarrativeSkillDescriptor,
    ProviderPromptSkill,
    ProductionSkill,
    ProductionSkillRole,
)

__all__ = [
    "ComposableProductionSkill",
    "CompiledProductionPrompt",
    "DEFAULT_NARRATIVE_SKILLS_ROOT",
    "DEFAULT_PROVIDER_SKILLS_ROOT",
    "DEFAULT_PRODUCTION_SKILLS_ROOT",
    "DEFAULT_SHARED_SKILL_ROOT",
    "NarrativeSkillDescriptor",
    "ProviderPromptSkill",
    "ProductionSkill",
    "ProductionSkillRole",
    "load_narrative_skills",
    "load_provider_prompt_skills",
    "load_production_skills",
    "load_shared_role_prompts",
]
