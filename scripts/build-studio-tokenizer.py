"""Convert an already-downloaded o200k vocabulary using the installed tokenizers.

The vocabulary and splitting pattern originate in OpenAI tiktoken (MIT).
The byte alphabet/BPE conversion follows Hugging Face Transformers' public
TikTokenConverter (Apache-2.0). This script does not download dependencies/data.
"""

import argparse
import base64
import gzip
from pathlib import Path

from tokenizers import Regex, Tokenizer, decoders, models, pre_tokenizers


PATTERN = "|".join([
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
])


def convert(source: Path, destination: Path) -> None:
    ranks = {base64.b64decode(token): int(rank) for token, rank in
             (line.split() for line in source.read_bytes().splitlines())}
    visible = [*range(33, 127), *range(161, 173), *range(174, 256)]
    missing = [value for value in range(256) if value not in visible]
    alphabet = dict(zip(visible + missing, map(chr, visible + list(range(256, 256 + len(missing))))))

    def encode(value: bytes) -> str:
        return "".join(alphabet[byte] for byte in value)

    vocabulary = {encode(token): rank for token, rank in ranks.items()}
    merges = []
    for token, rank in ranks.items():
        splits = [(token[:index], token[index:]) for index in range(1, len(token))
                  if token[:index] in ranks and token[index:] in ranks]
        splits.sort(key=lambda pair: (ranks[pair[0]], ranks[pair[1]]))
        merges.extend((rank, encode(left), encode(right)) for left, right in splits)
    merges.sort(key=lambda item: item[0])
    tokenizer = Tokenizer(models.BPE(vocabulary, [(left, right) for _, left, right in merges], ignore_merges=True))
    tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(PATTERN), behavior="isolated"),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False),
    ])
    tokenizer.decoder = decoders.ByteLevel()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(gzip.compress(tokenizer.to_str().encode(), mtime=0))
    print(f"Saved {len(vocabulary):,} vocabulary entries to {destination} ({destination.stat().st_size:,} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert(args.source, args.destination)
