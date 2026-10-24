"""Private CVL ZIP/CRC inventory for actual-file review. No extraction of images or ML."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from research.english.cvl import SOURCE, check_source
from research.preparation.archive_audit import external, safe_name


def inventory(sources: Path, destination: Path):
    sources, destination = external(sources), external(destination)
    source = check_source(sources)
    if source["status"] != "source_bytes_verified_only":
        raise ValueError("cvl_complete_manual_download_required")
    if (
        destination.exists()
        or destination.is_relative_to(sources)
        or sources.is_relative_to(destination)
    ):
        raise ValueError("cvl_choose_new_inventory_outside_sources")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".cvl-inventory-", dir=destination.parent))
    try:
        counts: Counter[str] = Counter()
        total, names, example = 0, set(), None
        with (
            zipfile.ZipFile(sources / SOURCE[0]) as archive,
            (temporary / "entries-private.jsonl").open("x") as output,
        ):
            if len(archive.infolist()) > 250_000:
                raise ValueError("cvl_archive_entry_ceiling")
            for entry in archive.infolist():
                name = safe_name(entry.filename)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                allowed = {0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG}
                if (
                    name != entry.filename.rstrip("/")
                    or name in names
                    or mode not in allowed
                    or entry.flag_bits & 1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    raise ValueError("cvl_unsafe_duplicate_or_unsupported_archive_member")
                names.add(name)
                if entry.is_dir():
                    continue
                if entry.file_size > 64_000_000:
                    raise ValueError("cvl_archive_member_ceiling")
                total += entry.file_size
                if total > 32_000_000_000:
                    raise ValueError("cvl_archive_expansion_ceiling")
                checksum, received = hashlib.sha256(), 0
                suffix = Path(name).suffix.lower()
                sample = (
                    bytearray()
                    if example is None and suffix == ".xml" and entry.file_size <= 1_000_000
                    else None
                )
                with archive.open(entry) as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        received += len(chunk)
                        if received > entry.file_size:
                            raise ValueError("cvl_archive_expanded_size_changed")
                        checksum.update(chunk)
                        if sample is not None:
                            sample.extend(chunk)
                if received != entry.file_size:
                    raise ValueError("cvl_archive_truncated_member")
                counts[suffix] += 1
                output.write(
                    json.dumps({"member": name, "bytes": received, "sha256": checksum.hexdigest()})
                    + "\n"
                )
                if sample is not None:
                    # Copy as inert bytes for curator inspection; do not parse/run it.
                    (temporary / "annotation-example-private.xml").write_bytes(sample)
                    example = name
        report = {
            "status": "inventory_only_pending_annotation_and_permission_review",
            "source": source["source"],
            "files": sum(counts.values()),
            "expanded_bytes": total,
            "extensions": dict(sorted(counts.items())),
            "non_directory_members_crc_checked": True,
            "annotation_example_available": example is not None,
            "images_decoded": False,
            "dataset_admitted": False,
            "model_inference": False,
            "training": "not_run",
        }
        (temporary / "inventory.json").write_text(json.dumps(report, indent=2) + "\n")
        if destination.exists():
            raise ValueError("cvl_inventory_created_concurrently")
        temporary.rename(destination)
        return report
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(inventory(args.sources, args.destination), indent=2))


if __name__ == "__main__":
    main()
