"""Security and lineage checks for the offline research path."""

import io
import stat
import tarfile
import zipfile

import pytest

from research.preparation.archive_audit import ROOT, Audit, external, safe_name
from research.preparation.freeze import stable_selection, validate_split_rows


@pytest.mark.parametrize("name", ["/etc/passwd", "../outside", "a/../../b", "C:/x", "a\\b"])
def test_rejects_escaping_archive_paths(name):
    with pytest.raises(ValueError, match="unsafe_archive_path"):
        safe_name(name)


def test_raw_staging_cannot_be_in_source_checkout():
    with pytest.raises(ValueError, match="outside_repository"):
        external(ROOT / "research/runs/raw")


def test_zip_link_is_rejected_without_writing_target(tmp_path):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("escape")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "../../private")
    with pytest.raises(ValueError, match="link"):
        Audit(tmp_path / "staging").read_zip(path, "test", save=True)
    assert not (tmp_path / "staging/test/escape").exists()


def test_zip_crc_checked_on_full_read(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("note.txt", b"original")
    path.write_bytes(path.read_bytes().replace(b"original", b"tampered", 1))
    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        Audit(tmp_path / "staging").read_zip(path, "test")


def test_gzip_trailer_is_independently_verified(tmp_path):
    import gzip

    from research.preparation.archive_audit import verify_gzip

    path = tmp_path / "trailer.gz"
    path.write_bytes(gzip.compress(b"intact payload"))
    assert verify_gzip(path) == len(b"intact payload")
    data = bytearray(path.read_bytes())
    data[-8] ^= 1
    path.write_bytes(data)
    with pytest.raises(gzip.BadGzipFile, match="CRC"):
        verify_gzip(path)


def test_tar_links_rejected(tmp_path):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../escape"
        t.addfile(info)
    with pytest.raises(ValueError, match="special_tar_member"):
        Audit(tmp_path / "staging").read_tar(path, "test")


def test_bad_image_quarantined_and_report_contains_no_path_or_label(tmp_path):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("private-name.jpg", b"not-an-image")
    audit = Audit(tmp_path / "staging")
    audit.read_zip(path, "fixture")
    result = audit.summary()
    assert result["corpora"]["fixture"]["image_errors"] == 1
    assert "private-name" not in str(result)


def test_tar_regular_file_only_extracts_to_external_root(tmp_path):
    path = tmp_path / "okay.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("folder/data.txt")
        info.size = 4
        t.addfile(info, io.BytesIO(b"data"))
    audit = Audit(tmp_path / "staging")
    audit.read_tar(path, "fixture", save=True)
    assert (tmp_path / "staging/fixture/folder/data.txt").read_bytes() == b"data"


def test_selection_is_order_independent_and_bounded_per_writer():
    rows = [{"writer": w, "id": str(i)} for w in ["one", "two"] for i in range(8)]
    first = stable_selection(rows, 20261021, 3)
    assert first == stable_selection(list(reversed(rows)), 20261021, 3)
    assert len(first) == 6
    assert first != stable_selection(rows, 20261020, 3)


@pytest.mark.parametrize("shared", ["writer", "page_id", "hash", "duplicate"])
def test_cross_split_identity_source_and_duplicate_leakage_rejected(shared):
    rows = [
        dict(writer="a", page_id="pa", hash="ha", duplicate="da", split="train"),
        dict(writer="b", page_id="pb", hash="hb", duplicate="db", split="test"),
    ]
    rows[1][shared] = rows[0][shared]
    with pytest.raises(ValueError, match="leakage"):
        validate_split_rows(rows)


def test_safe_numeric_checkpoint_roundtrips_without_fitting(tmp_path):
    import numpy as np

    from research.evaluation.local_baselines import load_tensors, save_tensors

    model = {
        "weights": np.eye(2, dtype=np.float32),
        "mean": np.zeros(2, np.float32),
        "priors": np.array([0.25, 0.75], np.float32),
    }
    checkpoint = tmp_path / "weights.safetensors"
    save_tensors(checkpoint, model)
    restored = load_tensors(checkpoint)
    for key in model:
        np.testing.assert_array_equal(restored[key], model[key])
    checkpoint.write_bytes(checkpoint.read_bytes() + b"unexpected")
    with pytest.raises(ValueError, match="trailing_data"):
        load_tensors(checkpoint)


def test_ocr_metrics_count_oov_failures_in_overall_score():
    from research.evaluation.local_baselines import recognition_metrics

    m = recognition_metrics(["क", "ख"], ["क", "क"], ["a", "b"], {"क"})
    assert m["wer"] == m["scalar_cer"] == 0.5
    assert m["out_of_vocabulary"]["wer"] == 1
    assert m["in_vocabulary"]["wer"] == 0


def test_frozen_study_rejects_changed_private_label_or_public_ledger(tmp_path):
    import json
    from types import SimpleNamespace

    from research.preparation.archive_audit import file_hash, sha
    from research.preparation.check_study import verify_study
    from research.preparation.freeze import writer_id

    row = dict(
        path="HindiOp2/1/1/1.jpg",
        id="sample",
        hash="a" * 64,
        split="train",
        page_id="page",
        duplicate="dup",
        writer="1",
        label="क",
        recognition=True,
    )
    public = {k: row[k] for k in ("id", "hash", "split", "page_id", "duplicate")}
    public.update(
        writer=writer_id("1"),
        label_hash=sha("क".encode()),
        recognition=True,
        style_role=None,
        glyph_candidate=False,
    )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(public) + "\n")
    sample = SimpleNamespace(
        sample_id="sample",
        content_hash="sha256:" + "a" * 64,
        writer_id=writer_id("1"),
        split="train",
        source_group_id="page",
    )
    manifest = SimpleNamespace(content_hash="sha256:" + file_hash(ledger), samples=[sample])
    verify_study([row], manifest, ledger)
    with pytest.raises(ValueError, match="label_changed"):
        verify_study([dict(row, label="ख")], manifest, ledger)
    ledger.write_text(ledger.read_text() + " ")
    with pytest.raises(ValueError, match="ledger_changed"):
        verify_study([row], manifest, ledger)
