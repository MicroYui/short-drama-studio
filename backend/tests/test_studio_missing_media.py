"""Missing library media must not block unrelated saved edits or current exports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from PIL import Image
from pydantic import JsonValue
import pytest

from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorClip, EditorProject, read_editorial, validate_editorial
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave


@dataclass
class MediaProject:
    service: ShortDramaService
    project: EditorProject
    old_id: str
    old_path: Path
    current_id: str
    current_path: Path

    def save(self, revision: int = 0) -> int:
        result = self.service.studio.save_timeline(self.project.id, StudioTimelineSave(
            expected_revision=revision, timeline={"project": self.payload()}))
        return int(cast(int, (result.project.document.editorial_timeline or {})["revision"]))

    def payload(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.project.model_dump(mode="json"))

    def export(self, revision: int) -> None:
        result = start_export(self.service, self.project.id,
            StudioExportRequest(expected_revision=revision, width=320, height=320), background=False)
        assert result.status == "completed", result.error
        assert result.duration_seconds == 1 and result.video_path and Path(result.video_path).is_file()


@pytest.fixture
def media_project(tmp_path: Path) -> MediaProject:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 320), "red").save(source)
    first = import_studio_media(service, pid, source, name="旧参考", kind=AssetKind.REFERENCE, asset_id=None)
    subject = first.document.assets[-1]
    old = subject.versions[-1]
    Image.new("RGB", (320, 320), "blue").save(source)
    second = import_studio_media(service, pid, source, name="当前画面", kind=AssetKind.REFERENCE, asset_id=subject.id)
    subject = next(item for item in second.document.assets if item.id == subject.id)
    current = next(item for item in subject.versions if item.id == subject.current_version_id)
    project = EditorProject.model_validate(read_editorial(service, pid).project)
    project.timelines[0].clips = [EditorClip(id="current-clip", assetId=current.id, type="image", startTime=0,
        duration=1, trimStart=0, trackIndex=0)]
    project.timelines[0].subtitles = []
    return MediaProject(service, project, old.id, Path(old.local_path or ""), current.id, Path(current.local_path or ""))


def test_unused_missing_history_can_save_reopen_and_export(media_project: MediaProject) -> None:
    case = media_project
    case.old_path.unlink()
    assert all(clip.assetId != case.old_id for timeline in case.project.timelines for clip in timeline.clips)
    revision = case.save()
    reopened = read_editorial(case.service, case.project.id)
    assert reopened.revision == revision
    assert case.old_id in {asset.id for asset in EditorProject.model_validate(reopened.project).assets}
    case.export(revision)


def test_missing_adopted_media_identifies_timeline_clip_asset_and_position(media_project: MediaProject) -> None:
    case = media_project
    revision = case.save()
    case.current_path.unlink()
    purposes: tuple[Literal["save", "export"], ...] = ("save", "export")
    for purpose in purposes:
        with pytest.raises(FileNotFoundError) as caught:
            validate_editorial(case.service, case.project.id, case.payload(), purpose=purpose)
        message = str(caught.value)
        for expected in (case.project.timelines[0].id, "video-1", "current-clip", case.current_id, "0–1 秒", str(case.current_path)):
            assert expected in message
    with pytest.raises(FileNotFoundError, match="current-clip"):
        case.save(revision)
    with pytest.raises(FileNotFoundError, match="current-clip"):
        case.export(revision)
    assert not case.service.get_project(case.project.id).document.studio_exports
    assert read_editorial(case.service, case.project.id).revision == revision


@pytest.mark.parametrize("scope", ["inactive_timeline", "disabled_track", "other_track_solo"])
def test_export_only_requires_media_on_active_output_tracks(media_project: MediaProject, scope: str) -> None:
    case = media_project
    timeline = case.project.timelines[0]
    unused_clip = timeline.clips[0].model_copy(deep=True, update={"id": "elsewhere-clip", "assetId": case.old_id, "trackIndex": 1})
    if scope == "inactive_timeline":
        other = timeline.model_copy(deep=True, update={"id": "other-timeline", "name": "另一时间线", "clips": [unused_clip]})
        case.project.timelines.append(other)
    else:
        timeline.clips.append(unused_clip)
        if scope == "disabled_track":
            timeline.tracks[1].enabled = False
            assert unused_clip.model_extra is not None
            unused_clip.model_extra["transitionIn"] = {"type": "dissolve", "duration": 0.5}
        else:
            timeline.tracks[0].solo = True
    revision = case.save()
    case.old_path.unlink()
    if scope == "inactive_timeline":
        # Saving still checks media used by all enabled saved timelines.
        with pytest.raises(FileNotFoundError, match="other-timeline"):
            case.save(revision)
    else:
        revision = case.save(revision)
    case.export(revision)


def test_zero_opacity_image_still_requires_file_consumed_by_renderer(media_project: MediaProject) -> None:
    case = media_project
    timeline = case.project.timelines[0]
    timeline.clips.append(timeline.clips[0].model_copy(deep=True,
        update={"id": "hidden-image", "assetId": case.old_id, "opacity": 0, "trackIndex": 1}))
    revision = case.save()
    case.old_path.unlink()
    with pytest.raises(FileNotFoundError, match="hidden-image"):
        case.export(revision)


@pytest.mark.parametrize("unregistered_exists", [True, False])
def test_unused_library_paths_still_must_belong_to_project(media_project: MediaProject, tmp_path: Path, unregistered_exists: bool) -> None:
    case = media_project
    unregistered = tmp_path / "not-imported.png"
    if unregistered_exists:
        Image.new("RGB", (320, 320), "green").save(unregistered)
    old = next(item for item in case.project.assets if item.id == case.old_id)
    old.path = str(unregistered)
    with pytest.raises(ValueError, match=f"素材 {case.old_id}.*未登记"):
        case.save()


@pytest.mark.parametrize("invalid", ["missing_asset", "wrong_type", "window"])
def test_offline_timeline_still_checks_bindings_and_source_windows(media_project: MediaProject, invalid: str) -> None:
    case = media_project
    timeline = case.project.timelines[0]
    timeline.tracks[0].enabled = False
    clip = timeline.clips[0]
    asset = next(item for item in case.project.assets if item.id == case.current_id)
    if invalid == "missing_asset":
        clip.assetId = "not-an-asset"
    elif invalid == "wrong_type":
        clip.type = "video"
    else:
        # Registration and stored duration remain meaningful even while offline.
        asset.type = clip.type = "video"
        asset.duration = 1
        clip.duration = 2
    case.current_path.unlink()
    with pytest.raises(ValueError, match="有效素材|超出了原素材时长"):
        validate_editorial(case.service, case.project.id, case.payload(), purpose="export")


def test_required_file_matches_explicit_take_not_unused_base_asset(media_project: MediaProject) -> None:
    case = media_project
    clip = case.project.timelines[0].clips[0]
    clip.assetId = case.old_id
    asset = next(item for item in case.project.assets if item.id == case.old_id)
    assert asset.model_extra is not None and clip.model_extra is not None
    asset.model_extra["takes"] = [{"path": str(case.current_path), "duration": 1}]
    clip.model_extra["takeIndex"] = 0
    case.old_path.unlink()
    revision = case.save()
    case.current_path.unlink()
    with pytest.raises(FileNotFoundError) as caught:
        case.export(revision)
    assert case.old_id in str(caught.value) and str(case.current_path) in str(caught.value)


def test_selected_take_still_requires_registered_media(media_project: MediaProject, tmp_path: Path) -> None:
    case = media_project
    clip = case.project.timelines[0].clips[0]
    asset = next(item for item in case.project.assets if item.id == clip.assetId)
    assert asset.model_extra is not None and clip.model_extra is not None
    asset.model_extra["takes"] = [{"path": str(tmp_path / "not-imported.png")}]
    clip.model_extra["takeIndex"] = 0
    with pytest.raises(ValueError, match="current-clip.*未登记"):
        case.save()
