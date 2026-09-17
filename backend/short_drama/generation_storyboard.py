"""Read saved generation groups without making a draft a formal Storyboard."""
from __future__ import annotations

from dataclasses import dataclass

from short_drama.models import ProjectDocument, ProductionDocument, ProductionDocumentVersion, ShotPlan
from short_drama.production_contracts import (
    ClipGroup, StoryboardContent, StoryboardDraftContent,
    STORYBOARD_DOCUMENT_KIND, STORYBOARD_DRAFT_DOCUMENT_KIND,
)
from short_drama.studio_storyboard import current_draft, _draft_shots  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True)
class GenerationStoryboardSource:
    document: ProductionDocument
    version: ProductionDocumentVersion
    content: StoryboardContent | StoryboardDraftContent

    @property
    def shots(self) -> list[ShotPlan]:
        return _draft_shots(self.content) if isinstance(self.content, StoryboardDraftContent) else self.content.shots

    @property
    def clip_groups(self) -> list[ClipGroup]:
        if isinstance(self.content, StoryboardContent):
            return self.content.clip_groups
        order = {node.id: node.sequence for node in self.content.scene_nodes}
        return [item.clip_group for item in sorted(self.content.committed_groups,
            key=lambda group: (order[group.scene_node_id], group.scene_group_sequence))]


def generation_storyboard_source(document: ProjectDocument, *, document_id: str | None = None,
                                 version_id: str | None = None) -> GenerationStoryboardSource:
    """Formal content wins; explicit historical refs remain exact and readable."""
    content: StoryboardContent | StoryboardDraftContent
    if document_id is None:
        document_id = document.storyboard_document_id
        if document_id is None:
            draft = current_draft(document)
            if draft is None:
                raise ValueError("No current Storyboard or saved storyboard draft groups exist")
            source, version, content = draft
            if not content.committed_groups:
                raise ValueError("The current storyboard draft has no complete saved group; save the selected group first")
            return GenerationStoryboardSource(source, version, content)
    source = next((item for item in document.production_documents if item.id == document_id), None)
    if source is None:
        raise ValueError(f"Storyboard source document is missing: {document_id}")
    version = next((item for item in source.versions if item.id == (version_id or source.current_version_id)), None)
    if version is None:
        raise ValueError(f"Storyboard source version is missing: {version_id}")
    if source.kind == STORYBOARD_DOCUMENT_KIND:
        content = StoryboardContent.model_validate(version.payload)
    elif source.kind == STORYBOARD_DRAFT_DOCUMENT_KIND:
        content = StoryboardDraftContent.model_validate(version.payload)
    else:
        raise ValueError(f"Document {source.id} is not a storyboard source")
    return GenerationStoryboardSource(source, version, content)


def generation_document(document: ProjectDocument) -> ProjectDocument:
    """An ephemeral draft-shot view; callers must persist the original document."""
    draft = current_draft(document)
    if draft is None:
        return document
    projected = document.model_copy(update={"shots": _draft_shots(draft[2])}, deep=True)
    from short_drama.studio_references import project_legacy_references
    project_legacy_references(projected)
    # Reference plans here are read projections of the saved cues, never a
    # separately persisted draft truth or an automatic reference choice.
    from short_drama.models import ReferencePlanEntry
    existing = {plan.shot_id for plan in projected.reference_plan}
    for shot in projected.shots:
        if shot.id not in existing:
            projected.reference_plan.append(ReferencePlanEntry(shot_id=shot.id,
                bindings=[], capability_valid=True, validation_notes=[]))
    return projected


def draft_group_source_matches(document: ProjectDocument, source_document_id: str | None,
                               source_version_id: str | None, group_id: str | None) -> bool:
    """Prove a saved draft group still has the same authored content.

    Finalization may reset revision counters and unrelated insertions change
    display sequence. Neither rewrites the source carried by an existing task.
    """
    if source_document_id is None or source_version_id is None or group_id is None:
        return False
    try:
        old = generation_storyboard_source(document, document_id=source_document_id, version_id=source_version_id)
        current = generation_storyboard_source(document)
    except ValueError:
        return False
    if not isinstance(old.content, StoryboardDraftContent):
        return False
    for field in ("project_id", "script_version_id", "bible_version_id", "production_spec_document_id", "production_spec_version_id"):
        if getattr(old.content, field) != getattr(current.content, field):
            return False
    old_group = next((group for group in old.clip_groups if group.id == group_id), None)
    group = next((group for group in current.clip_groups if group.id == group_id), None)
    if old_group is None or old_group != group:
        return False
    if isinstance(current.content, StoryboardDraftContent):
        prior = next(item for item in old.content.committed_groups if item.clip_group.id == group_id)
        now = next(item for item in current.content.committed_groups if item.clip_group.id == group_id)
        if prior.source_range != now.source_range or prior.scene_node_id != now.scene_node_id:
            return False
    old_shots = {shot.id: shot.model_copy(deep=True) for shot in old.shots}
    from short_drama.studio_references import shot_cues
    for shot in old_shots.values():
        for index, cue in enumerate(shot_cues(shot)):
            cue.id = cue.id or f"{shot.id}-reference-{index}"
    current_shots = {shot.id: shot for shot in generation_document(document).shots}
    ignored = {"revision", "sequence", "approval_status", "freshness", "merge_candidate", "local_rework_risk"}
    return all(shot_id in old_shots and shot_id in current_shots
        and old_shots[shot_id].model_dump(exclude=ignored) == current_shots[shot_id].model_dump(exclude=ignored)
        for shot_id in old_group.shot_ids)


def generation_revision_matches(document: ProjectDocument, shot_id: str, expected_revision: int,
                                source_document_id: str | None, source_version_id: str | None,
                                group_id: str | None) -> bool:
    current = next((shot for shot in generation_document(document).shots if shot.id == shot_id), None)
    if current is None:
        return False
    if current.revision == expected_revision:
        return True
    if not draft_group_source_matches(document, source_document_id, source_version_id, group_id):
        return False
    source = generation_storyboard_source(document, document_id=source_document_id, version_id=source_version_id)
    return any(shot.id == shot_id and shot.revision == expected_revision for shot in source.shots)
