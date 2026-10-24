"""Deterministic preprocessing variants, crop audit and paired comparison.

Everything here is data-only: no Torch, Transformers or Safetensors is imported, no model
runs and no real photograph is read. The preprocessing comparison is exercised with an
injected fake runtime so the pairing, selection rule and refusal paths are checked
without executing the frozen R6 checkpoint.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from research.english.volunteer import audit as audit_module
from research.english.volunteer import capture as capture_module
from research.english.volunteer import comparison as comparison_module
from research.english.volunteer import frozen as frozen_module
from research.english.volunteer import variants as variants_module
from research.english.volunteer.adaptation import CaptureSettings
from tests.backend.volunteer_fixtures import BASELINE_CORRECT, build_pilot_run

ROOT = Path(__file__).resolve().parents[2]


def _python(code: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{ROOT / 'packages/contracts'}:{ROOT}"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture()
def pilot(tmp_path: Path) -> dict[str, Any]:
    return build_pilot_run(tmp_path / "pilot")


def test_variant_preparation_is_deterministic_and_versioned(
    tmp_path: Path, pilot: dict[str, Any]
) -> None:
    run = tmp_path / "variants"
    first = variants_module.prepare(pilot["run"], run)
    assert first["status"] == "prepared_not_evaluated"
    second = variants_module.prepare(pilot["run"], run)
    assert second["status"] == "verified_existing_variants"
    assert second["files_written"] == 0
    payload = variants_module.load_variant_manifest(run)
    assert payload["variant_ids"] == list(variants_module.VARIANT_IDS)
    assert payload["source_crop_manifest_sha256"] == pilot["manifest_sha256"]
    for spec in variants_module.VARIANTS:
        entries = payload["by_variant"][spec.variant_id]
        assert len(entries) == 260
        for entry in entries:
            assert entry["source_crop_sha256"]
            assert entry["algorithm"] == spec.algorithm
            assert entry["algorithm_version"] == spec.version
            # One parameter set for every writer: nothing is tuned per image.
            assert entry["parameters"] == dict(spec.parameters)


def test_raw_condition_stays_byte_identical(tmp_path: Path, pilot: dict[str, Any]) -> None:
    run = tmp_path / "variants"
    variants_module.prepare(pilot["run"], run)
    payload = variants_module.load_variant_manifest(run)
    for entry in payload["by_variant"]["raw"]:
        assert entry["unchanged_bytes"] is True
        assert entry["crop_sha256"] == entry["source_crop_sha256"]


def test_derived_conditions_change_the_bytes_and_record_provenance(
    tmp_path: Path, pilot: dict[str, Any]
) -> None:
    run = tmp_path / "variants"
    variants_module.prepare(pilot["run"], run)
    payload = variants_module.load_variant_manifest(run)
    for variant_id in ("illumination-normalized", "contrast-normalized", "combined"):
        entries = payload["by_variant"][variant_id]
        assert any(not entry["unchanged_bytes"] for entry in entries)
    ruled = payload["by_variant"]["ruled-line-aware"]
    assert all("removed_rows" in entry["flags"] for entry in ruled)
    measurements = payload["measurements"]
    assert measurements["raw"]["crops_byte_identical_to_baseline"] == 260
    assert measurements["ruled-line-aware"]["samples"] == 260
    assert (
        measurements["ruled-line-aware"]["crops_byte_identical_to_baseline"]
        + measurements["ruled-line-aware"]["crops_with_changed_bytes"]
        == 260
    )


def test_preparation_leaves_the_pilot_untouched(tmp_path: Path, pilot: dict[str, Any]) -> None:
    before = _tree_hashes(pilot["run"])
    audit_module.build(pilot["run"])
    variants_module.prepare(pilot["run"], tmp_path / "variants")
    after = _tree_hashes(pilot["run"])
    # Only the audit's own derived outputs may appear; nothing existing changes.
    added = set(after) - set(before)
    assert added <= {"crop-audit.json"} | {
        f"error-review/{writer}.png" for writer in ("W01", "W02", "W03", "W04", "W05")
    }
    for relative, digest in before.items():
        assert after[relative] == digest


def test_illumination_normalization_is_deterministic_and_keeps_ink() -> None:
    image = Image.new("RGB", (64, 64), (210, 212, 218))
    for x in range(64):
        for y in range(64):
            if 20 <= y <= 40 and x < 8:
                image.putpixel((x, y), (40, 40, 60))
    first = variants_module.illumination_normalized(image)
    second = variants_module.illumination_normalized(image)
    assert first.image.tobytes() == second.image.tobytes()
    assert {"max_gain", "mean_gain", "min_gain"} <= set(first.flags)
    ink_before = image.convert("L").tobytes()
    ink_after = first.image.convert("L").tobytes()
    # Geometry is preserved exactly: the same pixels stay ink-dark.
    assert sum(value < 110 for value in ink_after) == sum(value < 110 for value in ink_before)
    assert min(ink_after) < 120
    assert max(ink_after) > 200


def test_contrast_normalization_guards_a_flat_crop_and_keeps_faint_strokes() -> None:
    flat = Image.new("RGB", (32, 32), (200, 200, 200))
    result = variants_module.contrast_normalized(flat)
    assert result.flags["stretched"] is False
    assert result.image.tobytes() == flat.tobytes()

    image = Image.new("RGB", (32, 32), (240, 240, 240))
    for y in range(4, 28):
        image.putpixel((8, y), (180, 180, 180))  # a faint stroke
    stretched = variants_module.contrast_normalized(image)
    luminance = stretched.image.convert("L").tobytes()
    stroke = luminance[16 * 32 + 8]
    background = luminance[24 * 32 + 24]
    assert stroke < background  # the faint stroke is still darker than the paper


def test_ruled_line_removal_refuses_when_it_would_take_too_many_rows() -> None:
    image = Image.new("RGB", (48, 48), (245, 245, 245))
    for y in range(1, 31):
        for x in range(48):
            image.putpixel((x, y), (60, 60, 70))  # a wide dark band
    for y in range(40, 47):
        image.putpixel((24, y), (30, 30, 40))  # a small glyph below the band
    result = variants_module.ruled_line_suppression(image)
    assert result.flags["refused"] is True
    assert result.flags["reason"] == "too_many_rule_rows"
    assert result.image.tobytes() == image.convert("RGB").tobytes()


def test_capture_plans_are_deterministic_and_bounded(pilot: dict[str, Any]) -> None:
    capture = CaptureSettings(
        seed=7,
        augmentation_probability=1.0,
        max_rotation_degrees=3.0,
        max_shear=0.04,
        min_scale=0.95,
        max_scale=1.05,
        max_translation_fraction=0.015,
        max_perspective_fraction=0.015,
        brightness_min=0.92,
        brightness_max=1.06,
        max_illumination_amplitude=0.05,
        blur_probability=0.4,
        max_blur_radius=0.6,
        jpeg_probability=0.4,
        jpeg_quality_min=80,
        jpeg_quality_max=92,
        ruled_background_probability=0.25,
    )
    first = capture_module.plan_for(capture, sample_id="W01-upper-A", epoch=1)
    again = capture_module.plan_for(capture, sample_id="W01-upper-A", epoch=1)
    assert first == again
    assert first != capture_module.plan_for(capture, sample_id="W01-upper-A", epoch=2)
    assert abs(first.rotate_degrees) <= 3.0
    assert 0.95 <= first.scale <= 1.05
    coefficients = capture_module.affine_coefficients(first, (48, 48))
    determinant = coefficients[0] * coefficients[4] - coefficients[1] * coefficients[3]
    assert determinant > 0  # orientation is preserved: no flips


def test_capture_margin_rule_never_covers_the_ink() -> None:
    image = Image.new("RGB", (48, 48), (244, 242, 236))
    for y in range(14, 34):
        for x in range(10, 30):
            image.putpixel((x, y), (30, 30, 45))
    ink_before = {index for index, value in enumerate(image.convert("L").tobytes()) if value < 110}
    plan = capture_module.CapturePlan(0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, None, 1)
    result = capture_module.apply(image.convert("RGB"), plan)
    ink_after = {index for index, value in enumerate(result.convert("L").tobytes()) if value < 110}
    assert ink_after == ink_before


@pytest.mark.parametrize("size", [(48, 48), (96, 64), (384, 384)])
@pytest.mark.parametrize("amount", [-0.015, 0.015])
def test_perspective_coefficients_map_output_corners_back_to_source(
    size: tuple[int, int], amount: float
) -> None:
    plan = dataclasses.replace(capture_module.IDENTITY, perspective_fraction=amount)
    coefficients = capture_module.perspective_coefficients(plan, size)
    width, height = size
    output_corners = [
        (-amount * width, -amount * height),
        ((1 + amount) * width, -amount * height),
        ((1 + amount) * width, (1 + amount) * height),
        (-amount * width, (1 + amount) * height),
    ]
    input_corners = [(0, 0), (width, 0), (width, height), (0, height)]
    a, b, c, d, e, f, g, h = coefficients
    for (x, y), expected in zip(output_corners, input_corners, strict=True):
        denominator = g * x + h * y + 1
        observed = ((a * x + b * y + c) / denominator, (d * x + e * y + f) / denominator)
        assert observed == pytest.approx(expected, abs=1e-8)


def test_perspective_solver_handles_a_projective_trapezoid() -> None:
    source = [(0.0, 0.0), (96.0, 0.0), (96.0, 64.0), (0.0, 64.0)]
    destination = [(3.0, 2.0), (90.0, 1.0), (100.0, 65.0), (-2.0, 60.0)]
    a, b, c, d, e, f, g, h = capture_module._solve_perspective(source, destination)
    assert abs(g) + abs(h) > 0
    for (x, y), expected in zip(source, destination, strict=True):
        denominator = g * x + h * y + 1
        assert ((a * x + b * y + c) / denominator, (d * x + e * y + f) / denominator) == (
            pytest.approx(expected, abs=1e-8)
        )


def test_perspective_solver_rejects_singular_or_incomplete_points() -> None:
    corners = [(0.0, 0.0), (48.0, 0.0), (48.0, 48.0), (0.0, 48.0)]
    with pytest.raises(ValueError, match="four_points"):
        capture_module._solve_perspective(corners[:3], corners[:3])
    with pytest.raises(ValueError, match="singular"):
        capture_module._solve_perspective([(0.0, 0.0)] * 4, corners)


def test_capture_applies_all_augmentation_branches_deterministically() -> None:
    image = Image.new("RGB", (96, 64), (245, 244, 240))
    for y in range(18, 44):
        for x in range(36, 44):
            image.putpixel((x, y), (30, 30, 45))
    original = image.tobytes()
    plan = capture_module.CapturePlan(
        rotate_degrees=2,
        shear=0.02,
        scale=1.01,
        delta_x_fraction=0.01,
        delta_y_fraction=-0.01,
        perspective_fraction=0.015,
        brightness=0.97,
        illumination_amplitude=0.04,
        illumination_direction=1.0,
        blur_radius=0.4,
        jpeg_quality=85,
        ruled_lines=1,
    )
    first = capture_module.apply(image, plan)
    second = capture_module.apply(image, plan)
    assert first.mode == "RGB"
    assert first.size == image.size
    assert first.tobytes() == second.tobytes()
    assert first.tobytes() != original
    assert image.tobytes() == original
    assert min(first.convert("L").tobytes()) < 110


def test_audit_separates_defects_from_recognition_failures(pilot: dict[str, Any]) -> None:
    report = audit_module.build(pilot["run"])
    assert report["samples"] == 260
    assert report["correct"] == BASELINE_CORRECT
    assert report["case_only_errors"] == 39
    assert report["other_errors"] == 260 - BASELINE_CORRECT - 39
    assert set(report["error_categories"]) <= set(audit_module.ALL_CATEGORIES)
    assert report["crop_defect_samples"] + report["clean_crop_samples"] == 260
    assert report["review_is_reviewer_judgment"] is True
    assert report["frozen_pilot_accuracy"] == 0.5076923076923077
    assert report["per_writer"]["W01"]["samples"] == 52
    audit = json.loads((pilot["run"] / audit_module.AUDIT_NAME).read_text(encoding="utf-8"))
    assert audit["baseline_modified"] is False
    assert audit["labels_changed"] == 0
    assert audit["model_execution"] is False


def test_audit_classification_uses_only_the_declared_defect_categories() -> None:
    outcome = audit_module.classify({"W01-upper-A": "A", "W01-upper-B": "b", "W01-upper-C": "Q"})
    assert outcome["W01-upper-A"]["correct"] is True
    assert outcome["W01-upper-B"]["case_only"] is True
    assert outcome["W01-upper-C"]["case_only"] is False
    for record in outcome.values():
        if record["crop_defect"]:
            assert record["category"] in audit_module.CROP_DEFECT_CATEGORIES


def test_comparison_selects_the_fixed_winner_with_a_fake_runtime(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "variants"
    variants_module.prepare(pilot["run"], run)
    baseline: dict[str, str] = dict(pilot["baseline"])
    sample_ids = sorted(baseline)

    def fake_generate_records(
        torch: Any,
        model: Any,
        tokenizer: Any,
        processor: Any,
        samples: Any,
        root: Path,
        *,
        batch_size: int,
        max_new_tokens: int,
        progress: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        variant_id = str(samples[0].crop_path).split("/")[1]
        truth = {sample.sample_id: sample.truth for sample in samples}
        records = []
        for sample_id in sample_ids:
            prediction = baseline[sample_id]
            if variant_id == "illumination-normalized" and sample_id in sample_ids[:8]:
                prediction = truth[sample_id]
            if variant_id == "contrast-normalized" and sample_id in sample_ids[8:24]:
                prediction = truth[sample_id]
            records.append(
                {
                    "sample_id": sample_id,
                    "writer_id": sample_id.split("-", 1)[0],
                    "truth": truth[sample_id],
                    "prediction": prediction,
                }
            )
        return records, {"samples": len(records), "batch_size": batch_size}

    monkeypatch.setattr(comparison_module, "generate_records", fake_generate_records)

    comparison = comparison_module.evaluate(
        run,
        pilot_run=pilot["run"],
        frozen_verifier=lambda path: {
            "inference": dict(frozen_module.INFERENCE),
            "checkpoint_sha256": frozen_module.WINNER_CHECKPOINT_SHA256,
            "winner_name": frozen_module.WINNER_NAME,
            "frozen_run": str(path),
            "pass_index": frozen_module.PASS_INDEX,
            "composition": "pinned_r5_canonical_base_plus_r5_delta_plus_selected_r6_delta",
        },
        torch_factory=lambda seed: object(),
        model_loader=lambda frozen, torch: (object(), object(), object(), {"applied_tensors": 0}),
    )
    assert comparison["baseline"]["accuracy"] == pytest.approx(0.5076923076923077)
    assert comparison["baseline"]["reproduced_by_raw_condition"] is True
    selection = comparison["selection"]
    assert selection["label"] == comparison_module.SELECTION_LABEL
    assert selection["winner"] in variants_module.VARIANT_IDS
    report = json.loads((run / comparison_module.COMPARISON_NAME).read_text(encoding="utf-8"))
    assert report["pilot_modified"] is False
    assert report["training_performed"] is False


def test_comparison_refuses_a_changed_derivative(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "variants"
    variants_module.prepare(pilot["run"], run)
    manifest_path = run / variants_module.MANIFEST_NAME_OUT
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][0]["source_crop_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    monkeypatch.setattr(
        comparison_module,
        "generate_records",
        lambda *args, **kwargs: ([], {"samples": 0}),
    )
    with pytest.raises(ValueError, match="source_hash_changed|entry_count"):
        comparison_module.evaluate(
            run,
            pilot_run=pilot["run"],
            frozen_verifier=lambda path: {
                "inference": dict(frozen_module.INFERENCE),
                "checkpoint_sha256": frozen_module.WINNER_CHECKPOINT_SHA256,
            },
            torch_factory=lambda seed: object(),
            model_loader=lambda frozen, torch: (
                object(),
                object(),
                object(),
                {"applied_tensors": 0},
            ),
        )


def test_preprocessing_stages_import_no_model_framework() -> None:
    result = _python(
        "import sys\n"
        "import research.english.volunteer.audit\n"
        "import research.english.volunteer.capture\n"
        "import research.english.volunteer.comparison\n"
        "import research.english.volunteer.variants\n"
        "print(','.join(sorted(n for n in ('torch', 'transformers', 'safetensors')"
        " if n in sys.modules)))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_selection_rule_is_declared_before_inference() -> None:
    ranked = {
        "raw": {
            "samples": {
                "correct": 132,
                "total": 260,
                "accuracy": 0.5076923076923077,
                "case_only_errors": 39,
                "casefolded_shape_accuracy": 0.6576923076923077,
            }
        },
        "combined": {
            "samples": {
                "correct": 136,
                "total": 260,
                "accuracy": 0.5230769230769231,
                "case_only_errors": 35,
                "casefolded_shape_accuracy": 0.6576923076923077,
            }
        },
    }
    selection = comparison_module.select_winner(ranked)
    assert selection["winner"] == "combined"
    assert selection["rule"] == variants_module.SELECTION_RULE
    assert selection["label"] == "development_selection_on_five_writers_not_independent_evidence"
    tied = dict(ranked)
    tied["combined"] = dict(ranked["raw"])
    assert comparison_module.select_winner(tied)["winner"] == "combined"  # variant id tiebreak


def test_paired_comparison_counts_fixes_and_regressions() -> None:
    baseline = {
        "W01-upper-A": {"sample_id": "W01-upper-A", "truth": "A", "prediction": "a"},
        "W01-upper-B": {"sample_id": "W01-upper-B", "truth": "B", "prediction": "B"},
        "W01-upper-C": {"sample_id": "W01-upper-C", "truth": "C", "prediction": "Q"},
    }
    after = [
        {"sample_id": "W01-upper-A", "truth": "A", "prediction": "A"},
        {"sample_id": "W01-upper-B", "truth": "B", "prediction": "b"},
        {"sample_id": "W01-upper-C", "truth": "C", "prediction": "Q"},
    ]
    paired = comparison_module.compare_records(
        {key: value["prediction"] for key, value in baseline.items()}, after
    )
    assert paired["fixes"] == ["W01-upper-A"]
    assert paired["regressions"] == ["W01-upper-B"]
    assert paired["unchanged_errors"] == ["W01-upper-C"]
    assert paired["net_change"] == 0


def test_comparison_evaluation_requires_the_owner_flag() -> None:
    result = _python(
        "from pathlib import Path\n"
        "import sys\n"
        "from research.english import volunteer_run\n"
        "sys.argv = ['volunteer_run', 'compare', '--run', '/tmp/x']\n"
        "try:\n    volunteer_run.main()\n"
        "except ValueError as error:\n    print(error)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "volunteer_compare_requires_owner_run"


def test_variant_outputs_refuse_symlinked_targets(tmp_path: Path, pilot: dict[str, Any]) -> None:
    run = tmp_path / "variants"
    run.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (run / variants_module.MANIFEST_NAME_OUT).symlink_to(target / "manifest.json")
    with pytest.raises(ValueError, match="must_not_be_symlink"):
        variants_module.prepare(pilot["run"], run)
