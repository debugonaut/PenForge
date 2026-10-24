"""Synthetic, framework-free R6 fixtures. This module defines no test cases.

Everything here is inert: 128x128 solid-crop PNGs, tiny numpy tensors written as
safetensors, and JSON metadata in the exact shapes the frozen R5 composition uses. No
pretrained model, real dataset row or Torch tensor is involved, so these fixtures can also
be imported by the subprocess that proves metadata preflight never loads a model.
"""

from __future__ import annotations

import hashlib
import io
import json
import string
from pathlib import Path

import numpy as np
from PIL import Image
from safetensors.numpy import save_file

from research.pretrained.artifacts import canonical_hash, digest_file

CLASSES: tuple[str, ...] = tuple(string.ascii_uppercase + string.ascii_lowercase)
VOCAB = 64
DECODER_LAYERS = 3
PNG_SIZE = (128, 128)
REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_CONFIG = {
    "model_type": "vision-encoder-decoder",
    "encoder": {"model_type": "vit"},
    "decoder": {
        "model_type": "trocr",
        "decoder_layers": DECODER_LAYERS,
        "decoder_start_token_id": 2,
        "eos_token_id": 2,
        "pad_token_id": 1,
    },
    "tie_word_embeddings": False,
}


def png_bytes(class_index: int, salt: int) -> bytes:
    """A uniform field carrying the class index, plus one marker pixel for uniqueness.

    The synthetic processor reads the median gray level, so the class index survives the
    bounded affine transform (whose white fill only affects edges) and the pad/resize path.
    """
    image = Image.new("L", PNG_SIZE, class_index)
    image.putpixel((1 + salt % 126, 1 + (salt * 7) % 126), 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pixel_hash(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(image.convert("L").tobytes()).hexdigest()


def file_inventory(folder: Path) -> dict[str, str]:
    return {item.name: digest_file(item) for item in sorted(folder.iterdir())}


def build_staging(root: Path) -> Path:
    staging = root / "stage"
    (staging / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for index, label in enumerate(CLASSES):
        for offset, (split, writer) in enumerate((("train", "w-train"), ("validation", "w-val"))):
            salt = index * 2 + offset + 2
            data = png_bytes(index, salt)
            digest = hashlib.sha256(data).hexdigest()
            (staging / "images" / f"{digest}.png").write_bytes(data)
            rows.append(
                {
                    "id": f"r6-{label}-{split}",
                    "hash": digest,
                    "path": f"images/{digest}.png",
                    "pixel_hash": pixel_hash(data),
                    "label": label,
                    "writer": writer,
                    "source_group": f"nist-page-{salt}",
                    "source_partition": 0,
                    "split": split,
                    "source_member": f"by_write/hsf_0/w{salt}/u{salt}_{salt}.png",
                }
            )
    (staging / "train-validation.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (staging / "train.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows if row["split"] == "train"),
        encoding="utf-8",
    )
    return staging


def build_base(root: Path) -> tuple[Path, dict[str, np.ndarray]]:
    base = root / "base"
    base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(BASE_CONFIG), encoding="utf-8")
    (base / "generation_config.json").write_text(
        json.dumps({"bos_token_id": 2, "eos_token_id": 2, "pad_token_id": 1}),
        encoding="utf-8",
    )
    tensors: dict[str, np.ndarray] = {
        "encoder.weight": np.zeros((1, 1), dtype=np.float32),
        "decoder.model.decoder.embed_tokens.weight": (
            np.arange(VOCAB * 6, dtype=np.float32).reshape(VOCAB, 6) / 1000.0
        ),
        "decoder.model.decoder.embed_positions.weight": np.zeros((8, 6), dtype=np.float32),
    }
    for layer in range(DECODER_LAYERS):
        tensors[f"decoder.model.decoder.layers.{layer}.weight"] = np.full(
            (6, 6), 0.05 * (layer + 1), dtype=np.float32
        )
        tensors[f"decoder.model.decoder.layers.{layer}.bias"] = np.zeros((6,), dtype=np.float32)
    save_file(tensors, str(base / "model.safetensors"))
    return base, tensors


def trainable_keys() -> list[str]:
    keys = ["decoder.model.decoder.embed_tokens.weight"]
    for layer in range(DECODER_LAYERS - 2, DECODER_LAYERS):
        keys.append(f"decoder.model.decoder.layers.{layer}.weight")
        keys.append(f"decoder.model.decoder.layers.{layer}.bias")
    return keys


def build_delta(root: Path, base: Path) -> Path:
    """A trainable-parameter delta in the frozen ``english-r5-selected-state-v1`` shape."""
    delta = root / "delta"
    delta.mkdir()
    tensors = {}
    for index, name in enumerate(trainable_keys()):
        shape = (
            (VOCAB, 6)
            if name.endswith("embed_tokens.weight")
            else ((6,) if name.endswith("bias") else (6, 6))
        )
        tensors["model." + name] = np.full(shape, 0.01 * (index + 1), dtype=np.float32)
    save_file(tensors, str(delta / "state.safetensors"))
    inventory = {
        name: {
            "dtype": "float32",
            "shape": [int(size) for size in tensors["model." + name].shape],
        }
        for name in trainable_keys()
    }
    state = {
        "version": "english-r5-selected-state-v1",
        "status": "pass_complete",
        "weights_only": True,
        "lineage_hash": "f" * 64,
        "recipe_hash": "e" * 64,
        "bundle_hash": "d" * 64,
        "init_checkpoint_sha256": canonical_hash(file_inventory(base)),
        "pass_index": 3,
        "source_state_sha256": "c" * 64,
        "trainable_inventory": inventory,
        "tensor_count": len(tensors),
        "tensors_sha256": digest_file(delta / "state.safetensors"),
    }
    (delta / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return delta


def build_winner(root: Path, base: Path, delta: Path) -> Path:
    """The frozen R5 run tree: its root holds the winner and, in reality, all R5 evidence."""
    run = root / "r5-run"
    run.mkdir()
    lineage = {
        "initialization_adapter": {
            "derived_path": str(base.resolve()),
            "derived_files": file_inventory(base),
        },
        "model_lineage_hash": "f" * 64,
    }
    winner = {
        "version": "english-r5-frozen-winner-v1",
        "status": "frozen_before_final_test",
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "winner": {
            "name": "r5_pass_0003",
            "kind": "r5_trainable_delta",
            "checkpoint_path": str(delta.resolve()),
            "checkpoint_files": file_inventory(delta),
        },
        "test_evaluated": False,
    }
    path = run / "winner-frozen.json"
    path.write_text(json.dumps(winner, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_bundle(root: Path, base: Path) -> Path:
    bundle = root / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text(json.dumps(BASE_CONFIG), encoding="utf-8")
    (bundle / "model.safetensors").write_bytes((base / "model.safetensors").read_bytes())
    return bundle


def build_recipe(root: Path, *, pass_limit: int = 2, name: str = "recipe.json") -> Path:
    raw = json.loads(
        (REPO_ROOT / "research/configs/english-r6-model-continuation.json").read_text()
    )
    raw.update(
        pass_limit=pass_limit,
        max_pass_wall_seconds=120,
        max_validation_wall_seconds=120,
        max_run_wall_seconds=600,
        max_cpu_seconds=900,
        max_rss_mb=2000,
        max_output_bytes=200_000_000,
        interrupted_attempt_charge_seconds=120,
    )
    path = root / name
    path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return path


def fake_verifier(_path: Path) -> dict:
    return {"model_id": "fixture", "revision": "fixture", "files": {}, "scope": "fixture"}


def build_case(root: Path, *, pass_limit: int = 2) -> dict:
    """Build the complete synthetic input set outside the repository checkout."""
    root.mkdir(parents=True, exist_ok=True)
    staging = build_staging(root)
    base, base_tensors = build_base(root)
    delta = build_delta(root, base)
    winner = build_winner(root, base, delta)
    bundle = build_bundle(root, base)
    recipe = build_recipe(root, pass_limit=pass_limit)
    return {
        "root": root,
        "r5_run": winner.parent,
        "staging": staging,
        "base": base,
        "base_tensors": base_tensors,
        "delta": delta,
        "winner": winner,
        "bundle": bundle,
        "recipe": recipe,
        "expected_winner_sha256": digest_file(winner),
        "expected_delta_sha256": digest_file(delta / "state.safetensors"),
    }


def build_plan(case: dict, output: Path, *, recipe: Path | None = None):
    from research.english.r6_model import load_plan

    return load_plan(
        staging=case["staging"],
        mapping_name="train-validation.jsonl",
        model_bundle=case["bundle"],
        init_checkpoint=case["base"],
        init_delta=case["delta"],
        r5_winner=case["winner"],
        output=output,
        recipe_path=recipe if recipe is not None else case["recipe"],
    )
