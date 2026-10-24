import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from font_fixtures import fixture_glyphs, fixture_manifest
from fontTools.ttLib import TTFont
from penforge_font_engine.compiler import compile_font
from penforge_font_engine.validation import check_shaping, validate_font


def fixture(n):
    rows = fixture_glyphs()
    for row in rows:
        row["source_hash"] = (
            "sha256:" + hashlib.sha256(str(row["outline"]["codepoints"]).encode()).hexdigest()
        )
    for cp in [101, 108, 49, 33]:
        base = next(r for r in rows if r["outline"]["codepoints"] == [cp])
        for i in range(1, n):
            alt = json.loads(json.dumps(base))
            alt["variant_index"] = i
            alt["source_hash"] = "sha256:" + hashlib.sha256(f"source-{cp}-{i}".encode()).hexdigest()
            alt["revision_hash"] = (
                "sha256:" + hashlib.sha256(f"revision-{cp}-{i}".encode()).hexdigest()
            )
            rows.append(alt)
    return rows


@pytest.mark.parametrize("n", [2, 3])
@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
def test_calt_n2_then_n3_invariants_repeatable_across_processes(tmp_path, n, format):
    manifest = fixture_manifest(options={"realism": 50 if n == 2 else 100})
    rows = fixture(n)
    data = compile_font(manifest, rows, format)
    report = check_shaping(data, rows, manifest)
    assert data == compile_font(manifest, list(reversed(rows)), format)
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps({"manifest": manifest.model_dump(mode="json"), "rows": rows, "format": format})
    )
    code = """
import json, sys, hashlib
from penforge_contracts import FontBuildManifest
from penforge_font_engine.compiler import compile_font
from penforge_font_engine.validation import check_shaping
v = json.load(open(sys.argv[1]))
manifest = FontBuildManifest.model_validate(v['manifest'])
data = compile_font(manifest, v['rows'], v['format'])
print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(),
                  'shaping': check_shaping(data, v['rows'], manifest)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source)],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH="apps/api:packages/contracts:packages/font-engine:."),
    )
    assert result.returncode == 0, result.stderr
    independent = json.loads(result.stdout)
    assert independent["sha256"] == hashlib.sha256(data).hexdigest()
    assert independent["shaping"] == report
    folder = Path("artifacts/phase08/fonts") / f"n{n}-{format}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"fixture.{format}").write_bytes(data)
    (folder / "shaping.json").write_text(json.dumps(report, indent=2))
    (folder / "independent-process.json").write_text(json.dumps(independent, indent=2))
    (folder / "hash.json").write_text(
        json.dumps(
            {"sha256": hashlib.sha256(data).hexdigest(), "domain": "synthetic_engineering_control"}
        )
    )
    validation = validate_font(data, manifest, rows, format, tmp_path / "validation")
    assert validation["status"] == "passed_with_warnings", validation
    (folder / "validation.json").write_text(json.dumps(validation, indent=2))


def test_insufficient_alternates_keep_valid_base_and_features_off(tmp_path):
    rows = fixture_glyphs()
    data = compile_font(fixture_manifest(options={"realism": 100}), rows, "ttf")
    assert "GSUB" not in TTFont(io.BytesIO(data))
    assert (
        check_shaping(data, rows, fixture_manifest(options={"realism": 100}))["status"] == "passed"
    )
