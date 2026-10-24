"""Offline status of the three manual downloads. Never downloads or runs ML."""

import argparse
import json
from pathlib import Path

from research.english.acquire import SOURCES, inspect
from research.english.cvl import SOURCE
from research.preparation.archive_audit import external


def check(folder: Path, *, letters_only: bool = False, verify: bool = False):
    folder = external(folder)
    required = {name: SOURCES[name] for name in ("by_write.zip", "by_class.zip")}
    if not letters_only:
        required[SOURCE[0]] = SOURCE[1:]
    files = {}
    for name, (size, md5) in required.items():
        path = folder / name
        if path.is_symlink():
            state = "symlink_rejected"
        elif not path.is_file():
            state = "missing_or_download_in_progress"
        elif path.stat().st_size != size:
            state = "incomplete_or_unexpected_size"
        else:
            state = "expected_size_only"
        files[name] = {"status": state, "expected_bytes": size}
    complete = all(item["status"] == "expected_size_only" for item in files.values())
    if complete and verify:
        for name, (size, md5) in required.items():
            files[name] = {"status": "bytes_verified_only", **inspect(folder / name, size, md5)}
    return {
        "status": "source_files_present" if complete else "downloads_not_ready",
        "checksum_verified": complete and verify,
        "files": files,
        "dataset_admitted": False,
        "model_execution": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--letters-only", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = check(args.sources, letters_only=args.letters_only, verify=args.verify)
    print(json.dumps(result, indent=2))
    if result["status"] == "downloads_not_ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
