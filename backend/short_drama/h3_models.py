"""Typed MiniMax-H3 adapter records kept outside the provider-neutral project document."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from short_drama.models import (
    DomainModel,
    EvidencePurpose,
    ExportRecord,
    GenerationTask,
    GenerationUnitShotWindow,
    ImageReferenceRole,
    ProjectAggregate,
    QaStatus,
    QaIssue,
    QaFailureCategory,
    RouteEvidence,
    Take,
    TaskStatus,
    TimelineManifest,
    VideoInspectionReport,
    VideoInspectionScope,
    VideoGenerationRoute,
)
from short_drama.reference_aliases import normalize_reference_alias


def normalize_h3_reference_alias(value: str) -> str:
    return normalize_reference_alias(value, max_length=80)


def derive_h3_reference_alias(value: str) -> str:
    """Normalize a system-generated alias, then truncate its normalized form safely."""

    return normalize_reference_alias(value, max_length=80, truncate=True)


class H3MediaKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class H3MediaRole(StrEnum):
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    REFERENCE_IMAGE = "reference_image"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_AUDIO = "reference_audio"


class H3VideoGuideKind(StrEnum):
    PREVIOUS_TAKE = "PREVIOUS_TAKE"
    TAIL_WINDOW = "TAIL_WINDOW"
    CAMERA_PREVIS = "CAMERA_PREVIS"


class H3CameraMotionPreset(StrEnum):
    STATIC = "STATIC"
    DOLLY_IN = "DOLLY_IN"
    DOLLY_OUT = "DOLLY_OUT"
    TRACK_LEFT = "TRACK_LEFT"
    TRACK_RIGHT = "TRACK_RIGHT"
    JIB_UP = "JIB_UP"
    JIB_DOWN = "JIB_DOWN"
    FOLLOW = "FOLLOW"


class H3VideoGuideBaseSource(StrEnum):
    TARGET_START_FRAME = "TARGET_START_FRAME"
    PREVIOUS_BOUNDARY_FRAME = "PREVIOUS_BOUNDARY_FRAME"


class H3RemoteStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class H3MediaSource(DomainModel):
    external_url: str | None = None
    local_path: str | None = None
    file_id: str | None = None

    @model_validator(mode="after")
    def validate_single_source(self) -> H3MediaSource:
        values = [self.external_url, self.local_path, self.file_id]
        if sum(bool(value and value.strip()) for value in values) != 1:
            raise ValueError("Exactly one of external_url, local_path, or file_id is required")
        return self


class H3MediaInput(DomainModel):
    kind: H3MediaKind
    role: H3MediaRole
    source: H3MediaSource
    asset_version_id: str | None = None
    video_guide_id: str | None = None
    subject_asset_id: str | None = Field(default=None, max_length=200)
    reference_alias: str | None = Field(default=None, min_length=1, max_length=80)
    binding_purpose: str | None = Field(default=None, min_length=1, max_length=500)
    reference_tags: list[str] = Field(default_factory=list[str], max_length=12)
    image_reference_role: ImageReferenceRole | None = Field(default=None, description=(
        "The image's visual purpose, separate from the H3 transport role. "
        "COMPOSITION or LAYOUT_GUIDE keeps a concrete Picture anchor in REFERENCE mode; "
        "it is soft guidance and does not become a hard first_frame/last_frame input. "
        "Omit for legacy reusable subject references."
    ))

    @field_validator("reference_alias", mode="before")
    @classmethod
    def normalize_reference_alias(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        return normalize_h3_reference_alias(value)

    @model_validator(mode="after")
    def validate_reference_metadata(self) -> H3MediaInput:
        if self.image_reference_role is not None and self.kind != H3MediaKind.IMAGE:
            raise ValueError("image_reference_role is only valid for image inputs")
        normalized_tags = [item.strip() for item in self.reference_tags if item.strip()]
        if normalized_tags != self.reference_tags or len(normalized_tags) != len(
            set(normalized_tags)
        ):
            raise ValueError("H3 reference tags must be trimmed and unique")
        return self


class H3ProbeRequest(DomainModel):
    shot_id: str
    resolution: str = "768P"
    aspect_ratio: str | None = None
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    duration_seconds: int | None = Field(default=None, ge=4, le=15)
    prompt_override: str | None = Field(default=None, max_length=7000)
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
    )
    purpose: EvidencePurpose = EvidencePurpose.CONTRACT_SMOKE
    learning_objective: str | None = Field(default=None, max_length=2000)
    regression_case_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_quality_learning_objective(self) -> H3ProbeRequest:
        if self.purpose == EvidencePurpose.QUALITY_PROBE:
            if self.learning_objective is None or not self.learning_objective.strip():
                raise ValueError("A QUALITY_PROBE requires a learning_objective")
        return self


class H3RoutePlanStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    EXECUTING = "EXECUTING"
    COMPLETE = "COMPLETE"
    STALE = "STALE"


class H3ShotRouteAction(StrEnum):
    REUSE_TAKE = "REUSE_TAKE"
    GENERATE = "GENERATE"


class H3ShotRouteReadiness(StrEnum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NEEDS_AGENT_DECISION = "NEEDS_AGENT_DECISION"
    NEEDS_ASSET = "NEEDS_ASSET"
    UNSUPPORTED = "UNSUPPORTED"


class H3ShotRouteExecutionStatus(StrEnum):
    PLANNED = "PLANNED"
    REUSED = "REUSED"
    QUEUED = "QUEUED"
    GENERATED = "GENERATED"
    SKIPPED = "SKIPPED"


class H3RouteAssetPreparationStatus(StrEnum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    READY = "READY"
    REJECTED = "REJECTED"


class H3RouteAssetPreparationUpdateStatus(StrEnum):
    """Lifecycle states that may be recorded after a preparation is planned."""

    GENERATING = "GENERATING"
    READY = "READY"
    REJECTED = "REJECTED"


class H3InspectionPolicy(DomainModel):
    scopes: list[VideoInspectionScope] = Field(min_length=1, max_length=2)
    max_frames: int = Field(ge=6, le=24)
    semantic_review: bool = True
    review_focus: str | None = Field(default=None, max_length=2000)


class H3RouteAssetPreparation(DomainModel):
    id: str
    shot_id: str
    role: H3MediaRole
    name: str = Field(min_length=1, max_length=120)
    descriptor: str = Field(min_length=1, max_length=4000)
    prompt: str = Field(min_length=1, max_length=32000)
    input_version_ids: list[str] = Field(default_factory=list[str], max_length=16)
    rationale: str = Field(min_length=1, max_length=2000)
    advisory_only: Literal[True] = True
    status: H3RouteAssetPreparationStatus = H3RouteAssetPreparationStatus.PLANNED
    asset_id: str | None = None
    image_task_ids: list[str] = Field(default_factory=list[str])
    approved_version_id: str | None = None
    review_summary: str | None = Field(default=None, max_length=4000)


class H3VideoGuide(DomainModel):
    id: str
    shot_id: str
    source_shot_id: str
    source_shot_revision: int = Field(ge=1)
    source_take_id: str
    kind: H3VideoGuideKind
    media_path: str
    duration_seconds: float = Field(gt=0.0, le=15.0)
    source_in_seconds: float = Field(default=0.0, ge=0.0)
    camera_motion: H3CameraMotionPreset | None = None
    previs_base_source: H3VideoGuideBaseSource | None = None
    base_asset_version_id: str | None = None
    source_qa_status: QaStatus
    source_quality_score: float = Field(ge=0.0, le=5.0)
    source_issue_codes: list[str] = Field(default_factory=list[str], max_length=24)
    rationale: str = Field(min_length=1, max_length=2000)
    warnings: list[str] = Field(default_factory=list[str], max_length=16)
    advisory_only: Literal[True] = True
    created_at: str

    @model_validator(mode="after")
    def validate_camera_previs_intent(self) -> H3VideoGuide:
        if self.kind == H3VideoGuideKind.CAMERA_PREVIS:
            if self.camera_motion is None or self.previs_base_source is None:
                raise ValueError(
                    "CAMERA_PREVIS requires explicit camera_motion and previs_base_source"
                )
        elif self.camera_motion is not None or self.previs_base_source is not None:
            raise ValueError(
                "Only CAMERA_PREVIS may declare camera motion or a previs base source"
            )
        return self


class H3ShotRoutePlan(DomainModel):
    resolution: str = "768P"
    aspect_ratio: str | None = None
    shot_id: str
    shot_sequence: int = Field(ge=1)
    generation_unit_id: str | None = Field(default=None, min_length=1, max_length=200)
    clip_group_id: str | None = Field(default=None, min_length=1, max_length=200)
    output_slot_id: str | None = Field(default=None, min_length=1, max_length=240)
    shot_ids: list[str] = Field(default_factory=list[str], max_length=500)
    input_shot_revisions: dict[str, int] = Field(default_factory=dict[str, int])
    shot_source_windows: list[GenerationUnitShotWindow] = Field(
        default_factory=list[GenerationUnitShotWindow],
        max_length=500,
    )
    input_script_version_id: str
    input_bible_version_id: str
    input_shot_revision: int = Field(ge=1)
    action: H3ShotRouteAction
    route: VideoGenerationRoute
    duration_seconds: int = Field(ge=4, le=15)
    prompt: str = Field(min_length=1, max_length=7000)
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
    )
    reuse_take_id: str | None = None
    route_evidence_ids: list[str] = Field(default_factory=list[str], max_length=100)
    rationale: str = Field(min_length=1, max_length=3000)
    risk_ids: list[str] = Field(default_factory=list[str], max_length=20)
    qa_focus_categories: list[QaFailureCategory] = Field(
        default_factory=list[QaFailureCategory],
        max_length=12,
    )
    warnings: list[str] = Field(default_factory=list[str], max_length=32)
    missing_inputs: list[str] = Field(default_factory=list[str], max_length=32)
    asset_preparations: list[H3RouteAssetPreparation] = Field(
        default_factory=list[H3RouteAssetPreparation],
        max_length=4,
    )
    video_guides: list[H3VideoGuide] = Field(
        default_factory=list[H3VideoGuide],
        max_length=3,
    )
    selected_video_guide_id: str | None = None
    reference_selection_candidate_ids: list[str] = Field(default_factory=list[str])
    candidate_count: int = Field(
        default=1,
        ge=1,
        description="Number of candidates requested by this explicit route-plan action.",
    )
    inspection: H3InspectionPolicy
    readiness: H3ShotRouteReadiness
    execution_status: H3ShotRouteExecutionStatus = H3ShotRouteExecutionStatus.PLANNED
    task_ids: list[str] = Field(default_factory=list[str])
    inspection_ids: list[str] = Field(default_factory=list[str], max_length=10)
    inspection_issue_codes: list[str] = Field(default_factory=list[str], max_length=50)
    override_allowed: Literal[True] = True

    @model_validator(mode="after")
    def validate_action_target(self) -> H3ShotRoutePlan:
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("H3 route-plan shot IDs must be unique")
        if any(revision < 1 for revision in self.input_shot_revisions.values()):
            raise ValueError("H3 route-plan shot revisions must be positive")
        if self.generation_unit_id is not None:
            window_ids = [item.shot_id for item in self.shot_source_windows]
            if not self.shot_ids:
                raise ValueError("A generation-unit route requires shot_ids")
            if window_ids != self.shot_ids:
                raise ValueError(
                    "Generation-unit shot windows must follow the declared shot_ids order"
                )
            if set(self.input_shot_revisions) != set(self.shot_ids):
                raise ValueError(
                    "Generation-unit route revisions must cover every declared shot"
                )
            if any(
                self.input_shot_revisions[item.shot_id] != item.shot_revision
                for item in self.shot_source_windows
            ):
                raise ValueError(
                    "Generation-unit shot windows and revision map must agree"
                )
        if self.action == H3ShotRouteAction.REUSE_TAKE and self.reuse_take_id is None:
            raise ValueError("REUSE_TAKE requires reuse_take_id")
        if self.action == H3ShotRouteAction.GENERATE and self.reuse_take_id is not None:
            raise ValueError("GENERATE cannot declare reuse_take_id")
        if self.selected_video_guide_id is not None:
            known = {item.id for item in self.video_guides}
            if self.selected_video_guide_id not in known:
                raise ValueError("selected_video_guide_id must reference a declared video guide")
            if not any(
                item.video_guide_id == self.selected_video_guide_id
                for item in self.media_inputs
            ):
                raise ValueError("Selected video guide must be present in media_inputs")
        if (
            self.reference_selection_candidate_ids
            and self.route != VideoGenerationRoute.REFERENCE
        ):
            raise ValueError(
                "Reference selection candidate IDs are only valid for the REFERENCE route"
            )
        if len(self.reference_selection_candidate_ids) != len(
            set(self.reference_selection_candidate_ids)
        ):
            raise ValueError("Reference selection candidate IDs must be unique")
        return self


class H3SavedRouteSource(DomainModel):
    route_plan_id: str = Field(min_length=1)
    state_revision: int = Field(ge=1)
    generation_unit_id: str = Field(min_length=1, description="Exact source decision identity; must equal the current target group.")


class H3ProductionRoutePlan(DomainModel):
    id: str
    project_id: str
    version: int = Field(ge=1)
    state_revision: int = Field(
        default=1,
        ge=1,
        description=(
            "Monotonic revision of this plan record's mutable preparation and "
            "execution state. The plan version remains the compiled-plan ordinal."
        ),
    )
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    resolution: str = "768P"
    status: H3RoutePlanStatus
    planner: str
    input_script_version_id: str
    input_bible_version_id: str
    input_storyboard_document_id: str | None = None
    input_storyboard_version_id: str | None = None
    input_generation_plan_document_id: str | None = None
    input_generation_plan_version_id: str | None = None
    input_voice_reference_revision: int = Field(default=0, ge=0)
    decisions: list[H3ShotRoutePlan] = Field(min_length=1)
    estimated_new_h3_seconds: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    estimated_cost_credits: float | None = Field(default=None, ge=0.0)
    warnings: list[str] = Field(default_factory=list[str], max_length=100)
    created_at: str
    updated_at: str
    executed_at: str | None = None
    source_operation_id: str | None = None
    reused_route_source: H3SavedRouteSource | None = None


class H3RouteAssetPreparationUpdateRequest(DomainModel):
    expected_state_revision: int = Field(
        ge=1,
        description=(
            "Exact H3 route-plan state revision used to prepare this mutation. "
            "Stale handoffs must be reloaded instead of overwriting newer state."
        ),
    )
    shot_id: str = Field(min_length=1, max_length=200)
    role: H3MediaRole
    status: H3RouteAssetPreparationUpdateStatus
    asset_id: str | None = Field(default=None, min_length=1, max_length=200)
    image_task_ids: list[str] | None = None
    approved_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    review_summary: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_lifecycle_payload(self) -> H3RouteAssetPreparationUpdateRequest:
        if self.image_task_ids is not None and len(self.image_task_ids) != len(
            set(self.image_task_ids)
        ):
            raise ValueError("Image task IDs for an H3 asset preparation must be unique")
        if (
            self.status == H3RouteAssetPreparationUpdateStatus.GENERATING
            and self.asset_id is None
        ):
            raise ValueError(f"{self.status.value} requires asset_id")
        if self.status == H3RouteAssetPreparationUpdateStatus.READY:
            if self.approved_version_id is None:
                raise ValueError("READY requires approved_version_id")
        elif self.approved_version_id is not None:
            raise ValueError("Only READY may declare approved_version_id")
        if self.status == H3RouteAssetPreparationUpdateStatus.REJECTED and (
            self.review_summary is None or not self.review_summary.strip()
        ):
            raise ValueError("REJECTED requires review_summary")
        return self


class H3RouteAssetPreparationUpdateResponse(DomainModel):
    plan: H3ProductionRoutePlan
    preparation: H3RouteAssetPreparation
    approved_version_number: int | None = Field(default=None, ge=1)


class H3ShotRouteDirective(DomainModel):
    """Explicit per-shot production intent supplied by the calling Agent or user."""

    shot_id: str = Field(default="", max_length=200)
    generation_unit_id: str | None = Field(default=None, min_length=1, max_length=200)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=300)
    saved_route_source: H3SavedRouteSource | None = Field(default=None, description="Actively reuse one exact saved route decision after checking its prompt against this current group. Reads its unchanged prompt, format and ordered media; no language call. Mutually exclusive with candidate/prompt/media/take choices. Current facts and files are revalidated and saved as a new source.")
    reuse_take_id: str | None = Field(default=None, min_length=1, max_length=200)
    reference_selection_candidate_ids: list[str] | None = None
    camera_previs_motion: H3CameraMotionPreset | None = None
    prompt_override: str | None = Field(default=None, min_length=1, max_length=7000,
        description="Exact compiled prompt for an explicit generation_unit_id + candidate_id. Not supported by a shot_id-only directive.")
    media_inputs: list[H3MediaInput] | None = Field(default=None, max_length=12,
        description="Ordered authored media for an explicit generation_unit_id + candidate_id. Not supported by a shot_id-only directive.")

    @model_validator(mode="after")
    def validate_route_choice(self) -> H3ShotRouteDirective:
        if bool(self.shot_id) == (self.generation_unit_id is not None):
            raise ValueError(
                "An H3 route directive must target exactly one shot or generation unit"
            )
        if self.saved_route_source is not None:
            if self.generation_unit_id != self.saved_route_source.generation_unit_id:
                raise ValueError("Saved route reuse must target its same generation unit")
            if any(value is not None for value in (self.candidate_id, self.prompt_override, self.media_inputs,
                    self.reuse_take_id, self.reference_selection_candidate_ids, self.camera_previs_motion)):
                raise ValueError("saved_route_source is exclusive with candidate, prompt, media, take and guide choices")
        if self.prompt_override is not None or self.media_inputs is not None:
            if self.generation_unit_id is None or self.candidate_id is None:
                raise ValueError(
                    "Authored prompt/media requires an explicit generation-unit candidate"
                )
        if self.candidate_id is not None and self.reuse_take_id is not None:
            raise ValueError(
                "An H3 shot route directive may choose candidate_id or reuse_take_id, not both"
            )
        if (
            self.reference_selection_candidate_ids is not None
            and len(self.reference_selection_candidate_ids)
            != len(set(self.reference_selection_candidate_ids))
        ):
            raise ValueError("Reference selection candidate IDs must be unique")
        return self


class H3ProductionRoutePlanRequest(DomainModel):
    resolution: str | None = None
    aspect_ratio: str | None = None
    source_operation_id: str | None = Field(default=None, min_length=1)
    shot_ids: list[str] = Field(default_factory=list[str], max_length=500)
    generation_plan_document_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    generation_plan_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    unit_ids: list[str] = Field(default_factory=list[str], max_length=500)
    use_language_agent: bool = True
    candidate_count: int = Field(
        default=1,
        ge=1,
        description="Number of candidates the calling Agent requests in this action.",
    )
    consider_video_guides: bool = True
    shot_directives: list[H3ShotRouteDirective] = Field(
        default_factory=list[H3ShotRouteDirective],
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_shot_directives(self) -> H3ProductionRoutePlanRequest:
        reused = [item for item in self.shot_directives if item.saved_route_source is not None]
        if reused and (len(self.shot_directives) != 1 or self.unit_ids != [reused[0].generation_unit_id]
                       or self.source_operation_id is not None):
            raise ValueError("Saved route reuse requires one explicit unit/directive and captures a new source operation")
        generation_plan_selected = any(
            (
                self.generation_plan_document_id is not None,
                self.generation_plan_version_id is not None,
                bool(self.unit_ids),
            )
        )
        if generation_plan_selected and self.shot_ids:
            raise ValueError(
                "Choose generation_plan_document_id/version_id/unit_ids or shot_ids, not both"
            )
        if (
            self.generation_plan_document_id is None
        ) != (self.generation_plan_version_id is None):
            raise ValueError(
                "generation_plan_document_id and generation_plan_version_id are required together"
            )
        if self.unit_ids and self.generation_plan_document_id is None:
            raise ValueError("unit_ids require a generation plan document and version")
        if len(self.unit_ids) != len(set(self.unit_ids)):
            raise ValueError("H3 generation-unit IDs must be unique")
        directive_ids = [
            item.shot_id or item.generation_unit_id or ""
            for item in self.shot_directives
        ]
        if len(directive_ids) != len(set(directive_ids)):
            raise ValueError("H3 route directives must use unique targets")
        if generation_plan_selected and any(
            bool(item.shot_id) for item in self.shot_directives
        ):
            raise ValueError(
                "Generation-plan route directives must target generation_unit_id"
            )
        if not generation_plan_selected and any(
            item.generation_unit_id is not None for item in self.shot_directives
        ):
            raise ValueError("Shot route directives must target shot_id")
        return self


class H3RouteSourceCheck(DomainModel):
    route_plan_id: str
    state_revision: int
    generation_unit_id: str | None = None
    shot_id: str
    checked_at: str
    status: Literal["CURRENT", "STALE", "UNKNOWN"]
    scope: Literal["SAVED_OPERATION_SOURCE", "CURRENT_INPUTS_ONLY"]
    reason: str | None = None


class H3ProductionRoutePlanResponse(DomainModel):
    plan: H3ProductionRoutePlan
    source_checks: list[H3RouteSourceCheck] = Field(default_factory=list[H3RouteSourceCheck])


class H3ProductionRoutePlanListResponse(DomainModel):
    plans: list[H3ProductionRoutePlan]
    source_checks: list[H3RouteSourceCheck] = Field(default_factory=list[H3RouteSourceCheck])


class H3ProductionRouteExecuteRequest(DomainModel):
    expected_state_revision: int = Field(
        ge=1,
        description=(
            "Exact persisted H3 route-plan state revision this execution continues from."
        ),
    )
    reuse_passed_takes: bool = True
    enqueue_new_generations: bool = False
    inspect_reused_takes: bool = True
    semantic_review: bool = True
    rebuild_manifest: bool = True
    render_export: bool = False


class H3ProductionRouteExecuteResponse(DomainModel):
    plan: H3ProductionRoutePlan
    project: ProjectAggregate
    queued_tasks: list[GenerationTask] = Field(default_factory=list[GenerationTask])
    inspections: list[VideoInspectionReport] = Field(
        default_factory=list[VideoInspectionReport]
    )
    manifest: TimelineManifest | None = None
    export: ExportRecord | None = None


class H3UrlValue(DomainModel):
    url: str


class H3ContentItem(DomainModel):
    type: str
    text: str | None = None
    image_url: H3UrlValue | None = None
    video_url: H3UrlValue | None = None
    audio_url: H3UrlValue | None = None
    role: H3MediaRole | None = None


class H3VideoRequest(DomainModel):
    model: str = "MiniMax-H3"
    content: list[H3ContentItem]
    resolution: str = "768P"
    duration: int = Field(ge=4, le=15)
    ratio: str | None = None


class H3CreateResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    trace_id: str | None = None


class H3TaskError(DomainModel):
    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    message: str | None = None


class H3TaskContent(DomainModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    prompt: str | None = None


class H3TaskUsage(DomainModel):
    model_config = ConfigDict(extra="ignore")

    total_seconds: int | None = None
    input_seconds: int | None = None
    output_seconds: int | None = None
    input_image_count: int | None = None
    input_audio_seconds: int | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class H3RemoteTask(DomainModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    model: str = "MiniMax-H3"
    status: H3RemoteStatus
    error: H3TaskError | None = None
    created_at: int | None = None
    updated_at: int | None = None
    content: H3TaskContent | None = None
    resolution: str | None = None
    duration: int | None = None
    usage: H3TaskUsage | None = None
    ratio: str | None = None
    task_type: str | None = None
    modality: str | None = None


class H3QueryResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    task: H3RemoteTask


class H3ListResponse(DomainModel):
    model_config = ConfigDict(extra="ignore")

    items: list[H3RemoteTask] = Field(default_factory=list[H3RemoteTask])
    total: int = 0


class H3DeleteResponse(DomainModel):
    task_id: str
    action: str
    status: str


class H3UploadedInput(DomainModel):
    local_path: str
    provider_file_id: str
    media_kind: H3MediaKind
    role: H3MediaRole
    expires_at: str


class H3PricingSnapshot(DomainModel):
    currency: str = "USD"
    output_per_second: float = 0.08
    reference_video_per_second: float = 0.08
    included_images: int = 5
    additional_image_each: float = 0.04
    reference_audio_each: float = 0.0
    captured_at: str
    source_url: str = "https://platform.minimax.io/docs/guides/pricing-paygo#video"
    billing_unit: str = "ACE_CREDIT"
    billing_resolution: str = "768P"
    billing_output_per_second: float = 0.60070822
    billing_source_url: str = (
        "https://platform.acedata.cloud/api/v1/services/"
        "2add8a2a-3577-4a0f-8fd4-e08aaabe8290"
    )


class H3ProviderRun(DomainModel):
    aspect_ratio: str | None = None
    id: str
    task_id: str
    project_id: str
    shot_id: str
    generation_plan_document_id: str | None = None
    generation_plan_version_id: str | None = None
    storyboard_document_id: str | None = None
    storyboard_version_id: str | None = None
    generation_unit_id: str | None = None
    clip_group_id: str | None = None
    output_slot_id: str | None = None
    shot_ids: list[str] = Field(default_factory=list[str])
    shot_windows: list[GenerationUnitShotWindow] = Field(
        default_factory=list[GenerationUnitShotWindow]
    )
    route: VideoGenerationRoute
    purpose: EvidencePurpose
    learning_objective: str | None = None
    regression_case_id: str | None = None
    prompt: str
    media_inputs: list[H3MediaInput] = Field(default_factory=list[H3MediaInput])
    request: H3VideoRequest | None = None
    # None preserves conservative recovery for runs written before this marker.
    # PREPARED is only local input work; STARTED is persisted before provider IO.
    submission_attempt_state: Literal["PREPARED", "STARTED"] | None = None
    uploaded_inputs: list[H3UploadedInput] = Field(default_factory=list[H3UploadedInput])
    latest_remote_task: H3RemoteTask | None = None
    pricing: H3PricingSnapshot
    estimated_cost_usd: float | None = None
    estimated_cost_credits: float | None = None
    output_url: str | None = None
    downloaded_path: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str
    updated_at: str


class H3ProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, min_length=1)
    clear_api_key: bool = False


class H3ProviderConfigResponse(DomainModel):
    supported_resolutions: list[str] = Field(default_factory=lambda: ["768P", "2K"])
    supported_aspect_ratios: list[str] = Field(default_factory=lambda: ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"])
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    base_url: str = "https://api.acedata.cloud"
    resolution: str = "768P"
    has_api_key: bool
    key_source: str
    can_submit: bool
    pricing: H3PricingSnapshot


class H3ReconcileRequest(DomainModel):
    provider_task_id: str = Field(min_length=1)


class H3NotSubmittedResolutionRequest(DomainModel):
    reason: str = Field(min_length=1, max_length=2000)


class H3TakeReviewRequest(DomainModel):
    hard_gate_passed: bool
    score: float = Field(ge=0.0, le=5.0)
    notes: str = Field(min_length=1)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue])

    @model_validator(mode="after")
    def require_rejection_classification(self) -> H3TakeReviewRequest:
        if not self.hard_gate_passed and not self.issues:
            raise ValueError("A rejected H3 Take requires at least one classified QA issue")
        return self


class H3TaskActionResponse(DomainModel):
    task: GenerationTask
    project: ProjectAggregate
    provider_run: H3ProviderRun


class H3ProbeResponse(H3TaskActionResponse):
    pass


class H3CompilePreviewResponse(DomainModel):
    prompt: str
    request: H3VideoRequest


class H3ProviderRunsResponse(DomainModel):
    runs: list[H3ProviderRun]


class H3RemoteTaskSummary(DomainModel):
    id: str
    status: H3RemoteStatus
    error: H3TaskError | None = None


class H3ProviderRunIndexItem(DomainModel):
    id: str
    task_id: str
    project_id: str
    shot_id: str
    generation_unit_id: str | None = None
    shot_ids: list[str] = Field(default_factory=list[str])
    route: VideoGenerationRoute
    task_status: TaskStatus | None = None
    take_ids: list[str] = Field(default_factory=list[str])
    remote: H3RemoteTaskSummary | None = None
    prompt_characters: int = Field(ge=0)
    media_input_count: int = Field(ge=0)
    downloaded_path: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str
    updated_at: str


class H3ProviderRunIndexResponse(DomainModel):
    """Discovery metadata; full requests stay in the exact ProviderRun record."""

    runs: list[H3ProviderRunIndexItem]
    details_tool: Literal["route.get_h3_run"] = "route.get_h3_run"
    task_state_tool: Literal["task.get"] = "task.get"


class H3ReconciliationCandidatesResponse(DomainModel):
    tasks: list[H3RemoteTask]


class H3TakeReviewResponse(DomainModel):
    take: Take
    evidence: RouteEvidence
    project: ProjectAggregate
