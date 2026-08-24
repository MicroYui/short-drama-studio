"""Evidence-first production route planning for MiniMax-H3."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from pydantic import Field

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
    H3ShotRouteAction,
    H3ShotRoutePlan,
    H3ShotRouteReadiness,
    H3VideoGuide,
    H3VideoGuideKind,
    H3ProviderRun,
)
from short_drama.models import (
    ApprovalStatus,
    AssetMediaType,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    DomainModel,
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    GenerationTask,
    ProjectDocument,
    QaFailureCategory,
    QaStatus,
    ReferencePlanEntry,
    RouteEvidence,
    ShotPlan,
    Take,
    TakeAvailability,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionScope,
)
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    validate_h3_probe,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)
from short_drama.h3_video_guides import append_h3_video_guide_guidance
from short_drama.reference_planner import (
    KeyframeBriefCompileRequest,
    KeyframePosition,
    ReferencePlanPreviewRequest,
    compile_keyframe_brief,
    preview_reference_plan,
)


class RoutePlanningError(ValueError):
    """Raised when a requested project or shot cannot be route-planned."""


class _AgentRouteChoice(DomainModel):
    shot_id: str
    candidate_id: str
    rationale: str = Field(min_length=1, max_length=3000)
    qa_focus_categories: list[QaFailureCategory] = Field(
        default_factory=list[QaFailureCategory],
        max_length=12,
    )
    warnings: list[str] = Field(default_factory=list[str], max_length=16)


class _AgentRouteChoices(DomainModel):
    choices: list[_AgentRouteChoice]


@dataclass
class _RouteCandidate:
    id: str
    action: H3ShotRouteAction
    route: VideoGenerationRoute
    prompt: str
    duration_seconds: int
    media_inputs: list[H3MediaInput]
    rationale: str
    quality_score: float
    reuse_take_id: str | None = None
    video_guide_id: str | None = None
    video_guide: H3VideoGuide | None = None
    evidence_ids: list[str] = field(default_factory=list[str])
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
) -> H3ProductionRoutePlan:
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
    planner = "evidence-first-route-agent-v1"
    needs_route_judgment = any(
        not any(item.action == H3ShotRouteAction.REUSE_TAKE for item in candidates)
        for candidates in candidates_by_shot.values()
    )
    if language_client is not None and needs_route_judgment:
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
                    + f"; deterministic evidence ranking used: {exc}"
                )
        if used_language_agent:
            planner += "+gpt-5.6-sol"

    decisions: list[H3ShotRoutePlan] = []
    new_seconds = 0
    for shot in shots:
        candidates = candidates_by_shot[shot.id]
        chosen = _fallback_candidate(candidates)
        agent_choice = agent_choices.get(shot.id)
        choice_warnings: list[str] = []
        choice_focus: list[QaFailureCategory] = []
        if agent_choice is not None:
            selected = next(
                (item for item in candidates if item.id == agent_choice.candidate_id),
                None,
            )
            if selected is None:
                choice_warnings.append(
                    "Language agent returned an unknown candidate; evidence ranking was retained."
                )
            elif chosen.action != H3ShotRouteAction.REUSE_TAKE:
                chosen = selected
                chosen.rationale = agent_choice.rationale
                choice_warnings.extend(agent_choice.warnings)
                choice_focus.extend(agent_choice.qa_focus_categories)

        applied_repair_strategy = _applied_director_strategy(document, shot.id)
        if (
            applied_repair_strategy
            in {
                DirectorRepairStrategy.CHANGE_KEYFRAME,
                DirectorRepairStrategy.CHANGE_CAMERA,
            }
            and chosen.action == H3ShotRouteAction.GENERATE
            and chosen.route
            not in {
                VideoGenerationRoute.FRAME_FIRST,
                VideoGenerationRoute.FRAME_FIRST_LAST,
            }
        ):
            frame_candidates = [
                item
                for item in candidates
                if item.route
                in {
                    VideoGenerationRoute.FRAME_FIRST,
                    VideoGenerationRoute.FRAME_FIRST_LAST,
                }
            ]
            if frame_candidates:
                chosen = _fallback_candidate(frame_candidates)
                choice_warnings.append(
                    "The latest applied Director repair changes the keyframe or camera, so "
                    "AUTO_DRAFT will execute it with a matching frame-controlled route. The "
                    "user may override the repair."
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
        if chosen.action == H3ShotRouteAction.GENERATE:
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
                candidate_count=(
                    1
                    if chosen.action == H3ShotRouteAction.REUSE_TAKE
                    else candidate_count
                ),
                inspection=inspection,
                readiness=chosen.readiness,
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
    revision_by_shot = {item.id: item.revision for item in document.shots}
    stale = [
        item.shot_id
        for item in plan.decisions
        if revision_by_shot.get(item.shot_id) != item.input_shot_revision
    ]
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
    fresh_prompt = build_h3_prompt(document, shot)
    candidates: list[_RouteCandidate] = []
    reusable = [
        take
        for take in takes
        if take.shot_id == shot.id
        and _take_is_reusable(
            document=document,
            shot=shot,
            take=take,
            task=task_by_id.get(take.task_id),
            evidence=evidence_by_take.get(take.id),
        )
    ]
    if reusable:
        best = max(
            reusable,
            key=lambda item: (
                item.is_selected,
                item.qa_report.score,
                -item.candidate_index,
                item.created_at,
            ),
        )
        route_evidence = evidence_by_take[best.id]
        run = run_by_task.get(best.task_id)
        candidates.append(
            _RouteCandidate(
                id=f"reuse:{best.id}",
                action=H3ShotRouteAction.REUSE_TAKE,
                route=best.route,
                prompt=run.prompt if run is not None else fresh_prompt,
                duration_seconds=duration,
                media_inputs=(
                    [item.model_copy(deep=True) for item in run.media_inputs]
                    if run is not None
                    else []
                ),
                reuse_take_id=best.id,
                evidence_ids=[route_evidence.id],
                rationale=(
                    "Reuse the current-version H3 Take that already passed technical and "
                    "human quality review; no provider generation is needed."
                ),
                quality_score=1000.0 + best.qa_report.score,
                warnings=duration_warnings,
            )
        )

    approved_inputs = _approved_media_inputs(document)
    templates: list[tuple[RouteEvidence, H3ProviderRun]] = []
    for item in evidence:
        if (
            item.shot_id != shot.id
            or item.status != EvidenceStatus.REVIEWED
            or item.hard_gate_passed is not True
            or item.purpose == EvidencePurpose.CONTRACT_SMOKE
        ):
            continue
        task = task_by_id.get(item.task_id)
        run = run_by_task.get(item.task_id)
        if task is None or run is None or not _task_matches(document, shot, task):
            continue
        templates.append((item, run))
    if templates and supported:
        template_evidence, template_run = max(
            templates,
            key=lambda item: (item[0].score or 0.0, item[0].reviewed_at or ""),
        )
        template_inputs = _refresh_media_inputs(template_run.media_inputs, approved_inputs)
        try:
            validate_h3_probe(
                route=template_run.route,
                prompt=fresh_prompt,
                duration_seconds=duration,
                media_inputs=template_inputs,
            )
        except H3CapabilityError:
            pass
        else:
            candidates.append(
                _RouteCandidate(
                    id=f"template:{template_evidence.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=template_run.route,
                    prompt=fresh_prompt,
                    duration_seconds=duration,
                    media_inputs=template_inputs,
                    evidence_ids=[template_evidence.id],
                    rationale=(
                        "Reuse the highest-scoring reviewed route condition while compiling a "
                        "fresh prompt from the current ShotPlan."
                    ),
                    quality_score=700.0 + (template_evidence.score or 0.0),
                    warnings=duration_warnings,
                )
            )

    frame_control_repair_applied = (
        applied_repair_strategy
        in {
            DirectorRepairStrategy.CHANGE_KEYFRAME,
            DirectorRepairStrategy.CHANGE_CAMERA,
        }
    )
    preparations = _asset_preparations(
        document,
        shot,
        reference_plan,
        force_start=frame_control_repair_applied,
    )
    start_preparation = next(
        (item for item in preparations if item.role == H3MediaRole.FIRST_FRAME),
        None,
    )
    if supported and start_preparation is not None:
        first_frame = next(
            (
                item
                for item in prepared_media_inputs
                if item.role == H3MediaRole.FIRST_FRAME
            ),
            None,
        )
        selected_preparation = start_preparation
        if first_frame is None:
            candidates.append(
                _RouteCandidate(
                    id=f"frame-first-pending:{shot.id}",
                    action=H3ShotRouteAction.GENERATE,
                    route=VideoGenerationRoute.FRAME_FIRST,
                    prompt=fresh_prompt,
                    duration_seconds=duration,
                    media_inputs=[],
                    rationale=(
                        "Prepare one shot-specific start frame before H3 because the declared "
                        "interaction or local rework risk benefits from stronger composition and "
                        "continuity control. The user may still override this recommendation."
                    ),
                    quality_score=900.0 if frame_control_repair_applied else 600.0,
                    warnings=[
                        *duration_warnings,
                        "The recommended Frame-first route needs an approved start frame before submission.",
                    ],
                    missing_inputs=[H3MediaRole.FIRST_FRAME.value],
                    preparations=[selected_preparation],
                    readiness=H3ShotRouteReadiness.NEEDS_ASSET,
                )
            )
        else:
            selected_preparation.status = H3RouteAssetPreparationStatus.READY
            selected_preparation.approved_version_id = first_frame.asset_version_id
            try:
                validate_h3_probe(
                    route=VideoGenerationRoute.FRAME_FIRST,
                    prompt=fresh_prompt,
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
                        prompt=fresh_prompt,
                        duration_seconds=duration,
                        media_inputs=[first_frame.model_copy(deep=True)],
                        rationale=(
                            "Use the approved shot-specific start frame to control identity, "
                            "composition, action readiness, and continuity without constraining "
                            "the terminal performance with a last frame."
                        ),
                        quality_score=950.0 if frame_control_repair_applied else 650.0,
                        warnings=duration_warnings,
                        preparations=[selected_preparation],
                    )
                )

    if supported:
        reference_candidate = _reference_candidate(
            document=document,
            shot=shot,
            plan=reference_plan,
            duration=duration,
            prompt=fresh_prompt,
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
                prompt=fresh_prompt,
                approved_inputs=approved_inputs,
            )
        )

    direct_readiness = (
        H3ShotRouteReadiness.READY_WITH_WARNINGS
        if preparations or duration_warnings
        else H3ShotRouteReadiness.READY
    )
    if supported:
        candidates.append(
            _RouteCandidate(
                id=f"direct:{shot.id}",
                action=H3ShotRouteAction.GENERATE,
                route=VideoGenerationRoute.DIRECT,
                prompt=fresh_prompt,
                duration_seconds=duration,
                media_inputs=[],
                rationale=(
                    "Direct H3 generation is the always-executable fallback. Missing visual "
                    "anchors remain advisory quality risks and do not block the user."
                ),
                quality_score=100.0,
                warnings=[
                    *duration_warnings,
                    *(
                        ["Visual anchor preparation is recommended before paid generation."]
                        if preparations
                        else []
                    ),
                ],
                readiness=direct_readiness,
            )
        )
    if not candidates:
        candidates.append(
            _RouteCandidate(
                id=f"unsupported:{shot.id}",
                action=H3ShotRouteAction.GENERATE,
                route=VideoGenerationRoute.DIRECT,
                prompt=fresh_prompt,
                duration_seconds=duration,
                media_inputs=[],
                rationale=(
                    "The current ShotPlan cannot be represented by one H3 request without "
                    "changing its duration; splitting is recommended but remains a Director choice."
                ),
                quality_score=-1000.0,
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
    prompt: str,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> _RouteCandidate | None:
    preview = preview_reference_plan(
        document,
        shot,
        plan,
        ReferencePlanPreviewRequest(),
    )
    inputs: list[H3MediaInput] = []
    for selected in preview.selected:
        approved = approved_inputs.get(selected.asset_version_id)
        if approved is None:
            continue
        media_type, source = approved
        kind_and_role = {
            AssetMediaType.IMAGE: (H3MediaKind.IMAGE, H3MediaRole.REFERENCE_IMAGE),
            AssetMediaType.VIDEO: (H3MediaKind.VIDEO, H3MediaRole.REFERENCE_VIDEO),
            AssetMediaType.AUDIO: (H3MediaKind.AUDIO, H3MediaRole.REFERENCE_AUDIO),
        }.get(media_type)
        if kind_and_role is None:
            continue
        kind, role = kind_and_role
        inputs.append(
            H3MediaInput(
                kind=kind,
                role=role,
                source=source.model_copy(deep=True),
                asset_version_id=selected.asset_version_id,
            )
        )
    voice_references, voice_warnings = _voice_reference_inputs(
        document=document,
        shot=shot,
        approved_inputs=approved_inputs,
    )
    inputs.extend(item for _character_name, item in voice_references)
    if not inputs:
        return None
    compiled_prompt = _append_voice_reference_guidance(prompt, voice_references)
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
        if preview.missing_requirement_ids or preview.warnings or voice_warnings
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
            "Bind the best approved reference-pack entries selected for the current shot"
            + (
                " and the optional character voice reference supplied by the user."
                if voice_references
                else "."
            )
        ),
        quality_score=(
            (850.0 if voice_references else 500.0)
            - len(preview.missing_requirement_ids) * 50.0
        ),
        warnings=list(
            dict.fromkeys(
                [
                    *preview.warnings,
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
        ),
        missing_inputs=list(preview.missing_requirement_ids),
        readiness=readiness,
    )


def _video_guide_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    guides: list[H3VideoGuide],
    duration: int,
    prompt: str,
    approved_inputs: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> list[_RouteCandidate]:
    voice_references, voice_warnings = _voice_reference_inputs(
        document=document,
        shot=shot,
        approved_inputs=approved_inputs,
    )
    result: list[_RouteCandidate] = []
    score_hints = {
        H3VideoGuideKind.PREVIOUS_TAKE: 90.0,
        H3VideoGuideKind.TAIL_WINDOW: 95.0,
        H3VideoGuideKind.CAMERA_PREVIS: 85.0,
    }
    for guide in guides:
        guide_input = H3MediaInput(
            kind=H3MediaKind.VIDEO,
            role=H3MediaRole.REFERENCE_VIDEO,
            source=H3MediaSource(local_path=guide.media_path),
            video_guide_id=guide.id,
        )
        media_inputs = [
            guide_input,
            *(item for _character_name, item in voice_references),
        ]
        compiled_prompt = append_h3_video_guide_guidance(prompt, guide)
        compiled_prompt = _append_voice_reference_guidance(
            compiled_prompt,
            voice_references,
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
                quality_score=(
                    score_hints[guide.kind]
                    + min(0.9, guide.source_quality_score / 10.0)
                ),
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
        character = max(
            (
                item
                for item in bible.characters
                if item.name == line.speaker or item.name in line.speaker
            ),
            key=lambda item: len(item.name),
            default=None,
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
        resolved.append(
            (
                character.name,
                H3MediaInput(
                    kind=H3MediaKind.AUDIO,
                    role=H3MediaRole.REFERENCE_AUDIO,
                    source=source.model_copy(deep=True),
                    asset_version_id=approved_version_id,
                ),
            )
        )
        total_audio_seconds += duration_seconds or 0.0
        seen_characters.add(character.id)
    return resolved, warnings


def _append_voice_reference_guidance(
    prompt: str,
    voice_references: list[tuple[str, H3MediaInput]],
) -> str:
    if not voice_references:
        return prompt
    mapping = "；".join(
        f"<Audio {index}>只作为角色{character_name}的声线、音色与说话方式参考"
        for index, (character_name, _media) in enumerate(voice_references, start=1)
    )
    return (
        f"{prompt} 声线参考：{mapping}。"
        "不要复制参考音频中的原台词、环境声、背景音乐或节拍；"
        "本镜头仍只说前述锁定台词，并保持原定说话人。"
    )


def _take_is_reusable(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    take: Take,
    task: GenerationTask | None,
    evidence: RouteEvidence | None,
) -> bool:
    if (
        take.provider != "minimax"
        or take.model != "MiniMax-H3"
        or take.freshness != Freshness.CURRENT
        or take.qa_status != QaStatus.PASSED
        or take.availability != TakeAvailability.AVAILABLE
        or task is None
        or evidence is None
        or evidence.purpose == EvidencePurpose.CONTRACT_SMOKE
        or evidence.status != EvidenceStatus.REVIEWED
        or evidence.hard_gate_passed is not True
        or not _task_matches(document, shot, task)
    ):
        return False
    path = Path(take.media_path).expanduser()
    return path.is_file() and path.stat().st_size > 0


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
        if asset.approved_version_id is None:
            continue
        version = next(
            (item for item in asset.versions if item.id == asset.approved_version_id),
            None,
        )
        if (
            version is None
            or version.approval_status != ApprovalStatus.APPROVED
            or version.media_type == AssetMediaType.TEXT_REFERENCE
        ):
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
    inputs: list[H3MediaInput],
    approved: dict[str, tuple[AssetMediaType, H3MediaSource]],
) -> list[H3MediaInput]:
    refreshed: list[H3MediaInput] = []
    for item in inputs:
        if item.asset_version_id is None:
            refreshed.append(item.model_copy(deep=True))
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
    *,
    force_start: bool = False,
) -> list[H3RouteAssetPreparation]:
    risk_level = _declared_risk_level(shot.local_rework_risk)
    if (
        not force_start
        and risk_level not in {"MEDIUM", "HIGH"}
        and not plan.interactions
    ):
        return []
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
                    "and action-ready composition into one H3 input."
                ),
            )
        )
    if plan.interactions:
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
                        "should compare it with stiffness risk before choosing F2."
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
    if len(plan.action_beats) >= 2:
        categories.append(QaFailureCategory.CHARACTER_ORIENTATION)
    if _declared_risk_level(shot.local_rework_risk) == "HIGH":
        categories.append(QaFailureCategory.COMPOSITION_QUALITY)
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


def _declared_risk_level(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith(("高", "high")):
        return "HIGH"
    if normalized.startswith(("中", "medium", "moderate")):
        return "MEDIUM"
    if normalized.startswith(("低", "low")):
        return "LOW"
    return "UNKNOWN"


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


def _fallback_candidate(candidates: list[_RouteCandidate]) -> _RouteCandidate:
    return max(candidates, key=lambda item: item.quality_score)


def _route_agent_batches(
    shots: list[ShotPlan],
    *,
    maximum_size: int = 20,
) -> list[list[ShotPlan]]:
    batches: list[list[ShotPlan]] = []
    current: list[ShotPlan] = []
    current_scene_id: str | None = None
    for shot in shots:
        scene_changed = bool(current) and shot.scene_id != current_scene_id
        if scene_changed or len(current) >= maximum_size:
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
    instructions = (
        "You are a production route agent for a live-action vertical short drama. Return JSON "
        "only, matching the supplied schema. Choose exactly one supplied candidate per shot. "
        "Never invent routes, asset IDs, Take IDs, or provider capabilities. A current-version "
        "human-reviewed reusable Take is normally preferred over paying to regenerate. For gaps, "
        "compare empirical route evidence, approved visual bindings, action complexity, and QA "
        "risk. Recommendations and warnings are advisory and dismissible. Long shots require "
        "temporal and boundary observation; do not claim sparse frames prove an action absent."
        " Optional video-guide candidates are visual-only controls derived from the selected "
        "preceding Take in the same scene or from the current shot's approved start frame. Treat "
        "raw preceding-video references as likely to continue or repeat their visible action even "
        "when the prompt asks otherwise. Choose TAIL_WINDOW or PREVIOUS_TAKE mainly when the target "
        "genuinely continues the same action and similar coverage: TAIL_WINDOW for boundary pose, "
        "prop state, screen direction, or camera momentum; PREVIOUS_TAKE when wider choreography "
        "and location context justify its larger error-inheritance surface. When the target changes "
        "coverage, reverses angle, or begins a new action, compare frame-controlled candidates first. "
        "A CAMERA_PREVIS rooted in TARGET_START_FRAME can test coarse camera path while retaining the "
        "approved target composition; one rooted in PREVIOUS_BOUNDARY_FRAME may preserve the wrong "
        "coverage. A 2D previs cannot validate body blocking or guarantee the planned end action. "
        "Avoid a guide whose source QA issues are likely to propagate. These candidates are never "
        "mandatory. quality_score_hint is only a "
        "conservative deterministic fallback ordering, not a learned quality score."
        " When the user supplied a character voice reference for a speaking shot, normally "
        "prefer the ready candidate carrying reference_audio unless an applied Director repair "
        "or stronger frame-control need outweighs it."
        " When an applied Director repair strategy is supplied, choose a candidate that actually "
        "executes that repair unless a current-version reviewed Take is being reused."
    )
    payload = {
        "output_schema": _AgentRouteChoices.model_json_schema(),
        "project": {
            "id": document.project.id,
            "genre": next(
                (
                    bible.style.genre
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
                        "quality_score_hint": candidate.quality_score,
                        "reuse_take_id": candidate.reuse_take_id,
                        "evidence_ids": candidate.evidence_ids,
                        "media_roles": [
                            item.role.value for item in candidate.media_inputs
                        ],
                        "media_assets": [
                            _media_input_label(document, item)
                            for item in candidate.media_inputs
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
