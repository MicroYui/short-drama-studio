"""Typed runtime representation compiled from Markdown Production Skills."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from short_drama.models import ProductionProfile


class ProductionSkillRole(StrEnum):
    CONCEPT = "CONCEPT"
    WRITER = "WRITER"
    PLANNER = "PLANNER"
    CRITIC = "CRITIC"
    STORY_REPAIR = "STORY_REPAIR"
    REVISION_VERIFIER = "REVISION_VERIFIER"
    FINAL_COLD_READ = "FINAL_COLD_READ"
    REVIEW_ROUTER = "REVIEW_ROUTER"
    DIRECTOR = "DIRECTOR"
    ASSET_REVIEWER = "ASSET_REVIEWER"
    ROUTE_PLANNER = "ROUTE_PLANNER"
    VIDEO_PROMPT_COMPILER = "VIDEO_PROMPT_COMPILER"
    EPISODE_REVIEWER = "EPISODE_REVIEWER"


@dataclass(frozen=True)
class ProductionSkill:
    profile: ProductionProfile
    label: str
    medium: str
    constitution: str
    concept: str
    writer: str
    planner: str
    critic: str
    story_repair: str
    revision_verifier: str
    final_cold_read: str
    review_router: str
    director: str
    asset_reviewer: str
    route_planner: str
    video_prompt_compiler: str
    episode_reviewer: str
    playbook: str
    recommended_playbook: tuple[str, ...]
    user_overridable: bool = True


@dataclass(frozen=True)
class ComposableProductionSkill:
    """One optional, role-scoped method selected semantically by an Agent."""

    id: str
    label: str
    description: str
    constitution: str
    role_prompts: dict[ProductionSkillRole, str]
    source_path: str
    user_overridable: bool = True

    def supports(self, role: ProductionSkillRole) -> bool:
        return role in self.role_prompts


@dataclass(frozen=True)
class NarrativeSkillDescriptor:
    """Compact metadata an Agent can inspect before choosing narrative methods."""

    id: str
    label: str
    description: str
    supported_roles: tuple[ProductionSkillRole, ...]
    user_overridable: bool = True


@dataclass(frozen=True)
class ProviderPromptSkill:
    provider: str
    label: str
    instructions: str
    reference_paths: tuple[str, ...]


@dataclass(frozen=True)
class CompiledProductionPrompt:
    profile: ProductionProfile
    roles: tuple[ProductionSkillRole, ...]
    text: str
    selected_skill_ids: tuple[str, ...] = ()
