"""Local hashed vectors. Recall stays on this machine; Jev only sees the shortlist."""

from __future__ import annotations

import hashlib
import math
import re

DIM = 384
_TOKEN = re.compile(r"[a-z0-9$]{2,}")


def embed(text: str) -> list[float]:
    tokens = _TOKEN.findall(text.lower())
    vector = [0.0] * DIM
    if not tokens:
        return vector
    for index, token in enumerate(tokens):
        _add(vector, token)
        if index:
            _add(vector, f"{tokens[index - 1]}_{token}")
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))


def _add(vector: list[float], feature: str) -> None:
    digest = hashlib.blake2s(feature.encode(), digest_size=8).digest()
    bucket = int.from_bytes(digest[:4], "little") % DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    vector[bucket] += sign
