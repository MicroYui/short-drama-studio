"""Current-content commands for the product workspace."""

from __future__ import annotations

from short_drama.skill_ids import SkillIds

from typing import Literal
from pydantic import Field, JsonValue, model_validator

from short_drama.models import DomainModel, ProjectAggregate, ProjectRecord, ShotPlan, ScriptLine


class StudioEpisodeCreate(DomainModel):
    title: str = Field(default="新的一集", min_length=1, max_length=120)


class StudioEpisodes(DomainModel):
    project_id: str
    episodes: list[ProjectRecord]


class StudioSceneSave(DomainModel):
    scene_id: str | None = None
    heading: str = Field(min_length=1, max_length=240)
    screenplay_text: str = Field(max_length=50000)
    expected_text: str | None = None
    lines: list[ScriptLine] | None = None
    summary: str | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0)
    narrative_purpose: str | None = None
    affected_shot_ids: list[str] | None = Field(default=None, description=(
        "After reading current shots, identify which shots need a visual update for this scene edit. "
        "Omit when impact is unknown to refresh all shots in the scene. An empty list means no visual change. "
        "Shots using changed structured dialogue are always included."
    ))


class StudioShotSave(DomainModel):
    shot: ShotPlan
    expected_revision: int = Field(ge=1)


class StudioSceneOutline(DomainModel):
    heading: str = Field(min_length=1, max_length=240)
    narrative_purpose: str = Field(min_length=1, max_length=2000)
    target_duration_seconds: float | None = Field(default=None, gt=0)


class StudioTimelineSave(DomainModel):
    timeline: dict[str, JsonValue]
    expected_revision: int = Field(default=0, ge=0)
    anchor_clip_id: str | None = None

    @model_validator(mode="after")
    def accept_editor_project(self) -> StudioTimelineSave:
        # The reader exposes the editor project directly as well as in a saved
        # timeline record. Both forms describe the same content.
        if ("project" not in self.timeline
                and isinstance(self.timeline.get("timelines"), list)
                and isinstance(self.timeline.get("assets"), list)):
            self.timeline = {"project": self.timeline}
        return self


class EditorialClipChange(DomainModel):
    id: str
    start_seconds: float | None = Field(default=None, ge=0)
    source_in_seconds: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0, le=1)
    muted: bool | None = None
    speed: float | None = Field(default=None, ge=0.1, le=16, allow_inf_nan=False)
    remove: bool = False


class StudioEditorialEdit(DomainModel):
    expected_revision: int = Field(ge=0)
    clips: list[EditorialClipChange] = Field(default_factory=list[EditorialClipChange], max_length=200)
    keep_linked_audio: bool = True
    shot_id: str | None = None
    anchor_clip_id: str | None = None


class StudioAssetOverlay(DomainModel):
    expected_revision: int = Field(ge=0)
    overlay_id: str = Field(min_length=1, max_length=200, description="Stable ID for this placed graphic; repeating the same placement does not duplicate it.")
    asset_version_id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    shot_id: str | None = None
    anchor_clip_id: str | None = None


class StudioMutationResult(DomainModel):
    project: ProjectAggregate
    notices: list[str] = Field(default_factory=list[str])


class StudioTextChangeInput(DomainModel):
    target: Literal["scene", "shot"]
    target_id: str
    field: str = Field(description="scene: screenplay_text/heading/summary; shot: visual/camera/framing/audio_intent or timeline_beats.<zero-based index>.<text field>")
    title: str = Field(min_length=1, max_length=160)
    before: str = Field(min_length=1, max_length=50000, description="Exact, uniquely identifiable text in the current field. Include enough surrounding text to disambiguate.")
    after: str = Field(max_length=50000)
    explanation: str = Field(default="", max_length=2000)
    lines: list[ScriptLine] | None = Field(default=None, description="Only changed/new dialogue lines from this edit. Keep stable IDs. On acceptance, other lines remain unchanged.")
    affected_shot_ids: list[str] | None = Field(default=None, description=(
        "For a scene edit, identify affected shots after reading their current content. "
        "Unlisted shots in that scene keep their current media and manual edits. "
        "Omit if the impact is unknown; use [] for no visual change. "
        "Shots using changed dialogue are always included."
    ))


class StudioTextChange(StudioTextChangeInput):
    id: str
    status: Literal["pending", "accepted", "rejected"] = "pending"
    decided_at: str | None = None


class StudioChangeRequest(DomainModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=2000)
    items: list[StudioTextChangeInput] = Field(min_length=1, max_length=30)


class StudioChangeSet(DomainModel):
    id: str
    project_id: str
    run_id: str | None = None
    title: str
    summary: str = ""
    items: list[StudioTextChange]
    created_at: str


class StudioChangeDecision(DomainModel):
    decision: Literal["accept", "reject"]


class StudioChangeResult(DomainModel):
    change: StudioChangeSet
    project: ProjectAggregate


class StudioRename(DomainModel):
    name: str = Field(min_length=1, max_length=120)


class StudioProjectCreate(DomainModel):
    name: str = Field(default="未命名作品", min_length=1, max_length=120)
    brief: str = Field(default="", max_length=50000)


class StudioScope(DomainModel):
    kind: Literal["episode", "scene", "shot", "asset", "film"] = "episode"
    id: str | None = None
    label: str = "当前集"


class StudioMessage(DomainModel):
    text: str = Field(min_length=1, max_length=12000)
    scope: StudioScope = Field(default_factory=StudioScope)


class StudioEvent(DomainModel):
    id: int
    project_id: str
    kind: str
    created_at: str
    data: dict[str, JsonValue]


class StudioJob(DomainModel):
    id: str
    project_id: str
    run_id: str | None = None
    status: Literal["queued", "running", "retrying", "pausing", "paused", "waiting_user", "completed", "failed"] = "queued"
    objective: str
    scope: StudioScope
    pending_messages: list[StudioMessage] = Field(default_factory=list[StudioMessage])
    error: str | None = None
    retry_attempt: int = Field(default=0, ge=0)
    retry_at: str | None = None
    created_at: str
    updated_at: str


class StudioWorker(DomainModel):
    id: str
    run_id: str
    role: str
    skill_role: str | None = None
    stage: str | None = None
    objective: str
    status: str
    next_owner: str | None = None
    summary: str | None = None
    tool_turns: int = 0
    latest_tool: str | None = None
    latest_reason: str | None = None


class StudioActivity(DomainModel):
    jobs: list[StudioJob]
    events: list[StudioEvent]
    workers: list[StudioWorker] = Field(default_factory=list[StudioWorker])
    context_window_tokens: int = 258000


class StudioJobControl(DomainModel):
    action: Literal["pause", "resume"]


class StudioShotMediaSelect(DomainModel):
    asset_version_id: str
    expected_revision: int = Field(ge=1)


class StudioPreferences(DomainModel):
    method_skill_ids: SkillIds = Field(default_factory=list[str], max_length=11)
    creative_direction: str = Field(default="", max_length=12000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"]


class StudioShotOccurrence(DomainModel):
    anchor_clip_id: str
    start_seconds: float
    duration_seconds: float


class StudioEditorialScope(DomainModel):
    shot_id: str
    anchor_clip_id: str | None
    film_start_seconds: float
    duration_seconds: float
    occurrences: list[StudioShotOccurrence]


class StudioEditorial(DomainModel):
    revision: int
    project: dict[str, JsonValue]
    scope: StudioEditorialScope | None = None


class StudioExportRequest(DomainModel):
    expected_revision: int = Field(ge=1)
    width: int = Field(default=1280, ge=256, le=3840)
    height: int = Field(default=720, ge=256, le=3840)
    fps: int = Field(default=24, ge=12, le=60)
    subtitles: bool = True


class StudioExport(DomainModel):
    id: str
    project_id: str
    editorial_revision: int
    status: Literal["running", "completed", "failed"] = "running"
    progress: str = "准备导出"
    video_path: str | None = None
    clean_video_path: str | None = None
    srt_path: str | None = None
    input_path: str | None = None
    error: str | None = None
    duration_seconds: float = 0
    width: int
    height: int
    fps: int
    created_at: str
