"""Film 1: a real failed attempt lost required turn_status during delegation."""

from __future__ import annotations

import json
from typing import Any

import pytest

from short_drama.agent_runtime import ContextPack, SpecialistTurnStatus, ToolAttemptRef
from short_drama.production_manager import (
    AgentPlanProposal, LanguageProductionPlanner, ProductionManagerError,
)
from tests.test_harness_tool_context import ScriptedClient


def _context() -> ContextPack:
    # Keep the incident's exact identities; media, Tool payloads and Providers are
    # unnecessary to reproduce the planner's nested output contract failure.
    selected = ToolAttemptRef(
        project_id="project-72ba2d9e4a2f",
        agent_run_id="agent-run-891e5b638dab41e683021f4411fbc197",
        work_order_id="work-order-62a3b2d424f940948d153b1584adf4b2",
        attempt_id="specialist-turn-3a011c0b33974b328595933f1c24b99e",
        tool_name="video.select_take",
        turn_status=SpecialistTurnStatus.COMPLETED,
        outcome_status="FAILED", has_arguments=True, has_result=True,
    )
    return ContextPack(
        run_id=selected.agent_run_id, project_id=selected.project_id,
        objective="Continue editing from the saved failed attempt without new media.",
        tool_attempt_refs=[
            selected.model_copy(update={"attempt_id": "unrelated-old-attempt"}),
            selected,
            selected.model_copy(update={"attempt_id": "another-unrelated-attempt"}),
        ],
    )


def _plan(refs: Any) -> str:
    return json.dumps({
        "summary": "Hand the exact failed operation to a focused editing Worker.",
        "steps": [{
            "id": "repair", "title": "Finish saved editing", "kind": "DELEGATION",
            "assigned_agent": "editor", "production_skill_role": "DIRECTOR",
            "production_prompt_stage": "POST",
            "instructions": "Read the saved failed request before choosing the local repair.",
            "arguments": {"tool_attempt_refs": refs},
        }],
    })


def _propose(client: ScriptedClient, context: ContextPack) -> AgentPlanProposal:
    return LanguageProductionPlanner(client).propose_plan(
        context=context, skill_prompt="Use the saved scope.", method_skills=[],
        tools=[], current_plan=None, reason="The existing Worker handed back a failed attempt.",
    )


def _payload(client: ScriptedClient, index: int) -> dict[str, Any]:
    return json.loads(json.dumps(client.payloads[index]))


@pytest.mark.parametrize("invalid_state", [None, "FAILED"])
def test_known_failed_attempt_can_correct_missing_or_confused_lifecycle(invalid_state: str | None) -> None:
    context = _context()
    before = context.model_dump(mode="json")
    complete = context.tool_attempt_refs[1].model_dump(mode="json")
    # The actual two rejected Leader responses copied these five fields only.
    authored = {key: complete[key] for key in (
        "project_id", "agent_run_id", "work_order_id", "attempt_id", "tool_name",
    )}
    if invalid_state is not None:
        authored["turn_status"] = invalid_state
    other = context.tool_attempt_refs[0].model_dump(mode="json")
    client = ScriptedClient([_plan([other, authored]), _plan([other, complete])])

    accepted = _propose(client, context)

    assert len(client.payloads) == 2
    first, correction = _payload(client, 0), _payload(client, 1)
    schema = first["output_contracts"]["tool_attempt_ref"]
    assert "turn_status" in schema["required"]
    assert schema["$defs"]["SpecialistTurnStatus"]["enum"] == [
        "REQUESTED", "IN_PROGRESS", "COMPLETED", "INTERRUPTED",
    ]
    feedback = correction["plan_validation_feedback"]
    assert feedback["kind"] == "INVALID_TOOL_ATTEMPT_REF_CONTRACT"
    issue = feedback["issues"][0]
    assert issue["loc"] == ["steps", 0, "arguments", "tool_attempt_refs", 1, "turn_status"]
    assert issue["type"] == ("missing" if invalid_state is None else "enum")
    assert feedback["matching_tool_attempt_refs"] == [complete]
    assert "adjacent_boundaries" not in feedback["instruction"]
    assert "outcome_status FAILED" in feedback["instruction"]
    assert accepted.steps[0].arguments["tool_attempt_refs"] == [other, complete]
    assert complete["turn_status"] == "COMPLETED" and complete["outcome_status"] == "FAILED"
    assert context.model_dump(mode="json") == before


@pytest.mark.parametrize("field", ["project_id", "agent_run_id", "work_order_id", "attempt_id", "tool_name"])
def test_complete_reference_with_wrong_identity_or_tool_still_fails(field: str) -> None:
    context = _context()
    before = context.model_dump(mode="json")
    invalid = context.tool_attempt_refs[1].model_dump(mode="json")
    invalid[field] = "not-the-current-" + field
    client = ScriptedClient([_plan([invalid]), _plan([invalid])])

    with pytest.raises(ProductionManagerError, match="repeated unresolved context references"):
        _propose(client, context)

    assert len(client.payloads) == 2
    feedback = _payload(client, 1)["plan_validation_feedback"]
    assert feedback["kind"] == "UNAVAILABLE_TOOL_ATTEMPT_REF"
    assert feedback["issues"] == [{
        "path": "steps[0].arguments.tool_attempt_refs[0]", "received": invalid,
    }]
    assert context.model_dump(mode="json") == before


def test_unknown_incomplete_reference_gets_no_guessed_correction_and_stops() -> None:
    context = _context()
    invalid = context.tool_attempt_refs[1].model_dump(mode="json")
    invalid.pop("turn_status")
    invalid["attempt_id"] = "invented-attempt"
    client = ScriptedClient([_plan([invalid]), _plan([invalid])])

    with pytest.raises(ProductionManagerError, match="turn_status"):
        _propose(client, context)

    assert len(client.payloads) == 2
    feedback = _payload(client, 1)["plan_validation_feedback"]
    assert feedback["matching_tool_attempt_refs"] == []
    assert feedback["issues"][0]["type"] == "missing"


def test_correction_of_one_ref_does_not_hide_progress_on_the_next_ref() -> None:
    context = _context()
    first, second = [ref.model_dump(mode="json") for ref in context.tool_attempt_refs[:2]]
    incomplete_first = {key: value for key, value in first.items() if key != "turn_status"}
    incomplete_second = {key: value for key, value in second.items() if key != "turn_status"}
    client = ScriptedClient([
        _plan([incomplete_first, incomplete_second]),
        _plan([first, incomplete_second]),
        _plan([first, second]),
    ])

    accepted = _propose(client, context)

    assert len(client.payloads) == 3
    for call_index, ref_index, ref in [(1, 0, first), (2, 1, second)]:
        feedback = _payload(client, call_index)["plan_validation_feedback"]
        assert feedback["issues"][0]["loc"][-2:] == [ref_index, "turn_status"]
        assert feedback["matching_tool_attempt_refs"] == [ref]
    assert accepted.steps[0].arguments["tool_attempt_refs"] == [first, second]


@pytest.mark.parametrize("invalid,location,issue_type", [
    (None, ["steps", 0, "arguments", "tool_attempt_refs"], "list_type"),
    ({"attempt_id": "wrong-wrapper"}, ["steps", 0, "arguments", "tool_attempt_refs"], "list_type"),
    (["wrong-string-entry"], ["steps", 0, "arguments", "tool_attempt_refs", 0], "model_type"),
])
def test_invalid_reference_container_has_actionable_location(
    invalid: Any, location: list[str | int], issue_type: str,
) -> None:
    client = ScriptedClient([_plan(invalid), _plan([])])
    accepted = _propose(client, _context())
    feedback = _payload(client, 1)["plan_validation_feedback"]
    assert feedback["kind"] == "INVALID_TOOL_ATTEMPT_REF_CONTRACT"
    assert feedback["issues"][0]["loc"] == location
    assert feedback["issues"][0]["type"] == issue_type
    assert feedback["matching_tool_attempt_refs"] == []
    assert accepted.steps[0].arguments["tool_attempt_refs"] == []
