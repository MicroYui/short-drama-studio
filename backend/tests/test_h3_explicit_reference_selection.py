"""Saved prose inventory and the ordered actual H3 request are distinct choices."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest, _source_identity, prepare_h3_group
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProductionRouteExecuteRequest, H3ProviderConfigUpdate
from short_drama.models import ApprovalStatus, AssetMediaType, Freshness, ReferenceBinding, ShotReferenceCue, TaskStatus, VideoGenerationRoute
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools.contracts import ToolCallStatus
from tests.test_h3_context_ir import FakeIR, due
from tests.test_h3_generation_unit_language import FakePromptCompiler, _fixture
from tests.test_short_drama_h3 import FakeH3Client


def selection_fixture(tmp_path: Path, count: int = 10):
    service, fake, document, generation, unit, request = _fixture(tmp_path)
    asset = document.assets[0]
    template = asset.versions[0]
    media: list[H3MediaInput] = []
    cues: list[ShotReferenceCue] = []
    for index in range(count):
        path = tmp_path / f"view-{index}.png"
        Image.new("RGB", (32, 48), (index * 20, 80, 120)).save(path)
        version = template.model_copy(deep=True, update={
            "id": f"selected-{index}", "version": index + 2, "media_type": AssetMediaType.IMAGE,
            "approval_status": ApprovalStatus.DRAFT, "freshness": Freshness.CURRENT,
            "local_path": str(path), "external_url": None,
        })
        asset.versions.append(version)
        cues.append(ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
            alias=f"view{index}", purpose=f"View {index}"))
        media.append(H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path=str(path)), asset_version_id=version.id,
            subject_asset_id=asset.id, reference_alias=f"view{index}", binding_purpose=f"View {index}"))
    document.shots[0].references = cues
    for plan in document.reference_plan:
        if plan.shot_id in unit.shot_ids:
            plan.bindings = ([ReferenceBinding(kind=asset.kind, target_id=item.asset_version_id, purpose=item.binding_purpose)
                              for item in media] if plan.shot_id == unit.shot_ids[0] else [])
    service.repository.save_project(document)
    return service, fake, document, generation, unit, request, media


def test_registry_nine_of_ten_then_restart_keeps_order_source_and_one_task(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request, media = selection_fixture(tmp_path)
    # Mirrors Fresh5 t18: retain all ten saved cues, explicitly omit one image.
    selected = list(reversed(media[1:]))
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=selected)
    before = document.model_copy(deep=True)
    result = service.tool_registry.invoke("route.prepare_h3_group", {"project_id": document.project.id,
        "request": {"clip_group_id": unit.clip_group_id, "media_inputs": [item.model_dump(mode="json") for item in selected],
                    "resolution": "2K", "aspect_ratio": "9:16", "enqueue": False}})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    state = result.output
    assert isinstance(state, dict) and state["status"] == "PREPARED", state
    operation_id = state["operation_id"]
    assert isinstance(operation_id, str)
    operation = service.repository.get_h3_group_operation(operation_id)
    assert operation is not None and operation.request.media_inputs == selected
    route = service.repository.get_h3_route_plan(operation.state.route_plan_id or "")
    assert route is not None and route.decisions[0].media_inputs == selected
    assert route.decisions[0].resolution == "2K" and route.decisions[0].aspect_ratio == "9:16"
    assert service.repository.list_tasks(document.project.id) == []
    assert service.get_project(document.project.id).document.shots == before.shots
    assert len(fake.calls) == 1
    payload = json.loads(fake.calls[0][1])
    assert all(item.asset_version_id in json.dumps(payload["route_media_bindings"]) for item in selected)
    assert media[0].asset_version_id not in json.dumps(payload["route_media_bindings"])
    # A stored operation from the old display ordering remains reusable.
    rows = operation.input_context["source_context"]
    assert isinstance(rows, dict)
    locked = rows["locked_shots"]
    assert isinstance(locked, list)
    for row in locked:
        assert isinstance(row, dict) and isinstance(row["locked_shot"], dict)
        visible = row["visible_asset_ids"]
        assert isinstance(visible, list)
        row["visible_asset_ids"] = list(reversed(visible))
    service.repository.save_h3_group_operation(operation)
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: fake)
    resumed = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=operation_id, enqueue=True))
    assert resumed.status == "WAITING_MEDIA", resumed.error
    assert resumed.route_plan_id == route.id and len(resumed.task_ids) == 1 and len(fake.calls) == 1
    task = restarted.repository.get_task(resumed.task_ids[0])
    assert task is not None
    run = restarted.repository.get_provider_run(task.id)
    assert run is not None
    assert run.media_inputs == selected


def test_omitted_media_still_checks_complete_automatic_ten_before_language(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request, _media = selection_fixture(tmp_path)
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id))
    assert result.status == "BLOCKED" and "nine" in (result.error or "")
    assert fake.calls == [] and service.repository.list_h3_route_plans(document.project.id) == []
    assert service.repository.list_tasks(document.project.id) == []


@pytest.mark.parametrize("changed", ["file", "cue", "role"])
def test_explicit_prepared_source_rejects_actual_changes_without_new_language(tmp_path: Path, changed: str) -> None:
    service, fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 2)
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media[:1])
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.clip_group_id, media_inputs=media[:1]))
    assert prepared.status == "PREPARED", prepared.error
    if changed == "file":
        Image.new("RGB", (40, 50), "red").save(media[0].source.local_path or "")
    else:
        current = service.repository.get_project(document.project.id)
        assert current is not None
        if changed == "cue":
            current.shots[0].references[0].purpose = "Different actual intended state"
        else:
            current.bible_versions[-1].characters[0].appearance += " new visible wardrobe"
        service.repository.save_project(current)
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=prepared.operation_id))
    assert resumed.status == "INPUTS_CHANGED", resumed.error
    assert len(fake.calls) == 1 and service.repository.list_tasks(document.project.id) == []


def test_context_ir_accepts_same_explicit_subset_and_preserves_saved_prose(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 2)
    prompt = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media[:1])
    client = FakeIR(prompt)
    pending = prepare_h3_group(service, document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.clip_group_id, media_inputs=media[:1], compile_mode="H3_CONTEXT_IR"), context_ir_client=client)
    assert pending.status == "WAITING_PROMPT", pending.error
    due(service.workspace_root / "h3-context-ir")
    result = prepare_h3_group(service, document.project.id, H3GroupOperationRequest(operation_id=pending.operation_id), context_ir_client=client)
    assert result.status == "PREPARED", result.error
    assert len(client.submitted) == 1 and fake.calls == []
    assert service.get_project(document.project.id).document.shots == document.shots


class BlockingCompiler(FakePromptCompiler):
    def __init__(self, output: str) -> None:
        super().__init__()
        self.output_text = output
        self.started, self.release = Event(), Event()

    def generate_text(self, *, instructions: str, input_text: str, max_output_tokens: int = 2200) -> str:
        self.started.set()
        assert self.release.wait(10)
        return super().generate_text(instructions=instructions, input_text=input_text, max_output_tokens=max_output_tokens)


def test_file_changes_while_language_runs_cannot_save_or_enqueue_old_source(tmp_path: Path) -> None:
    original, _fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 2)
    compiler = BlockingCompiler(build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media[:1]))
    service = ShortDramaService(database_path=original.repository.database_path, workspace_root=original.workspace_root,
        language_provider_factory=lambda _key, _url: compiler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.prepare_h3_group, document.project.id, H3GroupOperationRequest(
            clip_group_id=unit.clip_group_id, media_inputs=media[:1]))
        assert compiler.started.wait(10)
        Image.new("RGB", (50, 60), "blue").save(media[0].source.local_path or "")
        compiler.release.set()
        result = future.result(timeout=10)
    assert result.status == "INPUTS_CHANGED", result.error
    assert service.repository.list_h3_route_plans(document.project.id) == []
    assert service.repository.list_tasks(document.project.id) == []


def test_saved_fresh5_source_only_ignores_actual_visibility_set_permutation() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/fresh5_prepared_reference_source.json").read_text())
    original = fixture["input_context"]
    changed = deepcopy(original)
    by_shot = {row["shot_id"]: row["current"] for row in fixture["current_visible_asset_ids"]}
    for row in changed["source_context"]["locked_shots"]:
        row["visible_asset_ids"] = by_shot[row["locked_shot"]["id"]]
    assert original != changed and _source_identity(original) == _source_identity(changed)
    assert fixture["input_context"] == original
    # Ordering the actual Picture slots remains meaningful even with the same set.
    reordered = deepcopy(changed)
    reordered["media_inputs"].reverse()
    assert _source_identity(original) != _source_identity(reordered)
    for field in ("media_file_states", "media_inputs"):
        modified = deepcopy(changed)
        modified[field][0]["actual_change"] = "new content"
        assert _source_identity(original) != _source_identity(modified)
    changed["source_context"]["locked_shots"][0]["locked_shot"]["audio_intent"] += " new voice direction"
    assert _source_identity(original) != _source_identity(changed)


def test_editorial_only_draft_is_opt_in_per_request_without_changing_prose(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 2)
    for cue in document.shots[0].references:
        cue.usage = "EDITORIAL"
    service.repository.save_project(document)
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2])
    automatic = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id))
    assert automatic.status == "PREPARED", automatic.error
    auto_record = service.repository.get_h3_group_operation(automatic.operation_id)
    assert auto_record is not None and auto_record.resolved_media_inputs == []
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media[:1])
    selected = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.clip_group_id, media_inputs=media[:1]))
    assert selected.status == "PREPARED", selected.error
    assert service.get_project(document.project.id).document.shots == document.shots
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    queued = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=selected.operation_id, enqueue=True))
    assert queued.status == "WAITING_MEDIA", queued.error
    task = service.repository.get_task(queued.task_ids[0])
    assert task is not None
    assert service.h3_worker.input_staleness_reason(task) is None
    wrong = media[0].model_copy(update={"subject_asset_id": document.assets[2].id})
    rejected = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id, media_inputs=[wrong]))
    assert rejected.status == "BLOCKED" and "subject" in (rejected.error or "")
    assert len(fake.calls) == 2


@pytest.mark.parametrize("boundary", ["route_execute", "worker_upload", "worker_submit"])
@pytest.mark.parametrize("change", ["selected_file", "cue_without_revision"])
def test_persisted_operation_source_is_checked_at_actual_execution_boundaries(tmp_path: Path, boundary: str, change: str) -> None:
    original, fake, document, _generation, unit, _request, media = selection_fixture(tmp_path, 2)
    client = FakeH3Client()
    service = ShortDramaService(database_path=original.repository.database_path, workspace_root=original.workspace_root,
        language_provider_factory=lambda _key, _url: fake, h3_client_factory=lambda _key: client)
    service.h3_config.update(H3ProviderConfigUpdate(api_key="test-token"))
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=media[:1])
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(
        clip_group_id=unit.clip_group_id, media_inputs=media[:1], enqueue=boundary != "route_execute"))
    assert prepared.status == ("PREPARED" if boundary == "route_execute" else "WAITING_MEDIA"), prepared.error
    if boundary == "worker_submit":
        uploaded = service.h3_worker.advance(prepared.task_ids[0])
        assert uploaded.status == TaskStatus.SUBMITTING and client.upload_calls and not client.submitted_requests
    if change == "selected_file":
        Image.new("RGB", (35, 60), "green").save(media[0].source.local_path or "")
    else:
        current = service.repository.get_project(document.project.id)
        assert current is not None
        current.shots[0].references[0].purpose = "The new story state uses a different visible inscription"
        service.repository.save_project(current)
    if boundary == "route_execute":
        assert prepared.route_plan_id is not None
        with pytest.raises(WorkflowConflictError, match="saved H3 operation source"):
            service.execute_h3_production_route_plan(document.project.id, prepared.route_plan_id,
                H3ProductionRouteExecuteRequest(expected_state_revision=prepared.route_plan_state_revision,
                    enqueue_new_generations=True, reuse_passed_takes=False, inspect_reused_takes=False, semantic_review=False))
        assert service.repository.list_tasks(document.project.id) == []
    else:
        result = service.h3_worker.advance(prepared.task_ids[0])
        assert result.status == TaskStatus.FAILED and result.error_code == "H3_INPUTS_STALE"
    assert not client.submitted_requests
    if boundary != "worker_submit":
        assert not client.upload_calls
    assert len(fake.calls) == 1
