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
from short_drama.models import (
    AssetKind,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    EnqueueRequest,
    Freshness,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    RunMode,
)
from short_drama.reference_planner import (
    DirectorRepairDecisionRequest,
    ReferencePackEntryCreateRequest,
    ReplacementShotDraft,
    parse_director_advice,
)
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

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
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
            "tags": ["canonical_outfit", "background.scene_embedded"],
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
                    "contact_anchor": "entrance.handle.interior",
                    "contact_surface": "entrance.door_face.interior",
                    "contact_persists": True,
                    "rigid_attachments": [
                        {
                            "anchor": "entrance.handle.interior",
                            "surface": "entrance.door_face.interior",
                        },
                        {
                            "anchor": "entrance.handle.exterior",
                            "surface": "entrance.door_face.exterior",
                        },
                    ],
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
    assert set(body["complexity"]["missing_topology_anchors"]) == {
        "entrance.primary_door",
        "entrance.handle.interior",
        "entrance.handle.exterior",
        "entrance.door_face.interior",
        "entrance.door_face.exterior",
    }
    assert any("background-neutral" in item for item in body["warnings"])
    assert any(
        "duplicate or drifting entrances" in item
        for item in body["complexity"]["observations"]
    )

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
    assert "entrance.handle.interior on entrance.door_face.interior" in brief_body["prompt"]
    assert "entrance.handle.exterior on entrance.door_face.exterior" in brief_body["prompt"]
    assert "Do not create a collage" in brief_body["prompt"]
    assert "environment topology plate" in brief_body["prompt"]
    assert "Show no people" in brief_body["prompt"]
    assert "ORDERED ACTION BEATS" not in brief_body["prompt"]
    assert "Characters and eyeline" not in brief_body["prompt"]

    start_brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={
            "position": "START",
            "start_after_action_beat_order": 2,
        },
    )
    assert start_brief.status_code == 200, start_brief.text
    start_prompt = start_brief.json()["prompt"]
    assert "through order 2 have completed" in start_prompt
    assert "Do not replay completed beats" in start_prompt
    assert "ACTION BEATS COMPLETED BEFORE THIS FRAME" in start_prompt
    assert "ACTION BEATS REMAINING AFTER THIS FRAME" in start_prompt
    assert "entrance.primary_door=closed" in start_prompt
    assert "Other characters mentioned by broader continuity must remain off-screen" in start_prompt
    assert "State at this frame from completed beats" in start_prompt
    assert "Prop state in:" not in start_prompt

    canvas_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Previous approved frame",
            descriptor="Primary physical canvas for a derived keyframe",
        ),
    ).asset
    canvas_version = _approve_image(service, tmp_path, canvas_asset.id, "#111827")
    end_brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={
            "position": "END",
            "primary_canvas_version_id": canvas_version,
        },
    )
    assert end_brief.status_code == 200, end_brief.text
    end_body = end_brief.json()
    assert end_body["primary_input_version_id"] == canvas_version
    assert end_body["input_version_ids"][0] == canvas_version
    assert len(end_body["input_version_ids"]) == len(set(end_body["input_version_ids"]))
    assert "Image 1 is the authoritative physical frame" in end_body["prompt"]
    assert "hand-to-object contact side" in end_body["prompt"]
    assert "RIGID ASSEMBLY AND CONTACT RULES" in end_body["prompt"]
    assert "move entrance.handle.interior on entrance.door_face.interior" in end_body["prompt"]
    assert "keep RIGHT_HAND continuously attached" in end_body["prompt"]
    assert "must not disappear physically" in end_body["prompt"]

    selected_canvas = body["selected"][0]
    duplicate_brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={
            "position": "END",
            "requirement_ids": [selected_canvas["requirement_ids"][0]],
            "primary_canvas_version_id": selected_canvas["asset_version_id"],
        },
    )
    assert duplicate_brief.status_code == 200, duplicate_brief.text
    duplicate_body = duplicate_brief.json()
    assert duplicate_body["input_version_ids"] == [selected_canvas["asset_version_id"]]
    assert "Image 2:" not in duplicate_body["prompt"]

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
            "tags": [
                "entrance.primary_door",
                "entrance.fixed_panel",
                "entrance.handle.interior",
                "entrance.handle.exterior",
                "entrance.door_face.interior",
                "entrance.door_face.exterior",
            ],
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
    assert "Compare prompt retry, keyframe replacement, shot split" in instructions
    assert "Advice never blocks generation" in input_text
    assert '"provider_capability_profile"' in input_text
    assert '"repair_strategy_library"' in input_text


def test_auto_draft_director_loop_applies_executable_repair(tmp_path: Path) -> None:
    fake = FakeDirectorLanguageProvider(
        """{
          "advisories": [],
          "repair_options": [
            {
              "strategy": "CHANGE_CAMERA",
              "summary": "Use a rear coverage angle.",
              "rationale": "It reduces hidden orientation and contact requirements.",
              "tradeoff": "The face is not visible during this beat.",
              "recommended": true,
              "dismissible": true
            }
          ],
          "recommended_strategy": "CHANGE_CAMERA",
          "proposed_repair": {
            "decision": "APPLIED",
            "strategy": "CHANGE_CAMERA",
            "reason": "Use a simpler coverage angle while preserving the story beat.",
            "issue_codes": ["HUMAN_CHARACTER_ORIENTATION"],
            "shot_patch": {
              "camera": "fixed rear coverage angle"
            },
            "split_shots": []
          }
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

    result = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/director-loop",
        json={"user_direction": "Prefer an uninterrupted automatic draft."},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["repair_applied"] is True
    assert body["next_action"] == "REGENERATE_SHOT"
    updated_shot = next(
        item for item in body["project"]["document"]["shots"] if item["id"] == shot.id
    )
    assert updated_shot["camera"] == "fixed rear coverage angle"
    assert updated_shot["revision"] == 2
    assert body["project"]["document"]["repair_history"][-1]["decision"] == "APPLIED"


def test_auto_draft_repairs_rejected_takes_and_regenerates_without_user_pause(
    tmp_path: Path,
) -> None:
    fake = FakeDirectorLanguageProvider(
        """{
          "advisories": [],
          "repair_options": [
            {
              "strategy": "CHANGE_CAMERA",
              "summary": "Use simpler coverage.",
              "rationale": "It addresses the observed orientation failure.",
              "tradeoff": "The new angle reveals less facial detail.",
              "recommended": true,
              "dismissible": true
            }
          ],
          "recommended_strategy": "CHANGE_CAMERA",
          "proposed_repair": {
            "decision": "APPLIED",
            "strategy": "CHANGE_CAMERA",
            "reason": "Use stable rear coverage while preserving the narrative beat.",
            "issue_codes": ["AUTO_ORIENTATION_FAILURE"],
            "shot_patch": {"camera": "stable rear coverage"},
            "split_shots": []
          }
        }"""
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    project_id = project.document.project.id
    initial = service.auto_draft(project_id, render_export=False).project
    shot = initial.document.shots[-1]
    rejected_ids: list[str] = []
    for take in [item for item in initial.takes if item.shot_id == shot.id]:
        rejected_ids.append(take.id)
        take.qa_status = QaStatus.REJECTED
        take.qa_report.status = QaStatus.REJECTED
        take.qa_report.hard_gate_passed = False
        take.qa_report.score = 2.0
        take.qa_report.issues = [
            QaIssue(
                code="AUTO_ORIENTATION_FAILURE",
                severity="HIGH",
                message="The character orientation visibly conflicts with the shot intent.",
                repair_layer="DIRECTOR",
                category=QaFailureCategory.CHARACTER_ORIENTATION,
            )
        ]
        take.is_selected = False
        service.repository.save_take(take)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    repaired = service.auto_draft(project_id, render_export=False).project

    repaired_shot = next(item for item in repaired.document.shots if item.id == shot.id)
    assert repaired_shot.camera == "stable rear coverage"
    assert repaired_shot.revision == 2
    assert repaired.document.repair_history[-1].decision == DirectorRepairDecision.APPLIED
    old_takes = [item for item in repaired.takes if item.id in rejected_ids]
    assert all(item.freshness == Freshness.STALE for item in old_takes)
    current = [
        item
        for item in repaired.takes
        if item.shot_id == shot.id and item.freshness == Freshness.CURRENT
    ]
    assert len(current) == 2
    assert sum(item.is_selected for item in current) == 1
    assert repaired.progress.manifest_ready is True
    assert len(fake.generations) == 1


def test_director_recommendation_and_executable_repair_must_match() -> None:
    with pytest.raises(ValueError, match="invalid advisory JSON"):
        parse_director_advice(
            """{
              "repair_options": [
                {
                  "strategy": "CHANGE_CAMERA",
                  "summary": "Change coverage.",
                  "rationale": "Reduce orientation risk.",
                  "tradeoff": "Less facial detail.",
                  "recommended": true,
                  "dismissible": true
                }
              ],
              "recommended_strategy": "CHANGE_CAMERA",
              "proposed_repair": {
                "decision": "APPLIED",
                "strategy": "PROMPT_RETRY",
                "reason": "This must not be applied because it contradicts the recommendation.",
                "shot_patch": {"visual": "A materially revised visual intent."},
                "split_shots": []
              }
            }"""
        )


def test_guided_director_loop_waits_and_user_can_ignore_without_mutation(
    tmp_path: Path,
) -> None:
    fake = FakeDirectorLanguageProvider(
        """{
          "advisories": [],
          "repair_options": [
            {
              "strategy": "CHANGE_CAMERA",
              "summary": "Use a rear coverage angle.",
              "rationale": "It reduces hidden orientation requirements.",
              "tradeoff": "The face is not visible during this beat.",
              "recommended": true,
              "dismissible": true
            }
          ],
          "recommended_strategy": "CHANGE_CAMERA",
          "proposed_repair": {
            "decision": "APPLIED",
            "strategy": "CHANGE_CAMERA",
            "reason": "Use a simpler coverage angle while preserving the story beat.",
            "shot_patch": {"camera": "fixed rear coverage angle"},
            "split_shots": []
          }
        }"""
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    document = service.repository.get_project(project.document.project.id)
    assert document is not None
    document.project.run_mode = RunMode.GUIDED
    service.repository.save_project(document)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    client = _client(service)
    project_id = project.document.project.id
    shot = next(item for item in document.shots if item.sequence == 8)

    result = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/director-loop",
        json={},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["repair_applied"] is False
    assert body["next_action"] == "AWAIT_USER"
    unchanged = next(
        item for item in body["project"]["document"]["shots"] if item["id"] == shot.id
    )
    assert unchanged["revision"] == 1
    assert unchanged["camera"] == shot.camera

    ignored = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/director-repair",
        json={
            "decision": "IGNORED",
            "strategy": "CHANGE_CAMERA",
            "reason": "Keep the original creative choice.",
        },
    )
    assert ignored.status_code == 200, ignored.text
    ignored_body = ignored.json()
    ignored_shot = next(
        item
        for item in ignored_body["document"]["shots"]
        if item["id"] == shot.id
    )
    assert ignored_shot["revision"] == 1
    assert ignored_body["document"]["repair_history"][-1]["decision"] == "IGNORED"


def test_split_repair_updates_dependencies_and_excludes_old_take(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 8)
    service.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[shot.id], candidate_count=1),
    )
    service.drain(project_id, 20)
    before = service.get_project(project_id)
    old_take = next(item for item in before.takes if item.shot_id == shot.id)
    assert old_take.is_selected is True

    first = ReplacementShotDraft(
        id=shot.id,
        duration_seconds=2.5,
        narrative_purpose="Complete the first part of the action cleanly.",
        visual="The actor completes the object interaction before leaving the frame.",
        framing="medium",
        camera="stable side coverage",
        audio_intent="continuous ambience",
        continuity=shot.continuity.model_copy(deep=True),
        transition_out="action cut",
        local_rework_risk="medium",
    )
    second = ReplacementShotDraft(
        id=f"{project_id}-shot-09-regression",
        duration_seconds=2.5,
        narrative_purpose="Complete the spatial transition in a separate coverage shot.",
        visual="The actor continues moving away with head, torso, and path aligned.",
        framing="medium wide",
        camera="rear coverage",
        audio_intent="continuous ambience",
        continuity=shot.continuity.model_copy(deep=True),
        transition_out=shot.transition_out,
        local_rework_risk="medium",
        inherits_source_dependencies=True,
    )
    updated = service.decide_director_repair(
        project_id,
        shot.id,
        DirectorRepairDecisionRequest(
            decision=DirectorRepairDecision.APPLIED,
            strategy=DirectorRepairStrategy.SPLIT_SHOT,
            reason="Separate competing interaction and movement beats.",
            source_take_id=old_take.id,
            issue_codes=[
                "HUMAN_CONTACT_CONTINUITY",
                "HUMAN_CHARACTER_ORIENTATION",
            ],
            split_shots=[first, second],
        ),
    )

    assert len(updated.document.shots) == 9
    assert len(updated.document.execution_plan) == 9
    assert len(updated.document.reference_plan) == 9
    persisted_old_take = next(item for item in updated.takes if item.id == old_take.id)
    assert persisted_old_take.freshness == Freshness.STALE
    assert persisted_old_take.is_selected is False
    recorder = next(
        prop
        for bible in updated.document.bible_versions
        if bible.id == updated.document.project.current_bible_version_id
        for prop in bible.props
        if prop.name == "红色旧录音笔"
    )
    assert any(state.shot_id == second.id for state in recorder.states)
    assert updated.document.repair_history[-1].affected_shot_ids == [first.id, second.id]


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
