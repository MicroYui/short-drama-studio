"""Default grouped H3 voice choices use current sources; all clients are Fake."""

import json
from pathlib import Path

from PIL import Image
import pytest

from short_drama.asset_reference_truth import AssetReferenceTruthError, resolve_current_asset_media
from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.h3_route_planner import _available_media_inputs, _voice_reference_inputs, generation_unit_media_candidates, generation_unit_voice_reference_inputs
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest
from short_drama.models import ApprovalStatus, AssetKind, AssetMediaType, Freshness, ProductionDocumentOrigin, ShotReferenceCue, VideoGenerationRoute
from short_drama.production_contracts import GenerationPlanCompileRequest, StoryboardPatchRequest, StoryboardShotReplacement
from short_drama.providers.minimax_h3_compiler import H3CapabilityError, build_h3_generation_unit_prompt, compile_h3_request, validate_h3_audio_durations, validate_h3_inputs
from short_drama.service import ShortDramaService
from short_drama.voice_models import VoiceReferenceUpdateRequest
from tests.test_h3_generation_unit_language import _fixture
from tests.test_h3_generation_unit_worker import _harness
from tests.test_voice_reference import _render_audio


def _setup(tmp_path: Path):
    service, fake, doc, _generation, unit, _request = _fixture(tmp_path)
    asset = doc.assets[0]
    version = asset.versions[0]
    path = tmp_path / "selected-face.png"
    Image.new("RGB", (96, 144), "gray").save(path)
    version.media_type, version.approval_status, version.freshness = AssetMediaType.IMAGE, ApprovalStatus.DRAFT, Freshness.CURRENT
    version.local_path, version.external_url = str(path), None
    asset.current_version_id = version.id
    doc.shots[0].references = [ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
        alias="actor", purpose="Identity of the selected actor")]
    for plan in doc.reference_plan:
        if plan.shot_id in unit.shot_ids:
            plan.bindings = []
    service.repository.save_project(doc)
    return service, fake, doc, unit


def _voice(service: ShortDramaService, tmp_path: Path, project_id: str, character_id: str):
    path = tmp_path / f"{character_id}.wav"
    return service.upload_character_voice_reference(project_id, character_id, filename=path.name,
        descriptor=f"Selected voice for {character_id}", data=_render_audio(path))


def _inputs(service: ShortDramaService, project_id: str, shot_ids: list[str]):
    doc = service.get_project(project_id).document
    shots = [next(s for s in doc.shots if s.id == shot_id) for shot_id in shot_ids]
    candidates, _warnings = generation_unit_media_candidates(doc, shots)
    images = [c.media_input for c in candidates if c.media_input.kind == H3MediaKind.IMAGE]
    media = [*images, *generation_unit_voice_reference_inputs(doc, shots, selected_media_inputs=images)]
    return doc, shots, media


def test_default_tool_compiles_and_saves_current_draft_voice_and_audio_numbering(tmp_path: Path) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    old = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert old.asset is not None and old.version is not None
    newer = service.import_local_asset_version(doc.project.id, old.asset.id,
        AssetImportLocalRequest(local_path=str(tmp_path / "character-linxia.wav"), descriptor="Current selected voice"))
    selected = newer.version
    assert selected.approval_status == ApprovalStatus.DRAFT
    doc, shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    audio = [m for m in media if m.kind == H3MediaKind.AUDIO]
    assert [m.asset_version_id for m in audio] == [selected.id]
    assert next(a for a in doc.assets if a.id == old.asset.id).approved_version_id == old.version.id
    legacy, _warnings = _voice_reference_inputs(document=doc, shot=shots[1], approved_inputs=_available_media_inputs(doc))
    assert [m.asset_version_id for _name, m in legacy] == [selected.id]
    with pytest.raises(AssetReferenceTruthError, match="currently selected"):
        resolve_current_asset_media(doc, asset_version_id=old.version.id, subject_asset_id=old.asset.id)
    fake.output_text = build_h3_generation_unit_prompt(doc, unit, shots, route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    result = service.tool_registry.invoke("route.prepare_h3_group", {"project_id": doc.project.id,
        "request": {"shot_id": shots[0].id, "resolution": "2K", "aspect_ratio": "9:16", "enqueue": False}})
    assert result.output is not None and result.output["status"] == "PREPARED", result
    saved = service.repository.get_h3_group_operation(str(result.output["operation_id"]))
    assert saved is not None and saved.state.route_plan_id is not None
    decision = service.get_h3_production_route_plan(doc.project.id, saved.state.route_plan_id).plan.decisions[0]
    assert saved.resolved_media_inputs == decision.media_inputs == media
    payload = json.loads(fake.calls[0][1])
    assert [(m["provider_label"], m["asset_version_id"]) for m in payload["route_media_bindings"]] == [
        ("<Picture 1>", media[0].asset_version_id), ("<Audio 1>", selected.id)]
    assert "<Audio 1>" in decision.prompt and audio[0].binding_purpose in payload["route_media_bindings"][1]["purpose"]
    wire = compile_h3_request(route=decision.route, prompt=decision.prompt, duration_seconds=decision.duration_seconds,
        media_inputs=decision.media_inputs, resolution=decision.resolution, aspect_ratio=decision.aspect_ratio,
        resolve_url=lambda m: f"https://example.invalid/{m.asset_version_id}")
    assert [m.role for m in wire.content[1:]] == [H3MediaRole.REFERENCE_IMAGE, H3MediaRole.REFERENCE_AUDIO]
    assert wire.content[-1].audio_url is not None and selected.id in wire.content[-1].audio_url.url
    again = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(operation_id=saved.id))
    assert again.operation_id == saved.id and len(fake.calls) == 1 and service.repository.list_tasks(doc.project.id) == []


def test_two_speakers_are_scoped_by_typed_lines_and_first_occurrence(tmp_path: Path) -> None:
    service, _fake, doc, unit = _setup(tmp_path)
    lin = _voice(service, tmp_path, doc.project.id, "character-linxia")
    zhou = _voice(service, tmp_path, doc.project.id, "character-zhouheng")
    assert lin.version is not None and zhou.version is not None
    doc, shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    assert [m.asset_version_id for m in media if m.kind == H3MediaKind.AUDIO] == [lin.version.id]
    script = next(v for v in doc.script_versions if v.id == doc.project.current_script_version_id)
    first = next(line for line in script.lines if line.speaker_character_id == "character-linxia")
    second = next(line for line in script.lines if line.speaker_character_id == "character-zhouheng")
    combined = [shots[0].model_copy(update={"dialogue_line_ids": [second.id, first.id]}),
        shots[1].model_copy(update={"dialogue_line_ids": [first.id]})]
    scoped = generation_unit_voice_reference_inputs(doc, combined, selected_media_inputs=[])
    assert [m.asset_version_id for m in scoped] == [zhou.version.id, lin.version.id]
    assert [m.subject_asset_id for m in scoped] == [next(a.id for a in doc.assets if a.bible_entity_id == c)
        for c in ("character-zhouheng", "character-linxia")]
    first.speaker_character_id = None
    assert generation_unit_voice_reference_inputs(doc, shots, selected_media_inputs=[]) == []


def test_two_speaker_group_preserves_audio_order_through_actual_compiler_and_saved_route(tmp_path: Path) -> None:
    service, fake, doc, _unit = _setup(tmp_path)
    first = _voice(service, tmp_path, doc.project.id, "character-linxia")
    second = _voice(service, tmp_path, doc.project.id, "character-zhouheng")
    assert first.version is not None and second.version is not None
    doc = service.get_project(doc.project.id).document
    board = service.get_storyboard(doc.project.id)
    groups = [g.model_copy(deep=True) for g in board.storyboard.clip_groups]
    groups[0].shot_ids, groups[0].duration_seconds = [doc.shots[0].id], 5
    groups[1].shot_ids, groups[1].duration_seconds = [doc.shots[1].id, doc.shots[2].id], 14
    changed = doc.shots[1].model_copy(update={"references": [doc.shots[0].references[0]]})
    patched = service.patch_storyboard(doc.project.id, StoryboardPatchRequest(base_version_id=board.version.id,
        origin=ProductionDocumentOrigin.AGENT, clip_groups=groups, replacements=[StoryboardShotReplacement(
            shot_id=changed.id, expected_revision=changed.revision, replacement=changed)]))
    spec = service.get_production_spec(doc.project.id)
    plan = service.compile_generation_plan(doc.project.id, GenerationPlanCompileRequest(
        storyboard_document_id=patched.document.id, storyboard_version_id=patched.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        base_version_id=service.get_generation_plan(doc.project.id).version.id,
        origin=ProductionDocumentOrigin.AGENT))
    unit = plan.generation_plan.units[1]
    current = service.get_project(doc.project.id).document
    # The legacy fixture's third shot has text-only placeholder plan bindings;
    # this group instead explicitly selects the real image on its first shot.
    for reference_plan in current.reference_plan:
        if reference_plan.shot_id in unit.shot_ids:
            reference_plan.bindings = []
    service.repository.save_project(current)
    doc, shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    assert [m.asset_version_id for m in media if m.kind == H3MediaKind.AUDIO] == [first.version.id, second.version.id]
    fake.output_text = build_h3_generation_unit_prompt(doc, unit, shots, route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    response = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(clip_group_id=unit.clip_group_id))
    assert response.status == "PREPARED", response.error
    saved = service.repository.get_h3_group_operation(response.operation_id)
    assert saved is not None and saved.state.route_plan_id is not None
    decision = service.get_h3_production_route_plan(doc.project.id, saved.state.route_plan_id).plan.decisions[0]
    payload = json.loads(fake.calls[0][1])
    assert [(m["provider_label"], m["asset_version_id"]) for m in payload["route_media_bindings"] if m["kind"] == "AUDIO"] == [
        ("<Audio 1>", first.version.id), ("<Audio 2>", second.version.id)]
    assert decision.media_inputs == media and "<Audio 1>" in decision.prompt and "<Audio 2>" in decision.prompt
    assert len(fake.calls) == 1 and service.repository.list_tasks(doc.project.id) == []


def test_explicit_media_or_current_shot_audio_replaces_the_default_without_global_additions(tmp_path: Path) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    global_voice = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert global_voice.version is not None
    replacement_asset = service.create_asset(doc.project.id, AssetCreateRequest(kind=AssetKind.REFERENCE,
        name="One shot alternative", descriptor="Current explicit voice alternative")).asset
    replacement = service.import_local_asset_version(doc.project.id, replacement_asset.id,
        AssetImportLocalRequest(local_path=str(tmp_path / "character-linxia.wav"), descriptor="Alternative voice")).version
    doc, shots, default = _inputs(service, doc.project.id, unit.shot_ids)
    character_asset = next(a for a in doc.assets if a.bible_entity_id == "character-linxia")
    chosen = H3MediaInput(kind=H3MediaKind.AUDIO, role=H3MediaRole.REFERENCE_AUDIO,
        source=H3MediaSource(local_path=replacement.local_path),
        asset_version_id=replacement.id, subject_asset_id=character_asset.id,
        reference_alias="selected_voice", binding_purpose="This shot's selected voice")
    media = [default[0], chosen]
    fake.output_text = build_h3_generation_unit_prompt(doc, unit, shots, route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    explicit = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=shots[0].id,
        media_inputs=media, route=VideoGenerationRoute.REFERENCE))
    assert explicit.status == "PREPARED", explicit.error
    saved = service.repository.get_h3_group_operation(explicit.operation_id)
    assert saved is not None and saved.resolved_media_inputs == media
    shots[1].references.append(ShotReferenceCue(subject_asset_id=character_asset.id, asset_version_id=replacement.id,
        alias="selected_voice", purpose=chosen.binding_purpose or "voice"))
    service.repository.save_project(doc)
    via_cue = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=shots[0].id))
    assert via_cue.status == "PREPARED", via_cue.error
    cue_saved = service.repository.get_h3_group_operation(via_cue.operation_id)
    assert cue_saved is not None
    assert [m.asset_version_id for m in cue_saved.resolved_media_inputs if m.kind == H3MediaKind.AUDIO] == [replacement.id]
    cleared = service.update_character_voice_reference(doc.project.id, "character-linxia", VoiceReferenceUpdateRequest())
    assert cleared.binding is None
    assert generation_unit_voice_reference_inputs(cleared.project.document, shots, selected_media_inputs=[]) == []
    assert service.repository.list_tasks(doc.project.id) == []


@pytest.mark.parametrize("problem", ["REJECTED", "STALE", "MISSING", "NON_AUDIO"])
def test_bad_selected_binding_fails_before_language_without_falling_back(tmp_path: Path, problem: str) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    voice = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert voice.asset is not None and voice.version is not None
    doc = service.get_project(doc.project.id).document
    asset = next(a for a in doc.assets if a.id == voice.asset.id)
    version = next(v for v in asset.versions if v.id == voice.version.id)
    if problem == "REJECTED":
        version.approval_status = ApprovalStatus.REJECTED
    elif problem == "STALE":
        version.freshness = Freshness.STALE
    elif problem == "MISSING":
        version.local_path = str(tmp_path / "absent.wav")
    else:
        version.media_type = AssetMediaType.TEXT_REFERENCE
    service.repository.save_project(doc)
    result = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=unit.shot_ids[0]))
    assert result.status == "BLOCKED" and result.error is not None
    assert "character-linxia" in result.error and voice.asset.id in result.error
    assert not fake.calls and service.repository.list_tasks(doc.project.id) == []


def test_audio_only_is_rejected_without_choosing_a_visual_or_calling_language(tmp_path: Path) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    _voice(service, tmp_path, doc.project.id, "character-linxia")
    doc = service.get_project(doc.project.id).document
    doc.shots[0].references = []
    service.repository.save_project(doc)
    result = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=unit.shot_ids[0]))
    assert result.status == "BLOCKED" and "at least one reference image or reference video" in (result.error or "")
    assert not fake.calls and service.repository.list_tasks(doc.project.id) == []
    with pytest.raises(H3CapabilityError, match="at least one reference image"):
        validate_h3_inputs(route=VideoGenerationRoute.REFERENCE, duration_seconds=4,
            media_inputs=[H3MediaInput(kind=H3MediaKind.AUDIO, role=H3MediaRole.REFERENCE_AUDIO,
                source=H3MediaSource(external_url="https://example.invalid/voice.wav"))])


def test_overlong_group_feedback_has_actual_duration_and_local_edit_target(tmp_path: Path) -> None:
    service, fake, doc, _unit = _setup(tmp_path)
    board = service.get_storyboard(doc.project.id)
    changed = board.storyboard.shots[0].model_copy(update={"duration_seconds": 8.0})
    group = board.storyboard.clip_groups[0].model_copy(update={"duration_seconds": 16})
    service.patch_storyboard(doc.project.id, StoryboardPatchRequest(base_version_id=board.version.id,
        origin=ProductionDocumentOrigin.AGENT, replacements=[StoryboardShotReplacement(shot_id=changed.id,
            expected_revision=changed.revision, replacement=changed)], clip_groups=[group]))
    result = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(clip_group_id=group.id))
    assert result.status == "BLOCKED" and result.error is not None
    assert group.id in result.error and "16 seconds" in result.error and "4–15" in result.error
    assert "shot.patch_storyboard" in result.error and not fake.calls


def test_default_voice_duration_limit_does_not_silently_drop_later_speaker(tmp_path: Path) -> None:
    service, _fake, doc, unit = _setup(tmp_path)
    for character in ("character-linxia", "character-zhouheng"):
        _voice(service, tmp_path, doc.project.id, character)
    doc, shots, _media = _inputs(service, doc.project.id, unit.shot_ids)
    script = next(v for v in doc.script_versions if v.id == doc.project.current_script_version_id)
    shots[1].dialogue_line_ids = [line.id for line in script.lines[:2]]
    bound_assets = {v.asset_id for v in doc.voice_references}
    for asset in doc.assets:
        if asset.id in bound_assets:
            next(v for v in asset.versions if v.id == asset.current_version_id).duration_seconds = 10
    with pytest.raises(ValueError, match="totals at least 20 seconds.*15 seconds combined"):
        generation_unit_voice_reference_inputs(doc, shots, selected_media_inputs=[])


def test_unselected_older_draft_audio_and_unknown_subject_are_not_available(tmp_path: Path) -> None:
    service, _fake, doc, _unit = _setup(tmp_path)
    voice = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert voice.asset is not None and voice.version is not None
    first_draft = service.import_local_asset_version(doc.project.id, voice.asset.id,
        AssetImportLocalRequest(local_path=str(tmp_path / "character-linxia.wav"), descriptor="Earlier draft")).version
    current = service.import_local_asset_version(doc.project.id, voice.asset.id,
        AssetImportLocalRequest(local_path=str(tmp_path / "character-linxia.wav"), descriptor="Current draft")).version
    doc = service.get_project(doc.project.id).document
    assert first_draft.approval_status == current.approval_status == ApprovalStatus.DRAFT
    assert current.id in _available_media_inputs(doc) and first_draft.id not in _available_media_inputs(doc)
    with pytest.raises(AssetReferenceTruthError, match="currently selected"):
        resolve_current_asset_media(doc, asset_version_id=first_draft.id, subject_asset_id=voice.asset.id)
    with pytest.raises(AssetReferenceTruthError, match="Unknown subject Asset"):
        resolve_current_asset_media(doc, asset_version_id=current.id, subject_asset_id="absent-character")


def test_irrelevant_bad_voice_and_explicit_empty_inputs_do_not_add_global_audio(tmp_path: Path) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    lin = _voice(service, tmp_path, doc.project.id, "character-linxia")
    zhou = _voice(service, tmp_path, doc.project.id, "character-zhouheng")
    assert lin.version is not None and zhou.asset is not None and zhou.version is not None
    doc = service.get_project(doc.project.id).document
    invalid = next(v for a in doc.assets if a.id == zhou.asset.id for v in a.versions if v.id == zhou.version.id)
    invalid.approval_status = ApprovalStatus.REJECTED
    service.repository.save_project(doc)
    doc, shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    assert [m.asset_version_id for m in media if m.kind == H3MediaKind.AUDIO] == [lin.version.id]
    # An explicit native-audio/direct choice does not authorize omitting a
    # still-selected generation image. Clear that visual choice first.
    shots[0].references = []
    service.repository.save_project(doc)
    fake.output_text = build_h3_generation_unit_prompt(doc, unit, shots, route=VideoGenerationRoute.DIRECT, media_inputs=[])
    response = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=shots[0].id, media_inputs=[]))
    assert response.status == "PREPARED", response.error
    saved = service.repository.get_h3_group_operation(response.operation_id)
    assert saved is not None and saved.resolved_route == VideoGenerationRoute.DIRECT and saved.resolved_media_inputs == []
    assert len(fake.calls) == 1 and service.repository.list_tasks(doc.project.id) == []


@pytest.mark.parametrize("seconds", [1.0, 16.0])
def test_known_invalid_audio_duration_is_rejected_for_default_and_explicit_inputs(tmp_path: Path, seconds: float) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    voice = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert voice.asset is not None
    doc, _shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    path = tmp_path / "imported-voice.wav"
    _render_audio(path, duration=seconds)
    current = service.import_local_asset_version(doc.project.id, voice.asset.id,
        AssetImportLocalRequest(local_path=str(path), descriptor="Current imported voice")).version
    assert current.duration_seconds == seconds and current.approval_status == ApprovalStatus.DRAFT
    explicit = [item if item.kind != H3MediaKind.AUDIO else item.model_copy(update={
        "asset_version_id": current.id, "source": H3MediaSource(local_path=current.local_path)}) for item in media]
    for selected in (None, explicit):
        response = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(
            shot_id=unit.shot_ids[0], media_inputs=selected))
        assert response.status == "BLOCKED", response
        assert current.id in (response.error or "") and "林夏" in (response.error or "")
        assert f"{seconds:g} seconds" in (response.error or "") and "2–15" in (response.error or "")
    assert not fake.calls and service.repository.list_tasks(doc.project.id) == []


def test_audio_duration_without_saved_metadata_remains_unknown(tmp_path: Path) -> None:
    service, fake, doc, unit = _setup(tmp_path)
    voice = _voice(service, tmp_path, doc.project.id, "character-linxia")
    assert voice.version is not None
    doc, shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    version = next(v for a in doc.assets for v in a.versions if v.id == voice.version.id)
    version.duration_seconds = None
    validate_h3_audio_durations(doc, media)
    service.repository.save_project(doc)
    fake.output_text = build_h3_generation_unit_prompt(doc, unit, shots, route=VideoGenerationRoute.REFERENCE, media_inputs=media)
    response = service.prepare_h3_group(doc.project.id, H3GroupOperationRequest(shot_id=unit.shot_ids[0]))
    assert response.status == "PREPARED", response.error
    assert version.duration_seconds is None and len(fake.calls) == 1
    assert service.repository.list_tasks(doc.project.id) == []


def test_saved_worker_input_rechecks_known_audio_duration_before_submission(tmp_path: Path) -> None:
    service, _fake, doc, unit = _setup(tmp_path / "sources")
    voice = _voice(service, tmp_path / "sources", doc.project.id, "character-linxia")
    assert voice.version is not None
    doc, _shots, media = _inputs(service, doc.project.id, unit.shot_ids)
    # Start with the existing legacy-task fixture; no enqueue or remote advance.
    harness = _harness(tmp_path / "worker")
    harness.document.assets = doc.assets
    harness.document.shots = doc.shots
    harness.run.media_inputs = media
    harness.repository.save_project(harness.document)
    assert harness.worker.input_staleness_reason(harness.task, harness.run) is None
    version = next(v for a in harness.document.assets for v in a.versions if v.id == voice.version.id)
    version.duration_seconds = 1.0
    harness.repository.save_project(harness.document)
    reason = harness.worker.input_staleness_reason(harness.task, harness.run)
    assert voice.version.id in (reason or "") and "1 seconds" in (reason or "") and "2–15" in (reason or "")
