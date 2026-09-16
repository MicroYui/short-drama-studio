"""Current-content commands for the product workspace."""

from __future__ import annotations

from short_drama.skill_ids import SkillIds

from typing import Literal
from pydantic import Field, JsonValue, model_validator

from short_drama.models import DomainModel, ProjectAggregate, ProjectRecord, ShotPlan, ScriptLine, SceneContinuitySummary


class StudioEpisodeCreate(DomainModel):
    title: str = Field(default="新的一集", min_length=1, max_length=120)


class StudioEpisodes(DomainModel):
    project_id: str
    episodes: list[ProjectRecord]


class StudioSceneContent(DomainModel):
    heading: str = Field(min_length=1, max_length=240)
    screenplay_text: str = Field(max_length=50000)
    lines: list[ScriptLine] | None = None
    summary: str | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0)
    narrative_purpose: str | None = None
    entry_state: SceneContinuitySummary | None = Field(default=None, description=(
        "Relevant state established by the current screenplay at scene entry, not an unfulfilled outline. "
        "Omit to preserve; explicitly null to remove an obsolete state summary."
    ))
    exit_state: SceneContinuitySummary | None = Field(default=None, description=(
        "Relevant facts, prop/character states and open threads actually established by this scene's text. "
        "Omit to preserve; explicitly null to remove an obsolete state summary."
    ))
    affected_shot_ids: list[str] | None = Field(default=None, description=(
        "After reading current shots, identify which shots need a visual update for this scene edit. "
        "Omit when impact is unknown to refresh all shots in the scene. An empty list means no visual change. "
        "Shots using changed structured dialogue are always included."
    ))


class StudioSceneEdit(StudioSceneContent):
    scene_id: str | None = None
    expected_text: str | None = None


class StudioRelatedSceneUpdate(StudioSceneContent):
    scene_id: str
    expected_text: str = Field(description="Exact current body read from this related scene; protects concurrent edits.")
    reason: str = Field(min_length=1, max_length=1000, description=(
        "Concrete semantic connection to the main edit, such as a setup/payoff, shared fact or prop state."
    ))


class StudioSceneSave(StudioSceneEdit):
    related_scene_updates: list[StudioRelatedSceneUpdate] = Field(default_factory=list[StudioRelatedSceneUpdate], description=(
        "Optional narrowly related scene edits saved atomically with this scene. Use only when the current "
        "change affects an earlier setup, later payoff or established fact. Read each current body first; "
        "update its summary/purpose/state when affected. Do not include unchanged or unrelated scenes."
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


class StudioTakeSelectRequest(DomainModel):
    shot_ids: list[str] | None = None
    clip_ids: list[str] | None = None
    expected_editorial_revision: int | None = Field(default=None, ge=0)
    source_in_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False,
        description="Optional exact start in the candidate Take's source file. Supply both source bounds, one clip_id and the current editorial revision; preserves that use's film position and duration.")
    source_out_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False,
        description="Exclusive candidate source end. The span must equal the selected clip's duration multiplied by its existing speed; subtitles and all unselected uses stay unchanged.")

    @model_validator(mode="after")
    def validate_source_selection(self) -> StudioTakeSelectRequest:
        if (self.source_in_seconds is None) != (self.source_out_seconds is None):
            raise ValueError("source_in_seconds and source_out_seconds must be supplied together")
        if self.source_in_seconds is not None and self.source_out_seconds is not None:
            if self.source_out_seconds <= self.source_in_seconds:
                raise ValueError("source_out_seconds must follow source_in_seconds")
            if self.clip_ids is None or len(self.clip_ids) != 1:
                raise ValueError("An exact source interval requires exactly one clip_id")
            if self.expected_editorial_revision is None:
                raise ValueError("An exact source interval requires expected_editorial_revision")
        return self


class EditorialTitleStyle(DomainModel):
    """The same basic title controls exposed by the Studio editor."""

    font_size: float | None = Field(default=None, ge=1, le=500, allow_inf_nan=False,
        description="Title size in a 1080-unit-high canvas, independent of export resolution.")
    font_weight: Literal["normal", "bold"] | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    position_x: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    position_y: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    opacity: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class EditorialTransition(DomainModel):
    type: Literal["none", "fade-to-black", "dissolve"]
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False,
        description="Positive for a fade/dissolve, no longer than either affected clip. Use none with 0 to clear.")

    @model_validator(mode="after")
    def validate_duration(self) -> EditorialTransition:
        if (self.type == "none") != (self.duration_seconds == 0):
            raise ValueError("A transition needs positive duration_seconds; none uses 0.")
        return self


class EditorialTitleCreate(DomainModel):
    id: str = Field(min_length=1, max_length=200, description="New title clip ID, unique within the selected timeline. Existing titles use clips by ID.")
    text: str = Field(min_length=1, pattern=r"^[^{}\\]*$",
        description="Chinese and multiline text supported; braces and backslashes are not supported by the basic renderer.")
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    style: EditorialTitleStyle = Field(default_factory=EditorialTitleStyle)
    track_index: int | None = Field(default=None, ge=0,
        description="Unoccupied enabled visual track. Omit to choose one or add a title track without changing existing clips.")


class EditorialClipChange(DomainModel):
    id: str
    start_seconds: float | None = Field(default=None, ge=0)
    source_in_seconds: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0, le=1)
    muted: bool | None = None
    speed: float | None = Field(default=None, ge=0.1, le=16, allow_inf_nan=False)
    text: str | None = Field(default=None, min_length=1, pattern=r"^[^{}\\]*$", description="Only for an existing text/title clip; omit to preserve its text.")
    title_style: EditorialTitleStyle | None = Field(default=None, description="Only supplied basic title fields are merged; other saved style fields stay unchanged.")
    transition_in: EditorialTransition | None = None
    transition_out: EditorialTransition | None = Field(default=None,
        description="Video/image only. Dissolve updates both ends of same-track adjacent clips; none clears the paired dissolve. Cross-shot cuts use FILM scope.")
    remove: bool = False


class EditorialSubtitleChange(DomainModel):
    id: str = Field(min_length=1)
    start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    text: str | None = None
    style: dict[str, JsonValue] | None = None
    remove: bool = False


class EditorialSubtitleCreate(DomainModel):
    id: str = Field(min_length=1, max_length=200, description="New cue ID, unique in the selected timeline. Use subtitles to edit or remove an existing ID.")
    text: str = Field(min_length=1)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    style: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    track_index: int | None = Field(default=None, ge=0, description="Existing subtitle track index. Omit to use an enabled subtitle track or create one without changing other tracks.")

    @model_validator(mode="after")
    def validate_interval(self) -> EditorialSubtitleCreate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds.")
        return self


class StudioEditorialEdit(DomainModel):
    expected_revision: int = Field(ge=0)
    timeline_id: str | None = Field(default=None, min_length=1,
        description="Exact saved timeline from editor_selection/read_editorial. Omit only for unambiguous IDs on the current active timeline; duplicate IDs across timelines require this field. Explicit targets preserve the active selection.")
    clips: list[EditorialClipChange] = Field(default_factory=list[EditorialClipChange], max_length=200)
    subtitles: list[EditorialSubtitleChange] = Field(default_factory=list[EditorialSubtitleChange], max_length=200)
    add_subtitles: list[EditorialSubtitleCreate] = Field(default_factory=list[EditorialSubtitleCreate], max_length=200,
        description="Create captions without reading/writing the full project. Times use FILM coordinates unless shot_id selects SHOT_LOCAL; local cues must fit the edited shot use. Spoken captions should come from actual audio.transcribe_native results, with only supported recognition corrections, not unheard script lines.")
    add_titles: list[EditorialTitleCreate] = Field(default_factory=list[EditorialTitleCreate], max_length=100,
        description="Create basic Chinese text overlays with no media asset, independently of spoken subtitles. Times use FILM or the selected SHOT_LOCAL view; local titles must fit the edited shot use.")
    keep_linked_audio: bool = True
    shot_id: str | None = None
    anchor_clip_id: str | None = None

    @model_validator(mode="after")
    def validate_edit_scope(self) -> StudioEditorialEdit:
        if self.anchor_clip_id and not self.shot_id:
            raise ValueError("anchor_clip_id requires shot_id; FILM edits use timeline_id without either field.")
        added_ids = [cue.id for cue in self.add_subtitles]
        if len(set(added_ids)) != len(added_ids):
            raise ValueError("add_subtitles IDs must be unique within this request.")
        if set(added_ids).intersection(cue.id for cue in self.subtitles):
            raise ValueError("A subtitle ID cannot be both added and edited/removed in one request.")
        title_ids = [title.id for title in self.add_titles]
        if len(set(title_ids)) != len(title_ids):
            raise ValueError("add_titles IDs must be unique within this request.")
        if set(title_ids).intersection(clip.id for clip in self.clips):
            raise ValueError("A title ID cannot be both added and edited/removed in one request.")
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("clips IDs must be unique within this request; combine changes for one clip.")
        return self


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
    changed_scene_ids: list[str] = Field(default_factory=list[str])
    changed_shot_ids: list[str] = Field(default_factory=list[str])


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
    work_order_id: str | None = None
    title: str
    summary: str = ""
    items: list[StudioTextChange]
    created_at: str


class StudioChanges(DomainModel):
    changes: list[StudioChangeSet]
    next_cursor: str | None = None


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


class StudioEditorSelectedClip(DomainModel):
    id: str = Field(min_length=1)
    asset_id: str | None = None
    type: Literal["image", "video", "audio", "adjustment", "text"]
    source_shot_id: str | None = None
    source_take_id: str | None = None
    track_index: int = Field(ge=0)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    source_in_seconds: float = Field(ge=0, allow_inf_nan=False)
    source_window_start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source_window_end_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    speed: float = Field(default=1, gt=0, allow_inf_nan=False)
    reversed: bool = False


class StudioEditorSelectedGap(DomainModel):
    track_index: int = Field(ge=0)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self) -> StudioEditorSelectedGap:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Selected gap end_seconds must be after start_seconds")
        return self


class StudioEditorSelection(DomainModel):
    """Saved editor selection, not an instruction to mutate every selected item."""

    timeline_id: str = Field(min_length=1)
    editorial_revision: int = Field(ge=0)
    coordinate_space: Literal["FILM", "SHOT_LOCAL"] = Field(
        description="Clip/playhead/marker/gap times use this view; source_in/window times always use the media file."
    )
    film_start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False,
        description="Add this offset to SHOT_LOCAL view times to locate them in the film.")
    anchor_clip_id: str | None = None
    focus: Literal["clips", "subtitle", "gap", "timeline"]
    clips: list[StudioEditorSelectedClip] = Field(default_factory=list[StudioEditorSelectedClip])
    subtitle_ids: list[str] = Field(default_factory=list[str])
    playhead_seconds: float = Field(ge=0, allow_inf_nan=False)
    in_point_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    out_point_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    gap: StudioEditorSelectedGap | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> StudioEditorSelection:
        if (self.in_point_seconds is not None and self.out_point_seconds is not None
                and self.out_point_seconds < self.in_point_seconds):
            raise ValueError("Selected out_point_seconds must not precede in_point_seconds")
        return self


class StudioScope(DomainModel):
    kind: Literal["episode", "scene", "shot", "asset", "film"] = "episode"
    id: str | None = None
    label: str = "当前集"
    asset_version_id: str | None = Field(default=None, min_length=1,
        description="Exact media file selected from project assets; does not adopt it or replace the asset's current file.")
    take_id: str | None = Field(default=None, min_length=1,
        description="Exact generated source video selected for viewing or this request; does not adopt the Take.")
    editor_selection: StudioEditorSelection | None = Field(default=None,
        description="Exact saved editor selection at handoff. Read current objects before editing; selection is context, not a fixed workflow.")


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
    current_goal: str | None = Field(
        default=None,
        description="活动视图从当前 AgentRun 读取的有效目标；原始 objective 保留用于制作记录。",
    )
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
    waiting_review: bool = False
    waiting_until: str | None = None
    latest_error: str | None = None
    completed_artifact_count: int = 0
    latest_receipt: dict[str, JsonValue] | None = None
    selected_method_skill_ids: list[str] = Field(default_factory=list[str])


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
    default_subtitle_font_family: str | None = Field(default=None,
        description="Confirmed readable local default CJK subtitle family for preview/export. Null when unavailable. Response metadata only; explicit saved font families remain unchanged.")


class StudioExportRequest(DomainModel):
    expected_revision: int = Field(ge=1)
    width: int | None = Field(default=None, ge=256,
        description="Explicit output width. Omit width and height together to retain actual selected video detail in the project aspect ratio.")
    height: int | None = Field(default=None, ge=256,
        description="Explicit output height; supply together with width, or omit both to follow selected source media.")
    fps: int = Field(default=24, ge=12, le=60)
    subtitles: bool = True

    @model_validator(mode="after")
    def paired_output_dimensions(self) -> "StudioExportRequest":
        if (self.width is None) != (self.height is None):
            raise ValueError("Supply width and height together, or omit both to follow the selected media.")
        return self


class StudioExport(DomainModel):
    id: str
    project_id: str
    editorial_revision: int
    status: Literal["running", "cancelling", "cancelled", "completed", "failed"] = "running"
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
    updated_at: str | None = None
    finished_at: str | None = None
    subtitles: bool = True


class StudioExports(DomainModel):
    exports: list[StudioExport]
    next_cursor: str | None = None
