"""Owner-run comparison of the fixed preprocessing conditions against the raw baseline.

This is the only place that evaluates a preprocessing condition, and it is owner-run: the
agent never starts it. Before any model work it re-verifies the frozen pilot (manifest,
sources, crops, saved predictions and report), the fixed variant manifest with every
derivative's source hash and algorithm version, and the frozen R6 winner and checkpoint.

One greedy pass per condition, identical settings (same seed, batch size, token limit,
decoding and processor), separate private predictions and reports, then a paired
comparison by exact sample ID against the saved 50.77% baseline. Selection uses a rule
fixed before inference and recorded in the variant manifest.

The ``raw`` condition must reproduce the pilot's saved predictions exactly. If it does
not, the harness is not comparable and the run is refused rather than scored.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.english.volunteer import audit as audit_module
from research.english.volunteer import frozen as frozen_module
from research.english.volunteer import scoring as scoring_module
from research.english.volunteer import variants as variants_module
from research.english.volunteer.evaluate import (
    _load_selected_model,
    _prepare_torch,
    generate_records,
)
from research.english.volunteer.manifest import Manifest, load_manifest
from research.english.volunteer.prepare import (
    MANIFEST_NAME,
    PREDICTIONS_NAME,
    PREPARED_NAME,
    REPORT_NAME,
    private_run_directory,
)
from research.pretrained.artifacts import digest_file

COMPARISON_VERSION = "penforge-volunteer-preprocessing-comparison-v1"
CONDITIONS_DIR = "conditions"
COMPARISON_NAME = "comparison.json"
PREDICTION_VERSION = "penforge-volunteer-predictions-v1"
BASELINE_ACCURACY = 0.5076923076923077
BASELINE_EXACT = 132
SCOPE = "isolated_uppercase_and_lowercase_letter_crops_only"
SELECTION_LABEL = "development_selection_on_five_writers_not_independent_evidence"


@dataclass(frozen=True)
class ConditionSample:
    sample_id: str
    writer_id: str
    truth: str
    crop_path: str
    crop_sha256: str


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_comparison_{label}_missing_or_unsafe")
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


def verify_pilot(pilot_run: Path) -> tuple[Path, Manifest, dict[str, Any], dict[str, str]]:
    """Re-verify the frozen pilot completely, including its saved predictions."""
    root = private_run_directory(pilot_run)
    receipt = _read_json(root / PREPARED_NAME, "pilot_receipt")
    if receipt.get("status") != "prepared_not_evaluated":
        raise ValueError("volunteer_comparison_pilot_receipt_status_changed")
    manifest = load_manifest(root / MANIFEST_NAME)
    if receipt.get("crop_manifest_sha256") != manifest.sha256:
        raise ValueError("volunteer_comparison_pilot_manifest_changed")
    if not (root / REPORT_NAME).is_file() or not (root / PREDICTIONS_NAME).is_file():
        raise ValueError("volunteer_comparison_pilot_is_not_evaluated")
    report = _read_json(root / REPORT_NAME, "pilot_report")
    if report.get("status") != "private_five_writer_exploratory_pilot":
        raise ValueError("volunteer_comparison_pilot_report_status_changed")
    samples = report.get("samples", {})
    if samples.get("total") != len(manifest.samples):
        raise ValueError("volunteer_comparison_pilot_sample_total_changed")
    if round(float(samples.get("accuracy", -1)), 6) != round(BASELINE_ACCURACY, 6):
        raise ValueError("volunteer_comparison_pilot_accuracy_changed")
    predictions = audit_module.load_predictions(root / PREDICTIONS_NAME)
    if set(predictions) != {sample.sample_id for sample in manifest.samples}:
        raise ValueError("volunteer_comparison_pilot_predictions_inventory_changed")
    for record in manifest.sources:
        copy = root / record["copied_path"]
        if copy.is_symlink() or not copy.is_file():
            raise ValueError(f"volunteer_comparison_source_copy_missing:{record['writer_id']}")
        if digest_file(copy) != record["source_sha256"]:
            raise ValueError(f"volunteer_comparison_source_copy_changed:{record['writer_id']}")
        original = Path(record["source_path"])
        if original.is_file() and digest_file(original) != record["source_sha256"]:
            raise ValueError(f"volunteer_comparison_original_changed:{record['writer_id']}")
    for sample in manifest.samples:
        path = root / sample.crop_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"volunteer_comparison_crop_missing:{sample.sample_id}")
        if digest_file(path) != sample.crop_sha256:
            raise ValueError(f"volunteer_comparison_crop_changed:{sample.sample_id}")
    return root, manifest, report, predictions


def condition_samples(
    variant_run: Path, payload: Mapping[str, Any], variant_id: str
) -> list[ConditionSample]:
    """The 260 samples of one condition in the pilot's own inventory order."""
    entries = payload["by_variant"][variant_id]
    by_id = {entry["sample_id"]: entry for entry in entries}
    if len(by_id) != len(entries):
        raise ValueError(f"volunteer_comparison_duplicate_sample:{variant_id}")
    ordered = sorted(by_id.values(), key=lambda entry: entry["sample_id"])
    samples: list[ConditionSample] = []
    for entry in ordered:
        path = variant_run / str(entry["crop_path"])
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"volunteer_comparison_derivative_missing:{entry['sample_id']}")
        if digest_file(path) != entry["crop_sha256"]:
            raise ValueError(f"volunteer_comparison_derivative_changed:{entry['sample_id']}")
        samples.append(
            ConditionSample(
                sample_id=entry["sample_id"],
                writer_id=entry["writer_id"],
                truth=entry["truth"],
                crop_path=str(entry["crop_path"]),
                crop_sha256=entry["crop_sha256"],
            )
        )
    if len(samples) != 260:
        raise ValueError("volunteer_comparison_condition_sample_count_changed")
    return samples


def _cross_check_sources(payload: Mapping[str, Any], manifest: Manifest, variant_id: str) -> None:
    """Every derivative must declare the baseline crop hash it came from."""
    by_id = {sample.sample_id: sample for sample in manifest.samples}
    for entry in payload["by_variant"][variant_id]:
        baseline = by_id.get(entry["sample_id"])
        if baseline is None:
            raise ValueError(f"volunteer_comparison_unknown_sample:{entry['sample_id']}")
        if entry["source_crop_sha256"] != baseline.crop_sha256:
            raise ValueError(f"volunteer_comparison_source_hash_changed:{entry['sample_id']}")
        if entry["truth"] != baseline.truth or entry["writer_id"] != baseline.writer_id:
            raise ValueError(f"volunteer_comparison_label_or_writer_changed:{entry['sample_id']}")
        if variant_id == variants_module.RAW.variant_id:
            if entry["crop_sha256"] != baseline.crop_sha256:
                raise ValueError(
                    f"volunteer_comparison_raw_not_byte_identical:{entry['sample_id']}"
                )


def compare_records(
    baseline: Mapping[str, str], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Paired comparison by exact sample ID against the saved baseline predictions."""
    if len(records) != len(baseline):
        raise ValueError("volunteer_comparison_paired_inventory_changed")
    seen: set[str] = set()
    fixes: list[str] = []
    regressions: list[str] = []
    unchanged_errors: list[str] = []
    for record in records:
        sample_id = str(record["sample_id"])
        if sample_id in seen:
            raise ValueError(f"volunteer_comparison_duplicate_prediction:{sample_id}")
        seen.add(sample_id)
        truth = record["truth"]
        before = baseline[sample_id]
        after = record["prediction"]
        was_correct = before == truth
        now_correct = after == truth
        if now_correct and not was_correct:
            fixes.append(sample_id)
        elif was_correct and not now_correct:
            regressions.append(sample_id)
        elif not was_correct and not now_correct:
            unchanged_errors.append(sample_id)
    if seen != set(baseline):
        raise ValueError("volunteer_comparison_paired_inventory_incomplete")
    return {
        "fixes": sorted(fixes),
        "regressions": sorted(regressions),
        "unchanged_errors": sorted(unchanged_errors),
        "fix_count": len(fixes),
        "regression_count": len(regressions),
        "unchanged_error_count": len(unchanged_errors),
        "net_change": len(fixes) - len(regressions),
    }


def _condition_report(
    variant_id: str,
    spec: variants_module.VariantSpec,
    aggregate: Mapping[str, Any],
    paired: Mapping[str, Any],
    stats: Mapping[str, Any],
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "version": COMPARISON_VERSION,
        "condition": variant_id,
        "algorithm": spec.algorithm,
        "algorithm_version": spec.version,
        "parameters": dict(spec.parameters),
        "status": "owner_run_preprocessing_comparison",
        "scope": SCOPE,
        "product_qualification": "none",
        "training_performed": False,
        "model_execution": True,
        "development_selection": SELECTION_LABEL,
        "samples": aggregate,
        "paired_against_raw_baseline": paired,
        "timing": dict(stats),
        "audit_conditioned": audit,
    }


def select_winner(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Apply the rule fixed in the variant manifest before any inference."""
    ranked = []
    for variant_id, result in results.items():
        samples = result["samples"]
        ranked.append(
            {
                "variant_id": variant_id,
                "exact_correct": samples["correct"],
                "exact_accuracy": samples["accuracy"],
                "non_case_errors": samples["total"]
                - samples["correct"]
                - samples["case_only_errors"],
                "casefolded_shape_accuracy": samples["casefolded_shape_accuracy"],
            }
        )
    ranked.sort(
        key=lambda row: (
            -row["exact_correct"],
            row["non_case_errors"],
            -row["casefolded_shape_accuracy"],
            row["variant_id"],
        )
    )
    return {
        "rule": variants_module.SELECTION_RULE,
        "label": SELECTION_LABEL,
        "ranking": ranked,
        "winner": ranked[0]["variant_id"],
        "interpretation": (
            "Development-set selection on five writers. It is not an independent result, "
            "not a held-out measurement and not a product claim. Preprocessing cannot "
            "repair a crop whose ink is not the labelled letter."
        ),
    }


def evaluate(
    variant_run: Path,
    *,
    pilot_run: Path,
    frozen_run: Path = frozen_module.FROZEN_RUN,
    frozen_verifier: Callable[..., dict[str, Any]] = frozen_module.verify_frozen_winner,
    torch_factory: Callable[[int], Any] = _prepare_torch,
    model_loader: Callable[..., tuple[Any, Any, Any, dict[str, Any]]] = _load_selected_model,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Owner-run: one greedy pass per fixed condition, paired against the baseline."""
    pilot_root, manifest, pilot_report, baseline = verify_pilot(pilot_run)
    root = private_run_directory(variant_run)
    if root == pilot_root:
        raise ValueError("volunteer_comparison_must_not_write_into_the_pilot_run")
    for name in (COMPARISON_NAME,):
        if (root / name).exists():
            raise FileExistsError("volunteer_comparison_already_completed_use_a_new_directory")
    conditions = root / CONDITIONS_DIR
    if conditions.exists() and any(conditions.iterdir()):
        raise FileExistsError("volunteer_comparison_conditions_already_exist")
    payload = variants_module.load_variant_manifest(root)
    if payload.get("source_crop_manifest_sha256") != manifest.sha256:
        raise ValueError("volunteer_comparison_variants_use_a_different_pilot_manifest")
    for variant_id in variants_module.VARIANT_IDS:
        _cross_check_sources(payload, manifest, variant_id)
    frozen = frozen_verifier(frozen_run)
    inference = dict(frozen["inference"])
    if inference != frozen_module.INFERENCE:
        raise ValueError("volunteer_comparison_inference_recipe_changed")
    if frozen["checkpoint_sha256"] != frozen_module.WINNER_CHECKPOINT_SHA256:
        raise ValueError("volunteer_comparison_selected_checkpoint_changed")
    plan_samples = {
        variant_id: condition_samples(root, payload, variant_id)
        for variant_id in variants_module.VARIANT_IDS
    }
    outcome = audit_module.classify(baseline)
    clean_ids = {sample_id for sample_id, record in outcome.items() if not record["crop_defect"]}
    torch = torch_factory(int(inference["seed"]))
    model, tokenizer, processor, applied = model_loader(frozen, torch)
    results: dict[str, dict[str, Any]] = {}
    raw_records: list[dict[str, Any]] | None = None
    for variant_id in variants_module.VARIANT_IDS:
        spec = variants_module.variant_by_id(variant_id)
        started = time.monotonic()
        records, stats = generate_records(
            torch,
            model,
            tokenizer,
            processor,
            plan_samples[variant_id],
            root,
            batch_size=int(inference["batch_size"]),
            max_new_tokens=int(inference["max_new_tokens"]),
            progress=None if progress is None else _tagger(progress, variant_id),
        )
        paired = compare_records(baseline, records)
        if variant_id == variants_module.RAW.variant_id:
            raw_records = records
            if (
                paired["fix_count"]
                or paired["regression_count"]
                or any(
                    baseline[str(record["sample_id"])] != record["prediction"] for record in records
                )
            ):
                raise ValueError("volunteer_comparison_raw_condition_does_not_reproduce_the_pilot")
        aggregate = scoring_module.score(records)
        audit_view = _audit_view(outcome, records, clean_ids)
        report = _condition_report(variant_id, spec, aggregate, paired, stats, audit_view)
        report["execution"] = {
            "wall_seconds": time.monotonic() - started,
            "applied_delta_tensors": applied.get("applied_tensors"),
            "device": "cpu",
            "torch_threads": 2,
        }
        report["model"] = {
            "winner_name": frozen["winner_name"],
            "frozen_run": frozen["frozen_run"],
            "checkpoint_sha256": frozen["checkpoint_sha256"],
            "pass_index": frozen["pass_index"],
            "composition": frozen["composition"],
            "inference": inference,
        }
        report["data"] = {
            "pilot_run": str(pilot_root),
            "crop_manifest_sha256": manifest.sha256,
            "variant_manifest_sha256": payload["manifest_sha256"],
            "included_samples": len(plan_samples[variant_id]),
            "baseline_predictions_sha256": digest_file(pilot_root / PREDICTIONS_NAME),
        }
        directory = conditions / variant_id
        predictions_path = directory / PREDICTIONS_NAME
        if predictions_path.exists():
            raise FileExistsError(f"volunteer_comparison_predictions_exist:{variant_id}")
        _atomic_write(
            predictions_path,
            "".join(
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
            ).encode(),
        )
        report["predictions_sha256"] = digest_file(predictions_path)
        results[variant_id] = report
        _atomic_write(directory / REPORT_NAME, _json_bytes(report))
        if progress is not None:
            progress(
                json.dumps(
                    {
                        "stage": "compare",
                        "condition": variant_id,
                        "completed_conditions": len(results),
                        "total_conditions": len(variants_module.VARIANT_IDS),
                        "model_execution": True,
                    },
                    sort_keys=True,
                )
            )
    comparison = {
        "version": COMPARISON_VERSION,
        "status": "owner_run_preprocessing_comparison",
        "scope": SCOPE,
        "pilot_run": str(pilot_root),
        "variant_run": str(root),
        "crop_manifest_sha256": manifest.sha256,
        "variant_manifest_sha256": payload["manifest_sha256"],
        "baseline": {
            "source": "frozen_pilot_report",
            "accuracy": BASELINE_ACCURACY,
            "correct": BASELINE_EXACT,
            "total": len(baseline),
            "predictions_sha256": digest_file(pilot_root / PREDICTIONS_NAME),
            "report_sha256": digest_file(pilot_root / REPORT_NAME),
            "reproduced_by_raw_condition": raw_records is not None,
        },
        "conditions": results,
        "selection": select_winner(results),
        "audit_note": (
            "Fifty-eight of the 260 crops were judged defective in the crop audit; 55 of "
            "them produced errors. No preprocessing condition can repair a crop whose ink "
            "is not the labelled letter, so those errors stay fixed under every condition."
        ),
        "product_qualification": "none",
        "training_performed": False,
        "model_execution": True,
        "development_selection": SELECTION_LABEL,
        "pilot_modified": False,
    }
    _atomic_write(root / COMPARISON_NAME, _json_bytes(comparison))
    return comparison


def _tagger(progress: Callable[[str], None], variant_id: str) -> Callable[[str], None]:
    def emit(line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            progress(line)
            return
        payload["condition"] = variant_id
        progress(json.dumps(payload, sort_keys=True))

    return emit


def _audit_view(
    outcome: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    clean_ids: set[str],
) -> dict[str, Any]:
    """Accuracy split by the audit's reviewer categories, per condition."""
    clean_total = clean_total_correct = 0
    defect_total = defect_errors_remaining = 0
    for record in records:
        sample_id = str(record["sample_id"])
        correct = record["prediction"] == record["truth"]
        if sample_id in clean_ids:
            clean_total += 1
            clean_total_correct += int(correct)
        else:
            defect_total += 1
            defect_errors_remaining += int(not correct)
    return {
        "clean_crop_samples": clean_total,
        "clean_crop_correct": clean_total_correct,
        "clean_crop_accuracy": clean_total_correct / clean_total if clean_total else None,
        "crop_defect_samples": defect_total,
        "crop_defect_errors": defect_errors_remaining,
        "review_is_reviewer_judgment": True,
        "classification": dict(
            sorted(Counter(str(value["category"]) for value in outcome.values()).items())
        ),
    }


def summary(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Compact terminal summary: aggregate values only, never a per-sample prediction."""
    conditions = {}
    for variant_id, report in comparison["conditions"].items():
        samples = report["samples"]
        paired = report["paired_against_raw_baseline"]
        conditions[variant_id] = {
            "accuracy": samples["accuracy"],
            "correct": samples["correct"],
            "total": samples["total"],
            "casefolded_shape_accuracy": samples["casefolded_shape_accuracy"],
            "case_only_errors": samples["case_only_errors"],
            "uppercase_accuracy": samples["by_case"]["uppercase"]["accuracy"],
            "lowercase_accuracy": samples["by_case"]["lowercase"]["accuracy"],
            "per_writer_accuracy": {
                writer: value["accuracy"] for writer, value in samples["by_writer"].items()
            },
            "fixes": paired["fix_count"],
            "regressions": paired["regression_count"],
            "unchanged_errors": paired["unchanged_error_count"],
            "clean_crop_accuracy": report["audit_conditioned"]["clean_crop_accuracy"],
        }
    return {
        "status": comparison["status"],
        "baseline_accuracy": comparison["baseline"]["accuracy"],
        "conditions": conditions,
        "selection": comparison["selection"],
        "development_selection": comparison["development_selection"],
        "product_qualification": comparison["product_qualification"],
        "training_performed": False,
        "private_sample_results": "written_to_run_directory_only",
    }


__all__ = [
    "BASELINE_ACCURACY",
    "COMPARISON_NAME",
    "COMPARISON_VERSION",
    "CONDITIONS_DIR",
    "SELECTION_LABEL",
    "compare_records",
    "condition_samples",
    "evaluate",
    "select_winner",
    "summary",
    "verify_pilot",
]
