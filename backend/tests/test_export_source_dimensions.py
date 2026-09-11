"""Selected source detail survives local media ingestion, editorial and export."""

from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
import pytest
from pydantic import JsonValue, ValidationError

from short_drama.export_dimensions import source_output_dimensions
from short_drama.media import probe_media_with_ffmpeg
from short_drama.models import AssetKind, OutputSpec
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import _selected_video_dimensions
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioExport, StudioExportRequest, StudioShotMediaSelect, StudioTimelineSave
from short_drama.tools.contracts import ToolCallStatus


def _video(path: Path, width: int = 1152, height: int = 2048) -> None:
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=blue:s={width}x{height}:r=24:d=0.5", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, check=True)


def _selected_project(tmp_path: Path) -> tuple[ShortDramaService, str]:
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    document.shots = document.shots[:1]
    document.shots[0].duration_seconds = 0.5
    document.shots[0].dialogue_line_ids = []
    next(script for script in document.script_versions
         if script.id == document.project.current_script_version_id).lines = []
    service.repository.save_project(document)
    source = tmp_path / "source.mp4"
    _video(source)
    imported = import_studio_media(service, document.project.id, source, name="Original vertical detail",
        kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, document.project.id, document.shots[0].id,
        StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    return service, document.project.id


def test_default_tool_export_keeps_source_dimensions_and_explicit_size_is_honored(tmp_path: Path) -> None:
    service, pid = _selected_project(tmp_path)
    aggregate = service.get_project(pid)
    take = aggregate.takes[-1]
    assert (take.width, take.height) == (1152, 2048)
    editorial = read_editorial(service, pid)
    projected = EditorProject.model_validate(editorial.project)
    source_asset = next(asset for asset in projected.assets if asset.id == take.id)
    assert (source_asset.model_extra or {}).get("width") == 1152
    assert source_asset.path == take.media_path
    service.studio.save_timeline(pid, StudioTimelineSave(timeline={"project": editorial.project}))
    revision = read_editorial(service, pid).revision
    result = service.tool_registry.invoke("timeline.export_editorial", {
        "project_id": pid, "request": {"expected_revision": revision},
    })
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    exported = StudioExport.model_validate(result.output)
    assert (exported.width, exported.height) == (1152, 2048)
    assert exported.video_path
    assert probe_media_with_ffmpeg(Path(exported.video_path))["size"] == "1152x2048"
    manual = service.tool_registry.invoke("timeline.export_editorial", {
        "project_id": pid, "request": {"expected_revision": revision, "width": 720, "height": 1280},
    })
    assert manual.status == ToolCallStatus.SUCCEEDED, manual.error
    explicit = StudioExport.model_validate(manual.output)
    assert explicit.video_path and probe_media_with_ffmpeg(Path(explicit.video_path))["size"] == "720x1280"
    assert probe_media_with_ffmpeg(Path(take.media_path))["size"] == "1152x2048"


def test_legacy_manifest_follows_sources_and_preserves_explicit_output_on_rebuild(tmp_path: Path) -> None:
    service, pid = _selected_project(tmp_path)
    manifest = service.rebuild_manifest(pid)
    assert manifest.output_spec.follow_source_dimensions
    assert (manifest.output_spec.width, manifest.output_spec.height) == (1152, 2048)
    exported = service.export_project(pid, rebuild_manifest=False)
    assert probe_media_with_ffmpeg(Path(exported.clean_video_path))["size"] == "1152x2048"
    # A rebuilt automatic manifest follows changed project framing after restart.
    document = service.get_project(pid).document
    document.project.aspect_ratio = "4:3"
    service.repository.save_project(document)
    service = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    reframed = service.rebuild_manifest(pid)
    assert (reframed.output_spec.width, reframed.output_spec.height) == (2732, 2048)
    document = service.get_project(pid).document
    document.manifests[-1].output_spec = OutputSpec(width=720, height=1280)
    service.repository.save_project(document)
    manual = service.rebuild_manifest(pid)
    assert not manual.output_spec.follow_source_dimensions
    assert (manual.output_spec.width, manual.output_spec.height) == (720, 1280)


def test_default_geometry_uses_renderer_take_selection_not_asset_label_or_hidden_tracks(tmp_path: Path) -> None:
    service, pid = _selected_project(tmp_path)
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    timeline = editor.timelines[0]
    clip = next(item for item in timeline.clips if item.type == "video")
    asset = next(item for item in editor.assets if item.id == clip.assetId)
    small = tmp_path / "small.mp4"
    _video(small, 288, 512)
    assert asset.model_extra is not None and clip.model_extra is not None
    asset.model_extra.update({"width": 720, "height": 1280, "activeTakeIndex": 0,
        "takes": [{"path": str(small)}]})
    # activeTakeIndex doesn't switch this clip in the actual exporter.
    assert _selected_video_dimensions(editor) == [(1152, 2048)]
    clip.model_extra["takeIndex"] = 0
    hidden = clip.model_copy(deep=True, update={"id": "hidden", "trackIndex": 1})
    assert hidden.model_extra is not None
    hidden.model_extra.pop("takeIndex")
    timeline.tracks[1].enabled = False
    timeline.clips.append(hidden)
    assert _selected_video_dimensions(editor) == [(288, 512)]
    # Stored JSON keeps the same concrete selection across a reload.
    restored = EditorProject.model_validate(cast(dict[str, JsonValue], editor.model_dump(mode="json")))
    assert _selected_video_dimensions(restored) == [(288, 512)]


def test_empty_video_fallback_and_explicit_pair_contract() -> None:
    assert source_output_dimensions([], "9:16") == (720, 1280)
    assert source_output_dimensions([], "1:1") == (1080, 1080)
    assert source_output_dimensions([(1920, 1080)], "4:3") == (1920, 1440)
    assert StudioExportRequest(expected_revision=1).width is None
    with pytest.raises(ValidationError, match="together"):
        StudioExportRequest(expected_revision=1, width=1152)
