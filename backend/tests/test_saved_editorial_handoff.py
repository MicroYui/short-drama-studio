"""Historical edit receipts survive an unrelated concurrent edit without claiming it."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import ActionKind, AgentResultStatus, ArtifactRef, WorkOrder
from short_drama.agent_runtime.completion_saved_results import saved_completion_artifact_refs
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.production_manager import AgentWorkOrderResultRequest, LanguageSpecialistAgent, ProductionManagerError
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.tools import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient
from tests.test_studio_shot_editorial import episode as episode


def saved_opening(service: ShortDramaService, pid: str) -> tuple[WorkOrder, ArtifactRef, str, str]:
    timeline = EditorProject.model_validate(read_editorial(service, pid).project).timelines[0]
    clips = [clip for clip in timeline.clips if clip.type == "video"]
    for revision in range(1, 5):
        result = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
            "timeline_id": timeline.id, "expected_revision": revision,
            "clips": [{"id": clips[-1].id, "volume": 0.8 - revision / 10}]}})
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
    arguments: dict[str, JsonValue] = {"project_id": pid, "request": {
        "timeline_id": timeline.id, "expected_revision": 5,
        "clips": [{"id": clips[0].id, "source_in_seconds": 1, "duration_seconds": 3}]}}
    written = service.tool_registry.invoke("timeline.edit_clips", arguments)
    assert written.status == ToolCallStatus.SUCCEEDED, written.error
    ref = written.artifact_refs[0]
    assert ref.version == 6
    run = service.agent_runtime.create_run(project_id=pid, objective="剪短开头这一处采用；保留其他工作。", root_agent="leader")
    work = service.agent_runtime.create_work_order(run.id, sender="leader", recipient_role="director",
        objective=run.objective, why_now="当前开头修改已保存，交接给导演。",
        production_skill_role=ProductionSkillRole.DIRECTOR)
    work.specialist_turns = [SpecialistTurnRecord(id="saved-opening-edit", sequence=1,
        tool_name="timeline.edit_clips", arguments=arguments, reason="只裁切选中开头采用。",
        status=SpecialistTurnStatus.COMPLETED, result=written.model_dump(mode="json"))]
    service.agent_runtime.store.save_work_order(work)
    return work, ref, clips[0].id, clips[-1].id


def edit_other(service: ShortDramaService, pid: str, ref: ArtifactRef, clip_id: str) -> None:
    written = service.tool_registry.invoke("timeline.edit_clips", {"project_id": pid, "request": {
        "timeline_id": ref.artifact_id, "expected_revision": 6, "clips": [{"id": clip_id, "volume": 0.23}]}})
    assert written.status == ToolCallStatus.SUCCEEDED, written.error
    assert written.artifact_refs[0].version == 7


def completion(**updates: object) -> AgentWorkOrderResultRequest:
    return AgentWorkOrderResultRequest(status=AgentResultStatus.COMPLETED,
        concise_summary="开头采用已裁短并保存。", saved_tool_result_sequences=[1]).model_copy(update=updates)


def test_real_disjoint_edit_during_worker_handoff_keeps_exact_source_and_currentness(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, _ids = episode
    work, ref, opening, ending = saved_opening(service, pid)
    before = EditorProject.model_validate(read_editorial(service, pid).project)
    action = service.agent_runtime.begin_action(work.parent_run_id, kind=ActionKind.DELEGATION,
        agent_id="leader", input_summary=work.objective, work_order_id=work.id)
    started, release = Event(), Event()

    class WaitingClient(ScriptedClient):
        def generate_text(self, *, instructions: str, input_text: str,
                          image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
            started.set()
            assert release.wait(10), "parallel edit did not finish"
            return super().generate_text(instructions=instructions, input_text=input_text,
                image_urls=image_urls, max_output_tokens=max_output_tokens)

    client = WaitingClient([completion().model_dump_json(exclude_defaults=True)])
    agent = LanguageSpecialistAgent(client, service.tool_registry)
    agent.context_compiler = ModelInputCompiler(service.agent_runtime.store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(agent.execute, service.get_agent_work_order(work.id))
        try:
            assert started.wait(10), "Worker never reached its Fake language call"
            edit_other(service, pid, ref, ending)
        finally:
            release.set()
        request = pending.result(timeout=10)
    assert isinstance(request, AgentWorkOrderResultRequest)
    result = service.submit_agent_work_order_result(work.id, request).result
    assert result.status == AgentResultStatus.COMPLETED and len(client.payloads) == 1
    assert ref in result.evidence_refs and result.created_artifacts == result.updated_artifacts == []
    receipt = next(row for row in result.completion_receipts if row.get("kind") == "saved_tool_result")
    assert receipt["artifact_refs"] == [ref.model_dump(mode="json")]
    assert receipt["current_artifact_refs"] == []
    currentness = cast(list[dict[str, JsonValue]], receipt["artifact_currentness"])[0]
    assert (currentness["source_revision"], currentness["current_revision"]) == (6, 7)
    assert currentness["source_status"] == "HISTORICAL_REVISION" and currentness["current_content_verified"] is False
    assert saved_completion_artifact_refs(result.completion_receipts) == []
    finished_action = service.agent_runtime.store.get_action(action.id)
    assert finished_action is not None and finished_action.artifact_refs == []
    reopened = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    assert reopened.agent_runtime.store.get_agent_result(work.id) == result
    marker = cast(dict[str, JsonValue], receipt["saved_result"])
    assert reopened.agent_runtime.store.get_context_snapshot(cast(str, marker["context_ref"]), work.parent_run_id) == {
        "arguments": work.specialist_turns[0].arguments, "result": work.specialist_turns[0].result}
    after = EditorProject.model_validate(read_editorial(reopened, pid).project)
    old_clips = {clip.id: clip for clip in before.timelines[0].clips}
    new_clips = {clip.id: clip for clip in after.timelines[0].clips}
    assert new_clips[opening] == old_clips[opening]
    assert (new_clips[opening].trimStart, new_clips[opening].duration) == (1, 3)
    # The linked audio owns this video's original sound; its gain changes once.
    assert new_clips["linked-2"].volume == 0.23
    assert new_clips[ending] == old_clips[ending]
    assert after.timelines[0].subtitles == before.timelines[0].subtitles


def test_explicit_stale_current_output_is_rejected_with_claimed_and_current_revision(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _ids = episode
    work, ref, _opening, ending = saved_opening(service, pid)
    edit_other(service, pid, ref, ending)
    with pytest.raises(ProductionManagerError, match="claimed revision 6, current revision 7"):
        service.submit_agent_work_order_result(work.id, completion(updated_artifacts=[ref]))
    assert service.agent_runtime.store.get_agent_result(work.id) is None
    # Removing only the incorrect current-output claim retains the exact saved operation.
    assert service.submit_agent_work_order_result(work.id, completion()).result.status == AgentResultStatus.COMPLETED


def test_current_editorial_source_keeps_verified_output_receipt(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _ids = episode
    work, ref, _opening, _ending = saved_opening(service, pid)
    result = service.submit_agent_work_order_result(work.id, completion()).result
    assert saved_completion_artifact_refs(result.completion_receipts) == [ref]
    verified = next(row for row in result.completion_receipts if row.get("record_verified") is True)
    assert verified["current_state"] == {"editorial_revision": 6}
    receipt = next(row for row in result.completion_receipts if row.get("kind") == "saved_tool_result")
    assert cast(list[dict[str, JsonValue]], receipt["artifact_currentness"])[0]["source_status"] == "CURRENT"


@pytest.mark.parametrize("invalid", ["foreign", "missing", "future"])
def test_historical_sources_still_require_real_project_and_saved_timeline(
    episode: tuple[ShortDramaService, str, list[str]], invalid: str,
) -> None:
    service, pid, _ids = episode
    work, ref, _opening, _ending = saved_opening(service, pid)
    updates = {"project_id": "foreign"} if invalid == "foreign" else {"artifact_id": "missing"} if invalid == "missing" else {"version": 8}
    source = work.specialist_turns[0].result
    assert source is not None
    source["artifact_refs"] = [ref.model_copy(update=updates).model_dump(mode="json")]
    service.agent_runtime.store.save_work_order(work)
    expected = "another project" if invalid == "foreign" else "no current timeline object" if invalid == "missing" else "future revision 8"
    with pytest.raises(ProductionManagerError, match=expected):
        service.submit_agent_work_order_result(work.id, completion())
    assert service.agent_runtime.store.get_agent_result(work.id) is None
