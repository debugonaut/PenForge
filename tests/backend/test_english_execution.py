"""Owner guards and frozen English provenance, using fixed files only. No ML."""

import copy
import hashlib
import io
import json

import pytest
from PIL import Image

from research.english import execution, preflight, study
from research.english.runtime import score
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file


def write_json(path, body):
    path.write_text(json.dumps(body))


@pytest.fixture
def admitted_fixture(tmp_path, monkeypatch):
    """Approved-shaped records are tests of guards, never real data admission."""
    repository = tmp_path / "repo"
    manifests = repository / "research/manifests"
    manifests.mkdir(parents=True)
    old_registry = json.loads((ROOT / "research/manifests/writers-v1.yaml").read_text())
    write_json(manifests / "writers-v1.yaml", old_registry)
    # Exercise the same global validator with the pre-existing metadata source.
    metadata = json.loads(
        (ROOT / "research/manifests/split-validator-metadata-v1.yaml").read_text()
    )
    write_json(manifests / "metadata.yaml", metadata)
    monkeypatch.setattr(study, "ROOT", repository)
    staging = tmp_path / "staged"
    (staging / "images").mkdir(parents=True)
    rows, samples = [], []
    registry = copy.deepcopy(old_registry)
    registry["version"] = "1.2.0"
    for number, split in enumerate(("train", "validation", "test")):
        writer = f"fixture-english-{split}"
        registry["identities"].append(
            {
                "dataset_id": "english-guard-fixture",
                "writer_id": writer,
                "writer_group_id": writer,
                "evidence": "Fixed test metadata; not a handwriting participant",
            }
        )
        for index, label in enumerate(study.LETTERS):
            image = Image.new("L", (32, 32), 255)
            for bit in range(9):
                if (number * 52 + index + 1) & (1 << bit):
                    image.putpixel((bit, 10), 0)
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            data = stream.getvalue()
            checksum = hashlib.sha256(data).hexdigest()
            (staging / "images" / f"{checksum}.png").write_bytes(data)
            row = {
                "id": f"english-fixture-{number}-{index}",
                "hash": checksum,
                "path": f"images/{checksum}.png",
                "pixel_hash": hashlib.sha256(image.tobytes()).hexdigest(),
                "label": label,
                "writer": writer,
                "source_group": f"fixture-page-{split}",
                "split": split,
            }
            rows.append(row)
            samples.append(
                {
                    "sample_id": row["id"],
                    "writer_id": writer,
                    "writer_group_id": writer,
                    "script_code": "Latn",
                    "style": "mixed",
                    "split": split,
                    "content_hash": "sha256:" + checksum,
                    "source_group_id": row["source_group"],
                    "consent_state": "not_applicable",
                }
            )
    (staging / "study.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    write_json(
        staging / "preparation.json",
        {
            "status": "fixture_only_pending_admission",
            "version": "nist-letter-preparation-r3-v1",
            "sources": {
                name: {"bytes": size, "md5": md5}
                for name, (size, md5) in study.SOURCES.items()
                if name in {"by_write.zip", "by_class.zip"}
            },
        },
    )
    evidence = {
        "uri": "test-fixture://not-a-real-dataset",
        "checked_on": "2026-10-21",
        "finding": "Fixed geometric fixture only; not real handwriting permission or admission",
    }
    manifest = {
        "record_type": "dataset_manifest",
        "dataset_id": "english-guard-fixture",
        "version": "1.0.0",
        "status": "approved",
        "source_kind": "licensed_public",
        "source": [evidence],
        "script_codes": ["Latn"],
        "style_partitions": ["mixed"],
        "license": {
            "status": "verified",
            "identifier": "fixture-only",
            "evidence": [evidence],
            "permitted_uses": ["research_evaluation", "research_training"],
        },
        "consent": {"status": "not_applicable", "evidence": [evidence]},
        "writer_resolution": "resolved",
        "writer_registry_ref": "writers-v1:1.2.0",
        "content_hash": "sha256:" + digest_file(staging / "study.jsonl"),
        "samples": samples,
        "limitations": ["Fixed geometric test fixture only; no real participant or model"],
    }
    write_json(staging / "manifest.json", manifest)
    write_json(staging / "writers.json", registry)

    def repin():
        write_json(
            staging / "admission.json",
            {
                "version": "english-admission-r3-v1",
                "task": "letters",
                "preparation_sha256": digest_file(staging / "preparation.json"),
                "mapping_sha256": digest_file(staging / "study.jsonl"),
                "manifest_sha256": digest_file(staging / "manifest.json"),
                "registry_sha256": digest_file(staging / "writers.json"),
                "previous_registry_sha256": digest_file(manifests / "writers-v1.yaml"),
                "duplicate_review": "reviewed_exact_and_nonexact",
                "duplicate_review_ref": "test-fixture-only-review",
                "language_and_label_review_ref": "test-fixture-only-language-review",
            },
        )

    repin()
    return staging, rows, manifest, registry, repin


def test_english_admitted_fixture_keeps_52_letters_and_test_separate(admitted_fixture):
    staging, _, _, _, _ = admitted_fixture
    prepared, metadata = study.prepare(staging, study.Recipe(task="letters"))
    assert set(prepared["selected"]) == {"train", "validation"}
    assert metadata["partitions"]["test"]["n"] == 52
    assert all(len(group) == 52 for group in prepared["selected"].values())
    assert metadata["test_images_used_for_model_selection"] is False
    _, after = study.prepare(
        staging, study.Recipe(task="letters"), training=True, include_test=True
    )
    assert after == metadata


@pytest.mark.parametrize(
    "change",
    [
        "unreviewed",
        "missing",
        "case_changed",
        "registry_rewritten",
        "source_changed",
        "mapping_hash",
        "wrong_task",
    ],
)
def test_english_admission_and_source_guards_fail_closed(admitted_fixture, change):
    staging, rows, manifest, registry, repin = admitted_fixture
    if change == "unreviewed":
        manifest["status"] = "candidate"
        write_json(staging / "manifest.json", manifest)
        repin()
    elif change == "missing":
        (staging / "admission.json").unlink()
    elif change == "case_changed":
        rows[0]["label"] = "a"
        (staging / "study.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif change == "registry_rewritten":
        registry["identities"][0]["writer_group_id"] = "rewritten"
        write_json(staging / "writers.json", registry)
        repin()
    elif change == "source_changed":
        (staging / rows[0]["path"]).write_bytes(b"altered")
    elif change == "mapping_hash":
        manifest["content_hash"] = "sha256:" + "0" * 64
        write_json(staging / "manifest.json", manifest)
        repin()
    elif change == "wrong_task":
        body = json.loads((staging / "admission.json").read_text())
        body["task"] = "words"
        write_json(staging / "admission.json", body)
    with pytest.raises((ValueError, OSError)):
        study.prepare(staging, study.Recipe(task="letters"))


def test_english_global_writer_alias_cannot_leak_across_existing_fixture_splits(admitted_fixture):
    staging, _, manifest, registry, repin = admitted_fixture
    existing = json.loads((study.ROOT / "research/manifests/metadata.yaml").read_text())
    known = existing["samples"][0]["writer_group_id"]
    # Existing global fixture's first writer is train; put that alias in English test.
    for item in registry["identities"]:
        if item["writer_id"] == "fixture-english-test":
            item["writer_group_id"] = known
    for sample in manifest["samples"]:
        if sample["split"] == "test":
            sample["writer_group_id"] = known
    write_json(staging / "writers.json", registry)
    write_json(staging / "manifest.json", manifest)
    repin()
    with pytest.raises(ValueError, match="cross-split writer leakage"):
        study.prepare(staging, study.Recipe(task="letters"))


def test_english_new_training_requires_separate_training_permission(admitted_fixture):
    staging, _, manifest, _, repin = admitted_fixture
    manifest["license"]["permitted_uses"] = ["research_evaluation"]
    write_json(staging / "manifest.json", manifest)
    repin()
    study.prepare(staging, study.Recipe(task="letters"))
    with pytest.raises(ValueError, match="not approved"):
        study.prepare(staging, study.Recipe(task="letters"), training=True)


def test_english_inventory_report_cannot_substitute_for_a_prepared_study(admitted_fixture):
    staging, _, _, _, repin = admitted_fixture
    body = json.loads((staging / "preparation.json").read_text())
    body["version"] = "inventory-only"
    write_json(staging / "preparation.json", body)
    repin()
    with pytest.raises(ValueError, match="adapter_version"):
        study.prepare(staging, study.Recipe(task="letters"))


def test_english_related_corpus_cannot_copy_content_into_another_split(admitted_fixture):
    staging, _, manifest, registry, repin = admitted_fixture
    peer = copy.deepcopy(manifest)
    peer["dataset_id"] = "related-fixture"
    peer["samples"] = [
        dict(
            peer["samples"][0],
            sample_id="related-sample",
            writer_id="related-writer",
            writer_group_id="related-test-writer",
            source_group_id="related-page",
            split="test",
        )
    ]
    write_json(staging / "related.json", peer)
    registry["identities"].append(
        {
            "dataset_id": "related-fixture",
            "writer_id": "related-writer",
            "writer_group_id": "related-test-writer",
            "evidence": "Fixed test alias only",
        }
    )
    write_json(staging / "writers.json", registry)
    repin()
    admission = json.loads((staging / "admission.json").read_text())
    admission["related_manifests"] = {"related.json": digest_file(staging / "related.json")}
    write_json(staging / "admission.json", admission)
    with pytest.raises(ValueError, match="cross-split content leakage"):
        study.prepare(staging, study.Recipe(task="letters"))


@pytest.mark.parametrize(
    "owner,mode,train",
    [
        (False, "baseline", False),
        (False, "finetune", True),
        (True, "finetune", False),
        (True, "test", True),
        (True, "invalid", False),
    ],
)
def test_english_owner_guards_reject_execution_before_neural_operations(owner, mode, train):
    with pytest.raises(ValueError):
        execution.require_owner(owner, mode, train)


def parent_fixture():
    lineage = {"recipe": {"task": "letters"}, "study": {"partitions": {"validation": {"n": 52}}}}
    return {
        "version": "english-report-r3-v1",
        "status": "measured_research_only",
        "mode": "baseline",
        "lineage": lineage,
        "metrics": {
            "pretrained": {
                "n": 52,
                "wer": 0.5,
                "scalar_cer": 0.5,
                "generation_limit_hits": 0,
                "predictions_with_replacement_character": 0,
            }
        },
    }, lineage


def test_english_fitting_reason_uses_measured_errors_and_skips_error_free_baseline():
    report, lineage = parent_fixture()
    assert "50.00%" in execution.adaptation_reason(report)
    execution.validate_parent(report, "baseline", lineage)
    report["metrics"]["pretrained"].update(wer=0, scalar_cer=0)
    assert execution.adaptation_reason(report) is None


@pytest.mark.parametrize(
    "change", ["unmeasured", "nan", "different_task", "truncated", "replacement", "wrong_n"]
)
def test_english_fitting_requires_matching_usable_measured_baseline(change):
    report, lineage = parent_fixture()
    if change == "unmeasured":
        report["status"] = "prepared"
    if change == "nan":
        report["metrics"]["pretrained"]["scalar_cer"] = float("nan")
    if change == "different_task":
        different = copy.deepcopy(lineage)
        different["recipe"]["task"] = "words"
        with pytest.raises(ValueError):
            execution.validate_parent(report, "baseline", different)
        return
    if change == "truncated":
        report["metrics"]["pretrained"]["generation_limit_hits"] = 1
    if change == "replacement":
        report["metrics"]["pretrained"]["predictions_with_replacement_character"] = 1
    if change == "wrong_n":
        report["metrics"]["pretrained"]["n"] = 51
    with pytest.raises(ValueError):
        execution.adaptation_reason(report)


def test_english_test_requires_improved_frozen_checkpoint():
    report, lineage = parent_fixture()
    report.update(mode="finetune", metrics={"adapted": report["metrics"]["pretrained"]})
    with pytest.raises(ValueError, match="improved_valid_frozen"):
        execution.validate_parent(report, "finetune", lineage)
    report.update(
        checkpoint_files={"model.safetensors": "fixed"},
        validation_comparison={"improved_both": True},
    )
    execution.validate_parent(report, "finetune", lineage)


def test_english_fixed_predictions_use_distinct_case_sensitive_metrics():
    rows = [{"label": "A", "writer": "w1"}, {"label": "a", "writer": "w2"}]
    assert score("letters", rows, ["A", "A"], {"A": 1})["case_only_errors"] == 1
    rows[0]["label"], rows[1]["label"] = "Cat", "dog"
    assert score("words", rows, ["cat", "dog"], {"Cat": 1})["exact_word_accuracy"] == 0.5


def test_english_recipe_rejects_unbounded_or_unknown_training_choices():
    recipe = study.Recipe(task="letters")
    for change in (
        {"max_steps": 101},
        {"threads": 8},
        {"gpu": True},
        {"training_objective": "default_causal_loss"},
        {"task": "hindi"},
    ):
        with pytest.raises(ValueError):
            study.Recipe.model_validate(recipe.model_dump() | change)
    assert (
        study.load_recipe(
            ROOT / "research/configs/english-r3-letters.json", "letters"
        ).max_label_tokens
        == 8
    )
    with pytest.raises(ValueError):
        study.load_recipe(ROOT / "research/configs/english-r3-words.json", "letters")


def test_english_word_labels_preserve_unicode_punctuation_but_exclude_other_scripts():
    for label in ("Hello", "can't", "café", "co‑operate", "1920"):
        study.validate_label(label, "words")
    for label in ("क", "κόσμος", "two words", "A\u200b", "", "..."):
        with pytest.raises(ValueError):
            study.validate_label(label, "words")


def test_english_preflight_freezes_same_lineage_for_separate_test_encoding(monkeypatch):
    selected = {split: [{"label": "A"}] for split in ("train", "validation", "test")}

    def prepare(*_, include_test=False, **__):
        return {
            "selected": copy.deepcopy(
                selected if include_test else {k: v for k, v in selected.items() if k != "test"}
            )
        }, {"partitions": "fixed"}

    class Tokens:
        unk_token_id = 3
        all_special_ids = [0, 2, 3]

        def encode(self, *_, **__):
            return [0, 5, 2]

        def decode(self, *_, **__):
            return "A"

    monkeypatch.setattr(preflight, "prepare", prepare)
    monkeypatch.setattr(preflight, "verify_bundle", lambda _: {"bundle": "fixed"})
    monkeypatch.setattr(preflight, "tokenizer_for", lambda _: Tokens())
    monkeypatch.setattr(preflight, "source_inventory", lambda: {"code": "fixed"})
    monkeypatch.setattr(preflight.importlib.metadata, "version", lambda _: "fixture")
    _, baseline = preflight.check(ROOT, ROOT, study.Recipe(task="letters"))
    _, test = preflight.check(ROOT, ROOT, study.Recipe(task="letters"), include_test=True)
    assert baseline["lineage"] == test["lineage"]
    assert "test" not in baseline["lineage"]["study"]["encoding"]
    assert canonical_hash(baseline["lineage"]) == baseline["lineage_hash"]


def test_english_output_protects_existing_data_and_previous_runs(tmp_path):
    protected = tmp_path / "source"
    protected.mkdir()
    for output in (protected, protected / "child", tmp_path):
        with pytest.raises(ValueError):
            execution.new_output(output, (protected,))
    result = execution.new_output(tmp_path / "new-private-run", (protected,))
    assert result.is_dir()
    with pytest.raises(ValueError):
        execution.new_output(result, (protected,))
