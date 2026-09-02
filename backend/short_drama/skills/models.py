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
