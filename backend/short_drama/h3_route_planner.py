"""Evidence-first production route planning for MiniMax-H3."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from short_drama.asset_reference_truth import (
    AssetReferenceTruthError,
    resolve_current_asset_media,
)
from short_drama.h3_models import (
    H3InspectionPolicy,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3PricingSnapshot,
    H3ProductionRoutePlan,
    H3RouteAssetPreparation,
    H3RouteAssetPreparationStatus,
    H3RoutePlanStatus,
    H3ShotRouteDirective,
    H3ShotRouteAction,
    H3ShotRoutePlan,
    H3ShotRouteReadiness,
    H3VideoGuide,
    H3VideoGuideKind,
    H3ProviderRun,
    derive_h3_reference_alias,
)
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    DomainModel,
    EvidencePurpose,
    Freshness,
    GenerationTask,
    ProjectDocument,
    QAReport,
    QaFailureCategory,
    QaStatus,
    ReferencePlanEntry,
    RouteEvidence,
    ShotPlan,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionScope,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
)
from short_drama.production_contracts import (
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    GenerationUnit,
)
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_generation_unit_prompt,
    build_h3_prompt,
    validate_h3_probe,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)
from short_drama.reference_planner import (
    KeyframeBriefCompileRequest,
    KeyframePosition,
    ReferenceSelection,
    compile_keyframe_brief,
)
from short_drama.style_context import declared_style_value
from short_drama.visual_input_quality import (
    ReferenceVisualCandidate,
    ReferenceVisualRerankRequest,
    VisualInputContractError,
    build_reference_visual_candidates,
)


class RoutePlanningError(ValueError):
    """Raised when a requested project or shot cannot be route-planned."""


class _AgentRouteChoice(DomainModel):
    shot_id: str
    candidate_id: str
    reference_selection_candidate_ids: list[str] | None = None
    rationale: str = Field(min_length=1, max_length=3000)
    qa_focus_categories: list[QaFailureCategory] = Field(
        default_factory=list[QaFailureCategory],
        max_length=12,
    )
    warnings: list[str] = Field(default_factory=list[str], max_length=16)


class _AgentRouteChoices(DomainModel):
    choices: list[_AgentRouteChoice]


class _RouteDecisionEvidence(DomainModel):
    take_quality: QAReport | None = None
    take_qa_status: QaStatus | None = None
    take_is_selected: bool | None = None
    prior_route_evidence: RouteEvidence | None = None


@dataclass
class _RouteCandidate:
    id: str
    action: H3ShotRouteAction
    route: VideoGenerationRoute
    prompt: str
    duration_seconds: int
    media_inputs: list[H3MediaInput]
    rationale: str
    reuse_take_id: str | None = None
    video_guide_id: str | None = None
    video_guide: H3VideoGuide | None = None
    evidence_ids: list[str] = field(default_factory=list[str])
    decision_evidence: _RouteDecisionEvidence | None = None
    reference_selection_candidates: list[ReferenceVisualCandidate] = field(
        default_factory=list[ReferenceVisualCandidate]
    )
    reference_max_selected: int | None = None
    selected_reference_candidate_ids: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])
    missing_inputs: list[str] = field(default_factory=list[str])
    preparations: list[H3RouteAssetPreparation] = field(default_factory=list[H3RouteAssetPreparation])
    readiness: H3ShotRouteReadiness = H3ShotRouteReadiness.READY


def build_h3_production_route_plan(
    *,
    document: ProjectDocument,
    tasks: list[GenerationTask],
    takes: list[Take],
    evidence: list[RouteEvidence],
    provider_runs: list[H3ProviderRun],
    inspections: list[VideoInspectionReport],
    pricing: H3PricingSnapshot,
    version: int,
    created_at: str,
    shot_ids: list[str],
    candidate_count: int,
    language_client: LanguageProviderClient | None,
    prepared_media_inputs: dict[str, list[H3MediaInput]] | None = None,
    video_guides: dict[str, list[H3VideoGuide]] | None = None,
    video_guide_warnings: list[str] | None = None,
    shot_directives: list[H3ShotRouteDirective] | None = None,
    generation_units: list[GenerationUnit] | None = None,
    generation_plan_document_id: str | None = None,
    generation_plan_version_id: str | None = None,
    storyboard_document_id: str | None = None,
    storyboard_version_id: str | None = None,
) -> H3ProductionRoutePlan:
    if generation_units is not None:
        if shot_ids:
            raise RoutePlanningError(
                "A route plan may target generation units or legacy shots, not both"
            )
        return _build_h3_generation_unit_route_plan(
            document=document,
            pricing=pricing,
            version=version,
            created_at=created_at,
            generation_units=generation_units,
            candidate_count=candidate_count,
            language_client=language_client,
            prepared_media_inputs=prepared_media_inputs or {},
            shot_directives=shot_directives or [],
            generation_plan_document_id=generation_plan_document_id,
            generation_plan_version_id=generation_plan_version_id,
            storyboard_document_id=storyboard_document_id,
            storyboard_version_id=storyboard_version_id,
            warnings=list(video_guide_warnings or []),
        )
    selected_ids = set(shot_ids)
    known_ids = {shot.id for shot in document.shots}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise RoutePlanningError("Unknown shot IDs: " + ", ".join(unknown))
    shots = [
        shot
        for shot in sorted(document.shots, key=lambda item: item.sequence)
        if not selected_ids or shot.id in selected_ids
    ]
    if not shots:
        raise RoutePlanningError("A production route plan requires at least one shot")
    directive_by_shot = {
        item.shot_id: item
        for item in (shot_directives or [])
        if item.shot_id
    }
    unexpected_directives = sorted(set(directive_by_shot) - {item.id for item in shots})
    if unexpected_directives:
        raise RoutePlanningError(
            "Shot route directives refer to shots outside this plan: "
            + ", ".join(unexpected_directives)
        )

    task_by_id = {item.id: item for item in tasks}
    run_by_task = {item.task_id: item for item in provider_runs}
    evidence_by_take = {
        item.take_id: item for item in evidence if item.take_id is not None
    }
    inspections_by_take: dict[str, list[VideoInspectionReport]] = {}
    for report in inspections:
        inspections_by_take.setdefault(report.take_id, []).append(report)
    plan_by_shot = {item.shot_id: item for item in document.reference_plan}

    candidates_by_shot: dict[str, list[_RouteCandidate]] = {}
    risks_by_shot: dict[str, tuple[list[str], list[QaFailureCategory]]] = {}
    global_warnings: list[str] = list(video_guide_warnings or [])
    for shot in shots:
        reference_plan = plan_by_shot.get(shot.id, ReferencePlanEntry(
            shot_id=shot.id,
            bindings=[],
            capability_valid=True,
            validation_notes=[],
        ))
        risk_ids, qa_focus = _shot_risks(shot, reference_plan, evidence, takes)
        risks_by_shot[shot.id] = (risk_ids, qa_focus)
        applied_repair_strategy = _applied_director_strategy(document, shot.id)
        candidates_by_shot[shot.id] = _build_candidates(
            document=document,
            shot=shot,
            reference_plan=reference_plan,
            tasks=tasks,
            takes=takes,
            evidence=evidence,
            task_by_id=task_by_id,
            run_by_task=run_by_task,
            evidence_by_take=evidence_by_take,
            inspections_by_take=inspections_by_take,
            prepared_media_inputs=(prepared_media_inputs or {}).get(shot.id, []),
            video_guides=(video_guides or {}).get(shot.id, []),
            applied_repair_strategy=applied_repair_strategy,
        )

    agent_choices: dict[str, _AgentRouteChoice] = {}
    planner = "evidence-first-route-agent-v2"
    if language_client is not None:
        used_language_agent = False
        for batch in _route_agent_batches(shots):
            try:
                agent_choices.update(
                    _choose_with_language_agent(
                        document=document,
                        shots=batch,
                        candidates_by_shot=candidates_by_shot,
                        risks_by_shot=risks_by_shot,
                        client=language_client,
                    )
                )
                used_language_agent = True
            except (LanguageProviderError, ValueError) as exc:
                global_warnings.append(
                    "GPT-5.6-sol route advice unavailable for shots "
                    + ", ".join(shot.id for shot in batch)
                    + "; the technical baseline remains visible for debugging, but these "
                    + f"shots are waiting for a structured route decision: {exc}"
                )
        if used_language_agent:
            planner += "+gpt-5.6-sol"

    decisions: list[H3ShotRoutePlan] = []
    new_seconds = 0
    has_unresolved_route = False
    for shot in shots:
        candidates = candidates_by_shot[shot.id]
        current_reference_plan = plan_by_shot.get(
            shot.id,
            ReferencePlanEntry(
                shot_id=shot.id,
                bindings=[],
                capability_valid=True,
                validation_notes=[],
            ),
        )
        chosen = _technical_baseline_candidate(candidates)
        agent_choice = agent_choices.get(shot.id)
        choice_warnings: list[str] = []
        choice_focus: list[QaFailureCategory] = []
        has_structured_route_choice = False
        if agent_choice is not None:
            selected = next(
                (item for item in candidates if item.id == agent_choice.candidate_id),
                None,
            )
            if selected is None:
                choice_warnings.append(
                    "Language agent returned an unknown candidate; the direct technical baseline was retained."
                )
            else:
                try:
                    chosen = _materialize_reference_selection(
                        document=document,
                        shot=shot,
                        plan=current_reference_plan,
                        candidate=selected,
                        selection_candidate_ids=(
                            agent_choice.reference_selection_candidate_ids
                        ),
                    )
                except ValueError as exc:
                    choice_warnings.append(
                        "Route Agent returned an invalid typed reference selection; "
                        f"this shot remains unresolved: {exc}"
                    )
                else:
                    has_structured_route_choice = True
                    chosen.rationale = agent_choice.rationale
                    choice_warnings.extend(agent_choice.warnings)
                    choice_focus.extend(agent_choice.qa_focus_categories)

        directive = directive_by_shot.get(shot.id)
        if directive is not None and directive.candidate_id is not None:
            explicit_candidate = next(
                (item for item in candidates if item.id == directive.candidate_id),
                None,
            )
            if explicit_candidate is None:
                raise RoutePlanningError(
                    f"Explicit H3 route candidate {directive.candidate_id} is unavailable "
                    f"for shot {shot.id}"
                )
            try:
                chosen = _materialize_reference_selection(
                    document=document,
                    shot=shot,
                    plan=current_reference_plan,
                    candidate=explicit_candidate,
                    selection_candidate_ids=(
                        directive.reference_selection_candidate_ids
                    ),
                )
            except ValueError as exc:
                raise RoutePlanningError(
                    f"Explicit H3 reference selection is invalid for shot {shot.id}: {exc}"
                ) from exc
            has_structured_route_choice = True
        elif directive is not None and directive.reuse_take_id is not None:
            explicit_reuse = next(
                (
                    item
                    for item in candidates
                    if item.action == H3ShotRouteAction.REUSE_TAKE
                    and item.reuse_take_id == directive.reuse_take_id
                ),
                None,
            )
            if explicit_reuse is None:
                raise RoutePlanningError(
                    f"Explicit reuse Take {directive.reuse_take_id} is unavailable for shot {shot.id}"
                )
            chosen = explicit_reuse
            has_structured_route_choice = True

        if not has_structured_route_choice:
            has_unresolved_route = True
            choice_warnings.append(
                "No structured Route Agent decision or explicit route directive was available. "
                "DIRECT is shown only as a provider-compatible technical baseline for debugging; "
                "this shot is waiting for an Agent decision and cannot be executed."
            )

        risk_ids, risk_focus = risks_by_shot[shot.id]
        focus = list(dict.fromkeys([*risk_focus, *choice_focus]))
        inspection = _inspection_policy(shot, focus)
        warnings = list(dict.fromkeys([*chosen.warnings, *choice_warnings]))
        prior_inspections = inspections_by_take.get(chosen.reuse_take_id or "", [])
        if prior_inspections:
            warnings.append(
                f"Reusable Take already has {len(prior_inspections)} persisted video inspection(s)."
            )
        decision_readiness = (
            chosen.readiness
            if has_structured_route_choice
            else H3ShotRouteReadiness.NEEDS_AGENT_DECISION
        )
        if (
            has_structured_route_choice
            and chosen.action == H3ShotRouteAction.GENERATE
        ):
            new_seconds += chosen.duration_seconds * candidate_count
        decisions.append(
            H3ShotRoutePlan(
                shot_id=shot.id,
                shot_sequence=shot.sequence,
                input_script_version_id=document.project.current_script_version_id,
                input_bible_version_id=document.project.current_bible_version_id,
                input_shot_revision=shot.revision,
                action=chosen.action,
                route=chosen.route,
                duration_seconds=chosen.duration_seconds,
                prompt=chosen.prompt,
                media_inputs=[item.model_copy(deep=True) for item in chosen.media_inputs],
                reuse_take_id=chosen.reuse_take_id,
                route_evidence_ids=list(chosen.evidence_ids),
                rationale=chosen.rationale,
                risk_ids=risk_ids,
                qa_focus_categories=focus,
                warnings=warnings,
                missing_inputs=list(chosen.missing_inputs),
                asset_preparations=[
                    item.model_copy(deep=True) for item in chosen.preparations
                ],
                video_guides=[
                    item.model_copy(deep=True)
                    for item in (video_guides or {}).get(shot.id, [])
                ],
                selected_video_guide_id=chosen.video_guide_id,
                reference_selection_candidate_ids=list(
                    chosen.selected_reference_candidate_ids
                ),
                candidate_count=(
                    1
                    if chosen.action == H3ShotRouteAction.REUSE_TAKE
                    else candidate_count
                ),
                inspection=inspection,
                readiness=decision_readiness,
            )
        )

    status = (
        H3RoutePlanStatus.READY
        if all(
            item.readiness
            in {H3ShotRouteReadiness.READY, H3ShotRouteReadiness.READY_WITH_WARNINGS}
            for item in decisions
        )
        else H3RoutePlanStatus.PARTIAL
    )
    if has_unresolved_route:
        planner += "+awaiting-agent-decision"
    return H3ProductionRoutePlan(
        id=f"h3-route-plan-{uuid4().hex}",
        project_id=document.project.id,
        version=version,
        status=status,
        planner=planner,
        input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id,
        input_voice_reference_revision=document.project.voice_reference_revision,
        decisions=decisions,
        estimated_new_h3_seconds=new_seconds,
        estimated_cost_usd=round(new_seconds * pricing.output_per_second, 6),
        estimated_cost_credits=round(
            new_seconds * pricing.billing_output_per_second,
            6,
        ),
        warnings=global_warnings,
        created_at=created_at,
        updated_at=created_at,
    )


def _build_h3_generation_unit_route_plan(
    *,
    document: ProjectDocument,
    pricing: H3PricingSnapshot,
    version: int,
    created_at: str,
    generation_units: list[GenerationUnit],
    candidate_count: int,
    language_client: LanguageProviderClient | None,
    prepared_media_inputs: dict[str, list[H3MediaInput]],
    shot_directives: list[H3ShotRouteDirective],
    generation_plan_document_id: str | None,
    generation_plan_version_id: str | None,
    storyboard_document_id: str | None,
    storyboard_version_id: str | None,
    warnings: list[str],
) -> H3ProductionRoutePlan:
    if not generation_units:
        raise RoutePlanningError(
            "A generation-plan route requires at least one generation unit"
        )
    unit_ids = [item.id for item in generation_units]
    if len(unit_ids) != len(set(unit_ids)):
        raise RoutePlanningError("Generation-unit route targets must be unique")
    shot_by_id = {shot.id: shot for shot in document.shots}
    for unit in generation_units:
        unknown = [shot_id for shot_id in unit.shot_ids if shot_id not in shot_by_id]
        if unknown:
            raise RoutePlanningError(
                f"Generation unit {unit.id} references unknown shots: "
                + ", ".join(unknown)
            )
        stale = [
            window.shot_id
            for window in unit.shot_windows
            if shot_by_id[window.shot_id].revision != window.shot_revision
        ]
        if stale:
            raise RoutePlanningError(
                f"Generation unit {unit.id} uses outdated shot revisions: "
                + ", ".join(stale)
            )

    directive_by_unit = {
        item.generation_unit_id: item
        for item in shot_directives
        if item.generation_unit_id is not None
    }
    unexpected_directives = sorted(set(directive_by_unit) - set(unit_ids))
    if unexpected_directives:
        raise RoutePlanningError(
            "Route directives refer to generation units outside this plan: "
            + ", ".join(unexpected_directives)
        )

    candidates_by_unit: dict[str, list[_RouteCandidate]] = {}
    synthetic_by_unit: dict[str, ShotPlan] = {}
    for unit in generation_units:
        shots = [shot_by_id[shot_id] for shot_id in unit.shot_ids]
        synthetic_by_unit[unit.id] = _synthetic_shot_for_generation_unit(unit, shots)
        unit_inputs = prepared_media_inputs.get(unit.id)
        if unit_inputs is None:
            unit_inputs = [
                item.model_copy(deep=True)
                for shot_id in unit.shot_ids
                for item in prepared_media_inputs.get(shot_id, [])
            ]
        candidates_by_unit[unit.id] = _build_generation_unit_candidates(
            document=document,
            unit=unit,
            shots=shots,
            media_inputs=unit_inputs,
        )

    agent_choices: dict[str, _AgentRouteChoice] = {}
    planner = "evidence-first-generation-unit-route-agent-v1"
    if language_client is not None:
        synthetic_shots = [synthetic_by_unit[item.id] for item in generation_units]
        try:
            agent_choices = _choose_with_language_agent(
                document=document,
                shots=synthetic_shots,
                candidates_by_shot=candidates_by_unit,
                risks_by_shot={item.id: ([], []) for item in synthetic_shots},
                client=language_client,
            )
        except (LanguageProviderError, ValueError) as exc:
            warnings.append(
                "GPT-5.6-sol route advice unavailable for generation units; "
                "provider-compatible candidates remain visible, but execution waits for "
                f"a structured Agent decision: {exc}"
            )
        else:
            planner += "+gpt-5.6-sol"

    decisions: list[H3ShotRoutePlan] = []
    new_seconds = 0
    unresolved = False
    for unit in generation_units:
        shots = [shot_by_id[shot_id] for shot_id in unit.shot_ids]
        candidates = candidates_by_unit[unit.id]
        chosen = _technical_baseline_candidate(candidates)
        choice_warnings: list[str] = []
        has_structured_choice = False

        agent_choice = agent_choices.get(unit.id)
        if agent_choice is not None:
            selected = next(
                (item for item in candidates if item.id == agent_choice.candidate_id),
                None,
            )
            if selected is None:
                choice_warnings.append(
                    "Language Agent returned an unknown generation-unit candidate."
                )
            elif agent_choice.reference_selection_candidate_ids:
                choice_warnings.append(
                    "Generation-unit routes do not expose implicit reference-selection "
                    "candidates; choose one of the already materialized route candidates."
                )
            else:
                chosen = selected
                chosen.rationale = agent_choice.rationale
                choice_warnings.extend(agent_choice.warnings)
                has_structured_choice = True

        directive = directive_by_unit.get(unit.id)
        if directive is not None:
            if directive.reuse_take_id is not None:
                raise RoutePlanningError(
                    "Generation-unit Take reuse is only available after a matching "
                    "generation-unit candidate has been persisted"
                )
            if directive.reference_selection_candidate_ids:
                raise RoutePlanningError(
                    "Generation-unit directives must select a materialized candidate; "
                    "they cannot add implicit reference selections"
                )
            if directive.candidate_id is not None:
                selected = next(
                    (item for item in candidates if item.id == directive.candidate_id),
                    None,
                )
                if selected is None:
                    raise RoutePlanningError(
                        f"Explicit H3 route candidate {directive.candidate_id} is unavailable "
                        f"for generation unit {unit.id}"
                    )
                chosen = selected
                has_structured_choice = True

        if not has_structured_choice:
            unresolved = True
            choice_warnings.append(
                "No structured Route Agent decision or explicit generation-unit directive "
                "was available. DIRECT remains a technical baseline and is not executed yet."
            )
        readiness = (
            chosen.readiness
            if has_structured_choice
            else H3ShotRouteReadiness.NEEDS_AGENT_DECISION
        )
        if has_structured_choice and chosen.action == H3ShotRouteAction.GENERATE:
            new_seconds += chosen.duration_seconds * candidate_count
        first_shot = shots[0]
        decisions.append(
            H3ShotRoutePlan(
                shot_id=first_shot.id,
                shot_sequence=first_shot.sequence,
                generation_unit_id=unit.id,
                clip_group_id=unit.clip_group_id,
                output_slot_id=unit.output_slot_id,
                shot_ids=list(unit.shot_ids),
                input_shot_revisions={
                    item.shot_id: item.shot_revision for item in unit.shot_windows
                },
                shot_source_windows=[
                    item.model_copy(deep=True) for item in unit.shot_windows
                ],
                input_script_version_id=document.project.current_script_version_id,
                input_bible_version_id=document.project.current_bible_version_id,
                input_shot_revision=first_shot.revision,
                action=chosen.action,
                route=chosen.route,
                duration_seconds=chosen.duration_seconds,
                prompt=chosen.prompt,
                media_inputs=[item.model_copy(deep=True) for item in chosen.media_inputs],
                reuse_take_id=chosen.reuse_take_id,
                route_evidence_ids=list(chosen.evidence_ids),
                rationale=chosen.rationale,
                warnings=list(dict.fromkeys([*chosen.warnings, *choice_warnings])),
                missing_inputs=list(chosen.missing_inputs),
                candidate_count=(
                    1
                    if chosen.action == H3ShotRouteAction.REUSE_TAKE
                    else candidate_count
                ),
                inspection=_inspection_policy(synthetic_by_unit[unit.id], []),
                readiness=readiness,
            )
        )

    status = (
        H3RoutePlanStatus.READY
        if all(
            decision.readiness
            in {H3ShotRouteReadiness.READY, H3ShotRouteReadiness.READY_WITH_WARNINGS}
            for decision in decisions
        )
        else H3RoutePlanStatus.PARTIAL
    )
    if unresolved:
        planner += "+awaiting-agent-decision"
    return H3ProductionRoutePlan(
        id=f"h3-route-plan-{uuid4().hex}",
        project_id=document.project.id,
        version=version,
        status=status,
        planner=planner,
        input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id,
        input_storyboard_document_id=storyboard_document_id,
        input_storyboard_version_id=storyboard_version_id,
        input_generation_plan_document_id=generation_plan_document_id,
        input_generation_plan_version_id=generation_plan_version_id,
        input_voice_reference_revision=document.project.voice_reference_revision,
        decisions=decisions,
        estimated_new_h3_seconds=new_seconds,
        estimated_cost_usd=round(new_seconds * pricing.output_per_second, 6),
        estimated_cost_credits=round(
            new_seconds * pricing.billing_output_per_second,
            6,
        ),
        warnings=warnings,
        created_at=created_at,
        updated_at=created_at,
    )


def _build_generation_unit_candidates(
    *,
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    media_inputs: list[H3MediaInput],
) -> list[_RouteCandidate]:
    duration, duration_warnings, supported = _duration_for_h3(
        unit.requested_duration_seconds
    )
    direct_prompt = build_h3_generation_unit_prompt(
        document,
        unit,
        shots,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=duration,
    )
    candidates = [
        _RouteCandidate(
            id=f"direct:{unit.id}",
            action=H3ShotRouteAction.GENERATE,
            route=VideoGenerationRoute.DIRECT,
            prompt=direct_prompt,
            duration_seconds=duration,
            media_inputs=[],
            rationale=(
                "Generate this ordered editorial shot group as one H3 clip while retaining "
                "its typed source windows for later timeline splitting."
            ),
            warnings=duration_warnings,
            missing_inputs=(
                [] if supported else ["H3 supports 4–15 seconds per request"]
            ),
            readiness=(
                H3ShotRouteReadiness.READY_WITH_WARNINGS
                if supported and duration_warnings
                else (
                    H3ShotRouteReadiness.READY
                    if supported
                    else H3ShotRouteReadiness.UNSUPPORTED
                )
            ),
        )
    ]
    if not supported or not media_inputs:
        return candidates
    try:
        route = _generation_unit_media_route(media_inputs)
        prompt = build_h3_generation_unit_prompt(
            document,
            unit,
            shots,
            route=route,
            duration_seconds=duration,
            media_inputs=media_inputs,
        )
        validate_h3_probe(
            route=route,
            prompt=prompt,
            duration_seconds=duration,
            media_inputs=media_inputs,
        )
    except H3CapabilityError as exc:
        candidates[0].warnings.append(
            f"Prepared generation-unit media is not a valid H3 route: {exc}"
        )
        return candidates
    route_name = {
        VideoGenerationRoute.FRAME_FIRST: "frame-first",
        VideoGenerationRoute.FRAME_LAST: "frame-last",
        VideoGenerationRoute.FRAME_FIRST_LAST: "frame-first-last",
        VideoGenerationRoute.REFERENCE: "reference",
        VideoGenerationRoute.DIRECT: "direct",
    }[route]
    candidates.append(
        _RouteCandidate(
            id=f"{route_name}:{unit.id}",
            action=H3ShotRouteAction.GENERATE,
            route=route,
            prompt=prompt,
            duration_seconds=duration,
            media_inputs=[item.model_copy(deep=True) for item in media_inputs],
            rationale=(
                "Use the explicitly prepared media for the whole generation unit while "
                "preserving each editorial shot's declared source window."
            ),
            warnings=list(duration_warnings),
            readiness=(
                H3ShotRouteReadiness.READY_WITH_WARNINGS
                if duration_warnings
                else H3ShotRouteReadiness.READY
            ),
        )
    )
    return candidates


def _generation_unit_media_route(
    media_inputs: list[H3MediaInput],
) -> VideoGenerationRoute:
    roles = {item.role for item in media_inputs}
    reference_roles = {
        H3MediaRole.REFERENCE_IMAGE,
        H3MediaRole.REFERENCE_VIDEO,
        H3MediaRole.REFERENCE_AUDIO,
    }
    if roles & reference_roles:
        return VideoGenerationRoute.REFERENCE
    if roles == {H3MediaRole.FIRST_FRAME, H3MediaRole.LAST_FRAME}:
        return VideoGenerationRoute.FRAME_FIRST_LAST
    if roles == {H3MediaRole.FIRST_FRAME}:
        return VideoGenerationRoute.FRAME_FIRST
    if roles == {H3MediaRole.LAST_FRAME}:
        return VideoGenerationRoute.FRAME_LAST
    raise H3CapabilityError(
        "Prepared generation-unit media does not match one H3 route"
    )


def _synthetic_shot_for_generation_unit(
    unit: GenerationUnit,
    shots: list[ShotPlan],
) -> ShotPlan:
    first = shots[0]
    last = shots[-1]
    return first.model_copy(
        update={
            "id": unit.id,
            "sequence": first.sequence,
            "scene_id": first.scene_id,
            "duration_seconds": unit.requested_duration_seconds,
            "narrative_purpose": unit.generation_intent,
            "visual": " | ".join(shot.visual for shot in shots),
            "framing": " -> ".join(
                dict.fromkeys(shot.framing for shot in shots)
            ),
            "camera": " -> ".join(dict.fromkeys(shot.camera for shot in shots)),
            "audio_intent": " ".join(
                dict.fromkeys(shot.audio_intent for shot in shots)
            ),
            "dialogue_line_ids": [
                line_id for shot in shots for line_id in shot.dialogue_line_ids
            ],
            "timeline_beats": [],
            "continuity": first.continuity.model_copy(
                update={
                    "action_out": last.continuity.action_out,
                    "prop_state_out": last.continuity.prop_state_out,
                }
            ),
            "transition_out": last.transition_out,
            "merge_candidate": True,
            "local_rework_risk": " ".join(
                dict.fromkeys(shot.local_rework_risk for shot in shots)
            ),
            "revision": max(shot.revision for shot in shots),
        }
    )


def validate_route_plan_freshness(
    plan: H3ProductionRoutePlan,
    document: ProjectDocument,
) -> None:
    if plan.input_script_version_id != document.project.current_script_version_id:
        raise RoutePlanningError("Route plan uses an outdated script version")
    if plan.input_bible_version_id != document.project.current_bible_version_id:
        raise RoutePlanningError("Route plan uses an outdated project Bible")
    if plan.input_voice_reference_revision != document.project.voice_reference_revision:
        raise RoutePlanningError("Route plan uses outdated character voice references")
    if plan.input_storyboard_document_id is not None:
        if plan.input_storyboard_document_id != document.storyboard_document_id:
            raise RoutePlanningError("Route plan uses a different Storyboard document")
        storyboard_document = next(
            (
                item
                for item in document.production_documents
                if item.id == plan.input_storyboard_document_id
            ),
            None,
        )
        if (
            storyboard_document is None
            or storyboard_document.current_version_id
            != plan.input_storyboard_version_id
        ):
            raise RoutePlanningError("Route plan uses an outdated Storyboard version")
    if plan.input_generation_plan_document_id is not None:
        if (
            plan.input_generation_plan_document_id
            != document.generation_plan_document_id
        ):
            raise RoutePlanningError(
                "Route plan uses a different GenerationPlan document"
            )
        generation_document = next(
            (
                item
                for item in document.production_documents
                if item.id == plan.input_generation_plan_document_id
            ),
            None,
        )
        if (
            generation_document is None
            or generation_document.current_version_id
            != plan.input_generation_plan_version_id
        ):
            raise RoutePlanningError(
                "Route plan uses an outdated GenerationPlan version"
            )
        generation_version = next(
            (
                item
                for item in generation_document.versions
                if item.id == plan.input_generation_plan_version_id
            ),
            None,
        )
        generation_slot = next(
            (
                item
                for item in document.output_slots
                if item.id == GENERATION_PLAN_OUTPUT_SLOT_ID
            ),
            None,
        )
        if (
            generation_version is None
            or generation_slot is None
            or generation_slot.current_artifact_ref is None
            or generation_slot.current_artifact_ref.artifact_id
            != generation_document.id
            or generation_slot.current_artifact_ref.version
            != generation_version.version
        ):
            raise RoutePlanningError(
                "Route plan's GenerationPlan output slot is no longer current"
            )
    revision_by_shot = {item.id: item.revision for item in document.shots}
    stale: list[str] = []
    for item in plan.decisions:
        expected_revisions = item.input_shot_revisions or {
            item.shot_id: item.input_shot_revision
        }
        stale.extend(
            shot_id
            for shot_id, revision in expected_revisions.items()
            if revision_by_shot.get(shot_id) != revision
        )
    stale = list(dict.fromkeys(stale))
    if stale:
        raise RoutePlanningError(
            "Route plan uses outdated shot revisions: " + ", ".join(stale)
        )
    stale_guide_sources = [
        guide.source_shot_id
        for decision in plan.decisions
        if decision.selected_video_guide_id is not None
        for guide in decision.video_guides
        if guide.id == decision.selected_video_guide_id
        and revision_by_shot.get(guide.source_shot_id) != guide.source_shot_revision
    ]
    if stale_guide_sources:
        raise RoutePlanningError(
            "Route plan uses outdated video-guide source shots: "
            + ", ".join(stale_guide_sources)
        )


def _build_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    reference_plan: ReferencePlanEntry,
    tasks: list[GenerationTask],
    takes: list[Take],
    evidence: list[RouteEvidence],
    task_by_id: dict[str, GenerationTask],
    run_by_task: dict[str, H3ProviderRun],
    evidence_by_take: dict[str, RouteEvidence],
    inspections_by_take: dict[str, list[VideoInspectionReport]],
    prepared_media_inputs: list[H3MediaInput],
    video_guides: list[H3VideoGuide],
    applied_repair_strategy: DirectorRepairStrategy | None,
) -> list[_RouteCandidate]:
    del tasks, inspections_by_take
    duration, duration_warnings, supported = _duration_for_h3(shot.duration_seconds)
    direct_prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=duration,
    )
    candidates: list[_RouteCandidate] = []
    approved_inputs = _approved_media_inputs(document)
    reusable: list[Take] = []
    for take in takes:
        if take.shot_id != shot.id or not _take_is_available_for_reuse(
            document=document,
            shot=shot,
            take=take,
            task=task_by_id.get(take.task_id),
        ):
            continue
        run = run_by_task.get(take.task_id)
        if run is not None and len(
            _refresh_media_inputs(document, run.media_inputs, approved_inputs)
        ) != len(run.media_inputs):
            continue
        reusable.append(take)
    for take in sorted(
        reusable,
        key=lambda item: (item.candidate_index, item.created_at, item.id),
    ):
        route_evidence = evidence_by_take.get(take.id)
        run = run_by_task.get(take.task_id)
        candidates.append(
            _RouteCandidate(
                id=f"reuse:{take.id}",
                action=H3ShotRouteAction.REUSE_TAKE,
                route=take.route,
                prompt=run.prompt if run is not None else direct_prompt,
                duration_seconds=duration,
                media_inputs=(
                    [item.model_copy(deep=True) for item in run.media_inputs]
                    if run is not None
                    else []
                ),
                reuse_take_id=take.id,
                evidence_ids=(
                    [route_evidence.id] if route_evidence is not None else []
                ),
                rationale=(
                    "This current, locally available H3 Take can be reused without another "
                    "provider generation. Its recorded QA is decision evidence for the Route "
                    "Agent, not an automatic eligibility gate."
                ),
                decision_evidence=_take_decision_evidence(take, route_evidence),
                warnings=duration_warnings,
            )
        )

    templates: list[tuple[RouteEvidence, H3ProviderRun]] = []
    for item in evidence:
        if (
            item.shot_id != shot.id
            or item.purpose == EvidencePurpose.CONTRACT_SMOKE
        ):
            continue
        task = task_by_id.get(item.task_id)
        run = run_by_task.get(item.task_id)
        if (
            task is None
            or run is None
            or task.status != TaskStatus.SUCCEEDED
            or item.technical_passed is False
            or not _task_matches(document, shot, task)
        ):
            continue
        templates.append((item, run))
    if supported:
        for template_evidence, template_run in templates:
            template_inputs = _refresh_media_inputs(
                document,
                template_run.media_inputs,
                approved_inputs,
            )
            template_prompt = build_h3_prompt(
                document,
                shot,
                route=template_run.route,
                duration_seconds=duration,
                media_inputs=template_inputs,
                video_guides=video_guides,
            )
            try:
                validate_h3_probe(
                    route=template_run.route,
                    prompt=template_prompt,
                    duration_seconds=duration,
                    media_inputs=template_inputs,
                )
            except H3CapabilityError:
                continue
            candidates.append(
                _RouteCandidate(
                    id=f"template:{template_evidence.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=template_run.route,
                    prompt=template_prompt,
                    duration_seconds=duration,
                    media_inputs=template_inputs,
                    evidence_ids=[template_evidence.id],
                    decision_evidence=_RouteDecisionEvidence(
                        prior_route_evidence=template_evidence,
                    ),
                    rationale=(
                        "This prior technically usable route condition can be regenerated with "
                        "the current ShotPlan. Its review outcome is evidence for the Route Agent, "
                        "not an automatic ranking signal."
                    ),
                    warnings=duration_warnings,
                )
            )

    preparations = _asset_preparations(
        document,
        shot,
        reference_plan,
    )
    start_preparation = next(
        (item for item in preparations if item.role == H3MediaRole.FIRST_FRAME),
        None,
    )
    end_preparation = next(
        (item for item in preparations if item.role == H3MediaRole.LAST_FRAME),
        None,
    )
    first_frame = next(
        (
            item
            for item in prepared_media_inputs
            if item.role == H3MediaRole.FIRST_FRAME
        ),
        None,
    )
    last_frame = next(
        (
            item
            for item in prepared_media_inputs
            if item.role == H3MediaRole.LAST_FRAME
        ),
        None,
    )
    if supported and start_preparation is not None:
        if first_frame is None:
            candidates.append(
                _RouteCandidate(
                    id=f"frame-first-pending:{shot.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=VideoGenerationRoute.FRAME_FIRST,
                    prompt=build_h3_prompt(
                        document,
                        shot,
                        route=VideoGenerationRoute.FRAME_FIRST,
                        duration_seconds=duration,
                    ),
                    duration_seconds=duration,
                    media_inputs=[],
                    rationale=(
                        "Prepare one shot-specific start frame when stronger opening composition, "
                        "identity, or continuity control is useful. Interaction and repair history "
                        "are evidence for the Route Agent, not eligibility gates."
                    ),
                    warnings=[
                        *duration_warnings,
                        "The recommended Frame-first route needs an approved start frame before submission.",
                    ],
                    missing_inputs=[H3MediaRole.FIRST_FRAME.value],
                    preparations=[start_preparation.model_copy(deep=True)],
                    readiness=H3ShotRouteReadiness.NEEDS_ASSET,
                )
            )
        else:
            selected_preparation = start_preparation.model_copy(deep=True)
            selected_preparation.status = H3RouteAssetPreparationStatus.READY
            selected_preparation.approved_version_id = first_frame.asset_version_id
            frame_prompt = build_h3_prompt(
                document,
                shot,
                route=VideoGenerationRoute.FRAME_FIRST,
                duration_seconds=duration,
                media_inputs=[first_frame],
            )
            try:
                validate_h3_probe(
                    route=VideoGenerationRoute.FRAME_FIRST,
                    prompt=frame_prompt,
                    duration_seconds=duration,
                    media_inputs=[first_frame],
                )
            except H3CapabilityError:
                pass
            else:
                candidates.append(
                    _RouteCandidate(
                        id=f"frame-first:{shot.id}",
                        action=H3ShotRouteAction.GENERATE,
                        route=VideoGenerationRoute.FRAME_FIRST,
                        prompt=frame_prompt,
                        duration_seconds=duration,
                        media_inputs=[first_frame.model_copy(deep=True)],
                        rationale=(
                            "Use the approved shot-specific start frame to control identity, "
                            "composition, action readiness, and continuity without constraining "
                            "the terminal performance with a last frame."
                        ),
                        warnings=duration_warnings,
                        preparations=[selected_preparation],
                    )
                )

    if supported and end_preparation is not None:
        if last_frame is None:
            candidates.append(
                _RouteCandidate(
                    id=f"frame-last-pending:{shot.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=VideoGenerationRoute.FRAME_LAST,
                    prompt=build_h3_prompt(
                        document,
                        shot,
                        route=VideoGenerationRoute.FRAME_LAST,
                        duration_seconds=duration,
                    ),
                    duration_seconds=duration,
                    media_inputs=[],
                    rationale=(
                        "Prepare one shot-specific final frame when the terminal pose, prop "
                        "state, composition, or reveal matters more than locking the opening. "
                        "The Route Agent must still compare backward-inference risk with the "
                        "other candidates."
                    ),
                    warnings=[
                        *duration_warnings,
                        "The Last-frame route needs an approved final frame before submission.",
                    ],
                    missing_inputs=[H3MediaRole.LAST_FRAME.value],
                    preparations=[end_preparation.model_copy(deep=True)],
                    readiness=H3ShotRouteReadiness.NEEDS_ASSET,
                )
            )
        else:
            selected_preparation = end_preparation.model_copy(deep=True)
            selected_preparation.status = H3RouteAssetPreparationStatus.READY
            selected_preparation.approved_version_id = last_frame.asset_version_id
            frame_prompt = build_h3_prompt(
                document,
                shot,
                route=VideoGenerationRoute.FRAME_LAST,
                duration_seconds=duration,
                media_inputs=[last_frame],
            )
            try:
                validate_h3_probe(
                    route=VideoGenerationRoute.FRAME_LAST,
                    prompt=frame_prompt,
                    duration_seconds=duration,
                    media_inputs=[last_frame],
                )
            except H3CapabilityError:
                pass
            else:
                candidates.append(
                    _RouteCandidate(
                        id=f"frame-last:{shot.id}",
                        action=H3ShotRouteAction.GENERATE,
                        route=VideoGenerationRoute.FRAME_LAST,
                        prompt=frame_prompt,
                        duration_seconds=duration,
                        media_inputs=[last_frame.model_copy(deep=True)],
                        rationale=(
                            "Use the approved shot-specific final frame to lock the terminal "
                            "pose, prop state, reveal, and composition while leaving the opening "
                            "for H3 to infer from the declared ShotPlan."
                        ),
                        warnings=duration_warnings,
                        preparations=[selected_preparation],
                    )
                )

    if (
        supported
        and start_preparation is not None
        and end_preparation is not None
    ):
        missing_frame_roles = [
            role.value
            for role, media_input in (
                (H3MediaRole.FIRST_FRAME, first_frame),
                (H3MediaRole.LAST_FRAME, last_frame),
            )
            if media_input is None
        ]
        both_preparations: list[H3RouteAssetPreparation] = []
        for preparation, media_input in (
            (start_preparation, first_frame),
            (end_preparation, last_frame),
        ):
            selected_preparation = preparation.model_copy(deep=True)
            if media_input is not None:
                selected_preparation.status = H3RouteAssetPreparationStatus.READY
                selected_preparation.approved_version_id = media_input.asset_version_id
            both_preparations.append(selected_preparation)
        if missing_frame_roles:
            candidates.append(
                _RouteCandidate(
                    id=f"frame-first-last-pending:{shot.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=VideoGenerationRoute.FRAME_FIRST_LAST,
                    prompt=build_h3_prompt(
                        document,
                        shot,
                        route=VideoGenerationRoute.FRAME_FIRST_LAST,
                        duration_seconds=duration,
                    ),
                    duration_seconds=duration,
                    media_inputs=[
                        item.model_copy(deep=True)
                        for item in (first_frame, last_frame)
                        if item is not None
                    ],
                    rationale=(
                        "Prepare the missing shot-specific boundary frame or frames only when "
                        "both the opening composition and terminal state must be locked. The "
                        "Route Agent should avoid this route when two anchors would make the "
                        "performance rigid or hide an incompatible action path."
                    ),
                    warnings=[
                        *duration_warnings,
                        "The First-and-last-frame route needs both approved boundary frames before submission.",
                    ],
                    missing_inputs=missing_frame_roles,
                    preparations=both_preparations,
                    readiness=H3ShotRouteReadiness.NEEDS_ASSET,
                )
            )
        else:
            assert first_frame is not None
            assert last_frame is not None
            boundary_inputs = [
                first_frame.model_copy(deep=True),
                last_frame.model_copy(deep=True),
            ]
            frame_prompt = build_h3_prompt(
                document,
                shot,
                route=VideoGenerationRoute.FRAME_FIRST_LAST,
                duration_seconds=duration,
                media_inputs=boundary_inputs,
            )
            try:
                validate_h3_probe(
                    route=VideoGenerationRoute.FRAME_FIRST_LAST,
                    prompt=frame_prompt,
                    duration_seconds=duration,
                    media_inputs=boundary_inputs,
                )
            except H3CapabilityError:
                pass
            else:
                candidates.append(
                    _RouteCandidate(
                        id=f"frame-first-last:{shot.id}",
                        action=H3ShotRouteAction.GENERATE,
                        route=VideoGenerationRoute.FRAME_FIRST_LAST,
                        prompt=frame_prompt,
                        duration_seconds=duration,
                        media_inputs=boundary_inputs,
                        rationale=(
                            "Use both approved boundary frames because this shot needs explicit "
                            "control of its opening composition and terminal visible state. H3 "
                            "must still have a plausible continuous action path between them."
                        ),
                        warnings=duration_warnings,
                        preparations=both_preparations,
                    )
                )

    if supported:
        reference_candidate = _reference_candidate(
            document=document,
            shot=shot,
            plan=reference_plan,
            duration=duration,
            approved_inputs=approved_inputs,
        )
        if reference_candidate is not None:
            candidates.append(reference_candidate)

        candidates.extend(
            _video_guide_candidates(
                document=document,
                shot=shot,
                guides=video_guides,
                duration=duration,
                approved_inputs=approved_inputs,
            )
        )

    direct_readiness = (
        H3ShotRouteReadiness.READY_WITH_WARNINGS
        if duration_warnings
        else H3ShotRouteReadiness.READY
    )
    if supported:
        candidates.append(
            _RouteCandidate(
                id=f"direct:{shot.id}",
                action=H3ShotRouteAction.GENERATE,
                route=VideoGenerationRoute.DIRECT,
                prompt=direct_prompt,
                duration_seconds=duration,
                media_inputs=[],
                rationale=(
                    "Direct H3 generation is the always-executable fallback. Missing visual "
                    "anchors remain advisory quality risks and do not block the user."
                ),
                warnings=duration_warnings,
                readiness=direct_readiness,
            )
        )
    if not candidates:
        candidates.append(
            _RouteCandidate(
                id=f"unsupported:{shot.id}",
                action=H3ShotRouteAction.GENERATE,
                route=VideoGenerationRoute.DIRECT,
                prompt=direct_prompt,
                duration_seconds=duration,
                media_inputs=[],
                rationale=(
                    "The current ShotPlan cannot be represented by one H3 request without "
                    "changing its duration; splitting is recommended but remains a Director choice."
                ),
                warnings=duration_warnings,
                missing_inputs=["H3 supports 4–15 seconds per request"],
                preparations=preparations,
                readiness=H3ShotRouteReadiness.UNSUPPORTED,
            )
        )
    return candidates


def _reference_candidate(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    duration: int,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> _RouteCandidate | None:
    try:
        selection_candidates, max_selected, selection_warnings = (
            build_reference_visual_candidates(
                document=document,
                shot=shot,
                plan=plan,
                request=ReferenceVisualRerankRequest(),
                bound_candidates=False,
            )
        )
    except VisualInputContractError:
        selection_candidates = []
        max_selected = plan.max_reference_images
        selection_warnings = []

    voice_inputs: list[H3MediaInput] = []
    used_aliases: set[str] = set()
    voice_references, voice_warnings = _voice_reference_inputs(
        document=document,
        shot=shot,
        approved_inputs=approved_inputs,
    )
    for _character_name, item in voice_references:
        voice_inputs.append(_route_media_with_unique_alias(item, used_aliases))
    if not selection_candidates and not voice_inputs:
        return None

    warnings: list[str] = list(
        dict.fromkeys(
            [
                *selection_warnings,
                *voice_warnings,
                *(
                    [
                        "Using custom voice input selects H3 Reference mode; it cannot be "
                        "combined with first/last-frame roles in the same request."
                    ]
                    if voice_references
                    else []
                ),
            ]
        )
    )
    if selection_candidates:
        return _RouteCandidate(
            id=f"reference:{shot.id}",
            action=H3ShotRouteAction.GENERATE,
            route=VideoGenerationRoute.REFERENCE,
            prompt=build_h3_prompt(
                document,
                shot,
                route=VideoGenerationRoute.DIRECT,
                duration_seconds=duration,
            ),
            duration_seconds=duration,
            media_inputs=voice_inputs,
            rationale=(
                "REFERENCE 路线有多个合法的结构化视觉候选。"
                "Route Agent 需根据镜头职责显式选择 candidate id；"
                "候选分数及原因只是证据，不代表本地自动结论。"
            ),
            reference_selection_candidates=selection_candidates,
            reference_max_selected=max_selected,
            warnings=warnings,
            readiness=H3ShotRouteReadiness.NEEDS_AGENT_DECISION,
        )

    inputs = [item.model_copy(deep=True) for item in voice_inputs]
    compiled_prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=duration,
        media_inputs=inputs,
    )
    try:
        validate_h3_probe(
            route=VideoGenerationRoute.REFERENCE,
            prompt=compiled_prompt,
            duration_seconds=duration,
            media_inputs=inputs,
        )
    except H3CapabilityError:
        return None
    readiness = (
        H3ShotRouteReadiness.READY_WITH_WARNINGS
        if warnings
        else H3ShotRouteReadiness.READY
    )
    return _RouteCandidate(
        id=f"reference:{shot.id}",
        action=H3ShotRouteAction.GENERATE,
        route=VideoGenerationRoute.REFERENCE,
        prompt=compiled_prompt,
        duration_seconds=duration,
        media_inputs=inputs,
        rationale=(
            "Use the typed character voice reference explicitly supplied by the user for this "
            "speaking shot. No visual reference candidate is available to select."
        ),
        warnings=warnings,
        readiness=readiness,
    )


def _materialize_reference_selection(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    candidate: _RouteCandidate,
    selection_candidate_ids: list[str] | None,
) -> _RouteCandidate:
    if candidate.route != VideoGenerationRoute.REFERENCE:
        if selection_candidate_ids:
            raise ValueError(
                "reference_selection_candidate_ids may only accompany a REFERENCE candidate"
            )
        return candidate
    if not candidate.reference_selection_candidates:
        if selection_candidate_ids:
            raise ValueError("This REFERENCE candidate has no visual selection candidates")
        return candidate
    if selection_candidate_ids is None:
        raise ValueError(
            "REFERENCE candidates with visual options require "
            "reference_selection_candidate_ids"
        )
    if len(selection_candidate_ids) != len(set(selection_candidate_ids)):
        raise ValueError("Reference selection candidate IDs must be unique")
    max_selected = candidate.reference_max_selected or plan.max_reference_images
    if len(selection_candidate_ids) > max_selected:
        raise ValueError(
            f"At most {max_selected} visual reference candidates may be selected"
        )
    candidates_by_id = {
        item.candidate_id: item for item in candidate.reference_selection_candidates
    }
    unknown = [
        item for item in selection_candidate_ids if item not in candidates_by_id
    ]
    if unknown:
        raise ValueError(
            "Unknown reference selection candidate IDs: " + ", ".join(unknown)
        )

    approved_inputs = _approved_media_inputs(document)
    selected_candidates = [
        candidates_by_id[item] for item in selection_candidate_ids
    ]
    inputs: list[H3MediaInput] = []
    used_aliases: set[str] = set()
    for selected_candidate in selected_candidates:
        inputs.append(
            _reference_selection_media_input(
                document=document,
                shot=shot,
                plan=plan,
                selected=selected_candidate,
                approved_inputs=approved_inputs,
                used_aliases=used_aliases,
            )
        )
    for item in candidate.media_inputs:
        inputs.append(_route_media_with_unique_alias(item, used_aliases))
    if not inputs:
        raise ValueError("The explicit REFERENCE selection produced no media inputs")

    prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=candidate.duration_seconds,
        media_inputs=inputs,
    )
    validate_h3_probe(
        route=VideoGenerationRoute.REFERENCE,
        prompt=prompt,
        duration_seconds=candidate.duration_seconds,
        media_inputs=inputs,
    )

    covered_requirement_ids = {
        requirement_id
        for item in selected_candidates
        for requirement_id in item.requirement_ids
    }
    missing_requirement_ids = [
        requirement.id
        for requirement in plan.requirements
        if requirement.required and requirement.id not in covered_requirement_ids
    ]
    warnings = list(candidate.warnings)
    if missing_requirement_ids:
        warnings.append(
            "Route Agent selected a usable REFERENCE set that does not cover these declared "
            "requirements: " + ", ".join(missing_requirement_ids)
        )
    return replace(
        candidate,
        prompt=prompt,
        media_inputs=inputs,
        selected_reference_candidate_ids=list(selection_candidate_ids),
        warnings=list(dict.fromkeys(warnings)),
        missing_inputs=missing_requirement_ids,
        readiness=(
            H3ShotRouteReadiness.READY_WITH_WARNINGS
            if warnings or missing_requirement_ids
            else H3ShotRouteReadiness.READY
        ),
    )


def _reference_selection_media_input(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    selected: ReferenceVisualCandidate,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
    used_aliases: set[str],
) -> H3MediaInput:
    try:
        resolve_current_asset_media(
            document,
            asset_version_id=selected.asset_version_id,
            subject_asset_id=selected.subject_asset_id,
        )
    except AssetReferenceTruthError as exc:
        raise ValueError(
            f"Reference candidate {selected.candidate_id} no longer matches subject truth: {exc}"
        ) from exc
    approved = approved_inputs.get(selected.asset_version_id)
    if approved is None:
        raise ValueError(
            f"Reference candidate {selected.candidate_id} no longer has approved media"
        )
    media_type, source = approved
    kind_and_role = {
        AssetMediaType.IMAGE: (H3MediaKind.IMAGE, H3MediaRole.REFERENCE_IMAGE),
        AssetMediaType.VIDEO: (H3MediaKind.VIDEO, H3MediaRole.REFERENCE_VIDEO),
        AssetMediaType.AUDIO: (H3MediaKind.AUDIO, H3MediaRole.REFERENCE_AUDIO),
    }.get(media_type)
    if kind_and_role is None:
        raise ValueError(
            f"Reference candidate {selected.candidate_id} has unsupported media"
        )
    selection = ReferenceSelection(
        pack_id=selected.pack_id,
        entry_id=selected.entry_id,
        subject_asset_id=selected.subject_asset_id,
        asset_version_id=selected.asset_version_id,
        role=selected.role,
        angle=selected.angle,
        framing=selected.framing,
        tags=list(selected.tags),
        description=selected.description,
        requirement_ids=list(selected.requirement_ids),
        score=selected.deterministic_score,
        reasons=list(selected.deterministic_reasons),
    )
    alias, purpose = _selected_reference_metadata(document, shot, plan, selection)
    alias = _unique_route_reference_alias(
        alias,
        stable_id=selected.candidate_id,
        used_aliases=used_aliases,
    )
    used_aliases.add(alias)
    kind, role = kind_and_role
    return H3MediaInput(
        kind=kind,
        role=role,
        source=source.model_copy(deep=True),
        asset_version_id=selected.asset_version_id,
        subject_asset_id=selected.subject_asset_id,
        reference_alias=alias,
        binding_purpose=purpose,
        reference_tags=list(selected.tags),
    )


def _selected_reference_metadata(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    selected: ReferenceSelection,
) -> tuple[str, str]:
    subject = next(
        (asset for asset in document.assets if asset.id == selected.subject_asset_id),
        None,
    )
    subject_name = subject.name if subject is not None else selected.subject_asset_id
    matching_cues = [
        (beat.start_seconds, cue)
        for beat in shot.timeline_beats
        for cue in beat.references
        if cue.subject_asset_id == selected.subject_asset_id
        and set(cue.required_tags).issubset(selected.tags)
    ]
    if matching_cues:
        _start, cue = max(
            matching_cues,
            key=lambda item: (len(item[1].required_tags), -item[0]),
        )
        return cue.alias, cue.purpose
    alias_parts = [subject_name, *selected.tags[:2]]
    if len(alias_parts) == 1:
        alias_parts.append(selected.role.value.lower())
    requirement_purposes = [
        requirement.purpose
        for requirement in plan.requirements
        if requirement.id in selected.requirement_ids
    ]
    return "_".join(alias_parts), "；".join(requirement_purposes) or selected.description


def _route_reference_alias(value: str) -> str:
    """Normalize generated route aliases while respecting H3's 80-character limit."""

    return derive_h3_reference_alias(value)


def _unique_route_reference_alias(
    value: str,
    *,
    stable_id: str,
    used_aliases: set[str],
) -> str:
    alias = _route_reference_alias(value)
    if alias not in used_aliases:
        return alias
    suffix = _route_reference_alias(stable_id)[-16:]
    alias = _route_reference_alias(
        f"{alias[: max(1, 79 - len(suffix))]}_{suffix}"
    )
    if alias in used_aliases:
        raise RoutePlanningError(
            f"Reference selections produced duplicate semantic alias @{alias}"
        )
    return alias


def _route_media_with_unique_alias(
    media: H3MediaInput,
    used_aliases: set[str],
) -> H3MediaInput:
    raw_alias = media.reference_alias or f"{media.kind.value}_{media.role.value}"
    stable_id = (
        media.subject_asset_id
        or media.asset_version_id
        or media.video_guide_id
        or raw_alias
    )
    alias = _unique_route_reference_alias(
        raw_alias,
        stable_id=stable_id,
        used_aliases=used_aliases,
    )
    used_aliases.add(alias)
    if alias == media.reference_alias:
        return media
    return media.model_copy(update={"reference_alias": alias})


def _video_guide_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    guides: list[H3VideoGuide],
    duration: int,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> list[_RouteCandidate]:
    voice_references, voice_warnings = _voice_reference_inputs(
        document=document,
        shot=shot,
        approved_inputs=approved_inputs,
    )
    result: list[_RouteCandidate] = []
    for guide in guides:
        guide_input = H3MediaInput(
            kind=H3MediaKind.VIDEO,
            role=H3MediaRole.REFERENCE_VIDEO,
            source=H3MediaSource(local_path=guide.media_path),
            video_guide_id=guide.id,
            reference_alias=_route_reference_alias(
                f"camera_previs_{guide.source_shot_id}"
                if guide.kind == H3VideoGuideKind.CAMERA_PREVIS
                else f"motion_continuity_{guide.source_shot_id}"
            ),
            binding_purpose=(
                "只锚定粗略运镜路径与节奏"
                if guide.kind == H3VideoGuideKind.CAMERA_PREVIS
                else "只锚定前镜终态、运动方向与兼容的镜头惯性"
            ),
        )
        used_aliases: set[str] = set()
        media_inputs = [_route_media_with_unique_alias(guide_input, used_aliases)]
        media_inputs.extend(
            _route_media_with_unique_alias(item, used_aliases)
            for _character_name, item in voice_references
        )
        compiled_prompt = build_h3_prompt(
            document,
            shot,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=duration,
            media_inputs=media_inputs,
            video_guides=[guide],
        )
        try:
            validate_h3_probe(
                route=VideoGenerationRoute.REFERENCE,
                prompt=compiled_prompt,
                duration_seconds=duration,
                media_inputs=media_inputs,
            )
        except H3CapabilityError:
            continue
        warnings = list(dict.fromkeys([*guide.warnings, *voice_warnings]))
        result.append(
            _RouteCandidate(
                id=f"video-guide:{guide.id}",
                action=H3ShotRouteAction.GENERATE,
                route=VideoGenerationRoute.REFERENCE,
                prompt=compiled_prompt,
                duration_seconds=duration,
                media_inputs=media_inputs,
                video_guide_id=guide.id,
                video_guide=guide,
                rationale=guide.rationale,
                warnings=warnings,
                readiness=(
                    H3ShotRouteReadiness.READY_WITH_WARNINGS
                    if warnings
                    else H3ShotRouteReadiness.READY
                ),
            )
        )
    return result


def _voice_reference_inputs(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> tuple[list[tuple[str, H3MediaInput]], list[str]]:
    if not document.voice_references or not shot.dialogue_line_ids:
        return [], []
    script = next(
        (
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        ),
        None,
    )
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )
    if script is None or bible is None:
        return [], []
    lines = {item.id: item for item in script.lines}
    characters = {item.id: item for item in bible.characters}
    bindings = {item.character_id: item for item in document.voice_references}
    assets = {item.id: item for item in document.assets}
    resolved: list[tuple[str, H3MediaInput]] = []
    warnings: list[str] = []
    seen_characters: set[str] = set()
    total_audio_seconds = 0.0
    for line_id in shot.dialogue_line_ids:
        line = lines.get(line_id)
        if line is None:
            continue
        character = (
            characters.get(line.speaker_character_id)
            if line.speaker_character_id is not None
            else None
        )
        if character is None or character.id in seen_characters:
            continue
        binding = bindings.get(character.id)
        if binding is None:
            continue
        if len(resolved) >= 3:
            warnings.append(
                "This shot has more than three supplied character voice references; H3 accepts "
                "three reference-audio clips, so later speakers keep native voice generation."
            )
            break
        asset = assets.get(binding.asset_id)
        approved_version_id = asset.approved_version_id if asset is not None else None
        approved = (
            approved_inputs.get(approved_version_id)
            if approved_version_id is not None
            else None
        )
        if asset is None or approved is None or approved[0] != AssetMediaType.AUDIO:
            warnings.append(
                f"Voice reference for {character.name} is unavailable; native H3 voice remains usable."
            )
            continue
        approved_version = next(
            (
                item
                for item in asset.versions
                if item.id == approved_version_id
            ),
            None,
        )
        duration_seconds = (
            approved_version.duration_seconds
            if approved_version is not None
            else None
        )
        if (
            duration_seconds is not None
            and total_audio_seconds + duration_seconds > 15.0
        ):
            warnings.append(
                "Supplied character voice references exceed H3's 15-second combined audio "
                f"limit; {character.name} keeps native voice generation in this shot."
            )
            continue
        _media_type, source = approved
        character_asset = next(
            (
                candidate
                for candidate in document.assets
                if candidate.kind == AssetKind.CHARACTER
                and candidate.bible_entity_id == character.id
            ),
            None,
        )
        resolved.append(
            (
                character.name,
                H3MediaInput(
                    kind=H3MediaKind.AUDIO,
                    role=H3MediaRole.REFERENCE_AUDIO,
                    source=source.model_copy(deep=True),
                    asset_version_id=approved_version_id,
                    subject_asset_id=(
                        character_asset.id if character_asset is not None else None
                    ),
                    reference_alias=_route_reference_alias(f"{character.name}声线"),
                    binding_purpose=f"只锚定{character.name}的声线与声音质感",
                ),
            )
        )
        total_audio_seconds += duration_seconds or 0.0
        seen_characters.add(character.id)
    return resolved, warnings


def _take_is_available_for_reuse(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    take: Take,
    task: GenerationTask | None,
) -> bool:
    if (
        take.provider != "minimax"
        or take.model != "MiniMax-H3"
        or take.freshness != Freshness.CURRENT
        or take.availability != TakeAvailability.AVAILABLE
        or task is None
        or task.status != TaskStatus.SUCCEEDED
        or not _task_matches(document, shot, task)
    ):
        return False
    path = Path(take.media_path).expanduser()
    return path.is_file() and path.stat().st_size > 0


def _take_decision_evidence(
    take: Take,
    route_evidence: RouteEvidence | None,
) -> _RouteDecisionEvidence:
    return _RouteDecisionEvidence(
        take_quality=take.qa_report,
        take_qa_status=take.qa_status,
        take_is_selected=take.is_selected,
        prior_route_evidence=route_evidence,
    )


def _task_matches(
    document: ProjectDocument,
    shot: ShotPlan,
    task: GenerationTask,
) -> bool:
    return (
        task.input_script_version_id == document.project.current_script_version_id
        and task.input_bible_version_id == document.project.current_bible_version_id
        and task.input_shot_revision == shot.revision
        and task.input_voice_reference_revision
        == document.project.voice_reference_revision
    )


def _approved_media_inputs(
    document: ProjectDocument,
) -> dict[str, tuple[AssetMediaType, H3MediaSource]]:
    result: dict[str, tuple[AssetMediaType, H3MediaSource]] = {}
    for asset in document.assets:
        for version in asset.versions:
            if (
                version.approval_status != ApprovalStatus.APPROVED
                or version.media_type == AssetMediaType.TEXT_REFERENCE
            ):
                continue
            if version.media_type == AssetMediaType.IMAGE:
                if version.freshness != Freshness.CURRENT:
                    continue
            elif asset.approved_version_id != version.id:
                continue
            if version.local_path is not None:
                source = H3MediaSource(local_path=version.local_path)
            elif version.external_url is not None:
                source = H3MediaSource(external_url=version.external_url)
            else:
                continue
            result[version.id] = (version.media_type, source)
    return result


def _refresh_media_inputs(
    document: ProjectDocument,
    inputs: list[H3MediaInput],
    approved: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> list[H3MediaInput]:
    refreshed: list[H3MediaInput] = []
    for item in inputs:
        if item.asset_version_id is None:
            if item.subject_asset_id is not None:
                continue
            refreshed.append(item.model_copy(deep=True))
            continue
        try:
            resolve_current_asset_media(
                document,
                asset_version_id=item.asset_version_id,
                subject_asset_id=item.subject_asset_id,
            )
        except AssetReferenceTruthError:
            continue
        approved_item = approved.get(item.asset_version_id)
        if approved_item is None:
            continue
        _media_type, source = approved_item
        refreshed.append(item.model_copy(deep=True, update={"source": source}))
    return refreshed


def _asset_preparations(
    document: ProjectDocument,
    shot: ShotPlan,
    plan: ReferencePlanEntry,
) -> list[H3RouteAssetPreparation]:
    result: list[H3RouteAssetPreparation] = []
    try:
        start = compile_keyframe_brief(
            document,
            shot,
            plan,
            KeyframeBriefCompileRequest(position=KeyframePosition.START),
        )
    except ValueError:
        start = None
    if start is not None:
        result.append(
            H3RouteAssetPreparation(
                id=f"route-asset-prep-{uuid4().hex}",
                shot_id=shot.id,
                role=H3MediaRole.FIRST_FRAME,
                name=f"镜头 {shot.sequence:02d} 起始关键帧",
                descriptor=f"镜头 {shot.sequence:02d} 的动作起点和连续性画面",
                prompt=start.prompt,
                input_version_ids=start.input_version_ids,
                rationale=(
                    "A shot-specific start frame can bake identity, location, prop state, "
                    "and action-ready composition into one H3 input. Declared interactions "
                    "and repair history are supporting evidence rather than eligibility rules."
                ),
            )
        )
    try:
        end = compile_keyframe_brief(
            document,
            shot,
            plan,
            KeyframeBriefCompileRequest(position=KeyframePosition.END),
        )
    except ValueError:
        end = None
    if end is not None:
        result.append(
            H3RouteAssetPreparation(
                id=f"route-asset-prep-{uuid4().hex}",
                shot_id=shot.id,
                role=H3MediaRole.LAST_FRAME,
                name=f"镜头 {shot.sequence:02d} 终态关键帧",
                descriptor=f"镜头 {shot.sequence:02d} 的动作终态和空间连续性画面",
                prompt=end.prompt,
                input_version_ids=end.input_version_ids,
                rationale=(
                    "An end frame may improve terminal-state control, but the Director "
                    "should compare L2VA or FL2VA with backward-inference and stiffness risk. "
                    "Interaction evidence can strengthen the case without gating the option."
                ),
            )
        )
    return result


def _shot_risks(
    shot: ShotPlan,
    plan: ReferencePlanEntry,
    evidence: list[RouteEvidence],
    takes: list[Take],
) -> tuple[list[str], list[QaFailureCategory]]:
    categories: list[QaFailureCategory] = []
    if plan.interactions:
        categories.extend(
            [
                QaFailureCategory.CONTACT_CONTINUITY,
                QaFailureCategory.SPATIAL_CONTINUITY,
            ]
        )
    failed_take_ids = {
        item.take_id
        for item in evidence
        if item.shot_id == shot.id and item.hard_gate_passed is False and item.take_id
    }
    for take in takes:
        if take.id not in failed_take_ids and not (
            take.shot_id == shot.id and take.qa_status == QaStatus.REJECTED
        ):
            continue
        categories.extend(issue.category for issue in take.qa_report.issues)
    categories = list(dict.fromkeys(categories))
    risk_ids: list[str] = []
    if any(
        item in categories
        for item in {
            QaFailureCategory.CONTACT_CONTINUITY,
            QaFailureCategory.SPATIAL_CONTINUITY,
        }
    ):
        risk_ids.append("complex-contact-motion")
    if QaFailureCategory.CHARACTER_ORIENTATION in categories:
        risk_ids.append("turn-and-crossing-orientation")
    if QaFailureCategory.COMPOSITION_QUALITY in categories:
        risk_ids.append("dense-composition-quality")
    return risk_ids, categories


def _inspection_policy(
    shot: ShotPlan,
    focus: list[QaFailureCategory],
) -> H3InspectionPolicy:
    max_frames = min(24, max(8, math.ceil(shot.duration_seconds * 1.5)))
    scopes = [VideoInspectionScope.INTRA_SHOT]
    if shot.sequence > 1:
        scopes.append(VideoInspectionScope.SHOT_BOUNDARY)
    review_focus = None
    if focus:
        review_focus = (
            "Prioritize visible evidence for: "
            + ", ".join(item.value for item in focus)
            + ". Do not infer unseen actions from sparse frames."
        )
    return H3InspectionPolicy(
        scopes=scopes,
        max_frames=max_frames,
        semantic_review=True,
        review_focus=review_focus,
    )


def _applied_director_strategy(
    document: ProjectDocument,
    shot_id: str,
) -> DirectorRepairStrategy | None:
    return next(
        (
            record.strategy
            for record in reversed(document.repair_history)
            if record.decision == DirectorRepairDecision.APPLIED
            and (
                record.source_shot_id == shot_id
                or shot_id in record.affected_shot_ids
            )
        ),
        None,
    )


def _duration_for_h3(duration_seconds: float) -> tuple[int, list[str], bool]:
    rounded = math.ceil(duration_seconds)
    if rounded > 15:
        return 15, [
            f"Shot duration {duration_seconds:g}s exceeds H3's 15s request limit; "
            "the Director may split the shot or use another coverage design."
        ], False
    if rounded < 4:
        return 4, [
            f"Shot duration {duration_seconds:g}s is below H3's 4s request minimum; "
            "the generated clip will need a trim decision."
        ], True
    return rounded, [], True


def _technical_baseline_candidate(
    candidates: list[_RouteCandidate],
) -> _RouteCandidate:
    baseline = next(
        (
            item
            for item in candidates
            if item.action == H3ShotRouteAction.GENERATE
            and item.route == VideoGenerationRoute.DIRECT
            and not item.media_inputs
            and item.decision_evidence is None
        ),
        None,
    )
    if baseline is None:
        raise RoutePlanningError(
            "H3 route candidates did not include a provider-compatible DIRECT baseline"
        )
    return baseline


def _route_agent_batches(
    shots: list[ShotPlan],
) -> list[list[ShotPlan]]:
    batches: list[list[ShotPlan]] = []
    current: list[ShotPlan] = []
    current_scene_id: str | None = None
    for shot in shots:
        scene_changed = bool(current) and shot.scene_id != current_scene_id
        if scene_changed:
            batches.append(current)
            current = []
        if not current:
            current_scene_id = shot.scene_id
        current.append(shot)
    if current:
        batches.append(current)
    return batches


def _choose_with_language_agent(
    *,
    document: ProjectDocument,
    shots: list[ShotPlan],
    candidates_by_shot: dict[str, list[_RouteCandidate]],
    risks_by_shot: dict[str, tuple[list[str], list[QaFailureCategory]]],
    client: LanguageProviderClient,
) -> dict[str, _AgentRouteChoice]:
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ROUTE_PLANNER,),
        include_constitution=False,
    )
    instructions = (
        "你是 AI 原生竖屏视频制片流程中的生成路线 Agent。"
        f"{skill_prompt.text} 只返回符合给定 schema 的 JSON；每个镜头从已提供的候选中"
        "选择一个，不得发明路线、资产 ID、Take ID 或供应商能力。当前版本且本地技术可用"
        "的 Take 只是一个创作选项，不会因 QA 结果自动入选或排除；只有当它的实际成片比可用的新生成"
        "路线更适合当前镜头时才选择复用。逐镜综合真实路线证据、已批准的视觉绑定、动作"
        "复杂度与 QA 风险。遵守当前 Bible 已声明的媒介和 Style，不补写上游没有声明的"
        "视觉媒介、表面、摄影或运动假设。你的建议和警告都是可覆盖的。长镜头需要结合"
        "时间过程和镜头边界观察，不能用稀疏抽帧断言某个动作没有发生。"
        "可选 video-guide 候选只是一种视觉控制，来源要么是同场景中已选定的前一条 Take，"
        "要么是当前镜头已批准的起始帧。即使提示词要求不要重复，原始前镜视频仍可能延续"
        "或复现其可见动作，因此需要判断错误继承面。只有目标镜头确实延续相同动作与相近"
        "机位时，才重点比较 TAIL_WINDOW 或 PREVIOUS_TAKE：前者适合承接边界姿态、道具"
        "状态、银幕方向或摄影机惯性，后者只在更大范围的调度与场景信息确有价值时使用。"
        "如果目标镜头改变景别、反打或开始新动作，应优先比较帧控制候选。以 "
        "FRAME_LAST 适合终态姿态、道具状态、揭示结果或落幅比开场更重要的镜头，但模型"
        "需要反推开场，不能把尾帧误当作首帧。FRAME_FIRST_LAST 同时锁定两端，只在动作"
        "路径确实相容且双端控制收益高于表演僵硬风险时选择。"
        "TARGET_START_FRAME 为底图的 CAMERA_PREVIS 可以在保留批准构图的同时试验粗略"
        "运镜；以 PREVIOUS_BOUNDARY_FRAME 为底图时可能继承错误机位。二维预演不能验证"
        "真实身体调度，也不能保证计划中的终态动作。若来源 Take 的 QA 问题可能传播，"
        "应避免选择相应 guide；所有 guide 都不是必选项。已有 Take 与历史"
        "路线的 QA 信息只是结构化决策证据，不是候选资格门禁或人工排序分。"
        "如果说话镜头有用户提供的角色声线参考，应把携带 reference_audio 的就绪候选纳入"
        "重点比较，但仍由本镜头的表演、已应用的 Director 修复和帧控制需要共同决定。"
        "如果提供了已应用的 Director 修复策略，应选择真正执行该修复的候选；只有明确判断"
        "现有 Take 已经满足修复后的创作意图时，才选择复用。"
        "当 REFERENCE 候选提供 reference_selection_candidates 时，必须在 "
        "reference_selection_candidate_ids 中显式返回你选择的候选 ID；可选择一个或多个，"
        "也可以在同时存在声线输入时明确返回空列表。请根据每项候选的主体、职责、角度、景别、"
        "标签、需求覆盖和具体理由作出选择。deterministic_score 及其 reasons 只是检索证据，"
        "不能代替你的语义判断，也不要求选择分数最高者。非 REFERENCE 候选不要返回该字段。"
    )
    payload = {
        "output_schema": _AgentRouteChoices.model_json_schema(),
        "project": {
            "id": document.project.id,
            "genre": next(
                (
                    declared_style_value(bible.style.genre) or ""
                    for bible in document.bible_versions
                    if bible.id == document.project.current_bible_version_id
                ),
                "",
            ),
        },
        "scene_contexts": [
            scene.model_dump(mode="json")
            for script in document.script_versions
            if script.id == document.project.current_script_version_id
            for scene in script.structured_scenes
            if any(shot.scene_id == scene.id for shot in shots)
        ],
        "shots": [
            {
                "shot": shot.model_dump(mode="json"),
                "risk_ids": risks_by_shot[shot.id][0],
                "qa_focus_categories": [
                    item.value for item in risks_by_shot[shot.id][1]
                ],
                "applied_director_repair_strategy": (
                    strategy.value
                    if (strategy := _applied_director_strategy(document, shot.id))
                    is not None
                    else None
                ),
                "candidates": [
                    {
                        "id": candidate.id,
                        "action": candidate.action.value,
                        "route": candidate.route.value,
                        "rationale": candidate.rationale,
                        "decision_evidence": (
                            candidate.decision_evidence.model_dump(mode="json")
                            if candidate.decision_evidence is not None
                            else None
                        ),
                        "reuse_take_id": candidate.reuse_take_id,
                        "evidence_ids": candidate.evidence_ids,
                        "media_roles": [
                            item.role.value for item in candidate.media_inputs
                        ],
                        "media_assets": [
                            _media_input_label(document, item)
                            for item in candidate.media_inputs
                        ],
                        "reference_selection_max_selected": (
                            candidate.reference_max_selected
                        ),
                        "reference_selection_candidates": [
                            {
                                "selection_candidate_id": item.candidate_id,
                                "candidate_id": item.candidate_id,
                                "pack_id": item.pack_id,
                                "entry_id": item.entry_id,
                                "subject_asset_id": item.subject_asset_id,
                                "asset_version_id": item.asset_version_id,
                                "role": item.role.value,
                                "angle": item.angle.value,
                                "framing": item.framing.value,
                                "tags": item.tags,
                                "description": item.description,
                                "requirement_ids": item.requirement_ids,
                                "decision_evidence": {
                                    "deterministic_score": item.deterministic_score,
                                    "deterministic_reasons": (
                                        item.deterministic_reasons
                                    ),
                                },
                            }
                            for item in candidate.reference_selection_candidates
                        ],
                        "warnings": candidate.warnings,
                        "missing_inputs": candidate.missing_inputs,
                        "readiness": candidate.readiness.value,
                        "video_guide": (
                            {
                                "id": candidate.video_guide.id,
                                "kind": candidate.video_guide.kind.value,
                                "source_shot_id": candidate.video_guide.source_shot_id,
                                "source_shot_revision": candidate.video_guide.source_shot_revision,
                                "source_take_id": candidate.video_guide.source_take_id,
                                "duration_seconds": candidate.video_guide.duration_seconds,
                                "source_in_seconds": candidate.video_guide.source_in_seconds,
                                "camera_motion": (
                                    candidate.video_guide.camera_motion.value
                                    if candidate.video_guide.camera_motion is not None
                                    else None
                                ),
                                "previs_base_source": (
                                    candidate.video_guide.previs_base_source.value
                                    if candidate.video_guide.previs_base_source is not None
                                    else None
                                ),
                                "base_asset_version_id": (
                                    candidate.video_guide.base_asset_version_id
                                ),
                                "source_qa_status": candidate.video_guide.source_qa_status.value,
                                "source_quality_score": candidate.video_guide.source_quality_score,
                                "source_issue_codes": candidate.video_guide.source_issue_codes,
                            }
                            if candidate.video_guide is not None
                            else None
                        ),
                    }
                    for candidate in candidates_by_shot[shot.id]
                ],
            }
            for shot in shots
        ],
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        max_output_tokens=8000,
    )
    parsed = _AgentRouteChoices.model_validate_json(_strip_json_fence(output))
    expected = {shot.id for shot in shots}
    choices = {item.shot_id: item for item in parsed.choices}
    if set(choices) != expected:
        raise ValueError("Route agent must return exactly one choice for every requested shot")
    return choices


def _media_input_label(
    document: ProjectDocument,
    media_input: H3MediaInput,
) -> dict[str, str | None]:
    asset = next(
        (
            candidate
            for candidate in document.assets
            if any(
                version.id == media_input.asset_version_id
                for version in candidate.versions
            )
        ),
        None,
    )
    return {
        "role": media_input.role.value,
        "asset_version_id": media_input.asset_version_id,
        "asset_id": asset.id if asset is not None else None,
        "asset_name": asset.name if asset is not None else None,
        "asset_kind": asset.kind.value if asset is not None else None,
    }


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
