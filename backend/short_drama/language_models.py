"""Configuration contracts for the fixed GPT-5.6-sol language provider."""

from __future__ import annotations

from pydantic import Field

from short_drama.models import DomainModel


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
