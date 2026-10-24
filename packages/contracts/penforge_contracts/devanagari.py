"""Lightweight inventory/coverage evidence. No recognizer or font dependency."""

import json
from functools import lru_cache
from pathlib import Path

from .serialization import content_hash
from .unicode import graphemes, scalars

REQUIRED_SET_ID = "deva-tier1-ucd17-v1"


@lru_cache(maxsize=1)
def inventory():
    return json.loads(
        (Path(__file__).parents[1] / "policies/deva-tier1-ucd17-v1.json").read_bytes()
    )


def tier1():
    value = inventory()
    return {r["codepoint"] for r in value["entries"]} | set(value["interoperability_codepoints"])


def coverage_evidence(rows, *, shaping=None, requested_text=""):
    """Only actual per-font tested results count. Scalar completeness never means ready."""
    mapped = set()
    marks = set()
    forms = []
    for row in rows:
        if row.get("approval") != "approved":
            continue
        o = row.get("outline", row)
        cps = o["codepoints"]
        if (
            o.get("mapping", "cmap") == "cmap"
            and len(cps) == 1
            and row.get("variant_index", 0) == 0
        ):
            mapped.add(cps[0])
        else:
            forms.append(cps)
    required_marks = {
        r["codepoint"]
        for r in inventory()["entries"]
        if r["kind"] in {"Vowel_Dependent", "Nukta", "Virama", "Bindu", "Visarga"}
    }
    marks = mapped & required_marks
    tested = (shaping or {}).get("cases", [])
    passed = [
        r["codepoints"]
        for r in tested
        if r.get("status") == "passed" and not r.get("fallback_only")
    ]
    fallback = [
        r["codepoints"] for r in tested if r.get("status") == "passed" and r.get("fallback_only")
    ]
    requested = graphemes(requested_text)
    missing = [seq for seq in requested if seq not in passed and seq not in fallback]
    return dict(
        engineering_fixture=any(
            (r.get("outline", r).get("indic") or {}).get("source_classification")
            == "engineering_fixture"
            for r in rows
            if r.get("approval") == "approved"
        ),
        script_code="Deva",
        required_set_id=REQUIRED_SET_ID,
        inventory_hash=content_hash(inventory()),
        mapped=len(mapped),
        mapped_codepoints=sorted(mapped),
        missing=[[cp] for cp in sorted(tier1() - mapped)],
        required_mark_coverage=sorted(marks),
        missing_marks=sorted(required_marks - mapped),
        contextual_forms=forms,
        feature_support=(shaping or {}).get("features", []),
        tested_clusters=passed,
        fallback_only_clusters=fallback,
        missing_clusters=missing,
        shaping_validation_state="engineering_tested"
        if shaping and shaping.get("status") == "passed" and (passed or fallback)
        else "unverified",
        tier2="unavailable",
        complete_devanagari=False,
        complete_english=False,
        warnings=["devanagari_experimental", "corpus_and_handwriting_evaluation_pending"]
        + (["shaping_unverified"] if not (passed or fallback) else [])
        + (["missing_clusters"] if missing else []),
        requested_codepoints=scalars(requested_text),
    )
