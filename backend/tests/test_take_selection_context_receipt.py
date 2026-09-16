"""A saved source interval survives Registry, persistence and Worker compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime import AgentRuntimeStore
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.models import Take
from short_drama.production_manager import (
    AgentWorkOrderView, LanguageSpecialistAgent, SpecialistToolRequest, _specialist_tool_result_for_persistence,
    _specialist_working_set,
)
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import read_editorial
from short_drama.tools.contracts import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_studio_take_source_selection import candidate_use as candidate_use, episode as episode


def test_actual_adoption_receipt_reaches_restarted_worker_without_project_replay(
    candidate_use: tuple[ShortDramaService, str, Take, str], tmp_path: Path,
) -> None:
    service, pid, candidate, clip_id = candidate_use
    before = read_editorial(service, pid)
    arguments: dict[str, JsonValue] = {"take_id": candidate.id, "clip_ids": [clip_id],
        "expected_editorial_revision": before.revision, "source_in_seconds": 4.5, "source_out_seconds": 10.045}
    result = service.tool_registry.invoke("video.select_take", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED and result.receipt is not None, result.error
    saved = read_editorial(service, pid)
    state = result.receipt.current_state
    assert state["editorial_revision"] == saved.revision == before.revision + 1
    assert state["editorial_saved"] is True
    video = pointer_value(state, "/adopted_clips/0")
    sound = pointer_value(state, "/linked_native_audio_clips/0")
    assert isinstance(video, dict) and isinstance(sound, dict)
    assert video["clip_id"] == clip_id and sound["type"] == "audio"
    for actual in (video, sound):
        assert actual["source_in_seconds"] == 4.5 and actual["source_out_seconds"] == pytest.approx(10.045)
        assert actual["film_start_seconds"] == 3.875 and actual["film_end_seconds"] == pytest.approx(9.42)
        assert actual["duration_seconds"] == 5.545 and actual["media_path"] == candidate.media_path
        assert actual["source_take_id"] == candidate.id and actual["speed"] == 1
        assert actual["volume"] == .65 and actual["audio_fade_in_seconds"] == .25
    assert len(cast(list[JsonValue], state["adopted_clips"])) == 1
    assert len(cast(list[JsonValue], state["linked_native_audio_clips"])) == 1
    assert result.receipt.changed_ids == {}  # Acknowledgement is not a fabricated delta.
    store, work, context = runtime_work(tmp_path / "worker.sqlite3", project_id=pid)
    work.working_notes = "Previous selection might not have sent the explicit source window."
    persisted = _specialist_tool_result_for_persistence("video.select_take", result.model_dump(mode="json"), arguments=arguments)
    work.specialist_turns = [SpecialistTurnRecord(id="saved-selection", sequence=1, tool_name="video.select_take",
        arguments=arguments, reason="Adopt only the observed candidate interval.", status=SpecialistTurnStatus.COMPLETED,
        result=persisted)]
    work.loaded_tool_schema_ids = ["video.select_take", "timeline.read_editorial"]
    store.save_work_order(work)
    restarted = AgentRuntimeStore(store.database_path)
    restored = restarted.get_work_order(work.id)
    assert restored is not None
    original = restored.model_dump_json(exclude={"loaded_tool_schema_ids"})
    client = ScriptedClient([json.dumps({"tool_name": "timeline.read_editorial", "arguments": {
        "project_id": pid, "timeline_id": state["timeline_id"], "subtitle_ids": ["cue-1"]},
        "reason": "Continue with the target subtitle from the acknowledged adoption."})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(restarted)
    response = worker.execute(AgentWorkOrderView(work_order=restored, context=context, system_prompt="Continue editing."))
    assert len(client.payloads) == 1
    handoff = pointer_value(client.payloads[0], "/working_set/latest_handoff")
    assert isinstance(handoff, dict)
    assert pointer_value(handoff, "/request/arguments") == arguments
    assert pointer_value(handoff, "/result/receipt/current_state") == state
    handoff_result = handoff["result"]
    assert isinstance(handoff_result, dict) and "output" not in handoff_result
    assert "production_documents" not in json.dumps(client.payloads[0]["working_set"])
    assert restored.model_dump_json(exclude={"loaded_tool_schema_ids"}) == original
    assert isinstance(response, SpecialistToolRequest) and response.tool_name == "timeline.read_editorial"
    followup = service.tool_registry.invoke(response.tool_name, response.arguments)
    assert followup.status == ToolCallStatus.SUCCEEDED
    assert read_editorial(service, pid).revision == saved.revision
    # Idempotent confirmation reports actual saved facts and does not imply a
    # new revision, changed subtitle or newly generated media.
    arguments["expected_editorial_revision"] = saved.revision
    repeat = service.tool_registry.invoke("video.select_take", arguments)
    assert repeat.status == ToolCallStatus.SUCCEEDED and repeat.receipt == result.receipt
    assert read_editorial(service, pid) == saved


@pytest.mark.parametrize("later_read", [False, True])
def test_legacy_success_preserves_exact_arguments_without_inventing_saved_content(later_read: bool) -> None:
    arguments: dict[str, JsonValue] = {"take_id": "candidate", "clip_ids": ["target"],
        "expected_editorial_revision": 10, "source_in_seconds": 4.5, "source_out_seconds": 10.045}
    order = WorkOrder(id="editor", parent_run_id="run", sender="leader", recipient_role="editor",
        objective="Adopt the usable candidate and finish the subtitle/export.", why_now="User accepted both versions.")
    order.specialist_turns = [SpecialistTurnRecord(id="old-selection", sequence=1, tool_name="video.select_take",
        arguments=arguments, reason="Adopt the selected interval.", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "video.select_take", "status": "SUCCEEDED", "output": {"take_selection": []},
            "receipt": {"operation_completed": True, "current_state": {}, "changed_ids": {}}})]
    if later_read:
        order.specialist_turns.append(SpecialistTurnRecord(id="current-read", sequence=2, tool_name="timeline.read_editorial",
            arguments={"project_id": "project", "include_project": True}, reason="Read current edit.",
            status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"revision": 10}}))
    before = order.model_dump_json()
    current = _specialist_working_set(WorkOrder.model_validate_json(before), [], project_id="project",
        read_tool_ids=frozenset({"timeline.read_editorial"}), write_tool_ids=frozenset({"video.select_take"}))
    checkpoint = next(item for item in current.tool_checkpoints if item.tool_name == "video.select_take")
    assert checkpoint.request_identifiers == arguments and checkpoint.status == "SUCCEEDED"
    if not later_read:
        assert current.latest_handoff is not None
        assert pointer_value(current.latest_handoff, "/request/arguments") == arguments
        assert pointer_value(current.latest_handoff, "/result/receipt/current_state") == {}
    assert order.model_dump_json() == before  # No historical Trace rewrite.


def test_initial_take_receipt_does_not_claim_projected_clips_are_saved(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _shots = episode
    take = service.get_project(pid).takes[0]
    document = service.get_project(pid).document
    document.editorial_timeline = None
    service.repository.save_project(document)
    assert not service.get_project(pid).document.editorial_timeline
    result = service.tool_registry.invoke("video.select_take", {"take_id": take.id})
    assert result.status == ToolCallStatus.SUCCEEDED and result.receipt is not None
    assert result.receipt.current_state["editorial_saved"] is False
    assert result.receipt.current_state["take_selected_shot_ids"] == take.selected_shot_ids
    assert "adopted_clips" not in result.receipt.current_state


def test_default_selection_empty_clip_scope_matches_omission_but_empty_shots_still_fail(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, pid, _shots = episode
    take = service.get_project(pid).takes[0]
    default = service.tool_registry.invoke("video.select_take", {"take_id": take.id})
    assert default.status == ToolCallStatus.SUCCEEDED and default.receipt is not None
    adopted = default.receipt.current_state["adopted_clips"]
    assert isinstance(adopted, list) and len(adopted) == 1
    before = service.get_project(pid)
    empty_clips = service.tool_registry.invoke("video.select_take", {"take_id": take.id, "clip_ids": []})
    assert empty_clips.status == ToolCallStatus.SUCCEEDED
    assert empty_clips.receipt == default.receipt
    # Existing service semantics differ here: explicit empty shot_ids is an
    # invalid adoption, so it must not acquire a successful/default receipt.
    empty_shots = service.tool_registry.invoke("video.select_take", {"take_id": take.id, "shot_ids": []})
    assert empty_shots.status == ToolCallStatus.FAILED and empty_shots.receipt is None
    assert empty_shots.error is not None and "Selected shot_ids" in empty_shots.error.message
    after = service.get_project(pid)
    assert after.document == before.document and after.takes == before.takes
