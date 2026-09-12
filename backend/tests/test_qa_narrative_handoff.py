"""Actual QA receipts keep selected story evidence in resumed production input."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from short_drama.agent_runtime import AgentRuntimeStore, ArtifactRef, DelegationStatus
from short_drama.agent_runtime.media_handoffs import asset_inspection_scope, observed_media_handoffs
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection_narrative_scope import _fixture, _selection
from tests.test_asset_review_scopes import _save_work, _worker_input
from tests.test_completed_worker_handoff import worker_input
from tests.test_partial_media_handoff import leader_input


def test_actual_qa_scope_survives_restart_self_leader_selected_worker_and_offload(tmp_path: Path) -> None:
    service, language, base = _fixture(tmp_path)
    plain = deepcopy(base)
    explicit_defaults = deepcopy(base)
    explicit_defaults["request"].update(narrative_context=None, implementation_preferences=[])
    scoped = deepcopy(base)
    scoped["request"]["narrative_context"] = _selection()
    other_lines = deepcopy(scoped)
    other_lines["request"]["narrative_context"]["dialogue_line_ids"] = ["line-qiantai-06"]
    preference = deepcopy(scoped)
    preference["request"]["implementation_preferences"] = ["门洞同宽是自定实现方案，实际差异与用途影响分别检查。"]
    turns: list[dict[str, Any]] = []
    for sequence, args in enumerate([plain, explicit_defaults, scoped, other_lines, preference, scoped], 1):
        if sequence == 6:
            document = service.get_project(base["project_id"]).document
            current = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
            updated = current.model_copy(deep=True, update={"id": "script-v4-current", "version": 4})
            next(line for line in updated.lines if line.id == "line-qiantai-06").spoken_text = "当前保存的新方案台词。"
            document.script_versions.append(updated)
            document.project.current_script_version_id = updated.id
            service.repository.save_project(document)
        result = service.tool_registry.invoke("qa.inspect_asset_version", args)
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        turns.append(SpecialistTurnRecord(id=f"actual-qa-{sequence}", sequence=sequence,
            tool_name="qa.inspect_asset_version", arguments=args, reason="Review the exact candidate for the declared purpose.",
            status=SpecialistTurnStatus.COMPLETED, result=result.model_dump(mode="json")).model_dump(mode="json"))
    assert language.calls == 6
    original_turns = deepcopy(turns)
    work, context = _save_work(service, base["project_id"], turns)
    work.status = DelegationStatus.IN_PROGRESS
    service.production_manager.runtime.store.save_work_order(work)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    store = reopened.production_manager.runtime.store
    restored = store.get_work_order(work.id)
    assert restored is not None
    own_input = _worker_input(reopened, restored, context)
    reviews = [entry for entry in own_input["working_set"]["entities"] if entry["kind"] == "asset_review"]
    assert {entry["source_sequence"] for entry in reviews} == {2, 3, 4, 5, 6}
    for entry in reviews:
        expected = turns[entry["source_sequence"] - 1]["result"]["output"]
        state = entry["state"]
        assert state["narrative_context"] == expected["narrative_context"]
        assert state["issues"] == expected["issues"]
        assert state["recommended"] is False and state["score"] == 3.5
        assert state["attempt_ref"]["attempt_id"] == turns[entry["source_sequence"] - 1]["id"]
    assert next(entry for entry in reviews if entry["source_sequence"] == 5)["state"]["review_scope"]["implementation_preferences"] == preference["request"]["implementation_preferences"]
    assert {entry["state"]["narrative_context"]["script_version"] for entry in reviews if entry["state"]["narrative_context"]} == {3, 4}

    # Same runtime-owned observations reach the real Planner and selected receiver input.
    leader = leader_input(store, context)
    receiver = reopened.production_manager.runtime.create_work_order(context.run_id,
        sender="leader", recipient_role="asset_builder", objective="Use this observed folder", why_now="Required composition",
        scope=[ArtifactRef(project_id=context.project_id, artifact_type="asset_version", artifact_id=base["version_id"], version=4)])
    downstream = worker_input(store, receiver, context)
    for payload in (leader, downstream):
        rows = payload["media_observations"]
        assert isinstance(rows, list) and len(rows) == 5
        assert {row["attempt_id"] for row in rows if isinstance(row, dict)} == {f"actual-qa-{n}" for n in range(2, 7)}
        for row in rows:
            assert isinstance(row, dict)
            turn = next(turn for turn in turns if turn["id"] == row["attempt_id"])
            assert row["result"] == turn["result"]["output"]

    externalized, refs = observed_media_handoffs(store, run_id=context.run_id, project_id=context.project_id,
        selected_ids={base["version_id"]}, token_budget=0)
    assert len(externalized) == 5
    assert {ref.version for ref in refs if ref.artifact_type == "script_version"} == {3, 4}
    for row in externalized:
        assert isinstance(row, dict)
        marker = row["result"]
        assert isinstance(marker, dict) and isinstance(marker["context_ref"], str)
        snapshot = AgentRuntimeStore(store.database_path).get_context_snapshot(marker["context_ref"], context.run_id)
        turn = next(turn for turn in turns if turn["id"] == row["attempt_id"])
        assert snapshot == {"arguments": turn["arguments"], "result": turn["result"]}
    persisted = store.get_work_order(work.id)
    assert persisted is not None
    assert [turn.model_dump(mode="json") for turn in persisted.specialist_turns] == original_turns


def test_scope_defaults_preserve_future_fields_and_do_not_mutate_request() -> None:
    omitted: dict[str, Any] = {"narrative_context": {"scene_id": "scene"}, "future_scope": {"selected": ["exact"]}}
    explicit = deepcopy(omitted)
    explicit["narrative_context"]["dialogue_line_ids"] = []
    explicit["implementation_preferences"] = []
    before = deepcopy(explicit)
    assert asset_inspection_scope(omitted) == asset_inspection_scope(explicit)
    assert asset_inspection_scope({}) == asset_inspection_scope({"narrative_context": None, "implementation_preferences": []})
    assert asset_inspection_scope(explicit)["future_scope"] == {"selected": ["exact"]}
    assert explicit == before
