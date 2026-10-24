"""Fixed tiny ZIPs only: download readiness and private CVL inventory. No ML."""

import hashlib
import json
import zipfile

import pytest

from research.english import cvl_inventory, downloads


def test_partial_downloads_never_trigger_hashing_or_model_work(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "SOURCES", {"by_write.zip": (3, "a"), "by_class.zip": (3, "b")})
    monkeypatch.setattr(downloads, "SOURCE", ("cvl-database-1-1.zip", 4, "c"))
    (tmp_path / "by_write.zip").write_bytes(b"123")
    (tmp_path / "by_class.zip").write_bytes(b"123")
    (tmp_path / "cvl-database-1-1.zip").write_bytes(b"12")

    def forbidden(*_):
        raise AssertionError("Do not hash incomplete downloads")

    monkeypatch.setattr(downloads, "inspect", forbidden)
    result = downloads.check(tmp_path, verify=True)
    assert result["status"] == "downloads_not_ready" and result["model_execution"] is False
    assert result["files"]["cvl-database-1-1.zip"]["status"] == "incomplete_or_unexpected_size"
    assert downloads.check(tmp_path, letters_only=True)["status"] == "source_files_present"


def test_all_complete_sources_are_hashed_but_not_admitted(tmp_path, monkeypatch):
    data = b"fixed"
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(downloads, "SOURCES", {"by_write.zip": (5, md5), "by_class.zip": (5, md5)})
    monkeypatch.setattr(downloads, "SOURCE", ("cvl-database-1-1.zip", 5, md5))
    for name in ("by_write.zip", "by_class.zip", "cvl-database-1-1.zip"):
        (tmp_path / name).write_bytes(data)
    result = downloads.check(tmp_path, verify=True)
    assert result["checksum_verified"] is True and result["dataset_admitted"] is False
    assert all(
        item["sha256"] == hashlib.sha256(data).hexdigest() for item in result["files"].values()
    )


def test_cvl_inventory_keeps_names_private_and_never_admits_or_decodes(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    with zipfile.ZipFile(sources / cvl_inventory.SOURCE[0], "w") as archive:
        archive.writestr("corpus/page.xml", b'<Page><AttrRegion text="Fixture" /></Page>')
        archive.writestr("corpus/word-Fixture.tif", b"not decoded by inventory")
    monkeypatch.setattr(
        cvl_inventory,
        "check_source",
        lambda _: {
            "status": "source_bytes_verified_only",
            "source": {"sha256": "fixed-test-receipt"},
        },
    )
    destination = tmp_path / "inventory"
    report = cvl_inventory.inventory(sources, destination)
    assert report["files"] == 2 and report["non_directory_members_crc_checked"] is True
    assert report["annotation_example_available"] is True and report["images_decoded"] is False
    assert report["dataset_admitted"] is False and report["training"] == "not_run"
    assert "Fixture" not in json.dumps(report)
    assert "Fixture" in (destination / "entries-private.jsonl").read_text()
    with pytest.raises(ValueError, match="new_inventory"):
        cvl_inventory.inventory(sources, destination)


def test_cvl_inventory_rejects_unsafe_names_and_cleans_its_own_temp(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    with zipfile.ZipFile(sources / cvl_inventory.SOURCE[0], "w") as archive:
        archive.writestr("../outside.xml", b"unsafe")
    monkeypatch.setattr(
        cvl_inventory,
        "check_source",
        lambda _: {"status": "source_bytes_verified_only", "source": {}},
    )
    destination = tmp_path / "inventory"
    with pytest.raises(ValueError):
        cvl_inventory.inventory(sources, destination)
    assert not destination.exists() and not list(tmp_path.glob(".cvl-inventory-*"))
    assert not (tmp_path / "outside.xml").exists()
