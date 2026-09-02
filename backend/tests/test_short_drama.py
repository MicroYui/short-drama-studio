"""Integration coverage for the short-drama Mock vertical slice."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image

from short_drama.media import TimelineExporter
from short_drama.models import (
    ApprovalStatus,
    EnqueueRequest,
    Freshness,
    OutputSpec,
    TaskStatus,
    TimelineAudioClip,
    TimelineManifest,
    TimelineVideoClip,
)
from short_drama.service import ShortDramaService


def _initialize_fixture(client):
    response = client.post("/api/short-drama/fixtures/dont-trust-me")
    assert response.status_code == 200, response.text
    return response.json()


def _create_agent_project(
    client,
    project: dict[str, Any],
    *,
    render_export: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = client.post(
        "/api/short-drama/agent-projects",
        json={
            "project": project,
            "execution_target": "MOCK",
            "render_export": render_export,
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    fetched = client.get(f"/api/short-drama/projects/{created['project_id']}")
    assert fetched.status_code == 200, fetched.text
    return fetched.json(), created["run"]


def _run_mock_agent_to_completion(
    client,
    project_id: str,
    *,
    render_export: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = client.post(
        f"/api/short-drama/projects/{project_id}/agent-runs",
        json={
            "objective": "生成完整 Mock 制片结果",
            "unattended": True,
            "execution_target": "MOCK",
            "render_export": render_export,
        },
    )
    assert created.status_code == 200, created.text
    return _advance_agent_run_to_completion(client, project_id, created.json())


def _advance_agent_run_to_completion(
    client,
    project_id: str,
    current: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = current["snapshot"]["run"]["id"]
    for _ in range(16):
        if current["snapshot"]["run"]["status"] == "COMPLETED":
            break
        advanced = client.post(
            f"/api/short-drama/agent-runs/{run_id}/advance",
            json={"max_actions": 8},
        )
        assert advanced.status_code == 200, advanced.text
        current = advanced.json()["run"]
    else:
        raise AssertionError("Agent Mock production did not complete")
    fetched = client.get(f"/api/short-drama/projects/{project_id}")
    assert fetched.status_code == 200, fetched.text
    return fetched.json(), current


def _render_export_source(
    path: Path,
    *,
    size: str,
    fps: int,
    audio_sample_rate: int,
    duration_seconds: float,
) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    duration = f"{duration_seconds:.3f}"
    result = subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x273447:s={size}:r={fps}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate={audio_sample_rate}:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            str(audio_sample_rate),
            "-ac",
            "2",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_workflow_routes_are_not_exposed(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    removed_operations = {
        ("/api/short-drama/projects", "post"),
        ("/api/short-drama/planning-runs", "get"),
        ("/api/short-drama/planning-runs", "post"),
        ("/api/short-drama/planning-runs/{run_id}", "get"),
        ("/api/short-drama/planning-runs/{run_id}/advance", "post"),
        ("/api/short-drama/planning-runs/{run_id}/outline", "put"),
        ("/api/short-drama/planning-runs/{run_id}/outline/agent-revise", "post"),
        (
            "/api/short-drama/planning-runs/{run_id}/outline/revise-from-review",
            "post",
        ),
        ("/api/short-drama/planning-runs/{run_id}/script-quality", "get"),
        (
            "/api/short-drama/planning-runs/{run_id}/agent-tools/search-script",
            "post",
        ),
        (
            "/api/short-drama/planning-runs/{run_id}/agent-tools/revise-scenes",
            "post",
        ),
        ("/api/short-drama/script-quality", "get"),
        ("/api/short-drama/projects/autonomous-draft", "post"),
        (
            "/api/short-drama/projects/{source_project_id}/autonomous-repeats",
            "post",
        ),
        (
            "/api/short-drama/projects/{project_id}/autonomous-runs/{run_id}/advance",
            "post",
        ),
        ("/api/short-drama/projects/{project_id}/auto-draft", "post"),
    }
    assert all(method not in paths.get(path, {}) for path, method in removed_operations)
    assert "post" in paths["/api/short-drama/agent-projects"]
    assert "post" in paths["/api/short-drama/tasks/{task_id}/advance"]


def test_timeline_export_normalizes_heterogeneous_take_media(tmp_path: Path) -> None:
    first_path = tmp_path / "mock-like.mp4"
    second_path = tmp_path / "h3-like.mp4"
    _render_export_source(
        first_path,
        size="432x768",
        fps=30,
        audio_sample_rate=48000,
        duration_seconds=1.0,
    )
    _render_export_source(
        second_path,
        size="768x1344",
        fps=24,
        audio_sample_rate=32000,
        duration_seconds=1.0,
    )
    video_clips = [
        TimelineVideoClip(
            id=f"video-{index}",
            shot_id=f"shot-{index}",
            take_id=f"take-{index}",
            media_path=str(path),
            start_seconds=float(index - 1),
            source_in_seconds=0.0,
            duration_seconds=1.0,
            transition_in="none",
            transition_out="none",
        )
        for index, path in enumerate((first_path, second_path), start=1)
    ]
    audio_clips = [
        TimelineAudioClip(
            id=f"audio-{index}",
            shot_id=clip.shot_id,
            take_id=clip.take_id,
            media_path=clip.media_path,
            start_seconds=clip.start_seconds,
            duration_seconds=clip.duration_seconds,
            gain_db=0.0,
            role="MODEL_NATIVE_TEST",
        )
        for index, clip in enumerate(video_clips, start=1)
    ]
    manifest = TimelineManifest(
        id="heterogeneous-manifest-v1",
        project_id="heterogeneous-project",
        version=1,
        script_version_id="script-v1",
        bible_version_id="bible-v1",
        video_clips=video_clips,
        audio_clips=audio_clips,
        subtitle_cues=[],
        output_spec=OutputSpec(),
        duration_seconds=2.0,
        approval_status=ApprovalStatus.DRAFT,
        freshness=Freshness.CURRENT,
        created_at="2026-08-17T00:00:00Z",
    )

    export = TimelineExporter(tmp_path / "workspace").render(
        manifest,
        "2026-08-17T00:00:01Z",
    )

    assert all(check.passed for check in export.checks), export.checks
    assert Path(export.clean_video_path).is_file()
    assert Path(export.subtitled_video_path).is_file()


def test_timeline_export_chunks_long_multi_shot_manifest(tmp_path: Path) -> None:
    source_path = tmp_path / "short-source.mp4"
    _render_export_source(
        source_path,
        size="108x192",
        fps=12,
        audio_sample_rate=24000,
        duration_seconds=0.25,
    )
    clip_duration = 0.2
    video_clips = [
        TimelineVideoClip(
            id=f"video-{index:03d}",
            shot_id=f"shot-{index:03d}",
            take_id=f"take-{index:03d}",
            media_path=str(source_path),
            start_seconds=(index - 1) * clip_duration,
            source_in_seconds=0.0,
            duration_seconds=clip_duration,
            transition_in="none",
            transition_out="none",
        )
        for index in range(1, 26)
    ]
    audio_clips = [
        TimelineAudioClip(
            id=f"audio-{index:03d}",
            shot_id=clip.shot_id,
            take_id=clip.take_id,
            media_path=clip.media_path,
            start_seconds=clip.start_seconds,
            duration_seconds=clip.duration_seconds,
            gain_db=0.0,
            role="MODEL_NATIVE_TEST",
        )
        for index, clip in enumerate(video_clips, start=1)
    ]
    manifest = TimelineManifest(
        id="chunked-manifest-v1",
        project_id="chunked-project",
        version=1,
        script_version_id="script-v1",
        bible_version_id="bible-v1",
        video_clips=video_clips,
        audio_clips=audio_clips,
        subtitle_cues=[],
        output_spec=OutputSpec(
            width=108,
            height=192,
            fps=12,
            crf=28,
            audio_sample_rate=24000,
            audio_bitrate_kbps=64,
        ),
        duration_seconds=5.0,
        approval_status=ApprovalStatus.DRAFT,
        freshness=Freshness.CURRENT,
        created_at="2026-08-21T00:00:00Z",
    )

    export = TimelineExporter(tmp_path / "workspace").render(
        manifest,
        "2026-08-21T00:00:01Z",
    )

    assert all(check.passed for check in export.checks), export.checks
    chunk_dir = (
        tmp_path
        / "workspace"
        / manifest.project_id
        / "exports"
        / manifest.id
        / "normalized-chunks"
    )
    assert (chunk_dir / "chunk-001.mp4").is_file()
    assert (chunk_dir / "chunk-002.mp4").is_file()


def test_both_input_contracts_share_typed_planning_and_sqlite(client, test_state) -> None:
    fixture = _initialize_fixture(client)
    fixture_again = _initialize_fixture(client)
    assert fixture_again["document"]["project"]["id"] == fixture["document"]["project"]["id"]
    assert len(fixture["document"]["shots"]) == 8
    assert len(fixture["document"]["script_versions"][0]["lines"]) == 5
    assert fixture["document"]["project"]["source_mode"] == "SCRIPT_LOCKED"
    assert fixture["document"]["project"]["production_profile"] == (
        "AI_SHORT_DRAMA"
    )
    assert {
        entry["strategy"] for entry in fixture["document"]["execution_plan"]
    } == {
        "direct_audiovisual",
        "image_guided",
        "audio_driven",
        "hybrid_repair",
    }

    idea_text = "雨夜，一张过期车票让两名陌生人发现他们认识彼此。"
    idea, _idea_run = _create_agent_project(
        client,
        {
            "name": "一句话输入",
            "source_mode": "IDEA_EXPANSION",
            "production_profile": "GENERAL_VIDEO",
            "input_text": idea_text,
            "run_mode": "GUIDED",
        },
    )
    assert idea["document"]["project"]["input_text"] == idea_text
    assert idea["document"]["project"]["production_profile"] == "GENERAL_VIDEO"
    assert idea["document"]["script_versions"][0]["original_text"] == idea_text

    locked_text = "林夏：不要动那支录音笔。"
    locked, _locked_run = _create_agent_project(
        client,
        {
            "name": "已有剧本输入",
            "source_mode": "SCRIPT_LOCKED",
            "input_text": locked_text,
            "run_mode": "AUTO_DRAFT",
        },
    )
    assert locked["document"]["project"]["production_profile"] == (
        "AI_SHORT_DRAMA"
    )
    script = locked["document"]["script_versions"][0]
    assert script["original_text"] == locked_text
    assert script["lines"][0]["original_text"] == "不要动那支录音笔。"
    assert script["lines"][0]["spoken_text"] == "不要动那支录音笔。"
    assert script["lines"][0]["subtitle_text"] == "不要动那支录音笔。"

    for created in (idea, locked):
        project_id = created["document"]["project"]["id"]
        agent_runs = client.get(
            f"/api/short-drama/projects/{project_id}/agent-runs"
        )
        assert agent_runs.status_code == 200, agent_runs.text
        snapshots = agent_runs.json()["runs"]
        assert len(snapshots) == 1
        assert snapshots[0]["snapshot"]["run"]["project_id"] == project_id
        assert any(
            action["tool_name"] == "project.create"
            and action["status"] == "SUCCEEDED"
            for action in snapshots[0]["snapshot"]["actions"]
        )

    listed = client.get("/api/short-drama/projects")
    assert listed.status_code == 200
    assert len(listed.json()["projects"]) == 3

    database_path = test_state.config.app_data_dir / "short-drama.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()
    assert journal_mode == "wal"


def test_empty_and_image_inputs_share_the_same_planning_contract(
    client,
    tmp_path: Path,
) -> None:
    empty_body, _empty_run = _create_agent_project(
        client,
        {
            "source_mode": "AUTO_ORIGINAL",
            "input_text": "",
            "run_mode": "GUIDED",
        },
    )
    assert empty_body["document"]["project"]["source_mode"] == "AUTO_ORIGINAL"
    assert empty_body["document"]["project"]["name"]
    assert empty_body["document"]["project"]["target_duration_seconds"] == 300
    structured_scenes = empty_body["document"]["script_versions"][0][
        "structured_scenes"
    ]
    assert len(structured_scenes) == 1
    assert len(empty_body["document"]["shots"]) == len(structured_scenes)
    assert "0岁" not in str(empty_body["document"]["assets"])

    image_path = tmp_path / "visual-seed.png"
    Image.new("RGB", (96, 128), "#2d415b").save(image_path)
    body, _seeded_run = _create_agent_project(
        client,
        {
            "name": "图片输入",
            "source_mode": "IMAGE_SEEDED",
            "input_text": "保留冷蓝夜景与人物剪影关系。",
            "input_images": [
                {
                    "local_path": str(image_path),
                    "descriptor": "冷蓝夜景中的两个人物剪影",
                }
            ],
            "run_mode": "GUIDED",
        },
    )
    reference_asset = next(
        asset
        for asset in body["document"]["assets"]
        if asset["kind"] == "REFERENCE"
    )
    version = reference_asset["versions"][-1]
    assert version["media_type"] == "IMAGE"
    assert version["approval_status"] == "APPROVED"
    assert Path(version["local_path"]).is_file()
    assert all(
        any(
            binding["target_id"] == version["id"]
            and binding["requirement"] == "REQUIRED"
            for binding in plan["bindings"]
        )
        for plan in body["document"]["reference_plan"]
    )


def test_five_minute_agent_project_runs_to_manifest_without_user_stops(client) -> None:
    created, run = _create_agent_project(
        client,
        {
            "source_mode": "AUTO_ORIGINAL",
            "input_text": "",
            "run_mode": "AUTO_DRAFT",
        },
    )
    body, completed = _advance_agent_run_to_completion(
        client,
        created["document"]["project"]["id"],
        run,
    )

    assert completed["snapshot"]["run"]["status"] == "COMPLETED"
    assert body["document"]["project"]["target_duration_seconds"] == 300
    assert body["progress"]["shots_with_selected_take"] == len(
        body["document"]["shots"]
    )
    assert body["progress"]["manifest_ready"] is True
    assert "autonomous_runs" not in body["document"]
    assert body["exports"] == []


def test_general_profile_silent_fallback_runs_to_manifest(client) -> None:
    created, run = _create_agent_project(
        client,
        {
            "source_mode": "AUTO_ORIGINAL",
            "production_profile": "GENERAL_VIDEO",
            "input_text": "",
            "run_mode": "AUTO_DRAFT",
        },
    )
    body, completed = _advance_agent_run_to_completion(
        client,
        created["document"]["project"]["id"],
        run,
    )

    assert completed["snapshot"]["run"]["status"] == "COMPLETED"
    assert body["document"]["project"]["production_profile"] == (
        "GENERAL_VIDEO"
    )
    assert len(body["document"]["shots"]) == len(
        body["document"]["script_versions"][0]["structured_scenes"]
    )
    assert body["document"]["script_versions"][0]["lines"] == []
    assert body["progress"]["manifest_ready"] is True
    assert "autonomous_runs" not in body["document"]


def test_locked_script_preserves_more_than_ten_dialogue_lines(client) -> None:
    lines = [f"角色{i % 2 + 1}：第{i}句锁定对白。" for i in range(1, 13)]
    body, _run = _create_agent_project(
        client,
        {
            "name": "长对白锁定剧本",
            "source_mode": "SCRIPT_LOCKED",
            "input_text": "\n".join(lines),
            "run_mode": "GUIDED",
        },
    )
    script_lines = body["document"]["script_versions"][0]["lines"]
    assert len(script_lines) == 12
    assert [item["original_text"] for item in script_lines] == [
        f"第{i}句锁定对白。" for i in range(1, 13)
    ]


def test_worker_handles_cancel_retry_reconcile_and_missing_output(client, test_state) -> None:
    project = _initialize_fixture(client)
    project_id = project["document"]["project"]["id"]
    shots = project["document"]["shots"][:4]
    behaviors = {
        f"{shots[1]['id']}:1": "FAIL_ONCE",
        f"{shots[2]['id']}:1": "SUBMIT_UNKNOWN",
        f"{shots[3]['id']}:1": "OUTPUT_MISSING",
    }
    enqueue = client.post(
        f"/api/short-drama/projects/{project_id}/enqueue",
        json={
            "shot_ids": [shot["id"] for shot in shots],
            "candidate_count": 1,
            "operation": "GENERATE",
            "behavior_by_candidate": behaviors,
        },
    )
    assert enqueue.status_code == 200, enqueue.text
    tasks = enqueue.json()["tasks"]
    task_by_shot = {task["shot_id"]: task for task in tasks}

    cancelled_task = task_by_shot[shots[0]["id"]]
    cancel = client.post(f"/api/short-drama/tasks/{cancelled_task['id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["task"]["status"] == "CANCELLED"

    drain = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert drain.status_code == 200, drain.text
    after_drain = drain.json()["project"]
    statuses = {task["shot_id"]: task["status"] for task in after_drain["tasks"]}
    assert statuses[shots[0]["id"]] == "CANCELLED"
    assert statuses[shots[1]["id"]] == "FAILED"
    assert statuses[shots[2]["id"]] == "SUBMIT_UNKNOWN"
    assert statuses[shots[3]["id"]] == "OUTPUT_MISSING"

    failed_task = next(task for task in after_drain["tasks"] if task["status"] == "FAILED")
    unknown_task = next(task for task in after_drain["tasks"] if task["status"] == "SUBMIT_UNKNOWN")
    missing_task = next(task for task in after_drain["tasks"] if task["status"] == "OUTPUT_MISSING")

    retry_cancelled = client.post(f"/api/short-drama/tasks/{cancelled_task['id']}/retry")
    retry_failed = client.post(f"/api/short-drama/tasks/{failed_task['id']}/retry")
    assert retry_cancelled.status_code == 200
    assert retry_failed.status_code == 200
    reconcile = client.post(f"/api/short-drama/tasks/{unknown_task['id']}/reconcile")
    assert reconcile.status_code == 200, reconcile.text
    assert reconcile.json()["task"]["status"] == "ACKNOWLEDGED"
    assert test_state.short_drama.repository.provider_submission_count(
        unknown_task["request_fingerprint"]
    ) == 1

    recover = client.post(f"/api/short-drama/tasks/{missing_task['id']}/recover-output")
    assert recover.status_code == 200, recover.text
    assert recover.json()["task"]["status"] == "SUCCEEDED"

    final_drain = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert final_drain.status_code == 200, final_drain.text
    aggregate = final_drain.json()["project"]
    assert aggregate["progress"]["unresolved_tasks"] == 0
    assert aggregate["progress"]["shots_with_selected_take"] == 4
    assert len(aggregate["takes"]) == 4
    assert all(Path(take["media_path"]).exists() for take in aggregate["takes"])


def test_worker_recovers_running_and_interrupted_submit_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "short-drama.sqlite3"
    workspace_root = tmp_path / "workspace"
    first = ShortDramaService(database_path=database_path, workspace_root=workspace_root)
    project = first.initialize_fixture()
    project_id = project.document.project.id
    first_shot = project.document.shots[0]
    first.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[first_shot.id], candidate_count=1),
    )
    running_task = first.get_project(project_id).tasks[0]
    first.advance_task(running_task.id)
    first.advance_task(running_task.id)
    running_task = first.advance_task(running_task.id)
    assert running_task.status == TaskStatus.RUNNING

    second = ShortDramaService(database_path=database_path, workspace_root=workspace_root)
    result = second.drain(project_id, 20)
    assert result.remaining_runnable == 0
    assert result.project.takes[0].is_selected is True

    second_shot = project.document.shots[1]
    second.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[second_shot.id], candidate_count=1),
    )
    submitting_task = next(
        task
        for task in second.get_project(project_id).tasks
        if task.shot_id == second_shot.id and task.status == TaskStatus.QUEUED
    )
    submitting_task = second.advance_task(submitting_task.id)
    assert submitting_task.status == TaskStatus.SUBMITTING

    third = ShortDramaService(database_path=database_path, workspace_root=workspace_root)
    recovered = third.repository.get_task(submitting_task.id)
    assert recovered is not None
    assert recovered.status == TaskStatus.SUBMIT_UNKNOWN
    reconciled = third.reconcile_task(submitting_task.id)
    assert reconciled.status == TaskStatus.FAILED
    retry = third.retry_task(submitting_task.id)
    assert retry.attempt == 2
    third.drain(project_id, 20)
    current = third.get_project(project_id)
    assert {take.shot_id for take in current.takes if take.is_selected} == {
        first_shot.id,
        second_shot.id,
    }
    assert third.repository.provider_submission_count(submitting_task.request_fingerprint) == 0


def test_episode_review_endpoint_persists_non_blocking_unavailable_report(client) -> None:
    initialized = _initialize_fixture(client)
    project_id = initialized["document"]["project"]["id"]
    _run_mock_agent_to_completion(client, project_id, render_export=False)

    reviewed = client.post(
        f"/api/short-drama/projects/{project_id}/review-episode",
        json={"max_actions": 4},
    )

    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["report"]["status"] == "UNAVAILABLE"
    assert body["report"]["advisory_only"] is True
    assert "UNKNOWN" in body["report"]["summary"]
    assert body["project"]["episode_reviews"][-1]["id"] == body["report"]["id"]
    assert body["project"]["progress"]["manifest_ready"] is True


def test_complete_eight_shot_mock_timeline_export_and_local_invalidation(client) -> None:
    initialized = _initialize_fixture(client)
    project_id = initialized["document"]["project"]["id"]

    project, _run = _run_mock_agent_to_completion(
        client,
        project_id,
        render_export=True,
    )
    export = project["exports"][-1]
    assert project["progress"] == {
        "total_shots": 8,
        "shots_with_selected_take": 8,
        "total_tasks": 16,
        "terminal_tasks": 16,
        "failed_tasks": 0,
        "unresolved_tasks": 0,
        "passed_takes": 16,
        "stale_takes": 0,
        "manifest_ready": True,
        "export_ready": True,
    }
    assert len(project["takes"]) == 16
    assert sum(1 for take in project["takes"] if take["is_selected"]) == 8
    manifest_id = project["document"]["project"]["current_manifest_id"]
    manifest = next(item for item in project["document"]["manifests"] if item["id"] == manifest_id)
    assert len(manifest["video_clips"]) == 8
    assert len(manifest["audio_clips"]) == 8
    assert len(manifest["subtitle_cues"]) == 5
    assert manifest["duration_seconds"] == 50.0
    assert all(check["passed"] for check in export["checks"])
    assert Path(export["clean_video_path"]).exists()
    assert Path(export["subtitled_video_path"]).exists()
    assert Path(export["manifest_path"]).read_text(encoding="utf-8").count('"shot_id"') >= 16
    srt = Path(export["srt_path"]).read_text(encoding="utf-8")
    assert "十点零三分" in srt
    assert "别相信我" in srt

    shot_two_id = initialized["document"]["shots"][1]["id"]
    alternate = next(
        take
        for take in project["takes"]
        if take["shot_id"] == shot_two_id and take["candidate_index"] == 2
    )
    selected = client.post(f"/api/short-drama/takes/{alternate['id']}/select")
    assert selected.status_code == 200, selected.text
    assert selected.json()["progress"]["manifest_ready"] is False
    assert len(selected.json()["exports"]) == 1
    manifest_two = client.post(
        f"/api/short-drama/projects/{project_id}/manifest/rebuild"
    )
    assert manifest_two.status_code == 200, manifest_two.text
    assert manifest_two.json()["version"] == 2
    selected_clip = next(
        clip for clip in manifest_two.json()["video_clips"] if clip["shot_id"] == shot_two_id
    )
    assert selected_clip["take_id"] == alternate["id"]

    line_one = initialized["document"]["script_versions"][0]["lines"][0]
    revised_text = "你说他十点离开，可监控里，十点零四分他还在这里。"
    revise = client.post(
        f"/api/short-drama/projects/{project_id}/script/revise-line",
        json={
            "line_id": line_one["id"],
            "spoken_text": revised_text,
            "subtitle_text": revised_text,
            "change_reason": "验证局部失效传播",
        },
    )
    assert revise.status_code == 200, revise.text
    revised = revise.json()
    assert revised["progress"]["stale_takes"] == 2
    assert revised["progress"]["shots_with_selected_take"] == 7
    assert revised["progress"]["manifest_ready"] is False
    assert revised["document"]["script_versions"][-1]["original_text"] == line_one["speaker"] + "：" + line_one["original_text"] + "\n" + "\n".join(
        f"{line['speaker']}：{line['original_text']}"
        for line in initialized["document"]["script_versions"][0]["lines"][1:]
    )
    assert revised["document"]["script_versions"][-1]["spoken_text"].startswith(revised_text)

    regenerate = client.post(
        f"/api/short-drama/projects/{project_id}/enqueue",
        json={"shot_ids": [shot_two_id], "candidate_count": 2},
    )
    assert regenerate.status_code == 200, regenerate.text
    redrain = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert redrain.status_code == 200, redrain.text
    assert redrain.json()["project"]["progress"]["shots_with_selected_take"] == 8
    manifest_three = client.post(
        f"/api/short-drama/projects/{project_id}/manifest/rebuild"
    )
    assert manifest_three.status_code == 200, manifest_three.text
    assert manifest_three.json()["version"] == 3

    shot_three_id = initialized["document"]["shots"][2]["id"]
    shot_four_id = initialized["document"]["shots"][3]["id"]
    shot_five_id = initialized["document"]["shots"][4]["id"]
    retake = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot_three_id}/retake",
        json={},
    )
    extend = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot_four_id}/extend",
        json={"duration_delta_seconds": 2.0},
    )
    gap_fill = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot_five_id}/gap-fill",
        json={"duration_delta_seconds": 1.5},
    )
    assert retake.status_code == 200, retake.text
    assert extend.status_code == 200, extend.text
    assert gap_fill.status_code == 200, gap_fill.text
    rework_drain = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert rework_drain.status_code == 200, rework_drain.text
    reworked = rework_drain.json()["project"]
    assert {"RETAKE", "EXTEND", "GAP_FILL"}.issubset(
        {take["operation"] for take in reworked["takes"]}
    )
    extended_take = next(
        take
        for take in reworked["takes"]
        if take["shot_id"] == shot_four_id and take["operation"] == "EXTEND"
    )
    choose_extended = client.post(f"/api/short-drama/takes/{extended_take['id']}/select")
    assert choose_extended.status_code == 200, choose_extended.text
    manifest_four = client.post(
        f"/api/short-drama/projects/{project_id}/manifest/rebuild"
    )
    assert manifest_four.status_code == 200, manifest_four.text
    assert manifest_four.json()["version"] == 4
    assert manifest_four.json()["duration_seconds"] == 52.0

    final_export = client.post(
        f"/api/short-drama/projects/{project_id}/export",
        json={"rebuild_manifest": False},
    )
    assert final_export.status_code == 200, final_export.text
    assert final_export.json()["manifest_id"] == manifest_four.json()["id"]
    assert all(check["passed"] for check in final_export.json()["checks"])
