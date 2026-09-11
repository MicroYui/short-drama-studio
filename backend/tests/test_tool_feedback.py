"""Tool traces and receipts should make local repair possible without replaying writes."""

from dataclasses import replace
from pathlib import Path
from typing import ClassVar, cast

from pydantic import Field, JsonValue

from short_drama.studio_trace import production_trace
from short_drama.tools.contracts import ArtifactRef, ToolCallStatus, ToolCategory, ToolDescriptor, ToolEffect, ToolExecution, ToolModel, ToolSpec
from short_drama.tools.registry import ToolRegistry
from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.production_manager import _specialist_turn_handoff


class ClipInput(ToolModel):
    id: str
    start_seconds: float = Field(ge=0)


class EditRequest(ToolModel):
    clips: list[ClipInput]


class EditInput(ToolModel):
    project_id: str
    request: EditRequest


class SavedOutput(ToolModel):
    editorial_revision: int
    changed_clip_ids: list[str]
    status: str = "saved"


class CountingSpec(ToolSpec[EditInput, SavedOutput]):
    schema_calls: ClassVar[int] = 0

    def descriptor(self) -> ToolDescriptor:
        type(self).schema_calls += 1
        return super().descriptor()


def test_descriptor_is_computed_once_per_registration_and_returns_protected_copies() -> None:
    def save(_request: EditInput) -> ToolExecution[SavedOutput]:
        return ToolExecution(output=SavedOutput(editorial_revision=3, changed_clip_ids=["clip-a"]))

    spec = CountingSpec(id="timeline.edit", version=1, category=ToolCategory.TIMELINE,
        description="Save selected clips.", effect=ToolEffect.WRITE, idempotent=True,
        input_model=EditInput, output_model=SavedOutput, handler=save)
    before = CountingSpec.schema_calls
    first = ToolRegistry()
    first.register(spec)
    described = first.describe(spec.id)
    described.input_schema.clear()
    described.tags.append("should-not-leak")
    for _ in range(20):
        actual = first.list()[0]
        assert "properties" in actual.input_schema
        assert "should-not-leak" not in actual.tags
    assert CountingSpec.schema_calls == before + 1
    second = ToolRegistry()
    second.register(replace(spec, version=2))
    assert second.describe(spec.id).version == 2
    assert CountingSpec.schema_calls == before + 2


def test_invalid_nested_field_is_traced_with_exact_scope_and_has_no_write(tmp_path: Path) -> None:
    writes: list[str] = []
    events: list[tuple[str, dict[str, JsonValue]]] = []

    def save(request: EditInput) -> ToolExecution[SavedOutput]:
        writes.append(request.project_id)
        return ToolExecution(output=SavedOutput(editorial_revision=3, changed_clip_ids=["clip-a"]))

    registry = ToolRegistry()
    registry.register(ToolSpec(id="timeline.edit", version=1, category=ToolCategory.TIMELINE,
        description="Save selected clips.", effect=ToolEffect.WRITE, idempotent=True,
        input_model=EditInput, output_model=SavedOutput, handler=save))
    with production_trace(tmp_path, lambda kind, data: events.append((kind, data))):
        result = registry.invoke("timeline.edit", {"project_id": "project-1", "request": {
            "clips": [{"id": "clip-7", "start_seconds": -1}]}})
    assert not writes
    assert result.status == ToolCallStatus.INVALID_ARGUMENTS
    assert result.error is not None
    error = result.error.details[0]
    assert error.path == "/request/clips/0/start_seconds"
    assert error.identifiers["/request/clips/0"] == {"id": "clip-7", "start_seconds": -1}
    assert error.constraints == {"ge": 0}
    assert [kind for kind, _data in events] == ["tool.started", "tool.finished"]
    assert events[0][1]["tool_call_id"] == events[1][1]["tool_call_id"]
    assert events[1][1]["status"] == "INVALID_ARGUMENTS"
    assert cast(dict[str, JsonValue], events[1][1]["error_details"])["phase"] == "VALIDATION"
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_write_receipt_uses_saved_response_and_not_requested_ids() -> None:
    ref = ArtifactRef(project_id="project-1", artifact_type="editorial_timeline", artifact_id="film", version=3)

    def save(_request: EditInput) -> ToolExecution[SavedOutput]:
        return ToolExecution(output=SavedOutput(editorial_revision=3, changed_clip_ids=["clip-saved"], status="queued"), artifact_refs=(ref,))

    registry = ToolRegistry()
    registry.register(ToolSpec(id="timeline.edit", version=1, category=ToolCategory.TIMELINE,
        description="Save selected clips.", effect=ToolEffect.EXTERNAL, idempotent=True,
        input_model=EditInput, output_model=SavedOutput, handler=save))
    result = registry.invoke("timeline.edit", {"project_id": "project-1", "request": {"clips": [{"id": "clip-requested", "start_seconds": 0}]}})
    assert result.receipt is not None
    assert result.receipt.artifact_refs == [ref]
    assert result.receipt.current_state == {"editorial_revision": 3, "status": "queued"}
    assert result.receipt.changed_ids == {"changed_clip_ids": ["clip-saved"]}


def test_output_adapter_failure_preserves_written_refs_and_prevents_blind_retry(tmp_path: Path) -> None:
    ref = ArtifactRef(project_id="project-1", artifact_type="editorial_timeline", artifact_id="film", version=3)

    def save(_request: EditInput) -> ToolExecution[SavedOutput]:
        return ToolExecution(output=cast(SavedOutput, {"editorial_revision": "invalid"}), artifact_refs=(ref,))

    registry = ToolRegistry()
    registry.register(ToolSpec(id="timeline.edit", version=1, category=ToolCategory.TIMELINE,
        description="Save selected clips.", effect=ToolEffect.WRITE, idempotent=True,
        input_model=EditInput, output_model=SavedOutput, handler=save))
    with production_trace(tmp_path, lambda _kind, _data: None):
        result = registry.invoke("timeline.edit", {"project_id": "project-1", "request": {"clips": []}})
        missing = registry.invoke("timeline.unknown", {})
    assert result.artifact_refs == [ref]
    assert result.error is not None and result.error.phase == "OUTPUT_VALIDATION"
    assert result.error.retryable is False
    assert result.error.details[0].path == "/output/editorial_revision"
    assert "already ran" in result.error.suggested_actions[0]
    assert missing.error is not None and missing.error.phase == "LOOKUP"
    assert len(list(tmp_path.glob("*.json"))) == 4


def test_compact_worker_handoff_keeps_the_saved_receipt() -> None:
    receipt: dict[str, JsonValue] = {"operation_completed": True, "current_state": {"editorial_revision": 7},
        "changed_ids": {"changed_clip_ids": ["clip-a"]}, "artifact_refs": []}
    turn = SpecialistTurnRecord(id="turn-1", sequence=1, tool_name="timeline.edit_clips", arguments={},
        reason="Save a trim.", status=SpecialistTurnStatus.COMPLETED,
        result={"tool_id": "timeline.edit_clips", "status": "SUCCEEDED", "output": {"large": "x" * 10000},
                "receipt": receipt, "artifact_refs": []})
    handoff = _specialist_turn_handoff(turn, retain_exact_payload=False, compact_write_result=True)
    projected = cast(dict[str, JsonValue], handoff["result"])
    assert projected["receipt"] == receipt
    assert "output" not in projected
