"""Retain isolated engineering font outputs, feature source, traces and resources."""

import argparse
import hashlib
import json
import platform
import shutil
from pathlib import Path

from penforge_font_engine.indic import feature_source
from penforge_font_engine.isolated import compile_isolated

from scripts.deva_fixture import REFERENCE, fixture

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest, rows = fixture()
    (args.output / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    (args.output / "glyphs.json").write_text(json.dumps(rows, indent=2))
    names = {
        r["outline"]["codepoints"][0]: "space"
        if r["outline"]["codepoints"] == [32]
        else f"uni{r['outline']['codepoints'][0]:04X}"
        for r in rows
        if r["outline"]["mapping"] == "cmap"
    }
    (args.output / "features.fea").write_text(feature_source(rows, manifest, names))
    shutil.copy(REFERENCE / "OFL.txt", args.output / "REFERENCE-OFL.txt")
    shutil.copy(REFERENCE / "provenance.json", args.output / "reference-provenance.json")
    records = []
    for fmt in ("ttf", "otf", "woff2"):
        hashes = []
        for repeat in (1, 2):
            work = args.output / f"{fmt}-{repeat}"
            work.mkdir()
            data, report = compile_isolated(work, manifest.model_dump(mode="json"), rows, fmt)
            print(fmt, repeat, report["status"], flush=True)
            digest = hashlib.sha256(data).hexdigest()
            hashes.append(digest)
            (work / f"fixture.{fmt}").write_bytes(data)
            (work / "report.json").write_text(json.dumps(report, indent=2))
            records.append(
                dict(
                    format=fmt,
                    repeat=repeat,
                    sha256=digest,
                    resources=report.get("resources"),
                    isolation=report.get("isolation"),
                    status=report["status"],
                )
            )
            assert report["status"] in {"passed", "passed_with_warnings"}
        assert len(set(hashes)) == 1
    summary = dict(
        records=records,
        platform=platform.platform(),
        python=platform.python_version(),
        tool_versions=manifest.tool_versions,
        lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        handwriting_evaluation=False,
    )
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
