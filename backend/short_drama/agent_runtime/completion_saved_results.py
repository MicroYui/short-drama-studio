"""Attach explicitly selected saved Tool facts to a semantic Worker handoff.

The Worker still decides completion and explains new judgments. These receipts
describe persisted operations and observations; they never infer approval or
turn a queued task into finished media.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from short_drama.agent_runtime.asset_version_context import asset_construction_state
from short_drama.agent_runtime.contract_validation import AgentResultContractError
from short_drama.agent_runtime.context_markers import context_marker, context_read_contract
from short_drama.agent_runtime.media_handoffs import media_turn_observations
from short_drama.agent_runtime.models import ArtifactRef, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.models import ProjectDocument
from short_drama.studio_trace import record_trace


SAVED_RESULT_GUIDANCE = (
    "Optional saved_tool_result_sequences selects successful Tool source_sequence values from this WorkOrder. "
    "Runtime attaches their exact saved artifacts, current file/state receipts and applicable saved inspection/use "
    "observations, with original results available to read. Do not recopy those IDs, paths, dimensions or reports "
    "into prose. State your completion decision, additional judgments, limitations and remaining work once; "
    "keep any task-specific deliverable required by an explicit output schema. Tool success is not goal completion, "
    "quality approval or permission to exceed the user goal. Omit this optional field for analysis without saved outputs."
)


def _successful(turn: SpecialistTurnRecord) -> bool:
    return turn.status == SpecialistTurnStatus.COMPLETED and (turn.result or {}).get("status") == "SUCCEEDED"


def _refs(turn: SpecialistTurnRecord, project_id: str) -> list[ArtifactRef]:
    values = (turn.result or {}).get("artifact_refs")
    refs = [ArtifactRef.model_validate(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []
    if any(ref.project_id != project_id for ref in refs):
        raise AgentResultContractError(f"saved_tool_result_sequences/{turn.sequence}: result belongs to another project")
    return refs


def saved_tool_result_index(work: WorkOrder, project_id: str) -> list[JsonValue]:
    """Short source addresses, including earlier saves of the same Tool kind."""
    rows: dict[tuple[tuple[str, str, str, int | None], ...], JsonValue] = {}
    for turn in work.specialist_turns:
        if not _successful(turn):
            continue
        refs = [ref for ref in _refs(turn, project_id) if ref.artifact_type not in {"project", "bible", "project_document"}]
        if refs:
            # Exact identity includes version; a later read/save of the same
            # outputs can replace its address, not a different media or revision.
            key = tuple(sorted({ref.identity() for ref in refs}, key=str))
            rows[key] = {"source_sequence": turn.sequence, "tool_name": turn.tool_name,
                         "artifacts": [{"artifact_type": ref.artifact_type, "artifact_id": ref.artifact_id,
                                        "version": ref.version} for ref in refs]}
    return list(rows.values())


@dataclass
class SavedCompletionResults:
    artifact_refs: list[ArtifactRef] = field(default_factory=list[ArtifactRef])
    receipts: list[dict[str, JsonValue]] = field(default_factory=list[dict[str, JsonValue]])
    snapshots: dict[str, dict[str, JsonValue]] = field(default_factory=dict[str, dict[str, JsonValue]])

    def persist_sources(self, store: AgentRuntimeStore, run_id: str) -> None:
        for snapshot_id, payload in self.snapshots.items():
            saved = store.get_context_snapshot(snapshot_id, run_id)
            if saved == payload:
                continue
            if saved is not None:
                raise AgentResultContractError(f"Saved completion source changed: {snapshot_id}")
            store.save_context_snapshot(snapshot_id, run_id, payload)
            record_trace("context.offloaded", {"snapshot_id": snapshot_id, "run_id": run_id,
                "payload": payload, "reason": "selected_completion_tool_result"})


def saved_completion_artifact_refs(receipts: Sequence[dict[str, JsonValue]]) -> list[ArtifactRef]:
    """Only explicit saved-output selection contributes additional handoff refs."""
    refs: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    for receipt in receipts:
        if receipt.get("kind") != "saved_tool_result":
            continue
        values = receipt.get("artifact_refs")
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                ref = ArtifactRef.model_validate(value)
                refs[ref.identity()] = ref
    return list(refs.values())


def prepare_saved_completion_results(
    document: ProjectDocument, work: WorkOrder, sequences: Sequence[int],
) -> SavedCompletionResults:
    """Resolve exact successful turns in memory before writing any handoff."""
    result = SavedCompletionResults()
    if not sequences:
        return result
    project_id = document.project.id
    result.receipts.append({"kind": "saved_result_read",
        "read": context_read_contract(project_id=project_id, run_id=work.parent_run_id)})
    by_sequence = {turn.sequence: turn for turn in work.specialist_turns}
    selected: list[SpecialistTurnRecord] = []
    for sequence in dict.fromkeys(sequences):
        turn = by_sequence.get(sequence)
        if turn is None or not _successful(turn):
            raise AgentResultContractError(f"saved_tool_result_sequences/{sequence}: expected a persisted successful Tool result from this WorkOrder")
        selected.append(turn)

    versions = {version.id: (asset, version) for asset in document.assets for version in asset.versions}
    unique: dict[tuple[str, str, str, int | None], ArtifactRef] = {}

    def source(turn: SpecialistTurnRecord) -> dict[str, JsonValue]:
        snapshot_id = f"context-completion-tool-{work.id}-{turn.id}"
        result.snapshots[snapshot_id] = {"arguments": dict(turn.arguments), "result": turn.result}
        return context_marker(snapshot_id, "")

    for turn in selected:
        refs = _refs(turn, project_id)
        if not refs:
            raise AgentResultContractError(f"saved_tool_result_sequences/{turn.sequence}: result has no saved artifact references; report its analysis directly")
        canonical: list[ArtifactRef] = []
        for ref in refs:
            if ref.artifact_type == "asset_version" and ref.version is None and ref.artifact_id in versions:
                # The UUID names the immutable version. Never substitute current/latest.
                ref = ref.model_copy(update={"version": versions[ref.artifact_id][1].version})
            canonical.append(ref)
            unique[ref.identity()] = ref
        result.receipts.append({"kind": "saved_tool_result", "sequence": turn.sequence,
            "tool_name": turn.tool_name, "status": "SUCCEEDED", "observed_at": turn.updated_at.isoformat(),
            "artifact_refs": [ref.model_dump(mode="json") for ref in canonical], "saved_result": source(turn)})

    result.artifact_refs = list(unique.values())
    selected_version_ids = {ref.artifact_id for ref in result.artifact_refs if ref.artifact_type == "asset_version"}
    for version_id in sorted(selected_version_ids):
        found = versions.get(version_id)
        if found is None:
            continue  # Existing completion verification reports the exact missing ref.
        asset, version = found
        raw = version.model_dump(mode="json")
        result.receipts.append({"kind": "saved_media_metadata", "asset_id": asset.id,
            "asset_version_id": version.id, "asset_name": asset.name,
            "metadata": {**{key: raw.get(key) for key in (
                "version", "descriptor", "media_type", "mime_type", "width", "height", "duration_seconds",
                "parent_version_id", "image_task_id", "approval_status", "freshness")}, **asset_construction_state(raw)},
            "note": "Saved exact-version metadata at handoff; COMPLETED verifies file availability separately, never image quality."})

    # Reuse the existing observation identity: different purposes/reference scopes
    # remain separate; a later result for the same exact scope replaces that view.
    observations: dict[str, tuple[SpecialistTurnRecord, dict[str, JsonValue]]] = {}
    for turn in work.specialist_turns:
        if not _successful(turn):
            continue
        for key, body, _related, _evidence in media_turn_observations(work, turn, project_id):
            version_id = body.get("asset_version_id")
            if not isinstance(version_id, str) or version_id not in selected_version_ids:
                continue
            prior = observations.get(key)
            if prior is None or prior[0].updated_at < turn.updated_at:
                observations[key] = (turn, body)
    for turn, body in observations.values():
        output = body.get("result")
        if body.get("kind") == "image_inspection" and isinstance(output, dict):
            # Exact geometry and the complete report stay in the pageable source;
            # the conclusion and its stated scope/limitations remain visible.
            output = {key: output.get(key) for key in ("recommended", "score", "issues", "summary")}
        result.receipts.append({"kind": "saved_media_observation", "asset_id": body.get("asset_id"),
            "asset_version_id": body.get("asset_version_id"), "observation_kind": body.get("kind"),
            "scope": body.get("scope"), "result": output, "observed_at": turn.updated_at.isoformat(),
            "saved_result": source(turn)})
    return result
