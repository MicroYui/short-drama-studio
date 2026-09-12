"""The content page projects committed draft groups as they are written."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from short_drama.models import ProjectDocument, ProductionDocument, ProductionDocumentVersion, ProductionDocumentOrigin, ShotPlan
from short_drama.production_contracts import StoryboardDraftContent, StoryboardDraftGroupReplaceRequest, StoryboardWorkUnitContract
from short_drama.studio_models import StudioShotSave

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def current_draft(document: ProjectDocument) -> tuple[ProductionDocument, ProductionDocumentVersion, StoryboardDraftContent] | None:
    if document.storyboard_document_id:
        return None
    source = next((item for item in reversed(document.production_documents) if item.kind == "storyboard.draft"), None)
    if source is None:
        return None
    version = next(item for item in source.versions if item.id == source.current_version_id)
    return source, version, StoryboardDraftContent.model_validate(version.payload)


def _draft_shots(draft: StoryboardDraftContent) -> list[ShotPlan]:
    order = {node.id: node.sequence for node in draft.scene_nodes}
    groups = sorted(draft.committed_groups, key=lambda group: (order[group.scene_node_id], group.scene_group_sequence))
    shots = [shot.model_copy(deep=True) for group in groups for shot in group.shots]
    for index, shot in enumerate(shots, 1):
        shot.sequence = index
    return shots


def project_draft_shots(document: ProjectDocument) -> None:
    value = current_draft(document)
    if value is not None:
        document.shots = _draft_shots(value[2])


@dataclass(frozen=True)
class ResolvedCurrentShot:
    shot: ShotPlan
    draft_source: tuple[ProductionDocument, ProductionDocumentVersion] | None = None


def resolve_current_shot(document: ProjectDocument, shot_id: str) -> ResolvedCurrentShot | None:
    """Use the same committed draft projection as current content reads.

    A formal storyboard takes precedence. Never search an old draft after its
    shot was removed or finalized, and preserve the actual saved source version.
    """
    draft = current_draft(document)
    shots = document.shots if draft is None else _draft_shots(draft[2])
    if draft is not None:
        from short_drama.studio_references import project_legacy_references
        # Match current reads' deterministic cue IDs/legacy view without
        # modifying either the saved draft or its containing document.
        project_legacy_references(document.model_copy(update={"shots": shots}))
    shot = next((item for item in shots if item.id == shot_id), None)
    if shot is None:
        return None
    return ResolvedCurrentShot(shot, None if draft is None else (draft[0], draft[1]))


def save_draft_shot(service: ShortDramaService, document: ProjectDocument, shot_id: str, request: StudioShotSave) -> bool:
    from short_drama.studio_service import StudioContentConflict
    value = current_draft(document)
    if value is None:
        return False
    source, version, draft = value
    group = next((item for item in draft.committed_groups if any(shot.id == shot_id for shot in item.shots)), None)
    if group is None:
        return False
    current = next(shot for shot in group.shots if shot.id == shot_id)
    if current.revision != request.expected_revision:
        raise StudioContentConflict("这组分镜刚有新内容，请合并当前内容后保存。")
    if request.shot.id != shot_id or request.shot.project_id != document.project.id:
        raise ValueError("镜头必须属于当前集")
    replacement = request.shot.model_copy(deep=True)
    replacement.revision = current.revision + 1
    from short_drama.studio_references import sync_shot_references
    sync_shot_references(document, replacement)
    revised = group.model_copy(deep=True)
    revised.shots = [replacement if shot.id == shot_id else shot for shot in revised.shots]
    revised.clip_group.duration_seconds = sum(shot.duration_seconds for shot in revised.shots)
    service.replace_storyboard_draft_group(document.project.id, StoryboardDraftGroupReplaceRequest(
        draft_document_id=source.id, base_version_id=version.id, group=revised,
        replaced_clip_group_id=group.clip_group.id, origin=ProductionDocumentOrigin.USER,
        work_unit=StoryboardWorkUnitContract(id=group.work_unit_id, title="用户局部编辑",
            source_range=group.source_range, objective="保存当前镜头的用户编辑，保留其他镜头。",
            completion_criteria=["用户编辑写入同一分镜组"])))
    return True
