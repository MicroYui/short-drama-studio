"""Read explicit H3 time ranges without changing authored prompt text."""

from __future__ import annotations

import math
import re


_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
_CLOCK = r"\d+(?::\d{2}){1,2}(?:\.\d+)?"
_TIME = rf"(?:{_CLOCK}|{_NUMBER})"
_UNIT = r"(?:seconds?|secs?|s|秒)"
_RANGE = re.compile(
    rf"(?<![\w.:+\-–—])(?P<start>{_TIME})\s*(?P<start_unit>{_UNIT})?"
    rf"\s*[-–—]\s*(?P<end>{_TIME})\s*(?P<end_unit>{_UNIT})?(?!\w|\.\d|:\d)",
    re.IGNORECASE,
)


def time_range_marker(start_seconds: float, end_seconds: float) -> str:
    return f"[{start_seconds:.2f}-{end_seconds:.2f} seconds]"


def _seconds(value: str) -> float | None:
    parts = value.split(":")
    if len(parts) == 1:
        return float(value)
    # Clock minutes/seconds are sexagesimal; do not reinterpret malformed
    # 00:60.000 or recover a matching suffix inside an invalid timestamp.
    if any(float(part) >= 60 for part in parts[1:]):
        return None
    return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))


def _same_time(actual: float, expected: float) -> bool:
    # Keep the existing two-decimal deterministic marker usable for fractional
    # source boundaries, while also accepting the exact locked numeric value.
    return any(math.isclose(actual, value, rel_tol=0, abs_tol=1e-7)
               for value in (expected, float(f"{expected:.2f}")))


def _matches(match: re.Match[str], start_seconds: float, end_seconds: float) -> bool:
    left, right = match.group("start"), match.group("end")
    clocks = (":" in left, ":" in right)
    if clocks[0] != clocks[1]:
        return False  # No guessing units for a mixed clock/numeric range.
    if not clocks[0] and not match.group("end_unit"):
        return False  # Plain 1-2 may describe a count, not a time interval.
    start, end = _seconds(left), _seconds(right)
    return (start is not None and end is not None and start < end
            and _same_time(start, start_seconds) and _same_time(end, end_seconds))


def find_time_range(text: str, start_seconds: float, end_seconds: float) -> int:
    """Return the original position of an equivalent explicit range, or -1."""
    return next((match.start() for match in _RANGE.finditer(text)
                 if _matches(match, start_seconds, end_seconds)), -1)


def find_shot_time_range(text: str, shot_index: int, start_seconds: float, end_seconds: float) -> int:
    """A group window must still follow its own exact Shot N declaration."""
    for shot in re.finditer(rf"\[Shot {shot_index}\]\s*\[?\s*", text):
        match = _RANGE.match(text, shot.end())
        if match is not None and _matches(match, start_seconds, end_seconds):
            return shot.start()
    return -1
