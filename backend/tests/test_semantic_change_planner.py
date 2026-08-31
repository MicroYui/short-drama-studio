"""Dependency-aware user and Agent change planning regressions."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.change_planner import (
    ChangePlanTool,
    ChangeTargetKind,
    SemanticChangeIntent,
    SemanticChangePlanRequest,
    SemanticChangeTarget,
)
from short_drama.deps import get_short_drama_service
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService


class SemanticChangeLanguageClient:
    def validate_credentials(self) -> None:
        pass

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        assert "Semantic Change Planner Agent" in instructions
        payload = json.loads(input_text)
        character = next(
            item
            for item in payload["project_index"]
            if item["kind"] == ChangeTargetKind.CHARACTER.value
            and item["name"] == "林夏"
        )
        return SemanticChangeIntent(
            summary="把林夏在全片中的服装改为红色风衣。",
            requested_change=payload["instruction"],
            targets=[
                SemanticChangeTarget(
                    kind=ChangeTargetKind.CHARACTER,
                    target_id=character["target_id"],
                    reason="服装是角色级资产属性，应从角色根对象传播。",
                )
            ],
            preserve=["脸、发型、表演、台词、场景和其他角色保持不变"],
        ).model_dump_json()


def _service(root: Path) -> ShortDramaService:
    language = SemanticChangeLanguageClient()
    service = ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
        language_provider_factory=lambda _api_key, _base_url: language,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    return service


def _client(service: ShortDramaService) -> TestClient:
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return TestClient(app)


def test_agent_change_plan_resolves_character_asset_and_only_dependent_shots(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    before = service.get_project(project_id).model_dump_json()

    plan = service.preview_semantic_change(
        project_id,
        SemanticChangePlanRequest(
            instruction="把林夏所有镜头里的米白风衣改成红色，其他都不变。"
        ),
    )

    assert plan.intent.targets[0].kind == ChangeTargetKind.CHARACTER
    assert plan.affected_asset_ids == [f"{project_id}-asset-linxia"]
    assert plan.affected_shot_ids
    assert len(plan.affected_shot_ids) < len(project.document.shots)
    assert ChangePlanTool.CREATE_ASSET_VERSION in [step.tool for step in plan.steps]
    assert ChangePlanTool.UPDATE_REFERENCE_PLAN in [step.tool for step in plan.steps]
    assert ChangePlanTool.REGENERATE_SHOTS in [step.tool for step in plan.steps]
    assert plan.user_editable is True
    assert plan.applies_changes is False
    assert service.get_project(project_id).model_dump_json() == before


def test_explicit_target_and_http_tool_use_the_same_impact_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot_id = project.document.shots[2].id

    response = _client(service).post(
        f"/api/short-drama/projects/{project_id}/agent-tools/plan-change",
        json={
            "instruction": "这个镜头重新调一下表演，然后再生成一版。",
            "explicit_target_ids": [shot_id],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["affected_shot_ids"] == [shot_id]
    assert payload["intent"]["targets"][0]["kind"] == ChangeTargetKind.SHOT.value
    assert payload["steps"][0]["tool"] == ChangePlanTool.REGENERATE_SHOTS.value
    assert payload["applies_changes"] is False
