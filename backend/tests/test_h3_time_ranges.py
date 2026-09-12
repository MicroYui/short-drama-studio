"""Real failed draft plus bounded time-notation and locked-dialogue regressions."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest

from short_drama.h3_models import H3MediaInput
from short_drama.models import ProjectDocument, ShotPlan, VideoGenerationRoute
from short_drama.production_contracts import GenerationUnit, GenerationUnitShotWindow
from short_drama.providers.h3_language_prompt import build_language_prompt_input
from short_drama.providers.h3_prompt_skill import (
    compile_h3_generation_unit_prompt_ir, validate_h3_generation_unit_prompt_ir, validate_h3_prompt_ir,
)
from short_drama.providers.h3_timing import find_time_range, time_range_marker


@pytest.fixture
def actual() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures/h3_time_range_actual.json").read_text())


def _validate(actual: dict[str, Any], prompt: str, *, case_index: int = 0) -> None:
    case = actual["cases"][case_index]
    validate_h3_prompt_ir(prompt, ProjectDocument.model_validate(actual["document"]),
        ShotPlan.model_validate(case["shot"]), route=VideoGenerationRoute.REFERENCE,
        duration_seconds=case["request"]["duration_seconds"],
        media_inputs=[H3MediaInput.model_validate(x) for x in case["request"]["media_inputs"]])


@pytest.mark.parametrize(("first", "second"), [
    ("00:00.000–00:04.500", "00:04.500–00:05.000"),
    ("[00:00:00.000—00:00:04.500]", "[00:00:04.500—00:00:05.000]"),
    ("[0-4.5 seconds]", "[4.5-5 seconds]"),
    ("[0.00 - 4.50 seconds]", "[4.50 - 5.00 seconds]"),
    ("0 s – 4.5 s", "4.5 s – 5 s"),
    ("0–4.5秒", "4.5–5秒"),
])
def test_real_rejected_draft_accepts_only_equivalent_range_notation(
    actual: dict[str, Any], first: str, second: str,
) -> None:
    prompt = actual["cases"][0]["rejected_prompt"]
    _validate(actual, prompt.replace("00:00.000–00:04.500", first).replace("00:04.500–00:05.000", second))


@pytest.mark.parametrize("fault", ["start", "end", "missing", "reorder", "move_dialogue", "repeat_dialogue", "outside_narrative"])
def test_real_draft_still_requires_locked_ranges_and_utterance_placement(actual: dict[str, Any], fault: str) -> None:
    prompt = actual["cases"][0]["rejected_prompt"]
    first, second = "00:00.000–00:04.500", "00:04.500–00:05.000"
    if fault == "start":
        prompt = prompt.replace(first, "00:00.001–00:04.500")
    elif fault == "end":
        prompt = prompt.replace(first, "00:00.000–00:04.501")
    elif fault == "missing":
        prompt = prompt.replace(first, "During the utterance")
    elif fault == "reorder":
        prompt = prompt.replace(first, "TEMP").replace(second, first).replace("TEMP", second)
    elif fault in {"move_dialogue", "repeat_dialogue"}:
        block = re.search(r"<d>.*?</d>", prompt)
        assert block is not None
        if fault == "move_dialogue":
            prompt = prompt.replace(block[0], "")
        prompt = prompt.replace(second, second + ": " + block[0])
    else:
        prompt = prompt.replace(first, "During the utterance").replace("summary:", "summary: " + first)
    with pytest.raises(ValueError, match="timeline|time range|dialogue"):
        _validate(actual, prompt)


def test_actual_reaction_draft_does_not_infer_missing_range_from_other_fields_or_approximate_subdivisions(
    actual: dict[str, Any],
) -> None:
    prompt = actual["cases"][1]["rejected_prompt"]
    assert "approximately" in prompt
    with pytest.raises(ValueError, match="locked timeline range"):
        _validate(actual, prompt, case_index=1)


@pytest.mark.parametrize("text", [
    "[10.00-4.50 seconds]", "[-0.00-4.50 seconds]", "[0-4.501 seconds]",
    "00:60.000–00:04.500", "00:00.000–00:04.5001", "0–4.5 items",
    "x0–4.5 seconds", "00:00:00:00.000–00:04.500", "0–4.5e1 seconds",
])
def test_time_reader_does_not_accept_numeric_substrings_counts_or_malformed_timecodes(text: str) -> None:
    assert find_time_range(text, 0, 4.5) == -1


def test_existing_two_decimal_compiler_precision_remains_compatible() -> None:
    assert find_time_range(time_range_marker(1 / 3, 2 / 3), 1 / 3, 2 / 3) >= 0
    assert find_time_range("00:00.333–00:00.667", .333, .667) >= 0
    assert find_time_range("00:00.334–00:00.667", .333, .667) == -1


def _group(actual: dict[str, Any]) -> tuple[ProjectDocument, GenerationUnit, list[ShotPlan]]:
    document = ProjectDocument.model_validate(actual["document"])
    shots = [ShotPlan.model_validate(x["shot"]) for x in actual["cases"]]
    cursor = 0.0
    windows: list[GenerationUnitShotWindow] = []
    for shot in shots:
        windows.append(GenerationUnitShotWindow(shot_id=shot.id, shot_revision=shot.revision,
            source_in_seconds=cursor, source_out_seconds=cursor + shot.duration_seconds,
            duration_seconds=shot.duration_seconds))
        cursor += shot.duration_seconds
    unit = GenerationUnit(id="timing-group", clip_group_id="fixture-group", output_slot_id="video.fixture",
        shot_ids=[x.id for x in shots], shot_windows=windows, requested_duration_seconds=cursor,
        generation_intent="The unchanged response then the silent reaction.")
    return document, unit, shots


def _clock(value: float) -> str:
    return f"{int(value // 60):02d}:{value % 60:06.3f}"


@pytest.mark.parametrize("route", [VideoGenerationRoute.DIRECT, VideoGenerationRoute.REFERENCE])
def test_group_windows_accept_equal_timecodes_without_changing_dialogue_or_historical_beat_policy(
    actual: dict[str, Any], route: VideoGenerationRoute,
) -> None:
    document, unit, shots = _group(actual)
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=route)
    validate = lambda text: validate_h3_generation_unit_prompt_ir(text, document, unit, shots, route=route, duration_seconds=7)
    converted = re.sub(r"\[(\d+\.\d+)-(\d+\.\d+) seconds\]",
        lambda m: f"[{_clock(float(m[1]))}–{_clock(float(m[2]))}]", prompt)
    validate(converted)
    # Historical group prose must keep its explicit Shot windows, but may
    # describe the internal beats without separate repeated beat markers.
    windows: list[str] = []
    def hide_window(match: re.Match[str]) -> str:
        windows.append(match[0])
        return f"WINDOW{len(windows) - 1}"
    historical = re.sub(r"\[Shot \d+\] \[[^\]]+ seconds\]", hide_window, prompt)
    historical = re.sub(r"\[\d+\.\d+-\d+\.\d+ seconds\]", "", historical)
    for index, window in enumerate(windows):
        historical = historical.replace(f"WINDOW{index}", window)
    validate(historical)
    with pytest.raises(ValueError, match="locked window"):
        validate(converted.replace("[Shot 2] [00:05.000–00:06.500]", "[Shot 2] [00:05.000–00:06.501]"))
    block = re.search(r"<d>.*?</d>", converted)
    assert block is not None
    moved = converted.replace(block[0], "").replace("[00:04.500–00:05.000]", "[00:04.500–00:05.000] " + block[0])
    with pytest.raises(ValueError, match="dialogue"):
        validate(moved)


def test_single_shot_language_input_exposes_every_exact_beat_marker(actual: dict[str, Any]) -> None:
    document = ProjectDocument.model_validate(actual["document"])
    for case in actual["cases"]:
        shot = ShotPlan.model_validate(case["shot"])
        _instructions, text = build_language_prompt_input(document, shot,
            route=VideoGenerationRoute.REFERENCE, duration_seconds=case["request"]["duration_seconds"],
            media_inputs=[H3MediaInput.model_validate(x) for x in case["request"]["media_inputs"]],
            source_prompt=case["request"].get("source_prompt"))
        payload = json.loads(text)
        actual_beats = payload["locked_shot"]["timeline_beats"]
        for source, output in zip(shot.timeline_beats, actual_beats, strict=True):
            assert output.pop("required_timing_marker") == time_range_marker(source.start_seconds, source.end_seconds)
            assert output == source.model_dump(mode="json")
        assert "required_timing_marker" in payload["production_rules"]["timing_markers"]


def test_group_reference_explanation_does_not_replace_or_reorder_narrative_windows(actual: dict[str, Any]) -> None:
    document, unit, shots = _group(actual)
    route = VideoGenerationRoute.REFERENCE
    prompt = compile_h3_generation_unit_prompt_ir(document, unit, shots, route=route)
    explanation = (
        " The room texture supports [Shot 2] [00:05.000–00:06.500] and "
        "[Shot 1] [00:00.000–00:05.000]; these are reference usage notes. "
    )
    prompt = prompt.replace("retention_analysis:", "retention_analysis:" + explanation)
    validate_h3_generation_unit_prompt_ir(prompt, document, unit, shots, route=route, duration_seconds=7)
    missing = prompt.replace("[Shot 2] [5.00-6.50 seconds]", "The reaction continues")
    with pytest.raises(ValueError, match="locked window"):
        validate_h3_generation_unit_prompt_ir(missing, document, unit, shots, route=route, duration_seconds=7)
