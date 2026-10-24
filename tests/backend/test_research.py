"""Governance failures that would contaminate future scientific conclusions."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from research.models import (
    BenchmarkRecord,
    DatasetManifest,
    ExperimentRecord,
    ShapingCorpus,
    WriterRegistry,
)
from research.schemas import generate
from research.validate import (
    assert_build_eligible,
    audit,
    digest,
    load_record,
    validate_dataset_references,
    validate_datasets,
    validate_experiment_graph,
    validate_local_evidence,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def manifest_body():
    return json.loads((ROOT / "research/manifests/split-validator-metadata-v1.yaml").read_text())


@pytest.fixture
def registry():
    return load_record(ROOT / "research/manifests/writers-v1.yaml")


def test_actual_research_records_preserve_bounded_admission_and_research_only_status():
    report = audit()
    assert report["scope"] == "metadata_validation_only"
    approved = [
        load_record(p)
        for p in (ROOT / "research/manifests").glob("*.yaml")
        if json.loads(p.read_text()).get("status") == "approved"
    ]
    assert report["approved_datasets"] == 1
    assert [r.dataset_id for r in approved] == ["iiit-hw-dev-r1"]
    assert set(approved[0].license.permitted_uses) == {"research_evaluation", "research_training"}
    for path in (ROOT / "research/experiments").glob("*.yaml"):
        record = load_record(path)
        assert record.capability_status == "research_only"
        if record.status == "completed":
            assert record.record_id.startswith("r1-")
    assert set(report["partitions"]) == {
        f"{script}/{style}"
        for script in ("Latn", "Deva")
        for style in ("print", "cursive", "mixed")
    }
    assert generate(check=True) == []


def test_same_writer_across_datasets_is_not_independent(manifest_body, registry):
    first = DatasetManifest.model_validate(manifest_body)
    second_body = copy.deepcopy(manifest_body)
    second_body["dataset_id"] = "other-corpus"
    second_body["samples"] = [
        dict(second_body["samples"][0], split="test", sample_id="other-image")
    ]
    second = DatasetManifest.model_validate(second_body)
    identities = registry.model_dump(mode="json")
    identities["identities"].append(dict(identities["identities"][0], dataset_id="other-corpus"))
    with pytest.raises(ValueError, match="cross-split writer leakage"):
        validate_datasets([first, second], WriterRegistry.model_validate(identities))


@pytest.mark.parametrize("kind", ["content", "source", "duplicate", "parent"])
def test_duplicate_or_augmented_content_cannot_cross_splits(kind, manifest_body, registry):
    train, test = manifest_body["samples"][0], manifest_body["samples"][2]
    if kind == "content":
        test["content_hash"] = train["content_hash"]
    elif kind == "source":
        test["source_group_id"] = train["source_group_id"]
    elif kind == "duplicate":
        train["duplicate_group_id"] = test["duplicate_group_id"] = "curated-near-duplicate-group"
    else:
        test["parent_content_hashes"] = [train["content_hash"]]
    with pytest.raises(ValueError, match="cross-split"):
        validate_datasets([DatasetManifest.model_validate(manifest_body)], registry)


def test_forged_writer_group_or_missing_alias_is_rejected(manifest_body, registry):
    manifest_body["samples"][0]["writer_group_id"] = "unregistered-writer"
    with pytest.raises(ValueError, match="global registry"):
        validate_datasets([DatasetManifest.model_validate(manifest_body)], registry)


def test_registry_alias_cannot_resolve_two_ways(registry):
    body = registry.model_dump(mode="json")
    body["identities"].append(dict(body["identities"][0], writer_group_id="another-group"))
    with pytest.raises(ValidationError, match="exactly one"):
        WriterRegistry.model_validate(body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("writer_group_id", None),
        ("consent_state", "withdrawn"),
        ("consent_state", "pending"),
    ],
)
def test_unknown_writer_or_consent_remains_quarantined(field, value, manifest_body, registry):
    manifest_body["samples"][0][field] = value
    with pytest.raises(ValidationError, match="quarantine"):
        DatasetManifest.model_validate(manifest_body)
    manifest_body["samples"][0]["split"] = "quarantine"
    validate_datasets([DatasetManifest.model_validate(manifest_body)], registry)


def test_personal_font_permission_is_not_training_consent(manifest_body):
    manifest_body.update(source_kind="opt_in", status="candidate")
    manifest_body["license"]["status"] = "pending"
    with pytest.raises(ValidationError, match="separate active consent"):
        DatasetManifest.model_validate(manifest_body)


def test_fixture_or_candidate_is_not_eligible_training_data(manifest_body):
    fixture = DatasetManifest.model_validate(manifest_body)
    with pytest.raises(ValueError, match="not approved"):
        assert_build_eligible([fixture], "research_training")
    for name in ("iam-candidate-v1", "dhcd-candidate-v1", "caveat-synthetic-source-v1"):
        candidate = load_record(ROOT / f"research/manifests/{name}.yaml")
        with pytest.raises(ValueError, match="not approved"):
            assert_build_eligible([candidate], "research_training")


def test_no_grant_from_unreviewed_license(manifest_body):
    manifest_body["license"].update(status="pending", permitted_uses=["production_training"])
    with pytest.raises(ValidationError, match="cannot grant"):
        DatasetManifest.model_validate(manifest_body)


def test_local_font_and_license_are_integrity_checked(tmp_path):
    manifest = load_record(ROOT / "research/manifests/caveat-synthetic-source-v1.yaml")
    validate_local_evidence([manifest])
    (tmp_path / "public/fonts").mkdir(parents=True)
    (tmp_path / "public/fonts/CaveatVariable.woff2").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_local_evidence([manifest], tmp_path)


@pytest.fixture
def benchmark_body():
    return json.loads(
        (ROOT / "research/evaluation/benchmarks/latn-print-recognition-v1.yaml").read_text()
    )


@pytest.mark.parametrize("field,value", [("n", 1), ("p95_ms", 5.0), ("peak_rss_mb", 10.0)])
def test_unmeasured_benchmark_has_no_invented_observations(field, value, benchmark_body):
    benchmark_body[field] = value
    with pytest.raises(ValidationError, match="null results"):
        BenchmarkRecord.model_validate(benchmark_body)


def test_accuracy_cannot_become_measured_by_changing_label(benchmark_body):
    benchmark_body["status"] = "measured"
    with pytest.raises(ValidationError, match="reproducible provenance"):
        BenchmarkRecord.model_validate(benchmark_body)


def test_metadata_fixture_cannot_be_reported_as_measured_handwriting(benchmark_body, manifest_body):
    dataset = DatasetManifest.model_validate(manifest_body)
    benchmark_body.update(
        status="measured",
        measured_on="2026-10-19",
        n=1,
        dataset_manifests=[
            {
                "record_id": f"{dataset.dataset_id}:{dataset.version}",
                "content_hash": digest(dataset),
            }
        ],
        split_registry_hash="sha256:" + "a" * 64,
        config_hash="sha256:" + "b" * 64,
        code_ref="synthetic-test-only",
        tool_versions={"test": "1"},
        runtime="test",
        cpu="test",
    )
    benchmark_body["metrics"][0].update(value=0.1, n=1)
    benchmark = BenchmarkRecord.model_validate(benchmark_body)
    with pytest.raises(ValueError, match="not approved"):
        validate_dataset_references([dataset], [benchmark], [])


def test_changed_dataset_version_cannot_reuse_benchmark_provenance(benchmark_body, manifest_body):
    dataset = DatasetManifest.model_validate(manifest_body)
    benchmark_body["dataset_manifests"] = [
        {
            "record_id": f"{dataset.dataset_id}:{dataset.version}",
            "content_hash": digest(dataset),
        }
    ]
    benchmark = BenchmarkRecord.model_validate(benchmark_body)
    changed = dataset.model_copy(update={"limitations": ("Changed source record",)})
    with pytest.raises(ValueError, match="changed dataset"):
        validate_dataset_references([changed], [benchmark], [])


def test_nonfinite_metrics_and_unknown_versions_rejected(benchmark_body):
    benchmark_body["metrics"][0]["value"] = float("nan")
    with pytest.raises(ValidationError):
        BenchmarkRecord.model_validate(benchmark_body)
    benchmark_body["metrics"][0]["value"] = None
    benchmark_body["schema_version"] = 2
    with pytest.raises(ValidationError):
        BenchmarkRecord.model_validate(benchmark_body)


def test_shaping_starter_preserves_exact_codepoint_evidence():
    corpus = load_record(ROOT / "research/evaluation/corpora/deva-starter-v1.yaml")
    assert corpus.common_word_inventory is False
    assert corpus.font_ref is None
    assert all(case.expected_output_hash is None for case in corpus.cases)
    body = corpus.model_dump()
    body["cases"][0]["codepoints"] = [0xD800]
    with pytest.raises(ValidationError):
        ShapingCorpus.model_validate(body)


def test_completed_training_requires_recipe_and_evidence():
    record = load_record(ROOT / "research/experiments/latn-print-baseline-v1.yaml").model_dump()
    record.update(
        status="completed",
        stage="training",
        code_ref="source-hash",
        seed=10,
        environment_lock_hash="sha256:" + "a" * 64,
        config_hash="sha256:" + "b" * 64,
        command=["python", "future-train.py"],
        artifacts=[
            {
                "record_id": "artifact",
                "content_hash": "sha256:" + "c" * 64,
            }
        ],
    )
    with pytest.raises(ValidationError, match="justification"):
        ExperimentRecord.model_validate(record)
    record.update(
        method_kind="fine_tune",
        training_required=True,
        selection_rationale="Test rationale",
        recipe_ref={"record_id": "recipe", "content_hash": "sha256:" + "d" * 64},
        datasets=[{"record_id": "dataset", "content_hash": "sha256:" + "e" * 64}],
    )
    training = ExperimentRecord.model_validate(record)
    with pytest.raises(ValueError, match="prerequisite"):
        validate_experiment_graph([training], [])


def test_parent_record_hash_detects_lineage_rewrite():
    record = load_record(ROOT / "research/experiments/latn-print-baseline-v1.yaml")
    child_body = record.model_dump(mode="json")
    child_body.update(
        {
            "record_id": "child",
            "parents": [
                {
                    "record_id": record.record_id,
                    "content_hash": digest(record),
                }
            ],
        }
    )
    child = ExperimentRecord.model_validate(child_body)
    changed = record.model_copy(update={"seed": 4})
    with pytest.raises(ValueError, match="changed experiment parent"):
        validate_experiment_graph([changed, child], [])


def test_digest_is_independent_of_object_key_order():
    assert digest({"a": 1, "b": "कि"}) == digest({"b": "कि", "a": 1})
