"""Finalization hands over local saved timing/membership without a full read."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntime, AgentRuntimeStore, ContextPack
from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.models import ProductionDocumentOrigin
from short_drama.production_contracts import StoryboardDraftBeginRequest
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _compact_specialist_tool_result
from short_drama.tools import ToolCallStatus
from short_drama.tools.artifact_views import StoryboardMutationSummaryResponse
from tests.test_harness_tool_context import ScriptedClient, registry
from tests.test_storyboard_incremental_service import (
    _append_all_groups,  # pyright: ignore[reportUnknownVariableType] -- existing unannotated test fixture
    _draft,  # pyright: ignore[reportUnknownVariableType] -- existing unannotated test fixture
    _prepare_script_only_project, _seed_from_draft, _service,
)


FIXTURE = Path(__file__).parent / "fixtures" / "fresh4_director_context.json"


def test_real_fresh4_receipt_keeps_old_absence_and_saved_directory_without_prose() -> None:
    fixture = json.loads(FIXTURE.read_text())
    receipt = StoryboardMutationSummaryResponse.model_validate(fixture["finalize_result"]["output"])
    assert receipt.storyboard.shot_directory is None
    assert receipt.storyboard.clip_group_directory is None
    # Exact bounded projections of the six original successful group writes.
    raw = receipt.model_dump(mode="json")
    raw["storyboard"]["shot_directory"] = fixture["saved_shot_directory"]
    raw["storyboard"]["clip_group_directory"] = fixture["saved_group_directory"]
    enriched = StoryboardMutationSummaryResponse.model_validate(raw)
    output = enriched.model_dump(mode="json")
    assert sum(row.duration_seconds for row in enriched.storyboard.shot_directory or []) == 58
    assert [shot for group in enriched.storyboard.clip_group_directory or [] for shot in group.shot_ids] == enriched.storyboard.shots
    result = cast(dict[str, JsonValue], fixture["finalize_result"])
    result["output"] = output
    projected = _compact_specialist_tool_result("shot.finalize_storyboard", result)
    summary = cast(dict[str, JsonValue], cast(dict[str, JsonValue], projected["output"])["storyboard_summary"])
    assert summary["shot_directory"] == fixture["saved_shot_directory"]
    assert summary["clip_group_directory"] == fixture["saved_group_directory"]
    assert count_text_tokens(json_text(output)) - count_text_tokens(json_text(receipt.model_dump(mode="json"))) < 1000
    assert "timeline_beats" not in json_text(output)


def test_actual_finalize_registry_to_next_worker_uses_saved_shots_and_group_members(tmp_path: Path) -> None:
    service = _service(tmp_path)
    document, script, spec, dialogue_shots = _prepare_script_only_project(service)
    draft = _draft(document, script, spec, dialogue_shots=dialogue_shots)
    begun = service.begin_storyboard_draft(document.project.id, StoryboardDraftBeginRequest(
        seed=_seed_from_draft(draft), origin=ProductionDocumentOrigin.AGENT)).progress
    progress = _append_all_groups(service, document=document, script=script, dialogue_shots=dialogue_shots,
        draft_document_id=begun.draft_ref.artifact_id, base_version_id=begun.draft_version_id)
    arguments: dict[str, JsonValue] = {"project_id": document.project.id, "request": {
        "draft_document_id": begun.draft_ref.artifact_id, "draft_version_id": progress.draft_version_id, "origin": "AGENT"}}
    call = service.tool_registry.invoke("shot.finalize_storyboard", arguments)
    assert call.status == ToolCallStatus.SUCCEEDED and call.output is not None, call.error
    receipt = StoryboardMutationSummaryResponse.model_validate(call.output)
    canonical = service.get_project(document.project.id).document
    assert receipt.storyboard.shot_directory is not None
    assert receipt.storyboard.clip_group_directory is not None
    for row in receipt.storyboard.shot_directory:
        shot = next(shot for shot in canonical.shots if shot.id == row.shot_id)
        assert (row.sequence, row.scene_id, row.duration_seconds) == (shot.sequence, shot.scene_id, shot.duration_seconds)
    saved = service.get_storyboard(document.project.id, version_id=receipt.version.id).storyboard
    assert [(row.clip_group_id, row.shot_ids, row.duration_seconds) for row in receipt.storyboard.clip_group_directory] == [
        (group.id, group.shot_ids, group.duration_seconds) for group in saved.clip_groups]

    store = AgentRuntimeStore(tmp_path / "worker.sqlite3")
    runtime = AgentRuntime(store)
    run = runtime.create_run(project_id=document.project.id, objective="Hand off the saved directory", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="director", objective=run.objective, why_now="Storyboard saved")
    context = ContextPack(run_id=run.id, project_id=run.project_id, objective=work.objective)
    work.specialist_turns.append(SpecialistTurnRecord(id="actual-finalize", sequence=1,
        tool_name="shot.finalize_storyboard", arguments=arguments, reason="Save the complete storyboard",
        status=SpecialistTurnStatus.COMPLETED, result=call.model_dump(mode="json")))
    store.save_work_order(work)
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "arguments": {
        "project_id": context.project_id}, "reason": "Read a separate required scope"})])
    agent = LanguageSpecialistAgent(client, registry())
    agent.context_compiler = ModelInputCompiler(store)
    agent.execute(AgentWorkOrderView(work_order=work, context=context, system_prompt="Use saved timing directory."))
    payload = json_text(client.payloads[0])
    assert '"shot_directory"' in payload and '"clip_group_directory"' in payload
    assert json_text(cast(JsonValue, [row.model_dump(mode="json") for row in receipt.storyboard.shot_directory])) in payload
    assert json_text(cast(JsonValue, [row.model_dump(mode="json") for row in receipt.storyboard.clip_group_directory])) in payload
    assert saved.shots[0].visual not in payload
    assert len(client.payloads) == 1
