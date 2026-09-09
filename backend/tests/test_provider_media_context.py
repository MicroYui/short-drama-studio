"""Real H3 wire bodies must not leak into either Agent context entry point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.h3_models import (
    H3ContentItem, H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource,
    H3PricingSnapshot, H3ProviderRun, H3UploadedInput, H3UrlValue, H3VideoRequest,
)
from short_drama.models import EvidencePurpose, GenerationTask, OperationKind, TaskStatus, VideoGenerationRoute
from short_drama.production_manager import (
    _compact_specialist_tool_result, _specialist_tool_result_for_persistence,
)
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.tools.defaults import build_default_tool_registry
from short_drama.tools.contracts import ToolCallStatus


def _run() -> H3ProviderRun:
    body = "data:image/png;base64," + "ENCODED_IMAGE_BYTES" * 5000
    prompt = "原文对白与运动要求必须保留。"
    return H3ProviderRun(
        id="run-h3", task_id="task-h3", project_id="project-h3", shot_id="shot-1",
        route=VideoGenerationRoute.DIRECT, purpose=EvidencePurpose.PRODUCTION,
        prompt=prompt,
        media_inputs=[H3MediaInput(
            kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path="/approved/character.png"),
            asset_version_id="character-v2",
        )],
        request=H3VideoRequest(duration=13, ratio="9:16", content=[
            H3ContentItem(type="text", text=prompt),
            H3ContentItem(type="image_url", image_url=H3UrlValue(url=body)),
        ]),
        uploaded_inputs=[H3UploadedInput(
            local_path="/approved/character.png", provider_file_id=body,
            media_kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            expires_at="2026-09-09T00:00:00Z",
        )],
        pricing=H3PricingSnapshot(captured_at="2026-09-08T00:00:00Z"),
        last_error_code="H3_POLL_TRANSIENT", last_error_message="Query timed out",
        created_at="2026-09-08T00:00:00Z", updated_at="2026-09-08T00:00:00Z",
    )


@pytest.mark.parametrize("nested_attempt", [False, True])
def test_h3_run_tool_and_attempt_context_preserve_inputs_without_wire_bodies(nested_attempt: bool) -> None:
    run = _run()
    result: dict[str, JsonValue] = {
        "tool_id": "route.get_h3_run", "status": "SUCCEEDED",
        "output": cast(JsonValue, run.model_dump(mode="json")),
    }
    tool_name = "route.get_h3_run"
    if nested_attempt:
        result = {"tool_id": "agent.get_tool_attempt", "status": "SUCCEEDED", "output": {
            "attempt_ref": {"tool_name": tool_name}, "result": result,
        }}
        tool_name = "agent.get_tool_attempt"
    original = json.dumps(result, ensure_ascii=False)
    persisted = _specialist_tool_result_for_persistence(tool_name, result)
    projected = json.dumps(_compact_specialist_tool_result(tool_name, persisted), ensure_ascii=False)
    assert "ENCODED_IMAGE_BYTES" not in projected
    assert "character-v2" in projected and "/approved/character.png" in projected
    assert "H3_POLL_TRANSIENT" in projected and "Query timed out" in projected
    assert projected.count(run.prompt) == 1
    assert '"duration": 13' in projected and '"ratio": "9:16"' in projected
    assert len(projected) < 5000
    assert json.dumps(result, ensure_ascii=False) == original
    assert "ENCODED_IMAGE_BYTES" in json.dumps(persisted)


def test_provider_artifact_context_omits_transport_bodies_but_repository_retains_them(tmp_path: Path) -> None:
    repository = ShortDramaRepository(tmp_path / "test.sqlite3")
    service = ShortDramaService(database_path=repository.database_path, workspace_root=tmp_path / "media")
    run = _run()
    document = build_dont_trust_me_fixture(run.created_at)
    repository.save_project(document)
    shot = document.shots[0]
    run.project_id, run.shot_id = document.project.id, shot.id
    repository.save_task(GenerationTask(
        id=run.task_id, project_id=run.project_id, shot_id=run.shot_id,
        operation=OperationKind.GENERATE, candidate_index=0, status=TaskStatus.SUBMITTING,
        provider="minimax", model="MiniMax-H3", provider_run_id=run.id,
        request_fingerprint="test-request", input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id,
        requested_duration_seconds=13, attempt=1, created_at=run.created_at, updated_at=run.updated_at,
    ))
    repository.save_provider_run(run)
    summary = ProjectArtifactResolver(repository).summarize_artifact(ArtifactRef(
        project_id=run.project_id, artifact_type="h3_provider_run", artifact_id=run.id,
    ))
    assert summary is not None
    assert "ENCODED_IMAGE_BYTES" not in summary.summary
    assert "character-v2" in summary.summary and run.prompt in summary.summary
    persisted = repository.get_provider_run(run.task_id)
    assert persisted is not None and persisted.request == run.request
    assert persisted.uploaded_inputs == run.uploaded_inputs

    registry = build_default_tool_registry(service)
    index = registry.invoke("route.list_h3_runs", {"project_id": run.project_id})
    assert index.status == ToolCallStatus.SUCCEEDED
    serialized = index.model_dump_json()
    assert "ENCODED_IMAGE_BYTES" not in serialized
    assert run.prompt not in serialized
    assert len(serialized) < 5000
    assert index.output is not None
    entries = index.output["runs"]
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["task_id"] == run.task_id
    assert entries[0]["task_status"] == "SUBMITTING"
    assert entries[0]["prompt_characters"] == len(run.prompt)
    assert entries[0]["media_input_count"] == 1
    assert entries[0]["take_ids"] == []
    projected_index = _compact_specialist_tool_result(
        "route.list_h3_runs", cast(dict[str, JsonValue], index.model_dump(mode="json")),
    )
    assert projected_index["output"] == index.output
    exact = registry.invoke("route.get_h3_run", {"task_id": run.task_id})
    assert exact.status == ToolCallStatus.SUCCEEDED
    assert "ENCODED_IMAGE_BYTES" in exact.model_dump_json()
    assert repository.get_provider_run(run.task_id) == persisted
