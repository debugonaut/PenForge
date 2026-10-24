"""Deterministic cross-language hashing over a deliberately small JSON value domain.

Numbers use normalized big-endian IEEE754 binary64 bytes, strings use UTF-8 hex,
objects sort UTF-8 key bytes. Type tags remove representation ambiguity. NaN/Inf,
unsafe integers, surrogates and non-JSON values are rejected. -0 equals +0.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any


def _string_hex(value: str) -> str:
    return value.encode("utf-8", errors="strict").hex()


def _encode(value: Any) -> list:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) in (int, float):
        if type(value) is int and abs(value) > 2**53 - 1:
            raise ValueError("unsafe_integer")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite_number")
        if number.is_integer() and abs(number) > 2**53 - 1:
            raise ValueError("unsafe_integer")
        return ["number", struct.pack(">d", 0.0 if number == 0 else number).hex()]
    if type(value) is str:
        return ["string", _string_hex(value)]
    if type(value) is list:
        return ["array", [_encode(item) for item in value]]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("non_string_key")
        pairs = sorted((_string_hex(key), _encode(item)) for key, item in value.items())
        return ["object", [[key, item] for key, item in pairs]]
    raise ValueError("unsupported_json_value")


def canonical_bytes(value: Any, *, exclude_root_keys: set[str] | None = None) -> bytes:
    encoded = _encode(value)
    if exclude_root_keys:
        if type(value) is not dict:
            raise ValueError("root_exclusion_requires_object")
        excluded = {_string_hex(key) for key in exclude_root_keys}
        encoded[1] = [entry for entry in encoded[1] if entry[0] not in excluded]
    return json.dumps(encoded, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def content_hash(value: Any, *, exclude_root_keys: set[str] | None = None) -> str:
    return (
        "sha256:"
        + hashlib.sha256(canonical_bytes(value, exclude_root_keys=exclude_root_keys)).hexdigest()
    )
