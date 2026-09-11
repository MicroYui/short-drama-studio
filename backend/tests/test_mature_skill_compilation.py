"""Actual stage loading and H3 assembly regressions; no provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.models import ProductionProfile, VideoGenerationRoute
from short_drama.models import EnqueueRequest, VideoInspectionReport
from short_drama.production_media_context import previous_adopted_media_boundary
from short_drama.reference_planner import DirectorAdviceRequest
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_trace import production_trace, trace_context
from short_drama.video_inspection import inspection_source_context
from short_drama.production_profiles import (
    ProductionPromptStage, ProductionSkillRole, compile_production_prompt,
    production_method_skill_catalog,
)
from short_drama.providers.h3_language_prompt import (
    LanguagePromptValidationError, apply_language_prompt_section_repair,
    build_generation_unit_language_prompt_input, build_language_prompt_section_repair_input,
    normalize_language_prompt,
)
from short_drama.providers.h3_prompt_skill import validate_h3_generation_unit_prompt_ir
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from tests.test_h3_generation_unit_language import _fixture
from tests.test_reference_planner import FakeDirectorLanguageProvider


@pytest.mark.parametrize("method_id", [item.id for item in production_method_skill_catalog()])
def test_post_director_loads_editing_not_storyboard_responsibility(method_id: str) -> None:
    prompt = compile_production_prompt(
        ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,),
        method_skill_ids=(method_id,),
        stage=ProductionPromptStage.POST,
        include_constitution=False,
    ).text
    assert "使用当前已选视频、实际媒体时间和本集编辑时间线" in prompt
    assert "## 当前作品与最小修复" in prompt
    assert "## 表演设计" not in prompt
    assert "不通过视频串联保持连续性" not in prompt
    assert "你负责把当前采用的文本意图转成" not in prompt
    for stale_runtime in ("hub_canvas_write_node", "execution_locks", "runtime_refs", "review.after_execution", "concurrency_limit:"):
        assert stale_runtime not in prompt


@pytest.mark.parametrize("method_id", [item.id for item in production_method_skill_catalog()])
def test_every_method_compiler_contributes_craft_without_a_second_provider_template(method_id: str) -> None:
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.VIDEO_PROMPT_COMPILER,), method_skill_ids=(method_id,),
        stage=ProductionPromptStage.VISUAL_GEN, include_constitution=False).text
    for stale_contract in ("固定六段骨架", "H3 唯一可执行填空骨架", "prompt_section_titles", "generation_authorization_unit", "concurrency_limit:", "用户明确确认 H3 生成前"):
        assert stale_contract not in prompt
    assert "正常失败例" in prompt or method_id == "method.drama_series"


def test_drama_actual_methods_remove_forced_interaction_and_post_production_formulas() -> None:
    concept = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.CONCEPT,), method_skill_ids=("method.drama_series",),
        stage=ProductionPromptStage.STORY, include_constitution=False).text
    post = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA,
        roles=(ProductionSkillRole.DIRECTOR,), method_skill_ids=("method.drama_series",),
        stage=ProductionPromptStage.POST, include_constitution=False).text
    assert "两轮轻交互" not in concept and "target_episode_scope" not in concept
    assert "## 创作大纲" in concept
    assert "## 声音与字幕" in post and "## 精剪与情绪" in post
    for old_rule in ("前30秒必须", "每60秒", "固定0.5秒黑场", "零屏幕文字", "OCR清理字幕"):
        assert old_rule not in post


def test_actual_compiler_input_has_one_provider_format_and_explicit_boundary_evidence(tmp_path: Path) -> None:
    service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    boundary: dict[str, object] = {
        "take_id": "take-used", "source_out_seconds": 4.5,
        "observed_end_state": "The plate remains in the boy's left hand.",
        "observation_scope": "frames only; audio unknown",
    }
    spec = service.get_production_spec(document.project.id).spec
    instructions, input_text = build_generation_unit_language_prompt_input(
        document, unit, document.shots[:2], route=VideoGenerationRoute.DIRECT,
        duration_seconds=int(unit.requested_duration_seconds), production_spec=spec,
        media_inputs=[], method_skill_ids=("method.drama_series",),
        actual_previous_media_boundary=boundary,
    )
    assert "H3 唯一可执行填空骨架" not in instructions
    assert "f1.8" not in instructions
    assert "角色声音签名" in instructions
    assert "required_prompt_schema" in instructions
    payload = json.loads(input_text)
    assert payload["actual_previous_media_boundary"] == boundary
    assert payload["previous_clip_planned_terminal_state"] is None
    assert "previous_clip_terminal_state" not in payload


def test_h3_field_order_is_assembled_without_changing_dialogue_or_shot_body(tmp_path: Path) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    fields = fake.output_text.split("\n\n")
    assert len(fields) == 3
    misordered = "\n\n".join([fields[2], fields[0], fields[1]])
    result = normalize_language_prompt(
        misordered, document, route=VideoGenerationRoute.DIRECT,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=[], generation_unit=True,
    )
    assert result == fake.output_text
    validate_h3_generation_unit_prompt_ir(
        result, document, unit, document.shots[:2], route=VideoGenerationRoute.DIRECT,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=[],
    )


def test_h3_assembly_restores_missing_binding_but_rejects_conflicting_assignment(tmp_path: Path) -> None:
    _service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    media = [H3MediaInput(
        kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url="https://example.test/identity.png"),
        subject_asset_id=document.assets[0].id, reference_alias="actor",
    )]
    prompt = build_h3_generation_unit_prompt(
        document, unit, document.shots[:2], route=VideoGenerationRoute.REFERENCE, media_inputs=media,
    )
    missing = prompt.replace("@actor = <Subject 1>; ", "")
    result = normalize_language_prompt(
        missing, document, route=VideoGenerationRoute.REFERENCE,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=media, generation_unit=True,
    )
    assert "@actor = <Subject 1>" in result
    assert result.split("detailed_description:", 1)[1] == prompt.split("detailed_description:", 1)[1]
    with pytest.raises(LanguagePromptValidationError, match="conflicting binding"):
        normalize_language_prompt(
            prompt.replace("@actor = <Subject 1>", "@actor = <Subject 2>"), document,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=int(unit.requested_duration_seconds), media_inputs=media, generation_unit=True,
        )


@pytest.mark.parametrize("route", [
    VideoGenerationRoute.FRAME_FIRST, VideoGenerationRoute.FRAME_LAST,
    VideoGenerationRoute.FRAME_FIRST_LAST,
])
def test_h3_generation_unit_frame_alignment_is_built_by_code(tmp_path: Path, route: VideoGenerationRoute) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    result = normalize_language_prompt(
        fake.output_text, document, route=route,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=[], generation_unit=True,
    )
    assert result.endswith(fake.output_text)
    validate_h3_generation_unit_prompt_ir(
        result, document, unit, document.shots[:2], route=route,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=[],
    )


def test_h3_length_repair_replaces_only_requested_prose_and_final_validation_protects_dialogue(tmp_path: Path) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    prompt = fake.output_text.replace("overall_soundscape:", "overall_soundscape: " + "Repeated rain. " * 600)
    _instructions, repair_input = build_language_prompt_section_repair_input(
        prompt, source_input_text=json.dumps({"locked_shots": [], "source_prompt": "do not replay"}),
        route=VideoGenerationRoute.DIRECT,
    )
    repair = json.loads(repair_input)
    assert list(repair["sections_to_replace"]) == ["overall_soundscape"]
    assert "source_prompt" not in repair["locked_source"]
    soundscape = fake.output_text.split("overall_soundscape: ", 1)[1].split("\n\n", 1)[0]
    result = apply_language_prompt_section_repair(
        prompt, json.dumps({"overall_soundscape": soundscape}), repair_input_text=repair_input,
    )
    assert result == fake.output_text
    validate_h3_generation_unit_prompt_ir(
        result, document, unit, document.shots[:2], route=VideoGenerationRoute.DIRECT,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=[],
    )
    with pytest.raises(LanguagePromptValidationError, match="unexpected or missing"):
        apply_language_prompt_section_repair(prompt, json.dumps({"detailed_description": "changed"}), repair_input_text=repair_input)


def test_h3_normalizer_rejects_unknown_preamble_instead_of_discarding_facts(tmp_path: Path) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    with pytest.raises(LanguagePromptValidationError, match="unexpected preamble"):
        normalize_language_prompt(
            "The hero now carries a red umbrella.\n" + fake.output_text,
            document, route=VideoGenerationRoute.DIRECT,
            duration_seconds=int(unit.requested_duration_seconds), media_inputs=[], generation_unit=True,
        )


def test_service_compiler_repairs_only_overlong_section_inside_same_tool_and_keeps_trace(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(tmp_path)
    valid = fake.output_text
    original = valid.replace("overall_soundscape:", "overall_soundscape: " + "Redundant rain. " * 600)
    soundscape = valid.split("overall_soundscape: ", 1)[1].split("\n\n", 1)[0]
    sequence = FakeDirectorLanguageProvider([original, json.dumps({"overall_soundscape": soundscape})])
    service.language_provider_factory = lambda _key, _url: sequence
    with production_trace(tmp_path / "trace", lambda _kind, _payload: None):
        result = service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert result.compiled_prompt == valid
    assert len(sequence.generations) == 2
    repair_input = json.loads(sequence.generations[1][1])
    assert list(repair_input["sections_to_replace"]) == ["overall_soundscape"]
    traces = [json.loads(path.read_text()) for path in (tmp_path / "trace").glob("*.json")]
    assert any(row["kind"] == "h3.prompt_compiler.draft" and row["data"]["prompt"] == original for row in traces)
    assert any(row["kind"] == "h3.prompt_compiler.assembled" and row["data"]["prompt"] == valid for row in traces)
    assert service.repository.list_tasks(document.project.id) == []


def test_nested_director_inherits_work_order_method_and_post_stage_without_reloading_storyboard(tmp_path: Path) -> None:
    service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    empty = '{"advisories":[],"repair_options":[],"recommended_strategy":null,"proposed_repair":null}'
    fake = FakeDirectorLanguageProvider([empty, empty])
    service.language_provider_factory = lambda _key, _url: fake
    run = service.agent_runtime.create_run(
        project_id=document.project.id, objective="Edit the scene", root_agent="leader",
        active_skill_ids=["method.drama_series"],
    )
    work = service.agent_runtime.create_work_order(
        run.id, sender="leader", recipient_role="editor", objective="Polish current timing", why_now="The selected clips are ready",
        production_skill_role=ProductionSkillRole.DIRECTOR, production_prompt_stage=ProductionPromptStage.POST,
    )
    with trace_context(work_order_id=work.id, run_id=run.id):
        result = service.get_director_advice(document.project.id, document.shots[0].id, DirectorAdviceRequest())
        explicit = service.get_director_advice(document.project.id, document.shots[0].id, DirectorAdviceRequest(
            method_skill_ids=[], production_prompt_stage=ProductionPromptStage.STORYBOARD,
        ))
    assert result.method_skill_ids == ["method.drama_series"]
    assert result.production_prompt_stage == ProductionPromptStage.POST
    assert "使用当前已选视频、实际媒体时间和本集编辑时间线" in fake.generations[0][0]
    assert "## 表演设计" not in fake.generations[0][0]
    payload = json.loads(fake.generations[0][1])
    assert payload["current_group"]["ordered_shot_ids"] == unit.shot_ids
    assert explicit.method_skill_ids == []
    assert explicit.production_prompt_stage == ProductionPromptStage.STORYBOARD


def test_actual_media_boundary_uses_current_trimmed_endpoint_and_rejects_stale_evidence(tmp_path: Path) -> None:
    service, _fake, document, _generation, _unit, _request = _fixture(tmp_path)
    first, second = document.shots[:2]
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[first.id], candidate_count=1))
    take = service.drain(document.project.id, 50).project.takes[0]
    document = service.get_project(document.project.id).document
    editor = EditorProject.model_validate(read_editorial(service, document.project.id).project)
    clip = editor.timelines[0].clips[0]
    clip.trimStart, clip.duration, clip.speed = 1.0, 1.5, 2.0
    image_path = tmp_path / "observed-end.png"
    image_path.write_bytes(b"existing-frame")
    report = VideoInspectionReport.model_validate({
        "id": "inspection-end", "project_id": document.project.id, "shot_id": first.id, "take_id": take.id,
        "scopes": ["INTRA_SHOT"], "duration_seconds": take.duration_seconds,
        "width": take.width, "height": take.height, "fps": 24, "audio_present": True,
        "observed_source_in_seconds": 0, "observed_source_out_seconds": take.duration_seconds,
        "covered_shot_ids": [first.id], "source_context": inspection_source_context(document, take, [first]),
        "samples": [{"source_take_id": take.id, "timestamp_seconds": value, "image_path": str(image_path), "reasons": ["endpoint"]} for value in [3.9, 4.0]],
        "contact_sheet_path": str(image_path), "technical_checks": [], "semantic_status": "COMPLETE",
        "observations": [{"start_seconds": 3.85, "end_seconds": 4.0, "description": "The blue plate is held in the left hand.", "confidence": "HIGH"}],
        "summary": "A local observation", "created_at": "2026-09-11T00:00:00Z",
    })
    result = previous_adopted_media_boundary(document, target_shot_id=second.id, editorial_project=editor.model_dump(mode="json"), takes=[take], inspections=[report])
    assert result is not None
    assert result["playback_end_source_seconds"] == 4.0
    assert result["observation_status"] == "TIMESTAMPED_VISUAL_EVIDENCE"
    assert result["audio_observation_status"] == "UNKNOWN"
    observations = cast(list[dict[str, JsonValue]], result["observations"])
    assert observations[0]["description"] == "The blue plate is held in the left hand."
    clip.reversed = True
    reversed_result = previous_adopted_media_boundary(document, target_shot_id=second.id, editorial_project=editor.model_dump(mode="json"), takes=[take], inspections=[report])
    assert reversed_result is not None and reversed_result["playback_end_source_seconds"] == 1.0
    assert reversed_result["observation_status"] == "UNKNOWN"
    clip.reversed = False
    first.revision += 1
    next(shot for shot in document.shots if shot.id == first.id).revision = first.revision
    stale = previous_adopted_media_boundary(document, target_shot_id=second.id, editorial_project=editor.model_dump(mode="json"), takes=[take], inspections=[report])
    assert stale is not None and stale["observation_status"] == "UNKNOWN"
