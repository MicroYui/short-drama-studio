"""Explicit reuse of observed media keeps historical inputs and current edits."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
from pydantic import JsonValue
import pytest

from short_drama.models import (
    Freshness, GenerationUnitShotWindow, ProductionDocumentOrigin, QaStatus, Take,
    TakeAvailability,
)
from short_drama.agent_runtime.completion import verify_completion_claims
from short_drama.agent_runtime.models import ArtifactRef
from short_drama.production_contracts import (
    ProductionSpecCreateRequest, StoryboardCreateRequest, StoryboardPatchRequest,
    StoryboardShotReplacement,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def observed_candidate(
    episode: tuple[ShortDramaService, str, list[str]], tmp_path: Path,
) -> tuple[ShortDramaService, str, Take, str]:
    service, pid, ids = episode
    target_id = ids[1]
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(
        spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    source = _storyboard(service, pid, production_spec_document_id=spec.document.id,
        production_spec_version_id=spec.version.id)
    shot = next(item for item in source.shots if item.id == target_id)
    shot.duration_seconds = 10.5
    group = next(item for item in source.clip_groups if target_id in item.shot_ids)
    group.duration_seconds = 10.5
    source.computed_duration_seconds = sum(item.duration_seconds for item in source.shots)
    board = service.create_storyboard(pid, StoryboardCreateRequest(
        storyboard=source, origin=ProductionDocumentOrigin.AGENT))
    original = next(item for item in service.get_project(pid).takes if item.shot_id == target_id)
    task = service.repository.get_task(original.task_id)
    assert task is not None
    media = tmp_path / "observed-ending.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=320x180:r=24:d=11.54", "-f", "lavfi", "-i", "sine=frequency=880:duration=11.54",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(media)], check=True, capture_output=True)
    window = GenerationUnitShotWindow(shot_id=target_id, shot_revision=shot.revision,
        source_in_seconds=0, source_out_seconds=10.5, duration_seconds=10.5)
    task = task.model_copy(deep=True, update={"id": "observed-ending-task", "provider": "minimax",
        "model": "MiniMax-H3", "storyboard_document_id": board.document.id,
        "storyboard_version_id": board.version.id, "generation_unit_id": group.id,
        "clip_group_id": group.id, "shot_windows": [window],
        "input_shot_revisions": {target_id: shot.revision}, "input_shot_revision": shot.revision})
    service.repository.save_task(task)
    candidate = original.model_copy(deep=True, update={"id": "observed-ending", "task_id": task.id,
        "provider": task.provider, "model": task.model, "storyboard_document_id": board.document.id,
        "storyboard_version_id": board.version.id, "generation_unit_id": group.id,
        "clip_group_id": group.id, "shot_windows": [window], "media_path": str(media),
        "duration_seconds": 11.54, "qa_status": QaStatus.PENDING,
        "freshness": Freshness.CURRENT, "is_selected": False, "selected_shot_ids": []})
    candidate.qa_report.status = QaStatus.PENDING
    candidate.qa_report.hard_gate_passed = False
    service.repository.save_take(candidate)
    observed = shot.model_copy(deep=True, update={"duration_seconds": 11.54,
        "camera": "原镜头后靠在9.30–10.50秒；完整素材11.54秒，剪辑保留尾部。",
        "framing": "拟裁去下方桌缘；尚未执行，不宣称衔接已通过。"})
    service.patch_storyboard(pid, StoryboardPatchRequest(base_version_id=board.version.id,
        replacements=[StoryboardShotReplacement(shot_id=target_id, expected_revision=shot.revision,
            replacement=observed)], clip_groups=[group.model_copy(deep=True, update={
                "duration_seconds": 11.54, "generation_intent": "保留实物问答与后靠，不重新生成。",
                "reference_notes": ["原时长10.5秒是设计；原片11.54秒，待剪辑处理。"]})],
        origin=ProductionDocumentOrigin.AGENT))
    stale = service.repository.get_take(candidate.id)
    assert stale is not None and stale.freshness == Freshness.STALE
    assert stale.storyboard_version_id == board.version.id
    editor = EditorProject.model_validate(read_editorial(service, pid).project)
    clip = next(item for item in editor.timelines[0].clips if item.type == "video"
        and (item.model_extra or {}).get("sourceShotId") == target_id)
    return service, pid, stale, clip.id


@pytest.mark.parametrize("explicit_source", [False, True])
def test_observed_timing_and_treatment_allow_adoption_without_refreshing_source(
    observed_candidate: tuple[ShortDramaService, str, Take, str], explicit_source: bool,
) -> None:
    service, pid, candidate, clip_id = observed_candidate
    before = service.get_project(pid)
    saved = read_editorial(service, pid)
    old = EditorProject.model_validate(saved.project)
    video = next(item for item in old.timelines[0].clips if item.id == clip_id)
    affected = {clip_id, *cast(list[str], (video.model_extra or {})["linkedClipIds"])}
    result = service.select_take(candidate.id, shot_ids=[candidate.shot_id],
        clip_ids=[clip_id] if explicit_source else None,
        expected_editorial_revision=saved.revision if explicit_source else None,
        source_in_seconds=7.5 if explicit_source else None, source_out_seconds=11.5 if explicit_source else None)
    chosen = next(item for item in result.takes if item.id == candidate.id)
    assert chosen.freshness == Freshness.STALE and chosen.selected_shot_ids == [candidate.shot_id]
    assert chosen.model_dump(exclude={"selected_shot_ids", "is_selected"}) == candidate.model_dump(exclude={"selected_shot_ids", "is_selected"})
    assert result.document.shots == before.document.shots
    current = read_editorial(service, pid)
    edited = EditorProject.model_validate(current.project)
    for clip in edited.timelines[0].clips:
        original = next(item for item in old.timelines[0].clips if item.id == clip.id)
        if clip.id not in affected:
            assert clip == original
        else:
            assert clip.asset is not None and clip.asset.path == candidate.media_path
            assert (clip.startTime, clip.duration, clip.speed) == (original.startTime, original.duration, original.speed)
            assert clip.trimStart == (7.5 if explicit_source else 0)
    assert edited.timelines[0].subtitles == old.timelines[0].subtitles
    assert edited.timelines[0].tracks == old.timelines[0].tracks
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    assert reopened.repository.get_take(candidate.id) == chosen
    receipts = verify_completion_claims(reopened.repository, project_id=pid,
        artifact_refs=[ArtifactRef(project_id=pid, artifact_type="take", artifact_id=candidate.id)])
    assert receipts and "STALE" in str(receipts)
    assert read_editorial(reopened, pid) == current
    # No new save or promotion when the same current use is reconfirmed.
    reopened.select_take(candidate.id, shot_ids=[candidate.shot_id])
    assert read_editorial(reopened, pid) == current
    assert reopened.repository.get_take(candidate.id) == chosen
    # Automatic generation-source assembly remains deliberately strict.
    with pytest.raises(WorkflowConflictError, match="current technically available"):
        reopened.rebuild_manifest(pid)
    shot = next(item for item in result.document.shots if item.id == candidate.shot_id)
    with pytest.raises(WorkflowConflictError, match="outdated Shot revision"):
        reopened._validate_timeline_take_source(result.document, chosen, shot,
            storyboard_document_id=result.document.storyboard_document_id,
            storyboard_version_id=reopened.get_storyboard(pid).version.id)
    export = start_export(reopened, pid, StudioExportRequest(expected_revision=current.revision,
        width=480, height=270, fps=24), background=False)
    assert export.status == "completed", export.error
    assert export.video_path is not None
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", export.video_path,
        "-f", "null", "-"], check=True, capture_output=True)
    assert not decoded.stderr


@pytest.mark.parametrize("invalid", ["missing", "rejected", "unavailable", "foreign-task", "source-window", "current-shot", "source-group"])
def test_stale_selection_rejects_invalid_media_or_mapping_without_partial_writes(
    observed_candidate: tuple[ShortDramaService, str, Take, str], invalid: str,
) -> None:
    service, pid, candidate, clip_id = observed_candidate
    if invalid == "missing":
        Path(candidate.media_path).unlink()
    elif invalid == "rejected":
        candidate.qa_status = QaStatus.REJECTED
        service.repository.save_take(candidate)
    elif invalid == "unavailable":
        candidate.availability = TakeAvailability.MISSING
        service.repository.save_take(candidate)
    elif invalid == "foreign-task":
        task = service.repository.get_task(candidate.task_id)
        assert task is not None
        task.project_id = "different-project"
        service.repository.save_task(task)
    elif invalid == "source-window":
        candidate.shot_windows[0].source_in_seconds = 1
        candidate.shot_windows[0].source_out_seconds = 11.5
        service.repository.save_take(candidate)
    elif invalid == "source-group":
        task = service.repository.get_task(candidate.task_id)
        assert task is not None
        task.clip_group_id = "different-group"
        service.repository.save_task(task)
    else:
        document = service.get_project(pid).document
        board = next(item for item in document.production_documents if item.id == document.storyboard_document_id)
        version = next(item for item in board.versions if item.id == board.current_version_id)
        assert isinstance(version.payload, dict)
        shots = cast(list[dict[str, JsonValue]], version.payload["shots"])
        version.payload["shots"] = [item for item in shots if item["id"] != candidate.shot_id]
        service.repository.save_project(document)
    before = service.get_project(pid)
    with pytest.raises(WorkflowConflictError):
        service.select_take(candidate.id, shot_ids=[candidate.shot_id], clip_ids=[clip_id])
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    after = reopened.get_project(pid)
    assert after.document == before.document and after.takes == before.takes


def test_stale_multiple_uses_require_a_target_and_preserve_unselected_use(
    observed_candidate: tuple[ShortDramaService, str, Take, str],
) -> None:
    service, pid, candidate, clip_id = observed_candidate
    saved = read_editorial(service, pid)
    editor = EditorProject.model_validate(saved.project)
    video = next(item for item in editor.timelines[0].clips if item.id == clip_id)
    other = video.model_copy(deep=True, update={"id": "second-use", "startTime": 14, "linkedClipIds": []})
    editor.timelines[0].clips.append(other)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision, timeline={"project": as_json(editor)}))
    with pytest.raises(WorkflowConflictError, match="多处采用"):
        service.select_take(candidate.id, shot_ids=[candidate.shot_id])
    with pytest.raises(WorkflowConflictError, match="超出了原素材时长"):
        service.select_take(candidate.id, shot_ids=[candidate.shot_id], clip_ids=[clip_id],
            expected_editorial_revision=saved.revision + 1, source_in_seconds=8, source_out_seconds=12)
    service.select_take(candidate.id, shot_ids=[candidate.shot_id], clip_ids=[clip_id],
        expected_editorial_revision=saved.revision + 1, source_in_seconds=7.5, source_out_seconds=11.5)
    actual = EditorProject.model_validate(read_editorial(service, pid).project)
    assert next(item for item in actual.timelines[0].clips if item.id == other.id) == other
