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
from short_drama.language_defaults import (
    LANGUAGE_MODEL,
    LANGUAGE_REASONING_EFFORT,
    ReasoningEffort,
)


class LanguageProviderError(RuntimeError):
    """The configured language provider could not be validated."""


class LanguageProviderTransportError(LanguageProviderError):
    """No complete model decision arrived; the existing work can be resumed."""


class LanguageProviderContextWindowError(LanguageProviderError):
    """The provider rejected the input window; rebuild it before any retry."""


class LanguageProviderTransientError(LanguageProviderTransportError):
    """A transient HTTP failure can retry the language decision, not its Tools."""


def is_language_transport_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, LanguageProviderTransportError):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


class LanguageProviderIncompleteError(LanguageProviderError):
    """The provider stopped a generation before a complete payload existed."""

    def __init__(
        self,
        *,
        reason: str,
        partial_output_characters: int,
    ) -> None:
        self.reason = reason
        self.partial_output_characters = partial_output_characters
        super().__init__(f"Language generation incomplete: {reason}")


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
        model: str = LANGUAGE_MODEL,
        reasoning_effort: ReasoningEffort = LANGUAGE_REASONING_EFFORT,
        text_delta_observer: Callable[[str], None] | None = None,
        session: Session | None = None,
        timeout_seconds: float = 30.0,
        generation_timeout_seconds: float | tuple[float, float] | None = (30.0, 600.0),
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip() and not is_loopback_openai_url(base_url):
            raise ValueError("Language provider API key is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.text_delta_observer = text_delta_observer
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
                "Language provider connection validation could not reach the configured URL"
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
        from short_drama.studio_trace import clean_trace_text, record_trace
        from short_drama.studio_generation_draft import StreamedCreativeDraft
        from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
        from uuid import uuid4

        call_id = f"model-{uuid4().hex[:12]}"
        draft = StreamedCreativeDraft(call_id)
        record_trace("model.input", {
            "call_id": call_id, "model": self.model, "reasoning_effort": self.reasoning_effort,
            "instructions": clean_trace_text(instructions), "input_text": clean_trace_text(input_text),
            "image_count": len(image_urls or []), "max_output_tokens": max_output_tokens,
        })
        record_trace("model.started", {"call_id": call_id, "model": self.model,
            "reasoning_effort": self.reasoning_effort, "instruction_characters": len(instructions),
            "input_characters": len(input_text), "image_count": len(image_urls or [])})
        started_at = self._monotonic_clock()
        completion_type = "unknown_error"
        first_text_delta_at: float | None = None
        output_text = ""
        partial_output_characters = 0
        success = False
        error_kind: str | None = None
        response_metadata: dict[str, object] = {}

        def capture_response_metadata(payload: dict[str, object]) -> None:
            for key in ("id", "model", "usage"):
                if key in payload:
                    response_metadata[key] = payload[key]

        def capture_text_delta(delta: str) -> None:
            nonlocal partial_output_characters
            partial_output_characters += len(delta)
            draft.receive(delta)
            if self.text_delta_observer is not None:
                self.text_delta_observer(delta)

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
            # Leader/Worker compact before this boundary. Helpers called inside
            # Tools share this last check, so a large text request cannot bypass
            # the configured window. Vision usage is still provider-reported.
            budget = ContextBudget()
            estimated_text_tokens = count_text_tokens(instructions) + count_text_tokens(input_text)
            input_limit = budget.input_limit(max_output_tokens)
            record_trace("model.preflight", {"call_id": call_id, "window_tokens": budget.window_tokens,
                "estimated_text_tokens": estimated_text_tokens, "reserved_output_tokens": max_output_tokens,
                "input_limit_tokens": input_limit, "image_count": len(image_urls or []),
                "image_tokens_included": False, "within_text_budget": estimated_text_tokens <= input_limit})
            if estimated_text_tokens > input_limit:
                completion_type = "local_context_limit"
                raise LanguageProviderContextWindowError(
                    f"当前文本输入约 {estimated_text_tokens:,} tokens，超过 258k 总预算中预留输出后的 "
                    f"{input_limit:,} tokens。请缩小当前任务或按字段读取；未发送超限请求。")
            try:
                response = self.session.post(
                    f"{self.base_url}/responses",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "instructions": instructions,
                        "input": input_payload,
                        "reasoning": {"effort": self.reasoning_effort},
                        "max_output_tokens": max_output_tokens,
                        "stream": True,
                    },
                    timeout=self.generation_timeout_seconds,
                    stream=True,
                )
            except requests.RequestException as exc:
                completion_type = "transport_error"
                raise LanguageProviderTransportError(
                    "Language provider generation could not reach the configured URL"
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
                        response_observer=capture_response_metadata,
                        text_delta_observer=capture_text_delta,
                    )
                except LanguageProviderIncompleteError as exc:
                    completion_type = f"sse_incomplete_{exc.reason}"
                    partial_output_characters = exc.partial_output_characters
                    raise
                except LanguageProviderError:
                    completion_type = "sse_error"
                    raise
                output_text = stream_result.text
                completion_type = stream_result.completion_type
                first_text_delta_at = stream_result.first_text_delta_at
            else:
                try:
                    payload = cast(object, response.json())
                    if isinstance(payload, dict):
                        capture_response_metadata(cast(dict[str, object], payload))
                    output_text = self._extract_output_text_payload(cast(object, payload))
                except ValueError as exc:
                    completion_type = "json_error"
                    raise LanguageProviderError("Language generation returned invalid JSON") from exc
                except LanguageProviderError:
                    completion_type = "json_error"
                    raise
                completion_type = "json_response"
            success = True
            record_trace("model.output", {"call_id": call_id, "text": output_text})
            return output_text
        except Exception as exc:
            error_kind = type(exc).__name__
            raise
        finally:
            draft.finish(success=success)
            finished_at = self._monotonic_clock()
            log_language_generation(
                call_id=call_id,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                max_output_tokens=max_output_tokens,
                image_count=len(image_urls or []),
                response_metadata=response_metadata,
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
                output_characters=max(
                    len(output_text),
                    partial_output_characters,
                ),
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
                "Language provider model lookup returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise LanguageProviderError("Language provider model lookup returned an invalid object")
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
                "Language provider model list returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise LanguageProviderError("Language provider model list returned an invalid object")
        raw_data = cast(dict[str, object], payload).get("data")
        if not isinstance(raw_data, list):
            raise LanguageProviderError("Language provider model list did not contain data")
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
                "Language provider generation returned invalid JSON"
            ) from exc
        return OpenAICompatibleLanguageClient._extract_output_text_payload(payload)

    @staticmethod
    def _extract_output_text_payload(payload: object) -> str:
        if not isinstance(payload, dict):
            raise LanguageProviderError("Language provider generation returned an invalid object")
        response_payload = cast(dict[str, object], payload)
        if response_payload.get("status") == "incomplete":
            partial = response_payload.get("output_text")
            raise LanguageProviderIncompleteError(
                reason=OpenAICompatibleLanguageClient._stream_incomplete_reason(response_payload) or "unknown",
                partial_output_characters=len(partial) if isinstance(partial, str) else 0,
            )
        if response_payload.get("status") == "failed":
            raise LanguageProviderError(OpenAICompatibleLanguageClient._stream_error_message(response_payload))
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
            raise LanguageProviderError("Language provider generation returned no output text")
        return result

    def _extract_stream_output_text(
        self,
        response: Response,
        *,
        first_text_delta_observer: Callable[[float], None],
        response_observer: Callable[[dict[str, object]], None],
        text_delta_observer: Callable[[str], None],
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
                        "Language provider generation returned an invalid stream event: "
                        f"{exc.msg} at position {exc.pos} of {len(data)} near "
                        + repr(data[context_start:context_end])
                    ) from exc
                if not isinstance(event, dict):
                    continue
                event_payload = cast(dict[str, object], event)
                event_type = event_payload.get("type") or sse_event_type
                terminal_response = event_payload.get("response")
                if event_type in {"response.completed", "response.incomplete", "response.failed"} and isinstance(terminal_response, dict):
                    response_observer(cast(dict[str, object], terminal_response))
                if event_type == "response.output_text.delta":
                    delta = event_payload.get("delta")
                    if isinstance(delta, str):
                        text_deltas.append(delta)
                        text_delta_observer(delta)
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
                    reason = (
                        OpenAICompatibleLanguageClient._stream_incomplete_reason(
                            event_payload
                        )
                        or "unknown"
                    )
                    raise LanguageProviderIncompleteError(
                        reason=reason,
                        partial_output_characters=max(
                            len("".join(text_deltas)),
                            len(completed_text or ""),
                        ),
                    )
        except requests.RequestException as exc:
            raise LanguageProviderTransportError(
                "Language provider generation stream was interrupted"
            ) from exc
        finally:
            response.close()
        if not saw_completed and not (saw_text_done and saw_done_marker):
            raise LanguageProviderTransportError(
                "Language provider generation stream ended before completion"
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
        raise LanguageProviderError("Language provider generation returned no output text")

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
        return "Language provider generation stream failed"

    @staticmethod
    def _stream_incomplete_reason(event: dict[str, object]) -> str | None:
        candidates: list[object] = [event.get("incomplete_details")]
        response = event.get("response")
        if isinstance(response, dict):
            candidates.append(
                cast(dict[str, object], response).get("incomplete_details")
            )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            reason = cast(dict[str, object], candidate).get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
        return None

    @staticmethod
    def _raise_error(response: Response) -> None:
        if response.status_code < 400:
            return
        message = response.text or f"Language provider returned HTTP {response.status_code}"
        code: str | None = None
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    raw_code = cast(dict[str, object], error).get("code")
                    code = raw_code if isinstance(raw_code, str) else None
                    raw_message = cast(dict[str, object], error).get("message")
                    if isinstance(raw_message, str):
                        message = raw_message
        except ValueError:
            pass
        if code in {"context_length_exceeded", "context_window_exceeded", "max_context_length_exceeded"}:
            raise LanguageProviderContextWindowError(message)
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise LanguageProviderTransientError(message)
        raise LanguageProviderError(message)


def default_language_provider_factory(
    api_key: str,
    base_url: str,
) -> LanguageProviderClient:
    return OpenAICompatibleLanguageClient(api_key=api_key, base_url=base_url)
