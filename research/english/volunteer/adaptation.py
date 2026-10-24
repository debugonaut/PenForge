"""Bounded volunteer-domain adaptation of the frozen R6 winner: data and lifecycle.

Two stages, deliberately separated:

* :func:`prepare` is **data-only**. It re-verifies the frozen pilot, the frozen R6 winner
  and the admitted R6 training/validation mapping, then writes a private mixture of the
  unchanged 6,656 clean R6 training rows and the volunteer crops the crop audit judged
  correctly labelled. It imports no model framework.
* :func:`run` is **owner-only**. It continues the frozen R6 pass-2 winner for at most two
  passes with a fresh AdamW, capture-condition augmentation on the volunteer rows, a
  forgetting guard on the untouched 1,664-row R6 validation split and pass-boundary
  checkpoints. The agent never executes it.

The five pilot writers (W01–W05) are the *development/training* writers. Their crops are
never given an evaluation role inside this experiment: the whole writer enters training,
so no writer straddles training and evaluation. The external holdout is W06–W09 and lives
in :mod:`research.english.volunteer.holdout`.

Nothing here changes the R6 checkpoint, the R5 delta or the pinned base; new weights go
to a new private run directory and are labelled experimental, not product.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.english.volunteer import audit as audit_module
from research.english.volunteer import capture as capture_module
from research.english.volunteer import comparison as comparison_module
from research.english.volunteer import frozen as frozen_module
from research.english.volunteer.manifest import WRITERS
from research.english.volunteer.prepare import (
    PREDICTIONS_NAME,
    REPORT_NAME,
    private_run_directory,
)
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file

CONFIG_NAME = ROOT / "research/configs/english-volunteer-adaptation.json"
R6_RECIPE_NAME = ROOT / "research/configs/english-r6-model-continuation.json"
PREPARED_VERSION = "penforge-volunteer-adaptation-dataset-v1"
REPORT_VERSION = "penforge-volunteer-adaptation-report-v1"
MIXTURE_NAME = "mixture-private.jsonl"
PREPARED_NAME = "prepared.json"
REPORT_NAME_OUT = "adaptation-report.json"
SELECTION_NAME = "selection.json"
CHECKPOINT_DIR = "checkpoints"
BEST_DIR = "training/best"
CANDIDATE = "volunteer_adaptation_r6_pass2"
CAPABILITY_STATUS = "experimental_volunteer_domain_adaptation"
SELECTION_RULE = "forgetting_guard_then_validation_exact_then_lower_pass_index"
DEVELOPMENT_ROLE = "development_training_writers"
HOLDOUT_ROLE = "external_holdout_writers"
LIMITATIONS = (
    "experimental_volunteer_domain_adaptation_not_a_product_model",
    "twenty_six_clean_volunteer_crops_per_writer_is_a_very_small_adaptation_signal",
    "five_development_writers_cannot_prove_generalisation",
    "development_writers_enter_training_so_no_writer_straddles_roles",
    "external_evidence_requires_the_w06_w09_holdout",
    "isolated_uppercase_and_lowercase_letter_crops_only",
    "no_sentence_page_ocr_segmentation_personal_font_or_product_qualification",
)
R6_ROW_FIELDS = frozenset(
    {
        "id",
        "hash",
        "path",
        "pixel_hash",
        "label",
        "writer",
        "source_group",
        "source_partition",
        "split",
        "source_member",
    }
)


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class CaptureSettings:
    """The same attribute names :mod:`capture` reads, parsed from the config."""

    seed: int
    augmentation_probability: float
    max_rotation_degrees: float
    max_shear: float
    min_scale: float
    max_scale: float
    max_translation_fraction: float
    max_perspective_fraction: float
    brightness_min: float
    brightness_max: float
    max_illumination_amplitude: float
    blur_probability: float
    max_blur_radius: float
    jpeg_probability: float
    jpeg_quality_min: int
    jpeg_quality_max: int
    ruled_background_probability: float

    def record(self) -> dict[str, Any]:
        return {
            "version": capture_module.CAPTURE_VERSION,
            "parameters": {name: getattr(self, name) for name in capture_module.PARAMETER_NAMES},
        }


@dataclass(frozen=True)
class AdaptationConfig:
    path: Path
    sha256: str
    candidate: str
    seed: int
    pass_limit: int
    data_epoch_offset: int
    batch_size: int
    accumulate_batches: int
    learning_rate: float
    warmup_optimizer_steps: int
    final_learning_rate_ratio: float
    weight_decay: float
    clip_grad_norm: float
    max_label_tokens: int
    volunteer_max_share: float
    forgetting_floor_correct: int
    max_pass_wall_seconds: float
    max_validation_wall_seconds: float
    max_run_wall_seconds: float
    max_rss_mb: float
    max_output_bytes: int
    capture: CaptureSettings
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def record(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "candidate": self.candidate,
            "seed": self.seed,
            "pass_limit": self.pass_limit,
            "data_epoch_offset": self.data_epoch_offset,
            "batch_size": self.batch_size,
            "accumulate_batches": self.accumulate_batches,
            "learning_rate": self.learning_rate,
            "warmup_optimizer_steps": self.warmup_optimizer_steps,
            "final_learning_rate_ratio": self.final_learning_rate_ratio,
            "weight_decay": self.weight_decay,
            "clip_grad_norm": self.clip_grad_norm,
            "max_label_tokens": self.max_label_tokens,
            "volunteer_max_share": self.volunteer_max_share,
            "forgetting_floor_correct": self.forgetting_floor_correct,
            "selection_rule": SELECTION_RULE,
            "capture": self.capture.record(),
        }


def _require(mapping: Mapping[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"volunteer_adaptation_config_missing:{key}")
    return mapping[key]


def _number(value: object, field_name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"volunteer_adaptation_config_not_numeric:{field_name}")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"volunteer_adaptation_config_out_of_range:{field_name}")
    return number


def _integer(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"volunteer_adaptation_config_not_integer:{field_name}")
    if not minimum <= value <= maximum:
        raise ValueError(f"volunteer_adaptation_config_out_of_range:{field_name}")
    return value


def load_config(path: Path | None = None) -> AdaptationConfig:
    """Parse the single fixed recipe. There is no parameter search."""
    config_path = Path(path or CONFIG_NAME)
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("volunteer_adaptation_config_missing_or_unsafe")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("volunteer_adaptation_config_must_be_an_object")
    if payload.get("version") != "penforge-volunteer-adaptation-v1":
        raise ValueError("volunteer_adaptation_config_version_changed")
    if payload.get("candidate") != CANDIDATE:
        raise ValueError("volunteer_adaptation_config_candidate_changed")
    if payload.get("capability_status") != CAPABILITY_STATUS:
        raise ValueError("volunteer_adaptation_config_capability_changed")
    if payload.get("initialization") != "verified_frozen_r6_pass2_winner":
        raise ValueError("volunteer_adaptation_initialization_policy_changed")
    if payload.get("optimizer_initialization") != "fresh_adamw_no_prior_optimizer_state":
        raise ValueError("volunteer_adaptation_optimizer_policy_changed")
    if payload.get("volunteer_training_policy") != "audit_clean_only":
        raise ValueError("volunteer_adaptation_training_policy_changed")
    if payload.get("training_preprocessing") != "aspect_preserving_white_pad_vit_rgb_384_v1":
        raise ValueError("volunteer_adaptation_training_preprocessing_changed")
    if payload.get("validation_preprocessing") != "pinned_upstream_vit_rgb_resize_384_v1":
        raise ValueError("volunteer_adaptation_validation_preprocessing_changed")
    if payload.get("objective") != "teacher_forced_aligned_token_cross_entropy_v1":
        raise ValueError("volunteer_adaptation_objective_changed")
    if payload.get("decoding") != "greedy_no_lexicon_no_language_correction":
        raise ValueError("volunteer_adaptation_decoding_changed")
    if payload.get("class_order") != "uppercase_a_to_z_then_lowercase_a_to_z":
        raise ValueError("volunteer_adaptation_class_order_changed")
    if payload.get("selection_rule") != SELECTION_RULE:
        raise ValueError("volunteer_adaptation_selection_rule_changed")
    raw_capture = _require(payload, "capture")
    if not isinstance(raw_capture, Mapping):
        raise ValueError("volunteer_adaptation_capture_must_be_an_object")
    maximum_quality = _integer(
        _require(raw_capture, "jpeg_quality_max"), "jpeg_quality_max", minimum=60, maximum=100
    )
    minimum_quality = _integer(
        _require(raw_capture, "jpeg_quality_min"), "jpeg_quality_min", minimum=60, maximum=100
    )
    if minimum_quality > maximum_quality:
        raise ValueError("volunteer_adaptation_jpeg_quality_range_inverted")
    minimum_scale = _number(
        _require(raw_capture, "min_scale"), "min_scale", minimum=0.85, maximum=1.0
    )
    maximum_scale = _number(
        _require(raw_capture, "max_scale"), "max_scale", minimum=1.0, maximum=1.15
    )
    capture = CaptureSettings(
        seed=_integer(_require(raw_capture, "seed"), "capture_seed", minimum=1, maximum=2**31 - 1),
        augmentation_probability=_number(
            _require(raw_capture, "augmentation_probability"),
            "augmentation_probability",
            minimum=0.0,
            maximum=1.0,
        ),
        max_rotation_degrees=_number(
            _require(raw_capture, "max_rotation_degrees"),
            "max_rotation_degrees",
            minimum=0.0,
            maximum=6.0,
        ),
        max_shear=_number(
            _require(raw_capture, "max_shear"), "max_shear", minimum=0.0, maximum=0.08
        ),
        min_scale=minimum_scale,
        max_scale=maximum_scale,
        max_translation_fraction=_number(
            _require(raw_capture, "max_translation_fraction"),
            "max_translation_fraction",
            minimum=0.0,
            maximum=0.05,
        ),
        max_perspective_fraction=_number(
            _require(raw_capture, "max_perspective_fraction"),
            "max_perspective_fraction",
            minimum=0.0,
            maximum=0.03,
        ),
        brightness_min=_number(
            _require(raw_capture, "brightness_min"), "brightness_min", minimum=0.85, maximum=1.0
        ),
        brightness_max=_number(
            _require(raw_capture, "brightness_max"), "brightness_max", minimum=1.0, maximum=1.15
        ),
        max_illumination_amplitude=_number(
            _require(raw_capture, "max_illumination_amplitude"),
            "max_illumination_amplitude",
            minimum=0.0,
            maximum=0.12,
        ),
        blur_probability=_number(
            _require(raw_capture, "blur_probability"), "blur_probability", minimum=0.0, maximum=1.0
        ),
        max_blur_radius=_number(
            _require(raw_capture, "max_blur_radius"), "max_blur_radius", minimum=0.0, maximum=1.0
        ),
        jpeg_probability=_number(
            _require(raw_capture, "jpeg_probability"), "jpeg_probability", minimum=0.0, maximum=1.0
        ),
        jpeg_quality_min=minimum_quality,
        jpeg_quality_max=maximum_quality,
        ruled_background_probability=_number(
            _require(raw_capture, "ruled_background_probability"),
            "ruled_background_probability",
            minimum=0.0,
            maximum=1.0,
        ),
    )
    return AdaptationConfig(
        path=config_path.resolve(),
        sha256=digest_file(config_path),
        candidate=CANDIDATE,
        seed=_integer(_require(payload, "seed"), "seed", minimum=1, maximum=2**31 - 1),
        pass_limit=_integer(_require(payload, "pass_limit"), "pass_limit", minimum=1, maximum=2),
        data_epoch_offset=_integer(
            _require(payload, "data_epoch_offset"), "data_epoch_offset", minimum=1, maximum=64
        ),
        batch_size=_integer(_require(payload, "batch_size"), "batch_size", minimum=1, maximum=8),
        accumulate_batches=_integer(
            _require(payload, "accumulate_batches"), "accumulate_batches", minimum=1, maximum=16
        ),
        learning_rate=_number(
            _require(payload, "learning_rate"), "learning_rate", minimum=1e-7, maximum=2e-5
        ),
        warmup_optimizer_steps=_integer(
            _require(payload, "warmup_optimizer_steps"),
            "warmup_optimizer_steps",
            minimum=0,
            maximum=200,
        ),
        final_learning_rate_ratio=_number(
            _require(payload, "final_learning_rate_ratio"),
            "final_learning_rate_ratio",
            minimum=0.05,
            maximum=1.0,
        ),
        weight_decay=_number(
            _require(payload, "weight_decay"), "weight_decay", minimum=0.0, maximum=0.1
        ),
        clip_grad_norm=_number(
            _require(payload, "clip_grad_norm"), "clip_grad_norm", minimum=0.1, maximum=5.0
        ),
        max_label_tokens=_integer(
            _require(payload, "max_label_tokens"), "max_label_tokens", minimum=1, maximum=16
        ),
        volunteer_max_share=_number(
            _require(payload, "volunteer_max_share"),
            "volunteer_max_share",
            minimum=0.0,
            maximum=0.1,
        ),
        forgetting_floor_correct=_integer(
            _require(payload, "forgetting_floor_correct"),
            "forgetting_floor_correct",
            minimum=1,
            maximum=1664,
        ),
        max_pass_wall_seconds=_number(
            _require(payload, "max_pass_wall_seconds"),
            "max_pass_wall_seconds",
            minimum=60,
            maximum=7200,
        ),
        max_validation_wall_seconds=_number(
            _require(payload, "max_validation_wall_seconds"),
            "max_validation_wall_seconds",
            minimum=60,
            maximum=3600,
        ),
        max_run_wall_seconds=_number(
            _require(payload, "max_run_wall_seconds"),
            "max_run_wall_seconds",
            minimum=600,
            maximum=21600,
        ),
        max_rss_mb=_number(
            _require(payload, "max_rss_mb"), "max_rss_mb", minimum=1024, maximum=16384
        ),
        max_output_bytes=_integer(
            _require(payload, "max_output_bytes"),
            "max_output_bytes",
            minimum=10**8,
            maximum=3 * 10**9,
        ),
        capture=capture,
        raw=payload,
    )


# --------------------------------------------------------------------------- io helpers


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as stream:
        stream.write(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _write_new_or_identical(root: Path, relative: str, payload: bytes) -> bool:
    target = root / relative
    if target.is_symlink():
        raise ValueError(f"volunteer_adaptation_output_must_not_be_symlink:{relative}")
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError(f"volunteer_adaptation_existing_output_differs:{relative}")
        return False
    _atomic_write(target, payload)
    return True


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_adaptation_{label}_missing_or_unsafe")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ data preparation


def _frozen_session(frozen_run: Path) -> dict[str, Any]:
    path = Path(frozen_run) / "session.json"
    session = _read_json(path, "frozen_session")
    if session.get("version") != "r6-session-v1":
        raise ValueError("volunteer_adaptation_frozen_session_version_changed")
    lineage = session.get("model_lineage")
    if not isinstance(lineage, Mapping):
        raise ValueError("volunteer_adaptation_frozen_session_lineage_missing")
    if session.get("model_lineage_hash") != canonical_hash(lineage):
        raise ValueError("volunteer_adaptation_frozen_session_lineage_hash_changed")
    rows = lineage.get("rows")
    if not isinstance(rows, Mapping) or "splits" not in rows:
        raise ValueError("volunteer_adaptation_frozen_session_rows_missing")
    return session


def _compare_split_lineage(
    observed: Mapping[str, Any], expected: Mapping[str, Any], name: str
) -> None:
    keys = (
        "samples",
        "writers",
        "classes",
        "per_class",
        "sample_ids_sha256",
        "content_hashes_sha256",
        "pixel_hashes_sha256",
        "writers_sha256",
    )
    for key in keys:
        if observed.get(key) != expected.get(key):
            raise ValueError(f"volunteer_adaptation_{name}_lineage_changed:{key}")


def _volunteer_rows(
    manifest, baseline: Mapping[str, str], audit_report: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The audit-clean development crops, with writer and page ancestry preserved."""
    outcome = audit_module.classify(baseline)
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for sample in manifest.samples:
        record = outcome[sample.sample_id]
        if record["crop_defect"]:
            excluded.append(
                {
                    "sample_id": sample.sample_id,
                    "writer_id": sample.writer_id,
                    "truth": sample.truth,
                    "category": record["category"],
                    "reason": "crop_audit_judged_the_crop_defective",
                }
            )
            continue
        rows.append(
            {
                "kind": "volunteer",
                "id": f"vol-{sample.sample_id}",
                "sample_id": sample.sample_id,
                "label": sample.truth,
                "writer": sample.writer_id,
                "group": sample.group,
                "path": sample.crop_path,
                "crop_sha256": sample.crop_sha256,
                "source_sha256": sample.source_sha256,
                "page": sample.sample_id.split("-", 1)[0],
                "role": DEVELOPMENT_ROLE,
            }
        )
    if len({row["writer"] for row in rows}) != len(WRITERS):
        raise ValueError("volunteer_adaptation_clean_crops_lost_a_writer")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("volunteer_adaptation_duplicate_volunteer_row")
    summary = {
        "included": len(rows),
        "excluded": len(excluded),
        "excluded_samples": excluded,
        "per_writer": dict(sorted(Counter(row["writer"] for row in rows).items())),
        "per_class": dict(sorted(Counter(row["label"] for row in rows).items())),
        "audit_categories": dict(
            sorted(Counter(str(value["category"]) for value in outcome.values()).items())
        ),
        "audit_report_sha256": audit_report.get("review_version"),
        "writer_role": DEVELOPMENT_ROLE,
        "writers": list(WRITERS),
        "model_execution": False,
    }
    return rows, summary


def _mixture_rows(
    study: Any, volunteer_rows: Sequence[Mapping[str, Any]], config: AdaptationConfig
) -> list[dict[str, Any]]:
    r6_rows = []
    for row in study.train:
        if set(row) != R6_ROW_FIELDS:
            raise ValueError("volunteer_adaptation_r6_row_fields_changed")
        r6_rows.append({"kind": "r6", **{key: row[key] for key in sorted(R6_ROW_FIELDS)}})
    r6_writers = {row["writer"] for row in r6_rows}
    overlap = r6_writers & {row["writer"] for row in volunteer_rows}
    if overlap:
        raise ValueError("volunteer_adaptation_writer_overlap_between_r6_and_volunteer")
    if len(r6_rows) != 6656:
        raise ValueError("volunteer_adaptation_r6_training_inventory_changed")
    if len(volunteer_rows) == 0:
        raise ValueError("volunteer_adaptation_requires_clean_volunteer_crops")
    share = len(volunteer_rows) / (len(r6_rows) + len(volunteer_rows))
    if share > config.volunteer_max_share:
        raise ValueError("volunteer_adaptation_volunteer_share_exceeds_the_declared_cap")
    ids = [row["id"] for row in r6_rows] + [row["id"] for row in volunteer_rows]
    if len(set(ids)) != len(ids):
        raise ValueError("volunteer_adaptation_duplicate_mixture_id")
    classes = Counter(row["label"] for row in r6_rows)
    if len(classes) != 52:
        raise ValueError("volunteer_adaptation_r6_training_classes_changed")
    ordered_r6 = sorted(r6_rows, key=lambda row: row["id"])
    ordered_volunteer = sorted(
        (dict(row) for row in volunteer_rows), key=lambda row: str(row["id"])
    )
    return [*ordered_r6, *ordered_volunteer]


def prepare(
    pilot_run: Path,
    staging: Path,
    run: Path,
    *,
    frozen_run: Path = frozen_module.FROZEN_RUN,
    config: AdaptationConfig | None = None,
) -> dict[str, Any]:
    """Write the private, deterministic adaptation dataset. Data only, no model."""
    recipe = config or load_config()
    root = private_run_directory(run)
    if root == private_run_directory(pilot_run):
        raise ValueError("volunteer_adaptation_needs_its_own_run_directory")
    if root.is_relative_to(Path(frozen_run).resolve()):
        raise ValueError("volunteer_adaptation_must_not_write_into_the_frozen_r6_run")
    frozen = frozen_module.verify_frozen_winner(frozen_run)
    pilot_root, manifest, pilot_report, baseline = comparison_module.verify_pilot(pilot_run)
    session = _frozen_session(Path(frozen_run))

    from research.english.r6_model import rows as rows_module

    study = rows_module.load_study(Path(staging))
    lineage = study.lineage()
    expected_splits = session["model_lineage"]["rows"]["splits"]
    _compare_split_lineage(lineage["splits"]["train"], expected_splits["train"], "r6_training")
    _compare_split_lineage(
        lineage["splits"]["validation"], expected_splits["validation"], "r6_validation"
    )
    if len(study.validation) != 1664:
        raise ValueError("volunteer_adaptation_r6_validation_inventory_changed")

    audit_path = pilot_root / audit_module.AUDIT_NAME
    audit_report = _read_json(audit_path, "crop_audit")
    if audit_report.get("version") != audit_module.AUDIT_VERSION:
        raise ValueError("volunteer_adaptation_crop_audit_version_changed")
    if audit_report.get("crop_manifest_sha256") != manifest.sha256:
        raise ValueError("volunteer_adaptation_crop_audit_uses_a_different_manifest")
    volunteer_rows, volunteer_summary = _volunteer_rows(manifest, baseline, audit_report)
    mixture = _mixture_rows(study, volunteer_rows, recipe)
    mixture_bytes = "".join(json.dumps(row, sort_keys=True) + "\n" for row in mixture).encode()
    volunteer_share = len(volunteer_rows) / len(mixture)
    prepared = {
        "version": PREPARED_VERSION,
        "status": "prepared_not_trained",
        "candidate": recipe.candidate,
        "capability_status": CAPABILITY_STATUS,
        "selection_rule": SELECTION_RULE,
        "writer_role": DEVELOPMENT_ROLE,
        "holdout_role": HOLDOUT_ROLE,
        "holdout_status": "unavailable_w06_w09_not_supplied",
        "config": recipe.record,
        "frozen_winner": {
            "winner_name": frozen["winner_name"],
            "winner_document_sha256": frozen["winner_document_sha256"],
            "checkpoint_path": frozen["checkpoint_path"],
            "checkpoint_sha256": frozen["checkpoint_sha256"],
            "pass_index": frozen["pass_index"],
            "composition": frozen["composition"],
            "bundle_path": frozen["bundle"]["path"],
            "bundle_files": frozen["bundle"]["files"],
            "initialization": frozen["initialization"],
        },
        "pilot": {
            "run": str(pilot_root),
            "crop_manifest_sha256": manifest.sha256,
            "predictions_sha256": digest_file(pilot_root / PREDICTIONS_NAME),
            "report_sha256": digest_file(pilot_root / REPORT_NAME),
            "baseline_accuracy": pilot_report["samples"]["accuracy"],
            "baseline_correct": pilot_report["samples"]["correct"],
            "baseline_note": (
                "50.77% is the frozen five-writer pilot result and is not evidence of "
                "improvement after adaptation"
            ),
        },
        "staging": {
            "path": str(study.staging),
            "mapping_name": rows_module.DEFAULT_MAPPING,
            "mapping_sha256": study.mapping_sha256,
            "splits": lineage["splits"],
        },
        "mixture": {
            "sha256": hashlib.sha256(mixture_bytes).hexdigest(),
            "samples": len(mixture),
            "r6_train_samples": len(mixture) - len(volunteer_rows),
            "volunteer_samples": len(volunteer_rows),
            "volunteer_share": volunteer_share,
            "volunteer_share_cap": recipe.volunteer_max_share,
            "deterministic_order": "per_pass_hash_of_(seed,epoch,sample_id)",
            "synthetic_variants_counted_as_samples": False,
            "volunteer": volunteer_summary,
        },
        "validation": {
            "samples": len(study.validation),
            "forgetting_floor_correct": recipe.forgetting_floor_correct,
            "role": "clean_controlled_forgetting_guard_not_volunteer_evidence",
        },
        "model_execution": False,
        "training_performed": False,
    }
    output_plan(root, prepared, recipe)
    written = 0
    written += _write_new_or_identical(root, MIXTURE_NAME, mixture_bytes)
    written += _write_new_or_identical(root, PREPARED_NAME, _json_bytes(prepared))
    report = {
        "status": "prepared_not_trained" if written else "verified_existing_preparation",
        "run": str(root),
        "mixture_samples": len(mixture),
        "volunteer_samples": len(volunteer_rows),
        "volunteer_excluded": len(volunteer_summary["excluded_samples"]),
        "volunteer_share": volunteer_share,
        "validation_samples": len(study.validation),
        "forgetting_floor_correct": recipe.forgetting_floor_correct,
        "files_written": written,
        "model_execution": False,
        "training_performed": False,
        "next_stage": "owner_train",
    }
    return report


def _load_mixture(root: Path) -> list[dict[str, Any]]:
    path = root / MIXTURE_NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_adaptation_mixture_missing_or_unsafe")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"volunteer_adaptation_mixture_blank_line:{number}")
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("kind") not in {"r6", "volunteer"}:
            raise ValueError(f"volunteer_adaptation_mixture_row_shape:{number}")
        rows.append(value)
    return rows


def verify_prepared(
    run: Path,
    *,
    pilot_run: Path,
    staging: Path,
    frozen_run: Path = frozen_module.FROZEN_RUN,
    config: AdaptationConfig | None = None,
    verify_images: bool = True,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], Any]:
    """Re-verify every dataset input before the owner stage loads a model."""
    recipe = config or load_config()
    root = private_run_directory(run)
    prepared = _read_json(root / PREPARED_NAME, "prepared_receipt")
    if prepared.get("version") != PREPARED_VERSION:
        raise ValueError("volunteer_adaptation_prepared_version_changed")
    if prepared.get("status") not in {"prepared_not_trained"}:
        raise ValueError("volunteer_adaptation_prepared_status_changed")
    if prepared.get("model_execution") is not False:
        raise ValueError("volunteer_adaptation_prepared_claims_model_work")
    if prepared.get("config") != recipe.record:
        raise ValueError("volunteer_adaptation_config_changed_after_preparation")
    if prepared.get("frozen_winner", {}).get("checkpoint_sha256") != (
        frozen_module.WINNER_CHECKPOINT_SHA256
    ):
        raise ValueError("volunteer_adaptation_wrong_r6_checkpoint")
    frozen = frozen_module.verify_frozen_winner(frozen_run)
    recorded_winner = prepared.get("frozen_winner", {})
    if (
        frozen["checkpoint_sha256"] != recorded_winner.get("checkpoint_sha256")
        or frozen["winner_name"] != recorded_winner.get("winner_name")
        or frozen["winner_document_sha256"] != recorded_winner.get("winner_document_sha256")
    ):
        raise ValueError("volunteer_adaptation_frozen_winner_changed_after_preparation")
    pilot_root, manifest, _, baseline = comparison_module.verify_pilot(pilot_run)
    if prepared.get("pilot", {}).get("crop_manifest_sha256") != manifest.sha256:
        raise ValueError("volunteer_adaptation_pilot_manifest_changed")
    rows = _load_mixture(root)
    mixture = prepared.get("mixture", {})
    mixture_bytes = (root / MIXTURE_NAME).read_bytes()
    if hashlib.sha256(mixture_bytes).hexdigest() != mixture.get("sha256"):
        raise ValueError("volunteer_adaptation_mixture_hash_changed")
    if len(rows) != mixture.get("samples"):
        raise ValueError("volunteer_adaptation_mixture_sample_count_changed")
    if sum(1 for row in rows if row["kind"] == "volunteer") != mixture.get("volunteer_samples"):
        raise ValueError("volunteer_adaptation_volunteer_count_changed")
    by_id = {sample.sample_id: sample for sample in manifest.samples}
    clean = {
        sample_id
        for sample_id, record in audit_module.classify(baseline).items()
        if not record["crop_defect"]
    }
    volunteer_ids = set()
    for row in rows:
        if row["kind"] != "volunteer":
            continue
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str):
            raise ValueError("volunteer_adaptation_volunteer_row_lost_its_sample_id")
        sample = by_id.get(sample_id)
        if sample is None or sample.sample_id not in clean:
            raise ValueError("volunteer_adaptation_volunteer_sample_is_not_audit_clean")
        if row["crop_sha256"] != sample.crop_sha256 or row["label"] != sample.truth:
            raise ValueError("volunteer_adaptation_volunteer_row_changed")
        volunteer_ids.add(sample.sample_id)
        if verify_images:
            path = pilot_root / sample.crop_path
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"volunteer_adaptation_crop_missing:{sample.sample_id}")
            if digest_file(path) != sample.crop_sha256:
                raise ValueError(f"volunteer_adaptation_crop_changed:{sample.sample_id}")
    if volunteer_ids != clean:
        raise ValueError("volunteer_adaptation_volunteer_inventory_changed")
    from research.english.r6_model import rows as rows_module

    study = rows_module.load_study(Path(staging), verify_bytes=verify_images)
    expected = prepared.get("staging", {}).get("splits", {})
    _compare_split_lineage(study.lineage()["splits"]["train"], expected["train"], "r6_training")
    _compare_split_lineage(
        study.lineage()["splits"]["validation"], expected["validation"], "r6_validation"
    )
    output_plan(root, prepared, recipe)
    return root, prepared, rows, study


# ------------------------------------------------------------------------ scoring


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("volunteer_adaptation_percentile_requires_values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    correct = sum(record["prediction"] == record["truth"] for record in records)
    folded = sum(
        len(record["prediction"]) == 1
        and record["prediction"].casefold() == record["truth"].casefold()
        for record in records
    )
    return {
        "samples": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "casefolded_correct": folded,
        "casefolded_shape_accuracy": folded / total if total else None,
        "case_only_errors": folded - correct,
        "empty_predictions": sum(not record["prediction"].strip() for record in records),
    }


def score_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact, case-sensitive scoring of the R6 validation split, folded reported apart."""
    if not records:
        raise ValueError("volunteer_adaptation_scoring_requires_records")
    overall = _summary(records)
    by_writer = {
        writer: _summary([record for record in records if record["writer_id"] == writer])
        for writer in sorted({str(record["writer_id"]) for record in records})
    }
    by_case = {
        name: _summary([record for record in records if record["group"] == group])
        for name, group in (("uppercase", "upper"), ("lowercase", "lower"))
    }
    return {
        **overall,
        "total": overall["samples"],
        "by_writer": by_writer,
        "by_case": by_case,
        "accuracy_definition": "exact_case_sensitive_single_letter_match",
        "normalization": "none_raw_exact_no_trimming_no_case_change_no_language_correction",
    }


def select_pass(results: Sequence[Mapping[str, Any]], *, floor_correct: int) -> dict[str, Any]:
    """Fixed rule: forgetting guard, then validation exact, then lower pass index."""
    if not results:
        raise ValueError("volunteer_adaptation_selection_requires_a_pass")
    eligible = [result for result in results if result["samples"]["correct"] >= floor_correct]
    ordered = sorted(
        eligible,
        key=lambda result: (-result["samples"]["correct"], result["pass_index"]),
    )
    ranking = [
        {
            "pass_index": result["pass_index"],
            "validation_correct": result["samples"]["correct"],
            "validation_accuracy": result["samples"]["accuracy"],
            "passes_forgetting_guard": result["samples"]["correct"] >= floor_correct,
        }
        for result in sorted(results, key=lambda result: result["pass_index"])
    ]
    if not ordered:
        return {
            "rule": SELECTION_RULE,
            "forgetting_floor_correct": floor_correct,
            "ranking": ranking,
            "selected_pass": None,
            "status": "no_candidate_passed_the_forgetting_guard",
        }
    winner = ordered[0]
    return {
        "rule": SELECTION_RULE,
        "forgetting_floor_correct": floor_correct,
        "ranking": ranking,
        "selected_pass": winner["pass_index"],
        "selected_checkpoint": winner.get("checkpoint_sha256"),
        "selected_validation_correct": winner["samples"]["correct"],
        "status": "candidate_selected_on_validation",
    }


# --------------------------------------------------------------- owner-only engine


class AdaptationError(RuntimeError):
    """A bounded run refused or stopped before it could publish a candidate."""


@dataclass(frozen=True)
class StepPlan:
    """Separate one pass's accumulation count from the full scheduler horizon."""

    batches_per_pass: int
    optimizer_steps_per_pass: int
    total_optimizer_steps: int


def step_plan(samples: int, config: AdaptationConfig) -> StepPlan:
    """Count partial final batches and accumulations without loading a framework."""
    if samples < 1:
        raise AdaptationError("volunteer_adaptation_step_plan_requires_samples")
    batches = (samples + config.batch_size - 1) // config.batch_size
    steps = (batches + config.accumulate_batches - 1) // config.accumulate_batches
    return StepPlan(batches, steps, steps * config.pass_limit)


def output_plan(
    root: Path, prepared: Mapping[str, Any], config: AdaptationConfig
) -> dict[str, int | str]:
    """Bound retained full pass states plus one model-only selected state, without ML."""
    from research.english.r6_model.checkpoint import (
        estimate_full_state_bytes,
        estimate_selected_state_bytes,
    )

    inventory = prepared["frozen_winner"]["initialization"]["trainable_inventory"]
    full = estimate_full_state_bytes(inventory)
    selected = estimate_selected_state_bytes(inventory)
    # Count existing non-checkpoint evidence and reserve another 10 MB for logs/reports.
    # Retained checkpoints are counted by the fixed plan, so resume cannot double count.
    evidence = sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] not in {CHECKPOINT_DIR, "training"}
    )
    peak = config.pass_limit * full + selected + evidence + 10_000_000
    if peak > config.max_output_bytes:
        raise AdaptationError("volunteer_adaptation_checkpoint_plan_exceeds_output_limit")
    return {
        "policy": "retained_full_pass_states_plus_one_model_only_winner",
        "full_state_bytes": full,
        "selected_state_bytes": selected,
        "estimated_peak_bytes": peak,
        "max_output_bytes": config.max_output_bytes,
    }


def _checked_pass_batches(
    rows: Sequence[Mapping[str, Any]], config: AdaptationConfig, epoch: int, plan: StepPlan
) -> list[list[dict[str, Any]]]:
    from research.english.r5_model import batching

    groups = batching.epoch_batches(rows, config.batch_size, config.seed, epoch)
    observed_steps = (len(groups) + config.accumulate_batches - 1) // config.accumulate_batches
    if (
        len(groups) != plan.batches_per_pass
        or observed_steps != plan.optimizer_steps_per_pass
        or any(len(group) != config.batch_size for group in groups[:-1])
        or not groups
        or len(groups[-1]) != (len(rows) - 1) % config.batch_size + 1
    ):
        raise AdaptationError("volunteer_adaptation_step_plan_changed")
    batching.verify_coverage(rows, groups)
    return groups


@contextmanager
def _pass_execution(root: Path) -> Iterator[None]:
    """Keep pass failures visible without leaving a caught refusal marked active."""
    marker = root / "run-in-progress.json"
    _atomic_write(marker, _json_bytes({"status": "in_progress"}))
    try:
        yield
    except BaseException as error:
        _atomic_write(
            marker,
            _json_bytes(
                {
                    "status": "stopped",
                    "reason": str(error) or type(error).__name__,
                    "automatic_retry": False,
                    "optimizer_steps": None,
                    "resume_granularity": "pass_boundary_only",
                }
            ),
        )
        raise


@dataclass
class _Budget:
    """Cooperative wall/RSS/output guard; aborts before the ceilings are exceeded."""

    pass_seconds: float
    validation_seconds: float
    run_seconds: float
    rss_mb: float
    output_bytes: int
    root: Path
    started: float = field(default_factory=time.monotonic)
    prior_seconds: float = 0.0
    phase_started: float = field(default_factory=time.monotonic)
    phase_limit: float = 0.0
    last_disk_check: float = field(default_factory=time.monotonic)

    def begin_phase(self, seconds: float) -> None:
        self.phase_limit = seconds
        self.phase_started = time.monotonic()

    def reserve_output(self, additional_bytes: int) -> None:
        from research.english.r6_model.budgets import directory_bytes

        if directory_bytes(self.root) + additional_bytes > self.output_bytes:
            raise AdaptationError("volunteer_adaptation_output_limit_exceeded_before_save")

    def check(self, *, pass_index: int | None = None) -> None:
        from research.english.r6_model.budgets import directory_bytes, process_peak_rss_mb

        now = time.monotonic()
        if now - self.phase_started > self.phase_limit:
            raise AdaptationError("volunteer_adaptation_phase_wall_limit_exceeded")
        if now - self.started + self.prior_seconds > self.run_seconds:
            raise AdaptationError("volunteer_adaptation_run_wall_limit_exceeded")
        if pass_index is not None and now - self.phase_started > self.pass_seconds:
            raise AdaptationError("volunteer_adaptation_pass_wall_limit_exceeded")
        if process_peak_rss_mb() > self.rss_mb:
            raise AdaptationError("volunteer_adaptation_rss_limit_exceeded")
        # Walking the run directory on every optimizer step is needlessly expensive.
        if now - self.last_disk_check >= 30.0:
            self.last_disk_check = now
            if directory_bytes(self.root) > self.output_bytes:
                raise AdaptationError("volunteer_adaptation_output_limit_exceeded")


class _OwnerEngine:
    """Lazily binds Torch, the pinned runtime and the R6 delta. Never imported at module load."""

    def __init__(self) -> None:
        self.torch: Any = None
        self.model: Any = None
        self.tokenizer: Any = None
        self.processor: Any = None
        self.optimizer: Any = None
        self.named: list[tuple[str, Any]] = []
        self.recipe: Any = None
        self.inventory: dict[str, Any] = {}

    def setup(self, prepared: Mapping[str, Any], config: AdaptationConfig) -> None:
        import random

        import torch

        from research.english.r5_model import parameters as parameters_module
        from research.english.r6_model import initialization as initialization_module
        from research.english.r6_model.recipe import load_recipe
        from research.english.r6_model.runtime import ContinuationRuntime

        frozen = prepared["frozen_winner"]
        random.seed(config.seed)
        torch.manual_seed(config.seed)
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        torch.use_deterministic_algorithms(True)
        runtime = ContinuationRuntime(Path(frozen["bundle_path"]))
        self.tokenizer = runtime.tokenizer()
        self.processor = runtime.processor()
        model, _, inventory = runtime.load_composition(frozen["initialization"], torch=torch)
        # Second gate: the verified composition must be the frozen winner's own.
        initialization_module.verify_reconstruction_against_inventory(
            inventory, metadata=frozen["initialization"]
        )
        checkpoint = Path(prepared["frozen_winner"]["checkpoint_path"])
        if digest_file(checkpoint / "state.safetensors") != frozen["checkpoint_sha256"]:
            raise AdaptationError("volunteer_adaptation_selected_checkpoint_changed")
        state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
        if (
            state.get("pass_index") != frozen["pass_index"]
            or state.get("weights_only") is not True
            or state.get("tensors_sha256") != frozen["checkpoint_sha256"]
            or state.get("trainable_inventory") != inventory
        ):
            raise AdaptationError("volunteer_adaptation_selected_state_disagrees_with_inventory")
        tensors = initialization_module.read_delta_tensors(checkpoint / "state.safetensors")
        initialization_module.apply_delta(torch, model, tensors, inventory)
        del tensors
        parameters_module.set_eval_mode(model)
        self.named = parameters_module.named_trainable(model)
        self.optimizer = torch.optim.AdamW(
            [parameter for _, parameter in self.named],
            lr=config.learning_rate,
            weight_decay=config.weight_decay,
        )
        self.model = model
        self.torch = torch
        self.recipe = load_recipe(R6_RECIPE_NAME)
        self.inventory = {
            name: {
                "dtype": str(parameter.dtype).removeprefix("torch."),
                "shape": list(parameter.shape),
            }
            for name, parameter in self.named
        }

    def _volunteer_pixels(
        self, pilot_root: Path, row: Mapping[str, Any], config: AdaptationConfig, epoch: int
    ):
        from PIL import Image

        from research.english.r5_model.preprocess import aspect_preserving_pad

        path = pilot_root / str(row["path"])
        if path.is_symlink() or not path.is_file():
            raise AdaptationError(f"volunteer_adaptation_crop_missing:{row['id']}")
        if digest_file(path) != row["crop_sha256"]:
            raise AdaptationError(f"volunteer_adaptation_crop_changed:{row['id']}")
        with Image.open(path) as image:
            image.load()
            value = image.convert("RGB")
        plan = capture_module.plan_for(config.capture, sample_id=str(row["id"]), epoch=epoch)
        value = capture_module.apply(value, plan)
        padded = aspect_preserving_pad(value)
        size = getattr(self.processor, "size", None)
        if not isinstance(size, dict) or size.get("height") != 384 or size.get("width") != 384:
            raise AdaptationError("volunteer_adaptation_processor_canvas_changed")
        return self.processor(images=padded, return_tensors="pt", do_resize=False).pixel_values

    def training_pixels(
        self,
        row: Mapping[str, Any],
        *,
        staging: Path,
        pilot_root: Path,
        config: AdaptationConfig,
        epoch: int,
    ):
        from research.english.r6_model import preprocess as r6_preprocess

        if row["kind"] == "r6":
            return r6_preprocess.training_pixels(
                self.processor, staging, dict(row), self.recipe, epoch=epoch
            )
        return self._volunteer_pixels(pilot_root, row, config, epoch)

    def save(
        self,
        directory: Path,
        *,
        pass_index: int,
        data_epoch: int,
        step: int,
        weights_only: bool = False,
    ) -> dict[str, Any]:
        from safetensors.torch import save_file

        if directory.exists() or directory.is_symlink():
            raise AdaptationError("volunteer_adaptation_checkpoint_destination_exists")
        directory.mkdir(parents=True, mode=0o700)
        tensors = {
            f"model.{name}": parameter.detach().to("cpu").contiguous().clone()
            for name, parameter in self.named
        }
        if not weights_only:
            for name, parameter in self.named:
                entry = self.optimizer.state.get(parameter)
                if entry is None:
                    continue
                for key in ("exp_avg", "exp_avg_sq", "step"):
                    if key in entry:
                        tensors[f"optimizer.{name}.{key}"] = (
                            entry[key].detach().to("cpu").contiguous().clone()
                        )
        weights = directory / "state.safetensors"
        save_file(tensors, str(weights))
        import random

        metadata = {
            "version": "penforge-volunteer-adaptation-state-v1",
            "status": "pass_complete",
            "candidate": CANDIDATE,
            "pass_index": pass_index,
            "data_epoch": data_epoch,
            "optimizer_step": step,
            "tensors_sha256": digest_file(weights),
            "trainable_inventory": self.inventory,
            "tensor_count": len(tensors),
            "weights_only": weights_only,
            "torch_rng": self.torch.get_rng_state().tolist(),
            "python_rng_state": list(random.getstate()[1]),
        }
        _atomic_write(directory / "state.json", _json_bytes(metadata))
        return metadata

    def publish_selected(
        self, source: Path, destination: Path, *, expected_pass: int, expected_sha256: str
    ) -> dict[str, Any]:
        """Restore the selected pass, then save only its model tensors for inference."""
        metadata = self.load(source, expected_pass=expected_pass)
        if metadata["tensors_sha256"] != expected_sha256:
            raise AdaptationError("volunteer_adaptation_selected_source_changed")
        selected = self.save(
            destination,
            pass_index=expected_pass,
            data_epoch=metadata["data_epoch"],
            step=metadata["optimizer_step"],
            weights_only=True,
        )
        selected["source_state_sha256"] = expected_sha256
        _atomic_write(destination / "state.json", _json_bytes(selected))
        return selected

    def load(self, directory: Path, *, expected_pass: int) -> dict[str, Any]:
        from safetensors.torch import load_file

        metadata = _read_json(directory / "state.json", "adaptation_state")
        if (
            metadata.get("version") != "penforge-volunteer-adaptation-state-v1"
            or metadata.get("status") != "pass_complete"
            or metadata.get("pass_index") != expected_pass
            or metadata.get("candidate") != CANDIDATE
            or metadata.get("trainable_inventory") != self.inventory
        ):
            raise AdaptationError("volunteer_adaptation_resume_state_changed")
        weights = directory / "state.safetensors"
        if digest_file(weights) != metadata.get("tensors_sha256"):
            raise AdaptationError("volunteer_adaptation_resume_weights_changed")
        tensors = load_file(str(weights), device="cpu")
        for name, parameter in self.named:
            value = tensors.get(f"model.{name}")
            if value is None or list(value.shape) != list(parameter.shape):
                raise AdaptationError("volunteer_adaptation_resume_tensor_changed")
            with self.torch.no_grad():
                parameter.copy_(value)
            entry = self.optimizer.state.setdefault(parameter, {})
            for key in ("exp_avg", "exp_avg_sq", "step"):
                stored = tensors.get(f"optimizer.{name}.{key}")
                if stored is not None:
                    entry[key] = stored.clone()
        self.torch.set_rng_state(self.torch.tensor(metadata["torch_rng"], dtype=self.torch.uint8))
        return metadata


def _train_one_pass(
    engine: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    staging: Path,
    pilot_root: Path,
    config: AdaptationConfig,
    epoch: int,
    pass_index: int,
    base_step: int,
    expected_steps_per_pass: int,
    planned_total_steps: int,
    budget: _Budget,
    progress: Callable[[str], None] | None,
) -> dict[str, Any]:
    """One deterministic pass; every row is used exactly once."""
    from research.english.r5_model import batching as batching_module
    from research.english.r5_model import objective as objective_module
    from research.english.r5_model import scheduler as scheduler_module

    plan = step_plan(len(rows), config)
    if (
        expected_steps_per_pass != plan.optimizer_steps_per_pass
        or planned_total_steps != plan.total_optimizer_steps
        or not 1 <= pass_index <= config.pass_limit
        or base_step != (pass_index - 1) * plan.optimizer_steps_per_pass
    ):
        raise AdaptationError("volunteer_adaptation_step_plan_changed")
    groups = _checked_pass_batches(rows, config, epoch, plan)
    steps_per_pass = plan.optimizer_steps_per_pass
    torch = engine.torch
    model = engine.model
    optimizer = engine.optimizer
    seen: list[str] = []
    volunteer_seen = 0
    losses: list[float] = []
    optimizer_steps = 0
    started = time.monotonic()
    log_path = budget.root / f"pass-{pass_index:04d}-loss-private.jsonl"
    with log_path.open("w", encoding="utf-8") as log:
        for start in range(0, len(groups), config.accumulate_batches):
            accumulation = groups[start : start + config.accumulate_batches]
            budget.check(pass_index=pass_index)
            optimizer.zero_grad(set_to_none=True)
            combined = 0.0
            for group in accumulation:
                labels = batching_module.padded_label_ids(
                    torch,
                    engine.tokenizer,
                    [row["label"] for row in group],
                    config.max_label_tokens,
                )
                values = torch.cat(
                    [
                        engine.training_pixels(
                            row,
                            staging=staging,
                            pilot_root=pilot_root,
                            config=config,
                            epoch=epoch,
                        )
                        for row in group
                    ],
                    dim=0,
                )
                loss = objective_module.teacher_forced_loss(torch, model, values, labels)
                value = float(loss.detach())
                losses.append(value)
                combined += value / len(accumulation)
                (loss / len(accumulation)).backward()
                seen.extend(row["id"] for row in group)
                volunteer_seen += sum(1 for row in group if row["kind"] == "volunteer")
            torch.nn.utils.clip_grad_norm_(
                [parameter for _, parameter in engine.named],
                config.clip_grad_norm,
                error_if_nonfinite=True,
            )
            optimizer_steps += 1
            step = base_step + optimizer_steps
            multiplier = scheduler_module.learning_rate_multiplier(
                step,
                total_steps=planned_total_steps,
                warmup_steps=config.warmup_optimizer_steps,
                final_ratio=config.final_learning_rate_ratio,
            )
            scheduler_module.apply_multiplier(optimizer, config.learning_rate, multiplier)
            optimizer.step()
            log.write(
                json.dumps(
                    {
                        "step": step,
                        "pass": pass_index,
                        "data_epoch": epoch,
                        "loss": combined,
                        "learning_rate": config.learning_rate * multiplier,
                    }
                )
                + "\n"
            )
            if progress is not None and (
                optimizer_steps % 64 == 0 or optimizer_steps == steps_per_pass
            ):
                progress(
                    json.dumps(
                        {
                            "stage": "volunteer-adapt-train",
                            "pass_index": pass_index,
                            "completed_steps": optimizer_steps,
                            "total_steps": steps_per_pass,
                            "completed_samples": len(seen),
                            "total_samples": len(rows),
                        },
                        sort_keys=True,
                    )
                )
    batching_module.verify_coverage(rows, groups)
    if len(seen) != len(rows) or len(set(seen)) != len(rows):
        raise AdaptationError("volunteer_adaptation_pass_did_not_use_every_row_once")
    return {
        "pass_index": pass_index,
        "data_epoch": epoch,
        "status": "complete_one_pass",
        "samples": len(seen),
        "volunteer_samples": volunteer_seen,
        "r6_samples": len(seen) - volunteer_seen,
        "batches": len(groups),
        "optimizer_steps": optimizer_steps,
        "first_loss": losses[0] if losses else None,
        "last_loss": losses[-1] if losses else None,
        "mean_loss": sum(losses) / len(losses) if losses else None,
        "wall_seconds": time.monotonic() - started,
        "synthetic_variants_counted_as_samples": False,
        "encoder_frozen": True,
    }


def _validate(
    engine: Any,
    validation_rows: Sequence[Mapping[str, Any]],
    *,
    staging: Path,
    config: AdaptationConfig,
    pass_index: int,
    budget: _Budget,
    progress: Callable[[str], None] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One greedy, unaugmented pass over the untouched R6 validation split."""
    from research.english.r6_model import preprocess as r6_preprocess

    torch = engine.torch
    model = engine.model
    model.eval()
    inference_batches = 8
    ordered = sorted(validation_rows, key=lambda row: str(row["id"]))
    records: list[dict[str, Any]] = []
    latencies: list[float] = []
    truncated = 0
    started = time.monotonic()
    for start in range(0, len(ordered), inference_batches):
        budget.check()
        group = ordered[start : start + inference_batches]
        tick = time.monotonic()
        values = torch.cat(
            [
                r6_preprocess.validation_pixels(engine.processor, staging, dict(row))
                for row in group
            ],
            dim=0,
        )
        with torch.inference_mode():
            generated = model.generate(
                pixel_values=values,
                do_sample=False,
                num_beams=1,
                max_new_tokens=config.max_label_tokens,
                use_cache=True,
            )
        elapsed = (time.monotonic() - tick) * 1000 / len(group)
        for row, ids in zip(group, generated.tolist(), strict=True):
            text = engine.tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            truncated += int(engine.tokenizer.eos_token_id not in ids[1:])
            records.append(
                {
                    "sample_id": row["id"],
                    "writer_id": row["writer"],
                    "group": "upper" if row["label"].isupper() else "lower",
                    "truth": row["label"],
                    "prediction": text,
                }
            )
            latencies.append(elapsed)
        if progress is not None and (len(records) % 512 == 0 or len(records) == len(ordered)):
            progress(
                json.dumps(
                    {
                        "stage": "volunteer-adapt-validate",
                        "pass_index": pass_index,
                        "completed_samples": len(records),
                        "total_samples": len(ordered),
                    },
                    sort_keys=True,
                )
            )
    stats = {
        "samples": len(records),
        "wall_seconds": time.monotonic() - started,
        "generation_limit_hits": truncated,
        "per_crop_ms_p50": _percentile(latencies, 0.5),
        "per_crop_ms_p95": _percentile(latencies, 0.95),
        "validation_preprocessing": "pinned_upstream_vit_rgb_resize_384_v1",
        "decoding": "greedy_no_lexicon_no_language_correction",
    }
    return records, stats


def run(
    run: Path,
    *,
    owner_run: bool,
    pilot_run: Path,
    staging: Path,
    resume: bool = False,
    frozen_run: Path = frozen_module.FROZEN_RUN,
    config: AdaptationConfig | None = None,
    engine: Any | None = None,
    train_pass: Callable[..., dict[str, Any]] | None = None,
    validate_pass: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Owner-only bounded adaptation. Refuses without ``owner_run=True``."""
    if not owner_run:
        raise ValueError("volunteer_adaptation_requires_owner_run_acknowledgment")
    recipe = config or load_config()
    root, prepared, mixture, study = verify_prepared(
        run, pilot_run=pilot_run, staging=staging, frozen_run=frozen_run, config=recipe
    )
    if (root / REPORT_NAME_OUT).is_file():
        existing = _read_json(root / REPORT_NAME_OUT, "adaptation_report")
        if existing.get("status") == "training_complete_candidate_selected":
            raise FileExistsError("volunteer_adaptation_run_already_completed_use_a_new_directory")
    if not resume and (
        any(root.glob("pass-*-loss-private.jsonl"))
        or (root / CHECKPOINT_DIR).exists()
        or (root / "pass-results.json").exists()
    ):
        raise FileExistsError("volunteer_adaptation_partial_run_requires_resume_or_new_directory")
    if resume and not any((root / CHECKPOINT_DIR).glob("pass-*/state.json")):
        raise AdaptationError("volunteer_adaptation_resume_requires_a_complete_pass")
    pilot_root = Path(prepared["pilot"]["run"]).resolve()
    validation_rows = list(study.validation)
    storage = output_plan(root, prepared, recipe)
    plan = step_plan(len(mixture), recipe)
    steps_per_pass = plan.optimizer_steps_per_pass
    # Refuse a changed grouping before the model loads or any active marker is written.
    for pass_index in range(1, recipe.pass_limit + 1):
        _checked_pass_batches(mixture, recipe, recipe.data_epoch_offset + pass_index, plan)
    started = time.monotonic()
    started_cpu = time.process_time()
    budget = _Budget(
        pass_seconds=recipe.max_pass_wall_seconds,
        validation_seconds=recipe.max_validation_wall_seconds,
        run_seconds=recipe.max_run_wall_seconds,
        rss_mb=recipe.max_rss_mb,
        output_bytes=recipe.max_output_bytes,
        root=root,
    )
    active_engine = engine if engine is not None else _OwnerEngine()
    active_engine.setup(prepared, recipe)
    trainer = _train_one_pass if train_pass is None else train_pass
    validator = _validate if validate_pass is None else validate_pass
    passes: list[dict[str, Any]] = []
    start_pass = 1
    if resume:
        completed = (
            sorted((root / CHECKPOINT_DIR).glob("pass-*"))
            if (root / CHECKPOINT_DIR).is_dir()
            else []
        )
        if not completed:
            raise AdaptationError("volunteer_adaptation_resume_requires_a_complete_pass")
        latest = completed[-1]
        index = int(latest.name.split("-", 1)[1])
        active_engine.load(latest, expected_pass=index)
        start_pass = index + 1
        passes = list(_read_json(root / "pass-results.json", "pass_results").get("passes", []))
        if progress is not None:
            progress(
                json.dumps(
                    {"stage": "volunteer-adapt-resume", "resumed_from_pass": index},
                    sort_keys=True,
                )
            )
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "status": "running",
        "candidate": recipe.candidate,
        "capability_status": CAPABILITY_STATUS,
        "qualified_for_product": False,
        "product_qualification": "none",
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "selection_rule": SELECTION_RULE,
        "model_execution": True,
        "training_performed": True,
        "test_evaluated": False,
        "development_writers": list(WRITERS),
        "writer_role": DEVELOPMENT_ROLE,
        "holdout": {"role": HOLDOUT_ROLE, "status": "unavailable_w06_w09_not_supplied"},
        "limitations": list(LIMITATIONS),
        "config": recipe.record,
        "output_plan": storage,
        "data": {
            "mixture_sha256": prepared["mixture"]["sha256"],
            "mixture_samples": prepared["mixture"]["samples"],
            "volunteer_samples": prepared["mixture"]["volunteer_samples"],
            "volunteer_share": prepared["mixture"]["volunteer_share"],
            "validation_samples": len(validation_rows),
            "pilot": prepared["pilot"],
            "frozen_winner": {
                "checkpoint_sha256": prepared["frozen_winner"]["checkpoint_sha256"],
                "winner_name": prepared["frozen_winner"]["winner_name"],
            },
        },
        "resume": {"requested": resume, "granularity": "pass_boundary_only"},
    }
    with _pass_execution(root):
        for pass_index in range(start_pass, recipe.pass_limit + 1):
            epoch = recipe.data_epoch_offset + pass_index
            budget.begin_phase(recipe.max_pass_wall_seconds)
            training = trainer(
                active_engine,
                mixture,
                staging=study.staging,
                pilot_root=pilot_root,
                config=recipe,
                epoch=epoch,
                pass_index=pass_index,
                base_step=(pass_index - 1) * steps_per_pass,
                expected_steps_per_pass=plan.optimizer_steps_per_pass,
                planned_total_steps=plan.total_optimizer_steps,
                budget=budget,
                progress=progress,
            )
            checkpoint_dir = root / CHECKPOINT_DIR / f"pass-{pass_index:04d}"
            budget.reserve_output(int(storage["full_state_bytes"]))
            checkpoint = active_engine.save(
                checkpoint_dir,
                pass_index=pass_index,
                data_epoch=epoch,
                step=pass_index * steps_per_pass,
            )
            budget.begin_phase(recipe.max_validation_wall_seconds)
            records, timing = validator(
                active_engine,
                validation_rows,
                staging=study.staging,
                config=recipe,
                pass_index=pass_index,
                budget=budget,
                progress=progress,
            )
            predictions_path = root / f"pass-{pass_index:04d}-validation-predictions-private.jsonl"
            _atomic_write(
                predictions_path,
                "".join(json.dumps(record, sort_keys=True) + "\n" for record in records).encode(),
            )
            samples = score_records(records)
            result = {
                "pass_index": pass_index,
                "data_epoch": epoch,
                "checkpoint_sha256": checkpoint["tensors_sha256"],
                "predictions_sha256": digest_file(predictions_path),
                "training": training,
                "timing": timing,
                "samples": samples,
            }
            passes.append(result)
            _atomic_write(
                root / "pass-results.json",
                _json_bytes({"passes": passes, "validation_samples": len(validation_rows)}),
            )
            if progress is not None:
                progress(
                    json.dumps(
                        {
                            "stage": "volunteer-adapt-pass",
                            "pass_index": pass_index,
                            "validation_correct": samples["correct"],
                            "validation_accuracy": samples["accuracy"],
                            "forgetting_floor_correct": recipe.forgetting_floor_correct,
                        },
                        sort_keys=True,
                    )
                )
        selection = select_pass(passes, floor_correct=recipe.forgetting_floor_correct)
        if selection["selected_pass"] is None:
            report["status"] = "training_complete_no_candidate_passed_the_forgetting_guard"
            report["selection"] = selection
            report["passes"] = passes
            report["execution"] = {
                "wall_seconds": time.monotonic() - started,
                "cpu_seconds": time.process_time() - started_cpu,
                "device": "cpu",
                "torch_threads": 2,
            }
            _atomic_write(root / REPORT_NAME_OUT, _json_bytes(report))
            (root / "run-in-progress.json").unlink(missing_ok=True)
            return report
        selected = next(
            result for result in passes if result["pass_index"] == selection["selected_pass"]
        )
        best = root / BEST_DIR
        if best.exists():
            raise FileExistsError("volunteer_adaptation_best_directory_already_exists")
        best.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        budget.reserve_output(int(storage["selected_state_bytes"]))
        published = active_engine.publish_selected(
            root / CHECKPOINT_DIR / f"pass-{selected['pass_index']:04d}",
            best,
            expected_pass=selected["pass_index"],
            expected_sha256=selected["checkpoint_sha256"],
        )
        selection["selected_source_checkpoint"] = selected["checkpoint_sha256"]
        selection["selected_checkpoint"] = published["tensors_sha256"]
        selection["selected_validation_correct"] = selected["samples"]["correct"]
        selection["selected_validation_accuracy"] = selected["samples"]["accuracy"]
        selection["selected_casefolded_shape_accuracy"] = selected["samples"][
            "casefolded_shape_accuracy"
        ]
        _atomic_write(root / SELECTION_NAME, _json_bytes(selection))
        report["status"] = "training_complete_candidate_selected"
        report["selection"] = selection
        report["passes"] = passes
        report["execution"] = {
            "wall_seconds": time.monotonic() - started,
            "cpu_seconds": time.process_time() - started_cpu,
            "device": "cpu",
            "torch_threads": 2,
        }
        report["candidate_checkpoint"] = {
            "path": str(best),
            "files": sorted(item.name for item in best.iterdir()),
            "checkpoint_sha256": published["tensors_sha256"],
            "source_state_sha256": selected["checkpoint_sha256"],
            "weights_only": True,
            "pass_index": selected["pass_index"],
            "composition": (
                "pinned_r5_canonical_base_plus_r5_delta_plus_r6_delta_plus_adaptation_delta"
            ),
            "label": "experimental_volunteer_domain_adaptation",
        }
        report["evaluation_policy"] = (
            "development_writers_are_training_writers; external comparison requires the "
            "frozen w06-w09 holdout and is owner-run"
        )
        _atomic_write(root / REPORT_NAME_OUT, _json_bytes(report))
        (root / "run-in-progress.json").unlink(missing_ok=True)
        return report


def summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compact terminal summary: aggregate values only, never a per-sample prediction."""
    passes = [
        {
            "pass_index": result["pass_index"],
            "validation_correct": result["samples"]["correct"],
            "validation_samples": result["samples"]["samples"],
            "validation_accuracy": result["samples"]["accuracy"],
            "validation_casefolded_shape_accuracy": result["samples"]["casefolded_shape_accuracy"],
            "validation_case_only_errors": result["samples"]["case_only_errors"],
            "mean_loss": result["training"]["mean_loss"],
        }
        for result in report.get("passes", [])
    ]
    return {
        "status": report["status"],
        "candidate": report["candidate"],
        "capability_status": report["capability_status"],
        "product_qualification": report["product_qualification"],
        "passes": passes,
        "selection": report.get("selection"),
        "holdout": report.get("holdout"),
        "training_performed": True,
        "private_sample_results": "written_to_run_directory_only",
    }


__all__ = [
    "AdaptationConfig",
    "AdaptationError",
    "CANDIDATE",
    "CAPABILITY_STATUS",
    "DEVELOPMENT_ROLE",
    "HOLDOUT_ROLE",
    "LIMITATIONS",
    "PREPARED_NAME",
    "PREPARED_VERSION",
    "REPORT_NAME_OUT",
    "REPORT_VERSION",
    "SELECTION_RULE",
    "CaptureSettings",
    "StepPlan",
    "load_config",
    "prepare",
    "output_plan",
    "run",
    "score_records",
    "select_pass",
    "summary",
    "step_plan",
    "verify_prepared",
]
