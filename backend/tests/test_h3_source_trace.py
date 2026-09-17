"""Source polling stays read-only; actual compiler methods remain observable."""

import math
from pathlib import Path

from pydantic import JsonValue
import pytest

from short_drama.agent_runtime.models import ArtifactRef
from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.language_models import H3GenerationUnitContextRequest
from short_drama.providers.h3_context_ir import H3ContextIRPending, compile_h3_context_ir
from short_drama.providers.h3_language_prompt import build_generation_unit_source_input
from short_drama.studio_trace import production_trace, trace_context
from tests.test_h3_context_ir import FakeIR, due
from tests.test_h3_generation_unit_language import _fixture


def test_repeated_runner_context_and_route_reads_do_not_load_compiler_skills(tmp_path: Path) -> None:
    service, language, document, generation, unit, _request = _fixture(tmp_path)
    project_id = document.project.id
    prepared = service.prepare_h3_group(project_id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    assert prepared.route_plan_id
    refs = [
        ArtifactRef(project_id=project_id, artifact_type="h3_route_plan_index", artifact_id=project_id),
        ArtifactRef(project_id=project_id, artifact_type="h3_route_plan", artifact_id=prepared.route_plan_id),
        ArtifactRef(project_id=project_id, artifact_type="h3_group_operation", artifact_id=prepared.operation_id),
    ]
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=project_id, objective="Continue the prepared video group", root_agent="leader")
    runtime.checkpoint(run.id, current_focus=run.objective, artifact_refs=refs)
    request = H3GenerationUnitContextRequest(generation_plan_document_id=generation.document.id,
        generation_plan_version_id=generation.version.id, generation_unit_id=unit.id)
    before_source = service.get_h3_generation_unit_context(project_id, request).source_context
    before_project = service.repository.get_project(project_id)
    before_route = service.repository.get_h3_route_plan(prepared.route_plan_id)
    before_operation = service.repository.get_h3_group_operation(prepared.operation_id)
    before_language_calls = list(language.calls)
    entries: list[tuple[str, dict[str, JsonValue]]] = []
    directory = tmp_path / "read-trace"

    with production_trace(directory, lambda kind, value: entries.append((kind, value))), trace_context(agent_role="leader"):
        for _ in range(4):
            assert service.get_h3_generation_unit_context(project_id, request).source_context == before_source
            route = service.get_h3_production_route_plan(project_id, prepared.route_plan_id)
            assert route.source_checks[0].status == "CURRENT"
            assert service.list_h3_production_route_plans(project_id).source_checks[0].status == "CURRENT"
            # This is the same context projection used by the Runner's polling loop.
            view = service.get_agent_run(run.id)
            assert {artifact.ref.artifact_type for artifact in view.context.artifacts} >= {
                "h3_route_plan_index", "h3_route_plan", "h3_group_operation",
            }

    assert not any(kind == "skills.loaded" for kind, _ in entries)
    assert not list(directory.glob("*-skills.loaded.json"))
    assert language.calls == before_language_calls
    assert service.repository.get_project(project_id) == before_project
    assert service.repository.get_h3_route_plan(prepared.route_plan_id) == before_route
    assert service.repository.get_h3_group_operation(prepared.operation_id) == before_operation
    assert service.repository.list_tasks(project_id) == []


def test_real_language_compile_preserves_source_bytes_and_selected_method_trace(tmp_path: Path) -> None:
    service, language, document, _generation, unit, request = _fixture(tmp_path)
    entries: list[tuple[str, dict[str, JsonValue]]] = []
    spec = service.get_production_spec(document.project.id).spec
    directory = tmp_path / "compile-trace"
    with production_trace(directory, lambda kind, value: entries.append((kind, value))), trace_context(agent_role="video-prompt-compiler"):
        source = build_generation_unit_source_input(document, unit, document.shots[:2],
            route=request.route, duration_seconds=math.ceil(unit.requested_duration_seconds),
            production_spec=spec, media_inputs=[], aspect_ratio=spec.aspect_ratio)
        assert entries == []
        result = service.compile_h3_prompt_with_language_model(document.project.id, request)

    assert len(language.calls) == 1
    instructions, actual_source = language.calls[0]
    assert actual_source == source
    assert "角色声音签名" in instructions
    assert result.method_skill_ids == request.method_skill_ids == ["method.drama_series"]
    loads = [value for kind, value in entries if kind == "skills.loaded"]
    assert len(loads) == 1
    assert loads[0]["agent_role"] == "video-prompt-compiler"
    assert loads[0]["roles"] == ["VIDEO_PROMPT_COMPILER"]
    assert loads[0]["stage"] == "VISUAL_GEN"
    skills = loads[0]["skills"]
    assert isinstance(skills, list)
    assert any(isinstance(skill, dict) and skill["id"] == "method.drama_series" for skill in skills)
    assert len(list(directory.glob("*-skills.loaded.json"))) == 1


def test_context_ir_source_and_polling_do_not_load_unused_language_methods(tmp_path: Path) -> None:
    service, language, document, _generation, _unit, request = _fixture(tmp_path)
    client = FakeIR(language.output_text)
    entries: list[tuple[str, dict[str, JsonValue]]] = []
    with production_trace(tmp_path / "ir-trace", lambda kind, value: entries.append((kind, value))):
        with pytest.raises(H3ContextIRPending):
            compile_h3_context_ir(service, document.project.id, request, client=client)
        due(service.workspace_root / "h3-context-ir")
        result = compile_h3_context_ir(service, document.project.id, request, client=client)
        cached = compile_h3_context_ir(service, document.project.id, request)

    assert result.compiled_prompt == cached.compiled_prompt == language.output_text
    assert len(client.submitted) == len(client.queries) == 1
    assert language.calls == []
    assert not any(kind == "skills.loaded" for kind, _ in entries)
    assert any(kind == "h3.context_ir.output" for kind, _ in entries)
