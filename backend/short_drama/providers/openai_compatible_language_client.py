"""Connectivity and text generation for an OpenAI-compatible Responses endpoint."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import json
import time
from typing import Protocol, cast
from urllib.parse import urlsplit

import requests
from requests import Response, Session

from short_drama.observability import log_language_generation


class LanguageProviderError(RuntimeError):
    """The configured language provider could not be validated."""


class LanguageProviderClient(Protocol):
    def validate_credentials(self) -> None: ...

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str: ...


LanguageProviderClientFactory = Callable[[str, str], LanguageProviderClient]


@dataclass(frozen=True, slots=True)
class _StreamGenerationResult:
    text: str
    completion_type: str
    first_text_delta_at: float | None


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
        generation_timeout_seconds: float | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip() and not is_loopback_openai_url(base_url):
            raise ValueError("GPT-5.6-sol API key is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.generation_timeout_seconds = generation_timeout_seconds
        self._monotonic_clock = monotonic_clock

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

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        started_at = self._monotonic_clock()
        completion_type = "unknown_error"
        first_text_delta_at: float | None = None
        output_text = ""
        success = False
        error_kind: str | None = None
        input_payload: object = input_text
        if image_urls:
            input_payload = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": input_text},
                        *(
                            {"type": "input_image", "image_url": image_url}
                            for image_url in image_urls
                        ),
                    ],
                }
            ]
        try:
            try:
                response = self.session.post(
                    f"{self.base_url}/responses",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "instructions": instructions,
                        "input": input_payload,
                        "reasoning": {"effort": "low"},
                        "max_output_tokens": max_output_tokens,
                        "stream": True,
                    },
                    timeout=self.generation_timeout_seconds,
                    stream=True,
                )
            except requests.RequestException as exc:
                completion_type = "transport_error"
                raise LanguageProviderError(
                    "GPT-5.6-sol generation could not reach the configured URL"
                ) from exc
            try:
                self._raise_error(response)
            except LanguageProviderError:
                completion_type = "http_error"
                raise
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" in content_type.lower():
                def capture_first_text_delta(observed_at: float) -> None:
                    nonlocal first_text_delta_at
                    first_text_delta_at = observed_at

                try:
                    stream_result = self._extract_stream_output_text(
                        response,
                        first_text_delta_observer=capture_first_text_delta,
                    )
                except LanguageProviderError:
                    completion_type = "sse_error"
                    raise
                output_text = stream_result.text
                completion_type = stream_result.completion_type
                first_text_delta_at = stream_result.first_text_delta_at
            else:
                try:
                    output_text = self._extract_output_text(response)
                except LanguageProviderError:
                    completion_type = "json_error"
                    raise
                completion_type = "json_response"
            success = True
            return output_text
        except Exception as exc:
            error_kind = type(exc).__name__
            raise
        finally:
            finished_at = self._monotonic_clock()
            log_language_generation(
                model=self.model,
                success=success,
                completion_type=completion_type,
                elapsed_seconds=finished_at - started_at,
                time_to_first_text_delta_seconds=(
                    first_text_delta_at - started_at
                    if first_text_delta_at is not None
                    else None
                ),
                instruction_characters=len(instructions),
                input_characters=len(input_text),
                output_characters=len(output_text),
                error_kind=error_kind,
            )

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
        return OpenAICompatibleLanguageClient._extract_output_text_payload(payload)

    @staticmethod
    def _extract_output_text_payload(payload: object) -> str:
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

    def _extract_stream_output_text(
        self,
        response: Response,
        *,
        first_text_delta_observer: Callable[[float], None],
    ) -> _StreamGenerationResult:
        text_deltas: list[str] = []
        completed_text: str | None = None
        first_text_delta_at: float | None = None
        saw_text_done = False
        saw_completed = False
        saw_done_marker = False
        try:
            for sse_event_type, data in (
                OpenAICompatibleLanguageClient._iter_sse_events(response)
            ):
                if not data:
                    continue
                if data == "[DONE]":
                    saw_done_marker = True
                    continue
                if sse_event_type == "response.completed":
                    saw_completed = True
                    if text_deltas or completed_text is not None:
                        continue
                if sse_event_type not in {
                    None,
                    "response.output_text.delta",
                    "response.output_text.done",
                    "response.completed",
                    "response.failed",
                    "response.incomplete",
                    "error",
                }:
                    continue
                try:
                    event = cast(object, json.loads(data))
                except json.JSONDecodeError as exc:
                    context_start = max(0, exc.pos - 100)
                    context_end = min(len(data), exc.pos + 180)
                    raise LanguageProviderError(
                        "GPT-5.6-sol generation returned an invalid stream event: "
                        f"{exc.msg} at position {exc.pos} of {len(data)} near "
                        + repr(data[context_start:context_end])
                    ) from exc
                if not isinstance(event, dict):
                    continue
                event_payload = cast(dict[str, object], event)
                event_type = event_payload.get("type") or sse_event_type
                if event_type == "response.output_text.delta":
                    delta = event_payload.get("delta")
                    if isinstance(delta, str):
                        text_deltas.append(delta)
                        if delta and first_text_delta_at is None:
                            first_text_delta_at = self._monotonic_clock()
                            first_text_delta_observer(first_text_delta_at)
                    continue
                if event_type == "response.output_text.done":
                    saw_text_done = True
                    text = event_payload.get("text")
                    if isinstance(text, str) and text.strip():
                        completed_text = text
                    continue
                if event_type == "response.completed":
                    saw_completed = True
                    completed = event_payload.get("response")
                    if (
                        not text_deltas
                        and completed_text is None
                        and isinstance(completed, dict)
                    ):
                        completed_text = (
                            OpenAICompatibleLanguageClient._extract_output_text_payload(
                                cast(dict[str, object], completed)
                            )
                        )
                    continue
                if event_type in {"error", "response.failed"}:
                    message = OpenAICompatibleLanguageClient._stream_error_message(
                        event_payload
                    )
                    raise LanguageProviderError(message)
                if event_type == "response.incomplete":
                    raise LanguageProviderError(
                        OpenAICompatibleLanguageClient._stream_error_message(
                            event_payload
                        )
                    )
        except requests.RequestException as exc:
            raise LanguageProviderError(
                "GPT-5.6-sol generation stream was interrupted"
            ) from exc
        finally:
            response.close()
        if not saw_completed and not (saw_text_done and saw_done_marker):
            raise LanguageProviderError(
                "GPT-5.6-sol generation stream ended before completion"
            )
        result = "".join(text_deltas).strip()
        if result:
            return _StreamGenerationResult(
                text=result,
                completion_type=(
                    "sse_response_completed" if saw_completed else "sse_text_done"
                ),
                first_text_delta_at=first_text_delta_at,
            )
        if completed_text is not None and completed_text.strip():
            return _StreamGenerationResult(
                text=completed_text.strip(),
                completion_type=(
                    "sse_response_completed" if saw_completed else "sse_text_done"
                ),
                first_text_delta_at=first_text_delta_at,
            )
        raise LanguageProviderError("GPT-5.6-sol generation returned no output text")

    @staticmethod
    def _iter_sse_events(response: Response) -> Iterator[tuple[str | None, str]]:
        """Reassemble SSE event names and all data fields up to the blank line."""

        data_lines: list[str] = []
        event_type: str | None = None
        # Keep line splitting byte-oriented.  Some compatible gateways omit a
        # UTF-8 charset; requests then decodes as ISO-8859-1 and ``str.splitlines``
        # can mistake UTF-8 continuation bytes (for example 0x85) for newlines.
        for raw_line in response.iter_lines(decode_unicode=False):
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace")
            elif isinstance(raw_line, str):
                line = raw_line
            else:
                continue
            if not line:
                if data_lines:
                    yield event_type, "\n".join(data_lines)
                    data_lines = []
                event_type = None
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip() or None
                continue
            if line == "data":
                data_lines.append("")
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        if data_lines:
            yield event_type, "\n".join(data_lines)

    @staticmethod
    def _stream_error_message(event: dict[str, object]) -> str:
        candidates = [event.get("error")]
        response = event.get("response")
        if isinstance(response, dict):
            candidates.append(cast(dict[str, object], response).get("error"))
        for candidate in candidates:
            if isinstance(candidate, dict):
                message = cast(dict[str, object], candidate).get("message")
                if isinstance(message, str) and message.strip():
                    return message
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            return message
        return "GPT-5.6-sol generation stream failed"

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
