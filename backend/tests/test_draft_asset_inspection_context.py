"""Saved Fresh6 draft read and QA share the real current source, never a fake shot revision."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from PIL import Image

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.models import Asset, ProductionDocument, ProductionDocumentVersion, ScriptVersion
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_asset_review_scopes import _save_work, _worker_input, _reviews

REAL = json.loads((Path(__file__).parent / "fixtures/fresh6_draft_qa_source.json").read_text())


def setup(tmp_path):
    client = CapturingLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: client)
    doc = service.initialize_fixture().document
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    # Cropped source versions retain the selected actual group only. Sequence is
    # therefore projected by the same read resolver; all shot/body fields persist.
    real = json.loads(json.dumps(REAL, ensure_ascii=False).replace(REAL["source"]["project_id"], doc.project.id))
    doc.script_versions = [ScriptVersion.model_validate(real["narrative_source"])]
    doc.project.current_script_version_id = doc.script_versions[0].id
    doc.shots = []
    doc.storyboard_document_id = None
    versions = [ProductionDocumentVersion.model_validate({**row["version"], "payload": {
        **row["payload_header"], "committed_groups": [real["selected_committed_group"]]}}) for row in real["version_sources"]]
    draft = ProductionDocument.model_validate({**real["draft_document"], "versions": [v.model_dump(mode="json") for v in versions]})
    doc.production_documents.append(draft)
    doc.assets = []
    for row in real["media_sources"]:
        asset = Asset.model_validate({**row["asset_metadata"], "versions": row["selected_versions"]})
        for version in asset.versions:
            path = tmp_path / f"{version.id}.png"
            Image.new("RGB", (24, 32), "white").save(path)
            version.local_path = str(path)
        doc.assets.append(asset)
    service.repository.save_project(doc)
    return service, client, real, draft


def invoke(service, real):
    arguments = deepcopy(real["failed_qa_turn"]["arguments"])
    result = service.tool_registry.invoke("qa.inspect_asset_version", arguments)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    return arguments, result


def test_actual_draft_request_uses_read_projection_and_exact_current_provenance(tmp_path):
    service, client, real, draft = setup(tmp_path)
    before = service.get_project(draft.project_id).document
    current = next(v for v in draft.versions if v.id == draft.current_version_id)
    read = service.tool_registry.invoke("artifact.read_current", real["successful_read_turn"]["arguments"])
    assert read.status == ToolCallStatus.SUCCEEDED, read.error
    arguments, report = invoke(service, real)
    payload = json.loads(client.input_text)
    assert payload["shot"] == read.output["content"]["shot"]
    source = report.output["shot_source"]
    assert source == {"project_id": draft.project_id, "artifact_type": "storyboard_draft", "artifact_id": draft.id,
        "version": current.version, "version_id": current.id, "shot_id": arguments["request"]["shot_id"],
        "shot_revision": payload["shot"]["revision"]}
    assert payload["shot_source"] == source and report.output["shot_revision"] is None
    assert payload["narrative_context"]["lines"][0]["id"] == "line-qwxb-004"
    refs = report.artifact_refs
    assert ArtifactRef(project_id=draft.project_id, artifact_type="storyboard_draft", artifact_id=draft.id, version=5) in refs
    assert not any(ref.artifact_type in {"shot", "shot_plan"} for ref in refs)
    assert "shot_source" not in payload["output_schema"]["properties"]
    assert service.get_project(draft.project_id).document == before


def test_draft_update_and_formal_transition_keep_old_qa_exact_after_store_reload(tmp_path):
    service, client, real, draft = setup(tmp_path)
    args1, report1 = invoke(service, real)
    doc = service.repository.get_project(draft.project_id)
    source = next(item for item in doc.production_documents if item.id == draft.id)
    prior = next(v for v in source.versions if v.id == source.current_version_id)
    updated = prior.model_copy(deep=True, update={"id": "draft-offline-update", "version": 6, "parent_version_id": prior.id})
    updated.payload["committed_groups"][0]["shots"][0]["camera"] += "；离线修改后的镜头说明。"
    source.versions.append(updated)
    source.current_version_id = updated.id
    service.repository.save_project(doc)
    args2, report2 = invoke(service, real)
    assert report2.output["shot_source"]["version"] == 6
    turns = [{"id": f"actual-qa-{i}", "sequence": i, "tool_name": "qa.inspect_asset_version",
        "arguments": args, "reason": "Observe actual saved draft", "status": "COMPLETED", "result": report.model_dump(mode="json")}
        for i, (args, report) in enumerate(((args1, report1), (args2, report2)), 1)]
    work, context = _save_work(service, draft.project_id, turns)
    resumed = ShortDramaService(database_path=service.repository.database_path, workspace_root=tmp_path / "media")
    restored = resumed.agent_runtime_store.get_work_order(work.id)
    reviews = _reviews(_worker_input(resumed, restored, context)["working_set"])
    assert {r["state"]["shot_source"]["version"] for r in reviews} == {5, 6}
    for review in reviews:
        exact = resumed.tool_registry.invoke("agent.get_tool_attempt", {"attempt_ref": review["state"]["attempt_ref"], "payload": "BOTH"})
        assert exact.status == ToolCallStatus.SUCCEEDED
        assert exact.output["result"]["output"]["shot_source"] == review["state"]["shot_source"]
    # Existing formal-state contract takes precedence after finalization. This
    # fixture changes that state in a temp repository, without forging old QA.
    doc = service.repository.get_project(draft.project_id)
    from short_drama.studio_storyboard import project_draft_shots
    project_draft_shots(doc)
    mark_formal(doc)
    service.repository.save_project(doc)
    _args, formal = invoke(service, real)
    assert formal.output["shot_source"]["artifact_type"] == "shot"
    assert formal.output["shot_revision"] == formal.output["shot_source"]["version"]
    resolver = ProjectArtifactResolver(service.repository)
    ref = next(ref for ref in report1.artifact_refs if ref.artifact_type == "storyboard_draft")
    old = resolver.summarize_artifact(ref)
    assert old is not None and prior.id in old.summary
    assert report1.output["shot_source"]["version_id"] == prior.id


@pytest.mark.parametrize("case", ["removed", "other_project", "formal_missing"])
def test_draft_resolution_never_searches_unrelated_or_historical_shots(tmp_path, case):
    service, client, real, draft = setup(tmp_path)
    doc = service.repository.get_project(draft.project_id)
    if case == "removed":
        source = next(item for item in doc.production_documents if item.id == draft.id)
        next(v for v in source.versions if v.id == source.current_version_id).payload["committed_groups"] = []
    elif case == "formal_missing":
        mark_formal(doc)
    else:
        real["failed_qa_turn"]["arguments"]["request"]["shot_id"] = "shot-from-other-project"
    service.repository.save_project(doc)
    report = service.tool_registry.invoke("qa.inspect_asset_version", real["failed_qa_turn"]["arguments"])
    assert report.status == ToolCallStatus.FAILED and client.input_text == ""


def mark_formal(doc):
    doc.production_documents.append(ProductionDocument(id="offline-formal", project_id=doc.project.id,
        kind="storyboard", title="Offline formal-state control", current_version_id="offline-formal-v1",
        versions=[ProductionDocumentVersion(id="offline-formal-v1", version=1, payload={},
            origin="AGENT", created_at="2026-09-12T12:00:00Z")]))
    doc.storyboard_document_id = "offline-formal"
