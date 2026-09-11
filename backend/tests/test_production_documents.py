"""Versioned production-document handoff across service, tools, API, and runtime."""

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
)
from short_drama.deps import get_short_drama_service
from short_drama.models import (
    ProductionDocumentArtifactRef,
    ProductionDocumentCreateRequest,
    ProductionDocumentOrigin,
    ProductionDocumentReviseRequest,
    ProjectDocument,
)
from short_drama.production_manager import AgentRunCreateRequest
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from short_drama.tools.artifact_views import (
    ProductionArtifactMutationSummaryResponse,
    ProductionDocumentCompactListResponse,
    ProductionDocumentCompactReadResponse,
)


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _create_request(project_id: str) -> ProductionDocumentCreateRequest:
    return ProductionDocumentCreateRequest(
        kind="brand.fact-sheet",
        title="品牌事实表",
        payload={
            "facts": [
                {
                    "id": "fact-1",
                    "statement": "产品单次充电可使用 8 小时",
                    "status": "USER_PROVIDED",
                }
            ]
        },
        source_artifact_refs=[
            ProductionDocumentArtifactRef(
                project_id=project_id,
                artifact_type="project",
                artifact_id=project_id,
            )
        ],
        origin=ProductionDocumentOrigin.AGENT,
    )


def test_service_versions_documents_and_rejects_a_stale_base_after_restart(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id

    created = service.create_production_document(
        project_id,
        _create_request(project_id),
    )
    assert created.version.version == 1
    assert created.version.parent_version_id is None
    assert created.document.current_version_id == created.version.id

    revised = service.revise_production_document(
        project_id,
        created.document.id,
        ProductionDocumentReviseRequest(
            base_version_id=created.version.id,
            payload={"facts": [{"id": "fact-1", "status": "OBSERVED"}]},
            source_artifact_refs=created.version.source_artifact_refs,
            origin=ProductionDocumentOrigin.USER,
        ),
    )
    assert revised.version.version == 2
    assert revised.version.parent_version_id == created.version.id
    assert len(revised.document.versions) == 2

    with pytest.raises(WorkflowConflictError, match="stale version"):
        service.revise_production_document(
            project_id,
            created.document.id,
            ProductionDocumentReviseRequest(
                base_version_id=created.version.id,
                payload={"facts": []},
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    restarted = _service(tmp_path)
    historical = restarted.get_production_document(
        project_id,
        created.document.id,
        version_id=created.version.id,
    )
    assert historical.version.version == 1
    assert historical.version.payload == created.version.payload
    assert restarted.list_production_documents(project_id).documents[0].versions == (
        revised.document.versions
    )


def test_production_document_sources_are_exact_resolvable_project_artifacts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document
    project_id = project.project.id
    script = next(
        item
        for item in project.script_versions
        if item.id == project.project.current_script_version_id
    )

    created = service.create_production_document(
        project_id,
        ProductionDocumentCreateRequest(
            kind="continuity.observation",
            title="Continuity Observation",
            payload={"note": "Uses the approved script snapshot"},
            source_artifact_refs=[
                ProductionDocumentArtifactRef(
                    project_id=project_id,
                    artifact_type="script_version",
                    artifact_id=script.id,
                    version=script.version,
                )
            ],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert created.version.source_artifact_refs[0].version == script.version

    with pytest.raises(WorkflowConflictError, match="current project"):
        service.create_production_document(
            project_id,
            ProductionDocumentCreateRequest(
                kind="invalid.foreign-source",
                title="Foreign Source",
                source_artifact_refs=[
                    ProductionDocumentArtifactRef(
                        project_id="another-project",
                        artifact_type="project",
                        artifact_id="another-project",
                    )
                ],
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    resolved = service.create_production_document(
        project_id,
        ProductionDocumentCreateRequest(
            kind="continuity.source-id",
            title="Exact Source ID",
            source_artifact_refs=[
                ProductionDocumentArtifactRef(
                    project_id=project_id,
                    artifact_type="script_version",
                    artifact_id=script.id,
                )
            ],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert resolved.version.source_artifact_refs[0].version == script.version

    with pytest.raises(WorkflowConflictError, match="cannot be resolved"):
        service.revise_production_document(
            project_id,
            created.document.id,
            ProductionDocumentReviseRequest(
                base_version_id=created.version.id,
                payload={"note": "Must not persist a fabricated source"},
                source_artifact_refs=[
                    ProductionDocumentArtifactRef(
                        project_id=project_id,
                        artifact_type="script_version",
                        artifact_id=script.id,
                        version=script.version + 99,
                    )
                ],
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )
    current = service.get_production_document(project_id, created.document.id)
    assert current.version.id == created.version.id
    assert len(current.document.versions) == 1


def test_source_version_ids_resolve_without_requiring_redundant_numbers(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document
    project_id = project.project.id
    asset_version = project.assets[0].versions[0]
    bible = project.bible_versions[0]
    source = service.create_production_document(project_id, _create_request(project_id))
    expected = [
        ("asset_version", asset_version.id, asset_version.version),
        ("bible_version", bible.id, bible.version),
        ("production_document_version", source.version.id, source.version.version),
    ]
    created = service.create_production_document(
        project_id,
        ProductionDocumentCreateRequest(
            kind="production.source-notes",
            title="Sources already identify exact saved content",
            source_artifact_refs=[ProductionDocumentArtifactRef(
                project_id=project_id, artifact_type=kind, artifact_id=identity,
            ) for kind, identity, _number in expected],
            origin=ProductionDocumentOrigin.AGENT,
        ),
    )
    assert [(ref.artifact_type, ref.artifact_id, ref.version)
            for ref in created.version.source_artifact_refs] == expected


def test_project_document_accepts_legacy_json_without_production_documents(
    tmp_path: Path,
) -> None:
    document = _service(tmp_path).initialize_fixture().document
    legacy_json = document.model_dump(mode="json")
    for field_name in (
        "production_documents",
        "master_audio_document_id",
        "audio_structure_document_id",
        "timed_text_document_id",
    ):
        legacy_json.pop(field_name)

    restored = ProjectDocument.model_validate(legacy_json)

    assert restored.production_documents == []
    assert restored.master_audio_document_id is None
    assert restored.audio_structure_document_id is None
    assert restored.timed_text_document_id is None


def test_generic_document_api_cannot_bypass_managed_audio_contracts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id

    with pytest.raises(WorkflowConflictError, match="dedicated audio operation"):
        service.create_production_document(
            project_id,
            ProductionDocumentCreateRequest(
                kind="audio.master",
                title="Forged Master Audio",
                payload={"asset_version_id": "not-an-approved-audio-version"},
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    created = service.create_production_document(
        project_id,
        _create_request(project_id),
    )
    stored = service.repository.get_project(project_id)
    assert stored is not None
    stored_document = next(
        item for item in stored.production_documents if item.id == created.document.id
    )
    stored_document.kind = "audio.timed-text-lock"
    service.repository.save_project(stored)

    with pytest.raises(WorkflowConflictError, match="dedicated audio operation"):
        service.revise_production_document(
            project_id,
            created.document.id,
            ProductionDocumentReviseRequest(
                base_version_id=created.version.id,
                payload={"entries": []},
                origin=ProductionDocumentOrigin.AGENT,
            ),
        )

    current = service.get_production_document(project_id, created.document.id)
    assert current.version.id == created.version.id
    assert len(current.document.versions) == 1


def test_artifact_tools_return_parseable_version_refs_for_full_crud(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id

    created_result = service.tool_registry.invoke(
        "artifact.create_production_document",
        {
            "project_id": project_id,
            "request": _create_request(project_id).model_dump(mode="json"),
        },
    )
    assert created_result.status == ToolCallStatus.SUCCEEDED
    assert created_result.output is not None
    created = ProductionArtifactMutationSummaryResponse.model_validate(
        created_result.output
    )
    assert "versions" not in created_result.output["document"]
    assert "payload" not in created_result.output["version"]
    assert {
        (item.artifact_type, item.artifact_id, item.version)
        for item in created_result.artifact_refs
    } == {
        ("production_document", created.document.id, 1),
        ("production_document_version", created.version.id, 1),
    }

    revised_result = service.tool_registry.invoke(
        "artifact.revise_production_document",
        {
            "project_id": project_id,
            "production_document_id": created.document.id,
            "request": {
                "base_version_id": created.version.id,
                "payload": {"facts": [{"id": "fact-2"}]},
                "source_artifact_refs": [],
                "origin": "AGENT",
            },
        },
    )
    assert revised_result.status == ToolCallStatus.SUCCEEDED
    assert revised_result.artifact_refs[-1].artifact_type == (
        "production_document_version"
    )
    assert revised_result.artifact_refs[-1].version == 2

    get_result = service.tool_registry.invoke(
        "artifact.get_production_document",
        {
            "project_id": project_id,
            "production_document_id": created.document.id,
            "version_id": created.version.id,
        },
    )
    assert get_result.status == ToolCallStatus.SUCCEEDED
    assert get_result.output is not None
    read = ProductionDocumentCompactReadResponse.model_validate(get_result.output)
    assert read.version.version == 1
    assert read.document.current_version == 2
    assert (
        read.document.current_version_id
        == revised_result.artifact_refs[-1].artifact_id
    )
    serialized = json.dumps(get_result.output, ensure_ascii=False)
    assert serialized.count("产品单次充电可使用 8 小时") == 1
    assert "versions" not in get_result.output["document"]
    assert get_result.artifact_refs[-1].version == 1

    list_result = service.tool_registry.invoke(
        "artifact.list_production_documents",
        {"project_id": project_id},
    )
    assert list_result.status == ToolCallStatus.SUCCEEDED
    assert list_result.output is not None
    listed = ProductionDocumentCompactListResponse.model_validate(list_result.output)
    assert len(listed.documents) == 1
    assert "versions" not in json.dumps(list_result.output)
    assert list_result.artifact_refs[-1].version == 2


def test_resolver_and_production_board_expose_current_document_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    created = service.create_production_document(
        project_id,
        _create_request(project_id),
    )
    resolver = ProjectArtifactResolver(service.repository)
    version_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="production_document_version",
        artifact_id=created.version.id,
        version=created.version.version,
    )

    summary = resolver.summarize_artifact(version_ref)
    assert summary is not None
    assert summary.title == "品牌事实表 v1"
    assert json.loads(summary.summary)["version"]["payload"] == created.version.payload
    assert version_ref in project_artifact_refs(
        service.get_project(project_id).document
    )

    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="使用品牌事实表规划广告",
            auto_plan=False,
        ),
    )
    board = service.get_agent_production_board(run.snapshot.run.id)
    assert version_ref in board.artifact_refs


def test_production_document_http_endpoints_round_trip_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)

    created_response = client.post(
        f"/api/short-drama/projects/{project_id}/production-documents",
        json=_create_request(project_id).model_dump(mode="json"),
    )
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()

    revised_response = client.post(
        f"/api/short-drama/projects/{project_id}/production-documents/"
        f"{created['document']['id']}/revisions",
        json={
            "base_version_id": created["version"]["id"],
            "payload": {"facts": [{"id": "fact-1", "status": "OBSERVED"}]},
            "source_artifact_refs": [],
            "origin": "USER",
        },
    )
    assert revised_response.status_code == 200, revised_response.text
    assert revised_response.json()["version"]["version"] == 2

    historical_response = client.get(
        f"/api/short-drama/projects/{project_id}/production-documents/"
        f"{created['document']['id']}",
        params={"version_id": created["version"]["id"]},
    )
    assert historical_response.status_code == 200, historical_response.text
    assert historical_response.json()["version"]["version"] == 1

    listed_response = client.get(
        f"/api/short-drama/projects/{project_id}/production-documents"
    )
    assert listed_response.status_code == 200, listed_response.text
    assert listed_response.json()["documents"][0]["current_version_id"] == (
        revised_response.json()["version"]["id"]
    )
