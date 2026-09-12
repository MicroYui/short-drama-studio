"""A completed masked edit retains both precise choices across the Worker handoff."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.image_models import ImageTaskResponse, ImageTaskStatus
from short_drama.production_manager import (
    AgentWorkOrderView,
    LanguageSpecialistAgent,
    _specialist_tool_result_for_persistence,
)
from short_drama.production_profiles import ProductionSkillRole
from short_drama.tools import ToolCallStatus, ToolResult
from tests.test_harness_tool_context import ScriptedClient
from tests.test_image_edit_results import RecordingProvider, _queued


def _turn(tool: str, arguments: dict[str, JsonValue], result: ToolResult, sequence: int) -> SpecialistTurnRecord:
    serialized = result.model_dump(mode="json")
    original = deepcopy(serialized)
    persisted = _specialist_tool_result_for_persistence(tool, serialized, arguments=arguments)
    assert serialized == original
    assert {key: value for key, value in persisted.items() if key != "output"} == {
        key: value for key, value in original.items() if key != "output"
    }
    # Persistence intentionally summarizes the convenience ProjectAggregate;
    # the actual task, receipt and precise references remain unchanged.
    assert {key: value for key, value in cast(dict[str, JsonValue], persisted["output"]).items() if key != "project"} == {
        key: value for key, value in result.output.items() if key != "project"
    }
    return SpecialistTurnRecord(
        id=f"actual-candidate-{sequence}", sequence=sequence, tool_name=tool,
        arguments=arguments, reason="Continue from the actual completed image task.",
        status=SpecialistTurnStatus.COMPLETED, result=persisted,
    )


@pytest.mark.parametrize("after_other_tool", [False, True])
def test_actual_completed_candidates_reach_restarted_worker_without_changing_default(
    tmp_path: Path, after_other_tool: bool,
) -> None:
    provider = RecordingProvider()
    service, asset, _base, _mask, queued = _queued(tmp_path, provider)
    arguments: dict[str, JsonValue] = {"task_id": queued.id}
    result = service.tool_registry.invoke("image.advance", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    task = ImageTaskResponse.model_validate(result.output).task
    assert task.status == ImageTaskStatus.SUCCEEDED
    assert task.provider_output_version_id is not None
    assert task.provider_output_version_number is not None
    expected_refs = {
        (asset.project_id, "asset_version", task.output_version_id, task.output_version_number),
        (asset.project_id, "asset_version", task.provider_output_version_id, task.provider_output_version_number),
    }
    assert {ref.identity() for ref in result.artifact_refs if ref.artifact_type == "asset_version"} == expected_refs
    project_before = service.get_project(asset.project_id).document
    selected = next(item for item in project_before.assets if item.id == asset.id)
    assert selected.current_version_id == selected.versions[-1].id == task.output_version_id
    assert max(selected.versions, key=lambda item: item.version).id == task.output_version_id
    assert task.provider_output_version_id != task.output_version_id

    store = AgentRuntimeStore(tmp_path / "worker.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id=asset.project_id, objective="Prepare the selected image without making video.", root_agent="leader")
    work = runtime.create_work_order(
        run.id, sender="leader", recipient_role="asset_builder",
        objective="Compare the exact saved candidates before choosing one.", why_now="Image completed",
        production_skill_role=ProductionSkillRole.ASSET_BUILDER,
    )
    work.specialist_turns = [_turn("image.advance", arguments, result, 1)]
    if after_other_tool:
        capability = service.tool_registry.invoke("image.capability", {})
        assert capability.status == ToolCallStatus.SUCCEEDED, capability.error
        work.specialist_turns.append(_turn("image.capability", {}, capability, 2))
    work.loaded_tool_schema_ids = ["image.capability"]
    store.save_work_order(work)
    original = work.model_dump(mode="json", exclude={"loaded_tool_schema_ids"})

    reopened = AgentRuntimeStore(store.database_path)
    saved = reopened.get_work_order(work.id)
    assert saved is not None
    client = ScriptedClient([json.dumps({
        "tool_name": "image.capability", "arguments": {},
        "reason": "Both exact candidates are already available; continue the task.",
    })])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(
        work_order=saved,
        context=ContextPack(run_id=run.id, project_id=asset.project_id, objective=work.objective),
        system_prompt="Use actual saved image candidates; neither one is automatically accepted.",
    ))
    assert len(client.payloads) == 1
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    entry = next(item for item in working["entities"] if item["kind"] == "image_task")
    state = entry["state"]
    assert state["id"] == task.id and state["asset_id"] == asset.id
    for field in ("output_version_id", "output_version_number", "provider_output_version_id",
                  "provider_output_version_number", "output_path", "provider_output_path"):
        assert state[field] == task.model_dump(mode="json")[field]
    assert state["status"] == "SUCCEEDED"
    assert {(ref["project_id"], ref["artifact_type"], ref["artifact_id"], ref["version"])
            for ref in working["artifact_refs"] if ref["artifact_type"] == "asset_version"} == expected_refs
    assert working["latest_handoff"]["request"]["tool_name"] == ("image.capability" if after_other_tool else "image.advance")
    persisted = reopened.get_work_order(work.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == original
    saved_result = persisted.specialist_turns[0].result
    assert saved_result is not None
    assert cast(dict[str, JsonValue], saved_result["output"])["task"] == result.output["task"]
    assert saved_result["artifact_refs"] == result.model_dump(mode="json")["artifact_refs"]
    assert service.get_project(asset.project_id).document == project_before
    assert service.repository.get_image_task(task.id) == task
    assert len(provider.calls) == 1
