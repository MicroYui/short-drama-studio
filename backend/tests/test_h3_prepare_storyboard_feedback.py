"""Missing formal source feedback and recovery against real temporary SQLite."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import override

import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import ProductionDocumentOrigin, ProjectAggregate
from short_drama.production_contracts import (
    StoryboardDraftAppendRequest, StoryboardDraftBeginRequest,
    StoryboardDraftFinalizeRequest, StoryboardReadResponse,
)
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.service import ArtifactNotFoundError, ShortDramaService
from short_drama.tools.contracts import ToolCallStatus
from tests.test_h3_generation_unit_language import FakePromptCompiler, _fixture
from tests.test_storyboard_incremental_service import (
    _adjacent_prefix_batch,  # pyright: ignore[reportUnknownVariableType] -- existing fixture
    _append_all_groups,  # pyright: ignore[reportUnknownVariableType] -- existing fixture
    _draft,  # pyright: ignore[reportUnknownVariableType] -- existing fixture
    _prepare_script_only_project, _seed_from_draft,
)


@dataclass
class DraftFixture:
    service: ShortDramaService
    fake: FakePromptCompiler
    project_id: str
    group_id: str
    shot_id: str
    draft_id: str
    draft_version_id: str
    finalize: Callable[[], StoryboardReadResponse]


def _draft_fixture(tmp_path: Path) -> DraftFixture:
    fake = FakePromptCompiler()
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: fake)
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    document, script, spec, shots = _prepare_script_only_project(service)
    draft = _draft(document, script, spec, dialogue_shots=shots)
    begun = service.begin_storyboard_draft(document.project.id, StoryboardDraftBeginRequest(
        seed=_seed_from_draft(draft), origin=ProductionDocumentOrigin.AGENT)).progress
    work, groups = _adjacent_prefix_batch(script=script, dialogue_shots=shots, draft=draft)
    first = service.append_storyboard_group(document.project.id, StoryboardDraftAppendRequest(
        draft_document_id=begun.draft_ref.artifact_id, base_version_id=begun.draft_version_id,
        work_unit=work, group=groups[0], origin=ProductionDocumentOrigin.AGENT)).progress

    def finalize() -> StoryboardReadResponse:
        progress = _append_all_groups(service, document=document,
            script=script.model_copy(update={"lines": script.lines[1:]}), dialogue_shots=shots[1:],
            draft_document_id=begun.draft_ref.artifact_id, base_version_id=first.draft_version_id,
            starting_sequence=2, starting_continuity_in=[f"after-{script.lines[0].id}"])
        return service.finalize_storyboard(document.project.id, StoryboardDraftFinalizeRequest(
            draft_document_id=begun.draft_ref.artifact_id, draft_version_id=progress.draft_version_id,
            origin=ProductionDocumentOrigin.AGENT))

    return DraftFixture(service, fake, document.project.id, groups[0].clip_group.id,
        groups[0].shots[0].id, begun.draft_ref.artifact_id, first.draft_version_id, finalize)


@pytest.mark.parametrize("target", ["clip_group_id", "shot_id"])
def test_saved_draft_group_prepares_then_same_request_reuses_after_finalization(tmp_path: Path, target: str) -> None:
    from short_drama.generation_plan_state import derive_current_generation_plan
    from short_drama.generation_storyboard import generation_document
    fixture = _draft_fixture(tmp_path)
    service = fixture.service
    request = H3GroupOperationRequest.model_validate({target: fixture.group_id if target == "clip_group_id" else fixture.shot_id,
        "route": "DIRECT", "media_inputs": []})
    raw = service.repository.get_project(fixture.project_id)
    assert raw is not None
    document = generation_document(raw)
    unit = derive_current_generation_plan(raw)[0].units[0]
    fixture.fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots)
    call = service.tool_registry.invoke("route.prepare_h3_group", {
        "project_id": fixture.project_id, "request": request.model_dump(mode="json", exclude_none=True)})
    assert call.status == ToolCallStatus.SUCCEEDED, call.error
    assert call.output is not None and call.output["status"] == "PREPARED"
    first = service.get_generation_plan(fixture.project_id)
    assert first.generation_plan.storyboard_document_id == fixture.draft_id
    assert first.generation_plan.storyboard_version_id == fixture.draft_version_id
    assert service.repository.get_project(fixture.project_id).storyboard_document_id is None  # pyright: ignore[reportOptionalMemberAccess]
    fixture.finalize()
    repeated = service.prepare_h3_group(fixture.project_id, request)
    assert repeated.operation_id == call.output["operation_id"] and len(fixture.fake.calls) == 1
    assert service.repository.list_tasks(fixture.project_id) == []


def test_no_draft_and_unsaved_target_do_not_claim_saved_group(tmp_path: Path) -> None:
    fixture = _draft_fixture(tmp_path)
    unknown = fixture.service.tool_registry.invoke("route.prepare_h3_group", {
        "project_id": fixture.project_id, "request": {"clip_group_id": "not-a-saved-group"}})
    assert unknown.error is not None and "No current storyboard group matches" in unknown.error.message
    assert "Target is saved" not in unknown.error.message
    other = ShortDramaService(database_path=tmp_path / "empty.sqlite3", workspace_root=tmp_path / "empty-workspace")
    document = other.initialize_fixture().document
    missing = other.tool_registry.invoke("route.prepare_h3_group", {
        "project_id": document.project.id, "request": {"shot_id": document.shots[0].id}})
    assert missing.error is not None and "No current Storyboard or saved storyboard draft groups exist" in missing.error.message
    assert "Target is saved" not in missing.error.message


@pytest.mark.parametrize("damage", ["document", "version", "payload"])
def test_existing_formal_storyboard_missing_object_keeps_original_error(tmp_path: Path, damage: str) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    if damage == "document":
        document.storyboard_document_id = "missing-formal-document"
    else:
        board = next(item for item in document.production_documents if item.id == document.storyboard_document_id)
        if damage == "version":
            board.current_version_id = "missing-formal-version"
        else:
            version = next(version for version in board.versions if version.id == board.current_version_id)
            version.payload = {"invalid": "persisted storyboard payload"}
    # Normal writes reject malformed records; represent an already damaged
    # temporary database directly, without weakening the production validator.
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("UPDATE projects SET document_json = ? WHERE id = ?",
            (document.model_dump_json(), document.project.id))
    with pytest.raises(ValueError) as original:
        service.get_storyboard(document.project.id)
    with pytest.raises(type(original.value)) as failure:
        service.prepare_h3_group(document.project.id, H3GroupOperationRequest(shot_id=unit.shot_ids[0], method_skill_ids=[]))
    assert str(failure.value) == str(original.value)
    assert fake.calls == [] and service.repository.list_h3_group_operations(document.project.id) == []


class CountingStoryboardService(ShortDramaService):
    storyboard_reads: int = 0
    project_reads: int = 0
    on_missing: Callable[[], StoryboardReadResponse] | None = None

    @override
    def get_storyboard(self, project_id: str, *, version_id: str | None = None) -> StoryboardReadResponse:
        self.storyboard_reads += 1
        try:
            return super().get_storyboard(project_id, version_id=version_id)
        except ArtifactNotFoundError:
            callback = self.on_missing
            self.on_missing = None
            if callback is not None:
                callback()
            raise

    @override
    def get_project(self, project_id: str) -> ProjectAggregate:
        self.project_reads += 1
        return super().get_project(project_id)


def test_finalize_between_reads_is_retried_once_and_normal_read_has_no_extra_project_load(tmp_path: Path) -> None:
    from short_drama.h3_group_operation import _storyboard_for_preparation

    fixture = _draft_fixture(tmp_path)
    observer = CountingStoryboardService(database_path=fixture.service.repository.database_path,
        workspace_root=fixture.service.workspace_root)
    observer.on_missing = fixture.finalize
    request = H3GroupOperationRequest(clip_group_id=fixture.group_id)
    board = _storyboard_for_preparation(observer, fixture.project_id, request)
    assert fixture.group_id in [group.id for group in board.clip_groups]
    assert (observer.storyboard_reads, observer.project_reads) == (2, 1)
    again = _storyboard_for_preparation(observer, fixture.project_id, request)
    assert again.version.id == board.version.id
    assert (observer.storyboard_reads, observer.project_reads) == (3, 1)
