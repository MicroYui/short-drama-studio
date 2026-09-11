"""Editor selection survives HTTP, persistence and actual Runner model handoffs."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.repository import ShortDramaRepository
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioEditorSelection, StudioMessage, StudioScope
from short_drama.studio_runtime import StudioAgentRunner


class SelectionLanguageClient:
    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        del instructions, image_urls, max_output_tokens
        self.inputs.append(json.loads(input_text))
        return json.dumps({"summary": "已解释当前选择范围。", "goal_summary": "只解释选择，不修改作品。",
                           "goal_complete": True, "steps": []}, ensure_ascii=False)


class QueuedSelectionRunner(StudioAgentRunner):
    def _launch(self, job_id: str) -> None:
        del job_id


def selection(anchor: str, offset: float) -> dict[str, Any]:
    return {
        "timeline_id": "local-timeline", "editorial_revision": 38,
        "coordinate_space": "SHOT_LOCAL", "film_start_seconds": offset,
        "anchor_clip_id": anchor, "focus": "subtitle", "subtitle_ids": ["cue-1"],
        "playhead_seconds": 1.25, "in_point_seconds": .5, "out_point_seconds": 1.8,
        "clips": [{"id": "clip-1", "asset_id": "asset-1", "type": "video", "source_shot_id": "shot-1",
                   "source_take_id": "take-1", "track_index": 0, "start_seconds": 0, "duration_seconds": 2,
                   "source_in_seconds": 4, "source_window_start_seconds": 4, "source_window_end_seconds": 8,
                   "speed": 2, "reversed": True}],
        "gap": None,
    }


def service_for_test(tmp_path: Path) -> tuple[ShortDramaService, str, SelectionLanguageClient]:
    language = SelectionLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
        language_provider_factory=lambda _key, _url: language)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    service.studio_runtime = StudioAgentRunner(service, retry_delays=())
    return service, project_id, language


def wait_completed(service: ShortDramaService, job_id: str) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        job = next(item for item in service.repository.list_studio_jobs() if item.id == job_id)
        thread = service.studio_runtime.threads.get(job_id)
        if job.status == "completed" and (thread is None or not thread.is_alive()):
            return
        assert job.status != "failed", job.error
        threading.Event().wait(.015)
    pytest.fail("Selection handoff did not complete")


def test_old_scope_remains_compatible_and_invalid_time_range_is_specific() -> None:
    assert StudioMessage.model_validate({"text": "改这个镜头", "scope": {"kind": "shot", "id": "shot-1"}}).scope.editor_selection is None
    value = selection("anchor-b", 30)
    value["gap"] = {"track_index": 1, "start_seconds": 2, "end_seconds": 1}
    with pytest.raises(ValidationError, match="end_seconds must be after start_seconds"):
        StudioEditorSelection.model_validate(value)
    value["gap"] = None
    value["out_point_seconds"] = .1
    with pytest.raises(ValidationError, match="out_point_seconds must not precede"):
        StudioEditorSelection.model_validate(value)


def test_http_selection_and_followup_survive_persistence_and_model_handoff(tmp_path: Path) -> None:
    service, project_id, language = service_for_test(tmp_path)
    before = service.get_project(project_id).document.model_dump(mode="json")
    service.studio_runtime = QueuedSelectionRunner(service, retry_delays=())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    first = {"kind": "shot", "id": "shot-1", "label": "镜头 1 · 成片10秒处", "editor_selection": selection("anchor-a", 10)}
    followup = {**first, "editor_selection": selection("anchor-b", 30)}
    endpoint = f"/api/short-drama/studio/projects/{project_id}/messages"
    with TestClient(app) as client:
        initial = client.post(endpoint, json={"text": "解释这处字幕，不修改作品。", "scope": first})
        assert initial.status_code == 200, initial.text
        later = client.post(endpoint, json={"text": "补充：请解释这一次使用位置。", "scope": followup})
        assert later.status_code == 200, later.text
        job_id = initial.json()["id"]
        assert later.json()["id"] == job_id
    # Re-open the repository rather than relying on in-memory request objects.
    reloaded = ShortDramaRepository(service.repository.database_path).list_studio_jobs(project_id)[0]
    assert reloaded.scope.editor_selection is not None
    assert reloaded.scope.editor_selection.anchor_clip_id == "anchor-a"
    assert reloaded.pending_messages[0].scope.editor_selection is not None
    assert reloaded.pending_messages[0].scope.editor_selection.model_dump(mode="json") == followup["editor_selection"]
    service.studio_runtime = StudioAgentRunner(service, retry_delays=())
    service.studio_runtime.resume_after_restart()
    wait_completed(service, job_id)
    saved = service.repository.list_studio_jobs(project_id)[0]
    assert not saved.pending_messages
    assert saved.run_id is not None
    run = service.agent_runtime.store.get_run(saved.run_id)
    assert run is not None
    original_scope = json.loads(run.objective.split("用户选定范围：", 1)[1])
    assert original_scope["editor_selection"] == first["editor_selection"]
    instructions = language.inputs[0]["goal"]["latest_user_instructions"]
    actual_followup = json.loads(next(item for item in instructions if "作用范围：" in item).split("作用范围：", 1)[1])
    assert actual_followup["editor_selection"] == followup["editor_selection"]
    assert service.get_project(project_id).document.model_dump(mode="json") == before


def test_first_request_selection_is_visible_to_language_model(tmp_path: Path) -> None:
    service, project_id, language = service_for_test(tmp_path)
    chosen = selection("anchor-b", 30)
    scope = StudioScope.model_validate({"kind": "shot", "id": "shot-1", "editor_selection": chosen})
    job = service.studio_runtime.send(project_id, StudioMessage(text="只说明这处字幕选择，不修改。", scope=scope))
    wait_completed(service, job.id)
    assert language.inputs
    serialized = json.dumps(language.inputs[0], ensure_ascii=False)
    assert "anchor-b" in serialized and "SHOT_LOCAL" in serialized and "cue-1" in serialized
    assert "source_window_start_seconds" in serialized and "editorial_revision" in serialized
