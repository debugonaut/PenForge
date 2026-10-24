"""Pure reporters on observed predictions/labels. Empty and human-unreviewed stay null.

Reports use the existing BenchmarkRecord, not a second experiment format. Private
transcriptions and masks never enter reports, errors, stdout or operational events.
"""

import math
import statistics
from collections import defaultdict

from research.models import BenchmarkRecord, Metric
from research.validate import assert_build_eligible, digest, validate_datasets


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = row
    return previous[-1]


def cer(pairs):
    total = sum(len(truth) for truth, _ in pairs)
    return (
        sum(edit_distance(truth, prediction) for truth, prediction in pairs) / total
        if total
        else None
    )


def ece(observations, bins=10):
    if not observations:
        return None
    groups = defaultdict(list)
    for confidence, correct in observations:
        if not math.isfinite(confidence) or not 0 <= confidence <= 1 or type(correct) is not bool:
            raise ValueError("calibration_observation_invalid")
        groups[min(int(confidence * bins), bins - 1)].append((confidence, correct))
    return sum(
        len(g)
        / len(observations)
        * abs(statistics.mean(c for c, _ in g) - statistics.mean(ok for _, ok in g))
        for g in groups.values()
    )


def segmentation(truth, predicted):
    truth, predicted = set(map(tuple, truth)), set(map(tuple, predicted))
    if not truth and not predicted:
        return None, None
    iou = len(truth & predicted) / len(truth | predicted)

    def boundary(points):
        return {
            p
            for p in points
            if any(
                (p[0] + dx, p[1] + dy) not in points
                for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
            )
        }

    a, b = boundary(truth), boundary(predicted)
    f1 = 2 * len(a & b) / (len(a) + len(b)) if a or b else None
    return iou, f1


def paired_error(pairs):
    if not pairs:
        return None
    if any(
        len(a) != len(b) or not a or any(not math.isfinite(x) for x in [*a, *b]) for a, b in pairs
    ):
        raise ValueError("paired_observation_invalid")
    return statistics.mean(statistics.mean(abs(x - y) for x, y in zip(a, b)) for a, b in pairs)


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def metrics(rows, dimension):
    def mean(name):
        values = [r[name] for r in rows if r.get(name) is not None]
        return statistics.mean(values) if values else None

    if dimension == "recognition":
        pairs = [(r["truth"], r["prediction"]) for r in rows if "truth" in r and "prediction" in r]
        topk = [r["truth"] in r["top_k"] for r in rows if "truth" in r and "top_k" in r]
        return {
            "cer": cer(pairs),
            "top-k-accuracy": statistics.mean(topk) if topk else None,
            "ece": ece(
                [
                    (r["confidence"], r["correct"])
                    for r in rows
                    if "confidence" in r and "correct" in r
                ]
            ),
        }
    if dimension == "alignment_segmentation":
        masks = [
            segmentation(r["truth_mask"], r["prediction_mask"])
            for r in rows
            if "truth_mask" in r and "prediction_mask" in r
        ]
        ious, boundaries = (
            [v[0] for v in masks if v[0] is not None],
            [v[1] for v in masks if v[1] is not None],
        )
        return {
            "iou": statistics.mean(ious) if ious else None,
            "boundary-f1": statistics.mean(boundaries) if boundaries else None,
            "alignment-errors": mean("alignment_errors"),
            "split-errors": mean("split_errors"),
            "merge-errors": mean("merge_errors"),
        }
    if dimension == "outline_quality":
        return {
            "outline-mae": paired_error(
                [
                    (r["reference_outline"], r["predicted_outline"])
                    for r in rows
                    if "reference_outline" in r and "predicted_outline" in r
                ]
            ),
            "raster-mae": paired_error(
                [
                    (r["reference_raster"], r["predicted_raster"])
                    for r in rows
                    if "reference_raster" in r and "predicted_raster" in r
                ]
            ),
        }
    if dimension == "writer_fidelity":
        reviews = [
            r
            for r in rows
            if r.get("blinded") is True
            and r.get("rubric_hash")
            and r.get("reviewer_ref")
            and "preferred" in r
        ]
        pairs = [
            (r["heldout_reference_features"], r["predicted_features"])
            for r in rows
            if "heldout_reference_features" in r and "predicted_features" in r
        ]
        return {
            "heldout-feature-mae": paired_error(pairs),
            "blinded-preference": statistics.mean(r["preferred"] for r in reviews)
            if reviews
            else None,
        }
    if dimension == "user_burden":
        count = sum(r.get("glyphs_reviewed", 0) for r in rows)
        return {
            "corrections-per-glyph": sum(r.get("corrections", 0) for r in rows) / count
            if count
            else None,
            "first-useful-font-seconds": mean("first_useful_font_seconds"),
        }
    if dimension == "operations":
        times = [r["stage_ms"] for r in rows if r.get("stage_ms") is not None]
        return {
            "stage-p50-ms": percentile(times, 0.5),
            "stage-p95-ms": percentile(times, 0.95),
            "peak-rss-mb": max(
                (r["rss_mb"] for r in rows if r.get("rss_mb") is not None), default=None
            ),
            "storage-bytes": mean("storage_bytes"),
            "compute-seconds": mean("compute_seconds"),
        }
    return {}


def observation_count(name, rows):
    """Count actual contributing observations, excluding missing metric fields."""
    fields = {
        "cer": ("truth", "prediction"),
        "top-k-accuracy": ("truth", "top_k"),
        "ece": ("confidence", "correct"),
        "iou": ("truth_mask", "prediction_mask"),
        "boundary-f1": ("truth_mask", "prediction_mask"),
        "alignment-errors": ("alignment_errors",),
        "split-errors": ("split_errors",),
        "merge-errors": ("merge_errors",),
        "outline-mae": ("reference_outline", "predicted_outline"),
        "raster-mae": ("reference_raster", "predicted_raster"),
        "heldout-feature-mae": ("heldout_reference_features", "predicted_features"),
        "blinded-preference": ("preferred", "rubric_hash", "reviewer_ref"),
        "corrections-per-glyph": ("glyphs_reviewed",),
        "first-useful-font-seconds": ("first_useful_font_seconds",),
        "stage-p50-ms": ("stage_ms",),
        "stage-p95-ms": ("stage_ms",),
        "peak-rss-mb": ("rss_mb",),
        "storage-bytes": ("storage_bytes",),
        "compute-seconds": ("compute_seconds",),
    }[name]
    selected = [r for r in rows if all(r.get(f) is not None for f in fields)]
    if name == "blinded-preference":
        selected = [
            r
            for r in selected
            if r.get("blinded") is True and r["rubric_hash"] and r["reviewer_ref"]
        ]
    if name in {"iou", "boundary-f1"}:
        selected = [r for r in selected if r["truth_mask"] or r["prediction_mask"]]
    if name == "corrections-per-glyph":
        return sum(r["glyphs_reviewed"] for r in selected)
    return len(selected)


def report(template: BenchmarkRecord, rows, manifests, registry, run_metadata):
    """One dimension/partition per existing governance record. No run fabrication."""
    if template.status != "unmeasured":
        raise ValueError("new_report_requires_unmeasured_template")
    if not rows:
        return template
    validate_datasets(manifests, registry)
    assert_build_eligible(manifests, "research_evaluation")
    samples = {s.sample_id: (s, m) for m in manifests for s in m.samples}
    if len(samples) != sum(len(m.samples) for m in manifests):
        raise ValueError("ambiguous_sample_ids")
    seen = set()
    for row in rows:
        if row.get("sample_id") in seen or row.get("sample_id") not in samples:
            raise ValueError("evaluation_sample_invalid")
        seen.add(row["sample_id"])
        sample, dataset = samples[row["sample_id"]]
        if (sample.script_code, sample.style, sample.split) != (
            template.script_code,
            template.style,
            template.split,
        ):
            raise ValueError("evaluation_partition_mismatch")
        if (
            row.get("input_class") != template.input_class
            or row.get("condition") != template.condition
        ):
            raise ValueError("evaluation_partition_mismatch")
        if (template.evidence_domain == "synthetic_control") != (
            dataset.source_kind == "synthetic_font"
        ):
            raise ValueError("synthetic_real_evidence_mismatch")
        if "heldout_reference_features" in row:
            ref = samples.get(row.get("reference_sample_id"))
            if (
                not ref
                or ref[0].split != "test"
                or sample.split != "test"
                or ref[0].writer_group_id != sample.writer_group_id
                or ref[0].source_group_id == sample.source_group_id
                or (
                    {ref[0].content_hash, *ref[0].parent_content_hashes}
                    & {sample.content_hash, *sample.parent_content_hashes}
                )
            ):
                raise ValueError("heldout_writer_reference_invalid")
    values = metrics(rows, template.dimension)
    if template.evidence_domain == "synthetic_control" and "blinded-preference" in values:
        values["blinded-preference"] = None
    if not any(v is not None for v in values.values()):
        return template
    # Caller pins actual run date/code/config/tools/runtime/hardware. Missing values
    # fail existing BenchmarkRecord validation instead of being filled with guesses.
    allowed = {
        "measured_on",
        "code_ref",
        "config_hash",
        "tool_versions",
        "runtime",
        "cpu",
        "ram_mb",
        "provider",
    }
    if not set(run_metadata) <= allowed:
        raise ValueError("run_metadata_invalid")
    body = (
        template.model_dump(mode="json")
        | run_metadata
        | {
            "status": "measured",
            "n": len(rows),
            "split_registry_hash": digest(registry),
            "dataset_manifests": [
                {"record_id": f"{m.dataset_id}:{m.version}", "content_hash": digest(m)}
                for m in manifests
            ],
            "metrics": [
                Metric(
                    name=k,
                    unit="ratio"
                    if k
                    in {"cer", "ece", "iou", "boundary-f1", "top-k-accuracy", "blinded-preference"}
                    else "observed-units",
                    value=v,
                    n=observation_count(k, rows) if v is not None else None,
                ).model_dump(mode="json")
                for k, v in values.items()
            ],
        }
    )
    return BenchmarkRecord.model_validate(body)
