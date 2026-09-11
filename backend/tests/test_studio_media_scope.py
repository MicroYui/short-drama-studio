"""Concrete file selection reaches real Runner inputs without adopting media."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.repository import ShortDramaRepository
from short_drama.studio_models import StudioMessage, StudioScope
from short_drama.studio_runtime import StudioAgentRunner
from tests.test_studio_editor_selection import QueuedSelectionRunner, service_for_test, wait_completed


@pytest.mark.parametrize("scope", [
    StudioScope(kind="asset", id="paper", label="情况说明｜仅复核签名", asset_version_id="paper-single"),
    StudioScope(kind="shot", id="shot-1", label="镜头 1 的源视频", take_id="take-specific"),
])
def test_selected_media_reaches_first_language_input_without_content_changes(tmp_path: Path, scope: StudioScope) -> None:
    service, project_id, language = service_for_test(tmp_path)
    before = service.get_project(project_id).document.model_dump(mode="json")
    job = service.studio_runtime.send(project_id, StudioMessage(text="只解释这份素材，不修改。", scope=scope))
    wait_completed(service, job.id)
    stored = ShortDramaRepository(service.repository.database_path).list_studio_jobs(project_id)[0]
    assert stored.scope == scope
    request = json.dumps(language.inputs[0], ensure_ascii=False)
    assert (scope.asset_version_id or scope.take_id or "missing") in request
    assert service.get_project(project_id).document.model_dump(mode="json") == before


def test_media_followup_survives_restart_and_keeps_each_selected_state(tmp_path: Path) -> None:
    service, project_id, language = service_for_test(tmp_path)
    before = service.get_project(project_id).document.model_dump(mode="json")
    service.studio_runtime = QueuedSelectionRunner(service, retry_delays=())
    first = StudioScope(kind="asset", id="paper", label="双空白", asset_version_id="paper-blank")
    later = StudioScope(kind="asset", id="paper", label="仅复核签名", asset_version_id="paper-single")
    job = service.studio_runtime.send(project_id, StudioMessage(text="说明这份纸张。", scope=first))
    service.studio_runtime.send(project_id, StudioMessage(text="请说明后来选中的单签状态，不修改。", scope=later))
    stored = ShortDramaRepository(service.repository.database_path).list_studio_jobs(project_id)[0]
    assert stored.scope.asset_version_id == "paper-blank"
    assert stored.pending_messages[0].scope.asset_version_id == "paper-single"
    service.studio_runtime = StudioAgentRunner(service, retry_delays=())
    service.studio_runtime.resume_after_restart()
    wait_completed(service, job.id)
    instructions = language.inputs[0]["goal"]["latest_user_instructions"]
    actual = json.loads(next(item for item in instructions if "作用范围：" in item).split("作用范围：", 1)[1])
    assert actual["asset_version_id"] == "paper-single"
    assert service.get_project(project_id).document.model_dump(mode="json") == before


def test_old_scope_without_media_selection_still_round_trips() -> None:
    scope = StudioScope.model_validate({"kind": "asset", "id": "paper"})
    assert scope.asset_version_id is None and scope.take_id is None
    assert StudioScope.model_validate_json(scope.model_dump_json()) == scope
