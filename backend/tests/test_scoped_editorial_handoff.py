"""Local editorial work shares a timeline ID, not every delivery's scope."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ArtifactRef
from short_drama.agent_runtime.leader_context import completed_work_handoffs
from short_drama.agent_runtime.models import AgentResult, AgentResultStatus, SpecialistTurnRecord, SpecialistTurnStatus
from tests.test_completed_worker_handoff import worker_input
from tests.test_harness_tool_context import runtime_work


@pytest.mark.parametrize("timeline_type", ["editorial", "editorial_timeline"])
def test_same_timeline_does_not_deliver_another_clip_completion(tmp_path: Path, timeline_type: str) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    runtime = AgentRuntime(store)
    timeline = ArtifactRef(project_id=context.project_id, artifact_type=timeline_type,
                           artifact_id="shared-editorial", version=8)
    results: list[AgentResult] = []
    for name in ("opening", "ending"):
        work = runtime.create_work_order(context.run_id, sender="leader", recipient_role=f"{name}-editor",
                                        objective=f"Edit {name}", why_now="Local edit")
        result = AgentResult(id=f"result-{name}", work_order_id=work.id, status=AgentResultStatus.COMPLETED,
            concise_summary=f"Saved {name}; listening is still unknown.", updated_artifacts=[timeline],
            deliverable={"timeline_id": timeline.artifact_id, "video_uses": [
                {"clip_id": f"clip-{name}", "source_range_seconds": [2, 5], "speed": 1}],
                "audio_semantics_observed": False},
            completion_receipts=[{"artifact_ref": timeline.model_dump(mode="json"),
                "record_verified": True, "current_state": {"revision": 8}}])
        store.save_agent_result(result)
        results.append(result)
    receiver.task_inputs = {"timeline_id": timeline.artifact_id, "clip_id": "clip-opening"}
    receiver = type(receiver).model_validate({**receiver.model_dump(), "context_manifest": None, "scope": [timeline]})
    store.save_work_order(receiver)
    reopened = AgentRuntimeStore(store.database_path)
    payload = worker_input(reopened, receiver, context)
    rows = cast(list[dict[str, JsonValue]], payload["completion_handoffs"])
    assert [row["result_id"] for row in rows] == [results[0].id]
    assert cast(dict[str, JsonValue], rows[0]["result"])["deliverable"] == results[0].deliverable
    assert reopened.get_agent_result(results[1].work_order_id) == results[1]
    # Whole-run Leader inventory and an explicit completed-result request still
    # expose the full delivery. Sharing a project ID alone does not do that.
    all_rows, _ = completed_work_handoffs(reopened, run_id=context.run_id, project_id=context.project_id)
    assert len(all_rows) == 2
    for identifier in (results[1].id, results[1].work_order_id):
        selected, _ = completed_work_handoffs(reopened, run_id=context.run_id,
            project_id=context.project_id, selected_ids={identifier, timeline.artifact_id})
        assert len(selected) == 1
        assert cast(dict[str, JsonValue], selected[0])["result_id"] == results[1].id


def test_a_shared_timeline_is_not_a_match_but_a_cross_boundary_source_is(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    work = AgentRuntime(store).create_work_order(context.run_id, sender="leader", recipient_role="audio-editor",
        objective="Preserve dialogue across the cut", why_now="Continuous audio")
    result = AgentResult(id="result-audio", work_order_id=work.id, status=AgentResultStatus.COMPLETED,
        concise_summary="The preserved PCM crosses the picture cut; no listening claim.",
        deliverable={"timeline_id": "shared-editorial", "continuous_audio": {
            "source_take_id": "take-dialogue", "clip_id": "audio-continuous",
            "source_range_seconds": [0, 3], "film_range_seconds_at_last_read": [12.08, 15.08]},
            "audio_semantics_observed": False},
        updated_artifacts=[ArtifactRef(project_id=context.project_id, artifact_type="editorial_timeline",
                                      artifact_id="shared-editorial", version=8)])
    store.save_agent_result(result)
    for selected in ({"shared-editorial"}, {"shared-editorial", "take-dialogue"}):
        rows, _ = completed_work_handoffs(store, run_id=context.run_id,
            project_id=context.project_id, selected_ids=selected)
        assert bool(rows) == ("take-dialogue" in selected)
        if rows:
            body = cast(dict[str, JsonValue], cast(dict[str, JsonValue], rows[0])["result"])
            assert body["deliverable"] == result.deliverable


def test_returned_directory_does_not_select_every_group_but_later_request_does(tmp_path: Path) -> None:
    store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    runtime = AgentRuntime(store)
    work = runtime.create_work_order(context.run_id, sender="leader", recipient_role="director",
                                    objective="Prepare two groups", why_now="Saved source")
    refs = [ArtifactRef(project_id=context.project_id, artifact_type="generation_unit",
                        artifact_id=f"group-{name}", version=3) for name in ("opening", "ending")]
    result = AgentResult(id="result-groups", work_order_id=work.id, status=AgentResultStatus.COMPLETED,
        concise_summary="Both groups saved; individual observations remain limited.", created_artifacts=refs,
        deliverable={"groups": [{"generation_unit_id": ref.artifact_id,
            "decision": f"Preserve the recorded action for {ref.artifact_id}", "listening": "UNKNOWN"} for ref in refs]})
    store.save_agent_result(result)
    receiver.task_inputs = {"generation_unit_id": "group-ending"}
    receiver.specialist_turns = [SpecialistTurnRecord(id="directory-read", sequence=1,
        tool_name="artifact.read_current", arguments={"project_id": context.project_id}, reason="Read the directory",
        status=SpecialistTurnStatus.COMPLETED, result={"tool_id": "artifact.read_current", "status": "SUCCEEDED",
            "output": {"directory": "Both groups are available"}, "artifact_refs": [ref.model_dump(mode="json") for ref in refs]})]
    store.save_work_order(receiver)
    before = result.model_dump(mode="json")
    payload = worker_input(AgentRuntimeStore(store.database_path), receiver, context)
    rows = cast(list[dict[str, JsonValue]], payload["completion_handoffs"])
    body = cast(dict[str, JsonValue], rows[0]["result"])
    groups = cast(list[JsonValue], result.deliverable["groups"])
    assert cast(dict[str, JsonValue], body["deliverable"])["groups"] == [groups[1]]
    receiver.specialist_turns.append(SpecialistTurnRecord(id="selected-read", sequence=2,
        tool_name="artifact.read_current", arguments={"project_id": context.project_id, "generation_unit_id": "group-opening"},
        reason="Now explicitly read the other group", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "artifact.read_current", "status": "SUCCEEDED", "output": {}, "artifact_refs": []}))
    store.save_work_order(receiver)
    payload = worker_input(AgentRuntimeStore(store.database_path), receiver, context)
    rows = cast(list[dict[str, JsonValue]], payload["completion_handoffs"])
    assert cast(dict[str, JsonValue], rows[0]["result"])["deliverable"] == result.deliverable
    saved = store.get_agent_result(work.id)
    assert saved is not None and saved.model_dump(mode="json") == before


def test_actual_whole_film_read_selects_its_occurrences_not_unused_candidates(tmp_path: Path) -> None:
    from short_drama.agent_runtime.leader_context import completion_selected_ids

    _store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    receiver.specialist_turns = [SpecialistTurnRecord(id="film-read", sequence=1,
        tool_name="timeline.read_editorial", arguments={"project_id": context.project_id},
        reason="Read the entire saved film", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "timeline.read_editorial", "status": "SUCCEEDED", "output": {"content": {
            "is_saved": True, "coordinate_space": "FILM", "clips": [
                {"id": "opening-use", "shot_id": "opening"}, {"id": "ending-use", "shot_id": "ending"}],
            "subtitles": [{"id": "line-ending", "text": "A saved utterance"}]}},
            "artifact_refs": [{"project_id": context.project_id, "artifact_type": "take", "artifact_id": "unused-candidate"}]})]
    selected = completion_selected_ids(receiver, [])
    assert {"opening-use", "ending-use", "line-ending", "opening", "ending"} <= selected
    assert "unused-candidate" not in selected


def test_saving_a_project_body_does_not_select_every_unchanged_clip(tmp_path: Path) -> None:
    from short_drama.agent_runtime.leader_context import completion_selected_ids

    _store, receiver, context = runtime_work(tmp_path / "agent.sqlite3")
    receiver.task_inputs = {"clip_id": "opening-use"}
    receiver.specialist_turns = [SpecialistTurnRecord(id="save-film", sequence=1,
        tool_name="timeline.save_editorial", arguments={"project_id": context.project_id,
            "request": {"timeline_id": "film", "project": {"timelines": [{"clips": [
                {"id": "unrelated-use", "sourceTakeId": "unrelated-take"}]}]}}},
        reason="Save opening volume while preserving all other clips", status=SpecialistTurnStatus.COMPLETED)]
    selected = completion_selected_ids(receiver, [])
    assert "opening-use" in selected
    assert "unrelated-use" not in selected and "unrelated-take" not in selected
    receiver.specialist_turns.append(SpecialistTurnRecord(id="inspect-other", sequence=2,
        tool_name="video.inspect_take", arguments={"project_id": context.project_id, "take_id": "unrelated-take",
            "request": {"scope": "TAKE", "source_start_seconds": 2, "source_end_seconds": 3}},
        reason="Now explicitly inspect the other Take", status=SpecialistTurnStatus.COMPLETED))
    assert "unrelated-take" in completion_selected_ids(receiver, [])


def test_explicit_image_and_generation_media_selection_keeps_its_completed_sources(tmp_path: Path) -> None:
    from short_drama.agent_runtime.leader_context import completion_selected_ids

    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    work.specialist_turns = [SpecialistTurnRecord(id="actual-view", sequence=1, tool_name="image.view",
        arguments={"images": [{"asset_version_id": "selected-face"}, {"asset_version_id": "selected-room"}]},
        reason="Observe these exact images", status=SpecialistTurnStatus.COMPLETED),
        SpecialistTurnRecord(id="actual-reference", sequence=2, tool_name="route.prepare_h3_group",
        arguments={"request": {"media_inputs": [{"source": {"asset_version_id": "selected-keys"}}]}},
        reason="Use this reference", status=SpecialistTurnStatus.COMPLETED)]
    assert {"selected-face", "selected-room", "selected-keys"} <= completion_selected_ids(work, [])
