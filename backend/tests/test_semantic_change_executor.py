"""Versioned semantic-change execution and dependency invalidation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.change_executor import (
    ChangeExecutionError,
    SemanticArtifactPatch,
    SemanticChangeApplyRequest,
    prepare_semantic_change,
)
from short_drama.change_planner import (
    ChangeTargetKind,
    SemanticChangeIntent,
    SemanticChangePlan,
    SemanticChangeTarget,
)
from short_drama.models import Freshness
from short_drama.deps import get_short_drama_service
from short_drama.service import ShortDramaService


def _service(root: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
    )


def _plan(
    *,
    project_id: str,
    kind: ChangeTargetKind,
    target_id: str,
    affected_shot_ids: list[str],
    affected_script_line_ids: list[str] | None = None,
    affected_asset_ids: list[str] | None = None,
) -> SemanticChangePlan:
    return SemanticChangePlan(
        project_id=project_id,
        instruction="执行测试中的局部语义修改",
        intent=SemanticChangeIntent(
            summary="局部修改",
            requested_change="执行测试中的局部语义修改",
            targets=[
                SemanticChangeTarget(
                    kind=kind,
                    target_id=target_id,
                    reason="测试目标",
                )
            ],
            preserve=["未命中的内容保持不变"],
        ),
        affected_script_line_ids=affected_script_line_ids or [],
        affected_asset_ids=affected_asset_ids or [],
        affected_shot_ids=affected_shot_ids,
        stale_take_ids=["take-dependent"],
    )


def test_character_patch_versions_bible_and_invalidates_only_impacted_branch(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    document = aggregate.document
    current_bible_id = document.project.current_bible_version_id
    character = document.bible_versions[0].characters[0]
    affected_shot = document.shots[0]
    untouched_shot = document.shots[-1]

    prepared = prepare_semantic_change(
        document,
        SemanticChangeApplyRequest(
            plan=_plan(
                project_id=document.project.id,
                kind=ChangeTargetKind.CHARACTER,
                target_id=character.id,
                affected_shot_ids=[affected_shot.id],
            ),
            patches=[
                SemanticArtifactPatch(
                    target_kind=ChangeTargetKind.CHARACTER,
                    target_id=character.id,
                    updates={"wardrobe": "红色风衣，其他造型保持不变"},
                )
            ],
            change_reason="用户要求局部换装",
        ),
    )

    changed = prepared.document
    assert changed.project.current_bible_version_id != current_bible_id
    assert changed.bible_versions[-1].parent_version_id == current_bible_id
    assert changed.bible_versions[-1].characters[0].wardrobe.startswith("红色风衣")
    assert changed.shots[0].freshness == Freshness.STALE
    assert next(item for item in changed.shots if item.id == untouched_shot.id).freshness == untouched_shot.freshness
    assert prepared.result.bible_version_id == changed.project.current_bible_version_id
    assert prepared.result.invalidated_shot_ids == [affected_shot.id]


def test_script_and_asset_patches_create_versions_without_external_generation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    script = document.script_versions[0]
    line = script.lines[0]
    asset = document.assets[0]
    affected_shot_ids = [
        shot.id for shot in document.shots if line.id in shot.dialogue_line_ids
    ]
    prepared = prepare_semantic_change(
        document,
        SemanticChangeApplyRequest(
            plan=SemanticChangePlan(
                project_id=document.project.id,
                instruction="修改一句台词并更新角色资产规格",
                intent=SemanticChangeIntent(
                    summary="台词和资产局部修改",
                    requested_change="修改一句台词并更新角色资产规格",
                    targets=[
                        SemanticChangeTarget(
                            kind=ChangeTargetKind.SCRIPT_LINE,
                            target_id=line.id,
                            reason="目标台词",
                        ),
                        SemanticChangeTarget(
                            kind=ChangeTargetKind.ASSET,
                            target_id=asset.id,
                            reason="目标角色资产",
                        ),
                    ],
                ),
                affected_script_line_ids=[line.id],
                affected_asset_ids=[asset.id],
                affected_shot_ids=affected_shot_ids,
            ),
            patches=[
                SemanticArtifactPatch(
                    target_kind=ChangeTargetKind.SCRIPT_LINE,
                    target_id=line.id,
                    updates={"spoken_text": "我们现在就走。", "subtitle_text": "我们现在就走。"},
                ),
                SemanticArtifactPatch(
                    target_kind=ChangeTargetKind.ASSET,
                    target_id=asset.id,
                    updates={"descriptor": "红色风衣版本，保持原角色身份特征"},
                ),
            ],
            change_reason="局部对白和服装修改",
        ),
    )

    changed = prepared.document
    assert changed.script_versions[-1].parent_version_id == script.id
    assert changed.script_versions[-1].lines[0].spoken_text == "我们现在就走。"
    changed_asset = next(item for item in changed.assets if item.id == asset.id)
    assert len(changed_asset.versions) == len(asset.versions) + 1
    assert changed_asset.versions[-1].media_type.value == "TEXT_REFERENCE"
    assert prepared.result.asset_version_ids == [changed_asset.versions[-1].id]
    assert "No external generation provider" in prepared.result.verification.messages[1]


def test_patch_cannot_escape_the_edited_impact_plan(tmp_path: Path) -> None:
    document = _service(tmp_path).initialize_fixture().document
    character = document.bible_versions[0].characters[0]
    other_character = document.bible_versions[0].characters[1]
    request = SemanticChangeApplyRequest(
        plan=_plan(
            project_id=document.project.id,
            kind=ChangeTargetKind.CHARACTER,
            target_id=character.id,
            affected_shot_ids=[document.shots[0].id],
        ),
        patches=[
            SemanticArtifactPatch(
                target_kind=ChangeTargetKind.CHARACTER,
                target_id=other_character.id,
                updates={"wardrobe": "不应被修改"},
            )
        ],
        change_reason="越界测试",
    )

    with pytest.raises(ChangeExecutionError, match="outside the editable impact plan"):
        prepare_semantic_change(document, request)


def test_apply_endpoint_persists_patch_and_dependency_invalidation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    character = document.bible_versions[0].characters[0]
    shot = document.shots[0]
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    response = TestClient(app).post(
        f"/api/short-drama/projects/{document.project.id}/agent-tools/apply-change",
        json=SemanticChangeApplyRequest(
            plan=_plan(
                project_id=document.project.id,
                kind=ChangeTargetKind.CHARACTER,
                target_id=character.id,
                affected_shot_ids=[shot.id],
            ),
            patches=[
                SemanticArtifactPatch(
                    target_kind=ChangeTargetKind.CHARACTER,
                    target_id=character.id,
                    updates={"wardrobe": "深红色风衣"},
                )
            ],
            change_reason="端到端局部换装",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["applied_patch_count"] == 1
    persisted = service.get_project(document.project.id)
    current_bible = next(
        item
        for item in persisted.document.bible_versions
        if item.id == persisted.document.project.current_bible_version_id
    )
    assert current_bible.characters[0].wardrobe == "深红色风衣"
    assert persisted.document.shots[0].freshness == Freshness.STALE
