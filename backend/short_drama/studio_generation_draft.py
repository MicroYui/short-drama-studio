"""Display actual streamed creative text without committing partial JSON."""

from collections.abc import Callable
import json
from time import monotonic

from short_drama.studio_trace import record_trace


_FIELDS = {"heading": "场次", "screenplay_text": "剧本", "visual": "画面", "camera": "镜头",
    "framing": "构图", "audio_intent": "声音", "action": "动作", "dialogue": "对白"}


def _read_string(source: str, start: int) -> tuple[str, int, bool]:
    end = start + 1
    while end < len(source):
        if source[end] == "\\":
            end += 2
        elif source[end] == '"':
            return str(json.loads(source[start:end + 1])), end + 1, True
        else:
            end += 1
    value = source[start + 1:]
    # A supplier can split any JSON escape. Show only received, decodable text.
    for trim in range(min(7, len(value)) + 1):
        candidate = value[:len(value) - trim] if trim else value
        try:
            decoded = str(json.loads('"' + candidate + '"'))
            return decoded.encode("utf-16", "surrogatepass").decode("utf-16", "ignore"), len(source), False
        except (ValueError, UnicodeError):
            continue
    return "", len(source), False


def creative_text(source: str) -> str:
    """Read known creative fields; never display protocol, reasoning or raw JSON."""
    parts: list[str] = []
    index = 0
    while index < len(source):
        if source[index] != '"':
            index += 1
            continue
        try:
            key, index, complete = _read_string(source, index)
        except ValueError:
            break
        if not complete:
            break
        while index < len(source) and source[index].isspace():
            index += 1
        if key not in _FIELDS or index >= len(source) or source[index] != ":":
            continue
        index += 1
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source) or source[index] != '"':
            continue
        try:
            value, index, _complete = _read_string(source, index)
        except ValueError:
            break
        if value:
            parts.append(f"{_FIELDS[key]}\n{value}")
    return "\n\n".join(parts)


class StreamedCreativeDraft:
    def __init__(self, call_id: str, *, clock: Callable[[], float] = monotonic) -> None:
        self.call_id = call_id
        self.clock: Callable[[], float] = clock
        self.text: str = ""
        self.last_text: str = ""
        self.last_emit: float = 0.0

    def receive(self, delta: str) -> None:
        self.text += delta
        if self.clock() - self.last_emit >= .2:
            self._emit("generating")

    def finish(self, *, success: bool) -> None:
        if self.text:
            self._emit("received" if success else "interrupted", force=True)

    def _emit(self, status: str, *, force: bool = False) -> None:
        self.last_emit = self.clock()
        projected = creative_text(self.text)
        if not projected or (not force and projected == self.last_text):
            return
        record_trace("model.draft", {"call_id": self.call_id, "text": projected,
            "status": status, "source": "provider_delta", "saved": False})
        self.last_text = projected
