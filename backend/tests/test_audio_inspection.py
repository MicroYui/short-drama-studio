"""Manual AudioInspection tools, persistence, and Director handoff."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from short_drama.audio_inspection import (
    AudioSemanticResult,
    AudioSemanticService,
    _normalize_dialogue,
    _signal_metrics,
)
from short_drama.fixture import CANONICAL_LINES, CANONICAL_SPEAKER_CHARACTER_IDS
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    AudioContinuityAssessment,
    AudioInspectionRequest,
    EnqueueRequest,
)
from short_drama.reference_planner import DirectorAdviceRequest
from short_drama.service import ShortDramaService


def _run_mock_agent_to_manifest(client, project_id: str) -> None:
    enqueued = client.post(
        f"/api/short-drama/projects/{project_id}/enqueue",
        json={},
    )
    assert enqueued.status_code == 200, enqueued.text
    drained = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert drained.status_code == 200, drained.text
    rebuilt = client.post(
        f"/api/short-drama/projects/{project_id}/manifest/rebuild",
    )
    assert rebuilt.status_code == 200, rebuilt.text


class FakeAudioSemanticService(AudioSemanticService):
    provider = "fake-audio-semantics"
    transcription_model = "fake-transcriber-v1"
    speaker_model = "fake-speaker-v1"

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.overrides = overrides or {}
        self.calls: list[Path] = []

    def analyze(
        self,
        *,
        audio_path: Path,
        language: str,
        include_speaker_embedding: bool,
    ) -> AudioSemanticResult:
        assert language == "zh-CN"
        self.calls.append(audio_path)
        line_number = next(
            index
            for index in range(1, len(CANONICAL_LINES) + 1)
            if f"line-{index:02d}" in audio_path.name
        )
        _speaker, text, _kind = CANONICAL_LINES[line_number - 1]
        speaker_character_id = CANONICAL_SPEAKER_CHARACTER_IDS[line_number - 1]
        embedding = None
        if include_speaker_embedding:
            embedding = {
                "character-linxia": (1.0, 0.0),
                "character-zhouheng": (0.0, 1.0),
                None: (0.707106, 0.707106),
            }[speaker_character_id]
        return AudioSemanticResult(
            transcript_text=self.overrides.get(f"line-{line_number:02d}", text),
            voice_embedding=embedding,
            detected_language="zh-CN",
            confidence=0.99,
        )


class FakeDirectorLanguageProvider:
    def __init__(self) -> None:
        self.inputs: list[str] = []

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
        del image_urls, max_output_tokens
        assert "视频制作流程中的导演 Agent" in instructions
        self.inputs.append(input_text)
        return json.dumps(
            {
                "advisories": [
                    {
                        "category": "AUDIO",
                        "message": "Review the locked dialogue mismatch.",
                        "rationale": "The persisted audio inspection found a transcript difference.",
                        "suggested_action": "Regenerate the shot or replace its dialogue audio.",
                        "dismissible": True,
                    }
                ],
                "repair_options": [],
                "recommended_strategy": None,
                "proposed_repair": None,
            }
        )


def _service(
    tmp_path: Path,
    audio: AudioSemanticService | None,
    director: FakeDirectorLanguageProvider | None = None,
) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        audio_semantic_service=audio,
        language_provider_factory=(
            (lambda _api_key, _base_url: director)
            if director is not None
            else None
        ),
    )


def _produce_mock_manifest(
    service: ShortDramaService,
    project_id: str,
) -> None:
    service.enqueue(project_id, EnqueueRequest())
    service.drain(project_id, 100)
    service.rebuild_manifest(project_id)


def test_audio_inspection_closes_dialogue_and_voice_checks_with_fake_service(
    tmp_path: Path,
) -> None:
    audio = FakeAudioSemanticService()
    service = _service(tmp_path, audio)
    project = service.initialize_fixture()

    _produce_mock_manifest(service, project.document.project.id)
    assert service.get_project(project.document.project.id).audio_inspections == []
    result = service.inspect_current_manifest_audio(
        project.document.project.id,
        AudioInspectionRequest(),
    )

    assert len(result.project.audio_inspections) == 1
    report = result.report
    assert report.semantic_status.value == "COMPLETE"
    assert len(report.clip_inspections) == 8
    assert len(report.dialogue_checks) == 5
    assert all(item.exact_match is True for item in report.dialogue_checks)
    assert all(
        item.assessment == AudioContinuityAssessment.PASSED
        for item in report.speaker_checks
        if item.comparison_count > 0
    )
    linxia = next(item for item in report.speaker_checks if item.speaker == "林夏")
    recorded_linxia = next(
        item for item in report.speaker_checks if item.speaker == "录音笔中的林夏"
    )
    assert linxia.script_line_ids == [
        "project-dont-trust-me-line-01",
        "project-dont-trust-me-line-03",
    ]
    assert linxia.comparison_count == 1
    assert recorded_linxia.script_line_ids == ["project-dont-trust-me-line-04"]
    assert recorded_linxia.comparison_count == 0
    assert len(audio.calls) == 5
    persisted = service.repository.get_audio_inspection(report.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json") == report.model_dump(mode="json")


def test_audio_issue_is_available_to_director_agent_without_forcing_repair(
    tmp_path: Path,
) -> None:
    audio = FakeAudioSemanticService(
        {"line-01": "你说他九点离开，可监控里，十点零三分他还在这里。"}
    )
    director = FakeDirectorLanguageProvider()
    service = _service(tmp_path, audio, director)
    project = service.initialize_fixture()
    _produce_mock_manifest(service, project.document.project.id)
    assert service.get_project(project.document.project.id).audio_inspections == []
    result = service.inspect_current_manifest_audio(
        project.document.project.id,
        AudioInspectionRequest(),
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    report = result.report
    mismatch = next(
        item
        for item in report.dialogue_checks
        if item.script_line_id.endswith("line-01")
    )
    assert mismatch.exact_match is False
    assert any(
        item.code == "DIALOGUE_TRANSCRIPT_MISMATCH"
        and item.severity == "UNKNOWN"
        for item in mismatch.issues
    )
    semantic_evidence = next(
        item for item in mismatch.issues
        if item.code == "DIALOGUE_TRANSCRIPT_MISMATCH"
    )
    assert "expected_text='你说他十点离开" in semantic_evidence.message
    assert "transcript_text='你说他九点离开" in semantic_evidence.message
    assert "speaker_character_id='character-linxia'" in semantic_evidence.message
    shot = result.project.document.shots[1]
    take = next(
        item
        for item in result.project.takes
        if item.shot_id == shot.id and item.is_selected
    )

    advice = service.get_director_advice(
        project.document.project.id,
        shot.id,
        DirectorAdviceRequest(
            take_id=take.id,
            audio_inspection_id=report.id,
        ),
    )

    assert advice.advisories[0].category.value == "AUDIO"
    assert advice.proposed_repair is None
    assert "DIALOGUE_TRANSCRIPT_MISMATCH" in director.inputs[0]
    assert '"severity": "UNKNOWN"' in director.inputs[0]
    assert "character-linxia" in director.inputs[0]


def test_audio_inspection_endpoint_persists_non_semantic_report(client) -> None:
    initialized = client.post("/api/short-drama/fixtures/dont-trust-me").json()
    project_id = initialized["document"]["project"]["id"]
    _run_mock_agent_to_manifest(client, project_id)

    inspected = client.post(
        f"/api/short-drama/projects/{project_id}/inspect-audio",
        json={"semantic_review": False, "speaker_continuity": False},
    )

    assert inspected.status_code == 200, inspected.text
    body = inspected.json()
    assert body["report"]["semantic_status"] == "NOT_REQUESTED"
    assert len(body["report"]["clip_inspections"]) == 8
    assert len(body["project"]["audio_inspections"]) == 1
    assert all(
        check["passed"] for check in body["report"]["technical_checks"]
    )


def test_signal_metrics_detect_clipped_pcm(tmp_path: Path) -> None:
    path = tmp_path / "clipped.wav"
    samples = np.full(16000, 32767, dtype="<i2")
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16000)
        destination.writeframes(samples.tobytes())

    metrics = _signal_metrics(path)

    assert metrics.duration_seconds == 1.0
    assert metrics.clipped_sample_ratio > 0.99
    assert metrics.peak_dbfs > -0.01


def test_dialogue_normalization_treats_traditional_and_simplified_as_equivalent() -> None:
    assert _normalize_dialogue("現在，你還信自己嗎？") == _normalize_dialogue(
        "现在，你还信自己吗?"
    )
