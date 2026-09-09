"""Typed runtime representation compiled from Markdown Production Skills."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

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
    ASSET_PLANNER = "ASSET_PLANNER"
    ASSET_BUILDER = "ASSET_BUILDER"
    ASSET_VERIFIER = "ASSET_VERIFIER"
    ASSET_REVIEWER = "ASSET_REVIEWER"
    ROUTE_PLANNER = "ROUTE_PLANNER"
    VIDEO_PROMPT_COMPILER = "VIDEO_PROMPT_COMPILER"
    EPISODE_REVIEWER = "EPISODE_REVIEWER"


LEGACY_ROLE_FILE_ALIASES: Final[dict[ProductionSkillRole, ProductionSkillRole]] = {
    ProductionSkillRole.STORY_REPAIR: ProductionSkillRole.CONCEPT,
    ProductionSkillRole.REVIEW_ROUTER: ProductionSkillRole.PLANNER,
}


class ProductionPromptStage(StrEnum):
    """Explicit production stage used to select only relevant Skill sections."""

    ORCHESTRATION = "ORCHESTRATION"
    STORY = "STORY"
    STORYBOARD = "STORYBOARD"
    ASSETS = "ASSETS"
    VISUAL_GEN = "VISUAL_GEN"
    POST = "POST"


class SkillProvenanceKind(StrEnum):
    """How a local Skill relates to any upstream material."""

    PROJECT_ORIGINAL = "PROJECT_ORIGINAL"
    PROJECT_SYNTHESIS = "PROJECT_SYNTHESIS"
    OFFICIAL_ORIGINAL = "OFFICIAL_ORIGINAL"
    OFFICIAL_ORIGINAL_WITH_ADAPTER = "OFFICIAL_ORIGINAL_WITH_ADAPTER"
    OFFICIAL_TRANSLATION = "OFFICIAL_TRANSLATION"
    OFFICIAL_TRANSLATION_WITH_ADAPTER = "OFFICIAL_TRANSLATION_WITH_ADAPTER"
    UPSTREAM_OFFICIAL_WITH_ADAPTER = "UPSTREAM_OFFICIAL_WITH_ADAPTER"
    UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER = (
        "UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER"
    )
    UPSTREAM_COMMUNITY_WITH_ADAPTER = "UPSTREAM_COMMUNITY_WITH_ADAPTER"


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
    asset_planner: str
    asset_builder: str
    asset_verifier: str
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
    shared_prefix: tuple[str, str] | None = None
    shared_suffix: tuple[str, str] | None = None
    stage_role_prompts: dict[
        tuple[ProductionPromptStage, ProductionSkillRole],
        str,
    ] | None = None
    provenance: SkillProvenanceKind = SkillProvenanceKind.PROJECT_ORIGINAL
    upstream_sources: tuple[str, ...] = ()
    user_overridable: bool = True

    def supports(self, role: ProductionSkillRole) -> bool:
        return role in self.role_prompts

    def prompt_for(
        self,
        role: ProductionSkillRole,
        *,
        stage: ProductionPromptStage | None = None,
    ) -> str | None:
        """Return the narrowest declared prompt for an explicit stage and role."""

        if stage is not None and self.stage_role_prompts is not None:
            stage_prompt = self.stage_role_prompts.get((stage, role))
            if stage_prompt is not None:
                return stage_prompt
        return self.role_prompts.get(role)


@dataclass(frozen=True)
class NarrativeSkillDescriptor:
    """Compact metadata an Agent can inspect before choosing narrative methods."""

    id: str
    label: str
    description: str
    supported_roles: tuple[ProductionSkillRole, ...]
    user_overridable: bool = True


@dataclass(frozen=True)
class ProductionMethodSkillDescriptor:
    """Compact metadata an Agent can inspect before choosing production methods."""

    id: str
    label: str
    description: str
    supported_roles: tuple[ProductionSkillRole, ...]
    provenance: SkillProvenanceKind = SkillProvenanceKind.PROJECT_ORIGINAL
    upstream_sources: tuple[str, ...] = ()
    user_overridable: bool = True


@dataclass(frozen=True)
class ProviderPromptSkill:
    provider: str
    label: str
    instructions: str
    reference_paths: tuple[str, ...]
    provenance: SkillProvenanceKind = SkillProvenanceKind.PROJECT_ORIGINAL
    upstream_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledProductionPrompt:
    profile: ProductionProfile
    roles: tuple[ProductionSkillRole, ...]
    text: str
    selected_skill_ids: tuple[str, ...] = ()
    selected_method_skill_ids: tuple[str, ...] = ()
    stage: ProductionPromptStage | None = None
