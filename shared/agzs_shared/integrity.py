"""Цепочка SHA-256 для журналов. Канонический JSON не принимает дроби и произвольные объекты."""

import hashlib
import json
from collections.abc import Mapping, Sequence

GENESIS_HASH = "0" * 64


def canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def chain_hash(prev_hash: str, payload: Mapping[str, object]) -> str:
    if len(prev_hash) != 64 or any(char not in "0123456789abcdef" for char in prev_hash):
        raise ValueError("prev_hash должен быть SHA-256 в нижнем hex")
    material = (prev_hash + canonical_json(payload)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _normalize(value: object) -> object:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise TypeError("В хешируемый журнал не входят дробные числа")
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    raise TypeError(f"Несериализуемое значение журнала: {type(value).__name__}")
