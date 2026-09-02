"""Load editable Markdown Production Skills into typed runtime objects."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TypeAlias, cast

from short_drama.models import ProductionProfile
from short_drama.skills.models import (
    ProviderPromptSkill,
    ProductionSkill,
    ProductionSkillRole,
)


FrontmatterValue: TypeAlias = str | bool | int | list[str]
Frontmatter: TypeAlias = dict[str, FrontmatterValue]

DEFAULT_PRODUCTION_SKILLS_ROOT = Path(__file__).resolve().parent / "formats"
DEFAULT_PRODUCTION_WORKFLOW_PATH = (
    Path(__file__).resolve().parent / "workflows" / "end_to_end.md"
)
DEFAULT_PROVIDER_SKILLS_ROOT = Path(__file__).resolve().parent / "providers"


def _parse_frontmatter_value(raw_value: str, *, path: Path) -> FrontmatterValue:
    value = raw_value.strip()
    if not value:
        raise ValueError(f"Empty frontmatter value in {path}")
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    if value.isdecimal():
        return int(value)
    if value.startswith("[") or value.startswith('"'):
        try:
            decoded: object = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON-style YAML value in {path}: {value}") from exc
        if isinstance(decoded, str):
            return decoded
        if isinstance(decoded, list):
            decoded_items = cast(list[object], decoded)
            if all(isinstance(item, str) for item in decoded_items):
                return cast(list[str], decoded_items)
        raise ValueError(f"Unsupported frontmatter value in {path}: {value}")
    return value


def _read_markdown(path: Path) -> tuple[Frontmatter, str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, _clean_markdown_body(text, path=path)
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise ValueError(f"Unclosed frontmatter in {path}")
    metadata: Frontmatter = {}
    for line in text[4:closing].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition(":")
        if not separator or not key.strip():
            raise ValueError(f"Invalid frontmatter line in {path}: {line}")
        metadata[key.strip()] = _parse_frontmatter_value(raw_value, path=path)
    return metadata, _clean_markdown_body(text[closing + 5 :], path=path)


def _clean_markdown_body(text: str, *, path: Path) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        raise ValueError(f"Markdown body is empty in {path}")
    return body


def _required_string(metadata: Frontmatter, key: str, *, path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Frontmatter field '{key}' must be a string in {path}")
    return value.strip()


def _optional_bool(
    metadata: Frontmatter,
    key: str,
    *,
    default: bool,
    path: Path,
) -> bool:
    value = metadata.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Frontmatter field '{key}' must be a boolean in {path}")
    return value


def _optional_string_list(
    metadata: Frontmatter,
    key: str,
    *,
    path: Path,
) -> tuple[str, ...]:
    value = metadata.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Frontmatter field '{key}' must be a string list in {path}")
    return tuple(item.strip() for item in value if item.strip())


def _load_playbook(path: Path) -> tuple[str, tuple[str, ...]]:
    _metadata, body = _read_markdown(path)
    steps: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\d+\.\s+(.+?)\s*$", line)
        if match is None:
            continue
        label = match.group(1).strip()
        if label.startswith("**") and label.endswith("**"):
            label = label[2:-2].strip()
        steps.append(label)
    if not steps:
        raise ValueError(f"Playbook must contain a top-level ordered outline in {path}")
    return body, tuple(steps)


def load_production_workflow(
    path: Path = DEFAULT_PRODUCTION_WORKFLOW_PATH,
) -> str:
    """Load the shared, human-reviewable production process guidance."""

    _metadata, body = _read_markdown(path)
    return body


def _load_role_prompts(skill_directory: Path) -> dict[ProductionSkillRole, str]:
    prompts: dict[ProductionSkillRole, str] = {}
    for role in ProductionSkillRole:
        role_path = skill_directory / "roles" / f"{role.value.casefold()}.md"
        metadata, body = _read_markdown(role_path)
        references = _optional_string_list(metadata, "references", path=role_path)
        sections = [body]
        for relative_path in references:
            reference_path = skill_directory / relative_path
            if not reference_path.is_file():
                raise ValueError(
                    f"Role reference '{relative_path}' does not exist for {role_path}"
                )
            _reference_metadata, reference_body = _read_markdown(reference_path)
            sections.append(
                f"补充参考方法（{relative_path}）：\n{reference_body}"
            )
        prompts[role] = "\n\n".join(sections)
    return prompts


def _load_skill(skill_directory: Path) -> ProductionSkill:
    skill_path = skill_directory / "SKILL.md"
    metadata, constitution = _read_markdown(skill_path)
    try:
        profile = ProductionProfile(_required_string(metadata, "profile", path=skill_path))
    except ValueError as exc:
        raise ValueError(f"Unknown production profile in {skill_path}") from exc
    playbook_path = skill_directory / _required_string(
        metadata,
        "playbook",
        path=skill_path,
    )
    playbook, playbook_outline = _load_playbook(playbook_path)
    prompts = _load_role_prompts(skill_directory)
    return ProductionSkill(
        profile=profile,
        label=_required_string(metadata, "label", path=skill_path),
        medium=_required_string(metadata, "medium", path=skill_path),
        constitution=constitution,
        concept=prompts[ProductionSkillRole.CONCEPT],
        writer=prompts[ProductionSkillRole.WRITER],
        planner=prompts[ProductionSkillRole.PLANNER],
        critic=prompts[ProductionSkillRole.CRITIC],
        review_router=prompts[ProductionSkillRole.REVIEW_ROUTER],
        director=prompts[ProductionSkillRole.DIRECTOR],
        asset_reviewer=prompts[ProductionSkillRole.ASSET_REVIEWER],
        route_planner=prompts[ProductionSkillRole.ROUTE_PLANNER],
        video_prompt_compiler=prompts[ProductionSkillRole.VIDEO_PROMPT_COMPILER],
        episode_reviewer=prompts[ProductionSkillRole.EPISODE_REVIEWER],
        playbook=playbook,
        recommended_playbook=playbook_outline,
        user_overridable=_optional_bool(
            metadata,
            "user_overridable",
            default=True,
            path=skill_path,
        ),
    )


def load_production_skills(
    root: Path = DEFAULT_PRODUCTION_SKILLS_ROOT,
) -> tuple[ProductionSkill, ...]:
    """Compile the on-disk Markdown catalog without owning Agent orchestration."""

    skill_directories = sorted(
        path.parent for path in root.glob("*/SKILL.md") if path.is_file()
    )
    if not skill_directories:
        raise ValueError(f"No Production Skills found in {root}")
    skills = tuple(_load_skill(path) for path in skill_directories)
    profiles = [skill.profile for skill in skills]
    if len(profiles) != len(set(profiles)):
        raise ValueError(f"Duplicate production profile in {root}")
    missing_profiles = set(ProductionProfile).difference(profiles)
    if missing_profiles:
        missing = ", ".join(sorted(profile.value for profile in missing_profiles))
        raise ValueError(f"Missing Production Skills for: {missing}")

    return skills


def _load_provider_prompt_skill(skill_directory: Path) -> ProviderPromptSkill:
    skill_path = skill_directory / "SKILL.md"
    metadata, body = _read_markdown(skill_path)
    reference_paths = _optional_string_list(
        metadata,
        "prompt_references",
        path=skill_path,
    )
    sections = [body]
    for relative_path in reference_paths:
        reference_path = skill_directory / relative_path
        if not reference_path.is_file():
            raise ValueError(
                f"Provider prompt reference '{relative_path}' does not exist for "
                f"{skill_path}"
            )
        _reference_metadata, reference_body = _read_markdown(reference_path)
        sections.append(f"Prompt 编译参考（{relative_path}）：\n{reference_body}")
    return ProviderPromptSkill(
        provider=_required_string(metadata, "provider", path=skill_path),
        label=_required_string(metadata, "label", path=skill_path),
        instructions="\n\n".join(sections),
        reference_paths=reference_paths,
    )


def load_provider_prompt_skills(
    root: Path = DEFAULT_PROVIDER_SKILLS_ROOT,
) -> tuple[ProviderPromptSkill, ...]:
    """Load model-specific prompt compilers without owning Provider execution."""

    skill_directories = sorted(
        path.parent for path in root.glob("*/SKILL.md") if path.is_file()
    )
    if not skill_directories:
        raise ValueError(f"No Provider Prompt Skills found in {root}")
    skills = tuple(_load_provider_prompt_skill(path) for path in skill_directories)
    providers = [skill.provider for skill in skills]
    if len(providers) != len(set(providers)):
        raise ValueError(f"Duplicate Provider Prompt Skill in {root}")
    return skills
