"""Unicode 17 source evidence and UAX29 rev47 extended graphemes, scalar offsets."""

import json
from functools import lru_cache
from pathlib import Path

from .serialization import content_hash

UNICODE_VERSION = "17.0.0"


@lru_cache(maxsize=1)
def tables():
    return json.loads((Path(__file__).parents[1] / "unicode/17.0.0/tables.json").read_bytes())


def scalars(text: str) -> list[int]:
    values = [ord(c) for c in text]
    if any(0xD800 <= cp <= 0xDFFF for cp in values):
        raise ValueError("invalid_unicode_scalar")
    return values


def property_at(cp, rows, default="Other"):
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid][1] < cp:
            lo = mid + 1
        else:
            hi = mid
    return (
        (rows[lo][2] if len(rows[lo]) > 2 else True)
        if lo < len(rows) and rows[lo][0] <= cp
        else default
    )


def graphemes(text: str) -> list[list[int]]:
    cps = scalars(text)
    if not cps:
        return []
    t = tables()
    props = [property_at(cp, t["gcb"]) for cp in cps]
    incb = [property_at(cp, t["incb"]) for cp in cps]
    starts = [0]
    for i in range(1, len(cps)):
        a, b = props[i - 1 : i + 1]
        join = False
        if a == "CR" and b == "LF":
            join = True
        elif a in ("Control", "CR", "LF") or b in ("Control", "CR", "LF"):
            join = False
        elif (
            a == "L"
            and b in ("L", "V", "LV", "LVT")
            or a in ("LV", "V")
            and b in ("V", "T")
            or a in ("LVT", "T")
            and b == "T"
        ):
            join = True
        elif b in ("Extend", "ZWJ", "SpacingMark") or a == "Prepend":
            join = True
        elif incb[i] == "Consonant":
            j = i - 1
            linker = False
            while j >= 0 and incb[j] in ("Extend", "Linker"):
                linker |= incb[j] == "Linker"
                j -= 1
            join = linker and j >= 0 and incb[j] == "Consonant"
        if not join and a == "ZWJ" and property_at(cps[i], t["emoji"], False):
            j = i - 2
            while j >= 0 and props[j] == "Extend":
                j -= 1
            join = j >= 0 and bool(property_at(cps[j], t["emoji"], False))
        if not join and a == b == "Regional_Indicator":
            j = i - 1
            while j >= 0 and props[j] == "Regional_Indicator":
                j -= 1
            join = (i - 1 - j) % 2 == 1
        if not join:
            starts.append(i)
    return [cps[a:b] for a, b in zip(starts, starts[1:] + [len(cps)])]


def normalize(cps: list[int], mode: str) -> list[int]:
    if mode not in ("NFC", "NFD"):
        raise ValueError("unsupported_normalization")
    scalars("".join(chr(cp) for cp in cps))
    t = tables()
    result = []

    def decompose(cp):
        if 0xAC00 <= cp <= 0xD7A3:
            s = cp - 0xAC00
            return [0x1100 + s // 588, 0x1161 + s % 588 // 28] + (
                [0x11A7 + s % 28] if s % 28 else []
            )
        seq = t["decompositions"].get(str(cp))
        return [x for p in seq for x in decompose(p)] if seq else [cp]

    def ccc(cp):
        return t["ccc"].get(str(cp), 0)

    for cp in cps:
        for c in decompose(cp):
            result.append(c)
            j = len(result) - 1
            while j > 0 and ccc(c) and ccc(result[j - 1]) > ccc(c):
                result[j], result[j - 1] = result[j - 1], result[j]
                j -= 1
    if mode == "NFD" or not result:
        return result
    composed = [result[0]]
    starter = 0
    last = ccc(result[0])
    for cp in result[1:]:
        a = composed[starter]
        cc = ccc(cp)
        combined = t["compositions"].get(f"{a},{cp}")
        if 0x1100 <= a <= 0x1112 and 0x1161 <= cp <= 0x1175:
            combined = 0xAC00 + (a - 0x1100) * 588 + (cp - 0x1161) * 28
        elif 0xAC00 <= a <= 0xD7A3 and (a - 0xAC00) % 28 == 0 and 0x11A8 <= cp <= 0x11C2:
            combined = a + cp - 0x11A7
        if combined is not None and (last == 0 or last < cc):
            composed[starter] = combined
        else:
            if cc == 0:
                starter = len(composed)
            composed.append(cp)
            last = cc
    return composed


def sequence_evidence(text: str) -> dict:
    cps = scalars(text)
    return dict(
        schema_version=1,
        unicode_version=UNICODE_VERSION,
        source_codepoints=cps,
        source_hash=content_hash(cps),
        graphemes=graphemes(text),
        derived=[
            dict(
                mode=mode,
                codepoints=normalize(cps, mode),
                sha256=content_hash(normalize(cps, mode)),
            )
            for mode in ("NFC", "NFD")
        ],
    )


def capture_sequence(cps: list[int]) -> None:
    text = "".join(chr(cp) for cp in cps)
    scalars(text)
    if len(cps) == 1 and 33 <= cps[0] <= 126:
        return
    if not 1 <= len(cps) <= 16 or len(graphemes(text)) != 1:
        raise ValueError("one_supported_character_or_cluster_required")
    for i, cp in enumerate(cps):
        if 0x900 <= cp <= 0x97F and cp not in (0x950, 0x951, 0x952, 0x953, 0x954, 0x970, 0x971):
            continue
        if cp in (0x200C, 0x200D) and i and cps[i - 1] == 0x94D:
            continue
        raise ValueError("unsupported_capture_sequence_or_control")
