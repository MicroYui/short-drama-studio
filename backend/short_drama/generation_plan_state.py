"""Check the selected video units against current content, not a global cursor."""

from __future__ import annotations

from short_drama.models import DurationAuthority, ProjectDocument
from short_drama.production_contracts import (
    GenerationPlanContent,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    ProductionSpecContent, StoryboardDraftContent,
    compile_generation_plan,
)

from short_drama.generation_storyboard import (GenerationStoryboardSource, generation_storyboard_source,
    generation_document, draft_group_source_matches)


def derive_current_generation_plan(
    document: ProjectDocument,
) -> tuple[GenerationPlanContent, GenerationStoryboardSource, ProductionSpecContent]:
    """Project the saved current source documents in memory; never save a plan."""
    board = generation_storyboard_source(document)
    document = generation_document(document)
    spec_document = next((item for item in document.production_documents
                          if item.id == document.production_spec_document_id), None)
    if spec_document is None:
        raise ValueError("Current ProductionSpec is required")
    spec_version = next((v for v in spec_document.versions if v.id == spec_document.current_version_id), None)
    if spec_version is None:
        raise ValueError("Current ProductionSpec version is missing")
    spec = ProductionSpecContent.model_validate(spec_version.payload)
    shots = {shot.id: shot for shot in document.shots}
    if any(shot.id not in shots for shot in board.shots):
        raise ValueError("Current Storyboard has missing shot content")
    current = compile_generation_plan(
        storyboard=board.content, storyboard_document_id=board.document.id,
        storyboard_version_id=board.version.id, production_spec=spec,
        production_spec_document_id=spec_document.id, production_spec_version_id=spec_version.id,
        shot_revisions={shot.id: shots[shot.id].revision for shot in board.shots},
    )
    return current, board, spec


def validate_generation_units_current(
    document: ProjectDocument,
    plan: GenerationPlanContent,
    unit_ids: list[str] | None = None,
) -> None:
    """Unrelated storyboard edits must not invalidate an already prepared clip."""

    if plan.project_id != document.project.id:
        raise ValueError("GenerationPlan belongs to another project")
    if (
        plan.script_version_id != document.project.current_script_version_id
        or plan.bible_version_id != document.project.current_bible_version_id
    ):
        raise ValueError("GenerationPlan references outdated story inputs")
    if plan.production_spec_document_id != document.production_spec_document_id:
        raise ValueError("GenerationPlan references a different ProductionSpec")

    documents = {item.id: item for item in document.production_documents}
    storyboard_document = documents.get(plan.storyboard_document_id)
    spec_document = documents.get(plan.production_spec_document_id)
    if storyboard_document is None or spec_document is None:
        raise ValueError("GenerationPlan source documents are missing")
    spec_version = next((
        item for item in spec_document.versions
        if item.id == plan.production_spec_version_id
    ), None)
    if spec_version is None or spec_version.id != spec_document.current_version_id:
        raise ValueError("GenerationPlan references an outdated ProductionSpec version")
    planned_source = generation_storyboard_source(document, document_id=plan.storyboard_document_id, version_id=plan.storyboard_version_id)

    spec = ProductionSpecContent.model_validate(spec_version.payload)
    if spec.duration_contract.authority == DurationAuthority.AUDIO_LED:
        spec_slot = next((item for item in document.output_slots
                          if item.id == PRODUCTION_SPEC_OUTPUT_SLOT_ID), None)
        if spec_slot is None or spec_slot.current_artifact_ref is None:
            raise ValueError("Audio-led ProductionSpec inputs have changed")
    current, source, _spec = derive_current_generation_plan(document)
    current_shots = {shot.id: shot for shot in generation_document(document).shots}
    planned_units = {unit.id: unit for unit in plan.units}
    current_units = {unit.id: unit for unit in current.units}
    selected_ids = unit_ids or list(planned_units)
    for unit_id in selected_ids:
        unit = planned_units.get(unit_id)
        if unit is None:
            raise ValueError(f"Unknown generation unit: {unit_id}")
        draft_unchanged = draft_group_source_matches(document, plan.storyboard_document_id,
            plan.storyboard_version_id, unit.clip_group_id)
        if isinstance(planned_source.content, StoryboardDraftContent) and not draft_unchanged:
            raise ValueError(f"Saved draft generation group {unit_id} has changed; recompile its current source")
        if plan.storyboard_document_id != source.document.id and not draft_unchanged:
            raise ValueError("GenerationPlan references a different Storyboard")
        if any(window.shot_id not in current_shots or
               (current_shots[window.shot_id].revision != window.shot_revision and not draft_unchanged)
               for window in unit.shot_windows):
            raise ValueError(f"Generation unit references outdated shot revisions: {unit_id}")
        current_unit = current_units.get(unit_id)
        if draft_unchanged and current_unit is not None:
            current_unit = current_unit.model_copy(update={"shot_windows": [
                window.model_copy(update={"shot_revision": old.shot_revision})
                for window, old in zip(current_unit.shot_windows, unit.shot_windows, strict=True)]})
        if unit != current_unit:
            raise ValueError(f"Generation unit {unit_id} has changed; recompile its current source")
