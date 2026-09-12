"""Replay Fresh4's persisted broad read followed by scene/spec saves, offline."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from short_drama.agent_runtime import AgentRuntimeStore, WorkOrder
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.models import ProjectDocument
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_scene_write_context import _turn


REAL: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/fresh4_project_read.json").read_text())
PID: str = REAL["provenance"]["project_id"]
READS = frozenset({"project.get", "artifact.read_current", "agent.read_context"})
WRITES = frozenset({"script.save_scene", "script.revise_lines", "artifact.create_production_spec", "artifact.replace_production_spec"})


def _state(work: WorkOrder) -> dict[str, Any]:
    return _specialist_working_set(work, [], project_id=PID,
        read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode="json")


def _project_read(state: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in state["read_observations"] if item["tool_name"] == "project.get")


def _seed(tmp_path: Path) -> ShortDramaService:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    service.repository.save_project(ProjectDocument.model_validate(REAL["turns"][0]["result"]["output"]["document"]))
    return service


def test_exact_scene_save_retires_stale_broad_body_in_restarted_worker_input(tmp_path: Path) -> None:
    service = _seed(tmp_path)
    store, work, context = runtime_work(tmp_path / "agent.sqlite3")
    context.project_id = PID
    work.specialist_turns = [SpecialistTurnRecord.model_validate(item) for item in REAL["turns"][:2]]
    work.loaded_tool_schema_ids = ["project.get"]
    store.save_work_order(work)
    original = deepcopy(work.specialist_turns)
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({"tool_name": "project.get", "arguments": {"project_id": PID}, "reason": "Continue from saved current scene."})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt="Continue the task."))
    state = client.payloads[0]["working_set"]
    observed = _project_read(state)
    doc = observed["result"]["output"]["document"]
    source_doc = REAL["turns"][0]["result"]["output"]["document"]
    assert "script_versions" not in doc and "current_script_version_id" not in doc["project"]
    assert doc["bible_versions"] == source_doc["bible_versions"]
    assert [a["id"] for a in doc["assets"]] == [a["id"] for a in source_doc["assets"]]
    assert doc["project"]["name"] == source_doc["project"]["name"]
    assert observed["source_sequence"] == 3 and observed["request_identifiers"] == {"project_id": PID}
    scene = next(item["state"] for item in state["entities"] if item["kind"] == "scene")
    saved = REAL["turns"][1]["result"]["output"]
    assert scene == {**saved["saved_scenes"][0], "script_ref": saved["script_ref"]}
    assert len(scene["scene"]["screenplay_text"]) == 674
    assert reopened.get_work_order(work.id).specialist_turns == original


@pytest.mark.parametrize("target", [55, None])
def test_actual_spec_save_returns_saved_settings_and_replaces_stale_current_fields(tmp_path: Path, target: int | None) -> None:
    service = _seed(tmp_path)
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    save = deepcopy(REAL["turns"][1]["arguments"])
    result = service.tool_registry.invoke("script.save_scene", save)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    args = deepcopy(REAL["turns"][2]["arguments"])
    args["request"]["spec"]["duration_contract"]["target_seconds"] = target
    args["request"]["spec"]["target_duration_seconds"] = target
    result = service.tool_registry.invoke("artifact.create_production_spec", args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    project = service.get_project(PID).document.project
    expected = {"id": PID, "language": project.language, "aspect_ratio": project.aspect_ratio,
        "target_duration_seconds": project.target_duration_seconds}
    assert result.output["project_settings"] == expected
    assert project.target_duration_seconds == (55 if target is not None else 300)
    assert project.name == REAL["turns"][0]["result"]["output"]["document"]["project"]["name"]
    work.specialist_turns = [SpecialistTurnRecord.model_validate(item) for item in REAL["turns"][:2]]
    work.specialist_turns.append(_turn("artifact.create_production_spec", args, result, 5))
    state = _state(work)
    settings = next(item for item in state["entities"] if item["kind"] == "project_settings")
    assert settings["state"] == expected and settings["source_sequence"] == 5
    header = _project_read(state)["result"]["output"]["document"]["project"]
    assert not {"language", "aspect_ratio", "target_duration_seconds", "updated_at"} & header.keys()
    # A subsequent actual replace updates the same compact entity, then a fresh
    # project read becomes the sole body again. No past snapshot is relabelled.
    replacement = deepcopy(args)
    replacement["request"]["base_version_id"] = result.output["version"]["id"]
    replacement["request"]["spec"]["language"] = "en-US"
    replaced = service.tool_registry.invoke("artifact.replace_production_spec", replacement)
    assert replaced.status == ToolCallStatus.SUCCEEDED, replaced.error
    work.specialist_turns.append(_turn("artifact.replace_production_spec", replacement, replaced, 6))
    assert next(item["state"] for item in _state(work)["entities"] if item["kind"] == "project_settings")["language"] == "en-US"
    readargs = {"project_id": PID}
    work.specialist_turns.append(_turn("project.get", readargs, service.tool_registry.invoke("project.get", readargs), 7))
    assert not any(item["kind"] == "project_settings" for item in _state(work)["entities"])


@pytest.mark.parametrize("mode", ["failed", "other_project", "unrelated", "legacy_spec", "unknown_script"])
def test_history_and_unaffected_project_domains_are_not_rewritten(tmp_path: Path, mode: str) -> None:
    _store, work, _context = runtime_work(tmp_path / "agent.sqlite3")
    read = SpecialistTurnRecord.model_validate(REAL["turns"][0])
    write = SpecialistTurnRecord.model_validate(REAL["turns"][1 if mode != "legacy_spec" else 2])
    if mode == "failed":
        write.result = {"status": "FAILED", "error": {"message": "save rejected"}}
    elif mode == "other_project":
        write.arguments["project_id"] = "different-project"
    elif mode == "unrelated":
        write.tool_name = "unrelated.write"
        write.result = {"status": "SUCCEEDED", "output": {"saved": True}}
    elif mode == "unknown_script":
        write.tool_name = "script.revise_lines"
        write.result = {"status": "SUCCEEDED", "artifact_refs": [{"project_id": PID,
            "artifact_type": "script_version", "artifact_id": "new-script", "version": 5}]}
    page = SpecialistTurnRecord(id="historical-page", sequence=2, tool_name="agent.read_context",
        arguments={"project_id": PID, "snapshot_id": "original-history", "pointer": "/document/script_versions", "offset": 0, "limit": 100},
        reason="Read explicitly selected history.", status=SpecialistTurnStatus.COMPLETED,
        result={"status": "SUCCEEDED", "output": {"content": "old body intentionally requested"}})
    work.specialist_turns = [page, read]
    before_state = _state(work)
    before = before_state["latest_handoff"]["result"]["output"]["document"]
    before_history = next(item for item in before_state["read_observations"] if item["tool_name"] == "agent.read_context")
    work.specialist_turns.append(write)
    original = deepcopy(work.specialist_turns)
    state = _state(work)
    doc = _project_read(state)["result"]["output"]["document"]
    if mode in {"failed", "other_project", "unrelated"}:
        assert doc == before
    elif mode == "legacy_spec":
        assert "target_duration_seconds" not in doc["project"]
        assert not any(item["kind"] == "project_settings" for item in state["entities"])
    else:
        assert "script_versions" not in doc  # no assumed survival of other scenes
    historical = next(item for item in state["read_observations"] if item["tool_name"] == "agent.read_context")
    assert historical == before_history
    assert work.specialist_turns == original
