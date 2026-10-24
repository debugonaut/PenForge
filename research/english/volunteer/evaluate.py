"""Owner-only evaluation of the frozen R6 pass-2 winner on the volunteer crops.

This module is the only place in the pilot that may load a model. It verifies the frozen
winner, the prepared crop manifest, every crop digest and every source digest before it
imports Torch, reconstructs exactly ``pinned R5 base + R5 selected delta + selected R6
delta``, runs one greedy pass over the 260 isolated letter crops and scores the raw text.

No training, no fine-tuning, no beam search, no lexicon, no language correction, no
trimming and no case normalisation happen here. Only aggregate results reach the console;
per-sample predictions stay in the private run directory.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from research.english.volunteer import frozen as frozen_module
from research.english.volunteer import scoring as scoring_module
from research.english.volunteer.manifest import (
    EXPECTED_SAMPLES,
    Manifest,
    load_manifest,
)
from research.english.volunteer.prepare import (
    MANIFEST_NAME,
    PREDICTIONS_NAME,
    PREPARED_NAME,
    REPORT_NAME,
    private_run_directory,
)
from research.pretrained.artifacts import digest_file

PILOT_STATUS = "private_five_writer_exploratory_pilot"
REPORT_VERSION = "penforge-volunteer-report-v1"
PREDICTION_VERSION = "penforge-volunteer-predictions-v1"
MAX_WALL_SECONDS = 3600
SCOPE = "isolated_uppercase_and_lowercase_letter_crops_only"


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_{label}_missing_or_unsafe")
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as stream:
        stream.write(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def verify_prepared(run: Path) -> tuple[Path, dict[str, Any], Manifest]:
    """Re-verify the prepared run completely before any model import."""
    root = private_run_directory(run)
    if (root / REPORT_NAME).exists() or (root / PREDICTIONS_NAME).exists():
        raise FileExistsError(
            "volunteer_run_already_evaluated_use_a_new_output_directory:"
            "set PENFORGE_VOLUNTEER_RUN to a new directory and re-run prepare"
        )
    receipt = _read_json(root / PREPARED_NAME, "prepared_receipt")
    if receipt.get("status") != "prepared_not_evaluated":
        raise ValueError("volunteer_prepared_receipt_status_changed")
    if (
        receipt.get("model_execution") is not False
        or receipt.get("training_performed") is not False
    ):
        raise ValueError("volunteer_prepared_receipt_claims_model_work")
    manifest_path = root / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    if receipt.get("crop_manifest_sha256") != digest_file(manifest_path):
        raise ValueError("volunteer_crop_manifest_changed_after_preparation")
    if receipt.get("included_samples") != len(manifest.samples):
        raise ValueError("volunteer_prepared_receipt_sample_count_changed")
    for record in manifest.sources:
        copy = root / record["copied_path"]
        if copy.is_symlink() or not copy.is_file():
            raise ValueError(f"volunteer_source_copy_missing:{record['writer_id']}")
        if digest_file(copy) != record["source_sha256"]:
            raise ValueError(f"volunteer_source_copy_changed:{record['writer_id']}")
    for sample in manifest.samples:
        path = root / sample.crop_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"volunteer_crop_missing:{sample.sample_id}")
        if digest_file(path) != sample.crop_sha256:
            raise ValueError(f"volunteer_crop_changed:{sample.sample_id}")
    return root, receipt, manifest


class SampleRef(Protocol):
    """Structural view shared by the pilot manifest and preprocessing conditions."""

    @property
    def sample_id(self) -> str: ...

    @property
    def crop_path(self) -> str: ...

    @property
    def crop_sha256(self) -> str: ...

    @property
    def truth(self) -> str: ...

    @property
    def writer_id(self) -> str: ...


def crop_pixels(processor: Any, root: Path, sample: SampleRef) -> Any:
    """Pinned upstream preprocessing: the crop's own pixels, processor resize to 384."""
    from PIL import Image

    path = root / sample.crop_path
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_crop_missing:{sample.sample_id}")
    if digest_file(path) != sample.crop_sha256:
        raise ValueError(f"volunteer_crop_changed_during_run:{sample.sample_id}")
    size = getattr(processor, "size", None)
    if not isinstance(size, dict) or size.get("height") != 384 or size.get("width") != 384:
        raise ValueError("volunteer_processor_canvas_changed_from_pinned_384")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"volunteer_crop_format_changed:{sample.sample_id}")
        image.load()
        value = image.convert("RGB")
    return processor(images=value, return_tensors="pt", do_resize=True).pixel_values


def _batches(samples: Sequence[SampleRef], size: int) -> list[list[SampleRef]]:
    if not samples:
        raise ValueError("volunteer_evaluation_requires_samples")
    if size < 1:
        raise ValueError("volunteer_evaluation_requires_positive_batch_size")
    return [list(samples[start : start + size]) for start in range(0, len(samples), size)]


def generate_records(
    torch: Any,
    model: Any,
    tokenizer: Any,
    processor: Any,
    samples: Sequence[SampleRef],
    root: Path,
    *,
    batch_size: int,
    max_new_tokens: int,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One greedy pass over the crops; predictions are raw decoded text, never edited."""
    model.eval()
    started = time.monotonic()
    records: list[dict[str, Any]] = []
    per_crop_ms: list[float] = []
    limit_hits = 0
    eos_id = tokenizer.eos_token_id
    for group in _batches(samples, batch_size):
        if time.monotonic() - started > MAX_WALL_SECONDS:
            raise TimeoutError("volunteer_evaluation_exceeded_wall_ceiling")
        tick = time.monotonic()
        values = torch.cat([crop_pixels(processor, root, sample) for sample in group], dim=0)
        with torch.inference_mode():
            generated = model.generate(
                pixel_values=values,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                use_cache=True,
            )
        elapsed = (time.monotonic() - tick) * 1000 / len(group)
        for sample, ids in zip(group, generated.tolist(), strict=True):
            text = tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            records.append(
                {
                    "sample_id": sample.sample_id,
                    "writer_id": sample.writer_id,
                    "truth": sample.truth,
                    "prediction": text,
                }
            )
            # TrOCR shares the start and EOS id, so only a later EOS ends output.
            limit_hits += int(eos_id not in ids[1:])
            per_crop_ms.append(elapsed)
        if progress is not None:
            progress(
                json.dumps(
                    {
                        "stage": "evaluate",
                        "completed": len(records),
                        "total": len(samples),
                        "model_execution": True,
                    },
                    sort_keys=True,
                )
            )
    if len(records) != len(samples):
        raise ValueError("volunteer_prediction_count_differs_from_crop_count")
    stats = {
        "wall_seconds": time.monotonic() - started,
        "per_crop_ms_p50": _percentile(per_crop_ms, 0.5),
        "per_crop_ms_p95": _percentile(per_crop_ms, 0.95),
        "per_crop_ms_mean": sum(per_crop_ms) / len(per_crop_ms),
        "generation_limit_hits": limit_hits,
        "batch_size": batch_size,
        "max_new_tokens": max_new_tokens,
    }
    return records, stats


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("volunteer_percentile_requires_values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _prepare_torch(seed: int) -> Any:
    import torch

    torch.manual_seed(seed)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def _load_selected_model(
    frozen: Mapping[str, Any], torch: Any
) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Reconstruct R5 winner state, then apply the verified R6 pass-2 delta."""
    from research.english.r6_model import initialization as initialization_module
    from research.english.r6_model.runtime import ContinuationRuntime

    initialization = dict(frozen["initialization"])
    runtime = ContinuationRuntime(Path(frozen["bundle"]["path"]))
    tokenizer = runtime.tokenizer()
    processor = runtime.processor()
    model, tokenizer, inventory = runtime.load_composition(initialization, torch=torch)
    checkpoint = Path(frozen["checkpoint_path"])
    if digest_file(checkpoint / "state.safetensors") != frozen["checkpoint_sha256"]:
        raise ValueError("volunteer_selected_checkpoint_changed_before_delta_application")
    state = _read_json(checkpoint / "state.json", "selected_r6_state")
    if (
        state.get("pass_index") != frozen["pass_index"]
        or state.get("weights_only") is not True
        or state.get("tensors_sha256") != frozen["checkpoint_sha256"]
        or state.get("trainable_inventory") != inventory
    ):
        raise ValueError("volunteer_selected_r6_state_disagrees_with_runtime_inventory")
    tensors = initialization_module.read_delta_tensors(checkpoint / "state.safetensors")
    applied = initialization_module.apply_delta(torch, model, tensors, inventory)
    del tensors
    return model, tokenizer, processor, applied


def evaluate(
    run: Path,
    *,
    frozen_run: Path = frozen_module.FROZEN_RUN,
    frozen_verifier: Callable[..., dict[str, Any]] = frozen_module.verify_frozen_winner,
    torch_factory: Callable[[int], Any] = _prepare_torch,
    model_loader: Callable[..., tuple[Any, Any, Any, dict[str, Any]]] = _load_selected_model,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify everything, then run exactly one greedy pass and score it."""
    root, receipt, manifest = verify_prepared(run)
    frozen = frozen_verifier(frozen_run)
    inference = dict(frozen["inference"])
    if inference != frozen_module.INFERENCE:
        raise ValueError("volunteer_inference_recipe_changed")
    started = time.monotonic()
    torch = torch_factory(int(inference["seed"]))
    model, tokenizer, processor, applied = model_loader(frozen, torch)
    records, stats = generate_records(
        torch,
        model,
        tokenizer,
        processor,
        manifest.samples,
        root,
        batch_size=int(inference["batch_size"]),
        max_new_tokens=int(inference["max_new_tokens"]),
        progress=progress,
    )
    aggregate = scoring_module.score(records)
    report = {
        "version": REPORT_VERSION,
        "status": PILOT_STATUS,
        "scope": SCOPE,
        "product_qualification": "none",
        "training_performed": False,
        "model_execution": True,
        "capability_status": "research_only",
        "exploratory": True,
        "writer_count": len(frozen_module_frozen_writers(manifest)),
        "limitations": [
            "exploratory_five_writer_external_pilot_not_a_generalization_claim",
            "no_bootstrap_or_confidence_interval_five_writers_are_too_few",
            "isolated_letter_crops_only_no_sentence_or_page_ocr",
            "no_segmentation_quality_personal_font_quality_or_product_qualification",
            "volunteer_pilot_result_is_separate_from_the_r6_validation_accuracy",
        ],
        "samples": aggregate,
        "timing": stats,
        "execution": {
            "wall_seconds": time.monotonic() - started,
            "applied_delta_tensors": applied.get("applied_tensors"),
            "device": "cpu",
            "torch_threads": 2,
        },
        "model": {
            "winner_name": frozen["winner_name"],
            "frozen_run": frozen["frozen_run"],
            "winner_document_sha256": frozen["winner_document_sha256"],
            "checkpoint_path": frozen["checkpoint_path"],
            "checkpoint_sha256": frozen["checkpoint_sha256"],
            "checkpoint_files": frozen["checkpoint_files"],
            "pass_index": frozen["pass_index"],
            "composition": frozen["composition"],
            "base_checkpoint": frozen["initialization"]["base_checkpoint"],
            "base_files": frozen["initialization"]["base_files"],
            "r5_delta_checkpoint": frozen["initialization"]["delta_checkpoint"],
            "r5_delta_sha256": frozen["initialization"]["delta_sha256"],
            "trainable_keys_sha256": frozen["initialization"]["trainable_keys_sha256"],
            "bundle_path": frozen["bundle"]["path"],
            "bundle_files": frozen["bundle"]["files"],
            "inference": inference,
            "validation_context_accuracy": frozen["validation_context_accuracy"],
            "validation_context_note": frozen["validation_context_note"],
        },
        "data": {
            "source_images": [
                {
                    "writer_id": record["writer_id"],
                    "source_sha256": record["source_sha256"],
                    "source_bytes": record["source_bytes"],
                    "page_width": record["page_width"],
                    "page_height": record["page_height"],
                }
                for record in manifest.sources
            ],
            "crop_manifest_sha256": manifest.sha256,
            "crop_manifest_version": receipt.get("crop_generation_version"),
            "coordinates_sha256": receipt.get("coordinates_sha256"),
            "included_samples": len(manifest.samples),
            "excluded_samples": len(manifest.excluded),
            "expected_samples": EXPECTED_SAMPLES,
        },
    }
    predictions_path = root / PREDICTIONS_NAME
    if predictions_path.exists() or predictions_path.is_symlink():
        raise FileExistsError("volunteer_predictions_must_be_new")
    payload = "".join(
        json.dumps(
            {
                "version": PREDICTION_VERSION,
                "sample_id": record["sample_id"],
                "writer_id": record["writer_id"],
                "truth": record["truth"],
                "raw_prediction": record["prediction"],
            },
            sort_keys=True,
        )
        + "\n"
        for record in records
    )
    _atomic_write(predictions_path, payload.encode())
    report["predictions_sha256"] = digest_file(predictions_path)
    _atomic_write(root / REPORT_NAME, _json_bytes(report))
    return report


def frozen_module_frozen_writers(manifest: Manifest) -> list[str]:
    return sorted({sample.writer_id for sample in manifest.samples})


__all__ = [
    "PILOT_STATUS",
    "SCOPE",
    "crop_pixels",
    "evaluate",
    "generate_records",
    "verify_prepared",
]
