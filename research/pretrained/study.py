"""Metadata, text encoding and source-integrity preparation; no model execution."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research.preparation.check_study import check
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["trocr-transfer-r2-v2"]
    seed: int = Field(ge=0, le=2**32 - 1)
    capability_status: Literal["research_only"]
    source_study: Literal["local-r1"]
    train_per_writer: int = Field(ge=1, le=1500)
    validation_per_writer: int = Field(ge=1, le=1500)
    max_label_tokens: int = Field(ge=4, le=510)
    max_steps: int = Field(ge=1, le=1000)
    accumulate_steps: int = Field(ge=1, le=16)
    learning_rate: float = Field(gt=0, le=0.001)
    weight_decay: float = Field(ge=0, le=1)
    clip_grad_norm: float = Field(gt=0, le=10)
    trainable: Literal["last_two_decoder_layers_and_token_embedding_output_projection"]
    training_objective: Literal["teacher_forced_aligned_token_cross_entropy_v1"]
    image_preprocessing: Literal["pinned_upstream_vit_rgb_resize_384_v1"]
    decoding: Literal["greedy_no_lexicon_no_language_correction"]
    threads: int = Field(ge=1, le=4)
    max_wall_seconds: int = Field(ge=1, le=3600)
    max_cpu_seconds: int = Field(ge=1, le=7200)
    max_rss_mb: int = Field(ge=2000, le=8000)
    max_output_bytes: int = Field(ge=1400000000, le=3000000000)
    test_policy: Literal["r1_test_writers_previously_exposed_reused_comparison_only"]
    pretraining_overlap: Literal[
        "reported_iam_finetuning_original_pretraining_membership_not_independently_audited"
    ]


def load_recipe(path: Path) -> Recipe:
    return Recipe.model_validate_json(path.read_bytes())


def subset(rows: list[dict], split: str, per_writer: int | None, seed: int) -> list[dict]:
    eligible = [r for r in rows if r["split"] == split and r["recognition"]]
    chosen = []
    for writer in sorted({r["writer"] for r in eligible}):
        group = sorted(
            (r for r in eligible if r["writer"] == writer),
            key=lambda r: canonical_hash([seed, r["id"]]),
        )
        chosen.extend(group if per_writer is None else group[:per_writer])
    if not chosen:
        raise ValueError("recognition_partition_empty")
    return chosen


def source_path(staging: Path, row: dict) -> Path:
    corpus = (staging / "iiit-hw-dev-inner").resolve()
    raw = corpus / row["path"]
    resolved = raw.resolve(strict=True)
    if raw.is_symlink() or not resolved.is_relative_to(corpus) or not resolved.is_file():
        raise ValueError("staged_source_must_be_regular_inside_corpus")
    return resolved


def prepare(staging: Path, recipe: Recipe, *, include_test: bool = False) -> tuple[dict, dict]:
    readiness = check(staging, ROOT / "research/configs/local-r1.json")
    rows = [json.loads(line) for line in (staging / "study-r1.jsonl").read_text().splitlines()]
    selected = {
        "train": subset(rows, "train", recipe.train_per_writer, recipe.seed),
        "validation": subset(rows, "validation", recipe.validation_per_writer, recipe.seed),
    }
    if include_test:
        selected["test"] = subset(rows, "test", None, recipe.seed)
    for group in selected.values():
        for row in group:
            if digest_file(source_path(staging, row)) != row["hash"]:
                raise ValueError("staged_source_hash_changed")
    metadata = {
        "source_study": readiness,
        "recipe": recipe.model_dump(),
        "recipe_hash": canonical_hash(recipe.model_dump()),
        "partitions": {
            split: {
                "n": len(group),
                "writers": len({r["writer"] for r in group}),
                "sample_ids": [r["id"] for r in group],
                "selection_hash": canonical_hash([r["id"] for r in group]),
            }
            for split, group in selected.items()
        },
        "selected_source_bytes_rehashed": True,
    }
    # Vocabulary is a diagnostic stratum only; it never restricts model decoding.
    vocabulary = Counter(r["label"] for r in rows if r["split"] == "train" and r["recognition"])
    return {"selected": selected, "vocabulary": vocabulary}, metadata


def encode_label(tokenizer, label: str, maximum: int) -> list[int]:
    ids = tokenizer.encode(label, add_special_tokens=True, truncation=False)
    if not ids or len(ids) > maximum:
        raise ValueError("label_encoding_too_long_no_truncation_allowed")
    if tokenizer.unk_token_id in ids:
        raise ValueError("label_encoding_contains_unknown_token")
    if any(i in tokenizer.all_special_ids for i in ids[1:-1]):
        raise ValueError("label_encoding_contains_embedded_special_token")
    decoded = tokenizer.decode(ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    if decoded != label:
        raise ValueError("label_encoding_not_lossless")
    return ids


def tokenizer_for(bundle: Path):
    # Loading text vocab/merges does not load weights, infer or fit a model.
    from transformers import RobertaTokenizer

    return RobertaTokenizer(
        vocab_file=str(bundle / "vocab.json"),
        merges_file=str(bundle / "merges.txt"),
        add_prefix_space=False,
        clean_up_tokenization_spaces=False,
    )


def encoding_report(tokenizer, selected: dict, maximum: int) -> dict:
    counts = {}
    for split, rows in selected.items():
        lengths = [len(encode_label(tokenizer, r["label"], maximum)) for r in rows]
        counts[split] = {"n": len(lengths), "max_tokens": max(lengths), "lossless": True}
    return counts


def source_inventory() -> dict:
    paths = list((ROOT / "research/pretrained").glob("*.py"))
    paths += [
        ROOT / p
        for p in (
            "research/evaluation/local_baselines.py",
            "research/evaluation/deva_recognition.py",
            "research/evaluation/harness.py",
            "research/preparation/check_study.py",
            "research/preparation/freeze.py",
            "research/preparation/archive_audit.py",
            "research/models.py",
            "research/validate.py",
            "pyproject.toml",
            "uv.lock",
            "scripts/run_pretrained_research.sh",
            "scripts/train_pretrained_research.sh",
        )
    ]
    paths += list((ROOT / "packages/contracts/penforge_contracts").glob("unicode*.py"))
    paths += [ROOT / "packages/contracts/unicode/17.0.0/tables.json"]
    return {str(p.relative_to(ROOT)): digest_file(p) for p in sorted(set(paths)) if p.is_file()}
