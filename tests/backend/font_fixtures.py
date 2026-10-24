"""Authored synthetic geometric controls; no handwriting accuracy claim."""

import json
from datetime import UTC, datetime
from pathlib import Path

from penforge_contracts import FontBuildManifest

ROOT = Path(__file__).resolve().parents[2]


def fixture_manifest(**changes):
    return FontBuildManifest.model_validate(
        dict(
            snapshot_hash="sha256:" + "a" * 64,
            snapshot_created_at=datetime(2026, 10, 20, tzinfo=UTC),
            family_name="Penforge Fixture",
            tool_versions=json.loads(
                (ROOT / "packages/contracts/policies/font-toolchain.v1.json").read_text()
            ),
        )
        | changes
    )


def fixture_glyphs(full=True, nonbmp=False):
    source = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
    points = list(range(32, 127)) if full else list(range(65, 91)) + list(range(97, 123))
    if nonbmp:
        points += [119990]
    rows = []
    for cp in points:
        outline = json.loads(json.dumps(source))
        outline["codepoints"] = [cp]
        if cp == 32:
            outline["contours"] = []
            outline["metrics"] = {"advance_width": 250.0, "left_side_bearing": 0.0}
        rows.append(
            dict(outline=outline, approval="approved", origin="user_capture", variant_index=0)
        )
    return rows
