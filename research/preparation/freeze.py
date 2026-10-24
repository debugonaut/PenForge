"""Freeze a bounded, writer-disjoint study from the audited private ledger."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from penforge_contracts.unicode import graphemes

from research.models import DatasetManifest, WriterRegistry
from research.preparation.archive_audit import ROOT, external, file_hash, sha
from research.validate import assert_build_eligible, digest, validate_datasets


def sample_id(corpus: str, path: str) -> str:
    return "r1-" + sha((corpus + ":" + path).encode())[:24]


def writer_id(writer: str) -> str:
    return "w-" + sha(("iiit-old:" + writer).encode())[:16]


def stable_selection(rows, seed, maximum):
    groups = defaultdict(list)
    for row in rows:
        groups[row["writer"]].append(row)
    result = []
    for writer, group in sorted(groups.items()):
        result.extend(sorted(group, key=lambda r: sha(f"{seed}:{r['id']}".encode()))[:maximum])
    return sorted(result, key=lambda r: r["id"])


def validate_split_rows(rows):
    seen = defaultdict(set)
    for row in rows:
        for kind, value in (
            ("writer", row["writer"]),
            ("source", row["page_id"]),
            ("content", row["hash"]),
            ("duplicate", row["duplicate"]),
        ):
            if value:
                seen[kind, value].add(row["split"])
    if any(len(splits) > 1 for splits in seen.values()):
        raise ValueError("writer_source_or_duplicate_leakage")


def near_duplicates(rows, staging, config):
    """Bounded perceptual candidate search; verification uses raster MAE and ink IoU.

    Only the corpus entering this study needs cross-split near-duplicate adjudication.
    Other corpora are excluded from the study, even when exact overlap is absent.
    """
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    pairs, checked, proposed, exhausted = [], set(), 0, False
    for i, row in enumerate(rows):
        h = int(row["phash"], 16)
        for band in range(4):
            key = (band, (h >> (16 * band)) & 65535)
            for j in buckets[key]:
                if (j, i) in checked or rows[j]["hash"] == row["hash"]:
                    continue
                other = rows[j]
                if (h ^ int(other["phash"], 16)).bit_count() > config[
                    "near_duplicate_phash_hamming"
                ]:
                    continue
                if (
                    abs(np.log((row["width"] / row["height"]) / (other["width"] / other["height"])))
                    > 0.02
                ):
                    continue
                checked.add((j, i))
                proposed += 1
                if proposed > config["near_duplicate_max_candidates"]:
                    exhausted = True
                    break
                images = []
                for item in (row, other):
                    path = staging / config["corpus"] / item["path"]
                    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if image is None:
                        raise ValueError("verified_source_disappeared")
                    images.append(cv2.resize(image, (128, 32)).astype(np.float32) / 255)
                a, b = images
                ink_a, ink_b = a < 0.5, b < 0.5
                union = np.count_nonzero(ink_a | ink_b)
                iou = np.count_nonzero(ink_a & ink_b) / union if union else 0
                mae = float(np.abs(a - b).mean())
                if mae <= config["near_duplicate_normalized_mae"] and iou >= 0.98:
                    pairs.append(
                        {
                            "a": row["id"],
                            "b": other["id"],
                            "mae": mae,
                            "ink_iou": iou,
                            "cross_writer": row["writer"] != other["writer"],
                            "label_disagreement": row["label"] != other["label"],
                        }
                    )
            if exhausted:
                break
            buckets[key].append(i)
        if exhausted:
            break
    if exhausted:
        raise ValueError("near_duplicate_search_cap_reached_do_not_admit")
    return pairs, proposed


def split_summary(rows):
    result = {}
    for split in ("train", "validation", "test"):
        group = [r for r in rows if r["split"] == split]
        labels = Counter(r["label"] for r in group)
        result[split] = {
            "writers": len({r["writer"] for r in group}),
            "samples": len(group),
            "pages": len({r["page_id"] for r in group}),
            "unique_word_labels": len(labels),
            "scalar_count": sum(len(r["label"]) for r in group),
            "script_samples": dict(
                Counter(
                    "Deva" if any("\u0900" <= c <= "\u097f" for c in r["label"]) else "other"
                    for r in group
                )
            ),
            "scalar_distribution": dict(
                sorted(Counter(f"U+{ord(c):04X}" for r in group for c in r["label"]).items())
            ),
            "word_distribution_by_hash": {sha(k.encode()): v for k, v in sorted(labels.items())},
            "writer_samples": dict(sorted(Counter(writer_id(r["writer"]) for r in group).items())),
        }
    return result


def select_style_rows(rows, config):
    """Same text in each writer's pool; distinct pages AND labels across halves."""
    writers = sorted({r["writer"] for r in rows if r["split"] == "test"})
    pools: dict[tuple[str, int], dict[str, Any]] = {
        (w, half): {} for w in writers for half in (0, 1)
    }
    for row in rows:
        if row["split"] != "test":
            continue
        half = int(sha(("style:" + row["page_id"]).encode())[:8], 16) % 2
        pools[row["writer"], half].setdefault(row["label"], row)
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for half in (0, 1):
        common = set.intersection(*(set(pools[w, half]) for w in writers)) if writers else set()
        common -= used
        labels = sorted(common, key=lambda word: sha(f"{config['seed']}:{word}".encode()))
        labels = labels[: config["style_labels_per_half"]]
        if len(labels) < config["style_min_shared_labels"]:
            return [], {
                "status": "insufficient_shared_content",
                "minimum": config["style_min_shared_labels"],
            }
        used.update(labels)
        for writer in writers:
            for label in labels:
                row = dict(pools[writer, half][label])
                row["style_role"] = "calibration" if half == 0 else "query"
                selected.append(row)
    return selected, {
        "status": "ready",
        "writers": len(writers),
        "samples": len(selected),
        "page_and_label_disjoint": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "research/configs/local-r1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "research/prepared/local-r1")
    parser.add_argument(
        "--rebuild-private-only",
        action="store_true",
        help="Recreate private mappings against the immutable public R1 ledger; do not alter admission records.",
    )
    args = parser.parse_args()
    staging, output = external(args.staging), args.output
    if (output / "preparation.json").exists() and not args.rebuild_private_only:
        raise ValueError("frozen_preparation_exists_use_rebuild_private_only")
    config = json.loads(args.config.read_text())
    db = sqlite3.connect(staging / "inventory.sqlite")
    db.row_factory = sqlite3.Row
    # The ZIP physically uses HindiOp2/writer/page/image, while its annotation
    # names retain HindiSeg/split/writer/page/image. Resolve explicitly, never
    # invent a missing file or use a path from a different corpus.
    entry_map = {
        r["path"]: dict(r)
        for r in db.execute(
            "SELECT * FROM entries WHERE corpus=? AND kind='.jpg'", (config["corpus"],)
        )
    }
    rows, missing = [], []
    for label_row in db.execute(
        "SELECT * FROM labels WHERE corpus=? ORDER BY path", (config["corpus"],)
    ):
        row = dict(label_row)
        path = row["path"]
        if path.startswith("HindiSeg/"):
            path = "HindiOp2/" + "/".join(path.split("/")[2:])
        if path not in entry_map:
            missing.append(sample_id(config["corpus"], path))
            continue
        row.update(entry_map[path])
        rows.append(row)
    for row in rows:
        row.update(
            id=sample_id(config["corpus"], row["path"]),
            split={"val": "validation"}.get(row["original_split"], row["original_split"]),
            page_id="page-" + sha(f"{row['writer']}:{row['page']}".encode())[:24],
            duplicate="dup-" + row["hash"][:32],
        )
    other = {
        "/".join(r["path"].split("/")[2:]): r["hash"]
        for r in db.execute(
            "SELECT path,hash FROM entries WHERE corpus='iiit-hw-hindi-v1-inner' AND kind='.jpg'"
        )
    }
    corresponding = [(r, other.get("/".join(r["path"].split("/")[1:]))) for r in entry_map.values()]
    alias_paths = sum(h is not None for r, h in corresponding)
    alias_bytes = sum(r["hash"] == h for r, h in corresponding)
    alias_writers = Counter(r["path"].split("/")[1] for r, h in corresponding if r["hash"] == h)
    if any(alias_writers[w] == 0 for w in {r["writer"] for r in rows}):
        raise ValueError("cross_package_writer_alias_lacks_exact_path_and_byte_evidence")
    errors = {r["id"] for r in rows if r["error"]}
    hash_groups = defaultdict(list)
    norm_groups = defaultdict(list)
    for row in rows:
        hash_groups[row["hash"]].append(row)
        norm_groups[row["normalized_hash"]].append(row)
    conflicts: set[str] = set()
    for group in [*hash_groups.values(), *norm_groups.values()]:
        if len({r["writer"] for r in group}) > 1 or len({r["label"] for r in group}) > 1:
            conflicts.update(r["id"] for r in group)
    clean = [r for r in rows if r["id"] not in errors | conflicts]
    near, proposed = near_duplicates(clean, staging, config)
    for pair in near:
        if pair["cross_writer"] or pair["label_disagreement"]:
            conflicts.update((pair["a"], pair["b"]))
    clean = [r for r in rows if r["id"] not in errors | conflicts]
    # Within-writer duplicates remain in the same split; select one exact source only.
    seen, unique = set(), []
    for row in clean:
        if row["hash"] not in seen:
            unique.append(row)
            seen.add(row["hash"])
    validate_split_rows(unique)
    recognition_rows = stable_selection(unique, config["seed"], config["max_samples_per_writer"])
    style_rows, style_scope = select_style_rows(unique, config)
    combined = {r["id"]: dict(r, recognition=True) for r in recognition_rows}
    for row in style_rows:
        combined.setdefault(row["id"], dict(row, recognition=False))["style_role"] = row[
            "style_role"
        ]
    single = [
        r
        for r in unique
        if len(graphemes(r["label"])) == 1 and any(0x0900 <= ord(c) <= 0x097F for c in r["label"])
    ]
    for row in single:
        combined.setdefault(row["id"], dict(row, recognition=False))["glyph_candidate"] = True
    selected = sorted(combined.values(), key=lambda r: r["id"])
    validate_split_rows(selected)
    private = staging / "study-r1.jsonl"
    public_rows = [
        {k: r[k] for k in ("id", "hash", "split", "page_id", "duplicate")}
        | {
            "writer": writer_id(r["writer"]),
            "label_hash": sha(r["label"].encode()),
            "recognition": r["recognition"],
            "style_role": r.get("style_role"),
            "glyph_candidate": r.get("glyph_candidate", False),
        }
        for r in selected
    ]
    ledger = output / "split-ids.jsonl"
    ledger_text = "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in public_rows
    )
    if args.rebuild_private_only:
        preparation = json.loads((output / "preparation.json").read_text())
        if (
            "sha256:" + sha(ledger_text.encode()) != preparation["split_ids_hash"]
            or digest(config) != preparation["config_hash"]
            or ledger.read_text() != ledger_text
        ):
            raise ValueError("rebuilt_study_differs_from_frozen_r1")
        private.write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in selected)
        )
        print(
            json.dumps(
                {
                    "status": "private_mapping_rebuilt",
                    "samples": len(selected),
                    "public_records_changed": False,
                    "training": "not_run",
                }
            )
        )
        return
    private.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in selected)
    )
    output.mkdir(parents=True, exist_ok=True)
    ledger.write_text(ledger_text)
    # Resolve only the documented old-IIIT namespace. No identity inference for UC/Doha.
    registry_path = ROOT / "research/manifests/writers-v1.yaml"
    registry = json.loads(registry_path.read_text())
    registry["identities"] = [
        i
        for i in registry["identities"]
        if i["dataset_id"] not in {"iiit-hw-dev", "iiit-indic-hw-words", "iiit-hw-dev-r1"}
    ]
    for corpus in ("iiit-hw-dev", "iiit-indic-hw-words", "iiit-hw-dev-r1"):
        for writer in sorted({r["writer"] for r in selected}):
            registry["identities"].append(
                {
                    "dataset_id": corpus,
                    "writer_id": writer_id(writer),
                    "writer_group_id": "old-iiit-" + writer_id(writer),
                    "evidence": "Old archive Readme explicitly identifies writer/page folders; "
                    "cross-package aliases checked against exact image hashes in local R1 audit. "
                    "This is a provider namespace, not an inferred real-world identity.",
                }
            )
    registry["version"] = "1.1.0"
    parsed_registry = WriterRegistry.model_validate(registry)
    registry_path.write_text(json.dumps(parsed_registry.model_dump(mode="json"), indent=2) + "\n")
    base = json.loads((ROOT / "research/manifests/iiit-hw-dev-v1.yaml").read_text())
    base.update(
        dataset_id="iiit-hw-dev-r1",
        version="1.0.0",
        status="approved",
        writer_resolution="resolved",
        writer_registry_ref="writers-v1:1.1.0",
        content_hash="sha256:" + file_hash(ledger),
        style_partitions=["mixed"],
    )
    base["license"]["permitted_uses"] = ["research_evaluation", "research_training"]
    base["source"].append(
        {
            "uri": "research/prepared/local-r1/split-ids.jsonl",
            "content_hash": base["content_hash"],
            "checked_on": "2026-10-21",
            "finding": "Frozen bounded research study; exact approved sample IDs, "
            "source/label hashes, split and pseudonymous writer/page groups.",
        }
    )
    base["samples"] = [
        {
            "sample_id": r["id"],
            "writer_id": writer_id(r["writer"]),
            "writer_group_id": "old-iiit-" + writer_id(r["writer"]),
            "script_code": "Deva",
            "style": "mixed",
            "split": r["split"],
            "content_hash": "sha256:" + r["hash"],
            "source_group_id": r["page_id"],
            "duplicate_group_id": r["duplicate"],
            "consent_state": "not_applicable",
        }
        for r in selected
    ]
    base["limitations"] = [
        "Admission is limited to the frozen bounded R1 study and research uses; no production/model approval.",
        "Writer IDs are provider-documented, not independently authenticated humans; 12 original writers only.",
        "Style is unstratified mixed writing; print/cursive subtypes and independent transcription audit are absent.",
        "Private owner-held grant and provider-asserted participation remain the evidence ceiling.",
        "Word images provide no character alignment, baseline, pen timing/pressure or shaping truth.",
        "Other corpora, unresolved identities, image errors and cross-writer duplicate ambiguities are excluded.",
    ]
    manifest = DatasetManifest.model_validate(base)
    validate_datasets([manifest], parsed_registry)
    assert_build_eligible([manifest], "research_training")
    (ROOT / "research/manifests/iiit-hw-dev-r1.yaml").write_text(
        json.dumps(base, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    # Isolated labels are an evidence test, not arbitrary segmentation of word crops.
    repeated = Counter((r["writer"], r["label"]) for r in single)
    report = {
        "version": "frozen-study-r1",
        "config_hash": digest(config),
        "manifest_hash": digest(manifest),
        "split_ids_hash": "sha256:" + file_hash(ledger),
        "registry_hash": digest(parsed_registry),
        "audited_labeled_images": len(rows),
        "label_rows": db.execute(
            "SELECT count(*) FROM labels WHERE corpus=?", (config["corpus"],)
        ).fetchone()[0],
        "unreadable": len(errors),
        "cross_writer_ambiguities_quarantined": len(conflicts),
        "quarantine_scope": "cross-writer duplicate or conflicting-label duplicate ambiguity",
        "within_writer_exact_duplicates_removed": len(clean) - len(unique),
        "near_duplicate_candidates_verified": proposed,
        "near_duplicates": near,
        "missing_label_image_ids": missing,
        "unlabeled_image_count": len(entry_map) - len(rows),
        "old_package_same_canonical_paths": alias_paths,
        "old_package_same_bytes_at_canonical_path": alias_bytes,
        "old_package_alias_support_per_writer": {
            writer_id(w): n for w, n in sorted(alias_writers.items())
        },
        "full_clean_splits": split_summary(unique),
        "study_splits": split_summary(selected),
        "recognition_splits": split_summary(recognition_rows),
        "style_scope": style_scope,
        "isolated_grapheme_samples": len(single),
        "writer_label_pairs_with_repeats": sum(n >= 2 for n in repeated.values()),
        "repeat_pairs_on_distinct_pages": sum(
            len({r["page_id"] for r in single if (r["writer"], r["label"]) == key}) >= 2
            for key, n in repeated.items()
            if n >= 2
        ),
        "raw_paths_or_transcriptions_in_public_output": False,
    }
    (output / "preparation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "audited_labeled_images",
                    "unreadable",
                    "cross_writer_ambiguities_quarantined",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
