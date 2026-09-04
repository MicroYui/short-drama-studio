"""Relative same-shot Take comparison as a reusable Director-Agent tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    EnqueueRequest,
    QaStatus,
    SemanticReviewStatus,
    TakeComparisonDecision,
    TakeComparisonRequest,
    TechnicalCheck,
    VideoFrameSample,
    VideoInspectionReport,
    VideoInspectionScope,
)
from short_drama.service import ShortDramaService


NOW = "2026-08-27T12:00:00+00:00"


class FakeTakeComparisonLanguageProvider:
    def __init__(
        self,
        *,
        decision: TakeComparisonDecision,
        winner_index: int | None = None,
    ) -> None:
        self.decision = decision
        self.winner_index = winner_index
        self.calls: list[tuple[str, dict[str, Any], list[str]]] = []

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
        del max_output_tokens
        assert "同镜头 Take 对比工具" in instructions
        payload = json.loads(input_text)
        candidates = payload["candidates"]
        candidate_ids = [item["take"]["id"] for item in candidates]
        selected_take_id = (
            candidate_ids[self.winner_index]
            if self.decision == TakeComparisonDecision.SELECT_TAKE
            and self.winner_index is not None
            else None
        )
        self.calls.append((instructions, payload, list(image_urls or [])))
        return json.dumps(
            {
                "decision": self.decision.value,
                "selected_take_id": selected_take_id,
                "candidate_reviews": [
                    {
                        "take_id": take_id,
                        "relative_score": 4.7 if take_id == selected_take_id else 2.4,
                        "strengths": ["More coherent relative to the shared ShotPlan."],
                        "weaknesses": (
                            []
                            if take_id == selected_take_id
                            else ["Weaker continuity than the other candidate."]
                        ),
                    }
                    for take_id in candidate_ids
                ],
                "summary": (
                    "One candidate is the strongest current edit choice."
                    if selected_take_id is not None
                    else "Every candidate has a visible contradiction; generate a new option."
                ),
            }
        )


def _service(
    tmp_path: Path,
    language: FakeTakeComparisonLanguageProvider,
) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: language,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    return service


def _prepare_candidates(
    service: ShortDramaService,
    tmp_path: Path,
    *,
    candidate_count: int = 2,
) -> tuple[str, str, list[str]]:
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    service.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[shot.id], candidate_count=candidate_count),
    )
    generated = service.drain(project_id, 100).project
    takes = sorted(
        (take for take in generated.takes if take.shot_id == shot.id),
        key=lambda item: item.candidate_index,
    )
    assert len(takes) == candidate_count
    assert all(take.qa_status.value == "PASSED" for take in takes)

    for index, take in enumerate(takes, start=1):
        frame = tmp_path / f"take-{index}-frame.png"
        contact_sheet = tmp_path / f"take-{index}-contact.png"
        Image.new("RGB", (96, 160), (30 * index, 50, 80)).save(frame)
        Image.new("RGB", (192, 160), (30 * index, 50, 80)).save(contact_sheet)
        service.repository.save_video_inspection(
            VideoInspectionReport(
                id=f"inspection-{take.id}",
                project_id=project_id,
                shot_id=shot.id,
                take_id=take.id,
                scopes=[VideoInspectionScope.INTRA_SHOT],
                duration_seconds=take.duration_seconds,
                width=take.width,
                height=take.height,
                fps=24.0,
                audio_present=True,
                samples=[
                    VideoFrameSample(
                        source_take_id=take.id,
                        timestamp_seconds=0.0,
                        image_path=str(frame),
                        reasons=["START"],
                    ),
                    VideoFrameSample(
                        source_take_id=take.id,
                        timestamp_seconds=max(0.1, take.duration_seconds - 0.1),
                        image_path=str(frame),
                        reasons=["END"],
                    ),
                ],
                contact_sheet_path=str(contact_sheet),
                technical_checks=[
                    TechnicalCheck(
                        name="decode",
                        passed=True,
                        detail="Synthetic comparison fixture is readable.",
                    )
                ],
                semantic_status=SemanticReviewStatus.COMPLETE,
                semantic_model="gpt-5.6-sol",
                summary="Candidate was inspected against the same shot contract.",
                created_at=NOW,
            )
        )
    return project_id, shot.id, [take.id for take in takes]


def test_compare_takes_selects_relative_winner_and_persists_report(
    tmp_path: Path,
) -> None:
    language = FakeTakeComparisonLanguageProvider(
        decision=TakeComparisonDecision.SELECT_TAKE,
        winner_index=1,
    )
    service = _service(tmp_path, language)
    project_id, shot_id, take_ids = _prepare_candidates(service, tmp_path)
    warned_winner = service.repository.get_take(take_ids[1])
    assert warned_winner is not None
    warned_winner.qa_status = QaStatus.REJECTED
    warned_winner.qa_report.status = QaStatus.REJECTED
    warned_winner.qa_report.hard_gate_passed = False
    service.repository.save_take(warned_winner)
    response = service.compare_takes(
        project_id,
        TakeComparisonRequest(
            shot_id=shot_id,
            take_ids=take_ids,
            inspect_missing=False,
            apply_selection=True,
        ),
    )

    assert response.report.decision == TakeComparisonDecision.SELECT_TAKE
    assert response.report.selected_take_id == take_ids[1]
    assert next(take for take in response.project.takes if take.id == take_ids[1]).is_selected
    assert not next(take for take in response.project.takes if take.id == take_ids[0]).is_selected
    assert len(language.calls) == 1
    assert len(language.calls[0][2]) == 2
    assert language.calls[0][1]["technically_available_take_ids"] == take_ids
    assert language.calls[0][1]["candidates"][1]["take"]["qa_status"] == "REJECTED"

    restarted = _service(tmp_path, language)
    persisted = restarted.repository.list_take_comparisons(project_id)
    assert [item.id for item in persisted] == [response.report.id]
    assert restarted.get_project(project_id).take_comparisons[0].selected_take_id == take_ids[1]


def test_compare_takes_none_acceptable_clears_automatic_selection(
    tmp_path: Path,
) -> None:
    language = FakeTakeComparisonLanguageProvider(
        decision=TakeComparisonDecision.NONE_ACCEPTABLE,
    )
    service = _service(tmp_path, language)
    project_id, shot_id, take_ids = _prepare_candidates(service, tmp_path)
    service.select_take(take_ids[0])

    response = service.compare_takes(
        project_id,
        TakeComparisonRequest(
            shot_id=shot_id,
            take_ids=take_ids,
            inspect_missing=False,
            apply_selection=True,
        ),
    )

    assert response.report.decision == TakeComparisonDecision.NONE_ACCEPTABLE
    assert response.report.selected_take_id is None
    assert not any(
        take.is_selected for take in response.project.takes if take.shot_id == shot_id
    )


def test_compare_takes_agent_tool_endpoint_is_thin_and_user_controllable(
    tmp_path: Path,
) -> None:
    language = FakeTakeComparisonLanguageProvider(
        decision=TakeComparisonDecision.SELECT_TAKE,
        winner_index=0,
    )
    service = _service(tmp_path, language)
    project_id, shot_id, take_ids = _prepare_candidates(service, tmp_path)
    selected_before = {
        take.id
        for take in service.get_project(project_id).takes
        if take.shot_id == shot_id and take.is_selected
    }
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        result = client.post(
            f"/api/short-drama/projects/{project_id}/agent-tools/compare-takes",
            json={
                "shot_id": shot_id,
                "take_ids": take_ids,
                "inspect_missing": False,
                "apply_selection": False,
            },
        )

    assert result.status_code == 200, result.text
    assert result.json()["report"]["decision"] == "SELECT_TAKE"
    assert {
        take["id"]
        for take in result.json()["project"]["takes"]
        if take["shot_id"] == shot_id and take["is_selected"]
    } == selected_before


def test_mock_enqueue_accepts_agent_requested_candidate_batch_without_local_cap(
    tmp_path: Path,
) -> None:
    language = FakeTakeComparisonLanguageProvider(
        decision=TakeComparisonDecision.NONE_ACCEPTABLE,
    )
    service = _service(tmp_path, language)
    project = service.initialize_fixture()
    shot = project.document.shots[0]

    queued = service.enqueue(
        project.document.project.id,
        EnqueueRequest(shot_ids=[shot.id], candidate_count=6),
    )

    assert len([task for task in queued.tasks if task.shot_id == shot.id]) == 6


def test_compare_takes_accepts_agent_explicit_candidate_set_larger_than_five(
    tmp_path: Path,
) -> None:
    language = FakeTakeComparisonLanguageProvider(
        decision=TakeComparisonDecision.SELECT_TAKE,
        winner_index=5,
    )
    service = _service(tmp_path, language)
    project_id, shot_id, take_ids = _prepare_candidates(
        service,
        tmp_path,
        candidate_count=6,
    )

    response = service.compare_takes(
        project_id,
        TakeComparisonRequest(
            shot_id=shot_id,
            take_ids=take_ids,
            inspect_missing=False,
            apply_selection=False,
        ),
    )

    assert response.report.candidate_take_ids == take_ids
    assert response.report.selected_take_id == take_ids[5]
    assert [item.take_id for item in response.report.candidate_reviews] == take_ids
    assert len(language.calls) == 1
    assert len(language.calls[0][2]) == 6
