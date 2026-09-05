"""Typed Tool Registry tests using real adapters and small fakes, never mocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.service import ShortDramaService
from short_drama.tools import (
    ArtifactRef,
    ToolCallStatus,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolNotFoundError,
    ToolRegistry,
    ToolSpec,
)
from short_drama.tools.agent import (
    AgentDelegateInput,
    AgentDelegateOutput,
    register_tools as register_agent_tools,
)


class MultiplyInput(ToolModel):
    value: int


class MultiplyOutput(ToolModel):
    doubled: int


def _multiply(request: MultiplyInput) -> ToolExecution[MultiplyOutput]:
    return ToolExecution(output=MultiplyOutput(doubled=request.value * 2))


def _failing_tool(_request: MultiplyInput) -> ToolExecution[MultiplyOutput]:
    raise RuntimeError("planned failure")


def _multiply_spec(*, tool_id: str = "project.multiply") -> ToolSpec[MultiplyInput, MultiplyOutput]:
    return ToolSpec(
        id=tool_id,
        version=1,
        category=ToolCategory.PROJECT,
        description="Double an integer for registry contract tests.",
        effect=ToolEffect.READ,
        idempotent=True,
        input_model=MultiplyInput,
        output_model=MultiplyOutput,
        handler=_multiply,
        tags=frozenset({"math", "project"}),
    )


def test_registry_discovers_validates_and_invokes_typed_tools() -> None:
    registry = ToolRegistry()
    registry.register(_multiply_spec())

    descriptor = registry.describe("project.multiply")
    assert descriptor.category == ToolCategory.PROJECT
    assert descriptor.input_schema["properties"] == {
        "value": {"title": "Value", "type": "integer"}
    }
    assert [item.id for item in registry.list(tags=frozenset({"math"}))] == [
        "project.multiply"
    ]

    result = registry.invoke("project.multiply", {"value": 6})
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output == {"doubled": 12}

    invalid = registry.invoke("project.multiply", {"value": "not-an-integer"})
    assert invalid.status == ToolCallStatus.INVALID_ARGUMENTS
    assert invalid.error is not None
    assert invalid.error.kind == "ValidationError"


def test_registry_reports_lookup_execution_and_registration_errors() -> None:
    registry = ToolRegistry()
    registry.register(_multiply_spec())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_multiply_spec())
    with pytest.raises(ToolNotFoundError):
        registry.describe("project.missing")

    missing = registry.invoke("project.missing", {})
    assert missing.status == ToolCallStatus.NOT_FOUND

    registry.register(
        ToolSpec(
            id="project.fail",
            version=1,
            category=ToolCategory.PROJECT,
            description="Fail predictably for result-envelope testing.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=MultiplyInput,
            output_model=MultiplyOutput,
            handler=_failing_tool,
        )
    )
    failed = registry.invoke("project.fail", {"value": 1})
    assert failed.status == ToolCallStatus.FAILED
    assert failed.error is not None
    assert failed.error.kind == "RuntimeError"
    assert failed.error.message == "planned failure"


def test_service_exposes_default_registry_and_keeps_injected_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_multiply_spec())
    service = ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        tool_registry=registry,
    )

    expected_ids = {
        "artifact.create_asset",
        "audio.inspect_manifest",
        "image.enqueue",
        "planning.advance",
        "planning.start_script_revision",
        "project.create",
        "project.get",
        "project.list",
        "project.multiply",
        "qa.review_episode",
        "script.revise_lines",
        "shot.update_reference_plan",
        "task.advance",
        "timeline.rebuild",
        "video.inspect_take",
    }
    assert expected_ids <= {
        descriptor.id for descriptor in service.tool_registry.list()
    }

    result = service.tool_registry.invoke("project.list", {})
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output == {"projects": []}


def test_project_create_tool_can_use_agent_preallocated_project_id(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )

    result = service.tool_registry.invoke(
        "project.create",
        {
            "project_id": "project-agent-bootstrap",
            "request": {
                "name": "Agent bootstrap",
                "source_mode": "AUTO_ORIGINAL",
                "production_profile": "AI_SHORT_DRAMA",
                "target_duration_seconds": 12,
                "input_text": "",
                "input_images": [],
                "linked_asset_project_ids": [],
                "run_mode": "GUIDED",
            },
        },
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert result.output["document"]["project"]["id"] == "project-agent-bootstrap"
    assert result.artifact_refs[0].artifact_id == "project-agent-bootstrap"
    assert service.get_project("project-agent-bootstrap").document.project.name == (
        "Agent bootstrap"
    )


class FakeAgentDelegator:
    def delegate(self, request: AgentDelegateInput) -> AgentDelegateOutput:
        return AgentDelegateOutput(
            delegation_id="delegation-1",
            accepted=True,
            status="COMPLETE",
            summary=f"Handled by {request.recipient_role}",
        )


def test_agent_delegate_is_registered_only_with_an_agent_runtime() -> None:
    registry = ToolRegistry()
    register_agent_tools(registry, FakeAgentDelegator())
    artifact = ArtifactRef(
        project_id="project-1",
        artifact_type="scene",
        artifact_id="scene-3",
    )

    result = registry.invoke(
        "agent.delegate",
        {
            "recipient_role": "scene_director",
            "objective": "Revise the staging of scene 3.",
            "artifact_refs": [artifact.model_dump(mode="json")],
            "constraints": ["Preserve the accepted ending."],
            "expected_output": "A scene patch and concise rationale.",
        },
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output == {
        "delegation_id": "delegation-1",
        "accepted": True,
        "status": "COMPLETE",
        "summary": "Handled by scene_director",
    }
    assert result.artifact_refs == [artifact]
