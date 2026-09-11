"""Product integration checks with real persistence, media and local FFmpeg."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast
import zipfile

import imageio_ffmpeg
from PIL import Image
from pydantic import JsonValue
import pytest

from short_drama.service import ShortDramaService
from short_drama.models import AssetKind, ShotReferenceCue
from short_drama.h3_models import H3MediaInput
from short_drama.studio_editorial import EditorProject, read_editorial, edit_editorial
from short_drama.studio_export import start_export, recover_interrupted_exports, inspect_export
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import (
    EditorialClipChange, StudioEditorialEdit, StudioEpisodeCreate, StudioExportRequest,
    StudioShotMediaSelect, StudioShotSave, StudioTimelineSave,
)
from short_drama.studio_references import assert_generation_references
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_trace_export import export_studio_trace


@pytest.fixture
def studio(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")


def test_exact_prose_reference_replacement_and_removal(studio: ShortDramaService, tmp_path: Path) -> None:
    initial = studio.initialize_fixture().document
    pid = initial.project.id
    for name, color in [("front", "red"), ("replacement", "blue")]:
        Image.new("RGB", (320, 480), color).save(tmp_path / f"{name}.png")
    imported = import_studio_media(studio, pid, tmp_path / "front.png", name="角色参考", kind=AssetKind.CHARACTER, asset_id=None)
    asset = imported.document.assets[-1]
    old = asset.current_version_id
    shot = imported.document.shots[0].model_copy(deep=True)
    shot.visual = "🙂林一走进教室。"
    shot.references = [ShotReferenceCue(id="first-appearance", subject_asset_id=asset.id, asset_version_id=old,
        alias="lin_front", purpose="角色外观", field="visual", text_start=1, text_end=3)]
    for beat in shot.timeline_beats: beat.references = []
    saved = studio.studio.save_shot(pid, shot.id, StudioShotSave(shot=shot, expected_revision=shot.revision))
    current = saved.project.document.shots[0]
    assert current.visual[current.references[0].text_start:current.references[0].text_end] == "林一"
    plan = next(item for item in saved.project.document.reference_plan if item.shot_id == shot.id)
    assert [item.target_id for item in plan.bindings] == [old]
    media = H3MediaInput.model_validate({"kind": "IMAGE", "role": "reference_image", "asset_version_id": old, "source": {"local_path": asset.versions[-1].local_path}})
    assert_generation_references(saved.project.document, current, [media])
    with pytest.raises(ValueError, match="SHOT_REFERENCES_STALE"):
        assert_generation_references(saved.project.document, current, [])
    second = studio.studio.create_episode(pid, StudioEpisodeCreate(title="共享素材验收"))
    replacement = import_studio_media(studio, pid, tmp_path / "replacement.png", name="新角色参考", kind=AssetKind.CHARACTER, asset_id=asset.id, replace_version_id=old)
    new = next(item.current_version_id for item in replacement.document.assets if item.id == asset.id)
    assert new != old
    assert replacement.document.shots[0].references[0].asset_version_id == new
    assert next(item for item in studio.get_project(second.document.project.id).document.assets if item.id == asset.id).current_version_id == new
    current = replacement.document.shots[0].model_copy(deep=True)
    current.references = []
    removed = studio.studio.save_shot(pid, current.id, StudioShotSave(shot=current, expected_revision=current.revision))
    plan = next(item for item in removed.project.document.reference_plan if item.shot_id == current.id)
    assert plan.bindings == [] and plan.requirements == []


def test_editor_save_reopen_linked_trim_and_actual_export(studio: ShortDramaService, tmp_path: Path) -> None:
    document = studio.initialize_fixture().document
    pid = document.project.id
    source = tmp_path / "source.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=480x270:r=24:d=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True, capture_output=True)
    imported = import_studio_media(studio, pid, source, name="导入片段", kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(studio, pid, document.shots[0].id, StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    value = read_editorial(studio, pid)
    editor = EditorProject.model_validate(value.project)
    timeline = editor.timelines[0]
    video = next(item for item in timeline.clips if item.type == "video")
    assert video.duration <= 4 and video.trimStart == 0
    native = editor.assets[-1].model_copy(deep=True, update={"id": "native", "type": "audio"})
    editor.assets.append(native)
    audio = video.model_copy(deep=True, update={"id": "native-clip", "type": "audio", "assetId": "native", "trackIndex": 2, "linkedClipIds": [video.id]})
    video.muted = True
    if video.model_extra is not None: video.model_extra["linkedClipIds"] = [audio.id]
    timeline.clips.append(audio)
    studio.studio.save_timeline(pid, StudioTimelineSave(timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    edited = edit_editorial(studio, pid, StudioEditorialEdit(expected_revision=1, clips=[EditorialClipChange(id=video.id, source_in_seconds=0.5, duration_seconds=3.5, volume=0.6)]))
    after = EditorProject.model_validate(edited.project).timelines[0]
    assert [(item.trimStart, item.duration) for item in after.clips] == [(0.5, 3.5), (0.5, 3.5)]
    assert after.clips[0].muted and after.clips[1].volume == 0.6
    with pytest.raises(StudioContentConflict):
        edit_editorial(studio, pid, StudioEditorialEdit(expected_revision=1))
    restarted = ShortDramaService(database_path=studio.repository.database_path, workspace_root=studio.workspace_root)
    assert read_editorial(restarted, pid) == edited
    exported = start_export(studio, pid, StudioExportRequest(expected_revision=2, width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == 3.5 and exported.video_path
    checks = json.loads(Path(exported.video_path).with_name("technical-check.json").read_text())
    assert all(checks["checks"].values())
    inspection = inspect_export(studio, pid, exported.id)
    assert inspection["current_editorial_revision"] == 2
    assert len(cast(list[JsonValue], inspection["files"])) == 2
    # An old UI cannot export a different revision under the current edit's name.
    with pytest.raises(StudioContentConflict):
        start_export(studio, pid, StudioExportRequest(expected_revision=1))
    # Replacing a shot after editing keeps the cut and its linked original sound.
    replacement = import_studio_media(studio, pid, source, name="替换片段", kind=AssetKind.REFERENCE, asset_id=None)
    new_version = replacement.document.assets[-1].versions[-1]
    current_shot = replacement.document.shots[0]
    select_imported_video(studio, pid, current_shot.id, StudioShotMediaSelect(asset_version_id=new_version.id, expected_revision=current_shot.revision))
    replaced = read_editorial(studio, pid)
    replaced_clips = EditorProject.model_validate(replaced.project).timelines[0].clips
    assert replaced.revision == 3
    assert [(item.trimStart, item.duration) for item in replaced_clips] == [(0.5, 3.5), (0.5, 3.5)]
    assert replaced_clips[0].muted and replaced_clips[1].volume == 0.6
    assert all(item.asset and item.asset.path == new_version.local_path for item in replaced_clips)
    # The native LTX reader requires resolution even for replacement audio.
    assert all(isinstance(asset.get("resolution"), str)
               for asset in cast(list[dict[str, JsonValue]], replaced.project["assets"]))


def test_prose_saves_all_media_and_keeps_editorial_assets_out_of_h3_inputs(studio: ShortDramaService, tmp_path: Path) -> None:
    from short_drama.h3_route_planner import generation_unit_media_candidates
    from short_drama.models import VideoGenerationRoute
    from short_drama.providers.minimax_h3_compiler import H3CapabilityError, validate_h3_probe

    pid = studio.initialize_fixture().document.project.id
    image = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "blue").save(image)
    cues: list[ShotReferenceCue] = []
    media: list[H3MediaInput] = []
    for index in range(10):
        imported = import_studio_media(studio, pid, image, name=f"参考{index}", kind=AssetKind.REFERENCE, asset_id=None)
        asset = imported.document.assets[-1]
        version = asset.versions[-1]
        cues.append(ShotReferenceCue(subject_asset_id=asset.id, asset_version_id=version.id,
            alias=f"reference_{index}", purpose="标题图版" if index == 9 else "画面外观",
            usage="EDITORIAL" if index == 9 else "GENERATION"))
        media.append(H3MediaInput.model_validate({"kind": "IMAGE", "role": "reference_image",
            "asset_version_id": version.id, "subject_asset_id": asset.id,
            "source": {"local_path": version.local_path}}))
    document = studio.get_project(pid).document
    shot = document.shots[0].model_copy(deep=True)
    shot.references = cues
    for beat in shot.timeline_beats:
        beat.references = []
    result = studio.studio.save_shot(pid, shot.id, StudioShotSave(shot=shot, expected_revision=shot.revision))
    current = result.project.document.shots[0]
    assert len(current.references) == 10
    assert current.references[-1].usage == "EDITORIAL"
    plan = next(item for item in result.project.document.reference_plan if item.shot_id == shot.id)
    assert len(plan.bindings) == 9
    assert_generation_references(result.project.document, current, media[:9])
    with pytest.raises(ValueError, match="SHOT_REFERENCES_STALE"):
        assert_generation_references(result.project.document, current, media[1:9])
    candidates, _warnings = generation_unit_media_candidates(result.project.document, [current])
    assert {item.media_input.asset_version_id for item in candidates} == {item.asset_version_id for item in media[:9]}

    # Saving an over-capacity design is allowed; only a concrete H3 request fails.
    current.references[-1].usage = "GENERATION"
    studio.studio.save_shot(pid, current.id, StudioShotSave(shot=current, expected_revision=current.revision))
    reopened = studio.get_project(pid).document
    assert len(reopened.shots[0].references) == 10
    assert len(next(item for item in reopened.reference_plan if item.shot_id == shot.id).bindings) == 10
    with pytest.raises(H3CapabilityError, match="nine reference images"):
        validate_h3_probe(route=VideoGenerationRoute.REFERENCE, prompt="Use these references.", duration_seconds=5, media_inputs=media)


def test_interrupted_local_export_is_retryable_and_trace_contains_current_edit(studio: ShortDramaService) -> None:
    document = studio.initialize_fixture().document
    document.studio_exports = [{"id": "interrupted", "project_id": document.project.id, "editorial_revision": 1, "status": "running", "width": 480, "height": 270, "fps": 24, "created_at": "2026-09-09T00:00:00Z"}]
    studio.repository.save_project(document)
    recover_interrupted_exports(studio)
    assert studio.get_project(document.project.id).document.studio_exports[0]["status"] == "failed"
    studio.repository.append_studio_event(document.project.id, "chat.user", {"text": "用户直接补齐素材；保留这次操作。"})
    archive = export_studio_trace(studio, document.project.id)
    with zipfile.ZipFile(archive) as file:
        assert "用户直接补齐素材" in file.read("conversation.jsonl").decode()
        assert json.loads(file.read("project.json"))["document"]["studio_exports"][0]["status"] == "failed"


def test_comfy_adapter_uploads_exact_bound_file_and_imports_real_http_output(studio: ShortDramaService, tmp_path: Path) -> None:
    from tests.fakes.fake_comfy_server import fake_comfy_server
    from short_drama.studio_comfy import ComfyConfigUpdate, ComfyRunRequest, comfy_config, run_comfy
    document = studio.initialize_fixture().document
    path = tmp_path / "selected-image.png"
    Image.new("RGB", (320, 480), "green").save(path)
    value = import_studio_media(studio, document.project.id, path, name="待处理素材", kind=AssetKind.REFERENCE, asset_id=None)
    version = value.document.assets[-1].versions[-1]
    with fake_comfy_server() as (address, state):
        assert comfy_config(studio, ComfyConfigUpdate(base_url=address)).connected
        request = ComfyRunRequest.model_validate({"name": "导入处理验收", "workflow": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "output"}},
        }, "bindings": [{"node_id": "1", "input_name": "image", "asset_version_id": version.id}]})
        record = run_comfy(studio, document.project.id, request, background=False)
        assert record.status == "completed", record.error
        assert state["upload"] == path.read_bytes()
        assert state["prompt"]["1"]["inputs"]["image"] == state["filename"]
        output = next(item for item in studio.get_project(document.project.id).document.assets if item.id in record.output_asset_ids)
        assert output.versions[-1].local_path
        assert Path(output.versions[-1].local_path).read_bytes() == path.read_bytes()
        assert "ComfyUI" in (output.versions[-1].review_notes or "")
        # A restart follows the known prompt; it does not upload/submit again.
        from short_drama.studio_comfy import recover_interrupted_workflows
        pending = studio.repository.get_project(document.project.id)
        assert pending
        pending.comfy_workflows[-1]["status"] = "running"
        pending.comfy_workflows[-1]["output_asset_ids"] = []
        studio.repository.save_project(pending)
        state["prompt"] = None
        recover_interrupted_workflows(studio, background=False)
        assert state["prompt"] is None
        assert studio.get_project(document.project.id).document.comfy_workflows[-1]["status"] == "completed"
        with zipfile.ZipFile(export_studio_trace(studio, document.project.id)) as archive:
            saved = json.loads(archive.read(f"workflows/{record.id}/workflow.json"))
            assert saved["request"]["bindings"][0]["asset_version_id"] == version.id
            assert saved["submitted_graph"]["1"]["inputs"]["image"] == state["filename"]
            result = json.loads(archive.read(f"workflows/{record.id}/result.json"))
            assert result["outputs"]
            summary = json.loads(archive.read("production-summary.json"))
            assert summary["workflows"][-1]["id"] == record.id
            assert summary["workflows"][-1]["status"] == "completed"
