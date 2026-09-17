"""Local shot timing changes update inherited groups without overriding explicit input."""

from pathlib import Path

import pytest

from short_drama.models import ProductionDocumentOrigin
from short_drama.production_contracts import (
    GenerationPlanCompileRequest,
    StoryboardMutationResponse,
    StoryboardPatchRequest,
    StoryboardShotReplacement,
)
from short_drama.service import WorkflowConflictError
from short_drama.studio_models import StudioShotSave
from short_drama.tools.contracts import ToolCallStatus
from tests.test_storyboard_local_edits import _fixture


def _extend_request(board: StoryboardMutationResponse) -> StoryboardPatchRequest:
    shot = board.storyboard.shots[0]
    duration = shot.duration_seconds + 1
    replacement = shot.model_copy(update={
        "duration_seconds": duration,
        "timeline_beats": [
            beat.model_copy(update={"end_seconds": duration})
            if index == len(shot.timeline_beats) - 1 else beat.model_copy(deep=True)
            for index, beat in enumerate(shot.timeline_beats)
        ],
    }, deep=True)
    return StoryboardPatchRequest(base_version_id=board.version.id,
        replacements=[StoryboardShotReplacement(shot_id=shot.id,
            expected_revision=shot.revision, replacement=replacement)],
        origin=ProductionDocumentOrigin.AGENT)


def test_duration_replacement_through_registry_updates_group_and_compiled_source(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    before = service.get_project(pid)
    request = _extend_request(board)
    response = service.tool_registry.invoke("shot.patch_storyboard", {
        "project_id": pid, "request": request.model_dump(mode="json"),
    })
    assert response.status == ToolCallStatus.SUCCEEDED, response.error
    current = service.get_storyboard(pid)
    shot = current.storyboard.shots[0]
    assert shot.duration_seconds == request.replacements[0].replacement.duration_seconds
    assert shot.revision == board.storyboard.shots[0].revision + 1
    assert current.storyboard.clip_groups[0].duration_seconds == shot.duration_seconds
    assert current.storyboard.computed_duration_seconds == board.storyboard.computed_duration_seconds + 1
    assert current.storyboard.shots[1:] == board.storyboard.shots[1:]
    assert current.storyboard.clip_groups[1:] == board.storyboard.clip_groups[1:]
    after = service.get_project(pid)
    assert after.document.assets == before.document.assets
    assert after.document.editorial_timeline == before.document.editorial_timeline
    assert after.takes == before.takes and after.tasks == before.tasks
    plan = service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=current.document.id, storyboard_version_id=current.version.id,
        production_spec_document_id=current.storyboard.production_spec_document_id,
        production_spec_version_id=current.storyboard.production_spec_version_id,
        origin=ProductionDocumentOrigin.AGENT,
    ))
    assert plan.generation_plan.units[0].requested_duration_seconds == shot.duration_seconds
    assert plan.generation_plan.units[0].shot_windows[0].source_out_seconds == shot.duration_seconds


def test_duration_replacement_merges_unrelated_edit_but_rejects_changed_target(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    request = _extend_request(board)
    other = board.storyboard.shots[-1]
    service.studio.save_shot(pid, other.id, StudioShotSave(
        shot=other.model_copy(update={"camera": "人工选择：保留固定机位"}),
        expected_revision=other.revision,
    ))
    human = service.get_project(pid).document.shots[-1]
    saved = service.patch_storyboard(pid, request, current_content_edit=True)
    assert saved.storyboard.shots[-1] == human
    assert saved.storyboard.clip_groups[1:] == board.storyboard.clip_groups[1:]
    with pytest.raises(WorkflowConflictError, match="changed since the read version"):
        service.patch_storyboard(pid, request, current_content_edit=True)
    assert service.get_storyboard(pid).model_dump() == saved.model_dump()


@pytest.mark.parametrize("matching", [False, True])
def test_explicit_replacement_group_duration_is_validated_not_overwritten(tmp_path: Path, matching: bool) -> None:
    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    request = _extend_request(board)
    group = board.storyboard.clip_groups[0].model_copy(deep=True)
    if matching:
        group.duration_seconds = request.replacements[0].replacement.duration_seconds
    request.clip_groups = [group]
    if matching:
        saved = service.patch_storyboard(pid, request, current_content_edit=True)
        assert saved.storyboard.clip_groups[0] == group
    else:
        with pytest.raises(WorkflowConflictError, match="duration must equal its shot durations"):
            service.patch_storyboard(pid, request, current_content_edit=True)
        assert service.get_storyboard(pid).model_dump() == board.model_dump()


@pytest.mark.parametrize("fault", ["unknown_shot", "duplicate_coverage"])
def test_duration_replacement_does_not_accept_invalid_explicit_group_members(tmp_path: Path, fault: str) -> None:
    service, board = _fixture(tmp_path)
    request = _extend_request(board)
    group = board.storyboard.clip_groups[0].model_copy(deep=True)
    group.duration_seconds = request.replacements[0].replacement.duration_seconds
    group.shot_ids = ["unknown-shot"] if fault == "unknown_shot" else [board.storyboard.shots[-1].id]
    request.clip_groups = [group]
    with pytest.raises(WorkflowConflictError):
        service.patch_storyboard(board.storyboard.project_id, request, current_content_edit=True)
    assert service.get_storyboard(board.storyboard.project_id).model_dump() == board.model_dump()
