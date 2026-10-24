"""Verify source checks remain separate from corpus admission and execution."""

import hashlib

import pytest

from research.english import cvl


def test_cvl_readiness_names_the_missing_owner_download(tmp_path):
    report = cvl.check_source(tmp_path)
    assert report["missing"] == ["cvl-database-1-1.zip"]
    assert report["dataset_admitted"] is False and report["model_execution"] is False


def test_cvl_verified_bytes_never_imply_audited_labels_or_resolved_permission(
    tmp_path, monkeypatch
):
    data = b"fixed-source-fixture"
    name = "cvl-database-1-1.zip"
    monkeypatch.setattr(
        cvl, "SOURCE", (name, len(data), hashlib.md5(data, usedforsecurity=False).hexdigest())
    )
    path = tmp_path / name
    path.write_bytes(data)
    report = cvl.check_source(tmp_path)
    assert report["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert report["archive_content_audit"] == "not_run"
    assert "discrepancy" in report["permission_review"]
    assert report["dataset_admitted"] is False and report["model_execution"] is False
    path.write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="checksum_mismatch"):
        cvl.check_source(tmp_path)


def test_cvl_source_symlink_cannot_pass_as_a_manual_download(tmp_path):
    (tmp_path / cvl.SOURCE[0]).symlink_to(tmp_path / "missing-target")
    with pytest.raises(ValueError, match="symlink"):
        cvl.check_source(tmp_path)
