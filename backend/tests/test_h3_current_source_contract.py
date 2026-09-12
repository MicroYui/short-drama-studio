"""Current H3 READ, logical subjects and explicit saved-route reuse, without Providers."""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from short_drama.change_executor import SemanticArtifactPatch, SemanticChangeApplyRequest
from short_drama.change_planner import ChangeTargetKind
from short_drama.h3_group_operation import H3GroupOperationRequest, _source_identity, operation_source_staleness_reason
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProductionRoutePlanRequest, H3SavedRouteSource, H3ShotRouteDirective
from short_drama.language_models import H3GenerationUnitContextRequest
from short_drama.models import ApprovalStatus, AssetKind, AssetMediaType, Freshness, ImageReferenceRole, ProductionDocumentOrigin, ReferencePlanEntry, ShotReferenceCue, VideoGenerationRoute
from short_drama.production_contracts import GenerationPlanCompileRequest, StoryboardPatchRequest, StoryboardShotReplacement
from short_drama.providers.h3_language_prompt import _compiler_instructions
from short_drama.providers.h3_multishot_example import MULTISHOT_EXAMPLE
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt
from short_drama.reference_planner import ReferencePackEntryCreateRequest
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools.contracts import ToolCallStatus
from tests.test_h3_generation_unit_language import _fixture
from tests.test_semantic_change_executor import _plan


def _read_request(generation, unit):
    return H3GenerationUnitContextRequest(generation_plan_document_id=generation.document.id,
        generation_plan_version_id=generation.version.id, generation_unit_id=unit.id)


def _reword(service, project_id, shot_id):
    board = service.get_storyboard(project_id)
    shot = next(s for s in board.storyboard.shots if s.id == shot_id)
    replacement = shot.model_copy(deep=True)
    replacement.narrative_purpose += " Preserve this action and its outgoing state."
    return service.patch_storyboard(project_id, StoryboardPatchRequest(base_version_id=board.version.id,
        origin=ProductionDocumentOrigin.USER, replacements=[StoryboardShotReplacement(
            shot_id=shot.id, expected_revision=shot.revision, replacement=replacement)]))


def _compile_current(service, project_id):
    board = service.get_storyboard(project_id)
    spec = service.get_production_spec(project_id)
    current = service.get_generation_plan(project_id)
    return service.compile_generation_plan(project_id, GenerationPlanCompileRequest(
        storyboard_document_id=board.document.id, storyboard_version_id=board.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        base_version_id=current.version.id, origin=ProductionDocumentOrigin.AGENT))


def test_stale_public_read_returns_complete_current_group_without_save_or_provider(tmp_path):
    service, fake, document, generation, unit, request = _fixture(tmp_path)
    board = _reword(service, document.project.id, unit.shot_ids[0])
    before = service.repository.get_project(document.project.id)
    result = service.tool_registry.invoke("route.get_generation_unit_context", {
        "project_id": document.project.id, "request": _read_request(generation, unit).model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    output = result.output
    assert output["source_status"] == "CURRENT_PROJECTION" and output["source_changes"]
    assert output["generation_plan_version_id"] == generation.version.id
    assert output["current_storyboard"]["version_id"] == board.version.id
    assert output["current_clip_group"] == board.storyboard.clip_groups[0].model_dump(mode="json")
    assert output["source_context"]["locked_shots"][0]["locked_shot"]["revision"] == 2
    assert "OTHER_CLIP_ONLY" not in json.dumps(output)
    assert service.repository.get_project(document.project.id) == before
    with pytest.raises(WorkflowConflictError, match="outdated shot"):
        service.compile_h3_prompt_with_language_model(document.project.id, request)
    assert fake.calls == [] and service.repository.list_h3_route_plans(document.project.id) == []


def test_source_read_does_not_substitute_unknown_requested_unit(tmp_path):
    service, fake, document, generation, unit, _ = _fixture(tmp_path)
    request = _read_request(generation, unit).model_copy(update={"generation_unit_id": "missing-group"})
    with pytest.raises(KeyError):
        service.get_h3_generation_unit_context(document.project.id, request)
    assert fake.calls == []


def test_read_source_status_is_live_but_saved_routes_and_historical_records_are_unchanged(tmp_path):
    service, fake, document, generation, unit, _ = _fixture(tmp_path)
    first = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    original = service.repository.get_h3_route_plan(first.route_plan_id)
    assert service.get_h3_production_route_plan(document.project.id, original.id).source_checks[0].status == "CURRENT"
    _reword(service, document.project.id, unit.shot_ids[0])
    stale = service.get_h3_production_route_plan(document.project.id, original.id)
    assert stale.source_checks[0].status == "STALE" and "outdated shot" in stale.source_checks[0].reason
    assert stale.plan == original == service.repository.get_h3_route_plan(original.id)
    current = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[]))
    index = service.list_h3_production_route_plans(document.project.id)
    assert {c.route_plan_id: c.status for c in index.source_checks} == {original.id: "STALE", current.route_plan_id: "CURRENT"}
    assert len(fake.calls) == 2


def _composition_fixture(tmp_path):
    service, fake, document, generation, unit, _ = _fixture(tmp_path)
    subject = document.assets[0]
    version = subject.versions[0]
    path = tmp_path / "identity.png"
    Image.new("RGB", (64, 96), "white").save(path)
    version.media_type, version.local_path, version.external_url = AssetMediaType.IMAGE, str(path), None
    version.approval_status, version.freshness = ApprovalStatus.DRAFT, Freshness.CURRENT
    subject.current_version_id = version.id
    subject.approved_version_id = None
    container = subject.model_copy(deep=True)
    container.id, container.name, container.kind, container.bible_entity_id = "composition-owner", "Shot composition", AssetKind.REFERENCE, None
    child = version.model_copy(deep=True)
    child.id, child.parent_version_id = "composition-version", version.id
    child.local_path = str(tmp_path / "composition.png")
    Image.new("RGB", (64, 96), "gray").save(child.local_path)
    container.versions, container.current_version_id, container.approved_version_id = [child], child.id, None
    document.assets.append(container)
    document.shots[0].references.append(ShotReferenceCue(subject_asset_id=subject.id,
        asset_version_id=child.id, alias="composition", image_reference_role=ImageReferenceRole.COMPOSITION,
        purpose="Soft staging for this shot, preserving the subject's identity."))
    for plan in document.reference_plan:
        if plan.shot_id in unit.shot_ids:
            plan.bindings = []
    service.repository.save_project(document)
    service.add_reference_pack_entry(document.project.id, ReferencePackEntryCreateRequest(
        subject_asset_id=subject.id, asset_version_id=child.id, lineage_root_version_id=version.id,
        role="CHARACTER_BODY", description="Selected subject's soft composition pose."))
    document = service.get_project(document.project.id).document
    media = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(local_path=child.local_path), asset_version_id=child.id,
        subject_asset_id=subject.id, reference_alias="composition", image_reference_role=ImageReferenceRole.COMPOSITION)
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2], route=VideoGenerationRoute.REFERENCE, media_inputs=[media])
    return service, fake, document, generation, unit, media, container


def test_prepare_refresh_cannot_add_composition_owner_to_physical_subjects(tmp_path):
    service, fake, document, generation, unit, media, container = _composition_fixture(tmp_path)
    before = service.get_h3_generation_unit_context(document.project.id, _read_request(generation, unit))
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[media]))
    assert prepared.status == "PREPARED", prepared.error
    op = service.repository.get_h3_group_operation(prepared.operation_id)
    after = service.get_h3_generation_unit_context(document.project.id, _read_request(generation, unit))
    assert before.source_context == after.source_context
    assert container.id not in after.source_context["locked_shots"][0]["visible_asset_ids"]
    assert operation_source_staleness_reason(service, op, media_inputs=op.resolved_media_inputs) is None
    assert service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=op.id)).route_plan_id == prepared.route_plan_id
    assert len(fake.calls) == 1


def test_actual_fresh6_duplicate_owner_normalizes_but_real_subject_change_does_not(tmp_path):
    service, _, document, _, _, _ = _fixture(tmp_path)
    actual = json.loads((Path(__file__).parent / "fixtures/fresh6_h3_source_contract.json").read_text())
    template = document.assets[0]
    document.assets = []
    for row in actual["version_owners"]:
        asset = template.model_copy(deep=True)
        asset.id, asset.name, asset.kind = row["asset_id"], row["name"], AssetKind(row["kind"])
        asset.versions = [template.versions[0].model_copy(update={"id": vid}) for vid in row["version_ids"]]
        document.assets.append(asset)
    document.reference_plan = [ReferencePlanEntry.model_validate(actual["s12_reference_plan"])]
    saved = {"source_context": {"locked_shots": [actual["saved_s12"]], "visible_assets": ["许宁", "物流公司小会议室"]}}
    legacy = {"source_context": {"locked_shots": [actual["current_s12"]], "visible_assets": ["许宁", "物流公司小会议室", "第12镜｜许宁垂眼等待反应"]}}
    unchanged = deepcopy(legacy)
    assert _source_identity(saved, document=document) == _source_identity(legacy, document=document)
    assert legacy == unchanged
    genuinely_new = deepcopy(saved)
    genuinely_new["source_context"]["locked_shots"][0]["visible_asset_ids"].append("different-physical-subject")
    assert _source_identity(saved, document=document) != _source_identity(genuinely_new, document=document)


@pytest.mark.parametrize("field", ["action_out", "prop_state_out", "eyeline", "character_positions", "transition_out"])
def test_previous_terminal_uses_outgoing_truth_and_keeps_actual_boundary_changes(field):
    actual = json.loads((Path(__file__).parent / "fixtures/fresh6_h3_source_contract.json").read_text())
    source = {"source_context": {"previous_clip_planned_terminal_state": actual["previous_terminal"]}}
    changed = deepcopy(source)
    changed["source_context"]["previous_clip_planned_terminal_state"]["visual"] = "Only the middle action is rephrased."
    assert _source_identity(source) == _source_identity(changed)
    changed["source_context"]["previous_clip_planned_terminal_state"][field] += " A real outgoing change."
    assert _source_identity(source) != _source_identity(changed)
    source["source_context"]["previous_clip_planned_terminal_state"]["action_out"] = ""
    missing = deepcopy(source)
    missing["source_context"]["previous_clip_planned_terminal_state"]["visual"] += " Changed fallback."
    assert _source_identity(source) != _source_identity(missing)


def _reuse_request(generation, unit, route):
    return H3ProductionRoutePlanRequest(generation_plan_document_id=generation.document.id,
        generation_plan_version_id=generation.version.id, unit_ids=[unit.id],
        shot_directives=[H3ShotRouteDirective(generation_unit_id=unit.id, saved_route_source=H3SavedRouteSource(
            route_plan_id=route.id, state_revision=route.state_revision, generation_unit_id=unit.id))])


def test_saved_route_reuse_keeps_prompt_order_and_draft_files_with_new_source_and_restart(tmp_path):
    service, fake, document, generation, unit, media, _ = _composition_fixture(tmp_path)
    original = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id,
        media_inputs=[media], resolution="2K", aspect_ratio="9:16"))
    assert original.status == "PREPARED", original.error
    old_route = service.repository.get_h3_route_plan(original.route_plan_id)
    old_op = service.repository.get_h3_group_operation(original.operation_id)
    _reword(service, document.project.id, unit.shot_ids[0])
    current = _compile_current(service, document.project.id)
    request = _reuse_request(current, unit, old_route)
    result = service.tool_registry.invoke("route.plan_h3", {"project_id": document.project.id, "request": request.model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    route = service.repository.get_h3_route_plan(result.output["plan"]["id"])
    assert route.decisions[0].prompt == old_route.decisions[0].prompt
    assert route.decisions[0].media_inputs == old_route.decisions[0].media_inputs
    assert route.decisions[0].resolution == "2K" and route.decisions[0].aspect_ratio == "9:16"
    assert route.reused_route_source == request.shot_directives[0].saved_route_source
    assert route.source_operation_id != old_op.id
    assert len(fake.calls) == 1
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
        language_provider_factory=lambda _key, _url: (_ for _ in ()).throw(AssertionError("No Provider")))
    op = restarted.repository.get_h3_group_operation(route.source_operation_id)
    assert op.state.compile_mode == "SAVED_ROUTE"
    assert op.input_context["compile_mode"] == "SAVED_ROUTE"
    assert op.request.clip_group_id == op.state.clip_group_id == unit.clip_group_id
    assert op.state.generation_plan_version_id == current.version.id
    assert op.input_context["source_context"]["locked_shots"][0]["locked_shot"]["revision"] == 2
    assert restarted.repository.get_h3_group_operation(old_op.id) == old_op
    assert restarted.get_h3_production_route_plan(document.project.id, route.id).source_checks[0].status == "CURRENT"
    resumed = restarted.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=op.id))
    assert resumed.route_plan_id == route.id and resumed.status == "PREPARED", resumed.error
    Image.new("RGB", (65, 97), "black").save(media.source.local_path)
    assert restarted.get_h3_production_route_plan(document.project.id, route.id).source_checks[0].status == "STALE"
    assert restarted.repository.list_tasks(document.project.id) == []


@pytest.mark.parametrize("problem", ["state", "unit", "format", "file"])
def test_saved_route_reuse_invalid_selection_saves_no_partial_result(tmp_path, problem):
    service, fake, document, generation, unit, media, _ = _composition_fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.id, media_inputs=[media]))
    assert prepared.status == "PREPARED", prepared.error
    route = service.repository.get_h3_route_plan(prepared.route_plan_id)
    request = _reuse_request(generation, unit, route)
    if problem == "state":
        request.shot_directives[0].saved_route_source.state_revision += 1
    elif problem == "unit":
        request.shot_directives[0].saved_route_source.route_plan_id = "missing-route"
    elif problem == "format":
        request.aspect_ratio = "16:9"
    else:
        Path(media.source.local_path).unlink()
    before = service.repository.get_project(document.project.id)
    with pytest.raises(ValueError):
        service.compile_h3_production_route_plan(document.project.id, request)
    assert len(service.repository.list_h3_route_plans(document.project.id)) == 1
    assert len(service.repository.list_h3_group_operations(document.project.id)) == 1
    assert service.repository.get_project(document.project.id) == before
    assert len(fake.calls) == 1


def test_change_fields_write_outgoing_continuity_and_formal_storyboard_together(tmp_path):
    service, _, document, _, unit, _ = _fixture(tmp_path)
    shot = document.shots[0]
    result = service.apply_semantic_change(document.project.id, SemanticChangeApplyRequest(
        plan=_plan(project_id=document.project.id, kind=ChangeTargetKind.SHOT, target_id=shot.id, affected_shot_ids=[shot.id]),
        patches=[SemanticArtifactPatch(target_kind=ChangeTargetKind.SHOT, target_id=shot.id,
            updates={"continuity.action_out": "Hands withdraw after delivery.", "continuity.prop_state_out": "Paper rests unheld.",
                     "transition_out": "Cut to the recipient after delivery."})], change_reason="Keep the outgoing state exact."))
    saved = next(s for s in service.get_project(document.project.id).document.shots if s.id == shot.id)
    formal = next(s for s in service.get_storyboard(document.project.id).storyboard.shots if s.id == shot.id)
    assert saved.continuity.action_out == formal.continuity.action_out == "Hands withdraw after delivery."
    assert saved.continuity.prop_state_out == formal.continuity.prop_state_out == "Paper rests unheld."
    assert saved.timeline_beats == shot.timeline_beats
    assert result.result.applied_patch_count == 1


def test_complete_example_is_loaded_for_groups_and_retains_unique_events_and_windows(tmp_path):
    _, _, document, _, _, _ = _fixture(tmp_path)
    instructions = _compiler_instructions(document, method_skill_ids=[], generation_unit=True)
    assert MULTISHOT_EXAMPLE in instructions
    assert MULTISHOT_EXAMPLE not in _compiler_instructions(document, method_skill_ids=[])
    assert len(MULTISHOT_EXAMPLE) < 6000
    for line in ("所以你让先发。这句也写上，我签。", "你非得分这么清？", "你要我负责，就把经过写全。"):
        assert MULTISHOT_EXAMPLE.count(line) == 1
    for window in ("0.00-4.00", "4.00-6.00", "6.00-8.50", "8.50-12.50"):
        assert window in MULTISHOT_EXAMPLE
    assert "Placed pen stays still; hand contact moves paper" in MULTISHOT_EXAMPLE
    assert "12.50–13.00" in MULTISHOT_EXAMPLE and "silent hold" in MULTISHOT_EXAMPLE


@pytest.mark.parametrize("extra", ["candidate_id", "prompt_override", "media_inputs"])
def test_saved_route_source_rejects_competing_choices(extra):
    from pydantic import ValidationError
    values = {"generation_unit_id": "group-a", "saved_route_source": {
        "route_plan_id": "route-a", "state_revision": 1, "generation_unit_id": "group-a"},
        extra: [] if extra == "media_inputs" else "explicit-other-choice"}
    with pytest.raises(ValidationError, match="exclusive"):
        H3ShotRouteDirective.model_validate(values)


def test_saved_route_reuse_is_one_explicit_unit_not_a_mixed_batch():
    from pydantic import ValidationError
    directive = {"generation_unit_id": "group-a", "saved_route_source": {
        "route_plan_id": "route-a", "state_revision": 1, "generation_unit_id": "group-a"}}
    with pytest.raises(ValidationError, match="one explicit unit"):
        H3ProductionRoutePlanRequest.model_validate({"generation_plan_document_id": "plan-a",
            "generation_plan_version_id": "version-a", "unit_ids": ["group-a", "group-b"],
            "shot_directives": [directive, {"generation_unit_id": "group-b"}]})


def test_saved_route_reuse_revalidates_current_spoken_words(tmp_path):
    from short_drama.production_contracts import StoryboardReplaceRequest, ProductionSpecReplaceRequest
    service, fake, document, generation, unit, media, _ = _composition_fixture(tmp_path)
    prepared = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id, media_inputs=[media]))
    assert prepared.status == "PREPARED", prepared.error
    route = service.repository.get_h3_route_plan(prepared.route_plan_id)
    script = next(s for s in document.script_versions if s.id == document.project.current_script_version_id)
    line_id = next(line_id for shot in document.shots[:2] for line_id in shot.dialogue_line_ids)
    line = next(line for line in script.lines if line.id == line_id)
    service.apply_semantic_change(document.project.id, SemanticChangeApplyRequest(
        plan=_plan(project_id=document.project.id, kind=ChangeTargetKind.SCRIPT_LINE,
            target_id=line.id, affected_shot_ids=unit.shot_ids, affected_script_line_ids=[line.id]),
        patches=[SemanticArtifactPatch(target_kind=ChangeTargetKind.SCRIPT_LINE, target_id=line.id,
            updates={"spoken_text": "现在请你把经过说清楚。", "subtitle_text": "现在请你把经过说清楚。"})], change_reason="User changed the current spoken words."))
    current_document = service.get_project(document.project.id).document
    spec = service.get_production_spec(document.project.id)
    current_spec = service.replace_production_spec(document.project.id, ProductionSpecReplaceRequest(
        origin=ProductionDocumentOrigin.USER, base_version_id=spec.version.id,
        spec=spec.spec.model_copy(update={"script_version_id": current_document.project.current_script_version_id})))
    board = service.get_storyboard(document.project.id)
    updated = board.storyboard.model_copy(update={"script_version_id": current_document.project.current_script_version_id,
        "production_spec_version_id": current_spec.version.id, "shots": current_document.shots}, deep=True)
    service.replace_storyboard(document.project.id, StoryboardReplaceRequest(origin=ProductionDocumentOrigin.USER,
        base_version_id=board.version.id, storyboard=updated))
    current = _compile_current(service, document.project.id)
    before = service.repository.get_project(document.project.id)
    result = service.tool_registry.invoke("route.plan_h3", {"project_id": document.project.id,
        "request": _reuse_request(current, unit, route).model_dump(mode="json")})
    assert result.status == ToolCallStatus.FAILED
    assert "dialogue" in str(result.error).lower()
    assert service.repository.get_project(document.project.id) == before
    assert len(service.repository.list_h3_route_plans(document.project.id)) == 1
    assert len(service.repository.list_h3_group_operations(document.project.id)) == 1
    assert len(fake.calls) == 1
