"""The four real S04 failures retain enough relation data to fix one field."""

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.tools.contracts import ToolCategory, ToolEffect, ToolExecution, ToolSpec
from tests.test_harness_tool_context import ReadInput, ReadOutput, ScriptedClient, registry, runtime_work


def test_real_failed_relations_reach_restarted_worker_without_replaying_whole_shot(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/fresh2_shot_media_registration.json").read_text())
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    raw_turns = fixture["turns"]
    for raw in raw_turns[:4]:
        turn = SpecialistTurnRecord.model_validate({**raw, "reason": "Save the selected image and prose bindings",
            "status": "COMPLETED"})
        turn.arguments["project_id"] = context.project_id
        before = turn.model_dump(mode="json")
        work.specialist_turns = [turn]
        store.save_work_order(work)
        # The actual registry classifies the existing save tool as WRITE; this
        # context-only replay needs the same effect, with no media/API call.
        tools = registry()
        tools.register(ToolSpec(id="shot.save_current", version=1, category=ToolCategory.SHOT,
            description="Save current shot", effect=ToolEffect.WRITE, idempotent=True,
            input_model=ReadInput, output_model=ReadOutput,
            handler=lambda _request: ToolExecution(output=ReadOutput(status="unused"))))
        client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current",
            "arguments": {"project_id": context.project_id}, "reason": "Context replay only"})])
        agent = LanguageSpecialistAgent(client, tools)
        reopened = type(store)(tmp_path / "agent.sqlite3")
        agent.context_compiler = ModelInputCompiler(reopened)
        restarted = reopened.get_work_order(work.id)
        assert restarted is not None
        agent.execute(AgentWorkOrderView(work_order=restarted, context=context, system_prompt="Repair the reported relation only."))
        payload = cast(dict[str, JsonValue], client.payloads[0]["working_set"])
        latest = cast(dict[str, JsonValue], payload["latest_handoff"])
        relations = cast(dict[str, JsonValue], latest["attempted_media_relations"])
        args = cast(dict[str, JsonValue], raw["arguments"])
        registration = cast(dict[str, JsonValue], args["media_registration"])
        entries = cast(list[dict[str, JsonValue]], registration["reference_entries"])
        projected = cast(list[dict[str, JsonValue]], relations["reference_entries"])
        assert projected == [{"request_index": index, **{key: entry[key] for key in (
            "subject_asset_id", "asset_version_id", "role", "lineage_root_version_id") if key in entry}}
            for index, entry in enumerate(entries)]
        assert "not saved content" in cast(str, relations["note"])
        assert raw["arguments"]["request"]["shot"]["visual"] not in json.dumps(relations, ensure_ascii=False)
        request = cast(dict[str, JsonValue], latest["request"])
        assert "timeline_beats" not in cast(dict[str, JsonValue], request["arguments"])
        # Latest failure is deliberately not duplicated in pending repairs.
        assert payload["pending_tool_repairs"] == []
        work.specialist_turns.append(SpecialistTurnRecord(id=f"after-{turn.id}", sequence=turn.sequence + 1,
            tool_name="artifact.read_current", arguments={"project_id": context.project_id},
            reason="Resolve current ownership", status=SpecialistTurnStatus.COMPLETED,
            result={"status": "SUCCEEDED", "output": {"marker": "current index"}}))
        continued = _specialist_working_set(work, [], project_id=context.project_id,
            write_tool_ids=frozenset({"shot.save_current"}))
        assert continued.pending_tool_repairs[0].attempted_media_relations == relations
        assert turn.model_dump(mode="json") == before
    # Successful save carries actual saved content, not an old failed request.
    raw = raw_turns[4]
    work.specialist_turns = [SpecialistTurnRecord.model_validate({**raw,
        "reason": "Save the now-valid relations", "status": SpecialistTurnStatus.COMPLETED})]
    state = _specialist_working_set(work, [], project_id=context.project_id,
        write_tool_ids=frozenset({"shot.save_current"}))
    assert state.latest_handoff is not None
    assert "attempted_media_relations" not in state.latest_handoff
    assert state.pending_tool_repairs == []
