"""Completion claims refer to saved outputs; semantic analysis stays lightweight."""

from pathlib import Path
from typing import cast

from PIL import Image
from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import AgentResultStatus, ArtifactRef
from short_drama.agent_runtime.completion import verify_completion_claims
from short_drama.agent_runtime.contract_validation import AgentResultContractError
from short_drama.models import ApprovalStatus, AssetKind, EnqueueRequest
from short_drama.production_manager import AgentWorkOrderResultRequest, ProductionManagerError
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import read_editorial
from short_drama.studio_media import import_studio_media
from short_drama.tools import ToolCallStatus


@pytest.fixture
def service(tmp_path: Path) -> ShortDramaService:
    result = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    result.initialize_fixture()
    return result


def test_available_draft_receipt_and_missing_file_rejection(service: ShortDramaService, tmp_path: Path) -> None:
    project_id = service.list_projects().projects[0].id
    image_path = tmp_path / "draft.png"
    Image.new("RGB", (32, 32), "blue").save(image_path)
    added = import_studio_media(service, project_id, image_path, name="Current selected draft", kind=AssetKind.REFERENCE, asset_id=None)
    version = added.document.assets[-1].versions[-1]
    version.approval_status = ApprovalStatus.DRAFT
    service.repository.save_project(added.document)
    ref = ArtifactRef(project_id=project_id, artifact_type="asset_version", artifact_id=version.id, version=version.version)
    receipt = verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[ref])[0]
    assert receipt["current_state"] == {"approval_status": "DRAFT", "freshness": "CURRENT"}
    assert receipt["record_verified"] is True
    assert version.local_path is not None
    Path(version.local_path).unlink()
    with pytest.raises(AgentResultContractError, match="local_path.*not usable"):
        verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[ref])


def test_saved_artifact_revision_and_explicit_output_slots_are_checked(service: ShortDramaService) -> None:
    project_id = service.list_projects().projects[0].id
    shot = service.get_project(project_id).document.shots[0]
    ref = ArtifactRef(project_id=project_id, artifact_type="shot", artifact_id=shot.id, version=shot.revision)
    assert verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[ref])
    with pytest.raises(AgentResultContractError, match="artifact/version was not found"):
        verify_completion_claims(service.repository, project_id=project_id,
            artifact_refs=[ref.model_copy(update={"version": shot.revision + 5})])
    with pytest.raises(AgentResultContractError, match="expected_output_slot_ids/absent"):
        verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[], expected_output_slot_ids=["absent"])


def test_queued_task_receipt_does_not_assert_finished_media(service: ShortDramaService) -> None:
    project_id = service.list_projects().projects[0].id
    shot = service.get_project(project_id).document.shots[0]
    service.enqueue(project_id, EnqueueRequest(shot_ids=[shot.id], candidate_count=1))
    task = service.repository.list_tasks(project_id)[0]
    ref = ArtifactRef(project_id=project_id, artifact_type="generation_task", artifact_id=task.id)
    receipt = verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[ref])[0]
    assert receipt["current_state"] == {"status": task.status.value}
    assert receipt["files"] == []


def test_completion_submit_rejects_unwritten_claim_and_preserves_saved_receipt(service: ShortDramaService, tmp_path: Path) -> None:
    project_id = service.list_projects().projects[0].id
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=project_id, objective="Return the saved script.", root_agent="leader")
    order = runtime.create_work_order(run.id, sender="leader", recipient_role="writer",
        objective="Save one script.", why_now="The user requested it.")
    request = AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED, concise_summary="Saved script.",
        deliverable={"output_path": str(tmp_path / "missing.txt")})
    with pytest.raises(ProductionManagerError, match="output_path.*not usable"):
        service.submit_agent_work_order_result(order.id, request)
    assert runtime.store.get_agent_result(order.id) is None
    output = tmp_path / "script.txt"
    output.write_text("场景一：人物推门进来。", encoding="utf-8")
    completed = service.submit_agent_work_order_result(order.id,
        request.model_copy(update={"deliverable": {"output_path": str(output)}}))
    assert completed.result.completion_receipts
    stored = runtime.store.get_agent_result(order.id)
    assert stored is not None and stored.completion_receipts == completed.result.completion_receipts


def test_analytical_completion_remains_possible_but_next_actions_alone_do_not_finish(service: ShortDramaService) -> None:
    project_id = service.list_projects().projects[0].id
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=project_id, objective="Analyze the scene.", root_agent="leader")
    order = runtime.create_work_order(run.id, sender="leader", recipient_role="director",
        objective="Give a concrete observation.", why_now="The user requested analysis.", acceptance_criteria=["One useful conclusion"])
    with pytest.raises(ProductionManagerError, match="acceptance_criteria"):
        service.submit_agent_work_order_result(order.id, AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
            concise_summary="Continue later.", recommended_next_actions=["Read the scene."]))
    result = service.submit_agent_work_order_result(order.id, AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="The reveal has a clear cause.", findings=["The opening question motivates the final reveal."]))
    assert result.result.completion_receipts == []


def test_current_editorial_write_returns_saved_revision_and_resolvable_artifact(service: ShortDramaService) -> None:
    project_id = service.list_projects().projects[0].id
    current = read_editorial(service, project_id)
    result = service.tool_registry.invoke("timeline.save_editorial", {"project_id": project_id, "request": {
        "expected_revision": current.revision, "timeline": {"project": current.project}}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.receipt is not None and result.artifact_refs
    saved = service.get_project(project_id).document.editorial_timeline
    assert saved is not None
    assert result.receipt.current_state["editorial_revision"] == saved["revision"]
    receipts = verify_completion_claims(service.repository, project_id=project_id, artifact_refs=result.artifact_refs)
    assert cast(dict[str, JsonValue], receipts[0]["current_state"])["editorial_revision"] == saved["revision"]


def test_take_and_saved_export_require_available_files(service: ShortDramaService, tmp_path: Path) -> None:
    project_id = service.list_projects().projects[0].id
    shot = service.get_project(project_id).document.shots[0]
    service.enqueue(project_id, EnqueueRequest(shot_ids=[shot.id], candidate_count=1))
    take = service.drain(project_id, 50).project.takes[0]
    take_ref = ArtifactRef(project_id=project_id, artifact_type="take", artifact_id=take.id)
    assert verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[take_ref])[0]["files"]
    # An empty subtitle file is a valid export for media with no subtitles.
    subtitles = tmp_path / "empty.srt"
    subtitles.write_text("", encoding="utf-8")
    document = service.get_project(project_id).document
    document.studio_exports.append({"id": "export-local", "project_id": project_id, "editorial_revision": 3,
        "status": "completed", "video_path": take.media_path, "srt_path": str(subtitles), "duration_seconds": take.duration_seconds})
    service.repository.save_project(document)
    export_ref = ArtifactRef(project_id=project_id, artifact_type="studio_export", artifact_id="export-local", version=3)
    assert verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[export_ref])[0]["current_state"]
    with pytest.raises(AgentResultContractError, match="revision does not match"):
        verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[export_ref.model_copy(update={"version": 4})])
    Path(take.media_path).unlink()
    with pytest.raises(AgentResultContractError, match="media_path.*not usable"):
        verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[take_ref])
    with pytest.raises(AgentResultContractError, match="video_path.*not usable"):
        verify_completion_claims(service.repository, project_id=project_id, artifact_refs=[export_ref])
