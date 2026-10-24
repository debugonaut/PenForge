"""Data-only NIST audit and deterministic R5 preparation. Never imports model code."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from research.english import nist
from research.english.acquire import SOURCES, inspect
from research.english.r4_study import nist_writer_id
from research.english.r5_contract import FIELDS, LETTERS
from research.english.r5_data.common import (
    canonical_hash,
    code_inventory,
    digest_file,
    external,
    image_identity,
    jsonl,
    load_config,
    write_json,
)
from research.english.r5_data.history import assign_writers, collect_history


def select_rows(rows: list[dict], history_rows: list[dict], config: dict) -> tuple[dict, dict]:
    """Quarantine duplicate groups across writer/label/role, then pick one crop/writer/letter."""
    keys = ("hash", "pixel_hash", "normalized_pixel_hash")
    identities: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for row in [*rows, *history_rows]:
        for key in keys:
            identities[key, row[key]].add((row["writer"], row["label"], row["split"]))
    bad = {key for key, group in identities.items() if len(group) > 1}
    pools = defaultdict(list)
    quarantined = 0
    for row in rows:
        if any((key, row[key]) in bad for key in keys):
            quarantined += 1
            continue
        pools[row["split"], row["label"]].append(row)
    selected, coverage, unavailable = {}, {}, {}
    for split, cap in config["per_class_caps"].items():
        group, counts = [], {}
        for label in LETTERS:
            pool = sorted(
                pools[split, label],
                key=lambda r: canonical_hash([config["seed"], split, label, r["id"]]),
            )
            chosen: list[dict] = []
            writers, hashes, pixels, normalized = set(), set(), set(), set()
            for row in pool:
                if (
                    row["writer"] in writers
                    or row["hash"] in hashes
                    or row["pixel_hash"] in pixels
                    or row["normalized_pixel_hash"] in normalized
                ):
                    continue
                writers.add(row["writer"])
                hashes.add(row["hash"])
                pixels.add(row["pixel_hash"])
                normalized.add(row["normalized_pixel_hash"])
                if len(chosen) < cap:
                    chosen.append(row)
            counts[label] = {"available_writers": len(writers), "selected": len(chosen)}
            group.extend(chosen)
        minimum = 1 if split == "train" else config["minimum_evaluation_per_class"]
        if any(value["selected"] < minimum for value in counts.values()):
            if split in {"train", "validation"}:
                raise ValueError(f"r5_insufficient_case_sensitive_coverage:{split}")
            unavailable[split] = (
                "fewer_than_minimum_untouched_distinct_writers_for_one_or_more_letters"
            )
            group = []
        selected[split] = sorted(group, key=lambda row: row["id"])
        coverage[split] = counts
    return selected, {
        "candidate_images": len(rows),
        "duplicate_rows_quarantined": quarantined,
        "ambiguous_identity_groups": len(bad),
        "coverage": coverage,
        "unavailable_final_groups": unavailable,
    }


def prepare(
    sources: Path, r3: Path, r4: Path, destination: Path, *, config_path: Path | None = None
) -> dict:
    sources, r3, r4, destination = map(external, (sources, r3, r4, destination))
    if destination.exists() or any(
        destination.is_relative_to(p) or p.is_relative_to(destination) for p in (sources, r3, r4)
    ):
        raise ValueError("r5_destination_must_be_new_and_separate")
    config = load_config(config_path)
    history, exposure = collect_history(r3, r4)
    receipts = {
        name: inspect(sources / name, *SOURCES[name]) for name in ("by_class.zip", "by_write.zip")
    }
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".r5-prepare-", dir=destination.parent))
    db = sqlite3.connect(temporary / "class-index.sqlite")
    try:
        db.execute("CREATE TABLE classes (hash TEXT PRIMARY KEY, label TEXT, ambiguous INTEGER)")
        class_members = 0
        print(
            json.dumps({"stage": "class_archive_crc_and_label_join", "model_execution": False}),
            flush=True,
        )
        with zipfile.ZipFile(sources / "by_class.zip") as archive:
            for entry in nist.members(archive):
                if entry.is_dir():
                    continue
                data = archive.read(entry)
                class_members += 1
                if match := nist.CLASSES.fullmatch(entry.filename):
                    label = chr(int(match[1], 16))
                    if label not in LETTERS + "0123456789":
                        raise ValueError("r5_unexpected_source_class")
                    db.execute(
                        "INSERT INTO classes VALUES (?,?,0) ON CONFLICT(hash) DO UPDATE SET ambiguous=CASE WHEN label != excluded.label THEN 1 ELSE ambiguous END",
                        (nist.sha(data), label),
                    )
                elif Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                    raise ValueError("r5_class_archive_structure_changed")
        db.commit()
        print(
            json.dumps(
                {"stage": "writer_archive_crc_geometry_and_duplicates", "model_execution": False}
            ),
            flush=True,
        )
        with zipfile.ZipFile(sources / "by_write.zip") as archive:
            writers: dict[str, int] = {}
            for entry in nist.members(archive):
                if parts := nist.writer_parts(entry.filename):
                    partition, raw_writer, _, _ = parts
                    writer = nist_writer_id(raw_writer)
                    if writer in writers and writers[writer] != partition:
                        raise ValueError("r5_writer_spans_source_partitions")
                    writers[writer] = partition
            assignments, writer_audit = assign_writers(writers, exposure["writers"])
            rows, missing, blank, writer_members = [], 0, 0, 0
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                data = archive.read(entry)
                writer_members += 1
                parsed = nist.writer_parts(entry.filename)
                if not parsed:
                    if Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                        raise ValueError("r5_writer_archive_structure_changed")
                    continue
                partition, raw_writer, form, field = parsed
                writer = nist_writer_id(raw_writer)
                if writer not in assignments or field not in {"u", "l"}:
                    continue
                checksum = nist.sha(data)
                found = db.execute(
                    "SELECT label, ambiguous FROM classes WHERE hash=?", (checksum,)
                ).fetchone()
                if (
                    found is None
                    or found[1]
                    or found[0] not in LETTERS
                    or ((field == "u") != found[0].isupper())
                ):
                    missing += 1
                    continue
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        identity = image_identity(image)
                except ValueError as error:
                    if str(error) != "r5_blank_crop":
                        raise
                    blank += 1
                    continue
                rows.append(
                    {
                        "id": "r5-" + nist.sha(entry.filename.encode())[:24],
                        "hash": checksum,
                        "path": f"images/{checksum}.png",
                        "pixel_hash": identity["pixel_hash"],
                        "label": found[0],
                        "writer": writer,
                        "source_group": "nist-page-"
                        + nist.sha((raw_writer + ":" + form).encode())[:24],
                        "source_partition": partition,
                        "split": assignments[writer],
                        "source_member": entry.filename,
                        "normalized_pixel_hash": identity["normalized_pixel_hash"],
                        "geometry": identity,
                    }
                )
        selected, selection_audit = select_rows(rows, history, config)
        # Normalized identity records live beside the mapping; image bytes are unchanged publisher PNGs.
        selected_rows = [row for group in selected.values() for row in group]
        metadata = [{"id": row["id"], **row["geometry"]} for row in selected_rows]
        (temporary / "images").mkdir(mode=0o700)
        selected_members = {row["source_member"]: row["hash"] for row in selected_rows}
        with zipfile.ZipFile(sources / "by_write.zip") as archive:
            for member, expected in sorted(selected_members.items()):
                data = archive.read(member)
                if nist.sha(data) != expected:
                    raise ValueError("r5_source_changed_during_preparation")
                with (temporary / f"images/{expected}.png").open("xb") as output:
                    output.write(data)
        names = {
            "train": "train.jsonl",
            "validation": "validation.jsonl",
            "test": "final-test.jsonl",
            "school_test": "school-test.jsonl",
        }
        for split, name in names.items():
            (temporary / name).write_bytes(
                jsonl(
                    [
                        {
                            key: ("test" if key == "split" and split == "school_test" else row[key])
                            for key in FIELDS
                        }
                        for row in selected[split]
                    ]
                )
            )
        (temporary / "train-validation.jsonl").write_bytes(
            jsonl(
                [
                    {key: row[key] for key in FIELDS}
                    for split in ("train", "validation")
                    for row in selected[split]
                ]
            )
        )
        (temporary / "image-audit-private.jsonl").write_bytes(jsonl(metadata))
        (temporary / "history-private.jsonl").write_bytes(jsonl(history))
        write_json(temporary / "exposure.json", exposure)
        # The accepted licence and consent instrument are copied exactly as evidence, with their old admission pins.
        for name in ("admission.json", "manifest.json"):
            shutil.copyfile(r3 / name, temporary / ("r3-" + name))
        pins = {
            name: digest_file(temporary / name)
            for name in [
                *names.values(),
                "train-validation.jsonl",
                "image-audit-private.jsonl",
                "history-private.jsonl",
                "exposure.json",
                "r3-admission.json",
                "r3-manifest.json",
            ]
        }
        report = {
            "version": "english-r5-preparation-v1",
            "status": "prepared_local_research_only",
            "contract_version": "english-r5-contract-v1",
            "config": config,
            "config_hash": canonical_hash(config),
            "sources": receipts,
            "source_code_sha256": code_inventory(),
            "files": pins,
            "history": writer_audit,
            "selection": selection_audit,
            "partitions": {
                split: {
                    "samples": len(group),
                    "writers": len({r["writer"] for r in group}),
                    "source_partitions": dict(Counter(str(r["source_partition"]) for r in group)),
                }
                for split, group in selected.items()
            },
            "archive_members_crc_checked": {
                "by_class.zip": class_members,
                "by_write.zip": writer_members,
            },
            "unmapped_or_ambiguous": missing,
            "blank": blank,
            "case_preserved": True,
            "image_policy": config["image_policy"],
            "preprocessing_limits": {
                "page_baseline": "unavailable",
                "true_relative_letter_size": "unavailable",
                "model_crop_or_resize_quality": "unmeasured",
            },
            "prior_test_mistakes_used_for_tuning": False,
            "training": "not_run",
            "model_inference": False,
            "permission": "Existing owner-confirmed NIST non-commercial isolated-letter research admission reused; receipt/version not retained. No redistribution or product permission inferred.",
        }
        write_json(temporary / "preparation.json", report)
        write_json(
            temporary / "admission.json",
            {
                "version": "english-r5-derived-admission-v1",
                "preparation_sha256": digest_file(temporary / "preparation.json"),
                "scope": "local_noncommercial_isolated_letters_research",
                "source_admissions": exposure["admissions"],
            },
        )
        db.close()
        (temporary / "class-index.sqlite").unlink()
        for item in temporary.rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)
        temporary.rename(destination)
        return report
    finally:
        db.close()
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sources", "r3", "r4", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = prepare(args.sources, args.r3, args.r4, args.destination)
    print(
        json.dumps(
            {
                "status": report["status"],
                "history": report["history"],
                "partitions": report["partitions"],
                "unavailable_final_groups": report["selection"]["unavailable_final_groups"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
