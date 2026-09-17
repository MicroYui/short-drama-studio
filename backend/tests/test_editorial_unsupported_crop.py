"""Unsupported spatial crop must not produce a successful edit/export receipt."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial, validate_editorial
from short_drama.studio_export import list_exports
from short_drama.studio_shot_editorial import read_shot_editorial
from tests.test_studio_shot_editorial import as_json, episode as episode


EDGE_CROP: dict[str, JsonValue] = {"left": 0, "right": 0, "top": 0, "bottom": 26.171875}
RECTANGLE_CROP: dict[str, JsonValue] = {"x": 0, "y": 0, "width": 100, "height": 73.828125}


def _first_video(project: dict[str, JsonValue]) -> dict[str, JsonValue]:
    timelines = cast(list[dict[str, JsonValue]], project["timelines"])
    return next(clip for clip in cast(list[dict[str, JsonValue]], timelines[0]["clips"])
                if clip["type"] == "video")


@pytest.mark.parametrize("crop", [EDGE_CROP, RECTANGLE_CROP], ids=["edges", "rectangle"])
@pytest.mark.parametrize("local", [False, True], ids=["film", "shot"])
def test_actual_tool_rejects_crop_before_any_content_write(
    episode: tuple[ShortDramaService, str, list[str]], crop: dict[str, JsonValue], local: bool,
) -> None:
    service, project_id, shots = episode
    before = service.get_project(project_id)
    selection: dict[str, JsonValue] = {"include_project": True, **({"shot_id": shots[0]} if local else {})}
    read = service.tool_registry.invoke("timeline.read_editorial", {"project_id": project_id, **selection})
    assert read.error is None and read.output is not None
    content = cast(dict[str, JsonValue], read.output["content"])
    project = cast(dict[str, JsonValue], content["project"])
    clip = _first_video(project)
    clip["crop"] = deepcopy(crop)
    request: dict[str, JsonValue] = {"expected_revision": 1, "timeline": {"project": project}}
    if local:
        scope = read_shot_editorial(service, project_id, shots[0]).scope
        assert scope is not None
        request["anchor_clip_id"] = scope.anchor_clip_id
    result = service.tool_registry.invoke("timeline.save_editorial", {
        "project_id": project_id, **({"shot_id": shots[0]} if local else {}), "request": request})
    assert result.error is not None and result.receipt is None and not result.artifact_refs
    message = result.error.message
    timeline_id = cast(list[dict[str, JsonValue]], project["timelines"])[0]["id"]
    assert str(timeline_id) in message and str(clip["id"]) in message
    assert "/project/timelines/0/clips/0/crop" in message
    assert "删除该字段或设为 null" in message and "不能替代空间裁切" in message
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    after = reopened.get_project(project_id)
    assert after.document == before.document and after.takes == before.takes


@pytest.mark.parametrize("clear_with_null", [False, True], ids=["remove", "null"])
def test_legacy_crop_remains_readable_and_explicit_cleanup_restores_save_and_real_export(
    episode: tuple[ShortDramaService, str, list[str]], clear_with_null: bool,
) -> None:
    service, project_id, shots = episode
    document = service.get_project(project_id).document
    editor = EditorProject.model_validate(read_editorial(service, project_id).project)
    clip = next(item for item in editor.timelines[0].clips if item.type == "video")
    assert clip.model_extra is not None
    # These are established LTX fields, including fields kept as backend extras.
    clip.flipH = True
    clip.opacity = 80
    clip.model_extra.update({"crop": deepcopy(RECTANGLE_CROP), "colorLabel": "blue",
        "colorCorrection": {"brightness": 12, "contrast": 0, "saturation": -15, "temperature": 0,
            "tint": 0, "exposure": 0, "highlights": 0, "shadows": 0},
        "transitionIn": {"type": "fade-to-black", "duration": 0.25}})
    # Simulate an already saved historical record through the real temporary DB.
    assert document.editorial_timeline is not None
    document.editorial_timeline["project"] = as_json(editor)
    service.repository.save_project(document)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    before = reopened.get_project(project_id)
    read = reopened.tool_registry.invoke("timeline.read_editorial", {"project_id": project_id, "include_project": True})
    assert read.error is None and read.output is not None
    project = cast(dict[str, JsonValue], cast(dict[str, JsonValue], read.output["content"])["project"])
    assert _first_video(project)["crop"] == RECTANGLE_CROP
    assert _first_video(read_shot_editorial(reopened, project_id, shots[0]).project)["crop"] == RECTANGLE_CROP
    save_request: dict[str, JsonValue] = {"expected_revision": 1, "timeline": {"project": project}}
    rejected = reopened.tool_registry.invoke("timeline.save_editorial", {"project_id": project_id, "request": save_request})
    assert rejected.error is not None and rejected.receipt is None
    export_request: dict[str, JsonValue] = {"expected_revision": 1, "width": 480, "height": 270, "subtitles": False}
    refused_export = reopened.tool_registry.invoke("timeline.export_editorial", {"project_id": project_id, "request": export_request})
    assert refused_export.error is not None and "/clips/0/crop" in refused_export.error.message
    assert not list_exports(reopened, project_id).exports
    assert reopened.get_project(project_id).document == before.document
    expected = as_json(editor)
    for value in (project, expected):
        if clear_with_null:
            _first_video(value)["crop"] = None
        else:
            del _first_video(value)["crop"]
    accepted = reopened.tool_registry.invoke("timeline.save_editorial", {"project_id": project_id, "request": save_request})
    assert accepted.error is None and accepted.artifact_refs
    current = read_editorial(reopened, project_id)
    assert current.revision == 2
    assert current.project == expected  # linked audio, source IDs, subtitles and other clips survive.
    after = reopened.get_project(project_id)
    assert after.document.shots == before.document.shots and after.takes == before.takes
    export_request["expected_revision"] = 2
    exported = reopened.tool_registry.invoke("timeline.export_editorial", {"project_id": project_id, "request": export_request})
    assert exported.error is None and exported.output is not None
    assert exported.output["status"] == "completed" and exported.output["duration_seconds"] == 12
    output = Path(cast(str, exported.output["video_path"]))
    assert output.is_file()
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-xerror", "-i", str(output),
                    "-map", "0:v", "-map", "0:a", "-f", "null", "-"], check=True, capture_output=True)


def test_unused_legacy_crop_does_not_block_export_of_other_actual_clips(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, project_id, _shots = episode
    editor = EditorProject.model_validate(read_editorial(service, project_id).project)
    alternate = editor.timelines[0].model_copy(deep=True, update={"id": "unused-timeline"})
    assert alternate.clips[0].model_extra is not None
    alternate.clips[0].model_extra["crop"] = deepcopy(EDGE_CROP)
    editor.timelines.append(alternate)
    disabled = editor.timelines[0].clips[0].model_copy(deep=True, update={"id": "unused-clip", "trackIndex": 4})
    assert disabled.model_extra is not None
    disabled.model_extra["crop"] = deepcopy(RECTANGLE_CROP)
    editor.timelines[0].tracks.append(editor.timelines[0].tracks[0].model_copy(update={"id": "disabled", "enabled": False}))
    editor.timelines[0].clips.append(disabled)
    value = as_json(editor)
    assert validate_editorial(service, project_id, value, purpose="export") == editor
    with pytest.raises(ValueError, match="crop"):
        validate_editorial(service, project_id, value)
