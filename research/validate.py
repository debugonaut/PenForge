"""Offline cross-record checks. Never downloads source data or runs model code."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from research.models import (
    BenchmarkRecord,
    DatasetManifest,
    ExperimentRecord,
    ShapingCorpus,
    Use,
    WriterRegistry,
)

ROOT = Path(__file__).resolve().parents[1]
RECORD_TYPES: dict[str, type[BaseModel]] = {
    "dataset_manifest": DatasetManifest,
    "writer_registry": WriterRegistry,
    "benchmark_record": BenchmarkRecord,
    "experiment_record": ExperimentRecord,
    "shaping_corpus": ShapingCorpus,
}


def digest(value: BaseModel | dict[str, Any]) -> str:
    body = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def load_record(path: Path) -> BaseModel:
    # The manifests are YAML 1.2 in its JSON-compatible subset. This also avoids
    # arbitrary YAML constructors or hidden native type coercion.
    body = json.loads(path.read_text(encoding="utf-8"))
    kind = body.get("record_type")
    if kind not in RECORD_TYPES:
        raise ValueError("unknown research record type")
    return RECORD_TYPES[kind].model_validate(body)


def validate_datasets(manifests: list[DatasetManifest], registry: WriterRegistry) -> dict[str, Any]:
    """Reject writer, exact-duplicate, augmentation and parent leakage globally.

    Writer identity cannot be inferred from pixel hashes. Unknown identity stays
    quarantined; human curation must resolve aliases in this versioned registry.
    """
    identities = {
        (item.dataset_id, item.writer_id): item.writer_group_id for item in registry.identities
    }
    grouped_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    partitions: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    dataset_keys: set[tuple[str, str]] = set()
    for manifest in manifests:
        key = (manifest.dataset_id, manifest.version)
        if key in dataset_keys:
            raise ValueError("duplicate dataset manifest version")
        dataset_keys.add(key)
        for sample in manifest.samples:
            if sample.split == "quarantine":
                continue
            expected_group = identities.get((manifest.dataset_id, sample.writer_id))
            if not expected_group or sample.writer_group_id != expected_group:
                raise ValueError("sample writer is unresolved or differs from global registry")
            grouped_splits[("writer", expected_group)].add(sample.split)
            grouped_splits[("source", sample.source_group_id)].add(sample.split)
            if sample.duplicate_group_id:
                grouped_splits[("duplicate", sample.duplicate_group_id)].add(sample.split)
            for checksum in (sample.content_hash, *sample.parent_content_hashes):
                grouped_splits[("content", checksum)].add(sample.split)
            partition = f"{sample.script_code}/{sample.style}"
            partitions[partition][sample.split] += 1
    # Do not print identifying values in validation errors or CI logs.
    for (kind, _), splits in grouped_splits.items():
        if len(splits) > 1:
            raise ValueError(f"cross-split {kind} leakage")
    return {
        "datasets": len(manifests),
        "writer_groups": sum(kind == "writer" for kind, _ in grouped_splits),
        "partitions": {
            key: dict(sorted(value.items())) for key, value in sorted(partitions.items())
        },
        "scope": "metadata_validation_only",
    }


def assert_build_eligible(manifests: list[DatasetManifest], purpose: Use) -> None:
    """Admission check for a future private dataset builder, fail closed."""
    if not manifests:
        raise ValueError("dataset build requires at least one reviewed manifest")
    for manifest in manifests:
        if manifest.status != "approved" or purpose not in manifest.license.permitted_uses:
            raise ValueError("dataset not approved for requested use")
        if manifest.consent.status in {"pending", "withdrawn"}:
            raise ValueError("dataset consent does not permit a new build")
        if any(sample.split == "quarantine" for sample in manifest.samples):
            raise ValueError("quarantined samples cannot enter a dataset build")


def validate_experiment_graph(
    experiments: list[ExperimentRecord],
    benchmarks: list[BenchmarkRecord],
) -> None:
    """Evidence prerequisites only; this does not register or deploy a model."""
    records = {record.record_id: record for record in experiments}
    metrics = {record.record_id: record for record in benchmarks}
    if len(records) != len(experiments) or len(metrics) != len(benchmarks):
        raise ValueError("duplicate research record id")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record: ExperimentRecord) -> None:
        if record.record_id in visiting:
            raise ValueError("cyclic experiment lineage")
        if record.record_id in visited:
            return
        visiting.add(record.record_id)
        for link in record.parents:
            if link.record_id in records:
                visit(records[link.record_id])
        visiting.remove(record.record_id)
        visited.add(record.record_id)

    for record in experiments:
        visit(record)
    required_parents = {
        "baseline_comparison": {"dataset_research"},
        "training": {"baseline_comparison"},
        "heldout_evaluation": {"baseline_comparison"},
        "model_registration": {"heldout_evaluation"},
        "staged_deployment": {"model_registration"},
        "monitoring": {"staged_deployment"},
    }
    for record in experiments:
        parent_stages = set()
        for link in record.parents:
            parent = records.get(link.record_id)
            if not parent or digest(parent) != link.content_hash:
                raise ValueError("missing or changed experiment parent")
            if (parent.script_code, parent.style) != (record.script_code, record.style):
                raise ValueError("experiment lineage crosses script/style partitions")
            if record.status == "completed" and parent.status != "completed":
                raise ValueError("completed stage depends on planned work")
            parent_stages.add(parent.stage)
        linked_metrics = []
        for link in record.benchmarks:
            metric = metrics.get(link.record_id)
            if not metric or digest(metric) != link.content_hash:
                raise ValueError("missing or changed benchmark")
            if (metric.script_code, metric.style) != (record.script_code, record.style):
                raise ValueError("benchmark belongs to another script/style partition")
            if record.status == "completed" and metric.status != "measured":
                raise ValueError("completed stage cannot claim unmeasured benchmark")
            linked_metrics.append(metric)
        if record.status != "completed":
            continue
        if not required_parents.get(record.stage, set()) <= parent_stages:
            raise ValueError("research lifecycle prerequisite is missing")
        if record.stage == "baseline_comparison" and len(record.compared_methods) < 2:
            raise ValueError("baseline comparison requires at least two pinned methods")
        if (
            record.stage in {"baseline_comparison", "training", "heldout_evaluation"}
            and not linked_metrics
        ):
            raise ValueError("completed evaluation/training requires observed benchmark evidence")
        if record.stage == "heldout_evaluation":
            if record.training_required and "training" not in parent_stages:
                raise ValueError(
                    "trained model requires training lineage before held-out evaluation"
                )
            if not any(metric.split == "test" for metric in linked_metrics):
                raise ValueError("held-out evaluation requires untouched test split")
        if record.stage in {"training", "model_registration"} and not record.selection_rationale:
            raise ValueError("training/registration requires a written evidence-based rationale")


def validate_dataset_references(
    manifests: list[DatasetManifest],
    benchmarks: list[BenchmarkRecord],
    experiments: list[ExperimentRecord],
) -> None:
    datasets = {f"{item.dataset_id}:{item.version}": item for item in manifests}
    records: list[BenchmarkRecord | ExperimentRecord] = [*benchmarks, *experiments]
    for record in records:
        references = (
            record.dataset_manifests if isinstance(record, BenchmarkRecord) else record.datasets
        )
        for link in references:
            dataset = datasets.get(link.record_id)
            if not dataset or digest(dataset) != link.content_hash:
                raise ValueError("missing or changed dataset manifest")
            if (
                record.script_code not in dataset.script_codes
                or record.style not in dataset.style_partitions
            ):
                raise ValueError("dataset lacks required script/style partition")
            if isinstance(record, BenchmarkRecord) and record.status == "measured":
                assert_build_eligible([dataset], "research_evaluation")
                if not any(
                    sample.script_code == record.script_code
                    and sample.style == record.style
                    and sample.split == record.split
                    for sample in dataset.samples
                ):
                    raise ValueError("benchmark dataset lacks an eligible matching held-out split")
            if (
                isinstance(record, ExperimentRecord)
                and record.status == "completed"
                and record.stage == "training"
            ):
                assert_build_eligible([dataset], "research_training")


def validate_local_evidence(
    records: list[DatasetManifest | ShapingCorpus],
    root: Path = ROOT,
) -> None:
    for record in records:
        evidence = record.source
        if isinstance(record, DatasetManifest):
            evidence = (*evidence, *record.license.evidence, *record.consent.evidence)
        for item in evidence:
            if "://" in item.uri or item.content_hash is None:
                continue
            path = (root / item.uri.split("#", 1)[0]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("local evidence is missing or outside repository")
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != item.content_hash:
                raise ValueError("local evidence checksum mismatch")


def audit(root: Path = ROOT) -> dict[str, Any]:
    records = [
        load_record(path)
        for directory in (
            root / "research/manifests",
            root / "research/evaluation",
            root / "research/experiments",
        )
        for path in sorted(directory.rglob("*.yaml"))
    ]
    manifests = [record for record in records if isinstance(record, DatasetManifest)]
    registries = [record for record in records if isinstance(record, WriterRegistry)]
    if len(registries) != 1:
        raise ValueError("exactly one global writer registry is required")
    validate_local_evidence(
        [record for record in records if isinstance(record, (DatasetManifest, ShapingCorpus))], root
    )
    report = validate_datasets(manifests, registries[0])
    benchmarks = [record for record in records if isinstance(record, BenchmarkRecord)]
    experiments = [record for record in records if isinstance(record, ExperimentRecord)]
    if any(
        record.status == "measured" and record.split_registry_hash != digest(registries[0])
        for record in benchmarks
    ):
        raise ValueError("measured benchmark does not pin the current split registry")
    validate_dataset_references(manifests, benchmarks, experiments)
    validate_experiment_graph(experiments, benchmarks)
    report.update(
        {
            "records_validated": len(records),
            "measured_benchmarks": sum(record.status == "measured" for record in benchmarks),
            "completed_experiments": sum(record.status == "completed" for record in experiments),
            "approved_datasets": sum(record.status == "approved" for record in manifests),
        }
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        report = audit()
    except (ValueError, ValidationError, OSError):
        # Do not echo a Pydantic error containing untrusted handwriting fields.
        raise SystemExit("Research audit failed; inspect records privately.") from None
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
