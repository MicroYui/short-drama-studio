"""Keep provider media bodies in persistence, outside text-model working sets."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue


def provider_media_model_view(value: JsonValue) -> JsonValue:
    """Project media transport data without changing the persisted request.

    H3 runs already carry the exact prompt and ordered media_inputs. Their wire
    request repeats those inputs as large data URIs; model context needs only
    request parameters alongside the original prompt, asset IDs and paths.
    The recursive case also covers an explicit read of a nested ToolAttempt.
    """
    if isinstance(value, str) and value.startswith("data:") and "," in value:
        header = value.split(",", 1)[0]
        return {
            "kind": "inline_media",
            "media_type": header[5:].split(";", 1)[0],
            "encoded_characters": len(value),
            "body_location": "persisted provider request",
        }
    if isinstance(value, list):
        return [provider_media_model_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    source = cast(dict[str, JsonValue], value)
    if {"take_id", "semantic_status", "technical_checks", "observations", "samples"} <= source.keys():
        source = dict(source)
        samples = source.pop("samples")
        if isinstance(samples, list):
            frame_times: dict[str, list[JsonValue]] = {}
            for sample in samples:
                if isinstance(sample, dict):
                    take_id = sample.get("source_take_id")
                    if isinstance(take_id, str):
                        frame_times.setdefault(take_id, []).append(sample.get("timestamp_seconds"))
            source["sample_count"] = len(samples)
            source["sample_timestamps_by_take"] = cast(JsonValue, frame_times)
            source["sample_details_location"] = "persisted video_inspection.samples"
    if {"task_id", "media_inputs", "uploaded_inputs", "pricing"} <= source.keys():
        source = dict(source)
        request = source.pop("request", None)
        if isinstance(request, dict):
            content = request.get("content")
            source["request_parameters"] = {
                key: item for key, item in request.items() if key != "content"
            }
            source["request_content_count"] = len(content) if isinstance(content, list) else 0
    return {key: provider_media_model_view(item) for key, item in source.items()}
