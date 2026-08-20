"""Optional character voice-reference input and H3 routing coverage."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import (
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
)
from short_drama.models import VideoGenerationRoute
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _render_audio(path: Path, duration: float = 3.0) -> bytes:
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return path.read_bytes()


def _app(service: ShortDramaService) -> FastAPI:
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return app


def test_voice_reference_upload_normalizes_approves_binds_and_clears(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    character_id = "character-linxia"
    audio = _render_audio(tmp_path / "linxia.wav")

    with TestClient(_app(service)) as client:
        uploaded = client.post(
            f"/api/short-drama/projects/{project_id}/characters/{character_id}/voice-reference/upload",
            data={"descriptor": "林夏自然说话声线，仅参考音色与说话方式"},
            files={"file": ("linxia.wav", audio, "audio/wav")},
        )
        assert uploaded.status_code == 200, uploaded.text
        payload = uploaded.json()
        assert payload["binding"]["character_id"] == character_id
        assert payload["asset"]["kind"] == "REFERENCE"
        assert payload["asset"]["approved_version_id"] == payload["version"]["id"]
        assert payload["version"]["media_type"] == "AUDIO"
        assert payload["version"]["mime_type"] == "audio/wav"
        assert 2.9 <= payload["version"]["duration_seconds"] <= 3.1
        assert Path(payload["version"]["local_path"]).is_file()
        assert payload["project"]["document"]["project"]["voice_reference_revision"] == 1

        cleared = client.put(
            f"/api/short-drama/projects/{project_id}/characters/{character_id}/voice-reference",
            json={"asset_id": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["binding"] is None
        assert cleared.json()["project"]["document"]["project"]["voice_reference_revision"] == 2


def test_voice_reference_upload_rejects_clip_outside_h3_duration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    initial_asset_count = len(project.document.assets)
    audio = _render_audio(tmp_path / "too-short.wav", duration=1.0)

    with pytest.raises(WorkflowConflictError, match="between 2 and 15 seconds"):
        service.upload_character_voice_reference(
            project_id,
            "character-linxia",
            filename="too-short.wav",
            descriptor="过短声线参考",
            data=audio,
        )
    persisted = service.get_project(project_id)
    assert len(persisted.document.assets) == initial_asset_count
    assert persisted.document.voice_references == []


def test_route_planner_uses_character_voice_for_speaking_shot_and_native_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    audio = _render_audio(tmp_path / "linxia.wav")
    service.upload_character_voice_reference(
        project_id,
        "character-linxia",
        filename="linxia.wav",
        descriptor="林夏自然说话声线，仅参考音色与说话方式",
        data=audio,
    )
    document = service.repository.get_project(project_id)
    assert document is not None
    speaking_shot = next(item for item in document.shots if item.sequence == 2)
    silent_shot = next(item for item in document.shots if item.sequence == 1)
    speaking_shot.local_rework_risk = "低；稳定对白近景"
    silent_shot.local_rework_risk = "低；稳定空镜"
    service.repository.save_project(document)

    voice_plan = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[speaking_shot.id],
            use_language_agent=False,
        ),
    ).plan
    voice_decision = voice_plan.decisions[0]
    assert voice_decision.route == VideoGenerationRoute.REFERENCE
    assert [item.role.value for item in voice_decision.media_inputs] == [
        "reference_audio"
    ]
    assert "<Audio 1>" in voice_decision.prompt
    assert "不要复制参考音频中的原台词" in voice_decision.prompt

    native_plan = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[silent_shot.id],
            use_language_agent=False,
        ),
    ).plan
    native_decision = native_plan.decisions[0]
    assert native_decision.route == VideoGenerationRoute.DIRECT
    assert native_decision.media_inputs == []


def test_changing_voice_reference_stales_an_existing_route_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot_id = next(item.id for item in project.document.shots if item.sequence == 2)
    plan = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot_id],
            use_language_agent=False,
        ),
    ).plan
    audio = _render_audio(tmp_path / "linxia.wav")
    service.upload_character_voice_reference(
        project_id,
        "character-linxia",
        filename="linxia.wav",
        descriptor="更新后的林夏声线参考",
        data=audio,
    )

    with pytest.raises(WorkflowConflictError, match="outdated character voice references"):
        service.execute_h3_production_route_plan(
            project_id,
            plan.id,
            H3ProductionRouteExecuteRequest(
                enqueue_new_generations=False,
                inspect_reused_takes=False,
                semantic_review=False,
                rebuild_manifest=False,
            ),
        )


def test_route_planner_keeps_combined_character_voice_audio_within_h3_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    for character_id, filename in (
        ("character-linxia", "linxia.wav"),
        ("character-zhouheng", "zhouheng.wav"),
    ):
        service.upload_character_voice_reference(
            project_id,
            character_id,
            filename=filename,
            descriptor=f"{character_id}十秒声线参考",
            data=_render_audio(tmp_path / filename, duration=10.0),
        )
    document = service.repository.get_project(project_id)
    assert document is not None
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shot = next(item for item in document.shots if item.sequence == 2)
    shot.dialogue_line_ids = [script.lines[0].id, script.lines[1].id]
    shot.local_rework_risk = "低；双人稳定对白"
    service.repository.save_project(document)

    decision = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
        ),
    ).plan.decisions[0]

    assert [item.role.value for item in decision.media_inputs] == [
        "reference_audio"
    ]
    assert any("15-second combined audio limit" in item for item in decision.warnings)
