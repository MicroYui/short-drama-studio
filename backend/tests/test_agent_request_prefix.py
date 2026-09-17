"""Stable public contracts precede changing state in actual language requests."""

from copy import deepcopy
import json
from os.path import commonprefix
from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime.model_input import ModelInputCompiler, json_text
from short_drama.production_manager import (
    AgentWorkOrderView, LanguageProductionPlanner, LanguageSpecialistAgent, ordered_agent_model_payload,
)
from tests.test_harness_tool_context import ScriptedClient, registry, runtime_work


class CapturedClient(ScriptedClient):
    def __init__(self, outputs: list[str | Exception]) -> None:
        super().__init__(outputs)
        self.serialized: list[str] = []

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        self.serialized.append(input_text)
        return super().generate_text(instructions=instructions, input_text=input_text,
                                     image_urls=image_urls, max_output_tokens=max_output_tokens)


def test_top_level_order_preserves_every_value_and_nested_order() -> None:
    original: dict[str, JsonValue] = {
        "context": {"last": 3, "first": 1}, "unknown_future_field": [3, 2, 1],
        "goal": {"original_request": "保留原声", "latest_user_instructions": "只改03"},
        "response_schemas": {"z": {}, "a": {}}, "working_set": {"complete_result": [1, 2]},
        "tool_schemas": {"z.read": {"description": "z"}, "a.read": {"description": "a"}},
        "available_tools": [{"id": "z.read"}, {"id": "a.read"}], "feedback": None,
    }
    before = deepcopy(original)
    result = ordered_agent_model_payload(original)
    assert result == before and original == before
    assert list(result) == ["available_tools", "response_schemas", "tool_schemas",
                            "context", "unknown_future_field", "goal", "working_set", "feedback"]
    for key in original:
        assert json_text(result[key]) == json_text(original[key])


@pytest.mark.parametrize("role", ["worker", "leader"])
def test_actual_request_prefix_is_stable_across_dynamic_turns(tmp_path: Path, role: str) -> None:
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    response = {"status": "COMPLETED", "concise_summary": "Done."} if role == "worker" else {
        "summary": "Requested review completed.", "goal_complete": True, "steps": []}
    client = CapturedClient([json.dumps(response), json.dumps(response)])
    tools = registry()
    worker = LanguageSpecialistAgent(client, tools)
    worker.context_compiler = ModelInputCompiler(store)
    planner = LanguageProductionPlanner(client)
    planner.context_compiler = ModelInputCompiler(store)
    for marker in ("first-current-state", "second-current-state"):
        current = context.model_copy(update={"objective": marker})
        if role == "worker":
            worker.execute(AgentWorkOrderView(work_order=work.model_copy(update={"working_notes": marker}),
                                             context=current, system_prompt="Review the current facts."))
        else:
            planner.propose_plan(context=current, skill_prompt="", method_skills=[], tools=tools.list(),
                                 current_plan=None, reason=marker)
    first, second = client.payloads
    stable = [key for key in ("available_tools", "available_production_methods", "planning_response",
                             "output_contracts", "response_schemas", "tool_schemas") if key in first]
    assert list(first)[:len(stable)] == stable and list(second)[:len(stable)] == stable
    assert {key: first[key] for key in stable} == {key: second[key] for key in stable}
    assert first["context"] != second["context"]
    assert cast(dict[str, JsonValue], second["context"])["objective"] == "second-current-state"
    expected_prefix = json_text({key: first[key] for key in stable})[:-1]
    actual_prefix = commonprefix(client.serialized)
    assert actual_prefix.startswith(expected_prefix)
    dynamic_first = "work_order" if role == "worker" else "reason"
    legacy = [json_text({dynamic_first: item[dynamic_first], **{
        key: value for key, value in item.items() if key != dynamic_first}}) for item in (first, second)]
    assert len(actual_prefix) > len(commonprefix(legacy)) + 1000
    assert all(json.loads(text) == payload for text, payload in zip(client.serialized, client.payloads, strict=True))
    print(f"{role}: unchanged-values=true stable-contract-prefix={len(expected_prefix)} characters; "
          f"captured common prefix={len(actual_prefix)} versus dynamic-first={len(commonprefix(legacy))}. "
          "Provider cache usage was not measured.")
