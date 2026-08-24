"""Application service for the provider-neutral short-drama workflow."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from copy import deepcopy
from pathlib import Path
from typing import cast
from uuid import uuid4

from short_drama.asset_workbench import (
    AssetProviderConfigurationError,
    AssetWorkbench,
    AssetWorkbenchConflict,
    AssetWorkbenchNotFound,
    ImageProviderFactory,
    default_image_provider_factory,
)
from short_drama.asset_inspection import (
    SemanticAssetReview,
    build_base_asset_prompt,
    build_keyframe_correction_prompt,
    inspect_asset_image,
)
from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
    ReferenceViewNeed,
    build_reference_view_prompt,
    ensure_base_reference_pack_entries,
    missing_reference_views,
    search_asset_catalog,
)
from short_drama.audio_inspection import AudioInspector, AudioSemanticService
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.h3_models import (
    H3CompilePreviewResponse,
    H3ProductionRouteExecuteRequest,
    H3ProductionRouteExecuteResponse,
    H3ProductionRoutePlan,
    H3ProductionRoutePlanListResponse,
    H3ProductionRoutePlanRequest,
    H3ProductionRoutePlanResponse,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3NotSubmittedResolutionRequest,
    H3ProbeRequest,
    H3ProbeResponse,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
    H3ProviderRun,
    H3ProviderRunsResponse,
    H3ReconciliationCandidatesResponse,
    H3RoutePlanStatus,
    H3RouteAssetPreparation,
    H3RouteAssetPreparationStatus,
    H3ShotRouteAction,
    H3ShotRouteExecutionStatus,
    H3ShotRoutePlan,
    H3ShotRouteReadiness,
    H3TakeReviewRequest,
    H3TakeReviewResponse,
    H3TaskActionResponse,
)
from short_drama.h3_route_planner import (
    RoutePlanningError,
    build_h3_production_route_plan,
    validate_route_plan_freshness,
)
from short_drama.h3_worker import (
    H3ClientFactory,
    H3ProviderConfigurationError,
    PersistentH3Worker,
    default_h3_client_factory,
)
from short_drama.h3_video_guides import H3VideoGuideBuilder
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReviewDecision,
    AssetReviewRequest,
    ImageGenerationTask,
    ImageOperation,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
    ImageTaskCreateRequest,
    ImageTaskListResponse,
    ImageTaskResponse,
    ImageTaskStatus,
)
from short_drama.language_models import (
    LanguagePromptCompileRequest,
    LanguagePromptCompileResponse,
    LanguageProviderConfigResponse,
    LanguageProviderConfigUpdate,
)
from short_drama.media import MediaGenerationError, MockMediaRenderer, TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    AudioInspectionReport,
    AudioInspectionRequest,
    AudioInspectionResponse,
    Asset,
    AssetKind,
    AssetVersion,
    AssetMediaType,
    AutoDraftResponse,
    AutonomousExecutionTarget,
    AutonomousDraftCreateRequest,
    AutonomousRepeatCreateRequest,
    AutonomousDraftResponse,
    AutonomousRunAdvanceRequest,
    AutonomousRunRecord,
    AutonomousRunStatus,
    AutonomousRunStep,
    CharacterProfile,
    CreateProjectRequest,
    DirectorRepairDecision,
    DirectorRepairRecord,
    DirectorRepairStrategy,
    EnqueueRequest,
    EvidencePurpose,
    EvidenceStatus,
    ExecutionPlanEntry,
    ExportRecord,
    Freshness,
    GenerationTask,
    LocalReworkRequest,
    MockBehavior,
    OperationKind,
    OutputSpec,
    PlanningOutlineSnapshot,
    PlanningReviewSnapshot,
    PlanningRunCreateRequest,
    PlanningRunListResponse,
    PlanningRunRecord,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanningRunStep,
    ProgressSummary,
    ProjectAggregate,
    ProjectBibleVersion,
    ProjectDocument,
    ProjectListResponse,
    ProjectStage,
    ReferencePack,
    ReferencePackEntry,
    ReferenceRole,
    ReferenceBinding,
    ReferencePlanEntry,
    RouteEvidence,
    RunMode,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReviseScriptLineRequest,
    ScriptQualityCatalogResponse,
    ScriptQualityComparison,
    ScriptQualityHistoryResponse,
    ScriptQualityRoundSummary,
    ScriptQualityTrend,
    ScriptReviewSeverity,
    ScriptVersion,
    SemanticReviewStatus,
    ShotPlan,
    SourceMode,
    SubtitleCue,
    Take,
    TakeAvailability,
    TaskStatus,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
    VideoGenerationRoute,
    VideoInspectionReport,
    VideoInspectionRequest,
    VideoInspectionResponse,
    VoiceReferenceBinding,
    WorkerDrainResponse,
)
from short_drama.production_planner import (
    EpisodeOutline,
    ProductionPlanningResult,
    SceneShotPlan,
    assemble_multi_agent_planning_result,
    build_project_document_from_plan,
    create_production_plan,
    create_story_architecture,
    direct_story_scene,
    judge_story_revisions,
    refresh_lightweight_reference_plans,
    review_story_outline,
    revise_story_outline,
)
from short_drama.quality_learning import (
    H3CapabilityProfileResponse,
    build_h3_capability_profile,
    known_regression_case,
)
from short_drama.reference_planner import (
    build_director_advice_repair_input,
    DirectorAdviceRequest,
    DirectorAdviceResponse,
    DirectorLoopResponse,
    DirectorRepairDecisionRequest,
    KeyframeBriefCompileRequest,
    KeyframeBriefCompileResponse,
    ReferencePackEntryCreateRequest,
    ReferencePackEntryMutationResponse,
    ReferencePlanPreviewRequest,
    ReferencePlanPreviewResponse,
    ShotReferencePlanUpdateRequest,
    build_director_advice_input,
    compile_keyframe_brief,
    parse_director_advice,
    preview_reference_plan,
    role_is_compatible,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.h3_language_prompt import (
    LanguagePromptValidationError,
    build_language_prompt_input,
    validate_language_prompt,
)
from short_drama.providers.image_config import ImageProviderConfigStore
from short_drama.providers.image_provider import ImageProviderError
from short_drama.providers.language_config import LanguageProviderConfigStore
from short_drama.providers.minimax_h3_client import H3ClientError
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    compile_h3_request,
    validate_h3_probe,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderClientFactory,
    LanguageProviderError,
    default_language_provider_factory,
    is_loopback_openai_url,
)
from short_drama.repository import ShortDramaRepository
from short_drama.video_inspection import VideoInspector
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)
from short_drama.worker import InvalidTaskTransition, PersistentMockWorker, utc_now


class ProjectNotFoundError(KeyError):
    """Raised when an API operation references an unknown project."""


class ArtifactNotFoundError(KeyError):
    """Raised when an API operation references an unknown task, take, shot, or line."""


class WorkflowConflictError(ValueError):
    """Raised when persisted workflow state does not allow the requested action."""


class ProviderOperationError(RuntimeError):
    """Raised when a configured external provider cannot complete an operation."""


_TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}
_MAX_AUTONOMOUS_IMAGE_ATTEMPTS = 3
_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS = 3
_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS = 7200
_BOUNDED_BEST_EFFORT_CHECK = "bounded-auto-draft-best-effort-waiver"
_REFERENCE_VIEW_MARKER_PREFIX = "AUTO_REFERENCE_VIEW:"
_REFERENCE_VIEW_SKIP_PREFIX = "reference-view-skipped:"


def _take_is_timeline_eligible(take: Take) -> bool:
    return take.qa_status == QaStatus.PASSED or (
        _BOUNDED_BEST_EFFORT_CHECK in take.qa_report.checks
    )


def _next_h3_continuity_wave(
    document: ProjectDocument,
    takes: list[Take],
) -> list[str]:
    """Return the next uncovered shot in each scene.

    This lets later shots use an accepted preceding Take as an optional Route Agent
    input without forcing reference-video generation or serializing unrelated scenes.
    """

    covered = {
        take.shot_id
        for take in takes
        if take.is_selected
        and take.freshness == Freshness.CURRENT
        and take.availability == TakeAvailability.AVAILABLE
        and _take_is_timeline_eligible(take)
    }
    selected_scenes: set[str] = set()
    result: list[str] = []
    for shot in sorted(document.shots, key=lambda item: item.sequence):
        if shot.id in covered:
            continue
        scene_key = shot.scene_id or "__UNSCOPED_SCENE__"
        if scene_key in selected_scenes:
            continue
        selected_scenes.add(scene_key)
        result.append(shot.id)
    return result


def _missing_timeline_shot_ids(
    document: ProjectDocument,
    takes: list[Take],
    *,
    restrict_to: set[str] | None = None,
) -> list[str]:
    covered = {
        take.shot_id
        for take in takes
        if take.is_selected
        and take.freshness == Freshness.CURRENT
        and take.availability == TakeAvailability.AVAILABLE
        and _take_is_timeline_eligible(take)
    }
    return [
        shot.id
        for shot in sorted(document.shots, key=lambda item: item.sequence)
        if shot.id not in covered
        and (restrict_to is None or shot.id in restrict_to)
    ]


def _timeline_clip_window(shot: ShotPlan, take: Take) -> tuple[float, float]:
    """Fit provider media to the Director's shot duration without freezing frames.

    H3 can return a longer clip than requested. Dialogue shots keep their opening so
    speech is not entered mid-line. Silent action shots whose continuity state changes
    keep the resolved tail, which is usually the edit-safe portion containing the
    declared action-out state.
    """

    if take.operation in {OperationKind.EXTEND, OperationKind.GAP_FILL}:
        return 0.0, take.duration_seconds

    clip_duration = min(shot.duration_seconds, take.duration_seconds)
    overrun = max(0.0, take.duration_seconds - clip_duration)
    has_resolved_action = (
        shot.continuity.action_in.strip() != shot.continuity.action_out.strip()
        or shot.continuity.prop_state_in.strip() != shot.continuity.prop_state_out.strip()
    )
    preserve_tail = not shot.dialogue_line_ids and has_resolved_action
    source_in_seconds = overrun if preserve_tail else 0.0
    return source_in_seconds, clip_duration


def _can_receive_bounded_best_effort_waiver(
    take: Take,
    evidence: RouteEvidence | None,
) -> bool:
    if (
        take.freshness != Freshness.CURRENT
        or take.availability != TakeAvailability.AVAILABLE
        or take.qa_status != QaStatus.REJECTED
        or take.qa_report.score < 2.5
        or _BOUNDED_BEST_EFFORT_CHECK in take.qa_report.checks
        or evidence is None
        or evidence.technical_passed is not True
        or evidence.purpose == EvidencePurpose.CONTRACT_SMOKE
    ):
        return False
    high_issues: list[QaIssue] = []
    for issue in take.qa_report.issues:
        severity = issue.severity.upper()
        if severity in {"ERROR", "CRITICAL", "FATAL"}:
            return False
        if severity in {"HIGH", "MAJOR"}:
            high_issues.append(issue)
    if len(high_issues) > 1:
        return False
    non_waivable_categories = {
        QaFailureCategory.TECHNICAL_MEDIA,
        QaFailureCategory.CONTACT_CONTINUITY,
        QaFailureCategory.SPATIAL_CONTINUITY,
        QaFailureCategory.CHARACTER_ORIENTATION,
        QaFailureCategory.IDENTITY_CONTINUITY,
    }
    for issue in high_issues:
        if issue.category in non_waivable_categories:
            return False
        if issue.category == QaFailureCategory.PROP_CONTINUITY and any(
            token in issue.code.upper()
            for token in (
                "DUPLICAT",
                "STATE",
                "CONTACT",
                "HAND",
                "MISSING",
                "ORIENTATION",
                "TOPOLOGY",
            )
        ):
            return False
    return True


def _order_keyframe_reference_versions(
    bound: list[tuple[Asset, AssetVersion]],
    reference_plan: ReferencePlanEntry,
    preferred_version_ids: list[str],
) -> list[tuple[Asset, AssetVersion]]:
    """Keep the Agent/reference-plan priority when choosing an image-edit anchor."""

    by_version_id = {version.id: (asset, version) for asset, version in bound}
    by_asset_id = {asset.id: (asset, version) for asset, version in bound}
    ordered: list[tuple[Asset, AssetVersion]] = []
    seen: set[str] = set()

    def append(candidate: tuple[Asset, AssetVersion] | None) -> None:
        if candidate is None or candidate[1].id in seen:
            return
        ordered.append(candidate)
        seen.add(candidate[1].id)

    for version_id in preferred_version_ids:
        append(by_version_id.get(version_id))
    for requirement in reference_plan.requirements:
        append(by_asset_id.get(requirement.subject_asset_id))
    for binding in reference_plan.bindings:
        append(by_version_id.get(binding.target_id))
    for candidate in bound:
        append(candidate)
    return ordered


def _scope_keyframe_reference_versions(
    document: ProjectDocument,
    bound: list[tuple[Asset, AssetVersion]],
    reference_plan: ReferencePlanEntry,
    preferred_version_ids: list[str],
) -> list[tuple[Asset, AssetVersion]]:
    """Keep route preferences without dropping a visible subject's identity anchor."""

    if not bound:
        return []

    subject_by_version_id: dict[str, str] = {}
    for pack in document.reference_packs:
        for entry in pack.entries:
            subject_by_version_id[entry.asset_version_id] = pack.subject_asset_id
    for asset, version in bound:
        subject_by_version_id.setdefault(version.id, asset.id)

    preferred_order = {
        version_id: index for index, version_id in enumerate(preferred_version_ids)
    }
    preferred = sorted(
        (item for item in bound if item[1].id in preferred_order),
        key=lambda item: preferred_order[item[1].id],
    )
    candidates = [*preferred, *(item for item in bound if item not in preferred)]

    required_subject_ids: list[str] = []

    def require(subject_id: str) -> None:
        if subject_id not in required_subject_ids:
            required_subject_ids.append(subject_id)

    for requirement in reference_plan.requirements:
        if requirement.required:
            require(requirement.subject_asset_id)
    for beat in reference_plan.action_beats:
        require(beat.actor_asset_id)
    for interaction in reference_plan.interactions:
        require(interaction.actor_asset_id)
        require(interaction.target_asset_id)
    for requirement in reference_plan.requirements:
        require(requirement.subject_asset_id)

    limit = max(1, reference_plan.max_reference_images)
    if not required_subject_ids:
        if preferred:
            return preferred[:limit]
        return _filter_generation_reference_versions(bound, reference_plan)[:limit]

    selected: list[tuple[Asset, AssetVersion]] = []
    seen_versions: set[str] = set()

    def append(candidate: tuple[Asset, AssetVersion] | None) -> None:
        if (
            candidate is None
            or candidate[1].id in seen_versions
            or len(selected) >= limit
        ):
            return
        selected.append(candidate)
        seen_versions.add(candidate[1].id)

    # Reserve one best available reference per visible/required subject. Several
    # specialised location or prop views must not consume the whole provider input
    # budget while character identity anchors silently disappear.
    for subject_id in required_subject_ids:
        append(
            next(
                (
                    item
                    for item in candidates
                    if subject_by_version_id.get(item[1].id) == subject_id
                ),
                None,
            )
        )

    # Use remaining capacity for the Agent's higher-resolution semantic views.
    for candidate in preferred:
        append(candidate)
    for candidate in _filter_generation_reference_versions(bound, reference_plan):
        append(candidate)
    return selected


def _prioritize_keyframe_canvas(
    ordered: list[tuple[Asset, AssetVersion]],
    shot: ShotPlan,
) -> list[tuple[Asset, AssetVersion]]:
    """Choose a composition-friendly edit canvas from the selected references."""

    framing = shot.framing.lower()
    if any(
        token in framing
        for token in ("道具", "手部", "物件", "产品", "insert", "product", "detail")
    ):
        preferred_kind = AssetKind.PROP
    elif any(
        token in framing
        for token in ("双人", "多人", "群像", "全景", "远景", "wide", "two-shot", "master")
    ):
        preferred_kind = AssetKind.LOCATION
    elif any(
        token in framing
        for token in ("面部", "胸像", "单人", "近景", "特写", "portrait", "close-up")
    ):
        preferred_kind = AssetKind.CHARACTER
    else:
        character_count = sum(asset.kind == AssetKind.CHARACTER for asset, _ in ordered)
        preferred_kind = (
            AssetKind.LOCATION if character_count > 1 else AssetKind.CHARACTER
        )
    primary = next(
        (item for item in ordered if item[0].kind == preferred_kind),
        None,
    )
    if primary is None:
        return ordered
    return [primary, *(item for item in ordered if item[1].id != primary[1].id)]


def _filter_generation_reference_versions(
    bound: list[tuple[Asset, AssetVersion]],
    reference_plan: ReferencePlanEntry,
) -> list[tuple[Asset, AssetVersion]]:
    """Exclude continuity-only assets that the Agent did not plan to show in this shot."""

    subject_ids = {
        requirement.subject_asset_id for requirement in reference_plan.requirements
    }
    subject_ids.update(beat.actor_asset_id for beat in reference_plan.action_beats)
    for interaction in reference_plan.interactions:
        subject_ids.add(interaction.actor_asset_id)
        subject_ids.add(interaction.target_asset_id)
    if not subject_ids:
        return bound
    filtered = [item for item in bound if item[0].id in subject_ids]
    return filtered or bound


def _remap_exact_identifiers(
    value: object,
    identifier_map: dict[str, str],
) -> object:
    """Remap domain identifiers without rewriting paths, prompts, or prose substrings."""

    if isinstance(value, str):
        return identifier_map.get(value, value)
    if isinstance(value, list):
        return [
            _remap_exact_identifiers(item, identifier_map)
            for item in cast(list[object], value)
        ]
    if isinstance(value, dict):
        return {
            key: _remap_exact_identifiers(item, identifier_map)
            for key, item in cast(dict[str, object], value).items()
        }
    return value


class ShortDramaService:
    """Owns the SQLite truth source, worker, Mock adapter, and manifest exporter."""

    def __init__(
        self,
        *,
        database_path: Path,
        workspace_root: Path,
        h3_client_factory: H3ClientFactory | None = None,
        image_provider_factory: ImageProviderFactory | None = None,
        language_provider_factory: LanguageProviderClientFactory | None = None,
        audio_semantic_service: AudioSemanticService | None = None,
    ) -> None:
        self.repository = ShortDramaRepository(database_path)
        self.workspace_root = workspace_root
        self.media_renderer = MockMediaRenderer(workspace_root)
        self.exporter = TimelineExporter(workspace_root)
        self.video_inspector = VideoInspector(workspace_root)
        self.h3_video_guide_builder = H3VideoGuideBuilder(workspace_root)
        self.audio_inspector = AudioInspector(workspace_root)
        self.audio_semantic_service = audio_semantic_service
        self.worker = PersistentMockWorker(self.repository, self.media_renderer)
        self.h3_config = H3ProviderConfigStore(
            database_path.parent / "short-drama-provider-settings.json"
        )
        self.h3_worker = PersistentH3Worker(
            repository=self.repository,
            workspace_root=workspace_root,
            config_store=self.h3_config,
            client_factory=h3_client_factory or default_h3_client_factory,
        )
        self.image_config = ImageProviderConfigStore(
            database_path.parent / "short-drama-image-provider-settings.json"
        )
        self.asset_workbench = AssetWorkbench(
            repository=self.repository,
            workspace_root=workspace_root,
            config_store=self.image_config,
            provider_factory=image_provider_factory or default_image_provider_factory,
        )
        self.language_config = LanguageProviderConfigStore(
            database_path.parent / "short-drama-language-provider-settings.json"
        )
        self.language_provider_factory = (
            language_provider_factory or default_language_provider_factory
        )
        self._lock = threading.RLock()
        self._planning_run_locks: dict[str, threading.Lock] = {}

    def list_projects(self) -> ProjectListResponse:
        return ProjectListResponse(
            projects=[document.project for document in self.repository.list_projects()]
        )

    def list_planning_runs(self) -> PlanningRunListResponse:
        return PlanningRunListResponse(runs=self.repository.list_planning_runs())

    def get_planning_run(self, run_id: str) -> PlanningRunResponse:
        return self._planning_run_response(self._require_planning_run(run_id))

    def get_planning_run_script_quality(
        self,
        run_id: str,
    ) -> ScriptQualityHistoryResponse:
        run = self._require_planning_run(run_id)
        outlines = self.repository.list_planning_outline_snapshots(run_id)
        reviews = self.repository.list_planning_review_snapshots(run_id)
        return ScriptQualityHistoryResponse(
            comparison=self._script_quality_comparison(run, reviews),
            outline_snapshots=outlines,
            review_snapshots=reviews,
            revision_judge=run.revision_judge,
        )

    def list_script_quality_evaluations(self) -> ScriptQualityCatalogResponse:
        evaluations: list[ScriptQualityComparison] = []
        for run in self.repository.list_planning_runs():
            reviews = self.repository.list_planning_review_snapshots(run.id)
            if not reviews:
                continue
            evaluations.append(self._script_quality_comparison(run, reviews))
        return ScriptQualityCatalogResponse(evaluations=evaluations)

    def create_planning_run(
        self,
        request: PlanningRunCreateRequest,
    ) -> PlanningRunResponse:
        now = utc_now()
        run = PlanningRunRecord(
            id=f"planning-run-{uuid4().hex}",
            status=PlanningRunStatus.RUNNING,
            current_step=PlanningRunStep.STORY_ARCHITECT,
            request=request,
            created_at=now,
            updated_at=now,
        )
        self.repository.save_planning_run(run)
        return PlanningRunResponse(run=run)

    def advance_planning_run(self, run_id: str) -> PlanningRunResponse:
        lock = self._planning_run_lock(run_id)
        if not lock.acquire(blocking=False):
            raise WorkflowConflictError(f"Planning run {run_id} is already advancing")
        try:
            run = self._require_planning_run(run_id)
            if run.status == PlanningRunStatus.COMPLETE:
                return self._planning_run_response(run)
            if run.status == PlanningRunStatus.FAILED:
                raise WorkflowConflictError(
                    f"Planning run {run_id} failed and cannot be advanced"
                )
            run.status = PlanningRunStatus.RUNNING
            run.error_message = None
            self._advance_planning_run_once(run)
            run.updated_at = utc_now()
            self.repository.save_planning_run(run)
            return self._planning_run_response(run)
        except WorkflowConflictError:
            raise
        except Exception as exc:
            run = self.repository.get_planning_run(run_id)
            if run is None:
                raise
            run.status = PlanningRunStatus.NEEDS_ATTENTION
            run.error_message = str(exc)
            run.updated_at = utc_now()
            self.repository.save_planning_run(run)
            return self._planning_run_response(run)
        finally:
            lock.release()

    def _advance_planning_run_once(self, run: PlanningRunRecord) -> None:
        request = CreateProjectRequest.model_validate(
            run.request.model_dump(exclude={"render_export", "execution_target"})
        )
        client = self._configured_language_client()
        if run.current_step == PlanningRunStep.STORY_ARCHITECT:
            architecture = create_story_architecture(request, client)
            run.outline_json = architecture.outline.model_dump_json()
            run.outline_source = architecture.source
            run.total_scenes = len(architecture.outline.scenes)
            run.warnings.extend(architecture.warnings)
            self.repository.save_planning_outline_snapshot(
                run.id,
                PlanningOutlineSnapshot(
                    revision_round=0,
                    outline_json=run.outline_json,
                    source=architecture.source,
                    created_at=utc_now(),
                ),
            )
            self._complete_planning_step(
                run,
                PlanningRunStep.STORY_ARCHITECT,
                PlanningRunStep.SCRIPT_CRITIC,
            )
            return

        outline = self._planning_outline(run)
        if run.current_step == PlanningRunStep.SCRIPT_CRITIC:
            critique = review_story_outline(request, outline, client)
            if critique.report is not None:
                run.review_report = critique.report
                self.repository.save_planning_review_snapshot(
                    run.id,
                    PlanningReviewSnapshot(
                        review_round=run.revision_round + 1,
                        revision_round=run.revision_round,
                        report=critique.report,
                        created_at=utc_now(),
                    ),
                )
            run.critic_used = run.critic_used or critique.used
            run.warnings.extend(critique.warnings)
            if (
                critique.report is not None
                and critique.report.requires_revision
                and run.revision_round < run.max_revision_rounds
            ):
                next_step = PlanningRunStep.STORY_REVISION
            else:
                self._append_remaining_review_warning(run)
                if (
                    critique.report is not None
                    and self._latest_review_has_regression(run)
                ):
                    self._append_latest_review_regression_warning(run)
                    next_step = PlanningRunStep.REVISION_JUDGE
                else:
                    next_step = PlanningRunStep.SCENE_DIRECTOR
            self._complete_planning_step(
                run,
                PlanningRunStep.SCRIPT_CRITIC,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.STORY_REVISION:
            if run.review_report is None:
                raise WorkflowConflictError(
                    "Planning run has no script review to revise"
                )
            revision = revise_story_outline(
                request,
                outline,
                run.review_report,
                client,
                revision_round=run.revision_round + 1,
            )
            run.warnings.extend(revision.warnings)
            if revision.revised:
                run.outline_json = revision.outline.model_dump_json()
                run.revision_round += 1
                self.repository.save_planning_outline_snapshot(
                    run.id,
                    PlanningOutlineSnapshot(
                        revision_round=run.revision_round,
                        outline_json=run.outline_json,
                        source=(
                            f"Story Architect revision {run.revision_round}"
                        ),
                        created_at=utc_now(),
                    ),
                )
                next_step = PlanningRunStep.SCRIPT_CRITIC
            else:
                self._append_remaining_review_warning(run)
                next_step = PlanningRunStep.SCENE_DIRECTOR
            self._complete_planning_step(
                run,
                PlanningRunStep.STORY_REVISION,
                next_step,
            )
            return

        if run.current_step == PlanningRunStep.REVISION_JUDGE:
            self._run_revision_judge(run, request, client)
            self._complete_planning_step(
                run,
                PlanningRunStep.REVISION_JUDGE,
                PlanningRunStep.SCENE_DIRECTOR,
            )
            return

        if run.current_step == PlanningRunStep.SCENE_DIRECTOR:
            directed_scenes = [
                SceneShotPlan.model_validate_json(value)
                for value in run.directed_scene_json
            ]
            if run.next_scene_sequence > len(outline.scenes):
                run.current_step = PlanningRunStep.FINALIZE
                return
            scene = outline.scenes[run.next_scene_sequence - 1]
            handoff = (
                directed_scenes[-1].continuity_out.model_copy(deep=True)
                if directed_scenes
                else outline.scenes[0].continuity_in.model_copy(deep=True)
            )
            direction = direct_story_scene(
                request,
                outline,
                scene,
                handoff,
                client,
            )
            run.directed_scene_json.append(direction.scene.model_dump_json())
            run.used_scene_repair = run.used_scene_repair or direction.repaired
            run.used_scene_fallback = (
                run.used_scene_fallback or direction.used_fallback
            )
            run.warnings.extend(direction.warnings)
            run.next_scene_sequence += 1
            if run.next_scene_sequence > len(outline.scenes):
                self._complete_planning_step(
                    run,
                    PlanningRunStep.SCENE_DIRECTOR,
                    PlanningRunStep.FINALIZE,
                )
            return

        if run.current_step == PlanningRunStep.FINALIZE:
            directed_scenes = [
                SceneShotPlan.model_validate_json(value)
                for value in run.directed_scene_json
            ]
            planning = assemble_multi_agent_planning_result(
                request,
                outline,
                directed_scenes,
                outline_source=run.outline_source or "unknown story architect",
                critic_used=run.critic_used,
                revision_rounds=run.revision_round,
                used_scene_repair=run.used_scene_repair,
                used_scene_fallback=run.used_scene_fallback,
                warnings=run.warnings,
                review_report=run.review_report,
            )
            if run.project_id is None:
                run.project_id = f"project-{uuid4().hex[:12]}"
                run.updated_at = utc_now()
                self.repository.save_planning_run(run)
            document = build_project_document_from_plan(
                project_id=run.project_id,
                request=request,
                planning=planning,
                created_at=utc_now(),
                planning_run_id=run.id,
            )
            with self._lock:
                self.repository.save_project(document)
            if request.run_mode == RunMode.AUTO_DRAFT:
                autonomous = self._initialize_autonomous_run(
                    run.project_id,
                    request=request,
                    planning=planning,
                    execution_target=run.request.execution_target,
                    render_export=run.request.render_export,
                    run_id=run.autonomous_run_id
                    or f"autonomous-run-{run.id.removeprefix('planning-run-')}",
                )
                run.autonomous_run_id = autonomous.id
            self._complete_planning_step(
                run,
                PlanningRunStep.FINALIZE,
                PlanningRunStep.COMPLETE,
            )
            run.status = PlanningRunStatus.COMPLETE
            run.completed_at = utc_now()
            if PlanningRunStep.COMPLETE not in run.completed_steps:
                run.completed_steps.append(PlanningRunStep.COMPLETE)
            return

        if run.current_step == PlanningRunStep.COMPLETE:
            run.status = PlanningRunStatus.COMPLETE
            return
        raise WorkflowConflictError(
            f"Planning run cannot advance from {run.current_step.value}"
        )

    @staticmethod
    def _complete_planning_step(
        run: PlanningRunRecord,
        completed_step: PlanningRunStep,
        next_step: PlanningRunStep,
    ) -> None:
        if completed_step not in run.completed_steps:
            run.completed_steps.append(completed_step)
        run.current_step = next_step

    @staticmethod
    def _append_remaining_review_warning(run: PlanningRunRecord) -> None:
        report = run.review_report
        if report is None or not report.requires_revision:
            return
        warning = (
            f"当前剧本审阅仍保留 {len(report.issues)} 项可选修改建议；"
            "建议不会阻断后续规划，用户可继续、编辑或忽略。"
        )
        if warning not in run.warnings:
            run.warnings.append(warning)

    def _latest_review_has_regression(self, run: PlanningRunRecord) -> bool:
        reviews = self.repository.list_planning_review_snapshots(run.id)
        if len(reviews) < 2:
            return False
        previous = reviews[-2].report
        latest = reviews[-1].report
        previous_high = sum(
            issue.severity == ScriptReviewSeverity.HIGH
            for issue in previous.issues
        )
        latest_high = sum(
            issue.severity == ScriptReviewSeverity.HIGH
            for issue in latest.issues
        )
        return (
            len(latest.issues) > len(previous.issues)
            or latest_high > previous_high
        )

    def _run_revision_judge(
        self,
        run: PlanningRunRecord,
        request: CreateProjectRequest,
        client: LanguageProviderClient | None,
    ) -> None:
        reviews = self.repository.list_planning_review_snapshots(run.id)
        outlines = self.repository.list_planning_outline_snapshots(run.id)
        run.revision_judge = None
        if len(reviews) < 2:
            run.warnings.append(
                "Revision Judge 缺少两轮审稿证据，已保留最新版继续逐场规划。"
            )
            return
        older_review = reviews[-2]
        newer_review = reviews[-1]
        outlines_by_round = {
            snapshot.revision_round: snapshot for snapshot in outlines
        }
        older_snapshot = outlines_by_round.get(older_review.revision_round)
        newer_snapshot = outlines_by_round.get(newer_review.revision_round)
        if older_snapshot is None or newer_snapshot is None:
            run.warnings.append(
                "Revision Judge 缺少与最近两轮审稿对应的大纲快照，"
                "未替用户改选版本并继续使用最新版。"
            )
            return
        try:
            older_outline = EpisodeOutline.model_validate_json(
                older_snapshot.outline_json
            )
            newer_outline = EpisodeOutline.model_validate_json(
                newer_snapshot.outline_json
            )
        except ValueError as error:
            run.warnings.append(
                "Revision Judge 无法读取最近两版大纲，未替用户改选版本并继续使用最新版："
                + str(error)
            )
            return
        result = judge_story_revisions(
            request,
            older_revision_round=older_review.revision_round,
            older_outline=older_outline,
            older_review=older_review.report,
            newer_revision_round=newer_review.revision_round,
            newer_outline=newer_outline,
            newer_review=newer_review.report,
            client=client,
        )
        run.revision_judge = result.recommendation
        run.warnings.extend(result.warnings)
        if result.recommendation is None:
            return
        recommended = result.recommendation.recommended_revision_round
        label = "初稿" if recommended == 0 else f"第 {recommended} 轮修订稿"
        run.warnings.append(
            f"Revision Judge 建议优先比较并考虑 {label}"
            f"（{result.recommendation.confidence.value} 置信度）。"
            "当前流程未自动替换大纲，仍使用最新版继续；用户可忽略该建议。"
        )

    def _append_latest_review_regression_warning(
        self,
        run: PlanningRunRecord,
    ) -> None:
        reviews = self.repository.list_planning_review_snapshots(run.id)
        if len(reviews) < 2:
            return
        previous = reviews[-2].report
        latest = reviews[-1].report
        previous_high = sum(
            issue.severity == ScriptReviewSeverity.HIGH
            for issue in previous.issues
        )
        latest_high = sum(
            issue.severity == ScriptReviewSeverity.HIGH
            for issue in latest.issues
        )
        if not self._latest_review_has_regression(run):
            return
        warning = (
            "最近一轮复审较上一轮报告了更多问题"
            f"（总数 {len(previous.issues)}→{len(latest.issues)}，"
            f"HIGH {previous_high}→{latest_high}）。"
            "Director Agent 建议比较最近两版大纲后再决定；"
            "这不会阻断后续规划，用户也可忽略。"
        )
        if warning not in run.warnings:
            run.warnings.append(warning)

    @staticmethod
    def _planning_outline(run: PlanningRunRecord) -> EpisodeOutline:
        if run.outline_json is None:
            raise WorkflowConflictError("Planning run has no Story Architect outline")
        return EpisodeOutline.model_validate_json(run.outline_json)

    def _planning_run_response(self, run: PlanningRunRecord) -> PlanningRunResponse:
        document = (
            self.repository.get_project(run.project_id)
            if run.project_id is not None
            else None
        )
        project = self._aggregate(document) if document is not None else None
        return PlanningRunResponse(run=run, project=project)

    def _require_planning_run(self, run_id: str) -> PlanningRunRecord:
        run = self.repository.get_planning_run(run_id)
        if run is None:
            raise ArtifactNotFoundError(run_id)
        return run

    def _planning_run_lock(self, run_id: str) -> threading.Lock:
        with self._lock:
            lock = self._planning_run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._planning_run_locks[run_id] = lock
            return lock

    @staticmethod
    def _script_quality_comparison(
        run: PlanningRunRecord,
        reviews: list[PlanningReviewSnapshot],
    ) -> ScriptQualityComparison:
        rounds = [
            ScriptQualityRoundSummary(
                review_round=snapshot.review_round,
                revision_round=snapshot.revision_round,
                total_issues=len(snapshot.report.issues),
                high_issues=sum(
                    issue.severity == ScriptReviewSeverity.HIGH
                    for issue in snapshot.report.issues
                ),
                medium_issues=sum(
                    issue.severity == ScriptReviewSeverity.MEDIUM
                    for issue in snapshot.report.issues
                ),
                low_issues=sum(
                    issue.severity == ScriptReviewSeverity.LOW
                    for issue in snapshot.report.issues
                ),
                requires_revision=snapshot.report.requires_revision,
                categories=list(
                    dict.fromkeys(issue.category for issue in snapshot.report.issues)
                ),
            )
            for snapshot in reviews
        ]
        if not rounds:
            trend = ScriptQualityTrend.NOT_COMPARABLE
            total_delta: int | None = None
            high_delta: int | None = None
            persistent: list[str] = []
            absent: list[str] = []
            newly_reported: list[str] = []
            latest_total_delta: int | None = None
            latest_high_delta: int | None = None
            latest_regression_risk = False
            advisory = (
                "当前没有可用的语义审稿报告；结构校验结果不能替代 "
                "Script Critic 的剧本质量建议。"
            )
        elif len(rounds) == 1:
            trend = ScriptQualityTrend.NOT_COMPARABLE
            total_delta = None
            high_delta = None
            persistent = []
            absent = []
            newly_reported = []
            latest_total_delta = None
            latest_high_delta = None
            latest_regression_risk = False
            advisory = (
                "当前只有一轮独立审稿，尚不能比较修订前后变化；"
                "报告仍是可忽略建议，不构成剧本通过门槛。"
            )
        else:
            initial = rounds[0]
            latest = rounds[-1]
            total_delta = latest.total_issues - initial.total_issues
            high_delta = latest.high_issues - initial.high_issues
            initial_categories = set(initial.categories)
            latest_categories = set(latest.categories)
            persistent = sorted(initial_categories & latest_categories)
            absent = sorted(initial_categories - latest_categories)
            newly_reported = sorted(latest_categories - initial_categories)
            previous = rounds[-2]
            latest_total_delta = latest.total_issues - previous.total_issues
            latest_high_delta = latest.high_issues - previous.high_issues
            latest_regression_risk = (
                latest_total_delta > 0 or latest_high_delta > 0
            )
            if total_delta == 0 and high_delta == 0:
                trend = ScriptQualityTrend.UNCHANGED_REPORTED_ISSUES
            elif (
                total_delta < 0
                and high_delta <= 0
            ) or (
                high_delta < 0
                and total_delta <= 0
            ):
                trend = ScriptQualityTrend.FEWER_REPORTED_ISSUES
            elif (
                total_delta > 0
                and high_delta >= 0
            ) or (
                high_delta > 0
                and total_delta >= 0
            ):
                trend = ScriptQualityTrend.MORE_REPORTED_ISSUES
            else:
                trend = ScriptQualityTrend.MIXED_REPORTED_ISSUES
            advisory = (
                f"独立审稿报告从 {initial.total_issues} 项变为 "
                f"{latest.total_issues} 项，高严重度从 "
                f"{initial.high_issues} 项变为 {latest.high_issues} 项。"
                "这只描述 Critic 各轮报告的变化；类别名消失不等于问题已解决，"
                "新类别也可能是复审发现了更具体的问题。"
            )
            if latest_regression_risk:
                advisory += (
                    f" 最近一轮相比上一轮：总数 "
                    f"{latest_total_delta:+d}，HIGH {latest_high_delta:+d}；"
                    "Director Agent 应建议比较最近两版，不应因为轮次更新"
                    "就默认最新版更好。"
                )
        return ScriptQualityComparison(
            run_id=run.id,
            project_id=run.project_id,
            project_name=run.request.name or "未命名剧本",
            production_profile=run.request.production_profile,
            source_mode=run.request.source_mode,
            target_duration_seconds=run.request.resolved_target_duration_seconds(),
            rounds=rounds,
            trend=trend,
            total_issue_delta=total_delta,
            high_issue_delta=high_delta,
            latest_total_issue_delta=latest_total_delta,
            latest_high_issue_delta=latest_high_delta,
            latest_report_regression_risk=latest_regression_risk,
            persistent_category_labels=persistent,
            category_labels_no_longer_reported=absent,
            newly_reported_category_labels=newly_reported,
            advisory_summary=advisory,
        )

    def search_asset_catalog(
        self,
        request: AssetCatalogSearchRequest,
        *,
        current_project_id: str | None = None,
    ) -> AssetCatalogSearchResponse:
        if current_project_id is not None:
            self._require_project_document(current_project_id)
        return search_asset_catalog(
            self.repository.list_projects(),
            current_project_id=current_project_id,
            request=request,
        )

    def inspect_asset_coverage(self, project_id: str) -> AssetCoverageResponse:
        document = self._require_project_document(project_id)
        missing = missing_reference_views(document)
        return AssetCoverageResponse(
            project_id=project_id,
            ready=not missing,
            missing_views=missing,
        )

    def import_catalog_asset(
        self,
        project_id: str,
        request: AssetCatalogImportRequest,
    ) -> AssetMutationResponse:
        target_document = self._require_project_document(project_id)
        source_document = self._require_project_document(request.source_project_id)
        source_asset = self._require_asset_in_document(
            source_document,
            request.source_asset_id,
        )
        source_version = next(
            (
                item
                for item in source_asset.versions
                if item.id == request.source_version_id
            ),
            None,
        )
        if source_version is None:
            raise ArtifactNotFoundError(request.source_version_id)
        if (
            source_asset.approved_version_id != source_version.id
            or source_version.approval_status != ApprovalStatus.APPROVED
            or source_version.media_type != AssetMediaType.IMAGE
        ):
            raise WorkflowConflictError(
                "Only a current approved catalog image can be imported"
            )
        target_asset: Asset
        if request.target_asset_id is None:
            created = self.asset_workbench.create_asset(
                project_id,
                AssetCreateRequest(
                    kind=source_asset.kind,
                    name=request.name or source_asset.name,
                    descriptor=request.descriptor or source_version.descriptor,
                ),
            )
            target_document = self._require_project_document(project_id)
            target_asset = next(
                item
                for item in target_document.assets
                if item.current_version_id == created.id
            )
        else:
            target_asset = self._require_asset_in_document(
                target_document,
                request.target_asset_id,
            )
            if (
                target_asset.kind != AssetKind.REFERENCE
                and target_asset.kind != source_asset.kind
            ):
                raise WorkflowConflictError(
                    "Catalog source kind does not match the target asset"
                )
        version = self.asset_workbench.clone_catalog_image(
            project_id,
            target_asset.id,
            source_project_id=source_document.project.id,
            source_asset_id=source_asset.id,
            source_asset_name=source_asset.name,
            source_version=source_version,
            descriptor=request.descriptor or source_version.descriptor,
        )
        refreshed = self.get_project(project_id)
        asset = next(item for item in refreshed.document.assets if item.id == target_asset.id)
        return AssetMutationResponse(asset=asset, version=version, project=refreshed)

    def initialize_fixture(self) -> ProjectAggregate:
        with self._lock:
            fixture = build_dont_trust_me_fixture(utc_now())
            if not self.repository.project_exists(fixture.project.id):
                self.repository.save_project(fixture)
            return self.get_project(fixture.project.id)

    def create_project(self, request: CreateProjectRequest) -> ProjectAggregate:
        project, _planning = self._create_planned_project(request)
        self._import_creative_input_images(project.document.project.id, request)
        return self.get_project(project.document.project.id)

    def create_autonomous_draft(
        self,
        request: AutonomousDraftCreateRequest,
    ) -> AutonomousDraftResponse:
        create_request = CreateProjectRequest.model_validate(
            request.model_dump(exclude={"render_export", "execution_target"})
        )
        project, planning = self._create_planned_project(create_request)
        project_id = project.document.project.id
        run_id = f"autonomous-run-{uuid4().hex}"
        try:
            run = self._initialize_autonomous_run(
                project_id,
                request=create_request,
                planning=planning,
                execution_target=request.execution_target,
                render_export=request.render_export,
                run_id=run_id,
            )
            if request.execution_target == AutonomousExecutionTarget.REAL_H3:
                return self.advance_autonomous_run(
                    project_id,
                    run.id,
                    AutonomousRunAdvanceRequest(max_actions=1),
                )
            result = self._execute_auto_draft(
                project_id,
                render_export=request.render_export,
                autonomous_run_id=run.id,
            )
        except (WorkflowConflictError, ProviderOperationError, MediaGenerationError) as exc:
            if any(
                item.id == run_id
                for item in self._require_project_document(project_id).autonomous_runs
            ):
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.NEEDS_ATTENTION,
                    error_message=str(exc),
                )
            raise
        except Exception as exc:
            if any(
                item.id == run_id
                for item in self._require_project_document(project_id).autonomous_runs
            ):
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.FAILED,
                    error_message=str(exc),
                )
            raise
        completed = self._require_autonomous_run(project_id, run_id)
        return AutonomousDraftResponse(
            project=result.project,
            export=result.export,
            run=completed,
        )

    def _initialize_autonomous_run(
        self,
        project_id: str,
        *,
        request: CreateProjectRequest,
        planning: ProductionPlanningResult,
        execution_target: AutonomousExecutionTarget,
        render_export: bool,
        run_id: str | None = None,
    ) -> AutonomousRunRecord:
        resolved_run_id = run_id or f"autonomous-run-{uuid4().hex}"
        with self._lock:
            document = self._require_project_document(project_id)
            existing = next(
                (item for item in document.autonomous_runs if item.id == resolved_run_id),
                None,
            )
            if existing is None:
                now = utc_now()
                existing = AutonomousRunRecord(
                    id=resolved_run_id,
                    project_id=project_id,
                    status=AutonomousRunStatus.RUNNING,
                    current_step=AutonomousRunStep.IMPORT_INPUT_ASSETS,
                    completed_steps=[
                        AutonomousRunStep.NORMALIZE_INPUT,
                        AutonomousRunStep.PLAN,
                    ],
                    planner=planning.planner,
                    execution_target=execution_target,
                    render_export=render_export,
                    warnings=list(planning.warnings),
                    created_at=now,
                    updated_at=now,
                )
                document.autonomous_runs.append(existing)
                self.repository.save_project(document)
        if existing.current_step == AutonomousRunStep.IMPORT_INPUT_ASSETS:
            self._import_creative_input_images(project_id, request)
            self._advance_autonomous_run(
                project_id,
                existing.id,
                completed_step=AutonomousRunStep.IMPORT_INPUT_ASSETS,
                next_step=(
                    AutonomousRunStep.GENERATE
                    if execution_target == AutonomousExecutionTarget.MOCK
                    else AutonomousRunStep.PREPARE_ASSETS
                ),
            )
        return self._require_autonomous_run(project_id, existing.id)

    def create_autonomous_repeat(
        self,
        source_project_id: str,
        request: AutonomousRepeatCreateRequest,
    ) -> AutonomousDraftResponse:
        """Start an independent generation run from one approved preproduction package."""

        source = self._require_project_document(source_project_id)
        source_script = next(
            (
                item
                for item in source.script_versions
                if item.id == source.project.current_script_version_id
            ),
            None,
        )
        source_bible = next(
            (
                item
                for item in source.bible_versions
                if item.id == source.project.current_bible_version_id
            ),
            None,
        )
        if source_script is None or source_bible is None:
            raise WorkflowConflictError(
                "The source project has no current approved script and ProjectBible"
            )
        if (
            source_script.approval_status != ApprovalStatus.APPROVED
            or source_bible.approval_status != ApprovalStatus.APPROVED
        ):
            raise WorkflowConflictError(
                "The source script and ProjectBible must be approved before a repeat run"
            )
        if not source.shots or any(
            item.approval_status != ApprovalStatus.APPROVED
            or item.freshness != Freshness.CURRENT
            for item in source.shots
        ):
            raise WorkflowConflictError(
                "The source project needs a complete current approved shot plan"
            )

        source_assets = [
            item
            for item in source.assets
            if item.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
        ]
        approved_assets: list[tuple[Asset, AssetVersion]] = []
        for asset in source_assets:
            approved = self._approved_image_version(asset)
            if approved is None:
                raise WorkflowConflictError(
                    f"The source preproduction package lacks an approved image for {asset.name}"
                )
            if approved.local_path is not None:
                path = Path(approved.local_path).expanduser().resolve()
                if not path.is_file() or path.stat().st_size == 0:
                    raise WorkflowConflictError(
                        f"The approved source asset is unavailable: {asset.name}"
                    )
            approved_assets.append((asset, approved))
        if not approved_assets:
            raise WorkflowConflictError(
                "The source preproduction package has no approved core visual assets"
            )
        copied_voice_reference_count = 0
        voice_asset_ids = {item.asset_id for item in source.voice_references}
        for asset in source.assets:
            if asset.id not in voice_asset_ids:
                continue
            try:
                approved_voice = self._require_approved_voice_audio(asset)
            except WorkflowConflictError:
                continue
            approved_assets.append((asset, approved_voice))
            copied_voice_reference_count += 1

        project_id = f"project-{uuid4().hex[:12]}"
        now = utc_now()
        identifier_map: dict[str, str] = {
            source.project.id: project_id,
            source_script.id: f"{project_id}-script-v1",
            source_bible.id: f"{project_id}-bible-v1",
        }
        identifier_map.update(
            {
                line.id: f"{project_id}-line-{index:02d}"
                for index, line in enumerate(source_script.lines, start=1)
            }
        )
        identifier_map.update(
            {
                scene.id: f"{project_id}-scene-{index:02d}"
                for index, scene in enumerate(source_script.structured_scenes, start=1)
            }
        )
        identifier_map.update(
            {
                shot.id: f"{project_id}-shot-{shot.sequence:02d}"
                for shot in source.shots
            }
        )
        identifier_map.update(
            {
                asset.id: f"{project_id}-asset-{asset.kind.value.lower()}-{index:02d}"
                for index, (asset, _approved) in enumerate(approved_assets, start=1)
            }
        )

        script = ScriptVersion.model_validate(
            _remap_exact_identifiers(source_script.model_dump(mode="python"), identifier_map)
        )
        script.version = 1
        script.parent_version_id = None
        script.change_reason = (
            f"Fixed preproduction repeat of {source_project_id}"
        )
        bible = ProjectBibleVersion.model_validate(
            _remap_exact_identifiers(source_bible.model_dump(mode="python"), identifier_map)
        )
        bible.version = 1
        bible.parent_version_id = None
        shots = [
            ShotPlan.model_validate(
                _remap_exact_identifiers(item.model_dump(mode="python"), identifier_map)
            )
            for item in source.shots
        ]
        assets = [
            Asset(
                id=identifier_map[asset.id],
                project_id=project_id,
                kind=asset.kind,
                name=asset.name,
                current_version_id=approved.id,
                approved_version_id=approved.id,
                versions=[
                    approved.model_copy(
                        deep=True,
                        update={"parent_version_id": None},
                    )
                ],
            )
            for asset, approved in approved_assets
        ]
        retained_version_ids = {
            version.id for asset in assets for version in asset.versions
        }
        retained_asset_ids = {asset.id for asset in assets}
        execution_plan = [
            ExecutionPlanEntry.model_validate(
                _remap_exact_identifiers(item.model_dump(mode="python"), identifier_map)
            )
            for item in source.execution_plan
            if item.shot_id in identifier_map
        ]
        reference_plan: list[ReferencePlanEntry] = []
        for source_plan in source.reference_plan:
            if source_plan.shot_id not in identifier_map:
                continue
            plan = ReferencePlanEntry.model_validate(
                _remap_exact_identifiers(
                    source_plan.model_dump(mode="python"),
                    identifier_map,
                )
            )
            plan.bindings = [
                item
                for item in plan.bindings
                if item.target_id in retained_version_ids
            ]
            plan.requirements = [
                item
                for item in plan.requirements
                if item.subject_asset_id in retained_asset_ids
            ]
            plan.action_beats = [
                item
                for item in plan.action_beats
                if item.actor_asset_id in retained_asset_ids
            ]
            plan.interactions = [
                item
                for item in plan.interactions
                if item.actor_asset_id in retained_asset_ids
                and item.target_asset_id in retained_asset_ids
            ]
            reference_plan.append(plan)
        voice_references = [
            VoiceReferenceBinding.model_validate(
                _remap_exact_identifiers(
                    item.model_dump(mode="python"),
                    identifier_map,
                )
            )
            for item in source.voice_references
            if item.asset_id in identifier_map
        ]

        latest_applied_by_shot: dict[str, DirectorRepairRecord] = {}
        for record in source.repair_history:
            if record.decision == DirectorRepairDecision.APPLIED:
                latest_applied_by_shot[record.source_shot_id] = record
        inherited_repairs: list[DirectorRepairRecord] = []
        for source_shot_id, record in latest_applied_by_shot.items():
            mapped_source_shot_id = identifier_map.get(source_shot_id)
            if mapped_source_shot_id is None:
                continue
            inherited_repairs.append(
                DirectorRepairRecord(
                    id=f"director-repair-{uuid4().hex}",
                    source_shot_id=mapped_source_shot_id,
                    strategy=record.strategy,
                    decision=record.decision,
                    reason=f"Inherited approved preproduction decision: {record.reason}",
                    issue_codes=list(record.issue_codes),
                    affected_shot_ids=[
                        identifier_map[item]
                        for item in record.affected_shot_ids
                        if item in identifier_map
                    ],
                    created_at=now,
                )
            )

        run = AutonomousRunRecord(
            id=f"autonomous-run-{uuid4().hex}",
            project_id=project_id,
            status=AutonomousRunStatus.RUNNING,
            current_step=AutonomousRunStep.PLAN_ROUTES,
            completed_steps=[
                AutonomousRunStep.NORMALIZE_INPUT,
                AutonomousRunStep.PLAN,
                AutonomousRunStep.IMPORT_INPUT_ASSETS,
                AutonomousRunStep.PREPARE_ASSETS,
            ],
            planner=f"fixed-preproduction-repeat:{source_project_id}",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=request.render_export,
            warnings=[
                "This run reuses the source project's approved script, ProjectBible, shot plan, "
                "and core asset media; keyframes, H3 tasks, Takes, QA, timeline, and export are new.",
                *(
                    [
                        f"Copied {copied_voice_reference_count} optional character voice reference(s)."
                    ]
                    if copied_voice_reference_count
                    else []
                ),
            ],
            created_at=now,
            updated_at=now,
        )
        project = source.project.model_copy(
            deep=True,
            update={
                "id": project_id,
                "name": request.name or f"{source.project.name} · 独立重复",
                "run_mode": RunMode.AUTO_DRAFT,
                "stage": ProjectStage.PLANNED,
                "current_script_version_id": script.id,
                "current_bible_version_id": bible.id,
                "current_manifest_id": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        document = ProjectDocument(
            project=project,
            script_versions=[script],
            bible_versions=[bible],
            assets=assets,
            shots=shots,
            execution_plan=execution_plan,
            reference_plan=reference_plan,
            manifests=[],
            reference_packs=[],
            voice_references=voice_references,
            repair_history=inherited_repairs,
            autonomous_runs=[run],
        )
        with self._lock:
            self.repository.save_project(document)
        return self.advance_autonomous_run(
            project_id,
            run.id,
            AutonomousRunAdvanceRequest(max_actions=1),
        )

    def _create_planned_project(
        self,
        request: CreateProjectRequest,
    ) -> tuple[ProjectAggregate, ProductionPlanningResult]:
        client = self._configured_language_client()
        planning = create_production_plan(request, client)
        project_id = f"project-{uuid4().hex[:12]}"
        created_at = utc_now()
        document = build_project_document_from_plan(
            project_id=project_id,
            request=request,
            planning=planning,
            created_at=created_at,
        )
        with self._lock:
            self.repository.save_project(document)
            return self._aggregate(document), planning

    def _configured_language_client(self) -> LanguageProviderClient | None:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        return (
            self.language_provider_factory(api_key, base_url)
            if api_key or is_loopback_openai_url(base_url)
            else None
        )

    def _import_creative_input_images(
        self,
        project_id: str,
        request: CreateProjectRequest,
    ) -> None:
        imported_versions: list[tuple[str, str]] = []
        for index, image in enumerate(request.input_images, start=1):
            source_name = (
                Path(image.local_path).stem
                if image.local_path is not None
                else f"网络参考图 {index}"
            )
            created = self.create_asset(
                project_id,
                AssetCreateRequest(
                    kind=AssetKind.REFERENCE,
                    name=f"输入视觉种子 {index} · {source_name}"[:120],
                    descriptor=image.descriptor,
                ),
            )
            if image.local_path is not None:
                imported = self.import_local_asset_version(
                    project_id,
                    created.asset.id,
                    AssetImportLocalRequest(
                        local_path=image.local_path,
                        descriptor=image.descriptor,
                    ),
                )
            else:
                assert image.external_url is not None
                imported = self.import_url_asset_version(
                    project_id,
                    created.asset.id,
                    AssetImportUrlRequest(
                        external_url=image.external_url,
                        descriptor=image.descriptor,
                        media_type=AssetMediaType.IMAGE,
                    ),
                )
            self.review_asset_version(
                project_id,
                created.asset.id,
                imported.version.id,
                AssetReviewRequest(
                    decision=AssetReviewDecision.APPROVED,
                    notes="用户直接提供的输入图片，自动草稿可作为视觉种子使用。",
                ),
            )
            imported_versions.append((imported.version.id, image.descriptor))
        if imported_versions:
            with self._lock:
                document = self._require_project_document(project_id)
                requirement = (
                    "REQUIRED"
                    if request.source_mode == SourceMode.IMAGE_SEEDED
                    else "OPTIONAL"
                )
                for plan in document.reference_plan:
                    known_targets = {binding.target_id for binding in plan.bindings}
                    for version_id, descriptor in imported_versions:
                        if version_id in known_targets:
                            continue
                        plan.bindings.append(
                            ReferenceBinding(
                                kind=AssetKind.REFERENCE.value,
                                target_id=version_id,
                                purpose=f"用户提供的跨镜视觉种子：{descriptor}",
                                requirement=requirement,
                            )
                        )
                document.project.updated_at = utc_now()
                self.repository.save_project(document)

    def _require_autonomous_run(
        self,
        project_id: str,
        run_id: str,
    ) -> AutonomousRunRecord:
        document = self._require_project_document(project_id)
        run = next((item for item in document.autonomous_runs if item.id == run_id), None)
        if run is None:
            raise ArtifactNotFoundError(run_id)
        return run

    def _advance_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        *,
        completed_step: AutonomousRunStep,
        next_step: AutonomousRunStep,
    ) -> AutonomousRunRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next((item for item in document.autonomous_runs if item.id == run_id), None)
            if run is None:
                raise ArtifactNotFoundError(run_id)
            if completed_step not in run.completed_steps:
                run.completed_steps.append(completed_step)
            run.status = AutonomousRunStatus.RUNNING
            run.current_step = next_step
            run.error_message = None
            run.waiting_reason = None
            run.updated_at = utc_now()
            self.repository.save_project(document)
            return run

    def _finish_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        *,
        status: AutonomousRunStatus,
        error_message: str | None = None,
        export_id: str | None = None,
        completed_steps: list[AutonomousRunStep] | None = None,
    ) -> AutonomousRunRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next((item for item in document.autonomous_runs if item.id == run_id), None)
            if run is None:
                raise ArtifactNotFoundError(run_id)
            for step in completed_steps or []:
                if step not in run.completed_steps:
                    run.completed_steps.append(step)
            run.status = status
            run.error_message = error_message
            run.waiting_reason = error_message if status == AutonomousRunStatus.NEEDS_ATTENTION else None
            run.export_id = export_id or run.export_id
            now = utc_now()
            run.updated_at = now
            if status == AutonomousRunStatus.COMPLETE:
                run.current_step = AutonomousRunStep.COMPLETE
                if AutonomousRunStep.COMPLETE not in run.completed_steps:
                    run.completed_steps.append(AutonomousRunStep.COMPLETE)
                run.completed_at = now
            self.repository.save_project(document)
            return run

    def advance_autonomous_run(
        self,
        project_id: str,
        run_id: str,
        request: AutonomousRunAdvanceRequest,
    ) -> AutonomousDraftResponse:
        run = self._require_autonomous_run(project_id, run_id)
        if run.execution_target != AutonomousExecutionTarget.REAL_H3:
            raise WorkflowConflictError("Only a REAL_H3 autonomous run uses resumable advance")
        if run.status != AutonomousRunStatus.COMPLETE:
            with self._lock:
                document = self._require_project_document(project_id)
                persisted = next(
                    item for item in document.autonomous_runs if item.id == run_id
                )
                persisted.status = AutonomousRunStatus.RUNNING
                persisted.error_message = None
                persisted.waiting_reason = None
                persisted.updated_at = utc_now()
                self.repository.save_project(document)
        try:
            for _ in range(request.max_actions):
                progressed, waiting = self._advance_real_autonomous_run_once(
                    project_id,
                    run_id,
                )
                run = self._require_autonomous_run(project_id, run_id)
                if run.status in {
                    AutonomousRunStatus.COMPLETE,
                    AutonomousRunStatus.NEEDS_ATTENTION,
                    AutonomousRunStatus.FAILED,
                }:
                    break
                if waiting or not progressed:
                    break
        except (
            WorkflowConflictError,
            ProviderOperationError,
            MediaGenerationError,
        ) as exc:
            run = self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.NEEDS_ATTENTION,
                error_message=str(exc),
            )
        except Exception as exc:
            run = self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.FAILED,
                error_message=str(exc),
            )
            raise
        else:
            run = self._require_autonomous_run(project_id, run_id)
        export = next(
            (
                item
                for item in self.repository.list_exports(project_id)
                if item.id == run.export_id
            ),
            None,
        )
        return AutonomousDraftResponse(
            project=self.get_project(project_id),
            export=export,
            run=run,
        )

    def _advance_real_autonomous_run_once(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.current_step == AutonomousRunStep.PREPARE_ASSETS:
            return self._advance_base_asset_preparation(project_id, run_id)
        if run.current_step == AutonomousRunStep.PLAN_ROUTES:
            route_document = self._require_project_document(project_id)
            wave_shot_ids = _next_h3_continuity_wave(
                route_document,
                self.repository.list_takes(project_id),
            )
            if not wave_shot_ids:
                self._advance_autonomous_run(
                    project_id,
                    run_id,
                    completed_step=AutonomousRunStep.PLAN_ROUTES,
                    next_step=AutonomousRunStep.TIMELINE,
                )
                return True, False
            response = self.compile_h3_production_route_plan(
                project_id,
                H3ProductionRoutePlanRequest(
                    shot_ids=wave_shot_ids,
                    use_language_agent=True,
                    candidate_count=2,
                    consider_video_guides=True,
                ),
            )
            needs_asset = any(
                decision.readiness == H3ShotRouteReadiness.NEEDS_ASSET
                for decision in response.plan.decisions
            )
            with self._lock:
                document = self._require_project_document(project_id)
                persisted = next(
                    item for item in document.autonomous_runs if item.id == run_id
                )
                persisted.route_plan_id = response.plan.id
                if AutonomousRunStep.PLAN_ROUTES not in persisted.completed_steps:
                    persisted.completed_steps.append(AutonomousRunStep.PLAN_ROUTES)
                persisted.current_step = (
                    AutonomousRunStep.PREPARE_SHOT_ASSETS
                    if needs_asset
                    else AutonomousRunStep.GENERATE
                )
                persisted.waiting_reason = None
                persisted.updated_at = utc_now()
                self.repository.save_project(document)
            return True, False
        if run.current_step == AutonomousRunStep.PREPARE_SHOT_ASSETS:
            return self._advance_shot_asset_preparation(project_id, run_id)
        if run.current_step == AutonomousRunStep.GENERATE:
            return self._advance_real_video_generation(project_id, run_id)
        if run.current_step == AutonomousRunStep.QA_AND_REPAIR:
            return self._advance_real_qa_and_repair(project_id, run_id)
        if run.current_step == AutonomousRunStep.TIMELINE:
            self.rebuild_manifest(project_id)
            if run.render_export:
                self._advance_autonomous_run(
                    project_id,
                    run_id,
                    completed_step=AutonomousRunStep.TIMELINE,
                    next_step=AutonomousRunStep.EXPORT,
                )
            else:
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.COMPLETE,
                    completed_steps=[AutonomousRunStep.TIMELINE],
                )
            return True, False
        if run.current_step == AutonomousRunStep.AUDIO_QA:
            # Backward-compatible resume for runs persisted before automatic audio QA
            # was removed. Voice/audio inputs remain supported; audio inspection is manual.
            if run.render_export:
                self._advance_autonomous_run(
                    project_id,
                    run_id,
                    completed_step=AutonomousRunStep.AUDIO_QA,
                    next_step=AutonomousRunStep.EXPORT,
                )
            else:
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.COMPLETE,
                    completed_steps=[AutonomousRunStep.AUDIO_QA],
                )
            return True, False
        if run.current_step == AutonomousRunStep.EXPORT:
            export = self.export_project(project_id, rebuild_manifest=False)
            self._finish_autonomous_run(
                project_id,
                run_id,
                status=AutonomousRunStatus.COMPLETE,
                export_id=export.id,
                completed_steps=[AutonomousRunStep.EXPORT],
            )
            return True, False
        if run.current_step == AutonomousRunStep.COMPLETE:
            return False, False
        raise WorkflowConflictError(
            f"REAL_H3 run cannot advance from {run.current_step.value}"
        )

    def _advance_base_asset_preparation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        document = self._require_project_document(project_id)
        core_assets = [
            asset
            for asset in document.assets
            if asset.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
        ]
        for asset in core_assets:
            approved = self._approved_image_version(asset)
            if approved is not None:
                if self._rebind_asset_references(document, asset, approved):
                    document.project.updated_at = utc_now()
                    self.repository.save_project(document)
                    return True, False
                continue
            imported_candidate = next(
                (
                    version
                    for version in reversed(asset.versions)
                    if version.media_type == AssetMediaType.IMAGE
                    and version.image_task_id is None
                    and version.approval_status == ApprovalStatus.DRAFT
                    and (
                        version.local_path is not None
                        or version.external_url is not None
                    )
                ),
                None,
            )
            if imported_candidate is not None:
                _review, approved_by_agent = self._review_generated_asset(
                    project_id=project_id,
                    asset_id=asset.id,
                    version_id=imported_candidate.id,
                )
                if approved_by_agent:
                    refreshed = self._require_project_document(project_id)
                    refreshed_asset = self._require_asset_in_document(
                        refreshed,
                        asset.id,
                    )
                    approved_version = self._approved_image_version(refreshed_asset)
                    assert approved_version is not None
                    self._rebind_asset_references(
                        refreshed,
                        refreshed_asset,
                        approved_version,
                    )
                    refreshed.project.updated_at = utc_now()
                    self.repository.save_project(refreshed)
                return True, False
            tasks = self.asset_workbench.list_image_tasks(project_id, asset.id)
            latest = max(tasks, key=lambda item: item.created_at, default=None)
            if latest is None:
                if self._try_catalog_asset_reuse(document, asset):
                    return True, False
                self._enqueue_base_asset_image(document, asset, None)
                return True, False
            if latest.status in {ImageTaskStatus.QUEUED, ImageTaskStatus.SUBMITTING}:
                self.advance_image_task(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.OUTPUT_MISSING:
                self.recover_image_task_output(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.SUBMIT_UNKNOWN:
                raise WorkflowConflictError(
                    f"Image task {latest.id} has an unknown submission result; reconcile it before continuing"
                )
            if latest.status == ImageTaskStatus.SUCCEEDED:
                version = self._find_asset_version(
                    self._require_project_document(project_id),
                    latest.output_version_id,
                )[1]
                if version.approval_status == ApprovalStatus.DRAFT:
                    _review, approved_by_agent = self._review_generated_asset(
                        project_id=project_id,
                        asset_id=asset.id,
                        version_id=version.id,
                    )
                    if approved_by_agent:
                        refreshed = self._require_project_document(project_id)
                        refreshed_asset = self._require_asset_in_document(refreshed, asset.id)
                        approved_version = self._approved_image_version(refreshed_asset)
                        assert approved_version is not None
                        self._rebind_asset_references(
                            refreshed,
                            refreshed_asset,
                            approved_version,
                        )
                        refreshed.project.updated_at = utc_now()
                        self.repository.save_project(refreshed)
                    return True, False
                if version.approval_status == ApprovalStatus.REJECTED:
                    if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                        raise WorkflowConflictError(
                            f"Automatic visual asset retries exhausted for {asset.name}"
                        )
                    self._enqueue_base_asset_image(document, asset, latest)
                    return True, False
            if latest.status in {ImageTaskStatus.FAILED, ImageTaskStatus.CANCELLED}:
                if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                    raise WorkflowConflictError(
                        f"Image task retries exhausted for {asset.name}: {latest.error_message or latest.status.value}"
                    )
                self._enqueue_base_asset_image(document, asset, latest)
                return True, False
        now = utc_now()
        if ensure_base_reference_pack_entries(document, now=now):
            document.project.updated_at = now
            self.repository.save_project(document)
            return True, False
        reference_result = self._advance_reference_pack_preparation(project_id, run_id)
        if reference_result is not None:
            return reference_result
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.PREPARE_ASSETS,
            next_step=AutonomousRunStep.PLAN_ROUTES,
        )
        return True, False

    def _enqueue_base_asset_image(
        self,
        document: ProjectDocument,
        asset: Asset,
        previous: ImageGenerationTask | None,
    ) -> ImageGenerationTask:
        prompt = build_base_asset_prompt(document, asset)
        if previous is not None:
            try:
                previous_version = self._find_asset_version(
                    self._require_project_document(document.project.id),
                    previous.output_version_id,
                )[1]
            except ArtifactNotFoundError:
                previous_version = None
            if previous_version is not None and previous_version.review_notes:
                prompt += (
                    "\n\n上一候选未通过制片检查。仅修复以下问题，不改变资产身份："
                    + previous_version.review_notes
                )
        descriptor = next(
            (
                version.descriptor
                for version in asset.versions
                if version.media_type == AssetMediaType.TEXT_REFERENCE
            ),
            asset.versions[-1].descriptor,
        )
        try:
            return self.asset_workbench.enqueue_image_task(
                document.project.id,
                asset.id,
                ImageTaskCreateRequest(
                    operation=ImageOperation.GENERATE,
                    prompt=prompt,
                    descriptor=descriptor,
                ),
                attempt=(previous.attempt + 1 if previous is not None else 1),
                retry_of_task_id=previous.id if previous is not None else None,
            )
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(
                "请先在资产页配置 GPT Image 2 的 URL 和 API Key，再继续真人自动草稿。"
            ) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def _try_catalog_asset_reuse(self, document: ProjectDocument, asset: Asset) -> bool:
        canonical_name = self._canonical_asset_name(asset.name)
        if not canonical_name:
            return False
        canonical_roles = {
            AssetKind.CHARACTER: [ReferenceRole.CHARACTER_BODY],
            AssetKind.LOCATION: [ReferenceRole.LOCATION_MASTER],
            AssetKind.PROP: [ReferenceRole.PROP_PRODUCT],
        }.get(asset.kind, [])
        if not canonical_roles:
            return False
        tried_sources = {
            version.source.source_uri
            for version in asset.versions
            if version.source is not None and version.source.source_uri is not None
        }
        response = search_asset_catalog(
            self.repository.list_projects(),
            current_project_id=document.project.id,
            request=AssetCatalogSearchRequest(
                query=asset.name,
                kinds=[asset.kind],
                roles=canonical_roles,
                include_current_project=False,
                limit=20,
            ),
        )
        candidate = next(
            (
                item
                for item in response.candidates
                if self._canonical_asset_name(item.subject_asset_name) == canonical_name
                and (
                    f"short-drama://projects/{item.source_project_id}/assets/"
                    f"{item.subject_asset_id}/versions/{item.asset_version_id}"
                )
                not in tried_sources
            ),
            None,
        )
        if candidate is None:
            return False
        source_document = self._require_project_document(candidate.source_project_id)
        source_asset, source_version = self._find_asset_version(
            source_document,
            candidate.asset_version_id,
        )
        self.asset_workbench.clone_catalog_image(
            document.project.id,
            asset.id,
            source_project_id=source_document.project.id,
            source_asset_id=source_asset.id,
            source_asset_name=source_asset.name,
            source_version=source_version,
            descriptor=(
                f"复用资产库中的已批准母版候选：{source_version.descriptor}"
            ),
        )
        return True

    @staticmethod
    def _canonical_asset_name(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    def _advance_reference_pack_preparation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool] | None:
        document = self._require_project_document(project_id)
        run = self._require_autonomous_run(project_id, run_id)
        for need in missing_reference_views(document):
            key = self._reference_view_key(need)
            skip_prefix = f"{_REFERENCE_VIEW_SKIP_PREFIX}{key}:"
            if any(warning.startswith(skip_prefix) for warning in run.warnings):
                continue
            subject = self._require_asset_in_document(
                document,
                need.subject_asset_id,
            )
            root = self._find_asset_version(
                document,
                need.lineage_root_version_id,
            )[1]
            marker = f"{_REFERENCE_VIEW_MARKER_PREFIX}{key}"
            target = next(
                (
                    asset
                    for asset in document.assets
                    if asset.kind == AssetKind.REFERENCE
                    and any(marker in version.descriptor for version in asset.versions)
                ),
                None,
            )
            descriptor = self._reference_view_descriptor(subject, need, marker)
            if target is None:
                created = self.create_asset(
                    project_id,
                    AssetCreateRequest(
                        kind=AssetKind.REFERENCE,
                        name=f"{subject.name}·{need.role.value}",
                        descriptor=descriptor,
                    ),
                )
                self.asset_workbench.clone_catalog_image(
                    project_id,
                    created.asset.id,
                    source_project_id=project_id,
                    source_asset_id=subject.id,
                    source_asset_name=subject.name,
                    source_version=root,
                    descriptor=descriptor,
                )
                return True, False

            approved = self._approved_image_version(target)
            if approved is not None:
                self._register_reference_view(project_id, subject, approved, need)
                return True, False

            tasks = self.asset_workbench.list_image_tasks(project_id, target.id)
            latest = max(tasks, key=lambda item: item.created_at, default=None)
            if latest is None:
                self._enqueue_reference_view_image(
                    document,
                    target,
                    subject,
                    need,
                    previous=None,
                )
                return True, False
            if latest.status in {ImageTaskStatus.QUEUED, ImageTaskStatus.SUBMITTING}:
                self.advance_image_task(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.OUTPUT_MISSING:
                self.recover_image_task_output(latest.id)
                return True, False
            if latest.status == ImageTaskStatus.SUBMIT_UNKNOWN:
                raise WorkflowConflictError(
                    f"Image task {latest.id} has an unknown submission result; reconcile it before continuing"
                )
            if latest.status == ImageTaskStatus.SUCCEEDED:
                refreshed = self._require_project_document(project_id)
                output = self._find_asset_version(
                    refreshed,
                    latest.output_version_id,
                )[1]
                if output.approval_status == ApprovalStatus.DRAFT:
                    refreshed_subject = self._require_asset_in_document(
                        refreshed,
                        subject.id,
                    )
                    refreshed_root = self._find_asset_version(
                        refreshed,
                        need.lineage_root_version_id,
                    )[1]
                    _review, approved_by_agent = self._review_generated_asset(
                        project_id=project_id,
                        asset_id=target.id,
                        version_id=output.id,
                        canonical_references_override=[
                            (refreshed_subject, refreshed_root)
                        ],
                    )
                    if approved_by_agent:
                        self._register_reference_view(
                            project_id,
                            refreshed_subject,
                            self._find_asset_version(
                                self._require_project_document(project_id),
                                output.id,
                            )[1],
                            need,
                        )
                    return True, False
                if output.approval_status == ApprovalStatus.APPROVED:
                    self._register_reference_view(project_id, subject, output, need)
                    return True, False
                if output.approval_status == ApprovalStatus.REJECTED:
                    if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                        self._skip_reference_view(
                            project_id,
                            run_id,
                            key,
                            subject,
                            latest.error_message or output.review_notes or "review rejected",
                        )
                        return True, False
                    self._enqueue_reference_view_image(
                        document,
                        target,
                        subject,
                        need,
                        previous=latest,
                    )
                    return True, False
            if latest.status in {ImageTaskStatus.FAILED, ImageTaskStatus.CANCELLED}:
                if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                    self._skip_reference_view(
                        project_id,
                        run_id,
                        key,
                        subject,
                        latest.error_message or latest.status.value,
                    )
                    return True, False
                self._enqueue_reference_view_image(
                    document,
                    target,
                    subject,
                    need,
                    previous=latest,
                )
                return True, False
        return None

    @staticmethod
    def _reference_view_key(need: ReferenceViewNeed) -> str:
        payload = json.dumps(
            {
                "subject": need.subject_asset_id,
                "role": need.role.value,
                "angle": need.angle.value,
                "framing": need.framing.value,
                "tags": need.tags,
                "root": need.lineage_root_version_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _reference_view_descriptor(
        subject: Asset,
        need: ReferenceViewNeed,
        marker: str,
    ) -> str:
        tags = "、".join(need.tags) if need.tags else "无额外标签"
        return (
            f"{marker}\n从 {subject.name} 当前母版派生的同源参考视图；"
            f"角色={need.role.value}；角度={need.angle.value}；"
            f"景别={need.framing.value}；标签={tags}；用途={need.purpose}"
        )

    def _enqueue_reference_view_image(
        self,
        document: ProjectDocument,
        target: Asset,
        subject: Asset,
        need: ReferenceViewNeed,
        *,
        previous: ImageGenerationTask | None,
    ) -> ImageGenerationTask:
        refreshed = self._require_project_document(document.project.id)
        refreshed_target = self._require_asset_in_document(refreshed, target.id)
        seed = next(
            (
                version
                for version in reversed(refreshed_target.versions)
                if version.media_type == AssetMediaType.IMAGE
                and version.image_task_id is None
                and (
                    version.local_path is not None
                    or version.external_url is not None
                )
            ),
            None,
        )
        if seed is None:
            raise WorkflowConflictError("Derived reference asset has no canonical seed image")
        prompt = build_reference_view_prompt(refreshed, subject, need)
        if previous is not None:
            try:
                previous_output = self._find_asset_version(
                    refreshed,
                    previous.output_version_id,
                )[1]
            except ArtifactNotFoundError:
                previous_output = None
            if previous_output is not None and previous_output.review_notes:
                prompt += (
                    "\n\n上一候选未通过制片检查。仅修复以下问题，"
                    "不改变母版身份或空间几何："
                    + previous_output.review_notes
                )
        try:
            return self.asset_workbench.enqueue_image_task(
                refreshed.project.id,
                refreshed_target.id,
                ImageTaskCreateRequest(
                    operation=ImageOperation.EDIT,
                    prompt=prompt,
                    descriptor=self._reference_view_descriptor(
                        subject,
                        need,
                        f"{_REFERENCE_VIEW_MARKER_PREFIX}{self._reference_view_key(need)}",
                    ),
                    parent_version_id=seed.id,
                    input_version_ids=[seed.id],
                    primary_input_version_id=seed.id,
                ),
                attempt=(previous.attempt + 1 if previous is not None else 1),
                retry_of_task_id=previous.id if previous is not None else None,
            )
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(
                "请先在资产页配置 GPT Image 2 的 URL 和 API Key，再继续参考视图派生。"
            ) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def _register_reference_view(
        self,
        project_id: str,
        subject: Asset,
        approved: AssetVersion,
        need: ReferenceViewNeed,
    ) -> None:
        shown_shot_ids = need.shot_ids[:8]
        shot_summary = "、".join(shown_shot_ids)
        if len(need.shot_ids) > len(shown_shot_ids):
            shot_summary += f" 等（共 {len(need.shot_ids)} 镜）"
        self.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=subject.id,
                asset_version_id=approved.id,
                lineage_root_version_id=need.lineage_root_version_id,
                role=need.role,
                angle=need.angle,
                framing=need.framing,
                tags=need.tags,
                description=(
                    f"同源母版派生视图，用于镜头 {shot_summary}：{need.purpose}"
                )[:2000],
                priority=80,
            ),
        )

    def _skip_reference_view(
        self,
        project_id: str,
        run_id: str,
        key: str,
        subject: Asset,
        reason: str,
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next((item for item in document.autonomous_runs if item.id == run_id), None)
            if run is None:
                raise ArtifactNotFoundError(run_id)
            warning = (
                f"{_REFERENCE_VIEW_SKIP_PREFIX}{key}: {subject.name} 的可选派生视图"
                f"未在限定重试内收敛，已继续使用母版与现有参考；{reason}"
            )
            if warning not in run.warnings:
                run.warnings.append(warning)
            run.updated_at = utc_now()
            document.project.updated_at = run.updated_at
            self.repository.save_project(document)

    def _advance_shot_asset_preparation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        document = self._require_project_document(project_id)
        for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
            if decision.readiness != H3ShotRouteReadiness.NEEDS_ASSET:
                if self._refresh_rejected_keyframe_fallback(document, decision):
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_plan(plan)
                    return True, False
                continue
            shot = self._require_shot_in_document(document, decision.shot_id)
            for preparation in decision.asset_preparations:
                if preparation.approved_version_id is not None:
                    continue
                if preparation.asset_id is None:
                    self._create_keyframe_asset(document, plan, decision, preparation)
                    return True, False
                tasks = self.asset_workbench.list_image_tasks(
                    project_id,
                    preparation.asset_id,
                )
                latest = max(tasks, key=lambda item: item.created_at, default=None)
                if latest is None:
                    task = self._enqueue_keyframe_image(
                        document,
                        shot,
                        preparation,
                        None,
                    )
                    preparation.image_task_ids.append(task.id)
                    preparation.status = H3RouteAssetPreparationStatus.GENERATING
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_plan(plan)
                    return True, False
                if latest.status in {ImageTaskStatus.QUEUED, ImageTaskStatus.SUBMITTING}:
                    self.advance_image_task(latest.id)
                    return True, False
                if latest.status == ImageTaskStatus.OUTPUT_MISSING:
                    self.recover_image_task_output(latest.id)
                    return True, False
                if latest.status == ImageTaskStatus.SUBMIT_UNKNOWN:
                    raise WorkflowConflictError(
                        f"Keyframe task {latest.id} has an unknown submission result"
                    )
                if latest.status == ImageTaskStatus.SUCCEEDED:
                    version = self._find_asset_version(
                        self._require_project_document(project_id),
                        latest.output_version_id,
                    )[1]
                    if version.approval_status == ApprovalStatus.DRAFT:
                        review, approved_by_agent = self._review_generated_asset(
                            project_id=project_id,
                            asset_id=preparation.asset_id,
                            version_id=version.id,
                            shot=shot,
                            preparation=preparation,
                        )
                        preparation.review_summary = review.summary
                        if approved_by_agent:
                            preparation.status = H3RouteAssetPreparationStatus.READY
                            preparation.approved_version_id = version.id
                            media_input = self._h3_image_input(
                                self._require_project_document(project_id),
                                version.id,
                                preparation.role,
                            )
                            decision.media_inputs = [
                                item
                                for item in decision.media_inputs
                                if item.role != preparation.role
                            ] + [media_input]
                            decision.missing_inputs = [
                                item
                                for item in decision.missing_inputs
                                if item != preparation.role.value
                            ]
                            decision.warnings = [
                                item
                                for item in decision.warnings
                                if "needs an approved start frame" not in item
                            ]
                        else:
                            preparation.status = H3RouteAssetPreparationStatus.REJECTED
                        plan.updated_at = utc_now()
                        self.repository.save_h3_route_plan(plan)
                        return True, False
                    if version.approval_status == ApprovalStatus.REJECTED:
                        if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                            if self._fallback_failed_keyframe_preparation(
                                document=self._require_project_document(project_id),
                                plan=plan,
                                decision=decision,
                                failed_preparation=preparation,
                                reason=(
                                    f"Automatic keyframe review rejected {latest.attempt} "
                                    "candidate(s)."
                                ),
                            ):
                                return True, False
                            raise WorkflowConflictError(
                                f"Automatic keyframe retries exhausted for shot {shot.sequence}"
                            )
                        task = self._enqueue_keyframe_image(
                            document,
                            shot,
                            preparation,
                            latest,
                        )
                        preparation.image_task_ids.append(task.id)
                        preparation.status = H3RouteAssetPreparationStatus.GENERATING
                        plan.updated_at = utc_now()
                        self.repository.save_h3_route_plan(plan)
                        return True, False
                if latest.status in {ImageTaskStatus.FAILED, ImageTaskStatus.CANCELLED}:
                    if latest.attempt >= _MAX_AUTONOMOUS_IMAGE_ATTEMPTS:
                        if self._fallback_failed_keyframe_preparation(
                            document=self._require_project_document(project_id),
                            plan=plan,
                            decision=decision,
                            failed_preparation=preparation,
                            reason=(
                                f"Keyframe provider task failed or was cancelled "
                                f"{latest.attempt} time(s)."
                            ),
                        ):
                            return True, False
                        raise WorkflowConflictError(
                            f"Keyframe task retries exhausted for shot {shot.sequence}"
                        )
                    task = self._enqueue_keyframe_image(
                        document,
                        shot,
                        preparation,
                        latest,
                    )
                    preparation.image_task_ids.append(task.id)
                    preparation.status = H3RouteAssetPreparationStatus.GENERATING
                    plan.updated_at = utc_now()
                    self.repository.save_h3_route_plan(plan)
                    return True, False
            if all(
                item.approved_version_id is not None
                for item in decision.asset_preparations
            ):
                decision.readiness = (
                    H3ShotRouteReadiness.READY_WITH_WARNINGS
                    if decision.warnings
                    else H3ShotRouteReadiness.READY
                )
                plan.updated_at = utc_now()
                self.repository.save_h3_route_plan(plan)
                return True, False
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.PREPARE_SHOT_ASSETS,
            next_step=AutonomousRunStep.GENERATE,
        )
        plan.status = H3RoutePlanStatus.READY
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)
        return True, False

    def _fallback_failed_keyframe_preparation(
        self,
        *,
        document: ProjectDocument,
        plan: H3ProductionRoutePlan,
        decision: H3ShotRoutePlan,
        failed_preparation: H3RouteAssetPreparation,
        reason: str,
    ) -> bool:
        built = self._build_keyframe_fallback_inputs(
            document,
            decision,
            failed_preparation,
        )
        if built is None:
            return False
        bound, media_inputs = built

        failed_preparation.input_version_ids = [
            version.id for _asset, version in bound
        ]
        failed_preparation.status = H3RouteAssetPreparationStatus.REJECTED
        fallback_warning = (
            f"{reason} The advisory keyframe route was replaced with approved visual "
            "references so AUTO_DRAFT can continue; the user may override this route."
        )
        failed_preparation.review_summary = " ".join(
            item
            for item in [failed_preparation.review_summary, fallback_warning]
            if item
        )
        decision.route = VideoGenerationRoute.REFERENCE
        decision.media_inputs = [
            item
            for item in decision.media_inputs
            if item.role != H3MediaRole.REFERENCE_IMAGE
        ] + media_inputs
        decision.missing_inputs = []
        decision.readiness = H3ShotRouteReadiness.READY_WITH_WARNINGS
        decision.rationale = (
            "Shot-specific keyframe preparation did not converge within the bounded automatic "
            "retries. Continue with approved references for subjects the reference plan expects "
            "to be visible, while retaining stricter continuity QA; this is an overridable "
            "Director fallback."
        )
        decision.warnings = list(
            dict.fromkeys(
                [
                    *(
                        warning
                        for warning in decision.warnings
                        if "needs an approved start frame" not in warning.lower()
                        and "起始帧" not in warning
                    ),
                    fallback_warning,
                ]
            )
        )
        plan.status = (
            H3RoutePlanStatus.READY
            if all(
                item.readiness
                in {
                    H3ShotRouteReadiness.READY,
                    H3ShotRouteReadiness.READY_WITH_WARNINGS,
                }
                for item in plan.decisions
            )
            else H3RoutePlanStatus.PARTIAL
        )
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)
        return True

    def _build_keyframe_fallback_inputs(
        self,
        document: ProjectDocument,
        decision: H3ShotRoutePlan,
        failed_preparation: H3RouteAssetPreparation,
    ) -> tuple[list[tuple[Asset, AssetVersion]], list[H3MediaInput]] | None:
        reference_plan = self._require_reference_plan(document, decision.shot_id)
        bound = _scope_keyframe_reference_versions(
            document,
            self._approved_bound_image_versions(document, decision.shot_id),
            reference_plan,
            failed_preparation.input_version_ids,
        )
        media_inputs = [
            self._h3_image_input(
                document,
                version.id,
                H3MediaRole.REFERENCE_IMAGE,
            )
            for _asset, version in bound[:9]
        ]
        if not media_inputs:
            return None
        try:
            validate_h3_probe(
                route=VideoGenerationRoute.REFERENCE,
                prompt=decision.prompt,
                duration_seconds=decision.duration_seconds,
                media_inputs=media_inputs,
            )
        except H3CapabilityError:
            return None
        return bound, media_inputs

    def _refresh_rejected_keyframe_fallback(
        self,
        document: ProjectDocument,
        decision: H3ShotRoutePlan,
    ) -> bool:
        """Reconcile persisted fallbacks after the Agent's reference selection evolves."""

        if decision.route != VideoGenerationRoute.REFERENCE:
            return False
        failed_preparation = next(
            (
                item
                for item in decision.asset_preparations
                if item.status == H3RouteAssetPreparationStatus.REJECTED
            ),
            None,
        )
        if failed_preparation is None:
            return False
        built = self._build_keyframe_fallback_inputs(
            document,
            decision,
            failed_preparation,
        )
        if built is None:
            return False
        bound, media_inputs = built
        new_version_ids = [version.id for _asset, version in bound]
        current_reference_ids = [
            item.asset_version_id
            for item in decision.media_inputs
            if item.role == H3MediaRole.REFERENCE_IMAGE
            and item.asset_version_id is not None
        ]
        if (
            current_reference_ids == new_version_ids
            and failed_preparation.input_version_ids == new_version_ids
        ):
            return False
        failed_preparation.input_version_ids = new_version_ids
        decision.media_inputs = [
            item
            for item in decision.media_inputs
            if item.role != H3MediaRole.REFERENCE_IMAGE
        ] + media_inputs
        return True

    def _create_keyframe_asset(
        self,
        document: ProjectDocument,
        plan: H3ProductionRoutePlan,
        decision: H3ShotRoutePlan,
        preparation: H3RouteAssetPreparation,
    ) -> None:
        reference_plan = self._require_reference_plan(document, decision.shot_id)
        shot = self._require_shot_in_document(document, decision.shot_id)
        bound = _scope_keyframe_reference_versions(
            document,
            self._approved_bound_image_versions(document, decision.shot_id),
            reference_plan,
            preparation.input_version_ids,
        )
        if not bound:
            raise WorkflowConflictError(
                f"Shot {decision.shot_sequence} has no approved visual base for keyframe editing"
            )
        ordered = _prioritize_keyframe_canvas(
            _order_keyframe_reference_versions(
                bound,
                reference_plan,
                preparation.input_version_ids,
            ),
            shot,
        )
        primary_asset, primary_version = ordered[0]
        preparation.input_version_ids = [
            version.id for _asset, version in ordered[:16]
        ]
        created = self.create_asset(
            document.project.id,
            AssetCreateRequest(
                kind=AssetKind.REFERENCE,
                name=preparation.name,
                descriptor=preparation.descriptor,
            ),
        )
        if primary_version.local_path is not None:
            _seeded = self.import_local_asset_version(
                document.project.id,
                created.asset.id,
                AssetImportLocalRequest(
                    local_path=primary_version.local_path,
                    descriptor=(
                        f"镜头关键帧构图父图，来源于 {primary_asset.name} 的已批准版本"
                    ),
                ),
            )
        elif primary_version.external_url is not None:
            _seeded = self.import_url_asset_version(
                document.project.id,
                created.asset.id,
                AssetImportUrlRequest(
                    external_url=primary_version.external_url,
                    descriptor=(
                        f"镜头关键帧构图父图，来源于 {primary_asset.name} 的已批准版本"
                    ),
                    media_type=AssetMediaType.IMAGE,
                ),
            )
        else:
            raise WorkflowConflictError("Approved keyframe base has no usable media")
        preparation.asset_id = created.asset.id
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)

    def _enqueue_keyframe_image(
        self,
        document: ProjectDocument,
        shot: ShotPlan,
        preparation: H3RouteAssetPreparation,
        previous: ImageGenerationTask | None,
    ) -> ImageGenerationTask:
        if preparation.asset_id is None:
            raise WorkflowConflictError("Keyframe preparation has no target asset")
        refreshed = self._require_project_document(document.project.id)
        target = self._require_asset_in_document(refreshed, preparation.asset_id)
        parent = next(
            (
                item
                for item in reversed(target.versions)
                if item.media_type == AssetMediaType.IMAGE
                and item.image_task_id is None
            ),
            None,
        )
        if parent is None:
            raise WorkflowConflictError("Keyframe asset has no visual parent")
        reference_plan = self._require_reference_plan(refreshed, shot.id)
        bound = _scope_keyframe_reference_versions(
            refreshed,
            self._approved_bound_image_versions(refreshed, shot.id),
            reference_plan,
            preparation.input_version_ids,
        )
        ordered = _prioritize_keyframe_canvas(
            _order_keyframe_reference_versions(
                bound,
                reference_plan,
                preparation.input_version_ids,
            ),
            shot,
        )
        if not ordered:
            raise WorkflowConflictError("Keyframe asset has no approved visual references")
        primary_asset, primary_version = ordered[0]
        input_ids = list(
            dict.fromkeys(
                [
                    parent.id,
                    *(
                        version.id
                        for _asset, version in ordered
                        if version.id != primary_version.id
                    ),
                ]
            )
        )[:16]
        reference_lines = [
            (
                "Image 1: PRIMARY EDIT INPUT copied exactly from the approved "
                f"{primary_asset.kind.value} reference '{primary_asset.name}'. "
                + self._keyframe_primary_reference_lock(primary_asset.kind)
            ),
            *[
                (
                    f"Image {index}: approved {asset.kind.value} reference "
                    f"'{asset.name}'; use it only to preserve that subject's visible "
                    "identity, appearance, geometry, or topology where requested by the shot."
                )
                for index, (asset, _version) in enumerate(
                    (
                        item
                        for item in ordered
                        if item[1].id != primary_version.id
                    ),
                    start=2,
                )
            ],
        ]
        prompt = (
            build_keyframe_correction_prompt(
                preparation,
                preparation.review_summary if previous is not None else None,
            )
            + "\n\nACTUAL EDIT INPUT MAP:\n"
            + "\n".join(reference_lines)
            + "\nDo not blend different actors or replace the primary subject with a face "
            "from another reference. Reference-sheet or turnaround layouts must be transformed "
            "into the single cinematic frame requested above, never reproduced as a collage."
        )
        try:
            return self.asset_workbench.enqueue_image_task(
                refreshed.project.id,
                target.id,
                ImageTaskCreateRequest(
                    operation=ImageOperation.EDIT,
                    prompt=prompt,
                    descriptor=preparation.descriptor,
                    parent_version_id=parent.id,
                    input_version_ids=input_ids,
                    primary_input_version_id=parent.id,
                ),
                attempt=(previous.attempt + 1 if previous is not None else 1),
                retry_of_task_id=previous.id if previous is not None else None,
            )
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(
                "请先在资产页配置 GPT Image 2 的 URL 和 API Key，再继续关键帧准备。"
            ) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def _review_generated_asset(
        self,
        *,
        project_id: str,
        asset_id: str,
        version_id: str,
        shot: ShotPlan | None = None,
        preparation: H3RouteAssetPreparation | None = None,
        canonical_references_override: list[tuple[Asset, AssetVersion]] | None = None,
    ) -> tuple[SemanticAssetReview, bool]:
        document = self._require_project_document(project_id)
        asset = self._require_asset_in_document(document, asset_id)
        version = self._find_asset_version(document, version_id)[1]
        client = self._require_language_client()
        canonical_references = list(canonical_references_override or [])
        if shot is not None:
            reference_plan = self._require_reference_plan(document, shot.id)
            preferred_version_ids = (
                preparation.input_version_ids if preparation is not None else []
            )
            canonical_references = _prioritize_keyframe_canvas(
                _order_keyframe_reference_versions(
                    _scope_keyframe_reference_versions(
                        document,
                        self._approved_bound_image_versions(document, shot.id),
                        reference_plan,
                        preferred_version_ids,
                    ),
                    reference_plan,
                    preferred_version_ids,
                ),
                shot,
            )
        try:
            review = inspect_asset_image(
                document=document,
                asset=asset,
                version=version,
                client=client,
                shot=shot,
                preparation=preparation,
                canonical_references=canonical_references,
            )
        except (LanguageProviderError, ValueError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        severe = any(
            issue.severity.upper() in {"ERROR", "HIGH", "MAJOR", "CRITICAL", "FATAL"}
            for issue in review.issues
        )
        approved = review.recommended and review.score >= 3.5 and not severe
        issue_text = "; ".join(
            f"{item.code}:{item.message}" for item in review.issues
        )
        notes = (
            f"AUTO_DRAFT GPT-5.6-sol visual review score={review.score:.1f}; "
            f"{review.summary}"
            + (f"; issues={issue_text}" if issue_text else "")
        )
        self.review_asset_version(
            project_id,
            asset_id,
            version_id,
            AssetReviewRequest(
                decision=(
                    AssetReviewDecision.APPROVED
                    if approved
                    else AssetReviewDecision.REJECTED
                ),
                notes=notes,
            ),
        )
        return review, approved

    @staticmethod
    def _keyframe_primary_reference_lock(kind: AssetKind) -> str:
        if kind == AssetKind.CHARACTER:
            return (
                "Preserve the exact face identity, hair, wardrobe, and body appearance; "
                "change only pose, crop, camera, and scene composition required by the shot."
            )
        if kind == AssetKind.LOCATION:
            return (
                "Preserve the location's topology, fixed anchors, materials, and lighting logic; "
                "change only the camera framing required by the shot."
            )
        if kind == AssetKind.PROP:
            return (
                "Preserve the prop's exact shape, color, component placement, and material; "
                "change only pose and placement required by the shot."
            )
        return "Preserve its approved visual facts while applying only the requested shot change."

    def _advance_real_video_generation(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        has_unexecuted = any(
            decision.execution_status == H3ShotRouteExecutionStatus.PLANNED
            for decision in plan.decisions
        )
        if has_unexecuted:
            self.execute_h3_production_route_plan(
                project_id,
                plan.id,
                H3ProductionRouteExecuteRequest(
                    reuse_passed_takes=True,
                    enqueue_new_generations=True,
                    inspect_reused_takes=False,
                    semantic_review=False,
                    rebuild_manifest=False,
                    render_export=False,
                ),
            )
            return True, False
        plan = self.repository.get_h3_route_plan(plan.id) or plan
        all_project_tasks = self.repository.list_tasks(project_id)
        retry_children: dict[str, list[GenerationTask]] = {}
        for task in all_project_tasks:
            if task.retry_of_task_id is not None:
                retry_children.setdefault(task.retry_of_task_id, []).append(task)
        plan_changed = False
        for decision in plan.decisions:
            planned_ids = set(decision.task_ids)
            for parent_id in list(decision.task_ids):
                children = retry_children.get(parent_id, [])
                if not children:
                    continue
                earliest = min(children, key=lambda item: (item.created_at, item.id))
                if earliest.id not in planned_ids:
                    decision.task_ids.append(earliest.id)
                    planned_ids.add(earliest.id)
                    plan_changed = True
        if plan_changed:
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        task_ids = [task_id for item in plan.decisions for task_id in item.task_ids]
        tasks = [self._require_task(task_id) for task_id in task_ids]
        pending_submission = next(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.QUEUED, TaskStatus.SUBMITTING}
            ),
            None,
        )
        if pending_submission is not None:
            self.advance_task(pending_submission.id)
            return True, False

        missing_output = next(
            (task for task in tasks if task.status == TaskStatus.OUTPUT_MISSING),
            None,
        )
        if missing_output is not None:
            self.recover_task_output(missing_output.id)
            return True, False

        unknown_submission = next(
            (task for task in tasks if task.status == TaskStatus.SUBMIT_UNKNOWN),
            None,
        )
        if unknown_submission is not None:
            raise WorkflowConflictError(
                f"H3 task {unknown_submission.id} submission is unknown; reconcile it before continuing"
            )

        failed = next(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                and task.id not in retry_children
            ),
            None,
        )
        if failed is not None:
            if failed.attempt >= 2:
                raise WorkflowConflictError(
                    f"H3 retries exhausted for shot {failed.shot_id}: "
                    f"{failed.error_message or failed.status.value}"
                )
            retry = self.retry_task(failed.id)
            decision = next(
                item for item in plan.decisions if item.shot_id == failed.shot_id
            )
            decision.task_ids.append(retry.id)
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        running = min(
            (
                task
                for task in tasks
                if task.status in {TaskStatus.ACKNOWLEDGED, TaskStatus.RUNNING}
            ),
            key=lambda item: (item.updated_at, item.created_at, item.id),
            default=None,
        )
        if running is not None:
            advanced = self.advance_task(running.id)
            waiting = advanced.status in {
                TaskStatus.ACKNOWLEDGED,
                TaskStatus.RUNNING,
            }
            if waiting:
                self._set_autonomous_waiting(
                    project_id,
                    run_id,
                    "MiniMax-H3 tasks are still running; the desktop will continue polling.",
                )
            return True, waiting
        self._advance_autonomous_run(
            project_id,
            run_id,
            completed_step=AutonomousRunStep.GENERATE,
            next_step=AutonomousRunStep.QA_AND_REPAIR,
        )
        return True, False

    def _advance_real_qa_and_repair(
        self,
        project_id: str,
        run_id: str,
    ) -> tuple[bool, bool]:
        run = self._require_autonomous_run(project_id, run_id)
        if run.route_plan_id is None:
            raise WorkflowConflictError("REAL_H3 run has no production route plan")
        plan = self.repository.get_h3_route_plan(run.route_plan_id)
        if plan is None:
            raise ArtifactNotFoundError(run.route_plan_id)
        project = self.get_project(project_id)
        task_ids = {task_id for item in plan.decisions for task_id in item.task_ids}
        pending = sorted(
            (
                take
                for take in project.takes
                if take.task_id in task_ids
                and take.freshness == Freshness.CURRENT
                and take.qa_status == QaStatus.PENDING
            ),
            key=lambda item: next(
                shot.sequence
                for shot in project.document.shots
                if shot.id == item.shot_id
            ),
        )
        if pending:
            take = pending[0]
            decision = next(item for item in plan.decisions if item.shot_id == take.shot_id)
            existing = next(
                (
                    report
                    for report in reversed(project.video_inspections)
                    if report.take_id == take.id
                    and report.semantic_status == SemanticReviewStatus.COMPLETE
                ),
                None,
            )
            if existing is None:
                response = self.inspect_take_video(
                    take.id,
                    VideoInspectionRequest(
                        include_previous_boundary=True,
                        semantic_review=True,
                        max_frames=decision.inspection.max_frames,
                        review_focus=decision.inspection.review_focus,
                    ),
                )
                report = response.report
            else:
                report = existing
            if report.semantic_status != SemanticReviewStatus.COMPLETE:
                refreshed = self.get_project(project_id)
                failed_attempts = sum(
                    1
                    for item in refreshed.video_inspections
                    if item.take_id == take.id
                    and item.semantic_status
                    in {
                        SemanticReviewStatus.FAILED,
                        SemanticReviewStatus.UNAVAILABLE,
                    }
                )
                if failed_attempts < _MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS:
                    self._set_autonomous_waiting(
                        project_id,
                        run_id,
                        (
                            f"Semantic review for Take {take.id} was temporarily unavailable; "
                            f"AUTO_DRAFT will retry ({failed_attempts}/"
                            f"{_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS})."
                        ),
                    )
                    return True, True
                raise WorkflowConflictError(
                    f"Semantic video review remained unavailable for Take {take.id} "
                    f"after {_MAX_AUTONOMOUS_SEMANTIC_REVIEW_ATTEMPTS} attempts"
                )
            self._apply_automatic_h3_review(take, report)
            self._select_best_current_takes(project_id)
            decision.execution_status = H3ShotRouteExecutionStatus.GENERATED
            if report.id not in decision.inspection_ids:
                decision.inspection_ids.append(report.id)
            decision.inspection_issue_codes = list(
                dict.fromkeys(
                    [
                        *decision.inspection_issue_codes,
                        *(item.code for item in report.issues),
                    ]
                )
            )
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            return True, False

        self._select_best_current_takes(project_id)
        aggregate = self.get_project(project_id)
        planned_shot_ids = {item.shot_id for item in plan.decisions}
        missing = _missing_timeline_shot_ids(
            aggregate.document,
            aggregate.takes,
            restrict_to=planned_shot_ids,
        )
        if not missing:
            self._complete_h3_wave_or_episode(
                project_id=project_id,
                run_id=run_id,
                plan=plan,
                aggregate=aggregate,
            )
            return True, False
        if run.repair_round >= run.max_repair_rounds:
            waived_take_ids = self._apply_bounded_best_effort_waivers(
                project_id,
                run_id,
                missing,
            )
            if waived_take_ids:
                self._select_best_current_takes(project_id)
                refreshed = self.get_project(project_id)
                missing = _missing_timeline_shot_ids(
                    refreshed.document,
                    refreshed.takes,
                    restrict_to=planned_shot_ids,
                )
                if not missing:
                    self._complete_h3_wave_or_episode(
                        project_id=project_id,
                        run_id=run_id,
                        plan=plan,
                        aggregate=refreshed,
                    )
                    return True, False
            raise WorkflowConflictError(
                "REAL_H3 AUTO_DRAFT exhausted Director repair rounds for: "
                + ", ".join(missing)
            )
        repaired = self._run_autonomous_director_repairs(project_id, missing)
        if not repaired:
            raise WorkflowConflictError(
                "REAL_H3 AUTO_DRAFT needs an executable Director repair for: "
                + ", ".join(missing)
            )
        with self._lock:
            plan.status = H3RoutePlanStatus.PARTIAL
            plan.updated_at = utc_now()
            self.repository.save_h3_route_plan(plan)
            document = self._require_project_document(project_id)
            persisted = next(item for item in document.autonomous_runs if item.id == run_id)
            persisted.repair_round += 1
            persisted.route_plan_id = None
            persisted.current_step = AutonomousRunStep.PLAN_ROUTES
            persisted.waiting_reason = None
            persisted.updated_at = utc_now()
            self.repository.save_project(document)
        return True, False

    def _complete_h3_wave_or_episode(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: H3ProductionRoutePlan,
        aggregate: ProjectAggregate,
    ) -> None:
        plan.status = H3RoutePlanStatus.COMPLETE
        plan.updated_at = utc_now()
        self.repository.save_h3_route_plan(plan)
        remaining = _missing_timeline_shot_ids(
            aggregate.document,
            aggregate.takes,
        )
        if not remaining:
            self._advance_autonomous_run(
                project_id,
                run_id,
                completed_step=AutonomousRunStep.QA_AND_REPAIR,
                next_step=AutonomousRunStep.TIMELINE,
            )
            return
        with self._lock:
            document = self._require_project_document(project_id)
            persisted = next(
                item for item in document.autonomous_runs if item.id == run_id
            )
            persisted.route_plan_id = None
            persisted.current_step = AutonomousRunStep.PLAN_ROUTES
            persisted.waiting_reason = None
            persisted.updated_at = utc_now()
            self.repository.save_project(document)

    def _apply_automatic_h3_review(
        self,
        take: Take,
        report: VideoInspectionReport,
    ) -> None:
        evidence = self.repository.get_route_evidence_for_take(take.id)
        if evidence is None:
            raise WorkflowConflictError(f"Route evidence for Take {take.id} is missing")
        severe = [
            issue
            for issue in report.issues
            if issue.severity.upper()
            in {"ERROR", "HIGH", "MAJOR", "CRITICAL", "FATAL"}
        ]
        passed = evidence.technical_passed is True and not severe
        medium_count = sum(
            issue.severity.upper() in {"WARN", "WARNING", "MEDIUM", "MODERATE"}
            for issue in report.issues
        )
        low_count = sum(
            issue.severity.upper() in {"LOW", "MINOR"} for issue in report.issues
        )
        score = max(
            0.0,
            min(
                5.0,
                4.6 - len(severe) * 1.2 - medium_count * 0.45 - low_count * 0.2,
            ),
        )
        issues = list(report.issues)
        if not passed and not issues:
            issues.append(
                QaIssue(
                    code="AUTOMATED_TECHNICAL_GATE_FAILED",
                    severity="ERROR",
                    message="Provider media did not pass the stored technical gate.",
                    repair_layer="PROVIDER_RETRY",
                    category=QaFailureCategory.TECHNICAL_MEDIA,
                )
            )
        notes = (
            f"AUTO_DRAFT visual candidate judge: {report.summary} "
            "Audio-stream presence was checked here; transcript, expected-speaker metadata, "
            "cross-shot voice continuity, signal quality, and subtitle timing are evaluated by "
            "the post-timeline AudioInspection step. User override remains allowed."
        )
        self.review_h3_take(
            take.id,
            H3TakeReviewRequest(
                hard_gate_passed=passed,
                score=score,
                notes=notes,
                issues=issues,
            ),
        )

    def _set_autonomous_waiting(
        self,
        project_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next(item for item in document.autonomous_runs if item.id == run_id)
            run.status = AutonomousRunStatus.RUNNING
            run.waiting_reason = reason
            run.updated_at = utc_now()
            self.repository.save_project(document)

    def _require_language_client(self) -> LanguageProviderClient:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError(
                "GPT-5.6-sol is required for autonomous visual asset and video review"
            )
        return self.language_provider_factory(api_key, base_url)

    @staticmethod
    def _approved_image_version(asset: Asset) -> AssetVersion | None:
        if asset.approved_version_id is None:
            return None
        return next(
            (
                item
                for item in asset.versions
                if item.id == asset.approved_version_id
                and item.approval_status == ApprovalStatus.APPROVED
                and item.media_type == AssetMediaType.IMAGE
                and (item.local_path is not None or item.external_url is not None)
            ),
            None,
        )

    @staticmethod
    def _require_approved_voice_audio(asset: Asset) -> AssetVersion:
        if asset.kind != AssetKind.REFERENCE or asset.approved_version_id is None:
            raise WorkflowConflictError(
                "A character voice reference needs an approved REFERENCE audio asset"
            )
        version = next(
            (
                item
                for item in asset.versions
                if item.id == asset.approved_version_id
                and item.approval_status == ApprovalStatus.APPROVED
                and item.media_type == AssetMediaType.AUDIO
                and (item.local_path is not None or item.external_url is not None)
            ),
            None,
        )
        if version is None:
            raise WorkflowConflictError(
                "A character voice reference needs an approved audio version"
            )
        if version.local_path is not None:
            path = Path(version.local_path).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in {".wav", ".mp3"}:
                raise WorkflowConflictError(
                    "Local MiniMax-H3 voice references must be available WAV or MP3 files"
                )
            if path.stat().st_size > 15 * 1024 * 1024:
                raise WorkflowConflictError(
                    "MiniMax-H3 voice reference exceeds 15 MB"
                )
        return version

    @staticmethod
    def _rebind_asset_references(
        document: ProjectDocument,
        asset: Asset,
        approved: AssetVersion,
    ) -> bool:
        version_ids = {item.id for item in asset.versions}
        changed = False
        for plan in document.reference_plan:
            for binding in plan.bindings:
                if binding.target_id in version_ids and binding.target_id != approved.id:
                    binding.target_id = approved.id
                    changed = True
        return changed

    def _approved_bound_image_versions(
        self,
        document: ProjectDocument,
        shot_id: str,
    ) -> list[tuple[Asset, AssetVersion]]:
        plan = self._require_reference_plan(document, shot_id)
        result: list[tuple[Asset, AssetVersion]] = []
        seen: set[str] = set()
        shot = self._require_shot_in_document(document, shot_id)
        preview = preview_reference_plan(
            document,
            shot,
            plan,
            ReferencePlanPreviewRequest(),
        )
        for selection in preview.selected:
            try:
                asset, version = self._find_asset_version(
                    document,
                    selection.asset_version_id,
                )
            except ArtifactNotFoundError:
                continue
            if (
                version.id in seen
                or version.approval_status != ApprovalStatus.APPROVED
                or version.media_type != AssetMediaType.IMAGE
                or (version.local_path is None and version.external_url is None)
            ):
                continue
            seen.add(version.id)
            result.append((asset, version))
        for binding in plan.bindings:
            try:
                asset, version = self._find_asset_version(document, binding.target_id)
            except ArtifactNotFoundError:
                continue
            if (
                version.id in seen
                or version.approval_status != ApprovalStatus.APPROVED
                or version.media_type != AssetMediaType.IMAGE
                or (version.local_path is None and version.external_url is None)
            ):
                continue
            seen.add(version.id)
            result.append((asset, version))
        if result:
            return result
        for asset in document.assets:
            version = self._approved_image_version(asset)
            if version is not None and version.id not in seen:
                seen.add(version.id)
                result.append((asset, version))
        return result

    def _h3_image_input(
        self,
        document: ProjectDocument,
        version_id: str,
        role: H3MediaRole,
    ) -> H3MediaInput:
        _asset, version = self._find_asset_version(document, version_id)
        if version.local_path is not None:
            source = H3MediaSource(local_path=version.local_path)
        elif version.external_url is not None:
            source = H3MediaSource(external_url=version.external_url)
        else:
            raise WorkflowConflictError(f"AssetVersion {version.id} has no usable media")
        return H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=role,
            source=source,
            asset_version_id=version.id,
        )

    def _prepared_h3_media_inputs(
        self,
        document: ProjectDocument,
        plans: list[H3ProductionRoutePlan],
    ) -> dict[str, list[H3MediaInput]]:
        shot_revisions = {item.id: item.revision for item in document.shots}
        result: dict[str, list[H3MediaInput]] = {}
        claimed: set[tuple[str, H3MediaRole]] = set()
        for plan in plans:
            for decision in plan.decisions:
                if shot_revisions.get(decision.shot_id) != decision.input_shot_revision:
                    continue
                for preparation in decision.asset_preparations:
                    key = (decision.shot_id, preparation.role)
                    if (
                        key in claimed
                        or preparation.status != H3RouteAssetPreparationStatus.READY
                        or preparation.approved_version_id is None
                    ):
                        continue
                    try:
                        media = self._h3_image_input(
                            document,
                            preparation.approved_version_id,
                            preparation.role,
                        )
                    except (ArtifactNotFoundError, WorkflowConflictError):
                        continue
                    result.setdefault(decision.shot_id, []).append(media)
                    claimed.add(key)
        return result

    def delete_project(self, project_id: str) -> bool:
        return self.repository.delete_project(project_id)

    def get_project(self, project_id: str) -> ProjectAggregate:
        document = self._require_project_document(project_id)
        return self._aggregate(document)

    def get_h3_config(self) -> H3ProviderConfigResponse:
        return self.h3_config.response()

    def get_image_provider_config(self) -> ImageProviderConfigResponse:
        return self.asset_workbench.get_config()

    def update_image_provider_config(
        self,
        request: ImageProviderConfigUpdate,
    ) -> ImageProviderConfigResponse:
        return self.asset_workbench.update_config(request)

    def validate_image_provider_credentials(self) -> ImageProviderConfigResponse:
        try:
            return self.asset_workbench.validate_credentials()
        except AssetProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except ImageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc

    def get_language_provider_config(self) -> LanguageProviderConfigResponse:
        return self.language_config.response()

    def update_language_provider_config(
        self,
        request: LanguageProviderConfigUpdate,
    ) -> LanguageProviderConfigResponse:
        return self.language_config.update(request)

    def validate_language_provider_credentials(self) -> LanguageProviderConfigResponse:
        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        try:
            client = self.language_provider_factory(
                api_key,
                base_url,
            )
            client.validate_credentials()
        except LanguageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return self.language_config.response()

    def compile_h3_prompt_with_language_model(
        self,
        project_id: str,
        request: LanguagePromptCompileRequest,
    ) -> LanguagePromptCompileResponse:
        document = self._require_project_document(project_id)
        shot = next((item for item in document.shots if item.id == request.shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(request.shot_id)
        duration = request.duration_seconds or math.ceil(shot.duration_seconds)
        source_prompt = (
            request.source_prompt.strip()
            if request.source_prompt is not None
            else build_h3_prompt(document, shot)
        )
        if not source_prompt:
            raise WorkflowConflictError("H3 source prompt is empty")
        media_inputs = self._resolve_approved_asset_inputs(document, request.media_inputs)
        if media_inputs:
            try:
                validate_h3_probe(
                    route=request.route,
                    prompt=source_prompt,
                    duration_seconds=duration,
                    media_inputs=media_inputs,
                )
            except H3CapabilityError as exc:
                raise WorkflowConflictError(str(exc)) from exc

        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        instructions, input_text = build_language_prompt_input(
            document,
            shot,
            route=request.route,
            duration_seconds=duration,
            source_prompt=source_prompt,
            media_inputs=media_inputs,
        )
        try:
            client = self.language_provider_factory(api_key, base_url)
            compiled_prompt = client.generate_text(
                instructions=instructions,
                input_text=input_text,
            )
            validation_notes = validate_language_prompt(
                compiled_prompt,
                document,
                shot,
                duration_seconds=duration,
            )
        except (LanguageProviderError, LanguagePromptValidationError) as exc:
            raise ProviderOperationError(str(exc)) from exc
        return LanguagePromptCompileResponse(
            shot_id=shot.id,
            route=request.route,
            duration_seconds=duration,
            source_prompt=source_prompt,
            compiled_prompt=compiled_prompt,
            validation_notes=validation_notes,
        )

    def add_reference_pack_entry(
        self,
        project_id: str,
        request: ReferencePackEntryCreateRequest,
    ) -> ReferencePackEntryMutationResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            subject = self._require_asset_in_document(document, request.subject_asset_id)
            if subject.kind not in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}:
                raise WorkflowConflictError(
                    "Reference packs must belong to a character, location, or prop asset"
                )
            if not role_is_compatible(subject.kind, request.role):
                raise WorkflowConflictError(
                    f"Reference role {request.role.value} is incompatible with {subject.kind.value}"
                )
            _source_asset, source_version = self._find_asset_version(
                document,
                request.asset_version_id,
            )
            if (
                _source_asset.approved_version_id != source_version.id
                or source_version.approval_status != ApprovalStatus.APPROVED
            ):
                raise WorkflowConflictError(
                    "Only an asset's current approved formal version can enter a reference pack"
                )
            if source_version.media_type != AssetMediaType.IMAGE:
                raise WorkflowConflictError("Reference pack entries currently require image media")
            if request.lineage_root_version_id is not None:
                _root_asset, root_version = self._find_asset_version(
                    document,
                    request.lineage_root_version_id,
                )
                if (
                    root_version.media_type != AssetMediaType.IMAGE
                    or (
                        root_version.local_path is None
                        and root_version.external_url is None
                    )
                ):
                    raise WorkflowConflictError(
                        "Reference-pack lineage root must be a usable image"
                    )

            now = utc_now()
            pack_id = f"reference-pack-{subject.id}"
            pack = next(
                (item for item in document.reference_packs if item.id == pack_id),
                None,
            )
            if pack is None:
                pack = ReferencePack(
                    id=pack_id,
                    project_id=project_id,
                    subject_asset_id=subject.id,
                    name=f"{subject.name}参考包",
                    created_at=now,
                    updated_at=now,
                )
                document.reference_packs.append(pack)

            normalized_tags = list(
                dict.fromkeys(tag.strip() for tag in request.tags if tag.strip())
            )
            existing = next(
                (
                    item
                    for item in pack.entries
                    if item.asset_version_id == source_version.id
                    and item.role == request.role
                    and item.angle == request.angle
                    and item.framing == request.framing
                ),
                None,
            )
            if existing is None:
                entry = ReferencePackEntry(
                    id=f"reference-entry-{uuid4().hex[:12]}",
                    asset_version_id=source_version.id,
                    lineage_root_version_id=request.lineage_root_version_id,
                    role=request.role,
                    angle=request.angle,
                    framing=request.framing,
                    tags=normalized_tags,
                    description=request.description.strip(),
                    priority=request.priority,
                )
                pack.entries.append(entry)
            else:
                existing.lineage_root_version_id = request.lineage_root_version_id
                existing.tags = normalized_tags
                existing.description = request.description.strip()
                existing.priority = request.priority
                entry = existing
            pack.updated_at = now
            document.project.updated_at = now
            self.repository.save_project(document)
            return ReferencePackEntryMutationResponse(
                pack=pack,
                entry=entry,
                project=self._aggregate(document),
            )

    def update_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            shot = self._require_shot_in_document(document, shot_id)
            plan = self._require_reference_plan(document, shot_id)
            before = plan.model_dump(mode="json")
            self._apply_reference_plan_update(document, shot_id, request)
            now = utc_now()
            if plan.model_dump(mode="json") != before:
                shot.revision += 1
                self.repository.mark_takes_stale(project_id, [shot_id])
                self._invalidate_current_manifests(document)
                document.project.stage = ProjectStage.PLANNED
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def preview_shot_reference_plan(
        self,
        project_id: str,
        shot_id: str,
        request: ReferencePlanPreviewRequest,
    ) -> ReferencePlanPreviewResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            return preview_reference_plan(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def compile_keyframe_brief(
        self,
        project_id: str,
        shot_id: str,
        request: KeyframeBriefCompileRequest,
    ) -> KeyframeBriefCompileResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            return compile_keyframe_brief(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def get_director_advice(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorAdviceResponse:
        document = self._require_project_document(project_id)
        shot = self._require_shot_in_document(document, shot_id)
        plan = self._require_reference_plan(document, shot_id)
        try:
            preview = preview_reference_plan(document, shot, plan, request)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

        shot_takes = self.repository.list_takes_for_shot(project_id, shot_id)
        if request.take_id is not None:
            selected_take = next(
                (take for take in shot_takes if take.id == request.take_id),
                None,
            )
            if selected_take is None:
                raise ArtifactNotFoundError(request.take_id)
            qa_takes = [selected_take]
        else:
            qa_takes = [
                take for take in shot_takes if take.qa_status == QaStatus.REJECTED
            ][-5:]
        qa_issues = [
            issue.model_copy(deep=True)
            for take in qa_takes
            for issue in take.qa_report.issues
        ]
        reviewer_notes = [
            f"{take.id}: {take.qa_report.reviewer_notes}"
            for take in qa_takes
            if take.qa_report.reviewer_notes
        ]
        if request.inspection_id is not None:
            inspection = self.repository.get_video_inspection(request.inspection_id)
            if (
                inspection is None
                or inspection.project_id != project_id
                or inspection.shot_id != shot_id
            ):
                raise ArtifactNotFoundError(request.inspection_id)
            if request.take_id is not None and inspection.take_id != request.take_id:
                raise WorkflowConflictError(
                    "Director advice Take and video inspection do not refer to the same output"
                )
            qa_issues.extend(issue.model_copy(deep=True) for issue in inspection.issues)
            reviewer_notes.append(
                f"{inspection.id}: {inspection.summary}"
            )
        if request.audio_inspection_id is not None:
            audio_inspection = self.repository.get_audio_inspection(
                request.audio_inspection_id
            )
            if (
                audio_inspection is None
                or audio_inspection.project_id != project_id
            ):
                raise ArtifactNotFoundError(request.audio_inspection_id)
            shot_clip_inspections = [
                item
                for item in audio_inspection.clip_inspections
                if item.shot_id == shot_id
            ]
            shot_dialogue_checks = [
                item
                for item in audio_inspection.dialogue_checks
                if item.shot_id == shot_id
            ]
            if not shot_clip_inspections and not shot_dialogue_checks:
                raise WorkflowConflictError(
                    "Director advice audio inspection has no evidence for this shot"
                )
            if request.take_id is not None and not any(
                item.take_id == request.take_id
                for item in [*shot_clip_inspections, *shot_dialogue_checks]
            ):
                raise WorkflowConflictError(
                    "Director advice Take and audio inspection do not refer to the same output"
                )
            qa_issues.extend(
                issue.model_copy(deep=True)
                for item in shot_clip_inspections
                for issue in item.issues
            )
            qa_issues.extend(
                issue.model_copy(deep=True)
                for item in shot_dialogue_checks
                for issue in item.issues
            )
            relevant_speakers = {
                item.expected_speaker for item in shot_dialogue_checks
            }
            qa_issues.extend(
                issue.model_copy(deep=True)
                for issue in audio_inspection.issues
                if issue.code == "VOICE_CONTINUITY_REVIEW"
                and any(speaker in issue.message for speaker in relevant_speakers)
            )
            reviewer_notes.append(
                f"{audio_inspection.id}: {audio_inspection.summary}"
            )
        capability_profile = build_h3_capability_profile(
            self.repository.list_takes(project_id),
            project_id=project_id,
        )
        prior_repairs = [
            record.model_copy(deep=True)
            for record in document.repair_history
            if record.decision == DirectorRepairDecision.APPLIED
            and (
                record.source_shot_id == shot_id
                or shot_id in record.affected_shot_ids
            )
        ]

        api_key = self.language_config.api_key()
        base_url = self.language_config.base_url()
        if not api_key and not is_loopback_openai_url(base_url):
            raise WorkflowConflictError("GPT-5.6-sol API key is not configured")
        instructions, input_text = build_director_advice_input(
            document,
            shot,
            plan,
            preview,
            request,
            qa_issues,
            reviewer_notes,
            capability_profile,
            prior_repairs,
        )
        try:
            client = self.language_provider_factory(api_key, base_url)
            output_text = client.generate_text(
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS,
            )
        except LanguageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        try:
            payload = parse_director_advice(output_text)
        except ValueError as first_error:
            repair_instructions, repair_input = build_director_advice_repair_input(
                original_input=input_text,
                invalid_output=output_text,
                validation_error=first_error,
            )
            try:
                repaired_output = client.generate_text(
                    instructions=repair_instructions,
                    input_text=repair_input,
                    max_output_tokens=_DIRECTOR_ADVICE_MAX_OUTPUT_TOKENS,
                )
                payload = parse_director_advice(repaired_output)
            except (LanguageProviderError, ValueError) as repair_error:
                raise ProviderOperationError(
                    "Director Agent JSON and one automatic repair were invalid: "
                    f"first={first_error}; repair={repair_error}"
                ) from repair_error
        return DirectorAdviceResponse(
            shot_id=shot_id,
            advisories=payload.advisories,
            repair_options=payload.repair_options,
            recommended_strategy=payload.recommended_strategy,
            proposed_repair=payload.proposed_repair,
            capability_profile_version=capability_profile.version,
            qa_issue_codes=list(dict.fromkeys(issue.code for issue in qa_issues)),
        )

    def get_h3_capability_profile(
        self,
        project_id: str,
    ) -> H3CapabilityProfileResponse:
        self._require_project_document(project_id)
        return build_h3_capability_profile(
            self.repository.list_takes(project_id),
            project_id=project_id,
        )

    def compile_h3_production_route_plan(
        self,
        project_id: str,
        request: H3ProductionRoutePlanRequest,
    ) -> H3ProductionRoutePlanResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            if refresh_lightweight_reference_plans(document):
                document.project.updated_at = utc_now()
                self.repository.save_project(document)
        language_client = None
        if request.use_language_agent:
            api_key = self.language_config.api_key()
            base_url = self.language_config.base_url()
            if api_key or is_loopback_openai_url(base_url):
                language_client = self.language_provider_factory(api_key, base_url)
        existing = self.repository.list_h3_route_plans(project_id)
        takes = self.repository.list_takes(project_id)
        created_at = utc_now()
        prepared_media_inputs = self._prepared_h3_media_inputs(
            document,
            existing,
        )
        video_guides = {}
        video_guide_warnings: list[str] = []
        if request.consider_video_guides:
            video_guides, video_guide_warnings = self.h3_video_guide_builder.prepare(
                document=document,
                takes=takes,
                shot_ids=request.shot_ids,
                created_at=created_at,
                prepared_media_inputs=prepared_media_inputs,
            )
        try:
            plan = build_h3_production_route_plan(
                document=document,
                tasks=self.repository.list_tasks(project_id),
                takes=takes,
                evidence=self.repository.list_route_evidence(project_id),
                provider_runs=self.repository.list_provider_runs(project_id),
                inspections=self.repository.list_video_inspections(project_id),
                pricing=self.h3_config.pricing_snapshot(),
                version=max((item.version for item in existing), default=0) + 1,
                created_at=created_at,
                shot_ids=request.shot_ids,
                candidate_count=request.candidate_count,
                language_client=language_client,
                prepared_media_inputs=prepared_media_inputs,
                video_guides=video_guides,
                video_guide_warnings=video_guide_warnings,
            )
        except RoutePlanningError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self.repository.save_h3_route_plan(plan)
        return H3ProductionRoutePlanResponse(plan=plan)

    def list_h3_production_route_plans(
        self,
        project_id: str,
    ) -> H3ProductionRoutePlanListResponse:
        self._require_project_document(project_id)
        return H3ProductionRoutePlanListResponse(
            plans=self.repository.list_h3_route_plans(project_id)
        )

    def execute_h3_production_route_plan(
        self,
        project_id: str,
        plan_id: str,
        request: H3ProductionRouteExecuteRequest,
    ) -> H3ProductionRouteExecuteResponse:
        inspection_take_ids: list[str] = []
        queued_tasks: list[GenerationTask] = []
        with self._lock:
            document = self._require_project_document(project_id)
            plan = self.repository.get_h3_route_plan(plan_id)
            if plan is None or plan.project_id != project_id:
                raise ArtifactNotFoundError(plan_id)
            try:
                validate_route_plan_freshness(plan, document)
            except RoutePlanningError as exc:
                plan.status = H3RoutePlanStatus.STALE
                plan.updated_at = utc_now()
                self.repository.save_h3_route_plan(plan)
                raise WorkflowConflictError(str(exc)) from exc

            needs_provider_submission = any(
                item.action == H3ShotRouteAction.GENERATE
                and item.readiness
                not in {
                    H3ShotRouteReadiness.NEEDS_ASSET,
                    H3ShotRouteReadiness.UNSUPPORTED,
                }
                for item in plan.decisions
            )
            if (
                request.enqueue_new_generations
                and needs_provider_submission
                and not self.h3_config.api_key()
            ):
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing production H3 tasks"
                )
            for decision in sorted(plan.decisions, key=lambda item: item.shot_sequence):
                if decision.action == H3ShotRouteAction.REUSE_TAKE:
                    if not request.reuse_passed_takes or decision.reuse_take_id is None:
                        decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                        continue
                    self.select_take(decision.reuse_take_id)
                    decision.execution_status = H3ShotRouteExecutionStatus.REUSED
                    if request.inspect_reused_takes:
                        inspection_take_ids.append(decision.reuse_take_id)
                    continue

                if (
                    decision.readiness
                    in {H3ShotRouteReadiness.NEEDS_ASSET, H3ShotRouteReadiness.UNSUPPORTED}
                    or not request.enqueue_new_generations
                ):
                    decision.execution_status = H3ShotRouteExecutionStatus.SKIPPED
                    continue
                shot = self._require_shot_in_document(document, decision.shot_id)
                media_inputs = self._resolve_approved_asset_inputs(
                    document,
                    decision.media_inputs,
                )
                try:
                    validate_h3_probe(
                        route=decision.route,
                        prompt=decision.prompt,
                        duration_seconds=decision.duration_seconds,
                        media_inputs=media_inputs,
                    )
                except H3CapabilityError as exc:
                    raise WorkflowConflictError(str(exc)) from exc
                existing_tasks = self.repository.list_tasks_for_shot(project_id, shot.id)
                next_candidate = max(
                    (item.candidate_index for item in existing_tasks),
                    default=0,
                ) + 1
                task_ids: list[str] = []
                for offset in range(decision.candidate_count):
                    task, run, route_evidence = self._new_h3_task_and_run(
                        document=document,
                        shot_id=shot.id,
                        route=decision.route,
                        duration_seconds=decision.duration_seconds,
                        prompt=decision.prompt,
                        media_inputs=media_inputs,
                        purpose=EvidencePurpose.PRODUCTION,
                        learning_objective=None,
                        regression_case_id=None,
                        candidate_index=next_candidate + offset,
                        attempt=1,
                        retry_of_task_id=None,
                    )
                    self.repository.save_task(task)
                    self.repository.save_provider_run(run)
                    self.repository.save_route_evidence(route_evidence)
                    queued_tasks.append(task)
                    task_ids.append(task.id)
                decision.task_ids = task_ids
                decision.execution_status = H3ShotRouteExecutionStatus.QUEUED

            if queued_tasks:
                document = self._require_project_document(project_id)
                document.project.stage = ProjectStage.GENERATING
                document.project.updated_at = utc_now()
                self.repository.save_project(document)
                plan.status = H3RoutePlanStatus.EXECUTING
            elif all(
                item.execution_status == H3ShotRouteExecutionStatus.REUSED
                for item in plan.decisions
            ):
                plan.status = H3RoutePlanStatus.COMPLETE
            else:
                plan.status = H3RoutePlanStatus.PARTIAL
            plan.updated_at = utc_now()
            plan.executed_at = plan.updated_at
            self.repository.save_h3_route_plan(plan)

        inspections: list[VideoInspectionReport] = []
        persisted_by_take: dict[str, VideoInspectionReport] = {}
        for report in self.repository.list_video_inspections(project_id):
            persisted_by_take[report.take_id] = report
        for take_id in inspection_take_ids:
            decision = next(
                item for item in plan.decisions if item.reuse_take_id == take_id
            )
            existing_report = persisted_by_take.get(take_id)
            if existing_report is not None and (
                not request.semantic_review
                or existing_report.semantic_status == SemanticReviewStatus.COMPLETE
            ):
                inspections.append(existing_report)
                if existing_report.id not in decision.inspection_ids:
                    decision.inspection_ids.append(existing_report.id)
                decision.inspection_issue_codes = list(
                    dict.fromkeys(
                        [
                            *decision.inspection_issue_codes,
                            *(item.code for item in existing_report.issues),
                        ]
                    )
                )
                continue
            response = self.inspect_take_video(
                take_id,
                VideoInspectionRequest(
                    include_previous_boundary=True,
                    semantic_review=request.semantic_review,
                    max_frames=decision.inspection.max_frames,
                    review_focus=decision.inspection.review_focus,
                ),
            )
            inspections.append(response.report)
            decision.inspection_ids.append(response.report.id)
            decision.inspection_issue_codes = list(
                dict.fromkeys(
                    [
                        *decision.inspection_issue_codes,
                        *(item.code for item in response.report.issues),
                    ]
                )
            )

        manifest = None
        export = None
        if request.rebuild_manifest:
            aggregate = self.get_project(project_id)
            if aggregate.progress.shots_with_selected_take == aggregate.progress.total_shots:
                manifest = self.rebuild_manifest(project_id)
        if request.render_export and manifest is not None:
            export = self.export_project(project_id, rebuild_manifest=False)

        plan.updated_at = utc_now()
        if not queued_tasks and all(
            item.execution_status == H3ShotRouteExecutionStatus.REUSED
            for item in plan.decisions
        ):
            plan.status = H3RoutePlanStatus.COMPLETE
        self.repository.save_h3_route_plan(plan)
        return H3ProductionRouteExecuteResponse(
            plan=plan,
            project=self.get_project(project_id),
            queued_tasks=queued_tasks,
            inspections=inspections,
            manifest=manifest,
            export=export,
        )

    def decide_director_repair(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorRepairDecisionRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            shot = self._require_shot_in_document(document, shot_id)
            if request.source_take_id is not None:
                source_take = self.repository.get_take(request.source_take_id)
                if (
                    source_take is None
                    or source_take.project_id != project_id
                    or source_take.shot_id != shot_id
                ):
                    raise ArtifactNotFoundError(request.source_take_id)

            now = utc_now()
            affected_shot_ids = [shot_id]
            if request.decision == DirectorRepairDecision.APPLIED:
                if request.strategy == DirectorRepairStrategy.SPLIT_SHOT:
                    affected_shot_ids = self._apply_shot_split(
                        document,
                        shot,
                        request,
                    )
                else:
                    patch = request.shot_patch
                    if patch is not None:
                        if patch.dialogue_line_ids is not None:
                            self._validate_dialogue_line_ids(
                                document,
                                patch.dialogue_line_ids,
                            )
                        for field_name in patch.model_fields_set:
                            value = getattr(patch, field_name)
                            if value is not None:
                                setattr(shot, field_name, deepcopy(value))
                    if request.reference_plan is not None:
                        self._apply_reference_plan_update(
                            document,
                            shot_id,
                            request.reference_plan,
                        )
                    shot.revision += 1
                    self.repository.mark_takes_stale(project_id, [shot_id])
                self._invalidate_current_manifests(document)
                document.project.stage = ProjectStage.PLANNED

            document.repair_history.append(
                DirectorRepairRecord(
                    id=f"director-repair-{uuid4().hex}",
                    source_shot_id=shot_id,
                    source_take_id=request.source_take_id,
                    strategy=request.strategy,
                    decision=request.decision,
                    reason=request.reason.strip(),
                    issue_codes=list(dict.fromkeys(request.issue_codes)),
                    affected_shot_ids=affected_shot_ids,
                    created_at=now,
                )
            )
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def run_director_loop(
        self,
        project_id: str,
        shot_id: str,
        request: DirectorAdviceRequest,
    ) -> DirectorLoopResponse:
        advice = self.get_director_advice(project_id, shot_id, request)
        project = self.get_project(project_id)
        if project.document.project.run_mode != RunMode.AUTO_DRAFT:
            return DirectorLoopResponse(
                advice=advice,
                repair_applied=False,
                next_action="AWAIT_USER",
                project=project,
            )
        if advice.proposed_repair is None:
            return DirectorLoopResponse(
                advice=advice,
                repair_applied=False,
                next_action="NO_CHANGE",
                project=project,
            )
        proposed = advice.proposed_repair.model_copy(
            deep=True,
            update={
                "decision": DirectorRepairDecision.APPLIED,
                "source_take_id": request.take_id,
                "issue_codes": advice.qa_issue_codes,
            },
        )
        updated = self.decide_director_repair(project_id, shot_id, proposed)
        return DirectorLoopResponse(
            advice=advice,
            repair_applied=True,
            next_action="REGENERATE_SHOT",
            project=updated,
        )

    def create_asset(
        self,
        project_id: str,
        request: AssetCreateRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.create_asset(project_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ProjectNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_local_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportLocalRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.import_local(project_id, asset_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def import_url_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportUrlRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.import_url(project_id, asset_id, request)
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def review_asset_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetReviewRequest,
    ) -> AssetMutationResponse:
        try:
            version = self.asset_workbench.review_version(
                project_id,
                asset_id,
                version_id,
                request,
            )
            return self._asset_mutation_response(project_id, version.id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

    def update_character_voice_reference(
        self,
        project_id: str,
        character_id: str,
        request: VoiceReferenceUpdateRequest,
    ) -> VoiceReferenceMutationResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            character = self._require_current_character(document, character_id)
            existing = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            now = utc_now()
            if request.asset_id is None:
                if existing is not None:
                    document.voice_references.remove(existing)
                    document.project.voice_reference_revision += 1
                    document.project.updated_at = now
                    self.repository.save_project(document)
                return self._voice_reference_mutation_response(
                    project_id,
                    character.id,
                )

            asset = self._require_asset_in_document(document, request.asset_id)
            self._require_approved_voice_audio(asset)
            changed = existing is None or existing.asset_id != asset.id
            if existing is None:
                document.voice_references.append(
                    VoiceReferenceBinding(
                        character_id=character.id,
                        asset_id=asset.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif changed:
                existing.asset_id = asset.id
                existing.updated_at = now
            if changed:
                document.project.voice_reference_revision += 1
                document.project.updated_at = now
                self.repository.save_project(document)
        return self._voice_reference_mutation_response(project_id, character_id)

    def upload_character_voice_reference(
        self,
        project_id: str,
        character_id: str,
        *,
        filename: str,
        descriptor: str,
        data: bytes,
    ) -> VoiceReferenceMutationResponse:
        with self._lock:
            document = self._require_project_document(project_id)
            character = self._require_current_character(document, character_id)
            existing = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            target_asset_id = existing.asset_id if existing is not None else None
            character_name = character.name
        try:
            version = self.asset_workbench.import_voice_reference_bytes(
                project_id,
                asset_id=target_asset_id,
                asset_name=f"{character_name}声线参考",
                descriptor=descriptor,
                filename=filename,
                data=data,
            )
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc

        with self._lock:
            document = self._require_project_document(project_id)
            character = self._require_current_character(document, character_id)
            asset = next(
                (
                    candidate
                    for candidate in document.assets
                    if any(item.id == version.id for item in candidate.versions)
                ),
                None,
            )
            if asset is None:
                raise ArtifactNotFoundError(version.id)
            now = utc_now()
            binding = next(
                (
                    item
                    for item in document.voice_references
                    if item.character_id == character.id
                ),
                None,
            )
            if binding is None:
                document.voice_references.append(
                    VoiceReferenceBinding(
                        character_id=character.id,
                        asset_id=asset.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                binding.asset_id = asset.id
                binding.updated_at = now
            document.project.voice_reference_revision += 1
            document.project.updated_at = now
            self.repository.save_project(document)
        return self._voice_reference_mutation_response(
            project_id,
            character_id,
            version_id=version.id,
        )

    def enqueue_image_task(
        self,
        project_id: str,
        asset_id: str,
        request: ImageTaskCreateRequest,
    ) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.enqueue_image_task(project_id, asset_id, request)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(project_id))

    def list_image_tasks(
        self,
        project_id: str,
        asset_id: str | None = None,
    ) -> ImageTaskListResponse:
        try:
            tasks = self.asset_workbench.list_image_tasks(project_id, asset_id)
        except AssetWorkbenchNotFound as exc:
            raise ProjectNotFoundError(str(exc)) from exc
        return ImageTaskListResponse(tasks=tasks)

    def advance_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.advance_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except ImageProviderError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def cancel_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.cancel_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def retry_image_task(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.retry_image_task(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except (AssetWorkbenchConflict, AssetProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def recover_image_task_output(self, task_id: str) -> ImageTaskResponse:
        try:
            task = self.asset_workbench.recover_image_output(task_id)
        except AssetWorkbenchNotFound as exc:
            raise ArtifactNotFoundError(str(exc)) from exc
        except AssetWorkbenchConflict as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return ImageTaskResponse(task=task, project=self.get_project(task.project_id))

    def update_h3_config(
        self,
        request: H3ProviderConfigUpdate,
    ) -> H3ProviderConfigResponse:
        return self.h3_config.update(request)

    def validate_h3_credentials(self) -> H3ProviderConfigResponse:
        try:
            self.h3_worker.validate_credentials()
        except H3ProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return self.h3_config.response()

    def preview_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3CompilePreviewResponse:
        _document, _shot, prompt, duration, media_inputs = self._resolve_h3_probe(
            project_id=project_id,
            request=request,
        )

        def preview_url(media: H3MediaInput) -> str:
            if media.source.external_url is not None:
                return media.source.external_url
            if media.source.file_id is not None:
                return f"mm_file://{media.source.file_id}"
            assert media.source.local_path is not None
            return f"local-upload://{Path(media.source.local_path).name}"

        try:
            compiled = compile_h3_request(
                route=request.route,
                prompt=prompt,
                duration_seconds=duration,
                media_inputs=media_inputs,
                resolve_url=preview_url,
            )
        except H3CapabilityError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return H3CompilePreviewResponse(prompt=prompt, request=compiled)

    def enqueue_h3_probe(
        self,
        project_id: str,
        request: H3ProbeRequest,
    ) -> H3ProbeResponse:
        with self._lock:
            if not self.h3_config.api_key():
                raise WorkflowConflictError(
                    "AceData API token is required before enqueueing a paid H3 probe"
                )
            document, shot, prompt, duration, media_inputs = self._resolve_h3_probe(
                project_id=project_id,
                request=request,
            )
            existing = self.repository.list_tasks_for_shot(project_id, shot.id)
            candidate_index = max((task.candidate_index for task in existing), default=0) + 1
            task, run, evidence = self._new_h3_task_and_run(
                document=document,
                shot_id=shot.id,
                route=request.route,
                duration_seconds=duration,
                prompt=prompt,
                media_inputs=media_inputs,
                purpose=request.purpose,
                learning_objective=request.learning_objective,
                regression_case_id=request.regression_case_id,
                candidate_index=candidate_index,
                attempt=1,
                retry_of_task_id=None,
            )
            self.repository.save_task(task)
            self.repository.save_provider_run(run)
            self.repository.save_route_evidence(evidence)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return H3ProbeResponse(
                task=task,
                provider_run=run,
                project=self._aggregate(document),
            )

    def list_h3_provider_runs(self, project_id: str) -> H3ProviderRunsResponse:
        self._require_project_document(project_id)
        return H3ProviderRunsResponse(runs=self.repository.list_provider_runs(project_id))

    def list_h3_reconciliation_candidates(self) -> H3ReconciliationCandidatesResponse:
        try:
            tasks = self.h3_worker.list_reconciliation_candidates()
        except H3ProviderConfigurationError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        return H3ReconciliationCandidatesResponse(tasks=tasks)

    def enqueue(self, project_id: str, request: EnqueueRequest) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            available_shot_ids = {shot.id for shot in document.shots}
            requested_shot_ids = request.shot_ids or [shot.id for shot in document.shots]
            unknown = set(requested_shot_ids) - available_shot_ids
            if unknown:
                raise ArtifactNotFoundError(", ".join(sorted(unknown)))
            for shot in document.shots:
                if shot.id not in requested_shot_ids:
                    continue
                for candidate_index in range(1, request.candidate_count + 1):
                    if self._has_current_take_for_candidate(project_id, shot.id, candidate_index):
                        continue
                    behavior = self._resolve_behavior(
                        request.behavior_by_candidate,
                        shot.id,
                        candidate_index,
                    )
                    task = self._new_task(
                        document=document,
                        shot_id=shot.id,
                        operation=request.operation,
                        candidate_index=candidate_index,
                        requested_duration_seconds=shot.duration_seconds,
                        behavior=behavior,
                        attempt=1,
                        retry_of_task_id=None,
                    )
                    self.repository.save_task(task)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return self._aggregate(document)

    def advance_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.advance(task_id)
                if persisted.provider == "minimax"
                else self.worker.advance(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError, H3CapabilityError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def drain(self, project_id: str, max_steps: int) -> WorkerDrainResponse:
        self._require_project_document(project_id)
        processed_steps = self.worker.drain(project_id, max_steps)
        self._refresh_project_stage(project_id)
        return WorkerDrainResponse(
            processed_steps=processed_steps,
            remaining_runnable=self.repository.count_runnable_tasks(
                project_id,
                provider="mock",
            ),
            project=self.get_project(project_id),
        )

    def cancel_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.cancel(task_id)
                if persisted.provider == "minimax"
                else self.worker.cancel(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def retry_task(self, task_id: str) -> GenerationTask:
        with self._lock:
            task = self._require_task(task_id)
            if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                raise WorkflowConflictError(
                    f"Task {task.id} cannot retry from {task.status.value}"
                )
            document = self._require_project_document(task.project_id)
            retry_run: H3ProviderRun | None = None
            retry_evidence: RouteEvidence | None = None
            if task.provider == "minimax":
                if (
                    task.input_voice_reference_revision
                    != document.project.voice_reference_revision
                ):
                    raise WorkflowConflictError(
                        "Character voice references changed after this task; compile a new H3 "
                        "production route plan instead of retrying stale media inputs"
                    )
                run = self.repository.get_provider_run(task.id)
                if run is None:
                    raise WorkflowConflictError(f"H3 provider run for task {task.id} is missing")
                retry, retry_run, retry_evidence = self._new_h3_task_and_run(
                    document=document,
                    shot_id=task.shot_id,
                    route=run.route,
                    duration_seconds=int(task.requested_duration_seconds),
                    prompt=run.prompt,
                    media_inputs=run.media_inputs,
                    purpose=run.purpose,
                    learning_objective=run.learning_objective,
                    regression_case_id=run.regression_case_id,
                    candidate_index=task.candidate_index,
                    attempt=task.attempt + 1,
                    retry_of_task_id=task.id,
                )
            else:
                retry = self._new_task(
                    document=document,
                    shot_id=task.shot_id,
                    operation=task.operation,
                    candidate_index=task.candidate_index,
                    requested_duration_seconds=task.requested_duration_seconds,
                    behavior=MockBehavior.SUCCESS,
                    attempt=task.attempt + 1,
                    retry_of_task_id=task.id,
                )
            self.repository.save_task(retry)
            if retry_run is not None and retry_evidence is not None:
                self.repository.save_provider_run(retry_run)
                self.repository.save_route_evidence(retry_evidence)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return retry

    def reconcile_task(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        if persisted.provider == "minimax":
            raise WorkflowConflictError(
                "Use explicit MiniMax task reconciliation for an H3 SUBMIT_UNKNOWN task"
            )
        try:
            task = self.worker.reconcile(task_id)
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except InvalidTaskTransition as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return task

    def resolve_h3_submission(
        self,
        task_id: str,
        provider_task_id: str,
    ) -> H3TaskActionResponse:
        try:
            task = self.h3_worker.reconcile(task_id, provider_task_id.strip())
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        run = self.repository.get_provider_run(task.id)
        if run is None:
            raise WorkflowConflictError(f"H3 provider run for task {task.id} is missing")
        return H3TaskActionResponse(
            task=task,
            project=self.get_project(task.project_id),
            provider_run=run,
        )

    def resolve_h3_not_submitted(
        self,
        task_id: str,
        request: H3NotSubmittedResolutionRequest,
    ) -> H3TaskActionResponse:
        try:
            task = self.h3_worker.resolve_not_submitted(task_id, request.reason)
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except InvalidTaskTransition as exc:
            raise WorkflowConflictError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        run = self.repository.get_provider_run(task.id)
        if run is None:
            raise WorkflowConflictError(f"H3 provider run for task {task.id} is missing")
        return H3TaskActionResponse(
            task=task,
            project=self.get_project(task.project_id),
            provider_run=run,
        )

    def recover_task_output(self, task_id: str) -> GenerationTask:
        persisted = self._require_task(task_id)
        try:
            task = (
                self.h3_worker.recover_output(task_id)
                if persisted.provider == "minimax"
                else self.worker.recover_output(task_id)
            )
        except KeyError as exc:
            raise ArtifactNotFoundError(task_id) from exc
        except (InvalidTaskTransition, H3ProviderConfigurationError) as exc:
            raise WorkflowConflictError(str(exc)) from exc
        except H3ClientError as exc:
            raise ProviderOperationError(str(exc)) from exc
        self._refresh_project_stage(task.project_id)
        return task

    def select_take(self, take_id: str) -> ProjectAggregate:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            if take.freshness != Freshness.CURRENT:
                raise WorkflowConflictError("A stale Take cannot be selected for the current timeline")
            if take.qa_status != QaStatus.PASSED:
                raise WorkflowConflictError("Only a QA-passed Take can be selected")
            evidence = self.repository.get_route_evidence_for_take(take_id)
            if evidence is not None and evidence.purpose == EvidencePurpose.CONTRACT_SMOKE:
                raise WorkflowConflictError(
                    "A contract-smoke Take cannot be selected for the production timeline"
                )
            selected = self.repository.select_take(take_id)
            if selected is None:
                raise ArtifactNotFoundError(take_id)
            document = self._require_project_document(take.project_id)
            self._invalidate_current_manifests(document)
            document.project.stage = ProjectStage.REVIEW
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return self._aggregate(document)

    def inspect_take_video(
        self,
        take_id: str,
        request: VideoInspectionRequest,
    ) -> VideoInspectionResponse:
        take = self.repository.get_take(take_id)
        if take is None:
            raise ArtifactNotFoundError(take_id)
        document = self._require_project_document(take.project_id)
        shot = self._require_shot_in_document(document, take.shot_id)

        previous_take: Take | None = None
        previous_shot: ShotPlan | None = None
        if request.previous_take_id is not None:
            candidate = self.repository.get_take(request.previous_take_id)
            if candidate is None or candidate.project_id != take.project_id:
                raise ArtifactNotFoundError(request.previous_take_id)
            previous_take = candidate
            previous_shot = self._require_shot_in_document(document, candidate.shot_id)
        elif request.include_previous_boundary:
            previous_shot = max(
                (item for item in document.shots if item.sequence < shot.sequence),
                key=lambda item: item.sequence,
                default=None,
            )
            if previous_shot is not None:
                previous_take = next(
                    (
                        item
                        for item in reversed(
                            self.repository.list_takes_for_shot(
                                take.project_id,
                                previous_shot.id,
                            )
                        )
                        if item.is_selected and item.freshness == Freshness.CURRENT
                    ),
                    None,
                )
                if previous_take is None:
                    previous_shot = None

        language_client = None
        if request.semantic_review:
            api_key = self.language_config.api_key()
            base_url = self.language_config.base_url()
            if api_key or is_loopback_openai_url(base_url):
                language_client = self.language_provider_factory(api_key, base_url)
        report = self.video_inspector.inspect_video(
            document=document,
            shot=shot,
            take=take,
            previous_shot=previous_shot,
            previous_take=previous_take,
            request=request,
            language_client=language_client,
            created_at=utc_now(),
        )
        self.repository.save_video_inspection(report)
        return VideoInspectionResponse(
            report=report,
            project=self.get_project(take.project_id),
        )

    def inspect_current_manifest_audio(
        self,
        project_id: str,
        request: AudioInspectionRequest,
    ) -> AudioInspectionResponse:
        document = self._require_project_document(project_id)
        manifest = self._current_manifest(document)
        report = self.audio_inspector.inspect_manifest(
            document=document,
            manifest=manifest,
            request=request,
            semantic_service=self.audio_semantic_service,
            created_at=utc_now(),
        )
        self.repository.save_audio_inspection(report)
        return AudioInspectionResponse(
            report=report,
            project=self.get_project(project_id),
        )

    def _ensure_current_manifest_audio_inspection(
        self,
        project_id: str,
    ) -> AudioInspectionReport:
        document = self._require_project_document(project_id)
        manifest = self._current_manifest(document)
        existing = [
            item
            for item in self.repository.list_audio_inspections(project_id)
            if item.manifest_id == manifest.id
        ]
        if existing:
            latest = existing[-1]
            if (
                latest.semantic_status == SemanticReviewStatus.COMPLETE
                or self.audio_semantic_service is None
            ):
                return latest
        return self.inspect_current_manifest_audio(
            project_id,
            AudioInspectionRequest(
                semantic_review=True,
                speaker_continuity=True,
            ),
        ).report

    def _record_autonomous_audio_findings(
        self,
        project_id: str,
        run_id: str,
        report: AudioInspectionReport,
    ) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            run = next(
                (item for item in document.autonomous_runs if item.id == run_id),
                None,
            )
            if run is None:
                raise ArtifactNotFoundError(run_id)
            prefix = f"Audio QA {report.id}:"
            run.warnings = [item for item in run.warnings if not item.startswith(prefix)]
            severe = sum(
                issue.severity.upper()
                in {"ERROR", "HIGH", "MAJOR", "CRITICAL", "FATAL"}
                for issue in report.issues
            )
            run.warnings.append(
                f"{prefix} semantic={report.semantic_status.value}, "
                f"severe_issues={severe}. {report.summary}"
            )
            run.updated_at = utc_now()
            self.repository.save_project(document)

    def review_h3_take(
        self,
        take_id: str,
        request: H3TakeReviewRequest,
    ) -> H3TakeReviewResponse:
        with self._lock:
            take = self.repository.get_take(take_id)
            if take is None:
                raise ArtifactNotFoundError(take_id)
            if take.provider != "minimax" or take.model != "MiniMax-H3":
                raise WorkflowConflictError("Only a MiniMax-H3 Take uses H3 route review")
            evidence = self.repository.get_route_evidence_for_take(take_id)
            if evidence is None:
                raise WorkflowConflictError(f"Route evidence for Take {take_id} is missing")
            if request.hard_gate_passed and evidence.technical_passed is not True:
                raise WorkflowConflictError(
                    "Technical media gates failed; this Take cannot be marked hard-gate passed"
                )

            now = utc_now()
            status = QaStatus.PASSED if request.hard_gate_passed else QaStatus.REJECTED
            # A review request represents the current semantic judgment. Keep durable
            # media failures from the worker, but do not let superseded visual issues
            # accumulate forever across a reinspection of the same Take.
            merged_issues = {
                issue.code: issue
                for issue in take.qa_report.issues
                if issue.category == QaFailureCategory.TECHNICAL_MEDIA
            }
            merged_issues.update({issue.code: issue for issue in request.issues})
            take.qa_status = status
            take.qa_report.status = status
            take.qa_report.hard_gate_passed = request.hard_gate_passed
            take.qa_report.score = request.score
            take.qa_report.issues = list(merged_issues.values())
            take.qa_report.reviewer_notes = request.notes
            take.qa_report.reviewed_at = now
            if status != QaStatus.PASSED:
                take.is_selected = False
            self.repository.save_take(take)

            evidence.status = EvidenceStatus.REVIEWED
            evidence.hard_gate_passed = request.hard_gate_passed
            evidence.score = request.score
            evidence.notes = request.notes
            evidence.reviewed_at = now
            self.repository.save_route_evidence(evidence)

            document = self._require_project_document(take.project_id)
            document.project.stage = ProjectStage.REVIEW
            document.project.updated_at = now
            self.repository.save_project(document)
            return H3TakeReviewResponse(
                take=take,
                evidence=evidence,
                project=self._aggregate(document),
            )

    def revise_script_line(
        self,
        project_id: str,
        request: ReviseScriptLineRequest,
    ) -> ProjectAggregate:
        with self._lock:
            document = self._require_project_document(project_id)
            current = self._current_script(document)
            matching_line = next((line for line in current.lines if line.id == request.line_id), None)
            if matching_line is None:
                raise ArtifactNotFoundError(request.line_id)
            current.approval_status = ApprovalStatus.SUPERSEDED
            next_version_number = max(version.version for version in document.script_versions) + 1
            next_lines = [line.model_copy(deep=True) for line in current.lines]
            for line in next_lines:
                if line.id == request.line_id:
                    line.spoken_text = request.spoken_text
                    line.subtitle_text = request.subtitle_text
            now = utc_now()
            next_script = ScriptVersion(
                id=f"{project_id}-script-v{next_version_number}",
                project_id=project_id,
                version=next_version_number,
                source_mode=current.source_mode,
                original_text=current.original_text,
                spoken_text="\n".join(line.spoken_text for line in next_lines),
                subtitle_text="\n".join(line.subtitle_text for line in next_lines),
                lines=next_lines,
                structured_scenes=[scene.model_copy(deep=True) for scene in current.structured_scenes],
                review_report=(
                    current.review_report.model_copy(deep=True)
                    if current.review_report is not None
                    else None
                ),
                planning_run_id=current.planning_run_id,
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                parent_version_id=current.id,
                change_reason=request.change_reason,
                created_at=now,
            )
            document.script_versions.append(next_script)
            document.project.current_script_version_id = next_script.id
            affected_shot_ids = [
                shot.id for shot in document.shots if request.line_id in shot.dialogue_line_ids
            ]
            self.repository.mark_takes_stale(project_id, affected_shot_ids)
            self._invalidate_current_manifests(document)
            document.project.stage = ProjectStage.PLANNED
            document.project.updated_at = now
            self.repository.save_project(document)
            return self._aggregate(document)

    def enqueue_local_rework(
        self,
        project_id: str,
        shot_id: str,
        operation: OperationKind,
        request: LocalReworkRequest,
    ) -> GenerationTask:
        if operation == OperationKind.GENERATE:
            raise WorkflowConflictError("Use the regular enqueue operation for initial generation")
        with self._lock:
            document = self._require_project_document(project_id)
            shot = next((item for item in document.shots if item.id == shot_id), None)
            if shot is None:
                raise ArtifactNotFoundError(shot_id)
            existing = self.repository.list_tasks_for_shot(project_id, shot_id)
            candidate_index = max((task.candidate_index for task in existing), default=0) + 1
            duration = shot.duration_seconds
            if operation == OperationKind.EXTEND:
                duration += request.duration_delta_seconds or 2.0
            elif operation == OperationKind.GAP_FILL:
                duration = request.duration_delta_seconds if request.duration_delta_seconds > 0 else 1.5
            duration = max(0.5, duration)
            task = self._new_task(
                document=document,
                shot_id=shot_id,
                operation=operation,
                candidate_index=candidate_index,
                requested_duration_seconds=duration,
                behavior=request.behavior,
                attempt=1,
                retry_of_task_id=None,
            )
            self.repository.save_task(task)
            document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return task

    def rebuild_manifest(self, project_id: str) -> TimelineManifest:
        with self._lock:
            document = self._require_project_document(project_id)
            current_script = self._current_script(document)
            selected_by_shot: dict[str, Take] = {}
            for take in self.repository.list_takes(project_id):
                if (
                    take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and _take_is_timeline_eligible(take)
                ):
                    selected_by_shot[take.shot_id] = take
            missing = [shot.id for shot in document.shots if shot.id not in selected_by_shot]
            if missing:
                raise WorkflowConflictError(
                    "Every shot needs a current QA-passed selected Take before building the timeline: "
                    + ", ".join(missing)
                )
            self._invalidate_current_manifests(document)
            version = max((manifest.version for manifest in document.manifests), default=0) + 1
            now = utc_now()
            video_clips: list[TimelineVideoClip] = []
            audio_clips: list[TimelineAudioClip] = []
            subtitle_cues: list[SubtitleCue] = []
            line_by_id = {line.id: line for line in current_script.lines}
            cursor = 0.0
            for shot in sorted(document.shots, key=lambda item: item.sequence):
                take = selected_by_shot[shot.id]
                source_in_seconds, clip_duration = _timeline_clip_window(shot, take)
                video_clips.append(
                    TimelineVideoClip(
                        id=f"video-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        media_path=take.media_path,
                        start_seconds=cursor,
                        source_in_seconds=source_in_seconds,
                        duration_seconds=clip_duration,
                        transition_in="none",
                        transition_out=shot.transition_out,
                    )
                )
                audio_clips.append(
                    TimelineAudioClip(
                        id=f"audio-{shot.id}-{take.id}",
                        shot_id=shot.id,
                        take_id=take.id,
                        media_path=take.media_path,
                        start_seconds=cursor,
                        duration_seconds=clip_duration,
                        gain_db=0.0,
                        role=(
                            "MODEL_NATIVE_H3"
                            if take.provider == "minimax"
                            else "MODEL_NATIVE_MOCK_PLACEHOLDER"
                        ),
                    )
                )
                lines = [line_by_id[line_id] for line_id in shot.dialogue_line_ids if line_id in line_by_id]
                if lines:
                    usable_duration = max(0.5, clip_duration - 1.0)
                    cue_duration = usable_duration / len(lines)
                    for index, line in enumerate(lines):
                        cue_start = cursor + 0.5 + cue_duration * index
                        cue_end = min(cursor + clip_duration - 0.25, cue_start + cue_duration)
                        subtitle_cues.append(
                            SubtitleCue(
                                id=f"subtitle-{shot.id}-{line.id}",
                                script_line_id=line.id,
                                start_seconds=cue_start,
                                end_seconds=cue_end,
                                text=line.subtitle_text,
                                speaker=line.speaker,
                            )
                        )
                cursor += clip_duration
            manifest = TimelineManifest(
                id=f"{project_id}-manifest-v{version}",
                project_id=project_id,
                version=version,
                script_version_id=current_script.id,
                bible_version_id=document.project.current_bible_version_id,
                video_clips=video_clips,
                audio_clips=audio_clips,
                subtitle_cues=subtitle_cues,
                output_spec=OutputSpec(),
                duration_seconds=cursor,
                approval_status=ApprovalStatus.DRAFT,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            document.manifests.append(manifest)
            document.project.current_manifest_id = manifest.id
            document.project.stage = ProjectStage.TIMELINE_READY
            document.project.updated_at = now
            self.repository.save_project(document)
            return manifest

    def export_project(self, project_id: str, rebuild_manifest: bool = True) -> ExportRecord:
        with self._lock:
            document = self._require_project_document(project_id)
            if rebuild_manifest or document.project.current_manifest_id is None:
                manifest = self.rebuild_manifest(project_id)
            else:
                manifest = self._current_manifest(document)
            manifest_snapshot = manifest.model_copy(deep=True)

        # ffmpeg can take minutes for a long episode. Keep it outside the service lock so
        # progress reads and unrelated project work remain responsive during export.
        export = self.exporter.render(manifest_snapshot, utc_now())
        if not all(check.passed for check in export.checks):
            raise WorkflowConflictError("Technical export QA failed")

        with self._lock:
            document = self._require_project_document(project_id)
            if document.project.current_manifest_id != manifest_snapshot.id:
                raise WorkflowConflictError(
                    "Timeline changed while export was rendering; export the current timeline again"
                )
            self.repository.save_export(export)
            document.project.stage = ProjectStage.EXPORTED
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return export

    def auto_draft(self, project_id: str, render_export: bool) -> AutoDraftResponse:
        document = self._require_project_document(project_id)
        resumable = next(
            (
                run
                for run in reversed(document.autonomous_runs)
                if run.status
                in {AutonomousRunStatus.RUNNING, AutonomousRunStatus.NEEDS_ATTENTION}
            ),
            None,
        )
        run_id = resumable.id if resumable is not None else None
        try:
            return self._execute_auto_draft(
                project_id,
                render_export=render_export,
                autonomous_run_id=run_id,
            )
        except (WorkflowConflictError, ProviderOperationError, MediaGenerationError) as exc:
            if run_id is not None:
                self._finish_autonomous_run(
                    project_id,
                    run_id,
                    status=AutonomousRunStatus.NEEDS_ATTENTION,
                    error_message=str(exc),
                )
            raise

    def _execute_auto_draft(
        self,
        project_id: str,
        *,
        render_export: bool,
        autonomous_run_id: str | None,
    ) -> AutoDraftResponse:
        repair_round = 0
        max_repair_rounds = 2
        while True:
            self.enqueue(project_id, EnqueueRequest())
            drain_result = self.drain(project_id, max_steps=5000)
            if drain_result.remaining_runnable > 0:
                raise WorkflowConflictError("Worker did not reach an idle state")
            aggregate = self.get_project(project_id)
            if aggregate.progress.unresolved_tasks > 0:
                raise WorkflowConflictError(
                    "AUTO_DRAFT stopped at actionable task failures; reconcile, recover, or retry them first"
                )
            if autonomous_run_id is not None:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.GENERATE,
                    next_step=AutonomousRunStep.QA_AND_REPAIR,
                )

            self._select_best_current_takes(project_id)
            aggregate = self.get_project(project_id)
            missing_selected = [
                shot.id
                for shot in aggregate.document.shots
                if not any(
                    take.shot_id == shot.id
                    and take.is_selected
                    and take.freshness == Freshness.CURRENT
                    and _take_is_timeline_eligible(take)
                    for take in aggregate.takes
                )
            ]
            if not missing_selected:
                break
            if repair_round >= max_repair_rounds:
                raise WorkflowConflictError(
                    "AUTO_DRAFT exhausted Director repair rounds for: "
                    + ", ".join(missing_selected)
                )
            repaired = self._run_autonomous_director_repairs(
                project_id,
                missing_selected,
            )
            if not repaired:
                raise WorkflowConflictError(
                    "AUTO_DRAFT needs a passed Take or an executable Director repair for: "
                    + ", ".join(missing_selected)
                )
            repair_round += 1
            if autonomous_run_id is not None:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.QA_AND_REPAIR,
                    next_step=AutonomousRunStep.GENERATE,
                )
        if autonomous_run_id is not None:
            self._advance_autonomous_run(
                project_id,
                autonomous_run_id,
                completed_step=AutonomousRunStep.QA_AND_REPAIR,
                next_step=AutonomousRunStep.TIMELINE,
            )

        self.rebuild_manifest(project_id)
        if autonomous_run_id is not None:
            if render_export:
                self._advance_autonomous_run(
                    project_id,
                    autonomous_run_id,
                    completed_step=AutonomousRunStep.TIMELINE,
                    next_step=AutonomousRunStep.EXPORT,
                )
        export: ExportRecord | None = None
        if render_export:
            export = self.export_project(project_id, rebuild_manifest=False)
        if autonomous_run_id is not None:
            completed_steps = [AutonomousRunStep.TIMELINE]
            if export is not None:
                completed_steps.append(AutonomousRunStep.EXPORT)
            self._finish_autonomous_run(
                project_id,
                autonomous_run_id,
                status=AutonomousRunStatus.COMPLETE,
                export_id=export.id if export is not None else None,
                completed_steps=completed_steps,
            )
        return AutoDraftResponse(project=self.get_project(project_id), export=export)

    def _run_autonomous_director_repairs(
        self,
        project_id: str,
        shot_ids: list[str],
    ) -> bool:
        project = self.get_project(project_id)
        if project.document.project.run_mode != RunMode.AUTO_DRAFT:
            return False
        reports = project.video_inspections
        applied = False
        for shot_id in shot_ids:
            candidates = [
                take
                for take in project.takes
                if take.shot_id == shot_id and take.freshness == Freshness.CURRENT
            ]
            if not candidates:
                continue
            source_take = max(
                candidates,
                key=lambda take: (take.qa_report.score, take.created_at, take.id),
            )
            inspection = next(
                (
                    report
                    for report in reversed(reports)
                    if report.take_id == source_take.id
                ),
                None,
            )
            result = self.run_director_loop(
                project_id,
                shot_id,
                DirectorAdviceRequest(
                    take_id=source_take.id,
                    inspection_id=inspection.id if inspection is not None else None,
                    user_direction=(
                        "Continue the autonomous draft. Preserve the narrative purpose and only "
                        "apply a repair that addresses observed QA evidence. Compare prior applied "
                        "repairs with the latest QA result; when the same failure remains, prefer a "
                        "materially different strategy unless the actual inputs will change."
                    ),
                ),
            )
            applied = result.repair_applied or applied
        return applied

    def _select_best_current_takes(self, project_id: str) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            takes = self.repository.list_takes(project_id)
            takes_by_shot: dict[str, list[Take]] = {}
            for take in takes:
                if take.freshness == Freshness.CURRENT and _take_is_timeline_eligible(take):
                    takes_by_shot.setdefault(take.shot_id, []).append(take)
            for shot in document.shots:
                candidates = takes_by_shot.get(shot.id, [])
                if not candidates:
                    continue
                best = max(
                    candidates,
                    key=lambda take: (
                        take.qa_status == QaStatus.PASSED,
                        take.qa_report.score,
                        -take.candidate_index,
                    ),
                )
                if not best.is_selected:
                    self.repository.select_take(best.id)

    def _apply_bounded_best_effort_waivers(
        self,
        project_id: str,
        run_id: str,
        shot_ids: list[str],
    ) -> list[str]:
        waived_take_ids: list[str] = []
        with self._lock:
            document = self._require_project_document(project_id)
            run = next(
                (item for item in document.autonomous_runs if item.id == run_id),
                None,
            )
            if run is None:
                raise ArtifactNotFoundError(run_id)
            takes = self.repository.list_takes(project_id)
            evidence_by_take = {
                item.take_id: item
                for item in self.repository.list_route_evidence(project_id)
                if item.take_id is not None
            }
            now = utc_now()
            for shot_id in shot_ids:
                candidates = [
                    take
                    for take in takes
                    if take.shot_id == shot_id
                    and _can_receive_bounded_best_effort_waiver(
                        take,
                        evidence_by_take.get(take.id),
                    )
                ]
                if not candidates:
                    continue
                best = max(
                    candidates,
                    key=lambda take: (
                        take.qa_report.score,
                        -sum(
                            issue.severity.upper() in {"HIGH", "MAJOR"}
                            for issue in take.qa_report.issues
                        ),
                        -take.candidate_index,
                    ),
                )
                if _BOUNDED_BEST_EFFORT_CHECK not in best.qa_report.checks:
                    best.qa_report.checks.append(_BOUNDED_BEST_EFFORT_CHECK)
                issue_codes = [item.code for item in best.qa_report.issues]
                waiver_note = (
                    "AUTO_DRAFT bounded best-effort timeline waiver after "
                    f"{run.repair_round} repair round(s); QA remains REJECTED and unresolved "
                    "issues remain visible: "
                    + (", ".join(issue_codes) if issue_codes else "none")
                )
                best.qa_report.reviewer_notes = " ".join(
                    item
                    for item in [best.qa_report.reviewer_notes, waiver_note]
                    if item
                )
                self.repository.save_take(best)
                self.repository.select_take(best.id)
                evidence = evidence_by_take.get(best.id)
                if evidence is not None:
                    evidence.notes = " ".join(
                        item
                        for item in [evidence.notes, waiver_note]
                        if item
                    )
                    self.repository.save_route_evidence(evidence)
                warning = (
                    f"Bounded best-effort selected {best.id} for {shot_id}; QA remains "
                    f"REJECTED with hard_gate_passed=false. Unresolved: "
                    + (", ".join(issue_codes) if issue_codes else "none")
                )
                if warning not in run.warnings:
                    run.warnings.append(warning)
                waived_take_ids.append(best.id)
            if waived_take_ids:
                run.updated_at = now
                document.project.updated_at = now
                self.repository.save_project(document)
        return waived_take_ids

    def _resolve_h3_probe(
        self,
        *,
        project_id: str,
        request: H3ProbeRequest,
    ) -> tuple[ProjectDocument, ShotPlan, str, int, list[H3MediaInput]]:
        document = self._require_project_document(project_id)
        shot = next((item for item in document.shots if item.id == request.shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(request.shot_id)
        if (
            request.regression_case_id is not None
            and known_regression_case(request.regression_case_id) is None
        ):
            raise ArtifactNotFoundError(request.regression_case_id)
        prompt = (
            request.prompt_override.strip()
            if request.prompt_override is not None
            else build_h3_prompt(document, shot)
        )
        duration = request.duration_seconds or math.ceil(shot.duration_seconds)
        media_inputs = self._resolve_approved_asset_inputs(
            document,
            request.media_inputs,
        )
        try:
            validate_h3_probe(
                route=request.route,
                prompt=prompt,
                duration_seconds=duration,
                media_inputs=media_inputs,
            )
        except H3CapabilityError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        return document, shot, prompt, duration, media_inputs

    def _resolve_approved_asset_inputs(
        self,
        document: ProjectDocument,
        media_inputs: list[H3MediaInput],
    ) -> list[H3MediaInput]:
        approved_versions: dict[str, AssetVersion] = {}
        for asset in document.assets:
            approved_id = asset.approved_version_id
            if approved_id is None:
                continue
            approved = next(
                (version for version in asset.versions if version.id == approved_id),
                None,
            )
            if approved is not None and approved.approval_status == ApprovalStatus.APPROVED:
                approved_versions[approved.id] = approved

        expected_kinds = {
            AssetMediaType.IMAGE: H3MediaKind.IMAGE,
            AssetMediaType.VIDEO: H3MediaKind.VIDEO,
            AssetMediaType.AUDIO: H3MediaKind.AUDIO,
        }
        resolved: list[H3MediaInput] = []
        for media in media_inputs:
            if media.asset_version_id is None:
                resolved.append(media)
                continue
            version = approved_versions.get(media.asset_version_id)
            if version is None:
                raise WorkflowConflictError(
                    f"Asset version {media.asset_version_id} is not the asset's approved formal version"
                )
            expected_kind = expected_kinds.get(version.media_type)
            if expected_kind is None or expected_kind != media.kind:
                raise WorkflowConflictError(
                    f"Approved asset version {version.id} is not compatible with {media.kind.value}"
                )
            if version.local_path is not None:
                source = H3MediaSource(local_path=version.local_path)
            elif version.external_url is not None:
                source = H3MediaSource(external_url=version.external_url)
            else:
                raise WorkflowConflictError(
                    f"Approved asset version {version.id} has no usable media source"
                )
            resolved.append(media.model_copy(update={"source": source}))
        return resolved

    def _new_h3_task_and_run(
        self,
        *,
        document: ProjectDocument,
        shot_id: str,
        route: VideoGenerationRoute,
        duration_seconds: int,
        prompt: str,
        media_inputs: list[H3MediaInput],
        purpose: EvidencePurpose,
        learning_objective: str | None,
        regression_case_id: str | None,
        candidate_index: int,
        attempt: int,
        retry_of_task_id: str | None,
    ) -> tuple[GenerationTask, H3ProviderRun, RouteEvidence]:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        run_id = f"h3-run-{uuid4().hex}"
        shot = self._require_shot_in_document(document, shot_id)
        normalized_request = {
            "provider": "minimax",
            "model": "MiniMax-H3",
            "project_id": document.project.id,
            "shot_id": shot_id,
            "route": route.value,
            "resolution": "768P",
            "duration": duration_seconds,
            "prompt": prompt,
            "media_inputs": [
                item.model_dump(mode="json", exclude_none=True) for item in media_inputs
            ],
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "shot_revision": shot.revision,
            "voice_reference_revision": document.project.voice_reference_revision,
            "learning_objective": learning_objective,
            "regression_case_id": regression_case_id,
            "attempt": attempt,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                normalized_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        task = GenerationTask(
            id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            operation=OperationKind.GENERATE,
            candidate_index=candidate_index,
            status=TaskStatus.QUEUED,
            provider="minimax",
            model="MiniMax-H3",
            route=route,
            resolution="768P",
            provider_run_id=run_id,
            request_fingerprint=fingerprint,
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            input_shot_revision=shot.revision,
            input_voice_reference_revision=document.project.voice_reference_revision,
            requested_duration_seconds=float(duration_seconds),
            attempt=attempt,
            retry_of_task_id=retry_of_task_id,
            created_at=now,
            updated_at=now,
        )
        run = H3ProviderRun(
            id=run_id,
            task_id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            route=route,
            purpose=purpose,
            learning_objective=(
                learning_objective.strip() if learning_objective is not None else None
            ),
            regression_case_id=regression_case_id,
            prompt=prompt,
            media_inputs=[item.model_copy(deep=True) for item in media_inputs],
            pricing=self.h3_config.pricing_snapshot(),
            created_at=now,
            updated_at=now,
        )
        evidence = RouteEvidence(
            id=f"evidence-{uuid4().hex}",
            project_id=document.project.id,
            shot_id=shot_id,
            task_id=task_id,
            provider="minimax",
            model="MiniMax-H3",
            route=route,
            resolution="768P",
            purpose=purpose,
            status=EvidenceStatus.AWAITING_OUTPUT,
            created_at=now,
        )
        return task, run, evidence

    def _new_task(
        self,
        *,
        document: ProjectDocument,
        shot_id: str,
        operation: OperationKind,
        candidate_index: int,
        requested_duration_seconds: float,
        behavior: MockBehavior,
        attempt: int,
        retry_of_task_id: str | None,
    ) -> GenerationTask:
        now = utc_now()
        task_id = f"task-{uuid4().hex}"
        shot = self._require_shot_in_document(document, shot_id)
        normalized_request = {
            "project_id": document.project.id,
            "shot_id": shot_id,
            "operation": operation.value,
            "candidate_index": candidate_index,
            "script_version_id": document.project.current_script_version_id,
            "bible_version_id": document.project.current_bible_version_id,
            "shot_revision": shot.revision,
            "voice_reference_revision": document.project.voice_reference_revision,
            "requested_duration_seconds": requested_duration_seconds,
            "attempt": attempt,
        }
        fingerprint = hashlib.sha256(
            json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return GenerationTask(
            id=task_id,
            project_id=document.project.id,
            shot_id=shot_id,
            operation=operation,
            candidate_index=candidate_index,
            status=TaskStatus.QUEUED,
            provider="mock",
            model="deterministic-media-v1",
            resolution="432x768",
            request_fingerprint=fingerprint,
            input_script_version_id=document.project.current_script_version_id,
            input_bible_version_id=document.project.current_bible_version_id,
            input_shot_revision=shot.revision,
            input_voice_reference_revision=document.project.voice_reference_revision,
            requested_duration_seconds=requested_duration_seconds,
            attempt=attempt,
            retry_of_task_id=retry_of_task_id,
            mock_behavior=behavior,
            created_at=now,
            updated_at=now,
        )

    def _aggregate(self, document: ProjectDocument) -> ProjectAggregate:
        tasks = self.repository.list_tasks(document.project.id)
        takes = self.repository.list_takes(document.project.id)
        exports = self.repository.list_exports(document.project.id)
        selected_shots = {
            take.shot_id
            for take in takes
            if take.is_selected
            and take.freshness == Freshness.CURRENT
            and _take_is_timeline_eligible(take)
        }
        current_manifest = next(
            (
                manifest
                for manifest in document.manifests
                if manifest.id == document.project.current_manifest_id
                and manifest.freshness == Freshness.CURRENT
            ),
            None,
        )
        export_ready = current_manifest is not None and any(
            export.manifest_id == current_manifest.id for export in exports
        )
        progress = ProgressSummary(
            total_shots=len(document.shots),
            shots_with_selected_take=len(selected_shots),
            total_tasks=len(tasks),
            terminal_tasks=sum(1 for task in tasks if task.status in _TERMINAL_TASK_STATUSES),
            failed_tasks=sum(1 for task in tasks if task.status == TaskStatus.FAILED),
            unresolved_tasks=sum(
                1
                for task in tasks
                if task.status
                not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            ),
            passed_takes=sum(
                1
                for take in takes
                if take.qa_status == QaStatus.PASSED and take.freshness == Freshness.CURRENT
            ),
            stale_takes=sum(1 for take in takes if take.freshness == Freshness.STALE),
            manifest_ready=current_manifest is not None,
            export_ready=export_ready,
        )
        return ProjectAggregate(
            document=document,
            tasks=tasks,
            takes=takes,
            exports=exports,
            route_evidence=self.repository.list_route_evidence(document.project.id),
            video_inspections=self.repository.list_video_inspections(document.project.id),
            audio_inspections=self.repository.list_audio_inspections(document.project.id),
            progress=progress,
        )

    def _apply_reference_plan_update(
        self,
        document: ProjectDocument,
        shot_id: str,
        request: ShotReferencePlanUpdateRequest,
    ) -> None:
        requirements = [
            item.model_copy(
                deep=True,
                update={
                    "subject_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.subject_asset_id,
                    )
                },
            )
            for item in request.requirements
        ]
        action_beats = [
            item.model_copy(
                deep=True,
                update={
                    "actor_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.actor_asset_id,
                    )
                },
            )
            for item in request.action_beats
        ]
        interactions = [
            item.model_copy(
                deep=True,
                update={
                    "actor_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.actor_asset_id,
                    ),
                    "target_asset_id": self._resolve_asset_or_version_id(
                        document,
                        item.target_asset_id,
                    ),
                },
            )
            for item in request.interactions
        ]

        plan = self._require_reference_plan(document, shot_id)
        plan.requirements = requirements
        plan.action_beats = [
            item.model_copy(deep=True)
            for item in sorted(action_beats, key=lambda item: item.order)
        ]
        plan.interactions = interactions
        plan.max_reference_images = request.max_reference_images
        plan.planning_notes = (
            request.planning_notes.strip() if request.planning_notes else None
        )
        plan.validation_notes = [
            "Reference requirements are selected from approved semantic packs at preview time",
            "Spatial interaction constraints are provider-neutral and apply to keyframe and video planning",
            "Missing references, topology gaps, and split suggestions are non-blocking Director Agent advisories",
        ]

    def _apply_shot_split(
        self,
        document: ProjectDocument,
        source_shot: ShotPlan,
        request: DirectorRepairDecisionRequest,
    ) -> list[str]:
        shot_index = document.shots.index(source_shot)
        execution_index = next(
            (
                index
                for index, item in enumerate(document.execution_plan)
                if item.shot_id == source_shot.id
            ),
            None,
        )
        reference_index = next(
            (
                index
                for index, item in enumerate(document.reference_plan)
                if item.shot_id == source_shot.id
            ),
            None,
        )
        if execution_index is None or reference_index is None:
            raise WorkflowConflictError(
                f"Shot {source_shot.id} is missing execution or reference dependencies"
            )
        source_execution = document.execution_plan[execution_index]
        source_reference = document.reference_plan[reference_index]
        existing_ids = {item.id for item in document.shots if item.id != source_shot.id}
        replacement_ids: list[str] = []
        replacement_shots: list[ShotPlan] = []
        replacement_execution: list[ExecutionPlanEntry] = []
        replacement_references: list[ReferencePlanEntry] = []

        for draft in request.split_shots:
            replacement_id = draft.id or f"{source_shot.id}-split-{uuid4().hex[:10]}"
            if replacement_id in existing_ids or replacement_id in replacement_ids:
                raise WorkflowConflictError(f"Duplicate replacement shot ID: {replacement_id}")
            self._validate_dialogue_line_ids(document, draft.dialogue_line_ids)
            replacement_ids.append(replacement_id)
            replacement_shots.append(
                ShotPlan(
                    id=replacement_id,
                    project_id=document.project.id,
                    sequence=source_shot.sequence,
                    scene_id=source_shot.scene_id,
                    duration_seconds=draft.duration_seconds,
                    narrative_purpose=draft.narrative_purpose,
                    visual=draft.visual,
                    framing=draft.framing,
                    camera=draft.camera,
                    audio_intent=draft.audio_intent,
                    dialogue_line_ids=list(draft.dialogue_line_ids),
                    continuity=draft.continuity.model_copy(deep=True),
                    transition_out=draft.transition_out,
                    merge_candidate=draft.merge_candidate,
                    local_rework_risk=draft.local_rework_risk,
                    approval_status=ApprovalStatus.APPROVED,
                    freshness=Freshness.CURRENT,
                    revision=(source_shot.revision + 1 if replacement_id == source_shot.id else 1),
                )
            )
            replacement_execution.append(
                source_execution.model_copy(
                    deep=True,
                    update={
                        "shot_id": replacement_id,
                        "rationale": (
                            f"{source_execution.rationale} | Director repair: "
                            f"{request.reason.strip()}"
                        ),
                    },
                )
            )
            replacement_references.append(
                source_reference.model_copy(
                    deep=True,
                    update={"shot_id": replacement_id},
                )
            )

        document.shots[shot_index : shot_index + 1] = replacement_shots
        document.execution_plan[
            execution_index : execution_index + 1
        ] = replacement_execution
        document.reference_plan[
            reference_index : reference_index + 1
        ] = replacement_references
        for draft, replacement_id in zip(
            request.split_shots,
            replacement_ids,
            strict=True,
        ):
            if draft.reference_plan is not None:
                self._apply_reference_plan_update(
                    document,
                    replacement_id,
                    draft.reference_plan,
                )
        for sequence, shot in enumerate(document.shots, start=1):
            shot.sequence = sequence

        dependency_target = next(
            (
                replacement_id
                for draft, replacement_id in zip(
                    request.split_shots,
                    replacement_ids,
                    strict=True,
                )
                if draft.inherits_source_dependencies
            ),
            replacement_ids[-1],
        )
        bible = next(
            (
                item
                for item in document.bible_versions
                if item.id == document.project.current_bible_version_id
            ),
            None,
        )
        if bible is None:
            raise WorkflowConflictError("Project current ProjectBibleVersion is missing")
        for prop in bible.props:
            for state in prop.states:
                if state.shot_id == source_shot.id:
                    state.shot_id = dependency_target

        self.repository.mark_takes_stale(document.project.id, [source_shot.id])
        return replacement_ids

    def _validate_dialogue_line_ids(
        self,
        document: ProjectDocument,
        line_ids: list[str],
    ) -> None:
        if not line_ids:
            return
        script = self._current_script(document)
        known = {line.id for line in script.lines}
        unknown = sorted(set(line_ids) - known)
        if unknown:
            raise ArtifactNotFoundError(", ".join(unknown))

    def _asset_mutation_response(
        self,
        project_id: str,
        version_id: str,
    ) -> AssetMutationResponse:
        project = self.get_project(project_id)
        for asset in project.document.assets:
            version: AssetVersion | None = next(
                (item for item in asset.versions if item.id == version_id),
                None,
            )
            if version is not None:
                return AssetMutationResponse(
                    asset=asset,
                    version=version,
                    project=project,
                )
        raise ArtifactNotFoundError(version_id)

    def _voice_reference_mutation_response(
        self,
        project_id: str,
        character_id: str,
        *,
        version_id: str | None = None,
    ) -> VoiceReferenceMutationResponse:
        project = self.get_project(project_id)
        binding = next(
            (
                item
                for item in project.document.voice_references
                if item.character_id == character_id
            ),
            None,
        )
        if binding is None:
            return VoiceReferenceMutationResponse(project=project)
        asset = self._require_asset_in_document(project.document, binding.asset_id)
        version = next(
            (item for item in asset.versions if item.id == version_id),
            None,
        )
        if version is None and asset.approved_version_id is not None:
            version = next(
                (
                    item
                    for item in asset.versions
                    if item.id == asset.approved_version_id
                ),
                None,
            )
        return VoiceReferenceMutationResponse(
            binding=binding,
            asset=asset,
            version=version,
            project=project,
        )

    @staticmethod
    def _require_current_character(
        document: ProjectDocument,
        character_id: str,
    ) -> CharacterProfile:
        bible = next(
            (
                item
                for item in document.bible_versions
                if item.id == document.project.current_bible_version_id
            ),
            None,
        )
        if bible is None:
            raise WorkflowConflictError("Project current ProjectBibleVersion is missing")
        character = next(
            (item for item in bible.characters if item.id == character_id),
            None,
        )
        if character is None:
            raise ArtifactNotFoundError(character_id)
        return character

    def _refresh_project_stage(self, project_id: str) -> None:
        with self._lock:
            document = self._require_project_document(project_id)
            tasks = self.repository.list_tasks(project_id)
            takes = self.repository.list_takes(project_id)
            if any(task.status in _TERMINAL_TASK_STATUSES for task in tasks) and takes:
                document.project.stage = ProjectStage.REVIEW
            elif tasks:
                document.project.stage = ProjectStage.GENERATING
            document.project.updated_at = utc_now()
            self.repository.save_project(document)

    def _invalidate_current_manifests(self, document: ProjectDocument) -> None:
        for manifest in document.manifests:
            if manifest.freshness == Freshness.CURRENT:
                manifest.freshness = Freshness.STALE
        document.project.current_manifest_id = None

    def _current_script(self, document: ProjectDocument) -> ScriptVersion:
        script = next(
            (
                version
                for version in document.script_versions
                if version.id == document.project.current_script_version_id
            ),
            None,
        )
        if script is None:
            raise WorkflowConflictError("Project current ScriptVersion is missing")
        return script

    def _current_manifest(self, document: ProjectDocument) -> TimelineManifest:
        manifest = next(
            (
                item
                for item in document.manifests
                if item.id == document.project.current_manifest_id
                and item.freshness == Freshness.CURRENT
            ),
            None,
        )
        if manifest is None:
            raise WorkflowConflictError("Project has no current TimelineManifest")
        return manifest

    def _require_project_document(self, project_id: str) -> ProjectDocument:
        document = self.repository.get_project(project_id)
        if document is None:
            raise ProjectNotFoundError(project_id)
        return document

    @staticmethod
    def _require_asset_in_document(document: ProjectDocument, asset_id: str) -> Asset:
        asset = next((item for item in document.assets if item.id == asset_id), None)
        if asset is None:
            raise ArtifactNotFoundError(asset_id)
        return asset

    @staticmethod
    def _resolve_asset_or_version_id(
        document: ProjectDocument,
        candidate_id: str,
    ) -> str:
        """Accept an Agent-selected asset version anywhere an asset subject is required."""

        for asset in document.assets:
            if asset.id == candidate_id:
                return asset.id
            if any(version.id == candidate_id for version in asset.versions):
                return asset.id
        raise ArtifactNotFoundError(candidate_id)

    @staticmethod
    def _find_asset_version(
        document: ProjectDocument,
        version_id: str,
    ) -> tuple[Asset, AssetVersion]:
        for asset in document.assets:
            version = next((item for item in asset.versions if item.id == version_id), None)
            if version is not None:
                return asset, version
        raise ArtifactNotFoundError(version_id)

    @staticmethod
    def _require_shot_in_document(document: ProjectDocument, shot_id: str) -> ShotPlan:
        shot = next((item for item in document.shots if item.id == shot_id), None)
        if shot is None:
            raise ArtifactNotFoundError(shot_id)
        return shot

    @staticmethod
    def _require_reference_plan(
        document: ProjectDocument,
        shot_id: str,
    ) -> ReferencePlanEntry:
        plan = next((item for item in document.reference_plan if item.shot_id == shot_id), None)
        if plan is None:
            raise ArtifactNotFoundError(f"reference-plan:{shot_id}")
        return plan

    def _require_task(self, task_id: str) -> GenerationTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ArtifactNotFoundError(task_id)
        return task

    def _has_current_take_for_candidate(
        self,
        project_id: str,
        shot_id: str,
        candidate_index: int,
    ) -> bool:
        return any(
            take.candidate_index == candidate_index and take.freshness == Freshness.CURRENT
            for take in self.repository.list_takes_for_shot(project_id, shot_id)
        )

    def _resolve_behavior(
        self,
        behavior_by_candidate: dict[str, MockBehavior],
        shot_id: str,
        candidate_index: int,
    ) -> MockBehavior:
        return behavior_by_candidate.get(
            f"{shot_id}:{candidate_index}",
            behavior_by_candidate.get(str(candidate_index), MockBehavior.SUCCESS),
        )
