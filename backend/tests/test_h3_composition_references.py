"""A real composition picture remains a shot anchor through current bindings and compilation."""

import json
from pathlib import Path

import pytest

from short_drama.h3_group_operation import H3GroupOperationRequest
from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.h3_route_planner import _reference_selection_media_input
from short_drama.models import ApprovalStatus, AssetMediaType, ImageReferenceRole, ReferenceAngle, ReferenceFraming, ReferenceRole, ShotReferenceCue, VideoGenerationRoute
from short_drama.providers.h3_language_prompt import normalize_language_prompt
from short_drama.providers.h3_media_bindings import resolve_h3_media_bindings
from short_drama.providers.minimax_h3_compiler import build_h3_generation_unit_prompt, compile_h3_request
from short_drama.visual_input_quality import ReferenceVisualCandidate
from tests.test_h3_generation_unit_language import _fixture


def test_composition_picture_does_not_create_or_consume_an_identity_subject(tmp_path: Path) -> None:
    _service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    one, two = document.assets[:2]
    inputs = [H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url=f"https://example.test/{i}.png"),
        subject_asset_id=subject, image_reference_role=purpose,
        binding_purpose="Only counter staging at 0 seconds" if purpose == ImageReferenceRole.COMPOSITION else "Same character identity")
        for i, subject, purpose in [(1, one.id, ImageReferenceRole.SUBJECT_IDENTITY),
                                   (2, two.id, ImageReferenceRole.COMPOSITION),
                                   (3, one.id, ImageReferenceRole.SUBJECT_IDENTITY)]]
    bindings = resolve_h3_media_bindings(document, inputs, [])
    assert [b.provider_label for b in bindings] == ["<Picture 1>", "<Picture 2>", "<Picture 3>"]
    assert [b.prompt_label for b in bindings] == ["<Subject 1>", "<Picture 2>", "<Subject 1>"]
    text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=inputs)
    assert "Only counter staging at 0 seconds" in text
    assert "<Subject 2>" not in text
    assert "not a hard" in text
    assert "<Picture 1>" in text and "<Picture 3>" in text


def test_current_composition_cue_reaches_group_compiler_and_saved_handoff_once(tmp_path: Path) -> None:
    service, fake, document, _generation, unit, _request = _fixture(tmp_path)
    asset = document.assets[0]
    version = asset.versions[0]
    version.media_type, version.approval_status = AssetMediaType.IMAGE, ApprovalStatus.DRAFT
    version.local_path, version.external_url = None, "https://example.test/staged-frame.png"
    asset.current_version_id = version.id
    for shot in document.shots[:2]:
        shot.references = [ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
            alias="counter_start", purpose="Opening counter staging, only the customer and server visible",
            image_reference_role=ImageReferenceRole.COMPOSITION)]
    service.repository.save_project(document)
    media = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url=version.external_url), asset_version_id=version.id,
        subject_asset_id=asset.id, reference_alias="counter_start",
        binding_purpose=shot.references[0].purpose, image_reference_role=ImageReferenceRole.COMPOSITION)
    fake.output_text = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=[media])
    result = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(shot_id=unit.shot_ids[0]))
    assert result.error is None, result.error
    assert result.input_strategy["route"] == "REFERENCE"
    saved = service.repository.get_h3_group_operation(result.operation_id)
    assert saved is not None and saved.resolved_media_inputs == [media]
    assert "<Subject 1>" not in (saved.compiled_prompt or "")
    payload = json.loads(fake.calls[0][1])
    assert payload["route_media_bindings"][0]["image_reference_role"] == "COMPOSITION"
    assert payload["route_media_bindings"][0]["prompt_label"] == "<Picture 1>"
    assert len(fake.calls) == 1 and service.repository.list_tasks(document.project.id) == []
    resumed = service.prepare_h3_group(document.project.id, H3GroupOperationRequest(operation_id=result.operation_id))
    assert resumed.error is None and len(fake.calls) == 1


def test_hard_first_frame_still_cannot_mix_subject_or_soft_reference_images(tmp_path: Path) -> None:
    _fixture(tmp_path)
    frame = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(external_url="https://example.test/first.png"))
    soft = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url="https://example.test/soft.png"),
        image_reference_role=ImageReferenceRole.COMPOSITION)
    with pytest.raises(ValueError, match="reference|REFERENCE|mix"):
        compile_h3_request(prompt="A complete prepared opening frame develops into a short action.",
            route=VideoGenerationRoute.FRAME_FIRST, duration_seconds=8, media_inputs=[frame, soft],
            resolve_url=lambda media: media.source.external_url or "")


def test_single_shot_reference_selection_keeps_cue_purpose_and_normalizer_reconstructs_picture_alias(tmp_path: Path) -> None:
    _service, _fake, document, _generation, unit, _request = _fixture(tmp_path)
    asset, shot, plan = document.assets[0], document.shots[0], document.reference_plan[0]
    version = asset.versions[0]
    version.media_type, version.approval_status = AssetMediaType.IMAGE, ApprovalStatus.DRAFT
    version.local_path, version.external_url = None, "https://example.test/shot-layout.png"
    asset.current_version_id = version.id
    shot.references = [ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
        alias="prepared_layout", purpose="Opening camera projection and counter position only",
        image_reference_role=ImageReferenceRole.LAYOUT_GUIDE)]
    candidate = ReferenceVisualCandidate(candidate_id="layout", pack_id="selected", entry_id="selected",
        subject_asset_id=asset.id, asset_version_id=version.id, role=ReferenceRole.CANONICAL_MASTER,
        angle=ReferenceAngle.NOT_APPLICABLE, framing=ReferenceFraming.WIDE,
        description="Actual selected starting layout", deterministic_score=0)
    media = _reference_selection_media_input(document=document, shot=shot, plan=plan, selected=candidate,
        approved_inputs={version.id: (AssetMediaType.IMAGE, H3MediaSource(external_url=version.external_url))},
        used_aliases=set())
    assert media.image_reference_role == ImageReferenceRole.LAYOUT_GUIDE
    assert media.binding_purpose == shot.references[0].purpose
    prompt = build_h3_generation_unit_prompt(document, unit, document.shots[:2],
        route=VideoGenerationRoute.REFERENCE, media_inputs=[media])
    prompt = prompt.replace("@prepared_layout = <Picture 1>", "Declared staged image")
    normalized = normalize_language_prompt(prompt, document, route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8, media_inputs=[media], generation_unit=True)
    assert "@prepared_layout = <Picture 1>; <Picture 1> is a soft composition reference" in normalized
    assert "Opening camera projection and counter position only" in normalized
    assert "<Subject 1>" not in normalized
