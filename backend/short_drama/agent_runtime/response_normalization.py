"""Lossless compatibility for two observed Worker JSON serialization slips."""

from collections.abc import Collection
import json
from typing import cast

from pydantic import JsonValue


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Repeated object fields cannot be normalized unambiguously")
        result[key] = value
    return result


def normalize_specialist_tool_payload(
    payload: str, *, tool_request_fields: Collection[str]
) -> str:
    """Remove one misplaced structural brace without rewriting any field value.

    The caller supplies the actual typed Tool envelope's fields. Only a complete
    Tool request followed by one extra ``}``, or a missing ``reason`` / current
    ``working_notes`` field after an early closing brace, is recoverable. The
    original Tool arguments and metadata still pass the usual typed validation.
    Other suffixes, conflicting fields and incomplete values remain unchanged.
    """

    try:
        json.loads(payload)
    except json.JSONDecodeError:
        pass
    else:
        return payload

    try:
        decoded, end = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(payload)
    except ValueError:
        return payload
    if not isinstance(decoded, dict):
        return payload
    fields = set(cast(dict[str, JsonValue], decoded))
    if not {"tool_name", "arguments"} <= fields <= set(tool_request_fields):
        return payload

    remainder = payload[end:].strip()
    if remainder == "}":
        # Retain original number spellings, escaping, order and nested contents.
        return payload[:end]
    if not remainder.startswith(","):
        return payload

    try:
        continuation = json.loads("{" + remainder[1:], object_pairs_hook=_unique_object)
    except ValueError:
        return payload
    if not isinstance(continuation, dict):
        return payload
    continuation_fields = set(cast(dict[str, JsonValue], continuation))
    if not continuation_fields or not continuation_fields <= (
        {"reason", "working_notes"} & set(tool_request_fields)
    ) - fields:
        return payload

    # Remove only the completed root object's closing brace. Keep the entire
    # continuation, including whitespace and the final root closing brace.
    return payload[: end - 1] + payload[end:]
