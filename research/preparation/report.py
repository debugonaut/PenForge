"""Aggregate audited observations without exposing paths, handwriting or labels."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from research.preparation.archive_audit import ROOT, external, file_hash, sha
from research.validate import digest


def overlap_counts(db, column):
    corpora = [r[0] for r in db.execute("SELECT DISTINCT corpus FROM entries ORDER BY corpus")]
    numbers = {c: i for i, c in enumerate(corpora)}
    seen: dict[str, int] = {}
    for corpus, value in db.execute(
        f"SELECT corpus,{column} FROM entries WHERE kind IN ('.jpg','.png','.jpeg') AND error IS NULL"
    ):
        if value:
            seen[value] = seen.get(value, 0) | (1 << numbers[corpus])
    counts: Counter[str] = Counter()
    for mask in seen.values():
        if mask.bit_count() < 2:
            continue
        group = [c for c, i in numbers.items() if mask & (1 << i)]
        for a, c in enumerate(group):
            for d in group[a + 1 :]:
                counts[c + " / " + d] += 1
    return dict(sorted(counts.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staging", required=True, type=Path)
    p.add_argument("--audit-directory", default=ROOT / "research/runs/r1", type=Path)
    p.add_argument("--output", default=ROOT / "research/prepared/local-r1", type=Path)
    args = p.parse_args()
    db = sqlite3.connect(external(args.staging) / "inventory.sqlite")
    archives, inventories, resources = {}, {}, {}
    for path in sorted(args.audit_directory.glob("audit-*.json")):
        r = json.loads(path.read_text())
        archives.update(r["archives"])
        inventories.update(r["corpora"])
        resources[path.stem] = r["resources"]
    receipt = args.audit_directory / "independent-gzip-crc.json"
    if receipt.exists():
        gzip = json.loads(receipt.read_text())
        for name, item in zip(("iiit-hw-hindi-v1", "iiit-hw-hindi-v1-inner"), gzip, strict=True):
            archives[name].update(
                integrity="tar_headers_and_independent_gzip_crc",
                gzip_decoded_bytes=item["gzip_decoded_bytes"],
            )
    elif any(
        archives[name].get("integrity") != "tar_headers_and_independent_gzip_crc"
        for name in ("iiit-hw-hindi-v1", "iiit-hw-hindi-v1-inner")
    ):
        raise ValueError("independent_gzip_crc_evidence_required")
    result = {
        "version": "corpus-verification-r1",
        "date": "2026-10-21",
        "archives": archives,
        "inventories": inventories,
        "audit_resources": resources,
        "exact_shared_image_hashes": overlap_counts(db, "hash"),
        "exact_shared_normalized_thumbnail_hashes": overlap_counts(db, "normalized_hash"),
        "labels": {},
        "raw_images_or_labels_in_repository": False,
        "similarity_scope": "Exact byte and exact normalized-thumbnail hashes across every corpus; "
        "bounded pHash+MAE+ink-IoU verification within the sole admitted corpus during freeze. "
        "No claim that perceptual search identifies people or exhausts every cross-corpus near duplicate.",
    }
    for (corpus,) in db.execute("SELECT DISTINCT corpus FROM labels ORDER BY corpus"):
        entries = list(
            db.execute(
                "SELECT writer,page,original_split,label FROM labels WHERE corpus=?", (corpus,)
            )
        )
        groups = {
            split: {w for w, _, s, _ in entries if s == split} for split in ("train", "val", "test")
        }
        splits = {}
        for split in ("train", "val", "test"):
            subset = [(w, page, label) for w, page, s, label in entries if s == split]
            words = Counter(label for _, _, label in subset)
            full_distribution = {sha(k.encode()): n for k, n in sorted(words.items())}
            splits[split] = {
                "samples": len(subset),
                "groups": len(groups[split]),
                "pages": len({(w, page) for w, page, _ in subset}),
                "unique_word_labels": len(words),
                "word_distribution_hash": digest(full_distribution),
                "top_word_label_hashes": {sha(k.encode()): n for k, n in words.most_common(30)},
                "word_frequency_histogram": dict(sorted(Counter(words.values()).items())),
                "scalar_distribution": dict(
                    sorted(
                        Counter(f"U+{ord(c):04X}" for _, _, label in subset for c in label).items()
                    )
                ),
                "group_samples": dict(
                    sorted(
                        Counter(
                            "g-" + sha((corpus + ":" + w).encode())[:16] for w, _, _ in subset
                        ).items()
                    )
                ),
            }
        result["labels"][corpus] = {
            "original_splits": splits,
            "group_overlap_all_original_splits": len(set.intersection(*groups.values())),
            "group_interpretation": "documented_provider_writers"
            if corpus.endswith("inner")
            else "unadjudicated_collection_folders",
            "label_language": "Marathi" if corpus.endswith("marathi") else "Hindi",
            "script": "Deva",
            "style": "unstratified_mixed",
        }
    dhcd: Counter[str] = Counter()
    stems: Counter[str] = Counter()
    for (path,) in db.execute("SELECT path FROM entries WHERE corpus='dhcd' AND kind='.png'"):
        parts = path.split("/")
        dhcd[parts[1] + "/" + parts[2]] += 1
        stems[Path(path).stem] += 1
    result["dhcd"] = {
        "class_split_counts": dict(sorted(dhcd.items())),
        "numeric_file_stems": len(stems),
        "stems_reused_across_classes": sum(n > 1 for n in stems.values()),
        "writer_mapping_files": 0,
        "writer_resolution": "unresolved",
        "admitted": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "archive-verification.json"
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    report_hash = "sha256:" + file_hash(report)
    definitions = {
        "iiit-hw-dev-v1": (
            "iiit-hw-dev",
            "iiit-hw-dev-inner",
            [
                "The original ZIP verifies completely; its image root is HindiOp2 while annotation paths say HindiSeg/split.",
                "The full source remains candidate; only the separately frozen bounded R1 manifest may enter research builds.",
                "The older WORDS package substantially overlaps; do not combine them as independent writers or samples.",
                "No character boundaries, observed dynamics, independent label audit or production font evidence exists.",
            ],
        ),
        "iiit-indic-hw-words-v1": (
            "iiit-hw-hindi-v1",
            "iiit-hw-hindi-v1-inner",
            [
                "Outer and nested TAR gzip streams, members and images verify; no scripts were executed.",
                "This source substantially overlaps HW-Dev and is excluded as a second training corpus in R1.",
                "Documented writer paths support namespace aliases only with matching path and image-byte evidence.",
                "No personal-font or production recognition qualification follows from archive verification.",
            ],
        ),
        "iiit-indic-hw-uc-v1": (
            "iiit-indic-hw-uc-hindi",
            "iiit-indic-hw-uc-hindi",
            [
                "Both Hindi and Marathi ZIPs pass CRC/size/type/image inspection. Actual releases contain cropped word JPEGs and text labels, not the page JSON described by the landing page.",
                "Collection folders occur across published train/validation/test lists; those lists cannot establish writer-disjoint evaluation.",
                "Folder-to-human identities and cross-corpus aliases remain unadjudicated; all samples stay excluded from R1 training and writer-generalization claims.",
                "Unused/manual annotations and label correctness need separate curation; no glyph alignment or production font evidence exists.",
            ],
        ),
        "dhcd-candidate-v1": (
            "dhcd",
            "dhcd",
            [
                "The archive fully verifies: 92,000 32x32 images, 46 classes, published 78,200/13,800 image split.",
                "No annotation or writer-map file exists in this archive. Numeric image IDs do not establish writer identities.",
                "License/provenance review remains pending and no trustworthy writer-disjoint build is possible from current evidence; excluded from training and evaluation.",
                "Isolated glyph classification cannot validate personal style, word recognition or Indic font shaping.",
            ],
        ),
        "dohascript-v1": (
            None,
            "doha",
            [
                "The local sample has 200 JPEG-named paths: 197 match their extension, two decode as PNG despite a JPEG filename, and one is empty/unreadable. No trusted transcription or writer-map file was downloaded.",
                "Demographic filenames are private source metadata, not reliable global writer identities; no identities are inferred from them.",
                "Excluded from R1 training/evaluation. Research-only participant consent remains binding; institutional permission does not widen it.",
                "Page words cannot be assigned or split into glyph truth from an assumed fixed prompt.",
            ],
        ),
    }
    for filename, (archive, inventory, limitations) in definitions.items():
        path = ROOT / "research/manifests" / (filename + ".yaml")
        manifest = json.loads(path.read_text())
        manifest["content_hash"] = "sha256:" + (
            archives[archive]["sha256"] if archive else inventories[inventory]["inventory_sha256"]
        )
        if filename == "iiit-indic-hw-uc-v1":
            manifest["content_hash"] = digest(
                {
                    k: archives[k]["sha256"]
                    for k in ("iiit-indic-hw-uc-hindi", "iiit-indic-hw-uc-marathi")
                }
            )
        for source in manifest["source"]:
            for key, details in archives.items():
                if "sha256" not in details:
                    continue
                uri_tail = {
                    "iiit-hw-dev": "IIIT-HW-Dev.zip",
                    "iiit-hw-hindi-v1": "IIIT-HW-Hindi_v1.tar.gz",
                    "iiit-indic-hw-uc-hindi": "/hindi.zip",
                    "iiit-indic-hw-uc-marathi": "/marathi.zip",
                }.get(key)
                if uri_tail and source["uri"].endswith(uri_tail):
                    source.update(
                        content_hash="sha256:" + details["sha256"],
                        checked_on="2026-10-21",
                        finding=f"Local R1 verification: {details['bytes']} bytes match recorded expected size; SHA-256 and full archive integrity/image inspection recorded in the derived verification report. Permission scope is unchanged.",
                    )
        manifest["source"] = [
            s
            for s in manifest["source"]
            if s["uri"] != "research/prepared/local-r1/archive-verification.json"
        ]
        manifest["source"].append(
            {
                "uri": "research/prepared/local-r1/archive-verification.json",
                "content_hash": report_hash,
                "checked_on": "2026-10-21",
                "finding": "Complete local archive/sample audit with cryptographic inventories, image errors, annotation/group counts, cross-corpus exact comparisons and admission limitations. No raw paths, transcriptions or private grant instrument is included.",
            }
        )
        manifest["limitations"] = limitations + [
            "Private grants/provider participation remain asserted evidence, not independently reviewed participant opt-in."
        ]
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"archives": len(archives), "inventories": len(inventories), "report_hash": report_hash}
        )
    )


if __name__ == "__main__":
    main()
