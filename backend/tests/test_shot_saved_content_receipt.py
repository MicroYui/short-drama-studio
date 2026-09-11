"""An EDITORIAL binding must remain visible after the successful Shot write."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image
from pydantic import JsonValue

from short_drama.agent_runtime import AgentRuntimeStore, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.image_models import AssetImportLocalRequest
from short_drama.models import AssetKind, ShotPlan, ShotReferenceCue, ShotTimelineBeat
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.production_profiles import ProductionSkillRole
from short_drama.service import ShortDramaService
from short_drama.shot_write_receipts import summarize_saved_shot
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.tools.contracts import ToolCallStatus, ToolResult
from tests.test_harness_tool_context import ScriptedClient, runtime_work


REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/shot_editorial_save_receipt.json").read_text())
READS = frozenset({"artifact.read_current", "project.get", "artifact.get_asset_work_context"})
WRITES = frozenset({"shot.save_current"})


@pytest.fixture
def saved_case(tmp_path: Path) -> tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult]:
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "work")
    doc = service.initialize_fixture().document
    asset = next(item for item in doc.assets if item.kind == AssetKind.CHARACTER)
    image = tmp_path / "evidence.png"
    Image.new("RGB", (120, 180), "#adbfc6").save(image)
    candidate = service.import_local_asset_version(doc.project.id, asset.id,
        AssetImportLocalRequest(local_path=str(image), descriptor="An exact saved editorial image.")).version
    shot = doc.shots[0]
    cue = ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=candidate.id,
        alias="报价单", field="visual", text_start=3, text_end=6,
        purpose="Use the actual static evidence in editing.", usage="EDITORIAL", image_reference_role="COMPOSITION")
    changed = shot.model_copy(deep=True, update={"visual": "证据📄报价单", "references": [cue],
        "timeline_beats": [ShotTimelineBeat(start_seconds=0, end_seconds=shot.duration_seconds,
            visual="证据📄报价单", framing="Full frame", camera="Static", action="Hold the saved image.",
            dialogue_line_ids=shot.dialogue_line_ids, sound="Preserve the established ongoing dialogue.",
            references=[cue.model_copy(deep=True)])]})
    arguments: dict[str, JsonValue] = {"project_id": doc.project.id, "shot_id": shot.id,
        "request": {"expected_revision": shot.revision, "shot": changed.model_dump(mode="json")}}
    result = service.tool_registry.invoke("shot.save_current", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    saved = next(item for item in service.get_project(doc.project.id).document.shots if item.id == shot.id)
    return service, saved, arguments, result


def _turn(arguments: dict[str, JsonValue], result: ToolResult, sequence: int = 1) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"save-{sequence}", sequence=sequence, tool_name="shot.save_current",
        arguments=arguments, reason="Save the selected image binding.", status=SpecialistTurnStatus.COMPLETED,
        result=result.model_dump(mode="json"))


def _read(sequence: int = 2) -> SpecialistTurnRecord:
    return SpecialistTurnRecord(id=f"read-{sequence}", sequence=sequence, tool_name="project.get",
        arguments={"project_id": "test"}, reason="Read an unrelated small fact.",
        status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": {"status": "available"}})


def _summaries(work: WorkOrder) -> dict[str, dict[str, JsonValue]]:
    state = _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES)
    return {entity.id: entity.state for entity in state.entities if entity.kind == "saved_shot"}


def test_real_editorial_case_preserves_the_saved_occurrences_and_spoken_coverage() -> None:
    write, read = REAL["turns"]
    shot = ShotPlan.model_validate(read["result"]["output"]["content"]["shot"])
    assert write["result"]["output"]["reference_plan_preview"]["selected"] == []
    assert write["result"]["output"]["reference_plan_preview"]["complexity"]["action_beat_count"] == 0
    summary = summarize_saved_shot(shot)
    assert summary.revision == 3 and summary.duration_seconds == 1.5
    assert summary.dialogue_line_ids == ["line-yqq-02"]
    assert len(summary.beats) == 1 and summary.beats[0].dialogue_line_ids == ["line-yqq-02"]
    assert summary.audio_intent == shot.audio_intent
    assert [item.cue for item in summary.shot_cues] == [*shot.references, *shot.timeline_beats[0].references]
    assert [item.scope for item in summary.shot_cues] == ["SHOT", "BEAT"]
    assert {item.cue.usage for item in summary.shot_cues} == {"EDITORIAL"}
    assert {item.cue.asset_version_id for item in summary.shot_cues} == {"asset-version-9ed9636918b5"}
    assert all(item.time_range.start_seconds == 0 and item.time_range.end_seconds == 1.5 for item in summary.shot_cues)
    # The exact source offsets, not the alias length or UTF-16 code units.
    assert all(item.referenced_text == shot.visual[item.cue.text_start:item.cue.text_end]
        for item in summary.shot_cues)


def test_actual_save_receipt_contains_saved_cues_but_generation_preview_does_not(
    saved_case: tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult],
) -> None:
    service, shot, arguments, result = saved_case
    assert result.output is not None and result.receipt is not None
    expected = summarize_saved_shot(shot).model_dump(mode="json")
    assert result.output["saved_shot"] == result.receipt.current_state["saved_shot"] == expected
    assert result.output["reference_plan_preview_scope"] == result.receipt.current_state["reference_plan_preview_scope"] == "GENERATION_ONLY"
    assert {item["cue"]["id"] for item in expected["shot_cues"]} == {
        f"{shot.id}-shot-ref-0", f"{shot.id}-beat-0-ref-0"}
    assert {item["referenced_text"] for item in expected["shot_cues"]} == {"报价单"}
    selected = cast(dict[str, Any], result.output["reference_plan_preview"])["selected"]
    assert shot.references[0].asset_version_id not in [item["asset_version_id"] for item in selected]
    assert "visual" not in expected and "narrative_purpose" not in expected and "continuity" not in expected
    before = service.get_project(shot.project_id).document
    repeated = service.tool_registry.invoke("shot.save_current", arguments)
    assert repeated.status == ToolCallStatus.FAILED
    assert service.get_project(shot.project_id).document == before


def test_mixed_usage_keeps_editorial_occurrences_out_of_generation_bindings(
    tmp_path: Path, saved_case: tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult],
) -> None:
    service, shot, _arguments, _result = saved_case
    path = tmp_path / "generation.png"
    Image.new("RGB", (120, 180), "#cabeb0").save(path)
    generation = service.import_local_asset_version(shot.project_id, shot.references[0].subject_asset_id,
        AssetImportLocalRequest(local_path=str(path), descriptor="Current generation composition.")).version
    changed = shot.model_copy(deep=True)
    changed.references.append(ShotReferenceCue(subject_asset_id=shot.references[0].subject_asset_id,
        asset_version_id=generation.id, alias="生成构图", purpose="Use for generation composition.",
        usage="GENERATION", image_reference_role="COMPOSITION"))
    result = service.tool_registry.invoke("shot.save_current", {"project_id": shot.project_id, "shot_id": shot.id,
        "request": {"expected_revision": shot.revision, "shot": changed.model_dump(mode="json")}})
    assert result.status == ToolCallStatus.SUCCEEDED and result.output is not None, result.error
    summary = cast(dict[str, Any], result.output["saved_shot"])
    assert [item["cue"]["usage"] for item in summary["shot_cues"]] == ["EDITORIAL", "GENERATION", "EDITORIAL"]
    selected = cast(dict[str, Any], result.output["reference_plan_preview"])["selected"]
    assert generation.id in [item["asset_version_id"] for item in selected]
    assert shot.references[0].asset_version_id not in [item["asset_version_id"] for item in selected]
    document = service.get_project(shot.project_id).document
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    assert [item.target_id for item in plan.bindings] == [generation.id]


@pytest.mark.parametrize("after_other_tool", [False, True])
def test_actual_receipt_reaches_restarted_worker_once_and_survives_other_tool(
    tmp_path: Path, saved_case: tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult], after_other_tool: bool,
) -> None:
    service, shot, arguments, result = saved_case
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = shot.project_id
    work.production_skill_role = ProductionSkillRole.ASSET_BUILDER
    work.specialist_turns = [_turn(arguments, result)]
    if after_other_tool:
        work.specialist_turns.append(_read())
    work.loaded_tool_schema_ids = ["artifact.read_current"]
    store.save_work_order(work)
    original = work.model_dump(mode="json")
    restarted = AgentRuntimeStore(store.database_path)
    resumed = restarted.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "artifact.read_current", "reason": "Continue from the saved receipt.",
        "arguments": {"project_id": shot.project_id, "shot_id": service.get_project(shot.project_id).document.shots[1].id}})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(restarted)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Prepare selected images."))
    assert len(client.payloads) == 1
    working = cast(dict[str, Any], client.payloads[0]["working_set"])
    summaries = [item["state"] for item in working["entities"] if item["kind"] == "saved_shot"]
    expected = summarize_saved_shot(shot).model_dump(mode="json")
    if after_other_tool:
        assert summaries == [expected]
    else:
        assert summaries == []
        latest = working["latest_handoff"]["result"]
        assert "output" not in latest
        assert latest["receipt"]["current_state"]["saved_shot"] == expected
    preview = next(item["state"] for item in working["entities"] if item["kind"] == "reference_plan_preview")
    assert preview["scope"] == "GENERATION_ONLY"
    assert not any(item["kind"] == "shot" and item["id"] == shot.id for item in working["entities"])
    persisted = restarted.get_work_order(work.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json", exclude={"loaded_tool_schema_ids"}) == {
        key: value for key, value in original.items() if key != "loaded_tool_schema_ids"}


@pytest.mark.parametrize("change", ["empty", "old_output", "failed", "newer_read", "same_read", "older_pinned"])
def test_saved_summary_replaces_or_invalidates_only_the_actual_shot(
    tmp_path: Path, saved_case: tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult], change: str,
) -> None:
    _service, shot, arguments, result = saved_case
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    first = _turn(arguments, result)
    other = first.model_copy(deep=True, update={"id": "other-shot-save", "sequence": 2})
    assert other.result is not None
    other_shot = shot.model_copy(deep=True, update={"id": "unrelated-shot"})
    other.result["output"] = {"saved_shot": summarize_saved_shot(other_shot).model_dump(mode="json")}
    other.result["artifact_refs"] = [{"project_id": shot.project_id, "artifact_type": "shot", "artifact_id": other_shot.id, "version": shot.revision}]
    other.result["receipt"] = None
    work.specialist_turns = [first, other]
    if change in {"empty", "old_output", "failed"}:
        final = _turn(arguments, result, 3)
        assert final.result is not None
        newer = shot.model_copy(deep=True, update={"revision": shot.revision + 1, "references": [], "timeline_beats": []})
        final.result["output"] = ({} if change == "old_output" else {"saved_shot": summarize_saved_shot(newer).model_dump(mode="json")})
        final.result["artifact_refs"] = [{"project_id": shot.project_id, "artifact_type": "shot", "artifact_id": shot.id, "version": newer.revision}]
        final.result["receipt"] = None
        if change == "failed":
            final.result["status"] = "FAILED"
    else:
        revision = shot.revision + (1 if change == "newer_read" else -1 if change == "older_pinned" else 0)
        current = shot.model_copy(deep=True, update={"revision": revision})
        final = SpecialistTurnRecord(id="read-shot", sequence=3,
            tool_name="artifact.get_asset_work_context" if change == "older_pinned" else "artifact.read_current",
            arguments={"project_id": shot.project_id, "shot_id": shot.id}, reason="Read a precise Shot.",
            status=SpecialistTurnStatus.COMPLETED, result={"status": "SUCCEEDED", "output": (
                {"storyboard": {"shots": [current.model_dump(mode="json")]}} if change == "older_pinned"
                else {"content": {"shot": current.model_dump(mode="json")}})})
    work.specialist_turns.extend([final, _read(4)])
    summaries = _summaries(work)
    assert summaries[other_shot.id] == summarize_saved_shot(other_shot).model_dump(mode="json")
    if change == "empty":
        assert summaries[shot.id]["shot_cues"] == [] and summaries[shot.id]["revision"] == shot.revision + 1
    elif change in {"failed", "older_pinned"}:
        assert summaries[shot.id] == summarize_saved_shot(shot).model_dump(mode="json")
    else:
        assert shot.id not in summaries


def test_original_trace_cannot_acquire_a_saved_summary_from_request_intent(tmp_path: Path) -> None:
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    work.specialist_turns = [SpecialistTurnRecord.model_validate(deepcopy(REAL["turns"][0])), _read(9)]
    assert _summaries(work) == {}


def test_saved_summary_remains_exactly_readable_after_budget_offload(
    tmp_path: Path, saved_case: tuple[ShortDramaService, ShotPlan, dict[str, JsonValue], ToolResult],
) -> None:
    _service, shot, arguments, result = saved_case
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    work.specialist_turns = [_turn(arguments, result), _read()]
    state = _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode="json")
    prepared = ModelInputCompiler(store, budget=ContextBudget(window_tokens=4000,
        compact_at_tokens=1500, target_tokens=1000, envelope_tokens=100)).prepare(
        run_id=work.parent_run_id, project_id=context.project_id,
        instructions="Use the saved selected Shot.", payload={"working_set": state}, max_output_tokens=100,
        force_compact=True)
    payload = json.loads(prepared.input_text)
    marker = payload["working_set"]["entities"]
    assert isinstance(marker, dict) and "context_ref" in marker
    snapshot = store.get_context_snapshot(marker["context_ref"], work.parent_run_id)
    assert snapshot is not None
    entities = pointer_value(snapshot, marker["pointer"])
    assert isinstance(entities, list)
    assert next(item["state"] for item in entities if isinstance(item, dict) and item.get("kind") == "saved_shot") == summarize_saved_shot(shot).model_dump(mode="json")
    assert prepared.input_tokens == count_text_tokens("Use the saved selected Shot.") + count_text_tokens(prepared.input_text)
