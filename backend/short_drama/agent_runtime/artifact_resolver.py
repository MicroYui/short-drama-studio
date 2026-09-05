"""Read-only artifact summaries used to compile focused Agent context."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from typing import Protocol, cast

from short_drama.agent_runtime.models import ArtifactRef, ArtifactSummary
from short_drama.h3_models import H3ProductionRoutePlan
from short_drama.image_models import ImageGenerationTask
from short_drama.models import (
    GenerationTask,
    PlanningRunRecord,
    PlanningRunStep,
    ProductionDocument,
    ProductionDocumentVersion,
    ProjectDocument,
    Take,
)
from short_drama.production_contracts import (
    GENERATION_PLAN_DOCUMENT_KIND,
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    PRODUCTION_SPEC_DOCUMENT_KIND,
    PRODUCTION_SPEC_OUTPUT_SLOT_ID,
    STORYBOARD_DOCUMENT_KIND,
    STORYBOARD_DRAFT_DOCUMENT_KIND,
    STORYBOARD_OUTPUT_SLOT_ID,
    GenerationPlanContent,
    ProductionSpecContent,
    StoryboardContent,
    StoryboardDraftContent,
    StoryboardDraftPhase,
)


class ProjectDocumentStore(Protocol):
    def get_project(self, project_id: str) -> ProjectDocument | None: ...

    def get_planning_run(self, run_id: str) -> PlanningRunRecord | None: ...

    def get_task(self, task_id: str) -> GenerationTask | None: ...

    def get_image_task(self, task_id: str) -> ImageGenerationTask | None: ...

    def get_take(self, take_id: str) -> Take | None: ...


class ArtifactRecordLike(Protocol):
    id: str
    project_id: str

    def model_dump_json(self) -> str: ...


class ProjectArtifactRecordStore(Protocol):
    def list_exports(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_provider_runs(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_route_evidence(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def get_video_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_take_comparison(self, report_id: str) -> ArtifactRecordLike | None: ...

    def list_reference_visual_reranks(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_keyframe_comparisons(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_input_compatibility_reports(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def get_audio_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_episode_review(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_h3_route_plan(self, plan_id: str) -> H3ProductionRoutePlan | None: ...

    def list_h3_route_plans(
        self, project_id: str
    ) -> Sequence[H3ProductionRoutePlan]: ...


def _current_script(document: ProjectDocument):
    return next(
        (
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        ),
        None,
    )


def _current_bible(document: ProjectDocument):
    return next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )


@dataclass(frozen=True)
class _CurrentProductionChain:
    """The valid current Spec -> Storyboard -> GenerationPlan dependency chain."""

    spec_document: ProductionDocument | None = None
    spec_version: ProductionDocumentVersion | None = None
    spec: ProductionSpecContent | None = None
    storyboard_document: ProductionDocument | None = None
    storyboard_version: ProductionDocumentVersion | None = None
    storyboard: StoryboardContent | None = None
    generation_plan_document: ProductionDocument | None = None
    generation_plan_version: ProductionDocumentVersion | None = None
    generation_plan: GenerationPlanContent | None = None


def _bound_current_production_version(
    document: ProjectDocument,
    *,
    slot_id: str,
    document_id: str | None,
    document_kind: str,
    artifact_type: str,
) -> tuple[ProductionDocument, ProductionDocumentVersion] | None:
    """Resolve one managed output only when its slot and document agree exactly."""

    if document_id is None:
        return None
    output_slot = next(
        (item for item in document.output_slots if item.id == slot_id),
        None,
    )
    artifact_ref = None if output_slot is None else output_slot.current_artifact_ref
    if (
        artifact_ref is None
        or artifact_ref.project_id != document.project.id
        or artifact_ref.artifact_type != artifact_type
        or artifact_ref.artifact_id != document_id
        or artifact_ref.version is None
    ):
        return None
    production_document = next(
        (
            item
            for item in document.production_documents
            if item.id == document_id and item.kind == document_kind
        ),
        None,
    )
    if production_document is None:
        return None
    version = next(
        (
            item
            for item in production_document.versions
            if item.id == production_document.current_version_id
        ),
        None,
    )
    if version is None or version.version != artifact_ref.version:
        return None
    return production_document, version


def _current_production_chain(document: ProjectDocument) -> _CurrentProductionChain:
    """Return only outputs that still pin the project's current upstream truth."""

    script = _current_script(document)
    bible = _current_bible(document)
    if script is None or bible is None:
        return _CurrentProductionChain()

    spec_binding = _bound_current_production_version(
        document,
        slot_id=PRODUCTION_SPEC_OUTPUT_SLOT_ID,
        document_id=document.production_spec_document_id,
        document_kind=PRODUCTION_SPEC_DOCUMENT_KIND,
        artifact_type="production_spec",
    )
    if spec_binding is None:
        return _CurrentProductionChain()
    spec_document, spec_version = spec_binding
    try:
        spec = ProductionSpecContent.model_validate(spec_version.payload)
    except ValueError:
        return _CurrentProductionChain()
    story_duration_contract = (
        None if script.story_intent is None else script.story_intent.duration_contract
    )
    if (
        spec.project_id != document.project.id
        or spec.script_version_id != script.id
        or spec.bible_version_id != bible.id
        or (
            story_duration_contract is not None
            and spec.duration_contract != story_duration_contract
        )
    ):
        return _CurrentProductionChain()
    chain = _CurrentProductionChain(
        spec_document=spec_document,
        spec_version=spec_version,
        spec=spec,
    )

    storyboard_binding = _bound_current_production_version(
        document,
        slot_id=STORYBOARD_OUTPUT_SLOT_ID,
        document_id=document.storyboard_document_id,
        document_kind=STORYBOARD_DOCUMENT_KIND,
        artifact_type="storyboard",
    )
    if storyboard_binding is None:
        return chain
    storyboard_document, storyboard_version = storyboard_binding
    try:
        storyboard = StoryboardContent.model_validate(storyboard_version.payload)
    except ValueError:
        return chain
    if (
        storyboard.project_id != document.project.id
        or storyboard.script_version_id != script.id
        or storyboard.bible_version_id != bible.id
        or storyboard.production_spec_document_id != spec_document.id
        or storyboard.production_spec_version_id != spec_version.id
    ):
        return chain
    chain = _CurrentProductionChain(
        spec_document=spec_document,
        spec_version=spec_version,
        spec=spec,
        storyboard_document=storyboard_document,
        storyboard_version=storyboard_version,
        storyboard=storyboard,
    )

    generation_plan_binding = _bound_current_production_version(
        document,
        slot_id=GENERATION_PLAN_OUTPUT_SLOT_ID,
        document_id=document.generation_plan_document_id,
        document_kind=GENERATION_PLAN_DOCUMENT_KIND,
        artifact_type="generation_plan",
    )
    if generation_plan_binding is None:
        return chain
    generation_plan_document, generation_plan_version = generation_plan_binding
    try:
        generation_plan = GenerationPlanContent.model_validate(
            generation_plan_version.payload
        )
    except ValueError:
        return chain
    if (
        generation_plan.project_id != document.project.id
        or generation_plan.script_version_id != script.id
        or generation_plan.bible_version_id != bible.id
        or generation_plan.production_spec_document_id != spec_document.id
        or generation_plan.production_spec_version_id != spec_version.id
        or generation_plan.storyboard_document_id != storyboard_document.id
        or generation_plan.storyboard_version_id != storyboard_version.id
    ):
        return chain
    return _CurrentProductionChain(
        spec_document=spec_document,
        spec_version=spec_version,
        spec=spec,
        storyboard_document=storyboard_document,
        storyboard_version=storyboard_version,
        storyboard=storyboard,
        generation_plan_document=generation_plan_document,
        generation_plan_version=generation_plan_version,
        generation_plan=generation_plan,
    )


def _typed_production_version(
    document: ProjectDocument,
    *,
    document_id: str,
    kind: str,
    version: int | None,
):
    production_document = next(
        (
            item
            for item in document.production_documents
            if item.id == document_id and item.kind == kind
        ),
        None,
    )
    if production_document is None:
        return None
    return next(
        (
            item
            for item in production_document.versions
            if (
                (version is None and item.id == production_document.current_version_id)
                or (version is not None and item.version == version)
            )
        ),
        None,
    )


def _storyboard_versions(document: ProjectDocument):
    for production_document in document.production_documents:
        if production_document.kind != STORYBOARD_DOCUMENT_KIND:
            continue
        for version in production_document.versions:
            try:
                storyboard = StoryboardContent.model_validate(version.payload)
            except ValueError:
                continue
            yield production_document, version, storyboard


def _generation_plan_versions(document: ProjectDocument):
    for production_document in document.production_documents:
        if production_document.kind != GENERATION_PLAN_DOCUMENT_KIND:
            continue
        for version in production_document.versions:
            try:
                generation_plan = GenerationPlanContent.model_validate(version.payload)
            except ValueError:
                continue
            yield production_document, version, generation_plan


def _production_spec_summary(
    production_document_id: str,
    version_id: str,
    version_number: int,
    spec: ProductionSpecContent,
) -> str:
    """Keep the pinned duration contract ahead of verbose production direction."""

    payload = spec.model_dump(mode="json")
    priority_keys = (
        "project_id",
        "script_version_id",
        "bible_version_id",
        "duration_contract",
        "target_duration_seconds",
        "duration_intent",
    )
    priority = {key: payload.pop(key) for key in priority_keys}
    return json.dumps(
        {
            "production_spec_document_id": production_document_id,
            "production_spec_version_id": version_id,
            "production_spec_version": version_number,
            **priority,
            **payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _storyboard_draft_summary(
    production_document_id: str,
    version_id: str,
    version_number: int,
    draft: StoryboardDraftContent,
) -> str:
    """Return one phase-aware resume index without replaying authored shot prose."""

    nodes_by_id = {item.id: item for item in draft.scene_nodes}
    ordered_ledger = sorted(draft.timing_ledger, key=lambda item: item.sequence)
    ordered_plans = sorted(
        draft.planned_clip_groups,
        key=lambda item: (
            nodes_by_id[item.scene_node_id].sequence,
            item.scene_group_sequence,
        ),
    )
    ordered_groups = sorted(
        draft.committed_groups,
        key=lambda item: (
            nodes_by_id[item.scene_node_id].sequence,
            item.scene_group_sequence,
        ),
    )
    committed_ids = [item.clip_group.id for item in ordered_groups]
    committed_id_set = set(committed_ids)
    next_uncommitted = next(
        (item for item in ordered_plans if item.id not in committed_id_set),
        None,
    )
    latest_committed = ordered_groups[-1] if ordered_groups else None
    verification = draft.verification
    ledger_line_ids = [
        line_id
        for item in ordered_ledger
        for line_id in item.source_range.script_line_ids
    ]
    outlined_timing_ids = [
        timing_id for item in ordered_plans for timing_id in item.timing_entry_ids
    ]
    outlined_line_ids = [
        line_id
        for item in ordered_plans
        for line_id in item.source_range.script_line_ids
    ]
    next_script_line_id = (
        draft.script_line_ids[len(ledger_line_ids)]
        if len(ledger_line_ids) < len(draft.script_line_ids)
        else None
    )
    next_scene_node_id = next(
        (
            node.id
            for node in draft.scene_nodes
            if next_script_line_id in node.source_line_ids
        ),
        None,
    )
    next_outline_entry = (
        ordered_ledger[len(outlined_timing_ids)]
        if (
            draft.phase.value == "OUTLINE_BUILDING"
            and len(outlined_timing_ids) < len(ordered_ledger)
        )
        else None
    )
    latest_planned_continuity_out = (
        list(ordered_plans[-1].continuity_out)
        if ordered_plans
        else (
            list(ordered_ledger[-1].continuity_out)
            if ordered_ledger
            else []
        )
    )

    def bounded_text(value: str | None, *, limit: int = 600) -> str | None:
        if value is None or len(value) <= limit:
            return value
        return f"{value[: limit - 1].rstrip()}…"

    timing_audit = draft.timing_audit
    timing_audit_summary: dict[str, object] | None = None
    if timing_audit is not None:
        assessment = timing_audit.assessment
        timing_audit_summary = {
            "source_declared_duration_s": timing_audit.source_declared_duration_s,
            "target_duration_s": timing_audit.target_duration_s,
            "dialogue_units_total": timing_audit.dialogue_units_total,
            "minimum_spoken_duration_s": timing_audit.minimum_spoken_duration_s,
            "non_speech_reserve_s": timing_audit.non_speech_reserve_s,
            "computed_episode_duration_s": (
                timing_audit.computed_episode_duration_s
            ),
            "clip_group_count": timing_audit.clip_group_count,
            "alignment": timing_audit.alignment.value,
            "difference_seconds": timing_audit.difference_seconds,
            "assessment": (
                None
                if assessment is None
                else {
                    "estimated_range_seconds": (
                        None
                        if assessment.estimated_range_seconds is None
                        else assessment.estimated_range_seconds.model_dump(
                            mode="json"
                        )
                    ),
                    "alignment": assessment.alignment.value,
                    "pressure_points": [
                        bounded_text(item, limit=200)
                        for item in assessment.pressure_points[:3]
                    ],
                    "concurrent_performance": [
                        bounded_text(item, limit=200)
                        for item in assessment.concurrent_performance[:3]
                    ],
                    "resolution": assessment.resolution.value,
                    "rationale": bounded_text(assessment.rationale, limit=500),
                }
            ),
            "overloaded_shot_ids": timing_audit.overloaded_shot_ids,
            "overloaded_group_ids": timing_audit.overloaded_group_ids,
            "timing_audit_passed": timing_audit.timing_audit_passed,
            "notes": [
                bounded_text(item, limit=240) for item in timing_audit.notes[:4]
            ],
        }

    # The outline phase needs the complete sealed ledger for global repacking.
    # While the ledger itself is still growing, only the exact tail and cursor
    # are replayed; the current ScriptVersion remains separately available.
    ledger_for_context = (
        ordered_ledger
        if draft.phase.value != "LEDGER_BUILDING"
        else ordered_ledger[-1:]
    )

    return json.dumps(
        {
            "draft_document_id": production_document_id,
            "draft_version_id": version_id,
            "draft_version": version_number,
            "phase": draft.phase.value,
            "title": draft.title,
            "script_version_id": draft.script_version_id,
            "bible_version_id": draft.bible_version_id,
            "production_spec_document_id": draft.production_spec_document_id,
            "production_spec_version_id": draft.production_spec_version_id,
            "base_storyboard_version_id": draft.base_storyboard_version_id,
            # Duration reconciliation is a Leader decision, so its exact pinned
            # inputs and mismatch evidence must survive ContextPack truncation.
            "timing_audit": timing_audit_summary,
            "scene_nodes": [
                {
                    "id": node.id,
                    "scene_id": node.scene_id,
                    "sequence": node.sequence,
                    "heading": node.heading,
                }
                for node in draft.scene_nodes
            ],
            "timing_ledger": [
                {
                    "id": item.id,
                    "sequence": item.sequence,
                    "scene_node_id": item.scene_node_id,
                    "source_range": item.source_range.model_dump(mode="json"),
                    "source_text": bounded_text(item.source_text, limit=500),
                    "dialogue_units": item.dialogue_units,
                    "speech_rate_units_per_second": (
                        item.speech_rate_units_per_second
                    ),
                    "minimum_spoken_duration_seconds": (
                        item.minimum_spoken_duration_seconds
                    ),
                    "non_speech_reserve_seconds": (
                        item.non_speech_reserve_seconds
                    ),
                    "allocated_duration_seconds": (
                        item.allocated_duration_seconds
                    ),
                    "natural_cut": bounded_text(item.natural_cut, limit=500),
                    "continuity_in": [
                        bounded_text(value, limit=300)
                        for value in item.continuity_in
                    ],
                    "visible_changes": [
                        bounded_text(value, limit=300)
                        for value in item.visible_changes
                    ],
                    "continuity_out": [
                        bounded_text(value, limit=300)
                        for value in item.continuity_out
                    ],
                }
                for item in ledger_for_context
            ],
            "planned_clip_groups": [
                {
                    "clip_group_id": item.id,
                    "title": item.title,
                    "scene_node_id": item.scene_node_id,
                    "scene_group_sequence": item.scene_group_sequence,
                    "source_range_id": item.source_range.id,
                    "source_scene_ids": item.source_range.scene_ids,
                    "source_line_ids": item.source_range.script_line_ids,
                    "timing_entry_ids": item.timing_entry_ids,
                    "duration_seconds": item.duration_seconds,
                }
                for item in ordered_plans
            ],
            "next_unplanned_source": {
                "scene_node_id": next_scene_node_id,
                "script_line_id": next_script_line_id,
                "timing_entry_id": (
                    None if next_outline_entry is None else next_outline_entry.id
                ),
            },
            "latest_planned_continuity_out": [
                bounded_text(item, limit=400)
                for item in latest_planned_continuity_out
            ],
            "committed_clip_group_ids": committed_ids,
            "next_uncommitted_group": (
                None
                if next_uncommitted is None
                else {
                    "clip_group_id": next_uncommitted.id,
                    "title": next_uncommitted.title,
                    "scene_node_id": next_uncommitted.scene_node_id,
                    "scene_group_sequence": next_uncommitted.scene_group_sequence,
                    "source_range": {
                        "id": next_uncommitted.source_range.id,
                        "scene_ids": next_uncommitted.source_range.scene_ids,
                        "script_line_ids": (
                            next_uncommitted.source_range.script_line_ids
                        ),
                        "description": bounded_text(
                            next_uncommitted.source_range.description
                        ),
                        "starts_after": bounded_text(
                            next_uncommitted.source_range.starts_after
                        ),
                        "ends_before": bounded_text(
                            next_uncommitted.source_range.ends_before
                        ),
                    },
                    "duration_seconds": next_uncommitted.duration_seconds,
                    "objective": bounded_text(next_uncommitted.objective),
                    "continuity_in": [
                        bounded_text(item, limit=300)
                        for item in next_uncommitted.continuity_in
                    ],
                    "visible_changes": [
                        bounded_text(item, limit=300)
                        for item in next_uncommitted.visible_changes
                    ],
                    "continuity_out": [
                        bounded_text(item, limit=300)
                        for item in next_uncommitted.continuity_out
                    ],
                }
            ),
            "latest_committed_group": (
                None
                if latest_committed is None
                else {
                    "clip_group_id": latest_committed.clip_group.id,
                    "scene_node_id": latest_committed.scene_node_id,
                    "scene_group_sequence": latest_committed.scene_group_sequence,
                    "source_line_ids": (
                        latest_committed.source_range.script_line_ids
                    ),
                }
            ),
            "latest_continuity_out": (
                []
                if latest_committed is None
                else [
                    bounded_text(item, limit=400)
                    for item in latest_committed.clip_group.continuity_out
                ]
            ),
            "verification": (
                None
                if verification is None
                else {
                    "status": verification.status,
                    "summary": bounded_text(verification.summary),
                    "checked_clip_group_ids": verification.checked_clip_group_ids,
                    "finding_count": len(verification.findings),
                    "findings": [
                        {
                            "id": finding.id,
                            "summary": bounded_text(finding.summary),
                            "repair_scope": finding.repair_scope.value,
                            "affected_clip_group_ids": (
                                finding.affected_clip_group_ids
                            ),
                            "affected_scene_sequences": (
                                finding.affected_scene_sequences
                            ),
                            "suggested_revision": bounded_text(
                                finding.suggested_revision
                            ),
                        }
                        for finding in verification.findings[:16]
                    ],
                }
            ),
            "progress": {
                "phase": draft.phase.value,
                "scene_count": len(draft.scene_nodes),
                "timing_ledger_entry_count": len(ordered_ledger),
                "ledger_covered_script_line_count": len(ledger_line_ids),
                "outlined_timing_entry_count": len(outlined_timing_ids),
                "outlined_script_line_count": len(outlined_line_ids),
                "planned_clip_group_count": len(ordered_plans),
                "committed_clip_group_count": len(ordered_groups),
                "committed_shot_count": sum(
                    len(item.shots) for item in ordered_groups
                ),
                "covered_script_line_count": sum(
                    len(item.source_range.script_line_ids)
                    for item in ordered_groups
                ),
                "total_script_line_count": len(draft.script_line_ids),
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class ProjectArtifactResolver:
    """Resolve focused summaries from the canonical ProjectDocument."""

    def __init__(self, projects: ProjectDocumentStore) -> None:
        self._projects = projects

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        document = self._projects.get_project(ref.project_id)
        if document is None:
            return None
        if ref.artifact_type == "project":
            if ref.artifact_id != document.project.id:
                return None
            return ArtifactSummary(
                ref=ref,
                title=document.project.name,
                summary=project_summary(document),
            )
        if ref.artifact_type in {"content_planning_job", "planning_run"}:
            planning_run = self._projects.get_planning_run(ref.artifact_id)
            if (
                planning_run is None
                or planning_run.id != ref.artifact_id
                or planning_run.project_id != ref.project_id
            ):
                return None
            step_title = planning_run.current_step.value
            if (
                planning_run.current_step == PlanningRunStep.SCENE_WRITER
                and planning_run.total_scenes
            ):
                current = min(
                    planning_run.next_script_scene_sequence,
                    planning_run.total_scenes,
                )
                step_title += f" {current}/{planning_run.total_scenes}"
            elif (
                planning_run.current_step
                == PlanningRunStep.STORY_METADATA_REVISION
            ):
                if planning_run.revision_repair_round:
                    step_title += (
                        " verifier-repair "
                        f"round {planning_run.revision_repair_round}"
                    )
                else:
                    step_title += " initial"
            elif (
                planning_run.current_step == PlanningRunStep.SCENE_REVISION
                and planning_run.revision_scene_sequences
            ):
                total = len(planning_run.revision_scene_sequences)
                current = min(
                    planning_run.next_revision_scene_index + 1,
                    total,
                )
                step_title += f" {current}/{total}"
            elif (
                planning_run.current_step == PlanningRunStep.SCENE_DIRECTOR
                and planning_run.total_scenes
            ):
                current = min(
                    planning_run.next_scene_sequence,
                    planning_run.total_scenes,
                )
                step_title += f" {current}/{planning_run.total_scenes}"
            return ArtifactSummary(
                ref=ref,
                title=f"Content planning job {step_title}",
                summary=(
                    "Execution cursor only; canonical script, Bible, shots, and assets "
                    "remain in ProjectDocument. "
                    + planning_run.model_dump_json()
                ),
            )
        if ref.artifact_type in {"script", "script_version"}:
            script = next(
                (
                    item
                    for item in document.script_versions
                    if item.id == ref.artifact_id
                    and (ref.version is None or item.version == ref.version)
                ),
                None,
            )
            if script is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Script v{script.version}",
                # Storyboard planning needs the exact visible actions as well as
                # dialogue. Keep only the selected immutable version, but retain
                # both episode- and scene-level screenplay text verbatim.
                summary=json.dumps(
                    {
                        "id": script.id,
                        "version": script.version,
                        "source_mode": script.source_mode,
                        "original_text": script.original_text,
                        "screenplay_text": script.screenplay_text,
                        "spoken_text": script.spoken_text,
                        "subtitle_text": script.subtitle_text,
                        "story_intent": (
                            None
                            if script.story_intent is None
                            else script.story_intent.model_dump(mode="json")
                        ),
                        "ending_contract": (
                            None
                            if script.ending_contract is None
                            else script.ending_contract.model_dump(mode="json")
                        ),
                        "source_contract": (
                            None
                            if script.source_contract is None
                            else script.source_contract.model_dump(mode="json")
                        ),
                        "lines": [
                            item.model_dump(mode="json") for item in script.lines
                        ],
                        "structured_scenes": [
                            item.model_dump(mode="json")
                            for item in script.structured_scenes
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type in {"bible", "bible_version"}:
            bible = next(
                (
                    item
                    for item in document.bible_versions
                    if item.id == ref.artifact_id
                    and (ref.version is None or item.version == ref.version)
                ),
                None,
            )
            if bible is None:
                return None
            summary = {
                "characters": [item.model_dump(mode="json") for item in bible.characters],
                "locations": [item.model_dump(mode="json") for item in bible.locations],
                "props": [item.model_dump(mode="json") for item in bible.props],
                "style": bible.style.model_dump(mode="json"),
            }
            return ArtifactSummary(
                ref=ref,
                title=f"Project Bible v{bible.version}",
                summary=json.dumps(summary, ensure_ascii=False),
            )
        if ref.artifact_type == "scene":
            script = (
                _current_script(document)
                if ref.version is None
                else next(
                    (
                        item
                        for item in document.script_versions
                        if item.version == ref.version
                        and any(
                            scene.id == ref.artifact_id
                            for scene in item.structured_scenes
                        )
                    ),
                    None,
                )
            )
            if script is None:
                return None
            scene = next(
                (item for item in script.structured_scenes if item.id == ref.artifact_id),
                None,
            )
            if scene is None:
                return None
            line_by_id = {line.id: line for line in script.lines}
            lines = [
                line_by_id[line_id].model_dump(mode="json")
                for line_id in scene.line_ids
                if line_id in line_by_id
            ]
            return ArtifactSummary(
                ref=ref,
                title=scene.heading,
                summary=json.dumps(
                    {**scene.model_dump(mode="json"), "lines": lines},
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "shot":
            shot = next((item for item in document.shots if item.id == ref.artifact_id), None)
            if shot is not None and (
                ref.version is None or shot.revision == ref.version
            ):
                return ArtifactSummary(
                    ref=ref,
                    title=f"Shot {shot.sequence}",
                    summary=shot.model_dump_json(),
                )
            if ref.version is not None:
                historical = next(
                    (
                        (version, item)
                        for _, version, storyboard in _storyboard_versions(document)
                        for item in storyboard.shots
                        if item.id == ref.artifact_id and item.revision == ref.version
                    ),
                    None,
                )
                if historical is not None:
                    storyboard_version, historical_shot = historical
                    return ArtifactSummary(
                        ref=ref,
                        title=(
                            f"Shot {historical_shot.sequence} revision "
                            f"{historical_shot.revision}"
                        ),
                        summary=json.dumps(
                            {
                                "storyboard_version_id": storyboard_version.id,
                                "storyboard_version": storyboard_version.version,
                                "shot": historical_shot.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        ),
                    )
            return None
        if ref.artifact_type == "character":
            bible = (
                _current_bible(document)
                if ref.version is None
                else next(
                    (
                        item
                        for item in document.bible_versions
                        if item.version == ref.version
                    ),
                    None,
                )
            )
            character = None if bible is None else next(
                (item for item in bible.characters if item.id == ref.artifact_id),
                None,
            )
            if character is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=character.name,
                summary=character.model_dump_json(),
            )
        if ref.artifact_type in {"asset", "asset_version"}:
            asset = next(
                (
                    item
                    for item in document.assets
                    if (
                        ref.artifact_type == "asset"
                        and item.id == ref.artifact_id
                    )
                    or (
                        ref.artifact_type == "asset_version"
                        and any(
                            version.id == ref.artifact_id
                            and (
                                ref.version is None
                                or version.version == ref.version
                            )
                            for version in item.versions
                        )
                    )
                ),
                None,
            )
            if asset is None:
                return None
            if ref.artifact_type == "asset" and ref.version is not None:
                current = next(
                    (
                        version
                        for version in asset.versions
                        if version.id == asset.current_version_id
                    ),
                    None,
                )
                if current is None or current.version != ref.version:
                    return None
            if ref.artifact_type == "asset_version":
                version = next(
                    (
                        item
                        for item in asset.versions
                        if item.id == ref.artifact_id
                    ),
                    None,
                )
                if version is None:
                    return None
                return ArtifactSummary(
                    ref=ref,
                    title=f"{asset.name} v{version.version}",
                    summary=json.dumps(
                        {
                            "asset_id": asset.id,
                            "kind": asset.kind.value,
                            "name": asset.name,
                            "brief": (
                                None
                                if asset.brief is None
                                else asset.brief.model_dump(mode="json")
                            ),
                            "version": version.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                )
            return ArtifactSummary(
                ref=ref,
                title=asset.name,
                summary=asset.model_dump_json(),
            )
        if ref.artifact_type in {"reference_pack", "reference_pack_entry"}:
            pack = next(
                (
                    item
                    for item in document.reference_packs
                    if item.id == ref.artifact_id
                    or any(entry.id == ref.artifact_id for entry in item.entries)
                ),
                None,
            )
            if pack is None:
                return None
            if ref.artifact_type == "reference_pack_entry":
                entry = next(
                    (item for item in pack.entries if item.id == ref.artifact_id),
                    None,
                )
                if entry is None:
                    return None
                return ArtifactSummary(
                    ref=ref,
                    title=f"Reference pack entry {entry.id}",
                    summary=entry.model_dump_json(),
                )
            return ArtifactSummary(
                ref=ref,
                title=pack.name,
                summary=pack.model_dump_json(),
            )
        if ref.artifact_type == "storyboard_draft":
            version = _typed_production_version(
                document,
                document_id=ref.artifact_id,
                kind=STORYBOARD_DRAFT_DOCUMENT_KIND,
                version=ref.version,
            )
            if version is None:
                return None
            try:
                draft = StoryboardDraftContent.model_validate(version.payload)
            except ValueError:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Storyboard draft v{version.version}",
                summary=_storyboard_draft_summary(
                    ref.artifact_id,
                    version.id,
                    version.version,
                    draft,
                ),
            )
        if ref.artifact_type in {
            "production_document",
            "production_document_version",
        }:
            production_document = next(
                (
                    item
                    for item in document.production_documents
                    if (
                        ref.artifact_type == "production_document"
                        and item.id == ref.artifact_id
                    )
                    or (
                        ref.artifact_type == "production_document_version"
                        and any(
                            version.id == ref.artifact_id
                            for version in item.versions
                        )
                    )
                ),
                None,
            )
            if production_document is None:
                return None
            version = next(
                (
                    item
                    for item in production_document.versions
                    if (
                        (
                            ref.artifact_type == "production_document_version"
                            and item.id == ref.artifact_id
                        )
                        or (
                            ref.artifact_type == "production_document"
                            and ref.version is None
                            and item.id == production_document.current_version_id
                        )
                        or (
                            ref.artifact_type == "production_document"
                            and ref.version is not None
                            and item.version == ref.version
                        )
                    )
                    and (ref.version is None or item.version == ref.version)
                ),
                None,
            )
            if version is None:
                return None
            if production_document.kind == STORYBOARD_DRAFT_DOCUMENT_KIND:
                try:
                    draft = StoryboardDraftContent.model_validate(version.payload)
                except ValueError:
                    return None
                return ArtifactSummary(
                    ref=ref,
                    title=f"{production_document.title} v{version.version}",
                    summary=_storyboard_draft_summary(
                        production_document.id,
                        version.id,
                        version.version,
                        draft,
                    ),
                )
            return ArtifactSummary(
                ref=ref,
                title=f"{production_document.title} v{version.version}",
                summary=json.dumps(
                    {
                        "document_id": production_document.id,
                        "project_id": production_document.project_id,
                        "kind": production_document.kind,
                        "title": production_document.title,
                        "current_version_id": production_document.current_version_id,
                        "version": version.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "production_spec":
            version = _typed_production_version(
                document,
                document_id=ref.artifact_id,
                kind=PRODUCTION_SPEC_DOCUMENT_KIND,
                version=ref.version,
            )
            if version is None:
                return None
            try:
                spec = ProductionSpecContent.model_validate(version.payload)
            except ValueError:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"ProductionSpec v{version.version}",
                summary=_production_spec_summary(
                    ref.artifact_id,
                    version.id,
                    version.version,
                    spec,
                ),
            )
        if ref.artifact_type == "storyboard":
            version = _typed_production_version(
                document,
                document_id=ref.artifact_id,
                kind=STORYBOARD_DOCUMENT_KIND,
                version=ref.version,
            )
            if version is None:
                return None
            try:
                storyboard = StoryboardContent.model_validate(version.payload)
            except ValueError:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Storyboard v{version.version}",
                summary=storyboard.model_dump_json(),
            )
        if ref.artifact_type == "clip_group":
            match = next(
                (
                    (version, group)
                    for production_document, version, storyboard in _storyboard_versions(document)
                    if (
                        (
                            ref.version is None
                            and version.id == production_document.current_version_id
                        )
                        or (
                            ref.version is not None
                            and version.version == ref.version
                        )
                    )
                    for group in storyboard.clip_groups
                    if group.id == ref.artifact_id
                ),
                None,
            )
            if match is None:
                return None
            version, group = match
            return ArtifactSummary(
                ref=ref,
                title=group.title,
                summary=json.dumps(
                    {
                        "storyboard_version_id": version.id,
                        "storyboard_version": version.version,
                        "clip_group": group.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "generation_plan":
            version = _typed_production_version(
                document,
                document_id=ref.artifact_id,
                kind=GENERATION_PLAN_DOCUMENT_KIND,
                version=ref.version,
            )
            if version is None:
                return None
            try:
                generation_plan = GenerationPlanContent.model_validate(version.payload)
            except ValueError:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"GenerationPlan v{version.version}",
                summary=generation_plan.model_dump_json(),
            )
        if ref.artifact_type == "generation_unit":
            match = next(
                (
                    (version, unit)
                    for production_document, version, generation_plan in _generation_plan_versions(document)
                    if (
                        (
                            ref.version is None
                            and version.id == production_document.current_version_id
                        )
                        or (
                            ref.version is not None
                            and version.version == ref.version
                        )
                    )
                    for unit in generation_plan.units
                    if unit.id == ref.artifact_id
                ),
                None,
            )
            if match is None:
                return None
            version, unit = match
            return ArtifactSummary(
                ref=ref,
                title=f"GenerationUnit {unit.id}",
                summary=json.dumps(
                    {
                        "generation_plan_version_id": version.id,
                        "generation_plan_version": version.version,
                        "generation_unit": unit.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "output_slot":
            slot = next(
                (
                    item
                    for item in document.output_slots
                    if item.id == ref.artifact_id
                ),
                None,
            )
            if slot is None:
                return None
            if ref.version is None or ref.version == slot.revision:
                resolved_revision = slot.revision
                summary = slot.model_dump_json()
            else:
                snapshot = next(
                    (
                        item
                        for item in slot.revision_history
                        if item.revision == ref.version
                    ),
                    None,
                )
                if snapshot is None:
                    return None
                resolved_revision = snapshot.revision
                summary = json.dumps(
                    {
                        "id": slot.id,
                        **snapshot.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
            return ArtifactSummary(
                ref=ref,
                title=f"Output slot {slot.id} r{resolved_revision}",
                summary=summary,
            )
        if ref.artifact_type in {"timeline", "timeline_manifest"}:
            manifest = next(
                (
                    item
                    for item in document.manifests
                    if item.id == ref.artifact_id
                    and (ref.version is None or item.version == ref.version)
                ),
                None,
            )
            if manifest is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Timeline v{manifest.version}",
                summary=manifest.model_dump_json(),
            )
        if ref.artifact_type == "generation_task":
            return _artifact_record_summary(ref, self._projects.get_task(ref.artifact_id))
        if ref.artifact_type == "image_task":
            return _artifact_record_summary(
                ref,
                self._projects.get_image_task(ref.artifact_id),
            )
        if ref.artifact_type == "take":
            return _artifact_record_summary(ref, self._projects.get_take(ref.artifact_id))

        records = cast(ProjectArtifactRecordStore, self._projects)
        if ref.artifact_type == "h3_route_plan":
            plan = records.get_h3_route_plan(ref.artifact_id)
            if (
                plan is None
                or plan.project_id != ref.project_id
                or (
                    ref.version is not None
                    and plan.state_revision != ref.version
                )
            ):
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"H3 route plan v{plan.version} state {plan.state_revision}",
                summary=plan.model_dump_json(),
            )
        if ref.artifact_type == "h3_route_asset_preparation":
            matches = [
                (plan, preparation)
                for plan in records.list_h3_route_plans(ref.project_id)
                for decision in plan.decisions
                for preparation in decision.asset_preparations
                if preparation.id == ref.artifact_id
            ]
            if len(matches) != 1:
                return None
            plan, preparation = matches[0]
            if ref.version is not None and plan.state_revision != ref.version:
                return None
            return ArtifactSummary(
                ref=ref,
                title=preparation.name,
                summary=json.dumps(
                    {
                        "parent_plan_id": plan.id,
                        "parent_plan_version": plan.version,
                        "parent_plan_state_revision": plan.state_revision,
                        "preparation": preparation.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "video_inspection":
            return _artifact_record_summary(
                ref,
                records.get_video_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "take_comparison":
            return _artifact_record_summary(
                ref,
                records.get_take_comparison(ref.artifact_id),
            )
        if ref.artifact_type == "audio_inspection":
            return _artifact_record_summary(
                ref,
                records.get_audio_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "episode_review":
            return _artifact_record_summary(
                ref,
                records.get_episode_review(ref.artifact_id),
            )

        collection_loaders: dict[
            str,
            Callable[[str], Sequence[ArtifactRecordLike]],
        ] = {
            "export": records.list_exports,
            "h3_provider_run": records.list_provider_runs,
            "route_evidence": records.list_route_evidence,
            "reference_visual_rerank": records.list_reference_visual_reranks,
            "keyframe_comparison": records.list_keyframe_comparisons,
            "input_compatibility": records.list_input_compatibility_reports,
        }
        loader = collection_loaders.get(ref.artifact_type)
        if loader is not None:
            record = next(
                (item for item in loader(ref.project_id) if item.id == ref.artifact_id),
                None,
            )
            return _artifact_record_summary(ref, record)
        return None


def _artifact_record_summary(
    ref: ArtifactRef,
    record: ArtifactRecordLike | None,
) -> ArtifactSummary | None:
    if (
        record is None
        or record.id != ref.artifact_id
        or record.project_id != ref.project_id
    ):
        return None
    return ArtifactSummary(
        ref=ref,
        title=ref.artifact_type.replace("_", " ").title(),
        summary=record.model_dump_json(),
    )


def project_artifact_refs(document: ProjectDocument) -> list[ArtifactRef]:
    refs = [
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id=document.project.id,
        )
    ]
    script = _current_script(document)
    if script is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="script_version",
                artifact_id=script.id,
                version=script.version,
            )
        )
    bible = _current_bible(document)
    if bible is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="bible_version",
                artifact_id=bible.id,
                version=bible.version,
            )
        )
    current_chain = _current_production_chain(document)
    managed_current_versions: dict[str, tuple[str, str]] = {}
    if (
        current_chain.spec_document is not None
        and current_chain.spec_version is not None
    ):
        managed_current_versions[PRODUCTION_SPEC_DOCUMENT_KIND] = (
            current_chain.spec_document.id,
            current_chain.spec_version.id,
        )
    if (
        current_chain.storyboard_document is not None
        and current_chain.storyboard_version is not None
    ):
        managed_current_versions[STORYBOARD_DOCUMENT_KIND] = (
            current_chain.storyboard_document.id,
            current_chain.storyboard_version.id,
        )
    if (
        current_chain.generation_plan_document is not None
        and current_chain.generation_plan_version is not None
    ):
        managed_current_versions[GENERATION_PLAN_DOCUMENT_KIND] = (
            current_chain.generation_plan_document.id,
            current_chain.generation_plan_version.id,
        )
    current_production_document_ids = [
        document_id
        for document_id in (
            None
            if current_chain.spec_document is None
            else current_chain.spec_document.id,
            None
            if current_chain.storyboard_document is None
            else current_chain.storyboard_document.id,
            None
            if current_chain.generation_plan_document is None
            else current_chain.generation_plan_document.id,
            document.master_audio_document_id,
            document.audio_structure_document_id,
            document.timed_text_document_id,
        )
        if document_id is not None
    ]
    current_document_id_set = set(current_production_document_ids)
    production_documents_by_id = {
        item.id: item for item in document.production_documents
    }
    ordered_production_documents = [
        production_documents_by_id[document_id]
        for document_id in current_production_document_ids
        if document_id in production_documents_by_id
    ]
    ordered_production_documents.extend(
        item
        for item in document.production_documents
        if item.id not in current_document_id_set
    )
    current_typed_refs: list[ArtifactRef] = []
    current_generic_refs: list[ArtifactRef] = []
    other_production_refs: list[ArtifactRef] = []
    duration_reconciliation_candidates: list[tuple[str, list[ArtifactRef]]] = []
    production_child_refs: list[ArtifactRef] = []

    def remove_default_refs(items: Sequence[ArtifactRef]) -> None:
        identities = {item.identity() for item in items}
        for bucket in (
            current_typed_refs,
            current_generic_refs,
            other_production_refs,
        ):
            bucket[:] = [
                item for item in bucket if item.identity() not in identities
            ]

    for production_document in ordered_production_documents:
        current = next(
            (
                item
                for item in production_document.versions
                if item.id == production_document.current_version_id
            ),
            None,
        )
        if current is None:
            continue
        expected_managed_version = managed_current_versions.get(
            production_document.kind
        )
        if production_document.kind in {
            PRODUCTION_SPEC_DOCUMENT_KIND,
            STORYBOARD_DOCUMENT_KIND,
            GENERATION_PLAN_DOCUMENT_KIND,
        } and expected_managed_version != (
            production_document.id,
            current.id,
        ):
            # Managed historical outputs remain resolvable by exact requested
            # refs, but never enter the project's implicit current context.
            continue
        generic_refs = (
            current_generic_refs
            if production_document.id in current_document_id_set
            else other_production_refs
        )
        typed_refs = (
            current_typed_refs
            if production_document.id in current_document_id_set
            else other_production_refs
        )
        default_document_refs = [
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="production_document",
                artifact_id=production_document.id,
                version=current.version,
            ),
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="production_document_version",
                artifact_id=current.id,
                version=current.version,
            ),
        ]
        generic_refs.extend(default_document_refs)
        if production_document.kind == PRODUCTION_SPEC_DOCUMENT_KIND:
            typed_refs.append(
                ArtifactRef(
                    project_id=document.project.id,
                    artifact_type="production_spec",
                    artifact_id=production_document.id,
                    version=current.version,
                )
            )
        elif production_document.kind == STORYBOARD_DRAFT_DOCUMENT_KIND:
            try:
                draft = StoryboardDraftContent.model_validate(current.payload)
            except ValueError:
                continue
            draft_ref = ArtifactRef(
                project_id=document.project.id,
                artifact_type="storyboard_draft",
                artifact_id=production_document.id,
                version=current.version,
            )
            typed_refs.append(draft_ref)
            if draft.phase == StoryboardDraftPhase.DURATION_RECONCILIATION:
                # Project-level context describes active truth, not every
                # historical reconciliation branch. Historical exact refs are
                # still resolvable when a WorkOrder explicitly requests them.
                if (
                    script is None
                    or bible is None
                    or draft.script_version_id != script.id
                    or draft.bible_version_id != bible.id
                    or current_chain.spec_document is None
                    or current_chain.spec_version is None
                    or current_chain.spec is None
                    or current_chain.spec_document.id
                    != draft.production_spec_document_id
                    or current_chain.spec_version.id
                    != draft.production_spec_version_id
                ):
                    remove_default_refs([*default_document_refs, draft_ref])
                    continue
                reconciliation_refs: list[ArtifactRef] = []
                reconciliation_refs.append(
                    ArtifactRef(
                        project_id=document.project.id,
                        artifact_type="script_version",
                        artifact_id=script.id,
                        version=script.version,
                    )
                )
                pinned_spec_version = current_chain.spec_version
                pinned_spec = current_chain.spec
                story_duration_contract = (
                    None
                    if script.story_intent is None
                    else script.story_intent.duration_contract
                )
                if (
                    pinned_spec.script_version_id != script.id
                    or pinned_spec.bible_version_id != bible.id
                    or (
                        story_duration_contract is not None
                        and pinned_spec.duration_contract
                        != story_duration_contract
                    )
                ):
                    remove_default_refs([*default_document_refs, draft_ref])
                    continue
                reconciliation_refs.append(
                    ArtifactRef(
                        project_id=document.project.id,
                        artifact_type="production_spec",
                        artifact_id=draft.production_spec_document_id,
                        version=pinned_spec_version.version,
                    )
                )
                reconciliation_refs.append(draft_ref)
                duration_reconciliation_candidates.append(
                    (current.created_at, reconciliation_refs)
                )
        elif production_document.kind == STORYBOARD_DOCUMENT_KIND:
            try:
                storyboard = StoryboardContent.model_validate(current.payload)
            except ValueError:
                continue
            typed_refs.append(
                ArtifactRef(
                    project_id=document.project.id,
                    artifact_type="storyboard",
                    artifact_id=production_document.id,
                    version=current.version,
                )
            )
            production_child_refs.extend(
                ArtifactRef(
                    project_id=document.project.id,
                    artifact_type="clip_group",
                    artifact_id=group.id,
                    version=current.version,
                )
                for group in storyboard.clip_groups
            )
        elif production_document.kind == GENERATION_PLAN_DOCUMENT_KIND:
            try:
                generation_plan = GenerationPlanContent.model_validate(current.payload)
            except ValueError:
                continue
            typed_refs.append(
                ArtifactRef(
                    project_id=document.project.id,
                    artifact_type="generation_plan",
                    artifact_id=production_document.id,
                    version=current.version,
                )
            )
            production_child_refs.extend(
                ArtifactRef(
                    project_id=document.project.id,
                    artifact_type="generation_unit",
                    artifact_id=unit.id,
                    version=current.version,
                )
                for unit in generation_plan.units
            )
    # A Leader replan compiles at most 16 artifact summaries. Prioritize only
    # the latest active reconciliation branch and its exact immutable inputs;
    # stale branches never displace the current ScriptVersion.
    duration_reconciliation_refs = (
        max(duration_reconciliation_candidates, key=lambda item: item[0])[1]
        if duration_reconciliation_candidates
        else []
    )
    refs[1:1] = duration_reconciliation_refs
    refs.extend(current_typed_refs)
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="output_slot",
            artifact_id=slot.id,
            version=slot.revision,
        )
        for slot in document.output_slots
    )
    refs.extend(current_generic_refs)
    refs.extend(other_production_refs)
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="scene",
            artifact_id=scene.id,
            version=None if script is None else script.version,
        )
        for scene in ([] if script is None else script.structured_scenes)
    )
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
        for shot in document.shots
    )
    refs.extend(production_child_refs)
    deduplicated: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    for ref in refs:
        deduplicated.setdefault(ref.identity(), ref)
    return list(deduplicated.values())


def project_summary(document: ProjectDocument) -> str:
    script = _current_script(document)
    bible = _current_bible(document)
    current_chain = _current_production_chain(document)
    scene_summaries = [] if script is None else [
        f"{scene.id}:{scene.heading}:{scene.summary}" for scene in script.structured_scenes
    ]
    characters = [] if bible is None else [
        f"{item.id}:{item.name}:{item.performance}" for item in bible.characters
    ]
    ordered_shots = sorted(document.shots, key=lambda item: item.sequence)
    recent_shots = ordered_shots[-5:]
    shot_summaries = [
        f"{shot.id}#{shot.sequence}:{shot.narrative_purpose}:{shot.visual}"
        for shot in recent_shots
    ]
    current_document_summaries: list[str] = []
    production_documents_by_id = {
        item.id: item for item in document.production_documents
    }
    for label, production_document, version in (
        (
            "production_spec",
            current_chain.spec_document,
            current_chain.spec_version,
        ),
        (
            "storyboard",
            current_chain.storyboard_document,
            current_chain.storyboard_version,
        ),
        (
            "generation_plan",
            current_chain.generation_plan_document,
            current_chain.generation_plan_version,
        ),
    ):
        if production_document is None or version is None:
            continue
        current_document_summaries.append(
            f"{label}={production_document.id}@v{version.version}({version.id})"
        )
    for label, document_id in (
        ("master_audio", document.master_audio_document_id),
        ("audio_structure", document.audio_structure_document_id),
        ("timed_text", document.timed_text_document_id),
    ):
        if document_id is None:
            continue
        production_document = production_documents_by_id.get(document_id)
        if production_document is None:
            current_document_summaries.append(f"{label}={document_id}")
            continue
        current = next(
            (
                item
                for item in production_document.versions
                if item.id == production_document.current_version_id
            ),
            None,
        )
        if current is None:
            current_document_summaries.append(f"{label}={document_id}")
            continue
        current_document_summaries.append(
            f"{label}={document_id}@v{current.version}({current.id})"
        )
    output_slot_summaries: list[str] = []
    for slot in document.output_slots:
        artifact_ref = slot.current_artifact_ref
        if artifact_ref is None:
            output_slot_summaries.append(
                f"{slot.id}@r{slot.revision}=invalidated:{slot.invalidation_reason}"
            )
            continue
        version_suffix = (
            "" if artifact_ref.version is None else f"@v{artifact_ref.version}"
        )
        output_slot_summaries.append(
            f"{slot.id}@r{slot.revision}={artifact_ref.artifact_type}:"
            f"{artifact_ref.artifact_id}{version_suffix}"
        )
    planning_summary = "Planning metadata unavailable"
    if document.planning is not None:
        planning = document.planning
        warning_summary = " | ".join(planning.warnings) or "none"
        planning_summary = (
            f"Content planning job {planning.status.value}; "
            f"planner {planning.planner or 'pending'}; "
            f"job {planning.run_id or 'none'}; warnings {warning_summary}"
        )
    return "\n".join(
        [
            f"Project {document.project.name} ({document.project.id})",
            f"Profile {document.project.production_profile.value}; stage {document.project.stage.value}; "
            f"language {document.project.language}; aspect {document.project.aspect_ratio}; "
            f"target {document.project.target_duration_seconds}s",
            "Current production documents: "
            + (" | ".join(current_document_summaries) or "none"),
            "Output slots: " + (" | ".join(output_slot_summaries) or "none"),
            f"Counts: characters {len(characters)}; scenes {len(scene_summaries)}; "
            f"shots {len(document.shots)}; assets {len(document.assets)}; "
            f"manifests {len(document.manifests)}; production documents "
            f"{len(document.production_documents)}; output slots "
            f"{len(document.output_slots)}",
            f"Current script {document.project.current_script_version_id}; "
            f"current bible {document.project.current_bible_version_id}",
            "Original user brief: " + document.project.input_text[:4000],
            "Characters: " + " | ".join(characters),
            "Scenes: " + " | ".join(scene_summaries),
            f"Shot tail (last {len(recent_shots)} of {len(document.shots)}): "
            + " | ".join(shot_summaries),
            planning_summary,
        ]
    )


__all__ = ["ProjectArtifactResolver", "project_artifact_refs", "project_summary"]
