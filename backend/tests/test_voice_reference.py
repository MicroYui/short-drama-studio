"""Optional character voice-reference input and H3 routing coverage."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import (
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest
from short_drama.models import ApprovalStatus, AssetKind, AssetMediaType, Freshness, VideoGenerationRoute
from short_drama.visual_input_quality import ReferenceVisualRerankRequest, build_reference_visual_candidates
from short_drama.voice_models import VoiceReferenceUpdateRequest
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


class _FixedRouteAgent:
    def __init__(self) -> None:
        self.output = ""

    def validate_credentials(self) -> None:
        return None

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del instructions, input_text, image_urls, max_output_tokens
        return self.output


def _service_with_route_agent(
    tmp_path: Path,
) -> tuple[ShortDramaService, _FixedRouteAgent]:
    agent = _FixedRouteAgent()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: agent,
    )
    service.language_config.update(
        LanguageProviderConfigUpdate(
            api_key="test-key",
            base_url="https://example.invalid/v1",
        )
    )
    return service, agent


def _route_choice(shot_id: str, candidate_id: str) -> str:
    return (
        '{"choices":[{'
        f'"shot_id":"{shot_id}","candidate_id":"{candidate_id}",'
        '"rationale":"根据当前镜头的结构化资产和声音绑定做出路线决策。",'
        '"qa_focus_categories":[],"warnings":[]}]}'
    )


def _voice_choice_with_visual(service: ShortDramaService, project_id: str, shot_id: str) -> str:
    """A real PNG accompanies reference audio, as required by H3's input contract."""
    document = service.get_project(project_id).document
    asset = next(a for a in document.assets if a.bible_entity_id == "character-linxia")
    version = next(v for v in asset.versions if v.id == asset.current_version_id)
    path = service.workspace_root / "voice-reference-visual.png"
    Image.new("RGB", (96, 144), "gray").save(path)
    version.media_type, version.approval_status, version.freshness = AssetMediaType.IMAGE, ApprovalStatus.APPROVED, Freshness.CURRENT
    version.local_path, version.external_url = str(path), None
    asset.approved_version_id = version.id
    service.repository.save_project(document)
    shot = next(s for s in document.shots if s.id == shot_id)
    plan = next(p for p in document.reference_plan if p.shot_id == shot_id)
    choices, _limit, _warnings = build_reference_visual_candidates(document=document, shot=shot,
        plan=plan, request=ReferenceVisualRerankRequest(), bound_candidates=False)
    visual = next(c for c in choices if c.asset_version_id == version.id)
    return json.dumps({"choices": [{"shot_id": shot_id, "candidate_id": f"reference:{shot_id}",
        "reference_selection_candidate_ids": [visual.candidate_id],
        "rationale": "Use the selected face and character's actual voice reference.",
        "qa_focus_categories": [], "warnings": []}]})


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
    service, agent = _service_with_route_agent(tmp_path)
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
    agent.output = _voice_choice_with_visual(service, project_id, speaking_shot.id)

    voice_plan = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[speaking_shot.id],
            use_language_agent=True,
        ),
    ).plan
    voice_decision = voice_plan.decisions[0]
    assert voice_decision.route == VideoGenerationRoute.REFERENCE
    assert [item.role.value for item in voice_decision.media_inputs if item.kind.value == "AUDIO"] == [
        "reference_audio"
    ]
    assert any(item.role.value == "reference_image" for item in voice_decision.media_inputs)
    assert "<Audio 1>" in voice_decision.prompt
    assert "all spoken words remain the locked dialogue" in voice_decision.prompt

    agent.output = _route_choice(silent_shot.id, f"direct:{silent_shot.id}")
    native_plan = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[silent_shot.id],
            use_language_agent=True,
        ),
    ).plan
    native_decision = native_plan.decisions[0]
    assert native_decision.route == VideoGenerationRoute.DIRECT
    assert native_decision.media_inputs == []


def test_route_planner_binds_voice_only_from_structured_speaker_identity(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    service.upload_character_voice_reference(
        project_id,
        "character-linxia",
        filename="linxia.wav",
        descriptor="林夏自然说话声线",
        data=_render_audio(tmp_path / "linxia.wav"),
    )
    document = service.repository.get_project(project_id)
    assert document is not None
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shot = next(item for item in document.shots if item.sequence == 2)
    line = next(item for item in script.lines if item.id in shot.dialogue_line_ids)
    line.speaker = "完全不同的展示名"
    line.speaker_character_id = "character-linxia"
    character_asset = next(
        item for item in document.assets if item.name == "林夏定妆"
    )
    character_asset.name = "故意误导的资产名"
    character_asset.bible_entity_id = "character-linxia"
    service.repository.save_project(document)
    agent.output = _voice_choice_with_visual(service, project_id, shot.id)

    structured = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    ).plan.decisions[0]

    voice_input = next(
        item for item in structured.media_inputs if item.role.value == "reference_audio"
    )
    assert voice_input.subject_asset_id == character_asset.id
    assert voice_input.binding_purpose == "只锚定林夏的声线与声音质感"

    document = service.repository.get_project(project_id)
    assert document is not None
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    line = next(item for item in script.lines if item.id in shot.dialogue_line_ids)
    line.speaker = "林夏"
    line.speaker_character_id = None
    service.repository.save_project(document)
    agent.output = _route_choice(shot.id, f"direct:{shot.id}")

    unlinked = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    ).plan.decisions[0]
    assert all(item.role.value != "reference_audio" for item in unlinked.media_inputs)


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
                expected_state_revision=plan.state_revision,
                enqueue_new_generations=False,
                inspect_reused_takes=False,
                semantic_review=False,
                rebuild_manifest=False,
            ),
        )


def test_route_planner_keeps_combined_character_voice_audio_within_h3_limit(
    tmp_path: Path,
) -> None:
    service, agent = _service_with_route_agent(tmp_path)
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
    agent.output = _voice_choice_with_visual(service, project_id, shot.id)

    decision = service.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=True,
        ),
    ).plan.decisions[0]

    assert [item.role.value for item in decision.media_inputs if item.kind.value == "AUDIO"] == [
        "reference_audio"
    ]
    assert any(item.role.value == "reference_image" for item in decision.media_inputs)
    assert any("15-second combined audio limit" in item for item in decision.warnings)


def test_existing_draft_voice_can_be_bound_without_a_review(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    source = tmp_path / 'voice.wav'
    _render_audio(source)
    asset = service.create_asset(project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name='Voice', descriptor='Selected voice',
    )).asset
    version = service.import_local_asset_version(project_id, asset.id, AssetImportLocalRequest(
        local_path=str(source), descriptor='Use this available voice clip',
    )).version
    result = service.update_character_voice_reference(
        project_id, 'character-linxia', VoiceReferenceUpdateRequest(asset_id=asset.id),
    )
    assert result.binding is not None
    assert result.binding.asset_id == asset.id
    selected = next(a for a in result.project.document.assets if a.id == asset.id)
    assert next(v for v in selected.versions if v.id == version.id).approval_status == ApprovalStatus.DRAFT
