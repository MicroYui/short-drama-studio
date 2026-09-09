"""Clip-level prompt compilation and durable H3 handoff, with no real providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import (
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
    H3ProviderConfigUpdate,
    H3ShotRouteDirective,
)
from short_drama.language_models import (
    H3GenerationUnitContextRequest,
    LanguagePromptCompileRequest,
    LanguageProviderConfigUpdate,
)
from short_drama.models import (
    ApprovalStatus,
    AssetMediaType,
    Freshness,
    ProductionDocumentOrigin,
    VideoGenerationRoute,
)
from short_drama.production_contracts import (
    ClipGroup,
    GenerationPlanCompileRequest,
    ProductionAudioDirection,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    StoryboardContent,
    StoryboardCreateRequest,
)
from short_drama.providers.h3_prompt_skill import validate_h3_generation_unit_prompt_ir
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.reference_planner import ShotReferencePlanUpdateRequest
from short_drama.service import ProviderOperationError, ShortDramaService, WorkflowConflictError
from short_drama.tools.contracts import ToolCallStatus


class FakePromptCompiler:
    def __init__(self) -> None:
        self.output_text = ""
        self.original_visual = ""
        self.calls: list[tuple[str, str]] = []
        self.output_limits: list[int] = []

    def generate_text(self, *, instructions: str, input_text: str, max_output_tokens: int = 2200) -> str:
        self.calls.append((instructions, input_text))
        self.output_limits.append(max_output_tokens)
        return self.output_text


def _fixture(tmp_path: Path, *, verbose: bool = False, aspect_ratio: str = "9:16",
             recorded_constraints: list[str] | None = None):
    fake = FakePromptCompiler()
    service = ShortDramaService(
        database_path=tmp_path / "data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: fake,
    )
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    document = service.initialize_fixture().document
    project_id = document.project.id
    original_visual = document.shots[0].visual
    fake.original_visual = original_visual
    if verbose:
        document.shots[0].visual = " ".join([original_visual] * 1000)
    document.shots[2].visual += " OTHER_CLIP_ONLY"
    service.repository.save_project(document)
    spec = service.create_production_spec(project_id, ProductionSpecCreateRequest(origin=ProductionDocumentOrigin.AGENT, spec=ProductionSpecContent(
        project_id=project_id,
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        title="Clip compilation fixture",
        production_scope="Compile the existing screenplay into video clips.",
        production_mode="Realistic drama, preserving declared editorial cuts.",
        genre_and_setting="A confrontation in a convenience store on a rainy night.",
        language="zh-CN", aspect_ratio=aspect_ratio,
        target_duration_seconds=document.project.target_duration_seconds,
        duration_intent="Preserve natural source timing.",
        user_constraints=recorded_constraints or [],
        audio=ProductionAudioDirection(
            dialogue_intent="Native Mandarin dialogue with stable character voices.",
            ambience_intent="Preserve rain and refrigerator hum; no added score.",
        ),
    )))
    script = next(v for v in document.script_versions if v.id == document.project.current_script_version_id)
    shot_groups = [document.shots[:2], *([shot] for shot in document.shots[2:])]
    board = service.create_storyboard(project_id, StoryboardCreateRequest(origin=ProductionDocumentOrigin.AGENT, storyboard=StoryboardContent(
        project_id=project_id, title="A grouped screenplay",
        script_version_id=script.id, bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        script_line_ids=[line.id for line in script.lines], shots=document.shots,
        clip_groups=[ClipGroup(
            id=f"clip-{index}", title=f"Continuous action {index}",
            shot_ids=[shot.id for shot in shots],
            duration_seconds=int(sum(shot.duration_seconds for shot in shots)),
            generation_intent="Preserve all actions and dialogue in their existing order.",
            continuity_in=[shots[0].continuity.action_in],
            continuity_out=[shots[-1].continuity.action_out],
        ) for index, shots in enumerate(shot_groups, start=1)],
        computed_duration_seconds=sum(shot.duration_seconds for shot in document.shots),
    )))
    generation = service.compile_generation_plan(project_id, GenerationPlanCompileRequest(
        origin=ProductionDocumentOrigin.AGENT,
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
    ))
    document = service.get_project(project_id).document
    unit = generation.generation_plan.units[0]
    short_shots = [shot.model_copy(deep=True) for shot in document.shots[:2]]
    short_shots[0].visual = original_visual
    fake.output_text = build_h3_generation_unit_prompt(document, unit, short_shots)
    request = LanguagePromptCompileRequest(
        generation_plan_document_id=generation.document.id,
        generation_plan_version_id=generation.version.id,
        generation_unit_id=unit.id,
        method_skill_ids=["method.drama_series"],
    )
    return service, fake, document, generation, unit, request


def test_http_compiles_complete_clip_with_selected_method_and_scoped_sources(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, request = _fixture(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(
            f"/api/short-drama/projects/{document.project.id}/h3/prompt/compile",
            json=request.model_dump(mode="json"),
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["generation_unit_id"] == unit.id
    assert result["shot_ids"] == unit.shot_ids
    assert result["compiled_prompt"] == fake.output_text
    assert result["method_skill_ids"] == request.method_skill_ids
    assert len(fake.calls) == 1
    instructions, input_text = fake.calls[0]
    assert "角色声音签名" in instructions  # The selected Design source is actually loaded.
    payload = json.loads(input_text)
    assert payload["model_lock"] == {"provider": "minimax", "model": "MiniMax-H3", "resolution": "768P"}
    assert payload["operation_scope"]["h3_upload_or_submit"] is False
    assert payload["operation_scope"]["language_compilation_requested"] is True
    assert [row["locked_shot"]["id"] for row in payload["locked_shots"]] == unit.shot_ids
    assert payload["production_direction"]["audio"]["dialogue_intent"].startswith("Native Mandarin")
    assert "OTHER_CLIP_ONLY" not in input_text
    assert "source_prompt" not in payload  # Source facts are present once, not a duplicated draft.
    assert service.repository.list_tasks(document.project.id) == []


def test_long_source_can_be_compiled_without_truncating_locked_facts(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, request = _fixture(tmp_path, verbose=True)
    with pytest.raises(ValueError, match="exceeds 7000"):
        build_h3_generation_unit_prompt(document, unit, document.shots[:2])
    result = service.compile_h3_prompt_with_language_model(document.project.id, request)
    payload = json.loads(fake.calls[0][1])
    assert payload["locked_shots"][0]["locked_shot"]["visual"] == document.shots[0].visual
    assert len(fake.calls[0][1]) > 7000
    assert len(result.compiled_prompt) <= 7000
    assert result.compiled_prompt == fake.output_text
    assert fake.output_limits == [8000]


def test_compile_scope_keeps_creative_constraints_and_identifies_current_text_only_operation(tmp_path: Path) -> None:
    constraints = ["当时只建立分镜，不进入资产或媒体制作。", "保持所有逐字对白不变。"]
    service, fake, document, _generation, _unit, request = _fixture(
        tmp_path, recorded_constraints=constraints,
    )
    service.compile_h3_prompt_with_language_model(document.project.id, request)
    instructions, text = fake.calls[0]
    payload = json.loads(text)
    assert payload["production_direction"]["user_constraints"] == constraints
    assert payload["operation_scope"]["current_tool"] == "route.compile_h3_prompt"
    assert payload["operation_scope"]["media_generation"] is False
    assert payload["model_lock"]["model"] == "MiniMax-H3"
    assert "阶段备注不能替代当前 Tool" in instructions


def test_compile_validation_returns_model_diagnostic_with_format_error(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(tmp_path)
    fake.output_text = "Cannot compile: the supplied model target is missing."
    with pytest.raises(ProviderOperationError, match="the supplied model target is missing"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)


def test_compiler_revision_feedback_reaches_the_language_model(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, request = _fixture(tmp_path)
    request.revision_instructions = (
        "Previous output exceeded 7000 characters. Condense repeated subject and scene definitions; keep all locked timing and dialogue."
    )
    result = service.compile_h3_prompt_with_language_model(document.project.id, request)
    payload = json.loads(fake.calls[0][1])
    assert payload["compiler_feedback"] == request.revision_instructions
    assert [row["locked_shot"]["id"] for row in payload["locked_shots"]] == unit.shot_ids
    assert result.candidate_id == f"direct:{unit.id}"


def test_unit_context_tool_exposes_scoped_facts_and_real_approved_media_without_provider_calls(tmp_path: Path) -> None:
    service, fake, document, generation, unit, _request = _fixture(tmp_path)
    for index, asset in enumerate(document.assets):
        version = asset.versions[0]
        version.media_type = AssetMediaType.IMAGE
        version.local_path = None
        version.external_url = f"https://example.test/approved-{index}.png"
        version.approval_status = ApprovalStatus.APPROVED
        version.freshness = Freshness.CURRENT
        asset.approved_version_id = version.id
        asset.current_version_id = version.id
    service.repository.save_project(document)
    result = service.tool_registry.invoke("route.get_generation_unit_context", {
        "project_id": document.project.id,
        "request": {
            "generation_plan_document_id": generation.document.id,
            "generation_plan_version_id": generation.version.id,
            "generation_unit_id": unit.id,
        },
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    source = result.output["source_context"]
    assert [row["locked_shot"]["id"] for row in source["locked_shots"]] == unit.shot_ids
    assert "OTHER_CLIP_ONLY" not in json.dumps(result.output)
    assert result.output["media_candidates"]
    versions = {v.id: v for a in document.assets for v in a.versions}
    for candidate in result.output["media_candidates"]:
        media = candidate["media_input"]
        assert media["source"]["external_url"] == versions[media["asset_version_id"]].external_url
        assert set(candidate["serves_shot_ids"]) <= set(unit.shot_ids)
    assert fake.calls == []
    assert service.repository.list_tasks(document.project.id) == []
    assert {ref.artifact_type for ref in result.artifact_refs} == {"generation_plan", "generation_unit"}


def test_unit_context_rejects_stale_plan_before_returning_media(tmp_path: Path) -> None:
    service, fake, document, generation, unit, _request = _fixture(tmp_path)
    document.shots[0].revision += 1
    service.repository.save_project(document)
    with pytest.raises(WorkflowConflictError, match="outdated shot"):
        service.get_h3_generation_unit_context(document.project.id, H3GenerationUnitContextRequest(
            generation_plan_document_id=generation.document.id,
            generation_plan_version_id=generation.version.id,
            generation_unit_id=unit.id,
        ))
    assert fake.calls == []


def test_clip_compile_rejects_stale_source_before_language_call(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(tmp_path)
    document.shots[1].revision += 1
    service.repository.save_project(document)
    with pytest.raises(WorkflowConflictError, match="outdated shot"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert fake.calls == []


def test_recompiled_plan_uses_current_reference_revision_without_rewriting_storyboard(tmp_path: Path) -> None:
    service, fake, document, generation, unit, request = _fixture(tmp_path)
    shot = document.shots[0]
    reference_plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    original_storyboard = next(
        item for item in document.production_documents if item.id == document.storyboard_document_id
    ).model_dump(mode="json")
    service.update_shot_reference_plan(document.project.id, shot.id, ShotReferencePlanUpdateRequest(
        requirements=reference_plan.requirements,
        action_beats=reference_plan.action_beats,
        interactions=reference_plan.interactions,
        max_reference_images=reference_plan.max_reference_images,
        planning_notes="Use the approved references for the current video preparation.",
    ))
    with pytest.raises(WorkflowConflictError, match="outdated shot"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert fake.calls == []

    rebuilt = service.compile_generation_plan(document.project.id, GenerationPlanCompileRequest(
        origin=ProductionDocumentOrigin.AGENT,
        base_version_id=generation.version.id,
        storyboard_document_id=generation.generation_plan.storyboard_document_id,
        storyboard_version_id=generation.generation_plan.storyboard_version_id,
        production_spec_document_id=generation.generation_plan.production_spec_document_id,
        production_spec_version_id=generation.generation_plan.production_spec_version_id,
    ))
    updated = service.get_project(document.project.id).document
    current_shot = next(item for item in updated.shots if item.id == shot.id)
    new_unit = rebuilt.generation_plan.units[0]
    assert current_shot.revision == shot.revision + 1
    assert new_unit.shot_windows[0].shot_revision == current_shot.revision
    assert new_unit.shot_ids == unit.shot_ids
    assert new_unit.requested_duration_seconds == unit.requested_duration_seconds
    assert next(
        item for item in updated.production_documents if item.id == updated.storyboard_document_id
    ).model_dump(mode="json") == original_storyboard
    request.generation_plan_version_id = rebuilt.version.id
    result = service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert result.compiled_prompt == fake.output_text
    assert len(fake.calls) == 1


def test_clip_compile_rejects_unusable_media_before_language_call(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(tmp_path)
    request.route = VideoGenerationRoute.REFERENCE
    with pytest.raises(WorkflowConflictError, match="at least one reference"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert fake.calls == []


def test_clip_compile_reports_unsupported_format_before_language_call(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(
        tmp_path, aspect_ratio="16:9"
    )
    with pytest.raises(WorkflowConflictError, match="aspect ratios"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert fake.calls == []


def test_clip_compile_rejects_oversized_output(tmp_path: Path) -> None:
    service, fake, document, _generation, _unit, request = _fixture(tmp_path)
    accepted = fake.output_text
    fake.output_text += "x" * 7000
    with pytest.raises(ProviderOperationError, match="exceeds 7000") as failure:
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert str(failure.value).endswith(fake.output_text)
    request = LanguagePromptCompileRequest.model_validate({
        **request.model_dump(), "source_prompt": fake.output_text,
        "revision_instructions": "Revise the complete oversized draft; preserve all locked facts and required fields.",
    })
    fake.output_text = accepted
    revised = service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert json.loads(fake.calls[-1][1])["source_prompt"] == request.source_prompt
    assert revised.compiled_prompt == accepted
    assert service.repository.list_tasks(document.project.id) == []


def test_clip_speaker_validation_accepts_canonical_subject_labels_in_english_prose(tmp_path: Path) -> None:
    _service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    script = next(v for v in document.script_versions if v.id == document.project.current_script_version_id)
    lines = [line for line in script.lines if any(line.id in shot.dialogue_line_ids for shot in document.shots[:2])]
    references: list[H3MediaInput] = []
    replacements: dict[str, str] = {}
    for line in lines:
        if line.speaker in replacements:
            continue
        asset = next(a for a in document.assets if a.bible_entity_id == line.speaker_character_id)
        index = len(references) + 1
        references.append(H3MediaInput(
            kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url=f"https://example.test/actor-{index}.png"),
            subject_asset_id=asset.id, reference_alias=f"actor_{index}",
        ))
        replacements[line.speaker] = f"<Subject {index}>"
    prompt = build_h3_generation_unit_prompt(
        document, unit, document.shots[:2], route=VideoGenerationRoute.REFERENCE, media_inputs=references,
    )
    for name, label in replacements.items():
        prompt = prompt.replace(name, label)
    validate_h3_generation_unit_prompt_ir(
        prompt, document, unit, document.shots[:2], route=VideoGenerationRoute.REFERENCE,
        duration_seconds=int(unit.requested_duration_seconds), media_inputs=references,
    )
    header, body = prompt.split("detailed_description:", 1)
    for label in replacements.values():
        body = body.replace(label, "an unbound performer")
    with pytest.raises(ValueError, match="dialogue"):
        validate_h3_generation_unit_prompt_ir(
            header + "detailed_description:" + body, document, unit, document.shots[:2],
            route=VideoGenerationRoute.REFERENCE, duration_seconds=int(unit.requested_duration_seconds),
            media_inputs=references,
        )


def test_authored_clip_and_reference_order_survive_route_save_and_enqueue(tmp_path: Path) -> None:
    service, fake, document, generation, unit, request = _fixture(tmp_path, verbose=True)
    references = []
    for name, asset in zip(("second", "first"), reversed(document.assets[:2]), strict=True):
        version = asset.versions[0]
        version.media_type = AssetMediaType.IMAGE
        version.local_path = None
        version.external_url = f"https://example.test/{name}.png"
        version.approval_status = ApprovalStatus.APPROVED
        version.freshness = Freshness.CURRENT
        asset.approved_version_id = version.id
        asset.current_version_id = version.id
        references.append(H3MediaInput(
            kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url=version.external_url),
            subject_asset_id=asset.id, asset_version_id=version.id, reference_alias=name,
        ))
    service.repository.save_project(document)
    request.route = VideoGenerationRoute.REFERENCE
    request.media_inputs = references
    # A concise authored fixture has the same facts as the repeated source text.
    short_shots = [shot.model_copy(deep=True) for shot in document.shots[:2]]
    short_shots[0].visual = fake.original_visual
    fake.output_text = build_h3_generation_unit_prompt(
        document, unit, short_shots, route=request.route, media_inputs=references
    )
    result = service.compile_h3_prompt_with_language_model(document.project.id, request)
    payload = json.loads(fake.calls[-1][1])
    assert all(
        binding["required_binding"] == f"{binding['reference_alias']} = {binding['prompt_label']}"
        for binding in payload["route_media_bindings"]
    )
    broken_binding = fake.output_text.replace("@second = <Subject 1>", "@second = <Picture 1>")
    with pytest.raises(ValueError, match=r"@second = <Subject 1>; source upload is <Picture 1>"):
        validate_h3_generation_unit_prompt_ir(
            broken_binding, document, unit, short_shots, route=request.route,
            duration_seconds=int(unit.requested_duration_seconds), media_inputs=references,
        )
    planned = service.compile_h3_production_route_plan(document.project.id, H3ProductionRoutePlanRequest(
        generation_plan_document_id=generation.document.id,
        generation_plan_version_id=generation.version.id,
        unit_ids=[unit.id], use_language_agent=True, consider_video_guides=False,
        shot_directives=[H3ShotRouteDirective(
            generation_unit_id=unit.id, candidate_id=result.candidate_id,
            prompt_override=result.compiled_prompt, media_inputs=result.media_inputs,
        )],
    )).plan
    decision = planned.decisions[0]
    assert decision.prompt == result.compiled_prompt
    assert decision.media_inputs == references
    assert decision.shot_ids == unit.shot_ids
    read_plan = service.tool_registry.invoke("route.get_h3_plan", {
        "project_id": document.project.id, "plan_id": planned.id,
    })
    assert read_plan.status == ToolCallStatus.SUCCEEDED
    assert read_plan.output == {"plan": planned.model_dump(mode="json")}
    assert read_plan.artifact_refs[0].artifact_id == planned.id
    listed = service.tool_registry.invoke("route.list_h3_plans", {"project_id": document.project.id})
    index_ref = next(ref for ref in listed.artifact_refs if ref.artifact_type == "h3_route_plan_index")
    index = ProjectArtifactResolver(service.repository).summarize_artifact(index_ref)
    assert index is not None
    assert json.loads(index.summary)["plans"][0]["unit_ids"] == [unit.id]
    assert len(fake.calls) == 1
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=tmp_path / "workspace")
    persisted = restarted.repository.list_h3_route_plans(document.project.id)[-1]
    assert persisted.decisions[0].prompt == result.compiled_prompt
    assert persisted.decisions[0].media_inputs == references
    restarted.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    executed = restarted.execute_h3_production_route_plan(document.project.id, persisted.id, H3ProductionRouteExecuteRequest(
        expected_state_revision=persisted.state_revision, enqueue_new_generations=True,
        inspect_reused_takes=False, semantic_review=False, rebuild_manifest=False,
    ))
    assert len(executed.queued_tasks) == 1
    task = executed.queued_tasks[0]
    assert task.generation_unit_id == unit.id
    assert [window.shot_id for window in task.shot_windows] == unit.shot_ids
    run = next(item for item in restarted.repository.list_provider_runs(document.project.id) if item.task_id == task.id)
    assert run.prompt == result.compiled_prompt
    assert run.media_inputs == references
    assert len(fake.calls) == 1  # Route execution does not regenerate the prompt.


def test_clip_validator_rejects_reordered_windows(tmp_path: Path) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    prompt = fake.output_text.replace("[Shot 1] [0.00-5.00 seconds]", "SWAP_MARKER")
    prompt = prompt.replace("[Shot 2] [5.00-13.00 seconds]", "[Shot 1] [0.00-5.00 seconds]")
    prompt = prompt.replace("SWAP_MARKER", "[Shot 2] [5.00-13.00 seconds]")
    with pytest.raises(ValueError, match="reordered editorial"):
        validate_h3_generation_unit_prompt_ir(prompt, document, unit, document.shots[:2],
                                             route=VideoGenerationRoute.DIRECT, duration_seconds=13)


def test_clip_validator_rejects_dialogue_moved_out_of_its_shot(tmp_path: Path) -> None:
    _service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    script = next(
        item for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    line_ids = set(document.shots[1].dialogue_line_ids)
    line = next(item for item in script.lines if item.id in line_ids)
    prompt = fake.output_text.replace(line.spoken_text, "")
    prompt = prompt.replace("[Shot 2]", f"{line.spoken_text} [Shot 2]", 1)
    assert line.spoken_text in prompt
    with pytest.raises(ValueError, match="changed or omitted dialogue"):
        validate_h3_generation_unit_prompt_ir(
            prompt,
            document,
            unit,
            document.shots[:2],
            route=VideoGenerationRoute.DIRECT,
            duration_seconds=13,
        )


@pytest.mark.parametrize("payload", [
    {}, {"generation_plan_document_id": "plan"},
    {"shot_id": "shot", "generation_plan_document_id": "plan", "generation_plan_version_id": "v1", "generation_unit_id": "clip"},
])
def test_compile_request_requires_one_complete_target(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        LanguagePromptCompileRequest.model_validate(payload)
