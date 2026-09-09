"""Editable runtime responsibilities, separate from creative production methods."""

from pathlib import Path
from typing import Literal


def runtime_prompt(role: Literal["common", "leader", "worker", "leader_protocol", "worker_protocol"]) -> str:
    return (Path(__file__).parent / "runtime" / f"{role}.md").read_text(encoding="utf-8").strip()
