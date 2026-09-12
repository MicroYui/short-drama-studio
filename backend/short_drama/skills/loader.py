"""Load editable Markdown Production Skills into typed runtime objects."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TypeAlias, cast

from short_drama.models import ProductionProfile
from short_drama.skills.models import (
    ComposableProductionSkill,
    LEGACY_ROLE_FILE_ALIASES,
    MethodCategory,
    ProviderPromptSkill,
    ProductionPromptStage,
    ProductionSkill,
    ProductionSkillRole,
    SkillProvenanceKind,
)


FrontmatterStringLists: TypeAlias = dict[str, list[str]]
FrontmatterValue: TypeAlias = str | bool | int | list[str] | FrontmatterStringLists
Frontmatter: TypeAlias = dict[str, FrontmatterValue]

DEFAULT_PRODUCTION_SKILLS_ROOT = Path(__file__).resolve().parent / "formats"
DEFAULT_PRODUCTION_WORKFLOW_PATH = (
    Path(__file__).resolve().parent / "workflows" / "end_to_end.md"
)
DEFAULT_PROVIDER_SKILLS_ROOT = Path(__file__).resolve().parent / "providers"
DEFAULT_SHARED_SKILL_ROOT = Path(__file__).resolve().parent / "shared"
DEFAULT_NARRATIVE_SKILLS_ROOT = Path(__file__).resolve().parent / "narrative"
DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT = Path(__file__).resolve().parent / "methods"
DEFAULT_OFFICIAL_MINIMAX_H3_SKILLS_ROOT = (
    Path(__file__).resolve().parent / "sources" / "minimax_h3" / "original"
)
DEFAULT_OFFICIAL_MINIMAX_DESIGN_WORKFLOWS_ROOT = (
    Path(__file__).resolve().parent
    / "sources"
    / "minimax_design"
    / "3.0.11"
    / "workflows"
)
DEFAULT_MINIMAX_H3_SKILL_TRANSLATIONS_ROOT = (
    Path(__file__).resolve().parent / "sources" / "minimax_h3" / "translations"
)


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
    if value.startswith("[") or value.startswith("{") or value.startswith('"'):
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
        if isinstance(decoded, dict):
            decoded_mapping = cast(dict[object, object], decoded)
            if all(
                isinstance(key, str)
                and isinstance(items, list)
                and all(isinstance(item, str) for item in cast(list[object], items))
                for key, items in decoded_mapping.items()
            ):
                return {
                    cast(str, key): cast(list[str], items)
                    for key, items in decoded_mapping.items()
                }
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
    manifest = metadata.pop("manifest", None)
    if manifest is not None:
        if not isinstance(manifest, str):
            raise ValueError(f"manifest must name a JSON file in {path}")
        manifest_path = path.parent / manifest
        try:
            raw_manifest = cast(object, json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read Skill manifest {manifest_path}: {exc}") from exc
        if not isinstance(raw_manifest, dict):
            raise ValueError(f"Skill manifest must be an object: {manifest_path}")
        expanded: Frontmatter = {}
        for key, value in cast(dict[str, object], raw_manifest).items():
            expanded[key] = _parse_frontmatter_value(json.dumps(value, ensure_ascii=False), path=manifest_path)
        # A small frontmatter override is useful for user-authored methods and
        # keeps the existing editable Markdown contract compatible.
        metadata = {**expanded, **metadata}
    return metadata, _clean_markdown_body(text[closing + 5 :], path=path)


def _read_source_document(path: Path) -> str:
    """Read vendored Markdown/TXT without interpreting upstream frontmatter.

    External Skill packages may use full YAML that is intentionally richer than
    this project's compact frontmatter parser.  Their source stays byte-for-byte
    on disk; the runtime only removes an optional metadata block before inserting
    the authored body into an Agent prompt.
    """

    return _source_markdown_body(path)


def _source_markdown_body(path: Path) -> str:
    """Return an upstream Markdown body while leaving the vendored file untouched."""

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if text.startswith("---\n"):
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise ValueError(f"Unclosed source frontmatter in {path}")
        text = text[closing + 5 :]
    body = text.strip()
    if not body:
        raise ValueError(f"Source document body is empty in {path}")
    return body


def _markdown_heading(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    level = 0
    while level < len(stripped) and stripped[level] == "#":
        level += 1
    if level < 1 or level > 6 or level >= len(stripped) or stripped[level] != " ":
        return None
    title = stripped[level + 1 :].strip()
    return None if not title else (level, title)


def _read_source_section(path: Path, heading_title: str) -> str:
    """Read one exact Markdown section from an upstream source document.

    Adapters name the upstream heading explicitly.  This is declarative
    role-to-source composition, not keyword routing over a user's request.
    """

    lines = _source_markdown_body(path).splitlines()
    matches = [
        index
        for index, line in enumerate(lines)
        if (heading := _markdown_heading(line)) is not None
        and heading[1] == heading_title
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Source heading '{heading_title}' must appear exactly once in {path}"
        )
    start = matches[0]
    start_heading = _markdown_heading(lines[start])
    if start_heading is None:  # pragma: no cover - protected by the match above
        raise AssertionError("Resolved source section is not a heading")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        candidate = _markdown_heading(lines[index])
        if candidate is not None and candidate[0] <= start_heading[0]:
            end = index
            break
    section = "\n".join(lines[start:end]).strip()
    if not section:
        raise ValueError(f"Source section '{heading_title}' is empty in {path}")
    return section


def _read_role_source_section(
    skill_directory: Path,
    source_reference: str,
    *,
    skill_path: Path,
) -> tuple[str, str]:
    relative_path, separator, heading_title = source_reference.partition("::")
    if not separator or not relative_path.strip() or not heading_title.strip():
        raise ValueError(
            "Role source section must use '<relative path>::<exact heading>' in "
            f"{skill_path}: {source_reference}"
        )
    source_path = skill_directory / relative_path.strip()
    if not source_path.is_file():
        raise ValueError(
            f"Role source section '{relative_path}' does not exist for {skill_path}"
        )
    return relative_path.strip(), _read_source_section(
        source_path,
        heading_title.strip(),
    )


def _render_official_reference(*, source_label: str, body: str) -> str:
    # Source identity is retained in the Skill metadata and loader mapping.
    # The Agent only needs the selected method text; repeating provenance tags
    # around every section adds prompt noise and makes the upstream material
    # look like a separate instruction hierarchy.  Shared role guidance owns
    # the one-time Tool, interaction, and artifact boundary.
    _ = source_label
    return body.strip()


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


def _optional_provenance(
    metadata: Frontmatter,
    *,
    path: Path,
) -> SkillProvenanceKind:
    value = metadata.get("provenance", SkillProvenanceKind.PROJECT_ORIGINAL.value)
    if not isinstance(value, str):
        raise ValueError(f"Frontmatter field 'provenance' must be a string in {path}")
    try:
        return SkillProvenanceKind(value)
    except ValueError as exc:
        raise ValueError(f"Unknown Skill provenance '{value}' in {path}") from exc


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


def _optional_role_reference_map(
    metadata: Frontmatter,
    *,
    path: Path,
    key: str = "role_references",
) -> dict[ProductionSkillRole, tuple[str, ...]]:
    value = metadata.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(
            f"Frontmatter field '{key}' must be an object in {path}"
        )
    references: dict[ProductionSkillRole, tuple[str, ...]] = {}
    for raw_role, raw_paths in value.items():
        try:
            role = ProductionSkillRole(raw_role)
        except ValueError as exc:
            raise ValueError(
                f"Unknown role reference target '{raw_role}' in {path}"
            ) from exc
        references[role] = tuple(
            item.strip() for item in raw_paths if item.strip()
        )
    return references


def _optional_stage_role_reference_map(
    metadata: Frontmatter,
    *,
    path: Path,
    key: str = "stage_role_source_sections",
) -> dict[tuple[ProductionPromptStage, ProductionSkillRole], tuple[str, ...]]:
    value = metadata.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Frontmatter field '{key}' must be an object in {path}")
    references: dict[
        tuple[ProductionPromptStage, ProductionSkillRole],
        tuple[str, ...],
    ] = {}
    for raw_scope, raw_paths in value.items():
        raw_stage, separator, raw_role = raw_scope.partition(".")
        if not separator or not raw_stage or not raw_role:
            raise ValueError(
                f"Stage-role source key must use '<STAGE>.<ROLE>' in {path}: "
                f"{raw_scope}"
            )
        try:
            stage = ProductionPromptStage(raw_stage)
            role = ProductionSkillRole(raw_role)
        except ValueError as exc:
            raise ValueError(
                f"Unknown stage-role source target '{raw_scope}' in {path}"
            ) from exc
        references[(stage, role)] = tuple(
            item.strip() for item in raw_paths if item.strip()
        )
    return references


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


def _declared_roles(
    metadata: Frontmatter,
    *,
    path: Path,
) -> tuple[ProductionSkillRole, ...]:
    values = _optional_string_list(metadata, "roles", path=path)
    if not values:
        raise ValueError(
            f"Frontmatter field 'roles' must declare at least one role in {path}"
        )
    roles: list[ProductionSkillRole] = []
    for value in values:
        try:
            role = ProductionSkillRole(value)
        except ValueError as exc:
            raise ValueError(f"Unknown Production Skill role '{value}' in {path}") from exc
        if role not in roles:
            roles.append(role)
    return tuple(roles)


def _load_role_prompts(
    skill_directory: Path,
    *,
    roles: tuple[ProductionSkillRole, ...] | None = None,
    shared_role_prompt: str | None = None,
    shared_role_footer: str | None = None,
    role_references: dict[ProductionSkillRole, tuple[str, ...]] | None = None,
    role_source_sections: dict[ProductionSkillRole, tuple[str, ...]] | None = None,
    allow_missing_roles: bool = False,
    include_shared: bool = True,
) -> dict[ProductionSkillRole, str]:
    prompts: dict[ProductionSkillRole, str] = {}
    requested_roles = tuple(ProductionSkillRole) if roles is None else roles
    references_by_role = {} if role_references is None else role_references
    source_sections_by_role = (
        {} if role_source_sections is None else role_source_sections
    )
    if shared_role_prompt is not None:
        shared_role_path = skill_directory / shared_role_prompt
        if not shared_role_path.is_file():
            raise ValueError(
                f"Shared role prompt '{shared_role_prompt}' does not exist for "
                f"{skill_directory / 'SKILL.md'}"
            )
        _metadata, body = _read_markdown(shared_role_path)
        footer_body: str | None = None
        if shared_role_footer is not None:
            shared_footer_path = skill_directory / shared_role_footer
            if not shared_footer_path.is_file():
                raise ValueError(
                    f"Shared role footer '{shared_role_footer}' does not exist for "
                    f"{skill_directory / 'SKILL.md'}"
                )
            _footer_metadata, footer_body = _read_markdown(shared_footer_path)
        for role in requested_roles:
            sections = [body] if include_shared else []
            for source_reference in source_sections_by_role.get(role, ()):
                _relative_path, source_body = _read_role_source_section(
                    skill_directory,
                    source_reference,
                    skill_path=skill_directory / "SKILL.md",
                )
                sections.append(
                    _render_official_reference(
                        source_label=source_reference,
                        body=source_body,
                    )
                )
            for relative_path in references_by_role.get(role, ()):
                reference_path = skill_directory / relative_path
                if not reference_path.is_file():
                    raise ValueError(
                        f"Role reference '{relative_path}' does not exist for "
                        f"{skill_directory / 'SKILL.md'}"
                    )
                sections.append(
                    _render_official_reference(
                        source_label=relative_path,
                        body=_read_source_document(reference_path),
                    )
                )
            if footer_body is not None and include_shared:
                sections.append(footer_body)
            prompts[role] = "\n\n".join(sections)
        return prompts
    for role in requested_roles:
        role_path = skill_directory / "roles" / f"{role.value.casefold()}.md"
        if not role_path.is_file() and role in LEGACY_ROLE_FILE_ALIASES:
            current_role = LEGACY_ROLE_FILE_ALIASES[role]
            role_path = skill_directory / "roles" / f"{current_role.value.casefold()}.md"
        if not role_path.is_file():
            if allow_missing_roles:
                prompts[role] = ""
                continue
            raise ValueError(
                f"Production Skill declares role {role.value} but has no role file: {role_path}"
            )
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
        for relative_path in references_by_role.get(role, ()):
            reference_path = skill_directory / relative_path
            if not reference_path.is_file():
                raise ValueError(
                    f"Role reference '{relative_path}' does not exist for {role_path}"
                )
            sections.append(
                _render_official_reference(
                    source_label=relative_path,
                    body=_read_source_document(reference_path),
                )
            )
        for source_reference in source_sections_by_role.get(role, ()):
            _relative_path, source_body = _read_role_source_section(
                skill_directory,
                source_reference,
                skill_path=skill_directory / "SKILL.md",
            )
            sections.append(
                _render_official_reference(
                    source_label=source_reference,
                    body=source_body,
                )
            )
        prompts[role] = "\n\n".join(sections)
    return prompts


def load_shared_role_prompts(
    root: Path = DEFAULT_SHARED_SKILL_ROOT,
    *,
    stage: ProductionPromptStage | None = None,
) -> dict[ProductionSkillRole, str]:
    """Load the medium-neutral Chinese responsibility of every Agent role."""

    prompts = _load_role_prompts(root)
    if stage == ProductionPromptStage.POST:
        _metadata, post_director = _read_markdown(root / "roles" / "post_director.md")
        prompts[ProductionSkillRole.DIRECTOR] = post_director
    return prompts


def _load_composable_skill(skill_directory: Path) -> ComposableProductionSkill:
    skill_path = skill_directory / "SKILL.md"
    metadata, constitution = _read_markdown(skill_path)
    roles = _declared_roles(metadata, path=skill_path)
    role_references = _optional_role_reference_map(metadata, path=skill_path)
    role_source_sections = _optional_role_reference_map(
        metadata,
        path=skill_path,
        key="role_source_sections",
    )
    stage_role_source_sections = _optional_stage_role_reference_map(
        metadata,
        path=skill_path,
    )
    unsupported_reference_roles = set(role_references).union(
        role_source_sections
    ).difference(roles)
    if unsupported_reference_roles:
        unsupported = ", ".join(
            sorted(role.value for role in unsupported_reference_roles)
        )
        raise ValueError(
            f"Role references target undeclared roles ({unsupported}) in {skill_path}"
        )
    unsupported_stage_roles = {
        role for _stage, role in stage_role_source_sections if role not in roles
    }
    if unsupported_stage_roles:
        unsupported = ", ".join(
            sorted(role.value for role in unsupported_stage_roles)
        )
        raise ValueError(
            f"Stage-role references target undeclared roles ({unsupported}) in "
            f"{skill_path}"
        )
    if "constitution_references" in metadata:
        raise ValueError(
            "Whole upstream documents cannot be executable Skill constitutions; "
            f"declare role_source_sections or role_references in {skill_path}"
        )
    shared_role_prompt_value = metadata.get("shared_role_prompt")
    if shared_role_prompt_value is not None and not isinstance(
        shared_role_prompt_value,
        str,
    ):
        raise ValueError(
            f"Frontmatter field 'shared_role_prompt' must be a string in {skill_path}"
        )
    shared_role_footer_value = metadata.get("shared_role_footer")
    if shared_role_footer_value is not None and not isinstance(
        shared_role_footer_value,
        str,
    ):
        raise ValueError(
            f"Frontmatter field 'shared_role_footer' must be a string in {skill_path}"
        )
    stage_role_prompts = {
        (stage, role): _load_role_prompts(
            skill_directory,
            roles=(role,),
            shared_role_prompt=shared_role_prompt_value,
            shared_role_footer=shared_role_footer_value,
            role_references={
                role: role_references.get(role, ()),
            },
            role_source_sections={role: source_sections},
            include_shared=False,
        )[role]
        for (stage, role), source_sections in stage_role_source_sections.items()
    }
    role_prompts = _load_role_prompts(
        skill_directory,
        roles=roles,
        shared_role_prompt=shared_role_prompt_value,
        shared_role_footer=shared_role_footer_value,
        role_references=role_references,
        role_source_sections=role_source_sections,
        include_shared=False,
    )
    # Published MiniMax Design/H3 methods used one broad ASSET_REVIEWER slot.
    # Keep their vendored metadata untouched and expose that exact upstream
    # guidance to the three narrower local runtime responsibilities.
    legacy_asset_prompt = role_prompts.get(ProductionSkillRole.ASSET_REVIEWER)
    if legacy_asset_prompt is not None:
        asset_role_aliases = (
            ProductionSkillRole.ASSET_PLANNER,
            ProductionSkillRole.ASSET_BUILDER,
            ProductionSkillRole.ASSET_VERIFIER,
        )
        for role in asset_role_aliases:
            role_prompts.setdefault(role, legacy_asset_prompt)
        for (stage, role), prompt in tuple(stage_role_prompts.items()):
            if role != ProductionSkillRole.ASSET_REVIEWER:
                continue
            for alias in asset_role_aliases:
                stage_role_prompts.setdefault((stage, alias), prompt)
    return ComposableProductionSkill(
        id=_required_string(metadata, "id", path=skill_path),
        label=_required_string(metadata, "label", path=skill_path),
        description=_required_string(metadata, "description", path=skill_path),
        constitution=constitution,
        role_prompts=role_prompts,
        source_path=str(skill_path),
        shared_prefix=(
            (str((skill_directory / shared_role_prompt_value).resolve()), _read_markdown(skill_directory / shared_role_prompt_value)[1])
            if shared_role_prompt_value is not None else None
        ),
        shared_suffix=(
            (str((skill_directory / shared_role_footer_value).resolve()), _read_markdown(skill_directory / shared_role_footer_value)[1])
            if shared_role_footer_value is not None else None
        ),
        stage_role_prompts=stage_role_prompts,
        provenance=_optional_provenance(metadata, path=skill_path),
        upstream_sources=_optional_string_list(
            metadata,
            "upstream_sources",
            path=skill_path,
        ),
        user_overridable=_optional_bool(
            metadata,
            "user_overridable",
            default=True,
            path=skill_path,
        ),
        category=MethodCategory(metadata.get("category", MethodCategory.CREATIVE.value)),
        requirements=_optional_string_list(metadata, "requirements", path=skill_path),
    )


def load_narrative_skills(
    root: Path = DEFAULT_NARRATIVE_SKILLS_ROOT,
) -> tuple[ComposableProductionSkill, ...]:
    """Load explicitly supplied narrative methods, if any.

    The retired built-in narrative directory has been removed. This optional
    entry point still accepts a caller-provided directory; current production
    methods are loaded from the methods catalog instead.
    """

    skill_directories = sorted(
        path.parent for path in root.glob("*/SKILL.md") if path.is_file()
    )
    if not skill_directories:
        return ()
    skills = tuple(_load_composable_skill(path) for path in skill_directories)
    skill_ids = [skill.id for skill in skills]
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError(f"Duplicate narrative Production Skill id in {root}")
    return skills


def load_production_method_skills(
    root: Path = DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT,
) -> tuple[ComposableProductionSkill, ...]:
    """Load optional production methods without deciding when they apply."""

    skill_directories = sorted(
        path.parent for path in root.glob("*/SKILL.md") if path.is_file()
    )
    if not skill_directories:
        raise ValueError(f"No Production Method Skills found in {root}")
    skills = tuple(_load_composable_skill(path) for path in skill_directories)
    skill_ids = [skill.id for skill in skills]
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError(f"Duplicate Production Method Skill id in {root}")
    return skills


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
    prompts = _load_role_prompts(skill_directory, allow_missing_roles=True)
    return ProductionSkill(
        profile=profile,
        label=_required_string(metadata, "label", path=skill_path),
        medium=_required_string(metadata, "medium", path=skill_path),
        constitution=constitution,
        concept=prompts[ProductionSkillRole.CONCEPT],
        writer=prompts[ProductionSkillRole.WRITER],
        planner=prompts[ProductionSkillRole.PLANNER],
        critic=prompts[ProductionSkillRole.CRITIC],
        story_repair=prompts[ProductionSkillRole.STORY_REPAIR],
        revision_verifier=prompts[ProductionSkillRole.REVISION_VERIFIER],
        final_cold_read=prompts[ProductionSkillRole.FINAL_COLD_READ],
        review_router=prompts[ProductionSkillRole.REVIEW_ROUTER],
        director=prompts[ProductionSkillRole.DIRECTOR],
        asset_planner=prompts[ProductionSkillRole.ASSET_PLANNER],
        asset_builder=prompts[ProductionSkillRole.ASSET_BUILDER],
        asset_verifier=prompts[ProductionSkillRole.ASSET_VERIFIER],
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
        provenance=_optional_provenance(metadata, path=skill_path),
        upstream_sources=_optional_string_list(
            metadata,
            "upstream_sources",
            path=skill_path,
        ),
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
