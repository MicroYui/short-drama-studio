"""Small exact snapshot locators and one shared model-facing read contract."""

from pydantic import JsonValue


def context_marker(snapshot_id: str, pointer: str, *, estimated_tokens: int | None = None) -> dict[str, JsonValue]:
    marker: dict[str, JsonValue] = {"context_ref": snapshot_id, "pointer": pointer}
    if estimated_tokens is not None:
        marker["estimated_tokens"] = estimated_tokens
    return marker


def context_read_contract(*, project_id: str, run_id: str) -> dict[str, JsonValue]:
    return {
        "tool_name": "agent.read_context",
        "project_id": project_id,
        "run_id": run_id,
        "instruction": (
            "For each context_ref/pointer, call this Tool with these project_id/run_id, "
            "snapshot_id=context_ref, pointer unchanged, offset=0 and limit=6000. "
            "Follow next_offset for further pages. Each locator addresses exact saved content, "
            "not necessarily current content; refresh changed content before editing. "
            "visible_at is a JSON pointer to the same body in this model input; use the snapshot "
            "if that body has been externalized."
        ),
    }
