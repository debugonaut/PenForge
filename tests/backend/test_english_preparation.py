"""Fixed geometric ZIP fixtures and metadata guards. No model execution or fitting."""

import hashlib
import io
import json
import stat
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image

from research.english import acquire, nist
from research.english import check as check_module
from research.english.check import check_sources, check_staging


def png_fixture(number):
    image = Image.new("L", (128, 128), 255)
    for bit in range(16):
        if (number + 1) & (1 << bit):
            image.putpixel((10 + bit, 40), 0)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_nist_writer_paths_follow_provider_ranges_and_keep_page_and_field():
    assert nist.writer_parts("by_write/hsf_2/f1454_12/c1454_12/c1454_12_00009.png") == (
        2,
        "1454",
        "12",
        "c",
    )
    assert nist.writer_parts("by_write/hsf_2/f1454_12/u1454_12/u1454_12_00000.png")[-1] == "u"
    assert nist.writer_parts("by_write/hsf_2/f1454_12/u9999_12/u1454_12_00000.png") is None
    with pytest.raises(ValueError, match="outside_documented"):
        nist.writer_parts("by_write/hsf_4/f0000_12/u0000_12/u0000_12_00000.png")


@pytest.mark.parametrize(
    "name", ["../outside.png", "/absolute.png", "by_class/./A.png", "by_class\\escape.png"]
)
def test_nist_archive_paths_cannot_escape_or_silently_normalize(tmp_path, name):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"fixed")
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError):
        list(nist.members(archive))


@pytest.mark.parametrize(
    "changes",
    [{"flag_bits": 1}, {"external_attr": (stat.S_IFLNK | 0o777) << 16}, {"file_size": 1_000_001}],
)
def test_nist_rejects_encryption_links_and_member_expansion(changes):
    entry = zipfile.ZipInfo("by_class/41/hsf_0/hsf_0_00000.png")
    entry.file_size = 20
    for name, value in changes.items():
        setattr(entry, name, value)
    with pytest.raises(ValueError):
        list(nist.members(SimpleNamespace(infolist=lambda: [entry])))


def test_nist_splits_keep_school_writers_in_test_and_whole_writers_together():
    writers = {f"{n:04}": 0 for n in range(100)} | {"2100": 4, "4100": 8}
    first = nist.writer_splits(writers, 20261021)
    assert first == nist.writer_splits(dict(reversed(list(writers.items()))), 20261021)
    assert first["2100"] == "test" and "4100" not in first
    assert set(first.values()) == {"train", "validation", "test"}
    assert len(first) == len(set(first))


def test_nist_pixel_duplicate_quarantine_keeps_all_52_classes_and_different_writers():
    rows = [
        {
            "id": f"{split}-{label}-{rep}",
            "label": label,
            "split": split,
            "writer": f"{split}-writer-{rep}",
            "pixel_hash": f"{split}-{label}-{rep}",
        }
        for split in ("train", "validation", "test")
        for label in nist.LETTERS
        for rep in (0, 1)
    ]
    rows[0]["pixel_hash"] = rows[104]["pixel_hash"] = "copied-pixels"
    selected, report = nist.select(rows, {"train": 1, "validation": 1, "test": 1}, 7)
    assert len(selected) == 156
    assert report["pixel_ambiguities_quarantined"] == 2
    assert all(row["pixel_hash"] != "copied-pixels" for row in selected)
    assert {row["label"] for row in selected} == set(nist.LETTERS)
    with pytest.raises(ValueError, match="no_silent_class_omission"):
        nist.select(
            [row for row in rows if row["label"] != "z"],
            {"train": 1, "validation": 1, "test": 1},
            7,
        )


def make_archives(folder, monkeypatch):
    folder.mkdir()
    writers = [(0, "0000"), (0, "0001"), (0, "0002"), (4, "2100")]
    with (
        zipfile.ZipFile(folder / "by_write.zip", "w") as writer_zip,
        zipfile.ZipFile(folder / "by_class.zip", "w") as class_zip,
    ):
        for person, (partition, writer) in enumerate(writers):
            for index, label in enumerate(nist.LETTERS):
                data = png_fixture(100 * person + index)
                field = "u" if label.isupper() else "l"
                writer_zip.writestr(
                    f"by_write/hsf_{partition}/f{writer}_12/{field}{writer}_12/{field}{writer}_12_{index:05}.png",
                    data,
                )
                class_zip.writestr(
                    f"by_class/{ord(label):02x}/hsf_{partition}/{writer}_{index:05}.png", data
                )
    monkeypatch.setattr(
        nist,
        "SOURCES",
        {
            name: (
                path.stat().st_size,
                hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest(),
            )
            for name in ("by_write.zip", "by_class.zip")
            if (path := folder / name)
        },
    )
    monkeypatch.setattr(check_module, "SOURCES", nist.SOURCES)


def test_nist_audit_matches_real_zip_bytes_to_labels_and_freezes_private_study(
    tmp_path, monkeypatch
):
    sources, output = tmp_path / "sources", tmp_path / "prepared"
    make_archives(sources, monkeypatch)
    report = nist.audit(sources, output, caps={"train": 1, "validation": 1, "test": 1})
    assert report["status"] == "prepared_pending_curator_admission"
    assert report["all_archive_members_crc_checked"] is True
    assert report["model_inference"] is False and report["training"] == "not_run"
    assert report["class_images_crc_checked"] == 208
    assert (
        report["preparation_code_sha256"]["research/english/nist.py"]
        == hashlib.sha256((nist.ROOT / "research/english/nist.py").read_bytes()).hexdigest()
    )
    assert report["environment"]["pillow"]
    assert check_sources(sources)["status"] == "source_bytes_verified_only"
    assert check_staging(output)["status"] == "staging_integrity_verified_pending_admission"
    rows = [json.loads(line) for line in (output / "study.jsonl").read_text().splitlines()]
    assert len(rows) == 156 and len({row["label"] for row in rows}) == 52
    for row in rows:
        assert hashlib.sha256((output / row["path"]).read_bytes()).hexdigest() == row["hash"]
    groups = [
        {r["writer"] for r in rows if r["split"] == split}
        for split in ("train", "validation", "test")
    ]
    assert not groups[0] & groups[1] and not groups[0] & groups[2] and not groups[1] & groups[2]
    with pytest.raises(ValueError, match="new_staging"):
        nist.audit(sources, output, caps={"train": 1, "validation": 1, "test": 1})
    image_path = output / rows[0]["path"]
    image_path.write_bytes(b"altered")
    with pytest.raises(ValueError, match="staged_image_changed"):
        check_staging(output)


def test_manual_nist_source_verification_checks_size_symlinks_and_publisher_digest(tmp_path):
    path = tmp_path / "source.zip"
    path.write_bytes(b"fixture")
    md5 = hashlib.md5(b"fixture", usedforsecurity=False).hexdigest()
    assert acquire.inspect(path, 7, md5)["sha256"] == hashlib.sha256(b"fixture").hexdigest()
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum_mismatch"):
        acquire.inspect(path, 7, md5)
    alias = tmp_path / "alias.zip"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        acquire.inspect(alias, 7, md5)


def test_manual_readiness_names_missing_files_without_network_or_admission(tmp_path):
    result = check_sources(tmp_path)
    assert result == {
        "status": "missing_manual_downloads",
        "missing": ["by_write.zip", "by_class.zip"],
        "model_execution": False,
    }
