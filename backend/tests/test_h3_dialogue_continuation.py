"""Offline regression of real continuous speech spanning multiple shot beats."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from short_drama.models import AudioKind, ProjectDocument, ShotPlan, ShotTimelineBeat, VideoGenerationRoute
from short_drama.production_contracts import GenerationUnit, GenerationUnitShotWindow, ProductionSpecContent
from short_drama.providers.h3_dialogue_spans import shot_dialogue_spans
from short_drama.providers.h3_language_prompt import (
    build_generation_unit_language_prompt_input, build_language_prompt_input,
    validate_language_generation_unit_prompt, validate_language_prompt,
)
from short_drama.providers.h3_prompt_skill import (
    compile_h3_generation_unit_prompt_ir, compile_h3_prompt_ir,
    validate_h3_generation_unit_prompt_ir, validate_h3_prompt_ir,
)
from short_drama.service import ShortDramaService


LINE = "报价出错了。说明你签一下，就说你没核对。"


@pytest.fixture
def document(tmp_path: Path) -> ProjectDocument:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "work")
    document = service.initialize_fixture().document
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    script.lines[0].spoken_text = LINE
    script.lines[1].spoken_text = "先说清楚。"
    return document


def _shot(document: ProjectDocument, assignments: list[list[str]], *, duration: float = 5.5) -> ShotPlan:
    shot = document.shots[0]
    lines: list[str] = []
    for line_id in [line_id for beat in assignments for line_id in beat]:
        if not lines or lines[-1] != line_id:
            lines.append(line_id)
    boundaries = [0.0, 2.2, duration] if len(assignments) == 2 else [i * duration / len(assignments) for i in range(len(assignments) + 1)]
    return ShotPlan.model_validate({
        **shot.model_dump(), "duration_seconds": duration, "dialogue_line_ids": lines,
        "visual": "Two people sit across one desk.", "audio_intent": "Native Mandarin speech, with room tone.",
        "timeline_beats": [ShotTimelineBeat(
            start_seconds=boundaries[i], end_seconds=boundaries[i + 1],
            visual="The same two people remain seated.", framing="medium two shot", camera="locked camera",
            action="He pushes the paper." if i == 0 else "She watches his hand.",
            dialogue_line_ids=ids,
        ) for i, ids in enumerate(assignments)],
    })


def _ids(document: ProjectDocument) -> tuple[str, str]:
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    return script.lines[0].id, script.lines[1].id


def _unit(document: ProjectDocument, shot: ShotPlan) -> tuple[GenerationUnit, list[ShotPlan]]:
    silent = document.shots[1].model_copy(update={"duration_seconds": 2.0, "dialogue_line_ids": [], "timeline_beats": []})
    shots = [silent, shot]
    return GenerationUnit(
        id="unit-dialogue", clip_group_id="group-dialogue", output_slot_id="video.dialogue",
        shot_ids=[item.id for item in shots],
        shot_windows=[GenerationUnitShotWindow(
            shot_id=item.id, shot_revision=item.revision, source_in_seconds=start,
            source_out_seconds=start + item.duration_seconds, duration_seconds=item.duration_seconds,
        ) for item, start in zip(shots, (0.0, 2.0), strict=True)],
        requested_duration_seconds=2.0 + shot.duration_seconds,
        generation_intent="Keep the reaction and continuous speech in the declared shots.",
    ), shots


def _spec(document: ProjectDocument) -> ProductionSpecContent:
    return ProductionSpecContent.model_validate({
        "project_id": document.project.id, "script_version_id": document.project.current_script_version_id,
        "bible_version_id": document.project.current_bible_version_id, "title": "A continuous sentence",
        "production_scope": "Preserve the shots.", "production_mode": "Drama", "genre_and_setting": "Office",
        "language": "zh-CN", "aspect_ratio": "9:16",
    })


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("route", [VideoGenerationRoute.DIRECT, VideoGenerationRoute.REFERENCE])
def test_one_continuous_line_has_one_spoken_block_in_single_shot_and_group(
    document: ProjectDocument, grouped: bool, route: VideoGenerationRoute,
) -> None:
    first, _second = _ids(document)
    shot = _shot(document, [[first], [first]])
    source = shot.model_dump()
    if grouped:
        unit, shots = _unit(document, shot)
        prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=route)
        validate_h3_generation_unit_prompt_ir(prompt, document, unit, shots, route=route, duration_seconds=8)
        validate_language_generation_unit_prompt(prompt, document, unit, shots, route=route, duration_seconds=8, media_inputs=[])
        assert "once within 2.00 to 7.50 seconds" in prompt
        assert "[4.20-7.50 seconds]" in prompt
    else:
        prompt = compile_h3_prompt_ir(document, shot, route=route)
        validate_h3_prompt_ir(prompt, document, shot, route=route, duration_seconds=6)
        validate_language_prompt(prompt, document, shot, route=route, duration_seconds=6)
        assert "once within 0.00 to 5.50 seconds" in prompt
        assert "[2.20-5.50 seconds]" in prompt
    assert prompt.count(f"<d>[Chinese] {LINE}</d>") == 1
    assert "Continuation of utterance 1" in prompt
    assert "<scenetrans>" not in prompt
    assert shot.model_dump() == source


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("fault", ["repeat", "omit", "move", "soundscape"])
def test_validator_rejects_restarted_missing_or_moved_continuous_speech(
    document: ProjectDocument, grouped: bool, fault: str,
) -> None:
    first, _second = _ids(document)
    shot = _shot(document, [[first], [first]])
    unit, shots = _unit(document, shot)
    route = VideoGenerationRoute.DIRECT
    prompt = (compile_h3_generation_unit_prompt_ir(document, unit, shots, route=route) if grouped
              else compile_h3_prompt_ir(document, shot, route=route))
    block = f"<d>[Chinese] {LINE}</d>"
    if fault == "repeat":
        prompt = prompt.replace("Continuation of utterance 1", f"{block} Continuation of utterance 1")
    elif fault == "omit":
        # The text is still in the prompt, but is no longer spoken content.
        prompt = prompt.replace(block, LINE)
    elif fault == "move":
        prompt = prompt.replace(block, "").replace("Continuation of utterance 1", f"{block} Continuation of utterance 1")
    else:
        prompt = prompt.replace("overall_soundscape:", f"overall_soundscape: {block}")
    with pytest.raises(ValueError, match="dialogue"):
        if grouped:
            validate_h3_generation_unit_prompt_ir(prompt, document, unit, shots, route=route, duration_seconds=8)
        else:
            validate_h3_prompt_ir(prompt, document, shot, route=route, duration_seconds=6)


@pytest.mark.parametrize("same_text", [False, True])
def test_actual_repetition_and_distinct_ids_with_same_text_are_not_deduplicated(
    document: ProjectDocument, same_text: bool,
) -> None:
    first, second = _ids(document)
    if same_text:
        script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
        script.lines[1].spoken_text = LINE
        assignments = [[first], [second]]
    else:
        assignments = [[first], [second], [first]]
    shot = _shot(document, assignments, duration=6.0)
    assert [span.line_id for span in shot_dialogue_spans(shot)] == [ids[0] for ids in assignments]
    prompt = compile_h3_prompt_ir(document, shot, route=VideoGenerationRoute.DIRECT)
    block = f"<d>[Chinese] {LINE}</d>"
    assert prompt.count(block) == 2
    assert "(S2)" in prompt
    validate_h3_prompt_ir(prompt, document, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=6)
    with pytest.raises(ValueError, match="dialogue"):
        validate_h3_prompt_ir(prompt.replace(block, "", 1), document, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=6)


def test_span_mapping_follows_existing_empty_beat_and_legacy_shot_contract(document: ProjectDocument) -> None:
    first, _second = _ids(document)
    shot = _shot(document, [[first], [], [first]], duration=6.0)
    spans = shot_dialogue_spans(shot)
    assert len(spans) == 1 and spans[0].beat_indices == (0, 2)
    assert (spans[0].start_seconds, spans[0].end_seconds) == (0.0, 6.0)
    prompt = compile_h3_prompt_ir(document, shot, route=VideoGenerationRoute.DIRECT)
    validate_h3_prompt_ir(prompt, document, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=6)
    legacy = shot.model_copy(update={"timeline_beats": [], "dialogue_line_ids": [first, first]})
    assert len(shot_dialogue_spans(legacy)) == 2
    prompt = compile_h3_prompt_ir(document, legacy, route=VideoGenerationRoute.DIRECT)
    assert prompt.count(f"<d>[Chinese] {LINE}</d>") == 2
    validate_h3_prompt_ir(prompt, document, legacy, route=VideoGenerationRoute.DIRECT, duration_seconds=6)


def test_language_inputs_map_continuation_to_one_utterance_and_correct_group_offset(document: ProjectDocument) -> None:
    first, _second = _ids(document)
    shot = _shot(document, [[first], [first]])
    instructions, text = build_language_prompt_input(document, shot, route=VideoGenerationRoute.DIRECT, duration_seconds=6, source_prompt=None)
    payload = json.loads(text)
    expected = [{"utterance_id": "utterance-1", "line_id": first, "start_seconds": 0.0,
                 "end_seconds": 5.5, "start_beat_index": 0, "continuation_beat_indices": [1]}]
    assert payload["dialogue_spans"] == expected
    assert payload["locked_dialogue"][0]["id"] == first
    assert len(payload["locked_dialogue"]) == 1
    assert "production_rules.dialogue" in instructions
    assert "只写一个 <d>" in payload["production_rules"]["dialogue"]
    assert "不同 span" in payload["production_rules"]["dialogue"]
    unit, shots = _unit(document, shot)
    _, text = build_generation_unit_language_prompt_input(document, unit, shots, route=VideoGenerationRoute.DIRECT,
        duration_seconds=math.ceil(unit.requested_duration_seconds), production_spec=_spec(document), media_inputs=[])
    payload = json.loads(text)
    assert payload["locked_shots"][0]["dialogue_spans"] == []
    assert payload["locked_shots"][1]["dialogue_spans"] == [dict(expected[0], start_seconds=2.0, end_seconds=7.5)]
    assert payload["locked_shots"][1]["locked_shot"]["timeline_beats"][1]["start_seconds"] == 2.2


def test_group_prose_preserves_existing_shot_window_format_without_extra_beat_marker_gate(document: ProjectDocument) -> None:
    first, _second = _ids(document)
    shot = _shot(document, [[first], [first]])
    unit, shots = _unit(document, shot)
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=VideoGenerationRoute.DIRECT)
    # Keep each required [Shot N] window and the complete utterance's span.
    prompt = re.sub(r"(?<!\[Shot 2\] )\[(?:2\.00-4\.20|4\.20-7\.50) seconds\]", "", prompt)
    validate_language_generation_unit_prompt(prompt, document, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=8, media_inputs=[])
    assert prompt.count("<d>") == 1


def _voice_group(document: ProjectDocument, binding: str) -> tuple[GenerationUnit, list[ShotPlan]]:
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    first, second = script.lines[:2]
    if binding == "unbound":
        first.speaker, first.speaker_character_id = "赵衡", None
        second.speaker, second.speaker_character_id = "许岚", None
    else:
        first.speaker, first.speaker_character_id = "经理", "manager-a" if binding == "typed" else None
        second.speaker, second.speaker_character_id = "经理", "manager-b"
    first.audio_kind = second.audio_kind = AudioKind.DIALOGUE
    repeated = first.model_copy(update={"id": "line-repeated-a", "spoken_text": "我再说一次。"})
    if binding == "typed":
        repeated.speaker = "另一显示名"  # Explicit entity identity wins over presentation name.
    script.lines.append(repeated)
    shots = [
        _shot(document, [[line.id]], duration=4.0).model_copy(update={"id": f"voice-shot-{index}", "sequence": index + 1})
        for index, line in enumerate((first, second, repeated))
    ]
    unit = GenerationUnit(
        id="unit-voices", clip_group_id="group-voices", output_slot_id="video.voices",
        shot_ids=[shot.id for shot in shots], requested_duration_seconds=12,
        generation_intent="Keep the declared characters speaking in turn.",
        shot_windows=[GenerationUnitShotWindow(
            shot_id=shot.id, shot_revision=shot.revision, duration_seconds=4.0,
            source_in_seconds=index * 4.0, source_out_seconds=(index + 1) * 4.0,
        ) for index, shot in enumerate(shots)],
    )
    return unit, shots


@pytest.mark.parametrize("binding", ["unbound", "typed", "mixed"])
def test_request_voice_mapping_is_shared_across_shots_and_language_payloads(document: ProjectDocument, binding: str) -> None:
    unit, shots = _voice_group(document, binding)
    source = document.model_dump()
    reversed_shots = list(reversed(shots))
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, reversed_shots, route=VideoGenerationRoute.DIRECT)
    assert re.findall(r"\((S\d+)\).*?<d>", prompt) == ["S1", "S2", "S1"]
    validate_h3_generation_unit_prompt_ir(prompt, document, unit, reversed_shots, route=VideoGenerationRoute.DIRECT, duration_seconds=12)
    _, text = build_generation_unit_language_prompt_input(document, unit, reversed_shots, route=VideoGenerationRoute.DIRECT,
        duration_seconds=12, production_spec=_spec(document), media_inputs=[])
    payload = json.loads(text)
    assert [row["locked_dialogue"][0]["speaker_id"] for row in payload["locked_shots"]] == ["S1", "S2", "S1"]
    assert "不代表 Bible 实体" in payload["production_rules"]["dialogue"]
    # A separate single-shot request owns its numbering; no project-wide ID state.
    prompt = compile_h3_prompt_ir(document, shots[1], route=VideoGenerationRoute.DIRECT)
    assert re.findall(r"\((S\d+)\).*?<d>", prompt) == ["S1"]
    _, text = build_language_prompt_input(document, shots[1], route=VideoGenerationRoute.DIRECT, duration_seconds=4, source_prompt=None)
    assert json.loads(text)["locked_dialogue"][0]["speaker_id"] == "S1"
    assert document.model_dump() == source


@pytest.mark.parametrize("fault", ["two_people_one_voice", "one_person_two_voices"])
def test_conflicting_explicit_voice_labels_are_rejected(document: ProjectDocument, fault: str) -> None:
    unit, shots = _voice_group(document, "unbound")
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=VideoGenerationRoute.DIRECT)
    if fault == "two_people_one_voice":
        prompt = prompt.replace("(S2)", "(S1)")
    else:
        before, last = prompt.rsplit("(S1)", 1)
        prompt = before + "(S3)" + last
    with pytest.raises(ValueError, match="conflicting speaker ID"):
        validate_language_generation_unit_prompt(prompt, document, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=12, media_inputs=[])


@pytest.mark.parametrize("legacy", ["name_only", "different_numbers"])
def test_legacy_voice_prose_is_readable_without_forcing_new_numbering(document: ProjectDocument, legacy: str) -> None:
    unit, shots = _voice_group(document, "unbound")
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=VideoGenerationRoute.DIRECT)
    prompt = (re.sub(r" \(S\d+\)", "", prompt) if legacy == "name_only"
              else prompt.replace("(S1)", "(S9)").replace("(S2)", "(S4)"))
    validate_language_generation_unit_prompt(prompt, document, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=12, media_inputs=[])


def test_silent_listener_annotation_is_not_guessed_to_be_the_speaker(document: ProjectDocument) -> None:
    unit, shots = _voice_group(document, "unbound")
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=VideoGenerationRoute.DIRECT)
    prompt = prompt.replace("赵衡 (S1) says in Mandarin Chinese:",
                            "赵衡 (S1), looking toward silent 许岚 (S2), says in Mandarin Chinese:", 1)
    validate_language_generation_unit_prompt(prompt, document, unit, shots, route=VideoGenerationRoute.DIRECT, duration_seconds=12, media_inputs=[])
