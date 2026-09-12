"""Check the selected video units against current content, not a global cursor."""

from __future__ import annotations

from short_drama.models import DurationAuthority, ProjectDocument
from short_drama.production_contracts import (
    GenerationPlanContent,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    ProductionSpecContent,
    StoryboardContent,
    compile_generation_plan,
)


def derive_current_generation_plan(
    document: ProjectDocument,
) -> tuple[GenerationPlanContent, StoryboardContent, ProductionSpecContent]:
    """Project the saved current source documents in memory; never save a plan."""
    documents = {item.id: item for item in document.production_documents}
    board_document = documents.get(document.storyboard_document_id or "")
    spec_document = documents.get(document.production_spec_document_id or "")
    if board_document is None or spec_document is None:
        raise ValueError("Current Storyboard and ProductionSpec are required")
    board_version = next((v for v in board_document.versions if v.id == board_document.current_version_id), None)
    spec_version = next((v for v in spec_document.versions if v.id == spec_document.current_version_id), None)
    if board_version is None or spec_version is None:
        raise ValueError("Current Storyboard or ProductionSpec version is missing")
    board = StoryboardContent.model_validate(board_version.payload)
    spec = ProductionSpecContent.model_validate(spec_version.payload)
    shots = {shot.id: shot for shot in document.shots}
    if any(shot.id not in shots for shot in board.shots):
        raise ValueError("Current Storyboard has missing shot content")
    current = compile_generation_plan(
        storyboard=board, storyboard_document_id=board_document.id,
        storyboard_version_id=board_version.id, production_spec=spec,
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
    if plan.storyboard_document_id != document.storyboard_document_id:
        raise ValueError("GenerationPlan references a different Storyboard")
    if plan.production_spec_document_id != document.production_spec_document_id:
        raise ValueError("GenerationPlan references a different ProductionSpec")

    documents = {item.id: item for item in document.production_documents}
    storyboard_document = documents.get(plan.storyboard_document_id)
    spec_document = documents.get(plan.production_spec_document_id)
    if storyboard_document is None or spec_document is None:
        raise ValueError("GenerationPlan source documents are missing")
    storyboard_version = next((
        item for item in storyboard_document.versions
        if item.id == storyboard_document.current_version_id
    ), None)
    spec_version = next((
        item for item in spec_document.versions
        if item.id == plan.production_spec_version_id
    ), None)
    if spec_version is None or spec_version.id != spec_document.current_version_id:
        raise ValueError("GenerationPlan references an outdated ProductionSpec version")
    if storyboard_version is None:
        raise ValueError("GenerationPlan current Storyboard is missing")

    spec = ProductionSpecContent.model_validate(spec_version.payload)
    if spec.duration_contract.authority == DurationAuthority.AUDIO_LED:
        spec_slot = next((item for item in document.output_slots
                          if item.id == PRODUCTION_SPEC_OUTPUT_SLOT_ID), None)
        if spec_slot is None or spec_slot.current_artifact_ref is None:
            raise ValueError("Audio-led ProductionSpec inputs have changed")
    storyboard = StoryboardContent.model_validate(storyboard_version.payload)
    current_shots = {shot.id: shot for shot in document.shots}
    if any(shot.id not in current_shots for shot in storyboard.shots):
        raise ValueError("Current Storyboard has missing shot content")
    current = compile_generation_plan(
        storyboard=storyboard,
        storyboard_document_id=storyboard_document.id,
        storyboard_version_id=storyboard_version.id,
        production_spec=spec,
        production_spec_document_id=spec_document.id,
        production_spec_version_id=spec_version.id,
        shot_revisions={shot.id: current_shots[shot.id].revision for shot in storyboard.shots},
    )
    planned_units = {unit.id: unit for unit in plan.units}
    current_units = {unit.id: unit for unit in current.units}
    selected_ids = unit_ids or list(planned_units)
    for unit_id in selected_ids:
        unit = planned_units.get(unit_id)
        if unit is None:
            raise ValueError(f"Unknown generation unit: {unit_id}")
        if any(
            window.shot_id not in current_shots
            or current_shots[window.shot_id].revision != window.shot_revision
            for window in unit.shot_windows
        ):
            raise ValueError(f"Generation unit references outdated shot revisions: {unit_id}")
        if unit != current_units.get(unit_id):
            raise ValueError(
                f"Generation unit {unit_id} has changed; recompile its current source"
            )
