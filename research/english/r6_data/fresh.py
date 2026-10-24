"""Metadata-only upper-bound audit of untouched NIST writer coverage."""

from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from research.english import nist
from research.english.r4_study import nist_writer_id
from research.english.r5_data.common import checked, digest_file, read_jsonl
from research.english.r6_contract import LETTERS


def audit_fresh(staging: Path, sources: Path) -> dict:
    """CRC/length joins can overestimate coverage; never certify a test with them.

    A below-minimum upper bound proves unavailability without decoding/reviewing any
    final image. Exact cryptographic joins/duplicate curation are required if it passes.
    """
    preparation = json.loads(checked(staging, "preparation.json").read_text())
    exposure = json.loads(checked(staging, "exposure.json").read_text())
    exposed = set(exposure["writers"])
    for name in ("train.jsonl", "validation.jsonl", "final-test.jsonl", "school-test.jsonl"):
        exposed.update(r["writer"] for r in read_jsonl(checked(staging, name)))
    for name in ("by_write.zip", "by_class.zip"):
        receipt = preparation["sources"][name]
        if digest_file(checked(sources, name)) != receipt["sha256"]:
            raise ValueError("r6_fresh_source_archive_changed")
    class_keys = defaultdict(set)
    with zipfile.ZipFile(sources / "by_class.zip") as archive:
        for entry in archive.infolist():
            match = nist.CLASSES.fullmatch(entry.filename)
            if match and chr(int(match[1], 16)) in LETTERS:
                class_keys[entry.CRC, entry.file_size].add(chr(int(match[1], 16)))
    writers = {}
    all_writers = set()
    counts: dict[str, dict[str, set[str]]] = {
        domain: {label: set() for label in LETTERS} for domain in ("primary", "school")
    }
    with zipfile.ZipFile(sources / "by_write.zip") as archive:
        for entry in archive.infolist():
            parsed = nist.writer_parts(entry.filename)
            if not parsed:
                continue
            partition, raw_writer, _, field = parsed
            writer = nist_writer_id(raw_writer)
            all_writers.add(writer)
            if writer in exposed:
                continue
            writers[writer] = partition
            if field not in {"u", "l"}:
                continue
            for label in class_keys[entry.CRC, entry.file_size]:
                if (field == "u") == label.isupper():
                    counts["school" if partition == 4 else "primary"][label].add(writer)
    coverage = {
        domain: {label: len(group) for label, group in values.items()}
        for domain, values in counts.items()
    }
    proven = {domain: any(n < 16 for n in values.values()) for domain, values in coverage.items()}
    return {
        "version": "r6-fresh-writer-metadata-v1",
        "model_execution": False,
        "source_writers": len(all_writers),
        "exposed_writers": len(exposed & all_writers),
        "remaining_writers": len(writers),
        "by_partition": dict(Counter(map(str, writers.values()))),
        "coverage_upper_bound": coverage,
        "minimum_distinct_writers_per_class": 16,
        "proven_below_minimum": proven,
        "final_status": "unavailable_below_minimum"
        if all(proven.values())
        else "unavailable_requires_exact_join_and_duplicate_curation",
        "method": "Pinned archive metadata CRC+length possible-label union; collisions only inflate writer-per-letter upper bounds.",
        "final_images_viewed": False,
        "historical_final_predictions_used": False,
        "no_test_inventory_selected": True,
    }
