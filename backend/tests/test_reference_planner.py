"""Integration coverage for generic reference packs and spatial interaction planning."""

from __future__ import annotations

from pathlib import Path

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
from short_drama.models import AssetKind
from short_drama.reference_planner import ReferencePackEntryCreateRequest
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


class FakeDirectorLanguageProvider:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.generations: list[tuple[str, str]] = []

    def validate_credentials(self) -> None:
        return None

    def generate_text(self, *, instructions: str, input_text: str) -> str:
        self.generations.append((instructions, input_text))
        return self.output_text


def _client(service: ShortDramaService) -> TestClient:
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return TestClient(app)


def _approve_image(
    service: ShortDramaService,
    tmp_path: Path,
    asset_id: str,
    color: str,
) -> str:
    project_id = "project-dont-trust-me"
    source = tmp_path / f"{asset_id}.png"
    Image.new("RGB", (768, 1344), color).save(source)
    imported = service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(
            local_path=str(source),
            descriptor=f"approved source for {asset_id}",
        ),
    )
    reviewed = service.review_asset_version(
        project_id,
        asset_id,
        imported.version.id,
        AssetReviewRequest(decision="APPROVED", notes="usable reference"),
    )
    return reviewed.version.id


def test_reference_pack_selector_and_generic_interaction_risk(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    client = _client(service)
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 8)
    lin_asset_id = f"{project_id}-asset-linxia"
    store_asset_id = f"{project_id}-asset-store"
    recorder_asset_id = f"{project_id}-asset-recorder"
    lin_version = _approve_image(service, tmp_path, lin_asset_id, "#d6d3d1")
    store_version = _approve_image(service, tmp_path, store_asset_id, "#1e3a5f")
    recorder_version = _approve_image(service, tmp_path, recorder_asset_id, "#991b1b")

    entries = [
        {
            "subject_asset_id": lin_asset_id,
            "asset_version_id": lin_version,
            "role": "CHARACTER_BODY",
            "angle": "THREE_QUARTER_RIGHT",
            "framing": "FULL_BODY",
            "tags": ["canonical_outfit"],
            "description": "identity, body shape, and canonical outfit",
            "priority": 20,
        },
        {
            "subject_asset_id": store_asset_id,
            "asset_version_id": store_version,
            "role": "LOCATION_MASTER",
            "framing": "WIDE",
            "tags": ["rain_night"],
            "description": "master location layout and lighting",
            "priority": 10,
        },
        {
            "subject_asset_id": recorder_asset_id,
            "asset_version_id": recorder_version,
            "role": "PROP_PRODUCT",
            "framing": "DETAIL",
            "tags": ["red_recorder"],
            "description": "prop shape, color, and scale",
            "priority": 10,
        },
    ]
    for body in entries:
        response = client.post(
            f"/api/short-drama/projects/{project_id}/reference-packs/entries",
            json=body,
        )
        assert response.status_code == 200, response.text

    plan_response = client.put(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan",
        json={
            "requirements": [
                {
                    "id": "character-body",
                    "subject_asset_id": lin_asset_id,
                    "acceptable_roles": ["CHARACTER_BODY", "CHARACTER_FACE"],
                    "preferred_angles": ["THREE_QUARTER_RIGHT", "FRONT"],
                    "preferred_framings": ["FULL_BODY", "MEDIUM"],
                    "required_tags": ["canonical_outfit"],
                    "purpose": "identity, outfit, and body continuity",
                },
                {
                    "id": "location-interaction",
                    "subject_asset_id": store_asset_id,
                    "acceptable_roles": ["LOCATION_INTERACTION", "LOCATION_MASTER"],
                    "preferred_framings": ["WIDE"],
                    "purpose": "location geometry and interaction topology",
                },
                {
                    "id": "prop-product",
                    "subject_asset_id": recorder_asset_id,
                    "acceptable_roles": ["PROP_PRODUCT", "PROP_IN_HAND"],
                    "preferred_framings": ["DETAIL"],
                    "required_tags": ["red_recorder"],
                    "purpose": "prop identity and scale",
                },
            ],
            "action_beats": [
                {
                    "order": 1,
                    "actor_asset_id": lin_asset_id,
                    "action": "pick up the prop",
                    "required_end_state": "prop held in left hand",
                },
                {
                    "order": 2,
                    "actor_asset_id": lin_asset_id,
                    "action": "turn toward the exit",
                },
                {
                    "order": 3,
                    "actor_asset_id": lin_asset_id,
                    "action": "operate the designated exit target",
                    "target_anchor": "entrance.primary_door",
                    "required_end_state": "primary door open",
                },
                {
                    "order": 4,
                    "actor_asset_id": lin_asset_id,
                    "action": "cross the threshold",
                    "required_end_state": "actor outside",
                },
            ],
            "interactions": [
                {
                    "id": "exit-door",
                    "actor_asset_id": lin_asset_id,
                    "effector": "RIGHT_HAND",
                    "action": "push open",
                    "target_asset_id": store_asset_id,
                    "target_anchor": "entrance.primary_door",
                    "start_state": "closed",
                    "end_state": "open outward",
                    "unchanged_anchors": ["entrance.fixed_panel"],
                }
            ],
            "max_reference_images": 3,
            "planning_notes": "Generic compound interaction example; no shot-specific code path.",
        },
    )
    assert plan_response.status_code == 200, plan_response.text

    preview = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan/preview",
        json={},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert len(body["selected"]) == 3
    assert body["missing_requirement_ids"] == []
    assert body["complexity"]["action_beat_count"] == 4
    assert body["complexity"]["advisory_only"] is True
    assert body["proceed_allowed"] is True
    assert body["complexity"]["missing_topology_anchors"] == [
        "entrance.primary_door"
    ]

    brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={
            "position": "TOPOLOGY_REFERENCE",
            "requirement_ids": ["location-interaction"],
        },
    )
    assert brief.status_code == 200, brief.text
    brief_body = brief.json()
    assert brief_body["input_version_ids"] == [store_version]
    assert "entrance.primary_door" in brief_body["prompt"]
    assert "entrance.fixed_panel" in brief_body["prompt"]
    assert "Do not create a collage" in brief_body["prompt"]

    topology_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Entrance topology reference",
            descriptor="Reusable interaction topology reference",
        ),
    ).asset
    topology_version = _approve_image(
        service,
        tmp_path,
        topology_asset.id,
        "#334155",
    )
    topology_entry = client.post(
        f"/api/short-drama/projects/{project_id}/reference-packs/entries",
        json={
            "subject_asset_id": store_asset_id,
            "asset_version_id": topology_version,
            "role": "LOCATION_INTERACTION",
            "framing": "WIDE",
            "tags": ["entrance.primary_door", "entrance.fixed_panel"],
            "description": "unambiguous primary door and fixed adjacent panel",
            "priority": 50,
        },
    )
    assert topology_entry.status_code == 200, topology_entry.text

    upgraded = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan/preview",
        json={},
    )
    assert upgraded.status_code == 200, upgraded.text
    upgraded_body = upgraded.json()
    assert upgraded_body["complexity"]["missing_topology_anchors"] == []
    location_selection = next(
        item
        for item in upgraded_body["selected"]
        if item["subject_asset_id"] == store_asset_id
    )
    assert location_selection["role"] == "LOCATION_INTERACTION"
    assert location_selection["asset_version_id"] == topology_version


def test_director_agent_recommends_without_creating_a_generation_gate(
    tmp_path: Path,
) -> None:
    fake = FakeDirectorLanguageProvider(
        """{
          "advisories": [
            {
              "category": "SPLIT_SHOT",
              "message": "Consider separating the pickup from the exit action.",
              "rationale": "The handoff, turn, door interaction, and threshold crossing compete for attention in one shot.",
              "suggested_action": "Try two shots, or keep the original if the continuous take is intentional.",
              "dismissible": true
            }
          ]
        }"""
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    client = _client(service)
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 8)
    lin_asset_id = f"{project_id}-asset-linxia"
    store_asset_id = f"{project_id}-asset-store"

    plan_response = client.put(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan",
        json={
            "action_beats": [
                {
                    "order": 1,
                    "actor_asset_id": lin_asset_id,
                    "action": "pick up the recorder",
                },
                {
                    "order": 2,
                    "actor_asset_id": lin_asset_id,
                    "action": "leave through the entrance",
                    "target_anchor": "entrance.primary_door",
                },
            ],
            "interactions": [
                {
                    "id": "door",
                    "actor_asset_id": lin_asset_id,
                    "effector": "RIGHT_HAND",
                    "action": "push open",
                    "target_asset_id": store_asset_id,
                    "target_anchor": "entrance.primary_door",
                    "start_state": "closed",
                    "end_state": "open",
                }
            ],
        },
    )
    assert plan_response.status_code == 200, plan_response.text

    advice = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/director-advice",
        json={"user_direction": "Prefer a continuous performance if it works."},
    )
    assert advice.status_code == 200, advice.text
    body = advice.json()
    assert body["proceed_allowed"] is True
    assert body["advisories"][0]["category"] == "SPLIT_SHOT"
    assert body["advisories"][0]["dismissible"] is True
    assert len(fake.generations) == 1
    instructions, input_text = fake.generations[0]
    assert "do not apply a fixed action-count or duration threshold" in instructions
    assert "Advice never blocks generation" in input_text


def test_missing_recommended_reference_is_advisory_and_does_not_block_compile(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    client = _client(service)
    project_id = project.document.project.id
    shot = project.document.shots[0]
    lin_asset_id = f"{project_id}-asset-linxia"

    plan_response = client.put(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan",
        json={
            "requirements": [
                {
                    "id": "preferred-left-profile",
                    "subject_asset_id": lin_asset_id,
                    "acceptable_roles": ["CHARACTER_PROFILE"],
                    "preferred_angles": ["LEFT_PROFILE"],
                    "required_tags": ["canonical_outfit"],
                    "purpose": "Improve identity continuity for a left-profile shot",
                }
            ],
            "max_reference_images": 3,
        },
    )
    assert plan_response.status_code == 200, plan_response.text

    preview = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/reference-plan/preview",
        json={},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["missing_requirement_ids"] == ["preferred-left-profile"]
    assert preview_body["proceed_allowed"] is True
    assert preview_body["complexity"]["advisory_only"] is True

    brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={"position": "START"},
    )
    assert brief.status_code == 200, brief.text
    brief_body = brief.json()
    assert brief_body["missing_requirement_ids"] == ["preferred-left-profile"]
    assert brief_body["proceed_allowed"] is True
    assert brief_body["input_version_ids"] == []
    assert "No visual references supplied" in brief_body["prompt"]


def test_reference_pack_rejects_wrong_subject_role_and_unapproved_version(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    lin_asset_id = f"{project_id}-asset-linxia"
    store_asset_id = f"{project_id}-asset-store"
    store_version = _approve_image(service, tmp_path, store_asset_id, "#1e3a5f")

    with pytest.raises(WorkflowConflictError, match="incompatible"):
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=lin_asset_id,
                asset_version_id=store_version,
                role="LOCATION_MASTER",
                description="wrong semantic role",
            ),
        )

    draft_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Draft reference",
            descriptor="not approved",
        ),
    ).asset
    source = tmp_path / "draft.png"
    Image.new("RGB", (768, 1344), "white").save(source)
    draft = service.import_local_asset_version(
        project_id,
        draft_asset.id,
        AssetImportLocalRequest(local_path=str(source), descriptor="draft"),
    ).version
    with pytest.raises(WorkflowConflictError, match="current approved formal version"):
        service.add_reference_pack_entry(
            project_id,
            ReferencePackEntryCreateRequest(
                subject_asset_id=store_asset_id,
                asset_version_id=draft.id,
                role="LOCATION_INTERACTION",
                description="draft must not enter pack",
            ),
        )
