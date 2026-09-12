"""On-demand product views of the same method knowledge used by Agents."""

from short_drama.models import DomainModel
from short_drama.production_profiles import production_method_skill
from short_drama.skills.models import MethodCategory
from pydantic import Field


class StudioMethodSection(DomainModel):
    role: str
    stage: str | None = None
    text: str


class StudioMethodDetail(DomainModel):
    id: str
    label: str
    description: str
    principles: str
    sections: list[StudioMethodSection]
    provenance: str
    upstream_sources: list[str]
    user_overridable: bool
    category: MethodCategory = MethodCategory.CREATIVE
    requirements: list[str] = Field(default_factory=list[str])


def method_detail(skill_id: str) -> StudioMethodDetail:
    method = production_method_skill(skill_id)
    return StudioMethodDetail(id=method.id, label=method.label, description=method.description,
        principles=method.constitution, provenance=method.provenance.value,
        upstream_sources=list(method.upstream_sources), user_overridable=method.user_overridable,
        category=method.category, requirements=list(method.requirements),
        sections=[*[StudioMethodSection(role=role.value, text=text) for role, text in method.role_prompts.items()],
            *[StudioMethodSection(role=role.value, stage=stage.value, text=text)
                for (stage, role), text in (method.stage_role_prompts or {}).items()]])
