"""Derive a versioned Tier 1 from pinned official properties. No corpus guesses."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UCD = ROOT / "packages/contracts/unicode/17.0.0"
CATEGORIES = {
    "Consonant",
    "Vowel_Independent",
    "Vowel_Dependent",
    "Nukta",
    "Virama",
    "Bindu",
    "Visarga",
    "Avagraha",
    "Number",
}


def main():
    props = {}
    for line in (UCD / "IndicSyllabicCategory.txt").read_text().splitlines():
        body = line.split("#")[0].strip()
        if not body:
            continue
        value, cat = map(str.strip, body.split(";"))
        ends = value.split("..")
        for cp in range(int(ends[0], 16), int(ends[-1], 16) + 1):
            props[cp] = cat
    names = {}
    decomposition = {}
    for line in (UCD / "UnicodeData.txt").read_text().splitlines():
        f = line.split(";")
        cp = int(f[0], 16)
        names[cp] = f[1]
        if f[5] and not f[5].startswith("<"):
            decomposition[cp] = [int(x, 16) for x in f[5].split()]
    entries = []
    excluded = []
    for cp in range(0x900, 0x980):
        cat = props.get(cp, "Other")
        if cat in CATEGORIES or cp in (0x964, 0x965):
            entries.append(
                dict(
                    codepoint=cp,
                    name=names[cp],
                    kind="punctuation" if cp in (0x964, 0x965) else cat,
                    reason="Unicode 17 " + cat
                    if cp not in (0x964, 0x965)
                    else "Specification danda punctuation",
                    canonical_decomposition=decomposition.get(cp),
                )
            )
        else:
            excluded.append(
                dict(
                    codepoint=cp,
                    name=names.get(cp, "unassigned"),
                    category=cat,
                    reason="Outside bounded Tier 1 property policy; extended/Vedic/symbol support needs separate review",
                )
            )
    punctuation = (
        [32]
        + list(range(33, 48))
        + list(range(58, 65))
        + list(range(91, 97))
        + list(range(123, 127))
    )
    value = dict(
        schema_version=1,
        required_set_id="deva-tier1-ucd17-v1",
        unicode_version="17.0.0",
        entries=entries,
        interoperability_codepoints=punctuation,
        controls=dict(codepoints=[0x200C, 0x200D], policy="only_after_virama_not_drawn"),
        excluded=excluded,
        tier2=dict(status="unavailable", reason="licensed_corpus_frequency_and_review_required"),
        sources={
            n: hashlib.sha256((UCD / n).read_bytes()).hexdigest()
            for n in ("UnicodeData.txt", "IndicSyllabicCategory.txt", "Scripts.txt")
        },
    )
    path = ROOT / "packages/contracts/policies/deva-tier1-ucd17-v1.json"
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
    print(
        "Devanagari scalars:",
        len(entries),
        "interoperability:",
        len(punctuation),
        "excluded:",
        len(excluded),
    )


if __name__ == "__main__":
    main()
