"""Derive pinned reference tables; never fetches network data."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "packages/contracts/unicode/17.0.0"


def ranges(name):
    for line in (ROOT / name).read_text().splitlines():
        body = line.split("#")[0].strip()
        if not body:
            continue
        fields = [x.strip() for x in body.split(";")]
        ends = fields[0].split("..")
        yield int(ends[0], 16), int(ends[-1], 16), fields[1:]


def main():
    gcb = [[a, b, p[0]] for a, b, p in ranges("GraphemeBreakProperty.txt")]
    incb = [[a, b, p[1]] for a, b, p in ranges("DerivedCoreProperties.txt") if p[0] == "InCB"]
    emoji = [[a, b] for a, b, p in ranges("emoji-data.txt") if p[0] == "Extended_Pictographic"]
    excluded = {
        cp
        for a, b, p in ranges("DerivedNormalizationProps.txt")
        if p[0] == "Full_Composition_Exclusion"
        for cp in range(a, b + 1)
    }
    ccc, decompositions, compositions = {}, {}, {}
    for line in (ROOT / "UnicodeData.txt").read_text().splitlines():
        fields = line.split(";")
        cp = int(fields[0], 16)
        if int(fields[3]):
            ccc[str(cp)] = int(fields[3])
        if fields[5] and not fields[5].startswith("<"):
            seq = [int(x, 16) for x in fields[5].split()]
            decompositions[str(cp)] = seq
            if len(seq) == 2 and cp not in excluded:
                compositions[",".join(map(str, seq))] = cp
    data = dict(
        version="17.0.0",
        gcb=sorted(gcb),
        incb=sorted(incb),
        emoji=sorted(emoji),
        ccc=ccc,
        decompositions=decompositions,
        compositions=compositions,
    )
    (ROOT / "tables.json").write_text(json.dumps(data, separators=(",", ":")) + "\n")
    manifest = json.loads((ROOT / "sources.json").read_text())
    manifest["sources"] = [
        dict(
            url="https://www.unicode.org/Public/17.0.0/ucd/"
            + (
                "auxiliary/"
                if p.name.startswith("Grapheme")
                else "emoji/"
                if p.name == "emoji-data.txt"
                else ""
            )
            + p.name,
            path=p.name,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(ROOT.glob("*.txt"))
        if p.name != "LICENSE.txt"
    ] + [
        dict(
            url="https://www.unicode.org/license.txt",
            path="LICENSE.txt",
            sha256=hashlib.sha256((ROOT / "LICENSE.txt").read_bytes()).hexdigest(),
        )
    ]
    manifest["generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["tables_sha256"] = hashlib.sha256((ROOT / "tables.json").read_bytes()).hexdigest()
    (ROOT / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
