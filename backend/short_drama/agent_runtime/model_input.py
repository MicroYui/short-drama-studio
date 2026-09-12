"""Budget the complete model request, retaining exact overflow behind read Tools."""

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from short_drama.agent_runtime.context_markers import context_marker, context_read_contract
from short_drama.agent_runtime.store import AgentRuntimeStore
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.studio_trace import record_trace
from short_drama.agent_runtime.execution_control import execution_boundary
from short_drama.agent_runtime.input_references import project_visible_artifact_refs
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient, LanguageProviderContextWindowError


class ContextBudgetExceeded(ValueError):
    """Protected instructions cannot fit; no oversized paid request was made."""


def json_text(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pointer_value(value: JsonValue, pointer: str) -> JsonValue:
    if pointer and not pointer.startswith("/"):
        raise ValueError("A JSON pointer must be empty or start with /")
    for token in pointer.split("/")[1:] if pointer else []:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value:
                raise ValueError(f"Context pointer does not exist: {pointer}")
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError(f"Context pointer does not exist: {pointer}")
    return value


def _child(path: str, key: str | int) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _replace(payload: dict[str, JsonValue], pointer: str, replacement: JsonValue) -> None:
    parent_path, _, key = pointer.rpartition("/")
    parent = pointer_value(payload, parent_path)
    if isinstance(parent, list):
        parent[int(key)] = replacement
    elif isinstance(parent, dict):
        parent[key.replace("~1", "/").replace("~0", "~")] = replacement
    else:
        raise ValueError(f"Invalid context replacement: {pointer}")


def _candidates(payload: dict[str, JsonValue]) -> list[str]:
    """Only offload recoverable data, never user intent or runtime/output rules."""
    paths: list[str] = []
    work = payload.get("work_order")
    if isinstance(work, dict) and isinstance(work.get("task_inputs"), dict):
        paths.extend(_child("/work_order/task_inputs", key) for key in cast(dict[str, JsonValue], work["task_inputs"]))
    plan = payload.get("current_plan")
    if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
        for index, step in enumerate(cast(list[JsonValue], plan["steps"])):
            if isinstance(step, dict):
                paths.extend(f"/current_plan/steps/{index}/{key}" for key in ("arguments", "instructions") if key in step)
    working = payload.get("working_set")
    if isinstance(working, dict):
        for key in ("read_observations", "tool_attempt_observations", "completion_handoffs", "media_observations", "entities", "tool_checkpoints", "artifact_refs"):
            values = working.get(key)
            if isinstance(values, list):
                if key in {"entities", "tool_checkpoints", "artifact_refs"}:
                    paths.append(f"/working_set/{key}")
                    continue
                for index, value in enumerate(values):
                    path = f"/working_set/{key}/{index}"
                    paths.append(path + "/result" if isinstance(value, dict) and "result" in value else path)
                # Many different small observations can exceed the budget too.
                # Use their collection only after trying the larger single results.
                paths.append(f"/working_set/{key}")
        latest = working.get("latest_handoff")
        if isinstance(latest, dict) and "result" in latest:
            paths.append("/working_set/latest_handoff/result")
    for key in ("response_protocol_feedback", "plan_validation_feedback"):
        feedback = payload.get(key)
        if isinstance(feedback, dict):
            paths.extend(_child("/" + key, item) for item in ("rejected_response", "rejected_plan") if item in feedback)
    return paths


@dataclass(frozen=True)
class PreparedModelInput:
    input_text: str
    input_tokens: int
    original_input_tokens: int
    offloaded_blocks: int


class ModelInputCompiler:
    def __init__(self, store: AgentRuntimeStore | None = None, *, budget: ContextBudget = ContextBudget()) -> None:
        self.store = store
        self.budget = budget

    def prepare(self, *, run_id: str, project_id: str, instructions: str,
                payload: dict[str, JsonValue], max_output_tokens: int,
                force_compact: bool = False) -> PreparedModelInput:
        prepared = deepcopy(payload)
        read_contract = context_read_contract(project_id=project_id, run_id=run_id)
        working = prepared.get("working_set")
        if isinstance(working, dict) and working.get("context_read") == read_contract:
            # Keep one API explanation outside the sections that may be
            # externalized. Only move our own generated contract, not user data.
            working.pop("context_read")
            prepared["context_read"] = read_contract
        instruction_tokens = count_text_tokens(instructions)
        original_text = json_text(prepared)
        original_tokens = instruction_tokens + count_text_tokens(original_text)
        hard_limit = self.budget.input_limit(max_output_tokens)
        threshold = min(self.budget.compact_at_tokens, hard_limit)
        target = min(self.budget.target_tokens, hard_limit)
        # A provider can report a smaller real limit than its compatible model
        # alias implies. Rebuild from the original, never resend the same overflow.
        if force_compact:
            target = min(target, max(1, original_tokens // 2))
        current_tokens = original_tokens
        count = 0
        snapshot_id: str | None = None
        if current_tokens > threshold or force_compact:
            collection_fallbacks = {"/working_set/read_observations", "/working_set/tool_attempt_observations", "/working_set/completion_handoffs", "/working_set/media_observations"}
            candidates = sorted(
                ((count_text_tokens(json_text(pointer_value(prepared, path))), path) for path in _candidates(prepared)),
                key=lambda item: (item[1] in collection_fallbacks, -item[0]),
            )
            for size, path in candidates:
                if current_tokens <= target:
                    break
                if size < 512:
                    continue
                if self.store is None:
                    break
                if snapshot_id is None:
                    snapshot_id = f"context-{uuid4().hex}"
                    self.store.save_context_snapshot(snapshot_id, run_id, payload)
                    record_trace("context.offloaded", {"snapshot_id": snapshot_id, "run_id": run_id, "payload": payload})
                value = pointer_value(prepared, path)
                marker = context_marker(snapshot_id, path, estimated_tokens=size)
                if isinstance(value, dict):
                    marker["keys"] = cast(JsonValue, list(value)[:24])
                    for key in ("id", "kind", "tool_name", "status", "source_sequence"):
                        if key in value and isinstance(value[key], (str, int)):
                            marker[key] = value[key]
                elif isinstance(value, list):
                    marker["item_count"] = len(value)
                if count_text_tokens(json_text(marker)) >= size:
                    continue
                _replace(prepared, path, marker)
                prepared["context_read"] = read_contract
                count += 1
                current_tokens = instruction_tokens + count_text_tokens(json_text(prepared))
        # Only share references that remain visible after offloading. Persisted
        # snapshots retain the original complete Tool envelopes and bodies.
        prepared = project_visible_artifact_refs(prepared)
        text = json_text(prepared)
        current_tokens = instruction_tokens + count_text_tokens(text)
        record_trace("context.prepared", {
            "run_id": run_id, "window_tokens": self.budget.window_tokens,
            "compact_at_tokens": self.budget.compact_at_tokens, "target_tokens": target,
            "input_limit_tokens": hard_limit, "reserved_output_tokens": max_output_tokens,
            "envelope_tokens": self.budget.envelope_tokens,
            "estimated_input_tokens": current_tokens, "original_input_tokens": original_tokens,
            "estimator": "o200k_base", "instruction_characters": len(instructions),
            "input_characters": len(text), "original_input_characters": len(original_text),
            "offloaded_blocks": count, "snapshot_id": snapshot_id,
            "within_budget": current_tokens <= hard_limit,
        })
        if current_tokens > hard_limit:
            raise ContextBudgetExceeded(
                f"当前必要输入约 {current_tokens:,} tokens，超过 {self.budget.window_tokens:,} "
                f"上下文预算中预留输出后的 {hard_limit:,} tokens。可恢复内容已保存；"
                "需要缩小当前 WorkOrder 或按字段读取，未发送超限请求。"
            )
        return PreparedModelInput(text, current_tokens, original_tokens, count)


def generate_context_managed_text(client: LanguageProviderClient, compiler: ModelInputCompiler, *,
                                  run_id: str, project_id: str, instructions: str,
                                  payload: dict[str, JsonValue], max_output_tokens: int) -> str:
    prepared = compiler.prepare(run_id=run_id, project_id=project_id, instructions=instructions,
                                payload=payload, max_output_tokens=max_output_tokens)
    execution_boundary()
    try:
        return client.generate_text(instructions=instructions, input_text=prepared.input_text,
                                    max_output_tokens=max_output_tokens)
    except LanguageProviderContextWindowError:
        reduced = compiler.prepare(run_id=run_id, project_id=project_id, instructions=instructions,
                                   payload=payload, max_output_tokens=max_output_tokens, force_compact=True)
        if reduced.input_tokens >= prepared.input_tokens:
            raise ContextBudgetExceeded("供应商拒绝了上下文，必要输入无法进一步整理；未原样重发。") from None
        record_trace("context.provider_overflow", {"before_tokens": prepared.input_tokens,
                     "after_tokens": reduced.input_tokens, "run_id": run_id})
        execution_boundary()
        return client.generate_text(instructions=instructions, input_text=reduced.input_text,
                                    max_output_tokens=max_output_tokens)
