"""Current AceData output options survive real assembly, persistence and Fake transport."""

import json
from pathlib import Path

import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProbeRequest, H3ProviderConfigUpdate, H3ProductionRoutePlanRequest, H3ShotRouteDirective, H3ProductionRouteExecuteRequest
from short_drama.models import TaskStatus, VideoGenerationRoute
from short_drama.providers.minimax_h3_compiler import H3CapabilityError, build_h3_generation_unit_prompt, compile_h3_request
from short_drama.service import ShortDramaService
from tests.test_h3_generation_unit_language import _fixture
from tests.test_short_drama_h3 import FakeH3Client, _service


@pytest.mark.parametrize("ratio", ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"])
def test_every_documented_fixed_shape_reaches_2k_request(ratio: str) -> None:
    request = compile_h3_request(route=VideoGenerationRoute.DIRECT, prompt="One clear visible action.",
        duration_seconds=5, media_inputs=[], resolve_url=lambda _media: "", resolution="2K", aspect_ratio=ratio)
    assert request.model_dump(mode="json")["resolution"] == "2K" and request.ratio == ratio


def test_frame_adaptive_and_unsupported_options_fail_before_upload() -> None:
    frame = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(external_url="https://example.test/frame.png"))
    uploads: list[str] = []
    def url(media: H3MediaInput) -> str:
        uploads.append(media.role.value)
        return media.source.external_url or ""
    for resolution, ratio, route, media in [("4K", "16:9", VideoGenerationRoute.DIRECT, []),
        ("2K", "adaptive", VideoGenerationRoute.DIRECT, []),
        ("2K", "16:9", VideoGenerationRoute.FRAME_FIRST, [frame])]:
        with pytest.raises(H3CapabilityError):
            compile_h3_request(route=route, prompt="One action.", duration_seconds=5, media_inputs=media,
                resolve_url=url, resolution=resolution, aspect_ratio=ratio)
    assert uploads == []
    request = compile_h3_request(route=VideoGenerationRoute.FRAME_FIRST, prompt="Continue the starting scene.",
        duration_seconds=5, media_inputs=[frame], resolve_url=url, resolution="2K", aspect_ratio="adaptive")
    assert request.ratio == "adaptive" and request.resolution == "2K"


def test_landscape_group_2k_reaches_one_saved_task_and_fake_provider_after_restart(tmp_path: Path) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path, aspect_ratio="16:9")
    fake = FakeH3Client()
    service = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language, h3_client_factory=lambda _key: fake)
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-token"))
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[], resolution="2K", enqueue=True))
    assert result.status == "WAITING_MEDIA", result.error
    payload = json.loads(language.calls[0][1])
    assert payload["model_lock"]["resolution"] == "2K" and payload["aspect_ratio"] == "16:9"
    operation = service.repository.get_h3_group_operation(result.operation_id)
    assert operation and "16:9" in (operation.compiled_prompt or "")
    assert result.route_plan_id
    plan = service.repository.get_h3_route_plan(result.route_plan_id)
    assert plan and plan.resolution == "2K" and plan.decisions[0].aspect_ratio == "16:9"
    assert plan.estimated_cost_usd is None and plan.estimated_cost_credits is None
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language, h3_client_factory=lambda _key: fake)
    resumed = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=result.operation_id, enqueue=True))
    assert resumed.task_ids == result.task_ids and len(language.calls) == 1
    task_id = resumed.task_ids[0]
    assert restarted.advance_task(task_id).status == TaskStatus.SUBMITTING
    assert restarted.advance_task(task_id).status == TaskStatus.ACKNOWLEDGED
    assert len(fake.submitted_requests) == 1
    sent = fake.submitted_requests[0]
    assert sent.resolution == "2K" and sent.ratio == "16:9"
    assert restarted.get_project(document.project.id).document.project.aspect_ratio == "16:9"


def test_single_probe_resolution_shape_and_default_compatibility(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    document = service.initialize_fixture().document
    request = H3ProbeRequest(shot_id=document.shots[0].id, resolution="2K", aspect_ratio="4:3")
    preview = service.preview_h3_probe(document.project.id, request)
    assert preview.request.resolution == "2K" and preview.request.ratio == "4:3"
    assert "Compose the shot for a 4:3 frame" in preview.prompt
    assert "requested output shape takes precedence" in preview.prompt
    probe = service.enqueue_h3_probe(document.project.id, request)
    service.advance_task(probe.task.id)
    service.advance_task(probe.task.id)
    assert fake.submitted_requests[0].ratio == "4:3" and fake.submitted_requests[0].resolution == "2K"
    legacy = service.preview_h3_probe(document.project.id, H3ProbeRequest(shot_id=document.shots[0].id))
    assert legacy.request.resolution == "768P" and legacy.request.ratio == "9:16"
    assert service.get_project(document.project.id).document.project.aspect_ratio == "9:16"


def test_legacy_saved_operation_without_output_fields_reuses_existing_route(tmp_path: Path) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], route=VideoGenerationRoute.DIRECT, media_inputs=[]))
    operation = service.repository.get_h3_group_operation(prepared.operation_id)
    assert operation and prepared.route_plan_id
    operation.input_context.pop("resolution")
    operation.input_context.pop("aspect_ratio")
    service.repository.save_h3_group_operation(operation)
    plan = service.repository.get_h3_route_plan(prepared.route_plan_id)
    assert plan
    plan.decisions[0].aspect_ratio = None
    service.repository.save_h3_route_plan(plan)
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert resumed.error is None and resumed.route_plan_id == prepared.route_plan_id
    assert len(language.calls) == 1


def test_single_route_plan_keeps_square_2k_through_execution(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    document = service.initialize_fixture().document
    shot = document.shots[0]
    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id], resolution="2K", aspect_ratio="1:1",
            use_language_agent=False,
            shot_directives=[H3ShotRouteDirective(shot_id=shot.id, candidate_id=f"direct:{shot.id}")],
        ),
    )
    assert compiled.plan.decisions[0].aspect_ratio == "1:1"
    assert "Compose the shot for a 1:1 frame" in compiled.plan.decisions[0].prompt
    assert compiled.plan.estimated_cost_usd is None
    executed = service.execute_h3_production_route_plan(
        document.project.id, compiled.plan.id,
        H3ProductionRouteExecuteRequest(
            expected_state_revision=compiled.plan.state_revision,
            enqueue_new_generations=True, inspect_reused_takes=False, rebuild_manifest=False,
        ),
    )
    task = executed.queued_tasks[0]
    service.advance_task(task.id)
    service.advance_task(task.id)
    assert fake.submitted_requests[0].ratio == "1:1"
    assert fake.submitted_requests[0].resolution == "2K"
    assert service.get_project(document.project.id).document.project.aspect_ratio == "9:16"
    capability = service.get_h3_capability_profile(document.project.id)
    assert capability.supported_resolutions == ["768P", "2K"]
    assert "adaptive" in capability.supported_aspect_ratios


@pytest.mark.parametrize("route,role", [
    (VideoGenerationRoute.REFERENCE, H3MediaRole.REFERENCE_IMAGE),
    (VideoGenerationRoute.FRAME_FIRST, H3MediaRole.FIRST_FRAME),
])
def test_adaptive_media_group_is_compiled_and_submitted_with_same_shape(
    tmp_path: Path, route: VideoGenerationRoute, role: H3MediaRole,
) -> None:
    service, language, document, _generation, unit, _request = _fixture(tmp_path)
    fake = FakeH3Client()
    service = ShortDramaService(
        database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: language, h3_client_factory=lambda _key: fake,
    )
    service.update_h3_config(H3ProviderConfigUpdate(api_key="test-token"))
    media = H3MediaInput(kind=H3MediaKind.IMAGE, role=role,
        source=H3MediaSource(external_url="https://example.test/prepared-landscape.png"))
    language.output_text = build_h3_generation_unit_prompt(
        document, unit, document.shots[:2], route=route, media_inputs=[media], aspect_ratio="adaptive",
    )
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        shot_id=unit.shot_ids[0], route=route, media_inputs=[media], resolution="2K",
        aspect_ratio="adaptive", enqueue=True,
    ))
    assert result.status == "WAITING_MEDIA", result.error
    payload = json.loads(language.calls[0][1])
    assert payload["aspect_ratio"] == "adaptive"
    service.advance_task(result.task_ids[0])
    service.advance_task(result.task_ids[0])
    assert fake.submitted_requests[0].ratio == "adaptive"
    assert fake.submitted_requests[0].resolution == "2K"
    assert len(language.calls) == 1
