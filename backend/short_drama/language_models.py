"""Configuration contracts for the shared language provider."""

from __future__ import annotations

from short_drama.skill_ids import SkillIds

from pydantic import Field, JsonValue, model_validator

from short_drama.h3_models import H3MediaInput
from short_drama.production_contracts import ClipGroup
from short_drama.language_defaults import LANGUAGE_MODEL, LANGUAGE_REASONING_EFFORT
from short_drama.models import DomainModel, H3PictureOnlyTarget, VideoGenerationRoute


class LanguageProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    clear_api_key: bool = False


class LanguageProviderConfigResponse(DomainModel):
    provider: str = "openai-compatible"
    model: str = LANGUAGE_MODEL
    reasoning_effort: str = LANGUAGE_REASONING_EFFORT
    base_url: str = "https://api.openai.com/v1"
    has_api_key: bool
    key_source: str
    can_connect: bool


class H3GenerationUnitContextRequest(DomainModel):
    generation_plan_document_id: str = Field(min_length=1)
    generation_plan_version_id: str = Field(min_length=1)
    generation_unit_id: str = Field(min_length=1)


class H3GenerationUnitMediaCandidate(DomainModel):
    media_input: H3MediaInput
    reference_role: str
    serves_shot_ids: list[str]


class H3CurrentSourceDocument(DomainModel):
    document_id: str
    version_id: str
    version: int


class H3GenerationUnitContextResponse(DomainModel):
    project_id: str
    generation_plan_document_id: str
    generation_plan_version_id: str
    generation_plan_version: int
    generation_unit_id: str
    source_status: str = Field(default="CURRENT_PLAN", description="CURRENT_PLAN or CURRENT_PROJECTION. GenerationPlan identity above is the requested saved source, never an invented version for projected current facts.")
    source_changes: list[str] = Field(default_factory=list[str])
    current_clip_group: ClipGroup | None = None
    current_storyboard: H3CurrentSourceDocument | None = None
    current_production_spec: H3CurrentSourceDocument | None = None
    source_context: dict[str, JsonValue]
    media_candidates: list[H3GenerationUnitMediaCandidate]
    warnings: list[str] = Field(default_factory=list[str])
    selection_guidance: str = (
        "Candidates are an inventory, not a request. Choose the route and the "
        "smallest sufficient ordered subset for this unit. Source locators come "
        "from current usable AssetVersions explicitly selected for the task; copy them without guessing. "
        "SUBJECT_IDENTITY/SCENE_IDENTITY/PROP_IDENTITY images transfer reusable content. "
        "COMPOSITION/LAYOUT_GUIDE reference_image inputs are soft Picture anchors for staging, "
        "visible state or shot order, not extra people or hard first/last frames. "
        "For reliable starting composition, derive and inspect a complete scene frame with the "
        "existing image tools, then choose FRAME_FIRST with only that first_frame image. "
        "H3 cannot mix hard first/last-frame roles with reference media. "
        "Use video.extract_frame for a real Take frame when continuity benefits; source defects "
        "must be corrected before propagating it. No keyframe count or extra review round is mandatory. "
        "Read the next unit separately; full project and prior-stage reads are unnecessary."
    )


class LanguagePromptCompileRequest(DomainModel):
    picture_only: H3PictureOnlyTarget | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    shot_id: str | None = Field(default=None, min_length=1)
    generation_plan_document_id: str | None = Field(default=None, min_length=1)
    generation_plan_version_id: str | None = Field(default=None, min_length=1)
    generation_unit_id: str | None = Field(default=None, min_length=1)
    method_skill_ids: SkillIds = Field(default_factory=list[str])
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    duration_seconds: int | None = Field(
        default=None, ge=4, le=15,
        description="For a generation-unit target, omit to use ceil(unit.requested_duration_seconds), or pass that exact value. This field does not add source handles or change the saved unit. Save any intended shot/group timing change first, then prepare from that current source. A single-shot target uses its own duration rules.",
    )
    source_prompt: str | None = Field(
        default=None, max_length=32000,
        description="Optional complete prior draft to revise. Drafts may exceed the final H3 limit; only a validated output can be saved for execution.",
    )
    revision_instructions: str | None = Field(
        default=None, min_length=1, max_length=2000,
        description="Agent feedback for this compilation, including a prior failure. May revise wording, never locked facts or media order.",
    )
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
        description="The exact ordered media selected for this compilation. Unselected prose references remain saved; their omission does not change their usage or prove coverage. Use prepare_h3_group with media_inputs omitted for automatic current-reference expansion.",
    )

    @model_validator(mode="after")
    def validate_target(self) -> LanguagePromptCompileRequest:
        if self.picture_only is not None and self.shot_id is None:
            raise ValueError("PICTURE_ONLY compiles one saved shot occurrence")
        unit_target = (
            self.generation_plan_document_id,
            self.generation_plan_version_id,
            self.generation_unit_id,
        )
        if any(value is not None for value in unit_target):
            if self.shot_id is not None or not all(unit_target):
                raise ValueError(
                    "Choose one shot_id or a complete generation plan document/version/unit target"
                )
        elif self.shot_id is None:
            raise ValueError("A shot or generation-unit target is required")
        if any(not value.strip() for value in self.method_skill_ids) or len(
            self.method_skill_ids
        ) != len(set(self.method_skill_ids)):
            raise ValueError("Method skill IDs must be non-empty and unique")
        return self


class LanguagePromptCompileResponse(DomainModel):
    resolution: str = "768P"
    aspect_ratio: str | None = None
    candidate_id: str | None = None
    provider: str = "openai-compatible"
    model: str = LANGUAGE_MODEL
    reasoning_effort: str = LANGUAGE_REASONING_EFFORT
    shot_id: str | None = None
    generation_plan_document_id: str | None = None
    generation_plan_version_id: str | None = None
    generation_unit_id: str | None = None
    shot_ids: list[str] = Field(default_factory=list[str])
    method_skill_ids: SkillIds = Field(default_factory=list[str])
    media_inputs: list[H3MediaInput] = Field(default_factory=list[H3MediaInput])
    route: VideoGenerationRoute
    duration_seconds: int
    source_prompt: str
    compiled_prompt: str
    validation_notes: list[str]
