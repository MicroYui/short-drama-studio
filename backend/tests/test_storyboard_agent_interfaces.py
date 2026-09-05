"""Agent-facing tools, HTTP adapters, and exact storyboard artifact refs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver,
    project_artifact_refs,
    project_summary,
)
from short_drama.deps import get_short_drama_service
from short_drama.models import ProductionDocumentArtifactRef, ProductionDocumentOrigin
from short_drama.production_contracts import (
    ClipGroup,
    GENERATION_PLAN_OUTPUT_SLOT_ID,
    STORYBOARD_OUTPUT_SLOT_ID,
    GenerationPlanCompileRequest,
    GenerationPlanMutationResponse,
    OutputSlotBindRequest,
    OutputSlotListResponse,
    ProductionAudioDirection,
    ProductionProviderPreference,
    ProductionSpecContent,
    ProductionSpecCreateRequest,
    ProductionSpecMutationResponse,
    ProductionSpecReplaceRequest,
    ProductionVisualDirection,
    StoryboardContent,
    StoryboardCreateRequest,
    StoryboardMutationResponse,
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


def test_staged_storyboard_http_interfaces_are_exposed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    paths = app.openapi()["paths"]
    draft_prefix = "/api/short-drama/projects/{project_id}/storyboard-drafts"
    assert {
        draft_prefix,
        f"{draft_prefix}/{{draft_document_id}}/ledger/segments",
        f"{draft_prefix}/{{draft_document_id}}/ledger/complete",
        f"{draft_prefix}/{{draft_document_id}}/outline/segments",
        f"{draft_prefix}/{{draft_document_id}}/outline/complete",
        f"{draft_prefix}/{{draft_document_id}}/groups",
        f"{draft_prefix}/{{draft_document_id}}/verification",
        f"{draft_prefix}/{{draft_document_id}}/finalize",
    } <= paths.keys()

    for path in (
        f"{draft_prefix}/{{draft_document_id}}/ledger/segments",
        f"{draft_prefix}/{{draft_document_id}}/ledger/complete",
        f"{draft_prefix}/{{draft_document_id}}/outline/segments",
        f"{draft_prefix}/{{draft_document_id}}/outline/complete",
    ):
        operation = paths[path]["post"]
        assert operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/StoryboardDraftMutationResponse")


def _production_spec(service: ShortDramaService, project_id: str) -> ProductionSpecContent:
    document = service.get_project(project_id).document
    return ProductionSpecContent(
        project_id=project_id,
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        title="《别相信我》Agent 制片规格",
        production_scope="从完整剧本形成分镜、资产、视频、音频和初步时间线。",
        production_mode="Agent 自主制片，保留每个编辑镜头的独立边界。",
        genre_and_setting="雨夜便利店内发生的悬疑对峙。",
        language="zh-CN",
        aspect_ratio="9:16",
        target_duration_seconds=document.project.target_duration_seconds,
        duration_intent="按完整剧本自然拆镜，不把生成单元误当成编辑镜头。",
        visual=ProductionVisualDirection(
            medium_lock="写实影像",
            style_brief=["竖屏近距离对峙", "快速进入冲突"],
            lighting_intent="门外冷色雨光与店内暖白顶灯形成对比。",
        ),
        audio=ProductionAudioDirection(
            dialogue_intent="对白清晰并保留现场环境声。",
            ambience_intent="雨声和冰箱低频嗡鸣持续存在。",
        ),
        provider_preference=ProductionProviderPreference(
            provider="minimax",
            model="MiniMax-H3",
            resolution="768P",
        ),
        user_constraints=["不启用 LTX 模型"],
        agent_decisions=["由分镜语义决定 ClipGroup，而非固定镜头数"],
    )


def _storyboard(
    service: ShortDramaService,
    project_id: str,
    *,
    production_spec_document_id: str,
    production_spec_version_id: str,
) -> StoryboardContent:
    document = service.get_project(project_id).document
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    duration = sum(item.duration_seconds for item in document.shots)
    return StoryboardContent(
        project_id=project_id,
        title="《别相信我》完整分镜",
        script_version_id=document.project.current_script_version_id,
        bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=production_spec_document_id,
        production_spec_version_id=production_spec_version_id,
        script_line_ids=[item.id for item in script.lines],
        shots=document.shots,
        clip_groups=[
            ClipGroup(
                id=f"clip-group-{item.id}",
                title=f"雨夜便利店动作段 {sequence}",
                shot_ids=[item.id],
                duration_seconds=int(item.duration_seconds),
                generation_intent="在 H3 单次时长内完成当前镜头的可见动作。",
                transition_intent="与相邻生成单元保持语义连续。",
                continuity_in=["林夏位于轴线左侧"],
                visible_changes=[item.visual],
                continuity_out=["林夏视线锁定周衡"],
            )
            for sequence, item in enumerate(document.shots, start=1)
        ],
        computed_duration_seconds=duration,
        timing_notes=["台词结束后保留人物反应"],
    )


def test_project_context_prioritizes_canonical_documents_and_compacts_shots(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=_production_spec(service, project_id),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    storyboard = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=_storyboard(
                service,
                project_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    document = service.get_project(project_id).document
    template_shot = document.shots[0]
    document = document.model_copy(
        update={
            "shots": [
                template_shot.model_copy(
                    update={
                        "id": f"summary-shot-{index}",
                        "sequence": index + 1,
                        "narrative_purpose": f"summary-purpose-{index}",
                        "visual": f"summary-visual-{index}",
                    }
                )
                for index in range(12)
            ]
        }
    )

    refs = project_artifact_refs(document)
    ref_types = [item.artifact_type for item in refs]
    first_scene_or_shot = min(
        index
        for index, artifact_type in enumerate(ref_types)
        if artifact_type in {"scene", "shot"}
    )

    assert ref_types.index("production_spec") < ref_types.index("output_slot")
    assert ref_types.index("storyboard") < ref_types.index("output_slot")
    assert {"production_spec", "storyboard", "generation_plan", "output_slot"} <= set(
        ref_types[:12]
    )
    assert not {"scene", "shot", "clip_group", "generation_unit"} & set(
        ref_types[:12]
    )
    assert max(
        index
        for index, artifact_type in enumerate(ref_types)
        if artifact_type == "output_slot"
    ) < first_scene_or_shot
    assert ref_types.index("clip_group") > first_scene_or_shot

    summary = project_summary(document)
    assert summary.index("Current production documents:") < summary.index(
        "Original user brief:"
    )
    assert storyboard.document.id in summary
    assert STORYBOARD_OUTPUT_SLOT_ID in summary
    assert "Counts: characters " in summary
    assert "shots 12;" in summary
    assert "Shot tail (last 5 of 12):" in summary
    assert "summary-shot-0#1" not in summary
    assert "summary-shot-11#12" in summary


def test_agent_tools_handoff_exact_storyboard_and_generation_artifacts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    spec = _production_spec(service, project_id)

    spec_call = service.tool_registry.invoke(
        "artifact.create_production_spec",
        {
            "project_id": project_id,
            "request": {
                "spec": spec.model_dump(mode="json"),
                "origin": ProductionDocumentOrigin.AGENT,
            },
        },
    )

    assert spec_call.status == ToolCallStatus.SUCCEEDED
    assert spec_call.output is not None
    spec_result = ProductionSpecMutationResponse.model_validate(spec_call.output)
    assert spec_call.artifact_refs[0] == ArtifactRef(
        project_id=project_id,
        artifact_type="production_spec",
        artifact_id=spec_result.document.id,
        version=1,
    )

    storyboard = _storyboard(
        service,
        project_id,
        production_spec_document_id=spec_result.document.id,
        production_spec_version_id=spec_result.version.id,
    )
    storyboard_call = service.tool_registry.invoke(
        "shot.create_storyboard",
        {
            "project_id": project_id,
            "request": {
                "storyboard": storyboard.model_dump(mode="json"),
                "origin": ProductionDocumentOrigin.AGENT,
            },
        },
    )

    assert storyboard_call.status == ToolCallStatus.SUCCEEDED
    assert storyboard_call.output is not None
    storyboard_result = StoryboardMutationResponse.model_validate(
        storyboard_call.output
    )
    storyboard_ref_types = {item.artifact_type for item in storyboard_call.artifact_refs}
    assert {"storyboard", "shot", "clip_group"} <= storyboard_ref_types

    plan_call = service.tool_registry.invoke(
        "route.compile_generation_plan",
        {
            "project_id": project_id,
            "request": {
                "storyboard_document_id": storyboard_result.document.id,
                "storyboard_version_id": storyboard_result.version.id,
                "production_spec_document_id": spec_result.document.id,
                "production_spec_version_id": spec_result.version.id,
                "origin": ProductionDocumentOrigin.AGENT,
            },
        },
    )

    assert plan_call.status == ToolCallStatus.SUCCEEDED
    assert plan_call.output is not None
    plan_result = GenerationPlanMutationResponse.model_validate(plan_call.output)
    assert {item.artifact_type for item in plan_call.artifact_refs} == {
        "generation_plan",
        "generation_unit",
    }
    assert sum(
        len(item.shot_windows) for item in plan_result.generation_plan.units
    ) == len(storyboard.shots)
    unit = plan_result.generation_plan.units[0]

    bind_call = service.tool_registry.invoke(
        "project.bind_output_slot",
        {
            "project_id": project_id,
            "request": {
                "slot_id": "video.next-generation-unit",
                "artifact_ref": {
                    "project_id": project_id,
                    "artifact_type": "generation_unit",
                    "artifact_id": unit.id,
                    "version": plan_result.version.version,
                },
            },
        },
    )

    assert bind_call.status == ToolCallStatus.SUCCEEDED
    assert [item.artifact_type for item in bind_call.artifact_refs] == [
        "output_slot",
        "generation_unit",
    ]
    get_slot_call = service.tool_registry.invoke(
        "project.get_output_slot",
        {"project_id": project_id, "slot_id": "video.next-generation-unit"},
    )
    list_slots_call = service.tool_registry.invoke(
        "project.list_output_slots",
        {"project_id": project_id},
    )
    assert get_slot_call.status == ToolCallStatus.SUCCEEDED
    assert list_slots_call.status == ToolCallStatus.SUCCEEDED
    assert list_slots_call.output is not None
    listed_slots = OutputSlotListResponse.model_validate(list_slots_call.output)
    assert "video.next-generation-unit" in {
        item.id for item in listed_slots.output_slots
    }

    rebound_slot = service.bind_output_slot(
        project_id,
        OutputSlotBindRequest(
            slot_id="video.next-generation-unit",
            artifact_ref=ProductionDocumentArtifactRef(
                project_id=project_id,
                artifact_type="production_spec",
                artifact_id=spec_result.document.id,
                version=spec_result.version.version,
            ),
            expected_revision=1,
        ),
    ).output_slot
    resolver = ProjectArtifactResolver(service.repository)
    historical_slot = resolver.summarize_artifact(
        ArtifactRef(
            project_id=project_id,
            artifact_type="output_slot",
            artifact_id="video.next-generation-unit",
            version=1,
        )
    )
    current_slot = resolver.summarize_artifact(
        ArtifactRef(
            project_id=project_id,
            artifact_type="output_slot",
            artifact_id="video.next-generation-unit",
            version=rebound_slot.revision,
        )
    )
    assert historical_slot is not None
    assert current_slot is not None
    assert json.loads(historical_slot.summary)["current_artifact_ref"][
        "artifact_id"
    ] == unit.id
    assert json.loads(current_slot.summary)["current_artifact_ref"][
        "artifact_id"
    ] == spec_result.document.id


def test_historical_shot_refs_and_new_http_reads_remain_resolvable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=_production_spec(service, project_id),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    created = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=_storyboard(
                service,
                project_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    target = created.storyboard.shots[0]
    revised = target.model_copy(update={"visual": target.visual + "，镜头落到雨滴"})
    patched_call = service.tool_registry.invoke(
        "shot.patch_storyboard",
        {
            "project_id": project_id,
            "request": {
                "base_version_id": created.version.id,
                "replacements": [
                    {
                        "shot_id": target.id,
                        "expected_revision": target.revision,
                        "replacement": revised.model_dump(mode="json"),
                    }
                ],
                "origin": ProductionDocumentOrigin.AGENT,
            },
        },
    )
    assert patched_call.status == ToolCallStatus.SUCCEEDED

    resolver = ProjectArtifactResolver(service.repository)
    historical = resolver.summarize_artifact(
        ArtifactRef(
            project_id=project_id,
            artifact_type="shot",
            artifact_id=target.id,
            version=target.revision,
        )
    )

    assert historical is not None
    historical_payload = json.loads(historical.summary)
    assert historical_payload["shot"]["visual"] == target.visual
    assert historical_payload["storyboard_version"] == 1
    current_document = service.get_project(project_id).document
    ref_types = {item.artifact_type for item in project_artifact_refs(current_document)}
    assert {"production_spec", "storyboard", "clip_group", "output_slot"} <= ref_types

    with _client(service) as client:
        assert client.get(
            f"/api/short-drama/projects/{project_id}/production-spec"
        ).status_code == 200
        assert client.get(
            f"/api/short-drama/projects/{project_id}/storyboard"
        ).status_code == 200
        assert client.get(
            f"/api/short-drama/projects/{project_id}/output-slots"
        ).status_code == 200


def test_replacing_production_spec_invalidates_exact_downstream_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    spec_content = _production_spec(service, project_id)
    spec = service.create_production_spec(
        project_id,
        ProductionSpecCreateRequest(
            spec=spec_content,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    storyboard = service.create_storyboard(
        project_id,
        StoryboardCreateRequest(
            storyboard=_storyboard(
                service,
                project_id,
                production_spec_document_id=spec.document.id,
                production_spec_version_id=spec.version.id,
            ),
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    service.compile_generation_plan(
        project_id,
        GenerationPlanCompileRequest(
            storyboard_document_id=storyboard.document.id,
            storyboard_version_id=storyboard.version.id,
            production_spec_document_id=spec.document.id,
            production_spec_version_id=spec.version.id,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )

    replaced = service.replace_production_spec(
        project_id,
        ProductionSpecReplaceRequest(
            base_version_id=spec.version.id,
            spec=spec_content,
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert replaced.version.version == 2
    slots = {
        item.id: item for item in service.list_output_slots(project_id).output_slots
    }
    assert slots[STORYBOARD_OUTPUT_SLOT_ID].current_artifact_ref is None
    assert slots[GENERATION_PLAN_OUTPUT_SLOT_ID].current_artifact_ref is None

    with pytest.raises(
        WorkflowConflictError,
        match="different production spec version",
    ):
        service.compile_generation_plan(
            project_id,
            GenerationPlanCompileRequest(
                storyboard_document_id=storyboard.document.id,
                storyboard_version_id=storyboard.version.id,
                production_spec_document_id=replaced.document.id,
                production_spec_version_id=replaced.version.id,
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
