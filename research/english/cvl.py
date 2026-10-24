"""Verify the owner-downloaded CVL archive only; no network or model execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.english.acquire import inspect
from research.preparation.archive_audit import external

# Official Zenodo REST record and lab DOI link checked on 2026-10-21.
RECORD = "https://zenodo.org/records/1492267"
SOURCE = ("cvl-database-1-1.zip", 4216852947, "9cf3cdcd177f1ff26ac5c085382e5b4f")


def check_source(folder: Path) -> dict:
    folder = external(folder)
    name, size, md5 = SOURCE
    path = folder / name
    if not path.exists() and not path.is_symlink():
        return {
            "status": "missing_manual_downloads",
            "missing": [name],
            "dataset_admitted": False,
            "model_execution": False,
        }
    return {
        "status": "source_bytes_verified_only",
        "source": {"record": RECORD, "filename": name, **inspect(path, size, md5)},
        "archive_content_audit": "not_run",
        "permission_review": "pending_lab_cc_by_nc_3_0_zenodo_cc_by_nc_4_0_discrepancy",
        "dataset_admitted": False,
        "model_execution": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    result = check_source(args.sources)
    print(json.dumps(result, indent=2))
    if result["status"] == "missing_manual_downloads":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
