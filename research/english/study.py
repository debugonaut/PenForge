"""English study admission and reproducibility checks; no neural execution."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research.english.acquire import SOURCES
from research.english.cvl import SOURCE as CVL_SOURCE
from research.english.nist import LETTERS
from research.models import DatasetManifest, WriterRegistry
from research.preparation.archive_audit import external
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file
from research.validate import assert_build_eligible, digest, load_record, validate_datasets

Task = Literal["letters", "words"]


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["english-trocr-r3-v1"] = "english-trocr-r3-v1"
    task: Task
    capability_status: Literal["research_only"] = "research_only"
    seed: int = Field(default=20261021, ge=0, le=2**32 - 1)
    max_label_tokens: int = Field(default=64, ge=4, le=64)
    max_steps: int = Field(default=100, ge=1, le=100)
    accumulate_steps: int = Field(default=4, ge=1, le=4)
    learning_rate: float = Field(default=0.00005, gt=0, le=0.00005)
    weight_decay: float = Field(default=0.01, ge=0, le=1)
    clip_grad_norm: float = Field(default=1.0, gt=0, le=1)
    trainable: Literal["last_two_decoder_layers_and_token_embedding_output_projection"] = (
        "last_two_decoder_layers_and_token_embedding_output_projection"
    )
    training_objective: Literal["teacher_forced_aligned_token_cross_entropy_v1"] = (
        "teacher_forced_aligned_token_cross_entropy_v1"
    )
    image_preprocessing: Literal["pinned_upstream_vit_rgb_resize_384_v1"] = (
        "pinned_upstream_vit_rgb_resize_384_v1"
    )
    decoding: Literal["greedy_no_lexicon_no_language_correction"] = (
        "greedy_no_lexicon_no_language_correction"
    )
    threads: int = Field(default=2, ge=1, le=2)
    max_wall_seconds: int = Field(default=1800, ge=1, le=1800)
    max_cpu_seconds: int = Field(default=3600, ge=1, le=3600)
    max_rss_mb: int = Field(default=6000, ge=2000, le=6000)
    max_output_bytes: int = Field(default=2000000000, ge=1400000000, le=2000000000)
    test_policy: Literal["separate_owner_run_after_frozen_validation_comparison"] = (
        "separate_owner_run_after_frozen_validation_comparison"
    )
    pretraining_overlap: Literal["original_pretraining_membership_not_independently_audited"] = (
        "original_pretraining_membership_not_independently_audited"
    )


def load_recipe(path: Path, task: Task) -> Recipe:
    recipe = Recipe.model_validate_json(path.read_bytes())
    if recipe.task != task:
        raise ValueError("english_recipe_task_mismatch")
    return recipe


class Admission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["english-admission-r3-v1"]
    task: Task
    preparation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    related_manifests: dict[str, str] = Field(default_factory=dict)
    # Written only after actual-file review; the preparer never manufactures this.
    duplicate_review: Literal["reviewed_exact_and_nonexact"]
    duplicate_review_ref: str = Field(min_length=12, max_length=500)
    language_and_label_review_ref: str = Field(min_length=12, max_length=500)


def checked_file(folder: Path, name: str, ceiling: int = 32_000_000) -> Path:
    raw = folder / name
    if raw.is_symlink() or not raw.is_file() or not raw.resolve().is_relative_to(folder):
        raise ValueError("english_missing_audited_file_or_unsafe_reference")
    if raw.stat().st_size > ceiling:
        raise ValueError("english_metadata_or_image_size_ceiling")
    return raw


def source_path(staging: Path, row: dict) -> Path:
    staging = external(staging)
    if row["path"] != f"images/{row['hash']}.png":
        raise ValueError("english_image_reference_must_be_content_addressed_png")
    return checked_file(staging, row["path"], 8_000_000)


def validate_label(label: str, task: Task):
    if (
        not isinstance(label, str)
        or not label
        or len(label) > 128
        or any(char.isspace() or unicodedata.category(char).startswith("C") for char in label)
    ):
        raise ValueError("english_crop_requires_nonempty_word_or_letter_without_controls")
    if task == "letters":
        if len(label) != 1 or label not in LETTERS:
            raise ValueError("english_letter_study_requires_single_a_z_case_preserved")
    elif not any(char.isalnum() for char in label) or any(
        char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in label
    ):
        raise ValueError("english_word_contains_unreviewed_script_or_no_word_characters")


def prepare(staging: Path, recipe: Recipe, *, training: bool = False, include_test: bool = False):
    staging = external(staging)
    admission_path = checked_file(staging, "admission.json", 100_000)
    admission = Admission.model_validate_json(admission_path.read_bytes())
    if admission.task != recipe.task:
        raise ValueError("english_admission_task_mismatch")
    pins = {
        "preparation.json": admission.preparation_sha256,
        "study.jsonl": admission.mapping_sha256,
        "manifest.json": admission.manifest_sha256,
        "writers.json": admission.registry_sha256,
    }
    for name, checksum in admission.related_manifests.items():
        if (
            name in {*pins, "admission.json"}
            or len(checksum) != 64
            or any(char not in "0123456789abcdef" for char in checksum)
        ):
            raise ValueError("english_invalid_related_manifest_pin")
        pins[name] = checksum
    for name, expected in pins.items():
        if digest_file(checked_file(staging, name)) != expected:
            raise ValueError("english_admitted_metadata_changed")
    preparation = json.loads((staging / "preparation.json").read_text())
    expected_version = (
        "nist-letter-preparation-r3-v1"
        if recipe.task == "letters"
        else "cvl-word-preparation-r3-v1"
    )
    if preparation.get("version") != expected_version:
        raise ValueError("english_preparation_task_or_adapter_version_mismatch")
    expected_sources = (
        {name: SOURCES[name] for name in ("by_write.zip", "by_class.zip")}
        if recipe.task == "letters"
        else {CVL_SOURCE[0]: CVL_SOURCE[1:]}
    )
    for name, (size, md5) in expected_sources.items():
        receipt = preparation.get("sources", {}).get(name, {})
        if receipt.get("bytes") != size or receipt.get("md5") != md5:
            raise ValueError("english_preparation_requires_pinned_source_receipts")
    manifest = DatasetManifest.model_validate_json((staging / "manifest.json").read_bytes())
    registry = WriterRegistry.model_validate_json((staging / "writers.json").read_bytes())
    previous_path = ROOT / "research/manifests/writers-v1.yaml"
    previous = WriterRegistry.model_validate_json(previous_path.read_bytes())
    if digest_file(previous_path) != admission.previous_registry_sha256:
        raise ValueError("english_previous_registry_snapshot_changed")
    previous_entries = {digest(entry) for entry in previous.identities}
    if (
        registry.registry_id != previous.registry_id
        or registry.version == previous.version
        or not previous_entries.issubset({digest(entry) for entry in registry.identities})
    ):
        raise ValueError("english_registry_must_extend_frozen_identities_in_new_version")
    if manifest.writer_registry_ref != f"{registry.registry_id}:{registry.version}":
        raise ValueError("english_manifest_registry_reference_changed")
    if manifest.source_kind != "licensed_public" or manifest.script_codes != ("Latn",):
        raise ValueError("english_study_requires_reviewed_public_latin_data")
    if manifest.content_hash != "sha256:" + admission.mapping_sha256:
        raise ValueError("english_manifest_must_pin_exact_private_mapping")
    assert_build_eligible([manifest], "research_training" if training else "research_evaluation")
    others = [
        record
        for path in sorted((ROOT / "research/manifests").rglob("*.yaml"))
        if isinstance(record := load_record(path), DatasetManifest)
    ]
    others += [
        DatasetManifest.model_validate_json(checked_file(staging, name).read_bytes())
        for name in admission.related_manifests
    ]
    global_check = validate_datasets([*others, manifest], registry)
    rows = [json.loads(line) for line in (staging / "study.jsonl").read_text().splitlines()]
    if not rows or len(rows) > 6000 or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("english_empty_duplicate_or_unbounded_study")
    samples = {sample.sample_id: sample for sample in manifest.samples}
    if set(samples) != {row["id"] for row in rows}:
        raise ValueError("english_mapping_must_match_admitted_sample_inventory")
    observed: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        sample = samples[row["id"]]
        label = row["label"]
        validate_label(label, recipe.task)
        if (
            row["split"] != sample.split
            or row["writer"] != sample.writer_id
            or "sha256:" + row["hash"] != sample.content_hash
            or row["source_group"] != sample.source_group_id
        ):
            raise ValueError("english_mapping_differs_from_admitted_provenance")
        for key in ("writer", "source_group", "hash", "pixel_hash"):
            observed.setdefault((key, row[key]), set()).add(row["split"])
        if digest_file(source_path(staging, row)) != row["hash"]:
            raise ValueError("english_source_bytes_changed")
    if any(len(splits) != 1 for splits in observed.values()):
        raise ValueError("english_writer_source_or_duplicate_leakage")
    selected = {
        split: [r for r in rows if r["split"] == split] for split in ("train", "validation", "test")
    }
    for split, maximum in (("train", 4096), ("validation", 256), ("test", 1024)):
        if not 1 <= len(selected[split]) <= maximum:
            raise ValueError("english_partition_missing_or_exceeds_fixed_pilot_budget")
        if recipe.task == "letters" and {r["label"] for r in selected[split]} != set(LETTERS):
            raise ValueError("english_partition_must_cover_all_52_letters")
    # This is a crop-only word study; line inputs require a separate reviewed recipe.
    vocabulary = Counter(r["label"] for r in selected["train"])
    metadata = {
        "task": recipe.task,
        "unicode_database_version": unicodedata.unidata_version,
        "admission_sha256": digest_file(admission_path),
        "admitted_files": pins,
        "previous_registry_sha256": admission.previous_registry_sha256,
        "global_dataset_validation": global_check,
        "partitions": {
            split: {
                "n": len(group),
                "writers": len({r["writer"] for r in group}),
                "selection_hash": canonical_hash([r["id"] for r in group]),
            }
            for split, group in selected.items()
        },
        "test_images_used_for_model_selection": False,
    }
    if not include_test:
        del selected["test"]
    return {"selected": selected, "vocabulary": vocabulary}, metadata


def source_inventory() -> dict:
    paths = [
        *(ROOT / "research/english").glob("*.py"),
        *(ROOT / "research/pretrained").glob("*.py"),
    ]
    paths += [
        ROOT / name
        for name in (
            "research/models.py",
            "research/validate.py",
            "research/evaluation/local_baselines.py",
            "research/evaluation/deva_recognition.py",
            "research/preparation/archive_audit.py",
            "research/preparation/freeze.py",
            "research/preparation/check_study.py",
            "research/evaluation/harness.py",
            "research/fetch_deva_datasets.py",
            "pyproject.toml",
            "uv.lock",
            "scripts/train_english_research.sh",
            "scripts/run_english_research.sh",
            "scripts/prepare_english_research.sh",
            "research/configs/english-r3-letters.json",
            "research/configs/english-r3-words.json",
        )
    ]
    paths += list((ROOT / "research/manifests").rglob("*.yaml"))
    paths += list((ROOT / "packages/contracts/penforge_contracts").glob("unicode*.py"))
    paths += [ROOT / "packages/contracts/unicode/17.0.0/tables.json"]
    return {str(path.relative_to(ROOT)): digest_file(path) for path in sorted(set(paths))}
