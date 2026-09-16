"""Moved media remains the same content across episodes and actual export."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import shutil
import sqlite3
import subprocess
from threading import Event
from time import monotonic
from typing import cast

import imageio_ffmpeg
import pytest
from pydantic import JsonValue

from short_drama.service import ShortDramaService
from short_drama.repository import ShortDramaRepository
from short_drama.models import AssetKind
from short_drama.voice_models import VoiceReferenceUpdateRequest
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media
from short_drama.studio_media_recovery import media_usage, relink_media
from short_drama.studio_models import StudioEpisodeCreate, StudioExportRequest, StudioTimelineSave
from tests.test_studio_shot_editorial import episode as episode


def test_relink_updates_both_episode_timelines_without_changing_content(episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path) -> None:
    service, pid, _ = episode
    before = service.get_project(pid)
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    original_asset = editor.timelines[0].clips[0].asset
    assert original_asset is not None
    source = Path(original_asset.path)
    child = service.studio.create_episode(pid, StudioEpisodeCreate(title="共享素材第二集"))
    child_editor = editor.model_copy(deep=True, update={"id": child.document.project.id})
    for timeline in child_editor.timelines:
        for clip in timeline.clips:
            if clip.model_extra:
                clip.model_extra.pop("sourceShotId", None)
                clip.model_extra.pop("sourceTakeId", None)
    service.studio.save_timeline(child.document.project.id, StudioTimelineSave(timeline={"project": cast(dict[str, JsonValue], child_editor.model_dump(mode="json"))}))
    uses = media_usage(service, pid, path=str(source)).uses
    assert {use.episode_id for use in uses if use.kind == "editorial"} == {pid, child.document.project.id}
    moved = tmp_path / "moved.mp4"
    shutil.copy2(source, moved)
    source.unlink()
    restored = relink_media(service, pid, str(source), moved)
    assert set(restored.affected_episode_ids) == {pid, child.document.project.id}
    assert restored.project.document.shots == before.document.shots
    assert [take.id for take in restored.project.takes] == [take.id for take in before.takes]
    assert all(take.media_path == restored.path for take in restored.project.takes)
    for episode_id in restored.affected_episode_ids:
        saved = read_editorial(service, episode_id)
        assert saved.revision == 2
        current = EditorProject.model_validate(saved.project)
        assert current.timelines[0].subtitles == editor.timelines[0].subtitles
        assert [clip.duration for clip in current.timelines[0].clips] == [clip.duration for clip in editor.timelines[0].clips]
        assert all(asset.path == restored.path for asset in current.assets)
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    result = start_export(restarted, pid, StudioExportRequest(expected_revision=2, width=480, height=270), background=False)
    assert result.status == "completed", result.error
    assert result.duration_seconds == 12


def test_relink_rejects_a_different_media_shape_without_writing(episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path) -> None:
    from PIL import Image
    service, pid, _ = episode
    before = service.get_project(pid).document.model_dump_json()
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    asset = editor.timelines[0].clips[0].asset
    assert asset is not None
    wrong = tmp_path / "wrong.png"
    Image.new("RGB", (10, 10), "red").save(wrong)
    with pytest.raises(ValueError, match="类型|尺寸|时长"):
        relink_media(service, pid, asset.path, wrong)
    assert service.get_project(pid).document.model_dump_json() == before


def test_usage_keeps_exact_file_scope(episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path) -> None:
    service, pid, _ = episode
    asset = service.get_project(pid).document.assets[-1]
    version = asset.versions[-1]
    assert any(use.kind == "editorial" for use in media_usage(service, pid, asset_version_id=version.id).uses)
    with pytest.raises(ValueError, match="未登记"):
        media_usage(service, pid, path=str(tmp_path / "unrelated.mp4"))


@pytest.mark.parametrize("replacement_suffix", [".mp4", ".wav"])
def test_audio_relink_requires_actual_audio_before_writing(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path, replacement_suffix: str,
) -> None:
    service, pid, _ = episode
    audio = tmp_path / "voice.wav"
    silent = tmp_path / "silent.mp4"
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([executable, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=8", str(audio)], check=True, capture_output=True)
    imported = import_studio_media(service, pid, audio, name="声音", kind=AssetKind.REFERENCE, asset_id=None)
    source = imported.document.assets[-1].versions[-1].local_path
    assert source
    subprocess.run([executable, "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x180:r=24:d=8", "-c:v", "libx264", str(silent)], check=True, capture_output=True)
    replacement = tmp_path / f"wrong{replacement_suffix}"
    shutil.copy2(silent, replacement)
    before = service.get_project(pid).document.model_dump_json()
    with pytest.raises(ValueError, match="类型|音轨"):
        relink_media(service, pid, source, replacement)
    assert service.get_project(pid).document.model_dump_json() == before
    assert not (service.workspace_root / pid / "relinked-media").exists()


def test_voice_relink_preserves_provider_usable_format(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, _ = episode
    audio = tmp_path / "voice.wav"
    replacement = tmp_path / "voice.m4a"
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([executable, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=8", str(audio)], check=True, capture_output=True)
    imported = import_studio_media(service, pid, audio, name="声音", kind=AssetKind.REFERENCE, asset_id=None)
    asset = imported.document.assets[-1]
    bible = next(item for item in imported.document.bible_versions if item.id == imported.document.project.current_bible_version_id)
    service.update_character_voice_reference(pid, bible.characters[0].id, VoiceReferenceUpdateRequest(asset_id=asset.id))
    subprocess.run([executable, "-v", "error", "-y", "-i", str(audio), str(replacement)], check=True, capture_output=True)
    source = asset.versions[-1].local_path
    assert source
    before = service.get_project(pid).document.model_dump_json()
    with pytest.raises(ValueError, match="角色声音参考"):
        relink_media(service, pid, source, replacement)
    assert service.get_project(pid).document.model_dump_json() == before


def test_usage_ignores_unadopted_take_and_projects_unsaved_adoption(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, shot_ids = episode
    aggregate = service.get_project(pid)
    source = aggregate.takes[0]
    candidate_path = tmp_path / "unused-candidate.mp4"
    shutil.copy2(source.media_path, candidate_path)
    candidate = source.model_copy(deep=True, update={"id": "unused-candidate", "media_path": str(candidate_path),
        "is_selected": False, "selected_shot_ids": [], "candidate_index": 99})
    service.repository.save_take(candidate)
    assert media_usage(service, pid, path=str(candidate_path)).uses == []

    aggregate.document.editorial_timeline = None
    aggregate.document.manifests = []
    aggregate.document.project.current_manifest_id = None
    service.repository.save_project(aggregate.document)
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    uses = media_usage(service, pid, path=source.media_path).uses
    assert {use.shot_id for use in uses if use.kind == "shot"} == set(shot_ids)
    assert {(use.timeline_id, use.target_id, use.start_seconds, use.end_seconds) for use in uses if use.kind == "editorial"} == {
        (timeline.id, clip.id, clip.startTime, clip.startTime + clip.duration)
        for timeline in editor.timelines for clip in timeline.clips
    }
    assert service.get_project(pid).document.editorial_timeline is None


def test_usage_distinguishes_nonactive_and_disabled_timeline_locations(episode: tuple[ShortDramaService, str, list[str]]) -> None:
    service, pid, _ = episode
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    alternate = editor.timelines[0].model_copy(deep=True, update={"id": "other-timeline", "name": "备用时间线"})
    alternate.tracks[alternate.clips[0].trackIndex].enabled = False
    editor.timelines.append(alternate)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
    uses = media_usage(service, pid, path=editor.assets[0].path).uses
    alternate_uses = [use for use in uses if use.kind == "editorial" and use.timeline_id == alternate.id]
    assert alternate_uses and all("备用时间线" in use.label and "非当前时间线" in use.label for use in alternate_uses)
    assert any("轨道已停用" in use.label for use in alternate_uses)


def test_relink_includes_episode_created_during_copy_and_preserves_concurrent_edit(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, _ = episode
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    source = editor.assets[0].path
    replacement = tmp_path / "moved.mp4"
    shutil.copy2(source, replacement)
    with ThreadPoolExecutor(max_workers=1) as pool:
        # The actual file copy runs outside the content lock. Creating an episode
        # here deterministically reproduces a new reference during that phase.
        with service.studio.lock:
            pending = pool.submit(relink_media, service, pid, source, replacement)
            deadline = monotonic() + 10
            copied = service.workspace_root / pid / "relinked-media"
            while not list(copied.glob("*.mp4")):
                assert not pending.done(), pending.exception()
                assert monotonic() < deadline, "Relink did not reach its copy phase"
                Event().wait(0.01)
            child = service.studio.create_episode(pid, StudioEpisodeCreate(title="复制期间新建的集"))
            child_id = child.document.project.id
            child_editor = editor.model_copy(deep=True, update={"id": child_id})
            for timeline in child_editor.timelines:
                for clip in timeline.clips:
                    if clip.model_extra:
                        clip.model_extra.pop("sourceShotId", None)
                        clip.model_extra.pop("sourceTakeId", None)
            service.studio.save_timeline(child_id, StudioTimelineSave(timeline={"project": cast(dict[str, JsonValue], child_editor.model_dump(mode="json"))}))
            editor.timelines[0].subtitles[0].text = "复制期间用户刚保存的字幕"
            service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=1, timeline={"project": cast(dict[str, JsonValue], editor.model_dump(mode="json"))}))
        restored = pending.result(timeout=10)
    assert set(restored.affected_episode_ids) == {pid, child_id}
    for episode_id in (pid, child_id):
        saved = read_editorial(service, episode_id)
        current = EditorProject.model_validate(saved.project)
        assert all(asset.path == restored.path for asset in current.assets)
    parent = read_editorial(service, pid)
    assert parent.revision == 3
    assert EditorProject.model_validate(parent.project).timelines[0].subtitles[0].text == "复制期间用户刚保存的字幕"
    assert read_editorial(service, child_id).revision == 2


def test_relink_rolls_back_documents_takes_and_events_after_real_sqlite_failure(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> None:
    service, pid, _ = episode
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    source = editor.assets[0].path
    replacement = tmp_path / "moved.mp4"
    shutil.copy2(source, replacement)
    child_id = service.studio.create_episode(pid, StudioEpisodeCreate(title="事务回滚第二集")).document.project.id
    database = service.repository.database_path

    def stored_rows() -> list[list[tuple[object, ...]]]:
        with sqlite3.connect(database) as connection:
            return [connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                    for table in ("projects", "takes", "studio_events")]

    before = stored_rows()
    with sqlite3.connect(database) as connection:
        # This final event can only fail after documents AND Takes already point
        # to the new file inside the transaction. No substitute repository is used.
        connection.executescript(f"""
            CREATE TRIGGER fail_relink_event BEFORE INSERT ON studio_events
            WHEN NEW.project_id = '{child_id}' AND NEW.kind = 'assets.updated'
                 AND json_extract(NEW.data_json, '$.relinked_path') IS NOT NULL
            BEGIN
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM takes WHERE project_id = '{pid}' AND
                    json_extract(record_json, '$.media_path') = json_extract(NEW.data_json, '$.relinked_path')
                ) THEN RAISE(ABORT, 'takes were not updated before the failure') END;
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM projects WHERE id = '{pid}' AND
                    instr(document_json, json_extract(NEW.data_json, '$.relinked_path')) > 0
                ) THEN RAISE(ABORT, 'project was not updated before the failure') END;
                SELECT RAISE(ABORT, 'forced relink event failure');
            END;
        """)
    with pytest.raises(sqlite3.IntegrityError, match="forced relink event failure"):
        relink_media(service, pid, source, replacement)
    assert stored_rows() == before
    assert list((service.workspace_root / pid / "relinked-media").iterdir()) == []
    assert replacement.is_file() and Path(source).is_file()

    reopened = ShortDramaRepository(database)
    for episode_id in (pid, child_id):
        document = reopened.get_project(episode_id)
        assert document is not None
        assert any(version.local_path == source for asset in document.assets for version in asset.versions)
        assert reopened.list_studio_events(episode_id) == service.repository.list_studio_events(episode_id)
    assert all(take.media_path == source for take in reopened.list_takes(pid))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_relink_event")
    restored = relink_media(service, pid, source, replacement)
    assert set(restored.affected_episode_ids) == {pid, child_id}
    assert all(take.media_path == restored.path for take in reopened.list_takes(pid))
    assert all(any(event.data.get("relinked_path") == restored.path
                   for event in reopened.list_studio_events(episode_id)) for episode_id in (pid, child_id))
