"""R2 integrity/partition/text guards only. No model loading, inference or fitting."""

import hashlib
import io
import json
from copy import deepcopy

import pytest

from research.pretrained import artifacts as a
from research.pretrained.handoff import adaptation_reason
from research.pretrained.run import require_owner, validate_parent, verify_checkpoint
from research.pretrained.study import ROOT, encode_label, load_recipe, subset


def test_model_artifact_hash_and_git_blob_id_both_reject_changed_bytes(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b"safe")
    git_hash = hashlib.sha1(b"blob 4\0safe").hexdigest()
    sha256 = hashlib.sha256(b"safe").hexdigest()
    assert a.inspect_file(path, (4, git_hash))["sha256"] == sha256
    assert a.inspect_file(path, (4, sha256))["bytes"] == 4
    path.write_bytes(b"evil")
    for digest in (git_hash, sha256):
        with pytest.raises(ValueError, match="checksum"):
            a.inspect_file(path, (4, digest))


def fake_files(monkeypatch):
    content = {
        "config.json": json.dumps(
            {
                "model_type": "vision-encoder-decoder",
                "encoder": {"model_type": "vit"},
                "decoder": {"model_type": "trocr"},
            }
        ).encode(),
        "model.safetensors": b"fixed numerical fixture, never loaded",
    }
    monkeypatch.setattr(
        a,
        "FILES",
        {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in content.items()},
    )
    requests = []

    def response(url, timeout):
        requests.append(url)
        return io.BytesIO(content[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(a.urllib.request, "urlopen", response)
    return requests


def test_metadata_download_cannot_include_weights_or_pass_full_readiness(tmp_path, monkeypatch):
    requests = fake_files(monkeypatch)
    bundle = tmp_path / "metadata"
    result = a.download(bundle, metadata_only=True)
    assert result["scope"] == "metadata_only"
    assert all(not url.endswith("model.safetensors") for url in requests)
    assert not (bundle / "model.safetensors").exists()
    assert a.verify_bundle(bundle, metadata_only=True) == result
    with pytest.raises(ValueError, match="unexpected_or_missing"):
        a.verify_bundle(bundle)


def test_bundle_rejects_executable_extras_symlinks_and_receipt_changes(tmp_path, monkeypatch):
    fake_files(monkeypatch)
    bundle = tmp_path / "bundle"
    a.download(bundle)
    extra = bundle / "pytorch_model.bin"
    extra.write_bytes(b"never load pickle")
    with pytest.raises(ValueError, match="unexpected_or_missing"):
        a.verify_bundle(bundle)
    extra.unlink()
    config = bundle / "config.json"
    original = tmp_path / "original.json"
    config.rename(original)
    config.symlink_to(original)
    with pytest.raises(ValueError, match="regular_files"):
        a.verify_bundle(bundle)
    config.unlink()
    original.rename(config)
    (bundle / "receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="receipt_changed"):
        a.verify_bundle(bundle)


def test_downloader_preserves_existing_destination_and_refuses_repo(tmp_path, monkeypatch):
    requests = fake_files(monkeypatch)
    bundle = tmp_path / "existing"
    bundle.mkdir()
    with pytest.raises(ValueError, match="already_exists"):
        a.download(bundle)
    with pytest.raises(ValueError, match="outside_repository"):
        a.private_destination(ROOT / "research/runs/weights")
    assert not requests


def test_deterministic_balanced_subset_respects_recognition_roles_and_splits():
    rows = [
        {"id": f"{w}-{i}", "writer": w, "split": split, "recognition": i != 0}
        for w, split in [("a", "train"), ("b", "train"), ("c", "validation")]
        for i in range(8)
    ]
    train = subset(rows, "train", 3, 1)
    assert train == subset(list(reversed(rows)), "train", 3, 1)
    assert len(train) == 6 and all(r["recognition"] for r in train)
    validation = subset(rows, "validation", 3, 1)
    assert not {r["writer"] for r in train} & {r["writer"] for r in validation}
    with pytest.raises(ValueError, match="empty"):
        subset(rows, "test", None, 1)


class Tokens:
    unk_token_id = 3
    all_special_ids = [0, 2, 3, 4]

    def __init__(self, ids=None, decoded="कि"):
        self.ids = [0, 5, 6, 2] if ids is None else ids
        self.decoded = decoded

    def encode(self, _, **kwargs):
        assert kwargs["truncation"] is False
        return self.ids

    def decode(self, _, **kwargs):
        assert kwargs["clean_up_tokenization_spaces"] is False
        return self.decoded


def test_text_encoding_is_lossless_and_never_truncates_clusters():
    assert encode_label(Tokens(), "कि", 4) == [0, 5, 6, 2]
    for tokens, maximum, error in [
        (Tokens(), 3, "too_long"),
        (Tokens(ids=[0, 3, 2]), 4, "unknown_token"),
        (Tokens(ids=[0, 4, 2]), 4, "embedded_special"),
        (Tokens(decoded="क"), 4, "not_lossless"),
    ]:
        with pytest.raises(ValueError, match=error):
            encode_label(tokens, "कि", maximum)


def test_recipe_rejects_unknown_or_unbounded_resource_choices():
    recipe = load_recipe(ROOT / "research/configs/pretrained-r2.json")
    from research.pretrained.study import Recipe

    for change in [
        {"max_steps": 0},
        {"threads": 500},
        {"max_label_tokens": 999},
        {"gpu": True},
        {"version": "trocr-transfer-r2-v1"},
        {"training_objective": "causal_default"},
    ]:
        with pytest.raises(ValueError):
            Recipe.model_validate(recipe.model_dump() | change)


@pytest.mark.parametrize(
    "owner,mode,train",
    [
        (False, "baseline", False),
        (False, "finetune", True),
        (True, "finetune", False),
        (True, "test", True),
    ],
)
def test_owner_and_fitting_acknowledgments_fail_before_runtime(owner, mode, train):
    with pytest.raises(ValueError):
        require_owner(owner, mode, train)


def baseline_parent():
    lineage = {
        "study": {"partitions": {"validation": {"n": 64}}},
        "bundle": "fixed",
        "code": "fixed",
    }
    parent = {
        "status": "measured_research_only",
        "mode": "baseline",
        "lineage": lineage,
        "metrics": {"pretrained": {"n": 64, "wer": 0.9, "scalar_cer": 1.1, "grapheme_cer": 1.2}},
    }
    return parent, lineage


def test_finetuning_requires_measured_matching_baseline_not_just_metadata():
    parent, lineage = baseline_parent()
    validate_parent(parent, "baseline", lineage)
    for key, value in [("status", "prepared"), ("mode", "finetune"), ("lineage", {})]:
        with pytest.raises(ValueError, match="compatible"):
            validate_parent(parent | {key: value}, "baseline", lineage)
    invalid = deepcopy(parent)
    invalid["metrics"]["pretrained"]["scalar_cer"] = float("nan")
    with pytest.raises(ValueError, match="metric_missing_or_invalid"):
        validate_parent(invalid, "baseline", lineage)
    invalid["metrics"]["pretrained"]["n"] = 63
    with pytest.raises(ValueError, match="count_changed"):
        validate_parent(invalid, "baseline", lineage)


def test_owner_launcher_reason_records_measurements_and_skips_error_free_subset():
    report, _ = baseline_parent()
    reason = adaptation_reason(report)
    assert "90.00%" in reason and "110.00%" in reason and "120.00%" in reason
    assert "not a production quality threshold" in reason
    report["metrics"]["pretrained"].update(wer=0, scalar_cer=0, grapheme_cer=0)
    assert adaptation_reason(report) is None


@pytest.mark.parametrize("change", [{"status": "prepared"}, {"mode": "finetune"}])
def test_owner_launcher_never_justifies_fitting_from_unmeasured_or_wrong_mode_report(change):
    report, _ = baseline_parent()
    with pytest.raises(ValueError, match="compatible"):
        adaptation_reason(report | change)


def test_owner_launcher_never_justifies_fitting_from_nonfinite_metric():
    report, _ = baseline_parent()
    report["metrics"]["pretrained"]["wer"] = float("inf")
    with pytest.raises(ValueError, match="metric_missing_or_invalid"):
        adaptation_reason(report)


def test_test_execution_requires_exact_frozen_safe_checkpoint(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    names = ["config.json", "generation_config.json", "model.safetensors"]
    for name in names:
        (checkpoint / name).write_bytes(b"fixed fixture never executed")
    parent = {"checkpoint_files": {name: a.digest_file(checkpoint / name) for name in names}}
    assert verify_checkpoint(tmp_path / "report.json", parent) == checkpoint
    (checkpoint / "model.safetensors").write_bytes(b"altered")
    with pytest.raises(ValueError, match="checksum_mismatch"):
        verify_checkpoint(tmp_path / "report.json", parent)
