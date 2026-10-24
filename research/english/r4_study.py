"""Prepare and validate the private, writer-disjoint English letters R4 study.

This module audits local NIST SD19 archives and builds a fresh private split. It
does not download data, load a model, perform inference, or train.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import string
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from PIL import Image
from PIL import __version__ as pillow_version
from pydantic import BaseModel, ConfigDict, Field

from research.english import nist
from research.english.acquire import SOURCES, inspect
from research.english.study import checked_file
from research.models import (
    ConsentReview,
    DatasetManifest,
    Evidence,
    LicenseReview,
    ResearchSample,
    WriterIdentity,
    WriterRegistry,
)
from research.preparation.archive_audit import ROOT, external
from research.pretrained.artifacts import digest_file
from research.validate import assert_build_eligible, load_record, validate_datasets

LETTERS = string.ascii_uppercase + string.ascii_lowercase
# The pinned SD19 by_write archive contains HSF0, 1, 2, 3, 4, 6 and 7;
# HSF8 is absent. Keep available Census-origin partitions primary and HSF4 separate.
PRIMARY_PARTITIONS = (0, 1, 2, 3, 6, 7)
SCHOOL_PARTITION = 4
PRIMARY_SPLITS = ("train", "validation", "test")
R4_VERSION = "english-r4-letters-v1"
PREPARATION_VERSION = "nist-letter-preparation-r4-v1"
ADMISSION_VERSION = "english-admission-r4-v1"
OWNER_APPROVAL_REF = "urn:penforge:owner-approval:english-letters-r4:2026-10-21"


class R4Admission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: str = ADMISSION_VERSION
    task: str = "letters"
    authorization_ref: str = OWNER_APPROVAL_REF
    preparation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cross_domain_mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    related_manifests: dict[str, str]
    cross_domain_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    old_r3_writers_excluded: bool = True
    duplicate_policy: str = "exact_bytes_and_grayscale_pixels_quarantined"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def nist_writer_id(raw: str) -> str:
    return "nist-" + sha(raw.encode())[:16]


def writer_splits(
    writers: dict[str, int], excluded_writer_ids: set[str], seed: int
) -> tuple[dict[str, str], dict]:
    """Split eligible writers within each Census partition; reserve HSF4 for shift test."""
    result: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    for partition in PRIMARY_PARTITIONS:
        eligible = [
            writer
            for writer, observed_partition in writers.items()
            if observed_partition == partition and nist_writer_id(writer) not in excluded_writer_ids
        ]
        eligible.sort(key=lambda writer: canonical([seed, "r4", partition, writer]))
        if len(eligible) < 3:
            raise ValueError("r4_primary_partition_has_too_few_unused_writers")
        validation_n = max(1, round(len(eligible) * 0.15))
        test_n = max(1, round(len(eligible) * 0.15))
        if validation_n + test_n >= len(eligible):
            raise ValueError("r4_primary_partition_split_has_no_training_writers")
        validation = eligible[:validation_n]
        test = eligible[validation_n : validation_n + test_n]
        train = eligible[validation_n + test_n :]
        result.update((writer, "validation") for writer in validation)
        result.update((writer, "test") for writer in test)
        result.update((writer, "train") for writer in train)
        counts[str(partition)] = {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        }

    school = [
        writer
        for writer, partition in writers.items()
        if partition == SCHOOL_PARTITION and nist_writer_id(writer) not in excluded_writer_ids
    ]
    school.sort(key=lambda writer: canonical([seed, "r4-school-test", writer]))
    result.update((writer, "cross_test") for writer in school)
    counts["hsf4"] = {"cross_test": len(school)}
    if not {"train", "validation", "test"}.issubset(set(result.values())):
        raise ValueError("r4_primary_split_missing_partition")
    if excluded_writer_ids & {nist_writer_id(writer) for writer in result}:
        raise ValueError("r4_old_study_writer_was_not_excluded")
    return result, counts


def _allocate_quotas(available: dict[int, int], maximum: int) -> dict[int, int]:
    total = sum(available.values())
    target = min(maximum, total)
    if target <= 0:
        return {partition: 0 for partition in available}
    exact = {partition: target * count / total for partition, count in available.items()}
    quotas = {partition: int(value) for partition, value in exact.items()}
    left = target - sum(quotas.values())
    order = sorted(available, key=lambda partition: (-(exact[partition] % 1), partition))
    for partition in order[:left]:
        quotas[partition] += 1
    return quotas


def select_rows(
    rows: list[dict],
    *,
    seed: int,
    previous_hashes: set[str],
    previous_pixel_hashes: set[str],
    train_cap: int = 64,
    evaluation_cap: int = 32,
    minimum_evaluation: int = 16,
) -> tuple[list[dict], list[dict], dict]:
    """Quarantine exact/pixel duplicates, then select distinct writers per class/partition."""
    duplicate_groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        duplicate_groups[row["pixel_hash"]].append(row)
    ambiguous = {
        row["id"]
        for group in duplicate_groups.values()
        if len({(row["writer"], row["label"]) for row in group}) > 1
        for row in group
    }
    old_overlap = {
        row["id"]
        for row in rows
        if row["hash"] in previous_hashes or row["pixel_hash"] in previous_pixel_hashes
    }
    caps = {
        "train": train_cap,
        "validation": evaluation_cap,
        "test": evaluation_cap,
        "cross_test": evaluation_cap,
    }
    minimums = {
        "train": 1,
        "validation": minimum_evaluation,
        "test": minimum_evaluation,
        "cross_test": minimum_evaluation,
    }
    cross_possible = True
    for label in LETTERS:
        available_writers = {
            row["writer"]
            for row in rows
            if row["split"] == "cross_test"
            and row["label"] == label
            and row["id"] not in ambiguous
            and row["id"] not in old_overlap
        }
        if len(available_writers) < minimum_evaluation:
            cross_possible = False
            break
    if not cross_possible:
        caps.pop("cross_test")
        minimums.pop("cross_test")
    selected: list[dict] = []
    coverage: dict[str, dict[str, int]] = {}
    for split, maximum in caps.items():
        coverage[split] = {}
        for label in LETTERS:
            pool = [
                row
                for row in rows
                if row["split"] == split
                and row["label"] == label
                and row["id"] not in ambiguous
                and row["id"] not in old_overlap
            ]
            # One deterministic sample per writer and partition for this label.
            by_partition: dict[int, dict[str, dict]] = defaultdict(dict)
            for row in sorted(
                pool,
                key=lambda item: canonical([seed, split, label, item["id"]]),
            ):
                by_partition[row["partition"]].setdefault(row["writer"], row)
            available = {
                partition: len(writers) for partition, writers in by_partition.items() if writers
            }
            quotas = _allocate_quotas(available, maximum)
            chosen: list[dict] = []
            for partition in sorted(by_partition):
                quota = quotas.get(partition, 0)
                chosen.extend(
                    sorted(
                        by_partition[partition].values(),
                        key=lambda item: canonical([seed, split, label, item["writer"]]),
                    )[:quota]
                )
            if len(chosen) < maximum and sum(available.values()) > len(chosen):
                remaining = [
                    row
                    for partition, group in by_partition.items()
                    for row in group.values()
                    if row not in chosen
                ]
                remaining.sort(key=lambda item: canonical([seed, split, label, item["id"]]))
                chosen.extend(remaining[: maximum - len(chosen)])
            if len(chosen) < minimums[split]:
                raise ValueError(f"r4_insufficient_writer_coverage:{split}:{ord(label):04x}")
            if len({row["writer"] for row in chosen}) != len(chosen):
                raise ValueError("r4_selection_reused_writer_for_letter")
            coverage[split][label] = len(chosen)
            selected.extend(chosen)

    by_split: dict[str, list[dict]] = {
        split: [] for split in ("train", "validation", "test", "cross_test")
    }
    for row in selected:
        by_split[row["split"]].append(row)
    primary_writers = {
        split: {row["writer"] for row in by_split[split]}
        for split in ("train", "validation", "test")
    }
    if any(
        primary_writers[left] & primary_writers[right]
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    ):
        raise ValueError("r4_primary_writer_split_leakage")
    if primary_writers["train"] & {row["writer"] for row in by_split["cross_test"]}:
        raise ValueError("r4_school_writer_split_leakage")
    primary = [
        _public_row(row) for split in ("train", "validation", "test") for row in by_split[split]
    ]
    cross_domain = [_public_row(row) for row in by_split["cross_test"]]
    report = {
        "candidate_images": len(rows),
        "pixel_ambiguity_rows_quarantined": len(ambiguous),
        "previous_study_overlap_rows_quarantined": len(old_overlap),
        "selected_per_class": coverage,
        "cross_domain_test_available": cross_possible,
        "cross_domain_unavailable_reason": None
        if cross_possible
        else "fewer_than_minimum_distinct_writers_for_at_least_one_letter",
        "partitions": {
            split: {
                "samples": len(items),
                "writers": len({row["writer"] for row in items}),
                "source_partitions": dict(
                    sorted(Counter(row["partition"] for row in items).items())
                ),
            }
            for split, items in by_split.items()
        },
    }
    return primary, cross_domain, report


def _public_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "hash": row["hash"],
        "path": row["path"],
        "pixel_hash": row["pixel_hash"],
        "label": row["label"],
        "writer": row["writer"],
        "source_group": row["source_group"],
        "source_partition": row["partition"],
        "split": "test" if row["split"] == "cross_test" else row["split"],
        "source_member": row["source_member"],
    }


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in sorted(rows, key=lambda item: item["id"])
    ).encode("utf-8")


def _manifest(
    *,
    dataset_id: str,
    mapping_bytes: bytes,
    rows: list[dict],
    registry_ref: str,
    license_review: LicenseReview,
    consent: ConsentReview,
    source: tuple[Evidence, ...],
) -> DatasetManifest:
    samples = tuple(
        ResearchSample(
            sample_id=row["id"],
            writer_id=row["writer"],
            writer_group_id="nist-sd19-" + row["writer"][5:],
            script_code="Latn",
            style="print",
            split=row["split"],
            content_hash="sha256:" + row["hash"],
            source_group_id=row["source_group"],
            duplicate_group_id="dup-" + row["pixel_hash"][:32],
            parent_content_hashes=(),
            consent_ref=None,
            consent_state="not_applicable",
        )
        for row in rows
    )
    return DatasetManifest(
        dataset_id=dataset_id,
        version="1.0.0",
        status="approved",
        source_kind="licensed_public",
        source=source,
        script_codes=("Latn",),
        style_partitions=("print",),
        license=license_review,
        consent=consent,
        writer_resolution="resolved",
        writer_registry_ref=registry_ref,
        content_hash="sha256:" + sha(mapping_bytes),
        samples=samples,
        limitations=(
            "Local, non-commercial isolated-letter research only; this is not a product dataset.",
            "NIST source writer IDs are pseudonyms, not real-world identity assertions.",
            "No individual-participant opt-in is asserted; cross-corpus writer aliases are unresolved.",
            "The model's original pretraining overlap is not independently audited.",
        ),
    )


def _build_governance(
    baseline: Path,
    primary_rows: list[dict],
    cross_rows: list[dict],
    primary_bytes: bytes,
    cross_bytes: bytes,
) -> tuple[WriterRegistry, DatasetManifest, DatasetManifest | None, dict, bytes]:
    old_manifest = DatasetManifest.model_validate_json((baseline / "manifest.json").read_bytes())
    old_registry = WriterRegistry.model_validate_json((baseline / "writers.json").read_bytes())
    old_admission = json.loads((baseline / "admission.json").read_text())
    if "research_training" not in old_manifest.license.permitted_uses:
        raise ValueError("r4_requires_existing_owner_confirmed_nist_training_scope")
    previous_registry_path = ROOT / "research/manifests/writers-v1.yaml"
    previous_registry = WriterRegistry.model_validate_json(previous_registry_path.read_bytes())
    if digest_file(previous_registry_path) != old_admission.get("previous_registry_sha256"):
        raise ValueError("r4_frozen_writer_registry_does_not_match_prior_admission")

    registry_identities = list(old_registry.identities)
    known = {(item.dataset_id, item.writer_id) for item in registry_identities}
    all_rows = [*primary_rows, *cross_rows]
    for row in all_rows:
        dataset_id = (
            "nist-sd19-english-letters-r4-hsf4"
            if row.get("source_partition") == SCHOOL_PARTITION
            else "nist-sd19-english-letters-r4"
        )
        identity = (dataset_id, row["writer"])
        if identity in known:
            continue
        registry_identities.append(
            WriterIdentity(
                dataset_id=dataset_id,
                writer_id=row["writer"],
                writer_group_id="nist-sd19-" + row["writer"][5:],
                evidence=(
                    "NIST SD19 by_write path identifies this pseudonymous source writer. "
                    "This is a within-corpus split identity, not a real-world identity assertion."
                ),
            )
        )
        known.add(identity)

    registry = WriterRegistry(
        registry_id=old_registry.registry_id,
        version="1.3.0",
        identities=tuple(registry_identities),
        limitations=old_registry.limitations,
    )
    if not {
        (item.dataset_id, item.writer_id, item.writer_group_id)
        for item in previous_registry.identities
    }.issubset(
        {(item.dataset_id, item.writer_id, item.writer_group_id) for item in registry.identities}
    ):
        raise ValueError("r4_writer_registry_does_not_extend_frozen_registry")

    approval_evidence = Evidence(
        uri=OWNER_APPROVAL_REF,
        checked_on=date(2026, 10, 21),
        finding=(
            "Owner approved the fresh NIST writer split and comparison of expanded TrOCR "
            "fine-tuning with a 52-class visual classifier for local English-letter research."
        ),
    )
    license_review = LicenseReview(
        identifier=old_manifest.license.identifier,
        status="verified",
        evidence=tuple(old_manifest.license.evidence) + (approval_evidence,),
        permitted_uses=("research_evaluation", "research_training"),
        restrictions=(
            "Approved local non-commercial English isolated-letter research only.",
            "Do not redistribute NIST source archives or selected study images.",
            "Do not publish model checkpoints, derived weights, or predictions without a separate review.",
            "No product deployment or commercial use is included in this study.",
            "The owner-confirmed NIST terms acceptance receipt/version was not retained.",
        ),
    )
    consent = old_manifest.consent
    source = tuple(old_manifest.source)
    registry_ref = f"{registry.registry_id}:{registry.version}"
    primary_manifest = _manifest(
        dataset_id="nist-sd19-english-letters-r4",
        mapping_bytes=primary_bytes,
        rows=primary_rows,
        registry_ref=registry_ref,
        license_review=license_review,
        consent=consent,
        source=source,
    )
    cross_manifest = (
        _manifest(
            dataset_id="nist-sd19-english-letters-r4-hsf4",
            mapping_bytes=cross_bytes,
            rows=cross_rows,
            registry_ref=registry_ref,
            license_review=license_review,
            consent=consent,
            source=source,
        )
        if cross_rows
        else None
    )
    related_path = baseline / "manifest.json"
    related_hash = digest_file(related_path)
    related = {"previous-r3-manifest.json": related_hash}
    return registry, primary_manifest, cross_manifest, related, previous_registry_path.read_bytes()


def _load_old_rows(baseline: Path) -> tuple[list[dict], DatasetManifest, WriterRegistry]:
    from research.english.study import Recipe, prepare

    old_study, _ = prepare(baseline, Recipe(task="letters"), training=True, include_test=True)
    rows = [
        *old_study["selected"]["train"],
        *old_study["selected"]["validation"],
        *old_study["selected"]["test"],
    ]
    return (
        rows,
        DatasetManifest.model_validate_json((baseline / "manifest.json").read_bytes()),
        WriterRegistry.model_validate_json((baseline / "writers.json").read_bytes()),
    )


def audit(sources: Path, baseline: Path, destination: Path, *, seed: int = 20261022) -> dict:
    """Audit source archives and stage the pre-approved R4 experiment outside the repo."""
    sources, baseline, destination = external(sources), external(baseline), external(destination)
    if (
        destination.exists()
        or destination.is_relative_to(sources)
        or sources.is_relative_to(destination)
        or destination.is_relative_to(baseline)
        or baseline.is_relative_to(destination)
    ):
        raise ValueError("r4_destination_must_be_new_and_separate_from_inputs")
    old_rows, old_manifest, old_registry = _load_old_rows(baseline)
    excluded_writers = {row["writer"] for row in old_rows}
    previous_hashes = {row["hash"] for row in old_rows}
    previous_pixels = {row["pixel_hash"] for row in old_rows}

    receipts = {
        name: inspect(sources / name, *SOURCES[name]) for name in ("by_write.zip", "by_class.zip")
    }
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".english-r4-", dir=destination.parent))
    db = sqlite3.connect(temporary / "class-index.sqlite")
    try:
        db.execute("CREATE TABLE classes (hash TEXT PRIMARY KEY, label TEXT, ambiguous INTEGER)")
        class_images, class_members_crc_checked = 0, 0
        with zipfile.ZipFile(sources / "by_class.zip") as archive:
            for entry in nist.members(archive):
                if entry.is_dir():
                    continue
                data = archive.read(entry)
                class_members_crc_checked += 1
                match = nist.CLASSES.fullmatch(entry.filename)
                if not match:
                    if Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                        raise ValueError("nist_class_archive_structure_changed")
                    continue
                label = chr(int(match[1], 16))
                if label not in LETTERS + string.digits:
                    raise ValueError("nist_unexpected_character_class")
                db.execute(
                    "INSERT INTO classes VALUES (?,?,0) ON CONFLICT(hash) DO UPDATE SET "
                    "ambiguous=CASE WHEN label != excluded.label THEN 1 ELSE ambiguous END",
                    (sha(data), label),
                )
                class_images += 1
        db.commit()

        writers: dict[str, int] = {}
        with zipfile.ZipFile(sources / "by_write.zip") as archive:
            for entry in nist.members(archive):
                parsed = nist.writer_parts(entry.filename)
                if not parsed:
                    continue
                partition, writer, _, _ = parsed
                if writer in writers and writers[writer] != partition:
                    raise ValueError("nist_writer_occurs_in_multiple_partitions")
                writers[writer] = partition
            assignments, writer_partition_counts = writer_splits(writers, excluded_writers, seed)
            rows, unmapped, blank, writer_images = [], 0, 0, 0
            writer_members_crc_checked = 0
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                data = archive.read(entry)  # Full member read verifies its ZIP CRC.
                writer_members_crc_checked += 1
                parsed = nist.writer_parts(entry.filename)
                if not parsed:
                    if Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                        raise ValueError("nist_writer_archive_structure_changed")
                    continue
                partition, writer, form, field = parsed
                writer_images += 1
                split = assignments.get(writer)
                if split is None or field not in {"u", "l"}:
                    continue
                byte_hash = sha(data)
                found = db.execute(
                    "SELECT label, ambiguous FROM classes WHERE hash=?", (byte_hash,)
                ).fetchone()
                if found is None or found[1] or found[0] not in LETTERS:
                    unmapped += 1
                    continue
                label = found[0]
                if (field == "u") != label.isupper():
                    unmapped += 1
                    continue
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format != "PNG"
                        or image.size != (128, 128)
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise ValueError("nist_selected_image_format_or_dimensions_changed")
                    image.load()
                    gray = image.convert("L")
                    if gray.getextrema()[0] == gray.getextrema()[1]:
                        blank += 1
                        continue
                    pixel_hash = sha(gray.tobytes())
                rows.append(
                    {
                        "id": "r4-" + sha(entry.filename.encode())[:24],
                        "hash": byte_hash,
                        "path": "images/" + byte_hash + ".png",
                        "label": label,
                        "writer": nist_writer_id(writer),
                        "split": split,
                        "partition": partition,
                        "pixel_hash": pixel_hash,
                        "source_group": "nist-page-" + sha((writer + ":" + form).encode())[:24],
                        "source_member": entry.filename,
                    }
                )
        primary_rows, cross_rows, selection = select_rows(
            rows,
            seed=seed,
            previous_hashes=previous_hashes,
            previous_pixel_hashes=previous_pixels,
        )
        primary_mapping = _jsonl(primary_rows)
        cross_mapping = _jsonl(cross_rows)
        registry, manifest, cross_manifest, related, previous_registry = _build_governance(
            baseline, primary_rows, cross_rows, primary_mapping, cross_mapping
        )

        selected_members = {
            row["source_member"]: row["hash"] for row in [*primary_rows, *cross_rows]
        }
        with zipfile.ZipFile(sources / "by_write.zip") as archive:
            for entry in archive.infolist():
                if entry.filename not in selected_members:
                    continue
                data = archive.read(entry)
                checksum = sha(data)
                if checksum != selected_members[entry.filename]:
                    raise ValueError("nist_selected_source_changed_between_passes")
                target = temporary / "images" / f"{checksum}.png"
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not target.exists():
                    with target.open("xb") as output:
                        output.write(data)

        (temporary / "study.jsonl").write_bytes(primary_mapping)
        (temporary / "cross-domain-study.jsonl").write_bytes(cross_mapping)
        (temporary / "manifest.json").write_text(
            manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        if cross_manifest is not None:
            (temporary / "cross-domain-manifest.json").write_text(
                cross_manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
        (temporary / "writers.json").write_text(
            registry.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "previous-r3-manifest.json").write_bytes(
            (baseline / "manifest.json").read_bytes()
        )
        previous_mapping = (baseline / "study.jsonl").read_bytes()
        (temporary / "previous-r3-study.jsonl").write_bytes(previous_mapping)
        (temporary / "previous-writer-registry.json").write_bytes(previous_registry)
        code_files = (
            "research/english/r4_study.py",
            "research/english/nist.py",
            "research/english/acquire.py",
            "research/english/study.py",
            "research/models.py",
            "research/validate.py",
            "research/preparation/archive_audit.py",
            "research/pretrained/artifacts.py",
            "research/manifests/writers-v1.yaml",
        )
        preparation = {
            "version": PREPARATION_VERSION,
            "status": "owner_approved_local_research",
            "authorization_ref": OWNER_APPROVAL_REF,
            "created_on": date.today().isoformat(),
            "seed": seed,
            "primary_source_partitions": list(PRIMARY_PARTITIONS),
            "cross_domain_partition": SCHOOL_PARTITION,
            "per_class_caps": {"train": 64, "validation": 32, "test": 32, "cross_test": 32},
            "minimum_evaluation_per_class": 16,
            "sources": receipts,
            "source_images_crc_checked": class_images,
            "class_archive_members_crc_checked": class_members_crc_checked,
            "writer_images_crc_checked": writer_images,
            "writer_archive_members_crc_checked": writer_members_crc_checked,
            "source_writers": len(writers),
            "old_r3_writers_excluded": len(excluded_writers),
            "old_r3_writer_exclusion_hash": canonical(sorted(excluded_writers)),
            "old_r3_mapping_sha256": sha(previous_mapping),
            "writer_partition_counts": writer_partition_counts,
            "unmapped_or_ambiguous_candidate_images": unmapped,
            "blank_candidate_images": blank,
            "primary_mapping_sha256": sha(primary_mapping),
            "cross_domain_mapping_sha256": sha(cross_mapping),
            "source_code_sha256": {name: digest_file(ROOT / name) for name in code_files},
            "environment": {"python": sys.version, "pillow": pillow_version},
            "selection": selection,
            "writer_disjoint": True,
            "previous_study_content_excluded": True,
            "training": "not_run",
            "model_inference": False,
        }
        preparation_path = temporary / "preparation.json"
        preparation_path.write_text(json.dumps(preparation, indent=2, sort_keys=True) + "\n")
        admission = R4Admission(
            preparation_sha256=sha(preparation_path.read_bytes()),
            mapping_sha256=sha(primary_mapping),
            manifest_sha256=digest_file(temporary / "manifest.json"),
            registry_sha256=digest_file(temporary / "writers.json"),
            previous_registry_sha256=sha(previous_registry),
            previous_mapping_sha256=sha(previous_mapping),
            cross_domain_mapping_sha256=sha(cross_mapping),
            related_manifests={
                **related,
                **(
                    {
                        "cross-domain-manifest.json": digest_file(
                            temporary / "cross-domain-manifest.json"
                        )
                    }
                    if cross_manifest is not None
                    else {}
                ),
            },
            cross_domain_manifest_sha256=(
                digest_file(temporary / "cross-domain-manifest.json")
                if cross_manifest is not None
                else None
            ),
        )
        (temporary / "admission.json").write_text(admission.model_dump_json(indent=2) + "\n")
        db.close()
        (temporary / "class-index.sqlite").unlink()
        if destination.exists():
            raise ValueError("r4_staging_created_concurrently")
        temporary.rename(destination)
        return preparation
    finally:
        db.close()
        if temporary.exists():
            shutil.rmtree(temporary)


def validate_staging(staging: Path, *, training: bool = False, include_test: bool = True) -> dict:
    """Fail-closed validation for the private R4 package; no model is loaded."""
    staging = external(staging)
    admission_path = checked_file(staging, "admission.json", 100_000)
    admission = R4Admission.model_validate_json(admission_path.read_bytes())
    expected_related = {"previous-r3-manifest.json"}
    if admission.cross_domain_manifest_sha256 is not None:
        expected_related.add("cross-domain-manifest.json")
    if set(admission.related_manifests) != expected_related or any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in admission.related_manifests.values()
    ):
        raise ValueError("r4_related_metadata_pins_invalid")
    preparation_path = checked_file(staging, "preparation.json")
    if digest_file(preparation_path) != admission.preparation_sha256:
        raise ValueError("r4_preparation_hash_mismatch")
    preparation = json.loads(preparation_path.read_text())
    if (
        preparation.get("version") != PREPARATION_VERSION
        or preparation.get("status") != "owner_approved_local_research"
    ):
        raise ValueError("r4_preparation_not_approved_or_wrong_version")
    for name, expected in preparation.get("source_code_sha256", {}).items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or digest_file(path) != expected:
            raise ValueError("r4_preparation_source_code_changed")
    pins = {
        "manifest.json": admission.manifest_sha256,
        "writers.json": admission.registry_sha256,
        "previous-writer-registry.json": admission.previous_registry_sha256,
    }
    pins.update(admission.related_manifests)
    for name, expected in pins.items():
        path = checked_file(staging, name)
        if digest_file(path) != expected:
            raise ValueError("r4_metadata_pin_mismatch")
    mapping_path = checked_file(staging, "study.jsonl")
    if digest_file(mapping_path) != admission.mapping_sha256:
        raise ValueError("r4_mapping_pin_mismatch")
    cross_path = checked_file(staging, "cross-domain-study.jsonl")
    if digest_file(cross_path) != admission.cross_domain_mapping_sha256:
        raise ValueError("r4_cross_domain_mapping_pin_mismatch")
    manifest = DatasetManifest.model_validate_json(
        checked_file(staging, "manifest.json").read_bytes()
    )
    cross_manifest_path = staging / "cross-domain-manifest.json"
    if admission.cross_domain_manifest_sha256 is None:
        if (
            cross_manifest_path.is_symlink()
            or cross_manifest_path.exists()
            or ("cross-domain-manifest.json" in admission.related_manifests)
        ):
            raise ValueError("r4_unadmitted_cross_domain_manifest")
        cross_manifest = None
    else:
        cross_manifest_path = checked_file(staging, "cross-domain-manifest.json")
        if digest_file(cross_manifest_path) != admission.cross_domain_manifest_sha256:
            raise ValueError("r4_cross_domain_manifest_pin_mismatch")
        cross_manifest = DatasetManifest.model_validate_json(cross_manifest_path.read_bytes())
    registry = WriterRegistry.model_validate_json(
        checked_file(staging, "writers.json").read_bytes()
    )
    previous_registry = WriterRegistry.model_validate_json(
        checked_file(staging, "previous-writer-registry.json").read_bytes()
    )
    if manifest.dataset_id != "nist-sd19-english-letters-r4" or (
        cross_manifest is not None
        and cross_manifest.dataset_id != "nist-sd19-english-letters-r4-hsf4"
    ):
        raise ValueError("r4_manifest_dataset_id_mismatch")
    if manifest.writer_registry_ref != f"{registry.registry_id}:{registry.version}" or (
        cross_manifest is not None
        and cross_manifest.writer_registry_ref != manifest.writer_registry_ref
    ):
        raise ValueError("r4_registry_reference_mismatch")
    # The frozen old stage is copied as metadata and its exact mapping hash is
    # pinned in preparation.json; writer IDs are independently checked below.
    previous_manifest = DatasetManifest.model_validate_json(
        checked_file(staging, "previous-r3-manifest.json").read_bytes()
    )
    previous_mapping_path = checked_file(staging, "previous-r3-study.jsonl")
    if digest_file(previous_mapping_path) != admission.previous_mapping_sha256:
        raise ValueError("r4_previous_mapping_pin_mismatch")
    previous_rows = [json.loads(line) for line in previous_mapping_path.read_text().splitlines()]
    if sha(previous_mapping_path.read_bytes()) != preparation.get("old_r3_mapping_sha256"):
        raise ValueError("r4_previous_mapping_preparation_mismatch")
    previous_writers = {row["writer"] for row in previous_rows}
    if previous_writers != {sample.writer_id for sample in previous_manifest.samples}:
        raise ValueError("r4_previous_mapping_manifest_writer_mismatch")
    if canonical(sorted(previous_writers)) != preparation.get(
        "old_r3_writer_exclusion_hash"
    ) or len(previous_writers) != preparation.get("old_r3_writers_excluded"):
        raise ValueError("r4_previous_writer_exclusion_metadata_mismatch")
    primary_rows = [json.loads(line) for line in mapping_path.read_text().splitlines()]
    cross_rows = [json.loads(line) for line in cross_path.read_text().splitlines()]
    all_rows = [*primary_rows, *cross_rows]
    cross_ids = {row["id"] for row in cross_rows}
    if not primary_rows or len({row["id"] for row in all_rows}) != len(all_rows):
        raise ValueError("r4_empty_or_duplicate_study_rows")
    old_writer_groups = {
        sample.writer_group_id for sample in previous_manifest.samples if sample.writer_group_id
    }
    old_writer_ids = {sample.writer_id for sample in previous_manifest.samples}
    old_content_hashes = {row["hash"] for row in previous_rows}
    old_pixel_hashes = {row["pixel_hash"] for row in previous_rows}
    observed: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_split_label_writer: set[tuple[str, str, str]] = set()
    selected_pixel_hashes: set[str] = set()
    for row in all_rows:
        split = row["split"]
        if split not in {"train", "validation", "test"} or row["label"] not in LETTERS:
            raise ValueError("r4_unexpected_split_or_label")
        parsed = nist.writer_parts(row.get("source_member", ""))
        if not parsed:
            raise ValueError("r4_source_member_not_a_nist_writer_image")
        partition, raw_writer, form, field = parsed
        if (
            partition != row["source_partition"]
            or nist_writer_id(raw_writer) != row["writer"]
            or row["id"] != "r4-" + sha(row["source_member"].encode())[:24]
            or row["source_group"] != "nist-page-" + sha((raw_writer + ":" + form).encode())[:24]
            or field not in {"u", "l"}
            or (field == "u") != row["label"].isupper()
        ):
            raise ValueError("r4_source_provenance_mapping_mismatch")
        if row["path"] != f"images/{row['hash']}.png":
            raise ValueError("r4_image_reference_not_content_addressed")
        image_path = staging / row["path"]
        if (
            image_path.is_symlink()
            or not image_path.resolve().is_relative_to(staging)
            or not image_path.is_file()
        ):
            raise ValueError("r4_image_missing_or_unsafe")
        if digest_file(image_path) != row["hash"]:
            raise ValueError("r4_image_content_hash_mismatch")
        if row["hash"] in old_content_hashes or row["pixel_hash"] in old_pixel_hashes:
            raise ValueError("r4_previous_study_content_overlap")
        if row["pixel_hash"] in selected_pixel_hashes:
            raise ValueError("r4_duplicate_pixel_in_selected_mapping")
        selected_pixel_hashes.add(row["pixel_hash"])
        if row["writer"] in old_writer_ids:
            raise ValueError("r4_reused_old_study_writer_id")
        split_label_writer = (row["split"], row["label"], row["writer"])
        if split_label_writer in by_split_label_writer:
            raise ValueError("r4_writer_reused_for_same_letter_and_split")
        by_split_label_writer.add(split_label_writer)
        for key in ("writer", "source_group", "hash", "pixel_hash"):
            observed[(key, row[key])].add("cross_test" if row["id"] in cross_ids else split)
        group = "nist-sd19-" + row["writer"][5:]
        if group in old_writer_groups:
            raise ValueError("r4_reused_old_study_writer")
        if (row["source_partition"] == SCHOOL_PARTITION) != (row["id"] in cross_ids):
            raise ValueError("r4_writer_domain_partition_mismatch")
    if any(len(parts) > 1 for parts in observed.values()):
        raise ValueError("r4_cross_split_writer_source_or_duplicate_leakage")
    manifest_pairs = [(primary_rows, manifest)]
    if cross_manifest is not None:
        if not cross_rows:
            raise ValueError("r4_cross_domain_manifest_without_rows")
        manifest_pairs.append((cross_rows, cross_manifest))
    elif cross_rows:
        raise ValueError("r4_cross_domain_rows_without_manifest")
    for rows, target in manifest_pairs:
        sample_by_id = {sample.sample_id: sample for sample in target.samples}
        sample_ids = set(sample_by_id)
        if sample_ids != {row["id"] for row in rows}:
            raise ValueError("r4_manifest_mapping_disagreement")
        if target.content_hash != "sha256:" + sha(_jsonl(rows)):
            raise ValueError("r4_manifest_content_hash_disagreement")
        for row in rows:
            sample = sample_by_id[row["id"]]
            if (
                sample.writer_id != row["writer"]
                or sample.writer_group_id != "nist-sd19-" + row["writer"][5:]
                or sample.split != row["split"]
                or sample.content_hash != "sha256:" + row["hash"]
                or sample.source_group_id != row["source_group"]
                or sample.duplicate_group_id != "dup-" + row["pixel_hash"][:32]
            ):
                raise ValueError("r4_manifest_sample_mapping_mismatch")
    current_registry_entries = {
        (item.dataset_id, item.writer_id, item.writer_group_id) for item in registry.identities
    }
    previous_registry_entries = {
        (item.dataset_id, item.writer_id, item.writer_group_id)
        for item in previous_registry.identities
    }
    if not previous_registry_entries.issubset(current_registry_entries):
        raise ValueError("r4_registry_does_not_preserve_previous_identities")
    # Validate both R4 datasets with the prior English manifest in the shared global registry.
    # The cross-domain manifest is related to the primary manifest, so validate
    # together. Existing repository manifests remain part of the global check.
    repository_manifests = [
        record
        for path in sorted((ROOT / "research/manifests").rglob("*.yaml"))
        if path.is_file()
        if isinstance(record := load_record(path), DatasetManifest)
    ]
    candidate_manifests = [manifest, *([cross_manifest] if cross_manifest is not None else [])]
    global_validation = validate_datasets(
        [*repository_manifests, previous_manifest, *candidate_manifests], registry
    )
    assert_build_eligible(
        candidate_manifests,
        "research_training" if training else "research_evaluation",
    )
    counts = {
        split: {
            "samples": sum(row["split"] == split for row in primary_rows),
            "writers": len({row["writer"] for row in primary_rows if row["split"] == split}),
            "per_letter": {
                letter: sum(
                    row["split"] == split and row["label"] == letter for row in primary_rows
                )
                for letter in LETTERS
            },
        }
        for split in PRIMARY_SPLITS
    }
    if any(counts[split]["samples"] == 0 for split in ("train", "validation")):
        raise ValueError("r4_required_split_empty")
    if any(
        counts[split]["per_letter"][letter] < 16
        for split in ("validation", "test")
        for letter in LETTERS
    ):
        raise ValueError("r4_evaluation_missing_case_sensitive_class_coverage")
    if any(counts["train"]["per_letter"][letter] < 1 for letter in LETTERS):
        raise ValueError("r4_training_missing_case_sensitive_class_coverage")
    if cross_manifest is not None and any(
        sum(row["label"] == letter for row in cross_rows) < 16 for letter in LETTERS
    ):
        raise ValueError("r4_cross_domain_missing_case_sensitive_class_coverage")
    selected = {
        "train": [row for row in primary_rows if row["split"] == "train"],
        "validation": [row for row in primary_rows if row["split"] == "validation"],
    }
    if include_test:
        selected["test"] = [row for row in primary_rows if row["split"] == "test"]
        if cross_manifest is not None:
            selected["cross_test"] = cross_rows
    return {
        "status": "admitted_inputs_verified_only",
        "preparation": preparation,
        "partitions": counts,
        "selected": selected,
        "cross_domain": {
            "samples": len(cross_rows),
            "writers": len({row["writer"] for row in cross_rows}),
        },
        "global_dataset_validation": global_validation,
        "training_permission": "research_training" in manifest.license.permitted_uses,
        "model_inference": False,
        "training": "not_run",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(audit(args.sources, args.baseline, args.destination), indent=2))


if __name__ == "__main__":
    main()
