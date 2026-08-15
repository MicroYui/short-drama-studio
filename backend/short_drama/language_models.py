"""Configuration contracts for the fixed GPT-5.6-sol language provider."""

from __future__ import annotations

from pydantic import Field

from short_drama.h3_models import H3MediaInput
from short_drama.models import DomainModel, VideoGenerationRoute


class LanguageProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    clear_api_key: bool = False


class LanguageProviderConfigResponse(DomainModel):
    provider: str = "openai-compatible"
    model: str = "gpt-5.6-sol"
    base_url: str = "https://api.openai.com/v1"
    has_api_key: bool
    key_source: str
    can_connect: bool


class LanguagePromptCompileRequest(DomainModel):
    shot_id: str
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT
    duration_seconds: int | None = Field(default=None, ge=4, le=15)
    source_prompt: str | None = Field(default=None, max_length=7000)
    media_inputs: list[H3MediaInput] = Field(
        default_factory=list[H3MediaInput],
        max_length=12,
    )


class LanguagePromptCompileResponse(DomainModel):
    provider: str = "openai-compatible"
    model: str = "gpt-5.6-sol"
    shot_id: str
    route: VideoGenerationRoute
    duration_seconds: int
    source_prompt: str
    compiled_prompt: str
    validation_notes: list[str]
