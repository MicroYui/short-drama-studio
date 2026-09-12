"""Replace only loaded method paragraphs in four saved real requests.

Run from backend with the existing environment:
  python -m tests.replay_efficient_production_methods --trace-dir /path/to/studio-trace/job
No DB, Provider, image decoding, or product operation is used. This measures
instruction loading and input size only; model responses are not replayed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.token_budget import count_text_tokens
from short_drama.production_profiles import ProductionSkillRole
from short_drama.prompts import runtime_prompt
from short_drama.skills.loader import load_shared_role_prompts

ROOT = Path(__file__).resolve().parents[2]
BASE = "3c67c0b"
FILES = (
    ("leader", "20260912T021448292860-243a371b-model.input.json"),
    ("DIRECTOR", "20260912T015606742151-798d99fe-model.input.json"),
    ("ASSET_BUILDER", "20260912T030610722975-ff5e892d-model.input.json"),
    ("ASSET_VERIFIER", "20260912T030257450690-02fbe8a9-model.input.json"),
)
CHANGED = (
    "backend/short_drama/prompts/runtime/leader.md",
    "backend/short_drama/skills/shared/roles/director.md",
    "backend/short_drama/skills/shared/roles/asset_builder.md",
    "backend/short_drama/skills/shared/roles/asset_verifier.md",
    "backend/short_drama/skills/methods/drama_series/references/production-method.md",
    "backend/short_drama/skills/methods/drama_series/references/video-compilation.md",
)


def replay(trace_dir: Path) -> list[dict[str, JsonValue]]:
    replacements: list[tuple[str, str, str]] = []
    for path in CHANGED:
        old = subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=ROOT,
            check=True, capture_output=True, text=True).stdout.strip().split("\n\n")
        new = (ROOT / path).read_text().strip().split("\n\n")
        assert len(old) == len(new), path
        replacements.extend((path, before, after) for before, after in zip(old, new, strict=True) if before != after)
    events: dict[str, dict[str, JsonValue]] = {}
    for path in trace_dir.glob("*provider.language_generation.json"):
        event = cast(dict[str, JsonValue], json.loads(path.read_text())["data"])
        events[str(event["call_id"])] = event
    results: list[dict[str, JsonValue]] = []
    for role, name in FILES:
        path = trace_dir / name
        original_bytes = path.read_bytes()
        data = cast(dict[str, JsonValue], json.loads(original_bytes)["data"])
        before = cast(str, data["instructions"])
        after = before
        applied: dict[str, JsonValue] = {}
        for source, old, new in replacements:
            if old not in after:
                continue  # Other roles/stages are deliberately not injected.
            assert after.count(old) == 1, (role, source)
            after = after.replace(old, new)
            applied[source] = int(cast(int, applied.get(source, 0))) + 1
        expected = runtime_prompt("leader") if role == "leader" else load_shared_role_prompts()[ProductionSkillRole(role)]
        assert expected in after, f"Actual {role} body is not fully loaded"
        input_text = cast(str, data["input_text"])
        payload = cast(dict[str, JsonValue], json.loads(input_text))
        # Keep the exact work scope, visual report, schema and user facts. A new
        # instruction does not silently amend this old WorkOrder's authority.
        if role == "ASSET_BUILDER":
            assert "不修改故事、对白、时长、分组" in input_text
            assert "禁止创建、入队、提交或执行视频" in input_text
            assert "PROP_BASE_POSITION_DRIFT" in input_text
            assert "PARTIAL" in input_text and "LEADER_REPLAN" in input_text
        assert json.loads(input_text) == payload
        assert path.read_bytes() == original_bytes
        usage = events.get(str(data["call_id"]), {})
        old_tokens, new_tokens = count_text_tokens(before), count_text_tokens(after)
        payload_tokens = count_text_tokens(input_text)
        results.append({"role": role, "trace_file": name, "call_id": data["call_id"],
            "model": data.get("model"), "reasoning_effort": data.get("reasoning_effort"),
            "applied_paragraphs": applied, "instructions_before": old_tokens,
            "instructions_after": new_tokens, "instructions_delta": new_tokens - old_tokens,
            "unchanged_input_text_tokens": payload_tokens,
            "total_estimated_before": old_tokens + payload_tokens,
            "total_estimated_after": new_tokens + payload_tokens,
            "original_provider_usage": {key: usage.get(key) for key in
                ("input_tokens", "cached_input_tokens", "output_tokens", "elapsed_seconds")},
            "original_trace_and_payload_unchanged": True})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.trace_dir), ensure_ascii=False, indent=2))
