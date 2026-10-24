"""Data-only verification of the frozen R6 pass-2 winner and its composition.

Nothing here imports Torch, Transformers or Safetensors: it parses JSON, checks SHA-256
digests and reads bounded safetensors headers. The selected model is reconstructed from
the pinned R5 canonical base plus the R5 selected delta *plus* the selected R6 delta, in
that order. There is no fallback: a changed winner document, changed bundle, changed base,
changed R5 delta, changed R6 delta or a candidate that is not pass 2 aborts the run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.english.r5_data.common import canonical_hash
from research.english.r6_contract import tensor_header
from research.pretrained.artifacts import ROOT, digest_file

FROZEN_RUN = ROOT / "research/runs/r6-owner-20261022-ready"
WINNER_DOCUMENT_SHA256 = "ce381259229f4329c483907fc3ca4a9ac59006905699e2aea146e67c785fd2b0"
WINNER_CHECKPOINT_SHA256 = "1c2354f8bf98fdb4a52f9463f885ab1481cdb4886c974c67ae9e33b82a2f56aa"
WINNER_NAME = "r6_continuation_r5_pass3_pass_0002"
WINNER_VERSION = "english-r6-frozen-winner-v1"
WINNER_STATUS = "frozen_validation_only_final_unavailable"
CHECKPOINT_KIND = "r6_trainable_delta"
PASS_INDEX = 2
R5_CONTROL_NAME = "r5_unchanged_control"
CHECKPOINT_DIR = "training/best"
DEFAULT_BUNDLE = Path.home() / "PenForge-data/models/trocr-base-r2"
BUNDLE_ENV = "PENFORGE_PRETRAINED_MODEL"
INFERENCE = {
    "batch_size": 8,
    "decoding": "greedy_no_lexicon_no_language_correction",
    "max_new_tokens": 8,
    "normalization": "raw_exact_case_sensitive_no_trimming_or_language_correction",
    "preprocessing": "pinned_upstream_vit_rgb_resize_384_v1",
    "seed": 20261023,
}
# Safetensors headers spell dtypes `F32`; the frozen inventory records torch names.
_SAFETENSORS_DTYPES = {
    "BOOL": "bool",
    "U8": "uint8",
    "I8": "int8",
    "I16": "int16",
    "U16": "uint16",
    "I32": "int32",
    "U32": "uint32",
    "I64": "int64",
    "U64": "uint64",
    "F16": "float16",
    "BF16": "bfloat16",
    "F32": "float32",
    "F64": "float64",
    "F8_E4M3": "float8_e4m3fn",
    "F8_E5M2": "float8_e5m2",
}
REQUIRED_INITIALIZATION_KEYS = (
    "base_checkpoint",
    "base_files",
    "base_inventory_sha256",
    "decoder_layers",
    "delta_checkpoint",
    "delta_sha256",
    "trainable_inventory",
    "trainable_tensor_count",
)


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_{label}_missing_or_unsafe")
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized_dtype(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("volunteer_tensor_dtype_must_be_text")
    return _SAFETENSORS_DTYPES.get(value, value)


def _file_hashes(directory: Path, *, expected: Mapping[str, str], label: str) -> dict[str, str]:
    """Require the exact flat file inventory and every recorded digest."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"volunteer_{label}_directory_missing_or_unsafe")
    items = sorted(directory.iterdir())
    if {item.name for item in items} != set(expected):
        raise ValueError(f"volunteer_{label}_file_inventory_changed")
    observed = {}
    for item in items:
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"volunteer_{label}_requires_regular_files")
        digest = digest_file(item)
        if digest != expected[item.name]:
            raise ValueError(f"volunteer_{label}_digest_changed:{item.name}")
        observed[item.name] = digest
    return observed


def _verified_bundle(directory: Path, *, expected: Mapping[str, str]) -> dict[str, Any]:
    """Every recorded bundle file must match; extra plain files are inert and listed."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("volunteer_pinned_trocr_bundle_directory_missing_or_unsafe")
    observed: dict[str, str] = {}
    for name, digest in expected.items():
        item = directory / name
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"volunteer_pinned_trocr_bundle_file_missing:{name}")
        actual = digest_file(item)
        if actual != digest:
            raise ValueError(f"volunteer_pinned_trocr_bundle_digest_changed:{name}")
        observed[name] = actual
    extra_files = []
    for item in sorted(directory.iterdir()):
        if item.name in expected:
            continue
        if item.is_symlink() or not item.is_file():
            raise ValueError(
                f"volunteer_pinned_trocr_bundle_extra_must_be_regular_file:{item.name}"
            )
        extra_files.append(item.name)
    return {"files": observed, "extra_files": extra_files}


def resolve_bundle(bundle: Path | None = None) -> Path:
    """The pinned tokenizer/processor bundle, verified against the frozen inventory."""
    return Path(bundle or os.environ.get(BUNDLE_ENV) or DEFAULT_BUNDLE).expanduser()


def verify_frozen_winner(
    run: Path = FROZEN_RUN,
    *,
    winner_document_sha256: str = WINNER_DOCUMENT_SHA256,
    checkpoint_sha256: str = WINNER_CHECKPOINT_SHA256,
) -> dict[str, Any]:
    """Prove the frozen winner, its lineage and the R6 pass-2 delta, without a model."""
    root = Path(run).resolve()
    document_path = root / "winner-frozen.json"
    if document_path.is_symlink() or not document_path.is_file():
        raise ValueError("volunteer_frozen_winner_document_missing")
    document_digest = digest_file(document_path)
    if document_digest != winner_document_sha256:
        raise ValueError("volunteer_frozen_winner_document_hash_changed")
    document = _read_json(document_path, "frozen_winner_document")
    if (
        document.get("version") != WINNER_VERSION
        or document.get("status") != WINNER_STATUS
        or document.get("product_qualification") != "none"
        or document.get("test_evaluated") is not False
        or document.get("fresh_final") != "unavailable_below_minimum_untouched_writer_coverage"
    ):
        raise ValueError("volunteer_frozen_winner_document_status_changed")

    lineage = document.get("lineage")
    if not isinstance(lineage, Mapping) or document.get("lineage_hash") != canonical_hash(lineage):
        raise ValueError("volunteer_frozen_winner_lineage_hash_changed")
    model_lineage = lineage.get("model_lineage")
    if not isinstance(model_lineage, Mapping) or lineage.get(
        "model_lineage_hash"
    ) != canonical_hash(model_lineage):
        raise ValueError("volunteer_frozen_winner_model_lineage_hash_changed")
    if model_lineage.get("inference") != INFERENCE:
        raise ValueError("volunteer_frozen_winner_inference_recipe_changed")

    selected = document.get("winner")
    if not isinstance(selected, Mapping):
        raise ValueError("volunteer_frozen_winner_selection_missing")
    if (
        selected.get("name") != WINNER_NAME
        or selected.get("kind") != CHECKPOINT_KIND
        or selected.get("pass_index") != PASS_INDEX
    ):
        raise ValueError("volunteer_selection_is_not_the_frozen_r6_pass_2_candidate")
    if selected.get("inference_recipe", {}).get("order_namespace") != f"pass-{PASS_INDEX:04d}":
        raise ValueError("volunteer_selection_order_namespace_changed")
    checkpoint = Path(str(selected.get("checkpoint_path"))).resolve()
    if checkpoint != (root / CHECKPOINT_DIR).resolve():
        raise ValueError("volunteer_selected_checkpoint_path_changed")
    files = selected.get("checkpoint_files")
    if not isinstance(files, Mapping) or set(files) != {"state.json", "state.safetensors"}:
        raise ValueError("volunteer_selected_checkpoint_inventory_changed")
    if selected.get("checkpoint_sha256") != checkpoint_sha256:
        raise ValueError("volunteer_selected_checkpoint_hash_changed")
    if files.get("state.safetensors") != checkpoint_sha256:
        raise ValueError("volunteer_selected_checkpoint_file_hash_changed")
    observed_files = _file_hashes(checkpoint, expected=files, label="selected_r6_checkpoint")
    if observed_files["state.safetensors"] != checkpoint_sha256:
        raise ValueError("volunteer_selected_checkpoint_bytes_changed")

    ranked = document.get("ranked_candidates")
    if not isinstance(ranked, list):
        raise ValueError("volunteer_frozen_winner_ranking_missing")
    names = [item.get("name") for item in ranked if isinstance(item, Mapping)]
    if names.count(WINNER_NAME) != 1 or names.count(R5_CONTROL_NAME) != 1:
        raise ValueError("volunteer_frozen_winner_ranking_changed")
    if names.index(WINNER_NAME) != 0:
        raise ValueError("volunteer_frozen_winner_is_not_the_ranked_winner")
    if selected.get("checkpoint_sha256") == lineage.get("initialization", {}).get("delta_sha256"):
        raise ValueError("volunteer_frozen_winner_must_differ_from_the_r5_delta")

    selection = _read_json(root / "training/selection.json", "training_selection")
    won = selection.get("winner")
    if isinstance(won, Mapping) and won.get("name") not in (None, WINNER_NAME):
        raise ValueError("volunteer_training_selection_names_a_different_candidate")
    if WINNER_NAME not in json.dumps(selection):
        raise ValueError("volunteer_training_selection_does_not_contain_the_winner")

    state = _read_json(checkpoint / "state.json", "selected_r6_state")
    initialization = model_lineage.get("initialization")
    if not isinstance(initialization, Mapping):
        raise ValueError("volunteer_frozen_winner_initialization_missing")
    missing = [key for key in REQUIRED_INITIALIZATION_KEYS if key not in initialization]
    if missing:
        raise ValueError("volunteer_frozen_winner_initialization_incomplete:" + ",".join(missing))
    inventory = initialization["trainable_inventory"]
    if (
        not isinstance(inventory, Mapping)
        or len(inventory) != initialization["trainable_tensor_count"]
    ):
        raise ValueError("volunteer_frozen_winner_trainable_inventory_changed")
    if (
        state.get("pass_index") != PASS_INDEX
        or state.get("weights_only") is not True
        or state.get("status") != "pass_complete"
        or state.get("tensors_sha256") != checkpoint_sha256
        or state.get("trainable_inventory") != inventory
        or state.get("delta_sha256") != initialization["delta_sha256"]
        or state.get("base_inventory_sha256") != initialization["base_inventory_sha256"]
    ):
        raise ValueError("volunteer_selected_r6_state_disagrees_with_frozen_lineage")

    base = Path(initialization["base_checkpoint"]).resolve()
    base_files = _file_hashes(
        base, expected=initialization["base_files"], label="r5_canonical_base"
    )
    base_header = tensor_header(base / "model.safetensors")
    delta = Path(initialization["delta_checkpoint"]).resolve()
    if delta.is_symlink() or not delta.is_dir():
        raise ValueError("volunteer_r5_delta_directory_missing_or_unsafe")
    if sorted(item.name for item in delta.iterdir()) != ["state.json", "state.safetensors"]:
        raise ValueError("volunteer_r5_delta_file_inventory_changed")
    if digest_file(delta / "state.safetensors") != initialization["delta_sha256"]:
        raise ValueError("volunteer_r5_delta_hash_changed")

    r6_header = tensor_header(checkpoint / "state.safetensors")
    expected_keys = {f"model.{name}" for name in inventory}
    if set(r6_header) != expected_keys:
        raise ValueError("volunteer_r6_delta_tensor_names_differ_from_inventory")
    for name, record in inventory.items():
        observed = r6_header[f"model.{name}"]
        if observed["shape"] != record["shape"]:
            raise ValueError(f"volunteer_r6_delta_tensor_shape_changed:{name}")
        if _normalized_dtype(observed["dtype"]) != _normalized_dtype(record["dtype"]):
            raise ValueError(f"volunteer_r6_delta_tensor_dtype_changed:{name}")
        reference = base_header.get(name)
        if reference is None:
            raise ValueError(f"volunteer_r6_delta_key_missing_from_base:{name}")
        if reference["shape"] != observed["shape"]:
            raise ValueError(f"volunteer_r6_delta_shape_differs_from_base:{name}")

    bundle_spec = model_lineage.get("bundle")
    if not isinstance(bundle_spec, Mapping) or not isinstance(bundle_spec.get("files"), Mapping):
        raise ValueError("volunteer_frozen_winner_bundle_inventory_missing")
    bundle_path = resolve_bundle()
    bundle_inventory = _verified_bundle(
        bundle_path,
        expected={name: record["sha256"] for name, record in bundle_spec["files"].items()},
    )

    return {
        "frozen_run": str(root),
        "winner_name": WINNER_NAME,
        "winner_document_sha256": document_digest,
        "winner_document": str(document_path),
        "selection_rule": document.get("rule"),
        "selection_status": document.get("status"),
        "r5_control_candidate": R5_CONTROL_NAME,
        "validation_context_accuracy": selected.get("validation_metrics", {}).get("accuracy"),
        "validation_context_note": (
            "1,364/1,664 = 81.97% controlled R6 validation accuracy; context only, "
            "never mixed with the volunteer pilot result"
        ),
        "checkpoint_path": str(checkpoint),
        "checkpoint_files": dict(observed_files),
        "checkpoint_sha256": checkpoint_sha256,
        "pass_index": PASS_INDEX,
        "inference": dict(INFERENCE),
        "initialization": {
            "base_checkpoint": str(base),
            "base_files": dict(base_files),
            "base_inventory_sha256": initialization["base_inventory_sha256"],
            "base_tensor_count": len(base_header),
            "decoder_layers": initialization["decoder_layers"],
            "delta_checkpoint": str(delta),
            "delta_sha256": initialization["delta_sha256"],
            "trainable_tensor_count": len(inventory),
            "trainable_keys_sha256": canonical_hash(sorted(inventory)),
            "trainable_inventory": {name: inventory[name] for name in sorted(inventory)},
        },
        "bundle": {
            "path": str(bundle_path),
            "files": dict(bundle_inventory["files"]),
            "extra_files": list(bundle_inventory["extra_files"]),
        },
        "composition": "pinned_r5_canonical_base_plus_r5_delta_plus_selected_r6_delta",
        "model_execution": False,
        "training_performed": False,
    }


__all__ = [
    "DEFAULT_BUNDLE",
    "FROZEN_RUN",
    "INFERENCE",
    "PASS_INDEX",
    "WINNER_CHECKPOINT_SHA256",
    "WINNER_DOCUMENT_SHA256",
    "WINNER_NAME",
    "resolve_bundle",
    "verify_frozen_winner",
]
