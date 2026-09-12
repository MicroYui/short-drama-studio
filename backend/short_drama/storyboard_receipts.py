"""Actual saved Storyboard deltas, separate from visible artifact references."""
from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from short_drama.models import DomainModel
from short_drama.production_contracts import StoryboardContent, StoryboardReadResponse


class StoryboardObjectDelta(DomainModel):
    order: list[str]
    changed_ids: list[str]
    removed_ids: list[str]
    saved: list[dict[str, JsonValue]]


class StoryboardWriteContext(DomainModel):
    """Only actual parent equality permits carrying previously read objects."""
    document_id: str
    version_id: str
    version: int
    parent_version_id: str
    sources_changed: bool
    metadata: dict[str, JsonValue]
    objects: dict[str, StoryboardObjectDelta] = Field(default_factory=dict[str, StoryboardObjectDelta])


def storyboard_write_context(result: StoryboardReadResponse) -> StoryboardWriteContext | None:
    parent = next((version for version in result.document.versions
                   if version.id == result.version.parent_version_id), None)
    if parent is None:
        return None
    previous = StoryboardContent.model_validate(parent.payload).model_dump(mode="json")
    saved = result.storyboard.model_dump(mode="json")
    objects: dict[str, StoryboardObjectDelta] = {}
    for field in ("shots", "clip_groups", "scene_nodes"):
        old = {item["id"]: item for item in previous[field]}
        current = cast(list[dict[str, JsonValue]], saved[field])
        changed = [item for item in current if item != old.get(item["id"])]
        order = [cast(str, item["id"]) for item in current]
        objects[field] = StoryboardObjectDelta(order=order,
            changed_ids=[cast(str, item["id"]) for item in changed],
            removed_ids=[identifier for identifier in old if identifier not in order], saved=changed)
    source_fields = ("script_version_id", "bible_version_id", "production_spec_document_id", "production_spec_version_id")
    return StoryboardWriteContext(document_id=result.document.id, version_id=result.version.id,
        version=result.version.version, parent_version_id=parent.id,
        sources_changed=any(previous.get(key) != saved.get(key) for key in source_fields),
        metadata={key: value for key, value in saved.items() if key not in objects}, objects=objects)
