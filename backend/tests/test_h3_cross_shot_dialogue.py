"""One native utterance can continue across an editorial cut in one H3 request."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.h3_models import H3ProbeRequest, H3ProductionRoutePlanRequest, H3ProviderConfigUpdate
from short_drama.language_models import LanguagePromptCompileRequest
from short_drama.models import ProjectDocument, ProductionDocumentOrigin, ShotPlan, ShotTimelineBeat, VideoGenerationRoute
from short_drama.production_contracts import GenerationUnit, GenerationUnitShotWindow, StoryboardPatchRequest, StoryboardShotSplice
from short_drama.providers.h3_dialogue_spans import generation_unit_dialogue_spans, request_dialogue_spans
from short_drama.providers.h3_context_ir import H3ContextIRPending, compile_h3_context_ir
from short_drama.providers.h3_language_prompt import build_generation_unit_language_prompt_input, build_language_prompt_input
from short_drama.providers.h3_prompt_skill import (
    compile_h3_generation_unit_prompt_ir, compile_h3_prompt_ir,
    validate_h3_generation_unit_prompt_ir, validate_h3_prompt_ir,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from tests.test_h3_dialogue_continuation import _spec
from tests.test_h3_generation_unit_language import _fixture
from tests.test_h3_context_ir import FakeIR, due


def _spoken_shot(template: ShotPlan, identity: str, sequence: int, line_ids: list[str],
                 duration: float, *, start: float = 0.0, end: float | None = None) -> ShotPlan:
    finish = duration if end is None else end
    ranges: list[tuple[float, float, list[str]]] = []
    if start:
        ranges.append((0, start, []))
    ranges.append((start, finish, line_ids))
    if finish < duration:
        ranges.append((finish, duration, []))
    return ShotPlan.model_validate({
        **template.model_dump(), "id": identity, "sequence": sequence, "duration_seconds": duration,
        "dialogue_line_ids": line_ids, "visual": "A person shows a paper.", "camera": "Static.",
        "framing": "Close-up.", "narrative_purpose": "Show the evidence.",
        "audio_intent": "One uninterrupted line over the cut, with room tone.",
        "timeline_beats": [ShotTimelineBeat(start_seconds=a, end_seconds=b, visual="Paper remains visible.",
            framing="Close-up.", camera="Static.", action="Hold the paper.", dialogue_line_ids=ids)
            for a, b, ids in ranges],
    })


@pytest.fixture
def bridge(tmp_path: Path) -> tuple[ProjectDocument, GenerationUnit, list[ShotPlan]]:
    service = ShortDramaService(database_path=tmp_path / "state.db", workspace_root=tmp_path / "work")
    doc = service.initialize_fixture().document
    script = next(s for s in doc.script_versions if s.id == doc.project.current_script_version_id)
    script.lines[0].spoken_text = "这版是你改的。我还问过你，能不能发。"
    line_id = script.lines[0].id
    shots = [_spoken_shot(doc.shots[0], "action", 1, [line_id], 2.5, start=0.2, end=2.1),
             _spoken_shot(doc.shots[0], "evidence", 2, [line_id], 1.5, end=1.3)]
    doc.shots = shots
    unit = GenerationUnit(id="unit-bridge", clip_group_id="group-bridge", output_slot_id="video.bridge",
        shot_ids=[s.id for s in shots], requested_duration_seconds=4,
        generation_intent="One line continues over the evidence cut.",
        shot_windows=[GenerationUnitShotWindow(shot_id=s.id, shot_revision=s.revision,
            source_in_seconds=offset, source_out_seconds=offset+s.duration_seconds, duration_seconds=s.duration_seconds)
            for s, offset in zip(shots, (0.0, 2.5), strict=True)])
    return doc, unit, shots


@pytest.mark.parametrize("route", list(VideoGenerationRoute))
def test_one_utterance_uses_actual_cross_shot_window_and_keeps_visual_cuts(
    bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]], route: VideoGenerationRoute,
) -> None:
    doc, unit, shots = bridge
    before = doc.model_dump()
    spans = generation_unit_dialogue_spans(unit, list(reversed(shots)))
    assert len(spans) == 1
    assert (spans[0].start_seconds, spans[0].end_seconds) == (0.2, 3.8)
    assert [(p.shot_id, p.start_seconds, p.end_seconds) for p in spans[0].parts] == [
        ("action", 0.2, 2.1), ("evidence", 0.0, 1.3)]
    prompt = compile_h3_generation_unit_prompt_ir(doc, unit, list(reversed(shots)), route=route)
    assert prompt.count("<d>") == 1
    assert "once within 0.20 to 3.80 seconds" in prompt
    assert "[Shot 2] [2.50-4.00 seconds]" in prompt
    assert prompt.count("<scenetrans>") == 2
    validate_h3_generation_unit_prompt_ir(prompt, doc, unit, shots, route=route, duration_seconds=4)
    assert doc.model_dump() == before


def test_language_payload_has_one_text_source_and_derived_continuation(bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]]) -> None:
    doc, unit, shots = bridge
    _instructions, text = build_generation_unit_language_prompt_input(doc, unit, shots,
        route=VideoGenerationRoute.REFERENCE, duration_seconds=4, production_spec=_spec(doc), media_inputs=[])
    payload = json.loads(text)
    first, second = payload["locked_shots"]
    assert len(first["locked_dialogue"]) == 1 and second["locked_dialogue"] == []
    assert first["dialogue_spans"][0]["end_seconds"] == 3.8
    assert second["dialogue_spans"] == []
    assert second["continuing_dialogue"] == [{"utterance_id": "utterance-1", "line_id": shots[0].dialogue_line_ids[0],
        "speaker_id": "S1", "finish_by_seconds": 3.8, "beat_indices": [0]}]
    assert payload["request_dialogue_spans"][0]["parts"][1]["shot_id"] == "evidence"
    assert "<scenetrans>" in payload["production_rules"]["dialogue"]


@pytest.mark.parametrize("fault", ["repeat", "omit", "move", "soundscape"])
def test_group_validator_checks_utterance_once_at_its_starting_shot(
    bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]], fault: str,
) -> None:
    doc, unit, shots = bridge
    prompt = compile_h3_generation_unit_prompt_ir(doc, unit, shots, route=VideoGenerationRoute.DIRECT)
    block = re.search(r"<d>.*?</d>", prompt)
    assert block is not None
    if fault in {"move", "omit"}:
        prompt = prompt.replace(block[0], "", 1)
    if fault in {"repeat", "move"}:
        prompt = prompt.replace("<scenetrans> Continuation", f"{block[0]} <scenetrans> Continuation", 1)
    if fault == "soundscape":
        prompt = prompt.replace("overall_soundscape:", f"overall_soundscape: {block[0]}")
    with pytest.raises(ValueError, match="dialogue"):
        validate_h3_generation_unit_prompt_ir(prompt, doc, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=4)


def test_continuous_group_prose_does_not_require_every_beat_marker(bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]]) -> None:
    doc, unit, shots = bridge
    prompt = compile_h3_generation_unit_prompt_ir(doc, unit, shots, route=VideoGenerationRoute.DIRECT)
    prompt = re.sub(r"(?<!\[Shot [12]\] )\[\d+\.\d+-\d+\.\d+ seconds\]", "", prompt)
    validate_h3_generation_unit_prompt_ir(prompt, doc, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=4)


def test_empty_shot_distinct_ids_and_true_repetition_are_never_collapsed(bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]]) -> None:
    doc, _unit, shots = bridge
    first = shots[0].dialogue_line_ids[0]
    script = next(s for s in doc.script_versions if s.id == doc.project.current_script_version_id)
    second = script.lines[1].id
    script.lines[1].spoken_text = script.lines[0].spoken_text
    for middle, expected in [([], [first, first]), ([second], [first, second, first])]:
        chain = [shots[0], _spoken_shot(shots[0], "middle", 2, middle, 1), shots[1]]
        assert [span.line_id for span in request_dialogue_spans(chain)] == expected


@pytest.mark.parametrize("assignments", ["A_B", "A_B_A", "A_empty_A"])
def test_compiler_and_validator_preserve_distinct_actual_utterances(
    bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]], assignments: str,
) -> None:
    doc, unit, source = bridge
    script = next(s for s in doc.script_versions if s.id == doc.project.current_script_version_id)
    script.lines[1].spoken_text = script.lines[0].spoken_text
    ids = {"A": [script.lines[0].id], "B": [script.lines[1].id], "empty": []}
    shots = [_spoken_shot(source[0], f"shot-{index}", index+1, ids[name], 2)
             for index, name in enumerate(assignments.split("_"))]
    doc.shots = shots
    unit = unit.model_copy(update={"shot_ids": [s.id for s in shots], "requested_duration_seconds": 2*len(shots),
        "shot_windows": [GenerationUnitShotWindow(shot_id=s.id, shot_revision=s.revision,
            source_in_seconds=2*index, source_out_seconds=2*index+2, duration_seconds=2)
            for index, s in enumerate(shots)]})
    prompt = compile_h3_generation_unit_prompt_ir(doc, unit, shots, route=VideoGenerationRoute.DIRECT)
    assert prompt.count("<d>") == (3 if assignments == "A_B_A" else 2)
    validate_h3_generation_unit_prompt_ir(prompt, doc, unit, shots, route=VideoGenerationRoute.DIRECT,
                                        duration_seconds=int(unit.requested_duration_seconds))


@pytest.mark.parametrize("target", [0, 1])
def test_managed_single_shot_and_partial_group_do_not_regenerate_half_a_line(
    bridge: tuple[ProjectDocument, GenerationUnit, list[ShotPlan]], target: int,
) -> None:
    doc, unit, shots = bridge
    doc.storyboard_document_id = "current-managed-board"
    shot = shots[target]
    with pytest.raises(ValueError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
        compile_h3_prompt_ir(doc, shot, route=VideoGenerationRoute.DIRECT)
    with pytest.raises(ValueError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
        build_language_prompt_input(doc, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=4, source_prompt=None)
    with pytest.raises(ValueError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
        validate_h3_prompt_ir("untrusted override", doc, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=4)
    partial = unit.model_copy(update={"shot_ids": [shot.id], "requested_duration_seconds": shot.duration_seconds,
        "shot_windows": [GenerationUnitShotWindow(shot_id=shot.id, shot_revision=shot.revision,
            source_in_seconds=0, source_out_seconds=shot.duration_seconds, duration_seconds=shot.duration_seconds)]})
    with pytest.raises(ValueError, match="different groups"):
        compile_h3_generation_unit_prompt_ir(doc, partial, [shot], route=VideoGenerationRoute.DIRECT)


def test_saved_patch_prepare_restart_and_enqueue_preserve_one_utterance(tmp_path: Path) -> None:
    service, fake, doc, _generation, old_unit, _request = _fixture(tmp_path)
    board = service.get_storyboard(doc.project.id)
    originals = board.storyboard.shots[:2]
    line_id = originals[1].dialogue_line_ids[0]
    shots = [_spoken_shot(originals[0], originals[0].id, 1, [line_id], 2.5),
             _spoken_shot(originals[1], originals[1].id, 2, [line_id], 1.5)]
    group = board.storyboard.clip_groups[0].model_copy(update={"duration_seconds": 4})
    service.patch_storyboard(doc.project.id, StoryboardPatchRequest(base_version_id=board.version.id,
        origin=ProductionDocumentOrigin.AGENT, shot_splices=[StoryboardShotSplice(shot_ids=old_unit.shot_ids, shots=shots)],
        clip_groups=[group]))
    current = service.get_project(doc.project.id).document
    unit = old_unit.model_copy(update={"requested_duration_seconds": 4, "shot_windows": [
        GenerationUnitShotWindow(shot_id=s.id, shot_revision=s.revision, source_in_seconds=offset,
            source_out_seconds=offset+s.duration_seconds, duration_seconds=s.duration_seconds)
        for s, offset in zip(current.shots[:2], (0.0, 2.5), strict=True)]})
    fake.output_text = compile_h3_generation_unit_prompt_ir(current, unit, current.shots[:2], route=VideoGenerationRoute.DIRECT)
    prepared = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=shots[0].id,
        route=VideoGenerationRoute.DIRECT, media_inputs=[], enqueue=False))
    assert prepared.status == "PREPARED", prepared.error
    assert len(fake.calls) == 1 and service.repository.list_tasks(doc.project.id) == []
    service.h3_config.update(H3ProviderConfigUpdate(api_key="fake-token"))
    restarted = ShortDramaService(database_path=service.repository.database_path,
        workspace_root=service.workspace_root, language_provider_factory=lambda _k, _u: fake)
    queued = restarted.prepare_h3_group(doc.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id, enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    task = restarted.repository.get_task(queued.task_ids[0])
    assert task is not None and len(task.shot_windows) == 2
    run = restarted.get_h3_provider_run(task.id)
    assert run.prompt.count("<d>") == 1
    assert len(fake.calls) == 1
    for shot in current.shots[:2]:
        with pytest.raises(WorkflowConflictError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
            restarted.preview_h3_probe(doc.project.id, H3ProbeRequest(shot_id=shot.id, prompt_override="Do not bypass dialogue ownership."))
        with pytest.raises(ValueError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
            restarted.compile_h3_prompt_with_language_model(doc.project.id, LanguagePromptCompileRequest(shot_id=shot.id))
    with pytest.raises(WorkflowConflictError, match="H3_DIALOGUE_REQUEST_INCOMPLETE"):
        restarted.compile_h3_production_route_plan(doc.project.id, H3ProductionRoutePlanRequest(shot_ids=[shots[0].id]))
    assert len(fake.calls) == 1
    partial_task = task.model_copy(update={"input_shot_revisions": {shots[0].id: current.shots[0].revision}})
    assert "H3_DIALOGUE_REQUEST_INCOMPLETE" in (restarted.h3_worker.input_staleness_reason(partial_task, run) or "")
    # The optional official adapter uses the same facts/validator without a
    # second language expansion. This client is entirely local and deterministic.
    ir = FakeIR(fake.output_text)
    ir_request = LanguagePromptCompileRequest(generation_plan_document_id=prepared.generation_plan_document_id,
        generation_plan_version_id=prepared.generation_plan_version_id, generation_unit_id=prepared.generation_unit_id,
        route=VideoGenerationRoute.DIRECT, media_inputs=[])
    with pytest.raises(H3ContextIRPending):
        compile_h3_context_ir(restarted, doc.project.id, ir_request, client=ir)
    source = ir.submitted[0].content[0].text
    assert source is not None and '"continuing_dialogue"' in source
    due(restarted.workspace_root / "h3-context-ir")
    result = compile_h3_context_ir(restarted, doc.project.id, ir_request, client=ir)
    assert result.compiled_prompt.count("<d>") == 1
    assert len(ir.submitted) == len(ir.queries) == len(fake.calls) == 1
