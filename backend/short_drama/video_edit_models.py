"""One existing-video lip-sync operation, its durable execution and explicit adoption."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, JsonValue, model_validator
from short_drama.models import DomainModel, GenerationTask, ProjectAggregate

VideoEditStatus = Literal["QUEUED", "PREPARED", "SUBMITTING", "SUBMIT_UNKNOWN", "RUNNING", "SUCCEEDED", "FAILED", "PAUSED", "CANCELLED", "OUTPUT_MISSING"]


class LipSyncAudioSource(DomainModel):
    clip_id: str = Field(min_length=1, description="One existing audible audio/video clip in the same timeline, covering the full FILM selection. Omit audio_source to use the target's current original sound.")


class LipSyncRequest(DomainModel):
    shot_id: str
    timeline_id: str
    clip_id: str
    start_seconds: float = Field(ge=0, allow_inf_nan=False, description="Selection start in saved FILM coordinates, inside this exact video clip.")
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    expected_editorial_revision: int = Field(ge=1)
    audio_source: LipSyncAudioSource | None = None
    intent: str = Field(min_length=1, max_length=3000)
    preserve: list[str] = Field(default_factory=list[str], max_length=20)

    @model_validator(mode="after")
    def valid_duration(self) -> LipSyncRequest:
        if not 2 <= self.end_seconds - self.start_seconds <= 10:
            raise ValueError("口型修复选择须为同一画面片段内的 2–10 秒。")
        return self


class VideoEditSourceFile(DomainModel):
    path: str
    size_bytes: int
    modified_ns: int


class VideoEditRun(DomainModel):
    id: str
    task_id: str
    project_id: str
    shot_id: str
    kind: Literal["LIP_SYNC"] = "LIP_SYNC"
    provider: Literal["kling"] = "kling"
    model: str = "kling-lip-sync"
    status: VideoEditStatus = "QUEUED"
    request: LipSyncRequest
    source_take_id: str
    source_media_path: str
    source_in_seconds: float
    source_out_seconds: float
    source_speed: float
    source_reversed: bool
    source_files: list[VideoEditSourceFile]
    target_clip: dict[str, JsonValue]
    target_track: dict[str, JsonValue]
    audio_clip: dict[str, JsonValue]
    audio_track: dict[str, JsonValue]
    audio_media_path: str
    audio_asset_type: Literal["video", "audio"]
    # Only the relevant binding and sound selection, never the whole editorial revision.
    baseline: dict[str, JsonValue]
    prepared_video_path: str | None = None
    prepared_audio_path: str | None = None
    prepared_width: int | None = None
    prepared_height: int | None = None
    prepared_fps: float | None = None
    prepared_duration_seconds: float | None = None
    prepared_audio_sha256: str | None = None
    uploaded_video_url: str | None = None
    upload_http_status: int | None = None
    upload_error_detail: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    last_error_stage: Literal["prepare", "upload", "submit", "query", "download"] | None = None
    preparation_retry_count: int = Field(default=0, ge=0)
    submission_attempt_state: Literal["NOT_STARTED", "PREPARED", "STARTED", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"] = "NOT_STARTED"
    provider_task_id: str | None = None
    submission_http_status: int | None = None
    submission_response: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    latest_remote: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    output_url: str | None = None
    candidate_media_path: str | None = None
    candidate_take_id: str | None = None
    candidate_thumbnail_path: str | None = None
    quality_status: Literal["NOT_INSPECTED", "TECHNICAL_FAILED", "AWAITING_REVIEW"] = "NOT_INSPECTED"
    adoption_status: Literal["NOT_ADOPTED", "ADOPTED", "SOURCE_CHANGED"] = "NOT_ADOPTED"
    adopted_clip_ids: list[str] = Field(default_factory=list[str])
    adoption_notes: list[str] = Field(default_factory=list[str])
    paused_after_submit: bool = False
    control_revision: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    query_failures: int = 0
    next_poll_at: str | None = None
    actual_usage: dict[str, JsonValue] | None = None
    usage_status: Literal["NOT_PROVIDED", "PROVIDED"] = "NOT_PROVIDED"
    phase_seconds: dict[str, float] = Field(default_factory=dict[str, float])
    created_at: str
    updated_at: str
    submitted_at: str | None = None
    finished_at: str | None = None
    adopted_at: str | None = None


class LipSyncResponse(DomainModel):
    task: GenerationTask
    run: VideoEditRun
    warnings: list[str] = Field(default_factory=list[str])


class LipSyncAdoptResponse(DomainModel):
    run: VideoEditRun
    project: ProjectAggregate
    notices: list[str] = Field(default_factory=list[str])


class LipSyncReconcileRequest(DomainModel):
    provider_task_id: str | None = Field(default=None, min_length=1,
        description="Explicit known task ID only. If local ID is unknown, the remote request must exactly match prepared video and audio. No broad account list lookup.")
