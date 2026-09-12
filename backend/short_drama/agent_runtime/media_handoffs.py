"""Share saved media observations without waiting for a whole delegation.

This is a view of existing Tool turns, not another media/approval database. It
does not schedule a model call. Exact reports use the existing context reader,
including when the observing Worker is still busy with other assets.
"""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.models import (
    ArtifactRef, DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder,
)
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.studio_trace import record_trace


MEDIA_OBSERVATION_TOKEN_BUDGET = 12_000
MEDIA_OBSERVATION_GUIDANCE = (
    "These are saved observations about exact media, including partial deliveries from busy Workers. "
    "The source WorkOrder status does not decide whether its saved media can be reused. "
    "Keep each inspection's purpose, references, time and limitations: a different use or edited image "
    "is not automatically checked. IN_PROGRESS means a check is running, never that it passed. "
    "Reuse applicable visible findings; read omitted details through saved_observation.read rather than "
    "repeat an inspection to recover them. New visual questions may still need inspection. "
    "A saved reference entry records its stated use, not independent quality approval or current file availability."
)


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _strings(values: list[JsonValue]) -> set[str]:
    return {value for value in values if isinstance(value, str) and value}


def asset_inspection_scope(request: JsonValue) -> dict[str, JsonValue]:
    """Normalize optional defaults without dropping unknown or selected scope."""
    scope: dict[str, JsonValue] = {
        "shot_id": None, "canonical_reference_version_ids": [], "review_focus": None, "regions": [],
        **(request if isinstance(request, dict) else {}),
    }
    narrative = scope.get("narrative_context")
    if narrative is None:
        scope.pop("narrative_context", None)
    elif isinstance(narrative, dict):
        scope["narrative_context"] = {"dialogue_line_ids": [], **narrative}
    if scope.get("implementation_preferences") == []:
        scope.pop("implementation_preferences")
    return scope


def _observation(
    work: WorkOrder, turn: SpecialistTurnRecord, project_id: str,
) -> tuple[str, dict[str, JsonValue], set[str], list[ArtifactRef]] | None:
    args = turn.arguments
    if args.get("project_id") != project_id:
        return None
    result = turn.result or {}
    output = _dict(result.get("output"))
    request = _dict(args.get("request"))
    pending = turn.status in (SpecialistTurnStatus.REQUESTED, SpecialistTurnStatus.IN_PROGRESS)
    success = turn.status == SpecialistTurnStatus.COMPLETED and result.get("status") == "SUCCEEDED"
    if turn.tool_name == "qa.inspect_asset_version":
        if not success and not (pending and work.status in (
            DelegationStatus.PENDING, DelegationStatus.IN_PROGRESS, DelegationStatus.NEEDS_ATTENTION,
        )):
            return None
        asset_id, version_id = args.get("asset_id"), args.get("version_id")
        if not isinstance(version_id, str) or not version_id:
            return None
        scope = asset_inspection_scope(request)
        identity: JsonValue = [turn.tool_name, asset_id, version_id, scope,
                               output.get("narrative_context") if success else None,
                               "pending" if pending else "saved"]
        body: dict[str, JsonValue] = {"kind": "image_inspection", "asset_id": asset_id,
            "asset_version_id": version_id, "scope": scope, "result": output if success else None}
        reference_ids = scope.get("canonical_reference_version_ids")
        related = _strings([asset_id, version_id, scope.get("shot_id"),
            *(reference_ids if isinstance(reference_ids, list) else [])])
    elif turn.tool_name == "artifact.add_reference_entry" and success:
        entry, pack = _dict(output.get("entry")), _dict(output.get("pack"))
        asset_id, version_id = pack.get("subject_asset_id"), entry.get("asset_version_id")
        if not isinstance(version_id, str) or not isinstance(asset_id, str):
            return None
        # Use the entry returned by the save, never the requested intent.
        identity = [turn.tool_name, asset_id, entry.get("id")]
        body = {"kind": "saved_reference_use", "asset_id": asset_id,
            "asset_version_id": version_id, "result": {"pack_id": pack.get("id"), "entry": entry}}
        related = _strings([asset_id, version_id, entry.get("lineage_root_version_id")])
    else:
        return None
    refs: list[ArtifactRef] = []
    raw_refs = result.get("artifact_refs")
    for value in raw_refs if isinstance(raw_refs, list) else []:
        if not isinstance(value, dict):
            continue
        try:
            ref = ArtifactRef.model_validate(value)
        except ValueError:
            continue
        if ref.project_id == project_id:
            refs.append(ref)
    body.update({"work_order_id": work.id, "role": work.recipient_role,
        "work_order_status": work.status.value, "attempt_id": turn.id,
        "tool_name": turn.tool_name, "status": "IN_PROGRESS" if pending else "SUCCEEDED",
        "observed_at": turn.updated_at.isoformat()})
    return json.dumps(identity, ensure_ascii=False, sort_keys=True), body, related | {work.id}, refs


def media_turn_observations(
    work: WorkOrder, turn: SpecialistTurnRecord, project_id: str,
) -> list[tuple[str, dict[str, JsonValue], set[str], list[ArtifactRef]]]:
    """Project each actual saved use while retaining its original Tool turn."""
    if turn.tool_name != "artifact.add_reference_entries":
        observation = _observation(work, turn, project_id)
        return [] if observation is None else [observation]
    result = turn.result or {}
    output = _dict(result.get("output"))
    pack = _dict(output.get("pack"))
    entries, entry_ids = pack.get("entries"), output.get("entry_ids")
    if not isinstance(entries, list) or not isinstance(entry_ids, list):
        return []
    by_id = {entry.get("id"): entry for entry in entries
             if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    observations: list[tuple[str, dict[str, JsonValue], set[str], list[ArtifactRef]]] = []
    for entry_id in entry_ids:
        if not isinstance(entry_id, str) or entry_id not in by_id:
            continue
        # Reuse the single-use projection only on a copy; saved_observation
        # still points to the complete original batch request and receipt.
        projected_turn = turn.model_copy(update={"tool_name": "artifact.add_reference_entry",
            "result": {**result, "output": {"pack": pack, "entry": by_id[entry_id]}}})
        observation = _observation(work, projected_turn, project_id)
        if observation is not None:
            key, body, related, refs = observation
            body["tool_name"] = turn.tool_name
            entry = by_id[entry_id]
            version_ids = _strings([entry.get("asset_version_id"), entry.get("lineage_root_version_id")])
            # A scoped reader must not inherit the other uses' media merely
            # because all entries share one durable source receipt.
            scoped_refs = [ref for ref in refs if (
                (ref.artifact_type == "reference_pack" and ref.artifact_id == pack.get("id"))
                or (ref.artifact_type == "reference_pack_entry" and ref.artifact_id == entry_id)
                or (ref.artifact_type == "asset_version" and ref.artifact_id in version_ids)
            )]
            observations.append((key, body, related, scoped_refs))
    return observations


def observed_media_handoffs(
    store: AgentRuntimeStore, *, run_id: str, project_id: str,
    selected_ids: set[str] | None = None, exclude_work_order_id: str | None = None,
    token_budget: int = MEDIA_OBSERVATION_TOKEN_BUDGET,
) -> tuple[list[JsonValue], list[ArtifactRef]]:
    """Project latest reports per exact scope; keep different uses independently.

    Leader gets partial deliveries (completed deliveries have their own view).
    A Worker gets observations intersecting selected objects, exact media or
    their explicitly stated reference inputs. Common project/plan IDs do not
    match. Completed/cancelled work can still supply historical image facts,
    but never an in-progress check or a revived work assignment.
    """
    run = store.get_run(run_id)
    if run is None or run.project_id != project_id:
        return [], []
    selected: dict[str, tuple[WorkOrder, SpecialistTurnRecord, dict[str, JsonValue], list[ArtifactRef]]] = {}
    for work in store.list_work_orders(run_id):
        if work.id == exclude_work_order_id or (selected_ids is None and work.status == DelegationStatus.COMPLETED):
            continue
        for turn in work.specialist_turns:
            for key, body, related, refs in media_turn_observations(work, turn, project_id):
                if selected_ids is not None and not selected_ids & related:
                    continue
                previous = selected.get(key)
                if previous is None or previous[1].updated_at < turn.updated_at:
                    selected[key] = (work, turn, body, refs)

    rows: list[JsonValue] = []
    visible_refs: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    remaining = max(0, token_budget)
    for _work, turn, body, refs in sorted(selected.values(), key=lambda item: item[1].updated_at, reverse=True):
        if body["status"] == "SUCCEEDED":
            # A stable original snapshot makes cross-Worker reading possible
            # through agent.read_context; no guessed/unusable attempt access.
            payload: dict[str, JsonValue] = {"arguments": turn.arguments, "result": turn.result}
            snapshot_id = f"context-media-{turn.id}"
            saved = store.get_context_snapshot(snapshot_id, run_id)
            if saved is not None and saved != payload:
                snapshot_id += f"-{uuid4().hex[:12]}"
                saved = None
            if saved is None:
                store.save_context_snapshot(snapshot_id, run_id, payload)
                record_trace("context.offloaded", {"snapshot_id": snapshot_id, "run_id": run_id,
                    "payload": payload, "reason": "saved_media_observation"})
            marker: dict[str, JsonValue] = {"context_ref": snapshot_id, "pointer": "",
                "read": {"tool_name": "agent.read_context", "arguments": {
                    "project_id": project_id, "run_id": run_id, "snapshot_id": snapshot_id,
                    "pointer": "", "offset": 0, "limit": 6000}}}
            body["saved_observation"] = marker
            detail: dict[str, JsonValue] = {key: body[key] for key in ("scope", "result") if key in body}
            size = count_text_tokens(json.dumps(detail, ensure_ascii=False))
            if size > remaining:
                # Keep the exact page route, not a sliced prose summary that
                # silently omits coordinates or a limiting qualification.
                body.pop("scope", None)
                body["result"] = {**marker, "estimated_tokens": size}
            else:
                remaining -= size
        rows.append(body)
        for ref in refs:
            visible_refs[ref.identity()] = ref
    return rows, list(visible_refs.values())
