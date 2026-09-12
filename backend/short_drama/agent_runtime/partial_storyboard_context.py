"""Saved partial storyboard directories for an already-scheduled Leader decision."""
from __future__ import annotations

from typing import cast

from pydantic import JsonValue, ValidationError

from short_drama.agent_runtime.models import (
    ArtifactRef, DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus,
    ToolAttemptRef,
)
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.models import ProjectDocument
from short_drama.production_contracts import (
    STORYBOARD_DRAFT_DOCUMENT_KIND, StoryboardDraftContent, StoryboardDraftProgress,
)


_DRAFT_WRITES = frozenset({
    "shot.begin_storyboard_draft", "shot.append_storyboard_ledger_segment",
    "shot.complete_storyboard_ledger", "shot.append_storyboard_outline_segment",
    "shot.complete_storyboard_outline", "shot.append_storyboard_group",
    "shot.append_storyboard_groups", "shot.replace_storyboard_draft_group",
    "shot.record_storyboard_draft_verification",
})


def _progress(turn: SpecialistTurnRecord, project_id: str) -> StoryboardDraftProgress | None:
    if (turn.tool_name not in _DRAFT_WRITES or turn.status != SpecialistTurnStatus.COMPLETED
        or turn.arguments.get("project_id") != project_id or turn.result is None
        or turn.result.get("status") != "SUCCEEDED"):
        return None
    output = turn.result.get("output")
    if not isinstance(output, dict):
        return None
    try:
        progress = StoryboardDraftProgress.model_validate(output.get("progress"))
    except ValidationError:
        return None  # Old receipts remain in Trace; do not infer a saved draft.
    return progress if progress.draft_ref.project_id == project_id else None


def partial_storyboard_context(
    store: AgentRuntimeStore, *, run_id: str, document: ProjectDocument,
) -> tuple[list[JsonValue], list[ArtifactRef]]:
    """Resolve only this run's observed, still-current drafts; never scan all prose.

    The current exact payload supplies membership after replacements/removals.
    An unseen other-writer version or changed source retires the old index rather
    than relabelling its saved checkpoint as current. No scheduling is performed.
    """
    run = store.get_run(run_id)
    if run is None or run.project_id != document.project.id:
        return [], []
    plan = store.get_current_plan(run)
    if plan is None:
        return [], []
    project_id = document.project.id
    documents = {item.id: item for item in document.production_documents}
    current_board = documents.get(document.storyboard_document_id or "")
    board_version_id = None if current_board is None else current_board.current_version_id
    spec = documents.get(document.production_spec_document_id or "")
    entries: dict[str, tuple[JsonValue, ArtifactRef]] = {}
    for work in store.list_work_orders(run_id):
        if (work.active_plan_revision_id != plan.id or work.superseded_by_plan_revision_id is not None
            or work.status not in {DelegationStatus.PENDING, DelegationStatus.IN_PROGRESS,
                                   DelegationStatus.NEEDS_ATTENTION}):
            continue
        selected: tuple[SpecialistTurnRecord, StoryboardDraftProgress] | None = None
        for turn in sorted(work.specialist_turns, key=lambda item: item.sequence):
            progress = _progress(turn, project_id)
            if progress is not None:
                selected = turn, progress  # A new draft supersedes this WorkOrder's old branch.
        if selected is None:
            continue
        turn, progress = selected
        source = documents.get(progress.draft_ref.artifact_id)
        if (source is None or source.kind != STORYBOARD_DRAFT_DOCUMENT_KIND
            or source.current_version_id != progress.draft_version_id):
            continue
        version = next((item for item in source.versions if item.id == progress.draft_version_id
                        and item.version == progress.draft_ref.version), None)
        if version is None:
            continue
        try:
            draft = StoryboardDraftContent.model_validate(version.payload)
        except ValidationError:
            continue
        if (draft.project_id != project_id
            or draft.script_version_id != document.project.current_script_version_id
            or draft.bible_version_id != document.project.current_bible_version_id
            or draft.base_storyboard_version_id != board_version_id
            or spec is None or draft.production_spec_document_id != spec.id
            or draft.production_spec_version_id != spec.current_version_id
            or not draft.committed_groups):
            continue
        nodes = {node.id: node for node in draft.scene_nodes}
        groups = sorted(draft.committed_groups,
                        key=lambda item: (nodes[item.scene_node_id].sequence, item.scene_group_sequence))
        shots = [shot for group in groups for shot in group.shots]
        ref = ArtifactRef.model_validate(progress.draft_ref.model_dump(mode="json"))
        attempt = ToolAttemptRef(
            project_id=project_id, agent_run_id=run_id, work_order_id=work.id,
            attempt_id=turn.id, tool_name=turn.tool_name, turn_status=turn.status,
            outcome_status="SUCCEEDED", has_arguments=True, has_result=True,
        )
        entries[source.id] = ({
            "work_order_id": work.id, "status": "SAVED_PARTIAL_DRAFT", "finalized": False,
            "phase": draft.phase.value, "draft_ref": ref.model_dump(mode="json"),
            "draft_version_id": version.id, "source_sequence": turn.sequence,
            "saved_attempt": attempt.model_dump(mode="json"),
            "script_version_id": draft.script_version_id,
            "bible_version_id": draft.bible_version_id,
            "production_spec_version_id": draft.production_spec_version_id,
            "shot_directory": cast(list[JsonValue], [
                {"shot_id": shot.id, "sequence": shot.sequence, "scene_id": shot.scene_id,
                 "duration_seconds": shot.duration_seconds} for shot in shots]),
            "clip_group_directory": cast(list[JsonValue], [
                {"clip_group_id": group.clip_group.id, "scene_node_id": group.scene_node_id,
                 "scene_id": nodes[group.scene_node_id].scene_id,
                 "shot_ids": list(group.clip_group.shot_ids),
                 "duration_seconds": group.clip_group.duration_seconds} for group in groups]),
            "committed_duration_seconds": sum(shot.duration_seconds for shot in shots),
            "remaining_script_line_count": progress.remaining_script_line_count,
            "usage_boundary": "Saved partial design, not a finalized storyboard or media-readiness claim. Read the exact draft/attempt for prose before using its scoped design.",
        }, ref)
    return [value for value, _ in entries.values()], [ref for _, ref in entries.values()]
