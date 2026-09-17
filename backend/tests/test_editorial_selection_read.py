"""Selected editor facts survive the actual Tool -> Leader handoff without re-reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from short_drama.models import GenerationTask, Take
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioMessage, StudioScope
from short_drama.studio_runtime import StudioAgentRunner
from short_drama.tools.contracts import ToolCallStatus
from tests.test_studio_editor_selection import QueuedSelectionRunner
from tests.test_studio_harness import HarnessLanguageClient, _service, _wait_job


def seed_editor(service: ShortDramaService, pid: str) -> tuple[str, str]:
    document = service.get_project(pid).document
    one, two = [shot.id for shot in document.shots[:2]]

    def clip(identity: str, start: float, shot: str | None, kind: str = "video") -> dict[str, JsonValue]:
        return {"id": identity, "assetId": f"asset-{identity}", "type": kind,
            "startTime": start, "duration": 4, "trimStart": 2, "speed": 1.5,
            "reversed": True, "trackIndex": 0, "sourceShotId": shot,
            "sourceTakeId": "current-take", "sourceWindowStart": 2, "sourceWindowEnd": 8,
            "volume": .7, "muted": False}

    def cue(identity: str, start: float, end: float) -> dict[str, JsonValue]:
        return {"id": identity, "text": f"原字幕 {identity}", "startTime": start,
                "endTime": end, "trackIndex": 1, "style": {"fontSize": 32, "color": "#fff"}}

    # Metadata-only fixture: reads must not decode or require source media.
    document.editorial_timeline = {"revision": 38, "project": {
        "version": 2, "id": pid, "name": "Current film", "activeTimelineId": "film-a", "assets": [],
        "timelines": [
            {"id": "film-a", "name": "A", "tracks": [],
             "clips": [clip("first-use", 0, one), clip("next-shot", 4, two),
                       clip("second-use", 12, one), clip("image-note", 12.5, None, "image"),
                       clip("audio-note", 12.5, None, "audio")],
             "subtitles": [cue("cue-cross-cut", 3.5, 4.75), cue("cue-second-use", 13, 14.2),
                           cue("unrelated-cue", 7, 7.9)]},
            {"id": "film-b", "name": "B", "tracks": [], "clips": [],
             "subtitles": [cue("cue-second-use", 40, 41)]},
        ],
    }}
    service.repository.save_project(document)
    return one, two


@pytest.fixture
def selected_editor(tmp_path: Path) -> tuple[ShortDramaService, str, str, str]:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    one, two = seed_editor(service, pid)
    return service, pid, one, two


def read(service: ShortDramaService, pid: str, **arguments: object) -> dict[str, Any]:
    result = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, **arguments})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    return cast(dict[str, Any], result.output["content"])


def test_cross_cut_cue_returns_canonical_film_boundaries_and_both_local_intersections(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, one, two = selected_editor
    before = service.get_project(pid).document
    value = read(service, pid, timeline_id="film-a", subtitle_ids=["cue-cross-cut"])
    assert value["coordinate_space"] == "FILM" and value["editorial_revision"] == 38
    assert [(cue["id"], cue["start_seconds"], cue["end_seconds"]) for cue in value["subtitles"]] == [("cue-cross-cut", 3.5, 4.75)]
    assert value["clips"] == [] and "project" not in value and "exports" not in value
    mappings = value["shot_local_mappings"]
    assert [(item["shot_id"], item["anchor_clip_id"], item["film_start_seconds"]) for item in mappings] == [(one, "first-use", 0), (two, "next-shot", 4)]
    assert mappings[0]["subtitles"] == [{"id": "cue-cross-cut", "start_seconds": 3.5, "end_seconds": 4}]
    assert mappings[1]["subtitles"] == [{"id": "cue-cross-cut", "start_seconds": 0, "end_seconds": .75}]
    assert service.get_project(pid).document == before


def test_exact_second_use_does_not_default_to_first_and_keeps_source_distinct(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, one, _two = selected_editor
    value = read(service, pid, timeline_id="film-a", subtitle_ids=["cue-second-use"], clip_ids=["second-use"],
                 shot_id=one, anchor_clip_id="second-use")
    assert value["subtitles"][0]["start_seconds"] == 13
    assert value["shot_local_mappings"] == [{"shot_id": one, "anchor_clip_id": "second-use",
        "film_start_seconds": 12, "film_end_seconds": 16,
        "subtitles": [{"id": "cue-second-use", "start_seconds": 1, "end_seconds": 2.2}],
        "clips": [{"id": "second-use", "start_seconds": 0, "end_seconds": 4}]}]
    clip = value["clips"][0]
    assert (clip["start_seconds"], clip["end_seconds"], clip["source_in_seconds"]) == (12, 16, 2)
    assert clip["speed"] == 1.5 and clip["reversed"] is True and clip["volume"] == .7


def test_non_active_timeline_and_unowned_image_audio_are_exact(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, _one, _two = selected_editor
    value = read(service, pid, timeline_id="film-b", subtitle_ids=["cue-second-use"])
    assert value["subtitles"][0]["start_seconds"] == 40 and value["shot_local_mappings"] == []
    assert value["edit_arguments"] == {"timeline_id": "film-b", "expected_revision": 38}
    assert value["active_timeline_id"] == "film-a"
    value = read(service, pid, clip_ids=["image-note", "audio-note", "image-note"])
    assert [(item["id"], item["asset_id"], item["type"], item["source_shot_id"]) for item in value["clips"]] == [
        ("image-note", "asset-image-note", "image", None), ("audio-note", "asset-audio-note", "audio", None)]
    assert value["subtitles"] == []


@pytest.mark.parametrize("arguments,fragment", [
    ({"timeline_id": "film-a", "subtitle_ids": ["cue-second-use", "missing-cue"], "clip_ids": ["missing-clip"]}, "/subtitle_ids/1: 'missing-cue'"),
    ({"timeline_id": "gone", "subtitle_ids": ["cue-second-use"]}, "/timeline_id: 'gone'"),
    ({"subtitle_ids": ["cue-second-use"], "include_project": True}, "include_project cannot be combined"),
    ({"subtitle_ids": ["cue-second-use"], "anchor_clip_id": "second-use"}, "anchor_clip_id requires shot_id"),
])
def test_bad_selection_has_specific_repairable_error(
    selected_editor: tuple[ShortDramaService, str, str, str], arguments: dict[str, object], fragment: str,
) -> None:
    service, pid, _one, _two = selected_editor
    result = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, **arguments})
    assert result.status != ToolCallStatus.SUCCEEDED and result.error is not None
    assert fragment in result.error.message
    assert result.error.scope["subtitle_ids"] == arguments["subtitle_ids"]
    assert result.error.retryable is False


def test_explicit_shot_or_anchor_cannot_silently_retarget_selected_ids(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, one, _two = selected_editor
    result = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid,
        "timeline_id": "film-a", "shot_id": one, "anchor_clip_id": "first-use", "subtitle_ids": ["cue-second-use"]})
    assert result.error and "cue-second-use" in result.error.message and "do not intersect" in result.error.message


def test_legacy_local_read_still_marks_coordinate_origin_before_rows(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, one, _two = selected_editor
    value = read(service, pid, shot_id=one, anchor_clip_id="second-use")
    assert value["coordinate_space"] == "SHOT_LOCAL" and value["film_start_seconds"] == 12
    assert value["is_saved"] is True and value["editorial_is_saved"] is True
    assert value["content_origin"] == "SAVED_EDITORIAL" and all(row["is_saved"] for row in value["clips"])
    assert value["subtitles"][0]["startTime"] == 1


def test_equal_clip_and_subtitle_ids_do_not_mask_scope_mismatch(
    selected_editor: tuple[ShortDramaService, str, str, str],
) -> None:
    service, pid, one, _two = selected_editor
    document = service.get_project(pid).document
    assert document.editorial_timeline is not None
    project = cast(dict[str, Any], document.editorial_timeline["project"])
    timeline = project["timelines"][0]
    timeline["subtitles"][1]["id"] = "shared"
    timeline["clips"][0]["id"] = "shared"
    service.repository.save_project(document)
    result = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid,
        "shot_id": one, "anchor_clip_id": "shared", "subtitle_ids": ["shared"], "clip_ids": ["shared"]})
    assert result.error and "subtitle_ids='shared'" in result.error.message and "do not intersect" in result.error.message


@pytest.mark.parametrize("scoped", [False, True])
def test_shared_group_second_shot_maps_selected_objects_without_rewriting_film_rows(
    selected_editor: tuple[ShortDramaService, str, str, str], scoped: bool,
) -> None:
    service, pid, _one, _two = selected_editor
    case = json.loads((Path(__file__).parent / "fixtures/studio-whole-group-uses.json").read_text())["cases"][0]
    document = service.get_project(pid).document
    document.shots = [document.shots[0].model_copy(deep=True, update={"id": identity, "sequence": index + 1})
                     for index, identity in enumerate(case["expected"])]
    for row in case["takes"]:
        take = Take.model_validate({**row, "project_id": pid})
        service.repository.save_task(GenerationTask.model_validate({
            "id": take.task_id, "project_id": pid, "shot_id": take.shot_id, "operation": take.operation,
            "candidate_index": take.candidate_index, "status": "SUCCEEDED", "provider": "mock", "model": "local-fixture",
            "request_fingerprint": "local-whole-group", "input_script_version_id": document.script_versions[-1].id,
            "input_bible_version_id": document.bible_versions[-1].id, "requested_duration_seconds": take.duration_seconds,
            "attempt": 1, "created_at": take.created_at, "updated_at": take.created_at,
        }))
        service.repository.save_take(take)
    timeline = case["timeline"]
    timeline["subtitles"] = [
        {"id": "cross-group-window", "text": "保留完整字幕", "startTime": 6.4, "endTime": 7.4,
         "trackIndex": 0, "style": {"fontSize": 32}},
        {"id": "outside-second-shot", "text": "第一镜字幕", "startTime": 1, "endTime": 2, "trackIndex": 0},
    ]
    document.editorial_timeline = {"revision": 39, "project": {
        "version": 2, "id": pid, "name": "Shared group", "activeTimelineId": timeline["id"],
        "assets": case["assets"], "timelines": [timeline],
    }}
    service.repository.save_project(document)
    before = service.get_project(pid)
    anchor = "clip-shot-yqzyd-01"
    arguments: dict[str, object] = {"timeline_id": timeline["id"], "clip_ids": [anchor], "subtitle_ids": ["cross-group-window"]}
    if scoped:
        arguments.update(shot_id="shot-yqzyd-02", anchor_clip_id=anchor)
    value = read(service, pid, **arguments)
    assert value["clips"][0]["start_seconds"] == 0 and value["clips"][0]["end_seconds"] == 12.25
    assert value["clips"][0]["source_in_seconds"] == 0 and value["clips"][0]["source_shot_id"] == "shot-yqzyd-01"
    assert value["subtitles"] == [{"id": "cross-group-window", "text": "保留完整字幕", "start_seconds": 6.4,
                                    "end_seconds": 7.4, "track_index": 0, "style": {"fontSize": 32}}]
    mappings = {item["shot_id"]: item for item in value["shot_local_mappings"]}
    assert mappings["shot-yqzyd-02"] == {"shot_id": "shot-yqzyd-02", "anchor_clip_id": anchor,
        "film_start_seconds": 6.5, "film_end_seconds": 12.25,
        "subtitles": [{"id": "cross-group-window", "start_seconds": 0, "end_seconds": .9}],
        "clips": [{"id": anchor, "start_seconds": 0, "end_seconds": 5.75}]}
    assert set(mappings) == ({"shot-yqzyd-02"} if scoped else {"shot-yqzyd-01", "shot-yqzyd-02"})
    outside = service.tool_registry.invoke("timeline.read_editorial", {"project_id": pid, "timeline_id": timeline["id"],
        "shot_id": "shot-yqzyd-02", "anchor_clip_id": anchor, "subtitle_ids": ["outside-second-shot"]})
    assert outside.error and "do not intersect" in outside.error.message
    after = service.get_project(pid)
    assert after.document == before.document and after.takes == before.takes


def test_real_runner_first_contract_and_complete_exact_read_need_two_model_calls(tmp_path: Path) -> None:
    selected = ["cue-cross-cut", "cue-second-use"]
    selected_clips = ["second-use", "image-note", "audio-note"]

    def answer(payload: dict[str, Any]) -> dict[str, Any]:
        assert "timeline.read_editorial" in payload["tool_schemas"]
        schema = payload["tool_schemas"]["timeline.read_editorial"]["input_schema"]
        assert "subtitle_ids" in schema["properties"]
        actions = payload["context"]["recent_actions"]
        if not actions:
            return {"summary": "读取当前选择", "goal_complete": False, "steps": [{
                "id": "read-selected", "title": "读取选中字幕和片段", "kind": "TOOL_CALL", "assigned_agent": "leader",
                "tool_name": "timeline.read_editorial", "instructions": "只读取当前选择。",
                "arguments": {"project_id": payload["context"]["project_id"], "timeline_id": "film-a",
                              "subtitle_ids": selected, "clip_ids": selected_clips}}]}
        # This result is deliberately larger than both former 1600/1000-char
        # limits. Assert the actual next model input, not merely Tool output.
        summary = actions[-1]["summary"]
        assert len(summary) > 1600
        result = json.loads(summary)["content"]
        assert result["coordinate_space"] == "FILM" and result["editorial_revision"] == 38
        assert result["is_saved"] is True and result["editorial_is_saved"] is True
        assert result["content_origin"] == "SAVED_EDITORIAL"
        assert all(row["is_saved"] for row in result["clips"])
        assert [item["id"] for item in result["subtitles"]] == selected
        assert [item["id"] for item in result["clips"]] == selected_clips
        assert len(result["shot_local_mappings"]) == 3
        return {"summary": "已说明选中对象的本集时码和局部映射。", "goal_complete": True, "steps": []}

    client = HarnessLanguageClient(answer)
    service, pid = _service(tmp_path, client)
    seed_editor(service, pid)
    before = service.get_project(pid).document
    scope = StudioScope.model_validate({"kind": "film", "editor_selection": {
        "timeline_id": "film-a", "editorial_revision": 38, "coordinate_space": "FILM",
        "focus": "subtitle", "subtitle_ids": selected, "playhead_seconds": 13}})
    job = service.studio_runtime.send(pid, StudioMessage(text="只说明当前选择，不修改作品。", scope=scope))
    _wait_job(service, job.id, "completed")
    assert len(client.inputs) == 2
    assert service.get_project(pid).document == before


@pytest.mark.parametrize("followup_editor", [False, True])
def test_tool_warmup_is_scoped_and_covers_pending_editor_message(tmp_path: Path, followup_editor: bool) -> None:
    client = HarnessLanguageClient(lambda _payload: {"summary": "已说明。", "goal_complete": True, "steps": []})
    service, pid = _service(tmp_path, client)
    service.studio_runtime = QueuedSelectionRunner(service, retry_delays=())
    job = service.studio_runtime.send(pid, StudioMessage(text="只解释剧本，不修改。", scope=StudioScope(kind="episode")))
    if followup_editor:
        service.studio_runtime.send(pid, StudioMessage(text="补充：解释成片字幕。", scope=StudioScope(kind="film")))
    service.studio_runtime = StudioAgentRunner(service, retry_delays=())
    service.studio_runtime.resume_after_restart()
    _wait_job(service, job.id, "completed")
    assert ("timeline.read_editorial" in client.inputs[0]["tool_schemas"]) is followup_editor
