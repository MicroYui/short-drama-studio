"""Current Leader read observations, backed by the existing context snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.model_input import json_text
from short_drama.agent_runtime.completion_saved_results import saved_completion_artifact_refs
from short_drama.agent_runtime.media_handoffs import MEDIA_OBSERVATION_GUIDANCE, observed_media_handoffs
from short_drama.agent_runtime.models import (
    ActionRecord, ActionStatus, AgentResultStatus, ArtifactRef, DelegationStatus,
    SpecialistTurnRecord, SpecialistTurnStatus,
)
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.studio_trace import record_trace

if TYPE_CHECKING:
    from short_drama.tools.contracts import ToolDescriptor


LEADER_READ_TOKEN_BUDGET = 32_000
COMPLETION_HANDOFF_TOKEN_BUDGET = 16_000
BROAD_COMPLETION_REF_TYPES = frozenset({
    "project", "asset_plan", "asset_plan_index", "script", "script_version", "storyboard", "storyboard_draft",
    "production_spec", "production_document", "production_document_version", "bible", "bible_version", "generation_plan",
})


def exact_context_values(value: JsonValue) -> set[str]:
    """Match structured identifiers by equality; never infer scope from prose."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for child in value for item in exact_context_values(child)}
    if isinstance(value, dict):
        return {item for child in value.values() for item in exact_context_values(child)}
    return set()


def completed_work_handoffs(
    store: AgentRuntimeStore, *, run_id: str, project_id: str,
    selected_ids: set[str] | None = None, exclude_work_order_id: str | None = None,
    token_budget: int = COMPLETION_HANDOFF_TOKEN_BUDGET,
) -> tuple[list[JsonValue], list[ArtifactRef]]:
    """Retain completed deliveries, not the specialist's turns or old ownership.

    Leader receives this run's completion inventory. A Worker receives only
    deliveries intersecting its explicit object/media identifiers. Large bodies
    remain pageable through the same durable context mechanism as Tool results.
    """
    run = store.get_run(run_id)
    if run is None or run.project_id != project_id:
        return [], []
    handoffs: list[JsonValue] = []
    visible_refs: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    remaining = max(0, token_budget)
    broad_types = BROAD_COMPLETION_REF_TYPES
    works = store.list_work_orders(run_id, statuses=(DelegationStatus.COMPLETED,))
    for work in reversed(works):
        if work.id == exclude_work_order_id:
            continue
        result = store.get_agent_result(work.id)
        if result is None or result.status != AgentResultStatus.COMPLETED or result.stale_handoff:
            continue
        refs = [ref for ref in [*result.created_artifacts, *result.updated_artifacts,
                              *saved_completion_artifact_refs(result.completion_receipts)]
                if ref.project_id == project_id]
        relevant_ids = {ref.artifact_id for ref in refs if ref.artifact_type not in broad_types}
        # A selected parent Asset can be named inside the typed delivery even
        # when its created_artifacts contain only the exact child versions.
        def subject_ids(value: JsonValue) -> set[str]:
            if isinstance(value, list):
                return {item for child in value for item in subject_ids(child)}
            if isinstance(value, dict):
                artifact_type = value.get("artifact_type")
                if isinstance(artifact_type, str) and artifact_type in broad_types:
                    return set()
                ids = {child for key, child in value.items() if key in {
                    "asset_id", "asset_version_id", "shot_id", "take_id", "item_id", "work_item_id", "artifact_id",
                } and isinstance(child, str)}
                return ids | {item for child in value.values() for item in subject_ids(child)}
            return set()
        relevant_ids.update(subject_ids(result.deliverable))
        relevant_ids.update(subject_ids(cast(JsonValue, result.completion_receipts)))
        matched_ids = None if selected_ids is None else selected_ids & (relevant_ids | {work.id, result.id})
        if matched_ids is not None and not matched_ids:
            continue
        payload: dict[str, JsonValue] = {"result": result.model_dump(mode="json")}
        snapshot_id = f"context-completion-{result.id}"
        saved = store.get_context_snapshot(snapshot_id, run_id)
        if saved is not None and saved != payload:
            snapshot_id += f"-{uuid4().hex[:12]}"
            saved = None
        if saved is None:
            store.save_context_snapshot(snapshot_id, run_id, payload)
            record_trace("context.offloaded", {"snapshot_id": snapshot_id, "run_id": run_id,
                "payload": payload, "reason": "completed_work_handoff"})
        ref = saved_tool_result_ref(project_id, run_id, snapshot_id)
        ref["note"] = "Exact completed Worker result at the stated time; current media availability and new requested work remain separate."
        body: dict[str, JsonValue] = {key: value for key, value in result.model_dump(mode="json").items()
            if key in {"deliverable", "findings", "decisions", "assumptions", "unresolved_questions",
                       "recommended_next_actions", "completion_receipts"} and value}
        if matched_ids is not None and work.id not in matched_ids and result.id not in matched_ids:
            delivery = body.get("deliverable")
            if isinstance(delivery, dict):
                body["deliverable"] = {key: (
                    [row for row in value if not (subject_ids(row) | (exact_context_values(row) & relevant_ids))
                     or exact_context_values(row) & matched_ids]
                    if isinstance(value, list) and value and all(isinstance(row, dict) for row in value)
                    else value
                ) for key, value in delivery.items()}
            receipt_ids = matched_ids | exact_context_values(body.get("deliverable"))
            receipts = body.get("completion_receipts")
            if isinstance(receipts, list):
                def receipt_subjects(receipt: JsonValue) -> set[str]:
                    if isinstance(receipt, dict) and receipt.get("kind") in {"saved_media_metadata", "saved_media_observation"}:
                        # A chosen parent is not a choice of every crop/derived
                        # child. Keep the child's sources only after choosing it.
                        return {value for key in ("asset_id", "asset_version_id")
                                if isinstance(value := receipt.get(key), str)}
                    return subject_ids(receipt)
                body["completion_receipts"] = [receipt for receipt in receipts
                    if not receipt_subjects(receipt) or receipt_subjects(receipt) & receipt_ids]
                for receipt in body["completion_receipts"]:
                    if isinstance(receipt, dict) and receipt.get("kind") == "saved_tool_result":
                        raw_refs = receipt.get("artifact_refs")
                        if isinstance(raw_refs, list):
                            receipt["artifact_refs"] = [ref for ref in raw_refs
                                if exact_context_values(ref) & receipt_ids]
        size = count_text_tokens(json_text(body))
        view: JsonValue = body if size <= remaining else {**ref, "estimated_tokens": size}
        if size <= remaining:
            remaining -= size
        summary: JsonValue = result.concise_summary
        if count_text_tokens(result.concise_summary) > 512:
            summary = {"context_ref": snapshot_id, "pointer": "/result/concise_summary"}
        handoff: dict[str, JsonValue] = {"work_order_id": work.id, "result_id": result.id,
            "role": work.recipient_role, "status": result.status.value,
            "completed_at": result.created_at.isoformat(), "concise_summary": summary,
            "matched_artifact_ids": None if matched_ids is None else cast(JsonValue, sorted(matched_ids)),
            "saved_result": ref, "result": view}
        handoffs.append(handoff)
        body_ids = exact_context_values(body)
        for artifact in refs:
            if selected_ids is None or artifact.artifact_id in selected_ids or (
                artifact.artifact_type not in broad_types and artifact.artifact_id in body_ids
            ):
                visible_refs[artifact.identity()] = artifact
    return handoffs, list(visible_refs.values())


def saved_tool_result_ref(project_id: str, run_id: str, snapshot_id: str) -> dict[str, JsonValue]:
    return {
        "context_ref": snapshot_id,
        "pointer": "/result",
        "read": {"tool_name": "agent.read_context", "arguments": {
            "project_id": project_id, "run_id": run_id, "snapshot_id": snapshot_id,
            "pointer": "/result", "offset": 0, "limit": 6000,
        }},
        "note": "Exact observed result; refresh canonical content if it has since changed.",
    }


def project_leader_completion_actions(
    context: dict[str, JsonValue], working_set: Mapping[str, JsonValue], *,
    store: AgentRuntimeStore, run_id: str,
) -> dict[str, JsonValue]:
    """Share identical completion fields only with their exact saved result.

    This changes the outgoing view, never the ContextPack or historical result.
    Legacy digests without result identity and scoped/different handoffs stay
    intact. The durable pointer survives subsequent working-set budget offload.
    """
    actions = context.get("recent_actions")
    handoffs = working_set.get("completion_handoffs")
    if not isinstance(actions, list) or not isinstance(handoffs, list):
        return context
    projected: list[JsonValue] = []
    for action in actions:
        projected.append(action)
        if not isinstance(action, dict) or action.get("status") != "SUCCEEDED":
            continue
        digest = action.get("agent_result")
        if not isinstance(digest, dict) or digest.get("status") != "COMPLETED" or not digest.get("result_id"):
            continue
        matches = [row for row in handoffs if isinstance(row, dict)
            and row.get("result_id") == digest["result_id"]
            and row.get("work_order_id") == action.get("work_order_id")
            and row.get("status") == "COMPLETED" and row.get("matched_artifact_ids") is None]
        if len(matches) != 1:
            continue
        handoff = matches[0]
        saved = handoff.get("saved_result")
        if not isinstance(saved, dict) or not isinstance(saved.get("context_ref"), str) or saved.get("pointer") != "/result":
            continue
        snapshot_id = cast(str, saved["context_ref"])
        snapshot = store.get_context_snapshot(snapshot_id, run_id)
        source = None if snapshot is None else snapshot.get("result")
        if (not isinstance(source, dict) or source.get("id") != digest["result_id"]
                or source.get("work_order_id") != action.get("work_order_id")
                or source.get("status") != "COMPLETED"):
            continue
        visible = handoff.get("result")
        if not isinstance(visible, dict):
            continue
        if visible.get("context_ref") == snapshot_id and visible.get("pointer") == "/result":
            visible = source
        shared = [key for key in ("deliverable", "completion_receipts", "findings")
            if digest.get(key) and digest[key] == visible.get(key) == source.get(key)]
        if shared:
            projected[-1] = {**action, "agent_result": {
                **{key: value for key, value in digest.items() if key not in shared},
                "shared_completion_fields": cast(JsonValue, shared),
                "saved_result": {"context_ref": snapshot_id, "pointer": "/result"},
            }}
    return {**context, "recent_actions": projected}


def persist_leader_tool_result(
    store: AgentRuntimeStore, action: ActionRecord, *,
    arguments: Mapping[str, JsonValue], result: dict[str, JsonValue],
) -> None:
    """Save the complete result before any human-readable summary is shortened."""
    snapshot_id = f"context-action-{action.id}-{uuid4().hex[:12]}"
    payload: dict[str, JsonValue] = {"arguments": dict(arguments), "result": result}
    store.save_context_snapshot(snapshot_id, action.run_id, payload)
    action.tool_result_context_ref = snapshot_id
    store.save_action(action)
    record_trace("context.offloaded", {"snapshot_id": snapshot_id, "run_id": action.run_id,
        "payload": payload, "reason": "leader_tool_result"})


def leader_read_working_set(
    store: AgentRuntimeStore, *, run_id: str, project_id: str,
    tools: Sequence[ToolDescriptor],
    read_key: Callable[[SpecialistTurnRecord, Mapping[str, JsonValue] | None], str],
    project_result: Callable[[SpecialistTurnRecord], dict[str, JsonValue]],
    token_budget: int = LEADER_READ_TOKEN_BUDGET,
) -> dict[str, JsonValue]:
    """Keep distinct object/view/page reads; prefer recent bodies, retain all pointers.

    This uses the same read identity and scoped projection as Worker context.
    The bounded model view is rebuilt after restart; it never scans raw Trace or
    promotes every old result into the prompt. Historical actions without a
    saved result remain summaries rather than invented recoverable snapshots.
    """
    read_tools = {tool.id: tool for tool in tools if tool.effect.value == "READ"}
    selected: dict[str, tuple[ActionRecord, SpecialistTurnRecord]] = {}
    for sequence, action in enumerate(store.list_actions(run_id), 1):
        if (action.status != ActionStatus.SUCCEEDED or action.tool_result_context_ref is None
                or action.tool_name not in read_tools):
            continue
        snapshot = store.get_context_snapshot(action.tool_result_context_ref, run_id)
        if snapshot is None:
            continue
        arguments, result = snapshot.get("arguments"), snapshot.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        assert action.tool_name is not None
        turn = SpecialistTurnRecord(id=action.id, sequence=sequence, tool_name=action.tool_name,
            arguments=arguments, reason=action.input_summary, status=SpecialistTurnStatus.COMPLETED,
            result=result)
        key = read_key(turn, cast(dict[str, JsonValue], read_tools[action.tool_name].input_schema))
        selected[key] = (action, turn)

    observations: list[JsonValue] = []
    artifact_refs = {ref.identity(): ref for action, _turn in selected.values() for ref in action.artifact_refs}
    remaining = token_budget
    for action, turn in sorted(selected.values(), key=lambda pair: pair[1].sequence, reverse=True):
        assert action.tool_result_context_ref is not None
        ref = saved_tool_result_ref(project_id, run_id, action.tool_result_context_ref)
        result = project_result(turn)
        size = count_text_tokens(json_text(result))
        if size <= remaining:
            view: JsonValue = result
            remaining -= size
        else:
            # Do not slice prose or JSON. Even a displaced observation retains
            # the exact field/page entry point and its original read selectors.
            output = result.get("output")
            view = {**ref, "estimated_tokens": size,
                "keys": cast(JsonValue, list(result)),
                "output_keys": cast(JsonValue, list(output) if isinstance(output, dict) else [])}
        observations.append({
            "source_action_id": action.id, "source_sequence": turn.sequence,
            "tool_name": turn.tool_name, "arguments": turn.arguments,
            "observed_at": action.updated_at.isoformat(), "saved_result": ref,
            "result": view,
        })
    completions, completed_refs = completed_work_handoffs(store, run_id=run_id, project_id=project_id)
    artifact_refs.update({ref.identity(): ref for ref in completed_refs})
    media, media_refs = observed_media_handoffs(store, run_id=run_id, project_id=project_id)
    artifact_refs.update({ref.identity(): ref for ref in media_refs})
    return {"read_observations": observations, "completion_handoffs": completions,
        **({"media_observations": media, "media_observation_guidance": MEDIA_OBSERVATION_GUIDANCE} if media else {}),
        "artifact_refs": [ref.model_dump(mode="json") for ref in artifact_refs.values()]}
