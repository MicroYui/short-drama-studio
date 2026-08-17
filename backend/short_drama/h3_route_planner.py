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
    H3RoutePlanStatus,
    H3ShotRouteAction,
    H3ShotRoutePlan,
    H3ShotRouteReadiness,
    H3ProviderRun,
)
from short_drama.models import (
    ApprovalStatus,
    AssetMediaType,
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
    global_warnings: list[str] = []
    for shot in shots:
        reference_plan = plan_by_shot.get(shot.id, ReferencePlanEntry(
            shot_id=shot.id,
            bindings=[],
            capability_valid=True,
            validation_notes=[],
        ))
        risk_ids, qa_focus = _shot_risks(shot, reference_plan, evidence, takes)
        risks_by_shot[shot.id] = (risk_ids, qa_focus)
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
        )

    agent_choices: dict[str, _AgentRouteChoice] = {}
    planner = "evidence-first-route-agent-v1"
    needs_route_judgment = any(
        not any(item.action == H3ShotRouteAction.REUSE_TAKE for item in candidates)
        for candidates in candidates_by_shot.values()
    )
    if language_client is not None and needs_route_judgment:
        try:
            agent_choices = _choose_with_language_agent(
                document=document,
                shots=shots,
                candidates_by_shot=candidates_by_shot,
                risks_by_shot=risks_by_shot,
                client=language_client,
            )
            planner += "+gpt-5.6-sol"
        except (LanguageProviderError, ValueError) as exc:
            global_warnings.append(
                f"GPT-5.6-sol route advice unavailable; deterministic evidence ranking used: {exc}"
            )

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
        decisions=decisions,
        estimated_new_h3_seconds=new_seconds,
        estimated_cost_usd=round(new_seconds * pricing.output_per_second, 6),
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

    preparations = _asset_preparations(document, shot, reference_plan)
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
                preparations=preparations,
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
    if not inputs:
        return None
    try:
        validate_h3_probe(
            route=VideoGenerationRoute.REFERENCE,
            prompt=prompt,
            duration_seconds=duration,
            media_inputs=inputs,
        )
    except H3CapabilityError:
        return None
    readiness = (
        H3ShotRouteReadiness.READY_WITH_WARNINGS
        if preview.missing_requirement_ids or preview.warnings
        else H3ShotRouteReadiness.READY
    )
    return _RouteCandidate(
        id=f"reference:{shot.id}",
        action=H3ShotRouteAction.GENERATE,
        route=VideoGenerationRoute.REFERENCE,
        prompt=prompt,
        duration_seconds=duration,
        media_inputs=inputs,
        rationale=(
            "Bind the best approved reference-pack entries selected for the current shot."
        ),
        quality_score=500.0 - len(preview.missing_requirement_ids) * 50.0,
        warnings=list(preview.warnings),
        missing_inputs=list(preview.missing_requirement_ids),
        readiness=readiness,
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
) -> list[H3RouteAssetPreparation]:
    if shot.local_rework_risk not in {"高", "HIGH", "high"} and not plan.interactions:
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
    if shot.local_rework_risk in {"高", "HIGH", "high"}:
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
        "shots": [
            {
                "shot": shot.model_dump(mode="json"),
                "risk_ids": risks_by_shot[shot.id][0],
                "qa_focus_categories": [
                    item.value for item in risks_by_shot[shot.id][1]
                ],
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
                        "warnings": candidate.warnings,
                        "missing_inputs": candidate.missing_inputs,
                        "readiness": candidate.readiness.value,
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
        max_output_tokens=5000,
    )
    parsed = _AgentRouteChoices.model_validate_json(_strip_json_fence(output))
    expected = {shot.id for shot in shots}
    choices = {item.shot_id: item for item in parsed.choices}
    if set(choices) != expected:
        raise ValueError("Route agent must return exactly one choice for every requested shot")
    return choices


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
