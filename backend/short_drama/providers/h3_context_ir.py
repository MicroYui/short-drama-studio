"""Opt-in official H3 Context IR; separate credentials/transport, shared H3 grammar.

Public contract: https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir
This compiles prompts only. It never creates video tasks or invokes the language compiler.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock, RLock
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import quote

from pydantic import Field, JsonValue, model_validator
import requests

from short_drama.h3_models import H3ContentItem, H3CreateResponse, H3MediaInput, H3QueryResponse, H3RemoteTask
from short_drama.language_models import LanguagePromptCompileRequest, LanguagePromptCompileResponse
from short_drama.models import DomainModel, VideoGenerationRoute
from short_drama.providers.h3_language_prompt import (
    build_generation_unit_source_input, normalize_language_prompt,
    validate_language_generation_unit_prompt,
)
from short_drama.providers.minimax_h3_client import (
    H3DefiniteRequestError, H3SubmissionUnknown, H3TransientError, encode_h3_media_data_uri,
)
from short_drama.providers.minimax_h3_compiler import compile_h3_request
from short_drama.providers.h3_output_format import h3_prompt_document, resolve_h3_output_format
from short_drama.studio_references import assert_generation_references
from short_drama.studio_trace import record_trace

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class H3ContextIRRequest(DomainModel):
    model: Literal["MiniMax-H3"] = "MiniMax-H3"
    content: list[H3ContentItem]
    duration: int = Field(ge=4, le=15)
    ratio: str

    @model_validator(mode="after")
    def validate_text(self) -> H3ContextIRRequest:
        texts = [item.text for item in self.content if item.type == "text"]
        if len(texts) != 1 or not texts[0] or not texts[0].strip() or len(texts[0]) > 7000:
            raise ValueError("Context IR requires one nonempty text input of at most 7000 characters")
        if self.ratio not in {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
            raise ValueError("Unsupported official Context IR aspect ratio")
        return self


class ContextIRSubmissionUnknown(H3SubmissionUnknown):
    def __init__(self, cache_id: str, message: str) -> None:
        super().__init__(message)
        self.cache_id = cache_id


class H3ContextIRPending(ValueError):
    def __init__(self, cache_id: str, task_id: str, status: str, next_poll_at: str) -> None:
        super().__init__(f"Official H3 Context IR {task_id} is {status}; resume the saved group after {next_poll_at}")
        self.cache_id, self.task_id, self.status, self.next_poll_at = cache_id, task_id, status, next_poll_at


class ContextIRClient(Protocol):
    def submit(self, request: H3ContextIRRequest) -> str: ...
    def query(self, task_id: str) -> H3RemoteTask: ...


class OfficialH3ContextIRClient:
    """Official MiniMax credentials must never fall back to the AceData token."""

    def __init__(self, *, api_key: str, session: requests.Session | None = None, timeout_seconds: float = 60) -> None:
        if not api_key.strip():
            raise ValueError("MINIMAX_OFFICIAL_API_KEY is required for opt-in Context IR; the existing AceData token is a different credential")
        self._api_key, self._session, self._timeout = api_key.strip(), session or requests.Session(), timeout_seconds

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> object:
        submitting = method == "POST"
        try:
            response = self._session.request(method, f"https://api.minimax.io{path}",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload, timeout=self._timeout)
        except requests.RequestException as exc:
            error = H3SubmissionUnknown if submitting else H3TransientError
            raise error("Official Context IR transport failed; retain the saved task state") from exc
        if response.status_code >= 500:
            error = H3SubmissionUnknown if submitting else H3TransientError
            raise error(f"Official Context IR returned HTTP {response.status_code}")
        try:
            value = cast(object, response.json())
        except ValueError as exc:
            error = H3SubmissionUnknown if submitting else H3TransientError
            raise error("Official Context IR response was not JSON") from exc
        if response.status_code >= 400:
            detail = cast(dict[str, object], value).get("error") if isinstance(value, dict) else None
            message = cast(dict[str, object], detail).get("message") if isinstance(detail, dict) else None
            raise H3DefiniteRequestError(response.status_code, "OFFICIAL_CONTEXT_IR_REJECTED", str(message or f"HTTP {response.status_code}"))
        return value

    def submit(self, request: H3ContextIRRequest) -> str:
        payload = cast(dict[str, object], request.model_dump(mode="json", exclude_none=True))
        if len(json.dumps(payload, allow_nan=False).encode()) > 64 * 1024 * 1024:
            raise H3DefiniteRequestError(400, "REQUEST_TOO_LARGE", "Official Context IR request exceeds 64 MB; use public URLs or official mm_file references")
        value = self._request("POST", "/v2/h3_context_ir", payload)
        try:
            task_id = H3CreateResponse.model_validate(value).task_id
            if not task_id.strip():
                raise ValueError("empty task ID")
            return task_id
        except ValueError as exc:
            raise H3SubmissionUnknown("Official Context IR submit did not return a task ID; do not resubmit") from exc

    def query(self, task_id: str) -> H3RemoteTask:
        value = self._request("GET", f"/v2/query/video_generation/{quote(task_id, safe='')}")
        try:
            task = H3QueryResponse.model_validate(value).task
        except ValueError as exc:
            raise H3TransientError("Official Context IR query did not return a task") from exc
        if task.id != task_id or task.task_type != "h3_context_ir":
            raise H3TransientError("Official query returned another task/type; preserve the original task ID")
        return task


def build_context_ir_request(source_text: str, *, route: VideoGenerationRoute, duration: int,
    media_inputs: list[H3MediaInput], resolve_url: Callable[[H3MediaInput], str], aspect_ratio: str | None = None) -> H3ContextIRRequest:
    # Reuse mode/count/file validation and the exact ordered H3 content serialization.
    video = compile_h3_request(route=route, prompt=source_text, duration_seconds=duration,
        media_inputs=media_inputs, resolve_url=resolve_url, aspect_ratio=aspect_ratio)
    return H3ContextIRRequest(content=video.content, duration=duration, ratio=video.ratio or "adaptive")


class ContextIRRecord(DomainModel):
    id: str
    status: str = "NEW"
    task_id: str | None = None
    raw_prompt: str | None = None
    usage: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    next_poll_at: str | None = None
    error: str | None = None


_LOCK_GUARD = Lock()
_LOCKS: dict[str, RLock] = {}


def _save(path: Path, record: ContextIRRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    record_trace("h3.context_ir.state", record.model_dump(mode="json", exclude={"raw_prompt"}))


def advance_context_ir(*, client: ContextIRClient | Callable[[], ContextIRClient], cache_directory: Path, identity: dict[str, JsonValue],
    build_request: Callable[[], H3ContextIRRequest], retry_failed: bool = False) -> str:
    """One submit or query per call; persist before submit and retain successful output."""
    cache_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = cache_directory / f"{cache_id}.json"
    with _LOCK_GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), RLock())
    with lock:
        record = ContextIRRecord.model_validate_json(path.read_text()) if path.exists() else ContextIRRecord(id=cache_id)
        if record.status == "SUCCEEDED" and record.raw_prompt:
            return record.raw_prompt
        if record.status in {"SUBMITTING", "SUBMIT_UNKNOWN"}:
            raise ContextIRSubmissionUnknown(cache_id, f"Official Context IR submission outcome is unknown (cache {cache_id}); reconcile the official task before another submit")
        if record.status in {"FAILED", "REJECTED"}:
            if not retry_failed:
                raise ValueError(record.error or f"Official Context IR {record.status}; an explicit retry is required")
            record = ContextIRRecord(id=cache_id)
        if record.task_id is None:
            active_client = client() if callable(client) else client
            request = build_request()  # Deterministic errors happen before any submission intent is saved.
            record.status = "SUBMITTING"
            _save(path, record)
            try:
                record.task_id = active_client.submit(request)
            except H3DefiniteRequestError as exc:
                record.status, record.error = "REJECTED", str(exc)
                _save(path, record)
                raise
            except H3SubmissionUnknown as exc:
                record.status, record.error = "SUBMIT_UNKNOWN", str(exc)
                _save(path, record)
                raise ContextIRSubmissionUnknown(cache_id, str(exc)) from exc
            record.status = "queued"
        else:
            if record.next_poll_at and datetime.now(UTC) < datetime.fromisoformat(record.next_poll_at):
                raise H3ContextIRPending(cache_id, record.task_id, record.status, record.next_poll_at)
            active_client = client() if callable(client) else client
            task = active_client.query(record.task_id)
            record.status = task.status.value
            record.usage = cast(dict[str, JsonValue], task.usage.model_dump(mode="json")) if task.usage else {}
            if record.status == "succeeded":
                prompt = task.content.prompt if task.content else None
                if not prompt or not prompt.strip():
                    record.status, record.error = "FAILED", "Official Context IR succeeded without content.prompt; do not create video from an empty result"
                    _save(path, record)
                    raise ValueError(record.error)
                record.status, record.raw_prompt = "SUCCEEDED", prompt.strip()
                _save(path, record)
                record_trace("h3.context_ir.output", {"cache_id": cache_id, "task_id": record.task_id,
                    "prompt": record.raw_prompt, "usage": record.usage})
                return record.raw_prompt
            if record.status in {"failed", "cancelled"}:
                record.status, record.error = "FAILED", (task.error.message if task.error else None) or "Official Context IR task failed/cancelled"
                _save(path, record)
                raise ValueError(record.error)
        record.next_poll_at = (datetime.now(UTC) + timedelta(seconds=15)).isoformat()
        _save(path, record)
        raise H3ContextIRPending(cache_id, record.task_id, record.status, record.next_poll_at)


def _without_empty(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _without_empty(item) for key, item in value.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        return [_without_empty(item) for item in value]
    return value


def compile_h3_context_ir(service: ShortDramaService, project_id: str, request: LanguagePromptCompileRequest,
    *, client: ContextIRClient | None = None, retry_failed: bool = False) -> LanguagePromptCompileResponse:
    """Alternative group compiler; never call the language compiler before or afterward."""
    from short_drama.service import ProviderOperationError

    if request.picture_only is not None:
        raise ValueError("PICTURE_ONLY uses the LANGUAGE compiler with its saved picture/audio binding")
    if request.generation_unit_id is None:
        raise ValueError("Official Context IR currently requires a complete generation-unit target")
    # Deliberately reuse the service's canonical compiler checks instead of reimplementing asset/group truth.
    document, _plan, unit, shots, spec = service._h3_generation_unit_sources(project_id, request)  # pyright: ignore[reportPrivateUsage]
    duration = math.ceil(unit.requested_duration_seconds)
    if request.duration_seconds not in {None, duration}:
        raise ValueError("Context IR duration must equal the current generation unit")
    media = service._resolve_approved_asset_inputs(document, request.media_inputs)  # pyright: ignore[reportPrivateUsage]
    for shot in shots:
        assert_generation_references(document, shot, media, require_all=False)
    if request.source_prompt is not None:
        raise ValueError("Context IR consumes canonical source facts directly; select the language compiler for an existing compiled-prompt revision")
    resolution, aspect_ratio = resolve_h3_output_format(route=request.route, resolution=request.resolution or unit.resolution or "768P",
        aspect_ratio=request.aspect_ratio, default_aspect_ratio=spec.aspect_ratio)
    document = h3_prompt_document(document, route=request.route, aspect_ratio=aspect_ratio)
    input_text = build_generation_unit_source_input(document, unit, shots, route=request.route,
        resolution=resolution, aspect_ratio=aspect_ratio,
        duration_seconds=duration, production_spec=spec, media_inputs=media,
        revision_instructions=request.revision_instructions,
        actual_previous_media_boundary=service._previous_adopted_media_boundary(document, shots[0].id))  # pyright: ignore[reportPrivateUsage]
    facts = cast(dict[str, JsonValue], json.loads(input_text))
    # Remove empty serialization fields, never meaningful facts or dialogue. The official text limit is 7000 too.
    source = "Compile these locked facts into the required H3 Prompt IR fields only. Preserve every timing marker, exact spoken line and ordered reference binding; do not add shots or facts.\n" + json.dumps(_without_empty(facts), ensure_ascii=False, separators=(",", ":"))
    if len(source) > 7000:
        raise ValueError(f"Official Context IR source is {len(source)} characters, over its 7000-character text limit; use the default compiler or explicitly revise the group facts. No facts were truncated and no task was submitted")
    media_identity: list[JsonValue] = []
    for item in media:
        state: dict[str, JsonValue] = {"media": item.model_dump(mode="json")}
        if item.source.local_path:
            stat = Path(item.source.local_path).stat()
            state.update({"size": stat.st_size, "modified_ns": stat.st_mtime_ns})
        media_identity.append(state)
    identity: dict[str, JsonValue] = {"adapter_version": 1, "project_id": project_id, "provider": "official-minimax",
        "source": source, "route": request.route.value, "duration": duration, "media": media_identity}

    def resolve_url(item: H3MediaInput) -> str:
        if item.source.local_path:
            return encode_h3_media_data_uri(Path(item.source.local_path))
        if item.source.file_id:
            raise ValueError("Context IR cannot assume an AceData file_id belongs to the official MiniMax account; provide its official mm_file:// URL or local media")
        assert item.source.external_url
        return item.source.external_url

    record_trace("h3.context_ir.input", {"project_id": project_id, "generation_unit_id": unit.id,
        "source_text": source, "media_inputs": [item.model_dump(mode="json") for item in media],
        "model": "MiniMax-H3", "duration": duration, "route": request.route.value})
    actual_client = client or (lambda: OfficialH3ContextIRClient(api_key=os.environ.get("MINIMAX_OFFICIAL_API_KEY", "")))
    try:
        raw = advance_context_ir(client=actual_client, cache_directory=service.workspace_root / "h3-context-ir",
            identity=identity, retry_failed=retry_failed,
            build_request=lambda: build_context_ir_request(source, route=request.route, duration=duration, media_inputs=media, resolve_url=resolve_url, aspect_ratio=aspect_ratio))
    except (H3SubmissionUnknown, H3TransientError, H3DefiniteRequestError) as exc:
        raise ProviderOperationError(str(exc)) from exc
    prompt = normalize_language_prompt(raw, document, route=request.route, duration_seconds=duration, media_inputs=media, generation_unit=True)
    notes = validate_language_generation_unit_prompt(prompt, document, unit, shots, route=request.route, duration_seconds=duration, media_inputs=media)
    return LanguagePromptCompileResponse(provider="minimax-official-context-ir", model="MiniMax-H3", reasoning_effort="provider-managed",
        resolution=resolution, aspect_ratio=aspect_ratio,
        candidate_id=f"{request.route.value.lower().replace('_', '-')}:{unit.id}", generation_plan_document_id=request.generation_plan_document_id,
        generation_plan_version_id=request.generation_plan_version_id, generation_unit_id=unit.id, shot_ids=list(unit.shot_ids),
        method_skill_ids=list(request.method_skill_ids), media_inputs=media, route=request.route, duration_seconds=duration,
        source_prompt="", compiled_prompt=prompt, validation_notes=[*notes,
            "Official Context IR output validated with the same H3 production contract; no second compiler was called.",
            "Selected method IDs describe upstream source provenance; local language-compiler Skill prose is not injected into the official compiler."])
