"""Compact tool reads retain the save contract for titles without a media asset."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from pydantic import JsonValue
import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from tests.test_studio_shot_editorial import episode as episode


def title_project(service: ShortDramaService, project_id: str) -> tuple[int, dict[str, JsonValue]]:
    before = read_editorial(service, project_id)
    created = service.tool_registry.invoke("timeline.edit_clips", {"project_id": project_id, "request": {
        "expected_revision": before.revision, "add_titles": [{"id": "title-roundtrip", "text": "片名",
            "start_seconds": 0, "duration_seconds": 2}]}})
    assert created.error is None, created.model_dump_json()
    result = service.tool_registry.invoke("timeline.read_editorial", {"project_id": project_id, "include_project": True})
    assert result.output is not None, result.model_dump_json()
    content = cast(dict[str, JsonValue], result.output["content"])
    return cast(int, content["editorial_revision"]), cast(dict[str, JsonValue], content["project"])


def title_row(project: dict[str, JsonValue]) -> dict[str, JsonValue]:
    timelines = cast(list[dict[str, JsonValue]], project["timelines"])
    return next(clip for timeline in timelines
        for clip in cast(list[dict[str, JsonValue]], timeline["clips"]) if clip["id"] == "title-roundtrip")


def test_read_save_title_without_asset_roundtrips_and_preserves_media(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, project_id, _shots = episode
    revision, project = title_project(service, project_id)
    title = title_row(project)
    # This is the exact include_project tool output, not a reconstructed editor model.
    assert "assetId" not in title and "asset" not in title
    baseline = EditorProject.model_validate(read_editorial(service, project_id).project)
    cast(dict[str, JsonValue], title["textStyle"])["strokeWidth"] = 0
    saved = service.tool_registry.invoke("timeline.save_editorial", {"project_id": project_id, "request": {
        "expected_revision": revision, "timeline": {"project": project}}})
    assert saved.error is None, saved.model_dump_json()
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    current = read_editorial(reopened, project_id)
    assert current.revision == revision + 1
    actual = EditorProject.model_validate(current.project)
    expected = baseline.model_dump(mode="json")
    cast(dict[str, JsonValue], title_row(expected)["textStyle"])["strokeWidth"] = 0
    assert actual.model_dump(mode="json") == expected
    assert next(clip for timeline in actual.timelines for clip in timeline.clips
        if clip.id == "title-roundtrip").assetId is None


@pytest.mark.parametrize("media_type", ["video", "image", "audio"])
def test_missing_asset_still_rejects_media_and_does_not_save(
    episode: tuple[ShortDramaService, str, list[str]], media_type: str,
) -> None:
    service, project_id, _shots = episode
    revision, project = title_project(service, project_id)
    before = read_editorial(service, project_id)
    malformed = deepcopy(project)
    row = title_row(malformed)
    assert "assetId" not in row
    row["type"] = media_type
    result = service.tool_registry.invoke("timeline.save_editorial", {"project_id": project_id, "request": {
        "expected_revision": revision, "timeline": {"project": malformed}}})
    assert result.error is not None and "片段没有绑定有效素材" in result.error.message
    assert read_editorial(service, project_id) == before
