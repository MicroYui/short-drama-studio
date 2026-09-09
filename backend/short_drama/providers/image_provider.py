"""Provider-neutral interface for image generation and editing."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from short_drama.image_models import ImageProviderRequest, ImageUsage
from short_drama.models import (
    DomainModel,
    ImageReferenceAuthority,
    ImageReferenceRole,
)


class ImageProviderError(RuntimeError):
    """Base class for image provider failures."""


class ImageProviderDefiniteError(ImageProviderError):
    """A definite rejection where the provider did not accept useful work."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        same_request_retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.same_request_retryable = same_request_retryable


class ImageProviderSubmissionUnknown(ImageProviderError):
    """Transport or server failure where billing/work acceptance is ambiguous."""


class ImageProviderInput(DomainModel):
    asset_version_id: str
    image_url: str
    purpose: str | None = None
    subject_alias: str | None = None
    reference_role: ImageReferenceRole = ImageReferenceRole.SUPPORTING_VISUAL
    reference_authority: ImageReferenceAuthority = (
        ImageReferenceAuthority.SUPPORTING
    )


class ImageProviderResult(DomainModel):
    image_base64: str = Field(min_length=1)
    mime_type: str
    provider_request_id: str | None = None
    revised_prompt: str | None = None
    usage: ImageUsage = Field(default_factory=ImageUsage)


class ImageProvider(Protocol):
    def validate_credentials(self) -> None: ...

    def create(
        self,
        request: ImageProviderRequest,
        inputs: list[ImageProviderInput],
    ) -> ImageProviderResult: ...
