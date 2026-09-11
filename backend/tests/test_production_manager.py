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
    ToolAttemptRef,
    WorkOrder,
    WorkOrderNextOwner,
    WorkOrderProgressEntry,
)
from short_drama.deps import get_short_drama_service
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ContentPlanningOutput,
    DurationAlignment,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationFitAssessment,
    DurationMismatchResolution,
    DurationRange,
    DurationTargetOrigin,
    PlanningRunStatus,
    PlanningRunStep,
    ProductionExecutionTarget,
    CreativeInputImage,
    CreateProjectRequest,
    EnqueueRequest,
    ProductionDocumentArtifactRef,
    ProductionProfile,
    ProjectPlanningMetadata,
    ProjectStage,
    RunMode,
    ScriptRevisionOrigin,
    ScriptReviewIssue,
    ScriptReviewReport,
    ScriptReviewSeverity,
    SourceMode,
    ReviewRepairOwner,
    StoryRevisionVerification,
    StoryRevisionViolation,
    StoryIntent,
    StoryVerificationResolutionDecision,
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
    SpecialistWorkingSet,
    _artifact_write_handoff_refs,
    _compact_specialist_tool_arguments,
    _compact_specialist_tool_result,
    _normalize_specialist_response_payload,
    _specialist_tool_result_for_persistence,
    _specialist_working_set,
    specialist_tool_history_for_trace,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    production_method_skill_catalog,
)
from short_drama.skills.models import SkillProvenanceKind
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus


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


@pytest.mark.parametrize("tool_name", ["route.get_generation_plan", "route.compile_generation_plan"])
def test_generation_plan_handoff_keeps_selected_payload_once_and_omits_version_history(tool_name: str) -> None:
    selected: dict[str, JsonValue] = {"units": [{"id": "unit-1", "generation_intent": "EXACT_SELECTED_PLAN"}]}
    result: dict[str, JsonValue] = {
        "tool_id": tool_name, "status": "SUCCEEDED", "artifact_refs": [], "warnings": [], "error": None,
        "output": {
            "document": {
                "id": "generation-plan", "current_version_id": "version-3",
                "versions": [{"id": "version-1", "payload": {"old": "UNREQUESTED_HISTORY"}},
                             {"id": "version-3", "payload": selected}],
            },
            "version": {"id": "version-3", "version": 3, "payload": selected},
            "generation_plan": selected, "output_slot": {"id": "slot-current"},
        },
    }
    original = json.dumps(result)
    projected = _compact_specialist_tool_result(tool_name, result)
    output = projected["output"]
    assert isinstance(output, dict)
    assert output["generation_plan"] == selected
    assert output["output_slot"] == {"id": "slot-current"}
    encoded = json.dumps(projected)
    assert encoded.count("EXACT_SELECTED_PLAN") == 1
    assert "UNREQUESTED_HISTORY" not in encoded
    assert json.dumps(result) == original  # Projection does not change the durable result.
    compact = _compact_specialist_tool_result(tool_name, result, retain_exact_payload=False)
    assert "EXACT_SELECTED_PLAN" not in json.dumps(compact)


def test_route_plan_list_projects_coverage_without_replaying_prompt_bodies() -> None:
    result: dict[str, JsonValue] = {"tool_id": "route.list_h3_plans", "status": "SUCCEEDED", "output": {"plans": [
        {"id": f"plan-{index}", "state_revision": 1, "input_generation_plan_version_id": "gp-v3", "decisions": [
            {"generation_unit_id": f"unit-{index}", "shot_ids": [f"shot-{index}"], "readiness": "READY",
             "prompt": "COMPLETE_PROMPT_BODY" * 100, "media_inputs": [{"asset_version_id": f"image-{index}", "source": {"local_path": "/approved.png"}}]},
        ]} for index in range(12)
    ]}}
    projected = _compact_specialist_tool_result("route.list_h3_plans", result)
    encoded = json.dumps(projected)
    assert "COMPLETE_PROMPT_BODY" not in encoded
    assert "route.get_h3_plan" in encoded
    assert all(f'"unit-{index}"' in encoded and f'"image-{index}"' in encoded for index in range(12))
    assert encoded.count('"readiness": "READY"') == 12
    assert "COMPLETE_PROMPT_BODY" in json.dumps(result)


def test_asset_plan_write_handoff_adds_index_without_promoting_evidence() -> None:
    plan_ref = ArtifactRef(
        project_id="project-assets",
        artifact_type="asset_plan",
        artifact_id="asset-plan",
        version=2,
    )
    evidence_ref = ArtifactRef(
        project_id="project-assets",
        artifact_type="storyboard",
        artifact_id="storyboard",
        version=4,
    )

    handoff = _artifact_write_handoff_refs([plan_ref])

    assert evidence_ref not in handoff
    assert handoff == [
        plan_ref,
        plan_ref.model_copy(update={"artifact_type": "asset_plan_index"}),
    ]


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
        "shot.append_storyboard_groups",
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


def test_asset_mutation_history_compacts_embedded_project_aggregate_only() -> None:
    historical_payload = "PROJECT_HISTORY_MUST_STAY_PERSISTED_NOT_REPLAYED" * 1000
    project_aggregate: dict[str, JsonValue] = {
        "document": {
            "project": {
                "id": "project-assets",
                "name": "Asset project",
                "stage": "ASSET_PLANNING",
                "current_script_version_id": "script-v3",
                "current_bible_version_id": "bible-v2",
                "input_text": historical_payload,
                "updated_at": "2026-09-06T00:00:00Z",
            },
            "script_versions": [
                {"id": "script-v1", "screenplay_text": historical_payload}
            ],
            "assets": [{"id": "asset-old", "descriptor": historical_payload}],
            "shots": [{"id": "shot-old", "visual": historical_payload}],
            "reference_packs": [],
            "manifests": [],
            "production_documents": [
                {
                    "id": "asset-plan-1",
                    "kind": "asset.plan",
                    "current_version_id": "asset-plan-1-v4",
                    "versions": [
                        {"id": "asset-plan-1-v4", "payload": historical_payload}
                    ],
                }
            ],
            "asset_plan_document_id": "asset-plan-1",
            "output_slots": [
                {
                    "id": "asset.plan.current",
                    "revision": 4,
                    "current_artifact_ref": {
                        "project_id": "project-assets",
                        "artifact_type": "asset_plan",
                        "artifact_id": "asset-plan-1",
                        "version": 4,
                    },
                    "revision_history": [historical_payload],
                }
            ],
        },
        "tasks": [],
        "takes": [],
        "exports": [],
        "route_evidence": [],
        "video_inspections": [],
        "take_comparisons": [],
        "audio_inspections": [],
        "episode_reviews": [],
        "progress": {
            "total_tasks": 0,
            "terminal_tasks": 0,
            "export_ready": False,
        },
    }
    artifact_refs: list[JsonValue] = [
        {
            "project_id": "project-assets",
            "artifact_type": "asset_version",
            "artifact_id": "asset-character-v1",
            "version": 1,
        }
    ]
    result: dict[str, JsonValue] = {
        "tool_id": "artifact.create_asset",
        "status": "SUCCEEDED",
        "output": {
            "asset": {
                "id": "asset-character",
                "project_id": "project-assets",
                "kind": "CHARACTER",
                "name": "Lin Yi",
                "current_version_id": "asset-character-v1",
            },
            "version": {
                "id": "asset-character-v1",
                "version": 1,
                "descriptor": "Structured character identity brief",
            },
            "project": project_aggregate,
        },
        "artifact_refs": artifact_refs,
        "warnings": ["Keep the identity anchor stable."],
        "error": None,
    }

    compact = _compact_specialist_tool_result("artifact.create_asset", result)
    serialized = json.dumps(compact, ensure_ascii=False)

    # The durable Tool result is untouched; only the next model input is projected.
    assert historical_payload in json.dumps(result, ensure_ascii=False)
    assert "PROJECT_HISTORY_MUST_STAY_PERSISTED_NOT_REPLAYED" not in serialized
    assert compact["artifact_refs"] == artifact_refs
    assert compact["warnings"] == ["Keep the identity anchor stable."]
    output = compact["output"]
    assert isinstance(output, dict)
    assert output["asset"] == result["output"]["asset"]  # type: ignore[index]
    assert output["version"] == result["output"]["version"]  # type: ignore[index]
    project_summary = output["project"]
    assert isinstance(project_summary, dict)
    current_project = project_summary["project"]
    assert isinstance(current_project, dict)
    assert current_project["id"] == "project-assets"
    assert current_project["current_script_version_id"] == "script-v3"
    assert project_summary["document_pointers"] == {
        "master_audio_document_id": None,
        "audio_structure_document_id": None,
        "timed_text_document_id": None,
        "production_spec_document_id": None,
        "storyboard_document_id": None,
        "asset_plan_document_id": "asset-plan-1",
        "generation_plan_document_id": None,
    }
    assert project_summary["production_document_refs"] == [
        {
            "id": "asset-plan-1",
            "kind": "asset.plan",
            "current_version_id": "asset-plan-1-v4",
        }
    ]
    counts = project_summary["counts"]
    assert isinstance(counts, dict)
    assert counts["assets"] == 1
    assert counts["production_documents"] == 1


def test_whole_project_aggregate_tool_output_uses_same_structural_projection() -> None:
    result: dict[str, JsonValue] = {
        "tool_id": "script.revise_lines",
        "status": "SUCCEEDED",
        "output": {
            "document": {
                "project": {
                    "id": "project-script",
                    "stage": "SCRIPT_READY",
                    "current_script_version_id": "script-v5",
                    "current_bible_version_id": "bible-v1",
                },
                "script_versions": [
                    {"id": "script-v5", "screenplay_text": "DO_NOT_REPLAY_SCRIPT"}
                ],
                "assets": [],
                "shots": [],
                "production_documents": [],
                "output_slots": [],
            },
            "tasks": [],
            "takes": [],
            "exports": [],
            "progress": {"total_tasks": 0, "export_ready": False},
        },
        "artifact_refs": [
            {
                "project_id": "project-script",
                "artifact_type": "script_version",
                "artifact_id": "script-v5",
                "version": 5,
            }
        ],
        "warnings": [],
        "error": None,
    }

    compact = _compact_specialist_tool_result("script.revise_lines", result)
    serialized = json.dumps(compact, ensure_ascii=False)

    assert "DO_NOT_REPLAY_SCRIPT" not in serialized
    output = compact["output"]
    assert isinstance(output, dict)
    project = output["project"]
    assert isinstance(project, dict)
    assert project["current_script_version_id"] == "script-v5"
    assert compact["artifact_refs"] == result["artifact_refs"]


def test_many_mutation_turns_persist_domain_results_without_project_snapshots() -> None:
    marker = "REPEATED_PROJECT_SNAPSHOT_MUST_NOT_GROW_WORK_ORDER" * 2000
    aggregate: dict[str, JsonValue] = {
        "document": {
            "project": {
                "id": "project-assets",
                "stage": "ASSET_PLANNING",
                "current_script_version_id": "script-v3",
                "current_bible_version_id": "bible-v2",
            },
            "script_versions": [{"id": "script-v3", "payload": marker}],
            "assets": [{"id": "asset-existing", "payload": marker}],
            "shots": [],
            "production_documents": [],
            "output_slots": [],
        },
        "tasks": [],
        "takes": [],
        "exports": [],
        "progress": {"total_tasks": 0, "export_ready": False},
    }
    raw_results: list[dict[str, JsonValue]] = []
    turns: list[SpecialistTurnRecord] = []
    for index in range(8):
        raw_result: dict[str, JsonValue] = {
            "tool_id": "artifact.create_asset",
            "status": "SUCCEEDED",
            "output": {
                "asset": {
                    "id": f"asset-{index}",
                    "current_version_id": f"asset-{index}-v1",
                },
                "version": {"id": f"asset-{index}-v1", "version": 1},
                "task": {"id": f"image-task-{index}", "status": "PENDING"},
                "entry": {
                    "id": f"reference-entry-{index}",
                    "asset_version_id": f"asset-{index}-v1",
                },
                "usage": {"input_tokens": 11, "output_tokens": 7},
                "cost": {"estimated_amount": 0.02, "currency": "USD"},
                "project": aggregate,
            },
            "artifact_refs": [
                {
                    "project_id": "project-assets",
                    "artifact_type": "asset_version",
                    "artifact_id": f"asset-{index}-v1",
                    "version": 1,
                }
            ],
            "warnings": [f"warning-{index}"],
            "error": None,
        }
        raw_results.append(raw_result)
        persisted = _specialist_tool_result_for_persistence(
            "artifact.create_asset",
            raw_result,
        )
        persisted_output = persisted["output"]
        assert isinstance(persisted_output, dict)
        raw_output = raw_result["output"]
        assert isinstance(raw_output, dict)
        for key in ("asset", "version", "task", "entry", "usage", "cost"):
            assert persisted_output[key] == raw_output[key]
        assert persisted["artifact_refs"] == raw_result["artifact_refs"]
        assert persisted["warnings"] == raw_result["warnings"]
        assert persisted["status"] == raw_result["status"]
        assert persisted["error"] == raw_result["error"]
        turns.append(
            SpecialistTurnRecord(
                id=f"turn-{index}",
                sequence=index + 1,
                tool_name="artifact.create_asset",
                arguments={"project_id": "project-assets"},
                reason="Create the next planned core asset.",
                status=SpecialistTurnStatus.COMPLETED,
                result=persisted,
            )
        )

    work_order = WorkOrder(
        id="work-order-many-assets",
        parent_run_id="run-assets",
        sender="production-manager",
        recipient_role="asset-worker",
        objective="Create planned core assets.",
        why_now="The asset plan is current.",
        specialist_turns=turns,
    )
    raw_bytes = len(json.dumps(raw_results, ensure_ascii=False).encode())
    persisted_bytes = len(work_order.model_dump_json().encode())
    history_json = json.dumps(
        specialist_tool_history_for_trace(work_order),
        ensure_ascii=False,
    )

    assert marker not in work_order.model_dump_json()
    assert marker not in history_json
    assert persisted_bytes < raw_bytes / 20
    assert len(history_json.encode()) < raw_bytes / 20

    explicit_project_read: dict[str, JsonValue] = {
        "tool_id": "project.get",
        "status": "SUCCEEDED",
        "output": aggregate,
        "artifact_refs": [],
        "warnings": [],
        "error": None,
    }
    assert (
        _specialist_tool_result_for_persistence(
            "project.get",
            explicit_project_read,
        )
        is explicit_project_read
    )


@pytest.mark.parametrize(
    ("tool_name", "replace_fields"),
    [
        ("artifact.create_asset_plan", {}),
        (
            "artifact.replace_asset_plan",
            {"base_version_id": "asset-plan-version-1"},
        ),
    ],
)
def test_asset_plan_invalid_arguments_persist_compact_stable_node_feedback(
    tmp_path: Path,
    tool_name: str,
    replace_fields: dict[str, JsonValue],
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / tool_name / "short-drama.sqlite3",
        workspace_root=tmp_path / tool_name / "workspace",
    )
    candidate_marker = "FULL_ASSET_PLAN_BODY_MUST_NOT_REACH_WORKER_"
    full_candidate_marker = candidate_marker + ("x" * 8_000)
    arguments: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "request": {
            "asset_plan": {
                "project_id": "project-assets",
                "script_version_id": "script-version-1",
                "bible_version_id": "bible-version-1",
                "production_spec_document_id": "production-spec-1",
                "production_spec_version_id": "production-spec-version-1",
                "storyboard_document_id": "storyboard-1",
                "storyboard_version_id": "storyboard-version-1",
                "items": [
                    {
                        "id": "asset-item-hero",
                        "disposition": "ANCHOR",
                        "asset_kind": "CHARACTER",
                        "name": "男主角",
                        "rationale": "跨镜头稳定主角身份。",
                        "work_items": [
                            {
                                "id": "work-hero-master",
                                "operation": "GENERATE",
                                "output_role": "character_master",
                                "final_prompt": full_candidate_marker,
                                "size": "1344x768",
                                "review_purpose": "CANONICAL_MASTER",
                                "unexpected_full_candidate_field": (
                                    full_candidate_marker
                                ),
                            }
                        ],
                    }
                ],
                "notes": [full_candidate_marker],
            },
            "origin": "AGENT",
            **replace_fields,
        },
    }

    invalid = service.tool_registry.invoke(tool_name, arguments)
    assert invalid.status.value == "INVALID_ARGUMENTS"
    raw_result = invalid.model_dump(mode="json")
    assert candidate_marker in json.dumps(arguments, ensure_ascii=False)

    persisted = _specialist_tool_result_for_persistence(
        tool_name,
        raw_result,
        arguments=arguments,
    )
    persisted_json = json.dumps(persisted, ensure_ascii=False)
    assert candidate_marker not in persisted_json
    error = persisted["error"]
    assert isinstance(error, dict)
    feedback = json.loads(str(error["message"]))
    assert feedback["kind"] == "ASSET_PLAN_INPUT_VALIDATION"
    assert "artifact.retry_asset_plan_attempt" in feedback["instruction"]
    assert len(feedback["issues"]) == 1
    issue = feedback["issues"][0]
    assert issue["item_id"] == "asset-item-hero"
    assert issue["work_item_id"] == "work-hero-master"
    assert issue["loc"][-1] == "unexpected_full_candidate_field"
    assert issue["offending_node"] == {
        "item": {
            "id": "asset-item-hero",
            "disposition": "ANCHOR",
            "asset_kind": "CHARACTER",
            "brief": None,
        },
        "work_item": {
            "id": "work-hero-master",
            "operation": "GENERATE",
            "output_role": "character_master",
            "review_purpose": "CANONICAL_MASTER",
            "reference_pack_intent": None,
            "ordered_references": None,
            "text_plate_intent": None,
            "dependency_ids": None,
        },
    }

    work_order = WorkOrder(
        id=f"work-order-{tool_name}",
        parent_run_id="agent-run-assets",
        sender="production-manager",
        recipient_role="asset-planner",
        objective="Persist the complete AssetPlan.",
        why_now="The exact sources are current.",
        specialist_turns=[
            SpecialistTurnRecord(
                id=f"turn-{tool_name}",
                sequence=1,
                tool_name=tool_name,
                arguments=arguments,
                reason="Persist the complete candidate.",
                status=SpecialistTurnStatus.COMPLETED,
                result=persisted,
            )
        ],
    )
    working_set = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({tool_name}),
    ).model_dump(mode="json")
    working_set_json = json.dumps(working_set, ensure_ascii=False)
    assert candidate_marker not in working_set_json
    handoff_error = working_set["latest_handoff"]["result"]["error"]  # type: ignore[index]
    assert isinstance(handoff_error, dict)
    handoff_feedback = json.loads(str(handoff_error["message"]))
    assert handoff_feedback["issues"][0]["item_id"] == "asset-item-hero"
    assert handoff_feedback["issues"][0]["work_item_id"] == (
        "work-hero-master"
    )


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_external_compilation_handoff_exposes_exact_attempt_ref(status: str) -> None:
    order = WorkOrder(
        id="prompt-order", parent_run_id="prompt-run", sender="leader",
        recipient_role="compiler", objective="Compile and save the current unit.",
        why_now="Its inputs are current.",
        specialist_turns=[SpecialistTurnRecord(
            id="exact-compile-attempt", sequence=1, tool_name="route.compile_h3_prompt",
            arguments={"project_id": "prompt-project", "request": {"generation_unit_id": "unit-1"}},
            reason="Compile the exact unit.", status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "route.compile_h3_prompt", "status": status,
                    "output": {"compiled_prompt": "exact output"} if status == "SUCCEEDED" else None,
                    "artifact_refs": [], "warnings": [], "error": None},
        )],
    )
    working_set = _specialist_working_set(order, [], project_id="prompt-project")
    assert working_set.latest_handoff is not None
    reference = ToolAttemptRef.model_validate(working_set.latest_handoff["attempt_ref"])
    assert reference.attempt_id == "exact-compile-attempt"
    assert reference.work_order_id == order.id
    assert reference.outcome_status == status
    if status == "SUCCEEDED":
        assert "exact output" in json.dumps(working_set.latest_handoff)


@pytest.mark.parametrize("saved_unit", ["unit-1", "unit-2"])
def test_video_working_set_retires_consumed_drafts_and_only_the_saved_unit_context(saved_unit: str) -> None:
    plan_ref = {"project_id": "p", "artifact_type": "generation_plan", "artifact_id": "gp", "version": 3}
    route_ref = {"project_id": "p", "artifact_type": "h3_route_plan", "artifact_id": "rp", "version": 1}
    target: dict[str, JsonValue] = {"generation_plan_document_id": "gp", "generation_plan_version_id": "gp-v3", "generation_unit_id": "unit-1"}
    turns: list[SpecialistTurnRecord] = []

    def add(tool: str, arguments: dict[str, JsonValue], output: dict[str, JsonValue], *, refs: list[JsonValue] | None = None) -> None:
        turns.append(SpecialistTurnRecord(
            id=f"turn-{len(turns) + 1}", sequence=len(turns) + 1, tool_name=tool,
            arguments=arguments, reason="Continue the scoped preparation.", status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": tool, "status": "SUCCEEDED", "output": output,
                    "artifact_refs": refs or [], "warnings": [], "error": None},
        ))

    add("route.get_generation_plan", {"project_id": "p", "version_id": "gp-v3"},
        {"generation_plan": {"generation_intent": "FULL_PLAN_BODY"},
         "document": {"id": "gp"}, "version": {"id": "gp-v3", "payload": {"generation_intent": "FULL_PLAN_BODY"}}}, refs=[plan_ref])
    add("route.get_generation_unit_context", {"project_id": "p", "request": target},
        {"source_context": {"visual": "CURRENT_UNIT_SOURCE"}}, refs=[plan_ref])
    failed_ref: dict[str, JsonValue] = {
        "project_id": "p", "agent_run_id": "r", "work_order_id": "w", "attempt_id": "old-attempt",
        "tool_name": "route.compile_h3_prompt", "turn_status": "COMPLETED", "outcome_status": "FAILED",
        "has_arguments": True, "has_result": True,
    }
    add("agent.get_tool_attempt", {"project_id": "p", "attempt_ref": failed_ref, "payload": "BOTH"},
        {"attempt_ref": failed_ref, "arguments": {"source_prompt": "CONSUMED_OLD_DRAFT"},
         "result": {"error": {"message": "CONSUMED_OLD_DRAFT"}}})
    add("route.compile_h3_prompt", {"project_id": "p", "request": {**target, "source_prompt": "CONSUMED_OLD_DRAFT"}},
        {"compiled_prompt": "EXACT_CURRENT_PROMPT", "source_prompt": "CONSUMED_OLD_DRAFT", "media_inputs": [], **target})

    def working_set() -> SpecialistWorkingSet:
        return _specialist_working_set(WorkOrder(
            id="w", parent_run_id="r", sender="leader", recipient_role="compiler",
            objective="Prepare the current generation plan.", why_now="Sources are ready.", specialist_turns=turns,
        ), [], project_id="p", read_tool_ids=frozenset({"route.get_generation_plan", "route.get_generation_unit_context"}))

    compiled = working_set().model_dump_json()
    assert "EXACT_CURRENT_PROMPT" in compiled
    assert "CONSUMED_OLD_DRAFT" not in compiled
    assert "FULL_PLAN_BODY" not in compiled
    add("route.plan_h3", {"project_id": "p", "request": {
        "generation_plan_version_id": "gp-v3", "unit_ids": [saved_unit],
        "shot_directives": [{"generation_unit_id": saved_unit, "prompt_override": "EXACT_CURRENT_PROMPT"}],
    }}, {"plan": {"id": "rp", "decisions": [{"prompt": "EXACT_CURRENT_PROMPT"}]}}, refs=[route_ref])
    saved = working_set().model_dump_json()
    assert "EXACT_CURRENT_PROMPT" not in saved
    assert "CONSUMED_OLD_DRAFT" not in saved
    assert ("CURRENT_UNIT_SOURCE" in saved) == (saved_unit != "unit-1")
    assert "h3_route_plan" in saved
    assert "CONSUMED_OLD_DRAFT" in "".join(turn.model_dump_json() for turn in turns)


@pytest.mark.parametrize("operation", ["route.plan_h3", "route.compile_h3_prompt"])
@pytest.mark.parametrize("target_version", ["gp-v3", "gp-v4"])
def test_video_write_preserves_other_loaded_successful_compilations(
    operation: str, target_version: str,
) -> None:
    turns: list[SpecialistTurnRecord] = []
    for unit in ["unit-1", "unit-2"]:
        reference = ToolAttemptRef(
            project_id="p", agent_run_id="r", work_order_id="source-compiler",
            attempt_id=f"compile-{unit}", tool_name="route.compile_h3_prompt",
            turn_status="COMPLETED", outcome_status="SUCCEEDED",
            has_arguments=True, has_result=True,
        ).model_dump(mode="json")
        turns.append(SpecialistTurnRecord(
            id=f"read-{unit}", sequence=len(turns) + 1, tool_name="agent.get_tool_attempt",
            arguments={"attempt_ref": reference, "payload": "RESULT"},
            reason="Read the successful output from the prior Worker.",
            status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "agent.get_tool_attempt", "status": "SUCCEEDED", "output": {
                "attempt_ref": reference, "arguments": None,
                "result": {"tool_id": "route.compile_h3_prompt", "status": "SUCCEEDED", "output": {
                    "generation_plan_version_id": "gp-v3", "generation_unit_id": unit,
                    "candidate_id": f"reference:{unit}", "compiled_prompt": f"EXACT_PROMPT_{unit}",
                    "source_prompt": f"RETIRED_SOURCE_{unit}",
                    "media_inputs": [{"asset_version_id": f"approved-{unit}"}],
                }},
            }},
        ))
    order = WorkOrder(
        id="route-writer", parent_run_id="r", sender="leader", recipient_role="route-writer",
        objective="Save two independently compiled units.", why_now="Both outputs are ready.",
        specialist_turns=turns,
    )
    raw_reads = order.model_dump_json()
    before = _specialist_working_set(order, [], project_id="p").model_dump_json()
    assert "EXACT_PROMPT_unit-1" in before and "EXACT_PROMPT_unit-2" in before
    assert "RETIRED_SOURCE_" not in before
    assert "approved-unit-1" in before and "approved-unit-2" in before
    assert order.model_dump_json() == raw_reads
    request: dict[str, JsonValue] = {"generation_plan_version_id": target_version}
    if operation == "route.plan_h3":
        request["unit_ids"] = ["unit-1"]
    else:
        request["generation_unit_id"] = "unit-1"
    order.specialist_turns.append(SpecialistTurnRecord(
        id="consume-unit-1", sequence=3, tool_name=operation,
        arguments={"project_id": "p", "request": request},
        reason="Save or supersede only the first selected unit.",
        status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": operation, "status": "SUCCEEDED", "output": {"new_result": "saved"},
                "artifact_refs": [{"project_id": "p", "artifact_type": "h3_route_plan",
                                   "artifact_id": "route-1", "version": 1}]},
    ))
    after = _specialist_working_set(order, [], project_id="p").model_dump_json()
    assert ("EXACT_PROMPT_unit-1" in after) == (target_version != "gp-v3")
    assert "EXACT_PROMPT_unit-2" in after
    assert "approved-unit-2" in after
    assert "RETIRED_SOURCE_" not in after
    assert "RETIRED_SOURCE_" in order.model_dump_json()


def test_successful_compile_lookup_preserves_explicit_arguments_and_failed_drafts() -> None:
    raw: dict[str, JsonValue] = {"tool_id": "agent.get_tool_attempt", "status": "SUCCEEDED", "output": {
        "attempt_ref": {"tool_name": "route.compile_h3_prompt"},
        "arguments": {"request": {"source_prompt": "EXPLICITLY_REQUESTED_ARGUMENT"}},
        "result": {"status": "SUCCEEDED", "output": {
            "source_prompt": "REDUNDANT_RESULT_SOURCE", "compiled_prompt": "EXACT_OUTPUT",
        }},
    }}
    original = json.dumps(raw)
    projected = json.dumps(_compact_specialist_tool_result("agent.get_tool_attempt", raw))
    assert "EXPLICITLY_REQUESTED_ARGUMENT" in projected and "EXACT_OUTPUT" in projected
    assert "REDUNDANT_RESULT_SOURCE" not in projected
    assert json.dumps(raw) == original
    failed: dict[str, JsonValue] = {"tool_id": "agent.get_tool_attempt", "status": "SUCCEEDED", "output": {
        "attempt_ref": {"tool_name": "route.compile_h3_prompt"},
        "result": {"status": "FAILED", "error": {"message": "COMPLETE_FAILED_DRAFT"}},
    }}
    assert _compact_specialist_tool_result("agent.get_tool_attempt", failed) == failed


def test_asset_plan_attempt_drops_consumed_planning_context_body() -> None:
    marker = "CONSUMED_ASSET_PLANNING_CONTEXT_MUST_NOT_REPLAY_" + ("x" * 20_000)
    attempt_result: dict[str, JsonValue] = {
        "tool_id": "artifact.create_asset_plan",
        "status": "INVALID_ARGUMENTS",
        "output": None,
        "artifact_refs": [],
        "warnings": [],
        "error": {
            "kind": "ASSET_PLAN_INPUT_VALIDATION",
            "message": json.dumps(
                {
                    "kind": "ASSET_PLAN_INPUT_VALIDATION",
                    "issues": [
                        {
                            "item_id": "asset-item-hero",
                            "work_item_id": "work-hero-master",
                            "msg": "repair one field",
                        }
                    ],
                }
            ),
        },
    }
    working_set = _specialist_working_set(
        WorkOrder(
            id="work-order-consumed-asset-context",
            parent_run_id="agent-run-assets",
            sender="production-manager",
            recipient_role="asset-planner",
            objective="Create the AssetPlan.",
            why_now="The storyboard is ready.",
            specialist_turns=[
                SpecialistTurnRecord(
                    id="turn-read-planning-context",
                    sequence=1,
                    tool_name="artifact.get_asset_planning_context",
                    arguments={"project_id": "project-assets"},
                    reason="Read exact upstream truth once.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "artifact.get_asset_planning_context",
                        "status": "SUCCEEDED",
                        "output": {"storyboard": {"body": marker}},
                        "artifact_refs": [],
                        "warnings": [],
                        "error": None,
                    },
                ),
                SpecialistTurnRecord(
                    id="turn-create-candidate",
                    sequence=2,
                    tool_name="artifact.create_asset_plan",
                    arguments={
                        "project_id": "project-assets",
                        "request": {"asset_plan": {"items": []}},
                    },
                    reason="Persist the complete candidate.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result=attempt_result,
                ),
            ],
        ),
        [],
        project_id="project-assets",
        read_tool_ids=frozenset({"artifact.get_asset_planning_context"}),
        write_tool_ids=frozenset({"artifact.create_asset_plan"}),
    ).model_dump(mode="json")

    assert working_set["read_observations"] == []
    assert marker not in json.dumps(working_set, ensure_ascii=False)
    assert working_set["latest_handoff"]["attempt_ref"]["attempt_id"] == (  # type: ignore[index]
        "turn-create-candidate"
    )


def test_specialist_working_set_folds_old_turns_and_progress_by_entity() -> None:
    artifact_ref: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "artifact_type": "asset_version",
        "artifact_id": "asset-character-v3",
        "version": 3,
    }

    def build_working_set(cycles: int) -> dict[str, JsonValue]:
        turns: list[SpecialistTurnRecord] = []
        progress_entries: list[WorkOrderProgressEntry] = []
        sequence = 0
        for index in range(cycles):
            old_marker = f"HISTORICAL_BODY_{index:04d}_MUST_NOT_REPLAY"
            sequence += 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"turn-image-{index}",
                    sequence=sequence,
                    tool_name="image.advance",
                    arguments={
                        "project_id": "project-assets",
                        "task_id": "image-task-character",
                    },
                    reason="Advance the current image task.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "image.advance",
                        "status": "SUCCEEDED",
                        "output": {
                            "task": {
                                "id": "image-task-character",
                                "project_id": "project-assets",
                                "asset_id": "asset-character",
                                "output_version_id": "asset-character-v3",
                                "operation": "EDIT",
                                "status": "SUCCEEDED",
                                "request": {"prompt": old_marker * 20},
                                "attempt": index + 1,
                                "updated_at": f"2026-09-06T00:{index:02d}:00Z",
                            }
                        },
                        "artifact_refs": [artifact_ref],
                        "warnings": [],
                        "error": None,
                    },
                )
            )
            sequence += 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"turn-asset-{index}",
                    sequence=sequence,
                    tool_name="artifact.review_version",
                    arguments={
                        "project_id": "project-assets",
                        "asset_id": "asset-character",
                        "version_id": "asset-character-v3",
                    },
                    reason="Record the current asset decision.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "artifact.review_version",
                        "status": "SUCCEEDED",
                        "output": {
                            "asset": {
                                "id": "asset-character",
                                "project_id": "project-assets",
                                "kind": "CHARACTER",
                                "name": "Lin Yi",
                                "current_version_id": "asset-character-v3",
                                "approved_version_id": "asset-character-v3",
                            },
                            "version": {
                                "id": "asset-character-v3",
                                "version": 3,
                                "descriptor": old_marker * 20,
                                "approval_status": "APPROVED",
                                "review_notes": "Identity is production-ready.",
                            },
                        },
                        "artifact_refs": [artifact_ref],
                        "warnings": [],
                        "error": None,
                    },
                )
            )
            sequence += 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"turn-review-{index}",
                    sequence=sequence,
                    tool_name="qa.inspect_asset_version",
                    arguments={
                        "project_id": "project-assets",
                        "asset_id": "asset-character",
                        "version_id": "asset-character-v3",
                    },
                    reason="Inspect the current approved candidate.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "qa.inspect_asset_version",
                        "status": "SUCCEEDED",
                        "output": {
                            "recommended": True,
                            "score": 4.8,
                            "issues": [],
                            "summary": f"LATEST_REVIEW_{index:04d}",
                            "evidence_references": [
                                {
                                    "source_project_id": "project-assets",
                                    "asset_version_id": "asset-character-v3",
                                }
                            ],
                        },
                        "artifact_refs": [artifact_ref],
                        "warnings": [],
                        "error": None,
                    },
                )
            )
            progress_entries.append(
                WorkOrderProgressEntry(
                    id=f"progress-{index}",
                    work_order_id="work-order-assets",
                    sequence=index + 1,
                    author="asset-worker",
                    summary=f"PROGRESS_{index:04d}_{old_marker * 20}",
                    artifact_refs=[ArtifactRef.model_validate(artifact_ref)],
                )
            )

        work_order = WorkOrder(
            id="work-order-assets",
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-worker",
            objective="Finish the current character asset.",
            why_now="The asset plan selected this character.",
            specialist_turns=turns,
        )
        return _specialist_working_set(
            work_order,
            progress_entries,
        ).model_dump(mode="json")

    small = build_working_set(2)
    large = build_working_set(80)
    small_json = json.dumps(small, ensure_ascii=False)
    large_json = json.dumps(large, ensure_ascii=False)

    assert large["total_tool_turns"] == 240
    assert len(large_json.encode()) < len(small_json.encode()) + 1500
    assert "HISTORICAL_BODY_0000_MUST_NOT_REPLAY" not in large_json
    assert "PROGRESS_0000" not in large_json
    assert "LATEST_REVIEW_0079" in large_json
    assert "PROGRESS_0079" in large_json
    assert large["latest_handoff"]["sequence"] == 240  # type: ignore[index]
    entity_state = {
        (item["kind"], item["id"]): item["state"]
        for item in large["entities"]  # type: ignore[union-attr]
    }
    assert entity_state[("image_task", "asset-character")]["status"] == (
        "SUCCEEDED"
    )
    assert entity_state[("asset", "asset-character")][
        "approved_version_id"
    ] == "asset-character-v3"
    assert entity_state[("asset_version", "asset-character-v3")][
        "approval_status"
    ] == "APPROVED"
    assert entity_state[("asset_review", "asset-character-v3")][
        "recommended"
    ] is True
    assert large["artifact_refs"] == [artifact_ref]


def test_specialist_working_set_keeps_distinct_view_findings_without_replaying_reads() -> None:
    def build(cycles: int) -> dict[str, JsonValue]:
        turns: list[SpecialistTurnRecord] = []
        for cycle in range(cycles):
            for view in ("front", "profile", "back"):
                sequence = len(turns) + 1
                turns.append(
                    SpecialistTurnRecord(
                        id=f"inspection-{sequence}",
                        sequence=sequence,
                        tool_name="qa.inspect_asset_version",
                        arguments={
                            "project_id": "project-assets",
                            "asset_id": "one-character",
                            "version_id": f"version-{view}",
                        },
                        reason="Compare the selected views of the same character.",
                        status=SpecialistTurnStatus.COMPLETED,
                        result={
                            "tool_id": "qa.inspect_asset_version",
                            "status": "SUCCEEDED",
                            "output": {
                                "recommended": view != "front",
                                "summary": f"{view} finding {cycle}",
                                "issues": [],
                            },
                            "artifact_refs": [],
                        },
                    )
                )
        work_order = WorkOrder(
            id="work-views",
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-verifier",
            objective="Inspect three versions belonging to one character.",
            why_now="All three candidates are ready.",
            specialist_turns=turns,
        )
        return _specialist_working_set(
            work_order,
            [],
            read_tool_ids=frozenset({"qa.inspect_asset_version"}),
        ).model_dump(mode="json")

    small, repeated = build(1), build(80)
    reviews = {
        item["id"]: item["state"]
        for item in repeated["entities"]  # type: ignore[union-attr]
        if item["kind"] == "asset_review"
    }
    assert set(reviews) == {"version-front", "version-profile", "version-back"}
    assert reviews["version-front"]["recommended"] is False
    assert reviews["version-profile"]["recommended"] is True
    assert reviews["version-back"]["recommended"] is True
    for view in ("front", "profile", "back"):
        assert reviews[f"version-{view}"]["summary"] == f"{view} finding 79"
    assert len(json.dumps(repeated)) < len(json.dumps(small)) + 1000


def test_inspection_working_set_keeps_confirmed_absence_for_each_take() -> None:
    order = WorkOrder(
        id="work-inspection-coverage", parent_run_id="run-video", sender="manager",
        recipient_role="video-reviewer", objective="Find missing Take observations.",
        why_now="All existing videos are available.",
        specialist_turns=[SpecialistTurnRecord(
            id=f"read-{index}", sequence=index, tool_name="video.get_take_inspection",
            arguments={"take_id": take_id}, reason="Read existing evidence.",
            status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": "video.get_take_inspection", "status": "SUCCEEDED",
                    "output": {"take": {"id": take_id}, "report": None}},
        ) for index, take_id in enumerate(("take-a", "take-b"), 1)],
    )
    observed = _specialist_working_set(order, [])
    assert {entity.id for entity in observed.entities} == {"take-a", "take-b"}
    assert all(entity.state["inspection_available"] is False for entity in observed.entities)
    order.specialist_turns.append(SpecialistTurnRecord(
        id="new-report", sequence=3, tool_name="video.inspect_take",
        arguments={"take_id": "take-a"}, reason="Observe the missing Take.",
        status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "video.inspect_take", "status": "SUCCEEDED", "output": {"report": {
            "id": "report-a", "project_id": "project-video", "take_id": "take-a",
            "semantic_status": "COMPLETE", "technical_checks": [], "observations": [],
            "samples": [], "summary": "New observation replaces confirmed absence.",
        }}},
    ))
    after = _specialist_working_set(order, [])
    assert [entity.id for entity in after.entities] == ["take-b"]
    assert after.latest_handoff is not None and "report-a" in json.dumps(after.latest_handoff)


def test_h3_run_list_is_a_scoped_index_with_exact_read_paths() -> None:
    raw: dict[str, JsonValue] = {
        "tool_id": "route.list_h3_runs", "status": "SUCCEEDED", "output": {"runs": [
            {"id": f"run-{index}", "task_id": f"task-{index}", "project_id": "project-1",
             "generation_unit_id": f"group-{index}", "prompt": "exact prompt " * 2000,
             "media_inputs": [{"source": {"local_path": "/large/reference.png"}}],
             "uploaded_inputs": [], "pricing": {},
             "request": {"content": [{"type": "text", "text": "exact prompt " * 2000}]},
             "latest_remote_task": {"id": f"remote-{index}", "status": "succeeded", "error": None}}
            for index in range(14)
        ]},
    }
    before = json.dumps(raw)
    projected = _compact_specialist_tool_result("route.list_h3_runs", raw)
    assert json.dumps(raw) == before
    assert "exact prompt" not in json.dumps(projected)
    assert "large/reference.png" not in json.dumps(projected)
    output = projected["output"]
    assert isinstance(output, dict)
    assert output["details_tool"] == "route.get_h3_run" and output["task_state_tool"] == "task.get"
    runs = output["runs"]
    assert isinstance(runs, list) and len(runs) == 14
    assert isinstance(runs[0], dict) and runs[0]["task_id"] == "task-0"
    assert runs[0]["remote"] == {"id": "remote-0", "status": "succeeded", "error": None}
    assert runs[0]["prompt_characters"] == len("exact prompt " * 2000)
    assert len(json.dumps(projected)) < 12000


def test_specialist_working_set_retains_each_take_inspection_without_replaying_frames() -> None:
    def build(cycles: int) -> SpecialistWorkingSet:
        turns: list[SpecialistTurnRecord] = []
        for cycle in range(cycles):
            for take_id in ("take-a", "take-b"):
                sequence = len(turns) + 1
                report_id = f"report-{take_id}-{sequence}"
                turns.append(SpecialistTurnRecord(
                    id=f"turn-{sequence}", sequence=sequence,
                    tool_name="video.inspect_take", arguments={"take_id": take_id},
                    reason="Inspect the two existing Takes.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "video.inspect_take", "status": "SUCCEEDED",
                        "output": {"report": {
                            "id": report_id, "project_id": "project-video", "take_id": take_id,
                            "semantic_status": "COMPLETE", "technical_checks": [],
                            "observations": [{"description": f"{take_id} visible evidence"}],
                            "issues": [], "summary": f"{take_id} finding {cycle}",
                            "samples": [{
                                "source_take_id": take_id, "timestamp_seconds": index / 2,
                                "image_path": f"/raw/frames/{report_id}/frame-{index}.jpg",
                            } for index in range(24)],
                        }},
                        "artifact_refs": [{
                            "project_id": "project-video", "artifact_type": "video_inspection",
                            "artifact_id": report_id, "version": None,
                        }],
                    },
                ))
        order = WorkOrder(
            id="work-video-review", parent_run_id="run-video", sender="manager",
            recipient_role="video-reviewer", objective="Report both Take observations.",
            why_now="Both videos have been recovered.", specialist_turns=turns,
        )
        before = order.model_dump_json()
        result = _specialist_working_set(
            order, [], project_id="project-video",
        )
        assert order.model_dump_json() == before
        assert "/raw/frames/" in before
        assert "/raw/frames/" not in result.model_dump_json()
        return result

    small, repeated = build(1), build(40)
    assert repeated.latest_handoff is not None
    handoff = repeated.latest_handoff["result"]
    assert isinstance(handoff, dict) and isinstance(handoff["output"], dict)
    latest = handoff["output"]["report"]
    assert isinstance(latest, dict)
    facts = {entity.id: entity.state for entity in repeated.entities if entity.kind == "video_inspection"}
    assert set(facts) == {"take-a"}
    assert facts["take-a"]["summary"] == "take-a finding 39"
    assert latest["take_id"] == "take-b" and latest["summary"] == "take-b finding 39"
    assert facts["take-a"]["sample_count"] == latest["sample_count"] == 24
    assert {ref.artifact_id for ref in repeated.artifact_refs} == {"report-take-a-79", "report-take-b-80"}
    assert len(repeated.model_dump_json()) < len(small.model_dump_json()) + 1000


def test_specialist_working_set_retains_take_selection_and_replacement_after_restart() -> None:
    order = WorkOrder(
        id="work-editor", parent_run_id="run-editor", sender="manager",
        recipient_role="editor", objective="Select all existing groups and assemble.",
        why_now="The source media is available.",
    )

    def append(tool: str, arguments: dict[str, JsonValue], output: dict[str, JsonValue]) -> None:
        sequence = len(order.specialist_turns) + 1
        order.specialist_turns.append(SpecialistTurnRecord(
            id=f"turn-{sequence}", sequence=sequence, tool_name=tool,
            arguments=arguments, reason="Continue the current editing decision.",
            status=SpecialistTurnStatus.COMPLETED,
            result={"tool_id": tool, "status": "SUCCEEDED", "output": output},
        ))

    for take_id, shots in (("take-a", ["shot-a1", "shot-a2"]), ("take-b", ["shot-b"]), ("take-c", ["shot-a2"])):
        append("task.get", {"task_id": f"task-{take_id}"}, {"takes": [{
            "id": take_id, "task_id": f"task-{take_id}", "shot_id": shots[0],
            "shot_windows": [{"shot_id": shot, "source_in_seconds": index * 2, "source_out_seconds": (index + 1) * 2}
                             for index, shot in enumerate(shots)],
            "is_selected": False, "selected_shot_ids": [], "media_path": f"/{take_id}.mp4",
        }]})
    # Legacy successful selection responses contain only a project summary.
    for _ in range(3):
        append("video.select_take", {"take_id": "take-a"}, {"project": {"id": "project-video"}})
        append("video.select_take", {"take_id": "take-b"}, {"project": {"id": "project-video"}})

    def facts() -> dict[str, dict[str, JsonValue]]:
        before = order.model_dump_json()
        restored = WorkOrder.model_validate_json(before)
        state = _specialist_working_set(restored, [], project_id="project-video",
                                        read_tool_ids=frozenset({"task.get"}), write_tool_ids=frozenset({"video.select_take"}))
        assert restored.model_dump_json() == before
        return {item.id: item.state for item in state.entities if item.kind == "take"}

    selected = facts()
    assert selected["take-a"]["selected_shot_ids"] == ["shot-a1", "shot-a2"]
    assert selected["take-b"]["selected_shot_ids"] == ["shot-b"]
    assert selected["take-a"]["is_selected"] is selected["take-b"]["is_selected"] is True
    assert selected["take-c"]["is_selected"] is False
    append("video.select_take", {"take_id": "take-c"}, {"project": {"id": "project-video"}})
    replaced = facts()
    assert replaced["take-a"]["selected_shot_ids"] == ["shot-a1"]
    assert replaced["take-b"]["selected_shot_ids"] == ["shot-b"]
    assert replaced["take-c"]["selected_shot_ids"] == ["shot-a2"]
    # Current response snapshots override earlier cached Take reads, including deselection.
    append("video.select_take", {"take_id": "take-a"}, {"take_selection": [
        {"id": "take-a", "is_selected": True, "selected_shot_ids": ["shot-a1", "shot-a2"]},
        {"id": "take-c", "is_selected": False, "selected_shot_ids": []},
    ]})
    replaced = facts()
    assert replaced["take-a"]["selected_shot_ids"] == ["shot-a1", "shot-a2"]
    assert replaced["take-c"]["selected_shot_ids"] == []
    assert replaced["take-c"]["is_selected"] is False
    assert replaced["take-c"]["media_path"] == "/take-c.mp4"


def test_specialist_working_set_retains_approval_for_each_view_after_restart() -> None:
    turns: list[SpecialistTurnRecord] = []

    def append_reviews(cycle: int) -> None:
        for view, status in (("front", "APPROVED"), ("profile", "REJECTED"), ("back", "APPROVED")):
            sequence = len(turns) + 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"review-{sequence}",
                    sequence=sequence,
                    tool_name="artifact.review_version",
                    arguments={
                        "project_id": "project-assets",
                        "asset_id": "one-character",
                        "version_id": f"version-{view}",
                    },
                    reason="Record the decision for this exact supplemental view.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "artifact.review_version",
                        "status": "SUCCEEDED",
                        "output": {
                            "asset": {"id": "one-character", "approved_version_id": "canonical-face"},
                            "version": {
                                "id": f"version-{view}",
                                "approval_status": status,
                                "width": 344,
                                "height": 976,
                                "review_notes": f"{view} decision {cycle}",
                            },
                        },
                        "artifact_refs": [{
                            "project_id": "project-assets",
                            "artifact_type": "asset_version",
                            "artifact_id": f"version-{view}",
                        }],
                    },
                )
            )

    def rebuilt() -> dict[str, JsonValue]:
        work_order = WorkOrder(
            id="work-views",
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-verifier",
            objective="Review all views of one character.",
            why_now="Candidates are ready.",
            specialist_turns=turns,
        )
        restored = WorkOrder.model_validate_json(work_order.model_dump_json())
        return _specialist_working_set(
            restored, [], write_tool_ids=frozenset({"artifact.review_version"})
        ).model_dump(mode="json")

    append_reviews(0)
    first = rebuilt()
    for cycle in range(1, 80):
        append_reviews(cycle)
    repeated = rebuilt()
    versions = {
        entity["id"]: entity["state"]
        for entity in repeated["entities"]  # type: ignore[union-attr]
        if entity["kind"] == "asset_version"
    }
    assert set(versions) == {"version-front", "version-profile", "version-back"}
    assert versions["version-front"]["approval_status"] == "APPROVED"
    assert versions["version-profile"]["approval_status"] == "REJECTED"
    assert versions["version-back"]["approval_status"] == "APPROVED"
    assert all(value["width"] == 344 for value in versions.values())
    assert all(value["review_notes"].endswith("decision 79") for value in versions.values())
    assert {ref["artifact_id"] for ref in repeated["artifact_refs"]} == set(versions)  # type: ignore[union-attr]
    assert len(json.dumps(repeated)) < len(json.dumps(first)) + 1000


def test_specialist_working_set_keeps_independent_reference_facts_after_shot_write() -> None:
    pack: dict[str, JsonValue] = {
        "id": "pack-carrier", "subject_asset_id": "asset-carrier",
        "entries": [{
            "id": "entry-text", "asset_version_id": "version-text",
            "role": "PROP_STATE", "tags": ["ExactCase", "红烧肉八元"],
            "angle": "NOT_APPLICABLE", "framing": "DETAIL",
        }],
    }
    first_plan: dict[str, JsonValue] = {
        "shot_id": "shot-one", "requirements": [], "planning_notes": "old one",
    }
    second_plan: dict[str, JsonValue] = {
        "shot_id": "shot-two", "requirements": [], "planning_notes": "keep two",
    }
    new_plan: dict[str, JsonValue] = {
        "shot_id": "shot-one",
        "requirements": [{"id": "requirement-one", "required_tags": ["红烧肉八元"]}],
        "planning_notes": "saved one",
    }
    turns = [
        SpecialistTurnRecord(
            id="read-scope", sequence=1, tool_name="artifact.get_asset_work_context",
            arguments={"project_id": "project-assets"}, reason="Read the selected scope.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.get_asset_work_context", "status": "SUCCEEDED",
                "output": {
                    "reference_packs": [pack],
                    "shot_reference_plans": [first_plan, second_plan],
                    "storyboard": {"description": "STALE_SCOPE_BODY_MUST_NOT_REPLAY" * 100},
                },
                "artifact_refs": [{
                    "project_id": "project-assets", "artifact_type": "shot",
                    "artifact_id": "shot-one", "version": 1,
                }],
            },
        ),
        SpecialistTurnRecord(
            id="write-one", sequence=2, tool_name="shot.update_reference_plan",
            arguments={"project_id": "project-assets", "shot_id": "shot-one"},
            reason="Save the first shot without changing the other facts.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "shot.update_reference_plan", "status": "SUCCEEDED",
                "output": {"reference_plan": new_plan, "shot_id": "shot-one", "shot_revision": 2},
                "artifact_refs": [{
                    "project_id": "project-assets", "artifact_type": "shot",
                    "artifact_id": "shot-one", "version": 2,
                }],
            },
        ),
        SpecialistTurnRecord(
            id="preview-one", sequence=3, tool_name="shot.preview_reference_plan",
            arguments={"project_id": "project-assets", "shot_id": "shot-one"},
            reason="Verify the saved selection.", status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "shot.preview_reference_plan", "status": "SUCCEEDED",
                "output": {"shot_id": "shot-one", "missing_requirement_ids": []},
                "artifact_refs": [],
            },
        ),
        SpecialistTurnRecord(
            id="preview-two", sequence=4, tool_name="shot.preview_reference_plan",
            arguments={"project_id": "project-assets", "shot_id": "shot-two"},
            reason="Verify the other shot without losing the first result.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "shot.preview_reference_plan", "status": "SUCCEEDED",
                "output": {"shot_id": "shot-two", "missing_requirement_ids": ["missing-two"]},
                "artifact_refs": [],
            },
        ),
    ]

    def rebuild() -> SpecialistWorkingSet:
        work_order = WorkOrder(
            id="work-reference-continuation", parent_run_id="run-assets",
            sender="production-manager", recipient_role="asset-worker",
            objective="Bind two shots using the same approved pack.",
            why_now="Approved media are ready.", specialist_turns=turns,
        )
        restored = WorkOrder.model_validate_json(work_order.model_dump_json())
        return _specialist_working_set(
            restored, [], read_tool_ids=frozenset({
                "artifact.get_asset_work_context", "shot.preview_reference_plan",
            }), write_tool_ids=frozenset({
                "shot.update_reference_plan", "artifact.add_reference_entry", "shot.patch_storyboard",
            }),
        )

    current = rebuild()
    assert not current.read_observations
    assert "STALE_SCOPE_BODY_MUST_NOT_REPLAY" not in current.model_dump_json()
    facts = {(entity.kind, entity.id): entity.state for entity in current.entities}
    assert facts[("reference_pack", "pack-carrier")] == pack
    assert facts[("shot_reference_plan", "shot-one")] == new_plan
    assert facts[("shot_reference_plan", "shot-two")] == second_plan
    assert facts[("reference_plan_preview", "shot-one")]["missing_requirement_ids"] == []
    assert facts[("reference_plan_preview", "shot-two")]["missing_requirement_ids"] == ["missing-two"]

    turns.append(SpecialistTurnRecord(
        id="change-first-reference-plan", sequence=len(turns) + 1,
        tool_name="shot.update_reference_plan",
        arguments={"project_id": "project-assets", "shot_id": "shot-one"},
        reason="Changing one shot invalidates only its preview.",
        status=SpecialistTurnStatus.COMPLETED,
        result={
            "tool_id": "shot.update_reference_plan", "status": "SUCCEEDED",
            "output": {"reference_plan": new_plan},
            "artifact_refs": [{
                "project_id": "project-assets", "artifact_type": "shot",
                "artifact_id": "shot-one", "version": 3,
            }],
        },
    ))
    assert {entity.id for entity in rebuild().entities if entity.kind == "reference_plan_preview"} == {"shot-two"}

    updated_pack: dict[str, JsonValue] = {**pack, "entries": [{
        "id": "entry-text", "asset_version_id": "version-text",
        "role": "PROP_STATE", "tags": ["CorrectedTag"],
        "angle": "NOT_APPLICABLE", "framing": "DETAIL",
    }]}
    turns.append(SpecialistTurnRecord(
        id="replace-pack", sequence=len(turns) + 1, tool_name="artifact.add_reference_entry",
        arguments={"project_id": "project-assets"}, reason="Record a changed pack.",
        status=SpecialistTurnStatus.COMPLETED,
        result={
            "tool_id": "artifact.add_reference_entry", "status": "SUCCEEDED",
            "output": {"pack": updated_pack},
            "artifact_refs": [{
                "project_id": "project-assets", "artifact_type": "reference_pack",
                "artifact_id": "pack-carrier",
            }],
        },
    ))
    changed = rebuild()
    pack_facts = [entity for entity in changed.entities if entity.kind == "reference_pack"]
    assert len(pack_facts) == 1
    assert pack_facts[0].state == updated_pack
    assert not any(entity.kind == "reference_plan_preview" for entity in changed.entities)
    turns.append(SpecialistTurnRecord(
        id="change-shot-without-reference-body", sequence=len(turns) + 1, tool_name="shot.patch_storyboard",
        arguments={"project_id": "project-assets"}, reason="A different write invalidates this shot.",
        status=SpecialistTurnStatus.COMPLETED,
        result={
            "tool_id": "shot.patch_storyboard", "status": "SUCCEEDED", "output": {},
            "artifact_refs": [{
                "project_id": "project-assets", "artifact_type": "shot",
                "artifact_id": "shot-one", "version": 4,
            }],
        },
    ))
    assert {entity.id for entity in rebuild().entities if entity.kind == "shot_reference_plan"} == {"shot-two"}


def test_specialist_working_set_keeps_latest_successful_read_per_tool() -> None:
    def build_working_set(cycles: int) -> dict[str, JsonValue]:
        turns: list[SpecialistTurnRecord] = []
        sequence = 0
        for index in range(cycles):
            sequence += 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"turn-plan-{index}",
                    sequence=sequence,
                    tool_name="artifact.get_asset_plan",
                    arguments={
                        "project_id": "project-assets",
                        "version_id": "asset-plan-v1",
                    },
                    reason="Read the current asset plan.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "artifact.get_asset_plan",
                        "status": "SUCCEEDED",
                        "output": {
                            "asset_plan": {
                                "id": "asset-plan-v1",
                                "items": [
                                    {
                                        "id": "character-anchor",
                                        "prompt": f"LATEST_PLAN_{index:04d}",
                                    }
                                ],
                            }
                        },
                        "artifact_refs": [],
                        "warnings": [],
                        "error": None,
                    },
                )
            )
            sequence += 1
            turns.append(
                SpecialistTurnRecord(
                    id=f"turn-capability-{index}",
                    sequence=sequence,
                    tool_name="image.capability",
                    arguments={},
                    reason="Read current image capability.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "image.capability",
                        "status": "SUCCEEDED",
                        "output": {
                            "provider": "AZURE_OPENAI",
                            "model": "gpt-image-2",
                            "can_generate": True,
                            "size": {"allowed": ["1536x864"]},
                        },
                        "artifact_refs": [],
                        "warnings": [],
                        "error": None,
                    },
                )
            )
        return _specialist_working_set(
            WorkOrder(
                id="work-order-read-whiteboard",
                parent_run_id="run-assets",
                sender="production-manager",
                recipient_role="asset-worker",
                objective="Execute the current asset plan.",
                why_now="The canonical storyboard is ready.",
                specialist_turns=turns,
            ),
            [],
            read_tool_ids=frozenset(
                {"artifact.get_asset_plan", "image.capability"}
            ),
        ).model_dump(mode="json")

    small = build_working_set(2)
    large = build_working_set(80)
    large_json = json.dumps(large, ensure_ascii=False)

    assert large["latest_handoff"]["request"]["tool_name"] == (  # type: ignore[index]
        "image.capability"
    )
    assert [
        item["tool_name"] for item in large["read_observations"]  # type: ignore[union-attr]
    ] == ["artifact.get_asset_plan"]
    assert "LATEST_PLAN_0079" in large_json
    assert "LATEST_PLAN_0000" not in large_json
    assert len(large_json.encode()) < len(
        json.dumps(small, ensure_ascii=False).encode()
    ) + 1500


@pytest.mark.parametrize("finish_with_write", [False, True])
def test_specialist_working_set_keeps_distinct_documents_and_replaces_versions(
    finish_with_write: bool,
) -> None:
    def build(cycles: int) -> str:
        turns: list[SpecialistTurnRecord] = []
        for index in range(cycles):
            for document_id in ("asset-preparation", "storyboard-draft"):
                turns.append(SpecialistTurnRecord(
                    id=f"read-{document_id}-{index}",
                    sequence=len(turns) + 1,
                    tool_name="artifact.get_production_document",
                    arguments={
                        "project_id": "project-documents",
                        "production_document_id": document_id,
                        "version_id": f"{document_id}-v{index + 1}",
                    },
                    reason="Read the selected current document.",
                    status=SpecialistTurnStatus.COMPLETED,
                    result={
                        "tool_id": "artifact.get_production_document",
                        "status": "SUCCEEDED",
                        "output": {
                            "document": {"id": document_id},
                            "version": {
                                "id": f"{document_id}-v{index + 1}",
                                "version": index + 1,
                                "payload": {"body": f"BODY_{document_id}_{index:04d}"},
                            },
                        },
                        "artifact_refs": [{
                            "project_id": "project-documents",
                            "artifact_type": "production_document",
                            "artifact_id": document_id,
                            "version": index + 1,
                        }],
                    },
                ))
        if finish_with_write:
            turns.append(SpecialistTurnRecord(
                id="write-draft",
                sequence=len(turns) + 1,
                tool_name="shot.append_storyboard_group",
                arguments={"project_id": "project-documents"},
                reason="Save the new draft without changing asset preparation.",
                status=SpecialistTurnStatus.COMPLETED,
                result={
                    "tool_id": "shot.append_storyboard_group",
                    "status": "SUCCEEDED",
                    "output": {},
                    "artifact_refs": [{
                        "project_id": "project-documents",
                        "artifact_type": "production_document",
                        "artifact_id": "storyboard-draft",
                        "version": cycles + 1,
                    }],
                },
            ))
        result = _specialist_working_set(
            WorkOrder(
                id="work-order-documents", parent_run_id="run-documents",
                sender="production-manager", recipient_role="storyboard-worker",
                objective="Use the current assets and storyboard together.",
                why_now="Both documents inform the next group.",
                specialist_turns=turns,
            ),
            [],
            read_tool_ids=frozenset({"artifact.get_production_document"}),
            write_tool_ids=frozenset({"shot.append_storyboard_group"}),
        )
        assert len(result.read_observations) == 1
        assert result.read_observations[0].request_identifiers[
            "production_document_id"
        ] == "asset-preparation"
        return result.model_dump_json()

    small = build(2)
    large = build(40)
    assert large.count("BODY_asset-preparation_0039") == 1
    assert large.count("BODY_storyboard-draft_0039") == (0 if finish_with_write else 1)
    assert "BODY_asset-preparation_0000" not in large
    assert "BODY_storyboard-draft_0000" not in large
    def read_payload_size(value: str) -> int:
        state = json.loads(value)
        return len(json.dumps({
            "read_observations": state["read_observations"],
            "latest_handoff": state["latest_handoff"],
        }))

    assert read_payload_size(large) < read_payload_size(small) + 1000


@pytest.mark.parametrize("selector", ["shot_id", "scene_id"])
def test_current_content_reads_keep_distinct_scopes_without_replaying_indexes(
    selector: str,
) -> None:
    turns: list[SpecialistTurnRecord] = []
    content_key = selector.removesuffix("_id")
    for cycle in range(30):
        for identity in ("first", "second"):
            turns.append(SpecialistTurnRecord(
                id=f"read-{identity}-{cycle}", sequence=len(turns) + 1,
                tool_name="artifact.read_current",
                arguments={"project_id": "project-scoped", selector: identity},
                reason="Compare the two selected current contents.",
                status=SpecialistTurnStatus.COMPLETED,
                result={
                    "tool_id": "artifact.read_current", "status": "SUCCEEDED",
                    "output": {"content": {
                        "project_id": "project-scoped",
                        "bible": {"body": "SHARED_INDEX_BODY"},
                        "shot_index": [{"id": "first"}, {"id": "second"}],
                        content_key: {"id": identity, "body": f"BODY_{identity}_{cycle}"},
                    }},
                    "artifact_refs": [],
                },
            ))
    state = _specialist_working_set(
        WorkOrder(
            id="work-scoped", parent_run_id="run-scoped", sender="production-manager",
            recipient_role="asset-planner", objective="Plan related prop states.",
            why_now="Both selected shots are needed together.", specialist_turns=turns,
        ),
        [], read_tool_ids=frozenset({"artifact.read_current"}),
    )
    assert len(state.read_observations) == 1
    assert state.read_observations[0].request_identifiers[selector] == "first"
    serialized = state.model_dump_json()
    assert serialized.count("BODY_first_29") == 1
    assert serialized.count("BODY_second_29") == 1
    assert "BODY_first_0" not in serialized
    assert "BODY_second_0" not in serialized
    assert serialized.count("SHARED_INDEX_BODY") == 1
    assert len(turns[0].result["output"]["content"]["shot_index"]) == 2  # type: ignore[index]


@pytest.mark.parametrize(
    "tool_name, selectors",
    [
        ("artifact.get_asset_work_context", [
            {"scope": {"mode": "WORK_ITEM", "work_item_ids": ["work-props"]}, "shot_ids": ["shot-2", "shot-3"]},
            {"scope": {"mode": "WORK_ITEM", "work_item_ids": ["work-props"]}, "shot_ids": ["shot-12", "shot-13"]},
        ]),
        ("agent.read_context", [
            {"context_ref": "saved-dialogue", "json_pointer": "/lines", "offset": 0, "limit": 20},
            {"context_ref": "saved-dialogue", "json_pointer": "/lines", "offset": 20, "limit": 20},
        ]),
    ],
)
def test_scoped_tool_reads_keep_distinct_slices_and_pages(
    tool_name: str, selectors: list[dict[str, JsonValue]],
) -> None:
    turns: list[SpecialistTurnRecord] = []
    for cycle in range(30):
        for part, selector in enumerate(selectors):
            turns.append(SpecialistTurnRecord(
                id=f"scope-read-{part}-{cycle}", sequence=len(turns) + 1,
                tool_name=tool_name, arguments={"project_id": "project-slices", **selector},
                reason="Combine the requested independent content slices.",
                status=SpecialistTurnStatus.COMPLETED,
                result={"tool_id": tool_name, "status": "SUCCEEDED",
                        "output": {"body": f"SCOPE_BODY_{part}_{cycle}"}, "artifact_refs": []},
            ))
    state = _specialist_working_set(WorkOrder(
        id="work-slices", parent_run_id="run-slices", sender="production-manager",
        recipient_role="asset-planner", objective="Use all selected shots together.",
        why_now="The mapping needs more than one scoped read.", specialist_turns=turns,
    ), [], read_tool_ids=frozenset({tool_name}))
    value = state.model_dump_json()
    assert len(state.read_observations) == 1
    assert value.count("SCOPE_BODY_0_29") == 1
    assert value.count("SCOPE_BODY_1_29") == 1
    assert "SCOPE_BODY_0_0" not in value
    assert "SCOPE_BODY_1_0" not in value
    assert "SCOPE_BODY_0_0" in str(turns[0].result)


def test_saving_one_route_consumes_only_its_unit_context() -> None:
    turns = [SpecialistTurnRecord(
        id=f"unit-read-{unit}", sequence=sequence,
        tool_name="route.get_generation_unit_context",
        arguments={"project_id": "project-units", "request": {
            "generation_plan_version_id": "plan-v1", "generation_unit_id": unit,
        }},
        reason="Read each unit before choosing its media.",
        status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "route.get_generation_unit_context", "status": "SUCCEEDED",
                "output": {"body": f"UNIT_BODY_{unit}"}, "artifact_refs": []},
    ) for sequence, unit in enumerate(["first", "second"], 1)]
    turns.append(SpecialistTurnRecord(
        id="save-first-route", sequence=3, tool_name="route.plan_h3",
        arguments={"project_id": "project-units", "request": {
            "generation_plan_version_id": "plan-v1", "unit_ids": ["first"],
        }},
        reason="The first route is saved; the second still needs its inputs.",
        status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "route.plan_h3", "status": "SUCCEEDED", "output": {}, "artifact_refs": []},
    ))
    state = _specialist_working_set(WorkOrder(
        id="work-units", parent_run_id="run-units", sender="production-manager",
        recipient_role="video-compiler", objective="Prepare both units.",
        why_now="Both are ready.", specialist_turns=turns,
    ), [], read_tool_ids=frozenset({"route.get_generation_unit_context"}))
    value = state.model_dump_json()
    assert "UNIT_BODY_first" not in value
    assert value.count("UNIT_BODY_second") == 1


@pytest.mark.parametrize(
    "write_tool_name",
    ["artifact.patch_asset_plan", "artifact.replace_asset_plan"],
)
def test_specialist_working_set_drops_read_body_superseded_by_artifact_version(
    write_tool_name: str,
) -> None:
    old_ref: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "artifact_type": "asset_plan",
        "artifact_id": "asset-plan-document",
        "version": 1,
    }
    new_ref: dict[str, JsonValue] = {**old_ref, "version": 2}
    common_envelope: dict[str, JsonValue] = {
        "status": "SUCCEEDED",
        "warnings": [],
        "error": None,
    }
    turns = [
        SpecialistTurnRecord(
            id="turn-full-plan-v1",
            sequence=1,
            tool_name="artifact.get_asset_plan",
            arguments={"project_id": "project-assets"},
            reason="Read the exact current AssetPlan.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                **common_envelope,
                "tool_id": "artifact.get_asset_plan",
                "output": {
                    "asset_plan": {
                        "items": [
                            {
                                "id": "character",
                                "brief": (
                                    "STALE_COMPLETE_PLAN_BODY_MUST_BE_DROPPED" * 100
                                ),
                            }
                        ]
                    }
                },
                "artifact_refs": [old_ref],
            },
        ),
        SpecialistTurnRecord(
            id="turn-write-plan-v2",
            sequence=2,
            tool_name=write_tool_name,
            arguments={
                "project_id": "project-assets",
                "request": {"base_version_id": "asset-plan-version-1"},
            },
            reason="Persist the new AssetPlan version.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                **common_envelope,
                "tool_id": write_tool_name,
                "output": {
                    "project_id": "project-assets",
                    "asset_plan_identity": {
                        "document_id": "asset-plan-document",
                        "version_id": "asset-plan-version-2",
                        "version": 2,
                    },
                },
                "artifact_refs": [new_ref],
            },
        ),
        SpecialistTurnRecord(
            id="turn-plan-index-v2",
            sequence=3,
            tool_name="artifact.get_asset_plan_index",
            arguments={"project_id": "project-assets"},
            reason="Read the current lightweight inventory.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                **common_envelope,
                "tool_id": "artifact.get_asset_plan_index",
                "output": {
                    "asset_plan_identity": {
                        "document_id": "asset-plan-document",
                        "version_id": "asset-plan-version-2",
                        "version": 2,
                    },
                    "items": [{"id": "character", "name": "Lin Yi"}],
                },
                "artifact_refs": [new_ref],
            },
        ),
        SpecialistTurnRecord(
            id="turn-work-slice-v2",
            sequence=4,
            tool_name="artifact.get_asset_work_context",
            arguments={
                "project_id": "project-assets",
                "scope": {
                    "mode": "WORK_ITEM",
                    "work_item_ids": ["character-front"],
                },
            },
            reason="Read the exact bounded work context.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                **common_envelope,
                "tool_id": "artifact.get_asset_work_context",
                "output": {
                    "asset_plan_identity": {
                        "document_id": "asset-plan-document",
                        "version_id": "asset-plan-version-2",
                        "version": 2,
                    },
                    "items": [{"id": "character", "work_items": []}],
                },
                "artifact_refs": [new_ref],
            },
        ),
    ]

    working_set = _specialist_working_set(
        WorkOrder(
            id=f"work-order-{write_tool_name}",
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-worker",
            objective="Continue from the current AssetPlan version.",
            why_now="The prior version has been superseded.",
            specialist_turns=turns,
        ),
        [],
        read_tool_ids=frozenset(
            {
                "artifact.get_asset_plan",
                "artifact.get_asset_plan_index",
                "artifact.get_asset_work_context",
            }
        ),
        write_tool_ids=frozenset({write_tool_name}),
    ).model_dump(mode="json")
    working_set_json = json.dumps(working_set, ensure_ascii=False)

    assert "STALE_COMPLETE_PLAN_BODY_MUST_BE_DROPPED" not in working_set_json
    assert [
        item["tool_name"]
        for item in working_set["read_observations"]  # type: ignore[union-attr]
    ] == ["artifact.get_asset_plan_index"]
    checkpoint_by_tool = {
        item["tool_name"]: item
        for item in working_set["tool_checkpoints"]  # type: ignore[union-attr]
    }
    assert checkpoint_by_tool["artifact.get_asset_plan"]["artifact_refs"] == [
        old_ref
    ]
    assert checkpoint_by_tool[write_tool_name]["artifact_refs"] == [new_ref]
    assert working_set["artifact_refs"] == [new_ref]
    assert working_set["latest_handoff"]["request"]["tool_name"] == (  # type: ignore[index]
        "artifact.get_asset_work_context"
    )


def test_specialist_working_set_keeps_best_unresolved_write_repair_base() -> None:
    complete_snapshot: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "request": {
            "base_version_id": "asset-plan-v1",
            "asset_plan": {
                "id": "asset-plan-v2",
                "items": [
                    {
                        "id": "item-calculator",
                        "work_items": [
                            {
                                "id": "work-calculator",
                                "served_clip_group_ids": ["clip-group-7"],
                                "prompt": "CORRECTION_BASE_MUST_REMAIN_EXACT",
                            }
                        ],
                    }
                ],
            },
        },
    }
    turns = [
        SpecialistTurnRecord(
            id="turn-domain-conflict",
            sequence=1,
            tool_name="artifact.replace_asset_plan",
            arguments=complete_snapshot,
            reason="Replace the complete asset plan snapshot.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": "FAILED",
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {
                    "kind": "WorkflowConflictError",
                    "message": "The calculator item omits clip-group-7.",
                },
            },
        ),
        SpecialistTurnRecord(
            id="turn-malformed-retry",
            sequence=2,
            tool_name="artifact.replace_asset_plan",
            arguments={
                "project_id": "project-assets",
                "retry_from_persisted_failed_request": {"source_sequence": 1},
            },
            reason="Retry the prior replacement.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": "INVALID_ARGUMENTS",
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {
                    "kind": "ValidationError",
                    "message": "request is required and retry wrapper is forbidden.",
                },
            },
        ),
    ]
    work_order = WorkOrder(
        id="work-order-repair",
        parent_run_id="run-assets",
        sender="production-manager",
        recipient_role="asset-worker",
        objective="Replace the current asset plan.",
        why_now="The current plan is stale.",
        specialist_turns=turns,
    )

    working_set = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({"artifact.replace_asset_plan"}),
    ).model_dump(mode="json")
    working_set_json = json.dumps(working_set, ensure_ascii=False)

    assert working_set["latest_handoff"]["sequence"] == 2  # type: ignore[index]
    assert working_set["repair_tool_schema_ids"] == [
        "artifact.replace_asset_plan"
    ]
    assert len(working_set["pending_tool_repairs"]) == 1
    repair = working_set["pending_tool_repairs"][0]  # type: ignore[index]
    assert repair["source_sequence"] == 1
    assert repair["attempt_ref"]["attempt_id"] == "turn-domain-conflict"
    assert repair["request_identifiers"] == {
        "project_id": "project-assets",
        "request": {"base_version_id": "asset-plan-v1"},
    }
    assert repair["error"]["kind"] == "WorkflowConflictError"
    assert "CORRECTION_BASE_MUST_REMAIN_EXACT" not in working_set_json
    assert working_set["latest_handoff"]["attempt_ref"]["attempt_id"] == (  # type: ignore[index]
        "turn-malformed-retry"
    )
    assert working_set["latest_handoff"]["request"]["arguments"] == {  # type: ignore[index]
        "project_id": "project-assets"
    }

    work_order.specialist_turns.append(
        SpecialistTurnRecord(
            id="turn-corrected",
            sequence=3,
            tool_name="artifact.replace_asset_plan",
            arguments=complete_snapshot,
            reason="Submit the corrected complete snapshot.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.replace_asset_plan",
                "status": "SUCCEEDED",
                "output": {"version_id": "asset-plan-v2"},
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        )
    )
    resolved = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({"artifact.replace_asset_plan"}),
    ).model_dump(mode="json")

    assert resolved["pending_tool_repairs"] == []
    assert resolved["repair_tool_schema_ids"] == []


def test_specialist_working_set_successful_retry_resolves_source_lineage() -> None:
    work_order_id = "work-order-successful-asset-plan-retry"
    source_ref: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "agent_run_id": "run-assets",
        "work_order_id": work_order_id,
        "attempt_id": "turn-failed-create",
        "tool_name": "artifact.create_asset_plan",
        "turn_status": "COMPLETED",
        "outcome_status": "INVALID_ARGUMENTS",
        "has_arguments": True,
        "has_result": True,
    }
    turns = [
        SpecialistTurnRecord(
            id="turn-failed-create",
            sequence=1,
            tool_name="artifact.create_asset_plan",
            arguments={
                "project_id": "project-assets",
                "request": {"asset_plan": {"marker": "FULL_DRAFT"}},
            },
            reason="Create the complete AssetPlan candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.create_asset_plan",
                "status": "INVALID_ARGUMENTS",
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {"kind": "ValidationError", "message": "bad prompt"},
            },
        ),
        SpecialistTurnRecord(
            id="turn-successful-retry",
            sequence=2,
            tool_name="artifact.retry_asset_plan_attempt",
            arguments={
                "project_id": "project-assets",
                "attempt_ref": source_ref,
                "patches": [
                    {
                        "item_id": "item-hero",
                        "work_item_id": "work-hero-master",
                        "op": "replace",
                        "path": "/final_prompt",
                        "value": "fixed",
                    }
                ],
            },
            reason="Patch only the invalid prompt and retry.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.retry_asset_plan_attempt",
                "status": "SUCCEEDED",
                "output": {"operation": "CREATE"},
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        ),
    ]

    working_set = _specialist_working_set(
        WorkOrder(
            id=work_order_id,
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-worker",
            objective="Repair the failed AssetPlan candidate.",
            why_now="A compact retry can preserve the exact draft.",
            specialist_turns=turns,
        ),
        [],
        project_id="project-assets",
        write_tool_ids=frozenset(
            {
                "artifact.create_asset_plan",
                "artifact.retry_asset_plan_attempt",
            }
        ),
    ).model_dump(mode="json")

    assert working_set["pending_tool_repairs"] == []
    assert working_set["repair_tool_schema_ids"] == []


def test_specialist_working_set_failed_retry_keeps_only_latest_lineage_tip() -> None:
    work_order_id = "work-order-chained-asset-plan-retry"

    def attempt_ref(
        *,
        attempt_id: str,
        tool_name: str,
        outcome_status: str,
    ) -> dict[str, JsonValue]:
        return {
            "project_id": "project-assets",
            "agent_run_id": "run-assets",
            "work_order_id": work_order_id,
            "attempt_id": attempt_id,
            "tool_name": tool_name,
            "turn_status": "COMPLETED",
            "outcome_status": outcome_status,
            "has_arguments": True,
            "has_result": True,
        }

    source_ref = attempt_ref(
        attempt_id="turn-failed-create",
        tool_name="artifact.create_asset_plan",
        outcome_status="INVALID_ARGUMENTS",
    )
    first_retry_ref = attempt_ref(
        attempt_id="turn-first-failed-retry",
        tool_name="artifact.retry_asset_plan_attempt",
        outcome_status="FAILED",
    )
    failed_result: dict[str, JsonValue] = {
        "tool_id": "artifact.retry_asset_plan_attempt",
        "status": "FAILED",
        "output": None,
        "artifact_refs": [],
        "warnings": [],
        "error": {"kind": "ProductionManagerError", "message": "still invalid"},
    }
    turns = [
        SpecialistTurnRecord(
            id="turn-failed-create",
            sequence=1,
            tool_name="artifact.create_asset_plan",
            arguments={
                "project_id": "project-assets",
                "request": {"asset_plan": {"marker": "FULL_DRAFT"}},
            },
            reason="Create the complete AssetPlan candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                **failed_result,
                "tool_id": "artifact.create_asset_plan",
                "status": "INVALID_ARGUMENTS",
            },
        ),
        SpecialistTurnRecord(
            id="turn-first-failed-retry",
            sequence=2,
            tool_name="artifact.retry_asset_plan_attempt",
            arguments={
                "project_id": "project-assets",
                "attempt_ref": source_ref,
                "patches": [{"op": "replace", "path": "/notes", "value": []}],
            },
            reason="Apply the first local correction.",
            status=SpecialistTurnStatus.COMPLETED,
            result=failed_result,
        ),
        SpecialistTurnRecord(
            id="turn-latest-failed-retry",
            sequence=3,
            tool_name="artifact.retry_asset_plan_attempt",
            arguments={
                "project_id": "project-assets",
                "attempt_ref": first_retry_ref,
                "patches": [{"op": "replace", "path": "/notes", "value": []}],
            },
            reason="Apply the next local correction.",
            status=SpecialistTurnStatus.COMPLETED,
            result=failed_result,
        ),
        SpecialistTurnRecord(
            id="turn-read-after-retry",
            sequence=4,
            tool_name="artifact.get_asset_plan_index",
            arguments={"project_id": "project-assets"},
            reason="Inspect the current persisted plan identity.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.get_asset_plan_index",
                "status": "SUCCEEDED",
                "output": {"items": []},
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        ),
    ]

    working_set = _specialist_working_set(
        WorkOrder(
            id=work_order_id,
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-worker",
            objective="Repair the failed AssetPlan candidate.",
            why_now="Each retry supersedes its exact ancestor candidate.",
            specialist_turns=turns,
        ),
        [],
        project_id="project-assets",
        read_tool_ids=frozenset({"artifact.get_asset_plan_index"}),
        write_tool_ids=frozenset(
            {
                "artifact.create_asset_plan",
                "artifact.retry_asset_plan_attempt",
            }
        ),
    ).model_dump(mode="json")

    assert working_set["repair_tool_schema_ids"] == [
        "artifact.retry_asset_plan_attempt"
    ]
    assert len(working_set["pending_tool_repairs"]) == 1
    repair = working_set["pending_tool_repairs"][0]  # type: ignore[index]
    assert repair["attempt_ref"]["attempt_id"] == "turn-latest-failed-retry"
    assert "turn-failed-create" not in json.dumps(working_set)
    assert repair["source_sequence"] == 3


def test_specialist_working_set_drops_loaded_repair_after_alternative_write() -> None:
    failed_arguments: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "request": {
            "base_version_id": "asset-plan-v1",
            "asset_plan": {"marker": "FAILED_FULL_BODY_MUST_NOT_REPLAY"},
        },
    }
    attempt_ref: dict[str, JsonValue] = {
        "project_id": "project-assets",
        "agent_run_id": "run-assets",
        "work_order_id": "work-order-alternative-repair",
        "attempt_id": "turn-failed-replace",
        "tool_name": "artifact.replace_asset_plan",
        "turn_status": "COMPLETED",
        "outcome_status": "FAILED",
        "has_arguments": True,
        "has_result": True,
    }
    failed_result: dict[str, JsonValue] = {
        "tool_id": "artifact.replace_asset_plan",
        "status": "FAILED",
        "output": None,
        "artifact_refs": [],
        "warnings": [],
        "error": {
            "kind": "WorkflowConflictError",
            "message": "Use a targeted patch for the current version.",
        },
    }
    turns = [
        SpecialistTurnRecord(
            id="turn-failed-replace",
            sequence=1,
            tool_name="artifact.replace_asset_plan",
            arguments=failed_arguments,
            reason="Try a complete replacement.",
            status=SpecialistTurnStatus.COMPLETED,
            result=failed_result,
        ),
        SpecialistTurnRecord(
            id="turn-load-repair",
            sequence=2,
            tool_name="agent.get_tool_attempt",
            arguments={"attempt_ref": attempt_ref, "payload": "BOTH"},
            reason="Load the exact failed candidate once.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "agent.get_tool_attempt",
                "status": "SUCCEEDED",
                "output": {
                    "attempt_ref": attempt_ref,
                    "reason": "Try a complete replacement.",
                    "arguments": failed_arguments,
                    "result": failed_result,
                },
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        ),
        SpecialistTurnRecord(
            id="turn-successful-patch",
            sequence=3,
            tool_name="artifact.patch_asset_plan",
            arguments={
                "project_id": "project-assets",
                "request": {"base_version_id": "asset-plan-v1"},
            },
            reason="Apply the semantic repair with a targeted patch.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.patch_asset_plan",
                "status": "SUCCEEDED",
                "output": {"version_id": "asset-plan-v2"},
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        ),
    ]
    working_set = _specialist_working_set(
        WorkOrder(
            id="work-order-alternative-repair",
            parent_run_id="run-assets",
            sender="production-manager",
            recipient_role="asset-worker",
            objective="Repair the current AssetPlan.",
            why_now="The complete replacement was rejected.",
            specialist_turns=turns,
        ),
        [],
        project_id="project-assets",
        write_tool_ids=frozenset(
            {"artifact.replace_asset_plan", "artifact.patch_asset_plan"}
        ),
    ).model_dump(mode="json")

    assert working_set["pending_tool_repairs"] == []
    assert working_set["repair_tool_schema_ids"] == []
    assert "FAILED_FULL_BODY_MUST_NOT_REPLAY" not in json.dumps(
        working_set,
        ensure_ascii=False,
    )
    assert working_set["latest_handoff"]["request"]["arguments"] == {  # type: ignore[index]
        "project_id": "project-assets",
        "request": {"base_version_id": "asset-plan-v1"},
    }
    assert "output" not in working_set["latest_handoff"]["result"]  # type: ignore[operator,index]


def test_specialist_working_set_keeps_multiple_selected_attempts_until_write() -> None:
    def attempt_ref(name: str, tool_name: str) -> ToolAttemptRef:
        return ToolAttemptRef(
            project_id="project-assets",
            agent_run_id="run-assets",
            work_order_id=f"source-{name}",
            attempt_id=f"attempt-{name}",
            tool_name=tool_name,
            turn_status=SpecialistTurnStatus.COMPLETED,
            outcome_status=ToolCallStatus.FAILED.value,
            has_arguments=True,
            has_result=True,
        )

    first_ref = attempt_ref("first", "artifact.patch_asset_plan")
    second_ref = attempt_ref("second", "artifact.retry_asset_plan_attempt")

    def lookup_turn(
        *,
        sequence: int,
        ref: ToolAttemptRef,
        marker: str,
    ) -> SpecialistTurnRecord:
        return SpecialistTurnRecord(
            id=f"lookup-{sequence}",
            sequence=sequence,
            tool_name="agent.get_tool_attempt",
            arguments={
                "attempt_ref": ref.model_dump(mode="json"),
                "payload": "BOTH",
            },
            reason="Load one explicitly handed repair candidate.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "agent.get_tool_attempt",
                "status": ToolCallStatus.SUCCEEDED.value,
                "output": {
                    "attempt_ref": ref.model_dump(mode="json"),
                    "arguments": {"marker": marker},
                    "result": {"status": ToolCallStatus.FAILED.value},
                },
                "artifact_refs": [],
                "warnings": [],
                "error": None,
            },
        )

    work_order = WorkOrder(
        id="work-order-attempt-whiteboard",
        parent_run_id="run-assets",
        sender="production-manager",
        recipient_role="asset-worker",
        objective="Repair two related failed attempts.",
        why_now="Both exact outcomes are needed for one semantic correction.",
        specialist_turns=[
            lookup_turn(sequence=1, ref=first_ref, marker="FIRST_EXACT"),
            lookup_turn(sequence=2, ref=second_ref, marker="SECOND_EXACT"),
        ],
    )
    working_set = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({"artifact.patch_asset_plan"}),
    ).model_dump(mode="json")

    assert "SECOND_EXACT" in json.dumps(working_set["latest_handoff"])
    observations = working_set["tool_attempt_observations"]
    assert len(observations) == 1  # type: ignore[arg-type]
    assert observations[0]["attempt_ref"]["attempt_id"] == "attempt-first"  # type: ignore[index]
    assert "FIRST_EXACT" in json.dumps(observations)

    # Re-reading an already selected attempt replaces its identity slot instead
    # of growing history, while the other selected payload remains available.
    work_order.specialist_turns.append(
        lookup_turn(sequence=3, ref=first_ref, marker="FIRST_EXACT_REFRESHED")
    )
    repeated = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({"artifact.patch_asset_plan"}),
    ).model_dump(mode="json")
    assert "FIRST_EXACT_REFRESHED" in json.dumps(repeated["latest_handoff"])
    repeated_observations = repeated["tool_attempt_observations"]
    assert len(repeated_observations) == 1  # type: ignore[arg-type]
    assert repeated_observations[0]["attempt_ref"]["attempt_id"] == (  # type: ignore[index]
        "attempt-second"
    )
    assert "SECOND_EXACT" in json.dumps(repeated_observations)

    # Any subsequent write consumes the explicitly loaded candidates. If it
    # fails, that persisted write is now the addressable repair base.
    work_order.specialist_turns.append(
        SpecialistTurnRecord(
            id="attempt-local-patch",
            sequence=4,
            tool_name="artifact.patch_asset_plan",
            arguments={"project_id": "project-assets", "request": {}},
            reason="Apply the combined correction.",
            status=SpecialistTurnStatus.COMPLETED,
            result={
                "tool_id": "artifact.patch_asset_plan",
                "status": ToolCallStatus.FAILED.value,
                "output": None,
                "artifact_refs": [],
                "warnings": [],
                "error": {"kind": "Error", "message": "Repair this write."},
            },
        )
    )
    after_write = _specialist_working_set(
        work_order,
        [],
        project_id="project-assets",
        write_tool_ids=frozenset({"artifact.patch_asset_plan"}),
    ).model_dump(mode="json")
    assert after_write["tool_attempt_observations"] == []
    assert "FIRST_EXACT" not in json.dumps(after_write)
    assert "SECOND_EXACT" not in json.dumps(after_write)


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

    history = specialist_tool_history_for_trace(work_order)
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

    history = specialist_tool_history_for_trace(work_order)
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


def _persist_pending_revision_verifier(
    service: ShortDramaService,
    *,
    project_id: str,
    owner_agent_run_id: str,
) -> str:
    document = service.get_project(project_id).document
    started = service.start_script_revision(
        project_id,
        owner_agent_run_id=owner_agent_run_id,
        instruction="保留已成立主线，修复 Verifier 定位的局部回归。",
        base_script_version_id=document.project.current_script_version_id,
    ).run
    started.status = PlanningRunStatus.NEEDS_ATTENTION
    started.current_step = PlanningRunStep.REVISION_VERIFIER
    started.latest_revision_verification = StoryRevisionVerification(
        accepted=False,
        summary="修订稿仍有一处可定位的状态回归。",
        satisfied_issue_ids=[],
        satisfied_issue_indices=[],
        violations=[
            StoryRevisionViolation(
                issue_id="STATE-REGRESSION-1",
                category="STATE_REGRESSION",
                scene_sequences=[1],
                observation="第 1 场同一道具被同时写成已移除与仍在场。",
                repair_instruction="统一道具移除时点，保留其他剧情。",
            )
        ],
        duration_fit_assessment=DurationFitAssessment(
            estimated_range_seconds=DurationRange(
                minimum_seconds=document.project.target_duration_seconds,
                maximum_seconds=document.project.target_duration_seconds,
            ),
            alignment=DurationAlignment.ALIGNED,
            pressure_points=[],
            concurrent_performance=[],
            resolution=DurationMismatchResolution.REVISE_SCRIPT,
            rationale="本次 handoff 只涉及可定位剧情回归。",
        ),
    )
    started.error_message = "等待 AgentRun Leader 处理精确 Verifier 结果。"
    document = service.get_project(project_id).document
    assert document.planning is not None
    document.planning.status = PlanningRunStatus.NEEDS_ATTENTION
    service.repository.save_planning_run_with_project(started, document)
    return started.id


def _persist_pending_script_verifier(
    service: ShortDramaService,
    *,
    project_id: str,
    owner_agent_run_id: str,
) -> str:
    document = service.get_project(project_id).document
    started = service.start_script_revision(
        project_id,
        owner_agent_run_id=owner_agent_run_id,
        instruction="保留当前主线，让初次 Script Verifier 冷读整稿。",
        base_script_version_id=document.project.current_script_version_id,
    ).run
    started.status = PlanningRunStatus.NEEDS_ATTENTION
    started.current_step = PlanningRunStep.SCRIPT_VERIFIER
    started.review_report = ScriptReviewReport(
        summary="初次冷读发现一处可定位的因果缺口。",
        strengths=["主角优势已建立"],
        issues=[
            ScriptReviewIssue(
                issue_id="INITIAL-CAUSAL-GAP-1",
                category="CAUSAL_LOGIC",
                severity=ScriptReviewSeverity.MEDIUM,
                owner=ReviewRepairOwner.STORY_ARCHITECT,
                scene_sequences=[1],
                observation="开场行动缺少一个可见后果。",
                recommendation="保留其他剧情，只补齐该因果。",
            )
        ],
        performance_timing_assessment="当前时长可承载局部修订。",
        duration_fit_assessment=DurationFitAssessment(
            estimated_range_seconds=DurationRange(
                minimum_seconds=document.project.target_duration_seconds,
                maximum_seconds=document.project.target_duration_seconds,
            ),
            alignment=DurationAlignment.ALIGNED,
            pressure_points=[],
            concurrent_performance=[],
            resolution=DurationMismatchResolution.REVISE_SCRIPT,
            rationale="本次 handoff 只涉及可定位剧情问题。",
        ),
        requires_revision=True,
    )
    started.review_disposition = None
    started.latest_revision_verification = None
    started.error_message = "等待 AgentRun Leader 处理初次 Script Verifier 的精确结果。"
    document = service.get_project(project_id).document
    assert document.planning is not None
    document.planning.status = PlanningRunStatus.NEEDS_ATTENTION
    service.repository.save_planning_run_with_project(started, document)
    return started.id


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
                        production_prompt_stage=ProductionPromptStage.STORYBOARD,
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
    output_contracts = json.loads(client.inputs[0])["output_contracts"]
    assert planner_context["unattended"] is False
    assert planner_context["video_execution_target"] == "REAL_H3"
    assert "execution_target" not in planner_context
    assert planner_context["render_export"] is False
    assert output_contracts["plan"]["title"] == "AgentPlanProposal"
    assert (
        output_contracts["tool_schema_request"]["title"]
        == "AgentToolSchemaRequest"
    )
    instructions = client.instructions[0]
    assert "根据 ContextPack 中真实已有的内容、任务结果和依赖制定最小可执行计划" in instructions
    assert "当前 objective 表示这次交付" in instructions
    assert "不得用关键词匹配代替语义判断" in instructions
    assert "分工按真实职责和依赖" in instructions
    assert "artifact.get_asset_planning_context" in instructions
    assert "artifact.get_asset_plan_index" in instructions
    assert "artifact.get_asset_work_context" in instructions
    assert "execution_contract 是对当前内容" in instructions
    assert "不是版本号门禁" in instructions
    assert "不得自称该切片可执行" in instructions
    assert "不再叠加 project.get 和完整资产计划" in instructions
    assert "不在 Manager 内重复一套全流程手册" in instructions
    assert "video_execution_target 只选择视频生成执行器" in instructions
    assert "图片能力必须由 image.capability 的实际结果独立判断" in instructions
    assert "不创造未来的 document/version/work-item ID" in instructions
    assert "available_tools 只是紧凑目录" in instructions
    assert "当前制片技能：AI 短剧（本地格式适配）" in instructions
    assert "精简可用阶段线索" in instructions
    assert "shot.append_storyboard_ledger_segment" not in instructions
    assert "REFERENCE_GENERATE" not in instructions
    assert "CRITIC" not in instructions
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
            "selected_method_skill_ids": ["method.brand_promo"],
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
        "method.brand_promo"
    ]
    assert run.snapshot.current_plan.method_selection_rationale is not None
    assert "method.brand_promo" in run.snapshot.run.active_skill_ids
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


def test_language_planner_repairs_unknown_method_skill_before_persisting(
    tmp_path: Path,
) -> None:
    project_service = _service(tmp_path)
    project = project_service.initialize_fixture().document.project
    available_method_ids = {
        descriptor.id for descriptor in production_method_skill_catalog()
    }
    corrected_method_id = "method.brand_promo"
    assert corrected_method_id in available_method_ids
    assert "format.ai-short-drama" not in available_method_ids

    def planner_response(method_skill_ids: list[str]) -> str:
        return json.dumps(
            {
                "summary": "Select a registered production method before continuing.",
                "selected_method_skill_ids": method_skill_ids,
                "method_selection_rationale": (
                    "Use the available method catalog rather than a format Skill ID."
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

    client = SequencedLanguageClient(
        [
            planner_response(["format.ai-short-drama"]),
            planner_response([corrected_method_id]),
        ]
    )
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
        AgentRunCreateRequest(objective="Inspect the project with a known method."),
    )

    assert len(client.inputs) == 2
    first_payload, corrected_payload = [
        json.loads(value) for value in client.inputs
    ]
    assert "plan_validation_feedback" not in first_payload
    feedback = corrected_payload["plan_validation_feedback"]
    assert feedback["kind"] == "UNKNOWN_PRODUCTION_METHOD_SKILL"
    assert set(feedback["available_method_skill_ids"]) == available_method_ids
    assert "format.ai-short-drama" not in feedback["available_method_skill_ids"]

    current_plan = run.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.selected_method_skill_ids == [corrected_method_id]
    assert (
        len(service.agent_runtime.store.list_plan_revisions(run.snapshot.run.id))
        == 1
    )


def test_language_planner_repairs_unresolved_plan_artifact_refs_before_persisting(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    bible = next(
        item
        for item in aggregate.document.bible_versions
        if item.id == aggregate.document.project.current_bible_version_id
    )
    canonical_ref = {
        "project_id": project_id,
        "artifact_type": "bible_version",
        "artifact_id": bible.id,
        "version": bible.version,
    }
    invented_ref = {
        **canonical_ref,
        "artifact_type": "project_bible_version",
    }

    def director_plan(ref: dict[str, JsonValue]) -> str:
        return json.dumps(
            {
                "summary": "Delegate one bounded storyboard decision.",
                "steps": [
                    {
                        "id": "direct-storyboard-range",
                        "title": "Direct storyboard range",
                        "kind": "DELEGATION",
                        "production_skill_role": "DIRECTOR",
                        "assigned_agent": "storyboard-director",
                        "instructions": "Read the exact Bible version and direct the range.",
                        "arguments": {
                            "why_now": "The source versions are confirmed.",
                            "adjacent_boundaries": [
                                {
                                    "relationship": "incoming-story-state",
                                    "artifact_refs": [ref],
                                    "guidance": "Preserve the confirmed story world.",
                                }
                            ],
                        },
                        "depends_on": [],
                        "artifact_refs": [ref],
                    }
                ],
                "goal_complete": False,
            }
        )

    client.responses.extend(
        [
            director_plan(invented_ref),
            director_plan(canonical_ref),
        ]
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )

    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Use the confirmed sources to direct the next storyboard range."
        ),
    )

    assert len(client.inputs) == 2
    first_payload, corrected_payload = [
        json.loads(value) for value in client.inputs
    ]
    assert "plan_validation_feedback" not in first_payload
    feedback = corrected_payload["plan_validation_feedback"]
    assert feedback["kind"] == "UNAVAILABLE_ARTIFACT_REF"
    assert {
        issue["path"] for issue in feedback["issues"]
    } == {
        "steps[0].artifact_refs[0]",
        "steps[0].arguments.adjacent_boundaries[0].artifact_refs[0]",
    }
    assert all(
        issue["received"]["artifact_type"] == "project_bible_version"
        for issue in feedback["issues"]
    )
    assert canonical_ref in feedback["available_artifact_refs"]
    assert "ContextPack" in feedback["instruction"]

    current_plan = run.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.revision == 1
    assert (
        len(service.agent_runtime.store.list_plan_revisions(run.snapshot.run.id))
        == 1
    )
    step = current_plan.steps[0]
    assert [ref.model_dump(mode="json") for ref in step.artifact_refs] == [
        canonical_ref
    ]
    adjacent = step.arguments["adjacent_boundaries"]
    assert isinstance(adjacent, list)
    boundary = adjacent[0]
    assert isinstance(boundary, dict)
    assert boundary["artifact_refs"] == [canonical_ref]


def test_language_planner_repairs_unavailable_inherited_decision_ids_before_persisting(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient([])
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
            objective="Delegate the next bounded scene decision.",
            initial_plan=AgentPlanProposal(
                summary="Persist one checkpoint before delegation.",
                steps=[
                    PlanStep(
                        id="checkpoint",
                        title="Record current state",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Record the current state.",
                    )
                ],
            ),
        ),
    )
    visible_decision = service.agent_runtime.remember_decision(
        project_id=project.id,
        run_id=run.snapshot.run.id,
        statement="Keep the story inside the school canteen.",
        rationale="The user requested a tightly connected single-location story.",
    )

    def delegation_plan(decision_ids: list[str]) -> str:
        return json.dumps(
            {
                "summary": "Delegate one bounded scene decision.",
                "steps": [
                    {
                        "id": "write-scene",
                        "title": "Write the next scene",
                        "kind": "DELEGATION",
                        "production_skill_role": "WRITER",
                        "assigned_agent": "scene-writer",
                        "instructions": "Write the next bounded scene.",
                        "arguments": {
                            "why_now": "The location decision is confirmed.",
                            "inherited_decision_ids": decision_ids,
                        },
                        "depends_on": [],
                        "artifact_refs": [],
                    }
                ],
                "goal_complete": False,
            }
        )

    client.responses.extend(
        [
            delegation_plan(
                [f"{visible_decision.id}:decision-invented", "decision-unknown"]
            ),
            delegation_plan([visible_decision.id]),
        ]
    )

    advanced = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(max_actions=1),
    )

    assert len(client.inputs) == 2
    first_payload, corrected_payload = [
        json.loads(value) for value in client.inputs
    ]
    assert "plan_validation_feedback" not in first_payload
    feedback = corrected_payload["plan_validation_feedback"]
    assert feedback["kind"] == "UNAVAILABLE_DECISION_ID"
    assert feedback["available_decision_ids"] == [visible_decision.id]
    assert {
        issue["path"] for issue in feedback["issues"]
    } == {
        "steps[0].arguments.inherited_decision_ids[0]",
        "steps[0].arguments.inherited_decision_ids[1]",
    }
    assert {issue["received"] for issue in feedback["issues"]} == {
        f"{visible_decision.id}:decision-invented",
        "decision-unknown",
    }
    assert "concatenate" in feedback["instruction"]

    current_plan = advanced.run.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.revision == 2
    assert current_plan.steps[0].arguments["inherited_decision_ids"] == [
        visible_decision.id
    ]
    assert all(
        action.error_kind != "InvalidContextManifest"
        for action in advanced.run.snapshot.actions
    )


def test_language_planner_repairs_invalid_plan_contract_before_persisting(
    tmp_path: Path,
) -> None:
    invalid_plan = {
        "summary": "Delegate one bounded storyboard decision.",
        "steps": [
            {
                "id": "direct-storyboard-range",
                "title": "Direct storyboard range",
                "kind": "DELEGATION",
                "production_skill_role": "DIRECTOR",
                "instructions": "Direct the next storyboard range.",
                "arguments": {},
                "depends_on": [],
                "artifact_refs": [],
            }
        ],
        "goal_complete": False,
    }
    corrected_plan = {
        **invalid_plan,
        "steps": [
            {
                **invalid_plan["steps"][0],
                "assigned_agent": "storyboard-director",
            }
        ],
    }
    client = SequencedLanguageClient(
        [
            json.dumps(invalid_plan),
            json.dumps(corrected_plan),
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
        AgentRunCreateRequest(objective="Direct the next storyboard range."),
    )

    assert len(client.inputs) == 2
    first_payload, corrected_payload = [
        json.loads(value) for value in client.inputs
    ]
    assert "plan_validation_feedback" not in first_payload
    feedback = corrected_payload["plan_validation_feedback"]
    assert feedback["kind"] == "INVALID_PLAN_CONTRACT"
    assert any(
        issue["loc"] == ["steps", 0, "assigned_agent"]
        for issue in feedback["issues"]
    )

    current_plan = run.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.revision == 1
    stored_revisions = service.agent_runtime.store.list_plan_revisions(
        run.snapshot.run.id
    )
    assert len(stored_revisions) == 1
    assert current_plan.steps[0].assigned_agent == "storyboard-director"


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
        and "description" not in descriptor
        for descriptor in first_payload["available_tools"]
    )
    assert set(second_payload["tool_schemas"]) == {"project.get"}
    assert "description" in second_payload["tool_schemas"]["project.get"]
    assert "input_schema" in second_payload["tool_schemas"]["project.get"]
    assert "output_schema" not in second_payload["tool_schemas"]["project.get"]


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
    instructions = client.instructions[0]
    assert "完成 WorkOrder 指定的目标与工件" in instructions
    assert "按当前角色和阶段使用必要方法" in instructions
    assert "不读取未来阶段的整套手册" in instructions
    assert "tool_name、reason、arguments" in instructions
    assert "working_set 是当前可重建工作集，不是历史对话" in instructions
    assert "不叠加 project.get、整份上游工件和当前任务切片" in instructions
    assert "agent.get_tool_attempt" in instructions
    assert "后续写入尝试消费为止" in instructions
    assert "只修正交接结构，不把它当成创作意见" in instructions
    assert "导演的通用职责" in instructions
    assert "shot.append_storyboard_ledger_segment" not in instructions
    assert "第一个 Tool 请求必须是 project.get" not in instructions
    specialist_context = json.loads(client.inputs[0])["context"]
    assert specialist_context["unattended"] is False
    assert "scene-director" in client.inputs[0]

    completed = service.advance_agent_run(
        run.snapshot.run.id,
        AgentRunAdvanceRequest(),
    )
    assert completed.run.snapshot.run.status == AgentRunStatus.COMPLETED


def test_resumed_specialist_reads_latest_user_instructions_with_an_old_work_view(tmp_path: Path) -> None:
    from short_drama.agent_runtime.model_input import ModelInputCompiler
    from short_drama.production_manager import LanguageSpecialistAgent

    service = _service(tmp_path)
    pid = service.initialize_fixture().document.project.id
    run_id, work_order_id, _ = _start_pending_delegation(service, pid)
    old_view = service.get_agent_work_order(work_order_id)
    old_view.work_order.working_notes = "The earlier layout is already complete."
    client = FakeSpecialistLanguageClient()
    agent = LanguageSpecialistAgent(client)
    agent.context_compiler = ModelInputCompiler(service.agent_runtime.store)
    agent.execute(old_view)
    run = service.agent_runtime.store.get_run(run_id)
    assert run is not None
    run.latest_user_instructions = ["The two price labels overlap. Fix their layout and preserve the edit."]
    run.goal_summary = "Finish the film with the corrected opening labels."
    service.agent_runtime.store.save_run(run)
    # Reuse the same WorkOrder view, as during a resumed schema/decision cycle.
    agent.execute(old_view)
    first, resumed = [json.loads(value) for value in client.inputs]
    assert first["goal"]["latest_user_instructions"] == []
    assert resumed["goal"]["latest_user_instructions"] == run.latest_user_instructions
    assert resumed["goal"]["original_request"] == run.objective
    assert resumed["goal"]["current_goal"] == run.goal_summary
    assert resumed["work_order"]["working_notes"] == old_view.work_order.working_notes
    assert client.inputs[-1].count(run.latest_user_instructions[0]) == 1
    assert "优先于旧任务说明" in client.instructions[-1]


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
    assert "progress_entries" not in first_payload
    assert "tool_history" not in first_payload
    progress = first_payload["working_set"]["progress"]
    assert progress["total_events"] == 3
    assert progress["lifecycle"]["sequence"] == 3
    assert progress["lifecycle"]["summary"] == (
        "Specialist work started or resumed."
    )
    assert progress["worker_handoff"]["sequence"] == 2
    assert progress["worker_handoff"]["summary"] == (
        "Loaded the current staging and marked the eyeline for review."
    )
    assert second_payload["working_set"]["progress"] == progress
    assert first_payload["tool_schemas"] == {}
    assert set(first_payload["response_schemas"]) == {
        "final_result",
        "tool_request",
        "tool_schema_request",
    }
    assert list(
        first_payload["response_schemas"]["tool_request"]["properties"]
    ) == ["tool_name", "reason", "arguments", "working_notes"]
    artifact_ref_schema = first_payload["response_schemas"]["final_result"]["$defs"][
        "ArtifactRef"
    ]
    assert artifact_ref_schema["additionalProperties"] is False
    next_owner_schema = first_payload["response_schemas"]["final_result"][
        "properties"
    ]["next_owner"]
    assert next_owner_schema["anyOf"][0]["enum"] == [
        WorkOrderNextOwner.LEADER_REPLAN.value,
        WorkOrderNextOwner.USER_INPUT.value,
    ]
    assert client.max_output_token_requests == [20000, 20000]
    assert all(
        "input_schema" not in descriptor
        and "output_schema" not in descriptor
        and "description" not in descriptor
        for descriptor in first_payload["available_tools"]
    )
    assert set(second_payload["tool_schemas"]) == {"project.get"}
    assert "description" in second_payload["tool_schemas"]["project.get"]
    assert "input_schema" in second_payload["tool_schemas"]["project.get"]
    assert "output_schema" not in second_payload["tool_schemas"]["project.get"]
    persisted_progress = service.list_agent_work_order_progress(work_order_id).entries
    assert [entry.summary for entry in persisted_progress[:3]] == [
        "WorkOrder created: Review the current scene staging.",
        "Loaded the current staging and marked the eyeline for review.",
        "Specialist work started or resumed.",
    ]
    assert [entry.sequence for entry in persisted_progress] == [1, 2, 3, 4]


@pytest.mark.parametrize("failure_kind", ["multiple_json", "completed_questions"])
def test_invalid_specialist_response_protocol_returns_to_same_worker(
    tmp_path: Path,
    failure_kind: str,
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
    invalid_response = (
        json.dumps(corrected_result) + "\n" + json.dumps({"extra": True})
        if failure_kind == "multiple_json" else json.dumps({
            **corrected_result, "unresolved_questions": ["Unverified audio remains"],
        })
    )
    marker = "extra" if failure_kind == "multiple_json" else "Unverified audio remains"
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "requested_tool_schema_ids": ["project.get"],
                    "rationale": "Load the exact read contract before deciding.",
                }
            ),
            invalid_response,
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
    assert marker in failed.pending_protocol_feedback.rejected_response
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
    assert "input_schema" in correction_payload["tool_schemas"]["project.get"]
    assert "output_schema" not in correction_payload["tool_schemas"]["project.get"]
    protocol_feedback = correction_payload["response_protocol_feedback"]
    assert protocol_feedback["loaded_tool_schema_ids"] == ["project.get"]
    assert marker in protocol_feedback["rejected_response"]
    assert "不是剧情、创意或任务拆分意见" in client.instructions[2]
    progress = service.list_agent_work_order_progress(work_order_id).entries
    validator_entries = [
        entry for entry in progress if entry.author == "specialist-response-validator"
    ]
    assert len(validator_entries) == 2
    assert "same Worker" in validator_entries[-1].summary


@pytest.mark.parametrize(
    "specialist_next_owner",
    [
        WorkOrderNextOwner.WORKER_DECISION,
        WorkOrderNextOwner.WORKER_TOOL,
    ],
)
def test_specialist_cannot_claim_manager_owned_worker_state(
    tmp_path: Path,
    specialist_next_owner: WorkOrderNextOwner,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "status": "PARTIAL",
                    "concise_summary": "Continue inside the Worker runtime.",
                    "unresolved_questions": [],
                    "recommended_next_actions": ["Continue the same work."],
                    "next_owner": specialist_next_owner.value,
                }
            ),
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The specialist returned a valid handoff.",
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

    partial = service.execute_agent_work_order(work_order_id)

    assert partial.result.status == AgentResultStatus.PARTIAL
    persisted = service.agent_runtime_store.get_work_order(work_order_id)
    assert persisted is not None
    assert persisted.next_owner == WorkOrderNextOwner.WORKER_DECISION
    assert persisted.pending_protocol_feedback is not None
    ownership_issues = persisted.pending_protocol_feedback.validation_issues[
        "agent_result"
    ]
    assert ownership_issues[0]["type"] == "ValueError"
    assert "Manager-owned states" in str(ownership_issues[0]["msg"])

    completed = service.execute_agent_work_order(work_order_id)

    assert completed.result.status == AgentResultStatus.COMPLETED
    corrected = service.agent_runtime_store.get_work_order(work_order_id)
    assert corrected is not None
    assert corrected.status == DelegationStatus.COMPLETED
    assert corrected.pending_protocol_feedback is None


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
        '{"tool_name":"project.get","arguments":{},"unknown":true}}',
        '{"tool_name":"project.get","arguments":{}}}}',
        '{"tool_name":"project.get","arguments":{"project_id":',
    ],
)
def test_specialist_json_continuation_recovery_stays_narrow(
    payload: str,
) -> None:
    assert _normalize_specialist_response_payload(payload) == payload


def test_specialist_redundant_final_closing_brace_is_recovered() -> None:
    payload = (
        '{"tool_name":"artifact.create_asset_plan","arguments":'
        '{"project_id":"project-1","request":{"asset_plan":{"items":[]}}}}}'
    )

    normalized = _normalize_specialist_response_payload(payload)

    assert json.loads(normalized) == {
        "tool_name": "artifact.create_asset_plan",
        "arguments": {
            "project_id": "project-1",
            "request": {"asset_plan": {"items": []}},
        },
    }


def test_specialist_redundant_final_closing_brace_with_reason_is_recovered() -> None:
    payload = (
        '{"tool_name":"artifact.create_asset_plan","reason":"创建资产计划",'
        '"arguments":{"project_id":"project-1","request":{"asset_plan":{}}}}}'
    )

    normalized = _normalize_specialist_response_payload(payload)

    assert json.loads(normalized) == {
        "tool_name": "artifact.create_asset_plan",
        "reason": "创建资产计划",
        "arguments": {
            "project_id": "project-1",
            "request": {"asset_plan": {}},
        },
    }


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


def test_confirmed_user_decisions_survive_restart_and_automatic_replanning(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture().document.project
    interim = AgentPlanProposal(
        summary="Continue the available work before the next planning decision.",
        steps=[
            PlanStep(
                id="continue",
                title="Continue available work",
                kind=ActionKind.CHECKPOINT,
                assigned_agent="production-manager",
                instructions="Keep the completed work and return to planning.",
            )
        ],
    )
    run_id = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(objective="Finish available assets.", initial_plan=interim),
    ).snapshot.run.id
    statement = "Set the filtered character views aside and continue other assets."
    request = AgentRunReplanRequest(
        reason="The user changed the scope of this experiment.",
        proposed_plan=interim,
        confirmed_decisions=[statement, statement, "  "],
    )
    service.replan_agent_run(run_id, request)
    service.replan_agent_run(run_id, request)

    client = SequencedLanguageClient(
        [json.dumps({"summary": "Available work is finished.", "goal_complete": True, "steps": []})]
    )
    restarted = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    restarted.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    remembered = restarted.production_manager.runtime.store.list_decisions(
        project.id, run_id=run_id
    )
    assert sum(item.statement == statement for item in remembered) == 1
    assert all(item.statement != request.reason for item in remembered)

    result = restarted.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=2))

    assert result.run.snapshot.run.status == AgentRunStatus.COMPLETED
    context = json.loads(client.inputs[0])["context"]
    assert statement in [item["statement"] for item in context["decisions"]]
    assert request.reason not in [item["statement"] for item in context["decisions"]]


def test_external_replan_rejects_unresolved_artifact_ref_before_persisting(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    aggregate = service.initialize_fixture()
    project_id = aggregate.document.project.id
    bible = next(
        item
        for item in aggregate.document.bible_versions
        if item.id == aggregate.document.project.current_bible_version_id
    )
    run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="Keep only plans backed by exact project artifacts.",
            initial_plan=AgentPlanProposal(
                summary="The current valid plan remains the rollback point.",
                steps=[
                    PlanStep(
                        id="valid-checkpoint",
                        title="Valid checkpoint",
                        kind=ActionKind.CHECKPOINT,
                        assigned_agent="production-manager",
                        instructions="Keep the current plan intact.",
                    )
                ],
            ),
        ),
    )
    original_plan = run.snapshot.current_plan
    assert original_plan is not None

    invented_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="project_bible_version",
        artifact_id=bible.id,
        version=bible.version,
    )
    with pytest.raises(
        ProductionManagerError,
        match=r"(?i)unresolved exact artifactrefs",
    ):
        service.replan_agent_run(
            run.snapshot.run.id,
            AgentRunReplanRequest(
                reason="An external caller proposed an invented artifact type.",
                proposed_plan=AgentPlanProposal(
                    summary="This invalid plan must never become current.",
                    steps=[
                        PlanStep(
                            id="invalid-delegation",
                            title="Invalid delegation",
                            kind=ActionKind.DELEGATION,
                            production_skill_role=ProductionSkillRole.DIRECTOR,
                            assigned_agent="storyboard-director",
                            instructions="This invented reference must be rejected.",
                            arguments={
                                "adjacent_boundaries": [
                                    {
                                        "relationship": "incoming-story-state",
                                        "artifact_refs": [
                                            invented_ref.model_dump(mode="json")
                                        ],
                                    }
                                ]
                            },
                            artifact_refs=[invented_ref],
                        )
                    ],
                ),
            ),
        )

    persisted = service.get_agent_run(run.snapshot.run.id)
    assert persisted.snapshot.current_plan is not None
    assert persisted.snapshot.current_plan.id == original_plan.id
    assert persisted.snapshot.current_plan.revision == 1
    assert (
        len(service.agent_runtime.store.list_plan_revisions(run.snapshot.run.id))
        == 1
    )
    assert service.agent_runtime.store.list_actions(run.snapshot.run.id) == []
    assert service.agent_runtime.store.list_work_orders(run.snapshot.run.id) == []


def test_completed_script_only_child_job_returns_to_the_agent_run_objective(
    tmp_path: Path,
) -> None:
    client = FakePlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    created = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective=(
                "Use the confirmed screenplay to produce the canonical storyboard."
            ),
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    )
    run_id = created.snapshot.run.id
    document = service.get_project(project_id).document
    document.project.content_planning_output = ContentPlanningOutput.SCRIPT_ONLY
    document.project.stage = ProjectStage.SCRIPT_READY
    document.planning = ProjectPlanningMetadata(
        run_id="completed-script-revision",
        status=PlanningRunStatus.COMPLETE,
        owner_agent_run_id=run_id,
    )
    service.repository.save_project(document)

    replanned = service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason=(
                "The bounded screenplay repair is complete; continue the parent run."
            )
        ),
    )

    assert client.inputs
    assert replanned.snapshot.current_plan is not None
    assert replanned.snapshot.current_plan.goal_complete is False
    assert [step.tool_name for step in replanned.snapshot.current_plan.steps] == [
        "project.get"
    ]


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


def test_replan_supersedes_work_order_that_already_returned_to_leader(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, work_order_id, action_id = _start_pending_delegation(
        service,
        project_id,
    )
    returned = service.agent_runtime.store.get_work_order(work_order_id)
    assert returned is not None
    returned.status = DelegationStatus.NEEDS_ATTENTION
    returned.next_owner = WorkOrderNextOwner.LEADER_REPLAN
    service.agent_runtime.store.save_work_order(returned)

    replanned = service.replan_agent_run(
        run_id,
        AgentRunReplanRequest(
            reason="Create a focused replacement from the returned handoff.",
            proposed_plan=AgentPlanProposal(
                summary="The replacement has taken ownership of the remaining repair.",
                carried_work_order_ids=[work_order_id],
                goal_complete=True,
            ),
        ),
    )

    current_plan = replanned.snapshot.current_plan
    assert current_plan is not None
    assert current_plan.carried_work_order_ids == []
    superseded = service.agent_runtime.store.get_work_order(work_order_id)
    assert superseded is not None
    assert superseded.status == DelegationStatus.CANCELLED
    assert superseded.superseded_by_plan_revision_id == current_plan.id
    cancelled_action = service.agent_runtime.store.get_action(action_id)
    assert cancelled_action is not None
    assert cancelled_action.status == ActionStatus.CANCELLED


def test_finished_local_plan_replans_once_then_explicit_carried_wait_stays_idle(
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

    client.responses.append(json.dumps({
        "summary": "The new checkpoint is saved; wait for the retained specialist.",
        "steps": [], "carried_work_order_ids": [work_order_id], "goal_complete": False,
    }))

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
    assert waiting.run.snapshot.current_plan.id != plan.id
    wait_plan_id = waiting.run.snapshot.current_plan.id
    assert service.agent_runtime.store.get_work_order(work_order_id) is not None
    assert len(client.inputs) == 1
    again = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
    assert again.run.snapshot.run.status == AgentRunStatus.WAITING
    assert again.run.snapshot.current_plan is not None
    assert again.run.snapshot.current_plan.id == wait_plan_id
    assert len(client.inputs) == 1

    client.responses.append(json.dumps({
        "summary": "Keep the same specialist while incorporating the latest request.",
        "steps": [], "carried_work_order_ids": [work_order_id], "goal_complete": False,
    }))
    resumed = service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason="Incorporate the user's update while continuing the existing specialist.",
    ))
    assert resumed.snapshot.current_plan is not None
    assert resumed.snapshot.current_plan.carried_work_order_ids == [work_order_id]
    assert len(client.inputs) == 2
    assert service.agent_runtime.store.get_work_order(work_order_id) is not None


def test_carried_work_order_return_to_leader_automatically_replans(tmp_path: Path) -> None:
    client = SequencedLanguageClient([json.dumps({
        "summary": "The Leader has consumed the returned review.", "steps": [], "goal_complete": True,
    })])
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3", workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    run_id, work_order_id, _action_id = _start_pending_delegation(service, project_id)
    carried = service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason="Keep the active Worker.", proposed_plan=AgentPlanProposal(
            summary="Continue the existing review.", carried_work_order_ids=[work_order_id], goal_complete=True,
        ),
    ))
    old_plan = carried.snapshot.current_plan
    assert old_plan is not None
    service.submit_agent_work_order_result(work_order_id, AgentWorkOrderResultRequest(
        status=AgentResultStatus.PARTIAL, concise_summary="The review has findings for the Leader.",
        next_owner=WorkOrderNextOwner.LEADER_REPLAN,
    ))
    advanced = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    assert len(client.inputs) == 1
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.id != old_plan.id
    assert advanced.run.snapshot.current_plan.carried_work_order_ids == []
    assert advanced.run.snapshot.run.status == AgentRunStatus.COMPLETED


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
                selected_method_skill_ids=["method.brand_promo"],
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
                        production_prompt_stage=ProductionPromptStage.STORYBOARD,
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
    assert (
        work_order.work_order.production_prompt_stage
        == ProductionPromptStage.STORYBOARD
    )
    assert "当前制片阶段：STORYBOARD" in work_order.system_prompt
    assert work_order.work_order.context_manifest is not None
    assert (
        work_order.work_order.context_manifest.production_prompt_stage
        == ProductionPromptStage.STORYBOARD
    )
    assert work_order.work_order.constraints == ["Preserve the accepted ending."]
    assert "当前制片技能：AI 短剧（本地格式适配）" in work_order.system_prompt
    assert "导演的通用职责" in work_order.system_prompt
    assert "品牌宣传片" in work_order.system_prompt
    assert "导演动效语言" in work_order.system_prompt
    assert "把当前采用的文本意图转成可见的表演、调度、构图、动作、声音与时间设计" in (
        work_order.system_prompt
    )
    assert work_order.context.artifacts[0].ref.artifact_id == shot.id
    assert work_order.work_order.context_manifest.selected_skill_ids == (
        work_order.context.active_skill_ids
    )

    service.agent_runtime.set_run_active_skills(
        run.snapshot.run.id,
        ["format.ai-short-drama"],
    )
    snapshotted_work_order = service.get_agent_work_order(work_order_id)
    assert "method.brand_promo" in (
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


def test_agent_run_view_summarizes_pending_work_order_without_trace_growth(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    run_id, work_order_id, _action_id = _start_pending_delegation(
        service,
        project_id,
    )
    initial = service.get_agent_run(run_id)
    initial_json = initial.model_dump_json()

    stored = service.agent_runtime_store.get_work_order(work_order_id)
    assert stored is not None
    stored.production_prompt_stage = ProductionPromptStage.STORYBOARD
    for index in range(120):
        stored.specialist_turns.append(
            SpecialistTurnRecord(
                id=f"turn-public-view-{index}",
                sequence=index + 1,
                tool_name="image.advance",
                arguments={
                    "project_id": project_id,
                    "task_id": "image-task-current",
                },
                reason="Advance the current durable image task.",
                status=SpecialistTurnStatus.COMPLETED,
                result={
                    "tool_id": "image.advance",
                    "status": "SUCCEEDED",
                    "output": {
                        "task": {
                            "id": "image-task-current",
                            "status": "RUNNING",
                            "provider_payload": (
                                f"PUBLIC_RUN_VIEW_MUST_NOT_REPLAY_{index:03d}" * 100
                            ),
                        }
                    },
                    "artifact_refs": [],
                    "warnings": [],
                    "error": None,
                },
            )
        )
    service.agent_runtime_store.save_work_order(stored)

    public_view = service.get_agent_run(run_id)
    public_json = public_view.model_dump_json()
    pending = public_view.snapshot.pending_work_orders[0]

    assert pending.id == work_order_id
    assert pending.status == stored.status
    assert pending.next_owner == stored.next_owner
    assert pending.production_prompt_stage == ProductionPromptStage.STORYBOARD
    assert pending.specialist_turn_count == 120
    assert pending.latest_specialist_tool_name == "image.advance"
    assert pending.latest_specialist_turn_status == SpecialistTurnStatus.COMPLETED
    assert "specialist_turns" not in public_json
    assert "PUBLIC_RUN_VIEW_MUST_NOT_REPLAY" not in public_json
    assert len(public_json.encode()) < len(initial_json.encode()) + 1000

    # The explicit WorkOrder endpoint and runtime store remain lossless for
    # restart recovery and detailed trace inspection.
    complete_view = service.get_agent_work_order(work_order_id)
    assert len(complete_view.work_order.specialist_turns) == 120
    assert "PUBLIC_RUN_VIEW_MUST_NOT_REPLAY_000" in (
        complete_view.work_order.model_dump_json()
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
    model_context = planner_payload["context"]
    recent = model_context["recent_actions"]
    assert recent[-1]["status"] == "SUCCEEDED"
    action_ref_id = recent[-1]["artifact_ref_ids"][0]
    assert model_context["artifact_index"][action_ref_id]["artifact_id"] == project.id
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
    invalid_replan = json.dumps(
        {
            "summary": "No justified next action is available yet.",
            "goal_complete": False,
            "steps": [],
        }
    )
    client = SequencedLanguageClient([invalid_replan, invalid_replan])
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
    assert len(client.inputs) == 2
    second_payload = json.loads(client.inputs[1])
    assert second_payload["plan_validation_feedback"]["kind"] == (
        "INVALID_PLAN_CONTRACT"
    )
    assert advanced.run.snapshot.current_plan is not None
    assert advanced.run.snapshot.current_plan.revision == 1
    assert advanced.run.ready_steps == []
    assert any(
        "repeated the same invalid plan contract" in note
        for note in advanced.notes
    )
    assert any(
        "repeated the same invalid plan contract" in blocker
        for blocker in advanced.run.context.blockers
    )


def test_external_action_settlement_waits_after_repeated_invalid_contract(
    tmp_path: Path,
) -> None:
    invalid_replan = json.dumps(
        {
            "summary": "No justified next action is available yet.",
            "goal_complete": False,
            "steps": [],
        }
    )
    client = SequencedLanguageClient([invalid_replan, invalid_replan])
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
    assert len(client.inputs) == 2
    second_payload = json.loads(client.inputs[1])
    assert second_payload["plan_validation_feedback"]["kind"] == (
        "INVALID_PLAN_CONTRACT"
    )
    assert settled.run.snapshot.current_plan is not None
    assert settled.run.snapshot.current_plan.revision == 1
    assert settled.run.ready_steps == []
    assert any(
        "repeated the same invalid plan contract" in note
        for note in settled.notes
    )
    assert any(
        "repeated the same invalid plan contract" in blocker
        for blocker in settled.run.context.blockers
    )


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
    assert "tool_history" not in final_payload
    assert final_payload["working_set"]["total_tool_turns"] == 6
    assert len(final_payload["working_set"]["tool_checkpoints"]) == 1
    assert final_payload["working_set"]["tool_checkpoints"][0][
        "tool_name"
    ] == "project.get"
    assert final_payload["working_set"]["latest_handoff"]["result"][
        "status"
    ] == "SUCCEEDED"
    assert [
        json.loads(value)["working_set"]["total_tool_turns"]
        for value in client.inputs
    ] == list(range(7))
    payload_sizes = [len(value.encode()) for value in client.inputs]
    # Once the first exact project handoff exists, replaying the same entity does
    # not append another copy on every stateless Worker decision.
    assert payload_sizes[-1] < payload_sizes[1] + 1500
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


def test_builtin_specialist_receives_independent_read_observations_together(
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
        json.dumps(
            {
                "tool_name": "project.get",
                "arguments": {"project_id": project.id},
                "reason": "Read the canonical project before asset execution.",
            }
        ),
        json.dumps(
            {
                "tool_name": "image.capability",
                "arguments": {},
                "reason": "Read the current image provider capability.",
            }
        ),
        """{
          "status": "COMPLETED",
          "concise_summary": "The project evidence and image capability are both available.",
          "created_artifacts": [],
          "updated_artifacts": [],
          "findings": [],
          "decisions": ["Continue from the combined read observations."],
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
            objective="Confirm the asset worker can combine independent reads.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one grounded asset decision.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Delegate asset decision",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
                        assigned_agent="asset-worker",
                        instructions="Read project truth and image capability together.",
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
    while True:
        response = service.execute_agent_work_order(work_order_id)
        if response.result.status == AgentResultStatus.COMPLETED:
            break

    assert len(client.inputs) == 3
    final_payload = json.loads(client.inputs[-1])
    working_set = final_payload["working_set"]
    assert working_set["latest_handoff"]["request"]["tool_name"] == (
        "image.capability"
    )
    assert [
        item["tool_name"] for item in working_set["read_observations"]
    ] == ["project.get"]
    project_observation = working_set["read_observations"][0]
    assert project_observation["result"]["status"] == "SUCCEEDED"
    assert project_observation["result"]["output"]["document"]["project"][
        "id"
    ] == project.id
    assert "不得仅因某个观察" in client.instructions[-1]


def test_builtin_specialist_preloads_source_schemas_for_attempt_handoff(
    tmp_path: Path,
) -> None:
    client = SequencedLanguageClient(
        [
            json.dumps(
                {
                    "status": "COMPLETED",
                    "concise_summary": "The handed repair contracts are available.",
                    "created_artifacts": [],
                    "updated_artifacts": [],
                    "findings": [],
                    "decisions": ["Continue with the exact source Tool contracts."],
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
    run = service.agent_runtime.create_run(
        project_id=project_id,
        objective="Repair two handed asset-plan Tool attempts.",
        root_agent="production-manager",
    )

    refs: list[ToolAttemptRef] = []
    for index, tool_name in enumerate(
        ("artifact.patch_asset_plan", "artifact.retry_asset_plan_attempt"),
        start=1,
    ):
        source = service.agent_runtime.create_work_order(
            run.id,
            sender="production-manager",
            recipient_role="asset-planner",
            production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
            production_prompt_stage=ProductionPromptStage.ASSETS,
            objective="Persist one failed repair attempt.",
            why_now="The repair Worker needs its exact contract.",
        )
        attempt_id = f"source-attempt-{index}"
        source.specialist_turns.append(
            SpecialistTurnRecord(
                id=attempt_id,
                sequence=1,
                tool_name=tool_name,
                arguments={
                    "project_id": project_id,
                    "marker": f"SOURCE_BODY_MUST_STAY_LAZY_{index}",
                },
                reason="Try the source repair.",
                status=SpecialistTurnStatus.COMPLETED,
                result={
                    "tool_id": tool_name,
                    "status": ToolCallStatus.FAILED.value,
                    "output": None,
                    "artifact_refs": [],
                    "warnings": [],
                    "error": {"kind": "Error", "message": "Needs repair."},
                },
            )
        )
        source.status = DelegationStatus.NEEDS_ATTENTION
        source.next_owner = WorkOrderNextOwner.LEADER_REPLAN
        service.agent_runtime.store.save_work_order(source)
        refs.append(
            ToolAttemptRef(
                project_id=project_id,
                agent_run_id=run.id,
                work_order_id=source.id,
                attempt_id=attempt_id,
                tool_name=tool_name,
                turn_status=SpecialistTurnStatus.COMPLETED,
                outcome_status=ToolCallStatus.FAILED.value,
                has_arguments=True,
                has_result=True,
            )
        )

    repair = service.agent_runtime.create_work_order(
        run.id,
        sender="production-manager",
        recipient_role="asset-plan-repairer",
        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
        production_prompt_stage=ProductionPromptStage.ASSETS,
        objective="Use the handed attempts to repair the AssetPlan.",
        why_now="Both failures describe the remaining local correction.",
        tool_attempt_refs=refs,
    )

    response = service.execute_agent_work_order(repair.id)

    assert response.result.status == AgentResultStatus.COMPLETED
    first_payload = json.loads(client.inputs[0])
    assert set(first_payload["tool_schemas"]) == {
        "agent.get_tool_attempt",
        "artifact.patch_asset_plan",
        "artifact.retry_asset_plan_attempt",
    }
    assert "artifact.create_asset_plan" not in first_payload["tool_schemas"]
    assert "artifact.replace_asset_plan" not in first_payload["tool_schemas"]
    for schema in first_payload["tool_schemas"].values():
        assert "description" in schema
        assert "input_schema" in schema
        assert "output_schema" not in schema
    serialized = json.dumps(first_payload, ensure_ascii=False)
    assert "SOURCE_BODY_MUST_STAY_LAZY" not in serialized


def test_builtin_specialist_corrects_tool_contract_in_same_work_order(
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
        json.dumps(
            {
                "tool_name": "project.get",
                "arguments": {
                    "project_id": project.id,
                    "retry_from_persisted_failed_request": True,
                },
                "reason": "Attempt the canonical read with a malformed wrapper.",
            }
        ),
        json.dumps(
            {
                "tool_name": "project.get",
                "arguments": {"project_id": project.id},
                "reason": "Reuse the exact request and remove only the invalid field.",
            }
        ),
        json.dumps(
            {
                "status": "COMPLETED",
                "concise_summary": "The same Worker corrected its Tool request.",
                "created_artifacts": [],
                "updated_artifacts": [],
                "findings": [],
                "decisions": ["Continue from the successful canonical read."],
                "assumptions": [],
                "unresolved_questions": [],
                "evidence_refs": [],
                "recommended_next_actions": [],
            }
        ),
    ]
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    run = service.create_agent_run(
        project.id,
        AgentRunCreateRequest(
            objective="Correct a local Tool contract without changing scope.",
            initial_plan=AgentPlanProposal(
                summary="Delegate one canonical read.",
                goal_complete=True,
                steps=[
                    PlanStep(
                        id="delegate",
                        title="Read canonical project",
                        kind=ActionKind.DELEGATION,
                        production_skill_role=ProductionSkillRole.ASSET_REVIEWER,
                        assigned_agent="asset-worker",
                        instructions="Read the project and correct local Tool usage.",
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

    while True:
        response = service.execute_agent_work_order(work_order_id)
        if response.result.status == AgentResultStatus.COMPLETED:
            break

    assert len(client.inputs) == 3
    correction_payload = json.loads(client.inputs[1])
    correction_set = correction_payload["working_set"]
    assert correction_set["repair_tool_schema_ids"] == ["project.get"]
    assert correction_set["pending_tool_repairs"] == []
    assert correction_set["latest_handoff"]["request"]["arguments"] == {
        "project_id": project.id,
    }
    assert correction_set["latest_handoff"]["attempt_ref"]["tool_name"] == (
        "project.get"
    )
    assert correction_set["latest_handoff"]["result"]["status"] == (
        "INVALID_ARGUMENTS"
    )
    assert set(correction_payload["tool_schemas"]) == {
        "agent.get_tool_attempt",
        "project.get",
    }
    assert "input_schema" in correction_payload["tool_schemas"]["project.get"]
    assert "output_schema" not in correction_payload["tool_schemas"]["project.get"]
    assert "后续写入尝试消费为止" in client.instructions[1]

    final_payload = json.loads(client.inputs[2])
    assert final_payload["working_set"]["repair_tool_schema_ids"] == []
    assert final_payload["working_set"]["pending_tool_repairs"] == []
    stored = service.agent_runtime.store.get_work_order(work_order_id)
    assert stored is not None
    assert stored.status == DelegationStatus.COMPLETED
    assert stored.next_owner is None
    assert [turn.result["status"] for turn in stored.specialist_turns if turn.result] == [
        "INVALID_ARGUMENTS",
        "SUCCEEDED",
    ]


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
        decision.statement == "Video execution target: MOCK"
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
    script = next(
        item for item in document.script_versions if item.id == base_script_version_id
    )
    evidence_refs = [
        ProductionDocumentArtifactRef(
            project_id=project_id,
            artifact_type="script_version",
            artifact_id=script.id,
            version=script.version,
        )
    ]
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
            "revision_origin": ScriptRevisionOrigin.USER_FEEDBACK,
            "evidence_refs": [
                item.model_dump(mode="json") for item in evidence_refs
            ],
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
        revision_origin=ScriptRevisionOrigin.USER_FEEDBACK,
        evidence_refs=evidence_refs,
    ).run

    reopened = _service(tmp_path)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted.id)
    recovered = reopened.get_agent_run(run_id)
    current = reopened.get_project(project_id).document

    assert current.planning is not None
    assert current.planning.run_id == persisted.id
    assert persisted.revision_origin == ScriptRevisionOrigin.USER_FEEDBACK
    assert persisted.revision_evidence_refs == evidence_refs
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


def test_pending_story_verifier_requires_resolution_plan_not_another_advance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    agent_run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="处理精确 Story Verifier handoff。",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    ).snapshot.run
    planning_run_id = _persist_pending_revision_verifier(
        service,
        project_id=project_id,
        owner_agent_run_id=agent_run.id,
    )
    document = service.get_project(project_id).document

    fallback = service.production_manager._planning_precondition_plan(
        agent_run_id=agent_run.id,
        document=document,
        semantic_planning_available=False,
    )
    assert fallback is not None
    assert fallback.steps[0].kind == ActionKind.USER_INPUT
    assert "planning.resolve_story_verification" in fallback.steps[0].instructions
    assert service.production_manager._planning_precondition_plan(
        agent_run_id=agent_run.id,
        document=document,
        semantic_planning_available=True,
    ) is None
    assert (
        service.production_manager._semantic_planning_current_plan(
            agent_run_id=agent_run.id,
            document=document,
            current=_single_tool_plan(
                "planning.advance",
                {
                    "project_id": project_id,
                    "run_id": planning_run_id,
                    "owner_agent_run_id": agent_run.id,
                },
            ),
        )
        is None
    )

    resolve_plan = _single_tool_plan(
        "planning.resolve_story_verification",
        {
            "project_id": project_id,
            "run_id": planning_run_id,
            "owner_agent_run_id": agent_run.id,
            "decision": (
                StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION.value
            ),
            "rationale": "问题可定位于第 1 场，由 Repair Leader 重新规划。",
        },
    )
    assert service.production_manager._is_content_planning_plan(
        resolve_plan,
        agent_run_id=agent_run.id,
        document=document,
    )
    advance_plan = _single_tool_plan(
        "planning.advance",
        {
            "project_id": project_id,
            "run_id": planning_run_id,
            "owner_agent_run_id": agent_run.id,
        },
    )
    assert not service.production_manager._is_content_planning_plan(
        advance_plan,
        agent_run_id=agent_run.id,
        document=document,
    )


def test_semantic_replan_receives_pending_verifier_evidence_before_context_cutoff(
    tmp_path: Path,
) -> None:
    client = FakePlanningLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: client,
    )
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    agent_run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="依据精确 Revision Verifier handoff 决定下一步。",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    ).snapshot.run
    planning_run_id = _persist_pending_revision_verifier(
        service,
        project_id=project_id,
        owner_agent_run_id=agent_run.id,
    )
    planning_run = service.get_planning_run(planning_run_id).run
    document = service.get_project(project_id).document
    target_seconds = document.project.target_duration_seconds
    planning_run.outline_json = "LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD" * 1000
    planning_run.story_intent = StoryIntent(
        audience_experience="快速、直接的爽感。",
        dramatic_engine="普通计算能力成为全场奇迹。",
        progression_strategy="食堂震惊后立即升级到校长挽留。",
        tone_and_plausibility="优先节奏和爆点。",
        duration_contract=DurationContract(
            target_seconds=target_seconds,
            target_origin=DurationTargetOrigin.USER_DECLARED,
            authority=DurationAuthority.TARGET_LED,
            source_fidelity_policy=(
                DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
            ),
            mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
            decision_basis="用户明确声明本片目标时长。",
        ),
    )
    assert planning_run.latest_revision_verification is not None
    planning_run.latest_revision_verification = (
        planning_run.latest_revision_verification.model_copy(
            update={
                "duration_fit_assessment": DurationFitAssessment(
                    estimated_range_seconds=DurationRange(
                        minimum_seconds=target_seconds + 40.0,
                        maximum_seconds=target_seconds + 55.0,
                    ),
                    alignment=DurationAlignment.UNRESOLVED,
                    resolution=DurationMismatchResolution.REVISE_SCRIPT,
                    rationale="LEADER_DURATION_EVIDENCE_SENTINEL",
                )
            }
        )
    )
    service.repository.save_planning_run_with_project(planning_run, document)
    planning_ref = ArtifactRef(
        project_id=project_id,
        artifact_type="content_planning_job",
        artifact_id=planning_run_id,
    )
    service.agent_runtime.checkpoint(
        agent_run.id,
        current_focus="处理 Revision Verifier handoff。",
        artifact_refs=[planning_ref],
    )

    service.replan_agent_run(
        agent_run.id,
        AgentRunReplanRequest(reason="读取精确 handoff 后决定修订范围。"),
    )

    assert client.inputs
    planner_payload = json.loads(client.inputs[-1])
    model_context = planner_payload["context"]
    planning_artifact = next(
        item
        for item in model_context["artifacts"]
        if model_context["artifact_index"][item["artifact_ref_id"]][
            "artifact_type"
        ]
        == "content_planning_job"
    )
    summary = planning_artifact["summary"]
    assert len(summary) <= 3000
    assert '"source":"REVISION_VERIFIER"' in summary
    assert '"accepted":false' in summary
    assert '"alignment":"UNRESOLVED"' in summary
    assert f'"target_seconds":{target_seconds}' in summary
    assert "LEADER_DURATION_EVIDENCE_SENTINEL" in summary
    if "LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD" in summary:
        assert summary.index("LEADER_DURATION_EVIDENCE_SENTINEL") < summary.index(
            "LONG_OUTLINE_BEFORE_VERIFIER_PAYLOAD"
        )


def test_initial_script_verifier_revision_requires_leader_resolution_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    agent_run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="处理初次 Script Verifier 的精确 handoff。",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    ).snapshot.run
    planning_run_id = _persist_pending_script_verifier(
        service,
        project_id=project_id,
        owner_agent_run_id=agent_run.id,
    )
    document = service.get_project(project_id).document
    planning_run = service.get_planning_run(planning_run_id).run

    assert service.production_manager._planning_run_awaits_story_verification_resolution(
        planning_run
    )
    resolve_plan = _single_tool_plan(
        "planning.resolve_story_verification",
        {
            "project_id": project_id,
            "run_id": planning_run_id,
            "owner_agent_run_id": agent_run.id,
            "decision": (
                StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION.value
            ),
            "rationale": "因果缺口有精确证据，由 Repair Leader 重新判断范围。",
        },
    )
    assert service.production_manager._is_content_planning_plan(
        resolve_plan,
        agent_run_id=agent_run.id,
        document=document,
    )
    assert not service.production_manager._is_content_planning_plan(
        _single_tool_plan(
            "planning.advance",
            {
                "project_id": project_id,
                "run_id": planning_run_id,
                "owner_agent_run_id": agent_run.id,
            },
        ),
        agent_run_id=agent_run.id,
        document=document,
    )


def test_production_manager_uses_duration_authority_for_outside_verifier_result(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    agent_run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="根据 DurationContract authority 处理 Verifier 时长结果。",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    ).snapshot.run
    planning_run_id = _persist_pending_script_verifier(
        service,
        project_id=project_id,
        owner_agent_run_id=agent_run.id,
    )
    document = service.get_project(project_id).document
    planning_run = service.get_planning_run(planning_run_id).run
    assert planning_run.review_report is not None
    target = document.project.target_duration_seconds
    planning_run.story_intent = StoryIntent(
        audience_experience="观众跟随主角完成一次紧凑回报。",
        dramatic_engine="可见行动持续放大主角优势。",
        progression_strategy="按触发、升级和回报推进。",
        tone_and_plausibility="优先快节奏与可见回报。",
        duration_contract=DurationContract(
            target_seconds=target,
            target_origin=DurationTargetOrigin.USER_DECLARED,
            authority=DurationAuthority.TARGET_LED,
            acceptable_range_seconds=DurationRange(
                minimum_seconds=target - 10,
                maximum_seconds=target + 10,
            ),
            source_fidelity_policy=DurationFidelityPolicy.REWRITABLE,
            mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
            decision_basis="用户明确了目标时长，剧本尺度可修订。",
        ),
    )
    assert planning_run.story_intent.duration_contract is not None
    outside = DurationFitAssessment(
        estimated_range_seconds=DurationRange(
            minimum_seconds=target + 20,
            maximum_seconds=target + 40,
        ),
        alignment=DurationAlignment.OUTSIDE_ACCEPTED_RANGE,
        pressure_points=["中段解释密度过高"],
        concurrent_performance=[],
        resolution=DurationMismatchResolution.REVISE_SCRIPT,
        rationale="自然表演时长超出目标契约的可接受范围。",
    )
    planning_run.review_report = planning_run.review_report.model_copy(
        deep=True,
        update={
            "issues": [],
            "requires_revision": False,
            "duration_fit_assessment": outside,
        },
    )
    service.repository.save_planning_run_with_project(planning_run, document)

    assert service.production_manager._planning_run_awaits_story_verification_resolution(
        planning_run
    )
    resolve_plan = _single_tool_plan(
        "planning.resolve_story_verification",
        {
            "project_id": project_id,
            "run_id": planning_run_id,
            "owner_agent_run_id": agent_run.id,
            "decision": StoryVerificationResolutionDecision.ACCEPT_TRADEOFF.value,
            "rationale": "剧情回报优先，明确接受 TARGET_LED 时长取舍。",
        },
    )
    assert service.production_manager._is_content_planning_plan(
        resolve_plan,
        agent_run_id=agent_run.id,
        document=document,
    )

    for authority, target_origin, mismatch_resolution in (
        (
            DurationAuthority.SOURCE_LED,
            DurationTargetOrigin.SOURCE_DECLARED,
            DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
        ),
        (
            DurationAuthority.AUDIO_LED,
            DurationTargetOrigin.MASTER_AUDIO,
            DurationMismatchResolution.FOLLOW_MASTER_AUDIO,
        ),
    ):
        contract = planning_run.story_intent.duration_contract.model_copy(
            deep=True,
            update={
                "authority": authority,
                "target_origin": target_origin,
                "mismatch_resolution": mismatch_resolution,
            },
        )
        non_target = planning_run.model_copy(
            deep=True,
            update={
                "story_intent": planning_run.story_intent.model_copy(
                    deep=True,
                    update={"duration_contract": contract},
                )
            },
        )
        assert not service.production_manager._planning_run_awaits_story_verification_resolution(
            non_target
        )


def test_restart_reconciles_persisted_story_verification_resolution_without_replay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    agent_run = service.create_agent_run(
        project_id,
        AgentRunCreateRequest(
            objective="显式处理 Story Verifier handoff。",
            initial_plan=_single_tool_plan(
                "project.get",
                {"project_id": project_id},
            ),
        ),
    ).snapshot.run
    planning_run_id = _persist_pending_revision_verifier(
        service,
        project_id=project_id,
        owner_agent_run_id=agent_run.id,
    )
    rationale = "当前交付允许保留该状态取舍，Verifier 原结论继续留存。"
    step = PlanStep(
        id="resolve-story-verification",
        title="Resolve persisted verifier handoff",
        kind=ActionKind.TOOL_CALL,
        assigned_agent="production-manager",
        tool_name="planning.resolve_story_verification",
        instructions="Persist the Leader's explicit semantic decision.",
        arguments={
            "project_id": project_id,
            "run_id": planning_run_id,
            "owner_agent_run_id": agent_run.id,
            "decision": StoryVerificationResolutionDecision.ACCEPT_TRADEOFF.value,
            "rationale": rationale,
        },
    )
    plan = service.agent_runtime.replace_plan(
        agent_run.id,
        summary="Resolve the exact verifier result.",
        steps=[step],
    )
    interrupted = service.agent_runtime.begin_action(
        agent_run.id,
        kind=ActionKind.TOOL_CALL,
        agent_id="production-manager",
        input_summary=step.instructions,
        plan_revision_id=plan.id,
        plan_step_id=step.id,
        tool_name=step.tool_name,
    )

    resolved = service.resolve_story_verification(
        project_id,
        run_id=planning_run_id,
        owner_agent_run_id=agent_run.id,
        decision=StoryVerificationResolutionDecision.ACCEPT_TRADEOFF,
        rationale=rationale,
    ).run
    assert resolved.latest_revision_verification is not None
    assert resolved.latest_revision_verification.accepted is False

    reopened = _service(tmp_path)
    recovered_action = reopened.agent_runtime_store.get_action(interrupted.id)
    assert recovered_action is not None
    assert recovered_action.status == ActionStatus.SUCCEEDED
    assert "persisted Leader decision" in (recovered_action.output_summary or "")
    persisted = reopened.get_planning_run(planning_run_id).run
    assert persisted.story_verification_resolutions[-1].rationale == rationale
    assert persisted.latest_revision_verification is not None
    assert persisted.latest_revision_verification.accepted is False


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
