"""Integration coverage for generic reference packs and spatial interaction planning."""

from __future__ import annotations

import json
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
    ProductionProfile,
    ReferenceAngle,
    ReferenceBinding,
    ReferenceFraming,
    ReferenceRequirement,
    ReferenceRole,
    RunMode,
)
from short_drama.reference_planner import (
    DirectorAdviceRequest,
    DirectorRepairDecisionRequest,
    ReferencePlanPreviewRequest,
    ReferencePackEntryCreateRequest,
    ReplacementShotDraft,
    parse_director_advice,
    preview_reference_plan,
)
from short_drama.service import (
    ShortDramaService,
    WorkflowConflictError,
)


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


class FakeDirectorLanguageProvider:
    def __init__(self, output_text: str | list[str]) -> None:
        self.output_texts = (
            list(output_text) if isinstance(output_text, list) else [output_text]
        )
        self.generations: list[tuple[str, str]] = []
        self.max_output_token_requests: list[int] = []

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
        del image_urls
        self.generations.append((instructions, input_text))
        self.max_output_token_requests.append(max_output_tokens)
        if not self.output_texts:
            raise AssertionError("Unexpected Director Agent generation")
        return self.output_texts.pop(0)


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
    assert any("背景中性" in item for item in body["warnings"])
    assert any(
        "入口、窗户、柜台或其他场景结构重复或漂移" in item
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
    assert "entrance.handle.interior 位于 entrance.door_face.interior" in brief_body["prompt"]
    assert "entrance.handle.exterior 位于 entrance.door_face.exterior" in brief_body["prompt"]
    assert "不要生成拼贴图" in brief_body["prompt"]
    assert "环境拓扑参考图" in brief_body["prompt"]
    assert "画面不得出现人物" in brief_body["prompt"]
    assert "有序动作节拍" not in brief_body["prompt"]
    assert "人物与视线" not in brief_body["prompt"]

    start_brief = client.post(
        f"/api/short-drama/projects/{project_id}/shots/{shot.id}/keyframe-brief/compile",
        json={
            "position": "START",
            "start_after_action_beat_order": 2,
        },
    )
    assert start_brief.status_code == 200, start_brief.text
    start_prompt = start_brief.json()["prompt"]
    assert "截至顺序 2 的所有动作节拍都已完成" in start_prompt
    assert "不要重演已完成的节拍" in start_prompt
    assert "此帧之前已完成的动作节拍" in start_prompt
    assert "此帧之后待完成的动作节拍" in start_prompt
    assert "entrance.primary_door=closed" in start_prompt
    assert "更大范围连续性中提到的其他人物必须保持在画外" in start_prompt
    assert "由已完成节拍确定的此帧状态" in start_prompt
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
    assert "图片 1 是权威的物理画面" in end_body["prompt"]
    assert "手与物体的接触侧" in end_body["prompt"]
    assert "刚性组装与接触规则" in end_body["prompt"]
    assert "位于 entrance.door_face.interior 的 entrance.handle.interior" in end_body["prompt"]
    assert "保持 RIGHT_HAND 持续连接于" in end_body["prompt"]
    assert "不得物理消失" in end_body["prompt"]

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
    assert "图片 2：" not in duplicate_body["prompt"]

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


def test_semantic_requirement_uses_approved_core_binding_as_coarse_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 4)
    lin_asset = next(
        item
        for item in project.document.assets
        if item.kind == AssetKind.CHARACTER and item.name == "林夏定妆"
    )
    location_asset = next(
        item for item in project.document.assets if item.kind == AssetKind.LOCATION
    )
    lin_version_id = _approve_image(service, tmp_path, lin_asset.id, "#d6d3d1")
    location_version_id = _approve_image(
        service,
        tmp_path,
        location_asset.id,
        "#1e3a5f",
    )
    document = service.repository.get_project(project_id)
    assert document is not None
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    plan.bindings = [
        ReferenceBinding(
            kind=AssetKind.CHARACTER.value,
            target_id=lin_version_id,
            purpose="Approved Lin Xia identity anchor.",
            required=True,
        ),
        ReferenceBinding(
            kind=AssetKind.LOCATION.value,
            target_id=location_version_id,
            purpose="Approved store look anchor.",
            required=True,
        ),
    ]
    plan.requirements = [
        ReferenceRequirement(
            id="lin-face",
            subject_asset_id=lin_asset.id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            preferred_angles=[ReferenceAngle.FRONT],
            preferred_framings=[ReferenceFraming.FACE_CLOSE_UP],
            purpose="Preserve Lin Xia identity in a tight single.",
            required=True,
        ),
        ReferenceRequirement(
            id="store-look",
            subject_asset_id=location_asset.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            preferred_angles=[ReferenceAngle.NOT_APPLICABLE],
            preferred_framings=[ReferenceFraming.WIDE],
            purpose="Preserve the approved store lighting and palette.",
            required=True,
        ),
    ]
    service.repository.save_project(document)

    preview = preview_reference_plan(
        document,
        shot,
        plan,
        ReferencePlanPreviewRequest(max_images=2),
    )

    assert [item.asset_version_id for item in preview.selected] == [
        lin_version_id,
        location_version_id,
    ]
    assert preview.missing_requirement_ids == ["lin-face", "store-look"]
    assert any(
        warning.startswith("lin-face: using an approved core asset")
        for warning in preview.warnings
    )
    assert any(
        warning.startswith("store-look: using an approved core asset")
        for warning in preview.warnings
    )


def test_keyframe_scope_preserves_one_reference_per_required_subject(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 4)
    character = next(
        item
        for item in project.document.assets
        if item.kind == AssetKind.CHARACTER and item.name == "林夏定妆"
    )
    location = next(
        item for item in project.document.assets if item.kind == AssetKind.LOCATION
    )
    character_version_id = _approve_image(
        service,
        tmp_path,
        character.id,
        "#d6d3d1",
    )
    location_version_id = _approve_image(
        service,
        tmp_path,
        location.id,
        "#1e3a5f",
    )

    master_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Location master derivative",
            descriptor="Same-source location master view",
        ),
    ).asset
    master_version_id = _approve_image(
        service,
        tmp_path,
        master_asset.id,
        "#334155",
    )
    interaction_asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="Location interaction derivative",
            descriptor="Same-source location interaction view",
        ),
    ).asset
    interaction_version_id = _approve_image(
        service,
        tmp_path,
        interaction_asset.id,
        "#475569",
    )
    for request in (
        ReferencePackEntryCreateRequest(
            subject_asset_id=location.id,
            asset_version_id=master_version_id,
            role=ReferenceRole.LOCATION_MASTER,
            framing=ReferenceFraming.WIDE,
            description="Approved master view",
        ),
        ReferencePackEntryCreateRequest(
            subject_asset_id=location.id,
            asset_version_id=interaction_version_id,
            role=ReferenceRole.LOCATION_INTERACTION,
            framing=ReferenceFraming.MEDIUM,
            tags=["meeting_table"],
            description="Approved interaction view",
        ),
    ):
        service.add_reference_pack_entry(project_id, request)

    document = service.repository.get_project(project_id)
    assert document is not None
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    plan.bindings = [
        ReferenceBinding(
            kind=AssetKind.LOCATION.value,
            target_id=location_version_id,
            purpose="Approved location identity anchor.",
            required=True,
        ),
        ReferenceBinding(
            kind=AssetKind.CHARACTER.value,
            target_id=character_version_id,
            purpose="Approved character identity anchor.",
            required=True,
        ),
    ]
    plan.requirements = [
        ReferenceRequirement(
            id="location-master",
            subject_asset_id=location.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="Preserve the location master.",
            required=True,
        ),
        ReferenceRequirement(
            id="location-interaction",
            subject_asset_id=location.id,
            acceptable_roles=[ReferenceRole.LOCATION_INTERACTION],
            preferred_framings=[ReferenceFraming.MEDIUM],
            required_tags=["meeting_table"],
            purpose="Preserve interaction geometry.",
            required=True,
        ),
        ReferenceRequirement(
            id="character-identity",
            subject_asset_id=character.id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            purpose="Preserve the visible character identity.",
            required=True,
        ),
    ]
    plan.max_reference_images = 2
    service.repository.save_project(document)

    document = service.repository.get_project(project_id)
    assert document is not None
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    preview = preview_reference_plan(
        document,
        shot,
        plan,
        ReferencePlanPreviewRequest(),
    )
    assert len(preview.selected) == 2
    assert {item.subject_asset_id for item in preview.selected} == {
        location.id,
        character.id,
    }
    assert character_version_id in {
        item.asset_version_id for item in preview.selected
    }
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
    assert "不要用固定的动作数量或时长阈值代替导演判断" in instructions
    assert "比较修改提示词、替换关键帧、拆分镜头和更换覆盖机位" in instructions
    assert "建议不得阻塞生成" in input_text
    assert '"provider_capability_profile"' in input_text
    assert '"repair_strategy_library"' in input_text


def test_director_agent_repairs_invalid_json_once(tmp_path: Path) -> None:
    fake = FakeDirectorLanguageProvider(
        [
            "not-json",
            """{
              "advisories": [],
              "repair_options": [],
              "recommended_strategy": null,
              "proposed_repair": null
            }""",
        ]
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
    shot = project.document.shots[0]

    advice = client.post(
        f"/api/short-drama/projects/{project.document.project.id}/shots/{shot.id}/director-advice",
        json={},
    )

    assert advice.status_code == 200, advice.text
    assert advice.json()["advisories"] == []
    assert len(fake.generations) == 2
    assert fake.max_output_token_requests == [7200, 7200]
    repair_instructions, repair_input = fake.generations[1]
    assert "修复导演 Agent 先前返回的一份无效 JSON" in repair_instructions
    assert '"invalid_director_json": "not-json"' in repair_input
    assert '"original_director_context"' in repair_input


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


def test_director_reference_plan_normalizes_asset_version_ids(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    character = next(
        item for item in project.document.assets if item.kind == AssetKind.CHARACTER
    )
    location = next(
        item for item in project.document.assets if item.kind == AssetKind.LOCATION
    )
    character_version_id = character.versions[-1].id
    location_version_id = location.versions[-1].id

    result = service.decide_director_repair(
        project_id,
        shot.id,
        DirectorRepairDecisionRequest.model_validate(
            {
                "decision": "APPLIED",
                "strategy": "CHANGE_KEYFRAME",
                "reason": "Use the exact versions selected by the Director as subjects.",
                "reference_plan": {
                    "requirements": [
                        {
                            "id": "director-character-reference",
                            "subject_asset_id": character_version_id,
                            "acceptable_roles": ["CHARACTER_BODY"],
                            "purpose": "Preserve the selected character identity.",
                        }
                    ],
                    "action_beats": [
                        {
                            "order": 1,
                            "actor_asset_id": character_version_id,
                            "action": "enter frame",
                        }
                    ],
                    "interactions": [
                        {
                            "id": "director-location-interaction",
                            "actor_asset_id": character_version_id,
                            "effector": "BODY",
                            "action": "stand in",
                            "target_asset_id": location_version_id,
                            "target_anchor": "interaction zone",
                            "start_state": "outside",
                            "end_state": "inside",
                        }
                    ],
                    "max_reference_images": 3,
                },
            }
        ),
    )

    plan = next(
        item for item in result.document.reference_plan if item.shot_id == shot.id
    )
    assert plan.requirements[0].subject_asset_id == character.id
    assert plan.action_beats[0].actor_asset_id == character.id
    assert plan.interactions[0].actor_asset_id == character.id
    assert plan.interactions[0].target_asset_id == location.id


def test_director_agent_receives_prior_applied_repairs_as_strategy_evidence(
    tmp_path: Path,
) -> None:
    output = """{
      "advisories": [],
      "repair_options": [
        {
          "strategy": "CHANGE_CAMERA",
          "summary": "Use a simpler coverage angle.",
          "rationale": "It reduces the observed orientation risk.",
          "tradeoff": "The face is less prominent.",
          "recommended": true,
          "dismissible": true
        }
      ],
      "recommended_strategy": "CHANGE_CAMERA",
      "proposed_repair": {
        "decision": "APPLIED",
        "strategy": "CHANGE_CAMERA",
        "reason": "Use stable coverage while preserving the narrative beat.",
        "issue_codes": ["HUMAN_CHARACTER_ORIENTATION"],
        "shot_patch": {"camera": "stable rear coverage"},
        "split_shots": []
      }
    }"""
    fake = FakeDirectorLanguageProvider([output, output])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 8)

    first = service.run_director_loop(
        project_id,
        shot.id,
        request=DirectorAdviceRequest(),
    )
    assert first.repair_applied is True
    service.get_director_advice(project_id, shot.id, DirectorAdviceRequest())

    assert len(fake.generations) == 2
    instructions, input_text = fake.generations[1]
    assert "不要重复同一套无效策略" in instructions
    payload = json.loads(input_text)
    prior_repairs = payload["prior_repairs"]
    assert len(prior_repairs) == 1
    assert prior_repairs[0]["id"] == first.project.document.repair_history[-1].id
    assert prior_repairs[0]["source_shot_id"] == shot.id
    assert prior_repairs[0]["strategy"] == "CHANGE_CAMERA"
    assert prior_repairs[0]["decision"] == "APPLIED"
    assert prior_repairs[0]["affected_shot_ids"] == [shot.id]


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


def test_director_parser_discards_known_read_only_reference_plan_fields() -> None:
    parsed = parse_director_advice(
        """{
          "advisories": [],
          "repair_options": [
            {
              "strategy": "CHANGE_KEYFRAME",
              "summary": "Replace the boundary frame.",
              "rationale": "The observed boundary state is not supported.",
              "tradeoff": "One image preparation is added.",
              "recommended": true,
              "dismissible": true
            }
          ],
          "recommended_strategy": "CHANGE_KEYFRAME",
          "proposed_repair": {
            "decision": "APPLIED",
            "strategy": "CHANGE_KEYFRAME",
            "reason": "Use a matching boundary keyframe.",
            "shot_patch": null,
            "reference_plan": {
              "shot_id": "read-only-shot-id",
              "bindings": [],
              "capability_valid": true,
              "validation_notes": ["read-only"],
              "requirements": [],
              "action_beats": [],
              "interactions": [],
              "max_reference_images": 3,
              "planning_notes": "Prepare a matching start frame."
            },
            "split_shots": []
          }
        }"""
    )

    assert parsed.proposed_repair is not None
    assert parsed.proposed_repair.reference_plan is not None
    assert parsed.proposed_repair.reference_plan.planning_notes == (
        "Prepare a matching start frame."
    )


def test_director_parser_normalizes_persisted_fields_from_split_shots() -> None:
    continuity = {
        "character_positions": "The characters remain on the established axis.",
        "eyeline": "They maintain the established eyeline.",
        "prop_state_in": "The badge begins with the employee.",
        "prop_state_out": "The badge ends with the director.",
        "action_in": "The request has just been spoken.",
        "action_out": "The handoff is complete.",
    }
    replacement_base = {
        "project_id": "read-only-project",
        "sequence": 1,
        "approval_status": "DRAFT",
        "freshness": "CURRENT",
        "revision": 1,
        "duration_seconds": 2.5,
        "narrative_purpose": "Separate the dialogue from the handoff.",
        "visual": "Hold on the two characters without completing the handoff.",
        "framing": "stable two-shot",
        "camera": "fixed coverage",
        "audio_intent": "preserve the locked dialogue",
        "dialogue_line_ids": [],
        "continuity": continuity,
        "transition_out": "straight cut",
        "merge_candidate": False,
        "local_rework_risk": "low",
        "inherits_source_dependencies": True,
    }
    payload = {
        "advisories": [],
        "repair_options": [
            {
                "strategy": "SPLIT_SHOT",
                "summary": "Separate the risky action.",
                "rationale": "The observed handoff reverses state.",
                "tradeoff": "Adds one edit.",
                "recommended": True,
                "dismissible": True,
            }
        ],
        "recommended_strategy": "SPLIT_SHOT",
        "proposed_repair": {
            "decision": "APPLIED",
            "strategy": "SPLIT_SHOT",
            "reason": "Put dialogue and object transfer in separate shots.",
            "split_shots": [
                replacement_base,
                {
                    **replacement_base,
                    "sequence": 2,
                    "visual": "Show only the completed badge state.",
                },
            ],
        },
    }

    parsed = parse_director_advice(json.dumps(payload))

    assert parsed.proposed_repair is not None
    assert len(parsed.proposed_repair.split_shots) == 2
    assert parsed.proposed_repair.split_shots[0].inherits_source_dependencies is False
    assert parsed.proposed_repair.split_shots[1].inherits_source_dependencies is True


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
    document.project.production_profile = ProductionProfile.GENERAL_VIDEO
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
    director_instructions, director_input = fake.generations[0]
    assert "当前制片技能：通用视频。" in director_instructions
    assert "导演的通用职责：" in director_instructions
    assert "根据预期体验判断调度与节奏" in director_instructions
    assert "AI 原生短剧" not in director_instructions
    assert json.loads(director_input)["production_profile"] == "GENERAL_VIDEO"
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
    assert "未提供视觉参考图" in brief_body["prompt"]


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
