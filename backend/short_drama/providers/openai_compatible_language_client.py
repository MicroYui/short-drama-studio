"""Connectivity and text generation for an OpenAI-compatible Responses endpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import urlsplit

import requests
from requests import Response, Session


class LanguageProviderError(RuntimeError):
    """The configured language provider could not be validated."""


class LanguageProviderClient(Protocol):
    def validate_credentials(self) -> None: ...

    def generate_text(self, *, instructions: str, input_text: str) -> str: ...


LanguageProviderClientFactory = Callable[[str, str], LanguageProviderClient]


def is_loopback_openai_url(value: str) -> bool:
    hostname = (urlsplit(value).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


class OpenAICompatibleLanguageClient:
    """Validates URL, key, and fixed-model visibility without a paid generation."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "gpt-5.6-sol",
        session: Session | None = None,
        timeout_seconds: float = 30.0,
        generation_timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key.strip() and not is_loopback_openai_url(base_url):
            raise ValueError("GPT-5.6-sol API key is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.generation_timeout_seconds = generation_timeout_seconds

    def validate_credentials(self) -> None:
        try:
            response = self.session.get(
                f"{self.base_url}/models/{self.model}",
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
            if response.status_code in {404, 405}:
                response = self.session.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=self.timeout_seconds,
                )
                self._raise_error(response)
                self._validate_model_catalog(response)
                return
        except requests.RequestException as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol connection validation could not reach the configured URL"
            ) from exc
        self._raise_error(response)
        self._validate_model_identity(response)

    def generate_text(self, *, instructions: str, input_text: str) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/responses",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "instructions": instructions,
                    "input": input_text,
                    "reasoning": {"effort": "low"},
                    "max_output_tokens": 2200,
                },
                timeout=self.generation_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol generation could not reach the configured URL"
            ) from exc
        self._raise_error(response)
        return self._extract_output_text(response)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _validate_model_identity(self, response: Response) -> None:
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol model lookup returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise LanguageProviderError("GPT-5.6-sol model lookup returned an invalid object")
        raw_id = cast(dict[str, object], payload).get("id")
        if isinstance(raw_id, str) and raw_id and raw_id != self.model:
            raise LanguageProviderError(
                f"Configured endpoint returned model {raw_id!r}, expected {self.model!r}"
            )

    def _validate_model_catalog(self, response: Response) -> None:
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol model list returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise LanguageProviderError("GPT-5.6-sol model list returned an invalid object")
        raw_data = cast(dict[str, object], payload).get("data")
        if not isinstance(raw_data, list):
            raise LanguageProviderError("GPT-5.6-sol model list did not contain data")
        model_ids: set[str] = set()
        for item in cast(list[object], raw_data):
            if isinstance(item, str):
                model_ids.add(item)
            elif isinstance(item, dict):
                raw_id = cast(dict[str, object], item).get("id")
                if isinstance(raw_id, str):
                    model_ids.add(raw_id)
        if self.model not in model_ids:
            raise LanguageProviderError(
                f"Configured endpoint model list does not include {self.model!r}"
            )

    @staticmethod
    def _extract_output_text(response: Response) -> str:
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol generation returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise LanguageProviderError("GPT-5.6-sol generation returned an invalid object")
        response_payload = cast(dict[str, object], payload)
        direct_text = response_payload.get("output_text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        text_parts: list[str] = []
        raw_output = response_payload.get("output")
        if isinstance(raw_output, list):
            for raw_item in cast(list[object], raw_output):
                if not isinstance(raw_item, dict):
                    continue
                raw_content = cast(dict[str, object], raw_item).get("content")
                if not isinstance(raw_content, list):
                    continue
                for raw_part in cast(list[object], raw_content):
                    if not isinstance(raw_part, dict):
                        continue
                    part = cast(dict[str, object], raw_part)
                    text = part.get("text")
                    if part.get("type") == "output_text" and isinstance(text, str):
                        text_parts.append(text)
        result = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not result:
            raise LanguageProviderError("GPT-5.6-sol generation returned no output text")
        return result

    @staticmethod
    def _raise_error(response: Response) -> None:
        if response.status_code < 400:
            return
        message = response.text or f"Language provider returned HTTP {response.status_code}"
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    raw_message = cast(dict[str, object], error).get("message")
                    if isinstance(raw_message, str):
                        message = raw_message
        except ValueError:
            pass
        raise LanguageProviderError(message)


def default_language_provider_factory(
    api_key: str,
    base_url: str,
) -> LanguageProviderClient:
    return OpenAICompatibleLanguageClient(api_key=api_key, base_url=base_url)
