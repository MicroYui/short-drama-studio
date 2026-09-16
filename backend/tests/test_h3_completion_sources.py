"""H3 receipts and focused context expose live facts without changing history."""

import json
from pathlib import Path
from typing import cast

from PIL import Image
from pydantic import JsonValue
import pytest

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.agent_runtime.completion import verify_completion_claims
from short_drama.agent_runtime.models import ArtifactRef
from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.models import EnqueueRequest
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave
from tests.test_h3_current_source_contract import _composition_fixture, _reword
from tests.test_h3_generation_unit_language import _fixture


def _state(service: ShortDramaService, ref: ArtifactRef) -> dict[str, JsonValue]:
    receipt = verify_completion_claims(service.repository, project_id=ref.project_id, artifact_refs=[ref])[0]
    assert receipt["record_verified"] is True
    return cast(dict[str, JsonValue], receipt["current_state"])


def _checks(state: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    return cast(list[dict[str, JsonValue]], state["source_checks"])


def test_receipts_index_and_details_keep_old_stale_route_beside_current_route(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    project_id = document.project.id
    first = service.prepare_h3_group(project_id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    assert first.route_plan_id
    original = service.repository.get_h3_route_plan(first.route_plan_id)
    assert original is not None
    refs = [ArtifactRef(project_id=project_id, artifact_type="h3_route_plan", artifact_id=original.id,
                        version=original.state_revision),
            ArtifactRef(project_id=project_id, artifact_type="h3_group_operation", artifact_id=first.operation_id)]
    for ref in refs:
        state = _state(service, ref)
        assert state["source_status"] == "CURRENT"
        assert _checks(state)[0]["scope"] == "SAVED_OPERATION_SOURCE"
        assert _checks(state)[0]["reason"] is None
    _reword(service, project_id, unit.shot_ids[0])
    current = service.prepare_h3_group(project_id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    assert current.route_plan_id and current.route_plan_id != original.id
    before = service.repository.get_project(project_id)
    for ref in refs:
        state = _state(service, ref)
        assert state["source_status"] == "STALE"
        assert "outdated shot" in str(_checks(state)[0]["reason"])
        assert state["saved_status"] in {"READY", "PREPARED"}
    resolver = ProjectArtifactResolver(service.repository)
    index = resolver.summarize_artifact(ArtifactRef(project_id=project_id,
        artifact_type="h3_route_plan_index", artifact_id=project_id))
    assert index is not None
    rows = json.loads(index.summary)["plans"]
    assert {row["plan_id"]: row["source_status"] for row in rows} == {original.id: "STALE", current.route_plan_id: "CURRENT"}
    detail = resolver.summarize_artifact(refs[0])
    assert detail is not None and json.loads(detail.summary)["source_status"] == "STALE"
    assert service.repository.get_h3_route_plan(original.id) == original
    assert service.repository.get_project(project_id) == before
    assert len(fake.calls) == 2 and service.repository.list_tasks(project_id) == []


@pytest.mark.parametrize("change", ["replace_file", "missing_file"])
def test_receipt_rechecks_selected_draft_file_after_preparation(tmp_path: Path, change: str) -> None:
    service, fake, document, _generation, unit, media, _container = _composition_fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[media]))
    assert prepared.route_plan_id and media.source.local_path
    ref = ArtifactRef(project_id=document.project.id, artifact_type="h3_route_plan", artifact_id=prepared.route_plan_id)
    assert _state(service, ref)["source_status"] == "CURRENT"
    if change == "replace_file":
        Image.new("RGB", (65, 97), "black").save(media.source.local_path)
    else:
        Path(media.source.local_path).unlink()
    state = _state(service, ref)
    assert state["source_status"] == "STALE"
    assert _checks(state)[0]["reason"]
    assert len(fake.calls) == 1 and service.repository.list_tasks(document.project.id) == []


def test_legacy_route_receipt_reports_limited_check_and_unprepared_group_is_unknown(tmp_path: Path) -> None:
    service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    assert prepared.route_plan_id
    plan = service.repository.get_h3_route_plan(prepared.route_plan_id)
    operation = service.repository.get_h3_group_operation(prepared.operation_id)
    assert plan is not None and operation is not None
    legacy = plan.model_copy(update={"id": "legacy-route", "version": plan.version + 1, "source_operation_id": None}, deep=True)
    service.repository.save_h3_route_plan(legacy)
    state = _state(service, ArtifactRef(project_id=document.project.id, artifact_type="h3_route_plan", artifact_id=legacy.id))
    assert state["source_status"] == "CURRENT"
    assert _checks(state)[0]["scope"] == "CURRENT_INPUTS_ONLY"
    assert "no saved operation source" in str(_checks(state)[0]["reason"])
    operation.id = operation.state.operation_id = "unfinished-operation"
    operation.state.status, operation.state.route_plan_id = "PREPARING", None
    service.repository.save_h3_group_operation(operation)
    state = _state(service, ArtifactRef(project_id=document.project.id, artifact_type="h3_group_operation", artifact_id=operation.id))
    assert state["saved_status"] == "PREPARING" and state["source_status"] == "UNKNOWN"
    assert state["source_checks"] == [] and state["source_reason"]


def test_actual_adopted_previous_media_survives_read_and_trim_change_is_stale(tmp_path: Path) -> None:
    service, fake, document, generation, _unit, _request = _fixture(tmp_path)
    project_id = document.project.id
    previous_shot = document.shots[1]
    service.enqueue(project_id, EnqueueRequest(shot_ids=[previous_shot.id], candidate_count=1))
    service.drain(project_id, 50)
    document = service.get_project(project_id).document
    unit = generation.generation_plan.units[1]
    shots = [shot for shot in document.shots if shot.id in unit.shot_ids]
    fake.output_text = build_h3_generation_unit_prompt(document, unit, shots)
    prepared = service.prepare_h3_group(project_id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    assert prepared.status == "PREPARED", prepared.error
    assert prepared.route_plan_id
    operation = service.repository.get_h3_group_operation(prepared.operation_id)
    assert operation is not None
    source = cast(dict[str, JsonValue], operation.input_context["source_context"])
    boundary = cast(dict[str, JsonValue], source["actual_previous_media_boundary"])
    assert boundary["status"] == "ADOPTED_MEDIA" and boundary["take_id"]
    ref = ArtifactRef(project_id=project_id, artifact_type="h3_route_plan", artifact_id=prepared.route_plan_id)
    assert _state(service, ref)["source_status"] == "CURRENT"
    editorial = read_editorial(service, project_id)
    project = EditorProject.model_validate(editorial.project)
    clip = project.timelines[0].clips[0]
    clip.duration -= 0.5
    service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=editorial.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    state = _state(service, ref)
    assert state["source_status"] == "STALE"
    assert "saved H3 operation source" in str(_checks(state)[0]["reason"])
    assert service.repository.get_h3_group_operation(operation.id) == operation
    assert len(fake.calls) == 1
