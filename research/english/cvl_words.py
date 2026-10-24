"""Stage verified CVL English word crops, pending curator admission. No ML."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image
from PIL import __version__ as pillow_version

from research.english.cvl import SOURCE, check_source
from research.english.nist import canonical, sha
from research.english.study import validate_label
from research.preparation.archive_audit import ROOT, external, safe_name

NS = "{http://schema.primaresearch.org/PAGE/gts/pagecontent/2010-03-19}"
XML = re.compile(r"cvl-database-1-1/(trainset|testset)/xml/(\d{4})-([1234678])_attributes\.xml")
WORD = re.compile(
    r"cvl-database-1-1/(trainset|testset)/words/(\d{4})/(\d{4}-[1234678]-\d+-\d+)-(.+)\.tif"
)
ENGLISH_TEXTS = {"1", "2", "3", "4", "7", "8"}
CAPS = {"train": 64, "validation": 16, "test": 16}
# Leave room to replace malformed, blank or duplicate crops after decoding.
POOL_CAPS = {"train": 192, "validation": 64, "test": 64}


def annotation(raw: bytes, name: str) -> tuple[list[dict], dict]:
    match = XML.fullmatch(name)
    if not match or len(raw) > 1_000_000:
        raise ValueError("cvl_invalid_or_unbounded_xml")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, encoding = raw.decode("utf-16"), "utf16_bom"
    else:
        try:
            text, encoding = raw.decode("utf-8-sig"), "utf8"
        except UnicodeDecodeError:
            text, encoding = raw.decode("cp1252"), "cp1252"
    if any(token in text.upper() for token in ("<!DOCTYPE", "<!ENTITY")) or "\x00" in text:
        raise ValueError("cvl_xml_entities_dtd_or_nul_forbidden")
    # The authentic archive declares UTF-16 in ASCII/CP1252 files. Decode physical
    # bytes strictly, then require exact XML-to-filename labels; no replacement text.
    root = ET.fromstring(text)
    if root.tag != NS + "PcGts" or len(list(root.iter())) > 30_000:
        raise ValueError("cvl_unreviewed_xml_schema")
    pages = root.findall(NS + "Page")
    if len(pages) != 1:
        raise ValueError("cvl_xml_requires_one_page")
    page = pages[0]
    partition, writer, passage = match.groups()
    page_id = writer + "-" + passage
    regions = [
        node
        for node in root.iter(NS + "AttrRegion")
        if node.get("attrType") == "1" and node.get("fontType") == "2"
    ]
    if regions and page.get("imageFilename") != page_id + ".tif":
        raise ValueError("cvl_page_reference_mismatch")
    rows: list[dict] = []
    ids: dict[str, set[str]] = {}
    unlabelled, duplicate_ids = 0, 0
    for node in regions:
        rid, label = node.get("id", ""), node.get("text")
        if not re.fullmatch(re.escape(page_id) + r"-\d+-\d+", rid):
            raise ValueError("cvl_word_id_not_unique_or_wrong_page")
        if not label:
            unlabelled += 1
            continue
        if rid in ids:
            # The bundled German passage contains two correctly named crops for
            # each reused region ID. It is excluded from the English study.
            if passage != "6" or label in ids[rid]:
                raise ValueError("cvl_word_id_not_unique_or_wrong_page")
            duplicate_ids += 1
            ids[rid].add(label)
        else:
            ids[rid] = {label}
        if any(char in label for char in "/\\:"):
            raise ValueError("cvl_word_label_cannot_change_archive_path")
        member = f"cvl-database-1-1/{partition}/words/{writer}/{rid}-{label}.tif"
        if safe_name(member) != member:
            raise ValueError("cvl_unsafe_label_filename")
        rows.append(
            {
                "member": member,
                "raw_writer": writer,
                "passage": passage,
                "page": page_id,
                "publisher_partition": partition,
                "label": label,
                "xml_member": name,
                "xml_sha256": sha(raw),
            }
        )
    return rows, {
        "physical_encoding": encoding,
        "declared_utf16_physical_mismatch": 'encoding="UTF-16"' in text[:120]
        and encoding != "utf16_bom",
        "unlabelled_regions": unlabelled,
        "duplicate_labeled_ids": duplicate_ids,
        "without_handwriting": not regions,
    }


def rank(seed: int, value: str) -> str:
    return canonical([seed, value])


def writer_splits(rows: list[dict], seed: int) -> dict[str, str]:
    groups = {
        part: {r["raw_writer"] for r in rows if r["publisher_partition"] == part}
        for part in ("trainset", "testset")
    }
    if groups["trainset"] & groups["testset"]:
        raise ValueError("cvl_publisher_writer_overlap")
    training = sorted(groups["trainset"], key=lambda w: rank(seed, "writer:" + w))
    testing = sorted(groups["testset"], key=lambda w: rank(seed, "writer:" + w))
    if len(training) != 27 or len(testing) < 16:
        raise ValueError("cvl_expected_writer_inventory_changed")
    return {
        **dict.fromkeys(training[:6], "validation"),
        **dict.fromkeys(training[6:], "train"),
        **dict.fromkeys(testing[:16], "test"),
    }


def diverse(rows: list[dict], count: int, seed: int) -> list[dict]:
    ordered = sorted(rows, key=lambda r: rank(seed, r["id"] if "id" in r else r["member"]))
    unique, repeat, labels = [], [], set()
    for row in ordered:
        if row["label"] in labels:
            repeat.append(row)
        else:
            unique.append(row)
            labels.add(row["label"])
    return (unique + repeat)[:count]


def select(rows: list[dict], seed: int, *, excluded: set[str] | None = None):
    groups = defaultdict(list)
    for row in rows:
        groups[row["pixel_hash"]].append(row)
    conflicts = {
        r["id"]
        for group in groups.values()
        if len({r["writer"] for r in group}) > 1 or len({r["label"] for r in group}) > 1
        for r in group
    }
    removed = conflicts | (excluded or set())
    result = []
    for writer in sorted({r["writer"] for r in rows}):
        pool, seen = [], set()
        for row in sorted(rows, key=lambda r: r["id"]):
            if (
                row["writer"] == writer
                and row["id"] not in removed
                and row["pixel_hash"] not in seen
            ):
                pool.append(row)
                seen.add(row["pixel_hash"])
        wanted = CAPS[next(r["split"] for r in rows if r["writer"] == writer)]
        if len(pool) < wanted:
            raise ValueError("cvl_insufficient_reviewable_words_for_selected_writer")
        result.extend(diverse(pool, wanted, seed))
    return sorted(result, key=lambda r: r["id"]), len(conflicts)


def raster(raw: bytes) -> tuple[bytes, str]:
    if len(raw) > 8_000_000:
        raise ValueError("cvl_word_image_size_ceiling")
    with Image.open(io.BytesIO(raw)) as image:
        if (
            image.format != "TIFF"
            or getattr(image, "n_frames", 1) != 1
            or not 1 <= image.width * image.height <= 4_000_000
            or image.getexif().get(274, 1) != 1
        ):
            raise ValueError("cvl_unreviewed_image_format_frames_size_or_orientation")
        image.load()
        rgb = image.convert("RGB")
        pixel_hash = sha(f"{rgb.width}x{rgb.height}:RGB:".encode() + rgb.tobytes())
        if rgb.convert("L").getextrema()[0] == rgb.convert("L").getextrema()[1]:
            raise ValueError("cvl_blank_word_crop")
        clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
        output = io.BytesIO()
        clean.save(output, format="PNG", optimize=False, compress_level=6)
        return output.getvalue(), pixel_hash


def check_staging(staging: Path) -> dict:
    """Recheck the private word manifest and image bytes without using a model."""
    staging = external(staging)
    report_path = staging / "preparation.json"
    mapping_path = staging / "study.jsonl"
    candidates_path = staging / "candidates-private.jsonl"
    for path, ceiling in (
        (report_path, 1_000_000),
        (mapping_path, 8_000_000),
        (candidates_path, 64_000_000),
    ):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > ceiling:
            raise ValueError("cvl_staging_file_missing_unsafe_or_unbounded")
    report = json.loads(report_path.read_bytes())
    if (
        report.get("version") != "cvl-word-preparation-r3-v1"
        or report.get("dataset_admitted") is not False
        or report.get("model_inference") is not False
        or report.get("training") != "not_run"
        or report.get("nonexact_duplicate_review") != "pending"
    ):
        raise ValueError("cvl_staging_report_status_invalid")
    source_receipt = report.get("sources", {}).get(SOURCE[0], {})
    if source_receipt.get("bytes") != SOURCE[1] or source_receipt.get("md5") != SOURCE[2]:
        raise ValueError("cvl_staging_source_receipt_changed")
    mapping_bytes, candidate_bytes = mapping_path.read_bytes(), candidates_path.read_bytes()
    if sha(mapping_bytes) != report.get("mapping_sha256"):
        raise ValueError("cvl_study_mapping_changed")
    if sha(candidate_bytes) != report.get("candidate_mapping_sha256"):
        raise ValueError("cvl_candidate_mapping_changed")
    rows = [json.loads(line) for line in mapping_bytes.splitlines()]
    candidates = [json.loads(line) for line in candidate_bytes.splitlines()]
    if not rows or len(rows) > 6000 or len({row.get("id") for row in rows}) != len(rows):
        raise ValueError("cvl_study_mapping_empty_duplicate_or_unbounded")
    if not candidates or len(candidates) > 6000:
        raise ValueError("cvl_candidate_mapping_empty_or_unbounded")
    candidate_ids = {row.get("id") for row in candidates}
    if len(candidate_ids) != len(candidates) or not {row["id"] for row in rows} <= candidate_ids:
        raise ValueError("cvl_candidate_mapping_ids_invalid")
    observed: dict[tuple[str, str], set[str]] = {}
    images: dict[str, str] = {}
    for row in [*candidates, *rows]:
        validate_label(row["label"], "words")
        checksum = row.get("hash")
        if (
            row.get("split") not in CAPS
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or row.get("path") != f"images/{checksum}.png"
        ):
            raise ValueError("cvl_sample_split_or_content_address_changed")
        images[row["path"]] = row["hash"]
        for key in ("writer", "source_group", "hash", "pixel_hash"):
            observed.setdefault((key, row[key]), set()).add(row["split"])
    if any(len(splits) != 1 for splits in observed.values()):
        raise ValueError("cvl_writer_source_or_pixel_leakage")
    writer_counts = Counter((row["split"], row["writer"]) for row in rows)
    if any(count != CAPS[split] for (split, _), count in writer_counts.items()):
        raise ValueError("cvl_per_writer_study_cap_changed")
    for split, cap in (("train", 4096), ("validation", 256), ("test", 1024)):
        if not 1 <= sum(row["split"] == split for row in rows) <= cap:
            raise ValueError("cvl_study_partition_missing_or_over_budget")
    for relative, checksum in images.items():
        path = staging / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
            raise ValueError("cvl_staged_image_missing_unsafe_or_unbounded")
        data = path.read_bytes()
        if sha(data) != checksum:
            raise ValueError("cvl_staged_image_digest_changed")
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.mode != "RGB" or getattr(image, "n_frames", 1) != 1:
                raise ValueError("cvl_staged_png_format_changed")
            image.load()
            pixel = sha(f"{image.width}x{image.height}:RGB:".encode() + image.tobytes())
        if any(row["pixel_hash"] != pixel for row in candidates if row["path"] == relative):
            raise ValueError("cvl_staged_image_pixel_digest_changed")
    return {
        "status": "staging_integrity_verified_pending_admission",
        "samples": len(rows),
        "candidate_images": len(candidates),
        "images_rehashed": len(images),
        "writer_disjoint": True,
        "dataset_admitted": False,
        "model_execution": False,
    }


def audit(sources: Path, inventory: Path, destination: Path, seed: int = 20261021):
    sources, inventory, destination = external(sources), external(inventory), external(destination)
    source = check_source(sources)
    if source["status"] != "source_bytes_verified_only":
        raise ValueError("cvl_complete_manual_download_required")
    for other in (sources, inventory):
        if (
            destination.exists()
            or destination.is_relative_to(other)
            or other.is_relative_to(destination)
        ):
            raise ValueError("cvl_choose_new_nonoverlapping_destination")
    for name in ("inventory.json", "entries-private.jsonl"):
        path = inventory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000_000:
            raise ValueError("cvl_missing_safe_inventory")
    report = json.loads((inventory / "inventory.json").read_bytes())
    if report.get("source") != source["source"] or not report.get(
        "non_directory_members_crc_checked"
    ):
        raise ValueError("cvl_inventory_source_changed_or_crc_missing")
    ledger_rows = [
        json.loads(line) for line in (inventory / "entries-private.jsonl").read_text().splitlines()
    ]
    ledger = {r["member"]: r for r in ledger_rows}
    if len(ledger) != len(ledger_rows):
        raise ValueError("cvl_duplicate_inventory_members")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".cvl-words-", dir=destination.parent))
    try:
        (temporary / "images").mkdir(mode=0o700)
        with zipfile.ZipFile(sources / SOURCE[0]) as archive:
            entries = {e.filename: e for e in archive.infolist() if not e.is_dir()}
            if set(entries) != set(ledger) or any(
                entries[n].file_size != r["bytes"] for n, r in ledger.items()
            ):
                raise ValueError("cvl_inventory_members_changed")

            def read(name):
                data = archive.read(name)
                if sha(data) != ledger[name]["sha256"]:
                    raise ValueError("cvl_member_digest_changed")
                return data

            annotated: list[dict] = []
            stats: Counter[str] = Counter()
            encodings: Counter[str] = Counter()
            for name in sorted(n for n in entries if n.endswith(".xml")):
                page_rows, page = annotation(read(name), name)
                annotated.extend(page_rows)
                encodings[page["physical_encoding"]] += 1
                stats.update({k: int(v) for k, v in page.items() if isinstance(v, (int, bool))})
                stats["xml_pages"] += 1
            actual_words = {n for n in entries if "/words/" in n and n.endswith(".tif")}
            annotated_names = [r["member"] for r in annotated]
            if (
                len(set(annotated_names)) != len(annotated_names)
                or set(annotated_names) != actual_words
            ):
                raise ValueError("cvl_crop_xml_label_pairing_not_bijective")
            for row in annotated:
                match = WORD.fullmatch(row["member"])
                if (
                    not match
                    or match[2] != row["raw_writer"]
                    or not match[3].startswith(match[2] + "-")
                ):
                    raise ValueError("cvl_word_path_writer_mismatch")
            english, invalid = [], 0
            for row in annotated:
                if row["passage"] not in ENGLISH_TEXTS:
                    continue
                try:
                    validate_label(row["label"], "words")
                except ValueError:
                    invalid += 1
                    continue
                english.append(row)
            splits = writer_splits(english, seed)
            candidates = []
            rejected_images: Counter[str] = Counter()
            for writer, split in sorted(splits.items()):
                pool = diverse(
                    [r for r in english if r["raw_writer"] == writer], POOL_CAPS[split], seed
                )
                if len(pool) != POOL_CAPS[split]:
                    raise ValueError("cvl_insufficient_candidate_words_for_writer")
                for row in pool:
                    raw = read(row["member"])
                    try:
                        data, pixel = raster(raw)
                    except (ValueError, OSError) as error:
                        rejected_images[str(error)] += 1
                        continue
                    checksum = sha(data)
                    (temporary / "images" / (checksum + ".png")).write_bytes(data)
                    candidates.append(
                        {
                            **row,
                            "id": "cvl-" + sha(row["member"].encode())[:24],
                            "path": "images/" + checksum + ".png",
                            "hash": checksum,
                            "pixel_hash": pixel,
                            "writer": "cvl-w-" + sha(writer.encode())[:16],
                            "source_group": "cvl-page-" + sha(row["page"].encode())[:20],
                            "split": split,
                            "raw_sha256": ledger[row["member"]]["sha256"],
                        }
                    )
            selected, conflicts = select(candidates, seed)
            for name, rows in (("study.jsonl", selected), ("candidates-private.jsonl", candidates)):
                (temporary / name).write_text(
                    "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
                )
            result = {
                "version": "cvl-word-preparation-r3-v1",
                "status": "staged_pending_curator_admission",
                "sources": {SOURCE[0]: source["source"]},
                "inventory_sha256": sha((inventory / "inventory.json").read_bytes()),
                "inventory_ledger_sha256": sha((inventory / "entries-private.jsonl").read_bytes()),
                "seed": seed,
                "xml": {**dict(stats), "physical_encodings": dict(encodings)},
                "word_crops_exactly_paired": len(annotated),
                "german_text_6_excluded": sum(r["passage"] == "6" for r in annotated),
                "english_crops_before_label_filter": sum(
                    r["passage"] in ENGLISH_TEXTS for r in annotated
                ),
                "invalid_word_labels_excluded": invalid,
                "english_text_ids": sorted(ENGLISH_TEXTS),
                "candidate_images": len(candidates),
                "decoded_candidate_images": len(candidates),
                "invalid_images_excluded": dict(sorted(rejected_images.items())),
                "pixel_ambiguities_quarantined": conflicts,
                "partitions": {
                    split: {
                        "samples": sum(r["split"] == split for r in selected),
                        "writers": len({r["writer"] for r in selected if r["split"] == split}),
                        "distinct_labels": len(
                            {r["label"] for r in selected if r["split"] == split}
                        ),
                    }
                    for split in CAPS
                },
                "per_writer_caps": CAPS,
                "candidate_per_writer_caps": POOL_CAPS,
                "mapping_sha256": sha((temporary / "study.jsonl").read_bytes()),
                "candidate_mapping_sha256": sha(
                    (temporary / "candidates-private.jsonl").read_bytes()
                ),
                "decoder": {
                    "pillow": pillow_version,
                    "output": "rgb_png_original_pixels_metadata_stripped",
                },
                "source_code": {
                    str(p.relative_to(ROOT)): sha(p.read_bytes())
                    for p in (
                        Path(__file__),
                        ROOT / "research/english/cvl.py",
                        ROOT / "research/english/study.py",
                        ROOT / "pyproject.toml",
                        ROOT / "uv.lock",
                    )
                },
                "nonexact_duplicate_review": "pending",
                "license": "bundled_readme_cc_by_nc_3_0_zenodo_metadata_4_0_discrepancy_recorded",
                "dataset_admitted": False,
                "model_inference": False,
                "training": "not_run",
            }
            (temporary / "preparation.json").write_text(json.dumps(result, indent=2) + "\n")
            result["staging_integrity"] = check_staging(temporary)
            (temporary / "preparation.json").write_text(json.dumps(result, indent=2) + "\n")
        if destination.exists():
            raise ValueError("cvl_destination_created_concurrently")
        temporary.rename(destination)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--check-staging", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.check_staging is not None:
        if any(value is not None for value in (args.sources, args.inventory, args.destination)):
            parser.error("--check-staging cannot be combined with source preparation arguments")
        result = check_staging(args.check_staging)
    else:
        if any(value is None for value in (args.sources, args.inventory, args.destination)):
            parser.error("--sources, --inventory and --destination are required for preparation")
        assert (
            args.sources is not None and args.inventory is not None and args.destination is not None
        )
        result = audit(args.sources, args.inventory, args.destination)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
