"""Offline text-token accounting and the product's 258k context policy.

o200k counts are a preflight estimate for the configured compatible endpoint;
provider usage remains authoritative. The envelope reserve covers framing that
is not represented by plain text. No encoding or package is downloaded at run time.
"""

from dataclasses import dataclass
from functools import lru_cache
import gzip
from pathlib import Path
from typing import Protocol, cast

from tokenizers import Tokenizer


# Conservative request headroom, not a model-specific image-token formula.
# Actual multimodal usage is supplied by the provider. Images are not tokenized
# as base64 text. This leaves ordinary 100k text requests unchanged.
VISUAL_TOKEN_ALLOWANCE_PER_IMAGE = 8192


def reserved_visual_tokens(image_count: int) -> int:
    return max(0, image_count) * VISUAL_TOKEN_ALLOWANCE_PER_IMAGE


class _EncodingLike(Protocol):
    @property
    def ids(self) -> list[int]: ...


class _TokenizerLike(Protocol):
    def encode(self, sequence: str, *, add_special_tokens: bool) -> _EncodingLike: ...


class _TokenizerFactoryLike(Protocol):
    def from_str(self, value: str) -> _TokenizerLike: ...


@dataclass(frozen=True)
class ContextBudget:
    window_tokens: int = 258_000
    compact_at_tokens: int = 200_000
    target_tokens: int = 160_000
    envelope_tokens: int = 1_024

    def __post_init__(self) -> None:
        if not 0 < self.target_tokens < self.compact_at_tokens < self.window_tokens:
            raise ValueError("Context thresholds must satisfy target < compact_at < window")
        if self.envelope_tokens < 0:
            raise ValueError("envelope_tokens cannot be negative")

    def input_limit(self, max_output_tokens: int, *, visual_tokens: int = 0) -> int:
        return max(0, self.window_tokens - max_output_tokens - self.envelope_tokens - max(0, visual_tokens))


@lru_cache(maxsize=1)
def _tokenizer() -> _TokenizerLike:
    path = Path(__file__).with_name("tokenizer") / "o200k_base.json.gz"
    return cast(_TokenizerFactoryLike, Tokenizer).from_str(gzip.decompress(path.read_bytes()).decode("utf-8"))


def count_text_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)
