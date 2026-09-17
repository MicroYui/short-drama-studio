"""Returned media makes its contracts available without another discovery call."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.media_tool_schemas import media_tool_schema_ids
from short_drama.agent_runtime.models import ArtifactRef, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry, runtime_work


@pytest.mark.parametrize("case", ["none", "queued", "returned", "failed", "foreign", "take_scope", "text_only"])
def test_media_contracts_follow_persisted_objects_not_goal_words(tmp_path: Path, case: str) -> None:
    _store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work = type(work).model_validate({**work.model_dump(), "context_manifest": None,
        "objective": "Generate and inspect a video, preserve original audio and select a Take."})
    if case == "take_scope":
        work = type(work).model_validate({**work.model_dump(), "context_manifest": None,
            "scope": [ArtifactRef(project_id=context.project_id, artifact_type="take", artifact_id="selected-take")]})
    elif case not in {"none", "text_only"}:
        pid = "foreign-project" if case == "foreign" else context.project_id
        work.specialist_turns = [SpecialistTurnRecord(id="prepare", sequence=1,
            tool_name="route.prepare_h3_group", reason="Prepare selected group", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "FAILED" if case == "failed" else "SUCCEEDED", "artifact_refs": [
                {"project_id": pid, "artifact_type": "h3_group_operation", "artifact_id": "group-op"},
                {"project_id": pid, "artifact_type": "generation_task", "artifact_id": "task-video"}],
                "output": {"project_id": pid, "task_statuses": {"task-video": "SUCCEEDED" if case == "returned" else "QUEUED"}}})]
    loaded = media_tool_schema_ids(work, context.project_id)
    if case in {"none", "failed", "foreign", "text_only"}:
        assert loaded == []
    elif case == "queued":
        assert set(loaded) == {"route.get_h3_group_operation", "task.get", "task.advance"}
    else:
        assert {"video.inspect_take", "audio.transcribe_native", "route.review_h3_take", "video.select_take"} <= set(loaded)
        assert "audio.inspect_manifest" not in loaded  # A Take does not imply a film review.


def test_preload_is_one_model_decision_and_survives_reopen_without_running_tools(tmp_path: Path) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work = type(work).model_validate({**work.model_dump(), "context_manifest": None,
        "scope": [ArtifactRef(project_id=context.project_id, artifact_type="take", artifact_id="selected-take")]})
    store.save_work_order(work)
    tools = registry()
    calls: list[str] = []

    def unused(_request: ReadInput) -> ToolExecution[ReadOutput]:
        calls.append("unexpected")
        return ToolExecution(output=ReadOutput(status="available"))

    for tool_id in ("video.inspect_take", "audio.transcribe_native", "route.review_h3_take", "video.select_take"):
        tools.register(ToolSpec(id=tool_id, version=1, category=ToolCategory(tool_id.split(".")[0]),
            description="Current media contract", effect=ToolEffect.READ, idempotent=True,
            input_model=ReadInput, output_model=ReadOutput, handler=unused))
    request = json.dumps({"tool_name": "video.inspect_take", "arguments": {"project_id": context.project_id}, "reason": "Observe current candidate"})
    client = ScriptedClient([request])
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(store)
    worker.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Use current sources."))
    assert len(client.payloads) == 1 and calls == []
    assert "video.select_take" in client.payloads[0]["tool_schemas"]
    reopened = AgentRuntimeStore(store.database_path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None and "audio.transcribe_native" in saved.loaded_tool_schema_ids
    resumed_client = ScriptedClient([request])
    resumed = LanguageSpecialistAgent(resumed_client, tools)
    resumed.context_compiler = ModelInputCompiler(reopened)
    resumed.execute(AgentWorkOrderView(work_order=saved, context=context, system_prompt="Use current sources."))
    assert len(resumed_client.payloads) == 1 and calls == []
    assert resumed_client.payloads[0]["tool_schemas"] == client.payloads[0]["tool_schemas"]
