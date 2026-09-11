"""Recoverable handoff coverage for H3 route-plan image preparations."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from _routes.short_drama import router as short_drama_router
from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import (
    H3MediaRole,
    H3ProductionRouteExecuteRequest,
    H3ProductionRoutePlanRequest,
    H3RouteAssetPreparationUpdateRequest,
    H3RoutePlanStatus,
    H3ShotRouteDirective,
)
from short_drama.image_models import (
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReviewDecision,
    AssetReviewRequest,
)
from short_drama.models import (
    AssetKind,
    VideoGenerationRoute,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _client(service: ShortDramaService) -> TestClient:
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    return TestClient(app)


def _pending_start_frame_preparation(
    service: ShortDramaService,
) -> tuple[str, str, str, str]:
    project = service.initialize_fixture()
    document = project.document
    shot = next(item for item in document.shots if item.sequence == 2)
    compiled = service.compile_h3_production_route_plan(
        document.project.id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot.id],
            use_language_agent=False,
            consider_video_guides=False,
            shot_directives=[
                H3ShotRouteDirective(
                    shot_id=shot.id,
                    candidate_id=f"frame-first-pending:{shot.id}",
                )
            ],
        ),
    )
    decision = compiled.plan.decisions[0]
    preparation = next(
        item
        for item in decision.asset_preparations
        if item.role == H3MediaRole.FIRST_FRAME
    )
    return document.project.id, shot.id, compiled.plan.id, preparation.id


def _plan_state_revision(service: ShortDramaService, plan_id: str) -> int:
    plan = service.repository.get_h3_route_plan(plan_id)
    assert plan is not None
    return plan.state_revision


def _approved_image(
    service: ShortDramaService,
    tmp_path: Path,
    project_id: str,
) -> tuple[str, str]:
    created = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="镜头起始帧",
            descriptor="H3 镜头起始状态",
        ),
    )
    source = tmp_path / "approved-start-frame.png"
    Image.new("RGB", (768, 1344), "#5f6f52").save(source)
    imported = service.import_local_asset_version(
        project_id,
        created.asset.id,
        AssetImportLocalRequest(
            local_path=str(source),
            descriptor="已生成的镜头起始帧",
        ),
    )
    approved = service.review_asset_version(
        project_id,
        created.asset.id,
        imported.version.id,
        AssetReviewRequest(
            decision=AssetReviewDecision.APPROVED,
            purpose="CANONICAL_MASTER",
            notes="构图与角色连续性可用",
        ),
    )
    return created.asset.id, approved.version.id


def test_api_and_tool_persist_ready_handoff_across_restart(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    asset_id, version_id = _approved_image(service, tmp_path, project_id)
    endpoint = (
        f"/api/short-drama/projects/{project_id}/h3/production-route-plans/"
        f"{plan_id}/asset-preparations/{preparation_id}"
    )

    generating = _client(service).post(
        endpoint,
        json={
            "expected_state_revision": _plan_state_revision(service, plan_id),
            "shot_id": shot_id,
            "role": H3MediaRole.FIRST_FRAME.value,
            "status": "GENERATING",
            "asset_id": asset_id,
        },
    )
    assert generating.status_code == 200, generating.text
    assert generating.json()["preparation"]["status"] == "GENERATING"
    generating_state_revision = generating.json()["plan"]["state_revision"]
    old_plan_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="h3_route_plan",
        artifact_id=plan_id,
        version=generating_state_revision,
    )
    old_preparation_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="h3_route_asset_preparation",
        artifact_id=preparation_id,
        version=generating_state_revision,
    )

    ready_arguments = {
        "project_id": project_id,
        "plan_id": plan_id,
        "preparation_id": preparation_id,
        "request": {
            "expected_state_revision": generating_state_revision,
            "shot_id": shot_id,
            "role": H3MediaRole.FIRST_FRAME.value,
            "status": "READY",
            "approved_version_id": version_id,
            "review_summary": "起始状态、构图与道具位置均可用。",
        },
    }
    ready = service.tool_registry.invoke(
        "route.update_h3_asset_preparation",
        ready_arguments,
    )
    assert ready.status == ToolCallStatus.SUCCEEDED
    assert ready.output is not None
    assert ready.output["preparation"]["approved_version_id"] == version_id
    ready_state_revision = ready.output["plan"]["state_revision"]
    assert ready_state_revision == generating_state_revision + 1
    assert ready.output["approved_version_number"] is not None
    assert {
        (item.artifact_type, item.artifact_id) for item in ready.artifact_refs
    } >= {
        ("h3_route_plan", plan_id),
        ("h3_route_asset_preparation", preparation_id),
        ("asset", asset_id),
        ("asset_version", version_id),
    }
    refs_by_type = {item.artifact_type: item for item in ready.artifact_refs}
    assert refs_by_type["h3_route_plan"].version == ready_state_revision
    assert refs_by_type["h3_route_asset_preparation"].version == ready_state_revision
    assert refs_by_type["asset_version"].version == ready.output[
        "approved_version_number"
    ]

    resolver = ProjectArtifactResolver(service.repository)
    assert resolver.summarize_artifact(old_plan_ref) is None
    assert resolver.summarize_artifact(old_preparation_ref) is None
    assert resolver.summarize_artifact(refs_by_type["h3_route_plan"]) is not None
    preparation_summary = resolver.summarize_artifact(
        refs_by_type["h3_route_asset_preparation"]
    )
    assert preparation_summary is not None
    assert f'"parent_plan_state_revision": {ready_state_revision}' in (
        preparation_summary.summary
    )

    run = service.agent_runtime.create_run(
        project_id=project_id,
        objective="Continue H3 production from the exact image handoff",
        root_agent="production-manager",
    )
    work_order = service.agent_runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="video-worker",
        objective="Compile the exact prepared H3 inputs",
        why_now="The boundary image has been approved",
        scope=list(ready.artifact_refs),
    )
    context = service.agent_runtime.work_order_context(work_order.id)
    expected_refs = [
        item.model_copy(update={"version": None})
        if item.artifact_type in {"h3_route_plan", "h3_route_asset_preparation"}
        else item for item in ready.artifact_refs
    ]
    assert {item.ref.identity() for item in context.artifacts} == {
        item.identity() for item in expected_refs
    }
    first_updated_at = ready.output["plan"]["updated_at"]

    repeated = service.tool_registry.invoke(
        "route.update_h3_asset_preparation",
        ready_arguments,
    )
    assert repeated.status == ToolCallStatus.FAILED
    assert repeated.error is not None
    assert repeated.error.kind == "WorkflowConflictError"
    assert "expected revision" in repeated.error.message
    unchanged = service.repository.get_h3_route_plan(plan_id)
    assert unchanged is not None
    assert unchanged.updated_at == first_updated_at
    assert unchanged.state_revision == ready_state_revision

    recovered = _service(tmp_path)
    persisted = recovered.repository.get_h3_route_plan(plan_id)
    assert persisted is not None
    persisted_preparation = persisted.decisions[0].asset_preparations[0]
    assert persisted_preparation.status.value == "READY"
    assert persisted_preparation.asset_id == asset_id
    assert persisted_preparation.approved_version_id == version_id

    next_plan = recovered.compile_h3_production_route_plan(
        project_id,
        H3ProductionRoutePlanRequest(
            shot_ids=[shot_id],
            use_language_agent=False,
            consider_video_guides=False,
            shot_directives=[
                H3ShotRouteDirective(
                    shot_id=shot_id,
                    candidate_id=f"frame-first:{shot_id}",
                )
            ],
        ),
    ).plan
    decision = next_plan.decisions[0]
    assert decision.route == VideoGenerationRoute.FRAME_FIRST
    assert decision.media_inputs[0].asset_version_id == version_id


def test_route_plan_and_execute_tool_refs_follow_exact_plan_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    planned = service.tool_registry.invoke(
        "route.plan_h3",
        {
            "project_id": project_id,
            "request": {
                "shot_ids": [shot.id],
                "use_language_agent": False,
                "consider_video_guides": False,
                "shot_directives": [
                    {
                        "shot_id": shot.id,
                        "candidate_id": f"direct:{shot.id}",
                    }
                ],
            },
        },
    )
    assert planned.status == ToolCallStatus.SUCCEEDED
    assert planned.output is not None
    planned_ref = planned.artifact_refs[0]
    assert planned_ref.artifact_type == "h3_route_plan"
    assert planned_ref.version == planned.output["plan"]["state_revision"]

    resolver = ProjectArtifactResolver(service.repository)
    assert resolver.summarize_artifact(planned_ref) is not None
    executed = service.tool_registry.invoke(
        "route.execute_h3_plan",
        {
            "project_id": project_id,
            "plan_id": planned_ref.artifact_id,
            "request": {
                "expected_state_revision": planned_ref.version,
                "enqueue_new_generations": False,
                "inspect_reused_takes": False,
                "rebuild_manifest": False,
            },
        },
    )
    assert executed.status == ToolCallStatus.SUCCEEDED
    assert executed.output is not None
    executed_ref = executed.artifact_refs[0]
    assert executed_ref.artifact_type == "h3_route_plan"
    assert executed_ref.version == executed.output["plan"]["state_revision"]
    assert executed_ref.version == planned_ref.version + 1
    assert resolver.summarize_artifact(planned_ref) is None
    assert resolver.summarize_artifact(executed_ref) is not None

    run = service.agent_runtime.create_run(
        project_id=project_id,
        objective="Continue from the exact executed H3 route state",
        root_agent="production-manager",
    )
    order = service.agent_runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="video-worker",
        objective="Read the executed H3 route state",
        why_now="The route execution state changed",
        scope=list(executed.artifact_refs),
    )
    context = service.agent_runtime.work_order_context(order.id)
    assert [item.ref for item in context.artifacts] == [
        item.model_copy(update={"version": None}) for item in executed.artifact_refs
    ]


def test_stale_execute_cannot_overwrite_a_newer_asset_preparation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    initial_revision = _plan_state_revision(service, plan_id)
    asset = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="正在生成的起始帧",
            descriptor="H3 边界帧工作进度",
        ),
    ).asset
    updated = service.update_h3_route_asset_preparation(
        project_id,
        plan_id,
        preparation_id,
        H3RouteAssetPreparationUpdateRequest(
            expected_state_revision=initial_revision,
            shot_id=shot_id,
            role=H3MediaRole.FIRST_FRAME,
            status="GENERATING",
            asset_id=asset.id,
        ),
    )

    with pytest.raises(WorkflowConflictError, match="expected revision"):
        service.execute_h3_production_route_plan(
            project_id,
            plan_id,
            H3ProductionRouteExecuteRequest(
                expected_state_revision=initial_revision,
                enqueue_new_generations=False,
                inspect_reused_takes=False,
                rebuild_manifest=False,
            ),
        )

    persisted = service.repository.get_h3_route_plan(plan_id)
    assert persisted is not None
    preparation = persisted.decisions[0].asset_preparations[0]
    assert persisted.state_revision == updated.plan.state_revision
    assert preparation.status.value == "GENERATING"
    assert preparation.asset_id == asset.id


def test_rejected_handoff_and_exact_identity_checks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )

    with pytest.raises(WorkflowConflictError, match="expects role first_frame"):
        service.update_h3_route_asset_preparation(
            project_id,
            plan_id,
            preparation_id,
            H3RouteAssetPreparationUpdateRequest(
                expected_state_revision=_plan_state_revision(service, plan_id),
                shot_id=shot_id,
                role=H3MediaRole.LAST_FRAME,
                status="REJECTED",
                review_summary="角色形象不连续。",
            ),
        )

    rejected = service.update_h3_route_asset_preparation(
        project_id,
        plan_id,
        preparation_id,
        H3RouteAssetPreparationUpdateRequest(
            expected_state_revision=_plan_state_revision(service, plan_id),
            shot_id=shot_id,
            role=H3MediaRole.FIRST_FRAME,
            status="REJECTED",
            review_summary="角色形象不连续。",
        ),
    )
    assert rejected.preparation.status.value == "REJECTED"
    assert rejected.preparation.approved_version_id is None
    assert rejected.preparation.review_summary == "角色形象不连续。"

    document = service.repository.get_project(project_id)
    assert document is not None
    shot = next(item for item in document.shots if item.id == shot_id)
    shot.revision += 1
    service.repository.save_project(document)
    with pytest.raises(WorkflowConflictError, match="outdated shot revisions"):
        service.update_h3_route_asset_preparation(
            project_id,
            plan_id,
            preparation_id,
            H3RouteAssetPreparationUpdateRequest(
                expected_state_revision=rejected.plan.state_revision,
                shot_id=shot_id,
                role=H3MediaRole.FIRST_FRAME,
                status="REJECTED",
                review_summary="新分镜已经使该图失效。",
            ),
        )
    stale = service.repository.get_h3_route_plan(plan_id)
    assert stale is not None
    assert stale.status == H3RoutePlanStatus.STALE
    assert stale.state_revision == rejected.plan.state_revision + 1


def test_ready_requires_a_current_usable_image(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    draft = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="未完成的起始帧",
            descriptor="仅有文本说明",
        ),
    )

    with pytest.raises(WorkflowConflictError, match="current usable image"):
        service.update_h3_route_asset_preparation(
            project_id,
            plan_id,
            preparation_id,
            H3RouteAssetPreparationUpdateRequest(
                expected_state_revision=_plan_state_revision(service, plan_id),
                shot_id=shot_id,
                role=H3MediaRole.FIRST_FRAME,
                status="READY",
                asset_id=draft.asset.id,
                approved_version_id=draft.version.id,
            ),
        )


def test_superseded_ready_image_is_not_reused_by_next_route_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    asset_id, version_id = _approved_image(service, tmp_path, project_id)
    service.update_h3_route_asset_preparation(
        project_id,
        plan_id,
        preparation_id,
        H3RouteAssetPreparationUpdateRequest(
            expected_state_revision=_plan_state_revision(service, plan_id),
            shot_id=shot_id,
            role=H3MediaRole.FIRST_FRAME,
            status="READY",
            asset_id=asset_id,
            approved_version_id=version_id,
        ),
    )

    replacement_path = tmp_path / "replacement-start-frame.png"
    Image.new("RGB", (768, 1344), "#374151").save(replacement_path)
    replacement = service.import_local_asset_version(
        project_id,
        asset_id,
        AssetImportLocalRequest(
            local_path=str(replacement_path),
            descriptor="替换后的镜头起始帧",
        ),
    )
    service.review_asset_version(
        project_id,
        asset_id,
        replacement.version.id,
        AssetReviewRequest(
            decision=AssetReviewDecision.APPROVED,
            purpose="CANONICAL_MASTER",
            notes="新版本取代原关键帧",
        ),
    )

    with pytest.raises(WorkflowConflictError) as exc_info:
        service.compile_h3_production_route_plan(
            project_id,
            H3ProductionRoutePlanRequest(
                shot_ids=[shot_id],
                use_language_agent=False,
                consider_video_guides=False,
                shot_directives=[
                    H3ShotRouteDirective(
                        shot_id=shot_id,
                        candidate_id=f"frame-first:{shot_id}",
                    )
                ],
            ),
        )
    assert "is unavailable for shot" in str(exc_info.value)


@pytest.mark.parametrize("changed_input", ["script", "bible", "voice"])
def test_ready_preparation_is_not_reused_after_any_plan_input_changes(
    tmp_path: Path,
    changed_input: str,
) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    asset_id, version_id = _approved_image(service, tmp_path, project_id)
    service.update_h3_route_asset_preparation(
        project_id,
        plan_id,
        preparation_id,
        H3RouteAssetPreparationUpdateRequest(
            expected_state_revision=_plan_state_revision(service, plan_id),
            shot_id=shot_id,
            role=H3MediaRole.FIRST_FRAME,
            status="READY",
            asset_id=asset_id,
            approved_version_id=version_id,
        ),
    )

    document = service.repository.get_project(project_id)
    assert document is not None
    if changed_input == "script":
        current = next(
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        )
        replacement = current.model_copy(
            deep=True,
            update={
                "id": "script-h3-freshness-replacement",
                "version": current.version + 1,
                "parent_version_id": current.id,
            },
        )
        document.script_versions.append(replacement)
        document.project.current_script_version_id = replacement.id
    elif changed_input == "bible":
        current_bible = next(
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        )
        replacement_bible = current_bible.model_copy(
            deep=True,
            update={
                "id": "bible-h3-freshness-replacement",
                "version": current_bible.version + 1,
                "parent_version_id": current_bible.id,
            },
        )
        document.bible_versions.append(replacement_bible)
        document.project.current_bible_version_id = replacement_bible.id
    else:
        document.project.voice_reference_revision += 1
    service.repository.save_project(document)

    with pytest.raises(WorkflowConflictError, match="is unavailable for shot"):
        service.compile_h3_production_route_plan(
            project_id,
            H3ProductionRoutePlanRequest(
                shot_ids=[shot_id],
                use_language_agent=False,
                consider_video_guides=False,
                shot_directives=[
                    H3ShotRouteDirective(
                        shot_id=shot_id,
                        candidate_id=f"frame-first:{shot_id}",
                    )
                ],
            ),
        )


def test_ready_rejects_superseded_preparation_source_versions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, shot_id, plan_id, preparation_id = (
        _pending_start_frame_preparation(service)
    )
    source_asset_id, source_version_id = _approved_image(
        service,
        tmp_path,
        project_id,
    )
    plan = service.repository.get_h3_route_plan(plan_id)
    assert plan is not None
    preparation = plan.decisions[0].asset_preparations[0]
    preparation.input_version_ids = [source_version_id]
    service.repository.save_h3_route_plan(plan)

    replacement_path = tmp_path / "replacement-preparation-source.png"
    Image.new("RGB", (768, 1344), "#111827").save(replacement_path)
    replacement = service.import_local_asset_version(
        project_id,
        source_asset_id,
        AssetImportLocalRequest(
            local_path=str(replacement_path),
            descriptor="取代关键帧生成时使用的上游参考",
        ),
    )
    service.review_asset_version(
        project_id,
        source_asset_id,
        replacement.version.id,
        AssetReviewRequest(
            decision=AssetReviewDecision.APPROVED,
            purpose="CANONICAL_MASTER",
            notes="新参考已取代旧版本",
        ),
    )
    output_asset_id, output_version_id = _approved_image(
        service,
        tmp_path,
        project_id,
    )

    with pytest.raises(WorkflowConflictError, match="source asset versions"):
        service.update_h3_route_asset_preparation(
            project_id,
            plan_id,
            preparation_id,
            H3RouteAssetPreparationUpdateRequest(
                expected_state_revision=_plan_state_revision(service, plan_id),
                shot_id=shot_id,
                role=H3MediaRole.FIRST_FRAME,
                status="READY",
                asset_id=output_asset_id,
                approved_version_id=output_version_id,
            ),
        )
