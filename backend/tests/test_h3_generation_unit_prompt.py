"""Offline coverage for multi-shot H3 generation units."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.h3_models import (
    H3PricingSnapshot,
    H3ProductionRoutePlanRequest,
    H3RoutePlanStatus,
    H3ShotRouteDirective,
    H3ShotRouteReadiness,
)
from short_drama.h3_route_planner import build_h3_production_route_plan
from short_drama.production_contracts import GenerationUnit, GenerationUnitShotWindow
from short_drama.providers.h3_prompt_skill import (
    validate_h3_generation_unit_prompt_ir,
)
from short_drama.providers.minimax_h3_compiler import (
    build_h3_generation_unit_prompt,
)
from short_drama.service import ShortDramaService
from short_drama.models import VideoGenerationRoute


NOW = "2026-09-04T12:00:00+08:00"


def _fixture(tmp_path: Path):
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    return service.initialize_fixture().document


def _unit(document) -> GenerationUnit:
    shots = document.shots[:2]
    windows: list[GenerationUnitShotWindow] = []
    cursor = 0.0
    for shot in shots:
        windows.append(
            GenerationUnitShotWindow(
                shot_id=shot.id,
                shot_revision=shot.revision,
                source_in_seconds=cursor,
                source_out_seconds=cursor + shot.duration_seconds,
                duration_seconds=shot.duration_seconds,
            )
        )
        cursor += shot.duration_seconds
    return GenerationUnit(
        id="generation-unit-opening",
        clip_group_id="clip-group-opening",
        output_slot_id="video.generation-unit.generation-unit-opening",
        shot_ids=[shot.id for shot in shots],
        shot_windows=windows,
        requested_duration_seconds=cursor,
        generation_intent="Use the reaction cut to land the opening reveal quickly.",
        transition_intent="Preserve the declared editorial cut between the two shots.",
        continuity_in=["The opening hand position matches the prior clip."],
        visible_changes=["The second shot lands the visible reaction."],
        continuity_out=["The reaction pose is held for the next cut."],
        reference_notes=["Reuse the approved lead-character identity reference."],
        provider="minimax",
        model="MiniMax-H3",
        resolution="768P",
    )


def test_generation_unit_prompt_preserves_ordered_editorial_windows(
    tmp_path: Path,
) -> None:
    document = _fixture(tmp_path)
    unit = _unit(document)
    shots = document.shots[:2]

    prompt = build_h3_generation_unit_prompt(
        document,
        unit,
        shots,
        duration_seconds=13,
    )

    assert "integrated_multimodal_description:" in prompt
    assert "[Shot 1] [0.00-5.00 seconds]" in prompt
    assert "[Shot 2] [5.00-13.00 seconds]" in prompt
    assert "The second shot lands the visible reaction." in prompt
    assert "Reuse the approved lead-character identity reference." in prompt
    assert prompt.index("[Shot 1]") < prompt.index("[Shot 2]")
    checks = validate_h3_generation_unit_prompt_ir(
        prompt,
        document,
        unit,
        shots,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=13,
    )
    assert "2 editorial shot window(s) preserved in order" in checks


def test_generation_unit_route_plan_keeps_shot_boundaries_and_provenance(
    tmp_path: Path,
) -> None:
    document = _fixture(tmp_path)
    unit = _unit(document)

    plan = build_h3_production_route_plan(
        document=document,
        tasks=[],
        takes=[],
        evidence=[],
        provider_runs=[],
        inspections=[],
        pricing=H3PricingSnapshot(captured_at=NOW),
        version=1,
        created_at=NOW,
        shot_ids=[],
        candidate_count=1,
        language_client=None,
        shot_directives=[
            H3ShotRouteDirective(
                generation_unit_id=unit.id,
                candidate_id=f"direct:{unit.id}",
            )
        ],
        generation_units=[unit],
        generation_plan_document_id="generation-plan-document",
        generation_plan_version_id="generation-plan-version-1",
        storyboard_document_id="storyboard-document",
        storyboard_version_id="storyboard-version-1",
    )

    assert plan.status == H3RoutePlanStatus.READY
    assert plan.input_generation_plan_document_id == "generation-plan-document"
    assert plan.input_generation_plan_version_id == "generation-plan-version-1"
    assert plan.input_storyboard_document_id == "storyboard-document"
    assert plan.input_storyboard_version_id == "storyboard-version-1"
    assert len(plan.decisions) == 1
    decision = plan.decisions[0]
    assert decision.generation_unit_id == unit.id
    assert decision.clip_group_id == unit.clip_group_id
    assert decision.output_slot_id == unit.output_slot_id
    assert decision.shot_ids == unit.shot_ids
    assert decision.input_shot_revisions == {
        item.shot_id: item.shot_revision for item in unit.shot_windows
    }
    assert decision.shot_source_windows == unit.shot_windows
    assert decision.readiness == H3ShotRouteReadiness.READY
    assert decision.prompt.count("[Shot 1]") >= 1
    assert "[Shot 2]" in decision.prompt


def test_route_request_uses_legacy_shots_or_generation_plan_not_both() -> None:
    legacy = H3ProductionRoutePlanRequest(shot_ids=["shot-1"])
    assert legacy.generation_plan_document_id is None

    grouped = H3ProductionRoutePlanRequest(
        generation_plan_document_id="plan-document",
        generation_plan_version_id="plan-version",
        unit_ids=["unit-1"],
        shot_directives=[H3ShotRouteDirective(generation_unit_id="unit-1")],
    )
    assert grouped.unit_ids == ["unit-1"]

    with pytest.raises(ValidationError):
        H3ProductionRoutePlanRequest(
            shot_ids=["shot-1"],
            generation_plan_document_id="plan-document",
            generation_plan_version_id="plan-version",
        )
