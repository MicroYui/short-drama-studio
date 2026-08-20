"""Optional character voice-reference input contracts."""

from __future__ import annotations

from pydantic import Field

from short_drama.models import (
    Asset,
    AssetVersion,
    DomainModel,
    ProjectAggregate,
    VoiceReferenceBinding,
)


class VoiceReferenceUpdateRequest(DomainModel):
    asset_id: str | None = Field(default=None, min_length=1, max_length=200)


class VoiceReferenceMutationResponse(DomainModel):
    binding: VoiceReferenceBinding | None = None
    asset: Asset | None = None
    version: AssetVersion | None = None
    project: ProjectAggregate
