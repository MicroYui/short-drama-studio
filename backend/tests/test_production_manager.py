"""Agent-first Production Manager integration tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

from _routes.short_drama import router as short_drama_router
from short_drama.agent_runtime import (
    ActionKind,
    ActionStatus,
    AgentResultStatus,
    AgentRunStatus,
    ArtifactRef,
    DelegationStatus,
    PlanStep,
    SpecialistTurnRecord,
    SpecialistTurnStatus,
    WorkOrder,
    WorkOrderNextOwner,
)
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ProductionExecutionTarget,
    CreativeInputImage,
    CreateProjectRequest,
    EnqueueRequest,
    ProductionProfile,
    RunMode,
    SourceMode,
    TaskStatus,
)
from short_drama.production_manager import (
    AgentPlanProposal,
    AgentProjectCreateRequest,
    AgentRunAdvanceRequest,
    AgentRunCreateRequest,
    AgentRunReplanRequest,
    AgentWorkOrderProgressRequest,
    AgentWorkOrderResultRequest,
    ProductionManagerError,
    _compact_specialist_tool_arguments,
    _compact_specialist_tool_result,
    _normalize_specialist_response_payload,
    _specialist_tool_history,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    production_method_skill_catalog,
)
from short_drama.skills.models import SkillProvenanceKind
from short_drama.service import ShortDramaService


class FakePlanningLanguageClient:
    def __init__(self) -> None:
        self.instructions: list[str] = []
        self.inputs: list[str] = []

    def validate_credentials(self) -> None:
        return None

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        self.instructions.append(instructions)
        self.inputs.append(input_text)
        project_id = json.loads(input_text)["context"]["project_id"]
        return json.dumps(
            {
                "summary": (
                    "The Agent chose a direct inspection instead of a fixed pipeline."
                ),
                "steps": [
                    {
                        "id": "inspect-project",
                        "title": "Inspect current project",
                        "kind": "TOOL_CALL",
                        "assigned_agent": "production-manager",
                        "tool_name": "project.get",
                        "instructions": "Read the canonical project.",
                        "arguments": {"project_id": project_id},
                        "depends_on": [],
                        "artifact_refs": [],
                    }
                ],
            }
        )


class FakeSpecialistLanguageClient(FakePlanningLanguageClient):
    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls, max_output_tokens
        self.instructions.append(instructions)
        self.inputs.append(input_text)
        return """{
          "status": "COMPLETED",
          "concise_summary": "The director recommends preserving the eyeline.",
          "created_artifacts": [],
          "updated_artifacts": [],
          "findings": ["The staging is readable."],
          "decisions": ["Preserve the eyeline."],
          "assumptions": [],
          "unresolved_questions": [],
          "evidence_refs": [],
          "recommended_next_actions": ["Continue with the current shot plan."]
        }"""


class SequencedLanguageClient(FakePlanningLanguageClient):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.max_output_token_requests: list[int] = []

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del image_urls
        self.instructions.append(instructions)
        self.inputs.append(input_text)
        self.max_output_token_requests.append(max_output_tokens)
        if not self.responses:
            raise AssertionError("The test language client has no response left")
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "tool_name",
    [
        "shot.begin_storyboard_draft",
        "shot.append_storyboard_ledger_segment",
        "shot.complete_storyboard_ledger",
        "shot.append_storyboard_outline_segment",
        "shot.complete_storyboard_outline",
        "shot.append_storyboard_group",
    ],
)
def test_storyboard_draft_tool_results_keep_only_resumable_progress(
    tool_name: str,
) -> None:
    result: dict[str, JsonValue] = {
        "tool_id": tool_name,
        "status": "SUCCEEDED",
        "output": {
            "progress": {
                "draft_ref": {
                    "project_id": "project-storyboard",
                    "artifact_type": "storyboard_draft",
                    "artifact_id": "storyboard-draft-1",
                    "version": 7,
                },
                "draft_version_id": "storyboard-draft-1-v7",
                "phase": "OUTLINE_BUILDING",
                "scene_node_count": 3,
                "timing_ledger_entry_count": 14,
                "ledger_covered_script_line_count": 14,
                "outlined_timing_entry_count": 9,
                "outlined_script_line_count": 9,
                "committed_clip_group_count": 6,
                "committed_shot_count": 11,
                "covered_script_line_count": 14,
                "next_unplanned_scene_node_id": "scene-node-2",
                "next_unplanned_script_line_id": "line-15",
                "latest_planned_continuity_out": ["Tray remains visible."],
                "latest_scene_node_id": "scene-node-2",
                "latest_clip_group_id": "clip-group-6",
                "latest_shot_ids": ["shot-10", "shot-11"],
                "full_authored_prose": "DO_NOT_REPLAY_THIS_PROSE",
            },
            "debug_payload": "DO_NOT_REPLAY_THIS_OUTPUT",
        },
        "artifact_refs": [
            {
                "project_id": "project-storyboard",
                "artifact_type": "storyboard_draft",
                "artifact_id": "storyboard-draft-1",
                "version": 7,
            }
        ],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result(tool_name, result)
    serialized = json.dumps(compact, ensure_ascii=False)

    assert "DO_NOT_REPLAY" not in serialized
    output = compact["output"]
    assert isinstance(output, dict)
    progress = output["progress"]
    assert isinstance(progress, dict)
    assert progress["draft_version_id"] == "storyboard-draft-1-v7"
    assert progress["phase"] == "OUTLINE_BUILDING"
    assert progress["ledger_covered_script_line_count"] == 14
    assert progress["outlined_timing_entry_count"] == 9
    assert progress["committed_clip_group_count"] == 6
    assert compact["artifact_refs"] == result["artifact_refs"]


def test_storyboard_planning_tool_history_keeps_cursors_not_full_prose() -> None:
    ledger_arguments: dict[str, JsonValue] = {
        "project_id": "project-storyboard",
        "request": {
            "draft_document_id": "storyboard-draft-1",
            "base_version_id": "storyboard-draft-1-v2",
            "entries": [
                {
                    "id": "timing-3",
                    "sequence": 3,
                    "scene_node_id": "scene-node-1",
                    "source_range": {
                        "id": "range-3",
                        "scene_ids": ["scene-1"],
                        "script_line_ids": ["line-3", "line-4"],
                        "description": "DO_NOT_REPLAY_RANGE_PROSE",
                    },
                    "source_text": "DO_NOT_REPLAY_SOURCE_PROSE",
                    "dialogue_units": 10,
                    "allocated_duration_seconds": 6.5,
                    "natural_cut": "DO_NOT_REPLAY_CUT_PROSE",
                    "continuity_out": ["The calculator display reads 14."],
                }
            ],
            "notes": ["DO_NOT_REPLAY_SEGMENT_NOTES"],
            "origin": "AGENT",
        },
    }
    outline_arguments: dict[str, JsonValue] = {
        "project_id": "project-storyboard",
        "request": {
            "draft_document_id": "storyboard-draft-1",
            "base_version_id": "storyboard-draft-1-v5",
            "groups": [
                {
                    "id": "clip-group-2",
                    "title": "DO_NOT_REPLAY_GROUP_TITLE",
                    "scene_node_id": "scene-node-1",
                    "scene_group_sequence": 2,
                    "source_range": {
                        "id": "range-group-2",
                        "scene_ids": ["scene-1"],
                        "script_line_ids": ["line-3", "line-4"],
                        "description": "DO_NOT_REPLAY_GROUP_RANGE",
                    },
                    "timing_entry_ids": ["timing-3"],
                    "duration_seconds": 6.5,
                    "objective": "DO_NOT_REPLAY_OBJECTIVE",
                    "continuity_out": ["The calculator display reads 14."],
                }
            ],
            "notes": ["DO_NOT_REPLAY_OUTLINE_NOTES"],
            "origin": "AGENT",
        },
    }

    compact_ledger = _compact_specialist_tool_arguments(
        "shot.append_storyboard_ledger_segment",
        ledger_arguments,
    )
    compact_outline = _compact_specialist_tool_arguments(
        "shot.append_storyboard_outline_segment",
        outline_arguments,
    )
    serialized = json.dumps(
        [compact_ledger, compact_outline],
        ensure_ascii=False,
    )

    assert "DO_NOT_REPLAY" not in serialized
    ledger_request = compact_ledger["request"]
    assert isinstance(ledger_request, dict)
    ledger_segment = ledger_request["segment"]
    assert isinstance(ledger_segment, dict)
    assert ledger_segment["entry_ids"] == ["timing-3"]
    assert ledger_segment["source_line_ids"] == ["line-3", "line-4"]
    assert ledger_segment["allocated_duration_seconds"] == 6.5
    outline_request = compact_outline["request"]
    assert isinstance(outline_request, dict)
    compact_groups = outline_request["groups"]
    assert isinstance(compact_groups, list)
    first_compact_group = compact_groups[0]
    assert isinstance(first_compact_group, dict)
    assert first_compact_group["id"] == "clip-group-2"
    assert first_compact_group["timing_entry_ids"] == ["timing-3"]


def test_project_get_history_keeps_current_exact_script_without_project_history() -> None:
    current_script: dict[str, JsonValue] = {
        "id": "script-current",
        "version": 4,
        "screenplay_text": "CURRENT_ACTION: 食堂阿姨的勺子掉在台面上。",
        "structured_scenes": [
            {
                "id": "scene-1",
                "screenplay_text": "CURRENT_SCENE_ACTION: 林一指向两个菜盆。",
            }
        ],
        "lines": [{"id": "line-1", "spoken_text": "十四元。"}],
    }
    result: dict[str, JsonValue] = {
        "tool_id": "project.get",
        "status": "SUCCEEDED",
        "output": {
            "document": {
                "project": {
                    "id": "project-storyboard",
                    "current_script_version_id": "script-current",
                    "current_bible_version_id": "bible-current",
                },
                "script_versions": [
                    {
                        "id": "script-old",
                        "screenplay_text": "OLD_SCRIPT_MUST_DISAPPEAR",
                    },
                    current_script,
                ],
                "bible_versions": [
                    {"id": "bible-old", "characters": ["OLD_BIBLE"]},
                    {"id": "bible-current", "characters": ["CURRENT_BIBLE"]},
                ],
                "assets": [{"id": "asset-heavy", "payload": "HEAVY_ASSET"}],
                "shots": [{"id": "shot-heavy", "visual": "HEAVY_SHOT"}],
                "production_documents": [
                    {
                        "id": "production-spec-1",
                        "kind": "production.spec",
                        "title": "Spec",
                        "current_version_id": "production-spec-1-v2",
                        "versions": [
                            {
                                "id": "production-spec-1-v1",
                                "version": 1,
                                "payload": {"large": "OLD_PRODUCTION_PAYLOAD"},
                            },
                            {
                                "id": "production-spec-1-v2",
                                "version": 2,
                                "origin": "AGENT",
                                "created_at": "2026-09-05T00:00:00Z",
                                "payload": {"large": "CURRENT_PRODUCTION_PAYLOAD"},
                            },
                        ],
                    }
                ],
                "output_slots": [],
            },
            "tasks": [{"id": "task-heavy", "request": "HEAVY_TASK"}],
            "takes": [{"id": "take-heavy", "media": "HEAVY_TAKE"}],
            "progress": {"total_tasks": 1},
        },
        "artifact_refs": [],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result("project.get", result)
    serialized = json.dumps(compact, ensure_ascii=False)

    assert "CURRENT_ACTION" in serialized
    assert "CURRENT_SCENE_ACTION" in serialized
    assert "CURRENT_BIBLE" in serialized
    assert "OLD_SCRIPT_MUST_DISAPPEAR" not in serialized
    assert "OLD_BIBLE" not in serialized
    assert "HEAVY_ASSET" not in serialized
    assert "HEAVY_SHOT" not in serialized
    assert "HEAVY_TASK" not in serialized
    assert "HEAVY_TAKE" not in serialized
    assert "PRODUCTION_PAYLOAD" not in serialized


def test_specialist_history_replays_only_latest_exact_project_read() -> None:
    def project_result(marker: str) -> dict[str, JsonValue]:
        return {
            "tool_id": "project.get",
            "status": "SUCCEEDED",
            "output": {
                "document": {
                    "project": {
                        "id": "project-storyboard",
                        "current_script_version_id": "script-current",
                        "current_bible_version_id": "bible-current",
                    },
                    "script_versions": [
                        {
                            "id": "script-current",
                            "screenplay_text": marker,
                            "structured_scenes": [
                                {"id": "scene-1", "screenplay_text": marker}
                            ],
                        }
                    ],
                    "bible_versions": [
                        {"id": "bible-current", "notes": [f"{marker}_BIBLE"]}
                    ],
                    "production_documents": [],
                    "output_slots": [],
                    "assets": [{"id": "asset-heavy", "payload": marker}],
                },
                "tasks": [{"id": "task-heavy", "request": marker}],
                "takes": [{"id": "take-heavy", "media": marker}],
            },
            "artifact_refs": [],
            "warnings": [],
            "error": None,
        }

    work_order = WorkOrder(
        id="work-order-project-history",
        parent_run_id="run-storyboard",
        sender="production-manager",
        recipient_role="storyboard-planner",
        objective="Read current project truth.",
        why_now="The planner needs the current exact Script and Bible.",
        specialist_turns=[
            SpecialistTurnRecord(
                id="turn-project-old",
                sequence=1,
                tool_name="project.get",
                arguments={"project_id": "project-storyboard"},
                reason="Read the initial project state.",
                status=SpecialistTurnStatus.COMPLETED,
                result=project_result("OLD_PROJECT_EXACT_PROSE"),
            ),
            SpecialistTurnRecord(
                id="turn-project-latest",
                sequence=2,
                tool_name="project.get",
                arguments={"project_id": "project-storyboard"},
                reason="Refresh the project after the script changed.",
                status=SpecialistTurnStatus.COMPLETED,
                result=project_result("LATEST_PROJECT_EXACT_PROSE"),
            ),
        ],
    )

    history = _specialist_tool_history(work_order)
    serialized = json.dumps(history, ensure_ascii=False)

    assert "OLD_PROJECT_EXACT_PROSE" not in serialized
    assert serialized.count("LATEST_PROJECT_EXACT_PROSE") == 3
    first_result = history[0]["result"]
    assert isinstance(first_result, dict)
    first_output = first_result["output"]
    assert isinstance(first_output, dict)
    first_document = first_output["document"]
    assert isinstance(first_document, dict)
    assert first_document["script_versions"] == []
    assert first_document["bible_versions"] == []


def test_production_document_read_keeps_only_selected_exact_version_payload() -> None:
    result: dict[str, JsonValue] = {
        "tool_id": "artifact.get_production_document",
        "status": "SUCCEEDED",
        "output": {
            "document": {
                "id": "storyboard-draft-1",
                "project_id": "project-storyboard",
                "kind": "storyboard.draft",
                "title": "Storyboard draft",
                "current_version_id": "storyboard-draft-1-v9",
                "versions": [
                    {
                        "id": "storyboard-draft-1-v1",
                        "version": 1,
                        "payload": {"notes": ["OLD_DRAFT_PAYLOAD_1"]},
                    },
                    {
                        "id": "storyboard-draft-1-v8",
                        "version": 8,
                        "payload": {"notes": ["OLD_DRAFT_PAYLOAD_8"]},
                    },
                    {
                        "id": "storyboard-draft-1-v9",
                        "version": 9,
                        "payload": {"notes": ["SELECTED_DRAFT_PAYLOAD"]},
                    },
                ],
            },
            "version": {
                "id": "storyboard-draft-1-v9",
                "version": 9,
                "parent_version_id": "storyboard-draft-1-v8",
                "payload": {
                    "phase": "OUTLINE_BUILDING",
                    "timing_ledger": [
                        {"id": "timing-1", "source_text": "SELECTED_DRAFT_PAYLOAD"}
                    ],
                },
                "source_artifact_refs": [],
                "origin": "AGENT",
                "created_at": "2026-09-05T00:00:00Z",
            },
        },
        "artifact_refs": [],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result(
        "artifact.get_production_document",
        result,
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert "OLD_DRAFT_PAYLOAD" not in serialized
    assert serialized.count("SELECTED_DRAFT_PAYLOAD") == 1
    output = compact["output"]
    assert isinstance(output, dict)
    document = output["document"]
    assert isinstance(document, dict)
    assert "versions" not in document
    version = output["version"]
    assert isinstance(version, dict)
    assert version["id"] == "storyboard-draft-1-v9"
    payload = version["payload"]
    assert isinstance(payload, dict)
    assert payload["phase"] == "OUTLINE_BUILDING"


def test_specialist_history_keeps_only_latest_exact_production_document_read() -> None:
    def production_document_result(
        *,
        version_number: int,
        marker: str,
        phase: str,
    ) -> dict[str, JsonValue]:
        version_id = f"storyboard-draft-1-v{version_number}"
        return {
            "tool_id": "artifact.get_production_document",
            "status": "SUCCEEDED",
            "output": {
                "document": {
                    "id": "storyboard-draft-1",
                    "project_id": "project-storyboard",
                    "kind": "storyboard.draft",
                    "title": "Storyboard draft",
                    "current_version_id": version_id,
                    "versions": [
                        {
                            "id": "storyboard-draft-1-v1",
                            "version": 1,
                            "payload": {"notes": ["HISTORICAL_DOCUMENT_PROSE"]},
                        },
                        {
                            "id": version_id,
                            "version": version_number,
                            "payload": {"notes": [marker]},
                        },
                    ],
                },
                "version": {
                    "id": version_id,
                    "version": version_number,
                    "payload": {
                        "phase": phase,
                        "script_version_id": "script-v4",
                        "bible_version_id": "bible-v2",
                        "timing_ledger": [
                            {"id": f"timing-{version_number}", "source_text": marker}
                        ],
                    },
                    "source_artifact_refs": [],
                    "origin": "AGENT",
                    "created_at": "2026-09-05T00:00:00Z",
                },
            },
            "artifact_refs": [],
            "warnings": [],
            "error": None,
        }

    work_order = WorkOrder(
        id="work-order-storyboard",
        parent_run_id="run-storyboard",
        sender="production-manager",
        recipient_role="storyboard-planner",
        objective="Compile the storyboard in semantic stages.",
        why_now="The exact Script and Bible are ready.",
        specialist_turns=[
            SpecialistTurnRecord(
                id="turn-ledger-read",
                sequence=1,
                tool_name="artifact.get_production_document",
                arguments={
                    "project_id": "project-storyboard",
                    "document_id": "storyboard-draft-1",
                    "version_id": "storyboard-draft-1-v4",
                },
                reason="Read the sealed ledger.",
                status=SpecialistTurnStatus.COMPLETED,
                result=production_document_result(
                    version_number=4,
                    marker="SEALED_LEDGER_EXACT_PROSE",
                    phase="OUTLINE_BUILDING",
                ),
            ),
            SpecialistTurnRecord(
                id="turn-outline-read",
                sequence=2,
                tool_name="artifact.get_production_document",
                arguments={
                    "project_id": "project-storyboard",
                    "document_id": "storyboard-draft-1",
                    "version_id": "storyboard-draft-1-v8",
                },
                reason="Read the complete outline.",
                status=SpecialistTurnStatus.COMPLETED,
                result=production_document_result(
                    version_number=8,
                    marker="LATEST_OUTLINE_EXACT_PROSE",
                    phase="GROUP_AUTHORING",
                ),
            ),
        ],
    )

    history = _specialist_tool_history(work_order)
    serialized = json.dumps(history, ensure_ascii=False)

    assert "HISTORICAL_DOCUMENT_PROSE" not in serialized
    assert "SEALED_LEDGER_EXACT_PROSE" not in serialized
    assert serialized.count("LATEST_OUTLINE_EXACT_PROSE") == 1
    assert '"versions"' not in serialized
    first_result = history[0]["result"]
    assert isinstance(first_result, dict)
    first_output = first_result["output"]
    assert isinstance(first_output, dict)
    first_version = first_output["version"]
    assert isinstance(first_version, dict)
    assert "payload" not in first_version
    first_summary = first_version["payload_summary"]
    assert isinstance(first_summary, dict)
    assert first_summary["phase"] == "OUTLINE_BUILDING"
    latest_result = history[1]["result"]
    assert isinstance(latest_result, dict)
    latest_output = latest_result["output"]
    assert isinstance(latest_output, dict)
    latest_version = latest_output["version"]
    assert isinstance(latest_version, dict)
    latest_payload = latest_version["payload"]
    assert isinstance(latest_payload, dict)
    assert latest_payload["phase"] == "GROUP_AUTHORING"


def test_finalize_storyboard_tool_result_keeps_exact_canonical_handoff_not_prose() -> None:
    canonical_ref: dict[str, JsonValue] = {
        "project_id": "project-storyboard",
        "artifact_type": "storyboard",
        "artifact_id": "storyboard-1",
        "version": 3,
    }
    result: dict[str, JsonValue] = {
        "tool_id": "shot.finalize_storyboard",
        "status": "SUCCEEDED",
        "output": {
            "storyboard": {
                "project_id": "project-storyboard",
                "title": "数学能力倒退一百倍·完整分镜",
                "script_version_id": "script-v4",
                "bible_version_id": "bible-v2",
                "production_spec_document_id": "production-spec-1",
                "production_spec_version_id": "production-spec-1-v1",
                "script_line_ids": ["line-1", "line-2", "line-3"],
                "scene_nodes": [{"id": "scene-node-1"}, {"id": "scene-node-2"}],
                "shots": [
                    {
                        "id": "shot-1",
                        "visual": "DO_NOT_REPLAY_SHOT_VISUAL",
                        "camera": "DO_NOT_REPLAY_CAMERA",
                        "timeline_beats": [
                            {"performance": "DO_NOT_REPLAY_PERFORMANCE"}
                        ],
                    },
                    {"id": "shot-2", "visual": "DO_NOT_REPLAY_SECOND_SHOT"},
                ],
                "clip_groups": [
                    {
                        "id": "clip-group-1",
                        "generation_intent": "DO_NOT_REPLAY_GENERATION_PROMPT",
                    }
                ],
                "computed_duration_seconds": 120.0,
            },
            "document": {
                "id": "storyboard-1",
                "project_id": "project-storyboard",
                "kind": "storyboard",
                "title": "Storyboard",
                "current_version_id": "storyboard-1-v3",
                "versions": [{"payload": {"visual": "DO_NOT_REPLAY_VERSION"}}],
            },
            "version": {
                "id": "storyboard-1-v3",
                "version": 3,
                "parent_version_id": "storyboard-1-v2",
                "payload": {"visual": "DO_NOT_REPLAY_VERSION_PAYLOAD"},
                "source_artifact_refs": [],
                "origin": "AGENT",
                "created_at": "2026-09-05T12:00:00Z",
            },
            "output_slot": {
                "id": "storyboard.current",
                "revision": 3,
                "current_artifact_ref": canonical_ref,
                "previous_artifact_refs": [],
                "revision_history": [
                    {"invalidation_reason": "DO_NOT_REPLAY_SLOT_HISTORY"}
                ],
                "producer_agent_run_id": "agent-run-1",
                "producer_work_order_id": "work-order-1",
                "updated_at": "2026-09-05T12:00:00Z",
            },
        },
        "artifact_refs": [
            canonical_ref,
            {
                "project_id": "project-storyboard",
                "artifact_type": "shot",
                "artifact_id": "shot-1",
                "version": 1,
            },
        ],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result(
        "shot.finalize_storyboard",
        result,
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert "DO_NOT_REPLAY" not in serialized
    assert compact["artifact_refs"] == result["artifact_refs"]
    output = compact["output"]
    assert isinstance(output, dict)
    assert output["canonical_storyboard_ref"] == canonical_ref
    summary = output["storyboard_summary"]
    assert isinstance(summary, dict)
    assert summary["scene_count"] == 2
    assert summary["clip_group_count"] == 1
    assert summary["shot_count"] == 2
    assert summary["script_line_count"] == 3
    assert summary["computed_duration_seconds"] == 120.0
    version = output["version"]
    assert isinstance(version, dict)
    assert version["id"] == "storyboard-1-v3"
    assert version["version"] == 3


def _service(root: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
    )


def _single_tool_plan(
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> AgentPlanProposal:
    return AgentPlanProposal(
        summary=f"Invoke {tool_name}.",
        steps=[
            PlanStep(
                id=tool_name.replace(".", "-"),
                title=f"Invoke {tool_name}",
                kind=ActionKind.TOOL_CALL,
                assigned_agent="production-manager",
                tool_name=tool_name,
                instructions=f"Invoke {tool_name}.",
                arguments=arguments,
            )
        ],
    )


def _start_pending_delegation(
    service: ShortDramaService,
    project_id: str,
    *,
    strict_completion_contract: bool = False,
) -> tuple[str, str, str]:
    delegation_arguments: dict[str, JsonValue] = {}
    if strict_completion_contract:
        delegation_arguments = {
            "acceptance_criteria": ["Return a canonical delivery field."],
            "expected_output_schema": {
                "type": "object",
                "required": ["delivery"],
            },
        }
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Obtain one bounded specialist handoff.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one current-plan task.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate-current-task",
                        title="Delegate current task",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the current scene staging.",
                        arguments=delegation_arguments,
                    )
                ],
            ),
        ),
    )
    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    return (
        run.snapshot.run.id,
        advanced.created_work_order_ids[0],
        advanced.run.snapshot.actions[0].id,
    )


def _begin_interrupted_delegation_action(
    service: ShortDramaService,
    project_id: str,
) -> tuple[str, str, str]:
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Recover one interrupted specialist handoff.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one recoverable task.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate-recoverable-task",
                        title="Delegate recoverable task",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the current scene staging.",
                    )
                ],
            ),
        ),
    )
    plan = run.snapshot.current_plan
    assert plan is not None
    action = service.agent_runtime.begin_action(
        run.snapshot.run.id,
        kind=ActionKind.DELEGATION,
        agent_id="scene-director",
        input_summary="Review the current scene staging.",
        plan_revision_id=plan.id,
        plan_step_id="delegate-recoverable-task",
    )
    return run.snapshot.run.id, plan.id, action.id


def test_incomplete_agent_plan_requires_a_next_step() -> None:
    with pytest.raises(
        ValueError,
        match="incomplete Agent plan must contain at least one next step",
    ):
        AgentPlanProposal(summary="No next step", goal_complete=False)


def test_capability_catalog_shares_tools_across_file_backed_skills(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    catalog = service.get_agent_capabilities()
    expected_method_ids = {
        descriptor.id for descriptor in production_method_skill_catalog()
    }

    assert {skill.profile for skill in catalog.skills} == set(ProductionProfile)
    assert {skill.id for skill in catalog.production_methods} == expected_method_ids
    tool_ids = {tool.id for tool in catalog.tools}
    assert {"project.get", "change.preview", "change.apply"} <= tool_ids
    assert all(skill.role_names for skill in catalog.skills)
    assert {skill.provenance for skill in catalog.production_methods} == {
        SkillProvenanceKind.OFFICIAL_ORIGINAL_WITH_ADAPTER,
        SkillProvenanceKind.UPSTREAM_OFFICIAL_WITH_ADAPTER,
        SkillProvenanceKind.UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER,
        SkillProvenanceKind.UPSTREAM_COMMUNITY_WITH_ADAPTER,
    }
    assert all(skill.upstream_sources for skill in catalog.production_methods)


def test_configured_language_agent_authors_the_initial_dynamic_plan(
    tmp_path: Path,
) -> None:
    client = FakePlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect only what this objective needs.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )

    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.steps[0].tool_name == "project.get"
    assert run.snapshot.run.unattended is False
    assert run.snapshot.run.execution_target == ProductionExecutionTarget.REAL_H3
    assert run.snapshot.run.render_export is False
    planner_context = json.loads(client.inputs[0])["context"]
    assert planner_context["unattended"] is False
    assert planner_context["execution_target"] == "REAL_H3"
    assert planner_context["render_export"] is False
    assert "你可以选择、跳过、重排、并行或替换步骤" in client.instructions[0]
    assert "用户已明确给出的值、事实和选择直接采用" in client.instructions[0]
    assert "context.unattended=true" in client.instructions[0]
    assert "用户明确要求在某个工件或内容节点确认、审阅或选择时" in (
        client.instructions[0]
    )
    assert "MiniMax Design 的 drama-series、ad-tvc、mv 是项目级主 Workflow" in (
        client.instructions[0]
    )
    assert "MiniMax-H3 方法是可组合的任务级表达方法" in client.instructions[0]
    assert "计划执行器不会替步骤解析未来输出或做跨步骤模板插值" in (
        client.instructions[0]
    )
    assert "待结果落盘并进入下一次重规划" in client.instructions[0]
    assert "同一 Planner WorkOrder" in client.instructions[0]
    assert "shot.append_storyboard_ledger_segment" in client.instructions[0]
    assert "shot.complete_storyboard_ledger" in client.instructions[0]
    assert "shot.append_storyboard_outline_segment" in client.instructions[0]
    assert "shot.complete_storyboard_outline" in client.instructions[0]
    assert "artifact.get_production_document" in client.instructions[0]
    assert "不能凭进度摘要重建账本" in client.instructions[0]
    assert "若其中没有当前精确 ScriptVersion" in client.instructions[0]
    assert "必须先请求 project.get" in client.instructions[0]
    assert "completes_scene=true" in client.instructions[0]
    assert "4–15 秒整数执行边界" in client.instructions[0]
    assert "在首次封结 ledger 之前" in client.instructions[0]
    assert "allocated_duration_seconds 之和精确为 4–15 秒整数" in (
        client.instructions[0]
    )
    assert "不得先封存账本" in client.instructions[0]
    assert "同一 WorkOrder 内重新 begin 一个草稿分支" in (
        client.instructions[0]
    )
    assert "不得仅为获取 begin 生成的 ID 就结束 WorkOrder" in (
        client.instructions[0]
    )
    assert "不用 continuity 文本逐字相等" in client.instructions[0]
    assert "片段边界只是运行检查点" in client.instructions[0]
    assert "不设片段数量、大小或重试次数" in client.instructions[0]
    assert "在一次模型回答中输出全片账本" in client.instructions[0]
    assert "REVISION_VERIFIER" in client.instructions[0]
    assert "STORY_REPAIR" not in client.instructions[0]
    assert "FINAL_COLD_READ" not in client.instructions[0]
    assert "REVIEW_ROUTER" not in client.instructions[0]
    assert "CRITIC" not in client.instructions[0]
    assert "审核问题分派的通用职责" not in client.instructions[0]
    assert "当前制片技能：AI 短剧（本地格式适配）" in client.instructions[0]
    assert "共享端到端制片流程" in client.instructions[0]
    assert project.id in client.inputs[0]


def test_language_planner_semantically_selects_and_persists_method_skills(
    tmp_path: Path,
) -> None:
    project_service = _service(tmp_path)
    project = project_service.initialize_fixture().document.project
    expected_method_ids = {
        descriptor.id for descriptor in production_method_skill_catalog()
    }
    response = json.dumps(
        {
            "summary": "Use an explicit commercial-film method for the current objective.",
            "selected_method_skill_ids": ["method.minimax_h3.brand_promo_video"],
            "method_selection_rationale": (
                "The objective needs product claims translated into visible proof."
            ),
            "steps": [
                {
                    "id": "inspect-project",
                    "title": "Inspect current project",
                    "kind": "TOOL_CALL",
                    "assigned_agent": "production-manager",
                    "tool_name": "project.get",
                    "instructions": "Read the canonical project.",
                    "arguments": {"project_id": project.id},
                    "depends_on": [],
                    "artifact_refs": [],
                }
            ],
            "goal_complete": False,
        }
    )
    client = SequencedLanguageClient([response])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(objective="Turn a verified product claim into a video."),
    )

    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.selected_method_skill_ids == [
        "method.minimax_h3.brand_promo_video"
    ]
    assert run.snapshot.current_plan.method_selection_rationale is not None
    assert "method.minimax_h3.brand_promo_video" in run.snapshot.run.active_skill_ids
    planner_payload = json.loads(client.inputs[0])
    assert {
        item["id"] for item in planner_payload["available_production_methods"]
    } == expected_method_ids
    assert all(
        set(item) == {
            "id",
            "label",
            "description",
            "supported_roles",
            "user_overridable",
        }
        for item in planner_payload["available_production_methods"]
    )
    assert all(
        not any(
            marker in item["label"]
            for marker in ("官方发布", "官方精选", "社区收录")
        )
        for item in planner_payload["available_production_methods"]
    )
    assert "不得用关键词匹配代替语义判断" in client.instructions[0]


def test_language_planner_loads_only_explicitly_requested_tool_schema(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "requested_tool_schema_ids": ["project.get"],
                    "rationale": "The exact project read contract is needed for this step.",
                }
            ),
            json.dumps(
                {
                    "summary": "Read the canonical project using the disclosed contract.",
                    "steps": [
                        {
                            "id": "inspect-project",
                            "title": "Inspect current project",
                            "kind": "TOOL_CALL",
                            "assigned_agent": "production-manager",
                            "tool_name": "project.get",
                            "instructions": "Read the canonical project.",
                            "arguments": {},
                        }
                    ],
                }
            ),
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(objective="Inspect the current canonical project."),
    )

    assert run.snapshot.current_plan is not None
    assert run.snapshot.current_plan.steps[0].tool_name == "project.get"
    assert len(client.inputs) == 2
    first_payload, second_payload = [json.loads(value) for value in client.inputs]
    assert first_payload["tool_schemas"] == {}
    assert all(
        "input_schema" not in descriptor
        and "output_schema" not in descriptor
        for descriptor in first_payload["available_tools"]
    )
    assert set(second_payload["tool_schemas"]) == {"project.get"}
    assert "input_schema" in second_payload["tool_schemas"]["project.get"]
    assert "output_schema" in second_payload["tool_schemas"]["project.get"]


def test_language_planner_rejects_unknown_tool_schema_request(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "requested_tool_schema_ids": ["tool.not-in-catalog"],
                    "rationale": "This ID must be rejected instead of inferred.",
                }
            )
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(objective="Inspect the current canonical project."),
    )

    assert run.snapshot.current_plan is None
    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert any(
        "requested unknown Tool schemas" in blocker
        for blocker in run.context.blockers
    )


def test_agent_plan_rejects_an_unregistered_method_skill(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(
        ProductionManagerError,
        match="unknown Production Method Skills",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Do not silently invent a method.",
                initial_plan=AgentPlanProposal(
                    summary="Invalid method selection.",
                    selected_method_skill_ids=["method.not-registered"],
                    steps=[
                        PlanStep(
                            id="checkpoint",
                            title="Checkpoint",
                            kind=ActionKind.CHECKPOINT,
                            assigned_agent="production-manager",
                            instructions="This plan must be rejected.",
                        )
                    ],
                ),
            ),
        )


@pytest.mark.parametrize(
    ("target", "present", "absent"),
    [
        (
            ProductionExecutionTarget.MOCK,
            {"video.enqueue", "task.drain", "task.reconcile"},
            {
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
                "route.resolve_h3_submission",
                "route.resolve_h3_not_submitted",
            },
        ),
        (
            ProductionExecutionTarget.REAL_H3,
            {
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
            },
            {
                "video.enqueue",
                "video.retake",
                "video.extend",
                "video.gap_fill",
                "task.drain",
                "task.reconcile",
            },
        ),
        (
            None,
            {"project.get", "video.inspect_take", "route.plan_h3"},
            {
                "video.enqueue",
                "video.retake",
                "video.extend",
                "video.gap_fill",
                "task.drain",
                "task.reconcile",
                "route.enqueue_h3_probe",
                "route.execute_h3_plan",
                "route.list_h3_reconciliation",
                "route.resolve_h3_submission",
                "route.resolve_h3_not_submitted",
            },
        ),
    ],
)
def test_language_planner_catalog_filters_incompatible_generation_tools(
    tmp_path: Path,
    target: ProductionExecutionTarget | None,
    present: set[str],
    absent: set[str],
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "Stop after catalog inspection.",
                    "goal_complete": True,
                    "steps": [
                        {
                            "id": "checkpoint",
                            "title": "Checkpoint",
                            "kind": "CHECKPOINT",
                            "assigned_agent": "production-manager",
                            "instructions": "Record the bounded inspection.",
                        }
                    ],
                }
            )
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the tools available for this target.",
            execution_target=target,
        ),
    )

    tool_ids = {
        tool["id"]
        for tool in json.loads(client.inputs[0])["available_tools"]
    }
    assert present <= tool_ids
    assert tool_ids.isdisjoint(absent)
    assert {"project.get", "video.inspect_take", "route.plan_h3", "task.advance"} <= (
        tool_ids
    )


def test_agent_authored_plan_executes_tools_and_survives_service_restart(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    created = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Read the current project, then checkpoint what should change.",
            initial_plan=AgentPlanProposal(
                summary="Inspect first, then checkpoint.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="read-project",
                        title="Read the canonical project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Load the current project before making a decision.",
                        arguments={"project_id": project.id},
                    ),
                    PlanStep(
                        id="checkpoint",
                        title="Record the next focus",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="The project was read from the canonical store.",
                        depends_on=["read-project"],
                    ),
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        created.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(advanced.executed_action_ids) == 2
    assert advanced.run.snapshot.actions[0].tool_name == "project.get"
    assert advanced.run.snapshot.actions[0].artifact_refs[0].artifact_id == project.id

    reopened = _service(tmp_path)
    resumed = reopened.get_agent_run(created.snapshot.run.id)
    assert resumed.snapshot.run.status == AgentRunStatus.COMPLETED
    assert resumed.snapshot.current_plan is not None
    assert resumed.snapshot.current_plan.steps[0].id == "read-project"


def test_configured_specialist_agent_completes_work_order_round_trip(
    tmp_path: Path,
) -> None:
    client = FakeSpecialistLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get a scene-director decision.",
            initial_plan=AgentPlanProposal(
                summary="Delegate the bounded decision.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate-director",
                        title="Ask scene director",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the first shot's staging.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    response = service.execute_agent_work_order(waiting.created_work_order_ids[0])

    assert response.result.status == AgentResultStatus.COMPLETED
    assert response.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert response.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert response.run.context.decisions[0].statement == "Preserve the eyeline."
    assert "对应角色的 Production Skill" in client.instructions[0]
    assert "用户已明确给出的值、事实和选择直接采用" in client.instructions[0]
    assert "tool_name、reason、arguments" in client.instructions[0]
    assert "context.unattended=true" in client.instructions[0]
    assert "只有确实需要暂停时才返回 PARTIAL" in client.instructions[0]
    assert "begin、ledger 增量、ledger 完成、outline 增量" in (
        client.instructions[0]
    )
    assert "不要仅因为 begin 产生了新 ID" in (
        client.instructions[0]
    )
    assert "不要设固定字数、条数、场次数或调用次数" in (
        client.instructions[0]
    )
    assert "artifact.get_production_document" in client.instructions[0]
    assert "不能凭 Tool 进度摘要" in client.instructions[0]
    assert "第一个 Tool 请求必须是 project.get" in client.instructions[0]
    assert "completes_scene=true" in client.instructions[0]
    assert "4–15 秒整数的未来 H3 生成单元" in client.instructions[0]
    assert "在首次封结 ledger 前" in client.instructions[0]
    assert "allocated_duration_seconds 之和精确为 4–15 秒整数" in (
        client.instructions[0]
    )
    assert "不得先封存账本" in client.instructions[0]
    assert "本 WorkOrder 内再请求一次 shot.begin_storyboard_draft" in (
        client.instructions[0]
    )
    assert "不得仅为重启草稿返回 Leader" in client.instructions[0]
    assert "Tool 结果会回到你手上" in client.instructions[0]
    assert "无需逐字复制" in client.instructions[0]
    assert "REVISION_VERIFIER，必须先用" in client.instructions[0]
    specialist_context = json.loads(client.inputs[0])["context"]
    assert specialist_context["unattended"] is False
    assert "scene-director" in client.inputs[0]

    completed = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert completed.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_language_specialist_receives_append_only_work_order_progress(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "requested_tool_schema_ids": ["project.get"],
                    "rationale": "The specialist needs the exact project read contract.",
                }
            ),
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The staged eyeline is readable.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": ["The staging is readable."],
                    "decisions": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                    "evidence_refs": [],
                    "recommended_next_actions": [],
                }
            ),
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    _run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
    )
    service.append_agent_work_order_progress(
        work_order_id,
        AgentWorkOrderProgressRequest(
            author="scene-director",
            summary="Loaded the current staging and marked the eyeline for review.",
        ),
    )

    response = service.execute_agent_work_order(work_order_id)

    assert response.result.status == AgentResultStatus.COMPLETED
    assert len(client.inputs) == 2
    first_payload, second_payload = [json.loads(value) for value in client.inputs]
    progress_entries = first_payload["progress_entries"]
    assert [entry["sequence"] for entry in progress_entries] == [1, 2, 3]
    assert [entry["summary"] for entry in progress_entries] == [
        "WorkOrder created: Review the current scene staging.",
        "Loaded the current staging and marked the eyeline for review.",
        "Specialist work started or resumed.",
    ]
    assert second_payload["progress_entries"] == progress_entries
    assert first_payload["tool_schemas"] == {}
    assert set(first_payload["response_schemas"]) == {
        "final_result",
        "tool_request",
        "tool_schema_request",
    }
    assert list(
        first_payload["response_schemas"]["tool_request"]["properties"]
    ) == ["tool_name", "reason", "arguments"]
    artifact_ref_schema = first_payload["response_schemas"]["final_result"]["$defs"][
        "ArtifactRef"
    ]
    assert artifact_ref_schema["additionalProperties"] is False
    assert client.max_output_token_requests == [10000, 10000]
    assert all(
        "input_schema" not in descriptor
        and "output_schema" not in descriptor
        for descriptor in first_payload["available_tools"]
    )
    assert set(second_payload["tool_schemas"]) == {"project.get"}
    persisted_progress = service.list_agent_work_order_progress(work_order_id).entries
    assert [entry.summary for entry in persisted_progress[:3]] == [
        item["summary"] for item in progress_entries
    ]
    assert [entry.sequence for entry in persisted_progress] == [1, 2, 3, 4]


def test_invalid_specialist_response_protocol_returns_to_same_worker(
    tmp_path: Path,
) -> None:
    corrected_result = {
        "status": "COMPLETED",
        "concise_summary": "The director preserved the intended staging.",
        "created_artifacts": [],
        "updated_artifacts": [],
        "findings": ["The staging is readable."],
        "decisions": [],
        "assumptions": [],
        "unresolved_questions": [],
        "evidence_refs": [],
        "recommended_next_actions": [],
    }
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "requested_tool_schema_ids": ["project.get"],
                    "rationale": "Load the exact read contract before deciding.",
                }
            ),
            json.dumps(corrected_result) + "\n" + json.dumps({"extra": True}),
            json.dumps(corrected_result),
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, work_order_id, action_id = _start_pending_delegation(
        service,
        project_id,
    )
    original_plan_id = service.get_agent_run(run_id).snapshot.run.current_plan_revision_id

    partial = service.execute_agent_work_order(work_order_id)

    assert partial.result.status == AgentResultStatus.PARTIAL
    failed = service.agent_runtime_store.get_work_order(work_order_id)
    assert failed is not None
    assert failed.status == DelegationStatus.NEEDS_ATTENTION
    assert failed.next_owner == WorkOrderNextOwner.WORKER_DECISION
    assert failed.pending_protocol_feedback is not None
    assert failed.pending_protocol_feedback.loaded_tool_schema_ids == ["project.get"]
    assert set(failed.pending_protocol_feedback.validation_issues) == {
        "agent_result",
        "tool_request",
        "tool_schema_request",
    }
    assert "extra" in failed.pending_protocol_feedback.rejected_response
    action = service.agent_runtime_store.get_action(action_id)
    assert action is not None
    assert action.status == ActionStatus.RUNNING
    assert (
        service.get_agent_run(run_id).snapshot.run.current_plan_revision_id
        == original_plan_id
    )

    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    completed = service.execute_agent_work_order(work_order_id)

    assert completed.result.status == AgentResultStatus.COMPLETED
    corrected = service.agent_runtime_store.get_work_order(work_order_id)
    assert corrected is not None
    assert corrected.status == DelegationStatus.COMPLETED
    assert corrected.pending_protocol_feedback is None
    assert len(client.inputs) == 3
    correction_payload = json.loads(client.inputs[2])
    assert set(correction_payload["tool_schemas"]) == {"project.get"}
    protocol_feedback = correction_payload["response_protocol_feedback"]
    assert protocol_feedback["loaded_tool_schema_ids"] == ["project.get"]
    assert "extra" in protocol_feedback["rejected_response"]
    assert "不是剧情、创意或任务拆分意见" in client.instructions[2]
    progress = service.list_agent_work_order_progress(work_order_id).entries
    validator_entries = [
        entry for entry in progress if entry.author == "specialist-response-validator"
    ]
    assert len(validator_entries) == 2
    assert "same Worker" in validator_entries[-1].summary


def test_specialist_accepts_top_level_json_field_continuation(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    _run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
    )
    closed_object = json.dumps(
        {
            "tool_name": "project.get",
            "arguments": {"project_id": project_id},
        }
    )
    client.responses = [
        closed_object + ',"reason":"Read the canonical project before deciding."}'
    ]

    requested = service.execute_agent_work_order(work_order_id)

    assert requested.result.status == AgentResultStatus.PARTIAL
    work_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert work_order is not None
    assert work_order.pending_protocol_feedback is None
    assert work_order.next_owner == WorkOrderNextOwner.WORKER_TOOL
    assert len(work_order.specialist_turns) == 1
    turn = work_order.specialist_turns[0]
    assert turn.tool_name == "project.get"
    assert turn.arguments == {"project_id": project_id}
    assert turn.reason == "Read the canonical project before deciding."


@pytest.mark.parametrize(
    "payload",
    [
        (
            '{"tool_name":"project.get","arguments":{}}\n'
            '{"reason":"This is a second JSON object."}'
        ),
        '{"tool_name":"project.get","arguments":{}} trailing prose',
        '{"tool_name":"project.get","arguments":{}},"unknown":true}',
        '{"tool_name":"project.get","arguments":{"project_id":',
    ],
)
def test_specialist_json_continuation_recovery_stays_narrow(
    payload: str,
) -> None:
    assert _normalize_specialist_response_payload(payload) == payload


def test_plan_can_be_replaced_with_a_new_parallel_graph(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Revise only the selected scene.",
            initial_plan=AgentPlanProposal(
                summary="Initial idea",
                steps=[
                    PlanStep(
                        id="old-step",
                        title="Old step",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="This plan will be replaced.",
                    )
                ],
            ),
        ),
    )

    replaced = service.replan_agent_run(
        run.snapshot.run.id,
        AgentRunReplanRequest(
            reason="The Agent found two independent inspections.",
            proposed_plan=AgentPlanProposal(
                summary="Inspect story and staging in parallel.",
                steps=[
                    PlanStep(
                        id="story-check",
                        title="Check story impact",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="story-architect",
                        instructions="Review the story branch.",
                    ),
                    PlanStep(
                        id="staging-check",
                        title="Check staging impact",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="scene-director",
                        instructions="Review the staging branch.",
                    ),
                ],
            ),
        ),
    )

    assert replaced.snapshot.current_plan is not None
    assert replaced.snapshot.current_plan.revision == 2
    assert {step.id for step in replaced.ready_steps} == {
        "story-check",
        "staging-check",
    }


def test_replan_cancels_uncarried_work_and_keeps_late_result_as_stale_handoff(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    run_id, work_order_id, action_id = _start_pending_delegation(
        service,
        project_id,
        strict_completion_contract=True,
    )
    strict_order = service.agent_runtime.store.get_work_order(work_order_id)
    assert strict_order is not None
    assert strict_order.acceptance_criteria == [
        "Return a canonical delivery field."
    ]
    assert strict_order.expected_output_schema["required"] == ["delivery"]
    decisions_before = {
        item.id
        for item in service.agent_runtime.store.list_decisions(
            project_id,
            run_id=run_id,
        )
    }
    experiences_before = {
        item.id
        for item in service.agent_runtime.store.list_experiences(
            project_id,
            limit=100,
        )
    }

    replanned = service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason="The leader replaced the delegated task.",
            proposed_plan=AgentPlanProposal(
                summary="The replacement plan no longer needs the old handoff.",
                goal_complete=True,
            ),
        ),
    )

    cancelled = service.agent_runtime.store.get_work_order(work_order_id)
    assert cancelled is not None
    assert cancelled.status == DelegationStatus.CANCELLED
    current_plan = replanned.snapshot.current_plan
    assert current_plan is not None
    assert cancelled.superseded_by_plan_revision_id == current_plan.id
    cancelled_action = service.agent_runtime.store.get_action(action_id)
    assert cancelled_action is not None
    assert cancelled_action.status == ActionStatus.CANCELLED

    late_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="shot",
        artifact_id=aggregate.document.shots[0].id,
        version=aggregate.document.shots[0].revision,
    )
    late = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The superseded review arrived after replanning.",
            updated_artifacts=[late_ref],
            findings=["A late finding must remain historical."],
            decisions=["A late decision must not enter project memory."],
        ),
    )

    assert late.result.stale_handoff is True
    assert late.result.stale_reason is not None
    assert service.agent_runtime.store.get_agent_result(work_order_id) == late.result
    persisted_action = service.agent_runtime.store.get_action(action_id)
    assert persisted_action is not None
    assert persisted_action.status == ActionStatus.CANCELLED
    assert late_ref not in persisted_action.artifact_refs
    assert {
        item.id
        for item in service.agent_runtime.store.list_decisions(
            project_id,
            run_id=run_id,
        )
    } == decisions_before
    assert {
        item.id
        for item in service.agent_runtime.store.list_experiences(
            project_id,
            limit=100,
        )
    } == experiences_before


def test_replan_can_explicitly_carry_work_order_into_the_current_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, work_order_id, action_id = _start_pending_delegation(
        service,
        project_id,
    )

    replanned = service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason="The specialist task remains useful after replanning.",
            proposed_plan=AgentPlanProposal(
                summary="Keep the existing specialist handoff active.",
                carried_work_order_ids=[work_order_id],
                goal_complete=True,
            ),
        ),
    )

    current_plan = replanned.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.carried_work_order_ids == [work_order_id]
    carried = service.agent_runtime.store.get_work_order(work_order_id)
    assert carried is not None
    assert carried.status == DelegationStatus.PENDING
    assert carried.active_plan_revision_id == current_plan.id
    assert carried.superseded_by_plan_revision_id is None

    waiting = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(),
    )
    assert waiting.run.snapshot.run.status == AgentRunStatus.WAITING
    assert waiting.run.snapshot.current_plan is not None
    assert waiting.run.snapshot.current_plan.id == current_plan.id
    assert any("carried WorkOrders" in note for note in waiting.notes)

    completed = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The carried review completed against the current plan.",
        ),
    )

    assert completed.result.stale_handoff is False
    finished_order = service.agent_runtime.store.get_work_order(work_order_id)
    assert finished_order is not None
    assert finished_order.status == DelegationStatus.COMPLETED
    finished_action = service.agent_runtime.store.get_action(action_id)
    assert finished_action is not None
    assert finished_action.status == ActionStatus.SUCCEEDED
    assert completed.run.snapshot.run.status == AgentRunStatus.RUNNING
    settled = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_carried_work_order_waits_without_replanning_after_current_steps_finish(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
    )

    replanned = service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason="Finish one leader checkpoint while retaining the handoff.",
            proposed_plan=AgentPlanProposal(
                summary="Record the checkpoint, then wait for the carried handoff.",
                steps=[
                    PlanStep(
                        id="record-checkpoint",
                        title="Record checkpoint",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Record that the carried handoff remains pending.",
                    )
                ],
                carried_work_order_ids=[work_order_id],
                goal_complete=False,
            ),
        ),
    )
    plan = replanned.snapshot.current_plan
    assert plan is not None

    waiting = service.advance_agent_run(
        run_id,
        AgentRunAdvanceRequest(max_actions=1),
    )

    assert waiting.run.snapshot.run.status == AgentRunStatus.WAITING
    assert waiting.run.snapshot.current_plan is not None
    assert waiting.run.snapshot.current_plan.id == plan.id
    assert service.agent_runtime.store.get_work_order(work_order_id) is not None
    assert client.inputs == []


def test_stale_handoff_still_rejects_cross_project_artifact_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
    )
    service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason="The old specialist task is no longer part of the plan.",
            proposed_plan=AgentPlanProposal(
                summary="Finish without the superseded handoff.",
                goal_complete=True,
            ),
        ),
    )

    with pytest.raises(
        ProductionManagerError,
        match="outside the current run project",
    ):
        service.submit_agent_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="This stale result points at another project.",
                evidence_refs=[
                    ArtifactRef(
                        project_id="different-project",
                        artifact_type="shot",
                        artifact_id="shot-outside-current-project",
                        version=1,
                    )
                ],
            ),
        )

    assert service.agent_runtime.store.get_agent_result(work_order_id) is None


def test_agent_plan_cannot_target_a_different_project(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(
        ProductionManagerError,
        match="must target the current run project",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Inspect only the current project.",
                initial_plan=AgentPlanProposal(
                    summary="Invalid cross-project Tool call.",
                    steps=[
                        PlanStep(
                            id="inspect-other-project",
                            title="Inspect another project",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="project.get",
                            instructions="This should be rejected before execution.",
                            arguments={"project_id": "project-outside-this-run"},
                        )
                    ],
                ),
            ),
        )


def test_delegation_uses_compact_work_order_and_profile_specific_role_prompt(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    shot = aggregate.document.shots[0]
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Ask the scene director to review one shot.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one bounded review.",
                selected_method_skill_ids=["method.minimax_h3.brand_promo_video"],
                method_selection_rationale=(
                    "Use visible product proof and claim-to-shot reasoning."
                ),
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="director-review",
                        title="Director review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="specialist-alpha",
                        instructions="Review this shot's staging without changing other shots.",
                        arguments={
                            "why_now": "The shot is the only affected branch.",
                            "constraints": ["Preserve the accepted ending."],
                            "acceptance_criteria": ["Return one concise finding."],
                        },
                        artifact_refs=[
                            {
                                "project_id": project_id,
                                "artifact_type": "shot",
                                "artifact_id": shot.id,
                                "version": shot.revision,
                            }
                        ],
                    )
                ],
            ),
        ),
    )
    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = advanced.created_work_order_ids[0]
    work_order = service.get_agent_work_order(work_order_id)

    assert work_order.work_order.scope[0].artifact_id == shot.id
    assert work_order.work_order.recipient_role == "specialist-alpha"
    assert (
        work_order.work_order.production_skill_role
        == ProductionSkillRole.DIRECTOR
    )
    assert work_order.work_order.constraints == ["Preserve the accepted ending."]
    assert "当前制片技能：AI 短剧（本地格式适配）" in work_order.system_prompt
    assert "导演的通用职责" in work_order.system_prompt
    assert "MiniMax-H3：品牌宣传短片" in work_order.system_prompt
    assert "导演动效语言" in work_order.system_prompt
    assert "把已批准的文本意图转成可见的表演、调度、构图、动作、声音与时间设计" in (
        work_order.system_prompt
    )
    assert work_order.context.artifacts[0].ref.artifact_id == shot.id
    assert work_order.work_order.context_manifest is not None
    assert work_order.work_order.context_manifest.selected_skill_ids == (
        work_order.context.active_skill_ids
    )

    service.agent_runtime.set_run_active_skills(
        run.snapshot.run.id,
        ["format.ai-short-drama"],
    )
    snapshotted_work_order = service.get_agent_work_order(work_order_id)
    assert "method.minimax_h3.brand_promo_video" in (
        snapshotted_work_order.context.active_skill_ids
    )
    assert "导演动效语言" in (
        snapshotted_work_order.system_prompt
    )

    returned = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="Staging is clear; preserve the current eyeline.",
            decisions=["Preserve the current eyeline."],
            updated_artifacts=[work_order.work_order.scope[0]],
        ),
    )
    assert returned.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert returned.run.context.decisions[0].statement == "Preserve the current eyeline."
    assert returned.run.snapshot.run.status == AgentRunStatus.RUNNING
    settled = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED

    digital = aggregate.document.model_copy(deep=True)
    digital.project.id = "project-digital-human-test"
    digital.project.name = "Presenter test"
    digital.project.production_profile = ProductionProfile.DIGITAL_HUMAN
    digital.project.current_script_version_id = digital.script_versions[0].id
    digital.project.current_bible_version_id = digital.bible_versions[0].id
    for script_version in digital.script_versions:
        script_version.project_id = digital.project.id
    for bible_version in digital.bible_versions:
        bible_version.project_id = digital.project.id
    for asset in digital.assets:
        asset.project_id = digital.project.id
    for item in digital.shots:
        item.project_id = digital.project.id
    digital.reference_packs = []
    digital.manifests = []
    service.repository.save_project(digital)
    digital_run = service.create_agent_run(
        digital.project.id,
        AgentRunCreateRequest(
            objective="Review presenter performance.",
            initial_plan=AgentPlanProposal(
                summary="Delegate presenter direction.",
                steps=[
                    PlanStep(
                        id="presenter-direction",
                        title="Presenter direction",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review eyeline and gesture range.",
                    )
                ],
            ),
        ),
    )
    digital_advanced = service.advance_agent_run(
        digital_run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    digital_order = service.get_agent_work_order(
        digital_advanced.created_work_order_ids[0]
    )

    assert "当前制片技能：数字人口播视频" in digital_order.system_prompt
    assert "主播的视线" in digital_order.system_prompt
    assert digital_order.system_prompt != work_order.system_prompt


def test_delegation_accepts_guidance_only_and_shared_artifact_boundaries(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    shot = aggregate.document.shots[0]
    shot_ref = {
        "project_id": project_id,
        "artifact_type": "shot",
        "artifact_id": shot.id,
        "version": shot.revision,
    }
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Delegate a clip-group boundary without losing continuity.",
            initial_plan=AgentPlanProposal(
                summary="Use the same exact shot as scope and continuity anchor.",
                goal_complete=False,
                steps=[
                    PlanStep(
                        id="delegate-shared-boundary",
                        title="Delegate the bounded unit",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="storyboard-director",
                        instructions="Design the bounded clip group.",
                        arguments={
                            "why_now": "The source shot is ready.",
                            "adjacent_boundaries": [
                                {
                                    "relationship": "opening-state",
                                    "artifact_refs": [],
                                    "guidance": "This is the first unit; no prior clip exists.",
                                },
                                {
                                    "relationship": "outgoing-state",
                                    "artifact_refs": [shot_ref],
                                    "guidance": "Preserve the shot's final pose for the next unit.",
                                },
                            ],
                        },
                        artifact_refs=[shot_ref],
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_order = service.get_agent_work_order(advanced.created_work_order_ids[0])

    manifest = work_order.work_order.context_manifest
    assert manifest is not None
    assert [item.relationship for item in manifest.adjacent_boundaries] == [
        "opening-state",
        "outgoing-state",
    ]
    assert [item.ref.artifact_id for item in work_order.context.artifacts] == [shot.id]


def test_partial_work_order_result_can_be_completed_without_orphaning_the_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get one bounded specialist answer.",
            initial_plan=AgentPlanProposal(
                summary="Delegate once.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the scene.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = waiting.created_work_order_ids[0]

    partial = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.PARTIAL,
            concise_summary="The camera intention is still missing.",
            unresolved_questions=["Should the camera remain static?"],
            recommended_next_actions=["Answer the camera question."],
            next_owner=WorkOrderNextOwner.WORKER_DECISION,
        ),
    )

    assert partial.run.snapshot.run.status == AgentRunStatus.WAITING
    assert partial.run.snapshot.actions[0].status.value == "RUNNING"
    assert partial.run.snapshot.pending_work_orders[0].status.value == "NEEDS_ATTENTION"
    assert partial.run.context.open_questions == ["Should the camera remain static?"]

    completed = service.submit_agent_work_order_result(
        work_order_id,
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="Use a restrained static composition.",
        ),
    )

    assert completed.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert completed.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert completed.run.snapshot.pending_work_orders == []
    settled = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED
    with pytest.raises(ValueError, match="already COMPLETED"):
        service.submit_agent_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="Duplicate completion must not overwrite the result.",
            ),
        )


def test_agent_runtime_http_endpoints_are_inspectable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    client = TestClient(app)

    capabilities = client.get("/api/short-drama/agent-runtime/capabilities")
    assert capabilities.status_code == 200
    assert len(capabilities.json()["skills"]) == 4

    created = client.post(
        f"/api/short-drama/projects/{project_id}/agent-runs",
        json={
            "objective": "Create one inspectable checkpoint.",
            "initial_plan": {
                "summary": "One dynamic step",
                "goal_complete": True,
                "steps": [
                    {
                        "id": "checkpoint",
                        "title": "Checkpoint",
                        "kind": "CHECKPOINT",
                        "assigned_agent": "production-manager",
                        "instructions": "Record the current focus.",
                    }
                ],
            },
        },
    )
    assert created.status_code == 200
    run_id = created.json()["snapshot"]["run"]["id"]

    advanced = client.post(
        f"/api/short-drama/agent-runs/{run_id}/advance",
        json={"max_actions": 1},
    )
    assert advanced.status_code == 200
    assert advanced.json()["run"]["snapshot"]["run"]["status"] == "COMPLETED"

    listed = client.get(f"/api/short-drama/projects/{project_id}/agent-runs")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["snapshot"]["run"]["id"] == run_id

    delegated = client.post(
        f"/api/short-drama/projects/{project_id}/agent-runs",
        json={
            "objective": "Expose an exact WorkOrder handoff.",
            "initial_plan": {
                "summary": "Delegate one explicit unit.",
                "goal_complete": True,
                "steps": [
                    {
                        "id": "delegate",
                        "title": "Delegate exact unit",
                        "kind": "DELEGATION",
                        "assigned_agent": "scene-director",
                        "production_skill_role": "DIRECTOR",
                        "instructions": "Review the selected production unit.",
                        "arguments": {
                            "why_now": "The upstream unit is complete.",
                            "experience_tags": ["continuity"],
                            "acceptance_criteria": ["Return one finding."],
                        },
                    }
                ],
            },
        },
    )
    assert delegated.status_code == 200, delegated.text
    delegated_run_id = delegated.json()["snapshot"]["run"]["id"]
    delegated_advance = client.post(
        f"/api/short-drama/agent-runs/{delegated_run_id}/advance",
        json={"max_actions": 1},
    )
    assert delegated_advance.status_code == 200, delegated_advance.text
    work_order_id = delegated_advance.json()["created_work_order_ids"][0]
    work_order = client.get(
        f"/api/short-drama/agent-work-orders/{work_order_id}"
    )
    assert work_order.status_code == 200, work_order.text
    manifest = work_order.json()["work_order"]["context_manifest"]
    assert manifest["work_order_id"] == work_order_id
    assert manifest["objective"] == "Review the selected production unit."
    assert manifest["why_now"] == "The upstream unit is complete."
    assert manifest["experience_tags"] == ["continuity"]
    assert len(work_order.json()["progress_entries"]) == 1

    appended = client.post(
        f"/api/short-drama/agent-work-orders/{work_order_id}/progress",
        json={
            "author": "scene-director",
            "summary": "Loaded the explicit handoff and began the review.",
        },
    )
    assert appended.status_code == 200, appended.text
    assert appended.json()["sequence"] == 2
    progress = client.get(
        f"/api/short-drama/agent-work-orders/{work_order_id}/progress"
    )
    assert progress.status_code == 200, progress.text
    assert [entry["sequence"] for entry in progress.json()["entries"]] == [1, 2]

    bootstrapped = client.post(
        "/api/short-drama/agent-projects",
        json={
            "project": {
                "name": "HTTP bootstrap",
                "source_mode": "IDEA_EXPANSION",
                "input_text": "A concise presenter-led production test.",
                "run_mode": "GUIDED",
            },
            "execution_target": "MOCK",
            "render_export": False,
        },
    )
    assert bootstrapped.status_code == 200
    bootstrapped_project_id = bootstrapped.json()["project_id"]
    fetched = client.get(f"/api/short-drama/projects/{bootstrapped_project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["document"]["project"]["id"] == bootstrapped_project_id


def test_advance_executes_the_current_ready_batch_before_waiting(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Run all independent ready work, including one delegation.",
            initial_plan=AgentPlanProposal(
                summary="Three independent ready steps.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate a bounded review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the current staging.",
                    ),
                    PlanStep(
                        id="checkpoint",
                        title="Record the current focus",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Record the current production focus.",
                    ),
                    PlanStep(
                        id="inspect",
                        title="Inspect the canonical project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the canonical project.",
                        arguments={"project_id": project.id},
                    ),
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=3),
    )

    assert len(advanced.executed_action_ids) == 3
    assert len(advanced.created_work_order_ids) == 1
    assert [action.status.value for action in advanced.run.snapshot.actions] == [
        "RUNNING",
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert all(
        action.tool_name != "planning.advance"
        for action in advanced.run.snapshot.actions
    )
    assert advanced.run.snapshot.run.status == AgentRunStatus.WAITING

    completed = service.submit_agent_work_order_result(
        advanced.created_work_order_ids[0],
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The staging review is complete.",
        ),
    )
    assert completed.run.snapshot.run.status == AgentRunStatus.RUNNING
    settled = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_completed_bounded_plan_replans_from_the_latest_context(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            """{
              "summary": "The latest project read is enough for a final checkpoint.",
              "goal_complete": true,
              "steps": [{
                "id": "finish",
                "title": "Finish from latest context",
                "kind": "CHECKPOINT",
                "assigned_agent": "production-manager",
                "instructions": "Confirm the inspected project satisfies this bounded objective."
              }]
            }"""
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the project and decide whether the bounded goal is complete.",
            initial_plan=AgentPlanProposal(
                summary="Inspect first, then let the Agent observe the result.",
                steps=[
                    PlanStep(
                        id="inspect",
                        title="Inspect project",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the canonical project.",
                        arguments={"project_id": project.id},
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.revision == 2
    assert len(advanced.executed_action_ids) == 2
    planner_payload = json.loads(client.inputs[0])
    recent = planner_payload["context"]["recent_actions"]
    assert recent[-1]["status"] == "SUCCEEDED"
    assert recent[-1]["artifact_refs"][0]["artifact_id"] == project.id
    assert any("latest ContextPack" in note for note in advanced.notes)


def test_failed_actions_can_replan_beyond_the_legacy_fixed_count(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": (
                        "Record recovery after repeated failed plans."
                        if index == 5
                        else f"Retry invalid inspection plan {index}."
                    ),
                    "goal_complete": index == 5,
                    "steps": [
                        (
                            {
                                "id": "recovered",
                                "title": "Record recovery",
                                "kind": "CHECKPOINT",
                                "assigned_agent": "production-manager",
                                "instructions": "Record the recovered plan.",
                            }
                            if index == 5
                            else {
                                "id": f"invalid-inspection-{index}",
                                "title": f"Invalid inspection {index}",
                                "kind": "TOOL_CALL",
                                "assigned_agent": "production-manager",
                                "tool_name": "project.get",
                                "instructions": "Attempt an inspection without its required input.",
                                "arguments": {},
                            }
                        )
                    ],
                }
            )
            for index in range(1, 6)
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Recover after more than four consecutive failed plans.",
            initial_plan=AgentPlanProposal(
                summary="Start with an invalid inspection.",
                steps=[
                    PlanStep(
                        id="invalid-inspection-0",
                        title="Invalid inspection 0",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Attempt an inspection without its required input.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=6),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert [action.status for action in advanced.run.snapshot.actions] == [
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.FAILED,
        ActionStatus.SUCCEEDED,
    ]
    assert len(advanced.executed_action_ids) == 6
    assert len(client.inputs) == 5
    assert not any("bounded retry limit" in note for note in advanced.notes)


def test_completed_plan_waits_when_replan_has_no_actionable_or_terminal_path(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "No justified next action is available yet.",
                    "goal_complete": False,
                    "steps": [],
                }
            )
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Stop naturally when replanning has no supported next action.",
            initial_plan=AgentPlanProposal(
                summary="Record the current state before reconsidering the plan.",
                steps=[
                    PlanStep(
                        id="checkpoint",
                        title="Record current state",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Persist the current state.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.WAITING
    assert len(advanced.executed_action_ids) == 1
    assert len(client.inputs) == 1
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.revision == 1
    assert advanced.run.ready_steps == []
    assert any("invalid plan payload" in note for note in advanced.notes)


def test_external_action_settlement_waits_after_one_invalid_replan(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "summary": "No justified next action is available yet.",
                    "goal_complete": False,
                    "steps": [],
                }
            )
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Complete a delegated review and reconsider the next plan.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one review before replanning.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Return one concise staging review.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    completed = service.submit_agent_work_order_result(
        waiting.created_work_order_ids[0],
        AgentWorkOrderResultRequest(
            status=AgentResultStatus.COMPLETED,
            concise_summary="The staging review is complete.",
        ),
    )

    assert completed.run.snapshot.run.status == AgentRunStatus.RUNNING
    assert client.inputs == []

    settled = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.WAITING
    assert len(client.inputs) == 1
    assert settled.run.snapshot.current_plan is not None
    assert settled.run.snapshot.current_plan.revision == 1
    assert settled.run.ready_steps == []


def test_tool_failure_can_replan_and_continue_within_the_same_advance(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            """{
              "summary": "Recover from the invalid Tool arguments.",
              "goal_complete": true,
              "steps": [{
                "id": "recovered",
                "title": "Record the recovery",
                "kind": "CHECKPOINT",
                "assigned_agent": "production-manager",
                "instructions": "Record that the failed inspection was safely replanned."
              }]
            }"""
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Recover from an invalid inspection action.",
            initial_plan=AgentPlanProposal(
                summary="This Tool call intentionally omits its required argument.",
                steps=[
                    PlanStep(
                        id="invalid-inspection",
                        title="Invalid project inspection",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Attempt the bounded inspection.",
                    )
                ],
            ),
        ),
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=2),
    )

    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert [action.status.value for action in advanced.run.snapshot.actions] == [
        "FAILED",
        "SUCCEEDED",
    ]
    planner_payload = json.loads(client.inputs[0])
    assert planner_payload["context"]["recent_actions"][-1]["status"] == "FAILED"
    assert "project_id" in planner_payload["context"]["recent_actions"][-1]["summary"]


def test_builtin_specialist_can_call_shared_tools_before_returning_result(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    client.responses = [
        *[
            json.dumps(
                {
                    "tool_name": "project.get",
                    "arguments": {"project_id": project.id},
                    "reason": f"Inspect canonical evidence item {index} before deciding.",
                }
            )
            for index in range(1, 7)
        ],
        """{
              "status": "COMPLETED",
              "concise_summary": "The scene direction is grounded in the canonical project.",
              "created_artifacts": [],
              "updated_artifacts": [],
              "findings": ["The current staging is readable."],
              "decisions": ["Preserve the current staging."],
              "assumptions": [],
              "unresolved_questions": [],
              "evidence_refs": [],
              "recommended_next_actions": []
            }""",
    ]
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Get a grounded scene-direction decision.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one grounded review.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate scene direction",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Inspect the project and return one staging decision.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    work_order_id = waiting.created_work_order_ids[0]
    partial_results = []
    while True:
        response = service.execute_agent_work_order(work_order_id)
        if response.result.status == AgentResultStatus.COMPLETED:
            break
        partial_results.append(response.result)

    assert response.run.snapshot.run.status == AgentRunStatus.RUNNING
    settled = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert settled.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert len(partial_results) == 12
    assert all(item.status == AgentResultStatus.PARTIAL for item in partial_results)
    assert len(client.inputs) == 7
    first_payload = json.loads(client.inputs[0])
    assert "project.get" in {
        tool["id"] for tool in first_payload["available_tools"]
    }
    final_payload = json.loads(client.inputs[-1])
    assert len(final_payload["tool_history"]) == 6
    assert [
        len(json.loads(value)["tool_history"]) for value in client.inputs
    ] == list(range(7))
    assert all(
        item["result"]["status"] == "SUCCEEDED"
        for item in final_payload["tool_history"]
    )
    assert all(
        "tool_calls_remaining" not in json.loads(value) for value in client.inputs
    )
    assert all("轮次已用完" not in value for value in client.instructions)
    assert len(response.result.evidence_refs) == 1
    assert response.result.evidence_refs[0].artifact_id == project.id
    experiences = service.agent_runtime_store.list_experiences(project.id)
    assert experiences[0].summary == response.result.concise_summary
    assert "scene-director" in experiences[0].tags
    assert project.production_profile.value in experiences[0].tags


def test_specialist_cannot_bypass_parent_execution_target(tmp_path: Path) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project = service.initialize_fixture().document.project
    client.responses = [
        json.dumps(
            {
                "tool_name": "video.enqueue",
                "arguments": {"project_id": project.id, "request": {}},
                "reason": "Attempt a Mock generation from a REAL_H3 specialist.",
            }
        )
    ]
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Delegate one bounded production review.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            initial_plan=AgentPlanProposal(
                summary="Delegate the review.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Inspect the project and act only if compatible.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = waiting.created_work_order_ids[0]

    requested = service.execute_agent_work_order(work_order_id)

    assert requested.result.status == AgentResultStatus.PARTIAL
    assert service.repository.list_tasks(project.id) == []

    with pytest.raises(
        ProductionManagerError,
        match=r"video\.enqueue.*MOCK.*REAL_H3",
    ):
        service.execute_agent_work_order(work_order_id)

    tool_ids = {
        tool["id"]
        for tool in json.loads(client.inputs[0])["available_tools"]
    }
    assert "video.enqueue" not in tool_ids
    assert "route.execute_h3_plan" in tool_ids
    assert "project.get" in tool_ids
    assert service.repository.list_tasks(project.id) == []
    work_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert work_order is not None
    assert work_order.status == DelegationStatus.NEEDS_ATTENTION
    assert len(work_order.specialist_turns) == 1
    assert work_order.specialist_turns[0].result is not None
    assert work_order.specialist_turns[0].result["status"] == "FAILED"


def test_completed_work_order_contract_errors_are_wrapped(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Require one evidenced specialist finding.",
            initial_plan=AgentPlanProposal(
                summary="Delegate with a simple acceptance criterion.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate evidenced review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Return one evidenced finding.",
                        arguments={
                            "acceptance_criteria": ["Return one concise finding."]
                        },
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )

    with pytest.raises(
        ProductionManagerError,
        match="does not satisfy the WorkOrder contract",
    ):
        service.submit_agent_work_order_result(
            waiting.created_work_order_ids[0],
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="A prose-only completion is insufficient.",
            ),
        )


def test_specialist_contract_failure_returns_the_work_order_to_the_leader(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The specialist omitted the required delivery.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": ["The review itself completed."],
                    "decisions": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                    "evidence_refs": [],
                    "recommended_next_actions": [],
                }
            )
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    _run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
        strict_completion_contract=True,
    )

    with pytest.raises(
        ProductionManagerError,
        match="does not satisfy the WorkOrder contract",
    ):
        service.execute_agent_work_order(work_order_id)

    failed = service.agent_runtime_store.get_work_order(work_order_id)
    assert failed is not None
    assert failed.status == DelegationStatus.NEEDS_ATTENTION
    assert failed.next_owner == WorkOrderNextOwner.LEADER_REPLAN


def test_agent_project_bootstrap_creates_the_project_before_returning(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    guided = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Guided bootstrap",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="A presenter explains one practical idea.",
                run_mode=RunMode.GUIDED,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        )
    )

    guided_project = service.get_project(guided.project_id)
    assert guided_project.document.project.id == guided.project_id
    assert guided.run.snapshot.actions[0].tool_name == "project.create"
    assert guided.run.snapshot.actions[0].status.value == "SUCCEEDED"
    assert guided.run.snapshot.run.status == AgentRunStatus.COMPLETED
    assert guided.run.snapshot.run.unattended is False
    assert (
        guided.run.snapshot.run.execution_target
        == ProductionExecutionTarget.MOCK
    )
    assert guided.run.snapshot.run.render_export is False
    assert guided.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert guided.run.context.render_export is False
    assert guided.run.snapshot.current_plan is not None
    assert guided.run.snapshot.current_plan.goal_complete is True

    automatic = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Automatic bootstrap",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="Two characters discover a surprising package at the door.",
                run_mode=RunMode.AUTO_DRAFT,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        )
    )

    automatic_project = service.get_project(automatic.project_id)
    assert automatic_project.document.project.id == automatic.project_id
    assert automatic.run.snapshot.current_plan is not None
    assert automatic.run.snapshot.current_plan.revision == 1
    assert automatic.run.snapshot.run.status == AgentRunStatus.WAITING
    assert automatic.run.snapshot.run.unattended is True
    assert (
        automatic.run.snapshot.run.execution_target
        == ProductionExecutionTarget.MOCK
    )
    assert automatic.run.snapshot.run.render_export is False
    assert automatic.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert automatic.run.context.render_export is False
    assert automatic.run.ready_steps == []
    assert any(
        "semantic Production Manager" in blocker
        for blocker in automatic.run.snapshot.memory.blockers
    )


def test_local_fallback_does_not_author_a_mock_production_route(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    created = service.create_agent_project(
        AgentProjectCreateRequest(
            project=CreateProjectRequest(
                name="Agent-first default path",
                source_mode=SourceMode.IDEA_EXPANSION,
                input_text="Two characters discover a surprising package at the door.",
                run_mode=RunMode.AUTO_DRAFT,
            ),
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        )
    )

    aggregate = service.get_project(created.project_id)
    assert created.run.snapshot.run.status == AgentRunStatus.WAITING
    assert aggregate.document.project.stage.value != "EXPORTED"
    assert aggregate.exports == []
    assert created.run.ready_steps == []
    assert all(
        action.tool_name not in {"video.enqueue", "task.drain", "timeline.export"}
        for action in created.run.snapshot.actions
    )
    assert created.run.context.execution_target == ProductionExecutionTarget.MOCK
    assert created.run.context.render_export is True
    assert "autonomous_runs" not in aggregate.document.model_dump()


def test_existing_project_without_semantic_planner_waits_for_an_explicit_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Guided continuation",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A presenter explains one practical idea.",
            run_mode=RunMode.GUIDED,
        )
    ).document.project

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Produce a complete Mock draft from this guided project.",
            unattended=True,
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        ),
    )

    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.snapshot.run.unattended is True
    assert run.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert run.snapshot.run.render_export is True
    assert run.context.execution_target == ProductionExecutionTarget.MOCK
    assert run.context.render_export is True
    assert run.snapshot.current_plan is None
    assert run.ready_steps == []
    assert any(
        "supply initial_plan" in blocker for blocker in run.snapshot.memory.blockers
    )


def test_current_run_target_does_not_inherit_an_older_run_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Target isolation",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters find an unexpected parcel.",
            run_mode=RunMode.AUTO_DRAFT,
        )
    ).document.project
    older = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Prepare a real H3 production.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )
    assert older.snapshot.run.status == AgentRunStatus.WAITING
    assert older.ready_steps == []

    current = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Produce a deterministic Mock draft.",
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=True,
        ),
    )

    assert current.snapshot.run.status == AgentRunStatus.WAITING
    assert current.ready_steps == []
    assert older.context.execution_target == ProductionExecutionTarget.REAL_H3
    assert older.context.render_export is False
    assert current.context.execution_target == ProductionExecutionTarget.MOCK
    assert current.context.render_export is True
    assert current.context.decisions[0].run_id == current.snapshot.run.id
    assert current.context.decisions[0].statement == "Render export: yes"
    assert any(
        decision.statement == "Execution target: MOCK"
        and decision.run_id == current.snapshot.run.id
        for decision in current.context.decisions
    )


def test_context_options_are_not_reconstructed_from_decision_text(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Keep explicit runtime options typed.",
            auto_plan=False,
            execution_target=ProductionExecutionTarget.MOCK,
            render_export=False,
        ),
    )
    service.agent_runtime.remember_decision(
        project_id=project.id,
        run_id=run.snapshot.run.id,
        statement="Execution target: REAL_H3",
        rationale="A deliberately contradictory readable trace entry.",
    )
    service.agent_runtime.remember_decision(
        project_id=project.id,
        run_id=run.snapshot.run.id,
        statement="Render export: yes",
        rationale="A deliberately contradictory readable trace entry.",
    )

    current = service.get_agent_run(run.snapshot.run.id)

    assert current.snapshot.run.execution_target == ProductionExecutionTarget.MOCK
    assert current.snapshot.run.render_export is False
    assert current.context.execution_target == ProductionExecutionTarget.MOCK
    assert current.context.render_export is False
    assert {decision.statement for decision in current.context.decisions} >= {
        "Execution target: REAL_H3",
        "Render export: yes",
    }


@pytest.mark.parametrize(
    ("target", "tool_name", "required"),
    [
        (ProductionExecutionTarget.REAL_H3, "video.enqueue", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.retake", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.extend", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "video.gap_fill", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "task.drain", "MOCK"),
        (ProductionExecutionTarget.REAL_H3, "task.reconcile", "MOCK"),
        (ProductionExecutionTarget.MOCK, "route.enqueue_h3_probe", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.execute_h3_plan", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.list_h3_reconciliation", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.resolve_h3_submission", "REAL_H3"),
        (ProductionExecutionTarget.MOCK, "route.resolve_h3_not_submitted", "REAL_H3"),
    ],
)
def test_agent_plan_rejects_incompatible_execution_tool(
    tmp_path: Path,
    target: ProductionExecutionTarget,
    tool_name: str,
    required: str,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(
        ProductionManagerError,
        match=rf"{re.escape(tool_name)}.*{required}",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Attempt an incompatible generation action.",
                execution_target=target,
                initial_plan=_single_tool_plan(
                    tool_name,
                    {"project_id": project.id},
                ),
            ),
        )

    assert service.repository.list_tasks(project.id) == []


def test_provider_execution_tool_requires_an_explicit_run_target(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project

    with pytest.raises(ProductionManagerError, match="has no target"):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Attempt generation without choosing a provider target.",
                initial_plan=_single_tool_plan(
                    "video.enqueue",
                    {"project_id": project.id, "request": {}},
                ),
            ),
        )


def test_real_h3_run_cannot_advance_a_mock_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    task = service.enqueue(
        project.id,
        EnqueueRequest(candidate_count=1),
    ).tasks[0]

    with pytest.raises(
        ProductionManagerError,
        match=r"task\.advance.*MOCK.*REAL_H3",
    ):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Advance only tasks for the selected provider.",
                execution_target=ProductionExecutionTarget.REAL_H3,
                initial_plan=_single_tool_plan(
                    "task.advance",
                    {"task_id": task.id},
                ),
            ),
        )

    persisted = service.repository.get_task(task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.QUEUED


def test_local_planner_requires_a_semantic_planner_or_caller_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Target prompt",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator demonstrates a small workflow.",
            run_mode=RunMode.AUTO_DRAFT,
        )
    ).document.project
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(objective="Continue production."),
    )
    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.snapshot.current_plan is None
    assert run.ready_steps == []
    assert any(
        "configure the language planner or supply initial_plan" in blocker
        for blocker in run.snapshot.memory.blockers
    )

    caller_planned = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the canonical project.",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project.id},
            ),
        ),
    )
    assert caller_planned.snapshot.run.status == AgentRunStatus.RUNNING
    assert caller_planned.ready_steps[0].tool_name == "project.get"


def test_id_only_tool_arguments_cannot_mutate_another_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_a = service.create_project(
        CreateProjectRequest(
            name="Project A",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator explains a useful shortcut.",
        )
    ).document.project
    project_b = service.create_project(
        CreateProjectRequest(
            name="Project B",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters discover a package.",
        )
    ).document.project
    queued = service.enqueue(project_b.id, EnqueueRequest(candidate_count=1))
    foreign_task = queued.tasks[0]
    assert foreign_task.status == TaskStatus.QUEUED

    with pytest.raises(ProductionManagerError, match="task_id must belong"):
        service.create_agent_run(
            project_a.id,
            AgentRunCreateRequest(
                objective="Cancel a task.",
                initial_plan=AgentPlanProposal(
                    summary="Attempt a foreign task mutation.",
                    steps=[
                        PlanStep(
                            id="cancel-task",
                            title="Cancel task",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="task.cancel",
                            instructions="Cancel the selected task.",
                            arguments={"task_id": foreign_task.id},
                        )
                    ],
                ),
            ),
        )

    unchanged = service.repository.get_task(foreign_task.id)
    assert unchanged is not None
    assert unchanged.status == TaskStatus.QUEUED


def test_existing_run_cannot_use_project_create_without_its_bootstrap_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.create_project(
        CreateProjectRequest(
            name="Existing project",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A creator explains a useful shortcut.",
        )
    ).document.project
    before_ids = {item.id for item in service.list_projects().projects}

    with pytest.raises(ProductionManagerError, match="preallocated current run project_id"):
        service.create_agent_run(
            project.id,
            AgentRunCreateRequest(
                objective="Create another project.",
                initial_plan=AgentPlanProposal(
                    summary="Attempt an unscoped project creation.",
                    steps=[
                        PlanStep(
                            id="create-project",
                            title="Create project",
                            kind=ActionKind.TOOL_CALL,
                            assigned_agent="production-manager",
                            tool_name="project.create",
                            instructions="Create an unrelated project.",
                            arguments={
                                "request": {
                                    "name": "Unexpected project",
                                    "source_mode": "IDEA_EXPANSION",
                                    "input_text": "This must not be created.",
                                    "run_mode": "AUTO_DRAFT",
                                }
                            },
                        )
                    ],
                ),
            ),
        )

    assert {item.id for item in service.list_projects().projects} == before_ids


def test_agent_result_artifacts_must_match_the_parent_run_project(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_a = service.create_project(
        CreateProjectRequest(
            name="Result A",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="A presenter explains a practical tip.",
        )
    ).document.project
    project_b = service.create_project(
        CreateProjectRequest(
            name="Result B",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Two characters meet unexpectedly.",
        )
    ).document.project
    run = service.create_agent_run(
        project_a.id,
        AgentRunCreateRequest(
            objective="Delegate a bounded review.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one review.",
                steps=[
                    PlanStep(
                        id="delegate-review",
                        title="Delegate review",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review the current project.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_order_id = waiting.created_work_order_ids[0]

    with pytest.raises(ProductionManagerError, match="outside the current run project"):
        service.submit_agent_work_order_result(
            work_order_id,
            AgentWorkOrderResultRequest(
                status=AgentResultStatus.COMPLETED,
                concise_summary="Reviewed, but returned the wrong artifact.",
                created_artifacts=[
                    ArtifactRef(
                        project_id=project_b.id,
                        artifact_type="project",
                        artifact_id=project_b.id,
                    )
                ],
            ),
        )

    assert service.agent_runtime_store.get_agent_result(work_order_id) is None
    unchanged_order = service.agent_runtime_store.get_work_order(work_order_id)
    assert unchanged_order is not None
    assert unchanged_order.status == DelegationStatus.PENDING


def test_restart_normalizes_interrupted_tool_and_specialist_work(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    tool_run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Inspect the project.",
            initial_plan=AgentPlanProposal(
                summary="Inspect once.",
                steps=[
                    PlanStep(
                        id="inspect",
                        title="Inspect",
                        kind=ActionKind.TOOL_CALL,
                        assigned_agent="production-manager",
                        tool_name="project.get",
                        instructions="Read the project.",
                        arguments={"project_id": project.id},
                    )
                ],
            ),
        ),
    )
    assert tool_run.snapshot.current_plan is not None
    interrupted_action = service.agent_runtime.begin_action(
        tool_run.snapshot.run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="production-manager",
        input_summary="Read the project.",
        plan_revision_id=tool_run.snapshot.current_plan.id,
        plan_step_id="inspect",
        tool_name="project.get",
    )

    delegated_run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Ask a director.",
            initial_plan=AgentPlanProposal(
                summary="Delegate once.",
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review one scene.",
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(
        delegated_run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    work_order_id = waiting.created_work_order_ids[0]
    service.agent_runtime.begin_work_order(work_order_id)

    reopened = _service(tmp_path)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted_action.id)
    recovered_delegation = reopened.agent_runtime_store.get_action(
        waiting.run.snapshot.actions[0].id
    )
    recovered_order = reopened.agent_runtime_store.get_work_order(work_order_id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.FAILED
    assert "interrupted" in (recovered_action.error_message or "").lower()
    assert recovered_delegation is not None
    assert recovered_delegation.status == ActionStatus.RUNNING
    assert recovered_delegation.work_order_id == work_order_id
    assert recovered_order is not None
    assert recovered_order.status == DelegationStatus.NEEDS_ATTENTION
    assert reopened.get_agent_run(tool_run.snapshot.run.id).snapshot.run.status == AgentRunStatus.WAITING
    assert (
        reopened.get_agent_run(delegated_run.snapshot.run.id).snapshot.run.status
        == AgentRunStatus.WAITING
    )


def test_restart_reconciles_persisted_script_revision_start_without_replay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    document = service.initialize_fixture().document
    project_id = document.project.id
    base_script_version_id = document.project.current_script_version_id
    assert base_script_version_id is not None
    feedback = "保留原设定，只改第二场的反应和它对下一场的影响。"
    created = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Revise the current screenplay from user feedback.",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    )
    run_id = created.snapshot.run.id
    step = PlanStep(
        id="start-script-revision",
        title="Start screenplay revision",
        kind=ActionKind.TOOL_CALL,
        assigned_agent="production-manager",
        tool_name="planning.start_script_revision",
        instructions="Start one revision job from the complete user feedback.",
        arguments={
            "project_id": project_id,
            "owner_agent_run_id": run_id,
            "instruction": feedback,
            "base_script_version_id": base_script_version_id,
        },
    )
    plan = service.agent_runtime.replace_plan(
        run_id,
        summary="Start the user-requested screenplay revision.",
        steps=[step],
    )
    interrupted = service.agent_runtime.begin_action(
        run_id,
        kind=ActionKind.TOOL_CALL,
        agent_id="production-manager",
        input_summary=step.instructions,
        plan_revision_id=plan.id,
        plan_step_id=step.id,
        tool_name=step.tool_name,
    )

    persisted = service.start_script_revision(
        project_id,
        owner_agent_run_id=run_id,
        instruction=feedback,
        base_script_version_id=base_script_version_id,
    ).run

    reopened = _service(tmp_path)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted.id)
    recovered = reopened.get_agent_run(run_id)
    current = reopened.get_project(project_id).document

    assert current.planning is not None
    assert current.planning.run_id == persisted.id
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.SUCCEEDED
    assert persisted.id in (recovered_action.output_summary or "")
    assert any(
        ref.artifact_type == "content_planning_job"
        and ref.artifact_id == persisted.id
        for ref in recovered_action.artifact_refs
    )
    assert recovered.snapshot.run.status == AgentRunStatus.RUNNING
    assert len(recovered.ready_steps) == 1
    assert recovered.ready_steps[0].tool_name == "planning.advance"
    assert recovered.ready_steps[0].arguments["run_id"] == persisted.id


def test_restart_marks_delegation_without_persisted_work_order_for_replanning(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, _plan_id, action_id = _begin_interrupted_delegation_action(
        service,
        project_id,
    )

    reopened = _service(tmp_path)

    recovered_action = reopened.agent_runtime_store.get_action(action_id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.FAILED
    assert recovered_action.error_kind == "MissingDelegationWorkOrder"
    assert reopened.agent_runtime_store.list_work_orders(run_id) == []
    assert reopened.get_agent_run(run_id).snapshot.run.status == AgentRunStatus.WAITING


def test_restart_recovers_work_order_created_before_action_link_without_replay(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run_id, plan_id, action_id = _begin_interrupted_delegation_action(
        service,
        project_id,
    )
    work_order = service.agent_runtime.create_work_order(
        run_id,
        sender="production-manager",
        recipient_role="scene-director",
        production_skill_role=ProductionSkillRole.DIRECTOR,
        objective="Review the current scene staging.",
        why_now="The current plan delegated this bounded review.",
        source_plan_revision_id=plan_id,
        source_action_id=action_id,
    )

    reopened = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )

    recovered_action = reopened.agent_runtime_store.get_action(action_id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.RUNNING
    assert recovered_action.work_order_id == work_order.id
    assert reopened.get_agent_run(run_id).snapshot.run.status == AgentRunStatus.WAITING
    assert client.inputs == []

    advanced = reopened.advance_agent_run(run_id, AgentRunAdvanceRequest())
    assert advanced.run.snapshot.run.status == AgentRunStatus.WAITING
    assert len(reopened.agent_runtime_store.list_work_orders(run_id)) == 1
    assert client.inputs == []


def test_restart_restores_missing_work_order_reverse_link(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, plan_id, action_id = _begin_interrupted_delegation_action(
        service,
        project_id,
    )
    work_order = service.agent_runtime.create_work_order(
        run_id,
        sender="production-manager",
        recipient_role="scene-director",
        production_skill_role=ProductionSkillRole.DIRECTOR,
        objective="Review the current scene staging.",
        why_now="The current plan delegated this bounded review.",
        source_plan_revision_id=plan_id,
    )
    action = service.agent_runtime_store.get_action(action_id)
    assert action is not None
    action.work_order_id = work_order.id
    service.agent_runtime_store.save_action(action)

    reopened = _service(tmp_path)

    recovered_action = reopened.agent_runtime_store.get_action(action_id)
    recovered_order = reopened.agent_runtime_store.get_work_order(work_order.id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.RUNNING
    assert recovered_order is not None
    assert recovered_order.source_action_id == action_id
    assert reopened.get_agent_run(run_id).snapshot.run.status == AgentRunStatus.WAITING


@pytest.mark.parametrize(
    ("result_status", "expected_action", "expected_order", "expected_run"),
    [
        (
            AgentResultStatus.COMPLETED,
            ActionStatus.SUCCEEDED,
            DelegationStatus.COMPLETED,
            AgentRunStatus.COMPLETED,
        ),
        (
            AgentResultStatus.FAILED,
            ActionStatus.FAILED,
            DelegationStatus.FAILED,
            AgentRunStatus.WAITING,
        ),
        (
            AgentResultStatus.PARTIAL,
            ActionStatus.FAILED,
            DelegationStatus.NEEDS_ATTENTION,
            AgentRunStatus.WAITING,
        ),
    ],
)
def test_restart_reconciles_saved_agent_result_before_action_completion(
    tmp_path: Path,
    result_status: AgentResultStatus,
    expected_action: ActionStatus,
    expected_order: DelegationStatus,
    expected_run: AgentRunStatus,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, work_order_id, action_id = _start_pending_delegation(
        service,
        project_id,
    )
    service.agent_runtime.record_agent_result(
        work_order_id,
        status=result_status,
        concise_summary=f"Persisted {result_status.value.lower()} specialist result.",
    )
    pre_restart_action = service.agent_runtime_store.get_action(action_id)
    assert pre_restart_action is not None
    assert pre_restart_action.status == ActionStatus.RUNNING

    reopened = _service(tmp_path)

    recovered_action = reopened.agent_runtime_store.get_action(action_id)
    recovered_order = reopened.agent_runtime_store.get_work_order(work_order_id)
    assert recovered_action is not None
    assert recovered_action.status == expected_action
    assert recovered_order is not None
    assert recovered_order.status == expected_order
    assert reopened.agent_runtime_store.get_agent_result(work_order_id) is not None
    assert reopened.get_agent_run(run_id).snapshot.run.status == expected_run


def test_agent_project_bootstrap_does_not_report_partial_project_creation_as_success(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ProductionManagerError, match="Local asset does not exist"):
        service.create_agent_project(
            AgentProjectCreateRequest(
                project=CreateProjectRequest(
                    name="Broken visual seed",
                    source_mode=SourceMode.IMAGE_SEEDED,
                    input_images=[
                        CreativeInputImage(
                            local_path=str(tmp_path / "missing-seed.png"),
                            descriptor="A missing visual seed.",
                        )
                    ],
                    run_mode=RunMode.AUTO_DRAFT,
                ),
                execution_target=ProductionExecutionTarget.MOCK,
                render_export=True,
            )
        )

    runs = service.agent_runtime_store.list_runs()
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED
    actions = service.agent_runtime_store.list_actions(runs[0].id)
    assert actions[0].tool_name == "project.create"
    assert actions[0].status == ActionStatus.FAILED


def test_work_order_context_resolves_a_persisted_take_reference(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    service.enqueue(project.id, EnqueueRequest(candidate_count=1))
    drained = service.drain(project.id, 500)
    take = drained.project.takes[0]
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Review one generated take.",
            initial_plan=AgentPlanProposal(
                summary="Delegate the take review.",
                steps=[
                    PlanStep(
                        id="review-take",
                        title="Review take",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.DIRECTOR,
                        assigned_agent="scene-director",
                        instructions="Review only this generated take.",
                        artifact_refs=[
                            ArtifactRef(
                                project_id=project.id,
                                artifact_type="take",
                                artifact_id=take.id,
                            )
                        ],
                    )
                ],
            ),
        ),
    )
    waiting = service.advance_agent_run(run.snapshot.run.id, AgentRunAdvanceRequest())
    work_order = service.get_agent_work_order(waiting.created_work_order_ids[0])

    assert take.id in {
        item.ref.artifact_id for item in work_order.context.artifacts
    }
    assert take.id not in {
        item.artifact_id for item in work_order.context.unresolved_artifact_refs
    }


def test_local_fallback_never_authors_a_provider_task_route(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    queued = service.enqueue(project.id, EnqueueRequest(candidate_count=2))
    assert len(queued.tasks) > 8
    assert all(task.provider == "mock" for task in queued.tasks)
    mock_task_ids = {task.id for task in queued.tasks}

    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Continue all active generation tasks.",
            execution_target=ProductionExecutionTarget.REAL_H3,
            render_export=False,
        ),
    )

    assert run.snapshot.run.status == AgentRunStatus.WAITING
    assert run.ready_steps == []
    assert service.repository.list_tasks(project.id) == queued.tasks
    assert mock_task_ids == {task.id for task in queued.tasks}
