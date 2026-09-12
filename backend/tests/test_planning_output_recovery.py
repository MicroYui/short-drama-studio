"""Fresh3 output exhaustion: bounded replanning, not truncated-plan execution."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from requests import Session

from short_drama.agent_runtime import ActionKind, ArtifactRef, ContextPack, PlanStep
from short_drama.agent_runtime.execution_control import ExecutionYielded, execution_control
from short_drama.agent_runtime.model_input import ModelInputCompiler, generate_context_managed_text
from short_drama.agent_runtime.models import ArtifactSummary
from short_drama.agent_runtime.planning_output import PLANNING_OUTPUT_TOKENS, planning_response_guidance
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import (
    AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest,
    LanguageProductionPlanner, ProductionManagerError,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderIncompleteError, OpenAICompatibleLanguageClient,
)
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace
from tests.test_harness_tool_context import registry
from tests.test_language_provider import FakeResponse, FakeSession


FIXTURE = Path(__file__).parent / "fixtures/fresh3_planning_output_limit.json"


class PlanningClient:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = iter(outputs)
        self.payloads: list[dict[str, Any]] = []
        self.instructions: list[str] = []
        self.limits: list[int] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        assert not image_urls
        self.payloads.append(json.loads(input_text))
        self.instructions.append(instructions)
        self.limits.append(max_output_tokens)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return value


def interrupted(reason: str = "max_output_tokens") -> LanguageProviderIncompleteError:
    # Historical provider retained only this count, not the actual partial text.
    return LanguageProviderIncompleteError(reason=reason, partial_output_characters=17126)


def recorded_context() -> ContextPack:
    fixture = json.loads(FIXTURE.read_text())
    source = json.loads(fixture["request"]["input_text"])["context"]
    return ContextPack(run_id=source["run_id"], project_id=source["project_id"], objective=source["objective"],
        artifacts=[ArtifactSummary(ref=ArtifactRef.model_validate(ref), summary="Existing exact artifact")
                   for ref in source["artifact_index"].values()])


def compact_plan(context: ContextPack) -> str:
    shots = [ref for ref in context.artifact_index.values() if ref.artifact_type == "shot"][:2]
    return json.dumps({"summary": "Prepare the currently ready selected compositions; retain saved media.",
        "steps": [{"id": f"prepare-{index}", "title": "Prepare selected shot", "kind": "DELEGATION",
            "assigned_agent": f"builder-{index}", "production_skill_role": "ASSET_BUILDER",
            "production_prompt_stage": "ASSETS", "artifact_refs": [ref.model_dump(mode="json")],
            "instructions": "Prepare this shot's composition using existing adopted identities and spatial evidence; stop before video.",
            "arguments": {"why_now": "The current source and reference media are ready.",
                "task_inputs": {"shot_ids": [ref.artifact_id]},
                "acceptance_criteria": ["Save exact usable media references and report remaining limitations."]}}
            for index, ref in enumerate(shots)], "goal_complete": False}, ensure_ascii=False, separators=(",", ":"))


def propose(client: PlanningClient) -> AgentPlanProposal:
    context = recorded_context()
    return LanguageProductionPlanner(client).propose_plan(context=context, skill_prompt="Use the saved scope.",
        method_skills=[], tools=registry().list(), current_plan=None, reason="Continue the same pre-video goal.")


def test_actual_input_replay_keeps_every_fact_and_accepts_compact_reference_plan() -> None:
    fixture = json.loads(FIXTURE.read_text())
    request = fixture["request"]
    original = json.loads(request["input_text"])
    payload = deepcopy(original)
    payload["planning_response"] = planning_response_guidance()
    context = recorded_context()
    response = compact_plan(context)
    client = PlanningClient([response])
    generated = generate_context_managed_text(client, ModelInputCompiler(), run_id=context.run_id,
        project_id=context.project_id, instructions=request["instructions"], payload=payload,
        max_output_tokens=PLANNING_OUTPUT_TOKENS)
    actual = client.payloads[0]
    assert {key: actual[key] for key in original} == original
    assert request["model"] == "gpt-6-astra" and request["reasoning_effort"] == "xhigh"
    assert fixture["terminal_event"]["output_tokens"] == 12000
    assert fixture["terminal_event"]["reasoning_tokens"] == 5140
    proposal = AgentPlanProposal.model_validate_json(generated)
    assert len(proposal.steps) == 2 and not proposal.goal_complete
    assert all(step.artifact_refs and step.depends_on == [] for step in proposal.steps)
    # A deliberately small authored fixture proves contract representability,
    # not that the live model would choose it or that the full goal is complete.
    assert count_text_tokens(response) < 1000
    assert client.limits == [12000]


def test_schema_discovery_then_incomplete_replans_once_without_erasing_current_facts() -> None:
    context = recorded_context()
    client = PlanningClient([json.dumps({"requested_tool_schema_ids": ["project.get"], "rationale": "Inspect exact schema"}),
        interrupted(), compact_plan(context)])
    proposal = propose(client)
    assert len(proposal.steps) == 2
    assert client.limits == [12000, 12000, 12000]
    assert "planning_output_feedback" not in client.payloads[1]
    retry = client.payloads[2]
    assert retry["planning_output_feedback"]["partial_plan_executed"] is False
    assert retry["planning_output_feedback"]["partial_output_characters"] == 17126
    assert retry["context"] == client.payloads[1]["context"]
    assert retry["tool_schemas"] == client.payloads[1]["tool_schemas"]
    assert "project.get" in retry["tool_schemas"]
    assert "Optional fields" in retry["planning_response"]["instruction"]


@pytest.mark.parametrize("with_discovery", [False, True])
def test_repeated_output_exhaustion_is_finite_across_schema_discovery(with_discovery: bool) -> None:
    outputs: list[str | Exception] = [interrupted()]
    if with_discovery:
        outputs.append(json.dumps({"requested_tool_schema_ids": ["project.get"], "rationale": "Read the exact schema"}))
    outputs.append(interrupted())
    client = PlanningClient(outputs)
    with pytest.raises(ProductionManagerError, match="incomplete: max_output_tokens"):
        propose(client)
    assert len(client.payloads) == 2 + int(with_discovery)


@pytest.mark.parametrize("reason", ["content_filter", "unknown"])
def test_other_incomplete_reasons_do_not_use_output_recovery(reason: str) -> None:
    client = PlanningClient([interrupted(reason)])
    with pytest.raises(ProductionManagerError, match=f"incomplete: {reason}"):
        propose(client)
    assert len(client.payloads) == 1


@pytest.mark.parametrize("reason", ["pause requested", "new user message pending"])
def test_control_takes_over_before_output_recovery(reason: str) -> None:
    client = PlanningClient([interrupted(), compact_plan(recorded_context())])
    with execution_control(lambda: reason if client.payloads else None):
        with pytest.raises(ExecutionYielded, match=reason):
            propose(client)
    assert len(client.payloads) == 1


def test_recovery_does_not_bypass_reference_validation() -> None:
    invalid = json.loads(compact_plan(recorded_context()))
    invalid["steps"][0]["artifact_refs"][0]["artifact_id"] = "not-an-existing-shot"
    client = PlanningClient([interrupted(), json.dumps(invalid), json.dumps(invalid)])
    with pytest.raises(ProductionManagerError):
        propose(client)
    assert client.payloads[2]["plan_validation_feedback"]["kind"] == "UNAVAILABLE_ARTIFACT_REF"


@pytest.mark.parametrize("exhausted", [False, True])
def test_real_manager_only_persists_valid_plan_and_keeps_completed_actions(tmp_path: Path, exhausted: bool) -> None:
    response = json.dumps({"summary": "Read actual capability before the next preparation slice.", "steps": [{
        "id": "capability", "title": "Read capability", "kind": "TOOL_CALL", "assigned_agent": "leader",
        "tool_name": "route.h3_capability_profile", "instructions": "Read actual capability, without generating video."}]})
    client = PlanningClient([interrupted(), interrupted() if exhausted else response])
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective="Complete preparation and stop before video.",
        initial_plan=AgentPlanProposal(summary="Existing completed work", steps=[PlanStep(id="saved", title="Saved work",
            kind=ActionKind.CHECKPOINT, assigned_agent="leader", instructions="Retain already saved work.")])))
    run_id = view.snapshot.run.id
    runtime = service.production_manager.runtime
    service.production_manager.advance(run_id, AgentRunAdvanceRequest(max_actions=1), allow_replanning=False)
    before = [a.model_dump(mode="json") for a in runtime.store.list_actions(run_id)]
    if exhausted:
        with pytest.raises(ProductionManagerError, match="max_output_tokens"):
            service.replan_agent_run(run_id, AgentRunReplanRequest(reason="Continue from the completed preparation."))
    else:
        proposal = service.replan_agent_run(run_id, AgentRunReplanRequest(reason="Continue from the completed preparation."))
        assert proposal.snapshot.current_plan is not None and proposal.snapshot.current_plan.revision == 2
    assert [a.model_dump(mode="json") for a in runtime.store.list_actions(run_id)] == before
    assert len(runtime.store.list_plan_revisions(run_id)) == (1 if exhausted else 2)
    assert runtime.store.list_work_orders(run_id) == []  # Replanning never executed either plan's Tools.
    assert client.payloads[0]["goal"] == client.payloads[1]["goal"]
    assert len(client.payloads) == 2


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("terminal_only", [False, True])
def test_incomplete_adapter_trace_keeps_actual_text_but_never_returns_a_plan(
    tmp_path: Path, streaming: bool, terminal_only: bool,
) -> None:
    partial = '{"summary":"unfinished","steps":[{"id":"unsafe-to-execute"'
    terminal = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "output_text": partial, "usage": {"input_tokens": 40, "output_tokens": 12000, "total_tokens": 12040}}
    if terminal_only:
        terminal.pop("output_text")
        terminal["output"] = [{"type": "message", "content": [{"type": "output_text", "text": partial}]}]
    deltas = [] if terminal_only else ["data: " + json.dumps({"type": "response.output_text.delta", "delta": partial}), ""]
    response = FakeResponse(200, terminal, headers={"Content-Type": "text/event-stream"} if streaming else {},
        stream_lines=[*deltas,
            "data: " + json.dumps({"type": "response.incomplete", "response": terminal}), ""] if streaming else None)
    session = FakeSession(response)
    client = OpenAICompatibleLanguageClient(api_key="", base_url="http://localhost:4141/v1", session=cast(Session, session))
    events: list[str] = []
    with production_trace(tmp_path / "trace", lambda kind, _value: events.append(kind)):
        with pytest.raises(LanguageProviderIncompleteError) as raised:
            client.generate_text(instructions="Return a complete plan.", input_text="Continue preparation.", max_output_tokens=12000)
    assert raised.value.partial_output_text == partial
    traces = [json.loads(p.read_text()) for p in (tmp_path / "trace").glob("*.json")]
    incomplete = next(t["data"] for t in traces if t["kind"] == "model.incomplete")
    assert incomplete["text"] == partial and incomplete["saved"] is False
    assert not any(t["kind"] == "model.output" for t in traces)
    assert "model.incomplete" not in events  # Full raw text stays in Trace, not duplicated into UI events.
    assert len(session.post_calls) == 1
    request = session.post_calls[0][1]["json"]
    assert request["model"] == "gpt-6-astra" and request["reasoning"] == {"effort": "xhigh"}


def test_completed_response_keeps_existing_text_part_whitespace_contract() -> None:
    assert OpenAICompatibleLanguageClient._extract_output_text_payload({"status": "completed", "output": [
        {"content": [{"type": "output_text", "text": "  first  "},
                     {"type": "output_text", "text": " \n "},
                     {"type": "output_text", "text": "  second\n"}]}]}) == "first\nsecond"
