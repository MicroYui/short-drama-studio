"""OpenAI GPT Image 2 adapter for the provider-neutral ImageProvider boundary."""

from __future__ import annotations

import base64
from typing import cast

import requests
from pydantic import ValidationError
from requests import Response, Session

from short_drama.image_models import ImageOperation, ImageProviderRequest, ImageUsage
from short_drama.providers.image_provider import (
    ImageProviderDefiniteError,
    ImageProviderInput,
    ImageProviderResult,
    ImageProviderSubmissionUnknown,
)
from short_drama.providers.openai_image_endpoint import resolve_image_endpoint


class OpenAIImageClient:
    """Small requests-based client for GPT Image generation and edit endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        session: Session | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.endpoint = resolve_image_endpoint(base_url)
        self.base_url = self.endpoint.configured_url
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def validate_credentials(self) -> None:
        if self.endpoint.is_azure:
            self._validate_azure_credentials()
            return
        if self.endpoint.model_url is None:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image endpoint did not provide a model-validation URL"
            )
        try:
            response = self.session.get(
                self.endpoint.model_url,
                headers=self._headers(),
                timeout=30.0,
            )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI credential validation could not reach the API"
            ) from exc
        self._raise_error(response, ambiguous_server_error=False)

    def _validate_azure_credentials(self) -> None:
        """Use a schema-invalid request so Azure checks auth/route without generating."""

        try:
            response = self.session.post(
                self.endpoint.generation_url,
                headers=self._headers(),
                json={"model": "gpt-image-2", "prompt": None, "n": 0},
                timeout=30.0,
            )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "Azure GPT Image credential validation could not reach the API"
            ) from exc
        if response.status_code in {400, 422}:
            code, message = self._error_details(response)
            fatal_detail = f"{code} {message}".lower()
            if any(
                marker in fatal_detail
                for marker in (
                    "api-version",
                    "api version",
                    "deploymentnotfound",
                    "deployment not found",
                    "operationnotsupported",
                    "operation not supported",
                )
            ):
                self._raise_error(response, ambiguous_server_error=False)
            return
        self._raise_error(response, ambiguous_server_error=False)

    def create(
        self,
        request: ImageProviderRequest,
        inputs: list[ImageProviderInput],
    ) -> ImageProviderResult:
        if request.operation == ImageOperation.GENERATE and inputs:
            raise ValueError("Image generation does not accept edit inputs")
        if request.operation == ImageOperation.EDIT and not inputs:
            raise ValueError("Image editing requires at least one input image")

        payload: dict[str, object] = {
            "model": "gpt-image-2",
            "prompt": request.prompt,
            "n": 1,
            "size": request.size,
            "quality": request.quality.value,
            "background": request.background.value,
            "output_format": request.output_format.value,
        }
        endpoint = self.endpoint.generation_url
        if request.operation == ImageOperation.EDIT:
            endpoint = self.endpoint.edit_url
            payload["images"] = [{"image_url": item.image_url} for item in inputs]
            payload["input_fidelity"] = "high"

        try:
            response = self.session.post(
                endpoint,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image submission transport outcome is unknown"
            ) from exc
        self._raise_error(response, ambiguous_server_error=True)
        return self._parse_result(response, request)

    def _parse_result(
        self,
        response: Response,
        request: ImageProviderRequest,
    ) -> ImageProviderResult:
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response was not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ImageProviderSubmissionUnknown("OpenAI image response was not an object")
        typed = cast(dict[str, object], payload)
        raw_data = typed.get("data")
        if not isinstance(raw_data, list) or not raw_data or not isinstance(raw_data[0], dict):
            raise ImageProviderSubmissionUnknown("OpenAI image response did not contain image data")
        first = cast(dict[str, object], raw_data[0])
        encoded = first.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response did not contain a base64 image"
            )
        try:
            base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response contained invalid base64 data"
            ) from exc

        usage = ImageUsage()
        raw_usage = typed.get("usage")
        if isinstance(raw_usage, dict):
            try:
                usage = ImageUsage.model_validate(raw_usage)
            except ValidationError as exc:
                raise ImageProviderSubmissionUnknown(
                    "OpenAI image response contained invalid usage data"
                ) from exc
        revised_prompt = first.get("revised_prompt")
        mime_type = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }[request.output_format.value]
        return ImageProviderResult(
            image_base64=encoded,
            mime_type=mime_type,
            provider_request_id=(
                response.headers.get("x-request-id")
                or response.headers.get("apim-request-id")
            ),
            revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
            usage=usage,
        )

    def _headers(self) -> dict[str, str]:
        if self.endpoint.is_azure:
            return {
                "api-key": self.api_key,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_error(response: Response, *, ambiguous_server_error: bool) -> None:
        if response.status_code < 400:
            return
        code, message = OpenAIImageClient._error_details(response)
        if response.status_code >= 500 and ambiguous_server_error:
            raise ImageProviderSubmissionUnknown(message)
        raise ImageProviderDefiniteError(response.status_code, code, message)

    @staticmethod
    def _error_details(response: Response) -> tuple[str, str]:
        message = response.text or f"OpenAI request failed with {response.status_code}"
        code = str(response.status_code)
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    typed_error = cast(dict[str, object], error)
                    raw_code = typed_error.get("code") or typed_error.get("type")
                    raw_message = typed_error.get("message")
                    if isinstance(raw_code, (str, int)):
                        code = str(raw_code)
                    if isinstance(raw_message, str):
                        message = raw_message
        except ValueError:
            pass
        return code, message
