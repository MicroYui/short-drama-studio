"""Integration coverage for Phase 27 visual-input Agent Tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReviewRequest,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    AssetKind,
    ApprovalStatus,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRequirement,
    ReferenceBinding,
    ReferenceRole,
    ShotReferenceCue,
    VideoGenerationRoute,
)
from short_drama.reference_planner import (
    KeyframePosition,
    ReferencePackEntryCreateRequest,
    ShotReferencePlanUpdateRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.visual_input_quality import (
    InputCompatibilityDecision,
    InputCompatibilityRequest,
    KeyframeComparisonDecision,
    KeyframeComparisonRequest,
    ReferenceVisualRerankDecision,
    ReferenceVisualRerankRequest,
)


class FakeVisualInputLanguageProvider:
    def __init__(self) -> None:
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
        payload = json.loads(input_text)
        images = list(image_urls or [])
        self.calls.append((instructions, payload, images))
        if "视觉参考图重排 Tool" in instructions:
            candidates = payload["candidates"]
            selected = next(
                item
                for item in candidates
                if "camera-matched" in item["description"]
            )
            return json.dumps(
                {
                    "decision": ReferenceVisualRerankDecision.SELECT_REFERENCES.value,
                    "selections": [
                        {
                            "candidate_id": selected["candidate_id"],
                            "responsibilities": ["character identity and matching camera angle"],
                            "confidence": 0.88,
                        }
                    ],
                    "candidate_reviews": [
                        {
                            "candidate_id": item["candidate_id"],
                            "relative_score": (
                                4.6 if item["candidate_id"] == selected["candidate_id"] else 2.7
                            ),
                            "strengths": ["Project-local approved visual."],
                            "weaknesses": (
                                []
                                if item["candidate_id"] == selected["candidate_id"]
                                else ["Camera angle is less useful for this shot."]
                            ),
                        }
                        for item in candidates
                    ],
                    "issues": [],
                    "redundant_candidate_ids": [
                        item["candidate_id"]
                        for item in candidates
                        if item["candidate_id"] != selected["candidate_id"]
                    ],
                    "summary": "Use the camera-matched portrait as the smallest sufficient set.",
                }
            )
        if "关键帧相对比较 Tool" in instructions:
            candidates = payload["candidates"]
            return json.dumps(
                {
                    "decision": KeyframeComparisonDecision.NONE_ACCEPTABLE.value,
                    "selected_version_id": None,
                    "candidate_reviews": [
                        {
                            "asset_version_id": item["asset_version"]["id"],
                            "relative_score": 2.1,
                            "strengths": ["Readable single frame."],
                            "weaknesses": ["Action boundary is ambiguous."],
                            "video_generation_risks": [
                                "The motion model would need to invent the starting contact state."
                            ],
                        }
                        for item in candidates
                    ],
                    "issues": [
                        {
                            "category": "ACTION_BOUNDARY",
                            "severity": "HIGH",
                            "message": "Neither candidate provides an unambiguous start state.",
                            "evidence": "The required contact point is not visibly established.",
                            "affected_ids": [
                                item["asset_version"]["id"] for item in candidates
                            ],
                        }
                    ],
                    "summary": "Generate or upload a clearer start keyframe instead of choosing the least bad image.",
                }
            )
        if "输入兼容性检查 Tool" in instructions:
            return json.dumps(
                {
                    "decision": InputCompatibilityDecision.CHANGE_KEYFRAME.value,
                    "observations": [
                        {
                            "input_id": item["input_id"],
                            "visible_state": f"Observed state for {item['role']}.",
                            "evidence": "Visible image evidence was inspected before comparison.",
                        }
                        for item in payload["image_map"]
                    ],
                    "issues": [
                        {
                            "category": "SCENE_TOPOLOGY",
                            "severity": "HIGH",
                            "message": "The start and end frames do not share one stable layout.",
                            "evidence": "A fixed background anchor changes sides between the two frames.",
                            "affected_ids": [
                                item["input_id"]
                                for item in payload["image_map"]
                                if item["role"] in {"START_FRAME", "END_FRAME"}
                            ],
                        }
                    ],
                    "recommendations": [
                        "Replace the end keyframe with a same-layout state before H3 generation."
                    ],
                    "confidence": 0.91,
                    "summary": "The requested motion is reasonable, but the supplied endpoints conflict.",
                }
            )
        raise AssertionError("Unexpected visual-input Agent Tool call")


def _service(
    tmp_path: Path,
    language: FakeVisualInputLanguageProvider,
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


def _create_approved_image(
    service: ShortDramaService,
    tmp_path: Path,
    *,
    project_id: str,
    name: str,
    color: tuple[int, int, int],
) -> tuple[str, str]:
    created = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name=name,
            descriptor=f"{name} production candidate",
        ),
    )
    source = tmp_path / f"{created.asset.id}.png"
    Image.new("RGB", (192, 320), color).save(source)
    imported = service.import_local_asset_version(
        project_id,
        created.asset.id,
        AssetImportLocalRequest(
            local_path=str(source),
            descriptor=f"{name} visual candidate",
        ),
    )
    approved = service.review_asset_version(
        project_id,
        created.asset.id,
        imported.version.id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose="CANONICAL_MASTER",
            notes="test visual candidate",
        ),
    )
    return created.asset.id, approved.version.id


def _add_character_reference(
    service: ShortDramaService,
    *,
    project_id: str,
    subject_asset_id: str,
    version_id: str,
    description: str,
    angle: ReferenceAngle,
    priority: int,
) -> str:
    result = service.add_reference_pack_entry(
        project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject_asset_id,
            asset_version_id=version_id,
            role=ReferenceRole.CHARACTER_FACE,
            angle=angle,
            framing=ReferenceFraming.CLOSE_UP,
            description=description,
            priority=priority,
        ),
    )
    return result.entry.id


def test_reference_visual_reranker_selects_smallest_set_and_persists(
    tmp_path: Path,
) -> None:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    subject_asset_id = next(
        asset.id
        for asset in project.document.assets
        if asset.kind == AssetKind.CHARACTER and "林夏" in asset.name
    )
    _asset_a, version_a = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="generic portrait",
        color=(120, 70, 60),
    )
    _asset_b, version_b = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="camera matched portrait",
        color=(70, 110, 90),
    )
    entry_a = _add_character_reference(
        service,
        project_id=project_id,
        subject_asset_id=subject_asset_id,
        version_id=version_a,
        description="generic portrait",
        angle=ReferenceAngle.FRONT,
        priority=10,
    )
    entry_b = _add_character_reference(
        service,
        project_id=project_id,
        subject_asset_id=subject_asset_id,
        version_id=version_b,
        description="camera-matched portrait",
        angle=ReferenceAngle.THREE_QUARTER_RIGHT,
        priority=5,
    )
    service.update_shot_reference_plan(
        project_id,
        shot.id,
        ShotReferencePlanUpdateRequest(
            requirements=[
                ReferenceRequirement(
                    id="linxia-closeup",
                    subject_asset_id=subject_asset_id,
                    acceptable_roles=[ReferenceRole.CHARACTER_FACE],
                    preferred_angles=[ReferenceAngle.THREE_QUARTER_RIGHT],
                    preferred_framings=[ReferenceFraming.CLOSE_UP],
                    purpose="Preserve Lin Xia in this camera-facing close shot.",
                )
            ],
            max_reference_images=1,
        ),
    )

    response = service.rerank_shot_reference_visuals(
        project_id,
        shot.id,
        ReferenceVisualRerankRequest(
            candidate_entry_ids=[entry_a, entry_b],
            max_selected=1,
        ),
    )

    assert response.report.decision == ReferenceVisualRerankDecision.SELECT_REFERENCES
    assert response.report.selections[0].candidate_id == entry_b
    assert response.report.max_selected == 1
    assert response.report.advisory_only is True
    assert response.report.proceed_allowed is True
    assert len(language.calls) == 1
    assert len(language.calls[0][2]) == 2
    assert "先分别描述每张图片中实际可见的内容" in language.calls[0][0]

    restarted = _service(tmp_path, language)
    reports = restarted.list_visual_input_reports(project_id)
    assert [item.id for item in reports.reference_reranks] == [response.report.id]


def test_keyframe_comparison_allows_none_acceptable_without_mutating_assets(
    tmp_path: Path,
) -> None:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    asset_a, version_a = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="start candidate A",
        color=(100, 100, 120),
    )
    asset_b, version_b = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="start candidate B",
        color=(120, 100, 100),
    )

    response = service.compare_shot_keyframes(
        project_id,
        shot.id,
        KeyframeComparisonRequest(
            candidate_version_ids=[version_a, version_b],
            position=KeyframePosition.START,
            include_previous_boundary=False,
        ),
    )

    assert response.report.decision == KeyframeComparisonDecision.NONE_ACCEPTABLE
    assert response.report.selected_version_id is None
    assert response.report.proceed_allowed is True
    refreshed = service.get_project(project_id)
    assert next(asset for asset in refreshed.document.assets if asset.id == asset_a).approved_version_id == version_a
    assert next(asset for asset in refreshed.document.assets if asset.id == asset_b).approved_version_id == version_b
    assert len(service.repository.list_keyframe_comparisons(project_id)) == 1


def test_input_compatibility_endpoint_is_advisory_and_rejects_cross_project_ids(
    tmp_path: Path,
) -> None:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    _asset_a, version_a = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="compatibility start",
        color=(80, 100, 120),
    )
    _asset_b, version_b = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="compatibility end",
        color=(120, 100, 80),
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        result = client.post(
            f"/api/short-drama/projects/{project_id}/shots/{shot.id}/agent-tools/check-input-compatibility",
            json={
                "route": VideoGenerationRoute.FRAME_FIRST_LAST.value,
                "start_version_id": version_a,
                "end_version_id": version_b,
                "include_previous_boundary": False,
            },
        )
        listed = client.get(
            f"/api/short-drama/projects/{project_id}/agent-tools/visual-input-reports"
        )

    assert result.status_code == 200, result.text
    assert result.json()["report"]["decision"] == InputCompatibilityDecision.CHANGE_KEYFRAME.value
    assert result.json()["report"]["advisory_only"] is True
    assert result.json()["report"]["proceed_allowed"] is True
    assert result.json()["report"]["covered_shot_ids"] == [shot.id]
    assert "No current generation group" in result.json()["report"]["technical_warnings"][0]
    assert listed.status_code == 200
    assert len(listed.json()["compatibility_checks"]) == 1
    with pytest.raises(WorkflowConflictError, match="cross-project AssetVersion"):
        service.check_shot_input_compatibility(
            project_id,
            shot.id,
            InputCompatibilityRequest(
                route=VideoGenerationRoute.FRAME_FIRST,
                start_version_id="another-project-version",
                include_previous_boundary=False,
            ),
        )


def test_input_compatibility_reports_provider_contract_as_warning_not_creative_gate(
    tmp_path: Path,
) -> None:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    _asset, version = _create_approved_image(
        service,
        tmp_path,
        project_id=project_id,
        name="reference used with frame route",
        color=(90, 90, 90),
    )

    response = service.check_shot_input_compatibility(
        project_id,
        shot.id,
        InputCompatibilityRequest(
            route=VideoGenerationRoute.FRAME_FIRST,
            start_version_id=version,
            reference_version_ids=[version],
            include_previous_boundary=False,
        ),
    )

    assert response.report.proceed_allowed is True
    assert response.report.advisory_only is True
    assert "同一次 H3 请求中不能混用帧模式和参考图模式。" in response.report.technical_warnings


def test_input_compatibility_resolves_complete_current_group_and_marks_partial_sets(tmp_path: Path) -> None:
    # Reuse the public-service group setup used by H3's compilation tests.
    from tests.test_h3_generation_unit_language import _fixture

    service, _compiler, document, generation, unit, _request = _fixture(tmp_path)
    language = FakeVisualInputLanguageProvider()
    service.language_provider_factory = lambda _key, _url: language
    project_id = document.project.id
    chosen: list[tuple[str, str]] = []
    for index in range(2):
        chosen.append(_create_approved_image(service, tmp_path, project_id=project_id,
            name=f"group input {index}", color=(80, 100 + index * 30, 120)))
    document = service.get_project(project_id).document
    for shot, (asset_id, version_id) in zip(document.shots[:2], chosen, strict=True):
        asset = next(item for item in document.assets if item.id == asset_id)
        next(item for item in asset.versions if item.id == version_id).approval_status = ApprovalStatus.DRAFT
        shot.references = [ShotReferenceCue(subject_asset_id=asset_id, asset_version_id=version_id,
                                           alias=f"ref{shot.sequence}", purpose=f"Visible state in shot {shot.sequence}")]
        plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
        plan.bindings = [ReferenceBinding(kind=asset.kind, target_id=version_id, purpose=f"Group window {shot.sequence}")]
    service.repository.save_project(document)
    second = document.shots[1]
    result = service.check_shot_input_compatibility(project_id, second.id, InputCompatibilityRequest(
        route=VideoGenerationRoute.REFERENCE, include_previous_boundary=False))
    assert result.report.generation_unit_id == unit.id
    assert result.report.generation_plan_version_id == generation.version.id
    assert result.report.covered_shot_ids == unit.shot_ids
    assert result.report.shot_windows == unit.shot_windows
    assert result.report.reference_version_ids == [item[1] for item in chosen]
    assert result.report.full_input_set_observed
    payload = language.calls[-1][1]
    assert payload["generation_unit"]["id"] == unit.id
    assert [item["id"] for item in payload["current_shots"]] == unit.shot_ids
    assert len(language.calls[-1][2]) == 2
    assert payload["image_map"][1]["uses"][0]["shot_id"] == second.id
    partial = service.check_shot_input_compatibility(project_id, second.id, InputCompatibilityRequest(
        route=VideoGenerationRoute.REFERENCE, reference_version_ids=[chosen[1][1]], include_previous_boundary=False))
    assert not partial.report.full_input_set_observed
    assert language.calls[-1][1]["unobserved_selected_reference_ids"] == [chosen[0][1]]
    assert partial.report.proceed_allowed

    document.shots[0].revision += 1
    service.repository.save_project(document)
    with pytest.raises(WorkflowConflictError, match="outdated shot revisions"):
        service.check_shot_input_compatibility(project_id, second.id, InputCompatibilityRequest(
            route=VideoGenerationRoute.REFERENCE, include_previous_boundary=False))
