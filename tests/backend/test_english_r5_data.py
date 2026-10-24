"""Deterministic synthetic writer and pixel preparation, with no real model."""

import io
import json
import random
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from research.english.nist import sha
from research.english.r4_study import nist_writer_id
from research.english.r5_contract import CLASSES
from research.english.r5_data import prepare as preparation
from research.english.r5_data.check import check
from research.english.r5_data.common import external, image_identity, load_config
from research.english.r5_data.history import assign_writers


def png(seed, *, offset=0):
    image = Image.new("L", (128, 128), 255)
    rng = random.Random(seed)
    for _ in range(350):
        image.putpixel((20 + offset + rng.randrange(60), 20 + rng.randrange(70)), 0)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def identity(data):
    with Image.open(io.BytesIO(data)) as image:
        return image_identity(image)


def test_normalized_identity_detects_padding_translation_but_source_pixels_are_distinct():
    first, second = identity(png(10)), identity(png(10, offset=4))
    assert first["pixel_hash"] != second["pixel_hash"]
    assert first["normalized_pixel_hash"] == second["normalized_pixel_hash"]
    assert first["observed_baseline"] is None
    assert first["page_scale"] is None


def test_history_reserves_every_untouched_writer_and_excludes_old_tests():
    writers = {"trained": 0, "validated": 1, "tested": 4, "new": 2, "school": 4}
    result, report = assign_writers(
        writers, {"trained": "train", "validated": "validation", "tested": "test"}
    )
    assert result == {
        "trained": "train",
        "validated": "validation",
        "new": "test",
        "school": "school_test",
    }
    assert report["never_exposed_writers"] == 2
    assert report["historical_test_writers_excluded"] == 1


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    """Exercise the real ZIP parsing/CRC, hash join and selection with synthetic PNGs."""
    root = tmp_path_factory.mktemp("r5-data")
    sources, r3, r4, destination = [root / name for name in ("sources", "r3", "r4", "stage")]
    for folder in (sources, r3, r4):
        folder.mkdir()
    for name in ("admission.json", "manifest.json"):
        (r3 / name).write_text("{}")
    history, roles = [], {}
    with (
        zipfile.ZipFile(sources / "by_class.zip", "w") as classes,
        zipfile.ZipFile(sources / "by_write.zip", "w") as writers,
    ):
        for writer in range(33):
            role = "train" if writer == 0 else "validation" if writer <= 16 else "test"
            pseudonym = nist_writer_id(f"{writer:04d}")
            if role != "test":
                roles[pseudonym] = role
            for index, label in enumerate(CLASSES):
                data = png(1000 * writer + index)
                checksum = sha(data)
                classes.writestr(f"by_class/{ord(label):02x}/hsf_0/{writer}-{index}.png", data)
                field = "u" if label.isupper() else "l"
                member = f"by_write/hsf_0/f{writer:04d}_00/{field}{writer:04d}_00/{field}{writer:04d}_00_{index:05d}.png"
                writers.writestr(member, data)
                if role != "test":
                    history.append(
                        {
                            "id": f"old-{writer}-{index}",
                            "writer": pseudonym,
                            "label": label,
                            "split": role,
                            "hash": checksum,
                            **identity(data),
                        }
                    )
    patcher = pytest.MonkeyPatch()
    patcher.setattr(
        preparation, "collect_history", lambda *_: (history, {"writers": roles, "admissions": []})
    )
    patcher.setattr(
        preparation,
        "inspect",
        lambda path, *_: {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size},
    )
    try:
        report = preparation.prepare(sources, r3, r4, destination)
    finally:
        patcher.undo()
    return destination, report, root


def test_synthetic_archive_audit_is_case_sensitive_writer_disjoint_and_private(prepared):
    stage, report, _ = prepared
    assert report["model_inference"] is False
    assert report["partitions"]["test"]["samples"] == 832
    assert report["partitions"]["validation"]["samples"] == 832
    assert report["partitions"]["train"]["samples"] == 52
    assert report["partitions"]["school_test"]["samples"] == 0
    assert "school_test" in report["selection"]["unavailable_final_groups"]
    writer_groups = []
    for name in ("train.jsonl", "validation.jsonl", "final-test.jsonl"):
        rows = [json.loads(line) for line in (stage / name).read_text().splitlines()]
        assert {row["label"] for row in rows} == set(CLASSES)
        assert all(row["path"] == f"images/{row['hash']}.png" for row in rows)
        assert len({row["pixel_hash"] for row in rows}) == len(rows)
        writer_groups.append({row["writer"] for row in rows})
    assert not writer_groups[0] & writer_groups[1]
    assert not writer_groups[0] & writer_groups[2]
    assert not writer_groups[1] & writer_groups[2]
    assert (stage.stat().st_mode & 0o777) == 0o700
    assert ((stage / "train.jsonl").stat().st_mode & 0o777) == 0o600


def test_selection_quarantines_historical_content_and_reports_final_scarcity():
    rows = []
    for split in ("train", "validation", "test", "school_test"):
        for label in CLASSES:
            for writer in range(16):
                token = f"{split}-{label}-{writer}"
                rows.append(
                    {
                        "id": token,
                        "hash": token,
                        "pixel_hash": token,
                        "normalized_pixel_hash": token,
                        "label": label,
                        "writer": f"{split}-{writer}",
                        "split": split,
                    }
                )
    # A normalized collision with a historical test row cannot enter training.
    prior = {**rows[0], "writer": "old-test", "split": "test"}
    selected, audit = preparation.select_rows(rows, [prior], load_config())
    assert rows[0]["id"] not in {row["id"] for row in selected["train"]}
    assert audit["duplicate_rows_quarantined"] == 1
    # Every final group must cover every case-sensitive class at its minimum.
    selected, audit = preparation.select_rows(
        [r for r in rows if r["split"] != "test" or r["label"] != "a"], [], load_config()
    )
    assert selected["test"] == []
    assert "test" in audit["unavailable_final_groups"]
    again, _ = preparation.select_rows(list(reversed(rows)), [prior], load_config())
    assert again["train"] == preparation.select_rows(rows, [prior], load_config())[0]["train"]


def test_private_destination_refuses_repository_and_symlinks(tmp_path):
    with pytest.raises(ValueError):
        external(Path.cwd() / "research/raw-images")
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        external(tmp_path / "link")


@pytest.mark.parametrize(
    "name", ["train.jsonl", "final-test.jsonl", "image-audit-private.jsonl", "exposure.json"]
)
def test_preflight_rejects_changed_frozen_metadata_before_using_it(prepared, tmp_path, name):
    stage, _, _ = prepared
    target = tmp_path / "damaged"
    target.mkdir()
    for item in stage.iterdir():
        if item.is_file():
            shutil.copyfile(item, target / item.name)
    with (target / name).open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="metadata_pin_mismatch"):
        check(target)


def test_data_and_scoring_imports_do_not_import_model_frameworks():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import research.english.r5_data.check; import research.english.r5_eval.scoring; assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
