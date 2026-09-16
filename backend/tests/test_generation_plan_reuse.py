"""Concurrent group preparation shares an identical current execution plan."""

from pathlib import Path

import pytest

from short_drama.models import ProductionDocumentOrigin
from short_drama.production_contracts import GenerationPlanCompileRequest
from short_drama.service import WorkflowConflictError
from tests.test_h3_generation_unit_language import _fixture


def test_concurrent_compilation_reuses_current_plan_without_another_revision(
    tmp_path: Path,
) -> None:
    service, fake, document, original, _unit, _prompt = _fixture(tmp_path)
    project_id = document.project.id
    request = GenerationPlanCompileRequest(
        storyboard_document_id=original.generation_plan.storyboard_document_id,
        storyboard_version_id=original.generation_plan.storyboard_version_id,
        production_spec_document_id=original.generation_plan.production_spec_document_id,
        production_spec_version_id=original.generation_plan.production_spec_version_id,
        base_version_id=original.version.id,
        origin=ProductionDocumentOrigin.AGENT,
    )
    unchanged = service.compile_generation_plan(project_id, request)
    assert unchanged.version.id == original.version.id

    prepared = service.get_project(project_id).document
    prepared.shots[0].revision += 1
    service.repository.save_project(prepared)
    first_worker = service.compile_generation_plan(project_id, request)
    assert first_worker.version.id != original.version.id
    version_count = len(first_worker.document.versions)

    second_worker = service.compile_generation_plan(project_id, request)
    assert second_worker.version == first_worker.version
    assert second_worker.generation_plan == first_worker.generation_plan
    assert len(second_worker.document.versions) == version_count
    assert (
        second_worker.output_slot.current_artifact_ref
        == first_worker.output_slot.current_artifact_ref
    )
    assert fake.calls == []

    changed = service.get_project(project_id).document
    changed.shots[1].revision += 1
    service.repository.save_project(changed)
    with pytest.raises(WorkflowConflictError, match="stale version"):
        service.compile_generation_plan(project_id, request)
    assert service.get_generation_plan(project_id).version == first_worker.version
    assert fake.calls == []
