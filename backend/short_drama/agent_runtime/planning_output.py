"""Small response guidance for a plan, never a partial executable plan."""

from pydantic import JsonValue


PLANNING_OUTPUT_TOKENS = 12000


def planning_response_guidance() -> dict[str, JsonValue]:
    return {
        "max_output_tokens": PLANNING_OUTPUT_TOKENS,
        "includes_reasoning": True,
        "instruction": (
            "Return one complete, compact plan for currently actionable responsibilities. "
            "Use exact existing ArtifactRefs and selected object IDs instead of copying "
            "saved shot bodies, upstream deliverables or repeated shared constraints. "
            "Keep task-specific intent, scope, required evidence and acceptance criteria. "
            "Optional fields with no additional information may be omitted according to "
            "the schema; no need to write empty arrays, nulls or a custom output schema "
            "for each delegation. Keep independent ready work parallel where useful. "
            "Defer work requiring new results to the next plan under the same goal. "
            "This is not a step quota or permission to drop unfinished work or mark it complete."
        ),
    }


def output_limit_feedback(*, partial_output_characters: int) -> dict[str, JsonValue]:
    return {
        "kind": "PLAN_OUTPUT_LIMIT",
        "reason": "max_output_tokens",
        "max_output_tokens": PLANNING_OUTPUT_TOKENS,
        "partial_output_characters": partial_output_characters,
        "partial_plan_executed": False,
        "instruction": (
            "The previous response exhausted its shared reasoning/output allowance before "
            "a complete plan existed. Generate a fresh complete JSON object, not a suffix "
            "or continuation of the truncated text. Reduce this plan to a smaller useful "
            "set of ready responsibilities and concise reference-based delegations. "
            "Leave later work to subsequent planning under the unchanged goal. Preserve "
            "completed results and still-active carried work; do not recreate completed "
            "media or claim the goal is complete merely to shorten the response. All "
            "normal role, dependency, reference and output-contract checks still apply."
        ),
    }
